"""自动语种闸：连着几句中文就切到「只转录」档（`zh`）。纯函数 + 小状态，无 I/O。

详情见 `docs/TASKS-auto-lang-20261116.md`（L-2）。两条判据分开放、**别合并**：

1. `no_llm(mode)` —— 「这一档要不要发模型请求」。`zh`（中文课只转录）与 `raw`（纯转录）
   **完全同形**：不翻译、不矫正英文、不查专名、不出云端笔记。全仓所有按 `mode == "raw"`
   判「别发请求」的地方一律改成这对助手（`main.py` 12 处 / `overlay.py` 5 处 /
   `note_worker.py` 4 处），漏一处就是多花钱。

2. ⚠️ **`no_llm` 不能拿去判「别上云识别」。** `raw` 会关掉 Soniox（`cloud_asr_off_reason`
   对 `mode == "raw"` 返回「纯转录档」），`zh` **必须照常上云** —— 关了 Soniox 就退回本地
   英文模型，中文全乱（任务单开头那条）。`main.cloud_asr_off_reason` 里那句
   `if mode == "raw"` 是**唯一**保持只认 `"raw"` 的判据，别顺手改。

3. `LangGate` —— 吃 Soniox 封口句的 `lang`（L-1 的 `Sealed.lang`），**连续 `THRESHOLD`
   句是 `zh`** 时回一次 `True`（= 现在切 `zh`）。切过就**不再判、也不切回**（不回弹是刻意的：
   英文课里插一句中文不该抖档，中文课里念一段英文引文也不该切回来）。
   用户手动换过档（`trans["ever_off"]`）之后不再自动切 —— 那是用户的决定，不该被机器覆盖。

纯状态机，调用方只读返回值；没有任何线程 / 文件 / 时间依赖（`tests/test_lang_gate.py`）。
"""
from __future__ import annotations

#: 不调模型的翻译档。加新档时**只在这里加一处**（其余判据都走 `no_llm`）。
NO_LLM_MODES = ("raw", "zh")

#: 连续几句 `zh` 才认定「这节是中文课」。3 句 ≈ 十几秒 —— 一句中文的口误 / 引文不够，
#: 真的要讲中文的课 3 句就到了（第一句照常送翻，见任务单「已知缺口」）。
THRESHOLD = 3


def no_llm(mode) -> bool:
    """这一档是不是「一个模型请求都不发」。`None` / 空串 / 未知值一律 **False**（= 照常发）。

    ⚠️ 判「别上云识别」不能用它 —— 见模块头第 2 条。
    """
    return str(mode or "") in NO_LLM_MODES


def is_zh_lang(lang) -> bool:
    """封口句的语种标签是不是中文（L-1 只产出 `"zh"` / `""`；大小写与空白都容忍）。"""
    return str(lang or "").strip().lower() == "zh"


class LangGate:
    """「连着几句中文 → 切一次 `zh`」，切过就不再判。

    用法（`main.final_worker` 的 `sealedq` 消费处）::

        gate_zh = lang_gate.LangGate()
        ...
        if gate_zh.observe(_s.lang, ever_off=bool(trans.get("ever_off"))):
            ...  # 只在次处为真一次

    · `observe` 是**唯一**入口，返回 `True` 至多一次（`fired` 之后恒 `False`）。
    · `ever_off=True` 传进来（或构造时给）→ 立刻停在「永不切」，**之后连计数都不做**。
    · 非 `zh` 的一句把连击清零（`zh/en/zh/zh` → 最长连击 2 → 不切）。
    """

    __slots__ = ("_n", "_ever_off", "_fired", "_threshold")

    def __init__(self, *, ever_off: bool = False, threshold: int = THRESHOLD) -> None:
        self._threshold = max(1, int(threshold))
        self._ever_off = bool(ever_off)
        self._n = 0                 # 当前连击长度
        self._fired = False

    def observe(self, lang, *, ever_off: bool = False) -> bool:
        """喂一句的语种，返回「现在切 `zh`」（至多一次）。"""
        if ever_off:
            self._ever_off = True
        if self._fired or self._ever_off:
            return False
        if is_zh_lang(lang):
            self._n += 1
        else:
            self._n = 0
        if self._n >= self._threshold:
            self._fired = True
            self._n = 0
            return True
        return False

    @property
    def fired(self) -> bool:
        """已经切过（之后 `observe` 恒 `False`）。"""
        return self._fired

    @property
    def streak(self) -> int:
        """当前连着几句 `zh`（给调试通道 / 测试看，不参与判据）。"""
        return self._n
