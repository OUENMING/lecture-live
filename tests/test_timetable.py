#!/usr/bin/env python3
"""`timetable.py` 的判据 —— 从 `.ics` 认课。

    ClassLive.app/Contents/MacOS/python tests/test_timetable.py

## 这个文件钉住了什么

1. ⭐⭐ **折行在字节层拼** —— RFC 5545 §3.1 说折行**可能切在 UTF-8 多字节中间**，
   先解码再拼会产出替换字符，**而且不报错**。
2. ⭐ **按 `SUMMARY` 分组**，且**课型后缀要剥**（`X (Lecture)` 与 `X (Lab)` 是同一门课）。
3. ⭐ **课号猜得出** —— `\\d{2,4}` 吃不下 UCD 的**五位**课号 `ECON10740`（实测踩过）。
4. ⭐⭐ **`INTERVAL=2` 与 `EXDATE` 真的生效** —— 不看这两条，双周课会**周周误报**。
5. ⭐ **时区要换算** —— feed 用 UTC 时不换算就是把课排在错的时间上。
6. **坏数据要出声、不许抛** —— 一份课表里坏一条不该让整份导入失败。

⚠️ 夹具全部来自**调研实拉的真形态**（UCD 都柏林 / UC Davis / USC / SUTD / Progotec / 印度），
不是自己编一份漂亮的。
"""
from __future__ import annotations

import datetime
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import timetable as T                                             # noqa: E402

CASES: list[tuple[str, object]] = []
FAIL: list[str] = []
UTC = datetime.timezone.utc


def case(name: str):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


def ics(*lines: str) -> bytes:
    """拼一份 `.ics`，**行尾用 CRLF**（规范要求，真实 feed 也是）。"""
    return ("\r\n".join(("BEGIN:VCALENDAR",) + lines + ("END:VCALENDAR", ""))
            ).encode("utf-8")


def ev(uid, summary, dtstart, *, rrule="", exdate="", dtend="", desc=""):
    out = [f"BEGIN:VEVENT", f"UID:{uid}", f"SUMMARY:{summary}", f"DTSTART:{dtstart}"]
    if dtend:
        out.append(f"DTEND:{dtend}")
    if rrule:
        out.append(f"RRULE:{rrule}")
    if exdate:
        out.append(f"EXDATE:{exdate}")
    if desc:
        out.append(f"DESCRIPTION:{desc}")
    out.append("END:VEVENT")
    return out


