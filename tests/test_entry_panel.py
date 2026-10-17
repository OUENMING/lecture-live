#!/usr/bin/env python3
"""课程卡片面板：**纯函数那半**（不碰 AppKit 也能钉住的判据）。

    ClassLive.app/Contents/MacOS/python tests/test_entry_panel.py

## 为什么只测这半

面板本体是 AppKit 装配，`tests/test_panel.py` 已经在管「配方没被我改坏」。
但卡片上**显示什么字**是纯逻辑，而且是最容易悄悄错的地方 —— 它们全部抽成了
模块级函数，在这里逐条钉住：

1. ⭐ **「未知」与「零」必须显示成两样**（`— 条术语` vs `0 条术语`）。
   把读失败显示成 0 = 跟用户谎称「这门课一个词都没有」。
   ⭐ **判据形状：同一个函数、只换 `None` 和 `0`、断言输出不同** ——
   如果只测了 `None` 那一支，改成 `0` 也能过（那就是假测试）。
2. ⚠️ **课号不许显示两遍**（术语表首行本来就带课号）。跑第一遍时真发生过。
3. **不认识的 stage 也要显示点什么** —— 显示空白会让人以为卡住了。
4. **body 高度**：装得下就长，装不下才滚（第 N+1 门课不许出现断崖）。

⚠️ 本文件**不起事件循环**（`entry_panel` 的 AppKit 全在函数内 import，所以能这么测）。
   ⚠️ **唯一例外是最后一节 ⑬**：它**要建窗口**，因为那一节钉的是「新增课程」的
   **接线**（按钮 target → `submit_add` → 落盘），而接线坏掉时不报错、纯函数全绿，
   症状统一是「点了没反应」。它照样**不跑事件循环**，写端全隔离在 tempdir。
"""
from __future__ import annotations

import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import courses                                                     # noqa: E402
import entry_panel as E                                            # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  {detail}" if detail else ""))


def R(course="ECON10740", title="Exploring Economics", terms=36,
      materials=0, auto=0, last="2026-09-22"):
    return courses.Readiness(course, title, terms, materials, auto, last)


class FakeRes:
    def __init__(self, **kw):
        self.aborted = kw.get("aborted", False)
        self.added = kw.get("added", [])
        self.not_added = kw.get("not_added", [])
        self.skipped_existing = kw.get("skipped_existing", 0)
        self.notes_added = kw.get("notes_added", None)


