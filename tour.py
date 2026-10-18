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

import motion
import objc_own
import panel

#: 每页：`(SF Symbol 名, 标题, 正文行…)`。**唯一定义点**。一页一个概念，正文 ≤3 行。
#: ⚠️ 图标用**单色系统符号**不用 emoji：emoji 是彩色的、改不了色（同 overlay 顶栏 ❓ 换成 SF Symbol 那条）。
#: 第 1 页「这一版和旧版比」：每格 `(大数字, 一句话, 旧 → 新)`。⭐ 3.10 对 **3.9.7** 的四个数
#: （作者 2026-10-18 定）。**省电格是估算，其余实测**，出处（3.10 更新说明草稿，发版前并入 CHANGELOG.md）：
#:   · 错字 41%：OCW lec11 **同一段录音**，Soniox 流式 vs 本地 Nemotron 草稿，错字率 11.7% → 6.9%
#:   · 中文首字 45%：都柏林真课经 Worker 20 句，首字 p50 0.95 → 0.52 s
#:   · 0.9 秒：同 lec11，句末到定稿上移 p50 0.86 s（云端才有这个数 → 那句话里写明「网好的话」）
#:   · 省电 40%：**估算**（Soniox 在跑时本地草稿/定稿都停）：比 3.9.7 少 ~40%、比 3.8 少 ~88%
#:     （3.9.4 实测 106 s CPU / 240 s，其中本地草稿 40–50 s）—— 未重测 → 脚注必须写「省电为估算」
#: ⚠️ 旧 3.9.4 那六格（80% 省电 / 16% 错字 / 84% 漏句 / 89% 草稿 / 39% 句尾 / 45% 新字幕）**已删**：
#:     Soniox 换掉本地草稿后没重测，沿用就是旧数配新版本（作者 10-18：没测过的不放）。
#: ⚠️ 百分比 = (旧 − 新) / 旧，四舍五入到整数；改数字先改出处。
STATS: tuple = (
    ("41%", "错字更少，模型能力更强", "11.7% → 6.9%"),
    ("45%", "中文更快出来", "0.95 秒 → 0.52 秒"),
    ("0.9 秒", "牛逼级别的（网好的话）", "句末到上移 p50 0.86 s"),
    ("省电", "再省 40%", ""),
)
STATS_NOTE = "同一段课堂录音实测 · 对比 3.9.7（省电为估算）"

#: 这套介绍**内容**对应的版本。双击 .app 打开面板时，没看过这一版的介绍就弹一次
#: （新用户：没有标记；升级：标记比它旧）。⚠️ 改了 PAGES / STATS（尤其第 1 页「和旧版比」）
#: 就把它抬到那一版 —— 内容没变的小版本不重复弹，免得每次升级都看一遍过期的数字。
TOUR_VERSION = "3.10.0"


def should_show(seen: str) -> bool:
    """纯函数：标记里记着 `seen`（看过的介绍版本，可能是空串）时，要不要弹。"""
    import changelog
    seen = (seen or "").strip()
    return not seen or changelog.ver_key(seen) < changelog.ver_key(TOUR_VERSION)

PAGES: tuple = (
    ("chart.bar.xaxis", "这一版，和旧版比", STATS),
    ("rectangle.stack", "一门课，一张卡",
     ("课件拖进下面的虚线框，自动分到各门课。",
      "按回车直接开始：课表上这会儿的课，或者上次那门。",
      "开课前先看「课前回顾」：上节课讲到哪，不旷课就上节嗯。")),
    ("captions.bubble", "英文在上，中文在下",
     ("最底一行是正在听的草稿，说完一句就定下来往上走。",
      "转录及翻译模式以及UI经文献论证，符合阅读习惯。",
      "右上角一键切换：双语 → 只英·校 → 纯转录。")),
    ("questionmark.bubble", "没听懂，当场问",
     ("「讲一下」：把刚才那段讲给你听；底部输入框可以直接问。",
      "顶栏的问号：重点或没听懂都按它，课后笔记会指回那几句。",
      "底下冒出来的术语，点一下看解释。")),
    # ⚠️ 实时总结和课后笔记**合成一页**：它们是同一条提炼链（课上出纲要、课后进笔记），
    #    分开就是 6 页 —— 超过上面那条「≤5 步」。入口名「AI 实时总结」与顶栏章节条逐字一致
    #    （`overlay._chapter_bar_text`：没内容时显示「▸ AI 实时总结 · 点这里」）。
    ("list.bullet.rectangle", "重点自动提炼，课后有笔记",
     ("左上角「▸ AI 实时总结」：边听边分小节、提炼要点。",
      "老师说的考试、截止、调课 →「一些事儿」，点一下记进提醒事项。",
      "笔记全新重构：Obsidian 一份精简版，另有可交互网页版详细讲解。")),
)

