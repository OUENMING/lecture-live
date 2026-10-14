"""悬浮窗「隐藏 / 显示」「保持在最前」的行为判据（真窗口，真 AppKit）。

判的是**结果**：窗口在不在屏上、透明度回没回到 1、菜单标题、窗口层级 —— 不数调用次数。
⚠️ 写端：`Overlay.close()` 在用户拖过窗口时会写 `.window`；这里没人拖，但照旧备份还原（同 test_panel）。
"""
import os
import pathlib
import sys
import time
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))


def spin(s: float) -> None:
    from Foundation import NSDate, NSRunLoop
    end = time.monotonic() + s
    while time.monotonic() < end:
        NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.02))


class HideShow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
        NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)
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
        self.notes = []
        self.ov = overlay.Overlay(note=self.notes.append)
        self.ov.show()
        spin(0.1)

    def tearDown(self):
        self.ov.close()

    def test_点减号_窗口淡出后不在屏上_菜单变成显示(self):
        p = self.ov._panel
        self.assertTrue(p.isVisible())
        self.ov._btn_hide.performClick_(None)          # 真按钮 → 真回调
        spin(0.6)
        self.assertFalse(p.isVisible())
        self.assertEqual(p.alphaValue(), 1.0, "藏完要把透明度还原，否则下次显示是透明的")
        self.assertEqual(self.ov._mi_hide.title(), "显示字幕")
        self.assertIn("hide", self.notes)

    def test_菜单显示字幕_回到屏上且不透明(self):
        p = self.ov._panel
        self.ov._set_hidden(True)
        spin(0.6)
        self.ov._toggle_hidden()
        spin(0.6)
        self.assertTrue(p.isVisible())
        self.assertAlmostEqual(p.alphaValue(), 1.0, places=2)
        self.assertEqual(self.ov._mi_hide.title(), "隐藏字幕")

    def test_淡出途中又点显示_不会被收尾藏掉(self):
        p = self.ov._panel
        self.ov._set_hidden(True)
        self.ov._set_hidden(False)                     # 淡出还没完
        spin(0.6)
        self.assertTrue(p.isVisible())
        self.assertAlmostEqual(p.alphaValue(), 1.0, places=2)

    def test_减弱动态效果_立刻藏立刻显(self):
        import motion
        p = self.ov._panel
        with mock.patch.object(motion, "reduce_motion", lambda: True):
            self.ov._set_hidden(True)
            self.assertFalse(p.isVisible())
            self.ov._set_hidden(False)
            self.assertTrue(p.isVisible())

    def test_保持在最前_开关改窗口层级和勾选(self):
        from AppKit import NSFloatingWindowLevel, NSNormalWindowLevel
        p = self.ov._panel
        self.assertEqual(p.level(), NSFloatingWindowLevel)
        self.ov._toggle_on_top()
        self.assertEqual((p.level(), self.ov._mi_top.state()), (NSNormalWindowLevel, 0))
        self.ov._toggle_on_top()
        self.assertEqual((p.level(), self.ov._mi_top.state()), (NSFloatingWindowLevel, 1))

    def test_减号在顶栏上_紧挨在叉的左边(self):
        bh, bc = self.ov._btn_hide.frame(), self.ov._btn_close.frame()
        self.assertFalse(self.ov._btn_hide.isHidden())
        self.assertLess(bh.origin.x + bh.size.width, bc.origin.x)
        self.assertLess(bc.origin.x - (bh.origin.x + bh.size.width), 12.0)



class BarHover(unittest.TestCase):
    """顶栏按钮跟着鼠标进出淡入淡出（`_bar_hover` / `_bar_tick`）。判 alpha 结果，不数调用。"""
    setUpClass = HideShow.__dict__["setUpClass"]
    tearDownClass = HideShow.__dict__["tearDownClass"]
    setUp = HideShow.setUp
    tearDown = HideShow.tearDown

    def _alphas(self):
        return [round(b.alphaValue(), 2) for b in self.ov._bar]

    def test_开课先亮_宽限到点且鼠标不在窗内_淡出_在听小点不动(self):
        self.assertEqual(set(self._alphas()), {1.0})
        ear = self.ov._ear.layer().opacity()
        with mock.patch.object(self.ov, "_mouse_in_panel", return_value=False):
            self.ov._bar_tick(time.monotonic() + 3.1)
            spin(0.5)
        self.assertEqual(set(self._alphas()), {0.0})
        self.assertFalse(self.ov._ear.isHidden())
        self.assertEqual(self.ov._ear.layer().opacity(), ear)

    def test_鼠标进来立刻亮_出去等一会再暗(self):
        with mock.patch.object(self.ov, "_mouse_in_panel", return_value=False):
            self.ov._bar_tick(time.monotonic() + 3.1)
            spin(0.5)
            self.ov._bar_hover(True)
            spin(0.4)
            self.assertEqual(set(self._alphas()), {1.0})
            self.ov._bar_hover(False)
            self.ov._bar_tick(time.monotonic())             # 还没到 BAR_LEAVE_S
            spin(0.3)
            self.assertEqual(set(self._alphas()), {1.0}, "擦边划过不许闪")
            self.ov._bar_tick(time.monotonic() + 0.5)
            spin(0.5)
        self.assertEqual(set(self._alphas()), {0.0})

    def test_到点但鼠标其实在窗内_不藏(self):
        with mock.patch.object(self.ov, "_mouse_in_panel", return_value=True):
            self.ov._bar_tick(time.monotonic() + 3.1)
            spin(0.5)
        self.assertEqual(set(self._alphas()), {1.0})

    def test_淡出不碰isHidden_字幕按钮的显隐照旧归答案接管(self):
        before = [b.isHidden() for b in self.ov._bar]
        with mock.patch.object(self.ov, "_mouse_in_panel", return_value=False):
            self.ov._bar_tick(time.monotonic() + 3.1)
            spin(0.5)
        self.assertEqual([b.isHidden() for b in self.ov._bar], before)

    def test_再打开一次_真AppleEvent_藏着穿透着都叫回来(self):
        """菜单栏图标被挤没（刘海屏）时的找回路：给自己 pid 发真的 `aevt`/`rapp`，走真的 pump 收。"""
        import instance_lock
        p = self.ov._panel
        self.ov._set_hidden(True)
        self.ov._toggle_through()
        end = time.monotonic() + 0.6
        while time.monotonic() < end:
            self.ov.pump()
        self.assertFalse(p.isVisible())
        self.assertTrue(instance_lock.poke(os.getpid()))
        end = time.monotonic() + 1.0
        while time.monotonic() < end and not p.isVisible():
            self.ov.pump()
        spin(0.4)
        self.assertTrue(p.isVisible())
        self.assertFalse(self.ov._through, "叫回来还穿透着 = 看得见点不动")
        self.assertFalse(p.ignoresMouseEvents())
        self.assertIn("reopen", self.notes)

    def test_云端恢复时菜单栏图标旁不挂emoji(self):
        btn = self.ov._status.button()
        self.ov.notice("降级", warn=True)
        self.assertEqual(btn.title(), "⚠️")
        self.ov.notice("恢复了", warn=False)
        self.assertEqual(btn.title(), "" if btn.image() is not None else "🎧")

if __name__ == "__main__":
    unittest.main()
