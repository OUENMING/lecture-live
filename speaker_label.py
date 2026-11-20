"""说话人字母状态机：主讲不标，其他人发 B/C/D…（纯逻辑，不接 AppKit、不碰 I/O）。

对外接口：`count_units(text) -> int`、`long_enough(text) -> bool`、`SpeakerLabeler.observe(speaker, text) -> str`。
规则与数据见 `docs/RESEARCH-speaker-multilang-20261116.md` §11（Owen 2026-11-18 定）。
为什么不接在流水线上：字母一旦发出永不回收，判据必须能在离线真数据上重放（`scripts/replay_speaker_label.py`），
所以状态机只吃「(speaker, 一句文本)」这一对，不依赖仓库任何别的模块。
"""
from __future__ import annotations

import re

#: 够长门槛：英文词数 / 汉字个数，两条满足其一。
MIN_WORDS = 3
MIN_HAN = 4

#: 发字母的顺序。主讲不占字母（第一个「别人」拿 B），Z 之后不再发新字母。
LETTERS = "BCDEFGHIJKLMNOPQRSTUVWXYZ"

#: 汉字区间：㐀-䶿（扩展 A）+ 一-鿿（基本区）。只看这两段，别的文字一律按空白切词。
_HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


def count_units(text: str) -> int:
    """一句话的长度单位：汉字个数 + 非汉字部分按空白切出的词数。

    为什么不是 `len(text.split())`：中文里没有空格，一句 12 个汉字会被算成 1 个词，
    主讲判定就退化成「谁先说了长句」。
    """
    if not isinstance(text, str) or not text:
        return 0
    han = _HAN.findall(text)
    if han:
        return len(han) + len(_HAN.sub(" ", text).split())
    return len(text.split())


def long_enough(text: str) -> bool:
    """这句够不够「定稿的实义句」——只数长度，不看内容。"""
    if not isinstance(text, str) or not text:
        return False
    if len(_HAN.findall(text)) >= MIN_HAN:
        return True
    return len(text.split()) >= MIN_WORDS


class SpeakerLabeler:
    """逐句喂进去，拿回这句该显示的字母（`""` = 不标）。

    不抛异常：坏输入（`None`、非字符串、空 speaker）一律当空串。
    """

    def __init__(self) -> None:
        self._units: dict[str, int] = {}
        self._labels: dict[str, str] = {}
        self._main = ""

    # ---- 只读视图 -------------------------------------------------------
    @property
    def main(self) -> str:
        """当前主讲；还没有人说过话时是 `""`。"""
        return self._main

    @property
    def labels(self) -> dict[str, str]:
        """已发出的字母（副本，改它不影响状态机）。"""
        return dict(self._labels)

    def units(self, speaker: str) -> int:
        """某个说话人累计的长度单位（0 = 没出现过）。"""
        return self._units.get(speaker, 0)

    # ---- 主入口 ---------------------------------------------------------
    def observe(self, speaker: str, text: str) -> str:
        """吃一句，返回 `""` 或字母。顺序：先计数，再定主讲，最后判字母。"""
        sp = speaker if isinstance(speaker, str) else ""
        body = text if isinstance(text, str) else ""
        if not sp:
            # 没开说话人分离：不计数、不发字母，也不干扰主讲。
            return ""

        self._units[sp] = self._units.get(sp, 0) + count_units(body)
        self._update_main(sp)

        if sp == self._main:
            # 主讲不标 —— 哪怕他以前当过「别人」拿过字母。
            return ""
        if sp not in self._labels and long_enough(body):
            idx = len(self._labels)
            if idx < len(LETTERS):
                self._labels[sp] = LETTERS[idx]
        return self._labels.get(sp, "")

    def _update_main(self, sp: str) -> None:
        """第一次出现的人自动成为主讲；之后只有**严格超过**才易主（并列保持旧主讲）。"""
        if not self._main:
            self._main = sp
            return
        if self._units[sp] > self._units[self._main]:
            self._main = sp
