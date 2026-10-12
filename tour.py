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
PAGES: tuple = (
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
ICON_H = 56.0
TITLE_H = 30.0
LINE_H = 22.0
DOTS_H = 24.0
FOOT_H = 28.0
_W = 0.23              # NSFontWeightMedium，同 whatsnew / overlay
_SEMI = 0.4


def height(pages=PAGES) -> float:
    """卡片高度按**最长那一页**算 —— 翻页时窗口不跳。"""
    n = max(len(p[2]) for p in pages)
    return PAD + ICON_H + 12 + TITLE_H + 10 + n * LINE_H + 16 + DOTS_H + 8 + FOOT_H + PAD


def dots(i: int, n: int) -> str:
    """纯函数：进度点 `● ○ ○ ○`。"""
    return " ".join("●" if k == i else "○" for k in range(n))


def build(on_done=None, pages=PAGES):
    """构造并显示第 1 页。失败返回 None。`on_done(finished: bool)`：看完 = True，跳过 = False。"""
    try:
        from AppKit import (NSButton, NSColor, NSFont, NSMakeRect, NSTextAlignmentCenter,
                            NSTextField, NSWindowStyleMaskBorderless,
                            NSWindowStyleMaskNonactivatingPanel)

        h = height(pages)
        fp = panel.build(NSMakeRect(0, 0, WIDTH, h),
                         NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel)
        p, ve = fp.window, fp.glass
        p.setHasShadow_(True)

        def label(rect, size, *, alpha=1.0, weight=_W, center=False):
            lb = NSTextField.alloc().initWithFrame_(rect)
            lb.setEditable_(False); lb.setSelectable_(False)
            lb.setBezeled_(False); lb.setDrawsBackground_(False)
            lb.setFont_(NSFont.systemFontOfSize_weight_(size, weight))
            lb.setTextColor_(NSColor.whiteColor().colorWithAlphaComponent_(alpha))
            if center:
                lb.setAlignment_(NSTextAlignmentCenter)
            ve.addSubview_(lb)
            return lb

        from AppKit import NSImage, NSImageSymbolConfiguration, NSImageView
        y = h - PAD - ICON_H
        icon = NSImageView.alloc().initWithFrame_(NSMakeRect((WIDTH - ICON_H) / 2, y, ICON_H, ICON_H))
        icon.setSymbolConfiguration_(NSImageSymbolConfiguration.configurationWithPointSize_weight_(38.0, 0.0))
        icon.setContentTintColor_(NSColor.whiteColor().colorWithAlphaComponent_(0.92))
        ve.addSubview_(icon)
        y -= 12 + TITLE_H
        title = label(NSMakeRect(PAD, y, WIDTH - 2 * PAD, TITLE_H), 20.0, weight=_SEMI, center=True)
        y -= 10
        n_lines = max(len(pg[2]) for pg in pages)
        body = []
        for _ in range(n_lines):
            y -= LINE_H
            body.append(label(NSMakeRect(PAD, y, WIDTH - 2 * PAD, LINE_H), 13.0, alpha=0.88, center=True))

        fy = PAD
        dot = label(NSMakeRect(PAD, fy + FOOT_H + 8, WIDTH - 2 * PAD, DOTS_H), 11.0, alpha=0.6, center=True)

        def button(text, x, w, key=""):
            b = NSButton.alloc().initWithFrame_(NSMakeRect(x, fy, w, 28.0))
            b.setTitle_(text)
            b.setBezelStyle_(1)
            b.setFont_(NSFont.systemFontOfSize_weight_(13.0, _W))
            if key:
                b.setKeyEquivalent_(key)
            ve.addSubview_(b)
            return b

        skip = button("跳过", PAD, 72.0, "\x1b")
        back = button("上一步", WIDTH - PAD - 92.0 - 8.0 - 80.0, 80.0)
        nxt = button("下一步", WIDTH - PAD - 92.0, 92.0, "\r")
        state = {"i": 0, "closed": False}

        def show(i):
            sym, ttl, lines = pages[i]
            img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(sym, ttl)
            if img is not None:
                img.setTemplate_(True)
            icon.setImage_(img)
            title.setStringValue_(ttl)
            for k, lb in enumerate(body):
                lb.setStringValue_(lines[k] if k < len(lines) else "")
            dot.setStringValue_(dots(i, len(pages)))
            last = i == len(pages) - 1
            nxt.setTitle_("开始用" if last else "下一步")
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

        show(0)
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
