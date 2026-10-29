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
    kw.setdefault("retry_delays_s", (0.0, 0.1, 0.1, 0.1))   # 生产见 stream_asr.RETRY_DELAYS_S，测试压到 0.1 s
    kw.setdefault("retry_jitter", 0.0)                        # 既有判据量精确退避时间；抖动单独有一条
    kw.setdefault("drop_log", "")                             # 默认关：别让几十条判据的输出被掉线行淹掉
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


def t_transport_fatal_error_code_only_no_retry() -> None:
    """错误帧**只有 `error_code`**（没有 `error_type`）也认得出致命类，不白烧一把 key 重连。"""
    son = FakeSoniox()
    wk = FakeWorker(son.url)
    s = make_asr(wk, son)
    try:
        assert wait_until(s.alive)
        son.to_send.put({"error_code": 401, "error_message": "x"})
        son.to_send.put(("close", 1008, "policy violation"))
        assert wait_until(lambda: not s.alive() and s.reason() != ""), "该直接退回"
        assert s.reason() == "error", s.reason()
        assert len(wk.requests) == 1, f"只给数字码的致命错误也不许重连：领了 {len(wk.requests)} 次 key"
    finally:
        s.stop()
        son.close()
        wk.close()


def t_transport_missing_websockets_records_detail() -> None:
    """⭐ 3.10.1：本机缺 `websockets`（补依赖没成）→ 退回 `error`，且 `fallback_detail`
    写出异常类型 —— 那位测试者的报告里只有一个 `error`，原因靠猜。"""
    son = FakeSoniox()
    wk = FakeWorker(son.url)
    import ws_compat
    gone = ModuleNotFoundError("No module named 'websockets'")
    with mock.patch.object(ws_compat, "client", side_effect=gone):
        s = make_asr(wk, son)
        try:
            assert wait_until(lambda: not s.alive() and s.reason() != ""), "该退回"
            assert s.reason() == "error", s.reason()
            d = s.metrics()["fallback_detail"]
            assert d.startswith(("ImportError", "ModuleNotFoundError")), d
        finally:
            s.stop()
    son.close()
    wk.close()


def t_vendor_websockets_fallback_connects() -> None:
    """⭐ 3.10.1：**本机没装 websockets** 时 `ws_compat.client()` 取仓库自带的那份，且真能连上。

    子进程 `-I -S`：不加载 site-packages（= 那台补依赖失败的机器），`sys.path` 只有仓库根。
    ⚠️ 变异验证：删掉 `ws_compat` 里 `sys.path.insert(0, v)` -> 子进程 ImportError、这条红。
    """
    import subprocess
    son = FakeSoniox()
    try:
        code = ("import sys; sys.path.insert(0, %r)\n"
                "import ws_compat; wsc = ws_compat.client()\n"
                "import websockets; assert '/vendor/' in websockets.__file__, websockets.__file__\n"
                "ws = wsc.connect(%r, open_timeout=5); ws.close(); print('OK')\n"
                % (str(pathlib.Path(__file__).resolve().parent.parent), son.url))
        r = subprocess.run([sys.executable, "-I", "-S", "-c", code],
                           capture_output=True, text=True, timeout=30)
        assert r.returncode == 0 and "OK" in r.stdout, (r.stdout + r.stderr)[-400:]
    finally:
        son.close()


def t_transport_force_local_manual() -> None:
    """暗号「菠萝丁」：手动关流 → 原因记 manual、文案说清楚、不重连。"""
    son = FakeSoniox()
    wk = FakeWorker(son.url)
    s = make_asr(wk, son)
    try:
        assert wait_until(s.alive)
        s.force_local()
        assert wait_until(lambda: not s.alive() and s.reason() != ""), "该退回"
        assert s.reason() == "manual", s.reason()
        assert "菠萝丁" in s.notice_text(), s.notice_text()
        assert len(wk.requests) == 1, "手动退回不许再领 key"
        assert s.metrics()["fallback_reason"] == "manual"
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


def t_transport_reconnect_after_sealing_keeps_sealing() -> None:
    """重连**之前已经封过句**：新连接的 token 时间从 0 起，必须接着封口。

    2026-10-18 合并检查抓到：水位线停在旧连接的时间上，新连接的词全被当旧词丢掉
    → 整节课不再出字幕，`alive()` 却一直是真（本地也不接手）。
    """
    son = FakeSoniox()
    wk = FakeWorker(son.url)
    got: "queue.Queue[Sealed]" = queue.Queue()
    s = make_asr(wk, son, on_sealed=got.put)
    try:
        assert wait_until(s.alive)
        s.feed(silence(SR))                                   # 流里 0–1 s
        assert wait_until(lambda: son.connections and son.connections[0]["audio"] >= SR)
        son.to_send.put({"tokens": [tok("First", 0, 400, True), tok(".", 400, 500, True)]})
        assert wait_until(lambda: not got.empty()), "第一条连接该封出一句"
        first = got.get_nowait()
        son.to_send.put(("close", 1011, "boom"))
        assert wait_until(lambda: len(son.connections) >= 2 and s.alive()), "该重连"
        s.feed(silence(SR))                                   # 流里 1–2 s，走新连接
        assert wait_until(lambda: son.connections[1]["audio"] > 0)
        son.to_send.put({"tokens": [tok("Again", 0, 100, True), tok(".", 100, 150, True)]})
        assert wait_until(lambda: not got.empty()), "重连后新词被当成旧词丢了（水位线没对齐）"
        again = got.get_nowait()
        assert again.text == "Again.", again
        assert again.start_s >= first.end_s, f"新连接的时间该接在旧的后面：{first} → {again}"
    finally:
        s.stop()
        son.close()
        wk.close()


