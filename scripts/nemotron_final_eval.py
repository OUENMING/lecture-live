#!/usr/bin/env python3
"""量具：**本地定稿兜底**三列并排 —— Parakeet 整段 / Nemotron 整段重解码 / 段尾转录交接。

    ClassLive.app/Contents/MacOS/python scripts/nemotron_final_eval.py [--limit N]
        [--segs ~/.classlive/eval/ocw] [--json out.json]

## 为什么要有它

2026-11-20 Owen 拍板：本地只留**一个**流式模型（Nemotron），Parakeet / Whisper 删掉。
「云端不通时这一段的英文从哪来」于是只剩三种候选，全部要**先量后切**（项目硬规矩）：

| 列 | 怎么跑 |
|---|---|
| `parakeet` | 当年的本地定稿（D1 之前 `final_asr._local_chain` 调的就是它）。⚠️ 它已**从产品代码里删掉**，所以本文件自带一小段 sherpa 离线解码（`_ParakeetBaseline`）—— 它只是基线，不该再进注册表 |
| `nemotron-final` | **D1** 的做法：流式模型拿**整段**重新解码（每段新开一条 stream，喂整段 + `TAIL_PAD_S` 静音 → `input_finished` → 取结果）。WER 只差 2.5%，但要多一份 recognizer（+886 MB）且慢 3.4 倍 |
| `stream-tail` | ⭐ **D1b 的做法**：按上课的方式**逐块喂**（与 `draft_engine` 同块长 `SR//10`），段尾 `DraftEngine.finish()` 取那份完整假设。这一列才是生产现役的本地兜底 |

打分走 `scripts/stream_metrics.segment_score`（与 OCW 云端选型同一把尺子，口径唯一）。
**只本地跑，不发任何请求**；点云的那两条路在 `final_asr_eval.py`。

⚠️ **三列的两个"毫秒"不是一个口径**，别横着比：

- `parakeet` / `nemotron-final` 的 p50 = **这一段的全部识别墙钟**（从头解到尾）。
- `stream-tail` 的 p50 = **只有段尾那一截**（剩余样本 + 0.8 s 静音 + `finish` + 交接）。
  那一段的前缀在上课途中早就一块一块解完了 —— 而那些算力本来就要花（草稿每秒都在跑）。
  这正是 D1b 的全部意义：**段尾那一刻只剩下 76 ms 的活**，而不是重新解 1270 ms。
  要横着比，得把 `nemotron-final` 的整段墙钟和「草稿的总算力 + 段尾 76 ms」比。

## 判据（写死在文档里，免得事后找补）

- D1：Nemotron 整段解码的 WER **相对**比 Parakeet 差 > 30% → 停，不做删除。
- D1b：`stream-tail` 的 WER 不许比 `nemotron-final` **明显更差**（同一段话、同一模型，
  差别只在「逐块喂 + `finish`」vs「一次喂整段」）。差得多说明块边界把词切坏了。
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

    接口与「整段解码」那一列同形：`transcribe_lp(samples) -> (text, logprob|None)`。
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


def _run(name: str, engine, items, *, lat_name: str = "ms") -> dict:
    """整段解码那一类（Parakeet / Nemotron 整段）。`lat_name` 只是表头那个词的来源。"""
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
    return {"name": name, "segs": len(items), "empty": empty, "lat": lat_name,
            "empty_pct": round(100.0 * empty / max(1, len(items)), 2),
            "wer": sc.get("wer"), "S": sc.get("S"), "D": sc.get("D"), "I": sc.get("I"),
            "lost_words": sc.get("lost_words"), "lost_pct": sc.get("lost_pct"),
            "ms_p50": round(float(np.percentile(lat, 50)), 1),
            "ms_p95": round(float(np.percentile(lat, 95)), 1),
            "hyps": hyps}


class _StreamWhole:
    """`nemotron-final` 那一列：流式模型拿**整段**重新解码（D1 的做法，D1b 已不用）。

    每段**新开一条 stream** → 喂整段 → 补 `TAIL_PAD_S` 静音 → `input_finished` → 取结果。
    ⚠️ 这就是被 D1b 换掉的那条路（多一份 recognizer + 慢 3.4 倍），留着当对照。
    """

    def __init__(self, model_dir: str):
        import sherpa_onnx

        from asr import _find_file
        from draft_engine import TAIL_PAD_S

        def f(name: str) -> str:
            return _find_file(model_dir, [name])

        self._pad = int(TAIL_PAD_S * SR)
        self._rec = sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=f("tokens.txt"), encoder=f("encoder.int8.onnx"),
            decoder=f("decoder.int8.onnx"), joiner=f("joiner.int8.onnx"),
            num_threads=4, sample_rate=SR, feature_dim=80,
            decoding_method="greedy_search", enable_endpoint_detection=False, provider="cpu")

    def transcribe_lp(self, samples):
        if len(samples) < SR // 10:
            return "", None
        st = self._rec.create_stream()
        st.accept_waveform(SR, np.asarray(samples, dtype="float32"))
        st.accept_waveform(SR, np.zeros(self._pad, dtype="float32"))
        st.input_finished()
        while self._rec.is_ready(st):
            self._rec.decode_stream(st)
        return self._rec.get_result(st).strip(), None


#: 与 `main.partial_worker` 收到的一份缓冲同长（`Segmenter` / `_Latest` 都是这个粒度）。
CHUNK = SR // 10


def _run_tail(name: str, model_dir: str, items) -> dict:
    """⭐ D1b 那一列：**按上课的方式逐块喂**，段尾 `finish()` 取那份完整假设。

    ## 与产线的差别（要看清，别拿它当 e2e 成绩）

    - 产线里草稿线程与定稿线程是两条，段尾那份文本**早就**算好了，定稿只是去
      `SegmenterTail.take(tag)` 取（最多等 0.5 s）。这里**串行**做：先喂完这一段，
      再 `finish`，再计时 —— 所以 `tail_p50ms` 量的是「`finish` 本身 + 交接」，
      是产线上**一次**的真墙钟（`finish` 在产线也只跑一次）。
    - 草稿引擎用**真** `DraftEngine`（生产同一个类：熔断 / 待加载 / 没模型都会如实返回空）。
    - 块长 `CHUNK = SR//10` 与 `main._Latest` / `Segmenter` 一致；喂的样本与整段那两列**逐位相同**
      （所以 WER 的差别只可能来自「块边界」与「草稿中途的假设」）。
    - **`tail_ms` 只量段尾那一截**（`finish()` 开始 → 交接拿到文本）。前缀的识别在上课途中
      已经逐块做完了（那笔算力本来就要花 —— 草稿每秒都要一份），所以这不是"比整段解码快
      16 倍"，而是"**段尾只剩这么点活**"。产线上定稿那侧还会短等 ≤0.5 s（这里同步交接）。
    """
    from draft_engine import DraftEngine, NemotronDrafter, SegmentTail

    # 用真草稿引擎（生产同一个类与同一个 drafter）：`NemotronDrafter` 自己建 recognizer。
    eng = DraftEngine(NemotronDrafter(model_dir))
    if eng.name != "nemotron":
        raise SystemExit(f"没有可用的草稿引擎（{eng.reason or '流式模型未下载'}）")
    tails = SegmentTail(wait_s=0.0)          # 量具里同步交接，不短等（产线才等）
    hyps, lat, empty = [], [], 0
    for n, it in enumerate(items):
        audio = it["audio"]
        tag = n
        # ⚠️ `transcribe` 收的是「本段**从段首累积**的样本」（生产里就是 `Segmenter._buf()`
        #    那个形状），引擎自己只把**新增**的那截喂进流。所以每一份都把前缀再传一遍是对的。
        for i in range(CHUNK, len(audio) + 1, CHUNK):
            eng.transcribe(audio[:i], tag)
        t0 = time.perf_counter()
        text = eng.finish(audio, tag)                # 段尾：剩余样本 + 静音 + input_finished
        tails.put(tag, text)
        got = tails.take(tag) or ""
        lat.append((time.perf_counter() - t0) * 1000)
        if (n + 1) % 10 == 0:
            print(f"  {name}: {n + 1}/{len(items)}（本段段尾 {lat[-1]:.0f} ms）", flush=True)
        got = got.strip()
        if not got:
            empty += 1
        hyps.append(got)
    ref_all = " ".join(it["ref"] for it in items)
    sc = segment_score(ref_all, "\n".join(hyps))
    return {"name": name, "segs": len(items), "empty": empty, "lat": "tail",
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

    pk_dir = os.path.expanduser(PARAKEET_DIR)
    nm_dir = models.path_of("nemotron")
    results = []
    if pathlib.Path(pk_dir).is_dir():
        results.append(_run("parakeet", _ParakeetBaseline(pk_dir), items))
    else:
        print(f"⚠ 没有 Parakeet（{pk_dir}），不跑这一列", file=sys.stderr)
    if pathlib.Path(nm_dir).is_dir():
        results.append(_run("nemotron-final", _StreamWhole(nm_dir), items))
        results.append(_run_tail("stream-tail", nm_dir, items))
    else:
        print(f"⚠ 没有 Nemotron（{nm_dir}），不跑这两列", file=sys.stderr)

    print("\n%-16s %6s %8s %8s %7s %7s %7s %6s %9s %9s" %
          ("engine", "segs", "WER", "空段率", "S", "D", "I", "单位", "p50", "p95"))
    for r in results:
        unit = "段尾ms" if r.get("lat") == "tail" else "ms"
        print("%-16s %6d %7.1f%% %7.1f%% %7.1f%% %7.1f%% %7.1f%% %6s %9.0f %9.0f" % (
            r["name"], r["segs"], 100 * (r["wer"] or 0), r["empty_pct"],
            100 * (r["S"] or 0), 100 * (r["D"] or 0), 100 * (r["I"] or 0),
            unit, r["ms_p50"], r["ms_p95"]))
    by = {r["name"]: r for r in results}
    if "parakeet" in by and "nemotron-final" in by:
        p, n = by["parakeet"]["wer"], by["nemotron-final"]["wer"]
        if p:
            rel = (n - p) / p
            verdict = "停（>30% 更差，不删 Parakeet）" if rel > 0.30 else "可以切"
            print(f"\nNemotron 整段重解码相对 Parakeet: {rel:+.1%} → {verdict}")
    if "nemotron-final" in by and "stream-tail" in by:
        w, t = by["nemotron-final"]["wer"], by["stream-tail"]["wer"]
        if w:
            rel = (t - w) / w
            note = "块边界没切坏" if rel <= 0.10 else "⚠ 逐块喂更差 —— 块边界在切词"
            print(f"段尾转录相对整段重解码: {rel:+.1%} → {note}")
    if a.json:
        pathlib.Path(a.json).expanduser().write_text(
            json.dumps([{k: v for k, v in r.items() if k != "hyps"} for r in results],
                       ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
