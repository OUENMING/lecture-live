"""课后笔记 v2：**教授讲什么就讲什么** + 从零基础讲到那个程度 + 课件核对（step 2/3）。

## 这一版与旧版的三处根本不同

| | 旧（`obsidian_writer.REVIEW_SYS`） | 新（本模块） |
|---|---|---|
| 输入 | 只有转录 | 转录 + **本课课件片段**（`materials.Materials`）+ 术语表 |
| 口径 | 「绝不引入外部知识」→ 只能复述（`docs/PLAN-notes-and-ui.md` §0 的病根） | 拆事实/理解：🎙 忠实 · 📖 课件 · 📚 标准理论，冲突按 📖>📚>🎙 裁定 |
| 版式 | 英文为主 + 每条 3 语言层次 | **中文连贯讲解**（直觉→定义→为什么→怎么用→例题→变式），术语写「中文（English term）」 |

## 作者的五条硬要求（2026-10-17，逐条落在 prompt 里）

1. **教授讲到的必须讲，一个不漏** —— `lecture_points`（带时间戳）是覆盖度清单，
   `sections` 是笔记小节；两者对照表进笔记 §覆盖度，可人查。
2. **从零基础讲到教授那个程度**：是什么（说人话）→ 为什么需要 → 考试怎么考 → 例题。
3. **教授没讲到的补基础**：`prereq` 字段（揉碎了讲前置概念）。
4. **题目扩展**：教授例题（`example`，含每步"为什么"）+ 变式（`variants`）+ 补全题（`faded`）。
5. **中文讲解为主**，术语首次出现「中文（English term）」；正确性三道核 + 「修正说明」。

## 三道 LLM 调用（每节课）

    review×N（按 70 句分块，带课件片段）  →  align×1（覆盖度 + 课件对齐）
                                          →  verify×1（数字/公式三道核）

⚠️ 全部**注入 `chat`**（`chat(system, user, max_tokens) -> dict`）：判据零网络、零真 key。
⚠️ 失败一律 fail-soft：某块失败只少那一块（旧版同款纪律），绝不抛。
"""
from __future__ import annotations
import dataclasses
import json
import re

REVIEW_CHUNK = 70           # 每块喂多少句（同旧版：块太大 JSON 会被截断）
REVIEW_WORKERS = 5
REVIEW_MAX_TOKENS = 6000    # v2 每块输出更长（讲解 + 例题 + 变式）—— 实测取值见 out/ 那份验收笔记
ALIGN_MAX_TOKENS = 2000
VERIFY_MAX_TOKENS = 2500

#: 能画的图（**固定菜单**）—— 模型只选 kind + 给参数，画法是 `note_html` 里写死的。
#: 理由：让模型写绘图代码 = 让它在"正确性"上开出第二个战场；固定菜单里
#: 每一个 kind 都是由代码保证正确的（成本曲线/三次函数/供需图正好覆盖
#: 作者点名的三类：AC-MC、函数驻点、供需曲线）。
PLOT_KINDS = {
    "cost_curves": "TC=aq³+bq²+cq 的 AC 与 MC 曲线（标出 min AC 与 MC=AC 交点）。"
                   "params: {a,b,c}",
    "function_cubic": "三次函数 f(x)=ax³+bx²+cx+d 的曲线（标出驻点、凹凸、极值）。"
                      "params: {a,b,c,d}",
    "supply_demand": "线性供求：Qd=d0−d1·p、Qs=s0+s1·p（标出均衡点）。"
                     "params: {d0,d1,s0,s1}",
}

