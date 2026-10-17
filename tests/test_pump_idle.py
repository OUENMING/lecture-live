#!/usr/bin/env python3
"""3.9.7 省电：主循环「有事才醒」的判据。

    ClassLive.app/Contents/MacOS/python tests/test_pump_idle.py

## 钉住了什么

1. ⭐ **让步分档**（`Overlay._pump_tier` / `_pump_wait_s` / `_motion_active`）：
   有东西在动 → 热档 = **屏幕刷新周期** `_hot_wait_s()`（120 Hz → 8.33 ms / 60 Hz → 16.7 ms）；
   什么都没在动 → `PUMP_IDLE_LONG_S`；手势窗口内 → 0（热档）；窗口不可见 → `PUMP_HIDDEN_S`。
   `_motion_active` 的**每一条**（上滚 / 草稿淡出 / 草稿改字 / 主窗脏 / 转录区脏 /
   顶栏到点淡出）都单独钉一条 —— 漏一条就是一个动画被拖慢。
   另有一组桩屏幕的判据：60 / 120 / 0 / 抛异常 四档间隔 + 换屏后跟着变（见 `HotWaitFollowsScreen`）。
2. ⭐⭐ **`wake()` 真的能打断等待**：0.05 s 时从**另一个线程**叫一声，
   主线程那次等待必须在远早于 `PUMP_IDLE_LONG_S` 处返回。
   ⚠️ 这一条是本题的**全部意义**：没有它，省电就变成"字幕晚 0.1 s 上屏"。
   顺带钉：`wake()` 投的是**唤醒令牌**，不许被当成「用户刚动过」把热档续上。
3. ⭐ **不可见 = 只停"画"**：不可见时不渲染、不起跑动画，数据照收（`_history` 照长）；
   `screensDidSleep` 与 `occlusionState` 都算不可见。
4. ⭐ **恢复可见一帧画完**：`_history` 直接落位，**没有**任何动画在跑（跳过积压）。
5. 后台线程：`_Latest.stop()` 能叫醒阻塞的 `take()`；`DraftTranslator` 阻塞等 + `stop()`；
   声卡回调会叫醒主循环；`_WakeQueue` put 后自动 wake。
6. 接线（AST）：不可见时 `partial_worker` 跳过草稿识别、收尾投哨兵、报告写 `hidden_s`。

⚠️ 写端：真 `Overlay` 会在用户拖过窗口时写 `.window`；这里照旧备份还原（同 test_panel）。
"""
from __future__ import annotations

import ast
import pathlib
import queue
import sys
import threading
import time
import unittest

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))


# ══════════════════════════════════════════════════════════════════════
# 一、纯逻辑：让步分档（不需要窗口）
# ══════════════════════════════════════════════════════════════════════
def _clear_motion(ov) -> None:
    """清掉所有「在动」的旗标（含启动时那一次顶栏到点）。**唯一**一份清单。"""
    ov._tv._roll_t0 = None
    ov._draft_fade_t0 = None
    ov._draft_rev = {}
    ov._dirty = ov._urgent = False
    ov._tv._dirty = ov._tv._urgent = False
    ov._bar_hide_at = None


