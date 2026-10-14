"""碎句合并（`note_merge.py`）的判据。不联网：chat 一律注入替身。

⚠️ `scripts/check.py` 给子进程设了 `CLASSLIVE_NOTE_MERGE=0`（别的收尾用例不许真发请求），
   所以这里每条都在 `_on()` 里把开关临时打开。
"""
import os
import pathlib
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import note_merge as NM                 # noqa: E402
import obsidian_writer as ow            # noqa: E402


def E(ts, en, zh="", star=False):
    return {"ts": ts, "en": en, "zh": zh or f"<{en}>", "asr": en.lower(), "star": star}


def _on():
    return mock.patch.dict(os.environ, {"CLASSLIVE_NOTE_MERGE": "1"})


def fake_chat(log=None, fail=()):
    def chat(key, model, system, user, max_tokens, timeout):
        en = user.rsplit("EN: ", 1)[1]
        if log is not None:
            log.append(en)
        if any(f in en for f in fail):
            raise RuntimeError("boom")
        return {"zh": f"整句[{en}]"}
    return chat


class Runs(unittest.TestCase):
    def test_碎句接到第一行有句号为止(self):
        es = [E("10:00:00", "We start here."), E("10:00:03", "because in the end"),
              E("10:00:12", "taxpayers would not like it."), E("10:00:15", "Fine.")]
        self.assertEqual(NM.runs(es), [(1, 2)])

    def test_carry_超时的省略号也算没说完_且拼接时去掉(self):
        es = [E("10:00:00", "the price of the …"), E("10:00:09", "good goes up.")]
        self.assertEqual(NM.runs(es), [(0, 1)])
        self.assertEqual(NM._join([e["en"] for e in es]), "the price of the good goes up.")

    def test_最多并三行(self):
        es = [E(f"10:00:0{i}", w) for i, w in enumerate(["a b", "c d", "e f", "g h.", "i."])]
        self.assertEqual(NM.runs(es), [(0, 2)])

    def test_时间戳隔太久不并(self):
        es = [E("10:00:00", "so the idea is"), E("10:05:00", "Welcome back.")]
        self.assertEqual(NM.runs(es), [])

    def test_词数超限不并(self):
        es = [E("10:00:00", " ".join(["w"] * 50)), E("10:00:05", " ".join(["v"] * 20) + ".")]
        self.assertEqual(NM.runs(es), [])

    def test_最后一行没说完也不越界(self):
        self.assertEqual(NM.runs([E("10:00:00", "Done."), E("10:00:02", "and then")]), [])


