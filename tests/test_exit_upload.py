#!/usr/bin/env python3
"""**退出后留一个后台进程把队列传完** 的判据（3.10.x）。

    ClassLive.app/Contents/MacOS/python tests/test_exit_upload.py

## 钉住的是什么

1. `upload.drain()`：空即停 / 全在退避里即停（**不空转**）/ deadline / `should_stop` /
   每轮 `max_items=1` 且轮间 `sleep(pause_s)`
2. **跨进程只许一个写者**：`DrainLock` 互斥；`stop_drainer` 能停掉**真的子进程**；
   锁文件里的 pid 已经**不持锁**时**一个信号都不发**（pid 复用不许误杀）
3. `spawn_exit_uploader()` 的三道闸 + `start_new_session=True` + `taskpolicy -b`
4. **端到端**：临时 HOME + 本机 HTTP 服务器 + 真 `upload.py --drain` 子进程 ——
   父进程不等它，队列与锁在 30 s 内有界地自己走到终点
5. 接线：退出段 `_upload_on_exit()` 在 `_auto_update_on_exit()` 之前；
   启动段 `stop_drainer()` 在构造共享队列之前；`entry_launch` 关面板那条也起 drainer

⚠️ **HOME 全套隔离**（`CLAUDE.md` 的硬规矩：读端写端都要换）：本文件自己
   建临时 HOME，子进程 `HOME=<临时>`，绝不碰真 `~/.classlive/` 的队列 / 锁，
   也绝不向真 Worker 发请求（端点指向本机 `http.server`）。
⚠️ 每条都问过「把实现改坏它会不会红」—— 变异表见交回说明；关键几条在注释里点名。
"""
from __future__ import annotations

import ast
import http.server
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import threading
import time

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import paths                                                          # noqa: E402
import upload                                                         # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  {detail}" if detail else ""))


# ---------------------------------------------------------------- 假队列 / 假时钟

class _ScriptQueue:
    """按脚本回放 `pump` 的结果 —— `drain` 是纯循环，判据里不需要真队列。"""

    def __init__(self, pending: int, results: list[dict]):
        self._pending = pending
        self._script = list(results)
        self._n = 0
        self.max_items: list[int] = []

    @property
    def pending(self) -> int:
        return self._pending

    def pump(self, max_items: int = 1) -> dict:
        self.max_items.append(max_items)
        r = self._script[min(self._n, len(self._script) - 1)]
        self._n += 1
        self._pending = int(r.get("pending", self._pending))
        return {"sent": 0, "failed": 0, "expired": 0, **r}


