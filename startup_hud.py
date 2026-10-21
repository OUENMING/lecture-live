"""起步首屏：在慢初始化之前先给一个看得见的小窗。

## 为什么需要它

2026-10-21 实测：双击 `.app` 点「开始上课」后，第一次 23.0 s 才见字幕
（开麦试探 8.5 s + 翻译/术语 13.7 s + 悬浮窗 0.3 s），这段时间屏幕上什么都没有；
用户会以为没点着，再去点一次 App。热启动只要 2.9 s，隔离进程 0.4 s ——
所以问题不只是“初始化慢”，还有**界面出现得太晚**。

Apple 的指导与这个判断一致：
- HIG *Launching*：macOS 不要求 launch screen；应用应当尽快可用，品牌 splash 只属于 onboarding。
- HIG *Loading*：能显示东西就先显示（placeholder / 文案），并明确告诉用户在加载。
- WWDC19 *Optimizing App Launch*：第一帧目标 400 ms；与第一帧无关的工作要推迟，
  阻塞工作移出主线程。

因此这里做的不是品牌 splash，而是**同一个磨砂面板风格的功能性首屏**：
先显示「正在准备…」，再在主线程的初始化节点更新一行状态，真悬浮窗建好后关掉。

## 边界

- 只在 `--ui overlay` 且没有 `CLASSLIVE_NO_STARTUP_HUD=1` 时启用；
  终端 / 测试 / 探针不建窗。
- 只在 `.app` 路径（`CLASSLIVE_FROM_APP`）激活一次，保证不被前台应用盖住；
  终端路径不抢焦点。
- 窗口忽略鼠标事件；启动期间用户点不到、也挡不住别的窗口。
- 任何 AppKit 失败都静默返回 `None`，绝不能因为首屏把课拦掉。
"""
from __future__ import annotations

import os

WIDTH = 360.0
HEIGHT = 72.0
MARGIN = 24.0
TITLE = "ClassLive 正在准备…"
SUB = "马上开始；第一次启动会多等一会儿"
#: 主流程会用的状态文案。`main` / 探针都从这里取，避免两边各写一份漂移。
TEXT_AUDIO = "正在加载语音模型…"
TEXT_TRANSLATOR = "正在加载翻译与术语…"
TEXT_OVERLAY = "正在准备悬浮窗…"


def _debug_exc() -> None:
    """首屏失败默认静默；排查时 CLASSLIVE_DEBUG=1 打 traceback。"""
    if os.environ.get("CLASSLIVE_DEBUG"):
        import traceback
        traceback.print_exc()


def enabled(ui_mode: str, env: dict | None = None) -> bool:
    """该不该建首屏。纯函数，给判据用。"""
    e = os.environ if env is None else env
    if e.get("CLASSLIVE_NO_STARTUP_HUD") == "1":
        return False
    return ui_mode == "overlay"


class StartupHUD:
    """一个不可交互的磨砂小窗；`show()` 之后可反复 `note()`，最后 `close()`。"""

    def __init__(self) -> None:
        self._window = None
        self._label = None
        self._closed = False
        try:
            import panel
            from AppKit import (NSColor, NSFont, NSMakeRect, NSScreen, NSTextField,
                                NSWindowStyleMaskBorderless,
                                NSWindowStyleMaskNonactivatingPanel)

            scr = NSScreen.mainScreen().visibleFrame()
            rect = NSMakeRect(
                scr.origin.x + scr.size.width - WIDTH - MARGIN,
                scr.origin.y + scr.size.height - HEIGHT - MARGIN,
                WIDTH, HEIGHT)
            fp = panel.build(
                rect,
                NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel)
            fp.window.setIgnoresMouseEvents_(True)
            self._window = fp.window
            # `fp` 不需要留住：这里没传 on_resize，`resize_delegate` 是 None；
            # 窗口由 self 持有，glass/scrim/label 由 contentView 的 subview 关系持有。

            label = NSTextField.labelWithString_(TITLE)
            label.setFrame_(NSMakeRect(18.0, 39.0, WIDTH - 36.0, 19.0))
            label.setFont_(NSFont.systemFontOfSize_(13.0))
            label.setTextColor_(NSColor.whiteColor().colorWithAlphaComponent_(0.92))
            fp.glass.addSubview_(label)
            self._label = label

            sub = NSTextField.labelWithString_(SUB)
            sub.setFrame_(NSMakeRect(18.0, 17.0, WIDTH - 36.0, 16.0))
            sub.setFont_(NSFont.systemFontOfSize_(11.0))
            sub.setTextColor_(NSColor.whiteColor().colorWithAlphaComponent_(0.55))
            fp.glass.addSubview_(sub)
        except Exception:                             # noqa: BLE001
            self._window = None
            _debug_exc()

    def show(self) -> bool:
        """显示并尽量让第一帧真的画出来。失败返回 False，不影响上课。"""
        if self._window is None or self._closed:
            return False
        try:
            from AppKit import (NSApplication, NSApplicationActivationPolicyAccessory,
                                NSDate, NSRunLoop)
            app = NSApplication.sharedApplication()
            app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
            self._window.orderFrontRegardless()
            # 只有 .app 那条路激活；终端 / 探针不抢焦点（同 `_load_overlay` 的规矩）。
            if os.environ.get("CLASSLIVE_FROM_APP"):
                app.activateIgnoringOtherApps_(True)
                self._window.orderFrontRegardless()
            self._window.displayIfNeeded()
            # 没有 run loop 时 orderFront 不会自动画；给 AppKit 一个很短的窗口。
            # 30 ms 是“让第一帧落地”的成本，不是初始化的一部分。
            NSRunLoop.currentRunLoop().runUntilDate_(
                NSDate.dateWithTimeIntervalSinceNow_(0.03))
            return True
        except Exception:                             # noqa: BLE001
            _debug_exc()
            return False

    def note(self, text: str) -> None:
        """更新标题行的状态；任何失败都吞掉。"""
        if self._closed or self._label is None:
            return
        try:
            self._label.setStringValue_(text)
            self._window.displayIfNeeded()
        except Exception:                             # noqa: BLE001
            _debug_exc()

    def close(self) -> None:
        """关掉首屏；幂等。"""
        if self._closed:
            return
        self._closed = True
        try:
            if self._window is not None:
                self._window.orderOut_(None)
        except Exception:                             # noqa: BLE001
            _debug_exc()


def open_if_enabled(ui_mode: str) -> StartupHUD | None:
    """按环境/模式建首屏；建不起来返回 None。"""
    if not enabled(ui_mode):
        return None
    hud = StartupHUD()
    if not hud.show():
        hud.close()
        return None
    return hud
