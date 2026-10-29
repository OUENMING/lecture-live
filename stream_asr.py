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
import random
import re
import threading
import time
import urllib.parse
from typing import Callable, Iterable, NamedTuple, Sequence

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
#: 「这么久**一条消息都没收到**」→ 判死（半开连接不会自己报错）。
#: ⚠️ 口径是 `got`（最近一条**服务端消息**），不是 `last_audio`/`last_send`（客户端自己
#: 在发）——Soniox 在静音/只发 keepalive 时也会每 ~1 s 回一条进度消息（2026-10-21 实测），
#: 所以「有音频在推」不能当成「服务端还活着」。10-20 ECON10740 的 2m41s 静默就是
#: 旧口径 `max(got, last_audio, last_send)` 让 `last_audio` 恒新、永不触发造成的。
RESPONSE_TIMEOUT_S = 8.0
#: ⚠️ 2026-10-29 从 15 → 8：13 节上传课里健康会话的 `msg_gap_max_s` 都在 1.1–1.7 s（10 节），
#: 出问题的两节是 5.4 / 6.0 s；8 s 是健康最大值的 ~5 倍。重连现在很便宜（立刻 → 2 s → 5 s），
#: 误判的代价远小于「半开连接白等 15 s」。
#: 服务端处理进度落后已发送音频这么多秒 → 认为流已经「跟不上实时」（消息还在来，
#: 但内容是旧的）。`total_audio_proc_ms` 是 Soniox 官方给的进度字段（2026-10-21 核对）。
BACKLOG_S = 3.0
#: 落后持续这么久才动作 —— 挡掉正常的 1 s 级抖动，也给普通断线重连后的
#: 积压一点追平时间（服务端通常 1–2.5× 实时；3 s 内追平就不折腾）。
BACKLOG_HOLD_S = 3.0
#: 服务端一直回 `total_audio_proc_ms=0`（消息在来、但一毫秒音频都没处理）的兜底。
#: 开流/大 context 就绪时 0 是合法的；但持续这么久仍是 0 → 判 `stale` 重连。
ZERO_PROC_TIMEOUT_S = 15.0
#: ⭐ 2026-10-26 掉线自愈：第 n 次重连**之前**等多久（秒）；长度 = 预算（一段「连续不稳」里最多重连几次）。
#: 第一次立刻重连（短暂抖动不该多等）；之后逐步放慢，免得网络刚掉时
#: 连着撞墙（具体档位见下面 2026-10-29 的改动）。等待期间 Soniox 不出词，本地草稿 / 定稿照常接手（`allow_local()` 看 `alive()`），
#: 恢复后自动换回 Soniox。10-23 ECON10770 就是「一节课只给一次重连」→ 第二次掉线后 38% 的课退回本地。
#:
#: ⭐ 2026-10-29 改：原表 `(0, 30, 120, 300)` 对「连上就跑几十秒再掉」的链路等于自杀 ——
#: 中国用户 BUSI3222 一节课 Soniox 只有 4 个出词窗（128/36/152/343 s），窗间空档 35/140/307 s ≈
#: 退避表本身；4 次预算耗尽后 57 min 全本地。同行惯例：Soniox 官方 SDK 默认 1/2/4 s；AWS / gRPC /
#: websocket.org 的惯例是**封顶 30 s + 抖动**、长连接客户端近似无限重试（出处见
#: `docs/PLAN-stream-reconnect-20261029.md`）。这里 = 立刻 → 2 → 5 → 10 → 20 s，之后每 30 s 一次，
#: 连续失败约 31 min 才放弃（官方限速 100 次/分，30 s 一次远低于它）。
RETRY_DELAYS_S = (0.0, 2.0, 5.0, 10.0, 20.0) + (30.0,) * 60
#: 上一个会话稳定跑过这么久 → 预算还原。⚠️ 不能在「一连上」就还原（连上就掉的链路会永远停在最短间隔）；
#: 60 s ≈ 这一分钟里它真的在出词。原来 600 s：窗口都短于它 → 预算永远还原不了。
RETRY_RESET_S = 60.0
#: 退避之前等待不短于这么久，重连时丢掉队列里的旧音频跳到实时（掉线期间本地已经替这段出过字了；
#: Soniox 要求「实时或近实时」发音频，持续突发会被断开，所以不补发积压）。
RETRY_SKIP_AFTER_S = 2.0
#: 退避抖动幅度（±比例）。多位用户同时被同一次故障掐掉时别同时撞回去。
RETRY_JITTER = 0.2
#: 一次 `ws.send` 卡住超过这么久 → 判流卡死、掐掉 socket 重连。
#: ⚠️ 为什么要它：`send` 在 websockets 里是持锁 `sendall`，对端收不动（TCP 窗口塞满）时发送线程
#: 卡在里面，判死循环跟着停摆，库自带的 ping 超时也要拿同一把锁 —— 只能等 TCP 自己超时（分钟级）；
#: 这期间 `alive()` 仍为真 → 本地定稿被闸住、音频队列满了丢最旧 = 内容永久丢失
#: （10-29 第 1 节 3 个 >30 s 长洞、dropped 596 s）。
SEND_STUCK_S = 5.0
#: `metrics()["sessions"]` 最多记这么多条（30 s 一次的长期重连也撑得住一整天，不会无限长）。
SESS_LOG_MAX = 120
#: 收尾：发空文本帧后最多等这么久把最后的 final 收进来。
FINISH_WAIT_S = 3.0
#: 领 key 的 POST 超时（开课关键路径，短；重试由退回政策管）。
KEY_TIMEOUT_S = 4.0

