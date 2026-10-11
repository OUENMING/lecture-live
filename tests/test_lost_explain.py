#!/usr/bin/env python3
"""❓ 课后讲解卡（`polish.explain_lost` + `ObsidianWriter._lost_items`）的判据 —— **全部离线**。

    CLASSLIVE_UPLOAD=0 ClassLive.app/Contents/MacOS/python tests/test_lost_explain.py

## 钉住了什么

1. ⭐ `explain_lost` 返回**与 blocks 等长**的列表，否则返回 `[]`（整体不用，绝不错位）；
   任何异常 / 坏 JSON / 条数对不上 / 序号对不上 都不外抛。
2. ⭐ 21 块分两次请求（每 20 块一次）。
3. ⭐ **没有讲解时 `_lost_items` 与改动前逐字相同**（`BASELINE` 是改动前跑出来的原样输出）：
   fn 为 None / 返回 [] / 条数对不上 / `explain=False`（用户取消）全部如此，且取消时不调用 fn。
4. ⭐ 有讲解时只在每块末尾追加两行；问题里的 `::` 被拆开（否则伪造闪卡边界）。
5. ⭐ `close()` 里讲解是额外一层：fn 抛错笔记照写；`cancel` 置位不调用 fn。

⚠️ `chat` 是注入的替身，**不联网**；写端（`SESSIONS`）和 vault 都换到临时目录。
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
import threading
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import obsidian_writer as ow                                           # noqa: E402
import polish                                                          # noqa: E402

B = ["10:00:00", "10:00:02", "10:00:04", "10:00:16", "10:00:18",
     "10:00:30", "10:00:32", "10:00:34"]
#: 两次按下 -> 两块。改动前（没有讲解层）对这份夹具跑出来的原样输出。
BASELINE = [
    '- 按于 `10:00:33` · 这几句（`10:00:16`–`10:00:34`，共 5 句）\n'
    '  - `10:00:16` Z16\n  - `10:00:18` Z18\n  - `10:00:30` Z30\n'
    '  - `10:00:32` Z32\n  - `10:00:34` Z34',
    '- 按于 `10:00:05` · 这几句（`10:00:00`–`10:00:04`，共 3 句）\n'
    '  - `10:00:00` Z00\n  - `10:00:02` Z02\n  - `10:00:04` Z04',
]


def _entries():
    return [{"ts": t, "en": "E" + t[-2:], "zh": "Z" + t[-2:], "asr": "", "star": False}
            for t in B]


def _blocks(n):
    return [[{"en": f"en{k}", "zh": f"zh{k}"}] for k in range(n)]


def good_chat(calls=None):
    """按 user 里的 `[序号]` 回一份完整答案；记录每次请求。"""
    def chat(key, model, system, user, max_tokens, timeout):
        if calls is not None:
            calls.append(user)
        ids = [int(x) for x in re.findall(r"^\[(\d+)\]", user, re.M)]
        return {"items": [{"i": i, "explain": f"讲{i}\n换行", "q": f"问{i}", "a": f"答{i}"}
                          for i in ids]}
    return chat


class ExplainLost(unittest.TestCase):
    def run_it(self, blocks, chat):
        return polish.explain_lost(blocks, "k", "m", chat=chat)

    def test_normal_is_aligned_and_single_line(self):
        out = self.run_it(_blocks(3), good_chat())
        self.assertEqual(len(out), 3)
        self.assertEqual(out[1], {"explain": "讲1 换行", "q": "问1", "a": "答1"})

    def test_21_blocks_two_requests(self):
        calls: list = []
        out = self.run_it(_blocks(21), good_chat(calls))
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(out), 21)
        self.assertEqual(out[20]["q"], "问20")

    def test_bad_json_shape_returns_empty(self):
        self.assertEqual(self.run_it(_blocks(2), lambda *a: {"items": "x"}), [])
        self.assertEqual(self.run_it(_blocks(2), lambda *a: {}), [])

    def test_count_mismatch_returns_empty(self):
        def short(key, model, system, user, mt, to):
            return {"items": [{"i": 0, "explain": "a", "q": "b", "a": "c"}]}
        self.assertEqual(self.run_it(_blocks(2), short), [])

    def test_wrong_index_returns_empty(self):
        def bad(key, model, system, user, mt, to):
            return {"items": [{"i": 5, "explain": "a"}, {"i": 6, "explain": "b"}]}
        self.assertEqual(self.run_it(_blocks(2), bad), [])

    def test_missing_fields_become_empty_strings(self):
        def partial(key, model, system, user, mt, to):
            return {"items": [{"i": 0, "explain": "只有讲解"}, {"i": 1, "q": 12}]}
        out = self.run_it(_blocks(2), partial)
        self.assertEqual(out, [{"explain": "只有讲解", "q": "", "a": ""},
                               {"explain": "", "q": "", "a": ""}])

    def test_exception_never_escapes(self):
        def boom(*a):
            raise RuntimeError("503")
        self.assertEqual(self.run_it(_blocks(2), boom), [])

    def test_second_batch_failure_discards_everything(self):
        n = {"k": 0}

        def flaky(key, model, system, user, mt, to):
            n["k"] += 1
            if n["k"] == 2:
                raise RuntimeError("x")
            return good_chat()(key, model, system, user, mt, to)
        self.assertEqual(self.run_it(_blocks(21), flaky), [])

    def test_no_key_or_no_blocks_makes_no_request(self):
        def boom(*a):
            raise AssertionError("不该发请求")
        self.assertEqual(polish.explain_lost(_blocks(2), "", "m", chat=boom), [])
        self.assertEqual(polish.explain_lost([], "k", "m", chat=boom), [])


class LostItems(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = ow.SESSIONS
        ow.SESSIONS = Path(self._tmp.name) / "sessions"        # ⚠️ 写端也要换
        self.addCleanup(self._restore)

    def _restore(self):
        ow.SESSIONS = self._old
        self._tmp.cleanup()

    def writer(self, **kw):
        w = ow.ObsidianWriter(self._tmp.name, "TESTX", mode="yes", **kw)
        self.assertEqual(w.session_path.parent, ow.SESSIONS, "会话写到真 sessions/ 去了")
        w._lost_path().write_text(
            "\n".join(json.dumps({"epoch": 1.0, "t": t, "n": n, "kind": "lost"})
                      for t, n in [("10:00:33", 8), ("10:00:05", 3)]) + "\n",
            encoding="utf-8")
        return w

    def test_baseline_without_fn(self):
        self.assertEqual(self.writer()._lost_items(_entries()), BASELINE)

    def test_baseline_when_fn_gives_nothing_usable(self):
        for fn in (lambda b: [], lambda b: [{"explain": "x", "q": "q", "a": "a"}],
                   lambda b: 1 / 0):
            w = self.writer(lost_explain_fn=fn)
            self.assertEqual(w._lost_items(_entries()), BASELINE)

    def test_explain_false_does_not_call_fn(self):
        calls: list = []
        w = self.writer(lost_explain_fn=lambda b: calls.append(b) or [])
        self.assertEqual(w._lost_items(_entries(), explain=False), BASELINE)
        self.assertEqual(calls, [])

    def test_explain_appends_two_lines_per_block(self):
        seen: list = []

        def fn(blocks):
            seen.append(blocks)
            return [{"explain": f"讲{k}", "q": f"问{k}::带冒号", "a": f"答{k}"}
                    for k in range(len(blocks))]
        out = self.writer(lost_explain_fn=fn)._lost_items(_entries())
        self.assertEqual(len(seen), 1, "一次调用讲完所有块")
        self.assertEqual(seen[0][1], [{"en": "E00", "zh": "Z00"}, {"en": "E02", "zh": "Z02"},
                                      {"en": "E04", "zh": "Z04"}])
        for k, blk in enumerate(out):
            self.assertTrue(blk.startswith(BASELINE[k] + "\n"), "原有部分必须逐字不变")
        self.assertEqual(out[0].splitlines()[-2:],
                         ["  - 💡 讲解：讲0", "  - 问0: :带冒号::答0"])

    def test_empty_parts_are_skipped(self):
        out = self.writer(lost_explain_fn=lambda b: [
            {"explain": "", "q": "只有问", "a": ""}, {"explain": "只讲", "q": "", "a": "x"}
        ])._lost_items(_entries())
        self.assertEqual(out[0], BASELINE[0])
        self.assertEqual(out[1], BASELINE[1] + "\n  - 💡 讲解：只讲")


class CloseWiring(unittest.TestCase):
    def run_close(self, fn, cancel=None):
        with tempfile.TemporaryDirectory() as tmp:
            old = ow.SESSIONS
            ow.SESSIONS = Path(tmp) / "sessions"
            try:
                w = ow.ObsidianWriter(tmp, "TESTX", mode="yes", polish=False,
                                      lost_explain_fn=fn)
                w.append("hello there", "你好", raw="hello there")
                w.append("second one", "第二句", raw="second one")
                w.mark_lost()
                w.close(cancel=cancel)
                return w.vault_path.read_text(encoding="utf-8")
            finally:
                ow.SESSIONS = old

    def test_fn_failure_does_not_sink_the_note(self):
        note = self.run_close(lambda b: 1 / 0)
        self.assertIn("hello there", note)

    def test_cancel_skips_the_call(self):
        calls: list = []
        ev = threading.Event()
        ev.set()
        note = self.run_close(lambda b: calls.append(b) or [], cancel=ev)
        self.assertEqual(calls, [])
        self.assertIn("hello there", note)

    def test_close_calls_fn_once_when_not_cancelled(self):
        calls: list = []
        note = self.run_close(
            lambda b: calls.append(b) or [{"explain": "讲", "q": "问", "a": "答"}])
        self.assertEqual(len(calls), 1)
        self.assertIn("💡 讲解：讲", note)
        self.assertIn("问::答", note)


if __name__ == "__main__":
    unittest.main(verbosity=2)
