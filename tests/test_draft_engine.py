#!/usr/bin/env python3
"""`draft_engine.py` 的判据 —— 草稿引擎的选择、喂流、熔断、回退（`docs/PLAN-streaming-asr.md` §6.2）。

    ClassLive.app/Contents/MacOS/python tests/test_draft_engine.py

全离线、毫秒级、不需要模型：假识别器实现 `create_stream / is_ready / decode_stream / get_result`，
假 stream 记下被喂的样本。HOME 指向临时目录；本模块不写任何文件。
"""
from __future__ import annotations

import os
import pathlib
import shutil
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

_TMP_HOME = tempfile.mkdtemp(prefix="draft-engine-test-")
os.environ["HOME"] = _TMP_HOME

import numpy as np                                                    # noqa: E402

import draft_engine as de                                             # noqa: E402

GB = 2**30
WIN = 10 * 16000


def tearDownModule():
    shutil.rmtree(_TMP_HOME, ignore_errors=True)


class FakeStream:
    def __init__(self):
        self.fed: list[np.ndarray] = []
        self.pending = 0
        self.finished = False

    def accept_waveform(self, sr, samples):
        if sr != 16000:
            raise ValueError(sr)
        if self.finished:
            raise RuntimeError("往已经 input_finished 的流里喂")
        self.fed.append(np.asarray(samples).copy())
        self.pending += 1

    def input_finished(self):
        self.finished = True


class FakeRec:
    """`fail_on`：第几次 get_result 抛（从 1 数）。"""

    def __init__(self, fail_on: int | None = None):
        self.streams: list[FakeStream] = []
        self.calls = 0
        self.fail_on = fail_on

    def create_stream(self):
        s = FakeStream()
        self.streams.append(s)
        return s

    def is_ready(self, s):
        return s.pending > 0

    def decode_stream(self, s):
        s.pending -= 1

    def get_result(self, s):
        self.calls += 1
        if self.fail_on is not None and self.calls == self.fail_on:
            raise RuntimeError("boom")
        return f" n{sum(len(x) for x in s.fed)} "


def buf(n: int) -> np.ndarray:
    return np.arange(n, dtype=np.float32)


class Choose(unittest.TestCase):
    def test_full_table(self):
        """⚠️ 2026-11-20（桌面 D1）：只剩 `{"nemotron", "none"}` 两个答案。
        内存门（`MIN_RAM_BYTES`）与 `parakeet` 分支一起删了 —— 没有第二个模型可退，
        卡内存等于让云端失败的人只剩零字幕。
        """
        cases = [
            # mode,     present, expect
            ("auto", True, "nemotron"),
            ("auto", False, "none"),
            ("nemotron", True, "nemotron"),
            ("nemotron", False, "none"),
            ("off", True, "none"),
            ("off", False, "none"),
            ("bogus", True, "nemotron"),       # 非法 → 当 auto
            ("bogus", False, "none"),
            ("", True, "nemotron"),
        ]
        for mode, present, want in cases:
            with self.subTest(mode=mode, present=present):
                self.assertEqual(de.choose(mode, present)[0], want)

    def test_reasons_say_why(self):
        self.assertIn("未下载", de.choose("auto", False)[1])
        self.assertIn("关闭", de.choose("off", True)[1])
        self.assertEqual(de.choose("auto", True)[1], "")

    def test_no_ram_gate_any_more(self):
        """内存门必须**真的**不在了 —— 留一个 `MIN_RAM_BYTES` 常量在就是一种暗示。"""
        self.assertFalse(hasattr(de, "MIN_RAM_BYTES"),
                         "MIN_RAM_BYTES 还在 —— 内存门没删干净")


