#!/usr/bin/env python3
"""`entry_launch.main()` 在**真 AppKit 事件循环**里的退出码（2026-10-11 作者报「点关闭还是开始转录」）。

根因：`_stop()` 用的是 `AppHelper.stopEventLoop()` —— 它在 `runEventLoop` 底下会走到
`NSApp().terminate_()`，**进程当场 exit(0)**，`main()` 根本没机会 `return rc["code"]`。
于是关闭（应为 1）、`.course` 写不下（应为 4）**全都变成 0**，`cl` 读到 0 就拿上次的课开录。

`tests/test_entry_panel.py` 钉的是纯函数那半，事件循环是假的 —— 碰不到这条。这里起子进程跑真循环，
面板换成一个 0.3 s 后自己回调的桩（不弹真面板、不写 `.course`/`.test-mode`）。
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CHILD = r"""
import os, sys
sys.path.insert(0, os.environ["ROOT"])
import entry_launch, entry_panel
from PyObjCTools import AppHelper
from AppKit import NSApplication
entry_launch.CFG = __import__("pathlib").Path(os.environ["TMP"]) / ".course"   # 写端隔离
NSApplication.sharedApplication()
def fake_open_panel(*, on_start, on_close, **kw):
    action = os.environ["ACTION"]
    AppHelper.callLater(0.3, (lambda: on_start("ECON10010")) if action == "start" else on_close)
    return object()
entry_panel.open_panel = fake_open_panel
rc = entry_launch.main()
print("RETURNED", rc, flush=True)
sys.exit(rc)
"""


def run(action: str) -> tuple[int, str]:
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "ROOT": ROOT, "TMP": tmp, "ACTION": action}
        p = subprocess.run([sys.executable, "-c", CHILD], env=env, capture_output=True,
                           text=True, timeout=30)
        return p.returncode, p.stdout


def main() -> int:
    bad = 0
    for action, want in (("close", 1), ("start", 0)):
        code, out = run(action)
        ok = code == want and "RETURNED" in out
        bad += not ok
        print(f"{'✅' if ok else '❌'} {action}: 退出码 {code}（期望 {want}），main() 正常返回={'RETURNED' in out}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