def main() -> int:
    print("\n--- ① 课号不许显示两遍 ---")
    check("首行带课号 -> 不再加前缀",
          E.card_title(R(title="ECON10740 Exploring Economics"))
          == "ECON10740 Exploring Economics",
          E.card_title(R(title="ECON10740 Exploring Economics")))
    check("首行不带课号 -> 补上前缀",
          E.card_title(R(title="Advanced Topics")) == "ECON10740   Advanced Topics")
    check("课号是**别门课**的前缀时也不误判",
          E.card_title(R(course="ECON1074", title="ECON10740 X")) == "ECON1074   ECON10740 X")
    # ⚠️ 边界**只认 ASCII**：中文也是 `isalnum()`，紧跟中文正是「课号 + 课名」的常态写法，
    #    判成"课号是更长前缀"就会把课号拼两遍（2026-09-28 审查指出）。
    check("⭐ 课号后面紧跟中文（无空格）也不拼两遍",
          E.card_title(R(title="ECON10740经济学原理")) == "ECON10740经济学原理",
          E.card_title(R(title="ECON10740经济学原理")))
    check("标题空 -> 只显示课号", E.card_title(R(title="")) == "ECON10740")
    check("包中文的半角括号换全角（显示用）",
          E.card_title(R(course="SOC10020", title="SOC10020 Introduction to Sociology(社会学导论)"))
          == "SOC10020 Introduction to Sociology（社会学导论）",
          E.card_title(R(course="SOC10020", title="SOC10020 Introduction to Sociology(社会学导论)")))
    check("纯英文括号不动", E.card_title(R(title="ECON10740 Economics (Part 2)"))
          == "ECON10740 Economics (Part 2)")

    print("\n--- ② ⭐「未知」与「零」必须显示成两样 ---")
    zero = E.readiness_line(R(terms=0, materials=0))
    unknown = E.readiness_line(R(terms=None, materials=None))
    check("零 -> 写 0", "0 条术语" in zero and "0 份课件" in zero, zero)
    check("⭐ 未知 -> 写 —，不写 0", "— 条术语" in unknown and "— 份课件" in unknown,
          unknown)
    check("⭐ 两者输出**必须不同**（只测一支 = 假测试）", zero != unknown)
    check("零里不许出现 —", "—" not in zero, zero)

    print("\n--- ③ 日期 ---")
    check("2026-09-22 -> 9/22", E._short_date("2026-09-22") == "9/22")
    check("None -> 从没上过", E._short_date(None) == "从没上过")
    check("形状不对 -> 原样返回，不炸", E._short_date("garbage") == "garbage")
    check("readiness_line 里也生效", "上次上课 9/22" in E.readiness_line(R()))

    print("\n--- ③b 面板复用键必须含两个**写端注入点**（2026-10-01 审查 #29）---")
    # ⚠️ 动机：隔离跑器用 `prepare_fn`/`trash_fn` 把写端换成桩。键里少这两项时，
    #    同进程里先用真写端开过面板、再拿桩写端开 → **复用旧面板**、桩根本没生效
    #    → 拖文件会走真 `prep.prepare` 写真实术语表（踩「测试必须隔离写端」硬规矩）。
    #    改坏实现（把这两项从 `E._panel_key` 删掉）→ 前两条红。
    _wfn = lambda *a, **k: None                            # noqa: E731
    _pbase = {"glossary": "g", "state_root": "s", "sessions_dir": "d"}
    _k0 = E._panel_key(dict(_pbase))
    check("⭐⭐ 有 `prepare_fn` -> 复用键必须不同（否则桩写端被真实面板复用）",
          _k0 != E._panel_key({**_pbase, "prepare_fn": _wfn}), f"{_k0}")
    check("⭐⭐ `trash_fn` 同理",
          _k0 != E._panel_key({**_pbase, "trash_fn": _wfn}))
    check("⚠️ 既有口径不许被改坏：`on_start` 的存在与否仍改变键",
          _k0 != E._panel_key({"on_start": _wfn, **_pbase}))

    print("\n--- ④ 进度文案 ---")
    check("认识的 stage -> 中文名", E.progress_text("extract", 3, 7) == "抽文本 3/7…")
    check("total=0 -> 不带分母", E.progress_text("build", 0, 0) == "生成中文释义…")
    check("⚠️ 不认识的 stage -> 显示原名，**不留空白**",
          E.progress_text("weird", 1, 2) == "weird 1/2…")
    # ⭐⭐ 反向判据：面板的表**就是** `prep.STAGE_NAME`（一份实现）。
    #    ⚠️ 这条原来钉的是 `progress_text("notes", …) == "生成释义…"` —— 而 `notes`
    #    **正是面板自己抄的那张表里的死键**（`prep.STAGE_NAME` 用的是 `build`）。
    #    于是那条断言在**钉住缺陷本身**：换掉面板的表之后它才红。
    import prep as _prep
    _bad = [k for k in _prep.STAGE_NAME if E.progress_text(k, 1, 2).startswith(k)]
    check("⭐ `prep.STAGE_NAME` 的每个 key 都落成中文（没有退化成英文原名）",
          not _bad, str(_bad))
    check("⭐ 面板不再认识那个死键 `notes`（它本就不在 prep 的表里）",
          E.progress_text("notes", 0, 0) == "notes…")

    print("\n--- ⑤b 整块面板不超过可视区 92%（P1-c）---")
    _oh = 230.0
    _worst = [(vh, n) for vh in (600.0, 700.0, 871.0, 1200.0) for n in (0, 1, 5, 7, 30)
              if E.fit_body(E.body_height(n, vh), _oh, vh, E.CARD_H) + _oh > vh * 0.92 + 0.01
              and E.fit_body(E.body_height(n, vh), _oh, vh, E.CARD_H) > E.CARD_H + 0.01]
    check("⭐ 各屏高 × 各课数：卡片区封顶后整块 ≤ 可视区 92%（只有一张卡也放不下时才例外）",
          not _worst, str(_worst))
    check("⭐ 再小也至少留一张卡", E.fit_body(500.0, 900.0, 600.0, E.CARD_H) == E.CARD_H)
    check("装得下时不动卡片区", E.fit_body(300.0, _oh, 1200.0, E.CARD_H) == 300.0)
    check("面板 y：可视区底边以上，顶边离可视区顶 PANEL_TOP_GAP",
          E.panel_y(500.0, 80.0, 900.0) == 80.0 + 900.0 - 500.0 - E.PANEL_TOP_GAP)
    check("面板 y：放不下时贴可视区底边（不钻进 Dock）", E.panel_y(880.0, 80.0, 900.0) == 80.0)
    check("零课程提示卡装得下三行字", E.empty_card_height() >= E.CARD_PAD * 2 + E.L1_H + 2 * E.L2_H)

    print("\n--- ⑤ body 高度：装得下就长，装不下才滚 ---")
    natural5 = 5 * (E.CARD_H + E.CARD_GAP) - E.CARD_GAP
    check("5 门课 / 982 屏 -> 恰好装下（不留半张卡）",
          E.body_height(5, 982) == natural5, str(E.body_height(5, 982)))
    check("1 门课 -> 一张卡的高度", E.body_height(1, 982) == E.CARD_H)
    big = E.body_height(30, 982)
    check("30 门课 -> 封顶到屏幕的一部分（开始滚）",
          0 < big < 30 * (E.CARD_H + E.CARD_GAP), str(big))
    check("小屏上也不超过屏幕比例", E.body_height(30, 600) <= 600 * 0.61,
          str(E.body_height(30, 600)))
    check("⭐ 不出现「第 N+1 门课断崖」：5→6 是连续的",
          E.body_height(6, 982) - E.body_height(5, 982) > 0
          or E.body_height(6, 982) >= 982 * 0.60 - 1,
          f"{E.body_height(5, 982)} -> {E.body_height(6, 982)}")

    print("\n--- ⑥ 结果汇总：计数必须与实际相符 ---")
    check("全空 -> 只报加了 0 个",
          E.summarize(FakeRes()) == "加了 0 个词", E.summarize(FakeRes()))
    s = E.summarize(FakeRes(added=list("abc"), not_added=list("de"), notes_added=3))
    check("加了 3 / 没加 2 / 释义 3 三项都在",
          "加了 3" in s and "另有 2" in s and "释义 +3" in s, s)
    check("被中止 -> 单独报「没跑完」",
          E.summarize(FakeRes(added=list("a"), aborted="no_api_key")).startswith("没跑完"),
          E.summarize(FakeRes(added=list("a"), aborted="no_api_key")))
    check("计数为 0 的项不出现（别塞一堆 0）",
          "另有 0" not in E.summarize(FakeRes(added=list("a"))))

    print("\n--- ⑦ 结果列表：计数与列表**必须同源** ---")
    e = {"added": ["a", "b", "c"], "removed": set(), "not_added": [], "failed": []}
    check("没删过 -> 三条都在", E.kept_added(e) == ["a", "b", "c"], str(E.kept_added(e)))
    e["removed"] = {"b"}
    check("删过的**不再列**（列表跟着走）", E.kept_added(e) == ["a", "c"],
          str(E.kept_added(e)))
    check("⭐ 头部计数 == 列表长度（**同一个来源**）",
          f"本次加了 {len(E.kept_added(e))} 个" in E.result_header(e), E.result_header(e))
    # ⭐ 这一条是防 Anki / LingQ 那两次事故的：
    #    「18 added, 148 updated」和「says 1 new word but it does not」都是**计数撒谎**，
    #    而根因是计数和列表各算一遍。这里把它们逼成同一个来源，逐个规模都验一遍。
    check("⭐ 对每个删除规模，计数都与列表对得上（各算一遍必在这里红）",
          all(f"本次加了 {len(E.kept_added({'added': ['a', 'b', 'c', 'd'],
                                           'removed': set(list('abcd')[:k])}))} 个"
              in E.result_header({'added': ['a', 'b', 'c', 'd'],
                                  'removed': set(list('abcd')[:k])})
              for k in range(5)))
    check("删光了 -> 加了 0 个，且列表是空的",
          E.kept_added({"added": ["a"], "removed": {"a"}}) == []
          and "本次加了 0 个" in E.result_header({"added": ["a"], "removed": {"a"}}))
    check("「没加」只在非空时才出现",
          "没加" not in E.result_header({"added": ["a"]})
          and "没加" in E.result_header({"added": ["a"], "not_added": ["x"]}))
    # ⚠️⚠️ 这条原来喂的是 `("f", "why")` 这个**元组** —— 生产**永远不产生**这个形状
    #     （`failed` 的元素是 `prep.FileReport`）。于是它只钉住了计数串，还**遮住了
    #     形状不符**：换上真形状才知道渲染得对不对。2026-09-28 换成真形状 + 加守卫。
    import prep as _prep
    _fr = _prep.FileReport(path=pathlib.Path("/x/讲义.docx"), status="unsupported",
                           chars=0, blocks=0, skipped_shapes=0, ocr_pages=0,
                           error="不支持的格式：.docx")
    check("⭐ 失败夹具是生产**真形状**（`prep.FileReport`，不是一个随手元组）",
          isinstance(_fr, _prep.FileReport) and hasattr(_fr, "error"),
          type(_fr).__name__)
    check("失败项单独报（逐文件那条的落点）",
          "1 个文件失败" in E.result_header({"added": [], "failed": [_fr]}),
          E.result_header({"added": [], "failed": [_fr]}))
    check("失败行 = `⚠️ 文件名 — 原因`",
          E.failure_text(_fr) == "⚠️ 讲义.docx — 不支持的格式：.docx",
          E.failure_text(_fr))
    check("原因取 `error`；`error` 空才退 `status`（安全网）",
          E.failure_text(_prep.FileReport(
              path=pathlib.Path("/x/a.pdf"), status="unreadable", chars=0, blocks=0,
              skipped_shapes=0, ocr_pages=0, error="")) == "⚠️ a.pdf — unreadable")
    check("⭐ 失败行必须**单行**（那框只有 16pt，换行会被静默吃掉）",
          "\n" not in E.failure_text(_prep.FileReport(
              path=pathlib.Path("/x/b.pdf"), status="unreadable", chars=0, blocks=0,
              skipped_shapes=0, ocr_pages=0, error="ValueError: 一\n二")))
    # ⭐ `_ABORT_MSG` 的人话：**不许把代号漏给用户**（`§9.2 #5` 那条：
    #    用户看到的是 `all_files_failed` 这种机器名）
    _leak = [c for c in sorted(_prep.ABORT_MSG) if E.abort_msg(c) == c]
    check("⭐ 每条 abort 代号都有中文人话（不是原代号）", not _leak, str(_leak))
    check("未知代号退原名（宁可难看也别空白）",
          E.abort_msg("something_new") == "something_new")
    check("⭐ 卡片头只说状态 —— 整句人话落成结果区的一行",
          E.result_header({"added": [], "aborted": "all_files_failed"})
          == "本次加了 0 个　·　⚠️ 没跑完",
          E.result_header({"added": [], "aborted": "all_files_failed"}))
    # ⚠️ 原来这里是两条**恒真**的断言（OCR 指出）：一条 `check(..., True)`，
    #    一条断言的是 Python 字面量 `{"a","a"}` 的去重语义 —— **完全没碰被测代码**。
    #    改成真的调用 + 断言返回值，以及走真实路径验「删过的词只算一次」。
    _out, _err = [], ""
    for junk in ({}, None, {"added": None, "removed": None}):
        try:
            _out.append(E.result_header(junk))
        except Exception as e:                            # noqa: BLE001
            _err = f"{type(e).__name__}: {e}"
            break
    check("空/None entry 不抛，且返回的是**可显示的一行**",
          not _err and len(_out) == 3 and all(isinstance(x, str) and x for x in _out),
          _err or str(_out))
    _dup = {"added": ["a", "b"], "removed": ["a", "a"]}   # 重复项：不是 set
    check("⚠️ `removed` 传成**含重复项的 list** 也照样对（走真实路径）",
          E.kept_added(_dup) == ["b"] and "本次加了 1 个" in E.result_header(_dup),
          f"{E.kept_added(_dup)} / {E.result_header(_dup)}")

    print("\n--- ⑧b 数字键选课 / 回车开课：**键事件** -> 动作 ---")
    # ⚠️ 判据落在 `key_intent_from_event` 上（真 `NSEvent` 与假事件同形），
    #    不是「有没有装监视器」——装上了但映射错，用户按 2 选中的是第 3 门，
    #    而那种错**不会报错**。
    class _Ev:
        def __init__(self, ch, mods=0):
            self._ch, self._mods = ch, mods

        def charactersIgnoringModifiers(self):
            return self._ch

        def modifierFlags(self):
            return self._mods

    _order = ["AA10010", "BB20020", "CC30030"]
    check("按 1 / 2 / 3 -> 选中第 1 / 2 / 3 门",
          [E.key_intent_from_event(_Ev(str(i)), _order) for i in (1, 2, 3)]
          == [("select", c) for c in _order],
          str([E.key_intent_from_event(_Ev(str(i)), _order) for i in (1, 2, 3)]))
    check("按 4（只有 3 张卡）-> 不动", E.key_intent_from_event(_Ev("4"), _order) is None)
    check("按 0 / 6 / 字母 / 空格 -> 一律不处理",
          all(E.key_intent_from_event(_Ev(c), _order) is None
              for c in ("0", "6", "a", " ")))
    # ⭐ 2026-10-18：回车不再是「选中上次那门」，而是**直接开课**（用当前选中的那门）。
    check("⭐ 回车 -> **开始上课**那门（不再只是选中）",
          E.key_intent_from_event(_Ev("\r"), _order, "BB20020") == ("start", "BB20020"),
          str(E.key_intent_from_event(_Ev("\r"), _order, "BB20020")))
    check("回车键的另一种写法（keypad Enter = `\\n`）同样 -> 开始",
          E.key_intent_from_event(_Ev("\n"), _order, "BB20020") == ("start", "BB20020"))
    check("回车但没有选中的那门 -> 不动（不猜第一张）",
          E.key_intent_from_event(_Ev("\r"), _order, None) is None)
    check("⚠️ 选中的课不在表里（刚被删）-> 不开始",
          E.key_intent_from_event(_Ev("\r"), _order, "ZZ99999") is None)
    check("⚠️ 输入框有焦点 -> 一个键都不抢（1 / 5 / 回车）",
          all(E.key_intent_from_event(_Ev(c), _order, _order[0], editing=True) is None
              for c in ("1", "5", "\r")))
    check("⚠️ 带 ⌘ / ⌃ / ⌥ -> 不抢（那是系统快捷键）",
          all(E.key_intent_from_event(_Ev("1", m), _order) is None
              for m in (1 << 20, 1 << 18, 1 << 19)))
    check("事件形状读不出来 -> 不处理（不抛）",
          E.key_intent_from_event(object(), _order) is None)
    _rows = [R(course="A", last="2026-09-01"), R(course="B", last="2026-10-16"),
             R(course="C", last=None)]
    check("⭐ 上次那门 = 最近一次真上过课的那门",
          E.last_course_of(_rows) == "B", str(E.last_course_of(_rows)))
    check("一门都没上过 -> None", E.last_course_of([R(course="A", last=None)]) is None)

    print("\n--- ⑧c 「最可能那门」的排法（纯函数；now / 课表 / 上次那门全注入）---")
    # 2026-10-18 是周日；造一个**周二 10:35** 的时刻。
    import datetime as _dt
    import timetable as _T
    _now = _dt.datetime(2026, 10, 20, 10, 35)

    def _slot(hh, mm, *, minutes=60, weekday=1, iv=1, anchor=None, skip=()):
        return _T.Slot(weekday, hh, mm, minutes, interval=iv,
                       anchor=anchor or _dt.date(2026, 10, 13), skip=tuple(skip))

    def _lc(entries, when, **kw):
        got = E.likely_course(entries, when, **kw)
        return (got.course, got.why) if got else None

    def _lc_course(entries, when, **kw):
        """只要课号（None 安全 —— 判据**不许因为被测函数返回 None 而崩**）。"""
        got = E.likely_course(entries, when, **kw)
        return got.course if got else None

    check("① 正开着的课（10:00 开始、60 分钟）-> 就是它",
          _lc([("AA10010", [_slot(10, 0)])], _now) == ("AA10010", "课表上 10:00 是这门"),
          str(_lc([("AA10010", [_slot(10, 0)])], _now)))
    check("① 跨过整点的长课也算正开着（9:30 开始、120 分钟）",
          _lc_course([("AA10010", [_slot(9, 30, minutes=120)])], _now) == "AA10010")
    check("① 时长没写（0）-> 退回「同一小时」老口径（10:05 算开着）",
          _lc_course([("AA10010", [_slot(10, 5, minutes=0)])], _now) == "AA10010")
    check("② 20 分钟内要开（10:50）-> 也算它",
          _lc_course([("AA10010", [_slot(10, 50)])], _now) == "AA10010")
    check("② 21 分钟后（10:56）-> 不算「马上要上」",
          _lc([("AA10010", [_slot(10, 56)])], _now) is None)
    check("① 正开着 > ② 20 分钟内要开（现在这节赢，不是下一节）",
          _lc_course([("AA10010", [_slot(9, 0, minutes=120)]),
                      ("BB20020", [_slot(10, 50)])], _now) == "AA10010")
    check("③ 课表全不命中 -> 上次那门",
          _lc([("AA10010", [_slot(14, 0)])], _now,
              last_used="CC30030") == ("CC30030", "上次上的是这门"))
    check("③ 没有课表、只有历史 -> 上次那门",
          _lc_course([], _now, last_used="CC30030") == "CC30030")
    check("④ 课表/历史都没有 -> None（没证据就不猜）",
          _lc([], _now) is None)
    check("⚠️ 双周课这周不上（iv=2、锚点落在另一周）-> 不算",
          _lc([("AA10010", [_slot(10, 0, iv=2, anchor=_dt.date(2026, 10, 13))])],
              _now) is None)
    check("⚠️ 对照：双周课的「该上那周」-> 照样命中",
          _lc_course([("AA10010", [_slot(10, 0, iv=2, anchor=_dt.date(2026, 10, 6))])],
                     _now) == "AA10010")
    check("⚠️ 停课日（EXDATE）-> 不算",
          _lc([("AA10010", [_slot(10, 0, skip=[_dt.date(2026, 10, 20)])])],
              _now) is None)
    check("⚠️ 别的周几的同一时间 -> 不算（只认今天）",
          _lc([("AA10010", [_slot(10, 0, weekday=3)])], _now) is None)
    check("并列时取**更晚开始**的（同时段两门 → 刚开的那节）",
          _lc_course([("AA10010", [_slot(9, 40, minutes=120)]),
                      ("BB20020", [_slot(10, 20, minutes=120)])], _now) == "BB20020")
    check("⚠️ 先列谁不影响结果（同一份输入 -> 同一个答案）",
          _lc_course([("BB20020", [_slot(10, 20, minutes=120)]),
                      ("AA10010", [_slot(9, 40, minutes=120)])], _now) == "BB20020")

    print("\n--- ⑦b 结果区：导出清单 + 已在表里/你删过可展开 ---")
    _e = {"added": ["a"], "removed": set(), "not_added": ["x", "y"],
          "skipped_existing": ["old", "dup", "old"], "failed": []}
    check("⭐ 已在表里/你删过：去重保序",
          E.skipped_terms(_e) == ["old", "dup"], str(E.skipped_terms(_e)))
    check("⚠️ 非字符串 / 空项不混进列表（不把 None 变成 'None'）",
          E.skipped_terms({"skipped_existing": [None, "", "  ", "ok"]}) == ["ok"],
          str(E.skipped_terms({"skipped_existing": [None, "", "  ", "ok"]})))
    check("头部写出这组的计数（原来只有「另有 M 个没加」）",
          "2 个已在表里/你删过" in E.result_header(_e), E.result_header(_e))
    check("没有这组时头部一个字都不提",
          "已在表里" not in E.result_header({"added": ["a"]}))

    _h_collapsed = E.card_height(_e)
    check("⭐ 收起时多出**一行入口**（展开按钮那行）",
          abs(_h_collapsed - E.card_height({**_e, "skipped_existing": []})
              - E.RESULT_ROW) < 1e-9,
          f"{E.card_height({**_e, 'skipped_existing': []})} -> {_h_collapsed}")
    check("⭐ 展开后每个词各占一行（1 + len）",
          abs(E.card_height(_e, show_skipped=True) - _h_collapsed
              - 2 * E.RESULT_ROW) < 1e-9,
          f"{_h_collapsed} -> {E.card_height(_e, show_skipped=True)}")

    import tempfile as _tf
    with _tf.TemporaryDirectory() as _td:
        _root = pathlib.Path(_td)
        _p = E.export_not_added("ECON10740", ["x", "y"], root=_root,
                                today="2026-10-17")
        check("⭐ 清单落在**课程目录**里（`paths.course_dir` 的布局）",
              _p == _root / "courses" / "ECON10740" / "没加的术语_2026-10-17.txt",
              str(_p))
        _txt = _p.read_text(encoding="utf-8")
        check("⭐ 一行一个词 —— 与 `glossary/<课号>.txt` 同语法，可原样贴过去",
              [ln for ln in _txt.splitlines()
               if ln.strip() and not ln.startswith("#")] == ["x", "y"],
              repr(_txt))
        check("⚠️ 清单里写着这是哪门课、哪天（别让用户对着文件猜）",
              "ECON10740" in _txt and "2026-10-17" in _txt, _txt.splitlines()[0])
        check("⚠️ 空清单也照样写成文件（不抛、不写半个）",
              E.export_not_added("X", [], root=_root, today="2026-10-17").exists())

    print("\n--- ⑧ 卡片高度：**与坐标同一组常数推导** ---")
    check("⭐ card_height(None) == CARD_H —— 这条把推导锁住",
          E.card_height(None) == E.CARD_H,
          f"{E.card_height(None)} vs {E.CARD_H}")
    check("有结果 -> 变高",
          E.card_height({"added": ["a"], "removed": set()}) > E.CARD_H)
    h1 = E.card_height({"added": ["a"], "removed": set()})
    h2 = E.card_height({"added": ["a", "b"], "removed": set()})
    check("每多一个词 -> 恰好高一行（RESULT_ROW）",
          abs((h2 - h1) - E.RESULT_ROW) < 1e-9, f"{h1} -> {h2}")
    check("删掉一条 -> 卡片跟着缩（删光了也不留空行）",
          E.card_height({"added": ["a", "b"], "removed": {"b"}}) < h2)
    check("有「撤销」那一行 -> 再高一行",
          abs(E.card_height({"added": ["a"], "removed": {"a"},
                             "undo": {"text": "a"}})
              - E.card_height({"added": ["a"], "removed": {"a"}}) - E.RESULT_ROW) < 1e-9)

    print("\n--- ⑨ 零参数入口的退出码（`cl` 只读得动这个）---")
    # ⚠️⚠️ **这一组必须跑在「建过 `NSApplication`」之前**（它现在就在 ⑬/⑭ 前面）。
    #    理由不是洁癖，是它会**静默杀掉整个测试进程**：它调的 `on_close()`/`on_start()`
    #    走 `AppHelper.stopEventLoop()`，而 PyObjC 那份源码在没有 run loop 时是
    #    `if NSApp() is not None: NSApp().terminate_(None)` —— 建过 NSApp 之后
    #    `NSApp()` 就非空了 → **进程当场退出，rc=0、一个字都不报**（汇总行也不打）。
    #    2026-09-30 在 ⑳ 里实测踩到：日志跑到一半就没了，排查了半天。
    #    → **别把这一组挪到任何建窗口的那几节后面。**
    # ⚠️ 这一组的判据是**五条退出码必须互不相同**（0/1/2/3/4）：「用户取消」和「面板挂了」
    #    混在一起的话，要么违背用户意图（他取消了还录课），要么录不了课
    #    （正是作者拍第 1 条时要防的那件事）。
    #
    # ⚠️ 它会写**真的 `.course`**（`entry_launch.CFG`），所以按仓库「测试必须隔离写端」
    #    的规矩**先备份、跑完逐字还原**。同 `test_panel.py` 对 `.window` 的做法。
    import entry_launch
    import entry_panel as _EP

    # ⚠️ **路径从写端常量派生**，不要自己拼 `HERE / ".course"`（OCR 指出）：
    #    两处一旦漂移（比如 CFG 将来跟着 state_root 走），`finally` 里的还原会
    #    **静默指向错路径**，而末尾那条「逐字还原」比的正是同一个错路径 → 照样通过，
    #    真 `.course` 却留着测试值。
    cfg = entry_launch.CFG
    saved = cfg.read_bytes() if cfg.exists() else None
    _orig_open = _EP.open_panel
    # ⚠️⚠️ 3.9.3 起 `entry_launch.main()` 还多了两处**真副作用**，这一组必须一起关掉
    #    （2026-10-12 全仓审查核实）：
    #      ① `keyentry.clear_legacy_key()` —— `root=None` 时按 `paths.STATE_ROOT` 动
    #         **真**用户状态目录里的 `credentials`（把非 `cl-` 的值挪走、写 `key-cleared`
    #         标记），标记一旦被测试提前写掉，用户下一次启动的一次性搬家/提示就被跳过了；
    #      ② `update.spawn_draft_model_download()` —— ②/⑤ 那几条路返回 `CANCELLED`，
    #         会**真的**起一个脱离会话的子进程去下 464 MB 草稿模型（测试跑完它还在下）。
    #    `scripts/check.py` 给子进程设了 `HOME` 与 `CLASSLIVE_NO_AUTO_UPDATE`，闸门下看不出
    #    差别；但本文件开头叫人**直接跑** —— 那条路一个闸门都没有。
    import keyentry as _KE
    _orig_clear = _KE.clear_legacy_key
    _cleared: list = []

    def _fake_clear(**_kw):
        _cleared.append(1)                     # 记一笔：证明这一组真的走到了那一步
        return {"done": True, "moved": [], "first": False}

    _KE.clear_legacy_key = _fake_clear
    _no_upd_before = os.environ.get("CLASSLIVE_NO_AUTO_UPDATE")
    os.environ["CLASSLIVE_NO_AUTO_UPDATE"] = "1"
    try:
        def _run(fake):
            _EP.open_panel = fake
            return entry_launch.main()

        rc = _run(lambda **kw: (kw["on_start"]("ZZTEST"), object())[1])
        # ⚠️⚠️ **detail 是急切求值的**（2026-09-28 审查指出）：`check()` 的第三个参数
        #    在调用**之前**就算好了，`ok` 里的短路保护不到它 —— 生产回归导致 `.course`
        #    不存在时，这里会先抛 `FileNotFoundError`，在打印 ❌ 之前就把整轮测试崩掉
        #    （连汇总行都出不来），真因被"文件不存在"盖住。先安全取值再传。
        _course_txt = cfg.read_text(encoding="utf-8") if cfg.exists() else None
        check("① 点了「开始上课」-> 0，且 .course 写成那门课",
              rc == entry_launch.PICKED and _course_txt == "ZZTEST",
              f"rc={rc} course={_course_txt!r}")

        cfg.write_text("KEEPME", encoding="utf-8")
        rc = _run(lambda **kw: (kw["on_close"](), object())[1])
        # ⚠️ `check()` 的 detail 是**急切求值**的（`ok` 里的短路护不到它）——
        #    文件真没了的时候 `read_text` 会先抛 FileNotFoundError，连汇总行都打不出来。
        _keep = cfg.read_text(encoding="utf-8") if cfg.exists() else None
        check("⭐ ② 用户关掉面板 -> 1，且 **.course 一个字没动**（取消 ≠ 故障）",
              rc == entry_launch.CANCELLED and _keep == "KEEPME",
              f"rc={rc} course={_keep!r}")

        rc = _run(lambda **kw: None)
        check("⭐ ③ 面板起不来 -> 2（`cl` 据此**退回照旧立刻录课**）",
              rc == entry_launch.UNAVAILABLE, f"rc={rc}")

        # ⭐⭐ ④ **选了课但 `.course` 写不下去** -> 4（**不录**）
        #    2026-09-29 加。原来这一档复用 `2` → `cl` 照旧开麦，
        #    而盘上那份 `.course` 还是**上一门课**的 → 这节课**静默记到上一门课名下**
        #    （笔记/术语/课次全挂错门，不可逆）。文案当时写的却是「这次不录」。
        cfg.write_text("PREVIOUS", encoding="utf-8")     # 盘上留一门「上一门课」
        _good = entry_launch.CFG
        entry_launch.CFG = pathlib.Path("/nonexistent-cl-xyz/.course")  # 写必失败
        try:
            rc = _run(lambda **kw: (kw["on_start"]("ZZTEST"), object())[1])
        finally:
            entry_launch.CFG = _good
        _after = cfg.read_text(encoding="utf-8") if cfg.exists() else None
        check("⭐⭐ ④ 选了课但存不下来 -> 4（专属码，**不许**复用 2）",
              rc == entry_launch.UNSAVED,
              f"rc={rc} —— 若等于 2(UNAVAILABLE)，`cl` 会照旧录课、"
              f"并把它记到上一门课名下")
        check("⭐⭐ 而且盘上那份旧 `.course` **一个字没动**",
              _after == "PREVIOUS", f"现在是 {_after!r}")

        # ⭐ ④b 面板里更新装好了 -> 5（`cl` 据此重开自己）；`.course` 不许被动
        cfg.write_text("KEEPME", encoding="utf-8")
        rc = _run(lambda **kw: (kw["on_restart"](), object())[1])
        _keep = cfg.read_text(encoding="utf-8") if cfg.exists() else None
        check("⭐ ④b 面板里更新装好了 -> 5（RESTART），且 .course 一个字没动",
              rc == entry_launch.RESTART and _keep == "KEEPME", f"rc={rc} course={_keep!r}")
        check("⭐ 六个退出码互不相同",
              len({entry_launch.PICKED, entry_launch.CANCELLED, entry_launch.UNAVAILABLE,
                   entry_launch.NO_COURSES, entry_launch.UNSAVED, entry_launch.RESTART}) == 6)

        # ⭐⭐ ⑤ **零课程 -> 照常开面板**（2026-10-04 加）
        #    全新安装满足「一门课都没有」→ 原来一律返回 3 → 面板永远不出现 →
        #    就绪条上「下载语音模型」/ 建课 / 填 key 新用户都点不到（macOS 13 新用户实测）。
        #    ⚠️ 第一版只在「模型没齐」时开面板 —— 模型一下完面板又消失，同一个人又卡一次。
        #       所以这里**两种模型状态都要钉**：缺 与 齐，结果必须一样（都开面板）。
        #    ⚠️ **三样都换成桩**：课程清单（否则读真实 glossary/ 与 ~/.classlive/）、
        #       模型状态（否则读真实 ~/models/）、`open_panel`（上面已换）—— 同「测试必须
        #       隔离读端与写端」那条。零课程分支只读不写，`.course` 照样不会被动。
        import courses as _CO
        import doctor as _DOC
        import ready as _RD
        _o_lc, _o_ms = _CO.list_courses, _RD.model_states
        _opened: list = []

        def _fake_open(**kw):
            _opened.append(1)
            kw["on_close"]()
            return object()

        _EP.open_panel = _fake_open
        cfg.write_text("KEEPME", encoding="utf-8")
        try:
            _CO.list_courses = lambda *a, **k: []
            for _v, _label in (("missing", "必装模型缺"), ("ok", "模型齐"),
                               ("unknown", "模型 unknown")):
                _opened.clear()
                _RD.model_states = (lambda v: (lambda **k: {m.path: v for m in _DOC.MODELS}))(_v)
                rc = entry_launch.main()
                check(f"⭐⭐ ⑤ 零课程 + {_label} -> **开了面板**（用户关掉 -> 1，不是 3）",
                      rc == entry_launch.CANCELLED and _opened == [1],
                      f"rc={rc} opened={_opened}")

            # 零课程时，点「开始上课」那条路在面板里没有卡片可点 —— 但回调一旦来了
            # （比如用户先建了课）照常写 `.course`，别被这次改动误伤
            _opened.clear()
            _EP.open_panel = lambda **kw: (kw["on_start"]("ZZNEW"), object())[1]
            rc = entry_launch.main()
            _new = cfg.read_text(encoding="utf-8") if cfg.exists() else None
            check("⑥ 零课程的面板里建课后点「开始上课」-> 0，且 .course 写成那门课",
                  rc == entry_launch.PICKED and _new == "ZZNEW",
                  f"rc={rc} course={_new!r}")
        finally:
            _CO.list_courses, _RD.model_states = _o_lc, _o_ms
            _EP.open_panel = _fake_open

        check("⭐ 五条码互不相同（`cl` 的 case 分支靠这个分清）",
              len({entry_launch.PICKED, entry_launch.CANCELLED,
                   entry_launch.UNAVAILABLE, entry_launch.NO_COURSES,
                   entry_launch.UNSAVED}) == 5)
    finally:
        _KE.clear_legacy_key = _orig_clear
        if _no_upd_before is None:
            os.environ.pop("CLASSLIVE_NO_AUTO_UPDATE", None)
        else:
            os.environ["CLASSLIVE_NO_AUTO_UPDATE"] = _no_upd_before
        _EP.open_panel = _orig_open
        if saved is not None:
            cfg.write_bytes(saved)
        elif cfg.exists():
            cfg.unlink()
    check("⚠️ 这一组确实走到了搬家那一步（已换成桩，否则它会动真状态目录）",
          len(_cleared) >= 1, f"{len(_cleared)} 次")
    check("⚠️ 跑完 .course 逐字还原（隔离真的成立）",
          (cfg.read_bytes() if cfg.exists() else None) == saved)

    print("\n--- ⑩ 批量归档：映射表高度 + 分组（plan §7.12）---")

    def V(path, course, why="理由", source="model"):
        import classify as _cl
        return _cl.Verdict(path, course, why, source)

    vs = [V("a.pdf", "ECON10740"), V("b.pdf", "ECON10740"),
          V("c.pdf", "SOC10020"), V("d.pdf", None), V("e.pdf", None)]
    hit, left = E.split_verdicts(vs)
    check("split_verdicts：有归属的 3 份、认不出来的 2 份",
          len(hit) == 3 and len(left) == 2, f"{len(hit)}/{len(left)}")
    check("split_verdicts 是**划分**（两组不重不漏）",
          {id(v) for v in hit} | {id(v) for v in left} == {id(v) for v in vs}
          and not ({id(v) for v in hit} & {id(v) for v in left}))

    h1 = E.batch_card_height([V("a.pdf", "ECON10740")])
    h2 = E.batch_card_height([V("a.pdf", "ECON10740"), V("b.pdf", "ECON10740")])
    check("每多一行有归属的 -> 恰好高 BATCH_ROW",
          abs((h2 - h1) - E.BATCH_ROW) < 1e-9, f"{h1} -> {h2}")
    h3 = E.batch_card_height([V("a.pdf", "ECON10740"), V("d.pdf", None)])
    h4 = E.batch_card_height([V("a.pdf", "ECON10740"), V("d.pdf", None),
                              V("e.pdf", None)])
    # ⚠️ 拿**已经有两个组**的两张表比 —— 直接拿"只有 hit"去比会把
    #    「第一条未分类行顺带带来的 分隔线 + 组标题」算进去（那是 57，不是 24）。
    check("每多一行认不出来的 -> 也恰好高 BATCH_ROW",
          abs((h4 - h3) - E.BATCH_ROW) < 1e-9, f"{h3} -> {h4}")
    check("第一条未分类行要顺带带出 分隔线 + 组标题",
          abs((h3 - h1) - (E.BATCH_SEP + E.BATCH_HEAD + E.BATCH_ROW)) < 1e-9,
          f"{h1} -> {h3}")
    # ⭐ 没有"认不出来的"那组时，分隔线 + 组标题**不该**算进去。
    check("⭐ 没有未分类 -> 不算分隔与第二个组标题",
          abs(h1 - (E.CARD_PAD * 2 + E.BATCH_HEAD + E.BATCH_ROW)) < 1e-9, str(h1))
    check("空表 -> 只有上下的内边距（不崩、不为负）",
          E.batch_card_height([]) == E.CARD_PAD * 2, str(E.batch_card_height([])))

    print("\n--- ⑪ ⭐「未分类」不进队列（分错会污染术语表）---")
    g = E.group_for_archive([("a.pdf", "ECON10740"), ("b.pdf", "ECON10740"),
                             ("c.pdf", "SOC10020"), ("d.pdf", E.BATCH_UNDECIDED),
                             ("e.pdf", None), ("f.pdf", "")])
    check("⭐ 有归属的按课归组", g == {"ECON10740": ["a.pdf", "b.pdf"],
                                      "SOC10020": ["c.pdf"]}, str(g))
    check("⭐ 未分类 / 空标题 / None —— 一份都不进队列",
          "d.pdf" not in [x for v in g.values() for x in v]
          and "e.pdf" not in [x for v in g.values() for x in v]
          and "f.pdf" not in [x for v in g.values() for x in v], str(g))
    check("分组保持输入顺序", E.group_for_archive(
        [("z.pdf", "X"), ("a.pdf", "X")]) == {"X": ["z.pdf", "a.pdf"]})

    print("\n--- ⑫ 空状态说的那个按钮，必须真的存在 ---")
    # ⚠️⚠️ 上一版这里写的是「点右上角的「＋ 新增课程」」，而**那个按钮根本不存在** ——
    #     空状态在教用户去点一个不存在的东西。判据钉的是**两处不漂**：
    #     按钮标题（`ADD_TITLE`，建按钮时用的就是它）必须出现在空状态文案里。
    #     改按钮名却忘了改文案 → 这条红。
    _empty = " ".join(E.empty_state_lines())
    check("⭐ 空状态文案里的按钮名 = 真的建出来的那个按钮",
          E.ADD_TITLE in _empty, _empty)

    print("\n--- ⑭ delete_msg：删完那句话必须说实话 ---")
    _home = os.path.expanduser("~")
    _full = {"facts": {"sessions": 46, "materials": 0}, "trashed": ["a", "b"],
             "kept": None, "errors": []}
    _m = E.delete_msg("ECON10770", _full)
    check("全删：说进了废纸篓、说能拖回来",
          "废纸篓" in _m and "能拖回来" in _m, _m)
    # ⭐ 判据要指向那个位置：删了课再建同名，`sessions/` 的历史会**自己接回来** ——
    #    不说的话用户以为全没了（文案撒谎那类，同 `empty_state_lines` 那次）。
    check("⭐ 有上课记录就必须**点名说出来**（46 节，不是含糊的「记录还在」）",
          "46 节上课记录没动" in _m, _m)
    check("⚠️ 全删那句里**不许**出现保留区路径（两档不能长得一样）",
          "removed" not in _m, _m)

    _keep = {"facts": {"sessions": 0, "materials": 3}, "trashed": ["a"],
             "kept": _home + "/.classlive/courses/.removed/ECON10770", "errors": []}
    _m2 = E.delete_msg("ECON10770", _keep)
    # ⭐ 保留区里是**课件的原件唯一副本** —— 不写出它在哪，等于把原件藏起来
    check("⭐ 只删课号：必须写出课件**在哪儿**（且家目录缩成 `~`）",
          "~/.classlive/courses/.removed/ECON10770" in _m2, _m2)
    check("⭐ 没有上课记录就不许提（0 节别提）", "节上课记录" not in _m2, _m2)

    _m3 = E.delete_msg("X", {"facts": {}, "trashed": [],
                             "errors": ["术语表：磁盘满了"]})
    check("删失败 -> ⚠️ 开头 + 人话原因（不许静默当成功）",
          _m3.startswith("⚠️") and "磁盘满了" in _m3, _m3)

    _add_wiring_section()
    _digit_hint_section()
    _result_actions_section()
    _enter_section()
    _target_liveness_section()
    _chip_section()
    _test_mode_section()
    _cloud_asr_section()
    _vault_section()
    _guard_section()
    _key_entry_section()

    # ⚠️⚠️ **`bad` 不许在这里算！**（2026-09-29 抓到的假绿）
    #    原来这一行在这里算了一份 `RESULTS` 的快照，而后面还有 ⑮⑯⑰ 三组判据 ——
    #    于是**加在它后面的判据只打印、不进统计，失败了也不影响退出码**。
    #    实测：⑰ 里一条明明 ❌ 了，结算照报「137/137 通过」、`rc=0`。
    #    同族：`judges-that-look-like-they-test` 里那条「结算行比判据先算」。
    #    → 挪到最后，与结算行**同一个地方**（`return` 也跟着它）。
    print("\n--- ⑮ 搜索结果：点一条就地展开原文（`find.read`）---")
    check("read_span 前端不越到 0（命中第 1 行时）",
          E.read_span(1)[0] == 1, f"给的是 {E.read_span(1)}")
    check("read_span 的窗口宽度 = before + after + 1",
          E.read_span(50)[1] - E.read_span(50)[0] + 1
          == E.READ_BEFORE + E.READ_AFTER + 1,
          f"给的是 {E.read_span(50)}")

    # ⭐ 展开块：`find.read` 的输出是**给模型看的**，面板要剥壳（作者 2026-09-29 拍板）
    #    夹具用**真实会话格式**（含第二个时间块 + 一处 ASR 与 EN 不同）
    _raw = ("# f.md 第 2–10 行\n"
            "> [!abstract] 14:08:06\n"
            "> **EN**: The conventional energy removes negative energy.\n"
            "> **ZH**: 这些正能量会消除负能量。\n"
            "> **ASR**: The conventional energy removes negative energy.\n"
            "\n"
            "> [!abstract] 14:08:08\n"
            "> **EN**: What is it?\n"
            "> **ZH**: 那是什么?\n"
            "> **ASR**: What is it, though?\n")
    _clean = E.clean_read_text(_raw).splitlines()
    check("剥壳：抬头 `# 文件名 第 X–Y 行` **必须留着**",
          _clean[0] == "# f.md 第 2–10 行", f"给的是 {_clean[0]!r}")
    check("剥壳：`> [!abstract] 14:08:06` → 只剩时间",
          _clean[1] == "14:08:06", f"给的是 {_clean[1]!r}")
    check("剥壳：`**EN**: ` 前缀去掉",
          _clean[2] == "The conventional energy removes negative energy.",
          f"给的是 {_clean[2]!r}")
    check("剥壳：中文行原样（壳去掉了，内容一个字没动）",
          _clean[3] == "这些正能量会消除负能量。", f"给的是 {_clean[3]!r}")
    # ⭐ 同一时间块里 ASR 与 EN 一字不差 → 丢掉（不丢就是同一句连读两遍）
    #    ⚠️ 这条**必须按字段比**：真实顺序是 EN/ZH/ASR，EN 与 ASR 隔着 ZH。
    check("剥壳：与 EN 相同的 ASR 行被丢掉",
          "The conventional energy removes negative energy." not in _clean[4:],
          f"第 4 行起还有它：{_clean[4:]}")
    # ⭐ 反方向：ASR **不同**时不许误丢
    check("剥壳：ASR 与 EN 不同时必须留着",
          any("What is it, though?" in x for x in _clean), f"拿到 {_clean}")
    check("剥壳：空输入 → 空（不是 None、不抛）", E.clean_read_text("") == "")
    check("剥壳：第二块的时间戳照样剥壳",
          "14:08:08" in _clean, f"拿到 {_clean}")

    # ⭐⭐ 高度与排版**同一个来源**：展开 k 行，卡片就正好高 k * READ_LINE_H。
    #    变异验证（实跑过）：把 `search_card_height` 里的 `open_lines * READ_LINE_H`
    #    删掉 → 这四条立刻红。
    _base = E.search_card_height(5)
    for k in (0, 1, 9, 40):
        check(f"高度：5 条命中 + 展开 {k} 行 正好多 {k * E.READ_LINE_H}pt",
              abs((E.search_card_height(5, k) - _base) - k * E.READ_LINE_H) < 0.01,
              f"实际多 {E.search_card_height(5, k) - _base}")

    # ⭐⭐ 真的画一遍。**「没有子视图掉出卡片底部」是高度算少了的唯一症状**
    #     —— 而它**不报错**，只是把内容挤到框外（本仓库对 height 函数的既有锁，
    #     见 `tests/test_panel.py` ⑧ 组）。
    from find import Hit as _Hit
    _hits = [_Hit(path="/tmp/x.md", line=1 + i, text=f"命中 {i}", course="C",
                  date="2026-09-29", kind="session") for i in range(5)]
    for k in (0, 9):
        card, _tg = E._make_search_card(
            _hits, width=600.0, open_idx=(0 if k else None),
            open_text=("\n".join(f"第 {i} 行" for i in range(k)) or None),
            on_open=lambda i: None, targets=[])
        _low = min(float(v.frame().origin.y) for v in card.subviews())
        check(f"展开 {k} 行时没有子视图掉出卡片底部", _low >= -0.01,
              f"最低 y={_low:.1f}")
        # ⚠️ 弱引用：`setTarget_` 不持有 target，**必须由调用方留住**
        check(f"展开 {k} 行时把 targets 交回来了（弱引用要有人留）", len(_tg) == 5,
              f"拿到 {len(_tg)} 个")
        check(f"展开 {k} 行时交回来的 target 是活的（不是 None）",
              all(t is not None for t in _tg))

    print("\n--- ⑯ Handles 字段 ---")
    # ⭐ `Handles` 是**位置构造**踩过坑的地方：2026-09-29 插 `show_sessions` 时参数
    #    错了一格 —— `open_sessions` 落进 `delete_course`、删课 lambda 落进
    #    `show_sessions` → **调 show_sessions 实际在删课**，一个错都不报。
    #    修法是把构造改成关键字；这条钉住**字段名与顺序**，将来再有人插错至少看得见。
    check("Handles 的字段名与顺序（改构造方式后仍要一致）",
          E.Handles._fields == ("window", "close", "refresh", "set_status",
                                "start_batch", "search", "add_course",
                                "import_timetable",
                                "delete_course", "start_classify"),
          f"实际 {E.Handles._fields}")

    print("\n--- ⑰ 导入课表（拖 `.ics` 进来）---")
    check("挑课表：只认 .ics / .ical，且大小写不敏感",
          E.timetable_files(["/a/b.pdf", "/a/x.ICS", "/c/y.ics", "/d/z.txt"])
          == ["/a/x.ICS", "/c/y.ics"])
    check("挑课表：空 / None 不炸", E.timetable_files([]) == []
          and E.timetable_files(None) == [])

    # ⭐⭐ `acceptable()` —— 「面板收不收」的唯一定义点（悬停高亮 + 松手分流共用）。
    #    2026-09-29 加的：之前悬停只问 `extract.expand()`（能不能被**抽取**），
    #    而 `.ics` 不在支持集里 → 拖课表时**不高亮（说收不了）**，可一松手
    #    `run_prep` 真的把它导入了。判据钉的是**并集**这个形状。
    check("⭐ acceptable：课件收（走 extract 那条）",
          E.acceptable(["/x/讲义.pdf"]) is True)
    check("⭐⭐ acceptable：`.ics` 也收（走 timetable_files 那条 —— 这是那个缺口）",
          E.acceptable(["/x/timetable.ics"]) is True)
    check("⭐ acceptable：两条都落空 -> 拒（`.txt` 不是万能通行证的漏网）",
          E.acceptable(["/x/notes.txt"]) is False)
    check("⭐ acceptable：混拖 -> 收（HIG 子集语义）",
          E.acceptable(["/x/a.pdf", "/x/t.ics"]) is True
          and E.acceptable(["/x/a.pdf", "/x/Downloads"]) is True)
    check("⚠️ acceptable：空 / None -> 拒（不许把「什么都没有」当能收）",
          E.acceptable([]) is False and E.acceptable(None) is False)

    # ⭐⭐ 导入确认卡：**最后一行不许与底部按钮叠住**（2026-09-29 修）。
    #    `_make_import_card` 把按钮画在 `y = CARD_PAD`，而行循环推导出的**最后
    #    一行 y 也正好是 `CARD_PAD`** —— 高度少留了 `CARD_GAP_V + BTN_H`。
    #    ⚠️⚠️ **现有的「没有子视图掉出卡片底部」判据抓不到它**：那只查 `y >= 0`，
    #       而**叠住的两样东西都在 0 以上**。所以要查的是**重叠**，不是越界。
    #       这正是本仓库「判据要指向那个位置」那条 —— 换个症状就要换条判据。
    from AppKit import NSButton as _NSB
    from AppKit import NSIntersectsRect as _hits
    for _n in (1, 2, 5):
        _rows = [{"name": f"ECON1074{i}", "want": f"ECON1074{i}",
                  "state": "new", "when": "周二 15:00", "events": 1}
                 for i in range(_n)]
        _card, _tg = E._make_import_card(
            _rows, width=600.0, warn=[], on_confirm=lambda: None,
            on_cancel=lambda: None, targets=[])
        _btns = [v for v in _card.subviews() if isinstance(v, _NSB)]
        _lbls = [v for v in _card.subviews() if not isinstance(v, _NSB)]
        _bad = [(b.frame(), l.frame()) for b in _btns for l in _lbls
                if _hits(b.frame(), l.frame())]
        check(f"⭐ 导入卡（{_n} 门）最后一行不与按钮叠住"
              f"（高度要留 `CARD_GAP_V + BTN_H`）", not _bad,
              f"叠了 {len(_bad)} 处：{_bad[:1]}")
        check(f"⭐ 导入卡（{_n} 门）也没有子视图掉出底部",
              min(float(v.frame().origin.y) for v in _card.subviews()) >= -0.01)

    # ⭐ `drop_split()` —— 「哪些算课表、剩下哪些」的唯一定义点（2026-09-29 抽的）。
    #    抽它是因为那个分流原来是 `run_prep` 里内联的一行，而 `run_prep` 要起整个
    #    面板才跑得动 → 那一行**从来没有判据**。混拖会**丢掉课件那半边**，
    #    本轮只做到「说出来」（状态行提示再拖一次），这一条钉的就是那个分流。
    _i, _r = E.drop_split(["/a/x.ics", "/a/m.pdf", "/b/n.docx", "/c/y.ICAL"])
    check("⭐ drop_split：课表归课表、其余归其余（大小写都认）",
          _i == ["/a/x.ics", "/c/y.ICAL"] and _r == ["/a/m.pdf", "/b/n.docx"],
          f"ics={_i} rest={_r}")
    check("⭐ drop_split：没有课表时 ics 空、其余原样（顺序不许乱）",
          E.drop_split(["/a/1.pdf", "/a/2.pdf"]) == ([], ["/a/1.pdf", "/a/2.pdf"]))
    check("⚠️ drop_split：全是课表 -> 其余为空（那一档不该多说一句）",
          E.drop_split(["/a/x.ics"])[1] == [])
    check("⚠️ drop_split：空 / None 不炸",
          E.drop_split([]) == ([], []) and E.drop_split(None) == ([], []))

    # ⭐⭐ `plan_add` 的四个动作是 `bad` / **`exists`** / `pick` / `create`。
    #    第一版只把 `create` 当新建、其余全当「课号形状不对」→ **现成的课被标成
    #    "形状不对，跳过"**，而它一个字都没说错。变异验证（实跑过）：把
    #    `action in ("exists", "pick")` 改回 `action == "pick"` → 第 1、2 条立刻红。
    import timetable as _TT
    _ICS = (b"BEGIN:VCALENDAR\r\n"
            b"BEGIN:VEVENT\r\nUID:a\r\nSUMMARY:ECON10740: Exploring Economics (Lecture)\r\n"
            b"DTSTART;TZID=Europe/Dublin:20260908T150000\r\n"
            b"RRULE:FREQ=WEEKLY;BYDAY=TU\r\nEND:VEVENT\r\n"
            b"BEGIN:VEVENT\r\nUID:b\r\nSUMMARY:ECON10770: Introduction to Economics (Tutorial)\r\n"
            b"DTSTART:20260908T120000\r\nEND:VEVENT\r\n"
            b"BEGIN:VEVENT\r\nUID:c\r\nSUMMARY:!!!\r\nDTSTART:20260908T090000\r\nEND:VEVENT\r\n"
            b"END:VCALENDAR\r\n")
    _cs, _ = _TT.parse(_ICS)
    _rows = {c.name.split(":")[0]: E.ics_course_row(c, known=["ECON10770"])
             for c in _cs}
    check("⭐ 已有这门课 → `exists`（**不是**「形状不对」）",
          _rows["ECON10770"]["state"] == "exists", _rows["ECON10770"])
    check("不在清单里 → `new`（会新建）",
          _rows["ECON10740"]["state"] == "new", _rows["ECON10740"])
    check("课号猜得出（UCD 五位）", _rows["ECON10740"]["want"] == "ECON10740")
    check("时段渲染成人话（周几 + 时间）", "周二" in _rows["ECON10740"]["when"],
          _rows["ECON10740"]["when"])
    check("猜不出课号时退回课名（不硬猜）", _rows["!!!"]["want"] == "!!!", _rows["!!!"])

    # ⭐ 高度与排版同源（同 search/sessions 卡那条纪律）。
    #    ⚠️ 基线是**一行**不是零行 —— 空表也留一行（给「这份课表里没认出任何课程」那句），
    #       第一版拿空表当基线，于是期望写成 4×ROW 而实际是 3×ROW。**代码对、判据错。**
    check("导入卡高度：从 1 行到 k 行，每行正好 IMPORT_ROW",
          abs((E.import_card_height([{}] * 4) - E.import_card_height([{}]))
              - 3 * E.IMPORT_ROW) < 0.01,
          f"{E.import_card_height([{}] * 4)} vs {E.import_card_height([{}])}")
    check("导入卡高度：空表仍然留一行（那句话要有地方放）",
          abs(E.import_card_height([]) - E.import_card_height([{}])) < 0.01)

    print("\n--- ⑱ 课表预选 + 纠错入口 ---")
    # ⭐⭐ **不变量：`on_start is None` 时「预选」一个像素都不许加。**
    #     按钮行的高度（`card_height` 的 `CARD_GAP_V + BTN_H`）**只在 `on_start is not None`
    #     时才留**，所以那一档下卡片矮一截、就绪行本来就贴到 `y=CARD_PAD` ——
    #     再加一个按钮就是**叠在就绪行上**。
    #     变异验证（实跑过）：把「不是这门？」那个 `if` 的 `on_start is not None` 去掉
    #     → 这条立刻红。⚠️ **实测是先被截图抓到的**：既有判据只问"有没有掉出卡片底部"，
    #     而"两行叠在一起"根本没越界。
    from courses import Readiness as _RD
    _r0 = _RD("ECON10740", "Exploring Economics", 36, 0, 0, "2026-09-22")

    def _mk(**kw):
        return E._make_card(_r0, on_start=None, on_drop_files=lambda *a: None,
                            width=600.0, **kw)

    def _sig(c):
        return sorted((type(v).__name__, round(float(v.frame().origin.x), 1),
                       round(float(v.frame().origin.y), 1)) for v in c.subviews())

    check("⭐ on_start=None 时，「选中」一个按钮都不许加（否则叠在就绪行上）",
          _sig(_mk(hinted=True, why="课表上 15:00 是这门")) == _sig(_mk()),
          f"选中版 {len(_mk(hinted=True).subviews())} 个子视图，普通版 {len(_mk().subviews())} 个")

    # 反方向：**有按钮行时**，选中那张有且只有一颗「开始上课」
    # （否则上面那条会因为"什么都不画"而假绿）
    def _mk_act(**kw):
        return E._make_card(_r0, on_start=lambda c: None,
                            on_drop_files=lambda *a: None, width=600.0, **kw)

    from AppKit import NSButton as _NSBtn

    def _titles(c):
        return [v.title() for v in c.subviews() if isinstance(v, _NSBtn)]
    check("有按钮行时：选中那张卡只有一颗「开始上课」（2026-10-14 删了「不是这门？」）",
          _titles(_mk_act(hinted=True, on_select=lambda c: None)) == ["开始上课"],
          str(_titles(_mk_act(hinted=True, on_select=lambda c: None))))
    check("有按钮行时：**没选中**的卡一颗按钮都没有（不再每张卡挂一颗）",
          _titles(_mk_act(on_select=lambda c: None)) == [],
          str(_titles(_mk_act(on_select=lambda c: None))))
    # ⭐ 行为判据（`.claude/rules/appkit-ui.md`：别钉 flag，钉「回调真的触发」）
    _got: list = []
    _c = _mk_act(on_select=_got.append)
    _c.mouseDown_(None)
    check("⭐ 点没选中的卡 → on_select(课号) 真的被调到", _got == ["ECON10740"], str(_got))
    _got2: list = []
    _mk_act(hinted=True, on_select=_got2.append)   # 选中那张不挂点击（它有按钮）
    check("选中那张不挂「点卡选中」", getattr(_mk_act(hinted=True, on_select=_got2.append),
                                       "_on_click", None) is None)
    _w = "课表上 15:00 是这门"
    _sub = [v.stringValue() for v in _mk_act(hinted=True, why=_w).subviews()
            if hasattr(v, "stringValue") and not isinstance(v, _NSBtn)]
    check("⭐ 理由画在**选中那张卡**的第二行（原来在底栏，离卡太远）",
          any(t.startswith(_w + " · ") for t in _sub), str(_sub))
    _sub2 = [v.stringValue() for v in _mk_act(why=_w).subviews()
             if hasattr(v, "stringValue") and not isinstance(v, _NSBtn)]
    check("没选中的卡不带理由", not any(_w in t for t in _sub2), str(_sub2))

    print("\n" + "=" * 60)
    # ⚠️⚠️ `bad` **必须在这里算** —— 它是最后一句，所有判据都跑完了。
    #    这条纪律在本文件里被抓到过**两次**：第一次它在 368 行、第二次我把新判据
    #    又插到了它后面。**判据加在它之后 = 只打印、不进统计、失败也不影响退出码。**
    print("\n--- ⑲ 跨模块 API：引用了 `ready.X` 就必须真的存在 X ---")
    # ⭐⭐ 2026-09-29 加。OCR 审计（b8）抓到一条**真且用户可见**的：
    #    `_download_required().work()` 里写的是 `ready.ready_item_text(...)`,
    #    而 `ready.py` **从来没有这个函数**（它在 `entry_panel` 自己家里）。
    #    那两句在 `try` 内 → `AttributeError` 被吞 → 用户看到
    #    **「下载失败: module 'ready' has no attribute 'ready_item_text'」**，
    #    而模型其实**已经下好了**。
    #    ⚠️ 判据泛化到**整类**，不只这一处：把 `entry_panel` 里所有 `ready.X`
    #       都叠一遍 —— 跨模块引用打错名字是"静默吞掉"的重灾区。
    import ast as _ast
    import ready as _ready
    _src = pathlib.Path(__file__).resolve().parent.parent / "entry_panel.py"
    _tree = _ast.parse(_src.read_text(encoding="utf-8"))
    _used = set()
    for _n in _ast.walk(_tree):
        if (isinstance(_n, _ast.Attribute)
                and isinstance(_n.value, _ast.Name) and _n.value.id == "ready"):
            _used.add(_n.attr)
    _missing = sorted(a for a in _used if not hasattr(_ready, a))
    check("⭐⭐ `entry_panel` 引用的每个 `ready.X` 都真的存在"
          "（打错名字会被 try 吞成『下载失败』）",
          not _missing, f"不存在的: {_missing}")
    check("⚠️ 而且确实扫到了东西（空集会让上一条恒真）",
          len(_used) >= 3, f"只扫到 {sorted(_used)}")

    print("\n--- ⑲c 状态行只有一个实参（2026-10-12 核实）---")
    # ⚠️ 判据形状：**扫源码**（用 `ast`，同 `test_lazy_asr` / `test_draft_engine` 那几处的做法）。
    #    为什么不能只靠类型系统：直接调用 `set_status(t, 0)` 在**参数还在**的年代是合法调用
    #    （`alpha=0` = 全透明），TypeError 抓不到「语义被传错」这一类 —— 那正是本轮的缺陷。
    #    历史：第二参数是 `alpha`，而调用点从 2026-09-29（`610dddf`）起按「显示秒数 / 0 = 不自动
    #    消失」传（`2.0` / `3.0` / `0`），函数里却从来没有计时器 → 三处 `, 0)` 把「正在验证…」
    #    和课表推测的理由设成**全透明**（探针实测 `alpha=0.00`）。
    #    ⚠️ `_status` 还会被**当值**传出去（`AppHelper.callAfter(_status, 文案, 2.0)`）——
    #       按 `_status(` 找的话一处都扫不到，本轮就是这么漏过去的。两条都钉。
    import ast as _ast
    _src = (HERE / "entry_panel.py").read_text(encoding="utf-8")
    _tree = _ast.parse(_src)
    _bad_args, _bad_pass, _n_seen = [], [], 0
    for _n in _ast.walk(_tree):
        if not isinstance(_n, _ast.Call):
            continue
        _f = _n.func
        _name = _f.attr if isinstance(_f, _ast.Attribute) else getattr(_f, "id", "")
        if _name in ("_status", "set_status"):
            _n_seen += 1
            if len(_n.args) + len(_n.keywords) > 1:
                _bad_args.append(f"第 {_n.lineno} 行 {_name}（{len(_n.args)} 个实参）")
        elif _name in ("callAfter", "callLater", "_later") and _n.args:
            if getattr(_n.args[0], "id", None) == "_status":
                _n_seen += 1
                if len(_n.args) > 2:
                    _bad_pass.append(f"第 {_n.lineno} 行 {_name}(_status, …{len(_n.args) - 1} 个实参)")
    check("⭐⭐ 状态行不再接受第二个实参（就是被误当成秒数的那个 alpha）",
          not _bad_args, "；".join(_bad_args[:3]))
    check("⭐⭐ 当值传给 callAfter/_later 时也不许多给实参",
          not _bad_pass, "；".join(_bad_pass[:3]))
    check("⚠️ 而且确实扫到了调用点（一条都没扫到会让上面两条恒真）",
          _n_seen >= 20, f"只扫到 {_n_seen} 处")

    bad = [n for n, ok, _ in RESULTS if not ok]
    print(f"{len(RESULTS) - len(bad)}/{len(RESULTS)} 通过")
    for n in bad:
        print(f"  ❌ {n}")
    return 1 if bad else 0


