#!/usr/bin/env python3
"""渲染「翻译的 Key 换新了」那张一次性提示框，**截图**给人看。⚠️ **不 `runModal`**（那会挂住）。

    ClassLive.app/Contents/MacOS/python scripts/probe_keyreset.py [输出目录]

## 为什么单独一个探针

那张框 3.9.3 **只弹一次**（`entry_launch` 判 `first and moved`），而且只有在
「这台机器上真的被挪走了一把 key」时才会走到 —— 作者自己那台一旦搬过家，
就**再也看不到它了**。所以不能靠"跑一遍真流程"来验收外观，只能这样渲染出来看。

⚠️ 文案与生产**同源**（`keyentry.KEY_RESET_TITLE` / `KEY_RESET_MESSAGE`），
   框体与生产**同源**（`notice.build_alert`）—— 截出来的就是真实界面。

⚠️ **拍之前先拍一张基线**：显示器在睡时 `screencapture` 会拍出**纯黑**，
而那种图和"界面真的没渲染出来"长得一样。基线与实拍**字节数相同 = 空图**。

不在运行路径上 —— 不 import 它就什么都不会发生。
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import notice                                                        # noqa: E402

import keyentry                                                      # noqa: E402

#: 文案与生产同源；按钮也是生产那一个默认值（`alert()` 的 `buttons` 默认 `("知道了",)`）。
TITLE = keyentry.KEY_RESET_TITLE
MESSAGE = keyentry.KEY_RESET_MESSAGE
BUTTONS = ("知道了",)


def _shot(path: pathlib.Path, *, region=None) -> int:
    """截一张。`region` = `(x, y, w, h)`（**屏幕坐标，左上原点**）。返回**字节数**。"""
    cmd = ["screencapture", "-x", "-o"]
    if region is not None:
        cmd += ["-R", "%d,%d,%d,%d" % tuple(int(v) for v in region)]
    cmd.append(str(path))
    subprocess.run(cmd, check=False)
    return path.stat().st_size if path.exists() else 0


def _region_of(win) -> tuple:
    """`NSWindow` 的 frame（**左下原点**）→ `screencapture -R` 要的（**左上原点**）。"""
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
    out = pathlib.Path(argv[0]) if argv else pathlib.Path("/tmp/classlive-keyreset")
    out.mkdir(parents=True, exist_ok=True)

    from AppKit import NSApplication
    from Foundation import NSDate, NSRunLoop, NSDefaultRunLoopMode
    app = NSApplication.sharedApplication()

    base = _shot(out / "00-baseline.png")
    print(f"基线截图 {base} 字节", flush=True)

    alert, _url_btn = notice.build_alert(TITLE, MESSAGE, BUTTONS)
    # ⚠️⚠️ **这一行不能省**（2026-09-30 实测，`probe_keyentry.py` 的文件头记着）：
    #    不调 `layout()` 时窗口只有 260 宽、按钮**竖排**、附件视图根本不进视图树
    #    —— 而 `accessoryView()` 照样返回对象（"看起来设过了"）。
    #    生产走 `runModal()` 时它自己会 layout，所以**探针必须补上这一下**，
    #    否则截出来的是一张用户永远看不到的退化形态（第一版正是这么拍的）。
    alert.layout()
    win = alert.window()
    win.center()
    print("按钮:", [b.title() for b in alert.buttons()],
          "| 窗口宽", win.frame().size.width, flush=True)
    # 真实路径走 `notice._present()` 抬到模态层；探针不 runModal，自己抬，否则被别的窗口盖住。
    from AppKit import NSModalPanelWindowLevel
    win.setLevel_(NSModalPanelWindowLevel)
    win.orderFrontRegardless()
    # ⚠️ 必须**跑一会儿 runloop** 让它真的画上去 —— 建完立刻截会拍到空白
    for _ in range(10):
        NSRunLoop.currentRunLoop().runMode_beforeDate_(
            NSDefaultRunLoopMode, NSDate.dateWithTimeIntervalSinceNow_(0.05))

    shot = _shot(out / "01-popup.png", region=_region_of(win))
    print(f"实拍截图 {shot} 字节 -> {out / '01-popup.png'}", flush=True)
    if shot and shot == base:
        print("⚠️ 实拍和基线**字节数相同** —— 很可能拍的是黑图（显示器在睡？）", flush=True)
    win.orderOut_(None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
