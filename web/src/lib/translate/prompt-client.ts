// 提示词 Worker 的调用端：`createPrompt()` → 一个 `PromptBuilder`，消息往返按 id 对齐。
// 为什么单独这个文件：`prompt-worker.ts` 跑在 Worker 里（没有 DOM、不能 import 主线程的东西），
// 而主线程要的只是一个跟 `pyPrompt` 同形的 async 接口 —— 测试可以塞 `pyPrompt(本地 Pyodide)`，
// 真链路塞这个，`Translator` 一行都不用改。
// 失败不静默：任何一次调用超时 / Worker 报错都 reject，由 `Translator` 降级成「这句没译出」。

import type { DraftBegin, Prompt, PromptBuilder, TermConfig } from './prompt';

/** 用得到的那点 Worker 面（收窄，方便测试塞假的）。 */
export interface PromptWorker {
	postMessage(msg: unknown): void;
	terminate(): void;
	onmessage: ((ev: MessageEvent<unknown>) => void) | null;
	onerror?: ((ev: unknown) => void) | null;
}

export interface PromptClientOptions {
	/** 造 Worker；默认打包进 `./prompt-worker.ts` 的那个 */
	makeWorker?: () => PromptWorker;
	/** 单次调用的墙钟（毫秒）；默认 15 s（Pyodide 冷启动约 1.2 s，重活到不了这么久） */
	deadlineMs?: number;
}

type Reply = { type: 'ready' } | { type: 'reply'; id: number; result: unknown } | { type: 'error'; id: number; message: string };

function defaultWorker(): PromptWorker {
	return new Worker(new URL('./prompt-worker.ts', import.meta.url), {
		type: 'module'
	}) as unknown as PromptWorker;
}

type Waiter = { resolve: (v: unknown) => void; reject: (e: Error) => void; timer: ReturnType<typeof setTimeout> };

/**
 * 造一个提示词客户端。构造即开始 boot；`ready()` 等到 Python 可用。
 *
 * 不变量：同一个 `id` 的回复只送一次；Worker 报错 / 超时后这个 id 的 Promise 一定 reject
 * （挂住的 Promise 会让一句定稿永远排在队列头，后面的字幕全跟着停）。
 */
export function createPrompt(opts: PromptClientOptions = {}): PromptBuilder & { ready(): Promise<void>; dispose(): void } {
	const worker = opts.makeWorker ? opts.makeWorker() : defaultWorker();
	const deadline = opts.deadlineMs ?? 15_000;
	const waiters = new Map<number, Waiter>();
	let nextId = 1;
	let disposed = false;
	const { promise: ready, resolve: booted } = Promise.withResolvers<PromptBuilder>();
	ready.catch(() => {});

	const rejectAll = (msg: string): void => {
		for (const [id, w] of waiters) {
			clearTimeout(w.timer);
			w.reject(new Error(msg));
			waiters.delete(id);
		}
	};

	worker.onmessage = (ev: MessageEvent<unknown>) => {
		if (disposed) return;
		const msg = ev.data as Reply;
		if (msg?.type === 'ready') {
			booted(client);
			return;
		}
		if (msg?.type === 'error' && msg.id === 0) {
			rejectAll(msg.message);
			return;
		}
		if (msg?.type === 'reply') {
			const w = waiters.get(msg.id);
			if (!w) return;
			clearTimeout(w.timer);
			waiters.delete(msg.id);
			w.resolve(msg.result);
			return;
		}
		if (msg?.type === 'error') {
			const w = waiters.get(msg.id);
			if (!w) return;
			clearTimeout(w.timer);
			waiters.delete(msg.id);
			w.reject(new Error(msg.message));
		}
	};
	worker.onerror = () => rejectAll('提示词 Worker 挂了（Pyodide 没起来）');

	const call = (name: string, args: readonly unknown[]): Promise<unknown> => {
		if (disposed) return Promise.reject(new Error('提示词 Worker 已关闭'));
		const id = nextId++;
		return new Promise<unknown>((resolve, reject) => {
			const timer = setTimeout(() => {
				waiters.delete(id);
				reject(new Error(`提示词 Worker ${Math.round(deadline / 1000)} 秒没回话`));
			}, deadline);
			waiters.set(id, { resolve, reject, timer });
			worker.postMessage({ type: 'call', id, name, args });
		});
	};

	const client: PromptBuilder & { ready(): Promise<void>; dispose(): void } = {
		ready: () => ready.then(() => undefined),
		final: (en: string, context: readonly string[], cfg?: TermConfig) =>
			call('build_request', [en, context, cfg]).then((r) => r as Prompt),
		draft: (en: string, context: readonly string[], cfg?: TermConfig) =>
			call('build_draft_request', [en, context, cfg]).then((r) => r as Prompt),
		draftBegin: (seg: number, text: string) =>
			call('draft_begin', [seg, text]).then((r) => r as DraftBegin),
		draftAccept: (seq: number, seg: number, gen: number) =>
			call('draft_accept', [seq, seg, gen]).then((r) => Boolean(r)),
		draftFinal: (seg: number) => call('draft_final', [seg]).then((r) => Boolean(r)),
		dispose: () => {
			disposed = true;
			rejectAll('提示词 Worker 已关闭');
			worker.terminate();
		}
	};

	worker.postMessage({ type: 'init' });
	return client;
}