def t_transport_reconnect_fails_fallback() -> None:
    """服务整个下线：按预算（这里 2 次）重连都失败才退回，不是无限重试。"""
    son = FakeSoniox()
    wk = FakeWorker(son.url)
    s = make_asr(wk, son, retry_delays_s=(0.0, 0.1))
    try:
        assert wait_until(s.alive)
        son.close()
        assert wait_until(lambda: not s.alive() and s.reason() != "", 8.0), \
            f"预算用完该退回（reason={s.reason()!r}）"
        assert len(wk.requests) == 3, f"1 次首连 + 2 次重连，实际领 key {len(wk.requests)} 次"
        s.feed(silence(1000))
    finally:
        s.stop()
        son.close()
        wk.close()


def t_transport_second_drop_still_reconnects() -> None:
    """⭐ 10-23 ECON10770：一节课第二次掉线不能直接退回本地（旧写法整节课只给一次重连）。"""
    son = FakeSoniox()
    wk = FakeWorker(son.url)
    s = make_asr(wk, son, retry_delays_s=(0.0, 0.1, 0.1))
    try:
        assert wait_until(s.alive)
        son.to_send.put(("close", 1011, "boom"))
        assert wait_until(lambda: len(son.connections) >= 2 and s.alive()), "第一次掉线该重连"
        son.to_send.put(("close", 1011, "boom again"))
        assert wait_until(lambda: len(son.connections) >= 3 and s.alive(), 8.0), \
            "第二次掉线也该重连，而不是退回本地"
        assert s.reason() == "", s.reason()
        assert s.metrics()["reconnects"] == 2, s.metrics()["reconnects"]
    finally:
        s.stop()
        son.close()
        wk.close()


def t_transport_backoff_waits_between_retries() -> None:
    """第二次重连前要等退避时间；等待期间不判退回（本地接手），恢复后自动回到 Soniox。"""
    son = FakeSoniox()
    wk = FakeWorker(son.url)
    s = make_asr(wk, son, retry_delays_s=(0.0, 1.0))
    try:
        assert wait_until(s.alive)
        son.to_send.put(("close", 1011, "a"))
        assert wait_until(lambda: len(son.connections) >= 2 and s.alive())
        t0 = time.monotonic()
        son.to_send.put(("close", 1011, "b"))
        assert wait_until(lambda: not s.alive(), 3.0), "掉线后 alive 该为假（本地接手）"
        assert s.reason() == "", "退避等待中不是退回"
        assert wait_until(lambda: len(son.connections) >= 3 and s.alive(), 8.0)
        assert time.monotonic() - t0 >= 0.9, "第二次重连之前该等满退避时间"
        assert s.metrics()["down_s"] >= 0.8, s.metrics()["down_s"]
    finally:
        s.stop()
        son.close()
        wk.close()


def t_transport_budget_resets_after_stable_session() -> None:
    """上一个会话稳定跑过 `retry_reset_s` → 预算还原（长课不能只给固定几次）。"""
    son = FakeSoniox()
    wk = FakeWorker(son.url)
    s = make_asr(wk, son, retry_delays_s=(0.0,), retry_reset_s=0.3)
    try:
        assert wait_until(s.alive)
        for n in (2, 3, 4):
            time.sleep(0.5)                                  # 每个会话都稳定超过 0.3 s
            son.to_send.put(("close", 1011, f"drop{n}"))
            assert wait_until(lambda n=n: len(son.connections) >= n and s.alive(), 5.0), \
                f"第 {n} 次连接该接上（预算只有 1 次、靠稳定还原）"
        assert s.reason() == ""
    finally:
        s.stop()
        son.close()
        wk.close()


# ── 2026-10-29 中国用户断联（docs/PLAN-stream-reconnect-20261029.md）──

