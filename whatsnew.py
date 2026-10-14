#!/usr/bin/env python3
"""开课面板上的**非模态毛玻璃小卡**：「有新版本」/「已更新到 X」/「草稿模型还没装」共用一个画法。

2026-10-14 作者定的样子（照 cc-switch 的「近期更新」）：每个版本一个小标题，每条看点前一个标签
（新增 / 改进 / 修复），简短；有新版本时卡底就是「立即更新」。原来上课悬浮窗旁边那张
「已更新到 X」的长日志卡（可滚动的整份 CHANGELOG +「本版本不再提示」）**取消**，
它的事并到这里：开课前看一眼，比课上浮在字幕旁边合适。

为什么不用 NSAlert —— Apple HIG 原话：
    "Use alerts sparingly… they **interrupt the current task**"
    "**Avoid using an alert merely to provide information.**"
「说明改了什么」是纯信息、发生在 on load，两头都不该用 alert；这又是上课用的工具，
模态框会直接卡住主任务。所以：
  · **非模态** —— 不 runModal；不理它照样能开课（开课时面板会把它收掉）
  · **不抢焦点** —— orderFrontRegardless()，从不 activate（`NonactivatingPanel`）
  · 构造失败静默返回 None —— 它只是说明，不能因为它让课起不来（撞 ObjC 类名例外，见 `build`）
"""
from __future__ import annotations

import os
import pathlib

import panel
import objc_own

WIDTH = 460.0
PAD = 20.0
BODY_MAX_H = 300.0     # 列表区最高这么多，再长就在里面滚（一下跳好几版时）
_W = 0.23              # NSFontWeightMedium，同 overlay.WEIGHT
_SEMI = 0.4

#: 标签 → (R, G, B)。暗色毛玻璃上用系统色的暗色模式值（亮、不刺眼）；底 = 同色 18% 透明。
TAG_RGB = {"新增": (0.19, 0.82, 0.35),     # systemGreen (dark)
           "改进": (0.04, 0.52, 1.00),     # systemBlue (dark)
           "修复": (1.00, 0.62, 0.04)}     # systemOrange (dark)
ITEM_SIZE = 12.5
CHIP_SIZE = 10.5
CHIP_H = 17.0
CHIP_W = 34.0          # 两个汉字 + 两侧各 6pt；三种标签同宽，文字左边对齐成一条线
ITEM_GAP = 7.0
VER_GAP = 14.0


def updated_from(seen: str, cur: str, vers: list[str]) -> str:
    """该不该弹「已更新到 cur」：该 → 返回起点版本（报 `(起点, cur]`）；不该 → 空串。纯函数。

    · 没有 `seen`（全新安装）、`seen` 不在 CHANGELOG 里（手改过 / 老格式）→ 不弹
    · `cur` 不比 `seen` 新（同版、降级）→ 不弹 —— 比大小不是比不等（降级不该弹）
    每个版本只弹一次：调用方弹完就把 `.update-seen` 写成 `cur`。
    """
    import changelog
    if not seen or seen not in vers or cur not in vers:
        return ""
    return seen if changelog.ver_key(cur) > changelog.ver_key(seen) else ""


def _rgb(tag: str, a: float = 1.0):
    from AppKit import NSColor
    r, g, b = TAG_RGB.get(tag, (1.0, 1.0, 1.0))
    return NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, a)


def _label(text: str, size: float, *, alpha=1.0, weight=_W, color=None, wrap=False):
    from AppKit import (NSColor, NSFont, NSLineBreakByTruncatingTail,
                        NSLineBreakByWordWrapping, NSMakeRect, NSTextField)
    lb = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 10, 10))
    lb.setEditable_(False); lb.setSelectable_(False)
    lb.setBezeled_(False); lb.setDrawsBackground_(False)
    lb.setFont_(NSFont.systemFontOfSize_weight_(size, weight))
    lb.setTextColor_(color or NSColor.whiteColor().colorWithAlphaComponent_(alpha))
    lb.setStringValue_(text)
    if wrap:
        lb.cell().setWraps_(True)
        lb.cell().setLineBreakMode_(NSLineBreakByWordWrapping)
        lb.setMaximumNumberOfLines_(0)
    else:
        lb.cell().setLineBreakMode_(NSLineBreakByTruncatingTail)
    return lb


def _fit_h(lb, w: float) -> float:
    from AppKit import NSMakeRect
    return float(lb.cell().cellSizeForBounds_(NSMakeRect(0, 0, w, 1e5)).height)


