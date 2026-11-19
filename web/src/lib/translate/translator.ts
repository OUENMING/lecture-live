// 翻译接线：定稿句（排队、逐句流式上屏）+ 草稿句（只译最新、旧的作废）。
// 对外：`new Translator({ prompt, request, ... })` → `final(...)` / `draft(...)` / `noteFinal(seg)`；
// 状态与回调用在构造时注入，所以本模块不碰 DOM、不碰计时器全局、不认 fetch 地址。
// 为什么这样分两条：桌面版也是两条线程 —— 定稿串行（一句接一句，`final_worker`），
// 草稿单槽（新的顶掉旧的，`DraftTranslator`）。并发上限照桌面：定稿 1、草稿 1、互不等待。
// 失败口径：一次失败重试一次，两次都失败就标「这句没译出」（`failed`），下一句照常，不卡队列。

import type { TwoLineResult } from './format';
import { TwoLineParser, looksLikeEcho } from './format';
import type { PromptBuilder, TermConfig } from './prompt';
import { MAX_TOKENS, streamChat, type ChatError } from './stream';

/** 一次流式请求的注入面（真链路是 `streamChat`，测试里是假 fetch 塞进 `streamChat`）。 */
export type ChatFn = typeof streamChat;

export interface TranslatorDeps {
	/** 请求体拼装（Pyodide 里的 `prompt_core`；见 prompt.ts） */
	prompt: PromptBuilder;
	/** POST 的目标 URL + 请求头（含通行码）；每次调用现取，通行码可能中途换掉 */
	endpoint: () => { url: string; headers: Record<string, string> };
	/** 本课术语/领域配置；缺省 = 空（与桌面空术语表逐字相同） */
	cfg?: TermConfig;
	/** 注入的流式请求；默认真的 `streamChat` */
	chat?: ChatFn;
	/** 注入的时钟（延迟打点用）；默认 `Date.now` */
	now?: () => number;
	/** 定稿句译文变化：`zh` 是到目前累积的中文，`en` 是解析出的修正英文（还没出现就是原文） */
	onFinal: (ev: { seg: number; zh: string; en: string; done: boolean; failed: boolean }) => void;
	/** 草稿译文变化（整段覆盖，空串 = 清掉） */
	onDraft: (ev: { zh: string }) => void;
}

interface Queued {
	seg: number;
	en: string;
	context: string[];
	/** 封口时刻（毫秒），交给 `onFinal` 时原样带上，供 LiveSession 打延迟 */
	tSealed: number;
	attempt: number;
}

interface DraftJob {
	seq: number;
	seg: number;
	gen: number;
	en: string;
	ctl: AbortController;
}

/** 定稿句最多试几次（桌面：`fix_and_translate_stream` 失败由调用方兜底；这里多给一次重试）。 */
const MAX_ATTEMPTS = 2;

export class Translator {
	#deps: TranslatorDeps;
	#now: () => number;
	#chat: ChatFn;
	/** 已定稿的修正英文（桌面 `finals`）：下一句的前文窗口从这里取 */
	#finals: string[] = [];
	#q: Queued[] = [];
	#running = false;
	#closed = false;
	#draft: DraftJob | null = null;
	/** 最近一次上屏的草稿译文（清空时要知道上一条是不是自己发的） */
	#draftZh = '';
	/** 最近一次送翻的草稿（段号 + 原文），用来去重 —— 桌面由 `draft_policy` 的触发判据挡在前面 */
	#lastDraftSeg = -1;
	#lastDraftEn = '';

	constructor(deps: TranslatorDeps) {
		this.#deps = deps;
		this.#now = deps.now ?? (() => Date.now());
		this.#chat = deps.chat ?? streamChat;
	}

	/** 已定稿句的修正英文序列（前文窗口的唯一来源）。 */
	get context(): readonly string[] {
		return this.#finals;
	}

