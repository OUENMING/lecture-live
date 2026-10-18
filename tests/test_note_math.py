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
import json
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
        """⚠️ 措辞第四轮改过（转录没对上数字就是"未核对"，不再一律喊"硬禁区"）——
        判据钉的是**点名这件事**，不是那句话。"""
        rows = nm.check_review(self._bad())
        flagged = [r for r in rows if r["status"] == "unchecked"
                   and r["claim"].startswith("例题：") and "课上只给了片段" in r["how"]]
        self.assertTrue(flagged, f"「（假设）」没被点名：{rows}")

    def test_转录能对上数字的课上例子就放行(self):
        """⭐ 第四轮：🎙 例子里的数字在转录里出现过 → 这是**课上真给过**的题面，记 ✅。"""
        rev = {"sections": [{"heading": "2. 三次函数", "example": {
            "title": "🎙 13:18 最优化练习",
            "prompt": "求函数 $f(x)=x^{3}-12x$ 的驻点，并判断其性质。",
            "steps": [{"text": "f'(x)=3x^{2}-12"}], "answer": "驻点 x=2 和 x=-2"}}]}
        rows = nm.check_review(rev, transcript="So the derivative is 3x squared minus 12, "
                                              "and we get x equals 2 and minus 2.")
        src = [r for r in rows if r["claim"].startswith("例题：")]
        self.assertTrue(src, rows)
        self.assertEqual(src[0]["status"], "ok", src)

    def test_转录对不上数字就保持未核对(self):
        rev = {"sections": [{"heading": "2. 三次函数", "example": {
            "title": "🎙 13:18 某题",
            "prompt": "求函数 $f(x)=x^{3}-45x$ 的驻点。", "answer": "x=±3.87"}}]}
        rows = nm.check_review(rev, transcript="That is all for today, see you next week.")
        src = [r for r in rows if r["claim"].startswith("例题：")]
        self.assertTrue(src, rows)
        self.assertEqual(src[0]["status"], "unchecked", src)

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


class TestSecondDerivative(unittest.TestCase):
    """⭐ 作者第三轮第 5 条：未核对行里一半是「f''(-1)=-6<0，故为极大值」这种 —— 机械核完全能做。

    ⚠️ 三个必须同时成立，缺一个就会变成假修正：
       ⓐ 句子自己写了 `f''(x)=12x-6` 时用它（同一单元里可能有另一个同名的 `f`）；
       ⓑ 结论词只认**贴着**这条断言的那个（一句话里两条断言，各有各的「极大/极小」）；
       ⓒ `AC''(q)=450/q³` 是**函数**不是某点的值 → 这一档不核（不许猜）。
    """

    def test_点的二阶导与方向都对就_ok(self):
        rev = {"sections": [{"example": {"title": "📖 某卷", "prompt": "f(x)=x^3-3x",
                                         "steps": [{"text": "在 x=-1 处，f''(-1)=-6<0，函数凹，故为极大值点。"},
                                                   {"text": "f''(1)=6>0，故为极小值点。"}],
                                         "answer": "x=1 极小"}}]}
        rows = [r for r in nm.check_review(rev) if "二阶导" in r["how"] or "f''" in r["recomputed"]]
        self.assertTrue(rows, "二阶导断言一条都没核")
        self.assertEqual([r["status"] for r in rows], ["ok"] * len(rows), rows)

    def test_结论方向反了要报(self):
        rev = {"sections": [{"example": {"title": "📖 某卷", "prompt": "f(x)=x^3-3x",
                                         "steps": [{"text": "f''(1)=6>0，故为极大值点。"}],
                                         "answer": "x=1"}}]}
        rows = nm.check_review(rev)
        self.assertTrue([r for r in rows if r["status"] == "mismatch"], rows)

    def test_正文的除法也要核(self):
        """⭐ 第六轮（产物 I 的 §1）：正文写着「这里用到了5.3除以3得到2.89的计算」——
        三道核**一句没拦**（原来没有除法这一档），而 living 摘要里 2.89 的账本
        就指望它。判据：算错 → mismatch（进修正记录）、算对 → ok（不许误报）。"""
        rev = {"sections": [{"prose": ["这里用到了5.3除以3得到2.89的计算。"]}]}
        rows = [r for r in nm.check_review(rev) if "5.3" in r["claim"]]
        self.assertEqual([r["status"] for r in rows], ["mismatch"], rows)
        self.assertIn("1.767", rows[0]["recomputed"])
        self.assertIn("不是2.89", rows[0]["how"])
        ok = nm.check_review({"sections": [{"prose": ["60 除以 5 等于 12。",
                                                      "10 除以 3 约等于 3.3。"]}]})
        self.assertEqual([r["status"] for r in ok], ["ok", "ok"], ok)

    def test_真句子的逗号和约也要拦(self):
        """⭐ 2026-10-18 Δ（作者看截图抓的）：这道档的**起因句**本身没被拦住 ——
        真笔记里长的是「5.3除以3**，得到约**2.89」，比测试里那版紧的多了
        **一个逗号 + 一个"约"**，正则就漏了（这一档当初就是为这句话写的！）。
        判据：这句 → mismatch；紧版仍 mismatch、算对的仍 ok（宽了不许误报）。"""
        real = "首先，我们计算了某个数值：5.3除以3，得到约2.89。这个数值是平均成本在特定产量下的值。"
        rows = [r for r in nm.check_review({"sections": [{"prose": [real]}]}) if "5.3" in r["claim"]]
        self.assertEqual([r["status"] for r in rows], ["mismatch"], rows)
        self.assertIn("1.767", rows[0]["recomputed"])
        vs = nm.check_review({"sections": [{"prose": [
            "5.3除以3，得到约1.77。",                      # 算对（两位小数）
            "10 除以 4，得到约 2.5。",                     # 算对（一位小数）
        ]}]})
        self.assertEqual([r["status"] for r in vs], ["ok", "ok"], vs)

    def test_拐点那句_f_两撇为零不许判方向(self):
        """⭐ 第五轮自查（产物 H 的假 ⚠）：`f''(0)=0 且两侧凹凸性改变` 是**拐点的定义**，
        `凹凸` 是名词不是方向断言 —— 当时被判成"说凹 → 该为极大"（假 ⚠）。"""
        rev = {"sections": [{"example": {"title": "📖 卷", "prompt": "f(x)=x^3-3x",
                                         "steps": [{"text": "拐点在 $x=0$ 处，因为 $f''(0)=0$ 且两侧凹凸性改变。"}],
                                         "answer": "拐点 x=0"}}]}
        rows = [r for r in nm.check_review(rev) if "凹凸" in r["claim"]]
        self.assertTrue(rows, "拐点那句没有核对行")
        self.assertEqual([r["status"] for r in rows], ["ok"], rows)
        self.assertIn("不判极大极小", rows[0]["how"], rows)

    def test_凹凸性在旁边也不影响真方向词(self):
        """⚠️ `凹凸` 是名词：同句里有 `凹凸性在 x=0 变号` 时，方向词要认**后面那个**「极小」，
        不许把「凹凸」的凹当成"说凹 → 该为极大"。"""
        rev = {"sections": [{"prose": ["f(x)=x^3-3x。",
                                       "在 x=1 处，f''(1)=6>0，凹凸性在 x=0 变号，故该点为极小值点。"]}]}
        rows = [r for r in nm.check_review(rev) if "凹凸性" in r["claim"]]
        self.assertTrue(rows, rows)
        self.assertEqual([r["status"] for r in rows], ["ok"], rows)

    def test_函数形的二阶导不许当点值核(self):
        """`AC''(q)=450/q³`：`=450` 是系数的开头，不是某个点上的值 —— 曾把它读成 `AC''(450)`。"""
        rev = {"sections": [{"prose": ["AC(Q)=Q^2-12Q+60，AC''(q)=450/q^{3}>0，故为极小值。"]}]}
        rows = nm.check_review(rev)
        self.assertFalse([r for r in rows if "AC''" in str(r.get("recomputed") or "")], rows)


