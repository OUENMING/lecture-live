#!/usr/bin/env python3
"""云端翻译的分阶段超时：流中途卡住时要在 `READ_TIMEOUT_S` 内抛出，而不是等满总超时。

    ClassLive.app/Contents/MacOS/python tests/test_cloud_timeout.py

只连本机一个「发完响应头就不吭声」的假服务器；不碰真网络、不读写 `~/.classlive/`。
"""
from __future__ import annotations

import pathlib
import socket
import sys
import threading
import time

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import cloud_translator as CT                                        # noqa: E402


def _stalling_server() -> tuple[int, threading.Event]:
    """接一条连接，回 200 + SSE 头 + 一块数据，然后一直不发、不关。"""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    stop = threading.Event()

    def run():
        conn, _ = srv.accept()
        conn.recv(65536)
        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                     b"Transfer-Encoding: chunked\r\n\r\n")
        body = b'data: {"choices":[{"delta":{"content":"\xe4\xbd\xa0"}}]}\n\n'
        conn.sendall(f"{len(body):x}\r\n".encode() + body + b"\r\n")
        stop.wait(30)
        conn.close()
        srv.close()

    threading.Thread(target=run, daemon=True).start()
    return srv.getsockname()[1], stop


def main_() -> int:
    fail = []
    old = getattr(CT, "READ_TIMEOUT_S", None)
    CT.READ_TIMEOUT_S = 0.5
    port, stop = _stalling_server()
    try:
        tr = CT.CloudTranslator("k", "m", base_url=f"http://127.0.0.1:{port}", timeout=30.0)
        got, t0, err = [], time.monotonic(), None
        try:
            for d in tr._stream_chat([{"role": "user", "content": "x"}], max_tokens=8):
                got.append(d)
        except Exception as e:                                       # noqa: BLE001
            err = e
        dt = time.monotonic() - t0
        ok = err is not None and dt < 5.0 and got == ["你"]
        print(f"  {'✅' if ok else '❌'} ⭐⭐ 流中途卡住 -> {dt:.2f}s 内抛出（总超时 30 s；收到 {got}，{type(err).__name__}）")
        if not ok:
            fail.append("read-timeout")
        ok2 = getattr(tr._timeout, "connect", None) == min(30.0, getattr(CT, "CONNECT_TIMEOUT_S", -1))
        print(f"  {'✅' if ok2 else '❌'} 连接超时取 min(总超时, CONNECT_TIMEOUT_S)")
        if not ok2:
            fail.append("connect-timeout")
    finally:
        if old is not None:
            CT.READ_TIMEOUT_S = old
        stop.set()
    print(f"{2 - len(fail)}/2 通过")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main_())
