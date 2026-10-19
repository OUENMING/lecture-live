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
**字幕永远不用弹簧、不用过冲**(`PLAN-roll-motion.md` §7)。
⚠️ 2026-10-18 补充: 弹簧/过冲**只**用于按钮的按压反馈(Claude Desktop 那套值,
见下面 PRESS_* / SPRING_STOPS), 字幕的滚动、入列、让位一律仍是 ease-out-cubic ——
两条纪律不冲突: 字幕要的是「尽快就位然后静止」, 按钮要的是「按下去有弹性」。
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
#: ⭐ 一些事儿卡片上点完「记下 / 忽略」后，**处理结果停留几秒**再自动翻到下一条（没有就收起）。
#: 2026-10-19 作者报：3.10 点完卡片关不掉、小卡也不缩。停留这几秒是给「撤销」和
#: 「已记入提醒事项」那句回执一个看得见的窗口；写提醒事项**失败**时不自动走（要让人看见原因）。
DUTY_DONE_HOLD_S = 3.0

# ── Claude Desktop 设计系统的三组动效值（open-claude-ui cds-theme.css / docs v1-01, 2026-10-18）──
# 三组是一套、一起用：按钮按压弹簧（PRESS_*/SPRING_STOPS）· 静默三点（DOTS_*）·
# 状态词交叉淡换（SWAP_*）。`reduce_motion()` 为真时三组全跳过（原样设字 / 不缩不弹）。
PRESS_S = 0.06            # --cds-dur-fast：按下缩到 PRESS_SCALE 的时长
RELEASE_SPRING_S = 0.45   # --cds-dur-slow：抬起回弹的时长
PRESS_SCALE = 0.975       # 按下缩到 97.5%
#: 抬起的回弹曲线：CSS `linear()` 缓动的进度采样（等间隔时刻上的进度值，首 0 尾 1）。
#: 过冲 9.15% —— 作用在 0.025 的行程上只有 ~0.0023（很小，肉眼看是「轻轻一弹」）。
SPRING_STOPS = (0.0, .2459, .6526, .9468, 1.0764, 1.0915, 1.0585, 1.0219,
                .9993, .9914, .9921, .9957, .9988, 1.0004, 1.0)
DOTS_PERIOD_S = 1.9       # 一颗点一轮的周期
DOTS_DELAY_S = 0.15       # 相邻两颗点的相位差（整排读起来是一道波）
DOTS_D = 3.5              # 点直径
DOTS_GAP = 3.0            # 点与点之间
DOTS_TEXT_GAP = 5.0       # 状态词与第一颗点之间
SWAP_OUT_S = 0.20         # --cds-dur-base：旧词淡出 + 上移的时长
SWAP_IN_S = 0.30          # 新词淡入 + 从下方升起的时长（与旧词**重叠**，是交叉淡换）

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


# ══════════════════════════════════════════════════════════════════════
# 按压反馈（按钮）—— 唯一入口是 `panel.make_button` 造的按钮子类
# ══════════════════════════════════════════════════════════════════════
def _cubic(x1: float, y1: float, x2: float, y2: float):
    """CSS `cubic-bezier(x1,y1,x2,y2)` → `CAMediaTimingFunction`；拿不到 Quartz 返回 None。"""
    try:
        from Quartz import CAMediaTimingFunction
        # ⚠️ 四个下划线是 PyObjC 对 `functionWithControlPoints::::` 的取名法（实测）。
        return CAMediaTimingFunction.functionWithControlPoints____(x1, y1, x2, y2)
    except Exception:                                   # noqa: BLE001
        return None


def _center_scale(view, s: float):
    """绕**视图中心**缩放的层变换 `T(c)·S·T(−c)`。

    ⚠️ 刻意**不动 `anchorPoint`**（AppKit 给 view 层的锚点是 (0,0)）—— 改了它，
       之后任何一次 layout/`setFrame_` 都可能把按钮挪半个身位（AppKit 按锚点
       换算 position 的前提就是 (0,0)）。这个变换的**不动点 = bounds 中心**，
       与锚点无关（锚点 (0,0) 时固定点正好是 c，实测 m41/m42 把中心钉住）。
    """
    from Quartz import (CATransform3DIdentity, CATransform3DScale,
                        CATransform3DTranslate)
    b = view.bounds().size
    cx, cy = float(b.width) / 2.0, float(b.height) / 2.0
    t = CATransform3DIdentity
    t = CATransform3DTranslate(t, cx, cy, 0.0)
    t = CATransform3DScale(t, s, s, 1.0)
    t = CATransform3DTranslate(t, -cx, -cy, 0.0)
    return t