REVIEW_SYS = """你是课堂笔记写作者。给一名靠中文听英文课的本科生写**课后自学笔记** ——
目标是：**一个没去上课的学生，只看这份笔记就能把这段课学会**。

输入：一段逐句中英转录（带 [HH:MM:SS] 时间戳）；可能另附**本课课件/试卷的检索片段**。

## 一、教授讲到的必须讲（覆盖度）
先把这段里教授讲的**每一个知识点**列进 `lecture_points`（一个不漏，带时间戳）；
再为**每一个**知识点写一节 `sections`。宁可小节多、不要合并知识点。

## 二、每一节怎么写（由浅入深，像学长给学弟讲，不要教科书腔）
`prose` 是**连贯的中文段落**（2–5 段），按这个顺序讲透：
① 它是什么（大白话）→ ② 为什么需要它 / 在经济学或社会学里解决什么问题 →
③ 在哪里用到 → ④ 这门课/这类考试会怎么考。
⚠️ **不要一条一条 bullet 堆**：用完整的句子把道理讲通；公式用 LaTeX（行内 $…$，独立 $$…$$）。
⚠️ 术语首次出现写「中文（English term）」；英文原话**只在**引用定义原文/考试原题时出现。

另外按需填：
- `prereq`：理解这一节需要、而**教授没讲**的前置概念 —— 揉碎了补上（懂就空着）。
- `prereq_link`：这一节用到上一节的什么（衔接一句话，第一节能空）。
- `example`：**教授课上做的例题，完整写出来** —— 题目 + 一步步解 + **每步为什么**。
  教授没做例题就空着；有例题而你没写全，是最严重的失分。
- `variants`：在例题基础上给 1–2 道**变式题**（含完整解答步骤）。
- `faded`：把例题的关键步骤挖空的**补全题**（答案分开写）。
- `pitfalls`：学生最容易错的地方。

## 三、正确性（比你写得漂亮重要）
- 每条事实标来源：🎙 课上（转录里确实讲过）/ 📖 课件（附给你的片段里确实有）/
  📚 标准理论（教科书级共识）。
- **转录没讲的事实只能来自 📖 片段或标 📚**；📚 用来讲通"为什么"，
  **绝不许编造本课的数字、例子、结论**。
- 冲突裁定序：📖 课件 > 📚 标准理论 > 🎙 转录。课上讲错、或 ASR 听错的要**改对**，
  并在 `corrections` 里写：{"where": 位置, "spoken": 课上原话, "fixed": 修正值,
  "basis": 依据（📖 文件名 或 📚 哪条理论）}。
- 数字/公式**三道核**：ⓐ 自己代进去算一遍（代数自洽）ⓑ 与 📖 片段里的数字对照
  ⓒ 核不了的**必须**在 `checks` 里标 `unchecked` —— 不许静默通过。
  `checks` 每条：{"claim": 断言（如 "MC(4)=12"）, "how": 怎么核的, "status": "ok|mismatch|unchecked"}。

## 四、语言与格式
- 正文**全中文**（除术语的英文原形、公式、引用原话）。
- `sections` 里每个字段都是**单行字符串**（不出现换行、markdown 标记、引号）；
  `prose` / `steps` 是字符串数组，每个元素一段/一步。
- 适合画图的节给 `plot`：kind 只能从这三个里选 —— __PLOTS__（给不出参数就空着）。

输出严格 JSON（不许多余内容）：
{"title_zh": "本段主题（中文，≤20 字）",
 "lecture_points": [{"ts": "HH:MM", "point": "教授讲的知识点（中文一句）"}],
 "sections": [
   {"heading": "小标题（中文；有术语就写「中文（English term）」）",
    "ts": "本节起点时间戳",
    "prereq_link": "用到上一节的…（可空）",
    "prose": ["第一段", "第二段"],
    "prereq": "前置概念补课（可空）",
    "example": {"title": "例题名（标出处，如 📖 Exercise 5）", "prompt": "题面",
                "steps": [{"text": "这一步做什么", "why": "为什么这一步成立"}],
                "answer": "结论"},
    "variants": [{"q": "变式题", "steps": ["…"], "a": "答案"}],
    "faded": {"q": "补全题（步骤挖空）", "blanks": ["空1的答案", "空2的答案"], "a": "完整解"},
    "pitfalls": ["…"],
    "terms": [{"en": "English term", "zh": "中文"}],
    "sources": ["🎙 HH:MM", "📖 文件名"],
    "plot": {"kind": "cost_curves", "title": "图名", "params": {"a": 1}}}]},
 "qa": [{"q": "自测题（中文题干，术语用英文）", "a": "答案（含步骤）", "kind": "concept|calc|trap"}],
 "corrections": [{"where": "…", "spoken": "…", "fixed": "…", "basis": "…"}],
 "checks": [{"claim": "…", "how": "…", "status": "ok"}],
 "terms": [{"en": "本课出现的学科术语", "zh": "中文"}],
 "review_plan": ["课后可以先做的 2–3 件事（中文，带分钟数）"]}
只输出 JSON。"""