class Drafter(unittest.TestCase):
    def test_feeds_only_the_increment(self):
        rec = FakeRec()
        d = de.NemotronDrafter("", rec=rec)
        for n in (16000, 32000, 40000):
            out = d.update(buf(n), 7)
        self.assertEqual(len(rec.streams), 1)
        fed = np.concatenate(rec.streams[0].fed)
        np.testing.assert_array_equal(fed, buf(40000))
        self.assertEqual(out, "n40000")              # strip 过

    def test_same_buf_twice_feeds_nothing(self):
        rec = FakeRec()
        d = de.NemotronDrafter("", rec=rec)
        d.update(buf(16000), 1)
        d.update(buf(16000), 1)
        self.assertEqual(len(rec.streams[0].fed), 1)

    def test_new_stream_on_tag_change(self):
        rec = FakeRec()
        d = de.NemotronDrafter("", rec=rec)
        d.update(buf(16000), 1)
        out = d.update(buf(32000), 2)      # 更长的 buf：只靠 tag 判出新段
        self.assertEqual(len(rec.streams), 2)
        self.assertEqual(out, "n32000")

    def test_new_stream_on_shorter_buf_same_tag(self):
        rec = FakeRec()
        d = de.NemotronDrafter("", rec=rec)
        d.update(buf(32000), 1)
        d.update(buf(8000), 1)
        self.assertEqual(len(rec.streams), 2)
        np.testing.assert_array_equal(np.concatenate(rec.streams[1].fed), buf(8000))

    def test_missing_files_raise(self):
        d = tempfile.mkdtemp(dir=_TMP_HOME)
        with self.assertRaises(Exception):
            de.NemotronDrafter(d)


class Finish(unittest.TestCase):
    """段尾收一次（3.9.4）：剩余样本 + 0.8 s 静音 + input_finished → 本段完整假设。"""

    def test_feeds_rest_then_pad_then_finishes(self):
        rec = FakeRec()
        d = de.NemotronDrafter("", rec=rec)
        d.update(buf(16000), 3)
        out = d.finish(buf(20000), 3)
        s = rec.streams[0]
        self.assertTrue(s.finished)
        np.testing.assert_array_equal(np.concatenate(s.fed[:2]), buf(20000))    # 剩下那截一个不少
        self.assertEqual(len(s.fed[2]), int(de.TAIL_PAD_S * 16000))
        self.assertFalse(np.any(s.fed[2]))                                      # 补的是静音
        self.assertEqual(out, f"n{20000 + int(de.TAIL_PAD_S * 16000)}")

    def test_no_draft_this_segment_costs_nothing(self):
        rec = FakeRec()
        d = de.NemotronDrafter("", rec=rec)
        d.update(buf(16000), 1)
        self.assertEqual(d.finish(buf(30000), 2), "")         # 段 2 一份草稿都没跑过
        self.assertEqual(len(rec.streams), 1)

    def test_late_buffer_of_finished_segment_is_dropped(self):
        rec = FakeRec()
        d = de.NemotronDrafter("", rec=rec)
        d.update(buf(16000), 4)
        d.finish(buf(16000), 4)
        self.assertEqual(d.update(buf(16000), 4), "")         # 晚到的旧缓冲：不重解整段
        self.assertEqual(len(rec.streams), 1)
        self.assertEqual(d.update(buf(8000), 5), "n8000")     # 下一段照常
        self.assertEqual(len(rec.streams), 2)

    def test_engine_no_model_finish_is_empty(self):
        self.assertEqual(de.DraftEngine().finish(buf(100), 0), "")
        rec = FakeRec(fail_on=2)
        eng = de.DraftEngine(de.NemotronDrafter("", rec=rec))
        eng.transcribe(buf(16000), 0)
        self.assertEqual(eng.finish(buf(16000), 0), "")       # get_result 抛 → 不抛、熔断
        self.assertEqual(eng.name, "none")


class Engine(unittest.TestCase):
    def test_error_trips_to_no_draft_and_never_retries(self):
        """⭐⭐ 熔断 → **本节无草稿**（2026-11-20 之前是"退 Parakeet"，那个退路已删）。"""
        rec = FakeRec(fail_on=2)
        eng = de.DraftEngine(de.NemotronDrafter("", rec=rec))
        self.assertEqual(eng.transcribe(buf(16000), 1), ("n16000", "nemotron"))
        self.assertEqual(eng.transcribe(buf(32000), 1), ("", "none"))
        self.assertEqual(eng.name, "none")
        self.assertIn("boom", eng.fallback_reason)
        calls = rec.calls
        self.assertEqual(eng.transcribe(buf(48000), 1), ("", "none"))
        self.assertEqual(rec.calls, calls)           # 熔断后不再碰流式

    def test_no_model_means_no_draft(self):
        eng = de.DraftEngine(reason="流式模型未下载")
        self.assertEqual(eng.transcribe(buf(WIN + 5000), 1), ("", "none"))
        self.assertEqual(eng.name, "none")
        self.assertEqual(eng.reason, "流式模型未下载")
        self.assertEqual(eng.fallback_reason, "")
        self.assertTrue(eng.ready)

    def _clocked(self, durations):
        ts = []
        t = 0.0
        for d in durations:
            ts += [t, t + d]
            t += d + 1
        it = iter(ts)
        return lambda: next(it)

    def test_slow_trips_after_three_strikes(self):
        slow = de.SLOW_S + 0.1
        rec = FakeRec()
        eng = de.DraftEngine(de.NemotronDrafter("", rec=rec),
                             clock=self._clocked([slow, slow, slow]))
        for i in range(3):
            _, used = eng.transcribe(buf(16000 * (i + 1)), 1)
            self.assertEqual(used, "nemotron")       # 第 3 份照用
        self.assertEqual(eng.name, "none")
        self.assertIn("太慢", eng.fallback_reason)
        self.assertEqual(eng.transcribe(buf(64000), 1)[1], "none")

    def test_fast_call_resets_strikes(self):
        slow, fast = de.SLOW_S + 0.1, 0.1
        eng = de.DraftEngine(de.NemotronDrafter("", rec=FakeRec()),
                             clock=self._clocked([slow, slow, fast, slow, slow]))
        for i in range(5):
            self.assertEqual(eng.transcribe(buf(16000 * (i + 1)), 1)[1], "nemotron")
        self.assertEqual(eng.name, "nemotron")


