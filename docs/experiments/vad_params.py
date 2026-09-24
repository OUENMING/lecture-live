#!/usr/bin/env python3
"""实验: 改 VAD 参数能不能减少碎片? —— **答案是不能。**

用法:
    .venv/bin/python docs/experiments/vad_params.py <音频> [起始秒] [时长秒]

结论(2026-09-24, 本机实测, 详见下方"实测结果")
---------------------------------------------
碎片(段尾无终止标点、语义不成立)的成因**不是**静音阈值。7 种配置跑同一段
8 分钟真实课堂录音, 结果**完全一样**(段数恒为 45):

    配置                                   段数   中位时长   无终止标点
    base  现状 0.70/0.55/0.45 hang.35 min.6    45     11.3s      20%
    V1    保守 0.85/0.70/0.60                  45     11.5s       —
    V2    平铺 0.70/0.70/0.70                  45     11.6s       —
    V3    平铺 0.60/0.60/0.60                  45     11.4s       —
    V4    hangover 0.35->0.55                  45     11.3s       —
    V5    pre-roll 300->500ms                  45     11.3s       —
    V6    最短句 0.6->1.2s                     44     11.35s      —

原因是**切点根本不在静音处** —— 这节课的讲师几乎不换气, 47% 的段落直接撞
`MAX_UTTERANCE_S=12.0` 上限。跨四个窗口复测: 55% / 64% / 82% / 90% 的段落停在
cap 上, 中位时长**全部正好 12.0s**。

而**放宽上限也没用**:

    MAX_UTTERANCE_S   段数   停在cap   无终止标点
    12                45     47%       20%
    16                39     38%       15%
    20                34     24%       26%
    30                30     13%       27%

段数降了, 碎片率没降(噪声级, 无趋势)。放宽只让每段更长, 不让每段更完整。

→ **结论: 碎片的主要成因是远场 ASR 本身的转写质量, 不是断句。** 调 VAD 参数
   是一条死路, 别再在这上面花时间。

一个真丢内容的副产物
--------------------
`空转写`(有音频、VAD 判成话、ASR 吐空字符串)在三份录音 10 个窗口上都能复现,
最高 **21%**。放宽上限略有缓解(12→20s 时 9%→6%)。这才是该查的方向。
"""
import sys
import numpy as np

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))
import vad
from capture import PeakNormalizer, SR, load_file
from asr import load_asr

CH = SR // 10
TERM = tuple(".?!…")


def seg_count(path, start, dur, tiers, hang, minu, preroll, maxu, asr):
    vad._end_silence_threshold = (
        lambda t: (lambda d: t[0] if d < 3 else (t[1] if d < 8 else t[2])))(tiers)
    vad.HANGOVER, vad.MIN_UTTERANCE_S = hang, minu
    vad.PRE_ROLL_CHUNKS, vad.MAX_UTTERANCE_S = preroll, maxu
    src = load_file(path)
    clip = src[int(start * SR): int((start + dur) * SR)]
    pn = PeakNormalizer()
    audio = np.concatenate([pn.process(clip[i:i + CH]) for i in range(0, len(clip) - CH, CH)])
    got = []
    seg = vad.Segmenter(lambda _b: None, lambda _b: got.append(_b))
    for i in range(0, len(audio), CH):
        seg.accept(audio[i:i + CH])
    seg.flush()
    texts = [asr.transcribe(b) for b in got]
    durs = [len(b) / SR for b in got]
    n = len(got)
    return {
        "n": n,
        "at_cap": sum(1 for d in durs if d >= maxu - 0.15),
        "no_end": sum(1 for t in texts if t and not t.rstrip().endswith(TERM)),
        "empty": sum(1 for t in texts if not t.strip()),
        "median_s": float(np.median(durs)) if durs else 0.0,
        "hard_cuts": seg.diag["hard_cuts"],
    }


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__.strip().splitlines()[3].strip())
    path = sys.argv[1]
    start = float(sys.argv[2]) if len(sys.argv) > 2 else 300.0
    dur = float(sys.argv[3]) if len(sys.argv) > 3 else 480.0
    asr = load_asr(__import__("os").path.expanduser("~/models/parakeet-tdt-0.6b-v3-int8"))

    print(f"{'配置':<40}{'段数':>5}{'停在cap':>9}{'无终止':>9}{'空转写':>9}{'中位s':>7}{'硬切':>6}")
    for name, tiers, hang, minu, pre, maxu in [
        ("base  0.70/0.55/0.45 hang.35 min.6 pre3", (0.70, 0.55, 0.45), 0.35, 0.6, 3, 12.0),
        ("V1    0.85/0.70/0.60", (0.85, 0.70, 0.60), 0.35, 0.6, 3, 12.0),
        ("V2    0.70/0.70/0.70", (0.70, 0.70, 0.70), 0.35, 0.6, 3, 12.0),
        ("V3    0.60/0.60/0.60", (0.60, 0.60, 0.60), 0.35, 0.6, 3, 12.0),
        ("V4    hangover 0.35->0.55", (0.70, 0.55, 0.45), 0.55, 0.6, 3, 12.0),
        ("V5    pre-roll 300->500ms", (0.70, 0.55, 0.45), 0.35, 0.6, 5, 12.0),
        ("V6    最短句 0.6->1.2s", (0.70, 0.55, 0.45), 0.35, 1.2, 3, 12.0),
        ("V7    上限 12->20s", (0.70, 0.55, 0.45), 0.35, 0.6, 3, 20.0),
        ("V8    上限 12->30s", (0.70, 0.55, 0.45), 0.35, 0.6, 3, 30.0),
    ]:
        r = seg_count(path, start, dur, tiers, hang, minu, pre, maxu, asr)
        n = max(r["n"], 1)
        print(f"{name:<40}{r['n']:>5}{r['at_cap']:>4}({r['at_cap']/n*100:>3.0f}%)"
              f"{r['no_end']:>4}({r['no_end']/n*100:>3.0f}%)"
              f"{r['empty']:>4}({r['empty']/n*100:>3.0f}%){r['median_s']:>7.1f}{r['hard_cuts']:>6}",
              flush=True)


if __name__ == "__main__":
    main()