WIDTH = 460.0
PAD = 24.0
BADGE = 56.0           # 图标底座（圆角方块）边长
BADGE_R = 14.0         # 底座圆角 —— 同心：卡片 16pt 圆角里套 14pt
SYMBOL_PT = 26.0
TITLE_H = 30.0
LINE_H = 22.0
DOTS_H = 16.0
DOT, DOT_PILL, DOT_GAP = 6.0, 18.0, 6.0   # 进度点：当前页拉成胶囊（翻页时从圆点「长」过去）
FOOT_H = 28.0
TILE_H = 76.0          # 数字格：大数字 30 + 一句话 17 + 旧→新 14，上下各留约 8（原 66 时末行离底只剩 4pt）
TILE_GAP = 8.0
#: 数字格一行放几个。⚠️ 3.10 起是 **2×2**：四格挤一行时格宽只剩 97pt，「错字更少，模型能力更强」
#: （131pt）和「句末到上移 p50 0.86 s」（109pt）会被 `…` 截断 —— 数字卡最不该出截断。
#: 2 列格宽 202pt，四格全放得下（最宽 131pt）；页高与原来 3 列 ×2 行相同。
#: ⚠️ 改这个常量 `tests/test_tour.py` 会按新格宽重量宽度（截断 = 红）。
TILE_COLS = 2
NOTE_H = 16.0
NOTE_GAP = 12.0        # 脚注离格子的距离（原 6：脚注贴着格子像是最后一格的一部分）
FADE_S = motion.STATE_S   # 翻页淡入淡出（0.18→0.20，与面板其余换状态同档）；系统开了「减弱动态效果」就不做
_W = 0.23              # NSFontWeightMedium，同 whatsnew / overlay
_SEMI = 0.4

#: 三档按钮：主（下一步 / 开始用）实心胶囊 > 次（上一步）淡底胶囊（`panel.style_secondary`）> 弱（跳过）白字 0.45。
#: 三颗都用系统圆角壳的话，「跳过」这个逃生口和主操作抢同样的分量（2026-10-12 评审，核实属实）；
#: 「上一步」原来是裸字，看不出能点（10-13 评审）。
PRIMARY_BG, SECONDARY_A, TERTIARY_A = panel.PRIMARY_BG_A, panel.SECONDARY_FG_A, 0.45
DOT_ON, DOT_OFF = 0.92, 0.22


def is_stats(body) -> bool:
    """这一页的正文是数字格（`STATS` 那种三元组）而不是几行字。"""
    return bool(body) and not isinstance(body[0], str)


