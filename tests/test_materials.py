"""课件索引（`materials.Materials`）的判据：切块 / BM25 检索 / 语境核对。全离线。

⭐ 这一层的两个出口各有一条判据指向**真实语义**，不是"函数被调到了"：
   · `search(转录片段)` 必须把**相关的那一块**排到第一（换一段无关的 query 就换结果）；
   · `verify(词)` 必须认得出"本课材料里真写过它"，且**不做子串命中**。
"""
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import materials as mt          # noqa: E402

MD = """# ECON10790 Final Exam December 2025

Exercise 5 [16 points]: TC(q) = q^3 - 12q^2 + 60q.

## Exercise 5 answer

Average cost is AC = q^2 - 12q + 60 and marginal cost is MC = 3q^2 - 24q + 60.
Setting marginal cost equal to average cost gives q = 6, so the minimum
average cost is 24 euros.

## Exercise 7 answer

The shutdown price is the minimum of the average cost. For TC = 2q^2+q+32,
AC = 2q+1+32/q and MC = 4q+1, so the shutdown price is 17 euros.
"""


class SplitChunks(unittest.TestCase):
    def test_标题跟着块走(self):
        chunks = mt.split_chunks(MD)
        heads = [c.heading for c in chunks]
        self.assertIn("Exercise 5 answer", heads, heads)
        got = [c for c in chunks if c.heading == "Exercise 7 answer"]
        self.assertTrue(got)
        self.assertIn("shutdown price", got[0].text, "标题与正文错位了")

    def test_出处可回源(self):
        c = [c for c in mt.split_chunks(MD) if "shutdown" in c.text][0]
        self.assertIn("§Exercise 7", c.cite())


class Search(unittest.TestCase):
    def _idx(self):
        return mt.Materials([mt.Chunk(file="final.md", heading=c.heading, text=c.text)
                             for c in mt.split_chunks(MD)])

    def test_相关块排第一(self):
        idx = self._idx()
        got = idx.search("so the shutdown price, what is the minimum acceptable price? "
                         "the firm should not produce below it", k=2)
        self.assertTrue(got)
        self.assertIn("shutdown", got[0].text.lower())

    def test_换一个话题换一块(self):
        idx = self._idx()
        got = idx.search("setting marginal cost equal to average cost gives q equal 6")
        self.assertTrue(got)
        self.assertIn("q = 6".replace(" = ", " = "), got[0].text)

    def test_空索引不炸(self):
        self.assertEqual(mt.Materials([]).search("anything"), [])
        self.assertEqual(len(mt.Materials([])), 0)

    def test_片段总量有上限(self):
        idx = mt.Materials([mt.Chunk(file="f.md", heading=f"h{i}", text="word " * 400)
                            for i in range(20)])
        got = idx.search("word", k=10, max_chars=500)
        self.assertLessEqual(sum(len(c.text) for c in got), 1500)


class Verify(unittest.TestCase):
    def _idx(self):
        return mt.Materials([mt.Chunk(file="f.md", heading="", text=MD)])

    def test_真出现过的词认得出(self):
        idx = self._idx()
        self.assertTrue(idx.verify("shutdown price"))
        self.assertTrue(idx.verify("marginal cost"))

    def test_复数与大小写(self):
        self.assertTrue(mt.Materials([mt.Chunk("f", "", "two exercises here")])
                        .verify("exercise"))

    def test_子串不算(self):
        """`compile_term` 的词边界口径：`gg` 不该被 `suggest` 命中（`build_notes` 那条）。"""
        idx = mt.Materials([mt.Chunk("f", "", "we suggest a different approach")])
        self.assertFalse(idx.verify("gg"))

    def test_没出现的就是没有(self):
        self.assertFalse(self._idx().verify("Universal Social Charge"))

    def test_空索引与空词(self):
        self.assertFalse(mt.Materials([]).verify("anything"))
        self.assertFalse(self._idx().verify(""))


class ForCourse(unittest.TestCase):
    def test_从_vault_的_Study_目录建索引(self):
        with tempfile.TemporaryDirectory() as d:
            room = pathlib.Path(d) / "Study" / "ECON10790" / "materials"
            room.mkdir(parents=True)
            (room / "final.md").write_text(MD, encoding="utf-8")
            (room / "notes.txt").write_text("linear functions and slopes",
                                            encoding="utf-8")
            (room / "slides.pdf").write_bytes(b"%PDF-1.4 not text")   # 非文本不进索引
            idx = mt.Materials.for_course(d, "ECON10790")
            self.assertGreater(len(idx), 0)
            self.assertEqual(len(idx.files()), 2, idx.files())
            self.assertTrue(idx.verify("shutdown price"))

    def test_没有课件目录_是空索引不是异常(self):
        with tempfile.TemporaryDirectory() as d:
            idx = mt.Materials.for_course(d, "ECON99999")
            self.assertEqual(len(idx), 0)
            self.assertIn("无课件", idx.describe())

    def test_不猜别的课的目录(self):
        """⚠️ 只认本课号 —— 拿隔壁课的材料当依据 = 编造出处。"""
        with tempfile.TemporaryDirectory() as d:
            room = pathlib.Path(d) / "Study" / "ECON10740" / "materials"
            room.mkdir(parents=True)
            (room / "other.md").write_text("transfer pricing", encoding="utf-8")
            self.assertEqual(len(mt.Materials.for_course(d, "ECON10790")), 0)


class Snippets(unittest.TestCase):
    def test_出处跟着正文进_prompt(self):
        blk = mt.snippets_block([mt.Chunk(file="final.md", heading="Exercise 5",
                                         text="AC = q^2 - 12q + 60")])
        self.assertIn("📖 final.md §Exercise 5", blk)
        self.assertIn("AC = q^2 - 12q + 60", blk)
        self.assertEqual(mt.snippets_block([]), "")


if __name__ == "__main__":
    unittest.main()
