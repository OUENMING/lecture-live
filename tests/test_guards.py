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
import note_math as nm            # noqa: E402


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


class SelfTalk(unittest.TestCase):
    """⭐ 第六轮第 3 条：**自言自语**（模型的思考过程漏进答案）+ 清理覆盖全字段。

    用例是 ECON10770（10-09 厨房清洁博弈）产物里变式 2 答案的原话节选 ——
    不是 10-15 那节课的句子（作者要求新判据不许再只用一节课的语料）。
    """
    #: 产物原文（节选）：整段是模型在跟自己讨论，读者会以为那是答案。
    TALK = ("所以（打扫，不打扫）也是纳什均衡？检查：若行打扫、列不打扫，行得0，列得4；"
            "行偏离到不打扫得0，无利可图。这不对，因为行在列不打扫时无差异。"
            "但通常我们要求严格偏好？")

    def test_变式答案里的自言自语被删(self):
        rev = {"sections": [{"variants": [
            {"q": "变式的题面",
             "a": "纳什均衡仍是（不打扫，不打扫），每人得0。" + self.TALK}]}]}
        out, removed, warns = ng.scrub(rev)
        self.assertTrue(removed, "没删掉自言自语")
        self.assertTrue(warns)
        a = out["sections"][0]["variants"][0]["a"]
        self.assertNotIn("检查：", a)
        self.assertNotIn("这不对", a)
        self.assertIn("纳什均衡仍是（不打扫，不打扫），每人得0。", a, "干净的那一句被连带删了")

    def test_整段都是独白的答案_宁可不出(self):
        """宁缺：答案是**整段**自言自语 → 这道变式不出（留半道比没有更坏）。"""
        rev = {"sections": [{"variants": [{"q": "变式题面", "a": self.TALK}]}]}
        out, removed, warns = ng.scrub(rev)
        self.assertTrue(removed)
        self.assertEqual(out["sections"][0]["variants"], [], "整段独白的答案不该留下")
        self.assertTrue(any("整题不出" in w for w in warns), warns)

    def test_自测题干是问句_不许当独白删(self):
        """⚠️ 判据实测过的坑：自测的题干本来就以「？」收尾 —— 按解答清理会把题干删空。"""
        rev = {"qa": [{"q": "什么是公共物品？为什么市场会供给不足？",
                       "a": "既无竞争性也无排他性。搭便车让人不肯出钱。"}]}
        out, removed, _w = ng.scrub(rev)
        self.assertEqual(removed, [])
        self.assertEqual(out["qa"][0]["q"], "什么是公共物品？为什么市场会供给不足？")

    def test_哪些字段算解答(self):
        """`_solution_like` 的分类表（判据直接钉它，省得靠用例间接表达）。"""
        yes = [("sections", 0, "variants", 0, "a"), ("sections", 0, "example", "answer"),
               ("sections", 0, "faded", "a"), ("sections", 0, "variants", 0, "steps", 1, "text"),
               ("qa", 0, "a")]
        no = [("sections", 0, "variants", 0, "q"), ("sections", 0, "example", "prompt"),
              ("sections", 0, "variants", 0, "steps", 1, "why"), ("qa", 0, "q"),
              ("sections", 0, "prose", 1), ("checks", 0, "claim")]
        for p in yes:
            self.assertTrue(ng._solution_like(p), p)
        for p in no:
            self.assertFalse(ng._solution_like(p), p)

    def test_解答里的问句才算独白(self):
        """同一条文字：落在讲解里留着，落在解答里删掉。"""
        out, removed, _w = ng.scrub(
            {"sections": [{"prose": ["为什么会这样？因为价格等于边际成本。"],
                           "variants": [{"q": "题面", "a": "为什么会这样？因为价格等于边际成本时没人能改变结果。"}]}]})
        self.assertEqual(out["sections"][0]["prose"], ["为什么会这样？因为价格等于边际成本。"])
        self.assertIn("为什么会这样？", out["sections"][0]["variants"][0]["a"])

    def test_答案被清空就整道变式不出(self):
        """宁缺：答案是**整段**自言自语 → 这道变式不出（留半道比没有更坏）。"""
        rev = {"sections": [{"variants": [{"q": "题面", "a": self.TALK.split("。")[1] + "。"},
                                          {"q": "题的题面", "a": "$q=3$"}]}]}
        out, removed, warns = ng.scrub(rev)
        vs = out["sections"][0]["variants"]
        self.assertEqual([v["q"] for v in vs], ["题的题面"], vs)
        self.assertEqual(vs[0]["a"], "$q=3$")
        self.assertTrue(any("整题不出" in w for w in warns), warns)

    def test_短答案不许被扔(self):
        """⚠️ 判据实测过的坑：加"答案太短就不出"会扔掉真题（`$q=3$` 这种）。"""
        rev = {"sections": [{"variants": [{"q": "题面", "a": "6"}]}]}
        out, _r, _w = ng.scrub(rev)
        self.assertEqual(len(out["sections"][0]["variants"]), 1)
        self.assertEqual(out["sections"][0]["variants"][0]["a"], "6")

    def test_全字段都过一遍(self):
        """自测 / 核对表 / 术语 / 复习安排里的话也要清（原来只清 prose）。"""
        rev = {"sections": [{"heading": "h", "prose": ["正常讲解。"],
                             "terms": [{"en": "public good", "zh": "我可能记错的中文。"}]}],
               "qa": [{"q": "自测题？", "a": "我估计错了。"}],
               "checks": [{"claim": "4-3=1", "how": "这不对，因为说不通", "status": "unchecked"}],
               "review_plan": ["重新检查一遍全局均衡。"],
               "corrections": [{"where": "x", "spoken": "s", "fixed": "我漏掉了 3。"}]}
        out, removed, _w = ng.scrub(rev)
        self.assertGreaterEqual(len(removed), 4, removed)
        self.assertEqual(out["qa"], [], "自测的答案空了 → 整条不出")
        self.assertNotIn("这不对", out["checks"][0]["how"])
        self.assertEqual(out["review_plan"], [])
        self.assertEqual(out["corrections"][0]["fixed"], "")
        self.assertEqual(out["sections"][0]["terms"][0]["zh"], "")
        self.assertEqual(out["sections"][0]["prose"], ["正常讲解。"])

    def test_正经问句不误伤(self):
        for s in ("为什么会这样？因为价格等于边际成本时没人能改变结果。",
                  "等等，这里要先看不变量是什么。",  # 反面：这句**是**独白 → 见下一条
                  ):
            if s.startswith("等等"):
                continue
            out, removed, _w = ng.scrub({"sections": [{"prose": [s]}]})
            self.assertEqual(removed, [], f"误删：{s}")

    def test_等等这类口头语只删独白句(self):
        out, removed, _w = ng.scrub({"sections": [{"prose": ["等等，给我一分钟。"]}]})
        self.assertEqual(len(removed), 1, removed)
        self.assertEqual(out["sections"][0]["prose"], [])

    def test_遍历带路径(self):
        got = ng.paths_of({"sections": [{"variants": [{"a": "x"}]}], "qa": ["y"]})
        self.assertIn(("sections", 0, "variants", 0, "a"), got)
        self.assertIn(("qa", 0), got)


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

    def test_核定表里的引用也要核(self):
        """A 那份的核定表「断言」列里就留着一句拼接引用（正文里已经去掉了）。"""
        rev = {"sections": [{"prose": ["材料把停产价格定义为 AC 的最低点。"]}],
               "checks": [{"claim": "“the minimum of the average cost… the lowest acceptable price”",
                           "how": "材料原文", "status": "ok"}]}
        out, stripped, _w = self._run_review(rev)
        self.assertTrue(stripped)
        self.assertIn("（转述）", out["checks"][0]["claim"])
        self.assertNotIn("“", out["checks"][0]["claim"])

    def _run_review(self, rev):
        mat = type("M", (), {"_blob": self.BLOB})()
        return ng.quote_check(rev, materials=mat)

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

    def test_关系句_MC等于AC_不许当定义(self):
        """⚠️ 第三轮验收抓到的**误报**（产物 C 第 20 行）：教授说的「MC(q)=AC(q)」是
        "令两者相等"的条件句，不是给 MC 下定义 —— 当时拿它跟 TC 的导数比，报了
        "MC 与 TC 不自洽（AC(q) → 3q²−24q+60）"，还把正文里这个字面量改掉了。

        ⚠️ 两档守卫是**冗余**的（单关一档也能拦住；同时关掉才复现原始误报）——
           这是故意的：这类误报会改正文，代价太高。
        """
        rev = {"sections": [{"heading": "13:39 平均成本最小化", "prose": [
            "AC(q)=q^2-12q+60。", "TC(q)=q^3-12q^2+60q。", "令 MC(q)=AC(q)，解得 q=6。"]}]}
        out, fixes, warns = nl.reconcile(rev)
        self.assertEqual(fixes, [], "把关系句当定义了")
        self.assertEqual(warns, [])
        self.assertEqual(out["sections"][0]["prose"][-1], "令 MC(q)=AC(q)，解得 q=6。",
                         "正文里教授的原话被改了")

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
    """停产价格口径：**只在课件里找得到出处时**才写对照，并带出处（作者第六轮第 5 条）。

    ⚠️ 原来是一句写死的话（`CALIBER_LINE`），任何课提到停产价格就插 ——
       别的课（10740 / SOC10020）提到停产价格时，插进去的是一句**假的本课口径**。
    """

    MATERIAL = ("the firm will supply a positive quantity so long as the price is above the "
                "minimum of the average cost. that is the lowest acceptable price for this firm.")

    def _mats(self, blob):
        class H:
            def __init__(self, text, cite):
                self.text, self._c = text, cite

            def cite(self):
                return f"📖 {self._c}"

        class M:
            def __init__(self):
                self._blob = blob

            def search(self, q, k=3):
                return [H(blob, "2025-12 期末卷 Exercise 7")] if blob else []
        return M()

    def test_课件里有出处就补对照_并带出处(self):
        rev = {"sections": [{"heading": "停产价格（Shutting Down Price）", "ts": "13:16",
                             "prose": ["停产价格是价格临界点。"]}]}
        out, added, warns = ng.ensure_caliber(rev, materials=self._mats(self.MATERIAL))
        body = " ".join(out["sections"][0]["prose"])
        self.assertIn("AVC", body)
        self.assertIn("break-even", body)
        self.assertIn("2025-12 期末卷 Exercise 7", body, "对照必须带出处")
        self.assertNotIn("📖 📖", body, "出处标记不该叠两层（cite 自带 📖）")
        self.assertTrue(added and warns)

    def test_没有课件就一个字不写(self):
        """宁缺：机器没资格替这门课断言"我们的口径和教科书不一样"。"""
        rev = {"sections": [{"heading": "停产价格", "prose": ["停产价格是价格临界点。"]}]}
        out, added, _w = ng.ensure_caliber(rev)
        self.assertEqual(added, [])
        self.assertEqual(out["sections"][0]["prose"], ["停产价格是价格临界点。"])
        out2, added2, _w2 = ng.ensure_caliber(rev, materials=self._mats(""))
        self.assertEqual(added2, [])

    def test_课件本身就是_AVC_口径就不写对照(self):
        rev = {"sections": [{"heading": "停产价格", "prose": ["停产价格是价格临界点。"]}]}
        blob = ("shut down if the price falls below the minimum of the average variable cost "
                "(AVC), that is the lowest acceptable price.")
        _out, added, _w = ng.ensure_caliber(rev, materials=self._mats(blob))
        self.assertEqual(added, [], "课件与教科书一致时不该硬说本课口径不同")

    def test_已经写了就不动(self):
        rev = {"sections": [{"heading": "停产价格", "prose": ["标准理论中停产价格是 AVC 的最低点。"]}]}
        out, added, _w = ng.ensure_caliber(rev, materials=self._mats(self.MATERIAL))
        self.assertEqual(added, [])
        self.assertEqual(out["sections"][0]["prose"], ["标准理论中停产价格是 AVC 的最低点。"])

    def test_不涉及停产价格的节不补(self):
        rev = {"sections": [{"heading": "研究问题", "prose": ["研究问题决定方法论。"]}]}
        _out, added, _w = ng.ensure_caliber(rev, materials=self._mats(self.MATERIAL))
        self.assertEqual(added, [])

    def test_写死的口径句已经不在了(self):
        """⚠️ 判据钉着"不许再写回去"：那句课程专属的话不许出现在模块里。"""
        src = (pathlib.Path(__file__).resolve().parent.parent / "note_guards.py").read_text("utf-8")
        self.assertNotIn("CALIBER_LINE =", src, "写死的那句常量不许回来")
        self.assertNotIn("本课按 AC 最低点定义停产价格", src)
        self.assertIn("_shutdown_source", src, "找出处那条路要在")
        self.assertIn("materials=self._materials", (pathlib.Path(__file__).resolve().parent.parent
                                                   / "obsidian_writer.py").read_text("utf-8"),
                      "写端没把课件传进去 → 对照永远补不上")

    def test_多处提到也只在讲它的那一节补(self):
        """⭐ 作者 2026-10-18 点名：口径段曾被复制进好几个小节。

        13:16 的定义节 + 13:23 的二阶导数节（它的标题带着章名前缀"最优化练习与停业价格"）
        都提到它 —— 只许补进**第一个**（讲它的那节），别处一个字不加。
        """
        rev = {"sections": [
            {"heading": "[13:16] 平均成本与停业价格", "prose": ["停业价格是价格临界点。"]},
            {"heading": "[13:23] 最优化练习与停业价格：用二阶导数判断极值",
             "prose": ["这一节接着练习，顺带提到停业价格。"]},
            {"heading": "[13:40] 二阶导数的符号", "prose": ["$f''<0$ 是极大。"]}]}
        out, added, warns = ng.ensure_caliber(rev, materials=self._mats(self.MATERIAL))
        self.assertEqual(len(added), 1, f"口径被复制进了 {len(added)} 节")
        self.assertIn("AVC", " ".join(out["sections"][0]["prose"]))
        self.assertNotIn("口径对照", " ".join(out["sections"][1]["prose"]), "第二节也被插了")
        self.assertNotIn("口径对照", " ".join(out["sections"][2]["prose"]))
        self.assertTrue(warns)

    def test_出处不截半句(self):
        """⭐ 作者 2026-10-18 点名的第二个 bug：`[:200]` 把出处切在"…2q + 1"半句上。

        构造：第一句 >60 字、句末标点落在 200 字之后；后面再堆一段**超窗口**的长文 ——
        旧实现会切断第一句，新实现必须整句保留、以句末标点收尾，且窗口外的内容不硬塞。
        """
        first = ("supply a positive quantity so long as the price is above the minimum of the "
                 "average cost, and this is the rule the firm follows in the short run for the "
                 "whole of the trading period, until market conditions change substantially and "
                 "the managers revisit all of their fixed contracts. ")
        filler = "then more explanation keeps going here without a sentence break " * 6
        long_blob = first + filler + "that is the lowest acceptable price for this firm."
        self.assertGreater(len(first), 205, "这条用例要长过旧的 200 字截断点")
        rev = {"sections": [{"heading": "停业价格", "prose": ["停业价格是价格临界点。"]}]}
        out, added, _w = ng.ensure_caliber(rev, materials=self._mats(long_blob))
        self.assertTrue(added)
        body = out["sections"][0]["prose"][-1]
        self.assertRegex(body, r"[.。!！?？]$", "出处没有收在句末（被截半句）")
        self.assertIn("the managers revisit all of their fixed contracts.", body,
                      "完整的第一句必须留着（旧实现在 200 字处切断）")
        self.assertNotIn("lowest acceptable price", body,
                         "窗口外的内容被硬塞进来（应截到句末为止）")