def _case_drain(tmp: pathlib.Path) -> None:
    print("\n--- E1 ⭐⭐ `drain`：停的条件 / 每轮一条 / 轮间 pause ---")
    now = [0.0]
    sleeps: list[float] = []

    def _clock() -> float:
        return now[0]

    def _sleep(s: float) -> None:
        sleeps.append(s)
        now[0] += s

    # ① 队列空 -> 一次 pump 都不该调
    q0 = _ScriptQueue(0, [{"sent": 1, "pending": 0}])
    r0 = upload.drain(q0, clock=_clock, sleep=_sleep)
    # ⚠️ 变异验证：把 `if not queue.pending` 那条删掉 -> 这里 rounds 会变 1、reason 变 empty
    #    之前先空跑一轮（看起来结果一样，但白试了一次网络 —— 这条钉的是"没白试"）。
    check("⭐⭐ 队列空 -> 立刻停（`rounds=0`，一次都没试）",
          r0["rounds"] == 0 and not q0.max_items and r0["reason"] == "empty", str(r0))

    # ② 一条一条传，轮间 sleep(pause_s)
    q1 = _ScriptQueue(3, [{"sent": 1, "pending": 2}, {"sent": 1, "pending": 1},
                          {"sent": 1, "pending": 0}])
    r1 = upload.drain(q1, pause_s=2.0, clock=_clock, sleep=_sleep)
    # ⚠️ 变异验证：`pump(max_items=1)` 改成 `pump()` 或 `pump(max_items=queue.pending)`
    #    -> max_items 那条红（一口气倒空会被服务端风控）。
    check("⭐⭐ 每轮 `max_items=1`，三轮传完 3 条",
          q1.max_items == [1, 1, 1] and r1["rounds"] == 3 and r1["sent"] == 3
          and r1["reason"] == "empty", f"{q1.max_items} {r1}")
    # ⚠️ 变异验证：把轮间 `sleep(pause_s)` 删掉 -> 这条红。
    check("⭐⭐ 轮与轮之间 `sleep(pause_s)`（传完最后一轮不白睡）",
          sleeps == [2.0, 2.0], str(sleeps))

    # ③ 剩下的都在退避里 -> 停，且**不空转**
    sleeps.clear()
    q2 = _ScriptQueue(2, [{"sent": 0, "failed": 0, "pending": 2}])
    r2 = upload.drain(q2, clock=_clock, sleep=_sleep)
    # ⚠️ 变异验证：把 `if not r.get("sent") and not r.get("failed")` 那条删掉
    #    -> 循环直到 deadline（判据里会一直转）；reason 也不再是 backoff。
    check("⭐⭐ 全都还没到 `next_at` -> 试一轮就停（`reason=backoff`，不空转）",
          len(q2.max_items) == 1 and r2["reason"] == "backoff" and sleeps == [],
          f"{r2} pumps={len(q2.max_items)}")

    # ④ deadline：假时钟每睡一次前进 pause_s，必须自己到点
    sleeps.clear()
    now[0] = 0.0
    q3 = _ScriptQueue(1, [{"sent": 0, "failed": 1, "pending": 1}])
    r3 = upload.drain(q3, deadline_s=5.0, pause_s=2.0, clock=_clock, sleep=_sleep)
    # ⚠️ 变异验证：把 deadline 判断删掉 -> 这个循环**永不返回**（判据会超时红）。
    check("⭐⭐ 到 deadline 就停（有界：不会永远重试）",
          r3["reason"] == "deadline" and r3["rounds"] == 3, str(r3))

    # ⑤ should_stop：中途叫停（**当前这一轮之后就走，不再白睡 pause_s**）
    sleeps.clear()
    stop_calls = [0]

    def _stop_after_this_round() -> bool:
        stop_calls[0] += 1
        return stop_calls[0] >= 2                   # 第一轮发完之后说"停"

    q4 = _ScriptQueue(5, [{"sent": 1, "pending": 4}])
    r4 = upload.drain(q4, clock=_clock, sleep=_sleep,
                      should_stop=_stop_after_this_round)
    # ⚠️ 变异验证：把 pump 之后、`sleep(pause_s)` 之前那次 `should_stop()` 删掉
    #    -> sleeps 会变成 [2.0]（SIGTERM 之后还白等一轮 pause）。
    check("⭐⭐ `should_stop` 在轮间生效（当前这条发完就走，不再白睡）",
          r4["reason"] == "stopped" and r4["rounds"] == 1 and r4["sent"] == 1
          and sleeps == [], f"{r4} sleeps={sleeps}")

    q5 = _ScriptQueue(9, [{"sent": 1, "pending": 8}])
    r5 = upload.drain(q5, clock=_clock, sleep=_sleep, should_stop=lambda: True)
    check("⭐ 一开始就叫停 -> 一次都不试", r5["reason"] == "stopped" and not q5.max_items,
          str(r5))


# ---------------------------------------------------------------- 锁

_CHILD_HOLD = (
    "import sys, time\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "import upload\n"
    "lk = upload.DrainLock(root=sys.argv[2])\n"
    "print('GOT' if lk.acquire() else 'NOPE', flush=True)\n"
    "time.sleep(60)\n"
)

_CHILD_SLEEP = (
    "import sys, time\n"
    "print('READY', flush=True)\n"
    "time.sleep(60)\n"
)


def _spawn(code: str, *args: str) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", code, *args],
                            stdout=subprocess.PIPE, text=True)


