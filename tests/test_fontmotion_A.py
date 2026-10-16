#!/usr/bin/env python3
"""字体/动效「方案 A」七条改动 + 第 7 项（尾部截断）的判据。

    ClassLive.app/Contents/MacOS/python tests/test_fontmotion_A.py

对应 2026-10-16 那份字体/动效报告 §5 方案 A：

* **A1** 等宽数字 `tnum`（`overlay._ui_font`）—— 数字的横向推进宽度必须相等。
* **A3** 描边从「模糊阴影」换成「无模糊描边」（`_make_shadow`）。
* **A4** 草稿让位 `motion.EXIT_S` 0.16 → 0.14。
* **A5** 新行入列时最底那几行额外淡入（`NEW_LINE_FADE_S = MOVE_S * 0.55`）。
* **A6** 开课面板课卡列表一次性错位入场（`entry_panel._stagger_in`）。
* **A7** 模式胶囊切档时整排 frame 过渡比文字淡晚 0.04 s。
* **#7** 定稿/草稿中文装不下档位行数时，从**尾部**截断 + 「…」（只截显示）。

⚠️ 本文件**不跑事件循环**。只有 A5 / A7 那两节要建 `Overlay`（它要 AppKit），
   但 `orderFront` 只在 `show()` 里 —— 和 `test_audit_regressions.R13/R14b` 同一个路子。
   Reduce Motion 一律**打桩**：真机开着它时，A5 的位移支路整条不跑，判据会假绿。
"""
from __future__ import annotations

import math
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import motion                                                       # noqa: E402
import transcript_view as TV                                        # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  {detail}" if detail else ""))


def _width(text: str, font) -> float:
    from AppKit import NSAttributedString, NSFontAttributeName
    return float(NSAttributedString.alloc().initWithString_attributes_(
        text, {NSFontAttributeName: font}).size().width)


# ══════════════════════════════════════════════════════════════════════
# A1 等宽数字（tnum）
# ══════════════════════════════════════════════════════════════════════

def t_a1_tnum() -> None:
    print("\n--- A1 等宽数字（tnum） ---")
    import overlay as O

    for size in (18.0, 11.0, 12.5, 14.0):
        f = O._ui_font(size)
        ws = [_width(d, f) for d in "0123456789"]
        check(f"{size}pt: 十个数字宽度全等（spread=0）",
              max(ws) - min(ws) < 0.02, f"spread={max(ws) - min(ws):.3f}")

    # 现状对照：**不挂 tnum** 的同一个构造器是比例宽度 —— 否则上面那条会假绿。
    from AppKit import NSFont
    plain = NSFont.systemFontOfSize_weight_(18.0, O.WEIGHT)
    check("对照：裸 systemFontOfSize_weight_ 确实是比例宽度",
          abs(_width("1", plain) - _width("0", plain)) > 0.5,
          f"1={_width('1', plain):.3f} 0={_width('0', plain):.3f}")

    # 报告 §2.4 的那一串：数字刷成**等位数**时行宽必须**一模一样**
    # （`MC = 12.5` → `MC = 18.7` 才是"呼吸"；`111.1` 多一位, 天然更宽）。
    f18 = O._ui_font(18.0)
    check("⭐ 「MC = 12.5」与「MC = 18.7」行宽相等（刷数字不再呼吸）",
          abs(_width("MC = 12.5", f18) - _width("MC = 18.7", f18)) < 0.02,
          f"{_width('MC = 12.5', f18):.3f} vs {_width('MC = 18.7', f18):.3f}")
    check("对照：裸字体下这两行差 0.70pt（就是报告 §2.4 那条）",
          abs(_width("MC = 12.5", plain) - _width("MC = 18.7", plain)) > 0.3,
          f"{_width('MC = 12.5', plain):.3f} vs {_width('MC = 18.7', plain):.3f}")

    check("字号透传（18pt 拿到的就是 18pt）", abs(O._ui_font(18.0).pointSize() - 18.0) < 0.01)

    # 「只做收拢」：overlay.py 里 `NSFont.systemFontOfSize_weight_(` **只许剩一处**
    # （工厂自己）。多一处 = 有人又抄了一份没有 tnum 的字体。
    src = pathlib.Path(O.__file__).read_text(encoding="utf-8")
    n = src.count("NSFont.systemFontOfSize_weight_(")
    check("⭐ overlay.py 里该构造器只剩工厂一处（A2 收拢）", n == 1, f"命中 {n} 处")


# ══════════════════════════════════════════════════════════════════════
# A3 描边
# ══════════════════════════════════════════════════════════════════════

