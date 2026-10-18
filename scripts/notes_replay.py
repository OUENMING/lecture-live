#!/usr/bin/env python3
"""冻结回放：拿**守卫前原始输出**（`review_raw.json`）重跑守卫链 → md + html。**零 LLM 调用**。

为什么要有它（作者 F15 要求）：守卫的改动必须能在**冻结的模型输出**上重放验证 ——
不重新生成笔记（那会烧钱、还会因为模型漂移把"判据有效"和"模型今天写得不一样"混在一起）。
`runs/final_v3/<job>/review_raw.json` 是**守卫链之前**的模型输出，就是最忠实的输入。

用法：
    python scripts/notes_replay.py --data tests/fixtures/raw_runs/v3_busi3243/note_data.json \\
        --raw tests/fixtures/raw_runs/v3_busi3243/review_raw.json --out /tmp/v3r/busi3243

⚠️ 需要模型的那几步（速览/串讲、例题返修、代数讲解一致性、引用逐字核对、支持性核对、
   超限压缩、停产价格口径）**不跑**，逐条记 `would-repair` —— 回放给出的是**守卫层**的
   成品；那几层的产物沿用 v3 真跑里的原文（`note_data.json` 里已有），所以页面能整份读。
"""
from __future__ import annotations

import argparse
import copy
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import note_guards                                                  # noqa: E402
import note_ledger                                                  # noqa: E402
import note_render                                                  # noqa: E402
import note_v2                                                      # noqa: E402

#: 需要模型、回放里**不跑**的那几步（逐条记 `would-repair`）。
LLM_STEPS = [
    ("例题返修", "obsidian_writer._repair_exercises"),
    ("代数/讲解一致性", "note_v2.fix_symbol_consistency"),
    ("引用逐字核对", "note_guards.quote_check"),
    ("支持性核对", "note_guards.support_check"),
    ("超限单元压缩", "obsidian_writer._shorten_overlimit"),
    ("速览/串讲", "obsidian_writer._with_overview"),
    ("停产价格口径", "note_guards.ensure_caliber（要课件材料，回放不联网取材料）"),
]


def _lecture_terms(review: dict) -> set:
    """本课术语（小写）—— 回放里替掉写作层那句 `known=` 判据。**纯函数。**"""
    out: set = set()
    for s in review.get("sections") or []:
        if not isinstance(s, dict):
            continue
        for t in (s.get("terms") or []):
            if isinstance(t, dict):
                for k in ("en", "zh"):
                    if str(t.get(k) or "").strip():
                        out.add(str(t[k]).strip().lower())
    return out


def guard_chain(review: dict, *, entries: list) -> tuple:
    """按写作层**同一个顺序**跑确定性的守卫（模型那几步跳过并记账）。

    返回 `(review, warns, would_repair, counts)` —— `counts` 是**本次回放自己**数出来的
    `{"repaired","dropped","flagged","downgraded"}`（F15-4③：核对记录页脚必须按回放结果重算，
    不许沿用上一次真跑留下的那行 —— 旧的 `§15（missing-term：schooling）` 已经不该出现了）。
    **纯函数**（除 deepcopy）。
    """
    out = copy.deepcopy(review)
    warns: list = []
    would: list = []
    counts: dict = {"repaired": 0, "dropped": 0, "flagged": 0, "downgraded": 0}
    known = lambda t: str(t).strip().lower() in _lecture_terms(out)   # noqa: E731
    steps = (
        (lambda r: note_guards.retime_components(r), "组件错挂"),
        (lambda r: note_v2.clamp_components(r), "组件上限"),
        (lambda r: note_v2.dedupe_components(r), "组件去重"),
        (lambda r: note_ledger.reconcile(r), "更正账本核对"),
        (lambda r: note_guards.strip_false_sources(r, has_materials=False),
         "无课件来源降级"),
        (lambda r: note_guards.scrub(r), "猜测句清理"),
        (lambda r: note_guards.dedupe_sentences(r), "重复句去重"),
        (lambda r: note_guards.strip_clock_times(r), "正文时间戳清理"),
        (lambda r: note_guards.drop_trivial_cues(r), "纯算术线索问题"),
        (lambda r: note_ledger.ensure_min_value(r), "最小平均成本补值"),
        (lambda r: note_guards.fix_section_refs(r), "悬空节指路"),
        (lambda r: note_guards.clean_ledger(r), "台账收尾"),
        (lambda r: note_guards.drop_dangling_pointers(r), "悬空图表指路"),
        (lambda r: note_guards.drop_asr_orphans(r, entries=entries, known=known),
         "ASR 噪声清理"),
        (lambda r: note_guards.flag_conflicts(r, entries=entries), "跨节矛盾"),
        (lambda r: note_guards.strip_clock_times(r), "正文时间戳清理（串讲）"),
        (lambda r: note_guards.scrub(r), "猜测句清理（串讲）"),
        (lambda r: note_guards.annotate_mismatches(r), "核算结果落正文"),
    )
    for fn, label in steps:
        try:
            got, extra, w = fn(out)
        except Exception as e:                                     # noqa: BLE001
            warns.append(f"⚠ {label} 回放失败：{str(e)[:80]}")
            continue
        out = got
        for x in (w or []):
            warns.append(str(x))
        # 🧮 跟写作层同一条账：每个守卫自己报的 repaired/dropped/flagged/downgraded 加起来。
        for st in (extra or []):
            if not isinstance(st, dict):
                continue
            for k in counts:
                try:
                    counts[k] += int(st.get(k) or 0)
                except (TypeError, ValueError):
                    pass
        for st in (extra or []):
            if isinstance(st, dict):
                continue
            counts["dropped"] += 1           # 守卫台账里的非 dict 小标记 = 一处删
    for label, where in LLM_STEPS:
        would.append(f"{label}（{where}）")
    return out, warns, would, counts


