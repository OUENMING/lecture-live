#!/usr/bin/env python3
"""术语行会自己收起（3.10.1，Owen 10-19「出现术语的时候它不会自动隐藏」）。

最后一次命中后 `overlay.TERM_SHOW_S` 秒收起；钉住的不收；新命中续时。
"""
import ast
import os
import pathlib
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
ROOT = pathlib.Path(__file__).resolve().parent.parent


class 真窗口(unittest.TestCase):
    def _overlay(self):
        try:
            import overlay
            from overlay import Overlay
        except Exception as e:                       # noqa: BLE001
            self.skipTest(f"AppKit 不可用, 跳过: {e}")
        o = Overlay()
        self.addCleanup(o.close)
        self.now = [100.0]
        o._clock = lambda: self.now[0]
        return o, overlay.TERM_SHOW_S

    def test_到点收起(self):
        o, ttl = self._overlay()
        o.terms([("elasticity", "弹性")])
        self.assertIn("elasticity", o._gloss_text())
        self.now[0] += ttl - 0.1
        o._terms_tick(self.now[0])
        self.assertIn("elasticity", o._gloss_text(), "还没到点就收了")
        self.now[0] += 0.2
        o._terms_tick(self.now[0])
        self.assertEqual(o._gloss_text(), "")

    def test_新命中续时(self):
        o, ttl = self._overlay()
        o.terms([("elasticity", "弹性")])
        self.now[0] += ttl - 1.0
        o.terms([("surplus", "剩余")])
        self.now[0] += 2.0
        o._terms_tick(self.now[0])
        self.assertIn("surplus", o._gloss_text())

    def test_钉住的不收(self):
        o, ttl = self._overlay()
        o.terms([("elasticity", "弹性")])
        o._on_gloss_click()
        self.now[0] += ttl * 3
        o._terms_tick(self.now[0])
        self.assertIn("elasticity", o._gloss_text())

    def test_不能短到只活一句(self):
        import overlay
        self.assertGreaterEqual(overlay.TERM_SHOW_S, 10.0)


class 接线(unittest.TestCase):
    def test_pump_每帧叫(self):
        tree = ast.parse((ROOT / "overlay.py").read_text(encoding="utf-8"))
        fn = next(f for f in ast.walk(tree)
                  if isinstance(f, ast.FunctionDef) and f.name == "_pump_once")
        calls = {c.func.attr for c in ast.walk(fn)
                 if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)}
        self.assertIn("_terms_tick", calls)


if __name__ == "__main__":
    unittest.main(verbosity=2)
