"""往回翻的**视口归属**判据（2026-10-17 修「往上翻被拽回底部 / 底下闪」）。

作者原话：「当教授正在讲、字幕在正常转录的时候，我往上翻，它（底下）会非常闪烁、
乱，就是一直想把我拉回到最底下那种感觉。」

修的是三条（探针 `scripts/probe_scroll_flash.py` 修前两种布局都红）：
  ① **钉底可以和手势抢** —— 程序滚动落在「用户把视口滚走」与「下一个 tick」之间时，
     钉底把视口拽回 0 并把 `_expected_origin` 一起写成 0 → tick 读不出"用户动过"
     → 用户的滚动被吞、跟随永远翻不了假。
  ② **浏览保护只数定稿追加** —— 实时行显形/消失改行数但不记 `_pending_appends`，
     老内容整体挪一行而视口不补 → 视线朝最新那边跳一行（每句一次，定稿时又反向
     跳回）＝"底下很闪"。
  ③ **闲置 6s 自动回底** —— 读着读着整屏被拽走。

判据（每条都钉住一个**用户可见**的不变量）:
  A. 用户在 paint 之前滚走 -> paint **不许**把视口拽回底，跟随必须翻假
  B. 浏览态下喂一整句（草稿→流式→定稿）-> 视口顶那一行**一行都不许动**
  C. 无人输入、时间过了一分钟 -> 视口不许自己回底（跟随不许翻真）
  D. 用户自己滚回底 -> 跟随翻真（不变量，防修过头）
  E. 钉底只吸收零头（橡皮筋 / 亚像素）
  F. 「↓ N 句新的」：往回翻 + 有新定稿才出现、数得对、点它回到底部
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import transcript_view as tvm                                            # noqa: E402


class ScrollFollow(unittest.TestCase):
    def _overlay(self, rows: int = 60):
        try:
            from overlay import Overlay
        except Exception as e:                       # noqa: BLE001
            self.skipTest(f"AppKit 不可用, 跳过: {type(e).__name__}: {e}")
        o = Overlay()
        self.addCleanup(o.close)
        for i in range(rows):
            o.finalize(f"line {i} of the lecture", f"第{i}句：课堂内容。")
        self._settle(o)
        return o

    def _settle(self, o):
        for _ in range(8):
            o.pump()
        o._mark_dirty(urgent=True)
        for _ in range(4):
            o.pump()

    # ---- 工具 ----
    @staticmethod
    def _clip(o):
        return o._tv._scroll.contentView()

    def _origin(self, o) -> float:
        return float(self._clip(o).bounds().origin.y)

    def _gaze(self, o) -> int:
        """视口顶那一行在历史里的序号（`count-1-level`）—— 读者眼睛盯的那一行。"""
        tv = o._tv
        lv = int(self._origin(o) // tv._row_h)
        return tv._count() - 1 - lv

    def _user_scrolls_to(self, o, y: float) -> None:
        """把 clip 摆到 y = 用户滚过（对状态机而言与真手势同一条路）。"""
        self._clip(o).scrollToPoint_((0.0, float(y)))

    def _one_sentence(self, o, i: int = 0) -> None:
        """喂一整句（草稿逐词 → 草稿译文 → 定稿流式 → finalize），中间照常 pump。"""
        o.add_draft(f"so the monopolist chooses output number {i}")
        o.draft_zh(f"所以垄断者选择产量第{i}句")
        self._settle(o)
        o.seed_en(f"so the monopolist chooses output number {i}")
        for k in range(0, 24, 2):
            o.stream_zh(f"所以垄断者选择产量第{i}句"[k:k + 2])
            o.pump()
        o.finalize(f"So the monopolist chooses output number {i}.",
                   f"所以垄断者选择产量第{i}句。")
        self._settle(o)

    # ---- A. 钉底不许和手势抢 ----
    def test_paint_never_yanks_a_user_scroll(self):
        """用户在 paint 之前滚走（`_wait_interruptible` 那条派发路径的形状）:
        paint 必须认账交还，**不许**把视口拽回 0，跟随必须翻假。

        ⚠️ 变异验证：把 `_paint` 里的钉底改回无脑 `scrollToPoint_((0.0, 0.0))`
        （或删掉 `_sync_user_origin` 那一次调用）-> 这条红。"""
        o = self._overlay()
        tv = o._tv
        self.assertTrue(tv.following)
        self._user_scrolls_to(o, 300.0)          # 用户滚走，**还没** tick
        o.finalize("a brand new sentence arrives", "一句新的定稿到达。")   # 立刻触发 paint
        self.assertFalse(tv.following, "用户在 paint 之前滚走了，跟随必须翻假")
        self.assertGreaterEqual(self._origin(o), 300.0,
                                "paint 把用户滚走的视口拽回去了（＝和手势抢）")
        tv.tick()                                 # 紧接着的 tick 也不许反悔
        self.assertFalse(tv.following)
        self.assertGreaterEqual(self._origin(o), 300.0)

    # ---- B. 浏览态：视线一行都不许动 ----
    def test_gaze_is_anchored_while_browsing(self):
        """往回翻以后喂一整句：视口顶那一行必须**原地不动**。

        行数变了几行（定稿追加 / 实时行显形）就得补几行 —— 只数定稿追加会在
        实时行显形那一帧把视线往最新那边拽一行。
        ⚠️ 变异验证：把浏览保护改回只按 `_pending_appends`（只数定稿追加）-> 这条红。"""
        o = self._overlay()
        tv = o._tv
        self._user_scrolls_to(o, 500.0)
        tv.tick()
        self.assertFalse(tv.following)
        base = self._gaze(o)
        y0 = self._origin(o)

        o.add_draft("so the monopolist chooses output")     # 实时行显形（行数 +1）
        self._settle(o)
        self.assertEqual(self._gaze(o), base,
                         "实时行显形把视线拽走了一行")
        self.assertAlmostEqual(self._origin(o), y0 + tv._row_h, delta=0.6,
                               msg="行数 +1 就该补一个行高")
        o.draft_zh("所以垄断者选择产量")
        o.seed_en("so the monopolist chooses output")
        for k in range(0, 24, 2):
            o.stream_zh("所以垄断者选择产量"[k:k + 2])
            o.pump()
        self.assertEqual(self._gaze(o), base, "流式期间视线漂了")
        o.finalize("So the monopolist chooses output.", "所以垄断者选择产量。")
        self._settle(o)
        self.assertEqual(self._gaze(o), base, "定稿那一下视线漂了")

    # ---- C. 不许定时回底 ----
    def test_no_timed_return_to_bottom(self):
        """往回翻 + 有新定稿 + 时间过了一分钟：视口不许自己回底。

        ⚠️ 变异验证：把「闲置 `IDLE_S` 秒回底」那条规则加回 `tick` -> 这条红。"""
        o = self._overlay()
        tv = o._tv
        self._user_scrolls_to(o, 500.0)
        tv.tick()
        self.assertFalse(tv.following)
        o.finalize("something new while you read", "你读的时候又讲了一句。")
        self._settle(o)
        y = self._origin(o)
        self.assertGreater(y, 100.0)

        real = tvm.time.monotonic
        now = [real()]
        tvm.time.monotonic = lambda: now[0]          # 时钟可注入：一分钟 = 几毫秒
        try:
            for _ in range(60):
                now[0] += 1.0
                tv.tick()
        finally:
            tvm.time.monotonic = real
        self.assertFalse(tv.following, "一分钟没人碰它，跟随不该自己翻真")
        self.assertAlmostEqual(self._origin(o), y, delta=1.0,
                               msg="一分钟没人碰它，视口不该自己动")

    # ---- D/E. 不变量：防修过头 ----
    def test_user_bottom_resumes_following(self):
        """用户自己滚回底 -> 跟随翻真（回底只由用户触发的那一半）。

        ⚠️ 要两拍（与改前逐字同一条时序）: 第一拍先把「滚到底」**检测成用户滚动**
        （`_sync_user_origin` 接手后 tick 直接返回），下一拍没有新位移、且 origin
        在 `BOTTOM_EPS` 以内 → 才翻真。真身每帧都 tick（≥120Hz），差这一拍看不见。"""
        o = self._overlay()
        tv = o._tv
        self._user_scrolls_to(o, 400.0)
        tv.tick()
        self.assertFalse(tv.following)
        self._user_scrolls_to(o, 0.0)
        tv.tick()
        tv.tick()
        self.assertTrue(tv.following, "用户滚到底了，跟随该回来")

    def test_pin_absorbs_only_slack(self):
        """跟随态：橡皮筋 / 亚像素的零头被吸收，但**更远的位置**一律交还用户。"""
        o = self._overlay()
        tv = o._tv
        self._user_scrolls_to(o, 2.0)               # BOTTOM_EPS 以内的零头
        o._mark_dirty(urgent=True)
        self.assertAlmostEqual(self._origin(o), 0.0, delta=0.5,
                               msg="零头没被钉底吸收")
        self.assertTrue(tv.following)
        self._user_scrolls_to(o, 200.0)             # 明确滚走
        o._mark_dirty(urgent=True)
        self.assertFalse(tv.following)
        self.assertGreaterEqual(self._origin(o), 200.0, "钉底把视口拽回去了")

    # ---- F. 「↓ N 句新的」 ----
    def test_newline_pill_counts_and_jumps_back(self):
        o = self._overlay()
        tv = o._tv
        pill = o._btn_new
        self.assertTrue(pill.isHidden(), "在底部时不该有提示")
        self._user_scrolls_to(o, 500.0)
        tv.tick()
        self._settle(o)
        self.assertTrue(pill.isHidden(), "刚滚上去、还没有新句 -> 不出现")
        o.finalize("first new sentence", "第一句新的。")
        self._settle(o)
        o.finalize("second new sentence", "第二句新的。")
        self._settle(o)
        self.assertEqual(tv.new_since_leave, 2)
        self.assertFalse(pill.isHidden(), "攒了两句新的却没提示")
        self.assertIn("2", pill.title())
        self.assertIn("↓", pill.title())
        o._to_latest()                              # 点它（= 按钮的 action）
        self._settle(o)
        self.assertTrue(tv.following, "点了提示就该回到底部")
        self.assertAlmostEqual(self._origin(o), 0.0, delta=0.5)
        self.assertEqual(tv.new_since_leave, 0)
        self.assertTrue(pill.isHidden(), "回底后提示该收起来")

    def test_newline_pill_is_compact_and_text_fits(self):
        """尺寸**钉住**（比模式胶囊小一档）+ 标题**装得进这个框**（宽高都量）。

        ⚠️ 这几个数是作者 2026-10-17 定的观感（「胶囊做得更紧凑一点，现在有点大」）；
        改它们必须是有意的 —— 这条用例红 = 有人顺手动了样子。
        ⚠️ 颜色 / 描边 / 圆角**不**在这里钉：它们与模式胶囊**同源**（`MODE_CHIP_*`），
        改那一套是全局决定，不该被这个小胶囊拦下。
        """
        from AppKit import NSFontAttributeName
        from Foundation import NSString
        import overlay as O
        self.assertEqual(
            (O.NEWLINE_FONT_PT, O.NEWLINE_H, O.NEWLINE_PAD, O.NEWLINE_MIN_W,
             O.NEWLINE_INSET_B),
            (12.0, 19.0, 6.0, 56.0, 4.0))
        self.assertLess(O.NEWLINE_FONT_PT, O.MODE_CHIP_FONT_PT)
        self.assertLess(O.NEWLINE_H, O.MODE_CHIP_H)
        self.assertLess(O.NEWLINE_PAD, O.MODE_CHIP_PAD)
        self.assertLess(O.NEWLINE_INSET_B, 6.0, "底边距要比原来那一版更收")

        o = self._overlay()
        pill = o._btn_new
        self.assertEqual(pill.font().pointSize(), O.NEWLINE_FONT_PT)
        attrs = {NSFontAttributeName: pill.font()}
        for n in (1, 2, 12, 128):                   # 位数变多也不许截
            title = f"↓{O.NEWLINE_ARROW_GAP}{n} 句新的"
            w = o._newline_pill_width(title)        # 与生产同一条算式
            text = NSString.stringWithString_(title).sizeWithAttributes_(attrs)
            tw, th = float(text.width), float(text.height)
            self.assertGreaterEqual(w, O.NEWLINE_MIN_W)
            self.assertLessEqual(tw, w - 2.0 * O.NEWLINE_PAD + 1.0,
                                 f"「{title}」宽过框了（会被截）")
            self.assertLessEqual(th, O.NEWLINE_H,
                                 f"「{title}」高过框了（{th:.1f} > {O.NEWLINE_H}）")
        # 摆上去之后再量一次实物：宽度用上、右边缘与转录区对齐
        self._user_scrolls_to(o, 500.0)
        o._tv.tick()
        o.finalize("one more", "又一句。")
        self._settle(o)
        self.assertFalse(pill.isHidden())
        f = pill.frame()
        self.assertAlmostEqual(f.size.height, O.NEWLINE_H, delta=0.01)
        self.assertAlmostEqual(f.origin.y, o._scroll_y + O.NEWLINE_INSET_B, delta=0.01)
        self.assertAlmostEqual(f.origin.x + f.size.width,
                               o._width - O.PAD, delta=0.01,
                               msg="右边缘该与转录区右边缘对齐")
        text = NSString.stringWithString_(pill.title()).sizeWithAttributes_(attrs)
        self.assertLessEqual(float(text.width), f.size.width - 2.0 * O.NEWLINE_PAD + 1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
