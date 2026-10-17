#!/usr/bin/env python3
"""Soniox 实时流式识别的深模块：可用就用它，不可用就**本节课**退回本地流水线。

    s = StreamASR(passcode=…, terms=course_term_list(…), general=course_title(…),
                  on_sealed=lambda s: finalq_text.put(s))     # 永不抛
    s.alive()            # 现在是不是 Soniox 在出词（建连完成 → True）
    s.draft_text()       # 当前未封口那句（D 布局最底那一行）
    s.feed(samples)      # 音频回调：只入有界队列，立刻返回，绝不阻塞
    s.reason()           # 退回原因（"" = 没退回）；s.notice_text() 给人看
    s.stop()             # 下课收尾：发空帧等 3 s 收 final，再关

## 两层（`codebase-design`：接口小、实现厚）

1. **纯逻辑核心 `StreamCore`（不 import 网络库）**：吃 Soniox 的响应消息 → 维护
   「已 final token 列表 + 当前 non-final 快照（每条消息**整批替换**）」→ 按
   `P_stable(0.3 s)` 切出封口句。时钟注入，可毫秒级单测（`tests/test_stream_asr.py`）。
2. **传输层 `StreamASR`**：后台线程 `websockets.sync.client` 直连（`asyncio` 不进主线程），
   领 key → 连 `ws_url` → 发 config → 推音频 / keepalive / 断线判死 / 一次重连 / 收尾。

## P_stable(0.3)：封口判据（10-16 实验定的）

non-final 里句末标点**连续存在** 0.3 s 即判一句结束；先变 final 则取 final。
实测（OCW lec11 回放，`~/…/scratchpad/scribe/snxlive_eval.py`）：WER 6.9%、
上移 p50 0.86 s、假上移 1.1%。候选时刻记在**消息到达时**（`clock()`），
所以 0.3 s 是「标点已在屏上挂了多久」，不是音频时刻差。

⚠️ **判据要有人推时钟**：一句说完之后 Soniox 会安静下来，只在收到消息时判
就永远封不了口 —— 控制循环每轮调 `StreamCore.tick()`（这也是它单独一个方法的原因）。

## 退回政策（不来回切）

- 开课领 key 失败（没通行码 / 429 额度 / 403 / 503 / 网络 / 502）→ 一开始就是本地模式；
- 课中**致命类**（401/400/402/403/413、`temp_api_key_session_expired`）→ 立即退回，不重连；
- **网络类**（408/429/500/503、连接断、写失败、15 s 无响应）→ **重领 key 重连一次**，
  再失败才退回（`single_use` 的 key：重试必须先领新的，绝不复用）。
- 退回后 `alive()` 永远 False（`_finished` 一次性），`reason()` 记原因，
  调用方照 `notice_text()` 出一次提示、下一段开始走本地。

## 线程

- `feed()` 在音频线程：`queue.put_nowait`，满了**丢最旧**并计数（`dropped()`），永不阻塞。
- 控制线程：发音频 / keepalive / 判死 / 排空事件 → `StreamCore`；收线程：只把消息
  转成事件入队。两者都碰核心，用一把 `Lock`；`on_sealed` 回调在**控制线程**里、
  **锁外**触发（调用方别在这里做重活，只该入队）。
- `stop()` 由主线程调：置位 → 控制线程发空文本帧，`_finish_stream` 等最多 3 s
  把最后那几句 final 收进来、**并把还挂着的半句 flush 出去**，再关。

## 计费 / 隐私

按**流时长**计费（含静音；`connection-keepalive` 页原文），所以音频连续推、静音不省。
音频经作者 Worker 换的临时 key 直连 Soniox（Worker 不碰音频）；这一路沿用云端定稿
那套告知（`final_asr.consent_text`），本模块**不新增弹窗**。
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time
import urllib.parse
from typing import Callable, Iterable, NamedTuple

import numpy as np

import asr_endpoint
import final_asr

SR = 16000
#: 推音频的块长（官方节奏规则：实时 / 近实时，100–120 ms 一块）。
CHUNK_S = 0.12
#: 音频队列上限（秒）。满了丢最旧 —— 音频回调**绝不阻塞**（仓库雷区）。
QUEUE_MAX_S = 10.0
#: P_stable：non-final 句末标点连续存在这么久 → 封口（见文件头）。
STABLE_S = 0.3
#: 静音（没有音频可推）时每 5 s 一条 keepalive；官方硬线是 20 s。
KEEPALIVE_S = 5.0
#: 「在推音频但这么久没有任何响应」→ 判死（半开连接不会自己报错）。
RESPONSE_TIMEOUT_S = 15.0
#: 收尾：发空文本帧后最多等这么久把最后的 final 收进来。
FINISH_WAIT_S = 3.0
#: 领 key 的 POST 超时（开课关键路径，短；重试由退回政策管）。
KEY_TIMEOUT_S = 4.0

#: 开关：`auto`（默认）| `off`。**是否真的启用还要调用方再看两个条件**
#: （有通行码 + 云端定稿开着 + 翻译档不是 raw）—— 那是 `wanted()` 的语义，见 HANDBOOK §6.1。
ENV = "CLASSLIVE_STREAM_ASR"
#: 领 key 端点。默认 `asr_endpoint.ENDPOINT` 去掉 `/v1/transcribe` 换成 `/v1/soniox/key`；
#: `CLASSLIVE_ASR_ENDPOINT` / `CLASSLIVE_STREAM_ASR_ENDPOINT` 可覆盖（判据 / 联调）。
ENDPOINT = "https://asr.bldcam.page/v1/soniox/key"
#: Soniox WebSocket 落点（默认区；Worker 回的 `ws_url` 优先）。
WS_URL = "wss://stt-rt.soniox.com/transcribe-websocket"
#: 引擎名（进 `drafts` 元组第 7 项；草稿译文 / 现场采集按它分组）。
ENGINE = "soniox"

#: 句末标点（与 `draft_engine.END_PUNCT` 同口径）。
END_PUNCT = (".", "?", "!")
#: 控制 token：不是词，别进文本。
CONTROL = ("<end>", "<fin>")

#: 退回原因 → 给用户看的提示（走现有 notice 通道）。`_` 是兜底。
NOTICES = {
    "no_passcode": "实时识别暂不可用（没填通行码），已切回本地识别",
    "quota": "实时识别额度用完了，已切回本地识别",
    "key_rejected": "实时识别暂不可用，已切回本地识别",
    "not_configured": "实时识别暂不可用，已切回本地识别",
    "network": "实时识别连不上（网络），已切回本地识别",
    "timeout": "实时识别没有响应，已切回本地识别",
    "disconnected": "实时识别掉线了，已切回本地识别",
    "error": "实时识别出错了，已切回本地识别",
}
NOTICE_DEFAULT = "实时识别暂不可用，已切回本地识别"


def wanted() -> bool:
    """开关是不是 `auto`（默认）。`off` → False。

    ⚠️ 只看这一个开关。调用方还要自己判：有通行码 / `final_asr.cloud_forced_off()` 为假 /
    `final_asr.read_config()["enabled"]` / 翻译档不是 `raw` —— 见 HANDBOOK §6.1 那一行。
    """
    return os.environ.get(ENV, "").strip().lower() != "off"


def notice_for(reason: str) -> str:
    """退回原因 → 提示文案（主线程拿它发 `("notice", …)`）。"""
    return NOTICES.get(reason, NOTICE_DEFAULT)


def endpoint() -> str:
    """领 key 端点。`CLASSLIVE_STREAM_ASR_ENDPOINT` > `CLASSLIVE_ASR_ENDPOINT` 换路径 > 内置。"""
    env = os.environ.get("CLASSLIVE_STREAM_ASR_ENDPOINT")
    if env:
        return env
    base = os.environ.get("CLASSLIVE_ASR_ENDPOINT")
    if base:
        p = urllib.parse.urlsplit(base)
        return urllib.parse.urlunsplit((p.scheme, p.netloc, "/v1/soniox/key", "", ""))
    return ENDPOINT


def _is_end(tok: str) -> bool:
    return tok.strip().endswith(END_PUNCT)


# ══════════════════════════════════════════════════════════════════════
# 第一层：纯逻辑核心
# ══════════════════════════════════════════════════════════════════════

class Tok(NamedTuple):
    """一个 Soniox token。时间一律**毫秒**（官方字段原样）。`is_final` 逐 token 给。"""
    text: str
    start_ms: int
    end_ms: int
    is_final: bool = False


class Sealed(NamedTuple):
    """一句封口。`end_s` = 句末词结束的音频时刻（秒，相对本流起点）。"""
    text: str
    start_s: float
    end_s: float


def parse_tokens(raw) -> list[Tok]:
    """Soniox 消息的 `tokens` → `[Tok]`。

    - 没有 `start_ms` 的（`<end>` / `<fin>` 控制帧）**丢掉**；
    - 坏条目（缺字段 / 类型不对）静默跳过 —— 收线程里不许抛。
    """
    out: list[Tok] = []
    if not isinstance(raw, list):
        return out
    for t in raw:
        if not isinstance(t, dict):
            continue
        text = t.get("text")
        st, en = t.get("start_ms"), t.get("end_ms")
        if not isinstance(text, str) or text in CONTROL:
            continue
        if isinstance(st, bool) or isinstance(en, bool) or not isinstance(st, (int, float)) \
                or not isinstance(en, (int, float)):
            continue
        out.append(Tok(text, int(st), int(en), bool(t.get("is_final"))))
    return out


class StreamCore:
    """纯逻辑核心：消息进，草稿 / 封口句出。**单线程用**（StreamASR 在外面加锁）。

    时钟注入（`clock` 返回秒，默认 `time.monotonic`）：P_stable 判据可毫秒级单测。
    """

    def __init__(self, *, clock: Callable[[], float] = time.monotonic,
                 stable_s: float = STABLE_S) -> None:
        self._clock = clock
        self._stable_s = float(stable_s)
        self._final: list[Tok] = []
        self._nonfinal: list[Tok] = []
        self._final_keys: set[tuple[str, int]] = set()    # 已收进 final 文本的 token（防御性去重）
        #: 水位线：已封口到哪个音频时刻（毫秒，= 上一句句末 token 的 end_ms）。之后**结束在它之前**的
        #: token 一律当「已封口句的旧词」丢掉 —— non-final 变 final 时文本 / start_ms 常会微调，
        #: 按 (文本, start_ms) 精确匹配认不出来，同一句会被封好几遍（10-17 真链路冒烟抓到）。
        self._wm_ms = -1
        self._sentences: list[Sealed] = []
        #: 当前候选句末标点 (文本, start_ms)：取**最早**那个（不是最后那个 —— 老师接着往下说时
        #: 新句的句号会出现在后面，取最后那个会让计时一直重来，前一句要等到 final 才封）。
        self._cand_key: tuple[str, int] | None = None
        self._cand_at: float | None = None

    # ---- 输入

    def feed_msg(self, msg) -> None:
        """吃一条 Soniox 消息（`{"tokens": [...]}`；没有 tokens 的忽略）。永不抛。"""
        if not isinstance(msg, dict):
            return
        toks = parse_tokens(msg.get("tokens"))
        if not toks:
            return
        fin = [t for t in toks if t.is_final]
        nf = [t for t in toks if not t.is_final]
        self.feed(fin, nf)

    def feed(self, finals: Iterable[Tok], nonfinal: Iterable[Tok]) -> None:
        """吃一条**已预分好**的消息：final 列表 + non-final 快照（整批替换）。"""
        for t in finals:
            self._append_final(t)
        self._nonfinal = [t for t in nonfinal if t.end_ms > self._wm_ms]
        self._seal_final_ends()
        self._update_candidate()
        self._maybe_seal()

    # ---- 输出

    def draft_text(self) -> str:
        """当前未封口那句 = 已 final 的尾巴 + non-final 快照。"""
        return "".join(t.text for t in self._final) + "".join(t.text for t in self._nonfinal)

    def take_sentences(self) -> list[Sealed]:
        """取走已封口句（取一次就清）。"""
        out, self._sentences = self._sentences, []
        return out

    def pending(self) -> bool:
        """手上还有没有 token（收尾时判「还要不要等 final」用）。"""
        return bool(self._final or self._nonfinal)

    def tick(self) -> list[Sealed]:
        """时钟推进：把到期的 P_stable 封口切出来。**必须在没有新消息时也定期调**
        —— 一句说完之后 Soniox 会安静下来，只在 `feed` 里判就永远封不了口（真踩过）。
        """
        self._maybe_seal()
        return self.take_sentences()

    def flush(self) -> list[Sealed]:
        """收尾：手上还挂着的半句也封出去（按最后一块音频的结束时刻收口）。

        不下课时最后半句（没等到标点）不封就整句丢掉 —— 收尾调用方拿它补一句。
        """
        self._maybe_seal()
        if self._final or self._nonfinal:
            toks = self._final + self._nonfinal
            self._sentences.append(Sealed(
                "".join(t.text for t in toks),
                min(t.start_ms for t in toks) / 1000.0,
                max(t.end_ms for t in toks) / 1000.0))
            self._final, self._nonfinal = [], []
            self._cand_key, self._cand_at = None, None
        return self.take_sentences()

    # ---- 内部

    #: 两次看到的句末标点算「同一个」的容差：non-final 修订时 start_ms 会抖几十毫秒
    #: （10-16 离线评估用的也是 ±150 ms）。
    CAND_TOL_MS = 150

    def _append_final(self, t: Tok) -> None:
        key = (t.text, t.start_ms)
        if t.end_ms <= self._wm_ms or key in self._final_keys:
            return                                  # 已封口句的旧词 / 重复 final
        self._final_keys.add(key)
        self._final.append(t)

    def _seal_final_ends(self) -> None:
        """final 里出现句末标点 → 立刻封到那里（不用等 P_stable）；一条消息里可能有几句。"""
        while True:
            end = next((t for t in self._final if _is_end(t.text)), None)
            if end is None:
                return
            self._seal(end)

    def _update_candidate(self) -> None:
        end = next((t for t in self._nonfinal if _is_end(t.text)), None)
        if end is None:
            self._cand_key, self._cand_at = None, None
            return
        c = self._cand_key
        if (c is not None and c[0] == end.text
                and abs(c[1] - end.start_ms) <= self.CAND_TOL_MS):
            self._cand_key = (end.text, end.start_ms)   # 同一个句号，位置微调：不重新计时
            return
        self._cand_key, self._cand_at = (end.text, end.start_ms), self._clock()

    def _maybe_seal(self) -> None:
        if self._cand_key is None or self._cand_at is None:
            return
        if self._clock() - self._cand_at < self._stable_s:
            return
        end = next((t for t in self._nonfinal
                    if _is_end(t.text) and abs(t.start_ms - self._cand_key[1]) <= self.CAND_TOL_MS),
                   None)
        if end is not None:
            self._seal(end)
        else:
            self._cand_key, self._cand_at = None, None
        self._update_candidate()                    # 后面可能还挂着下一句的句号：从现在起计时

    def _seal(self, end: Tok) -> None:
        """封出「到 `end` 这个句末 token 为止」的那一句（final 尾巴 + non-final），水位线推到它的 end_ms。"""
        toks = [t for t in self._final + self._nonfinal if t.end_ms <= end.end_ms]
        if toks:
            self._sentences.append(Sealed(
                "".join(t.text for t in toks),
                min(t.start_ms for t in toks) / 1000.0,
                max(t.end_ms for t in toks) / 1000.0))
        self._wm_ms = max(self._wm_ms, end.end_ms)
        self._final = [t for t in self._final if t.end_ms > self._wm_ms]
        self._nonfinal = [t for t in self._nonfinal if t.end_ms > self._wm_ms]
        self._cand_key, self._cand_at = None, None


# ══════════════════════════════════════════════════════════════════════
# 第二层：传输
# ══════════════════════════════════════════════════════════════════════

class _KeyClient:
    """领 key 的 HTTP 客户端（注入接缝：判据里换成假 Worker / 固定响应）。

    ⚠️ 只在控制线程里调（`request_key` 的单调用方）；连接池随进程活，不显式关
    （与 `final_asr._client` 同规矩：一个进程一条连接池）。
    """

    def __init__(self, timeout: float = KEY_TIMEOUT_S) -> None:
        self._timeout = timeout
        self._client = None

    def request_key(self, passcode: str) -> dict:
        """`{"api_key", "ws_url", "region", "expires_in_seconds", "max_session_s"}`；失败抛 `_KeyError`。"""
        import httpx
        if self._client is None:
            self._client = httpx.Client(timeout=self._timeout)
        r = self._client.post(endpoint(),
                              headers={"Authorization": f"Bearer {asr_endpoint.token(passcode)}",
                                       "X-ClassLive-Install": final_asr.install_id(),
                                       "X-ClassLive-Version": asr_endpoint.version_header()},
                              json={})
        js = None
        try:
            js = r.json()
        except ValueError:
            pass
        if r.status_code != 200 or not isinstance(js, dict) or not js.get("api_key"):
            raise _KeyError(r.status_code, js)
        return js


class _KeyError(Exception):
    """领 key 失败。`status` = HTTP 状态码（0 = 网络异常）。"""

    def __init__(self, status: int, body=None) -> None:
        super().__init__(f"key http {status}")
        self.status = status
        self.body = body


#: 致命 `error_type`（拿到就别再连了）。名字取自官方错误目录
#: https://soniox.com/docs/api-reference/errors.mdx（2026-10-17 核对）：401 / 402 / 403 / 400 / 413 这几类
#: 重连只会再烧一把 key 再收到同一个错。数字串保留给「只给了 error_code」的情况。
FATAL_ERROR_TYPES = frozenset({
    "unauthenticated", "permission_denied",
    "organization_balance_exhausted", "organization_monthly_budget_exhausted",
    "project_monthly_budget_exhausted", "temp_api_key_session_expired",
    "invalid_request", "model_not_available", "max_duration_reached",
    "max_audio_duration_reached",
    "400", "401", "402", "403", "413",
})


def classify_error(error_type: str) -> str:
    """`error_type` → `"fatal"` / `"retry"`。只看 error_type（稳定），不看 message。"""
    et = (error_type or "").strip().lower()
    if not et:
        return "retry"
    if et in FATAL_ERROR_TYPES:
        return "fatal"
    return "retry"


def _key_reason(e: Exception) -> str:
    """领 key / 建连失败 → 退回原因（给用户看的那几个桶）。"""
    st = e.status if isinstance(e, _KeyError) else 0
    if st in (401, 403):
        return "key_rejected"
    if st == 429:
        return "quota"
    if st == 503:
        return "not_configured"
    return "network"


class StreamASR:
    """传输层：`feed()` 音频进、`draft_text()` / `on_sealed` 出、`alive()` 报状态。

    参数（都能注入，判据里不碰真网络）：
    - `passcode`: `cl-…` 通行码；空串 = 一开始就退回（没通行码）。
    - `terms` / `general`: 课程术语（`context.terms`）与课名（`context.general`）。
    - `on_sealed`: `Callable[[Sealed], None]`，收线程**锁外**调（调用方只该入队）。
    - `clock` / `stable_s`: 透传给 `StreamCore`。
    - `request_key`: 注入的领 key 函数（默认 `_KeyClient`）。
    - `key_timeout_s` / `keepalive_s` / `response_timeout_s` / `finish_wait_s` /
      `audio_max_s` / `chunk_s` / `ws_url`: 判据用的小值接缝（生产全用模块常量）。
    """

    ENGINE = ENGINE

    def __init__(self, *, passcode: str, terms: Iterable[str] = (),
                 general: str = "", on_sealed: Callable[[Sealed], None] | None = None,
                 clock: Callable[[], float] = time.monotonic, stable_s: float = STABLE_S,
                 request_key: Callable[[str], dict] | None = None,
                 key_timeout_s: float = KEY_TIMEOUT_S, keepalive_s: float = KEEPALIVE_S,
                 response_timeout_s: float = RESPONSE_TIMEOUT_S,
                 finish_wait_s: float = FINISH_WAIT_S, audio_max_s: float = QUEUE_MAX_S,
                 chunk_s: float = CHUNK_S, ws_url: str = WS_URL) -> None:
        self._passcode = (passcode or "").strip()
        self._terms = tuple(str(t).strip() for t in terms if str(t).strip())
        self._general = general or ""
        self._on_sealed = on_sealed
        self._stable_s = float(stable_s)
        self._keepalive_s = float(keepalive_s)
        self._response_timeout_s = float(response_timeout_s)
        self._finish_wait_s = float(finish_wait_s)
        self._chunk_s = float(chunk_s)
        self._ws_url = ws_url
        self._max_chunks = max(1, int(float(audio_max_s) / self._chunk_s))
        self._key = request_key or _KeyClient(timeout=key_timeout_s).request_key

        self._lock = threading.Lock()               # 保护 core / 状态字段
        self._core = StreamCore(clock=clock, stable_s=stable_s)
        #: ⚠️ **真的有界**（`maxsize`）：满了 `put_nowait` 抛 `Full` → 丢最旧。
        #    手写「先看 qsize 再 put」不是判据（多线程下照涨），真踩过：队列永远不满、从不丢。
        self._audio: "queue.Queue[bytes]" = queue.Queue(maxsize=self._max_chunks)
        self._events: "queue.Queue[tuple]" = queue.Queue()
        self._dropped = 0
        self._samples = 0                           # 采到的样本总数（含丢掉的）
        self._marks: list[tuple[int, int]] = []     # (段首累计样本, 段号)
        self._seg = 0
        self._alive = False
        self._stopped = False
        self._finished = False
        self._fatal = False
        self._reason = ""
        self._retried = False
        self._pending = ""                          # 上次会话的死因（重连时先清、判死时先看）
        self._thread = threading.Thread(target=self._run, name="stream-asr", daemon=True)
        self._thread.start()

    # ---- 对外（调用方接口）

    def alive(self) -> bool:
        """Soniox 现在是不是在出词（建连完成、还没退回）。"""
        with self._lock:
            return self._alive and not self._finished and not self._stopped

    def reason(self) -> str:
        """退回原因（`""` = 还没退回）。"""
        with self._lock:
            return self._reason

    def notice_text(self) -> str:
        """给人看的一行（`reason()` 的文案）；**没退回时是空串**（别报假警）。"""
        return notice_for(self.reason()) if self.reason() else ""

    def feed(self, samples) -> None:
        """音频回调：float32 样本 → 有界队列。**绝不阻塞**，满了丢最旧。"""
        try:
            arr = np.asarray(samples, dtype="float32")
            n = int(arr.size)
            if n == 0:
                return
            pcm = (np.clip(arr, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
            with self._lock:
                self._samples += n
            try:
                self._audio.put_nowait(pcm)
            except queue.Full:
                try:
                    self._audio.get_nowait()
                    self._audio.put_nowait(pcm)
                except queue.Empty:
                    pass
                with self._lock:
                    self._dropped += 1
        except Exception:                           # noqa: BLE001
            pass                                    # 音频回调永不抛（仓库雷区）

    def dropped(self) -> int:
        """因为队列满被丢掉的块数（诊断用）。"""
        with self._lock:
            return self._dropped

    def cum_samples(self) -> int:
        """采到的样本总数（含丢掉的）—— 调用方拿它算 `buf_s` / 音频时刻。"""
        with self._lock:
            return self._samples

    def mark_segment(self) -> int:
        """新 VAD 段开始：记下「现在」在流里的样本位置，返回段号。

        封口句的 `end_s` 是**音频时刻**，用 `seg_for_audio(end_s)` 映射回段号。
        （第一阶段只把这两个方法准备好，接线在第二阶段 `main.py`。）
        """
        with self._lock:
            self._seg += 1
            self._marks.append((self._samples, self._seg))
            return self._seg

    def seg_for_audio(self, t_s: float) -> int:
        """音频时刻（秒，相对本流起点）→ 段号。没有 mark 过就返回当前段号。"""
        n = int(max(0.0, float(t_s)) * SR)
        with self._lock:
            seg = 0
            for start, s in self._marks:
                if start <= n:
                    seg = s
                else:
                    break
            return seg or self._seg

    def draft_text(self) -> str:
        """当前未封口那句（D 布局最底那一行）。"""
        with self._lock:
            return self._core.draft_text()

    def stop(self) -> None:
        """收尾：发空文本帧，最多等 `finish_wait_s` 收 final，再关。可重复调。"""
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
        self._thread.join(timeout=self._finish_wait_s + 2.0)

    # ---- 控制线程

    def _run(self) -> None:
        reason = "stopped"
        try:
            if not self._passcode:
                reason = "no_passcode"
            else:
                reason = self._attempts()
        except Exception:                           # noqa: BLE001
            reason = "error"
        finally:
            self._finish(reason)

    def _attempts(self) -> str:
        """最多两次连接（网络类失败重领 key 重连一次）。返回退回原因（`""` = 干净停下）。

        ⚠️ 判死时先看 `_pending`：会话常常是「先 N 秒无响应、服务端随后关连接」——
        照返回值会记成 `disconnected`，把真正的原因盖掉。
        """
        while True:
            err = self._session()
            with self._lock:
                if self._stopped:
                    return ""
                if self._fatal:
                    return "error"
                if not err:
                    return "disconnected"           # 收线程死了但会话没报错
                if err == "disconnected" and self._pending:
                    err = self._pending
                if err not in ("network", "timeout", "disconnected"):
                    return err
                if self._retried:
                    return err
                self._retried = True

    def _session(self) -> str:
        """一次完整会话。返回 `""`（正常收尾）或失败原因。"""
        try:
            key = self._key(self._passcode)
        except _KeyError as e:
            return _key_reason(e)
        except Exception:                           # noqa: BLE001
            return "network"
        ws_url = str(key.get("ws_url") or self._ws_url)

        import websockets.sync.client as wsc
        try:
            ws = wsc.connect(ws_url, additional_headers={
                "Authorization": f"Bearer {key['api_key']}"}, open_timeout=5.0,
                close_timeout=1.0, ping_interval=20.0, legacy=True)
        except Exception:                           # noqa: BLE001
            return "network"

        # ⚠️ 新会话要**清掉上一次的遗留事件**（尤其收线程死时留下的那条 `("dead")`）——
        #    不清的话重连后的第一个循环就把新连接判死（真踩过：重连明明成功却立刻退回）。
        with self._lock:
            self._pending = ""                      # 上次的死因作废
            while True:
                try:
                    self._events.get_nowait()
                except queue.Empty:
                    break
        recv = threading.Thread(target=self._recv_loop, args=(ws,), name="stream-asr-recv",
                                daemon=True)
        recv.start()
        try:
            ws.send(json.dumps(self._config()))
            with self._lock:
                self._alive = True
            err = self._send_loop(ws)
        except Exception:                           # noqa: BLE001
            err = "disconnected"
        finally:
            with self._lock:
                self._alive = False
            try:
                ws.close()
            except Exception:                       # noqa: BLE001
                pass
            recv.join(timeout=2.0)
        return err

    def _config(self) -> dict:
        """config 消息。`context` 只在有内容时带；总量压在 8000 token 内（粗估 4 字符/token）。"""
        cfg: dict = {"model": "stt-rt-v5", "audio_format": "pcm_s16le",
                     "sample_rate": SR, "num_channels": 1,
                     "language_hints": ["en"], "enable_endpoint_detection": True}
        budget = 30000                              # ≈7500 token 的上界（官方上限 8000）
        terms: list[str] = []
        for t in self._terms:
            if len(t) > 200:
                continue
            if budget - len(t) - 1 < 0:
                break
            budget -= len(t) + 1
            terms.append(t)
        ctx: dict = {}
        if terms:
            ctx["terms"] = terms
        if self._general and budget > 200:
            ctx["general"] = [{"title": self._general[:200]}]
        if ctx:
            cfg["context"] = ctx
        return cfg

    def _send_loop(self, ws) -> str:
        """推音频 / keepalive / 判死。返回 `""`（正常收尾）或失败原因。

        ⚠️ 判死只在**在推音频**时算（`last_audio`）：静音期 Soniox 本来就不出词，
        拿「很久没响应」判死会把安静的课全判掉；keepalive 本身会引来回应，
        所以 `max(got, last_audio, last_send)` 之后 N 秒还没动静才是真死。
        """
        last_audio = time.monotonic()
        last_send = last_audio
        got = time.monotonic()
        dead = False
        chunk_samples = max(1, int(self._chunk_s * SR))
        carry = np.empty(0, dtype="<i2")
        while True:
            with self._lock:
                if self._stopped:
                    break
                if self._fatal:
                    return "error"
            got, fatal, dead = self._drain_events(got, dead)
            # ⚠️ `dead` 之前先看致命：真机上一个致命错误之后服务端立刻关连接，
            #    两个事件常在同一批里到；先看 `dead` 会把死因记成 disconnected。
            if fatal or self._fatal:
                return "error"
            now = time.monotonic()
            if now - max(got, last_audio, last_send) > self._response_timeout_s:
                # 判死时连接往往紧接着也断：先记原因（`_pending`），`_attempts` 才不会
                # 把死因记成 disconnected。
                with self._lock:
                    self._pending = "timeout"
                return "timeout"
            if dead:                                # 收线程死了
                return "disconnected"
            try:
                pcm = self._audio.get(timeout=0.2)
            except queue.Empty:
                now = time.monotonic()
                if now - last_audio > self._keepalive_s and now - last_send > self._keepalive_s:
                    try:                            # ⚠️ 发送失败按断线处理，绝不停在这
                        ws.send(json.dumps({"type": "keepalive"}))
                    except Exception:               # noqa: BLE001
                        return "disconnected"
                    last_send = now
                continue
            last_audio = time.monotonic()
            try:
                buf = np.frombuffer(pcm, dtype="<i2")
                if carry.size:
                    buf = np.concatenate((carry, buf))
                i = 0
                while i + chunk_samples <= buf.size:
                    ws.send(buf[i:i + chunk_samples].tobytes())
                    last_send = time.monotonic()
                    i += chunk_samples
                carry = buf[i:].copy()
            except Exception:                       # noqa: BLE001
                return "disconnected"
        return self._finish_stream(ws)

    def _drain_events(self, got: float, dead: bool = False) -> tuple[float, bool, bool]:
        """排空收线程的事件。返回 `(最近一条消息的时刻, 是不是致命, 收线程死了没)`。

        ⚠️ **整批排完再判**：同一批里「致命错误」可能排在「连接关闭」后面 —— 提前
        返回会让退回原因记成 `disconnected` 而不是真正的致命类。
        """
        fatal = False
        while True:
            try:
                kind, payload = self._events.get_nowait()
            except queue.Empty:
                break
            if kind == "dead":
                dead = True
                continue
            got = time.monotonic()
            if kind == "err":
                if classify_error(payload) == "fatal":
                    fatal = True
            elif kind == "msg":
                with self._lock:
                    self._core.feed(payload[0], payload[1])
                    sealed = self._core.take_sentences()
                for s in sealed:
                    self._fire(s)
        self._tick()
        if fatal:
            with self._lock:
                self._fatal = True
        return got, fatal, dead

    def _tick(self) -> None:
        """时钟推进：把到期的封口句送出去（收线程安静时也照跑，见 `StreamCore.tick`）。"""
        with self._lock:
            sealed = self._core.tick()
        for s in sealed:
            self._fire(s)

    def _recv_loop(self, ws) -> None:
        try:
            for raw in ws:
                if not isinstance(raw, (str, bytes)):
                    continue
                try:
                    msg = json.loads(raw)
                except (ValueError, UnicodeDecodeError):
                    continue
                if not isinstance(msg, dict):
                    continue
                et = msg.get("error_type")
                if isinstance(et, str) and et:
                    self._events.put(("err", et))
                    continue
                toks = msg.get("tokens")
                if not isinstance(toks, list):
                    continue
                parsed = parse_tokens(toks)
                fin = [t for t in parsed if t.is_final]     # 逐 token 的 is_final（官方形状）
                nf = [t for t in parsed if not t.is_final]
                self._events.put(("msg", (fin, nf)))
        except Exception:                           # noqa: BLE001
            pass
        finally:
            self._events.put(("dead", None))

    def _finish_stream(self, ws) -> str:
        """收尾：空文本帧 → 等最后的 final → 关。"""
        try:
            ws.send("")
        except Exception:                           # noqa: BLE001
            return "disconnected"
        deadline = time.monotonic() + self._finish_wait_s
        while time.monotonic() < deadline:
            with self._lock:
                if self._fatal or not self._core.pending():
                    break
            self._drain_events(time.monotonic())
            time.sleep(0.05)
        # 还挂着的半句也送出去（不然老师最后那半句整句丢）
        with self._lock:
            sealed = self._core.flush()
        for s in sealed:
            self._fire(s)
        return ""

    # ---- 状态

    def _fire(self, s: Sealed) -> None:
        cb = self._on_sealed
        if cb is None:
            return
        try:
            cb(s)
        except Exception:                           # noqa: BLE001
            pass                                    # 调用方的错不该打死收线程

    def _finish(self, reason: str) -> None:
        with self._lock:
            if self._finished:
                return
            self._finished = True
            self._alive = False
            if self._stopped:
                self._reason = ""
            else:
                self._reason = reason or "error"
        # 收尾后不再投递（`_finish_stream` 已 flush 过一次；这里只是把残留清干净）
        try:
            self._core.flush()
        except Exception:                           # noqa: BLE001
            pass
