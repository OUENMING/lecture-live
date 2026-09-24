#!/usr/bin/env python3
"""实验: 去混响(WPE) 对远场 ASR 有用吗? —— **结论: 用正确管线+正确判据, 测不出任何效果。**

## 最终结论（2026-09-24, 正确管线, 8 窗口 / 95 段 / 3 份真实录音）

    条件          词数    段数   无终止标点   空转写
    关           1409     95    25 (26%)    4 ( 4%)
    WPE t=10     1300    103    19 (18%)    8 ( 8%)

    词数    −7.7%   判据需 >±8%    → ❌ 落在噪声内
    无终止  −6.3 pp  判据需 >±18pp  → ❌ 落在噪声内

**方向偏正面但幅度全部落在噪声带内, 且空转写翻倍(4%→8%)、段数 +8%(WPE 确实扰动了 VAD)。**
判定: **不接入。** 成本 3.2% 实时(960s 音频 30.6s)不算贵, 但**没有可测收益, 不值得多一个依赖。**

⚠️ 注意别反过来过度解读成"WPE 有害" —— 词数 −7.7% 恰好卡在 ±8% 阈值上, 正确说法是
**"这个样本量下测不出效果"**, 不是"它伤转写"。

管线逐层验证过(才有资格说上面这些话):
    ① STFT→iSTFT 往返        相关 1.000000  ✅ 轴处理正确
    ② OnlineWPE 逐帧差异      均值 0.034 最大 6.91  ✅ 确实改变了信号(不是恒等)
    ③ 时域输出                相关 0.9736, RMS +0.0 dB  ✅ 正常语音, 不是静音
    ④ 参数有效性              taps=10 → 0.034 / taps=40 → 0.108  ✅ 参数真的起作用

## 曾报过"有效(−31%)", 已撤回 —— 记下那次错在哪


用法:
    .venv/bin/python docs/experiments/dereverb_ab.py <音频> <起秒> <时长秒> [...]
    .venv/bin/python docs/experiments/dereverb_ab.py --noise-floor <音频> <起秒> <时长秒>

⚠️⚠️ 先读这段: 这个脚本曾经报出过一个**假结果**, 原因是两层错误叠加
------------------------------------------------------------------
**第一层: 调用错了 —— 它做的是恒等变换, 根本没去混响。**

`nara_wpe.wpe()` 的 docstring 第一行是:
    Y: Complex valued STFT signal with shape (..., D, T)
**它要的是复数 STFT 频谱, 不是时域波形。** 当时传的是 `(1, N)` 时域 float64 ——
形状校验**静默通过**（被当成 `D=1`），但内部算的不是去混响。实测:

    wpe(x[None, :])   # x 是时域
      输入 RMS 1.00079  →  输出 RMS 1.00079
      与原信号相关 = 1.0000        ← 恒等变换

**第二层: 就算调用对了, 那个指标在这个样本量下也测不出东西。**

对**同一段音频**只加 ±1e-6 的数值扰动（人耳绝对听不出），重跑同一条链路:

    原样            无终止标点 36.4%   词数 152
    扰动 seed=1      无终止标点 18.2%   词数 165    ← −18.2 个百分点!
    扰动 seed=2      无终止标点 27.3%   词数 161
    扰动 seed=3      无终止标点 27.3%   词数 161
    扰动 seed=4      无终止标点 18.2%   词数 163    ← −18.2 个百分点!

**「无终止标点率」在 n≈11 段时的噪声底线约 ±18 个百分点**, 词数约 ±8%。
而当时报出的"改善"只有 8 个百分点 —— **完全落在噪声里。**
（运行 `--noise-floor` 可以在你自己的音频上重测这个底线。）

所以那个 "碎片 26%→18%（−31%）" **两个理由都不成立, 已从 README 撤下**。

## 据此修正的判据（本项目所有 A/B 都该遵守）

| 指标 | 可判定的最小效应 |
|---|---|
| 无终止标点率 | **> ±18 个百分点**（n≈11 段时） |
| 词数 | **> ±8%** |
| 空转写率 | 样本太小时不可判 |

**按这个底线重判旧结论**: 降噪词数 −41% ✅ 成立; whisper 词数 +33% ✅ 成立;
whisper 碎片 26%→14%(12pp) ⚠️ 落在噪声带内（但词数+逐段对照是硬的）;
OA 空转写 15%→0% ❌ 撤回。

## 正确的流式 API（如果将来要做）

`OnlineWPE(taps, delay, alpha, power_estimate, channel, frequency_bins)`
- ⚠️ `channel` **默认是 8** —— 单通道必须显式传 `channel=1`
- ⚠️ `frequency_bins` 必须 = `size//2+1`（512 点 FFT → 257）
- `.step_frame((F, D))` 或 `.step_block(...)`（源码注释: 只有 `block_shift=1` 可用）
- 实测 `taps=10, delay=3`: 3s 音频 0.09s CPU ≈ **0.6% 单核**, 内存平稳
- ⚠️ **`taps=40` 掉到 2.3× 实时**（贵 13 倍）—— 离线用的 40 不适合流式

⚠️ **离线 `wpe_v8` 在本机是坏的**: 5s 音频要 **3.7 GB** 内存（两次 OOM 被杀）,
且输出 −79 dBFS ≈ 静音。**根因未查明。**

依赖: WPE 做 STFT 要用 `nara_wpe.utils`, 它 **import scipy** —— 不在
`nara_wpe` 声称的 5 个轻依赖里, 要单独装。

## 下一步（如果还要追这条）

1. 先解决"离线版为何吐静音" —— 那是校验实现的基准
2. 用**正确管线**（STFT → OnlineWPE → iSTFT）在 **≥8 窗口**上重跑, 并按下面的噪声底线判读
3. 判据: 无终止标点要 **>18 个百分点**、词数要 **>8%** 才算数
"""
import sys
import os
import pathlib

