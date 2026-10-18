#!/usr/bin/env python3
"""**细滚动条**（作者 2026-10-18：面板里那条竖向滚动条太粗，要按最新的高级设计规范改细）。

    ClassLive.app/Contents/MacOS/python tests/test_thin_scroller.py

## 钉住什么

1. ⭐ **几何**：静止 3pt / 悬停 6pt（拖动同悬停）；滑块高度按「视口/文档」比例、但有下限；
   位置从带顶（文档开头）到带底（文档末尾）。
2. ⭐ **可见性**：不滚不悬停 → **不画**（不透明度归零）；**用户在滚**（live scroll）
   或悬停/拖动 → 亮起来，停下 `THIN_IDLE_S` 后自己淡出。
   程序化滚动（面板自己刷列表）**不许**把它闪出来。
3. ⭐ **系统那条不许回来**（`hasVerticalScroller()` 必须关着）+ 样式通知来了要把
   滚动视图按回 overlay（用户改「显示滚动条」/插鼠标时，AppKit 会自己改回去）。
4. ⭐ **输入三条路**：拖动（真的滚）、点轨道（翻页）、滚轮（交还给滚动视图，不截流）。
5. ⭐ 「增大对比度」跟着变粗；藏着时**不吃点击**（`hitTest_` 返回 None）。
6. 像素级复核：把滑块那层渲染成位图，量**画出来**的宽度（不是只比常量）。

⚠️ 语料是合成的（没有真实课堂文本）；面板建在隔离 `HOME` / 临时目录里，不写任何真实路径。
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import panel                                                       # noqa: E402

CODES = ["ECON10790", "ECON10730", "ECON10740", "SOC10020", "COMP10030",
         "MATH10050", "PHYS10010", "CHEM10020", "STAT10030", "HIST10040"]


def _strip_of(scroll_view):
    """滚动视图里那个细滚动条（找不到回 `None`）。"""
    for v in scroll_view.subviews():
        if type(v).__name__.endswith("ThinScroller"):
            return v
    return None


def _layer_pixels(layer, size, scale: float = 2.0):
    """把图层树渲染成位图 —— 量**画出来**的滑块（`cacheDisplay` 看不到子层）。"""
    from AppKit import (NSBitmapImageRep, NSGraphicsContext, NSDeviceRGBColorSpace)
    from Quartz import CGContextScaleCTM
    w, h = max(1, int(round(size[0] * scale))), max(1, int(round(size[1] * scale)))
    rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, w, h, 8, 4, True, False, NSDeviceRGBColorSpace, w * 4, 32)
    ctx = NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.setCurrentContext_(ctx)
    try:
        CGContextScaleCTM(ctx.CGContext(), scale, scale)
        layer.renderInContext_(ctx.CGContext())
    finally:
        NSGraphicsContext.restoreGraphicsState()
    return bytes(rep.bitmapData()), w, h, rep.bytesPerRow(), int(rep.samplesPerPixel())


def _thumb_span(view, *, thr: int = 24, scale: float = 2.0):
    """滑块在渲染出来的位图里占的列区间 → `(宽 px, 宽 pt)`；没有则 `(0, 0.0)`。"""
    buf, w, h, bpr, spp = _layer_pixels(view.layer(), view.bounds().size, scale)
    cols = []
    for x in range(w):
        for y in range(h):
            i = y * bpr + x * spp
            if max(buf[i:i + spp]) > thr:                  # ⚠️ 通道顺序别猜（ARGB/RGBA 都可能）
                cols.append(x)
                break
    if not cols:
        return 0, 0.0
    n = max(cols) - min(cols) + 1
    return n, n / scale


# ══════════════════════════════════════════════════════════════════════
# 一、纯函数 / 状态机（不碰窗口）
# ══════════════════════════════════════════════════════════════════════
class 纯函数(unittest.TestCase):
    def test_宽度两档(self):
        self.assertAlmostEqual(panel.thin_width(hovered=False), 3.0)
        self.assertAlmostEqual(panel.thin_width(hovered=True), 6.0)
        self.assertGreater(panel.thin_width(hovered=True), panel.thin_width(hovered=False))

    def test_增大对比度加粗一档(self):
        self.assertGreater(panel.thin_width(hovered=False, contrast=True),
                           panel.thin_width(hovered=False))
        self.assertGreater(panel.thin_width(hovered=True, contrast=True),
                           panel.thin_width(hovered=True))
        # 只加粗不加深 = 只做了半件事（对比度那一档还要更实）
        self.assertGreater(panel.thin_alpha(hovered=False, contrast=True),
                           panel.thin_alpha(hovered=False))
        self.assertGreater(panel.thin_alpha(hovered=True, contrast=True),
                           panel.thin_alpha(hovered=True))

    def test_高度按比例且有下限(self):
        h = panel.thin_thumb_h(doc_h=2_000.0, clip_h=1_000.0, strip_h=400.0)
        self.assertAlmostEqual(h, 200.0, places=1, msg="半页内容 → 半条带子")
        self.assertAlmostEqual(panel.thin_thumb_h(doc_h=1_000_000.0, clip_h=1_000.0,
                                                  strip_h=400.0), panel.THIN_MIN_H,
                               msg="内容极长 → 到下限为止（别画成发丝）")

    def test_位置从带顶到带底(self):
        strip, thumb = 400.0, 100.0
        self.assertAlmostEqual(panel.thin_thumb_y(frac=0.0, strip_h=strip, thumb_h=thumb),
                               strip - thumb, msg="文档开头 → 贴带顶")
        self.assertAlmostEqual(panel.thin_thumb_y(frac=1.0, strip_h=strip, thumb_h=thumb), 0.0,
                               msg="文档末尾 → 贴带底")
        self.assertAlmostEqual(panel.thin_thumb_y(frac=0.5, strip_h=strip, thumb_h=thumb),
                               (strip - thumb) / 2.0)

    def test_拖动的反函数是逆运算(self):
        strip, thumb = 400.0, 120.0
        for frac in (0.0, 0.13, 0.5, 0.87, 1.0):
            y = panel.thin_thumb_y(frac=frac, strip_h=strip, thumb_h=thumb)
            self.assertAlmostEqual(
                panel.thin_frac_for_y(knob_bottom=y, strip_h=strip, thumb_h=thumb), frac,
                places=6)

    def test_内容不比视口长就不画(self):
        self.assertIsNone(panel.thin_thumb_rect(doc_h=400.0, clip_h=400.0, origin_y=0.0,
                                                doc_top=0.0, strip_w=16.0, strip_h=400.0,
                                                width=3.0))
        self.assertIsNotNone(panel.thin_thumb_rect(doc_h=800.0, clip_h=400.0, origin_y=0.0,
                                                   doc_top=400.0, strip_w=16.0, strip_h=400.0,
                                                   width=3.0))

    def test_滑块贴右缘且内缩固定(self):
        r = panel.thin_thumb_rect(doc_h=800.0, clip_h=400.0, origin_y=0.0, doc_top=400.0,
                                  strip_w=16.0, strip_h=400.0, width=6.0)
        self.assertAlmostEqual(r[0] + r[2], 16.0 - panel.THIN_INSET, places=3,
                               msg="滑块右缘到带子右缘的距离该是 THIN_INSET")

    def test_状态机三段(self):
        m = panel._Thin(doc_h=800.0, clip_h=400.0, strip_w=16.0, strip_h=400.0)
        self.assertAlmostEqual(m.alpha(), 0.0, msg="没滚没悬停 → 不画")
        m.shown = True
        self.assertAlmostEqual(m.alpha(), panel.THIN_ALPHA)
        m.hovered = True
        self.assertAlmostEqual(m.alpha(), panel.THIN_ALPHA_HOVER)
        self.assertAlmostEqual(m.width(), 6.0, msg="悬停更粗")
        m.dragging = True
        m.hovered = False
        self.assertAlmostEqual(m.alpha(), panel.THIN_ALPHA_HOVER, msg="拖动期间算悬停那一档")

    def test_没得滚时状态机也不画(self):
        m = panel._Thin(doc_h=400.0, clip_h=400.0, strip_w=16.0, strip_h=400.0)
        m.shown = True
        m.hovered = True
        self.assertAlmostEqual(m.alpha(), 0.0)
        self.assertIsNone(m.rect())


# ══════════════════════════════════════════════════════════════════════
# 二、真面板（课程卡列表）
# ══════════════════════════════════════════════════════════════════════
class 真面板(unittest.TestCase):
    def _build(self):
        try:
            from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
        except ModuleNotFoundError as e:
            self.skipTest(f"AppKit 不可用, 跳过: {type(e).__name__}: {e}")
        NSApplication.sharedApplication().setActivationPolicy_(
            NSApplicationActivationPolicyAccessory)
        import entry_panel as E
        import ready as ready_mod
        td = tempfile.TemporaryDirectory()
        root = pathlib.Path(td.name)
        home = root / "home"
        home.mkdir()
        os.environ["HOME"] = str(home)
        os.environ.pop("OBSIDIAN_VAULT", None)
        gl = root / "glossary.txt"
        gl.write_text("# 假词表\n", encoding="utf-8")
        (root / "glossary").mkdir()
        for c in CODES:
            (root / "glossary" / f"{c}.txt").write_text(f"# {c} 测试课\n", encoding="utf-8")
        _orig = ready_mod.items
        ready_mod.items = lambda **k: [{"key": "mic", "state": "ok"},
                                       {"key": "models", "state": "ok"},
                                       {"key": "engine", "state": "ok"},
                                       {"key": "vault", "state": "ok", "detail": str(home)}]
        try:
            h = E.build(glossary=gl, sessions_dir=root / "sessions",
                        state_root=root / "state", on_start=lambda c: None,
                        on_test_mode=lambda on: True, on_cloud_asr=lambda c: True,
                        cloud_asr=True)
        finally:
            ready_mod.items = _orig
        self.assertIsNotNone(h, "面板没建起来")
        for _ in range(3):
            _pump(0.2)
        h.refresh()
        _pump(0.2)
        sc = self._list_scroll_view(h)
        self.assertIsNotNone(sc, "找不到卡片区的滚动视图")
        strip = _strip_of(sc)
        self.assertIsNotNone(strip, "滚动视图里没有细滚动条")
        return E, h, td, sc, strip

    @staticmethod
    def _list_scroll_view(h):
        from AppKit import NSScrollView
        found = []

        def walk(v):
            if isinstance(v, NSScrollView) and v.documentView() is not None:
                found.append(v)
            for s in v.subviews():
                walk(s)
        walk(h.window.contentView())
        return found[0] if found else None

    def _live_scroll(self, sc, dy: float):
        """模拟"用户在滚"：挪视口 + 发 AppKit 那条 live scroll 通知（真机上是它发的）。"""
        from AppKit import NSNotificationCenter, NSScrollViewDidLiveScrollNotification
        clip = sc.contentView()
        span = max(0.0, float(sc.documentView().bounds().size.height)
                   - float(clip.bounds().size.height))
        y = min(max(0.0, float(clip.bounds().origin.y) + dy), span)
        clip.scrollToPoint_((0.0, y))
        sc.reflectScrolledClipView_(clip)
        NSNotificationCenter.defaultCenter().postNotificationName_object_(
            NSScrollViewDidLiveScrollNotification, sc)
        _pump(0.05)

    def test_系统滚动条关掉了(self):
        E, h, td, sc, _strip = self._build()
        try:
            self.assertFalse(bool(sc.hasVerticalScroller()),
                             "系统那条又回来了（它固定 7pt 且不可配）")
        finally:
            E.close_panel()
            td.cleanup()

    def test_静止不画_滚了才画且是三点(self):
        E, h, td, sc, strip = self._build()
        try:
            m = strip._model
            self.assertAlmostEqual(m.alpha(), 0.0, msg="还没滚就把滑块画出来了")
            self.assertAlmostEqual(_thumb_span(strip)[1], 0.0, places=1,
                                   msg="静止时画面上不该有滑块")
            self._live_scroll(sc, 60.0)
            self.assertGreater(m.alpha(), 0.0, msg="滚了还没亮")
            self.assertAlmostEqual(m.width(), panel.THIN_W)
            n_px, pt = _thumb_span(strip)
            self.assertAlmostEqual(pt, panel.THIN_W, delta=0.75,
                                   msg=f"画出来是 {pt:.2f}pt（要 {panel.THIN_W}）")
        finally:
            E.close_panel()
            td.cleanup()

    def test_悬停变粗到六点(self):
        E, h, td, sc, strip = self._build()
        try:
            self._live_scroll(sc, 60.0)
            strip.mouseEntered_(None)
            _pump(0.1)
            m = strip._model
            self.assertTrue(m.hovered)
            self.assertAlmostEqual(m.width(), panel.THIN_W_HOVER)
            n_px, pt = _thumb_span(strip)
            self.assertAlmostEqual(pt, panel.THIN_W_HOVER, delta=0.75,
                                   msg=f"悬停画出来是 {pt:.2f}pt（要 {panel.THIN_W_HOVER}）")
            self.assertGreater(m.alpha(), panel.THIN_ALPHA - 1e-6, "悬停该更实")
            strip.mouseExited_(None)
            _pump(0.1)
            self.assertFalse(m.hovered)
        finally:
            E.close_panel()
            td.cleanup()

    def test_程序化滚动不闪(self):
        """面板自己刷列表 / 滚回顶**不该**像"用户滚了一下"那样闪滚动条。"""
        E, h, td, sc, strip = self._build()
        try:
            m = strip._model
            clip = sc.contentView()
            clip.scrollToPoint_((0.0, 40.0))
            sc.reflectScrolledClipView_(clip)
            _pump(0.1)
            self.assertAlmostEqual(m.alpha(), 0.0, msg="程序化滚动把滑块闪出来了")
        finally:
            E.close_panel()
            td.cleanup()

    def test_停下之后自己淡出(self):
        E, h, td, sc, strip = self._build()
        try:
            self._live_scroll(sc, 40.0)
            self.assertGreater(strip._model.alpha(), 0.0)
            deadline = time.monotonic() + panel.THIN_IDLE_S + 1.2
            while time.monotonic() < deadline and strip._model.alpha() > 0.0:
                _pump(0.1)
            self.assertAlmostEqual(strip._model.alpha(), 0.0,
                                   msg=f"{panel.THIN_IDLE_S}s 之后滑块还在")
            self.assertAlmostEqual(_thumb_span(strip)[1], 0.0, places=1, msg="画面上还留着")
        finally:
            E.close_panel()
            td.cleanup()

    def test_拖动真的滚动(self):
        from AppKit import (NSEvent, NSEventTypeLeftMouseDown, NSEventTypeLeftMouseDragged,
                            NSEventTypeLeftMouseUp)
        E, h, td, sc, strip = self._build()
        try:
            w = h.window
            self._live_scroll(sc, 80.0)
            _pump(0.1)
            m = strip._model
            before = float(sc.contentView().bounds().origin.y)
            rect = m.rect()
            local = (rect[0] + rect[2] / 2.0, rect[1] + rect[3] / 2.0)
            win_pt = strip.convertPoint_toView_(local, None)

            def ev(t, pt, n):
                return NSEvent.mouseEventWithType_location_modifierFlags_timestamp_windowNumber_context_eventNumber_clickCount_pressure_(
                    t, pt, 0, time.time(), w.windowNumber(), None, n, 1, 1.0)

            strip.mouseDown_(ev(NSEventTypeLeftMouseDown, win_pt, 1))
            self.assertTrue(m.dragging, "按在滑块上没进入拖动")
            for i in range(1, 5):
                strip.mouseDragged_(ev(NSEventTypeLeftMouseDragged,
                                       (win_pt.x, win_pt.y - 12.0 * i), 1 + i))
                _pump(0.05)
            strip.mouseUp_(ev(NSEventTypeLeftMouseUp, (win_pt.x, win_pt.y - 48.0), 9))
            self.assertFalse(m.dragging)
            after = float(sc.contentView().bounds().origin.y)
            self.assertGreater(after, before + 5.0,
                               f"往下拖滑块，内容没往文档后面走（{before:.0f} → {after:.0f}）")
        finally:
            E.close_panel()
            td.cleanup()

    def test_点轨道翻页(self):
        from AppKit import NSEvent, NSEventTypeLeftMouseDown
        E, h, td, sc, strip = self._build()
        try:
            w = h.window
            self._live_scroll(sc, 120.0)
            _pump(0.1)
            m = strip._model
            before = float(sc.contentView().bounds().origin.y)
            rect = m.rect()
            # 点滑块**上方**（画面靠后的位置 → y 更大）= 往回翻一页
            local = (rect[0] + rect[2] / 2.0, min(m.strip_h - 1.0, rect[1] + rect[3] + 30.0))
            win_pt = strip.convertPoint_toView_(local, None)
            ev = NSEvent.mouseEventWithType_location_modifierFlags_timestamp_windowNumber_context_eventNumber_clickCount_pressure_(
                NSEventTypeLeftMouseDown, win_pt, 0, time.time(), w.windowNumber(), None, 1, 1, 1.0)
            strip.mouseDown_(ev)
            _pump(0.1)
            after = float(sc.contentView().bounds().origin.y)
            self.assertLess(after, before - 24.0,
                            f"点轨道没翻页（{before:.0f} → {after:.0f}）")
        finally:
            E.close_panel()
            td.cleanup()

    def test_滚轮交还给滚动视图(self):
        try:
            import objc_own
            from AppKit import NSView
        except ModuleNotFoundError as e:
            self.skipTest(f"AppKit 不可用, 跳过: {type(e).__name__}: {e}")
        E, h, td, sc, strip = self._build()
        try:
            calls: list = []
            cls = objc_own.own("ThinWheelStub", NSView,
                               {"scrollWheel_": lambda self, ev: calls.append(ev)})
            stub = cls.alloc().initWithFrame_(((0.0, 0.0), (100.0, 100.0)))
            strip.removeFromSuperview()
            stub.addSubview_(strip)
            sentinel = object()
            strip.scrollWheel_(sentinel)
            self.assertEqual(len(calls), 1, "滚轮没交还给父滚动视图")
            self.assertIs(calls[0], sentinel)
        finally:
            E.close_panel()
            td.cleanup()

    def test_藏着的时候不吃点击(self):
        E, h, td, sc, strip = self._build()
        try:
            self.assertIsNone(strip.hitTest_(((8.0, 8.0),)),
                              "藏着的滑块还在右边偷走一条 16pt 的命中区")
            self._live_scroll(sc, 60.0)
            _pump(0.05)
            self.assertIsNotNone(strip.hitTest_(((8.0, 8.0),)),
                                 "亮着的时候该能点（拖动/翻页都靠它）")
        finally:
            E.close_panel()
            td.cleanup()

    def test_样式通知把滚动视图按回overlay(self):
        """用户改「显示滚动条」/插鼠标时 AppKit 会自己改回去 —— 我们要按回来。"""
        from AppKit import (NSScrollerStyleLegacy, NSScrollerStyleOverlay,
                            NSPreferredScrollerStyleDidChangeNotification, NSNotificationCenter)
        E, h, td, sc, strip = self._build()
        try:
            sc.setScrollerStyle_(NSScrollerStyleLegacy)
            self.assertEqual(int(sc.scrollerStyle()), int(NSScrollerStyleLegacy))
            NSNotificationCenter.defaultCenter().postNotificationName_object_(
                NSPreferredScrollerStyleDidChangeNotification, None)
            _pump(0.05)
            self.assertEqual(int(sc.scrollerStyle()), int(NSScrollerStyleOverlay),
                             "样式通知之后没按回 overlay（重逢就是那条 15pt 的 legacy 条）")
            self.assertFalse(bool(sc.hasVerticalScroller()))
        finally:
            E.close_panel()
            td.cleanup()

    def test_跟着增大对比度变粗(self):
        E, h, td, sc, strip = self._build()
        try:
            self._live_scroll(sc, 60.0)
            _pump(0.05)
            m = strip._model
            real_read = panel._contrast_on                        # 换掉读系统那一口（别动系统设置）
            panel._contrast_on = lambda: True
            try:
                panel._thin_contrast(strip)                       # 走真实入口
                _pump(0.05)
                self.assertTrue(m.contrast)
                self.assertGreaterEqual(m.width(), panel.THIN_W_CONTRAST)
                self.assertGreaterEqual(m.alpha(), panel.THIN_ALPHA_CONTRAST,
                                        "对比度开着时该更实，不只是更粗")
                n_px, pt = _thumb_span(strip)
                self.assertGreaterEqual(pt, panel.THIN_W_CONTRAST - 0.75,
                                        f"对比度开着时画出来只有 {pt:.2f}pt")
                # 关掉 → 回到那三档
                panel._contrast_on = lambda: False
                panel._thin_contrast(strip)
                _pump(0.05)
                self.assertFalse(m.contrast)
                self.assertAlmostEqual(_thumb_span(strip)[1], panel.THIN_W, delta=0.75)
            finally:
                panel._contrast_on = real_read
        finally:
            E.close_panel()
            td.cleanup()

    def test_滚动视图改尺寸滑块跟着走(self):
        from AppKit import NSMakeRect, NSViewFrameDidChangeNotification, NSNotificationCenter
        E, h, td, sc, strip = self._build()
        try:
            f = sc.frame()
            sc.setFrame_(NSMakeRect(f.origin.x, f.origin.y, f.size.width, f.size.height - 60.0))
            NSNotificationCenter.defaultCenter().postNotificationName_object_(
                NSViewFrameDidChangeNotification, sc)
            _pump(0.1)
            self.assertAlmostEqual(float(strip.frame().size.height),
                                   float(sc.bounds().size.height), delta=1.0,
                                   msg="滚动视图变矮了，滑块那条带子没跟上")
        finally:
            E.close_panel()
            td.cleanup()


def _pump(seconds: float = 0.05) -> None:
    from AppKit import NSApp, NSDate, NSDefaultRunLoopMode
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        ev = NSApp.nextEventMatchingMask_untilDate_inMode_dequeue_(
            2 ** 30, NSDate.dateWithTimeIntervalSinceNow_(0.02), NSDefaultRunLoopMode, True)
        if ev:
            NSApp.sendEvent_(ev)


if __name__ == "__main__":
    unittest.main(verbosity=2)
