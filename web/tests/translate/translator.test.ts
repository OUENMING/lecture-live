// Translator 的行为判据（不连网）：并发上限、失败重试一次后放弃、草稿只译最新、旧的作废。
//
// 请求体用真的 `rt.py`（Node 的 pyodide），所以「拼装 + 解析 + 重试」是同一份代码在跑；
// 网络那一层换成 `fakeFetch`，SSE 还按任意位置切开 —— 三件事都在这一层验，不靠真上游。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { test } from 'node:test';
import { loadPyodide } from 'pyodide';
import type { PyodideLike } from '../../src/lib/core/pyodide-runtime.ts';
import { pyPromptFromPyodide, type PromptBuilder } from '../../src/lib/translate/prompt.ts';
import { Translator, type ChatFn } from '../../src/lib/translate/translator.ts';
import { streamChat } from '../../src/lib/translate/stream.ts';
import { fakeFetch, sseText, type Recorded, type Scripted } from './fake-fetch.ts';

const CORE_PY = path.resolve(import.meta.dirname, '../../static/core-py');
const PYODIDE_DIR = path.dirname(new URL(import.meta.resolve('pyodide/package.json')).pathname);

let booted: Promise<PromptBuilder> | null = null;

function prompt(): Promise<PromptBuilder> {
	booted ??= (async () => {
		const { files } = JSON.parse(fs.readFileSync(path.join(CORE_PY, 'manifest.json'), 'utf8')) as {
			files: string[];
		};
		const py = (await loadPyodide({
			indexURL: `${PYODIDE_DIR}/`,
			stdout: () => {}
		})) as unknown as PyodideLike;
		for (const rel of files) {
			const dst = `/core-py/${rel}`;
			py.FS.mkdirTree(dst.slice(0, dst.lastIndexOf('/')));
			py.FS.writeFile(dst, new Uint8Array(await fs.promises.readFile(path.join(CORE_PY, rel))));
		}
		py.runPython(
			'import sys\n' +
				'if "/core-py" not in sys.path: sys.path.insert(0, "/core-py")\n' +
				'import rt\n' +
				'build_request = rt.build_request\n' +
				'build_draft_request = rt.build_draft_request\n' +
				'draft_begin = rt.draft_begin\n' +
				'draft_accept = rt.draft_accept\n' +
				'draft_final = rt.draft_final\n'
		);
		return pyPromptFromPyodide(py);
	})();
	return booted;
}

const ZH = '期中考试覆盖第一到三章。';
const EN = 'The midterm covers chapters one to three.';

function finalSse(zh = ZH, en = EN): string {
	return sseText(['ZH: ', zh, '\n', `EN: ${en}`]);
}

/** 造一个 Translator，`chat` 走真的 `streamChat` + 假 fetch。 */
function make(
	p: PromptBuilder,
	script: Scripted[]
): {
	tr: Translator;
	calls: Recorded[];
	events: { seg: number; zh: string; en: string; done: boolean; failed: boolean }[];
	drafts: string[];
} {
	const ff = fakeFetch(script);
	const events: { seg: number; zh: string; en: string; done: boolean; failed: boolean }[] = [];
	const drafts: string[] = [];
	const chat: ChatFn = (opts) => streamChat({ ...opts, fetch: ff.fetch });
	const tr = new Translator({
		prompt: p,
		// 与桌面一致：翻译不带调用类（`llm_client.headers(key)` 不传 call_class）
		endpoint: () => ({ url: 'https://example.test/v1/chat/completions', headers: {} }),
		chat,
		onFinal: (ev) => events.push(ev),
		onDraft: (ev) => drafts.push(ev.zh)
	});
	return { tr, calls: ff.calls, events, drafts };
}

test('定稿句：请求体、请求头、增量上屏、最终两行都对', async () => {
	const p = await prompt();
	const { tr, calls, events } = make(p, [{ sse: finalSse(), cuts: [7, 40, 120] }]);
	await tr.final(1, 'the midterm covers chapters one to three.', 1.5);

	assert.equal(calls.length, 1);
	const rec = calls[0];
	assert.equal(rec.url, 'https://example.test/v1/chat/completions');
	assert.equal(rec.headers['X-ClassLive-Call'], undefined, '翻译不带调用类（与桌面 llm_client.headers(key) 同）');
	assert.equal(rec.body.stream, true);
	assert.equal(rec.body.model, 'deepseek-flash');
	assert.equal(rec.body.max_tokens, 220, '短句仍是 220');
	assert.deepEqual(rec.body.thinking, { type: 'disabled' });
	const want = await p.final('the midterm covers chapters one to three.', [], undefined);
	assert.equal(rec.body.messages[0].content, want.system);
	assert.equal(rec.body.messages[1].content, want.user);

	// 中间态：中文是累积的，不许回退成更短的串
	let prev = '';
	for (const e of events) {
		assert.ok(e.zh.startsWith(prev), `中文中间态退化了：${JSON.stringify([prev, e.zh])}`);
		prev = e.zh;
	}
	const last = events[events.length - 1];
	assert.equal(last.done, true);
	assert.equal(last.failed, false);
	assert.equal(last.zh, ZH);
	assert.equal(last.en, EN);
	// 前文窗口：下一句带上这句的修正英文
	assert.deepEqual([...tr.context], [EN]);
});

