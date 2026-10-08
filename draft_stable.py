"""草稿显示层的"前缀冻结 + 灰尾"：连续两次草稿的最长公共前缀（LCP）以内的词不再改。

要解决的问题：草稿每秒用 Parakeet 重转写一次整段缓冲，前面已经显示的词偶尔被改掉
（量具 `scripts/stream_eval.py` 在 TTS 基线上量到：每 100 个已显示的词有 ~7–8 个在
"退后 2 个词的灰尾"之外被改写；真实课堂见 docs 或量具输出）。

做法（借 WhisperLiveKit 的 LocalAgreement 思路；它的代码是 Apache-2.0，这里是按思路重写）：
  · 比较**归一化**后的词（忽略大小写/标点）。上一次与这一次的 LCP 长度 l；
  · 冻结长度 c = max(已冻结, l − K)：LCP 末尾的 K 个词当"灰尾"，还不算稳；
  · 显示 = 冻结前缀（词不再变，标点/大小写仍跟随最新一次）+ 其余用最新假设；
  · 草稿窗口滑动（缓冲超过 10s，开头的词滚出窗口）时，检测并同步丢掉冻结区开头的词。
定稿一到，调用 `reset()`，定稿文本整句覆盖，不受冻结影响。

不碰翻译：送去翻译的仍是最新的原始假设，不是冻结后的文本。
纯文本、无状态依赖外部，毫秒级可测。
"""
from __future__ import annotations
import re

_NONWORD = re.compile(r"[^\w']+", re.UNICODE)


def _norm(tok: str) -> str:
    return _NONWORD.sub("", tok.lower().replace("’", "'"))


def _lcp(a: list[str], b: list[str]) -> int:
    k = 0
    for x, y in zip(a, b):
        if x != y:
            break
        k += 1
    return k


class DraftStabilizer:
    def __init__(self, holdback: int = 2):
        self.k = max(0, int(holdback))
        self.reset()

    def reset(self) -> None:
        self._shown: list[str] = []          # 当前显示的词（原样，含标点）
        self._fz: list[str] = []             # 冻结区的归一化词
        self._prev: list[str] = []           # 上一次原始假设（归一化）
        self.committed = 0                   # 冻结词数（UI 想把其余画成灰色就读它）

    def _slide(self, n_norm: list[str]) -> None:
        """缓冲窗口滑动：新假设的开头 3 个词出现在上一次假设的中间 → 前面的词已滚出窗口。"""
        probe = n_norm[:3]
        if len(probe) < 2 or not self._prev:
            return
        for o in range(1, len(self._prev) - len(probe) + 1):
            if self._prev[o:o + len(probe)] == probe:
                self._prev = self._prev[o:]
                self._shown = self._shown[o:]
                self._fz = self._fz[max(0, o):]
                self.committed = max(0, self.committed - o)
                self._fz = self._fz[:self.committed]
                return

    def update(self, text: str) -> str:
        toks = text.split()
        n_norm = [_norm(t) for t in toks]
        if not self._prev:
            self._shown, self._fz, self.committed = list(toks), [], 0
            self._prev = n_norm
            return " ".join(toks)
        l = _lcp(self._prev, n_norm)
        if l == 0:
            self._slide(n_norm)
            l = _lcp(self._prev, n_norm)
        # 新假设和冻结区大面积对不上（<一半词位置相同）→ 这是另一句话，不是"改写几个词"：重新开始。
        # 否则定稿还没到时下一句的草稿会被拖着上一句的冻结前缀（见 main.partial_worker 的 final_gen 时序）。
        if self.committed:
            same = sum(1 for i in range(min(self.committed, len(n_norm))) if self._fz[i] == n_norm[i])
            if same * 2 < self.committed:
                self.reset()
                self._shown, self._prev = list(toks), n_norm
                return " ".join(toks)
        c = max(self.committed, l - self.k)
        c = min(c, len(toks))
        # 冻结区：词身份沿用已冻结的；新冻结的词取最新假设
        fz = self._fz[:c]
        while len(fz) < c:
            fz.append(n_norm[len(fz)])
        shown: list[str] = []
        for i in range(c):
            shown.append(toks[i] if n_norm[i] == fz[i] else (self._shown[i] if i < len(self._shown) else toks[i]))
        shown.extend(toks[c:])
        self._shown, self._fz, self.committed = shown, fz, c
        self._prev = n_norm
        return " ".join(shown)

    def __call__(self, _prev_shown: str, text: str) -> str:
        """供 stream_eval 的 draft_filter(prev_shown, new) 接口用；上一次显示的文本由本对象自己记着。"""
        return self.update(text)
