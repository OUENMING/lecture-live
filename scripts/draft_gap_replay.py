#!/usr/bin/env python3
"""草稿译文「空窗」回放量具（R1 方案 F）。

读现场采集的 `<会话>.live.jsonl`（`draft` / `final` / `draft_zh` 行），对每种触发策略
重放"哪些草稿会送翻"，翻译耗时按 1.0s 计，统计：
  · 送翻次数、送翻文本以悬挂词结尾的占比（B 关 / B 开）；
  · 讲话中 ≥6s 没有新中文的次数与总秒数（"讲话中" = 该区间内 ≥4 条英文草稿），其中 ≥10s 的次数；
  · 真实记录（`draft_zh` + 带 `zh` 的 `final`）作对照行。
⚠️ 判据**直接调** `draft_policy.should_translate` 与 `main.strip_dangling`，脚本里不另写一份
（离线模拟和生产不是同一条路的亏，见 `draft_policy.append_only` 的 docstring）。
只读：不写任何文件。用法：python scripts/draft_gap_replay.py sessions/<会话>.live.jsonl
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import draft_policy                                    # noqa: E402
from main import is_incomplete, strip_dangling         # noqa: E402

TR_S = 1.0          # 翻译耗时
GAP_S = 6.0         # 空窗阈值
MIN_DRAFTS = 4      # 区间内至少这么多条草稿才算"讲话中"


def load(path):
    drafts, finals, real_zh, n_draft_zh = [], [], [], 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            k = d.get("kind")
            if k == "draft":
                drafts.append(d)
            elif k == "final":
                finals.append(d)
                if d.get("zh"):
                    real_zh.append(d["t"])
            elif k == "draft_zh":
                real_zh.append(d["t"])
                n_draft_zh += 1
    return drafts, finals, sorted(real_zh), n_draft_zh


def replay(drafts, policy):
    """返回 [(送翻时刻, 送翻原文)]，节流按 seg 重置（同 main.partial_worker）。"""
    sent, last_tr, last_len, last_sent, prev, last_seg = [], 0.0, 0, "", "", None
    for d in drafts:
        t, now = d["text"], d["t"]
        if d["seg"] != last_seg:
            last_seg = d["seg"]
            last_tr, last_len, last_sent = 0.0, 0, ""
        nw = len(t.split())
        if draft_policy.should_translate(policy, now=now, last_tr=last_tr, n_words=nw,
                                         last_len=last_len, text=t, prev_text=prev,
                                         sent_text=last_sent):
            sent.append((now, t))
            last_tr, last_len, last_sent = now, nw, t
        prev = t
    return sent


def gaps(zh_times, draft_times, end):
    """相邻中文事件之间 ≥GAP_S 且区间内 ≥MIN_DRAFTS 条草稿的区间 → (次数, 总秒数, ≥10s 次数)。"""
    ev = [0.0] + sorted(zh_times) + [end]
    n = tot = n10 = 0
    for a, b in zip(ev, ev[1:]):
        if b - a >= GAP_S and sum(1 for x in draft_times if a < x < b) >= MIN_DRAFTS:
            n += 1
            tot += b - a
            n10 += (b - a) >= 10
    return n, tot, n10


def main(path):
    drafts, finals, real_zh, n_draft_zh = load(path)
    dts = [d["t"] for d in drafts]
    end = max(dts + [f["t"] for f in finals])
    fin_t = [f["t"] for f in finals if f.get("zh")]
    rows = []
    g = gaps(real_zh, dts, end)
    rows.append(("真实记录", n_draft_zh, None, None, g))
    for pol in ("legacy", "novel"):
        sent = replay(drafts, pol)
        arr = [t + TR_S for t, _ in sent] + fin_t
        dang_raw = sum(is_incomplete(x) for _, x in sent) / max(1, len(sent))
        dang_b = sum(is_incomplete(strip_dangling(x)) for _, x in sent) / max(1, len(sent))
        rows.append((pol, len(sent), dang_raw, dang_b, gaps(arr, dts, end)))
    print(f"{path}  ({len(drafts)} 草稿 / {len(finals)} 定稿 / {end:.0f}s)")
    pct = lambda x: "-" if x is None else f"{x:.0%}"
    print(f"{'策略':<8}{'送翻':>6}{'悬挂尾(B关)':>13}{'悬挂尾(B开)':>13}{'空窗次':>8}{'空窗秒':>8}{'≥10s次':>8}")
    for name, n, da, db, (gn, gs, g10) in rows:
        print(f"{name:<8}{n:>6}{pct(da):>13}{pct(db):>13}{gn:>8}{gs:>8.0f}{g10:>8}")
    print("注：真实记录行的「送翻」= draft_zh 条数；翻译耗时按 1.0s 计；空窗含定稿带来的中文。")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