def t_transport_send_stuck_watchdog_reconnects() -> None:
    """⭐ `ws.send` 卡死（上行塞死、连接还活着）→ 看门狗掐 socket → 按 stale 重连。

    ⚠️ 本机回环上对端不读也**卡不住** `sendall`（实测：40 s 灌进 2574 s 音频都没阻塞，最后是库的
       ping 超时在 40 s 救场），所以不拿「真的塞死 TCP」当夹具，而是把第一条连接的 `send` 换成
       阻塞到 `close_socket()` 被调用为止 —— 与真卡死同形（卡在 send 里、靠掐 socket 才出得来）。
    ⚠️ `response_timeout_s=60`：只有看门狗能让它重连，别让旧的无响应判据抢了功劳。
    """
    import websockets.sync.client as wsc
    real_connect = wsc.connect
    unblock = threading.Event()
    calls = {"n": 0}

    def connect(*a, **kw):
        ws = real_connect(*a, **kw)
        calls["n"] += 1
        if calls["n"] == 1:                                   # 只让第一条连接卡
            real_send, real_close = ws.send, ws.close_socket
            sent = {"n": 0}

            def send(data, *aa, **kk):
                if isinstance(data, bytes):
                    sent["n"] += 1
                    if sent["n"] > 3:
                        unblock.wait(30.0)                    # 卡在 send 里
                        raise OSError("socket closed under a blocked send")
                return real_send(data, *aa, **kk)

            def close_socket():
                unblock.set()
                real_close()
            ws.send, ws.close_socket = send, close_socket
        return ws

    son = FakeSoniox()
    wk = FakeWorker(son.url)
    with mock.patch.object(wsc, "connect", connect):
        s = make_asr(wk, son, response_timeout_s=60.0, send_stuck_s=0.6, retry_delays_s=(0.0,))
        try:
            assert wait_until(s.alive)
            t0 = time.monotonic()
            while time.monotonic() - t0 < 8.0 and len(son.connections) < 2:
                s.feed(silence(1600))
                time.sleep(0.02)
            assert len(son.connections) >= 2, "发送卡死该被看门狗发现并重连"
            assert time.monotonic() - t0 < 6.0, "看门狗该在 send_stuck_s(0.6) 之后很快动手，而不是等别的判据"
            sess = s.metrics()["sessions"]
            assert sess and sess[0]["why"] == "stale", sess
            assert sess[0]["detail"].startswith("send stuck"), f"看门狗写的原因被 close 1006 盖掉了: {sess}"
        finally:
            unblock.set()
            s.stop()
            son.close()
            wk.close()


def t_transport_config_send_stuck_covered() -> None:
    """⭐ config 帧（建连后第一条 send）卡死 → 也要被看门狗救。

    2026-10-29 复核发现：看门狗原来只盯 keepalive 与音频两条路，config 帧和收尾空帧的
    `ws.send` 不设 `_send_since` → 那两处卡死它永远看不见。这里把第一条连接的**文本帧**
    卡住，断言仍然靠看门狗重连（`response_timeout_s=60` 排除旧判据抢功）。
    """
    import websockets.sync.client as wsc
    real_connect = wsc.connect
    unblock = threading.Event()
    calls = {"n": 0}

    def connect(*a, **kw):
        ws = real_connect(*a, **kw)
        calls["n"] += 1
        if calls["n"] == 1:
            real_send, real_close = ws.send, ws.close_socket
            state = {"blocked": False}

            def send(data, *aa, **kk):
                if isinstance(data, str) and not state["blocked"]:
                    state["blocked"] = True                  # 只卡 config 那一帧（收尾空帧是后面的事）
                    unblock.wait(30.0)
                    raise OSError("socket closed under a blocked config send")
                return real_send(data, *aa, **kk)

            def close_socket():
                unblock.set()
                real_close()
            ws.send, ws.close_socket = send, close_socket
        return ws

    son = FakeSoniox()
    wk = FakeWorker(son.url)
    with mock.patch.object(wsc, "connect", connect):
        s = make_asr(wk, son, response_timeout_s=60.0, send_stuck_s=0.6, retry_delays_s=(0.0,))
        try:
            t0 = time.monotonic()
            while time.monotonic() - t0 < 8.0 and len(son.connections) < 2:
                time.sleep(0.02)
            assert len(son.connections) >= 2, "config 帧卡死该被看门狗发现并重连"
            sess = s.metrics()["sessions"]
            assert sess and sess[0]["detail"].startswith("send stuck"), sess
        finally:
            unblock.set()
            s.stop()
            son.close()
            wk.close()


def t_transport_keepalive_send_stuck_covered() -> None:
    """⭐ 静音时 keepalive 那条 `ws.send` 卡死 → 也要被看门狗救（MUT8 曾漏过）。"""
    import websockets.sync.client as wsc
    real_connect = wsc.connect
    unblock = threading.Event()
    calls = {"n": 0}

    def connect(*a, **kw):
        ws = real_connect(*a, **kw)
        calls["n"] += 1
        if calls["n"] == 1:
            real_send, real_close = ws.send, ws.close_socket

            def send(data, *aa, **kk):
                if isinstance(data, str) and data and "keepalive" in data:
                    unblock.wait(30.0)
                    raise OSError("socket closed under a blocked keepalive send")
                return real_send(data, *aa, **kk)

            def close_socket():
                unblock.set()
                real_close()
            ws.send, ws.close_socket = send, close_socket
        return ws

    son = FakeSoniox()
    wk = FakeWorker(son.url)
    with mock.patch.object(wsc, "connect", connect):
        # keepalive_s 小 → 不喂音频也会很快走到那条 send
        s = make_asr(wk, son, response_timeout_s=60.0, send_stuck_s=0.6,
                     keepalive_s=0.2, retry_delays_s=(0.0,))
        try:
            t0 = time.monotonic()
            while time.monotonic() - t0 < 10.0 and len(son.connections) < 2:
                time.sleep(0.02)
            assert len(son.connections) >= 2, "keepalive 发送卡死该被看门狗发现并重连"
            sess = s.metrics()["sessions"]
            assert sess and sess[0]["detail"].startswith("send stuck"), sess
        finally:
            unblock.set()
            s.stop()
            son.close()
            wk.close()