def t_a3_stroke() -> None:
    print("\n--- A3 无模糊描边 ---")
    import overlay as O
    sh = O._make_shadow()
    check("blurRadius = 0.0（边缘不再是软的）", abs(float(sh.shadowBlurRadius()) - 0.0) < 1e-6,
          str(sh.shadowBlurRadius()))
    check("黑 α = 0.85", abs(float(sh.shadowColor().alphaComponent()) - 0.85) < 0.01,
          f"{float(sh.shadowColor().alphaComponent()):.3f}")
    dx, dy = sh.shadowOffset()
    check("offset 不变 = (0, -1)", abs(dx) < 1e-6 and abs(dy + 1.0) < 1e-6, f"({dx}, {dy})")


# ══════════════════════════════════════════════════════════════════════
# A4 草稿让位
# ══════════════════════════════════════════════════════════════════════

def t_a4_exit() -> None:
    print("\n--- A4 草稿让位 0.16 → 0.14 ---")
    check("motion.EXIT_S == 0.14", abs(motion.EXIT_S - 0.14) < 1e-9, str(motion.EXIT_S))
    check("离场仍比入场快（EXIT_S < MOVE_S）", motion.EXIT_S < motion.MOVE_S)
    check("与入场恰好 1:2（「让位」语义更强）",
          abs(motion.MOVE_S / motion.EXIT_S - 2.0) < 0.01,
          f"{motion.MOVE_S / motion.EXIT_S:.3f}")


# ══════════════════════════════════════════════════════════════════════
# A5 新行淡入
# ══════════════════════════════════════════════════════════════════════

def t_a5_newline_fade() -> None:
    print("\n--- A5 新行淡入（MOVE_S 的前 55%） ---")
    check("NEW_LINE_FADE_S == MOVE_S * 0.55",
          abs(TV.NEW_LINE_FADE_S - motion.MOVE_S * 0.55) < 1e-9,
          str(TV.NEW_LINE_FADE_S))
    check("起跑时 α = 0", TV.roll_fade_alpha(0.0, 0.0) == 0.0)
    check("到 NEW_LINE_FADE_S 时 α 恰好 = 1（不是 0.999）",
          TV.roll_fade_alpha(0.0, TV.NEW_LINE_FADE_S) == 1.0)
    check("中点 = ease-out(0.5)",
          abs(TV.roll_fade_alpha(0.0, TV.NEW_LINE_FADE_S / 2.0) - motion.ease_out(0.5)) < 1e-9)
    check("淡入时长 < 位移时长（先就位、后落位）", TV.NEW_LINE_FADE_S < motion.MOVE_S)

    # 真机路径：新行出现 → α0 起步；走过 NEW_LINE_FADE_S → α=1 但位移**还没落位**。
    try:
        from overlay import Overlay
    except Exception as e:                                          # noqa: BLE001
        check("Overlay 可构造（AppKit）", False, f"{type(e).__name__}: {e}")
        return
    o = Overlay()
    try:
        now = [100.0]
        o._clock = lambda: now[0]
        o._tv._clock = lambda: now[0]
        o._tv._reduce_motion = lambda: False        # 打桩：真机开着 RM 时这支不跑
        o._tv._animate_ok = lambda: True
        for i in range(4):
            o.finalize(f"line {i}", f"第{i}行")
        now[0] += 1.0
        o._tv.tick()
        check("（前提）之前的动画已走完", not o._tv.rolling)
        o.stream_zh("新的一句")
        o._mark_dirty(urgent=True)
        tv = o._tv
        check("新行出现 → 起跑（rolling）", tv.rolling)
        check("最底那 1 行进入淡入名单，且 α = 0",
              tv._roll_fade_n == 1 and tv._roll_alpha == 0.0,
              f"fade_n={tv._roll_fade_n} α={tv._roll_alpha}")
        now[0] += TV.NEW_LINE_FADE_S
        tv.tick()
        offs = [s["level"] for s in tv._slots if s["level"] is not None and not s["hidden"]]
        check("淡入满了（α=1）而位移仍在走（_roll_off != 0）",
              tv._roll_alpha == 1.0 and tv._roll_off > 0.5,
              f"α={tv._roll_alpha} off={tv._roll_off:.3f} 可见层={offs}")
        now[0] += motion.MOVE_S
        tv.tick()
        check("位移走完后落位（终态 α=1、偏移 0、不再 rolling）",
              not tv.rolling and tv._roll_alpha == 1.0 and tv._roll_off == 0.0)
    finally:
        o.close()


# ══════════════════════════════════════════════════════════════════════
# A6 开课面板课卡错位入场
# ══════════════════════════════════════════════════════════════════════

class _FakeLayer:
    def __init__(self) -> None:
        self.keys: list[str] = []

    def addAnimation_forKey_(self, anim, key):                      # noqa: N802
        self.keys.append(str(key))


