#!/usr/bin/env python3
"""相对日期换算器 —— 原话 + **上课日期** → 具体日期（纯函数）。

「一些事儿」卡片上那个日期字段靠它：老师嘴里说的是「这周三 / next Friday /
明天 / in two weeks」，日历里要的是 `2026-10-14`。同类先例是 PassAI
（“dates that PassAI calculated rather than read get **flagged as inferred**”，
passai.pro 博客 2026-10-07 更新）→ 所以算出来的日期在卡片上**单独标「推算」**、
可改（见 `duty.explain` 那句小字）。

## 接口就一个

    resolve(quote, *, day) -> When | None

`day` = **上课日期**（这节课是哪天）。`When` 里三样：`date` / `time`（原话没给钟点
就是 `None`）/ `phrase`（原话里命中的那一段，卡片上「推算依据」用）。

## 三条刻意定死的纪律

1. **只有未来**。算出来的日期**早于上课日**一律返回 `None`（`this Wednesday`
   在周五说出口时既可能指过去、也可能指下周三 —— 歧义就不猜）。
   算不出 / 有歧义 → `None`：卡片上日期留空，用户自己补，**不许猜一个错的**。
2. **日历算术，不是时间差**：`in two weeks` = +14 天；`next month` = 下个月的
   同日（该月没有 31 号就落到月末）。时区不参与 —— `day` 是**本地日历日**，
   调用方从课表/时钟取（`_as_date` 支持直接给 `date` 或带时区的 `datetime`）。
3. **表驱动**：一条规则 = 一段正则 + 一句人话的依据。加词改词只动
   `_rule_*`，别在别处再写一份日期猜测。

⚠️ **`week 6`（学期第 N 周）刻意不换算**：它要课表的锚点，本模块没有也不该有
   （brainstorm #6 的「风险」那条）。返回 `None`，卡片上留空。
"""
from __future__ import annotations

import dataclasses
import datetime
import re

#: 星期的中英文名。`weekday()` 口径（周一 = 0）。
_WD_EN = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
          "friday": 4, "saturday": 5, "sunday": 6}
_WD_ZH = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
_WD_LABEL = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

_MONTHS = {m: i + 1 for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"))}
_MONTH_RE = "|".join(("jan(?:uary)?", "feb(?:ruary)?", "mar(?:ch)?", "apr(?:il)?",
                      "may", "jun(?:e)?", "jul(?:y)?", "aug(?:ust)?",
                      "sep(?:t)?(?:ember)?", "oct(?:ober)?", "nov(?:ember)?",
                      "dec(?:ember)?"))
_WD_RE = "|".join(_WD_EN)

#: 一年里各月的天数（闰年单独判）—— `_add_months` 用。
_MDAYS = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)

# ---- 表驱动：每一条 = 一个正则 + 一段人话依据 ----------------------------------
_R_ISO = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
_R_MD = re.compile(rf"\b({_MONTH_RE})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", re.I)
_R_DM = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MONTH_RE})\b", re.I)
_R_ORD = re.compile(r"\b(?:on\s+)?(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)\b", re.I)
_R_REL_WD = re.compile(rf"\b(this|next|last|coming)\s+({_WD_RE})\b", re.I)
_R_BARE_WD = re.compile(rf"\b({_WD_RE})\b", re.I)
_R_TOMORROW = re.compile(r"\b(tomorrow|today|tonight|yesterday)\b", re.I)
_R_DAY_AFTER = re.compile(r"\bthe\s+day\s+after\s+tomorrow\b", re.I)
_R_IN_N = re.compile(r"\bin\s+(a|an|one|two|three|four|five|six|seven|eight|nine|ten|"
                     r"\d{1,2})\s+(day|week|month)s?\b", re.I)
