"""磨砂玻璃面板 —— **配方唯一定义点**。

`overlay.py`（字幕面板）和 `whatsnew.py`（更新卡片）要的是同一套窗口：
无边框 / 非激活 / 常浮于最上 / 深色磨砂材质 / 压一层半透明黑 scrim / 空白处可拖。
这套东西以前**两份拷贝**，连 `SCRIM_ALPHA = 0.38` 都各写一遍
（whatsnew 的注释原文：「与 overlay.SCRIM_ALPHA 同值」）。

## ⚠️ 为什么必须抽出来：一次真发生过的事故

两边都定义了同名的 ObjC 类 `_Panel` / `_DragLayer`。**Objective-C 的类是按名字
全局注册的** —— 第二个定义会抛 `_Panel is overriding existing Objective-C class`。
那次异常被一个 fail-soft 的 `except` 吞掉了，后果是：

    update 卡片静默返回 None（卡片根本不显示），
    而**独立测试全绿** —— 因为那些测试里 overlay 没被 import，只有同进程才复现。

当时的修法是**改名字**（`_CLWhatPanel`、`_T`）—— 治标：每个新消费者仍要记得
挑一个全局唯一的名字，坑还在原地。

这里的做法是**让调用方拿不到命名权**：类由本模块拥有、名字由本模块生成、
调用方只拿实例。撞名在结构上不可能发生，而且真撞上时**立刻抛，绝不 fail-soft**。

## 规矩

- **一个数字只有一个定义点**。`SCRIM_ALPHA` / `CORNER_RADIUS` 是**常量不是参数** ——
  实测两处本来就同值，也没有调用方需要不同值。参数化没有调用方的差异 = 白加的接口。
- **纯搬家**：调用方的像素与行为一行都不许变（`tests/test_panel.py` 钉住）。
- AppKit 一律**函数内 import**，这样没有窗口服务器时本模块仍可 import。
"""
from __future__ import annotations

import os
import typing

import motion
import objc_own

# ⚠️ 只为让 PyObjC 认得 `CGColorRef`：不导入 Quartz 时，每次 `NSColor….CGColor()`
#    都打一条 `ObjCPointerWarning`（地址每次不同 → 警告去重拦不住，终端刷屏）。
#    颜色照样设得上，纯噪音。overlay / entry_panel 都 import 本模块，所以放这里一处。
#    失败就算了（没有 Quartz 的环境本来也起不了窗口）。
try:
    import Quartz  # noqa: F401
except ImportError:
    pass

# 白底可读性：材质之上压的半透明黑。可读性其实由文字的紧凑描边承担
# （见 overlay.py 顶部那段），这一层只负责把白底压到让描边有依托。
SCRIM_ALPHA = 0.38
CORNER_RADIUS = 16.0


def _panel_class() -> type:
    """非激活但可成 key 的浮动面板。

    ⚠️ **一个类服务两个调用方，这是量出来的、不是猜的**（2026-09-26）：
       NSPanel 基类在两种 style mask 下的返回值实测为

           style          canBecomeMainWindow   canBecomeKeyWindow
           overlay(titled)      False                 True
           whatsnew(borderless) False                 False

       → whatsnew 原来那句 `canBecomeMainWindow -> False` 是**空操作**（基类本来就
         False），已删；而 `canBecomeKeyWindow -> True` 对 borderless **是必需的**
         （默认 False → 面板成不了 key → 上面的按钮点不了）。
    """
    from AppKit import NSPanel
    import objc

    def send_event(self, event):
        """窗口收到的**每一个**事件都经过这里 —— 唯一绕不开的位置。

        ⚠️ 为什么不 override 某个视图的 `mouseDown_`：实测（2026-09-24）即使
           `contentView.hitTest_()` 明确返回了我们的拖拽层，它的 `mouseDown_`
           **一次都没被调用**（没有报错，静默）。窗口级的 sendEvent_ 没这个问题。
        ⚠️ 用 `objc_own.cls_of("Panel")` 取回本类，**不要写 `type(self)`** ——
           类被继承时 `type(self)` 是子类，`objc.super` 的行为会变。见 objc_own 的说明。
        """
        if event.type() == 1:               # NSEventTypeLeftMouseDown
            try:
                from AppKit import NSApplication
                NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
            except Exception:               # noqa: BLE001
                pass
        objc.super(objc_own.cls_of("Panel"), self).sendEvent_(event)

    return objc_own.own("Panel", NSPanel, {
        "canBecomeKeyWindow": lambda self: True,
        "sendEvent_": send_event,
    })


def make_drag_layer(on_mousedown=None):
    """整面板的背景拖拽层 —— 让「空白处任意位置都能拖窗口」。

    为什么必须显式加这一层（2026-09-24 实测的拖动意图地图）：

        转录区   -> 移动 ✅（那里自己调了 performWindowDragWithEvent_）
        顶栏空白 -> **无反应** ❌
        输入行   -> **无反应** ❌

    于是用户按习惯去抓顶栏想移动窗口时什么都没发生，再往外一点就落进 5px 缩放带
    —— 体验成了「想拖动却变成缩放」。

    放在 z 序**最底**（紧跟 scrim 之后 join），所以控件、转录区、缩放抓取带都在它
    上面、各自照常收事件；只有真正的空白处才落到这一层。
    """
    from AppKit import NSView

    def can_move_window(self):
        # ⚠️ 必须 **True**（2026-09-24 实测定位）：这个返回值是 AppKit「按下背景即
        #    拖动窗口」的开关。设成 False 会让**背景完全拖不动**（而且 mouseDown_
        #    也收不到 —— 两边都落空）。症状就是「只有按住转录区才拖得动」。
        return True

    def mouse_down(self, event):
        win = self.window()
        if win is None:
            return
        # 点空白处 -> 手动激活 app。macOS 只让**活跃 app** 改光标，而面板带
        # NonactivatingPanel（那是「非激活时仍被合成」的前提）不会自激活。
        try:
            from AppKit import NSApplication
            NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        except Exception:                   # noqa: BLE001
            pass
        cb = getattr(self, "_cb", None)
        if cb is not None:
            cb(event)                       # 最外一圈 -> 调用方自己的嵌套循环缩放
        # 其余情况 AppKit 会凭 mouseDownCanMoveWindow=True 自己拖动窗口

    cls = objc_own.own("DragLayer", NSView, {
        "mouseDownCanMoveWindow": can_move_window,
        "mouseDown_": mouse_down,
    })
    v = cls.alloc().initWithFrame_(((0.0, 0.0), (100.0, 100.0)))
    v._cb = on_mousedown
    return v


def make_resize_delegate(on_resize):
    """窗口委托 —— 只为一件事：**live resize 期间也要重排内容**。

    ⚠️ 为什么必须用委托，不能继续在 `pump()` 里轮询（2026-09-24 实测）：
       原生拖边缘缩放时 AppKit 会进入它自己的事件跟踪循环，**我们的整个主循环被
       卡住 1239.8ms**（实测：空闲期 pump 最大间隔 9.6ms，拖拽期 1239.8ms）。
       那 1.2 秒里重排一次都跑不到 -> 窗口框在动、内容冻着，松手才跳一下。
       `windowDidResize:` 是在那个跟踪循环**内部**回调的 —— 这才是 AppKit 给
       live resize 的正规钩子。

    这不违反本仓库「轮询而非观察者」的既定做法：那条针对的是**滚动视图的 bounds
    通知**（弱引用 + 自我 setFrame 期间重入）；窗口尺寸变化没有那个重入面，而且
    轮询在拖拽期间**根本跑不到**。

    ⚠️ **`setDelegate_` 是弱引用** —— 调用方必须自己留住返回的对象（见
       `FrostedPanel` 的说明），否则它被 GC 掉、回调静默失效。
    """
    from AppKit import NSObject

    def did_resize(self, note):
        cb = getattr(self, "_cb", None)
        if cb:
            cb()

    cls = objc_own.own("ResizeDelegate", NSObject, {
        "windowDidResize_": did_resize,
        "windowDidEndLiveResize_": did_resize,
    })
    d = cls.alloc().init()
    d._cb = on_resize
    return d


class FrostedPanel(typing.NamedTuple):
    """`build()` 的把手。

    ⚠️ **调用方必须留住这个对象。** 里面的 `resize_delegate` 是被
       `setDelegate_` **弱引用**的 —— 解包后只留 `window` 而把本对象丢掉，
       delegate 会被 GC，缩放回调静默失效（那种「不报错的坏掉」最难查）。
       overlay 的做法是显式存进 `self._win_delegate` 和 `_targets`。
    """
    window: typing.Any            # NSPanel，chrome 已配好
    glass: typing.Any             # NSVisualEffectView —— 往这里 addSubview_
    scrim: typing.Any             # 半透明黑，z 序在 glass 之子最底
    drag: typing.Any              # 背景拖拽层，z 序最低
    resize_delegate: typing.Any   # 没传 on_resize 时为 None


