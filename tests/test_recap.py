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
            data = {"date": "2026-10-15", "tail": "x"}
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