import numpy as np
import sherpa_onnx

HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2]))
import vad                                                    # noqa: E402
from capture import PeakNormalizer, SR, load_file             # noqa: E402

CH = SR // 10
TERM = tuple(".?!…")
MODEL = os.path.expanduser("~/models/parakeet-tdt-0.6b-v3-int8")


def normalize(x):
    pn = PeakNormalizer()
    return np.concatenate([pn.process(x[i:i + CH])
                           for i in range(0, len(x) - CH, CH)])


def segments(x):
    got = []
    seg = vad.Segmenter(lambda _b: None, lambda y: got.append(y))
    for i in range(0, len(x), CH):
        seg.accept(x[i:i + CH])
    seg.flush()
    return got


def measure(rec, x):
    texts = []
    for g in segments(x):
        st = rec.create_stream()
        st.accept_waveform(SR, g)
        rec.decode_stream(st)
        texts.append(st.result.text.strip())
    n = max(len(texts), 1)
    return {
        "words": sum(len(t.split()) for t in texts),
        "seg": len(texts),
        "noend": sum(1 for t in texts if t and not t.rstrip().endswith(TERM)),
        "empty": sum(1 for t in texts if not t),
    }


def load_recognizer():
    return sherpa_onnx.OfflineRecognizer.from_transducer(
        encoder=f"{MODEL}/encoder.int8.onnx", decoder=f"{MODEL}/decoder.int8.onnx",
        joiner=f"{MODEL}/joiner.int8.onnx", tokens=f"{MODEL}/tokens.txt",
        num_threads=4, model_type="nemo_transducer")


def noise_floor(audio_path, a, b, seeds=(1, 2, 3, 4)):
    """⚠️ 必读: 量化这套指标在本样本量下的**噪声底线**。

    对同一段音频只加 ±1e-6 的扰动重跑, 看指标自己会晃多少。
    **任何小于这个晃动幅度的"改善"都是噪声, 不是效应。**"""
    rec = load_recognizer()
    base = normalize(load_file(audio_path)[int(a * SR):int((a + b) * SR)])
    m0 = measure(rec, base)
    print(f"  原样         无终止 {m0['noend'] / m0['seg'] * 100:5.1f}%   "
          f"词数 {m0['words']}  段数 {m0['seg']}")
    rates = []
    for s in seeds:
        rng = np.random.default_rng(s)
        pert = base + rng.standard_normal(len(base)).astype(np.float32) * 1e-6
        m = measure(rec, pert)
        r = m["noend"] / m["seg"] * 100
        rates.append(r)
        print(f"  扰动 seed={s}   无终止 {r:5.1f}%   "
              f"词数 {m['words']}  段数 {m['seg']}   ({r - m0['noend'] / m0['seg'] * 100:+.1f} pp)")
    r0 = m0["noend"] / m0["seg"] * 100
    print(f"\n  → 噪声底线: 无终止标点 **±{max(abs(r - r0) for r in rates):.1f} 个百分点**")
    print("     （小于这个幅度的「改善」都是噪声，不是效应）")


def main():
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__.strip().splitlines()[3].strip())
    if args[0] == "--noise-floor":
        if len(args) < 4:
            sys.exit("用法: --noise-floor <音频> <起秒> <时长秒>")
        print("噪声底线测量（同一音频，只加 ±1e-6 扰动）:")
        noise_floor(args[1], float(args[2]), float(args[3]))
        return
    sys.exit(__doc__.strip().splitlines()[3].strip())


if __name__ == "__main__":
    main()
