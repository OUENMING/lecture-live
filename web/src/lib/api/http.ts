// lib/api/http.ts —— 到 Worker 的唯一网络出口：基址、总墙钟、错误归一。
// 对外：request(path, body, {deadlineMs, fetch, code}) -> 已解析的 JSON；失败只抛 ApiError。
// 为什么归一到五个 kind：界面只关心「码不行 / 额度没了 / 设备满了 / 网络不通 / 服务端挂了」，
// 状态码与响应体形状（JSON `{error}` 或纯文本）留在这里，不往界面漏。
// 为什么是总墙钟：浏览器里挂住的连接会让每段读超时一个个续命，桌面版为此加了 CALL_DEADLINE_S
// （~/lecture-live/llm_client.py:65）—— 这里用 AbortController 给出同一个语义。

import { headers } from './identity';

export type ApiErrorKind =
	| 'passcode'
	| 'quota'
	| 'devices'
	| 'network'
	| 'server'
	| 'not_found'
	| 'gone'
	| 'too_large';

/** Worker 的拒绝说的是什么，翻成「人话」（口径抄 `keyentry.PASSCODE_REASONS`，多两条不是码问题的） */
const REASONS: Record<string, { kind: ApiErrorKind; say: string }> = {
	unknown: { kind: 'passcode', say: '通行码不对 —— 检查是不是复制全了' },
	revoked: { kind: 'passcode', say: '通行码已停用 —— 问给你码的人' },
	expired: { kind: 'passcode', say: '通行码过期了 —— 问给你码的人续期' },
	devices: { kind: 'devices', say: '这个码能用的设备数满了 —— 问给你码的人' },
	quota_exceeded: { kind: 'quota', say: '今天的额度用完了，明天自动恢复' },
	rate_limited: { kind: 'quota', say: '请求太密了，等一分钟再试' },
	// 网页会话账本（API-web-v0 §3）：这几种是「这节课怎么了」，不是码的问题
	not_found: { kind: 'not_found', say: '服务器上没有这节课 —— 可能换过通行码' },
	session_ended: { kind: 'server', say: '这节课已经下课了 —— 后续的句子不再上传' },
	seq_too_far: { kind: 'server', say: '句子编号跳得太远了 —— 先停一下再试' },
	session_deleted: { kind: 'gone', say: '这节课的数据已删除 —— 不再同步' },
	too_large: { kind: 'too_large', say: '一次发得太多了 —— 会自动拆小再发' },
	bad_body: { kind: 'server', say: '这一条内容不合要求 —— 改一下再试' },
	passes_unavailable: { kind: 'server', say: '通行码服务暂时不可用 —— 稍后再试' },
	soniox_not_configured: { kind: 'server', say: '转写服务还没配好 —— 稍后再试' },
	soniox_upstream: { kind: 'server', say: '转写服务（Soniox）暂时不可用 —— 稍后再试' }
};

export class ApiError extends Error {
	readonly kind: ApiErrorKind;
	/** Worker 响应体的 `error`（没有就是 `deadline` / `fetch` / `http_<状态码>`） */
	readonly reason: string;
	readonly status: number;

	constructor(kind: ApiErrorKind, reason: string, message: string, status = 0) {
		super(message);
		this.name = 'ApiError';
		this.kind = kind;
		this.reason = reason;
		this.status = status;
	}
}

/** 基址：`VITE_API_BASE`（打包时注入，可在 `.env` 里改）→ 默认线上 Worker */
export const DEFAULT_BASE = 'https://asr.bldcam.page';

export function apiBase(): string {
	const env = (import.meta as unknown as { env?: Record<string, string | undefined> }).env;
	return (env?.VITE_API_BASE || DEFAULT_BASE).replace(/\/+$/, '');
}

/** 一次调用的总墙钟；领 key / 查码这类短调用默认给 10 s */
export const DEFAULT_DEADLINE_MS = 10_000;

export type FetchLike = (url: string, init: RequestInit) => Promise<Response>;