def _guard_section() -> None:
    """⑲d 焦点守卫：**真跑一拍**（假窗口 + 假 `AppHelper.callLater`），钉住三个收手条件。

    ⚠️ 这一条是**行为**判据，不是源码判据 —— 守卫的缺陷（首拍早于上屏 → 一次都不跑）
       正是"代码看起来对、行为从没发生"那一类。真窗口那一版在
       `/tmp/probe_guard.py`（上屏后 `seen`、手动 key 后 1.5 s 自己还键盘）实测过；
       这里用假窗口把它变成常驻判据，不必起 run loop。
    """
    print("\n--- ⑲d 焦点守卫：一拍该做什么（假窗口，不起 run loop）---")
    import types
    from PyObjCTools import AppHelper as _AH

    calls: list = []

    class _Win:
        def __init__(self, visible=True, key=True, editing=False):
            self.visible, self.key, self.editing = visible, key, editing
            self.released = 0

        def isVisible(self):
            return self.visible

        def isKeyWindow(self):
            return self.key

        def firstResponder(self):
            return types.SimpleNamespace(
                isFieldEditor=lambda: self.editing, __eq__=lambda self, o: False)

        def makeFirstResponder_(self, _):
            self.released += 1

        def resignKeyWindow(self):
            self.released += 1

    _orig = _AH.callLater
    _AH.callLater = lambda delay, fn, *a: calls.append((delay, fn, a))
    try:
        # ① 可见 + key + **不在编辑** → 把键盘还出去，并继续排下一拍
        w = _Win(visible=True, key=True, editing=False)
        st = {"win": w, "gen": 1, "waits": 0}
        calls.clear()
        E._guard_tick(st, 1)
        check("⭐ 面板是 key 又没在编辑 → 把键盘还出去", w.released > 0, f"released={w.released}")
        check("⭐ 而且继续排下一拍（0.5 s）",
              len(calls) == 1 and calls[0][0] == 0.5, str(calls[:1]))

        # ② 正在编辑 → 一个字都不动（打字时被抢焦点是最坏的失败模式）
        w2 = _Win(visible=True, key=True, editing=True)
        E._guard_tick({"win": w2, "gen": 1, "waits": 0}, 1)
        check("⭐⭐ 正在编辑 → 不碰（检测不出来就当作在打字，保守）",
              w2.released == 0, f"released={w2.released}")

        # ③ 关了（取消令牌）→ 收手，**不再续期**
        calls.clear()
        E._guard_tick({"win": None, "gen": 1, "waits": 0}, 1)
        check("⭐⭐ 窗口关了（`win is None`）→ 不再续期", not calls, str(calls))

        # ④ 代号对不上（上一代的链子）→ 收手（否则每开关一次面板多一条永不退出的链子）
        calls.clear()
        E._guard_tick({"win": _Win(), "gen": 5, "waits": 0}, 4)
        check("⭐⭐ 上一代的链子醒来 → 认不出自己，收手", not calls, str(calls))

        # ⑤ 一直没上屏 → 等满上限就停（兜底，不许无界轮询）
        calls.clear()
        st5 = {"win": _Win(visible=False), "gen": 3, "waits": E._GUARD_MAX_WAITS}
        E._guard_tick(st5, 3)
        check("⚠️ 窗口始终不可见 → 等满上限就停（不然是无界轮询）", not calls, str(calls))
        calls.clear()
        E._guard_tick({"win": _Win(visible=False), "gen": 3, "waits": 0}, 3)
        check("⚠️ 而还没到上限时仍然接着等（别把兜底写成立刻放弃）", len(calls) == 1, str(calls))
    finally:
        _AH.callLater = _orig


