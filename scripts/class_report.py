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
    # ⚠️ 两个来源**都**写 `seg`（2026-10-09 起 `stream_eval --jsonl` 也写了），
    #    所以这里正常走 `seg`；`any(...)` 只是替 2026-10-08 那批**旧产物**兜底
    #    （它们缺 `seg`，但当年就是按 `utt` 分的，语义一致）。
    use_seg = any("seg" in e for e in events if e.get("kind") in ("draft", "final"))
    keyf = (lambda r: r.get("seg")) if use_seg else (lambda r: r.get("utt"))
    utts: dict = {}
    order: list = []
    for rec in events:
        k = rec.get("kind")
        if k not in ("draft", "final"):
            continue
        kk = keyf(rec)
        u = utts.get(kk)
        if u is None:
            u = utts[kk] = {"drafts": [], "final": "", "final_compute_s": None, "dur": None}
            order.append(kk)
        if k == "draft":
            t = rec.get("t", 0.0)
            u["drafts"].append({
                "audio_t": rec.get("audio_t"),          # 离线有；**现场是 None，不许补**（见下）
                "compute_s": float(rec.get("compute_s") or 0.0),
                "raw": rec.get("raw", rec.get("text", "")),
                "shown": rec.get("text", ""),
                "buf_s": float(rec.get("buf_s") or 0.0),
                "engine": rec.get("engine"),            # R3：nemotron 不滑窗（`stream_eval._slid`）
                "t": t,
            })
        else:
            # ⚠️ 一段的**多句**定稿拼起来（只有一段一句时才等于那一句）
            u["final"] = (u["final"] + " " + (rec.get("text") or "")).strip()
            if rec.get("compute_s") is not None:
                u["final_compute_s"] = float(rec["compute_s"])
    out = []
    for kk in order:
        u = utts[kk]
        # ⚠️⚠️ **不给 `audio_t` 造替身**（2026-10-09 被审查抓到一个真的错）：
        #    曾经写成"现场没有音频时刻就拿事件入队秒数顶上"，结果是
        #    `stream_eval.evaluate()` 里那条**专为现场加的** `audio_t is not None` 守卫
        #    永远不成立 → 屏幕上会打出一个**假的首字延迟**（它恒等于草稿的 compute_s，
        #    因为 `start_t` 就是 `draft[0].audio_t`，相减恒为 0）。
        #    生产里**根本没有音频时钟**（`livemetrics` 只记墙钟），这一格**必须留空**。
        #    `start_t/end_t/dur` 给 0.0 而不是 None：`None - None` 会炸，
        #    而且它们只在延迟那几项里被读，那几项已经因为 `audio_t is None` 而空了。
        _has_audio = bool(u["drafts"]) and u["drafts"][0]["audio_t"] is not None
        u["start_t"] = u["drafts"][0]["audio_t"] if _has_audio else 0.0
        u["end_t"] = u["drafts"][-1]["audio_t"] if _has_audio else 0.0
        u["dur"] = u["end_t"] - u["start_t"]
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
        st = DraftStabilizer(holdback)      # 构造 = `reset()`，等价于"每句边界作废冻结前缀"
        u2 = dict(u)
        u2["drafts"] = []
        for d in u["drafts"]:
            d2 = dict(d)
            d2["shown"] = st.update(d["raw"])
            u2["drafts"].append(d2)
        out.append(u2)
    return out


#: 「beyond-k」用的 k —— 直接用 `stream_metrics.DEFAULT_K`（**唯一定义点**），
#: 免得这里手抄一个 `2`，日子久了和 `stream_eval --k` 的默认悄悄分家。
ANALYZE_K = M.DEFAULT_K

#: `--ks` 的默认曲线（只在这里写一次；`build_report` 的默认值也指向它）。
DEFAULT_KS = (None, 1, 2, 3)


