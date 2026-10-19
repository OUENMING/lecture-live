#!/usr/bin/env python3
"""课后上传 —— **队列 / 退避 / 幂等 / 脱敏**。给测试模式用。

    from upload import UploadQueue
    q = UploadQueue(send=my_send)                 # 生产：HTTPS PUT 到 Cloudflare Worker
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
   生产传 **HTTPS PUT** 到 Cloudflare Worker（**用户零配置** —— 见
   `docs/PLAN-zero-config-upload.md`）；判据传一个**本地目录**或**本机 HTTP 服务器**
   （不碰真服务器、不需要凭据）。

## ⚠️ 为什么 key 是「会话名的哈希」

原文件名 `2026-09-24_203440_ECON10770` 含**课号 + 精确时间戳** = 一份课表。
哈希前缀一次解决三件事：**不泄漏元数据** · 重传幂等 · **整节删得掉**（一个前缀
= 一节课）。
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import threading
import time

import paths

#: 重试退避的两端（秒）。⚠️ 上界照 Firefox Telemetry 那套（1 min → 120 min）。
BACKOFF_MIN_S = 60.0
BACKOFF_MAX_S = 7200.0

#: 睡醒后**最多**把同一条目提前几次（`retry_now`）。⚠️ 有界是纪律：
#: 睡一觉就无限重推 = 拿服务端当靶子打；超过它退回正常退避（1 → 120 min）。
WAKE_RETRY_MAX = 3

#: 超过这么久还没传成的条目**丢弃**（同 Firefox 的 ping 最大年龄 14 天）。
#: ⚠️ 它同时挡在服务端保留期前面：手动清掉的对象，本机队列**不该还想重传**。
EXPIRE_S = 14 * 86400


def upload_enabled() -> bool:
    """上传总开关（`CLASSLIVE_UPLOAD=0` 关）。⚠️ **唯一定义点**。

    三个读端：`main.start_upload`（关着就不排队）、`TestSession.finish`
    （关着时没人来排上传 → journal 自己撤，见 `journal_upload_done`）、
    `recover_pending`（关着时**不排也不改名** —— 留着残局，等开着的那次启动再补）。
    """
    return os.environ.get("CLASSLIVE_UPLOAD", "1") == "1"


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
        #: ⚠️ 列表的读改写互斥（2026-10-01 审查 F25）：实例被收成进程级唯一一份之后，
        #:    「启动补传」与「收尾入队」两条线程会同时碰 `_items`。**锁里绝不做 I/O**
        #:    —— `enqueue` 在**主线程**被调，锁里带网络会让主线程等一个 rsync 超时。
        self._lock = threading.Lock()
        #: ⭐ 「判重 + 发送」的**原子占位**（2026-10-18 审查 D7）：共享实例上
        #:    `start_upload` 与 `retry_pending_uploads` 会各起一条 `pump` 线程，两者
        #:    可能同时读到 `sent_files` 里还没有该对象 → 各发一遍同一个对象（白传 9 MB）。
        #:    发送前先把 `(key, remote)` 占住、发完/失败再放掉；**只在内存里**（不落盘）
        #:    —— 进程死了占位自然消失，队列文件里不会留下永远清不掉的枷锁。
        self._inflight: set = set()

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
        except Exception as e:                                # noqa: BLE001
            # ⚠️ **不许静默**（2026-10-12 全仓审查）：调用方拿到的是「排进去几个」这个
            #    **成功计数**，而写盘失败一声不响 → 重启后条目凭空消失、谁都不知道。
            #    仍然不抛（落盘失败不该带崩收尾），但必须留下一行。
            print(f"⚠ 上传队列没写进盘（{type(e).__name__}: {str(e)[:60]}）"
                  f" —— 这次入队的条目重启后会丢")

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
        except Exception as e:                                # noqa: BLE001
            # ⚠️ 台账比队列更要紧：**没有它就删不掉远端那一节课**。静默 = 以后没人能补救。
            print(f"⚠ 上传台账没写进盘（{type(e).__name__}: {str(e)[:60]}）"
                  f" —— 这节上传后**删不掉**远端那份")

    # ------------------------------------------------------------ 入队
    def enqueue(self, files, stem: str) -> int:
        """把一节课的文件排进队列。返回**排进去几个**（已不存在的跳过）。

        ⚠️ 同一节课**重复入队会合并**（按 `key`）—— 不然崩溃后重跑会排两遍。
        """
        key = session_key(stem)
        paths_ok = [str(p) for p in files if pathlib.Path(p).is_file()]
        if not paths_ok:
            return 0
        with self._lock:                                      # ⚠️ F25：见 `_lock` 的说明
            if key in {i.get("key") for i in self._items}:
                return 0                                      # 已经排过 -> 合并，不重复
            self._items.append({"stem": str(stem), "key": key, "files": paths_ok,
                                "tries": 0, "next_at": 0.0, "created": self._now()})
            self._save()
            # ⚠️ **台账也必须在锁里**（2026-10-12 全仓审查）：`_save_ledger` 是
            #    「读当前 → 合并 → 写回」的复合更新，两个并发 `enqueue` 在锁外跑就会互相覆盖。
            #    而台账丢一条 = 远端那一节课**删不掉**（`key` 是哈希、不可逆）。
            #    （锁里只做本地小文件 I/O —— 上面那条「锁里绝不做 I/O」针对的是**网络**。）
            self._save_ledger(stem, key)
        return len(paths_ok)

    def add_files(self, files, stem: str) -> int:
        """把**新的文件**并进已有条目（没有条目就新开一条）。返回真的加进去几个。

        ⭐ 3.10：崩溃补传先把残段 wav 排上、后台又把同一段转成了 opus ——
        这时要给**同一条**条目补上 opus 路径。⚠️ 不能用 `enqueue`：它按 key 去重，
        已有条目时**直接返回 0**（那份新文件就永远排不进去）。
        """
        key = session_key(stem)
        add = [str(p) for p in files if pathlib.Path(p).is_file()]
        if not add:
            return 0
        with self._lock:
            for it in self._items:
                if it.get("key") != key:
                    continue
                have = list(it.get("files") or [])
                new = [f for f in add if f not in have]
                if new:
                    it["files"] = have + new
                    self._save()
                return len(new)
            self._items.append({"stem": str(stem), "key": key, "files": add,
                                "tries": 0, "next_at": 0.0, "created": self._now()})
            self._save()
            self._save_ledger(stem, key)
        return len(add)

    def retry_now(self) -> int:
        """把**还没到点**的条目提前到"现在"（睡醒 / 网络刚恢复时调）。返回动了几条。

        ⚠️ **有界**（`WAKE_RETRY_MAX`）：超过上限的条目**不动** —— 睡一觉就无限
           重推是把服务端当靶子打；那几条退回正常退避（1 → 120 min）。
        ⚠️ 只动 `next_at`，不动 `tries` —— 它表达的是"这一次提前了"，
           不是"又失败了一次"（退避的账不许被睡醒搅乱）。
        """
        now = self._now()
        n = 0
        with self._lock:
            for it in self._items:
                if float(it.get("next_at") or 0) <= now:
                    continue                                  # 本来就到点了
                if int(it.get("wake_retries") or 0) >= WAKE_RETRY_MAX:
                    continue                                  # 提前过太多次 -> 认退避
                it["wake_retries"] = int(it.get("wake_retries") or 0) + 1
                it["next_at"] = now
                n += 1
            if n:
                self._save()
        return n

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

        ⚠️ **逐文件失败分开**（Sentry 那条）：一节课里某个文件挂了，**别的条目不受影响**；
           同一条目下次重试**只补没传成的那些**（逐文件进度在 `sent_files` 里，
           ⭐ 3.10 —— 对象名确定，重发只是覆盖，但 9 MB 白传一遍没必要）。
        ⚠️ 一次只试 `max_items` 条（默认 1）—— 回连后一次性倒空会被服务端风控。
        ⚠️⚠️ **列表的读改写都过 `_lock`；网络调用不过**（2026-10-01 审查 F25）：
           实例收成进程级唯一一份之后，两条线程（启动补传 / 收尾入队）会同时碰
           `_items` —— 串行化全交给这把锁，而锁里绝不做 I/O（见 `_lock` 的说明）。
        """
        out = {"sent": 0, "failed": 0, "expired": 0, "pending": 0}
        now = self._now()
        with self._lock:
            keep = []
            for it in self._items:
                if now - float(it.get("created") or 0) > EXPIRE_S:
                    out["expired"] += 1                       # 太久没传成 -> 丢
                    continue
                keep.append(it)
            self._items = keep
            #: 挑本轮要试的（取快照；网络段不持锁）
            todo = []
            for it in list(self._items):
                if len(todo) >= max_items:
                    break
                if now < float(it.get("next_at") or 0):
                    continue                                  # 还没到重试时刻
                todo.append(it)

        for it in todo:
            ok = True
            for f in list(it.get("files") or []):
                local = pathlib.Path(f)
                if not local.is_file():
                    continue                                  # 文件没了 -> 跳过（不是失败）
                remote = _remote_name(local)
                # ⭐⭐ 3.10：**已经传成的文件不再重发**（逐文件进度落在队列里）——
                #    上次传到一半被杀 / 睡过去，这次只补缺的那几个。原来整条重来：
                #    对象名确定所以不重复，但 9 MB 的音频白传一遍。
                # ⚠️⚠️ 判重与发送必须在**同一个临界区**里（2026-10-18 审查 D7）：两条
                #    `pump` 线程（收尾 / 睡醒）会同时读到 `sent_files` 里尚无该对象 ——
                #    不占位就各发一遍。先原子占位，发完/失败再放掉。
                claim = (it.get("key"), remote)
                with self._lock:
                    if it not in self._items:
                        break                                 # 另一线程已把这条传完 -> 别再发
                    if remote in (it.get("sent_files") or []):
                        continue
                    if claim in self._inflight:
                        continue                              # 另一线程正在发同一个对象
                    self._inflight.add(claim)
                staged = self._prepare(local)
                try:
                    good = bool(self._send(staged, f"{it['key']}/{remote}"))
                except Exception:                             # noqa: BLE001
                    good = False                              # send 不许抛，但兜一层
                finally:
                    with self._lock:
                        self._inflight.discard(claim)
                    if staged is not None and staged != local:
                        try:
                            staged.unlink(missing_ok=True)
                        except Exception:                     # noqa: BLE001
                            pass
                if not good:
                    ok = False
                    break                                     # 这条先放下，下次从缺的那个接着
                with self._lock:                              # ⚠️ 立刻落盘 —— 传到一半被杀才接得上
                    if it in self._items:
                        it.setdefault("sent_files", []).append(remote)
                        self._save()
            with self._lock:
                if ok:
                    # ⚠️ 3.10.1：本轮开始后 `add_files` 可能又并进了新文件（诊断包在收尾
                    #    最后才排）—— 只发了快照里那几个就整条移走，新文件就永远不发了。
                    #    还有没发的 → 留着（到点即发），不算失败。
                    sent = set(it.get("sent_files") or [])
                    rest = [f for f in (it.get("files") or [])
                            if pathlib.Path(f).is_file()
                            and _remote_name(pathlib.Path(f)) not in sent]
                    if it in self._items and not rest:        # ⚠️ 另一线程可能已经动过
                        self._items.remove(it)
                    out["sent"] += 1
                else:
                    it["tries"] = int(it.get("tries") or 0) + 1
                    # ⚠️ 指数退避、**封顶 120 min**（照 Firefox 那套）。
                    it["next_at"] = now + min(BACKOFF_MIN_S * (2 ** (it["tries"] - 1)),
                                              BACKOFF_MAX_S)
                    out["failed"] += 1
                self._save()
        with self._lock:
            out["pending"] = len(self._items)
        return out

    # ------------------------------------------------------------ 只读
    @property
    def pending(self) -> int:
        return len(self._items)

    def items(self) -> list:
        """只读快照（判据用）。⚠️ 别再往里写。"""
        return [dict(i) for i in self._items]