class TestScopeAcrossUnits(unittest.TestCase):
    """⚠️ 第四轮在产物 G 上抓到的两条假 ⚠：变式的题面写成 `$f(x) = x^{3} - 12x$ 的驻点…`，
    表达式把 `$ 的驻点` 一起吞了 → 编译失败 → 这一单元"没有 f" → 回落去吃**另一单元**的
    `f(x)=x³−3x`，于是把对的 `驻点 x=2（极小值）` 报成"f'(2)≈9≠0"。"""

    def _rev(self):
        return {"sections": [{"heading": "[13:18] 三次函数",
                              "example": {"title": "📖 卷", "prompt": "给定 $f(x) = x^{3} - 3x$，求驻点。",
                                          "steps": [{"text": "令 f'(x)=0 得 x=1 和 x=-1。"}],
                                          "answer": "驻点 x=1（极小值）和 x=-1（极大值）。"},
                              "variants": [{"q": "求函数 $f(x) = x^{3} - 12x$ 的驻点，并判断其性质。",
                                            "steps": [{"text": "f'(x) = 3x^{2} - 12，驻点为 x=2 和 x=-2。"}],
                                            "a": "驻点 x=2（极小值），x=-2（极大值）。"}]}]}

    def test_题面里的_dollar_与汉字不进表达式(self):
        fns = nm.functions(["求函数 $f(x) = x^{3} - 12x$ 的驻点，并判断其性质。"])
        self.assertIn("f", fns, "变式的函数定义没解出来")
        self.assertEqual(fns["f"][1].strip(), "x^{3} - 12x", fns["f"][1])
        self.assertAlmostEqual(fns["f"][2](2.0), -16.0, places=6)   # x³−12x 在 2 处
        self.assertAlmostEqual(nm._d1(fns["f"][2], 2.0), 0.0, places=3)   # 2 是驻点

    def test_变式的驻点不许拿例题的_f_去核(self):
        rows = nm.check_review(self._rev())
        bad = [r for r in rows if r["status"] == "mismatch"]
        self.assertEqual(bad, [], f"拿另一单元的 f 核了变式：{bad}")
        self.assertTrue([r for r in rows if r["status"] == "ok"], rows)


class TestDedupeAndFilter(unittest.TestCase):
    """⭐ 作者第三轮第 5 条的第二半：同一句在 `prose` 与 `answer` 各出现一次 → 两道一模一样的行；
    空 claim 也曾在产物里占了两行。"""

    def test_同一句两处出现只留一行(self):
        line = "x=1 处为极小值点。"
        rev = {"sections": [{"prose": [line], "example": {"title": "📖 某卷",
                                                          "prompt": "f(x)=x^3-6x^2+9x+1。",
                                                          "steps": [{"text": line}],
                                                          "answer": line}}]}
        rows = nm.check_review(rev)
        self.assertEqual(len(rows), len({(r["claim"], r["recomputed"], r["status"]) for r in rows}))
        self.assertEqual(len(rows), 1, rows)

    def test_空_claim_不留行(self):
        rev = {"sections": [{"prose": ["", "  "], "checks": [{"claim": "", "status": "unchecked"},
                                                             {"claim": "  ", "status": "ok"}]}]}
        self.assertEqual(nm.check_review(rev), [])


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


class TestCrossSentenceNumbers(unittest.TestCase):
    """⭐ 第三轮离线复跑抓到的**跨句吃数**假修正（同一份文字，旧机械层报 2 行 ⚠、全是假的）。

    修法：先按小句切（逗号/句号/比较符/与和都切），小句内取最后一个 `=` —— 等号右边
    常是整条算式（`AC(4)=4^2-12*4+60=28`），但**不许跨过下一条断言**。
    """

    def test_两个断言并列要各核各的(self):
        rev = {"sections": [{"prose": ["AC(q)=q^2-12q+60。", "TC(q)=q^3-12q^2+60q。",
                                      "所以 AC(4)=28，MC(4)=12。"]}]}
        rows = nm.check_review(rev)
        self.assertEqual([r["status"] for r in rows], ["ok"], rows)
        self.assertIn("AC(4)=28", rows[0]["how"])

    def test_比较号两侧各是一条(self):
        rev = {"sections": [{"prose": ["AC(q)=q^2-12q+60。", "MC(q)=3q^2-24q+60。",
                                      "在 Q=4 时，MC(4)=12 < AC(4)=28，所以增加一单位产量会拉低平均。"]}]}
        rows = nm.check_review(rev)
        self.assertEqual([r["status"] for r in rows], ["ok", "ok"], rows)
        self.assertEqual({r["recomputed"] for r in rows}, {"12", "28"}, rows)

    def test_连等式取最后一个值(self):
        rev = {"sections": [{"prose": ["AC(q)=q^2-12q+60。",
                                      "计算 AC(4)=4^2-12*4+60=16-48+60=28。"]}]}
        rows = nm.check_review(rev)
        self.assertEqual([r["status"] for r in rows], ["ok"], rows)

    def test_误区句里的故意错值不许当断言(self):
        """`计算 MC(4) 时忘记乘以系数 3，得到 16-96+60=-20` —— 是一个**错例**，不是断言。"""
        rev = {"sections": [{"prose": ["MC(q)=3q^2-24q+60。",
                                      "计算 MC(4) 时忘记乘以系数 3，得到 16-96+60=-20。"]}]}
        self.assertEqual([r["status"] for r in nm.check_review(rev)], [], nm.check_review(rev))


