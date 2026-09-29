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
#: 按钮的**最小可读宽度**。按钮多了就按这个下限判「放不下」并**报错**，
#: 而不是悄悄裁掉一个（见 `set_buttons` 里那段）。⚠️ 12pt 字号下 `存 入` 两字
#: 加按钮内衬大约要 56pt，60 是留了余量的下限。
BTN_MIN_W = 60.0
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


def btn_geom(count: int) -> float:
    """`count` 个按钮时**每个多宽** —— 按钮几何的**唯一定义点**。

    ⚠️⚠️ 抽出来的理由（2026-09-29）：原来 `set_buttons` 与 `_relayout` **各写一份**，
        都用写死的 `BTN_W`。只改一处 → 另一处**立刻把它覆盖回去**。
        实测：给 `set_buttons` 算好 `bw=78.5`，量出来的按钮**还是 100 宽** ——
        因为 `set_buttons` 结尾就调 `_relayout()`，而它按 `BTN_W` 重排了一遍。
        （判据当场抓到的：同一轮里既打印 `n=4 bw=78.5`，又量到宽度 100.0。）
    ⚠️ `BTN_W` 只作**上限**：按钮一律等宽，所以「有几个按钮」不改变单个按钮的观感
        （≤3 个时与从前完全一样）。
    """
    if count <= 0:
        return BTN_W
    bw = BTN_W
    if count > 1:
        bw = min(BTN_W, (WIDTH - 2 * PAD - (count - 1) * BTN_GAP) / count)
    if bw < BTN_MIN_W:
        # 那是**编程错误**（调用方给了太多按钮），不是用户错误 ——
        # 悄悄裁掉一个按钮比报错坏得多。
        raise ValueError(
            f"按钮太多：{count} 个至少需要 {count * BTN_MIN_W + (count - 1) * BTN_GAP:.0f}pt，"
            f"卡片内宽只有 {WIDTH - 2 * PAD:.0f}pt")
    return bw


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

        def mk(text, alpha, font, wrap=False):
            # ⚠️ 不要加 `size` / `bold` 形参 —— 字号与字重**都在 `font` 里**
            #    （调用点自己 `systemFontOfSize_weight_` 构造）。2026-09-28 OCR 审计
            #    指出：原来那两个形参在函数体里从没被用过，`bold=True` 被静默忽略，
            #    维护者会以为「标题的粗体靠 bold 生效」从而改错地方。
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

        title_lbl = mk(title, 1.0, f_title)
        status_lbl = mk("", 0.92, f_status, wrap=True)
        hint_lbl = mk("", 0.55, f_hint)
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
            # ⚠️⚠️ **三层必须跟着窗口一起改**（2026-09-28 OCR 审计发现，实测确认）。
            #    `panel.build` 那一刻按初始尺寸定死了它们，而它们是 `contentView` 的
            #    **子视图** —— AppKit 对代码建的视图**默认不自动缩放**。于是卡片一变高，
            #    `glass` 还停在旧高度：**实测窗口 380×272 / glass 380×200**，
            #    顶部 72px **没有磨砂背景**。
            #    ⚠️ 变矮时反而看不出来（glass 从底部往上盖，多出来那截被窗口裁掉）
            #       ——所以这个 bug **只在状态文字变长时露头**，四个渲染阶段里
            #       只有"超长状态"那一个能撞到，我恰好没渲染过它。
            inner = NSMakeRect(0, 0, WIDTH, h)
            for layer in (fp.glass, getattr(fp, "scrim", None),
                          getattr(fp, "drag", None)):
                if layer is not None:
                    try:
                        layer.setFrame_(inner)
                    except Exception:                     # noqa: BLE001
                        pass
            for lb, y_, h_ in (
                (title_lbl, h - PAD - TITLE_H, TITLE_H),
                (status_lbl, PAD + BTN_H + GAP_BTN + HINT_H + GAP_HINT, sh + 2),
                (hint_lbl, PAD + BTN_H + GAP_BTN, HINT_H),
            ):
                lb.setFrame_(NSMakeRect(PAD, y_, inner_w, h_))
            # ⚠️ **宽度走 `btn_geom`**（唯一定义点）—— 与 `set_buttons` 同一份。
            #    两处各写一份的话，改一处会被另一处覆盖（2026-09-29 实测踩到）。
            _bw = btn_geom(len(live))
            for i, b in enumerate(live):              # 按钮永远贴着底
                b.setFrame_(NSMakeRect(PAD + i * (_bw + BTN_GAP), PAD,
                                       _bw, BTN_H))

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
            specs = list(specs or [])
            # ⚠️⚠️ **宽度走 `btn_geom`**（唯一定义点，与 `_relayout` 共用）。
            #    原来这里和 `_relayout` **各写一份**都用 `BTN_W` —— 只改一处，
            #    结尾那次 `_relayout()` 会**立刻把它覆盖回去**（2026-09-29 实测）。
            n = len(specs)
            bw = btn_geom(n)
            for i, (text, cb) in enumerate(specs):
                b = NSButton.alloc().initWithFrame_(
                    NSMakeRect(PAD + i * (bw + BTN_GAP), PAD, bw, BTN_H))
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
