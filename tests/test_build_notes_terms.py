"""`build_notes.collect_terms` = `--rebuild` 的保留集，必须是**全表**。

2026-10-18 合并检查抓到：它走 `translator.load_terms`，那条带每句注入的
加载上限（`TERM_LOAD_LIMIT` = 60）→ 第 60 条之后的课程术语不在保留集里 →
`build_notes --rebuild` 把它们已建的释义当垃圾删掉并落盘（`term_notes.json` 是用户数据）。
"""
from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import build_notes                                                   # noqa: E402
import translator                                                    # noqa: E402


class 保留集是全表(unittest.TestCase):
    def test_超过加载上限的课程术语也在(self):
        n = translator.TERM_LOAD_LIMIT + 15
        words = [f"zeta term {i:03d}" for i in range(n)]
        with tempfile.TemporaryDirectory() as d:
            here = pathlib.Path(d)
            (here / "glossary").mkdir()
            (here / "glossary.txt").write_text("public term one\n", encoding="utf-8")
            (here / "glossary" / "ECON99999.txt").write_text(
                "# ECON99999 Test\n" + "\n".join(words) + "\n", encoding="utf-8")
            with mock.patch.object(build_notes, "HERE", here):
                one = build_notes.collect_terms("ECON99999")
                every = build_notes.collect_terms(None)
        for got in (one, every):
            self.assertIn("public term one", got)
            missing = [w for w in words if w not in got]
            self.assertEqual(missing, [], f"保留集漏了 {len(missing)} 条（被加载上限截掉）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
