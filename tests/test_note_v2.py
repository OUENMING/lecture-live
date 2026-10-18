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
        self.assertIn("字数上限", seen["user"], "每块的长度上限（第七轮 H）没进 prompt")

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

    def test_chat_炸了要报出来_不许吞(self):
        """⚠️ 第六轮第 2 条：吞成 `{}` 会让上层以为"模型说一条都没覆盖" ——
        三节课全报「0/117 条落到节里」就是这么来的。"""
        def boom(s, u, m):
            raise RuntimeError("x")
        got = nv.align(REVIEW, chat=boom)
        self.assertIn("failed", got)
        self.assertIn("RuntimeError", got["failed"])

    def test_有骨架时不再要逐条回执(self):
        """⭐ 有骨架时只问两件事（引用了哪些课件、哪几条没讲到）—— 回执小到不会截断。

        ⚠️ 原来那版要模型把上百条要点**逐条抄一遍再配节**，输出超 2000 tokens
           被截断 → `JSONDecodeError` → `{}` → 覆盖度全 0。
        """
        seen: dict = {}

        def chat(s, u, m):
            seen["user"], seen["sys"] = u, s
            return {"materials_used": ["final.md"],
                    "not_covered": [{"i": 1, "why": "没找到"}],
                    "notes": "还行"}
        sk = [{"i": 1, "title": "T", "t0": "13:16:28", "t1": "13:26:35",
               "points": [{"ts": "13:17:46", "zh": "停产价格", "en": "", "terms": [],
                           "kind": "要点"}]}]
        got = nv.align(REVIEW, chat=chat, skeleton=sk)
        self.assertEqual(got["materials_used"], ["final.md"])
        self.assertEqual(got["missing"][0]["point"], "停产价格")
        self.assertNotIn('"covered"', seen["sys"], "有骨架时不该再要逐条 covered")
        self.assertIn("最多 10 条", seen["sys"])


class VerifyNumbers(unittest.TestCase):
    def test_挑出带等号的断言(self):
        claims = nv.numeric_claims({"sections": [SECTION]})
        texts = " ".join(c["text"] for c in claims)
        self.assertIn("AC=q^2-12q+60", texts, "带等号的定义没被挑出来")
        self.assertIn("令 $MC=AC$，得 $2q^2=12q$", texts, "变式的推导步骤没被挑出来")
        self.assertNotIn("先说直觉", texts, "没有数字的段落不该占核对的额度")

    def test_重算不一致标_mismatch(self):
        """讲解里的断言（🎙/📖）**仍然**可以判 mismatch —— 收窄不许收过头。"""
        rev = {"sections": [SECTION]}
        claims = nv.numeric_claims(rev)
        idx = next(i for i, c in enumerate(claims) if c["origin"] == "prose")

        def chat(s, u, m):
            return {"items": [{"i": idx, "status": "mismatch", "recomputed": "12",
                               "note": "代入 q=4 应为 12"}]}
        got = nv.verify_numbers(rev, chat=chat)
        row = next(r for r in got if r["claim"] == claims[idx]["text"][:160])
        self.assertEqual(row["status"], "mismatch")
        self.assertEqual(row["recomputed"], "12")
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
        # ⭐ 作者 2026-10-18 拍板：今晚 / +3 天 / +7 天（日期从**上课那天**算）。
        self.assertEqual(nv.review_dates("2026-10-15"),
                         ["2026-10-15", "2026-10-18", "2026-10-22"])

    def test_日期坏了返回空(self):
        self.assertEqual(nv.review_dates("不是日期"), [])

    def test_三档标签与动作(self):
        rows = nv.schedule_rows(["2026-10-15", "2026-10-18", "2026-10-22"])
        self.assertEqual([r["label"] for r in rows], ["今晚", "+3 天", "+7 天"])
        self.assertEqual(rows[0]["date"], "2026-10-15")
        self.assertTrue(all(r["task"] for r in rows), rows)
        self.assertEqual(nv.schedule_rows([]), [])


