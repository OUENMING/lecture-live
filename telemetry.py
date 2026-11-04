"""元数据脊柱：**每个工作单元一条宽事件**，课后还能回答「没预想到的故障」。

为什么要有它（`docs/PLAN-telemetry-20261103.md` §0.5）：`report.json` 是课后聚合，
只能回答已经想到的问题。想知道「用户那边正在发生、我们还没想到」的事，必须在边界上
记**带因果 id 的宽事件**，并且崩溃前留一口气（心跳 + 下次启动补记）。

## 三条纪律

1. **绝不阻塞主流程**：写盘在后台线程；有界队列满了丢**最旧**。音频回调 / 收尾
   任何一处都不许因为遥测卡住（同 `capture` / `vad` 的「回调立刻返回」）。
2. **元数据与内容分文件**：`events.jsonl` 只放数字 / 枚举 / 短串；逐句原文、译文、
   笔记块进 `content.jsonl`（Owen 2026-11-03 已授权全量，两份分开是为了能按需只留小的）。
3. **永不抛**：所有公开方法都兜住异常。遥测坏了 = 这一节少数据，不是课出事。

## 文件

`~/.classlive/telemetry/`
  · `<session_id>.events.jsonl`   append-only，元数据
  · `<session_id>.content.jsonl`  append-only，内容（默认开）
  · `heartbeat.json`              每 15 s 原子替换；下次启动据此判「上节没正常结束」

## 恢复

`start()` 会读 `heartbeat.json`：上一节没有 `session.end` / boot 变了 / 心跳超过
`STALE_S` → 在新会话里补一条 `session.recovered`（`outcome="abnormal"`，带最后
`seq` 与心跳年龄）。崩溃 / 强杀 / 断电都走这一条。
"""
from __future__ import annotations

import json
import os
import pathlib
import platform
import queue
import re
import secrets
import subprocess
import sys
import threading
import time
import uuid

import paths

SCHEMA_VERSION = 1
#: 元数据队列上限。满了丢最旧 —— 宁可少几条 breadcrumb，也不许顶住主线程。
MAX_QUEUE = 2000
#: 心跳间隔 / 判「上一节没结束」的年龄。
HEARTBEAT_S = 15.0
STALE_S = 60.0
#: 单条事件的 attrs 上限（深拷贝前的闸）；内容事件单独放宽。
_MAX_STR = 200
_MAX_KEYS = 40
_MAX_DEPTH = 2

#: 进程内唯一实例（调用方用模块级 `emit/content/heartbeat`，不必传对象）。
_CUR: "Telemetry | None" = None
_CUR_LOCK = threading.Lock()

_BOOT_ID: str | None = None


def _boot_id() -> str:
    """本次开机 id（`kern.boottime` 的秒）；拿不到 = ""（判据退回心跳年龄）。"""
    global _BOOT_ID
    if _BOOT_ID is not None:
        return _BOOT_ID
    out = ""
    try:
        raw = subprocess.run(["sysctl", "-n", "kern.boottime"], capture_output=True,
                             text=True, timeout=0.5).stdout or ""
        m = re.search(r"sec\s*=\s*(\d+)", raw)
        if m:
            out = m.group(1)
    except Exception:                                         # noqa: BLE001
        pass
    _BOOT_ID = out
    return out


def _app_version() -> str:
    try:
        import update
        return str(update.version() or "")
    except Exception:                                         # noqa: BLE001
        return ""


def _install_id() -> str:
    try:
        import final_asr
        return final_asr.install_id()
    except Exception:                                         # noqa: BLE001
        return ""


def _redact(s: str) -> str:
    """去掉绝对路径里的用户名与主机名（`/Users/<名>` → `~`）。"""
    s = re.sub(r"/Users/[^/\s\"']+", "~", s)
    try:
        s = s.replace(str(pathlib.Path.home()), "~")
    except Exception:                                         # noqa: BLE001
        pass
    try:
        host = platform.node()
        if host:
            s = s.replace(host, "<host>")
    except Exception:                                         # noqa: BLE001
        pass
    return s