# ── Soniox `context` 的预算（官方 context 页 2026-10-17 核对：
#    https://soniox.com/docs/stt/concepts/context —— 上限 8 000 token ≈ 10 000 字符，
#    超了回 `invalid_request`，而它在 `FATAL_ERROR_TYPES` 里 = 整节课退回本地）──
#: terms + general + text **合起来**的字符预算；留一成余量吃序列化骨架与估算偏差。
#: ⚠️ 从前是 30 000（≈ 官方口径的 3 倍）—— 词表一大就会静默踩线，这是那次收口的原因。
CONTEXT_CHAR_BUDGET = 9000
#: 单条术语字符上限 —— 超了整条跳过（手抄的长句不是术语）。
TERM_MAX_CHARS = 200
#: `general` 的键值对数上限（官方 Tips：10 个以内）与单个值字符上限。
GENERAL_KEYS_MAX = 10
GENERAL_VALUE_MAX = 200
#: `context.text`（课件大纲摘要）字符上限 —— 官方：text 影响力弱于 general / terms。
CONTEXT_TEXT_MAX = 1500
#: **平时**进 `context.terms` 的条数目标（可配置：`StreamASR(terms_max=…)`）。
#: ⚠️ 上限（`CONTEXT_CHAR_BUDGET`）只兜底；官方没说 context 大小对延迟的影响，
#: 所以平时就按这个**小目标**走 —— 先小后大，用真课数据说话（#3 的 terms_n 与封口延迟）。
TERMS_MAX = 100
#: 序列化骨架（键名、引号、逗号、括号）的保守余量 —— `build_context` 记账用。
_CONTEXT_SERIAL_MARGIN = 64
#: 封口延迟样本上限（一节课几十~几百句；封顶只为防异常时内存失控）。
SEAL_LAT_MAX = 5000

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
    "stale": "实时识别跟不上实时，已切回本地识别",
    "disconnected": "实时识别掉线了，已切回本地识别",
    "error": "实时识别出错了，已切回本地识别",
    "manual": "已切到本地识别 + 云端定稿（菠萝丁）；翻译不变",
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

    def at_boundary(self) -> bool:
        """此刻是不是在**两句之间**（手上没挂着半句，或挂着的那句已经说到句末标点）。

        ⭐ 3.11 端点换行：Soniox 的 `<end>` 约 38% 落在一句话中间（老师说到一半停顿，
        `snx/REPORT_ep.md`）—— 只有落在句与句之间的才算「讲完一口气」。
        """
        tail = (self.draft_text() or "").rstrip().rstrip("\"')\u201d\u2019")
        return not tail or tail[-1] in ".?!"

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

    # ---- 语意封口（`semantic_seal.py` 用）

    def unsealed_words(self) -> list[str]:
        """未封口那段文字的词（按空白切）。语意封口按「第几个词」说话，两边的切法必须一致。"""
        return self.draft_text().split()

    def seal_through_word(self, k: int, expect: Sequence[str] | None = None) -> bool:
        """语意封口：把未封口文字的前 `k+1` 个词（0 起）当一句封掉，水位线推到第 `k` 词末 token 的 `end_ms`。

        ⭐ 走的就是句号封口那条 `_seal` —— 水位线之前的词之后一律当旧词丢，所以 Soniox 后来把
        这段变 final / 补上句号都不会再封一遍。**触发判据换了，去重机制没换。**
        不封的情况（返回 `False`，什么都不动）：`k` 越界；`expect` 给了而当前前缀已经变了
        （决策花了一秒，这期间 Soniox 改了词或这句已被句号封走）；第 `k` 词落在一个跨词 token 中间。
        """
        toks = self._final + self._nonfinal
        text = "".join(t.text for t in toks)
        words = text.split()
        if k < 0 or k >= len(words):
            return False
        if expect is not None and words[:k + 1] != list(expect[:k + 1]):
            return False
        # 每个 token 的最后一个非空白字符落在第几个词里
        widx, prev_space, last_word = -1, True, []
        for t in toks:
            lw = None
            for ch in t.text:
                if ch.isspace():
                    prev_space = True
                else:
                    if prev_space:
                        widx += 1
                    prev_space = False
                    lw = widx
            last_word.append(lw)
        end = None
        for t, lw in zip(toks, last_word):
            if lw is not None and lw <= k:
                end = t if lw == k else end
        if end is None:
            return False
        self._seal(end)
        self._update_candidate()
        return True

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

# ── 生产量具（纯函数）──

def seal_latency_s(now: float, audio_t0: float | None, end_s: float) -> float | None:
    """封口延迟（秒）= 上移墙钟 − 句尾的音频时刻 —— 与离线评估「句号词尾→上移」同口径。

    实时推流下「音频时刻 + 推流滞后 ≈ 墙钟」，所以它量的是**用户多等的那段**：
    Soniox 处理 + P_stable(0.3 s) + 轮询粒度 + 网络。`audio_t0` 没设（还没喂过音频）→ `None`。
    负数钳到 0（丢块 / 时钟抖动会让它略小于 0 —— 0 表示「没多等」）。
    """
    if audio_t0 is None:
        return None
    return max(0.0, (float(now) - float(audio_t0)) - float(end_s))


def _pcts(vals: Iterable[float]) -> dict | None:
    """一串数 → `{"p50", "p90", "n"}`；**空表 → None**（"没采到"与"延迟为 0"要分得开，
    同 `testmode.pcts` 的口径）。"""
    xs = list(vals)
    if not xs:
        return None
    a = np.asarray(xs, dtype=float)
    return {"p50": round(float(np.percentile(a, 50)), 1),
            "p90": round(float(np.percentile(a, 90)), 1),
            "n": int(a.size)}

# ── context 组装（纯函数；config 消息与它的记账都从这儿出）──
#
# ⚠️ 官方口径（context 页，2026-10-17 核对）：terms + general + text **合起来**
#    8 000 token ≈ 10 000 字符，超了回 `invalid_request` = 整节课退回本地。
#    但那只当**兜底**：平时按 TERMS_MAX 条的小目标走（context 越大越可能拖慢
#    转录，官方没给大小-延迟曲线 —— 先小后大，拿真课数据调）。

#: 课号前缀 → 领域（`general.domain`）。认不出的前缀**不发这个键** —— 猜错领域比不给更坏。
_DOMAINS = {
    "ECON": "economics", "SOC": "sociology", "COMP": "computer science",
    "CS": "computer science", "MATH": "mathematics", "STAT": "statistics",
    "PHIL": "philosophy", "PSY": "psychology", "FIN": "finance",
    "ACC": "accounting", "LAW": "law", "GEOG": "geography", "HIST": "history",
}


def _domain_of(title: str) -> str:
    """课名开头形如 `ECON10730 …` → `"economics"`；认不出返回 `""`。"""
    m = re.match(r"\s*([A-Za-z]{2,})\s*\d", title or "")
    return _DOMAINS.get(m.group(1).upper(), "") if m else ""


