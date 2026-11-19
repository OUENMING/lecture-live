/* web/tests/audio-asr/worklet.test.mjs —— 采音 DSP 的离线自测（无浏览器 / 无 key / 不连网）
 *
 * 跑：node tests/audio-asr/worklet.test.mjs   （或 npm run test:audio-asr）
 *
 * 验四件事：
 *   1. 常量与帧长：1600 样本 / 100 ms，48k → 16k，一帧 s16le 3200 字节；
 *   2. 重采样**逐样本**与 S2 版一致（S2 版在 worktree 外的 s2 目录，找不到就 skip 并说明）；
 *   3. 分帧：跨块拼帧不丢不重、只出整帧；
 *   4. 端到端：1 s @48k 喂进处理器 → 10 帧 × 1600 样本，seq 连续。
 */
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { makeReport, loadWorklet } from './lib.mjs';

const HERE = path.dirname(new URL(import.meta.url).pathname);
const OURS = path.join(HERE, '..', '..', 'static', 'worklets', 'capture-worklet.js');
const S2 =
	process.env.S2_WORKLET || '/Users/owen/classlive-web-spike/s2/web/worklet.js';

/** 确定的伪随机信号（LCG + 两个正弦；同一份输入喂两个实现） */
function signal(n, seed = 12345) {
	const out = new Float32Array(n);
	let s = seed;
	for (let i = 0; i < n; i++) {
		s = (s * 1103515245 + 12345) & 0x7fffffff;
		const noise = (s / 0x7fffffff) * 2 - 1;
		out[i] = 0.3 * Math.sin((2 * Math.PI * 440 * i) / 48000) + 0.05 * noise;
	}
	return out;
}

function blocks(n, size) {
	const src = signal(n);
	const out = [];
	for (let i = 0; i < n; i += size) out.push(src.subarray(i, Math.min(n, i + size)));
	return out;
}

function sameF32(a, b) {
	if (a.length !== b.length) return `长度 ${a.length} vs ${b.length}`;
	for (let i = 0; i < a.length; i++) {
		if (!Object.is(a[i], b[i])) return `第 ${i} 个不同：${a[i]} vs ${b[i]}`;
	}
	return null;
}

