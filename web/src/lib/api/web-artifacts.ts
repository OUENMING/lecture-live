// lib/api/web-artifacts.ts —— 服务端 runner 写出来、浏览器只读的两样产物（CONTRACT-runner-20261120 §4 / §5）。
// 对外：getSummary(id) → LiveSummary | null，getNote(id, fmt) → string | null。
// 「还没出」（404 not_ready）是正常状态，回 null，不抛；其余失败照常抛 ApiError，由界面决定怎么说。

import { ApiError, request, type FetchLike } from './http';

export interface ArtifactOptions {
	fetch?: FetchLike;
	deadlineMs?: number;
}

/** summary.json 里的一章（§5：字段名就这几个） */
export interface SummaryChapter {
	idx: number;
	title: string;
	start_seq: number;
	end_seq: number;
	/** false = 正在讲的那一章（只有开章首段） */
	closed: boolean;
	summary: string;
	points: string[];
}

export interface LiveSummary {
	session_id: string;
	updated_at: string;
	/** true = 下课后的最后一版 */
	final: boolean;
	chapters: SummaryChapter[];
}

const SESSION_ID_RE = /^s_[0-9a-f]{12}$/;
const seg = (id: string): string => {
	if (!SESSION_ID_RE.test(id)) throw new Error(`不是服务端的 session_id：${id.slice(0, 24)}`);
	return `/v1/web/session/${id}`;
};

const notReady = (e: unknown) => e instanceof ApiError && e.status === 404;

const str = (v: unknown) => (typeof v === 'string' ? v : '');
const num = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? v : 0);

/** 服务端多给 / 少给字段都不让界面崩：逐字段收窄，章节按 idx 排 */
function normalize(raw: unknown): LiveSummary | null {
	if (!raw || typeof raw !== 'object') return null;
	const r = raw as Record<string, unknown>;
	const chapters = (Array.isArray(r.chapters) ? r.chapters : [])
		.filter((c): c is Record<string, unknown> => !!c && typeof c === 'object')
		.map((c) => ({
			idx: num(c.idx),
			title: str(c.title).trim(),
			start_seq: num(c.start_seq),
			end_seq: num(c.end_seq),
			closed: c.closed !== false,
			summary: str(c.summary).trim(),
			points: (Array.isArray(c.points) ? c.points : []).map(str).map((p) => p.trim()).filter(Boolean)
		}))
		.filter((c) => c.title || c.summary)
		.sort((a, b) => a.idx - b.idx);
	return { session_id: str(r.session_id), updated_at: str(r.updated_at), final: r.final === true, chapters };
}

/** GET /v1/web/session/:id/summary —— 正在上的课每 20 s 左右更新一次；没出回 null */
export async function getSummary(id: string, opts: ArtifactOptions = {}): Promise<LiveSummary | null> {
	try {
		const res = await request<unknown>(`${seg(id)}/summary`, undefined, { call: 'web_session', ...opts, method: 'GET' });
		return normalize(res);
	} catch (e) {
		if (notReady(e)) return null;
		throw e;
	}
}

/** GET /v1/web/session/:id/note?fmt=html|md —— 课后笔记原文；没出回 null */
export async function getNote(id: string, fmt: 'html' | 'md' = 'html', opts: ArtifactOptions = {}): Promise<string | null> {
	try {
		const text = await request<string>(`${seg(id)}/note?fmt=${fmt}`, undefined, {
			call: 'web_session',
			deadlineMs: 20_000, // 一份笔记几百 KB，比领 key 慢
			...opts,
			method: 'GET',
			raw: true
		});
		return text.trim() ? text : null;
	} catch (e) {
		if (notReady(e)) return null;
		throw e;
	}
}