def _remote_name(local: pathlib.Path) -> str:
    """远端对象名 —— **不带课号 / 时间戳**（2026-10-01 审查 F26）。

    ⚠️ 文件头声称「哈希目录是为了不泄漏元数据（课号 + 精确时间戳 = 课表）」，而原来
       文件名**原样保留** → 远端目录是哈希、里面还是 `2026-09-24_140000_ECON10740.md`
       —— 声明与实现对不上，换个收件方就是事实上的泄漏。这里按**文件类型**给稳定名。
    ⚠️ 故意**保留类型后缀**：收件端还要一眼分清报告 / 转录 / 音频 / 崩溃标记。
    ⚠️ 同一节课内类型名互不冲突；跨课由 `{key}/` 目录分开 —— 覆盖语义与原来一致
       （重发本来就是"同名覆盖"，见 `pump` 的 docstring）。
    """
    name = local.name
    if name.endswith(".report.json"):
        return "report.json"
    if name.endswith(".md"):
        return "transcript.md"
    m = re.search(r"\.(\d{3})\.opus$", name)
    if m:
        return f"audio.{m.group(1)}.opus"
    # ⚠️ 崩溃补传（`recover_pending`）会带上**没转成 opus 的 wav 分段**（`<stem>.NNN.wav`）——
    #    不认它就会**原名透传** → 课号+时间戳漏回去，**且**切到 HTTP 后 Worker 白名单
    #    也不认 `.wav` → 400 → 那条崩溃音频**永远传不上去**（一路退避到 14 天过期）。
    m = re.search(r"\.(\d{3})\.wav$", name)
    if m:
        return f"audio.{m.group(1)}.wav"
    # ⭐ 3.10.1 诊断包（`testmode.make_extras`）。
    if name.endswith(".extra.tar.gz"):
        return "extra.tar.gz"
    # ⚠️ 崩溃补传的标记文件叫 `<stem>.meta.json` —— 同上，不认它就会漏课号+时间戳。
    if name.endswith(".meta.json"):
        return "meta.json"
    # ⚠️ 认不出的名字**原样透传**（判据里塞的任意文件名走这条），不许乱改名 ——
    #    生产只会产出上面几类；改名只会让「名字对不上」出现在不该出现的地方。
    return name


