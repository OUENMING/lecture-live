"""跨课评测（`scripts/note_eval.py`）的**量具**判据。

为什么量具也要判据：这份表是拿来判断"改前改后谁更好"的 —— 量具自己错了，
结论就反了。所以每条口径至少钉一个**真实产物里的反例**（用例取自别的课，
不是 10-15 那节：作者 2026-10-17 第六轮要求新判据不许再只用 ECON10790 的句子）。

⚠️ 这里只测**纯函数**（打分口径）：不联网、不读 vault、不跑生成。
"""
import importlib.util
import os
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_spec = importlib.util.spec_from_file_location("note_eval", ROOT / "scripts/note_eval.py")
ne = importlib.util.module_from_spec(_spec)
sys.modules["note_eval"] = ne
_spec.loader.exec_module(ne)

#: ECON10770（10-09）产物里变式 2 答案的原话节选 —— 「模型的思考过程漏进了答案」。
SELFTALK_VARIANT = ("所以（打扫，不打扫）也是纳什均衡？但行在列不打扫时打扫得0，不打扫得0，"
                    "无差异，所以行可以打扫也可以不打扫。因此（打扫，不打扫）是纳什均衡吗？"
                    "需要仔细检查：均衡要求双方都没有偏离动机。这不对，因为行在列不打扫时"
                    "无差异。但通常我们要求严格偏好？")
#: 同一条纪律的另一种形态（第一人称 + 猜）。
SELFTALK_FIRST_PERSON = "说明我可能听错了公式，或者我漏掉了 q 的一次项。"


class TestSelftalk(unittest.TestCase):
    def test_扫得出变式答案里的自言自语(self):
        hits = ne.selftalk_hits({"sections": [{"variants": [{"a": SELFTALK_VARIANT}]}]})
        self.assertTrue(hits, "变式答案里的独白必须被抓到（清理层漏的就是它）")
        self.assertTrue(any("variants/0/a" in h["path"] for h in hits),
                        f"出处要指到变式答案：{hits}")

    def test_扫得出第一人称猜测(self):
        self.assertTrue(ne.selftalk_hits({"prose": [SELFTALK_FIRST_PERSON]}))

    def test_自查式独白自成一档(self):
        """独白那一档要能**单独**站住（别的档都不命中时也得抓到）。

        ⚠️ 用别的课的真实句子（ECON10770 变式 2 的答案里那句「我们重新想」）：
           少了这条，把独白档整档删掉，上面那些用例照样绿（实测踩到）。
        """
        hits = ne.selftalk_hits({"a": "我们重新想：均衡要求双方都没有偏离动机。"})
        self.assertEqual([h["pattern"] for h in hits], [3], hits)

    def test_干净正文不误报(self):
        clean = ("公共物品既不具有竞争性也不具有排他性，因此市场供给会不足。"
                 "把两个人的需求纵向相加，得到总边际收益，再令它等于边际成本。")
        self.assertEqual(ne.selftalk_hits({"sections": [{"prose": [clean]}]}), [])

    def test_全字段都要扫到(self):
        for obj in ({"qa": [{"a": SELFTALK_VARIANT}]},
                    {"sections": [{"faded": {"a": SELFTALK_VARIANT}}]},
                    {"sections": [{"example": {"steps": [{"why": SELFTALK_VARIANT}]}}]},
                    {"checks": [{"how": SELFTALK_VARIANT}]},
                    {"review_plan": [SELFTALK_VARIANT]}):
            self.assertTrue(ne.selftalk_hits(obj), f"没扫到：{obj}")


