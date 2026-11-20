# RunnerClient —— 跑者 ↔ Worker 的唯一 HTTP 出口（契约 §7，签名一字不差，W24 并行在用）。
# 管什么：claim / heartbeat / complete / lines / live / put_summary 六条 runner 路由（契约 §3）。
# 异常映射：heartbeat 404/409/410 → LeaseLost；lines 404/410 → SessionGone；
#   其余 4xx → WorkerError（不重试）；5xx 与网络错误重试 2 次（退避 1 s、3 s）。
# 为什么默认 httpx.request：一次性请求、无连接池常驻 —— runner 每 5 s 才醒一次，
#   池子的 keepalive 反而是负担；`http=` 参数留给测试注入假 request。
from __future__ import annotations

import json
import os
import socket
import time

import httpx


class LeaseLost(Exception):
    """租约丢了（heartbeat 404/409/410）：立刻停手、不许交回。"""


class SessionGone(Exception):
    """课不存在 / 已删（lines 404/410）：拉不到逐句，只能 fail。"""


class WorkerError(Exception):
    """Worker 回的 4xx（不重试）；`args[0]` 是 (status, code)。"""


#: 重试退避（秒）：5xx / 网络错 → 睡 1 s、3 s 再各试一次，共 3 次尝试。
_BACKOFF = (1.0, 3.0)


def _sleep(s: float) -> None:
    time.sleep(s)          # 测试 monkeypatch 这里，别真等


class RunnerClient:
    def __init__(self, base: str, token: str, *, timeout: float = 30.0, http=None):
        self._base = (base or "").rstrip("/")
        self._token = token
        self._timeout = timeout
        self._req = http or httpx.request
        # claim body 里要报「谁在领」（契约 §3）；名字不进构造签名 —— 测试/别的调用方
        # 可以直接改这个属性。
        self.name = os.environ.get("RUNNER_NAME") or socket.gethostname()

    # ---- 基础件 ----
    def _request(self, method: str, path: str, *, params=None, body=None):
        """一个请求 → (status, bytes)。5xx/网络错重试；4xx 原样回不重试。"""
        last: Exception | None = None
        for i in range(len(_BACKOFF) + 1):
            try:
                r = self._req(method, self._base + path, params=params, json=body,
                              headers={"Authorization": f"Bearer {self._token}"},
                              timeout=self._timeout)
                if r.status_code >= 500 and i < len(_BACKOFF):
                    last = WorkerError((r.status_code, r.text[:200]))
                    _sleep(_BACKOFF[i])
                    continue
                return r.status_code, r.content
            except httpx.HTTPError as e:        # 网络 / 超时（含 ConnectError）
                last = e
                if i < len(_BACKOFF):
                    _sleep(_BACKOFF[i])
                    continue
                raise
        raise last if last else RuntimeError("unreachable")

    def _json(self, method: str, path: str, *, params=None, body=None) -> dict:
        status, raw = self._request(method, path, params=params, body=body)
        if status >= 400:
            try:
                code = str(json.loads(raw).get("error") or "")
            except Exception:                    # noqa: BLE001
                code = ""
            raise WorkerError((status, code or f"http_{status}"))
        return json.loads(raw) if raw else {}

    # ---- 契约 §3 六条路由 ----
    def claim(self, kinds=("note",)) -> dict | None:
        status, raw = self._request(
            "POST", "/v1/runner/claim",
            body={"runner": self.name, "kinds": list(kinds)})
        if status == 204:
            return None
        if status >= 400:
            try:
                code = str(json.loads(raw).get("error") or "")
            except Exception:                    # noqa: BLE001
                code = ""
            raise WorkerError((status, code or f"http_{status}"))
        return json.loads(raw)

    def heartbeat(self, job: dict) -> str:
        status, raw = self._request(
            "POST", "/v1/runner/heartbeat",
            body={"job_id": job["job_id"], "attempt": job["attempt"],
                  "lease": job["lease"]})
        if status in (404, 409, 410):
            try:
                code = str(json.loads(raw).get("error") or "")
            except Exception:                    # noqa: BLE001
                code = ""
            raise LeaseLost(f"{status} {code or f'http_{status}'}: "
                            f"{job.get('job_id')}")
        if status >= 400:
            raise WorkerError((status, f"http_{status}"))
        return str(json.loads(raw).get("lease_until") or "")

    def complete_ok(self, job: dict, md: str, html: str, meta: dict) -> dict:
        return self._json("POST", "/v1/runner/complete",
                          body={"job_id": job["job_id"], "attempt": job["attempt"],
                                "lease": job["lease"], "ok": True,
                                "note": {"md": md, "html": html},
                                "meta": dict(meta or {})})

    def complete_fail(self, job: dict, reason: str) -> dict:
        return self._json("POST", "/v1/runner/complete",
                          body={"job_id": job["job_id"], "attempt": job["attempt"],
                                "lease": job["lease"], "ok": False,
                                "reason": str(reason)[:1000]})

    def lines(self, hash: str, session_id: str, after: int = 0) -> list:
        status, raw = self._request(
            "GET", "/v1/runner/lines",
            params={"hash": hash, "session_id": session_id, "after": int(after)})
        if status in (404, 410):
            raise SessionGone(f"{status} {hash}/{session_id}")
        if status >= 400:
            raise WorkerError((status, f"http_{status}"))
        text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        return [json.loads(x) for x in text.splitlines() if x.strip()]

    def live(self) -> list:
        return list(self._json("GET", "/v1/runner/live").get("sessions") or [])

    def put_summary(self, hash: str, session_id: str, doc: dict) -> None:
        status, _ = self._request(
            "PUT", "/v1/runner/summary",
            params={"hash": hash, "session_id": session_id}, body=doc)
        if status >= 400:
            raise WorkerError((status, f"http_{status}"))
        return None
