#!/usr/bin/env python3
"""「功能介绍」—— 点「下一步」几页看完 ClassLive 能干什么。**非模态磨砂卡片**，和更新卡片同一套配方。

## 契约

    card = tour.build(on_done=None)     # 显示第 1 页；失败返回 None（只是说明，不能让课起不来）
    card["next"]() / card["back"]() / card["close"]()
    tour.PAGES                          # 文案的唯一定义点（截图探针、判据都从这里取）

## 为什么长这样（2026-10-12 调研，出处都是一手）

- **页数 4**：Chameleon 1500 万次交互的基准 —— 3 步完成率 72%，4 步掉到 45%，7 步 16%；
  前 1% 的引导没有超过 5 步的。NN/g：「只放必须知道的、一页一个概念」。
- **一定能跳过、跳过就不再自动弹、但随时能再打开**：Apple HIG Onboarding 原话
  「If you let people skip the tutorial…don't present it again…make sure it's easy for people to find」；
  NN/g：用户经常直接跳过 deck-of-cards，硬塞只会挡路。
- **进度点**：Chameleon 实测带进度指示的完成率 +12%。
- **用户自己点开的引导完成率是被动弹出的 2–3 倍**（Chameleon 2025）→ 面板里常驻一个入口。
- **非模态、不抢焦点**：同 `whatsnew.py` 文件头那三条 —— 这是上课用的工具，任何弹窗都不许卡住开课。
- 回车 = 下一步、Esc = 跳过（`setKeyEquivalent_`）：键盘用户不用摸鼠标。

⚠️ 文案里提到的每个按钮 / 符号都要在界面上真的存在（README 里还写着已经合并掉的 ⭐ 按钮 —— 别照抄 README）。
"""
from __future__ import annotations

import os

import objc_own
import panel

#: 每页：`(SF Symbol 名, 标题, 正文行…)`。**唯一定义点**。一页一个概念，正文 ≤3 行。
#: ⚠️ 图标用**单色系统符号**不用 emoji：emoji 是彩色的、改不了色（同 overlay 顶栏 ❓ 换成 SF Symbol 那条）。
#: 第 1 页「这一版和旧版比」：每格 `(大数字, 一句话, 旧 → 新)`。⭐ 数字全是实测，出处：
#:   · 省电：同一段 240 s 课堂录音，CPU 能耗 1692 J → 333 J（旧 = 本地 Whisper 定稿 + Parakeet 草稿，即 3.8.x 默认），
#:     平均 6.6 W → 1.3 W —— `docs/HANDOFF-20261012-3.9.4.md` §E
#:   · 错字 / 漏句：MIT 公开课 322 段，本地 Whisper 12.0% / 2.5% → 云端 Groq（Opus）10.1% / 0.4% —— `docs/RESEARCH-cloud-asr.md`
#:   · 草稿乱改：回改率 5.7% → 0.6%（Parakeet 滑窗 → Nemotron 流式）—— `docs/PLAN-streaming-asr.md`
#:   · 句尾漏字：段尾草稿比定稿少的词 3.6 → 2.2（SOC10020 真课 47 段，3.9.3 → 3.9.4）—— 交接文档
#:   · 新字幕更快：新一段第一份草稿距上一段结束 p50 2.22 → 1.21 s（3.9.3 → 3.9.4，云端走通后实测）
#: ⚠️ 百分比 = (旧 − 新) / 旧，四舍五入到整数；改数字先改出处。
STATS: tuple = (
    ("80%", "更省电", "6.6 W → 1.3 W"),
    ("16%", "错字更少", "12.0% → 10.1%"),
    ("84%", "漏句更少", "2.5% → 0.4%"),
    ("89%", "草稿少乱改", "5.7% → 0.6%"),
    ("39%", "句尾少漏字", "每段 3.6 → 2.2 个词"),
    ("45%", "新字幕更快", "2.2 秒 → 1.2 秒"),
)
STATS_NOTE = "同一段课堂录音实测 · 前四项对比 3.8，后两项对比 3.9.3"

