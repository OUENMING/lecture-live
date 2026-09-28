#!/usr/bin/env python3
"""收尾卡 —— 「存不存 / 进度 / 结果」全在 UI 里，终端只留后路。

## 它解决的问题

2026-09-28：一节真实 tut（579 句）跑完后进程挂了 4 小时 12 分没退。根因是收尾
（问是否存笔记 + 精修 11 分钟）**同步跑在 AppKit 主线程上** —— 界面冻死、退不掉，
而 `cl` 从终端起的是普通进程、activation policy 又是 `.accessory`，
**不出现在「强制退出」窗口里**，作者连找都找不到。

这个卡把那一段搬到台前：窗口全程留着（✕ 就是退出口），问话和进度都在这儿。

`[官方]` Mac App Programming Guide「Don't Block the Main Thread」逐字：
> **In particular, never use the main thread to perform long-running or potentially
> unbounded tasks, such as tasks that require network access.**

## ⚠️ 四条设计约束（都是实测/回源来的）

1. **非模态**。`notice.alert` 那种 `runModal` 与「收尾期间窗口要一直可操作」直接冲突，
   而且 `notice._can_alert()` 的方向存疑（见计划 §6.5），不用它。
2. **非激活面板**（`NSWindowStyleMaskNonactivatingPanel`）—— 上课的工具不该抢焦点。
3. ⚠️ **按钮 target 必须持住**（`panel.make_button_target` 的返回值）——
   `setTarget_` 是弱引用，被 GC 掉就是「点了没反应，且不报错」。
4. ⚠️ **卡片高度按实测文字高度算**，不按"最多几行"预留 —— 预留会在状态行短的时候
   在中间留一条空档。用 `overlay` 同款的 AppKit 排版引擎量（`measure_text_h`），
   折行判据才与标签渲染**同源**。
"""
from __future__ import annotations

import panel

WIDTH = 380.0
PAD = 18.0
TITLE_H = 24.0
HINT_H = 17.0
BTN_H = 26.0
BTN_W = 100.0
BTN_GAP = 10.0
GAP_TITLE = 8.0
GAP_HINT = 12.0
GAP_BTN = 14.0

STATUS_FONT_SZ = 12.5
TITLE_FONT_SZ = 16.0
HINT_FONT_SZ = 11.0
BTN_FONT_SZ = 12.0


def measure_text_h(text: str, width: float, font) -> float:
    """文本按给定宽度折行后的**实测**高度。

    ⚠️ 用 AppKit 自己的排版引擎量，不猜字符宽度 —— 折行判据必须与标签渲染时的
       换行规则**同源**，否则会出现「我们以为放得下、标签却静默裁掉第 N 行」
       （`NSTextField` 超过 `maximumNumberOfLines` 既不省略也不报错）。
    ⚠️ 与 `overlay._measure_text_h` 是同一段实现。没合并是因为 `overlay` 那份
       在动画路径上被每帧调用，搬动它的收益不抵风险；**行为必须保持一致**。
    """
    from AppKit import (NSAttributedString, NSFontAttributeName, NSMakeSize,
                        NSStringDrawingUsesLineFragmentOrigin)
    a = NSAttributedString.alloc().initWithString_attributes_(
        text, {NSFontAttributeName: font})
    return a.boundingRectWithSize_options_(
        NSMakeSize(width, 1e7), NSStringDrawingUsesLineFragmentOrigin).size.height


