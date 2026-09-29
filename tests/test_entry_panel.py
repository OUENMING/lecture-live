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

        check("⭐ 三条码互不相同（`cl` 的 case 分支靠这个分清）",
              len({entry_launch.PICKED, entry_launch.CANCELLED,
                   entry_launch.UNAVAILABLE, entry_launch.NO_COURSES}) == 4)
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
    bad = [n for n, ok, _ in RESULTS if not ok]
    print(f"{len(RESULTS) - len(bad)}/{len(RESULTS)} 通过")
    for n in bad:
        print(f"  ❌ {n}")
    return 1 if bad else 0


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
