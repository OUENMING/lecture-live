// 合成 pptx / docx 的抽块判据（Pyodide 里的 `extract.py` 切片 vs 桌面 CPython `extract.py`）。
//
// 判据：**同一份合成文件，两条路的块逐条相同**（`text` / `kind` / `page` 全等）。
// 为什么值得一条测试：网页版的 `extract.py` 是 `sync-core.py` 按名字切出来的，切片机制本身
// 有 `--check` 钉着，但「切出来的那半跑起来是不是同一个结果」只有真跑一遍才知道
// （比如 `_paragraphs` 里的递归、表格用 `findall` 不用 `iter`，这些在切片里，错一个字就变）。
//
// 数据全是现造的（`ooxml.ts`），不含任何真实课件；桌面仓库不在本机 -> 整组 skip 并说明。
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { test } from 'node:test';
import { loadPyodide } from 'pyodide';
import { CORE_PY_ROOT, loadCore } from '../../src/lib/core/pyodide-runtime.ts';
import { pyPrep, stagePrep, type PrepBlock } from '../../src/lib/prep/py.ts';
import { docxBytes, pptxBytes, type SlideShape } from './ooxml.ts';

const WEB_ROOT = path.resolve(import.meta.dirname, '../..');
const CORE_PY = path.join(WEB_ROOT, 'static/core-py');
const PYODIDE_DIR = path.dirname(new URL(import.meta.resolve('pyodide/package.json')).pathname);
const DESKTOP = process.env.CL_DESKTOP ?? path.resolve(WEB_ROOT, '../../lecture-live');
const noDesktop = fs.existsSync(path.join(DESKTOP, 'extract.py'))
	? false
	: `桌面仓库不在 ${DESKTOP}（跳过 CPython 对照）`;

// ---- 合成语料（三页，覆盖 title / body / textbox / table / notes / 打乱的成员顺序）----
const SLIDES: SlideShape[][] = [
	[
		{ kind: 'title', texts: ['Opportunity cost and elasticity'] },
		{ kind: 'body', texts: ['The demand curve shifts when income changes'] },
		{ kind: 'textbox', texts: ['https://example.invalid/reading'] }
	],
	[
		{ kind: 'title', texts: ['Marginal revenue'] },
		{ kind: 'table', texts: ['Term|Meaning', 'marginal cost|the cost of one more unit'] },
		{ kind: 'body', texts: ['Firms maximise profit where marginal cost equals marginal revenue'] }
	],
	[
		{ kind: 'textbox', texts: ['Recap: elasticity matters'] },
		{ kind: 'subtitle', texts: ['Next week: welfare economics'] }
	]
];
const NOTES = [['Say this slowly'], ['Ask about the graph'], []];

const PPTX = pptxBytes(SLIDES, NOTES);
const DOCX = docxBytes([
	{ p: 'Opportunity cost is what you give up' },
	{ row: 'Term|Meaning' },
	{ row: 'elasticity|how much quantity responds' },
	{ p: 'Demand shifts when income changes' }
]);

let booted: Promise<ReturnType<typeof pyPrep>> | null = null;

/** 一份 Pyodide（Node 里走 node_modules，不拉 CDN），两个用例共用。 */
function prep() {
	booted ??= (async () => {
		const { files } = JSON.parse(fs.readFileSync(path.join(CORE_PY, 'manifest.json'), 'utf8')) as {
			files: string[];
		};
		return stagePrep({
			load: () => loadPyodide({ indexURL: `${PYODIDE_DIR}/`, stdout: () => {} }),
			read: async (rel) => new Uint8Array(await fs.promises.readFile(path.join(CORE_PY, rel))),
			manifest: files
		});
	})();
	return booted;
}

/** 桌面 `extract.extract()` 跑同一份字节 → 同样的 `[text, kind, page]` 三元组。 */
function desktopBlocks(suffix: 'pptx' | 'docx', bytes: Uint8Array): PrepBlock[] {
	const dir = fs.mkdtempSync('/tmp/cl-extract-');
	const file = path.join(dir, `a.${suffix}`);
	fs.writeFileSync(file, bytes);
	try {
		const run = spawnSync(
			'python3',
			[
				'-c',
				'import json, sys; sys.path.insert(0, sys.argv[1]); import extract;' +
					'r = extract.extract(sys.argv[2], ocr=False);' +
					'print(json.dumps({"status": r.status, "blocks": [[b.text, b.kind, b.page] for b in r.blocks]}))',
				DESKTOP,
				file
			],
			{ encoding: 'utf8' }
		);
		assert.equal(run.status, 0, `桌面 extract 跑挂了：\n${run.stderr}`);
		const got = JSON.parse(run.stdout) as { status: string; blocks: PrepBlock[] };
		assert.equal(got.status, 'ok', '桌面也抽不出这份合成文件（那这条测试没意义）');
		return got.blocks;
	} finally {
		fs.rmSync(dir, { recursive: true, force: true });
	}
}