ALIGN_SYS = """你在给一节英文课的**复盘笔记**做覆盖度核对与课件对齐。输入是：
① 教授这节课讲过的知识点清单（来自分块转录，按时间排序，已带时间戳）；
② 这份笔记的小节标题清单。

请输出严格 JSON：
{"covered": [{"point": "知识点（原样抄输入）", "section": "覆盖它的小节标题或 '缺失'"}],
 "materials_used": ["笔记里引用到的课件文件名"],
 "missing": [{"ts": "HH:MM", "point": "没被覆盖的知识点", "why": "为什么可能漏了（一句）"}],
 "notes": "一句总评：这份笔记的覆盖度如何、最该补什么（≤60 字）"}
⚠️ 只做对照，不要发明输入里没有的知识点。只输出 JSON。"""

VERIFY_SYS = """你在给课堂笔记做**数字与公式的第三道核**（前两道是写作时的代数自洽与课件对照）。
输入是一批断言（公式/代入/方程的解），以及可能附上的课件片段。

对每一条：
1. 自己把代数**重算一遍**（代入、移项、求导、解方程）；
2. 如果附了课件片段，对照里面的数字；
3. 判 status：
   - "ok"：重算结果与断言一致（或与课件一致）；
   - "mismatch"：重算结果与断言**不一致**（此时 note 里写正确的值）；
   - "unchecked"：信息不足、算不出来（**不许猜**，标它）。
输出严格 JSON：
{"items": [{"i": 序号, "status": "ok|mismatch|unchecked", "recomputed": "你算出来的值", "note": "依据或为什么不一致（≤60 字）"}]}
只输出 JSON。"""


# ---------------------------------------------------------------- 清洗
def _one_line(v) -> str:
    return " ".join(v.split()) if isinstance(v, str) else ""


def _lines(v) -> list:
    """字符串数组 -> 单行字符串数组（元素里混进换行会破坏渲染的 callout 结构）。"""
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, list):
        return []
    out = []
    for x in v:
        s = _one_line(x)
        if s:
            out.append(s)
    return out


def _steps(v) -> list:
    """`[{"text","why"}]`（也容忍纯字符串数组 —— 模型偶尔会省掉 why）。"""
    if not isinstance(v, list):
        return []
    out = []
    for x in v:
        if isinstance(x, str):
            s = _one_line(x)
            if s:
                out.append({"text": s, "why": ""})
        elif isinstance(x, dict):
            t = _one_line(x.get("text") or x.get("step") or x.get("t"))
            w = _one_line(x.get("why") or x.get("reason"))
            if t or w:
                out.append({"text": t, "why": w})
    return out


def _terms(v) -> list:
    if not isinstance(v, list):
        return []
    out = []
    for x in v:
        if isinstance(x, dict):
            en, zh = _one_line(x.get("en")), _one_line(x.get("zh"))
            if en:
                out.append({"en": en, "zh": zh})
        elif isinstance(x, str) and _one_line(x):
            out.append({"en": _one_line(x), "zh": ""})
    return out


def _clean_section(s) -> dict:
    if not isinstance(s, dict):
        return {}
    head = _one_line(s.get("heading"))
    if not head:
        return {}
    r = {"heading": head, "ts": _one_line(s.get("ts")),
         "prereq_link": _one_line(s.get("prereq_link")),
         "prose": _lines(s.get("prose")) or _lines(s.get("detail")),
         "prereq": _one_line(s.get("prereq")),
         "pitfalls": _lines(s.get("pitfalls")),
         "terms": _terms(s.get("terms")),
         "sources": _lines(s.get("sources")),
         "variants": [], "steps": [], "example": {}, "faded": {}, "plot": {}}
    ex = s.get("example")
    if isinstance(ex, dict):
        steps = _steps(ex.get("steps"))
        if steps or _one_line(ex.get("prompt")):
            r["example"] = {"title": _one_line(ex.get("title")),
                            "prompt": _one_line(ex.get("prompt")),
                            "steps": steps,
                            "answer": _one_line(ex.get("answer"))}
    for v in (s.get("variants") or []):
        if not isinstance(v, dict):
            continue
        q = _one_line(v.get("q") or v.get("question"))
        if q:
            r["variants"].append({"q": q, "steps": _steps(v.get("steps")),
                                  "a": _one_line(v.get("a") or v.get("answer"))})
    fd = s.get("faded")
    if isinstance(fd, dict):
        q = _one_line(fd.get("q"))
        if q:
            r["faded"] = {"q": q, "blanks": _lines(fd.get("blanks")),
                          "a": _one_line(fd.get("a") or fd.get("answer"))}
    pl = s.get("plot")
    if isinstance(pl, dict) and _one_line(pl.get("kind")) in PLOT_KINDS:
        params = pl.get("params")
        r["plot"] = {"kind": _one_line(pl.get("kind")),
                     "title": _one_line(pl.get("title")),
                     "params": params if isinstance(params, dict) else {}}
    return r