class Overview(unittest.TestCase):
    """速览的生成侧（全替身 chat，零网络）—— 第七轮，作者 2026-10-18（替代五分钟串讲）。"""

    def _rev(self):
        return {"sections": [dict(SECTION), dict(SECTION, heading="第二节")]}

    def test_输出字段被收进来(self):
        def chat(s, u, mt):
            self.assertIn("第 2 单元", u)          # 输入用的是合并后的概念单元
            self.assertNotIn("先说直觉", u, "速览输入里不该有讲解全文")
            self.assertIn("不超过", u)             # 上限写进了用户消息
            return {"summary": ["先讲了这个，再把它用到那个上，两条线在这里接上。"],
                    "self_check": "yes"}
        got = nv.overview(self._rev(), chat=chat, max_chars=200)
        self.assertEqual(got["summary"], ["先讲了这个，再把它用到那个上，两条线在这里接上。"])
        self.assertEqual(got["self_check"], "yes")

    def test_截断当失败(self):
        """⭐ `finish_reason=="length"` → 整段不要（半截叙述比没有更坏）。"""
        def cm(s, u, mt):
            return {"paragraphs": ["半截……"]}, "length"
        self.assertEqual(nv.overview(self._rev(), chat=lambda *a: {}, chat_meta=cm), {})

    def test_没小节不调用(self):
        def boom(*a):
            raise AssertionError("不该调用")
        self.assertEqual(nv.overview({}, chat=boom), {})

    def test_调用炸了返回空(self):
        def boom(s, u, mt):
            raise RuntimeError("网络炸了")
        self.assertEqual(nv.overview(self._rev(), chat=boom), {})


class ConceptUnits(unittest.TestCase):
    """概念单元（第七轮 A）：相邻同主干的小节并成一个单元；单元带 minutes 与 ts 区间。"""

    def _sec(self, ts, heading, **kw):
        return {"ts": ts, "heading": heading, "prose": [f"{heading}·讲解。"],
                "q": f"{heading}·问？", "example": {"prompt": f"{heading}·题",
                                                   "steps": [], "answer": "答"},
                "pitfalls": ["坑"], "terms": [{"en": "x", "zh": "某"}], **kw}

    def test_相邻同主干并成一个单元(self):
        rev = {"sections": [
            self._sec("13:16", "[13:16] 最优化练习与停业价格：计算平均成本"),
            self._sec("13:18", "[13:18] 最优化练习与停业价格：三次函数练习",
                      terms=[{"en": "y", "zh": "另"}]),
            self._sec("13:20", "[13:20] 平均成本、边际成本及其相互作用：相互位置"),
            self._sec("13:22", "[13:22] 平均成本、边际成本及其相互作用：交点"),
        ]}
        got = nv.merge_units(rev, end_ts="13:24")
        heads = [s["heading"] for s in got["sections"]]
        self.assertEqual(heads, ["最优化练习与停业价格", "平均成本、边际成本及其相互作用"])
        a = got["sections"][0]
        self.assertEqual(a["ts"], "13:16–13:18")
        self.assertEqual(a["minutes"], 4.0)          # 13:16→13:18 + 13:18→13:20
        self.assertEqual(a["prose"], ["[13:16] 最优化练习与停业价格：计算平均成本·讲解。",
                                      "[13:18] 最优化练习与停业价格：三次函数练习·讲解。"])
        self.assertEqual(a["example"]["prompt"], "[13:16] 最优化练习与停业价格：计算平均成本·题",
                         "重复例子只留第一个")
        self.assertEqual(len(a["terms"]), 2)         # 各自去重后并起来
        self.assertEqual(got["minutes"], 8.0)

    def test_只并相邻的(self):
        rev = {"sections": [
            self._sec("13:16", "[13:16] A：一"),
            self._sec("13:18", "[13:18] B：二"),
            self._sec("13:20", "[13:20] A：三"),
        ]}
        got = nv.merge_units(rev, end_ts="13:22")
        self.assertEqual(len(got["sections"]), 3, "中间的 B 把两段 A 隔开了，不许跨着并")
        self.assertEqual([s["heading"] for s in got["sections"]],
                         ["[13:16] A：一", "[13:18] B：二", "[13:20] A：三"],
                         "单成员单元保持原标题")

    def test_双前缀也认(self):
        """防御：模型自己在标题里写了 `[13:16]`（`merge_blocks` 还会再加一层）。"""
        rev = {"sections": [
            self._sec("13:16", "[13:16] [13:16] 最优化练习与停业价格：一"),
            self._sec("13:18", "[13:18] [13:18] 最优化练习与停业价格：二"),
        ]}
        got = nv.merge_units(rev, end_ts="13:20")
        self.assertEqual(len(got["sections"]), 1)
        self.assertEqual(got["sections"][0]["heading"], "最优化练习与停业价格")

    def test_没有_sections_原样返回(self):
        rev = {"qa": [{"q": "x", "a": "y"}]}
        self.assertEqual(nv.merge_units(rev), rev)

    def test_最后一节按课末算分钟(self):
        rev = {"sections": [self._sec("13:16", "[13:16] A：一"),
                            self._sec("13:20", "[13:20] B：二")]}
        got = nv.merge_units(rev, end_ts="13:26")
        self.assertEqual(got["sections"][1]["minutes"], 6.0)


