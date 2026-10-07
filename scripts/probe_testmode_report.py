#!/usr/bin/env python3
"""探针：**测试模式在「窗口已经关了」时到底产不产得出报告**。

    ClassLive.app/Contents/MacOS/python scripts/probe_testmode_report.py

## 为什么需要它（2026-10-07）

`report.json` **从 2026-09-24 之后再没写成过**（≈90 节），而屏上照样打
「数据包生成失败，但报告已写出」——**那是假话**。根因：浮窗模式的**正常停止就是 ✕**，
所以收尾等 `tester.finish()` 时 `ui._closed` **恒为真** → `_spin` 的循环体一次都不跑 →
后台 daemon 线程被丢下 → 进程退出把它杀了。

**这个故障藏了整整一周、没有任何闸门能拦** —— 因为默认闸门是毫秒级的，而这条要真跑
一个会话。所以它住在这里（`scripts/`，不在运行路径上，作者/接手的人手跑），
而不是住进默认闸门。

## 它怎么做到的（不用真课、不用 GUI）

`main.py` 里 UI 是这么来的：
    ui = TerminalUI() if args.ui == "terminal" else _load_overlay(...)
`_load_overlay` 是**模块级函数** → 换成一个「`_closed` 恒为真」的假 UI，
就**精确复现了 ✕ 那条路的收尾条件**。然后走**真实的** `main.main()`（真解析器 + 真 run +
真 finish + 真报告落盘）。

## 判据（红-绿都验过）

· 修好的版本 → 输出里有 `🧪 测试报告: <路径>`，且那个文件**真的存在**。
· 把 `spin_until` 里的 `wait_ui = not ui_gone()` 退回旧写法 → **一行报告都不打**，
  而且不会产出 `report.json`（2026-10-07 实测：正是事故形态）。

## ⚠️ 副作用（照实说）

· 它会往 `sessions/` 写一个**真的测试会话**（`sessions/` 的纪律是只追加，不清）。
· `CLASSLIVE_UPLOAD=0` 已设 —— **不会**把这次试跑传上去。
· `CLASSLIVE_NO_AUTO_UPDATE=1` 已设 —— 不会触发更新。
· vault 指到临时目录，不碰真 Obsidian 库。
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile
import wave

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np                                                    # noqa: E402


def _tiny_wav(path: pathlib.Path, secs: float = 5.0) -> None:
    sr = 16000
    t = np.arange(int(secs * sr)) / sr
    x = (0.2 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    w = wave.open(str(path), "wb")
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(sr)
    w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())
    w.close()


def main() -> int:
    # ⚠️ 必须在 import main **之前**设 —— 它在 import 期就会读这些。
    os.environ["CLASSLIVE_UPLOAD"] = "0"
    os.environ["CLASSLIVE_NO_AUTO_UPDATE"] = "1"
    import main as main_mod

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="cl_probe_tmreport_"))
    wav = tmp / "tiny.wav"
    _tiny_wav(wav)

    class _ClosedUI:
        """模拟「用户已经按了 ✕」—— 浮窗模式的**正常**停止方式。
        这正是事故里让 `_spin` 一次都不循环的那个条件。"""

        _closed = True
        drives_appkit = False

        def __getattr__(self, name):
            return lambda *a, **k: None

    main_mod._load_overlay = lambda *a, **k: _ClosedUI()
    sys.argv = ["main.py", "--test-mode", "--source", "file", "--path", str(wav),
                "--speed", "8", "--ui", "overlay", "--save-notes", "yes",
                "--vault", str(tmp / "vault"), "--no-bundle",
                "--no-record-audio", "--no-record-float"]
    main_mod.main()

    # 报告落点由 `TestSession.stem` 决定（= sessions/<stem>.report.json）。
    # ⚠️ **不能只看 stdout**：那条 `🧪 测试报告:` 行本身也被同一次改动改过口径 ——
    #    判据要落在**磁盘上真的有那个文件**（那才是"产出了报告"的本义）。
    newest = sorted((ROOT / "sessions").glob("*_LECTURE.report.json"),
                    key=lambda p: p.stat().st_mtime)
    if not newest:
        print("\n❌ 探针失败：sessions/ 里没有 report.json —— 收尾又没跑完")
        return 1
    p = newest[-1]
    if p.stat().st_mtime < _started:
        print(f"\n❌ 探针失败：只有旧报告（{p.name}），这次没写出新的")
        return 1
    print(f"\n✅ 探针通过：窗口已关（✕）的情况下仍然产出了 {p.name}")
    return 0


_started = 0.0

if __name__ == "__main__":
    _started = __import__("time").time()
    sys.exit(main())
