#!/usr/bin/env python3
"""「ClassLive 已更新到 X」的**非模态毛玻璃卡片**。

为什么不用 NSAlert —— 查过 Apple HIG，原话三条都踩中：

    "Use alerts sparingly… they **interrupt the current task**"
    "**Avoid using an alert merely to provide information.**"
    "Don't alert merely to convey information, **on load**…"

「更新后说明改了什么」正是**纯信息**、而且发生在**on load**。两头都不该用 alert。
（业界也没有一家用系统 alert 做 What's New：Raycast/Obsidian 走网页 changelog，
VS Code 走应用内页面。）

而且本项目还有一条比 HIG 更硬的约束：**这是上课录课用的工具**。
一个 on-load 的模态框在别的 app 里只是烦，在这里是**直接卡住主任务**。
所以这里做成**非模态**：它和 ClassLive 面板一起浮出来，你不理它也照样开始转录。

顺带解决两个"看起来廉价"的实测根因（都在 `_show_whats_new` 的 NSAlert 那条路上）：
  ① NSAlert 默认用 `NSApplicationIcon`，未打包脚本回落到**宿主解释器的图标**
     → 弹出来是 Python 火箭；
  ② 那时 app 的 activation policy 还是 `Regular`（实测）→ Dock 里会冒出 Python 图标。
自绘卡片两个问题都不存在。

⚠️ 与上课绝不冲突的三条：
  · **非模态** —— 不 runModal、不阻塞主循环；窗口关了只是少一张卡
  · **不抢焦点** —— orderFrontRegardless()，从不 activate（`NonactivatingPanel`）
  · 任何构造失败都静默 —— 它只是说明，不能因为它让课起不来
"""
from __future__ import annotations

import os
import pathlib
import re

# 复用 overlay.py 已经调好的那套（材质、圆角、发灰补偿都实测过），不另起一套
WIDTH = 620.0
PAD = 20.0
TITLE_H = 30.0
LINE_H = 21.0
LOG_H = 360.0          # 可滚动日志区的高度（有 log 时用它，没 log 时按摘要行数算）
FOOT_H = 68.0          # 底部两行：状态行 + (勾选框｜立即更新｜知道了)
CORNER = 16.0
SCRIM_ALPHA = 0.38     # 与 overlay.SCRIM_ALPHA 同值：白底 PPT 上也要能读

_W = 0.23              # NSFontWeightMedium，同 overlay.WEIGHT
_STRONG = 0.55         # NSFontWeightSemibold，日志里的小标题用

_Cls = None


def _classes():
    """延迟定义两个 ObjC 子类（⚠️ 只能定义一次，重复会报 override 错 —— overlay 踩过）。"""
    global _Cls
    if _Cls is None:
        import objc
        from AppKit import NSPanel, NSView

        # ⚠️⚠️ 类名**必须全局唯一**：ObjC 运行时按名字注册类，重名会抛
        # `_Panel is overriding existing Objective-C class`。overlay.py 里已经有
        # `_Panel` / `_DragLayer`，所以这里绝不能照抄那两个名字 —— 踩过：
        # build() 的 fail-soft except 把这条吞了，卡片**静默**变 None，
        # 独立测试却全绿（那时 overlay 没加载）。
        class _CLWhatPanel(NSPanel):
            # 非模态卡片：能成为 key（按钮可点），但不用它来激活 app
            def canBecomeKeyWindow(self):            # noqa: N802
                return True

            def canBecomeMainWindow(self):           # noqa: N802
                return False

            def sendEvent_(self, event):             # noqa: N802
                """点击时先激活 app —— 与 `overlay._Panel.sendEvent_` 同款处理。

                理由（overlay 那边实测出来的）：面板是 `NonactivatingPanel` 且只用
                `orderFrontRegardless()` 显示，app 从不激活；此时 AppKit 会把第一次
                点击**先用于激活窗口**、不送给控件（NSButton 的 `acceptsFirstMouse`
                默认 False）—— 表现为"按钮点了没反应，也不报错"。

                ⚠️ **2026-09-26 留下这段的经过，别当它是实测结论**：
                当时是拿一个自制脚手架测出"三个按钮一个都点不动"就下了判断，
                后来发现**脚手架本身是坏的** —— `AppHelper.runConsoleEventLoop()`
                只转 `NSRunLoop`，**从不调 `[NSApp run]`**，所以窗口服务器的事件
                根本没被取出（面板收到 **0** 个事件；换 `runEventLoop()` 后同一个
                卡片收到 76 个事件、按钮正常响应）。
                **所以"卡片按钮坏了"这个观察是假的。**
                这段保留，是因为它对齐 overlay 那套**已验证可用**的写法
                （真 app 里 app 不激活、面板不 key，与 overlay 面板处境相同），
                不是因为被实测证明必需。
                """
                if event.type() == 1:                # NSEventTypeLeftMouseDown
                    try:
                        from AppKit import NSApplication
                        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
                    except Exception:                # noqa: BLE001
                        pass
                objc.super(_CLWhatPanel, self).sendEvent_(event)

        class _CLWhatDrag(NSView):
            """整块背景可拖（和 overlay 同一个做法：mouseDownCanMoveWindow 是总开关）。"""
            def mouseDownCanMoveWindow(self):        # noqa: N802
                return True

        _Cls = (_CLWhatPanel, _CLWhatDrag)
    return _Cls