def _chip_helpers():
    """这一节和 ⑳ / ⑳b 共用的小工具（都在真窗口里找控件，不按下标猜）。"""
    from AppKit import NSButton, NSSearchField

    def walk(v, out=None):
        out = [] if out is None else out
        out.append(v)
        for c in (v.subviews() or []):
            walk(c, out)
        return out

    def chip_of(h):
        """标题行那颗胶囊 = 面板上唯一一颗 y > 60 的 `NSButton`（搜索框不是 `NSButton`）。"""
        got = [v for v in walk(h.window.contentView(), [])
               if isinstance(v, NSButton) and not isinstance(v, NSSearchField)
               and float(v.frame().origin.y) > 60.0]
        return got[0] if len(got) == 1 else None

    def items_of(menu):
        return [menu.itemAtIndex_(i) for i in range(menu.numberOfItems())]

    def titles(menu):
        return [(i.title() or "") for i in items_of(menu)]

    return walk, chip_of, items_of, titles


class _ReadyAccess:
    """就绪某一项在**当前**的落点 —— 2026-10-16 起它有两个（`PLAN-396` §1）：

      ① **只有一项**不正常 → 那一项直接写在**标题行的胶囊**上，点胶囊就是修它；
      ② **两项及以上** → 在**胶囊菜单**里各占一行，点那一行就是修它。
    ⚠️ 两条路最终都调 `on_ready_click(key)` —— 判据只关心「点了之后发生了什么」，
       所以这里把两条路封成一个口子（`label` / `click`），各节不用各写一遍。
    """

    def __init__(self, menus: list):
        self._w, self._chip_of, self._items_of, _t = _chip_helpers()
        self.menus = menus                       # ⚠️ 调用方已经把 `E.popup_menu` 换成桩了

    def open_menu(self, h):
        """点胶囊 → 菜单（`E.popup_menu` 是桩，不会真弹）。

        ⚠️⚠️ **只有带 `CHIP_TAIL`（「▾」）的胶囊才有菜单** —— 「一项不正常」那一档
           点下去是**直接修那一项**（比如「语音模型 缺 2 件 · 下载」会**真的开始下载**）。
           没带尾巴时这里**绝不点它**，返回 None（`label`/`click` 跟着变保守）。
        """
        chip = self._chip_of(h)
        if chip is None or not (chip.title() or "").endswith(E.CHIP_TAIL):
            return None
        self.menus.clear()
        chip.performClick_(None)
        return self.menus[-1] if self.menus else None

    def row(self, h, needle):
        """菜单里标题含 `needle` 的那一行。"""
        menu = self.open_menu(h)
        if menu is None:
            return None
        return next((i for i in self._items_of(menu)
                     if needle in (i.title() or "")), None)

    def label(self, h, needle):
        """那一格的**当前文字**（`ready_item_text` 那句，去掉「 · 动词」尾巴）。找不到 → None。"""
        chip = self._chip_of(h)
        if chip is not None and needle in (chip.title() or ""):
            return (chip.title() or "").split("  ·  ")[0]
        r = self.row(h, needle)
        return (r.title() or "").split("  ·  ")[0] if r is not None else None

    def click(self, h, needle) -> bool:
        """点那一格。⚠️ 只有「一项不正常」那一档才直接点胶囊 —— 那时胶囊上写的正是它；
        其它情况一律**只在菜单里点那一行**（见 `open_menu` 那条：不带 ▾ 的胶囊绝不点）。"""
        chip = self._chip_of(h)
        if chip is not None and needle in (chip.title() or ""):
            chip.performClick_(None)
            return True
        r = self.row(h, needle)
        if r is None:
            return False
        r.target().act_(r)
        return True

    def act(self, item):
        item.target().act_(item)


def _chip_section() -> None:
    """㉓ 就绪胶囊（`PLAN-396` §1）：文字三档 + **行为**（点了弹不弹菜单 / 修不修那一项）。

    ⚠️ 为什么要建窗口：这一节钉的是**接线**（弹没弹、调没调到），而接线的坏法一律不出声
       —— 纯函数那边全绿，症状统一是「点了没反应」。
    ⚠️ `popup_menu` 换成**桩**：真 `NSMenu` 会开一个同步的跟踪循环，测试会挂在那儿。
    ⚠️ 写端全隔离在 tempdir（`glossary=` / `state_root=`），`HOME` 也挪开
       （「笔记库」那条路会去读真 Obsidian 注册表）。
    """
    print("\n--- ㉓ 就绪胶囊：文字三档 / 点了弹不弹菜单 / 面板矮了 40pt ---")

    # ── 纯函数：哪一项算「要处理」──────────────────────────────────
    _bad = E.ready_item_bad
    check("全正常 → 不用管", not _bad({"key": "mic", "state": "ok"}))
    check("todo / warn / unknown / busy 都要管",
          [_bad({"key": "mic", "state": s}) for s in ("todo", "warn", "unknown", "busy")]
          == [True, True, True, True])
    # ⭐⚠️ 这一条是这条判据存在的理由：`state` 是 ok、但它**有事要做**
    check("⭐⭐ 齐了但缺草稿模型（`state=ok` + `offer_draft`）也算「要处理」",
          _bad({"key": "models", "state": "ok", "offer_draft": True}) is True)

    # ── 纯函数：三档文字（期望值**写字面量**）──────────────────────
    _OK = [{"key": "mic", "state": "ok"}, {"key": "models", "state": "ok"},
           {"key": "engine", "state": "ok", "detail": "云端翻译（推荐）"},
           {"key": "vault", "state": "ok", "detail": "/tmp/V"}]
    check("⭐ 全正常 → 「✓  已就绪」+ 形态 ok（点开是菜单）",
          E.ready_chip(_OK) == ("✓  已就绪", "ok", ""), str(E.ready_chip(_OK)))
    check("⭐ 只有麦克风被拒 → 直接写那一项 + 点了会干嘛（形态 one）",
          E.ready_chip([{**_OK[0], "state": "warn"}, *_OK[1:]])
          == ("⚠  麦克风  被拒  ·  去设置", "one", "mic"),
          str(E.ready_chip([{**_OK[0], "state": "warn"}, *_OK[1:]])))
    check("⭐ 只有笔记库未设 → 「—  笔记库  未设  ·  选文件夹」",
          E.ready_chip([*_OK[:3], {"key": "vault", "state": "unknown"}])
          == ("—  笔记库  未设  ·  选文件夹", "one", "vault"),
          str(E.ready_chip([*_OK[:3], {"key": "vault", "state": "unknown"}])))
    # ⚠️ 「下载中…」不许再挂一个「· 下载」—— 那是在教用户点一个**正在跑**的东西
    check("⭐ 正在下载 → 只写状态，不补动词",
          E.ready_chip([_OK[0], {**_OK[1], "state": "busy"}, *_OK[2:]])
          == ("◌  语音模型  下载中…", "one", "models"),
          str(E.ready_chip([_OK[0], {**_OK[1], "state": "busy"}, *_OK[2:]])))
    check("⭐ 两项要处理 → 「2 项要处理」+ 形态 many（点开是菜单）",
          E.ready_chip([{**_OK[0], "state": "warn"}, *_OK[1:3],
                        {"key": "vault", "state": "unknown"}])
          == ("2 项要处理", "many", ""),
          str(E.ready_chip([{**_OK[0], "state": "warn"}, *_OK[1:3],
                            {"key": "vault", "state": "unknown"}])))
    # ⭐ 反方向：不同的坏项**不许**给出同一句话（否则上面几条会因为「永远返回同一句」而假绿）
    check("⚠️ 而不同的坏项给的是不同的话（不是恒定的）",
          len({E.ready_chip([{**_OK[0], "state": "warn"}, *_OK[1:]])[0],
               E.ready_chip([*_OK[:3], {"key": "vault", "state": "unknown"}])[0],
               E.ready_chip(_OK)[0]}) == 3)

    # ── 纯函数：截断（窄面板下也放得下）──────────────────────────
    # ⚠️ 假 measure（一个字 10pt）→ 不用起窗口就能把边界钉死。
    _m = lambda s: len(s) * 10.0                                   # noqa: E731
    _fit = E.chip_fit
    check("放得下 → 原样 + 尾巴（尾巴是「点开有东西」的唯一提示）",
          _fit("abc", " ‣", 100.0, _m) == "abc ‣", _fit("abc", " ‣", 100.0, _m))
    _long = _fit("abcdefghij", " ‣", 60.0, _m)
    check("⭐ 太长了 → 裁到上限以内，且**尾巴留着**",
          _m(_long) <= 60.0 and _long.endswith(" ‣") and "…" in _long, repr(_long))
    check("⭐ 而且是「留下的刚好最多」那一档（再留一个字就超）",
          _m(_long[:-2] + _long[-2:]) <= 60.0 and "…" in _long,
          repr(_long))
    check("⚠️ 上限极小时也**不返回空**（至少还有尾巴）",
          _fit("abcdefghij", " ‣", 25.0, _m).endswith(" ‣"),
          repr(_fit("abcdefghij", " ‣", 25.0, _m)))

    try:
        import tempfile

        from AppKit import (NSApplication, NSApplicationActivationPolicyAccessory,
                            NSButton, NSSearchField)
    except Exception as e:                                        # noqa: BLE001
        check("AppKit 可用（这一节要真窗口）", False, f"{type(e).__name__}: {e}")
        return
    NSApplication.sharedApplication().setActivationPolicy_(
        NSApplicationActivationPolicyAccessory)

    import ready as ready_mod

    _walk, _chip_of, _items_of, _menu_titles = _chip_helpers()

    def _act(item):
        item.target().act_(item)

    _saved_home = os.environ.get("HOME")
    menus: list = []
    _orig_popup = E.popup_menu
    E.popup_menu = lambda menu, view: menus.append(menu)
    try:
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            fake_home = root / "home"
            fake_home.mkdir()
            os.environ["HOME"] = str(fake_home)      # ⚠️ 「笔记库」那条路会读真注册表
            os.environ.pop("OBSIDIAN_VAULT", None)
            gl = root / "glossary.txt"
            gl.write_text("# public table\n", encoding="utf-8")

            def _build(name, items, **kw):
                _orig = ready_mod.items
                ready_mod.items = lambda **k: [dict(i) for i in items]
                kw.setdefault("on_test_mode", lambda on: True)
                kw.setdefault("on_cloud_asr", lambda on: True)
                if "cloud_asr" not in kw and kw.get("on_cloud_asr") is not None:
                    kw["cloud_asr"] = True
                try:
                    return E.build(glossary=gl, sessions_dir=root / ("s" + name),
                                   state_root=root / ("st" + name),
                                   on_start=lambda c: None, **kw)
                finally:
                    ready_mod.items = _orig

            # ── ① 全正常：胶囊写「已就绪」，点开 = 菜单（用桩，不真弹）──
            h = _build("1", _OK)
            try:
                check("面板建起来了（㉓ 要真窗口）", h is not None)
                chip = _chip_of(h) if h is not None else None
                check("⭐ 标题行有且只有一颗胶囊", chip is not None)
                if chip is not None:
                    check("⭐⭐ 全正常 → 胶囊文字**逐字**是「✓  已就绪  ▾」（字面量）",
                          (chip.title() or "") == "✓  已就绪  ▾", repr(chip.title()))
                    menus.clear()
                    chip.performClick_(None)
                    check("⭐⭐ 点一下 → **弹菜单**（桩收到一次 popup）", len(menus) == 1,
                          f"命中 {len(menus)} 次")
                    if menus:
                        _t = _menu_titles(menus[-1])
                        check("⭐ 菜单里四项各一行（点了做它现在的事）+ 分隔线 + 两个勾选项",
                              _t == ["✓  麦克风", "✓  语音模型", "✓  云端翻译",
                                     "✓  笔记库", "", "云端转写 · 英文更准",
                                     "测试模式：关"], str(_t))
                        check("⭐ 那几行的 target 都活着（弱引用坑：没挂住 = 点了没反应）"
                              "（「语音模型」那一行是灰的状态行、没有 target，是**故意**的）",
                              all(i.target() is not None for i in _items_of(menus[-1])
                                  if i.isEnabled() and i.title()),
                              str([(i.title(), i.target() is not None)
                                   for i in _items_of(menus[-1]) if i.title()]))
                    check("⭐ 胶囊的 target 也活着（`S['chip_targets']` 挂住了）",
                          chip.target() is not None)
                    # ⚠️ 几何：胶囊右沿**不许越过搜索框**（窄面板下也不许）
                    _sf = [v for v in _walk(h.window.contentView(), [])
                           if isinstance(v, NSSearchField)]
                    _cf = chip.frame()
                    check("⭐⭐ 胶囊右沿停在搜索框左边（不挤坏搜索框）",
                          len(_sf) == 1
                          and float(_cf.origin.x + _cf.size.width)
                          <= float(_sf[0].frame().origin.x) - 10.0 + 0.6,
                          f"胶囊右沿 {float(_cf.origin.x + _cf.size.width):.1f} / "
                          f"搜索框左沿 {float(_sf[0].frame().origin.x):.1f}"
                          if _sf else "没找到搜索框")
            finally:
                if h is not None:
                    h.close()

            # ── ② 只有一项不正常：点一下就修，**不弹菜单** ────────────
            # 用「笔记库未设」那一档（点了走 `pick_folder`，是个**能观察到的副作用**）。
            _picked: list = []
            _orig_pick = E.pick_folder
            E.pick_folder = lambda **kw: (_picked.append(kw), None)[1]
            h2 = _build("2", [*_OK[:3], {"key": "vault", "state": "unknown"}])
            try:
                chip2 = _chip_of(h2) if h2 is not None else None
                check("⭐ 一项不正常 → 胶囊直接写那一项（字面量）",
                      chip2 is not None
                      and (chip2.title() or "") == "—  笔记库  未设  ·  选文件夹",
                      repr(chip2.title()) if chip2 is not None else "没有胶囊")
                if chip2 is not None:
                    menus.clear()
                    chip2.performClick_(None)
                    check("⭐⭐ 点一下 → **直接修那一项**（`on_ready_click('vault')` → 选文件夹）",
                          len(_picked) == 1, f"pick_folder 命中 {len(_picked)} 次")
                    check("⭐ 而且**没弹菜单**（一项的那一档不开菜单）",
                          not menus, f"弹了 {len(menus)} 次")
            finally:
                if h2 is not None:
                    h2.close()
                E.pick_folder = _orig_pick

            # ── ③ 两项不正常 → 「2 项要处理」→ 菜单 ────────────────────
            h3 = _build("3", [{**_OK[0], "state": "warn"}, *_OK[1:3],
                              {"key": "vault", "state": "unknown"}])
            try:
                chip3 = _chip_of(h3) if h3 is not None else None
                check("⭐⭐ 两项不正常 → 胶囊写「2 项要处理  ▾」（含「2 项」）",
                      chip3 is not None and "2 项" in (chip3.title() or "")
                      and (chip3.title() or "").endswith(E.CHIP_TAIL),
                      repr(chip3.title()) if chip3 is not None else "没有胶囊")
                if chip3 is not None:
                    menus.clear()
                    chip3.performClick_(None)
                    check("⭐ 点一下 → 弹菜单（这一档不开单一动作）", len(menus) == 1)
                    if menus:
                        check("⭐ 菜单里那两项**照旧各一行**（不是被合并掉）",
                              "笔记库" in " ".join(_menu_titles(menus[-1])), "")
            finally:
                if h3 is not None:
                    h3.close()

            # ── ④ 菜单里两个勾选项：状态与回调一致 ──────────────────────
            _tm_calls: list = []
            h4 = _build("4", _OK, on_test_mode=lambda on: (_tm_calls.append(on), True)[1],
                        test_mode=False)
            try:
                chip4 = _chip_of(h4) if h4 is not None else None
                menus.clear()
                if chip4 is not None:
                    chip4.performClick_(None)
                items4 = _items_of(menus[-1]) if menus else []
                _cloud = next((i for i in items4 if "云端转写" in (i.title() or "")), None)
                _tm = next((i for i in items4 if "测试模式" in (i.title() or "")), None)
                check("⭐⭐ 两个勾选项都在菜单里，且状态与开关初值一致"
                      "（云端转写=开→勾上；测试模式=False→未勾、标题写「关」）",
                      _cloud is not None and _cloud.state() == 1
                      and _tm is not None and _tm.state() == 0
                      and (_tm.title() or "") == "测试模式：关",
                      f"云端={_cloud and _cloud.state()} "
                      f"测试={_tm and (_tm.title(), _tm.state())}")
                if _tm is not None and chip4 is not None:
                    _act(_tm)
                    check("⭐⭐ 点菜单里那颗 → 回调真的收到 True（勾选项不是画着好看的）",
                          _tm_calls == [True], str(_tm_calls))
                    menus.clear()
                    chip4.performClick_(None)
                    _tm2 = next((i for i in _items_of(menus[-1]) if "测试模式" in (i.title() or "")),
                                None)
                    check("⭐ 再开菜单 → 勾上了、标题变「测试模式：开」",
                          _tm2 is not None and _tm2.state() == 1
                          and (_tm2.title() or "") == "测试模式：开",
                          repr(_tm2 and (_tm2.title(), _tm2.state())))
            finally:
                if h4 is not None:
                    h4.close()

            # ── ⑤ 面板高度：就绪项在不在**都不许**多出那一行（原来 +40pt）──
            h5a = _build("5a", _OK)
            h5b = _build("5b", [])
            try:
                _ha = float(h5a.window.frame().size.height) if h5a else 0.0
                _hb = float(h5b.window.frame().size.height) if h5b else 0.0
                check("⭐⭐ 有就绪项 / 没有就绪项 → 面板**一样高**（就绪那一行已经并进标题行）",
                      _ha > 0 and abs(_ha - _hb) < 0.01, f"{_ha} vs {_hb}")
                check("⭐ 而且没有就绪项时**没有胶囊**（不是画一颗空的）",
                      h5b is not None and _chip_of(h5b) is None)
                check("⭐ 没有就绪项时，测试模式退回落点条那颗按钮（不许跟着胶囊一起消失）",
                      h5b is not None
                      and len([v for v in _walk(h5b.window.contentView(), [])
                               if isinstance(v, NSButton)
                               and (v.title() or "").startswith("测试模式：")]) == 1)
                check("⚠️ 而 `READY_H` 这个常数**已经不在了**（没留一条没人用的就绪行高度）",
                      not hasattr(E, "READY_H"))

                # ── ⑥ 文字再长也裁在胶囊里，不挤搜索框 ─────────────────
                _long_detail = "还差 3 件，约 " + "1" * 60 + " MB"
                h6 = _build("6", [{**_OK[0]}, {"key": "models", "state": "todo",
                                               "detail": _long_detail}, *_OK[2:]])
                try:
                    chip6 = _chip_of(h6) if h6 is not None else None
                    _sf6 = [v for v in _walk(h6.window.contentView(), [])
                            if isinstance(v, NSSearchField)] if h6 is not None else []
                    check("⭐⭐ 超长状态 → 胶囊里出现「…」且右沿仍在搜索框左边（裁的是文字）",
                          chip6 is not None and "…" in (chip6.title() or ""),
                          repr(chip6.title()) if chip6 is not None else "没有胶囊")
                    if chip6 is not None and _sf6:
                        _f, _s = chip6.frame(), _sf6[0].frame()
                        check("⭐⭐ 而且右沿仍然停在搜索框左边",
                              float(_f.origin.x + _f.size.width)
                              <= float(_s.origin.x) - 10.0 + 0.6,
                              f"胶囊右沿 {float(_f.origin.x + _f.size.width):.1f} / "
                              f"搜索框左沿 {float(_s.origin.x):.1f}")
                finally:
                    if h6 is not None:
                        h6.close()
            finally:
                for _h in (h5a, h5b):
                    if _h is not None:
                        _h.close()
    finally:
        E.popup_menu = _orig_popup
        if _saved_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = _saved_home


