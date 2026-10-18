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
import time

#: 墙钟一次跳这么多 = 系统睡过一觉。⚠️ **唯一定义点** ——
#: `main.check_clock_and_device`（上课期间的 2 s 保安线程）与 `WakeWatch`（收尾
#: spinner 的 tick）共用它；两处各写一个 10.0 迟早会漂成两个数。
SLEEP_GAP_S = 10.0

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
    ⚠️⚠️ **它挡不住合盖**（clamshell sleep 是电源策略定的，任何 app 的 activity
    都拦不住）—— 见文件头那条。合盖那条路的答案是**可续跑**：收尾的每一步要么
    当场做完、要么被日志记下、下次启动接着做（`testmode` 的 journal +
    `upload.recover_pending` + `update.repair_interrupted_apply`）。
    """
    act = Activity(process_info, log=log)
    act.begin()
    try:
        yield act
    finally:
        act.end()


class WakeWatch:
    """睡醒探测：**轮询墙钟跳变**。`check()` 返回"距上次问话之间系统睡过一觉吗"。

    ## 为什么是轮询，不是 `NSWorkspaceDidWakeNotification`

    理由与 `main.check_clock_and_device` **逐字同源**（那边管上课期间的探测，这里
    给收尾期间用）：那两条通知必须挂在 `NSWorkspace.notificationCenter()` 上、
    而且**靠 run loop 投递** —— `--ui terminal` 那条路根本没有 run loop，通知
    永远到不了。墙钟跳变不挑模式、也不挑谁在跑。

    ## 形状

        w = WakeWatch()                      # 记下当前墙钟
        ...每轮 tick 里...  if w.check():   # 睡过一觉 -> 补做网络步骤
        w.sleep_seconds                      # 刚那次跳了多少秒（只读，判据用）

    ⚠️ 时钟是**墙钟**（`time.time`），不是 monotonic —— monotonic 在 macOS 睡眠
    期间**不前进**，正因如此，所有**超时**（`spin_until` / subprocess / httpx）
    都不会把睡眠算成"卡住"；而"发现睡过"恰恰要靠 monotonic 不动的那个差值。
    ⚠️ 它只是探测，**永不抛**、不持有任何资源；`check()` 可以任意频率调。
    """

    def __init__(self, *, gap_s: float = SLEEP_GAP_S, clock=None):
        self._gap_s = float(gap_s)
        self._clock = clock or time.time
        self._wall = self._clock()
        #: 上一次 `check()` 量到的跳变秒数（没睡就是 0）。
        self.sleep_seconds = 0.0
        #: 累计发现了几次睡醒（判据用）。
        self.wakes = 0

    def check(self) -> bool:
        """这一轮与上一轮之间墙钟跳变 > `gap_s` 吗。**永不抛。**"""
        now = self._clock()
        gap = now - self._wall
        self._wall = now
        self.sleep_seconds = gap if gap > self._gap_s else 0.0
        if self.sleep_seconds:
            self.wakes += 1
        return bool(self.sleep_seconds)
