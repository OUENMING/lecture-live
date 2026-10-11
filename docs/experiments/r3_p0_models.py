#!/usr/bin/env python3
"""R3 P0：草稿层候选流式模型在 OCW 真值语料上的快速对比（2026-10-11）。

一次跑一个模型（独立进程 → ru_maxrss 增量不互相污染）：
  <python> docs/experiments/r3_p0_models.py --kind stream --model-dir <sherpa 模型目录> --name nem560 --out <目录>
  <python> docs/experiments/r3_p0_models.py --kind stream --model-dir <3.5 目录> --lang en --name nem35 --out <目录>
  <python> docs/experiments/r3_p0_models.py --kind parakeet --name pk --out <目录>     # 对照：1 s 节拍 10 s 滑窗
  可加 --n 20 只跑前 20 段（调接法）。

接法（对应 PLAN-streaming-asr.md §4.1 NemotronDrafter 的「每段一条流、只喂增量」）：
  · 分段直接用 `segs.json` 的 t0/t1（322 段，与云端对比同一份切分），音频用 `segs.json.norm.npy`
    （生产送 ASR 的归一化波形）。⚠️ 没有跑 `Segmenter`：段界是固定的，草稿节拍近似为
    「段内每满 1.0 s（vad.PARTIAL_INTERVAL_S）取一次」—— 生产里节拍还受「当前是否有语音」门控。
  · 流式：每段 create_stream →（3.5 版 set_option("language", …)）→ 按 0.1 s 块 accept_waveform，
    每块后 `while is_ready: decode_stream` → 每满 1 s get_result 记一份草稿 → 段尾 input_finished
    再解码到底，get_result 为该段文本。endpoint 关（分段归段表管）。
  · Parakeet 对照（生产现状）：每满 1 s 转写 buf[-10 s:]；段文本 = 整段一次转写。
回改率：直接复用 `scripts/stream_metrics.rewrite_stats`（逐段、相邻草稿之间、只比前缀），
  与 `scripts/stream_eval.evaluate` 一样把 buf > PARTIAL_MAX_S(10 s) 的草稿剔出主统计
  （那是滑窗滚出，不是改写）；流式模型没有滑窗，另报「全部草稿」口径。
计时：tick = 两次草稿之间的全部 accept+decode+get_result（≈ 生产里一次 update 的成本）；
  block = 单个 0.1 s 块后的 decode 循环（只计真的解了码的块）。RTF = 段处理墙钟总和 ÷ 音频总长。
内存：ru_maxrss（macOS 单位字节）加载前 / 加载后 / 跑完。
"""
from __future__ import annotations
import argparse, json, os, resource, sys, time

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import numpy as np
import stream_metrics as M

SR = 16000
TICK_S = 1.0          # = vad.PARTIAL_INTERVAL_S（不 import vad：只要这个常数）
BLOCK = SR // 10      # 0.1 s，与 stream_eval.replay 的回放块一致
PARTIAL_MAX_S = 10    # = main.PARTIAL_MAX_S


def rss() -> int:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss   # macOS: bytes


def load_stream(model_dir: str, threads: int):
    import sherpa_onnx
    f = lambda n: os.path.join(model_dir, n)
    for n in ("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt"):
        if not os.path.isfile(f(n)):
            raise FileNotFoundError(f(n))
    return sherpa_onnx.OnlineRecognizer.from_transducer(
        tokens=f("tokens.txt"), encoder=f("encoder.int8.onnx"), decoder=f("decoder.int8.onnx"),
        joiner=f("joiner.int8.onnx"), num_threads=threads, sample_rate=SR, feature_dim=80,
        decoding_method="greedy_search", enable_endpoint_detection=False, provider="cpu")


def run_stream_seg(rec, seg: np.ndarray, lang: str | None, tail_pad: float):
    t_seg = time.perf_counter()
    s = rec.create_stream()
    if lang:
        s.set_option("language", lang)
    drafts, ticks, blocks = [], [], []
    t_tick = time.perf_counter()
    per_tick = int(TICK_S * SR)
    for i in range(0, len(seg), BLOCK):
        s.accept_waveform(SR, seg[i:i + BLOCK])
        a = time.perf_counter(); n = 0
        while rec.is_ready(s):
            rec.decode_stream(s); n += 1
        if n:
            blocks.append(time.perf_counter() - a)
        fed = min(i + BLOCK, len(seg))
        if fed % per_tick == 0:
            txt = rec.get_result(s).strip()
            ticks.append(time.perf_counter() - t_tick)
            if txt:
                drafts.append({"buf_s": fed / SR, "text": txt})
            t_tick = time.perf_counter()
    if tail_pad > 0:
        s.accept_waveform(SR, np.zeros(int(tail_pad * SR), dtype=np.float32))
    s.input_finished()
    while rec.is_ready(s):
        rec.decode_stream(s)
    final = rec.get_result(s).strip()
    return final, drafts, ticks, blocks, time.perf_counter() - t_seg