def body_h(body) -> float:
    """纯函数：一页正文占多高。数字格 = 行数 × 格高 + 脚注；文字 = 行数 × 行高。"""
    if is_stats(body):
        rows = -(-len(body) // TILE_COLS)
        return rows * TILE_H + (rows - 1) * TILE_GAP + NOTE_GAP + NOTE_H
    return len(body) * LINE_H


def height(pages=PAGES) -> float:
    """卡片高度按**最高那一页**算 —— 翻页时窗口不跳（矮的那页正文顶对齐，下面留白）。"""
    return PAD + BADGE + 14 + TITLE_H + 8 + max(body_h(p[2]) for p in pages) + 18 + DOTS_H + 14 + FOOT_H + PAD


def dots(i: int, n: int) -> list:
    """纯函数：每个点的 `(x, 宽, 不透明度)`，整排在卡片里水平居中。

    当前页是一颗胶囊（`DOT_PILL` 宽），其余是圆点 —— 翻页时胶囊从这一颗「长」到下一颗
    （2026-10-13 评审：只换透明度的一排圆点太弱，看不出在第几页）。整数坐标：非整数会让边发虚。
    """
    ws = [DOT_PILL if k == i else DOT for k in range(n)]
    x = float(round((WIDTH - sum(ws) - DOT_GAP * (n - 1)) / 2))
    out = []
    for k, w in enumerate(ws):
        out.append((x, w, DOT_ON if k == i else DOT_OFF))
        x += w + DOT_GAP
    return out


def _reduce_motion() -> bool:
    return motion.reduce_motion()          # 读法统一在 motion.py


def build(on_done=None, pages=PAGES):
    """构造并显示第 1 页。失败返回 None。`on_done(finished: bool)`：看完 = True，跳过 = False。"""
    try:
        from AppKit import (NSAttributedString, NSColor, NSFont,
                            NSFontAttributeName, NSForegroundColorAttributeName,
                            NSImage, NSImageSymbolConfiguration, NSImageView, NSMakeRect,
                            NSMutableAttributedString, NSMutableParagraphStyle,
                            NSParagraphStyleAttributeName, NSTextAlignmentCenter, NSTextField,
                            NSView, NSWindowStyleMaskBorderless,
                            NSWindowStyleMaskNonactivatingPanel)

        h = height(pages)
        n_max = max((len(pg[2]) for pg in pages if not is_stats(pg[2])), default=0)
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
        _cfg = NSImageSymbolConfiguration.configurationWithPointSize_weight_(SYMBOL_PT, 0.0)
        try:
            # 分层渲染(macOS 12+): 符号的次要部件自动淡一档, 单色白图标有了前后层次。
            # 老系统没有这个 API → 留在单色。
            _cfg = _cfg.configurationByApplyingConfiguration_(
                NSImageSymbolConfiguration.configurationWithHierarchicalColor_(
                    white.colorWithAlphaComponent_(0.92)))
        except Exception:                                 # noqa: BLE001
            pass
        icon.setSymbolConfiguration_(_cfg)
        icon.setContentTintColor_(white.colorWithAlphaComponent_(0.92))
        badge.addSubview_(icon)

        y -= 14 + TITLE_H
        title = label(NSMakeRect(PAD, y, WIDTH - 2 * PAD, TITLE_H), 20.0, weight=_SEMI)
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
            # 自上而下：大数字 / 这是什么 / 旧 → 新。末行用常规字重 —— 它是佐证，不该和标签一样重。
            for yy, hh, size, alpha, weight in ((38.0, 30.0, 24.0, 0.96, _SEMI),
                                                (22.0, 17.0, 12.0, 0.85, _W),
                                                (8.0, 14.0, 10.5, 0.50, 0.0)):
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
        para = NSMutableParagraphStyle.alloc().init()
        para.setAlignment_(NSTextAlignmentCenter)
        dot_y = fy + FOOT_H + 14 + (DOTS_H - DOT) / 2
        dot_views = []
        for _k in range(len(pages)):
            dv = NSView.alloc().initWithFrame_(NSMakeRect(0, dot_y, DOT, DOT))
            dv.setWantsLayer_(True)
            dv.layer().setCornerRadius_(DOT / 2)
            dv.layer().setBackgroundColor_(white.CGColor())
            ve.addSubview_(dv)
            dot_views.append(dv)

        def set_dots(i, animate=False):
            from AppKit import NSAnimationContext
            NSAnimationContext.beginGrouping()
            NSAnimationContext.currentContext().setDuration_(FADE_S if animate else 0.0)
            NSAnimationContext.currentContext().setTimingFunction_(motion.ca_timing())
            for dv, (x, w, a) in zip(dot_views, dots(i, len(pages))):
                tgt = dv.animator() if animate else dv
                tgt.setFrame_(NSMakeRect(x, dot_y, w, DOT))
                tgt.setAlphaValue_(a)
            NSAnimationContext.endGrouping()

        def button(text, x, w, *, alpha, primary=False, secondary=False, key=""):
            b = panel.make_button(NSMakeRect(x, fy, w, FOOT_H))
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
            elif secondary:
                panel.style_secondary(b, text)
            if key:
                b.setKeyEquivalent_(key)
            ve.addSubview_(b)
            return b

        skip = button("跳过", PAD - 8, 56.0, alpha=TERTIARY_A, key="\x1b")
        nxt = button("下一步", WIDTH - PAD - 96.0, 96.0, alpha=1.0, primary=True, key="\r")
        back = button("上一步", WIDTH - PAD - 96.0 - 8.0 - 76.0, 76.0, alpha=SECONDARY_A, secondary=True)
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
            # ⚠️ 图标、标题**钉死不动**，正文顶对齐（iOS「What's New」的做法；2026-10-13 评审）。
            #    原来矮的页整组往下居中 → 翻页时标题上下跳最多 48pt，眼睛每页都要重新找标题。
            off = 0.0
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
                t.setTimingFunction_(motion.ca_timing())
                content.layer().addAnimation_forKey_(t, "page")
            fill(i)
            set_dots(i, animate=animate and not _reduce_motion())
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
                "page": lambda: state["i"], "_targets": targets,
                "_title": title, "_badge": badge, "_dots": dot_views}
    except objc_own.ObjcNameCollision:
        raise                         # 程序缺陷，不吞（同 whatsnew.build 那条）
    except Exception:                                     # noqa: BLE001
        if os.environ.get("CLASSLIVE_DEBUG"):
            import traceback
            traceback.print_exc()
        return None
