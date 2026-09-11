"""Phase 0 闸门 + 验收探针: 真实触控板滚轮的两个关键行为。

**闸门(已通过 2026-09-11)**: 真实双指滚动能到达未激活的 NonactivatingPanel
并真正移动视口 —— 原方案成立, 不需要降级阶梯。

**本脚本额外验两件事**(都只能真机测, 合成事件测不出来):
  ① 收起态: 滚轮必须被**吞掉**(面板纹丝不动)。这是 99% 的使用状态,
     平时滚轮若无效应, 上课时面板会被滚走。
  ② 展开态: 滚上去后右上角**出现 ↓ 最新**, 滚回底部后**消失**。
     (跟随状态机用真实事件走一遍, 之前只用程序化滚动验过。)

**帧率诊断**(2026-09-11 加): 光看"帧间隔"会被用户停手污染 —— 抬手那几秒没有
位移, 会被算成"掉了 3 秒的帧"。所以这里把**滚轮事件时刻**也记下来, 只统计
「间隔期间确有事件到达」的那些间隔, 那才是真正的处理卡顿。同时报输入节奏
(触控板交付频率)与画面节奏(我们更新频率)的对比 —— 两者贴近 = 我们跟得上。

用法: .venv/bin/python3 probe_scroll.py
      按屏幕提示, 在每一阶段把鼠标移到悬浮窗上双指滚动。阶段会自动前进。
"""
import bisect
import pathlib
import sys
import time
import objc

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from AppKit import NSTextField, NSFont, NSColor

from overlay import Overlay

SENTS = [
    ("The Byzantine Empire survived for another thousand years after the west fell.",
     "西罗马陷落后，拜占庭帝国又存续了一千年，这说明制度延续的力量。"),
    ("Economists use regression to find relationships between variables.",
     "经济学家用回归分析来寻找变量之间的关系，但相关不等于因果。"),
    ("The readings for this module are on Brightspace and the deadline is Friday.",
     "这个模块的阅读材料在 Brightspace 上，截止日期是下周五午夜。"),
    ("Constrained optimization is the workhorse of modern microeconomics.",
     "约束优化是现代微观经济学的核心工具，用来求解有限资源下的最优决策。"),
    ("The Catholic Church preserved much of the Roman administrative legacy.",
     "天主教教会在很大程度上保留了罗马的行政遗产与拉丁语文传统。"),
]
PROBE_N = 60

PHASES = [
    (0.0, 16.0, True,  "① 收起态：双指滚动 -> 面板应【纹丝不动】"),
    (16.0, 40.0, False, "② 展开态：滚动 -> 应能【自由滚动】；滚上去后右上角出现 ↓ 最新"),
]


def _pct(xs, p):
    if not xs:
        return float('nan')
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p))]


def analyze(moves, events, pumps, ta, tb, pause_s=0.05):
    """moves: [(t, origin_y)]; events: [t] 滚轮事件时刻; pumps: [(t, ms)] 每轮 pump 耗时。

    ⚠️ **别再用"画面更新间隔"当帧率指标** —— 它会与两类**非卡顿**混淆:
      ① 用户抬手(已由停手边界剔除);
      ② **滚到顶/底的钳制**: origin 被 clamp, 事件照来但视口不动, 于是两次
         "位移"之间隔得很久 —— 看着像掉了半秒的帧, 其实一动不动是**正确**的。
    所以帧率主指标改用 **pump() 自身耗时**: 它是"我们这轮做了多久"的直接量,
    与视口能否移动无关。位移间隔只作为辅助信息(且注明钳制可能)。
    """
    ev = [t for t in events if ta <= t <= tb]
    mv = [t for (t, _) in moves if ta <= t <= tb]
    pm = [ms for (t, ms) in pumps if ta <= t <= tb]
    ei = [ev[i + 1] - ev[i] for i in range(len(ev) - 1)]
    bounds = {ev[i + 1] for i in range(len(ev) - 1) if ev[i + 1] - ev[i] > pause_s}
    mi_all = [mv[i + 1] - mv[i] for i in range(len(mv) - 1)]
    real, idle = [], []
    for i, d in enumerate(mi_all):
        lo, hi = mv[i], mv[i + 1]
        j0 = bisect.bisect_right(ev, lo)
        j1 = bisect.bisect_right(ev, hi)
        hit = ev[j0:j1]
        active = bool(hit) and not any(t in bounds for t in hit)
        (real if active else idle).append(d)
    med = _pct(real, 0.5)
    worst = _pct(pm, 0.99)
    return {
        "n_ev": len(ev), "n_mv": len(mv), "n_pump": len(pm),
        "in_med": _pct(ei, 0.5), "in_p90": _pct(ei, 0.9),
        "p50": _pct(pm, 0.5), "p90": _pct(pm, 0.9),
        "p99": worst, "pmax": max(pm) if pm else 0.0,
        "slow120": sum(1 for v in pm if v > 8.3),      # 超过 120Hz 一帧
        "slow60": sum(1 for v in pm if v > 16.7),      # 超过 60Hz 一帧
        "out_med": med, "out_p90": _pct(real, 0.9), "out_max": max(real) if real else 0.0,
        "over": [d for d in real if med and d > med * 1.6],
        "n_idle": len(idle), "idle_max": max(idle) if idle else 0.0,
    }


