#!/usr/bin/env python3
"""「一些事儿」在笔记里那一节 + 课前回顾那一层（`obsidian_writer` 的接线判据）。

⭐ 钉两件事（都是"接错线了但看不出来"的类型）：
   ① 笔记里 `### 一些事儿` 那一节：**有决定**时只列「记下的」+「未处理的（未确认）」，
      忽略掉的不写；**没有旁路文件**时走老路（一个字不改）。
   ② `close()` 里那一层真的调了 `recap_fn` 并把结果存进 `paths.recap_state()`；
      `recap_fn` 抛异常时**笔记照写**（第九层不许拖垮底座）。

⚠️ 全离线：`recap_fn` 是桩、`paths.STATE_ROOT` 指到 tempdir、vault 是 tempdir。
"""
import json
import os
import pathlib
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import duty                      # noqa: E402
import obsidian_writer as ow     # noqa: E402
import paths                     # noqa: E402

SESSION = ("> [!abstract] 10:00:03\n"
           "> **EN**: The problem set is due Friday.\n"
           "> **ZH**: 习题集周五截止。\n"
           "> **ASR**: the problem set is due friday\n\n"
           "> [!abstract] 10:00:12\n"
           "> **EN**: These three are very difficult to process.\n"
           "> **ZH**: 这三个很难处理。\n"
           "> **ASR**: these three are very difficult to process\n\n")

DECISIONS = [
    {"id": "d1", "action": "saved", "type": "作业截止", "title": "习题集周五截止",
     "date": "2026-10-16", "time": "", "inferred": True, "phrase": "Friday",
     "t": "10:00:03", "quote": "The problem set is due Friday.",
     "source": "regex", "reminders": "ok", "reason": ""},
    {"id": "d2", "action": "pending", "type": "调课", "title": "本周三停课",
     "date": "2026-10-14", "time": "", "inferred": False, "phrase": "",
     "t": "10:01:00", "quote": "There is no class on Wednesday this week.",
     "source": "model", "reminders": "", "reason": ""},
    {"id": "d3", "action": "ignored", "type": "其他", "title": "这条被忽略了",
     "date": "", "time": "", "inferred": False, "phrase": "",
     "t": "10:02:00", "quote": "The deadline is alive.", "source": "model",
     "reminders": "", "reason": ""},
]


class DutySection(unittest.TestCase):
    def test_有决定时只列记下的与未处理的(self):
        items = {"deadlines": [{"quote": "The problem set is due Friday.",
                                "t": "10:00:03", "source": "regex", "changed": False}],
                 "chapters": [], "atoms": []}
        out = "\n".join(ow._render_outline_section(items, decisions=DECISIONS))
        self.assertIn("### 一些事儿", out)
        self.assertIn("习题集周五截止", out)
        self.assertIn("已记下", out)
        self.assertIn("已写进提醒事项", out)
        self.assertIn("10-16（周五）·推算", out)
        self.assertIn("本周三停课", out)
        self.assertIn("**未确认**", out)
        self.assertNotIn("这条被忽略了", out, "忽略掉的不许出现")
        self.assertNotIn("Deadlines · 课务", out, "改名了")

    def test_提醒事项失败要写明(self):
        bad = [dict(DECISIONS[0], reminders="failed", reason="超时")]
        out = "\n".join(ow._render_outline_section({"deadlines": [], "chapters": []},
                                                   decisions=bad))
        self.assertIn("⚠ 提醒事项没写成", out)
        self.assertIn("超时", out)

    def test_没有决定时走老路(self):
        """老会话 / 纯转录档：`deadline_worthy` 过滤 + 「待确认」，一个字不改。"""
        items = {"deadlines": [
            {"quote": "The deadline is alive.", "t": "13:20:46",
             "source": "model+regex", "changed": False},
            {"quote": "There is no class on Wednesday this week.", "t": "13:10:00",
             "source": "regex", "changed": False},
        ], "chapters": [], "atoms": []}
        out = "\n".join(ow._render_outline_section(items))
        self.assertIn("### 一些事儿", out)
        self.assertIn("no class on Wednesday", out)
        self.assertIn("**待确认**", out)
        self.assertNotIn("alive", out)

    def test_全都忽略了那一节整节消失(self):
        only_ignored = [dict(DECISIONS[2])]
        out = ow._render_outline_section({"deadlines": [], "chapters": []},
                                         decisions=only_ignored)
        self.assertEqual(out, [])

    def test_记账不静默(self):
        items = {"deadlines": [
            {"quote": "The problem set is due Friday.", "t": "10:00:03",
             "source": "regex", "changed": False},
            {"quote": "The deadline is alive.", "t": "10:02:00",
             "source": "model", "changed": False},
        ], "chapters": [], "atoms": []}
        out = "\n".join(ow._render_outline_section(items, decisions=DECISIONS[:2]))
        self.assertIn("另有 1 条", out, "没认到的那条要记账，不许静默")
        self.assertIn("没有时间指向", out, "说清为什么没收录（不是一句「疑似误报」）")


