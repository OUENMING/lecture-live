#!/usr/bin/env python3
"""`duty_when`（相对日期换算器）的判据 —— **表驱动**。

覆盖四类必须钉住的情形（任务书原话）：
  · 跨周（`this Wednesday` 在周五说出口 = 歧义 → None；`next week` 落 +7）
  · 跨月（`the 3rd` 在 10 月底 → 11-03；`the 31st` 跳过没有 31 号的月）
  · 周末上课（周六上课：「this Friday」已经过去；裸 `Friday` = 下周五）
  · 无法确定（`week 6` / `this week` / `Last year` → None，卡片留空）

⚠️ 每条期望值都指向**具体日期**（不是"命中了没有"）—— `test_live_summary` 那晚
   `changed` 的教训：命中对了、值错了照样是 bug。
"""
import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import duty_when  # noqa: E402

#: 常用锚点。**真实课表上的日期**（原型里那三节课就在这几天）。
MON = datetime.date(2026, 10, 12)      # 周一（SOC10020 那节）
TUE = datetime.date(2026, 10, 13)      # 周二（ECON10740 那节）
THU = datetime.date(2026, 10, 15)      # 周四
SAT = datetime.date(2026, 10, 17)      # 周六（周末上课那一档）
WED_LATE = datetime.date(2026, 10, 28)  # 10 月末，跨月判据用