def t_transport_watchdog_thread_does_not_leak() -> None:
    """⭐ 每次会话结束看门狗线程都要退出（MUT9 曾漏过：`wd_stop` 不置位 → 每次重连漏一条线程）。"""
    son = FakeSoniox()
    wk = FakeWorker(son.url)
    before = sum(1 for t in threading.enumerate() if t.name == "stream-asr-wd")
    # ⚠️ 退避表要给够：只有一条的话第二轮掉线就直接退回本地，测不出「多次重连后的线程数」
    s = make_asr(wk, son, retry_delays_s=(0.0, 0.0, 0.0, 0.0))
    try:
        assert wait_until(s.alive)
        for want in (2, 3):                                   # 逼它掉线重连两轮（每轮等新连接真的起来）
            son.to_send.put(("close", 1011, "test"))          # 假服务端主动关：走正常重连
            t1 = time.monotonic()
            while time.monotonic() - t1 < 5.0 and len(son.connections) < want:
                time.sleep(0.02)
            assert len(son.connections) >= want, f"没重连起来就是空转：{len(son.connections)}"
            assert wait_until(s.alive, 3.0), "重连后该重新 alive"
        s.stop()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            alive = sum(1 for t in threading.enumerate() if t.name == "stream-asr-wd")
            if alive <= before:
                return
            time.sleep(0.1)
        alive = sum(1 for t in threading.enumerate() if t.name == "stream-asr-wd")
        raise AssertionError(f"看门狗线程泄漏：开始 {before} 条，结束还剩 {alive} 条")
    finally:
        s.stop()
        son.close()
        wk.close()


def t_transport_drop_log_goes_to_stdout() -> None:
    """⭐ 每次掉线往 stdout 打一行 —— 这是普通上课（非测试模式）唯一的现场线索。

    `sessions[]` 只进 `blocks.stream` 与 `.live.jsonl`，两者平时都关；stdout 进 `app.log`
    （双击 `.app` 时改道），而 `app.log` 本来就在诊断包里。10-29 那节课手上什么都没有，
    只能拿草稿时间戳反推 —— 这一条就是为那种时候留的。
    """
    import io
    from contextlib import redirect_stdout

    son = FakeSoniox()
    wk = FakeWorker(son.url)
    buf = io.StringIO()
    s = make_asr(wk, son, retry_delays_s=(0.0, 0.0), drop_log="DROP {ran}s {why}{detail} #{n}")
    try:
        with redirect_stdout(buf):
            assert wait_until(s.alive)
            son.to_send.put(("close", 1011, "x"))
            t0 = time.monotonic()
            while time.monotonic() - t0 < 5.0 and not buf.getvalue().strip():
                time.sleep(0.02)
    finally:
        s.stop()
        son.close()
        wk.close()
    out = buf.getvalue()
    assert "DROP" in out, f"掉线没有打到 stdout：{out!r}"
    assert "#1" in out and "disconnected" in out, out
    assert "1011" in out, f"close code 该带上：{out!r}"


def t_transport_backoff_has_jitter() -> None:
    """退避带抖动：抖动取下限时，第二次重连比基准退避（1.0 s）早。"""
    son = FakeSoniox()
    wk = FakeWorker(son.url)
    with mock.patch("stream_asr.random.uniform", return_value=-0.5):
        s = make_asr(wk, son, retry_delays_s=(0.0, 1.0), retry_jitter=0.5)
        try:
            assert wait_until(s.alive)
            son.to_send.put(("close", 1011, "a"))
            assert wait_until(lambda: len(son.connections) >= 2 and s.alive())
            t0 = time.monotonic()
            son.to_send.put(("close", 1011, "b"))
            assert wait_until(lambda: len(son.connections) >= 3 and s.alive(), 5.0)
            waited = time.monotonic() - t0
            assert 0.3 <= waited < 0.95, f"抖动下限该把 1.0 s 退避压到 ~0.5 s，实测 {waited:.2f}"
        finally:
            s.stop()
            son.close()
            wk.close()


