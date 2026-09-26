#!/usr/bin/env python3
"""单实例锁 —— 同一时间只允许一个 ClassLive 在跑。

## 为什么用 `fcntl.flock` 而不是「pidfile 存不存在」

pidfile 那套在进程被 `kill -9` 之后会留下一个**死 pid 的文件**，
下次启动会被一个已经不存在的进程"锁死"，得自己写清理逻辑
（`update.py` 里那个锁就为此带了 `LOCK_STALE_S` 过期时间）。

`flock` 是**内核托管**的：进程不管怎么死（含 SIGKILL、崩溃、断电），
锁都会自动释放。2026-09-26 实测确认。

⚠️ macOS **没有** `flock` 命令（BSD 系不带），只能用 Python 的 `fcntl.flock`。

## 为什么 `.app` 双击那条路其实不需要它

LaunchServices 自己保证同一个 bundle 只起一个实例（连续 `open` 两次实测只有 1 个进程）。
这个锁防的是**它管不到的那条路**：

- 终端里 `cl` 敲了两次
- `.app` 已经在跑，又从终端起了一个
- `cl file` 转录音频时，另一个 `cl` 在上课

⚠️ 反过来说：`.app` 那条路**不能靠这个锁** —— 见 `make-app.sh` 里
「不要复用 cl-bg.py 的 daemonize」那一段（双 fork 会让 LaunchServices 认不出实例）。
"""
from __future__ import annotations

import fcntl
import os
import pathlib

# ⚠️ 与 update.py / cl-bg.py 用同一个目录：不污染仓库、不进 git
STATE_DIR = pathlib.Path.home() / "Library" / "Logs" / "ClassLive"
LOCK_NAME = "instance.lock"


def acquire(path: pathlib.Path | None = None):
    """尝试拿实例锁。

    返回 `(lock_file, holder_pid)`：
      · 拿到了   -> `(打开的文件对象, None)`
      · 已被占用 -> `(None, 占用者的 pid 或 None)`

    ⚠️ **返回值里的文件对象必须被调用方一直持有到进程结束** ——
       它一旦被 GC 回收，文件描述符关闭，锁就跟着释放了。
       所以别写成 `acquire()` 之后不接返回值。

    ⚠️ 「被占用」与「连锁文件都建不出来」在这个返回值里**分不开**（都是 `None`）。
       只想知道状态、或要区分这两种情况，用 `probe()`。
    """
    p = pathlib.Path(path) if path else (STATE_DIR / LOCK_NAME)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        f = open(p, "a+", encoding="utf-8")
    except OSError:
        # 连锁文件都建不出来（磁盘满/权限）—— 当作"没锁"，不因此拦住上课
        return None, None
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        # 被占用了。读一下里面写的 pid，好告诉用户"是谁在跑"
        holder = None
        try:
            f.seek(0)
            holder = int((f.read() or "").strip() or 0) or None
        except (OSError, ValueError):
            pass
        f.close()
        return None, holder
    # 拿到了 —— 写下自己的 pid（纯为诊断，不参与判定）
    try:
        f.seek(0)
        f.truncate()
        f.write(str(os.getpid()))
        f.flush()
    except OSError:
        pass
    return f, None


def release(lock) -> None:
    """放掉 `acquire()` 拿到的锁。拿 `None`（没拿到）是合法的空操作。"""
    if lock is None:
        return
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        lock.close()
    except OSError:
        pass


def probe(path: pathlib.Path | None = None) -> tuple[str, int | None]:
    """只回答「现在有没有人在跑」—— **不改任何东西**。

    返回 `(state, holder_pid)`，state ∈ `{"free", "held", "unknown"}`。

    ⚠️ **为什么不复用 `acquire()`**，两条都是实测出来的：
      · `acquire()` 在锁文件不存在时会**创建**它（`mkdir` + `open(..., "a+")`）——
        一个只想知道状态的调用不该在文件系统上留痕
      · `acquire()` 把「被占用」和「建不出锁」**压成同一个返回值** `(None, …)`，
        照它写的探针会把「磁盘满/权限不够」报成「**正在上课**」—— 方向正好反了，
        而本仓库的规矩是「凡是要给别的脚本看的判据，先问它出错时倒向哪边」
    """
    p = pathlib.Path(path) if path else (STATE_DIR / LOCK_NAME)
    try:
        f = open(p, "r", encoding="utf-8")       # ⚠️ 只读打开，不存在就 FileNotFoundError
    except FileNotFoundError:
        return "free", None                      # 文件都没有 = 从没人拿过 = 空
    except OSError:
        return "unknown", None                   # 建不出/读不出 —— 当作没锁，别拦人
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        holder = None
        try:
            f.seek(0)
            holder = int((f.read() or "").strip() or 0) or None
        except (OSError, ValueError):
            pass
        f.close()
        return "held", holder
    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    f.close()
    return "free", None


def is_held(path: pathlib.Path | None = None) -> tuple[bool, int | None]:
    """`probe()` 的布尔投影 —— 「现在是不是有人在跑」。

    ⚠️ 与 `acquire()` 的语义区别：`acquire()` 拿到就**一直持有**（那才是互斥）；
    这里探完就放，**不持有、不创建、不戳 pid**。

    ⚠️ `"unknown"`（锁文件建不出）算 **`False`** —— 与 `acquire()` 的降级方向一致：
    锁坏掉时不该拦住任何事。

    ⚠️ **为什么只是告知、不做成互斥**：术语表只在启动时读一次
    （`translator.py` 的 `Translator.__init__`），所以 prep 和上课同时跑
    **没有竞态可防**。做成互斥反而会让「开课前想补两个词」被一句「正在上课」挡在门外
    —— 而那正是要它跑的场合。
    """
    state, holder = probe(path)
    return state == "held", holder


def describe_holder(pid: int | None) -> str:
    """把占用者描述成一句人话。"""
    if not pid:
        return "已经有一个 ClassLive 在跑"
    return f"已经有一个 ClassLive 在跑（进程 {pid}）"