def _chip(tag: str):
    """标签小胶囊：同色淡底 + 同色字，圆角 4（比按钮的胶囊收敛，读作「标注」而不是「可点」）。

    ⚠️ 底子与字分两层：NSTextField 的字贴着自己的顶画，直接给它上底色字会偏上（拍图量过）——
       底子是一块 layer 视图，字 `sizeToFit` 后在里面竖直居中。
    """
    from AppKit import NSMakeRect, NSView
    box = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, CHIP_W, CHIP_H))
    box.setWantsLayer_(True)
    box.layer().setCornerRadius_(4.0)
    box.layer().setBackgroundColor_(_rgb(tag, 0.18).CGColor())
    lb = _label(tag, CHIP_SIZE, weight=_SEMI, color=_rgb(tag))
    lb.sizeToFit()
    f = lb.frame().size
    lb.setFrame_(NSMakeRect((CHIP_W - f.width) / 2.0, (CHIP_H - f.height) / 2.0, f.width, f.height))
    box.addSubview_(lb)
    return box


def _rows(groups: list[dict], w: float) -> tuple[list, float]:
    """列表区的子视图与总高。坐标**自顶向下**（放进翻转的文档视图）。"""
    from AppKit import NSMakeRect
    views, y = [], 0.0
    for gi, g in enumerate(groups):
        if gi:
            y += VER_GAP
        v = _label(g["version"], 13.0, weight=_SEMI)
        v.sizeToFit()
        vw = v.frame().size.width + 2.0
        v.setFrame_(NSMakeRect(0, y, vw, 18.0))
        views.append(v)
        if g.get("date"):
            d = _label(g["date"], 11.0, alpha=0.5)
            d.setFrame_(NSMakeRect(vw + 6.0, y + 2.0, 120.0, 15.0))
            views.append(d)
        y += 18.0 + 6.0
        for tag, text in g["items"]:
            tx = CHIP_W + 8.0 if tag else 12.0
            lb = _label(text, ITEM_SIZE, alpha=0.9, wrap=True)
            h = _fit_h(lb, w - tx)
            lb.setFrame_(NSMakeRect(tx, y, w - tx, h))
            if tag:
                c = _chip(tag)
                c.setFrame_(NSMakeRect(0, y, CHIP_W, CHIP_H))
                views.append(c)
            else:
                dot = _label("·", ITEM_SIZE, alpha=0.5)
                dot.setFrame_(NSMakeRect(2.0, y, 10.0, h))
                views.append(dot)
            views.append(lb)
            y += h + ITEM_GAP
    return views, max(0.0, y - ITEM_GAP)


def _doc_class():
    from AppKit import NSView
    return objc_own.own("WhatsnewDoc", NSView, {"isFlipped": lambda self: True})