PAGES: tuple = (
    ("chart.bar.xaxis", "这一版，和旧版比", STATS),
    ("rectangle.stack", "一门课，一张卡",
     ("课件拖进下面的虚线框，自动分到各门课。",
      "上面那条就绪条：缺哪一项，就点哪一项。",
      "点「开始上课」才开麦。")),
    ("captions.bubble", "英文在上，中文在下",
     ("灰色的是还在听的草稿，变亮就是定稿。",
      "拖边角能把窗口拉大，往上翻能看整节课。",
      "右上角一键切换：双语 → 只英·校 → 纯转录。")),
    ("questionmark.bubble", "没听懂，当场问",
     ("「讲一下」：把刚才那段讲给你听；底部输入框可以直接问。",
      "顶栏的问号：重点或没听懂都按它，课后笔记会指回那几句。",
      "底下冒出来的术语，点一下看解释。")),
    ("book.closed", "下课自动出笔记",
     ("笔记写进你的 Obsidian：上面是复习层，下面折叠着逐字稿。",
      "最值得记的几句、课务（due / midterm）会单独挑出来。")),
)

WIDTH = 460.0
PAD = 24.0
BADGE = 56.0           # 图标底座（圆角方块）边长
BADGE_R = 14.0         # 底座圆角 —— 同心：卡片 16pt 圆角里套 14pt
SYMBOL_PT = 26.0
TITLE_H = 30.0
LINE_H = 22.0
DOTS_H = 16.0
FOOT_H = 28.0
TILE_H = 66.0          # 数字格：大数字 26 + 一句话 16 + 旧→新 14 + 上下留白
TILE_GAP = 8.0
TILE_COLS = 3
NOTE_H = 16.0
FADE_S = 0.18          # 翻页淡入淡出；系统开了「减弱动态效果」就不做
_W = 0.23              # NSFontWeightMedium，同 whatsnew / overlay
_SEMI = 0.4

#: 三档按钮：主（下一步 / 开始用）实心白底黑字 > 次（上一步）白字 0.75 > 弱（跳过）白字 0.45。
#: 三颗都用系统圆角壳的话，「跳过」这个逃生口和主操作抢同样的分量（2026-10-12 评审，核实属实）。
PRIMARY_BG, SECONDARY_A, TERTIARY_A = panel.PRIMARY_BG_A, 0.75, 0.45
DOT_ON, DOT_OFF = 0.92, 0.22


def is_stats(body) -> bool:
    """这一页的正文是数字格（`STATS` 那种三元组）而不是几行字。"""
    return bool(body) and not isinstance(body[0], str)


