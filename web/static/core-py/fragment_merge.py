"""跨段半句合并（3.9.7 第 4 项）：段尾碎句**先照常上屏**（零额外等待），
下一段首句定稿回来后，用**同一个修正翻译调用**把「尾句 + 首句」合起来重翻，
**原子地**替换屏上尾句那一行、并吞掉首句那一行（一次 UI 操作改两行）。

## 只影响课上屏幕

`sessions/<名>.md` **一个字不改** —— 三方契约（`obsidian_writer` 写 / `_parse` 读回 /
`cl last` grep）。合并结果追加写旁路 `sessions/<名>.merges.jsonl`（照 `.lost.jsonl`
那套纪律）；**课后笔记**的合并早在 `note_merge.py` 里做完了（覆盖 ≤3 行 / 间隔 ≤20 s，
比这里更宽）—— 这正是「半句先上屏、不等」这条取舍成立的前提：等下一段的钱课后已经付过。

## 为什么（design.md §1–§4；实测 `docs/experiments/fragment_merge_20261014/`）

12 s 硬切 / 静音切会把一句话劈成两行、各自单译。同一批 120 对盲评（1–5 分）：
「两行并成一句再译」意思 4.46 / 通顺 4.34，对照逐行单译 4.12 / 3.44（81 对赢 71）。
而并句要等下一段定稿（中位 +9.5 s），**直播等不起** → 改成「半句先上屏、回来再替换」。

## 阈值与判据的测量（design.md §3，作者 6 节真课 218.2 min / 1311 段 / 2540 行）

`MERGE_GAP_S = 1.5 s` 时触发 **409 次（ASR 口径）/ 377 次（屏上口径）**，即 1.87 / 1.73
次每分、比现有逐句请求多 **16.1% / 14.8%**；规则对 283 条人工标签
（`prosody/labels.jsonl`）精度 **0.835** / 召回 **0.771**（TP 81 / FP 16 / FN 24 / TN 162）。
阈值曲线在 1.5 s 后就平（2.0 s 只多 23 次、3.0 s 多 41 次），再放宽只收进「真的换句」。

⚠️ 两条**已知局限**（写进代码，不是免责）：
  · ASR 口径依赖 ASR 自己加的标点 —— **千问转写几乎每句都补句号**，对它失效；
    所以还叠一条「屏上文本也不以 `. ! ?` 收尾」的兜底口径（两套口径**任一成立**即触发）。
  · 合并行跨两个段号；`DraftView` / `livemetrics` / `summ.feed` 都假设段号 / 句号
    单调推进 —— 所以替换必须走**独立事件类型**，且**不落 `.md`、不喂 `live_summary`、
    不改 `finals`**（改 `finals` 会让之后所有请求的前文窗口下标整体前移、缓存全废）。
"""
from __future__ import annotations

#: 两段音频间隔小于它才算「同一次硬切 / 静音切把一句话劈成两行」。
#: 测量见文件头（design.md §3）：1.5 s 触发 409 / 377 次、+16.1% / +14.8% 请求，
#: 精度 0.835 / 召回 0.771。
MERGE_GAP_S = 1.5

#: 屏上口径的句末标点（英文行）。
_SCREEN_PUNCT = (".", "!", "?")
#: ASR 口径的句末标点 —— 与 `testmode.note_segment` 的 `end_punct` **逐字同口径**
#: （那边把 `…` 也算句末；两者不同就会让离线复盘的触发数对不上）。
_ASR_PUNCT = (".", "!", "?", "…")


def _frag_kind(tail_en: str, tail_raw_end_punct: bool) -> str:
    """段尾是不是碎句、是**哪套口径**说了算：`""` = 完整句。

    返回 `"asr"` / `"screen"` / `"both"`。
    ⚠️ `tail_en` 为空串时**一律**不算碎句 —— 段尾没有已上屏的行，也就没有可替换的对象。
    """
    tail = (tail_en or "").strip()
    if not tail:
        return ""
    asr = tail_raw_end_punct is False            # ASR 原文没有句末标点
    screen = not tail.endswith(_SCREEN_PUNCT)    # 屏上那一行也不以 . ! ? 收尾
    if asr and screen:
        return "both"
    if asr:
        return "asr"
    if screen:
        return "screen"
    return ""


