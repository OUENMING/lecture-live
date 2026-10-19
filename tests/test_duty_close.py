#!/usr/bin/env python3
"""「一些事儿」卡片**关得掉**、小卡**缩得回去**（作者 2026-10-19 报的 3.10 bug）：

    「长条它不会自己缩回去，然后那个长条你点击之后你更改完它也没办法关闭。」

① 卡片右上角有「收起」；点了**下一帧**关（不在点击回调里拆视图）；
② 点完「记下 / 忽略」→ 结果停 `motion.DUTY_DONE_HOLD_S` 秒 → 翻到下一条，没有了就收起；
   停留期间撤销 → 不走；写提醒事项失败 → 不走（卡已收起就重新摆出来）；
③ 小卡亮档写全标题，暗档缩成「一些事儿 N」，新条目再展开。
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
    return {"id": rid, "action": action, "type": "调课", "title": "本周三停课",
            "quote": "There is no class on Wednesday.", "date": "2026-10-14",
            "t": "13:52:56", **kw}


class 纯函数(unittest.TestCase):
    def test_暗档只留计数(self):
        self.assertEqual(overlay.chip_compact(2), "一些事儿 2")
        self.assertEqual(overlay.chip_compact(0), "")

    def test_停留时长是常数(self):
        self.assertGreater(motion.DUTY_DONE_HOLD_S, 0.0)
        self.assertLessEqual(motion.DUTY_DONE_HOLD_S, 5.0)


class 接线(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = ast.parse((ROOT / "overlay.py").read_text(encoding="utf-8"))

    def _fn(self, name):
        for fn in ast.walk(self.tree):
            if isinstance(fn, ast.FunctionDef) and fn.name == name:
                return fn
        self.fail(f"没找到 `{name}`")

    def _names(self, name) -> set:
        out = set()
        for n in ast.walk(self._fn(name)):
            if isinstance(n, ast.Attribute):
                out.add(n.attr)
        return out

    def test_pump_消费收起与停留(self):
        got = self._names("_pump_once")
        self.assertIn("_card_shut", got, "「收起」只立了旗没人收 → 点了没反应")
        self.assertIn("_duty_tick", got, "处理完的卡没人翻页 / 收起")

    def test_收起按钮不在回调里拆视图(self):
        self.assertNotIn("_card_close", self._names("_duty_shut"))


class 真窗口(unittest.TestCase):
    def _overlay(self, board):
        try:
            from overlay import Overlay
        except ModuleNotFoundError as e:
            self.skipTest(f"AppKit 不可用, 跳过: {e}")
        now = [100.0]
        holder = {}

        def on_duty(action, item):
            # 假的 `main._duty_decided`：把决定写回板子再推回悬浮窗
            new = {"save": "saved", "ignore": "ignored", "undo": "pending"}.get(action)
            if new is None:
                return
            board[:] = [{**x, "action": new} if x["id"] == item["id"] else x
                        for x in board]
            holder["o"].duty_update([dict(x) for x in board])

        o = Overlay(on_duty=on_duty)
        holder["o"] = o
        o._clock = lambda: now[0]
        return o, now

    @staticmethod
    def _pump(o):
        """`_pump_once` 里与卡片相关的那三步（窗口没上屏时 pump 会整段跳过）。"""
        if o._card_shut:
            o._card_shut = False
            if o._card_kind:
                o._card_close()
        if o._card_redo:
            o._card_redo = False
            o._card_refill()
        o._duty_tick(o._clock())

    def test_卡片有收起而且关得掉(self):
        board = [_item("d1"), _item("d2", title="下周必须到场")]
        o, now = self._overlay(board)
        try:
            o.duty_update([dict(x) for x in board])
            o._duty_open("d1")
            self.assertEqual(o._card_kind, "duty")
            hdr = o._card_hdr_act
            self.assertIsNotNone(hdr, "一些事儿卡片没有右上角动作")
            self.assertEqual(hdr["text"], "收起")
            hdr["on_act"]()
            self.assertEqual(o._card_kind, "duty", "收起要**下一帧**关，不在回调里拆")
            self._pump(o)
            self.assertEqual(o._card_kind, "")
            self.assertTrue(o._chip_show, "还有待处理的 → 小卡回来")
        finally:
            o.close()

    def test_记下后翻到下一条再收起(self):
        board = [_item("d1"), _item("d2", title="下周必须到场")]
        o, now = self._overlay(board)
        try:
            o.duty_update([dict(x) for x in board])
            o._duty_open("d1")
            o._duty_act("save", o._duty_get("d1"))
            self._pump(o)
            self.assertEqual(o._duty_open_id, "d1", "停留期内还看着刚处理的那条（可撤销）")
            now[0] += motion.DUTY_DONE_HOLD_S
            self._pump(o)
            self.assertEqual((o._card_kind, o._duty_open_id), ("duty", "d2"))
            o._duty_act("ignore", o._duty_get("d2"))
            self._pump(o)
            now[0] += motion.DUTY_DONE_HOLD_S
            self._pump(o)
            self.assertEqual(o._card_kind, "", "全处理完 → 卡片收起")
            self.assertFalse(o._chip_show, "全处理完 → 小卡淡出")
        finally:
            o.close()

    def test_停留期撤销就不走(self):
        board = [_item("d1")]
        o, now = self._overlay(board)
        try:
            o.duty_update([dict(x) for x in board])
            o._duty_open("d1")
            o._duty_act("save", o._duty_get("d1"))
            self._pump(o)
            o._duty_act("undo", o._duty_get("d1"))
            self._pump(o)
            now[0] += motion.DUTY_DONE_HOLD_S * 2
            self._pump(o)
            self.assertEqual((o._card_kind, o._duty_open_id), ("duty", "d1"))
        finally:
            o.close()

    def test_提醒事项失败不自动收(self):
        board = [_item("d1")]
        o, now = self._overlay(board)
        try:
            o.duty_update([dict(x) for x in board])
            o._duty_open("d1")
            o._duty_act("save", o._duty_get("d1"))
            self._pump(o)
            o.duty_reminder_result("d1", False, "没有授权")
            now[0] += motion.DUTY_DONE_HOLD_S * 2
            self._pump(o)
            self.assertEqual(o._card_kind, "duty", "失败原因要让人看见")
        finally:
            o.close()

    def test_卡收起后才失败会重新摆出来(self):
        board = [_item("d1")]
        o, now = self._overlay(board)
        try:
            o.duty_update([dict(x) for x in board])
            o._duty_open("d1")
            o._duty_act("save", o._duty_get("d1"))
            self._pump(o)
            now[0] += motion.DUTY_DONE_HOLD_S
            self._pump(o)
            self.assertEqual(o._card_kind, "")
            o.duty_reminder_result("d1", False, "没有授权")
            self.assertEqual((o._card_kind, o._duty_open_id), ("duty", "d1"))
        finally:
            o.close()

    def test_小卡暗档缩短_新条目再展开(self):
        board = [_item("d1"), _item("d2", title="下周必须到场，期中前最后一次复习课")]
        o, now = self._overlay(board)
        try:
            o.duty_update([dict(x) for x in board])
            self.assertIn("下周必须到场", o._chip_lbl.stringValue())
            wide = float(o._chip_hit.frame().size.width)
            now[0] += motion.CHIP_HOT_S
            o._chip_tick(now[0])
            self.assertEqual(o._chip_lbl.stringValue(), "一些事儿 2")
            narrow = float(o._chip_hit.frame().size.width)
            self.assertLess(narrow, wide * 0.7, f"暗档没缩：{wide:.0f} → {narrow:.0f}")
            self.assertFalse(o._chip_hit.isHidden(), "缩短了照样能点")
            board.append(_item("d3", title="作业二截止"))
            o.duty_update([dict(x) for x in board])
            self.assertIn("作业二截止", o._chip_lbl.stringValue())
        finally:
            o.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
