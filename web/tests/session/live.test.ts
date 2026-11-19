// 磁带端到端：LiveSession（真 Python 核心 + 假麦克风 + 假 Soniox）跑真磁带，
// 定稿句序列必须与 S1 用 CPython 跑同一盘磁带的结果**逐句相同**。
//
// 判据来自 TASKS M1：「回放 S1 的磁带，页面上的封口句与桌面回放逐字相同」。
// 这里判的是**界面看到的东西**（rows / draftEn / status），不是核心内部状态 ——
// 接线错了（少的、多的、重复的、顺序错的）只有在这一层才看得见。
//
// 期望值怎么来：S1 的基线是逐拍落的中间态，最后一拍可能还挂着半句；桌面版收尾会把它 flush 出来。
// 所以「期望定稿句」= 基线里的封口句 + （基线末尾草稿非空时）那句草稿，见 `expectedTexts`。
//
// 课堂数据只在本机读；磁带或基线缺了就 skip 并说明。
import './runes.ts';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { test } from 'node:test';
import { loadPyodide } from 'pyodide';
import { loadCore, type CoreLoad, type PyodideLike } from '../../src/lib/core/pyodide-runtime.ts';
import { localCore } from './local-core.ts';
import { LiveSession } from '../../src/lib/session/live.svelte.ts';
import { tapeReplay } from '../../src/lib/session/tape.ts';
import { baselineSentences, loadBaseline, loadTape, type Baseline } from './fixtures.ts';

const CORE_PY = path.resolve(import.meta.dirname, '../../static/core-py');
const PYODIDE_DIR = path.dirname(new URL(import.meta.resolve('pyodide/package.json')).pathname);

/** 期望的定稿句序列：基线封口句 + 收尾 flush 出的尾句（基线末尾草稿非空时才有） */
function expectedTexts(base: readonly Baseline[]): string[] {
	const out = baselineSentences(base).map((s) => s[0]);
	const tail = base.length ? base[base.length - 1].draft : '';
	if (tail.trim() !== '') out.push(tail);
	return out;
}

let booted: Promise<CoreLoad> | null = null;

/** 冷启动只量一次；每个用例自己 `reset()` 换核心 */
function pythonCore(): Promise<CoreLoad> {
	booted ??= (async () => {
		const { files } = JSON.parse(fs.readFileSync(path.join(CORE_PY, 'manifest.json'), 'utf8')) as {
			files: string[];
		};
		return loadCore({
			load: () => loadPyodide({ indexURL: `${PYODIDE_DIR}/`, stdout: () => {} }) as unknown as Promise<PyodideLike>,
			read: async (rel) => new Uint8Array(await fs.promises.readFile(path.join(CORE_PY, rel))),
			manifest: files
		});
	})();
	return booted;
}

/** 边跑边采样状态（回放是虚拟时钟，跑得比真实时间快，不能靠 sleep 等） */
async function untilDone(done: Promise<void>, sample: () => void): Promise<void> {
	let finished = false;
	void done.then(() => {
		finished = true;
	});
	while (!finished) {
		sample();
		await new Promise((r) => setTimeout(r, 0));
	}
	sample();
}

for (const name of ['out7', 'out5']) {
	test(`磁带端到端（${name}）：定稿句与 CPython 逐句相同`, async (t) => {
		const { lines, missing } = loadTape(name);
		const base = loadBaseline(name);
		if (missing || !base) {
			t.skip(`缺 ${missing ?? 'S1 基线'}（课堂数据只在本机，不进仓库）`);
			return;
		}
		const py = await pythonCore();
		py.reset();
		const c = localCore(py);
		const tape = tapeReplay({ messages: lines });
		const s = new LiveSession({ audio: tape.audio, asr: tape.asr, core: c, getKey: async () => 'tape' });
		await s.start();
		assert.equal(s.status, 'listening', '起完之后应在听');
		await tape.done;
		await s.end();

		const want = expectedTexts(base);
		const got = s.rows.map((r) => r.en);
		assert.equal(got.length, want.length, `定稿句数：界面 ${got.length} ≠ 期望 ${want.length}`);
		for (let k = 0; k < want.length; k++) {
			assert.equal(got[k], want[k], `第 ${k + 1} 句与 CPython 不同`);
		}
		// 起点：基线里有时间的那几句（尾句是 flush 出来的，没有基线对照）
		const bases = baselineSentences(base);
		for (let k = 0; k < bases.length; k++) {
			assert.equal(s.rows[k].t, bases[k][2], `第 ${k + 1} 句起点（秒）不同`);
			assert.equal(s.rows[k].id, k + 1, `第 ${k + 1} 行的 id 应是序号`);
		}
		// M2 之前的约定：还没有译文
		assert.deepEqual(
			s.rows.every((r) => r.zh === ''),
			true,
			'M2 之前译文应当为空'
		);
		assert.equal(s.draftEn, '', 'end() 之后草稿清空（尾句已定稿）');
		assert.equal(s.playing, false);
		assert.equal(s.status, 'ended');
		// 句文本可能真的重复（老师重复说 "Okay."），所以判的是 id：一句一个号，不许跳号
		assert.deepEqual(
			s.rows.map((r) => r.id),
			s.rows.map((_, k) => k + 1),
			'id 必须是连续序号'
		);
		t.diagnostic(`${name}：${lines.length} 条消息 → ${got.length} 句定稿，与 CPython 逐句相同`);
	});
}