class TestDefinitionParsing(unittest.TestCase):
    """题面里并列两个定义 / 变式步骤是字典 —— 两个实测过的解析坑。"""

    def test_题面并列两个定义不许吞(self):
        """`给定 AC(Q)=Q^2-12Q+60 和 MC(Q)=3Q^2-24Q+60，…` 曾把第二个定义吞进 AC 里 →
        报出 `AC(4)=12` 的假修正（AC 的表达式变成了 MC 的）。"""
        rev = {"sections": [{"prose": ["给定 AC(Q)=Q^2-12Q+60 和 MC(Q)=3Q^2-24Q+60，求 Q=4 时的两个成本。",
                                      "AC(4)=28，MC(4)=12。"]}]}
        rows = nm.check_review(rev)
        self.assertEqual([r["status"] for r in rows], ["ok", "ok"], rows)
        self.assertEqual({r["recomputed"] for r in rows}, {"28", "12"}, rows)

    def test_变式步骤是字典时取_text(self):
        """`{'text': '二阶导数 -3 < 0…', 'why': ''}` 曾被 `str(dict)` 整段贴进核对表。"""
        rev = {"sections": [{"variants": [{"q": "设 TC(q)=2q^2+q+32，求使平均成本最小的产量。",
                       "steps": [{"text": "AC(q)=TC(q)/q=2q+1+32/q", "why": "定义"},
                                 {"text": "AC''(q)=64/q^3>0，故 q = 4 为最小值点。", "why": ""}],
                       "a": "q=4"}]}]}
        rows = nm.check_review(rev)
        self.assertTrue(rows)
        for r in rows:
            self.assertNotIn("{'text'", r["claim"], "字典被当成了断言文本")
        self.assertEqual([r["status"] for r in rows], ["ok"], rows)


class TestPointThenConclusion(unittest.TestCase):
    """结论在点后面：`驻点为 x=1（极小值）和 x=-1（极大值）`。"""

    def test_两个驻点各自核(self):
        rev = {"sections": [{"example": {"title": "📖 卷", "prompt": "f(x)=x^3-3x",
                                         "steps": [{"text": "求导"}],
                                         "answer": "驻点为 x=1（极小值）和 x=-1（极大值）；函数在 x>0 时凸。"}}]}
        rows = nm.check_review(rev)
        self.assertEqual([r["status"] for r in rows], ["ok", "ok"], rows)
        self.assertEqual(sorted(r["recomputed"] for r in rows), ["f(-1)=2", "f(1)=-2"], rows)

    def test_点名了别的函数就不许拿本单元唯一的函数算(self):
        """`AC''(q)=…` 里的撇号曾被漏掉 → "文本里没点名" → 拿 TC 核 AC 的极值。"""
        rev = {"sections": [{"variants": [{"q": "设 TC(q)=2q^2+q+32，求使平均成本最小的产量。",
                       "steps": [{"text": "故 q = 4 为最小值点（AC 的二阶导为正）。"}], "a": "q=4"}]}]}
        rows = nm.check_review(rev)
        self.assertFalse([r for r in rows if r["status"] != "unchecked"], rows)


class TestSignLogic(unittest.TestCase):
    """纯符号逻辑：`二阶导数 -3 < 0 …该点为极大值点`（不需要函数）。

    ⚠️ 结论词要跟着**自己那条符号**走；提问句（`判断 x=2 是极大还是极小？`）不核。
    """

    def test_符号与结论一致报_ok(self):
        rev = {"sections": [{"prose": ["二阶导数 -3 < 0，因此函数在 x=2 附近是凹的，该点为极大值点。"]}]}
        rows = nm.check_review(rev)
        self.assertEqual([r["status"] for r in rows], ["ok"], rows)

    def test_符号与结论反着写报_mismatch(self):
        rev = {"sections": [{"prose": ["二阶导数为正，因此该点为极大值点。"]}]}
        rows = nm.check_review(rev)
        self.assertEqual([r["status"] for r in rows], ["mismatch"], rows)

    def test_一句两条符号各配各的结论(self):
        rev = {"sections": [{"prose": ["如果二阶导数为负，该点是极大值点；如果二阶导数为正，该点是极小值点。"]}]}
        rows = nm.check_review(rev)
        self.assertEqual([r["status"] for r in rows], ["ok", "ok"], rows)

    def test_提问句没有结论不核(self):
        rev = {"sections": [{"prose": ["已知函数在 x=-1 处二阶导数为负，判断该点是极大还是极小。"]}]}
        self.assertEqual([r["status"] for r in nm.check_review(rev) if "符号逻辑" in r["how"]], [],
                         nm.check_review(rev))


class TestNumberExtraction(unittest.TestCase):
    """⚠️ 第三次生成的产物上抓到的两类假修正（都出自变式题）。"""

    def test_根号里的点不许只读一半(self):
        """`在 q=10√6 处取最小` —— 只读 `10` 就会报 `AC'(10)≈0.36≠0` 的假修正。"""
        rev = {"sections": [{"variants": [{"q": "设 AC(q)=q+64/q，求最小平均成本。",
                                          "steps": ["在 q=10√6 处取最小。"],
                                          "a": "q=10√6"}]}]}
        rows = nm.check_review(rev)
        self.assertFalse([r for r in rows if r["status"] == "mismatch"], rows)

    def test_极值那个值是值不是点(self):
        """`最小平均成本为 16` —— 16 是**值**，不是驻点（实测报过 `AC'(16)≈0.75`）。"""
        rev = {"sections": [{"variants": [{"q": "设 AC(q)=q+64/q，求最小平均成本。",
                                          "steps": ["AC(q)=q+64/q。",
                                                    "最小平均成本为 16。"],
                                          "a": "16"}]}]}
        rows = nm.check_review(rev)
        self.assertFalse([r for r in rows if r["status"] == "mismatch"], rows)
        self.assertEqual(rows, [], "这句话本来就不该出核对行（没有点）")


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