#: 崩溃标记的后缀。`TestSession` 开录时写、`finish()` 时删。
#: ⚠️ **没有它，「崩溃那一整类数据」会全丢** —— 而崩溃现场恰恰是最该看的。
PENDING_TAIL = ".pending.json"

#: 同一份 journal 处理完之后改成的名字（`recover_pending` / `journal_upload_done`）。
#: ⭐ 3.10：补笔记那一步（`main.resume_pending_notes`）**必须也认它** —— 否则
#: `recover_pending` 一改名，它就再也扫不到（D25：默认配置下"永远不补笔记"）。
RECOVERED_TAIL = ".recovered.json"

#: 残局 journal 的**两种**名字（扫的时候都要收，见 `residual_journals`）。
RESIDUAL_TAILS = (PENDING_TAIL, RECOVERED_TAIL)


def residual_journals(sessions_dir) -> list:
    """扫残局 journal —— `.pending.json` 与 `.recovered.json` **两种名字都收**。

    返回 `[(journal_path, stem), ...]`（按文件名排序）。**永不抛**。

    ⚠️ 为什么必须两种都收（D25）：`recover_pending` 处理完是把 `.pending.json`
       **改名**成 `.recovered.json`（不删，见它的 docstring），而"补笔记"那一步排在
       它**后面** —— 只认 `.pending.json` 的话，默认配置（上传开着）下**永远扫不到**，
       笔记补生成一声不响地空转。`journal_upload_done` 在笔记没写成时也改这个名
       （D27），所以这里一并收。
    """
    out: list = []
    sd = pathlib.Path(sessions_dir)
    for tail in RESIDUAL_TAILS:
        try:
            for mk in sd.glob("*" + tail):
                out.append((mk, mk.name[: -len(tail)]))
        except Exception:                                     # noqa: BLE001
            pass
    return sorted(out, key=lambda t: t[0].name)


