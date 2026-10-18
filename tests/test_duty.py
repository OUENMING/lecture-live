#!/usr/bin/env python3
"""「一些事儿」数据层的判据（`duty.py`）—— 识别 / 类型 / 决定 / 提醒事项（注入的假 runner）。

⚠️ 纪律：**一个字节都不进真「提醒事项」**。osascript 的 runner 是注入的；
   旁路文件与备份全部写在 tempdir 里。
"""
import datetime
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import duty  # noqa: E402

MON = datetime.date(2026, 10, 12)      # 周一（SOC10020 那节）


def payload(quote, *, t="13:52:56", src=(101,), source="regex", changed=False):
    return {"kind": "deadline", "t": t, "quote": quote, "src": list(src),
            "source": source, "changed": changed}


class Classify(unittest.TestCase):
    def test_四类(self):
        cases = [
            ("There is no class on Wednesday this week.", False, "调课"),
            ("The exam is moved to Friday.", False, "调课"),
            ("Classes are cancelled next week.", False, "调课"),
            ("The final exam is on 2026-12-18.", False, "考试"),
            ("There will be a quiz next Friday.", False, "考试"),
            ("The problem set is due Friday.", False, "作业截止"),
            ("Hand in your essay tomorrow.", False, "作业截止"),
            ("Next week everyone must show up because groups will be assigned "
             "for the group project.", False, "其他"),
            ("Everyone gets attendance for this week.", False, "其他"),
        ]
        for q, changed, want in cases:
            with self.subTest(quote=q):
                self.assertEqual(duty.classify(q, changed=changed), want)

    def test_changed压过词表(self):
        """`changed=True` 是 `CHANGED_RE` 亲手判的 —— 类型必须是调课。"""
        self.assertEqual(duty.classify("The exam has been pushed back.", changed=True),
                         "调课")

    def test_空句给其他(self):
        self.assertEqual(duty.classify(""), "其他")
        self.assertEqual(duty.classify(None), "其他")


class ShortTitle(unittest.TestCase):
    def test_剥口水词(self):
        t = duty.short_title("So the first thing is there is an assignment on Brightspace")
        self.assertTrue(t.startswith("there is an assignment"), t)
        self.assertNotIn("first thing", t)
        self.assertEqual(duty.short_title("Ok so the exam is on Friday."),
                         "the exam is on Friday")

    def test_截断在词边界(self):
        t = duty.short_title("the problem set which we discussed in the tutorial is due Friday")
        self.assertTrue(t.endswith("…"), t)
        self.assertLessEqual(len(t), duty.TITLE_MAX + 1)
        self.assertNotIn(" …", t)

    def test_中文按字数截(self):
        t = duty.short_title("下周的实验报告必须在周二之前交给助教并且抄送给老师并且附上代码和截图")
        self.assertTrue(t.endswith("…"), t)
        self.assertLessEqual(len(t), duty.TITLE_MAX + 1)

    def test_短句原样(self):
        self.assertEqual(duty.short_title("The problem set is due Friday."),
                         "The problem set is due Friday")

    def test_空句有兜底(self):
        self.assertEqual(duty.short_title("   "), "（没有原话）")