def _lock_free(root: pathlib.Path) -> bool:
    lk = upload.DrainLock(root=root)
    if not lk.acquire():
        return False
    lk.release()
    return True


def _case_lock(tmp: pathlib.Path) -> None:
    print("\n--- E2 ⭐⭐⭐ 跨进程只许一个写者：flock + stop_drainer ---")
    root = tmp / "lockstate"

    lk1 = upload.DrainLock(root=root)
    lk2 = upload.DrainLock(root=root)
    got1 = lk1.acquire()
    # ⚠️ 变异验证：把 `fcntl.flock(...LOCK_NB)` 删掉 -> 这条红（第二个"drainer"能进）。
    got2 = lk2.acquire()
    check("⭐⭐ 第二个 drainer 拿不到锁（同一时刻只许一个写者）",
          got1 is True and got2 is False, f"{got1}/{got2}")
    check("⭐ 锁文件里写的是**自己的 pid**",
          upload._read_lock_pid(lk1.path) == os.getpid(),
          str(upload._read_lock_pid(lk1.path)))

    # `run_drain` 在锁被占时**直接退出 0**，一条都不碰
    logged: list[str] = []
    orig_log = upload._drain_log
    upload._drain_log = logged.append
    try:
        rc_busy = upload.run_drain(root=root)
    finally:
        upload._drain_log = orig_log
    check("⭐⭐ 锁被占时 `--drain` **直接退出 0**（不是失败）",
          rc_busy == 0 and any("已有 drainer" in s for s in logged), str(logged))
    lk1.release()

    # 真子进程持锁 -> stop_drainer 能在超时内停掉它，且锁真的放开了
    child = _spawn(_CHILD_HOLD, str(HERE), str(root))
    try:
        first = child.stdout.readline().strip() if child.stdout else ""
        check("⭐ 子进程拿到了锁（下面的 stop 才有意义）", first == "GOT", first)
        t0 = time.monotonic()
        stopped = upload.stop_drainer(timeout=5.0, root=root)
        dt = time.monotonic() - t0
        # ⚠️ 变异验证：把 `os.kill(pid, SIGTERM)` 删掉 -> 这里要等满 5 s 再 SIGKILL
        #    （dt 会 ≥5）；把 `_wait_lock_free` 删掉 -> stopped 变 False / 锁仍被占。
        check("⭐⭐ `stop_drainer` 停掉了**真的子进程**（锁已释放，≤timeout）",
              stopped is True and dt < 5.0 and _lock_free(root),
              f"stopped={stopped} 用时={dt:.2f}s")
        check("⭐ 子进程确实退出了（不是只放了锁）",
              child.wait(timeout=5) is not None)
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)

    # 锁文件里的 pid **不持锁** -> 一个信号都不许发（pid 复用不许误杀）
    victim = _spawn(_CHILD_SLEEP)
    try:
        if victim.stdout:
            victim.stdout.readline()
        lk_path = paths.upload_drain_lock(root=root)
        lk_path.parent.mkdir(parents=True, exist_ok=True)
        lk_path.write_text(str(victim.pid), encoding="utf-8")
        # ⚠️⚠️ 这条守的是**误杀**：只按 pid 文件发信号，就会打死一个凑巧复用了
        #    这个 pid 的无辜进程。必须先拿到锁证明"没人持锁"，然后什么都不做。
        #    变异验证：把开头 `if lock.acquire(): release; return False` 删掉 -> 这条红。
        r = upload.stop_drainer(timeout=1.0, root=root)
        time.sleep(0.3)
        check("⭐⭐ 锁没人持有时**不发信号**（陈旧 pid 不许误杀）",
              r is False and victim.poll() is None, f"r={r} alive={victim.poll() is None}")
    finally:
        if victim.poll() is None:
            victim.kill()
        victim.wait(timeout=5)

    # 锁机制自己坏了 -> 同样**不许**照着 pid 发信号（ENOLCK 不是"有 drainer"）
    other = _spawn(_CHILD_SLEEP)
    try:
        if other.stdout:
            other.stdout.readline()
        paths.upload_drain_lock(root=root).write_text(str(other.pid), encoding="utf-8")
        import errno as _errno
        orig_flock = upload.fcntl.flock

        def _broken(*a, **kw):
            raise OSError(_errno.ENOLCK, "No locks available")

        upload.fcntl.flock = _broken
        try:
            r_nolock = upload.stop_drainer(timeout=0.3, root=root)
        finally:
            upload.fcntl.flock = orig_flock
        time.sleep(0.3)
        # ⚠️ 变异验证：把 `if not lock.busy: return False` 删掉 -> 这条红（误杀）。
        check("⭐⭐ 锁机制坏了（ENOLCK）-> 也不发信号（别把坏锁当成 drainer）",
              r_nolock is False and other.poll() is None, str(r_nolock))
    finally:
        if other.poll() is None:
            other.kill()
        other.wait(timeout=5)


