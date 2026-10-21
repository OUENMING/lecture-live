#!/usr/bin/env python3
"""`llm_client` —— 对话模型的唯一出口（`docs/PLAN-passkey.md` §2 第 1 步：收口、行为逐字不变）。

    ClassLive.app/Contents/MacOS/python tests/test_llm_client.py

不联网：`llm_client._post_deadline` 换成记录器；墙钟用例用 `httpx.MockTransport`
（假流，受控字节节奏）。不写文件。
"""
from __future__ import annotations

import json
import pathlib
import sys
import time
import unittest

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import httpx                                                          # noqa: E402

import build_notes                                                    # noqa: E402
import llm_client                                                     # noqa: E402
import polish                                                         # noqa: E402
import asr_endpoint                                                   # noqa: E402


class _Resp:
    def __init__(self, content):
        self._c = content

    def raise_for_status(self):
        pass

    def json(self):
        return {"choices": [{"message": {"content": self._c}}]}


class Rec:
    """`llm_client._post_deadline` 的替身（F13：真正发请求的那一层）。

    记 `(url, headers, payload, timeout, deadline)`，回一段正常的 JSON 体 ——
    这样"发出去的请求体/请求头/超时"这些不变量照旧可钉，而不必碰网络。
    """

    def __init__(self, content='{"a": 1}', finish=None, status=200):
        self.calls, self.content = [], content
        self.finish, self.status = finish, status

    def __call__(self, url, *, headers=None, payload=None, timeout=None, deadline=None):
        self.calls.append({"url": url, "headers": headers, "json": payload,
                           "timeout": timeout, "deadline": deadline})
        ch = {"message": {"content": self.content}}
        if self.finish is not None:
            ch["finish_reason"] = self.finish
        return self.status, json.dumps({"choices": [ch]}).encode()


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


class Deadline(unittest.TestCase):
    """⭐ F13：**每次调用的硬墙钟上限**。

    现场（2026-10-18 实测）：`httpx` 的 `timeout=180` 是**每次读**的超时；上游（DeepSeek
    经 Worker 代理）思考时持续滴 **keep-alive 空行**，每隔几秒来几个字节 → read 超时永远
    不触发，一次调用把整条笔记链挂死（18 分钟 0% CPU、socket ESTABLISHED）。
    """

    def _patch_stream(self, handler):
        """把 `httpx.stream` 换成"带 MockTransport 的 Client.stream"（不联网、可控制字节节奏）。"""
        import contextlib

        def fake(method, url, **kw):
            client = httpx.Client(transport=httpx.MockTransport(handler))

            @contextlib.contextmanager
            def _cm():
                try:
                    with client.stream(method, url, **kw) as r:
                        yield r
                finally:
                    client.close()
            return _cm()
        orig = httpx.stream
        httpx.stream = fake
        return lambda: setattr(httpx, "stream", orig)

    def test_上游滴字节也要按总时长叫停(self):
        """滴 1 字节 / 20ms 的响应 → 必须在 deadline 附近抛超时，并把流提前关掉。"""
        produced = {"n": 0}
        total = 300

        def trickle():
            for _ in range(total):
                time.sleep(0.02)
                produced["n"] += 1
                yield b" "

        restore = self._patch_stream(lambda request: httpx.Response(200, content=trickle()))
        t0 = time.monotonic()
        try:
            with self.assertRaises(httpx.TimeoutException) as cm:
                llm_client.chat_json("sk-x", "m", "s", "u", max_tokens=1,
                                     timeout=30, deadline=0.25)
        finally:
            restore()
        spent = time.monotonic() - t0
        self.assertLess(spent, 3.0, f"没在 deadline 附近停（{spent:.1f}s）")
        self.assertLess(produced["n"], total, "流没被提前关掉（with 没生效）")
        self.assertIn("总时长", str(cm.exception))

    def test_正常路径照旧(self):
        """一次正常返回：照样拿到 JSON，`deadline` 默认是 `CALL_DEADLINE_S`。"""
        body = json.dumps({"choices": [{"message": {"content": '{"ok": 3}'}}]}).encode()
        restore = self._patch_stream(lambda request: httpx.Response(200, content=body))
        try:
            self.assertEqual(llm_client.chat_json("sk-x", "m", "s", "u", max_tokens=1),
                             {"ok": 3})
        finally:
            restore()

    def test_HTTP_错误照旧抛(self):
        """4xx/5xx 仍然抛 `HTTPStatusError`（带 status 与 body）。"""
        restore = self._patch_stream(lambda request: httpx.Response(500, content=b"upstream boom"))
        try:
            with self.assertRaises(httpx.HTTPStatusError) as cm:
                llm_client.chat_json("sk-x", "m", "s", "u", max_tokens=1)
        finally:
            restore()
        self.assertEqual(cm.exception.response.status_code, 500)

    def test_默认墙钟写在常量里(self):
        self.assertEqual(llm_client.CALL_DEADLINE_S, 300.0)
        self.rec = Rec()
        orig = llm_client._post_deadline
        llm_client._post_deadline = self.rec
        try:
            llm_client.chat_json("sk-x", "m", "s", "u", max_tokens=1)
        finally:
            llm_client._post_deadline = orig
        self.assertEqual(self.rec.calls[0]["deadline"], llm_client.CALL_DEADLINE_S)


