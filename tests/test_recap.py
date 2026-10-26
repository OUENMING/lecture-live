#!/usr/bin/env python3
"""课前回顾（`recap.py`）的判据 —— 生成 / 存读 / 行组成 / 今天那两行。

⭐ 这里钉着两条**行为纪律**（不是"有没有命中"）：
   · 「没有就不显示」：`card()` 里空的块必须是空列表（UI 据此整行不画）
   · 「不带数字」：`stars` 里任何带数字的句子都被挡下（模型与兜底档一视同仁）
⚠️ 全部离线：`chat` 是桩，存读走 tempdir，一个请求都不发。
"""
import datetime
import json
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import recap  # noqa: E402


def chap(title, t0="13:16:00", status="final", sents=()):
    return {"title_zh": title, "t0": t0, "status": status,
            "sentences": [{"zh": s} for s in sents]}


class Card(unittest.TestCase):
    def test_空数据什么都不显示(self):
        rows = recap.card({})
        for k in ("points", "stars", "open", "duties", "today"):
            self.assertEqual(rows[k], [], k)
        self.assertEqual(recap.card(None)["points"], [])

    def test_只有知识点时其余块是空(self):
        rows = recap.card({"points": [{"name": "停业价格", "explain": "价格低于它就停产"}]})
        self.assertEqual(rows["points"][0]["name"], "停业价格")
        self.assertEqual(rows["stars"], [])
        self.assertEqual(rows["today"], [])

    def test_不带数字的重点句被挡下(self):
        rows = recap.card({"stars": ["平均成本最低处，边际成本与它相等",
                                     "p=0.81 的那一条",
                                     "2026 年考过这个"]})
        self.assertEqual(rows["stars"], ["平均成本最低处，边际成本与它相等"])

    def test_空字符串不占行(self):
        rows = recap.card({"stars": ["", "   "], "open": ["", "真没懂的那句"],
                           "duties": [{"title": "", "type": "调课"},
                                      {"title": "本周三停课", "type": "调课"}]})
        self.assertEqual(rows["stars"], [])
        self.assertEqual(rows["open"], ["真没懂的那句"])
        self.assertEqual(len(rows["duties"]), 1)

    def test_条数上限(self):
        zh = "甲乙丙丁戊己庚辛壬"
        rows = recap.card({"points": [{"name": f"p{i}"} for i in range(20)],
                           "stars": [f"重点{zh[i]}" for i in range(9)]})
        self.assertEqual(len(rows["points"]), recap.POINTS_MAX)
        self.assertEqual(len(rows["stars"]), recap.STARS_MAX)

    def test_坏形状不炸(self):
        rows = recap.card({"points": ["不是 dict", None], "duties": [3, None]})
        self.assertEqual(rows["points"], [])
        self.assertEqual(rows["duties"], [])


