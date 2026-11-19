// 请求体逐字相同：网页版拼出来的 `{system, user}` 必须与桌面 CPython 生成的 fixture 一模一样。
// fixture 的来源见 `make-fixtures.py`（桌面 `prompt_core.py` 真跑一遍，只用合成英文句子）。
// 真链路里拼装跑在 Pyodide；这里用 Node 的 pyodide 跑**同一份** `static/core-py/prompt_core.py`，
// 两条路走的是同一个文件，所以这条判据对浏览器也成立（W4 的「一份 Python 两处跑」同一条）。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { test } from 'node:test';
import { loadPyodide } from 'pyodide';
import type { PyodideLike } from '../../src/lib/core/pyodide-runtime.ts';
import { pyPromptFromPyodide, type PromptBuilder } from '../../src/lib/translate/prompt.ts';

const CORE_PY = path.resolve(import.meta.dirname, '../../static/core-py');
const PYODIDE_DIR = path.dirname(new URL(import.meta.resolve('pyodide/package.json')).pathname);
const FIXTURE = path.resolve(import.meta.dirname, 'fixtures/prompts.json');

interface Case {
	en: string;
	context: string[];
	cfg: Record<string, unknown>;
	final: { system: string; user: string };
	draft: { system: string; user: string };
}

interface Fixture {
	tail: string;
	cases: Case[];
}

const fx = JSON.parse(fs.readFileSync(FIXTURE, 'utf8')) as Fixture;

let booted: Promise<PromptBuilder> | null = null;

function prompt(): Promise<PromptBuilder> {
	booted ??= (async () => {
		const { files } = JSON.parse(fs.readFileSync(path.join(CORE_PY, 'manifest.json'), 'utf8')) as {
			files: string[];
		};
		// 只起一份 Pyodide：翻译入口是 rt.py 的顶层函数，直接用 globals 拿（`loadCore` 只暴露 feed/tick）
		const py = (await loadPyodide({
			indexURL: `${PYODIDE_DIR}/`,
			stdout: () => {}
		})) as unknown as PyodideLike;
		for (const rel of files) {
			const dst = `/core-py/${rel}`;
			py.FS.mkdirTree(dst.slice(0, dst.lastIndexOf('/')));
			py.FS.writeFile(dst, new Uint8Array(await fs.promises.readFile(path.join(CORE_PY, rel))));
		}
		// 模块里的函数不在 Pyodide 的 globals 上：逐个绑到全局名（与 worker 的取法同一条）
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

test('定稿句请求体与桌面逐字相同（含两种上下文布局 / 术语 / 译名 / ctx_chunk=0）', async () => {
	const p = await prompt();
	for (const [i, c] of fx.cases.entries()) {
		const got = await p.final(c.en, c.context, c.cfg as never);
		assert.equal(got.system, c.final.system, `case ${i}: system 不同`);
		assert.equal(got.user, c.final.user, `case ${i}: user 不同`);
	}
});

test('草稿句请求体与桌面逐字相同', async () => {
	const p = await prompt();
	for (const [i, c] of fx.cases.entries()) {
		const got = await p.draft(c.en, c.context, c.cfg as never);
		assert.equal(got.system, c.draft.system, `case ${i}: system 不同`);
		assert.equal(got.user, c.draft.user, `case ${i}: user 不同`);
	}
});

test('块对齐窗口：同一块内起点不动（前缀缓存的前提）', async () => {
	const p = await prompt();
	const ctx = Array.from({ length: 18 }, (_, i) => `sentence ${i + 1}.`);
	// 第 18 句在块 [10,18) 内：窗口起点 = 第 10 句；第 20 句起进下一块
	const a = await p.final('x.', ctx.slice(0, 17).concat('x.'), {});
	const b = await p.final('y.', ctx.slice(0, 18).concat('y.'), {});
	const linesOf = (s: string): string[] => s.split('\n').filter((l) => l.startsWith('- '));
	assert.deepEqual(linesOf(a.user).slice(0, 7), linesOf(b.user).slice(0, 7));
	assert.ok(linesOf(a.user).length >= 7, '窗口至少 7 句（chunk=10 的上一块）');
});

test('草稿判据在 Python 那边：新草稿清中文、过期的不接受、定稿后清槽', async () => {
	const p = await prompt();
	const a = await p.draftBegin(1, 'The midterm covers');
	assert.equal(a.clear, true);
	assert.equal(await p.draftAccept(a.seq, 1, a.gen), true);

	// 同段**续写**（新文本以旧文本开头）不算换句：屏上的中文是接着长出来的，不许清
	const b = await p.draftBegin(1, 'The midterm covers chapters');
	assert.equal(b.clear, false, '同段续写不清中文');
	assert.equal(await p.draftAccept(b.seq, 1, b.gen), true, '最新那份能上屏');
	assert.equal(await p.draftAccept(a.seq, 1, a.gen), false, '更早的送翻已过期');

	await p.draftFinal(1);
	assert.equal(await p.draftAccept(b.seq, 1, b.gen), false, '这段定稿后草稿译文不许再上屏');

	// 新的一段、且不是上一句的续写 → 清槽
	const c = await p.draftBegin(2, 'A completely different sentence');
	assert.equal(c.clear, true, '换句 → 清掉屏上旧中文');
});
