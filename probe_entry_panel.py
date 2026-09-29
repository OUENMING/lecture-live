#!/usr/bin/env python3
"""课程卡片面板 · 验收跑器（**隔离模式**）。

    ClassLive.app/Contents/MacOS/python probe_entry_panel.py

面板浮在屏幕中上。可以做的：

    1. 往**任意一张卡**上拖一个文件（Finder 里随便什么文件都行）
    2. 点底部那条上的「选择文件…」（选一堆课件 → 批量归档）
    3. 点底部那条上的「＋ 新增课程」→ 输个课号 → 回车 / Esc
       ⚠️ 卡片上的「选择文件…」**2026-09-28 已删**（作者：看着太繁）——
          那条路现在只有底部这一个入口。
    4. 跑完之后，点结果里某一条右边的「删」，再点「撤销」
    5. ⭐ **右键任意一张卡 → 「删除课程…」** —— 会弹确认框（全部删除 / 只删课号 / 取消）
       ⚠️ 确认框只在 `CLASSLIVE_FROM_APP=1` 时弹（`notice.alert` 的既有判据）——
          跑器默认**不弹**，那时会看到状态行说「要在 ClassLive.app 里确认」。
          想看弹框就这样起：`CLASSLIVE_FROM_APP=1 … probe_entry_panel.py`
       ⚠️ 删的是 **`/tmp` 那份副本**；但「全部删除」会把副本文件真的送进你的废纸篓。
    6. 点右下角「关闭」

环境变量（不进交互）：

    PROBE_BATCH=1       一上来就跑批量归档（13 份假课件）
    PROBE_SEARCH=<词>    直接全库搜（只读）
    PROBE_ADD=<课号>     走新增课程那条路；**留空**（`PROBE_ADD=`）= 只打开那一行
    PROBE_SESSIONS=<课号>  直接开那门课的课次列表（读**替身** `sessions/`，见下）
    PROBE_ABORT=1       隔离地看「没跑完 + 逐文件失败」两行长什么样

## ⚠️ 隔离在哪（三条都是「上一版跑器踩过」的教训）

| 真实资源 | 这里 |
|---|---|
| `glossary/<课号>.txt` | **副本**在 `/tmp/classlive-probe-entry/glossary/` —— 删词改的是副本 |
| `~/.classlive/`（materials / prep-state） | `/tmp/classlive-probe-entry/courses/` |
| `term_notes.json` / DeepSeek API | **完全不碰**：`prepare` 是桩，不发请求、不花钱 |

⚠️ **`glossary=` 必须显式传。** 上一版没传 → `glossary_of` 落回真实 glossary，
于是「点删没反应」（假词不在真表里），看着像产品 bug。
⭐ 桩返回的词是**从副本里真读出来的行**（不是 `term0` 那种假词）——
这样才能真的删掉、真的撤销，而不是点了个空。

⚠️ 菜单栏图标不在这里 —— 那是 `overlay.py` 的，只有 `cl` 跑起来才有。（这不是 bug。）
⚠️ 退出：点「关闭」，或在本终端 Ctrl+C。
"""
from __future__ import annotations

import os
import pathlib
import shutil
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import classify                                                      # noqa: E402
import entry_panel                                                  # noqa: E402

ISO = pathlib.Path("/tmp/classlive-probe-entry")


class StubRes:
    """形状对齐 `prep.PrepResult` 的那几个字段。"""
    def __init__(self, added, not_added, failed=None, aborted=""):
        self.aborted = aborted
        self.added = added
        self.not_added = not_added
        self.skipped_existing = 0
        self.notes_added = len(added)
        # ⚠️ 2026-09-28 起面板会**逐文件**渲染失败、并把 `aborted` 说成人话 ——
        #    而原来的桩 `failed = []`、`aborted = False`，**这两档在隔离跑器里根本出不来**，
        #    于是作者手验看不见它们（`PROBE_ABORT=1` 就是为这个加的）。
        self.failed = failed or []


