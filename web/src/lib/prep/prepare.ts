// `lib/prep/prepare.ts` —— 「开课前的准备」的装配：课件 → 候选术语 → 该课术语表（前 100 条）。
//
// 对外：`prepareTerms(files, course, opts)` → `{ terms, stats, failed }`（界面只认这个）。
// 流程与桌面 `prep.prepare()` 同一个顺序：抽块 → 按文件分组 → `_chunks` 分块 → 每块一次 LLM →
// 合并（同词取最高 confidence）→ 复核（术语得在原文里出现）→ `_rank` → 截前 100。
// 判断全在 Pyodide 里的切片（`static/core-py/extract.py`、`prep.py`），TS 只做装配与错误收纳。
// 为什么把依赖全注入：真链路给 Pyodide + 真 fetch，测试给 `static/core-py` 同步跑 + 假 fetch
// （`tests/prep/prepare.test.ts`），两条路走的是**同一段装配代码**。
import { pdfBlocks } from './pdf';
import { prepLLM, type PrepCandidate } from './llm';
import type { PrepBlock, PrepExtract, PrepGroup, PrepPython, PrepRow } from './py';

/** 一份课件从哪里来：`File`（浏览器）或裸字节（测试 / 拖拽的 dataTransfer）。 */
export interface PrepFile {
	name: string;
	data: Uint8Array;
}

/** 进度：`done` 是「第几次 LLM 调用完成了」，`total` 是分块总数。 */
export interface PrepProgress {
	done: number;
	total: number;
}

/** 一份抽不出来的课件。`reason` 直接给人看（界面上就是这一行）。 */
export interface PrepFailure {
	name: string;
	reason: string;
}

/** 这条流水线的产出。`terms` **只含复核通过的**（桌面 `prepare()` 里 `verified` 那条判据）。 */
export interface PrepResult {
	terms: string[];
	stats: PrepStatsSummary;
	failed: PrepFailure[];
}

/** 审计数字：抽到了多少、分了几块；界面的「一份课件都没抽出来」靠它判。 */
export interface PrepStatsSummary {
	/** 成功抽块的课件数 */
	files: number;
	/** 块的条数 / 总字符数（后者是 user 段规模的量级） */
	blocks: number;
	chars: number;
	/** 分块数 = LLM 调用次数 */
	chunks: number;
}

export interface PrepareOptions {
	/** Pyodide 那份接缝（`stagePrep` / `pyPrep` 造） */
	py: PrepPython;
	/** 注入的 fetch（测试用假实现；默认浏览器全局） */
	fetch?: Parameters<typeof prepLLM>[0]['fetch'];
	/** 用哪枚通行码（默认读存下的那枚） */
	code?: string;
	/** PDF 抽文字（默认 pdf.js；测试注入假实现，免得在 Node 里拉 CDN） */
	pdf?: (data: Uint8Array) => Promise<PrepExtract>;
	/** 进度回调（每完成一次 LLM 调用一次；MO 里可能不调） */
	onProgress?: (p: PrepProgress) => void;
	/** 最多留几条（默认 100 —— 桌面进表的上限是 40，网页版只给界面选，留宽一截） */
	limit?: number;
}

/** 默认上限：比桌面的 `DEFAULT_MAX_AUTO`（40）宽 —— 网页版由人勾选，不自动写盘。 */
export const PREP_TERM_LIMIT = 100;

/**
 * 主流程。**永不抛**（除了调用方传错类型）：坏文件进 `failed`，一块抽不出来进 `failed`，
 * 其余照常往下走 —— 一个 PDF 坏了不该让另外五份课件白抽。
 *
 * `files` 为空或全抽不出时返回空 `terms`（界面据此说「一份课件都没抽出来」），
 * 与「术语表本来就是空的」区分得开的是 `stats.files`。
 */
