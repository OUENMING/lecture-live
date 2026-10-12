#!/usr/bin/env python3
"""`asr.LazyASR` —— Parakeet 用到才加载（草稿走 Nemotron 时省约 1.1 GB）。

    ClassLive.app/Contents/MacOS/python tests/test_lazy_asr.py

全离线：假模型目录（空文件）+ 假 loader，不加载真模型。只写临时目录。
"""
from __future__ import annotations

import ast
import os
import pathlib
import sys
import tempfile
import threading
import time
import unittest

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import asr                                                            # noqa: E402


def _fake_dir(tmp: str, names=("encoder.int8.onnx", "decoder.int8.onnx",
                                "joiner.int8.onnx", "tokens.txt")) -> str:
    for n in names:
        pathlib.Path(tmp, n).write_bytes(b"x")
    return tmp


class Fake:
    def __init__(self, d):
        time.sleep(0.05)                 # 拉宽并发窗口：两个线程同时首用时只许建一份
        self.d = d

    def transcribe_lp(self, s):
        return "hi", -0.1

    def transcribe(self, s):
        return "hi"


class TestLazy(unittest.TestCase):
    def test_missing_files_raise_at_construction(self):
        with tempfile.TemporaryDirectory() as t:
            made = []
            with self.assertRaises(FileNotFoundError):
                asr.LazyASR(t, loader=lambda d: made.append(d))
            self.assertEqual(made, [], "缺文件时不该去加载")

    def test_mixed_precision_raises_at_construction(self):
        with tempfile.TemporaryDirectory() as t:
            _fake_dir(t, ("encoder.int8.onnx", "decoder.onnx", "joiner.int8.onnx", "tokens.txt"))
            with self.assertRaises(FileNotFoundError):
                asr.LazyASR(t, loader=Fake)

    def test_not_loaded_until_first_use(self):
        with tempfile.TemporaryDirectory() as t:
            made = []
            a = asr.LazyASR(_fake_dir(t), loader=lambda d: (made.append(d), Fake(d))[1])
            self.assertFalse(a.loaded)
            self.assertEqual(made, [])
            self.assertEqual(a.transcribe_lp([0.0] * 2000), ("hi", -0.1))
            self.assertEqual(a.transcribe([0.0] * 2000), "hi")
            self.assertTrue(a.loaded)
            self.assertEqual(made, [t], "只加载一次")

    def test_concurrent_first_use_loads_once(self):
        with tempfile.TemporaryDirectory() as t:
            made = []
            a = asr.LazyASR(_fake_dir(t), loader=lambda d: (made.append(d), Fake(d))[1])
            ths = [threading.Thread(target=a.transcribe, args=([0.0] * 2000,)) for _ in range(8)]
            for th in ths:
                th.start()
            for th in ths:
                th.join()
            self.assertEqual(len(made), 1)

    def test_load_failure_propagates_and_retries(self):
        with tempfile.TemporaryDirectory() as t:
            n = {"k": 0}

            def boom(d):
                n["k"] += 1
                raise RuntimeError("bad onnx")
            a = asr.LazyASR(_fake_dir(t), loader=boom)
            for _ in range(2):
                with self.assertRaises(RuntimeError):
                    a.transcribe_lp([0.0] * 2000)
            self.assertFalse(a.loaded)
            self.assertEqual(n["k"], 2, "失败不缓存：下一次再试")


class TestMainWiring(unittest.TestCase):
    """main 里：先 lazy 核对文件；草稿退回 Parakeet 时立刻加载（失败在开课前说清楚）。"""

    def setUp(self):
        self.src = (HERE / "main.py").read_text(encoding="utf-8")
        self.tree = ast.parse(self.src)

    def test_load_asr_is_lazy(self):
        calls = [n for n in ast.walk(self.tree) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Name) and n.func.id == "load_asr"]
        self.assertEqual(len(calls), 1)
        kw = {k.arg: k.value for k in calls[0].keywords}
        self.assertTrue(isinstance(kw.get("lazy"), ast.Constant) and kw["lazy"].value is True)

    def test_eager_load_when_draft_is_parakeet(self):
        ifs = [n for n in ast.walk(self.tree) if isinstance(n, ast.If)
               and ast.unparse(n.test) == "draft.name == 'parakeet'"]
        self.assertEqual(len(ifs), 1, "得有且只有一处「草稿是 Parakeet → 立刻加载」")
        body = ast.unparse(ast.Module(body=ifs[0].body, type_ignores=[]))
        self.assertIn("asr.get()", body)
        self.assertIn("return", body, "加载失败要停下（与原来启动时缺模型同一行为）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
