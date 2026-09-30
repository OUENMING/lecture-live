#!/usr/bin/env python3
"""磨砂面板：配方抽出来之后，行为与**抽取前那份配方**逐项相同。

    ClassLive.app/Contents/MacOS/python tests/test_panel.py

## 判据长什么样

**不是**「跟一个写死的哈希比」，而是**同进程对拍**：
把抽取前那份配方**逐字冻在下面**（`frozen_recipe`），当场再建一个面板，
两边用**同一份枚举**（`dump` + `render_hash`）比。

这样做的两个好处（2026-09-26 由 altitude 审查指出原来的写法有问题）：

1. **不绑机器**。原来的判据是 sha256(离屏位图) 写死一个常量 ——
   它对 backing scale / 系统版本敏感，而 CLAUDE.md 把它当仓库闸门 →
   换台 Mac 必假失败。现在两边跑在**同一台机器**上，比的是「有没有差别」。
2. **不靠我预先挑**。原来是「手挑 13 个属性 + 一个哈希」，
   而手挑的清单**不是闭集** —— `hasShadow` / `contentMinSize` / `titleVisibility` /
   `isMovable` 两条腿都盖不住（同一个审查指出）。现在两边比的是**同一份 `dump`**，
   名单里有什么就比什么；名单随「又发现一个可观察量」加长。

⚠️ **仍然要说清楚它盖不住什么**，别把「两条腿」讲得比实际强：
这是**静态摊平**，所以**行为**（`sendEvent_` 的分派、拖拽、live resize）
不在这里面 —— `sendEvent_` 单独由第 ⑤ 组钉住；拖拽与 live resize 至今没有自动化覆盖。

⚠️ 构造真 `Overlay` 会往仓库根写 `.window`（窗口尺寸记忆）。按「测试必须隔离写端」
的规矩，这里**先备份、跑完还原**，绝不留下痕迹。
"""
from __future__ import annotations

import contextlib
import hashlib
import os
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  {detail}" if detail else ""))


# ══════════════════════════════════════════════════════════════════════
# 冻结的参照物：抽取**之前**那份配方
# （c76cac4 的父提交里的 overlay.py：`_make_panel` + 构造块 + 拖拽层，逐字抄来）
#
# ⚠️ **别改它。** 它是「纯搬家」的参照物 —— 将来真要改 panel.py 的配方，
#    这条测试会红，那正是要的：逼你想清楚「这次是有意改行为，还是改坏了」。
# ⚠️ 它也用 `objc_own` 取名，**测试里不手挑 ObjC 类名** ——
#    那正是 objc_own 要消灭的东西，参照物没理由是例外。
# ══════════════════════════════════════════════════════════════════════
def frozen_recipe(rect, style):
    """抽取前的磨砂面板配方。返回 `(window, glass, scrim, drag)`。"""
    from AppKit import (NSAppearance, NSAppearanceNameDarkAqua, NSBackingStoreBuffered,
                        NSColor, NSFloatingWindowLevel, NSPanel, NSView,
                        NSVisualEffectMaterialHUDWindow, NSVisualEffectStateActive,
                        NSVisualEffectView, NSWindowCollectionBehaviorCanJoinAllSpaces)
    import objc
    import objc_own

    def send_event(self, event):
        if event.type() == 1:                            # NSEventTypeLeftMouseDown
            try:
                from AppKit import NSApplication
                NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
            except Exception:                            # noqa: BLE001
                pass
        objc.super(objc_own.cls_of("FrozenPanel"), self).sendEvent_(event)

    panel_cls = objc_own.own("FrozenPanel", NSPanel, {
        "canBecomeKeyWindow": lambda self: True,
        "sendEvent_": send_event,
    })
    win = panel_cls.alloc().initWithContentRect_styleMask_backing_defer_(
        rect, style, NSBackingStoreBuffered, False)
    win.setAppearance_(NSAppearance.appearanceNamed_(NSAppearanceNameDarkAqua))
    win.setLevel_(NSFloatingWindowLevel)
    win.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces)
    win.setOpaque_(False)
    win.setBackgroundColor_(NSColor.clearColor())
    win.setMovableByWindowBackground_(True)

    content = win.contentView()
    glass = NSVisualEffectView.alloc().initWithFrame_(content.bounds())
    glass.setMaterial_(NSVisualEffectMaterialHUDWindow)
    glass.setState_(NSVisualEffectStateActive)
    glass.setWantsLayer_(True)
    glass.layer().setCornerRadius_(16.0)
    glass.layer().setMasksToBounds_(True)
    content.addSubview_(glass)

    scrim = NSView.alloc().initWithFrame_(glass.bounds())
    scrim.setWantsLayer_(True)
    scrim.layer().setBackgroundColor_(
        NSColor.blackColor().colorWithAlphaComponent_(0.38).CGColor())
    glass.addSubview_(scrim)

    drag_cls = objc_own.own("FrozenDrag", NSView, {
        "mouseDownCanMoveWindow": lambda self: True,
    })
    drag = drag_cls.alloc().initWithFrame_(glass.bounds())
    glass.addSubview_(drag)
    return win, glass, scrim, drag


# ══════════════════════════════════════════════════════════════════════
# 可观测量摊平
# ══════════════════════════════════════════════════════════════════════
def _pair(s) -> tuple:
    return (round(float(s.width), 3), round(float(s.height), 3))


def _rect(r) -> tuple:
    return (round(float(r.origin.x), 3), round(float(r.origin.y), 3),
            round(float(r.size.width), 3), round(float(r.size.height), 3))


def _cg(c):
    """CGColor → 四元组。⚠️ 转回 NSColor 再走 `_ns` —— Quartz 只导出了
    `CGColorGetAlpha` / `CGColorGetComponents`，没有 `CGColorGetRed` 那几个
    （而且灰度空间的 CGColor 分量个数也不一样）。绕这一下两个问题一起躲开。"""
    if c is None:
        return None
    from AppKit import NSColor
    return _ns(NSColor.colorWithCGColor_(c))


def _ns(c):
    """NSColor → 四元组。

    ⚠️ 必须先转色彩空间：`clearColor` 是 **Generic Gray** 空间，
       直接调 `.redComponent()` 会抛 `getRed:green:blue:alpha: not valid for
       the NSColor … colorspace`（第一次跑就撞上了）。
    """
    if c is None:
        return None
    from AppKit import NSColorSpace
    for space in (NSColorSpace.sRGBColorSpace(), NSColorSpace.genericRGBColorSpace()):
        c2 = c.colorUsingColorSpace_(space)
        if c2 is not None:
            return (round(c2.redComponent(), 4), round(c2.greenComponent(), 4),
                    round(c2.blueComponent(), 4), round(c2.alphaComponent(), 4))
    return ("unconvertible", str(c.colorSpaceName()))


def _tree(v) -> dict:
    import objc_own
    lay = v.layer()
    # ⚠️ **NSVisualEffectView 自己的三个属性也要比** —— 它们是配方的一部分。
    #
    #    ⚠️ **这三项加进来不是为了「抓得到」**（实测：删掉 `panel.py` 的 `setState_`，
    #    「离屏渲染与老配方一致」那条腿**本来就会红**，2 条断言、12/14）。
    #    加它是因为**原来报的信息没用** —— 只告诉你「两个哈希不同」，
    #    你得自己回去二分是哪一项。现在报的是 `effect.state: 0 vs 1`，直接点到项。
    #
    #    ⚠️⚠️ **但别把离屏的差值当成用户看得见的差值 —— 我犯过这个错**：
    #    离屏渲染里 `Active` 与默认的 `FollowsWindowActiveState` 差 2.3 倍
    #    （均亮 57.4 → 24.7，2026-09-26 实测），我当时据此写下
    #    「删掉那行面板会暗 2.3 倍」。**上屏实测推翻了它**：
    #    白底 168.7 vs 168.7、深底 27.3 vs 27.3 —— **两种 state 上屏完全一样**。
    #    离屏渲染没有「窗口背后是什么」可合成，那个差值是这个退化情形的产物。
    #    → 结论：**离屏对拍是有效的回归探测器（确定性、敏感），但它的幅度不是
    #      用户在屏幕上看到的幅度**。红了不等于用户看得出。
    #
    #    （没有 `material` 属性的视图就是普通 NSView，记 None。）
    try:
        effect = {"material": v.material(), "blendingMode": v.blendingMode(),
                  "state": v.state()}
    except AttributeError:
        effect = None
    return {
        # ⚠️ 类名归一成 "custom"：两边用的是各自的 key（`DragLayer` vs `FrozenDrag`），
        #    比原始类名会永远不等。层级、顺序、层的属性照比。
        "cls": ("custom" if type(v).__name__.startswith(objc_own._PREFIX)
                else type(v).__name__),
        "frame": _rect(v.frame()),
        "hidden": bool(v.isHidden()),
        "effect": effect,
        "layer": None if lay is None else {
            "cornerRadius": lay.cornerRadius(),
            "masksToBounds": bool(lay.masksToBounds()),
            "bg": _cg(lay.backgroundColor()),
        },
        "subviews": [_tree(s) for s in v.subviews()],
    }