_R_NEXT_WEEK = re.compile(r"\bnext\s+week\b", re.I)
_R_THIS_WEEK = re.compile(r"\b(this|next|last)\s+(week|month|semester|term)\b", re.I)
_R_END_WEEK = re.compile(r"\b(?:by\s+|at\s+)?(?:the\s+)?end\s+of\s+(?:the\s+|this\s+)?week\b", re.I)
_R_WEEK_N = re.compile(r"\bweek\s*(\d{1,2})\b", re.I)
_R_ZH_REL = re.compile(r"(下|本|这|该)(?:个)?(周|星期|礼拜)([一二三四五六日天])")
_R_ZH_WEEK = re.compile(r"(下|本|这|该)(?:个)?(周|星期|礼拜)(?![一二三四五六日天])")
_R_ZH_DAY = re.compile(r"(今天|今日|今晚|明天|明日|后天|昨天|昨晚)")
_R_ZH_IN = re.compile(r"([一二两三四五六七八九十\d]{1,3})\s*(?:个)?(天|周|星期|礼拜|月)\s*(?:后|以后|之后|内|内交|之前|以前)")

_WORD_NUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
             "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
_ZH_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6,
           "七": 7, "八": 8, "九": 9, "十": 10}

# ---- 钟点（第二把尺子，与日期分开）--------------------------------------------
_R_T_EN = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", re.I)
_R_T_24 = re.compile(r"\b(\d{1,2}):(\d{2})\b")
_R_T_WORD = re.compile(r"\b(noon|midnight)\b", re.I)
_R_T_ZH = re.compile(r"(上午|早上|下午|晚上|中午)\s*(\d{1,2})\s*点\s*(?:(半)|(\d{1,2})\s*分)?")
#: 「9 点」这种没有时段词的：口语里几乎一定是上午/白天。⚠️ 只有**一句话里没有任何
#: 24 小时线索**时才用它（避免 `at 20 点` 被读成 20:00 后又补成上午）。
_R_T_ZH_BARE = re.compile(r"(\d{1,2})\s*点(?:\s*(半)|(\d{1,2})\s*分)?")


@dataclasses.dataclass(frozen=True)
class When:
    """换算结果。`phrase` = 原话里命中的那一段（卡片上「推算依据」用）。"""
    date: datetime.date
    time: datetime.time | None
    phrase: str


def _as_date(day, tz=None) -> datetime.date:
    """`date` / `datetime` → **本地日历日**。

    ⚠️ `tz` 只在这一步有用（把一个带时区的时刻落成"用户那天"）；换算本身是
       日历算术，与 tz 无关 —— 所以它不是一个被忽略的参数。
    """
    if isinstance(day, datetime.datetime):
        if tz is not None and day.tzinfo is not None:
            return day.astimezone(tz).date()
        return day.date()
    if isinstance(day, datetime.date):
        return day
    raise TypeError(f"day 需要 date / datetime，得到 {type(day).__name__}")


def _is_leap(y: int) -> bool:
    return y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)


def _mdays(y: int, m: int) -> int:
    return 29 if (m == 2 and _is_leap(y)) else _MDAYS[m - 1]


def _add_months(d: datetime.date, n: int) -> datetime.date:
    """加 N 个日历月。该月没有那一天 → **落到月末**（不溢出到下个月）。"""
    m = d.month - 1 + n
    y = d.year + m // 12
    m = m % 12 + 1
    return datetime.date(y, m, min(d.day, _mdays(y, m)))


def _month_start(d: datetime.date) -> datetime.date:
    return d.replace(day=1)


def _monday(d: datetime.date) -> datetime.date:
    """ISO 周的第一天（周一）。⚠️ 用 ISO 而不是 `weekday()` 的直觉版 —— 欧洲的
    周历从周一起算，`本周三` 在周日说出时仍指**这个 ISO 周**的周三。"""
    return d - datetime.timedelta(days=d.weekday())


def _monthday_next(anchor: datetime.date, month: int, dom: int) -> datetime.date | None:
    """「某月某日」的下一个出现（≥ anchor）；已经过去 → `None`（不猜明年）。"""
    for year in (anchor.year, anchor.year + 1):
        if dom > _mdays(year, month):
            continue
        cand = datetime.date(year, month, dom)
        if cand >= anchor:
            return cand
    return None


