"""正文纪律的判据：**不许猜测**（`note_guards.scrub`）+ **引号必须逐字**（`quote_check`）
+ **更正账本**（`note_ledger`）。

⭐ 三条反例都是作者 2026-10-17 验收时从**真笔记**里逐字抄出来的：
   「说明我可能听错了公式」「或者我漏掉了 q 的一次项」
   「可能是简化处理（假设固定成本为零或忽略）」
   以及那句拼接的假引用「the minimum of the average cost… the lowest acceptable price」。

⚠️ 更正账本的固定用例来自真转录（`sessions/2026-10-15_131620_ECON10790.md`）：
   13:40:30 教授把 MC 念成 `−12Q`，13:40:41 自己重述成 `−24Q`；连跑两次时
   一次笔记写对、一次写了回去 —— 这就是"结果不稳定"那条。
"""
import os
import pathlib
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import note_guards as ng          # noqa: E402
import note_ledger as nl          # noqa: E402


class Speculation(unittest.TestCase):
    def _one(self, sentence: str):
        rev = {"sections": [{"heading": "x", "ts": "13:16", "prose": [sentence]}]}
        return ng.scrub(rev)

    def test_作者点名的三句都被删(self):
        for s in ("如果函数是这样，那么求导并令导数为零：无解，说明我可能听错了公式。",
                  "所以原题可能不是这个函数，或者我漏掉了 q 的一次项。",
                  "本课采用 AC 最低点作为停产价格，可能是简化处理（假设固定成本为零或忽略）。"):
            with self.subTest(s=s[:14]):
                out, removed, warns = self._one(s)
                self.assertTrue(removed, f"没删：{s}")
                left = " ".join(out["sections"][0]["prose"])
                self.assertNotIn("我", left, f"删了还留着第一人称：{left}")
                self.assertNotIn("可能", left, f"删了还留着猜测：{left}")
                self.assertTrue(warns)

    def test_中文写作习惯不许误伤(self):
        for s in ("考试时可能需要先求导再代入。", "我们来看这一节的图。",
                  "价格等于最低平均成本时企业刚好不亏不赚。"):
            out, removed, _w = self._one(s)
            self.assertEqual(removed, [], f"误删：{s}")
            self.assertEqual(out["sections"][0]["prose"], [s])

    def test_讲片段的那句补固定格式(self):
        rev = {"sections": [{"prose": ["具体怎么算的？如果函数是 AC(q)=60+500/q，"
                                       "那么说明我可能听错了公式。"]}]}
        out, removed, _w = ng.scrub(rev)
        body = " ".join(out["sections"][0]["prose"])
        self.assertIn(ng.FIXED_LINE, body, "不确定的事没有走固定格式")
        self.assertIn("具体怎么算的？", body, "同一段里的事实句被一起删了")

    def test_每一层文本都过一遍(self):
        """例题步骤/答案/变式/补全题里的猜测句同样要删（只清 prose = 漏一半）。"""
        rev = {"sections": [{"example": {"title": "🎙 片段", "prompt": "题面",
                                         "steps": [{"text": "可能是简化处理（假设忽略固定成本）。",
                                                    "why": "我漏掉了 q 的一次项。"}],
                                         "answer": "我可能听错了。"},
                             "variants": [{"q": "可能是别的函数。", "steps": ["我估计错了。"], "a": "6"}],
                             "faded": {"q": "可能是别的数。", "a": "我漏掉了。"}}]}
        out, removed, _w = ng.scrub(rev)
        ex = out["sections"][0]
        self.assertGreaterEqual(len(removed), 5, removed)
        self.assertEqual([st["text"] for st in ex["example"]["steps"] if st["text"]], [])
        self.assertEqual(ex["variants"][0]["a"], "6", "干净的内容被误删")


