#!/usr/bin/env python3
"""实验: sherpa-onnx 的**热词偏置**(hotwords_file) 对 Parakeet 有没有用? —— **没有, 而且没有可用区间。**

用法:
    .venv/bin/python docs/experiments/hotwords_ab.py <音频> <起始秒> <时长秒>

结论(2026-09-24, 本机实测)
--------------------------
两个前提先卡住了这条路:

1. **热词必须配 `decoding_method="modified_beam_search"`** —— 现行生产用的是
   `greedy_search`, 挂 hotwords_file 会直接报错:
   `Please use --decoding-method=modified_beam_search when using --hotwords-file`
2. 光是换成 beam search(不加热词)**就已经是负收益**:

     greedy(现状)     词数 104   CPU 16.0s
     beam(无热词)      词数  67   CPU 22.3s     ← 词数掉 36%, CPU +39%
     beam+热词         词数  80   CPU 22.0s

3. 而热词权重**没有可用区间**:

     score=2.0   正常文本, 但热词**一个都没触发**
     score=8.0   崩坏: "6 0 microne.. io" / "3 3 0 1 3 0 3 0 0 1 1. 2 0. 6 0 5 0..."
     score=20.0  **整段背诵热词表**: "entropy ionic macroscopic microscopic ionic microscopic..."

**机理**: 热词偏置是给 beam search 的 token 概率加偏置。当声学证据**根本不支持**那个词时
(如 "max chocolate" 之于 "macroscopic"), 要把它掰回来需要极大的偏置 —— 而那样偏置会
完全压过声学, 于是无论听到什么都输出热词表。**低权重不生效、高权重幻觉, 中间没有位置。**

⚠️ 本实验用 `hotwords_thermo.txt`(热力学术语)。目标失败句是 "max chocolate"(应为
"macroscopic") —— 在 greedy 基线上它**根本没被念错到能修的程度**时也不会命中,
高权重下则直接触发背诵。
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
MODEL = os.path.expanduser("~/models/parakeet-tdt-0.6b-v3-int8")
HOTWORDS = HERE.with_name("hotwords_thermo.txt")


def make(decoding: str, score: float | None = None, beam: int = 4):
    kw = {"decoding_method": decoding}
    if decoding != "greedy_search":
        kw["max_active_paths"] = beam
    if score is not None:
        kw.update(hotwords_file=str(HOTWORDS), hotwords_score=score,
                  modeling_unit="bpe", bpe_vocab=f"{MODEL}/tokens.txt")
    return sherpa_onnx.OfflineRecognizer.from_transducer(
        encoder=f"{MODEL}/encoder.int8.onnx", decoder=f"{MODEL}/decoder.int8.onnx",
        joiner=f"{MODEL}/joiner.int8.onnx", tokens=f"{MODEL}/tokens.txt",
        num_threads=4, model_type="nemo_transducer", **kw)


def run(rec, segs):
    out = []
    for g in segs:
        st = rec.create_stream()
        st.accept_waveform(SR, g)
        rec.decode_stream(st)
        out.append(st.result.text.strip())
    return out


def main():
    if len(sys.argv) < 4:
        sys.exit(__doc__.strip().splitlines()[3].strip())
    src = load_file(sys.argv[1])
    a, b = float(sys.argv[2]), float(sys.argv[3])
    clip = src[int(a * SR): int((a + b) * SR)]
    pn = PeakNormalizer()
    x = np.concatenate([pn.process(clip[i:i + CH])
                        for i in range(0, len(clip) - CH, CH)])
    segs = []
    seg = vad.Segmenter(lambda _b: None, lambda y: segs.append(y))
    for i in range(0, len(x), CH):
        seg.accept(x[i:i + CH])
    seg.flush()

    print(f"{len(segs)} 段\n")
    print(f"{'配置':<20}{'词数':>6}   前两段")
    for name, rec in (("greedy(现状)", make("greedy_search")),
                      ("beam(无热词)", make("modified_beam_search")),
                      ("beam+热词 2.0", make("modified_beam_search", 2.0)),
                      ("beam+热词 8.0", make("modified_beam_search", 8.0)),
                      ("beam+热词 20.0", make("modified_beam_search", 20.0))):
        ts = run(rec, segs)
        print(f"{name:<20}{sum(len(t.split()) for t in ts):>6}   "
              f"{ts[0][:40]!r} / {ts[1][:40] if len(ts) > 1 else ''!r}", flush=True)


if __name__ == "__main__":
    main()
