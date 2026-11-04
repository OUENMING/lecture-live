"""后台收尾任务的**所有权记录** —— 这节的笔记是谁在生成、还在不在。

为什么要有它（`docs/PLAN-note-background-20261103.md` §12 第 2、3、10 条）：收尾一旦
拆成独立进程，就会同时出现两个"谁来做"的问题 ——

1. **活着的 worker 被当成残局**：开机扫日志的两个恢复函数（`main.resume_pending_notes`、
   `upload.recover_pending`）看到 `note=pending` 就重跑 / 改名，第二节课一开就会重复
   生成、重复上传。
2. **同一节课两处并发写**：两个 worker 可能选中同一份笔记路径。

所以每节开工前先在这里**占位**（`claim`，`O_CREAT|O_EXCL`），跑的时候**打心跳**，
判活看 `pid` + 心跳时间（**不是**固定 10 分钟 —— 实测最坏 15:49，固定超时会把活
worker 当死）。恢复函数一律先问 `alive()`。

## 契约

- 一份 `~/.classlive/note-jobs/<stem>.json`，`stem` = 会话文件名（不含 `.md`）。
- `write` / `heartbeat` / `finish` **永不抛**、**原子**（tmp→rename）。
- `alive()` 的判据三条缺一不可：`state` 还没结束、`pid` 还活着、心跳没过期。
- 记录**保留不删**（一节一个约 200 B；删了就看不出这节有没有跑过）。要清理只挪不删。

⚠️ 全模块不碰 AppKit、不联网；`now` / `alive_fn` 可注入（判据全部可单测）。
"""
from __future__ import annotations

import json
import os
import pathlib
import time

#: 任务状态。`running` 之外的一律不算「还在跑」。
PENDING = "pending"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
STOPPED = "stopped"
_ENDED = frozenset({DONE, FAILED, STOPPED})

#: 心跳过期时间（秒）。worker 每 `HEARTBEAT_S` 打一次，所以正常工作时远小于它；
#: 过期 + pid 还在 = 卡死（网断了 / 线程死在 join 上）→ 让恢复函数接手。
#: ⚠️ 不能设成「固定 10 分钟」那种总时长上限：实测一节最长 15:49。
STALE_S = 180.0
HEARTBEAT_S = 15.0


def jobs_dir(*, root=None) -> pathlib.Path:
    """`~/.classlive/note-jobs/`（走 `paths`，不在别处再拼一次）。"""
    if root is not None:
        return pathlib.Path(root) / "note-jobs"
    import paths
    return paths.STATE_ROOT / "note-jobs"


def _stem_ok(stem: str) -> bool:
    """只收会话文件名那种 stem：非空、不含分隔符、不是 `.` / `..`。"""
    s = str(stem or "")
    return bool(s) and s not in (".", "..") and "/" not in s and "\\" not in s \
        and "\x00" not in s


def path(stem: str, *, root=None) -> pathlib.Path:
    """一份任务记录的路径。⚠️ stem 不合法 → 抛 `ValueError`（调用方本该先挡住）。"""
    if not _stem_ok(stem):
        raise ValueError(f"bad stem: {stem!r}")
    return jobs_dir(root=root) / f"{stem}.json"


