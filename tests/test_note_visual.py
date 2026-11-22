"""跨学科图元 / 图该不该画（Jev 闸）/ 术语信号 + 先认识 / 想一想（2026-11-22）。纯函数，零网络。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import note_html  # noqa: E402
import note_render  # noqa: E402
import note_v2  # noqa: E402
import note_vgate  # noqa: E402

TL = {"kind": "timeline", "title": "职业经历", "src": "lecture",
      "events": [{"when": "2010", "label": "读博"}, {"when": "2015", "label": "去央行", "detail": "做预测"},
                 {"when": "2020", "label": "到 ESRI"}]}
CA = {"kind": "causal", "title": "公地悲剧", "src": "lecture",
      "chain": [{"label": "船变多", "dir": "up"}, {"label": "每船捕获", "dir": "down"},
                {"label": "总收益", "dir": "↓"}], "links": ["拥挤"]}
MX = {"kind": "matrix", "title": "四类物品",
      "x": {"name": "排他性", "lo": "无", "hi": "有"}, "y": {"name": "竞争性", "lo": "无", "hi": "有"},
      "cells": [{"x": "hi", "y": "hi", "label": "私人物品"}, {"x": "lo", "y": "hi", "label": "共有资源"},
                {"x": "hi", "y": "lo", "label": "俱乐部物品"}, {"x": "low", "y": "no", "label": "公共物品", "detail": "国防"}]}
TR = {"kind": "tree", "title": "研究方法", "root": "研究方法",
      "children": [{"label": "定量", "children": ["问卷", "实验"]}, {"label": "定性", "detail": "访谈为主"}]}


class 清洗(unittest.TestCase):
    def test_四种都收(self):
        for dg in (TL, CA, MX, TR):
            self.assertTrue(note_v2._clean_diagram(dg), dg["kind"])

    def test_因果链_方向归一_links补齐(self):
        out = note_v2._clean_diagram(CA)
        self.assertEqual([c["dir"] for c in out["chain"]], ["up", "down", "down"])
        self.assertEqual(out["links"], ["拥挤", ""])

    def test_分类格_四格按固定顺序_缺一格就丢(self):
        out = note_v2._clean_diagram(MX)
        self.assertEqual([c["label"] for c in out["cells"]], ["共有资源", "私人物品", "公共物品", "俱乐部物品"])
        bad = dict(MX, cells=MX["cells"][:3])
        self.assertIsNone(note_v2._clean_diagram(bad))

    def test_不合规的丢(self):
        self.assertIsNone(note_v2._clean_diagram({"kind": "venn", "title": "x"}))
        self.assertIsNone(note_v2._clean_diagram(dict(TL, events=TL["events"][:2])))
        self.assertIsNone(note_v2._clean_diagram(dict(TR, children=TR["children"][:1])))
        self.assertIsNone(note_v2._clean_diagram(dict(CA, title="")))

    def test_标题挂来源标签(self):
        self.assertTrue(note_v2._clean_diagram(TL)["title"].startswith("🎙"))
        self.assertTrue(note_v2._clean_diagram(MX)["title"].startswith("✏️"))

    def test_why(self):
        self.assertEqual(note_v2._clean_why({"q": "为什么会搭便车", "a": "因为不能排他。"})["q"], "为什么会搭便车？")
        self.assertIsNone(note_v2._clean_why({"q": "为什么？", "a": ""}))

    def test_clean_section_带上_空的摘掉(self):
        sec = note_v2._clean_section({"heading": "甲", "prose": ["x"], "diagram": TL,
                                      "why": {"q": "为什么？", "a": "因为。"}})
        self.assertEqual(sec["diagram"]["kind"], "timeline")
        self.assertIn("why", sec)
        sec2 = note_v2._clean_section({"heading": "乙", "prose": ["x"], "diagram": {"kind": "x"}})
        self.assertNotIn("diagram", sec2)
        self.assertNotIn("why", sec2)

    def test_全讲上限4(self):
        rv = {"sections": [{"heading": str(i), "diagram": note_v2._clean_diagram(TL)} for i in range(6)]}
        out, dropped, _ = note_v2.clamp_components(rv)
        self.assertEqual(sum(1 for s in out["sections"] if s.get("diagram")), 4)
        self.assertEqual(len(dropped), 2)

    def test_合并单元带上后面成员的图(self):
        rv = {"sections": [{"heading": "甲：一", "prose": ["a"], "ts": "13:00:00"},
                           {"heading": "甲：二", "prose": ["b"], "ts": "13:02:00",
                            "diagram": note_v2._clean_diagram(TR)}]}
        out = note_v2.merge_units(rv, end_ts="13:10:00")
        self.assertEqual(out["sections"][0]["diagram"]["kind"], "tree")

    def test_提示词开关(self):
        self.assertIn('"kind": "timeline"', note_v2.VISUAL_BLOCK)
        os.environ["CLASSLIVE_NOTE_VISUAL"] = "0"
        try:
            self.assertFalse(note_v2._visual_enabled())
        finally:
            os.environ.pop("CLASSLIVE_NOTE_VISUAL", None)
        self.assertTrue(note_v2._visual_enabled())


class Jev闸(unittest.TestCase):
    def _rv(self):
        return {"sections": [{"heading": "甲", "prose": ["讲定义。"], "diagram": dict(TL),
                              "flow": {"title": "步骤", "steps": []}},
                             {"heading": "乙", "prose": ["y"], "diagram": dict(MX)}]}

    def test_低于阈值撤_拿不到保留(self):
        asked = []

        def ask(state, qs):
            asked.append(qs)
            return [0.9, 0.1] if len(qs) == 2 else [None]
        out, dropped, warns = note_vgate.gate(self._rv(), ask=ask)
        self.assertIn("flow", out["sections"][0])
        self.assertNotIn("diagram", out["sections"][0])
        self.assertIn("diagram", out["sections"][1], "None = 保留")
        self.assertEqual(len(dropped), 1)
        self.assertEqual(asked[0], [note_vgate.QUESTIONS["steps"], note_vgate.QUESTIONS["timeline"]])
        self.assertTrue(warns)

    def test_全挂_全保留且出声(self):
        def boom(state, qs):
            raise RuntimeError("401")
        rv = self._rv()
        out, dropped, warns = note_vgate.gate(rv, ask=boom)
        self.assertEqual(dropped, [])
        self.assertIn("diagram", out["sections"][0])
        self.assertTrue(any("一条概率都没拿到" in w for w in warns))
        self.assertIn("diagram", rv["sections"][0], "不许原地改")

    def test_没图不问(self):
        called = []
        note_vgate.gate({"sections": [{"heading": "x", "prose": ["y"]}]},
                        ask=lambda s, q: called.append(1) or [])
        self.assertEqual(called, [])


class 渲染(unittest.TestCase):
    def test_四种HTML(self):
        for dg, needle in ((TL, 'class="tl"'), (CA, 'class="chain"'), (MX, 'class="mx"'), (TR, 'class="tree"')):
            h = note_html.diagram_html(note_v2._clean_diagram(dg))
            self.assertIn(needle, h)
        h = note_html.diagram_html(note_v2._clean_diagram(CA))
        self.assertEqual(h.count('class="cn"'), 3)
        self.assertEqual(h.count('class="cl"'), 2)
        self.assertIn("拥挤", h)
        self.assertIn('class="dir down"', h)

    def test_节里有图_想一想_先认识(self):
        sec = {"heading": "公地", "q": "为什么会过度捕捞？",
               "prose": ["共有资源（common resource）会被过度使用，因为没人能被排除。", "所以 common resource 要管。"],
               "terms": [{"zh": "共有资源", "en": "common resource"}],
               "diagram": note_v2._clean_diagram(CA), "why": {"q": "为什么会过度？", "a": "每条船只看平均。"}}
        h = note_html._section_html(sec, 2, term_zhs=["共有资源"])
        self.assertIn('<span class="tm" id="kp2t1">共有资源</span>', h)
        self.assertIn('<a href="#kp2t1">共有资源</a>', h)
        self.assertLess(h.index("newterms"), h.index("讲解"), "先认识放在讲解前")
        self.assertIn("因果链", h)
        self.assertIn('<details class="why">', h)

    def test_没有新词就没有先认识(self):
        h = note_html._section_html({"heading": "x", "prose": ["没有括注。"]}, 1, term_zhs=["共有资源"])
        self.assertNotIn("newterms", h)

    def test_长词优先(self):
        found = []
        out = note_html._mark_terms("边际成本（marginal cost）", ["边际成本", "成本"], 1, found)
        self.assertIn(">边际成本</span>", out)
        self.assertEqual(found, [("边际成本", "marginal cost")])

    def test_md_四种(self):
        self.assertTrue(note_render.diagram_md(note_v2._clean_diagram(TL))[0].startswith("- **2010**"))
        self.assertIn("→（拥挤）→", note_render.diagram_md(note_v2._clean_diagram(CA))[0])
        self.assertEqual(len(note_render.diagram_md(note_v2._clean_diagram(MX))), 4)
        self.assertIn("    - 问卷", note_render.diagram_md(note_v2._clean_diagram(TR)))


class 悬空指路(unittest.TestCase):
    def test_两段整段都是指路句_不越界(self):
        import note_guards
        rv = {"sections": [{"heading": "甲", "prose": ["看下表。", "正文一句。", "见下图。"]}]}
        out, _, warns = note_guards.drop_dangling_pointers(rv)
        self.assertEqual(out["sections"][0]["prose"], ["正文一句。"])
        self.assertTrue(warns)


if __name__ == "__main__":
    unittest.main()
