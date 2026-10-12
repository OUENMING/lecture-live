"""ASR 抽象: 把一段 16kHz mono float32 音频转成英文文本。

默认为 sherpa-onnx + NVIDIA Parakeet-TDT-0.6B-v3(int8)。
只暴露 `transcribe(samples) -> str`, 无内部状态, 便于替换成 whisper 等后端。
线程安全: 内部加锁(草稿线程与定稿线程会并发调用同一 OfflineRecognizer)。

电平问题在 capture.PeakNormalizer 统一处理(Parakeet 对绝对电平敏感), 这里不再做。
"""
from __future__ import annotations
import glob, os, re, threading
from typing import Any, Callable

import numpy as np


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


def _parakeet_files(model_dir: str) -> tuple[str, str, str, str]:
    """`(encoder, decoder, joiner, tokens)` —— 只解析路径、不加载（缺 / 混放在这里就报）。"""
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
    # ⚠️⚠️ **tokens 只认精确名，不许退到 `*.txt`**（2026-09-29 修）。
    #    `_find_file` 自己的 docstring 就写着「猜错会**静默用错词表**
    #    （README.txt 也是 .txt）」—— 而这里原来传的是 `["tokens.txt", "*.txt"]`，
    #    正好是它警告的那种猜法：`tokens.txt` 缺失时，若目录里只有一个别的
    #    `.txt`，会把它**当词表**返回，识别结果是「看起来正常但全错」。
    #    ⚠️ 它不是"多个才报错"能挡住的 —— 命中**一个**时那条歧义闸门根本不触发。
    toks = _find_file(model_dir, ["tokens.txt"])
    return enc, dec, join, toks


class ParakeetASR:
    def __init__(self, model_dir: str, num_threads: int = 4):
        import sherpa_onnx
        enc, dec, join, toks = _parakeet_files(model_dir)
        self._rec = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=enc, decoder=dec, joiner=join, tokens=toks,
            num_threads=num_threads, model_type="nemo_transducer")
        self._lock = threading.Lock()

    def transcribe(self, samples) -> str:
        return self.transcribe_lp(samples)[0]

    def transcribe_lp(self, samples) -> tuple[str, float | None]:
        """文本 + 这一次调用对应的平均 logprob, **在锁内一起算出**(R8)。

        为什么要有它: 以前调用方在 `transcribe()` 返回后再读 `last_logprob`,
        而草稿线程同时在用同一个对象 —— 读到的可能是草稿那一次的值。
        所以 `last_logprob` 属性已删(全仓只有 `final_worker` 读它), 只留这条返回值。"""
        if len(samples) < 1600:            # 不足 0.1s
            return "", None
        with self._lock:                    # OfflineRecognizer 非线程安全
            stream = self._rec.create_stream()
            stream.accept_waveform(16000, samples)
            self._rec.decode_stream(stream)
            r = stream.result
            # 置信度随返回值给出, 供测试模式采集(`transcribe()` 仍只返回 str)。
            # 实测真实课堂平均 logprob ≈ −0.4、干净 TTS ≈ −0.013(差 30 倍),
            # 拿它当"这句可能没听准"的信号是可行的。
            # ⚠️ 走 `getattr` 与下面 Whisper 那份**对称**（2026-09-30 全量 OCR 审查发现）：
            #    sherpa 哪天不暴露这个属性，这里抛 AttributeError → 被 `final_worker` 的
            #    `except Exception` 吞成「⚠ 定稿失败」→ **整节课定稿全空**，而 Whisper 侧不会。
            _lp = getattr(r, "ys_log_probs", None)
            lp = float(np.mean(_lp)) if _lp is not None and len(_lp) else None
            return r.text.strip(), lp


