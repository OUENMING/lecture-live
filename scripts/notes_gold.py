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
import re
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


def c1_dummy_slope(d: dict) -> bool:
    """同一节里"哪一组是 1"与"哪一组拿哪条斜率"对不上。"""
    return bool(nc.dummy_slope_conflicts(d["review"]))


def c2_missing_term(d: dict) -> bool:
    """例题给某个变量一个系数，而本节模型式里它只在乘积项里。"""
    return bool(nc.missing_term_conflicts(d["review"]))


def c3_scrub_connective(d: dict) -> bool:
    """删句会让段落以连接词开头 → 是不是把原句留住了。"""
    _, _, warns = ng.scrub(d["review"])
    return any("保留了原句" in w for w in warns)


def c4_component_retime(d: dict) -> bool:
    """挂错节的组件是不是被挪走了。"""
    _, extra, _ = ng.retime_components(d["review"])
    return bool(extra and int(extra[0].get("repaired") or 0))


def c5_term_gloss(d: dict) -> bool:
    """括注抑制有没有生效（原文几个字之后已有同一个英文括注 → 不再叠第二个）。"""
    out = nv.announce_terms(d["text"], [tuple(x) for x in d["terms"]], set())
    return out == d["text"]


# ---- F15 新增判据（作者 round 3 的 A–H）-----------------------------------
def a8_scrub_keeps_factual(d: dict) -> bool:
    """**事实句不许被猜测句判据删掉**（True = 一句都没删）。"""
    _out, removed, _w = ng.scrub(d["review"])
    return not removed


def a9_scrub_cuts_self_talk(d: dict) -> bool:
    """**真自言自语要删掉**（True = 删了）。"""
    _out, removed, _w = ng.scrub(d["review"])
    return bool(removed)


def a10_solution_floor(d: dict) -> bool:
    """答案只剩一句结论 → 保留原文（True = 答案没被切短）。"""
    out, _removed, _w = ng.scrub(d["review"])
    a = (out.get("qa") or [{}])[0].get("a") or ""
    return a == (d["review"].get("qa") or [{}])[0].get("a")


def c6_missing_term_gate(d: dict) -> bool:
    """缺项判据**没**报（True = 不报；用于"没有模型式/不是乘积项"的 must-pass）。"""
    return not nc.missing_term_conflicts(d["review"])


def d3_symbol_sign(d: dict) -> bool:
    """同节同一个左端、代入本节定义后对不上（F15-E）。"""
    return bool(nc.symbol_sign_conflicts(d["review"]))


def d4_sign_flip(d: dict) -> bool:
    """跨节同一个符号一会减一会加（F15-F）。"""
    return bool(nc.sign_flip_conflicts(d["review"]))


def d5_dangling_pointer(d: dict) -> bool:
    """指着本节没有的图表的句子被删掉了（F15-G）。"""
    _out, extra, _w = ng.drop_dangling_pointers(d["review"])
    return bool(extra and int(extra[0].get("dropped") or 0))


def d6_empty_ledger(d: dict) -> bool:
    """没有修正值的账本行被丢掉了（F15-D）。"""
    _out, extra, _w = ng.clean_ledger(d["review"])
    return bool(extra and int(extra[0].get("dropped") or 0))


def d7_ledger_pointer(d: dict) -> bool:
    """账本/复习安排里的「第 N 节」指路被收窄了（F15-H）。"""
    _out, extra, _w = ng.clean_ledger(d["review"])
    return bool(extra and int(extra[0].get("repaired") or 0))


def d8_dup_gloss(d: dict) -> bool:
    """同一个括注 12 字内叠了两次 → 被收成一个（F15 §3）。"""
    return ng.collapse_dup_gloss(d["text"]) != d["text"]


# ---- F15 round 4 的四处打磨（标注文案/页脚/引语截断）------------------------
def _flag_marks(d: dict) -> list:
    """跑一遍跨节矛盾 → 收集正文里所有 `（⚠…）` 标注。"""
    out, _extra, _w = ng.flag_conflicts(d["review"], entries=d.get("entries"))
    marks: list = []
    for s in out.get("sections") or []:
        for path in ng.paths_of(s):
            v = ng._get(s, path)
            if isinstance(v, str):
                marks.extend(re.findall(r"（⚠[^）]*）", v))
    return marks


