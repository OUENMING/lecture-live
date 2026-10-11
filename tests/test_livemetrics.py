"""`livemetrics.py`（现场采集）的判据 —— 全离线、毫秒级、不碰真 `sessions/`。

    ClassLive.app/Contents/MacOS/python tests/test_livemetrics.py

## 钉住什么

1. **默认关**：不设 `CLASSLIVE_LIVE_LOG` 时 `open_for()` 返回 `None`
   —— 生产路径"一个字不改"这句话的唯一依据。
2. **一行一个 JSON、字段齐全**，且**写盘线程真的写完了**（`close()` 之后文件里
   的条数 = 放进去的条数，一条不丢）。
3. ⚠️ **绝不抛**：主循环里调用的那三个方法（`draft`/`draft_zh`/`final`）
   在任何状态下都不许抛 —— 包括已经 `close()` 之后、包括盘写不动的时候。
   （与 `testmode.py` 同一条设计前提："记录失败是少一份数据，不是课跑不下去"。）
4. **`final` 推进句号**：草稿归"它之后第一个 final"。
"""
from __future__ import annotations

import json
import os
import queue
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import livemetrics as LM


def _lines(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


def _mk_log():
    """一份临时 `.live.jsonl` 里的 `LiveLog` —— 三个测试类共用一份（原先抄了三遍）。"""
    d = tempfile.mkdtemp()
    p = pathlib.Path(d) / "s.live.jsonl"
    return LM.LiveLog(p), p


class OpenFor(unittest.TestCase):
    def test_off_by_default(self):
        old = os.environ.pop("CLASSLIVE_LIVE_LOG", None)
        try:
            self.assertIsNone(LM.open_for("/tmp/x/session.md"))
        finally:
            if old is not None:
                os.environ["CLASSLIVE_LIVE_LOG"] = old

    def test_default_applies_only_when_env_is_unset(self):
        """测试模式传 `default=True`：环境变量没设 → 开；显式 `0` → 仍然关。"""
        d = tempfile.mkdtemp()
        old = os.environ.pop("CLASSLIVE_LIVE_LOG", None)
        try:
            log = LM.open_for(os.path.join(d, "a.md"), default=True)
            self.assertIsNotNone(log)
            log.close()
            os.environ["CLASSLIVE_LIVE_LOG"] = "0"
            self.assertIsNone(LM.open_for(os.path.join(d, "b.md"), default=True))
        finally:
            os.environ.pop("CLASSLIVE_LIVE_LOG", None)
            if old is not None:
                os.environ["CLASSLIVE_LIVE_LOG"] = old

    def test_no_session_path_means_none(self):
        """没有落点就**不采**（不许退回别的目录造出第二个数据落点）。"""
        self.assertIsNone(LM.open_for(None, enabled=True))

    def test_env_switch_on(self):
        d = tempfile.mkdtemp()
        log = LM.open_for(os.path.join(d, "2026-10-10_120000_TEST.md"), enabled=True)
        self.assertIsNotNone(log)
        self.assertEqual(pathlib.Path(log.path).name, "2026-10-10_120000_TEST.live.jsonl")
        log.close()

    def test_env_var_value_is_read(self):
        d = tempfile.mkdtemp()
        os.environ["CLASSLIVE_LIVE_LOG"] = "1"
        try:
            log = LM.open_for(os.path.join(d, "a.md"))
        finally:
            os.environ.pop("CLASSLIVE_LIVE_LOG", None)
        self.assertIsNotNone(log)
        log.close()


class Writes(unittest.TestCase):
    def test_fields_survive_the_round_trip(self):
        log, p = _mk_log()
        log.draft("hello world", "hello world", buf_s=1.5, compute_s=0.31)
        log.draft_zh("你好")
        log.final("Hello world.", "你好世界。", "hello world")
        log.event("tap_probe", ok=True)
        log.close(echo_retries=2)

        recs = _lines(p)
        self.assertEqual([r["kind"] for r in recs],
                         ["event", "draft", "draft_zh", "final", "event", "summary"])
        self.assertEqual(recs[0]["event"], "meta")          # 头一行是 meta 事件
        d = recs[1]
        self.assertEqual((d["text"], d["raw"], d["buf_s"], d["compute_s"]),
                         ("hello world", "hello world", 1.5, 0.31))
        self.assertEqual(d["utt"], 0)
        self.assertEqual(recs[3]["utt"], 0)
        self.assertEqual(recs[3]["text"], "Hello world.")
        self.assertEqual(recs[3]["zh"], "你好世界。")
        self.assertTrue(recs[4]["ok"])
        self.assertEqual(recs[5]["echo_retries"], 2)
        self.assertEqual(recs[5]["dropped"], 0)
        # 行号连续且从 1 开始（每行唯一）
        self.assertEqual([r["i"] for r in recs], list(range(1, len(recs) + 1)))

    def test_raw_and_shown_are_both_kept(self):
        """⭐ 同场 A/B 的**全部依据**：冻结改了上屏文本，但原始假设必须原样留着。"""
        log, p = _mk_log()
        log.draft("the quantity falls by", "the quantity", buf_s=2.0)
        log.close()
        r = _lines(p)[1]
        self.assertEqual(r["raw"], "the quantity falls by")
        self.assertEqual(r["text"], "the quantity")

    def test_shown_defaults_to_raw(self):
        log, p = _mk_log()
        log.draft("abc")
        log.close()
        r = _lines(p)[1]
        self.assertEqual((r["raw"], r["text"]), ("abc", "abc"))

    def test_final_advances_utt(self):
        log, p = _mk_log()
        log.draft("one")
        log.final("one", "一", "one")
        log.draft("two")
        log.final("two", "二", "two")
        log.draft("three")
        log.close()
        recs = [r for r in _lines(p) if r["kind"] in ("draft", "final")]
        self.assertEqual([(r["kind"], r["utt"]) for r in recs],
                         [("draft", 0), ("final", 0), ("draft", 1), ("final", 1), ("draft", 2)])

    def test_nothing_is_lost_when_the_queue_is_not_full(self):
        """不变量：不丢的时候，放进去多少行就写出来多少行。"""
        log, p = _mk_log()
        for i in range(500):
            log.draft(f"w{i}", f"w{i}")
        log.final("end", "完", "end")
        log.close()
        drafts = [r for r in _lines(p) if r["kind"] == "draft"]
        self.assertEqual(len(drafts), 500)
        self.assertEqual(log.dropped, 0)

    def test_multi_kind_order_is_preserved(self):
        """写盘线程是**单条**队列 → 顺序必须与调用顺序一致（跨 kind 也是）。"""
        log, p = _mk_log()
        log.draft("a")
        log.draft_zh("甲")
        log.final("a", "甲", "a")
        log.draft("b")
        log.close()
        kinds = [r["kind"] for r in _lines(p)]
        self.assertEqual(kinds, ["event", "draft", "draft_zh", "final", "draft", "summary"])


class NeverRaises(unittest.TestCase):
    """⚠️ 这几条是**最重要**的一组：主循环里调用，抛一次就可能打死 UI。"""

    def test_all_methods_after_close(self):
        d = tempfile.mkdtemp()
        log = LM.LiveLog(pathlib.Path(d) / "x.live.jsonl")
        log.close()
        log.draft("a", "b")            # 不许抛
        log.draft_zh("啊")
        log.final("a", "啊", "a")
        log.event("late")
        log.close()                    # 幂等

    def test_unserializable_value_does_not_kill_the_writer_thread(self):
        """`json.dumps` 抛（值不可序列化）时：那一行丢掉，**线程要活着**，
        后面的行照写 —— 否则一次意外会让整节课的采集静默停摆。"""
        d = tempfile.mkdtemp()
        p = pathlib.Path(d) / "x.live.jsonl"
        log = LM.LiveLog(p)
        log.event("bad", obj=object())     # → TypeError inside _write
        log.event("good", ok=1)
        log.close()
        kinds = [r.get("event") for r in _lines(p)]
        self.assertIn("good", kinds)

    def test_no_writer_thread_when_file_cannot_be_opened(self):
        """开不了文件 → 对象仍然可用（方法全吞），`closed` 为真、`_err` 有话说。"""
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "adir"))
        log = LM.LiveLog(pathlib.Path(d) / "adir")       # 目录当文件
        self.assertTrue(log.closed)
        self.assertTrue(log._err)
        log.draft("a", "a")
        log.close()

    def test_dropped_counts_when_queue_is_full(self):
        """队列满 → 丢弃并计数，**且不抛**。

        ⚠️ 构造是**确定性**的：先把写盘线程收掉（`close()` 会 join 它），再把队列换成
           一个"已满且没人消费"的 —— 于是每一次 `_put` 都必然走 `Full` 那条路。
           （不这么构造的话是赛跑：写盘线程一边写一边腾位，丢不丢看运气。）
        """
        d = tempfile.mkdtemp()
        p = pathlib.Path(d) / "x.live.jsonl"
        log = LM.LiveLog(p)
        log.close()                                      # 写盘线程已退出
        log._q = queue.Queue(maxsize=1)
        log._q.put_nowait({"i": 0})                      # 占满，且没人取
        log.dropped, log.closed, log._stopping = 0, False, False
        for i in range(30):
            log.draft(f"w{i}", f"w{i}")
        self.assertEqual(log.dropped, 30)
        # 行号不许因为丢弃而留空洞（丢了的那些要把号还回去）
        before = log._i
        log._q.get_nowait()
        log.draft("ok")
        rec = log._q.get_nowait()
        self.assertEqual(rec["i"], before + 1)


