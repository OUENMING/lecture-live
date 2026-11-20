// `lib/prep/pdf.ts` —— PDF 抽文字（pdf.js，固定版本号动态 import，不进主包）。
//
// 对外：`pdfBlocks(data)` → `PrepExtract`（与 `extract.Block` 同形的块）；`pagesToBlocks(pages)` 是
//       纯函数（测试直接打它，不必在 Node 里拉 CDN）；`PDFJS_VERSION` / `PDFJS_CDN` / `PDFJS_VENDOR`
//       钉版本，`pdfModuleUrl()` / `pdfWorkerUrl()` 决定从哪儿拉（同源优先，CDN 兜底）。
// 为什么不用 Pyodide 里的 Python：桌面那条 PDF 路是 PDFKit + Vision（macOS 专有 API），浏览器没有；
// pdf.js 是浏览器里唯一成熟的替代。代价：**PDF 与 PPTX 不是同一条实现**，块形状相同而字节不必相同
// （PPTX/DOCX 有 CPython 逐块对照测试，PDF 没有）。
// ⚠️ 扫描件（没有文字层）不支持：桌面用 Vision OCR 兜底，网页版没有 OCR —— 明说不支持，不静默给空。
import { originBase } from '../core/pyodide-runtime';
import type { PrepBlock, PrepExtract } from './py';

/** 钉死的 pdf.js 版本。换版本要重跑 `tests/prep/pdf-blocks.test.ts`（那几条是形状判据，不是文本判据）。 */
export const PDFJS_VERSION = '4.8.69';
/** 兜底 CDN（与 Pyodide 同一个，已经为它开过一次 CSP 例外，别再加第二个域名）。 */
export const PDFJS_CDN = `https://cdn.jsdelivr.net/npm/pdfjs-dist@${PDFJS_VERSION}`;
/** 同源自托管根（`npm run vendor` 落到 `static/vendor/pdfjs/<ver>/legacy/build/`）。同源才躲得开 jsDelivr 的不稳定。 */
export const PDFJS_VENDOR = `/vendor/pdfjs/${PDFJS_VERSION}/legacy/build`;

/** 同一文件名（相对 `legacy/build/`）的两档候选：给了 `origin` 就同源在前，CDN 在后。 */
function pdfTiers(file: string): string[] {
	const origin = originBase();
	return origin
		? [`${origin}${PDFJS_VENDOR}/${file}`, `${PDFJS_CDN}/legacy/build/${file}`]
		: [`${PDFJS_CDN}/legacy/build/${file}`];
}

/** `pdf.min.mjs` 的两档地址（同源优先）；导出只为让测试钉住「同源在前」。 */
export function pdfModuleUrl(): string[] {
	return pdfTiers('pdf.min.mjs');
}

/** `pdf.worker.min.mjs` 的两档地址（跟模块同一档，不会一个同源一个 CDN）。 */
export function pdfWorkerUrl(): string[] {
	return pdfTiers('pdf.worker.min.mjs');
}

/** pdf.js 里我们用到的那点面（收窄，别让整包类型渗进来）。 */
interface PdfTextItem {
	str?: string;
}
interface PdfTextContent {
	items: PdfTextItem[];
}
interface PdfPage {
	getTextContent(): Promise<PdfTextContent>;
}
interface PdfDoc {
	numPages: number;
	getPage(n: number): Promise<PdfPage>;
	destroy(): Promise<void>;
}
interface PdfModule {
	GlobalWorkerOptions: { workerSrc: string };
	getDocument(src: { data: Uint8Array }): { promise: Promise<PdfDoc> };
}

type Importer = (url: string) => Promise<unknown>;