def e1_mark_no_bare_latex(d: dict) -> bool:
    """⚠ 文案里**没有裸的/截断的 LaTeX**：`$…$` 之外不许有反斜杠，`$…$` 之内不许有 `…`。"""
    marks = _flag_marks(d)
    if not marks:
        return False
    for m in marks:
        if "\\" in re.sub(r"\$[^$]*\$", "", m):
            return False
        for span in re.findall(r"\$[^$]*\$", m):
            if "…" in span:
                return False
    return True


def e2_symbol_sign_message(d: dict) -> bool:
    """符号定义差号那条 ⚠ **在**、而且把"应为/写成"说清（按本节定义代入）。"""
    marks = [m for m in _flag_marks(d) if "差一个负号" in m]
    if not marks:
        return False
    m = marks[0]
    return ("按本节的 $" in m and "应为 $" in m and "正文写成 $" in m and "g_j" in m)


def f1_flip_wording(d: dict) -> bool:
    """正负号文案说清"为什么两种写法都对"（偏置 = −阈值），不用"是另一回事"糊过去。"""
    marks = [m for m in _flag_marks(d) if "当阈值" in m]
    if not marks:
        return False
    return all("偏置 = −阈值" in m and "两种写法符号相反" in m and "是另一回事" not in m
               for m in marks)


def c7_quote_word_ok(d: dict) -> bool:
    """转录引语的截断落在**词边界**上（不把拉丁词从中间切开）。"""
    marks = _flag_marks(d)
    src = " ".join(str(e.get("en") or e.get("asr") or "") for e in (d.get("entries") or []))
    seen = False
    for m in marks:
        for q in re.findall(r"「([^」]*)…」", m):
            seen = True
            if re.search(r"[A-Za-z0-9]$", q) and not re.search(
                    re.escape(q) + r"(?![A-Za-z0-9])", src):
                return False
    return seen


def r1_replay_footer(d: dict) -> bool:
    """回放的页脚/台账按**本次回放**重算（不沿用上一次真跑那几行）。"""
    import notes_replay
    nd, raw = d["note_data"], d["raw"]
    rev, warns, would, counts = notes_replay.replay(nd, raw)
    data = notes_replay.note_data_from(nd, rev, warns=warns, would=would, counts=counts)
    blob = "\n".join(data.warns)
    if any(w and w in blob for w in (nd.get("warns") or [])):
        return False                              # 旧那几行还在 → 没重算
    if (nd.get("guard_summary") or "") and nd["guard_summary"] in data.guard_summary:
        return False
    return data.guard_summary.startswith("guards: 回放(replay)")


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
    "c1_dummy_slope": c1_dummy_slope,
    "c2_missing_term": c2_missing_term,
    "c3_scrub_connective": c3_scrub_connective,
    "c4_component_retime": c4_component_retime,
    "c5_term_gloss": c5_term_gloss,
    "a8_scrub_keeps_factual": a8_scrub_keeps_factual,
    "a9_scrub_cuts_self_talk": a9_scrub_cuts_self_talk,
    "a10_solution_floor": a10_solution_floor,
    "c6_missing_term_gate": c6_missing_term_gate,
    "d3_symbol_sign": d3_symbol_sign,
    "d4_sign_flip": d4_sign_flip,
    "d5_dangling_pointer": d5_dangling_pointer,
    "d6_empty_ledger": d6_empty_ledger,
    "d7_ledger_pointer": d7_ledger_pointer,
    "d8_dup_gloss": d8_dup_gloss,
    "e1_mark_no_bare_latex": e1_mark_no_bare_latex,
    "e2_symbol_sign_message": e2_symbol_sign_message,
    "f1_flip_wording": f1_flip_wording,
    "c7_quote_word_ok": c7_quote_word_ok,
    "r1_replay_footer": r1_replay_footer,
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
