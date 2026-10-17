"""确定性覆盖度（`note_cover`）的判据。

⭐ 作者 2026-10-17 第四轮：同一节课，A–C 报「教授知识点 33 个」、E 报 20 个，
   13:16–13:23 的「停产价格 / Exercise 1 三次函数」直接掉出了知识点地图。
   口径改成「章节 + 实时要点」，下面用**10-15 那节课的真实数据形状**钉住它
   （fixture 是从 `sessions/2026-10-15_131620_ECON10790.{chapters,atoms}.jsonl`
   抄下来的字段，不读真文件 —— 测试要能离线跑）。
"""
import os
import pathlib
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import note_cover as nc          # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: 10-15 那节课的三章（最后一条 final 就是笔记里「课堂纲要」的那三行）
CHAPTERS = [
    {"type": "chapter", "id": 0, "status": "interim", "title": "Optimization Practice: Cubic Function",
     "title_zh": "优化练习：三次函数", "t0": "13:16:28", "t1": "13:25:17"},
    {"type": "chapter", "id": 0, "status": "final",
     "title": "Optimization Practice and Shutdown Price", "title_zh": "最优化练习与停业价格",
     "t0": "13:16:28", "t1": "13:26:35"},
    {"type": "chapter", "id": 1, "status": "final",
     "title": "Average Cost, Marginal Cost, and Their Interaction",
     "title_zh": "平均成本、边际成本及其相互作用", "t0": "13:26:47", "t1": "13:37:18"},
    {"type": "chapter", "id": 2, "status": "final", "title": "Two Ways to Minimize a Function",
     "title_zh": "最小化函数的两种方法", "t0": "13:37:43", "t1": "13:42:12"},
]

#: 真实要点（节选：13:16–13:23 那一段 + 一条课务 + 别的章的）
ATOMS = [
    {"id": 0, "t": "13:17:46", "kind": "要点", "text": "The value is close to 3, about 2.89.",
     "terms": [], "zh": "该数值接近 3，约为 2.89。"},
    {"id": 2, "t": "13:17:46", "kind": "要点",
     "text": "The question also asks for the lowest acceptable price, the shutting down price.",
     "terms": [["shutting down price", "停产价格"]], "zh": "这道题还要求最低可接受价格，即停产价格。"},
    {"id": 3, "t": "13:17:46", "kind": "定义",
     "text": "The shutting down price equals the average cost at this quantity, 2.89.",
     "terms": [["average cost", "平均成本"]], "zh": "停产价格等于该产量下的平均成本，即 2.89。"},
    {"id": 5, "t": "13:19:06", "kind": "课务", "text": "The instructor will show last year's final exam.",
     "terms": [], "zh": "讲师会展示去年的期末考试题供练习。"},
    {"id": 9, "t": "13:22:10", "kind": "例子", "text": "For the cubic function, the first derivative is 3x squared minus 3.",
     "terms": [["first derivative", "一阶导数"]], "zh": "对于三次函数，一阶导数是 3x 平方减 3。"},
    {"id": 20, "t": "13:29:31", "kind": "要点", "text": "Average cost is total cost divided by quantity.",
     "terms": [["average cost", "平均成本"]], "zh": "平均成本是总成本除以产量。"},
    {"id": 30, "t": "13:39:00", "kind": "要点", "text": "Set the derivative to zero.",
     "terms": [], "zh": "要使函数最小化，令其导数等于零。"},
]

#: 转录分块（`[HH:MM:SS]` 开头 —— `lines_range` 认这个形状）
CHUNK_EARLY = [
    "[13:16:28] Okay so let's look at the question about the shutting down price.",
    "[13:17:46] The value is close to 3, about 2.89.",
    "[13:22:10] For the cubic function, the first derivative is 3x squared minus 3.",
]
CHUNK_LATE = [
    "[13:39:00] Set the derivative to zero — that is the first way to minimize.",
    "[13:40:20] Another way is to set marginal cost equal to average cost.",
]


