#!/usr/bin/env python3
"""下课**收尾汇总**出现的条件（作者 2026-10-17 问：「没识别到要记录的东西，下课那个板块
是不是就没有」）。

答案：**是，不出现** —— `duty_wrapup_show` 没有待处理条目就返回 `False`（一张空卡
不如不弹：下课那一刻最不该多看一屏没内容的东西）。这条行为本来只写在注释里，
现在钉成判据；超时（20 秒没人理）同样**什么都不写**，笔记里那几条标「未确认」。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import overlay  # noqa: E402


def _item(rid="d1", action="pending", **kw):
    return {"id": rid, "action": action, "type": "调课", "title": "本周三停课",
            "quote": "There is no class on Wednesday.", **kw}


class _Stub:
    """只够走 `duty_wrapup_show` 的**提前返回**那两支（0 条 / 全处理完）。"""
    _closed = False


class 没有待处理就不出现(unittest.TestCase):
    def test_一条都没有(self):
        self.assertFalse(overlay.Overlay.duty_wrapup_show(_Stub(), []))

    def test_空列表与_None_一样(self):
        self.assertFalse(overlay.Overlay.duty_wrapup_show(_Stub(), None))

    def test_全都处理过了(self):
        self.assertFalse(overlay.Overlay.duty_wrapup_show(
            _Stub(), [_item("d1", "saved"), _item("d2", "ignored")]))

    def test_窗口已经关了(self):
        class _Closed:
            _closed = True
        self.assertFalse(overlay.Overlay.duty_wrapup_show(_Closed(), [_item()]))


class 真窗口(unittest.TestCase):
    def _overlay(self):
        try:
            from overlay import Overlay
        except ModuleNotFoundError as e:
            self.skipTest(f"AppKit 不可用, 跳过: {e}")
        try:
            return Overlay()
        except Exception as e:                            # noqa: BLE001
            self.fail(f"Overlay() 构造失败: {type(e).__name__}: {e}")

    def test_有待处理才摆卡(self):
        o = self._overlay()
        try:
            self.assertTrue(o.duty_wrapup_show([_item()]))
            self.assertEqual(o._card_kind, "wrapup")
            o.duty_wrapup_finish()
            self.assertNotEqual(o._card_kind, "wrapup")
        finally:
            o.close()

    def test_超时等于什么都没写(self):
        """⭐ 20 秒没人理 → 一个铺子都不动（返回 0，条目原样待处理）。"""
        o = self._overlay()
        try:
            seen = []
            o._on_duty = lambda a, it: seen.append((a, it.get("id")))
            items = [_item("d1"), _item("d2")]
            self.assertEqual(o.duty_wrapup(items, timeout=0.0), 0)
            self.assertEqual(seen, [], "超时路径不该回任何决定")
            self.assertNotEqual(o._card_kind, "wrapup")
        finally:
            o.close()

    def test_全部忽略只对没处理的那几条回决定(self):
        """「全部忽略」= 一键把**还没处理的**都忽略；已记下的不动。"""
        o = self._overlay()
        try:
            seen = []
            o._on_duty = lambda a, it: seen.append((a, it.get("id")))
            o.duty_wrapup_show([_item("d1"), _item("d2", "saved")])
            o._wrapup_act("all")
            self.assertEqual(seen, [("ignore", "d1")])
        finally:
            o.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
