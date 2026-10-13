"""全应用动效的**唯一参数源**: 时长三档 + 一条缓动曲线 + 「减弱动态效果」读法。

为什么要收成一处(2026-10-13): 之前四个地方各写各的 —— 胶囊切档 0.2 / 悬停 0.12 /
引导翻页 0.18 / 上滚 0.28, 曲线一处是闭式 ease-out-cubic、其余三处**没设**
(= CA 默认的线性), 「减弱动态效果」读不到时 overlay 当「没开」、tour 当「开了」。
同一个面板上的动作节奏不一致, 读起来就是「各动各的」。

时长分档(`impeccable/animate` 的时长表与 `docs/PLAN-roll-motion.md` §1.3③ 交叉验证):
  · FEEDBACK_S 0.12 —— 即时反馈(悬停)。100–150ms 档。
  · STATE_S    0.20 —— 原地换状态(切档文字淡换、翻页、草稿淡出)。150–300ms 档。
  · MOVE_S     0.28 —— 有位移的(上滚、定稿入列)。macOS 惯例 0.20–0.35、CFR §15.119
    上限 0.433s, 取 0.28 离上限留 0.15s 余量。
  · EXIT_S     0.16 —— 离场比入场快(草稿在定稿顶上来时让位)。

曲线: **ease-out-cubic** `1-(1-t)³` —— 起步快、软着陆, 字幕要的就是「尽快就位, 然后静止」。
自己驱动 f(t) 的地方用 `ease_out`; 交给 Core Animation 的地方用 `ca_timing()`,
两者是**同一条曲线**(ease-out-cubic 的贝塞尔控制点 (0.33,1)(0.68,1))。
不用弹簧、不用过冲(`PLAN-roll-motion.md` §7)。
"""

from __future__ import annotations

FEEDBACK_S = 0.12
STATE_S = 0.20
MOVE_S = 0.28
EXIT_S = 0.16

EASE_POW = 3.0                      # ease-out-cubic 的指数
CA_CONTROL_POINTS = (0.33, 1.0, 0.68, 1.0)   # 同一条曲线的贝塞尔形式, 给 CA 用


def ease_out(t: float) -> float:
    """归一化时间 -> 进度 [0, 1]。闭式、单调、端点精确; [0,1] 外夹取。"""
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else float(t))
    return 1.0 - (1.0 - t) ** EASE_POW


def progress(t0: float, now: float, duration: float) -> float:
    """从 t0 起跑、时长 duration 的动画, 在 now 时刻的**归一化时间**(夹到 [0,1])。"""
    if duration <= 0.0:
        return 1.0
    return max(0.0, min(1.0, (now - t0) / duration))


def ca_timing():
    """Core Animation / NSAnimationContext 用的同一条缓动。拿不到 Quartz 就返回 None(= 默认)。"""
    try:
        from Quartz import CAMediaTimingFunction
        return CAMediaTimingFunction.functionWithControlPoints____(*CA_CONTROL_POINTS)
    except Exception:                                   # noqa: BLE001
        return None


def reduce_motion() -> bool:
    """系统「减弱动态效果」开着吗。**每次现读**(设置会在运行中变, 面板永不成为 key,
    没有「激活时重读」的时机)。读不到当「没开」—— 读不到通常是没有 AppKit, 那也不会有动画。"""
    try:
        from AppKit import NSWorkspace
        return bool(NSWorkspace.sharedWorkspace().accessibilityDisplayShouldReduceMotion())
    except Exception:                                   # noqa: BLE001
        return False
