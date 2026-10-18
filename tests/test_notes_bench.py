#!/usr/bin/env python3
"""F11 §5：**冻结判据集** —— 每个未来轮次都必须过这一关。

    ClassLive.app/Contents/MacOS/python tests/test_notes_bench.py

两组，共 32 条（纯本地、不联网、不发请求）：
  · `MUST_BLOCK`（16）：这些输入**必须**被机械核/守卫拦下来（假阴性 = 回归）；
  · `MUST_PASS`（16）：这些输入**必须**干净通过（假阳性 = 误伤真内容）。
每个 case 是一个 `() -> bool`，返回 True = "守卫开火"（拦下/改动/丢弃）。

转录切片（真课 3–5 分钟）在 `tests/fixtures/notes_bench/*.md`，供**网络**跑量器
`scripts/notes_bench.py` 用；本文件只钉**判据**，不跑量。切片的来源与窗口写在
每个 fixture 头部的注释里。
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import note_guards as ng          # noqa: E402
import note_math as nm            # noqa: E402
import note_v2 as nv              # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "notes_bench")


# ---------------------------------------------------------------- 小工具
def _sec(**kw) -> dict:
    s = {"heading": "h", "ts": "13:16", "prose": ["讲解一句。"]}
    s.update(kw)
    return s


def _mism(rev: dict) -> bool:
    return any(r.get("status") == "mismatch" for r in nm.check_review(rev))


def _ex(prog=None, *, prompt="求最低平均成本对应的价格。", steps=None, answer="P≈346.4。",
        givens=None, title="🎙 13:16") -> dict:
    ex = {"title": title, "prompt": prompt,
          "steps": steps if steps is not None else [{"text": "代入得 346.4。", "why": ""}],
          "answer": answer}
    if prog is not None:
        ex["program"] = prog
    if givens is not None:
        ex["givens"] = givens
    return ex


_ARG = {"var": "q_star", "op": "argmin", "expr": "60*q + 500/q", "x": "q", "range": [1, 10]}
_EV = {"var": "P", "op": "eval", "expr": "60*q + 500/q", "x": "q", "at": "q_star"}


# ---------------------------------------------------------------- MUST_BLOCK
def b_limit_as_min():
    return nm._limit_extremum_row("当 $q\\to\\infty$ 时 $AC\\to 80$，故最低成本为 80。") is not None


def b_limit_extremum_word():
    return nm._limit_extremum_row("q 趋于无穷时，最低点为 100。") is not None


def b_program_monotonic():
    _v, err = nm.run_program([dict(_ARG, expr="(60*q + 500)/q"), dict(_EV, expr="(60*q + 500)/q")])
    return "端点" in err


def b_program_answer_intermediate():
    rev = {"sections": [_sec(example=_ex([_ARG, _EV], answer="停产价格为 2.89。"))]}
    return _mism(rev)


def b_program_wild_number():
    rev = {"sections": [_sec(example=_ex([_ARG, _EV],
                                         steps=[{"text": "代入得 999。", "why": ""}]))]}
    return _mism(rev)


def b_faded_new_number():
    sec = _sec(example=_ex(prompt="$TC(q)=q^{3}-12q^{2}+60q$", steps=[], answer="$AC=q^2-12q+60$。"),
               faded={"q": "补全：$TC(q)=q^{3}-12q^{2}+100q$", "blanks": ["x"], "a": "y"})
    return bool(nm.reuse_rows(sec))


def b_q_as_p():
    rev = {"sections": [_sec(prose=["在 $q\\approx2.89$ 处取最低。"],
                             example=_ex(None, prompt="课上给出 $q\\approx2.89$ 处取最小，求答案。",
                                         steps=[{"text": "答案 $P\\approx2.89$。", "why": ""}],
                                         answer=""))]}
    return _mism(rev)


def b_value_as_point():
    rev = {"sections": [_sec(example=_ex(None, prompt="求最低平均成本 $AC(2.89)$ 的值。",
                                         steps=[{"text": "$AC(2.89)\\approx2.89$。", "why": ""}],
                                         answer=""))]}
    return _mism(rev)


def b_self_talk():
    rev = {"sections": [_sec(example=_ex(None, steps=[{"text": "先代进去。", "why": ""}],
                                         answer="（打扫，不打扫）是纳什均衡？这不对，因为…"))]}
    out, removed, _ = ng.scrub(rev)
    return bool(removed)


def b_first_person_guess():
    _out, removed, _ = ng.scrub({"sections": [_sec(prose=["说明我可能听错了公式。"])]})
    return bool(removed)


def b_trivial_cue():
    _out, dropped, _ = ng.drop_trivial_cues({"sections": [_sec(cues=["5.3除以3等于多少？"])]})
    return bool(dropped)


def b_dupe_sentence():
    s = ("具体来说，原题见 📖 期末卷。⚠ 课上只给了片段：原题见 📖 期末卷 / 未核对。"
         "⚠ 课上只给了片段：原题见 📖 期末卷 / 未核对。")
    _out, gone, _ = ng.dedupe_sentences({"sections": [_sec(prose=[s])]})
    return bool(gone)


def b_sign_contradiction():
    rev = {"sections": [_sec(example=_ex(None, steps=[
        {"text": "二阶导数为正，故该点为极大值点。", "why": ""}], answer=""))]}
    return _mism(rev)


def b_flow_repeats_example():
    ex = _ex(None, steps=[{"text": "写出两个半反应", "why": ""},
                          {"text": "配平氧原子", "why": ""},
                          {"text": "配平氢原子", "why": ""}])
    fl = {"title": "半反应配平", "steps": [{"label": "写出两个半反应"},
                                          {"label": "配平氧原子"},
                                          {"label": "配平氢原子"},
                                          {"label": "配平电荷"}]}
    out, dropped, _ = nv.dedupe_components({"sections": [_sec(example=ex, flow=fl)]})
    return bool(dropped) and "flow" not in out["sections"][0]


def b_same_title_component():
    c = {"title": "三种成本指标", "cols": ["", "A", "B"],
         "rows": [{"label": "x", "cells": ["1", "2"]}, {"label": "y", "cells": ["3", "4"]}]}
    rev = {"sections": [_sec(compare=dict(c)), _sec(compare=dict(c))]}
    out, dropped, _ = nv.dedupe_components(rev)
    return bool(dropped) and "compare" not in out["sections"][1]


def b_unsupported_numeric_claim():
    def ask(state, instructions):
        return [0.02] * len(instructions)
    rev = {"sections": [_sec(prose=["课上算出 AC(2.89)≈2.89。"])]}
    out, _extra, _ = ng.support_check(rev, entries=[{"ts": "13:16:00", "en": "x"}], ask=ask)
    return any(r.get("status") == "mismatch" for r in (out.get("checks") or []))


def b_conserve_bad_chem():
    """⭐ F12 §1：作者点名的坏方程式（H₂O 两边都有、e⁻ 放错边）必须拦。"""
    rev = {"sections": [_sec(example=_ex(answer="8H⁺ + 4H₂O + 8e⁻ → 4H₂O"))]}
    return _mism(rev)


def b_conserve_unbalanced():
    """⭐ F12 §1：元素不守恒（H₂ + O₂ → H₂O）必须拦。"""
    rev = {"sections": [_sec(example=_ex(answer="$H_2 + O_2 \\to H_2O$"))]}
    return _mism(rev)


def b_conserve_charge_unbalanced():
    """⭐ F12 §1：元素平了但**电荷**不平（Fe²⁺ → Fe³⁺ 少写电子）必须拦。"""
    rev = {"sections": [_sec(example=_ex(answer="Fe²⁺ → Fe³⁺"))]}
    return _mism(rev)


def b_conserve_dupe_species():
    """⭐ F12 §1：元素/电荷都平了，但**同一物种两边都出现**（没化简）必须拦。"""
    rev = {"sections": [_sec(example=_ex(answer="CO₂ + H₂O → H₂O + CO₂"))]}
    return _mism(rev)


def b_verdict_forced_fragment():
    """⭐ F12 §2：complete 但结论数字不在 quote、也无 program → 强制 fragment。"""
    ex = _ex(answer="P≈346.4。")
    ex["source_check"] = {"quote": "求最低平均成本。", "verdict": "complete"}
    rev = {"sections": [_sec(example=ex)]}
    sec = rev["sections"][0]
    sec["example"]["source_check"] = nm.enforce_source_verdict(dict(sec["example"]))
    return sec["example"]["source_check"].get("verdict") == "fragment"


# ---------------------------------------------------------------- MUST_PASS
def p_sum_chain_no_false_div():
    rev = {"sections": [_sec(prose=[
        "代入具体函数：AC(q)=60q+500/q，在q=2.89处，AC=60×2.89+500/2.89≈173.4+173.0=346.4。"])]}
    return _mism(rev)


def p_chemistry_imperative_not_collected():
    """祈使话（「配平」）本身永不进 claim；化学物种（MnO4-/Mn2+）是**具体给定**，收。"""
    got = ng._prompt_givens("配平 MnO4- → Mn2+（酸性溶液）")
    return any("配平" in g for g in got)


def p_program_ok():
    rev = {"sections": [_sec(example=_ex([_ARG, _EV]))]}
    return _mism(rev)


def p_chained_fraction_ok():
    rev = {"sections": [_sec(prose=["$AC'(q)=60-500/q^2=0$ 得 $q^2=500/60=25/3$。"])]}
    return _mism(rev)


def p_rounding_stationary_ok():
    rev = {"sections": [_sec(prose=["$AC(q)=60q+500/q$ 在 $q\\approx2.89$ 处取最小值。"])]}
    return _mism(rev)


def p_min_point_not_flagged():
    rev = {"sections": [_sec(prose=["令 $f'(x)=0$ 得 $x=6$，最低点为6。"],
                             example=_ex(None, prompt="已知 $f(x)=x^{2}-12x$，求最低点。",
                                         steps=[{"text": "$f'(x)=2x-12=0$ 得 $x=6$。", "why": ""}],
                                         answer=""))]}
    return _mism(rev)


def p_chinese_habit_not_scrubbed():
    _out, removed, _ = ng.scrub({"sections": [_sec(
        prose=["考试时可能需要先求导再代入。", "我们来看这一节的图。"])]})
    return bool(removed)


def p_clean_section_no_repair():
    calls: list = []

    def chat(*a):
        calls.append(1)
        return {}
    clean = {"heading": "[13:40] 干净", "ts": "13:40", "prose": ["这一节没有数字断言。"],
             "example": {"title": "📖 无", "prompt": "看看就好", "steps": [], "answer": "好"}}
    nv.repair_exercises({"sections": [clean]}, chat=chat)
    return bool(calls)


def p_valid_compare_kept():
    r = nv._clean_section({"heading": "h", "compare": {
        "title": "三种成本指标", "src": "lecture", "cols": ["", "AC", "MC"],
        "rows": [{"label": "定义", "cells": ["$TC/q$", "$dTC/dq$"]},
                 {"label": "最低点", "cells": ["与 MC 相等", "穿过 AC 最低点"]}]}})
    return "compare" not in r


def p_valid_flow_kept():
    r = nv._clean_section({"heading": "h", "flow": {
        "title": "梯度下降一轮", "src": "lecture",
        "steps": [{"label": "前向传播"}, {"label": "反向传播"}, {"label": "更新参数"}]}})
    return "flow" not in r


def p_single_label_untouched():
    return nv._fold_labels("🎙 13:16 停产价格") != "🎙 13:16 停产价格"


def p_non_numeric_downgrade_not_warned():
    def ask(state, instructions):
        return [0.02] * len(instructions)
    rev = {"sections": [_sec(prose=["老师在课上强调最低点处 $MC=AC$。"])]}
    out, _e, _ = ng.support_check(rev, entries=[{"ts": "13:16:00", "en": "x"}], ask=ask)
    return any(r.get("status") == "mismatch" for r in (out.get("checks") or []))


def p_min_at_six_ok():
    rev = {"sections": [_sec(prose=["$AC(q)=q^{2}-12q+60$ 在 $q=6$ 处取最小值。"])]}
    return _mism(rev)


def p_sign_correct_ok():
    rev = {"sections": [_sec(example=_ex(None, steps=[
        {"text": "二阶导数为负，故该点为极大值点。", "why": ""}], answer=""))]}
    return _mism(rev)


def p_symbol_claim_retrieves():
    class M:
        def __init__(self):
            c = type("C", (), {})()
            c.text = "At \U0001D45E = 4 we have \U0001D434\U0001D436 = 28 and \U0001D440\U0001D436 = 12."
            self._chunks = [c]

        def search(self, q, k=3, **kw):
            return []
    return not ng._materials_excerpts(M(), "$MC=12$")


def p_no_program_skip():
    return bool(nm.program_rows(_sec(example=_ex())))     # 会返回 [] → 干净


def p_conserve_mno4_ok():
    """⭐ F12 §1：标准酸性半反应配平，必须过。"""
    rev = {"sections": [_sec(example=_ex(answer="MnO4⁻ + 8H⁺ + 5e⁻ → Mn²⁺ + 4H₂O"))]}
    return _mism(rev)


def p_conserve_fe_ok():
    """⭐ F12 §1：`Fe²⁺ → Fe³⁺ + e⁻`，必须过。"""
    rev = {"sections": [_sec(example=_ex(answer="Fe²⁺ → Fe³⁺ + e⁻"))]}
    return _mism(rev)


def p_conserve_nonchem_arrow_ok():
    """⭐ F12 §1：非化学的箭头串（极限/取值）不许被当方程式。"""
    rev = {"sections": [_sec(prose=["当 $q\\to\\infty$ 时 AC→80。"],
                             example=_ex(answer="AC → 80"))]}
    return _mism(rev)


def p_verdict_supported_stays_complete():
    """⭐ F12 §2：数字都在 quote 里（含数词）→ 仍 complete。"""
    ex = _ex(prompt="求电子数。", answer="需要 40 个电子。", steps=[])
    ex["source_check"] = {"quote": "we need forty electrons.", "verdict": "complete"}
    out = nm.enforce_source_verdict(ex)
    return str(out.get("verdict")) != "complete"


MUST_BLOCK = [b_limit_as_min, b_limit_extremum_word, b_program_monotonic,
              b_program_answer_intermediate, b_program_wild_number, b_faded_new_number,
              b_q_as_p, b_value_as_point, b_self_talk, b_first_person_guess,
              b_trivial_cue, b_dupe_sentence, b_sign_contradiction,
              b_flow_repeats_example, b_same_title_component, b_unsupported_numeric_claim,
              b_conserve_bad_chem, b_conserve_unbalanced, b_conserve_charge_unbalanced,
              b_conserve_dupe_species, b_verdict_forced_fragment]

MUST_PASS = [p_sum_chain_no_false_div, p_chemistry_imperative_not_collected, p_program_ok,
             p_chained_fraction_ok, p_rounding_stationary_ok, p_min_point_not_flagged,
             p_chinese_habit_not_scrubbed, p_clean_section_no_repair, p_valid_compare_kept,
             p_valid_flow_kept, p_single_label_untouched, p_non_numeric_downgrade_not_warned,
             p_min_at_six_ok, p_sign_correct_ok, p_symbol_claim_retrieves, p_no_program_skip,
             p_conserve_mno4_ok, p_conserve_fe_ok, p_conserve_nonchem_arrow_ok,
             p_verdict_supported_stays_complete]


class FrozenBench(unittest.TestCase):
    def test_必拦全部(self):
        for f in MUST_BLOCK:
            with self.subTest(case=f.__name__):
                self.assertTrue(f(), f"{f.__name__} 没被拦（假阴性 = 回归）")

    def test_必过全部(self):
        for f in MUST_PASS:
            with self.subTest(case=f.__name__):
                self.assertFalse(f(), f"{f.__name__} 被误伤（假阳性）")

    def test_切片清单齐全(self):
        import glob
        files = sorted(os.path.basename(p) for p in glob.glob(os.path.join(FIX, "*.md")))
        self.assertGreaterEqual(len(files), 12, f"切片不足 12：{files}")
        for name in ("econ10790-1315.md", "soc10020-dublin.md", "che002a-stoich.md",
                     "che002a-redox.md", "ecs170-grad.md", "admin-none.md"):
            self.assertIn(name, files, f"缺切片 {name}")

    def test_切片是真转录(self):
        with open(os.path.join(FIX, "soc10020-dublin.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("[!abstract]", text, "切片里没有转录块")
        self.assertIn("Dublin", text, "都柏林那段的原话不在切片里")


if __name__ == "__main__":
    unittest.main(verbosity=2)
