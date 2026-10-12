#!/usr/bin/env python3
"""撞 12s 上限时切在句末（3.9.4，`vad.find_sentence_cut` + `Segmenter(sentence_ends=…)`）。

跑法: ClassLive.app/Contents/MacOS/python tests/test_vad_sentence.py
无网络、无模型：假 VAD（按块 RMS 判语音）+ 合成信号。数据来源见 `docs/HANDOFF-20261012-3.9.4.md`。
"""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import vad as vad_mod

SR = vad_mod.SR


def _tone(sec, amp=0.3):
    t = np.arange(int(round(sec * SR))) / SR
    return (amp * np.sin(2 * np.pi * 440 * t)).astype(np.float32)


def _with_dips(total, dips, width=0.2):
    """整段音，在 `dips`（秒）处各挖一个 `width` 秒的近静音 —— 比静音阈值短，不会触发正常断句。"""
    y = _tone(total)
    for d in dips:
        a, b = int(d * SR), int((d + width) * SR)
        y[a:b] *= 0.003
    return y


class _RmsVad:
    drift_prone = False
    last_ratio = 0.0

    def reset(self):
        pass

    def is_speech(self, chunk):
        sp = float(np.sqrt(np.mean(chunk ** 2))) > 0.05
        self.last_ratio = 1.0 if sp else 0.0
        return sp


def _segmenter(hint, env=None, partial=None):
    orig, old_env = vad_mod._make_vad, os.environ.get("CLASSLIVE_SEG_SENTENCE")
    vad_mod._make_vad = lambda *a, **k: _RmsVad()
    if env is not None:
        os.environ["CLASSLIVE_SEG_SENTENCE"] = env
    try:
        got = []
        seg = vad_mod.Segmenter(partial or (lambda b: None), got.append, sentence_ends=hint)
    finally:
        vad_mod._make_vad = orig
        if old_env is None:
            os.environ.pop("CLASSLIVE_SEG_SENTENCE", None)
        else:
            os.environ["CLASSLIVE_SEG_SENTENCE"] = old_env
    return seg, got


def _feed(seg, y):
    for i in range(0, len(y) - vad_mod.CHUNK + 1, vad_mod.CHUNK):
        seg.accept(y[i:i + vad_mod.CHUNK])


class FindSentenceCut(unittest.TestCase):
    def test_cuts_in_the_pause_before_a_late_timestamp(self):
        # 停顿在 5.0–5.2 s，句号时间戳晚到 5.7 s（真课里停顿中心中位在时间戳前 0.50 s）
        y = _with_dips(12.0, [5.0])
        cut = vad_mod.find_sentence_cut(y, (5.7,))
        self.assertIsNotNone(cut)
        self.assertTrue(5.0 <= cut / SR <= 5.2, cut / SR)

    def test_window_looks_back_not_forward(self):
        # 往后开的窗口（旧版 [t-0.3, t+0.4]）够不到 0.65 s 之前的停顿 —— 那版切进了下一句的头一个词
        y = _with_dips(12.0, [5.0])
        self.assertIsNotNone(vad_mod.find_sentence_cut(y, (5.75,)))
        self.assertIsNone(vad_mod.find_sentence_cut(y, (4.75,)), "停顿在时间戳之后 → 那是下一句里的停顿，不是这个句号的")

    def test_takes_the_last_qualifying_end(self):
        y = _with_dips(12.0, [3.0, 7.0])
        cut = vad_mod.find_sentence_cut(y, (3.5, 7.5))
        self.assertTrue(7.0 <= cut / SR <= 7.2, cut / SR)

    def test_rejects_ends_too_close_to_either_edge(self):
        y = _with_dips(12.0, [1.5, 11.2])
        self.assertIsNone(vad_mod.find_sentence_cut(y, (2.0,)), "头段太短")
        self.assertIsNone(vad_mod.find_sentence_cut(y, (11.6,)), "离段尾太近 = 等于没挪")

    def test_no_real_pause_no_cut(self):
        self.assertIsNone(vad_mod.find_sentence_cut(_tone(12.0), (6.0,)))

    def test_empty_or_garbage_is_none(self):
        y = _with_dips(12.0, [5.0])
        self.assertIsNone(vad_mod.find_sentence_cut(y, ()))
        self.assertIsNone(vad_mod.find_sentence_cut(y, None))
        self.assertIsNone(vad_mod.find_sentence_cut(y, ("x",)))


class SegmenterWiring(unittest.TestCase):
    Y = _with_dips(14.0, [5.0])           # 12 s 内没有够长的静音 → 必走硬切

    def test_hard_cut_moves_to_the_sentence_end(self):
        seg, got = _segmenter(lambda: (5.7,))
        _feed(seg, self.Y)
        self.assertTrue(5.0 <= len(got[0]) / SR <= 5.3, len(got[0]) / SR)
        self.assertEqual((seg.diag["hard_cuts"], seg.diag["sentence_cuts"]), (1, 1))
        seg.flush()
        self.assertEqual(sum(len(g) for g in got), len(self.Y) // vad_mod.CHUNK * vad_mod.CHUNK,
                         "挪回去的那截要接到下一段，一个样本都不能丢")

    def test_no_hint_or_empty_hint_keeps_old_cut(self):
        for hint in (None, lambda: ()):
            seg, got = _segmenter(hint)
            _feed(seg, self.Y)
            self.assertGreaterEqual(len(got[0]) / SR, 10.5, "没提示时应当还是在 12 s 附近切")
            self.assertEqual(seg.diag["sentence_cuts"], 0)

    def test_kill_switch(self):
        seg, got = _segmenter(lambda: (5.7,), env="0")
        _feed(seg, self.Y)
        self.assertGreaterEqual(len(got[0]) / SR, 10.5)

    def test_hint_that_raises_falls_back(self):
        def boom():
            raise RuntimeError("draft engine gone")
        seg, got = _segmenter(boom)
        _feed(seg, self.Y)
        self.assertGreaterEqual(len(got[0]) / SR, 10.5)

    def test_carried_audio_gets_a_draft_right_after_the_end(self):
        log = []
        seg, got = _segmenter(lambda: (5.7,), partial=lambda b: log.append(("partial", len(b))))
        seg.on_utterance_end = lambda b: log.append(("end", len(b)))
        _feed(seg, self.Y[: int(12.05 * SR)])
        i = next(k for k, e in enumerate(log) if e[0] == "end")
        self.assertEqual(log[i + 1][0], "partial", "新段那截要在段尾之后立刻出一份草稿")
        self.assertAlmostEqual(log[i + 1][1] / SR, 12.0 - log[i][1] / SR, delta=0.11)

    def test_report_mentions_sentence_cuts(self):
        seg, _ = _segmenter(lambda: (5.7,))
        _feed(seg, self.Y)
        self.assertIn("其中 1 次挪到了句末", seg.report())


if __name__ == "__main__":
    unittest.main(verbosity=1)
