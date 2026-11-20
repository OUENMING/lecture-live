// lib/prep/browser.ts —— 浏览器里「课件 → 候选术语」的一键入口：起一份主线程 Pyodide（同源优先）+ stage core-py → prepareTerms。
// 对外：termsFromFiles(files, course, onProgress) → PrepResult。Pyodide 只在第一次点「从课件生成」时下载，之后复用。
// 为什么在主线程：只在开课前跑一次、全程是等 LLM，Python 部分是毫秒级；为它再造一个 Worker 协议不划算。
import { CORE_PY_ROOT, importPyodide, type PyodideLike } from '../core/pyodide-runtime';
import { prepareTerms, type PrepProgress, type PrepResult } from './prepare';
import { stagePrep, type PrepPython } from './py';

let pyOnce: Promise<PrepPython> | null = null;

function pyForPrep(): Promise<PrepPython> {
	pyOnce ??= (async () => {
		const { mod, indexURL } = await importPyodide();
		const res = await fetch(`${CORE_PY_ROOT}/manifest.json`);
		if (!res.ok) throw new Error(`core-py/manifest.json: HTTP ${res.status}`);
		const { files } = (await res.json()) as { files: string[] };
		return stagePrep({
			load: () => mod.loadPyodide({ indexURL, stdout: () => {} }),
			manifest: files,
			read: async (rel) => {
				const r = await fetch(`${CORE_PY_ROOT}/${rel}`);
				if (!r.ok) throw new Error(`core-py/${rel}: HTTP ${r.status}`);
				return new Uint8Array(await r.arrayBuffer());
			}
		});
	})();
	pyOnce.catch(() => (pyOnce = null)); // 失败了下次重试，不缓存坏结果
	return pyOnce;
}

export async function termsFromFiles(
	files: readonly File[],
	course: string,
	onProgress?: (p: PrepProgress) => void
): Promise<PrepResult> {
	const py = await pyForPrep();
	const data = await Promise.all(files.map(async (f) => ({ name: f.name, data: new Uint8Array(await f.arrayBuffer()) })));
	return prepareTerms(data, course, { py, onProgress });
}
