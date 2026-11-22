"""note_ground：未核对断言 → 对照转录原话（2026-11-21 第三轮）。零网络：chat 全是假的。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import note_ground as g  # noqa: E402

LINES = [
    "[13:14:05] so UCD has a huge sports centre",
    "[13:14:11] there are about 136 clubs in UCD",
    "[13:27:02] interviewing 500 people would take five to seven years",
    "[13:30:10] a PhD usually has 40 to 50 people",
]


def _review(claims):
    return {"sections": [{"heading": "甲", "prose": ["课上讲了一些数字。"]}],
            "checks": [{"claim": c, "status": "unchecked", "how": ""} for c in claims]}


class 数字抽取(unittest.TestCase):
    def test_阿拉伯与中文数字(self):
        self.assertEqual(g.numbers_in("约 136 个俱乐部"), {"136"})
        self.assertEqual(g.numbers_in("需要五到七年"), {"5", "7"})
        self.assertEqual(g.numbers_in("四十到五十人"), {"40", "50"})
        self.assertEqual(g.numbers_in("一百三十六"), {"136"})
        self.assertEqual(g.numbers_in("总量 30,600"), {"30600"})

    def test_单个一二不当数(self):
        self.assertEqual(g.numbers_in("这一节讲二者的关系"), set())


class 找原话(unittest.TestCase):
    def test_数字直接对上(self):
        hits = g.find_evidence("UCD 约有 136 个俱乐部", LINES)
        self.assertTrue(hits and "136 clubs" in hits[0])

    def test_中文数字对英文拼写(self):
        hits = g.find_evidence("访谈 500 人需要五到七年", LINES)
        self.assertIn("five to seven", hits[0])

    def test_带前后行_两行切开的一句能拼回来(self):
        hits = g.find_evidence("访谈 500 人需要五到七年",
                               ["[13:27:50] if I interview 500 people", "[13:27:56] it is five to seven years"])
        self.assertIn("500 people", hits[0])
        self.assertIn("five to seven", hits[0])

    def test_没数字_按英文词找(self):
        hits = g.find_evidence("访谈数据不能放进 Stata", ["[13:40:00] you cannot put interviews into Stata"])
        self.assertTrue(hits and "Stata" in hits[0])

    def test_纯中文没数字就不找(self):
        self.assertEqual(g.find_evidence("社会科学不做假设检验", LINES), [])

    def test_一三六不误中十三(self):
        self.assertEqual(g.find_evidence("约 13 个", LINES), [])


class 判支持(unittest.TestCase):
    def test_支持_变已核_带原话(self):
        rv = _review(["UCD 约有 136 个俱乐部"])
        chat = lambda s, u, m: {"items": [{"i": 0, "verdict": "supported", "ts": "13:14:11",
                                           "quote": "there are about 136 clubs in UCD"}]}
        rows, ok, diff, _ = g.ground_unchecked(rv["checks"], rv, LINES, chat=chat)
        self.assertEqual((ok, diff), (1, 0))
        self.assertEqual(rows[0]["status"], "ok")
        self.assertIn("136 clubs", rows[0]["how"])
        self.assertEqual(rv["checks"][0]["status"], "unchecked", "不许原地改输入")

    def test_说法不同_仍未核_永不mismatch(self):
        rv = _review(["UCD 约有 136 个俱乐部"])
        chat = lambda s, u, m: {"items": [{"i": 0, "verdict": "different", "ts": "13:14:11",
                                           "quote": "there are about 136 clubs"}]}
        rows, ok, diff, _ = g.ground_unchecked(rv["checks"], rv, LINES, chat=chat)
        self.assertEqual((ok, diff), (0, 1))
        self.assertEqual(rows[0]["status"], "unchecked")
        self.assertIn("待你确认", rows[0]["how"])

    def test_编造的原话不算(self):
        rv = _review(["UCD 约有 136 个俱乐部"])
        chat = lambda s, u, m: {"items": [{"i": 0, "verdict": "supported", "ts": "13:14:11",
                                           "quote": "the professor said 136 clubs exist"}]}
        rows, ok, _, _ = g.ground_unchecked(rv["checks"], rv, LINES, chat=chat)
        self.assertEqual(ok, 0)
        self.assertEqual(rows[0]["status"], "unchecked")

    def test_支持但没给原话不算(self):
        rv = _review(["UCD 约有 136 个俱乐部"])
        chat = lambda s, u, m: {"items": [{"i": 0, "verdict": "supported", "quote": ""}]}
        _, ok, _, _ = g.ground_unchecked(rv["checks"], rv, LINES, chat=chat)
        self.assertEqual(ok, 0)

    def test_模型自己出的题不对照(self):
        rv = {"sections": [{"heading": "甲", "prose": ["讲解。"],
                            "example": {"title": "例", "prompt": "设有 136 个俱乐部",
                                        "answer": "136 个俱乐部 = 答案"}}],
              "checks": [{"claim": "136 个俱乐部 = 答案", "status": "unchecked"}]}
        called = []
        chat = lambda s, u, m: called.append(1) or {"items": []}
        g.ground_unchecked(rv["checks"], rv, LINES, chat=chat)
        self.assertEqual(called, [], "例题的断言不该送去对照")

    def test_已核的行不动_调用炸了原样返回(self):
        rv = _review(["UCD 约有 136 个俱乐部"])
        rv["checks"].append({"claim": "访谈 500 人需要五到七年", "status": "ok"})

        def boom(s, u, m):
            raise RuntimeError("x")
        rows, ok, diff, _ = g.ground_unchecked(rv["checks"], rv, LINES, chat=boom)
        self.assertEqual((ok, diff), (0, 0))
        self.assertEqual([r["status"] for r in rows], ["unchecked", "ok"])


if __name__ == "__main__":
    unittest.main()