def _log_attr(text: str):
    """把 CHANGELOG 的 Markdown 文本转成带样式的 NSAttributedString。

    只认四档（一级标题 / 版本标题 / 小节标题 / 正文与列表）—— 不为一个更新卡片
    引一套完整 Markdown 解析器。`**` / 反引号直接去掉：卡片是纯文本观感，
    留着会显示成字面的星号（踩过）。
    """
    from AppKit import (NSAttributedString, NSMutableAttributedString, NSColor,
                        NSFont, NSMutableParagraphStyle, NSFontAttributeName,
                        NSForegroundColorAttributeName, NSParagraphStyleAttributeName)

    def para(before: float) -> NSMutableParagraphStyle:
        st = NSMutableParagraphStyle.alloc().init()
        st.setParagraphSpacingBefore_(before)
        return st

    # ⚠️ 必须用 **Mutable** —— 不可变的 NSAttributedString 没有 append 方法，
    # 调 `attributedStringByAppendingAttributedString_` 会 AttributeError（踩过）。
    out = NSMutableAttributedString.alloc().init()
    for raw in (text or "").splitlines():
        s = raw.rstrip()
        # ⚠️ 代码围栏整行丢掉。不丢的话，下面那句 `.replace("`", "")` 会把
        #    ```bash 剥成光秃秃一个 "bash" 印在卡片上（收尾的 ``` 则变成空行）——
        #    实测 3.6.5 的卡片就是这样，升级须知里多出一行孤零零的 "bash"。
        #    引用块里的围栏（`> ```bash`）同样要丢 —— 只判开头会漏掉它。
        _probe = s.lstrip()
        if _probe.startswith("> "):
            _probe = _probe[2:].lstrip()
        if _probe.startswith("```"):
            continue
        if s.startswith("## ["):
            size, w, alpha, before, body = 14.0, 0.45, 1.0, 16.0, s[3:]
        elif s.startswith("### "):
            size, w, alpha, before, body = 12.5, _STRONG, 0.98, 12.0, s[4:]
        elif s.startswith("# "):
            size, w, alpha, before, body = 15.0, 0.45, 1.0, 2.0, s[2:]
        elif s.startswith("> "):
            size, w, alpha, before, body = 12.0, 0.0, 0.72, 0.0, "      " + s[2:]
        elif s.startswith(("- ", "* ")):
            size, w, alpha, before, body = 12.0, 0.0, 0.88, 0.0, "   •  " + s[2:]
        else:
            size, w, alpha, before, body = 12.0, 0.0, 0.78, 0.0, s
        body = body.replace("**", "").replace("`", "")
        body = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", body)   # 链接只留文字
        one = NSAttributedString.alloc().initWithString_attributes_(
            body + "\n",
            {NSFontAttributeName: NSFont.systemFontOfSize_weight_(size, w),
             NSForegroundColorAttributeName: NSColor.whiteColor().colorWithAlphaComponent_(alpha),
             NSParagraphStyleAttributeName: para(before)})
        out.appendAttributedString_(one)
    return out