class PumpWaitTiers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
            NSApplication.sharedApplication().setActivationPolicy_(
                NSApplicationActivationPolicyAccessory)
        except Exception as e:                       # noqa: BLE001
            raise unittest.SkipTest(f"AppKit 不可用: {type(e).__name__}: {e}")

    def setUp(self):
        import overlay
        self.O = overlay
        self.ov = overlay.Overlay()                  # 不 show()：只测纯决策
        self.now = time.monotonic()

    def tearDown(self):
        self.ov._closed = True

    def _wait(self):
        return self.ov._pump_wait_s(self.now)

    def _settle(self):
        """清掉所有「在动」的旗标（含启动时那一次顶栏到点）。"""
        _clear_motion(self.ov)
        self.ov._last_ev_t = 0.0

    def test_什么都没在动_是长档(self):
        self._settle()
        self.assertFalse(self.ov._motion_active(), "刚构造、什么都没在动")
        self.assertEqual(self._wait(), self.O.PUMP_IDLE_LONG_S)
        self.assertGreater(self.O.PUMP_IDLE_LONG_S, self.ov._hot_wait_s(),
                           "空闲档必须比热档长，否则这条省电等于没做")

    def test_每种在动的都会退回热档(self):
        """`_motion_active()` 的每一条都在这里 —— 漏一条 = 那个动画被拖慢。"""
        cases = {}

        def roll():
            self.ov._tv._roll_t0 = self.now

        def fade():
            self.ov._draft_fade_t0 = self.now

        def rev():
            self.ov._draft_rev["draft"] = ("x", [], self.now)

        def dirty():
            self.ov._dirty = True

        def urgent():
            self.ov._urgent = True

        def tv_dirty():
            self.ov._tv._dirty = True

        def bar():
            self.ov._bar_hide_at = self.now + 1.0

        cases["上滚/合并位移"] = roll
        cases["草稿让位淡出"] = fade
        cases["草稿改字淡换"] = rev
        cases["主窗脏(流式增量/答案打字/定稿)"] = dirty
        cases["主窗 urgent"] = urgent
        cases["转录区脏"] = tv_dirty
        cases["顶栏到点要淡出"] = bar
        for name, arm in cases.items():
            with self.subTest(name):
                self._settle()
                self.assertEqual(self._wait(), self.O.PUMP_IDLE_LONG_S, "清干净后应是长档")
                arm()
                self.assertTrue(self.ov._motion_active(), f"{name}: 没被认成『在动』")
                # 热档 = 屏幕刷新周期（不是写死的 PUMP_IDLE_S）
                self.assertEqual(self._wait(), self.ov._hot_wait_s(), f"{name}: 没退回热档")
                self.assertLess(self._wait(), self.O.PUMP_IDLE_LONG_S, f"{name}: 退回的是长档，不是热档")

    def test_手势窗口内是零等待(self):
        self.ov._last_ev_t = self.now            # 刚派发过事件
        self.assertEqual(self._wait(), 0.0)

    def test_不可见时是隐藏档_且优先于手势档(self):
        self.ov._last_ev_t = self.now
        self.ov._visible = False
        self.assertEqual(self._wait(), self.O.PUMP_HIDDEN_S)
        self.assertGreaterEqual(self.O.PUMP_HIDDEN_S, self.O.PUMP_IDLE_LONG_S)


# ══════════════════════════════════════════════════════════════════════
# 一·b、热档让步跟随**屏幕刷新率**（桩屏幕，不需要真窗口）
# ══════════════════════════════════════════════════════════════════════
class _StubScreen:
    """桩屏幕：只实现 `maximumFramesPerSecond()`。`raises` = 让它抛。"""

    def __init__(self, hz=120.0, raises=False):
        self.hz, self.raises, self.calls = hz, raises, 0

    def __repr__(self):
        return f"Screen({self.hz!r}{' raises' if self.raises else ''})"

    def maximumFramesPerSecond(self):
        self.calls += 1
        if self.raises:
            raise RuntimeError("no display")
        return self.hz


