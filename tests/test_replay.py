"""F15 冻结回放：守卫的改动在**守卫前的模型输出**上重放验证。**零 LLM。**

`tests/fixtures/raw_runs/` 里是 v3/v2 真跑攒下的 `review_raw.json`（守卫链之前）。
本文件只读它们 + `note_data.json` 的 `entries`，跑确定性守卫，断言 F15 的每一条：
A 猜测句判据不再误杀事实句、B 缺项假阳性不再报、C 转录矛盾落到**讲它的那一节**、
D 空修正行不出、E 同节符号差号标出来、F 跨节符号正负号翻转标出来、G 悬空图表指路句删掉、
H 账本节号收窄、以及 §2 的四条删除护栏。
"""
from __future__ import annotations

import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import note_conflict as nc                                        # noqa: E402
import note_guards as ng                                          # noqa: E402
import note_v2 as nv                                              # noqa: E402
import notes_replay as nr                                         # noqa: E402

RAW = ROOT / "tests" / "fixtures" / "raw_runs"
JOBS = ("v3_busi3243", "v3_ecs170", "v2_busi3243", "v2_ecs170",
        "v2_econ10790", "v2_che002a")


def _load(job: str, name: str) -> dict:
    return json.loads((RAW / job / name).read_text(encoding="utf-8"))


def _data(job: str) -> dict:
    return _load(job, "note_data.json")


def _raw(job: str) -> dict:
    return _load(job, "review_raw.json")


class Fixtures(unittest.TestCase):
    """冻结件本身要对得上（被谁改了、缺了，先在这里红）。"""

    def test_六份_raw_都在(self):
        for job in JOBS:
            self.assertTrue((RAW / job / "review_raw.json").exists(), job)

    def test_v3_两份带_note_data(self):
        for job in ("v3_busi3243", "v3_ecs170"):
            d = _data(job)
            self.assertTrue(d.get("entries"), job)
            self.assertIn("review", d)

    def test_审计件与_raw_对得上(self):
        audit = json.loads((RAW / "scrub_audit.json").read_text(encoding="utf-8"))
        self.assertEqual(27, len(audit["cases"]))
        self.assertEqual(0.0, audit["precision_before"]["precision"])
        jobs = {c["job"] for c in audit["cases"]}
        self.assertTrue(jobs <= set(JOBS), jobs)


