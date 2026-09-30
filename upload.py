#!/usr/bin/env python3
"""课后上传 —— **队列 / 退避 / 幂等 / 脱敏**。给测试模式用。

    from upload import UploadQueue
    q = UploadQueue(send=my_send)                 # 生产：rsync 到 VPS
    q.enqueue([pathlib.Path("a.opus"), ...], stem="2026-09-30_154742_ECON10770")
    q.pump()                                      # 试一轮；失败就留着

## 形状（照成熟做法：Glean / Firefox 那套）

```
收尾 → enqueue（**落盘**）→ pump（课后立刻试一次，失败留到下次启动）
                                ↓ 失败
                          退避 1 → 2 → … → **封顶 120 min**；**14 天过期**丢弃
```

## 四条纪律

1. ⚠️ **队列落盘、不在内存** —— 关机 / 崩溃 / 换个进程启动之后还得在。
2. ⚠️ **写队列用 `tmp → rename`** —— 半截文件**不进队列**（Glean 那条）。
3. ⚠️ **幂等靠 `key`**（会话名哈希前缀 + 原文件名）—— 同一节课重传**覆盖**同一批
   对象，不会变成两份。⚠️ **不能用内容哈希**：`report.json` 里有 `generated`
   时间戳，每次跑都不一样 → 内容哈希会把同一节课**变成两个对象**。
4. ⚠️ **传输无关**：`send(local, key) -> bool` 是唯一的洞。
   生产传 `rsync` 到 VPS；判据传一个**本地目录**（不碰真服务器、不需要凭据）。

## ⚠️ 为什么 key 是「会话名的哈希」

原文件名 `2026-09-24_203440_ECON10770` 含**课号 + 精确时间戳** = 一份课表。
哈希前缀一次解决三件事：**不泄漏元数据** · 重传幂等 · **整节删得掉**（一个前缀
= 一节课）。
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import time

import paths

#: 重试退避的两端（秒）。⚠️ 上界照 Firefox Telemetry 那套（1 min → 120 min）。
BACKOFF_MIN_S = 60.0
BACKOFF_MAX_S = 7200.0

#: 超过这么久还没传成的条目**丢弃**（同 Firefox 的 ping 最大年龄 14 天）。
#: ⚠️ 它同时挡在服务端保留期前面：手动清掉的对象，本机队列**不该还想重传**。
EXPIRE_S = 14 * 86400

#: 值里含**绝对路径**、上传前要去掉的键。⚠️ 不做这一步就会把**用户名和目录结构**
#: 一起传上去 —— 见 `docs/PLAN-test-mode.md` §6.6。
_SCRUB_KEYS = ("session_file", "note_file", "audio_files")


def _scrub_one(v: str) -> str:
    """一个路径 → `<path>/<文件名>`。**跟前缀无关**。

    ⚠️⚠️ 第一版只换 `Path.home()` 开头的那种（`/Users/owen/...`）。
       那在**生产**上够用（`sessions/` 与 vault 都在 home 下），但
       **换个位置就漏** —— 2026-09-30 端到端实测：隔离跑的路径在 `/tmp/...`，
       于是**一个字节都没换**，真传上去的还是完整目录结构。
       → 改成「只要是绝对路径就换成 `<path>/<名字>`」：不依赖 home 在哪，
         而且**连目录结构都不泄漏**（比只换用户名更彻底）。
    ⚠️ 不是绝对路径的（空串 / 相对路径 / 别的形状）**原样返回**，不硬套。
    """
    p = pathlib.PurePath(v)
    if not v or not p.is_absolute():
        return v
    return f"<path>/{p.name}" if p.name else "<path>"


def session_key(stem: str) -> str:
    """会话名 → 对象前缀（16 位十六进制）。⚠️ **同名永远同前缀** —— 幂等的根据。"""
    return hashlib.sha256(str(stem).encode("utf-8")).hexdigest()[:16]


def scrub(obj):
    """递归把已知键里的绝对路径换成 `<path>/<文件名>`。⚠️ **只改值，不改结构**。"""
    if isinstance(obj, dict):
        # ⚠️⚠️ **名单里的键可能是字符串、也可能是字符串列表**（`audio_files` 就是列表）。
        #    第一版只判 `isinstance(v, str)`，列表就落到下面那个递归分支去了 ——
        #    而递归对**裸字符串原样返回**，于是那一格**一个字节没换**。
        #    （2026-09-30 端到端实测：`session_file` 换了、`audio_files` 没换。）
        out = {}
        for k, v in obj.items():
            if k in _SCRUB_KEYS:
                if isinstance(v, str):
                    out[k] = _scrub_one(v)
                elif isinstance(v, list):
                    out[k] = [_scrub_one(x) if isinstance(x, str) else scrub(x)
                              for x in v]
                else:
                    out[k] = scrub(v)
            else:
                out[k] = scrub(v)
        return out
    if isinstance(obj, list):
        return [scrub(x) for x in obj]
    return obj


class UploadQueue:
    """待上传的东西 + 退避状态。**线程不安全**（调用方自己串行化）。"""

    def __init__(self, send, *, root=None, clock=time.monotonic, now=time.time):
        self._send = send
        self._clock = clock
        self._now = now
        self._path = paths.upload_queue(root=root)
        self._ledger = paths.upload_ledger(root=root)
        self._items = self._load()

    # ------------------------------------------------------------ 读写
    def _load(self) -> list:
        try:
            got = json.loads(self._path.read_text(encoding="utf-8"))
            return list(got.get("items") or [])
        except Exception:                                     # noqa: BLE001
            return []                                         # 没有/坏了 -> 空队列

    def _save(self) -> None:
        """⚠️ **tmp → rename** —— 半截文件不进队列。"""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"_v": 1, "items": self._items},
                                      ensure_ascii=False), encoding="utf-8")
            tmp.replace(self._path)
        except Exception:                                     # noqa: BLE001
            pass                                              # 存不下就少一份数据，别抛

    def _save_ledger(self, stem: str, key: str) -> None:
        """台账：`会话名 -> 前缀`。⚠️ **没有它就删不掉远端那一节课**（哈希不可逆）。"""
        try:
            cur = {}
            if self._ledger.exists():
                cur = json.loads(self._ledger.read_text(encoding="utf-8"))
            cur[str(stem)] = {"key": key, "at": time.strftime("%Y-%m-%d %H:%M:%S")}
            tmp = self._ledger.with_suffix(".tmp")
            tmp.write_text(json.dumps(cur, ensure_ascii=False, indent=1),
                           encoding="utf-8")
            tmp.replace(self._ledger)
        except Exception:                                     # noqa: BLE001
            pass

    # ------------------------------------------------------------ 入队
    def enqueue(self, files, stem: str) -> int:
        """把一节课的文件排进队列。返回**排进去几个**（已不存在的跳过）。

        ⚠️ 同一节课**重复入队会合并**（按 `key`）—— 不然崩溃后重跑会排两遍。
        """
        key = session_key(stem)
        if key in {i.get("key") for i in self._items}:
            return 0                                          # 已经排过 -> 合并，不重复
        paths_ok = [str(p) for p in files if pathlib.Path(p).is_file()]
        if not paths_ok:
            return 0
        self._items.append({"stem": str(stem), "key": key, "files": paths_ok,
                            "tries": 0, "next_at": 0.0, "created": self._now()})
        self._save()
        self._save_ledger(stem, key)
        return len(paths_ok)

    # ------------------------------------------------------------ 出队
    def _prepare(self, local: pathlib.Path) -> pathlib.Path | None:
        """发之前要加工的场合（脱敏）。⚠️ 加工出来的是**临时文件**，调用方负责删。

        ⚠️ 只对 `.json` 动手 —— 音频里没有路径，没必要多拷一份（9 MB 呢）。
        """
        if local.suffix.lower() != ".json":
            return local
        try:
            data = json.loads(local.read_text(encoding="utf-8"))
        except Exception:                                     # noqa: BLE001
            return local                                      # 解不开就原样发，别卡住
        tmp = local.with_name(local.name + ".scrubbed")
        try:
            tmp.write_text(json.dumps(scrub(data), ensure_ascii=False),
                           encoding="utf-8")
            return tmp
        except Exception:                                     # noqa: BLE001
            return local

    def pump(self, max_items: int = 1) -> dict:
        """试一轮。返回 `{sent, failed, expired, pending}`。

        ⚠️ **逐文件失败分开**（Sentry 那条）：一节课里某个文件挂了，
           下一次重试从**整条**再来一遍 —— 但**别的条目不受影响**。
           （对象名是确定的，所以重发已经成功的那几个只是覆盖，不会重复。）
        ⚠️ 一次只试 `max_items` 条（默认 1）—— 回连后一次性倒空会被服务端风控。
        """
        out = {"sent": 0, "failed": 0, "expired": 0, "pending": 0}
        now = self._now()
        keep = []
        for it in self._items:
            if now - float(it.get("created") or 0) > EXPIRE_S:
                out["expired"] += 1                           # 太久没传成 -> 丢
                continue
            keep.append(it)
        self._items = keep

        tried = 0
        for it in list(self._items):
            if tried >= max_items:
                break
            if now < float(it.get("next_at") or 0):
                continue                                      # 还没到重试时刻
            tried += 1
            ok = True
            for f in list(it.get("files") or []):
                local = pathlib.Path(f)
                if not local.is_file():
                    continue                                  # 文件没了 -> 跳过（不是失败）
                staged = self._prepare(local)
                try:
                    good = bool(self._send(staged, f"{it['key']}/{local.name}"))
                except Exception:                             # noqa: BLE001
                    good = False                              # send 不许抛，但兜一层
                finally:
                    if staged is not None and staged != local:
                        try:
                            staged.unlink(missing_ok=True)
                        except Exception:                     # noqa: BLE001
                            pass
                if not good:
                    ok = False
                    break                                     # 这条先放下，下次整条重试
            if ok:
                self._items.remove(it)
                out["sent"] += 1
            else:
                it["tries"] = int(it.get("tries") or 0) + 1
                # ⚠️ 指数退避、**封顶 120 min**（照 Firefox 那套）。
                it["next_at"] = now + min(BACKOFF_MIN_S * (2 ** (it["tries"] - 1)),
                                          BACKOFF_MAX_S)
                out["failed"] += 1
        self._save()
        out["pending"] = len(self._items)
        return out

    # ------------------------------------------------------------ 只读
    @property
    def pending(self) -> int:
        return len(self._items)

    def items(self) -> list:
        """只读快照（判据用）。⚠️ 别再往里写。"""
        return [dict(i) for i in self._items]


def rsync_path(remote_dir: str, sub: str) -> str:
    """`--rsync-path` 那个串。**抽出来只为一件事：让判据能钉住它。**

    ⚠️⚠️ **`$HOME` 不许转义**（不能写成 `\\$HOME`）。这条是**真跑才发现的**：

    · 走 **argv**（`subprocess.run([...])`）时**没有本地 shell** ——
      `\\$` 会**原样**传到远端，远端 shell 把它当**转义过的美元**，
      于是 `mkdir -p` 拿到的是**字面量 `$HOME`** → 目录没建 → rsync 报
      `change_dir ... No such file or directory`（**rc=3**）。
    · 而手工在 zsh 里敲 `--rsync-path="mkdir -p \\$HOME/… && rsync"` 时，
      **本地 shell 先吃掉那个反斜杠** → 远端拿到 `$HOME` → 正常展开 ✓。
    → **同一条命令，手敲能过、代码里不行。** 2026-09-30 实测两种写法：
      转义版 rc=3（失败）、不转义版 rc=0（成功）。

    ⚠️ 为什么要 `mkdir -p`：**本机的 rsync 是 openrsync**（macOS 新的替代实现，
       报 `2.6.9 compatible`），**不支持 `--mkpath`** → 目标目录不存在就直接失败。
       放进 `--rsync-path` 是**一次连接**（实测 0.61s vs 分开 ssh+rsync 的 1.07s）。
    """
    return (f"mkdir -p $HOME/{remote_dir}/{sub} && rsync" if sub else "rsync")


#: 崩溃标记的后缀。`TestSession` 开录时写、`finish()` 时删。
#: ⚠️ **没有它，「崩溃那一整类数据」会全丢** —— 而崩溃现场恰恰是最该看的。
PENDING_TAIL = ".pending.json"


def recover_pending(sessions_dir, send, *, root=None, clock=time.monotonic, now=time.time) -> dict:
    """开机扫残留的 `.pending.json`（上次跑测试模式**没收尾**），把它们排进队列。

    返回 `{found, queued}`。

    ⚠️⚠️ **为什么必须有这一步**：`TestSession.finish()` 跑不到（崩溃 / 强杀 / 关机）
       → **没有 report**，而 `enqueue` 是在 `finish()` 里调的 → 那一整类数据全丢。
       `.pending` 是**开录时就写好**的，所以它比 `finish()` 活得久。
    ⚠️ **不删标记，改名**（`.pending.json` → `.recovered.json`）——
       删了就再也看不出"这一节崩过"。⚠️ 而改名之后不会重复触发。
    ⚠️ **只排真的存在的文件** —— 崩溃时音频可能是半截的，那也是数据。
    """
    import json as _json
    sd = pathlib.Path(sessions_dir)
    out = {"found": 0, "queued": 0}
    if not sd.is_dir():
        return out
    q = UploadQueue(send, root=root, clock=clock, now=now)
    for mk in sorted(sd.glob("*" + PENDING_TAIL)):
        out["found"] += 1
        stem = mk.name[: -len(PENDING_TAIL)]
        files = sorted(sd.glob(stem + ".*.opus")) + sorted(sd.glob(stem + ".*.wav"))
        rep = sd / (stem + ".report.json")
        if rep.is_file():
            files.append(rep)
        sess = sd / (stem + ".md")
        if sess.is_file():
            files.append(sess)
        if files:
            # 写一份 meta，标明这次是**崩过的**
            meta = sd / (stem + ".meta.json")
            try:
                info = {"crash_recovered": True, "stem": stem,
                        "recovered_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "files": [f.name for f in files]}
                meta.write_text(_json.dumps(info, ensure_ascii=False, indent=1),
                                encoding="utf-8")
                files.append(meta)
            except Exception:                                 # noqa: BLE001
                pass
            out["queued"] += q.enqueue(files, stem)
        try:
            mk.replace(mk.with_name(mk.name.replace(PENDING_TAIL, ".recovered.json")))
        except Exception:                                     # noqa: BLE001
            pass
    return out


def make_rsync_send(host: str, remote_dir: str, *, timeout: float = 300.0):
    """造一个「rsync 到 VPS」的 `send`。**生产用的那一个**。"""
    import subprocess

    def send(local: pathlib.Path, key: str) -> bool:
        rel = str(key)
        sub = rel.rsplit("/", 1)[0] if "/" in rel else ""
        rp = rsync_path(remote_dir, sub)
        try:
            pr = subprocess.run(
                ["rsync", "-a", "--partial", f"--rsync-path={rp}",
                 str(local), f"{host}:{remote_dir}/{rel}"],
                capture_output=True, timeout=timeout)
            return pr.returncode == 0
        except Exception:                                     # noqa: BLE001
            return False
    return send


def make_local_send(base: str, *, root=None):
    """造一个「发到本地目录」的 `send` —— **判据用这个**（不碰真服务器、不要凭据）。"""
    base_p = pathlib.Path(base)

    def send(local: pathlib.Path, key: str) -> bool:
        dst = base_p / key
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(pathlib.Path(local).read_bytes())
            return True
        except Exception:                                     # noqa: BLE001
            return False
    return send
