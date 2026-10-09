#!/usr/bin/env python3
"""流式字幕量具：把音频按真实流水线的分段逻辑回放一遍，量 草稿稳定度 / 延迟 / 错词率。

用法（用 App 自带的 python，从仓库根目录）：
  ClassLive.app/Contents/MacOS/python scripts/stream_eval.py <音频> [--ref ref.txt]
      [--start S] [--dur D] [--final whisper|parakeet] [--jsonl out.jsonl] [--json out.json]

回放方式（与生产的差别要看清）：
  · 复用生产的 `Segmenter`（VAD 切句 + 每 1s 一次 on_partial）和 `PeakNormalizer`；
  · 草稿 = Parakeet 转写最近 PARTIAL_MAX_S 秒，定稿 = 整句转写（默认 Whisper，同生产）；
  · **同步回放、不排队**：生产里草稿线程与定稿线程抢同一把 ASR 锁，会有排队延迟，
    这里没有 → 报告的延迟是**下限**。"计算量"是真实墙钟，"音频时刻"是回放时钟（借 SimulStreaming
    的 computation-aware / unaware 两种记法）。
  · 翻译不在回放里（量的是 ASR 这一层）；草稿翻译的触发策略另有 --draft-policy 回放（见下）。

输出：每次草稿/定稿一行 JSONL（emission_time 风格），以及汇总指标。
指标定义见 `stream_metrics.py` 文件头。
"""
from __future__ import annotations
import argparse, json, os, sys, time

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import stream_metrics as M

PARTIAL_MAX_S = 10        # 与 main.PARTIAL_MAX_S 一致（不 import main：它拖一堆 UI 依赖）


