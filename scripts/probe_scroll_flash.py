#!/usr/bin/env python3
"""回放一段课 + 合成触控板手势：「往上翻会不会被拽回底部 / 底部闪不闪」的探针。

    ClassLive.app/Contents/MacOS/python scripts/probe_scroll_flash.py --layout classic
    ClassLive.app/Contents/MacOS/python scripts/probe_scroll_flash.py --layout d --json /path.json

## 它演什么

真面板（真 `Overlay`）+ 一段按真实节奏喂进去的课（`probe_d_layout.LECTURE`，
草稿逐词长 / 草稿译文 / 定稿流式 / finalize；每个泵周期都喂一次 = 与 `main` 的
`drain()` → `ui.pump()` 同一个顺序），然后在**教授继续讲**的时候发一条合成手势：
往上滑一段（phase: began→changed→ended）松手，再来一段惯性（momentumPhase:
began→changed→ended），之后 10 秒不再碰它，看视口会不会被程序自己拽回底部。

## 判据（作者原话：「我往上翻，它一直想把我拉回最底下」）

  ① **手势期间不许有反向位移** —— 手势只往上（正 delta），任何一帧 origin **变小**
     > `BACK_PX` 就是程序在跟手势抢（`retreat_frames`）。
  ② **松手后视线不许漂** —— 观察窗口里「视口顶那一行的序号」（first_line_idx）
     不许变（±1 行容忍取整）。它变小 = 被往最新那几行拽（`gaze_jumps`）。
  ③ **不许自己回底** —— 观察窗口里 origin 不许回到 `BOTTOM_EPS` 以内
     （`auto_return_s`）。回底只许由用户自己的手势触发。
  ④ **不许停不下来** —— 观察窗口结束时 follow 必须仍是**假**（`follow_end`）。
  ⑤ 反向不变量（防修过头）：看够了以后**自己**往下滚到底，follow 必须翻真
     （`resume_on_user_bottom`）。这条修前修后都必须过。

全绿 = PASS。任何一条红 = FAIL（这条探针在修复前就该是红的）。

## 合成事件怎么进窗口（隔离与安全）

⚠️ 硬边界：「合成滚轮事件只发给你自己开的隔离测试窗口」。

所以本探针**不往系统 HID 队列投事件**（那会滚到别的 app）。做法：
`CGEventCreateScrollWheelEvent` 造一个**真 CGEvent**（`isContinuous` + `ScrollPhase`
+ `MomentumPhase` + point/fixed 增量，与触控板事件同字段），用
`NSEvent.eventWithCGEvent_` 包成真 `NSEvent`，再在 **pump 的事件派发阶段**
（`_drain_events` 的包装里）交给**真** `TranscriptScroll.scrollWheel_` —— 即
AppKit 会把它交给的那个对象。事件对象与真触控板事件同字段，但**一个字节都不出
本进程**：不存在滚错窗口的可能（`CGEventPostToPid` 实测不通，且系统级投递需要
辅助功能权限）。

## 隔离

构造真 `Overlay` 会往仓库根写 `.window`（窗口尺寸记忆）：照 `probe_motion.py`
先备份、跑完还原。不跑 `main` / `cl`、回调全是桩、**不碰** `sessions/` /
`~/.classlive/`。窗口会短暂出现在屏幕上（要用真 run loop 才算数，见
`transcript_view.tick` 的可见性判据）。

⚠️⚠️ **作者在同一台机器上用电脑**，光标扫过面板 / 触控板滚一下就会把真事件喂进来
（实测过一次：视口被真滚动带到最底，探针差点报成"被拽回底"）。两道防线：
  ① 面板一开就 `setIgnoresMouseEvents_(True)`（= app 自己的「鼠标穿透」）——
     真实鼠标 / 滚轮**到不了**这张窗；合成事件直接交给滚动视图对象，照旧生效。
  ② 滚动视图收到的每一条滚轮事件都记「合成 / 真实」；出现真实事件、或面板有一帧
     被判「不可见」（`_visible` 假时 `tick` 根本不跑）→ 这一场判 **INVALID**（rc=2），
     宁可重跑也不拿脏数据下结论。另外每秒 `orderFrontRegardless()` 一次，别让别的
     窗口盖住面板。
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time
import typing

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from probe_d_layout import LECTURE                                            # noqa: E402
from probe_motion import isolated_window_file                                  # noqa: E402

# ---- 手势参数 --------------------------------------------------------------
GESTURE_AT_S = 6.0        # 开课几秒后发手势（先让课喂够、行够多）
HOLD_EVENTS = 14          # 「手指在板上滑」的事件条数
HOLD_DT = 0.02            # 两条之间 20ms（≈ 50Hz，触控板档）
HOLD_DY = (4, 6, 8, 10, 12, 14, 16, 18, 20, 20, 18, 16, 14, 12)   # px/事件
MOM_EVENTS = 18           # 「松手后的惯性」
MOM_DT = 0.03
OBSERVE_S = 10.0          # 松手后观察多久（> IDLE_S=6，才能看见闲置回底）

# ---- 判据阈值 --------------------------------------------------------------
BACK_PX = 4.0             # 反向位移超过这么多 px = 一帧「被拽回去」
GAZE_TOL_LINES = 1        # 视口顶行号允许的取整噪声（行）
BOTTOM_EPS = 3.0          # 与 transcript_view.BOTTOM_EPS 同口径

# ---- 课的节奏（与 probe_d_layout 同源，压缩一点点）------------------------
WORD_S = 0.22
STREAM_S = 0.04
CHUNK = 2
FINAL_LAG_S = 0.8
DRAFT_ZH_S = 0.8
SEG_GAP_S = 0.5


def momentum_deltas() -> list[float]:
    """惯性的增量序列：从 HOLD 末速度开始衰减到 0（触控板的形状）。"""
    out = []
    v = float(HOLD_DY[-1])
    for _ in range(MOM_EVENTS):
        out.append(round(v, 1))
        v *= 0.86
    out[-1] = 0.0
    return out


class ClassFeed:
    """按绝对时间喂课（等价于 `main.drain()` 每轮拿到的那串调用）。"""

    def __init__(self, lecture: list, total_s: float) -> None:
        self.events: list[tuple[float, str, tuple]] = []
        t = 0.0
        i = 0
        while t < total_s:
            s = lecture[i % len(lecture)]
            words = s.draft_en.split()
            for k, _w in enumerate(words):
                self.events.append((t + k * WORD_S, "add_draft", (" ".join(words[:k + 1]),)))
            t_zh1 = t + (len(words) // 2) * WORD_S
            self.events.append((t_zh1, "draft_zh", (s.draft_zh[: max(1, len(s.draft_zh) * 2 // 3)],)))
            t_end = t + max(0, len(words) - 1) * WORD_S
            self.events.append((t_end + DRAFT_ZH_S * 0.3, "draft_zh", (s.draft_zh,)))
            t_stream = t_end + FINAL_LAG_S
            self.events.append((t_stream, "seed_en", (s.seed_en,)))
            n = max(1, (len(s.final_zh) + CHUNK - 1) // CHUNK)
            for k in range(n):
                self.events.append((t_stream + k * STREAM_S, "stream_zh",
                                    (s.final_zh[k * CHUNK:(k + 1) * CHUNK],)))
            t_fin = t_stream + (n - 1) * STREAM_S + 0.15
            self.events.append((t_fin, "finalize", (s.final_en, s.final_zh)))
            t = t_fin + SEG_GAP_S
            i += 1
        self.events.sort(key=lambda e: e[0])
        self.i = 0

    def due(self, t: float, o) -> int:
        n = 0
        while self.i < len(self.events) and self.events[self.i][0] <= t:
            _tt, meth, args = self.events[self.i]
            self.i += 1
            getattr(o, meth)(*args)
            n += 1
        return n


class Wheel:
    """合成触控板手势：真 CGEvent → 真 NSEvent → 真 TranscriptScroll.scrollWheel_。

    ⚠️ **隔离**: 面板一开就 `setIgnoresMouseEvents_(True)`（= app 自己的「穿透」档）——
    真实鼠标 / 触控板事件**到不了**这张窗（作者在同一台机器上用电脑，光标扫过面板
    就会污染这一场）。合成事件是**直接交给滚动视图对象**的，不经过命中测试，照旧生效。
    ⚠️ 顺带记账：滚动视图收到的每一条滚轮事件都记一笔「合成 / 真实」—— 出现真实事件
    这一场就判 INVALID（宁可重跑，也不拿一场脏数据下结论）。
    """

    def __init__(self, o) -> None:
        self._o = o
        self._sv = o._tv._scroll
        self._t0 = 0.0
        self._q: list[tuple[float, object]] = []
        self._sent = 0
        self.injecting = False
        self.foreign = 0
        self.synthetic = 0

    def _hook_class(self) -> None:
        """给 TranscriptScroll.scrollWheel_ 挂一层计数（行为逐字交回原实现）。"""
        import objc
        from transcript_view import _view_classes
        cls = _view_classes()[0]
        orig = cls.scrollWheel_
        me = self

        def hook(self, event) -> None:                  # noqa: N802
            if me.injecting:
                me.synthetic += 1
            else:
                me.foreign += 1
            orig(self, event)

        try:
            objc.classAddMethod(cls, b"scrollWheel:", hook)
        except Exception:                               # noqa: BLE001 记账坏了不该拦住探针
            pass

    def schedule_gesture(self, at_s: float) -> float:
        """排一条「滑一段 + 松手惯性」。返回手势结束（惯性也停）的时刻。"""
        t = at_s
        for k in range(HOLD_EVENTS):
            self._q.append((t, self._mk(HOLD_DY[k % len(HOLD_DY)],
                                        phase=1 if k == 0 else 2)))
            t += HOLD_DT
        self._q.append((t, self._mk(0.0, phase=4)))          # 松手
        t += 0.04
        for k, dy in enumerate(momentum_deltas()):
            last = k == len(momentum_deltas()) - 1
            self._q.append((t, self._mk(dy, momentum=4 if last else (1 if k == 0 else 2))))
            t += MOM_DT
        return t

    def schedule_scroll_down_to_bottom(self, at_s: float, n: int = 60) -> None:
        """反向下滑（用来验「用户自己滚到底 → 恢复跟随」）。"""
        t = at_s
        for k in range(n):
            self._q.append((t, self._mk(-14.0, phase=1 if k == 0 else 2)))
            t += 0.01
        self._q.append((t, self._mk(0.0, phase=4)))

    def _mk(self, dy: float, phase: int = 0, momentum: int = 0):
        from AppKit import NSEvent
        from Quartz import (CGEventCreateScrollWheelEvent, CGEventSetLocation,
                            CGEventSetIntegerValueField, kCGScrollEventUnitPixel,
                            kCGScrollWheelEventIsContinuous,
                            kCGScrollWheelEventScrollPhase,
                            kCGScrollWheelEventMomentumPhase,
                            kCGScrollWheelEventPointDeltaAxis1,
                            kCGScrollWheelEventFixedPtDeltaAxis1,
                            kCGScrollWheelEventDeltaAxis1)
        f = self._o._panel.frame()
        cx = f.origin.x + f.size.width / 2.0
        cy = f.origin.y + f.size.height / 2.0
        e = CGEventCreateScrollWheelEvent(None, kCGScrollEventUnitPixel, 1, 0)
        CGEventSetLocation(e, (cx, cy))
        CGEventSetIntegerValueField(e, kCGScrollWheelEventIsContinuous, 1)
        CGEventSetIntegerValueField(e, kCGScrollWheelEventScrollPhase, int(phase))
        CGEventSetIntegerValueField(e, kCGScrollWheelEventMomentumPhase, int(momentum))
        CGEventSetIntegerValueField(e, kCGScrollWheelEventDeltaAxis1, int(dy))
        CGEventSetIntegerValueField(e, kCGScrollWheelEventPointDeltaAxis1, int(dy))
        CGEventSetIntegerValueField(e, kCGScrollWheelEventFixedPtDeltaAxis1, int(dy * 65536))
        return NSEvent.eventWithCGEvent_(e)

    def install(self, t0: float) -> None:
        """把合成事件挂到 pump 的**事件派发阶段**（`_drain_events` 的尾巴）。"""
        self._t0 = t0
        self._hook_class()
        orig = self._o._drain_events

        def drain(app, mode):                              # noqa: ANN001
            busy = orig(app, mode)
            if not self._q:
                return busy
            now = time.monotonic() - self._t0
            while self._q and self._q[0][0] <= now:
                _t, ev = self._q.pop(0)
                if ev is None:
                    continue
                try:
                    self.injecting = True
                    self._sv.scrollWheel_(ev)              # AppKit 会给的同一个对象
                    self._sent += 1
                except Exception:                          # noqa: BLE001
                    pass
                finally:
                    self.injecting = False
                busy = True
            return busy

        self._o._drain_events = drain

    @property
    def sent(self) -> int:
        return self._sent


class Frame(typing.NamedTuple):
    t: float
    origin: float
    follow: bool
    level: int          # 视口顶那一层的层号
    idx: int            # 视口顶那一行在历史里的序号（count-1-level）
    vis: bool = True    # 面板这一刻算不算「看得见」（不可见时 tick 不跑，数据作废）


def _frame(o, t: float) -> Frame:
    tv = o._tv
    origin = tv._scroll.contentView().bounds().origin.y
    lv = int(origin // tv._row_h) if tv._row_h > 0 else 0
    return Frame(round(t, 3), round(origin, 1), bool(tv.following), lv,
                 tv._count() - 1 - lv, bool(o._visible))


def judge(frames: list[Frame], g_start: float, g_end: float) -> dict:
    """判据见文件头。frames 是整场回放的逐帧记录。"""
    frames = [f for f in frames if f.vis]        # 不可见帧里 tick 不跑，别拿它判
    hold = [f for f in frames if g_start <= f.t <= g_end]
    obs = [f for f in frames if g_end + 0.5 <= f.t <= g_end + OBSERVE_S]

    retreat = []
    for a, b in zip(hold, hold[1:]):
        if b.origin < a.origin - BACK_PX:
            retreat.append((b.t, round(a.origin - b.origin, 1)))

    gaze_jumps = []
    if obs:
        base = obs[0].idx
        prev = base
        for f in obs[1:]:
            if abs(f.idx - prev) >= GAZE_TOL_LINES:
                gaze_jumps.append((f.t, prev, f.idx))
            prev = f.idx
        gaze_net = obs[-1].idx - base
    else:
        gaze_net = 0

    auto_return_t = None
    for f in obs:
        if f.origin <= BOTTOM_EPS:
            auto_return_t = f.t - g_end
            break
    follow_end = obs[-1].follow if obs else None

    # 反向下滑之后（由调用方追加的 frames 段）—— 用最后 2 秒判
    tail = [f for f in frames if f.t >= frames[-1].t - 2.0]
    resume = any(f.follow for f in tail)

    ok = (not retreat) and (not gaze_jumps) and auto_return_t is None \
        and follow_end is False and resume
    return {
        "gesture_s": [g_start, g_end],
        "retreat_frames": retreat,            # ① 手势期间被反向拽（红）
        "n_retreat": len(retreat),
        "gaze_jumps": gaze_jumps,             # ② 松手后视线漂（红）
        "n_gaze_jumps": len(gaze_jumps),
        "gaze_net_lines": gaze_net,
        "auto_return_s": auto_return_t,       # ③ 自己回底（红）
        "follow_end": follow_end,             # ④ 观察结束时还在不在跟随
        "resume_on_user_bottom": bool(resume),  # ⑤ 反向下滑能恢复跟随（必须真）
        "PASS": bool(ok),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--layout", choices=("classic", "d"), default="classic",
                    help="字幕布局（默认 classic = 已发布 3.9.7 那套）")
    ap.add_argument("--json", default="", metavar="PATH", help="把逐帧轨迹 + 判据写成 JSON")
    ap.add_argument("--observe", type=float, default=OBSERVE_S, metavar="S",
                    help=f"松手后观察秒数（默认 {OBSERVE_S}，要 > 6 才看得见闲置回底）")
    a = ap.parse_args()

    os.environ["CLASSLIVE_LAYOUT"] = "d" if a.layout == "d" else "classic"

    try:
        from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
        import overlay
        import motion
    except Exception as e:                                       # noqa: BLE001
        print(f"⚠ 起不来：{type(e).__name__}: {e}")
        return 1
    NSApplication.sharedApplication().setActivationPolicy_(
        NSApplicationActivationPolicyAccessory)

    assert (overlay.LAYOUT_D == (a.layout == "d")), "布局开关没生效"
    run_s = GESTURE_AT_S + 1.0 + a.observe + 4.0
    feed = ClassFeed(LECTURE, run_s)

    out = {"layout": a.layout, "reduce_motion": bool(motion.reduce_motion()),
           "frames": [], "judge": {}}
    print("=" * 72)
    print(f"滚动手势探针 · 布局 {a.layout}（LAYOUT_D={overlay.LAYOUT_D}）")
    print(f"  Reduce Motion={out['reduce_motion']}；"
          f"行高 / 中文档位由叠层自己定（见下面那行）")

    with isolated_window_file():
        o = overlay.Overlay(on_lost=lambda: True)
        try:
            o.show()
            o._panel.setFrameOrigin_((240.0, 200.0))
            # ⚠️ **隔离真实输入**: 作者在同一台机器上用电脑，光标扫过面板 / 触控板
            #    滚一下就会喂进真事件（实测能把这一场弄脏：视口被真滚动带到底，
            #    探针就报"被拽回底"）。穿透档（= app 自己的「鼠标穿透」）让真实
            #    鼠标 / 滚轮**到不了**这张窗；合成事件是直接交给滚动视图对象的，
            #    不走命中测试，照旧生效。真收到一条真事件 -> 这一场判 INVALID。
            o._panel.setIgnoresMouseEvents_(True)
            for _ in range(40):
                o.pump()
            if not o._panel.isVisible():
                print("⚠ 面板不可见 —— tick / 渲染都不跑，探针无意义，退出。")
                return 1
            print(f"  面板 {o._panel.frame().size.width:.0f}x{o._panel.frame().size.height:.0f}"
                  f"@({o._panel.frame().origin.x:.0f},{o._panel.frame().origin.y:.0f})"
                  f"  可见={bool(o._panel.isVisible())} 内部 _visible={o._visible}"
                  f"  穿透=是（不吃真实事件）")
            print(f"  判据: 反向位移 >{BACK_PX}px 即红; 观察窗 {a.observe:.0f}s")

            # 预热: 60 句历史（一节真课 1000+ 句，这里够翻就行）
            for i in range(60):
                o.finalize(f"line {i} of the lecture", f"第{i}句：课堂内容。")
            for _ in range(30):
                o.pump()

            wheel = Wheel(o)
            g_start = GESTURE_AT_S
            g_end = wheel.schedule_gesture(g_start)
            wheel.schedule_scroll_down_to_bottom(g_end + a.observe + 1.0)
            t0 = time.monotonic()
            wheel.install(t0)

            frames: list[Frame] = []
            last_front = 0.0
            while True:
                t = time.monotonic() - t0
                if t >= run_s:
                    break
                if t - last_front >= 1.0:
                    # 别的窗口盖上来会让内部 `_visible` 变假（tick 就不跑了）——
                    # 每秒把面板重新端到最前（不抢焦点: accessory + orderFrontRegardless）。
                    last_front = t
                    try:
                        o._panel.orderFrontRegardless()
                    except Exception:                          # noqa: BLE001
                        pass
                feed.due(t, o)               # drain() 那一半
                o.pump()                     # ui.pump() 那一半（事件 → tick → 画）
                frames.append(_frame(o, t))
                time.sleep(0.0005)
            hidden = sum(1 for f in frames if not f.vis)
            print(f"  合成事件发出 {wheel.sent} 条（到达滚动视图 {wheel.synthetic} 条）；"
                  f"逐帧 {len(frames)} 帧；手势 {g_start:.1f}–{g_end:.1f}s；"
                  f"真事件 {wheel.foreign} 条；不可见帧 {hidden}")
        finally:
            o.close()

    dirty = bool(wheel.foreign) or hidden > 3
    v = judge(frames, g_start, g_end + 0.05)
    v["foreign_events"] = int(wheel.foreign)
    v["hidden_frames"] = int(hidden)
    v["VALID"] = not dirty
    out["frames"] = [f._asdict() for f in frames]
    out["judge"] = v

    def show_frames(label: str, lo: float, hi: float, step: float = 0.6) -> None:
        print(f"  {label}")
        last = -1e9
        for f in frames:
            if lo <= f.t <= hi and f.t - last >= step:
                last = f.t
                print(f"    t={f.t:6.2f}  origin={f.origin:7.1f}  顶行#{f.idx:4d}"
                      f"  跟随={'是' if f.follow else '否'}")

    print("-" * 72)
    show_frames("手势期间（每 0.6s 一行）", g_start - 0.6, g_end + 0.6)
    show_frames("松手后观察窗", g_end, g_end + a.observe + 0.1, step=1.0)
    print("-" * 72)
    if not v["VALID"]:
        print(f"⚠ 这一场判 INVALID：真事件 {v['foreign_events']} 条 / 不可见帧 "
              f"{v['hidden_frames']} 帧 —— 数据不干净，**别下结论**，等屏幕安静了重跑。")
        return 2
    print(f"① 手势期间反向位移帧数      : {v['n_retreat']}"
          + (f"   ← 首次 {v['retreat_frames'][0]}" if v["retreat_frames"] else ""))
    print(f"② 松手后视线跳变            : {v['n_gaze_jumps']} 次"
          f"（净漂 {v['gaze_net_lines']:+d} 行）")
    print(f"③ 无人输入却回到最底        : "
          + (f"是，{v['auto_return_s']:.1f}s 后 ← 被拽回去了" if v["auto_return_s"] is not None
             else "否"))
    print(f"④ 观察结束时还在跟随        : {v['follow_end']}")
    print(f"⑤ 自己往下滚到底能恢复跟随  : {v['resume_on_user_bottom']}（必须真）")
    print("-" * 72)
    print("结论:", "✅ PASS —— 往上翻稳住、没人拽你" if v["PASS"] else "❌ FAIL —— 被程序拽了/闪了")

    if a.json:
        p = pathlib.Path(a.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"轨迹与判据 -> {p}")
    return 0 if v["PASS"] else 1


if __name__ == "__main__":
    sys.exit(main())