# ---------------------------------------------------------------- spawn 闸

def _pending_queue(state_root: pathlib.Path) -> pathlib.Path:
    state_root.mkdir(parents=True, exist_ok=True)
    f = state_root / "x.md"
    f.write_text("x", encoding="utf-8")
    # ⚠️ `enqueue` 会顺手写台账（同目录），判据不关心，但路径必须在 root 下。
    upload.UploadQueue(lambda *a: True, root=state_root).enqueue([f], "sess-spawn")
    return state_root


def _case_spawn(tmp: pathlib.Path) -> None:
    print("\n--- E3 ⭐⭐ `spawn_exit_uploader` 的三道闸 + 独立会话 + taskpolicy ---")
    calls: list[tuple[list[str], dict]] = []
    orig_popen = upload.subprocess.Popen
    orig_taskpolicy = upload.TASKPOLICY
    orig_root = paths.STATE_ROOT
    saved_env = {k: os.environ.get(k) for k in
                 ("CLASSLIVE_UPLOAD", "CLASSLIVE_NO_AUTO_UPDATE")}

    def _fake_popen(cmd, **kw):
        calls.append((list(cmd), dict(kw)))
        return object()

    try:
        upload.subprocess.Popen = _fake_popen
        # 空队列 -> 不起（盘上那份）
        paths.STATE_ROOT = tmp / "spawn-empty"
        os.environ["CLASSLIVE_UPLOAD"] = "1"
        os.environ.pop("CLASSLIVE_NO_AUTO_UPDATE", None)
        # ⚠️ 变异验证：把 `if not UploadQueue(...).pending: return False` 删掉 -> 这条红。
        check("⭐⭐ 队列空 -> 不 Popen", upload.spawn_exit_uploader() is False and not calls,
              str(calls))

        # 上传关 -> 不起
        st = _pending_queue(tmp / "spawn-on")
        paths.STATE_ROOT = st
        os.environ["CLASSLIVE_UPLOAD"] = "0"
        # ⚠️ 变异验证：把 `upload_enabled()` 那条闸删掉 -> 这条红。
        check("⭐⭐ 上传关着 -> 不 Popen", upload.spawn_exit_uploader() is False and not calls,
              str(calls))

        # 与自动更新同一道闸
        os.environ["CLASSLIVE_UPLOAD"] = "1"
        os.environ["CLASSLIVE_NO_AUTO_UPDATE"] = "1"
        # ⚠️ 变异验证：把 `CLASSLIVE_NO_AUTO_UPDATE` 那条删掉 -> 这条红（判据会真起进程）。
        check("⭐⭐ `CLASSLIVE_NO_AUTO_UPDATE` 设了 -> 不 Popen（与自动更新同一道闸）",
              upload.spawn_exit_uploader() is False and not calls, str(calls))

        # 正常：没有 taskpolicy 时就是四个参数的那条命令
        os.environ.pop("CLASSLIVE_NO_AUTO_UPDATE", None)
        upload.TASKPOLICY = tmp / "no-such-taskpolicy"
        ok = upload.spawn_exit_uploader()
        cmd, kw = calls[-1] if calls else ([], {})
        check("⭐⭐ 队列里有东西 -> Popen 起了（`start_new_session=True`）",
              ok is True and kw.get("start_new_session") is True, f"{ok} {kw}")
        check("⭐ 命令是 `<python> upload.py --drain --after-pid=<本进程>`（cwd 在仓库根）",
              cmd[:1] == [sys.executable] and cmd[-3:-1] == [str(HERE / "upload.py"), "--drain"]
              and cmd[-1] == f"--after-pid={os.getpid()}"
              and kw.get("cwd") == HERE, str(cmd))
        check("⭐ stdin/stdout/stderr 全 DEVNULL（不继承 App 的终端）",
              kw.get("stdin") == subprocess.DEVNULL
              and kw.get("stdout") == subprocess.DEVNULL
              and kw.get("stderr") == subprocess.DEVNULL, str(kw))

        # 有 taskpolicy 时前面加 `-b`（后台 QoS：低 CPU / 网络优先级）
        tp = tmp / "taskpolicy"
        tp.write_text("", encoding="utf-8")
        upload.TASKPOLICY = tp
        calls.clear()
        ok2 = upload.spawn_exit_uploader()
        cmd2 = calls[-1][0] if calls else []
        # ⚠️ 变异验证：把 `cmd[:0] = [TASKPOLICY, "-b"]` 删掉 -> 这条红。
        check("⭐⭐ `taskpolicy` 存在时带 `-b`（后台 QoS，省电）",
              ok2 is True and cmd2[:2] == [str(tp), "-b"]
              and cmd2[2] == sys.executable, str(cmd2))
    finally:
        upload.subprocess.Popen = orig_popen
        upload.TASKPOLICY = orig_taskpolicy
        paths.STATE_ROOT = orig_root
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ---------------------------------------------------------------- 端到端