def _cloud_asr_section() -> None:
    """⑳b 「云端转写」：**胶囊菜单顶层那颗勾选项**（`PLAN-396` §1；10-12 前是落点条上的开关，
    10-12 后是「语音模型」菜单里的勾选项，10-16 起挪到顶层跟「测试模式」并排）。
    钉：纯函数（子菜单 + 顶层勾选项）/ 那一行的状态词 / 点勾选项真的触发回调 / 写失败不翻 / 锁定 / 落盘接线。

    ⚠️ 写端全隔离：面板走 tempdir 的 glossary/state_root；`entry_launch` 那段换掉 `paths.STATE_ROOT`，
       绝不碰真 `~/.classlive/`。⚠️ 子菜单里「下载…」那一项**不点**（会真下模型）。
    """
    print("\n--- ⑳b 云端转写：胶囊菜单顶层的勾选项 ---")
    import tempfile

    # ── 纯函数：期望值写字面量 ─────────────────────────────────────
    _mm = E.models_menu
    _ok = {"key": "models", "state": "ok"}
    check("⭐ 「语音模型」子菜单：都齐了 → 只剩一行灰的「模型都已就绪」",
          [(i.get("key"), i.get("title"), i.get("enabled"), i.get("checked"))
           for i in _mm(_ok)] == [("info", "模型都已就绪", False, False)],
          str(_mm(_ok)))
    check("⭐ 缺模型 → 第一项「下载缺的模型」可点",
          _mm({"key": "models", "state": "todo"})[0]
          == {"key": "required", "title": "下载缺的模型", "enabled": True, "checked": False})
    check("⭐ 可加草稿模型 → 「下载流式草稿模型（464 MB）」",
          _mm({**_ok, "offer_draft": True}, draft_mb="464 MB")[0]["title"]
          == "下载流式草稿模型（464 MB）")
    check("正在下载 → 下载项不可点",
          _mm({"key": "models", "state": "todo"}, downloading=True)[0]["enabled"] is False)
    # ⭐ 反方向：云端转写**不许**再出现在模型子菜单里（两处会打架）
    check("⭐⭐ 「云端转写」**不在**模型子菜单里（挪去顶层跟「测试模式」并排了）",
          not any("云端转写" in (i.get("title") or "") for i in _mm(_ok)), str(_mm(_ok)))
    _ci = E.cloud_item(cloud_on=True, locked=False)
    check("⭐ 顶层勾选项（开着）：标题逐字 + 勾上 + 可点",
          (_ci["title"], _ci["checked"], _ci["enabled"])
          == ("云端转写 · 英文更准", True, True), str(_ci))
    check("⭐ 关着 → 未勾", E.cloud_item(cloud_on=False, locked=False)["checked"] is False)
    _lk = E.cloud_item(cloud_on=True, locked=True)
    check("⭐⭐ 环境变量锁定 → 未勾、不可点、标题点名 CLASSLIVE_CLOUD_ASR=0",
          _lk["checked"] is False and _lk["enabled"] is False
          and "CLASSLIVE_CLOUD_ASR=0" in _lk["title"], str(_lk))
    check("⭐ 关了云端 → 那一格写「已就绪 · 全本地」（不再有常驻开关，状态要看得见）",
          E.ready_item_text({**_ok, "cloud_off": True}) == "✓  语音模型  全本地",
          E.ready_item_text({**_ok, "cloud_off": True}))
    check("只有一颗开关（测试模式）时 y = 垂直居中",
          abs(E.switch_y(0, 1) - (E.DROP_H - E.BTN_H) / 2.0) < 1e-9, str(E.switch_y(0, 1)))

    try:
        from AppKit import NSApplication, NSApplicationActivationPolicyAccessory, NSButton
        import entry_launch
        import ready as ready_mod
    except Exception as e:                                    # noqa: BLE001
        check("AppKit 可用（这一节要真窗口）", False, f"{type(e).__name__}: {e}")
        return
    NSApplication.sharedApplication().setActivationPolicy_(
        NSApplicationActivationPolicyAccessory)

    def _walk(v, out):
        out.append(v)
        for c in (v.subviews() or []):
            _walk(c, out)
        return out

    def _btns(h, needle):
        return [v for v in _walk(h.window.contentView(), [])
                if isinstance(v, NSButton) and needle in (v.title() or "")]

    _w2, _chip_of, _items_of, _titles2 = _chip_helpers()

    menus: list = []
    _orig_popup = E.popup_menu
    E.popup_menu = lambda menu, view: menus.append(menu)

    def _open_menu(h):
        """点胶囊 → 顶端那份菜单（就绪每一项各一行 + 两个勾选项）。"""
        menus.clear()
        chip = _chip_of(h)
        if chip is None:
            return None, None
        chip.performClick_(None)
        return chip, (menus[-1] if menus else None)

    def _row(menu, needle):
        return next((i for i in _items_of(menu) if needle in (i.title() or "")), None)

    def _act(item):
        item.target().act_(item)

    _OK4 = [{"key": "mic", "state": "ok"}, {"key": "models", "state": "ok"},
            {"key": "engine", "state": "ok", "detail": "云端翻译（推荐）"},
            {"key": "vault", "state": "ok", "detail": "/tmp/V"}]

    try:
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            gl = root / "glossary.txt"
            gl.write_text("# public table\n", encoding="utf-8")

            def _build(name, items=None, **kw):
                # ⚠️ 就绪那四项用内存里的桩（否则"菜单里有没有这一行"会看机器的脸色）
                _o = ready_mod.items
                if items is not None:
                    ready_mod.items = lambda **k: [dict(i) for i in items]
                try:
                    return E.build(glossary=gl, sessions_dir=root / ("s" + name),
                                   state_root=root / ("st" + name),
                                   on_start=lambda c: None, **kw)
                finally:
                    ready_mod.items = _o

            calls: list = []
            h = _build("1", _OK4, on_test_mode=lambda on: True, test_mode=False,
                       on_cloud_asr=lambda on: (calls.append(on), True)[1], cloud_asr=True)
            try:
                check("⭐⭐ 面板上没有「云端转写」那颗常驻开关，也没有「测试模式」按钮"
                      "（两颗都进了胶囊菜单）",
                      h is not None and not _btns(h, "云端转写")
                      and not _btns(h, "测试模式："))
                chip, menu = _open_menu(h) if h is not None else (None, None)
                ci = _row(menu, "云端转写") if menu is not None else None
                check("⭐⭐ 点胶囊 → 弹菜单，顶层有「云端转写」且是勾着的",
                      ci is not None and ci.state() == 1 and ci.isEnabled(),
                      f"{_titles2(menu) if menu is not None else None}")
                if ci is not None:
                    check("⭐ 菜单项的 target 活着（弱引用坑：没挂住 = 点了没反应）",
                          ci.target() is not None)
                    _act(ci)
                    check("⭐⭐ 点一下 → 回调收到 False", calls == [False], str(calls))
                    _, menu2 = _open_menu(h)
                    ci2 = _row(menu2, "云端转写") if menu2 is not None else None
                    _mr2 = _row(menu2, "语音模型") if menu2 is not None else None
                    check("⭐⭐ 再开菜单 → 未勾，且「语音模型」那一行写着「全本地」",
                          ci2 is not None and ci2.state() == 0
                          and _mr2 is not None and "全本地" in (_mr2.title() or ""),
                          f"{ci2 and ci2.state()} {_mr2 and _mr2.title()!r}")
                    if ci2 is not None:
                        _act(ci2)
                        _, menu3 = _open_menu(h)
                        _mr3 = _row(menu3, "语音模型") if menu3 is not None else None
                        check("⭐ 再点 → 回调收到 True、「全本地」消失",
                              calls == [False, True]
                              and _mr3 is not None and "全本地" not in (_mr3.title() or ""),
                              f"{calls} {_mr3 and _mr3.title()!r}")
                _mr = _row(menu, "语音模型") if menu is not None else None
                check("⭐ 模型都齐了时，「语音模型」那一行是**灰的状态行**（不给空子菜单）",
                      _mr is not None and _mr.submenu() is None and not _mr.isEnabled(),
                      f"submenu={_mr and _mr.submenu()} enabled={_mr and _mr.isEnabled()}")
            finally:
                if h is not None:
                    h.close()

            # ── ⭐ 缺模型那一档：「语音模型」行给子菜单，里面能点「下载缺的模型」 ──
            #    ⚠️ **不点它**（会真下模型）—— 只钉「有这一项、可点、target 活着」。
            #    ⚠️ 而且这一档**必须有第二项也不正常**：只有模型一件事时胶囊走「一项」那条路，
            #       点一下**直接开下载**（那一档下面单独钉，这里绝不能点到）。
            h3 = _build("3", [{**_OK4[0], "state": "warn"},
                              {"key": "models", "state": "todo",
                               "detail": "还差 2 件，约 640.61 MB",
                               "left": ["a", "b"], "mb": 640.61}, *_OK4[2:]],
                        on_cloud_asr=lambda on: True, cloud_asr=True)
            try:
                chip3, menu = _open_menu(h3) if h3 is not None else (None, None)
                check("（前置）两项不正常 → 胶囊走「多项」那条路（点开菜单，不是直接下载）",
                      chip3 is not None and "2 项" in (chip3.title() or ""),
                      repr(chip3 and chip3.title()))
                _mr = _row(menu, "语音模型") if menu is not None else None
                _sub = _mr.submenu() if _mr is not None else None
                _dl = next((i for i in _items_of(_sub)
                            if "下载缺的模型" in (i.title() or "")), None) if _sub else None
                check("⭐⭐ 缺模型 → 「语音模型」行有子菜单，里面「下载缺的模型」可点、target 活着",
                      _dl is not None and _dl.isEnabled() and _dl.target() is not None,
                      f"{_titles2(_sub) if _sub is not None else None}")
            finally:
                if h3 is not None:
                    h3.close()

            c2: list = []
            h2 = _build("2", _OK4, on_cloud_asr=lambda on: (c2.append(on), False)[1],
                        cloud_asr=False)
            try:
                _, menu = _open_menu(h2) if h2 else (None, None)
                ci = _row(menu, "云端转写") if menu is not None else None
                if ci is not None:
                    check("（前置）初值=False → 未勾", ci.state() == 0)
                    _act(ci)
                    _, menu_after = _open_menu(h2)
                    ci_after = _row(menu_after, "云端转写") if menu_after is not None else None
                    check("⭐⭐ 写盘失败 → 不翻（再开菜单仍未勾）",
                          c2 == [True] and ci_after is not None and ci_after.state() == 0, f"{c2}")
                else:
                    check("（前置）菜单里有云端转写", False)
            finally:
                if h2 is not None:
                    h2.close()

            c4: list = []
            h4 = _build("4", _OK4, on_cloud_asr=lambda on: (c4.append(on), True)[1],
                        cloud_asr=True, cloud_asr_locked=True)
            try:
                _, menu = _open_menu(h4) if h4 else (None, None)
                ci = _row(menu, "云端转写") if menu is not None else None
                check("⭐⭐ 锁定：菜单项未勾、不可点、标题点名环境变量",
                      ci is not None and ci.state() == 0 and not ci.isEnabled()
                      and "CLASSLIVE_CLOUD_ASR=0" in (ci.title() or ""), str(ci and ci.title()))
                if ci is not None:
                    _act(ci)
                    check("锁定时即使硬触发 action，回调也不调", c4 == [], str(c4))
            finally:
                if h4 is not None:
                    h4.close()
    finally:
        E.popup_menu = _orig_popup

    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        # ── entry_launch 接线：回调真的写 final_asr 配置；初值读得回 ──────
        import final_asr
        import PyObjCTools.AppHelper as _AH
        _orig_open, _orig_stop = E.open_panel, _AH.stopEventLoop
        _env_saved = {k: os.environ.get(k) for k in (final_asr.ENV_SWITCH,)}
        _root_saved = final_asr.paths.STATE_ROOT
        seen: dict = {}

        def _fake_open(**kw):
            seen.update(init=kw.get("cloud_asr"), locked=kw.get("cloud_asr_locked"),
                        has=kw.get("on_cloud_asr") is not None)
            if kw.get("on_cloud_asr"):
                seen["ret"] = kw["on_cloud_asr"](seen.get("click", False))
            kw["on_close"]()
            return object()

        try:
            # ⚠️ 隔离写端：`STATE_ROOT` 是 import 时算好的常量，改 $HOME 没用（第一版就这样
            #    把真 ~/.classlive/cloud-asr.json 写了）→ 直接换掉 `paths.STATE_ROOT`。
            final_asr.paths.STATE_ROOT = root / "state-home"
            os.environ.pop(final_asr.ENV_SWITCH, None)
            _AH.stopEventLoop = lambda: None            # 见 ⑳：否则 terminate_ 杀进程
            E.open_panel = _fake_open
            seen["click"] = False
            entry_launch.main()
            check("⭐⭐ 面板点「关」→ entry_launch 真写了配置（read_config 读回 enabled=False）",
                  seen.get("ret") is True and final_asr.read_config()["enabled"] is False
                  and str(final_asr.paths.cloud_asr_config()).startswith(str(root)),
                  f"{seen} {final_asr.read_config()}")
            seen.clear(); seen["click"] = True
            entry_launch.main()
            check("⭐ 再点「开」→ 读回 True；且下次开面板初值 = 盘上的值",
                  final_asr.read_config()["enabled"] is True, str(seen))
            seen.clear(); seen["click"] = False
            entry_launch.main()
            seen2 = dict(seen); seen.clear()
            entry_launch.main()
            check("⭐ 初值来自 read_config：上次写 False -> 这次面板拿到 False",
                  seen2.get("init") is True and seen.get("init") is False,
                  f"{seen2} {seen}")
            os.environ[final_asr.ENV_SWITCH] = "0"
            seen.clear(); seen["click"] = True
            entry_launch.main()
            check("⭐ CLASSLIVE_CLOUD_ASR=0：面板拿到 locked=True、init=False，回调返回 False 且不写盘",
                  seen.get("locked") is True and seen.get("init") is False
                  and seen.get("ret") is False, str(seen))
        finally:
            E.open_panel, _AH.stopEventLoop = _orig_open, _orig_stop
            final_asr.paths.STATE_ROOT = _root_saved
            for k, v in _env_saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


