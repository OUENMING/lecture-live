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
import pathlib
import re
import threading
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
        #: ⚠️ 列表的读改写互斥（2026-10-01 审查 F25）：实例被收成进程级唯一一份之后，
        #:    「启动补传」与「收尾入队」两条线程会同时碰 `_items`。**锁里绝不做 I/O**
        #:    —— `enqueue` 在**主线程**被调，锁里带网络会让主线程等一个 rsync 超时。
        self._lock = threading.Lock()

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
        paths_ok = [str(p) for p in files if pathlib.Path(p).is_file()]
        if not paths_ok:
            return 0
        with self._lock:                                      # ⚠️ F25：见 `_lock` 的说明
            if key in {i.get("key") for i in self._items}:
                return 0                                      # 已经排过 -> 合并，不重复
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
                staged = self._prepare(local)
                try:
                    good = bool(self._send(staged, f"{it['key']}/{_remote_name(local)}"))
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
            with self._lock:
                if ok:
                    if it in self._items:                     # ⚠️ 另一线程可能已经动过
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
    # ⚠️ 崩溃补传的标记文件叫 `<stem>.meta.json` —— 同上，不认它就会漏课号+时间戳。
    if name.endswith(".meta.json"):
        return "meta.json"
    # ⚠️ 认不出的名字**原样透传**（判据里塞的任意文件名走这条），不许乱改名 ——
    #    生产只会产出上面几类；改名只会让「名字对不上」出现在不该出现的地方。
    return name


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
                # ⚠️⚠️ **不写 stem、不写原始文件名** —— 那会把课号 + 精确时间戳带进
                #      上传正文（`_prepare` 的 scrub 只认路径，不认这两个字段）。
                #      要反查用哈希 `key`（与 upload-ledger.json 对得上）。
                info = {"crash_recovered": True, "key": session_key(stem),
                        "recovered_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "n_files": len(files),
                        "kinds": sorted({f.suffix for f in files})}
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