class LazyASR:
    """首次 `transcribe` 才真加载的 Parakeet（与 `ParakeetASR` 同接口）。

    为什么：草稿走 Nemotron、定稿走云端时，Parakeet 只是**兜底**（云端失败 / 熔断 / R5 复核 /
    草稿熔断），常态一次都不用，却占约 1.1 GB 内存（本机实测 RSS +1053 / +1112 MB，加载 ~1.0 s）。
    代价：第一次兜底多等约 1 s。路径缺失 / 混放在**构造时**就报（`_parakeet_files`），
    不拖到课上；真加载失败（文件坏）在第一次兜底时抛 —— 调用方（`FinalASR` / `DraftEngine`）
    本来就接着异常。
    """

    def __init__(self, model_dir: str, loader: Callable[[str], Any] | None = None):
        _parakeet_files(model_dir)
        self._dir = model_dir
        self._loader = loader or ParakeetASR
        self._obj = None
        self._lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        return self._obj is not None

    def get(self):
        with self._lock:                    # 两个线程同时首用（草稿 + 定稿）只加载一份
            if self._obj is None:
                self._obj = self._loader(self._dir)
            return self._obj

    def transcribe(self, samples) -> str:
        return self.get().transcribe(samples)

    def transcribe_lp(self, samples) -> tuple[str, float | None]:
        return self.get().transcribe_lp(samples)


def load_asr(model_dir: str, *, lazy: bool = False):
    """加载草稿/兜底 ASR(Parakeet)。`lazy=True` → `LazyASR`（只核对文件，用到才加载）。

    ⚠️ **别给这个模型挂热词偏置**(`hotwords_file`) —— 本机实测(2026-09-24)是三条死路:
      ① 用默认 `greedy_search` 挂热词 → 直接 ValueError(它要求 modified_beam_search)
      ② 换 `modified_beam_search` + `modeling_unit="bpe"` 但**不给 `bpe_vocab`**
         → **SIGSEGV 段错误, 整个进程当场死**。上课中途崩, 正是最不能发生的事。
      ③ 就算参数配全了也不划算: 光换 beam search 就词数 −36%、CPU +39%;
         而热词权重没有可用区间 —— 2.0 完全不触发, 8.0 输出崩坏, 20.0 直接背诵热词表。
    详见 `docs/experiments/hotwords_ab.py`。
    """
    return LazyASR(model_dir) if lazy else ParakeetASR(model_dir)


# ---- 定稿用: Whisper(large-v3-turbo) ----
# 为什么值得多背一个模型: 8 窗口 / 95 段 / 3 份真实课堂录音实测 ——
#   有效词数 **+44%**(8/8 窗口一致, 同向概率 0.4%), 空转写 4%→0;
#   ⚠️ 段尾无终止标点 26%→16% 的幅度**落在噪声带内**(n≈11 段时底线 ±18pp),
#      不足以判定 —— 但逐段对照是定性硬证据
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
        return self.transcribe_lp(samples)[0]

    def transcribe_lp(self, samples) -> tuple[str, float | None]:
        """同 `ParakeetASR.transcribe_lp`: 文本与 logprob 在锁内一起返回。"""
        if len(samples) < 1600:            # 不足 0.1s
            return "", None
        with self._lock:
            stream = self._rec.create_stream()
            stream.accept_waveform(16000, samples)
            self._rec.decode_stream(stream)
            r = stream.result
            ys = getattr(r, "ys_log_probs", None)
            lp = float(np.mean(ys)) if ys is not None and len(ys) else None
            return r.text.strip(), lp


def load_final_asr(model_dir: str):
    """本地定稿 ASR(Whisper-turbo)。**缺失直接抛, 本函数不做静默回退。**

    3.9.0 起 Whisper 是**可选**模型: 定稿默认走云端, 本地只做兜底。
    `final_asr.load_final` 负责接住这里的异常并改用 Parakeet(并在启动时说清楚)。
    其余调用方(`scripts/stream_eval.py --final whisper`)仍把它当必需 —— 缺了就该报错。

    为什么本函数自己不回退: 定稿模型是转写质量的主要来源(实测**有效词数 +44%**,
    8/8 窗口一致)。静默少掉它 = "看着正常但打了折",
    而用户永远不会知道。**启动时说清楚, 比课上悄悄降质好。**
    """
    try:
        return WhisperASR(model_dir)
    except Exception as e:                                # noqa: BLE001
        raise RuntimeError(
            f"定稿模型加载失败: {model_dir}\n"
            f"    原因: {e}\n"
            f"    这是可选模型(云端转写不可用时才用它; 没有就用 Parakeet 兜底) —— 要装跑 `cl doctor` 看怎么装。") from e