class Skeleton(unittest.TestCase):
    def test_三章与要点都从数据来(self):
        sk = nc.skeleton(CHAPTERS, ATOMS)
        self.assertEqual([c["i"] for c in sk], [1, 2, 3])
        self.assertEqual(sk[0]["title"], "最优化练习与停业价格")
        self.assertEqual([p["zh"] for p in sk[0]["points"]], [
            "该数值接近 3，约为 2.89。",
            "这道题还要求最低可接受价格，即停产价格。",
            "停产价格等于该产量下的平均成本，即 2.89。",
            "对于三次函数，一阶导数是 3x 平方减 3。"])
        self.assertEqual(len(sk[1]["points"]), 1)
        self.assertEqual(len(sk[2]["points"]), 1)

    def test_课务不是知识点(self):
        sk = nc.skeleton(CHAPTERS, ATOMS)
        blob = " ".join(p["zh"] for c in sk for p in c["points"])
        self.assertNotIn("期末考试题供练习", blob, "课务不该进知识点清单（它走纲要的课务段）")

    def test_临时章顶掉_正式章只留一条(self):
        sk = nc.skeleton(CHAPTERS, [])
        self.assertEqual(len(sk), 3)
        self.assertNotIn("优化练习：三次函数", [c["title"] for c in sk], "interim 章不许进骨架")

    def test_时间窗外的要点不要(self):
        sk = nc.skeleton(CHAPTERS, ATOMS)
        self.assertNotIn("平均成本是总成本除以产量。",
                         [p["zh"] for p in sk[0]["points"]])

    def test_坏时间戳不猜(self):
        self.assertIsNone(nc.hms("13:5"))
        self.assertIsNone(nc.hms("99:00:00"))
        self.assertIsNone(nc.hms(""))
        self.assertEqual(nc.hms("13:16:28"), 13 * 3600 + 16 * 60 + 28)

    def test_重复要点只留一条(self):
        dup = ATOMS + [dict(ATOMS[0], id=99)]
        sk = nc.skeleton(CHAPTERS, dup)
        self.assertEqual(len([p for p in sk[0]["points"] if p["zh"] == "该数值接近 3，约为 2.89。"]), 1)


class BlockFor(unittest.TestCase):
    """⭐ 13:16–13:23 不再掉出去的那道闸：分块 prompt 里明文列出该段的章与要点。"""

    def test_早段的分块能看到第一章与停产价格(self):
        sk = nc.skeleton(CHAPTERS, ATOMS)
        block = nc.block_for(sk, CHUNK_EARLY)
        self.assertIn("【本课章节骨架", block, "骨架提示的抬头没了")
        self.assertIn("章 1「最优化练习与停业价格」", block)
        self.assertIn("停产价格等于该产量下的平均成本，即 2.89。", block)
        self.assertIn("对于三次函数，一阶导数是 3x 平方减 3。", block)
        self.assertNotIn("章 2「", block, "与本块不相交的章不该出现")

    def test_只列本段时间的要点(self):
        """⚠️ run F 的教训：把整章要点塞给每一块 → 输出超上限 → 整块静默消失。"""
        sk = nc.skeleton(CHAPTERS, ATOMS)
        block = nc.block_for(sk, ["[13:16:28] Opening line."])
        self.assertNotIn("该数值接近 3，约为 2.89。", block, "13:17:46 的要点不在本段，不该列")
        self.assertIn("本章还有 4 条要点落在别的段里", block, "其余要点要记账（别装作没有）")

    def test_晚段的分块只看第三段(self):
        sk = nc.skeleton(CHAPTERS, ATOMS)
        block = nc.block_for(sk, CHUNK_LATE)
        self.assertIn("章 3「最小化函数的两种方法」", block)
        self.assertIn("要使函数最小化，令其导数等于零。", block)
        self.assertNotIn("章 1「", block)

    def test_没有骨架就是空串(self):
        self.assertEqual(nc.block_for([], CHUNK_EARLY), "")
        self.assertEqual(nc.block_for(nc.skeleton(CHAPTERS, ATOMS), ["没有时间戳的一行"]), "")