def replay(audio: np.ndarray, asr_draft, asr_final, *, draft_filter=None, progress=None):
    """回放，返回 utterances 列表。
    draft_filter(prev_shown:str, new:str) -> str：草稿显示层策略（默认直通 = 生产现状），
    用来离线对比 "LCP+hold-back" 之类的改动，不必改生产代码。"""
    from capture import PeakNormalizer, SR
    from vad import Segmenter
    import vad
    clock = {"t": 0.0}
    cur = {"drafts": []}

    class _VirtualTime:          # Segmenter 用 time.monotonic() 判 1s 一次的草稿节拍
        @staticmethod            # 不换成回放时钟的话，快于实时的回放几乎触发不了草稿
        def monotonic():
            return clock["t"]
    real_time = vad.time
    vad.time = _VirtualTime
    utts: list[dict] = []

    def on_partial(buf):
        t0 = time.perf_counter()
        txt = asr_draft.transcribe(buf[-PARTIAL_MAX_S * SR:])
        dt = time.perf_counter() - t0
        if not txt:
            return
        shown_prev = cur["drafts"][-1]["shown"] if cur["drafts"] else ""
        shown = draft_filter(shown_prev, txt) if draft_filter else txt
        cur["drafts"].append({"audio_t": clock["t"], "compute_s": dt, "raw": txt, "shown": shown,
                              "buf_s": len(buf) / SR})

    def on_end(buf):
        t0 = time.perf_counter()
        txt = asr_final.transcribe(buf)
        dt = time.perf_counter() - t0
        dur = len(buf) / SR
        utts.append({"start_t": clock["t"] - dur, "end_t": clock["t"], "dur": dur,
                     "final": txt, "final_compute_s": dt, "drafts": cur["drafts"]})
        cur["drafts"] = []
        if hasattr(draft_filter, "reset"):
            draft_filter.reset()                 # 定稿一到，冻结前缀作废

    try:
        seg = Segmenter(on_partial, on_end)
        norm = PeakNormalizer()
        CH = SR // 10
        for i in range(0, len(audio), CH):
            clock["t"] = (i + CH) / SR
            seg.accept(norm.process(audio[i:i + CH]))
            if progress and (i // CH) % 600 == 0:
                progress(clock["t"])
        seg.flush()
    finally:
        vad.time = real_time
    return utts


def evaluate(utts, ref_text: str | None, k: int = 2) -> dict:
    n_draft = sum(len(u["drafts"]) for u in utts)
    # 回改：每句内部的相邻草稿（用"显示出来的"文本，而非 ASR 原始输出）
    # ⚠️ 缓冲超过 PARTIAL_MAX_S 后草稿窗口在滑动（开头的词滚出窗口，不是"改写"），
    #    这些草稿不进回改统计，单独数个数 —— 否则长句会把回改率虚高一大截。
    sliding = sum(1 for u in utts for d in u["drafts"] if d["buf_s"] > PARTIAL_MAX_S)
    shown = rew = beyond = 0
    for u in utts:
        s = M.rewrite_stats([d["shown"] for d in u["drafts"] if d["buf_s"] <= PARTIAL_MAX_S], k=k)
        shown += s["shown"]; rew += s["rewritten"]; beyond += s["rewritten_beyond_k"]
    # 草稿 → 定稿的跳动
    fr_edits = fr_len = 0
    for u in utts:
        if u["drafts"] and u["final"]:
            r = M.final_rewrite(u["drafts"][-1]["shown"], u["final"])
            if r["edits"] is not None:
                fr_edits += r["edits"]; fr_len += r["draft_len"]
    # ⚠️ 两处 `is not None` 是给**现场采集**（`livemetrics.py` 写的 `<会话>.live.jsonl`）
    #    留的口子：那份没有音频时刻、也没有定稿耗时 → 这几项**留空**（`n=0`），
    #    而不是拿 0 冒充。回放那条路字段齐全，数字与以前逐位一致。
    first_vis = [u["drafts"][0]["audio_t"] - u["start_t"] + u["drafts"][0]["compute_s"]
                 for u in utts
                 if u["drafts"] and u["drafts"][0]["audio_t"] is not None]
    final_comp = [u["final_compute_s"] for u in utts if u["final_compute_s"] is not None]
    rtf_final = [u["final_compute_s"] / u["dur"] for u in utts
                 if u["dur"] > 0 and u["final_compute_s"] is not None]
    out = {
        "utterances": len(utts), "drafts": n_draft,
        "audio_s": round(sum(u["dur"] for u in utts), 1),
        "draft_rewrite": {"k": k, "sliding_drafts_excluded": sliding, "shown_words": shown, "rewritten": rew,
                          "rate": rew / shown if shown else None,
                          "rewritten_beyond_k": beyond,
                          "rate_beyond_k": beyond / shown if shown else None},
        "draft_to_final": {"edits": fr_edits, "draft_words": fr_len,
                           "rate": fr_edits / fr_len if fr_len else None},
        "latency_first_visible_s": M.summarize_latency(first_vis),
        "latency_final_compute_s": M.summarize_latency(final_comp),
        "rtf_final": M.summarize_latency(rtf_final),
    }
    hyp = " ".join(u["final"] for u in utts if u["final"])
    if ref_text:
        w = M.wer(ref_text, hyp)
        out["wer_vs_reference"] = w
    # 无参考时给"草稿与定稿的分歧"，明确不是对真值的 WER
    last_drafts = " ".join(u["drafts"][-1]["shown"] for u in utts if u["drafts"])
    if last_drafts and hyp:
        out["draft_vs_final_disagreement"] = M.wer(hyp, last_drafts)["wer"]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("audio")
    ap.add_argument("--ref")
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--dur", type=float, default=0.0, help="0 = 到结尾")
    ap.add_argument("--final", choices=["whisper", "parakeet"], default="whisper")
    ap.add_argument("--model-dir", default=os.path.expanduser("~/models/parakeet-tdt-0.6b-v3-int8"))
    ap.add_argument("--final-model-dir", default=os.path.expanduser("~/models/sherpa-onnx-whisper-turbo"))
    ap.add_argument("--k", type=int, default=2, help="hold-back 词数（只影响 beyond_k 指标）")
    ap.add_argument("--stabilize", type=int, default=None, metavar="K",
                    help="草稿显示层用 draft_stable（前缀冻结，灰尾 K 词）；不给 = 生产现状（直通）")
    ap.add_argument("--jsonl")
    ap.add_argument("--json")
    a = ap.parse_args()

    from capture import load_file, SR
    from asr import load_asr, load_final_asr
    audio = load_file(a.audio)
    s = int(a.start * SR)
    audio = audio[s:s + int(a.dur * SR)] if a.dur else audio[s:]
    asr_d = load_asr(a.model_dir)
    asr_f = load_final_asr(a.final_model_dir) if a.final == "whisper" else asr_d
    t0 = time.time()
    flt = None
    if a.stabilize is not None:
        from draft_stable import DraftStabilizer
        flt = DraftStabilizer(a.stabilize)
    utts = replay(audio, asr_d, asr_f, draft_filter=flt,
                  progress=lambda t: print(f"  … 回放到 {t:.0f}s", file=sys.stderr, flush=True))
    ref = open(a.ref, encoding="utf-8").read() if a.ref else None
    res = evaluate(utts, ref, k=a.k)
    res["wall_s"] = round(time.time() - t0, 1)
    res["audio_file"] = os.path.basename(a.audio)
    res["final_engine"] = a.final
    res["stabilize_k"] = a.stabilize
    if a.jsonl:
        with open(a.jsonl, "w", encoding="utf-8") as f:
            for i, u in enumerate(utts):
                for d in u["drafts"]:
                    f.write(json.dumps({"utt": i, "kind": "draft", "audio_t": round(d["audio_t"], 2),
                                        "compute_s": round(d["compute_s"], 3), "text": d["shown"],
                                        # ⭐ `raw` = 冻结**之前**的模型原始假设（2026-10-09 加）。
                                        #    有了它，`class_report.py` 能把**同一份回放**离线
                                        #    跑成 hold-back 0/1/2/3 的曲线（不必重跑 ASR），
                                        #    也能和生产的 `<会话>.live.jsonl` 用同一套分析。
                                        "raw": d["raw"], "buf_s": round(d["buf_s"], 3)},
                                       ensure_ascii=False) + "\n")
                f.write(json.dumps({"utt": i, "kind": "final", "audio_t": round(u["end_t"], 2),
                                    "compute_s": round(u["final_compute_s"], 3), "text": u["final"]},
                                   ensure_ascii=False) + "\n")
    if a.json:
        json.dump(res, open(a.json, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
