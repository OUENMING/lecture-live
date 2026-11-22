#!/usr/bin/env python3
"""笔记可读性量具（**纯本地、离线、不发请求**）：一节笔记读起来有多累。

    ClassLive.app/Contents/MacOS/python scripts/notes_readability.py NOTE.md [...] [--json]

报的指标（`chars_cjk` / `h2` / `h3` / `sections` / `para_n` / `para_cjk_median` /
`para_cjk_p90` / `bullet_ratio` / `long_sent_ratio` / `boilerplate` / `dup_sent_ratio` /
`heading_cont`）逐个定义在 `analyze()` 的返回字典里。

统计正文时**剔除**：YAML 抬头、代码块（``` / ~~~）、`$$…$$` 公式块、HTML 标签与
`<details>…</details>`、Obsidian callout 头行（`> [!info]`）、表格行、列表行
（列表只算进 `bullet_ratio`）。**标题行**只算 `h2`/`h3`/`heading_cont`，不进字数。

⚠️ 这是**粗信号**量具，不是评分器：`dup_sent_ratio` 会把「复习速测里刻意重述的定义」
也算成重复；`boilerplate` 命中不等于写得差（有时是刻意的路标）。看趋势，别看单值。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import statistics

CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
BOILER = {
    "值得注意的是": r"值得注意的是",
    "需要注意的是": r"需要注意的是",
    "这一点非常重要": r"这一点非常重要",
    "非常重要": r"非常重要",
    "总而言之": r"总而言之",
    "综上所述": r"综上所述",
    "首先": r"首先",
    "其次": r"其次",
    "此外": r"此外",
    "这意味着": r"这意味着",
    "不仅…而且": r"不仅[^。]{0,30}而且",
    "不是…而是": r"不是[^。]{0,20}而是",
}
CONT_WORDS = ("续", "补充", "其余", "再谈")
#: 长句阈值（CJK 字数）。
LONG_SENT = 90
#: 重复句的最小长度（去掉标点空白后的 CJK 字数）。
DUP_MIN = 12


def cjk_count(s: str) -> int:
    return len(CJK.findall(s))


def _strip_noise(text: str) -> str:
    """去掉抬头 / 代码块 / 公式块 / HTML / callout 头 —— 剩下的才是正文。"""
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":                 # YAML 抬头
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                lines = lines[i + 1:]
                break
    out, in_fence, in_math = [], False, False
    for ln in lines:
        s = ln.strip()
        if s.startswith("```") or s.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if s.startswith("$$"):
            in_math = not (in_math or s.count("$$") >= 2)
            continue
        if in_math:
            continue
        if re.match(r"^>\s*\[!", s):                        # callout 头
            continue
        out.append(ln)
    t = "\n".join(out)
    t = re.sub(r"<details>.*?</details>", "", t, flags=re.S | re.I)
    t = re.sub(r"</?[A-Za-z][^>\n]*>", "", t)               # 残留 HTML 标签
    return t


def _is_list(s: str) -> bool:
    return bool(re.match(r"^\s*(?:[-*+]|\d+[.)])\s+", s))


def _is_table(s: str) -> bool:
    return s.lstrip().startswith("|")


def _is_heading(s: str) -> bool:
    return bool(re.match(r"^#{1,6}\s+", s.lstrip()))


def _sentences(body: str) -> list:
    parts = re.split(r"[。！？!?]+|\n+", body)
    return [p.strip() for p in parts if p.strip()]


def analyze(text: str) -> dict:
    body_raw = _strip_noise(text)
    lines = body_raw.splitlines()
    h2 = [ln.lstrip() for ln in lines if re.match(r"^##\s+", ln.lstrip())]
    h3 = [ln.lstrip() for ln in lines if re.match(r"^###\s+", ln.lstrip())]

    para_buf, paras, bullet_lines, nonblank = [], [], 0, 0
    body_lines = []
    for ln in lines:
        s = ln.strip()
        if s:
            nonblank += 1
        if _is_heading(ln) or _is_table(s):
            if para_buf:
                paras.append(" ".join(para_buf)); para_buf = []
            continue
        if _is_list(s):
            bullet_lines += 1
            if para_buf:
                paras.append(" ".join(para_buf)); para_buf = []
            body_lines.append(re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", ln))
            continue
        if not s:
            if para_buf:
                paras.append(" ".join(para_buf)); para_buf = []
            continue
        para_buf.append(s)
        body_lines.append(ln)
    if para_buf:
        paras.append(" ".join(para_buf))

    body = "\n".join(body_lines)
    pc = sorted(cjk_count(p) for p in paras) or [0]
    median = statistics.median(pc)
    p90 = pc[min(len(pc) - 1, int(round(0.9 * (len(pc) - 1))))]

    sents = _sentences(body)
    longs = [s for s in sents if cjk_count(s) > LONG_SENT]

    counts = {k: len(re.findall(p, body)) for k, p in BOILER.items()}

    keyed = {}
    for s in sents:
        k = re.sub(r"[\s\W_]+", "", s)
        if cjk_count(k) >= DUP_MIN:
            keyed[k] = keyed.get(k, 0) + 1
    dup_n = sum(1 for v in keyed.values() if v >= 2)

    return {
        "chars_cjk": cjk_count(body),
        "h2": len(h2), "h3": len(h3), "sections": len(h2),
        "para_n": len(paras),
        "para_cjk_median": median,
        "para_cjk_p90": p90,
        "bullet_ratio": round(bullet_lines / nonblank, 3) if nonblank else 0.0,
        "long_sent_ratio": round(len(longs) / len(sents), 3) if sents else 0.0,
        "boilerplate": sum(counts.values()),
        "boilerplate_by_word": counts,
        "dup_sent_ratio": round(dup_n / len(keyed), 3) if keyed else 0.0,
        "heading_cont": sum(1 for h in h2 + h3 if any(w in h for w in CONT_WORDS)),
        "sent_n": len(sents),
    }


ROW = ("chars_cjk", "sections", "h2", "h3", "para_n", "para_cjk_median",
       "para_cjk_p90", "bullet_ratio", "long_sent_ratio", "dup_sent_ratio",
       "boilerplate", "heading_cont")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("notes", nargs="+")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rows = {}
    for n in args.notes:
        p = pathlib.Path(n)
        if not p.is_file():
            print(f"✗ 没有这个文件：{p}")
            continue
        rows[p.name] = analyze(p.read_text(encoding="utf-8", errors="replace"))
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0

    print(f"{'metric':18}" + "".join(f"{k[:26]:>28}" for k in rows))
    for m in ROW:
        print(f"{m:18}" + "".join(f"{str(rows[k][m]):>28}" for k in rows))
    print("\n套话逐词（出现次数）：")
    for k in rows:
        w = {a: b for a, b in rows[k]["boilerplate_by_word"].items() if b}
        print(f"  {k}: {w or '无'}")
    print(f"\n阈值：长句 = CJK > {LONG_SENT} 字；重复句 = 去标点后 CJK ≥ {DUP_MIN} 字且全文 ≥2 次")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
