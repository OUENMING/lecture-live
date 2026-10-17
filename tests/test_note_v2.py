"""笔记 v2 的判据：生成侧（`note_v2`，全用替身 `chat`）+ 版式侧（`note_render` / `note_html`）。

⭐ 三条最重要的：
   · `note_v2.review` 的**清洗**：模型给歪 JSON 时宁可少内容，绝不半份错位贴上去；
   · `note_render.render` 的**结构不变量**：速览 → 纲要 → 讲解 → …（骨架先于细节）、
     附录 B 的转录**不精修**、中文占比达标（作者验收表里那条）；
   · `note_html` 的**六个组件**在页面上真的存在（含"没有 KaTeX 也不白屏"）。
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import note_html as nh          # noqa: E402
import note_render as nr        # noqa: E402
import note_v2 as nv            # noqa: E402


def entries(n=3):
    return [{"ts": f"13:{16 + i:02d}:00", "en": f"sentence about cost {i}.", "zh": f"第 {i} 句。",
             "asr": "", "star": False} for i in range(n)]


SECTION = {
    "heading": "边际成本（marginal cost）", "ts": "13:31",
    "prereq_link": "这一节要用到上一节的「平均成本」",
    "prose": ["先说直觉：边际成本是「再多做一单位要多花多少」。",
              "定义是 $MC=\\frac{dTC}{dq}$，在本题里等于 $3q^2-24q+60$。"],
    "prereq": "要知道导数的意思，不知道的话先看上一节。",
    "example": {"title": "📖 Exercise 5", "prompt": "$TC(q)=q^3-12q^2+60q$",
                "steps": [{"text": "求 $AC$：把 $TC$ 除以 $q$", "why": "平均成本的定义就是摊到每一单位"},
                          {"text": "求 $MC$：对 $q$ 求导", "why": "「边际」= 多一单位带来的增量"}],
                "answer": "$AC=q^2-12q+60$，$MC=3q^2-24q+60$"},
    "variants": [{"q": "设 $TC=q^3-6q^2+15q$，求使 $AC$ 最小的 $q$。",
                  "steps": [{"text": "令 $MC=AC$，得 $2q^2=12q$", "why": "相等处就是平均被拉平的地方"}],
                  "a": "$q=3$"}],
    "faded": {"q": "补全：$TC=q^3-12q^2+60q$ 时 $\\frac{dAC}{dq}=$ ____",
              "blanks": ["$2q-12$"], "a": "$q=6$"},
    "pitfalls": ["把 $MC$ 与 $AC$ 的符号搞反"], "terms": [{"en": "marginal cost", "zh": "边际成本"}],
    "sources": ["🎙 13:31", "📖 final.md"],
    "plot": {"kind": "cost_curves", "title": "AC 与 MC", "params": {"a": 1, "b": -12, "c": 60}},
}

REVIEW = {"title_zh": "成本函数", "sections": [SECTION],
          "qa": [{"q": "为什么 $MC<AC$ 时 $AC$ 会下降？", "a": "低于平均的数掺进来会把平均拉低。",
                  "kind": "concept"}],
          "checks": [{"claim": "MC(4)=12", "how": "代入 q=4", "status": "ok"}],
          "corrections": [{"where": "MC(4)", "spoken": "20", "fixed": "12", "basis": "📚 代入求导"}],
          "lecture_points": [{"ts": "13:31", "point": "边际成本的定义与计算"}],
          "covered": [{"point": "边际成本的定义与计算", "section": "[13:31] 边际成本（marginal cost）"}],
          "terms": [{"en": "marginal cost", "zh": "边际成本"}],
          "review_plan": ["重做 §2 的两种解法（15 分钟）"]}


class Clean(unittest.TestCase):
    def test_歪字段丢掉不抛(self):
        self.assertEqual(nv._clean(None), {})
        self.assertEqual(nv._clean({"sections": "不是列表"}), {})
        self.assertEqual(nv._clean({"sections": [{"heading": ""}]}), {})
        self.assertEqual(nv._clean({"qa": [{"q": "只有问题"}]}), {})

    def test_步骤容忍纯字符串(self):
        got = nv._clean({"sections": [{"heading": "H", "example": {
            "prompt": "P", "steps": ["第一步", "第二步"]}}]})
        steps = got["sections"][0]["example"]["steps"]
        self.assertEqual([s["text"] for s in steps], ["第一步", "第二步"])

    def test_图只认菜单里的_kind(self):
        good = nv._clean({"sections": [{"heading": "H", "plot": {
            "kind": "supply_demand", "params": {"d0": 100}}}]})
        self.assertEqual(good["sections"][0]["plot"]["kind"], "supply_demand")
        bad = nv._clean({"sections": [{"heading": "H", "plot": {"kind": "自作主张"}}]})
        self.assertEqual(bad["sections"][0]["plot"], {})

    def test_checks_status_只认三档(self):
        got = nv._clean({"checks": [{"claim": "x", "status": "嗯"}]})
        self.assertEqual(got["checks"][0]["status"], "unchecked", "非法状态该退到未核对")


class Merge(unittest.TestCase):
    def test_按块序_去重_加时间前缀(self):
        blocks = [{"title_zh": "A", "sections": [{"heading": "H1", "ts": "10:00", "prose": ["p"]}],
                   "qa": [{"q": "Q?", "a": "a"}], "terms": [{"en": "T", "zh": "t"}]},
                  {"sections": [{"heading": "H2", "ts": "10:10", "prose": ["p"]}],
                   "qa": [{"q": "q?", "a": "a2"}], "terms": [{"en": "t", "zh": "t"}]}]
        got = nv.merge_blocks(blocks)
        self.assertEqual([s["heading"] for s in got["sections"]],
                         ["[10:00] H1", "[10:10] H2"])
        self.assertEqual(len(got["qa"]), 1, "重复的 qa 没去掉（大小写无关）")
        self.assertEqual(len(got["terms"]), 1)
        self.assertEqual(got["title_zh"], "A")

    def test_全空返回空(self):
        self.assertEqual(nv.merge_blocks([{}, {}]), {})


class ReviewCall(unittest.TestCase):
    def test_没句子不发请求(self):
        calls = []

        def chat(s, u, m):
            calls.append(1)
            return {}

        self.assertEqual(nv.review([], chat=chat), {})
        self.assertEqual(calls, [])

    def test_课件片段进了_prompt(self):
        seen = {}

        def chat(system, user, max_tokens):
            seen["user"] = user
            seen["system"] = system
            return {"sections": [{"heading": "H", "ts": "10:00", "prose": ["p"]}]}

        class M:
            def search(self, q, k=3):
                from materials import Chunk
                return [Chunk(file="final.md", heading="Exercise 5", text="AC = q^2-12q+60",
                              score=1.0)]

        nv.review(entries(1), chat=chat, materials=M(), domain="ECON10790 Maths")
        self.assertIn("📖 final.md §Exercise 5", seen["user"])
        self.assertIn("Course: ECON10790 Maths", seen["user"])
        self.assertIn("cost_curves", seen["system"], "图菜单没进 prompt")

    def test_进度回调按块报(self):
        seen = []
        nv.review(entries(nv.REVIEW_CHUNK * 2), chat=lambda s, u, m: {},
                  on_progress=lambda st, d, t: seen.append((st, d, t)))
        self.assertEqual(seen, [("review", 0, 2), ("review", 1, 2)])


class Align(unittest.TestCase):
    def test_解析覆盖度(self):
        def chat(s, u, m):
            self.assertIn("边际成本", u)
            return {"covered": [{"point": "边际成本", "section": "1. 边际成本"}],
                    "missing": [{"ts": "13:40", "point": "shutdown price", "why": "页数不够"}],
                    "materials_used": ["final.md"], "notes": "覆盖不错"}

        got = nv.align(REVIEW, chat=chat)
        self.assertEqual(got["covered"][0]["section"], "1. 边际成本")
        self.assertEqual(got["missing"][0]["point"], "shutdown price")
        self.assertEqual(got["materials_used"], ["final.md"])

    def test_没知识点不调用(self):
        calls = []

        def chat(s, u, m):
            calls.append(1)
            return {}
        self.assertEqual(nv.align({"sections": []}, chat=chat), {})
        self.assertEqual(calls, [])

    def test_chat_炸了返回空(self):
        def boom(s, u, m):
            raise RuntimeError("x")
        self.assertEqual(nv.align(REVIEW, chat=boom), {})


class VerifyNumbers(unittest.TestCase):
    def test_挑出带等号的断言(self):
        claims = nv.numeric_claims({"sections": [SECTION]})
        texts = " ".join(c["text"] for c in claims)
        self.assertIn("AC=q^2-12q+60", texts, "带等号的定义没被挑出来")
        self.assertIn("令 $MC=AC$，得 $2q^2=12q$", texts, "变式的推导步骤没被挑出来")
        self.assertNotIn("先说直觉", texts, "没有数字的段落不该占核对的额度")

    def test_重算不一致标_mismatch(self):
        def chat(s, u, m):
            return {"items": [{"i": 0, "status": "mismatch", "recomputed": "12",
                               "note": "代入 q=4 应为 12"}]}
        got = nv.verify_numbers({"sections": [SECTION]}, chat=chat)
        self.assertEqual(got[0]["status"], "mismatch")
        self.assertEqual(got[0]["recomputed"], "12")
        corr = nv.corrections_from_mismatch(got)
        self.assertEqual(corr[0]["fixed"], "12")

    def test_核不了标_unchecked(self):
        got = nv.verify_numbers({"sections": [SECTION]},
                                chat=lambda s, u, m: {"items": [{"i": 0, "status": "unchecked"}]})
        self.assertEqual(got[0]["status"], "unchecked")

    def test_模型自己写的_checks_不丢(self):
        got = nv.verify_numbers(REVIEW, chat=lambda s, u, m: {})
        self.assertTrue(any(c["claim"] == "MC(4)=12" for c in got))

    def test_序号越界的条目丢掉(self):
        got = nv.verify_numbers({"sections": [SECTION]},
                                chat=lambda s, u, m: {"items": [{"i": 99, "status": "ok"}]})
        self.assertEqual([c for c in got if c.get("claim")], [])


class Schedule(unittest.TestCase):
    def test_间隔日期(self):
        self.assertEqual(nv.review_dates("2026-10-15"),
                         ["2026-10-17", "2026-10-22", "2026-11-05"])

    def test_日期坏了返回空(self):
        self.assertEqual(nv.review_dates("不是日期"), [])


class Anki(unittest.TestCase):
    def test_只导术语_自测与公式(self):
        rows = nv.anki_rows(REVIEW, course="ECON10790", date="2026-10-15")
        fronts = [r[0] for r in rows]
        self.assertIn("marginal cost", fronts)
        self.assertTrue(any("MC<AC" in f for f in fronts))
        self.assertIn("classlive ECON10790 20261015", rows[0][2])

    def test_csv_带_BOM_且三列(self):
        csv = nv.to_csv([["a", "b", "t"]])
        self.assertTrue(csv.startswith("\ufeff"))
        self.assertIn("a,b,t", csv)

    def test_mismatch_不进卡(self):
        bad = {"checks": [{"claim": "MC(4)=20", "how": "x", "status": "mismatch"}]}
        self.assertEqual(nv.anki_rows(bad), [])


class RenderShape(unittest.TestCase):
    def _data(self, **kw):
        d = nr.NoteData(date="2026-10-15", course="ECON10790", entries=entries(3),
                        review=REVIEW, materials_line="📖 课件 2 份 / 索引 9 段",
                        review_dates=["2026-10-17", "2026-10-22"])
        for k, v in kw.items():
            setattr(d, k, v)
        return d

    def test_结构顺序_骨架先于细节_底座在最后(self):
        out = nr.render(self._data())
        i_quick = out.index("## 0 · 五分钟速览")
        i_kc = out.index("## 讲解（由浅入深）")
        i_b = out.index("## 📜 完整逐句转录")
        self.assertLess(i_quick, i_kc)
        self.assertLess(i_kc, i_b, "转录必须在最后（证据层）")

    def test_中文占比达标(self):
        out = nr.render(self._data())
        self.assertGreater(nr.chinese_ratio(out), 0.6,
                           "作者验收表：正文中文占比 ≥60%")

    def test_没有逐句双语对照(self):
        """作者的原话：不要「一行中文一行英文」。转录区除外（那是证据层，且在附录 B）。"""
        body = nr.render(self._data()).split("## 📜 完整逐句转录")[0]
        self.assertNotIn("**EN**:", body, "正文里混进了逐句英文")
        self.assertNotIn("**ASR**:", body)

    def test_例题_每步带为什么(self):
        out = nr.render(self._data())
        self.assertIn("为什么这一步成立：平均成本的定义就是摊到每一单位", out)
        self.assertIn("#### 变式 1（先自己写，再看）", out)
        self.assertIn("#### 补全题（把关键步骤填上）", out)
        self.assertIn("⬜ $2q-12$", out)

    def test_自测有先答再揭晓与闪卡行(self):
        out = nr.render(self._data())
        self.assertIn("先答再揭晓", out)
        self.assertIn("> [!success]- 答案与解析", out)
        self.assertIn("::", out, "`问题::答案` 行丢了 —— Spaced Repetition 就用不了")

    def test_覆盖度与三道核清单(self):
        out = nr.render(self._data())
        self.assertIn("| 教授讲的（🎙） | 笔记覆盖 |", out)
        self.assertIn("边际成本的定义与计算", out)
        self.assertIn("数字 / 公式三道核", out)
        self.assertIn("修正记录", out)
        self.assertIn("代入求导", out, "修正记录必须写出依据（否则读者没法回源）")

    def test_头部写清课件核对与覆盖度(self):
        out = nr.render(self._data())
        self.assertIn("📖 课件 2 份 / 索引 9 段", out)
        self.assertIn("覆盖度：教授知识点 1 个 → 笔记覆盖 1 个", out)

    def test_没课件时头部明说(self):
        d = self._data(materials_line="📖 无课件材料",
                       warns=["本课**未做课件核对** —— 没找到这门课的课件材料"])
        out = nr.render(d)
        self.assertIn("未做课件核对", out)

    def test_转录不精修_原样(self):
        e = entries(1)
        e[0]["en"] = "the origional en sentence"
        self.assertIn("the origional en sentence", nr.render(self._data(entries=e)))

    def test_新加入术语库_N_条写进笔记(self):
        """作者要求的「有感知」：笔记里要有一节列出这节课往术语库里加了什么。"""
        d = self._data(glossary_added=["marginal cost", "shutdown price"])
        out = nr.render(d)
        self.assertIn("**本课新加入术语库 2 条**", out)
        self.assertIn("`marginal cost`", out)
        self.assertIn("`shutdown price`", out)

    def test_复习排程与旁车文件名(self):
        d = self._data(anki_name="x.anki.csv", html_name="x.html")
        out = nr.render(d)
        self.assertIn("2026-10-17", out)
        self.assertIn("x.anki.csv", out)
        self.assertIn("x.html", out)


class HtmlComponents(unittest.TestCase):
    def _page(self):
        d = nr.NoteData(date="2026-10-15", course="ECON10790", entries=entries(2),
                        review=REVIEW, glossary_added=["marginal cost"],
                        term_rows=["- **marginal cost** — 边际成本"],
                        review_dates=["2026-10-17"], html_name="x.html")
        return nh.note_page(d)

    def test_六个组件都在(self):
        h = self._page()
        self.assertIn('id="prog"', h)                            # 5 阅读进度
        self.assertIn("知识点地图", h)                            # 5 目录
        self.assertIn('<details class="step">', h)               # 1 分步展开
        self.assertIn("先自己想 · 再揭晓", h)                     # 2 先答再揭晓
        self.assertIn('class="tex"', h)                          # 3 公式
        self.assertIn('<svg class="plot"', h)                    # 4 图
        self.assertIn("@media print", h)                         # 6 打印兜底
        self.assertIn("prefers-reduced-motion", h)               # 6 动效纪律

    def test_公式离线不白屏(self):
        h = self._page()
        self.assertIn("data-tex=", h)
        self.assertGreaterEqual(h.count("katex"), 1, "KaTeX 的 CDN 引用没了")
        # TeX 源码**就是**页面里可见的内容（KaTeX 没加载时看到的就是它）
        self.assertIn("$MC=\\frac{dTC}{dq}$".replace("$", ""), h)
        self.assertIn("throwOnError", h)

    def test_默认亮色_可切换(self):
        h = self._page()
        self.assertIn('id="theme"', h)
        self.assertIn("prefers-color-scheme:dark", h)
        self.assertIn(":root[data-theme=dark]", h)

    def test_图有可拖参数与预采样路径(self):
        h = self._page()
        m = re.search(r'data-opts="([^"]+)"', h)
        self.assertTrue(m, "滑动条的预采样路径没进页面")
        self.assertIn("data-opts", h)
        self.assertIn("<input type=\"range\"", h)
        self.assertIn('polyline class="c0" points="', h)

    def test_没有别的外链(self):
        """⚠️ 作者只允许"允许失败的 CDN" —— 除 KaTeX 外不许再有远端资源。"""
        h = self._page()
        urls = set(re.findall(r'(?:src|href)="(https?://[^"]+)"', h))
        self.assertTrue(all("katex" in u for u in urls), urls)


class HtmlPlots(unittest.TestCase):
    def test_三个_kind_都画得出(self):
        for kind in nv.PLOT_KINDS:
            svg = nh.svg(kind, {}, pid="p")
            self.assertIn("<svg", svg, kind)
            self.assertIn("polyline", svg, kind)

    def test_min_ac_点画在_q_等于_6(self):
        """⭐ 判据指向**几何**（不是"函数被调用了"）：$q=-b/2a=6$ 处那个点得落在图上。"""
        series = nh.curves("cost_curves", {"a": 1, "b": -12, "c": 60})
        marker = [pts for idx, pts in series if idx == 2][0]
        self.assertEqual(len(marker), 1)
        q, ac = marker[0]
        self.assertAlmostEqual(q, 6.0, places=6)
        self.assertAlmostEqual(ac, 24.0, places=6)

    def test_三次函数驻点在_x_等于_正负1(self):
        series = nh.curves("function_cubic", {"a": 1, "b": 0, "c": -3, "d": 0})
        pts = sorted(x for idx, ps in series if idx == 1 for x, _y in ps)
        self.assertAlmostEqual(pts[0], -1.0, places=6)
        self.assertAlmostEqual(pts[1], 1.0, places=6)

    def test_供需均衡点(self):
        series = nh.curves("supply_demand", {"d0": 100, "d1": 2, "s0": 10, "s1": 1})
        marker = [ps for idx, ps in series if idx == 2][0]
        q, p = marker[0]
        self.assertAlmostEqual(q, 30.0, places=6)
        self.assertAlmostEqual(p, 35.0, places=6)

    def test_未知_kind_返回空(self):
        self.assertEqual(nh.svg("随便画的", {}), "")
        self.assertEqual(nh.curves("随便画的", {}), [])


if __name__ == "__main__":
    unittest.main()
