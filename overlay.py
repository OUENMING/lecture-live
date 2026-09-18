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

WEIGHT = 0.23                                     # NSFontWeightMedium
FLUSH_DT = 0.016                                  # 渲染合并闸门 = 一帧(约 60Hz)

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


def _make_panel(rect, style, backing, defer):
    """无边框 NonactivatingPanel。

    ⚠️ 必须 override **ObjC 名** `canBecomeKeyWindow`: 窗口无标题栏时基类返 False,
    AppKit 会据此放弃把它变成 key window —— 那样输入框永远拿不到键盘。
    Swift 名 `canBecomeKey` 无效(PyObjC 会把它注册进 runtime, 但 AppKit 从不调用)。
    `canBecomeMainWindow` 不用动: key 与 main 无关。
    类只定义一次(重复定义会报 override 错), 同 _ButtonTargetCls。
    """
    global _PanelCls
    from AppKit import NSPanel
    if _PanelCls is None:
        class _Panel(NSPanel):
            def canBecomeKeyWindow(self):           # noqa: N802
                return True

        _PanelCls = _Panel
    return _PanelCls.alloc().initWithContentRect_styleMask_backing_defer_(
        rect, style, backing, defer)


_InputDelegateCls = None


def _make_input_delegate(on_submit, on_cancel, on_focus=None, on_blur=None):
    """输入框委托: 回车/Esc 走 doCommandBySelector, 聚焦/失焦走 controlTextDid*Editing。

    ⚠️ 不用 target/action: 后者在 Tab 与失焦时也会触发(换个焦点 = 误提交)。
    收到的 selectors 是运行时字符串: `insertNewline:`(Return) / `cancelOperation:`(Esc)。
    PyObjC 把 `control:textView:doCommandBySelector:` 映射成下划线形式的方法名。
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

            def controlTextDidBeginEditing_(self, note):        # noqa: N802
                cb = getattr(self, "_on_focus", None)
                if cb:
                    cb()

            def controlTextDidEndEditing_(self, note):          # noqa: N802
                cb = getattr(self, "_on_blur", None)
                if cb:
                    cb()

        _InputDelegateCls = _InputDelegate
    d = _InputDelegateCls.alloc().init()
    d._on_submit = on_submit
    d._on_cancel = on_cancel
    d._on_focus = on_focus
    d._on_blur = on_blur
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


class Overlay:
    def __init__(self, on_quit=None, on_flag=None, on_translate=None, on_submit=None):
        from AppKit import (NSWindow, NSPanel, NSMakeRect, NSColor, NSTextField,
                            NSVisualEffectView, NSVisualEffectMaterialHUDWindow,
                            NSVisualEffectStateActive, NSWindowStyleMaskBorderless,
                            NSWindowStyleMaskNonactivatingPanel, NSBackingStoreBuffered,
                            NSTextAlignmentLeft, NSFont, NSLineBreakByWordWrapping,
                            NSLineBreakByTruncatingTail, NSView,
                            NSFloatingWindowLevel, NSWindowCollectionBehaviorCanJoinAllSpaces,
                            NSFocusRingTypeNone)

        self._width = WIDTH
        self._height = HEIGHT
        self._on_quit = on_quit or (lambda: None)
        self._on_flag = on_flag or (lambda: None)
        self._on_translate = on_translate or (lambda on: None)
        self._on_submit = on_submit or (lambda q: None)
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

        style = NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel
        self._panel = _make_panel(
            NSMakeRect(0, 0, self._width, self._height), style,
            NSBackingStoreBuffered, False)
        self._panel.setLevel_(NSFloatingWindowLevel)
        self._panel.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces)
        self._panel.setOpaque_(False)
        self._panel.setBackgroundColor_(NSColor.clearColor())
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

        self._NSFont, self._NSTF = NSFont, NSTextField
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
        self._scroll_h = VISIBLE_ROWS * ROW_H
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
            self._submit_input, self._cancel_input,
            self._on_input_focus, self._on_input_blur)
        self._targets.append(self._input_delegate)      # 保持强引用, 防 GC
        self._input.setDelegate_(self._input_delegate)
        ve.addSubview_(self._input)
        # 细线: 1px NSView, 颜色随焦点切换(见 _on_input_focus / _on_input_blur)。
        # 用与 scrim 同一个 pattern(setWantsLayer_ + layer().setBackgroundColor_)。
        self._input_rule = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 0, 0))
        self._input_rule.setWantsLayer_(True)
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
        self._translating = True
        self._engine_warn = False        # 云端翻译降级中(菜单栏图标提示用)
        self._btn_trans = self._button(
            "译 开", self._toggle_translate,
            "开启/关闭中文翻译。关闭后只出英文 —— 英文仍做上下文矫正(ASR 错听照修)，"
            "只是不显示中文，也不再查中文术语解析")
        self._btn_flag = self._button(
            "⭐", self._flag, "标记当前句为重点(写入 Obsidian 时加 ⭐ Exam Focus)")
        self._btn_close = self._button("✕", self._quit, "退出")
        # 展开/收回: 展开时显示更长的历史(固定占屏高 60%), 收回回到 3 句
        self._btn_expand = self._button(
            "▾ 展开", self._toggle_mode, "展开/收回更多历史")
        # "回到最新": 只在用户翻到上面去了以后出现。它同时是滚轮若投递失败时的
        # 保底导航(按钮已被实测证明可点)。
        self._btn_latest = self._button(
            "↓ 最新", self._tv.scroll_to_bottom, "回到最新一句")
        self._btn_latest.setHidden_(True)
        # 顶栏按钮按**右对齐**摆放(列表为左->右顺序), 实际 x/宽度在 _layout() 现算:
        # 宽度 = sizeToFit + 内边距(图标保底 28px 点击区)。旧代码硬编码 x, "展开"
        # 占 [W-170,W-108] 而"译 开"占 [W-130,W-74], **交叉 22px**, 渲染出来糊成
        # 一团("展开译 开")。自适应宽度后中英/展开收回换字都不会重叠。
        self._bar = [self._btn_latest, self._btn_expand,
                     self._btn_trans, self._btn_flag, self._btn_close]
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
        不是 `firstResponder() is self._input`。"""
        try:
            return self._input.currentEditor() is not None
        except Exception:                     # noqa: BLE001
            return False

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

    def _release_focus(self):
        """主动把 key window 交还出去。

        ⚠️ 为什么每次交互都要调: override canBecomeKeyWindow=True 之后, AppKit
        不再查询 needsPanelToBecomeKey —— 点面板任何地方(包括顶栏按钮)都可能让
        面板成为 key window, 于是**用户在自己 app 里按的键会被面板吃掉**
        (改之前面板永远不会 key, 所以这条回归是本次改动引入的)。
        "用户没在打字"的一切交互结束后都必须退让。
        """
        try:
            self._panel.makeFirstResponder_(None)
            self._panel.resignKeyWindow()
        except Exception:                     # noqa: BLE001
            pass

    def _submit_input(self):
        """回车: 取文字 -> 清空 -> 交还焦点 -> 交出去。空输入只清空。"""
        text = (self._input.stringValue() or "").strip()
        self._input.setStringValue_("")
        self._release_focus()
        if text:
            self._on_submit(text)

    def _cancel_input(self):
        """Esc: 清空 + 交还焦点。"""
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
        scr = self._panel.screen() or NSScreen.mainScreen()
        vis_h = scr.visibleFrame().size.height if scr else 900.0
        total = min(0.60 * vis_h, vis_h - 40)
        return max(2 * ROW_H,
                   total - BOTTOM_PAD - _pinned(GLOSS_H_BIG) - 8 - HEADER_H)

    def _toggle_mode(self):
        self._apply_mode(not self._collapsed)

    def _apply_mode(self, collapsed: bool):
        """切换展开/收回。面板原点在左下、向上长高, 所以**顶边保持不动**,
        否则顶栏按钮会跟着跳。"""
        self._collapsed = collapsed
        # 展开高度现场重算, 不用 __init__ 里那个 —— 那时面板可能还没落到目标屏幕,
        # _expanded_scroll_h 会退化到 900px 兜底值, 复用就会算出错的总高。
        self._expanded_h = self._expanded_scroll_h()
        self._scroll_h = VISIBLE_ROWS * ROW_H if collapsed else self._expanded_h
        self._pinned = self._pinned_h()
        new_h = BOTTOM_PAD + self._pinned + 8 + HEADER_H + self._scroll_h
        self._height = new_h
        self._tv.set_collapsed(collapsed)
        self._btn_expand.setTitle_("▾ 展开" if collapsed else "▴ 收回")

        f = self._panel.frame()
        top = f.origin.y + f.size.height
        vis = self._panel.screen().visibleFrame()
        oy = top - new_h
        oy = max(vis.origin.y + 20,
                 min(oy, vis.origin.y + vis.size.height - new_h - 20))
        self._panel.setFrame_display_(
            ((f.origin.x, oy), (self._width, new_h)), True)
        self._layout()

    def _on_follow_change(self, follow: bool):
        """跟随状态变化 -> 显示/隐藏"↓ 最新"按钮。"""
        try:
            self._btn_latest.setHidden_(follow)
        except Exception:                     # noqa: BLE001
            pass

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

    def _sync_trans_button(self):
        """按钮文字直接写状态(不靠颜色/图标变暗 —— emoji 不吃 tint, 且弱显色看不清)。"""
        from AppKit import NSColor
        self._btn_trans.setTitle_("译 开" if self._translating else "译 关")
        try:                                  # 颜色只是锦上添花, 拿不到也不影响可用
            self._btn_trans.setContentTintColor_(
                NSColor.systemGreenColor() if self._translating
                else NSColor.secondaryLabelColor())
        except Exception:                     # noqa: BLE001
            pass

    def _toggle_translate(self):
        """切换翻译开关。关闭后主链路跳过 LLM, 只保留英文转录。"""
        self.set_translating(not self._translating)

    def set_translating(self, on: bool) -> None:
        self._translating = bool(on)
        if not self._translating:
            # 关掉翻译: 清掉可能在途的草稿译文/术语, 别让它们留在屏上误导
            self._draft_zh_val = ""
            self._terms = []
            self._pinned_term = None
        self._sync_trans_button()
        self._mark_dirty(urgent=True)
        self._on_translate(self._translating)

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
        self._tv.set_content(
            self._history, self._cur_en, self._cur_zh,
            bool(self._streaming or self._cur_zh or self._cur_en))
        # 草稿与 💡 术语解析钉在面板底部, 不参与滚动
        show_draft = (not self._streaming) and bool(self._draft)
        self._draft_lbl.setStringValue_(f"▸ {self._draft}" if show_draft else "")
        self._draft_zh.setStringValue_(
            self._draft_zh_val if (not self._streaming and self._draft_zh_val) else "")
        self._gloss_lbl.setStringValue_(self._gloss_text())
        # 不再每次渲染都 orderFrontRegardless(会高频打扰窗口服务)