def _clean(v, depth: int = 0):
    """只留可 JSON 化的小值：数字 / 布尔 / 短串 / 浅表。超限返回 `None`。

    ⚠️ 这是**元数据**的白名单，不是隐私清洗器 —— 内容走 `content()`。
    """
    if v is None or isinstance(v, (bool, int, float)):
        return v
    if isinstance(v, str):
        return _redact(v[:_MAX_STR])
    if depth >= _MAX_DEPTH:
        return None
    if isinstance(v, dict):
        out = {}
        for i, (k, x) in enumerate(v.items()):
            if i >= _MAX_KEYS:
                break
            kk = _redact(str(k))[:_MAX_STR]
            xv = _clean(x, depth + 1)
            if xv is not None:
                out[kk] = xv
        return out
    if isinstance(v, (list, tuple)):
        vals = [_clean(x, depth + 1) for x in v[:20]]
        return [x for x in vals if x is not None]
    return None


def _last_event(path: pathlib.Path) -> dict:
    """读 jsonl 的最后一条（只读尾部 64 KB；坏行跳过）。永不抛。"""
    try:
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 65536))
            tail = f.read().decode("utf-8", "replace")
        for line in reversed(tail.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                got = json.loads(line)
            except ValueError:
                continue
            if isinstance(got, dict):
                return got
    except Exception:                                         # noqa: BLE001
        pass
    return {}


def recover_info(root=None, *, stale_s: float = STALE_S, now: float | None = None) -> dict:
    """上一节是否需要补一条 `session.recovered`。**只读**，永不抛。

    返回 `{prev_session_id, last_seq, heartbeat_age_s, boot_changed}`；没有残局 = `{}`。
    """
    try:
        d = paths.telemetry_dir(root=root)
        hb = json.loads((d / "heartbeat.json").read_text(encoding="utf-8"))
        if not isinstance(hb, dict):
            return {}
        sid = str(hb.get("session_id") or "")
        if not sid:
            return {}
        ts = hb.get("t_wall")
        age = None
        if isinstance(ts, (int, float)) and not isinstance(ts, bool):
            age = max(0.0, float(now if now is not None else time.time()) - float(ts))
        last = _last_event(d / f"{sid}.events.jsonl")
        ended = str(last.get("event") or "") == "session.end"
        boot_changed = bool(hb.get("boot_id")) and str(hb.get("boot_id")) != _boot_id()
        if ended and not boot_changed and (age is None or age <= float(stale_s)):
            return {}
        return {"prev_session_id": sid,
                "last_seq": int(last.get("seq") or hb.get("seq") or 0),
                "heartbeat_age_s": None if age is None else round(age, 1),
                "boot_changed": bool(boot_changed)}
    except Exception:                                         # noqa: BLE001
        return {}


class Telemetry:
    """一个会话的采集器。**所有公开方法都不抛**；调用方见模块级函数。"""

    def __init__(self, *, session_id: str, root=None, app_version: str = "",
                 region: str = "", install_id: str = "", content: bool = True,
                 max_queue: int = MAX_QUEUE) -> None:
        self.session_id = str(session_id)
        self.dir = paths.telemetry_dir(root=root)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.events_path = self.dir / f"{self.session_id}.events.jsonl"
        self.content_path = self.dir / f"{self.session_id}.content.jsonl"
        self._content_on = bool(content)
        self._install_id = install_id or _install_id()
        self._app_version = app_version or _app_version()
        self._region = str(region or "")
        self._boot_id = _boot_id()
        self._t0_mono = time.monotonic()
        self._t0_wall = time.time()
        self._seq = 0
        self._dropped = 0
        self._lock = threading.Lock()
        self._q: "queue.Queue[tuple[str, dict]]" = queue.Queue(maxsize=max(16, int(max_queue)))
        self._stop = threading.Event()
        self._closed = False
        self._hb_stop = threading.Event()
        self._hb_thread: threading.Thread | None = None
        self._writer = threading.Thread(target=self._run, name="telemetry-writer", daemon=True)
        self._writer.start()

    # ---- 对外 ---------------------------------------------------------
    def start(self, attrs: dict | None = None) -> None:
        """`session.start`；有残局就先补 `session.recovered`（同一条流，因果在前）。"""
        rec = recover_info(root=self.dir.parent, now=self._t0_wall)
        if rec:
            self.event("session.recovered", {**rec, "outcome": "abnormal"},
                       component="telemetry", severity="warn")
        self.event("session.start", attrs or {}, component="telemetry")

    def event(self, name: str, attrs: dict | None = None, *, component: str = "",
              severity: str = "info", caused_by: str = "") -> None:
        self._put("event", {"event": str(name), "component": str(component),
                            "severity": str(severity), "caused_by": str(caused_by),
                            "attrs": _clean(attrs or {}) or {}})

    def content(self, name: str, attrs: dict | None = None, *, component: str = "") -> None:
        """逐句 / 笔记块等内容事件（单独文件）。**不经过元数据白名单**。"""
        if not self._content_on:
            return
        try:
            payload = attrs or {}
        except Exception:                                     # noqa: BLE001
            payload = {}
        self._put("content", {"event": str(name), "component": str(component),
                              "attrs": payload})

    def heartbeat(self, provider=None) -> None:
        """写心跳文件 + 一条 `session.heartbeat` 事件。`provider()` 返回计数器 dict。"""
        extra = {}
        if provider is not None:
            try:
                got = provider()
                if isinstance(got, dict):
                    extra = got
            except Exception:                                 # noqa: BLE001
                extra = {}
        with self._lock:
            seq = self._seq
        snap = {"session_id": self.session_id, "seq": seq,
                "t_wall": round(time.time(), 3),
                "t_mono_ms": int((time.monotonic() - self._t0_mono) * 1000),
                "boot_id": self._boot_id, "pid": os.getpid(),
                "app_version": self._app_version, **extra}
        tmp = self.dir / "heartbeat.json.tmp"
        try:
            tmp.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.dir / "heartbeat.json")
        except Exception:                                     # noqa: BLE001
            pass
        self.event("session.heartbeat", {"layer_states": extra.get("layer_states") or {},
                                         "counters": extra.get("counters") or {},
                                         "queue_depth": self._q.qsize()},
                   component="telemetry")

    def start_heartbeat(self, provider=None, every_s: float = HEARTBEAT_S) -> None:
        if self._hb_thread is not None:
            return
        self.heartbeat(provider)                              # 立刻写第一口

        def loop() -> None:
            while not self._hb_stop.wait(max(1.0, float(every_s))):
                self.heartbeat(provider)

        self._hb_thread = threading.Thread(target=loop, name="telemetry-heartbeat",
                                           daemon=True)
        self._hb_thread.start()

    def end(self, outcome: str = "ok", attrs: dict | None = None) -> None:
        """`session.end`（`outcome` = ok / abnormal / failed / stopped）。"""
        self.event("session.end", {"outcome": str(outcome),
                                   "duration_s": round(time.time() - self._t0_wall, 1),
                                   **(attrs or {})},
                   component="telemetry",
                   severity="info" if outcome == "ok" else "warn")

    def close(self, timeout: float = 2.0) -> None:
        """停心跳、排空队列、关文件。幂等；永不抛。"""
        if self._closed:
            return
        self._closed = True
        try:
            self._hb_stop.set()
        except Exception:                                     # noqa: BLE001
            pass
        try:
            self._q.put_nowait(("__stop__", {}))
        except queue.Full:
            try:
                self._q.get_nowait()
                self._q.put_nowait(("__stop__", {}))
            except Exception:                                 # noqa: BLE001
                pass
        except Exception:                                     # noqa: BLE001
            pass
        try:
            self._writer.join(timeout=max(0.1, float(timeout)))
        except Exception:                                     # noqa: BLE001
            pass

    # ---- 内部 ---------------------------------------------------------
    def _put(self, kind: str, payload: dict) -> None:
        if self._closed:
            return
        try:
            self._q.put_nowait((kind, payload))
        except queue.Full:
            # 丢**最旧**再重试一次（有界；绝不阻塞调用方）。
            with self._lock:
                self._dropped += 1
            try:
                self._q.get_nowait()
                self._q.put_nowait((kind, payload))
            except Exception:                                 # noqa: BLE001
                pass
        except Exception:                                     # noqa: BLE001
            pass

    def _envelope(self, name: str, *, component: str = "", severity: str = "info",
                  caused_by: str = "", attrs: dict | None = None) -> dict:
        with self._lock:
            self._seq += 1
            seq = self._seq
        mono = int((time.monotonic() - self._t0_mono) * 1000)
        return {"schema_version": SCHEMA_VERSION,
                "event": name, "event_id": uuid.uuid4().hex,
                "session_id": self.session_id, "install_id": self._install_id,
                "seq": seq,
                "t_wall": round(time.time(), 3), "t_mono_ms": mono,
                "t_awake_ms": mono,                       # macOS 上 monotonic 睡眠不走
                "boot_id": self._boot_id,
                "app_version": self._app_version,
                "os_version": platform.mac_ver()[0] or platform.release(),
                "arch": platform.machine(),
                "region": self._region,
                "component": str(component), "severity": str(severity),
                "caused_by": str(caused_by) or None,
                "attrs": attrs or {}}

    def _run(self) -> None:
        ev = con = None
        try:
            ev = self.events_path.open("a", encoding="utf-8")
            if self._content_on:
                con = self.content_path.open("a", encoding="utf-8")
            while True:
                kind, payload = self._q.get()
                if kind == "__stop__":
                    break
                try:
                    if kind == "content":
                        if con is not None:
                            rec = {"schema_version": SCHEMA_VERSION,
                                   "session_id": self.session_id,
                                   "t_wall": round(time.time(), 3),
                                   "event": payload.get("event", ""),
                                   "component": payload.get("component", ""),
                                   "attrs": payload.get("attrs") or {}}
                            con.write(json.dumps(rec, ensure_ascii=False,
                                                 default=str) + "\n")
                            con.flush()
                        continue
                    rec = self._envelope(payload.get("event", ""),
                                         component=payload.get("component", ""),
                                         severity=payload.get("severity", "info"),
                                         caused_by=payload.get("caused_by", ""),
                                         attrs=payload.get("attrs") or {})
                    ev.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
                    ev.flush()
                except Exception:                             # noqa: BLE001
                    continue
        except Exception:                                     # noqa: BLE001
            pass
        finally:
            for h in (ev, con):
                try:
                    if h is not None:
                        h.close()
                except Exception:                             # noqa: BLE001
                    pass

    # 只读给判据 / 报告
    @property
    def dropped(self) -> int:
        with self._lock:
            return self._dropped


