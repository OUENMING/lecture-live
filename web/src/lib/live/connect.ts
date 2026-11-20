// lib/live/connect.ts —— 上课页用的「真链路」装配：麦克风 + Soniox + Pyodide 核心 + 翻译 + 本机落盘。
//
// LiveSession 本身全靠注入（便于磁带测试）；这里是唯一把真实现塞进去的地方，界面只调 `connectLive()`。
// 为什么开课前先领一次 key：Soniox 的 WebSocket 地址（ws_url）跟 key 一起回来，按区域不同，
// 客户端构造时就要地址；这一把 key 顺手给第一次连接用（single_use，60 s 内有效），重连再现领。
import { apiBase, api, getSonioxKey, headers, type SonioxKey } from '../api';
import { createSonioxClient } from '../asr/soniox';
import { createMicSource } from '../audio/mic';
import type { AudioSource } from '../audio';
import { createCore } from '../core';
import { LiveSession } from '../session';
import { openStore, type LineStore } from '../store';
import { Translator, createPrompt } from '../translate';
import { mergeForContext, termsForClass } from '../terms/cloud';
import { attachSync, type SyncView } from './sync';
import { startClassTelemetry } from './class-telemetry';
import { marksOf } from '../env';
import { BEAT_MS, clearActive, pickResume, tryTakeClassLock, writeActive, holdClassLock, type KV, type LockApi } from './resume';

export interface LiveHandle {
	session: LiveSession;
	/** 本机落盘（打不开 IndexedDB 时是内存兜底，`persistent=false`） */
	store: LineStore | null;
	session_id: string;
	/** 刷新 / 重开浏览器后接回了上一节没下课的课（字幕从接回那刻起；之前的在课后页） */
	readonly resumed: boolean;
	/** 接回时上一段已有的最大句号；本页新句的服务端序号 = 页内句号 + seq_base（新开的课恒为 0） */
	readonly seq_base: number;
	/** 麦克风（上课页的「换麦克风」用 `switchDevice`；拔掉时 `onDeviceLost`） */
	readonly audio: AudioSource;
	/** 同步到 Worker 的现状（只读快照，界面画小字用；存不了本机就是 `null`） */
	readonly sync: SyncView | null;
	/** 下课：收尾会话 → 推完最后几句 → 记下课时间 / 报下课 → 释放 Python。幂等。 */
	end(): Promise<void>;
}

const FALLBACK_WS = 'wss://stt-rt.soniox.com/transcribe-websocket';
/** 课名留空时发给服务端的占位（服务端只要非空；课后改名会覆盖） */
const UNNAMED = '未命名';

