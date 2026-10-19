"""`websockets.sync.client` 的唯一取法：本机装了用本机的，没装上就用仓库自带的 `vendor/websockets`。

⭐ 3.10.1：一位测试者 3.10.0 自动更新拉下了代码、补依赖（`uv pip install`）却没成 →
   缺 `websockets` → Soniox 连不上，整节课静默退回 Groq。补依赖为什么失败要看他本机的
   `update.log`（3.10.1 起随诊断包上传）；在那之前，代码自带一份纯 Python 的
   websockets 17.2（BSD-3，`vendor/websockets/LICENSE`；去掉了可选的 C 加速 `speedups.so`，
   掩码改走纯 Python —— 实时流每秒几 KB，差别量不出来）。
⚠️ 本机装了**能用**的版本就永远优先；只有它取不到（没装 / 太旧没有 `sync`）时，
   才把 vendor 放到 `sys.path` **最前**再取 —— 放末尾的话，一份太旧的本机版本会再次挡住它。
⚠️ `requirements.txt` 里的 `websockets>=15` 不删 —— 正常机器照旧走装好的那份。
"""
from __future__ import annotations

import pathlib
import sys

VENDOR = pathlib.Path(__file__).resolve().parent / "vendor"


def client():
    """返回 `websockets.sync.client` 模块。两份都拿不到时照常抛 ImportError。"""
    try:
        import websockets.sync.client as wsc
        return wsc
    except ImportError:
        pass
    v = str(VENDOR)
    if v in sys.path:
        sys.path.remove(v)
    sys.path.insert(0, v)
    # 第一次 import 失败可能留下半截的 `websockets` 包对象 —— 清掉再从 vendor 取
    for k in [m for m in sys.modules if m == "websockets" or m.startswith("websockets.")]:
        sys.modules.pop(k, None)
    import websockets.sync.client as wsc
    return wsc
