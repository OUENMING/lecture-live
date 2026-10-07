"""语音分段器: 用 Silero VAD(深度学习)判语音, 把流切成"一句""句"。

相比能量阈值: 免疫空调/键盘/翻书噪声, 对轻声更灵敏。

三个关键机制:
1. **Silero VAD**(sherpa-onnx): 逐 512 样本窗判语音, 块内多数票。
2. **梯级静音阈值**: 句子越长越收紧, 在讲师换气处切句(而非机械硬切):
     dur<3s → 0.70s | 3–8s → 0.55s | ≥8s → 0.45s; 绝对上限 12s
   (以 `_end_silence_threshold()` 为准。这里曾写 0.6/0.30/0.25 —— 是调参前的
    旧值, 与实现不符, 会误导照着注释改代码的人。)
3. **Pre-roll 300ms**: 静音→说话跳变时把前 300ms 补进语音头部, 消除清辅音吃字。

回调必须快速返回(重活交给调用方 worker 线程)。
"""
from __future__ import annotations
import time
from collections import deque
import numpy as np

# ⚠️ `models.py` **只有数据、没有 I/O** —— 所以核心音频模块可以放心 import 它，
#    而**不该**是 `import doctor`（那是个诊断脚本，依赖方向是反的）。
import models

SR = 16000
CHUNK = 1600                     # 0.1s
CHUNK_DUR = CHUNK / SR
MIN_UTTERANCE_S = 0.6
MAX_UTTERANCE_S = 12.0
# ⚠️⚠️ **`HANGOVER` 目前对行为没有任何影响**（2026-09-29 核实）——
#    它只用来把 `self.is_speaking` 从 True 翻回 False，而**那个标志的唯一读者
#    就是把它翻回来的那一行**（下面的 `elif`）。全仓没有别的地方读 `is_speaking`。
#    → 「防词间短停顿」这句**是假的**：切句只看 `silence_run` 与 `dur`。
#    ⚠️ 留着没删是因为它可能是有意给将来用的；**但它现在不做事，别照着这句注释改参**。
HANGOVER = 0.35
PARTIAL_INTERVAL_S = 1.0
PRE_ROLL_CHUNKS = 3              # 3 × 0.1s = 300ms
#: 连续判为"非语音"超过这么久 → 重建 VAD 状态（见 `SileroVad.reset`）。
#: ⚠️ **为什么不是在切句处重建**：实测（2026-10-07 围炉谈话那节）在**每个切句点**
#:    重建要 235 次、只把丢词压到 64；在**长静音**里重建只要 31 次、压到 50。
#:    静音里重建是零代价的（那时本来就没语音），而切句点可能贴着下一句的起头。
#: 阈值实测 0.6/0.8/1.0/1.5/2.0 s 都把丢词从 92 压到 49–61；1.5s 最省成本，取它。
RESET_AFTER_SILENCE_S = 1.5
VAD_MODEL = models.path_of("vad", "~/models/vad/silero_vad.onnx")
VAD_THRESHOLD = 0.4              # Silero 语音概率阈值。别为了"更灵敏"调低:
                                 # 实测 0.2 在弱信号下能多触发, 但 VAD 会近乎恒
                                 # 为"有语音", 句子再也断不开, 只能靠 12s 硬切,
                                 # 正常音量下反而丢词(样本: 78→63)。灵敏度应该
                                 # 靠 capture.PeakNormalizer 补电平, 不是降阈值。


