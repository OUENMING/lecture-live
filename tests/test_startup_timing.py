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
