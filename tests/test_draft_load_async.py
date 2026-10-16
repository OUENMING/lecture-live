#!/usr/bin/env python3
"""草稿模型**后台加载**（`draft_engine.load_draft_engine_async`，3.9.7 第 5c 项）的判据。

    ClassLive.app/Contents/MacOS/python tests/test_draft_load_async.py

## 钉住了什么

1. ⭐⭐ **不阻塞启动**：`load_draft_engine_async` 在后台那个构造（真机上约 1.28 s）
   还没回来时就返回了 —— 悬浮窗 / 采音 / 定稿不等它。
2. ⭐ **就绪前不出草稿、也不偷偷用 Parakeet**：`transcribe` / `finish` 返回空，
   `sentence_ends` 返回 `()`，`name` 先照选中的报 `nemotron`。
   定稿那一路**根本不碰这个引擎**，所以"等模型"只影响草稿字样。
3. ⭐ **就绪后草稿开始工作**：`transcribe` 从后台建好的那个引擎出字。
4. ⭐ **加载失败退 Parakeet**（不抛、不带水下任何人）：`name` 改口、`fallback_reason`
   有话说、`transcribe` 从 Parakeet 出，原样是既有兜底那条路。
5. `on_ready` 恰好一次（成功 True / 失败 False），坏回调不带水加载线程。
6. `main.py` 真的走的是异步那条（源码扫；同步版留给离线量具）。

⚠️ 全部用**假识别器 / 可控的闸门线程**，不加载真模型、不联网、不写文件。
⚠️ 线程是**手动放行**的（`Gate`），不靠 `sleep` 猜时序 —— 这样"就绪前"那几条
   断言是确定的，不是"跑得快所以碰巧还没好"。
"""
from __future__ import annotations

import os
import pathlib
import shutil
import sys
import tempfile
import threading
import time
import unittest

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

_TMP_HOME = tempfile.mkdtemp(prefix="draft-async-test-")
os.environ["HOME"] = _TMP_HOME

import numpy as np                                                    # noqa: E402

import draft_engine as de                                             # noqa: E402

GB = 2**30
WIN = 10 * 16000


def tearDownModule():
    shutil.rmtree(_TMP_HOME, ignore_errors=True)


class FakeDrafter:
    """后台"建好的"流式引擎。就绪后 `DraftEngine` 会用它出字。"""

    TEXT = "nemotron words"

    def __init__(self):
        self.updates = 0
        self.finishes = 0
        self.ends: tuple = (None, ())

    def update(self, buf, tag):
        self.updates += 1
        return self.TEXT

    def finish(self, buf, tag):
        self.finishes += 1
        return self.TEXT


class FakeParakeet:
    def __init__(self):
        self.seen: list[int] = []

    def transcribe(self, samples):
        self.seen.append(len(samples))
        return f"p{len(samples)}"


class Gate:
    """让"后台构造"停在闸门后面 —— 就绪前那几条断言才有确定的窗口。"""

    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.built = 0

    def make(self, result=None, error=None):
        def _make(_dir):
            self.built += 1
            self.started.set()
            if not self.release.wait(5):
                raise AssertionError("测试没放行闸门")
            if error is not None:
                raise error
            return result
        return _make


def buf(n: int) -> np.ndarray:
    return np.arange(n, dtype=np.float32)


class _Base(unittest.TestCase):
    def setUp(self):
        self.model = tempfile.mkdtemp(dir=_TMP_HOME)
        pathlib.Path(self.model, "tokens.txt").write_text("x")
        self.env = {de.ENV_MODEL_DIR: self.model}
        self.ready_calls: list[bool] = []

    def load(self, gate, on_ready=True, **kw):
        return de.load_draft_engine_async(
            FakeParakeet(), window_samples=WIN, env=self.env, ram_bytes=lambda: 16 * GB,
            make_drafter=gate.make(**kw),
            on_ready=(self.ready_calls.append if on_ready else None))

    def drain(self, eng):
        """等后台那一段真跑完（`ready` = 已尘埃落定）。"""
        self.assertTrue(eng._pending.thread.join(3) is None, "后台加载线程没结束")
        self.assertTrue(eng.ready)