class ChunkBudget(unittest.TestCase):
    """每块的长度上限（第七轮 H）：≈240 CJK 字/分钟，写进提示词。"""

    def test_上限按分钟算(self):
        chunk = ["[13:16:00] a", "[13:16:30] b", "[13:20:00] c"]     # 4 分钟
        m = re.search(r"至多 (\d+) 字", nv.chunk_budget_line(chunk))
        self.assertTrue(m, "上限没写进提示行")
        n = int(m.group(1))
        self.assertGreaterEqual(n, 960)      # ≥ 240×4
        self.assertLessEqual(n, 1200)        # ≤ 240×5（含半分钟余量）

    def test_没有时间戳给保守值(self):
        m = re.search(r"至多 (\d+) 字", nv.chunk_budget_line(["没有时间戳的行"]))
        self.assertEqual(int(m.group(1)), 720)      # 默认按 3 分钟


class ShortenOverlimit(unittest.TestCase):
    """超限单元压缩（第七轮 H）：> 上限 ×1.15 才发一次调用；失败/变不短都不动原文。"""

    def _rev(self, cjk: int, minutes=1.0):
        return {"sections": [{"ts": "13:16", "heading": "H",
                              "prose": ["字" * cjk], "minutes": minutes}]}

    def test_超限才发调用(self):
        calls = []

        def chat(s, u, mt):
            calls.append(u)
            return {"prose": ["短"]}
        got, _, warns = nv.shorten_overlimit(self._rev(1000), chat=chat)
        self.assertEqual(len(calls), 1)
        self.assertIn("上限是 240 字", calls[0])
        self.assertEqual(got["sections"][0]["prose"], ["短"])
        self.assertTrue(warns and "1 个单元" in warns[0], "调用数要记账")

    def test_没超限不发(self):
        calls = []

        def chat(s, u, mt):
            calls.append(u)
            return {"prose": ["短"]}
        got, _, warns = nv.shorten_overlimit(self._rev(200), chat=chat)
        self.assertEqual(calls, [], "没超限不许发调用")
        self.assertEqual(got["sections"][0]["prose"], ["字" * 200])
        self.assertEqual(warns, [])

    def test_变不短就不动(self):
        def chat(s, u, mt):
            return {"prose": ["更长的" * 500]}
        got, _, warns = nv.shorten_overlimit(self._rev(1000), chat=chat)
        self.assertEqual(got["sections"][0]["prose"], ["字" * 1000])
        self.assertEqual(warns, [])

    def test_调用炸了_fail_soft(self):
        def boom(s, u, mt):
            raise RuntimeError("炸")
        got, _, warns = nv.shorten_overlimit(self._rev(1000), chat=boom)
        self.assertEqual(got["sections"][0]["prose"], ["字" * 1000])
        self.assertEqual(warns, [])

    def test_没有_minutes_跳过(self):
        def boom(*a):
            raise AssertionError("不该调用")
        rev = {"sections": [{"heading": "H", "prose": ["字" * 5000]}]}
        got, _, warns = nv.shorten_overlimit(rev, chat=boom)
        self.assertEqual(warns, [])