def general_pairs(title: str) -> list[tuple[str, str]]:
    """课程标题 → 官方 `general` 模板的键值对（≤ `GENERAL_KEYS_MAX` 个短键值）。

    模板（官方 context 页 §Tips / §Experimental，2026-10-17 核对）：
    `topic`（课名）/ `domain`（由课号前缀认，认不出不发）/ `setting` / `language`。
    **没有标题 → 空表**（不凭空发明上下文）。
    """
    t = (title or "").strip()
    if not t:
        return []
    out = [("topic", t)]
    d = _domain_of(t)
    if d:
        out.append(("domain", d))
    out.extend([("setting", "university lecture"), ("language", "English")])
    return out[:GENERAL_KEYS_MAX]


def build_context(terms: Iterable[str], title: str = "", text: str = "",
                  *, budget: int = CONTEXT_CHAR_BUDGET,
                  terms_max: int = TERMS_MAX) -> tuple[dict, dict]:
    """组装 Soniox `context` 消息 + 记账。**纯函数** —— 判据直接喂它。

    优先序（预算/条数不够时先保前面的）：general（领域先验）→ terms（**按给定顺序**
    取前 `terms_max` 条 —— 撞上限时先保排在前的）→ text（截到剩余额度，`CONTEXT_TEXT_MAX` 封顶）。

    预算按**序列化后的字符数**算（官方 8 000 token ≈ 10 000 字符；默认 9 000 留余量），
    三个段落**合起来**不许超。返回 `(ctx, stats)`；`ctx` 空 dict = 不带 context 键。
    `stats` 键：`terms_n` / `terms_dropped`（**不进 context 的条数 —— 必须记账，不许静默**）/
    `terms_chars` / `general_n` / `text_chars`。
    """
    ctx: dict = {}
    stats = {"terms_n": 0, "terms_dropped": 0, "terms_chars": 0,
             "general_n": 0, "text_chars": 0}
    pairs = general_pairs(title)
    if pairs:
        ctx["general"] = [{"key": k, "value": str(v)[:GENERAL_VALUE_MAX]}
                          for k, v in pairs[:GENERAL_KEYS_MAX]]
        stats["general_n"] = len(ctx["general"])
    used = len(json.dumps(ctx, ensure_ascii=False)) if ctx else 2
    room = max(0, budget - used - _CONTEXT_SERIAL_MARGIN)
    all_terms = [str(t).strip() for t in terms if str(t).strip()]
    kept: list[str] = []
    for i, t in enumerate(all_terms):
        if i >= max(0, int(terms_max)):
            stats["terms_dropped"] += len(all_terms) - i
            break
        if len(t) > TERM_MAX_CHARS:
            stats["terms_dropped"] += 1        # 手抄的长句不是术语：跳过、记账、继续
            continue
        if len(t) + 4 > room:
            stats["terms_dropped"] += len(all_terms) - i
            break
        room -= len(t) + 4
        kept.append(t)
    if kept:
        ctx["terms"] = kept
        stats["terms_n"] = len(kept)
        stats["terms_chars"] = sum(len(t) for t in kept)
    txt = (text or "").strip()
    if txt:
        cap = min(CONTEXT_TEXT_MAX, max(0, room - 12))    # `, "text": ""` 的骨架
        cut = txt[:cap]
        if cut:
            ctx["text"] = cut
            stats["text_chars"] = len(cut)
    # 兜底：转义字符（引号等）让保守估计漏了 → 从尾部继续丢术语（照旧记账）。
    while ctx.get("terms") and len(json.dumps(ctx, ensure_ascii=False)) > budget:
        ctx["terms"].pop()
        stats["terms_n"] -= 1
        stats["terms_dropped"] += 1
    if not ctx.get("terms"):
        ctx.pop("terms", None)
    return ctx, stats


