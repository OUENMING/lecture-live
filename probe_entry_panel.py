#!/usr/bin/env python3
"""课程卡片面板 · 验收跑器（**隔离模式**）。

    ClassLive.app/Contents/MacOS/python probe_entry_panel.py

面板浮在屏幕中上。可以做的：

    1. 往**任意一张卡**上拖一个文件（Finder 里随便什么文件都行）
    2. 点卡片上的「选择文件…」
    3. 跑完之后，点结果里某一条右边的「删」，再点「撤销」
    4. 点右下角「关闭」

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
    h = entry_panel.open_panel(glossary=ISO / "glossary.txt", state_root=ISO,
                               prepare_fn=stub_prepare,
                               suggest_fn=stub_suggest,
                               on_close=AppHelper.stopEventLoop)
    if h is None:
        print("❌ build 返回 None")
        return 1

    from PyObjCTools import AppHelper

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

    AppHelper.runEventLoop()

    # 退出后核对：真 glossary 有没有被动过
    bad = []
    for f in real.glob("*.txt"):
        if (ISO / "glossary" / f.name).read_bytes() != f.read_bytes():
            bad.append(f.name)
    print("\n" + ("✅ 真 glossary 逐字节未变" if not bad
                  else f"❌ 这些被动过了：{bad}"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
