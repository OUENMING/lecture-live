#!/usr/bin/env python3
"""`stream_asr.py` 的判据：纯逻辑核心（毫秒级，时钟注入）+ 传输层（本机假 Soniox / 假 Worker）。

⚠️ 全程离线、不花钱：假 Worker 是本机 HTTP 服务器（返回 `ws_url` 指向本机假 Soniox）。
   `final_asr.install_id` / `asr_endpoint.version_header` 在**导入 stream_asr 之前**打桩 ——
   本文件**不碰**真 `~/.classlive/`（HOME 也已隔离）。
⚠️ 真 Soniox 只由 `scripts/probe_stream_asr.py` 连（人手动跑、花钱），本文件一次都不连。
"""
from __future__ import annotations

import json
import os
import pathlib
import queue
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

_TMP = tempfile.TemporaryDirectory(prefix="cl_stream_asr_test_")
os.environ["HOME"] = _TMP.name
os.environ["CLASSLIVE_LAYOUT"] = "classic"
os.environ["CLASSLIVE_CLOUD_ASR"] = "0"
os.environ["CLASSLIVE_ASR_TOKEN"] = "cl-test"
os.environ.setdefault("CLASSLIVE_STREAM_ASR", "auto")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

# ⚠️ 必须在 `import stream_asr` 之前：它经 `_KeyClient` 会调这两个函数（会碰真 HOME）。
mock.patch("final_asr.install_id", return_value="0" * 32).start()
mock.patch("asr_endpoint.version_header", return_value="0.0.0-test").start()

import numpy as np  # noqa: E402

import websockets.sync.server as wss  # noqa: E402

import stream_asr  # noqa: E402
from stream_asr import SR, Sealed, StreamASR, StreamCore, classify_error, notice_for  # noqa: E402

FAILED: list[str] = []


def check(name: str, fn) -> None:
    t0 = time.monotonic()
    try:
        fn()
        print(f"  ✅ {name}  ({time.monotonic() - t0:.2f}s)")
    except Exception as e:                          # noqa: BLE001
        FAILED.append(name)
        print(f"  ❌ {name}: {type(e).__name__}: {e}")


def wait_until(cond, timeout: float = 5.0, step: float = 0.01) -> bool:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if cond():
            return True
        time.sleep(step)
    return cond()


# ══════════════════════════════════════════════════════════════════════
# 假 Soniox / 假 Worker
# ══════════════════════════════════════════════════════════════════════

def tok(text, st=0, en=100, final=False) -> dict:
    return {"text": text, "start_ms": st, "end_ms": en, "is_final": final}


class FakeSoniox:
    """本机假 Soniox：收到音频回给 `recv_audio`；`to_send` 里有东西就发给客户端。"""

    def __init__(self, *, echo: bool = False, stall_after: int = 0) -> None:
        self.recv_audio: "queue.Queue[bytes]" = queue.Queue()
        self.to_send: "queue.Queue" = queue.Queue()
        self.connections: list[dict] = []
        self.echo = echo
        #: 收满这么多块之后**不再读**（模拟对端卡住）——把客户端的发送缓冲填满，
        #: 逼出「音频队列满 → 丢最旧」那条路（平时客户端发送端根本不阻塞，测不到）。
        self.stall_after = stall_after
        self._n = 0
        self._done = threading.Event()            # close() 时唤醒卡住的 handler
        self._stop = threading.Event()
        self._srv = wss.serve(self._handler, "127.0.0.1", 0)
        self.port = self._srv.socket.getsockname()[1]
        self.url = f"ws://127.0.0.1:{self.port}/"
        self._th = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self._th.start()

    def _handler(self, conn) -> None:
        n = self._n
        self._n += 1
        rec = {"n": n, "headers": dict(conn.request.headers), "config": None,
               "audio": 0, "keepalives": 0}
        self.connections.append(rec)
        try:
            first = True
            while not self._stop.is_set():
                # ⚠️ 每轮把 `to_send` **整队**发出去再 recv：静音时没有入帧，
                #    一轮只发一条会把后面的回复永远卡住（假服务自己踩过的坑）。
                while True:
                    try:
                        item = self.to_send.get_nowait()
                    except queue.Empty:
                        break
                    if isinstance(item, tuple):      # ("close", code, reason)
                        conn.close(*item[1:])
                        return
                    conn.send(item if isinstance(item, str) else json.dumps(item))
                try:
                    raw = conn.recv(timeout=0.02)
                except TimeoutError:                # ⚠️ 只是这段时间没消息，别当断线
                    continue
                except Exception:                   # noqa: BLE001  (关闭)
                    break
                if isinstance(raw, bytes):
                    rec["audio"] += len(raw)
                    if self.echo:
                        self.recv_audio.put(raw)
                    if self.stall_after and rec["audio"] > self.stall_after * 3200:
                        self._done.wait(30.0)       # 卡住不读：对端发送缓冲会被填满
                    time.sleep(0.05)                # 慢服务：给发送端制造背压
                else:
                    try:
                        js = json.loads(raw)
                    except ValueError:
                        js = None
                    if first and isinstance(js, dict):
                        rec["config"] = js
                        first = False
                    elif isinstance(js, dict) and js.get("type") == "keepalive":
                        rec["keepalives"] += 1
                    else:
                        break                        # 空文本帧 = 客户端收尾
        except Exception:                            # noqa: BLE001
            pass
        finally:
            try:
                conn.close()
            except Exception:                        # noqa: BLE001
                pass

    def close(self) -> None:
        self._stop.set()
        self._done.set()                             # 唤醒卡住的 handler（不然收尾要等满 30 s）
        try:
            self._srv.shutdown()
        except Exception:                            # noqa: BLE001
            pass


