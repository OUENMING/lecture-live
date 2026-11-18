#!/usr/bin/env python3
"""首页数据层（`home_model`）：**纯函数那半**的判据。

    ClassLive.app/Contents/MacOS/python tests/test_home_model.py

## 钉住的是什么

1. ⭐ **顺序永不重排** —— 猜中的那门在第 6 位就还在第 6 位。数字键 1–9 与格子的
   对应关系是**空间记忆**，一旦按猜测重排，`1` 一会儿是这门一会儿是那门。
2. ⭐ **三档证据各有各的出口**：`now`/`soon` 出顶部条 + 标亮那格；
   `last`（弱证据）**只标那格**、不出顶部条；无证据两边都不出。
3. **`0 份课件 · 0 条术语` 不许出现**（`meta` 为 None）；而「未知」（`None`）要画 `—`
   —— 两者混了就是跟用户谎称「这门课一个词都没有」。
4. `next_slot` 的**双周课 / 停课日**必须和 `entry_panel.likely_course` 同一个口径
   （都走 `timetable.Slot.on_week`），本文件用同一份课表分别喂两个函数对照。
5. `last_text` 的**边界**（0 / 1 / 6 / 7 / 13 / 14 / 55 / 56 天）逐个钉住。

⚠️ 本文件**不读盘、不写盘**（`home_model` 是纯函数，`rows` / 课表 / 时刻全在内存里造）。
⚠️ `home_model` 只在 `build()` 里**延迟** import `entry_panel`（那个模块 import 期会拖进
   AppKit）—— 所以第一节先钉住「只 import `home_model` 不会加载 AppKit」。
"""
from __future__ import annotations

import datetime
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import courses                                                     # noqa: E402
import home_model as H                                             # noqa: E402
import timetable as T                                              # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  {detail}" if detail else ""))


def R(course="ECON10740", title="Exploring Economics", terms=36, materials=3,
      auto=0, last="2026-09-22"):
    return courses.Readiness(course, title, terms, materials, auto, last)


def _slot(hh, mm, *, minutes=60, weekday=1, iv=1, anchor=None, skip=()):
    """默认锚在 **2026-10-13（周二）** —— 双周课的相位从这里算。"""
    return T.Slot(weekday, hh, mm, minutes, interval=iv,
                  anchor=anchor or datetime.date(2026, 10, 13), skip=tuple(skip))


#: 2026-10-20 是**周二**。全天判据都从这个「此刻」出发。
NOW = datetime.datetime(2026, 10, 20, 10, 35)
TODAY = NOW.date()


def _tile(m, code):
    for t in m.tiles:
        if t.code == code:
            return t
    return None