class T(unittest.TestCase):
    def setUp(self):
        self.rec = Rec()
        self._orig = llm_client._post_deadline
        llm_client._post_deadline = self.rec

    def tearDown(self):
        llm_client._post_deadline = self._orig

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
        seq = [("", "length"), ('{"ok": 2}', "stop")]
        calls = []

        def fake(url, *, headers=None, payload=None, timeout=None, deadline=None):
            calls.append(payload)
            content, fin = seq[len(calls) - 1]
            return 200, json.dumps({"choices": [
                {"message": {"content": content}, "finish_reason": fin}]}).encode()

        orig = llm_client._post_deadline
        llm_client._post_deadline = fake
        try:
            out = llm_client.chat_json("sk-x", "m", "S", "U", max_tokens=4096, effort="low")
        finally:
            llm_client._post_deadline = orig
        self.assertEqual(out, {"ok": 2})
        self.assertEqual(len(calls), 2, "截断必须重来一次")
        self.assertEqual(calls[0]["thinking"], {"type": "enabled"})
        self.assertEqual(calls[1]["thinking"], {"type": "disabled"}, "重来那次必须关思考")
        self.assertNotIn("reasoning_effort", calls[1])

    def test_no_effort_never_falls_back(self):
        """不带 effort 时行为逐字不变：截断照样抛（一次性，不重来）。"""
        calls = []

        def fake(url, *, headers=None, payload=None, timeout=None, deadline=None):
            calls.append(payload)
            return 200, json.dumps({"choices": [
                {"message": {"content": ""}, "finish_reason": "length"}]}).encode()

        orig = llm_client._post_deadline
        llm_client._post_deadline = fake
        try:
            with self.assertRaises(json.JSONDecodeError):
                llm_client.chat_json("sk-x", "m", "S", "U", max_tokens=10)
        finally:
            llm_client._post_deadline = orig
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
        want = {"Authorization": "Bearer cl-ying-0123456789abcdef",
                "X-ClassLive-Install": "ab" * 16,
                "X-ClassLive-Version": ver}
        tz = asr_endpoint.tz_header()                                   # 时区头（3.10.3 起）：拿不到就不发
        if tz:
            want["X-ClassLive-Tz"] = tz
        self.assertEqual(c["headers"], want)

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

    def test_docstring记的是真实的上限(self):
        """⭐ F11-9：不许再写死"压到 4096"（Worker 现为 8192，commit 0335853）。"""
        src = (HERE / "llm_client.py").read_text(encoding="utf-8")
        self.assertNotIn("压到 4096", src)
        self.assertNotIn("4096 时 content", src)
        self.assertIn("8192", src, "该记下真实的 Worker 上限 8192")


if __name__ == "__main__":
    unittest.main(verbosity=2)
