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

        # ⭐ 菜单栏那一项必须钉住。`_install_status_item` 整段在 try/except fail-soft 里
        #    （那是**对的** —— 图标不能因为建菜单项失败就整个消失），但后果是
        #    **写坏了完全静默**：图标还在，菜单少一项，而那一项恰好是
        #    「上课中加课件的唯一入口」。
        #    ⚠️ 拿不到 status item / 菜单时**报红**，不跳过 ——
        #       「验不了」和「验过了」必须分开（同 `readiness` 那条 None vs 0）。
        _menu = ov._status.menu() if ov._status is not None else None
        _titles = [mi.title() for mi in _menu.itemArray()] if _menu is not None else None
        check("菜单栏有「开课前的准备…」（不是静默少一项）",
              _titles == ["开启鼠标穿透", "开课前的准备…", "退出"], str(_titles))
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

    print("\n--- ⑥ entry_panel 的卡片：内容不许溢出卡片 ---")
    # ⭐ 这是 `card_height()` 那条推导**真正的锁**。
    #    `card_height(None) == CARD_H` 不是锁 —— 实测把整个算式换成写死的 `h = 88.0`
    #    照样全绿（硬编码的 88 恰好等于正确值）。而「建出卡片、量有没有子视图超出」
    #    不管你把常数改成什么、也不管高度是不是算出来的，都成立。
    import entry_panel as EP
    import courses as _c

    def _r(code="ECON10740", title="Exploring Economics"):
        return _c.Readiness(code, title, 36, 0, 0, "2026-09-22")

    for tag, ent in (("无结果", None),
                     ("1 条结果", {"added": ["elasticity"], "removed": set()}),
                     ("5 条 + 已删 1 + 撤销",
                      {"added": list("abcde"), "removed": {"b"},
                       "undo": {"text": "b"}}),
                     ("全删光（0 条但仍要显示头部）",
                      {"added": ["a"], "removed": {"a"}, "undo": {"text": "a"}}),
                     # ⭐ **这一条才是真正的探测器。** 行内容与卡片高度之间有一圈
                     #    **恒定的 ~54pt 余量**（`CARD_GAP_V` + `RESULT_TAIL` 那块），
                     #    所以 5 行的漂移吃不满它 —— 实测：把行距改成 `RESULT_ROW + 12`
                     #    或把高度算少 20，前四个用例**照样全绿**。
                     #    20 行（一门课加 40 个词是常态，所以这是真实数量级）
                     #    才让漂移累积到超过那圈余量。
                     ("20 条（漂移探测器）",
                      {"added": [f"term{i:02d}" for i in range(20)],
                       "removed": set()})):
        card = EP._make_card(_r(), on_start=None, on_prep=lambda c: None,
                             on_drop_files=lambda c, p: False, width=640.0,
                             entry=ent, on_delete=lambda t: None, on_undo=lambda: None)
        ch = float(card.frame().size.height)
        over = [(type(v).__name__, round(float(v.frame().origin.y
                                             + v.frame().size.height), 1))
                for v in card.subviews()
                if float(v.frame().origin.y + v.frame().size.height) > ch + 0.01]
        check(f"{tag}：没有子视图超出卡片高度（{ch:.0f}）", not over, str(over[:4]))
        check(f"{tag}：高度与 card_height() 一致",
              abs(ch - EP.card_height(ent)) < 0.01, f"{ch} vs {EP.card_height(ent)}")

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
    c1 = EP._make_card(_r(), on_start=None, on_prep=lambda c: None,
                       on_drop_files=lambda c, p: False, width=640.0, entry=e1,
                       on_delete=lambda t: None, on_undo=lambda: None)
    check("⭐⭐ 从上到下：课名 → 准备度 → 结果头 → 逐条（**正序**）",
          _reading_order(c1) == [EP.card_title(_r()), EP.readiness_line(_r()),
                                 EP.result_header(e1), "第一", "第二", "第三"],
          str(_reading_order(c1)))

    e2 = {"added": ["甲", "乙"], "removed": {"乙"}, "undo": {"text": "乙"}}
    seq2 = _reading_order(EP._make_card(
        _r(), on_start=None, on_prep=lambda c: None,
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
        term = [x.strip() for x in before.splitlines()
                if x.strip() and not x.startswith("#")][0]

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
            undos = _card_btns(h2.window.contentView(), "ECON10740", "撤销")
            check("⭐ 出现「撤销」按钮（HIG：删东西用撤销，不用确认框）",
                  len(undos) == 1, f"找到 {len(undos)} 个")
            if undos:
                _click(undos[0])
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

        closes = [b for b in _all_btns(h2.window.contentView())
                  if b.title() == "关闭"]
        check("⭐「关闭」的 target 还在（没被 GC）—— 弱引用的经典坑",
              len(closes) == 1 and closes[0].target() is not None,
              f"target={[str(b.target()) for b in closes]}")
        if closes and closes[0].target() is not None:
            _click(closes[0])
            check("⭐ 点「关闭」-> 窗口真的关掉了", not bool(h2.window.isVisible()))
    finally:
        EP2.close_panel()
        _sh.rmtree(iso, ignore_errors=True)

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
