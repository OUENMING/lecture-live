"""课后笔记的「先定计划、再按节写」：一次调用对全课出节计划，代码校验后按节切块。

为什么：`note_v2.review()` 原来按固定 `REVIEW_CHUNK=70` 行切块，每块各自决定写哪几节 ——
笔记的节反映的是**切块**而不是**课程结构**：同一概念被切进两块、两块各写一遍（重复、
车轱辘话），一块里又塞进不相干的话题。现在（照 Lintao-Lab/faithful-lecture-notes 的做法）：
  ① `architect`（一次调用）或 `note_cover.skeleton` 的章节骨架先出**全课节计划**；
  ② `validate` 用**代码**钉死边界（行号递增 / 过短合并 / 过长等分 / 0–1 节退回老路）；
  ③ 每块只写计划里分给它的那一节，`work_order` 把全课大纲 + 本节的问题/概念/边界告诉它。

口径（改之前先读）：
  · `validate` 是**纯函数**，只吃 `sections` 列表 + 转录行，不调模型 —— 判据全压在它身上；
  · 全部 fail-soft：`architect` 任何异常/空 → `[]`，`review()` 收到空计划就走老路
    （老路的 user 内容逐字不变，`tests/test_note_v2.py` 钉着）；
  · 过长节被等分成几段时**每段 title 完全相同** —— `note_v2.merge_units` 按标题主干把
    同名的连续小节并回一个单元，读者看到的仍是一节；
  · `boundary` 是「相邻节容易重复的内容」的**事前**约定（谁写谁不写），不是事后去重。
开关 `CLASSLIVE_NOTE_PLAN=1` 开（默认 0；登记在 `docs/HANDBOOK.md` §6.1）。
"""
from __future__ import annotations

import note_v2

MIN_LINES = 20          # 少于这么多行的节并掉（不够写一段话）
MAX_LINES = 110         # 多于这么多行的节等分（一块的输出 token 装不下）
MAX_CONCEPTS = 5
ORDER_MAX = 1500        # work_order 的字符上限（提示词不能挤掉转录）

PLAN_SYS = """你在给一节英文大学课的课后笔记**定节计划**。输入是整节课带时间戳的转录。你不写笔记正文，只决定笔记分几节、每节讲什么。

怎么分：
- 按老师真实的授课结构分：一节 = 一个读者需要弄懂的问题（一个概念、一个模型、一道完整的例题及其结论）。不要按时间平均切，不要因为老师换了一张幻灯片就另起一节。
- 同一个概念如果老师前后讲了两次（先讲、做题、再回头总结），归进同一节，start 取第一次讲的时间。
- 先修关系在前：后面的节要用到的概念，放在前面的节里讲清。
- 课务通知（作业、考试、截止日期）、开场寒暄不单独成节，并进相邻的节。
- 一节课通常 4–9 节；45 分钟以内的课 3–6 节。

每节给：
- title：中文短标题（≤16 字），说清这节讲什么，不带编号、不带「续」「补充」「其余部分」。
- question：这一节替读者回答的那个问题，一句中文。
- start：这一节在转录里开始的时间戳（必须是转录里出现过的 HH:MM:SS）。
- concepts：这一节必须讲到的 2–5 个概念或例题，短语，英文术语保留原文。
- boundary：相邻节容易重复的内容，写明「X 留给《某节》」；没有就空串。

只输出 JSON：{"sections":[{"title":"","question":"","start":"HH:MM:SS","concepts":[""],"boundary":""}]}"""


# ---------------------------------------------------------------- 小工具（纯函数）
def _txt(v) -> str:
    return " ".join(str(v or "").split())


def _concepts(v) -> list:
    """模型给的 concepts → 去空去重、最多 `MAX_CONCEPTS` 条短语。"""
    if isinstance(v, str):
        v = [v]
    out: list = []
    for x in (v or []):
        t = _txt(x)
        if t and t not in out:
            out.append(t)
    return out[:MAX_CONCEPTS]


def _line_seconds(lines: list) -> list:
    """`[HH:MM:SS] text` 行 → 秒数（解不出就是 None，行号照旧对齐）。"""
    return [note_v2._ts_seconds(str(ln).split(" ", 1)[0].strip("[]")) for ln in lines]


def _close(rows: list, n: int) -> list:
    """按下一个 lo（末节按课末）落 `hi`，丢掉空区间。"""
    for i, r in enumerate(rows):
        r["hi"] = rows[i + 1]["lo"] if i + 1 < len(rows) else n
    return [r for r in rows if r["hi"] > r["lo"]]