def _clean(obj) -> dict:
    """一块的 JSON -> 规范化的 dict。缺字段一律丢弃，**绝不抛**。"""
    if not isinstance(obj, dict):
        return {}
    r: dict = {}
    for k in ("title_zh", "title"):
        v = _one_line(obj.get(k))
        if v:
            r["title_zh"] = v
            break
    pts = []
    for p in (obj.get("lecture_points") or []):
        if isinstance(p, dict):
            t = _one_line(p.get("point") or p.get("text"))
            if t:
                pts.append({"ts": _one_line(p.get("ts")), "point": t})
    if pts:
        r["lecture_points"] = pts
    secs = [x for x in (_clean_section(s) for s in (obj.get("sections") or [])) if x]
    if secs:
        r["sections"] = secs
    qa = []
    for x in (obj.get("qa") or []):
        if not isinstance(x, dict):
            continue
        q, a = _one_line(x.get("q")), _one_line(x.get("a"))
        if q and a:
            qa.append({"q": q, "a": a, "kind": _one_line(x.get("kind"))})
    if qa:
        r["qa"] = qa
    corr = []
    for x in (obj.get("corrections") or []):
        if not isinstance(x, dict):
            continue
        fixed = _one_line(x.get("fixed"))
        if fixed:
            corr.append({"where": _one_line(x.get("where")),
                         "spoken": _one_line(x.get("spoken")),
                         "fixed": fixed, "basis": _one_line(x.get("basis"))})
    if corr:
        r["corrections"] = corr
    checks = []
    for x in (obj.get("checks") or []):
        if not isinstance(x, dict):
            continue
        claim = _one_line(x.get("claim"))
        if claim:
            st = _one_line(x.get("status")).lower()
            checks.append({"claim": claim, "how": _one_line(x.get("how")),
                           "status": st if st in ("ok", "mismatch", "unchecked") else "unchecked"})
    if checks:
        r["checks"] = checks
    terms = _terms(obj.get("terms"))
    if terms:
        r["terms"] = terms
    plan = _lines(obj.get("review_plan"))
    if plan:
        r["review_plan"] = plan
    return r


# ---------------------------------------------------------------- 合并
def _dedup(items: list, key) -> list:
    seen, out = set(), []
    for x in items:
        k = key(x)
        if k and k not in seen:
            seen.add(k)
            out.append(x)
    return out


def merge_blocks(blocks: list) -> dict:
    """各块结果 -> 一份 review dict。**按块序**（合并顺序就是笔记顺序 = 课堂顺序）。

    ⚠️ 小标题加 `[HH:MM]` 前缀（多块时）：读者/模型都要知道"这段在课上的哪一段"，
       也是 `_render` 里回链的锚。
    """
    live = [b for b in blocks if b]
    if not live:
        return {}
    multi = len(live) > 1
    out: dict = {"sections": [], "qa": [], "corrections": [], "checks": [],
                 "terms": [], "lecture_points": [], "review_plan": []}
    for b in live:
        if b.get("title_zh") and not out.get("title_zh"):
            out["title_zh"] = b["title_zh"]
        for s in (b.get("sections") or []):
            if multi and s.get("ts"):
                s = dict(s, heading=f"[{s['ts'][:5]}] {s['heading']}")
            out["sections"].append(s)
        for k, key in (("qa", lambda x: x["q"].strip().lower()),
                       ("corrections", lambda x: (x["where"] + x["spoken"]).strip().lower()),
                       ("checks", lambda x: x["claim"].strip().lower()),
                       ("terms", lambda x: x["en"].strip().lower()),
                       ("lecture_points", lambda x: x["point"].strip().lower())):
            out[k].extend(b.get(k) or [])
        out["review_plan"].extend(b.get("review_plan") or [])
    def _key(k):
        return _DEDUP_KEY.get(k, lambda x: str(x).strip().lower())
    out = {k: (v if k in ("title_zh", "sections") else _dedup(v, _key(k)))
           for k, v in out.items()}
    out["sections"] = [s for s in out["sections"] if s]
    return {k: v for k, v in out.items() if v}