def recover_pending(sessions_dir, send=None, *, transcode=None, queue=None,
                    root=None, clock=time.monotonic, now=time.time) -> dict:
    """开机扫残留的 `.pending.json`（上次跑测试模式**没收尾**），把它们排进队列。

    返回 `{found, queued, jobs}`。`jobs` = 交给后台转码的会话数（见下）。

    ⚠️⚠️ **为什么必须有这一步**：`TestSession.finish()` 跑不到（崩溃 / 强杀 / 关机）
       → **没有 report**，而 `enqueue` 是在 `finish()` 里调的 → 那一整类数据全丢。
       `.pending` 是**开录时就写好**的，所以它比 `finish()` 活得久。
       ⭐ 3.10 起标记还活着更久：它活到"上传已排队"（`journal_upload_done`）——
       "收过尾但还没排上传就被杀"这一格现在也有人接。
    ⚠️ **不删标记，改名**（`.pending.json` → `.recovered.json`）——
       删了就再也看不出"这一节崩过"。⚠️ 而改名之后不会重复触发。
    ⚠️ **只排真的存在的文件** —— 崩溃时音频可能是半截的，那也是数据。
    ⚠️ **上传关着**（`CLASSLIVE_UPLOAD=0`）时**不排也不改名** —— 留着残局，
       等开着的那次启动再补（不然一次离线启动会把残局"吃掉"却不上传）。
    ⭐⭐ `transcode`（`testmode.transcode_one`）注入后：残段 wav **先转码再排**
       （后台线程）—— 崩溃补传也走 opus（一段 600 s：1.7 MB vs 19 MB）。
       转不成就原样传 wav（数据不丢）。⚠️ 改名推迟到后台转完 —— 转码中途被杀，
       下次启动重来一遍（`enqueue` 按 key 去重、转码只碰还在的 wav，重复跑安全）。
    """
    import json as _json
    sd = pathlib.Path(sessions_dir)
    out = {"found": 0, "queued": 0, "jobs": 0}
    if not sd.is_dir():
        return out
    if not upload_enabled():
        return out
    # ⚠️⚠️ **调用方给队列就复用它**（2026-10-18 审查 D6）：`main` 全程用的是进程级
    #    共享的那一份（`_shared_upload_queue()`）。这里要是自己 `new` 一份，两个实例
    #    各有一份 `_items` 内存副本、各一把 `_lock`，却写**同一个**队列文件 —— 那把锁
    #    保护不到跨实例的写：主线程 `pump` 已成功上传并移除的条目会被本实例过期的
    #    `_items` 整份 `_save()` **复活**（下次再传一遍），后台新并入的 opus 也会被
    #    主线程的 `_save()` 覆盖。共用同一实例，`_lock` 才真正管住这条路径。
    q = queue if queue is not None else UploadQueue(send, root=root, clock=clock, now=now)
    jobs: list = []
    for mk in sorted(sd.glob("*" + PENDING_TAIL)):
        out["found"] += 1
        stem = mk.name[: -len(PENDING_TAIL)]
        steps = {}
        try:
            _j = _json.loads(mk.read_text(encoding="utf-8")) or {}
            if isinstance(_j, dict) and isinstance(_j.get("steps"), dict):
                steps = _j["steps"]
        except Exception:                                     # noqa: BLE001
            steps = {}                                        # 坏的当没有（老标记就是这形状）
        opus_all = sorted(sd.glob(stem + ".*.opus"))
        wavs = sorted(sd.glob(stem + ".*.wav"))
        opus = []
        if opus_all:
            # ⭐ 验过头的 opus 才算数；同一段两个名字都在时 **opus 优先**（wav 别跟着传）。
            #    ⚠️ 半截 opus（崩溃在转码中间）**不能**顶掉好 wav —— 那等于丢一段音频；
            #    但**没有 wav 的那种照传**（它是这一段唯一的副本，半截也是数据 ——
            #    同"崩溃时音频可能是半截的，那也是数据"那条既有纪律）。
            #    惰性 import：`upload` 是底层模块，顶层不能 import testmode（它 import 我们）。
            import testmode as _tm
            good, bad = [], []
            for p in opus_all:
                (good if _tm._opus_ok(p) else bad).append(p)
            _all_wav = set(wavs)
            _ok_wav = {p.with_suffix(".wav") for p in good}
            wavs = [w for w in wavs if w not in _ok_wav]
            opus = good + [p for p in bad if p.with_suffix(".wav") not in _all_wav]
        audio_now = opus
        pending_audio: list = []
        if transcode is not None and wavs:
            # ⭐ 残段先转码（后台）—— 现在**先不排**，等转出 opus 再排（省 10×）。
            pending_audio = list(wavs)
        else:
            audio_now = opus + wavs
        files = list(audio_now)
        rep = sd / (stem + ".report.json")
        if rep.is_file():
            files.append(rep)
        sess = sd / (stem + ".md")
        if sess.is_file():
            files.append(sess)
        if files or pending_audio:
            # 写一份 meta，标明这次是**崩过的**。
            # ⚠️ 即使音频还在后台转也要写（它是"这一节崩过"的凭据；只排它一条
            #    也把条目先立起来，后台转完的 opus 用 `add_files` 并进来）。
            meta = sd / (stem + ".meta.json")
            try:
                # ⚠️⚠️ **不写 stem、不写原始文件名** —— 那会把课号 + 精确时间戳带进
                #      上传正文（`_prepare` 的 scrub 只认路径，不认这两个字段）。
                #      要反查用哈希 `key`（与 upload-ledger.json 对得上）。
                #    ⭐ `steps` 把 journal 里"还欠哪几步"原样带上（只有步骤名，
                #      没有内容）—— 收件端一眼知道这一节是"收过尾没排上传"还是
                #      "课上崩了"。
                info = {"crash_recovered": True, "key": session_key(stem),
                        "recovered_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "n_files": len(files) + len(pending_audio),
                        "kinds": sorted({f.suffix for f in files + list(pending_audio)})}
                if steps:
                    info["steps"] = {str(k): str(v) for k, v in steps.items()}
                if pending_audio:
                    info["transcode_queued"] = len(pending_audio)
                meta.write_text(_json.dumps(info, ensure_ascii=False, indent=1),
                                encoding="utf-8")
                files.append(meta)
            except Exception:                                 # noqa: BLE001
                pass
        if files:
            # ⚠️⚠️ **必须用 `add_files` 不是 `enqueue`**（2026-10-18 审查 D16）：
            #    按 key 去重的那条（`enqueue`）在"条目已存在"时**直接丢新文件**。而本
            #    函数自己就会造出「条目已在、但缺这一段 opus」的状态：上一轮因 wav 要
            #    后台转码而推迟改名，条目里只排了 `meta.json`；转完第 1 段（wav 已删、
            #    opus 已落盘）后被杀 —— 本次启动该 opus 已在 `files` 里，却被 key 命中
            #    丢弃，它对应的 wav 又不存在（不会再走转码），于是**这一段音频永远不上传**。
            #    `add_files`：条目在就把新文件并进去，不在就新开一条（覆盖两种情形）。
            out["queued"] += q.add_files(files, stem)
        if pending_audio:
            jobs.append((stem, pending_audio))
            continue                                          # ⚠️ 改名推迟到后台转完
        try:
            mk.replace(mk.with_name(mk.name.replace(PENDING_TAIL, ".recovered.json")))
        except Exception:                                     # noqa: BLE001
            pass
    if jobs:
        out["jobs"] = len(jobs)
        threading.Thread(target=_resume_transcode, args=(q, jobs, transcode, sd),
                         daemon=True, name="cl-upload-transcode").start()
    return out