def install_edit_menu() -> bool:
    """给这个进程装一个**最小 Edit 菜单** —— 没有它，⌘C/⌘V/⌘A 在**所有**输入框里都是死的。

    ⚠️⚠️ 事实（Apple 一手 + 社区一致口径）：文本框的 ⌘C/⌘V/⌘X/⌘A 是**经菜单栏的
       键等价（key equivalent）分发**的，不是文本框自己处理的 ——
       「copy & paste works on a NSTextField **as long as you do not delete the
       Edit menu** from the standard Main menu」（kulman.sk, 2019；SO 上同题
       9k 浏览，答案同一条：把 Paste 接到 First Responder 的 `paste:`）。
       而本 app 是 `.accessory`、**从不建 mainMenu** → 实测（作者 2026-10-01 本机，
       填 key 框 + 面板搜索框）：**右键菜单的 Paste 能粘，⌘V 完全没反应**。
    ⚠️ 菜单**不用显示**也生效（accessory app 平时不显示菜单栏）。
       target 留 nil → 走响应链 → 谁在第一响应者谁处理。
    ⚠️ 幂等：装过就直接 True。**失败不许抛**（编辑快捷方式不该拦住面板）。
    """
    try:
        from AppKit import NSApplication, NSMenu, NSMenuItem
        app = NSApplication.sharedApplication()
        if app.mainMenu() is not None:
            return True
        main = NSMenu.alloc().init()
        edit = NSMenu.alloc().initWithTitle_("Edit")
        holder = NSMenuItem.alloc().init()
        holder.setSubmenu_(edit)
        main.addItem_(holder)
        for title, sel, key in (
                ("撤销", "undo:", "z"),
                ("重做", "redo:", "Z"),
                ("剪切", "cut:", "x"),
                ("拷贝", "copy:", "c"),
                ("粘贴", "paste:", "v"),
                ("全选", "selectAll:", "a")):
            edit.addItem_(NSMenuItem.alloc(
            ).initWithTitle_action_keyEquivalent_(title, sel, key))
        app.setMainMenu_(main)
        return True
    except Exception:                                     # noqa: BLE001
        return False


def build(rect, style, *, on_background_click=None, on_resize=None) -> FrostedPanel:
    """建一个配好 chrome 的磨砂面板。

    顺序是**载荷性的**，别调：appearance -> 窗口属性 -> glass -> scrim -> drag。
    后面每个调用方往 `glass` 里加的视图都会落在 drag 之上，各自照常收事件。

    `on_background_click` 只在点到**空白处**时触发（最外一圈，给嵌套循环缩放用）。
    `on_resize` 给了才装窗口委托 —— 不给就不装（不缩放的面板不需要它）。
    """
    # ⚠️ 面板 = 这个进程里有输入框了 → 先保证 ⌘C/⌘V/⌘A 能用（幂等）。
    #    放在这里 = **唯一岔口**，不用在 entry_launch / main / whatsnew 各调一遍。
    install_edit_menu()
    from AppKit import (NSAppearance, NSAppearanceNameDarkAqua, NSBackingStoreBuffered,
                        NSColor, NSFloatingWindowLevel, NSView,
                        NSVisualEffectMaterialHUDWindow, NSVisualEffectStateActive,
                        NSVisualEffectView, NSWindowCollectionBehaviorCanJoinAllSpaces)

    # ⚠️ 用**具名常量**不要写字面量 2 —— 这里是全仓唯一一份配方了，
    #    写死数字等于把这个模块降级成一份「看不懂的拷贝」。
    window = _panel_class().alloc(
    ).initWithContentRect_styleMask_backing_defer_(rect, style,
                                                   NSBackingStoreBuffered, False)

    # ⚠️ 必须**显式指定深色外观**。像素级实测（2026-09-24）：系统处于浅色模式时，
    #    Titled 窗口的 NSVisualEffectView 会跟随窗口 appearance，`.hudWindow` 被渲染
    #    成灰色 —— 面板中心平均亮度 **0.314**；强制 DarkAqua 后降到 **0.113**
    #    （暗 2.8 倍），通透感恢复。
    #    这就是「换成 Titled 之后变灰」的**真因**：与 material / blendingMode / opaque
    #    都无关（那三项实测本来就是对的：HUDWindow=13, BehindWindow=0, state=Active）。
    window.setAppearance_(NSAppearance.appearanceNamed_(NSAppearanceNameDarkAqua))
    window.setLevel_(NSFloatingWindowLevel)
    window.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces)
    window.setOpaque_(False)
    window.setBackgroundColor_(NSColor.clearColor())
    # ⚠️ 必须 **True**（2026-09-24 实测）：它是「按下背景即拖动窗口」的总开关，
    #    配合拖拽层的 `mouseDownCanMoveWindow -> True` 才生效。
    #    试过设 False 想自己接管拖拽，结果是**背景完全拖不动**。
    window.setMovableByWindowBackground_(True)

    content = window.contentView()
    glass = NSVisualEffectView.alloc().initWithFrame_(content.bounds())
    glass.setMaterial_(NSVisualEffectMaterialHUDWindow)
    glass.setState_(NSVisualEffectStateActive)
    glass.setWantsLayer_(True)
    glass.layer().setCornerRadius_(CORNER_RADIUS)
    glass.layer().setMasksToBounds_(True)   # scrim 是矩形，靠这里裁成圆角
    content.addSubview_(glass)

    # 白底可读性：材质之上、文字之下压一层半透明黑。必须是 glass 的子视图、
    # 且在下面所有内容之前加入 —— 材质画在 drawRect，设不了背景色。
    scrim = NSView.alloc().initWithFrame_(glass.bounds())
    scrim.setWantsLayer_(True)
    scrim.layer().setBackgroundColor_(
        NSColor.blackColor().colorWithAlphaComponent_(SCRIM_ALPHA).CGColor())
    glass.addSubview_(scrim)

    drag = make_drag_layer(on_background_click)
    drag.setFrame_(glass.bounds())
    glass.addSubview_(drag)

    delegate = None
    if on_resize is not None:
        delegate = make_resize_delegate(on_resize)
        window.setDelegate_(delegate)

    return FrostedPanel(window, glass, scrim, drag, delegate)


def make_scroll_view(rect, *, has_vertical=True):
    """在面板里建一个**观感正确**的 NSScrollView（文档视图由调用方自己塞）。

    ⚠️ **只抽这几行，不是抽整份实现。** 2026-09-26 逐行比对过现存两个滚动区
    （`whatsnew._add_log_view` 与 `transcript_view`）：真正重合的**只有这几行**，
    其余都是各自场景的行为（一个是 NSTextView 的文本管道，一个是「所有行等高 →
    O(1) 除法」的槽位池）。把它们一起抽过来是白带的复杂度。

    这几行**有原因，所以值得有唯一定义点**：
      · `drawsBackground_(False)` —— 面板是 vibrancy，HIG 明说别在控件下垫不透明底
      · `borderType_(0)` —— 不要 bezel
      · ⭐ **竖直滚动条 = 我们自己画的那条细的**（见「细滚动条」那一段）——
        系统那条在 macOS 26 上画出来**固定 7pt 且宽度不可配**，所以这里是
        「关掉系统的 + 装自己的」（`install_thin_scroller`）
      · `setScrollerStyle_(Overlay)` 仍要设：样式一变（用户改「显示滚动条」/
        插鼠标）系统会把**滚动视图**整个换档，`thin` 那边监听通知重设它
        （报告 §3.2：AppKit 会自己改回来）

    ⚠️ **不含 `setVerticalScrollElasticity_`** —— `transcript_view` 那项是**动态**的
    （构造设 0，之后按状态改 Automatic），不是静态约定，硬抄会把一个行为塞进配方。
    """
    from AppKit import NSScrollView, NSScrollerStyleOverlay

    sc = NSScrollView.alloc().initWithFrame_(rect)
    sc.setDrawsBackground_(False)
    sc.setBorderType_(0)                                  # NSNoBorder
    sc.setAutohidesScrollers_(True)
    sc.setScrollerStyle_(NSScrollerStyleOverlay)
    sc.setHasVerticalScroller_(False)                     # ⚠️ 系统那条不要（见文件头）
    sc.setHorizontalScrollElasticity_(0)                  # 0 = None
    if has_vertical:
        install_thin_scroller(sc)
    return sc


