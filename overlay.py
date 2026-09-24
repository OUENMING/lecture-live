"""macOS 悬浮窗: 卡片式双语流动排版 + 流式打字机 + 文字描边。

布局(自下而上):
  ⌨ 输入行(单行可编辑, Enter 提交 / Esc 取消) —— 钉在面板最底部
  💡 术语行(钉在底部固定区, 不随句子走): **默认只列术语名**, 点一下才展开解析并钉住
  ▸ 草稿行(灰, 仅在没有流式时显示) / 草稿即时中文译文
  转录区 ×3: 每句 中文(18pt Medium, 最多两行)+ 英文(11pt Medium)
             最下是当前句(流式), 其上为历史 —— 上方旧、下方新。

背景: NSVisualEffectView 材质之上压一层半透明黑 scrim, 文字再加紧凑的黑色描边。
      材质在 drawRect 里绘制, 无法直接设背景色; 实测纯白底透过材质后亮度仍有
      0.363, 白字只有 2.54:1 —— 白底 PPT 上就看不清了。scrim 把底压暗, 描边
      负责把字从任意底色里切出来(字幕界通行做法: 轮廓比填充对比度更管用)。

按钮: 🌐 翻译开关 | ⭐ 标记重点 | ✕ 退出(走回调, 不硬杀进程)
     鼠标穿透不在面板上(开启后窗口忽略鼠标事件, 按钮会集体失效), 只从菜单栏 🎧 切换。
调用约定: main.py 保证所有方法都在主线程调用。
"""
from __future__ import annotations
import pathlib
import time

try:
    from AppKit import NSRunLoop, NSDate
    _HAS_APPKIT = True
except Exception:          # noqa: BLE001
    _HAS_APPKIT = False

from transcript_view import TranscriptView   # 模块级无 AppKit 依赖, 可安全直接导入

# ---- 几何规范 ----
# 标定依据: 18pt Medium 的中文两行实测 42.0px, 故 47px 留 5px 余量。
# 全宽 628px 下一行约 33 个汉字; 实测 151 句真实定稿 p90=34 字、最大 56 字,
# 超过两行(66 字)的有 0 句 —— 两行覆盖 100% 观测数据。
#
# ⚠️ AppKit 的 NSTextField 里 NSLineBreakByTruncatingTail **强制单行、不换行**
#    (实测: 68 字中文在 Tail 模式下量得 21.0px = 一行, 在 WordWrapping 下 42.0px
#    = 两行)。所以"要换行又要省略号"在 AppKit 做不到 —— 想多行就必须用
#    WordWrapping + maximumNumberOfLines_(N)。原来的静默吞行 bug 正是
#    maxLines=1 + WordWrapping: 第二行被裁掉且没有省略号。
#    故: 多行标签(中文行/提升的英文行/展开态 💡)一律 WordWrapping;
#    单行标签(英文小字/收回态 💡)才用 TruncatingTail 拿省略号。
VISIBLE_ROWS = 3                                  # 收回态显示 3 句(2 历史 + 1 当前)
WIDTH = 660.0
PAD = 16.0
ROW_EN_H = 18.0                                   # 每行英文小字
ROW_GAP = 5.0
ROW_ZH_H = 47.0                                   # 每行中文两行
ROW_H = ROW_EN_H + ROW_GAP + ROW_ZH_H             # 70.0
GLOSS_H = 20.0                                    # 💡 解析: 收回态 = 一行(尾部省略)
GLOSS_H_BIG = 46.0                                # 💡 解析: 展开态 = 三行(实测 42.0)+ 余量
DRAFT_H = 18.0
DRAFT_ZH_H = 20.0
INPUT_H = 26.0                                    # ⌨ 单行输入框(含下方 1px 细线)
INPUT_GAP = 6.0                                   # 输入框与它上面那行(💡)的间隔
RULE_H = 1.0                                      # 输入框下方的细线: 取代填充块
RULE_A = 0.18                                     # 细线静止不透明度(白)
RULE_A_FOCUS = 0.45                               # 细线聚焦不透明度(白) —— 提到草稿那一档

# ---- 答案接管(Phase 3) ----
# 答案**接管转录区**渲染, 而不是新开一个槽位: 一段真实答案是 2000/1500/3150 字符
# (实测 3 轮 completion_tokens 499/375/786), 按 13pt 折行就有 250-535px —— 比收起态
# 的整个转录区(210px)还高, 连展开态(440px)都能吃满。只有"本来就最大的那块区域"
# 装得下。接管也顺带让"新字幕顶掉答案"不可能发生: 渲染源被整个换掉, 而 _history
# 只由 finalize() 追加、_render 从不碰它 —— 字幕在后台继续累积, 退出接管即重现。
GLOSS_PREFIX = ("EN：", "EN:")                     # 英文辅助行前缀(ANSWER_SYSTEM 锁定全角)
# 答案折行的上限。行槽的大字位是按"18pt 两行"标定的(ROW_ZH_H=47.0): 实测 18pt
# 一行 = 21.0px, 两行 = 42.0px, 三行 = 63.0px —— 所以阈值取在两行与三行之间。
# 答案沿用同一档, 读起来就是"更长的字幕卡片", 不需要任何新排版机制。
ANSWER_TEXT_H = 45.0


def _pinned(gloss_h: float) -> float:
    """底部固定区高度(⌨ 输入行 + 💡 + 英文草稿 + 中文草稿 + 各条间隔)。
    💡 会随展开/收回改变行数, 所以它是参数而非常量 —— 面板总高才不会算错。
    输入行必须算进来: _pinned 同时喂 PINNED_H / BASE_H / _apply_mode 的总高重算,
    漏掉任何一处, 面板高度与实际内容就会错位(输入框被裁掉或盖住滚动区)。"""
    return INPUT_H + INPUT_GAP + gloss_h + 4 + DRAFT_H + DRAFT_ZH_H + 4


PINNED_H = _pinned(GLOSS_H)                     # = 98.0 收回态固定区(不含 8px 间隔)
BOTTOM_PAD = 12.0
HEADER_H = 34.0                                   # 顶部按钮条(与正文不重叠)
BASE_H = BOTTOM_PAD + PINNED_H + 8 + HEADER_H     # = 152.0(收起态除转录区外的固定高度)
HEIGHT = BASE_H + VISIBLE_ROWS * ROW_H            # = 362.0

# ---- 用户可缩放 ----
# 行高**随宽度变**: 窄窗里中文一行装不下, 需要更多行才不吞字。
# 分档来自**实测 p100**(用 _measure_text_h 对 4797 句真实中文定稿逐句量折行高度,
# 取每个宽度下的最大所需行数):
#     面板 >=620px -> 2 行 (47px)      >=440px -> 3 行 (68px)
#     面板 >=360px -> 4 行 (89px)      更窄   -> 5 行 (110px)
# 这样最窄到 280px 仍然**一句不吞**。
# ⚠️ 关键: 变的只是"等高的那个值", **所有行依然等高** —— 回收池依赖的不变量
#    ("所有行等高")没有被破坏, 所以换一组统一尺寸是安全的。
LINE_H = 21.0                                     # 18pt Medium 一行实测 21.0px
LINE_TIERS = ((620.0, 2), (440.0, 3), (360.0, 4))
MAX_LINES = 5
MIN_WIDTH = 280.0                                 # 实测: 280px 时 5 行即可零吞字
MIN_ROWS = 1                                      # 最少露 1 句(作者: "一个句子也没关系")
EDGE_BAND = 5.0                                   # 缩放抓取带宽(px)
# ---- 窗口类型开关 ----
# 两条配方都实测过, 改这一个常量即可整条切换。详见 docs/OVERLAY-RESIZE-REVIEW.md
#
#   "titled"     Titled | FullSizeContentView + 隐藏标题栏 + 隐藏红绿灯
#                -> **原生四角 + 四边 8 向缩放、原生缩放光标、原生 live resize**
#                (AppKit 对无边框窗口只注册四条边的缩放区, 四角完全没有)
#                代价: AppKit 会给 Titled 窗口自动加一个标题栏 visual effect view,
#                      在浅色系统下把材质染灰 —— 靠 setAppearance_(DarkAqua) 抵消
#                      (实测面板中心亮度 0.314 -> 0.113)。
#
#   "borderless" 纯无边框
#                -> 材质外观最干净(不依赖上面那个经验补偿)
#                代价: **四角不能原生缩放**, 要自己实现; 而且自己实现时
#                      `movableByWindowBackground` 会吞掉 mouseDown, 两者互斥
#                      (实测: movable=True 时 pump 只看得到 MouseEntered/Moved)
#                移动仍然可用: movable=True + 视图 mouseDownCanMoveWindow->True
#
# ⚠️ 何时该切回 "borderless": 如果 macOS 升级后 Titled 那套坏掉。
#    已知先例: Warp #12393 / #12389 (macOS 27.0 beta) —— 红绿灯与四角/边缘
#    全部对鼠标无反应(辅助功能 API 仍可缩放)。**但那条的根因是 Warp 自己
#    额外加了一层 mouseDragged/Up 转发**, 与 Titled 配方本身无关; 我们没那层转发。
#    Ghostty #7568 (macOS 26) 则是 `macos-titlebar-style = transparent` 失效。
WINDOW_STYLE = "titled"

# 尺寸记忆。与 .course / .deepseek_key 同级同风格(各自模块管自己的小文件)。
# 读不到/写不进一律 fail-soft —— 缩放是便利功能, 不能因为它让课上崩。
WINDOW_STATE_FILE = pathlib.Path(__file__).with_name(".window")


def _lines_for_width(w: float) -> int:
    for thr, n in LINE_TIERS:
        if w >= thr:
            return n
    return MAX_LINES


def _zh_h_for(lines: int) -> float:
    return lines * LINE_H + 5.0                   # 与既有 2*21+5 = 47 同口径


def _row_h_for(lines: int) -> float:
    return ROW_EN_H + ROW_GAP + _zh_h_for(lines)

WEIGHT = 0.23                                     # NSFontWeightMedium
FLUSH_DT = 0.016                                  # 渲染合并闸门 = 一帧(约 60Hz)

# ---- 三档模式(顶栏按钮循环切换) ----
# 为什么要三档而不是两档: 原来只有一个"译 开/关", 但**关掉后英文仍然过 DeepSeek 做
# 上下文矫正**(见 main.py `_emit` 的 `translating["on"]` 分支) —— 对"我根本不需要
# 翻译、也不想联 API"的人, 没有可选项。第三档把"完全不调 LLM"变成显式选择:
# 全程离线、零 API 成本、零首字延迟、也最省电。
#   both  双语     —— DeepSeek 矫正英文错听 + 出中文
#   en    只英·校  —— 只矫正英文, 不出中文(旧"译 关"的行为)
#   raw   纯转录   —— ASR 直出, 一个 LLM 请求都不发
TRANS_MODE_TITLE = {"both": "双语", "en": "只英·校", "raw": "纯转录"}
TRANS_MODE_DESC = {
    "both": "DeepSeek 矫正英文错听 + 出中文",
    "en": "只矫正英文, 不出中文",
    "raw": "完全不调 LLM, ASR 直出",
}
# 延迟导入: 颜色是锦上添花, 拿不到也不该让模块导入失败
def _mode_color(name: str):
    def get():
        from AppKit import NSColor
        return {"both": NSColor.systemGreenColor(),
                "en": NSColor.systemYellowColor(),
                "raw": NSColor.secondaryLabelColor()}[name]
    return get

TRANS_MODE_COLOR = {k: _mode_color(k) for k in TRANS_MODE_TITLE}

# pump() 每轮的让步窗口。**这是滚动跟手度的天花板**:
# 固定 8ms 让步会让 pump 只有 ~113Hz, 而此时触控板手势正以 120Hz 投递事件 ——
# 等于欠采样, 手感就是"帧率低"。改成两档: 手势期间热轮询, 空闲才让 8ms 省 CPU。
#
# ⚠️ 关键不是"本轮有没有事件", 而是"**最近**有没有事件"。若只在"本轮派发过事件"
#    时才不让步, 那么两批滚轮事件之间那一轮(队列空)会睡满 8ms, 下一批事件就在
#    队列里干等最多 8ms 才被排空 -> 视口更新被量化到 8ms 桶, 与 120Hz 手势拍频
#    = 肉眼所见的"卡顿"。所以要按"距上次事件多久"判定, 见 PUMP_ACTIVE_S。
#
# ⚠️ 但热档**不能真的什么都不做**: 不跑 run loop 时 CoreAnimation 不提交,
#    画面会**整个冻住**(实测: 让步=0 时滚到别处截图全空白)。
#    解 = `runUntilDate_(NSDate.distantPast())`: 立刻返回但**仍跑完一轮** run loop。
#    实测 0.003ms, 比睡 0.0001s(0.128ms)快 40×, 且截图内容正确 -> 零尾延迟。
#    (旧值 0.0002 的让步尾部实测能到 8ms 量级, 会把个别帧顶过 8.3ms 而掉帧。)
PUMP_IDLE_S = 0.008              # 空闲让步(省 CPU, 此时无事可做)
PUMP_ACTIVE_S = 0.15             # 距上次事件在此窗口内 = 手势进行中 -> 热轮询

# 白底可读性: 实测纯白底透过 HUDWindow 材质后亮度仍有 0.363(因材质最多把白
# 压掉约 64%), 白字对它只有 2.54:1 —— 这才是"白底看不清"的真因。
# 压一层 alpha 黑 scrim 后有效亮度 L=1-a(近似), 实测:
#   a=0.30 → 3.02:1   a=0.45 → 3.33:1   a=0.85 → 4.60:1
# 想纯靠填充色达到 AA(4.5:1) 需要 a≈0.85, 那等于放弃毛玻璃。
# 所以采用字幕界通行做法: 中等 scrim 压底 + 紧凑描边勾轮廓 —— 轮廓不透明度
# 决定可读性, 底子只需压暗到让轮廓有依托。实测在纯白 PPT 上清晰可读。
SCRIM_ALPHA = 0.45


