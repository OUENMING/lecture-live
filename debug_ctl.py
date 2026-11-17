"""调试通道 —— 让 AI（和作者）在一节**正在跑**的 ClassLive 上问状态、等条件、发控制命令。

全部拿结构化回复和退出码，不靠 grep 日志。客户端是 `tools/clctl.py`。开关 `CLASSLIVE_DEBUG_CTL=1`
（`docs/HANDBOOK.md` §6.1）；隔离运行默认开，正式上课默认关。

## 接口（调用方只需要知道这些）

    ctl = debug_ctl.Ctl()                  # 不碰任何东西
    ctl.register_state("trans", fn)        # fn() -> JSON 友好的 dict；在主线程被调用
    ctl.register_command("mode", fn, "…")  # fn(args: str) -> data；出错 raise CtlError(code, msg)
    ctl.start()                            # 开关没开 → 直接返回 False，不起线程、不建 socket
    ctl.poll()                             # ⭐ 主循环每圈调一次；**命令只在这里、在调它的线程上执行**
    ctl.emit("final", n=3)                 # 记一条事件；任意线程可调
    ctl.close()                            # 清掉 socket 和 ctl.path；幂等

## 为什么是这个形状

- **关着就等于没有**：`start()` 不起线程，`poll()` / `emit()` 第一行就是一次布尔判断。
- **主线程从不碰 socket**：accept / 读 / 写全在通道自己的线程上；命令以 `(请求, Future)` 进队，
  `poll()` 每次最多取 8 条在**调用者线程**执行（AppKit 只能主线程碰，这条就是为它留的）。
  连接线程等 Future：队里 5 s 没人取就撤销并回 `timeout`（撤销后 `poll()` 不会再执行它）；
  已经开始执行的命令（如 `stall`）再多等它跑完。
- **能等事件，不只等状态**：`events` 命令回每个 kind 的**累计计数**，`clctl --wait 'events.final >= 5'` 用它。
- **没有 eval、没有任意路径参数、没有改文件的命令**；key / 通行码 / install-id 不进任何回复
  （状态分区由调用方注册，注册什么就只有什么）。

## 协议

一行一个 JSON。请求 `{"id","cmd","args"}`（`args` 是字符串，`state:a,b` → cmd=`state` args=`a,b`）；
回复 `{"id","ok","data","error":{"code","msg"}?,"slow_ms"?}`。`slow_ms` 只在命令执行 >=100 ms 时出现。
错误码：`unknown_command`（客户端退出 3）· `timeout`（退出 2）· `bad_args` / `failed` / `bad_request`（退出 1）。
"""
from __future__ import annotations

import collections
import concurrent.futures
import json
import os
import pathlib
import queue
import socket
import stat
import threading
import time

import paths

FLAG = "CLASSLIVE_DEBUG_CTL"
FLAG_TEXT = "CLASSLIVE_DEBUG_CTL_TEXT"

#: macOS 的 `sun_path` 是 104 字节（含结尾 NUL）；留点余量，超了就改放 /tmp。
SOCK_PATH_MAX = 100
RING = 2000
MAX_PER_POLL = 8
QUEUE_WAIT_S = 5.0
STALL_MAX_S = 5.0
TEXT_MAX = 200
MAX_REQUEST_BYTES = 64 * 1024
SLOW_MS = 100

_BUILTIN = ("ping", "help", "state", "events", "stall")


def enabled() -> bool:
    return os.environ.get(FLAG) == "1"


def text_enabled() -> bool:
    return os.environ.get(FLAG_TEXT) == "1"


class CtlError(Exception):
    """命令自己说「做不了」：回 `ok:false` + 这个错误码。"""

    def __init__(self, code: str, msg: str = ""):
        super().__init__(msg or code)
        self.code, self.msg = code, msg or code


class _Req:
    __slots__ = ("cmd", "args", "fut", "started")

    def __init__(self, cmd: str, args: str):
        self.cmd, self.args = cmd, args
        self.fut: concurrent.futures.Future = concurrent.futures.Future()
        self.started = threading.Event()