class A猜测句判据(unittest.TestCase):
    """F15-A：事实句不许删；作者点名的自言自语照旧删。"""

    def test_旧判据误杀的那_27_句现在一句都不删(self):
        audit = json.loads((RAW / "scrub_audit.json").read_text(encoding="utf-8"))
        bad = []
        for c in audit["cases"]:
            if c["label"] != "factual":
                continue
            rev = {"sections": [{"heading": "H", "ts": "09:45", "prose": [c["sentence"]]}]}
            _out, removed, _w = ng.scrub(rev)
            if removed:
                bad.append((c["job"], c["sentence"][:40], removed))
        self.assertEqual([], bad, f"旧判据误杀的句子又被删了：{bad}")

    def test_冻结_raw_上猜测句清理零删除(self):
        for job in JOBS:
            _out, removed, _w = ng.scrub(_raw(job))
            self.assertEqual([], removed, f"{job}：还好意思删{removed}")

    def test_作者点名的真自言自语照旧删(self):
        audit = json.loads((RAW / "scrub_audit.json").read_text(encoding="utf-8"))
        for case in audit["must_flag_after"]:
            with self.subTest(s=case["sentence"][:16]):
                if case.get("where") == "answer":
                    # 解答字段：整段自言自语 → 宁缺（这道变式不出）
                    rev = {"sections": [{"variants": [{"q": "变式题面", "a": case["sentence"]}]}]}
                    out, removed, warns = ng.scrub(rev)
                    self.assertTrue(removed, f"没删：{case['sentence'][:20]}")
                    self.assertEqual([], out["sections"][0]["variants"])
                    self.assertTrue(any("整题不出" in w for w in warns), warns)
                    continue
                rev = {"sections": [{"heading": "H", "ts": "13:16",
                                     "prose": [case["sentence"]]}]}
                _out, removed, warns = ng.scrub(rev)
                self.assertTrue(removed, f"没删：{case['sentence']}")
                self.assertTrue(warns)

    def test_答案只剩一句结论时不删(self):
        """作者 A：不许把 qa 答案切成「不能。」这种光秃秃的结论。"""
        rev = {"qa": [{"q": "p 值变小就能说效应更大吗？",
                       "a": "不能。检查：若样本更大、估计更精确，p 就会变小。"}]}
        out, _removed, _w = ng.scrub(rev)
        a = out["qa"][0]["a"] if out.get("qa") else ""
        self.assertIn("不能。", a)
        self.assertGreater(len(a), ng.SAFE_SOLUTION_MIN, "只剩一句结论了")

    def test_原文里那句抽样波动还在(self):
        """作者 A 的原句：§24 第一句（事实）不许删，段落不许从「这种」起头。"""
        rev = {"sections": [{"heading": "[09:45] 估计量对数据变化的敏感性", "ts": "09:45",
                             "prose": ["估计量不是数据的固定属性：删掉几个观测、换一批样本，"
                                       "β̂ 就会动。",
                                       "这种“动”就是抽样波动，方差衡量它。"]}]}
        out, removed, _w = ng.scrub(rev)
        p = out["sections"][0]["prose"]
        self.assertEqual([], removed)
        self.assertTrue(p[0].startswith("估计量不是数据的固定属性"), p)
        self.assertFalse(p[0].startswith("这种"), p)

    def test_段首代词不许留(self):
        """§2 ②：删完段落以「这种/这/该/它」起头 → 打回（保留原句）。"""
        _new, blocked, kept = ng.safe_delete(
            "我可能把这里的顺序记错了。这种“动”就是抽样波动。", [(0, 14)])
        self.assertEqual([], kept)
        self.assertTrue(blocked and blocked[0]["why"] == "第一句不许删", blocked)

    def test_qa_答案里的可能不算猜测(self):
        """作者 A：`p 变小可能只是样本更大` 是事实说明，不是模型在猜。"""
        rev = {"qa": [{"q": "p 值小就说明效应大吗？",
                       "a": "不能。p 变小可能只是样本更大、估计更精确；效应大小看系数本身，"
                            "不看 p。"}]}
        out, removed, _w = ng.scrub(rev)
        self.assertEqual([], removed)
        self.assertIn("可能只是样本更大", out["qa"][0]["a"])