class ResolveTable(unittest.TestCase):
    """原句 → 期望日期（`None` = 刻意的"算不出来"）。"""

    CASES = [
        # ---- ① 显式日期 ----
        ("Submit it on 2026-10-20.", MON, datetime.date(2026, 10, 20)),
        ("The exam is October 20.", MON, datetime.date(2026, 10, 20)),
        ("The exam is on Oct 20th.", MON, datetime.date(2026, 10, 20)),
        ("Submit by 20 October.", MON, datetime.date(2026, 10, 20)),
        ("The deadline is the 20th.", MON, datetime.date(2026, 10, 20)),
        ("Submit it on the 3rd.", WED_LATE, datetime.date(2026, 11, 3)),      # 跨月
        ("Submit it on the 3rd.", MON, datetime.date(2026, 11, 3)),
        ("Due on the 31st.", datetime.date(2026, 9, 5), datetime.date(2026, 10, 31)),
        # ---- ② this / next / coming + 星期几 ----
        ("There is no class on Wednesday this week.", MON, datetime.date(2026, 10, 14)),
        ("There is no class this Wednesday.", MON, datetime.date(2026, 10, 14)),
        ("That is due next Friday.", MON, datetime.date(2026, 10, 23)),
        ("That is due next Friday.", THU, datetime.date(2026, 10, 23)),
        ("Coming Friday we have a quiz.", THU, datetime.date(2026, 10, 16)),
        ("Coming Friday we have a quiz.", SAT, datetime.date(2026, 10, 23)),
        # ⚠️ 周五说「this Wednesday」= 已经过去 → 歧义，不猜（它是下周三还是本周三？）
        ("This Wednesday the tutorial is cancelled.", datetime.date(2026, 10, 16), None),
        # ---- ③ 裸星期几 ----
        ("The problem set is due Friday.", MON, datetime.date(2026, 10, 16)),
        ("Hand in your essay Monday.", SAT, datetime.date(2026, 10, 19)),
        ("The problem set is due Friday.", SAT, datetime.date(2026, 10, 23)),   # 周末上课
        # ---- ④ tomorrow / today / 过去 ----
        ("Hand in your essay tomorrow.", MON, datetime.date(2026, 10, 13)),
        ("The quiz is today.", MON, MON),
        ("The quiz is tonight.", MON, MON),
        ("The day after tomorrow we present.", MON, datetime.date(2026, 10, 14)),
        ("That was yesterday.", MON, None),
        ("I have the final exam of last year.", MON, None),
        ("The final exam of last Friday was hard.", MON, None),
        # ---- ⑤ in N ----
        ("The report is due in two weeks.", MON, datetime.date(2026, 10, 26)),
        ("The report is due in 3 days.", MON, datetime.date(2026, 10, 15)),
        ("The report is due in a week.", MON, datetime.date(2026, 10, 19)),
        ("See you in one month.", MON, datetime.date(2026, 11, 12)),
        # ---- ⑥ next week（原型里那一例）----
        ("Next week everyone must show up because groups will be assigned "
         "for the group project.", TUE, datetime.date(2026, 10, 20)),
        ("Next week everyone must show up.", SAT, datetime.date(2026, 10, 24)),
        # ---- ⑦ end of the week ----
        ("Submit it by the end of the week.", MON, datetime.date(2026, 10, 16)),
        ("Submit it by the end of the week.", SAT, datetime.date(2026, 10, 23)),
        # ---- ⑧ 中文 ----
        ("实验报告下周三交。", MON, datetime.date(2026, 10, 21)),
        ("下周三交实验报告。", TUE, datetime.date(2026, 10, 21)),
        ("这周三停课。", MON, datetime.date(2026, 10, 14)),
        ("这周三停课。", SAT, None),                                   # 已经过去
        ("下个星期一把作业交上来。", MON, datetime.date(2026, 10, 19)),
        ("下周分组。", TUE, datetime.date(2026, 10, 20)),
        ("明天交作业。", MON, datetime.date(2026, 10, 13)),
        ("后天考试。", MON, datetime.date(2026, 10, 14)),
        ("两周后交报告。", MON, datetime.date(2026, 10, 26)),
        # ---- ⑨ 无法确定（必须 None，不许猜）----
        ("The midterm is in week 6.", MON, None),
        ("Everyone gets attendance for this week.", MON, None),
        ("Classes end next term.", MON, None),
        ("I don't know when the deadline is.", MON, None),
        ("It's somewhere around the end of the month.", MON, None),
        ("There is an assignment on Brightspace which you will see.", MON, None),
    ]

    def test_表驱动(self):
        for quote, day, want in self.CASES:
            with self.subTest(quote=quote, day=day.isoformat()):
                got = duty_when.resolve(quote, day=day)
                self.assertEqual(None if got is None else got.date, want,
                                 f"{quote!r} @ {day}")

    def test_命中的片段被记下来(self):
        got = duty_when.resolve("That is due next Friday.", day=MON)
        self.assertEqual(got.phrase, "next Friday")
        got = duty_when.resolve("实验报告下周三交。", day=MON)
        self.assertEqual(got.phrase, "下周三")
    def test_跨年带上年份(self):
        got = duty_when.resolve("Submit it on January 5.", day=datetime.date(2026, 12, 20))
        self.assertEqual(got.date, datetime.date(2027, 1, 5))

    def test_跨年不带年份时仍然是明年(self):
        """12 月说「1 月 5 日」= 明年 —— `_monthday_next` 的第二个年份那一支。"""
        got = duty_when.resolve("The exam is 5 January.", day=datetime.date(2026, 12, 20))
        self.assertEqual(got.date, datetime.date(2027, 1, 5))

    def test_同一天也算命中(self):
        got = duty_when.resolve("The quiz is today.", day=MON)
        self.assertEqual(got.date, MON)

    def test_输入是datetime也收(self):
        got = duty_when.resolve("Tomorrow we submit.", day=datetime.datetime(2026, 10, 12, 13, 5))
        self.assertEqual(got.date, datetime.date(2026, 10, 13))

    def test_带时区的datetime按本地日算(self):
        tz = datetime.timezone(datetime.timedelta(hours=8))
        # 都柏林 10-12 23:30 = 东八区 10-13 07:30 → 按东八区那天算
        when = datetime.datetime(2026, 10, 12, 23, 30, tzinfo=datetime.timezone.utc)
        got = duty_when.resolve("Tomorrow we submit.", day=when, tz=tz)
        self.assertEqual(got.date, datetime.date(2026, 10, 14))

    def test_空输入不炸(self):
        for q in ("", None, "   "):
            self.assertIsNone(duty_when.resolve(q, day=MON))

    def test_类型错了就抛(self):
        with self.assertRaises(TypeError):
            duty_when.resolve("tomorrow", day="2026-10-12")