def _day_of_month_next(anchor: datetime.date, dom: int) -> datetime.date | None:
    """「the 20th」→ 本月 20 号；已过 → 下个月 20 号（跨月）。"""
    for k in (0, 1, 2):
        base = _add_months(_month_start(anchor), k)
        if dom > _mdays(base.year, base.month):
            continue                                    # 这个月没有 31 号
        cand = base.replace(day=dom)
        if cand >= anchor:
            return cand
    return None


def _weekday_this(anchor: datetime.date, wd: int) -> datetime.date | None:
    """「这周三」—— 本 ISO 周的那个星期几。**早于上课日 → None**（歧义）。"""
    cand = _monday(anchor) + datetime.timedelta(days=wd)
    return cand if cand >= anchor else None


def _weekday_next(anchor: datetime.date, wd: int) -> datetime.date:
    """「下周三」—— 下一个 ISO 周的那个星期几。"""
    return _monday(anchor) + datetime.timedelta(days=7 + wd)


def _weekday_coming(anchor: datetime.date, wd: int) -> datetime.date:
    """「coming Friday」= 不早于上课日的下一个该星期几（当天就算当天）。"""
    cand = _monday(anchor) + datetime.timedelta(days=wd)
    return cand if cand >= anchor else cand + datetime.timedelta(days=7)


def _weekday_bare(anchor: datetime.date, wd: int) -> datetime.date:
    """裸星期几（`due Friday`）：不早于上课日的下一个（含当天）。"""
    return _weekday_coming(anchor, wd)


def _last_weekday_of(d: datetime.date) -> datetime.date:
    return d.replace(day=_mdays(d.year, d.month))


