"""OCR 审查（2026-11-22）确认的笔记链路 bug 的回归测试 —— 每条一个最小复现。

⚠️ 写端全部隔离：用临时目录，不碰真 `sessions/` / `~/.classlive/`。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class WriterAppendDiskError(unittest.TestCase):
    def test_append_write_failure_does_not_raise(self):
        import obsidian_writer as ow
        with tempfile.TemporaryDirectory() as tmp:
            old = ow.SESSIONS
            ow.SESSIONS = Path(tmp) / "sessions"
            try:
                w = ow.ObsidianWriter(vault=tmp, course="TEST", mode="yes")
                w.append("one", "一")
                real_open = Path.open

                def boom(self, *a, **k):
                    if self == w.session_path:
                        raise OSError(28, "No space left on device")
                    return real_open(self, *a, **k)
                with mock.patch.object(Path, "open", boom), \
                        mock.patch("builtins.print"):
                    self.assertEqual(w.append("two", "二"), "")
                self.assertEqual(w.count, 1)          # 写失败句号不前进
                w.append("three", "三")
                self.assertEqual(w.count, 2)
            finally:
                ow.SESSIONS = old


class NoteJobsConcurrency(unittest.TestCase):
    def test_two_threads_write_same_record(self):
        import threading
        import note_jobs as nj
        with tempfile.TemporaryDirectory() as tmp:
            nj.write("s", {"a": 0}, root=tmp)
            bad = []

            def run(k):
                for i in range(300):
                    if not nj.write("s", {k: i}, root=tmp):
                        bad.append((k, i))
            ts = [threading.Thread(target=run, args=(k,)) for k in ("x", "y")]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
            self.assertEqual(bad, [])
            rec = nj.read("s", root=tmp)
            self.assertEqual((rec.get("x"), rec.get("y")), (299, 299))

    def test_claim_stale_record_only_one_winner(self):
        import threading
        import note_jobs as nj
        for _ in range(20):
            with tempfile.TemporaryDirectory() as tmp:
                nj.write("s", {"state": nj.DONE}, root=tmp)
                go = threading.Barrier(8)
                wins = []

                def run(i):
                    go.wait()
                    if nj.claim("s", root=tmp, pid=os.getpid()):
                        wins.append(i)
                ts = [threading.Thread(target=run, args=(i,)) for i in range(8)]
                for t in ts:
                    t.start()
                for t in ts:
                    t.join()
                self.assertEqual(len(wins), 1, wins)


class NoteMergeDeadline(unittest.TestCase):
    def test_chat_json_gets_wall_clock_deadline(self):
        import llm_client
        import note_merge as nm
        seen = {}

        def fake(*a, **k):
            seen.update(k)
            return {"zh": "x"}
        with mock.patch.object(llm_client, "chat_json", fake):
            nm._chat("k", "m", "s", "u", 400, 20.0)
        self.assertEqual(seen.get("deadline"), 20.0)


if __name__ == "__main__":
    unittest.main()
