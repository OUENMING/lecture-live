"""图该不该画：用 Jev 问「这一节的内容是不是这种结构」，不像就撤图（2026-11-22）。

为什么要这一步：
  系统综述（Guo et al. 2020, Educ Psychol Rev）—— 「放进一张图」本身不保证有用，
  图的结构要和内容对得上。模型偶尔为了画而画（给讲定义的节配时间线）。
  Jev 只给概率、不生成文字，正好当这道闸：每张图问**一个**判据（官方纪律：一次一个判断）。

口径：
  · 只管 `diagram`（时间线 / 因果链 / 2×2 / 层级树）和 `flow`（步骤图）；
    `compare` / `plot` 不过这道闸（对照表本来就要求「容易混的东西」，plot 是固定图形）。
  · state = 本节标题 + 讲解正文（截 1500 字）；每节一个请求，问它身上那几张图。
  · p < `MIN_P` → 撤图（进守卫台账）；**拿不到概率（None）→ 保留**（fail-soft：闸门坏了不许把图全吃掉）。
  · 阈值来源：2026-11-22 校准（5 节真课 × 18 个合并节，`scratchpad/vgate/calib.json`）——
    时间线 / 2×2 两簇分得开（不像 ≤0.47，像 ≥0.64）；因果链几乎处处 ≥0.6（拦不住什么，
    只挡离谱的）；步骤 / 层级树居中。0.5 取在空档里。⚠️ 换问法要重新校准（同 `keypoints` 头：
    绝对阈值跨 prompt 不可比）。

开关：`CLASSLIVE_NOTE_VGATE`（默认 `1`）—— 登记在 `docs/HANDBOOK.md` §6.1。
"""
from __future__ import annotations

import copy
import re

MIN_P = 0.5
STATE_CHARS = 1500

#: 每种结构**一个**判据（英文：Jev 的分辨力是在英文指令上测的；正文是中文也照样判）。
QUESTIONS = {
    "steps": "Does this section teach an ordered procedure, a sequence of steps someone "
             "follows one after another to do something?",
    "timeline": "Does this section describe events or stages that happened at different "
                "points in time, in chronological order?",
    "causal": "Does this section explain a cause-and-effect chain, where one change leads "
              "to another change?",
    "matrix": "Does this section sort things into categories using two dimensions, such as "
              "yes/no on two properties?",
    "tree": "Does this section break one concept down into several types or sub-parts?",
}


def _state(sec: dict) -> str:
    prose = " ".join(str(p) for p in (sec.get("prose") or []))
    prose = re.sub(r"\s+", " ", prose)[:STATE_CHARS]
    return ("A section of lecture notes (in Chinese).\n"
            f"Title: {sec.get('heading') or ''}\n\n{prose}")


def _asks(sec: dict) -> list:
    """本节要问的 `(字段, 结构)`：flow → steps；diagram → 它的 kind。"""
    out = []
    if isinstance(sec.get("flow"), dict):
        out.append(("flow", "steps"))
    dg = sec.get("diagram")
    if isinstance(dg, dict) and dg.get("kind") in QUESTIONS:
        out.append(("diagram", dg["kind"]))
    return out


def gate(review: dict, *, ask=None) -> tuple:
    """返回 `(review, dropped, warns)`；`ask(state, instructions) -> [p|None]`，默认走 Jev。

    不原地改输入。`ask` 抛异常 / 全 None → 原样保留，并在 warns 里说一句（出声纪律）。
    """
    if ask is None:
        import keypoints
        ask = keypoints.ask_batch
    out = copy.deepcopy(review)
    dropped: list = []
    n_asked = n_none = 0
    for i, sec in enumerate(out.get("sections") or [], 1):
        if not isinstance(sec, dict):
            continue
        asks = _asks(sec)
        if not asks:
            continue
        try:
            ps = list(ask(_state(sec), [QUESTIONS[k] for _f, k in asks]) or [])
        except Exception:                                     # noqa: BLE001
            ps = []
        ps += [None] * (len(asks) - len(ps))
        for (field, kind), p in zip(asks, ps):
            n_asked += 1
            if p is None:
                n_none += 1
                continue
            if p < MIN_P:
                t = str((sec.get(field) or {}).get("title") or "")[:20]
                dropped.append(f"第 {i} 节 {field}「{t}」（像{kind} p={p:.2f}）")
                sec.pop(field, None)
    warns = []
    if dropped:
        warns.append(f"图该不该画：撤了 {len(dropped)} 张（" + "、".join(dropped[:3])
                     + ("…" if len(dropped) > 3 else "") + "）")
    if n_asked and n_none == n_asked:
        warns.append(f"图该不该画：{n_asked} 问一条概率都没拿到（Jev 不可用）—— 图全部保留")
    return out, dropped, warns