class Segments(unittest.TestCase):
    """⭐ 段号字段 —— 现场采集的**分组键**（一段 VAD 会被切成多句定稿，草稿:定稿 ≠ 1:1）。

    ⚠️ 这一组钉的是 2026-10-09 真跑抓到的两个缺陷：
    ① 按"一句定稿"分组 → 32 组里 14 组只有 0–1 份草稿，`草稿→定稿` 虚高到 93.3%；
    ② 段号原来取自 `main.final_gen["n"]`（**定稿上屏时**才自增，比段落真正结束晚 2–4 秒）
       → 新段前几份草稿带着上一段的号 → 虚高到 65%。

    ⚠️ **计数器的所有者不是本模块**：段号是**流水线的身份**，由 `main.run()` 的
       `seg_gen` 铸造（否则"日志一关段号恒为 0"就成了流水线数据形状的一部分）。
       这里只钉"给什么就记什么"。
    """

    def test_draft_records_the_seg_it_is_given(self):
        log, p = _mk_log()
        log.draft("old one", seg=0)
        log.draft("new one", seg=1)
        log.close()
        self.assertEqual([r["seg"] for r in _lines(p) if r["kind"] == "draft"], [0, 1])

    def test_default_seg_is_zero(self):
        log, p = _mk_log()
        log.draft("a")
        log.close()
        self.assertEqual([r["seg"] for r in _lines(p) if r["kind"] == "draft"], [0])

    def test_segment_end_is_only_a_sink(self):
        """`segment_end(k)` 记一行 event，**不改变**之后草稿的 `seg`（它不是所有者）。"""
        log, p = _mk_log()
        log.draft("before", seg=0)
        log.segment_end(0)
        log.draft("after")                       # 不给 seg → 默认 0，不因为 event 而变
        log.close()
        recs = _lines(p)
        self.assertEqual([r["seg"] for r in recs if r["kind"] == "draft"], [0, 0])
        ev = [r for r in recs if r.get("event") == "segment_end"]
        self.assertEqual([r["seg"] for r in ev], [0])

    def test_final_carries_the_segment_it_came_from(self):
        """定稿可以**晚于**下一段的第一份草稿到达 —— 它仍要算在原来那段上。

        （生产里就是这样：定稿那条路要先跑 Whisper + LLM，比段落结束晚 2–4 秒。）
        """
        log, p = _mk_log()
        log.draft("d0", seg=0)
        log.draft("d1", seg=1)                   # 新段的第一份草稿先上
        log.final("the sentence", "这句", "the sentence", seg=0)   # 老段的定稿姗姗来迟
        log.close()
        recs = _lines(p)
        self.assertEqual([r["seg"] for r in recs if r["kind"] == "draft"], [0, 1])
        self.assertEqual([r["seg"] for r in recs if r["kind"] == "final"], [0])


