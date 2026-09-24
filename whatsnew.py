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

# 复用 overlay.py 已经调好的那套（材质、圆角、发灰补偿都实测过），不另起一套
WIDTH = 560.0
PAD = 20.0
TITLE_H = 30.0
LINE_H = 21.0
FOOT_H = 46.0          # 底部那行：勾选框 + 按钮
CORNER = 16.0
SCRIM_ALPHA = 0.38     # 与 overlay.SCRIM_ALPHA 同值：白底 PPT 上也要能读

_W = 0.23              # NSFontWeightMedium，同 overlay.WEIGHT

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

        class _CLWhatDrag(NSView):
            """整块背景可拖（和 overlay 同一个做法：mouseDownCanMoveWindow 是总开关）。"""
            def mouseDownCanMoveWindow(self):        # noqa: N802
                return True

        _Cls = (_CLWhatPanel, _CLWhatDrag)
    return _Cls


def build(version: str, body: str, flag_path: pathlib.Path | None = None,
          on_dismiss=None):
    """构造并显示卡片。失败返回 None（调用方不必管）。"""
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
        # 不处理就会出现字面的 `**上面**`(实测踩过)。只去 `**`, 不做真渲染:
        # 卡片是"扫一眼知道改了啥", 不值得为粗体引一套 attributed string。
        lines = [x.replace("**", "") for x in (body or "").splitlines() if x.strip()]
        if not lines:
            lines = ["完整说明见仓库里的 CHANGELOG.md"]
        lines.append("")
        lines.append("完整说明见 CHANGELOG.md")
        h = PAD + TITLE_H + len(lines) * LINE_H + 10 + FOOT_H + PAD

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
        ve.addSubview_(label(f"ClassLive 已更新到 {version}", y, 17.0, bold=True))

        for i, ln in enumerate(lines):
            y -= LINE_H
            ve.addSubview_(label(ln, y, 12.5, alpha=0.92 if ln else 0.0))

        # ---- 底部：不再提示 + 关闭 ----
        fy = PAD
        state_holder = {"skip": False}

        def on_close(_=None):
            try:
                if state_holder["skip"] and flag_path:
                    flag_path.write_text("1", encoding="utf-8")
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

        chk = NSButton.alloc().initWithFrame_(NSMakeRect(PAD, fy + 4, 190.0, 22.0))
        chk.setButtonType_(NSButtonTypeSwitch)
        chk.setTitle_("以后不再提示")
        chk.setFont_(NSFont.systemFontOfSize_(12.0))
        chk.setTarget_(chk); chk.setAction_("")           # 只当勾选框用，不需要动作
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
        _wire = _make_target(toggle); _wire2 = _make_target(on_close)
        chk.setTarget_(_wire); chk.setAction_("clicked:")
        btn.setTarget_(_wire2); btn.setAction_("clicked:")
        ve.addSubview_(chk); ve.addSubview_(btn)

        p.orderFrontRegardless()      # ⚠️ 不是 makeKeyAndOrderFront —— 绝不抢焦点
        return {"panel": p, "close": on_close, "_targets": [_wire, _wire2]}
    except Exception:                                     # noqa: BLE001
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
    """勾了「以后不再提示」就写这个文件 —— 之后永不再弹。"""
    return pathlib.Path(__file__).with_name(".update-skip")