class B缺项判据(unittest.TestCase):
    """F15-B：只有「X 只在乘积项里」才报；没有模型式的节不许报。"""

    def test_冻结的_v3_ecs170_S15_不再报(self):
        rev = _raw("v3_ecs170")
        got = [c for c in nc.missing_term_conflicts(rev) if c["section"] == 15]
        self.assertEqual([], got, got)

    def test_冻结的全部_raw_都不报缺项(self):
        for job in JOBS:
            self.assertEqual([], nc.missing_term_conflicts(_raw(job)), job)

    def test_变量根本不在模型里也不报(self):
        """作者 B：只有『只在乘积项里』才报 —— X 完全没出现在模型式里时不许报。"""
        rev = {"sections": [{"algebra": "$y=\\beta_0+\\beta_1 education+\\beta_2 experience$",
                             "prose": ["schooling 的系数是 0.5。"]}]}
        self.assertEqual([], nc.missing_term_conflicts(rev))

    def test_只有乘积项才报_没有模型式不报(self):
        no_model = {"sections": [{"algebra": "$t=\\hat\\beta/SE(\\hat\\beta)$",
                                  "prose": ["schooling 的系数是 0.5。"]}]}
        self.assertEqual([], nc.missing_term_conflicts(no_model))
        with_model = {"sections": [{
            "algebra": "$y=\\beta_0+\\beta_1 education+\\beta_2 experience"
                       "+\\beta_3(male\\cdot experience)+u$",
            "prose": ["male 的系数是 −2。"]}]}
        got = nc.missing_term_conflicts(with_model)
        self.assertEqual(1, len(got), got)
        self.assertIn("乘积", got[0]["why"])

    def test_标注句不再重复_本节模型式里(self):
        c = {"kind": "missing-term", "sections": [1], "section": 1, "term": "male",
             "why": "只出现在乘积项里（没有单独的主效应项）", "texts": ["male 的系数"]}
        rev = {"sections": [{"heading": "H", "prose": ["male 的系数是 −2。"]}]}
        import types
        orig = nc.conflicts
        nc.conflicts = types.SimpleNamespace(conflicts=lambda *a, **k: [c]).conflicts
        try:
            out, _extra, warns = ng.flag_conflicts(rev)
        finally:
            nc.conflicts = orig
        blob = json.dumps(out, ensure_ascii=False)
        self.assertIn("例题却给它一个系数", blob)
        self.assertNotIn("本节模型式里 male 本节的模型式里", blob)
        self.assertIn("⚠", blob)
        self.assertTrue(warns)


class C转录矛盾落节(unittest.TestCase):
    """F15-C：转录自己前后不一致 → 落到**时间跨度盖住那两句**的那一节。"""

    def test_v3_busi_落在_S16_不是_S15(self):
        out, _extra, warns = ng.flag_conflicts(_raw("v3_busi3243"),
                                               entries=_data("v3_busi3243")["entries"])
        s15 = json.dumps(out["sections"][14], ensure_ascii=False)
        s16 = json.dumps(out["sections"][15], ensure_ascii=False)
        self.assertIn("课上口述本身前后不一致", s16, "没落到 §16")
        self.assertNotIn("课上口述本身前后不一致", s15, "不该落到 §15")

    def test_引用保留原空格_时间写成_HHMMSS(self):
        out, _extra, _w = ng.flag_conflicts(_raw("v3_busi3243"),
                                            entries=_data("v3_busi3243")["entries"])
        blob = json.dumps(out["sections"][15], ensure_ascii=False)
        self.assertIn("🎙 09:34:15 说「For each additional year of experience", blob)
        self.assertNotIn("Foreachadditionalyear", blob)

    def test_时间跨度判据(self):
        secs = [{"ts": "09:29"}, {"ts": "09:31"}, {"ts": "09:35"}]
        src = {"quotes": [{"ts": "09:33:35", "quote": "a"}, {"ts": "09:34:15", "quote": "b"}]}
        self.assertEqual(2, ng._source_conflict_section(secs, src))
        src2 = {"quotes": [{"ts": "09:31", "quote": "a"}, {"ts": "09:34:15", "quote": "b"}]}
        self.assertEqual(2, ng._source_conflict_section(secs, src2))
        src3 = {"quotes": [{"ts": "09:30", "quote": "a"}, {"ts": "09:34:15", "quote": "b"}]}
        self.assertEqual(0, ng._source_conflict_section(secs, src3))

    def test_没有_ts_的引语不写空时间(self):
        """引语没有 ts 就不写它（宁可少一条证据，也不写「 说「…」」这种空时间）。"""
        src = {"quotes": [{"ts": "", "quote": "a"}, {"ts": "09:34:15", "quote": "b"}]}
        q = [x for x in src["quotes"] if str(x.get("ts") or "").strip()][:2]
        note = "；".join(f"🎙 {ng._hhmmss(x['ts'])} 说「{x['quote']}」" for x in q) if q else ""
        self.assertEqual("🎙 09:34:15 说「b」", note)


