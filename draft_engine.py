#!/usr/bin/env python3
"""草稿引擎：选哪个模型出草稿、怎么喂、坏了怎么退。

## 契约（`main.partial_worker` 只依赖这一个方法）

    eng = load_draft_engine(parakeet_asr, window_samples=PARTIAL_MAX_S * SR)   # 永不抛
    text, used = eng.transcribe(buf, tag)     # used ∈ {"nemotron", "parakeet"}；永不抛
    tail = eng.finish(buf, tag)               # 段尾：本段完整假设；没有 → ""；永不抛
    ends = eng.sentence_ends(tag)             # 本段已封口的句末时刻（秒，相对段首）；没有 → ()；永不抛
    eng.name / eng.reason / eng.fallback_reason                               # 只读

- `buf` 是**本段从段首累积的**样本（`Segmenter._buf()`），`tag` 是 put 那一刻的段号。
  流式引擎每段一条流、只喂新增样本 —— latest-wins 丢掉中间几份也不丢样本，下一份 `buf` 里全有。
- Parakeet（窗口型）只看最近 `window_samples`，与 R3 之前的行为逐字相同。

⚠️ 线程：**只能在一个线程里用**（partial_worker）。sherpa 的 OnlineStream 非线程安全；
   本类不加锁 —— 加了也挡不住第二个线程造成的「流状态错乱」，所以干脆规定单线程。
   唯一例外是 `sentence_ends`：它只读草稿线程**整个替换**的一个元组（不碰 sherpa），
   给 `vad.Segmenter` 在音频线程里读（3.9.4 句末切段）。
⚠️ 熔断后**本节课不再试**（不来回抖）：退回 Parakeet，直到进程结束。
⚠️ 段中间**不补静音、不调 `input_finished`**：那会结束这条流，后面再喂的样本全丢。
   段**尾**才补（`finish`，3.9.4）：这条流本来就要扔了，补一截静音让前瞻里那几个词解出来。

为什么单独成模块：`partial_worker` 是 `main.run()` 里的嵌套闭包，默认闸门够不到；
选择、喂流、熔断、回退全放这里，用假识别器做毫秒级判据（`tests/test_draft_engine.py`）。
"""
from __future__ import annotations

import os
import time
from typing import Any, Callable, Mapping

import models

ENV = "CLASSLIVE_DRAFT_ENGINE"                # auto | parakeet | nemotron（默认 auto）
ENV_MODEL_DIR = "CLASSLIVE_DRAFT_MODEL_DIR"   # 只给测试 / 排查用：指向别的模型目录
MODES = ("auto", "parakeet", "nemotron")
MIN_RAM_BYTES = 12 * 2**30    # auto 的内存门：模型常驻实测 +1073 MB（P0，OCW 全课）
SLOW_S, SLOW_STRIKES = 0.9, 3  # 连续 3 次单次更新 > 0.9 s 熔断（P0 实测 p95 0.15 s）
NUM_THREADS = 2
TAIL_PAD_S = 0.8               # 段尾补的静音（见 `NemotronDrafter.finish`）
SR = 16000
END_PUNCT = (".", "?", "!")


def sealed_ends(tokens, timestamps) -> tuple:
    """纯函数：**已封口**的句末标点时刻 —— 句末标点后面又开了一个新词（token 以空格开头）。

    ⚠️ 只认封口的：段尾那个还没下文的句号随时可能被改掉（流式假设会回改最后几个 token）。
    ⚠️ 时间戳是**模型发出该 token 的时刻**，比音频里那个停顿**晚**（10-12 真课 69 个句号：
       停顿中心中位在时间戳前 0.50 s）—— 用的人自己往前找，见 `vad.find_sentence_cut`。
    """
    toks, ts = list(tokens), list(timestamps)
    return tuple(float(ts[i]) for i in range(min(len(toks), len(ts)) - 1)
                 if toks[i].strip() in END_PUNCT and toks[i + 1].startswith(" "))


def choose(mode: str, model_present: bool, ram_bytes: int | None) -> tuple[str, str]:
    """纯函数：`(引擎名, 理由)`。理由空串 = 没什么要说的。

    - `mode` 非法 → 当 auto
    - parakeet → 手动指定
    - nemotron → 模型在就用（**不看内存**，用户明说要）；不在 → parakeet
    - auto → 模型不在 / 内存已知且不足 → parakeet；内存未知 → 放行并写明
    """
    mode = mode if mode in MODES else "auto"
    if mode == "parakeet":
        return "parakeet", "手动指定"
    if not model_present:
        return "parakeet", "流式模型未下载"
    if mode == "nemotron":
        return "nemotron", "手动指定"
    if ram_bytes is None:
        return "nemotron", "内存未知"
    if ram_bytes < MIN_RAM_BYTES:
        return "parakeet", f"内存 {ram_bytes / 2**30:.0f} GB < {MIN_RAM_BYTES // 2**30} GB"
    return "nemotron", ""


