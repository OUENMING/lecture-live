#!/usr/bin/env python3
"""零参数入口：**先开课程卡片面板，点「开始上课」才录课**。

    ClassLive.app/Contents/MacOS/python entry_launch.py <退出码文件>

作者 2026-09-26 拍的第 1 条：

    现在：  双击 .app → 立刻开麦录音
    之后：  双击 .app → 课程卡片面板 → 点某张卡的「开始上课」→ 写 .course + 启动录音

## ⚠️ 为什么是**独立进程**，而不是塞进 `main.py`

因为这一条是硬要求：**「面板崩了不能导致录不了课」**。

做成独立进程之后，那条保证是**结构上**成立的，不靠 try/except 兜：

    cl-bg.py（双重 fork）→ cl → entry_launch.py（本文件，短命）
                                    ↓ 退出码
                                  cl → main.py（录课那条路，**一个字没动**）

面板再怎么崩，最坏也就是本进程非零退出，`cl` 照旧走原来那条立刻开麦的路。

## 退出码（`cl` 只读得动这个）

    0 = 用户选了课（`.course` 已写好）→ 照常录课
    1 = 用户**主动关掉**了面板       → 不录（这不是故障，是意图）
    2 = **面板起不来**               → `cl` 退回「照旧立刻开麦」
    3 = 没课程可显示                 → 退回「照旧立刻开麦」（没课可选时不该卡住人）

⚠️ 1 和 2 必须分开：把「用户取消」也当成故障去录课，等于**违背用户意图**；
   把「面板挂了」当成取消，等于**录不了课**（正是要防的那件事）。
"""
from __future__ import annotations

import pathlib
import sys
import traceback

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

PICKED, CANCELLED, UNAVAILABLE, NO_COURSES = 0, 1, 2, 3
CFG = HERE / ".course"


def main() -> int:
    # ⚠️ `done` 这个闩是必需的，不是防御性冗余：**决策可能在 `runEventLoop()`
    #    之前就发生**（那时 `stopEventLoop()` 叫得太早，之后再进循环就**永远出不来**）。
    #    实测踩到过：测试里让 `open_panel` 立刻回调 `on_start`，进程就挂死在循环里。
    #    ⚠️ 真机上的按钮点击只会在循环跑起来之后发生，所以这条平时碰不到 ——
    #    但「平时碰不到」不等于「不会碰到」，而挂死的代价是整个录课流程卡住。
    rc = {"code": CANCELLED, "done": False}     # 默认：关掉面板 = 不录

    try:
        import courses
        import entry_panel
    except Exception:                           # noqa: BLE001
        traceback.print_exc()
        return UNAVAILABLE

    try:
        if not courses.list_courses(HERE / "glossary.txt"):
            print("⚠ 一门课都没有 —— 退回直接录课（别让人卡在空面板上）")
            return NO_COURSES
    except Exception:                           # noqa: BLE001
        traceback.print_exc()
        return UNAVAILABLE

    def on_start(course: str) -> None:
        """用户点了「开始上课」。

        ⚠️ 写 `.course` 用 `pathlib` 而不是 shell 重定向 —— 课号来自界面，
           不该有任何机会被当成 shell 语法。内容不带换行（与 `cl` 的
           `printf '%s'` 一致，下游读的是整个文件）。
        """
        try:
            CFG.write_text(course, encoding="utf-8")
            print(f"✅ 课程已设为 {course}")
            rc["code"] = PICKED
        except OSError as e:
            print(f"⚠ 写 .course 失败：{e} —— 这次不录")
            rc["code"] = UNAVAILABLE
        finally:
            _stop()

    def on_close() -> None:
        _stop()                                 # 保持默认的 CANCELLED

    def _stop() -> None:
        rc["done"] = True                       # ⚠️ 先立闩，再停循环 —— 见上面的说明
        from PyObjCTools import AppHelper
        AppHelper.stopEventLoop()

    try:
        h = entry_panel.open_panel(on_start=on_start, on_close=on_close)
    except Exception:                           # noqa: BLE001
        traceback.print_exc()
        return UNAVAILABLE

    if h is None:                               # `build()` 失败就是返回 None
        print("⚠ 面板起不来 —— 退回直接录课")
        return UNAVAILABLE

    from PyObjCTools import AppHelper
    if not rc["done"]:                          # ⚠️ 已经定了就别再进循环（会出不来）
        AppHelper.runEventLoop()
    return rc["code"]


if __name__ == "__main__":
    sys.exit(main())