_DEDUP_KEY = {
    "qa": lambda x: x["q"].strip().lower(),
    "corrections": lambda x: (x["where"] + x["spoken"]).strip().lower(),
    "checks": lambda x: x["claim"].strip().lower(),
    "terms": lambda x: x["en"].strip().lower(),
    "lecture_points": lambda x: x["point"].strip().lower(),
}


def transcript_lines(entries: list) -> list:
    return [f"[{e['ts']}] {e['en'] or e['asr']}"
            for e in entries if (e.get("en") or e.get("asr"))]


def chunk_lines(lines: list, size: int = REVIEW_CHUNK) -> list:
    return [lines[i:i + size] for i in range(0, len(lines), size)]


def _user_content(lines: list, *, domain: str = "", materials_block: str = "") -> str:
    body = "\n".join(lines)
    head = f"Course: {domain}\n" if domain else ""
    return (f"{head}{materials_block}"
            f"Transcript (each line starts with its timestamp):\n{body}")


def review(entries: list, *, chat, domain: str = "", materials=None,
           on_progress=None, workers: int = REVIEW_WORKERS,
           max_tokens: int = REVIEW_MAX_TOKENS) -> dict:
    """分块调 LLM → 合并。`chat(system, user, max_tokens) -> dict`。

    ⚠️ 并发发、**按块序收**（同旧版 `_review` 的取舍：串行那 4–5 块是下课→笔记
       最长的一段）。某块失败只少那一块 —— 绝不抛、绝不整体返回空。
    """
    lines = transcript_lines(entries)
    if not lines:
        return {}
    chunks = chunk_lines(lines)
    meta: dict = {}
    from concurrent.futures import ThreadPoolExecutor
    from materials import snippets_block

    def one(chunk: list) -> dict:
        try:
            hits = materials.search("\n".join(chunk)) if materials is not None else []
            user = _user_content(chunk, domain=domain,
                                 materials_block=snippets_block(hits))
            obj = chat(REVIEW_SYS.replace("__PLOTS__", _plots_menu()), user, max_tokens)
        except Exception:                                     # noqa: BLE001
            return {}
        return _clean(obj)

    ex = ThreadPoolExecutor(min(workers, len(chunks)))
    futs = [ex.submit(one, c) for c in chunks]
    ex.shutdown(wait=False)
    blocks = []
    for ci, fut in enumerate(futs):
        if on_progress is not None:
            try:
                on_progress("review", ci, len(chunks))
            except Exception:                                 # noqa: BLE001
                pass
        try:
            blocks.append(fut.result())
        except Exception:                                     # noqa: BLE001
            blocks.append({})
    got = merge_blocks(blocks)
    if got.get("title_zh"):
        meta["title_zh"] = got.pop("title_zh")
    got.update(meta)
    return got


def _plots_menu() -> str:
    return " · ".join(f"`{k}`（{v}）" for k, v in PLOT_KINDS.items())


def _fmt_plot_menu() -> str:
    return _plots_menu()


