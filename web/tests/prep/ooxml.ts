// 测试用的**合成** OOXML（PPTX / DOCX）—— 现写字节，不用真课件、不进仓库。
//
// 对外：`pptxBytes(slides, notes?)` / `docxBytes(blocks)` / `zipBytes(entries)` / `readZip` / `pdfTextStub`。
// 为什么自己写 zip：Node 没有现成的 pptx 生成器，而这几份测试要的只是「合法的 zip + 几个 XML 成员」，
// 用 `node:zlib` 手写一遍 65 行，比拖一个依赖进来干净（也只给测试用，不进运行时）。
// 与真实文件唯一的差别是缺 `[Content_Types].xml` 之类 —— 桌面 `extract.py` 只按成员名找 XML，不看这些。
//
// ⚠️ 这里的数据全是编的（`Opportunity cost` 这类教研词），不含任何真实课件内容。
import zlib from 'node:zlib';

const enc = new TextEncoder();

/** 一条 zip 记录（store 或 deflate 都行；`crc` 与 `size` 必须对，否则 Python 的 zipfile 会拒）。 */
interface Entry {
	name: string;
	data: Uint8Array;
}

const CRC_TABLE = (() => {
	const t = new Uint32Array(256);
	for (let i = 0; i < 256; i++) {
		let c = i;
		for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
		t[i] = c >>> 0;
	}
	return t;
})();

function crc32(data: Uint8Array): number {
	let c = 0xffffffff;
	for (const b of data) c = CRC_TABLE[(c ^ b) & 0xff] ^ (c >>> 8);
	return (c ^ 0xffffffff) >>> 0;
}

/** 造一个 zip（单次写入、deflate 压缩；够 `zipfile` / `JSZip` 两边读）。 */
export function zipBytes(entries: readonly Entry[]): Uint8Array {
	const locals: Uint8Array[] = [];
	const centrals: Uint8Array[] = [];
	let offset = 0;
	for (const e of entries) {
		const name = enc.encode(e.name);
		const comp = new Uint8Array(zlib.deflateRawSync(e.data));
		const crc = crc32(e.data);
		const local = new DataView(new ArrayBuffer(30));
		local.setUint32(0, 0x04034b50, true);
		local.setUint16(4, 20, true); // version needed
		local.setUint16(6, 0, true);
		local.setUint16(8, 8, true); // deflate
		local.setUint32(14, crc, true);
		local.setUint32(18, comp.length, true);
		local.setUint32(22, e.data.length, true);
		local.setUint16(26, name.length, true);
		locals.push(new Uint8Array(local.buffer), name, comp);

		const central = new DataView(new ArrayBuffer(46));
		central.setUint32(0, 0x02014b50, true);
		central.setUint16(4, 20, true);
		central.setUint16(6, 20, true);
		central.setUint16(8, 0, true);
		central.setUint16(10, 8, true);
		central.setUint32(16, crc, true);
		central.setUint32(20, comp.length, true);
		central.setUint32(24, e.data.length, true);
		central.setUint16(28, name.length, true);
		central.setUint32(42, offset, true);
		centrals.push(new Uint8Array(central.buffer), name);

		offset += 30 + name.length + comp.length;
	}
	const centralBytes = centrals.reduce((n, b) => n + b.length, 0);
	const end = new DataView(new ArrayBuffer(22));
	end.setUint32(0, 0x06054b50, true);
	end.setUint16(8, entries.length, true);
	end.setUint16(10, entries.length, true);
	end.setUint32(12, centralBytes, true);
	end.setUint32(16, offset, true);
	const all = [...locals, ...centrals, new Uint8Array(end.buffer)];
	const total = all.reduce((n, b) => n + b.length, 0);
	const out = new Uint8Array(total);
	let at = 0;
	for (const b of all) {
		out.set(b, at);
		at += b.length;
	}
	return out;
}

const esc = (s: string): string =>
	s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

/** 一张 slide 的形状：`{ kind, texts }`；`kind` = `title` / `subtitle` / `body` / `textbox` / `table`。 */
export interface SlideShape {
	kind: 'title' | 'subtitle' | 'body' | 'textbox' | 'table';
	texts: string[];
}

const PH_TYPE: Record<string, string> = { title: 'title', subtitle: 'subTitle', body: 'body' };

function paragraphs(texts: readonly string[]): string {
	return texts.map((t) => `<a:p><a:r><a:t>${esc(t)}</a:t></a:r></a:p>`).join('');
}

