#!/usr/bin/env python3
"""跨段半句合并（3.9.7 第 4 项）的判据 —— 全部离线（不联网、不写 sessions/）。

    ClassLive.app/Contents/MacOS/python tests/test_merge_replace.py

钉住了什么：
 1. `fragment_merge.should_merge` 纯函数：两套口径（ASR `end_punct` / 屏上标点）、
    gap 边界 1.49/1.5、空串、下一段无首句、gap 缺失、阈值常量。
 2. `transcript_view.LineLog`：按**身份号**定位的原子「替换尾行 + 吞掉下一行」，
    中间插了别的行也对；定位不到 / 同 id / 逆序 → 不动。
 3. `Overlay.replace_merge`（起 AppKit，缺则跳过）：一次 UI 操作改两行、
    **起跑「合并过渡」而不是上滚**（反向 roll：被吞掉那一行原地淡出、上面的行滑下来、
    尾句新文字淡换 —— S1）；Reduce Motion 下只淡换不位移；没人推帧时老实落位。
 4. 端到端（桩翻译器 + 手搓 final→drain 流水线）：半句尾 + gap 0.4 → 屏上最终一行是
    合并译文；`.md` 字节与**不开此功能**时逐字节相同；`.merges.jsonl` 恰一行且字段齐；
    重翻失败 → 屏上保持**两行原样**、旁路文件一行都没有。
 5. 收尾：在飞的重翻算「忙」—— 收尾循环要等它（`all_settled` + `merge_pending`）。

⚠️ 写端隔离：`ow.SESSIONS` 指到 `tempfile`；不碰真 `sessions/`。
"""
from __future__ import annotations

import pathlib
import queue
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import fragment_merge as fm                                          # noqa: E402
import obsidian_writer as ow                                         # noqa: E402
import transcript_view as tv                                         # noqa: E402
from main import all_settled                                         # noqa: E402


def _fake_strftime(fmt="", *_a):
    """把落盘抬头的时间戳钉死 —— 两次跑（开/不开合并）的 `.md` 才能逐字节比。"""
    return {"%Y-%m-%d": "2026-01-01", "%H%M%S": "120000",
            "%H:%M:%S": "12:00:00"}.get(fmt, "12:00:00")


class TestShouldMerge(unittest.TestCase):
    def test_complete_sentence_no_merge(self):
        self.assertFalse(fm.should_merge("a complete sentence.", True, "Next one.", 0.4))

    def test_screen_punct_gap_ok_triggers(self):
        # 屏上没句末标点（ASR 有）-> 屏上口径成立
        self.assertTrue(fm.should_merge("a half sentence", True, "Next one.", 0.4))

    def test_asr_punct_gap_ok_triggers(self):
        # ASR 没句末标点（屏上补了句号）-> ASR 口径成立
        self.assertTrue(fm.should_merge("a complete sentence.", False, "Next one.", 0.4))

    def test_gap_boundary(self):
        self.assertTrue(fm.should_merge("half", False, "next.", 1.49))
        self.assertFalse(fm.should_merge("half", False, "next.", 1.5))     # 严格小于
        self.assertFalse(fm.should_merge("half", False, "next.", 9.9))

    def test_gap_missing_is_false(self):
        self.assertFalse(fm.should_merge("half", False, "next.", None))

    def test_empty_tail_never_merges(self):
        self.assertFalse(fm.should_merge("", False, "next.", 0.4))
        self.assertFalse(fm.should_merge("   ", False, "next.", 0.4))

    def test_no_next_sentence(self):
        self.assertFalse(fm.should_merge("half", False, "", 0.4))
        self.assertFalse(fm.should_merge("half", False, "  ", 0.4))

    def test_trigger_kind(self):
        self.assertEqual(fm.trigger_kind("half", False), "both")
        self.assertEqual(fm.trigger_kind("half", True), "screen")
        self.assertEqual(fm.trigger_kind("done.", False), "asr")
        self.assertEqual(fm.trigger_kind("done.", True), "")
        self.assertEqual(fm.trigger_kind("", False), "")

    def test_gap_constant_pinned_to_measurement(self):
        self.assertEqual(fm.MERGE_GAP_S, 1.5)

    def test_note_matches_report_punct(self):
        # 与 testmode.note_segment 的 end_punct 同口径（含 `…`）
        self.assertTrue(fm.asr_end_punct("done."))
        self.assertTrue(fm.asr_end_punct("really? "))
        self.assertTrue(fm.asr_end_punct("trailing …"))
        self.assertFalse(fm.asr_end_punct("half a sentence"))
        self.assertTrue(fm.asr_end_punct(""))          # 空文本按"有标点"算（没有可替换的行）


