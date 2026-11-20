#!/usr/bin/env python3
"""`cl_web_data.mark_replaced` / `summarize` 的新字段 —— 刷新后旧会话被新会话接替的判据（纯函数，不联网）。

    ClassLive.app/Contents/MacOS/python tests/test_web_data_replaced.py

钉住什么：
 1. 同一个人、旧的没有 class_report、新的在旧的最后一条事件后 180 s 内开始 → 旧的标 replaced_by
 2. 旧的有 class_report（正常下课）→ 不标；新的开得太晚（> 180 s）→ 不标；别人的会话 → 不标
 3. summarize 数得出 pages（class_open 几条）和 resumed（接回几次）
"""
from __future__ import annotations

import pathlib
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "scripts"))

from cl_web_data import REPLACE_GAP_S, mark_replaced, summarize  # noqa: E402


def row(name, sid, first, last, report=None):
    return {"name": name, "session": sid, "first_t": first, "last_t": last, "report": report}


class MarkReplaced(unittest.TestCase):
    def test_refresh_replaces_old(self):
        rows = [row("amy", "a", 0, 100_000), row("amy", "b", 110_000, 500_000)]
        mark_replaced(rows)
        self.assertEqual(rows[0].get("replaced_by"), "b")
        self.assertNotIn("replaced_by", rows[1])

    def test_normal_end_is_not_replaced(self):
        rows = [row("amy", "a", 0, 100_000, report={"dur_s": 100}), row("amy", "b", 110_000, 500_000)]
        mark_replaced(rows)
        self.assertNotIn("replaced_by", rows[0])

    def test_late_or_other_person_not_replaced(self):
        late = 100_000 + (REPLACE_GAP_S + 1) * 1000
        rows = [row("amy", "a", 0, 100_000), row("amy", "b", late, late + 1),
                row("bob", "c", 110_000, 500_000)]
        mark_replaced(rows)
        self.assertNotIn("replaced_by", rows[0])

    def test_just_inside_gap_is_replaced(self):
        edge = 100_000 + REPLACE_GAP_S * 1000
        rows = [row("amy", "a", 0, 100_000), row("amy", "b", edge, edge + 1)]
        mark_replaced(rows)
        self.assertEqual(rows[0].get("replaced_by"), "b")


class SummarizeCounts(unittest.TestCase):
    def test_pages_and_resumed(self):
        evs = [{"type": "class_open", "t": 1}, {"type": "class_open", "t": 9}, {"type": "resumed", "t": 10}]
        s = summarize("amy", "a", evs)
        self.assertEqual((s["pages"], s["resumed"]), (2, 1))
        s0 = summarize("amy", "a", [{"type": "class_open", "t": 1}])
        self.assertEqual((s0["pages"], s0["resumed"]), (1, 0))


if __name__ == "__main__":
    unittest.main()
