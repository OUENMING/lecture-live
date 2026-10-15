"""复习层分块并发（`obsidian_writer._review`）的判据。不联网：`_call_review` 换成替身。"""
import os
import pathlib
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import obsidian_writer as ow            # noqa: E402


def entries(n):
    return [{"ts": f"10:{i // 60:02d}:{i % 60:02d}", "en": f"sentence {i}.", "asr": "", "zh": ""} for i in range(n)]


class ReviewParallel(unittest.TestCase):
    def writer(self, d):
        return ow.ObsidianWriter(d, "TESTX", mode="yes", api_key="k", polish=False)

    def test_各块同时发_结果按块序合并(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(ow, "SESSIONS", pathlib.Path(d) / "sessions"):   # ⚠️ 写端也换
            w = self.writer(d)
            live, peak, lock = [0], [0], threading.Lock()

            def fake(transcript):
                first = int(transcript.split("sentence ", 1)[1].split(".", 1)[0])
                ci = first // ow.REVIEW_CHUNK
                with lock:
                    live[0] += 1
                    peak[0] = max(peak[0], live[0])
                time.sleep(0.3 if ci == 0 else 0.1)              # 第 0 块最后回来
                with lock:
                    live[0] -= 1
                return {"title": f"T{ci}", "sections": [{"heading": f"H{ci}", "points": ["p"]}], "qa": []}

            w._call_review = fake
            w._overview = lambda heads: {}
            n = ow.REVIEW_CHUNK * 4
            t = time.monotonic()
            r = w._review(entries(n))
            dt = time.monotonic() - t
            self.assertEqual(peak[0], 4)
            self.assertLess(dt, 0.6, "串行要 0.6 s 以上")
            self.assertEqual([s["heading"].split("] ", 1)[1] for s in r["sections"]], ["H0", "H1", "H2", "H3"])
            self.assertEqual(r.get("title"), "T0", "meta 仍取第 0 块，哪怕它最后回来")

    def test_某块失败_其他块照收(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(ow, "SESSIONS", pathlib.Path(d) / "sessions"):
            w = self.writer(d)
            w._call_review = lambda tr: {} if "sentence 70." in tr else {"sections": [{"heading": "ok", "points": ["p"]}], "qa": []}
            w._overview = lambda heads: {}
            r = w._review(entries(ow.REVIEW_CHUNK * 2))
            self.assertEqual(len(r["sections"]), 1)


if __name__ == "__main__":
    unittest.main()
