#!/usr/bin/env python3
"""把一堆课件**建议**分到各门课 —— **只出建议**，写盘由调用方在作者点头后做。

⚠️ 本模块只做两件事：① 机械信号（课号字面）② 让模型在**给定候选中选一个或答 none**。
   它**不写任何文件、不跑 prep、不认识 Obsidian**。这是故意的：`prep._archive`
   是归档的唯一实现（见它的注释），这里再写一份拷贝循环就会重演那次的静默覆盖。

## ⚠️⚠️ 四条**已量穿、因此不在本模块里**的机械信号

2026-09-28 在作者真实的 457 份 `~/Downloads` 上量的（详见
`docs/PLAN-entry-panel.md` §7.9）。**别把它们再加回来** —— 每一条都看着像有信号：

| 信号 | 覆盖 | 精确率 |
|---|---|---|
| 文件名 × 课名单词 | 1.8% | 75% |
| 正文第一页 × 课名单词 | 6.3% | 7% |
| 正文 × 术语表撞词（184 条人工术语） | 7.2% | 12% |
| ⭐ **课号字面** | 0.7% | **100%** ← 唯一活下来的 |

根因：判别词全是通用学术词，`function` / `sample` / `policy` 在**任何**学术文档里都有。
阈值曲线也救不回来：≥3 条 12%、≥5 条 33%、≥6 条 50%、≥8 条才 100% 但只剩 1 份。

## ⚠️⚠️ 三条硬约束（各有出处，别凭手感改）

1. **不用模型自报的置信度当门槛。** `[论文]` ICLR 2024 逐字「LLMs, when verbalizing
   their confidence, **tend to be overconfident**」；且两篇研究结论**冲突**
   （另一篇说特定 prompt 下可校准）→ **不裁决 ⇒ 不采信**。
2. **必须允许「都不属于」。** `[官方]` paperless-ngx 逐字：类别是闭世界时
   「paperless will assign one of these correspondents to **ANY** new document」——
   一份朋友的讲义会被硬塞给某一门课。
3. **人工确认是唯一的闸门。** `[官方]` DEVONthink 逐字「The highest ranked suggestion
   is **presented first**」+ **不自动归档**；`[论文]` arXiv 2510.05307：
   **81%** 的参与者偏好"中间确认"而非"末尾确认"，任务耗时 **−13.54%**。

## 复用的是哪个 chat 助手

`build_notes._chat_json` —— 仓库里已有 **5 处**各自手写 httpx 调 DeepSeek
（`build_notes` / `cloud_translator` / `obsidian_writer` ×2 / `polish`）。
⚠️ **这里复用其中一个，不做第 6 处。** 真正的修法是把它们抽成 `llm.py`，
但那是另一批的事；本模块不扩大那笔债。
"""
from __future__ import annotations

import dataclasses
import pathlib
import re

MODEL = "deepseek-chat"
MAX_TOKENS = 300
TEMPERATURE = 0.0
HEAD_PAGES = 2                  # 判归属看头两页就够（§7.10 实测）
HEAD_CHARS = 2000               # 再多的正文对判断没帮助，只烧 token
TERMS_PER_COURSE = 12           # 给模型看的术语样本条数
NONE = ""                       # 模型答"都不属于"时返回的空课号


@dataclasses.dataclass(frozen=True)
class Verdict:
    """一份文件的归属**建议**。`course is None` = 未分类（合法且常见的结果）。"""
    path: str
    course: str | None
    why: str
    source: str                 # "code"（课号字面）| "model" | "none"（没抽到正文）


def by_code(name: str, courses) -> str | None:
    """课号字面命中。**唯一活下来的机械信号**：覆盖 0.7%，但 3/3 全对。

    多个课号同时出现 → 弃权（`ECON10730/10740` 这种联署的讲义确实存在）。
    """
    low = str(name).lower()
    hit = [c for c in courses if c.lower() in low]
    return hit[0] if len(hit) == 1 else None


def briefs(glossary_dir, courses, *, terms: int = TERMS_PER_COURSE) -> dict:
    """课号 → 给模型看的一段自我介绍。

    ⚠️ **不能只给课名。** `[论文]` ACL 2025《Dynamic Label Name Refinement》：
    标签嵌入相似度到 **0.91** 时 CoT「easily misled by similar label names」，
    **6 个数据集里 4 个反而掉分**；提升标签区分度后 **+0.48 ~ +5.23 点**。
    我们四门 ECON 的课名几乎同义，正撞在这个坑上
    → **带术语样本**就是那条论文推荐的修法，而且不需要新数据：
    `glossary/*.txt` 是 184 条人工术语，只有 8 个词跨课共享。
    """
    import courses as courses_mod
    out = {}
    for c in courses:
        title, sample = c, []
        try:
            p = courses_mod.glossary_file(glossary_dir, c)
            title = courses_mod.readiness(glossary_dir, c).title
            rows = [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines()]
            sample = [r for r in rows if r and not r.startswith("#")][:terms]
        except Exception:                                     # noqa: BLE001
            pass                                              # 读不到就退回光课号 + 课名
        out[c] = (f"{c} {title}" if title != c else c) + (
            f"　术语样本: {'、'.join(sample)}" if sample else "")
    return out


SYS = """你在帮一个学生的课件归档工具判断**这份课件属于哪门课**。

只依据给你的课件正文，**只能**从给定的课程列表里选一个，或者答"都不属于"。

⚠️ 「都不属于」是**常用且正确**的答案 —— 学生的下载目录里有大量与这些课无关的文件
（别人的讲义、期刊论文、其他课的作业、账单）。**宁可答空，不要硬凑。**

输出严格 JSON：{"course": "<课号，或空字符串表示都不属于>", "why": "一句话中文理由（不超过 20 字）"}
- `course` 必须是给定的课号之一，或空字符串。不许自造课号。
- `why` 要指向正文里的**具体证据**（课程名/课号/老师/术语），不许写"看起来像"。"""


