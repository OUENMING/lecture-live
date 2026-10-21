"""起步首屏的判据：开关 + 幂等关闭，不建真窗口。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import startup_hud  # noqa: E402


class Gating(unittest.TestCase):
    def test_只有_overlay_且没被环境变量关掉才建(self):
        self.assertTrue(startup_hud.enabled("overlay", {}))
        self.assertFalse(startup_hud.enabled("terminal", {}))
        self.assertFalse(startup_hud.enabled("overlay", {"CLASSLIVE_NO_STARTUP_HUD": "1"}))

    def test_环境变量只认_1(self):
        self.assertTrue(startup_hud.enabled("overlay", {"CLASSLIVE_NO_STARTUP_HUD": "0"}))
        self.assertTrue(startup_hud.enabled("overlay", {"CLASSLIVE_NO_STARTUP_HUD": ""}))

    def test_状态文案是模块常量_主流程和探针共用(self):
        for name in ("TEXT_AUDIO", "TEXT_TRANSLATOR", "TEXT_OVERLAY"):
            self.assertTrue(getattr(startup_hud, name), name)


class FakeLabel:
    def __init__(self):
        self.value = ""

    def setStringValue_(self, value):
        self.value = value


class FakeWindow:
    def __init__(self):
        self.out = 0
        self.display = 0

    def displayIfNeeded(self):
        self.display += 1

    def orderOut_(self, _sender):
        self.out += 1


class Lifecycle(unittest.TestCase):
    def make(self):
        h = startup_hud.StartupHUD.__new__(startup_hud.StartupHUD)
        h._window = FakeWindow()
        h._label = FakeLabel()
        h._closed = False
        return h

    def test_note_更新标题并触发重画(self):
        h = self.make()
        h.note("正在加载翻译…")
        self.assertEqual(h._label.value, "正在加载翻译…")
        self.assertEqual(h._window.display, 1)

    def test_close_幂等_关了不再改标题(self):
        h = self.make()
        h.note("准备中")
        h.close()
        h.close()
        self.assertEqual(h._window.out, 1, "close 只能 orderOut 一次")
        h.note("关掉之后不该再显示")
        self.assertEqual(h._label.value, "准备中")
        self.assertEqual(h._window.display, 1, "close 之后 note 不能再触发重画")


if __name__ == "__main__":
    unittest.main()