class Times(unittest.TestCase):
    CASES = [
        ("The exam starts at 9am.", datetime.time(9, 0)),
        ("The exam starts at 9:30 am.", datetime.time(9, 30)),
        ("Submit by 5pm on Friday.", datetime.time(17, 0)),
        ("Submit by 14:30.", datetime.time(14, 30)),
        ("The quiz is at noon.", datetime.time(12, 0)),
        ("考试上午 9 点开始。", datetime.time(9, 0)),
        ("考试下午 2 点半开始。", datetime.time(14, 30)),
        ("考试晚上 7 点 15 分开始。", datetime.time(19, 15)),
        ("早上 8 点交。", datetime.time(8, 0)),
        ("The exam is in week 6.", None),
        ("The problem set is due Friday.", None),
        ("Submit by 20 October.", None),
    ]

    def test_钟点表(self):
        for quote, want in self.CASES:
            with self.subTest(quote=quote):
                self.assertEqual(duty_when.time_in(quote)[0], want)

    def test_resolve带上钟点(self):
        got = duty_when.resolve("Submit by 5pm on Friday.", day=MON)
        self.assertEqual(got.date, datetime.date(2026, 10, 16))
        self.assertEqual(got.time, datetime.time(17, 0))

    def test_只有日期没有钟点(self):
        got = duty_when.resolve("The problem set is due Friday.", day=MON)
        self.assertIsNone(got.time)


class Format(unittest.TestCase):
    def test_日期格式(self):
        self.assertEqual(duty_when.fmt_date(datetime.date(2026, 10, 14)),
                         "10-14（周三）")

    def test_跨年才带年份(self):
        d = datetime.date(2027, 1, 5)
        self.assertEqual(duty_when.fmt_date(d, anchor=datetime.date(2026, 12, 20)),
                         "2027-01-05（周二）")
        self.assertEqual(duty_when.fmt_date(d, anchor=datetime.date(2027, 1, 1)),
                         "01-05（周二）")

    def test_推算说明带依据(self):
        w = duty_when.resolve("That is due next Friday.", day=MON)
        line = duty_when.explain(w, day=MON)
        self.assertIn("next Friday", line)
        self.assertIn("10-12（周一）", line)
        self.assertIn("10-23（周五）", line)

    def test_钟点进说明(self):
        w = duty_when.resolve("Submit by 5pm on Friday.", day=MON)
        self.assertIn("17:00", duty_when.explain(w, day=MON))


class AuditFalsePositives(unittest.TestCase):
    """审计点名的那 5 条假课务（`tests/test_note_format.py` 同一批原话）——
    它们**一个具体日期都指不出来**，所以换算器一律 `None`（卡片不许凭空造日期）。"""

    FALSE_POSITIVES = [
        "And like I said, I have the final exam of last year.",
        "I'll show you because there are a few exercises that we can do to "
        "practice more for the final exam.",
        "The deadline is alive.",
        "At the final exam, I made the assumption that you will know what is the "
        "average and marginal.",
        "But by the time you get to the final exam, you have done the processes.",
    ]

    def test_一条都不许算出日期(self):
        for q in self.FALSE_POSITIVES:
            with self.subTest(quote=q):
                self.assertIsNone(duty_when.resolve(q, day=MON))


class Undecided(unittest.TestCase):
    def test_算不出来但有线索的句子(self):
        for q in ("The midterm is in week 6.",
                  "Everyone gets attendance for this week.",
                  "Classes end next term."):
            self.assertTrue(duty_when.undecided(q), q)

    def test_能算出来的句子不是undecided(self):
        for q in ("The problem set is due Friday.", "Submit it on 2026-10-20."):
            self.assertFalse(duty_when.undecided(q), q)


if __name__ == "__main__":
    unittest.main()
