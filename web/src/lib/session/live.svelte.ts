// LiveSession —— 把采音 / 识别 / 实时核心串成一条真会话，对界面露出与 DemoPlayer 相同的形状。
//
// 对外只有两件事：只读字幕（rows / draftEn / draftZh / playing）与状态（status / level / message），
// 生命周期 start() / end()。任何阶段的失败都变成 status='error' + 可读中文 message，不往界面抛。
//
// 为什么依赖全注入：麦克风、Soniox、Python 核心都在外面造，本模块只决定「谁在什么时候推一拍」——
// 于是同一份代码既能跑真链路，也能在 Node 里拿磁带（tape.ts）端到端跑。
// 时钟不靠 setInterval：推核心的节拍永远挂在音频帧上（PLAN A10），静音判定也数帧不数墙钟。
import type { AsrClient } from '../asr';
import type { AudioFrame, AudioSource } from '../audio';
import type { Core } from '../core';
import type { ViewState } from '../state/viewstate';
import type { Translator } from '../translate';
import { latencyOf, type LatencyReport, type LatencySample } from '../translate';
import { DraftGate } from '../translate/draft-gate';

export type LiveStatus = 'starting' | 'listening' | 'silent' | 'reconnecting' | 'ended' | 'error';

/** 与 `demo/player.svelte.ts` 的 `CapRow` 同形（界面换源一行都不用改） */
export interface CapRow {
	/** 句子序号（从 1 起，与 ViewState 的 `seg` 同一个值） */
	id: number;
	en: string;
	zh: string;
	/** 这句的音频起点（秒） */
	t: number;
	/** 定稿译文还没到：zh 是草稿译文占位（或空），界面按草稿样式显示，且不拿英文顶替中文那行 */
	pending?: boolean;
}

/** 一份译文的一次变化（`Translator` 的回调；本模块只负责落进行和打点）。 */
export interface TranslationEvent {
	seg: number;
	zh: string;
	en: string;
	done: boolean;
	failed: boolean;
}

/** 6 s 没有声音 → 提示检查麦克风（PLAN M1 / E1）。帧长 100 ms，所以数 60 帧。 */
export const SILENCE_FRAMES = 60;
/** 「有声」的电平下限：与桌面 vad 的 `min_rms` 同口径，不做 dB 换算。 */
export const LOUD_LEVEL = 0.008;
/** 草稿桥换手点：定稿中文长到占位（封口时的草稿译文）的这个比例就换上（桌面 caption_flow.BRIDGE_RATIO） */
export const BRIDGE_RATIO = 0.6;

export interface LiveDeps {
	audio: AudioSource;
	core: Core;
	/** 领临时 key（single_use）。原样交给 `asr` 工厂，本模块不认发 key 的地址 */
	getKey: () => Promise<string>;
	/**
	 * 识别连接：给现成的 `AsrClient`，或给一个工厂。
	 *
	 * 工厂存在的理由：Soniox 客户端**构造即开始连**，而本模块只想在 `start()` 里连，
	 * 所以把「什么时候造」交给这里，key 原样递进去（`createSonioxClient({ getKey, url })`）。
	 */
	asr: AsrClient | ((getKey: () => Promise<string>) => AsrClient);
	/**
	 * 造翻译器（`lib/translate` 的 `Translator`）。不给 = 不翻译（只出英文，M1 的行为）。
	 *
	 * 是个工厂而不是实例：译文回到哪一行、延迟往哪记都是本模块的事，所以 hooks 由这里给；
	 * 而 `prompt`（Pyodide）/ `endpoint`（通行码）由调用方注入 —— 本模块不认网络也不认 Python。
	 */
	makeTranslator?: (hooks: TranslationHooks) => Translator;
}

/** 翻译器往回推的两个出口（`Translator` 的构造依赖就是这两条）。 */
export interface TranslationHooks {
	onFinal: (ev: TranslationEvent) => void;
	onDraft: (ev: { zh: string }) => void;
}

interface Waiter {
	rev: number;
	done: () => void;
}

export class LiveSession {
	rows = $state.raw<CapRow[]>([]);
	draftEn = $state.raw('');
	draftZh = $state.raw('');
	playing = $state(false);
	status = $state.raw<LiveStatus>('starting');
	/** 开课卡在哪一步（只在 status='starting' 时有意义）：界面据此说「正在下载识别核心」而不是笼统的「正在连接」 */
	step = $state.raw<'core' | 'asr' | 'mic' | ''>('core');
	/** 麦克风电平 0..1（每帧更新，给界面画电平条 / 静音提示） */
	level = $state.raw(0);
	/** 出错 / 提示的中文说明（U6：失败不许静默） */
	message = $state.raw('');

