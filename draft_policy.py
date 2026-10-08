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


def should_translate(policy: str, *, now: float, last_tr: float, n_words: int,
                     last_len: int, text: str, append_only: bool) -> bool:
    gap_ok = now - last_tr >= MIN_GAP_S
    new = n_words - last_len
    if not (gap_ok and new >= MIN_NEW_WORDS):
        return False
    if policy == "legacy":
        return True
    if policy == "clause":
        if ends_at_clause(text) or append_only:
            return True
        return new >= STARVE_WORDS or now - last_tr >= STARVE_S
    raise ValueError(f"未知策略: {policy}")
