#!/usr/bin/env python3
"""`NemotronFinal` —— 流式模型（Nemotron）拿**整段音频重新解码**，当本地定稿用。

## 为什么要有它

2026-11-20 桌面 D1：本地只留一个流式模型 Nemotron，Parakeet / Whisper 删掉。
可定稿兜底（云端不通时整节课的字幕来源）不能空着 —— 于是这里把**同一条流式模型**
当离线模型使：

    stream = rec.create_stream()
    stream.accept_waveform(16000, 整段)
    stream.accept_waveform(16000, TAIL_PAD_S 秒静音)   # ⚠️ 不补尾=吞掉段尾的词
    stream.input_finished()
    while rec.is_ready(stream): rec.decode_stream(stream)
    text = rec.get_result(stream)

⚠️ **不补尾不行**：`docs/PLAN-streaming-asr.md` §0.1 实测（OCW 20 段），不补尾的 1120 ms
   模型吞掉段尾近 1 s 的词（WER 16.2%），补 2.0 s → 10.3%。补的零要真解码，不是白送
   （RTF ≈ 0.15 → 0.8 s 约 120 ms）。草稿层 `NemotronDrafter.finish` 用的同一个数。

## 接口

与原来 `asr.ParakeetASR` 在 `FinalASR` 里扮演的角色**同形**：

    nf = NemotronFinal(model_dir)          # 不加载模型（只解析路径）
    text, lp = nf.transcribe_lp(samples)   # 首次调用才真加载；线程安全

`lp` 恒为 `None`：流式结果的 logprob 不是逐句对齐的（`get_result_all` 的 y 是整段 token 序列），
没有可信的平均值可比，而 `FinalResult.logprob` 只喂测试模式的统计 —— 宁缺毋滥。

## ⚠️ 线程：**自己的 recognizer 实例**，懒加载

草稿在 `partial_worker`、定稿在 `final_worker`，两个线程同时 decode 是常态。
`sherpa_onnx.OnlineRecognizer` 的 C API（`lib/python3.12/site-packages/sherpa_onnx/include/
sherpa-onnx/c-api/c-api.h`）**没有声明**多个 host 线程对同一 recognizer 并发
`SherpaOnnxDecodeOnlineStream` 是安全的；官方给并行的入口是 `DecodeMultipleOnlineStreams`
（内部线程池）。而 Python 侧默认**不释放 GIL**，两个线程本来就跑不到一起 →
共用一条 recognizer 只省内存、换不来并发，却要赌一把未文档化的线程安全。
→ 所以：**定稿用自己的一份 recognizer**，且**懒加载**（云端定稿失败才建）。
内存实测见 `docs/PLAN-drop-parakeet-20261110.md` 末尾「2026-11-20 实施」。

本类的 `_lock` 只保护**同一个实例**被多个线程调用（定稿线程自己 + 可能的离线量具）。
"""
from __future__ import annotations

import os
import threading
from typing import Any, Callable

SR = 16000
NUM_THREADS = 4
#: 段尾补的静音（秒）。与草稿层 `draft_engine` 共用同一个数 —— 唯一定义点在这里。
TAIL_PAD_S = 0.8


def make_recognizer(model_dir: str, num_threads: int = NUM_THREADS):
    """建一条 sherpa `OnlineRecognizer`（int8 三件套）。**不缓存、不共享** —— 谁要谁建。

    `from_transducer` 的其余参数与草稿层逐字相同（`draft_engine.NemotronDrafter`）：
    分段归 `Segmenter` 管，所以关掉 endpoint detection。
    """
    import sherpa_onnx

    from asr import _find_file

    def f(name: str) -> str:
        # 只给**精确名**；不许退到 `*.txt` 猜词表（`asr._find_file` 的 docstring 讲过为什么）
        return _find_file(model_dir, [name])

    return sherpa_onnx.OnlineRecognizer.from_transducer(
        tokens=f("tokens.txt"), encoder=f("encoder.int8.onnx"),
        decoder=f("decoder.int8.onnx"), joiner=f("joiner.int8.onnx"),
        num_threads=num_threads, sample_rate=SR, feature_dim=80,
        decoding_method="greedy_search",
        enable_endpoint_detection=False, provider="cpu")


class NemotronFinal:
    """整段解码适配器。构造**不加载模型**（只核对文件在不在）；首次 `transcribe_lp` 才建。

    `rec` / `loader` 是测试缝（假识别器、假加载器），生产走默认。
    """

    def __init__(self, model_dir: str, *, tail_pad_s: float = TAIL_PAD_S,
                 num_threads: int = NUM_THREADS,
                 loader: Callable[[str], Any] | None = None, rec: Any = None):
        from asr import _find_file       # 路径在**构造时**核对：缺文件别拖到课上第一次兜底

        for name in ("tokens.txt", "encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx"):
            _find_file(model_dir, [name])
        self._dir = model_dir
        self._pad = int(tail_pad_s * SR)
        self._loader = loader or (lambda d: make_recognizer(d, num_threads))
        self._rec = rec
        # ⚠️ 必须是 **RLock**：`transcribe_lp` 拿着锁再走 `_get()`（同线程），普通 `Lock`
        #    会当场自锁死 —— 2026-11-20 头一版就是这么挂住的（`sample` 里停在
        #    `lock_PyThread_acquire_lock`）。
        self._lock = threading.RLock()

    @property
    def loaded(self) -> bool:
        return self._rec is not None

    def _get(self):
        """调用方**必须**已持有 `self._lock`（`transcribe_lp` 就是）。"""
        if self._rec is None:
            self._rec = self._loader(self._dir)
        return self._rec

    def transcribe(self, samples) -> str:
        return self.transcribe_lp(samples)[0]

    def transcribe_lp(self, samples) -> tuple[str, float | None]:
        """整段 → 文本。不足 0.1 s 给空串（与 `ParakeetASR` 同一条规矩）。**永不抛到线程外**的
        担保在调用方（`final_asr` 接住异常），这里照常抛 —— 静默吞掉就没人知道模型坏了。"""
        if len(samples) < SR // 10:
            return "", None
        import numpy as np

        with self._lock:
            rec = self._get()
            stream = rec.create_stream()
            stream.accept_waveform(SR, np.asarray(samples, dtype="float32"))
            stream.accept_waveform(SR, np.zeros(self._pad, dtype=np.float32))
            stream.input_finished()
            while rec.is_ready(stream):
                rec.decode_stream(stream)
            return rec.get_result(stream).strip(), None


def load_final_asr(model_dir: str) -> NemotronFinal:
    """与 `final_asr.load_final` 对接的那个名字。**缺失直接抛**（由调用方决定怎么办）。"""
    return NemotronFinal(os.path.expanduser(model_dir))
