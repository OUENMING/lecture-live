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
    # ⚠️ **NSVisualEffectView 自己的三个属性也要比** —— 它们决定「材质到底渲不渲染」，
    #    而且**掉一个不会有任何症状**：离屏实测（2026-09-26），把 `state` 从
    #    `Active` 换成 AppKit 的默认 `FollowsWindowActiveState`，
    #    **在 app 不激活时面板暗 2.3 倍**（均亮 57.4 → 24.7）——
    #    而上课时 app 就是**一直不激活**。
    #
    #    ⚠️ **这三项加进来不是为了「抓得到」**（实测：删掉 `panel.py` 的 `setState_`，
    #    「离屏渲染与老配方一致」那条腿**本来就会红**，2 条断言、12/14）。
    #    加它是因为**原来报的信息没用** —— 只告诉你「两个哈希不同」，
    #    你得自己回去二分是哪一项。现在报的是 `effect.state: 0 vs 1`，直接点到项。
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
    check("单进程 import overlay + whatsnew 不炸", True)
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
        ov.close()
    os.environ["CLASSLIVE_DEBUG"] = "1"
    card = whatsnew.build("0.0.0", "测试摘要", date="2026-09-26")
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