def pid_alive(pid) -> bool:
    """`kill -0` 那条：只问「还在不在」，**不发信号**。

    ⚠️ `upload._pid_alive` 是同一条判据的既有副本（那边用于 drainer 锁）。
       两处都只有八行标准库调用，刻意不去互相 import：`upload` 拖着 httpx，
       而本模块要在恢复路径和子进程里都能被干净地 import。
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True                                  # 别人的进程（root）—— 活着
    except OSError:
        return False
    return True


def read(stem: str, *, root=None) -> dict:
    """读一份任务记录；没有 / 坏了 / stem 不合法一律当空。**永不抛。**"""
    try:
        raw = json.loads(path(stem, root=root).read_text(encoding="utf-8"))
    except Exception:                                # noqa: BLE001
        return {}
    return raw if isinstance(raw, dict) else {}


def write(stem: str, fields: dict, *, root=None, now=time.time) -> bool:
    """**合并**写入（不是整份覆盖）+ 刷新 `updated`。原子；失败吞掉。返回是否写成。"""
    try:
        cur = read(stem, root=root)
        for k, v in (fields or {}).items():
            cur[k] = v
        cur["stem"] = str(stem)
        cur["updated"] = float(now())
        p = path(stem, root=root)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(f".tmp{os.getpid()}")
        tmp.write_text(json.dumps(cur, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
        return True
    except Exception:                                # noqa: BLE001
        return False


def claim(stem: str, *, root=None, now=time.time, pid=None, **fields) -> bool:
    """**占位**：这节由我（`pid`）来做。`O_CREAT|O_EXCL` —— 先到先得，第二个拿到 False。

    ⚠️ 占位成功后写的是**整份新记录**（不沿用旧的 `done` / `failed`）。
    ⚠️ 已有一个**活着的**任务时返回 False，且**不碰**它的文件 —— 调用方据此决定
       是「跳过」还是「等它」。
    """
    try:
        p = path(stem, root=root)
        p.parent.mkdir(parents=True, exist_ok=True)
        if alive(stem, root=root, now=now):
            return False
        # 旧记录（已结束 / 心跳过期）不该挡住新任务：先挪开，再靠 O_EXCL 抢。
        # ⚠️ 抢不到就返回 False —— 说明另一个进程刚好也走到这里，让它跑。
        if p.exists():
            try:
                p.unlink()
            except OSError:
                pass
        try:
            fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            return False                             # 别人刚好抢先
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps({**fields, "stem": str(stem), "pid": int(pid or os.getpid()),
                                "state": RUNNING, "started_at": float(now()),
                                "updated": float(now())}, ensure_ascii=False))
        return True
    except Exception:                                # noqa: BLE001
        return False


def heartbeat(stem: str, *, root=None, now=time.time, **fields) -> bool:
    """打一次心跳（顺便带上 `stage` / `done` / `total` 这些进度字段）。

    ⚠️ 记录已经**结束**就不再写 —— 一个迟到的 worker 不许把 `done` 改回 `running`
       （否则恢复函数会以为它还在跑，笔记反而补不出来）。
    """
    if str(read(stem, root=root).get("state") or "") in _ENDED:
        return False
    return write(stem, {"state": RUNNING, **fields}, root=root, now=now)


def finish(stem: str, state: str = DONE, *, root=None, now=time.time, **fields) -> bool:
    """收工：`state` 落到 `done` / `failed` / `stopped`，以后 `alive()` 一律 False。"""
    if state not in _ENDED:
        state = FAILED
    return write(stem, {**fields, "state": state}, root=root, now=now)


def alive(stem: str, *, root=None, now=time.time, alive_fn=None) -> bool:
    """这节还有人在跑吗？`state` 未结束 + `pid` 活着 + 心跳没过期，三条**缺一不可**。

    ⚠️ 心跳过期而 pid 还在 = 卡死（网络断了 / 线程死在 join 上），也算「不在跑」——
       否则一次卡死会让这节的笔记永远补不出来（那正是固定 10 分钟超时要解决的问题）。
    """
    j = read(stem, root=root)
    if not j or str(j.get("state") or "") in _ENDED:
        return False
    pid = j.get("pid")
    if not pid:
        return False
    check = alive_fn or pid_alive
    try:
        if not check(pid):
            return False
    except Exception:                                # noqa: BLE001
        return False
    try:
        age = float(now()) - float(j.get("updated") or 0.0)
    except (TypeError, ValueError):
        return False
    return age < STALE_S


def stop(stem: str, *, root=None, timeout: float = 6.0, now=time.time) -> bool:
    """请后台任务**停下来**（面板上的「停止」）。返回「真的发出信号了」。

    ⚠️ **只给任务记录里那个 pid 发信号**，绝不按进程名杀 —— 同
       `upload.stop_drainer` 那条既有纪律（名字匹配会连带打死别的实例）。
    ⚠️ 先 `SIGTERM`：worker 装了 handler，会在**批次边界**停、并把已拿到的部分写成笔记
       （`obsidian_writer.close(cancel=…)`）。等 `timeout` 还不退才 `SIGKILL`。
    ⚠️ 永不抛：它可能跑在 UI 回调里。
    """
    import signal
    try:
        j = read(stem, root=root)
        if str(j.get("state") or "") in _ENDED:
            return False
        pid = int(j.get("pid") or 0)
        if not pid or pid == os.getpid() or not pid_alive(pid):
            # 已经不在了 —— 顺手把状态收成 stopped（不然 `alive()` 会一直
            # 看到 running + 旧心跳，等 STALE_S 才自己过期）。
            finish(stem, STOPPED, root=root, now=now)
            return False
        os.kill(pid, signal.SIGTERM)
        deadline = float(now()) + max(0.0, float(timeout))
        import time as _t
        while _t.time() < deadline:
            if not pid_alive(pid):
                return True
            _t.sleep(0.15)
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
        return True
    except Exception:                                    # noqa: BLE001
        return False


def active(*, root=None, now=time.time) -> list:
    """现在还在跑的 stem 列表（按心跳时间倒序）。给面板「后台任务」行用。"""
    out = []
    try:
        d = jobs_dir(root=root)
        if not d.is_dir():
            return out
        for p in d.glob("*.json"):
            stem = p.stem
            if alive(stem, root=root, now=now):
                j = read(stem, root=root)
                out.append({"stem": stem, "stage": j.get("stage") or "",
                            "done": j.get("done"), "total": j.get("total"),
                            "started_at": j.get("started_at"), "updated": j.get("updated")})
    except Exception:                                # noqa: BLE001
        return out
    return sorted(out, key=lambda r: -(r.get("updated") or 0))