def dump(win) -> dict:
    """摊平一个面板的**可观测量**，供两边对拍。

    ⚠️ 这是一份**枚举**，不是「闭集证明」—— 名单外的属性它看不见。
       但它由**两边同一份代码**跑，所以在名单里的任何差异都会露出来。
       `hasShadow` / `contentMinSize` / `titleVisibility` / `isMovable` 是
       2026-09-26 审查指出后补进来的（原来两条腿都盖不住）。
    """
    return {
        "appearance": win.appearance().name(),
        "styleMask": win.styleMask(),
        "level": win.level(),
        "collectionBehavior": win.collectionBehavior(),
        "opaque": win.isOpaque(),
        "backgroundColor": _ns(win.backgroundColor()),
        "alphaValue": win.alphaValue(),
        "movable": bool(win.isMovable()),
        "movableByWindowBackground": bool(win.isMovableByWindowBackground()),
        "hasShadow": bool(win.hasShadow()),
        "ignoresMouseEvents": bool(win.ignoresMouseEvents()),
        "titleVisibility": win.titleVisibility(),
        "titlebarAppearsTransparent": bool(win.titlebarAppearsTransparent()),
        "minSize": _pair(win.minSize()),
        "maxSize": _pair(win.maxSize()),
        "contentMinSize": _pair(win.contentMinSize()),
        "contentMaxSize": _pair(win.contentMaxSize()),
        "contentResizeIncrements": _pair(win.contentResizeIncrements()),
        "canBecomeKey": bool(win.canBecomeKeyWindow()),
        "canBecomeMain": bool(win.canBecomeMainWindow()),
        "contentView": _tree(win.contentView()),
    }


def diff(a: dict, b: dict, path: str = "") -> list[str]:
    """返回两份摊平结果的所有差异（含嵌套路径）。"""
    out: list[str] = []
    for k in sorted(set(a) | set(b)):
        p = f"{path}.{k}" if path else k
        if k not in a or k not in b:
            out.append(f"{p}: 只有一边有")
        elif isinstance(a[k], dict) and isinstance(b[k], dict):
            out += diff(a[k], b[k], p)
        elif isinstance(a[k], list) and isinstance(b[k], list):
            if len(a[k]) != len(b[k]):
                out.append(f"{p}: 长度 {len(a[k])} vs {len(b[k])}")
            else:
                for i, (x, y) in enumerate(zip(a[k], b[k])):
                    if isinstance(x, dict) and isinstance(y, dict):
                        out += diff(x, y, f"{p}[{i}]")
                    elif x != y:
                        out.append(f"{p}[{i}]: {x!r} vs {y!r}")
        elif a[k] != b[k]:
            out.append(f"{p}: {a[k]!r} vs {b[k]!r}")
    return out


