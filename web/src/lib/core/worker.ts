// 实时核心的 Web Worker：Pyodide + `static/core-py/` 的 Python 源。
//
// 主线程只发 init / feed / tick / flush；每处理一拍回一版快照。Python 调用都是同步的，
// 所以消息的先后就是处理的先后（不用 id 对齐）；boot 期间到的消息排在 init 后面。
// 这里只做搬运：投影在 `project.ts`，装载面在 `pyodide-runtime.ts`。
import type { WorkerIn, WorkerOut } from './protocol';
import { CORE_PY_ROOT, loadCore, importPyodide, type CoreLoad } from './pyodide-runtime';

interface WorkerScope {
	postMessage(msg: WorkerOut): void;
	onmessage: ((ev: MessageEvent<WorkerIn>) => void) | null;
}

const scope = globalThis as unknown as WorkerScope;

/** 拉 manifest 里列的文件，原样写进虚拟文件系统。Pyodide 本体同源优先（`importPyodide`）。 */
async function boot(): Promise<CoreLoad> {
	const { mod, indexURL } = await importPyodide();
	const res = await fetch(`${CORE_PY_ROOT}/manifest.json`);
	if (!res.ok) throw new Error(`core-py/manifest.json: HTTP ${res.status}`);
	const { files } = (await res.json()) as { files: string[] };
	return loadCore({
		load: () => mod.loadPyodide({ indexURL, stdout: () => {} }),
		read: async (rel) => {
			const r = await fetch(`${CORE_PY_ROOT}/${rel}`);
			if (!r.ok) throw new Error(`core-py/${rel}: HTTP ${r.status}`);
			return new Uint8Array(await r.arrayBuffer());
		},
		manifest: files
	});
}

let core: CoreLoad | null = null;
/** 串成一条链：init 一定最先，feed / tick 严格排队。 */
let chain: Promise<void> = Promise.resolve();

async function handle(msg: WorkerIn): Promise<void> {
	if (msg.type === 'init') {
		const c = await boot();
		core = c;
		scope.postMessage({
			type: 'ready',
			ms_boot: c.ms_boot,
			pyodide_version: c.pyodideVersion,
			python_version: c.pythonVersion,
			staged_bytes: c.stagedBytes
		});
		return;
	}
	if (!core) throw new Error(`core 还没 ready 就收到 ${msg.type}`);
	const snap = msg.type === 'feed' ? core.feed(msg.raw) : msg.type === 'flush' ? core.flush() : core.tick(msg.seq);
	scope.postMessage({ type: 'snapshot', snap });
}

scope.onmessage = (ev: MessageEvent<WorkerIn>) => {
	const msg = ev.data;
	chain = chain.then(async () => {
		try {
			await handle(msg);
		} catch (e) {
			// 一条消息出错不结束会话：回一条 error，主线程挂到 status.notice 上继续。
			scope.postMessage({ type: 'error', message: String(e instanceof Error ? e.message : e).slice(0, 500) });
		}
	});
};
