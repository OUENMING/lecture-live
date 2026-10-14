"""课后笔记里的「碎句合并」：把字幕切成两三行的同一句话并回一行，整句重译一遍。

为什么（2026-10-14 实测，`docs/experiments/fragment_merge_20261014/`）：
  3 节真课里 20–28% 的定稿**没有句末标点**、下一句属于下一段 —— 一句话被 12 s 切段劈成了两行，
  各自单独翻译。同一批 120 对、同一个模型盲评（1–5 分）：
    · 逐行单译（现状）               意思 4.12 · 通顺 3.44
    · 带下一段草稿当「后文」再译      4.08 · 3.41   ← 没用，直播里不做
    · 两行并成一句再译               4.46 · 4.34   ← 81 对里赢 71、输 10
  并句要等下一段定稿（中位 9.5 s 后才到），**直播等不起**；笔记是课后读的，等得起 → 只做在笔记里。

⚠️ 只改**笔记里**的 entries（同 `polish.py`）：`sessions/` 里的逐句日志一个字不动，它是三方契约。
⚠️ fail-soft：任何一批出错 / 回来的条目对不上 → 那一批**原样保留**，绝不因为合并丢句。
⚠️ EN 不重矫正 —— 只把几行原样拼起来（去掉 carry 超时补的「 …」），中文按整句重译。
开关 `CLASSLIVE_NOTE_MERGE=0` 关（登记在 `docs/HANDBOOK.md` §6.1）。
"""
from __future__ import annotations

import concurrent.futures as cf
import os
import re

END = (".", "?", "!")
MAX_LINES = 3            # 一句最多并几行（再长多半是 ASR 漏了标点，不是一句话）
MAX_WORDS = 60
MAX_GAP_S = 20.0         # 两行时间戳隔这么久就不当成同一句（中间停过课 / 换了话题）
WORKERS = 8
TIMEOUT_S = 20.0         # 单次请求
DEADLINE_S = 60.0        # 整段合并的总截止：到点还没回来的那几句照旧分行（收尾不许被它拖长）

# ⚠️⚠️ **一句一请求，不批量**（2026-10-14 实测）：12 句一批 + 每句带前文时，模型把别的句子 / 前文的译文
#    填到了这一句的编号下 —— 150 句盲评里 37 句「意思」反而变差，翻出来一看是整句**对错了号**
#    （"Next, if you have some questions…" 拿到的是另一句讲「未雨绸缪」的译文）。编号对得上不等于内容对得上，
#    校验不出来；一句一请求从根上没有这个问题（对照实验 M 那组就是这么做的）。
SYS = """你是英文课堂字幕的中文译者。实时字幕把老师说的一句话切成了几行、各自翻译，读起来是断的。
现在给你**拼回来的整句英文**（EN），请把它当成一句完整的话重新译成中文。
- 准确、通顺，专业术语用中文教材的说法；口语里的重复、口头禅可以略去，但不许增删意思。
- 只译 EN 这一句：前文只供理解，不要翻译它；不要改写英文，不要补充原文没有的内容。
- zh 是单行，不含换行、markdown 或引号。
输出严格 JSON：{"zh": "中文译文"}
只输出 JSON。"""


def enabled() -> bool:
    return os.environ.get("CLASSLIVE_NOTE_MERGE", "1") != "0"


def _text(e: dict) -> str:
    return (e.get("en") or e.get("asr") or "").strip()


def _unfinished(t: str) -> bool:
    """没有句末标点，或是 carry 超时补了「…」的那种 —— 这一行话没说完。"""
    return bool(t) and (t.endswith("…") or not t.endswith(END))


def _sec(ts) -> int | None:
    m = re.fullmatch(r"(\d{1,2}):(\d{2}):(\d{2})", str(ts or ""))
    return int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3]) if m else None