class NemotronDrafter:
    """一条 sherpa `OnlineRecognizer`，每段一条流。`update` 会抛（由 `DraftEngine` 兜）。"""

    def __init__(self, model_dir: str, num_threads: int = NUM_THREADS, *, rec=None):
        if rec is None:
            import sherpa_onnx

            from asr import _find_file

            def f(name: str) -> str:
                # 只给**精确名**；不许退到 `*.txt` 猜词表（`asr.ParakeetASR` 那段注释讲过为什么）
                return _find_file(model_dir, [name])

            rec = sherpa_onnx.OnlineRecognizer.from_transducer(
                tokens=f("tokens.txt"), encoder=f("encoder.int8.onnx"),
                decoder=f("decoder.int8.onnx"), joiner=f("joiner.int8.onnx"),
                num_threads=num_threads, sample_rate=SR, feature_dim=80,
                decoding_method="greedy_search",
                # ⚠️ 关掉 endpoint：分段归 `Segmenter` 管，两套分段同时开会互相打架
                enable_endpoint_detection=False, provider="cpu")
        self._rec = rec
        self._stream = None
        self._tag: object = object()
        self._fed = 0
        self._finished: object = object()     # 最近 `finish` 过的段号
        #: `(段号, 封口句末时刻…)` —— 草稿线程**整个替换**，音频线程只读（见模块头「线程」）
        self.ends: tuple = (None, ())

    def update(self, buf, tag) -> str:
        """喂到 `buf` 末尾，返回本段当前假设。已经 `finish` 过的段 → `""`（晚到的旧缓冲，丢掉）。"""
        if tag == self._finished:
            return ""
        if self._stream is None or tag != self._tag or len(buf) < self._fed:
            self._stream = self._rec.create_stream()
            self._tag, self._fed = tag, 0
        if len(buf) > self._fed:
            self._stream.accept_waveform(SR, buf[self._fed:])
            self._fed = len(buf)
        while self._rec.is_ready(self._stream):
            self._rec.decode_stream(self._stream)
        try:
            r = self._rec.get_result_all(self._stream)
            self.ends = (tag, sealed_ends(r.tokens, r.timestamps))
        except Exception:                     # noqa: BLE001 —— 拿不到时间戳只是不给提示，草稿照出
            self.ends = (tag, ())
        return self._rec.get_result(self._stream).strip()

    def finish(self, buf, tag) -> str:
        """段落结束：喂完剩下的样本 + 补 `TAIL_PAD_S` 静音 + `input_finished` → 本段**完整**假设。

        ⭐ 为什么（2026-10-12 SOC10020 真课回放，47 段对上 Groq 定稿）：不收尾时最后一条草稿
           比定稿平均少 3.6 个词（WER 26.6%）——模型的 560 ms 前瞻里那截从没被解码；
           收尾后少 2.2 个词（WER 20.3%），代价 p50 211 ms / p95 369 ms（草稿线程上）。
           补 0.5 / 0.8 / 1.2 s 差别在 0.3 个百分点内 → 取 0.8。
        ⚠️ 只在段尾调：`input_finished` 之后这条流**不能再喂**（模块头那条）；之后同一 `tag`
           再来 `update` 会因为 `_stream` 被清空而新开一条流（不会往死流里喂）。
        """
        if self._stream is None or tag != self._tag or len(buf) < self._fed:
            # 没跑过草稿 → 不额外花一整段的解码。硬切挪回了一截（模型已经听过切点后面）→ 不补尾，
            # 那会把下一段的词也带进来；也**不截到句号**（3.9.4 试过）：草稿槽只有一格，截掉的那几个词
            # 要等下一段第一份草稿（实测 ~2 s）才回来 —— 屏上会先消失再出现，不如原样挂着等下一段接上。
            return ""
        if len(buf) > self._fed:
            self._stream.accept_waveform(SR, buf[self._fed:])
        import numpy as np
        self._stream.accept_waveform(SR, np.zeros(int(TAIL_PAD_S * SR), dtype=np.float32))
        self._stream.input_finished()
        while self._rec.is_ready(self._stream):
            self._rec.decode_stream(self._stream)
        text = self._rec.get_result(self._stream).strip()
        self._stream, self._finished = None, tag
        return text