class MinValue(unittest.TestCase):
    """⭐ 作者第三轮第 4 条：官方解答里 `AC(6)=24`，三次生成的产物都没写出来。"""

    def test_缺了就按本节_AC_补一句(self):
        rev = {"sections": [{"heading": "13.39 平均成本最小化",
                             "prose": ["AC(Q)=Q^2-12Q+60。",
                                       "令 AC 的导数等于 0 得 Q=6，让平均成本最小的产量为 Q=6。"]}]}
        out, added, warns = nl.ensure_min_value(rev)
        self.assertTrue(added)
        self.assertIn("AC(6)=24", out["sections"][0]["prose"][-1])
        self.assertTrue(warns)

    def test_已经写了值就不动(self):
        rev = {"sections": [{"prose": ["AC(Q)=Q^2-12Q+60。", "最小平均成本 AC(6)=24。"]}]}
        out, added, _w = nl.ensure_min_value(rev)
        self.assertEqual(added, [])
        self.assertEqual(len(out["sections"][0]["prose"]), 2)

    def test_别的函数形状也算得对(self):
        """非多项式的 AC（`q+64/q`）也要算得出值 —— 值来自直接调用，不靠多项式。"""
        rev = {"sections": [{"heading": "x", "prose": [
            "AC(q)=q+64/q。", "在 q=8 处取最小。"]}]}
        out, added, _w = nl.ensure_min_value(rev)
        self.assertTrue(added)
        self.assertIn("AC(8)=16", out["sections"][0]["prose"][-1])

    def test_变式的答案不被节里补句(self):
        """⚠️ 第三轮验收：§8 的例题 AC 与变式的 AC 不是同一条 —— 题型单元的答案自己给结果，
        在节里再补一句"本节的最小平均成本"会把读者指错。"""
        rev = {"sections": [{"variants": [{"q": "设 AC(q)=q^2-8q+20，求最小平均成本。",
                                          "steps": ["在 q=4 处取最小。"], "a": "q=4"}]}]}
        out, added, _w = nl.ensure_min_value(rev)
        self.assertEqual(added, [])
        self.assertNotIn("prose", out["sections"][0])

    def test_捕获的表达式带_dollar_要剥掉(self):
        """⚠️ 产物 E 里出现过 `$AC(q)=q^2-12q+60$$`（模型按 `$…$` 写定义，结尾 `$` 被捕进表达式）。"""
        rev = {"sections": [{"prose": ["AC(q)=q^2-12q+60$。",
                                       "使平均成本最小的产量为 q=6。"]}]}
        out, added, _w = nl.ensure_min_value(rev)
        self.assertTrue(added)
        line = out["sections"][0]["prose"][-1]
        self.assertNotIn("$$", line, line)
        self.assertIn("$AC(q)=q^2-12q+60$：", line, line)

    def test_补的句子点名函数(self):
        rev = {"sections": [{"prose": ["AC(q)=q^2-12q+60。",
                                       "令 AC 的导数等于 0 得 q=6，使平均成本最小的产量为 q=6。"]}]}
        out, added, _w = nl.ensure_min_value(rev)
        self.assertTrue(added)
        self.assertIn("$AC(q)=q^{2}-12q+60$" if False else "AC(q)=q^2-12q+60",
                      out["sections"][0]["prose"][-1].replace(" ", ""))