class _Srv:
    """本机 PUT 收件端 —— 仿 Worker 的契约（**绝不向真 Worker 发请求**）。"""

    def __init__(self):
        self.reqs: list[dict] = []
        reqs = self.reqs

        class H(http.server.BaseHTTPRequestHandler):
            def do_PUT(self):                                 # noqa: N802
                n = int(self.headers.get("Content-Length") or 0)
                reqs.append({"path": self.path,
                             "auth": self.headers.get("Authorization"),
                             "body": self.rfile.read(n) if n else b""})
                self.send_response(200)
                self.end_headers()

            def log_message(self, *a):                        # noqa: D401
                pass

        self.s = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.s.server_address[1]}"
        threading.Thread(target=self.s.serve_forever, daemon=True).start()

    def close(self):
        self.s.shutdown()
        self.s.server_close()


def _queue_pending(state_root: pathlib.Path) -> int:
    try:
        got = json.loads(paths.upload_queue(root=state_root).read_text(encoding="utf-8"))
        return len(got.get("items") or [])
    except Exception:                                         # noqa: BLE001
        return -1


def _case_e2e(tmp: pathlib.Path) -> None:
    print("\n--- E4 ⭐⭐⭐ 端到端：真 `upload.py --drain` 子进程把 3 个文件传完 ---")
    home = tmp / "home"
    state_root = home / ".classlive"
    state_root.mkdir(parents=True)
    stem = "2026-10-19_100000_ECON10770"
    files = {
        f"{stem}.md": b"# transcript\n",
        f"{stem}.001.opus": b"OggS" + b"\0" * 40,
        f"{stem}.002.opus": b"OggS" + b"\0" * 20,
    }
    for name, body in files.items():
        (tmp / name).write_bytes(body)
    # 队列用**父进程里**的 UploadQueue 造（root 指向临时 HOME 那一份）
    q = upload.UploadQueue(lambda *a: True, root=state_root)
    n = q.enqueue([tmp / name for name in files], stem)
    check("⭐ 排进 3 个文件（判据自己的写端也在临时 HOME 里）", n == 3, str(n))

    srv = _Srv()
    env = {**os.environ, "HOME": str(home), "CLASSLIVE_UPLOAD": "1",
           "CLASSLIVE_UPLOAD_ENDPOINT": srv.url,
           "CLASSLIVE_UPLOAD_TOKEN": "e2e-token",
           "CLASSLIVE_NO_AUTO_UPDATE": "1"}
    child = None
    # 「App」= 一个还要活 1.5 s 的进程：drainer 必须等它退出才开始碰队列
    fake_app = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1.5)"])
    try:
        child = subprocess.Popen(
            [sys.executable, str(HERE / "upload.py"), "--drain", f"--after-pid={fake_app.pid}"],
            cwd=HERE, env=env,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)
        time.sleep(0.8)
        # ⚠️ 变异验证：删掉 `run_drain` 里的 `_wait_pid_gone` -> App 还活着时就发了、这条红。
        check("⭐⭐ App 还没退出时 drainer 一个对象都不发（不和 App 的线程抢队列文件）",
              fake_app.poll() is None and not srv.reqs, f"alive={fake_app.poll() is None} reqs={len(srv.reqs)}")
        fake_app.wait(timeout=10)
        # ⚠️⚠️ **父进程不等它** —— 只轮询终点（有界 30 s）。这正是 App 退出时的形状。
        end = time.time() + 30
        while time.time() < end:
            if len(srv.reqs) >= 3 and _queue_pending(state_root) == 0 and _lock_free(state_root):
                break
            time.sleep(0.05)
        got = {r["path"] for r in srv.reqs}
        key = upload.session_key(stem)
        want = {f"/{key}/transcript.md", f"/{key}/audio.001.opus", f"/{key}/audio.002.opus"}
        check("⭐⭐ 服务器收齐 3 个对象（父进程没等它，自己传完的）",
              got == want, str(sorted(got)))
        check("⭐⭐ 队列文件**清空**了（传完的条目出队）",
              _queue_pending(state_root) == 0, str(_queue_pending(state_root)))
        check("⭐⭐ 锁**已释放**（drainer 退出后没有僵尸锁）", _lock_free(state_root))
        check("⭐ 用的是环境变量指过去的端点 + token（令牌没漏进日志以外的任何地方）",
              all(r["auth"] == "Bearer e2e-token" for r in srv.reqs),
              str({r["auth"] for r in srv.reqs}))
        bodies = {r["path"]: r["body"] for r in srv.reqs}
        check("⭐ body 字节一致（md 那条）",
              bodies.get(f"/{key}/transcript.md") == files[f"{stem}.md"])
        # 日志与 `update.log` 同目录，但**在临时 HOME 里**
        log = home / "Library" / "Logs" / "ClassLive" / "upload.log"
        text = log.read_text(encoding="utf-8") if log.exists() else ""
        check("⭐ 日志落在 `~/Library/Logs/ClassLive/upload.log`（开始 + 结束原因 + 计数）",
              "开始" in text and "结束" in text and "empty" in text, text[-200:])
        check("⭐⭐ 日志里**没有 token**", "e2e-token" not in text)
    finally:
        if child is not None:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=10)
        srv.close()


