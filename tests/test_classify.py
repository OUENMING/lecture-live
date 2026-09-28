#!/usr/bin/env python3
"""`classify.py` 的判据 —— 纯函数那半 + 注入假依赖跑通 `suggest`。

    ClassLive.app/Contents/MacOS/python tests/test_classify.py

## 这个文件钉住了什么

1. **课号字面命中**（唯一活下来的机械信号）——命中判定、多课号弃权、大小写
2. ⭐ **模型说了一个不在候选里的课号 → 必须当"都不属于"** —— 这是本模块最容易
   写坏的地方：`parse_reply` 要是直接信 `obj["course"]`，一个幻觉出来的课号
   就会一路走到归档，而它会**污染那门课的术语表**
3. ⭐ **课号命中时不许调模型** —— 那 0.7% 是免费拿的，白花一次 API 是纯浪费；
   这条同时证明"机械层真的短路了"，不是摆设
4. **抽不出正文（扫描件）→ 未分类，且不调模型** —— 实测 12.4% 的 PDF 是这样
5. **模型抛异常 → 未分类，且把异常类名写进理由** —— 静默失败会让作者以为
   "这份真的不属于任何课"
6. ⚠️ **本模块不读模型的置信度**（见 `classify` 模块头的约束 1）——
   判据直接把带 `confidence` 的回复喂进去，看它会不会影响结果

⚠️ 写这里的断言**先问「把实现改坏它会不会红」**。本目录别的文件里被抓出过
   没有区分能力的判据（恒真 / 断言的是 Python 字面量），别再加一条。
"""
from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import classify                                               # noqa: E402

COURSES = ["ECON10730", "ECON10740", "ECON10770", "ECON10790", "SOC10020"]
BRIEFS = {c: f"{c} 某课名　术语样本: alpha、beta" for c in COURSES}


class ByCode(unittest.TestCase):
    def test_single_code_hits(self):
        self.assertEqual(classify.by_code("ECON10740 Task 1.pdf", COURSES), "ECON10740")

    def test_case_insensitive(self):
        self.assertEqual(classify.by_code("econ10740 task.pdf", COURSES), "ECON10740")

    def test_two_codes_abstains(self):
        """联署的讲义确实存在（实测 `MATH10250MATH10430Chap2.pdf`）→ 弃权，别猜。"""
        self.assertIsNone(classify.by_code("ECON10730 vs ECON10740.pdf", COURSES))

    def test_no_code_is_none(self):
        self.assertIsNone(classify.by_code("lecture 3.pdf", COURSES))

    def test_partial_code_is_not_a_hit(self):
        """`ECON1074` 不是任何课号 —— 子串匹配会把它算成命中，那是错的。"""
        self.assertIsNone(classify.by_code("ECON1074 notes.pdf", COURSES))


class ParseReply(unittest.TestCase):
    def test_valid_course(self):
        got = classify.parse_reply({"course": "SOC10020", "why": "标题写着"}, COURSES)
        self.assertEqual(got, ("SOC10020", "标题写着"))

    def test_hallucinated_course_becomes_none(self):
        """⭐ 承重判据：模型自造一个课号，绝不能一路走到归档。"""
        got, why = classify.parse_reply({"course": "ECON99999", "why": "看着像"}, COURSES)
        self.assertIsNone(got)
        self.assertTrue(why)

    def test_empty_course_means_none(self):
        got, why = classify.parse_reply({"course": "", "why": "都不属于"}, COURSES)
        self.assertIsNone(got)
        self.assertEqual(why, "都不属于")

    def test_non_dict_is_none(self):
        got, why = classify.parse_reply(None, COURSES)
        self.assertIsNone(got)
        self.assertTrue(why)

    def test_confidence_field_is_ignored(self):
        """⚠️ 约束 1：不采信模型自报置信度。高置信的幻觉课号照样要拦。"""
        got, _ = classify.parse_reply(
            {"course": "ECON88888", "why": "很有把握", "confidence": 0.99}, COURSES)
        self.assertIsNone(got)
        hi, _ = classify.parse_reply(
            {"course": "ECON10740", "why": "低置信", "confidence": 0.01}, COURSES)
        self.assertEqual(hi, "ECON10740")


class Prompt(unittest.TestCase):
    def test_prompt_carries_every_brief_and_the_head(self):
        p = classify.build_prompt("MAGIC_HEAD_TEXT", BRIEFS)
        self.assertIn("MAGIC_HEAD_TEXT", p)
        for c in COURSES:
            self.assertIn(c, p, f"候选 {c} 没进 prompt")

    def test_prompt_head_is_capped(self):
        p = classify.build_prompt("x" * (classify.HEAD_CHARS * 3), BRIEFS)
        self.assertLess(len(p), classify.HEAD_CHARS * 2 + 2000)


