#!/usr/bin/env python3
"""实验: 远场**去混响(WPE)** 对 ASR 有用吗? —— **本机实测: 有, 且是本轮唯一正收益。**

用法:
    .venv/bin/python docs/experiments/dereverb_ab.py <音频> <起秒> <时长秒> [音频 起 时 ...]

前置: `uv pip install nara_wpe`（MIT, 依赖仅 numpy/tqdm/soundfile/bottleneck/click,
      纯 Numpy 实现, 明确支持**单通道**）

为什么值得单独查这一条
----------------------
此前几轮我测的都是**降噪**(GTCRN/DPDFNet), 结论是负收益(−41% 词数)。但那条结论
**只覆盖降噪, 不覆盖去混响** —— Iwamoto 2022 原文明确限定 *"this paper focuses on
the single-channel SE (**noise reduction**) task"*。而教室远场里, **晚期混响**往往
才是退化主因(ScienceDirect 2017 综合评测: *"Late reverberation... contributes most
to the degradation"*)。

**所以"降噪有害"不能拿来否决"去混响"。** 这是我先前的一个方向性错误。

实测结果(2026-09-24, 8 窗口 / 95 段 / 3 份真实课堂录音)
------------------------------------------------------
    配置            词数   段数   无终止标点   空转写   平均 logprob
    关(现状)        1409    95    25 (26%)    4 ( 4%)   -0.397
    WPE t=10 d=3    1380    95    16 (17%)    4 ( 4%)   -0.400
    WPE t=40 d=3    1382    95    17 (18%)    3 ( 3%)   -0.389

**唯一一条"改善了碎片指标、其它都没退步"的路。**
- 无终止标点率 **26% → 17~18%**(相对 −31%)—— 我的自动判据大约对应人工判定碎片率的 60%
- 词数 −2%(噪声级)、空转写 4%→3%、logprob 略好(t=40)
- **成本 ~0.1% 实时**: 120s 音频的 WPE 只要 0.2s

⚠️ 尚未验证的三条(caveat, 别当成已解决)
1. **这是离线 WPE**(整段 120s 一次性算)。生产是流式, 要用 `nara_wpe` 的
   block-online 变体, 效果**未测**
2. **参数是在子集上选的**(t=40 d=3 先在单窗口上最好), 换房间/讲师要重调
3. **样本仍是 1 位讲师 / 3 份录音**; 现代教室若吸音好(RT60 低), 收益可能更小
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


def main():
    if len(sys.argv) < 4:
        sys.exit(__doc__.strip().splitlines()[3].strip())
    from nara_wpe.wpe import wpe

    rec = sherpa_onnx.OfflineRecognizer.from_transducer(
        encoder=f"{MODEL}/encoder.int8.onnx", decoder=f"{MODEL}/decoder.int8.onnx",
        joiner=f"{MODEL}/joiner.int8.onnx", tokens=f"{MODEL}/tokens.txt",
        num_threads=4, model_type="nemo_transducer")

    def decode(x):
        st = rec.create_stream()
        st.accept_waveform(SR, x)
        rec.decode_stream(st)
        r = st.result
        return (r.text.strip(),
                float(np.mean(r.ys_log_probs)) if len(r.ys_log_probs) else 0.0)

    args = sys.argv[1:]
    wins = [(args[i], float(args[i + 1]), float(args[i + 2]))
            for i in range(0, len(args) - 2, 3)]
    conds = [("关(现状)", None), ("WPE t=10 d=3", (10, 3)), ("WPE t=40 d=3", (40, 3))]
    agg = {k: dict(w=0, seg=0, noend=0, empty=0, lp=[]) for k, _ in conds}

    for path, a, b in wins:
        src = load_file(path)
        clip = src[int(a * SR): int((a + b) * SR)].astype(np.float64)
        for name, prm in conds:
            x = (normalize(clip.astype(np.float32)) if prm is None else
                 normalize(wpe(clip[None, :], taps=prm[0], delay=prm[1],
                               iterations=3)[0].astype(np.float32)))
            res = [decode(g) for g in segments(x)]
            ts = [t for t, _ in res]
            A = agg[name]
            A["w"] += sum(len(t.split()) for t in ts)
            A["seg"] += len(ts)
            A["noend"] += sum(1 for t in ts if t and not t.rstrip().endswith(TERM))
            A["empty"] += sum(1 for t in ts if not t)
            A["lp"] += [l for _, l in res]
        print(f"  {pathlib.Path(path).name[:12]} {a:.0f}-{b:.0f}s  ✓", flush=True)

    print(f"\n{'配置':<16}{'词数':>6}{'段数':>6}{'无终止':>10}{'空转写':>10}{'平均logprob':>13}")
    for name, _ in conds:
        A = agg[name]
        n = max(A["seg"], 1)
        print(f"  {name:<14}{A['w']:>6}{A['seg']:>6}"
              f"{A['noend']:>6}({A['noend'] / n * 100:>3.0f}%)"
              f"{A['empty']:>6}({A['empty'] / n * 100:>3.0f}%)"
              f"{np.mean(A['lp']):>13.3f}")


if __name__ == "__main__":
    main()