# ══════════════════════════════════════════════════════════════════════════════
# 细滚动条（自绘 overlay 滑块）—— 作者 2026-10-18 拍板
# ══════════════════════════════════════════════════════════════════════════════
# 为什么自绘、而不是「把系统滚动条调细」：
# ⚠️⚠️ 系统那条在 macOS 26 上**画出来固定 7pt、宽度不可配**。`NSScroller` 现在
#    **没有 cell**（`cell() is None`），滑块由图层的**私有子层**画 —— 把
#    `NSScroller` 子类（含 `isCompatibleWithOverlayScrollers=True` + `drawKnob`）
#    装上去，`drawKnob` **一次都不会被调用**（连手动 `display()` 都不调，2026-10-18
#    本机实测）。社区那套老配方（SO 51061738）针对的是 cell/legacy 那一代。
#    完整调研与读数见 `scratchpad/recap/scroller-research.md`（§2.2 / §5.2）。
# → 所以：**关掉系统滚动条**（滚轮不受影响），自己画一条 overlay 滑块 + 自己处理
#    悬停 / 拖动 / 翻页。比系统更细的那批 app 走的也是这条路（同报告 §4）。
#: 滑块宽度（pt）：静止 / 悬停（拖动同悬停）。
THIN_W = 3.0
THIN_W_HOVER = 6.0
#: 「增大对比度」开着时各加粗一档（跟随系统那条无障碍设置）。
THIN_W_CONTRAST = 5.0
THIN_W_CONTRAST_HOVER = 8.0
#: 白色 α：静止 / 悬停。
THIN_ALPHA = 0.28
THIN_ALPHA_HOVER = 0.52
#: 「增大对比度」时还要**更实**（只加粗不加深 = 只做了半件事）。
THIN_ALPHA_CONTRAST = 0.45
THIN_ALPHA_CONTRAST_HOVER = 0.72
#: 滑块离滚动视图右缘的内缩（pt）—— 系统那条实测内缩 ≈2.5pt，别贴边。
THIN_INSET = 2.0
#: **命中带**宽度（pt）：比滑块宽得多，3pt 的滑块也点得中（报告 §4 Height 那条）。
THIN_STRIP = 16.0
#: 滑块最短（pt）：内容比视口长很多时别画成一根发丝。
THIN_MIN_H = 24.0
#: 不滚、不悬停多久淡出（s）。
THIN_IDLE_S = 1.2


def thin_alpha(*, hovered: bool, contrast: bool = False) -> float:
    if contrast:
        return THIN_ALPHA_CONTRAST_HOVER if hovered else THIN_ALPHA_CONTRAST
    return THIN_ALPHA_HOVER if hovered else THIN_ALPHA


def thin_width(*, hovered: bool, contrast: bool = False) -> float:
    """滑块宽度：悬停更粗；「增大对比度」再各加一档。"""
    if contrast:
        return THIN_W_CONTRAST_HOVER if hovered else THIN_W_CONTRAST
    return THIN_W_HOVER if hovered else THIN_W


def thin_thumb_h(*, doc_h: float, clip_h: float, strip_h: float,
                 min_h: float = THIN_MIN_H) -> float:
    """滑块高度 = 视口/文档 × 带高，但不短于 `min_h`、不长于带本身。"""
    doc_h = max(1.0, float(doc_h))
    clip_h = max(1.0, float(clip_h))
    return max(min_h, min(float(strip_h), strip_h * clip_h / doc_h))


def thin_thumb_y(*, frac: float, strip_h: float, thumb_h: float) -> float:
    """滑块**底边** y（AppKit 口径：0 = 命中带底）。

    `frac` = 从**文档开头**算的滚动比例（0 = 开头 / 1 = 末尾）。
    ⚠️ 口径刻意跟翻转无关：`frac` 由 `documentVisibleRect().origin.y` 归一化而来，
       翻转文档与不翻转文档都是「0 = 开头」，这里只负责把它摆到带子的**顶**。
    """
    span = max(0.0, float(strip_h) - float(thumb_h))
    f = min(1.0, max(0.0, float(frac)))
    return span * (1.0 - f)


def thin_frac_for_y(*, knob_bottom: float, strip_h: float, thumb_h: float) -> float:
    """拖动的反函数：滑块底边 y → 滚动比例（`thin_thumb_y` 的逆）。"""
    span = max(1e-6, float(strip_h) - float(thumb_h))
    return min(1.0, max(0.0, 1.0 - float(knob_bottom) / span))


def thin_thumb_rect(*, doc_h: float, clip_h: float, origin_y: float, doc_top: float,
                    strip_w: float, strip_h: float, width: float,
                    min_h: float = THIN_MIN_H):
    """滑块在命中带里的 `(x, y, w, h)`；**内容不比视口长 → `None`**（没得滚就不画）。

    `origin_y` = `documentVisibleRect().origin.y`，`doc_top` = 可滚到头的那个值
        （= `doc_h - clip_h`，**从开始算的**最大量）。
    """
    doc_h = max(1.0, float(doc_h))
    clip_h = max(1.0, float(clip_h))
    if doc_h <= clip_h + 0.5:                             # 没得滚
        return None
    thumb_h = thin_thumb_h(doc_h=doc_h, clip_h=clip_h, strip_h=strip_h, min_h=min_h)
    span_doc = max(1e-6, doc_h - clip_h)
    frac = min(1.0, max(0.0, float(origin_y) / span_doc))
    y = thin_thumb_y(frac=frac, strip_h=strip_h, thumb_h=thumb_h)
    x = max(0.0, float(strip_w) - float(width) - THIN_INSET)
    return (x, y, float(width), thumb_h)


class _Thin:
    """细滚动条的**纯状态机**（不碰 AppKit）：`show / hover / drag` 进来的效果只改这几个数。

    与视图分开是刻意的：判据（几何 + 可见性）能直接量这个对象，
    不必去猜 AppKit 什么时候重画。
    """

    def __init__(self, *, doc_h: float = 0.0, clip_h: float = 1.0, origin_y: float = 0.0,
                 strip_w: float = THIN_STRIP, strip_h: float = 1.0):
        self.doc_h = float(doc_h)
        self.clip_h = float(clip_h)
        self.origin_y = float(origin_y)
        self.strip_w = float(strip_w)
        self.strip_h = float(strip_h)
        self.hovered = False
        self.dragging = False
        self.contrast = False
        self.shown = False                                # 最近有过滚动/悬停/拖动

    def scrollable(self) -> bool:
        return self.doc_h > self.clip_h + 0.5

    def width(self) -> float:
        return thin_width(hovered=self.hovered or self.dragging, contrast=self.contrast)

    def alpha(self) -> float:
        if not self.scrollable():
            return 0.0
        if not (self.shown or self.hovered or self.dragging):
            return 0.0
        return thin_alpha(hovered=self.hovered or self.dragging, contrast=self.contrast)

    def rect(self):
        return thin_thumb_rect(
            doc_h=self.doc_h, clip_h=self.clip_h, origin_y=self.origin_y,
            doc_top=max(0.0, self.doc_h - self.clip_h), strip_w=self.strip_w,
            strip_h=self.strip_h, width=self.width())


def _thin_hooks() -> dict:
    """`_ThinView` 的方法表（全在模块级函数里，ObjC 类只能持函数，不能持闭包外的状态）。"""

    def update_tracking_areas(self):
        from AppKit import (NSTrackingArea, NSTrackingMouseEnteredAndExited,
                            NSTrackingActiveAlways, NSTrackingInVisibleRect)
        model = getattr(self, "_model", None)
        if model is None:
            return
        for a in list(self.trackingAreas()):
            self.removeTrackingArea_(a)
        opt = NSTrackingMouseEnteredAndExited | NSTrackingActiveAlways | NSTrackingInVisibleRect
        self.addTrackingArea_(NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
            self.bounds(), opt, self, None))

    def mouse_entered(self, _ev):
        m = getattr(self, "_model", None)
        if m is None:
            return
        m.hovered = True
        _thin_show(self)
        _thin_paint(self)

    def mouse_exited(self, _ev):
        m = getattr(self, "_model", None)
        if m is None:
            return
        m.hovered = False
        if not m.dragging:
            _thin_arm_hide(self, THIN_IDLE_S)
        _thin_paint(self)

    def hit_test(self, _pt):
        """藏着的滑块**不吃点击**（细滑块不该在右边偷走一条 16pt 的命中区）。"""
        m = getattr(self, "_model", None)
        if m is None:
            return None
        if m.hovered or m.dragging or m.alpha() > 0.02:
            return self
        return None

    def mouse_down(self, ev):
        m = getattr(self, "_model", None)
        sc = self.superview()
        if m is None or sc is None:
            return
        pt = self.convertPoint_fromView_(ev.locationInWindow(), None)
        rect = m.rect()
        if rect is not None and rect[1] <= pt.y <= rect[1] + rect[3]:
            m.dragging = True                             # 抓住滑块
            self._grab = pt.y - rect[1]
            self._dragged = False
        else:                                             # 点轨道 = 翻页（Apple 支持页那两种语义里的「翻一页」）
            # ⚠️ 指针在滑块**上方**（AppKit y 更大）= 内容更靠文档开头那一侧 → 往回翻
            _thin_page_to(sc, -1 if (rect is None or pt.y > rect[1] + rect[3]) else 1)
        _thin_show(self)
        _thin_paint(self)

    def mouse_dragged(self, ev):
        m = getattr(self, "_model", None)
        sc = self.superview()
        if m is None or sc is None or not m.dragging:
            return
        pt = self.convertPoint_fromView_(ev.locationInWindow(), None)
        self._dragged = True
        rect = m.rect()
        thumb_h = rect[3] if rect else THIN_MIN_H
        knob_bottom = min(max(0.0, pt.y - getattr(self, "_grab", 0.0)), m.strip_h - thumb_h)
        frac = thin_frac_for_y(knob_bottom=knob_bottom, strip_h=m.strip_h, thumb_h=thumb_h)
        _thin_scroll_to_fraction(sc, frac)
        _thin_paint(self)

    def mouse_up(self, _ev):
        m = getattr(self, "_model", None)
        if m is None:
            return
        m.dragging = False
        _thin_arm_hide(self, THIN_IDLE_S)
        _thin_paint(self)

    def liveScroll_(self, _n):
        """用户在滚（滚轮/触控板）→ 亮起来。**程序化滚动不走这条**（那不该闪）。"""
        m = getattr(self, "_model", None)
        if m is None:
            return
        m.shown = True
        _thin_sync(self, repaint=True)
        _thin_arm_hide(self, THIN_IDLE_S)

    def scroll_wheel(self, ev):
        """滚轮**交还给滚动视图** —— 细滚动条只负责画，不截流。"""
        sc = self.superview()
        if sc is not None:
            sc.scrollWheel_(ev)

    return {
        "updateTrackingAreas": update_tracking_areas,
        "mouseEntered_": mouse_entered,
        "mouseExited_": mouse_exited,
        "hitTest_": hit_test,
        "mouseDown_": mouse_down,
        "mouseDragged_": mouse_dragged,
        "mouseUp_": mouse_up,
        "scrollWheel_": scroll_wheel,
        "liveScroll_": liveScroll_,
        "hideTick_": lambda self, _t: _thin_hide(self),
        "scrollMoved_": lambda self, _n: _thin_scrolled(self),
        "frameChanged_": lambda self, _n: _thin_sync(self, repaint=True),
        "contrastChanged_": lambda self, _n: _thin_contrast(self),
        "styleChanged_": lambda self, _n: _thin_style_back(self),
    }


