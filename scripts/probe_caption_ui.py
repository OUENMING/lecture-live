#!/usr/bin/env python3
"""Real Overlay shots for the 2026-10-21 caption changes.

Cases (all driven through Overlay's public API, fake clock, no main / no writes):
  01 stale Chinese before clear (old behavior)
  02 stale Chinese after clear (new DraftView.on_draft)
  03 Okay? short tail merged into previous row (no separate row)
  04 Okay? final still one row
  05 first word of a new sentence is its own row immediately
  06 same new sentence grows, row does not jump
  07 JOIN_ON: continuous speech appends to the previous row
"""
from __future__ import annotations

import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from probe_hint import _pump, _region_of, _shot  # noqa: E402
from probe_motion import isolated_window_file  # noqa: E402

from AppKit import (NSApplication, NSApplicationActivationPolicyAccessory, NSColor,  # noqa: E402
                    NSWindow, NSBorderlessWindowMask, NSBackingStoreBuffered,
                    NSWindowBelow)
import overlay  # noqa: E402
from draft_translate import DraftView  # noqa: E402

NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)

OUT = pathlib.Path(tempfile.mkdtemp(prefix="caption_ui_20261021_"))
CLOCK = [100.0]


def clock() -> float:
    return CLOCK[0]


def make():
    o = overlay.Overlay(on_lost=lambda: True)
    o._clock = clock
    o._tv._clock = clock
    o._tv._reduce_motion = lambda: False
    o._tv._animate_ok = lambda: True
    o.set_tail_merge(True)
    o._join_on = True
    # 探针图用不透明黑底：真窗是透明浮层，背后桌面/别的窗口会被 screencapture 一起拍进来。
    o._panel.setOpaque_(True)
    o._panel.setBackgroundColor_(NSColor.blackColor())
    bg = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        o._panel.frame(), NSBorderlessWindowMask, NSBackingStoreBuffered, False)
    bg.setBackgroundColor_(NSColor.blackColor())
    bg.setOpaque_(True)
    bg.setIgnoresMouseEvents_(True)
    bg.setLevel_(o._panel.level())
    bg.orderFrontRegardless()
    o.show()
    o._panel.orderFrontRegardless()
    bg.orderWindow_relativeTo_(NSWindowBelow, o._panel.windowNumber())
    o._backdrop = bg
    _pump(o, 0.5)
    return o


def draw(o, dt: float = 0.2):
    CLOCK[0] += dt
    o._mark_dirty(urgent=True)
    o._render()
    _pump(o, 0.08)


def shot(o, name: str):
    o._panel.orderFrontRegardless()
    _pump(o, 0.03)
    path = OUT / f"{name}.png"
    n = _shot(path, region=_region_of(o._panel))
    print(f"{name}: {n} bytes  live_visible={o._tv._live_visible} "
          f"live={o._tv._live!r} history={len(o._history)}")
    return n


def close(o):
    bg = getattr(o, "_backdrop", None)
    o.close()
    if bg is not None:
        bg.close()


def main() -> int:
    base = _shot(OUT / "00-baseline.png")
    print(f"baseline {base} bytes -> {OUT}")
    if base < 60_000:
        print("display asleep or no screen-recording permission; abort")
        return 1

    bad = 0
    with isolated_window_file():
        # --- stale Chinese: old vs new same-seg clear ---
        o = make()
        o.finalize("I would like you to read the Journal.",
                   "我希望你们读《华尔街日报》。")
        draw(o)
        dv = DraftView()
        dv.on_draft(70, "So it should be open now.")
        o.add_draft("So it should be open now.")
        o.draft_zh("所以它现在应该打开了。")
        draw(o)
        o.endpoint(True)
        # old behavior: same seg new English, no clear
        o.add_draft("Now hold")
        draw(o)
        bad += 0 if shot(o, "01-stale-zh-before") > 0 else 1
        # new behavior: DraftView.on_draft sees a new sentence -> clear first
        cleared = dv.on_draft(70, "Now hold")
        print(f"DraftView.on_draft same-seg new sentence -> {cleared}")
        if cleared:
            o.draft_zh("")
        draw(o)
        bad += 0 if shot(o, "02-stale-zh-after") > 0 else 1
        close(o)

        # --- Okay? short tail merged, even after an endpoint ---
        o = make()
        o.finalize("I would like you to read the Journal.",
                   "我希望你们读《华尔街日报》。")
        draw(o)
        o.endpoint(True)
        o.add_draft("Okay?")
        o.draft_zh("好的。")
        draw(o)
        bad += 0 if shot(o, "03-okay-merged") > 0 else 1
        o.seed_en("Okay?")
        o.stream_zh("好的。")
        draw(o)
        o.finalize("Okay?", "好的。")
        draw(o)
        bad += 0 if shot(o, "04-okay-final") > 0 else 1
        close(o)

        # --- first word new row / grows without jump ---
        o = make()
        o.finalize("I would like you to read the Journal.",
                   "我希望你们读《华尔街日报》。")
        draw(o)
        o.endpoint(True)
        o.add_draft("Now")
        draw(o)
        bad += 0 if shot(o, "05-first-word-row") > 0 else 1
        o.add_draft("Now hold on.")
        draw(o)
        bad += 0 if shot(o, "06-first-word-grown") > 0 else 1
        close(o)

        # --- JOIN_ON preserved: no endpoint => append to previous row ---
        o = make()
        o.finalize("I would like you to read the Journal.",
                   "我希望你们读《华尔街日报》。")
        draw(o)
        o.add_draft("Sometimes")
        o.draft_zh("有时")
        draw(o)
        bad += 0 if shot(o, "07-join-on") > 0 else 1
        close(o)

    print(f"OUT={OUT} bad={bad}")
    print("\n".join(str(p) for p in sorted(OUT.glob("*.png"))))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
