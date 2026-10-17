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

    def test_块失败要退一步_不许静默丢(self):
        """⭐ 第四轮 run F 的实测：带章节骨架后 4/5 块输出被截断 → 静默丢半节课。

        判据：① 第一次炸了要重试；② 重试还炸就**去掉骨架**再来一次（宁可按老版式写全）；
        ③ 真失败时记账（`chunk_stats`），全失败时仍然返回 `{}`。
        """
        calls = []

        def chat(system, user, max_tokens):
            calls.append((("骨架" in user), max_tokens))
            if "骨架" in user and len(calls) < 3:      # 前两次（带骨架）都炸
                raise ValueError("truncated JSON")
            return {"sections": [{"heading": "1. 甲", "ts": "13:20", "prose": ["p"]}]}

        got = nv.review(entries(3), chat=chat, skeleton=[{"i": 1, "title": "T",
                                                          "t0": "13:16:28", "t1": "13:26:35",
                                                          "points": [{"ts": "13:17", "zh": "要点甲"}]}])
        self.assertTrue(got.get("sections"), "兜底那一次该把内容写回来")
        self.assertEqual([x[0] for x in calls], [True, True, False],
                         "顺序该是：带骨架 → 带骨架加量 → 去掉骨架")
        self.assertEqual(got.get("chunk_stats", {}).get("failed"), [])

    def test_全失败时返回空_不装成功(self):
        def chat(system, user, max_tokens):
            raise RuntimeError("boom")

        self.assertEqual(nv.review(entries(3), chat=chat), {})
        got = nv.review(entries(3), chat=chat,
                        skeleton=[{"i": 1, "title": "T", "t0": "13:16:28", "t1": "13:26:35",
                                   "points": []}])
        self.assertEqual(got.get("sections"), None, "全失败不许有半份内容")

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

    def test_checks_里的_dict_断言被收拾成字符串(self):
        """⚠️ 2026-10-17 验收实测：模型把整个 dict 塞进 `claim`（那是 `steps` 的 schema），
        不收拾的话笔记的核定表里会出现 `{'text': 'f(1)=…', 'why': ''}` 这种字面量。"""
        rev = {"sections": [SECTION],
               "checks": [{"claim": {"text": "f(1)=1-6+9+1=5", "why": ""},
                           "status": "ok"}]}
        got = nv.verify_numbers(rev, chat=lambda s, u, m: {})
        claims = [c["claim"] for c in got]
        self.assertTrue(any("f(1)=1-6+9+1=5" in c for c in claims), claims)
        self.assertFalse(any("{'text'" in c or '{"text"' in c for c in claims), claims)

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

    def test_中文占比的尺子_不把转录算进正文(self):
        """⭐ 量具自身的判据（2026-10-17 实测踩到）：版式把转录那一段改了名，
        `chinese_ratio` 的旧锚点失效 → 345 句英文被算进"正文"，71% 掉到 34%。

        ⚠️ 这条**指向那一段抬头**：改名字要连量具一起改（否则验收表在说谎）。
        """
        out = nr.render(self._data(entries=[dict(e) for e in entries(6)]))
        body = nr.chinese_ratio.__wrapped__ if False else None
        del body
        self.assertNotIn("完整逐句转录", out.split("## 📜")[0])
        # 把转录换成纯英文长句：占比**不该受它影响**
        e2 = [{"ts": f"10:0{i}:00", "en": "english english english english " * 8,
               "zh": "", "asr": "", "star": False} for i in range(6)]
        self.assertAlmostEqual(nr.chinese_ratio(out), nr.chinese_ratio(nr.render(self._data(entries=e2))),
                               places=6)

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

    def test_速览里的句子在句末收(self):
        """实测踩到过：`prose[:80]` 把「令 $f'(x)=0$ 解」这种半句留在速览里。"""
        d = self._data()
        d.review = {"sections": [dict(SECTION, prose=[
            "第一节讲的东西先是这个道理，然后还有一个更长的解释，最后落到定义上。"])]}
        out = nr.render(d)
        gist = [ln for ln in out.splitlines() if ln.startswith("1. **")][0]
        self.assertTrue(gist.endswith("。"), gist)
        self.assertLess(len(gist), 120, gist)

    def test_三道核的_mismatch_措辞不同_它是发现不是口误(self):
        d = self._data(corrections_extra=[{"kind": "check", "where": "三道核",
                                           "spoken": "AC(2.89)=233",
                                           "fixed": "",
                                           "basis": "AC 单调递减，最低点不在 2.89"}])
        out = nr.render(d)
        self.assertIn("⚠ 三道核", out)
        self.assertNotIn("「AC(2.89)=233」→", out, "三道核的发现被写成了「原话 → 修正」")

    def test_头部写清课件核对与覆盖度(self):
        out = nr.render(self._data())
        self.assertIn("📖 课件 2 份 / 索引 9 段", out)
        self.assertIn("覆盖度：教授知识点 1 个 → 笔记覆盖 1 个", out)

    def test_没课件时头部明说(self):
        d = self._data(materials_line="📖 无课件材料",
                       warns=["本课**未做课件核对** —— 没找到这门课的课件材料"])
        out = nr.render(d)
        self.assertIn("未做课件核对", out)

    def test_精修那一行跟着_polish_字段走(self):
        """⭐ `--polish auto` 开着写"不再精修"就是假话（附录 A 那行是给读者的凭据）。"""
        self.assertIn("逐句转录不再精修", nr.render(self._data(polish_state="off")))
        for st in ("ok", "partial", "failed", "interrupted"):
            out = nr.render(self._data(polish_state=st))
            self.assertNotIn("逐句转录不再精修", out, st)
            self.assertIn("转录做过精修", out, st)

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
        # ⚠️ 第五轮自查（top-dark 截图）：系统暗色时页面已经暗了，按钮还写着「暗色」。
        self.assertIn("if(tbtn){ themeLabel();", h, "载入时没按生效主题摆正按钮标签")
        self.assertIn("tbtn.textContent=dark?", h)

    def test_图有可拖参数与预采样路径(self):
        h = self._page()
        m = re.search(r'data-opts="([^"]+)"', h)
        self.assertTrue(m, "滑动条的预采样路径没进页面")
        self.assertIn("data-opts", h)
        self.assertIn("<input type=\"range\"", h)
        self.assertIn('polyline class="c0" points="', h)

    def test_图有图例_刻度_人话滑杆(self):
        """⭐ 第四轮四条一起钉：① 每条曲线有色块图例（哪条是 AC 哪条是 MC）；
        ② 纵轴至少 3 个刻度；③ 滑杆是人话标签 + 拖动说明；④ 图题与卡片标题重复就不画。"""
        h = self._page()
        # ① 图例：两个色块 + 两条曲线名
        self.assertIn("AC 平均成本", h)
        self.assertIn("MC 边际成本", h)
        # ⚠️ 图例的**色块**：`<line ... stroke-width:2.5>`（曲线本身是 2.2，在 CSS 里）
        self.assertRegex(h, r'<line[^>]*stroke:var\(--c[0-9]\);stroke-width:2\.5',
                         "图例没有颜色对应（只有一行文字）")
        # ② 纵轴 5 个 + 横轴 5 个 = 10 个刻度
        self.assertGreaterEqual(h.count('class="tick"'), 10, "刻度太少（纵轴至少 3 个）")
        # ③ 滑杆：**标签那一处**是人话（只有 aria-label 换掉是挡不住学生的）
        m = re.search(r"<label>([^<]*)<b[^>]*>", h)
        self.assertTrue(m, "滑杆没有 label")
        self.assertIn("平均成本 AC 的一次项系数 b", m.group(1))
        self.assertIn("拖动看", h)
        # ④ 图题 == 卡片标题 -> 不重复
        self.assertTrue(nh.dup_caption("平均成本与边际成本曲线", "[13:29] 平均成本（Average Cost）"))
        self.assertFalse(nh.dup_caption("供需均衡", "[13:29] 平均成本"))

    def test_三道核表里不许有空单元格(self):
        """⚠️ 第四轮：`corrections_extra`（另一种形状）被当成核对行渲染 → 三行空单元格，
        还把"待确认"的计数抬高了 3。"""
        d = nr.NoteData(date="2026-10-15", course="ECON10790", entries=entries(2),
                        review=REVIEW, html_name="x.html",
                        corrections_extra=[{"kind": "check", "where": "三道核",
                                            "spoken": "x", "fixed": "y", "basis": "z"}])
        md = nr.render(d)
        block = md[md.index("数字 / 公式三道核"):]
        block = block[:block.index("\n## ") if "\n## " in block else len(block)]
        for line in block.splitlines():
            if line.startswith("| ") and "---" not in line and "断言" not in line:
                cells = [c.strip() for c in line.strip("|").split("|")]
                self.assertTrue(cells[0] and cells[1], f"表里有空单元格：{line!r}")
        self.assertNotIn("corrections_extra", md)
        self.assertIn("修正记录", md, "那三条还得有去处（附录 A）")

    def test_驻点画成圆点不是线段(self):
        """⚠️ 第四轮自查：`function_cubic` 的 1 号 series 是**两个点**（驻点），
        没声明成标记时会被当曲线连成一条直线（ex1 截图里那根 blue 假线）。"""
        s = nh.svg("function_cubic", {"a": 1, "b": 0, "c": -3, "d": 0}, pid="p1", caption="")
        self.assertEqual(len(re.findall(r"<polyline", s)), 1, "驻点被连成了曲线")
        self.assertGreaterEqual(len(re.findall(r'<circle class="dot"', s)), 3,
                                "两个驻点 + 图例圆点")
        s2 = nh.svg("cost_curves", {"a": 1, "b": -12, "c": 60}, pid="p2", caption="")
        self.assertEqual(len(re.findall(r"<polyline", s2)), 2, "AC/MC 两条曲线")

    def test_核对清单在_HTML_里有个可点的锚点(self):
        """⚠️ 页头那行写着「（点开看）」，可 HTML 里原来根本没有 `#checks`（死链）。"""
        d = nr.NoteData(date="2026-10-15", course="ECON10790", entries=entries(2),
                        review=REVIEW, html_name="x.html")
        h = nh.note_page(d)
        self.assertIn('href="#checks"', h)
        self.assertIn('id="checks"', h)
        detail = h[h.index('id="checks"'):]
        self.assertIn("数字 / 公式三道核", detail)
        # 章节覆盖也在这里（有 cover 时）
        d2 = nr.NoteData(date="2026-10-15", course="ECON10790", entries=entries(2),
                         review=dict(REVIEW, cover={"rows": [{"i": 1, "title": "T",
                             "t0": "13:16:28", "t1": "13:26:35", "sections": [],
                             "points": [{"gi": 1, "ts": "13:17", "point": "停产价格", "ok": False}],
                             "ok": False, "why": ""}], "n_chapters": 1, "n_ok": 0,
                             "n_points": 1, "n_covered": 0, "missing": []}),
                         html_name="x.html")
        h2 = nh.note_page(d2)
        self.assertIn("章节覆盖", h2)
        self.assertIn("还没落到的要点", h2)

    def test_页头只留给学生的一行_内部记录在页尾(self):
        """⚠️ 作者第三轮：页头给学生看的是「数字核对：N 处已核 · M 处待你确认」，
        生成器的活日志（改了哪条公式 / 删了几句猜测）挪到页尾折叠里。"""
        d = nr.NoteData(date="2026-10-15", course="ECON10790", entries=entries(2),
                        review=REVIEW, html_name="x.html",
                        warns=["MC 与 TC 不自洽（AC(q) → 3q^2-24q+60）"])
        h = nh.note_page(d)
        head = h[:h.index('id="kp1"')]
        self.assertIn("处已核", head)
        self.assertIn("待你确认", head)
        self.assertNotIn("不自洽", head, "内部诊断还在页头")
        self.assertIn('class="internal"', h)
        self.assertGreater(h.index('class="internal"'), h.index('id="kp1"'), "内部记录该在页尾")
        md = nr.render(d)
        self.assertNotIn("不自洽", md.split("## 1.")[0], "md 页头还有内部诊断")
        self.assertIn("生成核对记录（内部", md)

    def test_图只有一份且放在它的知识点里(self):
        """⚠️ 作者第三轮：图表区出现 5 张一模一样的 AC/MC 曲线，而且全堆在末尾。"""
        sec = dict(REVIEW["sections"][0])
        sec["plot"] = {"kind": "function_cubic", "title": "f(x)=x^{3}-3x 的图像",
                       "params": {"a": 1, "b": 0, "c": -3, "d": 0}}
        sec2 = dict(sec, heading="2. 别的知识点")
        d = nr.NoteData(date="2026-10-15", course="ECON10790", entries=entries(2),
                        review={"title_zh": "t", "sections": [sec, sec2]}, html_name="x.html")
        h = nh.note_page(d)
        self.assertEqual(h.count('<figure class="fig">'), 1, "重复的图没去掉")
        self.assertLess(h.index('<figure class="fig">'), h.index('id="kp2"'),
                        "图没放在它自己的知识点里")
        self.assertNotIn("<h2>图</h2>", h, "末尾那个图表区还在")

    def test_兜底文字不露_Tex_上标(self):
        """KaTeX 没加载时肉眼看到的就是 `.tex` 的内容 —— 不许露 `^{}`（作者第三轮：截图抓到「f(x)=x^{3}-3x 的图像」）。"""
        h = self._page()
        spans = re.findall(r'data-tex="([^"]{1,160})">([^<]{0,160})<', h)
        self.assertTrue(spans, "页面上一个 `.tex` 兜底都没有")
        for tex, vis in spans:
            self.assertNotIn("^", vis, f"兜底文字里还在露 TeX 上标：{vis!r}（源 {tex!r}）")
            self.assertNotRegex(vis, r"[{}]", f"兜底文字里还有 TeX 花括号：{vis!r}（源 {tex!r}）")
        # 转换函数本身（页面里所有 `.tex` 的内容都走它）
        import note_math
        self.assertEqual(note_math.pretty_tex("x^{3}-3x"), "x³-3x")
        # ⚠️⚠️ 第四轮（作者原话：「把指数吃错」）：`Q³⁻¹2Q²⁺⁶0Q` / `Q²⁻¹2Q+60` ——
        #    上标只许吃**紧跟 `^` 的那一个 token**（纯数字或 `{…}`），其余留在基线。
        self.assertEqual(note_math.pretty_tex("TC(Q)=Q^3-12Q^2+60Q"), "TC(Q)=Q³-12Q²+60Q")
        self.assertEqual(note_math.pretty_tex("AC(Q)=Q^2-12Q+60"), "AC(Q)=Q²-12Q+60")
        # 拿不准的（`^-1` 没花括号）宁可原样露 TeX 源码，也不许写成错的数学
        self.assertEqual(note_math.pretty_tex("x^-1"), "x^-1")
        self.assertEqual(note_math.pretty_tex("x^{-1}"), "x⁻¹")
        self.assertEqual(note_math.pretty_tex("q_{1} + 3q_{2}"), "q₁ + 3q₂")
        self.assertEqual(note_math.pretty_tex("f(x)=x^{3}-3x"), "f(x)=x³-3x")
        # ⚠️ `3x^2-3` 曾被上标正则写成 `3x²⁻³`（把后面的减号当成了指数的符号）
        self.assertEqual(note_math.pretty_tex("3x^2-3"), "3x²-3")
        self.assertEqual(note_math.pretty_tex("x^{-1}"), "x⁻¹")
        self.assertEqual(note_math.pretty_tex(r"x=\pm 1"), "x=± 1")

    def test_撇号与_pm_的兜底只转义一次(self):
        """⚠️ 第三轮截图抓到：兜底文字被 escape 两次 —— 页面上显示字面量 `&#x27;`、`&lt;`。

        根因：整段先 escape 再抽 `$…$`，TeX 源码里的撇号在抽取时已经成了实体，
        兜底又 escape 了一遍。现在公式段拿的是**原样 TeX**，只转义一次。
        """
        h = nh.inline("一阶导数 $f'(x)=3x^2-3$，驻点为 $x=\\pm 1$。")
        self.assertNotIn("&amp;", h, "兜底文字被转了两次")
        self.assertIn("data-tex=\"f&#x27;(x)=3x^2-3\"", h, "属性里应当是原样 TeX 的**单次**转义")
        self.assertIn('>f&#x27;(x)=3x²-3<', h, "兜底应当是 f'(x)=3x²-3（渲染后）")
        self.assertIn(">x=± 1<", h)
        self.assertNotIn("^{", h, "兜底文字里露了 TeX 上标")

    def test_没_JS_也看得见正文(self):
        """⚠️ 入场动画不许把正文藏起来 —— 模块纪律写着"不用 JS 也能读"。

        实测两处疼：① 截图工具滚过 `#kp1` 后，上面几张卡片的 IntersectionObserver
        还没触发 → `opacity:0` → 半屏空白；② 用户禁用 JS / 打印时同理。
        做法：藏内容的那条规则挂在 `html.js` 下（`<head>` 里一行内联脚本加这个类），
        没有 JS 就没有 `.js`，`.reveal` 默认可见。
        """
        h = self._page()
        self.assertIn('document.documentElement.className="js"', h)
        self.assertIn(".reveal{opacity:1;transform:none}", h, "默认可见那条没了")
        self.assertIn("html.js .reveal{opacity:0", h)
        self.assertIn("html.js .reveal{opacity:1!important", h, "打印/降低动效的兜底没了")

    def test_每张图的标题都在自己的_figure_里(self):
        """⚠️ 2026-10-17 自查抓到的版式 bug：标题原来是 figure 的**兄弟节点**，
        而 `.plots` 是 grid → 标题被排成单独一格，三张图散成一团（截图里看得很清楚）。"""
        h = self._page()
        plots = re.search(r'<div class="plots">(.*?)</div>\s*</section>', h, re.S)
        # 上面那条匹配不一定命中（结构可能变），所以判据落在"页面里"这一层：
        seg = plots.group(1) if plots else h
        self.assertNotIn("</figure><figcaption>", seg,
                         "又出现了裸 figcaption（会被 grid 排成独立一格）")
        figs = re.findall(r'<figure class="fig">(.*?)</figure>', seg, re.S)
        self.assertTrue(figs, "没找到任何图")
        for f in figs:
            self.assertEqual(f.count("<figcaption"), 1, "一张图应当只有一个标题")

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