def _thin_view_cls():
    from AppKit import NSView
    return objc_own.own("ThinScroller", NSView, _thin_hooks())


# ── 视图侧（模块级函数：ObjC 方法只能是模块级函数，状态在 `self._model` 上）────
def _thin_apply_geometry(view) -> None:
    """把模型算出来的 `(rect, width, alpha)` 落到图层上（几何**关动画**，透明度**走动画**）。"""
    from Quartz import CATransaction
    m = getattr(view, "_model", None)
    thumb = getattr(view, "_thumb", None)
    if m is None or thumb is None:
        return
    rect = m.rect()
    CATransaction.begin()
    CATransaction.setDisableActions_(True)                # ⚠️ 几何不做隐式动画（判据要的是即时值）
    if rect is None:
        thumb.setFrame_(((0.0, 0.0), (0.0, 0.0)))
    else:
        thumb.setFrame_(((rect[0], rect[1]), (rect[2], rect[3])))
        thumb.setCornerRadius_(rect[2] / 2.0)
    CATransaction.commit()


def _thin_paint(view) -> None:
    """刷一次：几何 + 颜色 + 透明度。"""
    from Quartz import CATransaction
    from AppKit import NSColor
    m = getattr(view, "_model", None)
    thumb = getattr(view, "_thumb", None)
    if m is None or thumb is None:
        return
    _thin_apply_geometry(view)
    a = m.alpha()
    try:
        # ⚠️⚠️ **颜色本身不带 alpha**，透明度只走 `opacity` 这一个旋钮。
        #    两个都带 = alpha 被乘两次（2026-10-18 量出来：白 α0.28 画出来只有 0.078，
        #    位图里最大字节 20 而不是 71 —— 屏上也会明显偏暗）。
        thumb.setBackgroundColor_(NSColor.whiteColor().CGColor())
    except Exception:                                     # noqa: BLE001
        pass
    CATransaction.begin()
    CATransaction.setAnimationDuration_(motion.STATE_S)   # 原地换状态那一档
    thumb.setOpacity_(a)
    CATransaction.commit()


def _thin_sync(view, *, repaint: bool = True) -> None:
    """从滚动视图读回真值（视口 / 文档 / 位置），再把带子摆到右缘。"""
    m = getattr(view, "_model", None)
    sc = view.superview()
    if m is None or sc is None:
        return
    clip = sc.contentView()
    doc = sc.documentView()
    sb = sc.bounds().size
    vs = clip.bounds().size if clip is not None else (0.0, 1.0)
    m.clip_h = float(vs.height)
    m.strip_w = min(float(sb.width), THIN_STRIP)
    m.strip_h = float(sb.height)
    old_doc = getattr(view, "_doc_seen", None)
    if doc is not None and doc is not old_doc:            # 文档换了 → 盯上它的尺寸变化
        try:
            from AppKit import NSViewFrameDidChangeNotification
            center = _nc()
            if old_doc is not None:
                center.removeObserver_name_object_(view, NSViewFrameDidChangeNotification, old_doc)
            doc.setPostsFrameChangedNotifications_(True)
            center.addObserver_selector_name_object_(
                view, b"frameChanged:", NSViewFrameDidChangeNotification, doc)
            view._doc_seen = doc
        except Exception:                                 # noqa: BLE001
            pass
    try:
        vis = sc.documentVisibleRect()
        m.origin_y = float(vis.origin.y)
        m.doc_h = float(doc.bounds().size.height) if doc is not None else m.clip_h
    except Exception:                                     # noqa: BLE001
        pass
    # 带子自己贴在滚动视图的右缘（跟随尺寸变化）
    try:
        view.setFrame_(((float(sb.width) - m.strip_w, 0.0), (m.strip_w, float(sb.height))))
    except Exception:                                     # noqa: BLE001
        pass
    if repaint:
        _thin_paint(view)


def _thin_scrolled(view) -> None:
    """视口变了（任何原因）→ 只同步几何，**不亮**（亮不亮由 `liveScroll_` 决定）。

    ⚠️ 分这两条是因为：面板自己会程序化滚动（列表刷新、滚回顶），
       那不该像"用户滚了一下"那样闪一下滚动条（2026-10-18 实测到的差别）。
    """
    _thin_sync(view, repaint=True)


def _thin_show(view) -> None:
    m = getattr(view, "_model", None)
    if m is None:
        return
    m.shown = True
    _thin_sync(view, repaint=True)
    _thin_arm_hide(view, THIN_IDLE_S)


def _thin_arm_hide(view, delay: float) -> None:
    """`delay` 秒后淡出（一次性定时器；每次活动都重排）。"""
    from AppKit import NSTimer
    t = getattr(view, "_hide_timer", None)
    if t is not None:
        try:
            t.invalidate()
        except Exception:                                 # noqa: BLE001
            pass
    m = getattr(view, "_model", None)
    if m is None or not (m.shown and not m.hovered and not m.dragging):
        view._hide_timer = None
        return
    view._hide_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        float(delay), view, b"hideTick:", None, False)


def _thin_hide(view) -> None:
    m = getattr(view, "_model", None)
    if m is None:
        return
    view._hide_timer = None
    m.shown = False
    _thin_paint(view)                                     # 定时器本来就在主 run loop，直接刷


def _contrast_on() -> bool:
    """系统「增大对比度」现在开着吗。

    ⚠️ 单独一个函数是**给判据留的口子**：测试替换它就能验「开着时更粗」，
       不必真去动系统的无障碍设置。
    """
    try:
        from AppKit import NSWorkspace
        return bool(NSWorkspace.sharedWorkspace().accessibilityDisplayShouldIncreaseContrast())
    except Exception:                                     # noqa: BLE001
        return False


def _thin_contrast(view) -> None:
    """「增大对比度」变了 → 跟着变粗/变实。"""
    m = getattr(view, "_model", None)
    if m is not None:
        m.contrast = _contrast_on()
    _thin_paint(view)


def _thin_style_back(view) -> None:
    """用户改了「显示滚动条」/ 插了鼠标 → **重设 overlay**（报告 §3.2：系统会自己改回去）。"""
    from AppKit import NSScrollerStyleOverlay
    sc = view.superview()
    if sc is None:
        return
    try:
        if sc.scrollerStyle() != NSScrollerStyleOverlay:
            sc.setScrollerStyle_(NSScrollerStyleOverlay)
    except Exception:                                     # noqa: BLE001
        pass


def _nc():
    from AppKit import NSNotificationCenter
    return NSNotificationCenter.defaultCenter()


def _thin_scroll_to_fraction(sc, frac: float) -> None:
    """把滚动视图滚到「文档的 `frac` 处」。"""
    clip = sc.contentView()
    doc = sc.documentView()
    if clip is None or doc is None:
        return
    doc_h = float(doc.bounds().size.height)
    clip_h = float(clip.bounds().size.height)
    span = max(0.0, doc_h - clip_h)
    clip.scrollToPoint_((0.0, min(max(0.0, float(frac) * span), span)))
    sc.reflectScrolledClipView_(clip)


def _thin_page_to(sc, direction: int) -> None:
    """点轨道 = 翻一页（`direction` > 0 = 往下）。"""
    clip = sc.contentView()
    if clip is None:
        return
    doc = sc.documentView()
    doc_h = float(doc.bounds().size.height) if doc is not None else float(clip.bounds().size.height)
    clip_h = float(clip.bounds().size.height)
    span = max(0.0, doc_h - clip_h)
    page = max(24.0, clip_h * 0.9)
    y = float(clip.bounds().origin.y) + (page if direction > 0 else -page)
    clip.scrollToPoint_((0.0, min(max(0.0, y), span)))
    sc.reflectScrolledClipView_(clip)