def _rule_date(quote: str, anchor: datetime.date):
    """按**优先级**过一遍日期规则。返回 `(date, phrase)` 或 `None`。

    顺序是有讲究的（先长后短、先显式后推测）：
      ① `2026-10-20`     ② `October 20` / `20 October`     ③ `the 20th`
      ④ `this/next/coming Friday`    ⑤ `the day after tomorrow` / `tomorrow`
      ⑥ `in two weeks`   ⑦ `next week`   ⑧ `end of the week`
      ⑨ 裸 `Friday`      ⑩ 中文（下周三 / 明天 / 两周后）
    ⑪ `week 6` —— **明确算不出来**，返回 `None`（不是没命中）
    """
    # ① 显式 ISO
    m = _R_ISO.search(quote)
    if m:
        try:
            d = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            d = None
        if d is not None:
            return (d, m.group(0)) if d >= anchor else None

    # ② 月名 + 日
    m = _R_MD.search(quote)
    if m:
        d = _monthday_next(anchor, _MONTHS[m.group(1).lower()[:3]], int(m.group(2)))
        return (d, m.group(0)) if d else None
    m = _R_DM.search(quote)
    if m:
        d = _monthday_next(anchor, _MONTHS[m.group(2).lower()[:3]], int(m.group(1)))
        return (d, m.group(0)) if d else None

    # ③ 序数日（the 20th）
    m = _R_ORD.search(quote)
    if m:
        dom = int(m.group(1))
        if 1 <= dom <= 31:
            d = _day_of_month_next(anchor, dom)
            return (d, m.group(0)) if d else None

    # ④ this / next / coming / last + weekday
    m = _R_REL_WD.search(quote)
    if m:
        word, wd = m.group(1).lower(), _WD_EN[m.group(2).lower()]
        if word == "last":
            return None                                  # 回指过去：不是一条待办
        if word == "this":
            d = _weekday_this(anchor, wd)
            return (d, m.group(0)) if d else None
        if word == "next":
            return _weekday_next(anchor, wd), m.group(0)
        return _weekday_coming(anchor, wd), m.group(0)   # coming

    # ⑤ 明天 / 后天 / 今天 / 昨天
    if _R_DAY_AFTER.search(quote):
        return anchor + datetime.timedelta(days=2), "the day after tomorrow"
    m = _R_TOMORROW.search(quote)
    if m:
        w = m.group(1).lower()
        if w == "yesterday":
            return None
        days = {"tomorrow": 1, "today": 0, "tonight": 0}[w]
        return anchor + datetime.timedelta(days=days), m.group(0)

    # ⑥ in N days / weeks / months
    m = _R_IN_N.search(quote)
    if m:
        n = _WORD_NUM.get(m.group(1).lower())
        if n is None:
            n = int(m.group(1))
        unit = m.group(2).lower()
        if unit == "day":
            return anchor + datetime.timedelta(days=n), m.group(0)
        if unit == "week":
            return anchor + datetime.timedelta(days=7 * n), m.group(0)
        return _add_months(anchor, n), m.group(0)        # month

    # ⑦ next week（无星期几）—— 原型里那一例：上课日 10-13（二）→ 10-20（二）
    m = _R_NEXT_WEEK.search(quote)
    if m:
        return anchor + datetime.timedelta(days=7), m.group(0)
    # ⑧ end of the week —— 本 ISO 周的周五；已经过去就下一次（周六上课那一档）
    m = _R_END_WEEK.search(quote)
    if m:
        fri = _monday(anchor) + datetime.timedelta(days=4)
        if fri < anchor:
            fri += datetime.timedelta(days=7)
        return fri, m.group(0)
    # this week / next month / next term 这类**没有星期几**的：只能算 next month
    m = _R_THIS_WEEK.search(quote)
    if m and m.group(2).lower() == "month" and m.group(1).lower() == "next":
        return _add_months(anchor, 1), m.group(0)
    if m and m.group(1).lower() == "last":
        return None
    # ⚠️ `this week` / `next term` 语料里确实出现过（原型那条「Everyone gets
    #    attendance for this week」）—— 它们**没有具体到某天**，不许猜。

    # ⑨ 裸星期几
    m = _R_BARE_WD.search(quote)
    if m:
        return _weekday_bare(anchor, _WD_EN[m.group(1).lower()]), m.group(0)
    # ⑩ 中文
    m = _R_ZH_REL.search(quote)
    if m:
        rel, wd = m.group(1), _WD_ZH[m.group(3)]
        if rel == "下":
            return _weekday_next(anchor, wd), m.group(0)
        d = _weekday_this(anchor, wd)
        return (d, m.group(0)) if d else None
    m = _R_ZH_WEEK.search(quote)
    if m:
        if m.group(1) == "下":
            return anchor + datetime.timedelta(days=7), m.group(0)
        return None                                      # 本周 / 这周：没到某天
    m = _R_ZH_DAY.search(quote)
    if m:
        w = m.group(1)
        if w in ("昨天", "昨晚"):
            return None
        days = {"今天": 0, "今日": 0, "今晚": 0, "明天": 1, "明日": 1, "后天": 2}[w]
        return anchor + datetime.timedelta(days=days), m.group(0)
    m = _R_ZH_IN.search(quote)
    if m:
        raw = m.group(1)
        n = int(raw) if raw.isdigit() else _ZH_NUM.get(raw)
        if n is not None and n > 0:
            unit = m.group(2)
            if unit == "天":
                return anchor + datetime.timedelta(days=n), m.group(0)
            if unit in ("周", "星期", "礼拜"):
                return anchor + datetime.timedelta(days=7 * n), m.group(0)
            return _add_months(anchor, n), m.group(0)     # 月
    return None


