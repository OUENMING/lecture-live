#!/usr/bin/env python3
"""草稿引擎：选哪个模型出草稿、怎么喂、坏了怎么退。

## 契约（`main.partial_worker` 只依赖这一个方法）

    eng = load_draft_engine(parakeet_asr, window_samples=PARTIAL_MAX_S * SR)   # 永不抛
    text, used = eng.transcribe(buf, tag)     # used ∈ {"nemotron", "parakeet"}；永不抛
    eng.name / eng.reason / eng.fallback_reason                               # 只读

- `buf` 是**本段从段首累积的**样本（`Segmenter._buf()`），`tag` 是 put 那一刻的段号。
  流式引擎每段一条流、只喂新增样本 —— latest-wins 丢掉中间几份也不丢样本，下一份 `buf` 里全有。
- Parakeet（窗口型）只看最近 `window_samples`，与 R3 之前的行为逐字相同。

⚠️ 线程：**只能在一个线程里用**（partial_worker）。sherpa 的 OnlineStream 非线程安全；
   本类不加锁 —— 加了也挡不住第二个线程造成的「流状态错乱」，所以干脆规定单线程。
⚠️ 熔断后**本节课不再试**（不来回抖）：退回 Parakeet，直到进程结束。
⚠️ 段中间**不补静音、不调 `input_finished`**：那会结束这条流，后面再喂的样本全丢。
   补尾只在「拿流式结果当整段定稿」时需要（P2 断网兜底，`docs/PLAN-streaming-asr.md` §0.1）。

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
SR = 16000


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

    def update(self, buf, tag) -> str:
        """喂到 `buf` 末尾，返回本段当前假设。"""
        if self._stream is None or tag != self._tag or len(buf) < self._fed:
            self._stream = self._rec.create_stream()
            self._tag, self._fed = tag, 0
        if len(buf) > self._fed:
            self._stream.accept_waveform(SR, buf[self._fed:])
            self._fed = len(buf)
        while self._rec.is_ready(self._stream):
            self._rec.decode_stream(self._stream)
        return self._rec.get_result(self._stream).strip()


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
