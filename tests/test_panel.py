#!/usr/bin/env python3
"""磨砂面板：配方抽出来之后，两个调用方**行为一模一样**。

    ClassLive.app/Contents/MacOS/python tests/test_panel.py

三组断言，各管一件不同的事：

① **属性** —— 构造出来的窗口/材质/scrim 逐项对。覆盖 `level` / `collectionBehavior`
   这些**离屏渲染看不见**的窗口层属性。
② **离屏渲染哈希** —— 内容层（材质 / DarkAqua / 圆角 / masksToBounds / scrim 透明度）
   有没有变。⚠️ 量具自身实测过灵敏度：它对上面五个属性敏感，
   而**对 `level` 那类窗口层属性是瞎的** —— 所以①和②缺一不可，不能只靠像素。
③ **单进程同时装两个消费者** —— 这条**以前从来没人测过**，而它正是 2026-09-26
   那次事故的现场：overlay 与 whatsnew 各自定义 `_Panel`，ObjC 按名字全局注册，
   第二个抛 `overriding existing Objective-C class`，被 fail-soft 吞掉 →
   卡片静默变 None，而**当时的独立测试全绿**（那些测试里 overlay 没被 import）。

⚠️ 构造真 `Overlay` 会往仓库根写 `.window`（窗口尺寸记忆）。按「测试必须隔离写端」
   的规矩，这里**先备份、跑完还原**，绝不留下痕迹。
"""
from __future__ import annotations

import contextlib
import hashlib
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  {detail}" if detail else ""))


def render_hash(window) -> str:
    """离屏渲染内容区，返回哈希。

    ⚠️ 同一个面板渲两次哈希是**确定**的（实测两个进程都是 `af7aef7457c1b8f8`），
       所以可以拿来当等价比对。
    """
    cv = window.contentView()
    rep = cv.bitmapImageRepForCachingDisplayInRect_(cv.bounds())
    cv.cacheDisplayInRect_toBitmapImageRep_(cv.bounds(), rep)
    data = bytes(rep.bitmapData()[: rep.bytesPerRow() * rep.pixelsHigh()])
    return hashlib.sha256(data).hexdigest()


# ⚠️ overlay 那边重构前的基线（改动前实测，见提交信息）
BASELINE_OVERLAY = "af7aef7457c1b8f8"
BASELINE_WHATSNEW = "cd20b263f4226bf9"


@contextlib.contextmanager
def restore_window_file():
    """隔离 overlay 的写端：`.window` 跑完必须还原。"""
    f = HERE / ".window"
    saved = f.read_bytes() if f.exists() else None
    try:
        yield
    finally:
        if saved is not None:
            f.write_bytes(saved)
        elif f.exists():
            f.unlink()


