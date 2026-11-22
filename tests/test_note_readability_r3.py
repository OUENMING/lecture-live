"""可读性第三轮：直引号规整、节内小标题（merge_units → HTML / 导图）。纯函数，零网络。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import note_guards  # noqa: E402
import note_html  # noqa: E402
import note_v2  # noqa: E402


class 直引号(unittest.TestCase):
    def test_含汉字_变直角引号_去内空格(self):
        self.assertEqual(note_guards.corner_quotes_text('问 " 为什么女性更少？" 吧'),
                         "问「为什么女性更少？」吧")

    def test_纯英文原话_变弯引号_原话核对仍认得(self):
        out = note_guards.corner_quotes_text('他说 "Stay hungry, stay foolish" 这句')
        self.assertIn("“Stay hungry, stay foolish”", out)
        self.assertTrue(note_guards._QUOTE.search(out), "quote_check 的正则必须还能抓到它")

    def test_奇数个_公式里_没汉字_都不动(self):
        for s in ('奇数 " 个', 'all "x" here', '$a="x"$ 和 $b="y"$'):
            self.assertEqual(note_guards.corner_quotes_text(s), s)

    def test_公式里的引号不参与配对(self):
        self.assertEqual(note_guards.corner_quotes_text('$f="x"$ 与 "一"'), '$f="x"$ 与「一」')

    def test_守卫不碰原话与台账(self):
        rv = {"sections": [{"prose": ['说 "好"'], "quote": '"好"'}],
              "checks": [{"claim": '说 "好"'}]}
        out, _, warns = note_guards.corner_quotes(rv)
        self.assertEqual(out["sections"][0]["prose"][0], "说「好」")
        self.assertEqual(out["sections"][0]["quote"], '"好"')
        self.assertEqual(out["checks"][0]["claim"], '说 "好"')
        self.assertEqual(rv["sections"][0]["prose"][0], '说 "好"', "不许原地改")
        self.assertTrue(warns)


def _sec(h, prose, ts):
    return {"heading": h, "prose": prose, "ts": ts}


class 小标题(unittest.TestCase):
    def test_两个以上子题_合并时插小标题(self):
        rv = {"sections": [_sec("市场失灵：外部性", ["甲段。"], "13:00:00"),
                           _sec("市场失灵：外部性", ["乙段。"], "13:02:00"),
                           _sec("市场失灵：公共物品", ["丙段。"], "13:04:00")]}
        out = note_v2.merge_units(rv, end_ts="13:10:00")
        self.assertEqual(len(out["sections"]), 1)
        self.assertEqual(out["sections"][0]["prose"],
                         ["## 外部性", "甲段。", "乙段。", "## 公共物品", "丙段。"])

    def test_多套一层前缀剥掉_同名子题不重复插(self):
        rv = {"sections": [_sec("甲：乙：一", ["a"], "13:00:00"),
                           _sec("甲：乙：二", ["b"], "13:02:00"),
                           _sec("甲：乙：一", ["c"], "13:04:00")]}
        out = note_v2.merge_units(rv, end_ts="13:10:00")
        self.assertEqual(out["sections"][0]["prose"], ["## 一", "a", "## 二", "b", "c"])

    def test_只有一个子题_不插(self):
        rv = {"sections": [_sec("市场失灵：外部性", ["甲段。"], "13:00:00"),
                           _sec("市场失灵", ["乙段。"], "13:02:00")]}
        out = note_v2.merge_units(rv, end_ts="13:10:00")
        self.assertEqual(out["sections"][0]["prose"], ["甲段。", "乙段。"])

    def test_subhead_of(self):
        self.assertEqual(note_v2.subhead_of("## 外部性"), "外部性")
        self.assertEqual(note_v2.subhead_of("外部性 ## 不是"), "")
        self.assertEqual(note_v2.subhead_of("#没空格"), "")

    def test_HTML_小标题_首段强调色给第一段正文_悬空小标题不印(self):
        sec = {"heading": "市场失灵", "prose": ["## 外部性", "甲段。", "## 空的"]}
        h = note_html._section_html(sec, 1) if hasattr(note_html, "_section_html") else None
        if h is None:
            self.skipTest("渲染入口名不同")
        self.assertIn('<h4 class="sub" id="kp1s1">外部性</h4>', h)
        self.assertNotIn("空的", h)
        self.assertIn('class="keyline"', h)
        self.assertNotIn("## ", h)

    def test_导图二级用小标题(self):
        secs = [{"heading": "甲", "q": "问？", "prose": ["## 子一", "x", "## 子二", "y"],
                 "terms": [{"zh": "术语", "en": "term"}]},
                {"heading": "乙", "prose": ["z"], "terms": [{"zh": "术语二", "en": "t2"}]}]
        d = note_html.mindmap_data("课", secs)
        leaves = [c["content"] for c in d["children"][0]["children"]]
        self.assertTrue(any("#kp1s1" in x and "子一" in x for x in leaves))
        self.assertFalse(any("术语" in x for x in leaves), "有小标题就不放术语")
        self.assertTrue(any("术语二" in x for x in
                            (c["content"] for c in d["children"][1]["children"])))


if __name__ == "__main__":
    unittest.main()
