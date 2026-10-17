"""数学两层硬核的判据：`note_math.auto_tex`（公式必进 LaTeX）+ `check_review`（数值复算）。

⭐ 每个用例都对着 2026-10-17 验收抓到的**真错**：
   笔记 §1 写着「TC(q)=60q+500（假设）」又说「使 AC 最小的产量 ≈ 2.89」——
   而 `AC=60+500/q` 单调递减、根本没有最小值。三道核当时只有"模型自己重算"
   与"课件对照"两层，两层都共用错的前提，所以谁都没拦住。

⚠️ 变异验证（每条判据都要能"把实现改坏就看到它红"）：
   · 把 `auto_tex` 的 `_convert_plain` 换成 `return seg` → `TestAutoTex` 全红；
   · 把 `check_review` 里的 `abs(d1) > tol` 改成 `False` → 反例用例红；
   · 把 `functions` 的 `**` 解析去掉 → 正确样例也红（会变成"没有函数"）。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import note_math as nm          # noqa: E402


class TestAutoTex(unittest.TestCase):
    def test_多项式被包成公式(self):
        got = nm.auto_tex("可得 TC(q)=q^3-3q^2+12q，再求导")
        self.assertIn("$TC(q)=q^{3}-3q^{2}+12q$", got)
        self.assertNotIn("q^3", got, "还有没转的裸 ^ 式子")

    def test_下标与隐含系数(self):
        self.assertIn("$AC=q^{2}-12q+60$", nm.auto_tex("平均成本 AC=q^2-12q+60"))
        self.assertIn("$q_{1} + 3q_{2}$", nm.auto_tex("约束是 q_1 + 3q_2"))

    def test_unicode_上标(self):
        self.assertIn("$AC=q^{2}-12q+60$", nm.auto_tex("AC=q²−12q+60"))

    def test_幂等_已写好的公式不动(self):
        s = "已经是 $q^{3}$ 与 $$AC'(q)=2q-12$$ 的公式"
        self.assertEqual(nm.auto_tex(s), s)
        self.assertEqual(nm.auto_tex(nm.auto_tex(s)), nm.auto_tex(s))

    def test_撇号与负号都在公式里(self):
        """⚠️ 验收实测：`f'(x)=3x^2-3` 曾包成 `f'($x)=3x^{2}-3$`（公式从括号中间开始）；`-500/q^2` 曾把负号留在外面。"""
        self.assertEqual(nm.auto_tex("f'(x)=3x^2-3"), "$f'(x)=3x^{2}-3$")
        self.assertEqual(nm.auto_tex("AC'(q) = -500/q^2"), "$AC'(q) = -500/q^{2}$")

    def test_不误伤普通文本(self):
        for s in ("file_name 不变", "e_mail 也不是公式", "价格是 2.89 欧元",
                  "AC 与 MC 的关系（没有 ^ 也不带下标）"):
            self.assertEqual(nm.auto_tex(s), s, f"误伤了：{s}")

    def test_没有数学记号的式子不动(self):
        """`2q-12=0` 没有 `^`/上标/下标 —— **不转**。

        ⚠️ 这是有意的保守：`2.89 欧元`、`AC 与 MC` 这种文本与它同形，
           包错 `$…$` 会让读者看到一堆乱码公式。判据只认"有数学记号"的片段
           （作者的原话也是"对残留的 **^** 式子兜底转换"）。
        """
        self.assertEqual(nm.auto_tex("令 2q-12=0 得 q=6"), "令 2q-12=0 得 q=6")

    def test_html_那一层也兜底(self):
        import note_html
        html = note_html.inline("于是 q^3-3q^2+12q 的导数是 3q^2-3")
        self.assertIn('class="tex"', html, "HTML 里没有公式元素（KaTeX 没有作用对象）")
        # ⚠️ HTML 这一层 `$…$` 被消费成 `.tex` 元素（`data-tex` 里是 TeX 源码）——
        #    所以判据看 `data-tex`，不是字面的 `$`。
        self.assertIn('data-tex="3q^{2}-3"', html)
        self.assertIn('data-tex="q^{3}-3q^{2}+12q"', html)


class TestFunctions(unittest.TestCase):
    def test_直接定义与互相引用(self):
        defs = nm.functions(["AC(q)=TC(q)/q", "TC(q)=60q+500"])
        self.assertIn("TC", defs)
        self.assertIn("AC", defs, "AC 引用了后面的 TC —— 一轮解析会把它丢掉")
        self.assertAlmostEqual(defs["AC"][2](2.89), (60 * 2.89 + 500) / 2.89, places=6)

    def test_laTeX_与_unicode_写法(self):
        defs = nm.functions(["AC(q) = q^{2} - 12q + 60", "TC(q) = q³ − 12q² + 60q"])
        self.assertAlmostEqual(defs["AC"][2](6), 24.0, places=9)
        self.assertAlmostEqual(defs["TC"][2](2), 2 ** 3 - 12 * 4 + 120, places=9)

    def test_认不出的表达式不硬算(self):
        self.assertEqual(nm.functions(["AC(q)=奖金/q"]), {})


class TestCheckReview(unittest.TestCase):
    """反例 = 验收里那一条（自编函数 + 凑出来的驻点）。"""

    def _bad(self):
        return {"sections": [{
            "heading": "停产价格与最低平均成本",
            "prose": ["平均成本 AC(q)=60+500/q。", "使 AC 最小的产量约为 2.89。"],
            "example": {"title": "🎙 课上例题",
                        "prompt": "已知总成本函数TC(q)=60q+500（假设），求停产价格。",
                        "steps": [{"text": "AC(q)=TC(q)/q"},
                                  {"text": "AC(2.89)=(60*2.89+500)/2.89=233.01"}],
                        "answer": "停产价格约为 233.01"}}]}

    def test_反例必须报_mismatch(self):
        rows = nm.check_review(self._bad())
        bad = [r for r in rows if r["status"] == "mismatch"]
        self.assertTrue(bad, f"三道核没抓住这个反例：{rows}")
        self.assertTrue(any("不是驻点" in r["how"] for r in bad), bad)
        self.assertTrue(any(r["recomputed"].startswith("AC'(2.89)") for r in bad), bad)

    def test_反例里的自编例题被点名(self):
        rows = nm.check_review(self._bad())
        flagged = [r for r in rows if r["status"] == "unchecked" and "硬禁区" in r["how"]]
        self.assertTrue(flagged, f"「（假设）」没被点名：{rows}")

    def test_正确样例报_ok(self):
        good = {"sections": [{
            "heading": "最低平均成本",
            "prose": ["平均成本 AC(q)=q^2-12q+60。",
                      "令 AC 的导数等于 0，得在 q=6 处取最小，AC(6)=24。"],
            "example": {"title": "📖 Exercise 5",
                        "prompt": "TC(q)=q^3-12q^2+60q，求使平均成本最小的产量。",
                        "steps": [{"text": "AC(q)=TC(q)/q=q^2-12q+60"},
                                  {"text": "AC(6)=24"}],
                        "answer": "q=6，AC=24"}}]}
        rows = nm.check_review(good)
        self.assertTrue(rows)
        self.assertEqual([r["status"] for r in rows], ["ok"] * len(rows), rows)
        self.assertTrue(any("二阶" in r["how"] for r in rows),
                        "极值结论必须走二阶条件那条判据")

    def test_二阶条件反着写也报_mismatch(self):
        """在极大值点写"取最小" —— 一阶导为 0，只有二阶条件能抓住。"""
        rev = {"sections": [{"prose": ["收益 TR(q)=-q^2+10q。", "在 q=5 处取最小。"],
                             "example": {"title": "📖 某试卷",
                                         "prompt": "TR(q)=-q^2+10q 的最小值点？"}}]}
        rows = nm.check_review(rev)
        self.assertTrue(any(r["status"] == "mismatch" and "二阶条件" in r["how"]
                            for r in rows), rows)

    def test_没有函数就标_unchecked_不许猜(self):
        rev = {"sections": [{"prose": ["使平均成本最小的产量约为 2.89。"]}]}
        rows = nm.check_review(rev)
        self.assertTrue(rows)
        self.assertTrue(all(r["status"] == "unchecked" for r in rows), rows)

    def test_代入值算错报_mismatch(self):
        rev = {"sections": [{"prose": ["平均成本 AC(q)=q^2-12q+60，所以 AC(4)=27。"]}]}
        rows = nm.check_review(rev)
        self.assertTrue(any(r["status"] == "mismatch" and "代入复算" in r["how"]
                            for r in rows), rows)

    def test_课件出处的例题不点名(self):
        rev = {"sections": [{"example": {"title": "📖 Exercise 5",
                                         "prompt": "Assume that TC(q)=q^3-12q^2+60q。"}}]}
        self.assertEqual(nm.check_review(rev), [])


class TestScoping(unittest.TestCase):
    """函数命名空间按**单元**分（讲解/例题/每道变式各一份）—— 判据实测抓到的假 mismatch。"""

    def test_变式的函数不串到例题里(self):
        """例题与变式各自定义了 `AC`：变式里的结论必须用**变式**的 AC 核。

        ⚠️ 这道判据是**变异验证挑出来的**：写成"断言里不含 `AC'(2)`"时，把实现
           退回"整节共用一个命名空间"它照样绿（那种场景下两种实现结果相同）。
           现在钉的是**方向相反**的结论：变式的 `q=4` 断言只有在用变式的 AC
           （min 在 q=2）时才会被判错 —— 串门到例题的 AC（min 在 q=4）就变 ok。
        """
        rev = {"sections": [{
            "heading": "平均成本最小化",
            "prose": ["停产价格是平均成本的最低点。"],
            "example": {"title": "📖 Exercise 7", "prompt": "TC(q)=2q^2+q+32，求最低可接受价格。",
                        "steps": [{"text": "AC(q)=TC(q)/q=2q+1+32/q"}],
                        "answer": "q=4，最低价 17。"},
            "variants": [{"q": "若 AC(q)=q^2-4q+10，求最小平均成本的产量。",
                          "steps": ["AC'(q)=2q-4=0", "在 q=2 处取最小，AC(2)=6。"],
                          "a": "q=4 处取最小（错的结论，用来钉方向）。"}]}]}
        rows = nm.check_review(rev)
        hit = [r for r in rows if "q=4 处取最小" in r["claim"]]
        self.assertTrue(hit, f"变式里那条断言没进核对：{rows}")
        self.assertEqual(hit[0]["status"], "mismatch",
                         f"变式用了别人的函数：{hit[0]}")
        self.assertIn("AC'(4)", hit[0]["recomputed"])

    def test_点名了别的函数就不许硬算(self):
        """单元里只定义了 TC，句子却写 `AC(6)=24` → 不许拿 TC 来算成 144。"""
        rev = {"sections": [{"example": {"title": "📖 某卷",
                                         "prompt": "TC(q)=q^3-12q^2+60q。",
                                         "steps": [{"text": "AC(6)=24"}]}}]}
        rows = nm.check_review(rev)
        self.assertFalse([r for r in rows if r["status"] == "mismatch"],
                         f"拿 TC 去核 AC 的断言了：{rows}")

    def test_连等式里的函数能解析(self):
        rev = {"sections": [{"example": {"title": "📖 某卷", "prompt": "TC(q)=2q^2+q+32。",
                                         "steps": [{"text": "AC(q)=TC(q)/q=2q+1+32/q"},
                                                   {"text": "AC(4)=17"}],
                                         "answer": "最低价 17。"}}]}
        rows = nm.check_review(rev)
        self.assertTrue([r for r in rows if r["status"] == "ok" and "AC(4)" in r["how"]],
                        f"连等式里的 AC 没解析出来：{rows}")

    def test_极大极小也认(self):
        """`x=1 极大值` 是极值断言（中文两种说法都要认）。"""
        rev = {"sections": [{"example": {"title": "📖 某卷",
                                         "prompt": "f(x)=x^3-6x^2+9x。",
                                         "steps": [{"text": "f'(x)=3x^2-12x+9=0 得 x=1 与 x=3。"},
                                                   {"text": "x=1 极大值，x=3 极小值。"}],
                                         "answer": "x=1 极大，x=3 极小。"}}]}
        rows = nm.check_review(rev)
        self.assertTrue([r for r in rows if r["status"] == "ok" and "极大" in r["how"]],
                        f"极大/极小没进判据：{rows}")
        self.assertFalse([r for r in rows if r["status"] == "mismatch"],
                         f"正确的极大极小被判成错：{rows}")


class TestNoFalsePositives(unittest.TestCase):
    """⚠️ 假"已修正"比漏核更坏（读者拿它对答案）—— 这里钉两句**实测被抓错的原文**。"""

    def test_箭头句不挑数字(self):
        """`最小值在q→0+处为无穷大？实际上AC(q)=80+2000/q…` —— 曾经挑到 80 报假修正。"""
        text = ("AC'(q) = -2000/q^2，无解，AC单调递减，最小值在q→0+处为无穷大？"
                "实际上AC(q)=80+2000/q，当q→∞时AC→80，所以没有最小值。")
        rev = {"sections": [{"prose": [text, "平均成本 AC(q)=80+2000/q。"]}]}
        rows = nm.check_review(rev)
        self.assertFalse([r for r in rows if r["status"] == "mismatch"], rows)
        self.assertTrue(any(r["status"] == "unchecked" and "太密" in r["how"] for r in rows),
                        rows)

    def test_导数枚举句整句跳过(self):
        """`…f''(-1)=-18<0（极大），f''(2)=18>0（极小）` —— 曾经挑到 18 报假修正。"""
        text = ("f'(x)=6x^2-6x-12=6(x^2-x-2)=6(x-2)(x+1)，驻点x=2和x=-1；"
                "f''(x)=12x-6；f''(-1)=-18<0（极大），f''(2)=18>0（极小）。")
        rev = {"sections": [{"prose": [text, "f(x)=x^3-3x^2-12x+1。"],
                             "example": {"title": "📖 某卷", "prompt": "f(x)=x^3-3x^2-12x+1。"}}]}
        rows = nm.check_review(rev)
        self.assertFalse([r for r in rows if r["status"] == "mismatch"], rows)

    def test_一句两个极值断言各自核(self):
        """`x=1 极大值，x=3 极小值。` —— 两个断言都要核（正确则全 ok，挡住模型层的乱改）。"""
        rev = {"sections": [{"example": {"title": "📖 某卷",
                                         "prompt": "f(x)=x^3-6x^2+9x+1。",
                                         "steps": [{"text": "x=1 极大值，x=3 极小值。"}],
                                         "answer": "x=1 极大，x=3 极小。"}}]}
        rows = nm.check_review(rev)
        self.assertTrue(rows)
        self.assertEqual([r["status"] for r in rows], ["ok"] * len(rows), rows)
        self.assertEqual(sorted({r["recomputed"] for r in rows}), ["f(1)=5", "f(3)=1"],
                         "两条断言只有一条被核了")


class TestPickFunction(unittest.TestCase):
    """⚠️ 两次生成的产物上都出现了同一个假修正：把"平均成本最小"的断言拿去核 MC'。"""

    def test_中文主语优先于句子里顺带提到的函数(self):
        rev = {"sections": [{"example": {"title": "📖 Exercise 5",
                                         "prompt": "TC(Q)=Q^3-12Q^2+60Q",
                                         "steps": [{"text": "AC(Q)=TC(Q)/Q=Q^2-12Q+60"},
                                                   {"text": "MC(Q)=3Q^2-24Q+60"},
                                                   # ⚠️ 这句话里**只有** ASCII 的 `MC`，
                                                   #    主语是中文的「平均成本」—— 映射
                                                   #    一去掉，判据就会拿 MC' 去核它。
                                                   {"text": "由 MC 与平均成本相等解出 Q=6；"
                                                            "在 Q=6 处平均成本最小。"}],
                                         "answer": "Q=6"}}]}
        rows = nm.check_review(rev)
        bad = [r for r in rows if r["status"] == "mismatch"]
        self.assertFalse(bad, f"又拿 MC 去核 AC 的断言了：{bad}")
        hit = [r for r in rows if "平均成本最小" in r["claim"]]
        self.assertTrue(hit, rows)
        self.assertIn("AC'(", hit[0]["how"], hit[0])

    def test_平均成本真的写错时照样报(self):
        rev = {"sections": [{"example": {"title": "📖 Exercise 5", "prompt": "TC(Q)=Q^3-12Q^2+60Q",
                                         "steps": [{"text": "AC(Q)=Q^2-12Q+60"},
                                                   {"text": "使平均成本最小的产量为 Q=4。"}],
                                         "answer": "Q=4"}}]}
        rows = nm.check_review(rev)
        self.assertTrue([r for r in rows if r["status"] == "mismatch"], rows)


class TestMerge(unittest.TestCase):
    def test_机械核优先(self):
        det = [{"claim": "AC(2.89)=233.01", "status": "mismatch", "how": "机械", "recomputed": "x"}]
        llm = [{"claim": "AC(2.89) = 233.01", "status": "ok", "how": "模型说没问题"},
               {"claim": "MC(4)=12", "status": "ok", "how": "模型"}]
        got = nm.merge_checks(det, llm)
        self.assertEqual(got[0]["status"], "mismatch", "机械核被模型核覆盖了")
        self.assertNotIn("模型说没问题", [r["how"] for r in got])
        self.assertIn("MC(4)=12", [r["claim"] for r in got])

    def test_两边都空(self):
        self.assertEqual(nm.merge_checks([], []), [])


if __name__ == "__main__":
    unittest.main()
