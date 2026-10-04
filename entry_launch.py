#!/usr/bin/env python3
"""零参数入口：**先开课程卡片面板，点「开始上课」才录课**。

    ClassLive.app/Contents/MacOS/python entry_launch.py <退出码文件>

作者 2026-09-26 拍的第 1 条：

    现在：  双击 .app → 立刻开麦录音
    之后：  双击 .app → 课程卡片面板 → 点某张卡的「开始上课」→ 写 .course + 启动录音

## ⚠️ 为什么是**独立进程**，而不是塞进 `main.py`

因为这一条是硬要求：**「面板崩了不能导致录不了课」**。

做成独立进程之后，那条保证是**结构上**成立的，不靠 try/except 兜：

    cl（零参数分支，cl:204-216）→ entry_launch.py（本文件，短命独立子进程）
                                    ↓ 退出码
                                  cl → exec "$PY" main.py（录课那条路，**一个字没动**）

面板再怎么崩，最坏也就是本进程非零退出，`cl` 照旧走原来那条立刻开麦的路。

## 退出码（`cl` 只读得动这个）

    0 = 用户选了课（`.course` 已写好）→ 照常录课
    1 = 用户**主动关掉**了面板       → 不录（这不是故障，是意图）
    2 = **面板起不来**               → `cl` 退回「照旧立刻开麦」
    3 = （已不再返回）原「没课程可显示」。零课程现在照常开面板 —— 空状态里有新建入口，
        新用户在那里下模型、建课。`cl` 仍认这个码（向后兼容），常量也留着。
    4 = 选了课但 **`.course` 写不下去** → **不录**
        ⚠️ 2026-09-29 加。原来这一档复用了 `2`，于是 `cl` 照旧开麦 ——
           而盘上那份 `.course` 还是**上一门课**的 → 这节课**静默记到上一门课名下**。
           文案当时写的是「这次不录」，**和实际行为正好相反**。
        ⚠️ 为什么是「不录」而不是「照旧录」：记到错课上的代价**不可逆**
           （笔记、术语表、课次全都挂错门），而少录一次用户当场就能发现。

⚠️ 1 和 2 必须分开：把「用户取消」也当成故障去录课，等于**违背用户意图**；
   把「面板挂了」当成取消，等于**录不了课**（正是要防的那件事）。
⚠️ 4 必须与 2 分开：**「面板能用」和「选的结果存得下来」是两件事** ——
   混在一起就会用一份陈旧的 `.course` 去录，而那比不录坏得多。

## 两个旁路文件（面板与 `cl` 的全部接口）

    面板「开始上课」 → 写 `.course`      → cl 读 → --course
    面板「测试模式」 → 写 `.test-mode`   → cl 读 → --test-mode

⚠️ **两者都住在安装目录**（`HERE`），都是几字节的纯文本，都在 `.gitignore` 里。
⚠️ `cl` 读的是**同一个文件**，但它先 `cd` 到安装目录再读相对路径 —— 同一份东西。
"""
from __future__ import annotations

import pathlib
import sys
import traceback

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

PICKED, CANCELLED, UNAVAILABLE, NO_COURSES, UNSAVED = 0, 1, 2, 3, 4
CFG = HERE / ".course"
#: 测试模式的开关（2026-09-30）。**旁路文件**，与 `.course` 同一族
#: （`paths.py` 文件头那张表里的"旧"档：住在安装目录、代码旁边），
#: 内容就是 `1` / `0`，不带换行。⚠️ `cl` 读它 → 给 `main.py` 加 `--test-mode`
#: （**只加在会录一节真课的那几条路上**；`cl file` 回放不算，见 `cl` 里那段）。
#: ⚠️ 它在 `.gitignore` 里（个人状态，不入库）—— 加新旁路文件时别忘了一起加。
TESTMODE_CFG = HERE / ".test-mode"


def read_test_mode(path=None) -> bool:
    """盘上的开关。**只有 `1` 算开**，其余（空/`0`/没这个文件/读不了）一律算关。

    ⚠️ 宽容方向是**关**：读不出来时绝不能变成"以为在测试模式里"——
       那是**会录音**的一条路（`main.py` 里 `--test-mode` 默认 `record_audio=True`）。
    """
    p = pathlib.Path(path) if path is not None else TESTMODE_CFG
    try:
        return p.read_text(encoding="utf-8").strip() == "1"
    except OSError:
        return False