class FakeWorker:
    """本机假 Worker：`POST /v1/soniox/key` 按脚本回（默认 200 + ws_url）。"""

    def __init__(self, ws_url: str, *, status: int = 200, body: dict | None = None) -> None:
        self.requests: list[dict] = []
        srv = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):                       # noqa: N802
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b""
                srv.requests.append({"path": self.path,
                                     "headers": dict(self.headers), "body": raw})
                st, js = srv.reply()
                payload = json.dumps(js).encode()
                self.send_response(st)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *a):               # noqa: A002
                pass

        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self._srv.server_address[1]}/v1/soniox/key"
        self.status = status
        self.body = body or {"api_key": "fake-key", "ws_url": ws_url, "region": "us",
                             "expires_in_seconds": 60, "max_session_s": 14400}
        self._th = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self._th.start()

    def reply(self) -> tuple[int, dict]:
        return self.status, dict(self.body)

    def close(self) -> None:
        try:
            self._srv.shutdown()
        except Exception:                            # noqa: BLE001
            pass


def make_asr(worker: FakeWorker, soniox: FakeSoniox, *, on_sealed=None, **kw) -> StreamASR:
    """建一个走假 Worker 的 StreamASR（`CLASSLIVE_ASR_ENDPOINT` 指到它）。"""
    os.environ["CLASSLIVE_ASR_ENDPOINT"] = worker.url
    kw.setdefault("keepalive_s", 0.5)
    kw.setdefault("response_timeout_s", 1.5)
    kw.setdefault("stable_s", 0.3)
    return StreamASR(passcode="cl-test", terms=["OLS", "regression"],
                     general="ECON10730 Data Analysis", on_sealed=on_sealed, **kw)


def silence(n: int) -> np.ndarray:
    return np.zeros(n, dtype="float32")


def hdr(headers: dict, name: str) -> str:
    """HTTP 头名不区分大小写（假服务那侧拿到的是原样小写）。"""
    return next((v for k, v in headers.items() if k.lower() == name.lower()), "")


# ══════════════════════════════════════════════════════════════════════
# ① 纯逻辑核心
# ══════════════════════════════════════════════════════════════════════

def t_core_nonfinal_batch_replace() -> None:
    c = StreamCore()
    c.feed_msg({"tokens": [tok("Hello", 0, 200), tok(" wor", 200, 300)]})
    assert c.draft_text() == "Hello wor", c.draft_text()
    c.feed_msg({"tokens": [tok("Hello", 0, 200), tok(" world", 200, 400)]})
    assert c.draft_text() == "Hello world", c.draft_text()
    # 整批替换：第二次的 non-final 就是全部，不是追加
    c.feed_msg({"tokens": [tok("Hello", 0, 200)]})
    assert c.draft_text() == "Hello", c.draft_text()


def t_core_final_appends_once() -> None:
    c = StreamCore()
    c.feed_msg({"tokens": [tok("Hello", 0, 200, True), tok(" world", 200, 400, True)]})
    assert c.draft_text() == "Hello world", c.draft_text()
    c.feed_msg({"tokens": [tok("Hello", 0, 200, True)]})      # 重复 final
    assert c.draft_text() == "Hello world", c.draft_text()
    # final 追加在 non-final 之前（屏上 = 已 final + 当前 non-final）
    c.feed_msg({"tokens": [tok(" again", 400, 600)]})
    assert c.draft_text() == "Hello world again", c.draft_text()


def t_core_control_filtered() -> None:
    c = StreamCore()
    c.feed_msg({"tokens": [tok("Hi", 0, 100, True), {"text": "<end>"},
                           {"text": "<fin>", "start_ms": 1, "end_ms": 2}]})
    assert c.draft_text() == "Hi", c.draft_text()


def t_core_p_stable() -> None:
    clk = {"t": 100.0}
    c = StreamCore(clock=lambda: clk["t"], stable_s=0.3)
    c.feed_msg({"tokens": [tok("Hello", 0, 200), tok(".", 200, 300)]})
    clk["t"] += 0.29
    c.feed_msg({"tokens": [tok("Hello", 0, 200), tok(".", 200, 300)]})
    assert c.take_sentences() == [], "0.29 s 不该封口"
    clk["t"] += 0.02                              # 共 0.31 s
    c.feed_msg({"tokens": [tok("Hello", 0, 200), tok(".", 200, 300)]})
    got = c.take_sentences()
    assert got == [Sealed("Hello.", 0.0, 0.3)], got
    assert c.draft_text() == "", c.draft_text()