class AnnounceTerms(unittest.TestCase):
    """术语就地括注（第七轮 F）：首次出现 → 「中文（English）」；HTML 与 md 共用这一份。"""

    def test_首次出现才括注(self):
        terms, seen = [("边际成本", "marginal cost")], set()
        a = nv.announce_terms("先讲边际成本，再讲边际成本。", terms, seen)
        self.assertEqual(a, "先讲边际成本（marginal cost），再讲边际成本。")
        b = nv.announce_terms("后面又见边际成本。", terms, seen)
        self.assertEqual(b, "后面又见边际成本。")

    def test_已经括注的不重复加(self):
        terms, seen = [("边际成本", "marginal cost")], set()
        t = "边际成本（marginal cost）是定义。"
        self.assertEqual(nv.announce_terms(t, terms, seen), t)
        self.assertIn(terms[0], seen)

    def test_in_review_首次落在正文_台账不碰_原_dict不动(self):
        rev = {"sections": [{"heading": "边际成本", "prose": ["先说边际成本。"],
                             "q": "边际成本怎么算？",
                             "terms": [{"en": "marginal cost", "zh": "边际成本"}]}],
               "cover": {"rows": [{"title": "边际成本"}]}}
        got = nv.announce_terms_in_review(rev)
        self.assertEqual(got["sections"][0]["prose"][0], "先说边际成本（marginal cost）。")
        self.assertEqual(got["sections"][0]["heading"], "边际成本",
                         "标题保持干净（目录/地图里会重复出现）")
        self.assertEqual(got["sections"][0]["q"], "边际成本怎么算？", "只标首次")
        self.assertEqual(got["cover"]["rows"][0]["title"], "边际成本", "台账不碰")
        self.assertEqual(rev["sections"][0]["prose"][0], "先说边际成本。", "原 dict 不许动")

    def test_速览先走(self):
        """walk 顺序 = 阅读顺序：页首速览先拿首次括注，小节里后出现的保持干净。"""
        rev = {"overview_long": ["先看边际成本。"],
               "sections": [{"prose": ["再说边际成本。"],
                             "terms": [{"en": "marginal cost", "zh": "边际成本"}]}]}
        got = nv.announce_terms_in_review(rev)
        self.assertEqual(got["overview_long"][0], "先看边际成本（marginal cost）。")
        self.assertEqual(got["sections"][0]["prose"][0], "再说边际成本。")

    def test_2元组形状也认(self):
        rev = {"sections": [{"prose": ["先说边际成本。"],
                             "terms": [["边际成本", "marginal cost"]]}]}
        got = nv.announce_terms_in_review(rev)
        self.assertIn("边际成本（marginal cost）", got["sections"][0]["prose"][0])

    def test_没术语就原样(self):
        rev = {"sections": [{"prose": ["随便说点什么。"]}]}
        self.assertEqual(nv.announce_terms_in_review(rev), rev)


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

    def test_结构顺序_骨架先于细节(self):
        """简版里该有的四块按序出现；详细版那一行在**最前面**（它是入口）。"""
        rev = dict(REVIEW, cover={"rows": [{"i": 1, "title": "章", "t0": "13:16", "t1": "13:20",
                                            "sections": [], "points": [], "ok": True}],
                                  "n_ok": 1, "n_chapters": 1, "n_points": 0, "n_covered": 0,
                                  "missing": []})
        d = self._data(review=rev, html_name="x.html",
                       chapter_items={"windows": [], "deadlines": [], "atoms": [],
                                      "chapters": [{"status": "final", "id": 0,
                                                    "title_zh": "成本函数", "t0": "13:16:28",
                                                    "t1": "13:20:00"}]})
        out = nr.render(d)
        i_quick = out.index("## 0 · 速览")
        i_kc = out.index("## 📑 课堂纲要")
        i_term = out.index("## 本课术语")
        i_misc = out.index("## 一些事儿")
        self.assertLess(out.index("详细版"), i_quick, "入口要在最前面")
        self.assertLess(i_quick, i_kc)
        self.assertLess(i_kc, i_term)
        self.assertLess(i_term, i_misc)

    def test_详细版里那几层一件不少(self):
        """⭐ 作者第六轮第 6 条：md 变简版，**丢掉的那几层必须在 HTML 里**。

        ⚠️ 换渲染层最容易出的事故是"两头都丢"（md 不印了、HTML 也没加）——
           逐层点名（讲解 / 例题 / 变式 / 补全题 / 自测 / 图表 / 修正记录 /
           ❓ 标的地方 / ⭐ / 🎯 / 逐句转录）。
        """
        sec = dict(REVIEW["sections"][0])
        sec["plot"] = {"kind": "cost_curves", "title": "AC 与 MC", "params": {}}
        d = self._data(review=dict(REVIEW, sections=[sec]),
                       lost_items=["> [!note]- 没听懂\n> 这里为什么能约掉"],
                       keypoint_items=[(0.9, "要记结论", [0])],
                       corrections_extra=[{"kind": "check", "where": "三道核",
                                           "spoken": "x", "fixed": "y", "basis": "z"}])
        d.entries = [dict(e, star=(i == 0)) for i, e in enumerate(d.entries)]
        md, h = nr.render(d), nh.note_page(d)
        for want in ("平均成本的定义就是摊到每一单位",   # 例题步骤的「为什么」
                     "AC 与 MC",                        # 图
                     "修正记录", "我标的地方", "没听懂",
                     "⭐ 我标记的重点", "🎯 这节课最值得记的几句", "要记结论",
                     "完整逐句转录", "先答再揭晓"):
            self.assertIn(want, h, f"详细版缺了：{want}")
        self.assertIn("sentence about cost 0", h, "逐句转录要原样在详细版里（一句不删）")

    def test_中文占比达标(self):
        out = nr.render(self._data())
        self.assertGreater(nr.chinese_ratio(out), 0.6,
                           "作者验收表：正文中文占比 ≥60%")

    def test_中文占比的尺子_不把转录算进正文(self):
        """⭐ 量具自身的判据（2026-10-17 实测踩到）：版式把转录那一段改了名，
        `chinese_ratio` 的旧锚点失效 → 345 句英文被算进"正文"，71% 掉到 34%。

        ⚠️ 第六轮起转录搬去了 HTML，但这条尺子仍然要认得那个抬头（详细版/旧产物
           里那份转录还会从这里路过）—— 把转录换成纯英文长句，占比**不该受它影响**。
        """
        out = nr.render(self._data(entries=[dict(e) for e in entries(6)]))
        e2 = [{"ts": f"10:0{i}:00", "en": "english english english english " * 8,
               "zh": "", "asr": "", "star": False} for i in range(6)]
        self.assertAlmostEqual(nr.chinese_ratio(out),
                               nr.chinese_ratio(nr.render(self._data(entries=e2))),
                               places=6)

    def test_没有逐句双语对照(self):
        """作者的原话：不要「一行中文一行英文」。简版正文里更不该有。"""
        body = nr.render(self._data())
        self.assertNotIn("**EN**:", body, "正文里混进了逐句英文")
        self.assertNotIn("**ASR**:", body)
        self.assertNotIn("EN", body.split("## 一些事儿")[0].replace("ENGLISH", ""))

    def test_例题在详细版里_每步带为什么(self):
        h = nh.note_page(self._data())
        self.assertIn("平均成本的定义就是摊到每一单位", h)
        self.assertIn("变式", h)
        self.assertTrue("2q-12" in h, "补全题的空也在详细版里")

    def test_自测_题干在简版_答案折叠在简版(self):
        """⭐ 版式 A（作者 2026-10-18）：简版列 3–5 题**带折叠答案**（Obsidian callout `-`）。
        ⚠️ 折叠≠裸文本：答案只许出现在 `> [!question]- 答案` 的引用行里（点一下才展开），
        否则就是"题还没做就看到了"；完整题库与逐步解析仍在详细版。"""
        md = nr.render(self._data())
        self.assertIn("复习速测（先想再看）", md)
        self.assertIn("为什么 $MC<AC$ 时 $AC$ 会下降？", md)
        self.assertIn("> [!question]- 答案", md)
        ans = "低于平均的数掺进来会把平均拉低。"
        j = md.index(ans)
        self.assertEqual(md[j - 2:j], "> ", "答案没在折叠 callout 里（会剧透）")
        self.assertIn("自测共 1 题", md)
        h = nh.note_page(self._data())
        self.assertIn(ans, h)
        self.assertIn("先答再揭晓", h)

    def test_覆盖度与三道核清单在详细版(self):
        h = nh.note_page(self._data())
        self.assertTrue("边际成本的定义与计算" in h, "退化档的覆盖度表（教授知识点）没进详细版")
        self.assertTrue("数字 / 公式三道核" in h)
        self.assertTrue("修正记录" in h)
        self.assertTrue("代入求导" in h, "修正记录必须写出依据（否则读者没法回源）")

    def test_速览用短段落而不是清单(self):
        """第七轮（作者）：速览 = 3–6 句短段落；逐节 gist 清单与「学完你应该会」清单都撤了。"""
        d = self._data()
        d.review = {"sections": [dict(SECTION, prose=["第一节讲的东西先是这个道理。"])],
                    "overview_long": ["这节课先说 A，再把 A 用到了 B 上。"],
                    "review_plan": ["重做例题（10 分钟）"]}
        out = nr.render(d)
        self.assertIn("这节课先说 A，再把 A 用到了 B 上。", out)
        self.assertNotIn("学完你应该会", out)
        self.assertFalse(any(ln.startswith("1. **") for ln in out.splitlines()),
                         "逐节 gist 清单该撤了")

    def test_三道核的_mismatch_措辞不同_它是发现不是口误(self):
        d = self._data(corrections_extra=[{"kind": "check", "where": "三道核",
                                           "spoken": "AC(2.89)=233",
                                           "fixed": "",
                                           "basis": "AC 单调递减，最低点不在 2.89"}])
        h = nh.note_page(d)
        self.assertTrue("⚠ 三道核" in h, "三道核的发现没进详细版")
        self.assertNotIn("「AC(2.89)=233」→", h, "三道核的发现被写成了「原话 → 修正」")
        md = nr.render(d)
        self.assertTrue("修正 1 处" in md, "简版要点出有几处修正（明细在详细版）")

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
        """转录（证据层）**原样**进详细版：不精修、不删句（md 简版里不再重复它）。"""
        e = entries(1)
        e[0]["en"] = "the origional en sentence"
        e[0]["asr"] = "the origional a s r"
        h = nh.note_page(self._data(entries=e))
        self.assertTrue("the origional en sentence" in h)
        self.assertTrue("the origional a s r" in h, "原始 ASR 也要留着")

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
        h = nh.note_page(d)
        block = h[h.index("数字 / 公式三道核"):]
        block = block[:block.index("</table>")]
        for row in block.split("<tr>")[2:]:
            cells = [c for c in row.split("<td>")[1:]]
            for c in cells[:2]:
                self.assertTrue(c.split("</td>")[0].strip(), f"表里有空单元格：{row[:80]!r}")
        self.assertNotIn("corrections_extra", h)
        self.assertTrue("修正记录" in h, "那三条还得有去处（详细版的修正记录）")

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
        head = md[:md.index("## 附录")]
        self.assertNotIn("不自洽", head, "md 页头还有内部诊断")
        self.assertTrue("生成核对记录（内部" in md.split("## 附录")[1], "内部记录该在页尾")

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

    def test_兜底不露_TeX_命令(self):
        """⭐ 第五轮自查（产物 H 的截图/HTML 扫描）：兜底里还露着 12 处反斜杠命令 ——
        `p = \\min AC`、`\\sqrt{\\frac{2}{3}}`、`AC(q) = \\underline{\\hspace{1cm}}`、
        `q^*=6`、`(q^n)'=nq^{n-1}`。逐条钉住（KaTeX 没加载时肉眼看到的就是这些）。"""
        import note_math
        pt = note_math.pretty_tex
        self.assertEqual(pt(r"p = \min AC"), "p = min AC")
        self.assertEqual(pt(r"q = \sqrt{\frac{2}{3}} \approx 0.816"), "q = √((2)/(3)) ≈ 0.816")
        self.assertEqual(pt(r"AC'(q) = \underline{\hspace{1cm}}"), "AC'(q) = ____")
        self.assertEqual(pt(r"\underline{\hspace{1cm}}"), "____")
        self.assertEqual(pt("q^*=6"), "q*=6")
        self.assertEqual(pt("(q^n)'=nq^{n-1}"), "(qⁿ)'=nqⁿ⁻¹")      # 单字母上标
        self.assertEqual(pt("x^{T}"), "xᵀ")                          # 转置
        self.assertEqual(pt("AC_{min}"), "ACₘᵢₙ")
        self.assertEqual(pt("x^12345"), "x¹²³⁴⁵")
        # ⚠️ 认不出的**绝不硬转**：`q^{q}` 若丢掉上标就成了 `qq`（读者当乘积）——
        #    与 `x^-1` 同口径：宁可露 TeX 源码，不许显示错的数学。
        self.assertEqual(pt("q^{q}"), "q^{q}")
        self.assertEqual(pt("x^-1"), "x^-1")

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