class 中文摘要(unittest.TestCase):
    """⭐ 作者 2026-10-17：卡片/小卡的标题要**中文摘要**（「本周三停课」那种），
    英文原话只留在「原话」行。摘要复用**已有的**中文（正则 = 即时译文；模型 = `Atom.zh`），
    **一次实时调用都不新增** —— 没有中文就退回英文原话的截短。"""

    def test_译文就是摘要(self):
        self.assertEqual(duty.summary_zh("这周三没有课。"), "这周三没有课")
        self.assertEqual(duty.summary_zh("习题集周五截止。"), "习题集周五截止")

    def test_剥口水词(self):
        self.assertEqual(duty.summary_zh("老师说下周三停课。"), "下周三停课")
        self.assertEqual(duty.summary_zh("同学们注意，周五要交作业。"), "周五要交作业")
        self.assertEqual(duty.summary_zh("那么，下周必须到场。"), "下周必须到场")

    def test_从句边界截短(self):
        got = duty.summary_zh("下周的实验报告必须在周二之前交给助教并且抄送给老师并且附上代码和截图")
        self.assertTrue(got.endswith("…"), got)
        self.assertLessEqual(len(got), duty.SUMMARY_MAX + 1)

    def test_没有中文退回英文且不带句号(self):
        self.assertEqual(duty.summary_zh("", en="The problem set is due Friday."),
                         "The problem set is due Friday")
        self.assertEqual(duty.summary_zh("   ", en="Hand in your essay tomorrow"),
                         "Hand in your essay tomorrow")

    def test_全空给占位(self):
        self.assertEqual(duty.summary_zh("", en=""), "（没有原话）")

    def test_原话行保留标点(self):
        """「原话」是引用不是标题 —— 句号照留（作者 2026-10-17）。"""
        self.assertEqual(duty.clean_quote("So, the problem set is due Friday.", limit=60),
                         "So, the problem set is due Friday.")
        self.assertTrue(duty.clean_quote("x " * 40, limit=20).endswith("…"))

    def test_标题都不带句号(self):
        for t in ("The problem set is due Friday.", "这周三没有课。", "这是标题！",
                  "这是标题？", "这是标题。 "):
            got = duty.clean_title(t)
            self.assertNotRegex(got, r"[.。!！?？]\s*$", got)

    def test_candidate用中文摘要当标题(self):
        it = duty.candidate({"t": "10:00", "quote": "There is no class on Wednesday this week.",
                             "zh": "这周三没有课。", "src": [1], "source": "regex",
                             "changed": False}, day=datetime.date(2026, 10, 17))
        self.assertIsNotNone(it)
        self.assertEqual(it["title"], "这周三没有课")
        self.assertEqual(it["quote"], "There is no class on Wednesday this week.")

    def test_candidate没有中文也不炸(self):
        it = duty.candidate({"t": "10:00", "quote": "The problem set is due Friday.",
                             "src": [1], "source": "regex", "changed": False},
                            day=datetime.date(2026, 10, 17))
        self.assertIsNotNone(it)
        self.assertEqual(it["title"], "The problem set is due Friday")


class TimePointer(unittest.TestCase):
    """**识别那一层唯一的过滤** —— 原型里那几句当正样本，审计那 5 条假课务当负样本。"""

    POSITIVE = [
        "There is no class on Wednesday this week.",
        "Everyone gets attendance for this week.",
        "Next week everyone must show up because groups will be assigned "
        "for the group project.",
        "The problem set is due Friday.",
        "Hand in your essay tomorrow.",
        "The midterm is in week 6.",
        "Classes end at the end of term.",
    ]
    NEGATIVE = [
        "And like I said, I have the final exam of last year.",
        "The deadline is alive.",
        "At the final exam, I made the assumption that you will know what is the "
        "average and marginal.",
        "So the first thing is there is an assignment on Brightspace which you will see.",
        "I'll show you because there are a few exercises that we can do to "
        "practice more for the final exam.",
        "",
    ]

    def test_正样本全认(self):
        for q in self.POSITIVE:
            with self.subTest(quote=q):
                self.assertTrue(duty.has_time_pointer(q, day=MON), q)

    def test_负样本全不认(self):
        for q in self.NEGATIVE:
            with self.subTest(quote=q):
                self.assertFalse(duty.has_time_pointer(q, day=MON), q)


class Candidate(unittest.TestCase):
    def test_原型第一条(self):
        c = duty.candidate(payload("There is no class on Wednesday this week."), day=MON)
        self.assertEqual(c["type"], "调课")
        self.assertEqual(c["date"], "2026-10-14")
        self.assertTrue(c["inferred"])
        self.assertEqual(c["phrase"], "Wednesday")

    def test_原型第二条(self):
        c = duty.candidate(payload("Next week everyone must show up because groups "
                                   "will be assigned for the group project.",
                                   t="16:46:25", source="model"), day=MON)
        self.assertEqual(c["type"], "其他")
        self.assertEqual(c["date"], "2026-10-19")      # +7（周一开的那节课）
        self.assertTrue(c["inferred"])

    def test_有时间指向但算不出日期(self):
        c = duty.candidate(payload("Everyone gets attendance for this week."), day=MON)
        self.assertIsNotNone(c)
        self.assertEqual(c["date"], "")
        self.assertFalse(c["inferred"])
        self.assertTrue(c["undecided"])

    def test_钟点进条目(self):
        c = duty.candidate(payload("Submit the report by 5pm on Friday."), day=MON)
        self.assertEqual(c["date"], "2026-10-16")
        self.assertEqual(c["time"], "17:00")

    def test_没有时间指向返回None(self):
        self.assertIsNone(duty.candidate(
            payload("So the first thing is there is an assignment on Brightspace "
                    "which you will see."), day=MON))

    def test_空payload不炸(self):
        self.assertIsNone(duty.candidate(None, day=MON))
        self.assertIsNone(duty.candidate({}, day=MON))


