#!/usr/bin/env python3
"""`llm_client` —— 对话模型的唯一出口（`docs/PLAN-passkey.md` §2 第 1 步：收口、行为逐字不变）。

    ClassLive.app/Contents/MacOS/python tests/test_llm_client.py

不联网：`httpx.post` 换成记录器。不写文件。
"""
from __future__ import annotations

import json
import pathlib
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import httpx                                                          # noqa: E402

import build_notes                                                    # noqa: E402
import llm_client                                                     # noqa: E402
import polish                                                         # noqa: E402


class _Resp:
    def __init__(self, content):
        self._c = content

    def raise_for_status(self):
        pass

    def json(self):
        return {"choices": [{"message": {"content": self._c}}]}


class Rec:
    def __init__(self, content='{"a": 1}'):
        self.calls, self.content = [], content

    def __call__(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return _Resp(self.content)


def _old_payload(model, system, user, max_tokens, temperature):
    """收口之前四处手写的请求体（逐字抄自 git 历史），钉「行为不变」。"""
    return {"model": model, "stream": False, "max_tokens": max_tokens,
            "temperature": temperature, "thinking": {"type": "disabled"},
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}]}


class T(unittest.TestCase):
    def setUp(self):
        self.rec = Rec()
        self._orig = httpx.post
        httpx.post = self.rec

    def tearDown(self):
        httpx.post = self._orig

    def test_build_notes_same_request(self):
        out = build_notes._chat_json("sk-x", "m", "S", "U", 99, temperature=0.4, timeout=15)
        self.assertEqual(out, {"a": 1})
        c = self.rec.calls[0]
        self.assertEqual(c["url"], "https://api.deepseek.com/v1/chat/completions")
        self.assertEqual(c["headers"], {"Authorization": "Bearer sk-x"})
        self.assertEqual(c["json"], _old_payload("m", "S", "U", 99, 0.4))
        self.assertEqual(c["timeout"], 15)

    def test_polish_same_request(self):
        polish._chat("sk-y", "m2", "S2", "U2", 7, 33)
        c = self.rec.calls[0]
        self.assertEqual(c["json"], _old_payload("m2", "S2", "U2", 7, 0.2))
        self.assertEqual(c["timeout"], 33)

    def test_non_dict_json_keeps_each_callers_contract(self):
        self.rec.content = json.dumps([1, 2])
        self.assertIsNone(build_notes._chat_json("k", "m", "s", "u", 1))
        self.assertEqual(polish._chat("k", "m", "s", "u", 1, 1), {})

    def test_bad_json_raises(self):
        self.rec.content = "not json"
        with self.assertRaises(json.JSONDecodeError):
            llm_client.chat_json("k", "m", "s", "u", max_tokens=1)

    def test_single_source_of_truth_for_the_address(self):
        hits = [p.name for p in HERE.glob("*.py")
                if p.name != "llm_client.py" and "api.deepseek.com" in p.read_text(encoding="utf-8")]
        self.assertEqual(hits, [], "DeepSeek 地址只许在 llm_client.py 里出现")


if __name__ == "__main__":
    unittest.main(verbosity=2)
