#!/usr/bin/env python3
"""`capture.SilenceWatch` 的判据 —— 收音持续数字静音时报一次、恢复时报一次。

    ClassLive.app/Contents/MacOS/python tests/test_silence_watch.py

纯状态机、不碰音频设备、不写文件。
"""
from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from capture import CHUNK, SilenceWatch  # noqa: E402

QUIET, LOUD = 0.0, 0.01          # 数字静音 / 安静房间的底噪量级（−40 dBFS）


def feed(w, peak, seconds):
    out = []
    for _ in range(int(round(seconds * 10))):
        r = w.feed(peak, CHUNK)
        if r:
            out.append(r)
    return out


class Watch(unittest.TestCase):
    def test_reports_once_after_hold_then_back(self):
        w = SilenceWatch(hold_s=15)
        self.assertEqual(feed(w, QUIET, 14.9), [])
        self.assertEqual(feed(w, QUIET, 30), ["silent"])     # 只报一次
        self.assertEqual(feed(w, LOUD, 1), ["back"])
        self.assertEqual(feed(w, LOUD, 5), [])

    def test_any_sound_resets_the_run(self):
        w = SilenceWatch(hold_s=15)
        self.assertEqual(feed(w, QUIET, 10) + feed(w, LOUD, 0.1) + feed(w, QUIET, 10), [])

    def test_quiet_room_is_not_silence(self):
        w = SilenceWatch(hold_s=15)
        self.assertEqual(feed(w, 10 ** (-70 / 20), 60), [])   # −70 dBFS 也算有声

    def test_can_alert_again_after_recovery(self):
        w = SilenceWatch(hold_s=5)
        self.assertEqual(feed(w, QUIET, 6) + feed(w, LOUD, 1) + feed(w, QUIET, 6), ["silent", "back", "silent"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
