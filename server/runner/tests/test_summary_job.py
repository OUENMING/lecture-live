#!/usr/bin/env python3
"""`summary_job.tick` 的判据 —— 网页版实时总结支（契约 §7 / §5，W24）。

钉住：
 H1 一节课全流程：出章 closed=false → 封章 closed=true；**模型调用次数写死**
    （REFRESH_GAP_S=inf → 每章 2 次章节调用；只判「没抛」会假绿 —— W16 §2.1 的教训）
 H2 两节课并行：游标各自推进、章不吃对方的句子
 H3 课被删（SessionGone）→ 直接丢，不写 final
全程 HOME 指向临时目录；假 client / 假模型写在本文件里（契约 §7），不联网、不花钱。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_RUNNER = os.path.dirname(_HERE)
_REPO = os.path.dirname(os.path.dirname(_RUNNER))
for _p in (_REPO, _RUNNER):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import summary_job  # noqa: E402  —— 路径排好后再 import

ATOM_REPLY = {"topic": "Price elasticity", "topic_zh": "需求的价格弹性",
              "points": [{"text": "law of demand", "zh": "需求定律",
                          "src": [0], "kind": "要点"}]}
CHAPTER_REPLY = {"title": "Elasticity", "title_zh": "弹性",
                 "sentences": [{"en": "Demand slopes down", "zh": "需求曲线向下",
                                "src": [1]}],
                 "para": {"lead": {"en": "Lead", "zh": "第一段结论", "src": [1]},
                          "body": []}}
TOP_KEYS = {"session_id", "updated_at", "final", "chapters"}          # 契约 §5
CH_KEYS = {"idx", "title", "start_seq", "end_seq", "closed", "summary", "points"}


class FakeClient:
    """契约 §3 签名的假 client —— 只实现 tick 用到的那三个方法。"""

    def __init__(self):
        self.live_now, self.lines_by, self.puts = [], {}, []
        self.gone = set()
        self.visible = {}          # (hash, sid) -> 已上账的行数（模拟“逐句推流”的时间轴）
        self.after_log = {}       # (hash, sid) -> 每次收到的游标（H2 断言用）

    def live(self):
        return [dict(x) for x in self.live_now]

    def lines(self, h, sid, after=0):
        self.after_log.setdefault((h, sid), []).append(after)
        if (h, sid) in self.gone:
            raise summary_job.SessionGone("session deleted")
        seen = self.visible.get((h, sid), 0)
        return [l for l in self.lines_by[(h, sid)][:seen] if l["seq"] > after]

    def put_summary(self, h, sid, doc):
        self.puts.append(((h, sid), doc))


def make_fake_chat(sleep_s=0.0):
    """假模型：patch `build_notes._chat_json`（实时总结真正调模型的出口，main.py 同款接线）。"""
    import copy
    calls = {"atom": 0, "chapter": 0, "all": 0}

    def fake(key, model, system, user, max_tokens, temperature=0.2,
             timeout=180.0, call_class=""):
        if sleep_s:
            time.sleep(sleep_s)
        calls["all"] += 1
        cls = call_class or ("chapter" if max_tokens >= 2000 else "atom")
        calls[cls] += 1
        return copy.deepcopy(ATOM_REPLY if cls == "atom" else CHAPTER_REPLY)

    return fake, calls


def mklines(base, n, t0, step, tag="w"):
    """一批句子：seq 从 base+1 起、课内秒 t0 起步长 step（契约 §3 行形状）。"""
    return [{"seq": base + i + 1, "t": t0 + i * step, "en": f"{tag} line {i + 1}",
             "zh": f"{tag} 第{i + 1}句"} for i in range(n)]


def live_entry(h, sid):
    return {"hash": h, "session_id": sid, "course": "ECON", "tz": "Europe/Dublin",
            "started_at": "2026-11-20T09:00:00Z",
            "last_line_at": "2026-11-20T09:10:00Z", "high_water": 99}


def poll(fn, timeout=15.0, what=""):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if fn():
            return
        time.sleep(0.05)
    raise AssertionError(f"超时等待 {what}")


def drained(s, n):
    """等 worker 把队列消化进当前窗口（自锚定 t0；下一批到达才会闭窗 —— 生产里两 tick
    隔 20 s，测试里是微秒级，必须显式等）。白盒：读 `_q` / `_buf`。"""
    poll(lambda: s.summ._q.empty() and len(s.summ._buf) == n, what=f"{ n } 句入窗")


class TickBase(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="cl-sum-home-")
        self._old = {k: os.environ.get(k) for k in ("HOME", "DEEPSEEK_API_KEY")}
        os.environ["HOME"] = self.home
        os.environ["DEEPSEEK_API_KEY"] = "cl-runner-test"

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.home, ignore_errors=True)


class TestLifecycle(TickBase):

    def test_h1_lifecycle_and_call_counts(self):
        h, sid = "a1b2c3d4e5f60718", "s_one"
        c = FakeClient()
        c.live_now = [live_entry(h, sid)]
        c.lines_by[(h, sid)] = (mklines(0, 5, 0, 2) + mklines(5, 5, 90, 2)
                                + mklines(10, 5, 180, 2) + mklines(15, 5, 270, 2))
        c.visible[(h, sid)] = 5
        st = {}
        fake, calls = make_fake_chat()
        with mock.patch("build_notes._chat_json", fake):
            summary_job.tick(c, st, 0.0)        # 开课 + 第 1 批（窗口未满，无调用）
            s = st["sessions"][(h, sid)]
            self.assertEqual(s.after, 5)
            drained(s, 5)
            c.visible[(h, sid)] = 10
            summary_job.tick(c, st, 20.0)       # 第 2 批 → 窗 1 闭合 → 原子 + 首段
            poll(lambda: calls["chapter"] == 1, what="首段")
            self.assertEqual(s.after, 10)
            summary_job.tick(c, st, 40.0)       # 章节有变化 → 写回 closed=false
            self.assertEqual(len(c.puts), 1)
            (ph, ps), doc = c.puts[0]
            self.assertEqual((ph, ps), (h, sid))
            self.assertEqual(set(doc), TOP_KEYS)
            ch = doc["chapters"]
            self.assertEqual(len(ch), 1)
            self.assertEqual(set(ch[0]), CH_KEYS)
            self.assertFalse(doc["final"])
            self.assertEqual((ch[0]["idx"], ch[0]["title"]), (1, "弹性"))
            self.assertEqual((ch[0]["start_seq"], ch[0]["end_seq"]), (1, 10))
            self.assertFalse(ch[0]["closed"])
            self.assertEqual(ch[0]["summary"], "第一段结论")
            self.assertEqual(ch[0]["points"], ["需求曲线向下"])
            c.visible[(h, sid)] = 15
            summary_job.tick(c, st, 60.0)       # 第 3 批 → 入窗未闭合
            drained(s, 5)
            c.visible[(h, sid)] = 20
            summary_job.tick(c, st, 80.0)       # 第 4 批 → 窗 2 闭合，同章吸收
            poll(lambda: calls["atom"] == 2 and s.summ.pending == 0, what="第二窗")
            # ⭐ 反证锚点：刷新已关（REFRESH_GAP_S=inf）→ 第二窗吸收后章节调用仍是 1
            self.assertEqual(calls["chapter"], 1)
            c.live_now = []                      # 下课
            t0 = time.time()
            summary_job.tick(c, st, 100.0)
            self.assertLess(time.time() - t0, 15.0)
            poll(lambda: any(d.get("final") for _, d in c.puts), 30.0, "final")
            finals = [d for _, d in c.puts if d["final"]]
            self.assertEqual(len(finals), 1)
            fch = finals[0]["chapters"][0]
            self.assertTrue(finals[0]["final"] and fch["closed"])
            self.assertEqual((fch["start_seq"], fch["end_seq"]), (1, 20))
            self.assertEqual(fch["summary"], "第一段结论")
            # ⭐ 调用次数从桌面判据推出来写死（不是「没抛就算」）：
            #    原子 2 窗 = 2 次；章节 = 首段 1 + 封章 1 = 每章 2 次
            self.assertEqual(calls["atom"], 2)
            self.assertEqual(calls["chapter"], 2)
            self.assertEqual(calls["all"], 4)
            summary_job.tick(c, st, 120.0)      # 收尾完 → 从 state 移除
            self.assertEqual(st["sessions"], {})
            self.assertEqual(len([d for _, d in c.puts if not d["final"]]), 1)

    def test_h2_two_sessions_parallel(self):
        ha, sa = "aaaa00000000000a", "s_a"
        hb, sb = "bbbb00000000000b", "s_b"
        c = FakeClient()
        c.live_now = [live_entry(ha, sa), live_entry(hb, sb)]
        c.lines_by[(ha, sa)] = mklines(0, 5, 0, 2) + mklines(5, 5, 90, 2)
        c.lines_by[(hb, sb)] = mklines(0, 12, 0, 2)
        c.visible[(ha, sa)] = 5
        c.visible[(hb, sb)] = 12
        st = {}
        fake, calls = make_fake_chat()
        with mock.patch("build_notes._chat_json", fake):
            summary_job.tick(c, st, 0.0)
            sva, svb = st["sessions"][(ha, sa)], st["sessions"][(hb, sb)]
            self.assertEqual((sva.after, svb.after), (5, 12))
            drained(sva, 5)
            drained(svb, 12)
            c.visible[(ha, sa)] = 10
            summary_job.tick(c, st, 20.0)       # 只有 A 有新句 → A 出章
            poll(lambda: calls["chapter"] == 1, what="A 首段")
            summary_job.tick(c, st, 40.0)       # 只有 A 写回
            self.assertEqual([k for k, _ in c.puts], [(ha, sa)])
            self.assertEqual(c.puts[0][1]["chapters"][0]["end_seq"], 10)
            self.assertEqual((sva.after, svb.after), (10, 12))
            c.live_now = []                     # 都下课
            summary_job.tick(c, st, 60.0)
            poll(lambda: len(c.puts) >= 3, 30.0, "两个 final")
            finals = {k: d for k, d in c.puts if d["final"]}
            self.assertEqual(set(finals), {(ha, sa), (hb, sb)})
            self.assertEqual(finals[(ha, sa)]["chapters"][0]["end_seq"], 10)
            self.assertEqual(finals[(hb, sb)]["chapters"][0]["end_seq"], 12)
            self.assertTrue(all(d["chapters"][0]["closed"] for d in finals.values()))
            summary_job.tick(c, st, 80.0)
            self.assertEqual(st["sessions"], {})
            # 游标轨迹各自推进：A 0→5→10；B 后续 tick 拉空（游标停在 12）
            self.assertEqual(c.after_log[(ha, sa)], [0, 5, 10])
            self.assertEqual(c.after_log[(hb, sb)], [0, 12, 12])

    def test_h3_session_gone_dropped_without_final(self):
        h, sid = "cccc00000000000c", "s_gone"
        c = FakeClient()
        c.live_now = [live_entry(h, sid)]
        c.lines_by[(h, sid)] = mklines(0, 5, 0, 2)
        c.visible[(h, sid)] = 5
        st = {}
        fake, _calls = make_fake_chat()
        with mock.patch("build_notes._chat_json", fake):
            summary_job.tick(c, st, 0.0)
            c.gone.add((h, sid))
            summary_job.tick(c, st, 20.0)       # 还在 live 里 → 喂时 SessionGone → 丢
            self.assertTrue(st["sessions"][(h, sid)].done)
            c.live_now = []
            summary_job.tick(c, st, 40.0)
            self.assertEqual(st["sessions"], {})
            self.assertEqual(c.puts, [])        # 不收尾、不写 final


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestDirtyRace(unittest.TestCase):
    """OCR E2：put_summary 往返期间又来的新章节（emit 置 dirty）不许被 put 之后的清零抹掉。"""

    def test_emit_during_put_keeps_dirty(self):
        import threading

        class S:
            def __init__(self, h, sid):
                self.hash, self.sid = h, sid
                self.lock = threading.Lock()
                self.dirty, self.done, self.finishing = True, False, False

        made = {}

        def mk(h, sid):
            made["s"] = S(h, sid)
            return made["s"]

        class C:
            puts = 0

            def live(self):
                return [live_entry("a" * 16, "s_race")]

            def put_summary(self, h, sid, doc):
                C.puts += 1
                with made["s"].lock:
                    made["s"].dirty = True        # 模拟 worker 线程此刻 emit 了新章

        st = {}
        with mock.patch.object(summary_job, "_Session", mk), \
                mock.patch.object(summary_job, "_feed", lambda c, s: None), \
                mock.patch.object(summary_job, "_doc", lambda s, final: {}):
            summary_job.tick(C(), st, time.time())
        self.assertEqual(C.puts, 1)
        self.assertTrue(made["s"].dirty, "put 期间的新章被抹掉了")