class TestBareFrac(unittest.TestCase):
    """第七轮：模型忘写 `$…$` 的裸 `\\frac{…}{…}` 也要包成公式（BUSI3243/ECS170 实测 12/16 处）。"""

    def test_裸_frac_连嵌套花括号一起包(self):
        got = nm.auto_tex(r"算一下 t = \frac{\hat{β}_j}{SE(\hat{β}_j)}，再查表。")
        self.assertIn(r"$\frac{\hat{β}_j}{SE(\hat{β}_j)}$", got)
        self.assertIn("算一下 t = ", got)
        self.assertIn("，再查表。", got)

    def test_已经在公式里的不动_幂等(self):
        s = r"$t = \frac{a}{b}$"
        self.assertEqual(nm.auto_tex(s), s)
        once = nm.auto_tex(r"\frac{1}{1+e^{-z}}")
        self.assertEqual(nm.auto_tex(once), once)

    def test_括号不配平就不动(self):
        self.assertNotIn("$", nm.auto_tex(r"\frac{1}{2 没闭合"))

    def test_两个_frac_各自包(self):
        got = nm.auto_tex(r"=\frac{∂E}{∂y}·\frac{∂y}{∂β}")
        self.assertEqual(got.count("$"), 4)

    def test_裸矩阵环境包成公式(self):
        """F7：ECS 实测 `W=\\begin{bmatrix}1&0\\\\0&1\\end{bmatrix}` 裸在正文里 → 包成 $…$。"""
        got = nm.auto_tex(r"权重为 W=\begin{bmatrix}1&0\\0&1\end{bmatrix}，求输出。")
        self.assertIn(r"$\begin{bmatrix}1&0\\0&1\end{bmatrix}$", got)
        self.assertIn("权重为 W=", got)
        self.assertIn("，求输出。", got)

    def test_没有配对的_end_就不动(self):
        self.assertNotIn("$", nm.auto_tex(r"\begin{bmatrix}1&0"))

    def test_真代码不误吞(self):
        """反引号外的真代码没有反斜杠命令 → 一个字节都不动。"""
        s = "在 PyTorch 里，一行 torch.nn.Linear(in_features, out_features) 就定义了这个层。"
        self.assertEqual(nm.auto_tex(s), s)


class TestLimitExtremum(unittest.TestCase):
    """F8-c（作者从 econ 变式 W3 抓到）：`q→∞` 的趋向值**不是**最值 —— 必须拦。

    文本里是 TeX（`$q\\to\\infty$`）也要认（F7 的原句就是 TeX 写法）。
    """

    W3 = "$AC(q)=100+\\frac{400}{q}$，当$q\\to\\infty$时$AC\\to100$，最低点为100。"

    def _rows(self, text):
        rev = {"sections": [{"heading": "H", "ts": "13:16",
                             "variants": [{"q": "求停产价格。", "a": text}]}]}
        return [r for r in nm.check_review(rev)
                if r.get("status") == "mismatch"
                and "极限" in str(r.get("recomputed") or "")]

    def test_TeX写法的最值断言拦住(self):
        self.assertTrue(self._rows(self.W3))

    def test_补全题_停产价格靠极限也要拦(self):
        t = ("$AC(q)=80+\\frac{200}{q}$，当$q\\to\\infty$时$AC\\to80$，"
             "故停产价格为80。")
        self.assertTrue(self._rows(t))

    def test_说清楚取不到就不误伤(self):
        """正话：`取不到` —— 明说没有最低点的是对的，不许拦。"""
        t = ("$AC(q)=100+\\frac{400}{q}$，当$q\\to\\infty$时$AC\\to100$，"
             "但这个值在有限产量下取不到，所以该函数没有最低点。")
        self.assertFalse(self._rows(t))


#: F9 的 fixture：**作者逐行核对过的转录原话**（`sessions/2026-10-15_131620_ECON10790.md`
#: L19–L36、L64–L86 —— READ ONLY 抄来，含 ASR 的 "5.3 over 3"（= 口头 "25 over 3"））。
F9_FRAGMENTS = (
    "Did someone calculate this value, more or less? It's something close to 3. "
    "3 point something. 5.3 over 3. Is what? 5.3 over 3. Yeah, but can you get the number? 2.8. "
    "2.89. 2.89. 2.89. 2.89. The first one. According to the fact, yes. "
    "The lowest acceptable price, the shutting down price to finish the question, "
    "So we say, OK, the shutting down price is P equal the average cost at … 2.89. "
    "So it's the value of your average cost at this quantity. "
    "and it's therefore 60 times this plus 500 divided by this point."
)


