#!/usr/bin/env python3
"""右栏实时总结的真窗截图（假数据，不跑 main、不写 sessions/）。

  ClassLive.app/Contents/MacOS/python scripts/probe_summary_pane.py [chapters.json]

产出：临时目录下 split-closed / split-open / split-interim 三张图。
显示器睡着时 screencapture 是黑的 → 改用视图自绘（cacheDisplayInRect），磨砂材质可能不出，但文字与布局照样看得清。
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from probe_caption_ui import make, draw, close  # noqa: E402
from probe_hint import _pump  # noqa: E402
from probe_motion import isolated_window_file  # noqa: E402

OUT = pathlib.Path(tempfile.mkdtemp(prefix="summary_pane_"))

SAMPLE = [
    {"id": 1, "status": "final", "t0": "10:03:10", "title": "Silos", "title_zh": "部门壁垒",
     "para": {"lead": {"zh": "组织越大越容易形成部门壁垒：各部门只盯自己的指标，信息不往外流。",
                       "bold": ["部门壁垒"]},
              "body": [{"zh": "课上的例子是索尼：十几个事业部各做各的随身听，没人共享技术。", "bold": ["索尼"]},
                       {"zh": "代价是重复投入、反应变慢，最后被更小的对手抢先。", "bold": []}]}},
    {"id": 2, "status": "final", "t0": "10:09:40", "title": "Cross-functional teams", "title_zh": "跨部门团队",
     "para": {"lead": {"zh": "打破壁垒的常用办法是跨部门团队，但前提是有一个共同的、可量化的目标。",
                       "bold": ["跨部门团队", "可量化"]},
              "body": [{"zh": "没有共同目标时，成员仍按原部门的考核行事，团队名存实亡。", "bold": []}]}},
    {"id": 3, "status": "interim", "t0": "10:16:05", "title": "Incentives", "title_zh": "激励设计",
     "sentences": [{"zh": "讲者开始讨论奖金该按个人还是按团队发。"}]},
]


def snap(o, name: str) -> None:
    o._panel.orderFrontRegardless()
    _pump(o, 0.05)
    v = o._panel.contentView()
    rep = v.bitmapImageRepForCachingDisplayInRect_(v.bounds())
    v.cacheDisplayInRect_toBitmapImageRep_(v.bounds(), rep)
    from AppKit import NSBitmapImageFileTypePNG
    data = rep.representationUsingType_properties_(NSBitmapImageFileTypePNG, {})
    p = OUT / f"{name}.png"
    data.writeToFile_atomically_(str(p), True)
    f = o._panel.frame()
    print(f"{name}: {p}  panel={f.size.width:.0f}x{f.size.height:.0f}  caption_w={o._width:.0f}  pane_w={o._pane_w:.0f}")


def geometry(o) -> int:
    """右栏开着时：字幕区、输入框、顶栏最右按钮都不越过字幕栏右边界 `o._width`；右栏在它右边。"""
    from AppKit import NSMaxX
    cap = o._width
    views = {"input": o._input, "gloss": o._gloss_lbl}
    for i, b in enumerate(o._bar):
        if not b.isHidden():
            views[f"bar{i}"] = b
    bad = 0
    for k, v in views.items():
        mx = NSMaxX(v.frame())
        if mx > cap + 0.5:
            print(f"  ❌ {k} maxX={mx:.0f} > 字幕栏 {cap:.0f}")
            bad += 1
    px = o._pane.scroll.frame().origin.x
    print(f"  字幕栏 {cap:.0f} · 右栏起点 {px:.0f} · history={len(o._history)} live_visible={o._tv._live_visible}")
    return bad + (0 if px >= cap else 1)


def main() -> int:
    chapters = json.loads(pathlib.Path(sys.argv[1]).read_text()) if len(sys.argv) > 1 else SAMPLE
    bad = 0
    with isolated_window_file():
        o = make()
        o.finalize("Big organisations tend to develop silos.", "大公司往往会形成部门壁垒。")
        o.finalize("Each unit optimises its own numbers.", "每个部门只优化自己的数字。")
        draw(o)
        for c in chapters[:-1]:
            o.summary_update({"kind": "chapter", "chapter": c})
        draw(o)
        snap(o, "1-split-closed")
        o.finalize("And this is a deliberately long sentence that should wrap across several lines inside the caption column so we can see whether anything spills into the summary pane on the right.",
                   "这是一句故意写得很长的句子，应该在字幕栏里折成好几行，用来检查有没有东西溢进右边的总结栏。")
        draw(o)
        w_before, x_before = o._panel.frame().size.width, o._panel.frame().origin.x
        o._outline_new = True
        o._on_chapter_click()
        draw(o)
        snap(o, "2-split-open")
        bad += geometry(o)
        bad += 0 if o._outline_new is False else 1
        print(f"  小圆点打开即熄：{o._outline_new is False}")
        o.summary_update({"kind": "chapter", "chapter": chapters[-1]})
        draw(o)
        snap(o, "3-split-new-interim")
        # 纲要并进右栏（2026-11-17）：课务 / 断档 / ❓ 都要在右栏里出现
        c0, c1 = chapters[0], chapters[min(1, len(chapters) - 1)]
        o._outline["marks"].append(str(c0.get("t0"))[:6] + "59")          # 落在第一章里
        o.summary_update({"kind": "deadline", "t": c0.get("t0"), "quote": "Problem set 3 is due next Friday",
                          "zh": "第三次习题下周五交", "src": [1], "source": "regex", "changed": False})
        o.summary_update({"kind": "gap", "t_from": c1.get("t0"), "t_to": c1.get("t0"), "state": "dropped"})
        draw(o)
        snap(o, "3b-split-outline-merged")
        o._pane.text.scrollToBeginningOfDocument_(None)
        draw(o)
        snap(o, "3c-split-outline-top")
        txt = str(o._pane.pin.string()) + str(o._pane.text.string())
        pf, sf = o._pane.pin.frame(), o._pane.scroll.frame()
        ok = pf.size.height > 0 and abs((sf.origin.y + sf.size.height) - pf.origin.y) < 0.5
        print(f"  课务固定在顶上、滚动区紧贴其下：{ok}  (pin h={pf.size.height:.0f})")
        bad += 0 if ok else 1
        for want in ("课务", "第三次习题下周五交", "离线", "标记过"):
            ok = want in txt
            print(f"  右栏含「{want}」：{ok}")
            bad += 0 if ok else 1
        o.summary_update({"kind": "chapter", "chapter": dict(chapters[-1], status="final", id=999)})
        print(f"  右栏开着来新正式章 → 小圆点不亮：{o._outline_new is False}")
        bad += 0 if o._outline_new is False else 1
        o._on_chapter_click()
        draw(o)
        snap(o, "4-split-closed-again")
        f = o._panel.frame()
        ok = abs(f.size.width - w_before) < 0.5 and abs(f.origin.x - x_before) < 0.5
        print(f"  关上后宽度/位置还原：{ok}  ({f.size.width:.0f}@{f.origin.x:.0f} vs {w_before:.0f}@{x_before:.0f})")
        bad += 0 if ok else 1
        # 菜单项「实时总结」开的也是右栏；切到不调模型的档右栏自动关
        o._mi_outline.target().clicked_(None) if o._mi_outline is not None else o._toggle_split()
        draw(o)
        ok = o._split_on and not o._outline_on
        print(f"  菜单项打开的是右栏（不是旧纲要）：{ok}")
        bad += 0 if ok else 1
        o.set_trans_mode("raw")
        draw(o)
        ok = not o._split_on and o._pane_w == 0
        print(f"  切到纯转录档右栏自动关：{ok}")
        bad += 0 if ok else 1
        o.set_trans_mode("both")
        close(o)
    print(OUT)
    print("❌ 有判据没过" if bad else "✅ 几何 / 小圆点 / 还原 全过")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
