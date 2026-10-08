"""system_tap.py 里**不需要系统授权**的部分：重采样、分块、静音看门狗、接口形状。

⚠️ 盖不住的（明说）：`ProcessTapBackend` 与 `_abl_to_array` 没有真机验证 ——
   它们碰 CoreAudio，创建 tap 会弹授权框。上机先跑 `scripts/probe_system_tap.py`。
"""
import ctypes, os, random, sys, unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import system_tap as T


def tone(freq, sr, secs, amp=0.5):
    t = np.arange(int(sr * secs)) / sr
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def rms(x):
    return float(np.sqrt(np.mean(np.square(x.astype(np.float64)))))


class Resampler(unittest.TestCase):
    def test_passband_tone_keeps_amplitude(self):
        for src in (48000, 44100):
            y = T.StreamResampler(src, 16000).process(tone(1000, src, 1.0))
            core = y[2000:-2000]                       # 去掉两端暂态
            self.assertAlmostEqual(rms(core), 0.5 / np.sqrt(2), delta=0.01, msg=f"src={src}")

    def test_output_length_matches_ratio(self):
        y = T.StreamResampler(48000, 16000).process(tone(440, 48000, 1.0))
        self.assertAlmostEqual(len(y), 16000, delta=T.StreamResampler(48000, 16000).half // 3 + 2)

    def test_stopband_is_attenuated(self):
        # 12kHz 超过目标奈奎斯特(8k)：不滤波的话会混叠成 4kHz，幅度原样。要求衰减 > 40dB
        y = T.StreamResampler(48000, 16000).process(tone(12000, 48000, 1.0, amp=0.5))
        self.assertLess(rms(y[2000:-2000]), 0.5 / np.sqrt(2) / 100)

    def test_chunking_does_not_change_output(self):
        rng = random.Random(5)
        x = (np.random.default_rng(1).standard_normal(48000).astype(np.float32)) * 0.1
        whole = T.StreamResampler(48000, 16000).process(x)
        r = T.StreamResampler(48000, 16000)
        parts, i = [], 0
        while i < len(x):
            n = rng.randint(1, 3000)
            parts.append(r.process(x[i:i + n]))
            i += n
        got = np.concatenate(parts)
        m = min(len(got), len(whole))
        self.assertGreater(m, 15000)
        np.testing.assert_allclose(got[:m], whole[:m], atol=1e-5)

    def test_upsample_works_too(self):
        y = T.StreamResampler(8000, 16000).process(tone(500, 8000, 1.0))
        self.assertAlmostEqual(rms(y[2000:-2000]), 0.5 / np.sqrt(2), delta=0.02)

    def test_rejects_bad_rate(self):
        with self.assertRaises(ValueError):
            T.StreamResampler(0, 16000)


class FakeBackend:
    """把预先给好的块一次推完；记录是否被 stop。"""

    def __init__(self, blocks, rate):
        self.blocks, self.rate, self.stopped = blocks, rate, False

    def start(self, on_audio):
        for b in self.blocks:
            on_audio(b, self.rate)

    def stop(self):
        self.stopped = True


def drain(src):
    out = []
    while True:
        c = src.poll()
        if c is None:
            return out
        out.append(c)


class Source(unittest.TestCase):
    def test_chunks_are_exactly_1600_and_rate_converted(self):
        blocks = [tone(1000, 48000, 0.1) for _ in range(30)]          # 3 秒
        s = T.SystemTapSource(FakeBackend(blocks, 48000))
        chunks = drain(s)
        self.assertTrue(all(len(c) == T.CHUNK and c.dtype == np.float32 for c in chunks))
        self.assertAlmostEqual(len(chunks), 30, delta=3)             # 3s / 0.1s，扣掉滤波器延迟
        self.assertAlmostEqual(rms(np.concatenate(chunks[5:-2])), 0.5 / np.sqrt(2), delta=0.02)

    def test_interface_shape_matches_callbacksource(self):
        s = T.SystemTapSource(FakeBackend([], 48000))
        self.assertIsNone(s.poll())
        self.assertFalse(s.is_done())
        self.assertIsInstance(s.device_name, str)
        s.close()

    def test_close_stops_backend_and_ignores_late_audio(self):
        b = FakeBackend([], 48000)
        s = T.SystemTapSource(b)
        s.close()
        self.assertTrue(b.stopped)
        s._feed(tone(1000, 48000, 0.5), 48000)
        self.assertIsNone(s.poll())

    def test_queue_drops_oldest_not_newest(self):
        # 灌 25 秒（>20 秒队列容量）：最后一块必须在，最早的要被丢
        n = 250
        blocks = [np.full(4800, i / n, dtype=np.float32) for i in range(n)]
        s = T.SystemTapSource(FakeBackend(blocks, 48000))
        chunks = drain(s)
        self.assertEqual(len(chunks), T.QUEUE_MAX)
        self.assertGreater(float(chunks[-1].mean()), 0.9)             # 新的在
        self.assertGreater(float(chunks[0].mean()), 0.15)             # 最旧的 ~15% 已被丢

    def test_rate_change_midstream_rebuilds_resampler(self):
        s = T.SystemTapSource(FakeBackend([], 48000))
        s._feed(tone(1000, 48000, 1.0), 48000)
        n1 = len(drain(s))
        s._feed(tone(1000, 44100, 1.0), 44100)
        n2 = len(drain(s))
        self.assertGreater(n1, 5)
        self.assertGreater(n2, 5)


class Watch(unittest.TestCase):
    def test_all_zero_warns_after_threshold(self):
        w = T.SilenceWatch(warn_s=2.0)
        z = np.zeros(4800, dtype=np.float32)
        for _ in range(15):                                          # 1.5 秒
            w.feed(z, 48000)
        self.assertIsNone(w.message())
        for _ in range(10):
            w.feed(z, 48000)
        self.assertIn("全是零", w.message())

    def test_once_heard_never_blames_permission(self):
        w = T.SilenceWatch(warn_s=1.0)
        w.feed(tone(440, 48000, 0.1), 48000)
        for _ in range(100):
            w.feed(np.zeros(4800, dtype=np.float32), 48000)
        self.assertIsNone(w.message())

    def test_tiny_noise_counts_as_heard(self):
        w = T.SilenceWatch(warn_s=0.5)
        x = np.zeros(4800, dtype=np.float32)
        x[10] = 1e-4
        w.feed(x, 48000)
        self.assertTrue(w.ever_heard)

    def test_source_health_exposes_watch(self):
        s = T.SystemTapSource(FakeBackend([np.zeros(4800, dtype=np.float32)] * 100, 48000))   # 10 秒全零
        self.assertIsNotNone(s.health())


class AblDecode(unittest.TestCase):
    """_abl_to_array 的形状：PyObjC 具体给什么类型要真机才知道，这里只钉两种已知可能。"""

    class _Buf:
        def __init__(self, arr, ch=1, as_int=False):
            self._arr = arr
            addr = arr.ctypes.data
            self.mData = addr if as_int else type("P", (), {"pointerAsInteger": addr})()
            self.mDataByteSize = arr.nbytes
            self.mNumberChannels = ch

    class _Abl:
        def __init__(self, buf):
            self.mNumberBuffers = 1
            self.mBuffers = [buf]

    def test_pointer_object_and_int_both_decode(self):
        a = np.arange(480, dtype=np.float32) / 480
        for as_int in (False, True):
            got = T._abl_to_array(self._Abl(self._Buf(a, as_int=as_int)))
            np.testing.assert_array_equal(got, a)

    def test_stereo_is_averaged(self):
        a = np.stack([np.ones(100), np.zeros(100)], axis=1).astype(np.float32).reshape(-1)
        got = T._abl_to_array(self._Abl(self._Buf(a, ch=2)))
        np.testing.assert_allclose(got, 0.5)

    def test_copy_survives_source_buffer_reuse(self):
        a = np.ones(100, dtype=np.float32)
        got = T._abl_to_array(self._Abl(self._Buf(a)))
        a[:] = 7
        self.assertEqual(float(got[0]), 1.0)

    def test_garbage_returns_none(self):
        self.assertIsNone(T._abl_to_array(object()))
        self.assertIsNone(T._abl_to_array(self._Abl(self._Buf(np.zeros(0, dtype=np.float32)))))


class Available(unittest.TestCase):
    def test_returns_pair(self):
        ok, why = T.available()
        self.assertIsInstance(ok, bool)
        self.assertIsInstance(why, str)


if __name__ == "__main__":
    unittest.main()
