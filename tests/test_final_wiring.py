#!/usr/bin/env python3
"""定稿上云接进主程序的判据(M 包): `main.py` 里的两个纯函数 + 接线形状。

    ClassLive.app/Contents/MacOS/python tests/test_final_wiring.py

钉住了什么(docs/PLAN-r2-cloud-final-asr.md R6 / R14 / R25 / §X1):
 1. `cloud_asr_off_reason`: 配置关 / 环境变量 / `--engine local` / 纯转录 / 文件倍速回放 都关云端
 2. `resolve_cloud_asr_enabled`: 首次告知 —— `.app` 弹窗点过才写 `acknowledged`;
    终端打印路径**不写**(否则终端启动一次就等于替 `.app` 用户同意了)
 3. 接线形状: 没有残留的 `last_logprob` / `load_final_asr`; `note_segment` 的每个关键字
    都在签名里(签名不匹配会被 `final_worker` 的 `except Exception` 吞成「定稿失败」,
    整节课定稿全空 —— §X1); `_emit` 在收尾放弃后不再翻译(R25)

⚠️ 不跑 `main.run()`; HOME 与 `paths.STATE_ROOT` 指向临时目录(读端写端都隔离)。
"""
from __future__ import annotations

import ast
import inspect
import os
import pathlib
import re
import shutil
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

_TMP_HOME = tempfile.mkdtemp(prefix="final-wiring-test-")
os.environ["HOME"] = _TMP_HOME
os.environ.pop("CLASSLIVE_CLOUD_ASR", None)

import final_asr                                                      # noqa: E402
import main as main_mod                                               # noqa: E402
import paths                                                          # noqa: E402
import testmode                                                       # noqa: E402

paths.STATE_ROOT = pathlib.Path(_TMP_HOME) / "state"

SRC = (HERE / "main.py").read_text(encoding="utf-8")
BASE = dict(cfg_enabled=True, engine="auto", mode="both", source="mic", speed=1.0)


def off(**kw):
    return main_mod.cloud_asr_off_reason(**{**BASE, **kw})


class TestOffReason(unittest.TestCase):
    def test_allowed_by_default(self):
        self.assertIsNone(off())

    def test_each_switch_turns_it_off(self):
        self.assertIsNotNone(off(cfg_enabled=False))
        self.assertIsNotNone(off(engine="local"))
        self.assertIsNotNone(off(mode="raw"))
        self.assertIsNotNone(off(source="file", speed=2.0))

    def test_other_values_stay_on(self):
        self.assertIsNone(off(engine="cloud"))
        self.assertIsNone(off(mode="en"))
        self.assertIsNone(off(source="file", speed=1.0))      # 倍速 1 的文件回放照常上云
        self.assertIsNone(off(source="mic", speed=3.0))       # 倍速只对 file 有意义

    def test_env_forces_off(self):
        os.environ["CLASSLIVE_CLOUD_ASR"] = "0"
        try:
            self.assertIsNotNone(off())
        finally:
            os.environ.pop("CLASSLIVE_CLOUD_ASR")

    def test_reasons_are_distinct_text(self):
        rs = {off(cfg_enabled=False), off(engine="local"), off(mode="raw"),
              off(source="file", speed=2.0)}
        self.assertEqual(len(rs), 4)