def _press_layer(view):
    """按钮的层（没有就补一个）。拿不到返回 None。"""
    lay = view.layer()
    if lay is None:
        view.setWantsLayer_(True)
        lay = view.layer()
    return lay


def _transform_value(t):
    """`CATransform3D` 结构体 → `NSValue`。

    ⚠️⚠️ **必须包**：裸结构体交给 CA 动画（`setFromValue_` / `setValues_`）时，
    PyObjC 不报错、单元测试也不炸 —— 但动画一提交（display link 的隐式事务）就抛
    ObjC 异常，`NSApplication _crashOnException:` **当场 SIGTRAP 掉整个进程**
    （2026-10-18 探针实测：`EXC_BREAKPOINT`，栈顶是 `NS_setFlushesWithDisplayLink`）。
    """
    try:
        from Foundation import NSValue
        return NSValue.valueWithCATransform3D_(t)
    except Exception:                                   # noqa: BLE001
        return t


def press_down(view) -> None:
    """按下反馈：缩到 `PRESS_SCALE`（`PRESS_S`，ease-out-cubic）。**失败静默**。

    ⚠️ 只有 `panel.make_button` 造的按钮子类在 `mouseDown_` 里调它 —— 业务代码别直接调。
    """
    if reduce_motion():
        return
    try:
        lay = _press_layer(view)
        if lay is None:
            return
        from Quartz import CABasicAnimation
        anim = CABasicAnimation.animationWithKeyPath_("transform")
        anim.setFromValue_(_transform_value(lay.transform()))
        anim.setToValue_(_transform_value(_center_scale(view, PRESS_SCALE)))
        anim.setDuration_(PRESS_S)
        anim.setTimingFunction_(ca_timing())
        lay.removeAnimationForKey_("press")
        lay.addAnimation_forKey_(anim, "press")
        lay.setTransform_(_center_scale(view, PRESS_SCALE))
    except Exception:                                   # noqa: BLE001
        pass


def press_up(view) -> None:
    """抬起反馈：从**正在显示的位置**按 `SPRING_STOPS` 回弹到 1（`RELEASE_SPRING_S`）。

    ⚠️ 起点取 `presentationLayer()` 的当前缩放 —— 按下不足 `PRESS_S` 就被抬起时
       从当前位置接着走（不跳回 PRESS_SCALE）；拿不到就退到 PRESS_SCALE。
    """
    if reduce_motion():
        return
    try:
        lay = _press_layer(view)
        if lay is None:
            return
        from Quartz import CAKeyframeAnimation
        start = PRESS_SCALE
        pres = lay.presentationLayer()
        if pres is not None:
            try:
                start = float(pres.transform().m11)     # 均匀缩放: m11 = scale
            except Exception:                           # noqa: BLE001
                pass
        start = min(1.0, max(PRESS_SCALE, start))
        anim = CAKeyframeAnimation.animationWithKeyPath_("transform")
        anim.setValues_([_transform_value(_center_scale(view, s))
                         for s in spring_scales(start)])
        anim.setDuration_(RELEASE_SPRING_S)
        anim.setCalculationMode_("linear")   # 相邻 stop 之间线性 —— CSS `linear()` 的语义
        lay.removeAnimationForKey_("press")
        lay.addAnimation_forKey_(anim, "press")
        lay.setTransform_(_center_scale(view, 1.0))     # 模型值回 1（= 恒等）
    except Exception:                                   # noqa: BLE001
        pass