class Build(unittest.TestCase):
    CHAPTERS = [chap("停业价格", sents=["价格低于这条线就该停产"]),
                chap("二阶导数检验", sents=["往上弯是谷底"]),
                chap("临时章", status="interim")]
    KEYS = [(0.81, "The marginal cost is what it costs to make one more unit.", [3]),
            (0.77, "At the minimum of average cost, marginal cost equals it.", [5]),
            (0.75, "The shutdown price is below 10 euros.", [9])]

    def _chat(self, system, user, max_tokens):
        self.assertIn("课前回顾", system)
        self.assertIn("停业价格", user)
        self.assertNotIn("临时章", user, "只有正式章该进骨架")
        self.assertLessEqual(len(user), recap.FEED_MAX_CHARS)
        return {"points": [{"name": "停业价格", "explain": "价格低到这条线以下就该停产。"},
                           {"name": "二阶导数检验", "explain": "曲线往上弯是谷底，往下弯是山顶。"}],
                "stars": ["边际成本就是多生产一件要多花的钱。",
                          "平均成本最低的地方，边际成本正好和它相等。",
                          "这条带数字 3 的不要", "第四条不要"]}

    def test_额外调用最多一次(self):
        """⭐ 任务书那条：「额外调用 ≤1 次」。**数调用**，不是"看着像一次"。"""
        n = {"calls": 0}

        def chat(system, user, max_tokens):
            n["calls"] += 1
            return {"points": [{"name": "停业价格", "explain": "x"}], "stars": []}

        recap.build(course="X", date="2026-10-15", chapters=self.CHAPTERS,
                    keypoints=self.KEYS, lost=[], duties=[], tail="", chat=chat)
        self.assertEqual(n["calls"], 1)

    def test_一问就出两样(self):
        data = recap.build(course="ECON10790", date="2026-10-15",
                           chapters=self.CHAPTERS, keypoints=self.KEYS,
                           lost=["13:42 那句没听懂：二阶导数的符号"],
                           duties=[{"title": "本周三停课", "type": "调课"}],
                           tail="these three are very difficult to process",
                           chat=self._chat, session="x.md")
        rows = recap.card(data)
        self.assertEqual([p["name"] for p in rows["points"]],
                         ["停业价格", "二阶导数检验"])
        self.assertEqual(len(rows["stars"]), 3,
                         "带数字那条掉了；其余三条没数字、在上限内")
        self.assertEqual(len(rows["open"]), 1)
        self.assertEqual(rows["duties"][0]["title"], "本周三停课")
        self.assertIn("very difficult", data["tail"])

    def test_没chat只存骨架(self):
        data = recap.build(course="X", date="2026-10-15", chapters=self.CHAPTERS,
                           keypoints=self.KEYS, lost=[], duties=[], tail="", chat=None)
        rows = recap.card(data)
        self.assertEqual(rows["points"], [], "没调用就没有解释")
        self.assertEqual(len(rows["stars"]), 2, "兜底档：Jev 里没数字的两条")

    def test_chat抛了不影响其余(self):
        def boom(*a, **k):
            raise RuntimeError("网络炸了")
        data = recap.build(course="X", date="2026-10-15", chapters=self.CHAPTERS,
                           keypoints=self.KEYS, lost=["❓ 一条"], duties=[],
                           tail="tail", chat=boom)
        self.assertEqual(data["points"], [])
        self.assertEqual(data["open"], ["❓ 一条"])
        self.assertEqual(data["tail"], "tail")

    def test_坏结构当没生成(self):
        data = recap.build(course="X", date="2026-10-15", chapters=self.CHAPTERS,
                           keypoints=[], lost=[], duties=[], tail="",
                           chat=lambda *a: "不是 dict")
        self.assertEqual(data["points"], [])

    def test_空骨架不调模型(self):
        called = {"n": 0}

        def chat(*a):
            called["n"] += 1
            return {}
        recap.build(course="X", date="2026-10-15", chapters=[], keypoints=[],
                    lost=[], duties=[], tail="", chat=chat)
        self.assertEqual(called["n"], 0, "没有可喂的章节就不该发请求")