# ══════════════════════════════════════════════════════════════════════
# 模块级单例（调用方不必到处传对象；没启动时全是 no-op）
# ══════════════════════════════════════════════════════════════════════

def start(*, root=None, region: str = "", content: bool = True,
          attrs: dict | None = None, session_id: str | None = None) -> Telemetry:
    """开一个会话并写 `session.start`；重复调用返回现有实例。**永不抛**。"""
    global _CUR
    with _CUR_LOCK:
        if _CUR is not None:
            return _CUR
        sid = str(session_id or
                  f"{time.strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(3)}")
        try:
            _CUR = Telemetry(session_id=sid, root=root, region=region, content=content)
        except Exception:                                     # noqa: BLE001
            _CUR = Telemetry.__new__(Telemetry)               # 极简空壳：只保证模块级调用不炸
            _CUR.session_id = sid
            _CUR._closed = True
            _CUR._q = queue.Queue()
            _CUR._lock = threading.Lock()
            _CUR._dropped = 0
            return _CUR
        _CUR.start(attrs)
        return _CUR


def current() -> "Telemetry | None":
    return _CUR


def emit(name: str, attrs: dict | None = None, **kw) -> None:
    t = _CUR
    if t is not None and not t._closed:
        try:
            t.event(name, attrs, **kw)
        except Exception:                                     # noqa: BLE001
            pass


def content(name: str, attrs: dict | None = None, **kw) -> None:
    t = _CUR
    if t is not None and not t._closed:
        try:
            t.content(name, attrs, **kw)
        except Exception:                                     # noqa: BLE001
            pass


def heartbeat(provider=None) -> None:
    t = _CUR
    if t is not None and not t._closed:
        try:
            t.heartbeat(provider)
        except Exception:                                     # noqa: BLE001
            pass


def start_heartbeat(provider=None, **kw) -> None:
    t = _CUR
    if t is not None and not t._closed:
        try:
            t.start_heartbeat(provider, **kw)
        except Exception:                                     # noqa: BLE001
            pass


def end(outcome: str = "ok", attrs: dict | None = None) -> None:
    t = _CUR
    if t is not None and not t._closed:
        try:
            t.end(outcome, attrs)
        except Exception:                                     # noqa: BLE001
            pass


def close() -> None:
    global _CUR
    with _CUR_LOCK:
        t, _CUR = _CUR, None
    if t is not None:
        try:
            t.close()
        except Exception:                                     # noqa: BLE001
            pass