# ══════════════════════════════════════════════════════════════════════
# 状态词：交叉淡换 + 「还在跑」的三颗静默点
# ══════════════════════════════════════════════════════════════════════
def is_running(text: str) -> bool:
    """这条状态词是「**还在跑**」吗。**纯函数**。

    判据 = 尾部「…」：本仓状态词的约定（2026-10-18 全量清点）—— 进行中的一律以
    「…」结尾（「下载中…」「正在生成笔记…」「搜「x」…」），做完 / 出错的不写
    （「已经是最新的了」「⚠ 没删：…」）。⚠️ 只影响**上屏那一步**：`ready_short`
    这类纯函数照旧返回带「…」的原串（判据钉的是它们，一个字不动）。
    """
    return str(text or "").rstrip().endswith("…")


def status_render(text: str, *, running=None, reduce=False) -> tuple:
    """状态词 → `(上屏文字, 要不要挂三颗点)`。**纯函数**。

    · 还在跑（`running=None` 时按 `is_running` 自动判）：**去掉尾部那一个「…」** ——
      点由渲染层挂上，不再是静态省略号；
    · 跑完 / 出错：原样、无点；
    · `reduce=True`（「减弱动态效果」）：原样带「…」、无点 —— 静态就够（HIG）。
    """
    if running is None:
        running = is_running(text)
    t = str(text or "")
    if not running or reduce:
        return (t, False)
    t = t.rstrip()
    if t.endswith("…"):                     # ⚠️ 只裁**那一个省略号**，不是无脑 `[:-1]`
        t = t[:-1].rstrip()
    return (t, True)


def spring_scales(start: float = PRESS_SCALE) -> tuple:
    """回弹的 scale 关键帧：从 `start` 出发按 `SPRING_STOPS` 的进度回到 1。**纯函数**。

    终点**精确等于 1.0**（`SPRING_STOPS[-1] == 1`）；`start=PRESS_SCALE` 时峰值
    ≈ 1.0023 —— 0.025 行程的 9.15%，肉眼只是「轻轻一弹」。
    """
    return tuple(start + (1.0 - start) * p for p in SPRING_STOPS)


def _text_width(text: str, font) -> float:
    """一段文字在给定字体下的宽度（AppKit 量，量不到退回粗估）。"""
    try:
        from AppKit import NSAttributedString, NSFontAttributeName
        return float(NSAttributedString.alloc().initWithString_attributes_(
            text, {NSFontAttributeName: font}).size().width)
    except Exception:                                   # noqa: BLE001
        try:
            return 0.6 * float(font.pointSize()) * len(text)
        except Exception:                               # noqa: BLE001
            return 0.0


def status_width(text: str, font, *, running=None) -> float:
    """这条状态词**上屏后**占的宽（文字 + 三颗点的占位）。**纯测宽，无副作用。**

    给自撑宽的容器用（就绪胶囊 `_refresh_chip`：它按文字宽算按钮宽）。
    `running` 与 `status_render` 同义 —— 胶囊裁过字的那些要显式传 False（见那里的说明）。
    """
    display, dots_on = status_render(text, running=running, reduce=reduce_motion())
    w = _text_width(display, font)
    if dots_on:
        w += DOTS_TEXT_GAP + 3 * DOTS_D + 2 * DOTS_GAP
    return w


def _view_text(view) -> str:
    """控件当前显示的文字。⚠️ `NSButton` 走 `title()` —— 它的 `stringValue()` 是
    **开关状态**（"0"/"1"），不是标题（PyObjC 实测：`setStringValue_` 后 `title()` 纹丝不动）。"""
    try:
        if hasattr(view, "title"):
            return str(view.title() or "")
    except Exception:                                   # noqa: BLE001
        pass
    try:
        return str(view.stringValue() or "")
    except Exception:                                   # noqa: BLE001
        return ""


def _set_view_text(view, text: str) -> None:
    if hasattr(view, "title"):
        view.setTitle_(text)
    else:
        view.setStringValue_(text)


def dots_reserve() -> float:
    """三颗点 + 它前面那段空隙占的宽 —— 自撑宽容器（就绪胶囊）排布时的预算。"""
    return DOTS_TEXT_GAP + 3 * DOTS_D + 2 * DOTS_GAP