/**
 * 一页的文字 → 块。**纯函数**：把 `getTextContent()` 里每个 item 的 `str` 拼成一行行的文本
 * （pdf.js 的 item 是文本片段，不一定按行切），再按桌面 `_pdf_paragraphs` 的口径
 * （`splitlines` → `strip` → 丢空行）变成 `body` 块。
 *
 * ⚠️ PDF 没有版式信息，所以 `kind` 一律 `body`（桌面那条路同理，见 `extract._pdf_blocks`）。
 */
export function pagesToBlocks(pages: readonly string[]): PrepBlock[] {
	const out: PrepBlock[] = [];
	pages.forEach((text, i) => {
		for (const line of (text ?? '').split('\n')) {
			const t = line.trim();
			if (t) out.push([t, 'body', i + 1]);
		}
	});
	return out;
}

/** 一页拿到的文字（pdf.js 的 item 之间补空格；同一行内的片段本来就没有分隔符）。 */
function pageText(content: PdfTextContent): string {
	const parts: string[] = [];
	for (const it of content.items) {
		if (typeof it.str === 'string' && it.str) parts.push(it.str);
	}
	// item 之间塞换行而不是空格：pdf.js 不给「这几个 item 在同一行」的信息，
	// 用换行至少不会把两行粘成一句（`pagesToBlocks` 再逐行 strip）。
	return parts.join('\n');
}

/**
 * PDF 字节 → 块。没有文字层的（扫描件）返回 `unreadable` 并说清原因 —— 不返回空块当成功。
 *
 * `importPdf` 只为测试注入（Node 里拉不到 CDN）；不给就按 `pdfModuleUrl()` 逐档试。抽不出任何文字时不抛，
 * 走 `status`（同桌面 `extract()` 的纪律：坏文件不许让调用方写 try）。
 */
export async function pdfBlocks(
	data: Uint8Array,
	importPdf: Importer = (url) => import(/* @vite-ignore */ url)
): Promise<PrepExtract> {
	let mod: PdfModule;
	let workerUrl = pdfWorkerUrl()[0];
	try {
		const [primary, ...rest] = pdfModuleUrl();
		try {
			mod = (await importPdf(primary)) as PdfModule;
		} catch (e) {
			if (!rest.length) throw e;
			console.warn(`[classlive] pdf.js 没从同源拉到（${e instanceof Error ? e.message : String(e)}），退到 jsDelivr`);
			mod = (await importPdf(rest[0])) as PdfModule;
			workerUrl = pdfWorkerUrl()[1];
		}
	} catch (e) {
		return {
			status: 'unreadable',
			error: `拉不到 pdf.js（${e instanceof Error ? e.message : String(e)}）`,
			blocks: [],
			stats: null
		};
	}
	// worker 跟模块同一档：主线程跑 pdf.js 会卡住界面，而把 4 MB worker 打进主包不值。
	mod.GlobalWorkerOptions.workerSrc = workerUrl;

	let doc: PdfDoc;
	try {
		doc = await mod.getDocument({ data }).promise;
	} catch (e) {
		return {
			status: 'unreadable',
			error: `PDF 打不开（${e instanceof Error ? e.message : String(e)}）`,
			blocks: [],
			stats: null
		};
	}

	try {
		const pages: string[] = [];
		for (let i = 1; i <= doc.numPages; i++) {
			const page = await doc.getPage(i);
			pages.push(pageText(await page.getTextContent()));
		}
		const blocks = pagesToBlocks(pages);
		if (!blocks.length) {
			return {
				status: 'unreadable',
				error: '这 PDF 没有文字层（扫描件）—— 网页版不做 OCR，换 PPTX / DOCX 或先自己 OCR',
				blocks: [],
				stats: null
			};
		}
		return {
			status: 'ok',
			error: '',
			blocks,
			stats: {
				pages: doc.numPages,
				shapes_seen: doc.numPages,
				shapes_skipped: 0,
				skipped_by_reason: {},
				ocr_pages: 0,
				by_kind: { body: blocks.length }
			}
		};
	} finally {
		await doc.destroy().catch(() => undefined);
	}
}
