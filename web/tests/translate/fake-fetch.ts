// 假 fetch：把一段预置文本当成 SSE 流吐出来，可按任意位置切片（不连网）。
// 对外：`sseResponse(chunks)` 造 `Response`，`fakeFetch(script)` 造 `fetch` 替身并记下请求。
// 为什么自己做 Response：Node 18+ 有全局 Response / ReadableStream，不用额外依赖；
// 切片点显式给定（而不是内部随机），测试才可复现 —— 随机切另有一条 `cutAtEveryByte`。
import assert from 'node:assert/strict';

/** 一段 SSE 文本（每块一个 delta），最后附 [DONE]。 */
export function sseText(deltas: readonly string[]): string {
	let out = '';
	for (const d of deltas) {
		out += `data: ${JSON.stringify({ choices: [{ delta: { content: d } }] })}\n\n`;
	}
	return out + 'data: [DONE]\n\n';
}

/** 把字符串按给定位置切成若干块。 */
export function splitAt(s: string, cuts: readonly number[]): string[] {
	const out: string[] = [];
	let prev = 0;
	for (const c of cuts) {
		out.push(s.slice(prev, c));
		prev = c;
	}
	out.push(s.slice(prev));
	return out.filter((x) => x !== '');
}

/** 造一个流式 Response：每块之间让出一次事件循环（真的分多次 read）。 */
export function sseResponse(parts: readonly string[]): Response {
	const enc = new TextEncoder();
	const stream = new ReadableStream<Uint8Array>({
		async start(ctl) {
			for (const p of parts) {
				ctl.enqueue(enc.encode(p));
				await new Promise<void>((r) => setTimeout(r, 0));
			}
			ctl.close();
		}
	});
	return new Response(stream, { status: 200, headers: { 'content-type': 'text/event-stream' } });
}

export interface Recorded {
	url: string;
	init: RequestInit;
	/** 解析过的请求体（JSON） */
	body: {
		model: string;
		messages: { role: string; content: string }[];
		stream: boolean;
		max_tokens: number;
		temperature: number;
		thinking: { type: string };
	};
	headers: Record<string, string>;
}

export interface Scripted {
	/** 这一次要吐的 SSE 文本；给 `Error` 就抛（模拟连不上） */
	sse?: string;
	/** 或者直接给状态码（模拟 429 / 401） */
	status?: number;
	/** 切片位置 */
	cuts?: number[];
	/** 卡住不返回，直到 signal abort（测取消 / 并发上限） */
	hang?: boolean;
}

/**
 * 假 fetch：按顺序消费 `script`（用完之后重复最后一条），并记录每次请求。
 * `hang` 的那一条在 abort 时抛 AbortError（与浏览器同形）。
 */
export function fakeFetch(script: Scripted[]): {
	fetch: (url: string, init: RequestInit) => Promise<Response>;
	calls: Recorded[];
} {
	const calls: Recorded[] = [];
	let i = 0;
	const fetch = async (url: string, init: RequestInit): Promise<Response> => {
		const body = JSON.parse(String(init.body)) as Recorded['body'];
		calls.push({
			url,
			init,
			body,
			headers: (init.headers ?? {}) as Record<string, string>
		});
		const step = script[Math.min(i, script.length - 1)];
		i += 1;
		if (step.hang) {
			const signal = init.signal as AbortSignal | undefined;
			await new Promise<void>((_, reject) => {
				if (signal?.aborted) return reject(abortErr());
				signal?.addEventListener('abort', () => reject(abortErr()), { once: true });
			});
		}
		if (step.status && step.status !== 200) {
			return new Response(JSON.stringify({ error: 'rate_limited' }), { status: step.status });
		}
		return sseResponse(splitAt(step.sse ?? '', step.cuts ?? []));
	};
	return { fetch, calls };
}

function abortErr(): Error {
	const e = new Error('aborted');
	e.name = 'AbortError';
	return e;
}

/** 断言：请求体就是桌面那一份（逐字）。 */
export function assertDesktopBody(rec: Recorded, expected: { system: string; user: string }): void {
	assert.equal(rec.body.messages.length, 2);
	assert.equal(rec.body.messages[0].role, 'system');
	assert.equal(rec.body.messages[1].role, 'user');
	assert.equal(rec.body.messages[0].content, expected.system);
	assert.equal(rec.body.messages[1].content, expected.user);
}
