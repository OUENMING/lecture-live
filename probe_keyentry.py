#!/usr/bin/env python3
"""渲染「填 API key」那张框，**截图**给人看。⚠️ **不 `runModal`**（那会挂住）。

    ClassLive.app/Contents/MacOS/python probe_keyentry.py [输出目录]

## 为什么单独一个探针

那张框是**用户可见文案**的载体，而仓库规矩是「推送到用户面前之前，先给作者看效果」。
`ask_text()` 自己会 `runModal`（阻塞），所以这里只调 `build_text_alert()` + `orderFront`，
截完就关。

⚠️ **拍之前先拍一张基线**：显示器在睡时 `screencapture` 会拍出**纯黑**，
而那种图和"界面真的没渲染出来"长得一样。基线与实拍**字节数相同 = 空图**。

不在运行路径上 —— 不 import 它就什么都不会发生。
"""
from __future__ import annotations

import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import notice                                                        # noqa: E402

#: 探针里那张框的内容 —— ⚠️ **与 `entry_panel._open_key_entry` 里逐字一致**。
#: 两处一旦分叉，截出来的图就不再代表真实界面了。
FIELDS = [
    {"key": "deepseek", "label": "DeepSeek key",
     "hint": "翻译用的。不填也能上课 —— 退回本地模型，质量差一些。"},
    {"key": "jev", "label": "Jev key · 可选",
     "hint": "给「重点句」和「课务」用。不填这两个功能就不出现。"},
]
TITLE = "填 API key"
MESSAGE = "只写进这台机器的 ~/.classlive/，不上传。"
BUTTONS = ("保存", "以后再说")


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

    ⚠️ 第一版用 `screencapture -l <windowNumber>`，截出来的**不是这张框**
       （标题对得上、控件全不对）—— 改按区域截，坐标自己算，不依赖窗口号。
    """
    from AppKit import NSScreen
    f = win.frame()
    h = NSScreen.mainScreen().frame().size.height
    pad = 24.0
    return (f.origin.x - pad,
            h - (f.origin.y + f.size.height) - pad,
            f.size.width + pad * 2,
            f.size.height + pad * 2)


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    out = pathlib.Path(argv[0]) if argv else pathlib.Path("/tmp/classlive-keyentry")
    out.mkdir(parents=True, exist_ok=True)

    from AppKit import NSApplication
    from Foundation import NSDate, NSRunLoop, NSDefaultRunLoopMode
    app = NSApplication.sharedApplication()

    base = _shot(out / "00-baseline.png")
    print(f"基线截图 {base} 字节", flush=True)

    alert, _boxes = notice.build_text_alert(TITLE, MESSAGE, FIELDS, BUTTONS)
    win = alert.window()
    win.center()
    win.orderFrontRegardless()
    # ⚠️ 必须**跑一会儿 runloop** 让它真的画上去 —— 建完立刻截会拍到空白
    #    （同 `overlay` 那条「不跑 run loop 就不提交」的坑）。
    for _ in range(10):
        NSRunLoop.currentRunLoop().runMode_beforeDate_(
            NSDefaultRunLoopMode, NSDate.dateWithTimeIntervalSinceNow_(0.05))

    shot = _shot(out / "01-dialog.png", region=_region_of(win))
    print(f"实拍截图 {shot} 字节 -> {out/'01-dialog.png'}", flush=True)
    if shot and shot == base:
        print("⚠️ 实拍和基线**字节数相同** —— 很可能拍的是黑图（显示器在睡？）", flush=True)
    win.orderOut_(None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
