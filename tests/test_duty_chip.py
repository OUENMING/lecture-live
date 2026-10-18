#!/usr/bin/env python3
"""角落小卡的**亮度策略**（作者 2026-10-17 问「可以收起来对吗？策略是什么」）：

    新条目到达 → 亮起（淡入，`motion.CHIP_HOT_S` 秒）→ 没人理就降到**低亮度**
    （**不消失、仍可点**）→ 再来新条目重新亮起 → 全部处理完淡出。

判据分三层：
① `chip_alpha` / `chip_new_pending` —— 策略本身是**纯函数**（没有窗口也能测）；
② 接线（源码级）：`pump` 每帧 tick、`duty_update` 认新条目、`layout` 的显示转换亮起；
③ 真 `Overlay` 上把时间轴走一遍（AppKit 不可用时跳过，同 R6 的规矩）。
"""
import ast
import os
import pathlib
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import motion  # noqa: E402
import overlay  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _item(rid="d1", action="pending", **kw):
    return {"id": rid, "action": action, "type": "调课",
            "title": "本周三停课", "quote": "There is no class on Wednesday.",
            **kw}


class 亮度两档(unittest.TestCase):
    def test_亮档与暗档(self):
        self.assertEqual(overlay.chip_alpha(100.0, 108.0), overlay.CHIP_ALPHA_HOT)
        self.assertEqual(overlay.chip_alpha(108.5, 108.0), overlay.CHIP_ALPHA_DIM)

    def test_到点那一瞬就是暗档(self):
        """`now < hot_until` 才算亮 —— 到点即降，不做「多亮一帧」的暧昧。"""
        self.assertEqual(overlay.chip_alpha(108.0, 108.0), overlay.CHIP_ALPHA_DIM)

    def test_暗档不消失(self):
        """⭐ 降到低亮度**不是收起**：0 永远不在这一档里。"""
        for i in range(0, 40):
            self.assertGreater(overlay.chip_alpha(float(i), 8.0), 0.0)

    def test_暗档仍在小字的可读档上(self):
        """0.55 是磨砂上小字的最低可读档（调研 §⑥ 那张实测 α 表）—— 别顺手调低。"""
        self.assertGreaterEqual(overlay.CHIP_ALPHA_DIM, 0.55)
        self.assertGreater(overlay.CHIP_ALPHA_HOT, overlay.CHIP_ALPHA_DIM)

    def test_亮档时长是常数(self):
        """作者原话「约 8 秒」；可配置 = 改 `motion.CHIP_HOT_S` 一处。"""
        self.assertEqual(motion.CHIP_HOT_S, 8.0)


class 新条目判定(unittest.TestCase):
    def test_空到有(self):
        self.assertTrue(overlay.chip_new_pending(set(), [_item()]))

    def test_同一条重复推不算新(self):
        """`duty_update` 每帧可能被推好几次（改类型 / 撤销）—— 那不该反复亮。"""
        self.assertFalse(overlay.chip_new_pending({"d1"}, [_item()]))

    def test_再来一条算新(self):
        self.assertTrue(overlay.chip_new_pending({"d1"}, [_item("d1"), _item("d2")]))

    def test_同标题的两条也算新(self):
        """⭐ 判据是 **id 集合的差**，不是那行字变了没有（第二件事不许悄悄暗着）。"""
        a, b = _item("d1"), _item("d2")           # 类型 / 标题 / 日期全一样
        self.assertTrue(overlay.chip_new_pending({"d1"}, [a, b]))

    def test_已处理的不算待处理(self):
        self.assertEqual(overlay.chip_pending_ids([_item(), _item("d2", "saved"), ]),
                         {"d1"})
        self.assertFalse(overlay.chip_new_pending({"d1"}, [_item("d1", "ignored")]))

    def test_脏数据不炸(self):
        self.assertEqual(overlay.chip_pending_ids(None), set())
        self.assertEqual(overlay.chip_pending_ids(["x", 3, {}]), set())
        self.assertEqual(overlay.chip_pending_ids([{"action": "pending"}]), set())


class 策略时间轴(unittest.TestCase):
    """把作者要的那套策略按时间轴走一遍（纯函数 + 一个假的「到点」判断）。"""

    def test_亮_降档_再亮(self):
        t0 = 100.0
        hot_until = t0 + motion.CHIP_HOT_S
        # 新条目到达 → 亮
        self.assertEqual(overlay.chip_alpha(t0, hot_until), overlay.CHIP_ALPHA_HOT)
        self.assertEqual(overlay.chip_alpha(t0 + 7.9, hot_until), overlay.CHIP_ALPHA_HOT)
        # 没人理 → 降到低亮度（还在，仍可点）
        self.assertEqual(overlay.chip_alpha(t0 + 8.1, hot_until), overlay.CHIP_ALPHA_DIM)
        # 再来一条 → 重新亮起（`_chip_lit` 把那 8 秒续上）
        hot_until = t0 + 8.5 + motion.CHIP_HOT_S
        self.assertEqual(overlay.chip_alpha(t0 + 8.5, hot_until), overlay.CHIP_ALPHA_HOT)


