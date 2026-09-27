"""第二批 · 步 0 探针（v2）：**整块面板就是落点，有反应会变绿**。

v1 的毛病：三个小格子要瞄准，而且「没反应」和「瞄偏了」分不清。
v2 把**整个面板**做成一个落点，收到拖拽立刻**变绿 + 换大字** ——
「有没有反应」这件事不用猜，一眼就能看出。

## 它回答什么

| 现象 | 含义 |
|---|---|
| **面板变绿** | ✅ 非 key / 非激活的浮动面板**收得到**拖拽 → §3.4「卡片即拖拽目标」成立 |
| 鼠标变成禁止符号 ⃠，面板不变绿 | 拖拽**到了窗口**但被拒 → 是我们的返回值/注册类型问题 |
| 鼠标禁止符号，也没别的反应 | 拖拽**根本没送到窗口** → 窗口级路由问题，设计要改 |
| 面板边缘出现十字/缩放光标 | 你抓到的是**窗口边缘**，不是落点 |
| 什么都没变，连窗口都没动 | 你抓的**根本不是这个面板** |

## 跑法

    ClassLive.app/Contents/MacOS/python probe_drag.py            # 只判 UTI（默认）
    PROBE_MODE=url ClassLive.app/Contents/MacOS/python probe_drag.py   # 在 draggingEntered 里读 URL

日志写 `/tmp/classlive-probe-drag.log`（**不碰仓库、不碰用户数据**）。

## ⚠️ 两条口径

- **本项目不沙盒**（实测 `codesign -d --entitlements -` 只回 Executable 一行），
  所以 `PROBE_MODE=url` 那个坑大概率不适用；跑它只是为了坐实，以及它顺带验通道。
- **回调里一律 try/except 并记日志** —— 回调里抛异常会被 AppKit 吞掉，
  症状和「没收到的拖拽」一模一样。不记下来就等于没有量具。
"""
from __future__ import annotations

import os
import pathlib
import sys
import time
import traceback

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

LOG_PATH = "/tmp/classlive-probe-drag.log"
MODE = os.environ.get("PROBE_MODE", "uti").strip().lower()
W, H = 720, 440
GREEN = (0.20, 0.85, 0.35)

_logf = open(LOG_PATH, "w", buffering=1)      # noqa: SIM115 — 进程级


def log(msg: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    _logf.write(line + "\n")


S = {"status": None, "drop": None, "hits": []}


def _guard(fn):
    """回调的守卫：**异常必须落到日志里**。

    ⚠️ AppKit 会吞掉回调里的异常 —— 症状和「这个回调没被调用」完全一样。
       没有这一层，一个 `AttributeError` 会伪装成「面板收不到拖拽」。
    """
    def wrapper(self, *a, **kw):
        try:
            return fn(self, *a, **kw)
        except Exception:                                   # noqa: BLE001
            log(f"[!!] {fn.__name__} 抛异常 —— 这次观察无效：\n{traceback.format_exc()}")
            return None
    wrapper.__name__ = fn.__name__
    return wrapper


def _drop_class():
    from AppKit import (NSDragOperationCopy, NSDragOperationNone, NSView,
                        NSPasteboardURLReadingFileURLsOnlyKey, NSURL,
                        NSApplication)
    import objc_own

    def _read_urls(self, sender):
        """`nil` 是错误、空数组是「没有」—— 分开报，别合成一个判断。"""
        try:
            got = sender.draggingPasteboard().readObjectsForClasses_options_(
                [NSURL], {NSPasteboardURLReadingFileURLsOnlyKey: True})
        except Exception as e:                              # noqa: BLE001
            return f"读抛异常 {type(e).__name__}: {e}"
        if got is None:
            return "**None（=错误，不是「没有」）**"
        if len(got) == 0:
            return "[]（没有文件 URL）"
        return f"{len(got)} 个：" + "、".join(u.path() for u in got)

    def _paint(self, on, text, sub=""):
        from AppKit import NSColor
        r, g, b = GREEN
        a = 0.42 if on else 0.0
        self.layer().setBackgroundColor_(
            NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, a).CGColor())
        S["status"].setStringValue_(text)
        if S["drop"] is not None:
            S["drop"].setStringValue_(sub)

    @_guard
    def dragging_entered(self, sender):
        win = self.window()
        app = NSApplication.sharedApplication()
        types = list(sender.draggingPasteboard().types() or [])
        log(f"★ draggingEntered  key={bool(win.isKeyWindow())} "
            f"active={bool(app.isActive())} mode={MODE}")
        log(f"    types = {types}")
        if MODE == "url":
            log(f"    ★ 读 URL -> {_read_urls(self, sender)}")
            ok = True
        else:
            ok = "public.file-url" in types
            log(f"    只判 UTI -> {'收' if ok else '拒'}")
        if ok:
            _paint(self, True, "★ 收到拖拽！松手试试", "")
        else:
            _paint(self, False, "拖拽到了，但类型不是文件（看日志）", "")
        return NSDragOperationCopy if ok else NSDragOperationNone

    @_guard
    def dragging_updated(self, sender):
        return NSDragOperationCopy

    @_guard
    def dragging_exited(self, sender):
        log("draggingExited（鼠标移开了）")
        _paint(self, False, "鼠标移开了 —— 再拖进来一次", "")

    @_guard
    def prepare_for_drag(self, sender):
        log("prepareForDragOperation")
        return True

    @_guard
    def perform_drag(self, sender):
        r = _read_urls(self, sender)
        log(f"★★★ 落点！ performDragOperation -> {r}")
        S["hits"].append(r)
        _paint(self, True, "✅ 拿到了：", r)
        S["status"].setStringValue_(f"✅ 第 {len(S['hits'])} 次落点成功")
        return True

    @_guard
    def mouse_down(self, event):
        from AppKit import NSApplication
        log("【鼠标按下到了面板】—— 说明输入路由是通的")
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        return None

    cls = objc_own.own("ProbeDropWhole", NSView, {
        "draggingEntered_": dragging_entered,
        "draggingUpdated_": dragging_updated,
        "draggingExited_": dragging_exited,
        "prepareForDragOperation_": prepare_for_drag,
        "performDragOperation_": perform_drag,
        "mouseDown_": mouse_down,
    })
    return cls


