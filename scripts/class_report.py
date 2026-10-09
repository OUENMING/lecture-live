#!/usr/bin/env python3
"""把一次现场的现场采集（`<会话>.live.jsonl`）或一次离线回放（`stream_eval --jsonl`）
变成一张表：**草稿稳定度 / 延迟 / 腰斩臆造候选**。

用法（用 App 自带的 python，从仓库根目录）：
  ClassLive.app/Contents/MacOS/python scripts/class_report.py <events.jsonl> [--json out.json]

## 为什么它是一份而不是两份

生产的 `livemetrics.py` 与离线的 `stream_eval.py --jsonl` **写的是同款字段**
（`utt/kind/text/raw/buf_s/compute_s[/audio_t]`），所以这一份分析同时读两种来源：

| 来源 | 回答的问题 | 不能回答的 |
|---|---|---|
| 现场 `.live.jsonl` | **生产真值**：用户实际看见的草稿有多不稳 | 延迟（没有音频时刻）· WER（没有参考稿） |
| 离线 `eval.jsonl` | 有参考稿的 WER·延迟·**任意 K 的曲线** | 不排队 → 延迟是下限 |

## 同场 A/B（`--ks`）

因为两个字段都记了（`raw` = 模型原始假设，`text` = 上屏的那份），所以**一次真课**
就能同时算出「基线回改率」与「hold-back K 之后」的 —— 不必跑两节课、不必重跑 ASR。
离线回放同理：一份 `raw` 就够了，`--ks 0,1,2,3` 出整条曲线。

## 指标定义

一律复用 `stream_eval.evaluate()` + `stream_metrics.py`（**不是**在这里重写一份），
所以现场的数和离线基线的数是**同一把尺子**量出来的。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stream_metrics as M          # noqa: E402
import stream_eval as SE            # noqa: E402


# ---------------------------------------------------------------- 读事件
def load_events(path) -> list[dict]:
    """读 `.jsonl`：一行一个事件。**空行与坏行跳过** —— 采集端崩过一行不该毁掉整份。"""
    out: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict) and "kind" in rec:
                out.append(rec)
    return out


def build_utts(events) -> list[dict]:
    """事件流 → `stream_eval.evaluate()` 吃的那种 utt 列表。

    ## 分组键：`seg`（现场）或 `utt`（离线回放）——**不是**"一句定稿"

    ⚠️⚠️ **一段 VAD 会被 `split_sentences` 切成多句定稿**，所以
       「草稿 : 定稿」在生产里**不是 1:1**。2026-10-09 实测：按定稿分组时
       32 句里 14 句只分到 0–1 份草稿，`草稿→定稿` 从 10.7% 跳到 **93.3%** ——
       那是**分组错了**，不是模型退化了。所以现场那份记 `seg`（= `main.final_gen["n"]`，
       一段缓冲一次草稿会话），一段的定稿**拼起来**当这一组的定稿文本。
       离线回放那份 `utt` 本来就是按句分的，直接用。

    ⚠️ 已知误差（现场）：定稿刚上屏那一瞬间，**下一段的**草稿可能已经在队列里
       —— 但那不要紧：`seg` 是产出方（草稿线程）自己读的世代号，与 drain 的顺序无关。
    """
    use_seg = any("seg" in e for e in events if e.get("kind") in ("draft", "final"))
    keyf = (lambda r: r.get("seg")) if use_seg else (lambda r: r.get("utt"))
    utts: dict = {}
    order: list = []
    cur = 0
    for rec in events:
        k = rec.get("kind")
        if k not in ("draft", "final"):
            continue
        kk = keyf(rec)
        if kk is None:
            kk = cur
        u = utts.get(kk)
        if u is None:
            u = utts[kk] = {"drafts": [], "final": "", "final_compute_s": None,
                            "dur": None, "_t": rec.get("t")}
            order.append(kk)
        if k == "draft":
            t = rec.get("t", 0.0)
            u["drafts"].append({
                "audio_t": rec.get("audio_t"),          # 离线有；现场没有（None）
                "compute_s": float(rec.get("compute_s") or 0.0),
                "raw": rec.get("raw", rec.get("text", "")),
                "shown": rec.get("text", ""),
                "buf_s": float(rec.get("buf_s") or 0.0),
                "t": t,
            })
        else:
            # ⚠️ 一段的**多句**定稿拼起来（只有一段一句时才等于那一句）
            u["final"] = (u["final"] + " " + (rec.get("text") or "")).strip()
            if rec.get("compute_s") is not None:
                u["final_compute_s"] = float(rec["compute_s"])
            cur = kk + 1
    out = []
    for kk in order:
        u = utts[kk]
        # 现场没有音频时刻 → 用事件自己的单调秒当代理（延迟那几项会被跳过，见 evaluate）
        for d in u["drafts"]:
            if d["audio_t"] is None:
                d["audio_t"] = d["t"]
        if u["drafts"]:
            u["start_t"] = u["drafts"][0]["audio_t"]
            u["end_t"] = u["drafts"][-1]["audio_t"]
            u["dur"] = u["end_t"] - u["start_t"]
        else:
            u["start_t"] = u["end_t"] = 0.0
            u["dur"] = 0.0
        out.append(u)
    return out


# ---------------------------------------------------------------- 离线套冻结
def apply_stabilizer(utts, holdback: int):
    """把 `raw` 流离线跑一遍 `DraftStabilizer`，返回**新的** utt 列表（`shown` 被换掉）。

    ⚠️ 每句边界要 `reset()` —— 与生产里「定稿一到就作废冻结前缀」是同一件事
       （`main.partial_worker` 的 `stab_gen` 那一支）。
    """
    from draft_stable import DraftStabilizer
    out = []
    for u in utts:
        st = DraftStabilizer(holdback)
        u2 = dict(u)
        u2["drafts"] = []
        for d in u["drafts"]:
            d2 = dict(d)
            d2["shown"] = st.update(d["raw"])
            u2["drafts"].append(d2)
        out.append(u2)
    return out


#: 「beyond-k」用的 k —— **必须与 `stream_eval --k` 的默认（2）一致**，
#: 否则这里的数字和 `docs/RESEARCH-stream-eval.md` 里那张表**不是同一把尺子**。
ANALYZE_K = 2


def sweep(utts, ref_text, ks) -> dict:
    """对一组 K 各算一次（`None` = 不冻结，用事件里已有的 `shown`）。

    ⚠️ 「冻结的 K」与「beyond-k 判据的 k」是**两件事**：前者改上屏文本，
       后者只影响"灰尾吸收后还看得见多少"这个统计量。这里前者随 `ks` 变，
       后者**钉死**在 `ANALYZE_K`。
    """
    out = {}
    for k in ks:
        u = apply_stabilizer(utts, k) if k is not None else utts
        out["baseline(raw)" if k is None else f"holdback={k}"] = SE.evaluate(
            u, ref_text, k=ANALYZE_K)
    return out


# ---------------------------------------------------------------- 多节课汇总
def pool_sweeps(res_list) -> dict:
    """把几份报告**按计数相加**再算比率 —— **不是**对比率取平均。

    ⚠️ 为什么必须这样：每一节的句子数/词数差很多（3 分钟切片 vs 50 分钟整节），
       对比率取平均等于让最短那节和最长那节一样重。开源同类项目（WhisperLiveKit 的
       `benchmark/`）也是先累加计数、最后才出比率。
    ⚠️ 只汇总**同一个设置名**都在的文件（`baseline(raw)` / `holdback=K`）。
    """
    names: list[str] = []
    for r in res_list:
        for n in r["sweep"]:
            if n not in names:
                names.append(n)
    out: dict = {}
    meta = {"sources": [r["meta"]["source"] for r in res_list],
            "utterances": sum(r["meta"]["utterances"] for r in res_list),
            "drafts": sum(r["meta"]["drafts"] for r in res_list),
            "sliding_excluded": sum(r["meta"]["sliding_excluded"] for r in res_list)}
    for name in names:
        got = [r["sweep"][name] for r in res_list if name in r["sweep"]]
        dr = {"k": ANALYZE_K, "sliding_drafts_excluded": sum(x["draft_rewrite"]["sliding_drafts_excluded"] for x in got),
              "shown_words": sum(x["draft_rewrite"]["shown_words"] for x in got),
              "rewritten": sum(x["draft_rewrite"]["rewritten"] for x in got),
              "rewritten_beyond_k": sum(x["draft_rewrite"]["rewritten_beyond_k"] for x in got)}
        dr["rate"] = dr["rewritten"] / dr["shown_words"] if dr["shown_words"] else None
        dr["rate_beyond_k"] = (dr["rewritten_beyond_k"] / dr["shown_words"]
                               if dr["shown_words"] else None)
        f_edits = sum(x["draft_to_final"]["edits"] for x in got)
        f_words = sum(x["draft_to_final"]["draft_words"] for x in got)
        out[name] = {"draft_rewrite": dr,
                     "draft_to_final": {"edits": f_edits, "draft_words": f_words,
                                        "rate": f_edits / f_words if f_words else None},
                     "latency_first_visible_s": {"n": 0}, "latency_final_compute_s": {"n": 0}}
    return {"meta": meta, "sweep": out, "pooled": True, "warnings": []}


# ---------------------------------------------------------------- 腰斩臆造候选
def _norm_seq(text: str) -> list[str]:
    return M.normalize(text or "")


def beyond_draft(final_words, draft_words) -> list[str]:
    """`final_words` 里**超出最后一份草稿覆盖范围**的那一段。

    判据是**贪婪子序列对齐**：把草稿的词按顺序在定稿里逐个对上，取**最后一个对上
    的位置**；那之后的定稿词就是"草稿没说过、定稿却说了"的尾巴。

    ⚠️ 为什么不用"某段连续文本在草稿里找不到"：单个常见词到处都有，短句永远"找得到"
       —— 那种判据会把**真正的**臆造（`the quantity is less.`）漏过去，却把
       正常的措辞差异报出来。最早的版本就是栽在这一点上（判据实测：0 条真命中）。
    """
    j = 0
    last = -1
    for i, w in enumerate(final_words):
        if j < len(draft_words) and w == draft_words[j]:
            last = i
            j += 1
    return final_words[last + 1:]


def hallucination_candidates(utts, *, min_tail: int = 3, max_tail: int = 12) -> list[dict]:
    """「VAD 腰斩半句 → Whisper 给前半句臆造续写」的**候选**清单。

    ⚠️⚠️ 这是**候选**，不是判决：Parakeet（草稿）与 Whisper（定稿）是两个模型，
       正常差异也会落在尾巴里。要人耳听 —— 录音就在 `<会话名>.001.opus` 里，
       `audio_t` 那一列给了每句的落点（`stream_eval --jsonl` 有；现场那份没有）。

    `next_head_repeats` 是**额外的**信号：下一句定稿的开头正是本句尾巴的前几个词
    —— 2026-10-08 实测到的形态（utt1 定稿 `…If demand is elastic, the quantity is less.`，
       而下一句才是真正的 `quantity falls by more than 10%.`）。
    """
    seqs = [_norm_seq(u["final"]) for u in utts]
    draft_seqs = [[_norm_seq(d["shown"]) for d in u["drafts"]] for u in utts]
    out = []
    for i, u in enumerate(utts):
        fin = seqs[i]
        if len(fin) < min_tail or not u["drafts"]:
            continue
        tail = beyond_draft(fin, draft_seqs[i][-1])
        if not (min_tail <= len(tail) <= max_tail):
            continue
        nxt = seqs[i + 1] if i + 1 < len(seqs) else []
        out.append({
            "utt": i,
            "final": u["final"],
            "last_draft": u["drafts"][-1]["shown"],
            "unsupported_tail": " ".join(tail),
            "tail_words": len(tail),
            "next_head_repeats": bool(nxt) and nxt[:min(2, len(tail))] == tail[:min(2, len(tail))],
            "next_final": (utts[i + 1]["final"] if i + 1 < len(utts) else ""),
        })
    return out


# ---------------------------------------------------------------- 打印
def _fmt(v, pct=False, nd=3):
    if v is None:
        return "  —"
    return f"{v * 100:.1f}%" if pct else f"{v:.{nd}f}"


def render(res: dict) -> str:
    lines = []
    a = res["meta"]
    src = a.get("source") or f"{len(a.get('sources') or [])} 份：{', '.join(a.get('sources') or [])}"
    lines.append(f"来源: {src}")
    lines.append(f"句子 {a['utterances']}   草稿 {a['drafts']}   "
                 f"滑动窗口剔除 {a['sliding_excluded']}")
    lines.append("")
    head = f"{'设置':<16}{'回改率':>9}{'beyond-k':>10}{'草稿→定稿':>11}{'首字p50':>9}{'定稿p95':>9}"
    lines.append(head)
    lines.append("-" * len(head))
    for name, r in res["sweep"].items():
        dr = r["draft_rewrite"]
        fv = r.get("latency_first_visible_s") or {}
        fc = r.get("latency_final_compute_s") or {}
        lines.append(f"{name:<16}{_fmt(dr['rate'], True):>9}{_fmt(dr['rate_beyond_k'], True):>10}"
                     f"{_fmt(r['draft_to_final']['rate'], True):>11}"
                     f"{_fmt(fv.get('p50')):>9}{_fmt(fc.get('p95')):>9}")
    if res.get("pooled") and res["sweep"]:
        top = next(iter(res["sweep"].values()))["draft_rewrite"]
        lines.append("")
        lines.append(f"（汇总：{top['shown_words']} 个已显示词 / "
                     f"{int(top['rewritten'])} 个被回改 —— **计数相加**，不是对比率取平均）")
    if res["sweep"] and "wer_vs_reference" in next(iter(res["sweep"].values())):
        w = next(iter(res["sweep"].values()))["wer_vs_reference"]
        lines.append("")
        lines.append(f"WER(对参考稿): {w['wer']*100:.1f}%  "
                     f"(替 {w['sub']} / 漏 {w['del']} / 插 {w['ins']}，参考 {w['ref_len']} 词)")
    hc = res.get("hallucination_candidates") or []
    lines.append("")
    lines.append(f"腰斩臆造候选: {len(hc)} 条（**要人耳听**，机械判据会误报）")
    for c in hc[:12]:
        flag = " ⭐下一句重复" if c["next_head_repeats"] else ""
        lines.append(f"  #{c['utt']} 尾 {c['tail_words']} 词无草稿支持: "
                     f"{c['unsupported_tail']!r}{flag}")
    if len(hc) > 12:
        lines.append(f"  …另有 {len(hc) - 12} 条见 --json")
    for w in res.get("warnings") or []:
        lines.append("")
        lines.append(f"⚠️ {w}")
    return "\n".join(lines)


# ---------------------------------------------------------------- CLI
def build_report(path, *, ref=None, ks=(None, 1, 2, 3)) -> dict:
    events = load_events(path)
    utts = build_utts(events)
    sliding = sum(1 for u in utts for d in u["drafts"] if d["buf_s"] > SE.PARTIAL_MAX_S)
    ref_text = open(ref, encoding="utf-8").read() if ref else None
    warns = []
    if not any("buf_s" in e for e in events if e.get("kind") == "draft"):
        # ⚠️ 2026-10-08 那批 jsonl 没写 `buf_s` → 缓冲超过 10s 后窗口滑动的草稿
        #    不会被剔除，**回改率会偏高一大截**（实测 39.6% → 50.8%）。
        #    不许静默：不警告的话，两批数字会被当成同一把尺子直接比。
        warns.append("这份文件没有 buf_s 字段（2026-10-09 之前写的）→ 滑动窗口那批草稿"
                     "没被剔除，回改率偏高，**不能与带 buf_s 的文件直接比**")
    if not any(e.get("kind") == "draft" and e.get("raw", e.get("text")) != e.get("text")
               for e in events):
        warns.append("没有一条草稿的 raw 与 text 不同 → 现场采集时前缀冻结应该是**关着**的"
                     "（或者这份是旧格式）")
    res = {
        "meta": {"source": os.path.basename(path), "utterances": len(utts),
                 "drafts": sum(len(u["drafts"]) for u in utts),
                 "sliding_excluded": sliding},
        "sweep": sweep(utts, ref_text, ks),
        "hallucination_candidates": hallucination_candidates(utts),
        "warnings": warns,
    }
    summ = [e for e in events if e.get("kind") == "summary"]
    if summ:
        res["summary"] = summ[-1]
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("events", nargs="+",
                    help="一份或多份 jsonl；给多份就按计数**汇总**（不是平均比率）")
    ap.add_argument("--ref", help="参考稿（只有离线回放有；只对单文件有意义）")
    ap.add_argument("--ks", default="0,1,2,3", help="hold-back 词数，逗号分隔")
    ap.add_argument("--json")
    a = ap.parse_args()
    ks = tuple(None if x.strip() in ("", "0", "none", "raw") else int(x)
               for x in a.ks.split(","))
    if None not in ks:
        ks = (None,) + ks
    if len(a.events) > 1:
        res = pool_sweeps([build_report(p, ks=ks) for p in a.events])
    else:
        res = build_report(a.events[0], ref=a.ref, ks=ks)
    print(render(res))
    if a.json:
        json.dump(res, open(a.json, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"\n→ {a.json}")


if __name__ == "__main__":
    main()
