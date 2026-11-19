// lib/core —— 实时核心：Soniox 原始消息进、ViewState 出。
// G0/S1：Pyodide Web Worker 跑 Python 核心，输出与 CPython 逐字节相同、冷启动 ~1.2 s → 走「一份 Python 两处跑」。
// 核心模块来自 P2 提纯后的 CORE_MODULES；在那之前用 s1/core 的拷贝做演示，不当正式代码。
import type { ViewState } from '../state/viewstate';

export interface Core {
	ready(): Promise<void>;
	/** 一条 Soniox 原始消息 */
	feed(raw: string): void;
	/** 音频帧节拍（推时钟） */
	tick(seq: number): void;
	/**
	 * 下课：把还没封口的尾句强制定稿。
	 *
	 * 与 `tick` 走同一条队列：回来的一版快照一定在这之前所有 feed / tick 之后。
	 * 只封一次 —— 重复调不会出重复句（`StreamCore.flush` 自己清 token）。
	 */
	flush(): void;
	onView(cb: (vs: ViewState) => void): void;
	dispose(): void;
}

// 实现（`pyodide-core.ts` 造 Worker、`project.ts` 出 ViewState、`protocol.ts` 是两侧的契约）。
export { createCore, type PyodideCoreOptions, type CoreWorker } from './pyodide-core';
export { project, noticeView, EMPTY_PROJ, type Snapshot, type ProjState, type SealedLine } from './project';
export { PYODIDE_VERSION, PYODIDE_CDN, PYODIDE_VENDOR, CORE_PY_ROOT, loadCore, importPyodide, originBase } from './pyodide-runtime';
