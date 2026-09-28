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
                               on_close=AppHelper.stopEventLoop)
    if h is None:
        print("❌ build 返回 None")
        return 1

    from PyObjCTools import AppHelper
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
