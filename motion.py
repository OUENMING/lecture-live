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
  · EXIT_S     0.14 —— 离场比入场快(草稿在定稿顶上来时让位)。
    2026-10-16 由 0.16 收到 0.14(字体/动效报告 §5 A4): 仍在「离场档」内, 且与
    MOVE_S 0.28 拉开**恰好 1:2** —— 「让位」的语义比 0.16 更强。

曲线: **ease-out-cubic** `1-(1-t)³` —— 起步快、软着陆, 字幕要的就是「尽快就位, 然后静止」。
自己驱动 f(t) 的地方用 `ease_out`; 交给 Core Animation 的地方用 `ca_timing()`,
两者是**同一条曲线**(ease-out-cubic 的贝塞尔控制点 (0.33,1)(0.68,1))。
不用弹簧、不用过冲(`PLAN-roll-motion.md` §7)。
"""

from __future__ import annotations

FEEDBACK_S = 0.12
STATE_S = 0.20
MOVE_S = 0.28
EXIT_S = 0.14
#: 开课面板课卡列表**一次性错位入场**时, 相邻两张卡的起跑间隔(秒)。
#: 来源: 字体/动效报告 §5 A6(那一段是全 app 动效真空最大的一块 —— 0 条动效)。
#: 一次性、不循环; `reduce_motion()` 为真时整条跳过、直接落位。
ENTER_STAGGER_S = 0.035
#: 指示灯（「在听」小点）的亮 / 灭：**快亮慢灭**，照电平表的弹道（ITU-R BS.2103 引 Bauer：
#: 上升 0.1 s、下降 0.5 s 最好）。灭用 ease-in-out（像余辉慢慢退），亮用 ease-out。2026-10-14。
ATTACK_S = 0.12
RELEASE_S = 0.50
#: ⭐ 角落小卡（一些事儿）在**无人理**多少秒后从"亮"降到"低亮度"档（2026-10-17 作者定的策略：
#: 新条目到达 → 亮起；约 8 秒没人理 → 降到低亮度（**不消失，仍可点**）；再来新条目 →
#: 重新亮起；全部处理完 → 淡出）。亮 / 暗两档的 alpha 在 `overlay.CHIP_ALPHA_*`。
#: 为什么是 8 s：它要盖住"作者正在看别处"的一眼扫视（通知类 TTL 的经验区间 5–10 s），
#: 又不至于在一条课务还没读完时就暗掉 —— 而且降到低亮度**不是消失**，宁可早暗不可错失。
CHIP_HOT_S = 8.0

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


def ca_timing_inout():
    """CA 的 ease-in-out（指示灯慢灭用）。拿不到 Quartz 就返回 None。"""
    try:
        from Quartz import CAMediaTimingFunction, kCAMediaTimingFunctionEaseInEaseOut
        return CAMediaTimingFunction.functionWithName_(kCAMediaTimingFunctionEaseInEaseOut)
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


def fade_swap(view, duration: float = STATE_S) -> None:
    """接下来这一次「换内容」用淡入淡出（层上的一次性 `CATransition`），不是一下跳过去。

    在**改内容之前**调。没有层 / 拿不到 Quartz / 「减弱动态效果」开着 → 什么都不做（= 直接换）。
    一次性的：只在真的换了时花一次合成，不留常驻动画。
    """
    if reduce_motion():
        return
    try:
        lay = view.layer()
        if lay is None:
            return
        from Quartz import CATransition, kCATransitionFade
        t = CATransition.animation()
        t.setType_(kCATransitionFade)
        t.setDuration_(duration)
        t.setTimingFunction_(ca_timing())
        lay.addAnimation_forKey_(t, "fadeSwap")
    except Exception:                                   # noqa: BLE001
        pass


def fade_property(layer, key: str, duration: float = STATE_S) -> None:
    """层的某个属性做**一次性过渡**：从**当前值**起跑，到随后写进去的那个值。

    ⚠️ 必须在**改值之前**调 —— 先挂动画、再写新值，这正是 Core Animation 的隐式
    动画契约（顶栏胶囊的悬停从 2026-10-14 起就是这么用的，本函数是把那一套收成一处）。
    ⚠️ 起点取 `layer.<key>()`（**模型值**）而不是 `presentationLayer()`：悬停这种
    一次性反馈不会在 `FEEDBACK_S`(0.12s) 里被反复打断，取模型值就够；要「从正在
    显示的位置接着走」的持续态（指示灯那种）自己取 presentationLayer，别用这里。
    ⚠️ `overlay._chip_hover` 自己那三行**刻意不动** —— 它已经跑在生产里、用的是同一个
    `FEEDBACK_S`，改它只有风险没有收益；新调用方（开课面板的课卡 / 落点条）用本函数。
    「减弱动态效果」开着 / 没层 / 拿不到 Quartz → **什么都不做**（= 直接改值）。
    """
    if reduce_motion() or layer is None:
        return
    try:
        from Quartz import CABasicAnimation, CATransaction
        anim = CABasicAnimation.animationWithKeyPath_(key)
        anim.setFromValue_(getattr(layer, key)())
        anim.setDuration_(duration)
        anim.setTimingFunction_(ca_timing())
        layer.addAnimation_forKey_(anim, "fade_" + key)
        CATransaction.flush()      # 立刻提交: 本函数返回后调用方马上写新值
    except Exception:                                   # noqa: BLE001
        pass