#: 第六轮第 1 条的 fixture：**ECON10770 厨房清洁博弈**（不是 10-15 那节课）。
#: 例题：成本 6 / 分担 3 / 价值 4 → 都打扫每人 4−3=1；
#: 变式：价值变 5（成本不变）→ 都打扫每人 **5−3=2** —— 实测被三道核按例题的
#: 4−3=1「修正」过，那正是这一条要根除的假修正。
GAME = {
    "sections": [{
        "heading": "厨房清洁博弈（kitchen cleaning game）", "ts": "12:11",
        "prose": ["教授说打扫总成本是 6，两个人分担则每人 3，于是搭便车的那个人多占 6-3=3。"],
        "example": {"title": "🎙 12:12 厨房清洁博弈",
                    "prompt": "打扫总成本 6，两人分担每人 3，价值 4",
                    "steps": [{"text": "都打扫时每人得4-3=1", "why": "要减掉自己那份成本"}],
                    "answer": "纳什均衡是（不打扫，不打扫）"},
        "variants": [{"q": "如果价值变为5，其他不变（成本 6，两人分担每人 3），均衡是什么？",
                      "steps": [{"text": "都打扫时每人得5-3=2", "why": "同样要减掉自己那份"}],
                      "a": "不打扫仍是优势策略"}],
        "faded": {"q": "补全：价值 4 时行参与者的收益是 ____ 减 3",
                  "blanks": ["4"], "a": "都打扫时每人 4-3=1"},
    }],
    "qa": [{"q": "搭便车是什么？", "a": "享受别人的成果；分摊下来每人 6-3=3 的成本。",
            "kind": "concept"}],
}