class Report(unittest.TestCase):
    """生成后核对：**每一章至少要有一个知识点落到某一节**。"""

    def _sk(self):
        return nc.skeleton(CHAPTERS, ATOMS)

    def test_作者抓到的那个缺口会被报出来(self):
        """E 的形态：节从 13:23 才开始，13:16–13:23 的内容没有独立知识点。

        ⚠️ 章节级"有没有节"是拦不住的（13:23 落在第一章窗口里）——
           必须**逐条要点**核：停产价格那两条没落到任何一节 → ⚠。
        """
        secs = [{"heading": "1. [13:23] 二阶导数与凹凸性", "ts": "13:23"},
                {"heading": "2. [13:29] 平均成本", "ts": "13:29"}]
        # 编号是全局的：章 1 = 1..4、章 2 = 5、章 3 = 6
        cov = [{"i": 5, "section": "2. [13:29] 平均成本"},      # 章 2 的那一条
               {"i": 6, "section": "2. [13:29] 平均成本"}]      # 章 3 的那一条
        rep = nc.report(self._sk(), secs, cov)
        rows = {r["i"]: r for r in rep["rows"]}
        self.assertFalse(rows[1]["ok"], "第一章（停产价格 / Exercise 1）该报 ⚠")
        self.assertEqual(rep["n_chapters"], 3)
        self.assertTrue(rows[2]["ok"] and rows[3]["ok"])
        miss = {m["point"] for m in rep["missing"]}
        self.assertIn("停产价格等于该产量下的平均成本，即 2.89。", miss)
        self.assertEqual(rep["n_covered"], 2, "只有两条落到了节里")

    def test_全覆盖时全绿(self):
        secs = [{"heading": f"{i}. 节", "ts": t} for i, t in
                enumerate(("13:18", "13:30", "13:40"), 1)]
        cov = [{"i": i, "section": f"{c}. 节"}
               for c, gis in ((1, [1, 2, 3, 4]), (2, [5]), (3, [6])) for i in gis]
        rep = nc.report(self._sk(), secs, cov)
        self.assertEqual(rep["n_ok"], 3)
        self.assertEqual(rep["n_covered"], rep["n_points"])
        self.assertEqual(rep["missing"], [])

    def test_一章没有任何节也要报(self):
        rep = nc.report(self._sk(), [{"heading": "1. 甲", "ts": "13:30"}],
                        [{"i": 5, "section": "1. 甲"}])
        rows = {r["i"]: r for r in rep["rows"]}
        self.assertFalse(rows[3]["ok"])
        self.assertIn("这一章的要点一条都没落到节里", rows[3]["why"])

    def test_编号是全局的_不是逐章重来(self):
        """⚠️ 自查抓到过：逐章 `flat_points([ch])` → 每章 `gi` 都从 1 开始 → 对齐全错位。"""
        flat = nc.flat_points(self._sk())
        self.assertEqual([p["gi"] for p in flat], list(range(1, len(flat) + 1)))
        self.assertEqual([p["chapter"] for p in flat], [1, 1, 1, 1, 2, 3])


class Wiring(unittest.TestCase):
    """接线：骨架要真进 prompt、真进对齐、真进笔记（都别只写在文档里）。"""

    def _src(self, name):
        return (ROOT / name).read_text(encoding="utf-8")

    def test_review_收骨架并逐块注入(self):
        src = self._src("note_v2.py")
        self.assertIn("skeleton: list | None = None", src)
        self.assertIn("note_cover.block_for(skeleton, chunk)", src)

    def test_对齐用骨架的编号(self):
        src = self._src("note_v2.py")
        self.assertIn("ALIGN_SYS_SK", src)
        self.assertIn("note_cover.flat_points(skeleton)", src)

    def test_写端接线(self):
        src = self._src("obsidian_writer.py")
        self.assertIn("note_cover.skeleton", src, "骨架没建")
        self.assertIn('review["cover"] = note_cover.report(', src, "报告没落进 review")
        self.assertIn("skeleton=sk", src, "骨架没传给 review/align")

    def test_两处渲染都用它(self):
        rd = self._src("note_render.py")
        self.assertIn("cv['n_ok']", rd, "md 页头没报章节覆盖")
        self.assertIn("章节覆盖", rd, "md §9 没有章节覆盖表")
        ht = self._src("note_html.py")
        self.assertIn('id="checks"', ht, "HTML 的「点开看」锚点还是死链")


if __name__ == "__main__":
    unittest.main(verbosity=2)