def t_core_period_revised() -> None:
    clk = {"t": 0.0}
    c = StreamCore(clock=lambda: clk["t"], stable_s=0.3)
    c.feed_msg({"tokens": [tok("Hello", 0, 200), tok(".", 200, 300)]})
    clk["t"] += 0.2
    c.feed_msg({"tokens": [tok("Hello", 0, 200), tok(",", 200, 300)]})   # 句号被改掉
    clk["t"] += 0.2
    c.feed_msg({"tokens": [tok("Hello", 0, 200), tok(",", 200, 300)]})
    assert c.take_sentences() == [], "标点被改掉后不该封口"
    clk["t"] += 0.2
    c.feed_msg({"tokens": [tok("Hello", 0, 200), tok(" world", 200, 500), tok(".", 500, 600)]})
    assert c.take_sentences() == [], "新句号才刚开始计时"
    clk["t"] += 0.31
    c.feed_msg({"tokens": [tok("Hello", 0, 200), tok(" world", 200, 500), tok(".", 500, 600)]})
    got = c.take_sentences()
    assert got == [Sealed("Hello world.", 0.0, 0.6)], got


def t_core_final_seals_immediately() -> None:
    clk = {"t": 0.0}
    c = StreamCore(clock=lambda: clk["t"], stable_s=0.3)
    c.feed_msg({"tokens": [tok("Hi", 0, 100, True), tok(".", 100, 150, True)]})
    got = c.take_sentences()
    assert got == [Sealed("Hi.", 0.0, 0.15)], got


def t_core_sealed_from_nonfinal_not_duplicated_by_final() -> None:
    clk = {"t": 0.0}
    c = StreamCore(clock=lambda: clk["t"], stable_s=0.3)
    c.feed_msg({"tokens": [tok("Hello", 0, 200), tok(".", 200, 300)]})
    clk["t"] += 0.31
    c.feed_msg({"tokens": [tok("Hello", 0, 200), tok(".", 200, 300)]})
    assert len(c.take_sentences()) == 1
    # Soniox 之后把同一句变 final → 不能再封一次（text+start_ms 去重）
    c.feed_msg({"tokens": [tok("Hello", 0, 200, True), tok(".", 200, 300, True)]})
    again = c.take_sentences()
    assert again == [], f"final 不该把已封口的句子再送一遍：{again}"
    assert c.draft_text() == "", c.draft_text()


def t_core_final_with_shifted_timing_not_resealed() -> None:
    """真链路（10-17 冒烟）：non-final 变 final 时 start_ms / 文本会微调 —— 按水位线认旧词，不再封一遍。"""
    clk = {"t": 0.0}
    c = StreamCore(clock=lambda: clk["t"], stable_s=0.3)
    c.feed_msg({"tokens": [tok("And", 0, 100), tok(" it is", 100, 400), tok(" interesting.", 400, 900)]})
    clk["t"] += 0.31
    c.feed_msg({"tokens": [tok("And", 0, 100), tok(" it is", 100, 400), tok(" interesting.", 400, 900)]})
    assert len(c.take_sentences()) == 1
    c.feed_msg({"tokens": [tok("And", 30, 120, True), tok(" it", 120, 260, True), tok(" is", 260, 400, True),
                           tok(" interest", 420, 700, True), tok("ing.", 700, 880, True), tok(" So", 1500, 1600)]})
    clk["t"] += 0.5
    c.feed_msg({"tokens": [tok(" So", 1500, 1600)]})
    assert c.take_sentences() == [], "同一句换了时间戳变 final，不该再封一遍"
    assert c.draft_text() == " So", c.draft_text()


def t_core_earliest_period_seals_while_next_sentence_grows() -> None:
    """老师接着往下说、后面又出现新句号时，前一句照样 0.3 s 封口（不因候选换成后一个而重新计时）。"""
    clk = {"t": 0.0}
    c = StreamCore(clock=lambda: clk["t"], stable_s=0.3)
    c.feed_msg({"tokens": [tok("One", 0, 100), tok(".", 100, 200)]})
    clk["t"] += 0.2
    c.feed_msg({"tokens": [tok("One", 0, 100), tok(".", 100, 200), tok(" Two", 300, 400), tok(".", 400, 500)]})
    clk["t"] += 0.11                               # 第一个句号已挂 0.31 s
    c.feed_msg({"tokens": [tok("One", 0, 100), tok(".", 100, 200), tok(" Two", 300, 400), tok(".", 400, 500)]})
    got = c.take_sentences()
    assert got and got[0].text == "One.", got