# ---------------------------------------------------------------- 折行
@case("⭐⭐ 折行：切在 UTF-8 多字节中间也要拼回来（字节层）")
def t_fold_multibyte():
    # 「社会学」= e7 a4 be e4 bc 9a e5 ad a6 —— 在第 1 个字节后就折行
    body = "社会学导论".encode("utf-8")
    raw = (b"BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:x\r\nSUMMARY:" + body[:1]
           + b"\r\n " + body[1:] + b"\r\nDTSTART:20260907T110000\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    cs, warn = T.parse(raw)
    # 变异验证：把 `unfold_bytes` 改成先 `.decode()` 再拼 → 这里会拿到 '�'
    assert len(cs) == 1, f"没认出来：{cs} / {warn}"
    assert cs[0].name == "社会学导论", f"折行拼坏了：{cs[0].name!r}"
    assert "�" not in cs[0].name, "出了替换字符 —— 说明是先解码再拼的"


@case("折行：LF 行尾也认（真实 feed 常见）")
def t_fold_lf():
    raw = ics(*ev("a", "Physics (Lecture)", "20260907T090000")).replace(b"\r\n", b"\n")
    cs, _ = T.parse(raw)
    assert len(cs) == 1 and cs[0].name == "Physics", cs


# ---------------------------------------------------------------- 分组
@case("按 SUMMARY 分组：不同 SUMMARY 就是不同课")
def t_group_by_summary():
    cs, _ = T.parse(ics(*ev("a", "Alpha (Lecture)", "20260907T090000"),
                         *ev("b", "Beta (Lecture)", "20260907T100000")))
    assert [c.name for c in cs] == ["Alpha", "Beta"], [c.name for c in cs]


@case("⭐ 课型后缀要剥：同一门课的 Lecture 与 Lab 归一")
def t_strip_section():
    # ⚠️ USC Banner 的形态：课号相同、靠括号区分课型
    cs, _ = T.parse(ics(*ev("a", "CSCI-104L (Lecture)", "20260907T090000"),
                         *ev("b", "CSCI-104L (Lab)", "20260909T090000")))
    assert len(cs) == 1, f"没归成一门课：{[c.name for c in cs]}"
    assert cs[0].name == "CSCI-104L" and cs[0].events == 2, cs[0]


@case("剥课型：不许把整串名字剥光")
def t_strip_never_empty():
    assert T.normalize_name("(Lecture)") == "(Lecture)", "全剥光了"
    assert T.normalize_name("Calculus (Lecture)") == "Calculus"
    assert T.normalize_name("Calculus (Tutorial)") == "Calculus"
    assert T.normalize_name("社会学导论（实验）") == "社会学导论"


# ---------------------------------------------------------------- 课号
@case("⭐⭐ 猜课号：UCD 的**五位**课号（`\\d{2,4}` 吃不下）")
def t_suggest_five_digits():
    # 变异验证（实跑过）：把 `_CODE_RE` 改回 `\\d{2,4}` → 这条立刻红。
    assert T.suggest_code("ECON10740: Exploring Economics") == "ECON10740"


@case("猜课号：UC Davis 的课号**里有空格**，不许按空格切")
def t_suggest_space_code():
    assert T.suggest_code("ECS 170 001 Introduction to AI") == "ECS 170 001"
    assert T.suggest_code("PHY 009B - Lecture") == "PHY 009B"


@case("猜课号：整串就是课号（USC Banner `CSCI-104L`）")
def t_suggest_only_code():
    assert T.suggest_code("CSCI-104L") == "CSCI-104L"
    assert T.suggest_code("MATH2010") == "MATH2010"


@case("猜课号：猜不出就返回空串 —— **不许硬猜**")
def t_suggest_gives_up():
    for s in ("Calculus", "Teamarb.u.Teamleitg.iProj", "Sociology 101",
              "Algorithm Design Techniques (25CS301)", "一级课程"):
        assert T.suggest_code(s) == "", f"{s!r} 被硬猜成 {T.suggest_code(s)!r}"


# ---------------------------------------------------------------- 双周 / 停课
@case("⭐⭐ INTERVAL=2：隔一周才上（不看它就会周周误报）")
def t_interval():
    # 变异验证：把 `Slot.on_week` 的 interval 分支删掉 → 这条红。
    raw = ics(*ev("a", "Tutorial (Tutorial)", "20260915T120000",   # 周二
                  rrule="FREQ=WEEKLY;INTERVAL=2;BYDAY=TU"))
    cs, _ = T.parse(raw)
    s = cs[0].slots[0]
    assert s.interval == 2 and s.anchor == datetime.date(2026, 9, 15), s
    assert s.on_week(datetime.date(2026, 9, 15)), "锚点那一周必须算"
    assert s.on_week(datetime.date(2026, 9, 29)), "第 2 个隔周必须算"
    assert not s.on_week(datetime.date(2026, 9, 22)), "中间那周**不该**上"
    assert not s.on_week(datetime.date(2026, 10, 6)), "第 4 周不该上"


@case("⭐ EXDATE：停课日那天不算")
def t_exdate():
    raw = ics(*ev("a", "Lecture (Lecture)", "20260907T110000",
                  rrule="FREQ=WEEKLY;BYDAY=MO",
                  exdate="20260928T110000"))
    cs, _ = T.parse(raw)
    s = cs[0].slots[0]
    assert s.on_week(datetime.date(2026, 9, 21)), "没停的那周要算"
    assert not s.on_week(datetime.date(2026, 9, 28)), "EXDATE 那天**不该**算"


@case("BYDAY 多个：一门课可以有几个时段")
def t_byday_multi():
    raw = ics(*ev("a", "Stats (Lecture)", "20260907T100000",
                  rrule="FREQ=WEEKLY;BYDAY=MO,WE,FR"))
    cs, _ = T.parse(raw)
    assert sorted(s.weekday for s in cs[0].slots) == [0, 2, 4], cs[0].slots


# ---------------------------------------------------------------- 时区
@case("⭐ 时区：UTC 源要换算到目标时区（否则课排在错的时间上）")
def t_timezone():
    # `DTSTART:20260907T000000Z` = UTC 零点 → 东九区是当天 09:00
    raw = ics(*ev("a", "Tokyo Class (Lecture)", "20260907T000000Z"))
    cs, _ = T.parse(raw, tz=datetime.timezone(datetime.timedelta(hours=9)))
    assert cs[0].slots[0].hh == 9, f"UTC 00:00 换成 +9 应该是 9 点，拿到 {cs[0].slots[0].hh}"


@case("时区：带 TZID 的源也换算")
def t_timezone_tzid():
    raw = ("\r\n".join(("BEGIN:VCALENDAR", "BEGIN:VEVENT", "UID:a",
                        "SUMMARY:Tokyo Class (Lecture)",
                        "DTSTART;TZID=Asia/Tokyo:20260907T090000",
                        "END:VEVENT", "END:VCALENDAR", ""))).encode("utf-8")
    cs, warn = T.parse(raw, tz=UTC)
    assert cs[0].slots[0].hh == 0, f"东京 09:00 = UTC 00:00，拿到 {cs[0].slots[0].hh}（warn={warn}）"


@case("时区：浮动时间（没有 Z 也没有 TZID）按**写的**算，不换")
def t_floating():
    cs, _ = T.parse(ics(*ev("a", "Floating (Lecture)", "20260907T140000")), tz=UTC)
    assert cs[0].slots[0].hh == 14, cs[0].slots[0]


# ---------------------------------------------------------------- 出声
@case("坏 DTSTART → 进 warn，**不是抛异常**")
def t_bad_dtstart():
    cs, warn = T.parse(ics(*ev("a", "Broken (Lecture)", "NOT-A-DATE"),
                           *ev("b", "Good (Lecture)", "20260907T090000")))
    assert [c.name for c in cs] == ["Good"], cs
    assert any("DTSTART" in w for w in warn), warn


@case("空 / 没有 VEVENT → 出声")
def t_empty():
    cs, warn = T.parse(ics())
    assert cs == [] and warn, (cs, warn)


@case("⚠️ `DESCRIPTION` 不许被读进来（隐私）")
def t_no_description():
    # ⚠️ `[一手]` UBC：「Shared calendars often contain **other people's
    #    sensitive information that they haven't consented to share**」
    raw = ics(*ev("a", "Econ (Lecture)", "20260907T090000",
                  desc="Staff: Dr. Someone\\nStudents: Alice\\, Bob"))
    cs, _ = T.parse(raw)
    blob = repr(cs)
    assert "Someone" not in blob and "Alice" not in blob, blob


@case("脏数据：title 带尾部空格 / 空 SUMMARY")
def t_dirty():
    cs, warn = T.parse(ics(*ev("a", "  Spaced Title   (Lecture) ", "20260907T090000"),
                           *ev("b", "", "20260907T100000")))
    assert [c.name for c in cs] == ["Spaced Title"], [c.name for c in cs]
    assert any("SUMMARY" in w for w in warn), warn


@case("⭐⭐ 预选：课表命中就推它，**并且说出为什么**")
def t_suggest_from_timetable():
    cs, _ = T.parse(ics(*ev("a", "ECON10740: Exploring Economics (Lecture)",
                            "DTSTART;TZID=Europe/Dublin:20260908T150000".replace(
                                "DTSTART;TZID=Europe/Dublin:", ""),
                            rrule="FREQ=WEEKLY;BYDAY=TU")))
    ent = [("ECON10740", list(cs[0].slots))]
    got = T.suggest(ent, datetime.datetime(2026, 9, 22, 15, 10))   # 周二 15:10
    assert len(got) == 1 and got[0].course == "ECON10740", got
    assert got[0].score == 1.0 and "15:00" in got[0].why, got[0]
    # ⭐ `[一手]` Microsoft HAX G11「Make clear why the system did what it did」
    #    —— 理由不能是空串，否则面板上"为什么这张排第一"对用户就是猜的
    assert got[0].why.strip(), "理由不能为空"


@case("⭐⭐ 预选：都不命中 → **空表**（不硬猜一个第一）")
def t_suggest_no_guess():
    cs, _ = T.parse(ics(*ev("a", "X (Lecture)", "20260908T150000",
                            rrule="FREQ=WEEKLY;BYDAY=TU")))
    ent = [("X", list(cs[0].slots))]
    # 同一天但不同小时
    assert T.suggest(ent, datetime.datetime(2026, 9, 22, 9, 0)) == []
    # 不同天
    assert T.suggest(ent, datetime.datetime(2026, 9, 23, 15, 0)) == []
    assert T.suggest([], datetime.datetime(2026, 9, 22, 15, 0)) == []


@case("预选：历史是**弱信号**（0.5），课表是强信号（1.0）")
def t_suggest_weights():
    cs, _ = T.parse(ics(*ev("a", "A (Lecture)", "20260908T150000",
                            rrule="FREQ=WEEKLY;BYDAY=TU")))
    ent = [("A", list(cs[0].slots))]
    when = datetime.datetime(2026, 9, 22, 15, 0)
    only_hist = T.suggest([], when, history={"B": [(1, 15)]})
    assert only_hist[0].score == 0.5, only_hist
    both = T.suggest(ent, when, history={"A": [(1, 15), (1, 15)]})
    assert both[0].score == 1.5, both            # 两条都给分，不是取大的那个
    assert "课表" in both[0].why and "2 次" in both[0].why, both[0].why


@case("预选：按分数排序、`limit` 生效；⚖️ 并列按课号（可复现）")
def t_suggest_order():
    cs, _ = T.parse(ics(*ev("a", "A (Lecture)", "20260908T150000",
                            rrule="FREQ=WEEKLY;BYDAY=TU")))
    ent = [("A", list(cs[0].slots))]
    # ⚠️ 历史那条**不看次数**（1 次和 2 次同分 0.5）—— 「这门课在这个时段上过」
    #    见过一次模式就成立；给次数加权会造一个没人验证过的 magic 公式，
    #    而它直接决定"预选哪张卡"。所以 B 和 C 并列，按课号排。
    hist = {"B": [(1, 15)], "C": [(1, 15), (1, 15)], "D": [(1, 15)]}
    got = T.suggest(ent, datetime.datetime(2026, 9, 22, 15, 0), history=hist)
    assert [g.course for g in got] == ["A", "B", "C"], got
    assert got[0].score == 1.0 and got[1].score == 0.5, got
    # ⚠️ 次数**必须出现在理由里** —— 面板只显示 why，不显示 score
    assert "1 次" in got[1].why and "2 次" in got[2].why, [g.why for g in got]
    assert len(T.suggest(ent, datetime.datetime(2026, 9, 22, 15, 0),
                         history=hist, limit=2)) == 2


@case("⭐ 预选也吃 INTERVAL / EXDATE（不是只看周几+小时）")
def t_suggest_respects_interval():
    cs, _ = T.parse(ics(*ev("a", "T (Tutorial)", "20260915T120000",   # 周二
                            rrule="FREQ=WEEKLY;INTERVAL=2;BYDAY=TU")))
    ent = [("T", list(cs[0].slots))]
    assert T.suggest(ent, datetime.datetime(2026, 9, 15, 12, 0)), "锚点那周该推"
    assert T.suggest(ent, datetime.datetime(2026, 9, 29, 12, 0)), "第 2 个隔周该推"
    # 变异验证：把 `Slot.on_week` 的 interval 分支短路掉 → 这条红
    assert T.suggest(ent, datetime.datetime(2026, 9, 22, 12, 0)) == [], "中间那周**不该**推"


@case("课表存/取：往返一致；坏数据不抛、跳过")
def t_dump_load():
    cs, _ = T.parse(ics(*ev("a", "A (Lecture)", "20260908T150000",
                            rrule="FREQ=WEEKLY;BYDAY=TU,TH"),
                         *ev("b", "B (Lab)", "20260909T100000",
                            exdate="20260916T100000")))
    ent = [(c.name, list(c.slots)) for c in cs]
    assert T.load(T.dump(ent)) == ent, T.load(T.dump(ent))
    assert T.load({}) == [] and T.load(None) == []
    assert T.load({"X": [{"d": "nope"}, {"h": 1}]}) == []      # 坏条目跳过，不抛


def main_() -> int:

    print("=" * 60)
    for name, fn in CASES:
        try:
            fn()
        except AssertionError as e:
            print(f"  ❌ {name}\n      {str(e)[:220]}")
            FAIL.append(name)
        except Exception as e:                                 # noqa: BLE001
            print(f"  ❌ {name}\n      {type(e).__name__}: {str(e)[:200]}")
            FAIL.append(name)
        else:
            print(f"  ✅ {name}")
    print("=" * 60)
    print(f"{len(CASES) - len(FAIL)}/{len(CASES)} 通过")
    for n in FAIL:
        print(f"  ❌ {n}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main_())