def _resume_transcode(q: UploadQueue, jobs: list, transcode, sd: pathlib.Path) -> None:
    """把崩溃残段的 wav **在后台**转成 opus，转完并进同一条队列条目。**永不抛。**

    ⚠️ wav 的删除交给 `transcode`（`testmode.transcode_one`：**验过 opus 才删**）——
       转不成的那条原样留在队列里（数据不丢，只是大）。
    ⚠️ 全部做完才把 `.pending.json` 改名 `.recovered.json`（半路被杀 -> 下次重来，
       重复跑是安全的：enqueue 按 key 去重、转码只碰还在的 wav）。
    """
    for stem, wavs in jobs:
        try:
            made, left = [], []
            for wav in wavs:
                try:
                    opus, _why = transcode(wav)
                except Exception:                             # noqa: BLE001
                    opus = None
                (made if opus is not None else left).append(opus or wav)
            if made:
                q.add_files(made, stem)
            if left:
                q.add_files(left, stem)                       # 没转成的原样传
        except Exception:                                     # noqa: BLE001
            pass
        mk = sd / (stem + PENDING_TAIL)
        try:
            mk.replace(mk.with_name(mk.name.replace(PENDING_TAIL, ".recovered.json")))
        except Exception:                                     # noqa: BLE001
            pass
    try:
        if q.pending:
            q.pump(max_items=q.pending)
    except Exception:                                         # noqa: BLE001
        pass


