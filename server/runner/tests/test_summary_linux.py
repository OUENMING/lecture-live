#!/usr/bin/env python3
"""Linux 可行性判据 —— W24：runner 跑在 VPS（Linux CPython），不许碰 AppKit / mlx。

在子进程里把 AppKit / Foundation / objc / mlx 塞成 None（import 即 ImportError，
模拟没装 PyObjC / mlx 的 Linux）后 import summary_job 必须成功，且逐段刷新确实关掉
（`REFRESH_GAP_S == inf`）。全程 HOME 指向临时目录；不联网、不花钱。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_RUNNER = os.path.dirname(_HERE)
_REPO = os.path.dirname(os.path.dirname(_RUNNER))

_CODE = f"""
import sys, os
for m in ("AppKit", "Foundation", "objc", "PyObjCTools", "mlx", "mlx.core"):
    sys.modules[m] = None            # import 即 ImportError —— 模拟 Linux 上没装
sys.path.insert(0, {_REPO!r})
sys.path.insert(0, {_RUNNER!r})
import summary_job
assert summary_job._live.REFRESH_GAP_S == float("inf"), "REFRESH_GAP_S 没关"
print("IMPORT_OK")
"""


class TestLinuxImport(unittest.TestCase):

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

    def test_import_with_appkit_and_mlx_stubbed(self):
        env = dict(os.environ)                    # setUp 已把 HOME 换成临时目录
        r = subprocess.run([sys.executable, "-c", _CODE], capture_output=True,
                           text=True, env=env, timeout=60)
        self.assertEqual(r.returncode, 0, f"stderr: {r.stderr[-500:]}")
        self.assertIn("IMPORT_OK", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
