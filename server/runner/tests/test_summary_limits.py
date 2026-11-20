#!/usr/bin/env python3
"""`summary_job.tick` 的边界判据 —— W24 任务单剩下的三条。

 H4 假模型 sleep 5 s：tick 仍 < 15 s（模型调用在每课自己的 worker 线程，不占 tick）
 H5 同时最多 20 节：第 21 节记日志跳过
 H7 计费头 seam：`llm_client.headers` 带上 contextvar 里的 `X-ClassLive-Bill-To: <hash16>`
全程 HOME 指向临时目录；假 client / 假模型复用 test_summary_job 里的，不联网、不花钱。
"""
from __future__ import annotations

import os
import sys
import time
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_RUNNER = os.path.dirname(_HERE)
_REPO = os.path.dirname(os.path.dirname(_RUNNER))
for _p in (_REPO, _RUNNER, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import summary_job                     # noqa: E402
from test_summary_job import (FakeClient, TickBase, live_entry,  # noqa: E402
                              mklines, make_fake_chat, poll, drained)


class TestLimits(TickBase):

    def test_h4_tick_fast_with_slow_model(self):
        """假模型每次调用睡 5 s：喂数据 / 下课的 tick 都必须 < 15 s（契约 §7）。"""
        h, sid = "dddd00000000000d", "s_slow"
        c = FakeClient()
        c.live_now = [live_entry(h, sid)]
        c.lines_by[(h, sid)] = mklines(0, 5, 0, 2) + mklines(5, 5, 90, 2)
        c.visible[(h, sid)] = 5
        st = {}
        fake, calls = make_fake_chat(sleep_s=5.0)
        with mock.patch("build_notes._chat_json", fake):
            summary_job.tick(c, st, 0.0)
            s = st["sessions"][(h, sid)]
            drained(s, 5)
            c.visible[(h, sid)] = 10
            t0 = time.time()                 # 这一 tick 触发窗 1 → 原子(5 s)+首段(5 s)
            summary_job.tick(c, st, 20.0)     # 全在 worker 线程里，tick 立刻回
            self.assertLess(time.time() - t0, 15.0)
            t0 = time.time()                 # 模型调用进行中再 tick 一次（拉空）
            summary_job.tick(c, st, 22.0)
            self.assertLess(time.time() - t0, 15.0)
            poll(lambda: calls["chapter"] == 1, 30.0, "首段（慢模型）")
            c.live_now = []                   # 下课：收尾也在自己的线程里
            t0 = time.time()
            summary_job.tick(c, st, 40.0)
            self.assertLess(time.time() - t0, 15.0)
            poll(lambda: any(d.get("final") for _, d in c.puts), 30.0, "final")
            summary_job.tick(c, st, 60.0)
            self.assertEqual(st["sessions"], {})
            fdoc = [d for _, d in c.puts if d["final"]][0]
            self.assertEqual(len(fdoc["chapters"]), 1)
            self.assertTrue(fdoc["chapters"][0]["closed"])

    def test_h5_cap_20_sessions(self):
        """第 21 节课跳过（记日志），state 里只有前 20 节。"""
        keys = [(f"h{i:016x}", f"s{i}") for i in range(21)]
        c = FakeClient()
        c.live_now = [live_entry(k[0], k[1]) for k in keys]
        for k in keys:
            c.lines_by[k] = mklines(0, 2, 0, 2)
            c.visible[k] = 2
        st = {}
        fake, _calls = make_fake_chat()
        with mock.patch("build_notes._chat_json", fake):
            summary_job.tick(c, st, 0.0)
            self.assertEqual(len(st["sessions"]), 20)
            self.assertNotIn(keys[20], st["sessions"])
            self.assertIn(keys[19], st["sessions"])
            # 清场：停掉 20 个 worker 线程，不留空转的 daemon
            for s in st["sessions"].values():
                s.running.clear()
            st["sessions"].clear()

    def test_h7_bill_header_seam(self):
        """与 W23 同一 seam：包一层 `llm_client.headers`，contextvar 里有值就带头。"""
        import llm_client
        self.assertTrue(getattr(llm_client.headers, "_cl_bill", False),
                        "llm_client.headers 没被包上（计费头 seam 缺失）")
        tok = summary_job._BILL.set("0123456789abcdef")
        try:
            h = llm_client.headers("sk-unit")
            self.assertEqual(h.get("X-ClassLive-Bill-To"), "0123456789abcdef")
        finally:
            summary_job._BILL.reset(tok)
        from server.runner import billing
        billing.clear_default()
        h2 = llm_client.headers("sk-unit")
        self.assertNotIn("X-ClassLive-Bill-To", h2)
        # 笔记任务设了进程默认时，总结线程里的 contextvar 仍然优先（Claude 11-20：两个包装曾互相覆盖）
        billing.set_default("aaaaaaaaaaaaaaaa")
        tok = summary_job._BILL.set("0123456789abcdef")
        try:
            self.assertEqual(llm_client.headers("sk-unit")["X-ClassLive-Bill-To"], "0123456789abcdef")
        finally:
            summary_job._BILL.reset(tok)
        self.assertEqual(llm_client.headers("sk-unit")["X-ClassLive-Bill-To"], "aaaaaaaaaaaaaaaa")
        billing.clear_default()


    def test_h8_tick_never_raises(self):
        """契约 §7：永不抛 —— live() 炸 / client 形状坏，tick 都只吞掉打日志。"""
        class Broken:
            def live(self):
                raise RuntimeError("worker 5xx")

        st = {}
        summary_job.tick(Broken(), st, 0.0)     # 不抛就是过
        self.assertEqual(st, {"sessions": {}})   # 只多了空容器，没别的状态


if __name__ == "__main__":
    unittest.main(verbosity=2)
