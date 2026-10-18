"""F14 ⑧⑨：跨节矛盾（同一件事在两节里被写成两个值）+ gold-set 打分器。

用例全部来自真实产物：ECS170 §14/§15（阶跃在 z=0 一处 0 一处 1）与
BUSI3243 §15/§18（男性 0.7 vs 0.3，且转录自己前后不一致）。
"""
import json
import os
import pathlib
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import note_conflict as nc          # noqa: E402
import note_guards as ng            # noqa: E402

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "notes_gold"


def _cases() -> list:
    return json.loads((FIXTURES / "detector_cases.json").read_text(encoding="utf-8"))


def _case(cid: str) -> dict:
    for c in _cases():
        if c["id"] == cid:
            return c
    raise KeyError(cid)


class PointValueConflicts(unittest.TestCase):
    def test_阶跃在z零处一处0一处1(self):
        rev = _case("B8-阶跃在z=0一处0一处1")["input"]["review"]
        got = nc.point_value_conflicts(rev)
        self.assertEqual(1, len(got), got)
        self.assertEqual("z", got[0]["var"])
        self.assertEqual([1, 2], got[0]["sections"])
        self.assertEqual({0.0, 1.0}, set(got[0]["values"]))

    def test_同口径不报(self):
        rev = _case("B8-两边同口径不报")["input"]["review"]
        self.assertEqual([], nc.point_value_conflicts(rev))

    def test_反例句不算断言(self):
        """`误以为阶跃函数在 z=0 处输出 1（课上定的是 0）` 是**反例**，不是矛盾源。"""
        got = nc._section_statements({"prose": ["误以为阶跃函数在 z=0 处输出 1（课上定的是 0）。"]})
        self.assertEqual([], got, got)

    def test_不同函数不成矛盾(self):
        """`tanh 在 z=0 输出 0` 与 `阶跃 z≥0 输出 1` 是两个函数 → 不许报。"""
        rev = {"sections": [
            {"heading": "阶跃", "terms": [{"en": "binary step function"}],
             "prose": ["阶跃函数：$z\\ge 0$ 输出 1，$z<0$ 输出 0。"]},
            {"heading": "tanh", "terms": [{"en": "tanh"}],
             "prose": ["tanh 关于原点对称，$z=0$ 输出 0。"]}]}
        self.assertEqual([], nc.point_value_conflicts(rev))


class GroupValueConflicts(unittest.TestCase):
    def test_男性0_7与0_3(self):
        c = _case("B9-男性0.7与0.3")["input"]
        got = nc.group_number_conflicts(c["review"], entries=c["entries"])
        self.assertEqual(1, len(got), got)
        self.assertEqual("男性", got[0]["label"])
        self.assertEqual([1, 2], got[0]["sections"])   # fixture 里就这两节

    def test_两组一致不报(self):
        c = _case("B9-两组数值一致不报")["input"]
        self.assertEqual([], nc.group_number_conflicts(c["review"], entries=c["entries"]))

    def test_没在转录里出现过的标签不报(self):
        """分组名是**念出来的**词 —— 没出现过就不算分组（v1 的 `值域` 假报）。"""
        rev = {"sections": [
            {"prose": ["值域是 0 到 1。"]}, {"prose": ["值域是 -1 到 1。"]}]}
        self.assertEqual([], nc.group_number_conflicts(rev, entries=[{"en": "range"}]))
        self.assertEqual(1, len(nc.group_number_conflicts(rev)),
                         "没有转录过滤时这两句就是矛盾（说明过滤闸真的在起作用）")


class SourceSelfConflict(unittest.TestCase):
    def test_两项之和安在两个组上(self):
        got = nc.source_sum_group_conflicts(_case("B9b-两项之和安在两个组上")["input"]["entries"])
        self.assertEqual(1, len(got), got)
        self.assertGreaterEqual(len(got[0]["groups"]), 2)
        self.assertTrue(all(q["quote"] for q in got[0]["quotes"]))

    def test_只提一个组不报(self):
        self.assertEqual([], nc.source_sum_group_conflicts(
            _case("B9b-只提一个组不报")["input"]["entries"]))

    def test_两组给了不同的数字之和不报(self):
        ents = [{"ts": "1", "en": "for male workers the return is 0.3 plus 0.4"},
                {"ts": "2", "en": "for female workers the return is 0.1 plus 0.2"}]
        self.assertEqual([], nc.source_sum_group_conflicts(ents))


