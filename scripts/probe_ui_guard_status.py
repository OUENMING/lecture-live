#!/usr/bin/env python3
"""焦点守卫与状态行的**行为探针** —— 两条 UI 判据的测量口（要人跑，check.py 不收）。

    ClassLive.app/Contents/MacOS/python scripts/probe_focus_guard.py
    ClassLive.app/Contents/MacOS/python scripts/probe_status_line.py

为什么单独有这两个探针（CLAUDE.md 的完成判据里「要人去跑的」那一档）：

1. **焦点守卫**（`entry_panel._guard_start` / `_guard_tick`）：它修的是一个
   「代码看着对、行为一次都没发生」的缺陷 —— 首拍早于 `orderFrontRegardless()`，
   于是 `isVisible()` 为假、`return` 把末尾的 `callLater` 也一起跳过，docstring 里
   那条不变量**从来没生效过**（2026-10-12 全仓审查核实）。`tests/test_entry_panel.py`
   的 ⑲d 用假窗口钉住每一拍该做什么；**真窗口 + 真 run loop** 只在这里量。
2. **状态行**（`entry_panel.set_status`）：它的第二参数是 `alpha`，而调用点从
   2026-09-29 起按「显示秒数 / 0 = 不自动消失」传 —— 三处 `, 0)` 把那句话设成
   **全透明**（用户看不见「正在验证…」和课表推测的理由）。这里直接读控件的
   `textColor().alphaComponent()`：旧实现 0.00，现在 1.00。

两者都用隔离的 `state_root`（写端不碰真 `~/.classlive/`），跑完自动关窗口。
"""
from __future__ import annotations

import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from AppKit import NSApplication, NSDate, NSRunLoop                    # noqa: E402

NSApplication.sharedApplication()

import entry_panel as E                                                # noqa: E402


def _open_isolated():
    iso = pathlib.Path(tempfile.mkdtemp(prefix="cl_ui_probe_"))
    (iso / "state").mkdir(parents=True, exist_ok=True)
    return E.open_panel(state_root=iso / "state"), iso


def _find_status_label(view):
    """面板里那句提示文字 —— 按它的**初始文案**找（`status_holder` 是 build 局部变量）。"""
    try:
        if hasattr(view, "stringValue") and str(view.stringValue() or "").startswith("拖课件到某张卡上"):
            return view
    except Exception:                                                  # noqa: BLE001
        pass
    for sub in list(view.subviews() or []):
        got = _find_status_label(sub)
        if got is not None:
            return got
    return None


def probe_focus_guard() -> int:
    print("=== ① 焦点守卫 ===")
    h, _iso = _open_isolated()
    if h is None:
        print("❌ 面板没建起来")
        return 1
    win = h.window
    print(f"刚开完： visible={win.isVisible()}  gen={E._GUARD['gen']}  "
          f"guard_win={'有' if E._GUARD['win'] is not None else '无'}")
    NSRunLoop.mainRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(1.0))
    print(f"跑 1 秒后： waits={E._GUARD['waits']}（该是 0 —— 窗口可见，链子在正常跑）")
    win.makeKeyWindow()
    print(f"手动弄成 key： isKeyWindow={win.isKeyWindow()}  "
          f"是否在编辑={E._win_is_editing(win)}")
    NSRunLoop.mainRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(1.5))
    ok = not win.isKeyWindow()
    print(f"再跑 1.5 秒： isKeyWindow={win.isKeyWindow()}  "
          f"{'✅ 守卫把键盘还出去了' if ok else '❌ 还赖着 —— 守卫没跑'}")
    h.close()
    print(f"close() 之后： guard_win={'有' if E._GUARD['win'] is not None else '无'}"
          f"（该是「无」）")
    return 0 if ok else 1


def probe_status_line() -> int:
    print("\n=== ② 状态行看得见吗 ===")
    h, _iso = _open_isolated()
    if h is None:
        print("❌ 面板没建起来")
        return 1
    lb = _find_status_label(h.window.contentView())
    if lb is None:
        print("❌ 没找到状态行标签（初始文案改了？本探针按「拖课件到某张卡上」找）")
        h.close()
        return 1
    print(f"初始： {str(lb.stringValue())[:18]!r}  alpha={lb.textColor().alphaComponent():.2f}")
    E._status("课表推测：ECON10740 —— 课表里刚上过、课件也在")
    a1 = lb.textColor().alphaComponent()
    E._status("正在验证…")
    a2 = lb.textColor().alphaComponent()
    print(f"写完状态句： alpha={a1:.2f}    「正在验证…」： alpha={a2:.2f}")
    ok = a1 == 1.0 and a2 == 1.0
    print("✅ 两句都看得见（alpha 1.0）" if ok
          else "❌ alpha 不是 1.0 —— 文字是全透明/半透明的（旧实现那三处就是 0.00）")
    h.close()
    return 0 if ok else 1


if __name__ == "__main__":
    rc = probe_focus_guard()
    rc |= probe_status_line()
    sys.exit(rc)
