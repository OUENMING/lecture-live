#!/usr/bin/env python3
"""Claude Desktop 那套三组动效的判据（2026-10-18）：

    ClassLive.app/Contents/MacOS/python tests/test_motion_cds.py

* **按压反馈**：值 / 曲线（`motion.PRESS_*` / `SPRING_STOPS` / `spring_scales`）、
  绕**中心**缩放（不能朝角落缩）、`press_down` / `press_up` 的模型值与动画、
  「减弱动态效果」整条跳过；按钮工厂 `panel.make_button` 是**唯一入口**。
* **静默三点**：`is_running` / `status_render` / `status_width` 纯函数；上屏时
  尾部「…」换成三颗点、非进行态撤点；`reduce_motion` 时保留「…」。
* **状态词换字**：换字造替身（旧词）、同字不重放、连续换字不叠（同时只有一个替身）。

⚠️ 本文件**不跑事件循环**、不发鼠标事件 —— 「真点击（按下/抬起时序、action 恰好一次）」
   由 `scripts/probe_motion.py` 的真 CGEvent 探针覆盖；这里钉的是不依赖事件循环的那半。
⚠️ `reduce_motion` 一律**打桩**：真机开着它时相关支路整条不跑，判据会假绿。
"""
from __future__ import annotations
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import motion                                                       # noqa: E402
import panel                                                        # noqa: E402

RESULTS: list = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  {detail}" if detail else ""))


def _with_reduce(value, fn):
    real = motion.reduce_motion
    try:
        motion.reduce_motion = lambda: value
        return fn()
    finally:
        motion.reduce_motion = real


# ══════════════════════════════════════════════════════════════════════
# ① 值（唯一真源在 motion.py）
# ══════════════════════════════════════════════════════════════════════
def t_values() -> None:
    print("\n--- ① 值 ---")
    check("PRESS_S == 0.06（--cds-dur-fast）", abs(motion.PRESS_S - 0.06) < 1e-9, str(motion.PRESS_S))
    check("RELEASE_SPRING_S == 0.45（--cds-dur-slow）",
          abs(motion.RELEASE_SPRING_S - 0.45) < 1e-9, str(motion.RELEASE_SPRING_S))
    check("PRESS_SCALE == 0.975", abs(motion.PRESS_SCALE - 0.975) < 1e-9, str(motion.PRESS_SCALE))
    want = (0.0, .2459, .6526, .9468, 1.0764, 1.0915, 1.0585, 1.0219,
            .9993, .9914, .9921, .9957, .9988, 1.0004, 1.0)
    check("SPRING_STOPS 逐字（15 个采样）", motion.SPRING_STOPS == want,
          f"{len(motion.SPRING_STOPS)} 个")
    check("首 0 尾 1（CSS linear() 的语义）",
          motion.SPRING_STOPS[0] == 0.0 and motion.SPRING_STOPS[-1] == 1.0)
    check("点：周期 1.9 / 相位差 0.15 / 直径 3.5 / 间距 3 / 文字间隙 5",
          (motion.DOTS_PERIOD_S, motion.DOTS_DELAY_S, motion.DOTS_D, motion.DOTS_GAP,
           motion.DOTS_TEXT_GAP) == (1.9, 0.15, 3.5, 3.0, 5.0))
    check("换字：退 0.20（= --cds-dur-base）/ 进 0.30",
          abs(motion.SWAP_OUT_S - 0.20) < 1e-9 and abs(motion.SWAP_IN_S - 0.30) < 1e-9)