	/** 排队翻一句定稿。返回 Promise 在本句真正处理完（成功 / 放弃）时 resolve。 */
	final(seg: number, en: string, tSealed: number = this.#now()): Promise<void> {
		if (this.#closed) return Promise.resolve();
		return new Promise<void>((resolve) => {
			this.#q.push({ seg, en, context: this.#finals.slice(), tSealed, attempt: 0 });
			this.#done.set(seg, resolve);
			void this.#pump();
		});
	}

	#done = new Map<number, () => void>();

	/** 某段定稿了：告诉 DraftView（清草稿槽 / 判过期），并取消在途的旧草稿请求。 */
	async noteFinal(seg: number): Promise<void> {
		// 这段定稿了，在途的草稿译文一定过期 —— 先取消，不必等 Python 回话
		if (this.#draft && this.#draft.seg <= seg) this.#cancelDraft();
		let drop = true;
		try {
			drop = await this.#deps.prompt.draftFinal(seg);
		} catch {
			/* 判据坏了不挡字幕：退回「定稿就清草稿槽」的保守做法 */
		}
		if (drop && this.#draftZh) {
			this.#draftZh = '';
			this.#deps.onDraft({ zh: '' });
		}
	}

	/**
	 * 一份英文草稿到了。要不要发、旧请求作不作废全在 `DraftView`（Pyodide 那份）。
	 * 同一段内文本没变、或这段已定稿，都不会发请求。
	 */
	draft(seg: number, en: string): void {
		if (this.#closed) return;
		if (!en.trim()) return;
		void this.#submitDraft(seg, en);
	}

	async #submitDraft(seg: number, en: string): Promise<void> {
		// 同一段同文本重复到达（每拍都可能有）不发第二遍；段号倒退的（晚到的旧草稿）直接丢
		if (seg < this.#lastDraftSeg) return;
		if (seg === this.#lastDraftSeg && en === this.#lastDraftEn) return;
		this.#lastDraftSeg = seg;
		this.#lastDraftEn = en;
		let b;
		try {
			b = await this.#deps.prompt.draftBegin(seg, en);
		} catch {
			return;
		}
		if (this.#closed) return;
		if (b.clear && this.#draftZh) {
			this.#draftZh = '';
			this.#deps.onDraft({ zh: '' });
		}
		// 只译最新一条：在途的旧请求一律取消
		this.#cancelDraft();
		const job: DraftJob = { seq: b.seq, seg, gen: b.gen, en, ctl: new AbortController() };
		this.#draft = job;
		void this.#runDraft(job);
	}

	/** 下课：不再接新活，在途的草稿丢掉（定稿队列会自然跑完）。 */
	close(): void {
		this.#closed = true;
		this.#cancelDraft();
	}

	#cancelDraft(): void {
		const job = this.#draft;
		if (!job) return;
		this.#draft = null;
		job.ctl.abort();
	}

	async #runDraft(job: DraftJob): Promise<void> {
		const ctx = this.#finals.slice(-1);
		let prompt: { system: string; user: string };
		try {
			prompt = await this.#deps.prompt.draft(job.en, ctx, this.#deps.cfg);
		} catch {
			return; // 拼请求失败不值得打扰用户：草稿是锦上添花
		}
		const { url, headers } = this.#deps.endpoint();
		let text = '';
		try {
			text = await this.#chat({
				url,
				headers,
				prompt,
				maxTokens: MAX_TOKENS.draft,
				signal: job.ctl.signal
			});
		} catch {
			return; // 草稿失败不重试（下一份草稿马上就会来），也不上屏错误
		}
		if (this.#draft !== job || !text.trim()) return;
		// 过期判据：段已定稿 / 已有更新的草稿 / 更新的译文已显示 → 丢
		try {
			if ((await this.#deps.prompt.draftAccept(job.seq, job.seg, job.gen)) !== true) return;
		} catch {
			return;
		}
		this.#draft = null;
		this.#draftZh = text.trim();
		this.#deps.onDraft({ zh: this.#draftZh });
	}

	async #pump(): Promise<void> {
		if (this.#running) return;
		this.#running = true;
		try {
			while (this.#q.length) {
				const job = this.#q.shift() as Queued;
				await this.#runFinal(job);
				const done = this.#done.get(job.seg);
				this.#done.delete(job.seg);
				done?.();
			}
		} finally {
			this.#running = false;
		}
	}

	async #runFinal(job: Queued): Promise<void> {
		const patch = (zh: string, en: string, done: boolean, failed = false): void => {
			this.#deps.onFinal({ seg: job.seg, zh, en, done, failed });
		};
		let zh = '';
		let enFixed = '';
		while (job.attempt < MAX_ATTEMPTS) {
			job.attempt += 1;
			if (this.#closed) return;
			zh = '';
			enFixed = '';
			const parser = new TwoLineParser((d) => {
				// 增量只去掉行首空白：`ZH: ` 后面那个空格属于第一个 delta，
				// 留着会让首字位置漂一下；末尾不清（下一块可能接着同一个词）
				zh += d;
				patch(zh.replace(/^\s+/, ''), enFixed || job.en, false);
			});
			try {
				const prompt = await this.#deps.prompt.final(job.en, job.context, this.#deps.cfg);
				const { url, headers } = this.#deps.endpoint();
				const text = await this.#chat({
					url,
					headers,
					prompt,
					maxTokens: MAX_TOKENS.final,
					onDelta: (d) => parser.feed(d)
				});
				if (!text.trim() && !zh) throw Object.assign(new Error('空回包'), { aborted: false });
				const res: TwoLineResult = parser.result(job.en);
				if (looksLikeEcho(res.zh, job.en)) throw Object.assign(new Error('回显'), { aborted: false });
				this.#finals.push(res.en);
				patch(res.zh, res.en, true);
				return;
			} catch (e) {
				const aborted = (e as ChatError)?.aborted === true;
				if (aborted) {
					patch(zh, enFixed || job.en, true, true);
					return;
				}
				// 还有下一次：把这一段已上屏的中文清掉，避免「垃圾+正文」粘连
				if (job.attempt < MAX_ATTEMPTS && zh) patch('', job.en, false);
			}
		}
		// 两次都失败：界面显示英文，中文标失败，后面照常
		this.#finals.push(job.en);
		patch('', job.en, true, true);
	}
}
