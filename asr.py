"""ASR 抽象: 把一段 16kHz mono float32 音频转成英文文本。

默认为 sherpa-onnx + NVIDIA Parakeet-TDT-0.6B-v3(int8)。
只暴露 `transcribe(samples) -> str`, 无内部状态, 便于替换成 whisper 等后端。
线程安全: 内部加锁(草稿线程与定稿线程会并发调用同一 OfflineRecognizer)。

电平问题在 capture.PeakNormalizer 统一处理(Parakeet 对绝对电平敏感), 这里不再做。
"""
from __future__ import annotations
import glob, os, re, threading


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
        # 三个文件各自独立解析, 所以"encoder 是 int8、decoder 是 fp32"这种混用不会
        # 报错 —— 而结果是"看起来正常但全错"(与 _find_file 里那条通配歧义同一个坑,
        # 只是跨文件)。官方发布的模型三件套精度一致, 不一致只可能是目录里混放了版本。
        if len({".int8.onnx" in p for p in (enc, dec, join)}) > 1:
            raise FileNotFoundError(
                f"模型目录 {model_dir} 里 int8 与非 int8 混用: "
                f"{[os.path.basename(p) for p in (enc, dec, join)]}")
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
    """加载草稿/兜底 ASR(Parakeet)。

    ⚠️ **别给这个模型挂热词偏置**(`hotwords_file`) —— 本机实测(2026-09-24)是三条死路:
      ① 用默认 `greedy_search` 挂热词 → 直接 ValueError(它要求 modified_beam_search)
      ② 换 `modified_beam_search` + `modeling_unit="bpe"` 但**不给 `bpe_vocab`**
         → **SIGSEGV 段错误, 整个进程当场死**。上课中途崩, 正是最不能发生的事。
      ③ 就算参数配全了也不划算: 光换 beam search 就词数 −36%、CPU +39%;
         而热词权重没有可用区间 —— 2.0 完全不触发, 8.0 输出崩坏, 20.0 直接背诵热词表。
    详见 `docs/experiments/hotwords_ab.py`。
    """
    return ParakeetASR(model_dir)


# ---- 定稿用: Whisper(large-v3-turbo) ----
# 为什么值得多背一个模型: 同一批真实课堂录音上实测比 Parakeet 强不少 ——
#   有效词数 +33%、段尾无终止标点率 22%→14%、空转写 9%→2%
# 逐段对照能看到它把 Parakeet 听错的词听对了("part"→"pot"、"he jokes"→"heat up")。
# 代价是慢 8 倍(4.0× vs 31.8× 实时), 所以**只用在定稿路径**, 草稿仍走 Parakeet
# (草稿每秒就要一份, 4× 实时根本供不上)。
#
# ⚠️ whisper 在难段上会**退化**: 吐 '... ... ...' 或整句复读。实测一个 11 段的窗口
# 里 3 段如此。调用方**必须**过 `is_degenerate()` 并回退 Parakeet —— 否则省略号会
# 直接糊在字幕上。

# ⚠️ 两处阈值都是"宁可漏杀不可错杀"量出来的:
#   词级要求**同词相邻 4 次以上** —— 3 次会误杀 "the the the"(转录中很常见);
#   句级要求**同一整句连刷 3 次** —— 2 次会误杀 "Okay. Okay. So..."(讲师真实口语)。
_DEGEN_TOKEN = re.compile(r"(\b[\w']+)(?:\s+\1){3,}", re.IGNORECASE)
_SENT_SPLIT = re.compile(r"[^.!?]+")


def _phrase_repeat(text: str, k: int = 3) -> bool:
    """同一句连刷 k 次?── whisper 的经典退化, 如 "Thank you. Thank you. Thank you."。

    按句末标点切句后看有没有 k 个**完全相同**的相邻句。比正则回溯稳, 也不会
    被 "Okay. Okay. So now..." 这种真实口语误伤。"""
    sents = [s.strip().lower() for s in _SENT_SPLIT.findall(text) if len(s.strip()) >= 4]
    for i in range(len(sents) - k + 1):
        if len(set(sents[i:i + k])) == 1:
            return True
    return False


def is_degenerate(text: str) -> bool:
    """whisper 的幻觉/复读输出。判据刻意保守: 只抓明显的退化, 不误杀正常课堂句。"""
    if not text:
        return False
    if text.count("...") >= 2:
        return True
    if _DEGEN_TOKEN.search(text) or _phrase_repeat(text):
        return True
    parts = text.split()
    return len(parts) > 20 and len(set(parts)) <= 2


class WhisperASR:
    """sherpa-onnx Whisper 包装。接口与 ``ParakeetASR`` 一致: ``transcribe(samples) -> str``。"""

    def __init__(self, model_dir: str, num_threads: int = 4, language: str = "en"):
        import sherpa_onnx

        def pick(*names: str) -> str:
            for n in names:
                p = os.path.join(model_dir, n)
                if os.path.exists(p):
                    return p
            raise FileNotFoundError(f"{model_dir} 里找不到 {' 或 '.join(names)}")

        enc = pick("turbo-encoder.int8.onnx", "turbo-encoder.onnx")
        dec = pick("turbo-decoder.int8.onnx", "turbo-decoder.onnx")
        # 与 ParakeetASR 同一条校验: 三件套精度必须一致, 混用会"看着正常全错"
        if len({".int8.onnx" in p for p in (enc, dec)}) > 1:
            raise FileNotFoundError(
                f"模型目录 {model_dir} 里 int8 与非 int8 混用: "
                f"{[os.path.basename(p) for p in (enc, dec)]}")
        self._rec = sherpa_onnx.OfflineRecognizer.from_whisper(
            encoder=enc, decoder=dec,
            tokens=pick("turbo-tokens.txt", "tokens.txt"),
            language=language, num_threads=num_threads)
        self._lock = threading.Lock()

    def transcribe(self, samples) -> str:
        if len(samples) < 1600:            # 不足 0.1s
            return ""
        with self._lock:
            stream = self._rec.create_stream()
            stream.accept_waveform(16000, samples)
            self._rec.decode_stream(stream)
            return stream.result.text.strip()


def load_final_asr(model_dir: str):
    """定稿路径的 ASR(Whisper-turbo)。**必需模型 —— 缺失直接抛, 不做静默回退。**

    为什么不留回退: 定稿模型是转写质量的主要来源(实测有效词数 +33%、
    段尾无终止标点 22%→14%、空转写 9%→2%)。静默少掉它 = "看着正常但打了折",
    而用户永远不会知道。**启动时说清楚, 比课上悄悄降质好。**
    """
    try:
        return WhisperASR(model_dir)
    except Exception as e:                                # noqa: BLE001
        raise RuntimeError(
            f"定稿模型加载失败: {model_dir}\n"
            f"    原因: {e}\n"
            f"    这是必需模型 —— 跑 `cl doctor` 看怎么装。") from e