def _add_log_view(parent, attr, x, y, w, h):
    """在卡片里放一个**可滚动**的日志区。滚动条按需出现（overlay 风格）。

    ⚠️ 四件套缺一不可，否则滚不动或不换行：
      `setVerticallyResizable_` + `setMaxSize_`（文本长了要长高）
      `textContainer().setWidthTracksTextView_`（宽度跟着视图走才会自动折行）
    """
    from AppKit import (NSMakeRect, NSScrollView, NSTextView, NSViewWidthSizable,
                        NSScrollerStyleOverlay)
    sc = NSScrollView.alloc().initWithFrame_(NSMakeRect(x, y, w, h))
    sc.setDrawsBackground_(False)
    sc.setBorderType_(0)                                  # NSNoBorder
    sc.setHasVerticalScroller_(True)
    sc.setAutohidesScrollers_(True)
    sc.setScrollerStyle_(NSScrollerStyleOverlay)
    sc.setHorizontalScrollElasticity_(0)

    tv = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, w, h))
    tv.setEditable_(False)
    tv.setSelectable_(True)                               # 允许选中复制
    tv.setDrawsBackground_(False)
    tv.setRichText_(True)
    tv.setTextContainerInset_((10.0, 6.0))
    tv.setVerticallyResizable_(True)
    tv.setHorizontallyResizable_(False)
    tv.setAutoresizingMask_(NSViewWidthSizable)
    tv.setMinSize_((0.0, h))
    tv.setMaxSize_((w, 1e7))
    tv.textContainer().setWidthTracksTextView_(True)
    tv.textContainer().setContainerSize_((w, 1e7))
    # ⚠️ NSTextView **没有** `setAttributedString_` —— 要走 textStorage（踩过）。
    tv.textStorage().setAttributedString_(attr)
    # ⚠️ 量高度这两个坑都踩过：
    #   · `sizeToFit()` 在容器宽度定稿前算 -> 算**高**（滚到底有几百像素空白）；
    #   · 直接量 `usedRect` 在视图还很矮时 -> 懒排版只排了可视部分 -> 算**矮**
    #     （结果滚不到底，末尾内容永远看不到）。
    # 正确做法：先把视图撑到很大迫使全量排版，再量，最后设成实测值。
    lm = tv.layoutManager()
    tc = tv.textContainer()
    tv.setFrame_(NSMakeRect(0, 0, w, 1e6))
    lm.ensureLayoutForTextContainer_(tc)
    used = lm.usedRectForTextContainer_(tc).size.height
    tv.setFrame_(NSMakeRect(0, 0, w, max(used + 16.0, h)))
    sc.setDocumentView_(tv)
    parent.addSubview_(sc)
    return sc


