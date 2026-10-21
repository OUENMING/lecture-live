#!/usr/bin/env python3
"""D 布局「屏上该显示哪几行」的判据（纯函数 `caption_flow.caption_rows`）。

    ClassLive.app/Contents/MacOS/python tests/test_caption_flow.py

钉四件事：
  · **定稿行 / 草稿行的存在性与内容**与 `overlay._render` cap 分支、`_render_draft`
    的 `active` **逐字同义**（D 布局换的是排版，不是判据）。
  · 四种组合把行放对地方：两个都有时**草稿行是 live**、定稿行进 items。
  · **草稿桥**：定稿中文没过 60% 之前接着显示草稿译文，不让同一句的中文先清空再长。
  · `history` 只读（`Overlay._history` 是外部引用）。

⭐ 每条断言都先问过「把实现改坏（去掉桥 / 把 draft_over 判反 / 加回 "▸ " 前缀），
   这条会不会红」—— 红不了的那种断言不留（见交回记录里的两次变异实验）。
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import caption_flow as C                                          # noqa: E402

#: 「还没说话」的 `Overlay` 状态。每条用例只覆盖自己关心的那几个键。
BASE = dict(cur_en="", cur_zh="", seed_en="", streaming=False,
            draft="", draft_zh="", draft_over=False)


def call(history=None, **kw):
    args = dict(BASE)
    args.update(kw)
    return C.caption_rows([] if history is None else history, **args)


def n_rows(r) -> int:
    """屏上的**行数**：冻结的行 + 最后那张卡（隐藏时不算）。

    ⭐ D 布局的承诺就是这个数：「定稿到了在原地升级」= 不变，「下一段草稿到」= +1。
    """
    return len(r.items) + (1 if r.live_visible else 0)


class FinalRow(unittest.TestCase):
    """规则 ①：定稿行什么时候在、英文取谁 —— 与 `_render` cap 分支同义。"""

    def test_streaming_alone_reserves_the_row(self):
        """流式一开始就占住那一格（ASR 原句 / 译文都还没到）—— 行在、内容先是空串。"""
        r = call(streaming=True)
        self.assertTrue(r.live_visible)
        self.assertEqual(r.live_kind, "final")
        self.assertEqual((r.items, r.live_en, r.live_zh), ([], "", ""))

    def test_text_without_streaming_still_shows(self):
        for kw in ({"cur_en": "one more unit"},
                   {"cur_zh": "多一个单位"},
                   {"cur_en": "one more unit", "cur_zh": "多一个单位"}):
            with self.subTest(**kw):
                r = call(streaming=False, **kw)
                self.assertTrue(r.live_visible)
                self.assertEqual(r.live_kind, "final")
                self.assertEqual(r.items, [])

    def test_en_is_the_seed_while_streaming(self):
        """模型先吐 ZH 后吐 EN：中文打字时英文行放 ASR 原句（修正版 finalize 才来）。"""
        r = call(streaming=True, seed_en="marginal revenue is below price",
                 cur_en="marginal revenue is below the price", cur_zh="边际收益低于价格")
        self.assertEqual(r.live_en, "marginal revenue is below price")

    def test_en_falls_back_to_cur_en_when_seed_missing(self):
        r = call(streaming=True, seed_en="", cur_en="streamed en", cur_zh="中文")
        self.assertEqual(r.live_en, "streamed en")

    def test_seed_is_ignored_when_not_streaming(self):
        """⚠️ 不流式时 seed 是**上一句的残留**（finalize 之外没人清它的中间态）—— 不许上屏。"""
        r = call(streaming=False, seed_en="stale seed", cur_en="live en")
        self.assertEqual(r.live_en, "live en")

    def test_zh_is_cur_zh(self):
        r = call(streaming=True, cur_zh="边际收益")
        self.assertEqual(r.live_zh, "边际收益")


class DraftRow(unittest.TestCase):
    """规则 ②：草稿行的存在性与 `_render_draft` 的 `active` 同义。"""

    def test_visible_when_not_streaming(self):
        r = call(draft="so the price", draft_zh="所以价格")
        self.assertTrue(r.live_visible)
        self.assertEqual(r.live_kind, "draft")
        self.assertEqual((r.live_en, r.live_zh), ("so the price", "所以价格"))

    def test_hidden_while_this_segment_streams(self):
        """防同一句出现两次：定稿流式期间草稿行**不占自己那一行**（除非 draft_over）。

        ⚠️ 这里的中文早已过桥的换手点 —— 桥是 `Bridge` 那一组的事，本组只看"行在不在"。
        """
        r = call(streaming=True, cur_zh="所以价格现在比边际成本高得多",
                 draft="so the price", draft_zh="所以价格")
        self.assertEqual(n_rows(r), 1)                    # 只有那一张卡
        self.assertEqual(r.items, [])
        self.assertEqual(r.live_kind, "final")
        self.assertEqual(r.live_en, "")                   # 草稿的英文没上屏（cur_en 也空）

    def test_hidden_even_with_only_zh_draft(self):
        """只有草稿译文（英文草稿还没到）也算"同一句"—— 一样不占自己那一行。"""
        r = call(streaming=True, cur_zh="所以", draft="", draft_zh="所以价格")
        self.assertEqual(n_rows(r), 1)                    # 没有第二行
        self.assertEqual(r.live_en, "")                   # 英文槽是定稿行的（空）
        self.assertEqual(r.live_zh, "所以价格")           # 中文是桥借来的（见 Bridge 组）
        self.assertEqual(r.live_kind, "draft")

    def test_draft_over_keeps_it_on_screen(self):
        r = call(streaming=True, cur_zh="所以", draft_over=True,
                 draft="now the quantity", draft_zh="现在产量")
        self.assertEqual(r.live_kind, "draft")
        self.assertEqual((r.live_en, r.live_zh), ("now the quantity", "现在产量"))

    def test_no_arrow_prefix(self):
        """⚠️ 老草稿栏的 "▸ " 前缀**不进**这一层（前缀是渲染层的事）。"""
        r = call(draft="so the price", draft_zh="所以价格")
        self.assertEqual(r.live_en, "so the price")
        self.assertFalse(r.live_en.startswith("▸"))

    def test_empty_text_is_not_a_row(self):
        """两个槽都空 = 没有草稿行（`draft_over` 也一样；流式那格单算，见 FinalRow）。"""
        for kw in ({}, {"draft_over": True}):
            with self.subTest(**kw):
                r = call(**kw)
                self.assertFalse(r.live_visible)
                self.assertEqual(r.live_kind, "none")


class Combination(unittest.TestCase):
    """规则 ③：四种组合谁进 items、谁是 live。"""

    HISTORY = [("Price equals marginal cost.", "价格等于边际成本。"),
               ("Total cost rises with output.", "总成本随产量上升。")]

    def test_both_final_freezes_into_items_and_draft_is_live(self):
        """上一段还在流式、老师已讲下一段（10-09 真课 81% 的段都这样）。"""
        r = call(self.HISTORY, streaming=True, seed_en="the monopolist sets", cur_zh="垄断者定",
                 draft="where marginal revenue", draft_zh="边际收益在哪", draft_over=True)
        self.assertEqual(r.items, self.HISTORY + [("the monopolist sets", "垄断者定")])
        self.assertEqual((r.live_en, r.live_zh), ("where marginal revenue", "边际收益在哪"))
        self.assertEqual(r.live_kind, "draft")

    def test_only_final_is_live(self):
        r = call(self.HISTORY, streaming=True, seed_en="the monopolist sets", cur_zh="垄断者定")
        self.assertEqual(r.items, self.HISTORY)
        self.assertEqual(r.live_kind, "final")
        self.assertEqual((r.live_en, r.live_zh), ("the monopolist sets", "垄断者定"))

    def test_only_draft_is_live(self):
        r = call(self.HISTORY, draft="where marginal revenue", draft_zh="边际收益在哪")
        self.assertEqual(r.items, self.HISTORY)
        self.assertEqual(r.live_kind, "draft")
        self.assertEqual((r.live_en, r.live_zh), ("where marginal revenue", "边际收益在哪"))

    def test_neither_hides_the_live_row(self):
        r = call(self.HISTORY)
        self.assertEqual(r.items, self.HISTORY)
        self.assertFalse(r.live_visible)
        self.assertEqual(r.live_kind, "none")
        self.assertEqual((r.live_en, r.live_zh), ("", ""))


class Bridge(unittest.TestCase):
    """规则 ④：草稿桥（阈值 `caption_flow.BRIDGE_RATIO`）。"""

    DRAFT_ZH = "所以垄断者面对整条需求曲线"          # 13 字 → 换手点 7.8

    def test_holds_draft_zh_below_the_ratio(self):
        cur_zh = "所以垄断"
        r = call(streaming=True, seed_en="so a monopolist", cur_zh=cur_zh,
                 draft="so a monopolist", draft_zh=self.DRAFT_ZH)
        self.assertLess(len(cur_zh), C.BRIDGE_RATIO * len(self.DRAFT_ZH))   # 前提自证
        self.assertEqual(r.live_zh, self.DRAFT_ZH)      # 中文还是草稿译文
        self.assertEqual(r.live_en, "so a monopolist")  # 英文是 ASR 原句，不受桥影响
        self.assertEqual(r.live_kind, "draft")

    def test_switches_to_cur_zh_at_the_ratio(self):
        zh = "所以垄断者面对整条需"                      # 10 字 ≥ 7.8
        r = call(streaming=True, seed_en="so a monopolist", cur_zh=zh,
                 draft="so a monopolist", draft_zh=self.DRAFT_ZH)
        self.assertGreaterEqual(len(zh), C.BRIDGE_RATIO * len(self.DRAFT_ZH))  # 前提自证
        self.assertEqual(r.live_zh, zh)
        self.assertEqual(r.live_kind, "final")

    def test_boundary_is_strictly_below(self):
        """`<` 不是 `<=`：恰好等于换手点时就该换（等号那一格差一个字）。"""
        draft_zh = "十" * 10                             # 换手点 6.0
        self.assertEqual(call(streaming=True, cur_zh="十" * 5,
                              draft_zh=draft_zh).live_zh, draft_zh)
        self.assertEqual(call(streaming=True, cur_zh="十" * 6,
                              draft_zh=draft_zh).live_zh, "十" * 6)

    def test_ratio_reads_draft_zh_not_draft_en(self):
        """长英文 + 短中文：拿英文长度当分母的话这条会红（8 字明明已过 2.4 的换手点）。"""
        draft_zh, cur_zh = "所以垄断", "所以垄断者的产出"
        r = call(streaming=True, cur_zh=cur_zh,
                 draft="so a monopolist faces the entire market demand curve by itself",
                 draft_zh=draft_zh)
        self.assertGreaterEqual(len(cur_zh), C.BRIDGE_RATIO * len(draft_zh))   # 前提自证
        self.assertEqual(r.live_zh, cur_zh)
        self.assertEqual(r.live_kind, "final")

    def test_no_bridge_without_draft_zh(self):
        """草稿译文还没到 → 没得桥，中文就是定稿的（哪怕只有一个字）。"""
        r = call(streaming=True, cur_zh="所", draft="so a monopolist")
        self.assertEqual(r.live_zh, "所")
        self.assertEqual(r.live_kind, "final")

    def test_no_bridge_when_the_draft_is_a_newer_segment(self):
        """`draft_over` 时草稿行照常显示，桥不参与（定稿行还在 items 里）。"""
        r = call(streaming=True, cur_zh="所", draft_over=True,
                 draft="now the quantity", draft_zh="现在产量")
        self.assertEqual(len(r.items), 1)                # 定稿行没被桥吃掉
        self.assertEqual(r.items[0], ("", "所"))
        self.assertEqual(r.live_zh, "现在产量")


class HistoryIsReadOnly(unittest.TestCase):
    """规则 ⑤：`history` 是 `Overlay._history` 本身（外部引用），只读、返回新 list。"""

    def test_input_list_is_not_mutated(self):
        h = [("A.", "甲。")]
        before = list(h)
        r = call(h, streaming=True, cur_zh="乙", draft="C", draft_zh="丙", draft_over=True)
        self.assertEqual(h, before)
        self.assertIsNot(r.items, h)
        self.assertEqual(r.items, before + [("", "乙")])

    def test_returns_a_new_list_even_when_nothing_is_added(self):
        h = [("A.", "甲。")]
        r = call(h, draft="C", draft_zh="丙")
        self.assertEqual(r.items, h)
        self.assertIsNot(r.items, h)


class Sequence(unittest.TestCase):
    """整条动线：草稿 → 同段开始流式（桥）→ 过 60%（final）→ 下一段草稿到（+1 行）
    → finalize（history +1、stream 消失，**行数不变**）。

    ⭐ 每一步都断言 `len(items) + live_visible` —— D 布局的承诺就是这两条行数：
    "定稿到了原地升级"（不变）与"下一段草稿到"（+1）。
    """

    def test_row_count_through_a_full_segment_turn(self):
        h = [("Marginal cost is the change in total cost.", "边际成本是总成本的变化量。")]
        draft_en, draft_zh = "so a monopolist faces the whole demand curve", "所以垄断者面对整条需求曲线"
        seed = "so a monopolist faces the whole demand curve"

        # ① 下一段的英文草稿先到（这一段还没开始定稿）—— 屏上 1 张卡。
        st = dict(draft=draft_en, draft_zh=draft_zh)
        r = call(h, **st)
        self.assertEqual(n_rows(r), 2)
        self.assertEqual(r.live_kind, "draft")
        self.assertEqual((r.live_en, r.live_zh), (draft_en, draft_zh))

        # ② 同一段开始流式（ASR 原句到、中文吐了 4 个字）—— 桥生效：中文仍是草稿译文。
        st |= dict(streaming=True, seed_en=seed, cur_zh="所以垄断")
        r = call(h, **st)
        self.assertLess(len(st["cur_zh"]), C.BRIDGE_RATIO * len(draft_zh))  # 前提自证
        self.assertEqual(n_rows(r), 2)                       # 不新增行
        self.assertEqual(r.live_zh, draft_zh)
        self.assertEqual(r.live_en, seed)
        self.assertEqual(r.live_kind, "draft")

        # ③ 定稿中文长过 60% → 换手（同一个位置，字换了）。
        st["cur_zh"] = "所以垄断者面对整条"
        r = call(h, **st)
        self.assertGreaterEqual(len(st["cur_zh"]), C.BRIDGE_RATIO * len(draft_zh))
        self.assertEqual(n_rows(r), 2)
        self.assertEqual(r.live_zh, st["cur_zh"])
        self.assertEqual(r.live_kind, "final")

        # ④ 下一段的草稿到（main 的 DraftView 判出 draft_over）→ 定稿行进 items、屏上 +1 行。
        st |= dict(draft_over=True, draft="now the quantity is lower", draft_zh="现在产量更低")
        r = call(h, **st)
        self.assertEqual(len(r.items), len(h) + 1)
        self.assertEqual(r.items[-1], (seed, "所以垄断者面对整条"))
        self.assertEqual((r.live_en, r.live_zh), ("now the quantity is lower", "现在产量更低"))
        self.assertEqual(n_rows(r), 3)                       # 2 → 3

        # ⑤ finalize(clear_draft=False)：定稿落进 history、流式槽清空 ——
        #    定稿那一行**原地**从 live 升成 items，屏上仍是 3 行。
        h = h + [("So a monopolist faces the whole demand curve.",
                  "所以垄断者面对整条需求曲线。")]
        r = call(h, draft="now the quantity is lower", draft_zh="现在产量更低")
        self.assertEqual(r.items, list(h))
        self.assertEqual(n_rows(r), 3)                       # ⭐ 行数不变
        self.assertEqual(r.live_kind, "draft")

    def test_finalize_without_a_newer_draft_keeps_the_row_too(self):
        """同一段原地升级的另一半：屏上只有流式行时 finalize（clear_draft=True）。"""
        h = [("A.", "甲。")]
        r = call(h, streaming=True, seed_en="B", cur_zh="乙")
        self.assertEqual(n_rows(r), 2)

        h = h + [("B.", "乙。")]                              # finalize 的账
        r = call(h)                                           # 草稿也被清掉了
        self.assertEqual(n_rows(r), 2)                        # 还是 2 行
        self.assertFalse(r.live_visible)
        self.assertEqual(r.live_kind, "none")


H = [("Perfect competition is a benchmark.", "完全竞争是一个基准。")]


class Pacing(unittest.TestCase):
    """让位节奏（`now` 传了才启用）：新句首词开新行 / 上一句译完才让位 / 短尾巴并进上一行。

    ⚠️ 10-17 Owen 实测的症状：Soniox 封口 0.5 s 后下一句就出字，老规矩让上一句译到一半就上移。
    ⚠️ 10-21 改：首词就开行（不再等 3 词）；短尾巴由 `TAIL_WORDS` 管。
    """

    def test_streaming_final_holds_next_draft_until_done(self):
        """上一句还在流式（译文没写完）-> 下一句草稿再长也不上屏，定稿行不让位。"""
        r = call(H, now=11.0, final_t0=10.0, streaming=True, seed_en="Monopoly is rare.",
                 cur_zh="垄断", draft="So a market with one firm", draft_over=True)
        self.assertEqual(n_rows(r), 2)                       # 历史 + 定稿行，没有第三行
        self.assertEqual(r.live_kind, "final")
        self.assertEqual(r.live_zh, "垄断")
        self.assertAlmostEqual(r.recheck_at, 10.0 + C.HOLD_MAX_S)

    def test_hold_gives_up_after_max(self):
        r = call(H, now=10.0 + C.HOLD_MAX_S, final_t0=10.0, streaming=True,
                 seed_en="Monopoly is rare.", cur_zh="垄断",
                 draft="So a market with one firm", draft_over=True)
        self.assertEqual(r.live_kind, "draft")
        self.assertEqual(n_rows(r), 3)

    def test_next_draft_starts_new_row_at_first_word(self):
        """上一句已定稿：下一句草稿第一个词就开新行（2026-10-21）。"""
        r = call(H, now=20.5, last_final_t=20.0, draft="So")
        self.assertTrue(r.live_visible)
        self.assertEqual(r.live_en, "So")
        self.assertEqual(n_rows(r), 2)
        r = call(H, now=20.6, last_final_t=20.0, draft="So a market")
        self.assertTrue(r.live_visible)
        self.assertEqual(r.live_en, "So a market")

    def test_after_a_pause_one_word_opens_a_row(self):
        r = call(H, now=24.0, last_final_t=20.0, draft="Now")
        self.assertTrue(r.live_visible)

    def test_same_sentence_draft_is_not_gated(self):
        """本地路径：草稿是**这一段**的（不是下一句）-> 不受 3 词门槛限制。"""
        r = call(H, now=20.5, last_final_t=20.0, streaming=True, seed_en="",
                 draft="Okay", draft_zh="好", draft_over=False)
        self.assertEqual(r.live_kind, "draft")             # 草稿桥照旧

    def test_short_tail_merges_into_previous_row(self):
        r = call(H, now=21.0, last_final_t=20.5, final_t0=20.8, merge_live=True,
                 streaming=True, seed_en="Okay?", cur_zh="好吗？")
        self.assertEqual(n_rows(r), 1)                     # 不多一行
        self.assertEqual(r.items[-1], ("Perfect competition is a benchmark. Okay?",
                                       "完全竞争是一个基准。好吗？"))

    def test_seed_holds_the_row_before_first_zh(self):
        """封口 -> 定稿开始（seed 到了）-> 第一个中文字之前：这句不许从屏上消失，中文接着显示它的草稿译文。"""
        r = call(H, now=30.2, final_t0=30.0, seed_en="Monopoly is rare.", streaming=False,
                 draft="So", draft_zh="所以", draft_over=True, bridge_zh="垄断很少")
        self.assertEqual(n_rows(r), 2)
        self.assertEqual((r.live_en, r.live_zh), ("Monopoly is rare.", "垄断很少"))

    def test_is_tail(self):
        self.assertTrue(C.is_tail("Okay?", 1.0))
        self.assertTrue(C.is_tail("Right, okay.", 2.9))
        self.assertFalse(C.is_tail("So a market.", 1.0))   # 3 词：自成一行
        self.assertFalse(C.is_tail("Okay?", 3.5))          # 停过：自成一行
        self.assertFalse(C.is_tail("Okay?", None))         # 没有上一行

    def test_is_tail_start(self):
        self.assertTrue(C.is_tail_start("Okay?"))
        self.assertTrue(C.is_tail_start("So"))
        self.assertTrue(C.is_tail_start("Right, okay."))
        self.assertFalse(C.is_tail_start("So a market"))     # 3 词：已经不是短开头
        self.assertFalse(C.is_tail_start("Now"))             # 内容词开头，不许误并
        self.assertFalse(C.is_tail_start("Now a new topic"))

    def test_no_now_keeps_old_rules(self):
        r = call(H, streaming=True, seed_en="x y", cur_zh="中", draft="a", draft_over=True)
        self.assertEqual(r.live_kind, "draft")
        self.assertIsNone(r.recheck_at)


if __name__ == "__main__":
    unittest.main(verbosity=2)