class HotWaitFollowsScreen(unittest.TestCase):
    """⭐ 3.9.7 追加（作者）：热档让步 = `1 / 屏幕.maximumFramesPerSecond()`，别再写死 8 ms。

    ⚠️ 60 Hz 的 MacBook Air 上 8 ms 让步让 pump 跑 ~125 Hz（比面板快一倍，白烧 CPU）；
       120 Hz 屏上退到 16.7 ms 又每帧晚半拍。桩屏幕驱动 `panel.screen()` —— 与真窗口无关。
    """

    @classmethod
    def setUpClass(cls):
        try:
            from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
            NSApplication.sharedApplication().setActivationPolicy_(
                NSApplicationActivationPolicyAccessory)
        except Exception as e:                       # noqa: BLE001
            raise unittest.SkipTest(f"AppKit 不可用: {type(e).__name__}: {e}")

    def setUp(self):
        import overlay
        self.O = overlay
        self.ov = overlay.Overlay()                  # 不 show()：只测纯决策

        class Panel:
            """桩面板：`screen()` 返回我们给的桩屏幕。"""

            def __init__(self, screen):
                self._s = screen

            def screen(self):
                return self._s

        self.Panel = Panel

    def tearDown(self):
        self.ov._closed = True

    def _use(self, screen):
        """换屏 = 换掉 `screen()` 的返回值（真机上此时 NSScreen 对象身份也会变）。"""
        self.ov._panel = self.Panel(screen)

    def test_60Hz_是16_7ms(self):
        self._use(_StubScreen(60))
        self.assertAlmostEqual(self.ov._hot_wait_s(), 1 / 60, places=9)

    def test_120Hz_是8_33ms(self):
        self._use(_StubScreen(120))
        self.assertAlmostEqual(self.ov._hot_wait_s(), 1 / 120, places=9)

    def test_坏值一律退回兜底(self):
        """0 / 负 / NaN / inf / 抛异常 / 拿不到屏幕 → `PUMP_IDLE_S`（宁可多刷，不可少刷）。"""
        for s in (_StubScreen(0), _StubScreen(-1), _StubScreen(float("nan")),
                  _StubScreen(float("inf")), _StubScreen(raises=True), None):
            with self.subTest(screen=repr(s)):
                self._use(s)
                self.assertEqual(self.ov._hot_wait_s(), self.O.PUMP_IDLE_S)

    def test_换屏后间隔跟着变(self):
        """60 Hz 屏 → 120 Hz 屏 → 没有屏幕：每一跳间隔都要跟着变。"""
        self._use(_StubScreen(60))
        self.assertAlmostEqual(self.ov._hot_wait_s(), 1 / 60, places=9)
        self._use(_StubScreen(120))
        self.assertAlmostEqual(self.ov._hot_wait_s(), 1 / 120, places=9)
        self._use(None)
        self.assertEqual(self.ov._hot_wait_s(), self.O.PUMP_IDLE_S)

    def test_同一块屏只读一次刷新率(self):
        """⚠️ 备忘：`screen()` 每轮现读（不会漏换屏），但派生周期按**对象身份**只算一次。"""
        s = _StubScreen(120)
        self._use(s)
        for _ in range(5):
            self.ov._hot_wait_s()
        self.assertEqual(s.calls, 1, f"同一块屏读了 {s.calls} 次刷新率（没按身份备忘）")

    def test_热档走runUntilDate_不当成长档(self):
        """⚠️ 关键回归：判据**不能**拿让步值去跟 `PUMP_IDLE_S`(0.008) 比大小 ——
        60 Hz 的热档是 0.0167 > 0.008，一比就把它误判成长档、走可打断等待（120/60 分叉）。"""
        self._use(_StubScreen(60))
        _clear_motion(self.ov)
        self.ov._last_ev_t = 0.0
        self.ov._dirty = True                     # 有东西在动
        seen = []
        self.ov._wait_interruptible = lambda *a, **k: seen.append(a)
        self.ov._yield_to_runloop(None, "NSDefaultRunLoopMode", time.monotonic())
        self.assertEqual(seen, [], "60 Hz 的热档被当成长档、走了可打断等待")
        self.assertEqual(self.ov._pump_tier(time.monotonic()), self.O.WAIT_HOT)

    def test_空闲档才走可打断等待(self):
        """反向：什么都不动 → 长档，并且**确实**走 `_wait_interruptible`（省电那一步）。"""
        self._use(_StubScreen(120))
        _clear_motion(self.ov)
        self.ov._last_ev_t = 0.0
        seen = []
        self.ov._wait_interruptible = lambda *a, **k: seen.append(a)
        self.ov._yield_to_runloop(None, "NSDefaultRunLoopMode", time.monotonic())
        self.assertEqual([a[2] for a in seen], [self.O.PUMP_IDLE_LONG_S],
                         "空闲档没走可打断等待")


# ══════════════════════════════════════════════════════════════════════
# 二、真实窗口：wake / 隐藏 / 恢复
# ══════════════════════════════════════════════════════════════════════
class _WinCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
        NSApplication.sharedApplication().setActivationPolicy_(
            NSApplicationActivationPolicyAccessory)
        f = HERE / ".window"
        cls._saved = f.read_bytes() if f.exists() else None

    @classmethod
    def tearDownClass(cls):
        f = HERE / ".window"
        if cls._saved is not None:
            f.write_bytes(cls._saved)
        elif f.exists():
            f.unlink()

    def setUp(self):
        import overlay
        self.O = overlay
        self.ov = overlay.Overlay()
        self.ov.show()
        # `occlusionState` 要跑一轮 run loop 才反映真实可见性 —— 先给它一轮
        self.ov.pump()

    def tearDown(self):
        self.ov.close()


