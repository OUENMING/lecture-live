// 主线程 ↔ Pyodide Worker 的消息契约。只过结构化可克隆的数据，没有回调。
//
// 方向：init → ready（一次）→ feed / tick / flush … → snapshot（每次一拍一版）；任何一步出错回 error。
// 顺序即语义：postMessage 保序，Worker 里每条消息同步处理，所以 snapshot 的先后就是 feed/tick 的先后。
// `flush` 是下课那一条：把还没封口的尾句强制定稿（`rt.py` → `StreamCore.flush()`）。
import type { Snapshot } from './project';

export type WorkerIn =
	| { type: 'init' }
	| { type: 'feed'; raw: string }
	| { type: 'tick'; seq: number }
	| { type: 'flush' };

export type WorkerOut =
	| {
			type: 'ready';
			/** 从 new Worker() 到 import rt 完成（ms），冷启动量具 */
			ms_boot: number;
			/** 从 CDN / node_modules 真下载的 Pyodide 字节数（除 .py 外） */
			pyodide_version: string;
			python_version: string;
			/** 写进虚拟文件系统的 .py + 数据字节数 */
			staged_bytes: number;
	  }
	| { type: 'snapshot'; snap: Snapshot }
	| { type: 'error'; message: string };