def install_thin_scroller(sc):
    """给一个 `NSScrollView` 装上细滚动条（`make_scroll_view` 里调；返回那个视图）。

    ⚠️ 系统滚动条要**先关掉**（`setHasVerticalScroller_(False)`）—— 两条一起出现就露馅了。
    ⚠️ 滚轮不受影响：滚动是滚动视图/剪裁视图管的，跟滚动条在不在没关系
       （判据：`test_滚轮在细滚动条上照样滚`）。
    """
    from AppKit import (NSViewFrameDidChangeNotification, NSViewBoundsDidChangeNotification)
    try:
        sc.setHasVerticalScroller_(False)
        view = _thin_view_cls().alloc().initWithFrame_(((0.0, 0.0), (THIN_STRIP, 10.0)))
        view.setWantsLayer_(True)
        view._model = _Thin(strip_w=THIN_STRIP, strip_h=10.0)
        try:
            from Quartz import CALayer
            thumb = CALayer.layer()
            thumb.setZPosition_(10.0)
            view.layer().addSublayer_(thumb)
            view._thumb = thumb
            view._grab = 0.0
            view._dragged = False
            view._doc_seen = None
            view._hide_timer = None
            sc.addSubview_(view)
        except Exception:                                 # noqa: BLE001
            return None
        view._model.contrast = _contrast_on()             # 「增大对比度」读一次
        # 监听：滚动 / 尺寸 / 系统两条通知
        sc.setPostsFrameChangedNotifications_(True)
        center = _nc()
        center.addObserver_selector_name_object_(view, b"frameChanged:",
                                                 NSViewFrameDidChangeNotification, sc)
        clip = sc.contentView()
        if clip is not None:
            clip.setPostsBoundsChangedNotifications_(True)
            center.addObserver_selector_name_object_(view, b"scrollMoved:",
                                                     NSViewBoundsDidChangeNotification, clip)
        from AppKit import (NSScrollViewWillStartLiveScrollNotification,
                            NSScrollViewDidLiveScrollNotification,
                            NSPreferredScrollerStyleDidChangeNotification,
                            NSWorkspaceAccessibilityDisplayOptionsDidChangeNotification)
        center.addObserver_selector_name_object_(view, b"liveScroll:",
                                                 NSScrollViewWillStartLiveScrollNotification, sc)
        center.addObserver_selector_name_object_(view, b"liveScroll:",
                                                 NSScrollViewDidLiveScrollNotification, sc)
        center.addObserver_selector_name_object_(view, b"styleChanged:",
                                                 NSPreferredScrollerStyleDidChangeNotification, None)
        center.addObserver_selector_name_object_(
            view, b"contrastChanged:", NSWorkspaceAccessibilityDisplayOptionsDidChangeNotification,
            None)
        _thin_sync(view)
        return view
    except Exception:                                     # noqa: BLE001
        return None


# ══════════════════════════════════════════════════════════════════════
# 拖拽落点
# ══════════════════════════════════════════════════════════════════════
def file_paths(pasteboard) -> list[str] | None:
    """从 pasteboard 里取文件路径。

    ⚠️⚠️ **`None` 是「读失败」、`[]` 是「没有文件」—— 必须分开。**
    `readObjectsForClasses:options:` 的 `nil` 是错误，不是空。合成一个判断的话
    「读失败」会被当成「拖了个非文件」，归因就全错了（`RESEARCH-macos-aesthetic.md` §11）。

    ⚠️ 按 UTI 过滤（`.urlReadingFileURLsOnly`）而不是自己判后缀 —— 后缀不代表类型。
    """
    from AppKit import NSURL, NSPasteboardURLReadingFileURLsOnlyKey
    try:
        got = pasteboard.readObjectsForClasses_options_(
            [NSURL], {NSPasteboardURLReadingFileURLsOnlyKey: True})
    except Exception:                                     # noqa: BLE001
        return None
    if got is None:
        return None
    out: list[str] = []
    for u in got:
        try:
            p = u.path()
        except Exception:                                 # noqa: BLE001
            continue
        if p:
            out.append(str(p))
    return out


# ⚠️ 哨兵：用来区分「**回调缺失**」（该拒）与「**回调返回了 `None`**」（算收）。
#    两者都必须和"回调抛异常"（也是拒）分开，见 `perform_drag`。
_NO_CALLBACK = object()


def make_drop_target(on_enter, on_drop, on_exit=None, *, types=None):
    """一个**只做落点**的视图：把 AppKit 那五个选择子收在一处。

    接口故意**不暴露 `NSDraggingInfo`** —— 调用方拿到的是
    `on_enter(pasteboard) -> bool` 与 `on_drop(paths: list[str])`。
    读 URL 那套（含 `nil`/空数组的区分、UTI 过滤）归这里的 `file_paths()`，
    于是每个落点不用各写一遍、也不会各错一遍。

    ⚠️⚠️ **回调是挂在实例上的，不是烙进类里的 —— 这一条是必须的，不是风格。**
    `objc_own.own()` **按 key 缓存类**：同一个 key 第二次调用拿回的是**同一个类对象**，
    所以任何写进 namespace 的闭包都还是**第一次**那个。要是图省事写成
    `own("DropTarget", NSView, {"draggingEntered_": lambda self, s: ...on_enter...})`，
    那么**第二个落点会调用第一个落点的回调** —— 今天 5 张卡就是「点哪张都进同一门课」。
    本函数因此把回调存成 `self._on_*`，方法体里 `getattr(self, ...)` 现取。
    （`entry_panel._button_class` 那边是同一个坑的另一半。）

    ⚠️ **五条踩过的约定**（都在 `RESEARCH-macos-aesthetic.md` §11）：
      · 「收不收」由 `draggingEntered_` 的返回值决定，**不是** `prepareForDragOperation_`
      · `performDragOperation_` **默认返回 false** —— 忘了实现 = 静默不收
      · 在 `draggingEntered_` 里查 pasteboard（**只查一次**），别放 `draggingUpdated_`
      · 返回 `NSDragOperationNone` 之后**仍会**收到 `draggingUpdated_`/`draggingExited_`
      · ⚠️ 用 `sender.draggingPasteboard()`，**别自己开 `NSPasteboard(name:)`** ——
        跨进程时「there is NO guarantee that this will be the pasteboard used」

    ⚠️ **回调里一律 `except` 并记日志。** AppKit 会吞掉回调里的异常，
       症状和「这个回调根本没被调用」一模一样 —— 不记下来就等于没有量具。
       （`probe_drag.py` 的 v1 就因为少了这层，把一次 `AttributeError` 看成了「拖拽收不到」。）

    ⚠️ **落点成功时不会收到 `draggingExited_`** —— 悬停高亮要在 `on_drop` 里也撤一次，
       不能只靠 `on_exit`。（HIG 逐字要求「people drag the content away 时撤掉」。）
    """
    import objc
    from AppKit import (NSDragOperationCopy, NSDragOperationNone, NSView,
                        NSPasteboardTypeFileURL)

    def _log(what: str) -> None:
        if os.environ.get("CLASSLIVE_DEBUG"):
            print(f"[drop] {what}", flush=True)

    def _call(self, which, *a, default=None):
        """取实例上的回调并调它。**异常必须落到日志**（见上面那条）。"""
        fn = getattr(self, which, None)
        if fn is None:
            return default
        try:
            return fn(*a)
        except Exception as e:                            # noqa: BLE001
            _log(f"{which} 抛异常 —— 这次观察无效：{type(e).__name__}: {e}")
            if os.environ.get("CLASSLIVE_DEBUG"):
                import traceback
                traceback.print_exc()
            return default

    def dragging_entered(self, sender):
        pb = sender.draggingPasteboard()
        self._n_upd = 0
        ok = bool(_call(self, "_on_enter", pb, default=False))
        # ⚠️ 决定**存下来** —— `draggingUpdated:` 要原样回放它（见那个方法）。
        self._accept = ok
        _log(f"[{getattr(self, '_tag', '?')}] entered -> {'收' if ok else '拒'}  "
             f"types={list(pb.types() or [])}")
        return NSDragOperationCopy if ok else NSDragOperationNone

    def dragging_updated(self, sender):
        """**回放 `draggingEntered:` 的决定**，不重新判定。

        ⚠️ 契约（AppKit 归档指南逐字，见 `PLAN-entry-panel.md §2.3`）：
           「`prepareForDragOperation:` 取决于 **the most recent invocation of
             `draggingEntered:` or `draggingUpdated:`**」—— 也就是说 updated 也在投票。
           原来这里**无条件返 `Copy`**：enter 已经拒了，updated 又把它收回来 →
           **用户悬停时看到"能收"，松手却什么都不会发生**（正是 `REVIEW §9.2 #5` 那条）。
        ⚠️ 为什么**不在这里重查 pasteboard**：公开约定是「在 `draggingEntered:` 里查
           （**只查一次**），别放 `draggingUpdated:`（那个会调多次）」。
        ⚠️ 为什么**存自己的决定**、而不是假设"返了 None 就不再被问"：契约另有一条
           「返回 `NSDragOperationNone` 之后**仍会**收到 `draggingUpdated:` /
             `draggingExited:`」。
        """
        self._n_upd = getattr(self, "_n_upd", 0) + 1
        return (NSDragOperationCopy if getattr(self, "_accept", False)
                else NSDragOperationNone)

    def dragging_exited(self, sender):
        self._accept = False
        _log(f"exited（期间 {getattr(self, '_n_upd', 0)} 次 draggingUpdated）")
        _call(self, "_on_exit")

    def prepare_for_drag(self, sender):
        return True

    def perform_drag(self, sender):
        paths = file_paths(sender.draggingPasteboard())
        _log(f"drop -> {paths!r}")
        # ⚠️⚠️ **「有回调但返回 `None`」算收；「回调缺失」算拒** —— 两者必须分开。
        #    · 返回 `None`：调用方**忘写 `return`** 时 `bool(None)` 会把一次**已经发生**的
        #      落盘说成"没收下"，而 `on_drop` 往往已经把事情做完了
        #      （`entry_panel.run_prep` 就是"跑起来了"）→ 用户看到文件弹回去 + 它自己在干活。
        #    · 缺回调：那是"这个落点没接线"，该拒。
        #    用一个**哨兵**把这两种 `None` 区分开（`_call` 的 `default` 同时覆盖"缺失"与"抛异常"）。
        got = _call(self, "_on_drop", paths, default=_NO_CALLBACK)
        if got is _NO_CALLBACK:
            return False
        return True if got is None else bool(got)

    def mouse_down(self, event):
        """点一下 = `self._on_click()`（卡片「选中这门」，2026-10-14）；没挂回调就照常往上传。

        ⚠️ 卡上的按钮（「开始上课」/「删」/「撤销」）自己吃掉点击，到不了这里 —— 只有
           空白处和文字标签（不可选的 `NSTextField` 把事件往上交）才会进来。
        """
        if getattr(self, "_on_click", None) is None:
            objc.super(type(self), self).mouseDown_(event)
            return
        _call(self, "_on_click")

    cls = objc_own.own("DropTarget", NSView, {
        "mouseDown_": mouse_down,
        "draggingEntered_": dragging_entered,
        "draggingUpdated_": dragging_updated,
        "draggingExited_": dragging_exited,
        "prepareForDragOperation_": prepare_for_drag,
        "performDragOperation_": perform_drag,
    })
    v = cls.alloc().initWithFrame_(((0.0, 0.0), (100.0, 100.0)))
    v._n_upd = 0
    # ⚠️ `_accept` 初值必须是 **False**：`draggingUpdated:` 可能在 `draggingEntered:`
    #    之前被问到（契约没保证顺序），那时"还没判过"只能当**拒** —— 当"收"就是在替
    #    调用方承诺一件没人答应过的事。
    v._accept = False
    v._on_enter, v._on_drop, v._on_exit = on_enter, on_drop, on_exit
    v.registerForDraggedTypes_(list(types) if types else [NSPasteboardTypeFileURL])
    return v


