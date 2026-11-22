"""数字核对的「转录对照」一步（2026-11-21 第三轮）：给**未核对**的课堂断言找转录原话。

为什么有这一步：
  写作块自己出的 `checks` 和第三道核（`note_v2.verify_numbers`）都**看不到转录** ——
  实测 SOC10020 新版 11 条「待你确认」里，`136 clubs` / `five to seven years` /
  `40 to 50 people` 原话都在转录里。外部证据（MiniCheck / LLM-AggreFact、Abridge
  FEVER 2024.24）一致：核「这句话有没有源文支持」要把**源文证据片段**交给核对器，
  长转录里难的是找片段 —— 所以找片段由代码做（确定性），判支持由模型做（一次调用）。

口径（硬边界）：
  · 只处理 `status == "unchecked"`、出处是**讲解**（`prose` / 写作块自己的 `checks`）的行；
    例题 / 变式 / 补全题 / 自测是模型自己出的题，转录对上也不算核（同 `cap_model_rows`）。
  · 只许 `unchecked → ok`（原话支持）；原话说法不同 → **仍是 unchecked**，`how` 里写原话。
    **永不产出 mismatch**：转录本身可能听错（ASR），没有改正文的权力。
  · 失败一律原样返回（fail-soft），绝不抛。

开关：`CLASSLIVE_NOTE_GROUND`（默认 `0`；`1` 开）—— 登记在 `docs/HANDBOOK.md` §6.1。
"""
from __future__ import annotations

import re

#: 一次最多对照几条（一条约 2 段原话 × 200 字，24 条 ≈ 1 万字输入）。
MAX_ROWS = 24
#: 每条最多附几段原话。
MAX_HITS = 2
#: 每段原话截多长。
HIT_CHARS = 200

_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
         "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
         "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS = {2: "twenty", 3: "thirty", 4: "forty", 5: "fifty", 6: "sixty",
         7: "seventy", 8: "eighty", 9: "ninety"}
_CN_DIGIT = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
             "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNIT = {"十": 10, "百": 100, "千": 1000}
_STOP = {"the", "and", "for", "that", "this", "with", "are", "was", "you", "not",
         "have", "has", "but", "its", "can", "one", "two"}

GROUND_SYS = """你在核对课堂笔记里的断言**有没有课上原话支持**。
每条断言后面附了从课堂转录里找到的 1–2 段原话（英文，带时间戳；转录是语音识别出来的，可能有听错的词）。

对每一条判 verdict：
- "supported"：原话**直接说了**断言里的这个数字/事实（意思一致即可，不要求逐字；中英文对应算一致）；
- "different"：原话讲的是同一件事，但数字或关系**和断言不一样**；
- "not_found"：附的原话没讲这件事，或看不出来。
不许用你自己的知识补判 —— 只看附的原话。拿不准就判 "not_found"。
quote 填支持/不一致所依据的那句原话（英文原文照抄，≤120 字符），not_found 时留空。
输出严格 JSON：{"items": [{"i": 序号, "verdict": "supported|different|not_found", "ts": "原话的时间戳", "quote": ""}]}
只输出 JSON。"""


def _cn_to_int(s: str) -> int | None:
    """「五」「四十」「一百三十六」「两千」→ 整数；认不出 → None。**纯函数。**"""
    if not s or any(ch not in _CN_DIGIT and ch not in _CN_UNIT for ch in s):
        return None
    total, cur = 0, None
    for ch in s:
        if ch in _CN_DIGIT:
            cur = _CN_DIGIT[ch]
        else:
            total += (cur if cur is not None else 1) * _CN_UNIT[ch]
            cur = None
    return total + (cur or 0)


def numbers_in(text: str) -> set:
    """断言里的数（阿拉伯数字去千分位 + 中文数字）→ 字符串集合。**纯函数。**"""
    t = str(text or "")
    out = {m.replace(",", "") for m in re.findall(r"\d[\d,]*(?:\.\d+)?", t)}
    for m in re.findall(r"[零〇一二两三四五六七八九十百千]+", t):
        v = _cn_to_int(m)
        if v is not None and not (v in (1, 2) and len(m) == 1):   # 「一」「二」太常见，不当数
            out.add(str(v))
    return {x.rstrip(".") for x in out if x}


def _en_words(n: int) -> list:
    if n < 20:
        return [_ONES[n]]
    if n < 100:
        t, o = divmod(n, 10)
        return [_TENS[t]] if o == 0 else [f"{_TENS[t]}-{_ONES[o]}", f"{_TENS[t]} {_ONES[o]}"]
    if n < 1000 and n % 100 == 0:
        return [f"{_ONES[n // 100]} hundred"]
    return []


def _num_patterns(nums: set) -> list:
    """数 → 在英文转录里找它的正则（数字本身，或 ≤99 / 整百的英文拼写）。"""
    pats = []
    for x in nums:
        alts = [re.escape(x)]
        if x.isdigit():
            n = int(x)
            if n >= 1000:
                alts.append(f"{n:,}".replace(",", r",?"))
            alts += [re.escape(w) for w in _en_words(n)]
        pats.append(re.compile(r"(?<![\d.])(?:" + "|".join(alts) + r")(?![\d])", re.I))
    return pats


