/* capture-worklet.js — 浏览器采音 → Soniox 的 DSP（AudioWorklet 模块）
 *
 * 来源：~/classlive-web-spike/s2/web/worklet.js（S2 穿刺，WER 15.87% / 追平桌面），逻辑逐行搬过来，
 * 只换了处理器名（`s2-pcm` → `classlive-capture`）和自测出口（`__S2` → `__CL_CAPTURE`）。
 *
 * 做三件事，全是纯计算：
 *   1. 任意设备采样率（44.1k / 48k）→ 16 kHz 单声道：先 127-tap 加窗 sinc 低通（防混叠），
 *      再按分数位置线性插值抽到 16 kHz（等价于 127 相位的多相滤波器，写起来最短）。
 *   2. 攒成 100 ms 一帧（1600 样本 @16k）—— 与桌面 `capture.CHUNK`（=1600, 0.1 s）同口径。
 *   3. Float32 → s16le（clamp 后 *32767 四舍五入）。
 * 可选：每帧过一遍 `PeakNormalizer`（`capture.PeakNormalizer` 逐行移植，见类注释）。
 *
 * 一个上下文对象都不碰网络：主线程拿到 ArrayBuffer 后自己 ws.send。
 *
 * ⚠️ AudioWorklet 模块不能走打包（要静态文件），所以本文件同时被 `tests/audio-asr/worklet.test.mjs`
 *    用 node:vm 跑（无浏览器、无 key）：
 *    - 纯逻辑都挂在 `globalThis.__CL_CAPTURE` 上；
 *    - 不用 `instanceof` 判断 TypedArray（跨 realm 会假）；
 *    - 不 import 别的文件（AudioWorklet 的模块解析在 Safari/Firefox 上坑多）。
 */

const OUT_RATE = 16000;
const FRAME_MS = 100;
const FRAME_SAMPLES = (OUT_RATE * FRAME_MS) / 1000; // 1600
const PROCESSOR_NAME = 'classlive-capture';

/* ── 低通：加窗 sinc（Hamming），taps 取奇数 ─────────────────────────── */
function designLowpass(taps, cutoffNorm) {
  // cutoffNorm 是相对输入采样率的截止频率（0..0.5）
  const h = new Float64Array(taps);
  const M = (taps - 1) / 2;
  let sum = 0;
  for (let i = 0; i < taps; i++) {
    const n = i - M;
    const sinc = n === 0 ? 2 * cutoffNorm : Math.sin(2 * Math.PI * cutoffNorm * n) / (Math.PI * n);
    const win = 0.54 - 0.46 * Math.cos((2 * Math.PI * i) / (taps - 1));
    h[i] = sinc * win;
    sum += h[i];
  }
  for (let i = 0; i < taps; i++) h[i] /= sum; // 直流增益 = 1
  return h;
}

/* ── 重采样（单声道，跨块连续） ───────────────────────────────────────── */
class Resampler {
  constructor(opts) {
    const o = opts || {};
    this.inRate = o.inRate || 48000;
    this.outRate = o.outRate || OUT_RATE;
    this.step = this.inRate / this.outRate; // 每个输出样本前进多少输入样本
    const wantTaps = o.taps || 127;
    const needFilter = this.outRate < this.inRate;
    this.taps = needFilter ? Math.max(3, wantTaps | 1) : 1;
    // 截止取 0.45*outRate（留一成余量给过渡带），低于输出 Nyquist 8 kHz
    this.h = needFilter ? designLowpass(this.taps, (0.45 * this.outRate) / this.inRate) : null;
    this.hist = new Float32Array(this.taps - 1); // 上一块尾巴（FIR 跨块）
    this.ext = new Float32Array(0);
    this.p = 0; // 下一个输出样本在「滤波后本块」里的分数位置（可能是 -1）
    this.prevY = 0; // 上一块滤波后的最后一个样本
    this.y = new Float32Array(0);
  }