def _test_mode_section() -> None:
    """⑳ 测试模式开关：**两态文案 / 胶囊菜单里那颗勾选项 / 退回落点条那一档 / 那个开关文件**。

    ⚠️ 为什么要建窗口：这一节钉的全是**接线**（开关在不在、点了调没调到、
       标题变没变、失败翻没翻回去），而接线的坏法**一律不出声** ——
       纯函数判据全绿，症状统一是「点了没反应」或**界面说的和盘上的不是一回事**。

    ⭐ 2026-10-16（`PLAN-396` §1）：它**不再是面板上一颗常驻按钮** —— 就绪那一格在时
       它是**标题行胶囊菜单里的勾选项**；那一格没建出来（`ready.items` 抛了 /
       用户先前把就绪那一条关掉了）才退回落点条。两条路都在下面钉住。

    ⚠️ 只建窗口、**不跑事件循环** —— `performClick_` 是同步的（`sendAction:to:`）。
    ⚠️ 就绪那四项用**内存里的桩**（不碰磁盘 / 真权限状态），否则这一节的形状
       会跟着跑它的那台机器变（原来就是这样：放得下 / 放不下两条路各命中一半）。
    ⚠️ `popup_menu` 换成**桩**：真 `NSMenu` 会开一个同步的跟踪循环，测试会挂在那儿。

    ⚠️ 写端全部隔离在 tempdir：面板走 `glossary=` / `state_root=`，
       开关文件走 `read_test_mode(path=…)` / `write_test_mode(…, path=…)`。
       ⚠️ **绝不碰仓库根目录那份真 `.test-mode`**（「测试必须隔离写端」那条硬规矩）。

    ⚠️ 最后一组**真跑 `cl`**（沙盒 + 假解释器），不算越界：`cl` 那半是 shell，
       没有别的判据盖得到，而它正是「开关按了到底生效没有」的**最后一厘米**。
    """
    print("\n--- ⑳ 测试模式开关：文案 / 胶囊菜单里的勾选项 / 退回落点条 ---")
    import shutil as _sh
    import subprocess as _sp
    import tempfile

    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    try:
        from AppKit import (NSApplication, NSApplicationActivationPolicyAccessory,
                            NSButton, NSTextField, NSFontAttributeName)
        from Foundation import NSAttributedString
        import entry_launch
        import ready as ready_mod
    except Exception as e:                                    # noqa: BLE001
        check("AppKit 可用（这一节要真窗口）", False, f"{type(e).__name__}: {e}")
        return

    NSApplication.sharedApplication().setActivationPolicy_(
        NSApplicationActivationPolicyAccessory)

    def _walk(v, out):
        out.append(v)
        for c in (v.subviews() or []):
            _walk(c, out)
        return out

    def _text_w(s, font) -> float:
        """按**真实的那个字体**量文字宽度（不是按字数估）。"""
        a = NSAttributedString.alloc().initWithString_attributes_(
            s, {NSFontAttributeName: font})
        return float(a.size().width)

    def _strip_of(h):
        """落点条 = 窗口里那个 (W_IN × DROP_H) 的视图 —— 不靠"第几个子视图"猜。"""
        for v in _walk(h.window.contentView(), []):
            f = v.frame()
            if (abs(float(f.size.width) - (E.WIDTH - 2 * E.PAD)) < 0.6
                    and abs(float(f.size.height) - E.DROP_H) < 0.6):
                return v
        return None

    def _toggles(h):
        """面板上那几颗开关（按**文案前缀**找，不按下标找）。"""
        out = []
        for v in _walk(h.window.contentView(), []):
            if isinstance(v, NSButton):
                try:
                    if (v.title() or "").startswith("测试模式："):
                        out.append(v)
                except Exception:                             # noqa: BLE001
                    pass
        return out

    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        gl = root / "glossary.txt"
        gl.write_text("# public table\n", encoding="utf-8")

        def _build(name, **kw):
            return E.build(glossary=gl, sessions_dir=root / ("s" + name),
                           state_root=root / ("st" + name),
                           on_start=lambda c: None, **kw)

        calls: list = []
        _w2, _chip_of, _items_of, _t2 = _chip_helpers()

        # ⚠️⚠️ **期望值写字面量，不写 `E.test_mode_title(True)`。**
        #    第一版就是拿那个函数当期望值 —— **变异验证当场抓出来**：
        #    把函数改成「永远返回关」之后，**两边一起变**，判据照样绿。
        #    这就是本仓记过的假绿形态「判据钉住的正是缺陷本身」。
        #    文案是**作者拍板的那个东西**（`PLAN-test-mode.md` §3.5），
        #    所以要逐字钉住；函数只是它的一处实现。
        ON, OFF = "测试模式：开", "测试模式：关"
        check("⭐ 开关文案与作者拍板的两态**逐字一致**（函数与字面量对得上）",
              E.test_mode_title(True) == ON and E.test_mode_title(False) == OFF,
              f"{E.test_mode_title(True)!r} / {E.test_mode_title(False)!r}")
        _OK4 = [{"key": "mic", "state": "ok"}, {"key": "models", "state": "ok"},
                {"key": "engine", "state": "ok", "detail": "云端翻译（推荐）"},
                {"key": "vault", "state": "ok", "detail": str(root / "V")}]
        _menus: list = []
        _orig_popup = E.popup_menu
        E.popup_menu = lambda menu, view: _menus.append(menu)

        def _mk(name, items, **kw):
            """建一块面板，就绪那四项**由我们说了算**（内存里的桩，不碰磁盘/真权限）。"""
            _o = ready_mod.items
            ready_mod.items = lambda **k: [dict(i) for i in items]
            try:
                return _build(name, **kw)
            finally:
                ready_mod.items = _o

        def _tm_item(h):
            """胶囊菜单里那颗「测试模式」勾选项（先开菜单）。"""
            chip = _chip_of(h)
            if chip is None:
                return None
            _menus.clear()
            chip.performClick_(None)
            if not _menus:
                return None
            return next((i for i in _items_of(_menus[-1])
                         if "测试模式" in (i.title() or "")), None)

        try:
            # ── ⭐ 正路（就绪那一格在）：开关 = 胶囊菜单里的勾选项 ────────
            h = _mk("1", _OK4, on_test_mode=lambda on: (calls.append(on), True)[1],
                    test_mode=False)
            check("面板建起来了（⑳ 这一节要真窗口）", h is not None)
            if h is not None:
                try:
                    check("⭐ 面板上**不再有**那颗常驻开关（2026-10-16 挪进胶囊菜单）",
                          not _toggles(h),
                          f"还命中 {[b.title() for b in _toggles(h)]}")
                    ti = _tm_item(h)
                    check("⭐ 菜单里那颗勾选项：**未勾**、标题逐字「测试模式：关」",
                          ti is not None and ti.state() == 0 and (ti.title() or "") == OFF,
                          f"{ti and (ti.title(), ti.state())}")
                    if ti is not None:
                        check("⭐ 它的 target 活着（弱引用坑：没挂住 = 点了没反应）",
                              ti.target() is not None)
                        ti.target().act_(ti)
                        check("⭐⭐ 点一下 → 回调真的收到 True", calls == [True], str(calls))
                        ti2 = _tm_item(h)
                        check("⭐ 再开菜单 → 勾上了、标题变「测试模式：开」",
                              ti2 is not None and ti2.state() == 1
                              and (ti2.title() or "") == ON,
                              repr(ti2 and (ti2.title(), ti2.state())))
                finally:
                    h.close()

            # ── ⭐⭐ 落盘失败 → **弹回原状**（界面不许撒谎）────────────────
            # ⚠️ 照 `.course` 那次事故（2026-09-29 的 `UNSAVED`）：写不下去时不改状态。
            #    这里更坏 —— 显示「开」而盘上是「关」= 用户以为在采集，其实什么都没记。
            calls2: list = []
            h2 = _mk("2", _OK4, on_test_mode=lambda on: (calls2.append(on), False)[1],
                     test_mode=False)
            try:
                ti = _tm_item(h2)
                check("（前置）第二块面板的菜单里也有那颗勾选项", ti is not None)
                if ti is not None:
                    ti.target().act_(ti)
                    ti2 = _tm_item(h2)
                    check("⭐⭐ 写盘失败 → **不翻**（再开菜单仍未勾、标题仍是「关」）",
                          calls2 == [True] and ti2 is not None and ti2.state() == 0
                          and (ti2.title() or "") == OFF,
                          f"{calls2} {ti2 and (ti2.title(), ti2.state())}")
            finally:
                h2.close()

            # ── ⚠️ 退路：就绪那一格没建出来 → 落点条上那颗按钮（原样子）────
            # 这一档 = `ready.items` 抛了 / 用户先前把就绪那一条关掉了（`dismissed()`）。
            c3: list = []
            h3 = _mk("3", [], on_test_mode=lambda on: (c3.append(on), True)[1],
                     test_mode=False)
            try:
                sw3 = _toggles(h3)
                check("⭐⭐ 就绪那一格没建出来 → 开关**退回落点条**（不许跟着一起消失）",
                      len(sw3) == 1, f"命中 {len(sw3)} 颗")
                if len(sw3) == 1:
                    strip3 = _strip_of(h3)
                    check("⭐ 而且它就挂在落点条上",
                          strip3 is not None and sw3[0].superview() is strip3)
                    sw3[0].performClick_(None)
                    check("⭐ 点一下 → 文案变「测试模式：开」、回调收到 True",
                          (sw3[0].title() or "") == ON and c3 == [True],
                          f"{sw3[0].title()!r} {c3}")
                # ── ⭐⭐ 提示文字塞得下 ───────────────────────────────────
                # ⚠️ 这一档（开关退回落点条）是**最紧**的一档：`HINT_W_TIGHT` 要比
                #    `HINT_W` 多让一颗开关的位置。文字超框 → `panel.make_label`
                #    默认就是换行 + **第二行被静默吃掉**（没有省略号，看着像句子就到这儿）。
                strip3 = _strip_of(h3)
                over, measured = [], 0
                for v in (strip3.subviews() if strip3 is not None else []):
                    if v.isHidden() or not isinstance(v, NSTextField):
                        continue
                    t = v.stringValue() or ""
                    if not t:
                        continue
                    measured += 1
                    w = _text_w(t, v.font())
                    if w > float(v.frame().size.width) + 0.01:
                        over.append((t, round(w, 1), round(float(v.frame().size.width), 1)))
                check("⭐⭐ 条上每条提示文字**实测宽度 ≤ 它的框宽**（超了就被静默吃掉）",
                      not over, str(over))
                check("⚠️ 而且真的量到了那几行（量到 0 行会让上一条恒真）",
                      measured >= 2, f"量到 {measured} 行")
            finally:
                h3.close()

            # ⚠️ 结构：没有落点（`on_test_mode=None`）时**一颗都不建**
            #    这一档 = 上课中从菜单栏打开的面板（那时这节课早在录了），开关没有意义。
            h4 = _mk("4", _OK4)
            try:
                check("⭐ `on_test_mode=None` → 面板上没有开关，菜单里也没有那一项",
                      h4 is not None and not _toggles(h4) and _tm_item(h4) is None,
                      f"命中 {len(_toggles(h4)) if h4 is not None else '—'} 颗")
            finally:
                h4.close()
            # 反方向：初值 True 时要显示「开」（否则上面那条会因为"永远不建"而假绿）
            h5 = _mk("5", _OK4, on_test_mode=lambda on: True, test_mode=True)
            try:
                ti = _tm_item(h5)
                check("⭐ `test_mode=True` → 菜单里那颗是勾着、标题写「开」",
                      ti is not None and ti.state() == 1 and (ti.title() or "") == ON,
                      repr(ti and (ti.title(), ti.state())))
            finally:
                h5.close()
        finally:
            E.popup_menu = _orig_popup

        # ── ⚠️ 两态的文字宽必须**一模一样**（切换时文字不许抖）────────────
        from AppKit import NSFont as _NSFont
        _font = _NSFont.systemFontOfSize_(12.0)
        _won = _text_w(E.test_mode_title(True), _font)
        _woff = _text_w(E.test_mode_title(False), _font)
        check("⚠️ 两态**实测文字宽完全相同**（切换时文字不抖）",
              abs(_won - _woff) < 1e-9, f"开={_won:.1f} 关={_woff:.1f}")
        check("⭐ 而且塞得进退回落点条时那颗按钮的框（`TEST_W` 要留得下它）",
              max(_won, _woff) <= E.TEST_W - 8.0,
              f"文字 {max(_won, _woff):.1f} / 框 {E.TEST_W}")

        # ── ⭐ 开关文件：读写的**全部边界** ──────────────────────────────
        # ⚠️ 走 `path=` 参数隔离 —— 绝不碰仓库根目录那份真文件。
        _p = root / "dot-test-mode"
        check("⭐ 写 1 → 读回 True",
              entry_launch.write_test_mode(True, path=_p) is True
              and entry_launch.read_test_mode(path=_p) is True)
        check("⭐ 而且盘上**就是 `1` 两个字符**（`cl` 那边比的正是它）",
              _p.read_text(encoding="utf-8") == "1", repr(_p.read_text(encoding="utf-8")))
        check("⭐ 写 0 → 读回 False（**关必须能盖掉上次的开**）",
              entry_launch.write_test_mode(False, path=_p) is True
              and entry_launch.read_test_mode(path=_p) is False)
        for bad in ("", "\n", "垃圾", "true", "yes", "２"):
            _p.write_text(bad, encoding="utf-8")
            check(f"⚠️ 内容是 {bad!r} → 算**关**（认不出来就绝不能当成开着 —— "
                  f"那是**会录音**的一条路）",
                  entry_launch.read_test_mode(path=_p) is False)
        _p.unlink()
        check("⭐ 文件不存在 → 关（**默认值**：装上就应该是关的）",
              entry_launch.read_test_mode(path=_p) is False)
        # ⚠️ 变异验证：把 `== "1"` 改成 `p.exists()` -> 上面「0 / 垃圾」那几条全红。

        # ── ⭐⭐ 最后一厘米（前半）：`entry_launch` 真的把开关落到盘上 ──────
        # 走 `entry_launch.main()` 那条**真路**（只把 `open_panel` 换成桩，同 ⑨ 的做法），
        # 断言它交给面板的那个回调**一调就写盘**，而且下次开面板读得回来。
        # ⚠️ `TESTMODE_CFG` 换到 tempdir —— 绝不碰仓库根目录那份真文件。
        #
        # ⚠️⚠️ **`AppHelper.stopEventLoop()` 会当场杀掉整个进程。** 实测（2026-09-30）：
        #    PyObjC 那份源码逐字是 —— 拿不到当前 run loop 的 stopper 时，
        #    `if NSApp() is not None: NSApp().terminate_(None)`。
        #    而这一节**已经建过 `NSApplication.sharedApplication()`**（⑬ 也建过）
        #    → `NSApp()` 非空 → `terminate_` → **进程直接退出，rc=0、一个字都不报**，
        #    汇总行也不打（第一版就是这么挂的，症状是「跑到一半日志没了」）。
        #    ⚠️ ⑨ 那组同样调 `on_close()`/`on_start()`，但它跑在**建 NSApplication 之前**
        #    → `NSApp()` 还是 None → 无害。**别把 ⑨ 挪到 ⑬ 后面。**
        #    → 这里把它换成空实现：我们要测的是「回调写没写盘」，不是 AppKit 的退出。
        import PyObjCTools.AppHelper as _AH
        _tm_saved = entry_launch.TESTMODE_CFG
        _tm_path = root / "entry-launch-test-mode"
        _orig_open = E.open_panel
        _orig_stop = _AH.stopEventLoop
        entry_launch.TESTMODE_CFG = _tm_path
        seen: dict = {}

        def _fake_open(**kw):
            seen["has_cb"] = kw.get("on_test_mode") is not None
            seen["init"] = kw.get("test_mode")
            seen["ret"] = kw["on_test_mode"](True)      # = 用户在面板上点亮它
            kw["on_close"]()                            # ⚠️ 立 `done` 闩，否则进 run loop 挂死
            return object()

        try:
            _AH.stopEventLoop = lambda: None            # ⚠️ 见上面的说明
            E.open_panel = _fake_open
            entry_launch.main()
            check("⭐⭐ 面板点了开关 → `entry_launch` **真的写了盘**（`1`）",
                  _tm_path.exists()
                  and _tm_path.read_text(encoding="utf-8") == "1"
                  and seen.get("ret") is True,
                  f"文件={_tm_path.read_text(encoding='utf-8') if _tm_path.exists() else '（没有）'} "
                  f"回调返回={seen.get('ret')!r}")
            check("⭐ 第一次开面板：初值给的是「关」（盘上本来没有这个文件）",
                  seen.get("init") is False and seen.get("has_cb") is True,
                  f"init={seen.get('init')!r} 有回调={seen.get('has_cb')}")
            seen.clear()
            entry_launch.main()
            check("⭐⭐ 下次开面板：**读得回上次写的那份**（初值变「开」）",
                  seen.get("init") is True, f"init={seen.get('init')!r}")
        finally:
            E.open_panel = _orig_open
            _AH.stopEventLoop = _orig_stop
            entry_launch.TESTMODE_CFG = _tm_saved

        # ── ⭐⭐ 最后一厘米：`cl` 到底带没带 `--test-mode` ─────────────────
        # 沙盒里真跑 `cl`：把脚本复制过去 + 造一个**假解释器**（它只 echo 收到的参数）。
        # 于是这一条量的是**真实的参数**，不是"源码里有没有那行字"。
        sand = root / "sandbox"
        (sand / "ClassLive.app" / "Contents" / "MacOS").mkdir(parents=True)
        _sh.copy2(HERE / "cl", sand / "cl")
        _fake = sand / "ClassLive.app" / "Contents" / "MacOS" / "python"
        _fake.write_text('#!/bin/sh\necho "FAKEPY $@"\nexit 0\n', encoding="utf-8")
        _fake.chmod(0o755)
        (sand / ".course").write_text("ECON10740", encoding="utf-8")
        (sand / "a.wav").write_bytes(b"RIFF")

        def _run_cl(*args):
            pr = _sp.run(["bash", str(sand / "cl"), *args],
                         capture_output=True, text=True, timeout=30)
            return pr.stdout + pr.stderr

        (sand / ".test-mode").write_text("1", encoding="utf-8")
        _out = _run_cl()
        # ⚠️ detail 是急切求值的，且这里取的是**倒数第 2 行** —— 输出不足 2 行时会先
        #    抛 IndexError 把整轮带崩（真因被盖掉）。先切片、再判长度。
        _lines = _out.strip().splitlines()
        check("⭐⭐ 开关是「1」→ `cl` 真的给 main.py 加了 `--test-mode`",
              "--test-mode" in _out, _lines[-1] if _lines else "（空）")
        check("⭐ 而且 ▶ 那行报出来了（**不许静默**）",
              "测试模式=开" in _out,
              _lines[-2] if len(_lines) >= 2 else f"（只 {len(_lines)} 行）")
        (sand / ".test-mode").write_text("0", encoding="utf-8")
        check("⭐ 开关是「0」→ 一个都不加",
              "--test-mode" not in _run_cl(), "（还带着旗标）")
        (sand / ".test-mode").unlink()
        check("⭐ 没有那个文件 → 也不加（默认关）",
              "--test-mode" not in _run_cl(), "（还带着旗标）")
        (sand / ".test-mode").write_text("1", encoding="utf-8")
        _out_f = _run_cl("file", str(sand / "a.wav"))
        check("⭐⭐⚠️ `cl file`（回放既有录音）**不带**这个旗标 —— "
              "否则开关还开着时，一次普通回放会变成**会上传的测试会话**",
              "--test-mode" not in _out_f,
              _out_f.strip().splitlines()[-1] if _out_f.strip() else "（空）")
        check("⚠️ 前置：那条回放确实跑到了 exec（否则上一条是因为「啥都没跑」而假绿）",
              "main.py" in _out_f, _out_f.strip()[:120])

        # ── ⭐⭐ 退出码 5：`cl` 真的重开自己，而且有上限 ─────────────────────
        # 假解释器：被叫去跑 entry_launch.py 时记一次数，前 `LIMIT` 次退 5，之后退 1（关面板）。
        _fake.write_text(
            '#!/bin/sh\n'
            'if [ "$1" = entry_launch.py ]; then\n'
            '  n=$(cat "$(dirname "$0")/n" 2>/dev/null || echo 0); n=$((n+1))\n'
            '  echo "$n" > "$(dirname "$0")/n"\n'
            '  [ "$n" -le "$LIMIT" ] && exit 5; exit 1\n'
            'fi\n'
            'echo "FAKEPY $@"\nexit 0\n', encoding="utf-8")
        _cnt = _fake.parent / "n"

        def _restart_run(limit: int) -> tuple[int, str]:
            _cnt.unlink(missing_ok=True)
            env = {k: v for k, v in os.environ.items() if k != "CL_RESTARTS"}
            pr = _sp.run(["bash", str(sand / "cl")], capture_output=True, text=True,
                         timeout=30, env={**env, "LIMIT": str(limit)})
            n = int(_cnt.read_text().strip()) if _cnt.exists() else 0
            return n, pr.stdout + pr.stderr

        _n, _o = _restart_run(1)
        check("⭐⭐ 面板退 5 → `cl` 重开一次面板（第二次关掉就不录）",
              _n == 2 and "重开面板" in _o and "main.py" not in _o, f"n={_n} {_o[-160:]!r}")
        _n, _o = _restart_run(99)
        check("⭐⭐ 面板一直退 5 → 最多重开两次就停（不死循环、不录课）",
              _n == 3 and "不再重开" in _o and "main.py" not in _o, f"n={_n} {_o[-160:]!r}")


def _target_liveness_section() -> None:
    """⑭ 面板上**每个能点的控件都必须有活着的 target**（弱引用那条）。

    ⚠️ 为什么单开一节，以及为什么**必须在这个函数返回窗口之后**再问：

    这条 bug 的坏法是「接线没生效，但哪儿都不报错」—— 纯函数判据全绿、
    `isEnabled` 也可能是真的，症状统一是**点了没反应**。本仓库被它咬过两次：

      ① 右键「删除课程」菜单项（2026-09-28 作者真机报「灰色点不动」）
      ② 搜索框回车 **+ 整条就绪条**（2026-09-28 OCR 审计抓出搜索框；
         就绪条那条是本机实测补上的 —— 条上每个按钮的 `target()` 都是 `None`）

    两次是**同一个形状**：`_target(…)` 的返回值只被**局部变量**接住。
    局部随栈帧消失，而 `setTarget_` 是**弱引用** → target 被回收 → 静默失效。
    ⭐ 所以判据的问法很关键：**问早了（还在建它的那个函数里）反而是绿的。**
    """
    print("\n--- ⑭ 每个能点的控件都有活着的 target（弱引用）---")
    import gc
    import tempfile

    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    try:
        from AppKit import (NSApplication, NSButton, NSSearchField,
                            NSApplicationActivationPolicyAccessory)
    except Exception as e:                                    # noqa: BLE001
        check("AppKit 可用（这一节要真窗口）", False, f"{type(e).__name__}: {e}")
        return

    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        gl = root / "glossary.txt"
        gl.write_text("# public table\n", encoding="utf-8")
        NSApplication.sharedApplication().setActivationPolicy_(
            NSApplicationActivationPolicyAccessory)

        # ⚠️ 就绪条只在**有可说的东西时**才画（三项都 ok 就不画）→ 想验它就得
        #    造一个出来。这里是**内存里的桩**，不碰磁盘、不碰真实权限状态。
        import ready as ready_mod
        _orig = ready_mod.items
        ready_mod.items = lambda **kw: [{"key": "perm", "state": "warn",
                                         "text": "麦克风未授权"}]
        try:
            h = E.build(glossary=gl, sessions_dir=root / "sessions",
                        state_root=root / "state", on_start=lambda c: None)
        finally:
            ready_mod.items = _orig
        if h is None:
            check("面板建起来了（⑭）", False)
            return
        try:
            gc.collect()                    # ⭐ 判据的前提：局部表已经消失
            views: list = []

            def _walk(v):
                views.append(v)
                for c in (v.subviews() or []):
                    _walk(c)

            _walk(h.window.contentView())
            dead = []
            for v in views:
                if isinstance(v, NSSearchField):
                    if v.target() is None:
                        dead.append("搜索框")
                elif isinstance(v, NSButton) and v.title():
                    if v.target() is None:
                        dead.append(str(v.title())[:20])
            check(f"⭐ {len(views)} 个视图里，可点控件的 target **全活着**（一个都不许是 None）",
                  not dead, f"死掉的：{dead}")
            check("⭐ 就绪胶囊**真的画出来了**（不然上一条是空对空）",
                  any(isinstance(v, NSButton) and "perm" in str(v.title())
                      for v in views),
                  # ⚠️ 标题是按 `key` + `state` 渲染的（`ready_item_text`），
                  #    **不含**桩里那个 `text` —— 第一版按 `text` 找，基线就红了一条。
                  # ⚠️ 2026-10-16 起它长这样：**只有这一项不正常** → 胶囊直接写那一项。
                  f"按钮标题们：{[str(v.title()) for v in views if isinstance(v, NSButton)]}")
        finally:
            try:
                h.close()
            except Exception:                                 # noqa: BLE001
                pass

        # ── ⭐ 面板复用的判据**必须含读写目标**（2026-09-28 OCR 审计）────────
        # 原来只看「有没有 on_start」：同一个进程里拿另一组 `glossary`/`state_root`
        # 再开面板会**复用旧面板** → 之后每一次增删改都写向**旧**路径。
        # 而本仓库有一条硬规矩「测试必须隔离写端」—— 不隔离就是写坏真数据。
        root_b = root / "第二组"
        (root_b / "glossary").mkdir(parents=True, exist_ok=True)
        gl_b = root_b / "glossary.txt"
        gl_b.write_text("# t\n", encoding="utf-8")
        ha = E.open_panel(glossary=gl, state_root=root / "stateA")
        hb = E.open_panel(glossary=gl, state_root=root / "stateA")   # 同一组 → 复用
        hc = E.open_panel(glossary=gl_b, state_root=root_b / "stateB")  # 另一组 → 重建
        check("⭐ 同一组路径：复用同一个面板", ha is hb, f"{ha is hb}")
        check("⭐⭐ 换一组路径：**必须重建**（否则后续读写全写到旧根）",
              ha is not hc, "居然复用了 —— 那就会写坏另一个根")
        for _h in (ha, hb, hc):
            try:
                _h.close()
            except Exception:                                 # noqa: BLE001
                pass


