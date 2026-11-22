#!/usr/bin/env python3
"""离线金标错误核对：拿 `tests/fixtures/local_runs/gold_errors.json` 的真题 / 真错，
   逐条问「这份笔记里，它讲到了吗？抄对了吗？」

    ClassLive.app/Contents/MacOS/python scripts/notes_gold_errors.py NOTE.md [...] \
        --course ECON10770 [--gold tests/fixtures/local_runs/gold_errors.json] [--json]

纯本地、**不发请求、不读 vault**（传进来的路径而已）。gold 是列表，每条
`{id, course, topic, truth, wrong_seen, check}`；`check` 里可能有
`present` / `bad_any` / `bad_all` / `good_any` / `good_all`（都是正则串列表）。
只取 `course` 匹配的条（大小写不敏感）。

判定（**按这个顺序**，先命中先算）：
  1. `present` 有任一条不命中 → `not_covered`（这段课压根没讲/笔记没写）
  2. `bad_any` 任一命中 **或** `bad_all` 全命中 → `wrong`
  3. `good_any` 任一命中 **或** `good_all` 全命中 → `right`
  4. 其余 → `unclear`（讲了，但没抓到对的也没抓到错的）
`wrong` 会打印命中的那段原文（前后各 60 字），方便人核 —— 正则只是线索，
**判 `wrong` 不等于内容真的错**（否定句、错处勘误都会撞上）。

文本归一化（匹配前）：去 `$` / `\\(` / `\\)` / `**`；`−–—` → `-`；
`×`、`\\times`、`\\cdot` → `*`；全角数字与逗号 → 半角。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
GOLD = ROOT / "tests" / "fixtures" / "local_runs" / "gold_errors.json"
FW = str.maketrans("０１２３４５６７８９，", "0123456789,")
VERDICTS = ("not_covered", "wrong", "right", "unclear")


def normalize(text: str) -> str:
    """公式壳子摘掉，只留可比对的字符。"""
    t = text.replace("**", "")
    for s in ("$", "\\(", "\\)"):
        t = t.replace(s, "")
    for s in ("−", "–", "—"):
        t = t.replace(s, "-")
    t = re.sub(r"\\times\b\s*|\\cdot\b\s*", "*", t)
    for s in ("×", "·"):
        t = t.replace(s, "*")
    return t.translate(FW)


def _hits(pats, text: str) -> list:
    out = []
    for p in pats or []:
        for m in re.finditer(p, text):
            out.append(m)
            break
    return out


def _ctx(text: str, m, pad: int = 60) -> str:
    a, b = max(0, m.start() - pad), min(len(text), m.end() + pad)
    s = (text[a:m.start()] + "⟦" + m.group(0) + "⟧" + text[m.end():b])
    return " ".join(s.split())


def judge(item: dict, text: str) -> dict:
    ck = item.get("check") or {}
    miss = [p for p in (ck.get("present") or []) if not re.search(p, text)]
    bad = _hits(ck.get("bad_any"), text)
    ba = ck.get("bad_all") or []
    if ba and all(re.search(p, text) for p in ba):
        bad = bad or _hits(ba, text)
    good = _hits(ck.get("good_any"), text)
    ga = ck.get("good_all") or []
    if ga and all(re.search(p, text) for p in ga):
        good = good or _hits(ga, text)
    if miss:
        v, ev = "not_covered", "缺：" + " | ".join(miss)
    elif bad:
        v, ev = "wrong", " · ".join(_ctx(text, m) for m in bad[:2])
    elif good:
        v, ev = "right", " · ".join(_ctx(text, m) for m in good[:2])
    else:
        v, ev = "unclear", "（没命中 good/bad）"
    return {"id": item.get("id", "?"), "topic": item.get("topic", ""),
            "verdict": v, "evidence": ev}


def scan(path: pathlib.Path, gold: list) -> list:
    raw = path.read_text(encoding="utf-8", errors="replace")
    # 归一化会挪动字符位置 → 只在归一化文本上算证据（够人看）。
    return [judge(it, normalize(raw)) for it in gold]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("notes", nargs="+", help="笔记 .md")
    ap.add_argument("--course", required=True)
    ap.add_argument("--gold", default=str(GOLD))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    gp = pathlib.Path(args.gold)
    if not gp.is_file():
        print(f"✗ 没有 gold：{gp}")
        return 1
    gold = [x for x in json.loads(gp.read_text(encoding="utf-8"))
            if str(x.get("course", "")).upper() == args.course.upper()]
    if not gold:
        print(f"✗ gold 里没有 course={args.course} 的条目（共 "
              f"{len(json.loads(gp.read_text(encoding='utf-8')))} 条）")
        return 1
    unknown = sorted({k for it in gold for k in (it.get("check") or {})
                      if k not in ("present", "bad_any", "bad_all",
                                   "good_any", "good_all")})
    if unknown:
        print(f"⚠️ gold 里还有本脚本不判的键（忽略）：{', '.join(unknown)}")

    all_rows, summary = [], {v: 0 for v in VERDICTS}
    for n in args.notes:
        p = pathlib.Path(n)
        if not p.is_file():
            print(f"✗ 没有这个文件：{p}")
            continue
        rows = scan(p, gold)
        for r in rows:
            summary[r["verdict"]] = summary.get(r["verdict"], 0) + 1
        all_rows.append({"note": str(p), "rows": rows})
        if args.json:
            continue
        print(f"\n=== {p} · {args.course} · {len(gold)} 条金标 ===")
        print(f"{'id':34} {'判定':12} 证据")
        for r in rows:
            print(f"{r['id']:34} {r['verdict']:12} {r['evidence'][:150]}")

    if args.json:
        out = {"course": args.course, "summary": summary,
               "gold_n": len(gold), "notes": all_rows}
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(f"\n合计 {sum(summary.values())} 条判定：" +
              " · ".join(f"{v} {summary.get(v, 0)}" for v in VERDICTS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
