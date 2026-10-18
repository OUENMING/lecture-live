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


if __name__ == "__main__":
    unittest.main()