  /** @param {Float32Array} block 单声道输入 */
  /** @returns {Float32Array} 本块产出的 16 kHz 样本 */
  process(block) {
    const n = block.length;
    if (!n) return new Float32Array(0);
    const T = this.taps;
    let y;

    if (T > 1) {
      // 滤波：ext = hist ++ block；y[k] = Σ h[j]*ext[k+j]（输出与输入等长，延迟 (T-1)/2）
      const ext = new Float32Array(T - 1 + n);
      ext.set(this.hist, 0);
      ext.set(block, T - 1);
      this.ext = ext;
      if (this.y.length !== n) this.y = new Float32Array(n);
      y = this.y;
      const h = this.h;
      for (let k = 0; k < n; k++) {
        let acc = 0;
        for (let j = 0; j < T; j++) acc += h[j] * ext[k + j];
        y[k] = acc;
      }
      this.hist.set(ext.subarray(n, n + T - 1), 0);
    } else {
      y = block; // 上采样 / 同速率：不滤
    }

    // 线性插值抽到 outRate（跨块靠 prevY + 负位置续上）
    const out = [];
    let p = this.p;
    while (true) {
      const i = Math.floor(p);
      const f = p - i;
      if (i + 1 >= n) break;
      const a = i >= 0 ? y[i] : this.prevY;
      const b = y[i + 1];
      out.push(a + (b - a) * f);
      p += this.step;
    }
    this.p = p - n;
    this.prevY = y[n - 1];

    const res = new Float32Array(out.length);
    for (let k = 0; k < out.length; k++) res[k] = out[k];
    return res;
  }
}

/* ── 定长分帧（100 ms） ─────────────────────────────────────────────── */
class Framer {
  constructor(frameSamples) {
    this.size = frameSamples || FRAME_SAMPLES;
    this.buf = new Float32Array(this.size);
    this.n = 0;
  }

  /** @returns {Int16Array[]} 本块凑满的整帧（s16le 内容，按 Int16 存） */
  push(samples) {
    const frames = [];
    let k = 0;
    while (k < samples.length) {
      const take = Math.min(this.size - this.n, samples.length - k);
      this.buf.set(samples.subarray(k, k + take), this.n);
      this.n += take;
      k += take;
      if (this.n === this.size) {
        frames.push(toInt16(this.buf));
        this.n = 0;
      }
    }
    return frames;
  }
}

function toInt16(f32) {
  const out = new Int16Array(f32.length);
  for (let i = 0; i < f32.length; i++) {
    let v = f32[i];
    if (v > 1) v = 1;
    else if (v < -1) v = -1;
    out[i] = Math.round(v * 32767);
  }
  return out;
}

/* ── PeakNormalizer：`capture.py:101` 的逐行移植 ─────────────────────────
 *
 * 为什么网页里也要有它（不是凭感觉，参数全是桌面那份）：
 *   麦克风收到的电平取决于离电脑多远，讲师在讲台比凑近说低 20–30 dB；Soniox 与本地模型
 *   一样对**绝对电平**敏感。桌面链路（`probe_stream_asr.py` / `main`）在送 ASR 之前就套了
 *   这一层，网页链路不套 → 两条链路的输入电平不一样，WER 的差就不能只归给采音。
 *
 * 口径：用**滑动峰值**（不是逐块峰值）—— 逐块自适应会跟着音节抖动，增益抖动本身就会把句子
 *      切碎。hold_s = 峰值衰减 10 倍所需秒数。
 * 参数与桌面一致：ceiling 0.95 / max_gain 30 dB / hold_s 4 s / smooth 0.02；
 * `capture.CHUNK/SR = 1600/16000 = 0.1 s`，所以我们按 100 ms 帧调，衰减系数与桌面**逐位相同**。
 */