class TestF9_QVSP(unittest.TestCase):
    """F9（作者读转录后重定口径）：课上的函数是 **AC(q)=60q+500/q**，q*=√(25/3)≈2.89，
    停产价格 **P=AC(q*)≈346.4** —— 「2.89 是**产量**，不是价格」。

    期望（作者的验收）：要么按口述还原（端到端能验：AC'=0 解出 q*、P=AC(q*)），
    要么只留片段；**永远不许 P≈2.89**。
    """

    def _good(self) -> dict:
        return {"sections": [{"heading": "[13:16] 停产价格", "ts": "13:16",
            "prose": ["按口述还原：$AC(q)=60q+500/q$ 在 $q\\approx2.89$ 处取最小值。"
                      "$AC'(q)=60-500/q^2=0$ 得 $q^2=500/60=25/3$；停产价格 $P=AC(2.89)\\approx346.4$。"],
            "example": {"title": "🎙+✏️ 按口述还原",
                        "prompt": "已知 $AC(q)=60q+500/q$，求停产价格。",
                        "steps": [{"text": "$AC'(q)=60-500/q^2=0$ 得 $q^2=25/3$，$q\\approx2.89$。",
                                   "why": "驻点"},
                                  {"text": "$AC(2.89)\\approx346.4$。", "why": "代回"}],
                        "answer": "停产价格约为346.4。"}}]}

    def test_按口述还原_端到端能验(self):
        rows = nm.check_review(self._good(), transcript=F9_FRAGMENTS)
        bad = [r for r in rows if r.get("status") == "mismatch"]
        self.assertEqual([], bad, bad)
        joined = " ".join(str(r.get("how")) for r in rows)
        self.assertIn("25/3", joined, "链式分数 500/60=25/3 要有自己的行（不许读成 500/60=25）")
        self.assertIn("舍入", joined, "q≈2.89 的驻点要按舍入容忍过（±1% 邻域变号）")
        self.assertIn("346.4", joined, "P=AC(2.89)≈346.4 要真的代入复算过")

    def test_旧写法_q当P_被拦(self):
        """F8 出货的形态（`AC(2.89)≈2.89`、P≈2.89）—— 必须拦，且落在题块里（走返修）。"""
        bad_case = {"sections": [{"heading": "[13:16]", "ts": "13:16",
            "example": {"title": "🎙 13:16（片段）",
                        "prompt": "已知平均成本函数在q=2.89时达到最小值，且AC(2.89)≈2.89，求停产价格。",
                        "steps": [{"text": "代入得P≈2.89。", "why": ""}],
                        "answer": "停产价格约为2.89。"}}]}
        rows = [r for r in nm.check_review(bad_case) if r.get("status") == "mismatch"]
        self.assertTrue(rows, "P≈2.89 必须被拦")
        s = bad_case["sections"][0]
        self.assertTrue(any(nm.unit_of_claim(s, str(r.get("claim") or "")) in nm.MODEL_AUTHORED
                            for r in rows), "行列必须落在题块里（走 F8 返修路径）")
        joined = " ".join(str(r.get("how") or "") for r in rows)
        self.assertIn("代入点", joined)

    def test_只留片段_不误伤(self):
        frag = {"sections": [{"heading": "[13:16]", "ts": "13:16",
            "example": {"title": "🎙 13:16（片段）",
                        "prompt": "课上算出 q≈2.89，函数没录全。",
                        "steps": [{"text": "老师只给了片段，复算不了。", "why": ""}],
                        "answer": ""}}]}
        bad = [r for r in nm.check_review(frag) if r.get("status") == "mismatch"]
        self.assertEqual([], bad, bad)

    def test_两个角色撞数_拦(self):
        """F9c 域中立：不许用「停产价格」字样，只认「同一个数 ≈ 取点、却出现在另一角色上」。"""
        rev = {"sections": [{"heading": "H", "ts": "13:16",
                             "prose": ["在 $q\\approx2.89$ 处取最低。"],
                             "example": {"title": "🎙 13:16",
                                         "prompt": "课上给出 $q\\approx2.89$ 处取最小，求答案。",
                                         "steps": [{"text": "所以答案 $P\\approx2.89$。", "why": ""}],
                                         "answer": ""}}]}
        rows = [r for r in nm.check_review(rev) if r.get("status") == "mismatch"]
        self.assertTrue(rows, "q≈2.89 与 P≈2.89 撞数必须被拦")
        self.assertIn("≈ 取点", " ".join(str(r.get("recomputed") or "") for r in rows))

    def test_最低点为取点不误伤(self):
        """「最低点为6」= 说的是取点本身，是正话 —— 不许拦。"""
        rev = {"sections": [{"heading": "H", "ts": "13:16",
                             "prose": ["令 $f'(x)=0$ 得 $x=6$，最低点为6。"],
                             "example": {"title": "🎙 13:16",
                                         "prompt": "已知 $f(x)=x^{2}-12x$，求最低点。",
                                         "steps": [{"text": "$f'(x)=2x-12=0$ 得 $x=6$。", "why": ""}],
                                         "answer": ""}}]}
        bad = [r for r in nm.check_review(rev) if r.get("status") == "mismatch"]
        self.assertEqual([], bad, bad)

    def test_和式里的除法项不许当单条(self):
        """F9 首跑实录（真实产物）：这两句都是**求和** —— 旧行为把除法项读成单条除法，
        报出两条假 ⚠（还因此把 §0 的题块按"复检仍错"撤了）。"""
        cases = [
            ("代入具体函数：AC(q)=60q+500/q，在q=2.89处，AC=60×2.89+500/2.89≈173.4+173.0=346.4。"
             "所以停产价格约为346.4。", "500÷2.89"),
            ("例如，若总成本 $TC(q)=q^2+225$，则 $AC(q)=q+225/q$，$MC(q)=2q$。令 $AC=MC$ 得 "
             "$q^2=225$，$q=15$，此时 $AC(15)=15+225/15=30$，所以停产价格为30。", "225÷15"),
            # DB 审查 D-25：中文**动词**形式的和式（`15 加上 225 除以 15 等于 30`）——
            # 连接词是「加上」不是 `+`，旧判据（只看前一个字符）放它过去 → 假 ⚠。
            ("例如 15 加上 225 除以 15 等于 30，所以停产价格为 30。", "225÷15"),
        ]
        for text, marker in cases:
            rows = nm.check_review({"sections": [{"heading": "H", "ts": "13:16",
                                                  "prose": [text]}]})
            self.assertFalse([r for r in rows if r.get("status") == "mismatch"
                              and marker in str(r.get("how") or "")], (text, rows))


