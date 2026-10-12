#!/usr/bin/env python3
"""`final_asr.py` 的判据 —— 定稿 ASR 深模块(云端优先 / 本地兜底)。

    ClassLive.app/Contents/MacOS/python tests/test_final_asr.py

## 钉住了什么(docs/PLAN-r2-cloud-final-asr.md §C4 + R3/R5/R7/R8)

 1. 云端 200 → source=groq, 本地没被调        7. allow_cloud False → 不发请求, "off", fell_back=False
 2. 服务器挂住 → 6 s 内回本地, 收尾 2 s       8. 编码失败 → 本地, "encode"
 3. 503 → 本地                                9. 没 Whisper → 草稿模型 / Whisper 退化 → 草稿模型
 4. 熔断: 3 次失败 → 不发 → 61 s 后试探 → 恢复   10. 请求头 + body 以 OggS 开头
 5. 云端退化文本 → 本地                      11. install_id 一次生成、600
 6. 云端空串: 短段接受、≥2 s 补跑草稿(R5)    12. transcribe_lp 的 logprob 与同一次调用对应
 R7. CLASSLIVE_CLOUD_ASR=0 → 不发请求、不写任何文件

⚠️ 本机 `http.server` 当 Worker, **不联网**; HOME 与 `paths.STATE_ROOT` 都指向临时目录
   (读端写端都隔离, 不碰真 `~/.classlive/`)。
"""
from __future__ import annotations

import http.server
import json
import os
import pathlib
import shutil
import stat
import sys
import tempfile
import threading
import time
import types
import unittest

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

_TMP_HOME = tempfile.mkdtemp(prefix="final-asr-test-")
os.environ["HOME"] = _TMP_HOME                    # 必须在 import paths 之前
os.environ.pop("CLASSLIVE_CLOUD_ASR", None)
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"

import numpy as np                                                    # noqa: E402

import asr                                                            # noqa: E402
import final_asr                                                      # noqa: E402
import paths                                                          # noqa: E402

STATE = pathlib.Path(_TMP_HOME) / ".classlive"
paths.STATE_ROOT = STATE                                              # 读端写端一起隔离
assert str(paths.STATE_ROOT).startswith(tempfile.gettempdir()) or "final-asr-test-" in str(paths.STATE_ROOT)

RNG = np.random.default_rng(0)


def audio(seconds: float) -> np.ndarray:
    return (RNG.standard_normal(int(seconds * 16000)) * 0.1).astype("float32")


class Stub:
    """假的本地 ASR: 记录调用, 返回预设 (text, logprob)。"""

    def __init__(self, text="local text", lp=-0.3):
        self.text, self.lp, self.calls = text, lp, 0

    def transcribe_lp(self, samples):
        self.calls += 1
        return self.text, self.lp