class Quotes(unittest.TestCase):
    # 真材料里的两段之间隔着一段推导（实测：Dec-2025 官方解答 Exercise 7(ii)）——
    # 距离就是判据的一部分，所以这里照抄"隔着推导"的形状。
    BLOB = ("the firm will supply a positive quantity so long as the price is above the "
            "minimum of the average cost. the value for q that minimizes the average cost is "
            "such that, at that value, ac = mc → 2q + 1 + 32 / q = 4q + 1 → 2q = 32 / q → q = 4. "
            "at that value we have ac = mc = 17 which is the lowest acceptable price !")

    def _run(self, text, transcript=""):
        rev = {"sections": [{"prose": [text], "example": {"prompt": text}}]}
        mat = type("M", (), {"_blob": self.BLOB})()
        return ng.quote_check(rev, materials=mat, transcript=transcript)

    def test_拼接的引用被去掉引号(self):
        out, stripped, warns = self._run(
            "原话是“the minimum of the average cost… the lowest acceptable price”。")
        self.assertGreaterEqual(len(stripped), 1)      # prose 与 example 各有一处
        self.assertIn("（转述）", out["sections"][0]["prose"][0])
        self.assertNotIn("“", out["sections"][0]["prose"][0])
        self.assertTrue(warns)

    def test_逐字的引用留着(self):
        out, stripped, _w = self._run("原话是“the lowest acceptable price”。")
        self.assertEqual(stripped, [])
        self.assertIn("“the lowest acceptable price”", out["sections"][0]["prose"][0])

    def test_相邻两段的省略号拼接可以留(self):
        out, stripped, _w = self._run(
            "“the minimum of the average cost. the value for q that minimizes”")
        self.assertEqual(stripped, [], out["sections"][0]["prose"][0])

    def test_中文引号不动(self):
        out, stripped, _w = self._run("教授用了「山峰」来比喻凸函数。")
        self.assertEqual(stripped, [])

    def test_转录也算出处(self):
        out, stripped, _w = self._run("他说“goes down the mountain”。",
                                      transcript="if it goes down the mountain, the average cost is falling")
        self.assertEqual(stripped, [])


class Ledger(unittest.TestCase):
    def test_转录里抓到教授那次重述(self):
        rows = [
            {"ts": "13:40:29", "en": "for marginal cost equal to average cost at this value.", "asr": ""},
            {"ts": "13:40:30", "en": "So you would have Q squared minus 12 Q plus 60 equal "
                                     "3 Q squared minus 12 Q plus 60.", "asr": ""},
            {"ts": "13:40:41", "en": "Q squared minus 24 Q plus 60.", "asr": ""},
        ]
        got = nl.from_transcript(rows)
        restate = [x for x in got if x["kind"] == "restate" and x["ts"] == "13:40:41"]
        self.assertTrue(restate, got)
        self.assertIn("minus 24", restate[0]["text"])

    def test_闲聊里的_sorry_不算(self):
        rows = [{"ts": "13:26:59", "en": "Because I'm sorry. You're processing, yes. Okay.", "asr": ""}]
        self.assertEqual(nl.from_transcript(rows), [])

    def test_材料里的公式读得出来_含_OCR_怪字形(self):
        mat = type("M", (), {"_chunks": [
            type("C", (), {"text": "𝑇𝐶(𝑞) = 𝑞! − 12𝑞\" + 60𝑞,", "file": "dec-2025-correction.md",
                           "heading": "Exercise 5"})(),
            type("C", (), {"text": "TC(q) = 3q \" + 4q + 2", "file": "2024-correction.md",
                           "heading": "Exercise 1"})(),
        ]})()
        got = {(f["name"], f["expr"]) for f in nl.from_materials(mat)}
        self.assertIn(("TC", "q^3 − 12q^2 + 60q"), got)
        self.assertIn(("TC", "3q^2 + 4q + 2"), got)