class 接线(unittest.TestCase):
    """源码级：三处接线各钉一条 —— 纯函数对了、没人调也是白搭。"""

    @classmethod
    def setUpClass(cls):
        cls.src = (ROOT / "overlay.py").read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.src)

    @classmethod
    def _calls_in(cls, func_name: str) -> set:
        out = set()
        for fn in ast.walk(cls.tree):
            if isinstance(fn, ast.FunctionDef) and fn.name == func_name:
                for c in ast.walk(fn):
                    if isinstance(c, ast.Call):
                        f = c.func
                        out.add(f.attr if isinstance(f, ast.Attribute) else
                                (f.id if isinstance(f, ast.Name) else ""))
        return out

    def test_pump_每帧叫_tick(self):
        self.assertIn("_chip_tick", self._calls_in("_pump_once"),
                      "没人调 `_chip_tick` → 小卡永远亮着，策略等于没做")

    def test_duty_update_认新条目(self):
        self.assertIn("chip_new_pending", self._calls_in("duty_update"),
                      "新条目到达时没有「亮起」这一步")

    def test_layout_显示转换时亮起(self):
        self.assertIn("_chip_lit", self._calls_in("_layout"),
                      "小卡从收起转回显示时没有亮起（淡入 + 续 8 秒）")

    def test_收起走_alpha_不走_setHidden(self):
        """收起必须是**淡出**：`_layout` 里不再用 `setHidden_` 硬切小卡的字。

        （`__init__` 的初始态与 `_chip_apply` 里「淡完才收」那一句不算 —— 判据只看 `_layout`。）
        """
        layout = [fn for fn in ast.walk(self.tree)
                  if isinstance(fn, ast.FunctionDef) and fn.name == "_layout"]
        self.assertTrue(layout, "没找到 `_layout`")
        hide_calls = [n for n in ast.walk(layout[0])
                      if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                      and n.func.attr == "setHidden_"
                      and isinstance(n.func.value, ast.Attribute)
                      and n.func.value.attr == "_chip_lbl"]
        self.assertEqual(hide_calls, [], "小卡的字又被 setHidden_ 硬切了（要在 `_chip_apply` 里淡）")
        self.assertIn("_chip_apply", self._calls_in("_layout"))


class 真窗口(unittest.TestCase):
    """③ 真 `Overlay` 上走一遍：亮 → 到点降档 → 新条目再亮 → 全处理完淡出。"""

    def test_小卡会亮会暗会再亮(self):
        try:
            from overlay import Overlay
        except ModuleNotFoundError as e:
            self.skipTest(f"AppKit 不可用, 跳过: {e}")
        o = Overlay()
        try:
            import duty as _duty
            now = [100.0]
            o._clock = lambda: now[0]
            o.duty_update([_item("d1")])
            self.assertTrue(o._chip_show, "有待处理条目 → 小卡该显示")
            self.assertEqual(o._chip_alpha, overlay.CHIP_ALPHA_HOT)
            self.assertEqual(o._chip_hot_until, 100.0 + motion.CHIP_HOT_S)
            self.assertIn("本周三停课", _duty.chip_text(o._duty_items))
            # 7.9 秒：还亮着
            now[0] = 107.9
            o._chip_tick(now[0])
            self.assertEqual(o._chip_alpha, overlay.CHIP_ALPHA_HOT)
            # 8.0 秒：降档，但**还在**（照样能点）
            now[0] = 108.0
            o._chip_tick(now[0])
            self.assertEqual(o._chip_alpha, overlay.CHIP_ALPHA_DIM)
            self.assertTrue(o._chip_show)
            self.assertFalse(o._chip_hit.isHidden(), "暗档照样可点 —— 别把入口一起关了")
            # 再来一条：重新亮起，续 8 秒
            o.duty_update([_item("d1"), _item("d2", title="下周必须到场")])
            self.assertEqual(o._chip_alpha, overlay.CHIP_ALPHA_HOT)
            self.assertEqual(o._chip_hot_until, 108.0 + motion.CHIP_HOT_S)
            # 全部处理完：淡出（alpha 0，不再占地方）
            o.duty_update([_item("d1", "saved"), _item("d2", "ignored")])
            self.assertFalse(o._chip_show)
            self.assertEqual(o._chip_alpha, 0.0)
        finally:
            o.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
