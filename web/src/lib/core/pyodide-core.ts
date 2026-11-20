// `Core` 的实现：主线程只管发 / 收，Pyodide 在 Worker 里（`worker.ts`），投影在 `project.ts`。
//
// 为什么投影放在主线程：Worker 只当「Python 的嘴」，界面状态留在主线程一处，
// 于是 ViewState 的生成（rev、行 id、草稿→定稿）是纯 TS 纯函数，能单测、能换实现。
import type { ViewState } from '../state/viewstate';
import type { Core } from './index';
import { EMPTY_PROJ, noticeView, project, type ProjState } from './project';
import type { WorkerIn, WorkerOut } from './protocol';

/** 用得到的 Worker 面（收窄，方便测试塞假 worker）。 */
export interface CoreWorker {
	postMessage(msg: WorkerIn): void;
	terminate(): void;
	onmessage: ((ev: MessageEvent<WorkerOut>) => void) | null;
	/** 脚本加载失败 / 崩溃（真 Worker 有；测试假件可以没有） */
	onerror?: ((ev: { message?: string }) => void) | null;
}

export interface PyodideCoreOptions {
	/** 换掉 Worker 的造法；默认是打包进 `./worker.ts` 的那个 */
	makeWorker?: () => CoreWorker;
	/** 课名（进 ViewState.course）；核心不知道课名，由调用方给一次 */
	course?: string;
}

function defaultWorker(): CoreWorker {
	return new Worker(new URL('./worker.ts', import.meta.url), { type: 'module' }) as unknown as CoreWorker;
}

/**
 * 造一个实时核心。返回时 Worker 已经在 boot，`feed` / `tick` 可以先发（排队等 ready）。
 *
 * 不变量：
 * - 每条 feed / tick / flush 至多产出**一版** ViewState，rev 严格递增（`view.apply` 靠它丢旧快照）。
 * - 出错不静默、不清屏：行留着，`status.notice` 挂提示。
 */
export function createCore(opts: PyodideCoreOptions = {}): Core {
	const worker = opts.makeWorker ? opts.makeWorker() : defaultWorker();
	const cbs = new Set<(vs: ViewState) => void>();
	let proj: ProjState = EMPTY_PROJ;
	let last: ViewState | null = null;
	let disposed = false;
	let settle: { resolve: () => void; reject: (e: Error) => void } | null = null;

	const ready = new Promise<void>((resolve, reject) => {
		settle = { resolve, reject };
	});
	// 调用方不一定 await ready（只靠 onView 看提示），别让它变成 unhandled rejection。
	ready.catch(() => {});

	function emit(next: { state: ProjState; view: ViewState }): void {
		proj = next.state;
		last = next.view;
		for (const cb of [...cbs]) {
			try {
				cb(next.view);
			} catch {
				/* 一个订阅者出错不拦别的订阅者 */
			}
		}
	}

	/** 出错：挂提示（不清屏）；还没 ready 就让 ready 失败，免得开课永远停在「启动中」 */
	function failWith(message: string): void {
		try {
			emit(noticeView(proj, message));
		} catch {
			/* 提示也挂不上就只走 reject */
		}
		if (settle) {
			settle.reject(new Error(message));
			settle = null;
		}
	}

	worker.onmessage = (ev: MessageEvent<WorkerOut>) => {
		if (disposed) return;
		const msg = ev.data;
		if (msg.type === 'ready') {
			settle?.resolve();
			settle = null;
			return;
		}
		if (msg.type === 'snapshot') {
			let next;
			try {
				next = project(proj, msg.snap, { course: opts.course });
			} catch (e) {
				failWith(`识别核心回了看不懂的数据：${e instanceof Error ? e.message : String(e)}`);
				return;
			}
			emit(next);
			return;
		}
		failWith(msg.message);
	};
	// Worker 脚本 404 / 被拦 / boot 时崩：不会回任何消息，没有这条 ready 会永远挂着（OCR 11-20 指出，已核）
	worker.onerror = (ev) => {
		if (disposed) return;
		failWith(`识别核心崩了${ev?.message ? `：${ev.message}` : ''}`);
	};

	worker.postMessage({ type: 'init' });

	return {
		ready: () => ready,
		feed: (raw: string) => {
			if (!disposed) worker.postMessage({ type: 'feed', raw });
		},
		tick: (seq: number) => {
			if (!disposed) worker.postMessage({ type: 'tick', seq });
		},
		flush: () => {
			if (!disposed) worker.postMessage({ type: 'flush' });
		},
		onView: (cb: (vs: ViewState) => void) => {
			cbs.add(cb);
			if (last) cb(last); // 晚到的订阅者先拿到当前这版（置顶小窗挂得晚）
		},
		dispose: () => {
			disposed = true;
			if (settle) {
				settle.reject(new Error('disposed'));
				settle = null;
			}
			cbs.clear();
			worker.terminate();
		}
	};
}