def _dots_state(view) -> bool:
    return bool(_get_attr(view, _DOTS_KEY))


#: 挂在控件上的两块状态（点层列表 / 换字替身的标识串）。⚠️ 用 **associated object**
#: 而不是 `view._motion_dots = …`：纯 ObjC 实例（`NSTextField` 标签）**不接受任意 Python
#: 属性**（`AttributeError: 'NSTextField' object has no attribute …`，本仓 2026-09-28 卡片那条同款坑）。
#: associated object 跟着对象一起释放，不留引用。
#: ⚠️⚠️ **替身只存标识串、不存替身对象本身**：存对象会造出
#: `label → assoc → 替身 → superview → 面板树 → label` 的环，而**环里有 PyObjC 实例
#: 时 `gc` 收不回**（`tests/test_panel.py` ⑧ 的存活对象判据当场抓到：+1.5 个/轮）。
#: 替身由父视图持有（正常的 subview 所有权），按 `identifier` 找回来。
_DOTS_KEY = "classlive.motion.dots"
_GHOST_KEY = "classlive.motion.ghost"
_GHOST_ID = "classlive.motion.ghost"


def _get_attr(view, key):
    try:
        import objc
        return objc.getAssociatedObject(view, key)
    except Exception:                                   # noqa: BLE001
        return None


def _set_attr(view, key, value) -> None:
    try:
        import objc
        objc.setAssociatedObject(view, key, value, objc.OBJC_ASSOCIATION_RETAIN)
    except Exception:                                   # noqa: BLE001
        pass


def _remove_dots(view) -> None:
    for lay in (_get_attr(view, _DOTS_KEY) or []):
        try:
            lay.removeFromSuperlayer()
        except Exception:                               # noqa: BLE001
            pass
    _set_attr(view, _DOTS_KEY, None)


def _dots_at(view, display: str) -> None:
    """把三颗点摆到 `display` 后面并起转（层直接挂在 label 的层上 —— 跟着 label 走）。"""
    from Quartz import CAKeyframeAnimation, CALayer, CACurrentMediaTime
    lay = _press_layer(view)
    if lay is None:
        return
    w = float(view.bounds().size.width)
    h = float(view.bounds().size.height)
    tw = _text_width(display, view.font())
    try:
        align = int(view.alignment())                  # NSControl.alignment：0 左 / 1 中 / 2 右
    except Exception:                                   # noqa: BLE001
        align = 0
    x0 = (w - tw) / 2.0 if align == 1 else ((w - tw) if align == 2 else 0.0)
    x = x0 + tw + DOTS_TEXT_GAP
    y = (h - DOTS_D) / 2.0
    try:
        color = view.textColor()
    except Exception:                                   # noqa: BLE001
        try:
            color = view.contentTintColor()
        except Exception:                               # noqa: BLE001
            color = None
    try:
        from AppKit import NSColor
        color = color or NSColor.whiteColor()
        cg = color.CGColor()
    except Exception:                                   # noqa: BLE001
        return
    ease = _cubic(0.4, 0.0, 0.2, 1.0)                  # cubic-bezier(.4,0,.2,1)
    t0 = CACurrentMediaTime()
    layers = []
    for i in range(3):
        dot = CALayer.layer()
        dot.setFrame_(((x + i * (DOTS_D + DOTS_GAP), y), (DOTS_D, DOTS_D)))
        dot.setCornerRadius_(DOTS_D / 2.0)
        dot.setBackgroundColor_(cg)
        dot.setOpacity_(0.0)                           # 起转前的模型值 = 不可见（否则会闪一下）
        op = CAKeyframeAnimation.animationWithKeyPath_("opacity")
        op.setValues_([0.0, 0.0, 1.0, 1.0, 1.0, 0.0, 0.0])
        op.setKeyTimes_([0.0, 0.08, 0.22, 0.36, 0.70, 0.90, 1.0])
        op.setTimingFunctions_([ease] * 6)
        op.setDuration_(DOTS_PERIOD_S)
        op.setRepeatCount_(float("inf"))
        op.setBeginTime_(t0 + i * DOTS_DELAY_S)
        ty = CAKeyframeAnimation.animationWithKeyPath_("transform.translation.y")
        ty.setValues_([2.0, 2.0, -2.5, 0.0, 0.0, 0.0, 0.0])
        ty.setKeyTimes_([0.0, 0.08, 0.22, 0.36, 0.70, 0.90, 1.0])
        ty.setTimingFunctions_([ease] * 6)
        ty.setDuration_(DOTS_PERIOD_S)
        ty.setRepeatCount_(float("inf"))
        ty.setBeginTime_(t0 + i * DOTS_DELAY_S)
        dot.addAnimation_forKey_(op, "dots.op")
        dot.addAnimation_forKey_(ty, "dots.y")
        lay.addSublayer_(dot)
        layers.append(dot)
    _set_attr(view, _DOTS_KEY, layers)


