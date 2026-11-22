# test_note_job.py —— run_note 成功路径（假模型：patch 桌面真正的模型出口 llm_client.chat_json）
# 与失败路径（缺通行码 / SessionGone / 租约丢）。全程 HOME 指向临时目录（读写两端一起隔离）。
# ⚠️ HOME 在 import 桌面模块之前设好（paths.STATE_ROOT import 时算）；与其他 runner 测试共用。
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

_HOME = os.environ.get("CLASSLIVE_RUNNER_TEST_HOME") or tempfile.mkdtemp(
    prefix="cl-runner-test-")
os.environ["CLASSLIVE_RUNNER_TEST_HOME"] = _HOME
os.environ["HOME"] = _HOME

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import llm_client                                   # noqa: E402
from server.runner import note_job                  # noqa: E402
from server.runner.client import SessionGone        # noqa: E402
from server.runner.tests import fakes               # noqa: E402

_CRED = Path(_HOME) / ".classlive" / "credentials"
#: ⚠️ 用 sk- 形假 key：cl- 通行码会让 keypoints 的 Jev 走真 Worker（测试不联网）。
_TEST_KEY = "sk-test-runner-key"


class FakeClient:
    """假 Worker client：只要 lines / complete_ok / complete_fail。"""

    def __init__(self, rows=None, err=None):
        self.rows = rows or []
        self.err = err
        self.oks: list = []
        self.fails: list = []
        self.lines_calls = 0

    def lines(self, hash, session_id, after=0):
        self.lines_calls += 1
        if self.err:
            raise self.err
        return [r for r in self.rows if int(r.get("seq") or 0) > int(after)]

    def complete_ok(self, job, md, html, meta):
        self.oks.append({"job": dict(job), "md": md, "html": html, "meta": dict(meta)})

    def complete_fail(self, job, reason):
        self.fails.append(str(reason))


def _fake_chat(calls):
    """桌面管道真正调模型的出口（obsidian_writer._chat/_chat_meta、note_merge、polish）
    全走 `llm_client.chat_json` —— patch 它，返回够桌面 JSON 解析的空复习层。"""
    def chat(key, model, system, user, **kw):
        calls.append({"model": model, "call_class": kw.get("call_class") or "",
                      "has_bill": "X-ClassLive-Bill-To" in llm_client.headers(key)})
        obj = {"sections": []}
        if kw.get("return_meta"):
            return obj, "stop", 2
        return obj
    return chat


class NoteJobTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._env = {k: os.environ.get(k) for k in
                    ("DEEPSEEK_API_KEY", "OBSIDIAN_VAULT", "CLASSLIVE_NOTE_MERGE")}
        cls._chat = llm_client.chat_json
        cls._hdr = llm_client.headers

    @classmethod
    def tearDownClass(cls):
        llm_client.chat_json = cls._chat
        llm_client.headers = cls._hdr
        for k, v in cls._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def setUp(self):
        _CRED.parent.mkdir(parents=True, exist_ok=True)
        _CRED.write_text(_TEST_KEY, encoding="utf-8")
        self.home = Path(_HOME)
        self.calls: list = []
        llm_client.chat_json = _fake_chat(self.calls)
        self.logs: list = []

    def tearDown(self):
        llm_client.chat_json = self._chat

    def _job(self, **kw):
        return fakes.sample_job(**kw)

    def test_success_complete_ok(self):
        c = FakeClient(rows=fakes.sample_lines())
        job = self._job()
        note_job.run_note(c, job, home=self.home, log=self.logs.append)
        self.assertEqual(len(c.oks), 1)
        self.assertEqual(c.fails, [])
        got = c.oks[0]
        self.assertIn("ECON10770", got["md"])
        self.assertIn("2026-11-20", got["md"])
        self.assertIn("<", got["html"][:200])          # 详细版 HTML 真出了
        self.assertEqual(got["meta"]["course"], "ECON10770")
        self.assertEqual(got["meta"]["lines"], 2)
        self.assertTrue(self.calls)                    # 复习层真的调了模型出口
        self.assertTrue(all(x["model"] == "deepseek-flash" for x in self.calls))
        # 每条任务独立目录，交回后删掉：VPS 上不留用户笔记，也不在共用笔记库里串课（Claude 11-20）
        self.assertFalse((self.home / "Desktop" / "烤菠萝的笔记库").exists())
        jobs = self.home / "jobs"
        self.assertEqual(list(jobs.iterdir()) if jobs.exists() else [], [])

    def test_bill_to_cleared_after_job(self):
        # OCR 11-22：上一条任务的计费头不许留给下一条（hash 非法时会记到上一个用户名下）
        c = FakeClient(rows=fakes.sample_lines())
        note_job.run_note(c, self._job(), home=self.home, log=self.logs.append)
        from server.runner import billing
        self.assertEqual(billing._default, "")

    def test_bill_to_header_installed(self):
        note_job.install_bill_to("0123456789abcdef")
        h = llm_client.headers("cl-runner-testcode", "")
        self.assertEqual(h["X-ClassLive-Bill-To"], "0123456789abcdef")
        # 直连 sk- key 也带头（Worker 只在码名=runner 时才认，宁多勿漏）
        note_job.install_bill_to("fedcba9876543210")
        self.assertEqual(llm_client.headers("sk-x")["X-ClassLive-Bill-To"],
                         "fedcba9876543210")
        note_job.install_bill_to("not-a-hash", log=self.logs.append)   # 不合形状 → 不装
        self.assertIn("fedcba9876543210",
                      llm_client.headers("sk-x")["X-ClassLive-Bill-To"] + "x")

    def test_fail_missing_credentials(self):
        cred_backup = _CRED.read_text(encoding="utf-8")
        _CRED.unlink()
        try:
            c = FakeClient(rows=fakes.sample_lines())
            note_job.run_note(c, self._job(), home=self.home, log=self.logs.append)
            self.assertEqual(c.oks, [])
            self.assertEqual(len(c.fails), 1)
            self.assertIn("通行码", c.fails[0])
        finally:
            _CRED.write_text(cred_backup, encoding="utf-8")

    def test_fail_session_gone(self):
        c = FakeClient(err=SessionGone("410 session_deleted"))
        note_job.run_note(c, self._job(), home=self.home, log=self.logs.append)
        self.assertEqual(c.oks, [])
        self.assertEqual(len(c.fails), 1)
        self.assertIn("SessionGone", c.fails[0])

    def test_fail_empty_lines(self):
        c = FakeClient(rows=[])
        note_job.run_note(c, self._job(), home=self.home, log=self.logs.append)
        self.assertEqual(len(c.fails), 1)
        self.assertIn("一句都没有", c.fails[0])

    def test_cancel_no_complete(self):
        c = FakeClient(rows=fakes.sample_lines())
        job = self._job()
        job["_cancel"] = threading.Event()
        job["_cancel"].set()
        note_job.run_note(c, job, home=self.home, log=self.logs.append)
        self.assertEqual(c.oks, [])
        self.assertEqual(c.fails, [])          # 停手不交，也不算失败

    def test_raw_level_no_model_calls(self):
        c = FakeClient(rows=fakes.sample_lines())
        note_job.run_note(c, self._job(level="raw"), home=self.home,
                          log=self.logs.append)
        self.assertEqual(len(c.oks), 1)
        self.assertEqual(self.calls, [])       # 纯转录档：一个模型请求都不发
        self.assertIn("ECON10770", c.oks[0]["md"])


if __name__ == "__main__":
    unittest.main()
