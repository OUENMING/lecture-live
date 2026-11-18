#!/usr/bin/env python3
"""拿一节真课的会话日志重放「实时总结」（假时钟、真模型、**test key**），看一章一段写出来什么样。

  ClassLive.app/Contents/MacOS/python scripts/summary_replay.py sessions/<名>.md [--minutes 40] [--out DIR] [--label 改前]

- 只读 `sessions/<名>.md`；不写 sessions/、不写 ~/.classlive/（chapter_path=None、append_atoms=None）。
- 花钱：只用 `cloud_translator.load_test_key()`（test1 通行码），一节 40 分钟 ≈ 30 次原子 + ~8 次章节调用。
- 产出：`<out>/chapters.json`（最后一版每章，可直接喂 `probe_summary_pane.py`）
  + `<out>/emits.jsonl`（每一条 chapter emit 一行，含课内秒 —— 时效量具的输入）
  + `<out>/report.md`（逐章段落 + 词数 / 闸门统计 + ⭐「时效」一节）。

## 「时效」量的是什么（改「每 75 s 重写当前章」之前/之后对比就看它）

  每章：开章 `t0`、第一条**有 para** 的 emit 距开章的秒数、更新次数、相邻两次更新的最大间隔。
  全节：首段距开课秒数、所有章「最大间隔」的最大值、章节调用次数。
  ⚠️ 不要因为「课内时钟跳得快」就误读：`sim_s` 是**课内秒**（假时钟），不是墙钟。
  ⚠️ 纯函数 `emit_record` / `timeliness` / `render_timeliness` 不联网、不碰文件，
     `tests/test_chapter_prev_para.py` 直接拿假 emits 调它们（跑真重放要花钱）。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import atom  # noqa: E402
import chapter as ch  # noqa: E402
import live_summary as live  # noqa: E402
from build_notes import _chat_json  # noqa: E402
from cloud_translator import load_test_key  # noqa: E402

BLOCK = re.compile(r"> \[!abstract\] (\d\d:\d\d:\d\d)\n> \*\*EN\*\*: (.*)\n> \*\*ZH\*\*: (.*)")


def load_items(md: pathlib.Path, minutes: float, start_min: float = 0.0) -> list:
    """`start_min`：从开课后第几分钟开始取（只重放中段，省调用 —— 2026-11-18 余额事故后加）。"""
    out = []
    t_first = None
    t_class = None
    for m in BLOCK.finditer(md.read_text(encoding="utf-8")):
        hh, mm, ss = map(int, m.group(1).split(":"))
        t = hh * 3600 + mm * 60 + ss
        t_class = t if t_class is None else t_class
        if t - t_class < start_min * 60:
            continue
        t_first = t if t_first is None else t_first
        if t - t_first > minutes * 60:
            break
        out.append((len(out) + 1, m.group(1), m.group(2).strip(), m.group(3).strip(), t - t_first))
    return out


def hms_to_s(hms) -> float:
    """`"HH:MM:SS"` → 秒（**墙钟**，不是课内秒）。读出不来返回 `0.0`
    （不抛 —— 报告宁少一个数不少一页）。"""
    try:
        hh, mm, ss = (int(x) for x in str(hms).split(":"))
    except (TypeError, ValueError):
        return 0.0
    return float(hh * 3600 + mm * 60 + ss)


def emit_record(payload: dict, sim_s: float) -> dict | None:
    """一条 streamq payload → `emits.jsonl` 的一行。**只有 `kind=="chapter"` 才记。**

    `sim_s` = 发出这一条时的**课内秒**（假时钟，与 `load_items` 的 `rel` 同域）。
    ⚠️ 形状不对（没 `chapter` / 不是 dict）→ `None`，不抛：这是量具，不是闸门。
    """
    if not isinstance(payload, dict) or payload.get("kind") != "chapter":
        return None
    c = payload.get("chapter")
    if not isinstance(c, dict):
        return None
    para = c.get("para")
    lead = para.get("lead") if isinstance(para, dict) else None
    return {"sim_s": float(sim_s),
            "id": c.get("id"),
            "status": c.get("status"),
            "version": c.get("version"),
            "t0": c.get("t0"),
            "t1": c.get("t1"),
            # 与时间线 `para=有/无` **同一判据**（`bool(para)`）—— 两处口径不一致，
            # 对比的时候会得到一个假的变化（report 写「有」而表里算「无」）。
            "has_para": bool(para),
            "lead_zh": str((lead or {}).get("zh") or "") if isinstance(lead, dict) else ""}


def timeliness(emits) -> dict:
    """`emits.jsonl` 的行（或 `emit_record` 的输出）→ 时效表。**纯函数、离线可测。**

    返回 `{"rows": [{id, t0, updates, first_para_s, first_para_after_s, max_gap_s}],
           "first_para_course_s", "max_gap_s", "calls"}`

    · `updates` = 这一章**总共**几条 emit（含第一条）
    · `first_para_s` / `max_gap_s` / 全节汇总都用**课内秒**（`sim_s`，与 `load_items` 的 `rel` 同域）
    · `first_para_after_s` = 距开章秒数；一次都没出过 para → `None`
    · 行按 `t0` 排（同 `report.md` 逐章那几节的顺序）
    ⚠️⚠️ **不能 `first_para_s - hms_to_s(t0)`**：`sim_s` 相对**开课**（首句 = 0），
       `t0` 是**墙钟** `HH:MM:SS` —— 两个真的原点不同，直接相减会把 `10:10:00` 减成 −36020 s
       （真重放火眼能看见，纯函数自测里看着也对，所以必须在这里就写清）。
       统一换成「相对本课首句墙钟」：`t0` 减本课最早那个 `t0`。
    ⚠️ `has_para` 看的是**这一条 emit 自己带的 para**，不是「这章最后有没有」。
    """
    rows: dict = {}
    for e in emits or ():
        if not isinstance(e, dict) or e.get("id") is None:
            continue
        r = rows.setdefault(e["id"], {"id": e["id"], "t0": None,
                                      "_ts": [], "first_para_s": None})
        ts = float(e.get("sim_s") or 0.0)
        r["_ts"].append(ts)
        r["t0"] = r["t0"] or e.get("t0")
        if e.get("has_para") and r["first_para_s"] is None:
            r["first_para_s"] = ts
    # 本课首句墙钟 = 所有章 `t0` 里最早的那个（= `items[0][1]`，假时钟下两者同源）
    walls = [hms_to_s(r["t0"]) for r in rows.values() if hms_to_s(r["t0"]) > 0]
    base = min(walls) if walls else 0.0
    out: list = []
    for r in rows.values():
        ts = sorted(r.pop("_ts"))
        gaps = [b - a for a, b in zip(ts, ts[1:])]
        fp = r["first_para_s"]
        out.append({**r,
                    "updates": len(ts),
                    "first_para_after_s": (None if fp is None
                                           else fp - (hms_to_s(r.get("t0")) - base)),
                    "max_gap_s": max(gaps) if gaps else None})
    out.sort(key=lambda x: hms_to_s(x.get("t0")))
    fps = [r["first_para_s"] for r in out if r["first_para_s"] is not None]
    gaps = [r["max_gap_s"] for r in out if r["max_gap_s"] is not None]
    return {"rows": out,
            "first_para_course_s": min(fps) if fps else None,
            "max_gap_s": max(gaps) if gaps else None,
            "calls": sum(1 for e in (emits or ()) if isinstance(e, dict))}


def render_timeliness(t: dict) -> list:
    """`timeliness()` 的结果 → report.md 里那一节的 markdown 行。**纯函数。**"""
    def n(v, unit="s"):
        return "—" if v is None else (f"{v:.0f}{unit}" if v == int(v) else f"{v:.1f}{unit}")

    lines = ["## 时效", "",
             "| 章id | 开章 t0 | 首条有 para（距开章） | 更新次数 | 相邻最大间隔 |",
             "|---|---|---|---|---|"]
    for r in t["rows"]:
        lines.append(f"| {r['id']} | {r.get('t0') or '—'} | "
                     f"{n(r['first_para_s'])}（{n(r['first_para_after_s'])}） | "
                     f"{r['updates']} | {n(r['max_gap_s'])} |")
    lines += ["", f"- 首段距开课：{n(t['first_para_course_s'])}",
              f"- 所有章的「相邻最大间隔」的最大值：{n(t['max_gap_s'])}",
              f"- 章节调用次数：{t['calls']}", ""]
    return lines


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("md")
    ap.add_argument("--minutes", type=float, default=40.0)
    ap.add_argument("--start-min", type=float, default=0.0, help="从开课后第几分钟开始取（只测中段）")
    ap.add_argument("--out")
    ap.add_argument("--label", default="", help="写进报告标题，方便改前 / 改后对比")
    a = ap.parse_args()
    key = load_test_key()
    calls = {"chapter": 0, "atom": 0, "fail": 0}
    lat: list = []
    clock = [0.0]

    def chat(sysp, block, max_tokens, temperature):
        cls = "chapter" if max_tokens >= ch.MAX_TOKENS else "atom"
        calls[cls] += 1
        t = time.monotonic()
        # ⚠️ 超时与真上课同值（main._summ_chat：20 s）—— 课上超时 = 重试一次再落空章
        r = _chat_json(key, atom.MODEL, sysp, block, max_tokens, temperature, timeout=20.0, call_class=cls)
        if cls == "chapter":
            lat.append(time.monotonic() - t)
        if r is None:
            calls["fail"] += 1
        return r

    emits: list = []
    emit_rows: list = []
    def emit(p):
        emits.append(p)
        rec = emit_record(p, clock[0])
        if rec is not None:
            emit_rows.append(rec)
        if p.get("kind") == "chapter":
            c = p["chapter"]
            timeline.append(f"课内 {clock[0] / 60:5.1f} min · 章 {c['id']} {c['status']:<7} · para={'有' if c.get('para') else '无'}")

    timeline: list = []
    s = live.LiveSummarizer(chat=chat, append_atoms=None, chapter_path=None,
                            emit=emit, chapters_enabled=True, deadline_gate=None)
    items = load_items(pathlib.Path(a.md), a.minutes, a.start_min)
    print(f"{len(items)} 句，{items[-1][4] / 60:.1f} 分钟")
    t0 = time.monotonic()
    for gid, hms, en, zh, rel in items:
        clock[0] = float(rel)
        s.feed((gid, hms, en, zh))
        s.step(float(rel))
    s.step(float(items[-1][4]) + 5)
    s.finish(timeout_s=120.0)
    last: dict = {}
    first_seen: dict = {}
    for e in emits:
        if e.get("kind") == "chapter":
            c = e["chapter"]
            last[c["id"]] = c
            first_seen.setdefault(c["id"], c.get("status"))
    out = pathlib.Path(a.out or tempfile.mkdtemp(prefix="summary_replay_"))
    out.mkdir(parents=True, exist_ok=True)
    chs = sorted(last.values(), key=lambda c: c["t0"])
    (out / "chapters.json").write_text(json.dumps(chs, ensure_ascii=False, indent=1), encoding="utf-8")
    # ⭐ 量的输入（改前/改后对比就 diff 这两个文件）—— 一行一条 chapter emit
    (out / "emits.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in emit_rows), encoding="utf-8")
    title = "段落总结重放" + (f" · {a.label}" if a.label else "")
    lines = [f"# {title} · {pathlib.Path(a.md).name} · 前 {a.minutes:.0f} 分钟",
             f"调用：章节 {calls['chapter']} / 原子 {calls['atom']} / 失败 {calls['fail']} · 墙钟 {time.monotonic() - t0:.0f}s",
             f"chapter 事件 {sum(1 for e in emits if e.get('kind') == 'chapter')} 条；首个 chapter 事件的课内时间见下", ""]
    first_t = next((e["chapter"]["t1"] for e in emits if e.get("kind") == "chapter"), None)
    lines.append(f"第一条总结（任何 status）覆盖到 {first_t}（开课 {items[0][1]}）")
    lines.append(f"章节调用耗时：max {max(lat or [0]):.1f}s · p50 {sorted(lat or [0])[len(lat or [0]) // 2]:.1f}s（超时 20 s）\n")
    lines.append("出章时间线（假时钟 = 课内时刻）：")
    lines += [f"- {x}" for x in timeline] + [""]
    for c in chs:
        p = c.get("para")
        lines.append(f"## {c.get('title_zh') or c.get('title')}  `{c['t0']}–{c['t1']}` {c['status']}")
        if not p:
            lines.append("- para: **None**（退回 sentences）")
            lines += [f"  - {x.get('zh')}" for x in c.get("sentences") or []]
        else:
            sents = [p["lead"]] + list(p.get("body") or [])
            lines.append(f"- words={p.get('words')} over={p.get('para_over')} 句数={len(sents)}")
            lines.append("- **ZH**：" + "".join(x.get("zh", "") for x in sents))
            lines.append("- **EN**：" + " ".join(x.get("en", "") for x in sents))
            lines.append("- bold：" + " / ".join("、".join(x.get("bold") or []) for x in sents))
        lines.append("")
    lines += render_timeliness(timeliness(emit_rows))
    (out / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