_GHOST_N = 0


def _ghost_id(view) -> str:
    """给这次换字造一个替身标识（按视图区分，替身按 `identifier` 找回来）。"""
    global _GHOST_N
    _GHOST_N += 1
    return f"{_GHOST_ID}.{_GHOST_N:x}"


def _remove_ghost(view) -> None:
    """撤掉这个视图上还没退场的替身（按标识找，不持有替身对象）。"""
    gid = _get_attr(view, _GHOST_KEY)
    if not gid:
        return
    _set_attr(view, _GHOST_KEY, None)
    try:
        sv = view.superview()
        for sub in (sv.subviews() if sv is not None else []):
            try:
                if sub.identifier() == gid:
                    sub.removeFromSuperview()
            except Exception:                           # noqa: BLE001
                continue
    except Exception:                                   # noqa: BLE001
        pass


def _make_ghost(view, text: str):
    """旧词的替身（同位置、同字体/颜色/对齐的不可交互 `NSTextField`）。"""
    from AppKit import NSTextField
    g = NSTextField.alloc().initWithFrame_(view.frame())
    g.setStringValue_(text)
    g.setEditable_(False)
    g.setSelectable_(False)
    g.setBezeled_(False)
    g.setDrawsBackground_(False)
    for setter, getter in (("setFont_", "font"), ("setAlignment_", "alignment")):
        try:
            getattr(g, setter)(getattr(view, getter)())
        except Exception:                               # noqa: BLE001
            pass
    try:
        g.setTextColor_(view.textColor())               # NSTextField
    except Exception:                                   # noqa: BLE001
        try:
            g.setTextColor_(view.contentTintColor())    # NSButton（胶囊）
        except Exception:                               # noqa: BLE001
            pass
    try:
        g.setMaximumNumberOfLines_(view.maximumNumberOfLines())
    except Exception:                                   # noqa: BLE001
        pass
    try:
        g.cell().setLineBreakMode_(view.cell().lineBreakMode())
    except Exception:                                   # noqa: BLE001
        pass
    g.setWantsLayer_(True)
    return g


def _basic(key: str, frm: float, to: float, dur: float, timing):
    """一条 `CABasicAnimation`；拿不到 Quartz 返回 None。"""
    try:
        from Quartz import CABasicAnimation
        a = CABasicAnimation.animationWithKeyPath_(key)
        a.setFromValue_(frm)
        a.setToValue_(to)
        a.setDuration_(dur)
        if timing is not None:
            a.setTimingFunction_(timing)
        return a
    except Exception:                                   # noqa: BLE001
        return None