class Srv:
    """本机 Worker 替身: 记录请求, 状态码 / 响应 / 睡眠可配。"""

    def __init__(self, status=200, body=None, sleep=0.0):
        self.status, self.sleep = status, sleep
        self.body = body if body is not None else {"text": "cloud text", "provider": "groq"}
        self.reqs: list[dict] = []
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):                                        # noqa: N802
                n = int(self.headers.get("Content-Length") or 0)
                data = self.rfile.read(n)
                outer.reqs.append({"headers": dict(self.headers), "body": data})
                if outer.sleep:
                    time.sleep(outer.sleep)
                out = json.dumps(outer.body).encode()
                try:
                    self.send_response(outer.status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(out)))
                    self.end_headers()
                    self.wfile.write(out)
                except OSError:
                    pass

            def log_message(self, *a):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1/transcribe"

    @property
    def n(self) -> int:
        return len(self.reqs)

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class Base(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(STATE, ignore_errors=True)
        os.environ.pop("CLASSLIVE_CLOUD_ASR", None)
        final_asr._client = None
        final_asr._PROC_INSTALL_ID = None
        self.echoes: list[str] = []
        self._echo = final_asr.echo
        final_asr.echo = self.echoes.append
        self.srv = None
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        final_asr.echo = self._echo
        os.environ.pop("CLASSLIVE_ASR_ENDPOINT", None)
        if self.srv:
            self.srv.close()

    def serve(self, **kw) -> Srv:
        self.srv = Srv(**kw)
        os.environ["CLASSLIVE_ASR_ENDPOINT"] = self.srv.url
        return self.srv

    def make(self, local=None, draft=None, allow=lambda: True, **kw):
        self.draft = draft or Stub("draft text")
        self.local = local
        return final_asr.FinalASR(local, self.draft, allow, **kw)


class TestCloudPath(Base):
    def test_01_cloud_ok(self):
        srv = self.serve()
        f = self.make()
        r = f.transcribe(audio(3))
        self.assertEqual((r.text, r.source, r.fell_back), ("cloud text", "groq", False))
        self.assertEqual(r.steps, ("cloud:groq",))
        self.assertEqual(self.draft.calls, 0)
        self.assertEqual(srv.n, 1)

    def test_01b_passcode_is_the_bearer(self):
        """一人一码：填的是通行码就拿它当转写令牌（Worker 记到人名下）；原生 sk- key 绝不发出去。"""
        import asr_endpoint
        os.environ.pop("CLASSLIVE_ASR_TOKEN", None)
        srv = self.serve()
        self.make(passcode="cl-ying-0123456789abcdef").transcribe(audio(3))
        self.make(passcode="sk-secret").transcribe(audio(3))
        auths = [r["headers"].get("Authorization") for r in srv.reqs]
        self.assertEqual(auths, ["Bearer cl-ying-0123456789abcdef", f"Bearer {asr_endpoint.TOKEN}"])

    def test_02_hang_hits_total_deadline(self):
        self.serve(sleep=7.0)
        f = self.make()
        t = time.monotonic()
        r = f.transcribe(audio(3))
        dt = time.monotonic() - t
        self.assertTrue(5.5 <= dt < 6.8, dt)                # 总截止 6 s
        self.assertEqual(r.steps, ("cloud:timeout", "local:parakeet"))
        self.assertEqual((r.text, r.source, r.fell_back), ("draft text", "local-parakeet", True))

    def test_02b_stopping_uses_short_deadline(self):
        self.serve(sleep=7.0)
        f = self.make(is_stopping=lambda: True)
        t = time.monotonic()
        r = f.transcribe(audio(3))
        dt = time.monotonic() - t
        self.assertTrue(1.7 <= dt < 2.8, dt)                # 收尾 2 s
        self.assertEqual(r.steps[0], "cloud:timeout")

    def test_03_503_goes_local(self):
        self.serve(status=503, body={"error": "all_failed"})
        r = self.make().transcribe(audio(3))
        self.assertEqual(r.steps, ("cloud:http-503", "local:parakeet"))
        self.assertEqual((r.source, r.fell_back), ("local-parakeet", True))
        self.assertEqual(r.trace["fallback_text"], "draft text")

    def test_04_breaker(self):
        srv = self.serve(status=503, body={})
        clk = Clock()
        f = self.make(clock=clk)
        for _ in range(3):
            f.transcribe(audio(3))
        self.assertEqual(srv.n, 3)
        r = f.transcribe(audio(3))
        self.assertEqual(srv.n, 3, "开闸期内不该再发请求")
        self.assertEqual(r.steps[0], "breaker")
        clk.t += 61
        srv.status, srv.body = 200, {"text": "back", "provider": "qwen"}
        r = f.transcribe(audio(3))                           # 半开: 试探 1 段
        self.assertEqual((srv.n, r.text, r.source), (4, "back", "qwen"))
        f.transcribe(audio(3))
        self.assertEqual(srv.n, 5)                           # 已恢复
        self.assertEqual(self.echoes, ["⚠ 云端转写暂不可用, 改用本机识别; 60s 后自动重试",
                                       "☁ 云端转写已恢复"])

    def test_04c_breaker_goes_to_notify(self):
        # 双击 .app 的人没有终端: 熔断开 / 恢复要走 notify(main 接到菜单栏图标), 不走 echo
        srv = self.serve(status=503, body={})
        clk, said = Clock(), []
        f = self.make(clock=clk, notify=lambda m, w: said.append(w))
        for _ in range(3):
            f.transcribe(audio(3))
        clk.t += 61
        srv.status, srv.body = 200, {"text": "back", "provider": "qwen"}
        f.transcribe(audio(3))
        self.assertEqual(said, [True, False])
        self.assertEqual(self.echoes, [], "走 notify 就不该再 echo")

    def test_04d_probe_released_when_encode_fails(self):
        """⭐⭐ 半开探测的「名额」必须还回来 —— 否则云端定稿**永久静默失效**。

        ⚠️ 根因（2026-10-12 全仓审查，核实过）：`_Breaker.allow()` 放行探测时置
           `_probing = True`，而复位它的**只有** `ok()` / `fail()` —— 两个都在 `_cloud()` 里。
           可 `_run()` 在 `allow()` 之后还有一条**根本不发请求就返回**的路
           （`_encode()` 返 None → 走本地），那条路两个都不调 → `_probing` 卡在 True →
           `allow()` 此后**永远**返 False → 云端定稿再也上不去，界面上只表现为
           "今天识别质量差了点"。**一旦撞上不可自愈。**
           触发条件：熔断开过一次 + 恰好那次编码失败（缺 soundfile / 编码异常）。
        """
        srv = self.serve(status=503, body={})
        clk = Clock()
        f = self.make(clock=clk)
        for _ in range(3):
            f.transcribe(audio(3))                      # 3 次失败 -> 熔断开
        self.assertEqual(srv.n, 3)
        clk.t += 61                                     # 过开闸期

        from unittest import mock
        with mock.patch.object(f, "_encode", lambda _s: None):
            r = f.transcribe(audio(3))                  # 探测到手，但**不发请求**
        self.assertEqual(r.steps, ("encode", "local:parakeet"))
        self.assertEqual(srv.n, 3, "这条路上本来就不该发请求")

        clk.t += 61
        srv.status, srv.body = 200, {"text": "back", "provider": "qwen"}
        r = f.transcribe(audio(3))
        # 改坏：把 `transcribe()` 里那个 `finally: release_probe()` 删掉 -> 这条红。
        self.assertEqual((r.text, r.source, srv.n), ("back", "qwen", 4),
                         "⚠️ 探测名额被吞了 —— 云端从此永久失效")

    def test_04b_failed_probe_reopens_silently(self):
        srv = self.serve(status=503, body={})
        clk = Clock()
        f = self.make(clock=clk)
        for _ in range(3):
            f.transcribe(audio(3))
        clk.t += 61
        f.transcribe(audio(3))                               # 试探失败
        self.assertEqual(srv.n, 4)
        f.transcribe(audio(3))                               # 又在闸内
        self.assertEqual(srv.n, 4)
        self.assertEqual(len(self.echoes), 1)                # 状态没变 → 不刷屏

    def test_05_cloud_degenerate_goes_local(self):
        self.serve(body={"text": "... ... ...", "provider": "groq"})
        r = self.make().transcribe(audio(3))
        self.assertEqual(r.steps, ("cloud:degenerate", "local:parakeet"))
        self.assertEqual(r.text, "draft text")
        self.assertTrue(r.trace["primary_degenerate"])

    def test_06_empty_short_accepted(self):
        self.serve(body={"text": "", "provider": "groq"})
        f = self.make()
        r = f.transcribe(audio(1.0))
        self.assertEqual((r.text, r.source), ("", "groq"))
        self.assertEqual(self.draft.calls, 0)

    def test_06b_empty_long_reruns_draft(self):                      # R5
        self.serve(body={"text": "", "provider": "groq"})
        r = self.make().transcribe(audio(3.0))
        self.assertEqual((r.text, r.source), ("draft text", "local-parakeet"))
        self.assertEqual(r.steps, ("cloud:groq", "cloud-empty", "local:parakeet"))
        self.assertEqual(self.draft.calls, 1)

    def test_06c_empty_long_draft_also_empty(self):
        self.serve(body={"text": "", "provider": "qwen"})
        r = self.make(draft=Stub("")).transcribe(audio(3.0))
        self.assertEqual((r.text, r.source), ("", "qwen"))

    def test_08_encode_failure(self):
        srv = self.serve()
        r = self.make(encode=lambda s: None).transcribe(audio(3))
        self.assertEqual(r.steps, ("encode", "local:parakeet"))
        self.assertEqual((srv.n, r.fell_back), (0, True))

    def test_10_headers_and_body(self):
        srv = self.serve()
        self.make().transcribe(audio(3))
        q = srv.reqs[0]
        h = {k.lower(): v for k, v in q["headers"].items()}
        self.assertEqual(h["authorization"], f"Bearer {final_asr.asr_endpoint.token()}")
        self.assertRegex(h["x-classlive-install"], r"^[0-9a-f]{32}$")
        self.assertEqual(h["x-audio-seconds"], "3.0")
        self.assertEqual(h["content-type"], "audio/ogg")
        self.assertTrue(q["body"].startswith(b"OggS"))

    def test_never_raises(self):
        class Boom:
            def transcribe_lp(self, s):
                raise RuntimeError("x")
        r = self.make(draft=Boom(), allow=lambda: False).transcribe(audio(1))
        self.assertEqual((r.text, r.source), ("", "none"))
        self.assertTrue(r.steps[-1].startswith("error:"))


class TestLocalPath(Base):
    def test_07_allow_false(self):
        srv = self.serve()
        r = self.make(allow=lambda: False).transcribe(audio(3))
        self.assertEqual(r.steps, ("off", "local:parakeet"))
        self.assertEqual((srv.n, r.fell_back), (0, False))
        self.assertEqual(r.logprob, -0.3)

    def test_09_no_whisper_uses_draft(self):
        r = self.make(local=None, allow=lambda: False).transcribe(audio(3))
        self.assertEqual((r.source, r.text), ("local-parakeet", "draft text"))

    def test_09b_whisper_degenerate_falls_to_draft(self):
        r = self.make(local=Stub("... ... ..."), allow=lambda: False).transcribe(audio(3))
        self.assertEqual(r.steps, ("off", "local:whisper", "degenerate", "local:parakeet"))
        self.assertEqual((r.source, r.text), ("local-parakeet", "draft text"))
        self.assertEqual(r.trace["primary_text"], "... ... ...")
        self.assertTrue(r.trace["primary_degenerate"])
        self.assertEqual(r.trace["fallback_text"], "draft text")

    def test_09c_whisper_ok(self):
        r = self.make(local=Stub("whisper text", -0.1), allow=lambda: False).transcribe(audio(3))
        self.assertEqual((r.source, r.text, r.logprob), ("local-whisper", "whisper text", -0.1))
        self.assertEqual(self.draft.calls, 0)
        self.assertIsNone(r.trace["fallback_text"])

    def test_load_final_survives_missing_whisper(self):
        d = Stub()
        f = final_asr.load_final("/nonexistent/whisper", d, lambda: False, preload_local=True)
        self.assertEqual(f.transcribe(audio(1)).source, "local-parakeet")
        self.assertTrue(any("Parakeet" in e for e in self.echoes))
        f2 = final_asr.load_final("/nonexistent/whisper", d, lambda: False, preload_local=False)
        self.assertEqual(f2.transcribe(audio(1)).source, "local-parakeet")


class TestEnvSwitch(Base):
    def test_r7_env_zero_writes_nothing_sends_nothing(self):
        srv = self.serve()
        os.environ["CLASSLIVE_CLOUD_ASR"] = "0"
        r = self.make().transcribe(audio(3))
        self.assertEqual(r.steps, ("off", "local:parakeet"))
        self.assertEqual(srv.n, 0)
        self.assertFalse(final_asr.write_config(False))
        cfg = final_asr.read_config()
        self.assertEqual(cfg["enabled"], False)
        self.assertTrue(cfg["acknowledged"])                 # 不弹告知
        self.assertFalse(STATE.exists(), "env=0 不许在 ~/.classlive 下建任何东西")


class TestConfigAndId(Base):
    def test_11_install_id(self):
        a = final_asr.install_id()
        self.assertRegex(a, r"^[0-9a-f]{32}$")
        self.assertEqual(final_asr.install_id(), a)
        mode = stat.S_IMODE(os.stat(paths.install_id()).st_mode)
        self.assertEqual(mode, 0o600)

    def test_install_id_repairs_loose_garbage_file(self):
        p = paths.install_id()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("garbage")
        os.chmod(p, 0o644)
        a = final_asr.install_id()
        self.assertRegex(a, r"^[0-9a-f]{32}$")
        self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)

    def test_install_id_write_failure_is_stable(self):
        (STATE).parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text("i am a file, not a dir")           # mkdir 必败
        try:
            a = final_asr.install_id()
            self.assertRegex(a, r"^[0-9a-f]{32}$")
            self.assertEqual(final_asr.install_id(), a)
        finally:
            STATE.unlink()

    def test_config_roundtrip_and_defaults(self):
        self.assertEqual(final_asr.read_config(), {"enabled": True, "acknowledged": ""})
        self.assertTrue(final_asr.write_config(False))
        c = final_asr.read_config()
        self.assertFalse(c["enabled"])
        self.assertRegex(c["acknowledged"], r"^\d{4}-\d{2}-\d{2}$")
        paths.cloud_asr_config().write_text("{not json")
        self.assertEqual(final_asr.read_config(), {"enabled": True, "acknowledged": ""})

    def test_consent_text_names_both_providers_and_no_false_guarantee(self):
        # 末段「想保证音频不出本机…」Owen 2026-10-11 看图后删掉；关法在面板开关的悬停提示里。
        t = final_asr.consent_text()
        self.assertIn("Groq", t)
        self.assertIn("阿里云", t)
        self.assertNotIn("纯转录档就不出本机", t)


