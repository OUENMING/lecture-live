// Node 里没有 Worker：把 Pyodide 直接用起来的 `PythonCore` 包成 `Core`（`lib/core` 的界面）。
//
// 浏览器那条路是 `createCore()`（Worker + postMessage）；这里同一份快照、同一份 `project()`，
// 只是同步调用，所以 LiveSession 的接线在两种「核心适配器」上是同一段代码 —— 这正是 M1-c 想验的。
// 将来若要「无 Worker 的核心」，这个适配器就该搬进 lib/core；现在只有测试用，放在测试目录。
import { project, EMPTY_PROJ, type ProjState } from '../../src/lib/core/index.ts';
import type { Core } from '../../src/lib/core/index.ts';
import type { PythonCore } from '../../src/lib/core/pyodide-runtime.ts';
import type { ViewState } from '../../src/lib/state/viewstate.ts';

export function localCore(py: PythonCore, course = ''): Core {
	let proj: ProjState = { ...EMPTY_PROJ, course };
	let last: ViewState | null = null;
	const cbs = new Set<(vs: ViewState) => void>();

	const emit = (next: { state: ProjState; view: ViewState }): void => {
		proj = next.state;
		last = next.view;
		for (const cb of [...cbs]) cb(next.view);
	};

	return {
		ready: () => Promise.resolve(),
		feed: (raw: string) => emit(project(proj, py.feed(raw), { course })),
		tick: (seq: number) => emit(project(proj, py.tick(seq), { course })),
		flush: () => emit(project(proj, py.flush(), { course })),
		onView: (cb) => {
			cbs.add(cb);
			if (last) cb(last);
		},
		dispose: () => cbs.clear()
	};
}