class Store(unittest.TestCase):
    def test_存读往返(self):
        with tempfile.TemporaryDirectory() as d:
            data = {"course": "ECON10790", "date": "2026-10-15",
                    "points": [{"name": "停业价格", "explain": "x"}], "stars": ["a"]}
            self.assertTrue(recap.save("ECON10790", data, root=d))
            got = recap.load("ECON10790", root=d)
            self.assertEqual(got["points"], data["points"])
            self.assertEqual(got["stars"], ["a"])

    def test_没有文件当没有(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(recap.load("ECON10790", root=d), {})

    def test_坏JSON当没有(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "courses" / "X" / "recap.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("{ 这不是 json", encoding="utf-8")
            self.assertEqual(recap.load("X", root=d), {})

    def test_没课号不写(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(recap.save("", {"a": 1}, root=d))


class Duties(unittest.TestCase):
    def test_合并去重且新的在前(self):
        prev = [{"title": "分组周", "when": "10-20（周二）", "type": "其他"}]
        new = [{"title": "本周三停课", "when": "10-14（周三）", "type": "调课"},
               {"title": "分组周", "when": "10-20（周二）", "type": "其他"}]
        got = recap.merge_duties(prev, new)
        self.assertEqual([x["title"] for x in got], ["本周三停课", "分组周"])

    def test_条目转行(self):
        items = [
            {"action": "saved", "type": "调课", "title": "本周三停课", "date": "2026-10-14",
             "time": "", "reminders": "ok"},
            {"action": "pending", "type": "考试", "title": "期末", "date": "2026-12-18",
             "time": "09:00"},
            {"action": "ignored", "type": "其他", "title": "这条别出现", "date": ""},
        ]
        rows = recap.duties_from_items(items, day=datetime.date(2026, 10, 12))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["when"], "10-14（周三）")
        self.assertEqual(rows[1]["when"], "12-18（周五） 09:00")

    def test_没日期不写when(self):
        rows = recap.duties_from_items([{"action": "pending", "title": "x", "type": "其他"}])
        self.assertEqual(rows[0]["when"], "")


class 临近的一些事儿_按今天过滤(unittest.TestCase):
    """⭐ 作者 2026-10-17：「临近的一些事儿」**按今天过滤**（`recap.upcoming`）。

    防的是：旧条目跟着一节节传下去 —— 拍摄里出过「上一节 10-15，这一条 10-14」。
    三条规则：① 有日期的只在 `[今天, 今天+14 天]` 之内；② 没日期的留，但最多随课传
    `DUTIES_CARRY_MAX` 次；③ 不知道今天是哪天 → 日期一条都不过滤。
    """

    DAY = datetime.date(2026, 10, 16)          # 周五（上一节是 10-15 周四）

    @staticmethod
    def _row(title, date, **kw):
        return {"type": "调课", "title": title, "when": date, "date": date, **kw}

    def test_表驱动_边界日(self):
        rows = [
            ("昨天", "2026-10-15", False),
            ("今天", "2026-10-16", True),
            ("明天", "2026-10-17", True),
            ("第 14 天整", "2026-10-30", True),
            ("第 15 天", "2026-10-31", False),
        ]
        for label, d, want in rows:
            with self.subTest(label):
                got = recap.upcoming([self._row("这件事", d)], day=self.DAY)
                self.assertEqual(bool(got), want,
                                 f"{label}（{d}）：该{'显示' if want else '不显示'}")

    def test_表驱动_跨月(self):
        day = datetime.date(2026, 10, 28)      # 周三
        rows = [("+14 天（跨月）", "2026-11-11", True), ("+15 天（跨月）", "2026-11-12", False)]
        for label, d, want in rows:
            with self.subTest(label):
                got = recap.upcoming([self._row("这件事", d)], day=day)
                self.assertEqual(bool(got), want, f"{label}（{d}）")

    def test_表驱动_没日期(self):
        rows = [("没日期", {}, True),
                ("没日期·传过 1 次", {"carry": 1}, True),
                ("没日期·传满 2 次", {"carry": 2}, False),
                ("没日期·越界 9 次", {"carry": 9}, False),
                ("日期认不出（老数据）", {"date": "10-14（周三）"}, True)]
        for label, kw, want in rows:
            with self.subTest(label):
                got = recap.upcoming([{"type": "其他", "title": "这件事", **kw}], day=self.DAY)
                self.assertEqual(bool(got), want, label)

    def test_不知道今天是哪天就不过滤(self):
        """`day=None`（老调用方 / 判据没传）→ 日期一律放行（宁可多留，不要错杀）。"""
        rows = [self._row("过去的", "2020-01-01"), self._row("将来的", "2030-01-01")]
        self.assertEqual(len(recap.upcoming(rows, day=None)), 2)

    def test_有日期的条目不受_carry_上限(self):
        """⭐ DB 审查 D-17：`carry` 只该管**没日期**的 —— 有日期的由日期窗口限寿。
        窗口内的事项（10 天后的考试）不许因为随课传了几次就被丢。"""
        row = self._row("考试", "2026-10-26", carry=recap.DUTIES_CARRY_MAX + 5)
        self.assertTrue(recap.upcoming([row], day=self.DAY), "窗口内的有条目被 carry 丢了")

    def test_不知道今天时有日期的照旧受_carry_管(self):
        """规则 ③：不知道今天就没有日期窗口可依，只能靠 carry 限寿。"""
        row = self._row("考试", "2026-10-26", carry=recap.DUTIES_CARRY_MAX)
        self.assertEqual(recap.upcoming([row], day=None), [])

    def test_窗口内的考试随课传三节仍在(self):
        """端到端：10 天后的考试连并 3 节（`carry` 涨到 3）都还在。"""
        one = [self._row("考试", "2026-10-26")]
        two = recap.merge_duties(one, [], day=self.DAY)
        three = recap.merge_duties(two, [], day=self.DAY)
        self.assertEqual([x["title"] for x in three], ["考试"])

    def test_坏形状不炸(self):
        self.assertEqual(recap.upcoming(None, day=self.DAY), [])
        self.assertEqual(recap.upcoming([None, "x", 3], day=self.DAY), [])

    def test_合并时过去的条目不再传下去(self):
        prev = [self._row("过去的", "2026-10-14"), self._row("将来的", "2026-10-19")]
        got = recap.merge_duties(prev, [], day=self.DAY)
        self.assertEqual([x["title"] for x in got], ["将来的"])

    def test_合并时没日期的算随课传了一次(self):
        prev = [{"type": "其他", "title": "没日期的"}, self._row("有日期的", "2026-10-19")]
        got = {x["title"]: x for x in recap.merge_duties(prev, [], day=self.DAY)}
        self.assertEqual(got["没日期的"]["carry"], 1)
        self.assertEqual(got["有日期的"]["carry"], 1)

    def test_这节课新产生的不算传过(self):
        got = recap.merge_duties([], [self._row("新的", "2026-10-19")], day=self.DAY)
        self.assertEqual(int(got[0].get("carry") or 0), 0, "新条目不该从 carry=1 起")

    def test_没日期的传到第_3_节就丢(self):
        """⭐ 「最多留 2 节」的完整走一遍：记下 → 第 2 节留 → 第 3 节丢。"""
        one = [{"type": "其他", "title": "没日期的"}]
        two = recap.merge_duties(one, [], day=self.DAY)          # 第 2 节
        self.assertEqual(len(two), 1)
        three = recap.merge_duties(two, [], day=self.DAY)        # 第 3 节
        self.assertEqual(three, [], "没日期的条目随课传满 2 次就该丢")

    def test_渲染那层也过滤(self):
        """⭐ 作者点名的是「**开课渲染时**」—— `card()` 自己也要挡（存下来的可能已旧）。"""
        data = {"points": [{"name": "停业价格", "explain": "价格低于它就停产"}],
                "duties": [self._row("过去的", "2026-10-14"),
                           self._row("将来的", "2026-10-19")]}
        rows = recap.card(data, day=self.DAY)
        self.assertEqual([x["title"] for x in rows["duties"]], ["将来的"])
        self.assertEqual(len(recap.card(data)["duties"]), 2, "不传今天 = 不过滤（老调用方）")

    def test_上限还是三条(self):
        rows = [self._row(f"第{i}件", "2026-10-19") for i in range(9)]
        self.assertEqual(len(recap.card({"duties": rows}, day=self.DAY)["duties"]),
                         recap.DUTIES_MAX)


class 解释的说法要严谨(unittest.TestCase):
    """⭐ 作者 2026-10-17：知识点解释**必须符合理论、不下绝对话**。

    点名的是「极值只可能出现在驻点或边界上」——**不可导点（如 |x| 在 0 处）也是极值**，
    这句本身就不严谨（原型 §"说话要严谨"那一行，原来还把它当"应说"的写法教给了模型）。
    """

    ROOT = pathlib.Path(__file__).resolve().parent.parent

    def _src(self, rel: str) -> str:
        return (self.ROOT / rel).read_text(encoding="utf-8")

    def test_提示词给了严谨写法(self):
        self.assertIn("可导函数的极值点导数为零", recap._SYS)
        # 「不说绝对话」这条约束要真的在（反例必须标着"反例"，不能光把错句摆进去）
        self.assertIn("绝对话", recap._SYS)
        self.assertIn("宁可少说一句", recap._SYS)
        self.assertIn("反例", recap._SYS)

    def test_夹具不再出现那个说法(self):
        src = self._src("scripts/probe_duty_recap.py")
        self.assertNotIn("极值只可能出现在驻点或边界上", src)
        self.assertIn("可导函数的极值点导数为零（驻点）", src)


class Tail(unittest.TestCase):
    def test_取最后一句英文(self):
        entries = [{"en": "First sentence."}, {"en": "", "asr": "raw fallback"},
                   {"en": "The last sentence of the class."}]
        self.assertEqual(recap.tail_quote(entries), "The last sentence of the class.")

    def test_空课返回空(self):
        self.assertEqual(recap.tail_quote([]), "")
        self.assertEqual(recap.tail_quote([{"en": ""}]), "")


class TodayExtra(unittest.TestCase):
    def _write_material(self, root, course, name, text, *, age_days=0.0):
        md = pathlib.Path(root) / "courses" / course / "materials"
        md.mkdir(parents=True, exist_ok=True)
        p = md / name
        p.write_text(text, encoding="utf-8")
        if age_days:
            ts = (datetime.datetime.now()
                  - datetime.timedelta(days=age_days)).timestamp()
            os.utime(p, (ts, ts))
        return p

    def test_今天传了课件就给一行(self):
        """⭐ 作者 2026-10-17：这一块**只留课件那条**（砍掉原来的 ②）。"""
        with tempfile.TemporaryDirectory() as d:
            self._write_material(d, "ECON10790", "week6.md",
                                 "# Monopoly\n\n## Deadweight loss\n\n正文很长很长的一段话在这里。")
            data = {"date": "2026-10-15", "tail": "these three are difficult"}
            got = recap.today_extra("ECON10790", data, root=d)
            self.assertEqual(len(got["today"]), 1)
            self.assertIn("week6.md", got["today"][0])
            self.assertIn("Monopoly", got["today"][0])
            self.assertNotIn("these three", "".join(got["today"]))

    def test_没新课件就整块不出现(self):
        """⭐ 原型 B 态：没传课件 → `today` 那一行不出现（不报错、不提示缺东西）。"""
        with tempfile.TemporaryDirectory() as d:
            self._write_material(d, "ECON10790", "old.md", "# 老课件", age_days=9)
            # 上节课日期取「5 天前」：比 9 天前的老课件新、跟着真实今天走（写死日期会随时间烂掉）
            last = (datetime.date.today() - datetime.timedelta(days=5)).isoformat()
            data = {"date": last, "tail": "x"}
            got = recap.today_extra("ECON10790", data, root=d)
            self.assertEqual(got, {})
            self.assertEqual(recap.card(got), {"course": "", "date": "", "points": [],
                                               "stars": [], "open": [], "duties": [],
                                               "today": []})

    def test_没目录也不炸(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(recap.today_extra("没有这门课", {"date": "2026-10-15"}, root=d), {})

    def test_旧课件但晚于上节课也算新的(self):
        with tempfile.TemporaryDirectory() as d:
            self._write_material(d, "X", "ppt.md", "# 新的一页", age_days=0.0)
            got = recap.today_extra("X", {"date": "2026-10-15"}, root=d)
            self.assertIn("ppt.md", got["today"][0])

    def test_抽不出标题也有文件名(self):
        with tempfile.TemporaryDirectory() as d:
            self._write_material(d, "X", "binary.pptx", "…")   # 不是真 pptx
            got = recap.today_extra("X", {"date": "2026-10-15"}, root=d)
            self.assertIn("binary.pptx", got["today"][0])

    def test_merge_extra不写信源(self):
        data = {"course": "X", "date": "10-15"}
        out = recap.merge_extra(data, {"today": ["① x"]})
        self.assertEqual(out["today"], ["① x"])
        self.assertNotIn("today", data, "不许改原 dict")


if __name__ == "__main__":
    unittest.main()