export interface RequestOptions {
	/** 总墙钟（毫秒）：到点 abort 并抛 `network / deadline` */
	deadlineMs?: number;
	/** 注入的 fetch（测试用假实现；默认浏览器全局） */
	fetch?: FetchLike;
	/** 用哪枚通行码（默认读存下的那枚；查码时要传「用户刚输的」） */
	code?: string;
	/** 调用类，进 `X-ClassLive-Call`（`web_session` 等；不给就不带这个头） */
	call?: string;
	/** HTTP 方法；默认 `POST`（网页会话的 `PUT` / `PATCH` / `GET` / `DELETE` 走这里） */
	method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
	/** 额外请求头（`Idempotency-Key` 这类）；调用方给，这里只合并 */
	headers?: Record<string, string>;
	/** 回原文不解析 JSON（课后笔记 HTML / Markdown 这类产物） */
	raw?: boolean;
}

function byStatus(status: number, reason: string): { kind: ApiErrorKind; say: string } {
	const known = REASONS[reason];
	if (known) return known;
	if (status === 401) return { kind: 'passcode', say: '通行码没生效 —— 重新输一次' };
	if (status === 403) return { kind: 'passcode', say: '这个通行码现在不能用 —— 问给你码的人' };
	if (status === 404) return { kind: 'not_found', say: '服务器上找不到这东西 —— 可能换过通行码' };
	if (status === 410) return { kind: 'gone', say: '这份数据已删除 —— 不再同步' };
	if (status === 413) return { kind: 'too_large', say: '一次发得太多了 —— 会自动拆小再发' };
	// 429 也可能是限流（纯文本 `rate limited`），一律当「等会儿再来」
	if (status === 429) return { kind: 'quota', say: '请求太密了，等一分钟再试' };
	return { kind: 'server', say: `服务器出错了（${status}）—— 稍后再试` };
}

function fail(status: number, text: string): ApiError {
	let reason = '';
	try {
		const js = JSON.parse(text) as { error?: unknown };
		reason = typeof js?.error === 'string' ? js.error : '';
	} catch {
		reason = text.trim().slice(0, 40);
	}
	const { kind, say } = byStatus(status, reason);
	return new ApiError(kind, reason || `http_${status}`, say, status);
}

/** 请求头：默认那套 + 本次要用的码（`code` 缺省就不覆盖） */
function sendHeaders(code?: string, call?: string): Record<string, string> {
	const h = headers(call);
	if (code) h.Authorization = `Bearer ${code}`;
	return h;
}

/**
 * 发一个 JSON 到 Worker 并解析回来（`method` 默认 POST）。所有失败都是 `ApiError`（网络类 kind='network'）。
 * 空响应体当 `{}`；状态码 2xx 但 body 不是 JSON 也当空对象（调用方自己判字段）。
 */
export async function request<T = unknown>(
	path: string,
	body: unknown,
	opts: RequestOptions = {}
): Promise<T> {
	const deadline = opts.deadlineMs ?? DEFAULT_DEADLINE_MS;
	const doFetch: FetchLike = opts.fetch ?? ((url, init) => fetch(url, init));
	const deadlineError = () =>
		new ApiError(
			'network',
			'deadline',
			`等太久没回音（${Math.round(deadline / 1000)} 秒），先停一下再试`
		);
	const ctl = new AbortController();
	let timer: ReturnType<typeof setTimeout> | undefined;
	const deadlineHit = new Promise<never>((_, reject) => {
		timer = setTimeout(() => {
			ctl.abort();
			reject(deadlineError());
		}, deadline);
	});

	let res: Response;
	let text: string;
	try {
		const method = opts.method ?? 'POST';
		const call = doFetch(apiBase() + path, {
			method,
			headers: {
				'Content-Type': 'application/json',
				...sendHeaders(opts.code, opts.call),
				...(opts.headers ?? {})
			},
			body: method === 'GET' ? undefined : JSON.stringify(body ?? {}),
			signal: ctl.signal
		});
		res = await Promise.race([call, deadlineHit]);
		text = await Promise.race([res.text(), deadlineHit]);
	} catch (e) {
		if (e instanceof ApiError) throw e;
		// abort 只有一个来源（上面的定时器）：那块报错其实都是「到点了」
		if (ctl.signal.aborted) throw deadlineError();
		throw new ApiError('network', 'fetch', '连不上服务器 —— 检查网络或稍后再试');
	} finally {
		clearTimeout(timer);
	}

	if (!res.ok) throw fail(res.status, text);
	if (opts.raw) return text as T;
	if (!text.trim()) return {} as T;
	try {
		return JSON.parse(text) as T;
	} catch {
		return {} as T;
	}
}