def main() -> int:
    # ───────────────────────────────────────────────────── ① 模块边界
    print("\n--- ① `home_model` 自己不许把 AppKit 拖进来 ---")
    import subprocess
    code = ("import sys; sys.path.insert(0, %r); import home_model; "
            "print('AppKit' in sys.modules)" % str(HERE))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    check("⭐ 只 import `home_model` → **不**加载 AppKit（纯数据层能单独跑）",
          out.stdout.strip() == "False", (out.stdout.strip() or out.stderr.strip()[-120:]))

    # ───────────────────────────────────────────── ② 三档 + 无证据
    print("\n--- ② 三档证据各有各的出口 ---")
    _ent_now = [("AA10010", [_slot(10, 0, minutes=60)])]          # 10:00 开、60 分钟 → 正开着
    m = H.build([R(course="AA10010")], _ent_now, NOW)
    check("⭐ ① 正开着 → 出顶部条，tier=now",
          m.suggest is not None and m.suggest.code == "AA10010"
          and m.suggest.tier == "now", str(m.suggest))
    check("⭐ 顶部条的钟点 = **排课那节的开始时间**（不是此刻 10:35）",
          m.suggest is not None and m.suggest.clock == "10:00",
          str(m.suggest.clock if m.suggest else None))
    check("① 已开始多久进 `rel`（10:35 − 10:00 = 35 分钟）",
          m.suggest is not None and m.suggest.rel == "已开始 35 分钟",
          str(m.suggest.rel if m.suggest else None))
    check("① 那门格子同时标亮 mark=now",
          _tile(m, "AA10010") is not None and _tile(m, "AA10010").mark == "now",
          str(_tile(m, "AA10010")))

    _ent_soon = [("AA10010", [_slot(10, 50, minutes=60)])]        # 15 分钟后要开
    m = H.build([R(course="AA10010")], _ent_soon, NOW)
    check("② 20 分钟内要开 → 也出顶部条，tier=soon + 「还有 15 分钟」",
          m.suggest is not None and m.suggest.tier == "soon"
          and m.suggest.clock == "10:50" and m.suggest.rel == "还有 15 分钟",
          str(m.suggest))
    check("② 那格同样标亮 mark=soon",
          _tile(m, "AA10010") is not None and _tile(m, "AA10010").mark == "soon")

    m = H.build([R(course="AA10010", last="2026-10-19")], [], NOW, last_used="AA10010")
    check("⭐ ③ 只有「上次上过」（弱证据）→ **不出**顶部条",
          m.suggest is None, str(m.suggest))
    check("③ 但那一格要标「上次」（mark=last）",
          _tile(m, "AA10010") is not None and _tile(m, "AA10010").mark == "last",
          str(_tile(m, "AA10010")))

    m = H.build([R(course="AA10010", last=None)], [], NOW)
    check("④ 无证据（没课表、没上过）→ 顶部条 None、格子 mark None",
          m.suggest is None and _tile(m, "AA10010").mark is None,
          f"{m.suggest} / {_tile(m, 'AA10010').mark}")

    # ────────────────────────────────── ③ 顺序：猜中的那门**不往前挪**
    print("\n--- ③ 顺序固定（空间记忆 / 数字键）---")
    _rows6 = [R(course=f"CC1000{i}", last=None) for i in range(1, 7)]
    _ent6 = [("CC10006", [_slot(10, 0, minutes=60)])]             # 第 6 位那门正开着
    m = H.build(_rows6, _ent6, NOW)
    check("⭐⭐ 猜中的课在第 6 位 → **仍在第 6 位**（1..6 原样）",
          [t.code for t in m.tiles]
          == [f"CC1000{i}" for i in range(1, 7)],
          str([t.code for t in m.tiles]))
    check("⭐ 而且被猜中的确实是第 6 位那门（`CC10006`），它的 key 还是 6",
          m.suggest is not None and m.suggest.code == "CC10006"
          and _tile(m, "CC10006").key == 6, str(m.suggest))
    check("① 第 1 位那门的 key 仍是 1（没被换走）", m.tiles[0].key == 1)

    _rows11 = [R(course=f"DD{10000 + i}", last=None) for i in range(11)]
    m = H.build(_rows11, [], NOW)
    check("⭐ key 只到 9：前 9 个是 1..9，第 10 个起 None",
          [t.key for t in m.tiles]
          == [1, 2, 3, 4, 5, 6, 7, 8, 9, None, None],
          str([t.key for t in m.tiles]))

    # ─────────────────────────────────────────────── ④ meta
    print("\n--- ④ meta：0 不说废话、未知画「—」---")
    m = H.build([R(course="AA10010", materials=14, terms=92)], [], NOW)
    check("有课件/术语 → 「14 份课件 · 92 条术语」",
          _tile(m, "AA10010").meta == "14 份课件 · 92 条术语",
          str(_tile(m, "AA10010").meta))
    _m0 = H.build([R(course="AA10010", materials=0, terms=0)], [], NOW)
    check("⭐⭐ 两个都是 0 → meta **None**（不许显示「0 份课件 · 0 条术语」）",
          _tile(_m0, "AA10010").meta is None, repr(_tile(_m0, "AA10010").meta))
    check("⚠️ 判据形状：同一条路只换 0 / 14 → 输出不同（不是恒 None 的假测试）",
          _tile(m, "AA10010").meta != _tile(_m0, "AA10010").meta)
    _mu = H.build([R(course="AA10010", materials=None, terms=5)], [], NOW)
    check("⭐ 未知（None）→ 「— 份课件 · 5 条术语」，**不是 0**",
          _tile(_mu, "AA10010").meta == "— 份课件 · 5 条术语",
          str(_tile(_mu, "AA10010").meta))
    _mt = H.build([R(course="AA10010", materials=2, terms=None)], [], NOW)
    check("另一头未知也一样（术语读不出 → 「— 条术语」）",
          _tile(_mt, "AA10010").meta == "2 份课件 · — 条术语",
          str(_tile(_mt, "AA10010").meta))
    check("⚠️ 未知 ≠ 零：None 那一支带「—」，0 那一支带「0」",
          _tile(_mu, "AA10010").meta != "0 份课件 · 5 条术语")

    # ───────────────────────────────────────────── ⑤ next_slot
    print("\n--- ⑤ next_slot：今天稍后 / 明天 / 周几 / 双周 / 停课 ---")
    check("今天稍后（周二 14:00，此刻 10:35）→ 「今天 14:00」",
          _tile(H.build([R(course="AA10010")],
                        [("AA10010", [_slot(14, 0)])], NOW),
                "AA10010").next_slot == "今天 14:00",
          str(_tile(H.build([R(course="AA10010")],
                           [("AA10010", [_slot(14, 0)])], NOW), "AA10010").next_slot))
    check("明天（周三 9:00）→ 「明天 9:00」（小时**不补零**）",
          _tile(H.build([R(course="AA10010")],
                        [("AA10010", [_slot(9, 0, weekday=2)])], NOW),
                "AA10010").next_slot == "明天 9:00",
          str(_tile(H.build([R(course="AA10010")],
                           [("AA10010", [_slot(9, 0, weekday=2)])], NOW),
                    "AA10010").next_slot))
    check("周四（day+2）→ 「周四 10:00」",
          _tile(H.build([R(course="AA10010")],
                        [("AA10010", [_slot(10, 0, weekday=3)])], NOW),
                "AA10010").next_slot == "周四 10:00",
          str(_tile(H.build([R(course="AA10010")],
                           [("AA10010", [_slot(10, 0, weekday=3)])], NOW),
                    "AA10010").next_slot))
    _mw = H.build([R(course="AA10010")], [("AA10010", [_slot(10, 0)])], NOW)
    check("⭐ 正开着的那节**不算**「下一次」（周二 10:00 已开 → 给下周二）",
          _tile(_mw, "AA10010").next_slot == "10月27日 10:00",
          str(_tile(_mw, "AA10010").next_slot))
    check("没课表 → next_slot None",
          _tile(H.build([R(course="AA10010")], [], NOW), "AA10010").next_slot is None)

    # 双周课：anchor = 2026-10-13，interval=2 → 10-13 上、10-20 **不上**、10-27 上
    _bi = [("AA10010", [_slot(10, 0, iv=2)])]
    check("⭐⭐ 双周课：这一周不上（10-20 不上）→ 跳到 10月27日，不是「今天」",
          _tile(H.build([R(course="AA10010")], _bi, NOW),
                "AA10010").next_slot == "10月27日 10:00",
          str(_tile(H.build([R(course="AA10010")], _bi, NOW), "AA10010").next_slot))
    check("⭐⭐ 双周课（今天 08:00，课还没开）：不上那周 → 今天**没有**这节，给 10月27日",
          _tile(H.build([R(course="AA10010")], _bi,
                        datetime.datetime(2026, 10, 20, 8, 0)),
                "AA10010").next_slot == "10月27日 10:00",
          str(_tile(H.build([R(course="AA10010")], _bi,
                            datetime.datetime(2026, 10, 20, 8, 0)),
                    "AA10010").next_slot))
    check("⭐⭐ 双周课：轮到的那一周（10-27 当天 09:00）→ 「今天 10:00」",
          _tile(H.build([R(course="AA10010")], _bi,
                        datetime.datetime(2026, 10, 27, 9, 0)),
                "AA10010").next_slot == "今天 10:00",
          str(_tile(H.build([R(course="AA10010")], _bi,
                            datetime.datetime(2026, 10, 27, 9, 0)),
                    "AA10010").next_slot))
    check("⚠️ 而且与 `entry_panel.likely_course` 同源：双周不上那周，现在**没有**强证据",
          H.build([R(course="AA10010")], _bi, NOW).suggest is None,
          str(H.build([R(course="AA10010")], _bi, NOW).suggest))

    # 停课日：今天那节被 EXDATE 掉 → 跳到下周同一时刻
    _sk = [("AA10010", [_slot(10, 0, minutes=60,
                             skip=(datetime.date(2026, 10, 20),))])]
    _ms = H.build([R(course="AA10010")], _sk, NOW)
    check("⭐ 停课日（今天 10:00 停课）→ 顶部条不出、next 跳到 10月27日",
          _ms.suggest is None
          and _tile(_ms, "AA10010").next_slot == "10月27日 10:00",
          f"{_ms.suggest} / {_tile(_ms, 'AA10010').next_slot}")
    _ms2 = H.build([R(course="AA10010")], _sk,
                   datetime.datetime(2026, 10, 20, 8, 0))
    check("⭐ 停课日（课还没开的 08:00）：今天那节被 EXDATE 撑掉 → 也给 10月27日",
          _tile(_ms2, "AA10010").next_slot == "10月27日 10:00",
          str(_tile(_ms2, "AA10010").next_slot))

    # ───────────────────────────────────────────── ⑥ last_text 边界
    print("\n--- ⑥ last_text 的各道边界（今天 = 2026-10-20）---")
    _cases = [
        (0, "今天", "2026-10-20"),
        (1, "昨天", "2026-10-19"),
        (6, "6 天前", "2026-10-14"),
        (7, "上周", "2026-10-13"),
        (13, "上周", "2026-10-07"),
        (14, "2 周前", "2026-10-06"),
        (55, "7 周前", "2026-08-26"),
        (56, "8月25日", "2026-08-25"),
    ]
    for _days, _want, _iso in _cases:
        got = _tile(H.build([R(course="AA10010", last=_iso)], [], NOW),
                    "AA10010").last_text
        check(f"上次 {_days} 天前 → 「{_want}」", got == _want, f"给的是 {got!r}")
    check("从没上过 → last_text None",
          _tile(H.build([R(course="AA10010", last=None)], [], NOW),
                "AA10010").last_text is None)
    check("日期形状坏掉 → 原样返回、不抛",
          _tile(H.build([R(course="AA10010", last="坏日期")], [], NOW),
                "AA10010").last_text == "坏日期",
          str(_tile(H.build([R(course="AA10010", last="坏日期")], [], NOW),
                    "AA10010").last_text))

    # ───────────────────────────────────────────── ⑦ first_run
    print("\n--- ⑦ first_run ---")
    _fr = [R(course="AA10010", terms=0, materials=0, last=None),
           R(course="BB20020", terms=0, materials=0, last=None)]
    check("⭐ 全 0 + 一节都没上过 → first_run=True",
          H.build(_fr, [], NOW).first_run is True)
    check("空 rows 也算全新用户（不能是一片空白）",
          H.build([], [], NOW).first_run is True)
    check("有一份课件 → first_run=False",
          H.build([*_fr, R(course="CC30030", terms=0, materials=1, last=None)],
                  [], NOW).first_run is False)
    check("有一条术语 → first_run=False",
          H.build([*_fr, R(course="CC30030", terms=1, materials=0, last=None)],
                  [], NOW).first_run is False)
    check("上过一次课 → first_run=False",
          H.build([R(course="AA10010", terms=0, materials=0, last="2026-10-19")],
                  [], NOW).first_run is False)

    # ───────────────────────────────── ⑧ 两个时间都拿得到时：clock 用排课时刻
    print("\n--- ⑧ 「两个时间都拿得到」时的口径 ---")
    _m = H.build([R(course="AA10010")], [("AA10010", [_slot(10, 20, minutes=60)])], NOW)
    check("⭐ clock 用**课表那节的开始时刻**（10:20），rel 用与此刻的差（15 分钟）",
          _m.suggest is not None and _m.suggest.clock == "10:20"
          and _m.suggest.rel == "已开始 15 分钟", str(_m.suggest))
    _m2 = H.build([R(course="AA10010")], [("AA10010", [_slot(10, 20, minutes=60)])],
                  datetime.datetime(2026, 10, 20, 10, 20))
    check("恰好在开始那一刻 → 「正在上」（不是「已开始 0 分钟」）",
          _m2.suggest is not None and _m2.suggest.rel == "正在上", str(_m2.suggest))

    bad = [n for n, ok, _ in RESULTS if not ok]
    print(f"{len(RESULTS) - len(bad)}/{len(RESULTS)} 通过")
    for n in bad:
        print(f"  ❌ {n}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
