#!/usr/bin/env python3
"""重点句 —— 用 Jev（TypeSafe System One）的 `noul` 给句子打概率，取 **top-k**。

    ClassLive.app/Contents/MacOS/python keypoints.py sessions/<某节课>.md [--top 12]

**它不生成文本**，只回答「这一句值得记吗」并给一个 0–1 的概率。所以它比走翻译那条
DeepSeek 便宜得多、也快得多（实测一次 20 问 ≈ 1550 input / 355 output tokens）。

## ⭐ 为什么是 top-k，不是阈值

实测（2026-09-29，四个真实段落）：
- 概率分布是**压缩的**（0.05–0.77），**不是双峰** —— p≥0.9 永远是 0 条
- ⚠️ **同一段换一个 prompt 写法，同一句从 0.77 掉到 0.61** → 绝对阈值**跨 prompt 不可比**
- 要用阈值就得先校准，而校准需要 ground truth —— 而 ground truth 恰好不稳定
  （`[论文]` Zhang & Fung 2009：人类标注者对「哪句是重点」**边界样本上根本不一致**）
- ⭐ 但**排序是稳的**（四个段落里 top-5 几乎不变）→ 所以取 **top-k**

## 官方两条纪律（`[官方]` `docs.typesafe.ai`）

1. `primitives.md`：「Ask for a judgment a knowledgeable person makes **in a second**…
   'Analyze this message and determine the best course of action' is **not**」
   → 每条 `instructions` **只问一个判据**。第一版塞了五个判据，被官方原文判为反例。
2. `jaggedness` #5：「Accuracy falls as the state grows with content **unrelated to the
   decision**」→「**Filter first; send only what the question needs**」
   → `state` 只放这一段，而且每一问**点名**它只看哪一句。

## ⚠️⚠️ 四个实测出来的坑（都踩过）

| 坑 | 症状 |
|---|---|
| `questions` 是**对象**不是数组，每个值也是**对象** | `400 expected record, received array` |
| 字段叫 **`instructions`**（不是 `text`），`type` 必填 | `400 param: questions.q1.instructions` |
| ⚠️ **必须发 `User-Agent`** | urllib 默认发 `Python-urllib/3.x` → Cloudflare **403 `error code: 1010`**。**它看起来像"请求太大"** —— 我先用 curl 试小的过了、再用 urllib 试大的 403，差点去查 size 上限 |
| 一次最多 **20 个问题** | `400 at most 20 questions per call`。⚠️ **这条不在官方文档里** —— 官方只有 token 预算、**没有数量上限**，所以 **20 是 CommandCode 代理加的** |
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

#: 一次请求最多几个问题。⚠️ **CommandCode 代理的限制，不是 TypeSafe 的**（见模块头）。
CHUNK = 20

#: `User-Agent` —— ⚠️ **不给会被 Cloudflare 挡成 403**（见模块头）。
UA = "ClassLive/0.0 (+https://github.com/OUENMING/lecture-live)"

ENDPOINT = "https://api.commandcode.ai/provider/v1/systemone"
MODEL = "typesafe/jev"

#: 一句话、一个判据。⭐ 照官方 `primitives.md` 那个「in a second」的标准写的。
#: ⚠️ **改这句会让绝对概率整体移动**（实测同一句 0.77 → 0.61），
#:    但**排序稳定** —— 所以下游只许用排名，别拿绝对分去卡阈值。
INSTRUCTION = (
    "Look at line {tag} only. If a student had to pass an exam on this lecture, "
    "would they write {tag} down on their revision sheet? Ignore every other line.")


#: 句末标点。⚠️ **只认 ASCII 的三种** —— 转录里几乎不出现中文标点，
#: 而 `…` / `--` 那些是**未完**的信号，不能当句末。
_END = (".", "?", "!")


def thoughts(sents, *, max_chars: int = 260):
    """把**一行字幕**合并成**一句完整的话** → `[(句子, [原始下标, …]), …]`。

    ⚠️⚠️ **为什么必须合**：转录是「一句字幕一行」，而**字幕在句子中间就断**。
       实测（SOC10020 那节 275 句）挑出来的 top-15 里：
         · `He used the method of mapping.`  —— 完整 ✅
         · `was both thought over, created systematically…` —— **半句** ❌
         · `about how we relate to other people…`          —— **半句** ❌
       单行打分 = **在给碎片打分**，而碎片天然"不像值得记的内容"，系统性地压低分数。

    ⚠️ 判据是**上一行有没有句末标点**（不是"这一行够不够长"）——
       `was both thought over…` 那一行本身挺长，但它是上一句的续。
    ⚠️ **必须带回原始下标** —— 挑出来的东西要能回到转录里定位；
       丢掉下标 = 挑出一条你**找不到在哪**的句子（同 `atom.traceable` 那条纪律）。
    ⚠️ `max_chars` 是**兜底**：ASR 漏标点时不许无限累积下去。
    """
    out: list = []
    buf: list = []
    idx: list = []
    for i, s in enumerate(sents or ()):
        buf.append(s)
        idx.append(i)
        joined = " ".join(buf)
        if joined.rstrip().endswith(_END) or len(joined) >= max_chars:
            out.append((joined, list(idx)))
            buf, idx = [], []
    if buf:                       # 结尾没标点的残段也要交出来，**不许悄悄丢掉**
        out.append((" ".join(buf), list(idx)))
    return out


def build_state(sents) -> str:
    """一段句子 → `state`。⚠️ 只放这一段（官方 `jaggedness` #5：无关内容会拖低准确率）。"""
    return ("This is an excerpt from a university lecture (English). Each numbered "
            "line is one sentence the lecturer said, in order.\n\n"
            + "\n".join(f"S{i + 1}. {s}" for i, s in enumerate(sents)))


