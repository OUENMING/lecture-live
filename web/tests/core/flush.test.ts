// flush：下课那一拍把还没封口的尾句强制定稿。
//
// 判据（这一条是 M1-c 的核心）：
// - 喂到一半 flush，尾句出现在定稿里 —— 不然老师最后半句整句丢；
// - 只出现**一次** —— `StreamCore.flush()` 会清 token，但叫方（LiveSession.end）可能重复调，
//   重复出句会让同一句进两次笔记，那是不可恢复的错。
// 规则不在这里定：走的就是桌面版收尾那条 `StreamCore.flush()`（`stream_asr._finish_stream`）。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { test } from 'node:test';
import { loadPyodide } from 'pyodide';
import { loadCore, type CoreLoad } from '../../src/lib/core/pyodide-runtime.ts';

const CORE_PY = path.resolve(import.meta.dirname, '../../static/core-py');
const PYODIDE_DIR = path.dirname(new URL(import.meta.resolve('pyodide/package.json')).pathname);

let booted: Promise<CoreLoad> | null = null;

function core(): Promise<CoreLoad> {
	booted ??= (async () => {
		const { files } = JSON.parse(fs.readFileSync(path.join(CORE_PY, 'manifest.json'), 'utf8')) as {
			files: string[];
		};
		return loadCore({
			load: () => loadPyodide({ indexURL: `${PYODIDE_DIR}/`, stdout: () => {} }),
			read: async (rel) => new Uint8Array(await fs.promises.readFile(path.join(CORE_PY, rel))),
			manifest: files
		});
	})();
	return booted;
}

/** 一条 Soniox 消息：句子说到一半，没有句末标点 → P_stable 永远不会到 → 不 flush 就丢 */
function halfSentence(text: string, startMs: number, endMs: number): string {
	return JSON.stringify({
		tokens: [{ text, start_ms: startMs, end_ms: endMs, is_final: true, language: 'en' }]
	});
}

test('喂到一半 flush：尾句进定稿，且只出现一次', async () => {
	const c = await core();
	c.reset(); // 真链路模式：消息不带 _wall，时钟用 monotonic

	// 第一句说完整（有句号）→ 正常封口，进定稿
	const first = c.feed(
		JSON.stringify({
			tokens: [
				{ text: 'Hello world', start_ms: 0, end_ms: 900, is_final: true, language: 'en' },
				{ text: '.', start_ms: 900, end_ms: 1000, is_final: false, language: 'en' }
			]
		})
	);
	assert.equal(first.draft, 'Hello world.', '说完这一句先进草稿');

	// 连推几拍把 P_stable 走完 → 第一句封口
	await new Promise((r) => setTimeout(r, 350));
	const sealed = c.tick(1);
	assert.deepEqual(
		sealed.sealed.map((s) => s.text),
		['Hello world.'],
		'第一句按 P_stable 正常封口'
	);
	assert.equal(sealed.draft, '');

	// 第二句说到一半（**没有**句末标点，P_stable 永远不到）—— 桌面版这时靠收尾 flush 补
	const half = c.feed(halfSentence(' And then we', 1000, 1800));
	assert.equal(half.draft, ' And then we', '半句挂在草稿上');
	assert.deepEqual(half.sealed, [], '没标点就不该封');

	// 下课：flush
	const f = c.flush();
	assert.deepEqual(
		f.sealed.map((s) => s.text),
		[' And then we'],
		'flush 把尾句强制定稿'
	);
	assert.equal(f.draft, '', 'flush 之后草稿清空（这句已经进定稿了）');
	assert.deepEqual(
		f.sealed.map((s) => [s.start, s.end, s.lang]),
		[[1, 1.8, 'en']],
		'时间取尾句自己的 token 范围'
	);

	// 重复 flush：不许再出一遍（同一句进两次笔记就是不可恢复的错）
	const again = c.flush();
	assert.deepEqual(again.sealed, [], 'flush 幂等：第二次不许再出句');
	assert.equal(again.draft, '');

	// flush 之后再来消息，照常工作（不是把核心关掉）
	const next = c.feed(
		JSON.stringify({
			tokens: [{ text: ' No way', start_ms: 2000, end_ms: 2400, is_final: true, language: 'en' }]
		})
	);
	assert.equal(next.draft, ' No way', 'flush 之后核心继续吃消息');
	assert.deepEqual(next.sealed, []);
});

test('flush 空核心：什么都不出，也不炸', async () => {
	const c = await core();
	c.reset();
	const f = c.flush();
	assert.deepEqual(f.sealed, []);
	assert.equal(f.draft, '');
});