def main() -> int:
    from AppKit import (NSApplication, NSApplicationActivationPolicyAccessory,
                        NSColor, NSFont, NSScreen, NSTextField, NSButton, NSObject,
                        NSWindowStyleMaskBorderless, NSWindowStyleMaskNonactivatingPanel,
                        NSPasteboardTypeFileURL)
    from Foundation import NSMakeRect

    import objc_own
    import panel

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    log("=" * 70)
    log(f"ClassLive 拖拽探针 v2   模式={MODE}")
    log("把一份 .pptx 从 Finder 拖到面板上 —— 收到就变绿并显示路径")
    log(f"日志: {LOG_PATH}")
    log("=" * 70)

    scr = NSScreen.mainScreen().frame()
    x = (scr.size.width - W) / 2.0
    y = 90.0                                   # AppKit 左下原点 → 贴在屏幕下半部
    mask = NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel
    fp = panel.build(NSMakeRect(x, y, W, H), mask)
    win = fp.window

    status = NSTextField.alloc().initWithFrame_(NSMakeRect(24.0, float(H) - 120.0,
                                                           float(W) - 48.0, 52.0))
    status.setStringValue_("等拖拽…")
    status.setEditable_(False); status.setBezeled_(False)
    status.setDrawsBackground_(False); status.setSelectable_(False)
    status.setTextColor_(NSColor.whiteColor())
    status.setFont_(NSFont.boldSystemFontOfSize_(22.0))
    status.cell().setWraps_(True)
    fp.glass.addSubview_(status)
    S["status"] = status

    drop = NSTextField.alloc().initWithFrame_(NSMakeRect(24.0, 46.0,
                                                        float(W) - 48.0, 110.0))
    drop.setStringValue_("（收到后会在这里显示文件路径）")
    drop.setEditable_(False); drop.setBezeled_(False)
    drop.setDrawsBackground_(False); drop.setSelectable_(True)
    drop.setTextColor_(NSColor.whiteColor())
    drop.setFont_(NSFont.systemFontOfSize_(12.0))
    drop.cell().setWraps_(True)
    fp.glass.addSubview_(drop)
    S["drop"] = drop

    hint = NSTextField.alloc().initWithFrame_(NSMakeRect(24.0, float(H) - 158.0,
                                                        float(W) - 48.0, 30.0))
    hint.setStringValue_(f"模式：{'在 draggingEntered 里读 URL' if MODE == 'url' else '只判 UTI，不读 URL'}"
                         f"　·　整块面板都是落点　·　拖拽期间 app 保持不激活")
    hint.setEditable_(False); hint.setBezeled_(False); hint.setDrawsBackground_(False)
    hint.setSelectable_(False)
    hint.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.55))
    hint.setFont_(NSFont.systemFontOfSize_(11.0))
    fp.glass.addSubview_(hint)

    cls = _drop_class()
    layer = cls.alloc().initWithFrame_(fp.glass.bounds())
    layer.setWantsLayer_(True)
    layer.registerForDraggedTypes_([NSPasteboardTypeFileURL])
    fp.glass.addSubview_(layer)                # 最后加 = 最上层
    log(f"落点层就绪：注册类型={list(layer.registeredDraggedTypes())} "
        f"frame={layer.frame()}")

    def on_quit(self, sender):
        _finish()

    tgt = objc_own.own("ProbeQuit2", NSObject, {"onQuit_": on_quit}).alloc().init()
    btn = NSButton.alloc().initWithFrame_(NSMakeRect(float(W) - 116.0, 10.0, 96.0, 26.0))
    btn.setTitle_("退出")
    btn.setTarget_(tgt); btn.setAction_("onQuit:")
    fp.glass.addSubview_(btn)

    win.orderFrontRegardless()
    log(f"面板已显示 key={bool(win.isKeyWindow())} active={bool(app.isActive())} "
        f"level={win.level()} frame={win.frame()}")

    from PyObjCTools import AppHelper
    AppHelper.runEventLoop()
    return 0


def _finish() -> None:
    from PyObjCTools import AppHelper
    log("=" * 70)
    if S["hits"]:
        log(f"结论：✅ 收到 {len(S['hits'])} 次落点 —— 非 key 面板能收拖拽")
        for h in S["hits"]:
            log(f"    · {h}")
    else:
        log("结论：❌ 一次落点都没有。⚠️ 但先看日志里有没有 `【鼠标按下到了面板】`：")
        log("    · 有 → 窗口收得到鼠标，但收不到拖拽 → 拖拽路由问题，设计要改")
        log("    · 没有 → 连点击都没到面板 → 你抓的可能不是这个面板（或被别的窗口压住）")
    log("=" * 70)
    _logf.close()
    AppHelper.stopEventLoop()


if __name__ == "__main__":
    sys.exit(main())
