#!/usr/bin/env python3
"""轻量客户端事件时间线（`<session>.client_events.jsonl`）。

## 为什么

`report.json` 只有聚合数字，`live.jsonl` 又有 2 MB；排障时最缺的是**谁在什么时刻
点了什么 / 哪一步失败过**。这里只记**事件名 + 少量标量字段**，一条一行，随
`extra.tar.gz` 上传（不单独发）。

## 约束

- **永不抛**：采集/收尾路上任何一步都不许因为它失败。
- **不记内容**：不写用户文字、不写路径、不写 key；只写事件名、计数、枚举、耗时。
- **有上限**：单文件超过 `MAX_BYTES` 就写一条 `events_truncated` 后停止；
  防止长课把诊断包撑爆（`make_extras` 单文件 16 MB 上限）。
"""
from __future__ import annotations

import json
import pathlib
import re
import threading
import time

MAX_BYTES = 4 * 1024 * 1024
TAIL = ".client_events.jsonl"

#: 字段名一旦命中这些词就不收（课号 / 路径 / 凭据都不进上传物）。
_DENY = ("course", "课号", "path", "file", "session", "key", "token", "secret", "email")
_SECRET_RE = re.compile(r"\b(cl-|apikey_|snx_(?:org_)?|gsk_|sk-|hf_)[A-Za-z0-9_\-]{8,}")
_PATH_RE = re.compile(
    r"(?:~|/(?:Users|private|var|tmp|Volumes|home|root|mnt)/|[A-Za-z]:\\Users\\)[^\s\"']+")
#: 已经写过 `events_truncated` 的文件（别每来一条事件都重读 4 MB）
_MARKED: set[str] = set()
_MARKED_LOCK = threading.Lock()


def _clean(v):
    if isinstance(v, str):
        s = _SECRET_RE.sub("***", v)
        return _PATH_RE.sub("<path>", s)[:200]
    return v


def path_for(session_path) -> pathlib.Path | None:
    try:
        p = pathlib.Path(str(session_path or ""))
        if not p.name:
            return None
        return p.with_name(p.stem + TAIL)
    except Exception:                                 # noqa: BLE001
        return None


def emit(session_path, kind: str, **fields) -> bool:
    """追加一条事件。返回是否写成；**永不抛**。字段只收标量。"""
    try:
        p = path_for(session_path)
        if p is None:
            return False
        try:
            if p.exists() and p.stat().st_size > MAX_BYTES:
                with _MARKED_LOCK:
                    if str(p) not in _MARKED and _mark_truncated(p):
                        _MARKED.add(str(p))
                return False
        except OSError:
            pass
        rec: dict = {"t": time.strftime("%Y-%m-%d %H:%M:%S"),
                     "epoch": round(time.time(), 3),
                     "kind": str(kind)[:60]}
        for k, v in (fields or {}).items():
            kk = str(k)[:40]
            if kk in ("t", "epoch", "kind"):
                continue                              # 元数据字段不许被调用方覆盖
            # ⚠️ 只有**字符串值**才可能是课号/路径/凭据；`files=3`、`has_course=True`
            #    这种计数/布尔别误伤（子串匹配会把它们一起吞掉）。
            if isinstance(v, str) and any(d in kk.lower() for d in _DENY):
                continue
            if isinstance(v, (str, int, float, bool)) or v is None:
                rec[kk] = _clean(v)
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(line)
        return True
    except Exception:                                 # noqa: BLE001
        return False


def _mark_truncated(p: pathlib.Path) -> bool:
    """超上限时留一条标记（只写一次）。返回是否写成 / 已存在。"""
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").rstrip().splitlines()
        if lines and '"events_truncated"' in lines[-1]:
            return True
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.strftime("%Y-%m-%d %H:%M:%S"),
                                "epoch": round(time.time(), 3),
                                "kind": "events_truncated",
                                "note": f"超过 {MAX_BYTES} 字节，后续事件未记录"},
                               ensure_ascii=False) + "\n")
        return True
    except Exception:                                 # noqa: BLE001
        return False
