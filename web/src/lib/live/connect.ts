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

export interface LiveHandle {
	session: LiveSession;
	/** 本机落盘（打不开 IndexedDB 时是内存兜底，`persistent=false`） */
	store: LineStore | null;
	session_id: string;
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
	const first: SonioxKey = await getSonioxKey();
	let spare: string | null = first.api_key;
	const getKey = async () => {
		if (spare) {
			const k = spare;
			spare = null;
			return k;
		}
		return (await getSonioxKey(first.region || undefined)).api_key;
	};

	const session_id = newSessionId();
	let store: LineStore | null = null;
	let syncer: ReturnType<typeof attachSync> | null = null;
	try {
		store = await openStore();
		await store.openSession(session_id, course);
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
		const t_s = session.rows.find((r) => r.id === seg)?.t ?? 0;
		writing = store?.append({ session_id, seq: seg, en, zh, t_s }).catch(() => {}) ?? writing;
		syncer?.push();
	};
	const mic = createMicSource();
	const session: LiveSession = new LiveSession({
		audio: mic,
		core,
		getKey,
		asr: (gk) => createSonioxClient({ getKey: gk, url: first.ws_url || FALLBACK_WS, context: sonioxContext }),
		makeTranslator: (hooks) =>
			new Translator({
				prompt,
				cfg: terms.length ? { terms } : undefined,
				endpoint: () => ({ url: `${apiBase()}/v1/chat/completions`, headers: headers() }),
				onFinal: (ev) => {
					hooks.onFinal(ev);
					if (ev.done || ev.failed) persist(ev.seg, ev.en, ev.failed ? '' : ev.zh);
				},
				onDraft: hooks.onDraft
			})
	});

	let ending: Promise<void> | null = null;
	const end = () =>
		(ending ??= (async () => {
			await session.end();
			// 收尾时还没译完的句子：按现状补写（已写过的不会被覆盖）
			for (const r of session.rows) persist(r.id, r.en, r.zh);
			await writing.catch(() => {});
			// 先把最后几句推完再记下课：顺序反过来服务端会拒收尾批（409 session_ended）
			await syncer?.end().catch(() => {});
			await store?.closeSession(session_id).catch(() => {});
			core.dispose();
			prompt.dispose();
		})());

	return {
		audio: mic,
		session,
		store,
		session_id,
		get sync() {
			return syncer?.state() ?? null;
		},
		end
	};
}
