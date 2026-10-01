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
    # ⚠️ 这一组的判据是**三条退出码必须互不相同**：「用户取消」和「面板挂了」
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
        check("⭐ ② 用户关掉面板 -> 1，且 **.course 一个字没动**（取消 ≠ 故障）",
              rc == entry_launch.CANCELLED
              and cfg.read_text(encoding="utf-8") == "KEEPME",
              f"rc={rc} course={cfg.read_text(encoding='utf-8')!r}")

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

        check("⭐ 四条码互不相同（`cl` 的 case 分支靠这个分清）",
              len({entry_launch.PICKED, entry_launch.CANCELLED,
                   entry_launch.UNAVAILABLE, entry_launch.NO_COURSES,
                   entry_launch.UNSAVED}) == 5)
    finally:
        _EP.open_panel = _orig_open
        if saved is not None:
            cfg.write_bytes(saved)
        elif cfg.exists():
            cfg.unlink()
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
    _target_liveness_section()
    _test_mode_section()
    _vault_section()
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

    print("\n--- ⑯ 课次列表（卡上「课次」按钮进的）---")
    # ⭐ `Handles` 是**位置构造**踩过坑的地方：2026-09-29 插 `show_sessions` 时参数
    #    错了一格 —— `open_sessions` 落进 `delete_course`、删课 lambda 落进
    #    `show_sessions` → **调 show_sessions 实际在删课**，一个错都不报。
    #    修法是把构造改成关键字；这条钉住**字段名与顺序**，将来再有人插错至少看得见。
    check("Handles 的字段名与顺序（改构造方式后仍要一致）",
          E.Handles._fields == ("window", "close", "refresh", "set_status",
                                "start_batch", "search", "add_course",
                                "show_sessions", "import_timetable",
                                "delete_course", "start_classify"),
          f"实际 {E.Handles._fields}")

    import pathlib as _pl
    from tempfile import TemporaryDirectory as _TD
    with _TD() as _d:
        _p = _pl.Path(_d) / "2026-09-24_202216_LECTURE.md"
        _r = E.session_row(_p, 44)
        check("session_row 从文件名取日期与时间",
              (_r["date"], _r["hhmm"], _r["stem"]) ==
              ("2026-09-24", "20:22", "2026-09-24_202216_LECTURE"), _r)
        check("⭐ 字数够 → ok（判据用的是 corpus.MIN_WORDS，不是抄来的数）",
              _r["state"] == "ok"
              and E.session_row(_p, E.corpus.MIN_WORDS - 1)["state"] == "thin",
              f"MIN_WORDS={E.corpus.MIN_WORDS}")
    # ⚠️ 空壳必须**说出来** —— 它们在 sessions/ 里跟真课长得一模一样，
    #    而 corpus 那道闸是**静默**排除它们的。
    check("空壳在行里明说", "空壳" in E.session_row_text("2026-09-26", "05:15", 2, "thin"))
    check("够格的**不许**被标成空壳",
          "空壳" not in E.session_row_text("2026-09-22", "15:02", 44, "ok"))

    # ⭐⭐ 高度与排版同源：**空组不留高，有组才留**（同 search_card_height 那条纪律）
    #     变异验证：把 `sessions_card_height` 的 `if n_orphans:` 去掉 → 第一条红。
    _base = E.sessions_card_height(6)
    check("高度：没有孤儿组时**一点都不多留**",
          abs(E.sessions_card_height(6, 0) - _base) < 0.01)
    check("高度：有 k 个孤儿就正好多 SEP + HEAD + k*ROW",
          abs((E.sessions_card_height(6, 3) - _base)
              - (E.SESS_SEP + E.SESS_HEAD + 3 * E.SESS_ROW)) < 0.01)

    # ⭐⭐ 真的画一遍：**没有子视图掉出卡片底部**（高度算少的唯一症状，且**不报错**）
    from find import Hit as _H  # noqa: F401   （只为确认这个模块能 import）
    _rows = [E.session_row(_pl.Path(f"2026-09-{d:02d}_150000_ECON10740.md"), 44)
             for d in (8, 15, 22)]
    _orph = [E.session_row(_pl.Path("2026-09-24_202216_LECTURE.md"), 44)]
    for _n in (0, 1):
        _card, _tg = E._make_sessions_card(
            _rows, width=600.0, title="这门课 3 节", on_back=lambda: None,
            on_adopt=(lambda s: None) if _n else None,
            orphans=(_orph if _n else []), targets=[])
        _low = min(float(v.frame().origin.y) for v in _card.subviews())
        check(f"课次卡（孤儿组 {'有' if _n else '无'}）没有子视图掉出底部",
              _low >= -0.01, f"最低 y={_low:.1f}")
        # ⚠️ 弱引用：没有孤儿时只有「返回」一个 target
        check(f"课次卡（孤儿组 {'有' if _n else '无'}）targets 交回来了",
              len(_tg) == (2 if _n else 1), f"拿到 {len(_tg)} 个")

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

    check("⭐ on_start=None 时，「预选」一个像素都不许加（否则叠在就绪行上）",
          _sig(_mk(hinted=True, on_not_this=lambda: None)) == _sig(_mk()),
          f"预选版 {len(_mk(hinted=True, on_not_this=lambda: None).subviews())} 个子视图，"
          f"普通版 {len(_mk().subviews())} 个")

    # 反方向：**有按钮行时**，「开始上课」与「不是这门？」两个都必须在
    # （否则上面那条会因为"什么都不画"而假绿）
    def _mk_act(**kw):
        return E._make_card(_r0, on_start=lambda c: None,
                            on_drop_files=lambda *a: None, width=600.0, **kw)

    _btns = [v for v in _mk_act(hinted=True, on_not_this=lambda: None).subviews()
             if float(v.frame().origin.y) < E.CARD_PAD + E.BTN_H - 0.01]
    check("有按钮行时：预选那张卡上「开始上课」与「不是这门？」都在",
          len(_btns) == 2, f"按钮行里拿到 {len(_btns)} 个")
    _btns2 = [v for v in _mk_act().subviews()
              if float(v.frame().origin.y) < E.CARD_PAD + E.BTN_H - 0.01]
    check("有按钮行时：**没预选**的卡只有一个按钮（不许到处挂「不是这门？」）",
          len(_btns2) == 1, f"拿到 {len(_btns2)} 个")

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

    bad = [n for n, ok, _ in RESULTS if not ok]
    print(f"{len(RESULTS) - len(bad)}/{len(RESULTS)} 通过")
    for n in bad:
        print(f"  ❌ {n}")
    return 1 if bad else 0