def time_in(quote: str) -> tuple[datetime.time | None, str]:
    """原话里的钟点 `(time, phrase)`；没给就是 `(None, "")`。

    ⚠️ **只有明确写了钟点才认**（`9am` / `14:30` / `中午 12 点`）。「明天下午」
       这类只给时段不给点的**不算** —— 日期字段照收，时间留给用户。
    """
    m = _R_T_EN.search(quote)
    if m:
        h, mi = int(m.group(1)), int(m.group(2) or 0)
        if 1 <= h <= 12 and mi <= 59:
            ampm = m.group(3).lower()
            h = (h % 12) + (12 if ampm == "pm" else 0)
            return datetime.time(h, mi), m.group(0)
    m = _R_T_24.search(quote)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        if h <= 23 and mi <= 59:
            return datetime.time(h, mi), m.group(0)
    m = _R_T_ZH.search(quote)
    if m:
        period, h, half, mi = m.group(1), int(m.group(2)), m.group(3), m.group(4)
        if 0 < h <= 24:
            mi = 30 if half else int(mi or 0)
            if mi <= 59:
                if period in ("下午", "晚上") and h < 12:
                    h += 12
                elif period in ("上午", "早上", "中午") and h == 12:
                    h = 12
                if h == 24:
                    h = 0
                return datetime.time(h, mi), m.group(0)
    m = _R_T_WORD.search(quote)
    if m:
        h = 12 if m.group(1).lower() == "noon" else 0
        return datetime.time(h, 0), m.group(0)
    m = _R_T_ZH_BARE.search(quote)
    if m:
        h = int(m.group(1))
        half, mi = m.group(2), m.group(3)
        if 0 < h <= 24 and not _R_T_24.search(quote):
            mi_ = 30 if half else int(mi or 0)
            if mi_ <= 59:
                return datetime.time(0 if h == 24 else h, mi_), m.group(0)
    return None, ""


def resolve(quote: str, *, day, tz=None) -> When | None:
    """原话 + 上课日期 → 具体日期时间；**算不出 / 有歧义 / 已经过去 → `None`**。

    ⚠️ 返回 `None` 是**正常结果**（卡片日期留空、用户自己补），不是错误 ——
       猜一个错的日期会写进「提醒事项」，那比不写严重得多。
    """
    anchor = _as_date(day, tz)
    s = str(quote or "")
    hit = _rule_date(s, anchor)
    if hit is None:
        return None
    d, phrase = hit
    if d < anchor:                                        # 双保险：规则里已经挡过
        return None
    t, _tp = time_in(s)
    return When(date=d, time=t, phrase=phrase)


def fmt_date(d: datetime.date, *, anchor: datetime.date | None = None) -> str:
    """`2026-10-14` → `10-14（周三）`；跨年时带上年份（`2027-01-05（周二）`）。"""
    s = d.strftime("%m-%d")
    if anchor is not None and d.year != anchor.year:
        s = f"{d.year}-{s}"
    return f"{s}（{_WD_LABEL[d.weekday()]}）"


def fmt_time(t: datetime.time | None) -> str:
    return "" if t is None else t.strftime("%H:%M")


def explain(when: When, *, day) -> str:
    """卡片上那行小字：`「next week」按上课日 10-13（周二）换算 = 10-20（周二）`。

    ⚠️ 措辞里**必须带「按上课日」** —— 读者要能一眼看出这是推算、依据是什么，
       算错了才好改（PassAI 那条先例）。
    """
    anchor = _as_date(day)
    tt = f" {fmt_time(when.time)}" if when.time else ""
    return (f"「{when.phrase}」按上课日 {fmt_date(anchor)} 推算 = "
            f"{fmt_date(when.date, anchor=anchor)}{tt}；可以改")


def undecided(quote: str) -> bool:
    """这句话里有没有**日期线索但算不出来**（`week 6` / `this week`）？

    卡片上据此写一句「原话的日期指不出来，先留空」—— 不是静默。
    """
    s = str(quote or "")
    if _R_WEEK_N.search(s):
        return True
    m = _R_THIS_WEEK.search(s)
    return bool(m and not (m.group(2).lower() == "month" and m.group(1).lower() == "next"))
