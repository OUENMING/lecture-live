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




class 术语出处能回源(unittest.TestCase):
    """`find_term_ts`：复数、口语填充词都要认；认不出就**不写时间**（2026-11-09）。"""

    def test_复数与填充词(self):
        ents = [
            {"ts": "11:16:16", "en": "They are not ideologies."},
            {"ts": "11:19:40", "en": "They are, uh, stable patterns of, uh, activity, right?"},
            {"ts": "11:27:26", "en": "natural and legal relatives"},
        ]
        self.assertEqual(build_notes.find_term_ts(ents, "ideology"), "11:16:16")
        self.assertEqual(build_notes.find_term_ts(ents, "stable patterns of activity"), "11:19:40")
        self.assertEqual(build_notes.find_term_ts(ents, "natural relatives"), "",
                         "换了说法不是同一句 —— 宁可未定位")

    def test_常规复数不能被词干化吃掉(self):
        ents = [{"ts": "1", "en": "Prices rose."},
                {"ts": "2", "en": "The service changed."}]
        self.assertEqual(build_notes.find_term_ts(ents, "price"), "1")
        self.assertEqual(build_notes.find_term_ts(ents, "service"), "2")

    def test_is_es_类复数(self):
        ents = [{"ts": "1", "en": "These analyses differ."},
                {"ts": "2", "en": "Multiple crises hit."}]
        self.assertEqual(build_notes.find_term_ts(ents, "analysis"), "1")
        self.assertEqual(build_notes.find_term_ts(ents, "crisis"), "2")

    def test_所有格也要命中(self):
        # 旧 `compile_term` 的 `(?:s|'s)?` 认所有格；换成词序列后曾整类漏掉
        # （2026-11-09 复核抓到）。`consumer's` / `consumers'` 去撇号后都落到复数。
        ents = [{"ts": "1", "en": "The consumer's role is key."}]
        self.assertEqual(build_notes.find_term_ts(ents, "consumer"), "1")
        ents = [{"ts": "2", "en": "Consumers' choices differ."}]
        self.assertEqual(build_notes.find_term_ts(ents, "consumer"), "2")

    def test_ics_us_is_结尾不该剥成形容词(self):
        # economics → economic / status → statu / analysis → analysi 都是假命中，
        # 讲义里出现频率远高于术语本身（2026-11-09 OCR 审查抓到）。
        ents = [{"ts": "1", "en": "The economic impact was large."},
                {"ts": "2", "en": "Economics is the study of choice."}]
        self.assertEqual(build_notes.find_term_ts(ents, "economics"), "2")
        ents = [{"ts": "1", "en": "The ethic of care matters."},
                {"ts": "2", "en": "Ethics is the study of morality."}]
        self.assertEqual(build_notes.find_term_ts(ents, "ethics"), "2")

    def test_常识词硬挡(self):
        import note_terms as nt
        self.assertTrue(nt.hard_common("Environment"))
        self.assertTrue(nt.hard_common("social"))
        self.assertFalse(nt.hard_common("monopoly"))

    def test_空输入不猜(self):
        self.assertEqual(build_notes.find_term_ts([], "ideology"), "")
        self.assertEqual(build_notes.find_term_ts([{"ts": "10:00:00", "en": ""}], "norm"), "")



class 短词被长词包含就不出行(unittest.TestCase):
    """`monopoly` 与 `monopoly of legitimate violence` 同表时，短词那条释义是错语境（3.11.1）。"""

    def test_containment(self):
        import note_terms as nt
        extra = [
            nt.Hit("monopoly", "一家公司独占整个市场", "11:45:44", "术语库"),
            nt.Hit("monopoly of legitimate violence", "正当暴力的垄断", "11:45:44", nt.SRC_LECTURE),
        ]
        rows = nt.final_rows(nt.Selection(hits=[], dropped=[]), extra=extra)
        terms = [r.term for r in rows]
        self.assertIn("monopoly of legitimate violence", terms)
        self.assertNotIn("monopoly", terms)

    def test_正确释义不能被占位长词顶掉(self):
        import note_terms as nt
        extra = [
            nt.Hit("price", "价格", "10:00:00", "术语库"),
            nt.Hit("price elasticity", "（本课术语）", "10:05:00", nt.SRC_LECTURE),
        ]
        terms = [r.term for r in nt.final_rows(nt.Selection(hits=[], dropped=[]), extra=extra)]
        self.assertIn("price", terms, "短词有真释义，不该被占位行顶掉")
        self.assertIn("price elasticity", terms)

    def test_未定位的真释义短词也不该被吞(self):
        # `SRC_UNLOCATED` = 本课讲过、只是没搜到时间 —— 不是「通用词典义」，
        # 不能按 `SRC_CURATED` / `SRC_AUTO` 处理（2026-11-09 复核抓到）。
        import note_terms as nt
        extra = [
            nt.Hit("supply", "供给", "", nt.SRC_UNLOCATED),
            nt.Hit("supply curve", "供给曲线", "10:05:00", nt.SRC_LECTURE),
        ]
        terms = [r.term for r in nt.final_rows(nt.Selection(hits=[], dropped=[]), extra=extra)]
        self.assertIn("supply", terms, "本课的释义不该被长词吞掉")
        self.assertIn("supply curve", terms)




class 未定位不写假时间(unittest.TestCase):
    def test_render_table_no_time(self):
        import note_terms as nt
        sel = nt.Selection(hits=[nt.Hit("ideology", "意识形态", "", nt.SRC_UNLOCATED)],
                           dropped=[])
        line = nt.render_table(sel)[0]
        self.assertIn("未定位", line)
        self.assertNotIn("`", line, "没有时间就不许印一个反引号时间")


class 复习层未定位不许退到小节时间(unittest.TestCase):
    def test_review_hits_keeps_empty_ts(self):
        import note_terms as nt
        import obsidian_writer as ow
        w = ow.ObsidianWriter(vault=None, course="T", mode="no")
        review = {"sections": [{"ts": "11:00:00–11:10:00",
                                "terms": [{"en": "social change", "zh": "社会变迁"}]}]}
        entries = [{"ts": "11:02:00", "en": "We discussed something else entirely."}]
        hits = w._review_term_hits(review, entries)
        self.assertEqual(len(hits), 1, hits)
        self.assertEqual(hits[0].ts, "", "搜不到就不能给一个假时间")
        self.assertEqual(hits[0].source, nt.SRC_UNLOCATED)


if __name__ == "__main__":
    unittest.main(verbosity=2)