class WakeInterrupts(_WinCase):
    def _idle(self):
        self.ov._tv._roll_t0 = None
        self.ov._draft_fade_t0 = None
        self.ov._draft_rev = {}
        self.ov._dirty = self.ov._urgent = False
        self.ov._tv._dirty = self.ov._tv._urgent = False
        self.ov._bar_hide_at = None
        self.ov._last_ev_t = 0.0
        # ⚠️ `_tv.tick()` 会因「视口位置 != 期望位置」把自己弄脏（首次总是不等）→
        #    那时让步会落回热档，量出来的是 8ms 而不是长档。先把期望值对齐。
        self.ov._tv._expected_origin = self.ov._tv._scroll.contentView().bounds().origin.y

    def _drain_app_events(self):
        from AppKit import (NSApplication, NSEventMaskAny, NSDefaultRunLoopMode)
        app = NSApplication.sharedApplication()
        mode = NSDefaultRunLoopMode
        while app.nextEventMatchingMask_untilDate_inMode_dequeue_(
                NSEventMaskAny, None, mode, True) is not None:
            pass

    def test_可打断的等待_被wake立刻叫醒(self):
        """⭐ 本题的**全部意义**：省电不能变成「字幕晚 0.1 s 上屏」。

        ⚠️ mask 只留 `ApplicationDefined`：环境里总有零星系统事件（实测本机 ~0.07 s
           来一个），不隔离的话「没 wake 也会早退」—— 那条判据就恒真、抓不住东西。
        """
        from AppKit import (NSApplication, NSDefaultRunLoopMode,
                            NSEventMaskApplicationDefined)
        ov = self.ov
        app = NSApplication.sharedApplication()
        mode = NSDefaultRunLoopMode
        self._drain_app_events()
        threading.Thread(target=lambda: (time.sleep(0.05), ov.wake()), daemon=True).start()
        t0 = time.monotonic()
        ov._wait_interruptible(app, mode, 0.4, mask=NSEventMaskApplicationDefined)
        dt = time.monotonic() - t0
        self.assertGreaterEqual(dt, 0.03, f"压根没等（{dt:.3f}s）？叫醒的是不是这一轮")
        # ⚠️ 改坏：把 `_wait_interruptible` 换回 `runUntilDate_` / `wake()` 空实现 → 0.4s → 红。
        self.assertLess(dt, 0.25, f"等待没被打断（睡了 {dt:.3f}s）—— 新数据要等满这一档才上屏")

    def test_泵那一轮也不睡满(self):
        ov = self.ov
        self._idle()
        self.assertTrue(ov.visible, "前提：窗口看得见（否则走的是隐藏档）")
        self.assertEqual(ov._pump_wait_s(time.monotonic()), self.O.PUMP_IDLE_LONG_S,
                         "前提：此刻是长档（什么都没在动）")
        self._drain_app_events()
        threading.Thread(target=lambda: (time.sleep(0.05), ov.wake()), daemon=True).start()
        t0 = time.monotonic()
        ov.pump()
        dt = time.monotonic() - t0
        self.assertLess(dt, self.O.PUMP_IDLE_LONG_S * 0.85,
                        f"pump 睡满了（{dt:.3f}s）—— wake() 没接进 pump")

    def test_唤醒令牌不算用户事件(self):
        """令牌要**丢掉**、且不算「用户刚动过」（用**假 app** 钉，不受环境事件干扰）。"""
        import overlay
        from AppKit import (NSEvent, NSEventTypeApplicationDefined, NSApplication,
                            NSDefaultRunLoopMode)
        app = NSApplication.sharedApplication()
        token = NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
            NSEventTypeApplicationDefined, (0, 0), 0, 0, 0, None, overlay.WAKE_SUBTYPE, 0, 0)
        self.assertTrue(overlay._is_wake_event(token), "令牌的 subtype 认不出来")

        class FakeApp:
            def __init__(self, events):
                self._ev = list(events)
                self.sent: list = []

            def nextEventMatchingMask_untilDate_inMode_dequeue_(self, *a):
                return self._ev.pop(0) if self._ev else None

            def sendEvent_(self, ev):
                self.sent.append(ev)

        mode = NSDefaultRunLoopMode
        fa = FakeApp([token])
        # ⚠️ 改坏：`_drain_events` 不丢令牌 → busy=True + 令牌被派发 → 两处红。
        self.assertFalse(self.ov._drain_events(fa, mode), "令牌被当成用户事件了")
        self.assertEqual(fa.sent, [], "令牌被 sendEvent_ 派出去了")
        fa2 = FakeApp([token, token])
        self.assertFalse(self.ov._drain_events(fa2, mode))
        self.assertEqual(len(fa2.sent), 0)
        self.assertTrue(overlay._is_wake_event(token) and not overlay._is_wake_event(None))


