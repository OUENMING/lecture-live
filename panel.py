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

import objc_own

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

    ⚠️ **只抽这四行，不是抽整份实现。** 2026-09-26 逐行比对过现存两个滚动区
    （`whatsnew._add_log_view` 与 `transcript_view`）：真正重合的**只有这四行**，
    其余都是各自场景的行为（一个是 NSTextView 的文本管道，一个是「所有行等高 →
    O(1) 除法」的槽位池）。把它们一起抽过来是白带的复杂度。

    这四行**有原因，所以值得有唯一定义点**：
      · `drawsBackground_(False)` —— 面板是 vibrancy，HIG 明说别在控件下垫不透明底
      · `borderType_(0)` —— 不要 bezel
      · overlay 滚动条 + 按需出现 —— 本 app 既有的观感（overlay 与 whatsnew 都这么设）

    ⚠️ **不含 `setVerticalScrollElasticity_`** —— `transcript_view` 那项是**动态**的
    （构造设 0，之后按状态改 Automatic），不是静态约定，硬抄会把一个行为塞进配方。
    """
    from AppKit import NSScrollView, NSScrollerStyleOverlay

    sc = NSScrollView.alloc().initWithFrame_(rect)
    sc.setDrawsBackground_(False)
    sc.setBorderType_(0)                                  # NSNoBorder
    sc.setHasVerticalScroller_(has_vertical)
    sc.setAutohidesScrollers_(True)
    sc.setScrollerStyle_(NSScrollerStyleOverlay)
    sc.setHorizontalScrollElasticity_(0)                  # 0 = None
    return sc


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

    cls = objc_own.own("DropTarget", NSView, {
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
