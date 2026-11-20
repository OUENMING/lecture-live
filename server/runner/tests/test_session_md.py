# test_session_md.py —— 写出的会话 md 能被桌面 `_parse` 原样读回（含中文、空 zh、课名带 `/`）。
# ⚠️ HOME 必须在 import 桌面模块之前指到临时目录（paths.STATE_ROOT 是 import 时算的），
#    与其他 runner 测试共用同一个临时家（CLASSLIVE_RUNNER_TEST_HOME）。
import os
import sys
import tempfile
import unittest
from pathlib import Path

_HOME = os.environ.get("CLASSLIVE_RUNNER_TEST_HOME") or tempfile.mkdtemp(
    prefix="cl-runner-test-")
os.environ["CLASSLIVE_RUNNER_TEST_HOME"] = _HOME
os.environ["HOME"] = _HOME

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from server.runner import session_md                     # noqa: E402
from server.runner.tests import fakes                   # noqa: E402


class SessionMdTest(unittest.TestCase):
    def setUp(self):
        self.out = Path(_HOME) / "md-out"
        self.lines = fakes.sample_lines() + [
            {"seq": 3, "t": 650, "en": "多行\n\n文本 line", "zh": "中文 带 换行\n的句子", "src": "asr"},
        ]

    def test_course_with_middle_dot(self):
        # 「·」原样进抬头会把 `_COURSE_DATE` 切错 → 整条任务失败（Claude 11-20 审查发现）
        p = session_md.write_session(
            self.lines, course="微观 · 第3讲", started_at="2026-11-20T09:00:00Z",
            tz="Europe/Dublin", out_dir=self.out)
        self.assertIn("# 微观 - 第3讲 · 2026-11-20 · 实时会话日志", p.read_text(encoding="utf-8"))

    def test_roundtrip_with_readback(self):
        p = session_md.write_session(
            self.lines, course="ECON10770/Micro: Theory",
            started_at="2026-11-20T09:00:00Z", tz="Europe/Dublin",
            out_dir=self.out)
        self.assertTrue(p.name.startswith("2026-11-20_"), p.name)
        # 课名里的 `/` `:` 清掉（Linux 上 `/` 是硬非法）
        self.assertNotIn("/", p.name)
        self.assertNotIn(":", p.name)
        text = p.read_text(encoding="utf-8")
        # 抬头用净化后的课名（「/」「:」会进桌面笔记库路径；Claude 11-20）
        self.assertIn("# ECON10770-Micro- Theory · 2026-11-20 · 实时会话日志", text)
        self.assertIn("> **EN**: Price elasticity of demand.", text)
        self.assertIn("> **ZH**: 需求的价格弹性。", text)
        # t=课内秒 → 挂钟（09:00Z = 都柏林 09:00 冬令时）
        self.assertIn("> [!abstract] 09:01:01", text)     # t=61
        self.assertIn("> [!abstract] 09:10:50", text)     # t=650
        # 换行被压平（_flat）—— `_parse` 只认每个字段的第一行
        self.assertIn("多行 文本 line", text)
        self.assertIn("中文 带 换行 的句子", text)
        # ⭐ 读回断言（write_session 内部已做，这里再独立对一遍）
        import obsidian_writer as ow
        got = ow.ObsidianWriter._parse(text)
        self.assertEqual(len(got), 3)
        self.assertEqual(got[1]["ts"], "09:01:01")
        self.assertEqual(got[1]["zh"], "")               # 空 zh 的句没有 ZH 行
        self.assertEqual(got[2]["zh"], "中文 带 换行 的句子")

    def test_course_date_regex_like_rebuild(self):
        """抬头要被 main._session_course_date 认得出（它认不出就拒绝补笔记）。"""
        p = session_md.write_session(
            self.lines, course="ECON10770", started_at="2026-11-20T09:00:00Z",
            tz="Europe/Dublin", out_dir=self.out)
        text = p.read_text(encoding="utf-8")
        m = session_md._COURSE_DATE.match(text)
        self.assertTrue(m)
        self.assertEqual(m.group(1).strip(), "ECON10770")
        self.assertEqual(m.group(2), "2026-11-20")

    def test_same_second_no_overwrite(self):
        kw = dict(course="C1", started_at="2026-11-20T09:00:00Z", tz="UTC",
                  out_dir=self.out)
        p1 = session_md.write_session(self.lines, **kw)
        p2 = session_md.write_session(self.lines, **kw)
        self.assertNotEqual(p1, p2)
        self.assertEqual(p1.read_text(), p2.read_text())

    def test_empty_lines_rejected(self):
        with self.assertRaises(ValueError):
            session_md.write_session([], course="C", started_at="2026-11-20T09:00:00Z",
                                     tz="UTC", out_dir=self.out)

    def test_safe_course(self):
        self.assertEqual(session_md.safe_course("a/b:c*d?"), "a-b-c-d")
        self.assertEqual(session_md.safe_course("  "), "LECTURE")


if __name__ == "__main__":
    unittest.main()
