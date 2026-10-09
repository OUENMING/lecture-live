"""草稿译文何时触发：纯函数，主循环和离线回放共用一份判据。

现状（policy "legacy"，生产在用）：距上次 ≥2.5s 且新增 ≥6 词。
候选（policy "clause"）：在 legacy 条件之上，要求"落在从句边界"才送——
  · 文本以 , . ; : ? ! 结尾（说话人刚停在一个短语边界），或
  · 这一次草稿相对上一次是纯追加（前面的词没被改写 → 这半句比较稳）；
  · 但不能饿死：新增 ≥12 词或距上次 ≥6s 就照送。
理由（来自 LiveCaptions-Translator / livecaption 的读码）：LLM 翻译半截从句最容易脑补，
等到从句边界或前缀稳定再送，译文更少翻来覆去。**这一条的收益没有用译文质量量过**
（离线只能数请求数和边界命中率，见 scripts/draft_policy_sim.py），所以默认仍是 legacy，
CLASSLIVE_DRAFT_TRIGGER=clause 才切换，要不要切由用户看过效果再定。
"""
from __future__ import annotations

MIN_GAP_S = 2.5
MIN_NEW_WORDS = 6
STARVE_WORDS = 12
STARVE_S = 6.0
_CLAUSE_END = tuple(",.;:?!")


def ends_at_clause(text: str) -> bool:
    return text.rstrip().endswith(_CLAUSE_END)


def _tokens(text: str) -> list[str]:
    """归一化到"词身份"：小写 + 去掉尾随的从句标点（与 `main.partial_worker` 原来那份逐字一致）。"""
    return [w.lower().strip(".,;:?!") for w in text.split()]


def append_only(prev: str, cur: str) -> bool:
    """这一次的假设是不是把上一次**原样接长**的（前面的词一个没改）。

    ⭐ **判据只在这里定义一次**（2026-10-09 收口）：原来是**主循环**里手搓一份
    （`[w.lower().strip(".,;:?!") ...]`）、`scripts/draft_policy_sim.py` 里又拿
    `stream_metrics.normalize` 写了**另一份**（那份还会把 `10%` 展开成 `ten percent`）
    —— 两份逻辑相似但不等价，于是**离线模拟预测的不是生产真跑的那条路**。
    现在两边都调这个函数；顺带主循环在 `legacy` 档（默认）不再白算它。
    """
    a, b = _tokens(prev), _tokens(cur)
    return b[:len(a)] == a


def should_translate(policy: str, *, now: float, last_tr: float, n_words: int,
                     last_len: int, text: str, prev_text: str = "") -> bool:
    """`prev_text` = 上一次的草稿原文（只给 `clause` 用；它自己派生 `append_only`）。"""
    gap_ok = now - last_tr >= MIN_GAP_S
    new = n_words - last_len
    if not (gap_ok and new >= MIN_NEW_WORDS):
        return False
    if policy == "legacy":
        return True
    if policy == "clause":
        if ends_at_clause(text) or append_only(prev_text, text):
            return True
        return new >= STARVE_WORDS or now - last_tr >= STARVE_S
    raise ValueError(f"未知策略: {policy}")
