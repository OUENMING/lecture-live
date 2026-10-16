#!/usr/bin/env python3
"""🩹 漏译句课后补译（`backfill.py` + `obsidian_writer.close()`）的判据 —— **全离线**。

    ClassLive.app/Contents/MacOS/python tests/test_backfill.py

## 钉住了什么

1. ⭐ `missing()` 只认「**EN 与 ZH 都空、ASR 有字**」—— 只英·校档（有 EN 没 ZH）
   与纯转录档的条目**一条都不碰**（那是用户的选择，不是漏译）。
2. ⭐ 补出来的中文进**课后笔记**、带 ` ·补` 标记；`sessions/<名>.md` **字节不变**
   （三方契约：`obsidian_writer` 写 / `_parse` 读回 / `cl last` grep）。
3. ⭐ 补译失败（没网 / 到点）只是少几句中文：**笔记照常生成**、不外抛，`done` 如实为 0。
4. ⭐ 已经全是中文的课 `need` 为 0，一个请求都不发（不许白花钱）。

⚠️ `chat` 是注入的替身（打在 `polish._chat` 上 —— `backfill` 复用 `polish_entries`），
   **不联网**；写端（`ow.SESSIONS`）与 vault 都换到临时目录。
⚠️ `check.py` 给子进程设 `CLASSLIVE_NOTE_MERGE=0`（收尾的碎句合并会联网），这里也显式
   按住它 —— 本文件要单独跑也一个请求都不发。
"""
from __future__ import annotations

import os
import pathlib
import re
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import backfill as BF                                                 # noqa: E402
import obsidian_writer as ow                                          # noqa: E402
import polish                                                         # noqa: E402


def fake_chat(calls=None, fail=False, same_en=False):
    """按 user 里的 `[序号]` 回一份完整答案；`calls` 收下每次请求的 prompt。

    `same_en=True` 模拟"模型把 ASR 原样抄回来"（`EN:` 那一段逐字照抄）。
    """
    def chat(key, model, system, user, max_tokens, timeout):
        if calls is not None:
            calls.append(user)
        if fail:
            raise RuntimeError("没网")
        ids = [int(x) for x in re.findall(r"^\[(\d+)\]", user, re.M)]
        src = re.findall(r"^\[\d+\] [\d:]+ \| EN: (.*)$", user, re.M)
        return {"items": [{"i": i,
                           "en": (src[i] if same_en and i < len(src)
                                  else f"fixed {i} sentence."),
                           "zh": f"补译{i}。"} for i in ids]}
    return chat


def _session(text: str):
    """(会话文件里的) 三个条目：一句正常、两句**只有 ASR**。"""
    return ("# TESTX · 2026-10-15 · 实时会话日志\n\n"
            "> [!abstract] 10:00:03\n"
            "> **EN**: first one.\n> **ZH**: 第一句。\n> **ASR**: first one.\n\n"
            + text)


ONLY_ASR = ("> [!abstract] 10:00:12\n> **ASR**: the lattice level\n\n"
            "> [!abstract] 10:00:20\n> **ASR**: how we measure the statistics\n\n")


def E(ts, en="", zh="", asr="", star=False):
    return {"ts": ts, "en": en, "zh": zh, "asr": asr, "star": star}


class Missing(unittest.TestCase):
    def test_只有ASR的才算漏译(self):
        es = [E("10:00:00", asr="only asr here"),
              E("10:00:01", en="fixed.", zh="修好了。", asr="fixd"),
              E("10:00:02", en="english only.", asr="english only"),   # 只英·校档
              E("10:00:03", zh="只有中文。", asr="zh only"),            # 不正常但也不补
              E("10:00:04", asr="   "),
              E("10:00:05", asr="")]
        self.assertEqual(BF.missing(es), [0])


