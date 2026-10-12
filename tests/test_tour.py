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
        # 百分比必须能从「旧 → 新」那一栏算回来（防止只改了一边）
        import re
        for big, lab, sub in tour.STATS:
            a, b = [float(x) for x in re.findall(r"[\d.]+", sub)[-2:]]
            self.assertAlmostEqual(round((a - b) / a * 100), int(big.rstrip("%")), delta=1, msg=f"{lab}: {sub}")

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
        on, off = tour.DOT_ON, tour.DOT_OFF
        self.assertEqual(tour.dots(0, 4), [on, off, off, off])
        self.assertEqual(tour.dots(3, 4), [off, off, off, on])
        self.assertGreater(on - off, 0.5, "当前页要一眼分得出")

    def test_short_pages_are_centred(self):
        self.assertEqual(tour.body_offset(66, 66), 0)
        self.assertEqual(tour.body_offset(44, 66), 11)
        self.assertEqual(tour.body_offset(45, 66), float(round(21 / 2)), "整数坐标")

    def test_button_hierarchy(self):
        # 主 > 次 > 弱：「跳过」不能和「下一步」一样重（2026-10-12 评审）
        self.assertGreater(tour.PRIMARY_BG, tour.SECONDARY_A)
        self.assertGreater(tour.SECONDARY_A, tour.TERTIARY_A)


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

    def test_skip_reports_not_finished(self):
        self.card["close"]()
        self.assertEqual(self.done, [False])


if __name__ == "__main__":
    unittest.main(verbosity=1)