def _shape(v, depth: int):
    """`help` 里每个状态字段的类型。dict 递归（最多 3 层），其余给名字。"""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    if isinstance(v, str):
        return "str"
    if isinstance(v, dict):
        return {str(k): _shape(x, depth - 1) for k, x in v.items()} if depth > 0 else "object"
    if isinstance(v, (list, tuple)):
        return "list"
    return type(v).__name__


class Ctl:
    def __init__(self, *, root=None, timeout_s: float = QUEUE_WAIT_S):
        self._root = root
        self._timeout_s = timeout_s
        self._on = False
        self._states: dict[str, object] = {}
        self._cmds: dict[str, tuple[object, str]] = {}
        self._q: queue.Queue[_Req] = queue.Queue()
        self._ring: collections.deque = collections.deque(maxlen=RING)
        self._counts: dict[str, int] = {}
        self._seq = 0
        self._ev_lock = threading.Lock()
        self._text = False
        self._srv: socket.socket | None = None
        self._threads: list[threading.Thread] = []
        self._conns: set[socket.socket] = set()
        self._conn_lock = threading.Lock()
        self._closing = threading.Event()
        self._sock_path: pathlib.Path | None = None
        self._ptr_path: pathlib.Path | None = None
        self._tmp_dir: pathlib.Path | None = None
        self.error: str | None = None

    # ---------- 注册 ----------
    def register_state(self, name: str, fn) -> None:
        self._states[name] = fn

    def register_command(self, name: str, fn, help: str = "") -> None:
        if name in _BUILTIN or not name.isalpha():
            raise ValueError(f"命令名不可用：{name!r}")
        self._cmds[name] = (fn, help)

    @property
    def sock_path(self) -> pathlib.Path | None:
        return self._sock_path

    # ---------- 事件（任意线程） ----------
    def emit(self, kind: str, n: int | None = None, text=None) -> None:
        if not self._on:
            return
        with self._ev_lock:
            self._seq += 1
            ev: dict = {"seq": self._seq, "t": round(time.time(), 3), "kind": kind}
            if n is not None:
                ev["n"] = n
            if self._text and text:
                pairs = text.items() if isinstance(text, dict) else (("en", text),)
                for k, v in pairs:
                    if k in ("en", "zh") and v:
                        ev[k] = str(v)[:TEXT_MAX]
            self._ring.append(ev)
            self._counts[kind] = self._counts.get(kind, 0) + 1

    # ---------- 主线程 ----------
    def poll(self) -> None:
        if not self._on:
            return
        for _ in range(MAX_PER_POLL):
            try:
                req = self._q.get_nowait()
            except queue.Empty:
                return
            if not req.fut.set_running_or_notify_cancel():
                continue                      # 连接线程已超时撤销
            req.started.set()
            t0 = time.monotonic()
            try:
                res = (True, self._run(req.cmd, req.args), None)
            except CtlError as e:
                res = (False, None, {"code": e.code, "msg": e.msg})
            except Exception as e:            # noqa: BLE001 —— 命令里任何异常都不许带崩主循环
                res = (False, None, {"code": "failed", "msg": f"{type(e).__name__}: {e}"})
            req.fut.set_result((*res, int((time.monotonic() - t0) * 1000)))

    def _run(self, cmd: str, args: str):
        if cmd == "ping":
            return {"pong": True, "pid": os.getpid(), "at": round(time.time(), 3)}
        if cmd == "help":
            return self._help()
        if cmd == "state":
            return self._state(args)
        if cmd == "events":
            return self._events(args)
        if cmd == "stall":
            try:
                s = float(args)
            except ValueError:
                raise CtlError("bad_args", "stall:<秒>，0 < 秒 <= 5") from None
            if not 0 < s <= STALL_MAX_S:
                raise CtlError("bad_args", f"stall 秒数要在 (0, {STALL_MAX_S:g}] 内")
            time.sleep(s)
            return {"stalled_s": s}
        if cmd in self._cmds:
            return self._cmds[cmd][0](args)  # type: ignore[operator]
        raise CtlError("unknown_command", f"没有这个命令：{cmd}")

    def _section(self, name: str):
        try:
            return self._states[name]()  # type: ignore[operator]
        except Exception as e:  # noqa: BLE001
            return {"_error": type(e).__name__}

    def _state(self, args: str) -> dict:
        names = [a.strip() for a in args.split(",") if a.strip()] or list(self._states)
        bad = [n for n in names if n not in self._states]
        if bad:
            raise CtlError("bad_args", f"没有这些状态分区：{','.join(bad)}（help 看列表）")
        out: dict = {"schema": 1, "at": round(time.time(), 3)}
        for n in names:
            out[n] = self._section(n)
        return out

    def _events(self, args: str) -> dict:
        opts: dict[str, str] = {}
        for part in [p for p in args.split(",") if p.strip()]:
            k, _, v = part.partition("=")
            opts[k.strip()] = v.strip()
        extra = set(opts) - {"since", "limit"}
        if extra:
            raise CtlError("bad_args", f"events 只认 since= / limit=：{sorted(extra)}")
        try:
            since = int(opts["since"]) if "since" in opts else None
            limit = max(0, int(opts.get("limit", 500)))
        except ValueError:
            raise CtlError("bad_args", "since / limit 要是整数") from None
        with self._ev_lock:
            ring = list(self._ring)
            counts = dict(self._counts)
            seq = self._seq
        lost = 0
        if since is None:
            items = ring[-limit:] if limit else []
        else:
            oldest = ring[0]["seq"] if ring else seq + 1
            lost = max(0, oldest - 1 - since)          # 想要的比缓冲里最老的还早 → 丢了几条
            items = [e for e in ring if e["seq"] > since][:limit]
        return {"seq": seq, "counts": counts, "lost": lost, "items": items}

    def _help(self) -> dict:
        cmds = {
            "ping": "活着吗",
            "help": "命令和状态分区（含每个字段的类型）",
            "state": "state[:分区,…] 当前状态",
            "events": "events[:since=<seq>,limit=<n>] 最近事件 + 每个 kind 的累计计数",
            "stall": f"stall:<秒> 故意卡住调 poll() 的线程（<={STALL_MAX_S:g}）",
        }
        cmds.update({k: v[1] for k, v in self._cmds.items()})
        return {"commands": cmds,
                "state": {n: _shape(self._section(n), 3) for n in self._states},
                "events": {"ring": RING, "text": self._text}}

    # ---------- 起 / 停 ----------
    def start(self) -> bool:
        """开关没开 → False，什么都不建。起不来（目录权限不对等）→ False 并把原因放 `self.error`。"""
        if self._on or not enabled():
            return self._on
        try:
            self._bind()
        except Exception as e:  # noqa: BLE001
            self.error = f"{type(e).__name__}: {e}"
            self._teardown_files()
            return False
        self._closing.clear()
        self._text = text_enabled()
        self._on = True
        t = threading.Thread(target=self._accept_loop, name="debug-ctl-accept", daemon=True)
        t.start()
        self._threads.append(t)
        return True

    def _bind(self) -> None:
        d = paths.ctl_dir(root=self._root)
        d.mkdir(parents=True, exist_ok=True)
        os.chmod(d, 0o700)
        sock = d / f"ctl-{os.getpid()}.sock"
        if len(os.fsencode(str(sock))) > SOCK_PATH_MAX:
            tmp = pathlib.Path("/tmp") / f"classlive-ctl-{os.getuid()}"
            try:
                tmp.mkdir(mode=0o700)
            except FileExistsError:
                pass
            st = tmp.lstat()
            if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
                raise PermissionError(f"{tmp} 不是自己的目录")
            os.chmod(tmp, 0o700)
            self._tmp_dir = tmp
            sock = tmp / f"{os.getpid()}.sock"
            self._ptr_path = d / "ctl.path"
        sock.unlink(missing_ok=True)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        old = os.umask(0o177)
        try:
            srv.bind(str(sock))
        finally:
            os.umask(old)
        os.chmod(sock, 0o600)
        srv.listen(32)
        srv.settimeout(0.2)
        self._srv, self._sock_path = srv, sock
        if self._ptr_path:
            self._ptr_path.write_text(str(sock) + "\n", encoding="utf-8")
            os.chmod(self._ptr_path, 0o600)

    def close(self) -> None:
        self._on = False
        self._closing.set()
        # 还在等的请求：直接告诉连接线程「关了」，别让它们傻等 5 s
        while True:
            try:
                req = self._q.get_nowait()
            except queue.Empty:
                break
            if req.fut.set_running_or_notify_cancel():
                req.started.set()
                req.fut.set_exception(CtlError("closed", "通道已关闭"))
        if self._srv is not None:
            try:
                self._srv.close()
            except OSError:
                pass
        with self._conn_lock:
            conns = list(self._conns)
        for c in conns:
            try:
                c.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        for t in self._threads:
            if t is not threading.current_thread():
                t.join(timeout=2)
        self._threads.clear()
        self._srv = None
        self._teardown_files()

    def _teardown_files(self) -> None:
        if self._sock_path:
            self._sock_path.unlink(missing_ok=True)
        if self._ptr_path and self._sock_path:
            try:
                if self._ptr_path.read_text(encoding="utf-8").strip() == str(self._sock_path):
                    self._ptr_path.unlink(missing_ok=True)
            except OSError:
                pass
        if self._tmp_dir:
            try:
                self._tmp_dir.rmdir()               # 别人的 socket 还在就清不掉，正好
            except OSError:
                pass
        self._sock_path = self._ptr_path = self._tmp_dir = None

    # ---------- 通道自己的线程 ----------
    def _accept_loop(self) -> None:
        while not self._closing.is_set():
            try:
                conn, _ = self._srv.accept()  # type: ignore[union-attr]
            except socket.timeout:
                continue
            except OSError:
                return
            conn.settimeout(0.5)
            with self._conn_lock:
                self._conns.add(conn)
            t = threading.Thread(target=self._serve, args=(conn,), name="debug-ctl-conn", daemon=True)
            t.start()
            self._threads = [x for x in self._threads if x.is_alive()] + [t]

    def _serve(self, conn: socket.socket) -> None:
        buf = b""
        try:
            while not self._closing.is_set():
                while b"\n" not in buf:
                    try:
                        chunk = conn.recv(4096)
                    except socket.timeout:
                        if self._closing.is_set():
                            return
                        continue
                    if not chunk:
                        return
                    buf += chunk
                    if len(buf) > MAX_REQUEST_BYTES:
                        return
                line, _, buf = buf.partition(b"\n")
                if line.strip():
                    conn.sendall(self._handle(line) + b"\n")
        except OSError:
            pass
        finally:
            with self._conn_lock:
                self._conns.discard(conn)
            try:
                conn.close()
            except OSError:
                pass

    def _handle(self, line: bytes) -> bytes:
        rid = None
        try:
            msg = json.loads(line)
            rid = msg.get("id")
            cmd = msg["cmd"]
            args = msg.get("args") or ""
            if not isinstance(cmd, str) or not isinstance(args, str):
                raise TypeError
        except (ValueError, KeyError, TypeError, AttributeError):
            return self._pack(rid, False, None, {"code": "bad_request", "msg": "要 {id, cmd, args}"})
        req = _Req(cmd, args)
        self._q.put(req)
        if not req.started.wait(self._timeout_s) and req.fut.cancel():
            # 还没人取走：撤销，之后 poll() 会跳过它
            return self._pack(rid, False, None, {"code": "timeout",
                                                 "msg": f"{self._timeout_s:g}s 内主线程没取这条命令"})
        try:
            ok, data, err, ms = req.fut.result(timeout=self._timeout_s + STALL_MAX_S + 5)
        except CtlError as e:
            return self._pack(rid, False, None, {"code": e.code, "msg": e.msg})
        except concurrent.futures.TimeoutError:
            return self._pack(rid, False, None, {"code": "timeout", "msg": "命令执行太久"})
        return self._pack(rid, ok, data, err, ms)

    @staticmethod
    def _pack(rid, ok, data, err, ms=0) -> bytes:
        out: dict = {"id": rid, "ok": ok, "data": data}
        if err:
            out["error"] = err
        if ms >= SLOW_MS:
            out["slow_ms"] = ms
        return json.dumps(out, ensure_ascii=False, default=str).encode("utf-8")