class HiddenStopsDrawing(_WinCase):
    def test_藏起来_不渲染不起跑_数据照收(self):
        ov = self.ov
        ov._set_hidden(True)
        self.assertFalse(ov.visible)
        self.assertEqual(ov._pump_wait_s(time.monotonic()), self.O.PUMP_HIDDEN_S)
        renders = []
        real = ov._render
        ov._render = lambda: (renders.append(1), real())[1]
        n0 = len(ov._history)
        ov.add_draft("hidden draft")
        ov.finalize("A hidden caption.", "一条看不见的字幕。")
        ov.pump()
        self.assertEqual(renders, [], "不可见时还在画")
        self.assertEqual(len(ov._history), n0 + 1, "不可见不能拦数据 —— 它必须照收")
        self.assertIsNone(ov._tv._roll_t0, "不可见时起了上滚动效（没人看得见）")
        self.assertTrue(ov._dirty, "脏标记要留着，恢复那一帧画它")

    def test_恢复可见_一帧画完_没有动画在跑(self):
        ov = self.ov
        ov._set_hidden(True)
        texts = []
        for i in range(4):
            t = f"caption number {i}"
            texts.append(t)
            ov.finalize(t, f"字幕 {i}。")
        ov.pump()                                 # 不可见：什么都不画
        ov._set_hidden(False)
        # 恢复那一帧（`_set_hidden(False)` 内部已经调过 `_land_everything`）
        self.assertTrue(ov.visible)
        self.assertIsNone(ov._tv._roll_t0, "恢复时还在跑积压的上滚")
        self.assertIsNone(ov._draft_fade_t0, "恢复时还在跑积压的草稿淡出")
        self.assertEqual(ov._draft_rev, {}, "恢复时还在跑积压的改字淡换")
        self.assertFalse(ov._dirty, "恢复那一帧没画（脏标记还挂着）")
        self.assertFalse(ov._no_animate)
        self.assertFalse(ov._draft_rev, "恢复时还在跑积压的改字淡换")
        # 恢复后不该再有**动画**在跑（顶栏到点淡出是 TTL，不算动画 —— 它可能还在挂着）
        ov._bar_hide_at = None
        self.assertFalse(ov._motion_active(), "恢复后还有动画在逐帧跑")        # 屏上就是最终状态（最后一句在，行数对得上）
        flat = " ".join(t for row in ov._tv._items for t in row if t)
        self.assertIn("caption number 3", flat, "恢复那一帧没有把当前 _history 画全")
        self.assertEqual(len(ov._tv._items), len(ov._history))

    def test_不可见时长累计_含进行中的那一段(self):
        ov = self.ov
        ov._set_hidden(True)
        ov._hidden_t0 = time.monotonic() - 2.0    # 假装藏了 2 秒
        self.assertGreaterEqual(ov.hidden_seconds, 2.0)
        ov._set_hidden(False)
        # ⚠️ 改坏（恢复时不结掉/不折进累计）→ 下面两条红。
        self.assertIsNone(ov._hidden_t0, "恢复后那一段应当已经结掉")
        self.assertGreaterEqual(ov.hidden_seconds, 2.0, "恢复后累计时长丢了")

    def test_屏幕睡眠算不可见_醒来落位(self):
        ov = self.ov
        ov._set_screens_sleeping(True)
        self.assertFalse(ov.visible)
        self.assertEqual(ov._pump_wait_s(time.monotonic()), self.O.PUMP_HIDDEN_S)
        ov._set_screens_sleeping(False)
        self.assertTrue(ov.visible)
        self.assertFalse(ov._dirty, "醒来那一帧应当已经画过")

    def test_被完全遮挡算不可见_部分遮挡不算(self):
        """真遮挡测里造不出来 —— 用**桩面板**驱动 `occlusionState` 那一条分支。

        ⚠️ 判据是 Apple 的语义：`Visible` 位在 = 看得见；部分遮挡**仍带**这一位，
           只有**完全**被盖住才丢。改坏（不看 occlusionState）→ 第一条红。
        """
        ov = self.ov
        from AppKit import NSWindowOcclusionStateVisible as VIS
        real = ov._panel

        class Fake:
            def __init__(self, occ):
                self._o = occ

            def occlusionState(self):
                return self._o

        try:
            ov._presented_t = time.monotonic() - 5.0   # 越过 occlusionState 的更新延迟
            ov._panel = Fake(0)                    # 完全被遮挡（没有 Visible 位）
            ov._refresh_visibility()
            self.assertFalse(ov.visible, "完全被遮挡时还在画")
            ov._panel = Fake(VIS)                  # 只是部分被盖住
            ov._refresh_visibility()
            self.assertTrue(ov.visible, "部分遮挡不该停渲染（Apple 的语义是仍可见）")
        finally:
            ov._panel = real

    def test_没上过屏的窗口_照常渲染(self):
        """`show()` 之前的 `occlusionState` 恒为「不可见」—— 不许据此停渲染。

        ⚠️ 改坏（拿 occlusionState 直接判可见性、不看 `_presented`）→ 红：
           测试里直接构造的 Overlay 会一帧都不画，`_tv_mode` 停在 "cap"。
        """
        import overlay
        o = overlay.Overlay()                      # 不 show()
        try:
            self.assertTrue(o._compute_visible(), "没上过屏就被判成『用户藏起来了』")
            o.finalize("Hello.", "你好。")
            self.assertFalse(o._dirty, "没上过屏就该照常渲染")
        finally:
            o._closed = True