class Load(unittest.TestCase):
    def setUp(self):
        self.model = tempfile.mkdtemp(dir=_TMP_HOME)
        pathlib.Path(self.model, "tokens.txt").write_text("x")

    def _load(self, env, make=None):
        made = []

        def default_make(d):
            made.append(d)
            return de.NemotronDrafter("", rec=FakeRec())
        eng = de.load_draft_engine(env=env, make_drafter=make or default_make)
        return eng, made

    def test_auto_with_model_picks_nemotron(self):
        eng, made = self._load({de.ENV_MODEL_DIR: self.model})
        self.assertEqual(eng.name, "nemotron")
        self.assertEqual(made, [self.model])

    def test_env_off_means_no_draft(self):
        eng, made = self._load({de.ENV: "off", de.ENV_MODEL_DIR: self.model})
        self.assertEqual(eng.name, "none")
        self.assertEqual(made, [])
        self.assertIn("关闭", eng.reason)

    def test_missing_dir_is_no_draft(self):
        eng, _ = self._load({de.ENV: "auto", de.ENV_MODEL_DIR: "/nonexistent/x"})
        self.assertEqual(eng.name, "none")
        self.assertIn("未下载", eng.reason)

    def test_constructor_raises_is_no_draft_not_raise(self):
        def bad(_):
            raise ImportError("no sherpa")
        eng, _ = self._load({de.ENV_MODEL_DIR: self.model}, make=bad)
        self.assertEqual(eng.name, "none")
        self.assertIn("no sherpa", eng.reason)

    def test_real_constructor_on_incomplete_dir_is_no_draft(self):
        eng = de.load_draft_engine(env={de.ENV_MODEL_DIR: self.model})
        self.assertEqual(eng.name, "none")
        self.assertIn("加载失败", eng.reason)

    def test_default_dir_comes_from_registry(self):
        import models
        self.assertTrue(models.path_of("nemotron"))
        eng, made = self._load({})
        # 临时 HOME 下注册表路径不存在 → 未下载；关键是没抛、没构造
        self.assertEqual((eng.name, made), ("none", []))


class SentenceEnds(unittest.TestCase):
    """3.9.4 句末切段的提示：草稿线程放下 `(段号, 封口句末…)`，音频线程按段号取。"""

    def test_only_sealed_end_punct_counts(self):
        toks = [" so", " it", ".", " Next", " point", "3", ".", "5", " is", "?"]
        ts = [0.1, 0.4, 1.0, 1.2, 1.5, 2.0, 2.1, 2.2, 2.6, 3.0]
        # "." 后跟 " Next" → 封口；"3.5" 里的 "." 后面不是空格开头 → 不算；段尾的 "?" 没下文 → 不算
        self.assertEqual(de.sealed_ends(toks, ts), (1.0,))
        self.assertEqual(de.sealed_ends([], []), ())
        self.assertEqual(de.sealed_ends([".", " A"], [0.5]), ())   # 时间戳缺一截 → 不越界

    def _rec(self, toks, ts):
        class R:
            tokens, timestamps = toks, ts

        class Rec(FakeRec):
            def get_result_all(self, s):
                return R()
        return Rec()

    def test_update_publishes_ends_for_its_tag(self):
        rec = self._rec([" a", ".", " B"], [0.5, 3.1, 3.3])
        eng = de.DraftEngine(de.NemotronDrafter("", rec=rec))
        eng.transcribe(buf(1600), 7)
        self.assertEqual(eng.sentence_ends(7), (3.1,))
        self.assertEqual(eng.sentence_ends(8), (), "别把上一段的句号当成这一段的（时刻是相对各自段首的）")

    def test_no_timestamps_or_no_model_is_empty_not_raise(self):
        eng = de.DraftEngine(de.NemotronDrafter("", rec=FakeRec()))
        eng.transcribe(buf(1600), 0)                 # FakeRec 没有 get_result_all → 草稿照出、提示为空
        self.assertEqual(eng.sentence_ends(0), ())
        self.assertEqual(de.DraftEngine().sentence_ends(0), ())

    def test_main_asks_with_the_current_segment(self):
        src = (HERE / "main.py").read_text(encoding="utf-8")
        self.assertIn('sentence_ends=lambda: draft.sentence_ends(seg_gen["n"])', src)


