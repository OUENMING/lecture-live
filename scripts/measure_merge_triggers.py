#!/usr/bin/env python3
"""跨段半句合并（3.9.7 第 4 项）的**触发频率**离线复盘 —— 只读，不联网、不写 sessions/。

用真实实现 `fragment_merge.should_merge` / `trigger_kind` 对作者 6 节课的
`<会话>.report.json`（段的时间轴 + ASR `end_punct`）与 `<会话>.live.jsonl`（屏上定稿行）
重放，核对 design.md §3 的 409 次（ASR 口径）/ 377 次（屏上口径）。

口径（与 `measure_triggers.py` / design.md §3 逐字相同）：
  · 段 i 的时间轴：`t` = 段尾（音频秒）、`dur_s` = 段长；
    音频间隔 gap = segs[j].t - segs[j].dur_s - segs[i].t（j = i+1）。
  · 段 i 的屏上尾行 = `live.jsonl` 里 seg == i 的**最后一条** `final` 的 `text`。
  · 段 j 的首行 = seg == j 的**第一条** `final` 的 `text`。
  · 触发 = `should_merge(尾行, end_punct_i, 首行, gap)`（要求两行都在）。

    ClassLive.app/Contents/MacOS/python scripts/measure_merge_triggers.py [--json 路径]
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import fragment_merge as fm                                          # noqa: E402

SESS = HERE / "sessions"
SESSIONS = [
    "2026-10-09_120723_ECON10770",
    "2026-10-12_130857_SOC10020",
    "2026-10-13_122245_ECON10770",
    "2026-10-13_150815_ECON10740",
    "2026-10-13_162749_ECON10740",
    "2026-10-15_131620_ECON10790",
]


def load(name: str):
    rep = json.loads((SESS / f"{name}.report.json").read_text(encoding="utf-8"))
    segs = {s["i"]: s for s in rep.get("segments") or []}
    fins: dict[int, list[dict]] = collections.defaultdict(list)
    with (SESS / f"{name}.live.jsonl").open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            o = json.loads(line)
            if o.get("kind") == "final":
                fins[o.get("seg")].append(o)
    for k in fins:
        fins[k].sort(key=lambda o: o["t"])
    return rep, segs, fins


def measure(name: str) -> dict:
    rep, segs, fins = load(name)
    dur = float(rep.get("duration_s") or 0.0)
    ids = sorted(segs)
    n_finals = sum(len(v) for v in fins.values())
    c = collections.Counter()
    for i, j in zip(ids, ids[1:]):
        si, sj = segs[i], segs[j]
        gap = sj["t"] - sj["dur_s"] - si["t"]
        fi, fj = fins.get(i) or [], fins.get(j) or []
        tail = fi[-1]["text"] if fi else ""
        head = fj[0]["text"] if fj else ""
        ep = si.get("end_punct") is not False      # True = 有句末标点
        kind = fm.trigger_kind(tail, ep)           # "" / asr / screen / both
        if not (tail.strip() and head.strip()) or not (gap < fm.MERGE_GAP_S):
            continue
        if fm.should_merge(tail, ep, head, gap):
            c["or"] += 1
        if kind in ("asr", "both"):
            c["asr"] += 1
        if kind in ("screen", "both"):
            c["screen"] += 1
    return {"session": name, "minutes": round(dur / 60.0, 2), "n_finals": n_finals,
            "trig_asr": c["asr"], "trig_screen": c["screen"], "trig_or": c["or"]}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--json", help="把明细也写一份 JSON（默认只打表）")
    a = ap.parse_args()
    rows = [measure(n) for n in SESSIONS]
    tot = {k: sum(r[k] for r in rows) for k in ("trig_asr", "trig_screen", "trig_or")}
    tot["n_finals"] = sum(r["n_finals"] for r in rows)
    tot["minutes"] = round(sum(r["minutes"] for r in rows), 1)

    print(f"{'session':26} {'min':>6} {'finals':>7} {'ASR':>5} {'screen':>7} {'OR':>5}")
    for r in rows:
        print(f"{r['session']:26} {r['minutes']:6.1f} {r['n_finals']:7d} "
              f"{r['trig_asr']:5d} {r['trig_screen']:7d} {r['trig_or']:5d}")
    print("-" * 62)
    print(f"{'TOTAL':26} {tot['minutes']:6.1f} {tot['n_finals']:7d} "
          f"{tot['trig_asr']:5d} {tot['trig_screen']:7d} {tot['trig_or']:5d}")
    print(f"\ndesign.md §3 基线： ASR 口径 **409** · 屏上口径 **377**（6 节 / 2540 行）")
    print(f"实测： ASR {tot['trig_asr']} · 屏上 {tot['trig_screen']} · "
          f"两口径任一(实际实现) {tot['trig_or']}")
    print(f"多出的重翻请求： ASR {100.0 * tot['trig_asr'] / tot['n_finals']:.1f}% · "
          f"屏上 {100.0 * tot['trig_screen'] / tot['n_finals']:.1f}% · "
          f"OR {100.0 * tot['trig_or'] / tot['n_finals']:.1f}%")
    if a.json:
        pathlib.Path(a.json).write_text(
            json.dumps({"per_session": rows, "total": tot}, ensure_ascii=False, indent=1),
            encoding="utf-8")
        print("写盘:", a.json)


if __name__ == "__main__":
    main()