def _vault_section() -> None:
    """㉑ 笔记库那一格：文案（字面量）/ 点击真的存下 / 取消与写失败都不动界面。

    ⚠️ 弹窗换掉（`E.pick_folder`）—— 真 `NSOpenPanel.runModal` 会阻塞测试。
    ⚠️ 写端全部隔离在 tempdir（`state_root=`）；**绝不碰真 `~/.classlive/vault`**。
    ⚠️ 只建窗口、**不跑事件循环** —— `performClick_` 是同步的（同 ⑳ 那条）。
    """
    print("\n--- ㉑ 笔记库那一格：文案 / 点击真的存下 / 失败不许撒谎 ---")

    # ---- 文案：期望值钉**字面量**（不是再问一遍 `ready_short` 算出来）----
    _t = E.ready_item_text
    check("未设 → `—  笔记库  未设`（字面量）",
          _t({"key": "vault", "state": "unknown"}) == "—  笔记库  未设",
          repr(_t({"key": "vault", "state": "unknown"})))
    check("已设 → `✓  笔记库`",
          _t({"key": "vault", "state": "ok"}) == "✓  笔记库",
          repr(_t({"key": "vault", "state": "ok"})))
    check("路径没了 → `⚠  笔记库  找不到了`",
          _t({"key": "vault", "state": "warn"}) == "⚠  笔记库  找不到了",
          repr(_t({"key": "vault", "state": "warn"})))

    # ---- 更新那一格 / 草稿模型提议（2026-10-12）----
    check("有新版 → `↑  更新  3.9.0`（字面量）",
          _t({"key": "update", "state": "todo", "kind": "available", "detail": "有新版本 3.9.0"})
          == "↑  更新  3.9.0")
    check("没装完 → `↑  更新  没装完`",
          _t({"key": "update", "state": "todo", "kind": "unfinished", "detail": "还没装完：x"})
          == "↑  更新  没装完")
    check("装不上 → `⚠  更新  装不上`",
          _t({"key": "update", "state": "warn", "kind": "blocked", "detail": "你改过 a.py"})
          == "⚠  更新  装不上")
    check("更新中 → `◌  更新  更新中…`", _t({"key": "update", "state": "busy"}) == "◌  更新  更新中…")
    _uo = E.update_outcome
    check("⭐ 只差收尾、收尾没成 → 状态行说原因、那一格回原样可重试（不写「已是最新」）",
          _uo({"ok": True, "pulled": False, "user_msg": "新版本已装好，但网断了 —— 下次打开会再试一次。"}, True)
          == ("新版本已装好，但网断了 —— 下次打开会再试一次。", None, False))
    _m, _it, _rs = _uo({"ok": True, "pulled": False, "user_msg": ""}, True)
    check("真是最新 → `✓  更新  已是最新`", _m == "已经是最新的了" and not _rs and _t(_it) == "✓  更新  已是最新",
          f"{_m!r} {_it}")
    check("⭐⭐ 装完之后再点那一格 → 不再弹「上次更新还没装完」（Owen 10-12 实测：又弹装依赖）",
          E.update_click_plan(_it, False) == "done", E.update_click_plan(_it, False))
    check("换了代码 + 能重开 → 重开", _uo({"ok": True, "pulled": True}, True)[2] is True)
    _m, _it, _rs = _uo({"ok": True, "pulled": True}, False)
    check("换了代码、不能重开 → `✓  更新  已装好`，再点也不弹",
          _m == "装好了 —— 关掉面板再打开就是新版本" and _t(_it) == "✓  更新  已装好"
          and E.update_click_plan(_it, False) == "done", f"{_m!r} {_it}")
    check("没成 → 原因 + 回原样", _uo({"ok": False, "user_msg": "另一个更新正在进行"}, True)
          == ("另一个更新正在进行", None, False))
    check("点击计划：更新中 → busy；没装完 / 装不上 / 有新版各走各的",
          [E.update_click_plan({"kind": k}, u) for k, u in
           (("available", True), ("unfinished", False), ("blocked", False), ("available", False))]
          == ["busy", "unfinished", "blocked", "available"])
    check("草稿模型没装 → `✓  语音模型  已就绪 · 缺草稿模型`",
          _t({"key": "models", "state": "ok", "offer_draft": True})
          == "✓  语音模型  已就绪 · 缺草稿模型")

    # ---- 翻译引擎那一格（2026-10-04）：「未配」→「没配 key」，说清楚要做什么 ----
    check("翻译引擎没配 → `—  翻译引擎  没配 key`（字面量）",
          _t({"key": "engine", "state": "unknown", "detail": "点一下填 key（推荐）· 或下本地模型"})
          == "—  翻译引擎  没配 key",
          repr(_t({"key": "engine", "state": "unknown", "detail": "x"})))
    check("配了云端 → `✓  云端翻译`（没被上一条误伤）",
          _t({"key": "engine", "state": "ok", "detail": "云端翻译（推荐）"})
          == "✓  云端翻译",
          repr(_t({"key": "engine", "state": "ok", "detail": "云端翻译（推荐）"})))
    # ⭐ 正常时只写名字、不正常时状态词照留（2026-10-14 瘦身）
    _slim = [_t({"key": "mic", "state": "ok"}), _t({"key": "mic", "state": "warn"}),
             _t({"key": "models", "state": "ok"}), _t({"key": "engine", "state": "ok", "detail": "本地模型"}),
             _t({"key": "update", "state": "ok", "kind": "done", "detail": "已是最新"})]
    check("瘦身：麦克风 / 被拒 / 语音模型 / 本地翻译 / 更新回话（字面量）",
          _slim == ["✓  麦克风", "⚠  麦克风  被拒", "✓  语音模型", "✓  本地翻译", "✓  更新  已是最新"],
          repr(_slim))
    # key 对话框的提示不许再说「退回本地模型」：本地模型是可选的、默认没装，
    # 对新用户那句话是假的（没 key + 没本地模型 = 只有英文字幕）
    # 文案的唯一出处是 `keyentry.dialog_fields`（3.9.2 起，面板与截图探针同源）
    import keyentry as _KE
    _hint = _KE.dialog_fields()[0]["hint"]
    check("⭐ key 对话框不再承诺「退回本地模型」（它默认没装）",
          "退回本地模型" not in _hint and "不填只有英文字幕" in _hint, repr(_hint))

    try:
        import os
        import tempfile

        from AppKit import (NSApplication, NSApplicationActivationPolicyAccessory,
                            NSButton)
        import ready as ready_mod
    except Exception as e:                                    # noqa: BLE001
        check("AppKit 可用（这一节要真窗口）", False, f"{type(e).__name__}: {e}")
        return
    NSApplication.sharedApplication().setActivationPolicy_(
        NSApplicationActivationPolicyAccessory)

    def _walk(v, out):
        out.append(v)
        for c in (v.subviews() or []):
            _walk(c, out)
        return out

    orig_pick = E.pick_folder    # ⚠️ `$OBSIDIAN_VAULT` **必须挪开**：本机 shell 里它指着真库 → 面板一开就是
    #    「已设」→「点了之后变已设」那条判据**恒真**（2026-10-01 变异验证抓到的）。
    #    挪开之后才是朋友那台机器的入场状态（未设 → 点了才变）。
    _saved_env = os.environ.pop("OBSIDIAN_VAULT", None)
    # ⚠️ 8-F 起**家目录也要挪**：自动探测会读**真的** Obsidian 注册表（本机就有
    #    SecondBrain）→ 把"未设入场"的前提搅掉，还会往沙盒里写进真库路径。
    _saved_home = os.environ.get("HOME")
    # ⚠️ 2026-10-16：就绪那一格不再是常驻按钮 —— 「笔记库」这一格的落点要么是
    #    **胶囊**（只有它一项不正常），要么是**胶囊菜单里那一行**（两项以上）。
    #    `_ReadyAccess` 把两条路封成一个口子；菜单用桩（真 `NSMenu` 会挂住测试）。
    _menus: list = []
    _orig_popup = E.popup_menu
    E.popup_menu = lambda menu, view: _menus.append(menu)
    _acc = _ReadyAccess(_menus)
    try:
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            _fake_home = root / "home"          # 空家目录 = 找不到任何库（8-F 的"0 个"档）
            _fake_home.mkdir()
            os.environ["HOME"] = str(_fake_home)
            gl = root / "glossary.txt"
            gl.write_text("# public table\n", encoding="utf-8")

            def _build(name, st):
                # ⚠️ 2026-10-16（`PLAN-396` §1）：就绪项的落点有**两个** ——
                #    只有它一项不正常时写在**胶囊**上（点它 = 直接修），两项以上时在**菜单**里。
                #    这里把「麦克风」「语音模型」两项**钉成「要处理」**，于是永远是「两项以上」
                #    那一档：「笔记库」那一行稳定地在菜单里 —— 判据才能跨状态读到它的字
                #    （否则修好之后胶囊会改去说**另一件**事，判据就读不到它了），
                #    而且判据点胶囊时**永远不会**点到「语音模型 · 下载」那条真下载的路。
                _o = ready_mod.items

                def _items(**kw):
                    out = _o(**kw)
                    return [{**i, "state": "warn"} if i.get("key") == "mic"
                            else ({**i, "state": "todo", "detail": "还差 1 件"}
                                  if i.get("key") == "models" else i)
                            for i in out]

                ready_mod.items = _items
                try:
                    return E.build(glossary=gl, sessions_dir=root / name,
                                   state_root=st, on_start=lambda c: None)
                finally:
                    ready_mod.items = _o

            # ── 点击：真的存进 `<state_root>/vault`，并且**就地**改字 ──────
            st = root / "st1"
            picked = root / "MyVault"
            picked.mkdir()
            (picked / ".obsidian").mkdir()                     # 像一个真库
            calls: list = []
            E.pick_folder = lambda **kw: (calls.append(kw), str(picked))[1]
            h = _build("s1", st)
            try:
                _lbl0 = _acc.label(h, "笔记库") if h is not None else None
                check("（前置）面板上找得到「笔记库」这一格（胶囊 / 胶囊菜单里那一行）",
                      _lbl0 is not None, repr(_lbl0))
                if _lbl0 is not None:
                    check("（前置）这一格**一开始是「未设」**—— 环境变量挪开了才测得出「变」",
                          _lbl0 == "—  笔记库  未设", repr(_lbl0))
                    check("⭐ 点得动（两条落点都通）", _acc.click(h, "笔记库"))
                    check("⭐ 点了真的弹了选文件夹（一次，且带着智能起点）",
                          len(calls) == 1 and bool(calls[0].get("start")), str(calls))
                    check("⭐⭐ 选完**真的写进了 `<state_root>/vault`**（写端隔离）",
                          (st / "vault").exists()
                          and (st / "vault").read_text(encoding="utf-8").strip()
                          == str(picked),
                          repr((st / "vault").read_text(encoding="utf-8")
                               if (st / "vault").exists() else None))
                    check("⭐ 而且那一格**就地**变成「已设」（不用重开面板）",
                          _acc.label(h, "笔记库") == "✓  笔记库",
                          repr(_acc.label(h, "笔记库")))
            finally:
                try:
                    h.close()
                except Exception:                              # noqa: BLE001
                    pass

            # ── 取消：什么都不动 ─────────────────────────────────────────
            st2 = root / "st2"
            E.pick_folder = lambda **kw: None
            h2 = _build("s2", st2)
            try:
                before = _acc.label(h2, "笔记库") if h2 is not None else None
                if before is not None:
                    _acc.click(h2, "笔记库")
                    check("⭐ 取消选目录 → 标题不动、盘上什么都不写",
                          _acc.label(h2, "笔记库") == before
                          and not (st2 / "vault").exists(),
                          f"{before!r} {(st2 / 'vault').exists()}")
            finally:
                try:
                    h2.close()
                except Exception:                              # noqa: BLE001
                    pass

            # ── 8-F①：家目录里恰好 1 个库 → 面板一开就**自动设上** ──────────
            # ⚠️ 2026-10-01（审查 F24）：兜底扫描**跳过 TCC 三目录**（Desktop/
            #    Documents/Downloads）—— 库放在普通顶层目录（这里 `Vaults/`），
            #    并放一个 `Documents/` 里的诱饵钉住「跳过」这条契约。
            homeA = root / "homeA"
            vA = homeA / "Vaults" / "MyVault"
            vA.mkdir(parents=True)
            (vA / ".obsidian").mkdir()
            _decoy = homeA / "Documents" / "Decoy"
            _decoy.mkdir(parents=True)
            (_decoy / ".obsidian").mkdir()
            os.environ["HOME"] = str(homeA)
            stA = root / "stA"
            hA = _build("A", stA)
            try:
                _lblA = _acc.label(hA, "笔记库") if hA is not None else None
                check("⭐⭐ 8-F：只有 1 个库 → **自动设上**（写进 `<state_root>/vault`）",
                      (stA / "vault").exists()
                      and (stA / "vault").read_text(encoding="utf-8").strip() == str(vA),
                      repr((stA / "vault").read_text(encoding="utf-8")
                           if (stA / "vault").exists() else None))
                check("⭐ 而且它**不是** `Documents/` 里的诱饵（F24：TCC 目录跳过）",
                      (stA / "vault").exists()
                      and str(_decoy) not in (stA / "vault").read_text(encoding="utf-8"))
                check("⭐ 而且那一格直接就是「已设」",
                      _lblA == "✓  笔记库", repr(_lblA))
            finally:
                os.environ["HOME"] = str(_fake_home)
                try:
                    hA.close()
                except Exception:                          # noqa: BLE001
                    pass

            # ── 8-F②：多个候选 → **绝不猜**（不设、不写；点开预指向其中之一）──
            homeB = root / "homeB"
            v1 = homeB / "Vaults" / "Two"          # ⚠️ F24：同①，别放 Documents
            v1.mkdir(parents=True)
            (v1 / ".obsidian").mkdir()
            v2 = homeB / "Obsidian" / "One"
            v2.mkdir(parents=True)
            (v2 / ".obsidian").mkdir()
            os.environ["HOME"] = str(homeB)
            stB = root / "stB"
            hB = _build("B", stB)
            try:
                _lblB = _acc.label(hB, "笔记库") if hB is not None else None
                check("⭐⭐ 8-F：多个候选 → **绝不猜**（格子仍未设、一个字节不写）",
                      _lblB == "—  笔记库  未设"
                      and not (stB / "vault").exists(),
                      f"{_lblB!r} vault_written={(stB / 'vault').exists()}")
                calls4: list = []
                E.pick_folder = lambda **kw: (calls4.append(kw), None)[1]
                if _lblB is not None:
                    _acc.click(hB, "笔记库")
                    check("⭐ 点开时预指向候选之一，且框里明说找到 2 个",
                          len(calls4) == 1
                          and calls4[0].get("start") in (str(v1), str(v2))
                          and "2" in str(calls4[0].get("message") or ""),
                          str(calls4))
            finally:
                os.environ["HOME"] = str(_fake_home)
                try:
                    hB.close()
                except Exception:                          # noqa: BLE001
                    pass

            # ── 写盘失败：**界面不许撒谎**（同测试模式开关那条）──────────
            st3 = root / "st3"
            E.pick_folder = lambda **kw: str(picked)
            import obsidian_writer as _OW
            orig_remember = _OW.remember_vault
            _OW.remember_vault = lambda *a, **k: None          # 静默失败
            h3 = _build("s3", st3)
            try:
                before3 = _acc.label(h3, "笔记库") if h3 is not None else None
                if before3 is not None:
                    _acc.click(h3, "笔记库")
                    check("⭐⭐ 写盘失败 → 标题**不许**变成「已设」",
                          _acc.label(h3, "笔记库") == before3,
                          repr(_acc.label(h3, "笔记库")))
            finally:
                _OW.remember_vault = orig_remember
                try:
                    h3.close()
                except Exception:                              # noqa: BLE001
                    pass
    finally:
        # ⚠️ 「原本没有」这一档也要还原：只写 `if _saved is not None: environ[k] = _saved`
        #    时，`HOME` 原本没设的话，本节点跑完它还**指着那个已被删掉的 tempdir** ——
        #    泄漏给后面所有节点（谁 `expanduser` 一下就踩到不存在的家目录）。
        #    本文件的立身之本就是「测试必须隔离写端」，还原要覆盖两种起点。
        if _saved_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = _saved_home
        if _saved_env is None:
            os.environ.pop("OBSIDIAN_VAULT", None)
        else:
            os.environ["OBSIDIAN_VAULT"] = _saved_env
        E.pick_folder = orig_pick
        E.popup_menu = _orig_popup


def _key_entry_section() -> None:
    """㉒ 填 key 那格：**回填**（重开不空白）+ 点一下真的走到 `ask_text`。

    ⚠️ 弹窗换掉（patch `notice.ask_text`）—— 真 `runModal` 会阻塞测试。
    ⚠️ 读端也换（patch `cloud_translator.load_api_key` / `keyentry.load_jev`）——
       不然判据读的是**真实用户**的 key（同 `test_keyentry` ③ 那条纪律：
       读端和写端都要能换）。
    """
    print("\n--- ㉒ 填 key：回填已存的、点一下真的开框 ---")
    try:
        import tempfile

        from AppKit import (NSApplication, NSApplicationActivationPolicyAccessory,
                            NSButton)
    except Exception as e:                                    # noqa: BLE001
        check("AppKit 可用（这一节要真窗口）", False, f"{type(e).__name__}: {e}")
        return
    NSApplication.sharedApplication().setActivationPolicy_(
        NSApplicationActivationPolicyAccessory)

    def _walk(v, out):
        out.append(v)
        for c in (v.subviews() or []):
            _walk(c, out)
        return out

    import cloud_translator
    import keyentry
    import notice
    orig = (notice.ask_text, cloud_translator.load_api_key, keyentry.load_jev)
    caps: list = []
    notice.ask_text = lambda title, message, fields, **kw: (
        caps.append(fields), None)[1]
    cloud_translator.load_api_key = lambda _=None: "sk-SECRET-DS"
    keyentry.load_jev = lambda **kw: "apikey-jev-SECRET"
    # ⚠️ 2026-10-16：这一格的落点要么是**胶囊**（只有它一项不正常），要么是
    #    **胶囊菜单里那一行**（两项以上）—— `_ReadyAccess` 把两条路封成一个口子。
    _menus: list = []
    _orig_popup = E.popup_menu
    E.popup_menu = lambda menu, view: _menus.append(menu)
    _acc = _ReadyAccess(_menus)
    _rmod = None
    try:
        import ready as _rmod                              # ⚠️ 见下面 `_items` 的说明
        import tempfile as _tf
        with _tf.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            gl = root / "glossary.txt"
            gl.write_text("# public table\n", encoding="utf-8")
            # ⚠️ 2026-10-16：就绪项的落点有**两个** —— 只有它一项不正常时写在**胶囊**上
            #    （点它 = 直接修，比如「语音模型 · 下载」会真的开始下载）；两项以上时在**菜单**里。
            #    这里把麦克风 / 语音模型**钉成「要处理」**，保证永远是「两项以上」那一档：
            #    「翻译引擎」那一行稳定地在菜单里，而且判据点胶囊时**永远碰不到**模型那条真下载的路。
            _orig_items = _rmod.items

            def _items(**kw):
                return [{**i, "state": "warn"} if i.get("key") == "mic"
                        else ({**i, "state": "todo", "detail": "还差 1 件"}
                              if i.get("key") == "models" else i)
                        for i in _orig_items(**kw)]

            _rmod.items = _items
            h = E.build(glossary=gl, sessions_dir=root / "s", state_root=root / "st",
                        on_start=lambda c: None)
            try:
                # 正常时那格叫「云端翻译 / 本地翻译」，没配时叫「翻译引擎」（2026-10-14 瘦身）
                _lbl = _acc.label(h, "翻译") if h is not None else None
                check("（前置）面板上找得到「翻译引擎」这一格（胶囊 / 胶囊菜单里那一行）",
                      _lbl is not None, repr(_lbl))
                if _lbl is not None:
                    _acc.click(h, "翻译")
                    ok = (len(caps) == 1 and isinstance(caps[0], list)
                          and len(caps[0]) == 2)
                    check("⭐ 点一下真的开了填 key 框（`ask_text` 被调到）",
                          ok, str(caps)[:200])
                    if ok:
                        by = {f["key"]: f for f in caps[0]}
                        check("⭐⭐ 两栏都**回填**了已存的 key（重开不空白）",
                              by["deepseek"].get("value") == "sk-SECRET-DS"
                              and by["jev"].get("value") == "apikey-jev-SECRET",
                              str({k: v.get("value") for k, v in by.items()}))
                        check("⭐ 已存那档的提示语就是「已存过」（钉字面量）",
                              [f.get("hint") for f in caps[0]] == ["已存过", "已存过"],
                              str([f.get("hint") for f in caps[0]]))
            finally:
                try:
                    h.close()
                except Exception:                              # noqa: BLE001
                    pass
    finally:
        (notice.ask_text, cloud_translator.load_api_key, keyentry.load_jev) = orig
        E.popup_menu = _orig_popup
        if _rmod is not None:
            _rmod.items = _orig_items


def _digit_hint_section() -> None:
    """⑧c 数字键的**可见提示**真的画在卡上（小号、低对比）。

    ⚠️ 为什么要建窗口：提示是 AppKit 视图，纯函数那边看不到。判据挑**字号 10** 的
       标签 —— 面板上其余文字是 11/12/13，只有这个数字提示是 10，所以不会把
       「1 份课件」里的 `1` 误认成卡号。
    """
    print("\n--- ⑧c 数字键提示画在卡上（1–5，小号）---")
    import tempfile

    try:
        from AppKit import (NSApplication, NSApplicationActivationPolicyAccessory,
                            NSTextField)
    except Exception as e:                                    # noqa: BLE001
        check("AppKit 可用（这一节要真窗口）", False, f"{type(e).__name__}: {e}")
        return

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    def walk(v, out=None):
        out = [] if out is None else out
        out.append(v)
        for c in (v.subviews() or []):
            walk(c, out)
        return out

    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        gl = root / "glossary.txt"
        gl.write_text("# public table\n", encoding="utf-8")
        (root / "glossary").mkdir()
        for c in ("AA10010", "BB20020"):
            (root / "glossary" / f"{c}.txt").write_text(f"# {c}\n", encoding="utf-8")
        h = E.build(glossary=gl, sessions_dir=root / "sessions",
                    state_root=root / "state", on_start=lambda c: None)
        check("面板建起来了", h is not None)
        if h is None:
            return
        try:
            labels = [v for v in walk(h.window.contentView(), [])
                      if isinstance(v, NSTextField)
                      and float(v.font().pointSize()) == 10.0]
            got = sorted(v.stringValue() for v in labels)
            check("⭐ 两张卡分别有 1 / 2（小号 10pt）", got == ["1", "2"], str(got))
            check("⭐ 而且它们不是标题/准备度那种 11–13pt 的字",
                  all(float(v.font().pointSize()) < 11.0 for v in labels))
        finally:
            h.close()


def _result_actions_section() -> None:
    """⑦c 「导出清单」/「展开」的**接线**真的通（点得到底，不是函数被调到）。

    ⚠️ 与 ⑭ 同一条理由：接线坏掉时 AppKit 吞异常、纯函数全绿，症状统一是
       「点了没反应」。这里真建一张卡、真 `performClick_()`。
    """
    print("\n--- ⑦c 导出清单 / 展开：接线真的通 ---")
    try:
        from AppKit import (NSApplication, NSApplicationActivationPolicyAccessory,
                            NSButton, NSTextField)
    except Exception as e:                                    # noqa: BLE001
        check("AppKit 可用（这一节要真卡片）", False, f"{type(e).__name__}: {e}")
        return

    NSApplication.sharedApplication().setActivationPolicy_(
        NSApplicationActivationPolicyAccessory)

    def buttons(card):
        return [v for v in card.subviews() if isinstance(v, NSButton)]

    def texts(card):
        return [v.stringValue() for v in card.subviews() if isinstance(v, NSTextField)]

    ent = {"added": ["keep"], "removed": set(), "not_added": ["x", "y"],
           "skipped_existing": ["old1", "old2"], "failed": []}
    calls: list = []

    def _card(show):
        return E._make_card(R(), on_start=None, on_drop_files=lambda c, p: False,
                            width=640.0, entry=ent,
                            on_export=lambda: calls.append("export"),
                            show_skipped=show,
                            on_toggle_skipped=lambda: calls.append("toggle"))

    c0 = _card(False)
    titles = sorted(b.title() for b in buttons(c0))
    check("⭐ 收起态三颗按钮都在：删 + 导出清单 + 展开",
          titles == ["删", "导出清单", "展开"], str(titles))
    check("⭐ 收起时那组词**不在**文字里（默认收起）",
          "old1" not in texts(c0) and "old2" not in texts(c0), str(texts(c0)))
    check("⭐ 收起态有入口那一行（计数）",
          any("已在表里 / 你删过 2 个" in t for t in texts(c0)), str(texts(c0)))

    _b = next(b for b in buttons(c0) if b.title() == "导出清单")
    _b.performClick_(None)
    check("⭐⭐ 点「导出清单」-> 回调真的跑了（不是空按钮）",
          calls == ["export"], str(calls))
    _b2 = next(b for b in buttons(c0) if b.title() == "展开")
    _b2.performClick_(None)
    check("⭐⭐ 点「展开」-> 回调真的跑了", calls == ["export", "toggle"], str(calls))

    c1 = _card(True)
    check("⭐ 展开后逐条列出来",
          "old1" in texts(c1) and "old2" in texts(c1), str(texts(c1)))
    check("⭐ 展开后按钮变「收起」",
          sorted(b.title() for b in buttons(c1)) == ["删", "导出清单", "收起"],
          str(sorted(b.title() for b in buttons(c1))))
    # 高度与行循环不许脱钩（同 ⑥ 那条：少算会从底部掉出去，看顶端看不出来）
    for _tag, _c, _sh in (("收起", c0, False), ("展开", c1, True)):
        _ch = float(_c.frame().size.height)
        check(f"⭐ {_tag}态：卡片高度 == card_height(entry, show_skipped={_sh})",
              abs(_ch - E.card_height(ent, show_skipped=_sh)) < 0.01,
              f"{_ch} vs {E.card_height(ent, show_skipped=_sh)}")
        _low = min(float(v.frame().origin.y) for v in _c.subviews())
        check(f"⭐ {_tag}态：没有子视图掉出卡片底部", _low >= -0.01, f"最低 y={_low:.1f}")