class Wiring(unittest.TestCase):
    """`main.partial_worker` 是嵌套闭包，跑不到 → 用 AST 钉「段号只有一个来源」。

    缺陷（2026-10-11 抓到）：`drafts` 元组用 put 时刻的段号，送翻却现读 `seg_gen["n"]`。
    段边界上 worker 晚一拍取到上一段的缓冲时，这段话的译文被标成**下一段** →
    `DraftView.accept` 放行 → 上一段的中文在下一段的草稿槽里冒出来（e2e 里 2/104 份草稿是这种晚到的）。
    """

    def _worker(self):
        import ast
        tree = ast.parse((HERE / "main.py").read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if isinstance(n, ast.FunctionDef) and n.name == "partial_worker":
                return n
        self.fail("main.py 里找不到 partial_worker")

    def test_seg_gen_is_read_once_as_fallback(self):
        import ast
        w = self._worker()
        reads = [n for n in ast.walk(w) if isinstance(n, ast.Subscript)
                 and isinstance(n.value, ast.Name) and n.value.id == "seg_gen"]
        self.assertEqual(len(reads), 1, "partial_worker 里只许在 `_seg = … else seg_gen[\"n\"]` 读一次")

    def test_submit_and_drafts_use_the_same_seg(self):
        import ast
        w = self._worker()
        calls = [n for n in ast.walk(w) if isinstance(n, ast.Call)]
        subs = [ast.unparse(n.args[1]) for n in calls if ast.unparse(n.func) == "draft_tr.submit"]
        puts = [ast.unparse(n.args[0].elts[4]) for n in calls if ast.unparse(n.func) == "drafts.put"]
        # 两条路：逐秒草稿用 put 时刻的 `_seg`；段尾收尾（3.9.4）用随 `put_end` 一起来的 `_eseg`。
        self.assertEqual(sorted(subs), ["_eseg", "_seg"])
        self.assertEqual(sorted(puts), ["_eseg", "_seg"])
        src = ast.unparse(w)
        self.assertIn("_eseg = _end[1]", src, "段尾那条的段号必须取自 put_end 带来的那个，别现读")

    def test_tail_identical_to_last_draft_is_not_sent_again(self):
        import ast
        src = ast.unparse(self._worker())
        self.assertIn("if t and (not (_end[1] == last_gen and t == prev_text)):", src,
                      "段尾收尾和这一段上一份草稿一字不差时，不许再上屏一遍、再送翻一次")



class Handoff(unittest.TestCase):
    """⭐ D1b：**段尾转录交接处** —— 草稿线程放、定稿线程取。

    它替代了 D1 的「拿整段重新解码」（`nemotron_final.NemotronFinal`，已删）：
    同一段话草稿线程在段尾那一刻就解完了，再解一遍纯属重复劳动（多 886 MB、慢 3.4 倍）。
    """

    def test_put_then_take_same_segment(self):
        t = de.SegmentTail()
        t.put(7, "hello there")
        self.assertEqual(t.take(7), "hello there")
        self.assertEqual((t.puts, t.hits, t.misses), (1, 1, 0))

    def test_empty_text_is_not_a_transcript(self):
        t = de.SegmentTail()
        t.put(3, "")                                  # 没喂到 / 熔断 → 空串
        t.put(4, None)
        t.put(None, "x")
        self.assertEqual(t.puts, 0, "空串 / 没段号被当成转录入账了")
        self.assertIsNone(t.take(3, timeout=0))

    def test_take_waits_for_a_late_put(self):
        """定稿往往比草稿早几毫秒排进队列 —— 短等一次能把「同一次段尾」对上。"""
        import threading
        import time
        t = de.SegmentTail(wait_s=0.5)
        th = threading.Thread(target=lambda: (time.sleep(0.05), t.put(5, "late")))
        th.start()
        got = t.take(5)                               # 默认用构造时的 wait_s
        th.join(2)
        self.assertEqual(got, "late", "短等没等到后到的 put")

    def test_take_gives_up_and_says_miss(self):
        import time
        t = de.SegmentTail(wait_s=0.05)
        t0 = time.monotonic()
        self.assertIsNone(t.take(9))
        dt = time.monotonic() - t0
        self.assertLess(dt, 0.35, f"等了 {dt:.2f}s —— 不许拖慢定稿")
        self.assertEqual(t.misses, 1)

    def test_keep_is_bounded(self):
        """段号单调递增 → 「只留最近 N 段」就是丢掉最小的那些。"""
        t = de.SegmentTail(keep=3)
        for i in range(6):
            t.put(i, f"t{i}")
        self.assertEqual(sorted(t._tails), [3, 4, 5], "有界没生效（内存会随课时长涨）")
        self.assertIsNone(t.take(0, timeout=0), "最老的段不该还在")

    def test_survives_concurrent_put_take(self):
        import threading
        # ⚠️ `keep` 必须 >= 总段数：否则生产者一口气放完 200 段，最老的那些**先被淘汰**，
        #    消费者再来等就是「段号已过期」——那是淘汰该有的行为，不是并发串了。
        #    （第一版写成 keep=64 就在这儿假红过一次。）
        t = de.SegmentTail(keep=256, wait_s=0.5)
        bad: list = []

        def producer():
            for i in range(200):
                t.put(i, f"t{i}")

        def consumer():
            for i in range(200):
                got = t.take(i)
                if got != f"t{i}":
                    bad.append((i, got))

        ths = [threading.Thread(target=producer), threading.Thread(target=consumer)]
        [x.start() for x in ths]
        [x.join(5) for x in ths]
        self.assertEqual(bad[:3], [], f"并发下丢了/取错了：{bad[:3]}")

    def test_clear(self):
        t = de.SegmentTail()
        t.put(1, "a")
        t.clear()
        self.assertIsNone(t.take(1, timeout=0))

    def test_handle_pins_the_segment_and_ignores_audio(self):
        """`final_asr` 认的形状：`handle(tag).transcribe_lp(音频)` —— **按段号取，忽略音频**。"""
        t = de.SegmentTail()
        t.put(12, "that segment")
        h = t.handle(12)
        self.assertEqual(h.transcribe_lp(buf(99999)), ("that segment", None))
        self.assertEqual(h.transcribe(buf(1)), "that segment")
        # 没这一段 → 空（调用方据此走「这一段没有定稿」那条告警路径）
        self.assertEqual(t.handle(13).transcribe_lp(buf(1600)), ("", None))
        # 不知道段号 → 永远给空，别猜
        self.assertEqual(t.handle(None).transcribe_lp(buf(1600)), ("", None))

    def test_never_raises(self):
        t = de.SegmentTail()
        t.put(1, "x")
        self.assertIsNotNone(t.take(1))

class LatestEnd(unittest.TestCase):
    """`main._Latest.put_end`：段尾那份**不被后来的逐秒缓冲顶掉**，并叫醒等着的 `take()`。"""

    def test_end_survives_later_put_and_wakes_take(self):
        import threading
        import time

        import main
        lt = main._Latest()
        got = {}

        def consumer():
            t0 = time.monotonic()
            got["buf"] = lt.take(2.0)
            got["dt"] = time.monotonic() - t0
            got["end"] = lt.take_end()
        th = threading.Thread(target=consumer)
        th.start()
        time.sleep(0.05)
        lt.put_end("SEG3", 3)
        th.join(3)
        self.assertLess(got["dt"], 1.0, "put_end 没叫醒 take()（要等满超时才收尾）")
        self.assertIsNone(got["buf"])
        self.assertEqual(got["end"], ("SEG3", 3))
        lt.put_end("SEG4", 4)
        lt.put("partial-of-5", 5)                   # 下一段的逐秒缓冲紧跟着到
        self.assertEqual(lt.take(0.1), "partial-of-5")
        self.assertEqual(lt.take_end(), ("SEG4", 4), "段尾那份被逐秒缓冲顶掉了")
        self.assertIsNone(lt.take_end())

if __name__ == "__main__":
    unittest.main(verbosity=2)
