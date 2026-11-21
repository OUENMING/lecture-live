// 一次流式对话请求：POST `{base}/v1/chat/completions` → 增量文本回调。
// 对外：`streamChat({...})` → `Promise<string>`（完整文本）；`aborted` / 失败一律抛 `ChatError`。
// 为什么自己发 fetch 而不用 `lib/api/http.request`：那个出口要先把整个响应体读完再 JSON.parse，
// 而这里要的是「首字一到就能上屏」（闸门：一句说完 → 中文首字 ≤ 2 s）。
// 请求体逐字照桌面 `cloud_translator._stream_chat`（stream_options 不开、thinking 显式关掉）：
// 开着思考模式会让 DeepSeek V4 先吐 reasoning_content，首字延迟翻几倍。

import { SseParser } from './sse';

/** Worker 只认这两个模型名（`handler.js` 的 LLM_MODELS）；桌面默认也用这个。 */
export const MODEL = 'deepseek-flash';

/** 桌面 `fix_and_translate_stream` / `translate_draft` 的 max_tokens。 */
export const MAX_TOKENS = { final: 220, draft: 120 } as const;

/** 定稿输出预算的上限：Worker 把网页请求封顶在 400（也是快路由的门槛）。 */
export const FINAL_MAX_TOKENS_MAX = 400;

/**
 * 定稿（ZH + EN 两段）的 max_tokens：短句 220，长句按原文长度加（0.75 token/字符），封顶 400。
 * ⭐ 2026-11-20：488 字符的句子 220 不够，EN 段被截断。桌面 `cloud_translator.final_max_tokens` 同口径。
 */
export function finalMaxTokens(en: string): number {
	return Math.max(MAX_TOKENS.final, Math.min(FINAL_MAX_TOKENS_MAX, Math.round((en || '').length * 0.75)));
}

/** 一次调用的总墙钟（毫秒）：桌面 `TIMEOUT_S = 30`，但首字等不到就该放弃，后面不许卡住。 */
export const DEADLINE_MS = 20_000;

export interface ChatError extends Error {
	/** 调用方靠它区分「我取消的」与「真失败」 */
	readonly aborted: boolean;
	readonly status: number;
}

function chatError(message: string, opts: { aborted?: boolean; status?: number } = {}): ChatError {
	const e = new Error(message) as Error & { aborted: boolean; status: number };
	e.name = 'ChatError';
	e.aborted = opts.aborted === true;
	e.status = opts.status ?? 0;
	return e as ChatError;
}

export interface StreamChatOptions {
	/** 完整 URL（`apiBase() + '/v1/chat/completions'`） */
	url: string;
	/** `lib/api/identity.headers('translate')` 那一套（含通行码） */
	headers: Record<string, string>;
	prompt: { system: string; user: string };
	maxTokens: number;
	signal?: AbortSignal;
	/** 每来一个增量文本调一次（首字打点就挂在这儿） */
	onDelta?: (delta: string) => void;
	/** 注入的 fetch（测试用假实现） */
	fetch?: (url: string, init: RequestInit) => Promise<Response>;
	/** 注入的墙钟（默认全局 setTimeout） */
	deadlineMs?: number;
}

/** 从 SSE 负载里取出一段增量文本；不是 JSON / 没有 content 的块直接跳过（桌面同口径）。 */
function deltaOf(payload: string): string {
	try {
		const obj = JSON.parse(payload) as {
			choices?: { delta?: { content?: unknown } }[];
		};
		const c = obj.choices?.[0]?.delta?.content;
		return typeof c === 'string' ? c : '';
	} catch {
		return '';
	}
}

/**
 * 发一次流式请求，边收边回调。返回拼好的完整文本。
 *
 * 不变量：`onDelta` 只会在 `signal` 未 abort 时被调用；函数返回时响应体一定已被读完或丢弃
 * （否则浏览器会把这条连接一直挂着，草稿并发会互相拖）。
 */
export async function streamChat(opts: StreamChatOptions): Promise<string> {
	const doFetch = opts.fetch ?? ((url, init) => fetch(url, init));
	const deadline = opts.deadlineMs ?? DEADLINE_MS;
	const ctl = new AbortController();
	const onOuterAbort = (): void => ctl.abort();
	if (opts.signal) {
		if (opts.signal.aborted) throw chatError('已取消', { aborted: true });
		opts.signal.addEventListener('abort', onOuterAbort, { once: true });
	}
	let timedOut = false;
	const timer = setTimeout(() => {
		timedOut = true;
		ctl.abort();
	}, deadline);

	const body = {
		model: MODEL,
		messages: [
			{ role: 'system', content: opts.prompt.system },
			{ role: 'user', content: opts.prompt.user }
		],
		stream: true,
		max_tokens: opts.maxTokens,
		temperature: 0.2,
		thinking: { type: 'disabled' }
	};

	try {
		let res: Response;
		try {
			res = await doFetch(opts.url, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json', ...opts.headers },
				body: JSON.stringify(body),
				signal: ctl.signal
			});
		} catch (e) {
			if (ctl.signal.aborted) throw abortReason(timedOut, opts.signal, deadline);
			throw chatError(`连不上翻译服务（${errText(e)}）`);
		}
		if (!res.ok) {
			const text = await res.text().catch(() => '');
			throw chatError(`翻译服务出错（${res.status}）${text.slice(0, 80)}`, { status: res.status });
		}
		if (!res.body) throw chatError('翻译服务的回包没有流');
		const reader = res.body.getReader();
		const decoder = new TextDecoder();
		const sse = new SseParser();
		const chunks: string[] = [];
		const take = (payloads: string[]): void => {
			for (const p of payloads) {
				const d = deltaOf(p);
				if (!d) continue;
				chunks.push(d);
				opts.onDelta?.(d);
			}
		};
		try {
			for (;;) {
				const { done, value } = await reader.read();
				if (done) break;
				take(sse.push(decoder.decode(value, { stream: true })));
				if (sse.done) break;
			}
			take(sse.push(decoder.decode()));
			take(sse.end());
		} catch (e) {
			if (ctl.signal.aborted) throw abortReason(timedOut, opts.signal, deadline);
			throw chatError(`读翻译流断了（${errText(e)}）`);
		} finally {
			reader.cancel().catch(() => {});
		}
		return chunks.join('');
	} finally {
		clearTimeout(timer);
		opts.signal?.removeEventListener('abort', onOuterAbort);
	}
}

function abortReason(timedOut: boolean, outer: AbortSignal | undefined, deadline: number): ChatError {
	if (timedOut) return chatError(`翻译服务 ${Math.round(deadline / 1000)} 秒没给完`, { aborted: true });
	if (outer?.aborted) return chatError('已取消（有更新的草稿）', { aborted: true });
	return chatError('已取消', { aborted: true });
}

function errText(e: unknown): string {
	return e instanceof Error ? e.message.slice(0, 120) : String(e).slice(0, 120);
}
