// lib/api/web-session.ts —— 网页会话账本的六个接口（docs/API-web-v0-20261117.md §3）。
// 对外：startSession / putLines / endSession / getSession / listSessions / deleteAll。
// 一律经 `lib/api/http.request`（唯一网络出口）+ `identity.headers('web')`，不自己 fetch；
// 失败只抛 `ApiError`，kind 里带「这节课怎么了」（not_found / gone / too_large）由调用方决定退不退。
// 时间在对外接口上是 epoch 毫秒（与本机 store 一致），在这里转服务端要的 ISO 字符串。

import { request, type FetchLike } from './http';

/** 调用方可以不传网络选项：测试注入假 fetch，生产走默认 */
export interface WebSessionOptions {
	fetch?: FetchLike;
	/** 总墙钟；推送一批句子 / 下课比领 key 长，默认仍交给 http.ts（10 s） */
	deadlineMs?: number;
}

/** 一批里的一句（`seq` 由 `lib/store` 发号，从 1 起连续） */
export interface WireLine {
	seq: number;
	t: number;
	en: string;
	zh: string;
	state?: 'final' | 'draft';
	src?: string;
}

export interface StartResult {
	session_id: string;
	server_time: string;
	limits?: { max_session_s: number; lines_batch: number; body_bytes: number };
}

export interface PutResult {
	confirmed_seq: number;
	high_water: number;
	received: number;
	ignored: number;
}

export interface EndResult {
	job_id: string;
	status: string;
	dedupe: boolean;
}

/** `GET /v1/web/session/:id` 的回包（只列界面用得上的字段；服务端多给的不清掉） */
export interface SessionView {
	session_id: string;
	course: string;
	state: string;
	created_at: string;
	ended_at: string | null;
	lines_confirmed: number;
	note: {
		job_id: string;
		state: 'queued' | 'running' | 'done' | 'failed';
		attempt: number;
		note_md: string | null;
		note_html: string | null;
		expires_at: string | null;
	} | null;
	tombstone: unknown;
}

export interface SessionSummary {
	session_id: string;
	course: string;
	state: string;
	created_at: string;
	lines_confirmed: number;
	note: { job_id: string; state: string } | null;
}

export interface ListResult {
	sessions: SessionSummary[];
	next_before: string | null;
}

/** 服务端 session_id 的形状（路径里会再校验一次；这里先挡住把本地 id 拼进 URL） */
const SESSION_ID_RE = /^s_[0-9a-f]{12}$/;
const SEG = (id: string): string => {
	if (!SESSION_ID_RE.test(id)) throw new Error(`不是服务端的 session_id：${id.slice(0, 24)}`);
	return `/v1/web/session/${id}`;
};

/** epoch 毫秒 → 服务端要的 ISO（UTC，秒精度）；非法值退回现在 */
const iso = (ms: number | undefined): string => {
	const d = typeof ms === 'number' && Number.isFinite(ms) ? new Date(ms) : new Date();
	return Number.isNaN(d.getTime()) ? new Date().toISOString() : d.toISOString();
};

const base = (opts: WebSessionOptions) => ({ call: 'web_session', ...opts });

/**
 * 开课。带 `Idempotency-Key`（本机会话 id）时 24 h 内重复提交回同一个 `session_id`（200），
 * 所以「断网没收到回包 → 重试」不会在服务器上开出第二节课。
 */
export async function startSession(
	course: string,
	started_at: number,
	idemKey: string,
	opts: WebSessionOptions = {}
): Promise<StartResult> {
	const res = await request<Partial<StartResult>>(
		'/v1/web/session',
		{ course, started_at: iso(started_at) },
		{ ...base(opts), headers: { 'Idempotency-Key': idemKey } }
	);
	if (!res.session_id) throw new Error('开课回包里没有 session_id'); // 契约破了就抛，不装成功
	return { session_id: res.session_id, server_time: res.server_time ?? '', limits: res.limits };
}

/** 追加逐句。服务器按 (session_id, seq) 先到先得；回 `confirmed_seq`（从 1 起连续确认到哪）。 */
export async function putLines(
	id: string,
	lines: WireLine[],
	opts: WebSessionOptions = {}
): Promise<PutResult> {
	const res = await request<Partial<PutResult>>(`${SEG(id)}/lines`, { lines }, {
		...base(opts),
		method: 'PUT'
	});
	return {
		confirmed_seq: res.confirmed_seq ?? 0,
		high_water: res.high_water ?? 0,
		received: res.received ?? lines.length,
		ignored: res.ignored ?? 0
	};
}

/** 下课 → 笔记任务。重复调用回同一个 `job_id`（`dedupe=true`），不会出第二份笔记。 */
export async function endSession(
	id: string,
	info: { ended_at: number; lines_total: number; level?: string },
	opts: WebSessionOptions = {}
): Promise<EndResult> {
	const res = await request<Partial<EndResult>>(
		`${SEG(id)}/end`,
		{ ended_at: iso(info.ended_at), lines_total: info.lines_total, level: info.level ?? 'std' },
		base(opts)
	);
	return { job_id: res.job_id ?? '', status: res.status ?? 'queued', dedupe: res.dedupe ?? false };
}

/** 这节课的状态 / 笔记地址（只读）。 */
export async function getSession(id: string, opts: WebSessionOptions = {}): Promise<SessionView> {
	const res = await request<SessionView>(SEG(id), undefined, { ...base(opts), method: 'GET' });
	if (!res || typeof res.session_id !== 'string') throw new Error('会话回包形状不对');
	return res;
}

/** 修改课名（课名可下课后再填）。服务器回新值；404 就是「服务器上找不到这节课」。 */
export async function renameSession(
	id: string,
	course: string,
	opts: WebSessionOptions = {}
): Promise<{ session_id: string; course: string }> {
	const res = await request<Partial<{ session_id: string; course: string }>>(SEG(id), { course }, {
		...base(opts),
		method: 'PATCH'
	});
	return { session_id: res.session_id ?? id, course: res.course ?? course };
}

/** 这枚码的历史列表（只有元数据，不含逐句）。 */
export async function listSessions(
	opts: WebSessionOptions & { limit?: number; before?: string } = {}
): Promise<ListResult> {
	const q = new URLSearchParams();
	if (opts.limit) q.set('limit', String(Math.min(50, opts.limit)));
	if (opts.before) q.set('before', opts.before);
	const qs = q.toString();
	const res = await request<Partial<ListResult>>(`/v1/web/sessions${qs ? `?${qs}` : ''}`, undefined, {
		...base(opts),
		method: 'GET'
	});
	return { sessions: res.sessions ?? [], next_before: res.next_before ?? null };
}

/** 删除这枚码名下的全部数据；之后 `GET` 回 410 `session_deleted`。 */
export async function deleteAll(opts: WebSessionOptions = {}): Promise<void> {
	await request('/v1/web/data', undefined, { ...base(opts), method: 'DELETE' });
}

/** 界面 / 同步层判断「要不要再试」只看 kind，不看状态码 */
export type { ApiErrorKind } from './http';
