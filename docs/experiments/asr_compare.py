#!/usr/bin/env python3
"""实验: 换 ASR 模型对远场课堂的转写质量有多大影响?

用法:
    ClassLive.app/Contents/MacOS/python docs/experiments/asr_compare.py <音频> <起秒> <时长秒> \\
        [--models parakeet,whisper-turbo] [--dump 6]

前置(模型不在仓库里, 自己下):
    parakeet        ~/models/parakeet-tdt-0.6b-v3-int8/   官方 sherpa-onnx 转好的 int8
    whisper-turbo   ~/models/sherpa-onnx-whisper-turbo/   564MB
        https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-whisper-turbo.tar.bz2

背景
----
前面几轮实验把碎片(段尾无终止标点、语义不成立)的成因逐个排除了:
  ❌ 不是 VAD 参数   —— 7 种配置结果完全一样(docs/experiments/vad_params.py)
  ❌ 不是降噪        —— 词数 -41%, 逐段证实删语音(denoise_ab.py)
  ❌ 不是课号错位    —— p=0.69(见会话记录)
  ❌ 不是空转写      —— 只占 3.2% 时长
剩下唯一没试的杠杆就是 **ASR 模型本身**。

指标（都已在别处建过基线, 可直接比）
------------------------------------
  词数            越多越好(在同为真实转录的前提下; 但要配合"看输出是不是胡言")
  段尾无终止标点率 越低越好 —— 与人工判定的"碎片率"相关(自动判据大约抓到 60%)
  平均 logprob    越接近 0 越好(仅 transducer 有 ys_log_probs)
  空转写率        越低越好
  耗时            是否还能实时(生产需要 ≥1× 实时)
⚠️ 词数多也可能是幻觉。**一定要看 --dump 的实际文本**, 别只看数字。
"""
import argparse
import os
import pathlib
import sys
import time

import numpy as np
import sherpa_onnx

HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2]))
import vad                                                    # noqa: E402
from capture import PeakNormalizer, SR, load_file             # noqa: E402

CH = SR // 10
TERM = tuple(".?!…")
MODELS_DIR = os.path.expanduser("~/models")


def _pick(d, *cands):
    """取第一个存在的文件。官方包同时给 int8 与非 int8, 名字随版本变, 别写死。"""
    for c in cands:
        p = os.path.join(d, c)
        if os.path.exists(p):
            return p
    raise SystemExit(f"{d} 里找不到 {cands}")


def build(kind):
    if kind == "parakeet":
        d = f"{MODELS_DIR}/parakeet-tdt-0.6b-v3-int8"
        return sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=_pick(d, "encoder.int8.onnx", "encoder.onnx"),
            decoder=_pick(d, "decoder.int8.onnx", "decoder.onnx"),
            joiner=_pick(d, "joiner.int8.onnx", "joiner.onnx"),
            tokens=f"{d}/tokens.txt", num_threads=4, model_type="nemo_transducer")
    if kind == "parakeet-fp16":
        d = f"{MODELS_DIR}/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-fp16"
        return sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=_pick(d, "encoder.fp16.onnx", "encoder.onnx"),
            decoder=_pick(d, "decoder.fp16.onnx", "decoder.onnx"),
            joiner=_pick(d, "joiner.fp16.onnx", "joiner.onnx"),
            tokens=f"{d}/tokens.txt", num_threads=4, model_type="nemo_transducer")
    if kind == "whisper-turbo":
        d = f"{MODELS_DIR}/sherpa-onnx-whisper-turbo"
        return sherpa_onnx.OfflineRecognizer.from_whisper(
            encoder=_pick(d, "turbo-encoder.int8.onnx", "turbo-encoder.onnx"),
            decoder=_pick(d, "turbo-decoder.int8.onnx", "turbo-decoder.onnx"),
            tokens=_pick(d, "turbo-tokens.txt", "tokens.txt"),
            language="en", num_threads=4)
    if kind == "canary-180m":
        # ⚠️ 已实测**否决**: 对本项目的远场课堂录音 100% 空输出(12s/30s/60s、原始/归一化/
        # ×0.3/×2 全试过), 而它在自带样例上是正常的 —— 是模型对该声学条件不适用, 不是
        # 集成问题。留着这个分支只为可复现, 别再下载它指望有提升。
        d = f"{MODELS_DIR}/canary"
        return sherpa_onnx.OfflineRecognizer.from_nemo_canary(
            encoder=_pick(d, "encoder.int8.onnx", "encoder.onnx"),
            decoder=_pick(d, "decoder.int8.onnx", "decoder.onnx"),
            tokens=f"{d}/tokens.txt", src_lang="en", tgt_lang="en", num_threads=4)
    raise SystemExit(f"未知模型 {kind}")


