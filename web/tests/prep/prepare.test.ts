// `prepareTerms` 的判据：抽块 → 分块 → 每块一次 LLM（假 fetch）→ 合并 → 复核 → 排序 → 前 100。
//
// 钉住什么（对应桌面 `prep.prepare()` 的顺序，判断全在 Pyodide 的切片里）：
//   1. 走通：候选里「原文里真的出现过的」留下、编的丢掉（复核）、顺序按 `_rank` 的分数
//   2. 分块：一份课件超过 `CHUNK_CHARS` 会分多次调用，进度回调 total = 块数
//   3. 一块失败（假 fetch 回 500）不带走整场 —— 进 failed，其余块照常
//   4. 坏文件（不是 zip）进 failed，不抛
//   5. 没有一份抽得出来 → terms 空、stats.files = 0（与「术语表本来就是空的」分得开）
// 不联网：`fetch` 是彻底假实现；提示词与判据都来自 `static/core-py/` 的切片。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { test } from 'node:test';
import { loadPyodide } from 'pyodide';
import { stagePrep, type PrepPython } from '../../src/lib/prep/py.ts';
import { prepareTerms, type PrepFile, type PrepProgress } from '../../src/lib/prep/prepare.ts';
import { PREP_MAX_TOKENS, PREP_TEMPERATURE } from '../../src/lib/prep/llm.ts';
import { docxBytes, pptxBytes, type SlideShape } from './ooxml.ts';

const CORE_PY = path.resolve(import.meta.dirname, '../../static/core-py');
const PYODIDE_DIR = path.dirname(new URL(import.meta.resolve('pyodide/package.json')).pathname);

