#!/usr/bin/env python3
"""拿一节真课的会话日志重放「实时总结」（假时钟、真模型、**test key**），看一章一段写出来什么样。

  ClassLive.app/Contents/MacOS/python scripts/summary_replay.py sessions/<名>.md [--minutes 40] [--out DIR]

- 只读 `sessions/<名>.md`；不写 sessions/、不写 ~/.classlive/（chapter_path=None、append_atoms=None）。
- 花钱：只用 `cloud_translator.load_test_key()`（test1 通行码），一节 40 分钟 ≈ 30 次原子 + ~8 次章节调用。
- 产出：`<out>/chapters.json`（最后一版每章，可直接喂 `probe_summary_pane.py`）+ `<out>/report.md`（逐章段落 + 词数 / 闸门统计）。
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


def load_items(md: pathlib.Path, minutes: float) -> list:
    out = []
    t_first = None
    for m in BLOCK.finditer(md.read_text(encoding="utf-8")):
        hh, mm, ss = map(int, m.group(1).split(":"))
        t = hh * 3600 + mm * 60 + ss
        t_first = t if t_first is None else t_first
        if t - t_first > minutes * 60:
            break
        out.append((len(out) + 1, m.group(1), m.group(2).strip(), m.group(3).strip(), t - t_first))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("md")
    ap.add_argument("--minutes", type=float, default=40.0)
    ap.add_argument("--out")
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
    def emit(p):
        emits.append(p)
        if p.get("kind") == "chapter":
            c = p["chapter"]
            timeline.append(f"课内 {clock[0] / 60:5.1f} min · 章 {c['id']} {c['status']:<7} · para={'有' if c.get('para') else '无'}")

    timeline: list = []
    s = live.LiveSummarizer(chat=chat, append_atoms=None, chapter_path=None,
                            emit=emit, chapters_enabled=True, deadline_gate=None)
    items = load_items(pathlib.Path(a.md), a.minutes)
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
    lines = [f"# 段落总结重放 · {pathlib.Path(a.md).name} · 前 {a.minutes:.0f} 分钟",
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
    (out / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
