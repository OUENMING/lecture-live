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


class _Resp2:
    def __init__(self, content, finish):
        self._c, self._f = content, finish

    def raise_for_status(self):
        pass

    def json(self):
        return {"choices": [{"message": {"content": self._c}, "finish_reason": self._f}]}


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

    # ── F11 §0：思考模式（只给课后笔记那条链）──────────────────────────────
    def test_effort_low_enables_thinking(self):
        """`effort="low"` → 开思考 + `reasoning_effort`；思考模式下 temperature 被忽略（不发）。"""
        llm_client.chat_json("sk-x", "m", "S", "U", max_tokens=99, effort="low")
        body = self.rec.calls[0]["json"]
        self.assertEqual(body["thinking"], {"type": "enabled"})
        self.assertEqual(body["reasoning_effort"], "low")
        self.assertNotIn("temperature", body, "思考模式下 temperature 会被忽略，不该发")
        self.assertEqual(body["max_tokens"], 99)

    def test_no_effort_keeps_thinking_disabled(self):
        """不传 effort（翻译/实时摘要/分类/术语）→ 现状逐字不变。"""
        llm_client.chat_json("sk-x", "m", "S", "U", max_tokens=99, temperature=0.4)
        self.assertEqual(self.rec.calls[0]["json"], _old_payload("m", "S", "U", 99, 0.4))

    def test_writer_note_exit_uses_low_effort(self):
        """⭐ 课后笔记的**唯一出口**（`ObsidianWriter._chat` / `_chat_meta`）传 low —— 别的出口不传。"""
        import obsidian_writer
        seen = []

        def fake(key, model, system, user, *, max_tokens, **kw):
            seen.append({"key": key, "model": model, "max_tokens": max_tokens, **kw})
            return ({"ok": 1}, "stop", 8) if kw.get("return_meta") else {"ok": 1}

        orig = llm_client.chat_json
        llm_client.chat_json = fake
        try:
            me = type("W", (), {"_key": "k", "_model": "m"})()
            obsidian_writer.ObsidianWriter._chat(me, "S", "U", 100)
            obsidian_writer.ObsidianWriter._chat_meta(me, "S", "U", 100)
        finally:
            llm_client.chat_json = orig
        self.assertEqual([c.get("effort") for c in seen], ["low", "low"])
        self.assertEqual(obsidian_writer.NOTE_EFFORT, "low")

    def test_align_exit_has_no_thinking(self):
        """`align`（长列表输出）关思考 —— SOC 实测开思考会截断（F11 §0）。"""
        import obsidian_writer
        seen = []

        def fake(key, model, system, user, *, max_tokens, **kw):
            seen.append(kw.get("effort", "MISSING"))
            return {"ok": 1}

        orig = llm_client.chat_json
        llm_client.chat_json = fake
        try:
            me = type("W", (), {"_key": "k", "_model": "m"})()
            obsidian_writer.ObsidianWriter._chat_plain(me, "S", "U", 100)
        finally:
            llm_client.chat_json = orig
        self.assertEqual(seen, ["MISSING"], "align 那个出口不该带 effort")
        import inspect
        src = inspect.getsource(obsidian_writer.ObsidianWriter._review_v2)
        self.assertIn("chat=self._chat_plain", src, "align 没走关思考的出口")

    def test_thinking_truncation_falls_back_to_disabled(self):
        """⭐ 带 effort 的调用收到 `finish_reason=length` → 关思考重来一次（不丢块）。"""
        seq = [_Resp2("", "length"), _Resp2('{"ok": 2}', "stop")]
        calls = []

        def fake(url, headers=None, json=None, timeout=None):
            calls.append(json)
            return seq[len(calls) - 1]

        orig = httpx.post
        httpx.post = fake
        try:
            out = llm_client.chat_json("sk-x", "m", "S", "U", max_tokens=4096, effort="low")
        finally:
            httpx.post = orig
        self.assertEqual(out, {"ok": 2})
        self.assertEqual(len(calls), 2, "截断必须重来一次")
        self.assertEqual(calls[0]["thinking"], {"type": "enabled"})
        self.assertEqual(calls[1]["thinking"], {"type": "disabled"}, "重来那次必须关思考")
        self.assertNotIn("reasoning_effort", calls[1])

    def test_no_effort_never_falls_back(self):
        """不带 effort 时行为逐字不变：截断照样抛（一次性，不重来）。"""
        calls = []

        def fake(url, headers=None, json=None, timeout=None):
            calls.append(json)
            return _Resp2("", "length")

        orig = httpx.post
        httpx.post = fake
        try:
            with self.assertRaises(json.JSONDecodeError):
                llm_client.chat_json("sk-x", "m", "S", "U", max_tokens=10)
        finally:
            httpx.post = orig
        self.assertEqual(len(calls), 1, "不带 effort 不许重来")

    def test_passcode_goes_to_worker_with_install_id(self):
        import final_asr
        orig = final_asr.install_id
        final_asr.install_id = lambda **kw: "ab" * 16
        try:
            build_notes._chat_json("cl-ying-0123456789abcdef", "m", "s", "u", 1)
        finally:
            final_asr.install_id = orig
        c = self.rec.calls[0]
        self.assertEqual(c["url"], "https://asr.bldcam.page/v1/chat/completions")
        ver = (HERE / "VERSION").read_text(encoding="utf-8").strip()   # 独立读，不用被测函数当期望值
        self.assertEqual(c["headers"], {"Authorization": "Bearer cl-ying-0123456789abcdef",
                                        "X-ClassLive-Install": "ab" * 16,
                                        "X-ClassLive-Version": ver})

    def test_native_key_never_sends_install_id(self):
        self.assertEqual(llm_client.headers("sk-abc"), {"Authorization": "Bearer sk-abc"})
        self.assertEqual(llm_client.base_for("sk-abc"), llm_client.BASE)
        self.assertEqual(llm_client.base_for(None), llm_client.BASE)

    def test_cloud_translator_routes_by_key(self):
        import cloud_translator as ct
        self.assertEqual(ct.CloudTranslator("sk-a", "m")._base, "https://api.deepseek.com/v1")
        import final_asr
        orig = final_asr.install_id
        final_asr.install_id = lambda **kw: "cd" * 16
        try:
            t = ct.CloudTranslator("cl-a-0123456789abcdef", "m")
        finally:
            final_asr.install_id = orig
        self.assertEqual(t._base, "https://asr.bldcam.page/v1")
        self.assertEqual(t._headers()["X-ClassLive-Install"], "cd" * 16)
        self.assertEqual(ct.CloudTranslator("cl-a-0123456789abcdef", "m", base_url="http://x/v1")._base,
                         "http://x/v1", "调用方指定的地址不许被改道")

    def test_asr_token_precedence(self):
        import os

        import asr_endpoint as ae
        old = os.environ.pop("CLASSLIVE_ASR_TOKEN", None)
        try:
            self.assertEqual(ae.token(), ae.TOKEN)
            self.assertEqual(ae.token("sk-x"), ae.TOKEN, "原生 key 不许当转写令牌发出去")
            self.assertEqual(ae.token("cl-a-0123456789abcdef"), "cl-a-0123456789abcdef")
            os.environ["CLASSLIVE_ASR_TOKEN"] = "t"
            self.assertEqual(ae.token("cl-a-0123456789abcdef"), "t")
        finally:
            os.environ.pop("CLASSLIVE_ASR_TOKEN", None)
            if old is not None:
                os.environ["CLASSLIVE_ASR_TOKEN"] = old

    def test_single_source_of_truth_for_the_address(self):
        hits = [p.name for p in HERE.glob("*.py")
                if p.name != "llm_client.py" and "api.deepseek.com" in p.read_text(encoding="utf-8")]
        self.assertEqual(hits, [], "DeepSeek 地址只许在 llm_client.py 里出现")


if __name__ == "__main__":
    unittest.main(verbosity=2)