class _KeyClient:
    """领 key 的 HTTP 客户端（注入接缝：判据里换成假 Worker / 固定响应）。

    ⚠️ 只在控制线程里调（`request_key` 的单调用方）；连接池随进程活，不显式关
    （与 `final_asr._client` 同规矩：一个进程一条连接池）。
    """

    def __init__(self, timeout: float = KEY_TIMEOUT_S, region: str = "") -> None:
        self._timeout = timeout
        self._region = str(region or "").strip().lower()
        self._client = None

    def request_key(self, passcode: str) -> dict:
        """`{"api_key", "ws_url", "region", "expires_in_seconds", "max_session_s"}`；失败抛 `_KeyError`。

        `region` 非空时随请求发给 Worker（`{"region": …}`）—— 那是**探测后的改道**（`region_route`）；
        空串 = 不给，Worker 按国家表 / 大洲表选默认（老行为逐字不变）。
        """
        import httpx
        if self._client is None:
            self._client = httpx.Client(timeout=self._timeout)
        r = self._client.post(endpoint(),
                              headers={"Authorization": f"Bearer {asr_endpoint.token(passcode)}",
                                       "X-ClassLive-Install": final_asr.install_id(),
                                       "X-ClassLive-Version": asr_endpoint.version_header()},
                              json={"region": self._region} if self._region else {})
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
      ⚠️ `terms` 的**顺序就是优先级**（本课词在前）—— 超 `terms_max` / 预算时先保前面的。
    - `context_text`: 课件大纲摘要（`context.text`，可选；受同一预算约束，截断到剩余额度）。
    - `terms_max`: 平时进 context 的条数目标（见模块常量 `TERMS_MAX`）。
    - `on_sealed`: `Callable[[Sealed], None]`，收线程**锁外**调（调用方只该入队）。
    - `clock` / `stable_s`: 透传给 `StreamCore`。
    - `request_key`: 注入的领 key 函数（默认 `_KeyClient`）。
    - `region`: 领 key 时带的 `region=`（`region_route` 探测后的改道；`""` = 不带、Worker 选默认）。
    - `key_timeout_s` / `keepalive_s` / `response_timeout_s` / `backlog_s` /
      `backlog_hold_s` / `zero_proc_timeout_s` / `finish_wait_s` /
      `audio_max_s` / `chunk_s` / `ws_url`: 判据用的小值接缝（生产全用模块常量）。
    """

    ENGINE = ENGINE

    def __init__(self, *, passcode: str, terms: Iterable[str] = (),
                 general: str = "", on_sealed: Callable[[Sealed], None] | None = None,
                 on_endpoint: Callable[[bool], None] | None = None,
                 clock: Callable[[], float] = time.monotonic, stable_s: float = STABLE_S,
                 request_key: Callable[[str], dict] | None = None,
                 key_timeout_s: float = KEY_TIMEOUT_S, keepalive_s: float = KEEPALIVE_S,
                 response_timeout_s: float = RESPONSE_TIMEOUT_S,
                 backlog_s: float = BACKLOG_S, backlog_hold_s: float = BACKLOG_HOLD_S,
                 zero_proc_timeout_s: float = ZERO_PROC_TIMEOUT_S,
                 finish_wait_s: float = FINISH_WAIT_S, audio_max_s: float = QUEUE_MAX_S,
                 chunk_s: float = CHUNK_S, ws_url: str = WS_URL, region: str = "",
                 context_text: str = "", terms_max: int = TERMS_MAX,
                 retry_delays_s: Iterable[float] = RETRY_DELAYS_S,
                 retry_reset_s: float = RETRY_RESET_S, retry_jitter: float = RETRY_JITTER,
                 send_stuck_s: float = SEND_STUCK_S) -> None:
        self._passcode = (passcode or "").strip()
        self._terms = tuple(str(t).strip() for t in terms if str(t).strip())
        self._general = general or ""
        self._context_text = context_text or ""
        #: context 在**构造期**就装好（terms/general/text 到收尾都不变）：
        # 启动日志要能立刻报「进 context 几条 / 截掉几条」（见 `metrics()`）。
        self._ctx, self._ctx_stats = build_context(
            self._terms, self._general, self._context_text, terms_max=terms_max)
        self._on_sealed = on_sealed
        #: ⭐ 3.11：Soniox `<end>`（语义端点）→ `on_endpoint(在两句之间?)`，收线程**锁外**调。
        self._on_endpoint = on_endpoint
        self._ep_n = 0
        self._ep_between = 0
        self._stable_s = float(stable_s)
        self._keepalive_s = float(keepalive_s)
        self._response_timeout_s = float(response_timeout_s)
        self._backlog_s = float(backlog_s)
        self._backlog_hold_s = float(backlog_hold_s)
        self._zero_proc_timeout_s = float(zero_proc_timeout_s)
        self._finish_wait_s = float(finish_wait_s)
        self._retry_delays_s = tuple(max(0.0, float(d)) for d in retry_delays_s)
        self._retry_reset_s = float(retry_reset_s)
        self._retry_jitter = max(0.0, float(retry_jitter))
        self._send_stuck_s = float(send_stuck_s)
        #: 正在 `ws.send` 里的起始时刻（`None` = 没在发）。看门狗线程读，只有发送线程写。
        self._send_since: float | None = None
        #: ⭐ 逐会话记录（P3）：每个会话结束记一条，进 `metrics()["sessions"]`。
        #: 以前只留最后一次的 close code，4 次重连各自为什么掉、跑了多久全不知道。
        self._sess_log: list[dict] = []
        self._t_start = time.monotonic()
        self._chunk_s = float(chunk_s)
        self._ws_url = ws_url
        self._max_chunks = max(1, int(float(audio_max_s) / self._chunk_s))
        self._key = request_key or _KeyClient(timeout=key_timeout_s, region=region).request_key

        self._lock = threading.Lock()               # 保护 core / 状态字段
        self._clock = clock
        self._core = StreamCore(clock=clock, stable_s=stable_s)
        #: ⚠️ **真的有界**（`maxsize`）：满了 `put_nowait` 抛 `Full` → 丢最旧。
        #    手写「先看 qsize 再 put」不是判据（多线程下照涨），真踩过：队列永远不满、从不丢。
        #: 元素 = `(这块第一个样本在本流里的位置, pcm)`：重连后要知道新会话的 0 ms 对应流里哪一刻。
        self._audio: "queue.Queue[tuple[int, bytes]]" = queue.Queue(maxsize=self._max_chunks)
        #: 本次会话的时间偏移（毫秒）：Soniox 每条连接的 token 时间都从 0 起，
        #: 加上「这条连接发出的第一块音频在流里的位置」才是流时间。`None` = 这条连接还没发过音频。
        #: ⚠️ 不加的话重连后新 token 全落在水位线之前被当旧词丢掉 → 整节课不再封口、
        #:    `alive()` 却还是真（本地也不接手）—— 2026-10-18 合并检查抓到。
        self._sess_off_ms: int | None = None
        #: 本次会话**已经发出去**的样本数 —— 判「服务端落后多少」的被减数。
        #: ⚠️ 与 `total_audio_proc_ms` 一样都是**会话相对**口径（重连后从 0 起）；
        #:    不要拿 `_sess_off_ms` 加绝对时刻再相减（差值会抵消，守卫却会被骗）。
        self._sent_samples = 0
        #: 最近一条消息里的 `total_audio_proc_ms`（会话相对毫秒）。
        #: `-1` = 还没收到（0 是**合法值**：刚开流时服务端确实处理到 0 ms）。
        self._proc_rel_ms = -1.0
        #: 收到那条进度消息的**同一刻**已经发出去多少毫秒音频。用这个跟
        #: `_proc_rel_ms` 相减，才是「消息到达时服务端落后多少」—— 不能拿
        #: 现在的 `_sent_samples` 去减旧消息，否则消息间隔（~1.5 s）会被算成落后。
        self._proc_sent_ms = 0.0
        self._last_msg_at = 0.0
        self._msg_gap_max = 0.0
        #: 带 `total_audio_proc_ms` 的消息序号 / 起计那条的序号。hold 必须等到**新读数**
        #: 仍落后才动作 —— 只拿一条冻结读数等时间过去，会把「服务端已追平但下一条消息
        #: 还没到」误判成持续落后（OCR 2026-10-21 第 3 条）。
        self._proc_seq = 0
        self._backlog_seq = 0
        self._backlog_since: float | None = None
        self._backlog_max_s = 0.0
        #: `total_audio_proc_ms` 一直为 0 的起计时刻（`None` = 没在 0 上卡住）。
        self._zero_proc_since: float | None = None
        #: 起计时那条消息的序号；兜底也必须等到**新读数**仍为 0，
        #: 否则后续缺 `total` 字段的消息会让 0 冻结、15s 后误判（OCR 第三轮第 1 条）。
        self._zero_proc_seq = 0
        #: 这次重连要不要「跳到实时」（丢弃积压音频）。timeout / stale 置位；
        #: 普通断线不清，按原样续传（10 s 队列内的短缺口不丢内容）。
        self._skip_next = False
        self._skipped_s = 0.0
        self._sess_n = 0
        self._reconnects = 0
        self._events: "queue.Queue[tuple]" = queue.Queue()
        self._dropped = 0
        self._dropped_samples = 0                   # 队列满丢掉的样本数（算 dropped_s 用）
        self._samples = 0                           # 采到的样本总数（含丢掉的）
        #: ⭐ 3.10 生产量具：第一块音频的墙钟（封口延迟的零点，见 `seal_latency_s`）。
        self._audio_t0: float | None = None
        #: ⭐ 3.10：从 `_fire` 出去的封口句数 + 每句的上移延迟（秒）。收尾 `metrics()` 用。
        self._sealed_n = 0
        self._seal_lat: list[float] = []
        self._marks: list[tuple[int, int]] = []     # (段首累计样本, 段号)
        self._seg = 0
        self._alive = False
        self._stopped = False
        self._finished = False
        self._fatal = False
        self._reason = ""
        #: ⭐ 3.10.1：退回原因是 `error` / `network` 时的**异常类型 + 一句话**（只进报告，
        #: 不上屏）。那次 `websockets` 没装上，报告里只有一个 `error`，猜了半天。
        self._detail = ""
        #: 掉线期间（上一个会话断了 → 新会话出词前）的累计秒数，进 `metrics()["down_s"]`。
        self._down_since: float | None = None
        self._down_s = 0.0
        self._manual = False                        # `force_local()` 关的（退回原因记 manual）
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

    def metrics(self) -> dict:
        """这一节课的只读快照（启动日志与收尾报告共用）。**随时可调、永不抛**。

        形状 = 收尾报告里的 `stream` 块（`.live.jsonl` 的 summary 行 / testmode 的
        `blocks.stream`）：`engine` / `fallback_reason`（`""` = 没退回）/ `sealed_n`
        （从 `_fire` 出去的封口句数）/ `seal_latency_s`（`{p50, p90, n}`，秒；`None` = 一句
        都没封）/ `reconnects` / `skipped_s` / `backlog_max_s` / `msg_gap_max_s` /
        `down_s` / `dropped_chunks` / `dropped_s` / `terms_n` / `terms_dropped`。
        ⚠️ 只记**事实** —— 指标怎么算归读的人（`scripts/class_report.py`）。
        """
        with self._lock:
            n = self._sealed_n
            lat = list(self._seal_lat)
            reason = self._reason
            detail = self._detail
            reconnects = self._reconnects
            skipped_s = self._skipped_s
            backlog_max_s = self._backlog_max_s
            msg_gap_max_s = self._msg_gap_max
            dropped = self._dropped
            dropped_samples = self._dropped_samples
            down_s = self._down_s + (time.monotonic() - self._down_since
                                     if self._down_since is not None else 0.0)
            sessions = [dict(x) for x in self._sess_log]
        st = self._ctx_stats
        return {"engine": ENGINE,
                "fallback_reason": reason,
                "fallback_detail": detail,
                "sealed_n": n,
                "seal_latency_s": _pcts(lat),
                #: ⭐ 2026-10-21：卡顿/断流的可观测性（报告里直接看得到）
                "reconnects": int(reconnects),
                "down_s": round(float(down_s), 1),      # 掉线期间 Soniox 没出词的累计秒数
                "skipped_s": round(float(skipped_s), 1),
                "backlog_max_s": round(float(backlog_max_s), 1),
                "msg_gap_max_s": round(float(msg_gap_max_s), 2),
                "dropped_chunks": int(dropped),
                "dropped_s": round(float(dropped_samples) / float(SR), 1),
                "terms_n": int(st.get("terms_n") or 0),
                "terms_dropped": int(st.get("terms_dropped") or 0),
                #: 每个会话一条：`at` 开始于第几秒 / `ran_s` 跑了多久 / `why` 怎么结束的 / `detail` close code 等
                "sessions": sessions}

    def feed(self, samples) -> None:
        """音频回调：float32 样本 → 有界队列。**绝不阻塞**，满了丢最旧。"""
        try:
            arr = np.asarray(samples, dtype="float32")
            n = int(arr.size)
            if n == 0:
                return
            pcm = (np.clip(arr, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
            with self._lock:
                start = self._samples
                self._samples += n
                if self._audio_t0 is None:
                    self._audio_t0 = self._clock()   # ⭐ 3.10：封口延迟的零点（只写一次）
            item = (start, pcm)
            try:
                self._audio.put_nowait(item)
            except queue.Full:
                # 先腾一格（可能消费者刚好取走了最后一块），再试着把新块放进去。
                # ⚠️ 记账按**真正没进队的那一块**：消费者取空时新块其实放得下，
                #    不能只加 `_dropped` 却漏 `_dropped_samples`（OCR 第三轮第 2 条）。
                old = None
                try:
                    old = self._audio.get_nowait()
                except queue.Empty:
                    pass
                dropped_item = None
                try:
                    self._audio.put_nowait(item)
                except queue.Full:
                    dropped_item = item
                else:
                    if old is not None:
                        dropped_item = old
                if dropped_item is not None:
                    with self._lock:
                        self._dropped += 1
                        self._dropped_samples += len(dropped_item[1]) // 2
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

    def sentence_wall_t0(self, end_s: float) -> float | None:
        """封口句「句尾音频」对应的**墙钟**（`_clock()` 同源）。

        `main` 用它填 `tr_latency.done(seg_t0=…)`：Soniox 的段号是跳的，
        `seg_ended_at[seg]` 在 Soniox 模式下指不到那一句（10-20 只有 179/1191 句有值，
        且起点是本地 VAD 段尾，不是这句的句尾）。用句尾音频 + 首块音频墙钟才是
        用户真正等的时间。`_audio_t0` 没设（还没喂过音频）→ None。
        """
        with self._lock:
            t0 = self._audio_t0
        return None if t0 is None else float(t0) + float(end_s)

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

    def unsealed_words(self) -> list[str]:
        """未封口那段的词（语意封口用，见 `StreamCore.unsealed_words`）。"""
        with self._lock:
            return self._core.unsealed_words()

    def seal_through_word(self, k: int, expect: Sequence[str] | None = None) -> bool:
        """语意封口：前 `k+1` 个词当一句送出去（见 `StreamCore.seal_through_word`）。

        和 `_tick` 同一个写法：锁里只改核心，`on_sealed` 回调在锁外发。
        """
        with self._lock:
            ok = self._core.seal_through_word(k, expect)
            sealed = self._core.take_sentences() if ok else []
        for s in sealed:
            self._fire(s, record_lat=False)
        return ok

    def stop(self) -> None:
        """收尾：发空文本帧，最多等 `finish_wait_s` 收 final，再关。可重复调。"""
        with self._lock:
            self._stopped = True
        # ⚠️ 已经停过也要等：暗号切回本地（`force_local`）的那条后台线程可能还在收尾，
        #    下课这一下直接返回的话，最后半句的 `on_sealed` 会落在 `sealedq` 判空之后（丢句）。
        self._thread.join(timeout=self._finish_wait_s + 2.0)

    def force_local(self) -> None:
        """手动退回本地（暗号「菠萝丁」，`hidden_switch`）：像收尾一样关掉，但原因记 `manual`。

        ⚠️ 会阻塞到收尾完成（最多 `finish_wait_s`）—— 调用方放后台线程（`StreamGate.force_local`）。
        """
        with self._lock:
            if self._stopped or self._finished:
                return
            self._manual = True
        self.stop()

    # ---- 控制线程

    def _run(self) -> None:
        reason = "stopped"
        try:
            if not self._passcode:
                reason = "no_passcode"
            else:
                reason = self._attempts()
        except Exception as e:                      # noqa: BLE001
            self._note_detail(e)
            reason = "error"
        finally:
            self._finish(reason)

    def _note_detail(self, e: BaseException) -> None:
        """记下最近一次失败的异常（类型 + 前 120 字）。永不抛。"""
        try:
            with self._lock:
                self._detail = f"{type(e).__name__}: {str(e)[:120]}"
        except Exception:                           # noqa: BLE001
            pass

    def _attempts(self) -> str:
        """连接 → 掉线 → 退避重连，直到预算用完才退回本地。返回退回原因（`""` = 干净停下）。

        ⭐ 2026-10-26 自愈：从前整节课只给**一次**重连（`_retried` 永不还原），第二次掉线就
        永久退回（10-23 ECON10770：12:06 用掉唯一一次，12:31 又被掐 → 后 19 min、38% 的课退回本地）。
        现在按 `RETRY_DELAYS_S` 退避重连，且上一个会话稳定跑过 `RETRY_RESET_S` 就把预算还原。
        致命类（key 被拒 / 余额用完 / 额度）不在可重连之列，照旧立刻退回。

        ⚠️ 判死时先看 `_pending`：会话常常是「先 N 秒无响应、服务端随后关连接」——
        照返回值会记成 `disconnected`，把真正的原因盖掉。
        """
        fails = 0
        while True:
            t0 = time.monotonic()
            err = self._session()
            ran = time.monotonic() - t0
            with self._lock:
                if self._stopped:
                    return ""
                if self._fatal:
                    return "error"
                if not err:
                    return "disconnected"           # 收线程死了但会话没报错
                if err == "disconnected" and self._pending:
                    err = self._pending
                if len(self._sess_log) < SESS_LOG_MAX:
                    self._sess_log.append({"at": round(t0 - self._t_start, 1), "ran_s": round(ran, 1),
                                           "why": err, "detail": self._detail[:40]})
            if err not in ("network", "timeout", "disconnected", "stale"):
                return err
            if ran >= self._retry_reset_s:
                fails = 0                           # 上一个会话稳定跑过：预算还原
            if fails >= len(self._retry_delays_s):
                return err
            base = self._retry_delays_s[fails]
            fails += 1
            with self._lock:
                self._down_since = time.monotonic()
                if base >= RETRY_SKIP_AFTER_S:
                    self._skip_next = True          # 等这么久，旧音频没必要续传
            # 抖动：同一次故障掐掉的人别同一秒撞回去（AWS Builders' Library：封顶退避 + 抖动）
            delay = base * (1.0 + random.uniform(-self._retry_jitter, self._retry_jitter)) if base > 0 else 0.0
            if delay > 0 and not self._wait(delay):
                return ""

    def _wait(self, seconds: float) -> bool:
        """退避等待；`stop()` 随时打断（返回 False）。"""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            with self._lock:
                if self._stopped:
                    return False
            time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
        with self._lock:
            return not self._stopped

    def _session(self) -> str:
        """一次完整会话。返回 `""`（正常收尾）或失败原因。"""
        try:
            key = self._key(self._passcode)
        except _KeyError as e:
            return _key_reason(e)
        except Exception as e:                      # noqa: BLE001
            self._note_detail(e)
            return "network"
        ws_url = str(key.get("ws_url") or self._ws_url)

        import ws_compat
        wsc = ws_compat.client()
        try:
            ws = wsc.connect(ws_url, additional_headers={
                "Authorization": f"Bearer {key['api_key']}"}, open_timeout=5.0,
                close_timeout=1.0, ping_interval=20.0, legacy=True)
        except Exception as e:                      # noqa: BLE001
            self._note_detail(e)
            return "network"

        # ⭐ 2026-10-21：timeout / stale 之后的重连要**跳到实时**，不是把积压续传。
        #    实时字幕慢 8.6 s 比丢几秒更糟；丢掉的几秒留给课后补译（音频还在）。
        with self._lock:
            self._sess_n += 1
            if self._sess_n > 1:
                self._reconnects += 1
            skip = self._skip_next
            self._skip_next = False
            self._proc_rel_ms = -1.0
            self._proc_sent_ms = 0.0
            self._sent_samples = 0
            self._proc_seq = 0
            self._backlog_seq = 0
            self._backlog_since = None
            self._zero_proc_since = None
            self._zero_proc_seq = 0
            self._last_msg_at = time.monotonic()
            self._send_since = None
        if skip:
            dropped = 0
            while True:
                try:
                    _start, pcm = self._audio.get_nowait()
                except queue.Empty:
                    break
                dropped += len(pcm) // 2
            self._skipped_s += dropped / float(SR)
            # 旧会话挂着的那半句先封出去（有内容就上屏，不跟新会话的词混在一起）
            with self._lock:
                sealed = self._core.flush()
            for s in sealed:
                self._fire(s)

        # ⚠️ 新会话要**清掉上一次的遗留事件**（尤其收线程死时留下的那条 `("dead")`）——
        #    不清的话重连后的第一个循环就把新连接判死（真踩过：重连明明成功却立刻退回）。
        with self._lock:
            self._pending = ""                      # 上次的死因作废
            self._sess_off_ms = None                # 新连接的 0 ms 等第一块音频定
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
                if self._down_since is not None:
                    self._down_s += time.monotonic() - self._down_since
                    self._down_since = None
            wd_stop = threading.Event()
            threading.Thread(target=self._send_watchdog, args=(ws, wd_stop), name="stream-asr-wd",
                             daemon=True).start()
            try:
                err = self._send_loop(ws)
            finally:
                wd_stop.set()
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
            self._note_close(ws, err)
        return err

    def _send_watchdog(self, ws, stop: threading.Event) -> None:
        """发送线程在 `ws.send` 里卡超过 `send_stuck_s` → 掐掉 socket，让会话按 `stale` 重连。

        ⚠️ 必须是**另一条线程**：发送线程自己卡在 `sendall` 里，没法判自己；库的 ping 超时要拿
        同一把 `protocol_mutex`，也救不了（见 `SEND_STUCK_S`）。`close_socket()` 不拿锁，
        它 shutdown 之后卡住的 `sendall` 立刻抛 OSError，发送线程走正常的「掉线」出口。
        """
        while not stop.wait(0.5):
            since = self._send_since
            if since is None or time.monotonic() - since < self._send_stuck_s:
                continue
            with self._lock:
                self._pending = "stale"
                self._skip_next = True
                self._detail = f"send stuck >{self._send_stuck_s:g}s"
            try:
                ws.close_socket()
            except Exception:                       # noqa: BLE001
                pass
            return

    def _note_close(self, ws, err: str) -> None:
        """服务端关连接的 close code + 原因记进 `fallback_detail`（只在掉线类原因时）。永不抛。

        10-23 那次 `fallback_detail` 是空的，只知道「被掐了」，不知道是谁掐的、为什么。
        """
        if err not in ("disconnected", "timeout", "stale"):
            return
        try:
            code = getattr(ws, "close_code", None)
            why = str(getattr(ws, "close_reason", "") or "")[:80]
            if code is not None or why:
                with self._lock:
                    # ⚠️ 看门狗先写了「send stuck」：socket 是我们自己掐的，close 1006 只是结果，
                    #    别拿它盖掉真正的原因（不然报告里又只剩一句看不出原因的 1006）。
                    if self._detail.startswith("send stuck"):
                        self._detail = f"{self._detail}; close {code}".strip()
                    else:
                        self._detail = f"close {code} {why}".strip()
        except Exception:                           # noqa: BLE001
            pass

    def _config(self) -> dict:
        """config 消息。`context` 在构造期就装好了（`build_context`），这里只搬 —— 重连时也一致。

        ⚠️ 预算按官方口径收口：8 000 token ≈ 10 000 字符、留一成余量，且
        **terms + general + text 合起来**算（官方 context 页，2026-10-17 核对）。
        从前是 30 000 字符的客户端上限（≈ 官方 3 倍）—— 词表一大就会踩 `invalid_request`
        （= 整节课退回本地）。被拿掉的条数在 `metrics()` 里记账，启动日志/报告都会写出来。
        """
        cfg: dict = {"model": "stt-rt-v5", "audio_format": "pcm_s16le",
                     "sample_rate": SR, "num_channels": 1,
                     "language_hints": ["en"], "enable_endpoint_detection": True}
        if self._ctx:
            cfg["context"] = self._ctx
        return cfg

    def _send_loop(self, ws) -> str:
        """推音频 / keepalive / 判死。返回 `""`（正常收尾）或失败原因。

        ⚠️ 判死看 **`got`**（最近一条**服务端消息**），不看 `last_audio`/`last_send`
        （那是客户端自己在发）。Soniox 在静音/只发 keepalive 时也会每 ~1 s 回一条
        进度消息（2026-10-21 实测），所以「有音频在推」不等于「服务端还活着」。
        旧写法 `max(got, last_audio, last_send)` 让 `last_audio` 每块都刷新 →
        永不触发（10-20 ECON10740 的 2m41s 静默没有任何日志）。
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
            # ⚠️ 只看 `got`（服务端消息）：Soniox 在静音/keepalive 时每 ~1 s 回一条
            #    进度消息（2026-10-21 实测），所以持续有音频并不代表服务端活着。
            #    旧写法 `max(got, last_audio, last_send)` 里 last_audio 每块都刷新 →
            #    永不触发（10-20 的 2m41s 静默没有任何日志，就是这么来的）。
            if now - got > self._response_timeout_s:
                with self._lock:
                    self._pending = "timeout"
                    self._skip_next = True          # 15 s 的积压不值得续传，跳到实时
                return "timeout"
            # 消息还在来、但内容落后已发送音频 → 流「跟不上实时」（10-20 卡完稳定
            # 慢 8.6 s 就是这个形状）。用官方 `total_audio_proc_ms` 判，不猜。
            # ⚠️ 两个量都是**会话相对**：`_proc_sent_ms` 是收到那条进度消息时
            #    已发出的量，`_proc_rel_ms` 是那条消息里的服务端进度 —— 相减才是
            #    「消息到达时服务端落后多少」。拿**现在**的 `_sent_samples` 去减旧消息
            #    会把消息间隔（~1.5 s）算成落后，正常抖动也可能误判。
            if self._proc_rel_ms > 0 and self._proc_sent_ms > 0:
                lag_ms = self._proc_sent_ms - self._proc_rel_ms
                if lag_ms > self._backlog_max_s * 1000.0:
                    self._backlog_max_s = lag_ms / 1000.0
                if lag_ms > self._backlog_s * 1000.0:
                    if self._backlog_since is None:
                        self._backlog_since = now
                        self._backlog_seq = self._proc_seq
                    # hold 必须等到**新读数**仍落后：只拿一条冻结读数等时间过去，
                    # 会把「服务端已追平、下一条消息还没到」误判成持续落后。
                    elif (now - self._backlog_since >= self._backlog_hold_s
                          and self._proc_seq > self._backlog_seq):
                        with self._lock:
                            self._pending = "stale"
                            self._skip_next = True
                        return "stale"
                else:
                    self._backlog_since = None
            else:
                self._backlog_since = None
            # ⚠️ 服务端一直回 `total=0`（消息在来、但一毫秒音频都没处理）：
            #    上面的 `got` 判死会被「消息在来」骗过，落后判据又因 `>0` 跳过。
            #    开流/大 context 就绪时 0 合法，所以给足 grace；持续 15 s 仍是 0 → stale。
            if self._proc_rel_ms == 0 and self._proc_sent_ms > 0:
                if self._zero_proc_since is None:
                    self._zero_proc_since = now
                    self._zero_proc_seq = self._proc_seq
                elif (now - self._zero_proc_since >= self._zero_proc_timeout_s
                      and self._proc_seq > self._zero_proc_seq):
                    # 必须等到新的进度消息仍是 0：缺 `total` 字段的消息不更新序号，
                    # 不能让一条冻结的 0 读数把正常流误判（OCR 第三轮第 1 条）。
                    with self._lock:
                        self._pending = "stale"
                        self._skip_next = True
                    return "stale"
            else:
                self._zero_proc_since = None
            if dead:                                # 收线程死了
                return "disconnected"
            try:
                start, pcm = self._audio.get(timeout=0.2)
            except queue.Empty:
                now = time.monotonic()
                if now - last_audio > self._keepalive_s and now - last_send > self._keepalive_s:
                    try:                            # ⚠️ 发送失败按断线处理，绝不停在这
                        self._send_since = time.monotonic()
                        ws.send(json.dumps({"type": "keepalive"}))
                    except Exception:               # noqa: BLE001
                        return "disconnected"
                    finally:
                        self._send_since = None
                    last_send = now
                continue
            last_audio = time.monotonic()
            if self._sess_off_ms is None:           # 只有本线程写；收线程不碰
                self._sess_off_ms = start * 1000 // SR
            try:
                buf = np.frombuffer(pcm, dtype="<i2")
                if carry.size:
                    buf = np.concatenate((carry, buf))
                i = 0
                while i + chunk_samples <= buf.size:
                    self._send_since = time.monotonic()
                    ws.send(buf[i:i + chunk_samples].tobytes())
                    self._send_since = None
                    self._sent_samples += chunk_samples
                    last_send = time.monotonic()
                    i += chunk_samples
                carry = buf[i:].copy()
            except Exception:                       # noqa: BLE001
                self._send_since = None
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
            if kind in ("msg", "end"):
                if self._last_msg_at > 0:
                    self._msg_gap_max = max(self._msg_gap_max, got - self._last_msg_at)
                self._last_msg_at = got
            if kind == "err":
                if classify_error(payload) == "fatal":
                    fatal = True
            elif kind == "msg":
                if len(payload) >= 3:
                    fin, nf, tot = payload[0], payload[1], payload[2]
                else:                               # 兼容旧事件形状（收线程现在恒发 3 元组）
                    fin, nf = payload
                    tot = None
                off = self._sess_off_ms
                if off is None:                     # 没发过音频就来了词（只有假服务会）：按当前流位置
                    with self._lock:
                        off = self._samples * 1000 // SR
                if off:
                    fin = [t._replace(start_ms=t.start_ms + off, end_ms=t.end_ms + off) for t in fin]
                    nf = [t._replace(start_ms=t.start_ms + off, end_ms=t.end_ms + off) for t in nf]
                if isinstance(tot, (int, float)) and not isinstance(tot, bool):
                    # 官方进度字段：`total_audio_proc_ms` 是**本会话**相对时刻。
                    # 与 `_sent_samples` 同口径；不要加 `_sess_off_ms`（见字段注释）。
                    with self._lock:
                        self._proc_rel_ms = float(tot)
                        self._proc_sent_ms = self._sent_samples * 1000.0 / SR
                        self._proc_seq += 1
                with self._lock:
                    self._core.feed(fin, nf)
                    sealed = self._core.take_sentences()
                for s in sealed:
                    self._fire(s)
            elif kind == "end":
                with self._lock:
                    between = self._core.at_boundary()
                    self._ep_n += 1
                    self._ep_between += int(between)
                cb = self._on_endpoint
                if cb is not None:
                    try:
                        cb(between)
                    except Exception:               # noqa: BLE001
                        pass                        # 调用方的错不该打死收线程
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
                if not (isinstance(et, str) and et):
                    ec = msg.get("error_code")         # 只给数字码的错误帧：按数字串分类
                    et = str(ec) if isinstance(ec, int) and not isinstance(ec, bool) else ""
                if et:
                    self._events.put(("err", et))
                    continue
                toks = msg.get("tokens")
                if not isinstance(toks, list):
                    continue
                parsed = parse_tokens(toks)
                fin = [t for t in parsed if t.is_final]     # 逐 token 的 is_final（官方形状）
                nf = [t for t in parsed if not t.is_final]
                # ⭐ 官方进度字段（2026-10-21 核对 Audio progress tracking）：服务端处理到
                #    哪一刻。空 `tokens` 的进度消息也带它 —— 这是「流跟不上实时」的唯一
                #    可靠判据（不能拿客户端发了多少音频冒充）。
                tot = msg.get("total_audio_proc_ms")
                if not isinstance(tot, (int, float)) or isinstance(tot, bool):
                    tot = None
                self._events.put(("msg", (fin, nf, tot)))
                # ⭐ 3.11：`<end>` 在 `parse_tokens` 里被当控制帧丢了 —— 这里单独报一声
                #    （排在这条消息的词之后：先吃完它之前的词，再判是不是在两句之间）。
                if any(isinstance(t, dict) and t.get("text") == "<end>" for t in toks):
                    self._events.put(("end", None))
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

    def _fire(self, s: Sealed, *, record_lat: bool = True) -> None:
        # ⭐ 3.10 量具：计数 + 上移延迟（墙钟 − 句尾音频时刻）。锁内只做加法，微秒级。
        #    语意封口（`record_lat=False`）不进延迟分布：它的「延迟」是我们故意等的，
        #    混进去会把 `seal_latency_s` 这个健康指标拉歪。
        with self._lock:
            self._sealed_n += 1
            if record_lat and len(self._seal_lat) < SEAL_LAT_MAX:
                lat = seal_latency_s(self._clock(), self._audio_t0, s.end_s)
                if lat is not None:
                    self._seal_lat.append(lat)
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
                self._reason = "manual" if self._manual else ""
            else:
                self._reason = reason or "error"
        # 收尾后不再投递（`_finish_stream` 已 flush 过一次；这里只是把残留清干净）
        try:
            self._core.flush()
        except Exception:                           # noqa: BLE001
            pass