class D台账空箭头(unittest.TestCase):
    """F15-D：没有修正值的行不许渲染成「「原话」→ （依据：…）」。"""

    def test_空修正行被丢掉(self):
        rev = {"corrections": [
            {"where": "10:03:29", "spoken": "原话 A", "fixed": "", "basis": "📚 标准理论"},
            {"where": "10:11:50", "spoken": "原话 B", "fixed": "   ", "basis": "b"},
            {"where": "10:12:00", "spoken": "原话 C", "fixed": "正确值", "basis": "b"},
            {"kind": "check", "spoken": "重算", "fixed": "", "basis": "核算"}]}
        out, extra, warns = ng.clean_ledger(rev)
        wheres = [c.get("where") for c in out["corrections"]]
        self.assertEqual(["10:12:00", None], wheres)      # check 行没有 where
        self.assertEqual(2, extra[0]["dropped"])
        self.assertTrue(warns)

    def test_渲染层也不出空箭头(self):
        import note_html
        html = note_html.note_page(_fake_data({"corrections": [
            {"where": "10:03:29", "spoken": "原话", "fixed": "", "basis": "b"}]}))
        self.assertNotIn("→ <b></b>", html)


def _fake_data(review_extra: dict):
    """最小 NoteData（渲染空箭头那条判据用）。"""
    import note_render
    return note_render.NoteData(date="2026-10-18", course="BUSI3243",
                                review=dict(review_extra), warns=[])


class E符号定义差号(unittest.TestCase):
    """F15-E：同一节里同一个左端写了两次、按本节定义代入后对不上 → 标 ⚠（只标不改）。"""

    def test_v3_ecs170_S53_抓到了(self):
        got = nc.symbol_sign_conflicts(_raw("v3_ecs170"))
        self.assertEqual(1, len(got), got)
        self.assertEqual(53, got[0]["section"])
        self.assertEqual("差一个负号", got[0]["why"])

    def test_其它_raw_一份都不报(self):
        for job in JOBS:
            if job == "v3_ecs170":
                continue
            self.assertEqual([], nc.symbol_sign_conflicts(_raw(job)), job)

    def test_标在正文里且不改式子(self):
        rev = _raw("v3_ecs170")
        out, extra, warns = ng.flag_conflicts(rev)
        self.assertGreaterEqual(extra[0]["flagged"], 1)
        blob = json.dumps(out["sections"][52], ensure_ascii=False)
        self.assertIn("差一个负号", blob)
        # 一个符号都不许改：本节的代数行与 raw 逐字一致
        self.assertEqual(_raw("v3_ecs170")["sections"][52]["algebra"],
                         out["sections"][52]["algebra"])

    def test_数值比对却符号一致时不报(self):
        rev = {"sections": [{"algebra": "", "prose": [
            "$g_j=-(\\hat y_j-y_j)$；$g_j=(y_j-\\hat y_j)$",
            "$\\partial E/\\partial w_{hj}=g_j b_h$；"
            "$\\partial E/\\partial w_{hj}=g_j b_h$"]}]}
        self.assertEqual([], nc.symbol_sign_conflicts(rev))


