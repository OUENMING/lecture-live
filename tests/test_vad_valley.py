#!/usr/bin/env python3
"""硬切点往回找能量低谷（PLAN-r1-translation-gap §I，实验见 RESEARCH-cloud-asr §14）。

跑法: ClassLive.app/Contents/MacOS/python tests/test_vad_valley.py
无网络、无模型：用假 VAD（按块 RMS 判语音）。
"""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import vad as vad_mod

SR = vad_mod.SR


def _tone(sec, amp=0.3):
    t = np.arange(int(sec * SR)) / SR
    return (amp * np.sin(2 * np.pi * 440 * t)).astype(np.float32)


def _dip_signal():
    """10.9s 音 + 0.2s 近静音(10.9–11.1s) + 2.9s 音 = 14s。
    近静音只有 0.2s < 静音阈值，所以不会触发正常断句，12s 处必走硬切。"""
    return np.concatenate([_tone(10.9), _tone(0.2, 0.001), _tone(2.9)])


class _RmsVad:
    """块 RMS > 0.05 即语音；近静音(0.001)判非语音。"""
    drift_prone = False
    last_ratio = 0.0

    def reset(self):
        pass

    def is_speech(self, chunk):
        sp = float(np.sqrt(np.mean(chunk ** 2))) > 0.05
        self.last_ratio = 1.0 if sp else 0.0
        return sp


def _segmenter(valley=True, partial=None):
    orig = vad_mod._make_vad
    vad_mod._make_vad = lambda *a, **k: _RmsVad()
    try:
        got = []
        seg = vad_mod.Segmenter(partial or (lambda b: None), got.append)
    finally:
        vad_mod._make_vad = orig
    seg.valley = valley
    return seg, got


def _feed(seg, y):
    for i in range(0, len(y) - vad_mod.CHUNK + 1, vad_mod.CHUNK):
        seg.accept(y[i:i + vad_mod.CHUNK])


class FindValleyCut(unittest.TestCase):
    def test_cut_lands_in_quiet_gap(self):
        buf = _dip_signal()[:12 * SR]
        cut = vad_mod.find_valley_cut(buf)
        self.assertTrue(int(10.9 * SR) <= cut <= int(11.1 * SR), cut / SR)

    def test_flat_signal_returns_len(self):
        buf = _tone(12.0)
        self.assertEqual(vad_mod.find_valley_cut(buf), len(buf))

    def test_short_buffer_returns_len(self):
        buf = _tone(1.0)
        self.assertEqual(vad_mod.find_valley_cut(buf), len(buf))


class SegmenterValley(unittest.TestCase):
    def test_no_samples_lost_or_duplicated(self):
        y = _dip_signal()
        seg, got = _segmenter(True)
        _feed(seg, y)
        seg.flush()
        self.assertEqual(len(got), 2)
        head, tail = got
        self.assertTrue(int(10.9 * SR) <= len(head) <= int(11.1 * SR), len(head) / SR)
        self.assertTrue(np.array_equal(np.concatenate(got), y), "样本丢了或重复了")
        # 下一段以留下的 rest 开头
        self.assertTrue(np.array_equal(tail[:1000], y[len(head):len(head) + 1000]))
        self.assertEqual(seg.diag["hard_cuts"], 1)
        self.assertAlmostEqual(seg.diag["valley_back_s"], 12.0 - len(head) / SR, places=6)

    def test_valley_off_is_old_behaviour(self):
        y = _dip_signal()
        seg, got = _segmenter(False)
        _feed(seg, y)
        seg.flush()
        self.assertEqual(len(got[0]), 12 * SR, "关掉开关必须恰好在 12s 硬切")
        self.assertTrue(np.array_equal(np.concatenate(got), y))
        self.assertEqual(seg.diag["valley_back_s"], 0.0)

    def test_partials_continue_after_valley_cut(self):
        y = _dip_signal()
        sizes = []
        seg, got = _segmenter(True, partial=lambda b: sizes.append(len(b)))
        _feed(seg, y[:12 * SR])
        self.assertEqual(len(got), 1)
        sizes.clear()                                      # 只看切点之后的草稿
        orig = vad_mod.time.monotonic
        vad_mod.time.monotonic = lambda: orig() + 100.0   # 越过 PARTIAL_INTERVAL_S
        try:
            seg.accept(y[12 * SR:12 * SR + vad_mod.CHUNK])
        finally:
            vad_mod.time.monotonic = orig
        self.assertEqual(len(sizes), 1)
        self.assertGreater(sizes[0], vad_mod.CHUNK, "草稿应含留下的 rest")

    def test_flush_right_after_cut_no_false_drop(self):
        # 切点就是缓冲末尾（无低谷）时 rest 为空，flush 不得误记 dropped
        seg, got = _segmenter(True)
        _feed(seg, _tone(12.0))
        seg.flush()
        self.assertEqual(len(got[0]), 12 * SR)
        self.assertEqual(seg.diag["dropped"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
