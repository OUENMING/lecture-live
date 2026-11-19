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
		for (const cb of [...cbs]) cb(next.view);
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
			emit(project(proj, msg.snap, { course: opts.course }));
			return;
		}
		emit(noticeView(proj, msg.message));
		if (settle) {
			settle.reject(new Error(msg.message));
			settle = null;
		}
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
			cbs.clear();
			worker.terminate();
		}
	};
}
