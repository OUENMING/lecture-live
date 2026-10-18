#!/usr/bin/env python3
"""F9b「支持性核对」（Jev / E3 口径）+ F9b 清洗三件套 —— **全部离线**（假判官注入）。

    ClassLive.app/Contents/MacOS/python tests/test_support_check.py

## 这个文件钉住了什么

1. ⭐ `support_claims` 只收「讲义说过 X」：🎙/🎙+✏️ 例题给定 + 课上/老师/本课句；
   ✏️ 自补（块/句）一条都不收 —— 查了是假警报（作者 F9b 第 1 条）。
2. ⭐ `support_check`：p<0.15 → 台账（checks）+ ⚠；p=None → 什么都不做；
   **只标不改**（正文逐字不动；唯一例外：例题给定 + F9 重建验证通过 → 挂牌 🎙+✏️）。
3. ⭐ 成本：≤2 次请求（≤20 问/批）—— 第 3 批的 claim 不查、条数进提醒。
4. ⭐ E3 的 state 前言 / 指令**原文**（L 编号 + 时间戳）—— 分辨力建立在它们上。
5. ⭐ F9b 清洗三件套：无标签变式/挖空默认 ✏️；「按口述还原」→ 合标 🎙+✏️ 在代码里做；
   空壳 example/faded 摘掉（scrub 不许碰空壳、碰出 `"steps": []`）。
6. ⭐ 机械核压模型核：`MC(6)=AC(6)` 式断言能用节内函数复算成两边相等 →
   模型行作废（`unchecked`），正文不标 ⚠、修正说明不列、原行留在台账。
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import note_guards as ng          # noqa: E402
import note_math as nm            # noqa: E402
import note_v2                    # noqa: E402

ENTRIES = [{"ts": f"13:16:{i:02d}", "en": f"line {i}"} for i in range(60)]
ENTRIES[29] = {"ts": "13:16:29",
               "en": "the average cost at 2.89 is the shutting down price"}


class _FakeMat:
    """最小材料替身：`_chunks` 非空（= 有材料）+ `search` 给一段文本。"""

    def __init__(self, text):
        class _C:
            pass
        c = _C()
        c.text = text
        self._chunks = [c]
        self._text = text

    def search(self, q, k=3, **kw):
        class _C:
            pass
        c = _C()
        c.text = self._text
        return [c]


def _mk_review() -> dict:
    return {"sections": [
        {"heading": "停产价格", "ts": "13:16",
         "example": {"title": "🎙 13:16 停产价格计算",
                     "prompt": "已知 $AC(q)=60q+500/q$，求停产价格。"},
         "prose": ["课上先写 $AC(q)=TC(q)/q$。",
                   "这里补一句自己的推导。"]},
        {"heading": "极值", "ts": "13:20",
         "example": {"title": "✏️ 自补：三次函数",
                     "prompt": "已知 $f(x)=x^{3}-3x^{2}+4$，求驻点。"}},
        {"heading": "MC=AC", "ts": "13:24",
         "prose": ["老师说最低点处 $MC=AC$。", "✏️ 自补：再多算一步。",
                   "✏️ 自补：课上没讲到这里，自己补的推导。"]},
        {"heading": "无标签例题", "ts": "13:28",
         "example": {"title": "", "prompt": "已知 $g(x)=x^{2}$，求最小值。"}},
    ]}


class Extraction(unittest.TestCase):
    def test_只收讲义断言_自补一条不收(self):
        got = [c["claim"] for c in ng.support_claims(_mk_review())]
        self.assertIn("已知 $AC(q)=60q+500/q$，求停产价格。", got)
        self.assertIn("课上先写 $AC(q)=TC(q)/q$。", got)
        self.assertIn("老师说最低点处 $MC=AC$。", got)
        self.assertNotIn("已知 $f(x)=x^{3}-3x^{2}+4$，求驻点。", got, "✏️ 例题块被收了")
        self.assertNotIn("✏️ 自补：再多算一步。", got, "✏️ 句子被收了")
        self.assertNotIn("✏️ 自补：课上没讲到这里，自己补的推导。", got,
                         "✏️ 自补句里带「课上」也被收了（自补豁免优先）")
        self.assertNotIn("这里补一句自己的推导。", got, "没有「课上/老师/本课」的句子被收了")
        self.assertNotIn("已知 $g(x)=x^{2}$，求最小值。", got, "无标签例题被收了（保守：只查断言讲义说过的东西）")

    def test_合标例题也算课上给定(self):
        r = _mk_review()
        r["sections"][0]["example"]["title"] = "🎙+✏️ 13:16 停产价格（按口述还原）"
        got = [c["claim"] for c in ng.support_claims(r)]
        self.assertIn("已知 $AC(q)=60q+500/q$，求停产价格。", got)

    def test_免责句和教材对照句不收(self):
        r = {"sections": [{"heading": "h", "ts": "13:16", "prose": [
            "⚠ 课上只给了片段：原题见 📖 卷子 / 未核对",
            "本课官方材料把最低点定义为停产价格；而 📚 标准教科书叫另一个名字。",
            "本课把 $AC$ 最低点叫停产价格。"]}]}
        got = [c["claim"] for c in ng.support_claims(r)]
        self.assertNotIn("⚠ 课上只给了片段：原题见 📖 卷子 / 未核对", got,
                         "免责句（未核对）被当 claim 收了 —— 会挂出'未核对句被标'的笑话")
        self.assertFalse([c for c in got if "📚" in c], "教材对照句被收了")
        self.assertIn("本课把 $AC$ 最低点叫停产价格。", got)

    def test_长句在标点处截_仍是逐字子串(self):
        long = "课上老师先讲了一段很长的铺垫，" * 12 + "然后给出结论 $q=6$。"
        r = {"sections": [{"heading": "h", "ts": "13:16", "prose": [long]}]}
        got = ng.support_claims(r)
        self.assertTrue(got)
        c = got[0]["claim"]
        self.assertLessEqual(len(c), 140)
        self.assertIn(c, long, "截完不再是逐字子串 —— annotate 会定位不到")


class Judging(unittest.TestCase):
    def _run(self, p, *, review=None, entries=None):
        calls: list = []

        def ask(state, instructions):
            calls.append((state, instructions))
            return [p] * len(instructions)

        out, extra, warns = ng.support_check(review or _mk_review(),
                                             entries=entries if entries is not None else ENTRIES,
                                             ask=ask)
        return out, extra, warns, calls

    def test_p_低于_tau_挂台账与警告(self):
        out, extra, warns, calls = self._run(0.05)
        rows = [r for r in (out.get("checks") or []) if r.get("status") == "mismatch"]
        self.assertEqual(len(rows), 3, f"该挂 3 条，得到 {len(rows)}")
        self.assertEqual(rows[0]["how"], "转录里没找到这句（核对）")
        self.assertIn("support p=0.05", rows[0]["recomputed"])
        self.assertTrue(any("挂 ⚠" in w for w in warns), warns)
        self.assertEqual(extra, [])
        # ⭐ 只标不改：正文逐字不动
        self.assertEqual(out["sections"][0]["prose"][0], "课上先写 $AC(q)=TC(q)/q$。")

    def test_p_None_什么都不做(self):
        out, _, warns, _ = self._run(None)
        self.assertFalse([r for r in (out.get("checks") or [])
                          if r.get("status") == "mismatch"])
        self.assertTrue(any("全部找到" in w for w in warns), warns)

    def test_tau_边界不挂(self):
        out, _, _, _ = self._run(0.15)
        self.assertFalse([r for r in (out.get("checks") or [])
                          if r.get("status") == "mismatch"])

    def test_state_与指令是E3原文_按节分批(self):
        _, _, _, calls = self._run(None)
        self.assertEqual(len(calls), 2, "两节有 claim → 两次请求（按节分批）")
        state, instrs = calls[0]
        self.assertTrue(state.startswith(ng.SUPPORT_PREAMBLE))
        self.assertIn("L1 [13:16:00] line 0", state)
        self.assertIn("L32 [13:16:31] line 31", state)
        self.assertNotIn("line 59", state, "第一节的窗口不许伸到第二节去")
        self.assertIn("[13:16:29] the average cost at 2.89 is the shutting down price",
                      state)
        self.assertTrue(instrs[0].startswith("Look at the transcript excerpt only."))
        self.assertIn("Answer yes only if the excerpt actually says it", instrs[0])
        self.assertIn("L60 [13:16:59] line 59", calls[1][0], "第二节有自己的窗口")

    def test_按节分批_各用各的窗口(self):
        r = {"sections": [
            {"heading": "A", "ts": "13:16", "prose": ["课上先写 $AC(q)=TC(q)/q$。"]},
            {"heading": "B", "ts": "13:16:50", "prose": ["老师强调 $MC=AC$。"]}]}
        calls: list = []

        def ask(state, instructions):
            calls.append(state)
            return [None] * len(instructions)

        ng.support_check(r, entries=ENTRIES, ask=ask)
        self.assertEqual(len(calls), 2, "两节 → 两次请求")
        self.assertNotIn("L60", calls[0], "第一节的窗口不该带第二节的时段")
        self.assertIn("L60 [13:16:59] line 59", calls[1])

    def test_单条claim窗口是前8后32(self):
        r = {"sections": [{"heading": "h", "ts": "13:16",
                           "prose": ["课上先写 $AC(q)=TC(q)/q$。"]}]}
        calls: list = []

        def ask(state, instructions):
            calls.append(state)
            return [None] * len(instructions)

        ng.support_check(r, entries=ENTRIES, ask=ask)
        state = calls[0]
        self.assertIn("L32 [13:16:31] line 31", state)
        self.assertNotIn("line 59", state, "窗口没截（±8/+32），全篇都塞进 state 了")

    def test_例题用标题里的时刻定窗口(self):
        r = {"sections": [{"heading": "h", "ts": "13:16",
                           "example": {"title": "🎙 13:16:20 课上例题（按口述还原）",
                                       "prompt": "已知 $AC(q)=q$，求最低点。"}}]}
        calls: list = []

        def ask(state, instructions):
            calls.append(state)
            return [None] * len(instructions)

        ng.support_check(r, entries=ENTRIES, ask=ask)
        state = calls[0]
        self.assertIn("L13 [13:16:12] line 12", state)
        self.assertNotIn("L1 [13:16:00]", state,
                         "没按标题的 🎙 时刻定窗口（还在用小节 ts）")

    def test_成本上限_15次请求(self):
        secs = [{"heading": f"h{i}", "ts": "13:16",
                 "example": {"title": f"🎙 13:16 例{i}",
                             "prompt": f"已知 $x{i}=0$，课上求 i={i}。"}}
                for i in range(20)]
        calls: list = []

        def ask(state, instructions):
            calls.append(instructions)
            return [None] * len(instructions)

        out, _, warns = ng.support_check({"sections": secs}, entries=ENTRIES, ask=ask)
        self.assertEqual(len(calls), 15, "上限 15 次请求")
        self.assertTrue(any("5 条超成本上限没查" in w for w in warns), warns)

    def test_判官炸了也不抛(self):
        def ask(state, instructions):
            raise RuntimeError("boom")

        out, extra, warns = ng.support_check(_mk_review(), entries=ENTRIES, ask=ask)
        self.assertFalse([r for r in (out.get("checks") or [])
                          if r.get("status") == "mismatch"])


class MaterialsJudge(unittest.TestCase):
    """F9c：📖 题面**对材料判**、没材料豁免 —— 绝不许因"讲师没念过卷子上的题"挂 📖。"""

    SEC = {"heading": "例", "ts": "13:25",
           "example": {"title": "📖 2025-12 期末卷 Exercise 5（🎙 13:40）",
                       "prompt": "$TC(q)=q^{3}-12q^{2}+60q$，求平均成本最低点处的产量与价格。"}}

    def test_题面对材料判(self):
        calls: list = []

        def ask(state, instructions):
            calls.append((state, instructions))
            return [0.05] * len(instructions)

        out, _, warns = ng.support_check(
            {"sections": [dict(self.SEC)]}, entries=ENTRIES,
            materials=_FakeMat("一些课件正文。"), ask=ask)
        self.assertEqual(len(calls), 1)
        state, instrs = calls[0]
        self.assertTrue(state.startswith(ng.SUPPORT_MAT_PREAMBLE))
        self.assertIn("materials excerpt", instrs[0])
        rows = [r for r in (out.get("checks") or []) if r.get("status") == "mismatch"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["how"], "材料里没找到这句（核对）")
        self.assertTrue(any("挂 ⚠" in w for w in warns), warns)

    def test_没材料就豁免(self):
        calls: list = []

        def ask(state, instructions):
            calls.append(instructions)
            return [0.05] * len(instructions)

        out, _, warns = ng.support_check({"sections": [dict(self.SEC)]},
                                         entries=ENTRIES, ask=ask)
        self.assertFalse(calls, "没有材料 → 这条不该问 Jev")
        self.assertFalse([r for r in (out.get("checks") or [])
                          if r.get("status") == "mismatch"])
        self.assertTrue(any("豁免" in w for w in warns), warns)

    def test_材料题被标也不挂牌(self):
        sec = {"heading": "停产价格", "ts": "13:16",
               "example": {"title": "📖 卷子（🎙 13:16）",
                           "prompt": "已知 $AC(q)=q^{2}-12q+60$，求最低点的产量。",
                           "steps": [{"text": "$AC(6)=6^{2}-12\\times 6+60=24$",
                                      "why": "代入"}]}}

        def ask(state, instructions):
            return [0.05] * len(instructions)

        out, _, _ = ng.support_check({"sections": [sec]}, entries=ENTRIES,
                                     materials=_FakeMat("text"), ask=ask)
        self.assertEqual(out["sections"][0]["example"]["title"], "📖 卷子（🎙 13:16）",
                         "📖 被标不许挂牌（挂牌是 🎙 重建的出口）")
        rows = [r for r in (out.get("checks") or []) if r.get("status") == "mismatch"]
        self.assertEqual(len(rows), 1)


class Relabel(unittest.TestCase):
    """🎙 例题给定被标：重建验证通过 → 挂牌 🎙+✏️；没通过 → 照常 ⚠。"""

    VERIFIED = {"heading": "停产价格", "ts": "13:16",
                "example": {"title": "🎙 13:16 停产价格",
                            "prompt": "已知 $AC(q)=q^{2}-12q+60$，求最低点的产量。",
                            "steps": [{"text": "$AC(6)=6^{2}-12\\times 6+60=24$",
                                       "why": "代入"}]}}
    NOT_VERIFIED = {"heading": "停产价格", "ts": "13:16",
                    "example": {"title": "🎙 13:16 停产价格",
                                "prompt": "已知停产价格约 346。"}}

    def _run(self, section):
        def ask(state, instructions):
            return [0.05] * len(instructions)

        return ng.support_check({"sections": [section]}, entries=ENTRIES, ask=ask)

    def test_验证通过挂牌合标(self):
        out, _, warns = self._run(self.VERIFIED)
        title = out["sections"][0]["example"]["title"]
        self.assertTrue(title.startswith("🎙+✏️ "), title)
        self.assertIn("按口述还原", title)
        rows = [r for r in (out.get("checks") or []) if r.get("status") == "mismatch"]
        self.assertFalse(rows, "验证通过的例题给定不该再挂 ⚠")
        self.assertTrue(any("挂牌" in w for w in warns), warns)

    def test_没验证照常挂(self):
        out, _, _ = self._run(self.NOT_VERIFIED)
        self.assertEqual(out["sections"][0]["example"]["title"], "🎙 13:16 停产价格")
        rows = [r for r in (out.get("checks") or []) if r.get("status") == "mismatch"]
        self.assertEqual(len(rows), 1)


class CleaningF9b(unittest.TestCase):
    def test_无标签变式挖空默认补自补标(self):
        r = note_v2._clean_section({"heading": "h",
                                    "variants": [{"q": "求 $q$。", "a": "6"}],
                                    "faded": {"q": "补全 $x=$__", "a": "$x=1$"}})
        self.assertTrue(r["variants"][0]["q"].startswith("✏️ "))
        self.assertTrue(r["faded"]["q"].startswith("✏️ "))
        r2 = note_v2._clean_section({"heading": "h",
                                     "variants": [{"q": "🎙 课上变式", "a": "6"}]})
        self.assertEqual(r2["variants"][0]["q"], "🎙 课上变式")

    def test_按口述还原在代码里给合标(self):
        r = note_v2._clean_section({"heading": "h", "example": {
            "title": "🎙 13:39 课上例题（按口述还原）", "prompt": "$AC(q)=q$。"}})
        self.assertEqual(r["example"]["title"], "🎙+✏️ 13:39 课上例题（按口述还原）")
        # 幂等 + 不含「按口述还原」的不动
        self.assertEqual(note_v2.with_recon_label(r["example"]["title"]),
                         r["example"]["title"])
        self.assertEqual(note_v2.with_recon_label("🎙 普通例题"), "🎙 普通例题")

    def test_空壳移除(self):
        r = note_v2._clean_section({"heading": "h",
                                    "example": {"steps": []}, "faded": {"steps": []}})
        self.assertNotIn("example", r)
        self.assertNotIn("faded", r)
        self.assertNotIn("plot", r)

    def test_scrub_不碰空壳长出steps(self):
        out, _, _ = ng.scrub({"sections": [{"heading": "h", "example": {}, "faded": {}}]})
        s = out["sections"][0]
        self.assertEqual(s.get("example"), {})
        self.assertNotEqual(s.get("example"), {"steps": []},
                            "scrub 碰了空壳 —— 又长出 F9b 要删的那种壳")


class MechOverride(unittest.TestCase):
    """模型自写的 mismatch 行：节内函数复算两边相等 → 作废只留台账（F9b 作者点名）。"""

    ROW = {"claim": "MC(6)=AC(6)", "status": "mismatch", "recomputed": "",
           "how": "3×36−72+60=96；AC(6)=24，不相等"}

    def test_两边相等则作废(self):
        data = {"sections": [{"heading": "AC", "ts": "13:16",
                              "prose": ["课上用 $AC(q)=q^{2}-12q+60$ 与 "
                                        "$MC(q)=3q^{2}-24q+60$。"]}]}
        got = nm.mech_override_rows([dict(self.ROW)], data)
        self.assertEqual(got[0]["status"], "unchecked")
        self.assertIn("作废", got[0]["how"])
        self.assertIn("24", got[0]["how"])
        # 修正说明（只收 mismatch）不再列它；原行仍可回查
        self.assertEqual(note_v2.corrections_from_mismatch(got), [])

    def test_复算不相等则不动(self):
        data = {"sections": [{"heading": "AC", "ts": "13:16",
                              "prose": ["课上用 $AC(q)=q^{2}-12q+60$ 与 $MC(q)=q^{2}+1$。"]}]}
        got = nm.mech_override_rows([dict(self.ROW)], data)
        self.assertEqual(got[0]["status"], "mismatch")

    def test_没有定义则不动(self):
        got = nm.mech_override_rows([dict(self.ROW)],
                                    {"sections": [{"heading": "x", "prose": ["没有函数。"]}]})
        self.assertEqual(got[0]["status"], "mismatch")

    def test_非等式断言不动(self):
        rows = [dict(self.ROW, claim="AC(4)=28，MC(4)=12"),
                {"claim": "AC(6)=24", "status": "ok", "recomputed": "24", "how": ""}]
        got = nm.mech_override_rows(rows, {"sections": [
            {"heading": "AC", "prose": ["$AC(q)=q^{2}-12q+60$，$MC(q)=3q^{2}-24q+60$。"]}]})
        self.assertEqual(got[0]["status"], "mismatch")
        self.assertEqual(got[1]["status"], "ok")


class Wiring(unittest.TestCase):
    def test_支持性核对在链上且在落正文之前(self):
        src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "obsidian_writer.py")
        with open(src, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("note_guards.support_check", text)
        self.assertLess(text.index("引用逐字核对"), text.index("支持性核对"))
        self.assertLess(text.index("支持性核对"), text.index("核算结果落正文"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
