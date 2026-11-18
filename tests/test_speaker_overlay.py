#!/usr/bin/env python3
"""说话人标注（2026-11-18）的悬浮窗判据 —— 全部离线（不联网、不写 sessions/）。

    ClassLive.app/Contents/MacOS/python tests/test_speaker_overlay.py

钉住了什么：
 1. `transcript_view.LineLog.labels` 与 `rows` 永远等长同序（追加 / 短尾巴并行 / 半句合并吞行）。
 2. `Overlay.finalize(speaker=)`：换了人不并进上一行（短尾巴 / 连着讲两条都不并）；同一个人照旧并。
 3. 渲染：别人那一行行首挂「说话人 B」、行首那一格让出位置；主讲那一行不挂；答案接管时一律不挂。
"""
from __future__ import annotations

import pathlib
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import transcript_view as tv                                         # noqa: E402


class TestLineLogLabels(unittest.TestCase):
    def test_labels_follow_rows(self):
        log = tv.LineLog()
        a = log.append("a", "甲")
        log.append("b", "乙", "B")
        c = log.append("c", "丙")
        self.assertEqual(log.labels, ["", "B", ""])
        log.amend_last("c c", "丙丙")
        self.assertEqual(log.labels, ["", "B", ""])
        self.assertTrue(log.replace_pair(a, c, "ac", "甲丙"))     # 吞掉第 3 行
        self.assertEqual(len(log.labels), len(log.rows))
        self.assertEqual(log.labels, ["", "B"])

    def test_failed_replace_leaves_labels(self):
        log = tv.LineLog()
        log.append("a", "甲", "B")
        before = list(log.labels)
        self.assertFalse(log.replace_pair(99, 100, "x", "y"))
        self.assertEqual(log.labels, before)


class TestOverlaySpeaker(unittest.TestCase):
    def _overlay(self):
        try:
            from overlay import Overlay
        except Exception as e:                       # noqa: BLE001
            self.skipTest(f"AppKit 不可用, 跳过: {type(e).__name__}: {e}")
        o = Overlay()
        self.addCleanup(o.close)
        o._tv._reduce_motion = lambda: True
        return o

    def _draw(self, o):
        o._render()
        o._tv.tick()

    def test_no_tail_merge_across_speakers(self):
        o = self._overlay()
        o.set_tail_merge(True)
        o.finalize("So the price is fixed in the short run.", "所以短期内价格是固定的。")
        o.set_live_speaker("B")
        o.finalize("Okay?", "好吗？", speaker="B")          # 短尾巴，但换了人
        self.assertEqual(len(o._history), 2)
        self.assertEqual(o._log.labels, ["", "B"])
        o.set_live_speaker("B")
        o.finalize("Right.", "对。", speaker="B")             # 同一个人的短尾巴照旧并
        self.assertEqual(len(o._history), 2)
        self.assertEqual(o._log.labels, ["", "B"])
        o.finalize("Okay?", "好吗？")                          # 主讲回来：不并进 B 那一行
        self.assertEqual(len(o._history), 3)
        self.assertEqual(o._log.labels, ["", "B", ""])

    def test_live_speaker_cleared_after_final(self):
        o = self._overlay()
        o.set_live_speaker("C")
        o.finalize("Is that right?", "是这样吗？", speaker="C")
        self.assertEqual(o._live_spk, "")

    def test_label_rendered_on_other_speaker_row_only(self):
        o = self._overlay()
        o.set_tail_merge(False)
        o.finalize("In the long run there is no profit.", "长期来看没有利润。")
        o.set_live_speaker("B")
        o.finalize("Can I take no profit in a competitive market?", "竞争市场里可以没有利润吗？",
                   speaker="B")
        self._draw(o)
        shown = {}
        for s in o._tv._slots:
            if s["level"] is None or s["hidden"]:
                continue
            shown[s["zh"].stringValue()] = s
        b = next(s for z, s in shown.items() if "竞争市场" in z)
        t = next(s for z, s in shown.items() if "长期" in z)
        self.assertFalse(b["spk"].isHidden())
        self.assertEqual(b["spk"].stringValue(), "说话人 B")
        self.assertGreater(b["spk_w"], 0.0)
        self.assertAlmostEqual(b["en"].frame().origin.x, b["spk_w"] + tv.SPEAKER_GAP, places=3)
        self.assertEqual(b["zh"].frame().origin.x, 0.0, "中文那格不让位（标注挂在英文前）")
        self.assertTrue(t["spk"].isHidden() or not t["spk_w"])
        self.assertEqual(t["en"].frame().origin.x, 0.0)

    def test_verbatim_rows_never_labelled(self):
        o = self._overlay()
        o._tv.set_speakers(["B", "B"], "B")
        o._tv.set_rows_verbatim(True)
        self.assertEqual(o._tv._spk_text(0), "")
        o._tv.set_rows_verbatim(False)
        self.assertEqual(o._tv._spk_text(0), "说话人 B")
        self.assertEqual(o._tv._spk_text(5), "说话人 B", "比 items 长的行算实时行的")


if __name__ == "__main__":
    unittest.main(verbosity=1)