class Wiring(unittest.TestCase):
    def test_三步都在复习层里接线了(self):
        src = pathlib.Path(__file__).resolve().parent.parent.joinpath("obsidian_writer.py").read_text()
        self.assertIn("note_ledger.reconcile", src)
        self.assertIn("note_guards.scrub", src)
        self.assertIn("note_guards.quote_check", src)
        self.assertIn("note_ledger.block", src, "账本没喂进 prompt")
        self.assertIn("note_guards.ensure_caliber", src, "口径对照没接线")
        self.assertIn("note_ledger.ensure_min_value", src, "最小平均成本补值没接线")
        # 2026-10-18 Δ 的四步都要在链上（尤其 annotate 必须在**最后**：标注不许进串讲输入）
        for key in ("note_guards.dedupe_sentences", "note_guards.strip_clock_times",
                    "note_guards.drop_trivial_cues", "note_guards.annotate_mismatches"):
            self.assertIn(key, src, f"{key} 没接线")
        self.assertLess(src.index("引用逐字核对"), src.index("核算结果落正文"),
                        "annotate 必须在清理链的最末（标注不许进串讲/scrub 的输入）")

    def test_prompt_里有两条纪律(self):
        import note_v2
        for key in ("⚠ 课上只给了片段", "不许第一人称", "引号"):
            self.assertIn(key, note_v2.REVIEW_SYS, f"prompt 缺纪律：{key}")
        # 2026-10-18 Δ：三条新规矩（过渡段不单独成段 / 正文禁时间戳 / 线索问题禁纯算术）
        # ⚠️ 2026-10-18 第七轮：「过渡段」那条的措辞从「不许单独写一段讲解」收紧成
        #    「不许单独成段」（同一判据，措辞跟着 prompt 的现行版本走）。
        for key in ("不许单独成段", "不许写钟点时间戳", "不许问纯算术"):
            self.assertIn(key, note_v2.REVIEW_SYS, f"prompt 缺新规矩：{key}")