def _make_shadow():
    from AppKit import NSShadow, NSColor
    sh = NSShadow.alloc().init()
    # 紧凑高不透明描边: 在任意底色(纯白 PPT / 彩图)上把字从背景里切出来。
    # 旧值 blur 3.0 / α0.85 太糊, 浅底上反而脏。
    sh.setShadowColor_(NSColor.blackColor().colorWithAlphaComponent_(0.80))
    sh.setShadowBlurRadius_(1.5)
    sh.setShadowOffset_((0.0, -1.0))
    return sh


def _measure_text_h(text: str, width: float, font) -> float:
    """文本按给定宽度折行后的**实测**高度(px)。主线程调用(AppKit)。

    用 AppKit 自己的排版引擎量, 不猜字符宽度: 折行的判据必须与标签渲染时的
    换行规则**同源**, 否则会出现"我们以为放得下、标签却静默裁掉第三行"
    (NSTextField 在 maximumNumberOfLines 超限时不留省略号也不报错)。"""
    from AppKit import (NSAttributedString, NSMakeSize, NSFontAttributeName,
                        NSStringDrawingUsesLineFragmentOrigin)
    a = NSAttributedString.alloc().initWithString_attributes_(
        text, {NSFontAttributeName: font})
    return a.boundingRectWithSize_options_(
        NSMakeSize(width, 1e7), NSStringDrawingUsesLineFragmentOrigin).size.height


_ButtonTargetCls = None


def _make_button_target(on_click):
    """返回一个 ObjC 按钮目标。类只定义一次(重复定义会报 override 错)。"""
    global _ButtonTargetCls
    import objc
    from AppKit import NSObject
    if _ButtonTargetCls is None:
        class _ButtonTarget(NSObject):
            def clicked_(self, sender):        # noqa: N802
                cb = getattr(self, "_cb", None)
                if cb:
                    cb()

        _ButtonTargetCls = _ButtonTarget
    t = _ButtonTargetCls.alloc().init()
    t._cb = on_click
    return t


_PanelCls = None


def _xy(p) -> tuple:
    """把 PyObjC 返回的点统一成 (x, y) 浮点元组。

    ⚠️ 同一类坑在本文件踩过多次: `CGEventGetLocation` / `NSEvent.mouseLocation`
    / `NSEvent.locationInWindow` 在 PyObjC 里返回的**不一定是结构体**(有的是元组),
    直接写 `.x` 会 AttributeError —— 而 PyObjC 会把这类异常**静默吞掉**,
    症状是"代码看着在、其实一次都没跑"。所有取点的地方一律过这个函数。
    """
    try:
        return float(p.x), float(p.y)
    except AttributeError:
        return float(p[0]), float(p[1])


def _make_panel(rect, style, backing, defer):
    """无边框 NonactivatingPanel。

    ⚠️ 必须 override **ObjC 名** `canBecomeKeyWindow`: 窗口无标题栏时基类返 False,
    AppKit 会据此放弃把它变成 key window —— 那样输入框永远拿不到键盘。
    Swift 名 `canBecomeKey` 无效(PyObjC 会把它注册进 runtime, 但 AppKit 从不调用)。
    `canBecomeMainWindow` 不用动: key 与 main 无关。
    类只定义一次(重复定义会报 override 错), 同 _ButtonTargetCls。

    `sendEvent_` 保留一处: 点面板时手动激活 app(macOS 只让活跃 app 改光标,
    而 NonactivatingPanel 按定义不会自激活)。移动与缩放的实际处理在拖拽层,
    见 `Overlay._on_drag_layer_mousedown` / `_track_loop`。
    """
    global _PanelCls
    from AppKit import NSPanel
    if _PanelCls is None:
        import objc

        class _Panel(NSPanel):
            def canBecomeKeyWindow(self):           # noqa: N802
                return True

            def sendEvent_(self, event):            # noqa: N802
                """窗口收到的**每一个**事件都经过这里 —— 这是唯一绕不开的位置。

                ⚠️ 为什么不去 override 某个视图的 mouseDown_: 实测(2026-09-24)
                即使 `contentView.hitTest_()` 明确返回了我们的拖拽层,
                它的 `mouseDown_` **一次都没被调用**(没有报错, 静默)。
                窗口级的 sendEvent_ 没有这个问题。
                """
                if event.type() == 1:               # NSEventTypeLeftMouseDown
                    try:
                        from AppKit import NSApplication
                        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
                    except Exception:               # noqa: BLE001
                        pass
                objc.super(_Panel, self).sendEvent_(event)

        _PanelCls = _Panel
    return _PanelCls.alloc().initWithContentRect_styleMask_backing_defer_(
        rect, style, backing, defer)


_InputDelegateCls = None


def _make_input_delegate(on_submit, on_cancel):
    """输入框委托: 回车/Esc 走 control:textView:doCommandBySelector:。

    ⚠️ 不用 target/action: 后者在 Tab 与失焦时也会触发(换个焦点 = 误提交)。
    收到的 selectors 是运行时字符串: `insertNewline:`(Return) / `cancelOperation:`(Esc)。
    PyObjC 把 `control:textView:doCommandBySelector:` 映射成下划线形式的方法名。

    ⚠️ **刻意不实现 `controlTextDidBeginEditing_` / `…EndEditing_`**: 实测 Begin 在
    真实启动路径下从不触发(见 show()), 拿它做聚焦反馈会得到一个永远不亮的界面。
    细线与光标一律由 `_sync_focus_look()` 在 pump 里按状态同步。
    """
    global _InputDelegateCls
    from AppKit import NSObject
    if _InputDelegateCls is None:
        class _InputDelegate(NSObject):
            def control_textView_doCommandBySelector_(self, control, textview, selector):  # noqa: N802
                if selector == "insertNewline:":
                    cb = getattr(self, "_on_submit", None)
                elif selector == "cancelOperation:":
                    cb = getattr(self, "_on_cancel", None)
                else:
                    return False            # 方向键/Tab/⌘C 等交给默认实现
                if cb:
                    cb()
                return True

        _InputDelegateCls = _InputDelegate
    d = _InputDelegateCls.alloc().init()
    d._on_submit = on_submit
    d._on_cancel = on_cancel
    return d


_ClickViewCls = None


def _make_click_view(on_click):
    """覆盖用的透明点击区: 落在 frame 内的鼠标点击回调 on_click()。

    ⚠️ 不用"空标题的 NSButton": NSButtonCell 在没有可见内容的区域可能返回
    NSCellHitNone, hitTest 直接把它漏掉 —— 我们要的恰恰是一块**没有内容**的
    矩形。NSView 只看 frame, 稳。

    ⚠️ acceptsFirstMouse 必须 True: 面板是 NonactivatingPanel, 从不激活 app,
    默认实现会把"非 key 窗口上的第一次点击"吞掉用于激活 —— 那正好是用户
    唯一的那次点击。
    """
    global _ClickViewCls
    from AppKit import NSView
    if _ClickViewCls is None:
        class _ClickView(NSView):
            def acceptsFirstMouse_(self, event):        # noqa: N802
                return True

            def mouseDown_(self, event):                # noqa: N802
                cb = getattr(self, "_cb", None)
                if cb:
                    cb()

        _ClickViewCls = _ClickView
    v = _ClickViewCls.alloc().initWithFrame_(((0, 0), (0, 0)))
    v._cb = on_click
    return v


_DragLayerCls = None


def _make_drag_layer(on_mousedown=None):
    """整面板的背景拖拽层 —— 让"空白处任意位置都能拖窗口"。

    为什么必须显式加这一层(2026-09-24 实测的拖动意图地图):
        转录区   -> 移动 ✅(那里 _TranscriptDoc 自己调了 performWindowDragWithEvent_)
        顶栏空白 -> **无反应** ❌
        输入行   -> **无反应** ❌
    于是用户按正常习惯去抓顶栏想移动窗口时什么都没发生, 再往外一点就落进 5px 缩放带
    —— 体验就成了"想拖动却变成缩放"。

    放在 z 序**最底**(紧跟 scrim), 所以控件、转录区、缩放抓取带都在它上面、各自照常
    收事件; 只有真正的空白处才落到这一层。
    """
    global _DragLayerCls
    from AppKit import NSView
    if _DragLayerCls is None:
        class _DragLayer(NSView):
            def mouseDownCanMoveWindow(self):   # noqa: N802
                # ⚠️ 必须 **True**(2026-09-24 实测定位): 这个返回值是 AppKit
                # "按下背景即拖动窗口"的开关。设成 False 会让**背景完全拖不动**
                # (而且 mouseDown_ 也收不到 —— 两边都落空)。
                # 症状就是作者报的"只有按住转录区才拖得动": 转录区的文档视图恰好是
                # True, 而这一层被我写成了 False 却盖住了顶栏等区域。
                return True

            def mouseDown_(self, event):        # noqa: N802
                win = self.window()
                if win is None:
                    return
                # 点面板任意空白处 -> 手动激活 app。macOS 只让**活跃 app** 改光标,
                # 而面板带 NonactivatingPanel(那是"非激活时仍被合成"的前提)不会自激活。
                try:
                    from AppKit import NSApplication
                    NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
                except Exception:               # noqa: BLE001
                    pass
                cb = getattr(self, "_cb", None)
                if cb is not None:
                    cb(event)                     # 最外一圈 -> 自己的嵌套循环缩放
                # 其余情况 AppKit 会凭 mouseDownCanMoveWindow=True 自己拖动窗口

        _DragLayerCls = _DragLayer
    v = _DragLayerCls.alloc().initWithFrame_(((0.0, 0.0), (100.0, 100.0)))
    v._cb = on_mousedown
    return v


_WindowDelegateCls = None


def _make_window_delegate(on_resize):
    """窗口委托 —— 只为一件事: **live resize 期间也要重排内容**。

    ⚠️ 为什么必须用委托, 不能继续在 `pump()` 里轮询(2026-09-24 实测):
    原生拖边缘缩放时 AppKit 会进入它自己的事件跟踪循环, **我们的整个主循环被
    卡住 1239.8ms**(实测: 空闲期 pump 最大间隔 9.6ms, 拖拽期 1239.8ms)。
    那 1.2 秒里 `_sync_panel_size()` 一次都跑不到 -> 窗口框在动、内容冻着,
    松手才跳一下。手感就是作者说的"卡顿不够丝滑"。
    `windowDidResize:` 是在那个跟踪循环**内部**回调的, 所以拖拽期间它能持续重排
    —— 这才是 AppKit 给 live resize 的正规钩子。

    这不违反本仓库"轮询而非观察者"的既定做法: 那条针对的是**滚动视图的 bounds
    通知**(弱引用 + 自我 setFrame 期间重入); 窗口尺寸变化没有那个重入面,
    而且轮询在拖拽期间**根本跑不到**, 除了委托没有别的办法。
    """
    global _WindowDelegateCls
    from AppKit import NSObject
    if _WindowDelegateCls is None:
        class _WindowDelegate(NSObject):
            def windowDidResize_(self, note):      # noqa: N802
                cb = getattr(self, "_cb", None)
                if cb:
                    cb()

            def windowDidEndLiveResize_(self, note):   # noqa: N802
                cb = getattr(self, "_cb", None)
                if cb:
                    cb()

        _WindowDelegateCls = _WindowDelegate
    d = _WindowDelegateCls.alloc().init()
    d._cb = on_resize
    return d