function shapeXml(shape: SlideShape): string {
	if (shape.kind === 'table') {
		// 表格住在 `<p:graphicFrame>`（桌面模块头第 1 个坑）—— 每行 `<a:tr>`，行内单元格 `<a:tc>`。
		const rows = shape.texts
			.map((row) => {
				const cells = row
					.split('|')
					.map((c) => `<a:tc><a:txBody>${paragraphs([c.trim()])}</a:txBody></a:tc>`)
					.join('');
				return `<a:tr>${cells}</a:tr>`;
			})
			.join('');
		return (
			'<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="9" name="t"/></p:nvGraphicFramePr>' +
			`<a:graphic><a:graphicData><a:tbl>${rows}</a:tbl></a:graphicData></a:graphic></p:graphicFrame>`
		);
	}
	const ph = shape.kind === 'textbox' ? '' : `<p:nvSpPr><p:nvPr><p:ph type="${PH_TYPE[shape.kind]}"/></p:nvPr></p:nvSpPr>`;
	return `<p:sp><p:nvSpPr><p:cNvPr id="2" name="s"/>${ph}</p:nvSpPr><p:txBody>${paragraphs(shape.texts)}</p:txBody></p:sp>`;
}

function slideXml(shapes: readonly SlideShape[]): string {
	return (
		'<?xml version="1.0" encoding="UTF-8"?>' +
		'<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" ' +
		'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">' +
		`<p:cSld><p:spTree>${shapes.map(shapeXml).join('')}</p:spTree></p:cSld></p:sld>`
	);
}

function notesXml(texts: readonly string[]): string {
	return (
		'<?xml version="1.0" encoding="UTF-8"?>' +
		'<p:notes xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" ' +
		'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">' +
		`<p:cSld><p:spTree><p:sp><p:txBody>${paragraphs(texts)}</p:txBody></p:sp></p:spTree></p:cSld></p:notes>`
	);
}

/** 合成 PPTX：`slides[i]` 是第 i+1 张（页号 1 起）；`notes[i]` 是第 i+1 张的备注。 */
export function pptxBytes(
	slides: readonly (readonly SlideShape[])[],
	notes: readonly (readonly string[])[] = []
): Uint8Array {
	// ⚠️ 成员顺序刻意打乱（真实文件里 slide10 会排在 slide2 前面）：页码必须靠文件名排序，不靠 zip 顺序。
	const entries: Entry[] = [];
	const order = slides.map((_, i) => i + 1);
	for (const n of [...order].reverse()) {
		entries.push({ name: `ppt/slides/slide${n}.xml`, data: enc.encode(slideXml(slides[n - 1])) });
	}
	notes.forEach((texts, i) => {
		entries.push({ name: `ppt/notesSlides/notesSlide${i + 1}.xml`, data: enc.encode(notesXml(texts)) });
	});
	entries.push({ name: 'ppt/presentation.xml', data: enc.encode('<?xml version="1.0"?><p:presentation/>') });
	return zipBytes(entries);
}

/** DOCX 的一段：段落（`text`）或表格（`row`，单元格用 `|` 分隔）。 */
export type DocxBlock = { p: string } | { row: string };

function wParas(texts: readonly string[]): string {
	return texts.map((t) => `<w:p><w:r><w:t>${esc(t)}</w:t></w:r></w:p>`).join('');
}

/** 合成 DOCX（`word/document.xml` 一份就够 —— 桌面那条路只看这一份）。 */
export function docxBytes(blocks: readonly DocxBlock[]): Uint8Array {
	const body = blocks
		.map((b) => {
			if ('p' in b) return `<w:p><w:r><w:t>${esc(b.p)}</w:t></w:r></w:p>`;
			const cells = b.row.split('|').map((c) => `<w:tc>${wParas([c.trim()])}</w:tc>`).join('');
			return `<w:tbl><w:tr>${cells}</w:tr></w:tbl>`;
		})
		.join('');
	const xml =
		'<?xml version="1.0" encoding="UTF-8"?>' +
		'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">' +
		`<w:body>${body}</w:body></w:document>`;
	return zipBytes([{ name: 'word/document.xml', data: enc.encode(xml) }]);
}

/** 一个 pdf.js `getTextContent()` 的替身：按行给 item（`pagesToBlocks` 的输入形状）。 */
export function pdfTextStub(pages: readonly (readonly string[])[]): { numPages: number; pages: string[][] } {
	return { numPages: pages.length, pages: pages.map((p) => [...p]) };
}
