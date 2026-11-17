#!/usr/bin/env python3
"""悬浮窗换皮（2026-11-17）的真窗截图：鼠标不在 / 鼠标在 / 右栏开着 / 浅色底。

  ClassLive.app/Contents/MacOS/python scripts/probe_restyle.py [chapters.json]

假数据、假时钟，不跑 main、不写 sessions/（`.window` 跑完还原）。
看什么：① 只有白色 ② 顶栏控件平时退到 ~35%、鼠标进来全亮 ③ 旧字幕逐行退后 ④ 术语胶囊 / 章节条。
本探针**不做断言**（给 Owen 看图用）；判据在 tests/。
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import probe_caption_ui as pcu  # noqa: E402
from probe_caption_ui import make, draw, close  # noqa: E402
from probe_hint import _shot, _region_of  # noqa: E402
from probe_motion import isolated_window_file  # noqa: E402
from probe_summary_pane import SAMPLE  # noqa: E402

LINES = [
    ("A monopolist is the only seller in its market.", "垄断者是其市场中唯一的卖家。"),
    ("So it faces the entire market demand curve by itself.", "所以它独自面对整条市场需求曲线。"),
    ("That means marginal revenue is always below the price.", "这意味着边际收益总是低于价格。"),
    ("So the monopolist produces less than a competitive market would.",
     "所以垄断者的产量低于一个竞争市场会有的产量。"),
    ("And that gap is what we call the deadweight loss.", "而这个差距就是我们所说的无谓损失。"),
]


def shot(o, name: str) -> None:
    o._panel.orderFrontRegardless()
    draw(o, 0.05)
    p = pcu.OUT / f"{name}.png"
    n = _shot(p, region=_region_of(o._panel))
    f = o._panel.frame()
    print(f"{name}: {p}  {n} B  panel={f.size.width:.0f}x{f.size.height:.0f}")


def main() -> int:
    chapters = json.loads(pathlib.Path(sys.argv[1]).read_text()) if len(sys.argv) > 1 else SAMPLE
    base = _shot(pcu.OUT / "00-baseline.png")
    if base < 60_000:
        print("显示器在睡 / 没有屏幕录制权限，screencapture 是黑的；退出")
        return 1
    with isolated_window_file():
        o = make()
        o._join_on = False                       # 每句一行：看得出逐行退后
        o.set_tail_merge(False)
        from AppKit import NSColor
        for en, zh in LINES:
            o.add_draft(en)
            draw(o, 0.4)
            o.finalize(en, zh)
            draw(o, 1.5)
        for c in chapters[:-1]:
            o.summary_update({"kind": "chapter", "chapter": c})
        o.terms([("deadweight loss", "无谓损失：产量低于有效水平造成的总剩余损失")])
        o.add_draft("Now let's look at how a regulator")
        draw(o, 0.6)
        o._set_bar_shown(False)
        draw(o, 0.6)
        shot(o, "1-idle")
        o._set_bar_shown(True)
        draw(o, 0.6)
        shot(o, "2-hover")
        o._on_gloss_click()                      # 钉住术语 → 胶囊里出解析
        draw(o, 0.4)
        shot(o, "3-term-pinned")
        o._on_gloss_click()
        o._on_chapter_click()                    # 开右栏
        draw(o, 0.6)
        o._set_bar_shown(False)
        draw(o, 0.6)
        shot(o, "4-split-idle")
        o._on_chapter_click()
        draw(o, 0.4)
        bg = getattr(o, "_backdrop", None)
        if bg is not None:                       # 浅色底（白 PPT）：看描边 + scrim 撑不撑得住
            bg.setBackgroundColor_(NSColor.colorWithWhite_alpha_(0.95, 1.0))
        o._panel.setOpaque_(False)
        o._panel.setBackgroundColor_(NSColor.clearColor())
        draw(o, 0.4)
        shot(o, "5-idle-light")
        close(o)
    print(f"输出目录 {pcu.OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