class Sidecar(unittest.TestCase):
    def test_路径命名法(self):
        self.assertEqual(str(duty.sidecar_path("/x/sessions/2026-10-12_SOC10020.md")),
                         "/x/sessions/2026-10-12_SOC10020.duty.jsonl")
        self.assertIsNone(duty.sidecar_path(None))

    def test_坏行跳过(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "s.duty.jsonl"
            p.write_text('{"kind":"item","id":"d1","quote":"a"}\n'
                         '这不是 JSON\n'
                         '{"kind":"item","id":"d2","quote":"b"}\n', encoding="utf-8")
            got = duty.load(p)
            self.assertEqual([x["id"] for x in got], ["d1", "d2"])
            self.assertEqual(got[0]["action"], "pending")

    def test_缺失文件空表(self):
        self.assertEqual(duty.load(pathlib.Path("/nonexistent/x.duty.jsonl")), [])
        self.assertEqual(duty.load(None), [])


class BoardState(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.tmp.name) / "s.duty.jsonl"
        self.board = duty.Board(day=MON, path=self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_认到候选(self):
        it = self.board.offer(payload("The problem set is due Friday."))
        self.assertIsNotNone(it)
        self.assertEqual(it["id"], "d1")
        self.assertEqual(it["action"], "pending")

    def test_同一条不重复弹卡(self):
        """正则那条先到、模型那条后到（`src` 有交集）→ 合并成一条。"""
        self.assertIsNotNone(self.board.offer(payload("The problem set is due Friday.",
                                                      src=(7,), source="regex")))
        self.assertIsNone(self.board.offer(payload("The problem set is due Friday.",
                                                   src=(7, 8), source="model")))
        its = self.board.items()
        self.assertEqual(len(its), 1)
        self.assertEqual(its[0]["source"], "regex/model")

    def test_模型那条先到正则在后再合并补上改期(self):
        self.board.offer(payload("The problem set is due Friday.", src=(7, 8),
                                 source="model"))
        self.board.offer(payload("The problem set is due Friday.", src=(8,),
                                 source="regex", changed=True))
        its = self.board.items()
        self.assertEqual(len(its), 1)
        self.assertTrue(its[0]["changed"])
        self.assertEqual(its[0]["type"], "调课")

    def test_不认的不进板子(self):
        self.assertIsNone(self.board.offer(payload("The deadline is alive.")))
        self.assertEqual(self.board.items(), [])

    def test_决定与回放(self):
        it = self.board.offer(payload("The problem set is due Friday."))
        self.board.decide(it["id"], "saved", title="改过的标题", date="2026-10-23")
        again = duty.Board(day=MON, path=self.path)
        got = again.get(it["id"])
        self.assertEqual(got["action"], "saved")
        self.assertEqual(got["title"], "改过的标题")
        self.assertEqual(got["date"], "2026-10-23")
        self.assertFalse(got["inferred"], "用户改过的日期不再是推算")

    def test_提醒事项结果回放(self):
        it = self.board.offer(payload("The problem set is due Friday."))
        self.board.decide(it["id"], "saved")
        self.board.note_reminder(it["id"], False, "超时")
        again = duty.Board(day=MON, path=self.path)
        got = again.get(it["id"])
        self.assertEqual(got["reminders"], "failed")
        self.assertEqual(got["reason"], "超时")

    def test_决定写进旁路文件而不是抬头(self):
        it = self.board.offer(payload("The problem set is due Friday."))
        self.board.decide(it["id"], "saved")
        lines = [json.loads(x) for x in self.path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(lines[0]["kind"], "item")
        self.assertEqual(lines[1]["kind"], "decision")
        self.assertEqual(lines[1]["action"], "saved")
        self.assertEqual(lines[1]["id"], it["id"])

    def test_撤销回pending(self):
        it = self.board.offer(payload("The problem set is due Friday."))
        self.board.decide(it["id"], "ignored")
        self.assertEqual(self.board.pending(), [])
        self.board.decide(it["id"], "pending")
        self.assertEqual(len(self.board.pending()), 1)

    def test_计数(self):
        a = self.board.offer(payload("The problem set is due Friday.", src=(1,)))
        self.board.offer(payload("There is no class on Wednesday this week.", src=(2,)))
        self.board.decide(a["id"], "saved")
        self.assertEqual(self.board.stats(),
                         {"items": 2, "pending": 1, "saved": 1, "ignored": 0,
                          "inferred": 2})


class ReminderScript(unittest.TestCase):
    def test_脚本真的对提醒事项说(self):
        self.assertIn('tell application "Reminders"', duty.SCRIPT)
        self.assertIn("make new reminder", duty.SCRIPT)
        # ⚠️ 日期按分量设（不是 date "…"，那个会被区域设置解析成别的日子）
        self.assertIn("set day of theDate to dDay", duty.SCRIPT)

    def test_设月份前先把_day_归_1(self):
        """⭐ DB 审查 D-0：AppleScript 的分量赋值顺序陷阱 —— `set month` 时若 day 比
        目标月长会**溢出进下个月**（今天 31 号、目标 2 月 → 3/1），整条提醒的日期错。
        正确顺序：day=1 → year → month → day。

        ⚠️ 这条只能**结构断言**：真跑要 osascript + 真的往「提醒事项」里写（判据不许
        碰用户的真数据），而 `current date` 的 day 由运行日决定，造不出稳定的红。
        """
        s = duty.SCRIPT
        i_reset = s.index("set day of theDate to 1")      # 先归 1
        i_year = s.index("set year of theDate to dYear")
        i_month = s.index("set month of theDate to dMonth")
        i_day = s.index("set day of theDate to dDay")
        self.assertLess(i_reset, i_year, "先把 day 归 1 再设 year")
        self.assertLess(i_year, i_month, "year 要在 month 之前")
        self.assertLess(i_month, i_day, "day 在最后设")

    def test_参数形状(self):
        item = {"title": "交作业", "quote": "Hand in your essay tomorrow.",
                "t": "13:52:56", "date": "2026-10-13", "time": "", "inferred": True,
                "phrase": "tomorrow"}
        argv = duty.reminder_argv(item)
        self.assertEqual(argv[0], "交作业")
        self.assertIn("Hand in your essay tomorrow.", argv[1])
        self.assertIn("课堂时间 13:52:56", argv[1])
        self.assertEqual(argv[2], duty.LIST_NAME)
        self.assertEqual(argv[3:8], ["2026", "10", "13", "9", "0"])   # 只有日期 → 9:00

    def test_有钟点用钟点(self):
        argv = duty.reminder_argv({"title": "x", "date": "2026-10-16", "time": "17:00"})
        self.assertEqual(argv[6:8], ["17", "0"])

    def test_没日期用今天(self):
        argv = duty.reminder_argv({"title": "x", "date": "", "time": ""})
        today = datetime.date.today()
        self.assertEqual(argv[3:6], [str(today.year), str(today.month), str(today.day)])

    def test_坏日期用今天(self):
        argv = duty.reminder_argv({"title": "x", "date": "周五"})
        self.assertEqual(argv[3:5], [str(datetime.date.today().year),
                                     str(datetime.date.today().month)])


class FakeProc:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


class WriteReminder(unittest.TestCase):
    def test_成功(self):
        seen = {}

        def runner(argv):
            seen["argv"] = argv
            return FakeProc(0)

        ok, reason = duty.write_reminder(["a", "b"], runner=runner)
        self.assertTrue(ok)
        self.assertEqual(reason, "")
        self.assertEqual(seen["argv"], ["a", "b"])

    def test_授权被拒给人话(self):
        def runner(argv):
            return FakeProc(1, "", "execution error: Not authorized to send Apple "
                                   "events to Reminders. (-1743)")
        ok, reason = duty.write_reminder(["a"], runner=runner)
        self.assertFalse(ok)
        self.assertIn("自动化", reason)

    def test_超时给人话(self):
        def runner(argv):
            raise subprocess.TimeoutExpired(cmd="osascript", timeout=30)
        ok, reason = duty.write_reminder(["a"], runner=runner)
        self.assertFalse(ok)
        self.assertIn("授权弹窗", reason)

    def test_怪错误不炸(self):
        def runner(argv):
            raise RuntimeError("boom")
        ok, reason = duty.write_reminder(["a"], runner=runner)
        self.assertFalse(ok)
        self.assertIn("boom", reason)


class Backup(unittest.TestCase):
    def test_只失败时写一条(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "duty-backup.jsonl"
            item = {"title": "交作业", "date": "2026-10-13", "quote": "…",
                    "t": "13:52:56", "type": "作业截止"}
            self.assertTrue(duty.backup(item, "超时", path=p))
            rec = json.loads(p.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(rec["title"], "交作业")
            self.assertEqual(rec["reason"], "超时")
            # 没调用过就不该有这个文件
            p2 = pathlib.Path(d) / "never.jsonl"
            self.assertFalse(p2.exists())

    def test_写不进去也不抛(self):
        self.assertFalse(duty.backup({"title": "x"}, "y", path=pathlib.Path("/dev/null/x/y")))


class StatusText(unittest.TestCase):
    def test_状态行(self):
        self.assertEqual(duty.status_line({"action": "pending"}), "")
        self.assertIn("已忽略", duty.status_line({"action": "ignored"}))
        self.assertIn("已记入提醒事项", duty.status_line(
            {"action": "saved", "reminders": "ok", "date": "2026-10-20"}))
        line = duty.status_line({"action": "saved", "reminders": "failed",
                                 "reason": "超时", "date": ""})
        self.assertIn("超时", line)
        self.assertIn("备份", line)

    def test_状态行不带emoji(self):
        """⭐ 2026-10-17：句首那个 ⚠/✔ 挪去 SF Symbols（`status_symbol`），文字里不许再有。"""
        for it in ({"action": "saved", "reminders": "failed", "reason": "超时"},
                   {"action": "saved", "reminders": "ok", "date": "2026-10-20"},
                   {"action": "saved"}, {"action": "ignored"}):
            line = duty.status_line(it)
            for ch in line:
                self.assertLess(ord(ch), 0x1F000, f"这句里还有 emoji：{line!r}")
            self.assertNotIn("⚠", line)
            self.assertNotIn("✔", line)

    def test_状态配的符号(self):
        self.assertEqual(duty.status_symbol({"action": "pending"}), "")
        self.assertEqual(duty.status_symbol({"action": "ignored"}), "")
        self.assertEqual(duty.status_symbol({"action": "saved", "reminders": "failed"}),
                         "exclamationmark.triangle")
        self.assertEqual(duty.status_symbol({"action": "saved", "reminders": "ok"}),
                         "checkmark.circle")
        self.assertEqual(duty.status_symbol({"action": "saved"}), "")

    def test_日期写法(self):
        self.assertEqual(duty.fmt_when({"date": "2026-10-20"}), "10-20（周二）")
        self.assertEqual(duty.fmt_when({"date": "2026-10-20", "time": "17:00"}),
                         "10-20（周二） 17:00")
        self.assertEqual(duty.fmt_when({"date": ""}), "")


class ChipText(unittest.TestCase):
    def test_没条目不显示(self):
        self.assertEqual(duty.chip_text([]), "")
        self.assertEqual(duty.chip_text([{"action": "ignored", "title": "x"}]), "")

    def test_一条(self):
        txt = duty.chip_text([{"action": "pending", "type": "调课",
                               "title": "本周三停课", "date": "2026-10-14"}])
        self.assertIn("调课", txt)
        self.assertIn("10-14（周三）", txt)
        self.assertNotIn("+", txt)
        # ⭐ 2026-10-17：前缀那个 📌 挪去 `overlay`（SF Symbols 单色小图）——
        #   这里只回**纯文字**（没 AppKit 的地方也该能直接读）。
        self.assertNotIn("📌", txt)
        for ch in txt:
            self.assertFalse(ord(ch) > 0x1F000, f"这行字里不该再有 emoji：{txt!r}")

    def test_多条带加号(self):
        its = [{"action": "pending", "type": "调课", "title": "a", "date": ""},
               {"action": "pending", "type": "考试", "title": "b", "date": ""}]
        self.assertIn("+1", duty.chip_text(its))
        self.assertIn("考试", duty.chip_text(its), "最新的那条在显示位")


if __name__ == "__main__":
    unittest.main()
