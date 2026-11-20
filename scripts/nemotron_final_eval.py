#!/usr/bin/env python3
"""量具：**本地定稿**到底用哪个模型 —— Parakeet 整段 vs Nemotron 整段重新解码。

    ClassLive.app/Contents/MacOS/python scripts/nemotron_final_eval.py [--limit N]
        [--segs ~/.classlive/eval/ocw] [--json out.json]

## 为什么要有它（桌面 D1 的前置条件）

2026-11-20 Owen 拍板：本地只留**一个**流式模型（Nemotron），Parakeet / Whisper 删掉。
删之前必须先回答一件事：**Nemotron 拿整段音频重新解码，能不能顶上 Parakeet 的位置？**
（定稿兜底 = 云端不通时整节课的字幕来源，差一点点可以，差一截不行。）

于是这里量两个数，语料用 `~/.classlive/eval/ocw`（322 段 OCW 真课切段 + 人工字幕）：

| 列 | 怎么跑 |
|---|---|
| Parakeet | 当年的本地定稿（2026-11-20 之前 `final_asr._local_chain` 调的就是它）。⚠️ 它已经**从代码里删掉**了，所以这个量具自带一小段 sherpa 离线解码（`_ParakeetBaseline`），只为本文件活着 |
| Nemotron | `NemotronFinal.transcribe_lp(整段)` —— 每段**新开一条 stream**，喂整段 + `TAIL_PAD_S` 静音 → `input_finished` → 取结果 |

打分走 `scripts/stream_metrics.segment_score`（与 OCW 云端选型同一把尺子，口径唯一）。
**只本地跑，不发任何请求**；点云的那两条路在 `final_asr_eval.py`。

## 判据（写死在文档里，免得事后找补）

Nemotron 的 WER **相对**比 Parakeet 差 > 30% → 停，不做后面的删除。差值在这里打出来。
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import models                                                        # noqa: E402
from stream_metrics import segment_score                              # noqa: E402

SR = 16000

#: 老 Parakeet 目录名。⚠️ 注册表里已经没有它了（`models.MODELS = (VAD, NEMOTRON)`）——
#: 这里写死是**故意的**：这个量具的全部意义就是拿它当基线，基线模型不该再进注册表。
PARAKEET_DIR = "~/models/parakeet-tdt-0.6b-v3-int8"


class _ParakeetBaseline:
    """够这个量具用的 Parakeet 整段解码（模型已从产品代码里删掉，这里留一份小的）。

    接口与 `NemotronFinal` 同形：`transcribe_lp(samples) -> (text, logprob|None)`。
    """

    def __init__(self, model_dir: str, num_threads: int = 4):
        import sherpa_onnx

        from asr import _find_file

        def f(name: str) -> str:
            return _find_file(model_dir, [name])

        self._rec = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=f("encoder.int8.onnx"), decoder=f("decoder.int8.onnx"),
            joiner=f("joiner.int8.onnx"), tokens=f("tokens.txt"),
            num_threads=num_threads, model_type="nemo_transducer")

    def transcribe_lp(self, samples):
        if len(samples) < SR // 10:
            return "", None
        stream = self._rec.create_stream()
        stream.accept_waveform(SR, samples)
        self._rec.decode_stream(stream)
        r = stream.result
        ys = getattr(r, "ys_log_probs", None)
        lp = float(np.mean(ys)) if ys is not None and len(ys) else None
        return r.text.strip(), lp


def load_corpus(segdir: pathlib.Path, limit: int):
    segs = json.loads((segdir / "segs.json").read_text(encoding="utf-8"))
    refs = json.loads((segdir / "ref.json").read_text(encoding="utf-8"))
    audio = np.load(segdir / "segs.json.norm.npy", mmap_mode="r")
    if limit:
        segs = segs[:limit]
    out = []
    for i, s in enumerate(segs):
        buf = np.asarray(audio[int(s["t0"] * SR):int(s["t1"] * SR)], dtype="float32")
        # 参考文本按**段边界重叠**取：ref 的句子与 segs 的切点不是同一套（人工字幕按句切），
        # 谁的中心落在本段里就算本段的参考 —— 与 OCW 量具同一做法（见 ref.json 的 t0/t1）。
        mid = [(r["t0"] + r["t1"]) / 2 for r in refs]
        ref = " ".join(r["text"] for r, m in zip(refs, mid) if s["t0"] <= m < s["t1"])
        out.append({"i": i, "t0": s["t0"], "t1": s["t1"], "ref": ref, "audio": buf})
    return out


def _run(name: str, engine, items) -> dict:
    hyps, lat, empty = [], [], 0
    for n, it in enumerate(items):
        t0 = time.perf_counter()
        text, _lp = engine.transcribe_lp(it["audio"])
        lat.append((time.perf_counter() - t0) * 1000)
        if (n + 1) % 10 == 0:
            print(f"  {name}: {n + 1}/{len(items)}（本段 {lat[-1]:.0f} ms）", flush=True)
        text = text.strip()
        if not text:
            empty += 1
        hyps.append(text)
    ref_all = " ".join(it["ref"] for it in items)
    sc = segment_score(ref_all, "\n".join(hyps))
    return {"name": name, "segs": len(items), "empty": empty,
            "empty_pct": round(100.0 * empty / max(1, len(items)), 2),
            "wer": sc.get("wer"), "S": sc.get("S"), "D": sc.get("D"), "I": sc.get("I"),
            "lost_words": sc.get("lost_words"), "lost_pct": sc.get("lost_pct"),
            "ms_p50": round(float(np.percentile(lat, 50)), 1),
            "ms_p95": round(float(np.percentile(lat, 95)), 1),
            "hyps": hyps}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--segs", default=str(pathlib.Path.home() / ".classlive/eval/ocw"))
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 段（默认全跑）")
    ap.add_argument("--json", default="", help="把逐段结果写到这个文件")
    a = ap.parse_args()

    segdir = pathlib.Path(a.segs).expanduser()
    if not (segdir / "segs.json").is_file():
        print(f"语料不存在：{segdir}/segs.json —— 跳过（本机没有 OCW 切段就不能量）",
              file=sys.stderr)
        return 2
    items = load_corpus(segdir, a.limit)
    print(f"语料 {segdir} · {len(items)} 段", flush=True)

    from nemotron_final import NemotronFinal

    pk_dir = os.path.expanduser(PARAKEET_DIR)
    nm_dir = models.path_of("nemotron")
    results = []
    if pathlib.Path(pk_dir).is_dir():
        results.append(_run("parakeet", _ParakeetBaseline(pk_dir), items))
    else:
        print(f"⚠ 没有 Parakeet（{pk_dir}），不跑这一列", file=sys.stderr)
    if pathlib.Path(nm_dir).is_dir():
        results.append(_run("nemotron-final", NemotronFinal(nm_dir), items))
    else:
        print(f"⚠ 没有 Nemotron（{nm_dir}），不跑这一列", file=sys.stderr)

    print("\n%-16s %6s %8s %8s %7s %7s %7s %8s %8s" %
          ("engine", "segs", "WER", "空段率", "S", "D", "I", "p50ms", "p95ms"))
    for r in results:
        print("%-16s %6d %7.1f%% %7.1f%% %7.1f%% %7.1f%% %7.1f%% %8.0f %8.0f" % (
            r["name"], r["segs"], 100 * (r["wer"] or 0), r["empty_pct"],
            100 * (r["S"] or 0), 100 * (r["D"] or 0), 100 * (r["I"] or 0), r["ms_p50"], r["ms_p95"]))
    by = {r["name"]: r for r in results}
    if "parakeet" in by and "nemotron-final" in by:
        p, n = by["parakeet"]["wer"], by["nemotron-final"]["wer"]
        if p:
            rel = (n - p) / p
            verdict = "停（>30% 更差，不删 Parakeet）" if rel > 0.30 else "可以切"
            print(f"\nNemotron 相对 Parakeet: {rel:+.1%} → {verdict}")
    if a.json:
        pathlib.Path(a.json).expanduser().write_text(
            json.dumps([{k: v for k, v in r.items() if k != "hyps"} for r in results],
                       ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
