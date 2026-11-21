#!/usr/bin/env python3
"""定稿 EN 段被输出额度截断时回退 ASR 原文 + 定稿预算随句长 —— 全离线。

    ClassLive.app/Contents/MacOS/python tests/test_en_fix_truncated.py

⭐ 2026-11-20 网页用户课：488 字符长句，ZH 先写吃掉大半 220 token，EN 写到半句就停，
截断 EN 覆盖了完整原文（中文完整，英文丢约 20 s）。下面的句子是编的，同形状。
网页 `web/tests/translate/format.test.ts` 末尾四条是同一组判据。
"""
from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from cloud_translator import final_max_tokens          # noqa: E402
from translator import _StreamParser, en_fix_truncated  # noqa: E402

LONG_RAW = ("so the firm keeps raising output, um, and if you remember when we drew the cost curve last week, "
            "marginal cost starts climbing once the factory runs near capacity, which pushes the supply curve up "
            "and to the left, and that is why the price rises in the short run. Does everyone see why the curve bends?")


def run(deltas, raw):
    p = _StreamParser(lambda s: None, lambda s: None)
    for d in deltas:
        p.feed(d)
    return p.result(raw)


class T(unittest.TestCase):
    def test_truncated_en_falls_back_to_raw(self):
        r = run(["ZH: 所以企业不断提高产量……大家明白曲线为什么会弯吗？\n",
                 "EN: so the firm keeps raising output, um, and if you remember when we drew the"], LONG_RAW)
        self.assertEqual(r.en_fixed, LONG_RAW)
        self.assertEqual(r.zh, "所以企业不断提高产量……大家明白曲线为什么会弯吗？")

    def test_normal_fix_kept(self):
        raw = "So the the midterm covers chapter one two three."
        r = run(["ZH: 期中考试覆盖第一到三章。\nEN: So the midterm covers chapters one to three."], raw)
        self.assertEqual(r.en_fixed, "So the midterm covers chapters one to three.")

    def test_less_than_half_falls_back(self):
        r = run(["ZH: 某句。\nEN: So the firm keeps raising output."], LONG_RAW)
        self.assertEqual(r.en_fixed, LONG_RAW)

    def test_raw_without_punct_not_flagged(self):
        raw = "and the price goes up when demand rises"
        self.assertFalse(en_fix_truncated("and the price goes up when demand", raw))

    def test_final_budget(self):
        self.assertEqual(final_max_tokens("short sentence."), 220)
        self.assertEqual(final_max_tokens("x" * 488), 366)
        self.assertEqual(final_max_tokens("x" * 2000), 400)


if __name__ == "__main__":
    unittest.main()