def t_spring() -> None:
    print("\n--- ① 回弹曲线 ---")
    sc = motion.spring_scales()
    check("首帧 = PRESS_SCALE", abs(sc[0] - motion.PRESS_SCALE) < 1e-12, f"{sc[0]:.6f}")
    check("⭐ 末帧**精确** = 1.0（不是 0.999）", sc[-1] == 1.0, repr(sc[-1]))
    peak = max(sc)
    check("过冲很小：峰值 ≈ 1.0023（0.025 行程的 9.15%）",
          abs(peak - (motion.PRESS_SCALE + 0.025 * 1.0915)) < 1e-6,
          f"peak={peak:.6f}")
    check("峰值出现在前半（先弹后落，不是越跑越高）",
          sc.index(peak) < len(sc) // 2, f"index={sc.index(peak)}")
    check("落定：最后三个值都贴着 1（|v-1| < 0.002）",
          all(abs(v - 1.0) < 0.002 for v in sc[-3:]), str([round(v, 4) for v in sc[-3:]]))
    check("从 1 出发 = 恒 1（没按过也不许抖）",
          all(abs(v - 1.0) < 1e-12 for v in motion.spring_scales(1.0)))
    check("对照：过冲**确实存在**（否则上面那条「峰值」恒真）", peak > 1.0, f"{peak:.6f}")


# ══════════════════════════════════════════════════════════════════════
# ② 「还在跑」→ 点（纯函数）
# ══════════════════════════════════════════════════════════════════════
def t_status_pure() -> None:
    print("\n--- ② is_running / status_render（纯函数） ---")
    for t in ("下载中…", "更新中…", "正在生成笔记…", "正在验证…", "搜「货币」…", "扫描 0/10…"):
        check(f"还在跑：{t!r}", motion.is_running(t))
    for t in ("已经是最新的了", "已清除搜索", "", "2 项要处理  ▾",
              "⚠ 没删：boom", "好 —— 本地 Whisper 留着，以后不再问"):
        check(f"不是：{t!r}", not motion.is_running(t))
    check("纯函数产出**一个字不动**（`ready_short` 那半没被改）",
          motion.status_render("下载中…")[0] == "下载中"
          and motion.status_render("下载中…")[1] is True)
    check("非进行态原样、无点", motion.status_render("已清除搜索") == ("已清除搜索", False))
    check("`running=True` 而文字**没有**尾部「…」→ 不裁字（不许吃掉最后一个字）",
          motion.status_render("正在验证", running=True) == ("正在验证", True),
          str(motion.status_render("正在验证", running=True)))
    check("`running=False` 显式关掉（胶囊裁出来的「…」不是进行态）",
          motion.status_render("◌  语音模型  缺 3 件，约 111…", running=False)
          == ("◌  语音模型  缺 3 件，约 111…", False))
    check("reduce：原样带「…」、无点",
          motion.status_render("下载中…", reduce=True) == ("下载中…", False))
    check("窄字符串宽度：进行态 = 文字 + 点的那一份",
          abs(motion.status_width("下载中…", None)
              - (motion.status_width("下载中", None) + motion.dots_reserve())) < 1e-6)
    check("reduce 下宽度 = 纯文字（点不占位）",
          _with_reduce(True, lambda: abs(motion.status_width("下载中…", None)
                                         - motion.status_width("下载中", None)) < 1e-6))


# ══════════════════════════════════════════════════════════════════════
# ③ 按钮工厂 + 按压（真 AppKit 对象，不跑事件循环）
# ══════════════════════════════════════════════════════════════════════
def t_press_view() -> None:
    print("\n--- ③ press_down / press_up（模型值 + 动画） ---")
    from AppKit import NSButton, NSMakeRect
    btn = panel.make_button(NSMakeRect(0.0, 0.0, 120.0, 30.0))
    btn.setWantsLayer_(True)        # 真机上按钮在面板里时层早就有了（style_* / 面板装配）
    check("工厂给出的是 NSButton 子类（可点、可 styled、可 setTarget）",
          isinstance(btn, NSButton) and type(btn) is not NSButton,
          type(btn).__name__)
    check("子类**覆盖了** mouseDown_（按压反馈的挂点）",
          "_ClassLivePressButton" in type(btn).__name__)
    plain = NSButton.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 10.0, 10.0))
    check("对照：裸 NSButton 没有反馈（否则「子类」这条无意义）",
          type(plain) is NSButton and type(plain) is not type(btn))

    check("按下前：恒等变换（不缩）",
          abs(float(btn.layer().transform().m11) - 1.0) < 1e-9)
    motion.press_down(btn)
    t = btn.layer().transform()
    check("按下：缩到 PRESS_SCALE",
          abs(float(t.m11) - motion.PRESS_SCALE) < 1e-6, f"m11={float(t.m11):.4f}")
    # 绕中心：不动点 = bounds 中心（锚点 (0,0) 时 m41/m42 应把中心钉住）
    cx, cy = 60.0, 15.0
    x2 = cx * float(t.m11) + float(t.m41)
    y2 = cy * float(t.m22) + float(t.m42)
    check("⭐⭐ 绕**中心**缩放（中心点不动 —— 不朝角落缩）",
          abs(x2 - cx) < 1e-6 and abs(y2 - cy) < 1e-6,
          f"({x2:.3f}, {y2:.3f}) vs ({cx}, {cy})")
    check("按下动画挂上了（key=press）", btn.layer().animationForKey_("press") is not None)

    motion.press_up(btn)
    t = btn.layer().transform()
    check("抬起：模型值**精确**回到 1（m11=1, 无平移）",
          abs(float(t.m11) - 1.0) < 1e-9 and abs(float(t.m41)) < 1e-9
          and abs(float(t.m42)) < 1e-9)
    anim = btn.layer().animationForKey_("press")
    check("抬起是**关键帧**动画（15 个值 = SPRING_STOPS 的长度）",
          anim is not None and len(list(anim.values())) == len(motion.SPRING_STOPS),
          f"{len(list(anim.values())) if anim else 0} 个值")
    check("关键帧之间线性（CSS linear() 的语义）",
          anim is not None and str(anim.calculationMode()) == "linear",
          str(anim.calculationMode()) if anim else "-")
    # ⚠️⚠️ 值必须是 NSValue 包装：裸 CATransform3D 结构体在 display link 提交事务时
    #    抛 ObjC 异常、整个进程 SIGTRAP（2026-10-18 探针实测；这条判据是它的静态替身）。
    from Foundation import NSValue
    vals = list(anim.values()) if anim is not None else []
    check("⭐⭐ 关键帧的值是 NSValue 包装（裸结构体 = 提交时 SIGTRAP）",
          bool(vals) and all(isinstance(v, NSValue) for v in vals),
          f"{[type(v).__name__ for v in vals[:2]]}")
    # 按下动画的 from/to 也必须是 NSValue（同一条纪律的另一半）
    motion.press_down(btn)
    danim = btn.layer().animationForKey_("press")
    check("按下动画的 from/to 也是 NSValue 包装",
          danim is not None and isinstance(danim.fromValue(), NSValue)
          and isinstance(danim.toValue(), NSValue),
          f"{type(danim.fromValue()).__name__} / {type(danim.toValue()).__name__}"
          if danim is not None else "-")

    # Reduce Motion：整条跳过
    def _rm() -> tuple:
        b2 = panel.make_button(NSMakeRect(0.0, 0.0, 120.0, 30.0))
        b2.setWantsLayer_(True)
        motion.press_down(b2)
        motion.press_up(b2)
        return (abs(float(b2.layer().transform().m11) - 1.0) < 1e-9,
                b2.layer().animationForKey_("press") is None)
    ok_scale, ok_noanim = _with_reduce(True, _rm)
    check("Reduce Motion：不缩、不挂动画", ok_scale and ok_noanim)

    # 键盘 / performClick_ 那条路照旧（子类只碰 mouseDown_）
    hits = []
    b3 = panel.make_button(NSMakeRect(0.0, 0.0, 80.0, 24.0))
    tg = panel.make_button_target(lambda: hits.append(1))
    b3.setTarget_(tg)
    b3.setAction_("clicked:")
    b3.performClick_(None)
    check("performClick_（键盘等价键那条路）恰好触发一次 action", hits == [1], str(hits))


