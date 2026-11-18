#!/usr/bin/env python3
"""说话人标注（2026-11-18）的真窗截图：双语 / 只英·校，老师不标、学生标「说话人 B」。

  ClassLive.app/Contents/MacOS/python scripts/probe_speaker_label.py

假数据、假时钟，不跑 main、不写 sessions/（`.window` 跑完还原）。本探针**不做断言**（看图用）；判据在 tests/。
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import probe_caption_ui as pcu  # noqa: E402
from probe_caption_ui import make, draw, close  # noqa: E402
from probe_hint import _shot, _region_of  # noqa: E402
from probe_motion import isolated_window_file  # noqa: E402

LINES = [
    ("", "In the short run, a competitive firm can make a profit.", "在短期内，竞争性企业可以获得利润。"),
    ("B", "Can I take no profit in a competitive market?", "在竞争市场里可以没有利润吗？"),
    ("", "Once again, in the short run, there can be profit.", "再说一遍，在短期内是可以有利润的。"),
    ("", "In the long run, there's no profit.", "从长期来看，是没有利润的。"),
    ("C", "So is that why firms keep entering the market?", "所以这就是企业不断进入市场的原因吗？"),
]


def shot(o, name: str) -> None:
    o._panel.orderFrontRegardless()
    draw(o, 0.05)
    p = pcu.OUT / f"{name}.png"
    n = _shot(p, region=_region_of(o._panel))
    print(f"{name}: {p}  {n} B")


def feed(o, lines, zh_on: bool) -> None:
    for spk, en, zh in lines:
        o.set_live_speaker(spk)
        o.add_draft(en)
        draw(o, 0.3)
        o.finalize(en, zh if zh_on else "", speaker=spk)
        draw(o, 1.2)


def main() -> int:
    base = _shot(pcu.OUT / "00-baseline.png")
    if base < 60_000:
        print("显示器在睡 / 没有屏幕录制权限，screencapture 是黑的；退出")
        return 1
    with isolated_window_file():
        o = make()
        o._join_on = False
        o.set_tail_merge(False)
        feed(o, LINES, True)
        o._set_bar_shown(False)
        draw(o, 0.6)
        shot(o, "1-bilingual")
        close(o)
        o = make()
        o._join_on = False
        o.set_tail_merge(False)
        try:
            o.set_translate_mode("en")
        except Exception:                        # noqa: BLE001
            pass
        feed(o, LINES, False)
        o._set_bar_shown(False)
        draw(o, 0.6)
        shot(o, "2-english-only")
        close(o)
    print(f"输出目录 {pcu.OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
