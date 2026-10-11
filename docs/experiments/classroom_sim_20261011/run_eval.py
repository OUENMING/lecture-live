#!/usr/bin/env python3
"""把 `make_classroom_sim.py` 合成出来的课堂语料逐份喂进流式量具，出「噪声/座位 → 字幕质量」的表。

量的就是**生产那两层**（装配全在 `scripts/stream_eval.py`，这里不重复接线）：
  草稿 = 生产那套 `draft_engine`（auto → **Nemotron 流式**，缺模型/内存不够/熔断才退 Parakeet 滑窗）；
  定稿 = `whisper`（本地兜底）/ `parakeet` / **`cloud`（生产那条云端链：Worker → Groq/千问）**。
所以表里的 WER 是「这条流水线会看到的定稿」的 WER，不是某个模型的离线批处理成绩。
⚠️ 跑 `--final cloud` 会真发请求、真花额度：Worker 限 **20 请求/分钟**，必须配 `--pace-rpm`
（默认 18）节流 —— 不节流会被打成 429 → 熔断 → 后面全走本地，那份 WER 就不是云端的了。

WER / 漏段的定义**不在这里重写** —— 用 `scripts/stream_metrics.segment_score`（与
`cloud_asr_20261010/ocw_eval.py` 同一把尺子）。**模型路径也不在这里抄** —— 走 `models.path_of`。

用法（仓库根，App 自带 python）：
    ClassLive.app/Contents/MacOS/python docs/experiments/classroom_sim_20261011/run_eval.py \
        [--only hall seminar_src_raw] [--final whisper|parakeet] [--tag hall]
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

CORPUS = pathlib.Path(os.path.expanduser("~/.classlive/eval/classroom_sim"))

#: 表格的列**只在这里定义一次**（键、表头、格式）。加一列改这一处，其余三处（row 字典、
#: 表头、格式串）原来各写一遍，加列必漏一处。
COLS: list[tuple[str, str, str]] = [
    ("name", "语料", "{}"),
    ("scene", "场景", "{}"),
    ("position", "座位", "{}"),
    ("level", "档", "{}"),
    ("snr_total_db", "有效 SNR (dB)", "{:.1f}"),
    ("rir", "RIR", "{}"),
    ("rir_rt60", "RT60 (s)", "{}"),
    ("rir_drr_db", "DRR (dB)", "{:+.1f}"),
    ("rir_dist_m", "距离 (m)", "{}"),
    ("wer", "WER", "{:.1%}"),
    ("lost_pct", "漏段", "{:.1%}"),
    ("draft_rewrite_rate", "草稿回改", "{:.1%}"),
    ("draft_disagreement", "草稿≠定稿", "{:.1%}"),
    ("latency_first_visible_p50", "首字延迟 p50 (s)", "{:.2f}"),
    ("ref_words", "词数", "{}"),
    # ⚠️ 分母必须跟着速率走：0 dB 档 VAD 判不出段时，"回改率 0.5%" 是在**很少的草稿**上算出来的，
    #    单看速率会读成"表现很好"。段数 / 草稿份数 / 显示词数三列就是给这些速率兜底的。
    ("utterances", "段数", "{}"),
    ("drafts", "草稿份数", "{}"),
    ("draft_shown_words", "草稿显示词", "{}"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default=str(CORPUS))
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--out", default=str(pathlib.Path(__file__).parent / "results"))
    ap.add_argument("--only", nargs="*", help="只跑名字里含这些子串的（默认全跑）")
    ap.add_argument("--final", choices=["whisper", "parakeet", "cloud"], default="whisper",
                    help="cloud = 生产那条云端定稿链（Worker → Groq/千问）；whisper/parakeet = 本地兜底件")
    ap.add_argument("--draft", choices=["auto", "parakeet", "nemotron"], default="auto",
                    help="草稿引擎，与生产同一个开关（默认 auto = 生产行为）")
    ap.add_argument("--model-dir", default=None, help="草稿模型目录（默认走 models 注册表）")
    ap.add_argument("--final-model-dir", default=None, help="定稿模型目录（默认走 models 注册表）")
    ap.add_argument("--tag", default="", help="结果文件名后缀（方便对比两次跑）")
    ap.add_argument("--pace-rpm", type=float, default=18.0,
                    help="云端定稿节流：每分钟最多几次请求（Worker 对 Groq 免费档限 20/分）")
    a = ap.parse_args()

    import models
    import stream_eval
    from asr import load_asr
    from capture import load_file
    from stream_metrics import segment_score

    corpus = pathlib.Path(a.corpus).expanduser()
    manifest = json.loads(pathlib.Path(a.manifest or corpus / "mix" / "manifest.json").read_text(encoding="utf-8"))
    if a.only:
        manifest = [m for m in manifest if any(k in m["name"] for k in a.only)]
    if not manifest:
        print("没有一条匹配", file=sys.stderr)
        return 2

    draft_dir = a.model_dir or models.path_of("parakeet")
    final_dir = a.final_model_dir or models.path_of("whisper")
    print(f"载模型…（草稿 {pathlib.Path(draft_dir).name} / 定稿 {a.final}）", flush=True)
    asr_d = load_asr(draft_dir)
    drafter = stream_eval.load_drafter(a.draft, asr_d)   # 与量具同一处装配
    print(f"草稿引擎：{drafter.name}" + (f"（{drafter.reason}）" if drafter.reason else ""), flush=True)
    # 定稿那层的装配全在量具里（`stream_eval.load_final_engine`），这里不重复接线
    asr_f = stream_eval.load_final_engine(a.final, final_dir, asr_d)

    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    pace = stream_eval.pace_for(a.final, a.pace_rpm)    # 与量具同一处换算，循环外算一次
    rows = []
    for m in manifest:
        t0 = time.time()
        audio = load_file(str(corpus / "mix" / m["file"]))
        pace = stream_eval.pace_for(a.final, a.pace_rpm)
        utts = stream_eval.replay(audio, drafter, asr_f, pace_s=pace)
        # ⚠️ ref 传 None：`evaluate` 自己那份 `wer_vs_reference`（逐词 Levenshtein）与我们
        #    报的 `segment_score`（difflib 块级 + 漏段）**不是同一个数**。同一份表里放两个
        #    "WER" 一定会被读错，所以只留一个、并说清是哪个。
        res = stream_eval.evaluate(utts, None)
        hyp = " ".join(u["final"] for u in utts if u["final"])
        sc = segment_score((corpus / "mix" / m["ref"]).read_text(encoding="utf-8"), hyp)
        row = {**m, "bursts": len(m["bursts"])}
        row.update({
            "wer": sc["wer"], "sub": sc["S"], "del": sc["D"], "ins": sc["I"],
            "lost_runs": sc["lost_runs"], "lost_words": sc["lost_words"],
            "lost_pct": sc["lost_pct"], "ref_words": sc["n"],
            "draft_rewrite_rate": res["draft_rewrite"]["rate"],
            "draft_shown_words": res["draft_rewrite"]["shown_words"],
            "drafts": res["drafts"],
            "draft_disagreement": res.get("draft_vs_final_disagreement"),
            "draft_engine": drafter.name,
            # 云端那一趟：每段走了哪条路 + **真正发出去的音频秒数**（不是文件时长）
            "final_sources": res.get("final_sources") or None,
            # ⚠️ 发出去的音频秒数 = **各段 dur 之和**（不是文件时长）：静音不上传
            "cloud_audio_s": (round(res["audio_s"], 1) if a.final == "cloud" else None),
            "latency_first_visible_p50": res["latency_first_visible_s"]["p50"],
            "final_compute_p50": res["latency_final_compute_s"]["p50"],
            "utterances": res["utterances"], "final_engine": a.final,
            "wall_s": round(time.time() - t0, 1),
        })
        rows.append(row)
        # ⚠️ 参考稿归一化后为空时 `segment_score` 返回 None（不是 0）—— 打印与打表都要能接住，
        #    否则一个空参考稿就把整轮跑挂掉（审计指出：这里原先漏了，只改了 ocw_eval 那一个调用方）
        # ⚠️ 云端那趟要当场判"这份到底是不是云端跑的"：本地兜底占比高 = 被限流污染了，
        #    那种数字不能当云端的成绩用（宁可吵，也别静默产出一份看着正常的坏数据）。
        if a.final == "cloud":
            srcs = row["final_sources"] or {}
            # ⚠️ 反过来数**本地兜底**：真实来源是 `groq` / `qwen` / `cloud` / `local-*` / `none`
            #    （`final_asr.py` 的三处 return），照白名单写会漏掉 `qwen` → 把千问那几段
            #    误判成"没走云端"、凭空报污染。数"本地"不用维护名单。
            local = sum(v for k, v in srcs.items() if k.startswith("local") or k == "none")
            tot = sum(srcs.values()) or 1
            if local / tot > 0.1:
                print(f"  ⚠️ {row['name']}：有 {local}/{tot} 段走了本地兜底"
                      f" —— 这份的 WER **不能**当云端成绩读", flush=True)
        wer = "  n/a" if row["wer"] is None else f"{row['wer']:5.1%}"
        lost = "  n/a" if row["lost_pct"] is None else f"{row['lost_pct']:5.1%}"
        print(f"{row['name']:26s} WER {wer} · 漏 {lost} · "
              f"回改 {row['draft_rewrite_rate'] or 0:5.1%} · 有效 SNR {row['snr_total_db']:6.1f} dB · "
              f"{row['wall_s']:5.1f}s", flush=True)

    tag = f"_{a.tag}" if a.tag else ""
    (out / f"per_file{tag}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    fmt = lambda v, f: "" if v is None else (f.format(v) if not isinstance(v, str) else v)  # noqa: E731
    md = ["| " + " | ".join(h for _, h, _ in COLS) + " |",
          "|" + "---|" * len(COLS)]
    for r in sorted(rows, key=lambda r: (r["scene"], str(r["position"]), str(r["level"]))):
        md.append("| " + " | ".join(fmt(r.get(k), f) for k, _, f in COLS) + " |")
    (out / f"RESULTS{tag}.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"\n{len(rows)} 行 → {out}/per_file{tag}.json · RESULTS{tag}.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
