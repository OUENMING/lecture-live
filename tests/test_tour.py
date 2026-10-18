#!/usr/bin/env python3
"""`tour.py`（功能介绍）的判据：页数 / 行数 / 文案放得下 / 翻页与关闭的行为。

    ClassLive.app/Contents/MacOS/python tests/test_tour.py

不写任何文件、不跑 main。要窗口服务器（建一张真卡片，不显示在用户面前太久）。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tour


def _pump(dt: float = 0.02) -> None:
    """跑一拍 run loop —— 卡片收放是 60fps 自己驱动的 `callLater`，得排空 NSApp 事件队列。

    ⚠️ 别用 `NSRunLoop.runMode_beforeDate_`：没有输入源时它**立刻返回**（实测 0.00s），
       动画一拍都不走（2026-10-18 第一版这么写、判据当场红）。
    """
    from AppKit import NSApp, NSDate, NSDefaultRunLoopMode
    ev = NSApp.nextEventMatchingMask_untilDate_inMode_dequeue_(
        2 ** 30, NSDate.dateWithTimeIntervalSinceNow_(dt), NSDefaultRunLoopMode, True)
    if ev:
        NSApp.sendEvent_(ev)


def _pump_until(pred, budget: float = 2.0) -> bool:
    """一直跑到 `pred()` 为真（或超时）。"""
    import time
    end = time.monotonic() + budget
    while time.monotonic() < end:
        if pred():
            return True
        _pump()
    return bool(pred())


class Content(unittest.TestCase):
    def test_short_enough_to_be_finished(self):
        # Chameleon：3 步 72% → 4 步 45% → 7 步 16%；前 1% 没有超过 5 步的
        self.assertTrue(3 <= len(tour.PAGES) <= 5, len(tour.PAGES))
        for sym, title, lines in tour.PAGES:
            if tour.is_stats(lines):
                self.assertTrue(1 <= len(lines) <= 6, title)
            else:
                self.assertTrue(1 <= len(lines) <= 3, title)

    def test_every_line_fits_on_one_line(self):
        from AppKit import NSFont, NSString
        font = NSFont.systemFontOfSize_weight_(13.0, tour._W)
        room = tour.WIDTH - 2 * tour.PAD
        for _, title, lines in tour.PAGES:
            if tour.is_stats(lines):
                continue
            for ln in lines:
                w = NSString.stringWithString_(ln).sizeWithAttributes_({"NSFont": font}).width
                self.assertLess(w, room, f"「{ln}」{w:.0f}pt 放不下（{room:.0f}pt）")

    def test_stats_fit_their_tiles(self):
        from AppKit import NSFont, NSString
        tile_w = (tour.WIDTH - 2 * tour.PAD - (tour.TILE_COLS - 1) * tour.TILE_GAP) / tour.TILE_COLS
        for (big, lab, sub) in tour.STATS:
            for txt, font in ((big, NSFont.systemFontOfSize_weight_(24.0, tour._SEMI)),
                              (lab, NSFont.systemFontOfSize_weight_(12.0, tour._W)),
                              (sub, NSFont.systemFontOfSize_weight_(10.5, tour._W))):
                w = NSString.stringWithString_(txt).sizeWithAttributes_({"NSFont": font}).width
                self.assertLess(w, tile_w - 8, f"「{txt}」{w:.0f}pt 放不下格子（{tile_w:.0f}pt）")
        w = NSString.stringWithString_(tour.STATS_NOTE).sizeWithAttributes_(
            {"NSFont": NSFont.systemFontOfSize_weight_(10.5, tour._W)}).width
        self.assertLess(w, tour.WIDTH - 2 * tour.PAD)

    def test_stats_percent_matches_before_after(self):
        # 百分比必须能从「旧 → 新」那一栏算回来（防止只改了一边）。
        # ⚠️ 3.10：只有 %-格能这么验 —— 「0.9 秒」「省电」那两格不是百分比（前者是一句实测、
        #    后者是估算），它们不该带「旧 → 新」那一栏。
        import re
        for big, lab, sub in tour.STATS:
            if not big.endswith("%"):
                self.assertNotIn("→", sub, f"{lab}: 非百分比格不该有「旧 → 新」")
                continue
            a, b = [float(x) for x in re.findall(r"[\d.]+", sub)[-2:]]
            self.assertAlmostEqual(round((a - b) / a * 100), int(big.rstrip("%")), delta=1, msg=f"{lab}: {sub}")

    def test_page_height_fits_content(self):
        # ⭐ 卡片高度 = **这一页**的内容 + 固定留白（作者 2026-10-18：卡片跟着内容收，
        #    三行字的页不再背数字页留下的 122pt 空带）。
        # ⚠️ 变异验证：`page_height` 改回按最高页取 -> 这条红。
        chrome = tour.page_height(("x", "t", ()))
        top_chrome = tour.PAD + tour.BADGE + 14 + tour.TITLE_H + 8
        for sym, ttl, lines in tour.PAGES:
            self.assertAlmostEqual(tour.page_height((sym, ttl, lines)),
                                   chrome + tour.body_h(lines), delta=0.01, msg=ttl)
            # 裁剪线（`FOOT_ZONE`）与高度公式里脚注下面那一段必须**同一个式子**
            # （改一处忘另一处 -> 红；见 `test_page_content_is_clipped_at_the_footer`）
            self.assertAlmostEqual(tour.page_height((sym, ttl, lines)),
                                   top_chrome + tour.body_h(lines) + tour.FOOT_ZONE,
                                   delta=0.01, msg=ttl)
        self.assertLess(tour.page_height(("x", "t", ("一行",))),
                        tour.page_height(("x", "t", ("一行", "两行"))), "行少的页要更矮")
        self.assertLess(tour.page_height(tour.PAGES[1]), tour.page_height(tour.PAGES[0]),
                        "文字页（三行）要比数字页（2×2 格）矮")
        self.assertLessEqual(tour.page_height(tour.PAGES[0]), tour.height(tour.PAGES),
                             "没有哪一页能高过画布")

    def test_quoted_labels_exist_in_the_app(self):
        # tour.py 文件头规矩：**文案里提到的每个按钮 / 符号都要在界面上真的存在**（别照抄 README）。
        # 这里把文案里「…」的标签抠出来逐个到 app 源码里找 —— 找不到就是编了 UI。
        # ⚠️ 变异验证：把某条文案里的「课前回顾」改成「上节课回顾」-> 这条红。
        import pathlib
        import re
        root = pathlib.Path(__file__).resolve().parent.parent
        src = ""
        for f in sorted(root.glob("*.py")):
            if f.name == "tour.py":          # 文案自己不算「界面上存在」
                continue
            src += f.read_text(encoding="utf-8", errors="replace")
        labels = set()
        for _, _t, lines in tour.PAGES:
            if tour.is_stats(lines):
                continue
            labels.update(re.findall(r"「([^」]+)」", " ".join(lines)))
        self.assertTrue(labels, "文案里一个「」标签都没有？")
        for lb in sorted(labels):
            self.assertIn(lb, src, f"「{lb}」在 app 源码里找不到 —— 别编 UI")
        # 没打引号、但同样是「界面上该有的东西」：顶栏三档模式的逐字名（`overlay.TRANS_MODE_TITLE`）。
        for m in ("双语", "只英·校", "纯转录"):
            self.assertIn(m, src, f"模式名「{m}」在 app 源码里找不到")

    def test_no_colour_emoji_and_no_removed_buttons(self):
        txt = "".join(t + "".join("".join(x) if not isinstance(x, str) else x for x in ls)
                      for _, t, ls in tour.PAGES)
        for bad in ("⭐", "❓", "💡", "🎯"):   # ⭐ 按钮 2026-09-29 已合并进问号；彩色 emoji 改不了色
            self.assertNotIn(bad, txt)

    def test_symbols_exist(self):
        from AppKit import NSImage
        for sym, title, _ in tour.PAGES:
            self.assertIsNotNone(NSImage.imageWithSystemSymbolName_accessibilityDescription_(sym, title), sym)

    def test_dots(self):
        # 当前页是胶囊，其余是圆点；整排居中、间距一致（2026-10-13 胶囊进度点）
        for i in (0, 2, 4):
            d = tour.dots(i, 5)
            self.assertEqual([w for _, w, _ in d],
                             [tour.DOT_PILL if k == i else tour.DOT for k in range(5)])
            self.assertEqual([a for _, _, a in d],
                             [tour.DOT_ON if k == i else tour.DOT_OFF for k in range(5)])
            left, right = d[0][0], d[-1][0] + d[-1][1]
            self.assertLessEqual(abs(left - (tour.WIDTH - right)), 1.0, "整排要居中")
            for (x0, w0, _), (x1, _, _) in zip(d, d[1:]):
                self.assertEqual(x1 - x0 - w0, tour.DOT_GAP)
        self.assertGreater(tour.DOT_ON - tour.DOT_OFF, 0.5, "当前页要一眼分得出")

    def test_button_hierarchy(self):
        # 主 > 次 > 弱：「跳过」不能和「下一步」一样重（2026-10-12 评审）
        self.assertGreater(tour.PRIMARY_BG, tour.SECONDARY_A)
        self.assertGreater(tour.SECONDARY_A, tour.TERTIARY_A)


class Trigger(unittest.TestCase):
    """弹不弹：新用户 / 升级到带新介绍的版本弹一次；看过这一版、或标记更新（降级）不弹。"""

    def test_should_show(self):
        v = tour.TOUR_VERSION
        self.assertTrue(tour.should_show(""), "新用户（没有标记）要弹")
        self.assertTrue(tour.should_show("3.9.3"), "看过更旧的介绍 = 升级，要弹")
        self.assertFalse(tour.should_show(v), "看过这一版不再弹")
        self.assertFalse(tour.should_show(v + "\n"), "标记带换行也认")
        self.assertFalse(tour.should_show("99.0.0"), "标记比介绍新（降级回来）不弹")

    def test_maybe_show_marks_once(self):
        # ⚠️ 写端隔离：标记写进临时目录，不碰真的 ~/.classlive/
        import tempfile
        import pathlib
        import entry_panel as EP
        import paths
        from AppKit import NSApplication
        NSApplication.sharedApplication()
        root = pathlib.Path(tempfile.mkdtemp())
        try:
            self.assertTrue(EP.maybe_show_tour(root=root), "第一次要弹")
            self.assertEqual(paths.tour_seen(root=root).read_text(), tour.TOUR_VERSION,
                             "一弹出来就记下（用户可能直接开课、不等看完）")
            # 改坏：把 maybe_show_tour 里的 should_show 判断去掉 -> 这条红（每次打开都弹）
            self.assertFalse(EP.maybe_show_tour(root=root), "第二次不弹")
        finally:
            c = EP.S.get("tour")
            if c:
                c["close"]()


class Behaviour(unittest.TestCase):
    def setUp(self):
        from AppKit import NSApplication
        NSApplication.sharedApplication()
        self.done = []
        self.card = tour.build(on_done=self.done.append)
        self.assertIsNotNone(self.card)

    def tearDown(self):
        self.card["close"]()

    def test_next_back_and_finish(self):
        c = self.card
        c["back"]()
        self.assertEqual(c["page"](), 0, "第一页上一步不动")
        for _ in range(len(tour.PAGES) - 1):
            c["next"]()
        self.assertEqual(c["page"](), len(tour.PAGES) - 1)
        c["back"]()
        self.assertEqual(c["page"](), len(tour.PAGES) - 2)
        c["next"](); c["next"]()
        self.assertEqual(self.done, [True], "最后一页再下一步 = 看完")
        c["close"]()
        self.assertEqual(self.done, [True], "关两次只回调一次")

    def test_header_does_not_move_between_pages(self):
        # ⭐ 图标和标题翻页不动（2026-10-13：原来矮的页整组下移居中，标题上下跳最多 48pt）
        c = self.card
        pos = []
        for _ in range(len(tour.PAGES)):
            pos.append((c["_title"].frame().origin.y, c["_badge"].frame().origin.y))
            c["next"]() if c["page"]() < len(tour.PAGES) - 1 else None
        self.assertEqual(len(set(pos)), 1, pos)

    def test_top_edge_fixed_and_height_per_page(self):
        # ⭐ 翻页：**顶边不动**（标题屏幕位置不跳）、高度 = 那一页的 `page_height`（底边收放）。
        #    ⚠️ 收放是 60fps 自己驱动的 —— 每次翻页 pump 到走完（`motion.MOVE_S` + 余量）；
        #       中间再抓一次顶边，钉住「动画途中也不跳」。
        # ⚠️ 变异验证：`_fit` 里 `top` 改取底边（origin 不动、只改高）-> 这条红。
        c = self.card
        tops, tys = [], []
        for i in range(len(tour.PAGES)):
            f = c["panel"].frame()
            tops.append(round(f.origin.y + f.size.height, 1))
            self.assertAlmostEqual(f.size.height, tour.page_height(tour.PAGES[i]), delta=0.5,
                                   msg=f"第 {i + 1} 页高度要按内容算")
            r = c["_title"].convertRect_toView_(c["_title"].bounds(), None)   # -> 窗口坐标
            tys.append(round(f.origin.y + r.origin.y, 1))
            if i < len(tour.PAGES) - 1:
                c["next"]()
                _pump(0.04); _pump(0.04)         # 动画途中（~0.08s，60fps 已走了几帧）
                f = c["panel"].frame()
                self.assertAlmostEqual(f.origin.y + f.size.height, tops[0], delta=1.0,
                                       msg=f"第 {i + 2} 页收放途中顶边跳了")
                self.assertTrue(
                    _pump_until(lambda: abs(c["panel"].frame().size.height
                                            - tour.page_height(tour.PAGES[i + 1])) < 0.5),
                    f"第 {i + 2} 页收放没走到位（还差 "
                    f"{c['panel'].frame().size.height - tour.page_height(tour.PAGES[i + 1]):.0f}pt）")
        self.assertEqual(len(set(tops)), 1, f"顶边要钉住: {tops}")
        self.assertEqual(len(set(tys)), 1, f"标题屏幕位置要钉住: {tys}")
        self.assertLess(c["panel"].frame().size.height, tour.height(tour.PAGES),
                        "最后一页（三行）不该还是画布那么高")

    def test_page_content_is_clipped_at_the_footer(self):
        # 收放动画途中内容滑过裁剪线要被**裁掉**（不钻到进度点 / 按钮底下，2026-10-18 拍到过）：
        # 裁剪层存在、开着 masksToBounds、下沿正好在 `FOOT_ZONE`、顶边贴住窗口顶边。
        # ⚠️ 变异验证：去掉 `setMasksToBounds_(True)` -> 这条红。
        clip = self.card["_clip"]
        self.assertTrue(clip.layer().masksToBounds(), "裁剪层要开着 masksToBounds")
        f = clip.frame()
        self.assertAlmostEqual(f.origin.y, tour.FOOT_ZONE, delta=0.5, msg="裁剪线下沿")
        self.assertAlmostEqual(f.origin.y + f.size.height,
                               self.card["panel"].frame().size.height, delta=0.5,
                               msg="裁剪层顶边要贴住窗口顶边（内容从顶对齐）")

    def test_current_dot_is_the_pill(self):
        c = self.card
        c["next"]()
        ws = [dv.frame().size.width for dv in c["_dots"]]
        self.assertEqual(ws.index(max(ws)), 1, ws)

    def test_skip_reports_not_finished(self):
        self.card["close"]()
        self.assertEqual(self.done, [False])


if __name__ == "__main__":
    unittest.main(verbosity=1)