class F跨节正负号(unittest.TestCase):
    """F15-F：同一个符号一节当阈值减、另一节当偏置加 → 在后一节标 ⚠。"""

    def test_v3_ecs170_的_gamma_theta_都抓到了(self):
        got = nc.sign_flip_conflicts(_raw("v3_ecs170"))
        syms = {c["sym"] for c in got}
        self.assertIn("gamma", syms)
        self.assertIn("theta", syms)
        for c in got:
            self.assertEqual(c["section"], c["sections"][-1])

    def test_只有带伤害的那两份报(self):
        """v3 ecs（γ §37→§43/§45、θ §10→§45）与 v2 ecs（γ §44→§50）是真的；
        其余四份一份都不许报（假阳性 = 回归）。"""
        fired = {job for job in JOBS if nc.sign_flip_conflicts(_raw(job))}
        self.assertEqual({"v3_ecs170", "v2_ecs170"}, fired)

    def test_合法不同符号_theta_与_b_不报(self):
        """b 与 θ 是**两个符号**（文中写明 b=−θ）：一个只加 b、另一个只减 θ → 不许报。"""
        rev = {"sections": [
            {"ts": "16:53", "algebra": "$\\sum_i w_i x_i+b>0,\\ b=-\\theta$"},
            {"ts": "17:41", "algebra": "$h=f(xV+b)$，$\\hat y=hW+b$"}]}
        self.assertEqual([], nc.sign_flip_conflicts(rev))

    def test_系数不是偏置_柯布道格拉斯不报(self):
        rev = {"sections": [
            {"ts": "10:00", "algebra": "$\\ln Y=\\ln A+\\alpha\\ln K$"},
            {"ts": "10:16", "algebra": "$e_i^2=\\alpha_0+\\alpha_1x_{i2}+u_i$"},
            {"ts": "10:30", "algebra": "$y=\\beta_0+\\beta_1x_1$"}]}
        self.assertEqual([], nc.sign_flip_conflicts(rev))

    def test_标在后一节(self):
        rev = {"sections": [
            {"ts": "17:30", "heading": "隐藏层", "prose": ["讲解。"],
             "algebra": "$h_h=f(\\sum_i v_{ih}x_i-\\gamma_h)$"},
            {"ts": "17:41", "heading": "多层感知机", "prose": ["讲解。"],
             "algebra": "$h=f(xV+\\gamma)$"}]}
        out, extra, warns = ng.flag_conflicts(rev)
        self.assertEqual(1, extra[0]["flagged"])
        self.assertIn("前面（第 1 节）把 γ 当阈值减去", json.dumps(out["sections"][1],
                                                              ensure_ascii=False))
        self.assertTrue(warns)


class G悬空图表指路(unittest.TestCase):
    """F15-G：指着本节没有的图表的句子 → 删掉（作者实录：§22「三者差别看下表即可」）。"""

    def test_v3_ecs170_S22_那句被删(self):
        """⚠️ 这条判据只在**组件已经被前面那几刀挪走/丢掉之后**才该响：raw 里 §22 还有表，
        所以回放整链（组件上限先丢掉那张表）之后它才成为悬空指针。"""
        rev = _raw("v3_ecs170")
        self.assertTrue(any("下表" in p for p in rev["sections"][21].get("prose") or []),
                        "fixture 变了")
        out, warns, _would = nr.guard_chain(rev, entries=_data("v3_ecs170")["entries"])
        self.assertFalse(any("三者差别看下表" in p
                             for p in (out["sections"][21].get("prose") or [])))
        self.assertTrue(any("指向不存在图表" in w for w in warns), warns)

    def test_有表时指路句不动(self):
        rev = {"sections": [{"heading": "H", "prose": ["结论见下表。"],
                             "compare": {"title": "t", "cols": ["a", "b"],
                                         "rows": [["1", "2"]]}}]}
        out, extra, _w = ng.drop_dangling_pointers(rev)
        self.assertEqual(rev, out)
        self.assertEqual(0, extra[0]["dropped"])