class SileroVad:
    """sherpa-onnx Silero VAD 包装。is_speech(chunk) 按 512 样本窗多数票判定。
    模型缺失时自动退化为能量阈值(仍可用, 只是抗噪差)。"""

    #: 内部状态会随时间漂移（见 `reset` 的 docstring）→ `Segmenter` 会在长静音里重建它。
    drift_prone = True

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

    def reset(self) -> None:
        """把 VAD 内部状态清回"刚加载"的样子。

        ⭐ **为什么需要它**（2026-10-07 实测，围炉谈话那节录音）：Silero 的状态会
           **漂移** —— 连续音频喂到 ~25s 后，它对一段真人语音给 **0/69** 个语音窗；
           同一段音频**单独喂是 33/72**，在它前面 2s 处 `reset()` 后是 43/69。
           漂移期间它判"非语音"的块，会被 `Segmenter` 当静音 → 那段话整段丢。
        → 所以 `Segmenter` 在**长静音**里调它（见 `RESET_AFTER_SILENCE_S`）：静音里
          重建状态零代价，而漂移一被清掉，紧跟着的语音立刻能检出。
        """
        self._vad.reset()
        self._rem = np.zeros(0, dtype=np.float32)
        self.last_ratio = 0.0


class _EnergyVad:
    """退化方案: RMS 能量 + 噪声地板。"""

    #: 没有会漂移的内部状态 → `Segmenter` 不重建它（`reset` 是空操作）。
    drift_prone = False

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

    def reset(self) -> None:
        """空操作 —— 能量 VAD **没有** Silero 那种会漂移的内部状态，且它的
        `noise_floor` 是**学习出来的**，清掉反而是倒退（那是它抗噪的全部依据）。"""
        pass


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
        self._pre_roll: deque = deque()          # 存 `(chunk, 该块的 last_ratio)`
        self.is_speaking = False
        self.has_speech = False
        self.silence_run = 0.0
        self.last_partial = 0.0
        self.last_speech_s: float | None = None
        #: 连续"非语音"累计秒数（**只在还没起段时**走，与 `silence_run` 是两回事：
        #: `silence_run` 只在 has_speech 之后才计）。到 `RESET_AFTER_SILENCE_S` 重建 VAD。
        self._blind_s = 0.0
        # --- 尾部诊断(只计数, 绝不参与切分判定) ---
        # 对应 Handy 的 VadTailReport: 把"句尾被切掉 / 整段被丢"变成可观测的数字,
        # 而不是只能事后翻音频猜。四个数各有明确含义, 全为零时收尾不打印任何东西:
        #   cuts         正常静音断句次数
        #   hard_cuts    命中 12s 上限被硬切 —— 段内没有够长的静音, 切点不对齐词边界
        #   dropped      收尾时因不足 MIN_UTTERANCE_S 被整段丢弃 —— 真实的丢内容
        #   weak_blocks  说话期间判静音、但块里其实**有**语音窗的块数, 会让
        #                silence_run 多走一格。实测远场课堂里很小(6 块/3min), 不是
        #                断句不准的主因 —— 真正的大头是 hard_cuts。
        #   vad_resets   长静音里重建 VAD 状态的次数（清 Silero 状态漂移，见
        #                `SileroVad.reset`）。正常为个位数/节；很高 = 状态漂移频繁。
        #   blind_dropped 因"判非语音而从没起段"被丢掉的块数（**只统计有语音窗的块**）：
        #                这是 `dropped` **看不见**的那一类真实丢内容（见 `accept`）。
        self.diag = {"cuts": 0, "hard_cuts": 0, "dropped": 0,
                     "dropped_s": 0.0, "weak_blocks": 0,
                     "vad_resets": 0, "blind_dropped": 0}

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
        self._blind_s = 0.0
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
        # ⚠️ `vad_resets` **不进这个守卫**（2026-10-07 OCR 审查指出）：它只要有一处
        #    ≥`RESET_AFTER_SILENCE_S` 的普通停顿就会自增 —— 那是**每节课的常态**，
        #    不是异常。让它参与非空判定，`report()` 就几乎永远非空，违背本函数
        #    「一切正常时返回空串，不产生噪音」的契约（`main.py` 收尾依赖它）。
        #    它和 `cuts` 一样：**只在别的条目已经触发时**顺带报出来。
        if not (d["hard_cuts"] or d["dropped"] or d["weak_blocks"]
                or d["blind_dropped"]):
            return ""
        bits = [f"断句 {d['cuts']} 次"]
        if d["hard_cuts"]:
            bits.append(f"12s 硬切 {d['hard_cuts']} 次(段内没有够长的静音, 只能撞上限切)")
        if d["dropped"]:
            bits.append(f"收尾丢弃 {d['dropped']} 段/{d['dropped_s']:.1f}s"
                        f"(有语音但不足 {MIN_UTTERANCE_S}s)")
        if d["weak_blocks"]:
            bits.append(f"句内弱音 {d['weak_blocks']} 块(有语音窗却没过阈值)")
        if d["blind_dropped"]:
            bits.append(f"⭐盲区丢块 {d['blind_dropped']} 块"
                        f"(判非语音却含语音窗 → 整段没进转录)")
        if d["vad_resets"]:
            bits.append(f"重建 VAD {d['vad_resets']} 次(清状态漂移)")
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
                self._blind_s = 0.0
                for old, _r in self._pre_roll:
                    self._parts.append(old)
                    self._n += len(old)
                self._pre_roll.clear()
            else:
                # ⭐ 这一支是**真实丢内容**的入口: 判非语音的块既不进段、也不被计数,
                #    所以 VAD 漂移期间整段话会无声无息消失。两条补救在这里:
                #    ① 记 `blind_dropped`（块里有语音窗 = 确实有话被丢）；
                #    ② 连续非语音够久就重建 VAD（清漂移，见 `SileroVad.reset`）。
                #    ⚠️ **计数点必须在"被 pre-roll 挤出"时，不在入队时**
                #       （2026-10-07 独立复核指出 + 我复现）：入队那几块**还可能被
                #       pre-roll 救回**（实测 2 块被计了数，而 35200 个样本一个不少
                #       全在段里）→ 那样报出来的"整段没进转录"是**过度声明**。
                #       只有被挤出 `PRE_ROLL_CHUNKS` 的那一块才是真丢了。
                self._pre_roll.append(
                    (chunk, float(getattr(self._vad, "last_ratio", 0.0))))
                while len(self._pre_roll) > PRE_ROLL_CHUNKS:
                    _oc, _or = self._pre_roll.popleft()
                    if _or > 0.0:
                        self.diag["blind_dropped"] += 1
                self._blind_s += CHUNK_DUR
                if self._blind_s >= RESET_AFTER_SILENCE_S:
                    self._blind_s = 0.0
                    # ⚠️ 只对**会漂移的** VAD 重建（2026-10-07 OCR 指出）：兜底的
                    #    `_EnergyVad` 没有会漂移的内部状态、`reset()` 是空操作，
                    #    无条件计数会让报告声称"清掉了漂移"，而现场根本没有漂移。
                    if getattr(self._vad, "drift_prone", False):
                        self._vad.reset()
                        self.diag["vad_resets"] += 1
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
            # ⚠️ 这一行的**唯一效果**就是把它刚读的那个标志翻回 False ——
            #    而 `is_speaking` 全仓没有别的读者（见 `HANGOVER` 那里的说明）。
            #    所以它目前是**空转**：既不影响切句，也不被外部读。
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
            # ⚠️⚠️ **这个守卫恒为真，是安全网不是判据**（2026-09-29 核实）。
            #    `silence_cut` 的**定义里就含** `dur >= MIN_UTTERANCE_S`（见上面），
            #    而 `hard_cut` 是 `dur >= MAX_UTTERANCE_S(12s)`，
            #    且 `len(buf)/SR` 恒等于刚才那个 `self.dur` → 条件永远成立。
            #    ⚠️ **留着是刻意的**：它挡的是「将来有人改了 `silence_cut` 的定义、
            #       却忘了这里」—— 那时它会真的开始丢内容。
            #    ⚠️ 但**丢了不报**：本分支没有 diag 计数（`_reset` 那条路有 `dropped`）。
            #       真开始丢的时候看不出来 —— 要查就先看这里。
            if len(buf) / SR >= MIN_UTTERANCE_S:
                self.on_utterance_end(buf)
        elif sp and now - self.last_partial >= PARTIAL_INTERVAL_S:
            self.last_partial = now
            self.on_partial(self._buf())