# ---------------------------------------------------------------- 校验（纯函数）
def validate(raw, lines: list) -> list:
    """模型给的 `sections`（或 dict 里的 `sections`）→ 校验过的计划；任何异常都返回 `[]`。"""
    try:
        return _validate(raw, lines)
    except Exception:                                     # noqa: BLE001  fail-soft
        return []


def _validate(raw, lines: list) -> list:
    items = raw.get("sections") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return []
    secs = _line_seconds(lines)
    n = len(secs)
    if not n:
        return []
    # a. 缺 title / start 解析不出 → 丢；按 start 排序、同 start 去重（先出现的留）
    kept: list = []
    for it in items:
        if not isinstance(it, dict):
            continue
        title = _txt(it.get("title"))
        start = note_v2._ts_seconds(it.get("start"))
        if not title or start is None:
            continue
        kept.append((start, title, it))
    kept.sort(key=lambda x: x[0])
    rows: list = []
    seen: set = set()
    for start, title, it in kept:
        if start in seen:
            continue
        seen.add(start)
        lo = next((i for i, t in enumerate(secs) if t is not None and t >= start), None)
        if lo is None:            # start 落在课末之后 → 没有对应的行
            continue
        rows.append({"title": title, "question": _txt(it.get("question")),
                     "concepts": _concepts(it.get("concepts")),
                     "boundary": _txt(it.get("boundary")), "lo": lo})
    if not rows:
        return []
    # b. 第一节强制从第 0 行起（开场的寒暄/课务归它）；行号必须严格递增
    rows[0]["lo"] = 0
    uniq: list = []
    for r in rows:
        if uniq and r["lo"] <= uniq[-1]["lo"]:
            continue
        uniq.append(r)
    out = _close(uniq, n)
    out = _merge_short(out, n)      # c
    if len(out) < 2:                # e. 0–1 节 → 没有计划，退回老路
        return []
    for k, r in enumerate(out, 1):  # 逻辑节号：等分前定（读者看到的「第 k 节」）
        r["n"] = k
    out = _split_long(out)          # d
    out = _close(out, n)
    for i, r in enumerate(out, 1):  # 块序号（与 `chunks` 一一对应）
        r["i"] = i
    return out


def _join(a: dict, b: dict) -> dict:
    """相邻两节合并：title/question 取**行数多**的那节；concepts 并集（≤5）；
    boundary 取**后一节**的（合并后它的结尾就是新的结尾）。"""
    win, lose = (a, b) if (a["hi"] - a["lo"]) >= (b["hi"] - b["lo"]) else (b, a)
    cons = list(win.get("concepts") or [])
    for c in (lose.get("concepts") or []):
        if c not in cons:
            cons.append(c)
    return {"title": win["title"],
            "question": win.get("question") or lose.get("question") or "",
            "concepts": cons[:MAX_CONCEPTS],
            "boundary": b.get("boundary") or a.get("boundary") or "",
            "lo": a["lo"], "hi": b["hi"]}


def _merge_short(rows: list, n: int) -> list:
    """c. 行数 < `MIN_LINES` 的节并进前一节（第一节则并进后一节），直到都不短。"""
    cur = [dict(r) for r in rows]
    while len(cur) > 1:
        idx = next((i for i, r in enumerate(cur) if r["hi"] - r["lo"] < MIN_LINES), None)
        if idx is None:
            break
        if idx == 0:
            cur[1] = _join(cur[0], cur[1])
            del cur[0]
        else:
            cur[idx - 1] = _join(cur[idx - 1], cur[idx])
            del cur[idx]
    return _close(cur, n)


