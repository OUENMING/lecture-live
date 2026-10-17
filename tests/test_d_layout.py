"""D 布局的悬浮窗判据（2026-10-17 定版; 草稿并进最底那一行 + 原地显影; 底板 10-17 A/B 后取消）。

「喂什么」在 `tests/test_caption_flow.py`（纯函数）; 这里钉「画成什么样」, 用真 `Overlay`、
注入时钟逐帧断言（套路同 `test_audit_regressions.R14b_FinalRollsIn`）。

判据:
  ① 草稿 → 同段定稿 → finalize: 行数不变、**不起跑**（原地升级, 不是新行）
  ② 新一段草稿到（上一段还在流）: 起跑; 最底那行**原地不动**、α 从 0 显影, 老行整体上推
  ③ 终态精确: 位移 0、入场 α 1、模糊摘掉
  ④ 按层降亮只在跟随态; 往回翻全亮; 没有底板
  ⑤ 实时行英文太长 → 从开头截（「…」+ 最新的词）; 进历史后恢复整句
"""
from __future__ import annotations

import os
import sys
import unittest

os.environ["CLASSLIVE_LAYOUT"] = "d"          # ⚠️ 在 import overlay 之前
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import transcript_view as tvm                  # noqa: E402


class DLayout(unittest.TestCase):
    def _overlay(self, rows: int = 3):
        try:
            import overlay
            from overlay import Overlay
        except Exception as e:                       # noqa: BLE001
            self.skipTest(f"AppKit 不可用, 跳过: {type(e).__name__}: {e}")
        self.assertTrue(overlay.LAYOUT_D)
        o = Overlay()
        self.addCleanup(o.close)
        self.now = [100.0]
        clock = lambda: self.now[0]                  # noqa: E731
        o._clock = clock
        o._tv._clock = clock
        o._tv._reduce_motion = lambda: False
        o._tv._animate_ok = lambda: True
        for i in range(rows):
            o.finalize(f"line {i}", f"第{i}行")
        self._settle(o)
        return o

    def _settle(self, o):
        self.now[0] += 1.0
        o._tv.tick()
        o._mark_dirty(urgent=True)
        self.assertFalse(o._tv.rolling)

    @staticmethod
    def _slot(tv, level):
        for s in tv._slots:
            if s["level"] == level and not s["hidden"]:
                return s
        return None

    def _y(self, tv, level):
        s = self._slot(tv, level)                    # 行底 = 两条标签里低的那条
        return min(s["zh"].frame().origin.y, s["en"].frame().origin.y)

    def test_in_place_upgrade_does_not_roll(self):
        o = self._overlay()
        tv = o._tv
        o.add_draft("Now let's look at the monopolist")
        o.draft_zh("现在看看垄断者")
        self._settle(o)                              # 草稿出现那一下的上推走完
        n0 = tv._count()
        o.seed_en("Now let's look at the monopolist")
        o.stream_zh("现在")                         # 桥: 中文仍是草稿译文
        o._mark_dirty(urgent=True)
        self.assertFalse(tv.rolling, "同段定稿开始流式 = 原地升级, 不该起跑")
        self.assertEqual(tv._count(), n0)
        self.assertEqual(self._slot(tv, 0)["zh"].stringValue(), "现在看看垄断者",
                         "桥没生效: 中文先被清成「现在」")
        o.stream_zh("我们来看看垄断者")
        o._mark_dirty(urgent=True)
        self.assertIn("垄断者", self._slot(tv, 0)["zh"].stringValue())
        self.assertEqual(tv._live_kind, "final")
        o.finalize("Now let's look at the monopolist.", "现在我们来看看垄断者。")
        o._mark_dirty(urgent=True)
        self.assertFalse(tv.rolling, "finalize 不该起跑（还是同一行）")
        self.assertEqual(tv._count(), n0)

    def test_new_segment_reveals_in_place_and_pushes_old_up(self):
        o = self._overlay()
        tv = o._tv
        o.seed_en("first segment")
        o.stream_zh("第一段的定稿")
        o._mark_dirty(urgent=True)
        self._settle(o)
        o.add_draft("second segment begins")
        o.draft_over_stream(True)
        o._mark_dirty(urgent=True)
        # 10-17 让位节奏：上一句还在流式（没译完）-> 下一段草稿先不上屏、不起跑
        self.assertFalse(tv.rolling, "上一句没译完就让位了")
        o.finalize("first segment", "第一段的定稿", clear_draft=False)
        o._mark_dirty(urgent=True)
        self.assertTrue(tv.rolling, "上一句译完、新一段草稿够 3 词却没起跑")
        self.assertAlmostEqual(self._y(tv, 0), 0.0, places=3, msg="新行不该位移")
        self.assertAlmostEqual(self._slot(tv, 0)["alpha"], 0.0, places=3, msg="新行应从 α0 显影")
        self.assertAlmostEqual(self._y(tv, 1), 0.0, places=3,
                               msg="被顶上去的那行应从原位（y=0）起跑")
        ys = []
        for _ in range(4):
            self.now[0] += tvm.motion.MOVE_S / 5
            tv.tick()
            self.assertAlmostEqual(self._y(tv, 0), 0.0, places=3)
            ys.append(self._y(tv, 1))
        self.assertEqual(ys, sorted(ys), "老行应单调上移")
        self.now[0] += tvm.motion.MOVE_S
        tv.tick()
        self.assertFalse(tv.rolling)
        self.assertAlmostEqual(self._y(tv, 1), tv._row_h, places=3, msg="终态没落在一个行高上")
        s0 = self._slot(tv, 0)
        self.assertEqual(s0["alpha"], 1.0)
        self.assertEqual(s0.get("blur", 0.0), 0.0, "模糊没摘掉")
        self.assertEqual(s0["en"].stringValue(), "second segment begins")

    def test_short_tail_joins_previous_row_only_with_soniox(self):
        """"Okay?" 离上一句 ≤3 s 封口 -> 并进上一行（行数不变）；没开 tail_merge（本地路径）-> 自成一行。"""
        for on in (True, False):
            with self.subTest(tail_merge=on):
                o = self._overlay()
                o.set_tail_merge(on)
                o.finalize("Monopoly is rare.", "垄断很少见。")
                n = len(o._history)
                self.now[0] += 1.0
                o.seed_en("Okay?")
                o.stream_zh("好吗？")
                o._mark_dirty(urgent=True)
                o.finalize("Okay?", "好吗？")
                if on:
                    self.assertEqual(len(o._history), n)
                    self.assertEqual(o._history[-1], ("Monopoly is rare. Okay?", "垄断很少见。好吗？"))
                else:
                    self.assertEqual(len(o._history), n + 1)

    def test_short_next_draft_waits_and_rechecks_after_gap(self):
        o = self._overlay()                           # 上一句定稿在 100.0，此刻 101.0
        o.add_draft("So a")
        o._mark_dirty(urgent=True)
        self.assertFalse(o._tv._live_visible, "2 个词就开了新行")
        self.assertAlmostEqual(o._flow_due, 100.0 + 3.0, places=2)   # 到点重画（停顿后 1 词也开）
        o.add_draft("So a market")
        o._mark_dirty(urgent=True)
        self.assertTrue(o._tv._live_visible)

    def test_entry_waits_for_old_row_to_clear(self):
        o = self._overlay()
        tv = o._tv
        o.add_draft("hello there everyone")         # ≥3 词才开新行（10-17 让位节奏）
        o._mark_dirty(urgent=True)
        self.assertTrue(tv.rolling)
        self.now[0] += tvm.D_ENTER_DELAY_S * 0.9
        tv.tick()
        self.assertLess(self._slot(tv, 0)["alpha"], 0.02, "新行在老行让开之前就显出来了")

    def test_dim_by_level_only_while_following(self):
        o = self._overlay(rows=4)
        tv = o._tv
        vis = {lv: self._slot(tv, lv)["alpha_vis"][0] for lv in (0, 1, 2)}
        self.assertAlmostEqual(vis[0], tvm.D_DIM[0])
        self.assertAlmostEqual(vis[1], tvm.D_DIM[1])
        self.assertAlmostEqual(vis[2], tvm.D_DIM[2])
        tv._follow = False                           # 等价于用户往回翻（tick 的那一支）
        tv._regeom()
        self.assertEqual(self._slot(tv, 1)["alpha_vis"], (1.0, 1.0), "往回翻时历史行应全亮")

    def test_no_plate(self):
        """底板 10-17 取消: 转录区父视图里不该多出任何垫底的视图。"""
        o = self._overlay()
        tv = o._tv
        self.assertFalse(hasattr(tv, "_update_plate"))
        n = len(tv._parent.subviews())
        o.add_draft("hello")
        o._mark_dirty(urgent=True)
        self._settle(o)
        self.assertEqual(len(tv._parent.subviews()), n)

    def test_live_english_is_cut_from_the_head(self):
        o = self._overlay()
        tv = o._tv
        words = " ".join(f"word{i}" for i in range(80))
        o.add_draft(words)
        o._mark_dirty(urgent=True)
        self._settle(o)
        shown = self._slot(tv, 0)["en"].stringValue()
        self.assertTrue(shown.startswith(tvm.ELLIPSIS), f"没从开头截: {shown[:30]}")
        self.assertTrue(shown.endswith("word79"), "最新的词没留住")
        o.finalize(words, "很长的一句")
        o.add_draft("next")
        o._mark_dirty(urgent=True)
        self._settle(o)
        self.assertFalse(self._slot(tv, 1)["en"].stringValue().startswith(tvm.ELLIPSIS),
                         "进了历史还在用实时行的开头截断")

    def test_new_characters_fade_in_then_settle(self):
        """流式中文新到的字从 D_TYPE_FROM 淡进来（不「跳字」）, 走完换回普通串。"""
        o = self._overlay()
        tv = o._tv
        o.seed_en("price and output")
        o.stream_zh("价格")
        o._mark_dirty(urgent=True)
        self._settle(o)
        o.stream_zh("与产量")
        o._mark_dirty(urgent=True)
        s0 = self._slot(tv, 0)
        j = tv._slots.index(s0)
        spec = tv._type.get((j, "zh"))
        self.assertIsNotNone(spec, "新字没进淡入")
        text, births = spec
        self.assertEqual(text, "价格与产量")
        self.assertEqual(births[:2], [0.0, 0.0], "旧字不该重新淡入")
        self.assertTrue(all(b > 0 for b in births[2:]), "新字没记出现时刻")
        self.assertTrue(tv.animating, "淡入期间宿主应保持热档")
        self.now[0] += tvm.D_TYPE_S + 0.01
        tv.tick()
        self.assertNotIn((j, "zh"), tv._type)
        self.assertEqual(s0["zh"].stringValue(), "价格与产量")
        self.assertFalse(tv.animating)

    def test_bridge_handover_keeps_same_chars_lit(self):
        """桥从草稿译文换到定稿译文时, 两版里相同的那几个字不该再淡一遍
        （Owen 10-17「没改词, 后半句还是刷新一下」; probe_d_layout 第 6 句就是这一下）。"""
        o = self._overlay()
        tv = o._tv
        o.add_draft("and that gap is what we call deadweight loss")
        o.draft_zh("这个差距就是无谓损失")
        self._settle(o)
        o.seed_en("and that gap is what we call deadweight loss")
        o.stream_zh("而这个差距就")                 # 6 ≥ 0.6 × 10 → 换成定稿译文
        o._mark_dirty(urgent=True)
        j = tv._slots.index(self._slot(tv, 0))
        text, births = tv._type[(j, "zh")]
        self.assertEqual(text, "而这个差距就")
        self.assertGreater(births[0], 0.0, "「而」是新字, 该淡入")
        self.assertEqual(births[1:], [0.0] * 5, "「这个差距就」两版都有, 被重新淡入了")

    def test_draft_bar_stays_empty(self):
        o = self._overlay()
        o.add_draft("a draft")
        o.draft_zh("草稿")
        o._mark_dirty(urgent=True)
        self.assertEqual(o._draft_lbl.stringValue(), "")
        self.assertEqual(o._draft_zh.stringValue(), "")


