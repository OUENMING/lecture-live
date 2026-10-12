#!/usr/bin/env python3
"""把「功能介绍」每一页渲染出来**截图**给人看。只建卡片，不跑 main，一个字节都不写用户状态。

    ClassLive.app/Contents/MacOS/python scripts/probe_tour.py [输出目录]

⚠️ 先拍一张基线：显示器在睡时 `screencapture` 是纯黑，和「没渲染出来」长得一样。
"""
from __future__ import annotations

import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from probe_keyentry import _region_of, _shot  # noqa: E402

import tour  # noqa: E402


def _pump(n=10):
    from Foundation import NSDate, NSDefaultRunLoopMode, NSRunLoop
    for _ in range(n):
        NSRunLoop.currentRunLoop().runMode_beforeDate_(
            NSDefaultRunLoopMode, NSDate.dateWithTimeIntervalSinceNow_(0.05))


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    out = pathlib.Path(argv[0]) if argv else pathlib.Path(tempfile.mkdtemp(prefix="classlive-tour-"))
    out.mkdir(parents=True, exist_ok=True)
    from AppKit import NSApplication
    NSApplication.sharedApplication()
    base = _shot(out / "00-baseline.png")
    card = tour.build()
    if card is None:
        print("❌ 卡片没建出来（CLASSLIVE_DEBUG=1 看异常）")
        return 1
    for i in range(len(tour.PAGES)):
        _pump()
        n = _shot(out / f"{i + 1:02d}-page.png", region=_region_of(card["panel"]))
        print(f"第 {i + 1} 页 {n} 字节 -> {out / f'{i + 1:02d}-page.png'}" + ("  ⚠️ 与基线同字节" if n == base else ""))
        if i < len(tour.PAGES) - 1:
            card["next"]()
    card["close"]()
    return 0


if __name__ == "__main__":
    sys.exit(main())