def should_merge(tail_en: str, tail_raw_end_punct: bool, next_first_en: str,
                 gap_s: float | None) -> bool:
    """段 i 的最后一行被切断了、且下一段紧跟着有首句 —— 该不该合起来重翻。纯函数。

    触发 = 段尾是碎句（两套口径任一成立，见 `_frag_kind`）**且** `gap_s < MERGE_GAP_S`
    **且**下一段确有首句（`next_first_en` 非空，否则无处可并）。
    `gap_s` 给 `None`（段号漂了 / 拿不到）一律不触发。
    """
    if not (next_first_en or "").strip():
        return False
    if gap_s is None or not (gap_s < MERGE_GAP_S):
        return False
    return bool(_frag_kind(tail_en, tail_raw_end_punct))


def trigger_kind(tail_en: str, tail_raw_end_punct: bool) -> str:
    """这次触发是哪套口径（写进旁路文件、供课后核）。只在 `should_merge` 为真时调。"""
    return _frag_kind(tail_en, tail_raw_end_punct)


def merge_text(tail_en: str, next_en: str) -> str:
    """送去重翻的文本 = 「尾句 + 首句」。"""
    return f"{(tail_en or '').strip()} {(next_en or '').strip()}".strip()


def translate_merge(job: dict, translate) -> tuple[str, str]:
    """把一条合并 job 重翻成 `(en, zh)`；**任何失败 / 空输入都返回 `("", "")`，绝不外抛**。

    `translate(text, context, on_zh, on_en)` 注入 `EngineRouter.merge_translate`（只走云端、不碰熔断）
    （判据用桩）。⚠️ 回调给 **no-op**：合并结果是一次**原子替换**，不流式（流式会把
    屏上实时行弄成半句、且它换的是**历史**里那一行）。
    ⚠️ 上下文用 `job["ctx"]` —— 那是**触发那一刻** `finals` 的快照，**不改 `finals`**。
    """
    text = merge_text(job.get("tail_en", ""), job.get("next_en", ""))
    if not text:
        return "", ""
    try:
        res = translate(text, list(job.get("ctx") or ()),
                        lambda _d: None, lambda _d: None)
    except Exception:                             # noqa: BLE001  失败只是"这半句没合起来"
        return "", ""
    return (getattr(res, "en_fixed", "") or ""), (getattr(res, "zh", "") or "")


class MergeTracker:
    """按段记账 + 触发判定。`drain()` 每定稿一行喂一次 `on_line(...)`，返回 job 或 `None`。

    只认**相邻**两段（`j == i + 1`）：跨过一整段就不像"被切开的半句"了。
    `gap_of(i, j) -> float | None` 由调用方注入（段 i→j 的音频间隔，口径见 design.md §3）。

    ⚠️ **行身份靠 `token`（`finalize` 铸的自增号），不靠行号**：从尾句上屏到重翻回来
    之间屏上可能又插了别的行（同段的后几句 / 答案接管换渲染源），行号会漂、身份不会。
    """

    def __init__(self, gap_of):
        self._gap_of = gap_of
        self._first: set[int] = set()          # 已经见过首行的段号
        self._tail: dict[int, dict] = {}       # seg -> {tok, en, zh, ts, end_punct}
        self._done: set[int] = set()           # 已经为这一段（作为 i）触发过

    def on_line(self, seg, token, en: str, zh: str, ts: str,
                end_punct: bool) -> dict | None:
        """喂一行定稿；本行恰是某段的**首行**且上一段尾句该合并时 → 返回一份 job。"""
        try:
            seg = int(seg)
        except Exception:                             # noqa: BLE001  形状不对 -> 不记账
            return None
        if token is None:
            return None
        first_time = seg not in self._first
        if first_time:
            self._first.add(seg)
        self._tail[seg] = {"tok": token, "en": en or "", "zh": zh or "",
                           "ts": ts or "", "end_punct": end_punct}
        if not first_time:
            return None
        i = seg - 1
        prev = self._tail.get(i)
        if prev is None or i in self._done:
            return None
        gap = self._gap_of(i, seg)
        if not should_merge(prev["en"], prev["end_punct"], en, gap):
            return None
        self._done.add(i)
        return {"i": i, "j": seg,
                "tail_tok": prev["tok"], "tail_en": prev["en"], "tail_zh": prev["zh"],
                "tail_ts": prev["ts"], "next_tok": token, "next_en": en or "",
                "next_zh": zh or "", "next_ts": ts or "",
                "gap_s": gap, "trigger": trigger_kind(prev["en"], prev["end_punct"])}


def asr_end_punct(asr_text: str) -> bool:
    """一段 ASR 原文的 `end_punct`（True = 以句末标点收尾）—— 与
    `testmode.note_segment` 的 `end_punct` 同口径。空文本按"有标点"算（没有可替换的行）。"""
    t = (asr_text or "").rstrip()
    if not t:
        return True
    return t.endswith(_ASR_PUNCT)