def _test_mode_section() -> None:
    """⑳ 测试模式开关：**宽度 / 文案 / 点击真的触发回调 / 那个开关文件**。

    ⚠️ 为什么要建窗口：这一节钉的全是**接线**（开关在不在、点了调没调到、
       标题变没变、失败弹没弹回去），而接线的坏法**一律不出声** ——
       纯函数判据全绿，症状统一是「点了没反应」或**界面说的和盘上的不是一回事**。

    ⚠️ 只建窗口、**不跑事件循环** —— `performClick_` 是同步的（`sendAction:to:`）。

    ⚠️ 写端全部隔离在 tempdir：面板走 `glossary=` / `state_root=`，
       开关文件走 `read_test_mode(path=…)` / `write_test_mode(…, path=…)`。
       ⚠️ **绝不碰仓库根目录那份真 `.test-mode`**（「测试必须隔离写端」那条硬规矩）。

    ⚠️ 最后一组**真跑 `cl`**（沙盒 + 假解释器），不算越界：`cl` 那半是 shell，
       没有别的判据盖得到，而它正是「开关按了到底生效没有」的**最后一厘米**。
    """
    print("\n--- ⑳ 测试模式开关：宽度 / 文案 / 点击真的触发回调 ---")
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

    def _rects_hit(a, b) -> bool:
        """两个 frame 有没有**真的重叠**（不相邻才算）。"""
        ax, ay, aw, ah = (float(a.origin.x), float(a.origin.y),
                          float(a.size.width), float(a.size.height))
        bx, by, bw, bh = (float(b.origin.x), float(b.origin.y),
                          float(b.size.width), float(b.size.height))
        return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah

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
        h = _build("1", on_test_mode=lambda on: (calls.append(on), True)[1],
                   test_mode=False)
        check("面板建起来了（⑳ 这一节要真窗口）", h is not None)
        if h is None:
            return
        try:
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

            strip = _strip_of(h)
            check("找得到落点条", strip is not None)
            if strip is None:
                return
            sw = [b for b in strip.subviews()
                  if isinstance(b, NSButton) and (b.title() or "") == OFF]
            check("⭐ 落点条上有测试模式开关，初值 =「测试模式：关」（`test_mode=False`）",
                  len(sw) == 1, f"命中 {len(sw)} 颗")
            if len(sw) != 1:
                return
            sw = sw[0]

            # ── ⭐⭐ 宽度扫描（照 `tests/test_panel.py` 那条）──────────────
            # ⚠️ 这条量的是**真几何**（两个 frame 会不会真重叠），不是照算式再算一遍 ——
            #    照着算式算的话，算式自己写错时两边一起错，判据照样绿。
            hit = []
            for v in strip.subviews():
                if v is sw or v.isHidden():
                    continue
                if _rects_hit(sw.frame(), v.frame()):
                    # ⚠️ `v.frame()` **不能直接迭代**（拿到的是 CGPoint/CGSize 那两个
                    #    结构，不是 4 个数字）—— 第一版这么写，在"真撞上"的那次
                    #    **先抛 TypeError、连汇总行都出不来**（detail 是急切求值的，
                    #    本仓记过这条；绿的时候看不出来）。
                    _f = v.frame()
                    hit.append((type(v).__name__,
                                (v.title() if isinstance(v, NSButton)
                                 else (v.stringValue() or ""))[:20],
                                [round(float(_f.origin.x), 1), round(float(_f.origin.y), 1),
                                 round(float(_f.size.width), 1),
                                 round(float(_f.size.height), 1)]))
            check("⭐⭐ 开关**不压到条上任何可见控件**（加它之后提示文字要缩掉一截）",
                  not hit, str(hit))

            # ⭐⭐ 提示文字塞得下 —— **反向的那一半**（上面那条只管"别压到别人"）
            # ⚠️ 这条守的是 `HINT_W` 那个式子的余量：文字超框 → `panel.make_label`
            #    默认就是换行 + **第二行被静默吃掉**（没有省略号，看着像句子就到这儿）。
            over, measured = [], 0
            for v in strip.subviews():
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
                  measured >= 3, f"量到 {measured} 行")

            # ── ⭐ 点击：**钉行为，不钉 flag** ─────────────────────────────
            # 📌 本仓 `ClickView` 就是 `mouseDownCanMoveWindow=True` 而完全正常 ——
            #    所以判据只看「点了之后发生了什么」。
            sw.performClick_(None)
            check("⭐ 点一下 → 文案变「测试模式：开」（**字面量**，不是再问一次函数）",
                  (sw.title() or "") == ON, repr(sw.title()))
            check("⭐⭐ 而且**回调真的被调到了**（收到 True）", calls == [True], str(calls))
            sw.performClick_(None)
            check("⭐ 再点一下 → 回「测试模式：关」，回调收到 False",
                  (sw.title() or "") == OFF and calls == [True, False],
                  f"{sw.title()!r} {calls}")
        finally:
            try:
                h.close()
            except Exception:                                 # noqa: BLE001
                pass

        # ── ⭐⭐ 落盘失败 → **弹回原状**（界面不许撒谎）────────────────────
        # ⚠️ 照 `.course` 那次事故（2026-09-29 的 `UNSAVED`）：写不下去时不改状态。
        #    这里更坏 —— 显示「开」而盘上是「关」= 用户以为在采集，其实什么都没记。
        calls2: list = []
        h2 = _build("2", on_test_mode=lambda on: (calls2.append(on), False)[1],
                    test_mode=False)
        try:
            sw2 = _toggles(h2) if h2 is not None else []
            check("（前置）第二块面板上也有开关", len(sw2) == 1, f"命中 {len(sw2)} 颗")
            if len(sw2) == 1:
                sw2[0].performClick_(None)
                check("⭐⭐ 写盘失败 → 文案**弹回「关」**（不许显示成开着）",
                      (sw2[0].title() or "") == "测试模式：关",
                      f"{sw2[0].title()!r}，回调={calls2}")
        finally:
            try:
                h2.close()
            except Exception:                                 # noqa: BLE001
                pass

        # ── ⚠️ 结构：没有落点（`on_test_mode=None`）时**一颗都不建** ──────
        # ⚠️ 这一档 = 上课中从菜单栏打开的面板（那时这节课早在录了），
        #    开关在那儿没有意义 —— 建了反而像"还能改"。
        h3 = _build("3")
        try:
            check("⭐ `on_test_mode=None` → 开关一颗都不建（上课中开的面板）",
                  h3 is not None and not _toggles(h3),
                  f"命中 {len(_toggles(h3)) if h3 is not None else '—'} 颗")
        finally:
            try:
                h3.close()
            except Exception:                                 # noqa: BLE001
                pass
        # 反方向：初值 True 时要显示「开」（否则上面那条会因为"永远不建"而假绿）
        h4 = _build("4", on_test_mode=lambda on: True, test_mode=True)
        try:
            sw4 = _toggles(h4) if h4 is not None else []
            check("⭐ `test_mode=True` → 初值显示「测试模式：开」",
                  len(sw4) == 1 and (sw4[0].title() or "") == "测试模式：开",
                  f"命中 {len(sw4)} 颗：{[b.title() for b in sw4]}")
        finally:
            try:
                h4.close()
            except Exception:                                 # noqa: BLE001
                pass

        # ── ⚠️ 两态的文字宽必须**一模一样**（切换时按钮不许抖）────────────
        from AppKit import NSFont as _NSFont
        _font = _NSFont.systemFontOfSize_(12.0)
        _won = _text_w(E.test_mode_title(True), _font)
        _woff = _text_w(E.test_mode_title(False), _font)
        check("⚠️ 两态**实测文字宽完全相同**（切换时文字不抖）",
              abs(_won - _woff) < 1e-9, f"开={_won:.1f} 关={_woff:.1f}")
        check("⭐ 而且塞得进按钮框（`TEST_W` 要留得下它）",
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
        check("⭐⭐ 开关是「1」→ `cl` 真的给 main.py 加了 `--test-mode`",
              "--test-mode" in _out, _out.strip().splitlines()[-1] if _out.strip() else "（空）")
        check("⭐ 而且 ▶ 那行报出来了（**不许静默**）",
              "测试模式=开" in _out, _out.strip().splitlines()[-2] if _out.strip() else "")
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
            check("⭐ 就绪条**真的画出来了**（不然上一条是空对空）",
                  any(isinstance(v, NSButton) and "perm" in str(v.title())
                      for v in views),
                  # ⚠️ 标题是按 `key` + `state` 渲染的（`ready_item_text`），
                  #    **不含**桩里那个 `text` —— 第一版按 `text` 找，基线就红了一条。
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
    check("已设 → `✓  笔记库  已设`",
          _t({"key": "vault", "state": "ok"}) == "✓  笔记库  已设",
          repr(_t({"key": "vault", "state": "ok"})))
    check("路径没了 → `⚠  笔记库  找不到了`",
          _t({"key": "vault", "state": "warn"}) == "⚠  笔记库  找不到了",
          repr(_t({"key": "vault", "state": "warn"})))

    try:
        import os
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

    def _vault_btn(h):
        for v in _walk(h.window.contentView(), []):
            if isinstance(v, NSButton) and "笔记库" in (v.title() or ""):
                return v
        return None

    orig_pick = E.pick_folder
    # ⚠️ `$OBSIDIAN_VAULT` **必须挪开**：本机 shell 里它指着真库 → 面板一开就是
    #    「已设」→「点了之后变已设」那条判据**恒真**（2026-10-01 变异验证抓到的）。
    #    挪开之后才是朋友那台机器的入场状态（未设 → 点了才变）。
    _saved_env = os.environ.pop("OBSIDIAN_VAULT", None)
    # ⚠️ 8-F 起**家目录也要挪**：自动探测会读**真的** Obsidian 注册表（本机就有
    #    SecondBrain）→ 把"未设入场"的前提搅掉，还会往沙盒里写进真库路径。
    _saved_home = os.environ.get("HOME")
    try:
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            _fake_home = root / "home"          # 空家目录 = 找不到任何库（8-F 的"0 个"档）
            _fake_home.mkdir()
            os.environ["HOME"] = str(_fake_home)
            gl = root / "glossary.txt"
            gl.write_text("# public table\n", encoding="utf-8")

            def _build(name, st):
                return E.build(glossary=gl, sessions_dir=root / name,
                               state_root=st, on_start=lambda c: None)

            # ── 点击：真的存进 `<state_root>/vault`，并且**就地**改字 ──────
            st = root / "st1"
            picked = root / "MyVault"
            picked.mkdir()
            (picked / ".obsidian").mkdir()                     # 像一个真库
            calls: list = []
            E.pick_folder = lambda **kw: (calls.append(kw), str(picked))[1]
            h = _build("s1", st)
            try:
                btn = _vault_btn(h) if h is not None else None
                check("（前置）面板上找得到「笔记库」那一格", btn is not None, "")
                if btn is not None:
                    check("（前置）这一格**一开始是「未设」**—— 环境变量挪开了才测得出「变」",
                          (btn.title() or "") == "—  笔记库  未设", repr(btn.title()))
                    btn.performClick_(None)
                    check("⭐ 点了真的弹了选文件夹（一次，且带着智能起点）",
                          len(calls) == 1 and bool(calls[0].get("start")), str(calls))
                    check("⭐⭐ 选完**真的写进了 `<state_root>/vault`**（写端隔离）",
                          (st / "vault").exists()
                          and (st / "vault").read_text(encoding="utf-8").strip()
                          == str(picked),
                          repr((st / "vault").read_text(encoding="utf-8")
                               if (st / "vault").exists() else None))
                    check("⭐ 而且那一格**就地**变成「已设」（不用重开面板）",
                          (btn.title() or "") == "✓  笔记库  已设", repr(btn.title()))
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
                b2 = _vault_btn(h2) if h2 is not None else None
                before = (b2.title() or "") if b2 is not None else None
                if b2 is not None:
                    b2.performClick_(None)
                    check("⭐ 取消选目录 → 标题不动、盘上什么都不写",
                          (b2.title() or "") == before
                          and not (st2 / "vault").exists(),
                          f"{b2.title()!r} {(st2 / 'vault').exists()}")
            finally:
                try:
                    h2.close()
                except Exception:                              # noqa: BLE001
                    pass

            # ── 8-F①：家目录里恰好 1 个库 → 面板一开就**自动设上** ──────────
            homeA = root / "homeA"
            vA = homeA / "Documents" / "MyVault"
            vA.mkdir(parents=True)
            (vA / ".obsidian").mkdir()
            os.environ["HOME"] = str(homeA)
            stA = root / "stA"
            hA = _build("A", stA)
            try:
                bA = _vault_btn(hA) if hA is not None else None
                check("⭐⭐ 8-F：只有 1 个库 → **自动设上**（写进 `<state_root>/vault`）",
                      (stA / "vault").exists()
                      and (stA / "vault").read_text(encoding="utf-8").strip() == str(vA),
                      repr((stA / "vault").read_text(encoding="utf-8")
                           if (stA / "vault").exists() else None))
                check("⭐ 而且那一格直接就是「已设」",
                      bA is not None and (bA.title() or "") == "✓  笔记库  已设",
                      repr(bA.title() if bA is not None else None))
            finally:
                os.environ["HOME"] = str(_fake_home)
                try:
                    hA.close()
                except Exception:                          # noqa: BLE001
                    pass

            # ── 8-F②：多个候选 → **绝不猜**（不设、不写；点开预指向其中之一）──
            homeB = root / "homeB"
            v1 = homeB / "Documents" / "Two"
            v1.mkdir(parents=True)
            (v1 / ".obsidian").mkdir()
            v2 = homeB / "Obsidian" / "One"
            v2.mkdir(parents=True)
            (v2 / ".obsidian").mkdir()
            os.environ["HOME"] = str(homeB)
            stB = root / "stB"
            hB = _build("B", stB)
            try:
                bB = _vault_btn(hB) if hB is not None else None
                check("⭐⭐ 8-F：多个候选 → **绝不猜**（格子仍未设、一个字节不写）",
                      bB is not None
                      and (bB.title() or "") == "—  笔记库  未设"
                      and not (stB / "vault").exists(),
                      f"{bB.title() if bB is not None else None!r} "
                      f"vault_written={(stB / 'vault').exists()}")
                calls4: list = []
                E.pick_folder = lambda **kw: (calls4.append(kw), None)[1]
                if bB is not None:
                    bB.performClick_(None)
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
                b3 = _vault_btn(h3) if h3 is not None else None
                before3 = (b3.title() or "") if b3 is not None else None
                if b3 is not None:
                    b3.performClick_(None)
                    check("⭐⭐ 写盘失败 → 标题**不许**变成「已设」",
                          (b3.title() or "") == before3, repr(b3.title()))
            finally:
                _OW.remember_vault = orig_remember
                try:
                    h3.close()
                except Exception:                              # noqa: BLE001
                    pass
    finally:
        if _saved_home is not None:
            os.environ["HOME"] = _saved_home
        if _saved_env is not None:
            os.environ["OBSIDIAN_VAULT"] = _saved_env
        E.pick_folder = orig_pick


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

    def _engine_btn(h):
        for v in _walk(h.window.contentView(), []):
            if isinstance(v, NSButton) and "翻译引擎" in (v.title() or ""):
                return v
        return None

    import cloud_translator
    import keyentry
    import notice
    orig = (notice.ask_text, cloud_translator.load_api_key, keyentry.load_jev)
    caps: list = []
    notice.ask_text = lambda title, message, fields, **kw: (
        caps.append(fields), None)[1]
    cloud_translator.load_api_key = lambda _=None: "sk-SECRET-DS"
    keyentry.load_jev = lambda **kw: "apikey-jev-SECRET"
    try:
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            gl = root / "glossary.txt"
            gl.write_text("# public table\n", encoding="utf-8")
            h = E.build(glossary=gl, sessions_dir=root / "s", state_root=root / "st",
                        on_start=lambda c: None)
            try:
                btn = _engine_btn(h) if h is not None else None
                check("（前置）面板上找得到「翻译引擎」那一格", btn is not None, "")
                if btn is not None:
                    btn.performClick_(None)
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