export async function run(report = makeReport()) {
	const w = loadWorklet(OURS, 48000);
	const api = w.api;
	if (!api) {
		report.check('worklet 挂了 globalThis.__CL_CAPTURE', false);
		return report.finish();
	}

	report.check('处理器名 classlive-capture', w.registered?.name === 'classlive-capture', w.registered?.name);
	report.check(
		'常量 16000 Hz / 100 ms / 1600 样本',
		api.OUT_RATE === 16000 && api.FRAME_MS === 100 && api.FRAME_SAMPLES === 1600,
		`${api.OUT_RATE} / ${api.FRAME_MS} / ${api.FRAME_SAMPLES}`
	);

	/* ── 分帧 ── */
	{
		const f = new api.Framer(1600);
		const flat = signal(1600);
		const one = f.push(flat);
		report.check('1600 样本 → 1 帧', one.length === 1 && one[0].length === 1600, `${one.length} 帧`);

		const g = new api.Framer(1600);
		const two = g.push(flat.subarray(0, 1000)).concat(g.push(flat.subarray(1000)));
		const expect = api.toInt16(flat);
		report.check('1000+600 跨块 → 1 帧', two.length === 1 && two[0].length === 1600);
		report.check(
			'跨块拼出来的帧内容等于整块',
			two.length === 1 && two[0].every((v, i) => v === expect[i])
		);

		const h = new api.Framer(1600);
		const three = h.push(signal(4800));
		report.check('4800 样本 → 3 帧', three.length === 3, `${three.length} 帧`);

		const r = new api.Framer(1600);
		const tail = r.push(signal(1500));
		report.check('不足一帧不吐出（1500 样本 → 0 帧）', tail.length === 0);

		const t16 = api.toInt16(new Float32Array([0, 0.5, 1, -1, 2, -2]));
		report.check(
			's16le clamp + 四舍五入（0.5→16384 / 2→32767 / -2→-32767）',
			t16[0] === 0 && t16[1] === 16384 && t16[2] === 32767 && t16[3] === -32767 && t16[4] === 32767 && t16[5] === -32767,
			Array.from(t16).join(',')
		);
		report.check('1600 样本 = 3200 字节 s16le', expect.byteLength === 3200, `${expect.byteLength} 字节`);
	}

	/* ── 重采样：时长比例 ── */
	{
		const rs = new api.Resampler({ inRate: 48000, outRate: 16000, taps: 127 });
		let out = 0;
		for (const b of blocks(48000, 128)) out += rs.process(b).length;
		report.check('48k → 16k：1 s 输入出 16000 ± 2 样本', Math.abs(out - 16000) <= 2, `${out} 样本`);

		const rs2 = new api.Resampler({ inRate: 16000, outRate: 16000, taps: 127 });
		let out2 = 0;
		for (const b of blocks(16000, 128)) out2 += rs2.process(b).length;
		report.check(
			'16k 输入不做滤波（taps=1）且样本数基本不变',
			rs2.taps === 1 && Math.abs(out2 - 16000) <= 2,
			`taps=${rs2.taps} / ${out2}（同速率时首块丢 1 样本，S2 同）`
		);
	}

	/* ── 逐样本对照 S2 版 ── */
	if (!fs.existsSync(S2)) {
		report.skip('重采样逐样本与 S2 版一致', `找不到 ${S2}（可用 S2_WORKLET=… 指定）`);
	} else {
		const s2 = loadWorklet(S2, 48000).api;
		const a = new api.Resampler({ inRate: 48000, outRate: 16000, taps: 127 });
		const b = new s2.Resampler({ inRate: 48000, outRate: 16000, taps: 127 });
		const ins = blocks(48000, 128);
		let diffs = 0;
		let first = null;
		let total = 0;
		for (const blk of ins) {
			const ya = a.process(blk);
			const yb = b.process(blk);
			const d = sameF32(ya, yb);
			total += ya.length;
			if (d) {
				diffs += 1;
				first = first || `块 ${diffs}：${d}`;
			}
		}
		report.check(`重采样逐样本与 S2 版一致（${total} 样本 / ${ins.length} 块）`, diffs === 0, first || '逐位相同');

		const fa = new api.Framer(1600);
		const fb = new s2.Framer(1600);
		const sa = signal(16000);
		const framesA = fa.push(sa);
		const framesB = fb.push(sa);
		const frameDiff = framesA.length === framesB.length && framesA.every((f, i) => sameF32(f, framesB[i]) === null);
		report.check('分帧结果与 S2 版一致', frameDiff, `${framesA.length} vs ${framesB.length} 帧`);

		const na = new api.PeakNormalizer({});
		const nb = new s2.PeakNormalizer({});
		const chunks = blocks(16000 * 3, 1600);
		let normDiff = null;
		for (const c of chunks) {
			const ca = Float32Array.from(c);
			const cb = Float32Array.from(c);
			na.apply(ca);
			nb.apply(cb);
			normDiff = normDiff || sameF32(ca, cb);
			if (na.lastGain !== nb.lastGain || na.peak !== nb.peak || na.capped !== nb.capped) {
				normDiff = normDiff || `增益状态不同：${na.lastGain} vs ${nb.lastGain}`;
			}
		}
		report.check('PeakNormalizer 输出与状态逐帧与 S2 版一致', !normDiff, normDiff || `${chunks.length} 帧`);
	}

	/* ── 端到端：处理器 1 s @48k ── */
	{
		const p = w.instantiate({ normalize: true, taps: 127 });
		if (!p) {
			report.check('处理器可实例化', false);
		} else {
			const ready = w.messages.find((m) => m.type === 'ready');
			report.check(
				'构造即 post ready（48000→16000 / 1600 样本）',
				ready && ready.inRate === 48000 && ready.outRate === 16000 && ready.frameSamples === 1600,
				JSON.stringify(ready)
			);
			const src = signal(48000);
			for (let i = 0; i < 48000; i += 128) {
				p.process([[src.subarray(i, Math.min(48000, i + 128))]]); // 真 Web Audio：inputs[0] 是声道数组
			}
			const frames = w.messages.filter((m) => m.type === 'frame');
			const seqOk = frames.every((f, i) => f.seq === i);
			const sizeOk = frames.every((f) => f.samples === 1600 && f.data.byteLength === 3200);
			report.check(
				'1 s @48k → 10 帧 × 1600 样本 / 3200 字节，seq 0..9',
				frames.length === 10 && seqOk && sizeOk,
				`${frames.length} 帧 / seq ${frames.map((f) => f.seq).join(',')}`
			);
			const nonZero = frames.some((f) => new Int16Array(f.data).some((v) => v !== 0));
			report.check('帧里有真实样本（不是全零）', nonZero);
			report.check('process 返回 true（不自杀）', p.process([]) === true);
		}
	}

	return report.finish();
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) await run();