# ---------------------------------------------------------------- 覆盖度对齐
def align(review_data: dict, *, chat, materials=None,
          max_tokens: int = ALIGN_MAX_TOKENS) -> dict:
    """覆盖度 + 课件对齐（1 次调用）。输入只有"知识点清单 + 小节标题"（便宜）。"""
    pts = review_data.get("lecture_points") or []
    secs = [s.get("heading", "") for s in (review_data.get("sections") or [])]
    if not pts or not secs:
        return {}
    lines = "\n".join(f"[{p.get('ts', '')}] {p.get('point', '')}" for p in pts)
    user = (f"Professor's knowledge points (in order):\n{lines}\n\n"
            f"Note section headings:\n" + "\n".join(f"- {s}" for s in secs))
    try:
        obj = chat(ALIGN_SYS, user, max_tokens)
    except Exception:                                         # noqa: BLE001
        return {}
    if not isinstance(obj, dict):
        return {}
    out: dict = {}
    covered = []
    for x in (obj.get("covered") or []):
        if isinstance(x, dict):
            p, s = _one_line(x.get("point")), _one_line(x.get("section"))
            if p:
                covered.append({"point": p, "section": s})
    if covered:
        out["covered"] = covered
    missing = []
    for x in (obj.get("missing") or []):
        if isinstance(x, dict):
            p = _one_line(x.get("point"))
            if p:
                missing.append({"ts": _one_line(x.get("ts")), "point": p,
                                "why": _one_line(x.get("why"))})
    out["missing"] = missing
    used = _lines(obj.get("materials_used"))
    if used:
        out["materials_used"] = used
    notes = _one_line(obj.get("notes"))
    if notes:
        out["notes"] = notes
    return out


# ---------------------------------------------------------------- 数字三道核
_NUM = re.compile(r"\d+(?:\.\d+)?")


def numeric_claims(review_data: dict, *, limit: int = 24) -> list:
    """从笔记里**挑出值得核的数字断言**（纯函数，判据靠它）。

    ⚠️ 只挑"带等号/结论感"的那种（`MC(4)=12` / `q = 6` / `AC = 24`），
       普通叙述里的数字（"这节课 26 分钟"）不该占核对的额度 ——
       额度有限（每次调用只核一批），要花在会被扣分的地方。
    """
    out: list = []
    for s in (review_data.get("sections") or []):
        texts = list(s.get("prose") or [])
        ex = s.get("example") or {}
        texts += [ex.get("prompt", ""), ex.get("answer", "")]
        texts += [st.get("text", "") for st in (ex.get("steps") or [])]
        for v in (s.get("variants") or []):
            texts += [v.get("q", ""), v.get("a", "")]
            # ⚠️ 变式的**步骤**也要核：那里正是移项/求导出错的地方（判据实测抓到过）。
            texts += [st.get("text", "") for st in (v.get("steps") or [])]
        fd = s.get("faded") or {}
        texts += [fd.get("q", ""), fd.get("a", "")]
        for t in texts:
            if not t or not _NUM.search(t):
                continue
            if not re.search(r"[=＝]|约等于|等于", t):
                continue
            out.append({"where": s.get("heading", ""), "text": t})
            if len(out) >= limit:
                return out
    # 已有 checks 的也带上（模型自己写的断言最值得复核）
    for c in (review_data.get("checks") or []):
        out.append({"where": "checks", "text": c.get("claim", "")})
        if len(out) >= limit:
            break
    return out


def verify_numbers(review_data: dict, *, chat, materials=None,
                   max_tokens: int = VERIFY_MAX_TOKENS) -> list:
    """数字/公式第三道核（1 次调用）→ 规范化后的 `checks` 列表（不是 dict）。

    ⚠️ `mismatch` **不改正文**：它进「修正说明」清单由渲染层显示 ——
       静默改数字是这份笔记最不能做的事（读者用它对答案）。
    """
    claims = numeric_claims(review_data)
    if not claims:
        return list(review_data.get("checks") or [])
    rows = []
    evidence = []
    for i, c in enumerate(claims):
        rows.append(f"[{i}] ({c['where']}) {c['text']}")
        if materials is not None:
            for hit in materials.search(c["text"], k=1):
                evidence.append(f"[{i}] {hit.cite()} {hit.text[:300]}")
    user = ("Claims:\n" + "\n".join(rows)
            + (("\n\nMaterials excerpts:\n" + "\n".join(evidence[:6]))
               if evidence else ""))
    try:
        obj = chat(VERIFY_SYS, user, max_tokens)
    except Exception:                                         # noqa: BLE001
        return list(review_data.get("checks") or [])
    items = obj.get("items") if isinstance(obj, dict) else None
    if not isinstance(items, list):
        return list(review_data.get("checks") or [])
    got: dict = {}
    for it in items:
        if not isinstance(it, dict) or not isinstance(it.get("i"), int):
            continue
        i = it["i"]
        if not (0 <= i < len(claims)):
            continue
        st = _one_line(it.get("status")).lower()
        got[i] = {"claim": claims[i]["text"][:160],
                  "how": (_one_line(it.get("note")) or "")[:160],
                  "status": st if st in ("ok", "mismatch", "unchecked") else "unchecked",
                  "recomputed": _one_line(it.get("recomputed"))[:80]}
    out = [got[i] for i in sorted(got)]
    # 模型自己写的 checks 也留着（它们带着别的依据）；同 claim 以核过的为准。
    seen = {c["claim"].strip().lower() for c in out}
    for c in (review_data.get("checks") or []):
        if c.get("claim", "").strip().lower() not in seen:
            out.append(dict(c, recomputed=""))
    return out