class VerifyNarrowing(unittest.TestCase):
    """第六轮第 1 条：三道核**只核来自 🎙/📖 的断言**，模型自己出的题最多标未核对。"""

    def _run(self, status_for):
        """替身 `chat`：按 `status_for(序号, 断言文本)` 给状态。返回（行, 发给模型的 user）。"""
        seen: dict = {}

        def chat(system, user, max_tokens):
            seen["user"] = user
            seen["system"] = system
            claims = nv.numeric_claims(GAME)
            return {"items": [{"i": i, "status": status_for(i, c["text"]),
                               "recomputed": "4-3=1", "note": "价值应为4非5"}
                              for i, c in enumerate(claims)]}
        return nv.verify_numbers(GAME, chat=chat), seen

    def test_每条断言带出处与自己那道题的题面(self):
        by = {c["text"]: c for c in nv.numeric_claims(GAME)}
        v = by["都打扫时每人得5-3=2"]
        self.assertEqual(v["origin"], "variant")
        self.assertIn("价值变为5", v["ctx"], "变式的上下文要是**它自己**的题面")
        self.assertNotIn("4-3", v["ctx"])
        self.assertEqual(by["都打扫时每人得4-3=1"]["origin"], "example")
        self.assertEqual(by["教授说打扫总成本是 6，两个人分担则每人 3，于是搭便车的那个人多占 6-3=3。"]
                         ["origin"], "prose")
        self.assertEqual(by["享受别人的成果；分摊下来每人 6-3=3 的成本。"]["origin"], "qa")

    def test_变式不会被例题参数改(self):
        rows, seen = self._run(lambda _i, _t: "mismatch")
        var = [r for r in rows if "5-3=2" in r["claim"]]
        self.assertTrue(var, "变式那一步应当进了核对表")
        self.assertTrue(all(r["status"] == "unchecked" for r in var),
                        f"变式不许出现「已修正」：{var}")
        # 题面也要一并送到模型眼前（它拿不到别的题的参数就串不了）
        line = [ln for ln in seen["user"].splitlines() if "5-3=2" in ln][0]
        self.assertIn("变式", line)
        self.assertIn("价值变为5", line)

    def test_例题解答也不会被改(self):
        rows, _ = self._run(lambda _i, _t: "mismatch")
        ex = [r for r in rows if "4-3=1" in r["claim"]]
        self.assertTrue(ex)
        self.assertTrue(all(r["status"] == "unchecked" for r in ex), ex)

    def test_自测答案也不会被改(self):
        rows, _ = self._run(lambda _i, _t: "mismatch")
        qa = [r for r in rows if "享受别人的成果" in r["claim"]]
        self.assertTrue(qa)
        self.assertTrue(all(r["status"] == "unchecked" for r in qa), qa)

    def test_讲解里的断言仍然可以判修正(self):
        """收窄的方向不能过头：教授口误/ASR 听错的那一类**必须**还能改。"""
        def status(i, txt):
            return "mismatch" if "6-3=3" in txt else "ok"
        rows, _ = self._run(status)
        prose = [r for r in rows if "教授说打扫总成本是 6" in r["claim"]]
        self.assertTrue(prose)
        self.assertTrue(all(r["status"] == "mismatch" for r in prose), prose)

    def test_查不到出处的断言按未核对(self):
        """兜底方向：查不到出处 ≠ 有资格改正文。"""
        rows = nv.cap_model_rows([{"claim": "某个别处都没有的断言 7-3=4", "status": "mismatch",
                                   "how": "", "recomputed": ""}], GAME)
        self.assertEqual(rows[0]["status"], "unchecked")
        self.assertIn("找不到", rows[0]["how"])

    def test_模型给自己出的题判通过也不算数(self):
        rows, _ = self._run(lambda _i, _t: "ok")
        var = [r for r in rows if "5-3=2" in r["claim"]]
        self.assertTrue(all(r["status"] == "unchecked" for r in var),
                        "模型对自己的解答自洽 → 判 ok 等于没核，要降成未核对")


if __name__ == "__main__":
    unittest.main()