class AnnotateMismatch(unittest.TestCase):
    """2026-10-18 Δ：三道核的 mismatch 必须**落到正文原位**（作者看截图抓的）——
    原来只进文末折叠清单，正文照样把错算术当事实印；清单里还写着"已修正"。"""

    REAL = ("首先，我们计算了某个数值：5.3除以3，得到约2.89。这个数值是平均成本在特定产量下的值。"
            "具体来说，平均成本函数为AC(q)= (60q+500)/q？但课上只给了片段："
            "原题见📖 2025-12期末卷 Exercise 7 / 未核对。")
    MECH = {"claim": REAL, "status": "mismatch", "recomputed": "5.3/3 ≈ 1.767",
            "how": "5.3÷3=1.767，不是2.89"}

    def test_错除法那一段在句子后面原位标出(self):
        out, _, warns = ng.annotate_mismatches({"sections": [{"prose": [self.REAL]}],
                                                "checks": [dict(self.MECH)]})
        got = out["sections"][0]["prose"][0]
        self.assertIn("得到约2.89。（⚠ 核算对不上：5.3÷3=1.767，不是2.89）这个数值", got)
        self.assertTrue(out["checks"][0].get("annotated"))
        self.assertEqual(1, got.count("核算对不上"))
        self.assertTrue(warns and "1 处" in warns[0], warns)

    def test_机械核与模型核同一句只标一次(self):
        """同一条断言可能同时有机械核行与模型核行（两行 how 不同）—— 页面上只许一个 ⚠。"""
        rows = [dict(self.MECH),
                {"claim": self.REAL, "status": "mismatch", "recomputed": "5.3/3≈1.767",
                 "how": "5.3除以3约1.77，不是2.89"}]
        out, _, _ = ng.annotate_mismatches({"sections": [{"prose": [self.REAL]}], "checks": rows})
        got = out["sections"][0]["prose"][0]
        self.assertEqual(1, got.count("核算对不上"))
        self.assertTrue(all(r.get("annotated") for r in out["checks"]))

    def test_机械核与标注是一条链(self):
        """从真句子出发：机械核出 `mismatch` → 标注落进正文（端到端，不起模型）。"""
        rev = {"sections": [{"prose": [self.REAL]}]}
        rows = [r for r in nm.check_review(rev) if "5.3" in r["claim"]]
        self.assertEqual(["mismatch"], [r["status"] for r in rows])
        out, _, _ = ng.annotate_mismatches({**rev, "checks": rows})
        self.assertIn("核算对不上：5.3÷3=1.767，不是2.89", out["sections"][0]["prose"][0])

    def test_没问题的行一个字不动(self):
        rev = {"sections": [{"prose": [self.REAL]}],
               "checks": [{"claim": self.REAL, "status": "ok", "how": "一致"},
                          {"claim": self.REAL, "status": "unchecked", "how": "核不了"}]}
        out, _, warns = ng.annotate_mismatches(rev)
        self.assertEqual(self.REAL, out["sections"][0]["prose"][0])
        self.assertEqual([], warns)
        self.assertNotIn("annotated", out["checks"][0])

    def test_找不到承载字段也不许炸(self):
        out, _, warns = ng.annotate_mismatches(
            {"sections": [{"prose": ["完全不相干的一段。"]}],
             "checks": [dict(self.MECH)]})
        self.assertEqual("完全不相干的一段。", out["sections"][0]["prose"][0])
        self.assertEqual([], warns)

    def test_真实产线顺序_先去重再标注也找得到(self):
        """⚠️ 2026-10-18 实测踩坑：`checks.claim` 是 `verify_numbers` 截过的 **160 字**，
        而归一化（`_norm` 剥掉全部 CJK）会把前缀缩水 —— 先跑去重（删掉第二遍
        "⚠ 课上只给了片段…"）再标注时，按"归一化后 60 字"去比就找不回来了。
        现在前缀长度取在**原串**上（90/64/40/24 渐进），去重后照样命中。"""
        dup = ("首先，我们计算了某个数值：5.3除以3，得到约2.89。这个数值是平均成本在特定产量下的值。"
               "具体来说，平均成本函数为AC(q)= (60q+500)/q？但课上只给了片段："
               "原题见📖 2025-12期末卷 Exercise 7 / 未核对。⚠ 课上只给了片段："
               "原题见📖 2025-12期末卷 Exercise 7 / 未核对。")
        rev = {"sections": [{"prose": [dup]}],
               "checks": [{"claim": dup[:160], "status": "mismatch",
                           "recomputed": "5.3/3 ≈ 1.767", "how": "5.3除以3约1.77，不是2.89"}]}
        ded, _, _ = ng.dedupe_sentences(rev)
        out, _, warns = ng.annotate_mismatches(ded)
        got = out["sections"][0]["prose"][0]
        self.assertIn("核算对不上：5.3除以3约1.77，不是2.89", got)
        self.assertTrue(out["checks"][0].get("annotated"))
        self.assertTrue(warns and "1 处" in warns[0], warns)

    def test_how_里的全角括号不切坏标注(self):
        """剥 marker 的正则靠"第一个 `）` 收口"——`how` 里的括号换成半角才切得干净。"""
        row = {"claim": self.REAL, "status": "mismatch", "recomputed": "x",
               "how": "数值二阶导（正）与结论矛盾"}
        out, _, _ = ng.annotate_mismatches({"sections": [{"prose": [self.REAL]}],
                                            "checks": [row]})
        got = out["sections"][0]["prose"][0]
        self.assertIn("核算对不上：数值二阶导(正)与结论矛盾）", got)
        out2, _, _ = ng.annotate_mismatches(out)         # 幂等：再来一遍不插第二个
        self.assertEqual(1, out2["sections"][0]["prose"][0].count("核算对不上"))