def corrections_from_mismatch(checks: list) -> list:
    """`mismatch` -> 修正说明条目（渲染层与附录共用一条口径）。"""
    out = []
    for c in checks or []:
        if c.get("status") == "mismatch":
            out.append({"kind": "check",
                        "where": "三道核",
                        "spoken": c.get("claim", ""),
                        "fixed": c.get("recomputed", ""),
                        "basis": c.get("how", "")})
    return out


# ---------------------------------------------------------------- 复习排程
def review_dates(date_str: str, *, offsets=(2, 7, 21)) -> list:
    """间隔复习日期（写进笔记的「复习排程」那一节；不做推送）。

    ⚠️ 间隔取 2/7/21 天：`docs/PLAN-notes-and-ui.md` §2 的调研口径（Cepeda 2006/2008：
       最优间隔随目标保留期增长；Dunlosky 2013 里 spacing 是唯二 high utility 之一）。
       刻意**不做**"智能排程" —— 笔记只写日期，什么时候看由人定。
    """
    import datetime
    try:
        d0 = datetime.date.fromisoformat(date_str)
    except ValueError:
        return []
    return [(d0 + datetime.timedelta(days=o)).isoformat() for o in offsets]


def anki_rows(review_data: dict, *, course: str = "", date: str = "") -> list:
    """复习层 → Anki CSV 行 `[front, back, tags]`（**本地生成、零 LLM**）。

    ⚠️ 只导出"能当卡片"的东西：术语（英→中）、自测题（问→答）、公式结论。
       调研（`research/d-format.md`）里 Anki 社区对"自动生成的烂卡"评价很差 ——
       所以**不导出**整段讲解，也不假装它是完整的复习计划（笔记 §复习排程 里有说明）。
    """
    tags = " ".join(x for x in ("classlive", course, (date or "").replace("-", "")) if x)
    rows: list = []
    seen: set = set()
    for t in (review_data.get("terms") or []):
        en, zh = t.get("en", ""), t.get("zh", "")
        if not en or not zh or en.lower() in seen:
            continue
        seen.add(en.lower())
        rows.append([en, zh, f"{tags} terminology"])
    heads = {s.get("heading", "") for s in (review_data.get("sections") or [])}
    for q in (review_data.get("qa") or []):
        if not q.get("q") or not q.get("a"):
            continue
        k = q["q"].strip().lower()
        if k in seen:
            continue
        seen.add(k)
        rows.append([q["q"], q["a"], f"{tags} selftest"])
    for c in (review_data.get("checks") or []):
        if c.get("status") == "ok" and c.get("claim"):
            k = c["claim"].strip().lower()
            if k not in seen:
                seen.add(k)
                rows.append([c["claim"], c.get("how", ""), f"{tags} formula"])
    del heads
    return rows


def to_csv(rows: list) -> str:
    """Anki 导入用 CSV（`front,back,tags`，UTF-8 带 BOM —— Excel 打开不乱码）。"""
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    for r in rows:
        w.writerow(r)
    return "\ufeff" + buf.getvalue()


def dumps(obj) -> str:
    """调试用（收尾日志/判据里看结构）。"""
    return json.dumps(obj, ensure_ascii=False, indent=1)