test('合成 PPTX：Pyodide 切片的块与桌面 CPython 逐条相同（含表格 / 备注 / 打乱的成员顺序）', async () => {
	const py = await prep();
	const web = py.pptxFile({ name: 'synthetic.pptx', data: PPTX });
	assert.equal(web.status, 'ok', web.error);
	if (noDesktop) return;
	assert.deepEqual(web.blocks, desktopBlocks('pptx', PPTX));
	assert.ok(
		web.blocks.some(([, kind]) => kind === 'table'),
		'合成文件里得有表格行（不然「表格住在 graphicFrame」那条没被覆盖）'
	);
	const slidePages = web.blocks.filter(([, kind]) => kind !== 'notes').map(([, , page]) => page);
	assert.deepEqual(
		slidePages,
		[...slidePages].sort((a, b) => a - b),
		'幻灯片块的页号必须升序（成员顺序被打乱过）；备注页是整段追加的，不在这一判里'
	);
});

test('合成 DOCX：Pyodide 切片的块与桌面 CPython 逐条相同（段落与表格保序）', async () => {
	const py = await prep();
	const web = py.docxFile({ name: 'synthetic.docx', data: DOCX });
	assert.equal(web.status, 'ok', web.error);
	if (noDesktop) return;
	assert.deepEqual(web.blocks, desktopBlocks('docx', DOCX));
});

test('坏文件：不是 zip / 没有 slideN.xml → status=unreadable 且带原因，不抛', async () => {
	const py = await prep();
	const junk = py.pptxFile({ name: 'junk.pptx', data: new TextEncoder().encode('not a zip at all') });
	assert.equal(junk.status, 'unreadable');
	assert.match(junk.error, /BadZip|zip/i);
	const empty = py.pptxFile({ name: 'empty.pptx', data: pptxBytes([]) });
	assert.equal(empty.status, 'unreadable');
	assert.match(empty.error, /slide/);
	const docx = py.docxFile({ name: 'junk.docx', data: PPTX });
	assert.equal(docx.status, 'unreadable', '拿 pptx 当 docx 必须判出来');
});

test('块与提示词都不在 TS 里重写：system 提示词来自 prep._prompt()，含 kind 说明', async () => {
	const py = await prep();
	const sys = py.prompt();
	assert.match(sys, /术语候选表/, '提示词是桌面那条（不是 TS 里的副本）');
	assert.match(sys, /\(table\)|表格的\*\*一行\*\*/, 'kind 说明是 prep 从 extract.KIND_MEANING 拼的');
	assert.match(sys, /40%/, '重复行阈值是现场插的，不是手写死的');
});

test('CORE_PY_ROOT 是 Pyodide 里 import 的落点（装载面与 core 那份同一个常量）', async () => {
	const py = await prep();
	assert.ok(py, 'stagePrep 成功');
	assert.equal(CORE_PY_ROOT, '/core-py');
});

// 顺带钉住：`loadCore` 与 `stagePrep` 用的是**同一份** manifest（两处列的不一致会静默少文件）。
test('manifest 同时供得动实时核心与开课准备（两份装载面不各列一份清单）', async () => {
	const { files } = JSON.parse(fs.readFileSync(path.join(CORE_PY, 'manifest.json'), 'utf8')) as {
		files: string[];
	};
	for (const f of ['prep_rt.py', 'prep.py', 'extract.py', 'stream_core.py', 'rt.py']) {
		assert.ok(files.includes(f), `manifest 少了 ${f}`);
	}
	const core = await loadCore({
		load: () => loadPyodide({ indexURL: `${PYODIDE_DIR}/`, stdout: () => {} }),
		read: async (rel) => new Uint8Array(await fs.promises.readFile(path.join(CORE_PY, rel))),
		manifest: files
	});
	const snap = core.tick(1);
	assert.equal(typeof snap.now, 'number', '同一份 manifest 也 boot 得起实时核心');
});