def build_prompt(head: str, course_briefs: dict) -> str:
    rows = "\n".join(f"- {b}" for b in course_briefs.values())
    return (f"【候选课程】\n{rows}\n\n"
            f"【课件正文（前 {HEAD_PAGES} 页节选）】\n{head[:HEAD_CHARS]}\n\n"
            f"这份课件属于哪门课？")


def parse_reply(obj, courses) -> tuple[str | None, str]:
    """把模型回的那个 dict 变成 (课号 | None, 理由)。

    ⚠️ **不读它的置信度**（约束 1）。只做两件事：课号在不在候选里、理由照抄。
    """
    if not isinstance(obj, dict):
        return None, "模型没给出可解析的结果"
    got = str(obj.get("course") or "").strip()
    why = str(obj.get("why") or "").strip()[:40]
    if got in courses:
        return got, why or "模型判断"
    return None, why or "模型判断：都不属于"


def suggest(paths, *, courses, course_briefs, head_of, ask,
            on_progress=None) -> list[Verdict]:
    """逐份给建议。**不写盘、不改任何状态。**

    `head_of(path) -> str`：抽正文（注入，测试里给假的，不用 AppKit）
    `ask(prompt) -> dict | None`：调模型（注入，测试里给假的，不发网络请求）
    `on_progress(done, total, name)`：在**工作线程**里被调，回写 UI 由调用方负责
    """
    out = []
    total = len(paths)
    for i, p in enumerate(paths, 1):
        p = pathlib.Path(p)
        if on_progress:
            on_progress(i, total, p.name)

        c = by_code(p.name, courses)
        if c:
            out.append(Verdict(str(p), c, "文件名里有课号", "code"))
            continue

        try:
            head = head_of(p) or ""
        except Exception as e:                                # noqa: BLE001
            out.append(Verdict(str(p), None, f"读不出正文：{type(e).__name__}", "none"))
            continue
        if len(head.strip()) < 40:
            out.append(Verdict(str(p), None, "抽不出正文（扫描件？）", "none"))
            continue

        try:
            obj = ask(build_prompt(head, course_briefs))
        except Exception as e:                                # noqa: BLE001
            out.append(Verdict(str(p), None, f"模型调用失败：{type(e).__name__}", "none"))
            continue
        got, why = parse_reply(obj, set(courses))
        out.append(Verdict(str(p), got, why, "model"))
    return out


# ---------------------------------------------------------------- 真实依赖
def head_of(path) -> str:
    """真抽正文：`extract` 的**头两页**、不开 OCR。

    ⚠️ 实测 55/444 = 12.4% 的 PDF 这条抽不出字（扫描件）→ 那些直接进「未分类」。
    **不 OCR**：分类不值得付 OCR 的时间与钱，而且它只是个"预填"。
    """
    import extract
    res = extract.extract(path, ocr=False, max_pages=HEAD_PAGES)
    return "\n".join(b.text for b in res.blocks)


def make_ask(key: str, *, model: str = MODEL):
    """真的调模型。复用 `build_notes._chat_json`（见模块头：不做第 6 处手写 httpx）。"""
    from build_notes import _chat_json

    def ask(prompt: str):
        return _chat_json(key, model, SYS, prompt, MAX_TOKENS, TEMPERATURE)

    return ask


def main(argv=None) -> int:
    """命令行跑一遍：**在真文件上出建议表，不写任何东西**（给作者看效果用）。"""
    import argparse
    import courses as courses_mod
    from cloud_translator import load_api_key

    ap = argparse.ArgumentParser(description="只看不动：给一批文件出归属建议")
    ap.add_argument("paths", nargs="*", help="要判的文件；不给就读 ~/Downloads")
    ap.add_argument("--glossary", default=str(
        pathlib.Path(__file__).resolve().parent / "glossary"))
    ap.add_argument("--limit", type=int, default=0, help="最多判几份（0=全跑）")
    ap.add_argument("--no-model", action="store_true",
                    help="只跑机械层，不调模型（零成本、零网络）")
    a = ap.parse_args(argv)

    paths = [pathlib.Path(p) for p in a.paths] or sorted(
        p for p in (pathlib.Path.home() / "Downloads").rglob("*")
        if p.is_file() and p.suffix.lower() in (".pdf", ".pptx"))
    if a.limit:
        paths = paths[:a.limit]

    names = courses_mod.list_courses(a.glossary)
    print(f"候选课程 {names}")
    b = briefs(a.glossary, names)
    for k, v in b.items():
        print(f"  {v[:96]}")
    print()

    ask = (lambda prompt: None) if a.no_model else None
    if ask is None:
        key = load_api_key()
        if not key:
            print("❌ 没有 API key（用 --no-model 只跑机械层）")
            return 2
        ask = make_ask(key)

    def prog(i, n, name):
        print(f"  [{i:>3}/{n}] {name[:60]}", flush=True)

    vs = suggest(paths, courses=names, course_briefs=b,
                 head_of=head_of, ask=ask, on_progress=prog)
    hit = [v for v in vs if v.course]
    print(f"\n{'='*74}\n判了 {len(vs)} 份；有归属的 {len(hit)} 份，未分类 {len(vs)-len(hit)} 份\n")
    for v in hit:
        print(f"  [{v.course}] {pathlib.Path(v.path).name[:56]}")
        print(f"          {v.source} · {v.why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