def sweep(utts, ref_text, ks, want_disagreement: bool = False) -> dict:
    """对一组 K 各算一次（`None` = 不冻结，用事件里已有的 `shown`）。

    ⚠️ 「冻结的 K」与「beyond-k 判据的 k」是**两件事**：前者改上屏文本，
       后者只影响"灰尾吸收后还看得见多少"这个统计量。这里前者随 `ks` 变，
       后者**钉死**在 `ANALYZE_K`。

    ⚠️ `want_disagreement`（整课 WER，**O(n²)**，一节真课单遍 5–9 秒）**只给基线那一遍开** ——
       原来每个 K 都算一遍（默认 4 遍，20–40 秒），而文本表根本不打这一项。
    """
    out = {}
    for k in ks:
        u = apply_stabilizer(utts, k) if k is not None else utts
        out["baseline(raw)" if k is None else f"holdback={k}"] = SE.evaluate(
            u, ref_text, k=ANALYZE_K,
            want_disagreement=want_disagreement and k is None)
    return out


# ---------------------------------------------------------------- 生产延迟（只有现场那份有）
def live_latency(events) -> dict | None:
    s = _latency_samples(events)
    return None if s is None else {k: M.summarize_latency(v) for k, v in s.items()}


def _latency_samples(events) -> dict | None:
    """现场 `.live.jsonl` 的**墙钟**延迟。离线回放那份没有这两种字段 → `None`。

    - `final_delay_s`：一段音频**结束**（`segment_end` 事件，挂在 `Segmenter.on_utterance_end`）
      到这一段**第一句定稿上屏**（`final` 事件）的秒数 = Whisper + 翻译 + 排队。
      ⭐ 2026-10-09 之前的文件也有（`segment_end` 一直在记）。
    - `draft_lag_s`：草稿缓冲交出 → 上屏（`lag_s`，2026-10-09 晚加的字段）。
    - `first_visible_s`：每段**第一份**草稿的 `buf_s + lag_s`
      ⚠️ 只在**实时音源**（麦克风/系统声）下成立：`buf_s` 是音频秒，要它等于墙钟秒，
         音频就得按实时速度进来（`--source file --speed 2` 时会被低估一半）。
         另含 300 ms pre-roll（`vad.PRE_ROLL_CHUNKS`）→ 偏大至多 0.3 s。
    """
    seg_end = {e.get("seg"): e["t"] for e in events
               if e.get("kind") == "event" and e.get("event") == "segment_end"}
    first_final: dict = {}
    first_draft: dict = {}
    lags = []
    for e in events:
        k = e.get("kind")
        if k == "final" and e.get("seg") in seg_end and e.get("seg") not in first_final:
            first_final[e["seg"]] = e["t"]
        elif k == "draft" and e.get("lag_s") is not None:
            lags.append(float(e["lag_s"]))
            first_draft.setdefault(e.get("seg"), float(e.get("buf_s") or 0.0) + float(e["lag_s"]))
    if not seg_end and not lags:
        return None
    # ⚠️ 返回**原始样本** —— 多节汇总要把样本拼起来再算分位数（分位数不能相加/平均）。
    return {
        "final_delay_s": [first_final[s] - seg_end[s] for s in first_final],
        "draft_lag_s": lags,
        "first_visible_s": list(first_draft.values()),
    }


