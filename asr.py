"""ASR 抽象: 把一段 16kHz mono float32 音频转成英文文本。

默认为 sherpa-onnx + NVIDIA Parakeet-TDT-0.6B-v3(int8)。
只暴露 `transcribe(samples) -> str`, 无内部状态, 便于替换成 whisper 等后端。
线程安全: 内部加锁(草稿线程与定稿线程会并发调用同一 OfflineRecognizer)。

电平问题在 capture.PeakNormalizer 统一处理(Parakeet 对绝对电平敏感), 这里不再做。
"""
from __future__ import annotations
import glob, os, threading


def _find_file(dirname: str, patterns) -> str:
    """按 patterns 的**优先级**取第一个命中的文件。

    ⚠️ 两个坑:
    1) glob 的返回顺序取决于文件系统目录迭代顺序(OS 相关且不确定), 不排序则
       同一目录在不同机器/不同次运行可能选到不同文件;
    2) 通配("*.txt")命中多个时**不许猜** —— 猜错会静默用错词表(README.txt 也是
       .txt), 或 int8 与非 int8 混用, 而识别结果是"看起来正常但全错"。报出来
       让调用方决定。精确名(无通配)最多命中一个, 不受此限。
    """
    for pat in patterns:
        hits = sorted(glob.glob(os.path.join(dirname, pat)))
        if not hits:
            continue
        if "*" in pat and len(hits) > 1:
            names = [os.path.basename(h) for h in hits[:6]]
            raise FileNotFoundError(
                f"模型目录 {dirname} 里 {pat} 命中 {len(hits)} 个文件, "
                f"无法确定用哪个: {names}{' …' if len(hits) > 6 else ''}")
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
