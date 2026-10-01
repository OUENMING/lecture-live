#!/usr/bin/env python3
"""把「开课前的准备」面板上那颗**测试模式开关**渲染出来并截图。

    ClassLive.app/Contents/MacOS/python scripts/probe_test_mode.py [输出目录]

默认输出 `~/Desktop/classlive-review/test-mode-toggle/`，拍两张：
`01-关.png`（默认）与 `02-开.png`（点一下之后）。

## 为什么单独一个探针

那颗开关是**用户可见文案**的载体（`测试模式：关` ⇄ `测试模式：开`），
而本仓的规矩是「**推送到用户面前之前，先给作者看效果**」。同 `probe_keyentry.py`。

## 两条纪律

⚠️ **一个字节都不写盘。** `on_test_mode` 是**桩**（返回 `True`，不落盘）——
   仓库根目录那份真 `.test-mode` 是「这节课录不录音、传不传」的开关，
   探针**绝不能碰它**（[[test-harness-must-isolate-writes]]）。
   面板本身在这一路也不写：`prepare`/`suggest`/`delete` 都要人点才会跑，这里只渲染。

⚠️ **拍之前先拍一张基线**（照 `probe_keyentry.py`）：显示器在睡时 `screencapture`
   会拍出**纯黑**，而那种图和"界面真的没渲染出来"长得一样。
   基线与实拍**字节数相同 = 空图**。

不在运行路径上 —— 不 import 它就什么都不会发生。
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

# ⚠️ 本文件在 `scripts/` 下，仓库根是**上一级** —— `.parent` 会指向 `scripts/`，
#    那样 import 仓库模块会静默找不到（报错点在后面，不容易看出是路径问题）。
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _shot(path: pathlib.Path, *, region=None) -> int:
    """截一张。`region` = `(x, y, w, h)`（**屏幕坐标，左上原点**）。返回**字节数**。"""
    cmd = ["screencapture", "-x", "-o"]
    if region is not None:
        cmd += ["-R", "%d,%d,%d,%d" % tuple(int(v) for v in region)]
    cmd.append(str(path))
    subprocess.run(cmd, check=False)
    return path.stat().st_size if path.exists() else 0


def _region_of(win) -> tuple:
    """`NSWindow` 的 frame（**左下原点**）→ `screencapture -R` 要的（**左上原点**）。

    ⚠️ 别用 `screencapture -l <windowNumber>` —— `probe_keyentry.py` 那轮实测过：
       截出来的**不是这张框**（标题对得上、控件全不对）。
    """
    from AppKit import NSScreen
    f = win.frame()
    h = NSScreen.mainScreen().frame().size.height
    pad = 24.0
    return (f.origin.x - pad,
            h - (f.origin.y + f.size.height) - pad,
            f.size.width + pad * 2,
            f.size.height + pad * 2)


def _pump(seconds: float = 0.5) -> None:
    """跑一会儿 runloop —— **建完立刻截会拍到空白**
    （同 `overlay` 那条「不跑 run loop 就不提交」的坑）。"""
    from Foundation import NSDate, NSRunLoop, NSDefaultRunLoopMode
    for _ in range(max(1, int(seconds / 0.05))):
        NSRunLoop.currentRunLoop().runMode_beforeDate_(
            NSDefaultRunLoopMode, NSDate.dateWithTimeIntervalSinceNow_(0.05))


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    out = (pathlib.Path(argv[0]) if argv
           else pathlib.Path.home() / "Desktop/classlive-review/test-mode-toggle")
    out.mkdir(parents=True, exist_ok=True)

    try:
        from AppKit import (NSApplication, NSApplicationActivationPolicyAccessory,
                            NSButton)
        import entry_panel as E
    except Exception as e:                                    # noqa: BLE001
        print(f"⚠ 起不来：{type(e).__name__}: {e}")
        return 1

    NSApplication.sharedApplication().setActivationPolicy_(
        NSApplicationActivationPolicyAccessory)

    base = _shot(out / "00-baseline.png")
    print(f"基线截图 {base} 字节", flush=True)

    h = E.build(on_start=lambda c: None,
                on_test_mode=lambda on: True,      # ⚠️ 桩：**不落盘**
                test_mode=False)
    if h is None:
        print("⚠ 面板没建起来（`CLASSLIVE_DEBUG=1` 看 traceback）")
        return 1

    def _find():
        stack, found = [h.window.contentView()], []
        while stack:
            v = stack.pop()
            for c in (v.subviews() or []):
                stack.append(c)
            if isinstance(v, NSButton) and (v.title() or "").startswith("测试模式："):
                found.append(v)
        return found

    try:
        h.window.orderFrontRegardless()
        _pump()
        sw = _find()
        if len(sw) != 1:
            print(f"⚠ 找到 {len(sw)} 颗开关 —— 截图会拍到一个没有开关的面板")
            return 1
        n1 = _shot(out / "01-关.png", region=_region_of(h.window))
        print(f"实拍（关）{n1} 字节 -> {out / '01-关.png'}", flush=True)
        sw[0].performClick_(None)           # = 点一下（桩，不写盘）
        _pump(0.3)
        n2 = _shot(out / "02-开.png", region=_region_of(h.window))
        print(f"实拍（开）{n2} 字节 -> {out / '02-开.png'}", flush=True)
        # ⚠️ 按文字**实际画了多宽**扫一遍：开关右边那几行提示文字超框就会被
        #    AppKit 静默吃掉（没有省略号），那种图看不出问题，这里先量。
        for b in _find():
            print(f"   开关文案 {b.title()!r}  frame={b.frame()}", flush=True)
        if n1 and (n1 == base or n2 == base):
            print("⚠️ 实拍和基线**字节数相同** —— 很可能拍的是黑图（显示器在睡？）")
    finally:
        h.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