class Suggest(unittest.TestCase):
    """全程注入假依赖 —— 不碰网络、不碰 AppKit、不碰磁盘。"""

    def _suggest(self, names, *, heads=None, replies=None):
        """`replies` 是**按调用顺序**排的回复列表（用文件名找回复太脆）。"""
        heads = heads or {}
        queue = list(replies or [])
        seen_asks = []

        def head_of(p):
            return heads.get(str(p), "")

        def ask(prompt):
            i = len(seen_asks)
            seen_asks.append(prompt)
            return queue[i] if i < len(queue) else None

        out = classify.suggest([pathlib.Path(n) for n in names],
                               courses=COURSES, course_briefs=BRIEFS,
                               head_of=head_of, ask=ask)
        return out, seen_asks

    def test_code_hit_skips_the_model(self):
        """⭐ 0.7% 那部分是免费拿的 —— 白花一次 API 是纯浪费。"""
        out, asks = self._suggest(["ECON10740 Task 1.pdf"],
                                  heads={"ECON10740 Task 1.pdf": "x" * 500},
                                  replies=[{"course": "SOC10020", "why": "瞎说"}])
        self.assertEqual(out[0].course, "ECON10740")
        self.assertEqual(out[0].source, "code")
        self.assertEqual(len(asks), 0, "课号命中却还是调了模型")

    def test_short_head_is_uncategorised_and_skips_the_model(self):
        out, asks = self._suggest(["scan.pdf"], heads={"scan.pdf": "   "})
        self.assertIsNone(out[0].course)
        self.assertEqual(out[0].source, "none")
        self.assertEqual(len(asks), 0)

    def test_model_exception_names_the_failure(self):
        def boom(_p):
            return "y" * 500

        def ask(_prompt):
            raise TimeoutError("slow")

        out = classify.suggest([pathlib.Path("a.pdf")], courses=COURSES,
                               course_briefs=BRIEFS, head_of=boom, ask=ask)
        self.assertIsNone(out[0].course)
        self.assertIn("TimeoutError", out[0].why)

    def test_head_exception_names_the_failure(self):
        def boom(_p):
            raise RuntimeError("PDFKit 打不开这个文件")

        out = classify.suggest([pathlib.Path("a.pdf")], courses=COURSES,
                               course_briefs=BRIEFS, head_of=boom,
                               ask=lambda _p: None)
        self.assertIsNone(out[0].course)
        self.assertIn("RuntimeError", out[0].why)

    def test_model_verdict_flows_through(self):
        out, asks = self._suggest(["roster.pdf"], heads={"roster.pdf": "z" * 500},
                                  replies=[{"course": "SOC10020", "why": "标题写着"}])
        self.assertEqual(out[0].course, "SOC10020")
        self.assertEqual(out[0].source, "model")
        self.assertEqual(len(asks), 1)

    def test_progress_reports_every_file(self):
        seen = []
        classify.suggest([pathlib.Path(n) for n in ("a.pdf", "b.pdf", "c.pdf")],
                         courses=COURSES, course_briefs=BRIEFS,
                         head_of=lambda _p: "", ask=lambda _p: None,
                         on_progress=lambda i, n, name: seen.append((i, n, name)))
        self.assertEqual([s[0] for s in seen], [1, 2, 3])
        self.assertTrue(all(s[1] == 3 for s in seen))


class ModuleHygiene(unittest.TestCase):
    """⚠️ 这两条查的是**代码**不是**文档** —— 先剥掉模块 docstring 再查，
    否则模块头里讲"不许出现 httpx"那句话本身就会把判据弄红。"""

    @staticmethod
    def _code() -> str:
        import ast
        src = pathlib.Path(classify.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        doc = ast.get_docstring(tree, clean=False)
        return src.replace(doc, "", 1) if doc else src

    def test_no_new_httpx_site(self):
        """⚠️ 仓库已有 5 处各自手写 httpx 调 DeepSeek —— 本模块**不许**变成第 6 处。

        真正的修法是抽 `llm.py`（另一批的事）；在那之前，这条判据拦住它继续长。
        """
        code = self._code()
        self.assertNotIn("import httpx", code)
        self.assertNotIn("httpx.post", code)

    def test_module_does_not_write(self):
        """本模块只出建议 —— 不许有任何写盘调用（归档是 `prep._archive` 的活）。"""
        code = self._code()
        for bad in ("write_text", "shutil.", "copyfile", "os.remove", "unlink("):
            self.assertNotIn(bad, code, f"classify.py 里出现了写盘迹象：{bad}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