class TestF11Program(unittest.TestCase):
    """F11 §2：**代码算、模型讲** —— 例题给 program，note_math 数值引擎执行。

    作者 2026-10-18 批准这轮架构改动（反转 10-17 的"禁止代码算答案"）：
    两次例题错误都是模型自己算/自己还原出来的。程序只用现成的数值引擎
    （`ast` 白名单 + 数值导数），**没有新依赖（无 sympy）**。
    """

    def _ex(self, **kw) -> dict:
        ex = {"title": "🎙 13:16", "prompt": "已知 $AC(q)=60q+500/q$，求停产价格。",
              "steps": [{"text": "令 $AC'(q)=0$ 求驻点。", "why": "一阶条件"},
                        {"text": "把 $q^*$ 代回 $AC$。", "why": "求价格"}],
              "answer": "停产价格约为346.4。"}
        ex.update(kw)
        return {"sections": [{"heading": "[13:16] 停产价格", "ts": "13:16",
                              "prose": ["讲解。"], "example": ex}]}

    def test_argmin_eval_端到端(self):
        rev = self._ex(program=[
            {"var": "q_star", "op": "argmin", "expr": "60*q + 500/q", "x": "q",
             "range": [1, 10]},
            {"var": "P", "op": "eval", "expr": "60*q + 500/q", "x": "q", "at": "q_star"}])
        vals, err = nm.run_program(rev["sections"][0]["example"]["program"])
        self.assertEqual("", err)
        self.assertAlmostEqual(vals["q_star"], 2.8868, places=3)
        self.assertAlmostEqual(vals["P"], 346.41, places=1)
        bad = [r for r in nm.check_review(rev) if r.get("status") == "mismatch"]
        self.assertEqual([], bad, bad)

    def test_单调函数当最值_程序落端点_失败(self):
        """`(60q+500)/q` 单调递减 —— argmin 会落到区间端点，program 直接失败（F8 那类错的根治）。"""
        rev = self._ex(program=[
            {"var": "q_star", "op": "argmin", "expr": "(60*q + 500)/q", "x": "q",
             "range": [1, 10]},
            {"var": "P", "op": "eval", "expr": "(60*q + 500)/q", "x": "q", "at": "q_star"}],
            answer="最低点为 60。")
        rows = [r for r in nm.check_review(rev) if r.get("status") == "mismatch"]
        self.assertTrue(rows, "单调函数的 argmin 必须失败")
        self.assertIn("端点", " ".join(str(r.get("how") or "") for r in rows))

    def test_答案端出中间量_P对不上q星(self):
        """目标量是最后一步的 var：答案写了中间量 q*≈2.89 而不是 P≈346.4 → mismatch。"""
        rev = self._ex(program=[
            {"var": "q_star", "op": "argmin", "expr": "60*q + 500/q", "x": "q",
             "range": [1, 10]},
            {"var": "P", "op": "eval", "expr": "60*q + 500/q", "x": "q", "at": "q_star"}],
            steps=[{"text": "驻点 $q^*\\approx2.89$。", "why": ""}],
            answer="停产价格为 2.89。")
        rows = [r for r in nm.check_review(rev) if r.get("status") == "mismatch"]
        how = " ".join(str(r.get("how") or "") for r in rows)
        self.assertTrue(rows, "答案端中间量必须拦")
        self.assertIn("中间量", how)

    def test_步骤里的野数字_拦(self):
        """步骤里冒出一个既不是程序值、也不是题面给定的数 → mismatch（喂返修）。"""
        rev = self._ex(program=[
            {"var": "q_star", "op": "argmin", "expr": "60*q + 500/q", "x": "q",
             "range": [1, 10]},
            {"var": "P", "op": "eval", "expr": "60*q + 500/q", "x": "q", "at": "q_star"}],
            steps=[{"text": "驻点 $q^*\\approx2.89$，代入得 999。", "why": ""}],
            answer="停产价格约为346.4。")
        rows = [r for r in nm.check_review(rev) if r.get("status") == "mismatch"]
        self.assertTrue(any("999" in str(r.get("how") or "") for r in rows), rows)

    def test_argmin找内部极小_argmax落端点(self):
        """`(q-3)^2` 在 [0,6] 有内部极小 3：`argmin` 找得到，`argmax` 落端点 → 失败。
        钉住 op 的方向（argmin/argmax 不许互换）。"""
        vals, err = nm.run_program([
            {"var": "p", "op": "argmin", "expr": "(q-3)^2", "x": "q", "range": [0, 6]}])
        self.assertEqual("", err)
        self.assertAlmostEqual(vals["p"], 3.0, places=4)
        _v, err = nm.run_program([
            {"var": "p", "op": "argmax", "expr": "(q-3)^2", "x": "q", "range": [0, 6]}])
        self.assertIn("端点", err)

    def test_deriv_与_root(self):
        vals, err = nm.run_program([
            {"var": "slope", "op": "deriv", "expr": "q^3 - 3*q", "x": "q", "at": 1}])
        self.assertEqual("", err)
        self.assertAlmostEqual(vals["slope"], 0.0, places=6)
        vals, err = nm.run_program([
            {"var": "r", "op": "root", "expr": "q^2 - 4", "x": "q", "range": [0, 10]}])
        self.assertEqual("", err)
        self.assertAlmostEqual(vals["r"], 2.0, places=6)

    def test_givens_常量表能用(self):
        vals, err = nm.run_program(
            [{"var": "y", "op": "eval", "expr": "K/q", "x": "q", "at": 4}],
            {"K": 100})
        self.assertEqual("", err)
        self.assertAlmostEqual(vals["y"], 25.0)

    def test_坏的op_与坏的表达式_给人话(self):
        _v, err = nm.run_program([{"var": "a", "op": "integrate",
                                   "expr": "q", "x": "q", "range": [0, 1]}])
        self.assertIn("未知 op", err)
        _v, err = nm.run_program([{"var": "a", "op": "eval", "expr": "q +* 2",
                                   "x": "q", "at": 1}])
        self.assertIn("解析不了", err)

    def test_没有program的例题_整段跳过(self):
        rev = self._ex()                     # 定性/概念题：没有 program
        rows = nm.program_rows(rev["sections"][0])
        self.assertEqual([], rows, "没有 program 不许做数值判断")

    def test_补全题同一批数_过(self):
        """F11 §4：faded = 例题原样挖空（数不换）→ 不报。"""
        sec = {"heading": "h", "ts": "13:16",
               "example": {"title": "📖 Ex5", "prompt": "$TC(q)=q^{3}-12q^{2}+60q$",
                           "steps": [{"text": "求 $AC$。", "why": ""}],
                           "answer": "$AC=q^2-12q+60$。"},
               "faded": {"q": "补全：$TC(q)=q^{3}-12q^{2}+60q$ 时 $AC=$ ____",
                         "blanks": ["$q^2-12q+60$"], "a": "$q^2-12q+60$"}}
        self.assertEqual([], nm.reuse_rows(sec))

    def test_补全题冒新数字_拦(self):
        sec = {"heading": "h", "ts": "13:16",
               "example": {"title": "📖 Ex5", "prompt": "$TC(q)=q^{3}-12q^{2}+60q$",
                           "steps": [], "answer": "$AC=q^2-12q+60$。"},
               "faded": {"q": "补全：$TC(q)=q^{3}-12q^{2}+100q$ 时 $AC=$ ____",
                         "blanks": ["$q^2-12q+100$"], "a": "$q^2-12q+100$"}}
        rows = nm.reuse_rows(sec)
        self.assertTrue(rows, "补全题换了给定数（100）必须拦")
        self.assertIn("同一道例题", str(rows[0].get("how")))

    def test_补全题答案的派生值不误伤(self):
        """解里的派生值（dAC/dq=0 → q=6）不是新算术 —— 只查题面。"""
        sec = {"heading": "h", "ts": "13:16",
               "example": {"title": "📖 Ex5", "prompt": "$TC(q)=q^{3}-12q^{2}+60q$",
                           "steps": [], "answer": "$AC=q^2-12q+60$。"},
               "faded": {"q": "补全：$TC(q)=q^{3}-12q^{2}+60q$，令 $AC'=0$ 得 $q=$ ____",
                         "blanks": ["6"], "a": "$q=6$"}}
        self.assertEqual([], nm.reuse_rows(sec))

    def test_自变量名不一致也认(self):
        """模型把自变量写成 `x`，而 `x` 字段填 `q`（真跑 econ §3/§9 实测）→ 引擎要能认。"""
        vals, err = nm.run_program([
            {"var": "q_star", "op": "root", "expr": "x + 225/x - 2*x", "x": "q",
             "range": [1, 30]}])
        self.assertEqual("", err)
        self.assertAlmostEqual(vals["q_star"], 15.0, places=4)

    def test_步骤里的给定函数系数不算野数字(self):
        """步骤里写出给定函数 `q^2 - 12q + 60`（**纯文本，不在 `$…$` 里**），
        系数 12/60 是给定 —— 相依 caused by sign flip（题面是 `-12q^2`）也不许当野数字（econ §8 实测）。"""
        rev = self._ex(program=[
            {"var": "q_star", "op": "argmin", "expr": "x^2 - 12*x + 60", "x": "x",
             "range": [1, 10]}],
            prompt="总成本 $TC(q)=q^{3}-12q^{2}+60q$，求平均成本最低时的产量。",
            steps=[{"text": "AC = TC/q = q^2 - 12q + 60，令导数为零。", "why": ""}],
            answer="最低点在 $q=6$。")
        bad = [r for r in nm.check_review(rev) if r.get("status") == "mismatch"]
        self.assertEqual([], bad, bad)

    def test_没有例题或faded_空(self):
        self.assertEqual([], nm.reuse_rows({"heading": "h"}))
        self.assertEqual([], nm.reuse_rows({"heading": "h", "faded": {"q": "x", "a": "y"}}))

    def test_变式用裸定义_不误报(self):
        """变式写 `$AC=2q+1+32/q$`（裸定义，没 `(q)`）→ 不许回落拿例题的 AC 去核（econ §3 实测）。"""
        sec = {"heading": "h", "ts": "13:25",
               "example": {"title": "📖 Ex5", "prompt": "某企业成本函数 $TC(q)=q^{2}+225$。求停产价。",
                           "steps": [{"text": "$AC(q)=q+225/q$，$MC(q)=2q$", "why": ""},
                                     {"text": "令 $q+225/q=2q$ 得 $q=15$", "why": ""},
                                     {"text": "$AC(15)=30$", "why": ""}],
                           "answer": "停产价 $p=30$，产量 $q=15$。"},
               "variants": [{"q": "✏️ 若 $TC(q)=2q^{2}+q+32$，求停产价。",
                             "steps": [{"text": "裸定义：$AC=2q+1+32/q$，$MC=4q+1$", "why": ""},
                                       {"text": "$2q+1+32/q=4q+1$ 得 $q=4$", "why": ""},
                                       {"text": "$AC(4)=8+1+8=17$", "why": ""}],
                             "a": "停产价 $p=17$。"}]}
        bad = [r for r in nm.check_review({"sections": [sec]}) if r.get("status") == "mismatch"]
        self.assertEqual([], bad, bad)

    def test_补全题新数字_进机械核并归faded单元(self):
        """接进 `check_review`：补全题的新数字要能被返修闸看得见（归 faded 单元）。"""
        sec = {"heading": "h", "ts": "13:16",
               "example": {"title": "📖 Ex5", "prompt": "$TC(q)=q^{3}-12q^{2}+60q$",
                           "steps": [], "answer": "$AC=q^2-12q+60$。"},
               "faded": {"q": "补全：$TC(q)=q^{3}-12q^{2}+100q$ 时 $AC=$ ____",
                         "blanks": ["$q^2-12q+100$"], "a": "$q^2-12q+100$"}}
        rev = {"sections": [sec]}
        bad = [r for r in nm.check_review(rev) if r.get("status") == "mismatch"
               and nm.unit_of_claim(sec, str(r.get("claim") or "")) == "faded"]
        self.assertTrue(bad, "补全题的新数字没进机械核（返修闸看不见）")


