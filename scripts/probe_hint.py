#!/usr/bin/env python3
"""把 ❓ 轻点拨（计划 §G1）在悬浮窗上的样子渲染出来并截图。

    ClassLive.app/Contents/MacOS/python scripts/probe_hint.py

建一个 `Overlay`，放两句字幕 + 一个术语，分别 `show_hint` 两种文字
（「正在提炼」→「💡 一句话」），各拍一张图到 `tempfile.mkdtemp()` 下并打印路径。

## 两条纪律（照 `probe_test_mode.py`）

⚠️ **只建 Overlay，不跑 main** —— 回调全是桩，一个字节都不写 `sessions/` / Obsidian。
⚠️ **拍之前先拍一张基线**：显示器在睡时 `screencapture` 会拍出纯黑，
   和"界面没渲染出来"长得一样。实拍字节数与基线相同 = 空图。

推送前要给作者看图（`show-before-push`）。不在运行路径上。
"""
from __future__ import annotations

import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _shot(path: pathlib.Path, *, region=None) -> int:
    cmd = ["screencapture", "-x", "-o"]
    if region is not None:
        cmd += ["-R", "%d,%d,%d,%d" % tuple(int(v) for v in region)]
    cmd.append(str(path))
    subprocess.run(cmd, check=False)
    return path.stat().st_size if path.exists() else 0


def _region_of(win) -> tuple:
    """`NSWindow` frame（左下原点）→ `screencapture -R`（左上原点）。
    ⚠️ 别用 `-l <windowNumber>`：probe_keyentry 实测截出来的不是这张窗。
    ⚠️ 翻转用的屏高必须是**这张窗所在的那块屏**（`win.screen()`）—— 多显示器下
       `NSScreen.mainScreen()` 可能是另一块（高/原点都不同）→ 坐标系翻转错位，
       拍出来的是**别人窗口**（2026-10-17 实测：等宽双屏时拍到了一屏网页）。"""
    from AppKit import NSScreen
    f = win.frame()
    scr = getattr(win, "screen", None)
    scr = scr() if callable(scr) else None
    scr = scr or NSScreen.mainScreen()
    h = scr.frame().size.height if scr is not None else 0.0
    pad = 24.0
    return (f.origin.x - pad, h - (f.origin.y + f.size.height) - pad,
            f.size.width + pad * 2, f.size.height + pad * 2)


def _pump(o, seconds: float = 0.6) -> None:
    """跑 runloop 并驱动 overlay —— 建完立刻截会拍到空白。"""
    import time
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        o.pump()


def main() -> int:
    try:
        from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
        from overlay import Overlay
    except Exception as e:                                    # noqa: BLE001
        print(f"⚠ 起不来：{type(e).__name__}: {e}")
        return 1
    NSApplication.sharedApplication().setActivationPolicy_(
        NSApplicationActivationPolicyAccessory)

    out = pathlib.Path(tempfile.mkdtemp(prefix="probe_hint_"))
    base = _shot(out / "00-baseline.png")
    print(f"基线截图 {base} 字节 -> {out / '00-baseline.png'}", flush=True)

    o = Overlay(on_lost=lambda: True)          # ⚠️ 桩：不落盘
    o.show()
    o.finalize("Marginal cost is the cost of producing one more unit.",
               "边际成本是多生产一单位的成本。")
    o.finalize("So price equals marginal cost in perfect competition.",
               "所以在完全竞争中价格等于边际成本。")
    o.terms([("marginal cost", "多生产一单位带来的额外成本")])
    _pump(o)

    bad = 0
    for name, text in (("01-正在提炼.png", "❓ 已标记 · 正在提炼一句话…"),
                       ("02-一句话.png", "💡 老师在讲：完全竞争下价格等于边际成本")):
        o.show_hint(text, ttl=60)
        _pump(o)
        n = _shot(out / name, region=_region_of(o._panel))
        print(f"实拍 {n} 字节 -> {out / name}", flush=True)
        if n == base:
            print("⚠ 字节数与基线相同 = 很可能是空图（显示器在睡？）")
            bad = 1
    o.close()
    return bad


if __name__ == "__main__":
    sys.exit(main())
