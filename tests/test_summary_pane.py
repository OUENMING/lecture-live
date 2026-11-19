"""右栏实时总结的纯函数层（summary_pane.para_blocks / bold_segments）。不起窗口。"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import summary_pane as sp  # noqa: E402


def ch(i, t0, status="final", para=None, sentences=None, title_zh="标题"):
    return {"id": i, "t0": t0, "status": status, "title": "T", "title_zh": title_zh,
            "para": para, "sentences": sentences or []}


class Segments(unittest.TestCase):
    def test_拼回去等于原文(self):
        t = "需求弹性大于 1 时，涨价 1% 会让销量降超过 1%。"
        for bolds in ([], ["大于 1"], ["大于 1", "超过 1%"], ["不存在"], ["1"], ["", None]):
            seg = sp.bold_segments(t, bolds)
            self.assertEqual("".join(s for s, _ in seg), t, bolds)

    def test_只加粗逐字出现的项(self):
        seg = sp.bold_segments("收入下降", ["收入", "利润"])
        self.assertEqual(seg, [("收入", True), ("下降", False)])

    def test_重叠项先到先得不嵌套(self):
        seg = sp.bold_segments("价格弹性", ["价格弹性", "弹性"])
        self.assertEqual([s for s, b in seg if b], ["价格弹性"])

    def test_同一项只加粗第一次(self):
        seg = sp.bold_segments("弹性和弹性", ["弹性"])
        self.assertEqual(sum(1 for _, b in seg if b), 1)

    def test_空文本(self):
        self.assertEqual(sp.bold_segments("", ["x"]), [])


class CjkSpace(unittest.TestCase):
    def test_去掉中文与数字字母之间的空格_加粗照样对得上(self):
        para = {"lead": {"zh": "在美国花费 1000 美元，实际 GDP 增长 3%。", "bold": ["实际 GDP"]}, "body": []}
        b = sp.para_blocks({"1": ch(1, "10:00:00", para=para)})[0]
        text = "".join(s for s, _ in b["lead"])
        self.assertEqual(text, "在美国花费1000美元，实际GDP增长3%。")
        self.assertIn(("实际GDP", True), b["lead"])

    def test_英文句内空格不动(self):
        self.assertEqual(sp._sent_zh({"en": "real GDP rose 3 percent"}), "real GDP rose 3 percent")


class Blocks(unittest.TestCase):
    def test_有para用para_lead与body分开(self):
        para = {"lead": {"zh": "结论句。", "bold": ["结论"]},
                "body": [{"zh": "依据一。", "bold": []}, {"zh": "依据二。", "bold": ["二"]}]}
        b = sp.para_blocks({1: ch(1, "10:00:00", para=para)})[0]
        self.assertEqual("".join(s for s, _ in b["lead"]), "结论句。")
        self.assertEqual("".join(s for s, _ in b["body"]), "依据一。依据二。")
        self.assertIn(("结论", True), b["lead"])

    def test_无para退回sentences不丢章(self):
        b = sp.para_blocks([ch(1, "10:00:00", sentences=[{"zh": "甲。"}, {"zh": "乙。"}, {"en": "C."}])])[0]
        self.assertEqual(b["lead"], [("甲。", False)])
        self.assertEqual(b["body"], [("乙。C.", False)])

    def test_para坏形状也退回(self):
        b = sp.para_blocks([ch(1, "10:00:00", para={"lead": "oops"}, sentences=[{"zh": "甲。"}])])[0]
        self.assertEqual(b["lead"], [("甲。", False)])

    def test_空章不画(self):
        self.assertEqual(sp.para_blocks([ch(1, "10:00:00")]), [])

    def test_按时间排序_临时版标记_时间取HHMM(self):
        bs = sp.para_blocks({2: ch(2, "10:09:00", status="interim", sentences=[{"zh": "后"}]),
                             1: ch(1, "10:01:30", sentences=[{"zh": "前"}])})
        self.assertEqual([b["id"] for b in bs], [1, 2])
        self.assertEqual([b["interim"] for b in bs], [False, True])
        self.assertEqual(bs[0]["t"], "10:01")

    def test_标题zh优先(self):
        b = sp.para_blocks([ch(1, "1", sentences=[{"zh": "x"}], title_zh="")])[0]
        self.assertEqual(b["title"], "T")

    def test_脏数据不抛(self):
        self.assertEqual(sp.para_blocks([None, "x", {"no": "id"}]), [])
        self.assertEqual(sp.para_blocks(None), [])


if __name__ == "__main__":
    unittest.main()


class PaneModel(unittest.TestCase):
    """纲要并进右栏（2026-11-17 Owen）：课务 / 断档 / ❓ 一样不丢。"""

    def para(self, zh="结论。"):
        return {"lead": {"zh": zh, "bold": []}, "body": []}

    def outline(self, **kw):
        o = {"chapters": {0: dict(ch(0, "10:00:00", para=self.para()), t1="10:10:00"),
                          1: dict(ch(1, "10:20:00", para=self.para()), t1="10:30:00")},
             "deadlines": [], "gaps": [], "marks": []}
        o.update(kw)
        return o

    def test_课务置顶最新在上且中文优先(self):
        m = sp.pane_model(self.outline(deadlines=[
            {"t": "10:01:00", "quote": "Essay due Friday", "zh": "论文周五交"},
            {"t": "10:05:00", "quote": "Quiz moved", "zh": "", "changed": True}]))
        self.assertEqual([d["text"] for d in m["deadlines"]], ["Quiz moved", "论文周五交"])
        self.assertTrue(m["deadlines"][0]["changed"])
        self.assertEqual(m["deadlines"][1]["t"], "10:01")

    def test_断档按时间插在章节之间(self):
        m = sp.pane_model(self.outline(gaps=[
            {"t_from": "10:12:00", "t_to": "10:18:00", "state": "dropped"},
            {"t_from": "10:31:00", "t_to": "10:33:00", "state": "failed"}]))
        kinds = [it["kind"] for it in m["items"]]
        self.assertEqual(kinds, ["chapter", "gap", "chapter", "gap"])
        self.assertIn("离线", m["items"][1]["why"])
        self.assertNotIn("离线", m["items"][3]["why"])       # failed ≠ 离线

    def test_标记挂到所在章_不在任何章的按时间单列(self):
        m = sp.pane_model(self.outline(marks=["10:05:30", "10:15:00", "10:25:00", "10:40:00"]))
        chs = [it for it in m["items"] if it["kind"] == "chapter"]
        self.assertEqual(chs[0]["marks"], ["10:05"])
        self.assertEqual(chs[1]["marks"], ["10:25"])
        self.assertEqual([it["kind"] for it in m["items"]], ["chapter", "mark", "chapter", "mark"])

    def test_同一分钟两次标记只写一次(self):
        m = sp.pane_model(self.outline(marks=["10:05:10", "10:05:40"]))
        chs = [it for it in m["items"] if it["kind"] == "chapter"]
        self.assertEqual(chs[0]["marks"], ["10:05"])

    def test_段数只数章节(self):
        m = sp.pane_model(self.outline(gaps=[{"t_from": "10:12:00", "t_to": "10:18:00"}],
                                       marks=["10:40:00"]))
        self.assertEqual(m["n"], 2)

    def test_板书标记(self):
        c = ch(0, "10:00:00", sentences=[{"zh": "看图。", "flag": "board"}])
        m = sp.pane_model({"chapters": {0: c}})
        self.assertTrue(m["items"][0]["board"])

    def test_空纲要与坏数据不崩(self):
        self.assertEqual(sp.pane_model({}), {"n": 0, "deadlines": [], "items": []})
        m = sp.pane_model({"chapters": {}, "deadlines": [None, {"quote": ""}], "gaps": [None],
                           "marks": [None, ""]})
        self.assertEqual(m, {"n": 0, "deadlines": [], "items": []})