def t_transport_sessions_log_per_drop() -> None:
    """每次掉线都留一条：开始时刻 / 跑了多久 / 原因 / close code（以前只剩最后一次）。"""
    son = FakeSoniox()
    wk = FakeWorker(son.url)
    s = make_asr(wk, son, retry_delays_s=(0.0, 0.0, 0.0))
    try:
        assert wait_until(s.alive)
        for n, code in ((2, 1011), (3, 1012)):
            time.sleep(0.15)
            son.to_send.put(("close", code, f"x{n}"))
            assert wait_until(lambda n=n: len(son.connections) >= n and s.alive(), 5.0)
        sess = s.metrics()["sessions"]
        assert len(sess) == 2, sess
        assert [x["why"] for x in sess] == ["disconnected", "disconnected"], sess
        assert "1011" in sess[0]["detail"] and "1012" in sess[1]["detail"], sess
        assert all(x["ran_s"] > 0.1 for x in sess), sess
    finally:
        s.stop()
        son.close()
        wk.close()


def t_retry_policy_constants_fit_the_incident() -> None:
    """钉住 10-29 那节课的形状：出词窗只有 36–343 s、掉线频繁 → 预算必须扛得住、还原不能要 10 分钟。
    窗间空档要短（< 1 分钟就接回），且长期重连不能放弃得太早。"""
    d = stream_asr.RETRY_DELAYS_S
    assert d[0] == 0.0 and d[1] <= 5.0, "头两次重连必须几乎立刻（原表 0/30 s 是 57 分钟全本地的原因）"
    assert max(d) <= 30.0, "单次退避封顶 30 s（同行惯例）"
    assert list(d) == sorted(d), "退避只增不减"
    assert sum(d) >= 20 * 60, f"连续失败至少坚持 20 分钟再放弃，实际 {sum(d):.0f}s"
    assert stream_asr.RETRY_RESET_S <= 120.0, "会话跑过 ≤2 分钟就该还原预算（原 600 s 窗口永远够不着）"
    assert stream_asr.RESPONSE_TIMEOUT_S <= 10.0, "半开连接最多白等 ~10 s"


def t_transport_stop_interrupts_backoff() -> None:
    """退避等待中下课：`stop()` 不能被 30 s 的等待拖住。"""
    son = FakeSoniox()
    wk = FakeWorker(son.url)
    s = make_asr(wk, son, retry_delays_s=(0.0, 30.0))
    try:
        assert wait_until(s.alive)
        son.to_send.put(("close", 1011, "a"))
        assert wait_until(lambda: len(son.connections) >= 2 and s.alive())
        son.to_send.put(("close", 1011, "b"))
        assert wait_until(lambda: not s.alive(), 3.0)
        t0 = time.monotonic()
        s.stop()
        assert time.monotonic() - t0 < 5.0, "stop() 被退避等待拖住了"
        assert s.reason() == "", s.reason()
    finally:
        son.close()
        wk.close()


def t_transport_close_code_in_detail() -> None:
    """退回时 `fallback_detail` 带服务端的 close code + 原因（10-23 那次是空的）。"""
    son = FakeSoniox()
    wk = FakeWorker(son.url)
    s = make_asr(wk, son, retry_delays_s=())
    try:
        assert wait_until(s.alive)
        son.to_send.put(("close", 1011, "boom"))
        assert wait_until(lambda: s.reason() != "", 8.0)
        m = s.metrics()
        assert m["fallback_reason"] == "disconnected", m
        assert "1011" in m["fallback_detail"], m["fallback_detail"]
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
    s = make_asr(wk, son, retry_delays_s=(), response_timeout_s=0.5, keepalive_s=2.0, stable_s=0.3)
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