def report(a):
    if not a["n_ev"]:
        print("     (本阶段没有滚轮事件, 跳过)")
        return
    print("  滚轮事件 %d 个 / 画面位移 %d 次 / pump %d 轮"
          % (a["n_ev"], a["n_mv"], a["n_pump"]))
    print("  输入节奏  中位 %5.1fms  p90 %5.1fms   <- 触控板交付频率"
          % (a["in_med"] * 1e3, a["in_p90"] * 1e3))
    print("  ★ pump 耗时 中位 %5.2fms  p90 %5.2fms  p99 %5.2fms  最大 %6.2fms"
          % (a["p50"], a["p90"], a["p99"], a["pmax"]))
    print("             超过 8.3ms(掉 120Hz 帧): %d / %d   超过 16.7ms(掉 60Hz 帧): %d"
          % (a["slow120"], a["n_pump"], a["slow60"]))
    print("  位移间隔  中位 %5.1fms  p90 %5.1fms (仅滚动中; ⚠️顶/底钳制会拉长, 非卡顿)"
          % (a["out_med"] * 1e3, a["out_p90"] * 1e3))
    print("  停手间隔 %d 次(最长 %.0fms)" % (a["n_idle"], a["idle_max"] * 1e3))
    if a["n_pump"] < 50:
        print("  ⚠️ 采样太少, 请在展开阶段持续滚十来秒")
    elif a["p99"] <= 8.3 and a["slow120"] == 0:
        print("  ✅ 每轮 pump 都在 120Hz 一帧内(p99 %.2fms), 我们不是瓶颈" % a["p99"])
    elif a["slow60"] == 0:
        print("  ⚠️ 有 %d 轮 pump 超过 120Hz 一帧(但都不到 60Hz 一帧) —— 偶发轻微"
              % a["slow120"])
    else:
        print("  ⚠️ 有 %d 轮 pump 超过 60Hz 一帧 —— 真掉帧, 需查是哪一段慢"
              % a["slow60"])