def build(title: str, subtitle: str = "", groups: list[dict] | None = None, *,
          body: str = "", note: str = "", buttons: list[tuple] = (), over=None):
    """构造并显示卡片。失败返回 None（调用方不必管）。

    `groups`：`changelog.groups()` 的产物（版本小标题 + 带标签的条目）；`body`：没有列表时的一段话。
    `buttons`：`[(标题, 回调, "primary"|"secondary"), …]`，从左到右摆、整体靠右；点哪颗都先关卡再回调。
    `over`：叠在哪个窗口上方居中（开课面板）；`None` 放屏幕中上。
    返回 `{"panel", "close", "_targets"}`（`_targets` 由调用方持住 —— target 是弱引用）。
    """
    try:
        from AppKit import (NSButton, NSMakeRect, NSWindowStyleMaskBorderless,
                            NSWindowStyleMaskNonactivatingPanel)
        inner = WIDTH - 2 * PAD
        views, list_h = _rows(groups or [], inner) if groups else ([], 0.0)
        body_lb = _label(body, ITEM_SIZE, alpha=0.9, wrap=True) if body else None
        note_lb = _label(note, 11.0, alpha=0.6, wrap=True) if note else None
        body_h = _fit_h(body_lb, inner) if body_lb else 0.0
        note_h = _fit_h(note_lb, inner) if note_lb else 0.0
        well_h = min(list_h, BODY_MAX_H) + (2 * 12.0 if views else 0.0)

        h = (PAD + 24.0 + (18.0 if subtitle else 0.0) + 14.0
             + (well_h + 12.0 if views else 0.0)
             + (body_h + 12.0 if body_lb else 0.0)
             + (note_h + 14.0 if note_lb else 0.0)
             + (28.0 if buttons else 0.0) + PAD)
        fp = panel.build(NSMakeRect(0, 0, WIDTH, h),
                         NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel)
        p, ve = fp.window, fp.glass
        p.setHasShadow_(True)        # borderless 默认没阴影（Titled 的 overlay 才有）—— 不进配方

        y = h - PAD - 24.0           # 窗口坐标不翻转：从顶往下减
        t = _label(title, 17.0, weight=_SEMI)
        t.setFrame_(NSMakeRect(PAD, y, inner, 24.0))
        ve.addSubview_(t)
        if subtitle:
            y -= 18.0
            s = _label(subtitle, 11.0, alpha=0.6)
            s.setFrame_(NSMakeRect(PAD, y, inner, 16.0))
            ve.addSubview_(s)
        y -= 14.0

        if views:
            # 列表垫一块浅底（极淡白底 + 发丝边，与开课面板的课程卡同一套分量）：看得出哪块能滚
            from AppKit import NSColor, NSView
            y -= well_h
            well = NSView.alloc().initWithFrame_(NSMakeRect(PAD - 8.0, y, inner + 16.0, well_h))
            well.setWantsLayer_(True)
            _w = NSColor.whiteColor()
            well.layer().setCornerRadius_(10.0)
            well.layer().setBackgroundColor_(_w.colorWithAlphaComponent_(0.05).CGColor())
            well.layer().setBorderWidth_(0.5)
            well.layer().setBorderColor_(_w.colorWithAlphaComponent_(0.10).CGColor())
            ve.addSubview_(well)
            vis_h = well_h - 24.0
            doc = _doc_class().alloc().initWithFrame_(NSMakeRect(0, 0, inner, list_h))
            for v in views:
                doc.addSubview_(v)
            sc = panel.make_scroll_view(NSMakeRect(PAD, y + 12.0, inner, vis_h),
                                        has_vertical=list_h > vis_h)
            sc.setDocumentView_(doc)
            ve.addSubview_(sc)
            y -= 12.0
        if body_lb:
            y -= body_h
            body_lb.setFrame_(NSMakeRect(PAD, y, inner, body_h))
            ve.addSubview_(body_lb)
            y -= 12.0
        if note_lb:
            y -= note_h
            note_lb.setFrame_(NSMakeRect(PAD, y, inner, note_h))
            ve.addSubview_(note_lb)

        done = {"v": False}

        def _close():
            try:
                p.orderOut_(None)
            except Exception:                             # noqa: BLE001
                pass

        def _pick(fn):
            def go(_=None):
                if done["v"]:
                    return
                done["v"] = True
                _close()
                try:
                    fn()
                except Exception:                         # noqa: BLE001
                    if os.environ.get("CLASSLIVE_DEBUG"):
                        import traceback
                        traceback.print_exc()
            return go

        targets, x = [], WIDTH - PAD
        for text, fn, kind in reversed(list(buttons)):
            bw = max(80.0, 26.0 + 13.5 * len(text))
            x -= bw
            b = NSButton.alloc().initWithFrame_(NSMakeRect(x, PAD, bw, 28.0))
            (panel.style_primary if kind == "primary" else panel.style_secondary)(b, text)
            tg = panel.make_button_target(_pick(fn))
            b.setTarget_(tg); b.setAction_("clicked:")
            ve.addSubview_(b)
            targets.append(tg)
            x -= 10.0

        if over is not None:
            try:
                f = over.frame()
                p.setFrameOrigin_((f.origin.x + (f.size.width - WIDTH) / 2.0,
                                   f.origin.y + f.size.height - h - 120.0))
            except Exception:                             # noqa: BLE001
                pass
        p.orderFrontRegardless()      # ⚠️ 不是 makeKeyAndOrderFront —— 绝不抢焦点
        return {"panel": p, "close": _close, "_targets": targets, "height": h}
    except objc_own.ObjcNameCollision:
        # ⚠️⚠️ **这一类不吞**：撞 ObjC 类名是程序缺陷，不是偶发 —— 吞掉就回到 2026-09-26
        #    「卡片静默变 None、独立测试全绿」那次事故。上课起不来的代价小于静默失效。
        raise
    except Exception:                                     # noqa: BLE001
        # fail-soft 曾两次藏住真 bug（类名撞车、不可变的 NSAttributedString）→ 留个开关看真异常
        if os.environ.get("CLASSLIVE_DEBUG"):
            import traceback
            traceback.print_exc()
        return None


def seen_flag_path() -> pathlib.Path:
    """`.update-seen`：上次「已更新到 X」报到哪一版（内容是版本号）。"""
    return pathlib.Path(__file__).with_name(".update-seen")
