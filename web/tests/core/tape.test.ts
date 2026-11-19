// 真磁带（本机只读，不进仓库）回放：Pyodide 里的实时核心必须与 CPython 跑同一盘磁带**逐字相同**。
//
// 判据（S1 穿刺的 G0 闸门，现在变成常驻测试）：
// - 每条消息后的草稿文本与 CPython 基线逐字相同；
// - 每条消息封出来的句子（text / lang / start / end）逐条相同；
// - 全盘封口句序列逐字相同。
// 顺带量：Node 冷启动（Pyodide boot + stage + import rt）与单条消息处理耗时 p50 / p99。
//
// 课堂数据只在本机读：`~/.classlive/eval/diar_spike/`（磁带）与 `classlive-web-spike/s1/out/`（CPython 基线）。
// 缺任何一个就 skip 并说明，不造假数据，也不往仓库里拷磁带。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { test } from 'node:test';
import { loadPyodide } from 'pyodide';
import { EMPTY_PROJ, project, type ProjState, type SealedLine } from '../../src/lib/core/project.ts';
import { loadCore, type CoreLoad } from '../../src/lib/core/pyodide-runtime.ts';

const CORE_PY = path.resolve(import.meta.dirname, '../../static/core-py');
const PYODIDE_DIR = path.dirname(new URL(import.meta.resolve('pyodide/package.json')).pathname);
const TAPE_DIR = process.env.CL_TAPE_DIR ?? '/Users/owen/.classlive/eval/diar_spike';
const BASE_DIR = process.env.CL_CORE_BASE ?? '/Users/owen/classlive-web-spike/s1/out';
const TAPES = ['out7', 'out5'];

interface Baseline {
	i: number;
	wall: number;
	draft: string;
	sealed: SealedLine[];
}

let booted: Promise<CoreLoad> | null = null;

/** 冷启动只量一次，两盘磁带共用同一个 Pyodide（reset() 换核心） */
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

/** 线性插值分位数（与 feed.py 的 `_pct` 同口径）。 */
function pct(xs: number[], q: number): number {
	if (xs.length === 0) return 0;
	const s = [...xs].sort((a, b) => a - b);
	const pos = ((s.length - 1) * q) / 100;
	const lo = Math.floor(pos);
	const hi = Math.min(lo + 1, s.length - 1);
	return s[lo] + (s[hi] - s[lo]) * (pos - lo);
}

const report: Record<string, unknown> = {};