test('失败一次重试、两次都失败就标 failed，后面照常翻', async () => {
	const p = await prompt();
	const { tr, calls, events } = make(p, [
		{ status: 500 },
		{ status: 500 },
		{ sse: finalSse('第二句译文。', 'The second sentence.') }
	]);
	await tr.final(1, 'the midterm covers chapters one to three.');
	const failed = events.filter((e) => e.done && e.failed);
	assert.equal(failed.length, 1, '两次失败后必须落一条 failed');
	assert.equal(failed[0].zh, '', '失败那句没有中文（界面显示英文）');
	assert.equal(failed[0].en, 'the midterm covers chapters one to three.');
	assert.equal(calls.length, 2, '只许重试一次（共两次请求）');

	await tr.final(2, 'the second sentence.');
	const ok = events.filter((e) => e.done && !e.failed);
	assert.equal(ok.length, 1);
	assert.equal(ok[0].zh, '第二句译文。', '失败之后下一句照常翻');
});

test('并发上限：定稿串行（同时在途最多 1 个）', async () => {
	const p = await prompt();
	const ff = fakeFetch([{ sse: finalSse() }]);
	let inFlight = 0;
	let peak = 0;
	const chat: ChatFn = async (opts) => {
		inFlight += 1;
		peak = Math.max(peak, inFlight);
		try {
			return await streamChat({ ...opts, fetch: ff.fetch });
		} finally {
			inFlight -= 1;
		}
	};
	const tr = new Translator({
		prompt: p,
		endpoint: () => ({ url: 'https://example.test/v1/chat/completions', headers: {} }),
		chat,
		onFinal: () => {},
		onDraft: () => {}
	});
	await Promise.all([tr.final(1, 'one.'), tr.final(2, 'two.'), tr.final(3, 'three.')]);
	assert.equal(peak, 1, `定稿请求不许并发（实测峰值 ${peak}）`);
	assert.equal(ff.calls.length, 3);
});

test('草稿：只译最新一条，在途的旧请求被取消（旧的译文不上屏）', async () => {
	const p = await prompt();
	const ff = fakeFetch([
		{ hang: true }, // 第一份草稿：挂住，等 abort
		{ sse: sseText(['第二份草稿的译文']) }
	]);
	let aborted = 0;
	const chat: ChatFn = (opts) =>
		streamChat({ ...opts, fetch: ff.fetch }).catch((e: unknown) => {
			if ((e as { aborted?: boolean })?.aborted) aborted += 1;
			throw e;
		});
	const drafts: string[] = [];
	const tr = new Translator({
		prompt: p,
		endpoint: () => ({ url: 'https://example.test/v1/chat/completions', headers: {} }),
		chat,
		onFinal: () => {},
		onDraft: (ev) => drafts.push(ev.zh)
	});
	tr.draft(1, 'The midterm covers');
	await new Promise<void>((r) => setTimeout(r, 20));
	tr.draft(1, 'The midterm covers chapters one to three.');
	await new Promise<void>((r) => setTimeout(r, 200));

	assert.equal(aborted, 1, '旧草稿请求必须被 abort（不是等它跑完再丢）');
	assert.deepEqual(drafts.filter((d) => d !== ''), ['第二份草稿的译文'], '只有最新的那份上屏');
});

test('草稿：这段定稿后，在途的草稿译文不许再贴回来', async () => {
	const p = await prompt();
	const ff = fakeFetch([{ sse: sseText(['晚到的草稿译文']) }]);
	const drafts: string[] = [];
	const tr = new Translator({
		prompt: p,
		endpoint: () => ({ url: 'https://example.test/v1/chat/completions', headers: {} }),
		chat: (opts) => streamChat({ ...opts, fetch: ff.fetch }),
		onFinal: () => {},
		onDraft: (ev) => drafts.push(ev.zh)
	});
	tr.draft(1, 'The midterm covers chapters');
	await tr.noteFinal(1); // 这段定稿了：`DraftView.on_final` 让那份送翻过期
	await new Promise<void>((r) => setTimeout(r, 120));
	assert.deepEqual(drafts.filter((d) => d !== ''), [], '定稿之后草稿译文不许上屏');
});

