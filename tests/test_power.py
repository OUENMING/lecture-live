#!/usr/bin/env python3
"""防空闲睡眠（`power.py`，3.9.7 第 5a 项）的判据 —— **全离线**。

    ClassLive.app/Contents/MacOS/python tests/test_power.py

## 钉住了什么

1. ⭐ **正常路径**：`keep_system_awake()` 从来持有到出来 —— `beginActivity…` 一次、
   `endActivity_` 一次，选项是 `空闲系统睡眠` + `突然终止` 两个位。
2. ⭐⭐ **抛异常路径也释放**：`with` 体里抛出去，`endActivity_` 仍要调到。
   （这是本题的全部意义 —— 收尾里任何一步炸掉都不许把断言漏在机器上，
   漏了 = 这台笔记本以后不合盖也不睡。）
3. 掐不到 `NSProcessInfo`（非 macOS / 没 pyobjc）→ **永不抛**，且不去调
   `endActivity_` 一个我们没拿到的 token。
4. **`main()` 真的套在 `run(args)` 上**（AST；`main` 是 `main.py` 的进程入口，
   收尾那一段住在 `run()` 里）。

⚠️ 注入的是**假 `NSProcessInfo`** —— 打真断言会把这台机器的空闲睡眠拦着
（测试进程退出时才释放，不可接受）。本文件一个真 AppKit 调用都不发。
"""
from __future__ import annotations

import ast
import os
import pathlib
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import power                                                          # noqa: E402


class FakeProcessInfo:
    """只记两件事：被叫了几次、拿到的 token 是什么。"""

    def __init__(self):
        self.begins = 0
        self.ends = 0
        self.options = None
        self.reason = None
        self.tokens: list = []
        self.ended: list = []

    def beginActivityWithOptions_reason_(self, options, reason):
        self.begins += 1
        self.options, self.reason = options, reason
        tok = object()
        self.tokens.append(tok)
        return tok

    def endActivity_(self, token):
        self.ends += 1
        self.ended.append(token)


class FakeBoom:
    def beginActivityWithOptions_reason_(self, options, reason):
        raise RuntimeError("没有 pyobjc")

    def endActivity_(self, token):
        raise AssertionError("根本不该调到这里")


class KeepAwake(unittest.TestCase):
    def test_正常出来_各一次(self):
        pi, logs = FakeProcessInfo(), []
        with power.keep_system_awake(pi, log=logs.append):
            pass
        self.assertEqual((pi.begins, pi.ends), (1, 1))
        self.assertEqual(pi.options, power.IDLE_SYSTEM_SLEEP_DISABLED
                         | power.SUDDEN_TERMINATION_DISABLED)
        self.assertEqual(pi.ended, pi.tokens, "释放的不是拿到的那个 token")
        self.assertTrue(pi.reason and "ClassLive" in pi.reason, pi.reason)
        self.assertEqual(logs, [])

    def test_抛异常_仍然释放(self):
        pi = FakeProcessInfo()
        with self.assertRaises(RuntimeError):
            with power.keep_system_awake(pi, log=lambda *_: None):
                raise RuntimeError("收尾里炸了")
        self.assertEqual((pi.begins, pi.ends), (1, 1))

    def test_拿不上_不外抛_也不去释放(self):
        pi, logs = FakeBoom(), []
        with power.keep_system_awake(pi, log=logs.append):
            pass
        self.assertEqual(len(logs), 1, "拿不上要出声一次（但不许抛）")
        self.assertIn("没有 pyobjc", logs[0])

    def test_重复调用幂等(self):
        pi = FakeProcessInfo()
        act = power.Activity(pi, log=lambda *_: None)
        self.assertTrue(act.begin())
        self.assertTrue(act.begin())
        act.end()
        act.end()
        self.assertEqual((pi.begins, pi.ends), (1, 1))


