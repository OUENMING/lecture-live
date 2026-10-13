"""中文定稿的折行: 只折在**词组边界**上, 定稿不留一两个字的孤尾行。纯函数, 宽度由调用方注入。

为什么要自己折(2026-10-13 实测, 3 节真课 1459 句定稿, 18pt 中文, 宽 628pt):
AppKit 对中文是**逐字**折行 —— 折成两行的 143 句里**约一半折在一个词中间**(jieba 判 71 处)
(「周围的事 / 情」「她们不发 / 言」「设 / 施」), **33 句(23%)尾行只剩 ≤2 个字**
(「…第一步在这里已经完成 / 了。」)。窄窗(408pt)同样是 40% / 18%。

效果(同一批 1459 句; ⚠️ 「词中间」要用**独立**分词器判 —— 拿 BudouX 自己的边界去判
BudouX 的折法恒等于 0, 是循环论证。下面用 jieba 0.42.1 判, jieba 只在评估时用、不随应用发):
    628pt  折在词中间 71 → 19 处   孤尾 33 → 0
    408pt            219 → 44 处        80 → 0
剩下的是 BudouX 模型自己分错的(「都 / 柏林」「额 / 外捕 / 获量」), 不是折行逻辑的问题。

规则来源(都回源核过):
  · Netflix Timed Text Style Guide「Line Treatment」: 折在标点后、连词 / 介词前;
    不拆冠词与名词、形容词与名词、主语代词与动词 —— 即**不拆语言单位**。
  · Gerber-Morón & Szarkowska 2018(JEMR, eye tracking, PMC7733619): 观众明显偏好
    按句法单位折行的字幕(最强一组 86% vs 14%), 而且**看句法多于看形状**。
    → 所以本模块的第一目标是「不拆词组」, 形状(孤尾)是第二目标; **不追求两行等长**
      (Netflix 原文是「宁可下宽上窄的金字塔, 但上行别只剩一两个词」, 不是等宽)。
  · 词组边界用 **BudouX**(Google, Apache-2.0)的简体中文模型: `vendor/budoux/zh-hans.json`
    + 下面 `phrases()` 里那 30 行打分循环(逐行照搬 budoux 0.9.3 `parser.py`, 许可证见
    `vendor/budoux/LICENSE`)。不 pip 装: 装了就改 `requirements.txt`, 而依赖清单一变,
    朋友那台的后台自动更新就停了(见 `update.py` 的 quiet 判据)。

两种用法:
  · `final=False`(流式中的实时行): **贪心**按词组装满每行。前缀只会长, 断点只在
    装不下时往后走 —— 与英文按词折行同一种「词跳到下一行」, 不会来回跳。
  · `final=True`(定稿): **只在贪心折法留下孤尾时**才重折(动态规划, 行数不变,
    尾行至少 4 个汉字宽、偏好折在标点后); 没有孤尾就与流式一字不差。
    ⚠️ 为什么不「每句都全局最优」: 实测(默认宽度 143 句两行定稿)若每句都按全局代价
    重折, 有 **122 句(85%)在定稿那一刻换断点** —— 流式行刚读到一半, 字又挪一次,
    这正是 CHI 2023 那篇说的「布局变化让人分心」。只修孤尾时挪 43 句(30%, 都是尾行不足 4 字的);
    408pt 宽 102/438、320pt 宽 144/615。三档宽度下定稿孤尾(≤2 字)都是 0, 词中折断不变。

兜底: 词组比一整行还宽 → 拆成单字; 按词组折比 AppKit 逐字折**多一行** → 返回原文
(让 AppKit 自己折)—— 行高是固定档位, 多出来的一行会被静默裁掉。
"""

from __future__ import annotations

import json
import os

_MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor", "budoux", "zh-hans.json")
_model = None
_PUNCT_END = "，。；：！？、,.;:!?）)」』”"
ORPHAN_CHARS = 4            # 定稿尾行窄于这么多个汉字宽 = 孤尾(按字宽, 与面板宽窄无关)
PUNCT_BONUS = 0.08          # 折在标点后的奖励(与 (余白/宽)² 同单位)
_CACHE_MAX = 512


def _load():
    global _model
    if _model is None:
        try:
            with open(_MODEL_PATH, encoding="utf-8") as f:
                _model = json.load(f)
        except Exception:                                   # noqa: BLE001
            _model = {}                                     # 模型缺了 = 不分词, 退回逐字(行为同旧版)
    return _model