class DedupeSentences(unittest.TestCase):
    """2026-10-18 Δ②：模型抄格式抄两遍（真笔记里「⚠ 课上只给了片段…」一段出现两次）。"""

    def test_课上只给了片段抄两遍只留一遍(self):
        s = ("具体来说，平均成本函数为AC(q)= (60q+500)/q？但课上只给了片段："
             "原题见📖 2025-12期末卷 Exercise 7 / 未核对。⚠ 课上只给了片段："
             "原题见📖 2025-12期末卷 Exercise 7 / 未核对。")
        out, _, warns = ng.dedupe_sentences({"sections": [{"prose": [s]}]})
        got = out["sections"][0]["prose"][0]
        self.assertEqual(1, got.count("课上只给了片段"), got)
        self.assertIn("？但课上只给了片段：", got)
        self.assertTrue(warns and "1 处" in warns[0], warns)

    def test_短句重复是行文_不动(self):
        s = "对。对。再算一遍。对。"
        out, _, warns = ng.dedupe_sentences({"sections": [{"prose": [s]}]})
        self.assertEqual(s, out["sections"][0]["prose"][0])
        self.assertEqual([], warns)


class StripClock(unittest.TestCase):
    """2026-10-18 Δ⑥（leader 拍板）：正文不许写钟点时间戳 —— 时间在节标题上。"""

    def test_教授在几点几分这种写法的戳删掉(self):
        s = "纯粹的章节过渡：教授在 12:50:41 以笑声结束当前内容，没有讲授任何新概念。"
        out, _, warns = ng.strip_clock_times({"sections": [{"prose": [s]}]})
        got = out["sections"][0]["prose"][0]
        self.assertNotIn("12:50:41", got)
        self.assertIn("教授以笑声结束当前内容", got)
        self.assertTrue(warns and "1 处" in warns[0], warns)

    def test_戳挡在的字前连的一起收(self):
        """回放自查抓到：`；12:50:28 的「…」是…` 只删戳会剩个残句（"； 的…"）。"""
        s = "这是本段唯一有实际安排信息的一句话；12:50:28 的「a one day pleasure」是课堂收尾的插话。"
        out, _, _ = ng.strip_clock_times({"sections": [{"prose": [s]}]})
        got = out["sections"][0]["prose"][0]
        self.assertNotIn("12:50:28", got)
        self.assertIn("；「a one day pleasure」是课堂收尾的插话。", got)

    def test_节标题_出处_台账的时间戳一个字不动(self):
        """⚠️ 回放自查（2026-10-18）：初版把节标题的 `[13:16]` 前缀和账本的
        `corrections.where`（原话发生时间）也删了 —— 时间在标题上是**该有的**。"""
        rev = {"sections": [{"heading": "[12:50] 章节过渡", "ts": "12:50:41",
                             "prose": ["正文。"],
                             "example": {"title": "📖 卷（🎙 13:29）", "prompt": "p"},
                             "sources": ["🎙 12:50:41-12:51:00"]}],
               "checks": [{"claim": "12:50:41 的断言", "status": "unchecked", "how": "h"}],
               "corrections": [{"where": "12:50:41", "spoken": "教授在 12:50:41 说"}]}
        out, _, _ = ng.strip_clock_times(rev)
        self.assertEqual("[12:50] 章节过渡", out["sections"][0]["heading"])
        self.assertEqual("12:50:41", out["sections"][0]["ts"])
        self.assertEqual("📖 卷（🎙 13:29）", out["sections"][0]["example"]["title"])
        self.assertEqual("🎙 12:50:41-12:51:00", out["sections"][0]["sources"][0])
        self.assertEqual("12:50:41 的断言", out["checks"][0]["claim"])
        self.assertEqual("12:50:41", out["corrections"][0]["where"])
        self.assertEqual("教授在 12:50:41 说", out["corrections"][0]["spoken"])


class TrivialCues(unittest.TestCase):
    """2026-10-18 Δ③：纯算术线索问题（"5.3除以3等于多少？"）—— 计算器能答，丢。"""

    def test_纯算术丢掉_真问题留着(self):
        rev = {"sections": [{"cues": ["5.3除以3等于多少？", "停产价格如何定义？", "2+2是多少？"]}]}
        out, _, warns = ng.drop_trivial_cues(rev)
        self.assertEqual(["停产价格如何定义？"], out["sections"][0]["cues"])
        self.assertTrue(warns and "2 条" in warns[0], warns)

    def test_带文字的问题都留着(self):
        keep = ["MC 和 AC 谁穿过谁？", "AC 的最小值等于多少？", "为什么要除以 q？",
                "5.3 除以 3 在题里起了什么作用？"]
        out, _, warns = ng.drop_trivial_cues({"sections": [{"cues": list(keep)}]})
        self.assertEqual(keep, out["sections"][0]["cues"])
        self.assertEqual([], warns)


if __name__ == "__main__":
    unittest.main()