class ConserveF12(unittest.TestCase):
    """⭐ F12 §1：例题里的化学方程式守恒核对（元素计数 + 电荷 + 无重复物种）。"""

    def _why(self, eq):
        return nm._check_equation(eq) if nm._looks_chemical(eq) else None

    def test_坏例子必须拦(self):
        # 作者点名的那条（CHE002A §18 真产物）：H₂O 两边都有、e⁻ 放错边 → 不守恒。
        bad = "8H⁺ + 4H₂O + 8e⁻ → 4H₂O"
        self.assertTrue(nm._looks_chemical(bad))
        self.assertNotEqual("", self._why(bad))

    def test_两条好例子必须过(self):
        for good in ("MnO4⁻ + 8H⁺ + 5e⁻ → Mn²⁺ + 4H₂O", "Fe²⁺ → Fe³⁺ + e⁻"):
            self.assertTrue(nm._looks_chemical(good), good)
            self.assertEqual("", self._why(good), good)

    def test_各种写法都要认得(self):
        for good in ("2Na + Cl₂ → 2NaCl", "2Na + Cl2 → 2NaCl",
                     "Ca(OH)₂ + 2H⁺ → Ca²⁺ + 2H₂O", "CuSO4·5H2O → CuSO4 + 5H2O",
                     "Cr2O7²⁻ + 14H⁺ + 6e⁻ → 2Cr³⁺ + 7H₂O",
                     "MnO_4^- + 8H^+ + 5e^- -> Mn^{2+} + 4H_2O",
                     "2H₂O(l) → 2H₂(g) + O₂(g)",              # 状态标 (l)(g)
                     "2H₂(g)+O₂(g) → 2H₂O(l)",                # 不写空格的 +
                     "NaOH(aq) + HCl(aq) → NaCl(aq) + H₂O(l)",
                     "MnO4⁻(aq) + 8H⁺(aq) + 5e⁻ → Mn²⁺(aq) + 4H₂O(l)"):
            self.assertEqual("", self._why(good), good)

    def test_不配平必须拦(self):
        for bad in ("H₂ + O₂ → H₂O", "2H₂ + O₂ → H₂O", "H₂+O₂→H₂O", "NaCl → Na⁺"):
            self.assertNotEqual("", self._why(bad), bad)

    def test_非化学箭头不算(self):
        for s in ("q → ∞", "AC → 80", "P = 60 → 346.4", "MC=AC → q=15"):
            eqs = nm._equations_in(s)
            self.assertFalse(any(nm._looks_chemical(e) for e in eqs), s)

    def test_接进机械核归例题单元(self):
        sec = {"heading": "h", "ts": "10:36",
               "example": {"title": "🎙 10:36", "prompt": "配平半反应。", "steps": [],
                           "answer": "8H⁺ + 4H₂O + 8e⁻ → 4H₂O"}}
        rows = [r for r in nm.check_review({"sections": [sec]})
                if r.get("status") == "mismatch"]
        self.assertTrue(rows, "坏方程式没进机械核")
        self.assertEqual("example", nm.unit_of_claim(sec, str(rows[0].get("claim") or "")))