# ══════════════════════════════════════════════════════════════════════
# ④ 状态词上屏：点 / 替身 / reduce
# ══════════════════════════════════════════════════════════════════════
def _dots_of(v) -> list:
    return list(motion._get_attr(v, motion._DOTS_KEY) or [])


def _ghost_of(v):
    gid = motion._get_attr(v, motion._GHOST_KEY)
    sv = v.superview()
    if not gid or sv is None:
        return None
    for sub in (sv.subviews() or []):
        try:
            if sub.identifier() == gid:
                return sub
        except Exception:                                   # noqa: BLE001
            continue
    return None


def t_status_view() -> None:
    print("\n--- ④ render_status（标签 / 按钮） ---")
    from AppKit import NSButton, NSMakeRect, NSTextField, NSView
    host = NSView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 300.0, 100.0))
    lb = NSTextField.alloc().initWithFrame_(NSMakeRect(10.0, 10.0, 200.0, 18.0))
    lb.setStringValue_("拖课件到某张卡上")
    host.addSubview_(lb)

    motion.render_status(lb, "下载中…")
    check("进行态：上屏文字**去掉**尾部「…」", lb.stringValue() == "下载中", repr(lb.stringValue()))
    dots = _dots_of(lb)
    check("挂了**三颗**点（层，不是视图）", len(dots) == 3, f"{len(dots)} 颗")
    check("每颗点都带两条无限循环动画（透明度 + 位移）",
          all(d.animationForKey_("dots.op") is not None
              and d.animationForKey_("dots.y") is not None for d in dots))
    check("点挂在 label 的层上（跟着 label 走，不是面板坐标系里的孤儿）",
          all(d.superlayer() is lb.layer() for d in dots))
    ids_before = [id(d) for d in dots]
    motion.render_status(lb, "下载中…")
    check("同一句话再来一次：**不重放**（点还是原来那三颗）",
          [id(d) for d in _dots_of(lb)] == ids_before)

    motion.render_status(lb, "正在生成笔记…")
    check("换字：旧词造了**替身**（替身上是旧文字）",
          _ghost_of(lb) is not None and _ghost_of(lb).stringValue() == "下载中",
          repr(_ghost_of(lb).stringValue()) if _ghost_of(lb) is not None else "没有替身")
    ghost1 = _ghost_of(lb)
    motion.render_status(lb, "正在收尾…")
    ghosts = [s for s in host.subviews()
              if getattr(s, "identifier", lambda: "")() and "ghost" in str(s.identifier())]
    check("⭐⭐ 连续换字**不叠**：屏上只有一个替身（上一个当场撤掉）",
          len(ghosts) == 1 and ghosts[0] is not ghost1, f"{len(ghosts)} 个替身")

    motion.render_status(lb, "✅ 已写入 12 句")
    check("跑完：文字原样、点撤掉", lb.stringValue() == "✅ 已写入 12 句" and not _dots_of(lb))

    # 按钮（就绪胶囊）：走 title，不走 stringValue（那是开关状态！）
    btn = panel.make_button(NSMakeRect(0.0, 0.0, 120.0, 22.0))
    host.addSubview_(btn)
    btn.setStringValue_("1")            # 假装是个开着的开关态
    motion.render_status(btn, "◌  语音模型  下载中…")
    check("⭐ 胶囊换的是 title（`stringValue` 是开关状态 0/1，不许被文字覆盖）",
          btn.title() == "◌  语音模型  下载中" and str(btn.stringValue()) == "1",
          f"title={btn.title()!r} stringValue={btn.stringValue()!r}")
    check("胶囊也有三颗点", len(_dots_of(btn)) == 3)

    def _rm() -> tuple:
        lb2 = NSTextField.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 200.0, 18.0))
        host.addSubview_(lb2)
        motion.render_status(lb2, "下载中…")
        return (lb2.stringValue(), len(_dots_of(lb2)), _ghost_of(lb2))
    txt, n, g = _with_reduce(True, _rm)
    check("Reduce Motion：保留「…」、无点、无替身",
          txt == "下载中…" and n == 0 and g is None, f"{txt!r} {n} {g}")