# ══════════════════════════════════════════════════════════════════════════════
# 输入框的聚焦指示：关掉系统蓝环 + 补一圈白细框（作者 2026-10-17 拍板）
# ══════════════════════════════════════════════════════════════════════════════
# 背景（`entry_panel.py` 2100-2150 那段踩坑记的原文）：那个蓝环是**系统聚焦环**，
# 颜色**不可设**（`NSFocusRingType` 只有三个枚举、无颜色参数，它取用户的系统强调色）。
# 苹果的 HIG 允许「**用背景/边框表示聚焦**时关掉系统环」，而**不补替代**就是
# W3C F78（违反 1.4.11 非文本对比 / 2.4.7 聚焦可见）—— 所以「关环」和「补白细框」
# 是**一件事的两半**，本模块把两半放在同一个入口里（`focus_border` / `focus_field`）。
#: 那圈白细框（1 **物理像素**）的两档白 α：静止 ↔ 聚焦。
#: ⭐ **全 app 一套**：⌨ 提问输入行的细线、卡片输入框的外壳、课程面板的搜索框与
#:    「＋新建」输入框都读这两档 —— 别再各写一份数。
FOCUS_RING_IDLE = 0.18
FOCUS_RING_ON = 0.55


def hairline() -> float:
    """1 **物理像素**折成的点数（Retina 0.5pt / 非 Retina 1pt）—— 发丝边的宽度。

    ⚠️ 描边用 `1.0` 会在 2x 屏上画成 2 物理像素（一条糊边）；量过才写这个函数。
    """
    try:
        from AppKit import NSScreen
        scr = NSScreen.mainScreen()
        scale = float(scr.backingScaleFactor()) if scr is not None else 2.0
    except Exception:                                        # noqa: BLE001
        scale = 2.0
    return 1.0 / max(1.0, scale)


def no_focus_ring(field) -> bool:
    """关掉这个控件的**系统聚焦环**。回 `True` = 设上了（fail-soft 不抛）。

    ⚠️⚠️ **必须用常量 `NSFocusRingTypeNone`，别写数字**：`NSFocusRingType` 里
      `Default = 0`、`None = 1`（本机 PyObjC 实测）—— `setFocusRingType_(0)` 是
      「我要系统默认」，蓝环照画。2026-10-17 在卡片输入框上抓到的就是这个形状：
      **注释写着「去掉 AppKit 默认蓝环」、值写的是 0**，于是环一直在。
    ⚠️ 只关环**不算完**：调用方得补替代指示（见 `focus_border`）—— 理由见本段文件头。
    """
    try:
        from AppKit import NSFocusRingTypeNone
        field.setFocusRingType_(NSFocusRingTypeNone)
        return True
    except Exception:                                        # noqa: BLE001
        return False


class FocusBorder:
    """**替代的聚焦指示**：一圈白色发丝边（`layer` 的 border，1 物理像素）。

    α 两档：`FOCUS_RING_IDLE`（静止）↔ `FOCUS_RING_ON`（聚焦）。
    句柄给调用方两件东西：
      · `set_focus(bool)` —— **谁来驱动都行**（事件路 / 每帧轮询路，见 `focus_field`）；
      · `alpha()` —— **从图层读回**当前描边 α，判据直接断言它（别去记"我们想设的值"）。

    ⚠️⚠️ **句柄只拿控件那个图层（`layer`），不拿控件本身。** `focus_field` 会把句柄
      挂在控件自己的 `__dict__` 里（`fld._focus_border = handle`）—— 那条链如果同时
      反向指回控件，就是一个 `控件 → 句柄 → 控件` 的环，而 **PyObjC 对象参与的环
      收集不掉**：2026-10-17 实测 3 个 `panel.focus_field(NSTextField, …)` 实例**一个
      都收不回**（`test_panel.py`「开/关 N 轮存活对象一样多」那条当场红：9 轮多出 9 个框）。
      ⚠️ 试过用**弱引用**绕开，**不行**：PyObjC 实例根本不给建弱引用
      （`TypeError: cannot create weak reference to '_ClassLive…' object`，本机实测）——
      所以只能**单向**：图层不反向持有视图（`CALayer.delegate` 本来就是弱引用），
      控件活着时图层跟着活，控件走了整条链一起走。
    """

    def __init__(self, view, *, radius: float):
        self.radius = float(radius)
        self._on = False
        self._painted = False
        self.layer = None
        try:
            view.setWantsLayer_(True)
            lay = view.layer()
            lay.setBorderWidth_(hairline())
            lay.setCornerRadius_(self.radius)
            self.layer = lay
            self._ok = lay is not None
        except Exception:                                    # noqa: BLE001
            self._ok = False
        self.set_focus(False)

    def ok(self) -> bool:
        """装上了没有（没装上时 `alpha()` 回 -1）。"""
        return bool(self._ok)

    def is_focused(self) -> bool:
        return self._on

    def set_focus(self, on: bool) -> None:
        """切那一圈边的 α。**幂等**（值没变直接返回）—— 每帧轮询那条路会一直调它。"""
        on = bool(on)
        if on == self._on and self._painted:
            return
        try:
            from AppKit import NSColor
            alpha = FOCUS_RING_ON if on else FOCUS_RING_IDLE
            self.layer.setBorderColor_(
                NSColor.whiteColor().colorWithAlphaComponent_(alpha).CGColor())
            # ⚠️ 描边色设上了才认这次切换（`_painted` 在副作用之后写 —— 与
            #    `overlay._sync_focus_look` 同一个理由：失败要留下重试的机会）。
            self._painted = True
        except Exception:                                    # noqa: BLE001
            pass
        self._on = on

    def alpha(self) -> float:
        """**从图层读回**的描边 α；读不到回 `-1.0`（判据要的是真值，不是我们想设的值）。"""
        try:
            from AppKit import NSColor
            if self.layer is None:
                return -1.0
            c = self.layer.borderColor()
            if c is None:
                return -1.0
            return float(NSColor.colorWithCGColor_(c).alphaComponent())
        except Exception:                                    # noqa: BLE001
            return -1.0