class BoardToNote(unittest.TestCase):
    """端到端（模块级）：旁路文件 → 决定 → 笔记那一节。"""

    def test_旁路文件喂到笔记里(self):
        with tempfile.TemporaryDirectory() as d:
            sess = pathlib.Path(d) / "2026-10-12_SOC10020.md"
            board = duty.Board(day=__import__("datetime").date(2026, 10, 12),
                               path=duty.sidecar_path(sess))
            it = board.offer({"kind": "deadline", "t": "13:52:56",
                              "quote": "There is no class on Wednesday this week.",
                              "src": [7], "source": "model", "changed": True})
            board.decide(it["id"], "saved")
            board.note_reminder(it["id"], True, "")
            loaded = duty.load(duty.sidecar_path(sess))
            out = "\n".join(ow._render_outline_section({"deadlines": [], "chapters": []},
                                                       decisions=loaded))
            self.assertIn("一些事儿", out)
            self.assertIn("There is no class on", out, "标题由原话截出来")
            self.assertIn("已写进提醒事项", out)


class RecapLayer(unittest.TestCase):
    """`close()` 里那一层：真调 `recap_fn`、真存、失败不连累笔记。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cl_recap_layer_")
        self._old_root = paths.STATE_ROOT
        paths.STATE_ROOT = pathlib.Path(self.tmp) / "state"
        paths.STATE_ROOT.mkdir(parents=True, exist_ok=True)
        # ⚠️⚠️ **必须连 `SESSIONS` 一起换掉**（2026-09-29 那条教训，本文件第一版又踩了）：
        #    `ObsidianWriter.__init__` 会 `SESSIONS.mkdir()` 再写一个
        #    `<日期>_<时间>_<课号>.md` —— 只换 vault 的话，跑一次测试就往**真
        #    `sessions/`** 落一个文件（写端隔离是硬边界）。
        self._old_sessions = ow.SESSIONS
        ow.SESSIONS = pathlib.Path(self.tmp) / "sessions"
        ow.SESSIONS.mkdir(parents=True, exist_ok=True)
        self.vault = pathlib.Path(self.tmp) / "vault"
        (self.vault / "Lectures").mkdir(parents=True, exist_ok=True)
        self.sess = pathlib.Path(self.tmp) / "sessions" / "2026-10-15_131600_ECON10790.md"
        self.sess.parent.mkdir(parents=True, exist_ok=True)
        self.sess.write_text(SESSION, encoding="utf-8")

    def tearDown(self):
        paths.STATE_ROOT = self._old_root
        ow.SESSIONS = self._old_sessions
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _writer(self, fn):
        w = ow.ObsidianWriter(str(self.vault), "ECON10790", mode="ask",
                              recap_fn=fn)
        w.session_path = self.sess
        # ⚠️ 必须真喂两句：`close()` 有一条「零句又无问答」的早退，不喂就到不了
        #    第九层（那条早退是给麦克风故障那种课准备的，别绕开它）。
        w.append("The problem set is due Friday.", "习题集周五截止。",
                 raw="the problem set is due friday")
        w.append("These three are very difficult to process.", "这三个很难处理。",
                 raw="these three are very difficult to process")
        return w

    def test_真的调了并存下来(self):
        seen = {}

        def fn(payload):
            seen.update(payload)
            return {"course": "ECON10790", "date": "10-15",
                    "points": [{"name": "停业价格", "explain": "价格低于它就停产。"}],
                    "stars": ["边际成本就是多生产一件要多花的钱。"]}

        w = self._writer(fn)
        w.close(ask=lambda n: True)
        self.assertEqual(seen.get("course"), "ECON10790")
        self.assertIn("very difficult", seen.get("tail") or "")
        self.assertEqual(seen.get("session"),
                         "2026-10-15_131600_ECON10790.md")
        self.assertIsInstance(seen.get("chapters"), dict)
        saved = json.loads(paths.recap_state("ECON10790").read_text(encoding="utf-8"))
        self.assertEqual(saved["points"][0]["name"], "停业价格")
        self.assertEqual(saved["stars"], ["边际成本就是多生产一件要多花的钱。"])

    def test_没传recap_fn就不生成(self):
        w = self._writer(None)
        w.close(ask=lambda n: True)
        self.assertFalse(paths.recap_state("ECON10790").exists())

    def test_抛了也不连累笔记(self):
        def boom(_payload):
            raise RuntimeError("炸了")

        w = self._writer(boom)
        msg = w.close(ask=lambda n: True)
        self.assertIn("笔记已保存", msg or "")
        self.assertFalse(paths.recap_state("ECON10790").exists())


if __name__ == "__main__":
    unittest.main()
