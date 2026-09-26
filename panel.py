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

import typing

# 白底可读性：材质之上压的半透明黑。可读性其实由文字的紧凑描边承担
# （见 overlay.py 顶部那段），这一层只负责把白底压到让描边有依托。
SCRIM_ALPHA = 0.38
CORNER_RADIUS = 16.0

#: 本模块拥有的 ObjC 类。key 是内部标识，值是类 —— **单一 definition point**。
_OWNED: "dict[str, type]" = {}

#: 所有本模块生成的 ObjC 类名都用这个前缀。改它没有意义，除非你同时确认
#: 进程里没有别的代码在用同一个前缀。
_PREFIX = "_ClassLive"


def _own_class(key: str, base: type, namespace: dict) -> type:
    """取（或首次定义）一个由本模块拥有的 ObjC 子类。

    ⚠️ **绝不 fail-soft、绝不返回 None。** 名字被占用时立刻抛 —— 静默失败正是
       2026-09-26 那次事故里最贵的部分（卡片不显示，而测试全绿）。
    ⚠️ 别用 `objc.getClassList()` 做预检：**实测它在定义前后都返回 `False`**
       （2026-09-26 独立复现），拿它当判据等于永远查不到。
       `objc.lookUpClass` 才是对的：缺了抛 `nosuchclass_error`，在则返回类。
    """
    got = _OWNED.get(key)
    if got is not None:
        return got
    import objc
    name = _PREFIX + key
    try:
        existing = objc.lookUpClass(name)
    except Exception:                       # noqa: BLE001 — nosuchclass_error
        existing = None
    if existing is not None:
        raise RuntimeError(
            f"ObjC 类名 {name} 已被占用（拿到的是 {existing}）——\n"
            f"  进程里有两份 panel.py，或者有人手工用了这个前缀。\n"
            f"  ⚠️ 别改这里去绕开：2026-09-26 那次「更新卡片静默变 None」"
            f"就是撞名被 fail-soft 吞掉的结果。先查清是谁占的。")
    cls = type(name, (base,), namespace)
    _OWNED[key] = cls
    return cls


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
    _super = objc.super          # 捕获一次 —— send_event 是**每个事件**都会跑的

    def send_event(self, event):
        """窗口收到的**每一个**事件都经过这里 —— 唯一绕不开的位置。

        ⚠️ 为什么不 override 某个视图的 `mouseDown_`：实测（2026-09-24）即使
           `contentView.hitTest_()` 明确返回了我们的拖拽层，它的 `mouseDown_`
           **一次都没被调用**（没有报错，静默）。窗口级的 sendEvent_ 没这个问题。
        """
        if event.type() == 1:               # NSEventTypeLeftMouseDown
            try:
                from AppKit import NSApplication
                NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
            except Exception:               # noqa: BLE001
                pass
        _super(type(self), self).sendEvent_(event)

    return _own_class("Panel", NSPanel, {
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

    cls = _own_class("DragLayer", NSView, {
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

    cls = _own_class("ResizeDelegate", NSObject, {
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


def build(rect, style, *, on_background_click=None, on_resize=None) -> FrostedPanel:
    """建一个配好 chrome 的磨砂面板。

    顺序是**载荷性的**，别调：appearance -> 窗口属性 -> glass -> scrim -> drag。
    后面每个调用方往 `glass` 里加的视图都会落在 drag 之上，各自照常收事件。

    `on_background_click` 只在点到**空白处**时触发（最外一圈，给嵌套循环缩放用）。
    `on_resize` 给了才装窗口委托 —— 不给就不装（不缩放的面板不需要它）。
    """
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