class Wiring(unittest.TestCase):
    """`main.main()` 是录课进程的入口；断言必须套着 `run(args)`。"""

    def _main_fn(self):
        tree = ast.parse((HERE / "main.py").read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if isinstance(n, ast.FunctionDef) and n.name == "main":
                return n
        self.fail("main.py 里找不到 main()")

    def test_run_被_keep_system_awake_罩住(self):
        fn = self._main_fn()
        guarded = []
        for node in ast.walk(fn):
            if not isinstance(node, ast.With) or len(node.items) != 1:
                continue
            ctx = node.items[0].context_expr
            if "keep_system_awake" not in ast.unparse(ctx):
                continue
            guarded.append(node)
        self.assertTrue(guarded, "main() 里没有 `with power.keep_system_awake()`")
        calls = [ast.unparse(n) for w in guarded for n in ast.walk(w)]
        self.assertIn("run(args)", calls, "`run(args)` 没被那个 `with` 包住")


class WakeWatchCase(unittest.TestCase):
    """⭐ 3.10：睡醒探测 —— 收尾 spinner 的 tick 靠它发现"睡过一觉"（`main._spin`）。

    ⚠️ 时钟注入假墙钟，**不真睡**。
    """

    def test_没跳变就不报(self):
        t = [100.0]
        w = power.WakeWatch(clock=lambda: t[0])
        self.assertFalse(w.check())
        t[0] += 2.0
        self.assertFalse(w.check())
        self.assertEqual((w.wakes, w.sleep_seconds), (0, 0.0))

    def test_墙钟跳变_报一次_然后归零(self):
        t = [100.0]
        w = power.WakeWatch(clock=lambda: t[0])
        t[0] += power.SLEEP_GAP_S + 5.0            # 睡了一觉
        self.assertTrue(w.check())
        self.assertAlmostEqual(w.sleep_seconds, power.SLEEP_GAP_S + 5.0)
        self.assertFalse(w.check(), "同一次睡醒只许报一次")
        self.assertEqual(w.wakes, 1)

    def test_阈值可调(self):
        t = [0.0]
        w = power.WakeWatch(gap_s=3.0, clock=lambda: t[0])
        t[0] += 4.0
        self.assertTrue(w.check())


class SleepGapSingleSource(unittest.TestCase):
    """⚠️ `SLEEP_GAP_S` 只有一份定义（`power.py`）—— 上课那条保安线程与收尾的
    `WakeWatch` 共用。两处各写一个 10.0 迟早会漂成两个数。"""

    def test_main_读的是_power_那份(self):
        src = (HERE / "main.py").read_text(encoding="utf-8")
        self.assertIn("SLEEP_GAP_S = power.SLEEP_GAP_S", src,
                      "main.py 又自己写了一份字面量 —— 改回 `power.SLEEP_GAP_S`")
        self.assertEqual(power.SLEEP_GAP_S, 10.0, "值变了要两边一起想清楚")


class WakeWiring(unittest.TestCase):
    """睡醒补传真的接在收尾 spinner 上（AST，不起进程）。"""

    def _tree(self):
        return ast.parse((HERE / "main.py").read_text(encoding="utf-8"))

    def _fn(self, name):
        for n in ast.walk(self._tree()):
            if isinstance(n, ast.FunctionDef) and n.name == name:
                return n
        return None

    def test_tick_里看一眼_wake(self):
        tick = self._fn("_tick")
        self.assertIsNotNone(tick, "main.py 里找不到 _tick（收尾 spinner）")
        calls = [ast.unparse(n) for n in ast.walk(tick)]
        # ⚠️ 变异验证：把 `_wake.check()` 那段删掉 -> 这条红（睡醒没人补推）。
        self.assertTrue(any("_wake.check()" in c for c in calls),
                        "收尾 spinner 的 tick 没接睡醒探测")
        # ⚠️ 变异验证：把 `retry_pending_uploads(...)` 那行删掉 -> 这条红。
        self.assertTrue(any("retry_pending_uploads" in c for c in calls),
                        "睡醒了却没补推上传")

    def test_上课那条保安线程_睡醒也补推(self):
        txt = ast.unparse(self._tree())
        # ⚠️ 变异验证：把 `on_wake=_on_wake_net` 改回 `_reset_net` -> 这条红。
        self.assertIn("on_wake=_on_wake_net", txt)
        fn = self._fn("_on_wake_net")
        self.assertIsNotNone(fn, "`_on_wake_net` 不见了")
        calls = [ast.unparse(n) for n in ast.walk(fn)]
        self.assertTrue(any("retry_pending_uploads" in c for c in calls),
                        "上课睡醒只复位了连接池，没补推上传")


if __name__ == "__main__":
    unittest.main(verbosity=2)