def t_transport_stall_with_continuous_audio_times_out() -> None:
    """⭐ 2026-10-21：**持续喂音频**、服务器一声不吭 → 必须判死。

    旧判据：`now - max(got, last_audio, last_send) > timeout`；`last_audio` 每取一块
    音频就刷新 → 有音频时 `max` 恒 ≈ now，**永不触发**。10-20 ECON10740 就是这样
    静默了 2m41s 而没有任何日志。这里用「一直喂」把那个形状钉住。
    """
    son = FakeSoniox()
    wk = FakeWorker(son.url)
    s = make_asr(wk, son, retry_delays_s=(), response_timeout_s=0.5, keepalive_s=2.0, stable_s=0.3)
    try:
        assert wait_until(s.alive)
        t0 = time.monotonic()
        while time.monotonic() - t0 < 1.2:        # 超过 response_timeout 仍在喂
            s.feed(silence(SR // 10))
            time.sleep(0.02)
            if s.reason() == "timeout":
                break
        # ⚠️ 必须在**还在喂的时候**就判死；等停止喂再判会测到旧判据的「静音超时」那条路
        assert s.reason() == "timeout", f"持续喂了 1.2s 仍未判死: {s.reason()!r}"
        assert not s.alive()
    finally:
        s.stop()
        son.close()
        wk.close()


def t_transport_progress_messages_keep_alive() -> None:
    """静音期只要服务端**回进度消息**，就不许判死（Soniox 实测 ~1 s 一条）。"""
    son = FakeSoniox()
    wk = FakeWorker(son.url)
    s = make_asr(wk, son, response_timeout_s=0.5, keepalive_s=2.0, stable_s=0.3)
    try:
        assert wait_until(s.alive)
        t0 = time.monotonic()
        while time.monotonic() - t0 < 1.4:
            son.to_send.put({"tokens": [], "total_audio_proc_ms": 0})
            time.sleep(0.15)
            assert s.reason() == "", f"有进度消息却判死了: {s.reason()!r}"
    finally:
        s.stop()
        son.close()
        wk.close()


def t_transport_zero_progress_after_reconnect_not_stale() -> None:
    """重连后服务端 `total_audio_proc_ms=0` 是正常的，**不许**因此再判 stale。

    OCR 2026-10-21 抓到的边界：旧实现用 `_proc_abs_ms = total + _sess_off_ms`，重连后
    `_sess_off_ms > 0`，`total=0` 也会让绝对守卫放行 → 新会话推 3 s 音频就误重连。
    现在两个量都按**会话相对**算，`total=0` 时守卫为假。
    """
    son = FakeSoniox()
    wk = FakeWorker(son.url)
    s = make_asr(wk, son, response_timeout_s=5.0, keepalive_s=2.0,
                 backlog_s=0.5, backlog_hold_s=0.2, stable_s=0.3)
    try:
        assert wait_until(s.alive)
        s.feed(silence(SR))                       # 第一段 1 s，建立 _sess_off_ms=1000
        assert wait_until(lambda: son.connections and son.connections[0]["audio"] >= SR)
        son.to_send.put(("close", 1011, "boom"))
        assert wait_until(lambda: len(son.connections) >= 2, 5.0), "该重连"
        son.to_send.put({"tokens": [], "total_audio_proc_ms": 0})
        s.feed(silence(SR * 2))                   # 新会话 2 s；若用绝对守卫会误判 stale
        time.sleep(0.8)
        assert s.reason() == "", f"total=0 被误判 stale: {s.reason()!r}"
        assert len(son.connections) == 2, \
            f"total=0 被误判 stale，连接数={len(son.connections)}"
    finally:
        s.stop()
        son.close()
        wk.close()


def t_transport_backlog_reconnects_to_live() -> None:
    """服务端进度落后已发送音频 → `stale`，重连并把积压丢掉（跳到实时）。"""
    son = FakeSoniox()
    wk = FakeWorker(son.url)
    s = make_asr(wk, son, response_timeout_s=5.0, keepalive_s=2.0,
                 backlog_s=0.5, backlog_hold_s=0.2, stable_s=0.3)
    try:
        assert wait_until(s.alive)
        s.feed(silence(SR * 2))                   # 2 s 音频进队列
        assert wait_until(lambda: son.connections and son.connections[0]["audio"] >= SR * 2)
        # 服务端只处理到 0.5 s -> 落后 1.5 s，超过 0.5 s 门槛（>0 才判，挡开流误报）
        son.to_send.put({"tokens": [], "total_audio_proc_ms": 500})
        assert wait_until(lambda: s.metrics()["backlog_max_s"] > 0.5, 2.0)
        # 第二条**新读数**仍然落后（判据要求 hold 期间有新消息确认，不能拿冻结值等时间）
        son.to_send.put({"tokens": [], "total_audio_proc_ms": 500})
        assert wait_until(lambda: len(son.connections) >= 2, 5.0), \
            f"落后 1.5 s / 门槛 0.5 s 该重连，实际连接数={len(son.connections)}"
        assert s.metrics()["reconnects"] >= 1
    finally:
        s.stop()
        son.close()
        wk.close()


def t_transport_backlog_caught_up_no_stale() -> None:
    """落后读数之后服务端追平：**不许**因为「时间过去了」就判 stale。

    OCR 2026-10-21 第 3 条：`_proc_rel_ms` 只在消息到达时更新，循环每轮拿同一组
    冻结值重算 lag。这里把 hold 设得比下一条消息早，验证没有新读数时不会动作；
    下一条 `total=2000` 追平后必须清零。
    """
    son = FakeSoniox()
    wk = FakeWorker(son.url)
    s = make_asr(wk, son, response_timeout_s=5.0, keepalive_s=2.0,
                 backlog_s=0.5, backlog_hold_s=0.1, zero_proc_timeout_s=15.0,
                 stable_s=0.3)
    try:
        assert wait_until(s.alive)
        s.feed(silence(SR * 2))
        assert wait_until(lambda: son.connections and son.connections[0]["audio"] >= SR * 2)
        son.to_send.put({"tokens": [], "total_audio_proc_ms": 500})
        assert wait_until(lambda: s.metrics()["backlog_max_s"] > 0.5, 2.0)
        time.sleep(0.25)                          # 超过 hold，但没有新读数 -> 不该动作
        assert s.reason() == "", f"没有新读数就判 stale: {s.reason()!r}"
        son.to_send.put({"tokens": [], "total_audio_proc_ms": 2000})   # 追平
        time.sleep(0.6)
        assert s.reason() == ""
        assert s.metrics()["reconnects"] == 0, "追平后不该重连"
    finally:
        s.stop()
        son.close()
        wk.close()


def t_transport_zero_proc_stuck_reconnects() -> None:
    """服务端一直回 `total=0`（消息在来但不处理音频）→ 必须判 stale。

    OCR 2026-10-21 第 2 条：`got` 被持续刷新、`total=0` 又被 `>0` 守卫跳过，
    两端都判不出来。补 `_zero_proc_since` 兜底。
    """
    son = FakeSoniox()
    wk = FakeWorker(son.url)
    s = make_asr(wk, son, retry_delays_s=(), response_timeout_s=5.0, keepalive_s=2.0,
                 backlog_s=0.5, backlog_hold_s=0.2, zero_proc_timeout_s=0.5,
                 stable_s=0.3)
    try:
        assert wait_until(s.alive)
        t0 = time.monotonic()
        while time.monotonic() - t0 < 3.0:
            s.feed(silence(SR // 10))
            son.to_send.put({"tokens": [], "total_audio_proc_ms": 0})
            time.sleep(0.1)
            if s.reason():
                break
        assert s.reason() == "stale", f"卡在 total=0 未判死: {s.reason()!r}"
    finally:
        s.stop()
        son.close()
        wk.close()


def t_transport_zero_proc_frozen_reading_no_stale() -> None:
    """`total=0` 之后消息**不带** total：0 是冻结读数，不许因此判 stale。

    OCR 第三轮第 1 条：`_recv_loop` 允许 `tot=None`；如果只拿上一条 0 读数等时间，
    后续缺字段的消息会让 0 永远不更新 → 15s 后误判。起计时也要记序号。
    """
    son = FakeSoniox()
    wk = FakeWorker(son.url)
    s = make_asr(wk, son, response_timeout_s=5.0, keepalive_s=2.0,
                 backlog_s=0.5, backlog_hold_s=0.2, zero_proc_timeout_s=0.5,
                 stable_s=0.3)
    try:
        assert wait_until(s.alive)
        s.feed(silence(SR))
        assert wait_until(lambda: son.connections and son.connections[0]["audio"] >= SR)
        son.to_send.put({"tokens": [], "total_audio_proc_ms": 0})
        time.sleep(0.15)
        t0 = time.monotonic()
        while time.monotonic() - t0 < 1.0:
            son.to_send.put({"tokens": []})        # 没有 total 字段
            s.feed(silence(SR // 10))
            time.sleep(0.1)
        assert s.reason() == "", f"冻结的 0 读数被误判: {s.reason()!r}"
        assert s.metrics()["reconnects"] == 0
    finally:
        s.stop()
        son.close()
        wk.close()


def t_transport_normal_reconnect_stale_retries_live() -> None:
    """普通断线续传后仍 stale：允许再跳一次实时，不要直接退回本地。

    OCR 2026-10-21 第 1 条：重连本身慢时队列积压会让第二次会话判 stale，
    而 `_retried` 已用尽 → 整节课退回本地。这里给「disconnected → stale」
    多一次 `skip` 重连（最多第三次）。
    """
    son = FakeSoniox()
    wk = FakeWorker(son.url)
    s = make_asr(wk, son, response_timeout_s=5.0, keepalive_s=2.0,
                 backlog_s=0.5, backlog_hold_s=0.2, zero_proc_timeout_s=15.0,
                 stable_s=0.3)
    try:
        assert wait_until(s.alive)
        s.feed(silence(SR))
        assert wait_until(lambda: son.connections and son.connections[0]["audio"] >= SR)
        son.to_send.put(("close", 1011, "boom"))
        assert wait_until(lambda: len(son.connections) >= 2 and s.alive(), 5.0)
        s.feed(silence(SR * 2))
        assert wait_until(lambda: son.connections[1]["audio"] >= SR * 2, 5.0)
        son.to_send.put({"tokens": [], "total_audio_proc_ms": 500})
        assert wait_until(lambda: s.metrics()["backlog_max_s"] > 0.5, 2.0)
        son.to_send.put({"tokens": [], "total_audio_proc_ms": 500})
        assert wait_until(lambda: len(son.connections) >= 3, 5.0), \
            "普通断线续传后 stale 应再跳一次，而不是直接退回本地"
        assert s.reason() == "", f"不该退回: {s.reason()!r}"
    finally:
        s.stop()
        son.close()
        wk.close()


def t_transport_network_then_stale_retries_live() -> None:
    """建连失败（network）→ 续传后 stale：也要允许再跳一次，不要直接退回本地。

    OCR 第三轮第 3 条：首因是 `network`（key/WS 建连失败）时，旧条件只认
    `disconnected`，第二次 stale 会直接 return。这里用「第一次返回坏 ws_url」复现。
    """
    son = FakeSoniox()

    class BadThenGood(FakeWorker):
        def __init__(self, ws_url: str) -> None:
            self.n = 0
            super().__init__(ws_url)

        def reply(self):
            self.n += 1
            if self.n == 1:
                return 200, {"api_key": "fake-key", "ws_url": "ws://127.0.0.1:1/",
                             "region": "us", "expires_in_seconds": 60,
                             "max_session_s": 14400}
            return super().reply()

    wk = BadThenGood(son.url)
    s = make_asr(wk, son, response_timeout_s=5.0, keepalive_s=2.0,
                 backlog_s=0.5, backlog_hold_s=0.2, zero_proc_timeout_s=15.0,
                 stable_s=0.3)
    try:
        assert wait_until(s.alive, 8.0), f"第一次建连失败后该重连成功: {s.reason()!r}"
        s.feed(silence(SR * 2))
        assert wait_until(lambda: son.connections and son.connections[0]["audio"] >= SR * 2)
        son.to_send.put({"tokens": [], "total_audio_proc_ms": 500})
        assert wait_until(lambda: s.metrics()["backlog_max_s"] > 0.5, 2.0)
        son.to_send.put({"tokens": [], "total_audio_proc_ms": 500})
        assert wait_until(lambda: len(son.connections) >= 2, 5.0), \
            "network → stale 也应再跳一次，而不是直接退回本地"
        assert s.reason() == "", f"不该退回: {s.reason()!r}"
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
    # ⚠️ 故意用默认 `_chunk_s=0.12`，但每块喂 0.1 s —— 生产也是这个错位
    #    （capture.CHUNK=0.1 s，模块默认 0.12）。`dropped_s` 必须按样本数算。
    s = make_asr(wk, son, audio_max_s=0.5)                  # 队列上限约 4 块
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
        m = s.metrics()
        assert m["dropped_chunks"] == s.dropped()
        # 每块 0.1 s：按样本数算的秒数应跟块数一致（不能拿 _chunk_s=0.12 估）
        assert abs(m["dropped_s"] - s.dropped() * 0.1) < 0.011, m
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
        ("transport: 只有 error_code 的致命错误也不重连", t_transport_fatal_error_code_only_no_retry),
        ("transport: 缺 websockets → error + 异常类型", t_transport_missing_websockets_records_detail),
        ("vendor: 没装 websockets 用自带那份、真能连上", t_vendor_websockets_fallback_connects),
        ("transport: 暗号手动退回（manual）", t_transport_force_local_manual),
        ("transport: 余额耗尽 → 不重连直接退回", t_transport_balance_exhausted_falls_back_now),
        ("transport: 断线重连一次", t_transport_reconnect_once),
        ("transport: 封过句之后重连还能接着封", t_transport_reconnect_after_sealing_keeps_sealing),
        ("transport: 重连也失败 → 退回", t_transport_reconnect_fails_fallback),
        ("transport: 第二次掉线仍重连", t_transport_second_drop_still_reconnects),
        ("transport: 重连前按退避等待", t_transport_backoff_waits_between_retries),
        ("transport: 稳定跑过后预算还原", t_transport_budget_resets_after_stable_session),
        ("transport: 发送卡死 → 看门狗重连", t_transport_send_stuck_watchdog_reconnects),
        ("transport: config 帧卡死 → 看门狗重连", t_transport_config_send_stuck_covered),
        ("transport: keepalive 卡死 → 看门狗重连", t_transport_keepalive_send_stuck_covered),
        ("transport: 看门狗线程不泄漏", t_transport_watchdog_thread_does_not_leak),
        ("transport: 退避带抖动", t_transport_backoff_has_jitter),
        ("transport: 掉线打到 stdout", t_transport_drop_log_goes_to_stdout),
        ("transport: 每次掉线记一条会话日志", t_transport_sessions_log_per_drop),
        ("transport: 退避常量贴合 10-29 形状", t_retry_policy_constants_fit_the_incident),
        ("transport: stop() 打断退避", t_transport_stop_interrupts_backoff),
        ("transport: 退回详情带 close code", t_transport_close_code_in_detail),
        ("transport: 领 key 429 → 本地", t_transport_key_429_is_local),
        ("transport: 没通行码 → 本地", t_transport_no_passcode),
        ("transport: 15 s 无响应判死", t_transport_stall_times_out),
        ("transport: 持续喂音频也必须判死", t_transport_stall_with_continuous_audio_times_out),
        ("transport: 进度消息保活不误判", t_transport_progress_messages_keep_alive),
        ("transport: 重连后 total=0 不误判", t_transport_zero_progress_after_reconnect_not_stale),
        ("transport: 进度落后 → 重连跳实时", t_transport_backlog_reconnects_to_live),
        ("transport: 追平后不许判 stale", t_transport_backlog_caught_up_no_stale),
        ("transport: total=0 卡死 → stale", t_transport_zero_proc_stuck_reconnects),
        ("transport: 冻结的 total=0 不误判", t_transport_zero_proc_frozen_reading_no_stale),
        ("transport: 普通断线后 stale 再跳一次", t_transport_normal_reconnect_stale_retries_live),
        ("transport: network 后 stale 再跳一次", t_transport_network_then_stale_retries_live),
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