class H账本指路(unittest.TestCase):
    """F15-H：`where`/复习安排里的「第 N 节」指路收窄成时间戳。"""

    def test_只留时间戳(self):
        rev = {"corrections": [{"where": "第 3 节（🎙 17:02）", "spoken": "q", "fixed": "y"}],
               "review_plan": ["用 5 分钟默写…（对照第二节）", "读 §3"]}
        out, extra, warns = ng.clean_ledger(rev)
        self.assertEqual("（🎙 17:02）", out["corrections"][0]["where"])
        self.assertEqual(["用 5 分钟默写…", "读 §3"], out["review_plan"])
        self.assertEqual(2, extra[0]["repaired"])
        self.assertTrue(warns)

    def test_去掉之后什么都不剩就保留原样(self):
        rev = {"corrections": [{"where": "第 3 节", "spoken": "q", "fixed": "y"}]}
        out, extra, _w = ng.clean_ledger(rev)
        self.assertEqual("第 3 节", out["corrections"][0]["where"])
        self.assertEqual(0, extra[0]["repaired"])

    def test_冻结的_v3_ecs_账本里没有第_N_节(self):
        import re
        out, _e, _w = ng.clean_ledger(_raw("v3_ecs170"))
        for c in out.get("corrections") or []:
            self.assertFalse(re.search(r"第\s*\d+\s*节", str(c.get("where") or "")), c)


class 括注不叠(unittest.TestCase):
    """F15 §3：同一个括注 12 字内出现两次 → 只留前一个（两种括号都认）。"""

    def test_两种括号都收(self):
        self.assertEqual("方差分析（ANOVA）模型与 t 检验。",
                         ng.collapse_dup_gloss("方差分析（ANOVA）模型（ANOVA）与 t 检验。"))
        self.assertEqual("回归 (OLS) 用 与它。",
                         ng.collapse_dup_gloss("回归 (OLS) 用 (OLS) 与它。"))

    def test_超过窗口不碰(self):
        s = "斜率（slope）这个量在很多地方用，和别处的（slope）不是一个意思。"
        self.assertEqual(s, ng.collapse_dup_gloss(s))

    def test_不同括注不碰(self):
        s = "交互项（interaction term）与虚拟变量（dummy）。"
        self.assertEqual(s, ng.collapse_dup_gloss(s))

    def test_announce_terms_窗口是_12(self):
        got = nv.announce_terms("方差分析模型（ANOVA）", [("方差分析", "ANOVA")], set())
        self.assertEqual("方差分析模型（ANOVA）", got)
        # 距离 7 个字：窗口 6 看不见（会再插一次），窗口 12 看得见
        got2 = nv.announce_terms("方差分析模型和它的记号（ANOVA）", [("方差分析", "ANOVA")], set())
        self.assertEqual("方差分析模型和它的记号（ANOVA）", got2)