def body_h(body) -> float:
    """纯函数：一页正文占多高。数字格 = 行数 × 格高 + 脚注；文字 = 行数 × 行高。"""
    if is_stats(body):
        rows = -(-len(body) // TILE_COLS)
        return rows * TILE_H + (rows - 1) * TILE_GAP + 6 + NOTE_H
    return len(body) * LINE_H


def height(pages=PAGES) -> float:
    """卡片高度按**最高那一页**算 —— 翻页时窗口不跳（矮的那页在正文区里垂直居中）。"""
    return PAD + BADGE + 14 + TITLE_H + 8 + max(body_h(p[2]) for p in pages) + 18 + DOTS_H + 14 + FOOT_H + PAD


def body_offset(h_page: float, h_max: float) -> float:
    """纯函数：这一页正文往下挪多少才在正文区里居中（矮的页不在底下留一截空洞）。整数坐标：非整数会让字发虚。"""
    return float(round((h_max - h_page) / 2))


def dots(i: int, n: int) -> list:
    """纯函数：每个点的不透明度。**同一个实心圆、只换透明度**（原生 PageControl 的做法；
    `●` 和 `○` 在 SF 里直径与重心不齐，○ 的线还细，混排显散）。"""
    return [DOT_ON if k == i else DOT_OFF for k in range(n)]


def _reduce_motion() -> bool:
    try:
        from AppKit import NSWorkspace
        return bool(NSWorkspace.sharedWorkspace().accessibilityDisplayShouldReduceMotion())
    except Exception:                                     # noqa: BLE001
        return True


def build(on_done=None, pages=PAGES):
    """构造并显示第 1 页。失败返回 None。`on_done(finished: bool)`：看完 = True，跳过 = False。"""
    try:
        from AppKit import (NSAttributedString, NSButton, NSColor, NSFont,
                            NSFontAttributeName, NSForegroundColorAttributeName,
                            NSImage, NSImageSymbolConfiguration, NSImageView, NSMakeRect,
                            NSMutableAttributedString, NSMutableParagraphStyle,
                            NSParagraphStyleAttributeName, NSTextAlignmentCenter, NSTextField,
                            NSView, NSWindowStyleMaskBorderless,
                            NSWindowStyleMaskNonactivatingPanel)

        h = height(pages)
        n_max = max((len(pg[2]) for pg in pages if not is_stats(pg[2])), default=0)
        bh_max = max(body_h(pg[2]) for pg in pages)
        fp = panel.build(NSMakeRect(0, 0, WIDTH, h),
                         NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel)
        p, ve = fp.window, fp.glass
        p.setHasShadow_(True)
        white = NSColor.whiteColor()

        # 翻页时换的东西都在这一层里，整层淡入淡出（按钮和进度点不动）
        content = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, WIDTH, h))
        content.setWantsLayer_(True)
        ve.addSubview_(content)

        def label(rect, size, *, alpha=1.0, weight=_W):
            lb = NSTextField.alloc().initWithFrame_(rect)
            lb.setEditable_(False); lb.setSelectable_(False)
            lb.setBezeled_(False); lb.setDrawsBackground_(False)
            lb.setFont_(NSFont.systemFontOfSize_weight_(size, weight))
            lb.setTextColor_(white.colorWithAlphaComponent_(alpha))
            lb.setAlignment_(NSTextAlignmentCenter)
            content.addSubview_(lb)
            return lb

        y = h - PAD - BADGE
        badge = NSView.alloc().initWithFrame_(NSMakeRect((WIDTH - BADGE) / 2, y, BADGE, BADGE))
        badge.setWantsLayer_(True)
        lay = badge.layer()
        lay.setCornerRadius_(BADGE_R)
        try:
            lay.setCornerCurve_("continuous")             # 连续曲率圆角（squircle），macOS 11+
        except Exception:                                 # noqa: BLE001
            pass
        lay.setBackgroundColor_(white.colorWithAlphaComponent_(0.07).CGColor())
        lay.setBorderColor_(white.colorWithAlphaComponent_(0.12).CGColor())
        lay.setBorderWidth_(1.0)
        content.addSubview_(badge)
        icon = NSImageView.alloc().initWithFrame_(NSMakeRect(0, 0, BADGE, BADGE))
        icon.setSymbolConfiguration_(
            NSImageSymbolConfiguration.configurationWithPointSize_weight_(SYMBOL_PT, 0.0))
        icon.setContentTintColor_(white.colorWithAlphaComponent_(0.92))
        badge.addSubview_(icon)

        y -= 14 + TITLE_H
        title = label(NSMakeRect(PAD, y, WIDTH - 2 * PAD, TITLE_H), 20.0, weight=_SEMI)
        badge_y0, title_y0 = badge.frame().origin.y, y
        body_top = y - 8
        body = [label(NSMakeRect(PAD, body_top - (k + 1) * LINE_H, WIDTH - 2 * PAD, LINE_H),
                      13.0, alpha=0.82) for k in range(n_max)]

        # 数字格（第 1 页「和旧版比」）：先建好一组格子，翻到那页才显示
        n_tiles = max((len(pg[2]) for pg in pages if is_stats(pg[2])), default=0)
        tile_w = (WIDTH - 2 * PAD - (TILE_COLS - 1) * TILE_GAP) / TILE_COLS
        tiles = []
        for k in range(n_tiles):
            r, c = divmod(k, TILE_COLS)
            tv = NSView.alloc().initWithFrame_(NSMakeRect(
                PAD + c * (tile_w + TILE_GAP), 0, tile_w, TILE_H))
            tv.setWantsLayer_(True)
            tl = tv.layer()
            tl.setCornerRadius_(10.0)
            try:
                tl.setCornerCurve_("continuous")
            except Exception:                             # noqa: BLE001
                pass
            tl.setBackgroundColor_(white.colorWithAlphaComponent_(0.06).CGColor())
            tl.setBorderColor_(white.colorWithAlphaComponent_(0.10).CGColor())
            tl.setBorderWidth_(1.0)
            content.addSubview_(tv)
            parts = []
            for yy, hh, size, alpha, weight in ((32.0, 30.0, 24.0, 0.96, _SEMI),
                                                (17.0, 17.0, 12.0, 0.85, _W),
                                                (4.0, 15.0, 10.5, 0.50, _W)):
                lb = NSTextField.alloc().initWithFrame_(NSMakeRect(0, yy, tile_w, hh))
                lb.setEditable_(False); lb.setSelectable_(False)
                lb.setBezeled_(False); lb.setDrawsBackground_(False)
                lb.setFont_(NSFont.systemFontOfSize_weight_(size, weight))
                lb.setTextColor_(white.colorWithAlphaComponent_(alpha))
                lb.setAlignment_(NSTextAlignmentCenter)
                tv.addSubview_(lb)
                parts.append(lb)
            tiles.append((tv, r, parts))
        note = label(NSMakeRect(PAD, 0, WIDTH - 2 * PAD, NOTE_H), 10.5, alpha=0.45)

        fy = PAD
        dot = NSTextField.alloc().initWithFrame_(NSMakeRect(PAD, fy + FOOT_H + 14, WIDTH - 2 * PAD, DOTS_H))
        dot.setEditable_(False); dot.setSelectable_(False)
        dot.setBezeled_(False); dot.setDrawsBackground_(False)
        ve.addSubview_(dot)
        para = NSMutableParagraphStyle.alloc().init()
        para.setAlignment_(NSTextAlignmentCenter)
        dot_font = NSFont.systemFontOfSize_(7.0)

        def set_dots(i):
            s_ = NSMutableAttributedString.alloc().init()
            for k, a in enumerate(dots(i, len(pages))):
                s_.appendAttributedString_(NSAttributedString.alloc().initWithString_attributes_(
                    ("   " if k else "") + "●",
                    {NSFontAttributeName: dot_font,
                     NSForegroundColorAttributeName: white.colorWithAlphaComponent_(a),
                     NSParagraphStyleAttributeName: para}))
            dot.setAttributedStringValue_(s_)

        def button(text, x, w, *, alpha, primary=False, key=""):
            b = NSButton.alloc().initWithFrame_(NSMakeRect(x, fy, w, FOOT_H))
            b.setBordered_(False)
            b.setWantsLayer_(True)
            col = (NSColor.blackColor().colorWithAlphaComponent_(0.85) if primary
                   else white.colorWithAlphaComponent_(alpha))
            b.setAttributedTitle_(NSAttributedString.alloc().initWithString_attributes_(
                text, {NSFontAttributeName: NSFont.systemFontOfSize_weight_(13.0, _SEMI if primary else _W),
                       NSForegroundColorAttributeName: col,
                       NSParagraphStyleAttributeName: para}))
            if primary:
                panel.style_primary(b, text)
            if key:
                b.setKeyEquivalent_(key)
            ve.addSubview_(b)
            return b

        skip = button("跳过", PAD - 8, 56.0, alpha=TERTIARY_A, key="\x1b")
        nxt = button("下一步", WIDTH - PAD - 96.0, 96.0, alpha=1.0, primary=True, key="\r")
        back = button("上一步", WIDTH - PAD - 96.0 - 8.0 - 64.0, 64.0, alpha=SECONDARY_A)
        state = {"i": 0, "closed": False}

        def retitle(b, text):
            a = NSMutableAttributedString.alloc().initWithAttributedString_(b.attributedTitle())
            a.replaceCharactersInRange_withString_((0, a.length()), text)
            b.setAttributedTitle_(a)

        def fill(i):
            sym, ttl, lines = pages[i]
            img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(sym, ttl)
            if img is not None:
                img.setTemplate_(True)
            icon.setImage_(img)
            title.setStringValue_(ttl)
            off = body_offset(body_h(lines), bh_max)
            # 图标、标题、正文**一起**下移：矮的页整组居中，不是标题顶着、正文悬在半空
            for v, y0 in ((badge, badge_y0), (title, title_y0)):
                v.setFrameOrigin_((v.frame().origin.x, y0 - off))
            stats = is_stats(lines)
            for k, lb in enumerate(body):
                lb.setStringValue_(lines[k] if not stats and k < len(lines) else "")
                f = lb.frame()
                lb.setFrameOrigin_((f.origin.x, body_top - (k + 1) * LINE_H - off))
            for k, (tv, r, parts) in enumerate(tiles):
                shown = stats and k < len(lines)
                tv.setHidden_(not shown)
                if shown:
                    for lb, txt in zip(parts, lines[k]):
                        lb.setStringValue_(txt)
                    f = tv.frame()
                    tv.setFrameOrigin_((f.origin.x, body_top - off - (r + 1) * TILE_H - r * TILE_GAP))
            note.setHidden_(not stats)
            if stats:
                note.setStringValue_(STATS_NOTE)
                note.setFrameOrigin_((PAD, body_top - off - body_h(lines)))

        def show(i, animate=True):
            if animate and not _reduce_motion():
                from Quartz import CATransition
                t = CATransition.animation()
                t.setType_("fade")
                t.setDuration_(FADE_S)
                content.layer().addAnimation_forKey_(t, "page")
            fill(i)
            set_dots(i)
            last = i == len(pages) - 1
            retitle(nxt, "开始用" if last else "下一步")
            back.setHidden_(i == 0)
            skip.setHidden_(last)     # 最后一页「开始用」就是关，再留个「跳过」是两个按钮干一件事
            state["i"] = i

        def close(finished=False):
            if state["closed"]:
                return
            state["closed"] = True
            try:
                p.orderOut_(None)
            except Exception:                             # noqa: BLE001
                pass
            if on_done:
                try:
                    on_done(finished)
                except Exception:                         # noqa: BLE001
                    pass

        def go_next():
            if state["i"] >= len(pages) - 1:
                close(True)
            else:
                show(state["i"] + 1)

        def go_back():
            if state["i"] > 0:
                show(state["i"] - 1)

        targets = []
        for b, cb in ((skip, lambda: close(False)), (nxt, go_next), (back, go_back)):
            t = panel.make_button_target(cb)
            b.setTarget_(t); b.setAction_("clicked:")
            targets.append(t)

        show(0, animate=False)
        p.center()
        p.orderFrontRegardless()      # ⚠️ 不是 makeKeyAndOrderFront —— 绝不抢焦点
        return {"panel": p, "next": go_next, "back": go_back, "close": close,
                "page": lambda: state["i"], "_targets": targets}
    except objc_own.ObjcNameCollision:
        raise                         # 程序缺陷，不吞（同 whatsnew.build 那条）
    except Exception:                                     # noqa: BLE001
        if os.environ.get("CLASSLIVE_DEBUG"):
            import traceback
            traceback.print_exc()
        return None
