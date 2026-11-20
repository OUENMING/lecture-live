"""ASR 的**共用件** —— 找文件、退化检测。

⚠️ **2026-11-20（桌面 D1）**：Parakeet / Whisper 已从代码里删掉，本地识别只剩流式模型
Nemotron 一个：

- 段尾转录（云端失败时的定稿兜底）→ `draft_engine.SegmentTail`（D1b 起不再整段重解码）
- 实时草稿 → `draft_engine.NemotronDrafter`

所以本文件**不再有**任何识别器类。留下的两件东西仍然被两边共用，所以还叫 asr.py：
`_find_file`（模型目录里按精确名取文件）与 `is_degenerate`（复读 / 幻觉判定）。

⚠️ `is_degenerate` 还留在 `final_asr` 的判据里：流式模型**也会**在小概率上吐复读
（"… … …" / 同一句连刷），而那个判据是「宁可漏杀不可错杀」量出来的 —— 删掉它等于
把一条一直有效的防线一起拆了。
"""
from __future__ import annotations

import glob
import os
import re


def _find_file(dirname: str, patterns) -> str:
    """按 patterns 的**优先级**取第一个命中的文件。

    ⚠️ 两个坑:
    1) glob 的返回顺序取决于文件系统目录迭代顺序(OS 相关且不确定), 不排序则
       同一目录在不同机器/不同次运行可能选到不同文件;
    2) 通配("*.txt")命中多个时**不许猜** —— 猜错会静默用错词表(README.txt 也是
       .txt), 或 int8 与非 int8 混用, 而识别结果是"看起来正常但全错"。报出来
       让调用方决定。精确名(无通配)最多命中一个, 不受此限。
    ⚠️ 所以**一律只传精确名**给这个函数（`draft_engine` 就是这么调的）。
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


#: 模型目录型识别模型「必须在」的文件（`models.REQUIRED_FILES` 的那一项的注释里解释了为什么
#: 只断言存在 + 非空、不算 sha）。这里不重复定义 —— 这里只是名字的**用法**示例。
MODEL_FILES = ("tokens.txt", "encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx")


# ---- 退化（复读 / 幻觉）判定 ----
# ⚠️ 两处阈值都是"宁可漏杀不可错杀"量出来的:
#   词级要求**同词相邻 4 次以上** —— 3 次会误杀 "the the the"(转录中很常见);
#   句级要求**同一整句连刷 3 次** —— 2 次会误杀 "Okay. Okay. So..."(讲师真实口语)。
_DEGEN_TOKEN = re.compile(r"(\b[\w']+)(?:\s+\1){3,}", re.IGNORECASE)
_SENT_SPLIT = re.compile(r"[^.!?]+")


def _phrase_repeat(text: str, k: int = 3) -> bool:
    """同一句连刷 k 次?── 识别模型典型的退化, 如 "Thank you. Thank you. Thank you."。

    按句末标点切句后看有没有 k 个**完全相同**的相邻句。比正则回溯稳, 也不会
    被 "Okay. Okay. So now..." 这种真实口语误伤。"""
    sents = [s.strip().lower() for s in _SENT_SPLIT.findall(text) if len(s.strip()) >= 4]
    for i in range(len(sents) - k + 1):
        if len(set(sents[i:i + k])) == 1:
            return True
    return False


def is_degenerate(text: str) -> bool:
    """识别模型的幻觉/复读输出。判据刻意保守: 只抓明显的退化, 不误杀正常课堂句。"""
    if not text:
        return False
    if text.count("...") >= 2:
        return True
    if _DEGEN_TOKEN.search(text) or _phrase_repeat(text):
        return True
    parts = text.split()
    return len(parts) > 20 and len(set(parts)) <= 2
