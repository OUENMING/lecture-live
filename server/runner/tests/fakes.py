# fakes.py —— 本机假 Worker（http.server 线程），给 client / loop 测试用。
# 管什么：契约 §3 六条 runner 路由的最小实现 + 请求计数 / 可编程的 5xx 与心跳状态码。
# 对外接口：`FakeWorker(token)` → `.base` / `.stop()`；队列 `.jobs`、记录 `.completes`
#   `.heartbeats` `.summaries`、逐句仓 `.lines_store[(hash, session_id)]`。
# 不 import 任何桌面模块（client 测试不碰 HOME）。
from __future__ import annotations

import json
import threading
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


class _Handler(BaseHTTPRequestHandler):
    @property
    def fw(self):
        return self.server.fw

    def log_message(self, *a):                     # 静音：测试自己的断言才是账
        pass

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        try:
            return json.loads(raw or b"{}")
        except Exception:                          # noqa: BLE001
            return {}

    def _send(self, status, obj=None):
        raw = json.dumps(obj).encode() if obj is not None else b""
        self.send_response(status)
        if obj is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        if raw:
            self.wfile.write(raw)

    def _authed(self):
        if self.headers.get("Authorization") != f"Bearer {self.fw.token}":
            self._send(401, {"error": "unauthorized"})
            return False
        return True

    def _maybe_fail(self, method) -> bool:
        """可编程 5xx：`fw.fail_next[(method, path 前缀)] = 次数`。命中返回 True（已回 500）。"""
        with self.fw.lock:
            for (m, pre), left in list(self.fw.fail_next.items()):
                if m == method and self.path.startswith(pre) and left > 0:
                    self.fw.fail_next[(m, pre)] = left - 1
                    self._send(500, {"error": "boom"})
                    return True
        return False

    def do_POST(self):
        if not self._authed():
            return
        u = urlparse(self.path)
        with self.fw.lock:
            self.fw.counts[("POST", u.path)] += 1
        if self._maybe_fail("POST"):
            return
        b = self._body()
        if u.path == "/v1/runner/claim":
            with self.fw.lock:
                job = self.fw.jobs.pop(0) if self.fw.jobs else None
            if job is None:
                self._send(204)
            else:
                self._send(200, job)
        elif u.path == "/v1/runner/heartbeat":
            with self.fw.lock:
                self.fw.heartbeats.append(b)
                code = self.fw.hb_code
            if code == 200:
                self._send(200, {"lease_until": "2026-11-20T12:00:30Z"})
            else:
                self._send(code, {"error": self.fw.hb_error or "lease_mismatch"})
        elif u.path == "/v1/runner/complete":
            with self.fw.lock:
                self.fw.completes.append(b)
            self._send(200, {"job_id": b.get("job_id"),
                             "state": "done" if b.get("ok") else "queued",
                             "attempt": b.get("attempt")})
        else:
            self._send(404, {"error": "not_found"})

    def do_GET(self):
        if not self._authed():
            return
        u = urlparse(self.path)
        with self.fw.lock:
            self.fw.counts[("GET", u.path)] += 1
        if self._maybe_fail("GET"):
            return
        if u.path == "/v1/runner/lines":
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            key = (q.get("hash") or "", q.get("session_id") or "")
            with self.fw.lock:
                rows = self.fw.lines_store.get(key)
                gone = key in self.fw.deleted
            if gone:
                self._send(410, {"error": "session_deleted"})
            elif rows is None:
                self._send(404, {"error": "not_found"})
            else:
                after = int(q.get("after") or 0)
                raw = ("\n".join(json.dumps(r) for r in rows
                                 if int(r.get("seq") or 0) > after) + "\n").encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
        elif u.path == "/v1/runner/live":
            with self.fw.lock:
                sess = list(self.fw.live_sessions)
            self._send(200, {"sessions": sess})
        else:
            self._send(404, {"error": "not_found"})

    def do_PUT(self):
        if not self._authed():
            return
        u = urlparse(self.path)
        if self._maybe_fail("PUT"):
            return
        if u.path == "/v1/runner/summary":
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            with self.fw.lock:
                self.fw.summaries.append((q, self._body()))
            self._send(204)
        else:
            self._send(404, {"error": "not_found"})


class FakeWorker:
    """起在本机随机端口的假 Worker。用完 `stop()`。"""

    def __init__(self, token="rn_test_token"):
        self.token = token
        self.lock = threading.Lock()
        self.jobs: list = []
        self.hb_code, self.hb_error = 200, ""
        self.completes: list = []
        self.heartbeats: list = []
        self.summaries: list = []
        self.lines_store: dict = {}
        self.deleted: set = set()
        self.live_sessions: list = []
        self.fail_next: dict = {}
        self.counts: Counter = Counter()
        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._srv.fw = self
        self._t = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self._t.start()

    @property
    def base(self):
        return f"http://127.0.0.1:{self._srv.server_address[1]}"

    def stop(self):
        self._srv.shutdown()
        self._srv.server_close()


def sample_job(job_id="j1", *, hash_="0123456789abcdef", session_id="s_1",
               course="ECON10770", level="normal", attempt=1):
    """契约 §3 claim 回包形状。"""
    return {"job_id": job_id, "kind": "note", "attempt": attempt, "lease": "L1",
            "lease_until": "2026-11-20T12:00:00Z",
            "input": {"hash": hash_, "session_id": session_id, "course": course,
                      "tz": "Europe/Dublin", "started_at": "2026-11-20T09:00:00Z",
                      "ended_at": "2026-11-20T10:00:00Z", "lines_total": 2,
                      "level": level}}


def sample_lines():
    return [{"seq": 1, "t": 3, "en": "Price elasticity of demand.", "zh": "需求的价格弹性。",
             "src": "asr"},
            {"seq": 2, "t": 61, "en": "它衡量数量对价格变化的敏感程度。", "zh": "",
             "src": "asr"}]