def build_questions(n: int, *, tag=lambda i: f"S{i + 1}") -> dict:
    """`n` 个 `noul` —— 一句一个判据。**纯函数。**"""
    return {f"S{i + 1}": {"type": "noul",
                          "instructions": INSTRUCTION.format(tag=tag(i))}
            for i in range(n)}


def parse_answers(payload, n: int) -> list:
    """响应 → 长度恰为 `n` 的概率表（缺的补 `None`）。**纯函数。**

    ⚠️ **按 `S<i>` 的编号回填，不按返回顺序** —— 少一条就整体错位一格，
       而错位在「取 top-k」下**完全看不出来**（还是 k 条，只是全偏了）。
    """
    out: list = [None] * n
    for k, v in ((payload or {}).get("answers") or {}).items():
        if not (isinstance(k, str) and k[:1] == "S" and k[1:].isdigit()):
            continue
        i = int(k[1:]) - 1
        p = (v or {}).get("noul")
        if 0 <= i < n and isinstance(p, (int, float)):
            out[i] = float(p)
    return out


def score(sents, *, ask) -> list:
    """一批句子 → 每句一个概率（`None` = 这次没拿到）。

    ⚠️ **`ask` 是注入进来的**（照 `codebase-design`「接受依赖，不要自己造依赖」）：
       判据传个假 `ask` 就能跑，不用联网。生产那条是 `ask_commandcode`。
    ⚠️ 超过 `CHUNK` 就分批 —— 每批各自带一份 `state`（⚠️ 该服务**没有 prompt cache**，
       所以 state 是被重复计费的）。
    """
    sents = list(sents or ())
    out: list = []
    for lo in range(0, len(sents), CHUNK):
        part = sents[lo:lo + CHUNK]
        got = parse_answers(ask(build_state(part), build_questions(len(part))), len(part))
        out.extend(got)
    return out


def top_k(scores, sents, k: int = 12) -> list:
    """概率最高的 `k` 句 → `[(概率, 下标, 句子), …]`，降序。**纯函数。**

    ⚠️ `None`（没拿到答案）**排除**，不当 0 —— 「没答」和「答了 0」是两件事。
    ⚠️ 并列按句子下标排（否则结果不可复现）。
    """
    rows = [(p, i, s) for i, (p, s) in enumerate(zip(scores, sents)) if p is not None]
    rows.sort(key=lambda r: (-r[0], r[1]))
    return rows[:k]