export async function prepareTerms(
	files: readonly PrepFile[],
	course: string,
	opts: PrepareOptions
): Promise<PrepResult> {
	const py = opts.py;
	const pdf = opts.pdf ?? pdfBlocks;
	const failed: PrepFailure[] = [];
	const perFile: [string, PrepBlock[]][] = [];
	let chars = 0;

	for (const file of files) {
		const got = await extractOne(py, pdf, file);
		if (got.status !== 'ok') {
			failed.push({ name: file.name, reason: got.error || `抽不出文字（${got.status}）` });
			continue;
		}
		chars += got.blocks.reduce((n, b) => n + b[0].length, 0);
		perFile.push([file.name, got.blocks]);
	}

	if (!perFile.length) {
		return {
			terms: [],
			stats: { files: 0, blocks: 0, chars: 0, chunks: 0 },
			failed
		};
	}

	const groups = py.chunks(perFile);
	const system = py.prompt();
	const merged = new Map<string, PrepCandidate>();
	let done = 0;
	opts.onProgress?.({ done: 0, total: groups.length });

	for (const group of groups) {
		let got: PrepCandidate[];
		try {
			got = await prepLLM({ system, user: py.user(course, group), fetch: opts.fetch, code: opts.code });
		} catch (e) {
			// ⚠️ 一块失败**不中止**：桌面 `prepare()` 是直接放弃整场（它的出口是写文件），
			//    网页版这里只出候选给人看，丢一块回来半份表比什么都没有好。
			failed.push({ name: groupName(group), reason: reasonOf(e) });
			done += 1;
			opts.onProgress?.({ done, total: groups.length });
			continue;
		}
		for (const c of got) {
			const key = c.term.toLowerCase();
			const old = merged.get(key);
			if (!old || c.confidence > old.confidence) merged.set(key, c);
		}
		done += 1;
		opts.onProgress?.({ done, total: groups.length });
	}

	const blocks = perFile.flatMap(([, bs]) => bs);
	if (!merged.size) {
		return {
			terms: [],
			stats: { files: perFile.length, blocks: blocks.length, chars, chunks: groups.length },
			failed
		};
	}

	const rows = py.score(blocks, [...merged.values()]);
	const terms = pickTerms(rows, opts.limit ?? PREP_TERM_LIMIT);
	return {
		terms,
		stats: { files: perFile.length, blocks: blocks.length, chars, chunks: groups.length },
		failed
	};
}

/** 按后缀挑抽块实现：`pdf` → 注入的那条，`docx` → Python，其余（含 `.pptx`）→ Python 的 pptx。 */
async function extractOne(
	py: PrepPython,
	pdf: (data: Uint8Array) => Promise<PrepExtract>,
	file: PrepFile
): Promise<PrepExtract> {
	const lower = file.name.toLowerCase();
	try {
		if (lower.endsWith('.pdf')) return await pdf(file.data);
		if (lower.endsWith('.docx')) return py.docxFile(file);
		if (lower.endsWith('.pptx')) return py.pptxFile(file);
	} catch (e) {
		// 抽块自己承诺不抛（坏 zip → `unreadable`），但 Pyodide 那边抛的是 Python 异常，
		// 过了 JS 边界会变成 Error —— 兜住它，别让一份课件带走整场。
		return { status: 'unreadable', error: reasonOf(e), blocks: [], stats: null };
	}
	return {
		status: 'unreadable',
		error: `不支持的格式：${suffixOf(file.name) || '(无扩展名)'}`,
		blocks: [],
		stats: null
	};
}

/** 复核通过的行 → 术语（顺序就是 `_rank` 的 rank 序，不在这里重排）。 */
function pickTerms(rows: readonly PrepRow[], limit: number): string[] {
	const out: string[] = [];
	for (const r of rows) {
		if (!r.verified) continue;
		out.push(r.term);
		if (out.length >= limit) break;
	}
	return out;
}

/** 一整块涉及哪些文件（失败时告诉人是哪份；超过两份就省略 —— 那不是「一份坏文件」）。 */
function groupName(group: PrepGroup): string {
	const names = [...new Set(group.map(([n]) => n))];
	return names.length <= 2 ? names.join('、') : `${names[0]} 等 ${names.length} 份`;
}

function suffixOf(name: string): string {
	const i = name.lastIndexOf('.');
	return i < 0 ? '' : name.slice(i).toLowerCase();
}

function reasonOf(e: unknown): string {
	return (e instanceof Error ? e.message : String(e)).slice(0, 160);
}