def phrases(sentence: str) -> list[str]:
    """BudouX 的打分循环(budoux 0.9.3 `Parser.parse`, Apache-2.0)。模型缺失时退化为逐字。"""
    if not sentence:
        return []
    m = _load()
    if not m:
        return list(sentence)
    g = m.get
    uw1, uw2, uw3, uw4, uw5, uw6 = (g(k, {}) for k in ("UW1", "UW2", "UW3", "UW4", "UW5", "UW6"))
    bw1, bw2, bw3 = (g(k, {}) for k in ("BW1", "BW2", "BW3"))
    tw1, tw2, tw3, tw4 = (g(k, {}) for k in ("TW1", "TW2", "TW3", "TW4"))
    base = -sum(sum(v.values()) for v in m.values()) * 0.5
    s = sentence
    chunks = [s[0]]
    for i in range(1, len(s)):
        score = base
        if i > 2:
            score += uw1.get(s[i - 3], 0)
        if i > 1:
            score += uw2.get(s[i - 2], 0)
        score += uw3.get(s[i - 1], 0)
        score += uw4.get(s[i], 0)
        if i + 1 < len(s):
            score += uw5.get(s[i + 1], 0)
        if i + 2 < len(s):
            score += uw6.get(s[i + 2], 0)
        if i > 1:
            score += bw1.get(s[i - 2:i], 0)
        score += bw2.get(s[i - 1:i + 1], 0)
        if i + 1 < len(s):
            score += bw3.get(s[i:i + 2], 0)
        if i > 2:
            score += tw1.get(s[i - 3:i], 0)
        if i > 1:
            score += tw2.get(s[i - 2:i + 1], 0)
        if i + 1 < len(s):
            score += tw3.get(s[i - 1:i + 2], 0)
        if i + 2 < len(s):
            score += tw4.get(s[i:i + 3], 0)
        if score > 0:
            chunks.append(s[i])
        else:
            chunks[-1] += s[i]
    return chunks


def _tokens(text: str, width_of, max_w: float):
    """词组 + 宽度; 比一行还宽的词组拆成单字(不然它永远放不下)。"""
    out = []
    for p in phrases(text):
        w = width_of(p)
        if w > max_w:
            out.extend((c, width_of(c)) for c in p)
        else:
            out.append((p, w))
    return out


# ⚠️ 行宽必须**整段实测**, 不能把各词组的宽度加起来: 实测加和会少算 ~20px
#    (词组末尾的「，」单独量时被压成半宽, 排在行中间却是全宽)—— 加和版在
#    1459 句里有 86 句以为放得下、AppKit 实排却多出一行。所以宽度按 (起, 止) 现量、记忆化。
def _span_width(tok, width_of, memo, a, i):
    key = (a, i)
    w = memo.get(key)
    if w is None:
        w = memo[key] = width_of("".join(t for t, _ in tok[a:i]))
    return w


def _greedy(tok, max_w, width_of, memo):
    lines, a = [], 0
    for i in range(1, len(tok) + 1):
        if i - 1 > a and _span_width(tok, width_of, memo, a, i) > max_w:
            lines.append([t for t, _ in tok[a:i - 1]]); a = i - 1
    lines.append([t for t, _ in tok[a:]])
    return lines


def _dp(tok, max_w, n_lines, width_of, memo, orphan_w):
    """恰好 n_lines 行、每行放得下, 代价最小的折法; 没有就 None。"""
    k = len(tok)
    INF = float("inf")
    # best[j][i] = 前 i 个词组折成 j 行的最小代价
    best = [[INF] * (k + 1) for _ in range(n_lines + 1)]
    back = [[-1] * (k + 1) for _ in range(n_lines + 1)]
    best[0][0] = 0.0
    for j in range(1, n_lines + 1):
        for i in range(1, k + 1):
            last = j == n_lines
            if last and i != k:
                continue
            for a in range(i - 1, -1, -1):
                w = _span_width(tok, width_of, memo, a, i)
                if w > max_w:
                    break
                if best[j - 1][a] == INF:
                    continue
                if last:
                    c = 1.0 if w < orphan_w else 0.0
                else:
                    c = ((max_w - w) / max_w) ** 2
                    if tok[i - 1][0][-1:] in _PUNCT_END:
                        c -= PUNCT_BONUS
                if best[j - 1][a] + c < best[j][i]:
                    best[j][i] = best[j - 1][a] + c
                    back[j][i] = a
    if best[n_lines][k] == INF:
        return None
    lines, i = [], k
    for j in range(n_lines, 0, -1):
        a = back[j][i]
        lines.append([t for t, _ in tok[a:i]])
        i = a
    return lines[::-1]


_cache: dict = {}


def wrap(text: str, width_of, max_w: float, *, natural_lines: int, final: bool) -> str:
    """返回插好 `\\n` 的文本; 不该动时原样返回。

    `width_of(s)`: 单行排版宽度(px), 生产环境注入 AppKit 实测;
    `natural_lines`: AppKit 逐字折这段要几行 —— 我们的折法**不许比它多一行**。
    """
    if not text or "\n" in text or natural_lines <= 1 or max_w <= 0:
        return text
    key = (text, round(max_w, 1), natural_lines, final)
    hit = _cache.get(key)
    if hit is not None:
        return hit
    tok = _tokens(text, width_of, max_w)
    memo: dict = {}
    lines = _greedy(tok, max_w, width_of, memo)
    if len(lines) > natural_lines:
        out = text                                       # 按词组折会多一行 -> 交还 AppKit
    else:
        if final and len(lines) > 1:
            orphan_w = width_of("中" * ORPHAN_CHARS)
            if width_of("".join(lines[-1])) < orphan_w:      # 只有孤尾才重折, 见文件头
                lines = _dp(tok, max_w, len(lines), width_of, memo, orphan_w) or lines
        out = "\n".join("".join(l) for l in lines)
    if len(_cache) > _CACHE_MAX:
        _cache.clear()
    _cache[key] = out
    return out