def render_hash(window) -> str:
    """离屏渲染内容区。⚠️ **只用来和同一台机器上另一个面板比**，
    绝不写死成常量（原写法对 backing scale / 系统版本敏感，换机器必假失败）。"""
    cv = window.contentView()
    rep = cv.bitmapImageRepForCachingDisplayInRect_(cv.bounds())
    cv.cacheDisplayInRect_toBitmapImageRep_(cv.bounds(), rep)
    return hashlib.sha256(bytes(rep.bitmapData()[: rep.bytesPerRow() * rep.pixelsHigh()])
                          ).hexdigest()[:16]


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
    from AppKit import (NSApplication, NSApplicationActivationPolicyAccessory,
                        NSWindowStyleMaskBorderless, NSWindowStyleMaskClosable,
                        NSWindowStyleMaskFullSizeContentView, NSWindowStyleMaskNonactivatingPanel,
                        NSWindowStyleMaskResizable, NSWindowStyleMaskTitled)
    from Foundation import NSMakeRect

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    import objc
    import objc_own
    import panel

    MASK_OV = (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
               | NSWindowStyleMaskResizable | NSWindowStyleMaskFullSizeContentView
               | NSWindowStyleMaskNonactivatingPanel)
    MASK_WN = NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel

    print("\n--- ①② 与「抽取前的配方」同进程对拍 ---")
    for tag, rect, mask, kw in (
            ("overlay 参数",  NSMakeRect(0, 0, 660, 330), MASK_OV,
             {"on_resize": lambda: None}),
            ("whatsnew 参数", NSMakeRect(0, 0, 300, 120), MASK_WN, {})):
        fp = panel.build(rect, mask, **kw)
        old = frozen_recipe(rect, mask)[0]
        d = diff(dump(fp.window), dump(old))
        check(f"{tag}：摊平后的可观测量逐项相同", not d,
              "；".join(d[:4]) if d else f"{len(dump(fp.window))} 项")
        ha, hb = render_hash(fp.window), render_hash(old)
        check(f"{tag}：离屏渲染与老配方一致", ha == hb, f"{ha} vs {hb}")

    print("\n--- ③ 单进程同时装两个消费者（那次事故的现场）---")
    import overlay
    import whatsnew
    # ⚠️ 原来这里是 `check("单进程 import overlay + whatsnew 不炸", True)` —— **恒真**
    #    （2026-09-28 审查指出）：上面两句 import 真要炸，进程早就 traceback 崩了，
    #    根本轮不到这一行；它把"导入没抛"伪装成"验证了两个消费者能共存"。
    #    真判据在下面（`overlay.Overlay()` 真的构造出来 + 与老配方对拍）。
    #    这里只断言**两个模块都带着自己的对象工厂**——事故现场是 ObjC 类名撞车。
    check("两个模块都导进来了、且各自的对象工厂在",
          callable(getattr(overlay, "Overlay", None))
          and callable(getattr(whatsnew, "skip_flag_path", None)),
          f"overlay.Overlay={getattr(overlay, 'Overlay', None)}")
    with restore_window_file():
        ov = overlay.Overlay()
        # ⚠️ 这里**不能整体对拍**：真 Overlay 在配方之外还自己加了几样 ——
        #    `setContentMinSize_`、Titled 的 titleVisibility + titlebarAppearsTransparent、
        #    `_layout()` 把拖拽层铺成它自己的尺寸。那些是**它的** chrome，不是配方的，
        #    所以只比配方负责的那几项 + 内容层的**结构**。
        ref_dump = dump(frozen_recipe(NSMakeRect(0, 0, 660, 330), MASK_OV)[0])
        live_dump = dump(ov._panel)
        _KEYS = ("appearance", "styleMask", "level", "collectionBehavior", "opaque",
                 "backgroundColor", "alphaValue", "movableByWindowBackground", "canBecomeKey")
        dd = diff({k: live_dump[k] for k in _KEYS}, {k: ref_dump[k] for k in _KEYS})
        check("真 Overlay 的面板：配方负责的 9 项与老配方一致", not dd,
              "；".join(dd[:3]) if dd else "9 项")

        # ⚠️ 只比**配方负责的那两层**：contentView 的子视图（glass），
        #    以及 glass 的**头两个**子视图（scrim、drag，顺序即 z 序）。
        #    真 Overlay 在 glass 里还加了字幕/滚动区/按钮 —— 整棵树本来就不该相等。
        def _head(t, depth=2):
            if depth == 0:
                return t["cls"]
            return (t["cls"], [_head(s, depth - 1) for s in t["subviews"][:2]])
        check("真 Overlay 的配方层结构一致（contentView → glass → [scrim, drag]）",
              _head(live_dump["contentView"]) == _head(ref_dump["contentView"]),
              f"{_head(live_dump['contentView'])}")
        check("真 Overlay 的拖拽层能拖窗口",
              bool(ov._drag_layer.mouseDownCanMoveWindow()))

        # ⭐ 与上面那条**互补**：术语行那次点击**真的会触发回调**。
        #    为什么必须有它：`mouseDownCanMoveWindow` 是本仓库咬过两次的雷区
        #    （窗口四角缩放 / `NSSplitView` 的 pane），而 `ClickView` 的默认值是
        #    `True` —— **只看 flag 值会得出「它是坏的」这个错误结论**。
        #    2026-09-28 实测（`/tmp/probe_gloss_click_v3.py`，含量具自测）：
        #    视图**自己实现了 `mouseDown_`** 时 AppKit 把事件交给它、不拖窗口
        #    （红/绿两态都响；`postEvent_` 走真实派发路径也响）。
        #    → 那条雷区的**正确机制**是「不接管 mouseDown 的新视图才要显式设 False」，
        #      不是「所有新视图」。所以这里钉**行为**，不钉 flag。
        #    ⚠️ 没覆盖的一条：app **处于激活态**时没测过（那要 `activateIgnoringOtherApps_`，
        #      会抢用户焦点）。本 app 是 `.accessory`、面板是 NonactivatingPanel，
        #      正常使用不会走到那个状态。
        try:
            from AppKit import NSApplication, NSEvent, NSLeftMouseDown
            ov._terms = [("Marginal cost", "边际成本")]
            ov._layout()
            ov._panel.setFrameOrigin_((-4000.0, 0.0))   # 离屏显示: 不闪, 但必须真 isVisible
            ov._panel.orderFrontRegardless()
            _gh = ov._gloss_hit.frame()
            _pt = (_gh.origin.x + _gh.size.width / 2.0,
                   _gh.origin.y + _gh.size.height / 2.0)
            _hit = ov._panel.contentView().hitTest_(_pt)
            _ev = NSEvent.mouseEventWithType_location_modifierFlags_timestamp_windowNumber_context_eventNumber_clickCount_pressure_(
                NSLeftMouseDown, _pt, 0, 0.0, ov._panel.windowNumber(), None, 0, 1, 1.0)
            NSApplication.sharedApplication().sendEvent_(_ev)
            check("⭐ 术语行点击真的触发回调（钉行为，不钉 flag 值）",
                  ov._pinned_term is not None,
                  f"落点={_hit.__class__.__name__ if _hit else None} "
                  f"flag={bool(ov._gloss_hit.mouseDownCanMoveWindow())} "
                  f"_pinned_term={ov._pinned_term}")
        except Exception as e:                          # noqa: BLE001
            check("⭐ 术语行点击真的触发回调（钉行为，不钉 flag 值）", False,
                  f"{type(e).__name__}: {e}")

        # ⭐ `_open_prep`（菜单栏「开课前的准备…」）起不来时**不许默认静默**。
        #    `PLAN-entry-panel §8.1 #14`：那一段原来只在 `CLASSLIVE_DEBUG` 下才出声 →
        #    **默认路径上用户点了那一项、什么都没发生、也没留下任何痕迹**。
        #    ⚠️ 这里钉的是"**留了痕迹**"，不是"痕迹长什么样"。
        import io as _io
        import contextlib as _ctx
        import entry_panel as _EPmod
        _orig_open = _EPmod.open_panel

        def _boom(*a, **k):
            raise RuntimeError("桩：面板起不来")
        _EPmod.open_panel = _boom
        try:
            _buf = _io.StringIO()
            with _ctx.redirect_stderr(_buf):
                ov._open_prep()
            _err = _buf.getvalue()
        finally:
            _EPmod.open_panel = _orig_open
        check("⭐ 面板起不来时**留了痕迹**（不是默认静默 —— §8.1 #14）",
              "面板起不来" in _err and "RuntimeError" in _err, repr(_err[:100]))

        # ⭐ 菜单栏那一项必须钉住。`_install_status_item` 整段在 try/except fail-soft 里
        #    （那是**对的** —— 图标不能因为建菜单项失败就整个消失），但后果是
        #    **写坏了完全静默**：图标还在，菜单少一项，而那一项恰好是
        #    「上课中加课件的唯一入口」。
        #    ⚠️ 拿不到 status item / 菜单时**报红**，不跳过 ——
        #       「验不了」和「验过了」必须分开（同 `readiness` 那条 None vs 0）。
        _menu = ov._status.menu() if ov._status is not None else None
        _titles = [mi.title() for mi in _menu.itemArray()] if _menu is not None else None
        # ⚠️ 2026-09-30：列表里多了「课堂纲要」（计划 §9.9），插在准备之前。
        #    判据的**意图没变** —— 钉的是「`开课前的准备…` 不许静默少一项」，
        #    不是那个固定长度。所以这里比对**完整列表**，改动一眼能看见。
        check("菜单栏有「开课前的准备…」（不是静默少一项）",
              _titles == ["开启鼠标穿透", "课堂纲要", "开课前的准备…", "退出"], str(_titles))
        # ⭐⭐ T25（计划 §9.9 标的风险）：`NSMenu` 默认 `autoenablesItems = True`，
        #    它会**按 target 响不响应 action 自动改 `enabled`** ——
        #    而我们的 target 是响应的 → 有可能把 `setEnabled_(False)` **覆盖掉**。
        #    ⚠️ 这条**必须实测**，不能靠读文档：`isEnabled()` 在 autoenable 生效前
        #    读到的可能还是我们设的那个值（**假绿**）。这里让 AppKit 真的走一遍
        #    菜单校验（`menu.update()`），再读。
        _m = ov._mi_outline
        check("⭐ 菜单栏有「课堂纲要」", _m is not None and _m.title() == "课堂纲要")
        if _m is not None:
            ov.set_trans_mode("raw")
            try:
                ov._status.menu().update()
            except Exception:                              # noqa: BLE001
                pass
            _raw_enabled = _m.isEnabled()
            ov.set_trans_mode("both")
            try:
                ov._status.menu().update()
            except Exception:                              # noqa: BLE001
                pass
            _both_enabled = _m.isEnabled()
            # 改坏：把 `set_trans_mode` 里那句 `setEnabled_(mode != "raw")` 删掉 -> 上面那条红
            check("⭐⭐ 纯转录档下菜单项**真的置灰**（autoenable 没把它覆盖掉）",
                  _raw_enabled is False, f"raw 档 isEnabled={_raw_enabled}")
            check("⭐ 切回双语后**恢复可点**", _both_enabled is True,
                  f"both 档 isEnabled={_both_enabled}")

        # ⭐ 窄面板下**可见**按钮不许跑到面板外。
        #    ⚠️ 宽度从 `MIN_WIDTH` **派生**，不许写死 —— 写死的话下限一改断言就腐坏
        #    （而且会变成"钉住一个过时的宽度"）。260928 实测：7 个按钮里 6 个可见，
        #    它们 + 间距需要 286px、加两侧 pad = 318px，所以 MIN_WIDTH 从 280 抬到 320。
        #    ⚠️ `_width` 是布局的输入，改完要还原。
        _w0, _h0 = ov._width, ov._height
        # ⭐ 章节条（计划 §9.8）的宽度扫描断言 —— 喂一条章，否则它一直是空的。
        ov.summary_update({"kind": "chapter", "chapter": {
            "id": 0, "status": "final", "title": "A Rather Long Chapter Title Here",
            "title_zh": "题", "t0": "15:05:27", "t1": "15:16:35", "lo": 1, "hi": 9,
            "sentences": []}})
        ov._outline_new = False
        try:
            # ⚠️ **必须带一个小数宽度**（455.5）—— 否则「`floor` 生效」那条**没有区分能力**：
            #    320/360/455/900 这几个宽度下可用宽度**碰巧都是整数**，
            #    把 `math.floor` 删掉判据照样绿（我第一版就是这么假绿的）。
            #    455.5 未取整是 120.5 ✅ 真能判。live resize 之后宽度本来就是小数。
            for _w in (overlay.MIN_WIDTH, overlay.MIN_WIDTH + 40.0, 360.0, 455.0,
                       455.5, 900.0):
                ov._width = _w
                ov._layout()
                _off = [(b.title(), round(b.frame().origin.x, 1)) for b in ov._bar
                        if not b.isHidden() and b.frame().origin.x < overlay.PAD]
                check(f"宽 {_w:.1f} 时没有可见按钮挤进左边距", not _off, str(_off))
                # ---- 章节条 ----
                _lb = ov._chapter_lbl
                if _lb.isHidden():
                    check(f"宽 {_w:.1f} 时章节条隐藏（可用宽度不够）", True, "")
                    continue
                # ⚠️ 这条**挡不住**把 `avail = (x+gap) - pad` 写成 `x - pad` ——
                #    那样只会让条**窄 6px**、不会压到按钮（⚠️ **计划 §1.3① 的措辞反了**：
                #    它说那样会"多算一个 gap"、压到最左按钮，实测是**少** 6px）。
                #    留着它是因为**不重叠**才是真正要守的那条。
                _bx = _lb.frame().origin.x + _lb.frame().size.width
                _hit = [b.title() for b in ov._bar
                        if not b.isHidden() and b.frame().origin.x < _bx]
                check(f"⭐⭐ 宽 {_w:.1f} 时章节条**不压到任何可见按钮**", not _hit, str(_hit))
                check(f"⭐ 宽 {_w:.1f} 时章节条从左边距起", _lb.frame().origin.x == overlay.PAD,
                      str(_lb.frame().origin.x))
                # 改坏：把 `math.floor(avail)` 换成 `avail` -> **在 455.5 那一档**红。
                check(f"⭐⭐ 宽 {_w:.1f} 时章节条宽度是**整数**（`floor` 生效；不取整文字会发虚）",
                      float(_lb.frame().size.width).is_integer(),
                      str(_lb.frame().size.width))
                check(f"⭐ 宽 {_w:.1f} 时点击区与标签**同框**（同一个数，别各算各的）",
                      ov._chapter_hit.frame().size.width == _lb.frame().size.width
                      and ov._chapter_hit.frame().origin.x == _lb.frame().origin.x,
                      f"hit={ov._chapter_hit.frame()} lbl={_lb.frame()}")
        finally:
            ov._width, ov._height = _w0, _h0
            ov._layout()
        # ⚠️ 可用宽度**不够 90** 时必须隐藏（计划 §9.8 的三个条件之一）。
        try:
            ov._width = 340.0
            ov._layout()
            _avail = ov._chapter_lbl.frame().size.width if not ov._chapter_lbl.isHidden() else 0.0
            check("⭐ 窄到放不下时章节条**隐藏**（而不是画成半截）",
                  ov._chapter_lbl.isHidden() or _avail >= overlay.CHAPTER_MIN_W,
                  f"hidden={ov._chapter_lbl.isHidden()} w={_avail}")
        finally:
            ov._width, ov._height = _w0, _h0
            ov._layout()
        ov.close()
    # ⚠️ `CLASSLIVE_DEBUG` 是**进程级**副作用 —— 不还原的话，同一进程里后面的断言
    #    都带着「上一次的调试环境」跑（debug 开/关会改变被测代码的日志与分支），
    #    可能把只在 debug 关闭时出现的回归遮掉（OCR 指出）。
    _dbg_prev = os.environ.get("CLASSLIVE_DEBUG")
    os.environ["CLASSLIVE_DEBUG"] = "1"
    try:
        card = whatsnew.build("0.0.0", "测试摘要", date="2026-09-26")
    finally:
        if _dbg_prev is None:
            os.environ.pop("CLASSLIVE_DEBUG", None)
        else:
            os.environ["CLASSLIVE_DEBUG"] = _dbg_prev
    check("同一进程里 whatsnew.build() 不是 None（是 None = 那次事故复现了）",
          card is not None)
    if card is not None:
        cp = card.get("panel")
        check("卡片窗口是真窗口且 chrome 对",
              cp is not None and cp.appearance().name() == "NSAppearanceNameDarkAqua")
        cp.orderOut_(None)

    print("\n--- ④ 撞名守卫：要炸，而且不能被吞 ---")
    from AppKit import NSObject
    probe_name = objc_own._PREFIX + "ProbeCollide"
    try:
        objc.lookUpClass(probe_name)
    except Exception:                                   # noqa: BLE001 — nosuchclass_error
        type(probe_name, (NSObject,), {})
    try:
        objc_own.own("ProbeCollide", NSObject, {})
        check("名字被占时 own() 抛错", False, "没抛 = 守卫失效")
    except objc_own.ObjcNameCollision:
        check("名字被占时 own() 抛 ObjcNameCollision", True)
    except Exception as e:                              # noqa: BLE001
        check("名字被占时 own() 抛 ObjcNameCollision", False, f"抛的是 {type(e).__name__}")

    # ⚠️ 关键那条：whatsnew 的 fail-soft **不能**把它吞成 None。
    _orig = panel.build

    def _boom(*a, **kw):
        raise objc_own.ObjcNameCollision("测试用：模拟撞名")

    panel.build = _boom
    try:
        whatsnew.build("0.0.0", "撞名测试", date="2026-09-26")
        check("撞名不会被 whatsnew 的 fail-soft 吞成 None", False, "被吞了 —— 事故会复现")
    except objc_own.ObjcNameCollision:
        check("撞名不会被 whatsnew 的 fail-soft 吞成 None", True)
    except Exception as e:                              # noqa: BLE001
        check("撞名不会被 whatsnew 的 fail-soft 吞成 None", False, f"抛的是 {type(e).__name__}")
    finally:
        panel.build = _orig

    print("\n--- ⑤ sendEvent_ 真能分派（不是只「能解析」）---")
    from AppKit import NSEvent
    fp3 = panel.build(NSMakeRect(0, 0, 200, 100), MASK_OV)
    ev = NSEvent.mouseEventWithType_location_modifierFlags_timestamp_windowNumber_context_eventNumber_clickCount_pressure_(
        1, (10.0, 10.0), 0, 0.0, 0, None, 0, 1, 1.0)
    try:
        fp3.window.sendEvent_(ev)
        check("左键事件走通 sendEvent_（激活 + objc.super 分派）", True)
    except Exception as e:                              # noqa: BLE001
        check("左键事件走通 sendEvent_（激活 + objc.super 分派）", False,
              f"{type(e).__name__}: {e}")
    check("cls_of('Panel') 就是面板类", objc_own.cls_of("Panel") is type(fp3.window))

    print("\n--- ⑥ entry_panel 的卡片：内容不许溢出卡片 ---")
    # ⭐ 这是 `card_height()` 那条推导**真正的锁**。
    #    `card_height(None) == CARD_H` 不是锁 —— 实测把整个算式换成写死的 `h = 88.0`
    #    照样全绿（硬编码的 88 恰好等于正确值）。而「建出卡片、量有没有子视图超出」
    #    不管你把常数改成什么、也不管高度是不是算出来的，都成立。
    import entry_panel as EP
    import courses as _c

    def _r(code="ECON10740", title="Exploring Economics"):
        return _c.Readiness(code, title, 36, 0, 0, "2026-09-22")

    import prep as _prep        # 只为造**生产真形状**的失败项（`FileReport`）
    for tag, ent in (("无结果", None),
                     ("1 条结果", {"added": ["elasticity"], "removed": set()}),
                     ("5 条 + 已删 1 + 撤销",
                      {"added": list("abcde"), "removed": {"b"},
                       "undo": {"text": "b"}}),
                     ("全删光（0 条但仍要显示头部）",
                      {"added": ["a"], "removed": {"a"}, "undo": {"text": "a"}}),
                     # ⭐ **这一条才是真正的探测器。** 行内容与卡片高度之间有一圈
                     #    **恒定的余量** —— 2026-09-28 实测 = **10.0pt**（就是那个
                     #    `CARD_PAD`，与行数**无关**：n=0/1/5/20 都是 10.0）。
                     #    ⚠️ 本注释原写「~54pt（`CARD_GAP_V` + `RESULT_TAIL` 那块）」——
                     #    两个数都站不住：实测是 10；而 **`RESULT_TAIL` 这个符号全仓不存在**
                     #    （只在注释里），是本文件第 ⑧ 类"死引用"。
                     #    ⚠️ 原作者写的「把行距 +12 / 把高度算少 20，前四个用例照样全绿」
                     #    **我没能复现其机制**（10pt 余量看着不该容得下 +12/行）——
                     #    **不替它背书**，留在这里等下一次有人真去查。
                     #    20 行（一门课加 40 个词是常态，所以这是真实数量级）
                     #    被当成能累积到暴露漂移的那一档。
                     ("20 条（漂移探测器）",
                      {"added": [f"term{i:02d}" for i in range(20)],
                       "removed": set()}),
                     # ⭐ **新三档行**（失败逐文件 / 没加逐条 / 没跑完的人话）：
                     #    它们**必须真的出现在夹具里** —— 原来四个夹具全是 `failed: []`，
                     #    于是"没有子视图超出卡片高度"那条断言对这三档**一条都盖不到**。
                     ("失败+没加+没跑完（新三档行）",
                      {"added": ["a"],
                       "failed": [_prep.FileReport(
                           path=pathlib.Path("/x/讲义.docx"), status="unsupported",
                           chars=0, blocks=0, skipped_shapes=0, ocr_pages=0,
                           error="不支持的格式：.docx"),
                           _prep.FileReport(
                           path=pathlib.Path("/x/b.pdf"), status="unreadable",
                           chars=0, blocks=0, skipped_shapes=0, ocr_pages=0,
                           error="ValueError: boom")],
                       "not_added": ["term-x", "term-y"],
                       "aborted": "all_files_failed",
                       "removed": set()})):
        card = EP._make_card(_r(), on_start=None,
                             on_drop_files=lambda c, p: False, width=640.0,
                             entry=ent, on_delete=lambda t: None, on_undo=lambda: None)
        ch = float(card.frame().size.height)
        over = [(type(v).__name__, round(float(v.frame().origin.y
                                             + v.frame().size.height), 1))
                for v in card.subviews()
                if float(v.frame().origin.y + v.frame().size.height) > ch + 0.01]
        check(f"{tag}：没有子视图超出卡片高度（{ch:.0f}）", not over, str(over[:4]))
        # ⚠️ 必须和 `_make_card` 传**同一个 `has_actions`** —— 它这轮传的是
        #    `on_start=None`（不画按钮），高度就少了 `CARD_GAP_V + BTN_H` 那一块。
        #    2026-09-28 之前「选择文件…」永远在，所以这个参数不存在。
        check(f"{tag}：高度与 card_height(has_actions=False) 一致",
              abs(ch - EP.card_height(ent, has_actions=False)) < 0.01,
              f"{ch} vs {EP.card_height(ent, has_actions=False)}")
        # ⚠️⚠️ **上面两条都抓不到「`card_height` 与行循环脱钩」**：
        #    · 「高度一致」是**同义反复** —— 卡片的 frame 本来就是 `card_height(entry)` 设的；
        #    · 「没有子视图超出高度」只看**顶端** —— 而 `card_height` **少算**时所有行
        #      **整体下移**（`y` 从 `card_height - CARD_PAD` 起算），顶端不会超，
        #      倒是**底部掉出卡片**（负坐标）。
        #    → 少算是**危险方向**，补这一条把它钉住。（2026-09-28 加 `failed`/`not_added`/
        #      `aborted` 三档行时才查清这套断言的覆盖边界；作者原注说"没有子视图超出高度"
        #      是"真正的锁"，实际它锁的是"行有没有参与 y 链"，不是"高度算得对不对"。）
        _low = min(float(v.frame().origin.y) for v in card.subviews())
        check(f"{tag}：没有子视图掉出卡片底部（最低 y={_low:.1f}）", _low >= -0.01,
              f"最低 y={_low:.1f} —— 高度少算了？")

    # ⭐ **按钮画不画，卡片高度要跟着变** —— 2026-09-28 删掉卡片上的「选择文件…」
    #    之后才暴露出来的：`card_height` 原来**不管按钮画不画都留 `BTN_H`**，
    #    而以前「选择文件…」永远在，所以看不出来；删掉之后 `on_start is None`
    #    （上课中从菜单栏打开的面板）那一档的卡片下半截就是**空白**。
    check("card_height：画按钮比不画正好多 CARD_GAP_V + BTN_H",
          abs((EP.card_height() - EP.card_height(has_actions=False))
              - (EP.CARD_GAP_V + EP.BTN_H)) < 0.01,
          f"{EP.card_height()} vs {EP.card_height(has_actions=False)}")

    # ⭐⭐ **阅读顺序**：把文字标签按 y 从高到低排，必须与期望的阅读顺序一致。
    #     2026-09-26 真出过这个 bug —— 结果块从卡片底部往上长，于是
    #     **列表是倒的**（最后加的排最上面）、**头部跑到了列表下面**。
    #     上一条「没有子视图超出去」**抓不到**它：溢出和顺序是两件事。
    from AppKit import NSTextField as _TF

    def _reading_order(card):
        items = [(float(v.frame().origin.y), v.stringValue())
                 for v in card.subviews() if isinstance(v, _TF)]
        return [t for _, t in sorted(items, reverse=True)]

    e1 = {"added": ["第一", "第二", "第三"], "removed": set(), "undo": None}
    c1 = EP._make_card(_r(), on_start=None,
                       on_drop_files=lambda c, p: False, width=640.0, entry=e1,
                       on_delete=lambda t: None, on_undo=lambda: None)
    check("⭐⭐ 从上到下：课名 → 准备度 → 结果头 → 逐条（**正序**）",
          _reading_order(c1) == [EP.card_title(_r()), EP.readiness_line(_r()),
                                 EP.result_header(e1), "第一", "第二", "第三"],
          str(_reading_order(c1)))

    # ⭐⭐ 结果区的行必须是 **1 行 + 省略号**（`NSLineBreakByTruncatingTail` == 4）。
    #     ⚠️ 不传 `truncate=True` 时 `make_label` 拿到的是 **`wraps=True` + `byWordWrapping`**
    #     （2026-09-28 实测，不是"硬切"）→ 超长文本**静默换行、第二行起被吃掉，且没有省略号**
    #     —— 看着像句子就到这儿了。这条钉的是"会不会被静默吃掉"，不是样式。
    #     ⚠️ 只挑**结果区那三条**来判（标题/准备度/头部本来就走默认，不该按这条要求）。
    _long_term, _long_not = "很长的词条名" * 20, "另一个很长的术语" * 20
    e3 = {"added": [_long_term], "removed": set(),
          "failed": [_prep.FileReport(path=pathlib.Path("/x/讲义.docx"),
                                      status="unsupported", chars=0, blocks=0,
                                      skipped_shapes=0, ocr_pages=0,
                                      error="不支持的格式：.docx" * 12)],
          "not_added": [_long_not]}
    c3 = EP._make_card(_r(), on_start=None,
                       on_drop_files=lambda c, p: False, width=640.0, entry=e3,
                       on_delete=lambda t: None, on_undo=lambda: None)
    _want = {_long_term, EP.failure_text(e3["failed"][0]), _long_not}
    _texts = [v.stringValue() for v in c3.subviews() if isinstance(v, _TF)]
    _notrunc = [t[:14] for t in _want
                if t in _texts
                and next(v for v in c3.subviews()
                         if isinstance(v, _TF) and v.stringValue() == t
                         ).cell().lineBreakMode() != 4]
    check("⭐ 结果区的长行是 1 行 + 省略号（不是被静默换行吃掉第二行）",
          not _notrunc and not (_want - set(_texts)),
          f"未截断={_notrunc} 没找到={_want - set(_texts)}")

    e2 = {"added": ["甲", "乙"], "removed": {"乙"}, "undo": {"text": "乙"}}
    seq2 = _reading_order(EP._make_card(
        _r(), on_start=None,
        on_drop_files=lambda c, p: False, width=640.0, entry=e2,
        on_delete=lambda t: None, on_undo=lambda: None))
    check("⭐ 删过的那条不再列；「已删除」+「撤销」在**最下面**",
          seq2 == [EP.card_title(_r()), EP.readiness_line(_r()),
                   EP.result_header(e2), "甲", "已删除 乙"], str(seq2))

    print("\n--- ⑦ 删 / 撤销：**点得到底**（接线，不是被调用的那个函数）---")
    # ⚠️⚠️ 这一组存在的唯一理由：**单元测试全绿接线也可能是断的。**
    #     2026-09-26 实测：`_make_card` 调的是 `on_delete(t)`（只传词），而
    #     `do_delete(course, term)` 要两个参数 → `TypeError` **被 AppKit 吞掉** →
    #     **点「删」静默无效**。而 `remove_terms` 自己的 95 条测试**全绿** ——
    #     它们测的是「被调用的那个函数」，不是接线。
    #     同一轮还挖出 `glossary_of` 写死 `root/"glossary"`，导致 `build(glossary=…)`
    #     这个注入点根本没生效（**验收跑器以为在改 /tmp 的副本，实际指向真实 glossary**）。
    import shutil as _sh
    import tempfile as _tf
    import entry_panel as EP2

    iso = pathlib.Path(_tf.mkdtemp(prefix="cl-test-del-"))
    real = HERE / "glossary" / "ECON10740.txt"
    real_before = real.read_bytes()
    try:
        (iso / "courses").mkdir()
        _sh.copytree(HERE / "glossary", iso / "glossary")
        (iso / "glossary.txt").write_text("", encoding="utf-8")
        gpath = iso / "glossary" / "ECON10740.txt"
        before = gpath.read_text(encoding="utf-8")
        # ⚠️ 先判空再取 `[0]` —— 否则隔离副本一旦没有可删的词，整组断言以 **IndexError
        #    崩掉**，跑器分不清「产品坏了」和「测试数据空了」（OCR 指出）。
        _terms = [x.strip() for x in before.splitlines()
                  if x.strip() and not x.startswith("#")]
        check("隔离副本里至少有一条术语可删（否则下面那组测不了）",
              bool(_terms), f"{len(_terms)} 条：{before!r}")
        term = _terms[0] if _terms else ""

        def _card_btns(root, course, title):
            """那门课卡片上、按**视觉顺序**（y 降序）排的按钮。

            ⚠️ **不能靠遍历顺序** —— 用 `stack.pop()` 递归是 LIFO，顺序是反的：
              `[0]` 会拿到**最后一行**的按钮。症状极具欺骗性（点了「删」、
              状态行说另一个词不在表里，看着像产品 bug，其实是跑器点错了）。
              视觉顺序只能从几何 `frame().origin.y` 来。
            """
            from AppKit import NSButton
            stack = [root]
            while stack:
                v = stack.pop()
                if getattr(v, "_course", None) == course:
                    hits = [(float(b.frame().origin.y), b) for b in v.subviews()
                            if isinstance(b, NSButton) and b.title() == title]
                    return [b for _, b in sorted(hits, key=lambda x: x[0],
                                                 reverse=True)]
                try:
                    stack.extend(v.subviews())
                except Exception:                                 # noqa: BLE001
                    pass
            return []

        def _click(b):
            """⚠️ `performClick_(None)` 在**不起事件循环**的进程里不派发（实测：
            按钮 enabled、target/action 都在、也在窗口里，点了什么都不发生）。
            走 `NSApp.sendAction_to_from_` —— AppKit 自己派发时走的那一步。"""
            from AppKit import NSApplication
            return bool(NSApplication.sharedApplication().sendAction_to_from_(
                b.action(), b.target(), b))

        EP2.close_panel()
        EP2.S["result"].clear()
        EP2.S["result"]["ECON10740"] = {"added": [term], "not_added": [],
                                        "failed": [], "removed": set(), "undo": None}
        h2 = EP2.open_panel(glossary=iso / "glossary.txt", state_root=iso)
        check("entry_panel 起得来（隔离 glossary）", h2 is not None)
        if h2 is not None:
            dels = _card_btns(h2.window.contentView(), "ECON10740", "删")
            check("卡片上有「删」按钮且派发得动", len(dels) == 1 and _click(dels[0]),
                  f"找到 {len(dels)} 个")
            after = gpath.read_text(encoding="utf-8")
            check("⭐ 点「删」-> **隔离副本**里那一行真的没了",
                  term not in after.splitlines() and term in before.splitlines())
            # ⚠️⚠️ **这条必须在「删」之后立刻判，不能只在最后判。**
            #     2026-09-26：我把 `glossary_of` 变异回「写死 root/glossary」来验上面那条，
            #     结果删除**真的落在了真 glossary 上** —— 而末尾那条检查**报绿**，
            #     因为「撤销」已经把它逐字放回去了（末尾再比当然相同）。
            #     → **判据要在事情发生的那一刻成立，不是等一切回滚之后再判。**
            check("⚠️ 点「删」的那一刻真 glossary 就没被碰（隔离是真的）",
                  real.read_bytes() == real_before)

            # ⭐⭐ **重建必须是推迟的。** 作者 2026-09-26 报「删到最后一个会卡顿 →
            #     删/撤销都点不动 → 第二次又正常」：在按钮 action 里同步拆掉
            #     那个按钮自己所在的视图树，而 `NSButton` 的点击是在 `NSCell` 的
            #     **模态跟踪循环**里回调的 —— 循环还在栈上、视图已经没了 → 主线程卡住。
            #     → 点完**立刻查**应当查不到重建结果；pump 一轮之后才查到。
            check("⭐ 点完「删」立刻查 -> 还没重建（证明真的推迟了，不是同步拆的）",
                  len(_card_btns(h2.window.contentView(), "ECON10740", "撤销")) == 0,
                  "同步重建了 —— 那正是会卡住的那个写法")

            def _pump(sec=0.3):
                """跑一小会儿主 runloop，让 `_later()` 排的回调真的执行。

                ⚠️ 不 pump 的话 `AppHelper.callAfter` 一直排队里 —— 测试会看到
                   「推迟了但永远不发生」，而那是个假绿。
                """
                from AppKit import NSDate, NSRunLoop
                NSRunLoop.mainRunLoop().runUntilDate_(
                    NSDate.dateWithTimeIntervalSinceNow_(sec))

            _pump()
            undos = _card_btns(h2.window.contentView(), "ECON10740", "撤销")
            check("⭐ pump 一轮之后出现「撤销」按钮（HIG：删东西用撤销，不用确认框）",
                  len(undos) == 1, f"找到 {len(undos)} 个")
            # ⭐ **接线**：⑩ 组只证明"那个函数会滚"，这条证明**`refresh` 真的调了它** ——
            #    意图是**一次性**的，重画之后必须被 `pop` 掉；留着没消费就是没接上。
            check("⭐ 「删」之后那个一次性滚动意图**已被 `refresh` 消费**",
                  EP2.S.get("_undo_scroll") is None, repr(EP2.S.get("_undo_scroll")))
            if undos:
                _click(undos[0])
                _pump()
                check("⭐⭐ 点「撤销」-> 副本**逐字回到原样**",
                      gpath.read_text(encoding="utf-8") == before)
        check("⚠️ 真 glossary 逐字节未变（隔离真的成立）",
              real.read_bytes() == real_before)

        # ⚠️⚠️ `setTarget_` 是**弱引用** —— target 一被 GC，`target()` 变 None，
        #     症状是「点了完全没反应，也不报错」（2026-09-26 作者实测「关闭无反应」）。
        #     → 必须**先 gc.collect() 再查**，否则这条测不到那个 bug。
        import gc as _gc
        _gc.collect()

        def _all_btns(root):
            from AppKit import NSButton
            out, stack = [], [root]
            while stack:
                v = stack.pop()
                if isinstance(v, NSButton):
                    out.append(v)
                try:
                    stack.extend(v.subviews())
                except Exception:                                 # noqa: BLE001
                    pass
            return out

        # ⚠️ 这两条原来在 `if h2 is not None` **外面**（2026-09-28 审查指出）：
        #    面板起不来时会走到 `h2.window` → `AttributeError` → **整个进程崩掉**，
        #    后面的断言全不跑、"失败"退化成"崩溃且不可读"（连 `n/N 通过` 都没有）。
        if h2 is not None:
            closes = [b for b in _all_btns(h2.window.contentView())
                      if b.title() == "关闭"]
            check("⭐「关闭」的 target 还在（没被 GC）—— 弱引用的经典坑",
                  len(closes) == 1 and closes[0].target() is not None,
                  f"target={[str(b.target()) for b in closes]}")
            if closes and closes[0].target() is not None:
                _click(closes[0])
                check("⭐ 点「关闭」-> 窗口真的关掉了",
                      not bool(h2.window.isVisible()))

        # ⭐⭐⭐ **生产路径：`state_root=None`。** 这一条是本轮最该有的测试。
        #     隔离跑器一直传 `state_root=ISO`，把生产那支**整个绕过**了 ——
        #     而那一支里 `run_prep` 的参数名 `paths` **遮蔽了模块 `paths`**：
        #     `paths.materials_dir(course)` 变成「对列表取属性」→ `AttributeError`，
        #     且它发生在 `S["busy"]=True` **之后** → 异常一逃出，`busy` 永久卡 True
        #     → **这个进程再也拖不动**；而症状在拖拽路径上还被异常守卫吞掉。
        #     ⚠️ 教训：**隔离跑器会把生产路径整个绕过 —— 两种都得跑。**
        #     ⚠️ 本组**只读**真 `~/.classlive`（`prepare_fn` 是桩，不写任何东西）。
        called: dict = {}

        def _stub(course, files, *, on_progress=None):
            called["course"] = course
            called["files"] = list(files)
            if on_progress:
                on_progress("build", 1, 1)
            return type("R", (), {"added": [], "not_added": [], "failed": [],
                                  "aborted": False, "notes_added": None})()

        EP2.close_panel()
        EP2.S["result"].clear()
        EP2.S.pop("busy", None)
        h4 = EP2.open_panel(prepare_fn=_stub)       # ← **不传 state_root / glossary**
        check("state_root=None（生产那支）面板也起得来", h4 is not None)
        if h4 is not None:
            _cards, _stack = [], [h4.window.contentView()]
            while _stack:
                v = _stack.pop()
                if getattr(v, "_course", None):
                    _cards.append(v)
                try:
                    _stack.extend(v.subviews())
                except Exception:                                 # noqa: BLE001
                    pass
            check("有卡片可以落", len(_cards) > 0, f"{len(_cards)} 张")
            if _cards:
                _fake = ["/tmp/（桩不会读它）不存在的文件.pdf"]
                check("⭐ 落点返回 True（没在 busy 之前抛异常）",
                      bool(_cards[0]._on_drop(_fake)))
                from AppKit import NSDate, NSRunLoop
                # ⚠️ 用**截止时间**而不是固定轮数（原来 40×0.05s = 2s 挂钟预算）：
                #    机器一忙、callAfter 排队一慢就会假红，而且假红看起来像真卡死。
                _dl = time.monotonic() + 15.0
                while time.monotonic() < _dl:
                    NSRunLoop.mainRunLoop().runUntilDate_(
                        NSDate.dateWithTimeIntervalSinceNow_(0.05))
                    if not EP2.S.get("busy"):
                        break
                check("⭐⭐ `busy` 回到 False（**没卡死** —— 卡死就再也拖不动）",
                      not EP2.S.get("busy"),
                      f"busy={EP2.S.get('busy')}；等了 15s 仍是 True = 真卡死")
                check("⭐⭐ 桩真的被调到了（说明整条生产路径走通了）",
                      called.get("course") == _cards[0]._course
                      and called.get("files") == _fake, str(called))
        EP2.close_panel()
    finally:
        EP2.close_panel()
        # ⚠️⚠️ **隔离失效时要还原现场**（2026-09-28 审查指出）：本文件开头那条
        #    「测试必须隔离写端……先备份、跑完还原」原来只做到**备份 + 检查**，
        #    缺了「还原」这半 —— 万一 `glossary_of` 回归写死成真实路径，
        #    这一节会**真的改掉用户手写的词表**，而测试只报一条红就完了
        #    （上面那几条 `real_before` 断言正是为这种情况写的，但它们**不会修**）。
        #    这里做逐字节兜底还原：没变就什么都不做。
        try:
            if real.exists() and real.read_bytes() != real_before:
                real.write_bytes(real_before)
                print("  ⚠️ 真 glossary 被动过 —— 已按内存备份**逐字节还原**"
                      "（说明隔离失效了，去查 `glossary_of`）")
        except OSError as e:
            print(f"  ❗ 真 glossary 被改过，而还原失败：{e}")
            print(f"     ⚠️ 备份只有内存这一份（{len(real_before)} 字节），别关这个终端")
        _sh.rmtree(iso, ignore_errors=True)

    print("\n--- ⑧ 开/关不许漏面板（闭包成环）---")
    # ⚠️ 审查代理量出来的：开/关 3 轮后 `{'Panel': 3, 'DropTarget': 15, …}` —— **每轮漏一整个面板**。
    #    环有多条：`win._entry_targets` → target → `do_close` → win；
    #    卡片 `_targets` → refresh/do_delete/do_undo → doc/ve/win；
    #    以及**卡片落点回调** → `run_prep` → `set_status` → 状态标签 → superview(ve) → 子树 → 卡片
    #    （最后这条不含 win，所以窗口能走，但它是个**孤岛**，gc 收不回）。
    #    → `do_close` 必须把这几条全断掉，并 `close()`（`orderOut_` 不释放窗口）。
    #    ⚠️ 判据是**不随轮数增长**，不是「等于 0」：最后一轮那份会被 AppKit 自己留着。
    def _ours():
        import gc as _g
        _g.collect(); _g.collect()
        return sum(1 for o in _g.get_objects()
                   if type(o).__name__.startswith("_ClassLive"))

    counts = []
    for rounds in (3, 9):
        for _ in range(rounds):
            _hh = EP2.open_panel(state_root=iso)
            if _hh is not None:
                _hh.close()
        counts.append(_ours())
    check(f"⭐ 开/关 3 轮与 9 轮后存活对象**一样多**（{counts}）—— 不随轮数增长",
          counts[0] == counts[1], str(counts))

    print("\n--- ⑨ 拖拽契约：收不收**由返回值决定**（原来零覆盖）---")
    # ⚠️⚠️ 这一组是**本文件第一次能自动回答"拖进来会发生什么"**。
    #    原来全仓唯一碰拖拽的断言直接调 Python 回调（`_on_drop(_fake)`）——
    #    于是 `draggingEntered:` 的返回值、`draggingUpdated:` 的无条件 Copy、
    #    `performDragOperation:` 的 default-False，**一条都没被测过**。
    #    契约就是「收不收由**返回值**决定」，而返回值**不需要真拖拽、不需要事件循环、
    #    不需要窗口** —— 桩 sender + 假 pasteboard 就够。
    from AppKit import NSDragOperationCopy as _COPY, NSDragOperationNone as _NONE

    class _URL:
        def __init__(self, p):
            self._p = p
        def path(self):
            return self._p

    class _PB:
        """假 pasteboard。

        ⚠️ **必须实现 `types()`** —— `panel.dragging_entered` 里那行
        `_log(f"…types={list(pb.types() …)}")` 的 f-string 是**提前求值**的
        （在 `_log` 检查 `CLASSLIVE_DEBUG` **之前**），而那行**在 `_call` 的 try 外面**
        → 假 pb 缺 `types()` 会让 `draggingEntered:` **抛异常逃出去**。
        ⚠️ `readObjectsForClasses_options_` 也必须有：`file_paths` 缺了会走 except →
        返回 `None`（="读失败"，与 `[]` 刻意分开）→ 于是你会**自然地测到拒收**。
        """
        def __init__(self, paths):
            self._paths = paths
        def types(self):
            return ["public.file-url"]
        def readObjectsForClasses_options_(self, cls, opts):
            return [_URL(p) for p in self._paths]

    class _Sender:
        def __init__(self, pb):
            self._pb = pb
        def draggingPasteboard(self):
            return self._pb

    def _mk(paths, on_drop="unset"):
        calls = []
        def _e(pb):
            from extract import is_supported
            return any(is_supported(p) for p in (panel.file_paths(pb) or []))
        if on_drop == "unset":                      # 忘写 return 的那种回调
            def _d(ps):
                calls.append(ps)
        else:
            _d = on_drop
        return panel.make_drop_target(_e, _d), _Sender(_PB(paths)), calls

    _t, _s, _ = _mk(["/x/讲义.pdf"])
    check("支持的输入 -> `draggingEntered:` 收", _t.draggingEntered_(_s) == _COPY)
    check("收下之后 `draggingUpdated:` **也**是收（回放 entered 的决定）",
          _t.draggingUpdated_(_s) == _COPY)

    # ⚠️ 2026-09-28：**`.docx` 进支持集了**（作者定），所以这里的"不支持"样本
    #    换成 `.txt`。原来钉的是 `.docx` —— 那条判据没坏，是**被支持集的变化作废了**。
    _t2, _s2, _ = _mk(["/x/notes.txt"])
    check("⭐ 不支持的输入（`.txt`）-> `draggingEntered:` 拒",
          _t2.draggingEntered_(_s2) == _NONE)
    check("⭐⭐ enter 拒了之后 `draggingUpdated:` **必须也拒**"
          "（原来无条件返 Copy —— `§9.2 #5` 的 P/1）",
          _t2.draggingUpdated_(_s2) == _NONE)

    _t3, _s3, _ = _mk(["/x/a.pdf", "/x/Downloads"])
    check("⭐ 混合输入（1 支持 + 1 目录）-> ≥1 个支持就收"
          "（HIG 的子集语义 + 失败逐文件）", _t3.draggingEntered_(_s3) == _COPY)

    _t4, _s4, _c4 = _mk(["/x/a.pdf"])
    check("⭐ 回调返回 `None` -> **算收**（调用方忘写 `return` 时不许说成『没收下』）",
          _t4.performDragOperation_(_s4) is True)
    check("  而且回调**真的被调到了**", _c4 == [["/x/a.pdf"]], str(_c4))

    _t5, _s5, _ = _mk(["/x/a.pdf"], on_drop=lambda ps: False)
    check("回调显式 `False` -> 拒", _t5.performDragOperation_(_s5) is False)

    _t6 = panel.make_drop_target(lambda pb: True, None)      # 回调**缺失**
    check("⚠️ 回调**缺失** -> 拒（与『返回 None』必须分开）",
          _t6.performDragOperation_(_Sender(_PB(["/x/a.pdf"]))) is False)

    # ⚠️ 假 pb 的 `types()` 是硬要求 —— 少了它上面**每一条都会抛异常**。
    #    （先崩在 `draggingEntered` 里，异常从 `_call` 的 try 外面逃出去。）
    # ⚠️ 原来这里有一条 `check("假 pasteboard 满足 types() …",
    #    isinstance(_PB([]).types(), list))` —— **恒真**（2026-09-28 审查指出）：
    #    `_PB` 是本文件自己的假对象，断它自己的属性永远不会红；真缺了 `types()`
    #    是**抛 AttributeError**（上面那几条会当场崩），也不是这条抓得到的。
    #    → 删掉假断言，只留这条**说明**：`panel.dragging_entered` 里那句
    #    `types=` 是**提前求值**的、且在 `_call` 的 try 外面，所以假 pb 必须有它。

    # ⚠️⚠️ 上面那些用的是**本地的判据替身**（`_e` 里内联了 `is_supported`）——
    #    那钉的是 `make_drop_target` 的契约，**不是 `entry_panel._enter`**。
    #    变异验证当场抓到：把 `_enter` 改回"只看非空文件列表"，上面**一条都不红**。
    #    → 必须**走真正的 `_enter`**。`_make_card` 返回的就是那个 DropTarget，
    #      所以它的 `_on_enter` 就是真回调（这正是 ⑦ 组那条教训：
    #      **要测"接线"，不是测"被调用的那个函数"**）。
    _card = EP._make_card(_r(), on_start=None,
                          on_drop_files=lambda c, p: True, width=640.0)
    check("⭐⭐ 真 `entry_panel._enter`：`.txt` **拒**"
          "（原来只看『非空文件列表』→ 高亮说能收、跑完说 unsupported）",
          bool(_card._on_enter(_PB(["/x/notes.txt"]))) is False)
    # ⚠️ 2026-09-28 起 `.docx` 进支持集 —— 这条钉的是**新行为**，
    #    与上面那条 `.txt` 是一对（改 `_SUPPORTED` 就会有一条红）。
    check("⭐ 真 `entry_panel._enter`：`.docx` 现在**收**（2026-09-28 进支持集）",
          bool(_card._on_enter(_PB(["/x/讲义.docx"]))) is True)
    # ⭐ 文件夹：走 `extract.expand()`，**一层**里找得到能抽的就收。
    #    ⚠️ 这两条需要真目录（`expand` 会 `iterdir`）—— 用 tempfile，不碰仓库任何东西。
    import shutil as _sh
    import tempfile as _tf
    _tmpd = _tf.mkdtemp(prefix="cl_enter_")
    try:
        _with = pathlib.Path(_tmpd) / "有"
        _with.mkdir()
        (_with / "a.pdf").write_bytes(b"")
        _without = pathlib.Path(_tmpd) / "无"
        _without.mkdir()
        (_without / "a.txt").write_bytes(b"")
        check("⭐ 真 `_enter`：文件夹里有能抽的 -> **收**",
              bool(_card._on_enter(_PB([str(_with)]))) is True)
        check("⭐ 真 `_enter`：文件夹里一个能抽的都没有 -> **拒**（别高亮说能收）",
              bool(_card._on_enter(_PB([str(_without)]))) is False)
    finally:
        _sh.rmtree(_tmpd, ignore_errors=True)
    check("⭐ 真 `entry_panel._enter`：目录也拒（它同样不在支持集里）",
          bool(_card._on_enter(_PB(["/x/Downloads"]))) is False)
    check("⭐ 真 `entry_panel._enter`：`.pdf` / `.pptx` 收",
          bool(_card._on_enter(_PB(["/x/讲义.pdf"]))) is True
          and bool(_card._on_enter(_PB(["/x/讲义.pptx"]))) is True)
    check("⭐ 真 `entry_panel._enter`：混合 -> 收（HIG 子集语义）",
          bool(_card._on_enter(_PB(["/x/a.pdf", "/x/Downloads"]))) is True)

    # ⭐⭐ `.ics`：**悬停必须说"收"** —— 2026-09-29 实测的缺口。
    #    松手那条是真的会导入（`run_prep:2848` → `run_import`），但悬停判据当时
    #    只问 `extract.expand()`（= 能不能被**抽取**），而 `.ics` 不在支持集里
    #    → 屏上「不高亮（说收不了）」，一松手**东西真进去了**。
    #    ⚠️ 这是 `REVIEW §9.2 #5` 那条的**镜像**：那次是"高亮着却收不了"，
    #       这次是"不高亮却收下了"。两者同源 —— 悬停判的和松手做的是两件事。
    #    ⚠️ 判据钉的是**真 `_enter`**（不是替身）：替身只证明 `make_drop_target`
    #       的契约，而这条缺陷住在本文件的 `_enter` 里。
    check("⭐⭐ 真 `_enter`：`.ics` **收**（松手那条真会导入，悬停不许说收不了）",
          bool(_card._on_enter(_PB(["/x/timetable.ics"]))) is True)
    check("⭐ 真 `_enter`：`.ical` 也收（`timetable_files` 认两个后缀）",
          bool(_card._on_enter(_PB(["/x/timetable.ical"]))) is True)
    check("⭐ 真 `_enter`：`.ics` + 课件混拖 -> 收",
          bool(_card._on_enter(_PB(["/x/a.pdf", "/x/t.ics"]))) is True)
    check("⚠️ 真 `_enter`：`.ics` 不是万能通行证 —— `.txt` 仍然拒",
          bool(_card._on_enter(_PB(["/x/notes.txt"]))) is False)

    # ⭐⭐ **跨层端到端**：`_drop` 的返回值必须**跟 `on_drop_files` 走**。
    #    这一环是 `performDragOperation:` 认不认这次落地的**唯一**依据：
    #    `panel.perform_drag` 拿到的就是它的返回值（`None` 现在算收，但**真值必须传得上来**）。
    #    真调用方是 `run_prep`（它 `return True/False`）—— 这里用桩把那条契约钉死。
    _cd1 = EP._make_card(_r(), on_start=None,
                         on_drop_files=lambda c, p: True, width=640.0)
    _cd0 = EP._make_card(_r(), on_start=None,
                         on_drop_files=lambda c, p: False, width=640.0)
    check("⭐⭐ 真 `_drop` 的返回值**跟 `on_drop_files` 走**（真值传得上来）",
          _cd1._on_drop(["/x/a.pdf"]) is True and _cd0._on_drop(["/x/a.pdf"]) is False,
          f"{_cd1._on_drop(['/x/a.pdf'])!r} / {_cd0._on_drop(['/x/a.pdf'])!r}")

    print("\n--- ⑩ 「撤销」行必须**滚进视野**（§8.1 #7 / HIG › Undo and redo）---")
    # ⚠️⚠️ **这条判据必须自带区分能力，而且要带零假设对照。**
    #     我第一版把它塞进 ⑦ 组的**真面板**里 —— 而那里**可视区高 482、卡只有 158**
    #     → 整张卡都看得见，**滚不滚都绿**（变异没变红才发现）。这是"假信心判据"。
    #     这里另起一个小视口（88pt）+ 一张高卡（576pt）：**不滚就一定看不见**，
    #     并且**先断言"不滚时确实看不见"**（零假设对照）—— 否则下面那条可能是空的。
    from AppKit import NSScrollView as _SV, NSView as _NSV
    _FlipDoc = objc_own.own("ProbeFlipDoc", _NSV, {"isFlipped": lambda self: True})
    _e10 = {"added": [f"t{i:02d}" for i in range(20)], "not_added": [], "failed": [],
            "removed": set(), "undo": {"text": "t00", "entries": [(1, "t00\n")]}}
    _sc10 = _SV.alloc().initWithFrame_(NSMakeRect(0, 0, 300, 88))
    _sc10.setHasVerticalScroller_(True)
    _doc10 = _FlipDoc.alloc().initWithFrame_(NSMakeRect(0, 0, 300, 600))
    _sc10.setDocumentView_(_doc10)
    _card10 = EP._make_card(_r(), on_start=None,
                            on_drop_files=lambda c, p: False, width=300.0, entry=_e10)
    _card10._course = "ZZ"
    _doc10.addSubview_(_card10)
    _doc10.setFrameSize_((300.0, EP.card_height(_e10)))

    def _undo_seen():
        _v = _sc10.documentVisibleRect()
        _q = _card10._undo_lbl.convertRect_toView_(_card10._undo_lbl.bounds(), _doc10)
        return (_q.origin.y < _v.origin.y + _v.size.height
                and _q.origin.y + _q.size.height > _v.origin.y)

    check("⚠️ 零假设对照：**不滚时「撤销」行确实在视野外**"
          "（否则下面那条是空的 —— 我第一版就栽在这）", _undo_seen() is False)
    check("⭐⭐ 滚一下 -> 「撤销」行进视野（§8.1 #7 / HIG）",
          EP._scroll_undo_into_view(_doc10, "ZZ") is True and _undo_seen() is True)
    check("⚠️ 课号对不上 -> 返回 False，不抛",
          EP._scroll_undo_into_view(_doc10, "NOT_THERE") is False)

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