def runs(entries: list[dict]) -> list[tuple[int, int]]:
    """要并的行段 `[(i0, i1), …]`（闭区间，i1 > i0）。纯函数。

    从一行没说完的开始，往后接到第一行有句末标点为止；超过 `MAX_LINES` / `MAX_WORDS`、
    时间戳隔太久、或下一行是空的，就停（停在哪就并到哪，不足两行不算）。
    """
    out, i, n = [], 0, len(entries)
    while i < n - 1:
        if not _unfinished(_text(entries[i])):
            i += 1
            continue
        j, words = i, len(_text(entries[i]).split())
        while j + 1 < n and j - i + 1 < MAX_LINES and _unfinished(_text(entries[j])):
            nxt = _text(entries[j + 1])
            a, b = _sec(entries[j].get("ts")), _sec(entries[j + 1].get("ts"))
            if not nxt or words + len(nxt.split()) > MAX_WORDS:
                break
            if a is not None and b is not None and not 0 <= b - a <= MAX_GAP_S:
                break
            j += 1
            words += len(nxt.split())
        if j > i:
            out.append((i, j))
        i = j + 1
    return out


def _join(parts: list[str]) -> str:
    return " ".join(p.strip().removesuffix("…").strip() for p in parts if p and p.strip())


def _chat(key, model, system, user, max_tokens, timeout):
    import llm_client
    obj = llm_client.chat_json(key, model, system, user, max_tokens=max_tokens,
                               temperature=0.2, timeout=timeout)
    return obj if isinstance(obj, dict) else {}


def merge_entries(entries: list[dict], key: str | None, model: str, *, domain: str = "",
                  stats: dict | None = None, cancel=None, chat=None) -> list[dict]:
    """返回**新的** entries：每个行段并成一条（时间戳取第一行、`star` 取任一行），其余原样。

    `stats` 填 `{"runs", "merged", "failed"[, "timed_out"]}`；`chat` 默认联网，测试注入替身。
    绝不外抛（这是 `close()` 里「额外一层」）。
    """
    st = stats if stats is not None else {}
    st.update(runs=0, merged=0, failed=0)
    if not key or not entries or not enabled():
        return entries
    rs = runs(entries)
    st["runs"] = len(rs)
    if not rs:
        return entries
    chat = chat or _chat
    from translator import domain_block
    en_of = {k: _join([entries[x].get("en") or entries[x].get("asr") or ""
                       for x in range(i0, i1 + 1)]) for k, (i0, i1) in enumerate(rs)}

    def one(k: int) -> str:
        if cancel is not None and cancel.is_set():
            return ""
        i0 = rs[k][0]
        ctx = " ".join(_text(e) for e in entries[max(0, i0 - 2):i0])
        obj = chat(key, model, SYS, f"{domain_block(domain)}前文: {ctx or '(无)'}\nEN: {en_of[k]}",
                   400, TIMEOUT_S)
        zh = obj.get("zh")
        return " ".join(zh.split()) if isinstance(zh, str) else ""

    zh_of: dict[int, str] = {}
    ex = cf.ThreadPoolExecutor(WORKERS)
    futs = {ex.submit(one, k): k for k in range(len(rs))}
    try:
        for f in cf.as_completed(futs, timeout=DEADLINE_S):
            try:
                zh = f.result()
            except Exception:                             # noqa: BLE001
                zh = ""
            if zh:
                zh_of[futs[f]] = zh
            else:
                st["failed"] += 1
    except cf.TimeoutError:
        st["timed_out"] = sum(1 for f in futs if not f.done())
    except Exception:                                     # noqa: BLE001
        pass
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
    out, k_at = [], {i0: k for k, (i0, _) in enumerate(rs)}
    i = 0
    while i < len(entries):
        k = k_at.get(i)
        if k is None or k not in zh_of:
            out.append(entries[i])
            i += 1
            continue
        i0, i1 = rs[k]
        grp = entries[i0:i1 + 1]
        out.append({**grp[0], "en": en_of[k], "zh": zh_of[k],
                    "asr": _join([e.get("asr") or "" for e in grp]),
                    "star": any(e.get("star") for e in grp), "merged": len(grp)})
        st["merged"] += 1
        i = i1 + 1
    return out