class Reconcile(unittest.TestCase):
    """⭐ 固定用例：`−12Q → −24Q`（教授 13:40:41 自己更正过的那处）。"""

    def _rev(self, mc: str, tc: str = "Q^3-12Q^2+60Q"):
        return {"sections": [{
            "heading": "12. 平均成本最小化与MC=AC条件", "ts": "13:39",
            "prose": ["边际成本是总成本对 Q 的导数。"],
            "example": {"title": "🎙 13:39:41 平均成本最小化", "prompt": f"TC(Q)={tc}",
                        "steps": [{"text": f"MC(Q)=TC'(Q)={mc}"},
                                  {"text": "令 MC=AC 得 Q=6"}],
                        "answer": f"MC(Q)={mc}，最小平均成本 $24$"}}]}

    def test_写回口误版会被自动改正(self):
        out, fixes, warns = nl.reconcile(self._rev("3Q^2-12Q+60"))
        self.assertTrue(fixes, "口误版没被拦下")
        self.assertIn("3Q^{2}-24Q+60", fixes[0]["fixed"])
        self.assertEqual(fixes[0]["where"], "12. 平均成本最小化与MC=AC条件")
        body = out["sections"][0]["example"]
        self.assertIn("3Q^{2}-24Q+60", body["answer"])
        self.assertIn("3Q^{2}-24Q+60", body["steps"][0]["text"])
        self.assertTrue(warns)

    def test_写对的就不动(self):
        rev = self._rev("3Q^2-24Q+60")
        out, fixes, warns = nl.reconcile(rev)
        self.assertEqual(fixes, [])
        self.assertEqual(out["sections"][0]["example"]["answer"],
                         rev["sections"][0]["example"]["answer"])

    def test_AC_也要与_TC_over_q_自洽(self):
        rev = {"sections": [{"heading": "x", "prose": ["AC(Q)=Q^2-12Q+600。", "TC(Q)=Q^3-12Q^2+60Q。"]}]}
        out, fixes, _w = nl.reconcile(rev)
        self.assertTrue(any("AC" in f["spoken"] for f in fixes), fixes)
        self.assertIn("Q^{2}-12Q+60", out["sections"][0]["prose"][0])

    def test_变式的自造函数不误伤(self):
        """变式题本来就可以自己设函数 —— 只要它自己自洽，不许改。"""
        rev = {"sections": [{"variants": [{"q": "设 TC(Q)=Q^2+64", "steps": ["MC(Q)=2Q"],
                                          "a": "停产价格 16"}]}]}
        out, fixes, warns = nl.reconcile(rev)
        self.assertEqual(fixes, [], "变式的自造函数被误改了")
        self.assertEqual(warns, [])

    def test_没有_TC_就不猜(self):
        rev = {"sections": [{"prose": ["MC(q)=3q^2-12q+60"], "example": {"prompt": "MC(q)=3q^2-12q+60"}}]}
        _out, fixes, warns = nl.reconcile(rev)
        self.assertEqual(fixes, [])

    def test_多项式工具(self):
        p = nl.Poly({3: 1, 2: -12, 1: 60})
        self.assertTrue(p.deriv().close(nl.Poly({2: 3, 1: -24, 0: 60})))
        self.assertTrue(p.div_var().close(nl.Poly({2: 1, 1: -12, 0: 60})))
        self.assertEqual(p.deriv().show("Q"), "3Q^{2}-24Q+60")
        self.assertEqual(nl.Poly({2: 3, 1: 1}).div_var().show("q"), "3q+1")
        self.assertIsNone(nl.Poly({1: 1, -1: 2}).div_var())


class Caliber(unittest.TestCase):
    """⚠️ 同一节课连跑两次：一次有 AVC 对照、一次没有（作者第 2 轮验收点名要它）。"""

    def test_缺了就按固定句式补上(self):
        rev = {"sections": [{"heading": "1. 停产价格（Shutting Down Price）", "ts": "13:16",
                             "prose": ["停产价格是价格临界点。"]}]}
        out, added, warns = ng.ensure_caliber(rev)
        body = " ".join(out["sections"][0]["prose"])
        self.assertIn("AVC", body)
        self.assertIn("break-even", body)
        self.assertTrue(added and warns)

    def test_已经写了就不动(self):
        rev = {"sections": [{"heading": "停产价格", "prose": ["标准理论中停产价格是 AVC 的最低点。"]}]}
        out, added, _w = ng.ensure_caliber(rev)
        self.assertEqual(added, [])
        self.assertEqual(out["sections"][0]["prose"], ["标准理论中停产价格是 AVC 的最低点。"])

    def test_不涉及停产价格的节不补(self):
        rev = {"sections": [{"heading": "三次函数求导", "prose": ["多项式逐项求导。"]}]}
        _out, added, _w = ng.ensure_caliber(rev)
        self.assertEqual(added, [])


class Wiring(unittest.TestCase):
    def test_三步都在复习层里接线了(self):
        src = pathlib.Path(__file__).resolve().parent.parent.joinpath("obsidian_writer.py").read_text()
        self.assertIn("note_ledger.reconcile", src)
        self.assertIn("note_guards.scrub", src)
        self.assertIn("note_guards.quote_check", src)
        self.assertIn("note_ledger.block", src, "账本没喂进 prompt")
        self.assertIn("note_guards.ensure_caliber", src, "口径对照没接线")

    def test_prompt_里有两条纪律(self):
        import note_v2
        for key in ("⚠ 课上只给了片段", "不许第一人称", "引号"):
            self.assertIn(key, note_v2.REVIEW_SYS, f"prompt 缺纪律：{key}")


if __name__ == "__main__":
    unittest.main()