// 11-20 排障打点：每次请求一条 trace（结局 / HTTP 状态 / 耗时），且 onTrace 抛错不影响翻译
test('onTrace：失败带状态码、重试带 attempt、成功带首字耗时；回调抛错照常翻', async () => {
	const p = await prompt();
	const ff = fakeFetch([{ status: 503 }, { sse: finalSse() }]);
	const traces: import('../../src/lib/translate/translator.ts').TraceEvent[] = [];
	const events: { done: boolean; failed: boolean; zh: string }[] = [];
	const tr = new Translator({
		prompt: p,
		endpoint: () => ({ url: 'https://example.test/v1/chat/completions', headers: {} }),
		chat: (opts) => streamChat({ ...opts, fetch: ff.fetch }),
		onFinal: (ev) => events.push(ev),
		onDraft: () => {},
		onTrace: (ev) => {
			traces.push(ev);
			throw new Error('打点坏了');
		}
	});
	await tr.final(1, 'the midterm covers chapters one to three.');
	assert.equal(traces.length, 2);
	assert.equal(traces[0].outcome, 'error');
	assert.equal(traces[0].status, 503);
	assert.equal(traces[0].attempt, 1);
	assert.equal(typeof traces[0].wait_ms, 'number');
	assert.equal(traces[1].outcome, 'ok');
	assert.equal(traces[1].attempt, 2);
	assert.equal(typeof traces[1].first_ms, 'number');
	const last = events[events.length - 1];
	assert.equal(last.done && !last.failed && last.zh, ZH, 'onTrace 抛错不许拦住译文');
});

test('OCR B：draftAccept 往返期间换成了更新的草稿 → 旧的不上屏、新的不被抹掉', async () => {
	const p = await prompt();
	const ff = fakeFetch([{ sse: sseText(['旧草稿译文']) }, { sse: sseText(['新草稿译文']) }]);
	let release!: () => void;
	const gate = new Promise<void>((r) => (release = r));
	let first = true;
	const wrapped = new Proxy(p, {
		get(t, k, r) {
			if (k === 'draftAccept')
				return async (...a: Parameters<PromptBuilder['draftAccept']>) => {
					const ok = await t.draftAccept(...a);
					if (first) {
						first = false;
						await gate; // 模拟：Worker 判完「接受」、回包还在路上
						return true;
					}
					return ok;
				};
			const v = Reflect.get(t, k, r);
			return typeof v === 'function' ? v.bind(t) : v;
		}
	}) as PromptBuilder;
	const drafts: string[] = [];
	const tr = new Translator({
		prompt: wrapped,
		endpoint: () => ({ url: 'https://example.test/v1/chat/completions', headers: {} }),
		chat: (opts) => streamChat({ ...opts, fetch: ff.fetch }),
		onFinal: () => {},
		onDraft: (ev) => drafts.push(ev.zh)
	});
	tr.draft(90, 'The midterm covers');
	await new Promise<void>((r) => setTimeout(r, 60)); // 旧的已卡在 accept
	tr.draft(90, 'The midterm covers chapters one to three.'); // 段号用 90：rt 的 DraftView 跨用例共享，前面的用例已定稿 1
	await new Promise<void>((r) => setTimeout(r, 20));
	release();
	await new Promise<void>((r) => setTimeout(r, 200));
	assert.deepEqual(drafts.filter((d) => d !== '').slice(-1), ['新草稿译文'], '最后上屏的必须是新草稿');
	assert.ok(!drafts.includes('旧草稿译文'), '旧草稿不该上屏');
});

test('定稿 max_tokens 按原文长度加，封顶 400', async () => {
	const { finalMaxTokens } = await import('../../src/lib/translate/stream.ts');
	assert.equal(finalMaxTokens('short sentence.'), 220);
	assert.equal(finalMaxTokens('x'.repeat(488)), 366);   // 2026-11-20 那句长句：220 不够
	assert.equal(finalMaxTokens('x'.repeat(2000)), 400);  // Worker 网页封顶 400
	const p = await prompt();
	const longEn = 'word '.repeat(100).trim() + '.';
	const { tr, calls } = make(p, [{ sse: finalSse(), cuts: [] }]);
	await tr.final(1, longEn, 1.5);
	assert.equal(calls[0].body.max_tokens, finalMaxTokens(longEn));
	assert.ok(calls[0].body.max_tokens > 220);
});