def main() -> int:
    from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    import panel
    from Quartz import CGColorGetAlpha

    print("\n--- ① panel.build 的属性 ---")
    from AppKit import (NSWindowStyleMaskBorderless, NSWindowStyleMaskClosable,
                        NSWindowStyleMaskFullSizeContentView, NSWindowStyleMaskNonactivatingPanel,
                        NSWindowStyleMaskResizable, NSWindowStyleMaskTitled)
    from Foundation import NSMakeRect

    MASK_OV = (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
               | NSWindowStyleMaskResizable | NSWindowStyleMaskFullSizeContentView
               | NSWindowStyleMaskNonactivatingPanel)
    fp = panel.build(NSMakeRect(0, 0, 660, 330), MASK_OV, on_resize=lambda: None)
    w, g, s, d = fp.window, fp.glass, fp.scrim, fp.drag
    check("appearance 强制 DarkAqua", w.appearance().name() == "NSAppearanceNameDarkAqua",
          str(w.appearance().name()))
    check("material = HUDWindow(13)", g.material() == 13, str(g.material()))
    check("state = Active(1)", g.state() == 1, str(g.state()))
    check("cornerRadius = 16", g.layer().cornerRadius() == 16.0, str(g.layer().cornerRadius()))
    check("masksToBounds", bool(g.layer().masksToBounds()))
    check("scrim alpha = 0.38",
          abs(CGColorGetAlpha(s.layer().backgroundColor()) - 0.38) < 1e-6,
          f"{CGColorGetAlpha(s.layer().backgroundColor()):.3f}")
    check("level = Floating(3)", w.level() == 3, str(w.level()))
    check("collectionBehavior = CanJoinAllSpaces(1)", w.collectionBehavior() == 1,
          str(w.collectionBehavior()))
    check("opaque = False", not w.isOpaque())
    check("movableByWindowBackground = True", bool(w.isMovableByWindowBackground()))
    check("drag.mouseDownCanMoveWindow = True", bool(d.mouseDownCanMoveWindow()))
    check("glass 子视图 z 序 = [scrim, drag]",
          [type(v).__name__ for v in g.subviews()] == ["NSView", "_ClassLiveDragLayer"],
          str([type(v).__name__ for v in g.subviews()]))
    check("resize_delegate 已装到窗口上",
          fp.resize_delegate is not None and w.delegate() is fp.resize_delegate)

    print("\n--- ② 离屏渲染哈希（对不过 = 内容层变了）---")
    h = render_hash(w)
    check(f"overlay 面板渲染与重构前一致", h.startswith(BASELINE_OVERLAY),
          f"{h[:16]} vs 基线 {BASELINE_OVERLAY}")

    fp2 = panel.build(NSMakeRect(0, 0, 300, 120),
                      NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel)
    h2 = render_hash(fp2.window)
    check("whatsnew 面板渲染与重构前一致", h2.startswith(BASELINE_WHATSNEW),
          f"{h2[:16]} vs 基线 {BASELINE_WHATSNEW}")
    check("不传 on_resize 就不装 delegate", fp2.resize_delegate is None)

    print("\n--- ③ 单进程同时装两个消费者（那次事故的现场）---")
    import overlay
    import whatsnew
    check("单进程 import overlay + whatsnew 不炸", True)
    with restore_window_file():
        ov = overlay.Overlay()
        live = ov._panel
        check("真 Overlay 构造出来的面板属性也对",
              live.appearance().name() == "NSAppearanceNameDarkAqua"
              and ov._ve.material() == 13
              and ov._drag_layer.mouseDownCanMoveWindow()
              and ov._win_delegate is not None,
              f"{live.appearance().name()} / material={ov._ve.material()}")
        # ⚠️ Overlay 的窗口与独立 build 的**不是同一个类吗**？是同一个 —— 闩锁保证。
        check("overlay 用的是同一个 ObjC 面板类", type(live) is type(w),
              f"{type(live).__name__}")
        ov.close()
    # ⚠️ 打开这个开关：`whatsnew.build` 有 fail-soft 的 `except`（那是它的既定设计 ——
    #    卡片只是说明，不能因为它让课起不来），但它**已经两次把真 bug 藏起来**
    #    （ObjC 类名撞车、不可变的 NSAttributedString）。调试开关会让真异常露出来。
    os.environ["CLASSLIVE_DEBUG"] = "1"
    card = whatsnew.build("0.0.0", "测试摘要", date="2026-09-26")
    check("同一进程里 whatsnew.build() 不是 None（是 None = 那次事故复现了）",
          card is not None)
    if card is not None:
        cp = card.get("panel")
        check("卡片窗口是真窗口且 chrome 对",
              cp is not None and cp.appearance().name() == "NSAppearanceNameDarkAqua",
              str(cp.appearance().name()) if cp else "panel 不在返回值里")
        cp.orderOut_(None)

    bad = [n for n, ok, _ in RESULTS if not ok]
    print("\n" + "=" * 60)
    print(f"{len(RESULTS) - len(bad)}/{len(RESULTS)} 通过")
    if bad:
        print("失败：")
        for n in bad:
            print(f"  ❌ {n}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
