// `lib/prep/py.ts` —— 「开课前的准备」的 Python 侧接缝：`static/core-py/` 里那份切片跑在 Pyodide 里。
//
// 对外：`PrepPython`（`pptxFile` / `docxFile` / `prompt` / `chunks` / `user` / `score` / `dispose`）、
//       `pyPrep(py)`（从一份已 import 过 `prep_rt` 的 Pyodide 造它）、`stagePrep(deps)`（stage + import）。
// 为什么单独一层：捆块、拼 user 段、复核与排序都是桌面的**同一份** `extract.py` / `prep.py`
// （`web/scripts/sync-core.py` 按名字切出来，`--check` 钉着），TS 这边只过数据、不重写判据。
// 为什么文件要落虚拟 FS：抽块函数吃路径（桌面就这么调的），改签名就等于切片不再是桌面那份。
import type { PyodideLike } from '../core/pyodide-runtime';
import { CORE_PY_ROOT } from '../core/pyodide-runtime';

/** 一个块：与 `extract.Block`（NamedTuple）同形。`kind` 的取值见 `extract.BLOCK_KINDS`。 */
export type PrepBlock = [text: string, kind: string, page: number];

/** `extract.ExtractStats` 的镜像；`null` = 抽取根本没走到那一步。 */
export interface PrepStats {
	pages: number;
	shapes_seen: number;
	shapes_skipped: number;
	skipped_by_reason: Record<string, number>;
	ocr_pages: number;
	by_kind: Record<string, number>;
}

/** `extract()` 的 `status` 口径（`ok` / `empty` / `unreadable`），只少了 `unsupported`（这里不收不支持的格式）。 */
export interface PrepExtract {
	status: 'ok' | 'empty' | 'unreadable';
	error: string;
	blocks: PrepBlock[];
	stats: PrepStats | null;
}

/** 分块 = `prep._chunks` 的一块：`[[文件名, 块…], …]`（一份文件超限时会在多个块里出现多次）。 */
export type PrepGroup = [name: string, blocks: PrepBlock[]][];

/** `prep._rank` 的一行。`rank` 从 1 起，同分按 `term` 字典序。 */
export interface PrepRow {
	term: string;
	confidence: number;
	why: string;
	verified: boolean;
	count: number;
	spread: number;
	spread_ratio: number;
	title_hits: number;
	score: number;
	rank: number;
}

/** 交给 `pptxFile` / `docxFile` 的东西：只有字节（文件名只用来给虚拟 FS 定个后缀）。 */
export interface PrepBytes {
	name: string;
	data: Uint8Array;
}

/** 虚拟 FS 里课件文件的单调序号（`extract()` 用它区分同名文件）。 */
let next = 1;

export interface PrepPython {
	/** PPTX 字节 → 块（`extract._pptx_blocks`） */
	pptxFile(file: PrepBytes): PrepExtract;
	/** DOCX 字节 → 块（`extract._docx_blocks`） */
	docxFile(file: PrepBytes): PrepExtract;
	/** 候选词抽取的 system 提示词（`prep._prompt`，不是 TS 里的字符串） */
	prompt(): string;
	chunks(perFile: [name: string, blocks: PrepBlock[]][]): PrepGroup[];
	/** 一次调用的 user 段（`prep._extract_user`，含重复行清单） */
	user(course: string, group: PrepGroup): string;
	/** 复核 + 排序（`prep._verify_folded` / `_term_stats` / `_rank`） */
	score(blocks: PrepBlock[], merged: { term: string; confidence: number; why: string }[]): PrepRow[];
}

/** `prep_rt.py` 导出的名字 —— 少一个就当场报（比在流水线里 undefined 崩掉好查）。 */
const ENTRY = ['pptx', 'docx', 'prompt', 'chunks', 'user', 'score'] as const;

type PyFn = (...args: unknown[]) => unknown;

function parse<T>(raw: unknown): T {
	if (typeof raw !== 'string') throw new Error('prep_rt.py 的入口没有回字符串');
	return JSON.parse(raw) as T;
}

/** 虚拟 FS 里放课件字节的目录（一次性、不进用户视野）。 */
const TMP = '/tmp/cl-prep';

/** 从一份已经 `import prep_rt` 的 Pyodide 造接缝。名字缺一个就抛（不等到流水线中途）。 */
export function pyPrep(py: PyodideLike): PrepPython {
	const fns = new Map<string, PyFn>();
	for (const n of ENTRY) {
		const f = py.globals.get(n);
		if (typeof f !== 'function') throw new Error(`core-py 没导出 prep_rt.${n}`);
		fns.set(n, f as PyFn);
	}
	const call = (name: string, args: readonly unknown[]): unknown => {
		const f = fns.get(name);
		if (!f) throw new Error(`core-py 没导出 prep_rt.${name}`);
		return f(...args);
	};

	const extract = (kind: 'pptx' | 'docx', file: PrepBytes): PrepExtract => {
		const suffix = kind === 'pptx' ? '.pptx' : '.docx';
		// 文件名只用一次：同名会互相覆盖，所以带一个单调序号（Pyodide 是单线程，无并发问题）。
		const path = `${TMP}/${kind}-${next++}${suffix}`;
		py.FS.mkdirTree(TMP);
		py.FS.writeFile(path, file.data);
		return parse<PrepExtract>(call(kind, [path]));
	};

	return {
		pptxFile: (file) => extract('pptx', file),
		docxFile: (file) => extract('docx', file),
		prompt: () => String(call('prompt', [])),
		chunks: (perFile) => parse<PrepGroup[]>(call('chunks', [JSON.stringify(perFile)])),
		user: (course, group) => String(call('user', [course, JSON.stringify(group)])),
		score: (blocks, merged) =>
			parse<PrepRow[]>(call('score', [JSON.stringify(blocks), JSON.stringify(merged)]))
	};
}

/** `stagePrep` 要什么：与 `core.loadCore` 同一套注入（浏览器 CDN + fetch，Node 测试 fs）。 */
export interface StageDeps {
	load(): Promise<PyodideLike>;
	read(rel: string): Promise<Uint8Array>;
	manifest: string[];
}

/**
 * 把 `manifest.json` 列的 .py 写进虚拟 FS → 加 sys.path → `import prep_rt` → 取那六个函数。
 *
 * 装载面与 `core.loadCore` 同一套注入（`load` / `read` / `manifest`）：浏览器给 CDN + fetch，
 * Node 测试给 `node_modules` + `fs`。要不要另开一份 Pyodide 由调用方定（这套不再 boot 第二个）。
 */
export async function stagePrep(deps: StageDeps): Promise<PrepPython> {
	const py = await deps.load();
	const staged = await Promise.all(deps.manifest.map((rel) => deps.read(rel)));
	for (let i = 0; i < deps.manifest.length; i++) {
		const dst = `${CORE_PY_ROOT}/${deps.manifest[i]}`;
		py.FS.mkdirTree(dst.slice(0, dst.lastIndexOf('/')));
		py.FS.writeFile(dst, staged[i]);
	}
	py.runPython(
		`import sys\nif ${JSON.stringify(CORE_PY_ROOT)} not in sys.path: sys.path.insert(0, ${JSON.stringify(CORE_PY_ROOT)})`
	);
	py.runPython('from prep_rt import pptx, docx, prompt, chunks, user, score');
	return pyPrep(py);
}