let booted: Promise<PrepPython> | null = null;
function prep(): Promise<PrepPython> {
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

const enc = new TextEncoder();

/** 一份 3 页 pptx，标题档里有真正的领域词（复核会放行），正文里有一个「编出来的」词。 */
const SLIDES: SlideShape[][] = [
	[
		{ kind: 'title', texts: ['Opportunity cost and elasticity'] },
		{ kind: 'body', texts: ['Scarcity forces every choice to have a cost'] }
	],
	[
		{ kind: 'title', texts: ['Marginal revenue'] },
		{ kind: 'body', texts: ['Profit is maximised where marginal cost equals marginal revenue'] }
	],
	[{ kind: 'body', texts: ['Recap: opportunity cost again'] }]
];

const PPTX = pptxBytes(SLIDES);
const GOOD: PrepFile = { name: 'lecture.pptx', data: PPTX };

/** 一次假对话：`replies` 里第 n 项是第 n 次调用的输出（`null` = 这次回 500）。 */
function fakeFetch(replies: (string[] | null)[], log: unknown[] = []): typeof fetch {
	let i = 0;
	return (async (_url: string, init: RequestInit) => {
		const body = JSON.parse(String(init.body)) as Record<string, unknown>;
		log.push(body);
		const reply = replies[Math.min(i++, replies.length - 1)];
		if (reply === null) {
			return new Response(JSON.stringify({ error: 'upstream down' }), {
				status: 500,
				headers: { 'content-type': 'application/json' }
			});
		}
		const content = JSON.stringify({
			candidates: reply.map((term, k) => ({ term, confidence: 0.9 - k * 0.1, why: 'w' }))
		});
		return new Response(JSON.stringify({ choices: [{ message: { content } }] }), {
			status: 200,
			headers: { 'content-type': 'application/json' }
		});
	}) as unknown as typeof fetch;
}

test('走通：原文出现过的留下、编的丢掉，请求体是桌面的形状（非流式 + json_object + 4000 / 0.2）', async () => {
	const py = await prep();
	const log: Record<string, unknown>[] = [];
	const progress: PrepProgress[] = [];
	const res = await prepareTerms([GOOD], 'ECON10770 Microeconomics', {
		py,
		fetch: fakeFetch(
			[
				[
					'opportunity cost', // 原文有
					'elasticity', // 原文有
					'marginal revenue', // 原文有
					'monopsony power', // 原文没有 -> 复核必须丢掉
					'' // 空 term -> 归一化时丢
				]
			],
			log
		),
		onProgress: (p) => progress.push(p)
	});

	assert.deepEqual([...res.terms].sort(), ['elasticity', 'marginal revenue', 'opportunity cost']);
	assert.deepEqual(res.failed, []);
	assert.equal(res.stats.files, 1);
	assert.ok(res.stats.blocks > 0);
	assert.equal(res.stats.chunks, 1);
	assert.deepEqual(progress, [
		{ done: 0, total: 1 },
		{ done: 1, total: 1 }
	]);

	const body = log[0];
	assert.equal(body.stream, false, '抽候选词是非流式（桌面 _chat_json 同口径）');
	assert.equal(body.max_tokens, PREP_MAX_TOKENS);
	assert.equal(body.temperature, PREP_TEMPERATURE);
	assert.deepEqual(body.response_format, { type: 'json_object' });
	const msgs = body.messages as { role: string; content: string }[];
	assert.deepEqual(
		msgs.map((m) => m.role),
		['system', 'user'],
		'Worker 的提示词闸只放行 [system, user]'
	);
	assert.match(msgs[0].content, /术语候选表/, 'system 是 prep._prompt()');
	assert.match(msgs[1].content, /课程：ECON10770 Microeconomics/, 'user 段是 prep._extract_user()');
	assert.match(msgs[1].content, /\(title\)/, '块带 kind 标签（桌面的行格式）');
});

test('一块失败不带走整场：进 failed，其余块照常出词', async () => {
	const py = await prep();
	// 造一份超长课件（> CHUNK_CHARS 字符）→ 至少两块。
	const long = 'Scarcity and opportunity cost '.repeat(1600); // 约 4.6 万字
	const big = pptxBytes([
		[{ kind: 'body', texts: [long] }],
		[{ kind: 'title', texts: ['Marginal revenue'] }]
	]);
	const res = await prepareTerms([{ name: 'big.pptx', data: big }], 'ECON1', {
		py,
		fetch: fakeFetch([null, ['marginal revenue', 'opportunity cost']])
	});
	assert.ok(res.stats.chunks >= 2, `应该分了至少两块（实际 ${res.stats.chunks}）`);
	assert.equal(res.failed.length, 1, '失败的那块要报出来');
	assert.match(res.failed[0].reason, /服务器出错|upstream/i);
	assert.deepEqual([...res.terms].sort(), ['marginal revenue', 'opportunity cost'], '另一块照样出词');
});

test('坏文件进 failed 不抛；一份都抽不出来时 terms 空而 stats.files = 0', async () => {
	const py = await prep();
	const res = await prepareTerms(
		[
			{ name: 'broken.pptx', data: enc.encode('not a zip') },
			{ name: 'notes.txt', data: enc.encode('hello') }
		],
		'ECON1',
		{ py, fetch: fakeFetch([[]]) }
	);
	assert.deepEqual(res.terms, []);
	assert.equal(res.failed.length, 2);
	assert.equal(res.failed[0].name, 'broken.pptx');
	assert.match(res.failed[0].reason, /BadZip|zip/i);
	assert.equal(res.failed[1].name, 'notes.txt');
	assert.match(res.failed[1].reason, /不支持的格式/);
	assert.deepEqual(res.stats, { files: 0, blocks: 0, chars: 0, chunks: 0 });
});

test('docx 与 pptx 混着喂：块合在一起算分（表格行也进语料）', async () => {
	const py = await prep();
	const docx = docxBytes([{ p: 'Demand shifts when income changes' }, { row: 'elasticity|how much' }]);
	const res = await prepareTerms([GOOD, { name: 'sheet.docx', data: docx }], 'ECON1', {
		py,
		fetch: fakeFetch([['elasticity', 'demand shifts', 'opportunity cost']])
	});
	assert.equal(res.stats.files, 2);
	assert.deepEqual([...res.terms].sort(), ['demand shifts', 'elasticity', 'opportunity cost']);
});

test('PDF 那条走注入的抽块函数（不在 Node 里拉 CDN）：没有文字层 → failed 说清是扫描件', async () => {
	const py = await prep();
	let called = 0;
	const res = await prepareTerms(
		[{ name: 'handout.pdf', data: new Uint8Array([1, 2, 3]) }],
		'ECON1',
		{
			py,
			fetch: fakeFetch([[]]),
			pdf: async () => {
				called += 1;
				return {
					status: 'unreadable' as const,
					error: '这 PDF 没有文字层（扫描件）',
					blocks: [],
					stats: null
				};
			}
		}
	);
	assert.equal(called, 1, 'pdf.ts 的入口被调到了');
	assert.equal(res.failed[0].name, 'handout.pdf');
	assert.match(res.failed[0].reason, /扫描件/);
});