class Overlay:
    def __init__(self, on_quit=None, on_flag=None, on_translate=None, on_submit=None,
                 on_ask=None, on_new_topic=None):
        from AppKit import (NSWindow, NSPanel, NSMakeRect, NSColor, NSTextField,
                            NSVisualEffectView, NSVisualEffectMaterialHUDWindow,
                            NSVisualEffectStateActive, NSWindowStyleMaskBorderless,
                            NSWindowStyleMaskNonactivatingPanel, NSBackingStoreBuffered,
                            NSWindowStyleMaskResizable, NSWindowStyleMaskTitled,
                            NSWindowStyleMaskClosable, NSWindowStyleMaskFullSizeContentView,
                            NSWindowTitleHidden,
                            NSAppearance, NSAppearanceNameDarkAqua,
                            NSTextAlignmentLeft, NSFont, NSLineBreakByWordWrapping,
                            NSLineBreakByTruncatingTail, NSView,
                            NSFloatingWindowLevel, NSWindowCollectionBehaviorCanJoinAllSpaces,
                            NSFocusRingTypeNone)

        self._width = WIDTH
        self._height = HEIGHT
        # ---- 用户缩放状态 ----
        # 收回态/展开态的转录区高度各自记住用户拖出来的值(None = 用默认)。
        # 为什么要分开记: 展开/收回会把面板高度整个重算, 如果只记一个值,
        # 用户在收回态调好的高度一按「展开」就没了。
        self._collapsed_scroll_h = VISIBLE_ROWS * ROW_H
        # 当前生效的行尺寸(随宽度变, 见 _apply_row_metrics)。默认 = 2 行那一档。
        self._row_h = ROW_H
        self._zh_h = ROW_ZH_H
        self._expanded_h_user: float | None = None
        # pump 里轮询面板实际尺寸的缓存。**刻意不用 NSWindowDelegate 的
        # windowDidResize:** —— 本仓库既定做法是轮询而非观察者(见
        # transcript_view.tick 的说明: 观察者是弱引用 GC 陷阱 + 会在我们自己
        # 的 setFrame 期间重入)。缩放是低频事件, 每帧比两个浮点几乎免费。
        self._last_panel_size: tuple[float, float] | None = None
        # 只有用户真的拖过才写 .window。见 _sync_panel_size 里的说明。
        self._user_resized = False
        # 我们自己调 setFrame（展开 / 答案接管）时置真 —— 让 _sync_panel_size
        # 别把我们自己的改动当成用户拖了窗口。见该函数的说明。
        self._programmatic_resize = False
        self._on_quit = on_quit or (lambda: None)
        self._on_flag = on_flag or (lambda: None)
        self._on_translate = on_translate or (lambda on: None)
        self._on_submit = on_submit or (lambda q: None)
        self._on_ask = on_ask or (lambda: None)
        self._on_new_topic = on_new_topic or (lambda: None)
        # 全量历史(不再 deque(maxlen=3) —— 展开态要能翻到更早的句子)。
        # 一节课 544 句约 <200KB, 无需上限。
        self._history: list = []
        self._dirty = False
        self._urgent = False
        self._last_flush = 0.0
        self._last_ev_t = 0.0        # 上次派发事件的时间(决定让步时长, 见 pump)
        self._closed = False         # close() 幂等标志(✕ / Ctrl+C / 正常结束)
        self._cur_zh = ""
        self._cur_en = ""
        self._streaming = False
        self._draft = ""
        self._shadow = _make_shadow()
        self._targets = []        # ⚠️ 必须常驻: NSControl.setTarget_ 是弱引用,
                                  # 不保存就会被 Python GC 回收 -> target() 变 None -> 点击失效
        self._label_cache = {}    # 标签文本缓存, 见 _set_cached

        # ---- 答案接管状态(Phase 3) ----
        # ⚠️ 命名: `_pinned` 已被"底部固定区高度"占用, `_pinned_term` 是术语,
        #    答案一律用 `_answer*` 前缀, 三者互不干扰。
        # 折行状态机(全部随增量推进, 不做全量重排):
        #   _answer_text   累积的原文(answer_done 时用来与完整回答对账)
        #   _answer_rows   已定稿的行 [(大字, 小字)] —— 答案行的唯一真源
        #   _answer_pend   当前**未闭合的源行**原文(模型换行才算闭合)
        #   _answer_fold   上面这段里已被折进折行状态机的字符数
        #   _answer_pr     当前源行已闭合的行(下一个词放不下才闭合)
        #   _answer_pw     当前源行里还没落行的词
        self._answer_on = False
        self._answer_text = ""
        self._answer_rows: list = []
        self._answer_pend = ""
        self._answer_fold = 0
        self._answer_pr: list = []
        self._answer_pw: list = []
        self._mode_before_answer = True     # 接管前是"收起"吗(dismiss 时还原)
        self._tv_mode = "cap"               # 转录区当前喂的是字幕还是答案
        # 上一轮是否已经 answer_done。追问时用它判断"这是一轮新的回答" ——
        # 不重置的话新一轮的流式增量会**接在上一轮答案后面**, 屏上是两轮粘在一起
        # (实测: 4 行的答案涨到 8 行, 直到 answer_done 对账才恢复)。
        self._answer_finished = False

        # 尺寸记忆要在建面板**之前**读 —— 初始 frame 就用它, 否则会先闪一下默认尺寸。
        self._load_window_state()

        # ---- 窗口类型: 见模块顶部的 WINDOW_STYLE 开关 ----
        # ⚠️ `NonactivatingPanel` **两条路都必须保留** —— 实测去掉它, 窗口在 app
        # 非激活时 `occlusionState` 丢掉 Visible 位(8194->8192), **界面完全不可见**。
        # ⚠️ `Resizable` 必须**构造时**给, 不能运行时改: NSWindow.h:341 原文说改
        # styleMask 会重建视图层级(无边框与有标题栏的顶层视图是不同子类)。
        if WINDOW_STYLE == "titled":
            style = (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
                     | NSWindowStyleMaskResizable | NSWindowStyleMaskFullSizeContentView
                     | NSWindowStyleMaskNonactivatingPanel)
        else:
            style = (NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel
                     | NSWindowStyleMaskResizable)
        self._panel = _make_panel(
            NSMakeRect(0, 0, self._width, self._height), style,
            NSBackingStoreBuffered, False)
        # 宽度下限见 MIN_WIDTH 的实测依据。
        # ⚠️ **不设 `setContentResizeIncrements_`** —— 试过按 ROW_H 吸附高度, 手感是
        # "拖 30px 没反应、突然跳 70px", 作者的原话是"完全不跟手"。缩放要像拉窗口一样
        # 连续跟手, 所以让高度自由; 视口底部露半行字是可接受的(滚动视图本来就该这样)。
        self._panel.setContentMinSize_((MIN_WIDTH, self._min_height()))
        # live resize 期间也要重排内容(否则拖拽那 1.2 秒里内容冻着, 松手才跳)。
        # ⚠️ setDelegate_ 是**弱引用** -> 必须进 _targets 保命; 被 GC 掉的后果是
        #    拖拽期间内容又冻回去, 而且**完全不报错**(与 _targets 里其它目标同理)。
        self._win_delegate = _make_window_delegate(self._sync_panel_size)
        self._targets.append(self._win_delegate)
        self._panel.setDelegate_(self._win_delegate)
        # ⚠️ 轮询缓存必须在这里就用**真实 frame** 初始化。留成 None 的话, 第一次
        # _sync_panel_size 必然判成"尺寸变了" -> 把构造本身误记成"用户拖过" ->
        # 于是光是构造+close() 就会往仓库写 .window(跑一次测试落一个文件)。
        _f0 = self._panel.frame()
        self._last_panel_size = (_f0.size.width, _f0.size.height)
        # 藏标题文字 + 标题栏透明 —— 配合 Titled|FullSizeContentView 就是"看着无边框、
        # 行为是正常窗口"。这两条与 fullSizeContentView 互为前提(Apple 文档原文)。
        if WINDOW_STYLE == "titled":
            self._panel.setTitleVisibility_(NSWindowTitleHidden)
            self._panel.setTitlebarAppearsTransparent_(True)
        # ⚠️ 必须**显式指定深色外观**。像素级实测(2026-09-24): 系统处于浅色模式时,
        # Titled 窗口的 NSVisualEffectView 会跟随窗口 appearance, `.hudWindow` 被渲染
        # 成灰色 —— 面板中心平均亮度 **0.314**; 强制 DarkAqua 后降到 **0.113**
        # (暗 2.8 倍), 通透感恢复。
        # 这就是"换成 Titled 之后变灰"的**真因**: 与 material / blendingMode / opaque
        # 都无关(那三项实测本来就是对的: HUDWindow=13, BehindWindow=0, state=Active)。
        # Borderless 时不明显, 因为那时窗口没有主题框架、外观继承路径不同。
        self._panel.setAppearance_(
            NSAppearance.appearanceNamed_(NSAppearanceNameDarkAqua))
        # 红绿灯只存在于 Titled 窗口; borderless 下 standardWindowButton_ 全返回
        # None, 这个调用是安全的空操作, 所以不额外加条件。
        self._hide_traffic_lights()
        self._panel.setLevel_(NSFloatingWindowLevel)
        self._panel.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces)
        self._panel.setOpaque_(False)
        self._panel.setBackgroundColor_(NSColor.clearColor())
        # ⚠️ 必须 **True**(2026-09-24 实测): 它是"按下背景即拖动窗口"的总开关,
        # 配合拖拽层的 `mouseDownCanMoveWindow -> True` 才生效。
        # 试过设 False 想自己接管拖拽, 结果是**背景完全拖不动**(见拖拽层的说明)。
        self._panel.setMovableByWindowBackground_(True)

        content = self._panel.contentView()
        ve = NSVisualEffectView.alloc().initWithFrame_(content.bounds())
        ve.setMaterial_(NSVisualEffectMaterialHUDWindow)
        ve.setState_(NSVisualEffectStateActive)
        ve.setWantsLayer_(True)
        ve.layer().setCornerRadius_(16.0)
        ve.layer().setMasksToBounds_(True)   # scrim 是矩形, 靠这里裁成圆角
        content.addSubview_(ve)
        self._ve = ve

        # 白底可读性: 材质之上、文字之下压一层半透明黑。
        # 必须是 ve 的子视图、且在下面所有文字之前加入 —— 材质画在 drawRect,
        # 设不了背景色, 只能靠这层 scrim 把白底压暗(3.4:1 -> 4.7:1)。
        scrim = NSView.alloc().initWithFrame_(ve.bounds())
        scrim.setWantsLayer_(True)
        scrim.layer().setBackgroundColor_(
            NSColor.blackColor().colorWithAlphaComponent_(SCRIM_ALPHA).CGColor())
        ve.addSubview_(scrim)
        self._scrim = scrim

        # 背景拖拽层: 紧跟 scrim 加入 = z 序最底, 所以后面所有控件/转录区/缩放带
        # 都在它上面, 各自照常收事件; 只有空白处落到这里 -> 拖窗口。
        self._drag_layer = _make_drag_layer(self._on_drag_layer_mousedown)
        ve.addSubview_(self._drag_layer)

        self._NSFont, self._NSTF = NSFont, NSTextField
        # 答案折行的实测字体: 必须与转录区大字位用的是**同一个** 18pt Medium,
        # 否则量出来的行高与标签实际排版对不上, 折行判据就是假的。
        self._answer_font = NSFont.systemFontOfSize_weight_(18.0, WEIGHT)
        # 存成实例属性而不靠局部作用域: 后面 _set_rule_focus / _on_input_focus
        # 是**方法**, 那里没有 __init__ 的局部名 —— 早先直接写 NSColor 会 NameError,
        # 且被 except 吞掉, 症状是"细线永远不上色"这种完全静默的失败。
        self._NSColor = NSColor
        self._align, self._wrap = NSTextAlignmentLeft, NSLineBreakByWordWrapping
        self._trunc = NSLineBreakByTruncatingTail

        # ---- 转录区: 聊天式滚动 + 视图回收 ----
        # 收回态与展开态共用同一个 NSScrollView(收回=钉在底部只露 3 行、滚轮被吞)。
        # 不做两套渲染器: 收回态占 99% 使用时间, 让它跑在旧代码上等于把 bug
        # 藏在你不看的地方。
        self._collapsed = True
        self._scroll_y = BOTTOM_PAD + PINNED_H
        self._scroll_h = self._collapsed_scroll_h
        self._pinned = PINNED_H
        self._expanded_h = self._expanded_scroll_h()
        f_zh = (NSFont.systemFontOfSize_weight_(18.0, WEIGHT),
                NSColor.whiteColor(), NSLineBreakByWordWrapping)
        f_en = (NSFont.systemFontOfSize_weight_(11.0, WEIGHT),
                NSColor.whiteColor().colorWithAlphaComponent_(0.80), self._trunc)
        f_big = (NSFont.systemFontOfSize_weight_(18.0, WEIGHT),
                 NSColor.whiteColor(), NSLineBreakByWordWrapping)
        self._tv = TranscriptView(
            ve, self._label, f_zh, f_en, f_big,
            self._width, PAD, ROW_EN_H, ROW_GAP, ROW_ZH_H, ROW_H,
            max_scroll_h=self._expanded_h,
            on_follow_change=self._on_follow_change)
        self._tv.set_collapsed(True)      # 初始收回态: 滚轮必须从一开始就被吞掉
        # 载入的宽度可能是窄窗(上次拖过) -> 立刻按它定行尺寸, 否则会先用 2 行的
        # 默认尺寸画一帧, 再跳成 4/5 行。默认宽度下这个调用是空操作。
        self._apply_row_metrics(self._width)

        self._draft_lbl = self._label(11.0, NSColor.whiteColor().colorWithAlphaComponent_(0.50), 1)
        ve.addSubview_(self._draft_lbl)
        # 草稿的即时中文译文(尽早可见; 定稿后被正式卡片取代)
        self._draft_zh = self._label(12.5, NSColor.whiteColor().colorWithAlphaComponent_(0.78), 1)
        self._draft_zh_val = ""
        ve.addSubview_(self._draft_zh)

        # ---- 单行输入框(Phase 1: 只把文字交给回调, 问答引擎是 Phase 2) ----
        # 钉在面板最底部(见 _layout 的自下而上游标)。不用 _label(): 那个强制
        # setEditable_(False)。⚠️ setDelegate_ 与 setTarget_ 一样是**弱引用** ——
        # 委托对象必须进 _targets 保命, 否则被 GC 后回车直接没反应。
        # 裸 NSTextField 即可: 实测非 key 面板上的首次点击能正常拿到 field editor
        # (NSControl 对控件默认 acceptsFirstMouse=YES), 不需要子类。
        #
        # 视觉: 与面板里其它文字**同一套规则**, 不填背景色。
        # 既定决策是"可读性由紧凑描边承担而非填充色"(见 SCRIM_ALPHA 注释), 其它
        # 内容都是直接浮在玻璃上、靠描边立住; 一个黑方块会立刻读成贴上去的外来
        # 控件。改成字段下方一条 1px 细线: 平时近乎隐形, 聚焦时才亮 —— 它的身份
        # 是"字幕流里一条可以打字的线", 不是表单框。
        self._input = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 0, 0))
        self._input.setEditable_(True)
        self._input.setSelectable_(True)
        self._input.setBezeled_(False)
        self._input.setBordered_(False)
        self._input.setDrawsBackground_(False)
        self._input.setFocusRingType_(NSFocusRingTypeNone)  # 去掉 AppKit 默认蓝环
        self._input.setFont_(NSFont.systemFontOfSize_weight_(14.0, WEIGHT))
        self._input.setTextColor_(NSColor.whiteColor())
        self._input.setMaximumNumberOfLines_(1)
        self._input.setLineBreakMode_(self._trunc)      # Tail 模式强制单行(见顶部注释)
        self._input.setStringValue_("")
        self._input.setShadow_(self._shadow)            # 与所有其它文字同一描边
        self._input.setToolTip_("Enter 提交 · Esc 取消")
        try:
            from AppKit import NSAttributedString, NSForegroundColorAttributeName
            self._input.setPlaceholderAttributedString_(
                NSAttributedString.alloc().initWithString_attributes_(
                    "提问或追问…",
                    {NSForegroundColorAttributeName:
                     NSColor.whiteColor().colorWithAlphaComponent_(0.50)}))
        except Exception:                               # noqa: BLE001
            self._input.setPlaceholderString_("提问或追问…")
        self._input_delegate = _make_input_delegate(
            self._submit_input, self._cancel_input)
        self._targets.append(self._input_delegate)      # 保持强引用, 防 GC
        self._input.setDelegate_(self._input_delegate)
        ve.addSubview_(self._input)
        # 细线: 1px NSView, 颜色随焦点切换(见 _on_input_focus / _on_input_blur)。
        # 用与 scrim 同一个 pattern(setWantsLayer_ + layer().setBackgroundColor_)。
        self._input_rule = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 0, 0))
        self._input_rule.setWantsLayer_(True)
        self._focus_look_on = None          # 未知 -> 第一次 _sync_focus_look 必然生效
        self._set_rule_focus(False)
        ve.addSubview_(self._input_rule)
        # 术语行(命中术语表时显示, 暖黄色); 钉在面板底部, 不随句子滚动。
        # 默认**只列术语名**, 完整解析要点击才展开(见 _on_gloss_click)。
        self._gloss_lbl = self._label(
            11.5, NSColor.colorWithCalibratedRed_green_blue_alpha_(1.0, 0.83, 0.45, 0.95), 1,
            self._trunc)
        ve.addSubview_(self._gloss_lbl)
        self._terms: list = []        # 当前句命中的 (术语, 解析), 不随 finalize 清空
        self._pinned_term = None      # 被点开并钉住的术语 -> 解析永久留在屏上
        # 透明点击区盖在术语行上(最后加入 = 在最上层)。
        self._gloss_hit = _make_click_view(self._on_gloss_click)
        ve.addSubview_(self._gloss_hit)

        # 面板按钮只保留"永远点得到"的两个。
        # 鼠标穿透不能放在面板上: 开启后窗口忽略所有鼠标事件, 按钮会集体失效(单向死锁),
        # 所以穿透只从菜单栏 🎧 切换。
        self._through = False
        self._trans_mode = "both"        # both | en | raw, 见 _cycle_translate
        self._engine_warn = False        # 云端翻译降级中(菜单栏图标提示用)
        self._btn_trans = self._button(
            TRANS_MODE_TITLE["both"], self._cycle_translate,
            "点一下循环三种模式:\n"
            "  双语     —— DeepSeek 矫正英文错听 + 出中文\n"
            "  只英·校  —— 只矫正英文, 不出中文\n"
            "  纯转录   —— 完全不调 LLM, ASR 直出(零 API、零延迟、最省电)")
        self._btn_flag = self._button(
            "⭐", self._flag, "标记当前句为重点(写入 Obsidian 时加 ⭐ Exam Focus)")
        self._btn_close = self._button("✕", self._quit, "退出")
        # 答案接管期间显示「新话题」的位置:
        # 「讲一下」= 用固定问题开一轮讲解; 「新话题」= 清掉问答线程并回到字幕。
        self._btn_ask = self._button(
            "讲一下", self._ask,
            "把刚讲的这一段讲清楚(中文讲解, 关键处留一行 EN：英文原文)")
        self._btn_topic = self._button(
            "新话题", self._new_topic, "结束当前问答线程, 回到字幕")
        # ⚠️ 「展开/收回」按钮已删(2026-09-24)。它原本承担的是"解锁滚动"——
        # 而滚动权限已与收起/展开解耦(见 transcript_view.set_collapsed), 现在
        # **拉窗口 = 看几句, 滚动 = 往回翻**, 两者正交, 不需要这个按钮。
        # `_apply_mode` 保留: **答案接管**仍在用它自动展开/还原(见 answer 接管那段)。
        # "回到最新": 只在用户翻到上面去了以后出现。它同时是滚轮若投递失败时的
        # 保底导航(按钮已被实测证明可点)。
        self._btn_latest = self._button(
            "↓ 最新", self._tv.scroll_to_bottom, "回到最新一句")
        self._btn_latest.setHidden_(True)
        # 顶栏按钮按**右对齐**摆放(列表为左->右顺序), 实际 x/宽度在 _layout() 现算:
        # 宽度 = sizeToFit + 内边距(图标保底 28px 点击区)。旧代码硬编码 x, "展开"
        # 占 [W-170,W-108] 而"译 开"占 [W-130,W-74], **交叉 22px**, 渲染出来糊成
        # 一团("展开译 开")。自适应宽度后中英换字都不会重叠。
        # ⚠️ _btn_latest 是**隐藏但仍占位**的, 所以它必须留在列表最前(视觉最左),
        #    插到中间会在按钮组内部凭空留一段间隔。
        self._bar = [self._btn_latest, self._btn_ask,
                     self._btn_topic, self._btn_trans, self._btn_flag,
                     self._btn_close]
        self._sync_trans_button()
        self._install_status_item()               # 菜单栏: 鼠标穿透开关 + 退出

        self._layout()
        self._move_to_corner()

    # ---- 构造辅助 ----
    def _label(self, size, color, maxlines, break_mode=None):
        lbl = self._NSTF.alloc().initWithFrame_(((0, 0), (0, 0)))
        lbl.setEditable_(False); lbl.setSelectable_(False)
        lbl.setBezeled_(False); lbl.setDrawsBackground_(False)
        # 系统字体(AppleSystemUIFont)已自动把拉丁/数字解析到 SF Pro、汉字到苹方,
        # 并自带中英间距(实测 +2.25px)。显式点名 PingFang SC 反而是降级。
        # 真正的杠杆是字重: Medium 比 Regular 在汉字上多 10.9% 墨量(宽度不变)。
        lbl.setFont_(self._NSFont.systemFontOfSize_weight_(size, WEIGHT))
        lbl.setTextColor_(color)
        lbl.setStringValue_("")
        lbl.setAlignment_(self._align)
        lbl.setMaximumNumberOfLines_(maxlines)
        lbl.setLineBreakMode_(break_mode or self._wrap)
        lbl.setShadow_(self._shadow)          # 黑描边: 白底 PPT 上也清晰
        return lbl

    def _button(self, title, cb, tooltip: str = ""):
        from AppKit import NSButton, NSMakeRect
        b = NSButton.alloc().initWithFrame_(NSMakeRect(0, 0, 60, 24))
        b.setTitle_(title); b.setBordered_(False)
        b.setFont_(self._NSFont.systemFontOfSize_(14))
        if tooltip:
            b.setToolTip_(tooltip)

        def _clicked():
            # 点按钮不是"要打字" -> 先交还 key, 再做事。否则面板会把用户在自己
            # app 里按的快捷键吃掉(见 _release_focus 的键盘回归说明)。
            self._release_focus()
            cb()

        target = _make_button_target(_clicked)
        self._targets.append(target)          # 保持强引用, 防 GC(否则 target 变 None)
        b.setTarget_(target); b.setAction_("clicked:")
        self._ve.addSubview_(b)
        return b

    def _install_status_item(self):
        """菜单栏图标: 穿透模式下面板忽略鼠标事件, 只能从这里切回来/退出。"""
        try:
            from AppKit import (NSStatusBar, NSVariableStatusItemLength,
                                NSMenu, NSMenuItem)
            self._status = NSStatusBar.systemStatusBar().statusItemWithLength_(
                NSVariableStatusItemLength)
            self._status.button().setTitle_("🎧")
            menu = NSMenu.alloc().init()

            self._mi_through = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "开启鼠标穿透", None, "")
            t1 = _make_button_target(self._toggle_through)
            self._targets.append(t1)
            self._mi_through.setTarget_(t1); self._mi_through.setAction_("clicked:")
            menu.addItem_(self._mi_through)

            mi_quit = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("退出", None, "")
            t2 = _make_button_target(self._quit)
            self._targets.append(t2)
            mi_quit.setTarget_(t2); mi_quit.setAction_("clicked:")
            menu.addItem_(mi_quit)

            self._status.setMenu_(menu)
        except Exception:                     # noqa: BLE001
            # ⚠️ 走到这里时 `statusItemWithLength_` 可能**已经成功**、图标已经挂在
            # 菜单栏上了 —— 后面任何一步（建菜单项 / setTarget_ / setMenu_）失败都会
            # 跳到这里。直接 `= None` 会**丢掉引用**，而 close() 只在 `_status` 非 None
            # 时才 removeStatusItem_ → 留下一个**永远清不掉的孤儿图标**：
            # 点它没反应（没有菜单），而它偏偏是穿透模式下**唯一的恢复入口**。
            # 所以先把已创建的撤掉，再置 None。(2026-09-24 OCR 分块审计发现。)
            try:
                if self._status is not None:
                    from AppKit import NSStatusBar
                    NSStatusBar.systemStatusBar().removeStatusItem_(self._status)
            except Exception:                 # noqa: BLE001
                pass
            finally:
                self._status = None

    def _move_to_corner(self):
        from AppKit import NSScreen
        # 用面板自己所在屏幕的 visibleFrame, 与展开时的 clamp 保持同一坐标系
        scr = self._panel.screen().visibleFrame() if self._panel.screen() \
            else NSScreen.mainScreen().visibleFrame()
        self._panel.setFrameOrigin_((scr.origin.x + scr.size.width - self._width - 40,
                                     scr.origin.y + 78))

    # ---- 输入框 ----
    def _is_editing(self) -> bool:
        """用户此刻是否真的在输入框里打字。

        field editor 存在 = 正在编辑。NSTextField 拿到焦点时 first responder 是
        AppKit 临时建的 field editor(NSTextView), **不是输入框自己** —— 所以判据
        不是 `firstResponder() is self._input`。

        ⚠️ 出错时返回 **True**(不是 False): 这个值决定"面板能不能保持 key",
        返回 False 会让不变量在没有可靠依据的情况下**主动抢走焦点** —— 正在打字
        时被抢是最坏的失败模式。检测不出来就当作在打字, 保守。"""
        try:
            return self._input.currentEditor() is not None
        except Exception:                     # noqa: BLE001
            return True

    def _set_rule_focus(self, on: bool) -> None:
        """细线: 白 α0.18(静止) <-> 白 α0.45(聚焦)。

        ⚠️ **刻意不用暖黄**。这个 app 的层次轴是**白字 alpha**, 不是色相
        (正文 1.00 / 英文 0.80 / 草稿 0.50); 而暖黄在这个世界里**已经被术语行占用** ——
        让它同时表示"聚焦"会稀释那个信号。复用已承载语义的强调色是稀释, 不是节省。
        所以聚焦只沿用自己的语言: 把细线提到草稿那一档(光标同理, 见 _on_input_focus)。
        """
        try:
            c = self._NSColor.whiteColor().colorWithAlphaComponent_(
                RULE_A_FOCUS if on else RULE_A)
            self._input_rule.layer().setBackgroundColor_(c.CGColor())
        except Exception:                     # noqa: BLE001
            pass

    def _on_input_focus(self) -> None:
        self._set_rule_focus(True)
        try:
            # 光标显式设白。⚠️ **不设的话 AppKit 给的是 Catalog 语义色
            # `System textInsertionPointColor`** —— 实测 sRGB (0.0, 0.478, 1.0),
            # 即系统蓝, 比暖黄更外来。
            # ⚠️ 且 setInsertionPointColor_ 在 NSTextField 上**不存在**(实测),
            # 它在 NSTextView 上 —— 必须经 field editor 设。
            ed = self._input.currentEditor()
            if ed is not None:
                ed.setInsertionPointColor_(self._NSColor.whiteColor())
        except Exception:                     # noqa: BLE001
            pass

    def _on_input_blur(self) -> None:
        self._set_rule_focus(False)

    def _sync_focus_look(self) -> None:
        """按**实际编辑状态**同步细线与光标。每帧调一次, 状态没变就直接返回。

        ⚠️ 为什么不用委托的 `controlTextDidBeginEditing_`/`…EndEditing_`:
        实测**从不触发**(见 show() 里的说明) —— `orderFrontRegardless()` 时
        AppKit 已经把 field editor 装成了 first responder, "开始编辑"这个状态
        转换根本没发生过, 那个回调永远等不到。委托回调不可依赖, 所以改成在
        pump 里比对状态: **一个机制覆盖**「点进来 / 点出去 / 提交 / Esc /
        程序自己退让」全部路径, 而不是逐个交互点打补丁。"""
        on = self._is_editing()
        if on == self._focus_look_on:
            return
        self._focus_look_on = on
        if on:
            self._on_input_focus()
        else:
            self._on_input_blur()

    def _release_focus(self):
        """主动把 key window 交还出去。

        ⚠️ 为什么每次交互都要调: override canBecomeKeyWindow=True 之后, AppKit
        不再查询 needsPanelToBecomeKey —— 点面板任何地方(包括顶栏按钮)都可能让
        面板成为 key window, 于是**用户在自己 app 里按的键会被面板吃掉**
        (改之前面板永远不会 key, 所以这条回归是本次改动引入的)。
        "用户没在打字"的一切交互结束后都必须退让。
        """
        # ⚠️ 两个调用**分开** try: 合在一起时 makeFirstResponder_ 抛错会连
        # resignKeyWindow 一起跳过 —— 面板留在 key 状态, 而不变量下一帧再调一次,
        # 变成无界重试(实测模拟: 400 帧调 400 次仍未释放)。
        try:
            self._panel.makeFirstResponder_(None)
        except Exception:                     # noqa: BLE001
            pass
        try:
            self._panel.resignKeyWindow()
        except Exception:                     # noqa: BLE001
            pass

    def _submit_input(self):
        """回车: 取文字 -> 清空 -> **重新装上 field editor**(连续输入), 交出去。

        ⚠️ 这里刻意**不**调 _release_focus()。连续输入(Phase 3)要求提交后能接着打
        下一问, 而每次追问都要重新点一次输入框是实测确认的现状。

        ⚠️ 代价: 它与 pump() 里那条"键盘不自留"不变量正面冲突 —— 那条的判据是
        `isKeyWindow() and not _is_editing()`, 输入框一旦保持焦点, `_is_editing()`
        恒为真, 不变量就再也不触发, 面板可以无限期保持 key window, 于是"点面板空白
        处之后别的 app 按键被吞"那个 Phase 1 修过的 bug 有复活的口子。
        取舍(作者定案): 提交动作本身就是"我正在跟这个框交互"的强信号, 此刻让出
        焦点是错的 —— 所以让不变量在编辑态下显式让位, 靠三条护栏兜住:
          ① 进编辑态的**唯一**入口是用户真的点输入框: show() 显式清过一次
             (见 show()), 顶栏按钮与术语行点击都会 _release_focus;
          ② Esc(_cancel_input) 是显式的"我打完了"出口, 仍然让出 key;
          ③ 切到别的 app 会自然夺走 key(面板是 NonactivatingPanel, 从不激活 app)。
        仍未验证(自动化测不了): 连续问 3 轮后切到浏览器按方向键/空格是否正常。
        若被吞 -> 退回"提交后让出焦点"的旧行为, 或改成"答案流结束后再让出"。"""
        text = (self._input.stringValue() or "").strip()
        self._input.setStringValue_("")
        if text:
            self._on_submit(text)
        # 放在回调**之后**: 回调抛错也不能把焦点丢掉(否则下一问又要重新点一次)。
        try:
            self._panel.makeFirstResponder_(self._input)
        except Exception:                     # noqa: BLE001
            pass

    def _cancel_input(self):
        """Esc: 清空 + 交还焦点 —— 连续输入的唯一显式出口。"""
        self._input.setStringValue_("")
        self._release_focus()

    # ---- 展开 / 收回 ----
    def _gloss_h(self) -> float:
        """💡 解析高度: 收回态一行(尾部省略), 展开态最多四行铺满完整解析
        (锁定决策)。草稿两行不随模式变化, 所以固定区增量就是这两者之差。"""
        return GLOSS_H if self._collapsed else GLOSS_H_BIG

    def _pinned_h(self) -> float:
        return _pinned(self._gloss_h())

    def _expanded_scroll_h(self) -> float:
        """展开态: **面板总高** = 屏可见高的 60%(锁定决策), 转录区 = 总高 - 固定区。
        固定区要用展开态的 💡 行高(三行), 否则总高会超。留 20px 上下余量以便裁剪。

        ⚠️ 构造期调用时面板可能还没 orderFront, screen() 会是 None —— 那时退到
        主屏, **不要**退到某个写死的高度: 这个返回值还决定了滚动池大小 K
        (K = ceil(h/row_h)+2), 猜小了会在展开后露出空白行。"""
        from AppKit import NSScreen
        if self._expanded_h_user is not None:
            # 用户自己拖过展开态高度, 听他的 —— 但**地板仍然要过**: 否则在展开态把
            # 高度拖小之后, 按「展开」会让窗口比收回态还矮, 按钮语义直接反了。
            # (实测: 展开 518 -> 展开态拖到 292 -> 收回 362 -> 再展开只有 292。)
            return max(self._expanded_h_user, self._collapsed_scroll_h + self._row_h)
        scr = self._panel.screen() or NSScreen.mainScreen()
        vis_h = scr.visibleFrame().size.height if scr else 900.0
        total = min(0.60 * vis_h, vis_h - 40)
        # ⚠️ 展开必须**至少多露出一行**。用户把收回态拖得比"屏高 60%"还高时,
        # 光按屏高算会让展开态比收回态还矮 -> 按「展开」什么都不发生, 按钮像坏的。
        # (回归测试 R7 就是这么抓到的: 残留尺寸 521 时 assertGreater 失败。)
        return max(2 * self._row_h,
                   self._collapsed_scroll_h + self._row_h,
                   total - BOTTOM_PAD - _pinned(GLOSS_H_BIG) - 8 - HEADER_H)

    def _apply_mode(self, collapsed: bool):
        """切换收起/展开。面板原点在左下、向上长高, 所以**顶边保持不动**,
        否则顶栏按钮会跟着跳。

        ⚠️ 顶栏的「展开」按钮已删, 所以现在**只有答案接管会调它**
        (答案出现时展开、退出接管时还原)。保留它是因为那条路径仍需要这个能力。
        """
        from AppKit import NSScreen
        self._collapsed = collapsed
        # 展开高度现场重算, 不用 __init__ 里那个 —— 那时面板可能还没落到目标屏幕,
        # _expanded_scroll_h 会退化到 900px 兜底值, 复用就会算出错的总高。
        self._expanded_h = self._expanded_scroll_h()
        self._scroll_h = self._collapsed_scroll_h if collapsed else self._expanded_h
        self._pinned = self._pinned_h()
        new_h = BOTTOM_PAD + self._pinned + 8 + HEADER_H + self._scroll_h
        self._height = new_h
        self._tv.set_collapsed(collapsed)

        f = self._panel.frame()
        top = f.origin.y + f.size.height
        oy = top - new_h
        # ⚠️ screen() 可能为 None(面板还没 orderFront)。答案出现时的自动展开是在
        # 这个前提下也会走到这条路径的, 直接 .visibleFrame() 会 AttributeError 并把
        # 整个按钮回调打死 —— 取不到屏幕就不夹取, 只保持顶边不动。
        scr = self._panel.screen() or NSScreen.mainScreen()
        if scr is not None:
            vis = scr.visibleFrame()
            oy = max(vis.origin.y + 20,
                     min(oy, vis.origin.y + vis.size.height - new_h - 20))
        # ⚠️ 置标志再改 frame，最后**必须**在 finally 里复位：AppKit 会在
        # setFrame_display_ **内部同步**回调 windowDidResize → _sync_panel_size。
        # 那一刻下面那句缓存回写还没执行，缓存还是旧尺寸 —— 没有这个标志的话，
        # _sync_panel_size 会把我们自己展开误判成用户拖了窗口。
        self._programmatic_resize = True
        try:
            self._panel.setFrame_display_(
                ((f.origin.x, oy), (self._width, new_h)), True)
        finally:
            self._programmatic_resize = False
        # ⚠️ 必须回写轮询缓存: 这是**我们自己**改的 frame, 不是用户拖的。
        # 不回写的话下一轮 pump 的 _sync_panel_size 会把它当成用户缩放,
        # 于是展开时那个屏高 60% 的高度会被记成"用户拖出来的展开态高度"。
        # ⚠️ 要读**真实 frame** 而不是我们算的 new_h: AppKit 可能按
        # contentMinSize / resizeIncrements 微调落地高度, 记成 new_h 就会留下
        # 一个对不上的缓存值, 下一轮 sync 又把它当成用户拖拽(踩过: 跑一次
        # 回归测试就往仓库落一个 .window)。
        _fa = self._panel.frame()
        self._last_panel_size = (_fa.size.width, _fa.size.height)
        self._layout()

    def _on_follow_change(self, follow: bool):
        """跟随状态变化 -> 显示/隐藏"↓ 最新"按钮。"""
        try:
            self._btn_latest.setHidden_(follow)
        except Exception:                     # noqa: BLE001
            pass

    # ---- 用户缩放 ----
    def _load_window_state(self) -> None:
        """读回上次的窗口尺寸。fail-soft: 读不到/格式坏就用默认, 绝不抛。"""
        try:
            raw = WINDOW_STATE_FILE.read_text(encoding="utf-8").strip().lower()
            w_s, h_s = raw.split("x")
            w, h = float(w_s), float(h_s)
        except Exception:                     # noqa: BLE001
            return
        # 换过屏幕、手改坏了、或存了个荒唐值 -> 退回默认, 别把面板放到屏外
        if not (MIN_WIDTH <= w <= 8000.0 and self._min_height() <= h <= 8000.0):
            return
        # ⚠️ 还要按**当前屏幕**夹一次: 在外接大屏上调好的尺寸, 拔掉显示器后再启动
        # 会让面板出屏。实测 `.window=900x4000` 时上下都出屏, **顶栏的展开/退出按钮
        # 点不到 = 退不出程序**。这里只夹尺寸, 原点由 _move_to_corner 负责。
        from AppKit import NSScreen
        scr = NSScreen.mainScreen()
        if scr is not None:
            vis = scr.visibleFrame()
            w = min(w, max(MIN_WIDTH, vis.size.width - 40.0))
            h = min(h, max(self._min_height(), vis.size.height - 40.0))
        self._width, self._height = w, h
        self._collapsed_scroll_h = max(
            ROW_H, h - BOTTOM_PAD - PINNED_H - 8 - HEADER_H)

    def _save_window_state(self) -> None:
        # 尺寸 == 默认时没什么可记的 —— 直接不写文件。
        # ⚠️ 这一条也是**测试隔离**的关键: 回归测试里 R8(答案接管)会改面板尺寸,
        # 于是 `_user_resized` 被置真、close() 就往仓库落一个 `.window`。跑一次测试
        # 落一个文件, 而且残留值会改变下一次运行的行为。
        # ⚠️ 比的是**要写出去的值**(收回态高度), 不是 self._height ——
        # self._height 在答案接管时是展开态的高度(实测 518), 拿它比永远不等,
        # 守卫形同虚设。踩过。
        if (abs(self._width - WIDTH) < 0.5
                and abs(BASE_H + self._collapsed_scroll_h - HEIGHT) < 0.5):
            return
        try:
            # 存**收回态**的高度, 不是 self._height: 收回态占 99% 使用时间, 是用户
            # 平时看到的样子。存 self._height 的话, 在展开态退出 -> 下次启动的
            # 收回态会莫名其妙变很高(踩过: 存下来是 620x521 而不是 620x292)。
            WINDOW_STATE_FILE.write_text(
                f"{self._width:.0f}x{BASE_H + self._collapsed_scroll_h:.0f}\n",
                encoding="utf-8")
        except Exception:                     # noqa: BLE001
            pass

    def _min_height(self) -> float:
        """最小高度 = 固定区 + MIN_ROWS 行。行高随宽度变, 所以它必须现算。"""
        return BASE_H + self._row_h * MIN_ROWS

    def _apply_row_metrics(self, width: float) -> None:
        """按当前宽度算行尺寸并推给转录区(窄窗中文要更多行才不吞字)。"""
        lines = _lines_for_width(width)
        zh_h, row_h = _zh_h_for(lines), _row_h_for(lines)
        if abs(row_h - self._row_h) < 0.5:
            return
        self._row_h, self._zh_h = row_h, zh_h
        self._tv.set_row_metrics(zh_h, row_h, lines)
        # 行高变了 -> 最小高度也变了, 得同步给窗口, 否则缩不到新下限
        try:
            self._panel.setContentMinSize_((MIN_WIDTH, self._min_height()))
        except Exception:                     # noqa: BLE001
            pass

    def _hide_traffic_lights(self) -> None:
        """藏掉左上角三个系统按钮 —— 但**保留** Titled 带来的原生缩放能力。

        这是 macOS 社区的既有做法: Christian Tietze 2020-10 那篇博客的标题就是
        《Hide Traffic Light Buttons in NSWindow Without Removing Resize Functionality》,
        Ghostty 的 `HiddenTitlebarTerminalWindow.swift` 同款。
        Tietze 藏的是**四个**(含 .fullScreenButton); 我们实测只有 3 个
        (styleMask 没设 FullScreen 位, type 7 为 None), 所以循环写成 0..7 防御。

        ⚠️ 用 `setHidden_(True)` 而**不是** `removeFromSuperview()` —— 后者查不到
        任何来源支持(搜 `standardWindowButton removeFromSuperview` 零命中), 而
        Tietze 与 mkll/NSWindowStyles 两处有出处的做法都用 isHidden。
        「红绿灯会自己回来」的社区实证指的是**位置**在 resize 后复位, 不是可见性。
        ⚠️ 必须**可重复调用**: Ghostty 的注释原文 "macOS breaks it usually",
        所以 show() 里也再调一次。
        """
        try:
            for i in range(8):
                b = self._panel.standardWindowButton_(i)
                if b is not None:
                    b.setHidden_(True)
        except Exception:                     # noqa: BLE001
            pass

    # ---- 窗口级鼠标分派(移动 + 缩放, 含四角) ----
    def _zone_(self, lx: float, ly: float, w: float, h: float):
        """点 (lx,ly)(面板内坐标, y 向上) 落在哪个缩放区; 不在最外一圈则 None。"""
        e = EDGE_BAND
        left, right = lx < e, lx > w - e
        bottom, top = ly < e, ly > h - e
        if top and left:
            return "tl"
        if top and right:
            return "tr"
        if bottom and left:
            return "bl"
        if bottom and right:
            return "br"
        if left:
            return "l"
        if right:
            return "r"
        if top:
            return "t"
        if bottom:
            return "b"
        return None

    def _resized_frame(self, zone: str, start, dx: float, dy: float):
        """按 zone 决定哪条边跟手, **对边固定** —— 与正常窗口一致。

        `start` 是 mouseDown 时的 frame; `dx/dy` 是 AppKit 屏幕坐标增量(y 向上)。
        """
        from AppKit import NSScreen
        sx, sy = start.origin.x, start.origin.y
        sw, sh = start.size.width, start.size.height
        right, top = sx + sw, sy + sh
        w = max(MIN_WIDTH, sw + dx if "r" in zone else (sw - dx if "l" in zone else sw))
        h = sh + dy if "t" in zone else (sh - dy if "b" in zone else sh)
        h = max(self._min_height(), h)
        x = right - w if "l" in zone else sx
        y = sy if "t" in zone else top - h
        scr = self._panel.screen() or NSScreen.mainScreen()
        if scr is not None:
            vis = scr.visibleFrame()
            x = max(vis.origin.x, min(x, vis.origin.x + vis.size.width - w))
            y = max(vis.origin.y, min(y, vis.origin.y + vis.size.height - h))
        return ((x, y), (w, h))

    def _track_loop(self, zone, start, mouse0) -> None:
        """**嵌套事件循环**: 自己抽拖拽事件, 不依赖主循环。

        ⚠️ 这是本功能的关键(2026-09-24 实测): 原生拖边缘缩放时 AppKit 进入它自己的
        事件跟踪循环, **我们的主循环被卡住 1239.8ms**(实测: 空闲期 pump 最大间隔
        9.6ms, 拖拽期 1239.8ms)。所以"在 pump 里轮询尺寸再重排"这条路**根本跑不到**
        —— 窗口框在动、内容冻着, 松手才跳一下。手感就是作者说的"卡顿不够丝滑"。
        嵌套循环跑在 `NSEventTrackingRunLoopMode` 里, 事件立刻到手, 每一步都**同步**
        重排 -> 真正跟手。这也是 Cocoa 社区做自定义窗口缩放的经典手法。

        ⚠️ mask 必须**同时**含 LeftMouseDragged 与 LeftMouseUp: 只匹配前者的话
        松手后循环会一直转下去(表现为"手松了还在缩")。
        """
        from AppKit import (NSLeftMouseDraggedMask, NSLeftMouseUpMask,
                            NSEventTrackingRunLoopMode, NSDate, NSEvent,
                            NSEventTypeLeftMouseUp)
        win = self._panel
        mask = NSLeftMouseDraggedMask | NSLeftMouseUpMask
        while True:
            e = win.nextEventMatchingMask_untilDate_inMode_dequeue_(
                mask, NSDate.distantFuture(), NSEventTrackingRunLoopMode, True)
            if e is None or e.type() == NSEventTypeLeftMouseUp:
                break
            mx, my = _xy(NSEvent.mouseLocation())
            dx, dy = mx - mouse0[0], my - mouse0[1]
            # 只有 "resize" 一种模式(移动由 AppKit 的 movableByWindowBackground
            # 自己处理, 不走这里)。原先还有个 "move" 分支, 全仓无调用点, 已删。
            new = self._resized_frame(zone, start, dx, dy)
            win.setFrame_display_(new, True)
            self._sync_panel_size()            # 每一步同步重排 -> 丝滑

    def _on_drag_layer_mousedown(self, event) -> bool:
        """拖拽层收到 mouseDown: 在**最外一圈(含四角)**就自己跑嵌套循环缩放,
        否则返回 False 让调用方走 AppKit 原生的 performWindowDragWithEvent_(移动)。

        ⚠️ 四角必须自己来: AppKit 对无边框窗口**只给四条边注册缩放区, 四角没有**
        (实测: 四角有缩放光标, 但拖拽完全无反应)。
        """
        from AppKit import NSEvent
        f = self._panel.frame()
        mx, my = _xy(NSEvent.mouseLocation())
        zone = self._zone_(mx - f.origin.x, my - f.origin.y,
                           f.size.width, f.size.height)
        if not zone:
            return False
        self._track_loop(zone, f, (mx, my))
        return True

    def _sync_panel_size(self) -> None:
        """轮询面板实际尺寸; 用户拖过就把布局跟上。

        兼顾两条路径：
        · **轮询**（每帧比两个浮点，近乎免费）—— 覆盖非拖拽期间的变化；
        · **windowDidResize 委托** —— 原生缩放期间 AppKit 在自己的跟踪循环里回调它，
          而那时轮询**根本跑不到**（实测 pump 相邻两次调用间隔 1239.8ms）。
          委托是 live resize 唯一的正规钩子。

        ⚠️ 委托会在 `setFrame_display_` **内部同步**回调进来，所以调用方若是在
        程序性改尺寸，必须先置 `_programmatic_resize`（见 _apply_mode）。

        ⚠️ 宽度变化会**连带改行高**(窄窗中文要更多行才不吞字, 见 _apply_row_metrics)。
        回收池依赖的不变量是"所有行**等高**", 不是"行高 == 70" —— 换一组统一尺寸
        是安全的, 池的容量会跟着重算。
        """
        try:
            f = self._panel.frame()
        except Exception:                     # noqa: BLE001
            return
        size = (f.size.width, f.size.height)
        if size == self._last_panel_size:
            return
        old_w = self._width                   # ⚠️ 必须在覆盖 _width **之前**取
        self._last_panel_size = size
        self._width, self._height = size
        scroll_h = max(self._row_h, self._height - BOTTOM_PAD - self._pinned - 8 - HEADER_H)
        self._scroll_h = scroll_h
        # ⚠️ 只有**用户拖出来的**尺寸才值得记。没有这个守卫的话，光是构造 + close()
        # 就会往仓库里写 .window —— 跑一次回归测试就落一个文件，而且残留值会改变
        # 下一次运行的行为（测试结果依赖上次留下的状态）。**这个坑踩过两次。**
        #
        # `_programmatic_resize` 这一层是第三次：前两次修补（下面那句缓存回写、
        # 以及 _save_window_state 改成比"要写出去的值"）只堵住了**轮询**路径。
        # 后来为 live resize 加了 windowDidResize 委托，它会**同步**回调进本函数 ——
        # 于是我们自己展开面板（答案接管 / 收起展开）时，这里又把用户调好的高度
        # 顶掉成接管态的高度，并让 .window 被写出去。（2026-09-24 OCR 分块审计发现）
        if not self._programmatic_resize:
            self._user_resized = True
            # 回写给他**当前所在的那个态**：收回态调好的高度不该按一次「展开」就消失。
            if self._collapsed:
                self._collapsed_scroll_h = scroll_h
            else:
                self._expanded_h_user = scroll_h
        # 宽度变了 -> 先按新宽度定行尺寸(窄窗中文要更多行), 再推宽度、重折答案。
        # 顺序要紧: 行高决定槽位容量, 槽位容量又决定答案行够不够放。
        if abs(self._width - old_w) > 0.5:
            self._apply_row_metrics(self._width)
        self._tv.set_width(self._width)
        # 宽度变了 -> 答案行必须**重折**: `_answer_rows` 是按折行**当时的宽度**实测
        # 出来的, 宽度一变就是陈的。窗口缩窄后每行需要更多行, 超过槽位容量
        # (ROW_ZH_H) 的部分会被 AppKit **静默截断**(不留省略号)。
        # ⚠️ MIN_WIDTH 只保护**字幕**(字幕每次按新宽度重渲), **不保护答案**(答案是
        # 缓存值) —— 这是 2026-09-24 代码审计抓出来的漏网路径。
        if abs(self._width - old_w) > 0.5:
            self._answer_refold()
        self._layout()

    def _layout(self):
        from AppKit import NSMakeRect
        pad = PAD
        w = self._width - 2 * pad
        y = BOTTOM_PAD
        gh = self._gloss_h()
        # ⌨ 输入行钉在面板最底部。y 从这里往上走, 与 _pinned() 的加和顺序必须
        # 逐项一致(输入行 + 间隔 + 💡 + 间隔 + 草稿 + 间隔 + 草稿译文),
        # 否则面板总高与内容会错位。
        # 细线在输入框正下方 1px, 输入框占剩下的 INPUT_H - RULE_H —— 文字底部贴着
        # 细线, 细线即"基线"。两者高度之和仍等于 INPUT_H, 所以 _pinned() / BASE_H /
        # 高度重算都不用动。
        self._input_rule.setFrame_(NSMakeRect(pad, y, w, RULE_H))
        self._input.setFrame_(NSMakeRect(pad, y + RULE_H, w, INPUT_H - RULE_H))
        y += INPUT_H + INPUT_GAP
        # 💡 钉在固定区顶部: 收回态一行(尾部省略), 展开态三行铺满完整解析。
        # 文字不变, 只改 frame 高度/行数上限/换行模式 —— 一份字符串两态通用。
        # (AppKit: 要换行必须 WordWrapping; 单行才用 TruncatingTail 拿省略号。)
        self._gloss_lbl.setMaximumNumberOfLines_(1 if self._collapsed else 3)
        self._gloss_lbl.setLineBreakMode_(
            self._trunc if self._collapsed else self._wrap)
        self._gloss_lbl.setFrame_(NSMakeRect(pad, y, w, gh))
        self._gloss_hit.setFrame_(NSMakeRect(pad, y, w, gh))     # 点击区与术语行同框
        self._draft_lbl.setFrame_(NSMakeRect(pad, y + gh + 4, w, DRAFT_H))
        self._draft_zh.setFrame_(
            NSMakeRect(pad, y + gh + 4 + DRAFT_H + 4, w, DRAFT_ZH_H))
        self._scroll_y = BOTTOM_PAD + self._pinned
        self._tv.set_frame(self._scroll_y, self._scroll_h)           # 转录区
        # 顶栏: 从右边缘往左摆(列表是左->右顺序, 故 reversed)。sizeToFit 取文字
        # 实际宽度, 加内边距; 图标(⭐/✕)保底 28px 点击区 —— 它们的字形只有 15-24px,
        # 直接按字宽会给一个点不中的小目标。
        gap = 6.0
        x = self._width - pad
        for b in reversed(self._bar):
            b.sizeToFit()
            w = max(b.frame().size.width + 10.0, 28.0)
            x -= w
            b.setFrame_(NSMakeRect(x, self._height - 28, w, 24))
            x -= gap
        self._drag_layer.setFrame_(((0.0, 0.0), (self._width, self._height)))
        self._ve.setFrame_(self._panel.contentView().bounds())
        self._scrim.setFrame_(self._ve.bounds())

    def _install_edit_menu(self):
        """挂最小 Edit 菜单。

        ⚠️ 为什么需要: Accessory app 默认没有主菜单, 而 ⌘X/⌘C/⌘V/⌘A 是**经主菜单
        的 keyEquivalent** 路由到 first responder 的 —— 不挂这份菜单, 输入框里
        这些键就是死的。这是计划里标注的"最大残留风险"; 构造已通过, 但
        **运行时是否真生效需要真人按键验证**(见 Phase 1 未验证项)。
        """
        try:
            from AppKit import NSApplication, NSMenu, NSMenuItem
            app = NSApplication.sharedApplication()
            if app.mainMenu() is not None:
                return                        # 已有主菜单就别覆盖
            main = NSMenu.alloc().init()
            holder = NSMenuItem.alloc().init()
            edit = NSMenu.alloc().initWithTitle_("Edit")
            # target 留空 -> 走响应链(编辑动作由当前 field editor 实现)
            for title, sel, key in (("Cut", "cut:", "x"), ("Copy", "copy:", "c"),
                                    ("Paste", "paste:", "v"),
                                    ("Select All", "selectAll:", "a")):
                edit.addItem_(NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                    title, sel, key))
            holder.setSubmenu_(edit)
            main.addItem_(holder)
            app.setMainMenu_(main)
        except Exception:                     # noqa: BLE001
            pass

    # ---- 对外 ----
    def show(self):
        from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
        app = NSApplication.sharedApplication()
        app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        self._install_edit_menu()
        # orderFrontRegardless 不变: 显示不激活 app(绝不调 NSApp.activate —— 已废弃且会抢焦点)
        self._panel.orderFrontRegardless()
        self._hide_traffic_lights()   # 红绿灯会自己回来(社区实证), 每次显示都再藏一遍
        # ⚠️ 必须显式清掉 first responder。实测: orderFrontRegardless 之后 AppKit 会
        # **自动**把输入框的 field editor 装成 first responder —— 于是 `_is_editing()`
        # 从启动那一刻起就恒为 True, pump 里那条"键盘不自留"不变量被**永久短路**,
        # 一次都不会触发。踩过: 三轮验证全绿, 因为都用手动 makeFirstResponder_ 驱动,
        # 没复现"启动即编辑态"这个真实状态。清掉之后, 只有用户真的点了输入框才进编辑态。
        self._release_focus()

    def close(self) -> None:
        """撤掉面板与菜单栏图标。**幂等**, 可重复调用(✕ / Ctrl+C / 正常结束都会走)。

        ⚠️ 为什么必须有: 退出后 main 还要走"是否存入 Obsidian"的 `input()` 询问,
        那是**主线程上的阻塞**。旧代码从不关面板 -> 窗口留在屏上且不再刷新(主循环
        已退出, 没人再 pump), 用户看到的就是"点了 ✕ 就卡死"; 而面板是
        NSFloatingWindowLevel + CanJoinAllSpaces, 全屏 PPT 时它跟着走、终端提示
        被压在后面, 根本看不到那个提问。

        ⚠️ orderOut 之后必须**跑一轮 run loop**(distantPast)让 AppKit 把这下提交出去 ——
        紧接着就阻塞在 input() 上的话, 窗口可能还留在屏幕上(同 pump() 里那个
        "不跑 run loop 就不提交"的坑)。
        """
        if getattr(self, "_closed", False):
            return
        self._closed = True
        # 先收一次尺寸再落盘: 用户拖完**立刻**按 ✕ 时, pump 可能还没来得及轮询到。
        self._sync_panel_size()
        if self._user_resized:
            self._save_window_state()
        try:
            self._panel.orderOut_(None)
        except Exception:                     # noqa: BLE001
            pass
        status = getattr(self, "_status", None)
        if status is not None:
            try:
                from AppKit import NSStatusBar
                NSStatusBar.systemStatusBar().removeStatusItem_(status)
            except Exception:                 # noqa: BLE001
                pass
            self._status = None
        if _HAS_APPKIT:
            try:
                from AppKit import NSRunLoop, NSDate
                NSRunLoop.currentRunLoop().runUntilDate_(NSDate.distantPast())
            except Exception:                 # noqa: BLE001
                pass

    def add_draft(self, en: str):
        self._draft = en
        self._mark_dirty(urgent=True)

    def draft_zh(self, zh: str):
        """草稿的即时中文译文(定稿到来前的先看版)。"""
        self._draft_zh_val = zh or ""
        self._mark_dirty(urgent=True)

    def terms(self, hits) -> None:
        """本句命中的术语 [(术语, 解析), ...]。

        **不直接显示解析**: 屏幕上只出现术语名(便宜、不抢阅读), 点一下才展开并钉住
        (见 _on_gloss_click)。`_terms` 也**不随 finalize 清空** —— 术语每 ~10 句才
        命中一次, 若跟着每句清空, 名字只活 1 句就没了, 这才是"太快消失"的真因。
        钉住的术语(_pinned_term)更不会被新命中的术语顶掉。
        """
        self._terms = list(hits or [])
        self._mark_dirty(urgent=True)

    def _on_gloss_click(self):
        """点术语行: 未钉 -> 钉住当前术语展开解析; 已钉 -> 取消, 退回术语名列表。"""
        self._pinned_term = None if self._pinned_term is not None \
            else (self._terms[0] if self._terms else None)
        self._release_focus()        # 点这里也会让面板变 key(见 _release_focus)
        self._mark_dirty(urgent=True)

    def _gloss_text(self) -> str:
        if self._pinned_term is not None:
            t, note = self._pinned_term
            return f"📌 {t}：{note}"
        if self._terms:
            return "◈ " + " · ".join(t for t, _ in self._terms) + "  ▸ 点开解析"
        return ""

    def stream_zh(self, delta: str):
        self._streaming = True
        self._cur_zh += delta
        self._mark_dirty()

    def stream_en(self, delta: str):
        self._cur_en += delta
        self._mark_dirty()

    def finalize(self, en: str, zh: str):
        if not zh and not en:
            return
        self._history.append((en, zh))
        self._cur_zh, self._cur_en, self._streaming, self._draft = "", "", False, ""
        self._draft_zh_val = ""
        self._mark_dirty(urgent=True)

    # ---- 答案接管(Phase 3) ----
    # 一条线程两个入口(「讲一下」按钮 / 输入框追问), 共用一块渲染区: 答案活跃时
    # 转录区喂**答案行**, 否则喂字幕行。为什么是接管而不是新开一块: 见模块顶部
    # GLOSS_PREFIX 那一段(真实答案比整个转录区还高, 没有别的槽位装得下)。
    def answer_delta(self, delta: str):
        """答案流式增量。

        ⚠️ **非紧急** mark_dirty(与 stream_zh 同规矩): 16ms 合并闸门本来就是给流式
        增量用的; urgent 只留给离散事件(接管开始 / answer_done / 退出接管)。"""
        if not delta:
            return
        if self._answer_finished:
            # 上一轮已经结束 -> 这是**新一轮**的第一个增量。先清掉上一轮的答案,
            # 否则两轮会粘成一段读不通的文字(实测: 4 行涨到 8 行, 直到
            # answer_done 对账才恢复)。
            # 取舍: **一轮显示一轮的答案**。追问本身就说明上一轮已经看过 ——
            # 继续堆在屏上只会把两轮混起来。完整线程仍然全部落进 Obsidian
            # 笔记与终端日志, 不丢。
            self._answer_reset()
        self._answer_enter()
        self._answer_feed(delta)
        self._mark_dirty()

    def answer_done(self, question: str, text: str):
        """一轮回答结束。`text` 是引擎给的**完整**回答, 以它为准对账: 失败路径
        (没 key / 云端异常 / 上下文超限)一个字都不流, 全部内容只在这一次调用里。

        (question 只有日志/落盘用途, 渲染只用回答正文。)"""
        text = text or ""
        if not text and not self._answer_text:
            # 空回答 -> **不要**进入接管。接管会把渲染源换成 0 行的答案: 屏幕整个
            # 空掉、字幕被藏起来、面板还自动展开, 而且**没有任何自动恢复路径**
            # (实测: 5 句字幕不可见 + 面板 362→519, 只能靠「新话题」救回来)。
            # 一个字都没产出, 就当这一轮没发生过。
            return
        self._answer_enter()
        got = self._answer_text
        if text and text != got:
            if text.startswith(got):
                self._answer_feed(text[len(got):])
            else:                             # 对不上就以引擎的完整版为准
                self._answer_reset()
                self._answer_feed(text)
        self._answer_feed("", flush=True)     # 收尾: 最后那条没有换行的源行也要闭合
        self._answer_finished = True          # 下一轮的增量据此判断"该重置了"
        self._mark_dirty(urgent=True)

    def _answer_enter(self):
        """第一份答案内容到达 -> 进入接管(含自动展开)。"""
        if self._answer_on:
            return
        self._answer_on = True
        # 自动展开到既有的"展开态"(屏可见高 60%, 与 ▾ 展开 是同一个状态)。
        # ⚠️ **只在接管开始与结束时改窗口尺寸**: _apply_mode 会取 NSScreen、重设全部
        # subview frame、setFrame_display_(窗口 resize + 整窗重绘), 并让转录池按新高度
        # 重建 —— 按 token 调它等于每个增量把整个面板重建一次。
        # 用户原来的收起/展开选择记下来, 退出接管时还原。
        self._mode_before_answer = self._collapsed
        if self._collapsed:
            self._apply_mode(False)

    def _clear_answer(self):
        """退出接管: 清答案缓冲 + 还原面板状态 + 字幕重现(「新话题」按钮走这里)。

        **字幕不会丢** —— 这是接管方案的核心安全性质: _history 只由 finalize() 追加,
        _render 从不碰它, 所以接管期间到达的句子一直在后台累积, 这里只是把渲染源
        换回它, 停下期间的字幕会一次性全部出现。"""
        if not self._answer_on and self._tv_mode == "cap":
            return
        self._answer_on = False
        self._answer_reset()
        if self._collapsed != self._mode_before_answer:
            self._apply_mode(self._mode_before_answer)
        self._mark_dirty(urgent=True)

    def _answer_reset(self):
        self._answer_text = ""
        self._answer_rows = []
        self._answer_pend = ""
        self._answer_fold = 0
        self._answer_pr = []
        self._answer_pw = []
        self._answer_finished = False

    def _answer_refold(self) -> None:
        """按**当前宽度**把答案重新折一遍。宽度变化时调用(见 _sync_panel_size)。

        只清折行产物, 不动 `_answer_text` —— 它是重折的输入, 也是 answer_done 对账
        的真源。实现上就是把它清空再喂回去, 复用 `_answer_feed` 这一条路径,
        不另写一套折行逻辑。
        """
        if not self._answer_on or not self._answer_text:
            return
        text = self._answer_text
        self._answer_rows = []
        self._answer_pend = ""
        self._answer_fold = 0
        self._answer_pr = []
        self._answer_pw = []
        self._answer_text = ""
        # ⚠️ `flush` 必须跟"答案流完了没"走，**不能恒为 True**：
        # 这个函数会在**流式期间**被触发（用户读答案时拖窗口改宽度 → _sync_panel_size
        # → 这里）。`flush=True` 会把**还在流式中的最后一行**当成已闭合提交，于是
        # 下次增量到来时它只能另起一行 —— 被 SSE 切开的半截词（"regres" + "sion"）
        # 就这样变成屏上两行。
        # 流完时（_answer_finished）才该 flush，否则最后一行反而会悬着不落行。
        # (2026-09-24 OCR 分块审计发现。)
        self._answer_feed(text, flush=self._answer_finished)

    def _answer_feed(self, delta: str, flush: bool = False):
        """把新到的答案文本喂进折行状态机。

        源行(模型输出的换行)一旦闭合就折成行**定稿**; 只有最后那条未闭合的源行会
        随增量重排, 而重排只从"还没落行的词"开始 —— 已闭合的行不再测量。
        (全量重排的代价是实测出来的: boundingRectWithSize_ 单次 66µs, 2000 字符的
         答案每来一个增量重排一次要 5-10ms, 而这是主线程 —— 它还背着音频循环。)"""
        if delta:
            self._answer_text += delta
            self._answer_pend += delta
        while True:
            raw = self._answer_take_closed_line(flush)
            if raw is None:
                break
            self._answer_commit_line(raw)
        self._answer_wrap_pending()

    def _answer_take_closed_line(self, flush: bool):
        """取出一条**已闭合的源行**原文(没有就 None), 并把它的词折进折行状态。"""
        s = self._answer_pend
        nl = s.find("\n", self._answer_fold)
        if nl < 0:
            if flush and s:
                nl = len(s)                   # 收尾: 结尾没有换行的那条也算闭合
            else:
                self._answer_fold_words()
                return None
        self._answer_pw.extend(s[self._answer_fold:nl].split())
        raw = s[:nl]
        self._answer_pend = s[nl + 1:]
        self._answer_fold = 0
        return raw

    def _answer_fold_words(self):
        """把未闭合区里的**完整词**折进 _answer_pw。

        ⚠️ 只折到最后一个空白为止: SSE 分片会把一个词切成两半, 把半个词当成一个
        词, 下一片到达时就会变成两个词("regres" + "sion")。"""
        s = self._answer_pend
        rest = s[self._answer_fold:]
        if rest and not rest[-1].isspace():
            j = max(rest.rfind(" "), rest.rfind("\t"))
            if j < 0:
                return
            rest = rest[:j + 1]
        if rest:
            self._answer_pw.extend(rest.split())
            self._answer_fold += len(rest)

    def _answer_commit_line(self, raw: str):
        """一条源行闭合: 把折好的词收成行, 提交进 _answer_rows。

        唯一的特例是 `EN：` 英文辅助行 —— 它是**上一行的小字**, 不是一张新卡片
        (这正是「中文为主 + 英文辅助」在行槽里的落法: 18pt 大字位 = 中文讲解,
        11pt 小字位 = EN：教授的原文措辞/术语)。"""
        self._answer_wrap_pending()           # 收尾时可能一次折进很多词, 先闭合能闭合的
        s = (raw or "").strip()
        pw = self._answer_pw
        pr = list(self._answer_pr)
        self._answer_pw, self._answer_pr = [], []
        if not s:
            return
        if s.startswith(GLOSS_PREFIX):
            rows = self._answer_rows
            if rows and not rows[-1][1]:
                rows[-1] = (rows[-1][0], s)
            else:
                rows.append((s, ""))          # 罕见: 没有上一行可挂 -> 自己占一行
            return
        if pw:
            # 到这里 pw 里的词必然**合起来放得下**: _answer_wrap_pending 只在
            # "下一个词加上去就放不下"时闭合, 而"单个词自己就放不下"的超长词
            # (长 URL / 长 CJK) 已经在 _answer_take_row 的空行分支里按字符切开了。
            pr.append(" ".join(pw))
        for r in pr:
            self._answer_rows.append((r, ""))

    def _answer_wrap_pending(self):
        w = self._answer_pw
        # ⚠️ 判据是 `while w` 而**不是** `while len(w) > 1`: 只剩一个词时，它也可能
        # 是"自己就放不下"的超长词(长 URL / 一长串 CJK)，必须交给 _answer_take_row
        # 切开；用 >1 会让它绕过 fit 判定，直接落到 _answer_commit_line 的
        # 无检查 append 上并被静默裁掉。单个放得下的词会让 _answer_take_row 返回
        # (None, w) 而正常 break —— 既不会提前闭合该等的行，也不会死循环。
        while w:
            row, rest = self._answer_take_row(w)
            if row is None:
                break
            self._answer_pr.append(row)
            w = rest
        self._answer_pw = w

    def _answer_take_row(self, words: list):
        """贪心收一行: 返回 (行文本, 剩余词); 还没法闭合就 (None, words)。

        ⚠️ 只在"下一个词已经到齐、且加上它就放不下"时才闭合 —— 文本只追加, 一个词
        一旦放不进当前行就**永远**放不进, 所以那一刻的 cur 就是定稿。于是每个词只
        参与一次测量, 增量代价与新增词数成正比, 不随答案总长增长。"""
        cur: list = []
        for i, w in enumerate(words):
            if not cur and not self._answer_fits(w):
                # ⚠️ 空行 + **单个词自己就放不下**。上面那条 `cur and ...` 判不到它
                # (cur 为空就直接 append)，于是这一行超出槽位会被**静默裁掉** ——
                # NSTextField 超过 maximumNumberOfLines 既不留省略号也不报错。
                # 实测触发阈值: 单个 token > 102 ASCII / > 68 CJK 字符
                # (长 URL、一长串汉字、无空格的语言都会踩到)。
                cut = self._answer_split_to_fit(w) or w[:1]   # 保底一个字符, 防死循环
                rest = w[len(cut):]
                return cut, ([rest] if rest else []) + words[i + 1:]
            cand = " ".join(cur + [w])
            if cur and not self._answer_fits(cand):
                return " ".join(cur), words[i:]
            cur.append(w)
        return None, words

    def _answer_split_to_fit(self, word: str) -> str:
        """把放不下的**无空格长词**按字符切成能放下的最长前缀(二分, O(log n) 次测量)。

        为什么是切而不是丢: 长 URL、长 CJK 串在课堂内容里真会出现(链接、引文、
        外文), 丢掉就是信息损失; 切开至少全都看得见。"""
        lo, hi, best = 1, len(word), 0
        while lo <= hi:
            mid = (lo + hi) // 2
            if self._answer_fits(word[:mid]):
                best, lo = mid, mid + 1
            else:
                hi = mid - 1
        return word[:best]

    def _answer_fits(self, text: str) -> bool:
        """这段文本在当前文档宽度下放得下 ANSWER_MAX_LINES 个视觉行吗。

        宽度取 TranscriptView 的**实测**文档宽度, 不是面板宽度猜出来的值: 展开态挂
        竖向滚动条时 clip 比面板窄十几 px, 猜宽了标签会静默裁掉第三行(NSTextField
        的 maxLines 超限既不留省略号也不报错 —— 见模块顶部那段)。"""
        if len(text) <= 24:
            # 最宽的字符在 18pt 下约 18px -> 24 字符 <= 432px, 一定只占一行。
            # 这是纯粹的快速路径: 它把大部分词从 66µs 的实测里省掉。
            return True
        try:
            h = _measure_text_h(text, self._tv.text_width() - 2.0, self._answer_font)
        except Exception:                     # noqa: BLE001
            return True                       # 量不出来就放行: 宁可多一行风险, 也别整段不显示
        return h <= ANSWER_TEXT_H

    def _answer_view_rows(self) -> list:
        """当前该渲染的行 = 已定稿行 + 未闭合源行的临时行。

        最后那行含尚未折行的残词(可能是个还没写完的词), 它就是打字机效果的来源。"""
        rows = list(self._answer_rows)
        rows.extend((r, "") for r in self._answer_pr)
        tail = " ".join(self._answer_pw)
        rest = self._answer_pend[self._answer_fold:].strip()
        if rest:
            tail = f"{tail} {rest}".strip()
        if tail:
            rows.append((tail, ""))
        return rows

    def _set_cached(self, key: str, lbl, text: str):
        """标签文本按 key 缓存, 相同就跳过写入。

        ⚠️ _render 每 16ms 就可能跑一次(答案流式期间每帧都会走到这), 而
        NSTextField.setStringValue_ 是 O(len) 的排版 + 重绘。与 transcript_view
        的槽位缓存同一个理由。"""
        if self._label_cache.get(key) == text:
            return
        self._label_cache[key] = text
        lbl.setStringValue_(text)

    # ---- 渲染合并 ----
    # 现状: main.py::drain() 会把 streamq 排空, 而每个增量都触发一次完整 _render
    # (11 次无条件 setStringValue_)。一个音频 tick 内排空 N 个增量 = N 次全量重绘。
    # 改成脏标记 + 时间闸门: 一串增量合并成一次绘制, 硬事件(定稿/术语)立即换屏。
    def _mark_dirty(self, urgent: bool = False):
        self._dirty = True
        if urgent:
            self._urgent = True
        self._flush_if_due()

    def _flush_if_due(self):
        if not self._dirty:
            return
        if not self._urgent:
            now = time.monotonic()
            if now - self._last_flush < FLUSH_DT:
                return
            self._last_flush = now
        self._dirty = False
        self._urgent = False
        self._last_flush = time.monotonic()
        self._render()

    def pump(self):
        """驱动 AppKit。⚠️ 必须显式排空 NSApplication 事件队列并 sendEvent_,
        否则真实鼠标点击会一直积压在 NSApp 队列里, 永远到不了窗口/按钮
        (实测: 只跑 NSRunLoop.runUntilDate_ 时事件仍在队列中)。

        顺序很重要(滚动流畅度):
          ① 先派发事件 —— 滚轮在这里移动 clip origin
          ② 立刻 tick + flush —— 把该重算的槽位算好、标签写好
          ③ **最后**才让步 —— 让 AppKit 把标签画到屏幕
        旧顺序是 ①→③→②, 等于"先空转 8ms 再写标签", 画面要等下一轮才更新,
        白白多一帧(8ms)延迟。

        让步时长靠"距上次事件多久"决定, 不靠"本轮是否有事件"(见常量说明):
        手势期间只跑 run loop 不睡(零延迟), 停手 150ms 后回到 8ms 省 CPU。
        """
        if not _HAS_APPKIT:
            return
        from AppKit import (NSApplication, NSEventMaskAny, NSDefaultRunLoopMode,
                            NSRunLoop, NSDate)
        app = NSApplication.sharedApplication()
        mode = NSDefaultRunLoopMode
        busy = False
        while True:
            ev = app.nextEventMatchingMask_untilDate_inMode_dequeue_(
                NSEventMaskAny, None, mode, True)
            if ev is None:
                break
            app.sendEvent_(ev)
            busy = True
        # 键盘不自留 —— 用**不变量**兜住所有路径, 而不是逐个交互点打补丁。
        # override canBecomeKeyWindow=True 之后, 点面板的**任何**非控件区域
        # (转录区 / 草稿行 / 顶栏空白 / 缝隙)都会让面板变成 key window 并赖在那里,
        # 于是用户在别的 app 里按的键被吃掉。独立验证实测确认了这四条路径都中招,
        # 而逐点补 _release_focus() 只能覆盖已知的那几个。
        # 判据: 只有真的在编辑输入框时, 面板才允许是 key。退让后 isKeyWindow()
        # 转 False, 本条不再触发(不是每轮都调)。
        if self._panel.isKeyWindow() and not self._is_editing():
            self._release_focus()
        self._sync_focus_look()      # 细线/光标跟着真实编辑状态走(委托回调不可靠)
        # 用户拖过窗口边缘 -> 把宽度/转录区高度收进来并重排。放在 tick 之前:
        # tick 要基于更新后的几何算池, 顺序反了会画一帧旧几何。
        self._sync_panel_size()
        now = time.monotonic()
        if busy:
            self._last_ev_t = now
        self._tv.tick()          # 跟随状态机 + 闲置回底(含滚动后的池重算)
        self._flush_if_due()
        rl = NSRunLoop.currentRunLoop()
        if now - self._last_ev_t < PUMP_ACTIVE_S:
            # 手势中: 立刻返回, 但**仍跑完一轮 run loop** —— 不能真跳过,
            # 否则 CA 不提交、画面冻住(见常量说明的实测)。
            rl.runUntilDate_(NSDate.distantPast())
        else:
            rl.runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(PUMP_IDLE_S))

    # 收尾路径(main.py 文件播完后的冲刷循环)只调 drain()、不调 pump(),
    # 所以 mutator 自己会 _flush_if_due(); 那里每轮 sleep(0.02) > FLUSH_DT,
    # 末句仍会画出, main.py 无需改动。

    # ---- 按钮回调 ----
    def _quit(self):
        # 先撤窗口再回调: 用户点了 ✕, 必须**立刻**看到它消失。之后的收尾
        # (冲刷/询问是否存 Obsidian)会阻塞主线程, 窗口留着不动 = 看起来卡死。
        self.close()
        self._on_quit()

    def _flag(self):
        self._on_flag()

    def _ask(self):
        """「讲一下」: 用固定问题开一轮讲解。

        ⚠️ 这是 AppKit 主线程回调(pump -> sendEvent_ 派发进来的): 只能 queue.put /
        置标志 / 打印, **不许联网、不许 sleep、不许长持 qa_lock** —— 主线程同时还在
        跑音频循环与渲染, 在这里做任何慢事都是整条流水线停顿。
        (问题文本是常量: 转录底座由 answer_worker / answer_user_content 稍后附加。)"""
        self._on_ask()

    def _new_topic(self):
        """「新话题」: 清空问答线程 + 退出答案接管、回到字幕。"""
        self._on_new_topic()
        self._clear_answer()

    def _sync_trans_button(self):
        """按钮文字直接写状态(不靠颜色/图标变暗 —— emoji 不吃 tint, 且弱显色看不清)。"""
        from AppKit import NSColor
        self._btn_trans.setTitle_(TRANS_MODE_TITLE[self._trans_mode])
        try:                                  # 颜色只是锦上添花, 拿不到也不影响可用
            self._btn_trans.setContentTintColor_(TRANS_MODE_COLOR[self._trans_mode]())
        except Exception:                     # noqa: BLE001
            pass

    def _cycle_translate(self):
        """在三种模式间循环: 双语 → 只英文(矫正) → 纯转录 → 双语。

        单按钮循环而不是两个独立开关: 顶栏宽度不增, 且三态互斥本就是一个枚举,
        拆成两个布尔会造出一个无意义组合(译开 + AI 关 = 要中文但不调模型, 做不到)。
        想从双语直达纯转录要点两下 —— 那是低频操作, 换来的是永远看得见全部三态。"""
        order = ("both", "en", "raw")
        self.set_trans_mode(order[(order.index(self._trans_mode) + 1) % len(order)])

    def _toggle_translate(self):
        """[已弃用] 旧的两态切换。保留以免外部调用点报错, 内部改走 _cycle_translate。"""
        self._cycle_translate()

    def set_trans_mode(self, mode: str) -> None:
        """三种模式(见 TRANS_MODE_TITLE)。旧接口 set_translating(bool) 仍可用, 会映射成
        both/en —— 保持向后兼容, 调用方不必一次改完。"""
        if mode not in TRANS_MODE_TITLE:
            return
        self._trans_mode = mode
        if mode != "both":
            # 不出中文: 清掉可能在途的草稿译文/术语, 别让它们留在屏上误导
            self._draft_zh_val = ""
            self._terms = []
            self._pinned_term = None
            # ⚠️ 答案缓冲(_answer_*)刻意**不在这里清**: 讲解是独立入口, 不被这个开关
            # 替代、也不依赖它。往这个清理块里加答案状态就是把"译关仍可用"打掉。
        self._sync_trans_button()
        self._mark_dirty(urgent=True)
        self._on_translate(mode)

    def set_translating(self, on: bool) -> None:
        """向后兼容旧调用点: 开=双语, 关=只英文(矫正)。"""
        self.set_trans_mode("both" if on else "en")

    def _toggle_through(self):
        """切换鼠标穿透(仅从菜单栏调用; 面板按钮在穿透后会失效)。"""
        self._through = not self._through
        self._panel.setIgnoresMouseEvents_(self._through)
        if getattr(self, "_status", None) and getattr(self, "_mi_through", None):
            self._mi_through.setTitle_(
                "关闭鼠标穿透" if self._through else "开启鼠标穿透")

    def notice(self, msg: str, warn: bool = False):
        """引擎状态通知(云端降级/恢复), 由 main 经 streamq 转到主线程调用。

        字幕区不打断 —— 课上看的是内容; 状态走菜单栏图标(🎧→⚠️)加 tooltip,
        终端里留一份文字记录。"""
        self._engine_warn = warn
        status = getattr(self, "_status", None)
        if status is not None:
            try:
                btn = status.button()
                btn.setTitle_("⚠️" if warn else "🎧")
                btn.setToolTip_(msg if warn else "")
            except Exception:                 # noqa: BLE001
                pass
        print(("⚠ " if warn else "✅ ") + msg, flush=True)

    # ---- 渲染 ----
    def _render(self):
        # 转录区(含历史/实时行/回收/跟随)整个交给 TranscriptView。
        # ⚠️ 一次 set_content 原子写完 —— 拆成 set_items + set_live 会让最新句
        #    既进历史又留在实时位, 屏上重复一行(见 TranscriptView.set_content)。
        #
        # 答案接管: 这里按"答案是否活跃"分支 —— 这是**唯一**喂 self._tv 的地方,
        # 所以答案要上屏只需要改这一处(Phase 3 的接管方案就建立在这条上)。
        # 切进/切出那一帧用 replace_items(整体替换, 不记追加): 条数从 N 条字幕变成
        # M 条答案行再换回来, 按差值记账会凭空多出 N-M 次"新句到达"(见它的注释)。
        if self._answer_on:
            rows = self._answer_view_rows()
            if self._tv_mode != "ans":
                self._tv_mode = "ans"
                self._tv.set_rows_verbatim(True)    # 行 = (大字中文讲解, 小字 EN：英文)
                self._tv.set_scroll_hold(True)      # 读答案期间不许自动回底(坑 1)
                self._tv.replace_items(rows)
                # 答案是从第一行读起的**文档**, 不是"最新在最下"的字幕流
                self._tv.scroll_to_top()
            else:
                self._tv.set_content(rows, "", "", False)
        elif self._tv_mode != "cap":
            self._tv_mode = "cap"
            self._tv.set_rows_verbatim(False)
            self._tv.set_scroll_hold(False)
            self._tv.replace_items(self._history)
            self._tv.scroll_to_bottom()
        else:
            self._tv.set_content(
                self._history, self._cur_en, self._cur_zh,
                bool(self._streaming or self._cur_zh or self._cur_en))
        # 草稿与 💡 术语解析钉在面板底部, 不参与滚动
        show_draft = (not self._streaming) and bool(self._draft)
        self._set_cached("draft", self._draft_lbl,
                         f"▸ {self._draft}" if show_draft else "")
        self._set_cached("draft_zh", self._draft_zh,
                         self._draft_zh_val if (not self._streaming and self._draft_zh_val) else "")
        self._set_cached("gloss", self._gloss_lbl, self._gloss_text())
        # 不再每次渲染都 orderFrontRegardless(会高频打扰窗口服务)
