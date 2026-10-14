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

    5 = 面板里点了「更新」、新代码已经拉下来 → `cl` **重开自己**（`exec`，读到的是新文件），
        再进一次面板。本进程里的模块是旧的，所以不能就地接着用。`cl` 用 `CL_RESTARTS` 限次，防死循环。

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

import os
import pathlib
import sys
import traceback

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

PICKED, CANCELLED, UNAVAILABLE, NO_COURSES, UNSAVED, RESTART = 0, 1, 2, 3, 4, 5
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


def _cloud_state() -> tuple[bool, bool]:
    """`(开关初值, 是否被环境变量锁死)`。初值 = `final_asr.read_config()["enabled"]`；
    读不出来 = `(False, False)`（宁可少上云）。"""
    try:
        import final_asr
        return bool(final_asr.read_config()["enabled"]), bool(final_asr.cloud_forced_off())
    except Exception:                                   # noqa: BLE001
        return False, False


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

    def on_cloud_asr(on: bool) -> bool:
        """「云端转写」被点了（「语音模型」菜单里的勾选项）：当场写 `cloud-asr.json`（面板不写盘）。
        返回 `False` = 没写进去（含 `CLASSLIVE_CLOUD_ASR=0` 时 `write_config` 什么都不写）→ 面板弹回原状。
        """
        import final_asr
        ok = final_asr.write_config(on)
        if ok:
            print(f"云端转写{'已开 —— 定稿音频片段会发给云端识别' if on else '已关 —— 全本地'}")
        return ok

    def on_restart() -> None:
        """面板里的更新装好了新代码 → 让 `cl` 重开（退出码 `RESTART`）。"""
        rc["code"] = RESTART
        _stop()

    def _stop() -> None:
        rc["done"] = True                       # ⚠️ 先立闩，再停循环 —— 见上面的说明
        # ⚠️⚠️ **不能用 `AppHelper.stopEventLoop()`**（2026-10-11 作者实测「点关闭还是开始转录」）：
        #    在 `runEventLoop` 底下它会走到 `NSApp().terminate_()` → 进程当场 exit(0)，
        #    `main()` 来不及 `return rc["code"]` → 关闭(1) / 存不下(4) 全变成 0 → `cl` 拿上次的课开录。
        #    `NSApp.stop_` 只让 `run()` 返回（`runEventLoop` 随之正常退出）；它要处理完**下一个事件**
        #    才生效，所以补一个空的应用事件把循环推一下。判据：`tests/test_entry_launch_exit.py`（真循环）。
        from AppKit import NSApp, NSEvent, NSEventTypeApplicationDefined
        app = NSApp()
        if app is None:
            return
        app.stop_(None)
        ev = NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
            NSEventTypeApplicationDefined, (0, 0), 0, 0, 0, None, 0, 0, 0)
        app.postEvent_atStart_(ev, True)

    # ⚠️ 3.9.3 的一次性搬家（把老的翻译 key 挪到旁边）**必须赶在建面板之前** ——
    #    就绪条「翻译引擎」那一格读的是 `keyentry.has_any()`。放到后面的话，
    #    面板会一口咬定"配好了"，而一开课翻译其实是断的（界面撒谎是本仓最忌的那类）。
    try:
        import keyentry as _ke
    except Exception:                           # noqa: BLE001
        _ke = None                              # 搬不动不该拦上课；`main.run()` 那条路还会再试一次
    _got = _ke.clear_legacy_key() if _ke is not None else {}   # 它自己永不抛
    # ⭐ 真的挪走了东西才弹这一张（Owen 2026-10-12 定文案）。
    #    `first and moved` = **这台机器上确实有一把 key 被撤了** —— 那正是唯一该打扰用户的情形。
    #    ⚠️ 三条"不该弹"全靠这一个判据挡住：新用户（`moved` 空）、已经配好通行码的（`moved` 空）、
    #       以及第二次启动（`first` 为 False）。**别放宽成"看版本号"** —— 那会把本来就没 key 的
    #       人、以及已经拿到码的人一起拦一下，而他们什么都没失去。
    #    ⚠️ 时机：就在**建面板之前**，此刻没有课在录、没有音频在采、没有模型在加载 ——
    #       模态框挡不住任何正在进行的活。`cl` 那条路在终端里已经被 `main.run` 打了一行字。
    #    ⚠️ `notice.alert` 自己判「有没有人看得见」（`_can_alert` → 只有双击 .app 那侧
    #       `CLASSLIVE_FROM_APP=1` 才弹）；终端 / 管道 / 判据里它只 print，**不会挂住**。
    if _ke is not None and _got.get("first") and _got.get("moved"):
        import notice
        notice.alert(_ke.KEY_RESET_TITLE, _ke.KEY_RESET_MESSAGE)

    cloud_on, cloud_locked = _cloud_state()
    try:
        h = entry_panel.open_panel(on_start=on_start, on_close=on_close,
                                   on_test_mode=on_test_mode,
                                   test_mode=read_test_mode(),
                                   on_cloud_asr=on_cloud_asr,
                                   cloud_asr=cloud_on, cloud_asr_locked=cloud_locked,
                                   on_restart=on_restart)
    except Exception:                           # noqa: BLE001
        traceback.print_exc()
        return UNAVAILABLE

    if h is None:                               # `build()` 失败就是返回 None
        print("⚠ 面板起不来 —— 退回直接录课")
        return UNAVAILABLE

    # ⭐ 功能介绍：新用户第一次、升级到带新介绍的版本后，**双击 .app 一打开就弹一次**
    #    （Owen 2026-10-13：不是下课后）。只认 `.app`（`CLASSLIVE_FROM_APP`）—— 终端 `cl` 和判据不弹。
    if os.environ.get("CLASSLIVE_FROM_APP"):
        try:
            entry_panel.maybe_show_tour()
        except Exception:                       # noqa: BLE001
            traceback.print_exc()               # 介绍卡坏了不该拦上课

    from PyObjCTools import AppHelper
    if not rc["done"]:                          # ⚠️ 已经定了就别再进循环（会出不来）
        AppHelper.runEventLoop()

    # ⭐ 关掉面板、**没开课** = 空闲 —— 这时把自动更新和流式草稿模型（10-14 起必装）放后台。
    #    自动更新原来只挂在下课退出（`main._auto_update_on_exit`）：只开面板看一眼就关的人
    #    永远轮不到它（2026-10-13 加，Owen 同意）。限频 / 锁 / 分级都在 `auto_update` 里。
    #    ⚠️ 只认 `CANCELLED`：选了课（`PICKED`）之后是 `main` 那条路管，
    #       **上课途中绝不下** —— 464 MB 会和云端定稿的音频上传抢同一条教室 Wi-Fi。
    #    ⚠️ 闸门与自动更新共用（见 `main._draft_model_on_exit` 的说明）；子进程
    #       `start_new_session=True`，用户这就走人也照样下完。
    if rc["code"] == CANCELLED and not os.environ.get("CLASSLIVE_NO_AUTO_UPDATE"):
        try:
            import update as _u
            _u.spawn_auto_update()
            _u.spawn_draft_model_download()
        except Exception:                       # noqa: BLE001
            pass
    return rc["code"]


if __name__ == "__main__":
    sys.exit(main())