def t_core_new_sentence_after_seal() -> None:
    clk = {"t": 0.0}
    c = StreamCore(clock=lambda: clk["t"], stable_s=0.3)
    c.feed_msg({"tokens": [tok("One", 0, 100), tok(".", 100, 200)]})
    clk["t"] += 0.31
    c.feed_msg({"tokens": [tok("One", 0, 100), tok(".", 100, 200),
                           tok(" Two", 200, 400)]})
    got = c.take_sentences()
    assert got == [Sealed("One.", 0.0, 0.2)], got
    assert c.draft_text() == " Two", c.draft_text()


def t_core_flush_pending() -> None:
    c = StreamCore()
    c.feed_msg({"tokens": [tok("Half", 0, 200), tok(" sentence", 200, 400)]})
    got = c.flush()
    assert got == [Sealed("Half sentence", 0.0, 0.4)], got
    assert c.draft_text() == "" and c.flush() == []


def t_context_budget_hard_cap_and_counting() -> None:
    """⭐ 超长术语表：config 总长 ≤ 官方口径预算（terms+general+text 合起来），
    被截的条数**如实记账**（不许静默）—— 从前客户端上限 30 000 ≈ 官方 3 倍，一撞就整节课退回。"""
    terms = [f"term{i:03d}-" + "x" * 36 for i in range(400)]          # ≈400×45 字符
    s = StreamASR(passcode="", terms=terms, general="ECON10730 Data Analysis",
                  terms_max=400)
    try:
        ctx = s._config()["context"]
        assert len(json.dumps(ctx, ensure_ascii=False)) <= stream_asr.CONTEXT_CHAR_BUDGET
        m = s.metrics()
        assert m["terms_n"] + m["terms_dropped"] == 400, m
        assert 0 < m["terms_n"] < 400, m
        assert len(ctx["terms"]) == m["terms_n"]
        assert ctx["terms"][0] == terms[0], "顺序保持：先到先保（本课词排在前的语义）"
        assert ctx["terms"][-1] == terms[m["terms_n"] - 1]
    finally:
        s.stop()


def t_context_count_cap_is_the_normal_path() -> None:
    """⭐ 平时走的是**条数小目标**（`TERMS_MAX`）不是字符上限：101 条短词该截 1 条；
    `terms_max` 可配置（判据/调参用）。"""
    terms = [f"t{i:03d}" for i in range(stream_asr.TERMS_MAX + 1)]
    s = StreamASR(passcode="", terms=terms)
    try:
        m = s.metrics()
        assert m["terms_n"] == stream_asr.TERMS_MAX and m["terms_dropped"] == 1, m
    finally:
        s.stop()
    s2 = StreamASR(passcode="", terms=terms, terms_max=5)
    try:
        m2 = s2.metrics()
        assert m2["terms_n"] == 5 and m2["terms_dropped"] == len(terms) - 5, m2
    finally:
        s2.stop()


def t_context_no_terms_stays_clean() -> None:
    """没有术语、也没有课名 → **不带 context**（不凭空发明上下文）；
    有课名时也不带空的 `terms` 键。"""
    s = StreamASR(passcode="", terms=[])
    try:
        assert "context" not in s._config(), s._config()
    finally:
        s.stop()
    s2 = StreamASR(passcode="", terms=[], general="ECON10730 Data Analysis")
    try:
        ctx = s2._config().get("context") or {}
        assert "terms" not in ctx and ctx.get("general"), ctx
    finally:
        s2.stop()


def t_context_text_shares_the_budget() -> None:
    """`context.text` 与 terms/general **同一预算**：超了截到上限，总量仍 ≤ 预算；
    且它排在 terms 之后 —— 术语不被 text 挤掉。"""
    terms = [f"term{i:03d}-" + "x" * 16 for i in range(50)]
    s = StreamASR(passcode="", terms=terms, general="ECON10730 Data Analysis",
                  context_text="课件大纲摘要 " * 2000)
    try:
        ctx = s._config()["context"]
        assert "text" in ctx, sorted(ctx)
        assert len(ctx["text"]) <= stream_asr.CONTEXT_TEXT_MAX, len(ctx["text"])
        assert len(json.dumps(ctx, ensure_ascii=False)) <= stream_asr.CONTEXT_CHAR_BUDGET
        assert s.metrics()["terms_dropped"] == 0, "术语没超量：text 不许把 terms 挤掉"
    finally:
        s.stop()


def t_general_official_shape() -> None:
    """`general` 用官方模板的形状 `[{key, value}]`（旧版发的 `{"title": …}` 是错的形状）；
    认不出的课号前缀**不发 domain**（猜错领域比不给更坏）。"""
    pairs = stream_asr.general_pairs("ECON10730 Data Analysis")
    assert pairs[0] == ("topic", "ECON10730 Data Analysis"), pairs
    assert ("domain", "economics") in pairs, pairs
    assert ("setting", "university lecture") in pairs, pairs
    assert len(pairs) <= stream_asr.GENERAL_KEYS_MAX
    assert stream_asr.general_pairs("") == []
    assert all(k != "domain" for k, _ in stream_asr.general_pairs("Mystery 101"))


