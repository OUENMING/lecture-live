"""语音分段器: 用 Silero VAD(深度学习)判语音, 把流切成"一句""句"。

相比能量阈值: 免疫空调/键盘/翻书噪声, 对轻声更灵敏。

三个关键机制:
1. **Silero VAD**(sherpa-onnx): 逐 512 样本窗判语音, 块内多数票。
2. **梯级静音阈值**: 句子越长越收紧, 在讲师换气处切句(而非 15s 机械硬切):
     dur<3s → 0.6s | 3–7s → 0.30s | >7s → 0.25s; 绝对上限 12s
3. **Pre-roll 300ms**: 静音→说话跳变时把前 300ms 补进语音头部, 消除清辅音吃字。

回调必须快速返回(重活交给调用方 worker 线程)。
"""
from __future__ import annotations
import time
from collections import deque
import numpy as np

SR = 16000
CHUNK = 1600                     # 0.1s
CHUNK_DUR = CHUNK / SR
MIN_UTTERANCE_S = 0.6
MAX_UTTERANCE_S = 12.0
HANGOVER = 0.35                  # 说话结束后保持"说话"这么久(防词间短停顿)
PARTIAL_INTERVAL_S = 1.0
PRE_ROLL_CHUNKS = 3              # 3 × 0.1s = 300ms
VAD_MODEL = "~/models/vad/silero_vad.onnx"
VAD_THRESHOLD = 0.4              # Silero 语音概率阈值。别为了"更灵敏"调低:
                                 # 实测 0.2 在弱信号下能多触发, 但 VAD 会近乎恒
                                 # 为"有语音", 句子再也断不开, 只能靠 12s 硬切,
                                 # 正常音量下反而丢词(样本: 78→63)。灵敏度应该
                                 # 靠 capture.PeakNormalizer 补电平, 不是降阈值。


class SileroVad:
    """sherpa-onnx Silero VAD 包装。is_speech(chunk) 按 512 样本窗多数票判定。
    模型缺失时自动退化为能量阈值(仍可用, 只是抗噪差)。"""

    def __init__(self, model_path: str, threshold: float = VAD_THRESHOLD):
        import os
        import sherpa_onnx
        path = os.path.expanduser(model_path)
        if not os.path.exists(path):
            raise FileNotFoundError(f"VAD 模型不存在: {path}")
        cfg = sherpa_onnx.VadModelConfig()
        cfg.silero_vad.model = path
        cfg.sample_rate = SR
        cfg.silero_vad.threshold = threshold
        self._vad = sherpa_onnx.VadModel.create(cfg)
        self._ws = self._vad.window_size()
        self._rem = np.zeros(0, dtype=np.float32)

    def is_speech(self, chunk: np.ndarray) -> bool:
        buf = np.concatenate([self._rem, chunk.astype(np.float32)])
        n = (len(buf) // self._ws) * self._ws
        if n == 0:
            self._rem = buf
            return False
        votes = total = 0
        for i in range(0, n, self._ws):
            total += 1
            if self._vad.is_speech(buf[i:i + self._ws]):
                votes += 1
        self._rem = buf[n:]
        return votes * 2 >= total          # 多数票


class _EnergyVad:
    """退化方案: RMS 能量 + 噪声地板。"""

    def __init__(self, min_rms: float = 0.008):
        self.min_rms = min_rms
        self.noise_floor = min_rms

    def is_speech(self, chunk: np.ndarray) -> bool:
        r = float(np.sqrt(np.mean(chunk ** 2))) if chunk.size else 0.0
        sp = r > max(self.min_rms, self.noise_floor * 2.5)
        if not sp:
            self.noise_floor = 0.99 * self.noise_floor + 0.01 * max(r, 1e-6)
        return sp


def _make_vad(model_path: str = VAD_MODEL, threshold: float = VAD_THRESHOLD):
    try:
        return SileroVad(model_path, threshold)
    except Exception as e:                     # noqa: BLE001
        print(f"⚠ Silero VAD 不可用({e}); 回退能量 VAD。")
        return _EnergyVad()


def _end_silence_threshold(dur: float) -> float:
    """梯级静音阈值。**不能低于 0.45s**:
    英语从句/列举的换气停顿普遍 250–400ms, 只有句末(600–1000ms)才是真断句。
    阈值过小会把 "the mathematics that" 这类从句在关系词处腰斩, 导致翻译脑补。"""
    if dur < 3.0:
        return 0.70        # 短句: 防过早打碎
    if dur < 8.0:
        return 0.55        # 正常停顿
    return 0.45            # 长句: 需明显换气才切(绝不设 0.25)


class Segmenter:
    def __init__(self, on_partial, on_utterance_end, vad_model: str = VAD_MODEL):
        self.on_partial = on_partial
        self.on_utterance_end = on_utterance_end
        self._vad = _make_vad(vad_model)
        self._parts: list[np.ndarray] = []
        self._n = 0
        self._pre_roll: deque = deque(maxlen=PRE_ROLL_CHUNKS)
        self.is_speaking = False
        self.has_speech = False
        self.silence_run = 0.0
        self.last_partial = 0.0
        self.last_speech_s: float | None = None

    @property
    def dur(self) -> float:
        return self._n / SR

    def _buf(self) -> np.ndarray:
        if not self._parts:
            return np.zeros(0, dtype=np.float32)
        if len(self._parts) == 1:
            return self._parts[0]
        return np.concatenate(self._parts)

    def _reset(self) -> None:
        self._parts = []
        self._n = 0
        self.has_speech = False
        self.is_speaking = False
        self.silence_run = 0.0
        self.last_partial = time.monotonic()

    def flush(self) -> None:
        """音频结束时把当前缓冲强制定稿。"""
        if self.has_speech and self.dur >= MIN_UTTERANCE_S:
            buf = self._buf().copy()
            self._reset()
            self.on_utterance_end(buf)
        else:
            self._reset()

    def accept(self, chunk: np.ndarray) -> None:
        chunk = chunk.astype(np.float32, copy=False)
        sp = self._vad.is_speech(chunk)
        now = time.monotonic()

        if not self.has_speech:
            if sp:
                # 语音开始: 把 pre-roll(前 300ms)补进头部, 消除吃字
                self.has_speech = True
                self.is_speaking = True
                self.last_speech_s = now
                for old in self._pre_roll:
                    self._parts.append(old)
                    self._n += len(old)
                self._pre_roll.clear()
            else:
                self._pre_roll.append(chunk)
                return

        if sp:
            self.is_speaking = True
            self.silence_run = 0.0
            self.last_speech_s = now
        elif self.is_speaking and now - (self.last_speech_s or now) >= HANGOVER:
            self.is_speaking = False

        self._parts.append(chunk)
        self._n += len(chunk)

        # 连续静音时长(不受 hangover 影响, 否则阈值会被吃掉 0.35s)
        if sp:
            self.silence_run = 0.0
        else:
            self.silence_run += CHUNK_DUR

        dur = self.dur
        if (self.silence_run >= _end_silence_threshold(dur) and dur >= MIN_UTTERANCE_S) \
                or dur >= MAX_UTTERANCE_S:
            buf = self._buf().copy()
            self._reset()
            if len(buf) / SR >= MIN_UTTERANCE_S:
                self.on_utterance_end(buf)
        elif sp and now - self.last_partial >= PARTIAL_INTERVAL_S:
            self.last_partial = now
            self.on_partial(self._buf())