def replay(note_data: dict, raw: dict) -> tuple:
    """回放一次：返回 `(review, warns, would_repair, counts)`。**纯函数**（除了 deepcopy）。"""
    return guard_chain(raw, entries=note_data.get("entries") or [])


def guard_summary(counts: dict, would: list) -> str:
    """页脚那行台账 —— 按**本次回放**的数字重算。**纯函数。**"""
    line = (f"guards: 回放(replay) repaired {counts['repaired']}, "
            f"dropped {counts['dropped']}, flagged {counts['flagged']}, "
            f"downgraded {counts['downgraded']}")
    if would:
        line += f" · 需要模型未跑 {len(would)} 步"
    return line


def note_data_from(note_data: dict, review: dict, *, warns: list,
                   would: list, counts: dict) -> "note_render.NoteData":
    """把回放出来的 review 装回 `NoteData`（其余字段沿用 v3 真跑的原值）。

    ⚠️ F15-4③：`warns` 与 `guard_summary` **必须按回放结果重算**，不许沿用上一次真跑的那份 ——
       旧那份里还写着「1 处跨节对不上已在后一节原位标 ⚠：§15（missing-term：schooling）」，
       而那条标注已经被 B 修掉了；页脚说假话比页脚缺行更坏。
    """
    import dataclasses
    fields = {f.name for f in dataclasses.fields(note_render.NoteData)}
    kw = {k: v for k, v in (note_data or {}).items() if k in fields}
    # ⚠️ 守卫链之后才跑的模型层（速览 `overview_long` 等）不在 review_raw.json 里 ——
    #    回放出来的 review 里没有这些键，要从真跑的 review 补回来，否则速览整块是空的。
    review = dict(review)
    for k, v in ((note_data or {}).get("review") or {}).items():
        review.setdefault(k, v)
    kw["review"] = review
    head = ["本页由**冻结回放**重生成（守卫链重跑；速览/串讲/例题返修等模型层沿用原产物）"]
    kw["warns"] = head + list(warns)
    kw["guard_summary"] = guard_summary(counts, would)
    # 回放只跑守卫层：不装 KaTeX 资源（不留一个指不到的文件名），
    # Anki 卡那行也是上次的产物 —— 一并清掉，免得指出错文件。
    kw["html_name"] = ""
    kw["anki_name"] = ""
    return note_render.NoteData(**kw)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", required=True, help="note_data.json（v3 真跑的产物）")
    ap.add_argument("--raw", required=True, help="review_raw.json（守卫链之前的模型输出）")
    ap.add_argument("--out", required=True, help="输出目录（写 .md 与 .html）")
    ap.add_argument("--name", default="", help="输出文件名（默认沿用 note_data 里的）")
    args = ap.parse_args()

    data_path, raw_path = pathlib.Path(args.data), pathlib.Path(args.raw)
    note_data = json.loads(data_path.read_text(encoding="utf-8"))
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    review, warns, would, counts = replay(note_data, raw)
    data = note_data_from(note_data, review, warns=warns, would=would, counts=counts)

    out_dir = pathlib.Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = args.name or f"{data.date}_{data.course}"
    md = note_render.render(data)
    (out_dir / f"{stem}.md").write_text(md, encoding="utf-8")
    import note_html
    (out_dir / f"{stem}.html").write_text(note_html.note_page(data), encoding="utf-8")
    (out_dir / f"{stem}.replay.json").write_text(
        json.dumps({"warns": warns, "would_repair": would}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"✅ 回放（零 LLM）：{out_dir / stem}.md + .html")
    print(f"   守卫提醒 {len(warns)} 条；would-repair {len(would)} 步")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