def t_metrics_seal_counts_and_latency() -> None:
    """⭐ 3.10 量具：封口句数 + 上移延迟（墙钟 − 句尾音频时刻）—— 注入时钟做**精确**断言。"""
    son = FakeSoniox()
    wk = FakeWorker(son.url)
    clk = {"t": 100.0}
    got: "queue.Queue[Sealed]" = queue.Queue()
    s = make_asr(wk, son, on_sealed=got.put, clock=lambda: clk["t"])
    try:
        assert wait_until(s.alive)
        s.feed(silence(SR // 10))                 # 第一块音频：延迟零点 = 100.0
        clk["t"] = 102.0                          # 句尾音频 0.4 s 的那句在墙钟 102.0 封口
        son.to_send.put({"tokens": [tok("Hi", 0, 300, True), tok(".", 300, 400, True)]})
        assert wait_until(lambda: not got.empty()), "final 句号该立刻封口"
        assert got.get_nowait().end_s == 0.4
        clk["t"] = 103.30
        son.to_send.put({"tokens": [tok(" Bye", 400, 900), tok(".", 900, 1000)]})
        assert wait_until(lambda: "Bye." in s.draft_text()), s.draft_text()
        clk["t"] = 103.61                         # 候选句号挂了 0.31 s -> P_stable 到了
        son.to_send.put({"tokens": [tok(" Bye", 400, 900), tok(".", 900, 1000)]})
        assert wait_until(lambda: not got.empty()), "P_stable 0.3 s 后该封口"
        m = s.metrics()
        assert m["engine"] == "soniox" and m["fallback_reason"] == "", m
        assert m["sealed_n"] == 2, m
        lat = m["seal_latency_s"]
        assert lat and lat["n"] == 2, lat
        assert abs(lat["p50"] - 2.105) < 0.1, lat   # (1.6 + 2.61) / 2
        assert m["terms_n"] == 2 and m["terms_dropped"] == 0, m   # make_asr 的两条都进了
    finally:
        s.stop()
        son.close()
        wk.close()


def t_seal_latency_pure() -> None:
    """延迟公式：负数钳 0（丢块/抖动）；没喂过音频 -> None（**不是 0** —— 那是两回事）。"""
    f = stream_asr.seal_latency_s
    assert abs(f(10.5, 9.0, 1.2) - 0.3) < 1e-9, f(10.5, 9.0, 1.2)
    assert f(9.0, 9.0, 0.5) == 0.0
    assert f(60.0, None, 1.0) is None


def t_classify_and_notice() -> None:
    assert classify_error("organization_balance_exhausted") == "fatal"
    assert classify_error("unauthenticated") == "fatal"
    assert classify_error("service_unavailable") == "retry"
    assert classify_error("temp_api_key_session_expired") == "fatal"
    assert classify_error("403") == "fatal"
    assert classify_error("429") == "retry"
    assert classify_error("") == "retry"
    assert "额度" in notice_for("quota")
    assert notice_for("没见过的原因") == stream_asr.NOTICE_DEFAULT


# ══════════════════════════════════════════════════════════════════════
# ② 传输层（假 Worker + 假 Soniox）
# ══════════════════════════════════════════════════════════════════════

def t_transport_happy_path() -> None:
    son = FakeSoniox(echo=True)
    wk = FakeWorker(son.url)
    got: "queue.Queue[Sealed]" = queue.Queue()
    s = make_asr(wk, son, on_sealed=got.put)
    try:
        assert wait_until(s.alive), "没建起来"
        assert wk.requests[0]["path"] == "/v1/soniox/key"
        assert hdr(wk.requests[0]["headers"], "Authorization") == "Bearer cl-test"
        assert hdr(wk.requests[0]["headers"], "X-ClassLive-Install")
        son.to_send.put({"tokens": [tok("Hello", 0, 200), tok(".", 200, 300)]})
        assert wait_until(lambda: not got.empty()), "P_stable 0.3 s 后该封口"
        first = got.get_nowait()
        assert first.text == "Hello." and first.end_s == 0.3, first
        assert s.draft_text() == "", s.draft_text()
        son.to_send.put({"tokens": [tok("Bye", 300, 400, True), tok(".", 400, 450, True)]})
        assert wait_until(lambda: not got.empty()), "final 句号该立刻封口"
        second = got.get_nowait()
        assert second.text == "Bye.", second
        cfg = son.connections[0]["config"]
        assert cfg["model"] == "stt-rt-v5" and cfg["audio_format"] == "pcm_s16le"
        assert cfg["sample_rate"] == 16000 and cfg["num_channels"] == 1
        assert cfg["language_hints"] == ["en"] and cfg["enable_endpoint_detection"] is True
        assert cfg["context"]["terms"] == ["OLS", "regression"]
        g = {e["key"]: e["value"] for e in cfg["context"]["general"]}
        assert g["topic"] == "ECON10730 Data Analysis"
        assert g["domain"] == "economics" and g["setting"] == "university lecture"
        assert g["language"] == "English"
        assert "api_key" not in cfg, "key 不许进 config 消息（只在 Authorization 头里）"
        assert hdr(son.connections[0]["headers"], "Authorization") == "Bearer fake-key"
        # ⚠️ 块长 120 ms（3840 B）：一份 0.1 s 的样本还凑不满一块，会被 carry 住 ——
        #    喂三份（0.3 s）才保证有一块真的发出去（上一条测试也栽在这）。
        for _ in range(3):
            s.feed(silence(SR // 10))
            time.sleep(0.05)
        assert wait_until(lambda: son.connections[0]["audio"] > 0), "音频没推出去"
        assert s.cum_samples() == 3 * (SR // 10)
    finally:
        s.stop()
        son.close()
        wk.close()
    assert not s.alive() and s.reason() == "", (s.alive(), s.reason())


def t_transport_fatal_error_no_retry() -> None:
    """致命错误（403/会话过期那类）→ 立即退回，**不重连**。

    ⚠️ 真机上 error frame 之后 Soniox 会**立刻断连**（官方 error-handling 页）——
    假服务照此办理：先发错误帧再关。两件事可能同一批到达，判据是「先看 error_type」。
    """
    son = FakeSoniox()
    wk = FakeWorker(son.url)
    s = make_asr(wk, son)
    try:
        assert wait_until(s.alive)
        son.to_send.put({"error_type": "temp_api_key_session_expired"})
        son.to_send.put(("close", 1008, "policy violation"))
        assert wait_until(lambda: not s.alive() and s.reason() != ""), "致命错误该直接退回"
        assert s.reason() == "error", s.reason()
        assert len(wk.requests) == 1, "致命类不许重连（key 是 single_use，重连=白烧）"
        assert len(son.connections) == 1
        s.feed(silence(1000))                     # 死了之后喂音频不许抛
        assert "本地识别" in s.notice_text()
    finally:
        s.stop()
        son.close()
        wk.close()


def t_transport_balance_exhausted_falls_back_now() -> None:
    """`organization_balance_exhausted`（余额用完）是致命类：**不重连**，直接退回本地。"""
    son = FakeSoniox()
    wk = FakeWorker(son.url)
    s = make_asr(wk, son)
    try:
        assert wait_until(s.alive)
        son.to_send.put({"error_type": "organization_balance_exhausted"})
        son.to_send.put(("close", 1008, "balance"))
        assert wait_until(lambda: not s.alive() and s.reason() != "", 8.0), s.reason()
        assert len(wk.requests) == 1, f"余额用完不该再领 key 重连：{len(wk.requests)}"
        s.feed(silence(1000))
    finally:
        s.stop()
        son.close()
        wk.close()


def t_transport_reconnect_once() -> None:
    son = FakeSoniox()
    wk = FakeWorker(son.url)
    got: "queue.Queue[Sealed]" = queue.Queue()
    s = make_asr(wk, son, on_sealed=got.put)
    try:
        assert wait_until(s.alive)
        son.to_send.put(("close", 1011, "boom"))
        assert wait_until(lambda: len(son.connections) >= 2 and s.alive()), "该重领 key 重连一次"
        assert len(wk.requests) == 2, len(wk.requests)
        son.to_send.put({"tokens": [tok("Again", 0, 100, True), tok(".", 100, 150, True)]})
        assert wait_until(lambda: not got.empty()), "重连后该照常出词"
        assert got.get_nowait().text == "Again."
    finally:
        s.stop()
        son.close()
        wk.close()


def t_transport_reconnect_fails_fallback() -> None:
    son = FakeSoniox()
    wk = FakeWorker(son.url)
    s = make_asr(wk, son)
    try:
        assert wait_until(s.alive)
        son.close()                               # 服务整个下线：重连必失败
        assert wait_until(lambda: not s.alive() and s.reason() != "", 8.0), \
            f"重连也失败该退回（reason={s.reason()!r}）"
        assert len(wk.requests) <= 2, "最多重连一次"
        s.feed(silence(1000))
    finally:
        s.stop()
        son.close()
        wk.close()


def t_transport_key_429_is_local() -> None:
    son = FakeSoniox()
    wk = FakeWorker(son.url, status=429, body={"error": "quota_exceeded"})
    s = make_asr(wk, son)
    try:
        assert wait_until(lambda: s.reason() != ""), "429 该一开始就退回"
        assert s.reason() == "quota", s.reason()
        assert s.metrics()["fallback_reason"] == "quota"     # ⭐ 3.10：退回原因进量具块
        assert not s.alive()
        assert "额度" in s.notice_text()
        assert len(son.connections) == 0, "没额度不该去连 Soniox"
    finally:
        s.stop()
        son.close()
        wk.close()


def t_transport_no_passcode() -> None:
    son = FakeSoniox()
    wk = FakeWorker(son.url)
    s = StreamASR(passcode="", request_key=lambda pc: wk and {})
    try:
        assert wait_until(lambda: s.reason() != "")
        assert s.reason() == "no_passcode" and not s.alive()
    finally:
        s.stop()
        son.close()
        wk.close()


def t_transport_stall_times_out() -> None:
    son = FakeSoniox()
    wk = FakeWorker(son.url)
    # ⚠️ keepalive 要**比响应超时慢**，否则每次 keepalive 都会把判死窗口往后推
    s = make_asr(wk, son, response_timeout_s=0.5, keepalive_s=2.0, stable_s=0.3)
    try:
        assert wait_until(s.alive)
        for _ in range(30):                       # 一直喂，Soniox 一声不吭
            s.feed(silence(SR // 10))
            time.sleep(0.02)
        assert wait_until(lambda: s.reason() == "timeout", 6.0), s.reason()
        assert not s.alive()
    finally:
        s.stop()
        son.close()
        wk.close()


def t_transport_queue_full_drops_oldest() -> None:
    """队列满 → 丢最旧、`feed()` 不阻塞。

    ⚠️ 平时客户端发送端根本不阻塞（库内部有发送缓冲），队列永远不满、测不到丢弃。
    要让这条判据真的能红：假服务收两块之后**卡住不读**，把发送缓冲填满，逼出发送阻塞。
    """
    son = FakeSoniox(stall_after=1)
    wk = FakeWorker(son.url)
    s = make_asr(wk, son, audio_max_s=0.5, chunk_s=0.1)     # 队列上限 5 块
    try:
        assert wait_until(s.alive)
        worst = 0.0
        # 一口气喂 300 块（30 s 音频，远超过内核发送/接收缓冲）：前面一批被内核缓冲吃掉
        # （发送端不阻塞），填满之后发送端真的卡住 → 队列满 → 丢最旧。
        for _ in range(300):
            t0 = time.monotonic()
            s.feed(silence(1600))                 # 0.1 s 一块
            worst = max(worst, time.monotonic() - t0)
        assert worst < 0.05, f"feed() 单次阻塞 {worst * 1000:.0f}ms —— 音频回调必须立刻返回"
        assert s.cum_samples() == 300 * 1600
        assert s.dropped() > 0, "队列满了该丢最旧"
        assert s._audio.qsize() <= s._max_chunks, "队列必须是有界的"
    finally:
        son._done.set()                           # 先放开卡住的假服务，收尾才不用等满
        s.stop()
        son.close()
        wk.close()


def t_transport_keepalive_when_idle() -> None:
    son = FakeSoniox()
    wk = FakeWorker(son.url)
    s = make_asr(wk, son, keepalive_s=0.4)
    try:
        assert wait_until(s.alive)
        assert wait_until(lambda: son.connections[0]["keepalives"] >= 1, 5.0), \
            "静音时该发 keepalive"
    finally:
        s.stop()
        son.close()
        wk.close()


def t_transport_stop_clean() -> None:
    son = FakeSoniox()
    wk = FakeWorker(son.url)
    got: "queue.Queue[Sealed]" = queue.Queue()
    s = make_asr(wk, son, on_sealed=got.put)
    try:
        assert wait_until(s.alive)
        s.feed(silence(SR // 10))
        t0 = time.monotonic()
        s.stop()
        assert time.monotonic() - t0 < 5.0, "stop() 该在收尾窗口内返回"
        assert not s.alive() and s.reason() == "", (s.alive(), s.reason())
        s.stop()                                  # 幂等
    finally:
        son.close()
        wk.close()


def t_transport_stop_flushes_half_sentence() -> None:
    """下课收尾：还没等到句号的半句也要送出去（不然老师最后那半句整句丢）。"""
    son = FakeSoniox()
    wk = FakeWorker(son.url)
    got: "queue.Queue[Sealed]" = queue.Queue()
    s = make_asr(wk, son, on_sealed=got.put)
    try:
        assert wait_until(s.alive)
        son.to_send.put({"tokens": [tok("Half", 0, 200), tok(" sentence", 200, 400)]})
        assert wait_until(lambda: s.draft_text() == "Half sentence"), s.draft_text()
        s.stop()
        assert wait_until(lambda: not got.empty(), 3.0), "收尾该 flush 半句"
        flushed = got.get_nowait()
        assert flushed.text == "Half sentence", flushed
    finally:
        son.close()
        wk.close()


def t_transport_default_key_client() -> None:
    """不注入 `request_key`：走真 `_KeyClient`（指向假 Worker）—— 认头写法的判据。"""
    son = FakeSoniox()
    wk = FakeWorker(son.url)
    os.environ["CLASSLIVE_ASR_ENDPOINT"] = wk.url
    s = StreamASR(passcode="cl-test", keepalive_s=5.0)
    try:
        assert wait_until(s.alive, 6.0), s.reason()
        h = wk.requests[0]["headers"]
        assert hdr(h, "Authorization") == "Bearer cl-test"
        assert hdr(h, "X-ClassLive-Install")
        assert hdr(h, "X-ClassLive-Version")
        assert json.loads(wk.requests[0]["body"] or b"{}") == {}, "不给 region 时 body 还是空的（老行为）"
    finally:
        s.stop()
        son.close()
        wk.close()


def t_transport_region_goes_to_worker() -> None:
    """`region=` 透传进领 key 的请求体（多地区改道就靠这一条；空串不带）。"""
    son = FakeSoniox()
    wk = FakeWorker(son.url)
    os.environ["CLASSLIVE_ASR_ENDPOINT"] = wk.url
    for region, want in (("jp", {"region": "jp"}), (" EU ", {"region": "eu"}), ("", {})):
        s = StreamASR(passcode="cl-test", keepalive_s=5.0, region=region)
        try:
            assert wait_until(s.alive, 6.0), (region, s.reason())
            assert json.loads(wk.requests[-1]["body"] or b"{}") == want, (region, wk.requests[-1]["body"])
        finally:
            s.stop()
        wk.requests.clear()
    son.close()
    wk.close()


def t_transport_segments() -> None:
    son = FakeSoniox()
    wk = FakeWorker(son.url)
    s = make_asr(wk, son)
    try:
        assert wait_until(s.alive)
        s.feed(silence(SR))                       # 1 s
        m1 = s.mark_segment()
        s.feed(silence(SR))                       # 共 2 s
        m2 = s.mark_segment()
        assert (m1, m2) == (1, 2), (m1, m2)
        assert s.seg_for_audio(1.5) == 1          # 第一个 mark 在 1.0 s
        assert s.seg_for_audio(2.5) == 2          # 第二个 mark 在 2.0 s
        assert s.seg_for_audio(9.9) == 2
    finally:
        s.stop()
        son.close()
        wk.close()


def main() -> int:
    print("stream_asr 判据（全部离线）")
    for name, fn in [
        ("core: non-final 整批替换", t_core_nonfinal_batch_replace),
        ("core: final 追加 + 去重", t_core_final_appends_once),
        ("core: <end>/<fin> 过滤", t_core_control_filtered),
        ("core: P_stable 0.3 s 封口", t_core_p_stable),
        ("core: 句号被改掉不封口", t_core_period_revised),
        ("core: 先变 final 立刻封口", t_core_final_seals_immediately),
        ("core: 已封口不被 final 重复", t_core_sealed_from_nonfinal_not_duplicated_by_final),
        ("core: 封口后新句留下当草稿", t_core_new_sentence_after_seal),
        ("core: final 换了时间戳不重封", t_core_final_with_shifted_timing_not_resealed),
        ("core: 最早的句号先封口", t_core_earliest_period_seals_while_next_sentence_grows),
        ("core: 收尾 flush 半句", t_core_flush_pending),
        ("config: 超长术语表 ≤ 预算 + 截断记账", t_context_budget_hard_cap_and_counting),
        ("config: 平时按条数小目标（TERMS_MAX）", t_context_count_cap_is_the_normal_path),
        ("config: 没术语不带 context", t_context_no_terms_stays_clean),
        ("config: text 与 terms 同一预算", t_context_text_shares_the_budget),
        ("config: general 官方形状", t_general_official_shape),
        ("量具: seal_latency 纯函数", t_seal_latency_pure),
        ("量具: 封口数 + 上移延迟（metrics）", t_metrics_seal_counts_and_latency),
        ("classify_error / notice_for", t_classify_and_notice),
        ("transport: 正常出两句 + config", t_transport_happy_path),
        ("transport: 致命错误不重连", t_transport_fatal_error_no_retry),
        ("transport: 余额耗尽 → 不重连直接退回", t_transport_balance_exhausted_falls_back_now),
        ("transport: 断线重连一次", t_transport_reconnect_once),
        ("transport: 重连也失败 → 退回", t_transport_reconnect_fails_fallback),
        ("transport: 领 key 429 → 本地", t_transport_key_429_is_local),
        ("transport: 没通行码 → 本地", t_transport_no_passcode),
        ("transport: 15 s 无响应判死", t_transport_stall_times_out),
        ("transport: 队列满丢旧不阻塞", t_transport_queue_full_drops_oldest),
        ("transport: 静音时 keepalive", t_transport_keepalive_when_idle),
        ("transport: stop() 干净收尾", t_transport_stop_clean),
        ("transport: stop() flush 半句", t_transport_stop_flushes_half_sentence),
        ("transport: 默认领 key 客户端（真头）", t_transport_default_key_client),
        ("transport: region= 进领 key 请求体", t_transport_region_goes_to_worker),
        ("transport: 段号映射", t_transport_segments),
    ]:
        check(name, fn)
    print(f"\n{'❌ 失败 ' + str(len(FAILED)) + '：' + '、'.join(FAILED) if FAILED else '✅ 全部通过'}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
