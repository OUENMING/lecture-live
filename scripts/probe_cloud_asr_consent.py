#!/usr/bin/env python3
"""渲染「云端转写」两态（「语音模型」那一格：已就绪 / 已就绪 · 全本地）与首次告知弹窗，并截图。

    ClassLive.app/Contents/MacOS/python scripts/probe_cloud_asr_consent.py [输出目录]

默认输出到 `$TMPDIR/classlive-cloud-asr-XXXX/`（新建，路径会打印），拍三张：
`01-面板-开.png`、`02-面板-关.png`（点一下之后）、`03-告知弹窗.png`。

## 纪律（照 `probe_test_mode.py`）

⚠️ **一个字节都不写真盘。** 进程一开头把 `HOME` 指到临时目录并设 `CLASSLIVE_UPLOAD=0`
   （`paths.STATE_ROOT` 是 import 时算的，所以 **必须先于任何仓库模块 import**），
   面板的 `glossary/sessions/state_root` 也都给临时目录；`on_cloud_asr` / `on_test_mode` 是桩。
   图片写到 `$TMPDIR` 下新建的目录，不写仓库、不写真 `~/.classlive/`、不写 `sessions/`。
⚠️ **拍之前先拍一张基线**：显示器在睡时 `screencapture` 会拍出纯黑；基线与实拍字节数相同 = 空图。
⚠️ 告知弹窗只**渲染**（与 `notice.alert` 同款 NSAlert + `notice._present`），**不 `runModal`**、
   不等人点，拍完立刻 `orderOut`。

不在运行路径上 —— 不 import 它就什么都不会发生。
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile

# ⚠️ 隔离必须在 import 仓库模块之前
_HOME = tempfile.mkdtemp(prefix="classlive-probe-home-")
os.environ["HOME"] = _HOME
os.environ["CLASSLIVE_UPLOAD"] = "0"
os.environ.pop("CLASSLIVE_CLOUD_ASR", None)

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _shot(path: pathlib.Path, *, region=None) -> int:
    cmd = ["screencapture", "-x", "-o"]
    if region is not None:
        cmd += ["-R", "%d,%d,%d,%d" % tuple(int(v) for v in region)]
    cmd.append(str(path))
    subprocess.run(cmd, check=False)
    return path.stat().st_size if path.exists() else 0


def _region_of(win, pad: float = 24.0) -> tuple:
    """`NSWindow` frame（左下原点）-> `screencapture -R`（左上原点）。"""
    from AppKit import NSScreen
    f = win.frame()
    h = NSScreen.mainScreen().frame().size.height
    return (f.origin.x - pad, h - (f.origin.y + f.size.height) - pad,
            f.size.width + pad * 2, f.size.height + pad * 2)


def _pump(seconds: float = 0.5) -> None:
    from Foundation import NSDate, NSRunLoop, NSDefaultRunLoopMode
    for _ in range(max(1, int(seconds / 0.05))):
        NSRunLoop.currentRunLoop().runMode_beforeDate_(
            NSDefaultRunLoopMode, NSDate.dateWithTimeIntervalSinceNow_(0.05))


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    out = pathlib.Path(argv[0]) if argv else pathlib.Path(
        tempfile.mkdtemp(prefix="classlive-cloud-asr-"))
    out.mkdir(parents=True, exist_ok=True)
    print(f"图片目录：{out}", flush=True)

    try:
        from AppKit import (NSAlert, NSApplication,
                            NSApplicationActivationPolicyAccessory, NSButton)
        import entry_panel as E
        import final_asr
        import notice
    except Exception as e:                                    # noqa: BLE001
        print(f"⚠ 起不来：{type(e).__name__}: {e}")
        return 1

    # 双保险：HOME 隔离若没生效（STATE_ROOT 已落在真目录）就拒绝继续
    if not str(final_asr.paths.STATE_ROOT).startswith(_HOME):
        print(f"⚠ STATE_ROOT={final_asr.paths.STATE_ROOT} 不在临时 HOME 下 —— 拒绝运行")
        return 1

    NSApplication.sharedApplication().setActivationPolicy_(
        NSApplicationActivationPolicyAccessory)

    base = _shot(out / "00-baseline.png")
    print(f"基线截图 {base} 字节", flush=True)
    sizes = []

    tmp = pathlib.Path(_HOME)
    (tmp / "glossary.txt").write_text("# public table\n", encoding="utf-8")
    calls: list = []
    h = E.build(glossary=tmp / "glossary.txt", sessions_dir=tmp / "sessions",
                state_root=tmp / "state", on_start=lambda c: None,
                on_test_mode=lambda on: True, test_mode=False,   # 桩：不落盘
                on_cloud_asr=lambda on: (calls.append(on), True)[1], cloud_asr=True)
    if h is None:
        print("⚠ 面板没建起来（`CLASSLIVE_DEBUG=1` 看 traceback）")
        return 1

    def _find(prefix):
        stack, found = [h.window.contentView()], []
        while stack:
            v = stack.pop()
            stack.extend(v.subviews() or [])
            if isinstance(v, NSButton) and (v.title() or "").startswith(prefix):
                found.append(v)
        return found

    try:
        h.window.orderFrontRegardless()
        _pump()
        # 10-12 起「云端转写」在「语音模型」那一格的菜单里：换掉弹菜单那一步，直接点菜单项
        #（菜单本身的样子要真屏拍，见 tests/test_entry_panel.py ⑳b 的说明）。
        cell = [b for b in _find("") if "语音模型" in (b.title() or "")]
        if len(cell) != 1:
            print(f"⚠ 找到 {len(cell)} 个「语音模型」格")
            return 1
        sizes.append(_shot(out / "01-面板-开.png", region=_region_of(h.window)))
        print(f"实拍（开）{sizes[-1]} 字节 -> {out / '01-面板-开.png'}", flush=True)
        menus = []
        _orig_popup, E.popup_menu = E.popup_menu, lambda m, v: menus.append(m)
        try:
            cell[0].performClick_(None)
        finally:
            E.popup_menu = _orig_popup
        item = next((menus[-1].itemAtIndex_(i) for i in range(menus[-1].numberOfItems())
                     if "云端转写" in (menus[-1].itemAtIndex_(i).title() or "")), None) if menus else None
        if item is None:
            print("⚠ 菜单里没有「云端转写」")
            return 1
        item.target().act_(item)
        _pump(0.3)
        sizes.append(_shot(out / "02-面板-关.png", region=_region_of(h.window)))
        print(f"实拍（关）{sizes[-1]} 字节 -> {out / '02-面板-关.png'}   回调={calls}"
              f"   那一格={cell[0].title()!r}", flush=True)
    finally:
        h.close()

    # 首次告知弹窗：与 `notice.alert` 同款（NSAlert + `_present`），只渲染、不 runModal
    a = NSAlert.alloc().init()
    a.setMessageText_("定稿转写会用云端")
    a.setInformativeText_(final_asr.consent_text())
    a.addButtonWithTitle_("好的")
    try:
        # ⚠️ NSAlert 不 `runModal` 就不会排版（第一版拍出文字被截成两行、按钮错位、
        #    冒出「<Do not show this」的半成品）→ 先 `layout()` 让它按真实内容排一遍。
        a.layout()
        notice._present(a.window())
        _pump(0.6)
        sizes.append(_shot(out / "03-告知弹窗.png", region=_region_of(a.window())))
        print(f"实拍（弹窗）{sizes[-1]} 字节 -> {out / '03-告知弹窗.png'}", flush=True)
    finally:
        a.window().orderOut_(None)

    if base and any(n == base for n in sizes):
        print("⚠️ 实拍和基线字节数相同 —— 很可能拍的是黑图（显示器在睡？）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