def decode(rec, x):
    st = rec.create_stream()
    st.accept_waveform(SR, x)
    t0 = time.time()
    rec.decode_stream(st)
    dt = time.time() - t0
    r = st.result
    lp = float(np.mean(r.ys_log_probs)) if len(getattr(r, "ys_log_probs", [])) else None
    return r.text.strip(), lp, dt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("audio")
    ap.add_argument("start", type=float)
    ap.add_argument("dur", type=float)
    ap.add_argument("--models", default="parakeet,whisper-turbo")
    ap.add_argument("--dump", type=int, default=0, help="并排打印前 N 段文本")
    a = ap.parse_args()

    kinds = [k.strip() for k in a.models.split(",") if k.strip()]
    recs = {k: build(k) for k in kinds}

    src = load_file(a.audio)
    clip = src[int(a.start * SR): int((a.start + a.dur) * SR)]
    pn = PeakNormalizer()
    audio = np.concatenate([pn.process(clip[i:i + CH]) for i in range(0, len(clip) - CH, CH)])
    got = []
    seg = vad.Segmenter(lambda _b: None, lambda _b: got.append(_b))
    for i in range(0, len(audio), CH):
        seg.accept(audio[i:i + CH])
    seg.flush()

    print(f"{pathlib.Path(a.audio).name}  {a.start:.0f}–{a.start + a.dur:.0f}s")
    print(f"VAD 固定(现状参数) → {len(got)} 段;  硬切 {seg.diag['hard_cuts']}  丢弃 {seg.diag['dropped']}\n")
    hdr = f"{'模型':<16}{'词数':>6}{'无终止':>12}{'空转写':>10}{'平均logprob':>13}{'总耗时':>9}{'实时率':>8}"
    print(hdr); print("-" * len(hdr))
    outs = {}
    for k in kinds:
        texts, lps, tot, t0 = [], [], 0.0, time.time()
        for g in got:
            t, lp, dt = decode(recs[k], g)
            texts.append(t); tot += dt
            if lp is not None:
                lps.append(lp)
        wall = time.time() - t0
        outs[k] = texts
        n = max(len(texts), 1)
        noend = sum(1 for t in texts if t and not t.rstrip().endswith(TERM))
        empty = sum(1 for t in texts if not t)
        lp_s = f"{np.mean(lps):.3f}" if lps else "n/a"
        print(f"{k:<16}{sum(len(t.split()) for t in texts):>6}"
              f"{noend:>7}({noend / n * 100:>3.0f}%){empty:>6}({empty / n * 100:>3.0f}%)"
              f"{lp_s:>13}{wall:>8.1f}s{a.dur / max(wall, 1e-9):>7.1f}×", flush=True)

    if a.dump and len(kinds) >= 2:
        print(f"\n=== 并排对照（前 {a.dump} 段）===")
        for i in range(min(a.dump, len(got))):
            print(f"\n[{i}] {len(got[i]) / SR:.1f}s")
            for k in kinds:
                print(f"  {k:<16}{outs[k][i]}")


if __name__ == "__main__":
    main()