# ══════════════════════════════════════════════════════════════════════
# 三、后台线程（离线，不起真设备）
# ══════════════════════════════════════════════════════════════════════
class LatestStop(unittest.TestCase):
    def test_stop_叫醒阻塞的take(self):
        import main as M
        latest = M._Latest()
        got = {}

        def consumer():
            got["buf"] = latest.take()             # timeout=None：一直等
            got["stopped"] = latest.stopped

        th = threading.Thread(target=consumer, daemon=True)
        th.start()
        time.sleep(0.2)
        self.assertTrue(th.is_alive(), "`take()` 不该空转返回 —— 它要阻塞等")
        latest.stop()
        th.join(1.0)
        self.assertFalse(th.is_alive(), "`stop()` 没叫醒 `take()`")
        self.assertIsNone(got["buf"])
        self.assertTrue(got["stopped"])

    def test_put_仍然叫醒(self):
        import main as M
        latest = M._Latest()
        latest.put(b"x", 3)
        self.assertEqual(latest.take(), b"x")
        self.assertEqual(latest.taken_tag, 3)


class DraftTranslatorStopAndPause(unittest.TestCase):
    @staticmethod
    def _make(calls, **kw):
        import draft_translate as D
        return D.DraftTranslator(
            translate=lambda text, on_zh, ctx: (calls.append(text), on_zh("中"))[1],
            output=lambda s, g, z: None, mode_ok=lambda: True,
            should_yield=lambda: False, context=lambda: "", stats={}, **kw)

    def test_run_阻塞等_不停机不空醒(self):
        calls: list = []
        t = self._make(calls)
        seen: list = []
        real_step = t.step

        def spy(timeout: float = 0.3) -> bool:
            seen.append(timeout)
            return real_step(timeout)

        t.step = spy
        ev = threading.Event(); ev.set()
        th = threading.Thread(target=t.run, args=(ev,), daemon=True)
        th.start()
        time.sleep(0.35)
        self.assertTrue(th.is_alive())
        self.assertEqual(calls, [], "队列空时不该做任何事（更不该空醒后再调翻译）")
        # ⚠️ `timeout=None` = 阻塞等（3.9.7 省电）。改坏（回到 `step(0.3)`，3.3 次/秒空醒）→ 红。
        self.assertEqual(seen, [None], f"run() 不是阻塞等：超时参数为 {seen}")
        t.stop()
        th.join(1.0)
        self.assertFalse(th.is_alive(), "`stop()` 没让 run() 收摊")

    def test_paused_不发请求(self):
        calls: list = []
        t = self._make(calls, paused=lambda: True)
        t.submit(1, 0, "some draft text")
        self.assertTrue(t.step(0))
        self.assertEqual(calls, [], "窗口不可见时还在发草稿翻译请求")

    def test_stop_哨兵被_step_认出(self):
        import draft_translate as D
        calls: list = []
        t = self._make(calls)
        t._q.put_nowait(D._STOP)                   # 只投哨兵（不调 stop()，免得它先把 _stopped 置上）
        self.assertFalse(t._stopped)
        self.assertTrue(t.step(0))
        # ⚠️ 改坏（step 不认哨兵）→ `_stopped` 恒 False → 红。
        self.assertTrue(t._stopped)