def focus_border(field, *, radius: float, host=None) -> FocusBorder | None:
    """给一个**已经建好**的输入框装替代聚焦指示（**并顺手关掉系统环**）。

    返回句柄（`set_focus` / `alpha`）；装不上回 `None`（界面照常能用，只是少了这圈边）。
    `host` = 画那圈边的视图（默认就是输入框自己）。卡片那两格传的是**外壳** ——
    边画在外壳上、字段内缩在里，是那边一直以来的样子（见 `overlay._card_field_box`）。

    ⚠️ 本函数**不装聚焦检测**（谁聚焦谁失焦）—— 要么用 `focus_field()` 造控件
       （事件路），要么由调用方的每帧/每拍轮询来调 `set_focus`（轮询路）。
    """
    no_focus_ring(field)
    try:
        return FocusBorder(host if host is not None else field, radius=radius)
    except Exception:                                        # noqa: BLE001
        return None


def focus_field(base, rect, *, radius: float, host=None, key: str = "FocusField"):
    """造一个**自带聚焦响应**的输入框：`base` 的子类 + `focus_border` 那圈边。

    返回 `(控件, 句柄)` —— 句柄可能是 `None`（那圈边没装上也照样把控件给你）。

    ⚠️ **为什么要子类**（而不是委托回调）：`NSTextField` 的**委托**
      `controlTextDidBeginEditing_` / `controlTextDidEndEditing_` 在本 app 的启动
      路径下**实测从不触发**（见 `overlay._make_input_delegate` 的注释）。
      改用**控件自己的方法**：`becomeFirstResponder` 亮 + `textDidEndEditing_` 灭 ——
      为什么是这两条、为什么不是 `resignFirstResponder`，见 `_focus_hooks` 的注释。
    ⚠️ **普通 `NSTextField` 实例上挂不了 Python 属性**（`AttributeError`，本机实测），
      所以句柄只能存在**本函数造的那个子类实例**上（子类实例挂得上）——
      这也是"非得有个工厂"的原因，不是图省事。
    ⚠️ **类名交给 `objc_own`**（全项目唯一的 ObjC 类注册点）：`key` 换个 base 会抛
      `ObjcNameCollision` —— 搜索框（`NSSearchField`）与普通框要**用不同的 key**。
    """
    import objc
    fld = None
    try:
        cls = objc_own.own(key, base, _focus_hooks(key))
        fld = cls.alloc().initWithFrame_(rect)
    except Exception:                                        # noqa: BLE001
        fld = None
    if fld is None:                                          # 子类这条路断了 → 退普通控件
        try:
            fld = base.alloc().initWithFrame_(rect)
        except Exception:                                    # noqa: BLE001
            return None, None
    handle = focus_border(fld, radius=radius, host=host if host is not None else fld)
    try:
        fld._focus_border = handle
    except Exception:                                        # noqa: BLE001
        pass
    return fld, handle


def _focus_hooks(key: str) -> dict:
    """`focus_field` 那个子类的方法表：**聚焦 → 亮，编辑结束 → 暗**。

    ⚠️⚠️ **不是 `becomeFirstResponder` / `resignFirstResponder` 这一对**
      （第一版就是这么写的，2026-10-17 在真窗口上量出来是错的）：`NSTextField`
      拿到第一响应者之后会**立刻把 field editor（NSTextView）换上去**，于是字段
      自己紧接着就被 `resignFirstResponder` —— 日志逐字是 `become, resign`
      **成对出现**，而此刻正文**正在编辑**（`currentEditor()` 非空）。照那一对做，
      圈边会在点进去的瞬间亮一下又灭。
    实测有效的两条（同一次实验的四种路径都量过：点另一个框 / Esc / 点空白 / 回车提交）：
      · 亮 = `becomeFirstResponder` —— 点进去必到，且**必须调基类实现**：
        它负责开始编辑（不调 = 框点进去打不了字）；
      · 灭 = `textDidEndEditing_` —— **编辑真的结束**才来（`resignFirstResponder`
        在 field editor 接管那一步就会来，判不了）。
    ⚠️ 委托那对（`controlTextDidBeginEditing_` / `controlTextDidEndEditing_`）在本 app
      的启动路径下**从不触发**，别换回去（`overlay._make_input_delegate` 的注释记着同一条）。
    ⚠️ `objc.super` 的第一参要用 `objc_own.cls_of(key)`，**别用 `type(self)`**
      （类被子类化时会从子类开始找，行为不一样 —— `objc_own.cls_of` 的 docstring 逐字记过）。
    """
    import objc

    def _become(self):
        h = getattr(self, "_focus_border", None)
        if h is not None:
            h.set_focus(True)
        return objc.super(objc_own.cls_of(key), self).becomeFirstResponder()

    def _end(self, note):
        h = getattr(self, "_focus_border", None)
        if h is not None:
            h.set_focus(False)
        return objc.super(objc_own.cls_of(key), self).textDidEndEditing_(note)

    return {"becomeFirstResponder": _become, "textDidEndEditing_": _end}


def make_label(text, rect, size, *, alpha=1.0, bold=False,
               color=None, selectable=False, truncate=False):
    """面板里的一行文字。**默认不可选、无 bezel、无底色** —— 这三样是每条都要设的。

    ⚠️ 别用 `NSFont.systemFontOfSize_` 之外的自造字号（HIG 那 11 档，
       见 `RESEARCH-macos-aesthetic.md` §2.1）；字距也不要手动加（系统字体自带，
       同节实测证伪过「HIG 那张表是让人手动加的」）。

    ⚠️⚠️ **不传 `truncate=True` 时，本函数根本不碰换行设置** ——
       拿到的是 `NSTextFieldCell` 自己的默认值 **`wraps=True` + `byWordWrapping`**。
       配上 16pt 高的框，效果是 **"文字换行、第二行起被悄悄吃掉，而且没有省略号"** ——
       正是本仓库记过的那个坑（`overlay.py` 文件头：**`1 行 + WordWrapping` 静默吞第二行**），
       比"硬切"更坏，因为看着像句子就到这儿了。
       → **长文本要单行省略号，必须显式传 `truncate=True`**（那会设
       `wraps=False` + `byTruncatingTail`，实测 `lineBreakMode == 4`）。
       ⚠️ 短标签（标题 / 准备度 / 表头）**走默认是对的** —— 它们放得下，
          `tests/test_panel.py` 那条判据也就只挑了结果区那三行来钉。

    ⚠️ 这里**曾经有一个 `wrap` 参数**，2026-09-30 删掉了：它**两个分支都没生效** ——
       `wrap=True` 设的 `setWraps_(True)` 本来就是默认值，而**全仓一个调用点都没有**
       （`wrapup.py` / `whatsnew.py` 里那些 `wrap=` 是它们**各自**的局部 helper）。
       留着的害处是：读签名的人会以为 `wrap=False` 关掉了换行，其实没有。
    """
    from AppKit import NSColor, NSFont, NSTextField

    lb = NSTextField.alloc().initWithFrame_(rect)
    lb.setStringValue_(text or "")
    lb.setEditable_(False)
    lb.setSelectable_(selectable)
    lb.setBezeled_(False)
    lb.setDrawsBackground_(False)
    lb.setFont_(NSFont.boldSystemFontOfSize_(size) if bold
                else NSFont.systemFontOfSize_(size))
    lb.setTextColor_(color or NSColor.whiteColor().colorWithAlphaComponent_(alpha))
    if truncate:
        from AppKit import NSLineBreakByTruncatingTail
        lb.cell().setLineBreakMode_(NSLineBreakByTruncatingTail)
    return lb


def make_button_target(cb):
    """把一个 Python 回调包成 ObjC target（`NSControl.setTarget_` 是**弱引用**）。

    ⚠️ **返回值必须由调用方持住**，否则被 Python GC 回收 —— 按钮点了没反应，
       而且 AppKit 不报错。`whatsnew` / `overlay` 各自持在 `_targets` 列表里。

    ⚠️ 与 `overlay._make_button_target` / `whatsnew._make_target` **共用同一个
       ObjC 类**（`objc_own` 的 key `ButtonTarget`，那正是"想共用就用同一个 key"
       的场合）。三处的闭包体是同一件事，收尾卡起用这一份。
    """
    import objc_own
    from AppKit import NSObject

    def clicked(self, sender):                            # noqa: N802
        f = getattr(self, "_cb", None)
        if f:
            f()

    t = objc_own.own("ButtonTarget", NSObject, {"clicked_": clicked}).alloc().init()
    t._cb = cb
    return t


def make_button(rect):
    """造一颗**带按压反馈**的按钮 —— 面板里可点按钮的唯一工厂。

    按下缩到 `motion.PRESS_SCALE`、抬起按 `motion.SPRING_STOPS` 回弹到 1
    （时长 / 曲线 / 过冲全在 `motion.py`，值来自 Claude Desktop 那套设计系统，
    2026-10-18）。拿到的就是一颗普通 `NSButton` 子类实例：`style_primary` /
    `setTarget_` / `setAction_` / 键盘等价键 / `performClick_` 全部照常。

    ⚠️ **为什么是子类**（而不是窗口级事件监视器 / `sendEvent_`）—— 2026-10-18
       真 CGEvent 探针实测：按钮的跟踪循环（`NSCell.trackMouse…`）把 mouseUp
       **直接吞掉**，本地事件监视器只看得到 mouseDown、**看不到 mouseUp**；
       而 `mouseDown_` 包一层 `super` 的写法里，`super` 恰好在 mouse-up 时返回
       （含「拖出去松开」），action 也在其中**恰好触发一次**。所以只有这条路
       拿得到「抬起」这个时机。
    ⚠️ **勾选框 / 菜单项 / 系统弹窗的按钮**不走这里（本工厂只管面板里可点的
       push button）；禁用的按钮在 `mouseDown_` 里自会跳过（`isEnabled`）。
    """
    return _press_button_class().alloc().initWithFrame_(rect)