def stub_prepare(course, files, *, on_progress=None):
    """假「跑 prep」：**不读文件、不出网、不写术语表**，只报进度 + 返回结果。

    ⭐ 返回的 `added` 是**副本里真实存在的行** —— 这样「删」那条链路才验得动
    （上一版返回 `term0`…`term6`，作者一眼看出「不是 PPT 的词汇」，
    而那也导致点「删」必然 `not_found`）。

    `PROBE_ABORT=1` —— 隔离地看「没跑完 + 逐文件失败」两行长什么样（不碰任何真数据）。
    """
    print(f"\n★ 落点：课 = {course}   文件 = {list(files)}", flush=True)
    if os.environ.get("PROBE_ABORT"):
        import prep as _p
        bad = _p.FileReport(
            path=pathlib.Path("/tmp/（桩造的）假课件.pdf"), status="unreadable",
            chars=0, blocks=0, skipped_shapes=0, ocr_pages=0,
            error="ValueError: 不是 PDF（桩造的样本，用来验失败行）")
        for stage, total in (("extract", 1), ("candidates", 0)):
            for i in range(1, total + 1):
                if on_progress:
                    on_progress(stage, i, total)
                time.sleep(0.5)
        print("  桩返回：all_files_failed（+1 条失败）", flush=True)
        return StubRes([], [], failed=[bad], aborted="all_files_failed")
    g = ISO / "glossary" / f"{course}.txt"
    terms = [x.strip() for x in g.read_text(encoding="utf-8").splitlines()
             if x.strip() and not x.startswith("#")] if g.exists() else []
    added = terms[:3]
    not_added = ["（桩：这两个是故意没加的）", "placeholder"]
    for stage, total in (("extract", 2), ("candidates", 1), ("append", 1)):
        for i in range(1, total + 1):
            if on_progress:
                on_progress(stage, i, total)
            time.sleep(0.5)
    print(f"  桩返回：加了 {added} / 没加 {not_added}", flush=True)
    return StubRes(added, not_added)


def stub_suggest(files, *, courses, course_briefs, head_of, ask, on_progress=None):
    """假「批量分类」：**不读文件、不出网**，返回一份固定建议。

    ⚠️ 形状必须与 `classify.suggest` **逐参对齐** —— 它是通过
       `entry_panel` 的 `suggest_fn` 注入点顶替真实现的那个函数。

    这份样本刻意复制 2026-09-28 那轮**真跑**的形状：5 份认得出来（其中 2 份带课号）、
    7 份认不出来。这样渲染出来的映射卡与当时给作者看的那张**同形**。
    """
    rows = [("Intro 2026 Theme 2 Sociology - Copy.pdf", "SOC10020", "标题写着 Introduction to Sociology"),
            ("Exploring Economics_Outline 2026.pdf", "ECON10740", "标题里就有课号"),
            ("ECON10740 Task 1 - Ou Enming 25242844.pdf", "ECON10740", "文件名里有课号"),
            ("Syllabus - Introduction to Sociology 2026.pdf", "SOC10020", "正文标题 SOC10020"),
            ("ECON10740 Task 1 - Information Literacy.pdf", "ECON10740", "文件名里有课号")]
    left = [("ps-8.pdf", None, "都不属于（Math10260）"),
            ("1_fundamental.pdf", None, "都不属于（C 语言课）"),
            ("chen10040_2025-26_Assignment_1.pdf", None, "都不属于（CHEM10040）"),
            ("OP0145-HPR_Rheumatoid_arthriti.pdf", None, "都不属于（医学论文）"),
            ("1-s2.0-S0003687015000198-main.pdf", None, "都不属于（期刊论文）"),
            ("EEEN10010 - Power and energy systems.pdf", None, "都不属于（能源工程）"),
            ("Template for Assignment 2 in PSY10140 Mind and Brain (1).pdf", None,
             "都不属于（PSY10140）")]
    out = []
    for i, (name, c, why) in enumerate(rows + left, 1):
        if on_progress:
            on_progress(i, len(rows) + len(left), name)
            time.sleep(0.12)
        out.append(classify.Verdict(
            f"/tmp/假课件/{name}", c, why, "code" if "课号" in why and "标题" not in why else "model"))
    return out


