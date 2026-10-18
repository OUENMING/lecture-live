"""笔记止血（2026-10-17 step 1）的判据：术语表过滤 + 课务误报过滤。

⭐ 两份**真实数据**当判据底座（都是从 vault 的笔记/仓库的缓存里抄出来的原话）：
   · `tests` 里的 `_AUDIT_TERMS` —— 审计点名的那几条（`notes/report.md` §1.2 A6/B1/B2/C4）
   · `_AUDIT_DEADLINES` —— 那份 ECON10790 笔记里 5 条课务，**0 条是真的**

⚠️ 纪律：这里的每个期望值都指向**具体那句话**（不是"有没有命中"）——
    2026-09-30 `changed` 那次教训（命中对了、值错了）就是这么来的。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import chapter          # noqa: E402
import note_terms as nt  # noqa: E402


class DeadlineWorthy(unittest.TestCase):
    #: 这 5 条是 2026-10-15 ECON10790 笔记里真实的课务条目（审计 §1.2 A8：0 真值）。
    FALSE_POSITIVES = [
        "And like I said, I have the final exam of last year.",
        "I'll show you because there are a few exercises that we can do to practice more for the final exam.",
        "The deadline is alive.",
        "At the final exam, I made the assumption that you will know what is the average and marginal.",
        "But by the time you get to the final exam, you have done the processes.",
    ]
    #: 真课务：时间锚点或"提交动作"至少占一个（都是从 vault 其它笔记里抄的原句）。
    TRUE_POSITIVES = [
        "There is no class on Wednesday this week.",
        "Everyone gets attendance for this week.",
        "Next week everyone must show up because groups will be assigned for the group project.",
        "The problem set is due Friday.",
        "Hand in your essay tomorrow.",
        "Submit it on Brightspace.",
        "So the first thing is there is an assignment on Brightspace which you will see.",
    ]

    def test_审计点名的五条全部挡下(self):
        for q in self.FALSE_POSITIVES:
            self.assertFalse(chapter.deadline_worthy(q), f"该挡下的留下了：{q}")

    def test_真课务一条不许误伤(self):
        for q in self.TRUE_POSITIVES:
            self.assertTrue(chapter.deadline_worthy(q), f"真课务被挡了：{q}")

    def test_改期直接放行(self):
        """`changed=True` 是 `CHANGED_RE` 亲手判的强信号，压掉它 = 删掉「已改期」。"""
        q = "So we have pushed the deadline."          # 没有时间锚点、没有动作词
        self.assertFalse(chapter.deadline_worthy(q))
        self.assertTrue(chapter.deadline_worthy(q, changed=True))

    def test_空句不炸(self):
        self.assertFalse(chapter.deadline_worthy(""))
        self.assertFalse(chapter.deadline_worthy(None))

    def test_渲染层真的接线了(self):
        """⭐ 判据指向**渲染输出**（不是那个纯函数）：接线断了这条红。"""
        import obsidian_writer as ow
        items = {"deadlines": [
            {"quote": "The deadline is alive.", "t": "13:20:46", "source": "model+regex",
             "changed": False},
            {"quote": "There is no class on Wednesday this week.", "t": "13:10:00",
             "source": "regex", "changed": False},
        ], "chapters": [], "atoms": []}
        out = "\n".join(ow._render_outline_section(items))
        self.assertNotIn("alive", out)
        self.assertIn("no class on Wednesday", out)
        self.assertIn("另有 1 条课务线索疑似误报", out, "被过滤了却不说 —— 静默")


class TermStopgap(unittest.TestCase):
    """术语表过滤：判据全部来自审计原文（人名/口语词/常识词/错语境义）。"""

    def test_人名挡下(self):
        for t in ("Masha", "Mary", "Larry"):
            self.assertEqual(nt.drop_reason(t, type_="person", source=nt.SRC_AUTO), "人名")

    def test_称谓开头也当人名(self):
        self.assertIn("人名", nt.drop_reason("Professor Smith", source=nt.SRC_CURATED))

    def test_口语与常识词挡下(self):
        for t in ("Interesting", "Anything", "yeah", "Department", "English", "Irish",
                  "European", "Euro", "Celsius", "PDF"):
            self.assertTrue(nt.drop_reason(t, source=nt.SRC_AUTO), f"没挡下：{t}")

    def test_本课与术语库来源也要挡口语常识词(self):
        """⭐ DB 审查 D-20：`SRC_AUTO` 被 `drop_reason` 更早的分支兜住，所以上面那条测不到
        `_common_word_signal` 的判序 bug —— 「大写单 token = 专有名词」的提前 return 抢在
        口语/常识词判断之前，`Interesting`/`Euro` 会从 `本课`/`术语库` 两条来源**放行**。"""
        for t in ("Interesting", "Euro", "Celsius"):
            self.assertEqual(nt.drop_reason(t, source=nt.SRC_CURATED), "口语/常识词", t)
            self.assertTrue(nt.drop_reason(t, source=nt.SRC_LECTURE), t)
        # 专有名词不许被这条误伤（`Coase` 是经济学课要收的词）—— 但必须是**本课**的词
        self.assertEqual(nt.drop_reason("Coase", source=nt.SRC_CURATED,
                                        lecture_term=True), "")

    def test_跨课术语库必须有本课证据(self):
        """⭐ F14 ②：术语库是全课共享的一只文件。ECS170（CS 课）的术语表里长出过
        `structure — 社会结构，影响行为的规则和制度 · 术语库`、
        `variable — 会变化的量，比如收入、年龄` —— 别的课的定义，读者会当真。"""
        for t in ("structure", "variable", "dataset"):
            why = nt.drop_reason(t, source=nt.SRC_CURATED)
            self.assertIn("跨课术语库", why, t)
            self.assertEqual("", nt.drop_reason(t, source=nt.SRC_CURATED, lecture_term=True), t)
            self.assertEqual("", nt.drop_reason(t, source=nt.SRC_CURATED, verified=True), t)

    def test_错语境义挡下_usc(self):
        """⭐ B1 那条：`USC` 的缓存解析是南加州大学，而本课是 Universal Social Charge。

        ⚠️ 判据是**词面完全相同**的两个义项 —— 只有「本课材料里有没有它」能分开，
           所以这里断言的是"没有核对 → 不进表"（`verified=False`）。
        """
        why = nt.drop_reason("USC", type_="org", source=nt.SRC_AUTO, verified=False)
        self.assertTrue(why, "USC 没有核对也进了表")

    def test_核对过的自动缓存条目放行(self):
        """核对通过 = 本课术语表/课件里真有这个词 —— 挡住它就等于砍掉有价值的条目。"""
        self.assertEqual(
            nt.drop_reason("GNI", type_="concept", source=nt.SRC_AUTO, verified=True), "")
        self.assertEqual(
            nt.drop_reason("Google Scholar", type_="org", source=nt.SRC_AUTO,
                           verified=True), "")

    def test_预置术语库也要本课证据(self):
        """`term_notes.json` 里的定义确实是人工/付费建的，但它**不分课**。
        ⭐ F14 ②：仍然要一条本课证据（本课术语表/课件里有，或复习层把它当术语抽出来）。
        多词短语（`marginal cost`）不受"常识词"那档影响，所以给上证据就直接放行。"""
        self.assertEqual(nt.drop_reason("marginal cost", source=nt.SRC_CURATED,
                                        lecture_term=True), "")
        self.assertEqual(nt.drop_reason("stationary point", source=nt.SRC_CURATED,
                                        verified=True), "")
        self.assertEqual(nt.drop_reason("marginal cost", source=nt.SRC_CURATED),
                         "跨课术语库（本课没把它当术语用）")

    def test_单token非缩写的自动缓存要核对(self):
        self.assertTrue(nt.drop_reason("Mohari", type_="place", source=nt.SRC_AUTO))
        self.assertEqual(nt.drop_reason("Mohari", type_="place", source=nt.SRC_AUTO,
                                        verified=True), "")


class TermSelect(unittest.TestCase):
    """`select` 是**纯逻辑**（命中与核对都注入）—— 所以判据不碰盘、不碰缓存文件。"""

    def _match(self, sentence):
        table = {
            "The marginal cost is below the average cost.": [
                ("marginal cost", "边际成本：多生产一单位增加的成本", nt.SRC_CURATED)],
            "Masha asked a good question about the derivative.": [
                ("Masha", "动画《玛莎和熊》里的小女孩", nt.SRC_AUTO),
                ("derivative", "导数，描述瞬时变化率", nt.SRC_CURATED)],
            "USC is a charge on income.": [
                ("USC", "南加州大学", nt.SRC_AUTO)],
        }
        return table.get(sentence, [])

    def test_命中过滤后按时间戳去重(self):
        entries = [
            {"ts": "13:20:00", "en": "Masha asked a good question about the derivative."},
            {"ts": "13:21:00", "en": "The marginal cost is below the average cost."},
            {"ts": "13:22:00", "en": "USC is a charge on income."},
        ]
        # ⭐ F14 ②：`术语库` 行要有本课证据 —— 这里注入"本课复习层抽到过这两个词"
        sel = nt.select(entries, match=self._match,
                        lecture_terms={"marginal cost", "derivative"},
                        type_of=lambda t: "person" if t in ("Masha", "Larry") else "concept")
        self.assertEqual([h.term for h in sel.hits], ["derivative", "marginal cost"])
        self.assertEqual(sel.hits[0].ts, "13:20:00", "时间戳该取最早命中那句")
        reasons = sel.reasons()
        self.assertEqual(reasons.get("人名"), 1)
        self.assertEqual(reasons.get("无法核对本课语境"), 1)

    def test_已核对时自动缓存条目回来(self):
        entries = [{"ts": "13:22:00", "en": "USC is a charge on income."}]
        sel = nt.select(entries, match=self._match, type_of=lambda t: "concept",
                        verify=lambda t: t == "USC")
        self.assertEqual([h.term for h in sel.hits], ["USC"])

    def test_页脚只在有过滤时出现(self):
        empty = nt.Selection(hits=[], dropped=[])
        self.assertEqual(empty.footer(), "")
        sel = nt.select([{"ts": "13:20:00",
                          "en": "Masha asked a good question about the derivative."}],
                        match=self._match,
                        type_of=lambda t: "person" if t == "Masha" else "")
        # Masha（人名）+ derivative（术语库但本课无据）→ 2 条都在页脚记账
        self.assertIn("已从术语表过滤 2 条", sel.footer())
        self.assertIn("跨课术语库", sel.footer())

    def test_上限截断也记账(self):
        """⚠️ 超额只截断会**静默丢东西** —— 被截的必须进 `dropped`。"""
        def match(sentence):
            return [("marginal cost", "边际成本", nt.SRC_CURATED),
                    ("average cost", "平均成本", nt.SRC_CURATED)]
        sel = nt.select([{"ts": "13:21:00", "en": "whatever"}], match=match,
                        type_of=lambda t: "", max_n=1,
                        lecture_terms={"marginal cost", "average cost"})
        self.assertEqual([h.term for h in sel.hits], ["marginal cost"])
        self.assertEqual([d.reason for d in sel.dropped], ["超出上限"])


class TermIndexSource(unittest.TestCase):
    """`TermNotes.entry_of` —— 过滤要用的「来源 + 条目」两个字段的唯一出口。"""

    def test_来源与条目(self):
        import json
        import pathlib
        import tempfile
        from build_notes import TermNotes
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "term_notes.json"
            p.write_text(json.dumps({"terms": {
                "marginal cost": {"level": "basic", "detail": "多生产一单位增加的成本"},
                "USC": {"level": "gloss", "detail": "人工写对的那条"}}}), encoding="utf-8")
            (pathlib.Path(d) / "term_notes_auto.json").write_text(json.dumps({"terms": {
                "USC": {"level": "gloss", "detail": "南加州大学", "type": "org"},
                "Masha": {"level": "gloss", "detail": "人名", "type": "person"}}}),
                encoding="utf-8")
            n = TermNotes(p)
            self.assertEqual(n.entry_of("USC")[0], "curated", "curated 必须覆盖 auto")
            self.assertEqual(n.entry_of("USC")[1]["detail"], "人工写对的那条")
            src, entry = n.entry_of("Masha")
            self.assertEqual(src, "auto")
            self.assertEqual(entry.get("type"), "person")
            self.assertEqual(n.entry_of("没这个词"), ("", {}))


if __name__ == "__main__":
    unittest.main()