def _split_long(rows: list) -> list:
    """d. 行数 > `MAX_LINES` 的节等分成 ceil(n/MAX_LINES) 段，**每段 title 完全相同**。"""
    out: list = []
    for r in rows:
        n = r["hi"] - r["lo"]
        k = max(1, -(-n // MAX_LINES))
        if k == 1:
            out.append(dict(r))
            continue
        base, rem = divmod(n, k)
        lo = r["lo"]
        for j in range(k):
            piece = dict(r, part=j + 1, parts=k)
            piece["lo"] = lo
            piece["hi"] = lo + base + (1 if j < rem else 0)
            out.append(piece)
            lo = piece["hi"]
    return out


# ---------------------------------------------------------------- 出计划
#: ⚠️ 2026-11-22 实测：4000 时长课（680 行 / 4 万字符）的思考 token 把预算吃光 → 空回复
#:    → 计划静默为空（12 节里 3 节）。12000 时同样三节都出了 10 节计划（22–26 s）。
ARCHITECT_MAX_TOKENS = 12000


def architect(lines: list, *, chat, domain: str = "",
              max_tokens: int = ARCHITECT_MAX_TOKENS) -> list:
    """一次调用拿全课节计划。`chat(system, user, max_tokens) -> dict`；失败返回 `[]`（并出声）。"""
    try:
        if not lines:
            return []
        user = f"Course: {domain}\n" + "\n".join(lines)
        return validate(chat(PLAN_SYS, user, max_tokens), lines)
    except Exception as e:                                # noqa: BLE001  fail-soft，但要出声
        print(f"   ⚠ 节计划调用失败（{type(e).__name__}: {str(e)[:80]}）→ 按老路切块", flush=True)
        return []


def from_skeleton(sk, lines: list) -> list:
    """`note_cover.skeleton(...)` 的章 → 同样形状的计划（question 空串、concepts 取该章前 5 条要点）。"""
    items: list = []
    for c in (sk or []):
        if not isinstance(c, dict):
            continue
        title = _txt(c.get("title"))
        if not title or note_v2._ts_seconds(c.get("t0")) is None:
            continue
        pts = [_txt(p.get("zh")) or _txt(p.get("text"))
               for p in (c.get("points") or []) if isinstance(p, dict)]
        items.append({"title": title, "question": "", "start": c.get("t0"),
                      "concepts": [x for x in pts if x], "boundary": ""})
    return validate(items, lines) if items else []


def chunks(lines: list, plan: list) -> list:
    """按计划的 `lo`/`hi` 切行 —— 与 `plan` **一一对应**（`validate` 保证每段非空）。"""
    out: list = []
    for it in (plan or []):
        lo = max(0, int(it.get("lo") or 0))
        hi = int(it.get("hi") or 0)
        out.append(list(lines[lo:hi]) if hi > lo else [])
    return out


def _order_text(plan: list, item: dict, k: int, width: int) -> str:
    rows: list = []
    seen_n: set = set()
    for p in plan:
        n = p.get("n") or p.get("i")
        if n in seen_n:                 # 等分出的同节后几段：大纲里只列一次
            continue
        seen_n.add(n)
        line = f"第 {n} 节《{p.get('title')}》"
        if p.get("question"):
            line += f"——{p['question']}"
        if width and len(line) > width:
            line = line[:max(1, width - 1)] + "…"
        rows.append(line)
    parts = ["## 本节的工作单（全课是**先定好节计划、再按节写**的）",
             "\n**全课大纲**（只用来定位：知道自己在哪儿，别写别的节）：",
             "\n".join(rows),
             f"\n**你只写第 {k} 节《{item.get('title')}》** —— 本块写出的全部小节都属于这一节，"
             f"不要另起计划里没有的节，也不要把别的节的内容搬进来。"
             + (f"\n这一节太长、按时间分成了 {item['parts']} 段，你拿到的是第 {item['part']} 段："
                f"只写本段转录里讲到的部分；"
                + ("从这一节的开头讲起。" if item["part"] == 1 else
                   "前面几段已经写过这一节的开头和定义，别重复，直接接着讲本段的新内容。")
                if item.get("parts", 1) > 1 else ""),
             f"要回答的问题：{item.get('question') or item.get('title')}"]
    cons = "、".join(item.get("concepts") or [])
    if cons:
        parts.append(f"要讲到的概念：{cons}")
    if item.get("boundary"):
        parts.append(f"这些留给别的节：{item['boundary']}")
    parts.append(f"本块所有小节的 heading 直接以「{item.get('title')}」这几个字开头"
                 f"（不加书名号、不加编号；要分子题就写成「{item.get('title')}：子题」）。")
    return "\n".join(parts)


def work_order(plan: list, item: dict) -> str:
    """给「只写这一节」的那一块的提示文字（中文，≤ `ORDER_MAX` 字符；大纲太长就截每行）。"""
    if not plan or not isinstance(item, dict):
        return ""
    if not (1 <= int(item.get("i") or 0) <= len(plan)):
        return ""
    k = int(item.get("n") or item.get("i"))
    s = ""
    for width in (240, 90, 64, 44, 30, 20, 14):
        s = _order_text(plan, item, k, width)
        if len(s) <= ORDER_MAX:
            return s
    return s[:ORDER_MAX]
