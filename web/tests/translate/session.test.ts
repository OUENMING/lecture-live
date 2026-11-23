// 接线判据：翻译回填到 `rows`、草稿译文回填到 `draftZh`、每句的 t_sealed / t_zh_first 被记下来。
// 用假 Core（直接吐 ViewState）而不是磁带：这里判的是接线，不是核心；磁带那条在 tests/session。
// `Translator` 是真的（真 prompt_core + 假 fetch），所以「谁在什么时候送翻」也是真判据。
import '../session/runes.ts';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { test } from 'node:test';
import { loadPyodide } from 'pyodide';
import type { Core, Snapshot } from '../../src/lib/core/index.ts';
import type { PyodideLike } from '../../src/lib/core/pyodide-runtime.ts';
import type { ViewState } from '../../src/lib/state/viewstate.ts';
import { pyPromptFromPyodide, type PromptBuilder } from '../../src/lib/translate/prompt.ts';
import { Translator } from '../../src/lib/translate/translator.ts';
import { streamChat } from '../../src/lib/translate/stream.ts';
import { LiveSession } from '../../src/lib/session/live.svelte.ts';
import { fakeFetch, sseText } from './fake-fetch.ts';

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

/** 假核心：只按调用方给的快照吐 ViewState（`feed`/`tick`/`flush` 都是空操作）。 */
function fakeCore(): { core: Core; push: (rows: ViewState['rows']) => void } {
	let cb: ((vs: ViewState) => void) | null = null;
	let rev = 0;
	const snap = (_s: Snapshot): void => {};
	const core: Core = {
		ready: () => Promise.resolve(),
		feed: snap,
		tick: snap,
		flush: () => {},
		onView: (f) => {
			cb = f;
		},
		dispose: () => {}
	};
	const push = (rows: ViewState['rows']): void => {
		rev += 1;
		cb?.({ rev, rows, status: {}, course: '', elapsed_s: 0 });
	};
	return { core, push };
}

function rowsWith(
	finals: { seg: number; en: string }[],
	draft?: { seg: number; en: string }
): ViewState['rows'] {
	const out: ViewState['rows'] = finals.map((f) => ({
		id: `s${f.seg}`,
		kind: 'final',
		en: f.en,
		zh: '',
		zh_state: 'none',
		seg: f.seg,
		t_s: f.seg * 5,
		terms: []
	}));
	if (draft) {
		out.push({
			id: `s${draft.seg}`,
			kind: 'draft',
			en: draft.en,
			zh: '',
			zh_state: 'none',
			seg: draft.seg,
			t_s: draft.seg * 5,
			terms: []
		});
	}
	return out;
}

/** 等一个条件成立（接线是异步的：Python 往返 + fetch 流），最多 2 s。 */
async function waitFor(ok: () => boolean, what: string): Promise<void> {
	const until = Date.now() + 2000;
	while (Date.now() < until) {
		if (ok()) return;
		await new Promise<void>((r) => setTimeout(r, 10));
	}
	assert.fail(`等不到：${what}`);
}

test('翻译接进会话：中文进 rows、草稿译文进 draftZh、延迟打点齐全', async () => {
	const p = await prompt();
	const ff = fakeFetch([
		{ sse: sseText(['ZH: ', '第一句译文。', '\nEN: First sentence.']) },
		{ sse: sseText(['第二句草稿译文']) }
	]);
	const { core, push } = fakeCore();
	const s = new LiveSession({
		audio: { start: async () => {}, stop: () => {}, get level() { return 0.05; } },
		core,
		getKey: async () => 'tape',
		asr: { send: () => {}, onMessage: () => {}, state: 'open', close: () => {} },
		makeTranslator: (hooks) =>
			new Translator({
				prompt: p,
				endpoint: () => ({ url: 'https://example.test/v1/chat/completions', headers: {} }),
				chat: (opts) => streamChat({ ...opts, fetch: ff.fetch }),
				onFinal: hooks.onFinal,
				onDraft: hooks.onDraft
			})
	});
	await s.start();

	push(rowsWith([{ seg: 1, en: 'first sentence.' }]));
	await waitFor(() => s.rows[0]?.zh === '第一句译文。', '定稿中文上屏');
	assert.equal(s.rows[0].en, 'First sentence.', '修正后的英文换上去了');

	push(rowsWith([{ seg: 1, en: 'first sentence.' }], { seg: 2, en: 'second draft' }));
	await waitFor(() => s.draftZh === '第二句草稿译文', '草稿译文上屏');
	assert.equal(s.draftEn, 'second draft');

	const lat = s.latency();
	assert.equal(lat.n, 1, '只有一句出了中文');
	assert.equal(typeof lat.p50, 'number');
	assert.ok((lat.max ?? 0) >= 0, '总延迟非负');
	assert.ok((lat.sealP50 ?? 0) >= 0, '有「段尾 → 封口」的拆分');
	assert.ok((lat.p50 ?? 0) >= (lat.zhP50 ?? 0), '总延迟从音频结束算起，不小于「封口 → 首字」');
	await s.end();
});

test('翻译失败：界面留着英文，后面那句照常（延迟只数出了中文的）', async () => {
	const p = await prompt();
	// 500 属基础设施类失败：首发 + 三次退避重试 = 4 次都失败才落 failed（退避不真等）
	const ff = fakeFetch([
		{ status: 500 },
		{ status: 500 },
		{ status: 500 },
		{ status: 500 },
		{ sse: sseText(['ZH: ', '第二句译文。', '\nEN: Second sentence.']) }
	]);
	const { core, push } = fakeCore();
	const s = new LiveSession({
		audio: { start: async () => {}, stop: () => {}, get level() { return 0.05; } },
		core,
		getKey: async () => 'tape',
		asr: { send: () => {}, onMessage: () => {}, state: 'open', close: () => {} },
		makeTranslator: (hooks) =>
			new Translator({
				prompt: p,
				endpoint: () => ({ url: 'https://example.test/v1/chat/completions', headers: {} }),
				chat: (opts) => streamChat({ ...opts, fetch: ff.fetch }),
				onFinal: hooks.onFinal,
				onDraft: hooks.onDraft,
				sleep: async () => {}
			})
	});
	await s.start();
	push(rowsWith([{ seg: 1, en: 'first sentence.' }]));
	await waitFor(() => s.rows[0]?.en !== undefined, '第一行出现');
	await new Promise<void>((r) => setTimeout(r, 300));
	assert.equal(s.rows[0].zh, '', '两次失败后没有中文');
	assert.equal(s.rows[0].en, 'first sentence.', '界面留着英文（不空白、不卡住）');

	push(rowsWith([{ seg: 1, en: 'first sentence.' }, { seg: 2, en: 'second sentence.' }]));
	await waitFor(() => s.rows[1]?.zh === '第二句译文。', '第二句照常翻');
	assert.equal(s.latency().n, 1);
	await s.end();
});