# ══════════════════════════════════════════════════════════════════════
# ⑤ 收拢：可点按钮只许从 panel.make_button 来
# ══════════════════════════════════════════════════════════════════════
def t_single_factory() -> None:
    print("\n--- ⑤ 按钮工厂是唯一入口 ---")
    bad = []
    for f in ("entry_panel.py", "whatsnew.py", "wrapup.py", "tour.py", "overlay.py"):
        src = (HERE / f).read_text(encoding="utf-8")
        n = src.count("NSButton.alloc().initWithFrame_")
        if n:
            bad.append(f"{f}: {n} 处")
    check("⭐ 五个界面文件里**没有**裸 `NSButton.alloc()` 的可点按钮（都走 panel.make_button）",
          not bad, "；".join(bad))
    src = (HERE / "panel.py").read_text(encoding="utf-8")
    check("工厂本身只此一处（`_press_button_class().alloc()` 那一次）",
          src.count("_press_button_class().alloc().initWithFrame_(rect)") == 1
          and src.count("NSButton.alloc()") == 0,
          f"工厂 alloc 命中 {src.count('_press_button_class().alloc().initWithFrame_(rect)')}"
          f" / 裸 NSButton.alloc() {src.count('NSButton.alloc()')}")
    # ⚠️⚠️ 这条来自一次**真事故**（2026-10-18）：`_swap_text` 里写了 `CATransaction.begin()`
    #    而 commit 在重构中被删掉 —— 开着的事务吞掉之后所有隐式提交，
    #    **整个开课面板再也不渲染**，而窗口 `isVisible()` 照样是 True、
    #    所有单元判据全绿（它们不画屏）。行为判据测不到这类，先按源码配平钉住。
    msrc = (HERE / "motion.py").read_text(encoding="utf-8")
    check("⭐⭐ `CATransaction` 的 begin / commit 配平（开着不提交 = 面板永不渲染）",
          msrc.count("CATransaction.begin()") == msrc.count("CATransaction.commit()"),
          f"begin {msrc.count('CATransaction.begin()')} / "
          f"commit {msrc.count('CATransaction.commit()')}")


def main() -> int:
    print("Claude Desktop 三组动效（按压 / 静默点 / 换字）")
    for fn in (t_values, t_spring, t_status_pure, t_press_view, t_status_view,
               t_single_factory):
        try:
            fn()
        except Exception as e:                              # noqa: BLE001
            import traceback
            traceback.print_exc()
            check(f"{fn.__name__} 抛出异常", False, f"{type(e).__name__}: {e}")
    bad = [n for n, ok, _ in RESULTS if not ok]
    print(f"\n{'❌ ' + str(len(bad)) + ' 条不通过: ' + ', '.join(bad) if bad else '✅ 全部通过'}"
          f"（{len(RESULTS)} 条）")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