class TestCorrections(unittest.TestCase):
    def test_算得对的断言被判假修正(self):
        # ECON10770 变式 1：价值 5（题面参数），模型算 5-3=2 是对的，
        # 三道核却按例题的 4-3=1「修正」了它。
        checks = [{"status": "mismatch", "claim": "都打扫时每人得5-3=2",
                   "recomputed": "都打扫 4-3=1"}]
        got = ne.correction_audit(checks)
        self.assertEqual(got["wrong"], 1, got)

    def test_算错的断言被判改得有理(self):
        checks = [{"status": "mismatch", "claim": "都打扫时每人得5-3=3",
                   "recomputed": "5-3=2"}]
        self.assertEqual(ne.correction_audit(checks)["justified"], 1)

    def test_对错混在一条里不算假修正(self):
        """一条断言里既有算对的、也有算错的等式 → 只能算「改得有理」。

        ⚠️ 这条钉的是判据的**方向**：`all(ok)` 才是假修正。用 `any(ok)` 的话，
           只要断言里蒙对一个等式，真错误也会被记成假修正（量具反过来骗人）。
        """
        checks = [{"status": "mismatch",
                   "claim": "都打扫每人得5-3=2；一人打扫打扫者得5-6=0",
                   "recomputed": "一人打扫打扫者得 5-6=-1"}]
        self.assertEqual(ne.correction_audit(checks)["justified"], 1)

    def test_提不出等式就列给人看(self):
        checks = [{"status": "mismatch", "claim": "总收益无依据",
                   "recomputed": "总收益非50"}]
        got = ne.correction_audit(checks)
        self.assertEqual(got["unverifiable"], 1)
        self.assertEqual(got["rows"][0]["claim"], "总收益无依据")

    def test_没被判修正的条目不进账(self):
        self.assertEqual(ne.correction_audit(
            [{"status": "ok", "claim": "5-3=2"}, {"status": "unchecked", "claim": "x"}]
        )["n_mismatch"], 0)

    def test_出处能定位到变式(self):
        review = {"sections": [{"heading": "厨房博弈", "variants": [{"a": "都打扫时每人得5-3=2"}]}]}
        self.assertEqual(ne.locate_claim(review, "都打扫时每人得5-3=2"),
                         "sections/0/variants/0/a")


class TestTeXLeak(unittest.TestCase):
    def test_math_之外的命令算漏(self):
        self.assertEqual(len(ne.tex_leaks("最低点是 $\\min AC$ 而这里写着 \\min AC")), 1)

    def test_math_里的命令不算漏(self):
        self.assertEqual(ne.tex_leaks(r"停产价 $p=\min AVC$ 与 $\sqrt{2}$ 都正常"), [])


class TestCoverage(unittest.TestCase):
    SK = [{"i": 1, "title": "章一", "t0": "12:00:00", "t1": "12:10:00",
           "points": [{"ts": "12:01:00", "zh": "a"}, {"ts": "12:09:00", "zh": "b"}]},
          {"i": 2, "title": "章二", "t0": "12:10:00", "t1": "12:20:00",
           "points": [{"ts": "12:15:00", "zh": "c"}]}]

    def test_按时间窗算覆盖(self):
        got = ne.cover_by_time(self.SK, [{"heading": "一", "ts": "12:00:30"},
                                         {"heading": "二", "ts": "12:12:00"}])
        self.assertEqual((got["n_covered"], got["n_points"]), (3, 3), got)
        self.assertEqual((got["n_ok"], got["n_chapters"]), (2, 2))

    def test_第一句在第一节之前也算覆盖(self):
        got = ne.cover_by_time(self.SK, [{"heading": "一", "ts": "12:02:00"}])
        self.assertTrue(got["rows"][0]["points"][0]["ok"], got)

    def test_没有节就是没覆盖(self):
        got = ne.cover_by_time(self.SK, [])
        self.assertEqual(got["n_covered"], 0)


class TestTerms(unittest.TestCase):
    def test_机构词与唯一出处都算非术语(self):
        # ⚠️ `Brightspace` 在转录里出现 **2 次**：出处那一档不再挡它 ——
        #    这时只有"通用词表"这一档能挡（实测：只出现 1 次的用例会把两档混在一起，
        #    删掉通用词表判据照样绿）。
        got = ne.term_audit(["Brightspace", "externality"],
                            transcript="upload it to Brightspace. "
                                       "Brightspace is where you submit. "
                                       "the externality is positive. another externality.",
                            materials_blob="externality of consumption")
        by = {r["term"]: r for r in got["rows"]}
        self.assertTrue(by["Brightspace"]["non_term"], "机构词不该进库（哪怕出现很多次）")
        self.assertFalse(by["externality"]["non_term"], "学科词（转录+课件都出现）要能进")

    def test_只出现一次不算(self):
        got = ne.term_audit(["tutorial"], transcript="the tutorial starts now",
                            materials_blob="")
        self.assertTrue(got["rows"][0]["non_term"])


class TestMdBody(unittest.TestCase):
    def test_切掉附录B的逐句转录(self):
        md = "## 讲解\n正文\n## 📜 完整逐句转录（附录 B · 点击展开 · 3 句）\n> EN: hi\n"
        self.assertIn("正文", ne.md_body(md))
        self.assertNotIn("EN: hi", ne.md_body(md))


class TestWalk(unittest.TestCase):
    def test_字符串全走一遍_路径带下标(self):
        got = dict(ne.walk_strings({"a": ["x", {"b": "y"}], "c": 1}))
        self.assertEqual(sorted(got), ["a/0", "a/1/b"])
        self.assertEqual(got["a/1/b"], "y")


if __name__ == "__main__":
    unittest.main()
