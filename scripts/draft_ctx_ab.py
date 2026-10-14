#!/usr/bin/env python3
"""草稿翻译「带上下文」A/B（R1 方案 D）：同一批真实送过翻的草稿，各翻一遍（关 / 开）。

从 `<会话>.live.jsonl` 取 N 条：每条 `draft_zh` 行之前最近的那条 `draft` 行（= 当时送翻的那份，
再过 `strip_dangling`），前一句取它之前最近的 `final`。均匀抽样。
课程 / 领域 / 术语的加载方式与 `main.py` 一致（`load_translator(api_key, model, glossary, context,
course=…)`，glossary 默认 `<仓库>/glossary.txt`）。
用云端真请求，费用几分钱。只读 `sessions/`；结果写 Markdown 到 --out（默认 scratchpad，**不写进 sessions/**）。
用法：python scripts/draft_ctx_ab.py [live.jsonl] [--n 40] [--course ECON10770] [--out PATH]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import cloud_translator as CT                          # noqa: E402
from main import strip_dangling                        # noqa: E402

DEFAULT_LIVE = os.path.join(HERE, "..", "sessions", "2026-10-09_120723_ECON10770.live.jsonl")
DEFAULT_OUT = ("/private/tmp/claude-501/-Users-owen/ca5a4f17-da2c-454a-ae54-4174f7b25187/"
               "scratchpad/draft_ctx_ab.md")


def pick(path: str, n: int) -> list[tuple[str, str]]:
    """[(送翻的草稿, 前一句定稿)]，均匀抽 n 条。"""
    last_draft, last_final, pairs = "", "", []
    with open(path, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            k = r.get("kind")
            if k == "draft":
                last_draft = r.get("raw") or r.get("text") or ""
            elif k == "final":
                last_final = r.get("text") or ""
            elif k == "draft_zh" and last_draft:
                pairs.append((strip_dangling(last_draft), last_final))
    if len(pairs) <= n:
        return pairs
    step = len(pairs) / n
    return [pairs[int(i * step)] for i in range(n)]


def esc(s: str) -> str:
    return (s or "").replace("|", "\\|").replace("\n", " ")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("live", nargs="?", default=DEFAULT_LIVE)
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--course", default="ECON10770")
    ap.add_argument("--model", default="deepseek-flash")        # main.py --cloud-model 的默认值
    ap.add_argument("--out", default=DEFAULT_OUT)
    a = ap.parse_args()

    key = CT.load_test_key()
    if not key:
        print("没有 API key"); return 1
    tr = CT.load_translator(key, a.model, os.path.join(HERE, "..", "glossary.txt"), 5,
                            course=a.course)
    pairs = pick(a.live, a.n)
    rows = []
    for i, (en, prev) in enumerate(pairs, 1):
        out = {}
        for flag in ("0", "1"):
            os.environ["CLASSLIVE_DRAFT_CONTEXT"] = flag
            try:
                out[flag] = tr.translate_draft(en, None, context=[prev] if prev else [])
            except Exception as e:                              # noqa: BLE001
                out[flag] = f"(失败: {type(e).__name__}: {str(e)[:60]})"
        os.environ.pop("CLASSLIVE_DRAFT_CONTEXT", None)
        rows.append((en, prev, out["0"], out["1"]))
        print(f"{i}/{len(pairs)}", flush=True)

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    diff = sum(1 for r in rows if r[2] != r[3])
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(f"# 草稿翻译 上下文 A/B（{a.course}，{a.model}，{len(rows)} 条，{diff} 条不同）\n\n")
        f.write(f"领域 = {tr._domain!r}；课程术语 {len(tr._course_terms)} 条（取前 40）。\n\n")
        f.write("| # | 英文草稿 | 上一句 | 关 | 开 |\n|---|---|---|---|---|\n")
        for i, (en, prev, off, on) in enumerate(rows, 1):
            mark = "" if off == on else " **≠**"
            f.write(f"| {i}{mark} | {esc(en)} | {esc(prev)} | {esc(off)} | {esc(on)} |\n")
    print(f"→ {a.out}（{diff}/{len(rows)} 条不同）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