	#deps: LiveDeps;
	#asr: AsrClient | null = null;
	#started = false;
	#ended = false;
	#ending: Promise<void> | null = null;
	#rev = 0;
	#silentFrames = 0;
	#waiters: Waiter[] = [];
	#tr: Translator | null = null;
	/** 每句的译文与打点（`latency()` 的唯一来源；界面不读）。 */
	#trRows = new Map<
		number,
		{
			zh: string;
			en: string;
			state: 'none' | 'draft' | 'final' | 'failed';
			/** 封口那一刻的草稿译文 */
			ph: string;
			/** 说话人字母（主讲 → 空串） */
			spk: string;
		}
	>();
	#samples = new Map<number, LatencySample & { tAudioEnd: number | null }>();
	/** 每句的音频起点（秒），接 `t_s` 与打点用 */
	#sealedAt = new Map<number, number>();
	/** 最后一次看到草稿变化的墙钟（毫秒）：段尾 → 封口那段耗时的起点 */
	/** 每句草稿最后一次变化的时刻（毫秒）：段尾 → 封口那段耗时的起点 */
	#draftStartAt = new Map<number, number>();
	/** 草稿送翻节流（桌面 `draft_policy` 的 novel 档）：不然每变一个词就发一次请求 */
	#gate = new DraftGate();
	/** 草稿槽此刻显示的中文（普通字段：封口时要读，别去读 $state 那份） */
	#shownDraftZh = '';

	constructor(deps: LiveDeps) {
		this.#deps = deps;
	}

	/** 起核心 → 连识别 → 起麦克风。失败落到 status='error'，不抛。 */
	async start(): Promise<void> {
		if (this.#started) return;
		this.#started = true;
		const { core, audio, asr, getKey } = this.#deps;

		core.onView((vs) => this.#onView(vs));
		this.step = 'core';
		try {
			await core.ready();
		} catch (e) {
			this.#fail('识别核心没能启动（网页版需要 Chrome / Edge，并允许下载约 13 MB 的 WebAssembly）', e);
			return;
		}
		if (this.#ended) return;

		if (this.#deps.makeTranslator) {
			try {
				this.#tr = this.#deps.makeTranslator({
					onFinal: (ev) => this.#onTranslation(ev),
					onDraft: (ev) => this.#onDraftZh(ev.zh)
				});
			} catch (e) {
				// 翻译起不来不该挡字幕：英文照出，只是没有中文
				const detail = e instanceof Error ? e.message : String(e);
				this.message = `翻译没起来（${detail.slice(0, 120)}）—— 先只显示英文`;
			}
		}

		this.step = 'asr';
		try {
			this.#asr = typeof asr === 'function' ? asr(getKey) : asr;
			this.#asr.onMessage((raw) => {
				core.feed(raw);
				this.#settle();
			});
		} catch (e) {
			this.#fail('连不上实时识别（领取临时通行证失败），请检查网络后重试', e);
			return;
		}

		this.step = 'mic';
		try {
			await audio.start((f) => this.#onFrame(f));
		} catch (e) {
			this.#fail('打不开麦克风：请在地址栏允许使用麦克风，并检查系统里的麦克风权限', e);
			return;
		}
		if (this.#ended) return;
		// 麦已经起来了：playing 由这里置位（不会因为「第一帧还没到」卡在 starting）
		this.step = '';
		this.playing = true;
		this.#silentFrames = 0;
		this.#settle();
	}

	/**
	 * 下课：停麦 → flush（尾句强制定稿）→ 关识别。
	 *
	 * `flush` 那一版快照要等它落地再关连接，否则最后一句可能还在队列里；最多等 2 s 就放弃。
	 * 幂等：重复调只有第一次干活（同一句进两次笔记是不可恢复的错）。
	 */
	async end(): Promise<void> {
		this.#ending ??= this.#runEnd();
		return this.#ending;
	}

	async #runEnd(): Promise<void> {
		if (this.#ended) return;
		this.#ended = true;
		this.playing = false;
		try {
			this.#deps.audio.stop();
		} catch {
			/* 麦已经没了，不是错 */
		}
		const before = this.#rev;
		try {
			this.#deps.core.flush();
		} catch (e) {
			this.#fail('核心没响应，最后半句可能没存下来', e);
		}
		await this.#awaitRev(before, 2000);
		// 尾句已经排进翻译队列：等它跑完再收（最多 3 s），否则下课那一句永远只有英文
		try {
			await this.#drainTranslation(3000);
		} catch {
			/* 翻译收尾失败不影响下课 */
		}
		this.#tr?.close();
		try {
			this.#asr?.close();
		} catch {
			/* 已经关了 */
		}
		if (this.status !== 'error') this.status = 'ended';
	}

	// ---- 帧节拍

	#onFrame(f: AudioFrame): void {
		this.#asr?.send(f.pcm);
		this.#deps.core.tick(f.seq);
		this.level = this.#deps.audio.level;
		if (this.level >= LOUD_LEVEL) this.#silentFrames = 0;
		else this.#silentFrames += 1;
		this.#settle();
	}