class DerivF12(unittest.TestCase):
    """⭐ F12：**声明的**导数关系按数值导数核对（跨学科：不认「边际」/「MC」）。"""

    @staticmethod
    def _sec(prompt, steps, program=None, answer=""):
        ex = {"title": "🎙 13:40", "prompt": prompt, "steps": steps, "answer": answer}
        if program is not None:
            ex["program"] = program
        return {"heading": "h", "ts": "13:40", "prose": [], "example": ex}

    def _flags(self, sec):
        return [r for r in nm.check_review({"sections": [sec]})
                if r.get("status") == "mismatch"]

    def test_econ_9真错必须拦(self):
        # 作者点名：TC' 是 3q²−24q+60，笔记写 3q²−12q+60（dTC/dq 记法声明了关系）。
        sec = self._sec("$TC(q)=q^3-12q^2+60q$，用 MC=AC 求最低点。",
                        [{"text": "$MC = dTC/dq = 3q^2 - 12q + 60$", "why": ""}],
                        answer="$q=6$")
        self.assertTrue(self._flags(sec))

    def test_正确的MC必须过(self):
        sec = self._sec("$TC(q)=q^3-12q^2+60q$。",
                        [{"text": "$MC = dTC/dq = 3q^2 - 24q + 60$", "why": ""}],
                        answer="$MC=3q^2-24q+60$")
        self.assertEqual([], self._flags(sec))

    def test_prime记法也核(self):
        good = self._sec("$AC(q)=q^2-12q+60$。",
                         [{"text": "$AC'(q)=2q-12$", "why": ""}], answer="$AC'=2q-12$")
        bad = self._sec("$AC(q)=q^2-12q+60$。",
                        [{"text": "$AC'(q)=2q-60$", "why": ""}], answer="$AC'=2q-60$")
        self.assertEqual([], self._flags(good))
        self.assertTrue(self._flags(bad))

    def test_physics_换名也认_program声明(self):
        good = self._sec("$x(t)=5t^2$，求 $t=2$ 时速度 $v$。",
                         [{"text": "$v(t)=10t$", "why": ""}, {"text": "$v(2)=20$", "why": ""}],
                         program=[{"var": "v", "op": "deriv", "expr": "5*t^2", "x": "t", "at": 2}],
                         answer="$v(2)=20$（$v(t)=10t$）")
        bad = json.loads(json.dumps(good))
        bad["example"]["steps"] = [{"text": "$v(t)=5t$", "why": ""}]
        bad["example"]["answer"] = "$v(2)=20$（$v(t)=5t$）"
        self.assertEqual([], self._flags(good))
        self.assertTrue(self._flags(bad))

    def test_没声明关系不判断(self):
        # 只有 x(t)=5t² 与 v(t)=5t（错），但没有 prime/d 记法、program 也没有 deriv 步
        # → 关系没被声明 → **不判断**（不许靠名字猜）。
        sec = self._sec("$x(t)=5t^2$。", [{"text": "$v(t)=5t$", "why": ""}], answer="$v(t)=5t$")
        self.assertEqual([], self._flags(sec))

    def test_令导数为零是解方程不误伤(self):
        # ⭐ 实测假警：`令 f'(x)=0，得 x=±1` 是**解方程**，不是"导数恒为 0"。
        sec = self._sec("$f(x)=x^3-3x$。",
                        [{"text": "令 $f'(x)=0$，得 $x=1$ 或 $x=-1$", "why": ""},
                         {"text": "$f''(x)=6x$", "why": ""}],
                        answer="$x=1$（极小），$x=-1$（极大）")
        self.assertEqual([], self._flags(sec))

    def test_latex_记法都要认得(self):
        # 笔记是 KaTeX 渲染的 → LaTeX 是**常态**：每种写法都要"错的拦、对得过"。
        base = "$TC(q)=q^3-12q^2+60q$。"
        forms = [
            r"$\frac{dTC}{dq}=3q^2-12q+60$",
            r"$\frac{d}{dq}TC(q)=3q^2-12q+60$",
            r"$\frac{\mathrm{d}TC}{\mathrm{d}q}=3q^2-12q+60$",
            r"$\dfrac{dTC}{dq}=3q^2-12q+60$",
            r"$\tfrac{dTC}{dq}=3q^2-12q+60$",
            r"$\frac{\partial TC}{\partial q}=3q^2-12q+60$",
            r"$\partial TC/\partial q=3q^2-12q+60$",
            r"$\frac{d\,TC}{d\,q}=3q^2-12q+60$",
            r"$TC^{\prime}(q)=3q^2-12q+60$",
            r"$TC'\left(q\right)=3q^2-12q+60$",
            r"$MC(q)=TC'(q)=3q^2-12q+60$",
        ]
        for s in forms:
            self.assertTrue(self._flags(self._sec(base, [{"text": s, "why": ""}], answer="$q=6$")),
                            f"该拦：{s}")
            self.assertEqual([], self._flags(self._sec(
                base, [{"text": s.replace("12q+60", "24q+60"), "why": ""}], answer="$q=6$")),
                f"该过：{s}")

    def test_点不够就不判断(self):
        # 域外点太多（log 在负值）→ 有效点 <5 → 不判断（宁可漏，不误伤）。
        sec = self._sec("$f(x)=\\ln(x)$。", [{"text": "$f'(x)=1/x$", "why": ""}], answer="$1/x$")
        self.assertEqual([], self._flags(sec))


class VerdictF12(unittest.TestCase):
    """⭐ F12 §2：`complete` 必须被代码复核（结论数字要在 quote 里或由 program 算出）。"""

    def test_数字不在quote则强制fragment(self):
        ex = {"prompt": "求停产价。$AC=60q+500/q$", "steps": [], "answer": "P≈346.4。",
              "source_check": {"quote": "求最低平均成本。", "verdict": "complete"}}
        out = nm.enforce_source_verdict(ex)
        self.assertEqual("fragment", out["verdict"])
        self.assertTrue(out.get("forced"))

    def test_数字在quote则保留complete(self):
        ex = {"prompt": "求停产价。", "steps": [], "answer": "P≈346.4。",
              "source_check": {"quote": "停下来那点大约 346.4，是 sixty 乘这个加五百除这个。",
                               "verdict": "complete"}}
        self.assertEqual("complete", nm.enforce_source_verdict(ex)["verdict"])

    def test_数词也算说出来(self):
        ex = {"prompt": "需要多少电子？", "steps": [], "answer": "需要 40 个电子。",
              "source_check": {"quote": "we need forty electrons.", "verdict": "complete"}}
        self.assertEqual("complete", nm.enforce_source_verdict(ex)["verdict"])

    def test_负号不算没说过(self):
        # 口语 quote 写「minus 12」/「12」，例题写 −12 —— 不许因符号判成 fragment。
        ex = {"prompt": "$TC=q^3-12q^2+60q$", "steps": [], "answer": "$q=6$",
              "source_check": {"quote": "Q squared minus 12 Q plus 60", "verdict": "complete"}}
        self.assertEqual("complete", nm.enforce_source_verdict(ex)["verdict"])

    def test_中间算术不在quote不降级(self):
        # 「8 与 5 的 LCM 是 40」是步骤里的推导，本来就不在 quote 里 —— 不降级。
        ex = {"prompt": "两个半反应分别产生 8 e⁻ 和消耗 5 e⁻，如何合并？",
              "steps": [{"text": "8 和 5 的最小公倍数是 40。", "why": ""}],
              "answer": "分别乘以 5 和 8 后相加。",
              "source_check": {"quote": "eight electrons ... five electrons", "verdict": "complete"}}
        self.assertEqual("complete", nm.enforce_source_verdict(ex)["verdict"])

    def test_program算出来的允许(self):
        ex = {"prompt": "求 P。", "steps": [], "answer": "P≈346.4。",
              "program": [{"var": "P", "op": "eval", "expr": "60*q + 500/q", "x": "q", "at": 2.89}],
              "source_check": {"quote": "把 2.89 带进去。", "verdict": "complete"}}
        self.assertEqual("complete", nm.enforce_source_verdict(ex)["verdict"])


if __name__ == "__main__":
    unittest.main()