def build(*, title: str = "收尾", on_close=None):
    """构造并显示收尾卡。**失败返回 `None`** —— 收尾绝不能因为卡起不来就跑不下去。

    返回的 dict：
      · `panel`   —— NSWindow（调用方用来 `place_beside`）
      · `close()` —— order out，幂等
      · `set_status(text, alpha=0.92)` —— 会**按实测高度重排**（顶边不动，往下长）
      · `set_hint(text, alpha=0.55)`
      · `set_buttons([(标题, 回调), …])` —— 换一批按钮，旧的直接摘掉
    """
    try:
        from AppKit import (NSButton, NSColor, NSFont, NSMakeRect, NSTextField,
                            NSTextAlignmentLeft, NSLineBreakByWordWrapping,
                            NSWindowStyleMaskBorderless,
                            NSWindowStyleMaskNonactivatingPanel)

        fp = panel.build(
            NSMakeRect(0, 0, WIDTH, 200.0),
            NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel)
        p, ve = fp.window, fp.glass
        # ⚠️ borderless 面板**没有**默认阴影（overlay 那个 Titled 才有）——
        #    与 whatsnew 同一条，不进 panel.py 的配方。
        p.setHasShadow_(True)

        f_title = NSFont.systemFontOfSize_weight_(TITLE_FONT_SZ, 0.4)
        f_status = NSFont.systemFontOfSize_(STATUS_FONT_SZ)
        f_hint = NSFont.systemFontOfSize_(HINT_FONT_SZ)

        def mk(text, size, alpha, font, bold=False, wrap=False):
            lb = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 10, 10))
            lb.setEditable_(False)
            lb.setSelectable_(False)
            lb.setBezeled_(False)
            lb.setDrawsBackground_(False)
            lb.setFont_(font)
            lb.setTextColor_(NSColor.whiteColor().colorWithAlphaComponent_(alpha))
            lb.setStringValue_(text)
            lb.setAlignment_(NSTextAlignmentLeft)
            lb.setLineBreakMode_(NSLineBreakByWordWrapping)
            if wrap:
                lb.setMaximumNumberOfLines_(0)
            ve.addSubview_(lb)
            return lb

        title_lbl = mk(title, TITLE_FONT_SZ, 1.0, f_title, bold=True)
        status_lbl = mk("", STATUS_FONT_SZ, 0.92, f_status, wrap=True)
        hint_lbl = mk("", HINT_FONT_SZ, 0.55, f_hint)
        inner_w = WIDTH - 2 * PAD

        targets: list = []
        live: list = []

        def _relayout():
            """按**当前状态文字的实际高度**重算卡片高度，顶边不动。

            ⚠️ 不预留行数：预留会在状态行短的时候在中间留一条空档（第一版就是那样，
               渲染出来一眼能看见）。"""
            sh = max(STATUS_FONT_SZ + 4,
                     measure_text_h(status_lbl.stringValue(), inner_w, f_status))
            h = (PAD + BTN_H + GAP_BTN + HINT_H + GAP_HINT + sh + GAP_TITLE
                 + TITLE_H + PAD)
            f = p.frame()
            top = f.origin.y + f.size.height          # 顶边固定，往下长
            p.setFrame_display_(NSMakeRect(f.origin.x, top - h, WIDTH, h), True)
            for lb, y_, h_ in (
                (title_lbl, h - PAD - TITLE_H, TITLE_H),
                (status_lbl, PAD + BTN_H + GAP_BTN + HINT_H + GAP_HINT, sh + 2),
                (hint_lbl, PAD + BTN_H + GAP_BTN, HINT_H),
            ):
                lb.setFrame_(NSMakeRect(PAD, y_, inner_w, h_))
            for i, b in enumerate(live):              # 按钮永远贴着底
                b.setFrame_(NSMakeRect(PAD + i * (BTN_W + BTN_GAP), PAD,
                                       BTN_W, BTN_H))

        def set_status(text, alpha=0.92):
            try:
                status_lbl.setStringValue_(text or "")
                status_lbl.setTextColor_(
                    NSColor.whiteColor().colorWithAlphaComponent_(alpha))
                _relayout()
            except Exception:                             # noqa: BLE001
                pass

        def set_hint(text, alpha=0.55):
            try:
                hint_lbl.setStringValue_(text or "")
                hint_lbl.setTextColor_(
                    NSColor.whiteColor().colorWithAlphaComponent_(alpha))
            except Exception:                             # noqa: BLE001
                pass

        def set_buttons(specs):
            for b in live:
                try:
                    b.removeFromSuperview()
                except Exception:                         # noqa: BLE001
                    pass
            live.clear()
            targets.clear()                               # ⚠️ 一并放掉旧的 target
            for i, (text, cb) in enumerate(specs or []):
                b = NSButton.alloc().initWithFrame_(
                    NSMakeRect(PAD + i * (BTN_W + BTN_GAP), PAD, BTN_W, BTN_H))
                b.setTitle_(text)
                # ⚠️ `setBezelStyle_(1)` = rounded，与 `entry_panel.mk` 同一档 ——
                #    收尾卡上的按钮必须**看起来像按钮**（第一版用无边框，渲染出来
                #    与旁边的说明文字分不出来，作者一眼看不出能点）。
                b.setBezelStyle_(1)
                b.setFont_(NSFont.systemFontOfSize_(BTN_FONT_SZ))
                t = panel.make_button_target(cb)
                targets.append(t)
                b.setTarget_(t)
                b.setAction_("clicked:")
                ve.addSubview_(b)
                live.append(b)
            _relayout()

        state = {"closed": False}

        def close(_=None):
            if state["closed"]:
                return
            state["closed"] = True
            try:
                p.orderOut_(None)
            except Exception:                             # noqa: BLE001
                pass
            if on_close:
                try:
                    on_close()
                except Exception:                         # noqa: BLE001
                    pass

        _relayout()
        p.orderFrontRegardless()
        return {"panel": p, "close": close, "set_status": set_status,
                "set_hint": set_hint, "set_buttons": set_buttons,
                "_targets": targets, "_live": live, "_fp": fp,
                # ⚠️ `_status_lbl` / `_hint_lbl` 是**验收用的读回口**（同 `_targets`）——
                #    跨线程那条路（worker -> callAfter -> 卡）在离线判据里看不到，
                #    只能靠回读控件的真实字符串来断言「进度真的上屏了」。
                "_status_lbl": status_lbl, "_hint_lbl": hint_lbl,
                "_height": lambda: p.frame().size.height}
    except Exception:                                     # noqa: BLE001
        return None