class 安全删除四护栏(unittest.TestCase):
    """F15 §2：正文删字只走 `safe_delete`，四条护栏每条都能独立打回。**纯函数级**。"""

    def test_1_第一句不许删(self):
        s = "我可能听错了公式。估计量不是数据的固定属性。"
        new, blocked, kept = ng.safe_delete(s, [(0, 10)])
        self.assertEqual(s, new)
        self.assertEqual([], kept)
        self.assertEqual("第一句不许删", blocked[0]["why"])

    def test_1_整段全删是允许的(self):
        """例外：整段都是独白（字段被清空）→ 没有段落可"没头没脑"，允许（宁缺）。"""
        s = "我可能听错了公式。"
        new, blocked, kept = ng.safe_delete(s, [(0, len(s))])
        self.assertEqual("", new.strip())
        self.assertEqual([], blocked)
        self.assertTrue(kept)

    def test_2_删后段首是代词就保留原句(self):
        """作者 A 的现场形状：删掉首句之后段落从「这种」起 → 打回（保留原句）。"""
        s = "我可能听错了。这种“动”就是抽样波动。"
        new, blocked, kept = ng.safe_delete(s, [(0, 6)])
        self.assertEqual(s, new)
        self.assertEqual([], kept)
        self.assertTrue(blocked)

    def test_2_段首连接词(self):
        """规则本体：删完之后段落从连接词/指代词起 → `_bad_after` 必须报。"""
        for lead in ("接着", "然后", "因此", "所以", "其中", "但同时", "此外", "它", "该",
                     "这种", "这个", "这就是"):
            with self.subTest(lead=lead):
                self.assertTrue(ng._bad_after(lead + "后面的内容。"), lead)
        # 反例：正常开头不许报（`这是…` 是自指，不是回指）
        for ok in ("估计量不是数据的固定属性。", "价格等于边际成本。", "Sigmoid 会饱和。",
                   "这是本段唯一有实际安排信息的一句话。", "这时换一批样本。"):
            with self.subTest(ok=ok[:6]):
                self.assertEqual("", ng._bad_after(ok))

    def test_3_美元符不配对就打回(self):
        s = "方差是 $\\sigma^2/[SST_j]$，共线时被吹大。"
        # 删掉 `[SST_j]$` 里的右半边 → $ 不配对
        a = s.find("[SST_j]") + len("[SST_j]")
        _n, blocked, _k = ng.safe_delete(s, [(a, a + 1)])
        self.assertTrue(blocked and "$" in blocked[0]["why"], blocked)

    def test_3_空括号与叠标点(self):
        s = "误差来自（共线性）与样本。"
        a = s.find("共线性")
        _n, blocked, _k = ng.safe_delete(s, [(a, a + 3)])
        self.assertTrue(blocked and "空括号" in blocked[0]["why"], blocked)
        s2 = "第一点。第二点，，第三点。"
        _n2, blocked2, _k2 = ng.safe_delete(s2, [(9, 12)])
        self.assertTrue(any("叠标点" in b["why"] for b in blocked2), blocked2)

    def test_4_公式里出现过的记号免疫(self):
        rev = {"sections": [{"algebra": "$VIF_j=1/(1-R_j^2)$", "prose": ["VIF 是方差膨胀因子。"]}]}
        immune = ng.immune_from_review(rev)
        self.assertIn("vif", immune)
        s = "这里 VIF 只是记号，删掉。"
        _n, blocked, _k = ng.safe_delete(s, [(s.find("VIF"), s.find("VIF") + 3)],
                                         immune=immune)
        self.assertTrue(blocked and "记号" in blocked[0]["why"], blocked)

    def test_时间戳带括号_整对拿走不留空括号(self):
        out, _e, _w = ng.strip_clock_times(
            {"sections": [{"prose": ["考试范围（12:50:28）就这些。"]}]})
        got = out["sections"][0]["prose"][0]
        self.assertEqual("考试范围就这些。", got)


class 回放全链(unittest.TestCase):
    """整链回放（零 LLM）跑得完，而且出来的东西是**干净版**。"""

    def test_两条链都跑得完(self):
        for job in ("v3_busi3243", "v3_ecs170"):
            rev, warns, would = nr.replay(_data(job), _raw(job))
            self.assertTrue(rev.get("sections"), job)
            self.assertTrue(would, "would-repair 记账丢了")
            self.assertTrue(any("速览" in w for w in warns) or warns, warns)

    def test_回放后_v3_busi_S24_首句还在(self):
        rev, _w, _wo = nr.replay(_data("v3_busi3243"), _raw("v3_busi3243"))
        p = rev["sections"][23]["prose"][0]
        self.assertTrue(p.startswith("估计量不是数据的固定属性"), p[:40])

    def test_回放后_v3_ecs_S53_标了差号(self):
        rev, _w, _wo = nr.replay(_data("v3_ecs170"), _raw("v3_ecs170"))
        blob = json.dumps(rev["sections"][52], ensure_ascii=False)
        self.assertIn("差一个负号", blob)

    def test_回放不需要网络也不需要_API_key(self):
        import os
        keep = {k: os.environ.get(k) for k in
                ("DEEPSEEK_API_KEY", "OPENROUTER_API_KEY", "JEV_API_KEY")}
        for k in keep:
            os.environ.pop(k, None)
        try:
            rev, _w, _wo = nr.replay(_data("v3_ecs170"), _raw("v3_ecs170"))
            self.assertTrue(rev.get("sections"))
        finally:
            for k, v in keep.items():
                if v is not None:
                    os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