def _schedule_dump(h, secs: float) -> None:
    """临时仪表：几秒后把窗口的视图树打出来（排查"卡在不在"用，不是产品的一部分）。

    ⚠️ 走 `AppHelper.callLater` —— **AppKit 只能在主线程碰**，从 worker 线程摸视图树
       是这个仓库明令的不变量。
    """
    def _walk(v, depth=0):
        try:
            f = v.frame()
            print(f"{'  ' * depth}{type(v).__name__} "
                  f"({f.origin.x:.0f},{f.origin.y:.0f} {f.size.width:.0f}×{f.size.height:.0f}) "
                  f"hidden={bool(v.isHidden())}", flush=True)
        except Exception as e:                                # noqa: BLE001
            print(f"{'  ' * depth}<{type(v).__name__}: {e}>", flush=True)
            return
        if depth < 6:
            for c in (v.subviews() or []):
                _walk(c, depth + 1)

    def _go():
        print("\n===== 视图树 dump =====", flush=True)
        b = entry_panel.S.get("batch")
        print("S['batch'] =", None if b is None else
              {k: (len(v) if isinstance(v, list) else v) for k, v in b.items()},
              flush=True)
        print("S['panel'] is None?", entry_panel.S.get("panel") is None,
              "| S['busy'] =", entry_panel.S.get("busy"), flush=True)
        _walk(h.window.contentView())
        print("--- 手动再 refresh() 一次 ---", flush=True)
        try:
            h.refresh()
        except Exception:                                     # noqa: BLE001
            import traceback
            traceback.print_exc()
            print("❌ refresh 抛了（上面那个 traceback）", flush=True)
        _walk(h.window.contentView())
        print("===== dump 结束 =====\n", flush=True)

    from PyObjCTools import AppHelper
    AppHelper.callLater(secs, _go)


