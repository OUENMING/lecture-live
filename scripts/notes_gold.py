#!/usr/bin/env python3
"""F14 gold-set 打分器（**离线、不联网**）。

用法：
    python3 scripts/notes_gold.py            # 全部判据，逐条列
    python3 scripts/notes_gold.py -v         # 连没命中的用例也列出来

读 `tests/fixtures/notes_gold/detector_cases.json`：每条用例 = 一个判据 + 一段输入 +
`must`（`flag` = 必须命中；`pass` = 必须不命中）。逐判据报：
  · **漏报率**（false-pass）= must=flag 却没命中 / must=flag 总数  ← 判据失灵
  · **误报率**（false-flag）= must=pass 却命中 / must=pass 总数    ← 判据太凶
两个都为 0 才算这条判据过关；任一非 0 → 退出码 1（可当提交门禁）。

⚠️ 只跑本仓库的纯函数判据；需要网络的"终读判官"在 `scripts/notes_judge.py`。
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import note_conflict as nc          # noqa: E402
import note_guards as ng            # noqa: E402
import note_terms as nt             # noqa: E402
import note_v2 as nv                # noqa: E402

CASES = ROOT / "tests" / "fixtures" / "notes_gold" / "detector_cases.json"

_SRC = {"术语库": nt.SRC_CURATED, "自动缓存": nt.SRC_AUTO, "本课": nt.SRC_LECTURE}


def a2_cross_course_term(d: dict) -> bool:
    """跨课术语库条目有没有被挡下（有本课证据才放行）。"""
    return bool(nt.drop_reason(d["term"], source=_SRC[d["source"]],
                               verified=d.get("verified", False),
                               lecture_term=d.get("lecture_term", False)))


def _annotated(d: dict) -> str:
    out, _, _ = ng.annotate_mismatches(d)
    return " ".join(str(p) for s in (out.get("sections") or [])
                    for p in (s.get("prose") or []))


def a3_quote_label(d: dict) -> bool:
    """引语没找到 → 必须用「没找到原话」这句标签（不是"数字对不上"）。"""
    got = _annotated(d)
    return "没找到这句原话" in got


def a3_numeric_label(d: dict) -> bool:
    """数值对不上 → 必须用「数字对不上」这句标签。"""
    return "数字对不上" in _annotated(d)


def a4_section_refs(d: dict) -> bool:
    """悬空节指路有没有被改掉。"""
    out, extra, _ = ng.fix_section_refs(d)
    e = extra[0] if extra else {}
    return bool(int(e.get("repaired") or 0) + int(e.get("dropped") or 0))


def a5_false_sources(d: dict) -> bool:
    """没课件时「课件/PPT」来源声称有没有被降级。"""
    _, extra, _ = ng.strip_false_sources(d["review"], has_materials=d["has_materials"])
    return bool(extra and int(extra[0].get("downgraded") or 0))


def a6_asr_orphans(d: dict) -> bool:
    """只活在转录噪声里的常数/编号有没有被删。"""
    known = set(d.get("known") or [])
    _, extra, _ = ng.drop_asr_orphans(d["review"], entries=d.get("entries") or [],
                                      known=lambda t: t in known)
    return bool(extra and int(extra[0].get("dropped") or 0))


def a7_shorten_floor(d: dict) -> bool:
    """压缩结果有没有被可读性地板挡下。"""
    return bool(nv.too_compressed(d["lines"], d["prose"]))


def b8_point_value(d: dict) -> bool:
    return bool(nc.point_value_conflicts(d["review"]))


def b9_group_value(d: dict) -> bool:
    return bool(nc.group_number_conflicts(d["review"], entries=d.get("entries")))


def b9b_source_conflict(d: dict) -> bool:
    return bool(nc.source_sum_group_conflicts(d["entries"]))


CHECKS = {
    "a2_cross_course_term": a2_cross_course_term,
    "a3_quote_label": a3_quote_label,
    "a3_numeric_label": a3_numeric_label,
    "a4_section_refs": a4_section_refs,
    "a5_false_sources": a5_false_sources,
    "a6_asr_orphans": a6_asr_orphans,
    "a7_shorten_floor": a7_shorten_floor,
    "b8_point_value": b8_point_value,
    "b9_group_value": b9_group_value,
    "b9b_source_conflict": b9b_source_conflict,
}


def main(argv: list) -> int:
    verbose = "-v" in argv
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    stats: dict = {}
    bad: list = []
    for c in cases:
        fn = CHECKS.get(c["check"])
        if fn is None:
            bad.append(f"未知判据：{c['check']}（{c['id']}）")
            continue
        try:
            fired = bool(fn(c["input"]))
        except Exception as e:                                  # noqa: BLE001
            bad.append(f"{c['id']}：判据抛异常 {type(e).__name__}: {e}")
            continue
        st = stats.setdefault(c["check"], {"flag": 0, "flag_fired": 0,
                                          "pass": 0, "pass_fired": 0, "miss": [], "fp": []})
        if c["must"] == "flag":
            st["flag"] += 1
            st["flag_fired"] += int(fired)
            if not fired:
                st["miss"].append(c["id"])
                bad.append(f"漏报：{c['id']}（{c['why']}）")
        else:
            st["pass"] += 1
            st["pass_fired"] += int(fired)
            if fired:
                st["fp"].append(c["id"])
                bad.append(f"误报：{c['id']}（{c['why']}）")
    print(f"{'判据':<24}{'漏报':>10}{'误报':>10}   用例")
    tot_miss = tot_fp = tot_flag = tot_pass = 0
    for name, st in stats.items():
        miss = (st["flag"] - st["flag_fired"]) / st["flag"] if st["flag"] else 0.0
        fp = st["pass_fired"] / st["pass"] if st["pass"] else 0.0
        tot_miss += st["flag"] - st["flag_fired"]
        tot_fp += st["pass_fired"]
        tot_flag += st["flag"]
        tot_pass += st["pass"]
        print(f"{name:<24}{miss:>9.0%}{fp:>10.0%}   "
              f"must_flag {st['flag_fired']}/{st['flag']} · must_pass "
              f"{st['pass'] - st['pass_fired']}/{st['pass']}")
        if verbose:
            for x in st["miss"]:
                print(f"      漏报 {x}")
            for x in st["fp"]:
                print(f"      误报 {x}")
    print("-" * 60)
    print(f"合计：must_flag {tot_flag - tot_miss}/{tot_flag}（漏报 {tot_miss}）· "
          f"must_pass {tot_pass - tot_fp}/{tot_pass}（误报 {tot_fp}）· "
          f"用例 {len(cases)}")
    if bad:
        print("\n未通过：")
        for b in bad:
            print("  ✗", b)
        return 1
    print("✅ 全部判据：漏报 0、误报 0")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