def _press_button_class():
    """按压反馈的按钮类（`objc_own` 拥有，进程里只定义一次）。"""
    from AppKit import NSButton
    import objc

    def mouse_down(self, event):                          # noqa: N802
        try:
            if self.isEnabled():
                motion.press_down(self)
        except Exception:                                 # noqa: BLE001
            pass
        # 跟踪循环在 super 里面：一直转到 mouse-up（里面/外面松开都算）。
        objc.super(objc_own.cls_of("PressButton"), self).mouseDown_(event)
        try:
            motion.press_up(self)
        except Exception:                                 # noqa: BLE001
            pass

    return objc_own.own("PressButton", NSButton, {"mouseDown_": mouse_down})


#: 按钮三档分量，一眼看得出先点哪个（2026-10-12 设计评审；10-13 次要档也换成胶囊）：
#:   主按钮   实心白底、深色字 —— 一张卡上最想让人点的那**一**颗
#:   次要按钮 极淡的白底 + 发丝边、白字 —— 与主按钮同形同高，只是轻（不再用系统圆角壳：
#:            系统壳 20pt 高、6pt 圆角，跟 26pt 胶囊并排时两种形状打架）
#:   逃生口   无边框淡字（跳过 / 不存）
PRIMARY_BG_A, PRIMARY_FG_A = 0.92, 0.85
SECONDARY_BG_A, SECONDARY_LINE_A, SECONDARY_FG_A = 0.10, 0.16, 0.88


def _style_capsule(btn, text: str, *, size: float, weight: float, fg, bg, line=None) -> None:
    from AppKit import (NSAttributedString, NSFont, NSFontAttributeName,
                        NSForegroundColorAttributeName, NSMutableParagraphStyle,
                        NSParagraphStyleAttributeName, NSTextAlignmentCenter)
    btn.setBordered_(False)
    btn.setWantsLayer_(True)
    para = NSMutableParagraphStyle.alloc().init()
    para.setAlignment_(NSTextAlignmentCenter)
    btn.setAttributedTitle_(NSAttributedString.alloc().initWithString_attributes_(
        text, {NSFontAttributeName: NSFont.systemFontOfSize_weight_(size, weight),
               NSForegroundColorAttributeName: fg,
               NSParagraphStyleAttributeName: para}))
    lay = btn.layer()
    lay.setCornerRadius_(btn.frame().size.height / 2.0)
    lay.setBackgroundColor_(bg.CGColor())
    if line is not None:
        lay.setBorderWidth_(0.5)                          # 发丝线：scale 2 上正好 1 设备像素
        lay.setBorderColor_(line.CGColor())


def tinted_title(text: str, size: float, alpha: float):
    """无边框按钮的标题：白字、给定不透明度、居中（逃生口那一档用）。"""
    from AppKit import (NSAttributedString, NSColor, NSFont, NSFontAttributeName,
                        NSForegroundColorAttributeName, NSMutableParagraphStyle,
                        NSParagraphStyleAttributeName, NSTextAlignmentCenter)
    para = NSMutableParagraphStyle.alloc().init()
    para.setAlignment_(NSTextAlignmentCenter)
    return NSAttributedString.alloc().initWithString_attributes_(
        text, {NSFontAttributeName: NSFont.systemFontOfSize_(size),
               NSForegroundColorAttributeName: NSColor.whiteColor().colorWithAlphaComponent_(alpha),
               NSParagraphStyleAttributeName: para})


def style_primary(btn, text: str, *, size: float = 13.0) -> None:
    """把一颗 `NSButton` 画成主按钮。高度取按钮当前 frame（圆角 = 高度一半）。

    ⚠️ 只改外观：target / action / keyEquivalent 由调用方照旧设。
    ⚠️ 用 `attributedTitle` 定字色 —— 无边框按钮的 `contentTintColor` 管不到文字。
    """
    from AppKit import NSColor
    _style_capsule(btn, text, size=size, weight=0.4,
                   fg=NSColor.blackColor().colorWithAlphaComponent_(PRIMARY_FG_A),
                   bg=NSColor.whiteColor().colorWithAlphaComponent_(PRIMARY_BG_A))


def style_secondary(btn, text: str, *, size: float = 13.0) -> None:
    """次要按钮：与 `style_primary` 同形（胶囊、同高），极淡底 + 发丝边。规矩同上。"""
    from AppKit import NSColor
    w = NSColor.whiteColor()
    _style_capsule(btn, text, size=size, weight=0.0,
                   fg=w.colorWithAlphaComponent_(SECONDARY_FG_A),
                   bg=w.colorWithAlphaComponent_(SECONDARY_BG_A),
                   line=w.colorWithAlphaComponent_(SECONDARY_LINE_A))


def make_hover_owner(cb):
    """`NSTrackingArea` 的 owner：鼠标进 / 出时回调 `cb(True/False)`。**返回值由调用方持住**（同上）。

    ⚠️ 用独立 owner 而不是给按钮写子类：按钮仍是普通 `NSButton`，点击、拖窗判据都不变。
    """
    import objc_own
    from AppKit import NSObject

    def entered(self, event):                             # noqa: N802
        f = getattr(self, "_cb", None)
        if f:
            f(True)

    def exited(self, event):                              # noqa: N802
        f = getattr(self, "_cb", None)
        if f:
            f(False)

    t = objc_own.own("HoverOwner", NSObject,
                     {"mouseEntered_": entered, "mouseExited_": exited}).alloc().init()
    t._cb = cb
    return t


def place_beside(anchor, card) -> None:
    """把浮卡摆在主面板旁边，**放不下就换一边，四边都放不下才贴可见区左上角**。

    ⚠️ **必须先检查再落位**，不能算一个位置就 `setFrameOrigin_` —— 踩过：原本算的是
       「面板正下方」，算出来 y=-123 放不下 → 退回「面板正上方」，但屏幕可见区顶
       放不下，而 **AppKit 会把窗口夹回可见区** → 卡片掉下来正好盖住面板上半。
       （2026-09-28 从 `overlay._show_whatsnew_card` 提出来，两份浮卡共用一份定义。）

    ⚠️ 顺序是「左·右·下·上」：竖排（左/右）优先，因为主面板是横长条，
       摆在旁边不会压住字幕区。
    """
    from AppKit import NSScreen
    pf, cf = anchor.frame(), card.frame()
    gap = 10.0
    vis = NSScreen.mainScreen().visibleFrame()
    top = pf.origin.y + pf.size.height - cf.size.height      # 与面板顶对齐
    for x, y in (
        (pf.origin.x - cf.size.width - gap, top),            # 左
        (pf.origin.x + pf.size.width + gap, top),            # 右
        (pf.origin.x + pf.size.width - cf.size.width,
         pf.origin.y - cf.size.height - gap),                # 下
        (pf.origin.x + pf.size.width - cf.size.width,
         pf.origin.y + pf.size.height + gap),                # 上
    ):
        if (x >= vis.origin.x and y >= vis.origin.y
                and x + cf.size.width <= vis.origin.x + vis.size.width
                and y + cf.size.height <= vis.origin.y + vis.size.height):
            card.setFrameOrigin_((x, y))
            return
    card.setFrameOrigin_((vis.origin.x + gap,                   # 四边都放不下
                          vis.origin.y + vis.size.height - cf.size.height - gap))


def hide_traffic_lights(window) -> None:
    """藏掉左上角三个系统按钮 —— 但**保留** Titled 带来的原生缩放能力。

    这是 macOS 社区的既有做法：Christian Tietze 2020-10 那篇博客的标题就是
    《Hide Traffic Light Buttons in NSWindow Without Removing Resize Functionality》，
    Ghostty 的 `HiddenTitlebarTerminalWindow.swift` 同款。
    Tietze 藏的是**四个**（含 .fullScreenButton）；我们实测只有 3 个
    （styleMask 没设 FullScreen 位，type 7 为 None），所以循环写成 0..7 防御。

    ⚠️ 用 `setHidden_(True)` 而**不是** `removeFromSuperview()` —— 后者查不到任何
       来源支持，而 Tietze 与 mkll/NSWindowStyles 两处有出处的做法都用 isHidden。
       「红绿灯会自己回来」的社区实证指的是**位置**在 resize 后复位，不是可见性。
    ⚠️ 必须**可重复调用**：Ghostty 的注释原文 "macOS breaks it usually"，
       所以调用方在 show() 里也再调一次。
    """
    try:
        for i in range(8):
            b = window.standardWindowButton_(i)
            if b is not None:
                b.setHidden_(True)
    except Exception:                       # noqa: BLE001
        pass