# ---------------------------------------------------------------- 多节课汇总
def pool_sweeps(res_list) -> dict:
    """把几份报告**按计数相加**再算比率 —— **不是**对比率取平均。

    ⚠️ 为什么必须这样：每一节的句子数/词数差很多（3 分钟切片 vs 50 分钟整节），
       对比率取平均等于让最短那节和最长那节一样重。开源同类项目（WhisperLiveKit 的
       `benchmark/`）也是先累加计数、最后才出比率。
    ⚠️ 只汇总**同一个设置名**都在的文件（`baseline(raw)` / `holdback=K`）。
    """
    names = list(dict.fromkeys(n for r in res_list for n in r["sweep"]))
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
        dr["rate"] = M.rate(dr["rewritten"], dr["shown_words"])
        dr["rate_beyond_k"] = M.rate(dr["rewritten_beyond_k"], dr["shown_words"])
        f_edits = sum(x["draft_to_final"]["edits"] for x in got)
        f_words = sum(x["draft_to_final"]["draft_words"] for x in got)
        out[name] = {"draft_rewrite": dr,
                     "draft_to_final": {"edits": f_edits, "draft_words": f_words,
                                        "rate": M.rate(f_edits, f_words)},
                     # 汇总那份**没有**延迟/WER（每节的 `audio_t` 与参考稿都不在身上）
                     "latency_first_visible_s": {"n": 0}, "latency_final_compute_s": {"n": 0}}
    # ⚠️⚠️ 下面四项 2026-10-14 前**整块丢掉**：两份各 65 / 31 条候选，汇总打「0 条」，
    #    `--json` 还把这份 0 当正常结果写出去（下游读它不报错）。现在逐份带来源拼起来。
    srcs = meta["sources"]
    res: dict = {"meta": meta, "sweep": out, "pooled": True,
                 "hallucination_candidates": [dict(c, source=s) for r, s in zip(res_list, srcs)
                                              for c in r.get("hallucination_candidates") or []],
                 "warnings": [f"[{s}] {w}" for r, s in zip(res_list, srcs)
                              for w in r.get("warnings") or []]}
    sums = [dict(r["summary"], source=s) for r, s in zip(res_list, srcs) if r.get("summary")]
    if sums:
        res["summaries"] = sums
    samples = [r["_latency_samples"] for r in res_list if r.get("_latency_samples")]
    if samples:
        res["live_latency"] = {k: M.summarize_latency([x for s in samples for x in s[k]])
                               for k in samples[0]}
    elif any(r.get("live_latency") for r in res_list):
        res["warnings"].append("各份有生产延迟、但没带原始样本 → 汇总里不出延迟"
                               "（用 build_report(..., keep_samples=True) 建）")
    return res


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
    # ⚠️ 只要**每段最后一份**草稿 —— 曾经归一化过整张二维矩阵（只用 `[i][-1]`），
    #    一节 50 分钟的课白算约 2700 次 `normalize`。
    last_seq = [_norm_seq(u["drafts"][-1]["shown"]) if u["drafts"] else [] for u in utts]
    out = []
    for i, u in enumerate(utts):
        fin = seqs[i]
        if len(fin) < min_tail or not u["drafts"]:
            continue
        tail = beyond_draft(fin, last_seq[i])
        if not (min_tail <= len(tail) <= max_tail):
            continue
        nxt = seqs[i + 1] if i + 1 < len(seqs) else []
        out.append({
            "utt": i,
            "final": u["final"],
            "last_draft": u["drafts"][-1]["shown"],
            "unsupported_tail": " ".join(tail),
            "tail_words": len(tail),
            "next_head_repeats": bool(nxt) and nxt[:2] == tail[:2],
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
    first = next(iter(res["sweep"].values()), {})
    if res.get("pooled") and first:
        top = first["draft_rewrite"]
        lines.append("")
        lines.append(f"（汇总：{top['shown_words']} 个已显示词 / "
                     f"{int(top['rewritten'])} 个被回改 —— **计数相加**，不是对比率取平均）")
    if "wer_vs_reference" in first:
        w = first["wer_vs_reference"]
        lines.append("")
        lines.append(f"WER(对参考稿): {w['wer']*100:.1f}%  "
                     f"(替 {w['sub']} / 漏 {w['del']} / 插 {w['ins']}，参考 {w['ref_len']} 词)")
    lat = res.get("live_latency")
    if lat:
        lines.append("")
        lines.append("生产延迟（墙钟秒，p50 / p95 / 样本数）：")
        for key, label in (("first_visible_s", "首字（实时音源才准）"),
                           ("draft_lag_s", "草稿 交出→上屏"),
                           ("final_delay_s", "段结束→定稿上屏")):
            v = lat[key]
            lines.append(f"  {label:<14}{_fmt(v['p50'])} / {_fmt(v['p95'])}   n={v['n']}")
    hc = res.get("hallucination_candidates") or []
    lines.append("")
    lines.append(f"腰斩臆造候选: {len(hc)} 条（**要人耳听**，机械判据会误报）")
    for c in hc[:12]:
        flag = " ⭐下一句重复" if c["next_head_repeats"] else ""
        where = f"{c['source']} " if "source" in c else ""
        lines.append(f"  {where}#{c['utt']} 尾 {c['tail_words']} 词无草稿支持: "
                     f"{c['unsupported_tail']!r}{flag}")
    if len(hc) > 12:
        lines.append(f"  …另有 {len(hc) - 12} 条见 --json")
    for w in res.get("warnings") or []:
        lines.append("")
        lines.append(f"⚠️ {w}")
    return "\n".join(lines)


# ---------------------------------------------------------------- CLI
def build_report(path, *, ref=None, ks=DEFAULT_KS, want_disagreement=False,
                 keep_samples=False) -> dict:
    """单份报告。`keep_samples=True` 时多带一份延迟原始样本给 `pool_sweeps` 拼。"""
    events = load_events(path)
    utts = build_utts(events)
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
    sw = sweep(utts, ref_text, ks, want_disagreement=want_disagreement)
    # ⚠️「滑动窗口剔除」**不在本地再数一遍** —— 它已经在 `evaluate()` 的
    #    `draft_rewrite.sliding_drafts_excluded` 里（同一份口径，别再写第二份来实现同一件事）。
    _first = next(iter(sw.values()), {}).get("draft_rewrite", {})
    res = {
        "meta": {"source": os.path.basename(path), "utterances": len(utts),
                 "drafts": sum(len(u["drafts"]) for u in utts),
                 "sliding_excluded": _first.get("sliding_drafts_excluded", 0)},
        "sweep": sw,
        "hallucination_candidates": hallucination_candidates(utts),
        "warnings": warns,
    }
    summ = [e for e in events if e.get("kind") == "summary"]
    if summ:
        res["summary"] = summ[-1]
    samples = _latency_samples(events)
    if samples is not None:
        res["live_latency"] = {k: M.summarize_latency(v) for k, v in samples.items()}
        if keep_samples:
            res["_latency_samples"] = samples
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("events", nargs="+",
                    help="一份或多份 jsonl；给多份就按计数**汇总**（不是平均比率）")
    ap.add_argument("--ref", help="参考稿（只有离线回放有；只对单文件有意义）")
    ap.add_argument("--ks", default=",".join("0" if k is None else str(k) for k in DEFAULT_KS),
                    help="hold-back 词数，逗号分隔（0/省略 = 基线那一行）")
    ap.add_argument("--json")
    a = ap.parse_args()
    ks = tuple(None if x.strip() in ("", "0", "none", "raw") else int(x)
               for x in a.ks.split(","))
    if None not in ks:
        ks = (None,) + ks
    # ⚠️ 整课 WER 那个 O(n²) 只在要写 `--json` 时才开（文本表不打它）——
    #    不开的话"上完课第一件事就是跑一遍报告"这条路上省掉 1 遍平方 DP。
    want_dis = bool(a.json) or bool(a.ref)
    if len(a.events) > 1:
        if a.ref:
            ap.error("--ref 只对单文件有意义（参考稿对不上多节课）")
        res = pool_sweeps([build_report(p, ks=ks, want_disagreement=want_dis, keep_samples=True)
                           for p in a.events])
    else:
        res = build_report(a.events[0], ref=a.ref, ks=ks, want_disagreement=want_dis)
    print(render(res))
    if a.json:
        json.dump(res, open(a.json, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"\n→ {a.json}")


if __name__ == "__main__":
    main()