def main() -> int:
    if ISO.exists():
        shutil.rmtree(ISO)
    (ISO / "courses").mkdir(parents=True)
    if os.environ.get("PROBE_ZERO"):
        # ⭐ **造一次"一门课都没有"** —— 这是 A（零课程不跑模型 + 就地建课 +
        #    按文件名免费先分）唯一能被看到的路径。不拷任何术语表。
        (ISO / "glossary").mkdir()
    else:
        shutil.copytree(HERE / "glossary", ISO / "glossary")
    (ISO / "glossary.txt").write_text("", encoding="utf-8")
    real = HERE / "glossary"

    print(f"隔离根     = {ISO}")
    print(f"真 glossary = {real}   ← **一个字都不会动**")
    print("\n拖一个文件到任意一张卡上试试；跑完点「删」再点「撤销」。")

    # ⚠️⚠️ **`on_close=AppHelper.stopEventLoop` 不是可选的**（`PLAN-entry-panel §8.1 #12`）：
    #    下面那条「真 glossary 逐字节未变」的自检在 `runEventLoop()` **之后**，
    #    而 `entry_panel.do_close()` **自己不停事件循环** —— 不传这个钩子，
    #    点「关闭」之后循环照跑，**那条自检是死代码**（只有 Ctrl+C 才可能走到，
    #    而 Ctrl+C 在这个进程里不一定送达）。
    #    `on_close` 是 `entry_panel` 现成的口子（`_build` 的形参，`do_close` 末尾断环之后才调）。
    from PyObjCTools import AppHelper
    # ⚠️⚠️ **课次列表也读 `sessions/` —— 跑器必须隔离它。**
    #    不隔离的话「判给本课」会往**真的 `sessions/`** 里写一个 `.attribution.json`
    #    （不是删数据，但"隔离跑器"这条性质就破了）。这里造**同名但内容是我们自己写的**
    #    替身：名字取真的（列表渲染得像），字数是特意配好的 —— 一半够格、一半是空壳，
    #    好让两档状态都看得到。
    sess_iso = ISO / "sessions"
    sess_iso.mkdir(exist_ok=True)
    _STUBS = [("2026-09-22_150213_ECON10740", "ok"), ("2026-09-15_150616_ECON10740", "ok"),
              ("2026-09-08_150000_ECON10740", "thin"),
              ("2026-09-24_202216_LECTURE", "ok"), ("2026-09-28_120341_ECON10xxx", "ok"),
              ("2026-09-26_051548_ECON10770", "thin")]
    for _stem, _kind in _STUBS:
        _p = sess_iso / f"{_stem}.md"
        if not _p.exists():
            # ⚠️ 会话正文的**唯一读法**是 `obsidian_writer._parse`，它的抬头正则
            #    `_TS` 是**行首锚定**的（`^> [!abstract]`）—— 行首多一个空格就一条都认不出，
            #    而且**不报错**，只是每行显示 `0 句`。（第一版替身就是这么写的，已修。）
            _line = ("the quick brown fox jumps over the lazy dog while the lecture "
                     "continues through every single chapter of this course today")
            _body = ""
            if _kind == "ok":
                _body = "\n".join(f"> [!abstract] 09:0{i}:00\n> **EN**: {_line}\n"
                                  for i in range(4))
            _p.write_text(f"# {_stem.split('_', 2)[-1]} · stub\n\n{_body}", encoding="utf-8")

    # `PROBE_TIMETABLE=1` —— **预选**那条路：造一份「时段就是现在」的课表塞进隔离根，
    #    好让面板把某张卡排到第一、并在按钮上说清为什么。
    #    ⚠️ 时段是**按当前时刻算的**（不是写死的）—— 写死的话这个跑器只在某个小时有效。
    #    ⚠️⚠️ **必须在 `open_panel` 之前写** —— 面板是在 build 那一刻就算好 `S["guesses"]` 的。
    if os.environ.get("PROBE_TIMETABLE"):
        import datetime as _dt
        import json as _json
        _now = _dt.datetime.now()
        _slot = {"d": _now.weekday(), "h": _now.hour, "m": 0, "min": 60,
                 "iv": 1, "a": _now.date().isoformat(), "skip": []}
        (ISO / "timetable.json").write_text(
            _json.dumps({"_v": 1, "ECON10740": [_slot]}, ensure_ascii=False),
            encoding="utf-8")
        print(f"\nPROBE_TIMETABLE —— 造了「周{_now.weekday() + 1} {_now.hour}:00」的课表"
              f"（ECON10740），面板应把它排第一")

    h = entry_panel.open_panel(glossary=ISO / "glossary.txt", state_root=ISO,
                               sessions_dir=sess_iso,
                               # ⚠️ `on_start` 必须给个桩 —— 不给的话卡片上**一个按钮都不画**
                               #    （`_acts = on_start is not None`），于是「开始上课」/「课次」/
                               #    「不是这门？」这条按钮行在跑器里**整个看不到**，
                               #    而它正是出过 bug 的那一行（按钮叠在就绪行上）。
                               #    桩只打印，**不写 `.course`、不起录音**。
                               on_start=lambda c: print(f"（桩）开始上课：{c}"),
                               prepare_fn=stub_prepare,
                               suggest_fn=stub_suggest,
                               on_close=AppHelper.stopEventLoop)
    if h is None:
        print("❌ build 返回 None")
        return 1

    # `PROBE_BATCH=1` —— 一上来就把批量那条链路整个跑一遍（映射卡直接上屏），
    # 不用手拖。⚠️ 走的是 `Handles.start_batch` 那个**程序化入口**，因为
    # 真拖拽进不了验收跑器（`test_panel.py` 第 ⑨ 组有同样的说明）。
    if os.environ.get("PROBE_BATCH"):
        print("\nPROBE_BATCH=1 —— 直接跑批量：13 份假课件（5 份有归属 / 7 份未分类）")
        AppHelper.callAfter(h.start_batch, [f"/tmp/假课件/样本{i}.pdf" for i in range(13)])
        if os.environ.get("PROBE_DUMP"):
            _schedule_dump(h, 5.0)

    # `PROBE_SEARCH=<词>` —— 直接跑一遍全库搜索（**只读**，不改任何东西）。
    if os.environ.get("PROBE_SEARCH"):
        q = os.environ["PROBE_SEARCH"]
        print(f"\nPROBE_SEARCH={q!r} —— 直接搜（只读）")
        AppHelper.callAfter(h.search, q)

    # `PROBE_ICS=1` —— 造一份假课表走导入那条路（**只到确认卡，不落盘**）。
    if os.environ.get("PROBE_ICS"):
        _p = ISO / "probe-timetable.ics"
        _p.write_bytes(
            b"BEGIN:VCALENDAR\r\n"
            b"BEGIN:VEVENT\r\nUID:a\r\nSUMMARY:ECON10740: Exploring Economics (Lecture)\r\n"
            b"DTSTART;TZID=Europe/Dublin:20260908T150000\r\n"
            b"DTEND;TZID=Europe/Dublin:20260908T160000\r\n"
            b"RRULE:FREQ=WEEKLY;BYDAY=TU\r\nEND:VEVENT\r\n"
            b"BEGIN:VEVENT\r\nUID:b\r\nSUMMARY:ECS 170 001 Introduction to AI (Lecture)\r\n"
            b"DTSTART:20260914T141000Z\r\nRRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,WE\r\n"
            b"END:VEVENT\r\n"
            b"BEGIN:VEVENT\r\nUID:c\r\nSUMMARY:ECON10770: Introduction to Economics (Tutorial)\r\n"
            b"DTSTART:20260908T120000\r\nEND:VEVENT\r\n"
            b"END:VCALENDAR\r\n")
        print(f"\nPROBE_ICS —— 导入 {_p.name}（只解析，不落盘）")
        AppHelper.callAfter(h.import_timetable, [str(_p)])

    # `PROBE_SESSIONS=<课号>` —— 直接打开那门课的课次列表（读的是**替身目录**）。
    if os.environ.get("PROBE_SESSIONS"):
        _c = os.environ["PROBE_SESSIONS"]
        print(f"\nPROBE_SESSIONS={_c!r} —— 直接开课次列表")
        AppHelper.callAfter(h.show_sessions, _c)

    # `PROBE_ZERO=1` —— 零课程那一档。造几个假课件直接走批量那条路；
    #   再加 `PROBE_ZERO_ADD=<课号>` 就在 3 秒后建这门课 ——
    #   用来验收「建一门，名字里带那个课号的**立刻**归好（0 次 API）」。
    if os.environ.get("PROBE_ZERO"):
        zdir = pathlib.Path("/tmp/classlive-probe-zero")
        zdir.mkdir(exist_ok=True)
        names = ["ECON10740_Lecture3.pdf", "ECON10740_Lecture4.pdf",
                 "SOC10020_reading.pdf", "别人的讲义.pdf", "期刊论文样本.pdf"]
        for n in names:
            (zdir / n).write_bytes(b"%PDF-1.4\n% fake\n")
        files = [str(zdir / n) for n in names]
        print(f"\nPROBE_ZERO=1 —— 零课程：{len(files)} 份假课件走批量那条路")
        AppHelper.callAfter(h.start_batch, files)
        _add = os.environ.get("PROBE_ZERO_ADD")
        if _add:
            print(f"  3 秒后建课 {_add!r}（看「免费按文件名分」那一步）")
            AppHelper.callLater(3.0, lambda: h.add_course(_add))

    # `PROBE_ADD=<课号>` —— 走新增课程那条路（等价于点 «＋ 新增课程» → 敲入 → 回车）。
    # ⚠️ 留空（`PROBE_ADD=`）就是**只打开那一行、什么都不提交** —— 看输入行长什么样
    #    用这个。（跑器里没有真键盘，`add_course` 是 `Handles` 上的程序化入口。）
    if os.environ.get("PROBE_ADD") is not None:
        txt = os.environ["PROBE_ADD"]
        print(f"\nPROBE_ADD={txt!r} —— 新增课程"
              f"（{'只打开那一行' if not txt else '敲入并回车'}）")
        AppHelper.callAfter(h.add_course, txt)

    AppHelper.runEventLoop()

    # 退出后核对：真 glossary 有没有被动过
    bad = []
    for f in real.glob("*.txt"):
        copy = ISO / "glossary" / f.name
        # ⚠️ 副本可能**根本不存在**：`PROBE_ZERO=1` 那条路只 `mkdir`、一份都不拷。
        #    原来直接 `read_bytes()` → 每个文件抛 FileNotFoundError、整段自检崩掉，
        #    **拿不到「真 glossary 未变」这个结论**（而它是隔离跑器最关键的保障）。
        #    缺副本 = ❌，不是异常。
        if not copy.exists() or copy.read_bytes() != f.read_bytes():
            bad.append(f.name)
    print("\n" + ("✅ 真 glossary 逐字节未变" if not bad
                  else f"❌ 这些被动过了：{bad}"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
