"""流式字幕量具的纯函数部分：词错误率、草稿回改率、延迟分位数。

没有 I/O、没有模型 —— 这样它们能在默认闸门里毫秒级被测试。
跑音频的部分在 `scripts/stream_eval.py`。

指标定义的出处与取舍（2026-10-08 研读开源同类项目后定）：
  · WER/CER：先归一化（小写、去标点）再算词级编辑距离 —— 借 WhisperLiveKit 的口径，
    否则 "Hello," 与 "hello" 会被记成一个错。中文按字算（CER）。
  · 回改率：**开源项目里没有现成指标**，这里自定义 ——
    相邻两次草稿之间，上一份里"已经显示过、却没有作为前缀保留下来"的词数 ÷ 上一份的词数。
    只比前缀，因为 UI 是整行替换，前面的词一动用户就会看见跳。
  · hold-back K：若只显示"到最长公共前缀再退 K 词"，K 词以内的回改会被灰尾吸收；
    `rewritten_beyond_k` 量的就是吸收后仍会被用户看见的回改。
"""
from __future__ import annotations
import math
import re

_PUNCT = re.compile(r"[^\w\s']", re.UNICODE)
_NUM = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?(%?)")
_ONES = ("zero one two three four five six seven eight nine ten eleven twelve thirteen "
         "fourteen fifteen sixteen seventeen eighteen nineteen").split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def _int_words(n: int) -> str:
    """0..999,999,999,999 的英文读法（不带连字符、不带 and）：与 ASR 输出数字/参考稿写字母之间对齐用。"""
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + ("" if n % 10 == 0 else " " + _ONES[n % 10])
    if n < 1000:
        return _ONES[n // 100] + " hundred" + ("" if n % 100 == 0 else " " + _int_words(n % 100))
    for unit, name in ((10 ** 9, "billion"), (10 ** 6, "million"), (10 ** 3, "thousand")):
        if n >= unit:
            rest = n % unit
            return _int_words(n // unit) + " " + name + ("" if rest == 0 else " " + _int_words(rest))
    return str(n)


def _num_sub(m: "re.Match") -> str:
    whole = int(m.group(1).replace(",", ""))
    out = _int_words(whole) if whole < 10 ** 12 else m.group(1)
    if m.group(2):
        out += " point " + " ".join(_ONES[int(c)] for c in m.group(2))
    if m.group(3):
        out += " percent"
    return " " + out + " "
_CJK = re.compile(r"[一-鿿]")


def normalize(text: str) -> list[str]:
    """小写、去标点（保留撇号）、按空白切词；含中日韩字符时按字切。"""
    # 数字先读成英文单词：ASR 写 "10%"、参考稿写 "ten percent" 不该被记成错
    t = _NUM.sub(_num_sub, text.lower().replace("’", "'"))
    t = _PUNCT.sub(" ", t)
    t = t.replace("_", " ")
    if _CJK.search(t):
        out: list[str] = []
        for tok in t.split():
            if _CJK.search(tok):
                out.extend(ch for ch in tok if not ch.isspace())
            else:
                out.append(tok)
        return out
    return t.split()


def edit_ops(ref: list[str], hyp: list[str], free_hyp_prefix: bool = False) -> tuple[int, int, int]:
    """词级 Levenshtein：返回 (替换, 删除, 插入)。删除=参考里有、假设里漏了。

    free_hyp_prefix=True：假设开头的若干词可以免费跳过（半全局对齐）。
    用于"草稿只转写最近 10s，定稿是整句"——草稿天然只覆盖定稿的后半段。"""
    n, m = len(ref), len(hyp)
    # dp[i][j] = (总代价, S, D, I)
    prev = [((0, 0, 0, 0) if free_hyp_prefix else (j, 0, 0, j)) for j in range(m + 1)]
    for i in range(1, n + 1):
        cur = [(i, 0, i, 0)]
        for j in range(1, m + 1):
            if ref[i - 1] == hyp[j - 1]:
                cur.append(prev[j - 1])
                continue
            s = prev[j - 1]
            d = prev[j]
            ins = cur[j - 1]
            best = min((s[0], 0), (d[0], 1), (ins[0], 2))
            if best[1] == 0:
                cur.append((s[0] + 1, s[1] + 1, s[2], s[3]))
            elif best[1] == 1:
                cur.append((d[0] + 1, d[1], d[2] + 1, d[3]))
            else:
                cur.append((ins[0] + 1, ins[1], ins[2], ins[3] + 1))
        prev = cur
    _, s, d, i = prev[m]
    return s, d, i


def wer(ref_text: str, hyp_text: str) -> dict:
    """返回 {'wer','sub','del','ins','ref_len'}；参考为空时 wer=None（没有分母就不报数）。"""
    r, h = normalize(ref_text), normalize(hyp_text)
    s, d, i = edit_ops(r, h)
    return {"wer": (s + d + i) / len(r) if r else None,
            "sub": s, "del": d, "ins": i, "ref_len": len(r)}


def lcp_len(a: list[str], b: list[str]) -> int:
    k = 0
    for x, y in zip(a, b):
        if x != y:
            break
        k += 1
    return k


#: 「hold-back 判据」用的 k 默认值 —— **唯一定义点**（`rewrite_stats` / `stream_eval.evaluate` /
#: `stream_eval --k` / `class_report` 全指向它）。散成四份手抄的 `2` 时，
#: 数字就会在"同一把尺子"的旗号下悄悄不同源。
DEFAULT_K = 2


def rate(num: int, den: int) -> float | None:
    """比率；分母为 0 返回 `None`（**不是 0** —— "没有数"和"比率是 0"要分得开）。"""
    return num / den if den else None


def rewrite_stats(drafts: list[str], k: int = DEFAULT_K) -> dict:
    """同一句内、连续草稿之间的回改。

    shown      = 各次"上一份草稿"的词数之和（被用户看见过的词）
    rewritten  = 其中没有作为前缀保留下来的词数
    beyond_k   = 把最后 k 个词当灰尾吸收之后，仍然被改写的词数
    """
    shown = rewritten = beyond = 0
    steps = 0
    for prev_t, new_t in zip(drafts, drafts[1:]):
        p, n = normalize(prev_t), normalize(new_t)
        if not p:
            continue
        steps += 1
        l = lcp_len(p, n)
        shown += len(p)
        rewritten += len(p) - l
        beyond += max(0, (len(p) - k) - l)
    return {"steps": steps, "shown": shown, "rewritten": rewritten,
            "rewritten_beyond_k": beyond, "k": k,
            "rate": rate(rewritten, shown),
            "rate_beyond_k": rate(beyond, shown)}


def final_rewrite(last_draft: str, final: str) -> dict:
    """最后一份草稿 → 定稿：词级编辑距离 ÷ 草稿词数（定稿覆盖草稿时用户看到的跳动量）。
    定稿开头比草稿多出来的词不计（那是草稿窗口之外的内容）。"""
    d, f = normalize(last_draft), normalize(final)
    if not d:
        return {"edits": None, "draft_len": 0, "rate": None, "prefix_kept": None}
    # 草稿只覆盖最近 N 秒：对定稿做"免费跳过开头"的半全局对齐，量的才是重叠部分的改写
    s, de, i = edit_ops(d, f, free_hyp_prefix=True)
    return {"edits": s + de + i, "draft_len": len(d),
            "rate": (s + de + i) / len(d)}


def percentile(values: list[float], p: float) -> float | None:
    """最近秩分位数（不插值），与 WhisperLiveKit 的 p95 口径同类；空表返回 None。"""
    if not values:
        return None
    v = sorted(values)
    idx = max(0, min(len(v) - 1, math.ceil(p / 100.0 * len(v)) - 1))
    return v[idx]


def summarize_latency(values: list[float]) -> dict:
    return {"n": len(values), "p50": percentile(values, 50),
            "p95": percentile(values, 95), "max": max(values) if values else None}
