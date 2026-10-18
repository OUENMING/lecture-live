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

    def test_标题后紧跟正文不许丢(self):
        """DB 审查 D-16：`## 标题\\n正文`（中间没有空行）的块 —— 标题只吃自己那一行，
        后面的正文必须留下。旧实现把整块当标题 `continue` 掉 → **静默丢正文**。"""
        text = "## Exercise 5 answer\nAverage cost is AC = q^2 - 12q + 60."
        chunks = mt.split_chunks(text)
        self.assertEqual([c.heading for c in chunks], ["Exercise 5 answer"], chunks)
        self.assertIn("Average cost", chunks[0].text, "标题后的正文被丢了")

    def test_标题后正文也进检索与核对(self):
        text = ("## Exercise 5 answer\n"
                "The shutdown price equals the minimum of the average cost.")
        idx = mt.Materials([mt.Chunk(file="f.md", heading=c.heading, text=c.text)
                            for c in mt.split_chunks(text)])
        self.assertTrue(idx.verify("shutdown price"), "正文丢了 → 核对判成没出现过")

    def test_多个标题连排每段各自成块(self):
        text = "# A\n# B\nbody of B"
        chunks = mt.split_chunks(text)
        self.assertEqual([c.heading for c in chunks], ["B"], chunks)
        self.assertIn("body of B", chunks[0].text)


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

    def test_无标题材料的块不被当成同一节吞掉(self):
        """DB 审查 D-1：没有 markdown 标题的材料（`.txt`）所有块 `heading` 都是 `""` ——
        旧键 `(file, heading)` 相同 → 只能返回得分最高的一块，top-k 退化成 1 块。"""
        q = "transfer pricing elasticity of demand"
        body = "\n\n".join(f"{q} " * 8 + f"这是第 {i} 段的独有内容。" for i in range(3))
        chunks = [mt.Chunk(file="notes.txt", heading=c.heading, text=c.text)
                  for c in mt.split_chunks(body)]
        self.assertGreater(len(chunks), 1, "夹具本身该切成多块")
        idx = mt.Materials(chunks)
        got = idx.search(q, k=3)
        self.assertGreater(len(got), 1, "无标题文件只返回了一块（top-k 退化成 1）")
        self.assertEqual({c.text for c in got}, {c.text for c in chunks})

    def test_只有一个标题的材料也不被压成一块(self):
        """同一形状的第二例：通篇只有一个 `# 标题`（整份材料 = 一节）—— 按节去重等于
        把整份材料压成一块，静默丢内容。"""
        q = "transfer pricing elasticity of demand"
        text = "# ECON10790 Final Exam\n\n" + "\n\n".join(
            f"{q} " * 8 + f"第 {i} 题。" for i in range(3))
        chunks = [mt.Chunk(file="exam.md", heading=c.heading, text=c.text)
                  for c in mt.split_chunks(text)]
        heads = {c.heading for c in chunks}
        self.assertEqual(len(heads), 1, heads)          # 真的只有一个节
        idx = mt.Materials(chunks)
        got = idx.search(q, k=3)
        self.assertGreater(len(got), 1, "单节材料被压成一块（静默丢内容）")

    def test_多节文件的同一节仍然只留一块(self):
        """去重的本意（同一节不重复取、省 prompt）不许被上面的修复破坏。"""
        idx = mt.Materials([mt.Chunk(file="f.md", heading=h,
                                     text=f"word alpha part {i} {h}")
                            for i, h in enumerate(["S1", "S2", "S3"])])
        got = idx.search("word alpha part", k=3)
        heads = [c.heading for c in got]
        self.assertEqual(len(heads), len(set(heads)), f"同一节取了两块：{heads}")


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