test('Pyodide 冷启动 + 两盘磁带回放与 CPython 基线逐字相同', async (t) => {
	const c = await core();
	report.pyodide_version = c.pyodideVersion;
	report.python_version = c.pythonVersion;
	report.ms_boot_cold = c.ms_boot;
	report.staged_bytes = c.stagedBytes;
	const allWork: number[] = [];

	for (const name of TAPES) {
		const tape = path.join(TAPE_DIR, name, 'prod_ctx.jsonl');
		const base = path.join(BASE_DIR, `${name}.cpython.jsonl`);
		if (!fs.existsSync(tape) || !fs.existsSync(base)) {
			t.diagnostic(`skip ${name}：缺 ${fs.existsSync(tape) ? base : tape}（课堂数据只在本机）`);
			continue;
		}
		const lines = fs.readFileSync(tape, 'utf8').split('\n').filter((l) => l.trim() !== '');
		const baseline = fs
			.readFileSync(base, 'utf8')
			.split('\n')
			.filter((l) => l.trim() !== '')
			.map((l) => JSON.parse(l) as Baseline);
		assert.equal(lines.length, baseline.length, `${name}: 磁带 ${lines.length} 条 ≠ 基线 ${baseline.length} 条`);

		c.reset();
		const work: number[] = [];
		let proj: ProjState = EMPTY_PROJ;
		const flat: SealedLine[] = [];
		const t0 = performance.now();

		for (let i = 0; i < lines.length; i++) {
			const a = performance.now();
			const snap = c.feed(lines[i]); // 浏览器里也是这条路径：一条消息一拍
			work.push(performance.now() - a);
			flat.push(...snap.sealed);

			assert.equal(snap.n_msgs, i + 1, `${name}#${i}: n_msgs`);
			assert.equal(snap.draft, baseline[i].draft, `${name}#${i}: 草稿文本与 CPython 不同`);
			assert.deepEqual(
				snap.sealed.map((s) => ({ text: s.text, lang: s.lang, start: s.start, end: s.end })),
				baseline[i].sealed,
				`${name}#${i}: 这一拍封出来的句子与 CPython 不同`
			);
			// 时钟：磁带时刻 − 第一条的时刻（Python 侧算，不比真时钟）
			assert.ok(Math.abs(snap.now - (baseline[i].wall - baseline[0].wall)) < 1e-9, `${name}#${i}: elapsed`);
			assert.equal(snap.notice, null, `${name}#${i}: 不该有 error 提示`);

			// 顺带把投影走一遍：行 id 连续、seg 与顺序一致、rev 严格递增（界面靠这些）
			const { state, view } = project(proj, snap);
			assert.equal(view.rev, proj.rev + 1, `${name}#${i}: rev`);
			view.rows.forEach((row, k) => {
				assert.equal(row.id, `s${k + 1}`, `${name}#${i}: 行 id`);
				assert.equal(row.seg, k + 1, `${name}#${i}: seg`);
				assert.equal(row.zh, '', `${name}#${i}: M2 之前没有译文`);
				assert.equal(row.zh_state, 'none', `${name}#${i}: zh_state`);
				const isLast = k === view.rows.length - 1;
				assert.equal(row.kind, isLast && snap.draft.trim() !== '' ? 'draft' : 'final', `${name}#${i}: kind`);
			});
			proj = state;
		}
		const ms = performance.now() - t0;
		allWork.push(...work);
		report[`${name}_n_msgs`] = lines.length;
		report[`${name}_n_sealed`] = flat.length;
		report[`${name}_ms_total`] = Math.round(ms);
		report[`${name}_ms_p50`] = +pct(work, 50).toFixed(3);
		report[`${name}_ms_p99`] = +pct(work, 99).toFixed(3);
		report[`${name}_ms_max`] = +pct(work, 100).toFixed(3);
		t.diagnostic(`${name}: ${lines.length} 条消息 / 封口 ${flat.length} 句 / ${Math.round(ms)} ms`);
	}

	if (allWork.length > 0) {
		report.ms_p50_all = +pct(allWork, 50).toFixed(3);
		report.ms_p99_all = +pct(allWork, 99).toFixed(3);
		report.ms_max_all = +pct(allWork, 100).toFixed(3);
		report.ms_boot_warm = (await core()).ms_boot;
		console.log(`[core 量具] ${JSON.stringify(report)}`);
	}
});

test('tick() 自己会推时钟：没有新消息也能把说完的一句封出来', async (t) => {
	const c = await core();
	c.reset(); // 退回「真链路」模式：消息不带 _wall，时钟用 monotonic
	const msg = JSON.stringify({
		tokens: [
			{ text: 'Hello world', start_ms: 0, end_ms: 1000, is_final: true, language: 'en' },
			{ text: '.', start_ms: 1000, end_ms: 1200, is_final: false, language: 'en' }
		]
	});
	const first = c.feed(msg);
	assert.equal(first.draft, 'Hello world.');
	assert.deepEqual(first.sealed, [], '刚说完还不该封（P_stable 0.3 s 没到）');

	const early = c.tick(1);
	assert.deepEqual(early.sealed, [], '0.3 s 之前 tick 不许封');
	assert.equal(early.seq, 1);

	await new Promise((r) => setTimeout(r, 350));
	const late = c.tick(2);
	assert.equal(late.sealed.length, 1, 'tick 该把到期的封口切出来');
	assert.equal(late.sealed[0].text, 'Hello world.');
	assert.equal(late.draft, '');
	t.diagnostic('tick 推时钟：0.35 s 后封出 "Hello world."');
});