class _FakeView:
    def __init__(self) -> None:
        self.alpha = 1.0
        self.wants = False
        self.lay = _FakeLayer()

    def setAlphaValue_(self, a):                                    # noqa: N802
        self.alpha = float(a)

    def setWantsLayer_(self, b):                                    # noqa: N802
        self.wants = bool(b)

    def layer(self):
        return self.lay

    def superview(self):
        return None


def t_a6_card_stagger() -> None:
    print("\n--- A6 课卡错位入场 ---")
    import entry_panel as E
    check("motion.ENTER_STAGGER_S == 0.035（唯一真源在 motion.py）",
          abs(motion.ENTER_STAGGER_S - 0.035) < 1e-9, str(motion.ENTER_STAGGER_S))
    tl = E.entrance_timeline(3)
    check("时间线：每卡错位 35ms、整排 +6px 起步",
          tl == [(0.0, E.CARD_ENTER_DY), (0.035, E.CARD_ENTER_DY), (0.07, E.CARD_ENTER_DY)],
          str(tl))
    check("0 张卡 → 空表（不会凭空多一次入场）", E.entrance_timeline(0) == [])

    real = motion.reduce_motion
    try:
        motion.reduce_motion = lambda: True                          # 打桩：开着 Reduce Motion
        v = _FakeView()
        E._stagger_in([v])
        check("Reduce Motion：直接落位（α=1、零动画）",
              v.alpha == 1.0 and v.lay.keys == [], str(v.lay.keys))

        motion.reduce_motion = lambda: False
        v2 = _FakeView()
        E._stagger_in([v2])
        check("正常：每卡两条一次性动画（opacity + 位移）",
              sorted(v2.lay.keys) == ["enter_opacity", "enter_transform.translation.y"],
              str(v2.lay.keys))
        check("空列表 / 全是 None 不炸", E._stagger_in([]) is None and E._stagger_in([None]) is None)
    finally:
        motion.reduce_motion = real


# ══════════════════════════════════════════════════════════════════════
# A7 胶囊切档：frame 比文字晚 0.04s
# ══════════════════════════════════════════════════════════════════════

def t_a7_chip_delay() -> None:
    print("\n--- A7 胶囊 frame 过渡晚 0.04s ---")
    import overlay as O
    from AppKit import NSMakeRect
    check("CHIP_FRAME_DELAY_S == 0.04", abs(O.CHIP_FRAME_DELAY_S - 0.04) < 1e-9,
          str(O.CHIP_FRAME_DELAY_S))
    check("延迟 < 过渡时长（否则是两段动画不是错开）",
          O.CHIP_FRAME_DELAY_S < O.MODE_CHIP_ANIM_S)

    try:
        from overlay import Overlay
    except Exception as e:                                          # noqa: BLE001
        check("Overlay 可构造（AppKit）", False, f"{type(e).__name__}: {e}")
        return
    o = Overlay()
    real_rm = O._reduce_motion
    try:
        O._reduce_motion = lambda: False
        b = o._btn_trans
        old = NSMakeRect(100.0, 330.0, 80.0, O.MODE_CHIP_H)
        new = NSMakeRect(90.0, 330.0, 96.0, O.MODE_CHIP_H)
        b.setFrame_(old)
        frames = [(b, new)]
        o._chip_anim = True
        o._place_bar(frames, old)
        check("切档瞬间：frame **没有**立刻落位（推迟到 40ms 后）",
              abs(b.frame().size.width - 80.0) < 0.5, f"w={b.frame().size.width:.1f}")

        gen = o._chip_frame_gen
        o._chip_frames_now(frames, gen)
        check("到点后落位到新宽度", abs(b.frame().size.width - 96.0) < 0.5,
              f"w={b.frame().size.width:.1f}")

        # 40ms 内又切一次档 → 上一批到点的回调必须作废（否则会把位置打回去）。
        stale = gen
        o._chip_anim = True
        o._place_bar(frames, old)
        b.setFrame_(NSMakeRect(70.0, 330.0, 110.0, O.MODE_CHIP_H))
        o._chip_frames_now(frames, stale)
        check("⭐ 过时的批次到点后作废（不把新位置打回去）",
              abs(b.frame().size.width - 110.0) < 0.5, f"w={b.frame().size.width:.1f}")

        # Reduce Motion：不推迟、直接落位。
        O._reduce_motion = lambda: True
        b.setFrame_(old)
        o._chip_anim = True
        o._place_bar([(b, new)], old)
        check("Reduce Motion：同步落位（不推迟）",
              abs(b.frame().size.width - 96.0) < 0.5, f"w={b.frame().size.width:.1f}")
    finally:
        O._reduce_motion = real_rm
        o.close()


