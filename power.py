#!/usr/bin/env python3
"""上课 / 收尾期间**不要空闲睡眠** —— 一个进程级 AppKit 活动断言。

## 为什么要它

macOS 在整机没有输入、又没别的大活时会**空闲睡眠**。ClassLive 恰恰是这种形状：
屏前没人动鼠标、没有前台交互，只有后台在采音 + 跑模型 + 等网络。一旦睡下去，
`capture` 拿不到音频（音频子系统随睡眠停摆，醒来后字幕接着往前走，那一段缺口
**谁都看不出来**）—— 真实事故：一位美国用户 10-14 那节课**中间 44 分钟没有声音**。
收尾那一段更脆：复习层 / 补译 / 打包上传是几十秒到几分钟的**纯等待 + 网络**，
屏前同样没人动，正好满足空闲睡眠的条件。

## 契约

    with keep_system_awake():        # 进入持有，退出（**含抛异常**）释放
        ...

- 断言是**进程级**的（`NSProcessInfo.beginActivityWithOptions_reason_`），只挡
  「空闲系统睡眠」+「突然终止」。传进流水线的活（采音 / 转写 / 翻译 / 落盘）都不是
  AppKit，没有别的口子能表达「我还在干活」。
- ⚠️⚠️ **合盖睡眠挡不住**：clamshell sleep 是硬件/电源策略定的，任何 app 的
  `NSActivity` 都拦不住（Apple 文档明说 assertion 只在"系统本来打算 idle sleep"
  时生效）。想连合盖也录，只能去「系统设置 → 电池 / 锁屏」，那是**用户自己的设置**,
  本模块**刻意不碰**（不改 `pmset`、不改显示、不改音量）。
- **永不抛**：拿不到 `NSProcessInfo`（非 macOS / 没装 pyobjc）就什么都不做 ——
  少一层保险，但绝不因为它把课拦下来。

## 判据

`tests/test_power.py` 注入一个假的 `NSProcessInfo`，断言正常 / 抛异常两条路径
都 `beginActivity…` 一次、`endActivity_` 一次；再断言 `main.main()` 真的把它套在
`run(args)` 上（AST，同 `test_draft_engine.Wiring` 的做法）。
"""
from __future__ import annotations

import contextlib

#: `NSActivityIdleSystemSleepDisabled`（Foundation `NSProcessInfo.h`，值已在
#: 这台机器上实测：1048576）。**没有**用 `NSActivityUserInitiated` —— 那个还会
#: 顺带拉高"性能/亮度"档，我们只要「别睡」。
IDLE_SYSTEM_SLEEP_DISABLED = 1 << 20
#: `NSActivitySuddenTerminationDisabled`（实测 16384）。持着它，macOS 不会为了
#: 省时间在退出/登出时把进程"直接砍掉"（收尾正在写盘，宁可慢一点也要写完）。
SUDDEN_TERMINATION_DISABLED = 1 << 14

OPTIONS = IDLE_SYSTEM_SLEEP_DISABLED | SUDDEN_TERMINATION_DISABLED

#: 理由串会出现在 `pmset -g assertions` / 活动监视器里，写清楚是谁在持着。
REASON = "ClassLive 正在录课并生成课后笔记"


def process_info():
    """真的 `NSProcessInfo`。⚠️ 只在调用点 import —— 非 macOS 上这个模块仍可导入。

    抽成函数是为了**判据能整块换掉它**（注入替身），不是为了生产上有第二个实现。
    """
    from Foundation import NSProcessInfo
    return NSProcessInfo.processInfo()


class Activity:
    """一次持有。`begin()` / `end()` 各自**幂等**，且**永不抛**。

    ⚠️ 掐断言失败时 `end()` 什么都不做（`_token is None`）—— 不去调一个我们根本
    没拿到的 token，也不重复喊同一句警告。
    """

    def __init__(self, process_info=None, log=None):
        self._process_info = process_info
        self._log = log
        self._token = None
        self._began = False
        self._ended = False

    def _pi(self):
        # ⚠️ 存下来的那个（判据注入的替身）优先；没有才去拿真的。
        return self._process_info if self._process_info is not None else process_info()

    def _warn(self, msg: str) -> None:
        if self._log is not None:
            try:
                self._log(msg)
            except Exception:                     # noqa: BLE001 —— 记一句话不许反过来咬人
                pass

    def begin(self) -> bool:
        """拿断言。已经拿过就原样返回；返回**是否真的持有着**。永不抛。"""
        if self._began:
            return self._token is not None
        self._began = True
        try:
            self._token = self._pi().beginActivityWithOptions_reason_(OPTIONS, REASON)
        except Exception as e:                    # noqa: BLE001
            self._token = None
            self._warn(f"⚠ 防空闲睡眠断言没拿上（不影响录课/笔记）："
                       f"{type(e).__name__}: {e}")
        return self._token is not None

    def end(self) -> None:
        """释放。幂等；没拿过（或拿失败）就什么都不做。永不抛。"""
        if self._ended:
            return
        self._ended = True
        if self._token is None:
            return
        token, self._token = self._token, None
        try:
            self._pi().endActivity_(token)
        except Exception as e:                    # noqa: BLE001
            self._warn(f"⚠ 防空闲睡眠断言没释放掉：{type(e).__name__}: {e}")


@contextlib.contextmanager
def keep_system_awake(process_info=None, log=None):
    """`with` 一进去就持有，出来（**正常 / 抛异常都算**）就释放。

    ⚠️ 释放放在 `finally` 里是**要求**（不是风格）：拿住了不还，这台机器的空闲
    睡眠就永远被这个进程拦着 —— 而"拦着不睡"正是这个模块要干的事，泄漏出去的
    后果（笔记本不合盖也永远不睡）比不拿还糟。
    """
    act = Activity(process_info, log=log)
    act.begin()
    try:
        yield act
    finally:
        act.end()
