"""起步计时（`main.startup_timing`）的判据：纯函数，不起任何东西。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import main                             # noqa: E402


class StartupTiming(unittest.TestCase):
    TP = {"run": 10.0, "audio": 10.3, "draft": 11.7, "translator": 11.9, "overlay": 12.4}

    def test_各段是相邻打点之差_点按取墙钟差(self):
        r = main.startup_timing(self.TP, 1000.0, 999.2)
        f = r["fields"]
        self.assertEqual((f["audio_s"], f["draft_s"], f["translator_s"], f["overlay_s"]), (0.3, 1.4, 0.2, 0.5))
        self.assertEqual(f["run_s"], 2.4)
        self.assertEqual(f["click_to_run_s"], 0.8)
        self.assertEqual(f["click_to_overlay_s"], 3.2)
        self.assertIn("字幕出现 3.2 s", r["line"])

    def test_点按太久以前或在未来_不当点按(self):
        for click in (1000.0 - 121.0, 1001.0, None):
            r = main.startup_timing(self.TP, 1000.0, click)
            self.assertNotIn("click_to_overlay_s", r["fields"], click)
            self.assertTrue(r["line"].startswith("起步 → 字幕出现 2.4 s"), r["line"])

    def test_缺的段跳过_不算错位(self):
        r = main.startup_timing({"run": 0.0, "draft": 1.0, "overlay": 1.5}, 5.0, None)
        self.assertEqual(r["fields"], {"draft_s": 1.0, "overlay_s": 0.5, "run_s": 1.5})

    def test_草稿模型就绪单列一项_用绝对偏移(self):
        """3.9.7 第 5c 项：草稿模型后台加载 → 就绪时刻**不插进那条链**。

        ⚠️ 就绪在**另一个线程**上，可能晚于 translator / overlay —— 当成相邻差值
        会把后面几段算成负数。
        """
        tp = dict(self.TP, draft_ready=13.9)      # 晚于 overlay(12.4)
        r = main.startup_timing(tp, 1000.0, None)
        self.assertEqual(r["fields"]["draft_ready_s"], 3.9, "就绪时刻该是从 run 起算的绝对偏移")
        # 原有那几项一个都没动
        self.assertEqual((r["fields"]["audio_s"], r["fields"]["draft_s"],
                          r["fields"]["translator_s"], r["fields"]["overlay_s"]),
                         (0.3, 1.4, 0.2, 0.5))
        self.assertEqual(r["fields"]["run_s"], 2.4)
        self.assertIn("草稿模型就绪 +3.9", r["line"])

    def test_没有就绪打点_不多写字段(self):
        r = main.startup_timing(self.TP, 1000.0, None)
        self.assertNotIn("draft_ready_s", r["fields"])
        self.assertNotIn("就绪", r["line"])

    def test_首屏_hud_用绝对偏移_后续链从它起算(self):
        """P0 首屏发生在 audio 之前；它单列，且不能让 HUD 时间被重复算进“开麦试探”。"""
        tp = dict(self.TP, hud=10.1)
        r = main.startup_timing(tp, 1000.0, None)
        f = r["fields"]
        self.assertEqual(f["hud_s"], 0.1)
        # audio 从 hud 起算：10.3-10.1=0.2；后面几段不变
        self.assertEqual((f["audio_s"], f["draft_s"], f["translator_s"], f["overlay_s"]),
                         (0.2, 1.4, 0.2, 0.5))
        self.assertEqual(f["run_s"], 2.4)
        self.assertIn("首屏 0.1", r["line"])
        self.assertIn("字幕出现 2.4 s", r["line"])



class PreHudSplit(unittest.TestCase):
    """U2-A：首屏之前那段拆成六项，之和 = hud_s。"""
    SIX = ("pre_lock_s", "pre_reset_s", "pre_keymove_s", "pre_mic_s", "hud_import_s", "hud_show_s")

    def test_六项齐全_之和等于_hud_s(self):
        tp = {"run": 100.0, "lock": 100.004, "reset": 100.005, "keymove": 100.021,
              "mic": 100.120, "hud_import": 100.470, "hud": 100.612, "audio": 101.0}
        f = main.startup_timing(tp, 1000.0, None)["fields"]
        for k in self.SIX:
            self.assertIn(k, f)
        self.assertLess(abs(sum(f[k] for k in self.SIX) - f["hud_s"]), 0.05)
        self.assertEqual(f["hud_import_s"], 0.35)
        self.assertEqual(f["hud_show_s"], 0.142)

    def test_某步没打上点_缺项但不抛_之和仍对(self):
        """某一步抛异常 → 它的点没记；打点只跳过那一项，其余照常，总和仍等于 hud_s。"""
        tp = {"run": 5.0, "lock": 5.01, "keymove": 5.05, "hud_import": 5.3, "hud": 5.4}
        r = main.startup_timing(tp, 1000.0, None)
        f = r["fields"]
        self.assertNotIn("pre_reset_s", f)
        self.assertNotIn("pre_mic_s", f)
        self.assertLess(abs(sum(f[k] for k in self.SIX if k in f) - f["hud_s"]), 0.05)
        self.assertEqual(f["run_s"], 0.4)

    def test_没有首屏_不出这六项(self):
        f = main.startup_timing({"run": 0.0, "lock": 0.1, "overlay": 1.0}, 5.0, None)["fields"]
        for k in self.SIX:
            self.assertNotIn(k, f)

    def test_run_里六个打点都在_且按序(self):
        src = open(main.__file__, encoding="utf-8").read()
        body = src[src.index("def run(args)"):]
        pos = [body.index(f'_tp["{k}"] = time.monotonic()') for k in ("lock", "reset", "keymove", "mic", "hud_import")]
        self.assertEqual(pos, sorted(pos))
        self.assertLess(pos[-1], body.index('_tp["hud"] = time.monotonic()'))


class ClickStamp(unittest.TestCase):
    """面板留的点按戳：读到就删 —— 第二次起（从终端起 `cl`）不许捡到旧的。读写都在临时目录。"""

    def test_读一次就删_坏的当没有(self):
        import pathlib, tempfile
        from unittest import mock
        import instance_lock
        with tempfile.TemporaryDirectory() as d, mock.patch.object(instance_lock, "STATE_DIR", pathlib.Path(d)):
            self.assertIsNone(main._click_wall())
            (pathlib.Path(d) / "start-click").write_text("1234.5", encoding="utf-8")
            self.assertEqual(main._click_wall(), 1234.5)
            self.assertFalse((pathlib.Path(d) / "start-click").exists())
            self.assertIsNone(main._click_wall())
            (pathlib.Path(d) / "start-click").write_text("坏", encoding="utf-8")
            self.assertIsNone(main._click_wall())


if __name__ == "__main__":
    unittest.main()