function newSessionId(): string {
	const d = new Date();
	const p = (n: number) => String(n).padStart(2, '0');
	const stamp = `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
	return `web-${stamp}-${crypto.randomUUID().slice(0, 6)}`;
}

/**
 * 先领 key（同时就是查码 + 查额度）再装配。领 key 失败直接抛 `ApiError`，界面按 kind 出提示；
 * 后面任何一环失败都由 LiveSession 变成 status='error' + 中文 message，不抛。
 *
 * `course` 可以为空串（2026-11-19 起课名可下课后再填）：这时术语取草稿键 `cl.terms.__next`，
 * 同步开课时发服务端要的非空占位 `'未命名'`，课后页 `renameCourse` 再改成真课名。
 */
export async function connectLive(course: string): Promise<LiveHandle> {
	const kv: KV | null = typeof localStorage === 'undefined' ? null : localStorage;
	const locks = (typeof navigator !== 'undefined' ? ((navigator as unknown as { locks?: LockApi }).locks ?? null) : null) as LockApi | null;
	// 刷新 / 重开：同课名、心跳新鲜、没被别的标签页占着 → 接回同一节课（不开新会话）
	const prev = pickResume(kv, course, Date.now());
	let lock = prev ? await tryTakeClassLock(locks, prev) : null;
	const resumed = !!lock;
	const session_id = lock && prev ? prev : newSessionId();
	if (!lock) lock = await holdClassLock(locks, session_id);
	const courseKey = course.trim();
	const beat = () => writeActive(kv, { session_id, course: courseKey, beat: Date.now() });
	beat();
	const beatTimer = setInterval(beat, BEAT_MS);
	const stopBeat = (forget: boolean) => {
		clearInterval(beatTimer);
		if (forget) clearActive(kv, session_id);
		lock?.release();
	};
	// 测试数据（旁路，出错只丢数据）：开课前就起，领 key 失败也要记下来
	const ct = startClassTelemetry({ session_id, course });
	const k0 = performance.now();
	let first: SonioxKey;
	try {
		first = await getSonioxKey();
	} catch (e) {
		ct.track('key_failed', { ms: Math.round(performance.now() - k0), err: String((e as Error)?.message ?? e).slice(0, 300), kind: (e as { kind?: string })?.kind });
		void ct.end({ reason: 'key_failed' });
		// 没开成：新会话的记录不留（下次不去「接回」一节从没开始过的课）；接回的那节记录留着，下次还能接
		stopBeat(!resumed);
		throw e;
	}
	ct.track('key_ok', { ms: Math.round(performance.now() - k0), region: first.region, max_session_s: first.max_session_s });
	let spare: string | null = first.api_key;
	const getKey = async () => {
		if (spare) {
			const k = spare;
			spare = null;
			return k;
		}
		const t = performance.now();
		try {
			const k = (await getSonioxKey(first.region || undefined)).api_key;
			ct.track('key_ok', { ms: Math.round(performance.now() - t), again: true });
			return k;
		} catch (e) {
			ct.track('key_failed', { ms: Math.round(performance.now() - t), again: true, err: String((e as Error)?.message ?? e).slice(0, 300) });
			throw e;
		}
	};

	let store: LineStore | null = null;
	let syncer: ReturnType<typeof attachSync> | null = null;
	let seqBase = 0;
	let tBase = 0;
	try {
		store = await openStore();
		await store.openSession(session_id, course);
		if (resumed) {
			// 页内句号从 1 重新数，服务端按 (session, seq) 先到先得 —— 不垫高会把接回后的句子全当重复丢掉
			const have = await store.linesOf(session_id);
			seqBase = have.reduce((m, l) => Math.max(m, l.seq), 0);
			const meta = (await store.listSessions()).find((m) => m.session_id === session_id);
			if (meta) tBase = Math.max(0, Math.round((Date.now() - meta.started_at) / 1000));
			ct.track('resumed', { seq_base: seqBase, t_base: tBase });
		}
		void store.purgeOlderThan(30).catch(() => {});
		// 同步到 Worker：开课不在这里等（没网也照常上课，句子先落本机，恢复后补传）
		syncer = attachSync({
			store,
			local_session_id: session_id,
			// 服务端要求 course 非空；没命名就发占位，课后改名会覆盖它
			course: course.trim() || UNNAMED,
			api
		});
	} catch {
		store = null; // 存不了也照常上课；界面按 store=null 提示「关掉就没了」
	}

	// 术语：个人（本机）+ 同课共享池里个人没有的词，进 Soniox context（提高识别率）和翻译提示词的 user 段。
	// 课名只进 Soniox 的 general，不进翻译 system（桌面的 domain 会改 system 提示词，服务端白名单只认三版原文）。
	// 共享池拉不到 / 超时就是空数组（`termsForClass` 不抛）：网络问题不能挡上课。
	const pulled = await termsForClass(api, course);
	const terms = mergeForContext(pulled.personal, pulled.shared);
	// 这节实际用过的**个人**术语存一份快照：课后命名时靠它并进那门课的术语表（课名当时还没定）。
	// 共享池的不进快照 —— 那不是用户自己敲的，不该在改名时被写进他的术语表。
	const personalRaw = pulled.personal.join('\n');
	if (personalRaw.trim()) {
		try {
			localStorage.setItem(`cl.sessterms.${session_id}`, personalRaw);
		} catch {
			/* 存不住就只影响「课后并术语」这一步 */
		}
	}
	const sonioxContext = {
		general: [
			{ key: 'topic', value: course.slice(0, 120) },
			{ key: 'setting', value: 'university lecture' },
			{ key: 'language', value: 'English' }
		],
		...(terms.length ? { terms } : {})
	};

	const core = createCore({ course });
	const prompt = createPrompt();
	// 一句译完（或放弃）才落盘：store.append 是「写一次」，写早了中文就缺了
	// 写入串成一条链：下课时要等它写完再推，否则最后几句可能还没落盘就 flush 了
	let writing: Promise<unknown> = Promise.resolve();
	const persist = (seg: number, en: string, zh: string) => {
		const t_s = (session.rows.find((r) => r.id === seg)?.t ?? 0) + tBase;
		writing = store?.append({ session_id, seq: seg + seqBase, en, zh, t_s }).catch(() => {}) ?? writing;
		syncer?.push();
	};
	const mic = createMicSource();
	const session: LiveSession = new LiveSession({
		audio: mic,
		core,
		getKey,
		asr: (gk) =>
			createSonioxClient({
				getKey: gk,
				url: first.ws_url || FALLBACK_WS,
				context: sonioxContext,
				createSocket: (u, pr) => ct.createSocket(u, pr)
			}),
		makeTranslator: (hooks) =>
			new Translator({
				prompt,
				cfg: terms.length ? { terms } : undefined,
				endpoint: () => ({ url: `${apiBase()}/v1/chat/completions`, headers: headers() }),
				onFinal: (ev) => {
					hooks.onFinal(ev);
					if (ev.done || ev.failed)
						ct.sentence({ seg: ev.seg, failed: ev.failed, en_len: ev.en.length, zh_len: ev.zh.length, lat: session.sampleOf(ev.seg) });
					if (ev.done || ev.failed) persist(ev.seg, ev.en, ev.failed ? '' : ev.zh);
				},
				onDraft: hooks.onDraft,
				onTrace: (ev) => ct.trace(ev)
			})
	});

	ct.attach({ session, mic, sync: () => syncer?.state() ?? null });

	let ending: Promise<void> | null = null;
	const end = () =>
		(ending ??= (async () => {
			await session.end();
			const tele = ct.end({
				rows: session.rows.length,
				latency: session.latency(),
				sync: syncer?.state() ?? null,
				persistent: !!store,
				// ❓ 课上标记（桌面 `.lost.jsonl` 的网页版）：只记句号，不记内容
				marks: marksSafe(session_id)
			});
			// 收尾时还没译完的句子：按现状补写（已写过的不会被覆盖）
			for (const r of session.rows) persist(r.id, r.en, r.zh);
			await writing.catch(() => {});
			// 先把最后几句推完再记下课：顺序反过来服务端会拒收尾批（409 session_ended）
			await syncer?.end().catch(() => {});
			await store?.closeSession(session_id).catch(() => {});
			stopBeat(true);
			core.dispose();
			prompt.dispose();
			// 不等：录音最后一段 / 遥测尾批在后台收（≤4 s），下课跳课后页不被它拖住
			void tele;
		})());

	return {
		audio: mic,
		session,
		store,
		session_id,
		resumed,
		seq_base: seqBase,
		get sync() {
			return syncer?.state() ?? null;
		},
		end
	};
}

function marksSafe(id: string): number[] {
	try {
		return marksOf(id).slice(0, 500);
	} catch {
		return [];
	}
}