class InterpreterAndTiming(unittest.TestCase):
    def test_t_is_monotonic_nondecreasing(self):
        log, p = _mk_log()
        for i in range(20):
            log.draft(f"w{i}")
        log.close()
        ts = [r["t"] for r in _lines(p)]
        self.assertEqual(ts, sorted(ts))


class Blocks(unittest.TestCase):
    """逐块记录（`block()` → `<会话名>.blocks.npz`）。"""

    def test_values_round_trip_exactly_and_summary_points_at_the_file(self):
        import numpy as np
        log, p = _mk_log()
        rows = [(0.01, 31.6, 0.0, 1600), (0.5, 1.9, 1.0, 1600), (0.03, 30.0, 0.5, 800)]
        for r in rows:
            log.block(*r)
        log.close()
        npz = p.with_name("s.blocks.npz")         # `s.live.jsonl` → `s.blocks.npz`（不是 `s.live.blocks.npz`）
        with np.load(npz) as z:
            for i, k in enumerate(("peak", "gain", "vad", "n")):
                np.testing.assert_allclose(z[k], [r[i] for r in rows], rtol=1e-6)
        summ = [r for r in _lines(p) if r["kind"] == "summary"][-1]
        self.assertEqual(summ["blocks"], 3)
        self.assertEqual(summ["blocks_file"], "s.blocks.npz")

    def test_no_blocks_no_file(self):
        log, p = _mk_log()
        log.draft("x")
        log.close()
        self.assertFalse(p.with_name("s.blocks.npz").exists())
        self.assertNotIn("blocks", [r for r in _lines(p) if r["kind"] == "summary"][-1])

    def test_never_overwrites_an_existing_file(self):
        """`sessions/` 只追加：同名已在 → 放弃写，原文件一个字节不动，并在 err 里说出来。"""
        log, p = _mk_log()
        npz = p.with_name("s.blocks.npz")
        npz.write_bytes(b"precious")
        log.block(0.1, 2.0, 1.0, 1600)
        log.close()
        self.assertEqual(npz.read_bytes(), b"precious")
        self.assertIn("已存在", [r for r in _lines(p) if r["kind"] == "summary"][-1]["err"])

    def test_block_never_raises(self):
        log, _ = _mk_log()
        log.block("not-a-number", None, object(), -1)   # 类型全错也不抛
        log.close()
        log.block(0.1, 1.0, 1.0, 1600)                   # 收尾之后也不抛

    def test_a_bad_row_never_misaligns_the_columns(self):
        """坏的一行**整行**不进；不许出现「peak 进了、gain 没进」→ 之后每一块都错位。"""
        import numpy as np
        log, p = _mk_log()
        log.block(0.1, 2.0, 1.0, 1600)
        log.block(0.2, None, 1.0, 1600)                  # 第二列坏
        log.block(0.3, 3.0, 0.0, -5)                     # 最后一列坏
        log.block(0.4, 4.0, 0.5, 1600)
        log.close()
        with np.load(p.with_name("s.blocks.npz")) as z:
            self.assertEqual({k: len(z[k]) for k in z.files}, {"peak": 2, "gain": 2, "vad": 2, "n": 2})
            np.testing.assert_allclose(z["gain"], [2.0, 4.0])


class Lag(unittest.TestCase):
    def test_lag_written_only_when_given(self):
        log, p = _mk_log()
        log.draft("a", buf_s=1.0, lag_s=0.4567)
        log.draft("b", buf_s=2.0)
        log.close()
        d = [r for r in _lines(p) if r["kind"] == "draft"]
        self.assertEqual(d[0]["lag_s"], 0.457)
        self.assertNotIn("lag_s", d[1])


class Engine(unittest.TestCase):
    def test_engine_written_only_when_given(self):
        log, p = _mk_log()
        log.draft("a", buf_s=1.0, engine="nemotron")
        log.draft("b", buf_s=2.0)
        log.close()
        d = [r for r in _lines(p) if r["kind"] == "draft"]
        self.assertEqual(d[0]["engine"], "nemotron")
        self.assertNotIn("engine", d[1])




if __name__ == "__main__":
    unittest.main(verbosity=2)