class WakeQueueAndCapture(unittest.TestCase):
    def test_wake_queue_put后自动wake_没装waker时是普通Queue(self):
        import main as M
        hits: list = []
        q = M._WakeQueue()
        q.put(("final", 1))
        q.put_nowait(("final", 2))
        self.assertEqual(hits, [], "没装 waker 时不该有任何副作用")
        self.assertEqual(q.get_nowait(), ("final", 1))
        self.assertFalse(q.empty())
        q.set_waker(lambda: hits.append(1))
        q.put(("final", 3))
        self.assertEqual(hits, [1])
        q.put_nowait(("final", 4))
        self.assertEqual(hits, [1, 1])
        self.assertEqual(q.qsize(), 3)

    def test_waker抛异常不影响入队(self):
        import main as M
        q = M._WakeQueue()
        q.set_waker(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        q.put("x")                                 # 不许抛
        self.assertEqual(q.get_nowait(), "x")

    def test_声卡回调会叫醒主循环(self):
        import capture
        import numpy as np
        calls: list = []
        src = object.__new__(capture.CallbackSource)   # 不 __init__：不开真设备
        src._q = queue.Queue(maxsize=capture.QUEUE_MAX)
        src._waker = lambda: calls.append(1)
        src._cb(np.zeros(capture.CHUNK, dtype=np.float32), capture.CHUNK, None, None)
        self.assertEqual(calls, [1], "新块到达没叫醒主循环")
        self.assertEqual(src._q.qsize(), 1)

    def test_归一化源转发set_waker(self):
        import capture
        seen = []
        inner = type("Inner", (), {"poll": lambda self: None, "is_done": lambda self: False,
                                   "close": lambda self: None,
                                   "set_waker": lambda self, fn: seen.append(fn)})()
        capture._NormalizedSource(inner).set_waker("W")
        self.assertEqual(seen, ["W"])

    def test_文件源按绝对时刻表出块_主循环慢也不累积落后(self):
        """⚠️ 10-17 实测：浮窗空闲档每轮睡 0.1 s，旧写法 `next_due = now + 0.1` 把每轮多出的
        时间累积成落后 —— 90 s 片段落后 2.6 s 还在涨，demo 声音和字幕越走越不同步。"""
        import capture
        import numpy as np
        from unittest import mock
        clock = {"t": 100.0}
        with mock.patch.object(capture.time, "monotonic", lambda: clock["t"]):
            src = capture.FileSource(samples=np.zeros(capture.CHUNK * 50, dtype=np.float32))
            got = 0
            for _ in range(30):                       # 主循环每 0.13 s 才来一次（比一块 0.1 s 慢）
                if src.poll() is not None:
                    got += 1
                clock["t"] += 0.13
            # 3.9 s 墙钟：落后的块下一轮立刻补出（每轮一块，追得上）→ 30 轮出 30 块（旧写法也 30，
            # 但它的「应出时刻」一直往后推）；判据看**应出时刻**有没有被推走：
            self.assertLessEqual(src._next_due, 100.0 + 30 * capture.CHUNK / capture.SR + 1e-9)

    def test_文件源每块到期叫醒主循环(self):
        import capture
        import numpy as np
        import threading
        import time as _t
        n = {"k": 0}
        ev = threading.Event()

        def wake():                                   # 替身主循环：被叫醒就取一块
            n["k"] += 1
            src.poll()
            if n["k"] >= 3:
                ev.set()
        src = capture.FileSource(samples=np.zeros(capture.CHUNK * 50, dtype=np.float32))
        src.set_waker(wake)
        t0 = _t.monotonic()
        self.assertTrue(ev.wait(2.0))                 # 每到一块的应出时刻叫一次（3 块 ≈ 0.2 s）
        self.assertLess(_t.monotonic() - t0, 0.6)
        src.close()
        k = n["k"]
        _t.sleep(0.25)
        self.assertEqual(n["k"], k)                   # close 之后不再叫


# ══════════════════════════════════════════════════════════════════════
# 四、接线（AST：闭包跑不到，钉住「改法」）
# ══════════════════════════════════════════════════════════════════════
def _func(name: str):
    tree = ast.parse((HERE / "main.py").read_text(encoding="utf-8"))
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    raise AssertionError(f"main.py 里找不到 {name}")


def _run_src() -> str:
    return ast.unparse(_func("run"))


class Wiring(unittest.TestCase):
    def test_partial_worker_不可见时跳过草稿识别并计数(self):
        src = ast.unparse(_func("partial_worker"))
        self.assertIn("if _ui_invisible():", src, "不可见时没有跳过草稿识别")
        self.assertIn("draft_skipped['n'] += 1", src, "跳过了却没计数（报告里看不出来）")
        self.assertIn("draft.reset()", src, "恢复后没有把草稿流干净重开")
        self.assertIn("_skip_seg", src, "恢复后没有『从下一个段开始』")

    def test_草稿译文线程挂了不可见开关(self):
        run = _run_src()
        self.assertIn("paused=_draft_tr_paused", run, "草稿译文线程没接不可见的开关")
        self.assertIn("draft_skipped['zh'] += 1", run)

    def test_收尾投哨兵_叫醒所有阻塞的worker(self):
        """`running.clear()` 那一段必须投哨兵 —— 否则阻塞等的新写法会挂到进程退出。"""
        run = _run_src()
        self.assertIn("for _q in (properq, answerq, hintq, mergeq):", run)
        self.assertIn("latest.stop()", run)
        self.assertIn("draft_tr.stop()", run)
        self.assertIn("_q.put_nowait(None)", run)

    def test_final_worker_空转改成阻塞等(self):
        src = ast.unparse(_func("final_worker"))
        self.assertNotIn("time.sleep(0.2)", src, "final_worker 还在 0.2 s 硬轮询")
        self.assertIn("finalq.get(timeout=", src, "没改成阻塞等")

    def test_其余worker不再定时轮询(self):
        for name in ("proper_worker", "answer_worker", "hint_worker", "merge_worker"):
            with self.subTest(name):
                src = ast.unparse(_func(name))
                self.assertNotIn("timeout=0.5", src)
                self.assertNotIn("timeout=0.2", src)

    def test_报告里记了不可见时长与跳过的草稿数(self):
        src = _run_src()
        self.assertIn("note_ui_value('hidden_s'", src)
        self.assertIn("note_ui_value('draft_skipped_hidden'", src)
        self.assertIn("note_ui_value('draft_zh_skipped_hidden'", src)

    # ---- ⭐ 3.9.8 新采的接线（闭包跑不到，钉住「改法」）----
    def test_streamq装上唤醒延迟统计_只在测试模式(self):
        """⚠️ 变异验证：把 `streamq.set_stats(...)` 那三行删掉 → 红（`②` 永远采不到）。"""
        src = _run_src()
        self.assertIn("streamq.set_stats(tester.note_streamq_dwell)", src)

    def test_overlay四个采集钩子都接了(self):
        """⚠️ 变异验证：少接一个（比如漏 `on_view`）→ 对应那条红（那格永远空）。"""
        run = _run_src()
        for kw in ("on_vis=tester.on_vis", "on_wait=tester.add_wait",
                   "on_view=tester.note_view", "on_power_event=tester.note_event"):
            self.assertIn(kw, run, f"`_load_overlay(...)` 没接 {kw}")

    def test_收尾写了energy与capture块(self):
        run = _run_src()
        self.assertIn("note_block('energy'", run)
        self.assertIn("note_block('capture'", run)

    def test_合并与补译的原文接了(self):
        self.assertIn("tester.note_merge_item",
                      ast.unparse(_func("drain")), "drain 的 replace 分支没记合并原文")
        self.assertIn("tester.note_backfill_items",
                      ast.unparse(_func("_backfill_for")), "补译原文没记")

    def test_定稿翻译的idle_before_ms接了(self):
        self.assertIn("_tr_idle.append(_idle_ms)", ast.unparse(_func("_emit")))
        self.assertIn("idle_before_ms=", ast.unparse(_func("drain")))


class ReportBlockNames(unittest.TestCase):
    def test_ui块里那几格能写进去(self):
        """`note_ui_value` 写的名字进的是 `blocks.ui`（快照），不是新 block。"""
        import testmode
        self.assertIn("ui", testmode.BLOCKS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