class NotBlocking(_Base):
    def test_构造没回来就已经返回(self):
        g = Gate()
        t0 = time.monotonic()
        eng = self.load(g, result=FakeDrafter())
        dt = time.monotonic() - t0
        self.assertLess(dt, 0.2, f"load_draft_engine_async 等了 {dt:.2f}s（该立刻返回）")
        self.assertTrue(g.started.wait(2), "后台根本没开始建")
        self.assertFalse(eng.ready)
        self.assertFalse(g.release.is_set())

    def test_就绪前不出草稿也不用Parakeet(self):
        g = Gate()
        pk = FakeParakeet()
        eng = de.load_draft_engine_async(pk, window_samples=WIN, env=self.env,
                                         ram_bytes=lambda: 16 * GB,
                                         make_drafter=g.make(result=FakeDrafter()))
        self.assertTrue(g.started.wait(2))
        self.assertEqual(eng.name, "nemotron", "还没失败就先改口了")
        self.assertEqual(eng.transcribe(buf(1600), 1), ("", "nemotron"))
        self.assertEqual(eng.finish(buf(1600), 1), "")
        self.assertEqual(eng.sentence_ends(1), ())
        self.assertEqual(pk.seen, [], "就绪前偷偷用了 Parakeet 出草稿（行为变了）")
        g.release.set()
        self.drain(eng)
        self.assertEqual(pk.seen, [], "就绪之后更不该走 Parakeet")

    def test_就绪后草稿开始工作(self):
        g = Gate()
        eng = self.load(g, result=FakeDrafter())
        self.assertTrue(g.started.wait(2))
        self.assertEqual(eng.transcribe(buf(1600), 1), ("", "nemotron"))   # 就绪前：空
        g.release.set()
        self.drain(eng)
        self.assertEqual(eng.name, "nemotron")
        self.assertEqual(eng.transcribe(buf(1600), 1), (FakeDrafter.TEXT, "nemotron"))
        self.assertEqual(eng.transcribe(buf(3200), 1), (FakeDrafter.TEXT, "nemotron"))


class LoadFailure(_Base):
    def test_失败退Parakeet_不外抛(self):
        g = Gate()
        eng = self.load(g, error=ImportError("没有 sherpa"))
        self.assertTrue(g.started.wait(2))
        g.release.set()
        self.drain(eng)
        self.assertEqual(eng.name, "parakeet")
        text, used = eng.transcribe(buf(1600), 1)
        self.assertEqual((text, used), ("p1600", "parakeet"))
        self.assertIn("没有 sherpa", eng.fallback_reason, "降级了却不说为什么")
        self.assertEqual(eng.finish(buf(1600), 1), "")
        self.assertEqual(eng.sentence_ends(1), ())

    def test_on_ready成功一次(self):
        g = Gate()
        eng = self.load(g, result=FakeDrafter())
        self.assertTrue(g.started.wait(2))
        g.release.set()
        self.drain(eng)
        self.assertEqual(self.ready_calls, [True])

    def test_on_ready失败一次(self):
        g = Gate()
        eng = self.load(g, error=RuntimeError("坏模型"))
        self.assertTrue(g.started.wait(2))
        g.release.set()
        self.drain(eng)
        self.assertEqual(self.ready_calls, [False])

    def test_on_ready自己抛_不带水加载(self):
        def boom(_ok):
            raise RuntimeError("回调坏了")
        g = Gate()
        eng = de.load_draft_engine_async(FakeParakeet(), window_samples=WIN, env=self.env,
                                         ram_bytes=lambda: 16 * GB,
                                         make_drafter=g.make(result=FakeDrafter()), on_ready=boom)
        self.assertTrue(g.started.wait(2))
        g.release.set()
        self.drain(eng)
        self.assertEqual(eng.transcribe(buf(1600), 1), (FakeDrafter.TEXT, "nemotron"))


class PicksParakeet(_Base):
    def test_选中parakeet时没有后台那一段(self):
        made = []

        def _make(d):
            made.append(d)
            return FakeDrafter()
        eng = de.load_draft_engine_async(
            FakeParakeet(), window_samples=WIN, env={de.ENV: "parakeet"}, ram_bytes=lambda: 16 * GB,
            make_drafter=_make, on_ready=self.ready_calls.append)
        self.assertTrue(eng.ready)
        self.assertEqual(eng.name, "parakeet")
        self.assertEqual(made, [], "选 Parakeet 还去建流式模型")
        self.assertEqual(self.ready_calls, [], "没走后台 → 就绪时刻由调用方自己记")

    def test_同步版行为不变(self):
        """`load_draft_engine`（离线量具那条路）仍是**同步**建好 —— 真机 1.28 s 那道题不该改它的语义。"""
        made = []

        def _make(d):
            made.append(d)
            return FakeDrafter()
        eng = de.load_draft_engine(FakeParakeet(), window_samples=WIN, env=self.env,
                                   ram_bytes=lambda: 16 * GB, make_drafter=_make)
        self.assertTrue(eng.ready)
        self.assertEqual(eng.name, "nemotron")
        self.assertEqual(made, [self.model])
        self.assertEqual(eng.transcribe(buf(1600), 1), (FakeDrafter.TEXT, "nemotron"))


class Wiring(unittest.TestCase):
    def test_main走异步那条(self):
        src = (HERE / "main.py").read_text(encoding="utf-8")
        self.assertIn("load_draft_engine_async(", src,
                      "开课路径又回到同步加载了（悬浮窗/采音会重新等那 1.28 s）")
        self.assertIn('_tp.setdefault("draft_ready"', src,
                      "起步计时里没有「草稿模型就绪」这一项")


if __name__ == "__main__":
    unittest.main(verbosity=2)