# ---------------------------------------------------------------- 接线（AST）

def _calls(tree: ast.AST, name: str) -> list[ast.Call]:
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call)
            and (getattr(n.func, "id", None) == name
                 or getattr(n.func, "attr", None) == name)]


def _enclosing_func(tree: ast.AST, line: int):
    best = None
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = getattr(n, "end_lineno", n.lineno)
            if n.lineno <= line <= end and (best is None or n.lineno > best.lineno):
                best = n
    return best


def _ancestors_map(tree: ast.AST) -> dict:
    parents: dict[int, ast.AST] = {}
    for n in ast.walk(tree):
        for c in ast.iter_child_nodes(n):
            parents[id(c)] = n
    return parents


def _has_ancestor(node: ast.AST, parents: dict, typ) -> bool:
    cur = parents.get(id(node))
    while cur is not None:
        if isinstance(cur, typ):
            return True
        cur = parents.get(id(cur))
    return False


def _case_wiring() -> None:
    print("\n--- E5 ⭐⭐ 接线：退出顺序 / 启动先停 drainer / 面板那条 ---")
    main_src = (HERE / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(main_src)
    parents = _ancestors_map(tree)

    up_calls = _calls(tree, "_upload_on_exit")
    auto_calls = _calls(tree, "_auto_update_on_exit")
    # ⚠️ 变异验证：把 `_upload_on_exit()` 挪到 `_auto_update_on_exit()` 后面 -> 这条红。
    # ⚠️ 变异验证：去掉 try（直接裸调）-> "在 try 里" 那条红。
    check("⭐⭐ 退出段：`_upload_on_exit()` 在 `_auto_update_on_exit()` **之前**",
          bool(up_calls) and bool(auto_calls)
          and min(c.lineno for c in up_calls) < min(c.lineno for c in auto_calls),
          f"upload={[c.lineno for c in up_calls]} auto={[c.lineno for c in auto_calls]}")
    check("⭐⭐ `_upload_on_exit()` 被 try 包住（绝不影响退出）",
          all(_has_ancestor(c, parents, ast.Try) for c in up_calls),
          str([c.lineno for c in up_calls]))

    stop_calls = _calls(tree, "stop_drainer")
    check("⭐ 开机只调一次 `stop_drainer()`（别到处埋）", len(stop_calls) == 1,
          str([c.lineno for c in stop_calls]))
    if stop_calls:
        stop = stop_calls[0]
        fn = _enclosing_func(tree, stop.lineno)
        shared = [c for c in _calls(tree, "_shared_upload_queue")
                  if fn is not None and fn.lineno <= c.lineno <= getattr(fn, "end_lineno", 0)]
        # ⚠️ 变异验证：把 `_upmod.stop_drainer()` 挪到 `_q = _shared_upload_queue()`
        #    后面 -> 这条红（那就是"两个写者同时在写队列"）。
        check("⭐⭐ 启动段：`stop_drainer()` 在**构造共享队列之前**",
              bool(shared) and stop.lineno < min(c.lineno for c in shared),
              f"stop@{stop.lineno} shared={[c.lineno for c in shared]}")

    fn_src = None
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == "_upload_on_exit":
            fn_src = ast.get_source_segment(main_src, n) or ""
    check("⭐ `_upload_on_exit` 走的是 `upload.spawn_exit_uploader()`（不另写一套）",
          fn_src is not None and "spawn_exit_uploader" in fn_src, str(fn_src and fn_src[:80]))

    el_tree = ast.parse((HERE / "entry_launch.py").read_text(encoding="utf-8"))
    el_spawn = _calls(el_tree, "spawn_exit_uploader")
    el_auto = _calls(el_tree, "spawn_auto_update")
    el_parents = _ancestors_map(el_tree)
    # ⚠️ 变异验证：把 entry_launch 那三行删掉 -> 这条红（关面板就走的人永远补不完）。
    check("⭐⭐ 面板关闭（`CANCELLED`）那条也起 drainer（在 if 里）",
          bool(el_spawn) and bool(el_auto)
          and min(c.lineno for c in el_spawn) > min(c.lineno for c in el_auto)
          and all(_has_ancestor(c, el_parents, ast.If) for c in el_spawn),
          f"spawn={[c.lineno for c in el_spawn]} auto={[c.lineno for c in el_auto]}")


def main() -> int:
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="cl_exitup_"))
    try:
        _case_drain(tmp)
        _case_lock(tmp)
        _case_spawn(tmp)
        _case_e2e(tmp)
        _case_wiring()
        bad = [n for n, ok, _ in RESULTS if not ok]
        print("\n" + "=" * 60)
        print(f"{len(RESULTS) - len(bad)}/{len(RESULTS)} 通过")
        for n in bad:
            print(f"  ❌ {n}")
        return 1 if bad else 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