class DraftEngine:
    def __init__(self, parakeet, streaming=None, *, window_samples: int, reason: str = "",
                 clock: Callable[[], float] = time.monotonic):
        self._parakeet = parakeet
        self._streaming = streaming
        self._window = int(window_samples)
        self._clock = clock
        self._strikes = 0
        self.reason = reason
        self.fallback_reason = ""

    @property
    def name(self) -> str:
        """当前生效的引擎：`nemotron` 或 `parakeet`（熔断后变 parakeet）。"""
        return "nemotron" if self._streaming is not None else "parakeet"

    def transcribe(self, buf, tag) -> tuple[str, str]:
        if self._streaming is not None:
            try:
                t0 = self._clock()
                text = self._streaming.update(buf, tag)
                self._strikes = self._strikes + 1 if self._clock() - t0 > SLOW_S else 0
                if self._strikes >= SLOW_STRIKES:
                    self._trip(f"太慢：连续 {SLOW_STRIKES} 次 > {SLOW_S}s")
                return text, "nemotron"          # 这一份照常用（已经算出来了）
            except Exception as e:               # noqa: BLE001
                self._trip(f"出错：{e!r}")        # 落到下面的 Parakeet，**这一份不丢**
        return self._parakeet.transcribe(buf[-self._window:]), "parakeet"

    def finish(self, buf, tag) -> str:
        """段尾收一次（见 `NemotronDrafter.finish`）。Parakeet / 熔断后 / 出错 → `""`（= 不补，旧行为）。**永不抛。**

        ⚠️ 不计入「太慢」熔断：这一下本来就要多解 0.8 s 静音，与逐秒草稿不是一个量级。
        """
        if self._streaming is None:
            return ""
        try:
            return self._streaming.finish(buf, tag)
        except Exception as e:               # noqa: BLE001
            self._trip(f"出错：{e!r}")
            return ""

    def sentence_ends(self, tag) -> tuple:
        """本段（`tag`）最近一次草稿里已封口的句末时刻。Parakeet / 熔断后 / 不是这一段 → `()`。**永不抛**。"""
        try:
            got_tag, ends = self._streaming.ends if self._streaming is not None else (None, ())
            return ends if got_tag == tag else ()
        except Exception:                     # noqa: BLE001
            return ()

    def _trip(self, why: str) -> None:
        self._streaming = None
        self.fallback_reason = why


def _ram_bytes() -> int | None:
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return None


def _mode(env: Mapping[str, str]) -> str:
    return (env.get(ENV) or "auto").strip().lower()


def want_model(env: Mapping[str, str] = os.environ,
               ram_bytes: Callable[[], int | None] | None = None) -> bool:
    """这台机器装了 Nemotron 会不会用上？给更新卡片问「要不要提议下载」。

    与 `load_draft_engine` 走**同一个** `choose`（假设模型已在）—— 内存不够 / 手动指定 parakeet
    的机器装了也用不上，就别让它下 464 MB。**永不抛**（出错当「不要」）。
    """
    try:
        return choose(_mode(env), True, (ram_bytes or _ram_bytes)())[0] == "nemotron"
    except Exception:                            # noqa: BLE001
        return False


def load_draft_engine(parakeet, *, window_samples: int, env: Mapping[str, str] = os.environ,
                      ram_bytes: Callable[[], int | None] | None = None,
                      make_drafter: Callable[[str], Any] = NemotronDrafter) -> DraftEngine:
    """**永不抛**。任何一步失败 → Parakeet 引擎，`reason` 写清楚。

    「模型在不在」用 `doctor.model_present`（全仓唯一判据，目录非空）。
    """
    try:
        import doctor
        mode = _mode(env)
        model_dir = env.get(ENV_MODEL_DIR) or models.path_of("nemotron")
        ram = (ram_bytes or _ram_bytes)()
        name, why = choose(mode, bool(model_dir) and doctor.model_present(model_dir), ram)
        if name == "parakeet":
            return DraftEngine(parakeet, window_samples=window_samples, reason=why)
        return DraftEngine(parakeet, make_drafter(model_dir),
                           window_samples=window_samples, reason=why)
    except Exception as e:                       # noqa: BLE001
        return DraftEngine(parakeet, window_samples=window_samples,
                           reason=f"流式模型加载失败：{e!r}")