def run_parakeet_seg(P, seg: np.ndarray):
    t_seg = time.perf_counter()
    drafts, ticks = [], []
    per_tick = int(TICK_S * SR)
    for end in range(per_tick, len(seg) + 1, per_tick):
        a = time.perf_counter()
        txt = P.transcribe(seg[max(0, end - PARTIAL_MAX_S * SR):end]).strip()
        ticks.append(time.perf_counter() - a)
        if txt:
            drafts.append({"buf_s": end / SR, "text": txt})
    a = time.perf_counter()
    final = P.transcribe(seg).strip()
    fin_t = time.perf_counter() - a
    return final, drafts, ticks, [fin_t], time.perf_counter() - t_seg


def rew(segs_drafts, only_unslid: bool) -> dict:
    shown = rw = bk = steps = 0
    for ds in segs_drafts:
        texts = [d["text"] for d in ds if (not only_unslid or d["buf_s"] <= PARTIAL_MAX_S)]
        r = M.rewrite_stats(texts)
        shown += r["shown"]; rw += r["rewritten"]; bk += r["rewritten_beyond_k"]; steps += r["steps"]
    return {"steps": steps, "shown_words": shown, "rewritten": rw, "rate": M.rate(rw, shown),
            "rate_beyond_k": M.rate(bk, shown), "k": M.DEFAULT_K}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", choices=["stream", "parakeet"], required=True)
    ap.add_argument("--model-dir", default=os.path.expanduser("~/models/parakeet-tdt-0.6b-v3-int8"))
    ap.add_argument("--name", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--data", default=os.path.expanduser("~/.classlive/eval/ocw"))
    ap.add_argument("--lang", default=None, help="3.5 多语种版：每条流 set_option('language', …)")
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--tail-pad", type=float, default=0.0, help="input_finished 前补零秒数（默认不补）")
    ap.add_argument("--n", type=int, default=0, help="只跑前 n 段（0 = 全部）")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    z = np.load(os.path.join(a.data, "segs.json.norm.npy"), mmap_mode="r")
    segs = json.load(open(os.path.join(a.data, "segs.json")))
    if a.n:
        segs = segs[:a.n]

    import sherpa_onnx
    r0 = rss(); t0 = time.perf_counter()
    if a.kind == "stream":
        model = load_stream(a.model_dir, a.threads)
    else:
        sys.path.insert(0, ROOT)
        import asr
        model = asr.load_asr(a.model_dir)
    load_s = time.perf_counter() - t0; r1 = rss()

    finals, all_drafts, ticks, blocks, seg_walls, audio = [], [], [], [], [], 0.0
    per_seg = []
    for k, sg in enumerate(segs):
        seg = np.ascontiguousarray(z[int(sg["t0"] * SR):int(sg["t1"] * SR)], dtype=np.float32)
        audio += len(seg) / SR
        if a.kind == "stream":
            fin, ds, tk, bl, w = run_stream_seg(model, seg, a.lang, a.tail_pad)
        else:
            fin, ds, tk, bl, w = run_parakeet_seg(model, seg)
        finals.append(fin); all_drafts.append(ds); ticks += tk; blocks += bl; seg_walls.append(w)
        per_seg.append({"i": k, "dur": len(seg) / SR, "wall_s": w, "final": fin,
                        "drafts": [[d["buf_s"], d["text"]] for d in ds]})
        if k % 40 == 0:
            print(f"  [{a.name}] {k}/{len(segs)} audio {audio:.0f}s", file=sys.stderr, flush=True)
    r2 = rss()

    hyp = os.path.join(a.out, f"hyp_{a.name}.txt")
    open(hyp, "w", encoding="utf-8").write(" ".join(f for f in finals if f))
    with open(os.path.join(a.out, f"segs_{a.name}.jsonl"), "w", encoding="utf-8") as f:
        for p in per_seg:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    lat = lambda v: {k: (round(x, 4) if isinstance(x, float) else x) for k, x in M.summarize_latency(v).items()}
    res = {
        "name": a.name, "kind": a.kind, "model_dir": os.path.basename(os.path.normpath(a.model_dir)),
        "sherpa_onnx": sherpa_onnx.__version__, "threads": a.threads, "lang": a.lang, "tail_pad": a.tail_pad,
        "segments": len(segs), "empty_finals": sum(1 for f in finals if not f),
        "audio_s": round(audio, 1), "load_s": round(load_s, 2),
        "rtf": round(sum(seg_walls) / audio, 4),
        "seg_wall_s": lat(seg_walls),
        "tick_s": lat(ticks), ("block_decode_s" if a.kind == "stream" else "final_transcribe_s"): lat(blocks),
        "rss_mb": {"before_load": round(r0 / 2**20), "after_load": round(r1 / 2**20),
                   "after_run": round(r2 / 2**20), "load_delta": round((r1 - r0) / 2**20),
                   "run_delta": round((r2 - r0) / 2**20)},
        "drafts": sum(len(d) for d in all_drafts),
        "drafts_buf_gt_10s": sum(1 for ds in all_drafts for d in ds if d["buf_s"] > PARTIAL_MAX_S),
        "rewrite_le10s": rew(all_drafts, True),
        "rewrite_all": rew(all_drafts, False),
        "hyp": hyp,
    }
    json.dump(res, open(os.path.join(a.out, f"res_{a.name}.json"), "w"), ensure_ascii=False, indent=2)
    print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