def write_test_mode(on: bool, path=None) -> bool:
    """写盘，**返回成不成**。

    ⚠️⚠️ 这个返回值是**接口的一部分**（面板拿它决定要不要把开关弹回原状）：
       盘上没写成就显示「开」，用户会以为在采集 —— 而**什么都没记**。
       同 `.course` 那次事故的形状（2026-09-29 的 `UNSAVED`）：
       **界面说的和盘上写的是两件事**。
    """
    p = pathlib.Path(path) if path is not None else TESTMODE_CFG
    try:
        p.write_text("1" if on else "0", encoding="utf-8")
        return True
    except OSError as e:
        print(f"⚠ 写 .test-mode 失败：{e}")
        return False


def main() -> int:
    # ⚠️ `done` 这个闩是必需的，不是防御性冗余：**决策可能在 `runEventLoop()`
    #    之前就发生**（那时 `stopEventLoop()` 叫得太早，之后再进循环就**永远出不来**）。
    #    实测踩到过：测试里让 `open_panel` 立刻回调 `on_start`，进程就挂死在循环里。
    #    ⚠️ 真机上的按钮点击只会在循环跑起来之后发生，所以这条平时碰不到 ——
    #    但「平时碰不到」不等于「不会碰到」，而挂死的代价是整个录课流程卡住。
    rc = {"code": CANCELLED, "done": False}     # 默认：关掉面板 = 不录

    try:
        import entry_panel
    except Exception:                           # noqa: BLE001
        traceback.print_exc()
        return UNAVAILABLE

    # ⚠️ **零课程也照常开面板**（2026-10-04 改）。原来这里「一门课都没有 → 退回直接录课」
    #    （返回 3）。但**全新安装**恰好满足这一条：新 clone 里 `glossary/` 与
    #    `~/.classlive/courses/` 都是空的 → 面板永远不出现 → 新用户既点不到就绪条上
    #    「下载语音模型」，也建不了课、填不了 key，只能在录课起步时撞上「模型没装好」
    #    （一位 macOS 13 新用户实测；第一版只在「模型没齐」时开面板，模型一下完
    #    面板又消失了，同一个人又卡了一次 —— 所以不按模型状态分叉）。
    #    面板的零课程空状态（`empty_state_lines`）与「＋ 新增课程」早就有了。
    #    代价：从没建过课、想直接录音的人要先建一门课；想跳过面板用 `CL_NO_PANEL=1`。

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
            # ⚠️ 用**专属**退出码 `UNSAVED`，不是 `UNAVAILABLE`（2026-09-29 修）。
            #    复用 2 会让 `cl` 照旧开麦，而盘上那份 `.course` 还是上一门课的
            #    → 这节课**静默记到上一门课名下**（不可逆：笔记/术语/课次全挂错门）。
            print(f"⚠ 写 .course 失败：{e} —— 这次不录（免得记到上一门课上）")
            rc["code"] = UNSAVED
        finally:
            _stop()

    def on_close() -> None:
        _stop()                                 # 保持默认的 CANCELLED

    def on_test_mode(on: bool) -> bool:
        """落点条上那颗开关被点了。⚠️ **当场写盘、不等到「开始上课」** ——
        它是个**持久状态**（"这节课怎么录"），同 `.course` 那条：
        写盘这件事在面板外面做，面板只负责把值喊出来。

        ⚠️ 返回 `False` = 没写进去 → 面板会把标题弹回原状（别让界面撒谎）。
        """
        ok = write_test_mode(on)
        if ok:
            print(f"测试模式{'已开 —— 这节课会录音频并上传' if on else '已关'}")
        return ok

    def _stop() -> None:
        rc["done"] = True                       # ⚠️ 先立闩，再停循环 —— 见上面的说明
        from PyObjCTools import AppHelper
        AppHelper.stopEventLoop()

    try:
        h = entry_panel.open_panel(on_start=on_start, on_close=on_close,
                                   on_test_mode=on_test_mode,
                                   test_mode=read_test_mode())
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
