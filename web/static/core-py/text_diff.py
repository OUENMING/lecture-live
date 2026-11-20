"""草稿被**改字**时, 只让变化的那一段做过渡 —— token 对齐 + 换行判据。**纯函数**。

为什么要它(证据, 报告 §3.4/§5 方案 C1): Liu 等, CHI EA 2023
「Modeling and Improving Text Stability in Live Captions」, 用户研究 **N=123**:
已显示的文字被改(flicker)**与疲劳/分心正相关**、与可读性负相关; 他们的算法三步
逐字是「**tokenized alignment**, semantic merging, and **smooth animation**」,
而且改字**只在"不破坏既有换行布局"时才做**。本模块就是那第一步(对齐)与
第三步要的输入(哪些 run 变了 + 换行有没有动)。

⚠️ 报告 §3.4 把这条挂在「草稿→定稿」上 —— **那不是本仓库的形态**: 定稿进历史上滚、
草稿槽清空, 不是原地替换。真正"改已显示文字"的两处是同一次**草稿**的逐词修正
(`overlay._render_draft`)与跨段半句合并(`transcript_view` 的 merge 过渡)。

分词**不自己写**: 英文按词(连尾部空白, 保证拼回去逐字等于原文), 中文走
`linebreak.phrases` —— BudouX, 与**折行**同一把尺(`linebreak.py` 的文件头写了为什么
用 BudouX、以及为什么不做成 pip 依赖)。

⚠️ 诚实前提(判据盖不住的一处): 若变化段是**插入/删除**而不是替换, 它后面的
未变化词会横向挪位(整串仍逐字是真值)。`same_breaks` 只能保证它们**不换行搬家**
(行号不变); 同一行内的横向位移量不出来(要 AppKit 量宽度), 所以没有拦。
真正的退出条件只用换行: 换行一动就整串直接换(见 `same_breaks`)。
"""

from __future__ import annotations

import re

import linebreak

#: 英文按词切: 词 + 尾随空白。**保形** —— `"".join()` 回去逐字等于原文。
#: `\s+` 那条分支管行首/连续空白, 少了它 `" a"` 会丢掉那个空格(判据钉着)。
_EN_TOK = re.compile(r"[^\s]+\s*|\s+")


def tokens(text: str, *, cjk: bool = False) -> list[str]:
    """保形分词: `"".join(tokens(t, cjk=…)) == t` **恒成立**。空串 -> `[]`。

    `\\n` 单独作为一个 token 发出去 —— 它是**换行**, 不是词的一部分:
    渲染层要按 run 拼属性串, 把换行夹在词里只会让"这一 run 在第几行"说不清。
    """
    if not text:
        return []
    out: list[str] = []
    for i, seg in enumerate(text.split("\n")):
        if i:
            out.append("\n")
        if not seg:
            continue
        if cjk:
            out.extend(linebreak.phrases(seg))       # BudouX: 逐字保留, 拼回 = seg
        else:
            out.extend(_EN_TOK.findall(seg))
    return out


def diff_runs(old: str, new: str, *, cjk: bool = False) -> list[tuple[str, bool]]:
    """旧串 -> 新串: 新串按 token 对齐后的 `[(文本, 是否变了)]`。

    规则(**只有前缀/后缀裁剪**, 保守但不会判错):
      · `old == new` / `new` 是 `old` 的**逐字前缀延展**(只是末尾追加)
        → 只有一段, `changed=False`(调用方据此**不做任何动画**, 见方案 §5 C1);
      · 否则: 去掉**公共的 token 前缀/后缀**, 中间那一段就是"变了的那一段"
        (`changed=True`), 前后两段原样不动。改动不连续时会**多算**几个词进变化段
        (宁可在不该动的地方慢慢淡, 也不漏掉该动的)。

    三条不变量(判据钉着):
      1. `"".join(t for t, _ in diff_runs(o, n)) == n` —— 拼回去逐字等于新串;
      2. 变化段**至多一段**, 且空串输入不会凭空造出变化段;
      3. 未变化的 token **一个都不许进变化段**(除非它被夹在两处改动中间)。
    """
    if not new:
        return []
    if old == new or new.startswith(old):
        return [(new, False)]
    to, tn = tokens(old, cjk=cjk), tokens(new, cjk=cjk)
    p = 0
    while p < len(to) and p < len(tn) and to[p] == tn[p]:
        p += 1
    s = 0
    while (s < len(to) - p and s < len(tn) - p
           and to[len(to) - 1 - s] == tn[len(tn) - 1 - s]):
        s += 1
    n_head = sum(len(t) for t in tn[:p])
    n_tail = sum(len(t) for t in tn[len(tn) - s:]) if s else 0
    runs: list[tuple[str, bool]] = []
    head, mid = new[:n_head], new[n_head:len(new) - n_tail]
    tail = new[len(new) - n_tail:] if n_tail else ""
    if head:
        runs.append((head, False))
    if mid:
        runs.append((mid, True))
    elif head and tail:                    # 只差在 token 边界上(理论上到不了, 兜底别吞字)
        runs[-1] = (head + tail, False)
        tail = ""
    if tail:
        runs.append((tail, False))
    if not any(ch for _, ch in runs):      # 找不到差异(分词边界抖了一下) -> 老实直接换
        return [(new, False)]
    return runs


def same_breaks(old: str, new: str, runs) -> bool:
    """`runs` 里**未变化**的那些段, 在旧串与新串里是不是落在**同一行**。

    ⚠️ 只在为真时做逐段淡入: 换行位置一动, 未变化的词就会整块搬到别的行上 ——
    那正是 CHI 2023 点名的另一种分心(layout change), 那种情况不如老老实实直接换
    (报告 §5 C1 的退出条件)。
    """
    if old.count("\n") != new.count("\n"):
        return False
    unch = sum(len(t) for t, ch in runs if not ch)
    old_span = max(0, len(old) - unch)     # 变化段在旧串里占多长(前缀/后缀是共享的)
    o = n = 0
    for text, ch in runs:
        if ch:
            n += len(text)
            o += old_span
            continue
        if old.count("\n", 0, o) != new.count("\n", 0, n):
            return False
        o += len(text)
        n += len(text)
    return True
