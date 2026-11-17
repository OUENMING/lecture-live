#!/usr/bin/env python3
"""clctl —— 调试通道的客户端（服务端：`debug_ctl.py`，开关 `CLASSLIVE_DEBUG_CTL=1`）。只依赖标准库。

    clctl [--root <STATE_ROOT>] [--sock <路径>] [--json] [--timeout 秒] <命令>
    clctl ... --wait '<路径> <==|!=|>|<|>=|<=> <值>' [--timeout 10]
    clctl ... --record <输出.jsonl> [--every 0.5] [--for 60]

命令写成 `名字[:参数]`：`ping` · `help` · `state:listen,trans` · `events:since=12` · `mode:raw` · `stall:2` · `stop`。
标准输出只有 `data`（可接 `jq`）；错误在标准错误；`--json` 打印整个回复。

--wait 的路径：`trans.mode`（状态字段，点号逐层）；以 `events.` 开头则是**事件累计计数**
（`events.final >= 5`），所以能等「发生过几次」，不只等「现在是什么」。

退出码：0 成功 · 1 命令失败 · 2 没回复 / 超时 · 3 没有这个命令 · 64 用法错。
找 socket 的顺序：`--sock` → `$CLASSLIVE_CTL_SOCK` → `<root>/run/ctl.path` → `<root>/run/` 下最新的 `ctl-*.sock`。
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import pathlib
import re
import socket
import sys
import time

EXIT_OK, EXIT_FAIL, EXIT_NOREPLY, EXIT_UNKNOWN, EXIT_USAGE = 0, 1, 2, 3, 64
#: 服务端排队上限 5 s + 最长的 stall 5 s + 余量
DEFAULT_REQ_TIMEOUT = 12.0

_ids = itertools.count(1)


class NoReply(Exception):
    """连不上 / 没回复 / 回复不是 JSON。"""


class Usage(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Usage(message)


def default_root() -> pathlib.Path:
    return pathlib.Path.home() / ".classlive"


def find_socket(root=None, sock=None, env=None) -> str:
    env = os.environ if env is None else env
    if sock:
        return str(sock)
    if env.get("CLASSLIVE_CTL_SOCK"):
        return env["CLASSLIVE_CTL_SOCK"]
    run = pathlib.Path(root if root else default_root()) / "run"
    cands: list[str] = []
    try:
        line = (run / "ctl.path").read_text(encoding="utf-8").strip()
        if line:
            cands.append(line)
    except OSError:
        pass
    socks = []
    for p in run.glob("ctl-*.sock"):
        try:
            socks.append((p.stat().st_mtime, str(p)))
        except OSError:
            pass
    cands += [s for _, s in sorted(socks, reverse=True)]
    # 进程被强杀时 socket 文件会留下：挑第一个**连得上**的，不挑最新的死 socket
    for c in cands:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                s.connect(c)
            return c
        except OSError:
            continue
    if cands:
        raise NoReply(f"socket 都连不上（进程可能已退出）：{cands[:3]}")
    raise NoReply(f"找不到 socket（{run} 下没有 ctl.path / ctl-*.sock；App 没开或没设 CLASSLIVE_DEBUG_CTL=1）")


def request(sock_path: str, cmd: str, args: str = "", timeout: float = DEFAULT_REQ_TIMEOUT) -> dict:
    """发一条命令，等同 id 的回复。连不上 / 超时 / 乱码都抛 `NoReply`。"""
    rid = f"c{os.getpid()}-{next(_ids)}"
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(sock_path)
        s.sendall(json.dumps({"id": rid, "cmd": cmd, "args": args}).encode("utf-8") + b"\n")
        buf = b""
        while True:
            while b"\n" in buf:
                line, _, buf = buf.partition(b"\n")
                if not line.strip():
                    continue
                try:
                    reply = json.loads(line)
                except ValueError as e:
                    raise NoReply(f"回复不是 JSON：{e}") from None
                if not isinstance(reply, dict):          # --sock 指到别的服务上：null / 数组 / 字符串
                    raise NoReply("回复不是 JSON 对象")
                if reply.get("id") == rid:
                    return reply
            chunk = s.recv(65536)
            if not chunk:
                raise NoReply("连接被对端关了")
            buf += chunk
    except socket.timeout:
        raise NoReply(f"{timeout:g}s 内没有回复") from None
    except OSError as e:
        raise NoReply(f"{type(e).__name__}: {e}") from None
    finally:
        s.close()


def exit_code(reply: dict) -> int:
    if reply.get("ok"):
        return EXIT_OK
    code = (reply.get("error") or {}).get("code")
    if code == "unknown_command":
        return EXIT_UNKNOWN
    if code in ("timeout", "closed"):
        return EXIT_NOREPLY
    return EXIT_FAIL


# ---------- --wait ----------

_COND = re.compile(r"^\s*(\S+?)\s*(==|!=|>=|<=|>|<)\s*(.+?)\s*$")
_OPS = {
    "==": lambda a, b: a == b, "!=": lambda a, b: a != b,
    ">": lambda a, b: a > b, "<": lambda a, b: a < b,
    ">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b,
}


def parse_value(text: str):
    t = text.strip()
    if t in ("true", "false"):
        return t == "true"
    if t == "null":
        return None
    try:
        return int(t)
    except ValueError:
        pass
    try:
        return float(t)
    except ValueError:
        pass
    if len(t) >= 2 and t[0] == t[-1] and t[0] in "'\"":
        t = t[1:-1]
    return t


def parse_cond(text: str):
    m = _COND.match(text)
    if not m:
        raise Usage(f"--wait 要写成 '<路径> <==|!=|>|<|>=|<=> <值>'：{text!r}")
    path, op, raw = m.groups()
    want = parse_value(raw)
    if op not in ("==", "!=") and (isinstance(want, (bool, str)) or want is None):
        raise Usage("大小比较（> < >= <=）只能用数字；布尔 / 字符串只能 == / !=")
    return path, op, want


_MISSING = object()


def dig(obj, path: str):
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return _MISSING
    return cur


def _holds(actual, op: str, want) -> bool:
    if actual is _MISSING:
        return False
    if op in ("==", "!="):
        if isinstance(actual, bool) != isinstance(want, bool):
            return op == "!="
        if isinstance(actual, (int, float)) and isinstance(want, (int, float)) and not isinstance(actual, bool):
            return _OPS[op](float(actual), float(want))
        return _OPS[op](actual, want)
    if isinstance(actual, bool) or not isinstance(actual, (int, float)):
        return False
    return _OPS[op](float(actual), float(want))


def _fetch(sock_path: str, path: str, timeout: float):
    """取这条路径现在的值：`events.<kind>` 取累计计数，其余取 `state:<第一段>`。"""
    if path.startswith("events."):
        r = request(sock_path, "events", "limit=0", timeout)
        if not r.get("ok"):
            return r, _MISSING
        return r, (r["data"].get("counts") or {}).get(path[len("events."):], 0)
    r = request(sock_path, "state", path.split(".")[0], timeout)
    if not r.get("ok"):
        return r, _MISSING
    return r, dig(r["data"], path)


def wait_for(sock_path: str, cond: str, timeout: float, err, interval: float = 0.1) -> int:
    path, op, want = parse_cond(cond)
    deadline = time.monotonic() + timeout
    last = _MISSING
    while True:
        left = deadline - time.monotonic()
        try:                                     # 一次瞬时没回复（主线程正忙）≠ 条件不成立到底：接着轮询到 deadline
            r, last = _fetch(sock_path, path, max(DEFAULT_REQ_TIMEOUT, left + 1) if left > 0 else DEFAULT_REQ_TIMEOUT)
        except NoReply as e:
            if time.monotonic() >= deadline:
                err.write(f"clctl: 超时，{e}\n")
                return EXIT_NOREPLY
            time.sleep(interval)
            continue
        if not r.get("ok"):
            err.write(f"clctl: {(r.get('error') or {}).get('msg', '命令失败')}\n")
            return exit_code(r)
        if _holds(last, op, want):
            return EXIT_OK
        if time.monotonic() >= deadline:
            shown = "（路径不存在）" if last is _MISSING else json.dumps(last, ensure_ascii=False)
            err.write(f"clctl: 超时，{path} 当时是 {shown}\n")
            return EXIT_NOREPLY
        time.sleep(interval)


# ---------- --record ----------

def record(sock_path: str, out_path: str, every: float, total: float, err) -> int:
    n = 0
    t_end = time.monotonic() + total
    try:
        f = open(out_path, "w", encoding="utf-8")
    except OSError as e:
        err.write(f"clctl: 写不了 {out_path}：{e}\n")
        return EXIT_USAGE
    with f:
        while True:
            t = time.time()
            try:
                r = request(sock_path, "state", "", DEFAULT_REQ_TIMEOUT)
            except NoReply as e:
                err.write(f"clctl: 取样中断（已写 {n} 条）：{e}\n")
                return EXIT_NOREPLY                  # 截断的记录不能冒充成功
            if not r.get("ok"):
                err.write(f"clctl: {(r.get('error') or {}).get('msg', '命令失败')}\n")
                return exit_code(r)
            f.write(json.dumps({"t": round(t, 3), "state": r["data"]}, ensure_ascii=False) + "\n")
            f.flush()
            n += 1
            if time.monotonic() + every > t_end:
                return EXIT_OK
            time.sleep(every)


# ---------- 入口 ----------

def _parser() -> _Parser:
    ap = _Parser(prog="clctl", add_help=False)
    ap.add_argument("--root")
    ap.add_argument("--sock")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--timeout", type=float)
    ap.add_argument("--wait")
    ap.add_argument("--record")
    ap.add_argument("--every", type=float, default=0.5)
    ap.add_argument("--for", dest="duration", type=float, default=60.0)
    ap.add_argument("-h", "--help", action="store_true")
    ap.add_argument("command", nargs="*")
    return ap


def main(argv=None, out=None, err=None) -> int:
    out = out or sys.stdout
    err = err or sys.stderr
    argv = sys.argv[1:] if argv is None else list(argv)
    try:
        a = _parser().parse_args(argv)
        if a.help:
            out.write(__doc__ or "")
            return EXIT_OK
        modes = [bool(a.command), a.wait is not None, a.record is not None]
        if sum(modes) != 1:
            raise Usage("要且只要一个：<命令> / --wait / --record")
        if len(a.command) > 1:
            raise Usage(f"命令只能有一个（含空格要加引号）：{a.command}")
        if a.every <= 0 or a.duration <= 0 or (a.timeout is not None and a.timeout <= 0):
            raise Usage("--every / --for / --timeout 要大于 0")
        if a.wait is not None:
            parse_cond(a.wait)                  # 用法错先于连接错
        sock_path = find_socket(a.root, a.sock)
        if a.wait is not None:
            return wait_for(sock_path, a.wait, a.timeout if a.timeout else 10.0, err)
        if a.record is not None:
            return record(sock_path, a.record, max(0.05, a.every), a.duration, err)
        cmd, _, args = a.command[0].partition(":")
        if not cmd:
            raise Usage("命令不能为空")
        reply = request(sock_path, cmd, args, a.timeout or DEFAULT_REQ_TIMEOUT)
    except Usage as e:
        err.write(f"clctl: {e}\n用法见 `clctl --help`\n")
        return EXIT_USAGE
    except NoReply as e:
        err.write(f"clctl: {e}\n")
        return EXIT_NOREPLY
    if a.json:
        out.write(json.dumps(reply, ensure_ascii=False) + "\n")
    elif reply.get("ok"):
        out.write(json.dumps(reply.get("data"), ensure_ascii=False) + "\n")
    else:
        e = reply.get("error") or {}
        err.write(f"clctl: [{e.get('code')}] {e.get('msg')}\n")
    return exit_code(reply)


if __name__ == "__main__":
    sys.exit(main())