	/** 由「帧节拍 / 识别消息 / 生命周期」推一次状态；error 与 ended 是终点，不再被覆盖。 */
	#settle(): void {
		if (this.status === 'ended' || this.status === 'error') return;
		const conn = this.#asr?.state;
		if (conn === 'failed') {
			this.#fail('实时识别连不上了（自动重连 6 次都没成功），请检查网络后重新开课');
			return;
		}
		if (conn === 'closed') {
			this.status = 'ended';
			this.playing = false;
			return;
		}
		if (conn === 'reconnecting') {
			this.status = 'reconnecting';
			return;
		}
		// 还没开课 / 识别连接还没开：诚实地说还在起动，不假装在听
		if (!this.playing || conn === 'idle' || conn === 'connecting') {
			this.status = 'starting';
			return;
		}
		if (this.#silentFrames >= SILENCE_FRAMES) {
			this.status = 'silent';
			return;
		}
		this.status = 'listening';
	}

	// ---- 核心快照 → 界面形状

	#onView(vs: ViewState): void {
		this.#rev = vs.rev;
		let draftEn = '';
		let draftSeg = 0;
		for (const r of vs.rows) {
			if (r.kind === 'draft') {
				draftEn = r.en;
				draftSeg = r.seg;
				continue;
			}
			if (!this.#sealedAt.has(r.seg)) this.#newFinal(r.seg, r.en, r.t_s, r.spk ?? '');
		}
		if (draftEn !== this.draftEn) {
			this.draftEn = draftEn;
			if (draftEn.trim() !== '' && this.#gate.admit(draftSeg, draftEn, Date.now() / 1000)) {
				this.#draftStartAt.set(draftSeg, Date.now());
				void this.#tr?.draft(draftSeg, draftEn);
			}
		}
		this.#render();
		if (vs.status.notice) this.message = vs.status.notice;
		this.#release();
	}

	/** 一句刚封口：记打点、排进翻译队列（串行，一句接一句）。 */
	#newFinal(seg: number, en: string, t_s: number, spk = ''): void {
		this.#sealedAt.set(seg, t_s);
		// 封口时把正在看的草稿译文接过来当占位：中文那行不消失、不先变英文再重打（2026-11-20 实测每句都闪）。
		// 草稿槽同时清空，免得同一句中文上下各一份。
		const ph = this.#shownDraftZh.trim();
		this.#trRows.set(seg, { zh: '', en, state: 'none', ph, spk });
		if (ph) this.#onDraftZh('');
		this.#samples.set(seg, {
			// 段尾 → 封口：最后一次看到这句还是草稿的时刻（桌面 `seg_to_zh` 的拆分口径）
			tAudioEnd: this.#draftStartAt.get(seg) ?? null,
			tSealed: Date.now(),
			tZhFirst: null
		});
		const tr = this.#tr;
		if (!tr) return;
		void tr.final(seg, en).then(() => tr.noteFinal(seg));
	}

	/** 按 `#trRows` 重建界面形状（`rows` 是 `$state.raw`，必须整份换掉）。 */
	#render(): void {
		const out: CapRow[] = [];
		for (const [seg, r] of [...this.#trRows].sort((a, b) => a[0] - b[0])) {
			const settled = r.state === 'final' || r.state === 'failed';
			// 定稿译文逐字流进来时，长到占位的 BRIDGE_RATIO 之前继续显示占位：整句只换一次，不从一两个字重新长。
			// 比例同桌面 `caption_flow.BRIDGE_RATIO`：原来要追满占位长度，定稿比草稿短时旧草稿一直挂到译完（Owen：译文滞留）
			const zh = settled || r.zh.length >= BRIDGE_RATIO * r.ph.length ? r.zh : r.ph;
			out.push({
				id: seg,
				en: r.en,
				zh,
				t: this.#sealedAt.get(seg) ?? 0,
				pending: !settled,
				...(r.spk ? { spk: r.spk } : {})
			});
		}
		this.rows = out;
	}

	/** `Translator` 的定稿回调：中文逐字上屏，首字落时刻（延迟量具的唯一来源）。 */
	#onTranslation(ev: TranslationEvent): void {
		const row = this.#trRows.get(ev.seg);
		if (!row) return;
		const sample = this.#samples.get(ev.seg);
		if (ev.zh) {
			row.zh = ev.zh;
			if (sample && sample.tZhFirst === null) sample.tZhFirst = Date.now();
		}
		if (ev.en) row.en = ev.en;
		row.state = ev.failed ? 'failed' : ev.done ? 'final' : 'draft';
		this.#render();
	}

	#onDraftZh(zh: string): void {
		this.#shownDraftZh = zh;
		this.draftZh = zh;
	}

	/** 等翻译队列排空（下课收尾用）。到点就放弃 —— 不许把下课卡住。 */
	async #drainTranslation(ms: number): Promise<void> {
		if (!this.#tr) return;
		const deadline = Date.now() + ms;
		while (Date.now() < deadline) {
			if (this.#tr.context.length >= this.#trRows.size) return;
			await new Promise<void>((r) => setTimeout(r, 50));
		}
	}

	/**
	 * 延迟汇总：说完 → 中文首字（毫秒）。没数据时各项为 null，不返回 0 冒充达标。
	 *
	 * ⚠️ 「说完」这里只能取**这段草稿最后一次变化**的时刻（`ViewState` 只有 `t_s` = 段起点，
	 * 没有段尾），所以封口那一段会量得偏短。要权威数字用 `scripts/measure-latency.ts`
	 * （它拿得到 `Snapshot.sealed[].end`）。
	 */
	latency(): LatencyReport {
		return latencyOf([...this.#samples.values()]);
	}

	/** 一句的延迟拆分（毫秒；遥测逐句打点用，对齐桌面 `.live.jsonl`）。没这句 / 没译出的字段为 null。 */
	sampleOf(seg: number): { seal_ms: number | null; zh_ms: number | null; total_ms: number | null } | null {
		const s = this.#samples.get(seg);
		if (!s) return null;
		const zh = s.tZhFirst === null ? null : s.tZhFirst - s.tSealed;
		const seal = s.tAudioEnd === null || s.tAudioEnd === undefined ? null : s.tSealed - s.tAudioEnd;
		return { seal_ms: seal, zh_ms: zh, total_ms: zh === null ? null : zh + (seal ?? 0) };
	}

	#awaitRev(before: number, ms: number): Promise<void> {
		if (this.#rev > before) return Promise.resolve();
		return new Promise<void>((resolve) => {
			const waiter: Waiter = { rev: before, done: () => resolve() };
			const timer = setTimeout(() => {
				this.#waiters = this.#waiters.filter((w) => w !== waiter);
				resolve();
			}, ms);
			waiter.done = () => {
				clearTimeout(timer);
				resolve();
			};
			this.#waiters.push(waiter);
		});
	}

	#release(): void {
		if (this.#waiters.length === 0) return;
		const left: Waiter[] = [];
		for (const w of this.#waiters) {
			if (this.#rev > w.rev) w.done();
			else left.push(w);
		}
		this.#waiters = left;
	}

	/** 一处收尾：状态、提示、资源都收干净（失败也要把麦关掉，不然权限灯一直亮）。 */
	#fail(text: string, e?: unknown): void {
		const detail = e instanceof Error ? e.message : e === undefined ? '' : String(e);
		this.message = detail ? `${text}（${detail.slice(0, 200)}）` : text;
		this.status = 'error';
		this.playing = false;
		try {
			this.#deps.audio.stop();
		} catch {
			/* 本来就没起来 */
		}
	}
}