def main():
    o = Overlay()
    o.show()

    from transcript_view import _view_classes
    ScrollCls, _ = _view_classes()
    state = {"recv": 0, "evt": []}

    def _counting_scroll(self, event):              # noqa: N802
        state["recv"] += 1
        state["evt"].append(time.monotonic())
        if getattr(self, "_scroll_enabled", True):
            objc.super(ScrollCls, self).scrollWheel_(event)

    objc.classAddMethod(ScrollCls, b"scrollWheel:", _counting_scroll)

    for i in range(PROBE_N):
        en, zh = SENTS[i % len(SENTS)]
        o.finalize(f"[{i}] {en}", f"[{i}] {zh}")
    for _ in range(4):
        o.pump()

    # 从收起态开始(闸门探针是直接展开, 这里要测收起态的吞轮行为)
    o._apply_mode(PHASES[0][2])
    for _ in range(3):
        o.pump()

    H = o._panel.frame().size.height

    def mk(y, size, color, bold=False):
        l = NSTextField.alloc().initWithFrame_(((16.0, y), (620.0, 22.0)))
        l.setEditable_(False); l.setSelectable_(False)
        l.setBezeled_(False); l.setDrawsBackground_(False)
        l.setFont_((NSFont.boldSystemFontOfSize_(size) if bold
                    else NSFont.systemFontOfSize_(size)))
        l.setTextColor_(color)
        l.setStringValue_("")
        o._ve.addSubview_(l)
        return l

    phase_lbl = mk(H - 74.0, 15.0, NSColor.systemYellowColor(), bold=True)
    read_lbl = mk(H - 96.0, 13.0, NSColor.whiteColor())
    hint_lbl = mk(H - 116.0, 11.0, NSColor.whiteColor().colorWithAlphaComponent_(0.65))
    hint_lbl.setStringValue_("鼠标移到本窗上双指滚动；阶段会自动前进")

    print("=" * 66)
    print("验收探针: %d 句历史, 收起 -> 展开 两阶段自动切换" % PROBE_N)
    print("  ① 0-16s  收起态 —— 滚轮应无任何反应")
    print("  ② 16-40s 展开态 —— 应能滚动; 滚上去出现 ↓最新, 滚回底部消失")
    print("=" * 66)

    t0 = time.monotonic()
    stats = []
    cur = None
    last_print = -1
    last_lbl_t = 0.0
    try:
        while time.monotonic() - t0 < PHASES[-1][1] + 1.0:
            el = time.monotonic() - t0
            idx = next((i for i, p in enumerate(PHASES) if p[0] <= el < p[1]), None)
            if idx is None:
                break
            a, b, collapsed, text = PHASES[idx]
            if cur != idx:
                if cur is not None:
                    stats[-1]["tb"] = time.monotonic()
                cur = idx
                o._apply_mode(collapsed)
                for _ in range(3):
                    o.pump()
                stats.append({"i": idx, "text": text, "collapsed": collapsed,
                              "recv0": state["recv"], "recv": 0,
                              "omin": None, "omax": None, "moves": [], "pumps": [],
                              "ta": time.monotonic(), "tb": None,
                              "latest_seen_visible": False,
                              "latest_seen_hidden_after_scroll": False,
                              "bad_at": None, "hidden_since": None,
                              "last_oy": None, "last_oy_t": 0.0})
                print("\n▶ 阶段 %d: %s" % (idx + 1, text), flush=True)

            _tp = time.perf_counter()
            o.pump()
            _pm = (time.perf_counter() - _tp) * 1000
            _pt = time.monotonic()
            st = stats[-1]
            st["pumps"].append((_pt, _pm))
            oy = o._tv._scroll.contentView().bounds().origin.y
            st["recv"] = state["recv"] - st["recv0"]
            st["omin"] = oy if st["omin"] is None else min(st["omin"], oy)
            st["omax"] = oy if st["omax"] is None else max(st["omax"], oy)

            now = time.monotonic()
            if st["last_oy"] is None or abs(oy - st["last_oy"]) > 0.5:
                st["moves"].append((now, oy))
                st["last_oy"] = oy
                st["last_oy_t"] = now

            hidden = o._btn_latest.isHidden()
            if oy > 3.0 and not hidden:
                st["latest_seen_visible"] = True
            # ⚠️ 只认"**持续**隐藏"才算问题: 离开底部的那一帧, 视口已动而
            # following 还没被 tick 改写, 会出现 1 帧的相位差(实测 origin=4~17
            # 时 follow 才翻转, 属正常)。要连续 >50ms 才判为真异常。
            if oy > 3.0 and hidden:
                if st["hidden_since"] is None:
                    st["hidden_since"] = now
                elif now - st["hidden_since"] > 0.05 and st["bad_at"] is None:
                    st["bad_at"] = (round(oy, 1), o._tv.following, st["recv"])
                    st["latest_seen_hidden_after_scroll"] = True
            else:
                st["hidden_since"] = None

            remain = max(0, b - el)
            if now - last_lbl_t > 0.1:        # 节流: 探针自己别成为负担
                last_lbl_t = now
                phase_lbl.setStringValue_("%s   (剩 %.0fs)" % (text, remain))
                read_lbl.setStringValue_(
                    "recv=%d   origin.y=%.0f   ↓最新=%s"
                    % (st["recv"], oy, "隐藏" if hidden else "显示"))
            key = (idx, int(remain))
            if key != last_print and int(remain) % 4 == 0:
                last_print = key
                print("   t=%.0fs recv=%-4d origin=%-7.0f ↓最新=%s"
                      % (el, st["recv"], oy, "隐藏" if hidden else "显示"), flush=True)
            # 不再 sleep: 节奏完全由 pump 的让步决定(与真身 main.py 一致)。
            # 原来的 sleep(0.01) 会把事件排空压到 ~100Hz, 探针自身就成了卡顿源。
    except KeyboardInterrupt:
        print("\n(手动中断)")
    if stats and stats[-1]["tb"] is None:
        stats[-1]["tb"] = time.monotonic()

    print()
    print("=" * 66)
    print("结果")
    print("=" * 66)
    ok_all = True
    for st in stats:
        moved = (st["omax"] - st["omin"]) if st["omin"] is not None else 0.0
        if st["collapsed"]:
            good = st["recv"] > 0 and moved < 1.0
            verdict = ("✅ 收到 %d 个滚轮事件但视口未动 —— 正确吞掉"
                       % st["recv"]) if good else \
                      ("❌ recv=%d 视口移动 %.0fpx —— 收起态不该能滚"
                       % (st["recv"], moved))
        else:
            good = moved > 5.0 and st["latest_seen_visible"] \
                and not st["latest_seen_hidden_after_scroll"]
            if moved <= 5.0:
                verdict = "❌ 视口没动(移动 %.0fpx)" % moved
            elif not st["latest_seen_visible"]:
                verdict = "❌ 滚上去了但 ↓最新 没出现"
            elif st["latest_seen_hidden_after_scroll"]:
                verdict = ("⚠️ 滚上去时 ↓最新 曾经是隐藏的"
                           + ("  (首次见于 origin=%.1f following=%s recv=%d)"
                              % st["bad_at"] if st["bad_at"] else ""))
            else:
                verdict = "✅ 视口移动 %.0fpx, ↓最新 出现/消失正确" % moved
        ok_all = ok_all and good
        print("  阶段 %d (%s): %s" % (st["i"] + 1,
                                     "收起" if st["collapsed"] else "展开", verdict))
        report(analyze(st["moves"], state["evt"], st["pumps"],
                       st["ta"], st["tb"] or time.monotonic()))

    print()
    print("总结:", "✅ 全部通过 —— 滚动方案可上线" if ok_all
          else "⚠️ 见上方 ❌/⚠️ 项")


if __name__ == "__main__":
    main()