# ------------------------------------------------------------------ 生产那条路
def ask_commandcode(state: str, questions: dict, *, token: str, timeout: float = 60.0):
    """真发一次请求。⚠️ **`User-Agent` 必须有** —— 见模块头那条 403。"""
    body = json.dumps({"model": MODEL, "state": state,
                       "questions": questions}).encode("utf-8")
    req = urllib.request.Request(
        ENDPOINT, data=body,
        headers={"Authorization": "Bearer " + token,
                 "Content-Type": "application/json",
                 "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        # ⚠️ **把响应体带出来** —— 这个 API 的错误信息全在 body 里
        #    （`at most 20 questions per call` 那种），只报状态码等于没说。
        detail = e.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"Jev {e.code}: {detail}") from e


def sentences(path) -> list:
    """一份会话文件 → 英文定稿句。⚠️ 读法唯一的定义点在 `obsidian_writer._parse`。"""
    import pathlib
    out = []
    for ln in pathlib.Path(path).read_text(encoding="utf-8", errors="ignore").splitlines():
        if ln.startswith("> **EN**: "):
            t = ln[9:].strip()
            if len(t) > 15:          # 太短的（"Okay." / "Right."）没有判断价值
                out.append(t)
    return out


def pick(sents, *, token_value: str = "", timeout: float = 30.0, k: int = 10) -> list:
    """一整节课的英文句子 → `[(概率, 句子, [原始行下标]), …]`，降序。**收尾那一步调它。**

    ⚠️⚠️ **失败一律返回空表，绝不抛。** 它跑在**收尾**那一步，抛了会连累后面的笔记落盘
       —— 本仓库记过同类形状（收尾里一个没包住的小功能 → 整节课丢笔记）。
    ⚠️ **没配 token 也返回空表**（= 功能关着，不是错误）。
    ⚠️ 单位走 `thoughts()`（并成一句完整的话）—— 实测并句比单行**省 14% token，
       而且能捞出单行漏掉的句子**（`this is one of the most important
       methodological breakthroughs` 那句就是）。
    """
    if not token_value:
        token_value = token()
    if not token_value or not sents:
        return []
    try:
        built = thoughts(sents)
        items = [t for t, _ in built]
        sc = score(items, ask=lambda st, qs: ask_commandcode(
            st, qs, token=token_value, timeout=timeout))
        return [(p, items[i], built[i][1]) for p, i, _ in top_k(sc, items, k)]
    except Exception:                                         # noqa: BLE001
        return []


def token(*, root=None) -> str:
    """从 `~/.classlive/jev-token` 读 token；**没有就返回空串**（= 功能关着）。

    ⚠️ **不是错误** —— 没配 token 就是不用这个功能（同 `polish` 的 fail-soft）。
    """
    import pathlib
    try:
        import paths
        p = paths.jev_token(root=root)
    except Exception:                                         # noqa: BLE001
        return ""
    try:
        return p.read_text(encoding="utf-8").splitlines()[0].strip()
    except (OSError, IndexError):
        return ""


def token_from_cc_switch() -> str:
    """从 cc-switch 库里取 CommandCode 的 token。

    ⚠️ **这是实验用的近路**，不是产品的取凭证方式 —— 产品那条走
       `paths.credentials()`（`~/.classlive/credentials`）。
    """
    import pathlib
    import sqlite3
    db = pathlib.Path.home() / ".cc-switch/cc-switch.db"
    con = sqlite3.connect("file:" + str(db) + "?mode=ro", uri=True)
    cfg = json.loads(con.execute(
        "select settings_config from providers where name='commandcode'"
    ).fetchone()[0])
    return cfg["env"]["ANTHROPIC_AUTH_TOKEN"]


def main(argv=None) -> int:
    import sys
    import time
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__)
        return 2
    path = argv[0]
    k = 12
    if "--top" in argv:
        k = int(argv[argv.index("--top") + 1])
    unit = "thought" if "--unit" in argv and argv[argv.index("--unit") + 1] == "thought" \
        else "line"
    raw = sentences(path)
    if not raw:
        print("这份文件里没读到英文句子")
        return 1
    if unit == "thought":
        built = thoughts(raw)
        sents = [t for t, _ in built]
        origin = [ix for _, ix in built]
    else:
        sents, origin = raw, [[i] for i in range(len(raw))]
    n_calls = (len(sents) + CHUNK - 1) // CHUNK
    print(f"{path}  单位={unit}  {len(raw)} 行字幕 → {len(sents)} 个打分单位"
          f" → {n_calls} 次调用")
    tok = token_from_cc_switch()
    tin = tout = 0

    def ask(state, qs):
        nonlocal tin, tout
        got = ask_commandcode(state, qs, token=tok)
        u = got.get("usage") or {}
        tin += int(u.get("input_tokens") or 0)
        tout += int(u.get("output_tokens") or 0)
        return got

    t0 = time.time()
    sc = score(sents, ask=ask)
    dt = time.time() - t0
    print(f"耗时 {dt:.1f}s（{dt / max(1, n_calls):.1f}s/次）  "
          f"tokens in {tin} / out {tout}  ≈ ${tin / 1e6 * 0.042:.5f}")
    print(f"\n=== 概率最高的 {k} 条 ===")
    for p, i, s in top_k(sc, sents, k):
        # ⭐ **带上原始行号区间** —— 挑出来的东西必须能回到转录里定位
        ix = origin[i]
        tag = f"L{ix[0] + 1}" if len(ix) == 1 else f"L{ix[0] + 1}-{ix[-1] + 1}"
        print(f"  {p:.3f}  [{tag:>10}] {s[:104]}")
    got = [p for p in sc if p is not None]
    if got:
        print(f"\n分布: min {min(got):.2f}  max {max(got):.2f}  "
              f"中位 {sorted(got)[len(got) // 2]:.2f}   拿到 {len(got)}/{len(sents)} 条")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
