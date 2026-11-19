// 拼请求体的 Web Worker：单独一份 Pyodide 跑 `static/core-py/prompt_core.py`（同一份桌面代码）。
// 对外：worker 只认 `{type:'init'}` / `{type:'call', id, name, args}`，回 `{type:'ready'}` / `{type:'reply', id, result}`。
// 为什么要第二份 Pyodide：核心那份 Worker（lib/core）只暴露 feed / tick / flush，本任务不许改它；
// 于是这里按 `manifest.json` 再 stage 一遍同一批 .py（同源已缓存，增量成本主要是内存）。
// 为什么不在这里拼字符串：逐字相同靠 prompt_core 自己，任何一条提示词改字都会让服务端白名单拒收。

import { CORE_PY_ROOT, importPyodide } from '../core/pyodide-runtime';
import { pyPromptFromPyodide, type PromptBuilder } from './prompt';

export interface PromptCall {
	type: 'call';
	id: number;
	name: string;
	args: readonly unknown[];
}

type In = { type: 'init' } | PromptCall;

interface Scope {
	postMessage(msg: unknown): void;
	onmessage: ((ev: MessageEvent<In>) => void) | null;
}

const scope = globalThis as unknown as Scope;

let prompt: PromptBuilder | null = null;
let chain: Promise<void> = Promise.resolve();

async function boot(): Promise<PromptBuilder> {
	const { mod, indexURL } = await importPyodide();
	const res = await fetch(`${CORE_PY_ROOT}/manifest.json`);
	if (!res.ok) throw new Error(`core-py/manifest.json: HTTP ${res.status}`);
	const { files } = (await res.json()) as { files: string[] };
	const py = await mod.loadPyodide({ indexURL, stdout: () => {} });
	for (const rel of files) {
		const r = await fetch(`${CORE_PY_ROOT}/${rel}`);
		if (!r.ok) throw new Error(`core-py/${rel}: HTTP ${r.status}`);
		const dst = `${CORE_PY_ROOT}/${rel}`;
		py.FS.mkdirTree(dst.slice(0, dst.lastIndexOf('/')));
		py.FS.writeFile(dst, new Uint8Array(await r.arrayBuffer()));
	}
	py.runPython(
		`import sys\nif ${JSON.stringify(CORE_PY_ROOT)} not in sys.path: sys.path.insert(0, ${JSON.stringify(CORE_PY_ROOT)})\nimport rt\nfrom rt import *`
	);
	// ⚠️ 只 `import rt` 的话 globals 里只有模块 rt，pyPromptFromPyodide 按名字取 build_request 会全失败
	// （2026-11-18 浏览器实测：「core-py 没导出 build_request」→ 一句都没送翻）。
	return pyPromptFromPyodide(py);
}

scope.onmessage = (ev: MessageEvent<In>) => {
	const msg = ev.data;
	chain = chain.then(async () => {
		try {
			if (msg.type === 'init') {
				prompt = await boot();
				scope.postMessage({ type: 'ready' });
				return;
			}
			if (!prompt) throw new Error('prompt worker 还没 ready');
			const result = await callByName(prompt, msg.name, msg.args); // PromptBuilder 全是 async：不 await 就把 Promise 往 postMessage 里塞（不能克隆）
			scope.postMessage({ type: 'reply', id: msg.id, result });
		} catch (e) {
			const message = e instanceof Error ? e.message : String(e);
			scope.postMessage({ type: 'error', id: msg.type === 'call' ? msg.id : 0, message: message.slice(0, 400) });
		}
	});
};

/** 名字 → `PromptBuilder` 的方法（Worker 只过数据，不过函数）。 */
function callByName(p: PromptBuilder, name: string, a: readonly unknown[]): unknown {
	switch (name) {
		case 'build_request':
			return p.final(String(a[0]), a[1] as string[], a[2] as never);
		case 'build_draft_request':
			return p.draft(String(a[0]), a[1] as string[], a[2] as never);
		case 'draft_begin':
			return p.draftBegin(Number(a[0]), String(a[1]));
		case 'draft_accept':
			return p.draftAccept(Number(a[0]), Number(a[1]), Number(a[2]));
		case 'draft_final':
			return p.draftFinal(Number(a[0]));
		default:
			throw new Error(`prompt worker 不认 ${name}`);
	}
}
