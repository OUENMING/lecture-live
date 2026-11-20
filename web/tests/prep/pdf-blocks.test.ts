// PDF 那条路的两层判据：`pagesToBlocks`（纯函数）与 `pdfBlocks`（注入假的 pdf.js 模块）。
//
// 为什么不在测试里拉真 pdf.js：CDN 拉包要联网，而这套测试「不联网、不花钱」是硬规矩。
// 于是 `pdfBlocks` 的 importer 是注入的 —— 假模块照着 pdf.js 用到的那点面（`getDocument` /
// `getPage` / `getTextContent` / `numPages` / `GlobalWorkerOptions`）写，形状对了，逻辑就测得动。
// 真 pdf.js 的行为（真的把 PDF 解成 item）这里**没测**，那是 Claude 验收时的浏览器活儿。
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { PDFJS_CDN, PDFJS_VERSION, pagesToBlocks, pdfBlocks } from '../../src/lib/prep/pdf.ts';

/** 一个假 pdf.js 模块：`pages` 每项是该页的文本片段（item）。 */
function fakePdf(pages: readonly (readonly string[])[] | 'throw') {
	const mod = {
		workerSrc: '',
		GlobalWorkerOptions: { workerSrc: '' },
		getDocument: () => ({
			promise:
				pages === 'throw'
					? Promise.reject(new Error('InvalidPDFException'))
					: Promise.resolve({
							numPages: pages.length,
							getPage: async (n: number) => ({
								getTextContent: async () => ({ items: pages[n - 1].map((str) => ({ str })) })
							}),
							destroy: async () => undefined
						})
		})
	};
	return { mod, url: '' };
}

test('pagesToBlocks：每页按行切、丢空行、kind 一律 body、page 从 1 起（桌面 _pdf_paragraphs 的口径）', () => {
	const blocks = pagesToBlocks(['First line\n  \nSecond line  ', '\nThird\n']);
	assert.deepEqual(blocks, [
		['First line', 'body', 1],
		['Second line', 'body', 1],
		['Third', 'body', 2]
	]);
});

test('pdfBlocks：item 之间按行摊开（不粘成一句），页号跟着 getPage 走', async () => {
	let url = '';
	const fake = fakePdf([
		['Opportunity', 'cost'],
		['marginal revenue']
	]);
	const got = await pdfBlocks(new Uint8Array([1]), async (u) => {
		url = u;
		return fake.mod;
	});
	assert.equal(url, `${PDFJS_CDN}/legacy/build/pdf.min.mjs`, '从钉死的版本号那个 CDN 拉');
	assert.equal(fake.mod.GlobalWorkerOptions.workerSrc, `${PDFJS_CDN}/legacy/build/pdf.worker.min.mjs`);
	assert.equal(got.status, 'ok', got.error);
	assert.deepEqual(
		got.blocks.map(([t, k, p]) => [t, k, p]),
		[
			['Opportunity', 'body', 1],
			['cost', 'body', 1],
			['marginal revenue', 'body', 2]
		]
	);
	assert.deepEqual(got.stats?.by_kind, { body: 3 });
});

test('没有文字层（扫描件）：status=unreadable 并说清不支持 OCR，不返回空块当成功', async () => {
	const fake = fakePdf([[], []]);
	const got = await pdfBlocks(new Uint8Array([1]), async () => fake.mod);
	assert.equal(got.status, 'unreadable');
	assert.deepEqual(got.blocks, []);
	assert.match(got.error, /扫描件|OCR/);
});

test('坏 PDF / 拉不到 pdf.js：都落成 unreadable，不抛', async () => {
	const broken = await pdfBlocks(new Uint8Array([1]), async () => fakePdf('throw').mod);
	assert.equal(broken.status, 'unreadable');
	assert.match(broken.error, /打不开/);

	const offline = await pdfBlocks(new Uint8Array([1]), async () => {
		throw new Error('net::ERR_INTERNET_DISCONNECTED');
	});
	assert.equal(offline.status, 'unreadable');
	assert.match(offline.error, /拉不到 pdf\.js/);
});

test('版本号是钉死的字面量（不进主包，换版本要手改）', () => {
	assert.match(PDFJS_VERSION, /^\d+\.\d+\.\d+$/);
	assert.ok(PDFJS_CDN.includes(PDFJS_VERSION));
});