class FlagConflictsGuard(unittest.TestCase):
    def test_标在后一节并说清两边(self):
        rev = _case("B8-阶跃在z=0一处0一处1")["input"]["review"]
        out, extra, warns = ng.flag_conflicts(rev)
        self.assertEqual(1, extra[0]["flagged"])
        later = out["sections"][1]["prose"]
        self.assertTrue(any("⚠" in p for p in later), later)
        self.assertIn("二值阶跃函数及其缺陷", " ".join(later))
        self.assertTrue(warns)

    def test_转录自己前后不一致要写出来(self):
        c = _case("B9-男性0.7与0.3")["input"]
        out, extra, warns = ng.flag_conflicts(c["review"], entries=c["entries"])
        self.assertEqual(1, extra[0]["flagged"])
        joined = " ".join(out["sections"][1]["prose"])
        self.assertIn("⚠", joined)
        self.assertIn("课上口述", joined)

    def test_没有矛盾一个字不动(self):
        rev = {"sections": [{"heading": "A", "prose": ["x"]}, {"heading": "B", "prose": ["y"]}]}
        out, extra, warns = ng.flag_conflicts(rev)
        self.assertEqual(rev, out)
        self.assertEqual([], warns)


class GoldSetScorer(unittest.TestCase):
    """`scripts/notes_gold.py` 的判据全部进单元测试 —— 漏报/误报都为 0 才算过。"""

    def test_每条判据零漏报零误报(self):
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                        + "/scripts")
        import notes_gold
        miss, fp = [], []
        for c in _cases():
            fn = notes_gold.CHECKS[c["check"]]
            fired = bool(fn(c["input"]))
            if c["must"] == "flag" and not fired:
                miss.append(c["id"])
            if c["must"] == "pass" and fired:
                fp.append(c["id"])
        self.assertEqual([], miss, f"漏报：{miss}")
        self.assertEqual([], fp, f"误报：{fp}")

    def test_例题集是完整的四讲清单(self):
        ex = json.loads((FIXTURES / "examples.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(ex), 29, "作者要求 ≥29 道（逐条算过对的）")
        courses = {e["course"] for e in ex}
        self.assertEqual({"ECON10790", "ECS170", "BUSI3243", "CHE002A"}, courses)
        for e in ex:
            self.assertTrue(e["question"] and e["answer"], e["id"])


class DummySlopeSwap(unittest.TestCase):
    """F14-2 ⑩：**同一节内**虚拟变量的"哪一组是 1"与"哪一组拿哪条斜率"对不上。

    ⚠️ 这是 F13「代数↔讲解」判据（42b9d97）漏掉的那一类：组名只出现在讲解里，
       代数里只有 `D=0/D=1` —— 对应关系要两步推理（`D=1 ⇔ 女性`、`D=1 ⇔ β₃+β₄`），
       旧的键（组名）在代数里根本不存在，所以配不上对。
    """

    REAL17 = _case("C1-虚拟变量斜率写反")["input"]["review"]

    def test_组名与斜率对不上时命中(self):
        got = nc.dummy_slope_conflicts(self.REAL17)
        self.assertEqual(1, len(got), got)
        self.assertEqual(1, got[0]["section"], "fixture 里只有这一节")
        self.assertEqual({"女性", "男性"}, set(got[0]["labels"]))
        self.assertEqual({"0": ["b3"], "1": ["b3", "b4"]}, got[0]["slopes"])

    def test_定义与讲解一致时不报(self):
        rev = _case("C1-虚拟变量斜率一致")["input"]["review"]
        self.assertEqual([], nc.dummy_slope_conflicts(rev))

    def test_标在讲的那一段_并说出课上前后不一致(self):
        c = _case("C1-虚拟变量斜率写反")["input"]
        out, extra, warns = ng.flag_conflicts(c["review"], entries=c["entries"])
        self.assertEqual(1, extra[0]["flagged"])
        joined = " ".join(out["sections"][0].get("prose") or [])
        self.assertIn("本节自己的定义与讲解对不上", joined)
        self.assertIn("课上口述", joined, "转录自己前后不一致要一起写出来")


class MissingTerm(unittest.TestCase):
    """F14-2 ⑪：例题给某个变量一个系数，而本节模型式里它只在乘积项里（没有主效应）。"""

    REAL15 = _case("C2-例题给了一个模型里没有主效应的项的系数")["input"]["review"]

    def test_只在乘积里出现就命中(self):
        got = nc.missing_term_conflicts(self.REAL15)
        self.assertEqual(1, len(got), got)
        self.assertEqual("male", got[0]["term"])
        self.assertIn("乘积", got[0]["why"])

    def test_模型里有独立项时不报(self):
        rev = _case("C2-主效应在模型里")["input"]["review"]
        self.assertEqual([], nc.missing_term_conflicts(rev))

    def test_标注落在例题那一段(self):
        c = _case("C2-例题给了一个模型里没有主效应的项的系数")["input"]
        out, extra, warns = ng.flag_conflicts(c["review"])
        self.assertEqual(1, extra[0]["flagged"])
        joined = " ".join(out["sections"][0].get("prose") or [])
        self.assertIn("例题却给它一个系数", joined)


if __name__ == "__main__":
    unittest.main()