test('断线重连后不重复、不丢句（out7 中段断 8 s + 重发 5 条）', async (t) => {
	const { lines, missing } = loadTape('out7');
	const base = loadBaseline('out7');
	if (missing || !base) {
		t.skip(`缺 ${missing ?? 'S1 基线'}（课堂数据只在本机）`);
		return;
	}
	const py = await pythonCore();
	py.reset();
	const c = localCore(py);
	const tape = tapeReplay({ messages: lines, outage: { from: 30, to: 38, resend: 5 }, yieldEvery: 20 });
	const s = new LiveSession({ audio: tape.audio, asr: tape.asr, core: c, getKey: async () => 'tape' });
	await s.start();

	const seen = new Set<string>();
	await untilDone(tape.done, () => seen.add(s.status));
	await s.end();

	assert.ok(seen.has('reconnecting'), `断线期间 status 应出现 reconnecting（实际 ${[...seen].join('/')}）`);
	assert.ok(seen.has('listening'), '重连之后应回到 listening');
	const got = s.rows.map((r) => r.en);
	assert.deepEqual(got, expectedTexts(base), '断线（含重发）之后句子不重复、不丢、顺序不变');
	t.diagnostic(`断线用例：${got.length} 句，状态经过 ${[...seen].join(' → ')}`);
});

test('6 s 没有声音 → silent，恢复有声 → listening（E1）', async (t) => {
	const { lines, missing } = loadTape('out7');
	if (missing) {
		t.skip(`缺 ${missing}（课堂数据只在本机）`);
		return;
	}
	const py = await pythonCore();
	py.reset();
	const c = localCore(py);
	// 0–2 s 有声，2–10 s 静音，10 s 之后恢复
	const tape = tapeReplay({ messages: lines, level: (ts) => (ts > 2 && ts < 10 ? 0 : 0.05), yieldEvery: 20 });
	const s = new LiveSession({ audio: tape.audio, asr: tape.asr, core: c, getKey: async () => 'tape' });
	await s.start();

	const seen: string[] = [];
	await untilDone(tape.done, () => {
		if (seen[seen.length - 1] !== s.status) seen.push(s.status);
	});
	await s.end();

	assert.ok(seen.includes('silent'), `静音 8 s 应出现 silent（实际经过 ${seen.join(' → ')}）`);
	assert.equal(seen[seen.length - 1], 'listening', `恢复有声后应回到 listening（实际 ${seen[seen.length - 1]}）`);
	assert.ok(seen.indexOf('silent') > 0, 'silent 之前应当先是 listening');
	t.diagnostic(`silent 判定：状态经过 ${seen.join(' → ')}`);
});

test('end() 后尾句只出现一次（停在半句上截断）', async (t) => {
	const { lines, missing } = loadTape('out7');
	const base = loadBaseline('out7');
	if (missing || !base) {
		t.skip(`缺 ${missing ?? 'S1 基线'}（课堂数据只在本机）`);
		return;
	}
	// 找一个「草稿非空」的拍当截断点：那就是老师最后那半句
	const limit = Math.floor(base.length / 3);
	let cut = -1;
	for (let k = limit; k > 0 && k > limit - 300; k--) {
		if (base[k].draft.trim() !== '') {
			cut = k;
			break;
		}
	}
	assert.ok(cut > 0, '基线里应当有停在半句上的拍（不然这条用例测不到尾句）');
	const tail = base[cut].draft;

	const py = await pythonCore();
	py.reset();
	const c = localCore(py);
	const tape = tapeReplay({ messages: lines.slice(0, cut + 1) });
	const s = new LiveSession({ audio: tape.audio, asr: tape.asr, core: c, getKey: async () => 'tape' });
	await s.start();
	await tape.done;

	assert.equal(s.draftEn, tail, '截断时草稿就是那半句');
	await s.end();
	await s.end(); // 幂等：界面可能点两次「下课」

	const texts = s.rows.map((r) => r.en);
	assert.equal(texts.filter((x) => x === tail).length, 1, '尾句必须正好出现一次');
	assert.deepEqual(
		s.rows.map((r) => r.id),
		s.rows.map((_, k) => k + 1),
		'id 必须是连续序号（重复封口会让 id 跳号）'
	);
	assert.equal(texts.at(-1), tail, '尾句应当排在最后');
	assert.equal(s.draftEn, '', 'end() 之后草稿清空');
	t.diagnostic(`end()：尾句 "${tail.slice(0, 30)}" 只出现一次，定稿共 ${texts.length} 句`);
});

test('phase 的唯一来源：麦没起来 → status=error + 中文提示，不抛', async (t) => {
	const py = await pythonCore();
	py.reset();
	const c = localCore(py);
	const tape = tapeReplay({ messages: [] });
	let stopped = 0;
	const s = new LiveSession({
		audio: {
			start: async () => {
				throw new Error('Permission denied');
			},
			stop: () => {
				stopped += 1;
			},
			get level() {
				return 0;
			}
		},
		asr: tape.asr,
		core: c,
		getKey: async () => 'tape'
	});
	await s.start(); // 不抛
	assert.equal(s.status, 'error');
	assert.match(s.message, /麦克风/, '提示要说清是麦克风的问题');
	assert.match(s.message, /Permission denied/, '把底层原因带上（排错要看）');
	assert.equal(s.playing, false);
	assert.equal(stopped, 1, '失败也要把麦关掉（权限灯不许一直亮）');
	t.diagnostic(`麦失败：status=error，message="${s.message}"`);
});
