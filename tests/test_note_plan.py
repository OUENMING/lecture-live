"""W1 的判据：`note_plan`（纯函数为主）+ `note_v2.review(plan=...)` 的接线。

⭐ 三条最重要的：
   · `validate` 是**代码钉边界**的地方：行号必须严格递增、过短并掉、过长等分且标题相同、
     0/1 节退回老路 —— 模型给歪的 JSON 不许把笔记切乱；
   · `review(plan=None)` 的 user 内容必须与改动前**逐字一致**（老路不许被动到）；
   · `architect` 全 fail-soft：模型抛异常只少那一次，绝不抛给调用方。

时间戳口径：`lines()` 每秒一行、从 13:16:00 起；测试里的 `at(s)` 就是「第 s 秒」。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import note_plan as np_      # noqa: E402
import note_v2 as nv         # noqa: E402


def at(s: int) -> str:
    """第 s 秒 → `13:MM:SS`（与 `lines()` 的节拍一致）。"""
    return f"13:{16 + s // 60:02d}:{s % 60:02d}"


def lines(n=200):
    return [f"[{at(i)}] sentence about cost {i}." for i in range(n)]


def entries(n=200):
    return [{"ts": at(i), "en": f"sentence about cost {i}.", "asr": ""} for i in range(n)]


def sec(title, start, question="问什么", concepts=None, boundary=""):
    return {"title": title, "question": question, "start": start,
            "concepts": concepts if concepts is not None else ["marginal cost"],
            "boundary": boundary}


class _不等分(unittest.TestCase):
    """多数用例只看合并/排序/接线：把 MAX_LINES 调大，免得 >110 行的节被等分干扰断言。"""
    def setUp(self):
        self._max = np_.MAX_LINES
        np_.MAX_LINES = 10_000

    def tearDown(self):
        np_.MAX_LINES = self._max


class 计划校验(_不等分):
    def test_乱序与重复_start_排序去重(self):
        plan = np_.validate([sec("乙", at(100)), sec("甲", at(0)),
                             sec("丙", at(100)), sec("丁", at(200))], lines(300))
        self.assertEqual([p["title"] for p in plan], ["甲", "乙", "丁"])
        self.assertEqual([(p["lo"], p["hi"]) for p in plan],
                         [(0, 100), (100, 200), (200, 300)])

    def test_坏_start_与缺_title_丢掉(self):
        plan = np_.validate([sec("", at(0)), sec("坏", "abc"),
                             {"title": "无 start"}, "不是 dict",
                             sec("甲", at(0)), sec("乙", at(100)), sec("丙", at(200))],
                            lines(300))
        self.assertEqual([p["title"] for p in plan], ["甲", "乙", "丙"])

    def test_首节强制从第0行(self):
        plan = np_.validate([sec("甲", at(100)), sec("乙", at(200))], lines(300))
        self.assertEqual(plan[0]["lo"], 0)          # 开场的寒暄/课务归第一节
        self.assertEqual(plan[0]["title"], "甲")
        self.assertEqual(plan[-1]["hi"], 300)

    def test_过短并进前一节_标题取行数多的(self):
        # 乙只有 15 行（<MIN_LINES）→ 并进前一节，标题仍是「甲」
        plan = np_.validate([sec("甲", at(0)), sec("乙", at(95)),
                             sec("丙", at(110)), sec("丁", at(220))], lines(300))
        self.assertEqual([p["title"] for p in plan], ["甲", "丙", "丁"])
        self.assertEqual([(p["lo"], p["hi"]) for p in plan],
                         [(0, 110), (110, 220), (220, 300)])

    def test_节数不超过2就退回老路(self):
        # 甲 10 行过短 → 并进后一节 → 只剩一节 → 返回 []
        self.assertEqual(np_.validate([sec("甲", at(0)), sec("乙", at(10))], lines(300)), [])

    def test_过长等分且标题相同(self):
        np_.MAX_LINES = self._max
        plan = np_.validate([sec("甲", at(0)), sec("乙", at(20)), sec("丙", at(260))],
                            lines(300))
        long_ = [p for p in plan if p["title"] == "乙"]
        self.assertEqual(len(long_), 3)                     # 240 行 → 每段 80
        self.assertEqual(len({p["title"] for p in long_}), 1)
        self.assertEqual([p["hi"] - p["lo"] for p in long_], [80, 80, 80])
        self.assertEqual(long_[0]["lo"], 20)
        self.assertEqual(long_[-1]["hi"], 260)
        self.assertEqual({p["n"] for p in long_}, {2})          # 逻辑节号不变
        self.assertEqual([p["part"] for p in long_], [1, 2, 3])
        wo = np_.work_order(plan, long_[1])
        self.assertIn("你只写第 2 节《乙》", wo)
        self.assertIn("第 2 段", wo)
        self.assertEqual(wo.count("《乙》——"), 1)                 # 大纲里同节只列一次
        for p in plan:
            self.assertLessEqual(p["hi"] - p["lo"], np_.MAX_LINES)

    def test_零节一节返回空(self):
        self.assertEqual(np_.validate([], lines(300)), [])
        self.assertEqual(np_.validate({"sections": []}, lines(300)), [])
        self.assertEqual(np_.validate("garbage", lines(300)), [])
        self.assertEqual(np_.validate([sec("只有一节", at(0))], lines(300)), [])
        self.assertEqual(np_.validate([sec("甲", at(0))], []), [])

    def test_输出字段齐全且最多5个概念(self):
        plan = np_.validate([sec("甲", at(0), concepts=[f"c{i}" for i in range(9)]),
                             sec("乙", at(100))], lines(200))
        self.assertEqual(sorted(plan[0]),
                         sorted(["i", "n", "title", "question", "concepts", "boundary", "lo", "hi"]))
        self.assertEqual([p["i"] for p in plan], [1, 2])
        self.assertEqual(len(plan[0]["concepts"]), 5)


class 骨架转计划(unittest.TestCase):
    def test_两章两节(self):
        sk = [{"i": 1, "title": "成本概念", "t0": at(0), "t1": at(100),
               "points": [{"zh": "总成本"}, {"zh": "平均成本"}]},
              {"i": 2, "title": "边际成本", "t0": at(100), "t1": at(200),
               "points": [{"zh": "导数"}, {"zh": "交点"}]}]
        plan = np_.from_skeleton(sk, lines())
        self.assertEqual([p["title"] for p in plan], ["成本概念", "边际成本"])
        self.assertEqual([(p["lo"], p["hi"]) for p in plan], [(0, 100), (100, 200)])
        self.assertEqual(plan[0]["question"], "")
        self.assertEqual(plan[1]["concepts"], ["导数", "交点"])

    def test_空骨架返回空(self):
        self.assertEqual(np_.from_skeleton([], lines()), [])
        self.assertEqual(np_.from_skeleton(None, lines()), [])


class 工作单(_不等分):
    def setUp(self):
        super().setUp()
        self.plan = np_.validate([sec("甲", at(0), boundary="乙 的范围"),
                                  sec("乙", at(100))], lines(200))

    def test_含全局大纲与本节任务(self):
        s = np_.work_order(self.plan, self.plan[1])
        self.assertIn("你只写第 2 节《乙》", s)
        self.assertIn("第 1 节《甲》", s)                  # 全局大纲
        self.assertIn("heading 直接以「乙」这几个字开头", s)
        # boundary 挂在**第一节**上 → 那一节的工作单里要有「留给别的节」
        self.assertIn("这些留给别的节：乙 的范围", np_.work_order(self.plan, self.plan[0]))

    def test_不超上限(self):
        big = np_.validate([sec(f"标题{i}" * 6, at(i * 60)) for i in range(9)],
                           lines(600))
        self.assertTrue(big)
        for it in big:
            self.assertLessEqual(len(np_.work_order(big, it)), np_.ORDER_MAX)

    def test_切块与计划一一对应(self):
        ls = lines(200)
        got = np_.chunks(ls, self.plan)
        self.assertEqual(len(got), len(self.plan))
        self.assertEqual(got[0], ls[:self.plan[0]["hi"]])


class 出计划(_不等分):
    def test_architect_合法_JSON(self):
        calls = []

        def chat(sys_, user, mt):
            calls.append((sys_, user, mt))
            return {"sections": [sec("甲", at(0)), sec("乙", at(100))]}

        plan = np_.architect(lines(200), chat=chat, domain="ECON10790")
        self.assertEqual([p["title"] for p in plan], ["甲", "乙"])
        self.assertEqual(len(calls), 1)                   # 只调一次
        # 11-22：4000 时长课思考 token 吃光预算 → 空计划（12 节里 3 节）；默认不许再降回去
        self.assertGreaterEqual(calls[0][2], 12000)
        self.assertTrue(calls[0][1].startswith(f"Course: ECON10790\n{lines(200)[0]}"))

    def test_architect_异常或空返回空表(self):
        def boom(*a):
            raise RuntimeError("网络")

        self.assertEqual(np_.architect(lines(200), chat=boom), [])
        self.assertEqual(np_.architect(lines(200), chat=lambda *a: {}), [])
        self.assertEqual(np_.architect(lines(200), chat=lambda *a: {"sections": "x"}), [])
        self.assertEqual(np_.architect([], chat=lambda *a: {"sections": []}), [])


class 喂给_review(_不等分):
    """`review(plan=...)`：按节切块、每块带工作单；`plan=None` 与改动前逐字一致。"""

    def _chat(self, seen):
        def chat(sys_, user, mt):
            seen.append(user)
            return {"sections": [{"heading": "[13:16] 甲", "ts": "13:16", "prose": ["讲解。"]}]}
        return chat

    def test_有计划时每块带工作单(self):
        seen: list = []
        plan = np_.validate([sec("甲", at(0)), sec("乙", at(100))], lines(200))
        self.assertEqual(nv.review([], chat=self._chat(seen)), {})   # 空转录直接早退
        got = nv.review(entries(200), chat=self._chat(seen), plan=plan)
        self.assertEqual(len(seen), len(plan))             # 块数 = 计划块数
        for k, u in enumerate(seen, 1):
            self.assertIn(f"你只写第 {k} 节", u)
        self.assertEqual(got.get("chunk_stats", {}).get("plan"), len(plan))

    def test_无计划时不含工作单且记_plan_0(self):
        seen: list = []
        got = nv.review(entries(200), chat=self._chat(seen))
        self.assertTrue(seen)
        for u in seen:
            self.assertNotIn("你只写第", u)
        self.assertEqual(got.get("chunk_stats", {}).get("plan"), 0)

    def test_无计划的_user_与改动前逐字相同(self):
        # 老路口径：固定 70 行/块 + `extra + chunk_budget_line(c)`，再拼转录
        seen: list = []
        es = entries(200)
        nv.review(es, chat=self._chat(seen))
        ls = nv.transcript_lines(es)
        for i, c in enumerate(nv.chunk_lines(ls)):
            want = nv._user_content(c, domain="", materials_block="",
                                    extra=nv.chunk_budget_line(c))
            self.assertEqual(seen[i], want)


if __name__ == "__main__":
    unittest.main()