class CarryBirths(unittest.TestCase):
    """`carry_births` 纯函数: 字没变只是挪了位置（折行点 / 开头截断 / 改了别处）就不算新字。"""

    def _new(self, prev, display, **kw):
        nb = tvm.carry_births(prev, [0.0] * len(prev), display, 9.0, **kw)
        self.assertEqual(len(nb), len(display))
        return "".join(c for c, b in zip(display, nb) if b == 9.0)

    def test_line_break_moved(self):
        self.assertEqual(self._new("今天我们要看\n垄断者", "今天我们\n要看垄断者如何"), "如何")

    def test_head_truncation_shifted(self):
        self.assertEqual(self._new("… seller in the", "… in the market", min_run=3), " market")

    def test_draft_tail_revised(self):
        self.assertEqual(self._new("how a monopolist chooses output", "how a monopolist chooses",
                                   min_run=3), "")

    def test_rewrite_ignores_lone_coincidences(self):
        self.assertEqual(self._new("今天的课", "我们的书"), "我们的书")

    def test_keeps_previous_births(self):
        nb = tvm.carry_births("价格", [3.0, 4.0], "价格与", 9.0)
        self.assertEqual(nb, [3.0, 4.0, 9.0])


class HeadTruncate(unittest.TestCase):
    """`truncate_head_to_height` 是纯函数: 用假尺子（每 10 字一行, 行高 10）钉住。"""

    @staticmethod
    def _measure(s: str) -> float:
        return 10.0 * (len(s) // 10 + 1)

    def test_fits_unchanged(self):
        self.assertEqual(tvm.truncate_head_to_height("short", self._measure, 20.0), "short")

    def test_keeps_tail_with_prefix(self):
        text = "alpha beta gamma delta epsilon zeta eta theta"
        out = tvm.truncate_head_to_height(text, self._measure, 20.0)
        self.assertTrue(out.startswith(tvm.ELLIPSIS))
        self.assertTrue(out.endswith("theta"))
        self.assertLessEqual(self._measure(out), 20.0)
        self.assertEqual(text, "alpha beta gamma delta epsilon zeta eta theta")


if __name__ == "__main__":
    unittest.main()