class TestMergeTracker(unittest.TestCase):
    def _tracker(self, gaps=None):
        gaps = gaps or {}
        return fm.MergeTracker(lambda i, j: gaps.get((i, j), 0.4))

    def test_fires_on_next_segment_first_line(self):
        tr = self._tracker()
        self.assertIsNone(tr.on_line(0, 10, "half a sentence", "半句", "10:00:01", False))
        job = tr.on_line(1, 11, "for a fireworks display.", "烟花表演。", "10:00:02", True)
        self.assertIsNotNone(job)
        self.assertEqual((job["i"], job["j"]), (0, 1))
        self.assertEqual(job["tail_tok"], 10)
        self.assertEqual(job["next_tok"], 11)
        self.assertEqual(job["trigger"], "both")
        self.assertEqual(job["tail_ts"], "10:00:01")

    def test_only_fires_once_per_prev_segment(self):
        tr = self._tracker()
        tr.on_line(0, 10, "half", "半句", "t0", False)
        self.assertIsNotNone(tr.on_line(1, 11, "first.", "首句", "t1", True))
        self.assertIsNone(tr.on_line(1, 12, "second.", "第二", "t2", True))   # 不是首行了
        self.assertIsNone(tr.on_line(2, 13, "third.", "第三", "t3", True))   # i=1 已处理

    def test_gap_too_big_no_job(self):
        tr = self._tracker({(0, 1): 2.0})
        tr.on_line(0, 10, "half", "半句", "t0", False)
        self.assertIsNone(tr.on_line(1, 11, "first.", "首句", "t1", True))


class TestLineLog(unittest.TestCase):
    def test_append_ids_increase(self):
        log = tv.LineLog()
        a = log.append("a.", "甲")
        b = log.append("b.", "乙")
        self.assertEqual((a, b), (1, 2))
        self.assertEqual(log.rows, [("a.", "甲"), ("b.", "乙")])
        self.assertEqual(log.ids, [1, 2])

    def test_replace_pair_atomic_and_identity(self):
        log = tv.LineLog()
        t0 = log.append("half a sentence", "半句")
        log.append("middle line.", "中间")            # 中间插了别的行
        t2 = log.append("next first.", "下一句")
        self.assertTrue(log.replace_pair(t0, t2, "merged.", "合并。"))
        self.assertEqual(log.rows, [("merged.", "合并。"), ("middle line.", "中间")])
        self.assertEqual(log.ids, [t0, 2])            # 身份表与行表同序等长
        self.assertEqual(len(log.rows), len(log.ids))

    def test_replace_pair_refuses_bad_ids(self):
        log = tv.LineLog()
        t0 = log.append("a.", "甲")
        t1 = log.append("b.", "乙")
        before = (list(log.rows), list(log.ids))
        self.assertFalse(log.replace_pair(t0, t0, "x", "y"))     # 同 id
        self.assertFalse(log.replace_pair(t0, 999, "x", "y"))    # 找不到
        self.assertFalse(log.replace_pair(t1, t0, "x", "y"))     # 逆序
        self.assertEqual((log.rows, log.ids), before, "失败路径一个字节都不该动")