class Merge(unittest.TestCase):
    ES = [E("10:00:00", "We start here."), E("10:00:03", "because in the end", star=True),
          E("10:00:12", "taxpayers would not like it."), E("10:00:15", "Fine.")]

    def test_并成一条_时间戳取第一行_star_取任一行_其余原样(self):
        st = {}
        with _on():
            out = NM.merge_entries(self.ES, "k", "m", stats=st, chat=fake_chat())
        self.assertEqual(len(out), 3)
        self.assertIs(out[0], self.ES[0])
        self.assertIs(out[2], self.ES[3])
        m = out[1]
        self.assertEqual((m["ts"], m["star"], m["merged"]), ("10:00:03", True, 2))
        self.assertEqual(m["en"], "because in the end taxpayers would not like it.")
        self.assertEqual(m["zh"], "整句[because in the end taxpayers would not like it.]")
        self.assertEqual(st, {"runs": 1, "merged": 1, "failed": 0})

    def test_一句一请求_每次只送这一句(self):
        """⭐ 批量时模型会把别句的译文填到这一句的编号下（实测 37/150 句意思变差）。"""
        es = [E("10:00:00", "one two"), E("10:00:02", "three."), E("10:00:04", "four five"),
              E("10:00:06", "six.")]
        log = []
        with _on():
            out = NM.merge_entries(es, "k", "m", chat=fake_chat(log))
        self.assertEqual(sorted(log), ["four five six.", "one two three."])
        self.assertEqual([e["zh"] for e in out], ["整句[one two three.]", "整句[four five six.]"])

    def test_某句失败_那几行原样保留_别的照并(self):
        es = [E("10:00:00", "one two"), E("10:00:02", "three."), E("10:00:04", "four five"),
              E("10:00:06", "six.")]
        st = {}
        with _on():
            out = NM.merge_entries(es, "k", "m", stats=st, chat=fake_chat(fail=("four",)))
        self.assertEqual([e["en"] for e in out], ["one two three.", "four five", "six."])
        self.assertEqual((st["merged"], st["failed"]), (1, 1))

    def test_模型回空或不是字符串_不并(self):
        with _on():
            out = NM.merge_entries(self.ES, "k", "m", chat=lambda *a: {"zh": 3})
        self.assertEqual(out, self.ES)

    def test_开关关掉_没有_key_都原样且不调(self):
        called = []
        chat = fake_chat(called)
        with mock.patch.dict(os.environ, {"CLASSLIVE_NOTE_MERGE": "0"}):
            self.assertIs(NM.merge_entries(self.ES, "k", "m", chat=chat), self.ES)
        with _on():
            self.assertIs(NM.merge_entries(self.ES, None, "m", chat=chat), self.ES)
        self.assertEqual(called, [])

    def test_取消_不再发请求(self):
        c = threading.Event()
        c.set()
        log = []
        with _on():
            out = NM.merge_entries(self.ES, "k", "m", chat=fake_chat(log), cancel=c)
        self.assertEqual((log, out), ([], self.ES))

    def test_总截止_到点没回来的照旧分行_不拖长收尾(self):
        def slow(*a):
            time.sleep(1.0)
            return {"zh": "晚了"}
        st = {}
        t0 = time.monotonic()
        with _on(), mock.patch.object(NM, "DEADLINE_S", 0.2):
            out = NM.merge_entries(self.ES, "k", "m", stats=st, chat=slow)
        self.assertLess(time.monotonic() - t0, 0.8)
        self.assertEqual(out, self.ES)
        self.assertEqual(st.get("timed_out"), 1)


class Wiring(unittest.TestCase):
    SESSION = ("# TESTX · 2026-10-14 · 实时会话日志\n\n"
               "> [!abstract] 10:00:03\n> **EN**: because in the end\n> **ZH**: 因为归根结底\n"
               "> **ASR**: because in the end\n\n"
               "> [!abstract] 10:00:12\n> **EN**: taxpayers would not like it.\n"
               "> **ZH**: 纳税人不会喜欢。\n> **ASR**: taxpayers would not like it.\n\n")

    def test_笔记里是并好的那一句_会话文件一字不动(self):
        with tempfile.TemporaryDirectory() as d, _on(), \
                mock.patch.object(ow, "SESSIONS", pathlib.Path(d) / "sessions"):   # ⚠️ 写端也换
            w = ow.ObsidianWriter(d, "TESTX", mode="yes", api_key="k", polish=False)
            w._review = lambda *a, **k: {}
            w._keypoint_items = lambda *a, **k: []
            sp = pathlib.Path(d) / "s.md"
            sp.write_text(self.SESSION, encoding="utf-8")
            w.session_path, w._n = sp, 2
            with mock.patch.object(NM, "_chat", fake_chat()):
                w.close()
            note = pathlib.Path(w.vault_path).read_text(encoding="utf-8")
            self.assertIn("整句[because in the end taxpayers would not like it.]", note)
            self.assertNotIn("因为归根结底", note)
            self.assertEqual(sp.read_text(encoding="utf-8"), self.SESSION)

    def test_合并抛异常_笔记照写逐行版(self):
        with tempfile.TemporaryDirectory() as d, _on(), \
                mock.patch.object(ow, "SESSIONS", pathlib.Path(d) / "sessions"):   # ⚠️ 写端也换
            w = ow.ObsidianWriter(d, "TESTX", mode="yes", api_key="k", polish=False)
            w._review = lambda *a, **k: {}
            w._keypoint_items = lambda *a, **k: []
            sp = pathlib.Path(d) / "s.md"
            sp.write_text(self.SESSION, encoding="utf-8")
            w.session_path, w._n = sp, 2
            with mock.patch.object(NM, "merge_entries", side_effect=RuntimeError("x")):
                w.close()
            self.assertIn("因为归根结底", pathlib.Path(w.vault_path).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