class Entries(unittest.TestCase):
    ES = [E("10:00:00", en="first one.", zh="第一句。", asr="first one."),
          E("10:00:12", asr="the lattice level"),
          E("10:00:20", asr="how we measure the statistics")]

    def run_it(self, **kw):
        with mock.patch.object(polish, "_chat", kw.pop("chat", fake_chat())):
            return BF.backfill_entries(self.ES, kw.pop("key", "k"), "m", **kw)

    def test_补出中文和英文_入参一个字不动(self):
        out, st = self.run_it()
        self.assertEqual(st, {"need": 2, "done": 2})
        self.assertEqual(out[1]["zh"], "补译0。")
        self.assertEqual(out[1]["en"], "fixed 0 sentence.")
        self.assertTrue(out[1]["bf"], "没有 bf 标记 —— 笔记里认不出它是补的")
        self.assertEqual(out[2]["zh"], "补译1。")
        # 不需要补的那条**原对象**原样传回来（不是拷贝）
        self.assertIs(out[0], self.ES[0])
        # ⭐ 入参（= 调用方手里那份）一个字段都不许变
        self.assertEqual(self.ES[1], E("10:00:12", asr="the lattice level"))

    def test_矫正英文与ASR逐字相同_不写EN行(self):
        """模型把 ASR 原样抄回来时，笔记里不该多一行和 ASR 一模一样的 EN。"""
        out, st = self.run_it(chat=fake_chat(same_en=True))
        self.assertEqual(st["done"], 2)
        self.assertEqual(out[1]["en"], "", "与 ASR 相同的 EN 被写进去了（同一行 noise）")
        self.assertEqual(out[1]["zh"], "补译0。")

    def test_没网_原样返回且不外抛(self):
        out, st = self.run_it(chat=fake_chat(fail=True))
        self.assertEqual(out, self.ES)
        self.assertIs(out[1], self.ES[1], "失败时那几条必须原样传回来")
        self.assertEqual(st, {"need": 2, "done": 0})

    def test_到点就不发请求(self):
        calls: list = []
        t0 = time.monotonic()
        out, st = self.run_it(chat=fake_chat(calls), deadline_s=-1.0)
        self.assertLess(time.monotonic() - t0, 1.0, "到点后还在等")
        self.assertEqual((calls, st["done"]), ([], 0))
        self.assertEqual(st["need"], 2)
        self.assertEqual(out, self.ES)

    def test_取消_不再发请求(self):
        calls: list = []
        c = threading.Event()
        c.set()
        out, st = self.run_it(chat=fake_chat(calls), cancel=c)
        self.assertEqual((calls, st["done"]), ([], 0))
        self.assertEqual(out, self.ES)

    def test_没有_key或没有漏译句_一个请求都不发(self):
        def boom(*a):
            raise AssertionError("不该发请求")
        with mock.patch.object(polish, "_chat", boom):
            out, st = BF.backfill_entries(self.ES, None, "m")
            self.assertIs(out, self.ES)
            self.assertEqual(st, {"need": 0, "done": 0})
            done = [dict(e, zh="已经有了。") for e in self.ES]
            out2, st2 = BF.backfill_entries(done, "k", "m")
            self.assertIs(out2, done)
            self.assertEqual(st2, {"need": 0, "done": 0})


class CloseWiring(unittest.TestCase):
    """收尾那一步：笔记里补上中文 + ` ·补`；`sessions/` 一个字节不动。"""

    def run_close(self, fn=True, chat=None):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.dict(os.environ, {"CLASSLIVE_NOTE_MERGE": "0"}), \
                mock.patch.object(ow, "SESSIONS", pathlib.Path(d) / "sessions"):
            kw = {}
            if fn is not None:
                def _bf(entries):                     # 生产那条路（`main._backfill_for`）
                    with mock.patch.object(polish, "_chat", chat or fake_chat()):
                        return BF.backfill_entries(entries, "k", "m")
                kw["backfill_fn"] = _bf if fn is True else fn
            w = ow.ObsidianWriter(d, "TESTX", mode="yes", api_key="k",
                                  polish=False, **kw)
            w._review = lambda *a, **k: {}
            w._keypoint_items = lambda *a, **k: []
            sp = pathlib.Path(d) / "s.md"
            sp.write_text(_session(ONLY_ASR), encoding="utf-8")
            w.session_path, w._n = sp, 3
            before = sp.read_bytes()
            w.close()
            note = pathlib.Path(w.vault_path).read_text(encoding="utf-8")
            return note, before, sp.read_bytes()

    def test_笔记里补出中文和标记_会话文件字节不变(self):
        note, before, after = self.run_close()
        self.assertIn("补译0。", note)
        self.assertIn("补译1。", note)
        self.assertIn("fixed 0 sentence.", note)
        self.assertEqual(note.count(" ·补"), 2, "标记数不对（每条补译的中文各一个）")
        self.assertEqual(after, before, "sessions/ 被改写了（三方契约！）")
        self.assertEqual(after.decode("utf-8"), _session(ONLY_ASR))

    def test_没网_笔记照常生成_没有标记(self):
        note, before, after = self.run_close(chat=fake_chat(fail=True))
        self.assertIn("the lattice level", note, "笔记没生成 / 连 ASR 都丢了")
        self.assertIn("## 📜 完整逐句转录", note)
        self.assertNotIn(" ·补", note)
        self.assertNotIn("补译0。", note)
        self.assertEqual(after, before)

    def test_补译抛异常_笔记照常生成(self):
        def boom(_entries):
            raise RuntimeError("补译自己崩了")
        note, before, after = self.run_close(fn=boom)
        self.assertIn("the lattice level", note)
        self.assertNotIn(" ·补", note)
        self.assertEqual(after, before)

    def test_不给backfill_fn_逐字不变(self):
        """`None` = 这个功能关着（老调用方 / 判据）—— 笔记里一个字都不该变。"""
        note, before, after = self.run_close(fn=None)
        self.assertNotIn(" ·补", note)
        self.assertIn("> > **ASR**: the lattice level", note)
        self.assertEqual(after, before)


class Report(unittest.TestCase):
    """`backfill` 那一格在测试模式报告里的落点（`main._backfill_for` 记的就是它）。"""

    def test_补译计数进报告(self):
        import json
        import testmode as tm
        st = {"need": 2, "done": 1}
        t = tm.TestSession(pathlib.Path(tempfile.mkdtemp()) / "x.md")
        t.note_block("backfill", st)
        rep = json.loads(pathlib.Path(t.finish(bundle=False)).read_text(encoding="utf-8"))
        self.assertEqual(rep["blocks"]["backfill"], st)
        self.assertNotIn("bad_block", [e["kind"] for e in rep["events"]],
                         "块名没登记 —— `note_block` 会静默丢掉它")


if __name__ == "__main__":
    unittest.main(verbosity=2)