class TestOverlayReplace(unittest.TestCase):
    """`Overlay.replace_merge` —— 起 AppKit（缺则跳过）。"""

    def _overlay(self, reduce_motion):
        try:
            import overlay
            from overlay import Overlay
        except Exception as e:                       # noqa: BLE001
            self.skipTest(f"AppKit 不可用, 跳过: {type(e).__name__}: {e}")
        o = Overlay()
        self.addCleanup(o.close)
        self.now = [100.0]
        o._tv._clock = lambda: self.now[0]
        o._tv._reduce_motion = lambda: reduce_motion
        o._tv._animate_ok = lambda: True
        return o, overlay

    def _settle(self, o, overlay):
        self.now[0] += overlay.motion.MOVE_S + 0.05
        o._tv.tick()

    def test_replace_two_lines_atomically(self):
        o, overlay = self._overlay(reduce_motion=False)
        t0 = o.finalize("half a sentence", "半句")
        o.finalize("middle line.", "中间")            # 中间插一行 —— 行号会漂, 身份不会
        t2 = o.finalize("for a fireworks display.", "烟花表演。")
        self._settle(o, overlay)
        self.assertFalse(o._tv.rolling)
        self.assertTrue(o.replace_merge(t0, t2, "merged.", "合并。"))
        self._settle(o, overlay)
        self.assertEqual(o._history, [("merged.", "合并。"), ("middle line.", "中间")])

    def test_replace_rolls_the_merge_not_the_scroll(self):
        """⭐ 半句合并那一帧起跑的是**合并过渡**（反向 roll），不是上滚。

        ⚠️ 2026-10-16 改（S1, 报告 §5 方案 C1）: 这里原来断言「行数变少 -> 结构上不滚」。
        现在少一行**要过渡** —— 被吞掉那一行原地淡出、上面的行按 `MOVE_S` 滑下来、
        尾句那一行的新文字按 `STATE_S` 淡换（已显示文字被改 = 分心, CHI 2023 N=123）。
        判据因此改成「起跑的是 `_roll_from < 0` 那一支 + 淡入名单只有尾句那一行」，
        并且位移**从 `_roll_off_lo` 层起**（实时行那一层不许跟着动）。
        """
        for rm in (False, True):                    # Reduce Motion: 只淡换、不位移
            with self.subTest(reduce_motion=rm):
                o, overlay = self._overlay(reduce_motion=rm)
                t0 = o.finalize("half a sentence", "半句")
                t1 = o.finalize("for a fireworks display.", "烟花表演。")
                self._settle(o, overlay)
                self.assertFalse(o._tv.rolling)
                self.assertTrue(o.replace_merge(t0, t1, "merged.", "合并。"))
                self.assertTrue(o._tv.rolling, "少一行的那一下没起跑")
                self.assertEqual(o._tv._roll_from, 0.0 if rm else -o._tv._row_h)
                self.assertEqual(o._tv._roll_fade_n, 1)          # 只有尾句那一行淡入
                self.assertEqual(o._tv._roll_fade_s, tv.MERGE_FADE_S)
                self.assertEqual(o._tv._roll_off_lo, 0)          # 没有实时行 -> 从最底起
                self._settle(o, overlay)
                self.assertEqual(o._tv._roll_off, 0.0)
                self.assertFalse(o._tv.rolling)

    def test_replace_merge_falls_back_when_no_frames(self):
        """没人推帧（`_animate_ok` 假）时**老实落位**：一个字节都不该悬在中间。

        （提交/收尾冲刷那条路只调 drain 不调 pump —— 起跑的动画会停在半路，见 `_start_roll`。）
        """
        o, overlay = self._overlay(reduce_motion=False)
        t0 = o.finalize("half a sentence", "半句")
        t1 = o.finalize("for a fireworks display.", "烟花表演。")
        self._settle(o, overlay)
        o._tv._animate_ok = lambda: False
        self.assertTrue(o.replace_merge(t0, t1, "merged.", "合并。"))
        self.assertFalse(o._tv.rolling)
        self.assertEqual(o._tv._roll_off, 0.0)
        self.assertEqual(o._tv._roll_fade_n, 0)
        self.assertIsNone(o._tv._ghost_j)

    def test_replace_missing_id_is_noop(self):
        o, overlay = self._overlay(reduce_motion=False)
        t0 = o.finalize("half a sentence", "半句")
        self._settle(o, overlay)
        before = list(o._history)
        self.assertFalse(o.replace_merge(t0, 12345, "x", "y"))
        self.assertEqual(o._history, before)


class _FakeUI:
    """只实现 drain 用到的那两个方法 + 身份账本（无 AppKit）。"""

    def __init__(self):
        self.log = tv.LineLog()

    def finalize(self, en, zh, *, clear_draft=True):
        return self.log.append(en, zh)

    def replace_merge(self, tail_tok, next_tok, en, zh):
        return self.log.replace_pair(tail_tok, next_tok, en, zh)


class _Harness:
    """手搓「final 上屏 -> 跨段判定 -> 重翻 -> 原子替换」这一条路（用真实实现）。"""

    def __init__(self, tmp):
        self.tmp = pathlib.Path(tmp)
        self.ui = _FakeUI()
        self.w = ow.ObsidianWriter(str(self.tmp / "vault"), "TESTM", mode="yes")
        self.tracker = fm.MergeTracker(lambda i, j: 0.4)

    def line(self, seg, en, zh, ts, end_punct):
        tok = self.ui.finalize(en, zh)
        # `append` 的返回值就是它写进 `.md` 抬头的时间戳；桩里用固定 ts 对齐。
        self.w.append(en, zh, raw=en)
        return self.tracker.on_line(seg, tok, en, zh, ts, end_punct)

    def apply(self, job, translate):
        job["ctx"] = []
        en, zh = fm.translate_merge(job, translate)
        if not (en or zh):
            return False
        if not self.ui.replace_merge(job["tail_tok"], job["next_tok"], en, zh):
            return False
        self.w.mark_merge(job["tail_ts"], job["next_ts"], en, zh,
                          gap_s=job["gap_s"], trigger=job["trigger"])
        return True