def build(version: str, summary: str, date: str = "", log: str = "",
          flag_path: pathlib.Path | None = None, on_dismiss=None, on_update=None):
    """构造并显示卡片。失败返回 None（调用方不必管）。

    `log` 非空 -> 中间是**可滚动的完整更新日志**（所有版本，新的在上）；
    为空 -> 退回"摘要几行"的紧凑版。两条路都是非模态、不抢焦点。

    `on_update` 给了才会建「立即更新」按钮。它的签名是
    `on_update(set_status, set_title, done)` —— 由调用方在**后台线程**里跑，
    跑完用这三个回调把进度写回卡片（绝不在主线程做网络）。
    """
    try:
        from AppKit import (NSAppearance, NSAppearanceNameDarkAqua, NSButton,
                            NSButtonTypeSwitch, NSColor, NSFont, NSMakeRect,
                            NSFloatingWindowLevel, NSLineBreakByWordWrapping,
                            NSTextField, NSTextAlignmentLeft, NSVisualEffectMaterialHUDWindow,
                            NSVisualEffectStateActive, NSVisualEffectView, NSView,
                            NSWindowCollectionBehaviorCanJoinAllSpaces,
                            NSWindowStyleMaskBorderless, NSWindowStyleMaskNonactivatingPanel)
        Panel, DragLayer = _classes()

        # ⚠️ 摘要是从 CHANGELOG.md 里抠出来的 **Markdown**, 而这里只画纯文本 ——
        # 不处理就会出现字面的 `**上面**`(实测踩过)。
        summary_lines = [x.replace("**", "").replace("`", "")
                         for x in (summary or "").splitlines() if x.strip()]
        if not log and not summary_lines:
            summary_lines = ["完整说明见仓库里的 CHANGELOG.md"]
        body_h = LOG_H if log else len(summary_lines) * LINE_H + 10
        h = PAD + TITLE_H + body_h + 12 + FOOT_H + PAD

        style = NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel
        p = Panel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, WIDTH, h), style, 2, False)
        # ⚠️ 必须显式 DarkAqua —— overlay 那边像素级实测过：系统浅色时 `.hudWindow`
        # 会被渲染成灰（面板中心亮度 0.314 → 强制深色后 0.113）。这里同款材质，同款毛病。
        p.setAppearance_(NSAppearance.appearanceNamed_(NSAppearanceNameDarkAqua))
        p.setLevel_(NSFloatingWindowLevel)
        p.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces)
        p.setOpaque_(False)
        p.setBackgroundColor_(NSColor.clearColor())
        p.setMovableByWindowBackground_(True)
        p.setHasShadow_(True)

        content = p.contentView()
        ve = NSVisualEffectView.alloc().initWithFrame_(content.bounds())
        ve.setMaterial_(NSVisualEffectMaterialHUDWindow)
        ve.setState_(NSVisualEffectStateActive)
        ve.setWantsLayer_(True)
        ve.layer().setCornerRadius_(CORNER)
        ve.layer().setMasksToBounds_(True)
        content.addSubview_(ve)

        scrim = NSView.alloc().initWithFrame_(ve.bounds())
        scrim.setWantsLayer_(True)
        scrim.layer().setBackgroundColor_(
            NSColor.blackColor().colorWithAlphaComponent_(SCRIM_ALPHA).CGColor())
        ve.addSubview_(scrim)

        drag = DragLayer.alloc().initWithFrame_(ve.bounds())
        ve.addSubview_(drag)

        def label(text, y, size, alpha=1.0, bold=False, wrap=False):
            lb = NSTextField.alloc().initWithFrame_(
                NSMakeRect(PAD, y, WIDTH - 2 * PAD, LINE_H))
            lb.setEditable_(False); lb.setSelectable_(False)
            lb.setBezeled_(False); lb.setDrawsBackground_(False)
            lb.setFont_(NSFont.systemFontOfSize_weight_(size, _W if not bold else 0.4))
            lb.setTextColor_(NSColor.whiteColor().colorWithAlphaComponent_(alpha))
            lb.setStringValue_(text)
            lb.setAlignment_(NSTextAlignmentLeft)
            lb.setLineBreakMode_(NSLineBreakByWordWrapping)
            if wrap:
                lb.setMaximumNumberOfLines_(0)
            return lb

        # 从顶部往下摆（这版窗口坐标是**不翻转**的，y=0 在底部，所以从 h 往下减）
        y = h - PAD - TITLE_H
        _title = f"ClassLive 已更新到 {version}" + (f" · {date}" if date else "")
        ve.addSubview_(label(_title, y, 17.0, bold=True))

        if log:
            _add_log_view(ve, _log_attr(log), PAD, FOOT_H + PAD,
                          WIDTH - 2 * PAD, LOG_H)
        else:
            for ln in summary_lines:
                y -= LINE_H
                ve.addSubview_(label(ln, y, 12.5, alpha=0.92))

        # ---- 底部：状态行 + 勾选框 + 按钮 ----
        # ⚠️ 为什么要单独一行状态：卡片原本只在 `VERSION` 变了时弹（= 已经拉完了），
        # 那一刻没什么可拉的 —— 「立即更新」按钮会显得没意义。加一行常驻状态
        # （当前版本 / 是否落后 / 更新结果），按钮才有上下文。
        fy = PAD
        state_holder = {"skip": False}

        def on_close(_=None):
            try:
                if state_holder["skip"] and flag_path:
                    # ⚠️ 写**版本号**而不是 "1"：跳过只对**这个版本**生效，
                    # 出了新版本要重新弹（作者 2026-09-25 定的节奏）。
                    # 写 "1" 会让它变成**永久关闭**，而且没有恢复入口 ——
                    # VS Code 就是栽在这上面（issue #109912：误点后只能删掉整个
                    # workspace 或全局状态，维护者 closed as out-of-scope，
                    # 社区被迫去教改 state.vscdb 数据库）。
                    flag_path.write_text(version, encoding="utf-8")
            except Exception:                             # noqa: BLE001
                pass
            try:
                p.orderOut_(None)
            except Exception:                             # noqa: BLE001
                pass
            if on_dismiss:
                try:
                    on_dismiss()
                except Exception:                         # noqa: BLE001
                    pass

        # 状态行（常驻）。内容由调用方通过 set_status 改；默认显示当前版本。
        # ⚠️ y 取 fy+34 而不是贴 footer 顶 —— 贴顶会让它顶到日志区最后一行（实测差 3px）。
        status = label(f"当前 {version}", fy + 34.0, 11.5, alpha=0.75)

        def set_status(text: str, alpha: float = 0.75) -> None:
            try:
                status.setStringValue_(text)
                status.setTextColor_(
                    NSColor.whiteColor().colorWithAlphaComponent_(alpha))
            except Exception:                             # noqa: BLE001
                pass

        chk = NSButton.alloc().initWithFrame_(NSMakeRect(PAD, fy + 4, 190.0, 22.0))
        chk.setButtonType_(NSButtonTypeSwitch)
        chk.setTitle_("本版本不再提示")
        chk.setFont_(NSFont.systemFontOfSize_(12.0))
        try:
            chk.setContentTintColor_(NSColor.whiteColor().colorWithAlphaComponent_(0.85))
        except Exception:                                 # noqa: BLE001
            pass

        def toggle(_=None):
            state_holder["skip"] = bool(chk.state())

        btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(WIDTH - PAD - 92.0, fy + 2, 92.0, 28.0))
        btn.setTitle_("知道了")
        btn.setBezelStyle_(1)                             # rounded
        btn.setFont_(NSFont.systemFontOfSize_weight_(13.0, _W))

        # 「立即更新」—— 只在给了 on_update 时才建。点了之后**在后台线程**跑，
        # 结果用 set_status / set_update_title 回写到卡片上，绝不阻塞主线程。
        ubtn = None
        if on_update:
            ubtn = NSButton.alloc().initWithFrame_(
                NSMakeRect(WIDTH - PAD - 92.0 - 8.0 - 104.0, fy + 2, 104.0, 28.0))
            ubtn.setTitle_("立即更新")
            ubtn.setBezelStyle_(1)
            ubtn.setFont_(NSFont.systemFontOfSize_weight_(12.5, _W))

            def on_update_clicked(_=None):
                try:
                    if ubtn is not None:
                        ubtn.setEnabled_(False)
                        ubtn.setTitle_("更新中…")
                    set_status("正在检查远程…", 0.75)
                    on_update(set_status, lambda t: ubtn and ubtn.setTitle_(t),
                              lambda: ubtn and ubtn.setEnabled_(True))
                except Exception:                         # noqa: BLE001
                    if os.environ.get("CLASSLIVE_DEBUG"):
                        import traceback
                        traceback.print_exc()
                    set_status("更新按钮出错", 1.0)

        _wire = _make_target(toggle)
        _wire2 = _make_target(on_close)
        chk.setTarget_(_wire); chk.setAction_("clicked:")
        btn.setTarget_(_wire2); btn.setAction_("clicked:")
        ve.addSubview_(chk); ve.addSubview_(btn)
        ve.addSubview_(status)
        targets = [_wire, _wire2]
        if ubtn is not None:
            _wire3 = _make_target(on_update_clicked)
            ubtn.setTarget_(_wire3); ubtn.setAction_("clicked:")
            ve.addSubview_(ubtn)
            targets.append(_wire3)

        p.orderFrontRegardless()      # ⚠️ 不是 makeKeyAndOrderFront —— 绝不抢焦点
        return {"panel": p, "close": on_close, "set_status": set_status,
                "set_update_title": (lambda t: ubtn and ubtn.setTitle_(t)),
                "set_update_enabled": (lambda on: ubtn and ubtn.setEnabled_(on)),
                "has_update_button": ubtn is not None,
                "_targets": targets}
    except Exception:                                     # noqa: BLE001
        # fail-soft 是本模块的**既定设计**（见文件头：它只是说明，不能因为它让课起不来）。
        # 但它已经**两次**把真 bug 藏起来（ObjC 类名撞车、不可变的 NSAttributedString），
        # 排查成本很高 —— 所以留一个显式开关，`CLASSLIVE_DEBUG=1` 就能看到真实异常。
        if os.environ.get("CLASSLIVE_DEBUG"):
            import traceback
            traceback.print_exc()
        return None


_TargetCls = None


def _make_target(cb):
    """包一个 ObjC target 对象。⚠️ 与 overlay 一样: 必须由调用方持引用，否则被 GC。"""
    global _TargetCls
    from AppKit import NSObject
    if _TargetCls is None:
        class _T(NSObject):
            def clicked_(self, sender):                   # noqa: N802
                f = getattr(self, "_cb", None)
                if f:
                    f()
        _TargetCls = _T
    t = _TargetCls.alloc().init()
    t._cb = cb
    return t


def seen_flag_path() -> pathlib.Path:
    return pathlib.Path(__file__).with_name(".update-seen")


def skip_flag_path() -> pathlib.Path:
    """勾了「本版本不再提示」就写这个文件，**内容是版本号**。

    ⚠️ 存版本号而不是 `1`：跳过只对**该版本**生效，新版本会重新弹。
    判定在 `main._whatsnew_payload`（它取 `max(.update-seen, .update-skip)`）。
    """
    return pathlib.Path(__file__).with_name(".update-skip")