class PeakNormalizer {
  constructor(opts) {
    const o = opts || {};
    this.ceiling = o.ceiling == null ? 0.95 : o.ceiling;
    const maxGainDb = o.maxGainDb == null ? 30.0 : o.maxGainDb;
    const holdS = o.holdS == null ? 4.0 : o.holdS;
    const frameS = o.frameS == null ? FRAME_MS / 1000 : o.frameS;
    this.maxGain = Math.pow(10, maxGainDb / 20);
    this.decay = Math.pow(10, -frameS / Math.max(holdS, 0.1));
    this.smooth = o.smooth == null ? 0.02 : o.smooth;
    this.peak = 1e-4;
    this.gain = 1.0;
    this.blocks = 0;
    this.capped = 0;
    this.lastPeak = 0;
    this.lastGain = 1;
  }

  /** 原地乘增益 */
  apply(chunk) {
    let p = 0;
    for (let i = 0; i < chunk.length; i++) {
      const v = chunk[i] < 0 ? -chunk[i] : chunk[i];
      if (v > p) p = v;
    }
    this.peak = Math.max(p, this.peak * this.decay);
    const wantUncapped = this.ceiling / Math.max(this.peak, 1e-9);
    const want = Math.min(wantUncapped, this.maxGain);
    this.gain += (want - this.gain) * this.smooth;
    const g = p > 1e-9 ? Math.min(this.gain, this.ceiling / p) : this.gain;
    for (let i = 0; i < chunk.length; i++) chunk[i] *= g;
    this.blocks += 1;
    if (wantUncapped > this.maxGain) this.capped += 1;
    this.lastPeak = p;
    this.lastGain = g;
    return chunk;
  }
}

/* ── AudioWorkletProcessor ───────────────────────────────────────────── */
class CaptureProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const o = (options && options.processorOptions) || {};
    this.inRate = sampleRate; // 全局：本 AudioContext 的采样率
    this.rs = new Resampler({ inRate: sampleRate, outRate: OUT_RATE, taps: o.taps || 127 });
    this.framer = new Framer(FRAME_SAMPLES);
    this.norm = o.normalize ? new PeakNormalizer({}) : null;
    this.seq = 0;
    this.inSamples = 0;
    this.outSamples = 0;
    this.port.postMessage({
      type: 'ready',
      inRate: sampleRate,
      outRate: OUT_RATE,
      taps: this.rs.taps,
      frameSamples: FRAME_SAMPLES,
      frameMs: FRAME_MS,
    });
  }

  process(inputs) {
    const chans = inputs[0];
    if (!chans || !chans.length) return true;
    let mono = chans[0];
    this.inSamples += mono.length;
    if (chans.length > 1) {
      // 立体声：取平均（浏览器通常已按 channelCount=1 混好，这里只兜底）
      const acc = new Float32Array(mono.length);
      for (let c = 0; c < chans.length; c++) {
        const ch = chans[c];
        for (let i = 0; i < acc.length; i++) acc[i] += ch[i];
      }
      for (let i = 0; i < acc.length; i++) acc[i] /= chans.length;
      mono = acc;
    }
    const y = this.rs.process(mono);
    if (this.norm && y.length) this.norm.apply(y);
    const frames = this.framer.push(y);
    for (let k = 0; k < frames.length; k++) {
      const f = frames[k];
      this.outSamples += f.length;
      this.port.postMessage(
        { type: 'frame', seq: this.seq++, samples: f.length, data: f.buffer },
        [f.buffer]
      );
    }
    return true;
  }
}

registerProcessor(PROCESSOR_NAME, CaptureProcessor);

// 自测入口（浏览器里无害；node:vm 里靠它取纯逻辑）
globalThis.__CL_CAPTURE = {
  PROCESSOR_NAME,
  OUT_RATE,
  FRAME_MS,
  FRAME_SAMPLES,
  Resampler,
  Framer,
  PeakNormalizer,
  toInt16,
  designLowpass,
};