def _swap_text(view, display: str) -> None:
    """交叉淡换：旧词上移淡出（`SWAP_OUT_S`），新词从下方 6pt 淡入（`SWAP_IN_S`）。

    ⚠️ 旧词是**替身视图**（同位置的 NSTextField）—— `NSTextField` 一个控件只有一份
       文字，想「两个词同时在屏上」就得有第二个视图。替身活 `SWAP_OUT_S + 50ms`
       就被撤掉（`AppHelper.callLater`，不走 `CATransaction` 完成块 —— 见下面的实测）。
    ⚠️ 替身的移出是**向上** 4pt：本 app 的面板里 label 的父视图都不是 flipped
       （`ve` / 落点条 / 卡片，全部 y 朝上），所以 +y = 向上。换了 flipped 的父视图
       要跟着翻符号。
    """
    _remove_ghost(view)
    old = _view_text(view)
    if old:
        superview = view.superview()
        if superview is not None:
            ghost = _make_ghost(view, old)
            gid = _ghost_id(view)
            try:
                ghost.setIdentifier_(gid)
            except Exception:                           # noqa: BLE001
                gid = ""
            superview.addSubview_(ghost)
            _set_attr(view, _GHOST_KEY, gid)
            glay = ghost.layer()
            if glay is not None and gid:
                # ⚠️ 不用显式 `CATransaction`（begin 了就必须 commit —— 2026-10-18 实测：
                #    漏掉 commit 会让整个面板**再也不渲染**，而窗口 `isVisible()` 还是 True）。
                glay.addAnimation_forKey_(
                    _basic("opacity", 1.0, 0.0, SWAP_OUT_S, _cubic(0.165, 0.84, 0.44, 1.0)),
                    "swap.out.op")
                glay.addAnimation_forKey_(
                    _basic("transform.translation.y", 0.0, 4.0, SWAP_OUT_S,
                           _cubic(0.165, 0.84, 0.44, 1.0)),
                    "swap.out.ty")
                glay.setOpacity_(0.0)   # 模型值停在「不可见」：退场走完就不会回闪
                # ⚠️⚠️ **不要用 `CATransaction.setCompletionBlock_`** —— 实测它
                #    **在 commit 那一刻就触发**（不是动画走完），替身会被当场撤掉、
                #    交叉淡换整个变成「新词直接出现」（2026-10-18 探针抓到）。
                #    用 `AppHelper.callLater`（本仓 `_later` 同款）：只捕获**替身自己**，
                #    不捕获父视图/label（否则 headless 里定时器不触发 = 按住整棵面板树，
                #    `test_panel.py` ⑧ 的存活对象判据当场红）。
                def _drop():
                    try:
                        ghost.removeFromSuperview()
                    except Exception:                   # noqa: BLE001
                        pass
                try:
                    from PyObjCTools import AppHelper
                    AppHelper.callLater(SWAP_OUT_S + 0.05, _drop)
                except Exception:                       # noqa: BLE001
                    pass
    _set_view_text(view, display)
    lay = _press_layer(view)
    if lay is None:
        return
    lay.addAnimation_forKey_(
        _basic("opacity", 0.0, 1.0, SWAP_IN_S, _cubic(0.3, 0.7, 0.4, 1.0)), "swap.in.op")
    lay.addAnimation_forKey_(
        _basic("transform.translation.y", -6.0, 0.0, SWAP_IN_S, _cubic(0.3, 0.7, 0.4, 1.0)),
        "swap.in.ty")
    lay.setOpacity_(1.0)     # 模型值停在「可见」；⚠️ 不碰 transform（按钮可能正被按着）


def render_status(view, text: str, *, running=None) -> None:
    """状态词的**唯一渲染出口**：交叉淡换 + 「还在跑」时挂三颗静默点。**失败静默**。

    · `view`：`NSTextField` 标签或 `NSButton`（就绪胶囊）—— 只动文字与层；
    · 换的字与现在**一样**时什么都不做（不重放动画、不重启点）；
    · 连续换字：上一次没退场的替身与在飞的动画**先撤再上新**（不叠）；
    · `reduce_motion()` 为真：原样设字（**保留「…」**）、不换字动画、不挂点。
    · `running`：`None` = 按尾部「…」自动判（`is_running`）；`False` = 这条虽以「…」
      结尾但**不是**在跑（胶囊里被裁出来的省略号就是这种，见 `_refresh_chip`）。
    """
    try:
        reduce = reduce_motion()
        display, dots_on = status_render(text, running=running, reduce=reduce)
        old = _view_text(view)
        if old == display and _dots_state(view) == dots_on:
            return
        if reduce:
            _remove_ghost(view)
            _remove_dots(view)
            _set_view_text(view, display)
            return
        _swap_text(view, display)
        _remove_dots(view)
        if dots_on:
            _dots_at(view, display)
    except Exception:                                   # noqa: BLE001
        pass