def _body(ln) -> str:
    return re.sub(r"^\s*\[[^\]]*\]\s*", "", str(ln))       # 时间戳里的数字不算（13:14 ≠ 13）


def find_evidence(claim: str, lines: list, *, k: int = MAX_HITS) -> list:
    """断言 → 转录里最可能讲它的 ≤k 处原话（命中行 + 前后各一行，带命中行的时间戳）。**纯函数。**

    打分：断言里的数每对上一个 +3，断言里的英文词（≥3 字母）每出现一个 +1。
    候选：有数字的断言 → 至少对上一个数；没数字的 → 至少对上一个英文词（`Stata`、`Bert`）。
    纯中文、没数字的断言找不到原话（中英跨语言，词面对不上）→ 返回空，交回读者确认。
    ⚠️ 带前后行：转录按停顿切句，「500 people … / … five to seven years」常被切成两行，
       只给命中那一行，核对器看不出两件事是一句话（2026-11-21 实测）。
    """
    nums = numbers_in(claim)
    words = {w.lower() for w in re.findall(r"[A-Za-z]{3,}", str(claim))} - _STOP
    if not nums and not words:
        return []
    pats = _num_patterns(nums)
    bodies = [_body(ln) for ln in (lines or [])]
    scored = []
    for idx, body in enumerate(bodies):
        hit = sum(1 for p in pats if p.search(body))
        low = body.lower()
        wh = sum(1 for w in words if re.search(r"\b" + re.escape(w) + r"\b", low))
        if (nums and not hit) or (not nums and not wh):
            continue
        scored.append((-(3 * hit + wh), idx))
    scored.sort()
    out, used = [], set()
    for _, idx in scored:
        if len(out) >= k or idx in used:
            continue
        used.update({idx - 1, idx, idx + 1})
        ts = re.match(r"^\s*(\[[^\]]*\])", str(lines[idx]))
        ctx = " ".join(b for b in bodies[max(0, idx - 1):idx + 2] if b)
        if len(ctx) > HIT_CHARS * 2:
            ctx = ctx[:HIT_CHARS * 2] + "…"
        out.append(f"{ts.group(1) + ' ' if ts else ''}{ctx}")
    return out


def ground_unchecked(checks: list, review: dict, lines: list, *, chat,
                     max_tokens: int = 3000) -> tuple:
    """未核对的讲解类断言 → 找原话 → 一次调用判支持。返回 `(checks, n_ok, n_diff, n_sent)`。

    `n_sent` = 找到原话、送去判的条数（0 = 没有可对照的；>0 而 n_ok=0 = 送了但没一条被支持
    或调用失败 —— 终端那行要分得清这两种）。

    `checks` 不被原地修改（返回新列表）。任何异常 → 原样返回、计数 0。
    """
    import note_v2
    rows = [dict(c) if isinstance(c, dict) else c for c in (checks or [])]
    todo = []                                   # (行号, 原话列表)
    for j, c in enumerate(rows):
        if not isinstance(c, dict) or c.get("status") != "unchecked":
            continue
        if note_v2._claim_origin(review, str(c.get("claim") or "")) not in ("prose", "checks"):
            continue
        hits = find_evidence(str(c.get("claim") or ""), lines)
        if hits:
            todo.append((j, hits))
        if len(todo) >= MAX_ROWS:
            break
    if not todo:
        return rows, 0, 0, 0
    user = "\n\n".join(
        f"[{i}] 断言：{rows[j]['claim']}\n原话：\n" + "\n".join(f"  {h}" for h in hits)
        for i, (j, hits) in enumerate(todo))
    try:
        obj = chat(GROUND_SYS, user, max_tokens)
        items = obj.get("items") if isinstance(obj, dict) else None
        if not isinstance(items, list):
            return rows, 0, 0, len(todo)
    except Exception:                                         # noqa: BLE001
        return rows, 0, 0, len(todo)
    n_ok = n_diff = 0
    for it in items:
        if not isinstance(it, dict) or not isinstance(it.get("i"), int):
            continue
        if not (0 <= it["i"] < len(todo)):
            continue
        j, hits = todo[it["i"]]
        v = str(it.get("verdict") or "").strip().lower()
        quote = str(it.get("quote") or "").strip()[:120]
        ts = str(it.get("ts") or "").strip()[:12]
        # ⚠️ 引文必须真在附的原话里（模型可能编）：对不上就当没判。
        if quote and not any(quote.lower()[:40] in h.lower() for h in hits):
            continue
        cite = f"🎙 {ts} 原话「{quote}」" if quote else "🎙 转录原话"
        if v == "supported" and quote:
            rows[j]["status"] = "ok"
            rows[j]["how"] = f"{cite}（对照转录）"
            rows[j]["grounded"] = "supported"
            n_ok += 1
        elif v == "different" and quote:
            rows[j]["how"] = f"{cite} 与笔记说法不同 —— 正文不改，待你确认"
            rows[j]["grounded"] = "different"
            n_diff += 1
    return rows, n_ok, n_diff, len(todo)