def make_http_send(base_url: str, token: str, *, timeout: float = 60.0):
    """造一个「HTTPS PUT 到 Worker」的 `send`。**生产用的那一个。**

    ⚠️⚠️ **为什么是 HTTP 不是 rsync**：rsync 那条路要求用户机器上有 ssh 别名 +
       私钥（作者的是 `bldcam`）—— 而"测试模式的上传"是**给朋友的**，朋友机器上
       没有那些东西 → 上传**静默失败**（0 条到位，见 `docs/PLAN-zero-config-upload.md`）。
       HTTPS 任何装了 app 的机器都能直接发 → **用户零配置**。

    ⚠️ `token` 是**公开**的（随 app 发出、仓库 MIT 公开）—— 它**不是密钥**，
       只证明"这是 ClassLive 在发"。真正的防线在服务端（Worker）：路径白名单 /
       体积上限 / 限流 / 只写。见 `docs/PLAN-zero-config-upload.md` §6。

    ⚠️ 与 rsync 版**契约完全一致**：成功 True / 失败 False / **不许抛**
       （调用方是退避循环，见 `UploadQueue.pump`）。
    ⚠️ **整份读进来发**（`local.read_bytes()`，一节课 ≤ 约 9 MB）—— **不用**
       `open(...)` 交给 httpx：那样 httpx 会走 **chunked**（无 `Content-Length`），
       而 Worker 的体积闸门正是读 `Content-Length` → chunked 会被当成 0 拒掉。
       发 bytes 才保证带上 `Content-Length`。
    """
    import httpx

    def send(local: pathlib.Path, key: str) -> bool:
        try:
            with httpx.Client(timeout=timeout) as c:
                r = c.put(f"{base_url.rstrip('/')}/{key}",
                          content=local.read_bytes(),
                          headers={"Authorization": f"Bearer {token}",
                                   "Content-Type": "application/octet-stream"})
            return 200 <= r.status_code < 300
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
