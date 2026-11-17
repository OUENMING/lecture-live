#!/usr/bin/env python3
"""`cl_admin.py route` 的判据 —— 参数解析 + 打印 + 发出去的那两个请求（**全部 mock 网络**）。

    ClassLive.app/Contents/MacOS/python tests/test_cl_admin_route.py

钉住什么：
 1. `route` 不带参数 → **只**读（routes/get），把生效表连「来自哪里」一起打出来
 2. `route <类> <上游>` → 先 get、合并、再 set（**不覆盖别人设过的类**），最后重新 get 打印生效表
 3. `route --reset` → 只发 `{routes: {}}`（清空回默认）
 4. 非法上游 / 只给一个参数 → 直接退出，且**一个字节都没发出去**
 5. `*` 一律打出来（表里没有就标明「其余 → deepseek」）
 6. 类名 / 上游都归一成小写（终端里敲 `CHAPTER OpenCode` 也认）

⚠️ `cl_admin.admin_post` 整个换掉：本文件不联网、不读令牌、不碰真 `~/.classlive/`（HOME 指向临时目录）。
"""
from __future__ import annotations

import contextlib
import copy
import io
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "scripts"))

_TMP_HOME = tempfile.mkdtemp(prefix="cl-admin-route-test-")
os.environ["HOME"] = _TMP_HOME                    # 必须在 import paths 之前（STATE_ROOT 是 import 时算的）

import cl_admin                                                       # noqa: E402

DEFAULTS = {"note": "cc", "polish": "cc", "prep": "cc"}


def snapshot(stored: dict | None = None, env: dict | None = None) -> dict:
    """Worker 的 `/v1/admin/routes/get` 形状（三层 + effective）。合并语义的判据在 JS 那边，这里只喂数据。"""
    env = env or {"chapter": "cc"}
    eff = {**DEFAULTS, **env, **(stored or {})}
    return {"upstreams": ["deepseek", "cc", "opencode"], "stored": dict(stored or {}),
            "env": dict(env), "defaults": dict(DEFAULTS), "effective": eff}


class RouteCMD(unittest.TestCase):
    def setUp(self):
        self.state = {"stored": {}, "snapshot": snapshot()}
        self.calls = []
        self._patch = mock.patch.object(cl_admin, "admin_post", side_effect=self._post)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()

    def _post(self, path, body=None, **_kw):
        self.calls.append((path, copy.deepcopy(body)))
        if path == "routes/get":
            return copy.deepcopy(self.state["snapshot"])
        if path == "routes/set":
            self.state["stored"] = dict((body or {}).get("routes") or {})
            self.state["snapshot"] = snapshot(self.state["stored"])
            return {"ok": True, "routes": self.state["stored"]}
        raise AssertionError(f"不该发这个请求：{path}")

    def run_cli(self, argv) -> str:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(cl_admin.main(argv), 0)
        return buf.getvalue()

    # ---------- 1. 只看 ----------
    def test_只看_只读一次_并标出来源(self):
        out = self.run_cli(["route"])
        self.assertEqual([p for p, _ in self.calls], ["routes/get"], "看一眼不许写")
        self.assertIn("note", out)
        self.assertIn("← 默认", out)
        self.assertIn("chapter", out)
        self.assertIn("← env CHAT_ROUTES", out)
        self.assertIn("60 秒内生效", out)

    def test_没有星号也把其余类打出来(self):
        lines = [ln for ln in self.run_cli(["route"]).splitlines() if ln.startswith("  *")]
        self.assertEqual(len(lines), 1, "没有 `*` 也要有一行说明「其余」")
        self.assertIn("deepseek", lines[0])
        self.assertIn("内置", lines[0])

    # ---------- 2. 设一类 ----------
    def test_设一类_先读后合并再写(self):
        self.state["stored"] = {"chapter": "deepseek"}
        self.state["snapshot"] = snapshot(self.state["stored"])
        out = self.run_cli(["route", "note", "opencode"])
        self.assertEqual([p for p, _ in self.calls],
                         ["routes/get", "routes/set", "routes/get"])
        self.assertEqual(self.calls[1][1], {"routes": {"chapter": "deepseek", "note": "opencode"}},
                         "合并：别人设过的类不许被覆盖")
        self.assertIn("note → opencode", out)
        self.assertIn("← 你设的", out)

    def test_设星号(self):
        self.run_cli(["route", "*", "cc"])
        self.assertEqual(self.calls[1][1], {"routes": {"*": "cc"}})

    def test_类名与上游都归一(self):
        self.run_cli(["route", "  CHAPTER ", "OpenCode"])
        self.assertEqual(self.calls[1][1], {"routes": {"chapter": "opencode"}})

    # ---------- 3. 清空 ----------
    def test_reset_只发空表(self):
        self.state["stored"] = {"note": "opencode"}
        self.state["snapshot"] = snapshot(self.state["stored"])
        out = self.run_cli(["route", "--reset"])
        self.assertEqual(self.calls, [("routes/set", {"routes": {}})])
        self.assertIn("默认", out)

    # ---------- 4. 拒绝 ----------
    def test_非法上游_直接退出且一个字节都没发(self):
        with self.assertRaises(SystemExit) as e:
            with contextlib.redirect_stdout(io.StringIO()):
                cl_admin.main(["route", "note", "gemini"])
        self.assertIn("deepseek", str(e.exception))
        self.assertEqual(self.calls, [], "校验要在联网之前")

    def test_只给一个参数_直接退出(self):
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stdout(io.StringIO()):
                cl_admin.main(["route", "note"])
        self.assertEqual(self.calls, [])

    # ---------- 5. 表里已经有 `*` 时只打一行（别重复） ----------
    def test_星号只打一行(self):
        self.state["stored"] = {"*": "opencode"}
        self.state["snapshot"] = snapshot(self.state["stored"])
        lines = [ln for ln in self.run_cli(["route"]).splitlines() if ln.lstrip().startswith("*")]
        self.assertEqual(len(lines), 1)
        self.assertIn("opencode", lines[0])
        self.assertIn("你设的", lines[0])


if __name__ == "__main__":
    unittest.main()
