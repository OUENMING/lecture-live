#!/usr/bin/env python3
"""实拍起步首屏（P0）：可见性 + 两行状态，不跑 main、不写用户数据。"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from probe_hint import _region_of, _shot  # noqa: E402
import startup_hud  # noqa: E402


def main() -> int:
    from AppKit import (NSApplication, NSApplicationActivationPolicyAccessory,
                        NSDate, NSRunLoop)
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    out = pathlib.Path(tempfile.mkdtemp(prefix="startup_hud_20261021_"))
    base = _shot(out / "00-baseline.png")
    print(f"baseline {base} bytes -> {out}")
    if base <= 0:
        print("screencapture failed (no file / no screen-recording permission); abort")
        return 1
    # 不要用“PNG 字节数 < 60k”判显示器睡：素色壁纸也可能很小。直接问 Quartz。
    from Quartz import CGDisplayIsAsleep, CGMainDisplayID
    if CGDisplayIsAsleep(CGMainDisplayID()):
        print(f"display asleep (baseline {base} bytes); abort")
        return 1

    hud = startup_hud.StartupHUD()
    if not hud.show():
        print("hud.show() failed")
        return 1
    if os.environ.get("CLASSLIVE_FROM_APP"):
        app.activateIgnoringOtherApps_(True)

    def pump(seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            NSRunLoop.currentRunLoop().runUntilDate_(
                NSDate.dateWithTimeIntervalSinceNow_(0.02))

    pump(0.5)
    n1 = _shot(out / "01-starting.png", region=_region_of(hud._window))
    hud.note(startup_hud.TEXT_TRANSLATOR)
    pump(0.2)
    n2 = _shot(out / "02-translator.png", region=_region_of(hud._window))
    text_ok = hud._label.stringValue() == startup_hud.TEXT_TRANSLATOR
    # 关闭前算好区域：orderOut 之后 NSWindow.screen() 可能返回 None，多屏会拍错屏。
    region = _region_of(hud._window)
    hud.close()
    pump(0.2)
    n3 = _shot(out / "03-closed.png", region=region)
    visible = bool(hud._window.isVisible())
    print(f"OUT={out} starting={n1} translator={n2} closed={n3} "
          f"text_ok={text_ok} visible_after_close={visible}")
    print("\n".join(str(p) for p in sorted(out.glob("*.png"))))
    return 0 if (n1 > 0 and n2 > 0 and n3 > 0 and text_ok and not visible) else 1


if __name__ == "__main__":
    sys.exit(main())