# ══════════════════════════════════════════════════════════════════════
# #7 译文超限：尾部截断 + 「…」（只截显示）
# ══════════════════════════════════════════════════════════════════════

def _fake_measure(per_line: int = 10, line_h: float = 20.0):
    """假尺子：每行 10 个字符。让 `truncate_to_height` 的判据不依赖 AppKit。"""
    def m(s: str) -> float:
        return (math.ceil(len(s) / per_line) if s else 0) * line_h
    return m


def t_7_truncate() -> None:
    print("\n--- #7 译文尾部截断（纯函数） ---")
    m = _fake_measure()
    short = "短译文"
    r = TV.truncate_to_height(short, m, 40.0)
    check("短串原样返回（还是同一个对象）", r is short, repr(r))
    check("空串原样（0 行，不补省略号）", TV.truncate_to_height("", m, 40.0) == "")

    long = "字" * 100
    out = TV.truncate_to_height(long, m, 40.0)
    check("超长串以「…」结尾", out.endswith(TV.ELLIPSIS), repr(out[-6:]))
    check("⭐ 截到档位行数内（高度 ≤ max_h）", m(out) <= 40.0 + 0.5,
          f"len={len(out)} h={m(out)}")
    check("确实丢掉了内容（不是端上整段）", len(out) < len(long), f"{len(out)} < {len(long)}")
    check("⭐ 原文对象未被改（返回的是新串）", long == "字" * 100 and out is not long)
    check("连后缀都放不下时只留后缀", TV.truncate_to_height(long, m, 5.0) == TV.ELLIPSIS)


def t_7_display_only() -> None:
    print("\n--- #7 只截显示：原文一字不动 ---")
    try:
        from overlay import Overlay
    except Exception as e:                                          # noqa: BLE001
        check("Overlay 可构造（AppKit）", False, f"{type(e).__name__}: {e}")
        return
    import overlay as O
    o = Overlay()
    try:
        o._width = O.MIN_WIDTH
        o._apply_row_metrics(O.MIN_WIDTH)
        o.finalize("short english", "短译文。")
        for _ in range(3):
            o.pump()
        n0, s0 = o._tv._overflow_n, o._tv._zh_seen_n

        long_zh = "这是一句非常长的中文译文，" * 20
        o.finalize("long english", long_zh)
        for _ in range(3):
            o.pump()

        check("超长译文仍被记账（观测口径没被截断顶掉）",
              o._tv._overflow_n > n0 and o._tv._zh_seen_n > s0,
              f"overflow={o._tv._overflow_n} seen={o._tv._zh_seen_n}")

        check("⭐ 原文对象未被改（sessions/ · livemetrics · writer 拿到的是它）",
              o._tv._items[-1][1] == long_zh,
              f"len={len(o._tv._items[-1][1])}")

        shown = [s["zh"].stringValue() for s in o._tv._slots
                 if s["zh"].stringValue()]
        assert shown, "用例前提：屏上应当有行"
        o.long = max(shown, key=len)
        trunc = [t for t in shown if t.endswith(TV.ELLIPSIS)]
        check("⭐ 屏上那条超长译文以「…」结尾", bool(trunc),
              (trunc[0][-8:] if trunc else o.long[-8:]))
        if trunc:
            t = trunc[0]
            lbl = next(s["zh"] for s in o._tv._slots if s["zh"].stringValue() == t)
            h = O._measure_text_h(t, lbl.frame().size.width, lbl.font())
            check("⭐ 显示高度不超过档位行数（不再静默裁掉）",
                  h <= o._tv._zh_h + 0.5, f"h={h:.1f} 档位盒高={o._tv._zh_h:.1f}")
        check("短的那句原样显示（没被无端截）",
              any(t == "短译文。" for t in shown))
    finally:
        o.close()


def main() -> int:
    print("字体/动效方案 A（A1/A3/A4/A5/A6/A7 + #7）")
    for fn in (t_a1_tnum, t_a3_stroke, t_a4_exit, t_a5_newline_fade,
               t_a6_card_stagger, t_a7_chip_delay, t_7_truncate, t_7_display_only):
        try:
            fn()
        except Exception as e:                                      # noqa: BLE001
            import traceback
            traceback.print_exc()
            check(f"{fn.__name__} 抛出异常", False, f"{type(e).__name__}: {e}")
    bad = [n for n, ok, _ in RESULTS if not ok]
    print(f"\n{'❌ ' + str(len(bad)) + ' 条不通过: ' + ', '.join(bad) if bad else '✅ 全部通过'}"
          f"（{len(RESULTS)} 条）")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