class TestConsent(unittest.TestCase):
    def run_it(self, cfg, *, app, choice="好的"):
        calls = {"alert": [], "write": []}

        def alert(title, msg, buttons=("知道了",), url=None, fallback=None):
            calls["alert"].append((title, msg, buttons, fallback))
            return choice

        said: list = []
        got = main_mod.resolve_cloud_asr_enabled(
            read=lambda: cfg, write=lambda en: calls["write"].append(en) or True,
            alert=alert, can_alert=lambda: app, say=said.append)
        return got, calls, said

    def test_app_path_asks_and_records_yes(self):
        got, c, _ = self.run_it({"enabled": True, "acknowledged": ""}, app=True)
        self.assertTrue(got)
        self.assertEqual(len(c["alert"]), 1)
        title, msg, buttons, fb = c["alert"][0]
        self.assertEqual(buttons, ("好的",), "只留一个按钮(Owen 2026-10-11); 想关去面板开关")
        self.assertEqual(msg, final_asr.consent_text())
        self.assertEqual(c["write"], [True])

    def test_app_path_records_yes_whatever_alert_returns(self):
        # 弹不出来时 alert 返回 fallback / None —— 单按钮告知, 结果都是「开 + 已告知」
        got, c, _ = self.run_it({"enabled": True, "acknowledged": ""}, app=True, choice=None)
        self.assertTrue(got)
        self.assertEqual(c["write"], [True])

    def test_terminal_path_never_writes_acknowledged(self):
        got, c, said = self.run_it({"enabled": True, "acknowledged": ""}, app=False)
        self.assertTrue(got)
        self.assertEqual(c["write"], [], "终端启动一次就写了 acknowledged = 替 .app 用户同意了")
        self.assertEqual(len(said), 1, "终端每次启动只打一行提示")
        self.assertIn("CLASSLIVE_CLOUD_ASR=0", said[0])

    def test_already_acknowledged_is_silent(self):
        got, c, said = self.run_it({"enabled": True, "acknowledged": "2026-10-11"}, app=True)
        self.assertTrue(got)
        self.assertEqual((c["alert"], c["write"], said), ([], [], []))
        got, c, said = self.run_it({"enabled": False, "acknowledged": "2026-10-11"}, app=True)
        self.assertFalse(got)
        self.assertEqual((c["alert"], c["write"]), ([], []))

    def test_disabled_never_asks(self):
        got, c, _ = self.run_it({"enabled": False, "acknowledged": ""}, app=True)
        self.assertFalse(got)
        self.assertEqual((c["alert"], c["write"]), ([], []))

    def test_env_off_touches_nothing(self):
        os.environ["CLASSLIVE_CLOUD_ASR"] = "0"
        try:
            got, c, said = self.run_it(final_asr.read_config(), app=True)
        finally:
            os.environ.pop("CLASSLIVE_CLOUD_ASR")
        self.assertFalse(got)
        self.assertEqual((c["alert"], c["write"], said), ([], [], []))

    def test_default_args_read_config_under_tmp_home(self):
        # 默认参数走真 read_config → 临时目录里没文件 = 未告知; 终端路径(没设 FROM_APP)只提示、不写
        os.environ.pop("CLASSLIVE_FROM_APP", None)
        self.assertTrue(main_mod.resolve_cloud_asr_enabled(say=lambda s: None))
        self.assertFalse(paths.cloud_asr_config().exists())


class TestWiringShape(unittest.TestCase):
    def test_no_leftovers(self):
        self.assertNotIn("last_logprob", SRC)
        self.assertNotIn("load_final_asr", SRC)
        self.assertNotIn("asr_final", SRC)

    def test_load_final_after_trans_and_with_stopping(self):
        i_trans = SRC.index('trans = {"mode": "both"}')
        i_load = SRC.index("final_asr.load_final(")
        self.assertGreater(i_load, i_trans, "allow_cloud 要读 trans[\"mode\"], 必须在它之后装配")
        call = SRC[i_load:i_load + 400]
        self.assertIn("preload_local=", call)
        self.assertIn("is_stopping=stopping.is_set", call)

    def test_note_segment_keywords_match_signature(self):
        sig = inspect.signature(testmode.TestSession.note_segment).parameters
        calls = [n for n in ast.walk(ast.parse(SRC))
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "note_segment"]
        self.assertTrue(calls, "main.py 里找不到 note_segment 调用")
        for c in calls:
            kws = {k.arg for k in c.keywords}
            for kw in kws:
                self.assertIn(kw, sig, f"note_segment 没有参数 {kw!r}")
            self.assertLessEqual(len(c.args) + 1, len(sig))
            self.assertTrue({"steps", "trace"} <= kws)

    def test_emit_skips_after_giving_up(self):
        m = re.search(r"def _emit\(text: str, seg: int \| None = None\) -> None:\n(.*?)final_gen\[", SRC, re.S)
        self.assertIsNotNone(m)
        self.assertIn("running.is_set()", m.group(1), "R25: _emit 前要判 running")

    def test_final_worker_uses_final_transcribe(self):
        self.assertIn("final.transcribe(buf)", SRC)


def tearDownModule():
    shutil.rmtree(_TMP_HOME, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