class TestTranscribeLp(unittest.TestCase):
    def test_12_logprob_matches_own_call(self):
        def make(cls):
            o = object.__new__(cls)
            o._lock = threading.Lock()
            o.last_logprob = None

            class Stream:
                def accept_waveform(self, sr, w):
                    self.v = float(w[0])

            class Rec:
                def create_stream(self):
                    return Stream()

                def decode_stream(self, st):
                    time.sleep(0.002)
                    st.result = types.SimpleNamespace(text=f"t{st.v}", ys_log_probs=[st.v])
            o._rec = Rec()
            return o

        for cls in (asr.ParakeetASR, asr.WhisperASR):
            m = make(cls)
            bad: list = []

            def worker(v):
                w = np.full(3200, v, dtype="float32")
                for _ in range(40):
                    t, lp = m.transcribe_lp(w)
                    if lp != v or t != f"t{v}":
                        bad.append((v, t, lp))
            ths = [threading.Thread(target=worker, args=(v,)) for v in (-1.0, -2.0)]
            [t.start() for t in ths]
            [t.join() for t in ths]
            self.assertEqual(bad, [], cls.__name__)
            self.assertEqual(m.transcribe_lp(np.zeros(100, dtype="float32")), ("", None))
            self.assertEqual(m.transcribe(np.full(3200, -1.0, dtype="float32")), "t-1.0")


def tearDownModule():
    shutil.rmtree(_TMP_HOME, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
