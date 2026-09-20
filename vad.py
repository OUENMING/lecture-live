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
        # 本块里判为语音的窗占比(0~1), 供尾部诊断读 —— 0.4 阈值下"没过阈值"
        # 与"完全没有语音"是两件事, 后者才说明这块是真静音。
        self.last_ratio = 0.0

    def is_speech(self, chunk: np.ndarray) -> bool:
        buf = np.concatenate([self._rem, chunk.astype(np.float32)])
        n = (len(buf) // self._ws) * self._ws
        if n == 0:
            self._rem = buf
            self.last_ratio = 0.0
            return False
        votes = total = 0
        for i in range(0, n, self._ws):
            total += 1
            if self._vad.is_speech(buf[i:i + self._ws]):
                votes += 1
        self._rem = buf[n:]
        self.last_ratio = votes / total
        return votes * 2 >= total          # 多数票


class _EnergyVad:
    """退化方案: RMS 能量 + 噪声地板。"""

    def __init__(self, min_rms: float = 0.008):
        self.min_rms = min_rms
        self.noise_floor = min_rms
        self.last_ratio = 0.0              # 能量 VAD 只有二值, 见 SileroVad.last_ratio

    def is_speech(self, chunk: np.ndarray) -> bool:
        r = float(np.sqrt(np.mean(chunk ** 2))) if chunk.size else 0.0
        sp = r > max(self.min_rms, self.noise_floor * 2.5)
        if not sp:
            self.noise_floor = 0.99 * self.noise_floor + 0.01 * max(r, 1e-6)
        self.last_ratio = 1.0 if sp else 0.0
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
        # --- 尾部诊断(只计数, 绝不参与切分判定) ---
        # 对应 Handy 的 VadTailReport: 把"句尾被切掉 / 整段被丢"变成可观测的数字,
        # 而不是只能事后翻音频猜。四个数各有明确含义, 全为零时收尾不打印任何东西:
        #   cuts         正常静音断句次数
        #   hard_cuts    命中 12s 上限被硬切 —— 段内没有够长的静音, 切点不对齐词边界
        #   dropped      收尾时因不足 MIN_UTTERANCE_S 被整段丢弃 —— 真实的丢内容
        #   weak_blocks  说话期间判静音、但块里其实**有**语音窗的块数, 会让
        #                silence_run 多走一格。实测远场课堂里很小(6 块/3min), 不是
        #                断句不准的主因 —— 真正的大头是 hard_cuts。
        self.diag = {"cuts": 0, "hard_cuts": 0, "dropped": 0,
                     "dropped_s": 0.0, "weak_blocks": 0}

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
            if self.has_speech:        # 有语音但不够长 -> 整段丢弃, 记一笔
                self.diag["dropped"] += 1
                self.diag["dropped_s"] += self.dur
            self._reset()

    def report(self) -> str:
        """收尾诊断快照(对应 Handy 的 VadTailReport)。只报**发生过**的事,
        一切正常时返回空串 —— 不产生噪音。三个可疑数各自指向不同的修法。"""
        d = self.diag
        if not (d["hard_cuts"] or d["dropped"] or d["weak_blocks"]):
            return ""
        bits = [f"断句 {d['cuts']} 次"]
        if d["hard_cuts"]:
            bits.append(f"12s 硬切 {d['hard_cuts']} 次(段内没有够长的静音, 只能撞上限切)")
        if d["dropped"]:
            bits.append(f"收尾丢弃 {d['dropped']} 段/{d['dropped_s']:.1f}s"
                        f"(有语音但不足 {MIN_UTTERANCE_S}s)")
        if d["weak_blocks"]:
            bits.append(f"句内弱音 {d['weak_blocks']} 块(有语音窗却没过阈值)")
        return "🎧 VAD 诊断: " + " · ".join(bits)

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

        # 已在说话中、这块判静音、但块里确实有语音窗 -> VAD 漏检。
        # 记它是因为这类块会让 silence_run 提前走完, 是"切早了"的直接嫌疑。
        if not sp and float(getattr(self._vad, "last_ratio", 0.0)) > 0.0:
            self.diag["weak_blocks"] += 1

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
        silence_cut = (self.silence_run >= _end_silence_threshold(dur)
                       and dur >= MIN_UTTERANCE_S)
        hard_cut = dur >= MAX_UTTERANCE_S
        if silence_cut or hard_cut:
            # 诊断: 分得清"正常断句"和"撞上限硬切"—— 后者必然切在词中间
            if hard_cut and not silence_cut:
                self.diag["hard_cuts"] += 1
            else:
                self.diag["cuts"] += 1
            buf = self._buf().copy()
            self._reset()
            if len(buf) / SR >= MIN_UTTERANCE_S:
                self.on_utterance_end(buf)
        elif sp and now - self.last_partial >= PARTIAL_INTERVAL_S:
            self.last_partial = now
            self.on_partial(self._buf())
