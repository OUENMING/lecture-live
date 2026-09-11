"""ASR 抽象: 把一段 16kHz mono float32 音频转成英文文本。

默认为 sherpa-onnx + NVIDIA Parakeet-TDT-0.6B-v3(int8)。
只暴露 `transcribe(samples) -> str`, 无内部状态, 便于替换成 whisper 等后端。
线程安全: 内部加锁(草稿线程与定稿线程会并发调用同一 OfflineRecognizer)。

电平问题在 capture.PeakNormalizer 统一处理(Parakeet 对绝对电平敏感), 这里不再做。
"""
from __future__ import annotations
import glob, os, threading


def _find_file(dirname: str, patterns) -> str:
    for pat in patterns:
        hits = glob.glob(os.path.join(dirname, pat))
        if hits:
            return hits[0]
    raise FileNotFoundError(
        f"模型目录 {dirname} 里找不到 {' 或 '.join(patterns)}")


class ParakeetASR:
    def __init__(self, model_dir: str, num_threads: int = 4):
        import sherpa_onnx
        enc = _find_file(model_dir, ["encoder.int8.onnx", "encoder.onnx"])
        dec = _find_file(model_dir, ["decoder.int8.onnx", "decoder.onnx"])
        join = _find_file(model_dir, ["joiner.int8.onnx", "joiner.onnx"])
        toks = _find_file(model_dir, ["tokens.txt", "*.txt"])
        self._rec = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=enc, decoder=dec, joiner=join, tokens=toks,
            num_threads=num_threads, model_type="nemo_transducer")
        self._lock = threading.Lock()

    def transcribe(self, samples) -> str:
        if len(samples) < 1600:            # 不足 0.1s
            return ""
        with self._lock:                    # OfflineRecognizer 非线程安全
            stream = self._rec.create_stream()
            stream.accept_waveform(16000, samples)
            self._rec.decode_stream(stream)
            return stream.result.text.strip()


def load_asr(model_dir: str):
    return ParakeetASR(model_dir)