class _StubRes:
    def __init__(self, en, zh):
        self.en_fixed = en
        self.zh = zh


def _ok_translate(en, zh):
    def f(text, context, on_zh, on_en):
        assert "want to spend" in text and "for a fireworks" in text, text
        return _StubRes(en, zh)
    return f


def _boom_translate(*_a, **_k):
    raise RuntimeError("云端挂了")


class TestPipeline(unittest.TestCase):
    def _run(self, with_merge, translate):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(ow, "SESSIONS", pathlib.Path(d) / "sessions"), \
                mock.patch("obsidian_writer.time.strftime", _fake_strftime):
            h = _Harness(d)
            # 段 0（尾句是碎句，ASR 也没标点）
            self.assertIsNone(h.line(0, "So if you're deciding whether you want to spend",
                                     "所以如果你在决定要不要花", "10:00:01", False))
            job = h.line(1, "for a fireworks display.", "为了生日的烟花表演。",
                         "10:00:09", True)
            applied = h.apply(job, translate) if (with_merge and job) else False
            h.w.close_merges()
            md = h.w.session_path.read_bytes()
            mp = h.w._merge_path()
            merges = (mp.read_text(encoding="utf-8").splitlines()
                      if mp and mp.exists() else [])
            return md, merges, h.ui.log.rows, applied, job

    def test_success_merges_screen_and_sidecar(self):
        md, merges, rows, applied, job = self._run(True, _ok_translate("Merged.", "合并译文。"))
        self.assertTrue(applied)
        self.assertEqual(rows, [("Merged.", "合并译文。")], "屏上应只剩合并后那一行")
        self.assertEqual(len(merges), 1, ".merges.jsonl 应恰一行")
        import json
        rec = json.loads(merges[0])
        self.assertEqual(rec["kind"], "merge")
        self.assertEqual(rec["a"], "10:00:01")
        self.assertEqual(rec["b"], "10:00:09")
        self.assertEqual(rec["en"], "Merged.")
        self.assertEqual(rec["zh"], "合并译文。")
        self.assertEqual(rec["trigger"], "both")
        self.assertAlmostEqual(rec["gap_s"], 0.4, places=3)

    def test_md_bytes_identical_with_and_without_merge(self):
        md_on, _, _, _, _ = self._run(True, _ok_translate("Merged.", "合并译文。"))
        md_off, merges_off, rows_off, applied_off, _ = self._run(False, _ok_translate("x", "y"))
        self.assertFalse(applied_off)
        self.assertEqual(merges_off, [], "不开合并时不该有旁路文件内容")
        self.assertEqual(len(rows_off), 2, "不开合并时屏上是两行")
        self.assertEqual(md_on, md_off, "开了合并后 sessions/<名>.md 必须逐字节不变（三方契约）")

    def test_translate_failure_keeps_two_lines(self):
        md, merges, rows, applied, _ = self._run(True, _boom_translate)
        self.assertFalse(applied)
        self.assertEqual(len(rows), 2, "重翻失败 -> 屏上保持两行原样")
        self.assertEqual(merges, [], "没换成 -> 旁路文件一行都没有")
        self.assertIn(b"for a fireworks display.", md)


class TestSettleWaitsForMerge(unittest.TestCase):
    def test_all_settled_blocks_while_merge_inflight(self):
        finalq, streamq, drafts = queue.Queue(), queue.Queue(), queue.Queue()
        inflight = [1]

        def worker():
            time.sleep(0.3)
            streamq.put(("replace", {}, "en", "zh"))
            inflight[0] = 0

        t = threading.Thread(target=worker)
        t.start()
        got = []
        settled = False
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            while True:
                try:
                    got.append(streamq.get_nowait())
                except queue.Empty:
                    break
            if all_settled(finalq, streamq, drafts, False, "") and inflight[0] == 0:
                settled = True
                break
            time.sleep(0.02)
        t.join(timeout=5)
        self.assertTrue(settled, "收尾循环没等到在飞的重翻")
        self.assertTrue(any(x[0] == "replace" for x in got), "重翻结果丢了")

    def test_wiring_shape(self):
        # R28 已钉「tag 必须有 drain 分支」；这里再钉收尾闸门与线程接线（防止只写一半）。
        src = (HERE / "main.py").read_text(encoding="utf-8")
        self.assertIn('elif item[0] == "replace"', src)
        self.assertIn('threading.Thread(target=merge_worker', src)
        self.assertGreaterEqual(src.count('busy["on"] or merge_pending()'), 2,
                                "收尾冲刷的两处 all_settled 都要把在飞的重翻算「忙」")


if __name__ == "__main__":
    unittest.main(verbosity=2)