def _enter_section() -> None:
    """⑧d 回车 = 「开始上课」：**驱动真的 `_on_key`**，与卡片按钮对拍。

    ⚠️ 为什么破例建窗口（同 ⑬）：这条钉的是**接线** —— 「回车 → `on_start`」和
       「点按钮 → `on_start`」必须是**同一条路、同一门课**。接线坏掉时 AppKit
       吞异常、纯函数判据全绿，症状统一是「按了没反应」。

    ⚠️ 不跑事件循环：`on_start` 是桩、同步的。
    ⚠️ `_win_hot` / `_win_is_editing` 在判据里换掉 —— 测试进程里窗口拿不到 key，
       而那两条不是本节要测的东西（守卫那节有自己的判据）。
    ⚠️ 写端全部隔离在 tempdir（glossary / sessions / state_root 都指过去）。
    """
    print("\n--- ⑧d 回车 = 「开始上课」：接线真的通（建窗口，不跑事件循环）---")
    import tempfile

    try:
        from AppKit import (NSApplication, NSApplicationActivationPolicyAccessory,
                            NSButton)
    except Exception as e:                                    # noqa: BLE001
        check("AppKit 可用（这一节要真窗口）", False, f"{type(e).__name__}: {e}")
        return

    class _Ev:
        def __init__(self, ch):
            self._ch = ch

        def charactersIgnoringModifiers(self):
            return self._ch

        def modifierFlags(self):
            return 0

    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        gl = root / "glossary.txt"
        gl.write_text("# public table\n", encoding="utf-8")
        (root / "glossary").mkdir()
        for c in ("AA10010", "BB20020"):
            (root / "glossary" / f"{c}.txt").write_text(f"# {c}\n", encoding="utf-8")
        ses = root / "sessions"
        ses.mkdir()
        # 「上次那门」= 最近一次真上过课的 —— 没有课表时预选就回落到它
        (ses / "2026-10-01_090000_BB20020.md").write_text("# 一节课\n", encoding="utf-8")
        NSApplication.sharedApplication().setActivationPolicy_(
            NSApplicationActivationPolicyAccessory)
        started: list = []
        h = E.build(glossary=gl, sessions_dir=ses, state_root=root / "state",
                    on_start=started.append)
        check("面板建起来了", h is not None)
        if h is None:
            return
        _hot, _edit = E._win_hot, E._win_is_editing
        E._win_hot, E._win_is_editing = (lambda w: True), (lambda w: False)
        try:
            handler = E.S.get("key_handler")
            check("⚠️ 前置：拿到真的 `_on_key`（拿不到下面全是空转）",
                  callable(handler), str(handler)[:70])
            _lk = E.S.get("likely")
            check("⭐ 预选 = 上次那门（没有课表，回落到 last_used）",
                  bool(_lk) and _lk.course == "BB20020", str(_lk))
            if not callable(handler):
                return
            handler(_Ev("\r"))
            check("⭐⭐ 回车 -> `on_start` 被调到、课号 = 选中的那门",
                  started == ["BB20020"], str(started))

            # 对拍：卡片上那颗「开始上课」——**同一个 `on_start`、同一门课**
            def walk(v, out=None):
                out = [] if out is None else out
                out.append(v)
                for c in (v.subviews() or []):
                    walk(c, out)
                return out

            btns = [v for v in walk(h.window.contentView())
                    if isinstance(v, NSButton) and v.title() == "开始上课"]
            check("⚠️ 前置：卡片上恰好一颗「开始上课」（否则下一条空转）",
                  len(btns) == 1, f"{len(btns)} 颗")
            if btns:
                btns[0].performClick_(None)
            check("⭐⭐ 点按钮 -> 同一个 `on_start`、同一门课（与回车逐字对上）",
                  started == ["BB20020", "BB20020"], str(started))

            # 正在编辑（任何 field editor）时，回车与数字键都不许被抢
            E._win_is_editing = (lambda w: True)
            started.clear()
            handler(_Ev("\r"))
            handler(_Ev("1"))
            check("⭐⭐ 输入框有焦点时回车**不抢**（在输入行里回车 = 建课）",
                  started == [], str(started))
            check("⚠️ 编辑中数字键也不抢（搜索框里打 `1` 只是打一个 `1`）",
                  E.S.get("picked") is None, str(E.S.get("picked")))

            # 没有可预选的那一门 -> 回车什么都不做（不挑第一张）
            E._win_is_editing = (lambda w: False)
            started.clear()
            _kept = E.S.pop("likely", None)
            handler(_Ev("\r"))
            check("⭐ 课表/历史都没有 -> 回车什么都不做",
                  started == [], str(started))
            if _kept is not None:
                E.S["likely"] = _kept
        finally:
            E._win_hot, E._win_is_editing = _hot, _edit
            try:
                h.close()
            except Exception:                                 # noqa: BLE001
                pass


def _add_wiring_section() -> None:
    """⑬ 新增课程：**接线**真的通（要建窗口，本文件其余部分不建）。

    ⚠️ 为什么值得破例建窗口：这一节要钉的正是**接线**，而接线的坏法全都不出声 ——
       按钮 target 被 GC 掉、`glossary_of` 指到别的文件、`submit_add` 没接上，
       三种都**不报错**、纯函数判据**全绿**，症状统一是「点了没反应」。
       本仓库栽过同形状的（`_make_card` 传错参数 → 点「删」静默无效）。

    ⚠️ 只建窗口、**不跑事件循环** —— `submit_add`/`create` 全是同步的，
       只有 `_later(refresh)` 排的重画不会执行，而这一节不断言重画。

    ⚠️ 写端全部隔离在 tempdir（`glossary=` 与 `state_root=` 都指过去）——
       同「测试必须隔离写端」那条硬规矩。
    """
    print("\n--- ⑬ 新增课程：接线真的通（建窗口，不跑事件循环）---")
    import tempfile
    import threading
    import time

    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    try:
        from AppKit import (NSApplication,
                            NSApplicationActivationPolicyAccessory)
    except Exception as e:                                    # noqa: BLE001
        check("AppKit 可用（这一节要真窗口）", False, f"{type(e).__name__}: {e}")
        return

    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        gl = root / "glossary.txt"
        gl.write_text("# public table\n", encoding="utf-8")
        app = NSApplication.sharedApplication()
        app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

        TRASHED: list = []

        def _fake_trash(p):
            # ⚠️⚠️ **绝不碰真的 `~/.Trash`** —— 测试往用户废纸篓里扔东西
            #    就是「测试必须隔离写端」那条硬规矩的违反。真搬走（搬进 tempdir
            #    的桶里），因为判据要看的是「东西真的不在了」。
            import shutil as _sh
            pp = pathlib.Path(p)
            TRASHED.append(str(pp))
            if pp.exists():
                dest = root / "_trashbox" / pp.name
                dest.parent.mkdir(parents=True, exist_ok=True)
                _sh.move(str(pp), str(dest))
            return True, ""

        ASKED: list = []
        SUGGESTED = threading.Event()      # 桩被调过就置位（等它，不 sleep 猜）

        def _fake_suggest(paths, **kw):
            # ⭐ **A 的核心可观测量**：零课程时这个函数**一次都不该被调用**。
            ASKED.append(list(paths))
            SUGGESTED.set()
            import classify as _cl
            return [_cl.Verdict(str(p), None, "桩：认不出", "none") for p in paths]

        h = E.build(glossary=gl, sessions_dir=root / "sessions",
                    state_root=root / "state", on_start=lambda c: None,
                    trash_fn=_fake_trash, suggest_fn=_fake_suggest)
        check("面板建起来了", h is not None)
        if h is None:
            return
        try:
            # ── ⭐ A：零课程时**一次模型都不调** ────────────────────────
            # 动机：候选列表是空的 → 模型按 `classify.SYS` 只能答"都不属于"，
            # 跑 N 次纯粹烧钱，用户拿到的还是 N 行「未分类」。
            pdfs = []
            for k in (1, 2, 3):
                f = root / f"讲义{k}.pdf"
                f.write_bytes(b"%PDF-1.4\n")
                pdfs.append(str(f))
            # 第 3 份的名字里带课号 —— 建完课它该**免费**被认出来
            named = root / "ECON10999_L1.pdf"
            named.write_bytes(b"%PDF-1.4\n")
            pdfs.append(str(named))

            h.start_batch(pdfs)
            # ⚠️⚠️ **判据必须同步、确定** —— 第一版我断言的是 `ASKED == []`，
            #    而它是**假的**：`suggest` 在工作线程里跑，测试是同步的，
            #    断言时它还没轮到 → **把短路整段删掉，这条照样绿**
            #    （变异验证当场抓出来的）。「因为来不及所以没调」不是「没调」。
            # ⭐ 真正确定的观测是**状态**：短路生效时 `need_course` 为真、`busy` 为假；
            #    一旦走进分类那条路，`busy` 立刻为真、`need_course` 根本不存在。
            _b0 = E.S.get("batch") or {}
            check("⭐⭐ 零课程：**没走进分类那条路**（need_course 为真、busy 为假）",
                  _b0.get("need_course") is True and _b0.get("busy") is False,
                  f"need_course={_b0.get('need_course')} busy={_b0.get('busy')}")
            check("⭐ 计数器此刻**还没被调**（辅助观测：它只证明'还没来得及'，不是判据）",
                  ASKED == [], f"被调了 {len(ASKED)} 次")
            check("⭐ 而且进了「等用户建课」那一档（不是静默失败）",
                  bool(E.S["batch"] and E.S["batch"].get("need_course")),
                  str({k: v for k, v in (E.S.get("batch") or {}).items()
                       if k != "pending"}))
            check("⭐ 拖进来的文件没丢（全在 pending 里等着）",
                  len(E.S["batch"].get("pending") or []) == len(pdfs),
                  str(len(E.S["batch"].get("pending") or [])))

            h.add_course("  ECON10999  ")          # 带空格，验证会 strip
            p = root / "glossary" / "ECON10999.txt"
            check("⭐ 程序化建课真的落盘（走的就是手输那条路）",
                  p.exists() and p.read_text(encoding="utf-8") == "# ECON10999\n",
                  str(p))
            if E.S["batch"].get("need_course"):
                # ⭐ 零课程那一档：**留在输入行里**让用户接着建（故意不收起）
                check("⭐ 建完课：名字里带课号的那份**免费**被认出来了",
                      E.S["batch"].get("matched", {}).get(str(named)) == "ECON10999",
                      str(E.S["batch"].get("matched")))
                check("⭐⭐ 到这一步**仍然一次 suggest 都没调**（免费那步是纯字符串匹配）",
                      ASKED == [], f"被调了 {len(ASKED)} 次")

                # ── ⭐⭐ 「开始分类」之后：免费认出来的那份**不许被覆盖** ──────
                # 动机（2026-09-28 OCR 审计）：`_classify` 里明明预读了已有的 verdict
                # （注释还写着"覆盖会把它们静默丢掉"），下一行却整句被 `suggest()` 覆盖 ——
                # 而 `suggest` 只为传进去的那些出结果，于是**免费那几份从结果列表上消失**。
                # ⚠️ 这是**数据丢失**类，不是显示问题：用户刚看着它们被认出来。
                h.start_classify()
                check("⭐ 桩 suggest 真的被调到了（走进了分类那条路）",
                      SUGGESTED.wait(5.0), "5 秒内没被调")
                # ⚠️ 等工作线程收尾用**有界轮询**（不是 sleep 猜时间）——
                #    判据是**状态**（busy 翻假），不是"等了多久"。
                _t0 = time.monotonic()
                while (E.S.get("batch") or {}).get("busy") and time.monotonic() - _t0 < 5.0:
                    time.sleep(0.01)
                _vs = {str(v.path).split("/")[-1]: v.course
                       for v in ((E.S.get("batch") or {}).get("verdicts") or [])}
                check("⭐⭐ 免费认出来的那份**仍在** verdicts 里（没被 suggest 的结果覆盖）",
                      _vs.get(named.name) == "ECON10999", str(_vs))
                check("⭐ 而且模型那批也在（是**合并**，不是二选一）",
                      _vs.get("讲义1.pdf") is None and len(_vs) == len(pdfs),
                      f"verdicts 里 {len(_vs)} 条，拖进来 {len(pdfs)} 份：{_vs}")
                # 收尾：把这一档收掉，别影响后面的判据
                E.S["batch"] = None
                E.S["add"] = None
                E.S.pop("_focus_add", None)

            # ── ⭐⭐ 「取消」那一格：`mine is None` 不许崩 ────────────────
            # 动机（2026-09-30，pyright 抓出来的）：`work()` 里那句守卫写的是
            #   `if S.get("batch") is not mine: return`
            # 而用户在 `_classify` 与线程**真正开跑**之间点了「取消」时：
            #   `S["batch"] = None` → `mine` 也是 `None`
            #   → `None is not None` 判成 **False**（"没换过"）→ 不返回
            #   → 落到 `mine["verdicts"] = ...` → **TypeError**（daemon 线程里，没人接）
            # 症状：点了取消之后面板偶尔不刷新 —— 极难查。
            #
            # ⚠️⚠️ **必须确定性构造，不能用 sleep 赌窗口**（本文件被抓过两次：
            #    「因为来不及所以没调」不是「没调」）。办法：**把线程 target 抓下来**，
            #    手工决定什么时候跑它 —— 于是"取消发生在线程开跑之前"就成了必然。
            # ⚠️ `_start_classify` 要求批次处于「等着分类」那一档
            #    （`need_course` 为真 + 有 `pending`），而上一段刚把 batch 清成 None
            #    → 不补状态的话它直接早退，**一个线程都不会起**，
            #    于是下面的前置变红（第一版就是这么写的）。
            E.S["batch"] = {"verdicts": [], "busy": False, "total": len(pdfs),
                            "dropped": 0, "error": "", "need_course": True,
                            "pending": list(pdfs), "matched": {}}
            _real_thread = E.threading.Thread
            _captured: list = []

            class _NoStartThread:
                def __init__(self, target=None, daemon=None, **kw):
                    _captured.append(target)

                def start(self):
                    pass                              # 抓下来，不真跑

            try:
                E.threading.Thread = _NoStartThread
                h.start_classify()
            finally:
                E.threading.Thread = _real_thread
            check("⚠️ 前置：确实抓到了那个 worker（否则下面两条是空转）",
                  len(_captured) == 1 and callable(_captured[0]),
                  f"抓到 {len(_captured)} 个")
            # 现在**必定**模拟出那个窗口：批次在 worker 跑之前就没了
            E.S["batch"] = None
            # ⚠️⚠️ **观测量选「那段最贵的 I/O」，不是 `suggest`**（第一版写的是
            #    `len(ASKED) 不变`，**变异验证当场证明它是假绿**）：
            #    删掉早退之后，代码在 `S["batch"]["weak"] = …` 那一行就 `TypeError`
            #    掉进 `except` 了，**根本走不到 `suggest`** → 两边都"没调 suggest"。
            #    而 `corpus.keywords()`（把每门课的转录全读一遍）在它**之前**，
            #    正好是早退唯一能挡住的、也是最贵的那一步。
            import corpus as _corpus_mod
            _kw_calls: list = []
            _real_kw = _corpus_mod.keywords

            def _spy_kw(*a, **kw):
                _kw_calls.append(a)
                return _real_kw(*a, **kw)

            _corpus_mod.keywords = _spy_kw
            try:
                _captured[0]()                        # 同步跑 worker
                _boom = ""
            except Exception as e:                    # noqa: BLE001
                _boom = f"{type(e).__name__}: {str(e)[:80]}"
            finally:
                _corpus_mod.keywords = _real_kw
            check("⭐⭐ 批次已被取消 -> worker **不炸**（原来 TypeError）", not _boom, _boom)
            check("⭐⭐ 而且**早退**了 —— 那段最贵的 I/O（读全部转录）一次都没跑",
                  _kw_calls == [], f"`corpus.keywords` 被调了 {len(_kw_calls)} 次")
            check("⭐ 而且没把 `batch` 又变回一个 dict（取消就是取消）",
                  E.S.get("batch") is None, str(E.S.get("batch"))[:60])

            # ── ⭐⭐ 「取消后又重拖」：A 批的「认不准」名单不许盖进 B 批 ──────
            # 动机（2026-10-01 审核指出，pre-existing）：A 的 worker 跑到写回那步时，
            # 原来写的是 `S["batch"]["weak"] = …` —— 而那个 `S["batch"]` 可能**已经是 B 批**
            # → A 的名单出现在 B 的卡片上。修法：写捕获的 `mine` + 写前查代际。
            # ⚠️ 确定性构造：**换批这个动作放在 `corpus.keywords` 里**（它在 `mine`
            #    捕获之后、写回之前必然被调）—— 不用 sleep 赌窗口。
            _A = {"verdicts": [], "busy": False, "total": len(pdfs),
                  "dropped": 0, "error": "", "need_course": True,
                  "pending": list(pdfs), "matched": {}}
            _B = {"verdicts": [], "busy": True, "total": 1,
                  "dropped": 0, "error": "", "need_course": False,
                  "pending": [], "matched": {}}
            E.S["batch"] = _A
            _captured2: list = []

            class _NoStartThread2:
                def __init__(self, target=None, daemon=None, **kw):
                    _captured2.append(target)

                def start(self):
                    pass                              # 抓下来，不真跑

            try:
                E.threading.Thread = _NoStartThread2
                h.start_classify()
            finally:
                E.threading.Thread = _real_thread
            check("⚠️ 前置：抓到第二个 worker（否则下面三条是空转）",
                  len(_captured2) == 1 and callable(_captured2[0]),
                  f"抓到 {len(_captured2)} 个")
            _real_kw2 = _corpus_mod.keywords

            def _swap_kw(*a, **kw):
                E.S["batch"] = _B            # ← 换批就发生在这里（worker 已捕获 mine=_A）
                return ({}, {"X课": "没有语料"})

            _corpus_mod.keywords = _swap_kw
            try:
                _captured2[0]()
                _boom2 = ""
            except Exception as e:                    # noqa: BLE001
                _boom2 = f"{type(e).__name__}: {str(e)[:80]}"
            finally:
                _corpus_mod.keywords = _real_kw2
            check("⭐⭐ 换批窗口里 worker 不炸", not _boom2, _boom2)
            check("⭐⭐ A 批的「认不准」名单**不许盖进 B 批**（写的是捕获的 `mine`）",
                  _B.get("weak") is None and _B.get("has_key") is None,
                  f"B.weak={_B.get('weak')!r} B.has_key={_B.get('has_key')!r}")
            check("⭐ 而且那份名单**写进了 A 自己**（没白丢、也没写错人）",
                  _A.get("weak") == {"X课": "没有语料"}, repr(_A.get("weak")))
            E.S["batch"] = None

            check("建完输入行收起来了", E.S.get("add") is None)

            h.add_course("ECON10999")              # 第二次 = 已经有了
            check("⭐ 第二次**不覆盖**别人的文件",
                  p.read_text(encoding="utf-8") == "# ECON10999\n",
                  p.read_text(encoding="utf-8"))
            check("「已经有了」时输入行**留着**让他就地改",
                  E.S.get("add") is not None)

            h.add_course("BAD/CODE")               # 当不了文件名
            check("⭐ 非法课号一个文件都不建",
                  not (root / "glossary" / "BAD").exists()
                  and list((root / "glossary").iterdir()) == [p],
                  str(list((root / "glossary").iterdir())))
            check("输入行也留着", E.S.get("add") is not None)

            # ── ⭐⭐ 右键菜单的 target **必须还活着** ─────────────────────
            # 2026-09-28 真机上「菜单变灰、点不动」换来的：`setTarget_` 是**弱引用**，
            # 写成 `setTarget_(_target(...))` 的话那个临时对象**语句一结束就被回收**，
            # 菜单项于是没有 target → AppKit 的自动启用逻辑沿响应链找不到能响应
            # `act:` 的 → **禁用**（灰）。⚠️ 当时**所有单测全绿** —— 它们测的是函数，
            # 不是接线。这条判据就是补那个洞：直接问菜单项"你的 target 呢"。
            h.refresh()                        # 不用事件循环，直接把卡片画出来
            _menus: list = []

            def _walk_menu(v):
                if v.menu() is not None:
                    _menus.append(v.menu())
                for _c in (v.subviews() or []):
                    _walk_menu(_c)

            _walk_menu(h.window.contentView())
            # ⚠️ **判据要指向那个位置**：`v.menu()` 会把 **AppKit 给文本框自带的那套**
            #    （Cut/Copy/Paste）也捞出来 —— 那种菜单 target 本来就该是 `None`
            #    （它走响应链），拿它当判据会**假红**（第一版就红在 `['Cut']` 上）。
            #    → 只看**我们自己那个**：一条、「删除课程…」。
            _del = [m for m in _menus if m.numberOfItems() == 1
                    and m.itemAtIndex_(0).title() == "删除课程…"]
            check("⭐ 卡片上挂着我们的「删除课程…」菜单", bool(_del),
                  f"共 {len(_menus)} 个菜单 / 命中 {len(_del)}")
            _noT = [m.itemAtIndex_(0).title() for m in _del
                    if m.itemAtIndex_(0).target() is None]
            check("⭐⭐ 菜单项的 target **不是 None**（是 None 就变灰点不动）",
                  bool(_del) and not _noT, str(_noT))
            # ⚠️ 必须**先 `update()`** —— 那是 AppKit 显示菜单前自己跑的那一步，
            #    而 `isEnabled()` 在菜单没更新过时**恒报 True**。第一版漏了它，
            #    于是这条判据在"target 是 None"的坏版本下**照样绿**（假判据）。
            for _m in _del:
                _m.update()
            _dis = [m.itemAtIndex_(0).title() for m in _del
                    if not m.itemAtIndex_(0).isEnabled()]
            check("⭐ 菜单项是**启用**的（没有 target 的项会被 AppKit 禁用成灰）",
                  bool(_del) and not _dis, str(_dis))

            # ── 删除课程那条路（假废纸篓，不碰真的 ~/.Trash）──────────
            ses = root / "sessions"
            ses.mkdir(exist_ok=True)
            rec = ses / "2026-09-01_090000_10999.md"
            rec.write_text("# 一节课\n", encoding="utf-8")
            mats = root / "state" / "courses" / "ECON10999" / "materials"
            mats.mkdir(parents=True, exist_ok=True)
            (mats / "讲义.pdf").write_bytes(b"x")

            h.delete_course("ECON10999", keep=True)      # = 菜单里的「只删课号」
            check("⭐ 术语表进了废纸篓（走的是 `courses.delete` 同一条路）",
                  str(root / "glossary" / "ECON10999.txt") in TRASHED, str(TRASHED))
            check("⭐ 课件搬进保留区，原件还在",
                  (root / "state" / "courses" / ".removed" / "ECON10999"
                   / "materials" / "讲义.pdf").exists())
            check("⭐⭐ 上课记录一个字节都没动",
                  rec.read_text(encoding="utf-8") == "# 一节课\n")
            check("⭐ 删完面板上就没这张卡了",
                  "ECON10999" not in E.courses.list_courses(
                      gl, state_root=root / "state"))

            h.close()                              # 收起那一行
            check("关掉面板后不残留新增模式（跨面板的模块级状态）",
                  E.S.get("add") is None)
        finally:
            try:
                h.close()
            except Exception:                                 # noqa: BLE001
                pass


if __name__ == "__main__":
    sys.exit(main())
