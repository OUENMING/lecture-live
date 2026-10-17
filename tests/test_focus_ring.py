#!/usr/bin/env python3
"""**输入框的聚焦指示**：去掉系统蓝环、改白色细框（作者 2026-10-17 拍板）。

    ClassLive.app/Contents/MacOS/python tests/test_focus_ring.py

## 为什么要这一组

那个蓝圈是**系统聚焦环**，颜色不可设（`NSFocusRingType` 只有三个枚举、无颜色参数），
它取用户的系统强调色。2026-09-29 那次以「关掉默认指示却不补替代 = W3C F78
（1.4.11 / 2.4.7）」为由放弃；现在补上了替代（白细框），于是可以关。
⚠️ 而**关环的写法有个坑**：枚举里 `Default = 0`、`None = 1` —— 卡片输入框那行
   原来写的是 `setFocusRingType_(0)`，注释却写着「去掉蓝环」，于是环一直在。

## 钉住什么

1. ⭐ 枚举那个坑**用常量钉住**（判据直接读 AppKit 的值：0 是 Default、1 是 None）。
2. ⭐ 三个窗口里**所有可编辑 `NSTextField`** 的 `focusRingType()` 都必须是 `None`
   （overlay 的 ⌨ 提问行 / 卡片标题与日期框；课程面板的搜索框与「＋新建」框）。
3. ⭐ 替代指示真的会亮：**成为第一响应者 → 描边变亮**；**编辑结束 → 复位**
   （卡片那两格还要证明**只有正在编辑的那一格**亮 —— 旧版是全都亮）。
4. ⭐ 兜底：钩子没来的一拍之内由守卫同步回来（面板那条 0.5s 链）。
5. ⭐ **句柄不许把控件钉住**（`控件 → 句柄 → 控件` 那种环，PyObjC 收不回 —— 实测过）。

⚠️ 语料全是合成的；窗口是隔离目录里的真窗口（`HOME` 换过、不碰真数据）。
"""
from __future__ import annotations

import gc
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import panel                                                       # noqa: E402


def _editable_fields(root) -> list:
    """窗口（或任意视图）里**所有可编辑的 `NSTextField`**（含子类，如 NSSearchField）。"""
    from AppKit import NSTextField
    out: list = []

    def walk(v):
        try:
            if isinstance(v, NSTextField) and bool(v.isEditable()):
                out.append(v)
            for s in v.subviews():
                walk(s)
        except Exception:                                     # noqa: BLE001
            pass

    walk(root)
    return out


def _pump(n: int = 8, dt: float = 0.02) -> None:
    """跑几拍 run loop（字段编辑器换防 / 通知都是在这上面发生的）。"""
    from AppKit import NSApp, NSDate, NSDefaultRunLoopMode
    for _ in range(n):
        ev = NSApp.nextEventMatchingMask_untilDate_inMode_dequeue_(
            2 ** 30, NSDate.dateWithTimeIntervalSinceNow_(dt), NSDefaultRunLoopMode, True)
        if ev:
            NSApp.sendEvent_(ev)


def _pump_until(pred, budget: float = 2.0, dt: float = 0.02) -> bool:
    """一直转到 `pred()` 为真（或超时）。

    ⚠️ **别写死拍数**：字段编辑器的换防要走几拍 run loop，慢机器上拍数写死了就忽红忽绿
       （2026-10-17 第一版写死 8 拍，搜索框换到「＋新建」框那条就红了）。
    """
    import time
    t0 = time.time()
    while time.time() - t0 < budget:
        if pred():
            return True
        _pump(2, dt)
    return bool(pred())


def _cg_alpha(cg) -> float:
    from AppKit import NSColor
    if cg is None:
        return -1.0
    return float(NSColor.colorWithCGColor_(cg).alphaComponent())


def _item(rid="d1", **kw):
    return {"id": rid, "action": "pending", "type": "调课", "title": "本周三停课",
            "quote": "There is no class on Wednesday.", "date": "2026-10-14",
            "t": "13:52:56", **kw}


# ══════════════════════════════════════════════════════════════════════
# 一、常量（不碰窗口）
# ══════════════════════════════════════════════════════════════════════
class 常量(unittest.TestCase):
    def test_两档alpha有大小关系(self):
        self.assertGreater(panel.FOCUS_RING_ON, panel.FOCUS_RING_IDLE)
        self.assertGreater(panel.FOCUS_RING_IDLE, 0.0)
        self.assertLessEqual(panel.FOCUS_RING_ON, 1.0)

    def test_发丝边是一物理像素(self):
        import AppKit
        try:
            from AppKit import NSScreen
            scale = float(NSScreen.mainScreen().backingScaleFactor())
        except Exception:                                     # noqa: BLE001
            self.skipTest("没有屏幕信息，跳过")
        self.assertAlmostEqual(panel.hairline() * scale, 1.0, places=3,
                               msg="描边不是 1 物理像素（Retina 上会糊成 2px）")
        self.assertIsNotNone(AppKit)

    def test_枚举的坑用常量钉住(self):
        """⭐ `NSFocusRingTypeDefault = 0`、`NSFocusRingTypeNone = 1` —— 写 0 是"要默认"。"""
        from AppKit import (NSFocusRingTypeDefault, NSFocusRingTypeNone,
                            NSFocusRingTypeExterior)
        self.assertEqual(int(NSFocusRingTypeDefault), 0)
        self.assertEqual(int(NSFocusRingTypeNone), 1, "None 不是 1 了？去查 SDK")
        self.assertEqual(int(NSFocusRingTypeExterior), 2)

    def test_全仓不许给环形传数字(self):
        """⭐ 源码级（AST）：全仓**每一处** `setFocusRingType_()` 都得传**常量**。

        ⚠️ 用 AST 而不是找字符串 —— 注释和 docstring 里正大光明地讲着这个坑，
           找字符串会把说明文字本身判成违规（本测试第一版就栽在这）。
        """
        import ast
        root = pathlib.Path(__file__).resolve().parent.parent
        bad: list = []
        seen = 0
        for f in sorted(root.glob("*.py")):
            tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "setFocusRingType_"):
                    seen += 1
                    if node.args and isinstance(node.args[0], ast.Constant):
                        bad.append((f.name, node.lineno, node.args[0].value))
        self.assertGreaterEqual(seen, 1, "一处 setFocusRingType_ 都没扫到 —— 判据空转")
        self.assertEqual(bad, [], f"给系统聚焦环传了数字（0=Default，环照画）：{bad}")


# ══════════════════════════════════════════════════════════════════════
# 二、悬浮窗（⌨ 提问行 + 卡片两格）
# ══════════════════════════════════════════════════════════════════════
class 悬浮窗(unittest.TestCase):
    def _overlay(self):
        try:
            from overlay import Overlay
        except ModuleNotFoundError as e:
            self.skipTest(f"AppKit 不可用, 跳过: {type(e).__name__}: {e}")
        try:
            return Overlay()
        except Exception as e:                                # noqa: BLE001
            self.fail(f"Overlay() 构造失败: {type(e).__name__}: {e}")

    def _open_card(self, o):
        o.duty_update([_item("d1")])
        o._duty_open("d1")
        return [b for b in o._card_pending if b["kind"] == "field"]

    def test_窗口里所有输入框都没有系统环(self):
        from AppKit import NSFocusRingTypeNone
        o = self._overlay()
        try:
            self._open_card(o)
            fields = _editable_fields(o._panel.contentView())
            self.assertGreaterEqual(len(fields), 3,
                                    f"扫到的输入框太少（{len(fields)}）—— 判据会空转")
            bad = [(type(f).__name__, int(f.focusRingType())) for f in fields
                   if int(f.focusRingType()) != int(NSFocusRingTypeNone)]
            self.assertEqual(bad, [], f"还有系统聚焦环：{bad}")
        finally:
            o.close()

    def test_卡片两格都有替代指示且静止时是一档(self):
        o = self._overlay()
        try:
            blks = self._open_card(o)
            self.assertEqual(len(blks), 2, f"卡片里的输入行不是 2 格：{len(blks)}")
            for blk in blks:
                h = o._card_focus.get(blk["fld"])
                self.assertIsNotNone(h, f"{blk['key']}：外壳上没有聚焦指示句柄")
                self.assertTrue(h.ok(), f"{blk['key']}：指示没装到外壳上")
                self.assertIs(h.layer, blk["box"].layer(),
                              f"{blk['key']}：指示没画在外壳那一层上")
                self.assertAlmostEqual(h.alpha(), panel.FOCUS_RING_IDLE, places=3)
        finally:
            o.close()

    def test_只亮正在编辑的那一格(self):
        """⭐ 旧版是「谁在打字都把所有外壳一起提亮」—— 与 docstring 说的「那一格」不符。"""
        o = self._overlay()
        try:
            o.show()
            blks = {b["key"]: b for b in self._open_card(o)}
            title, date = blks["title"], blks["date"]
            o._panel.makeFirstResponder_(title["fld"])
            for _ in range(4):
                o.pump()
            if title["fld"].currentEditor() is None:
                self.skipTest("这台机器上拿不到字段编辑器（窗口不是 key?），跳过")
            ht = o._card_focus[title["fld"]]
            hd = o._card_focus[date["fld"]]
            self.assertAlmostEqual(ht.alpha(), panel.FOCUS_RING_ON, places=3,
                                   msg="正在编辑的标题框没提亮")
            self.assertAlmostEqual(hd.alpha(), panel.FOCUS_RING_IDLE, places=3,
                                   msg="日期框被连坐提亮了（旧版那个「全都亮」）")
            o._release_focus()
            for _ in range(4):
                o.pump()
            self.assertAlmostEqual(ht.alpha(), panel.FOCUS_RING_IDLE, places=3,
                                   msg="结束编辑后没复位")
        finally:
            o.close()

    def test_提问行的细线跟着编辑状态走(self):
        """⌨ 提问行的替代指示是它**自己那条细线**（同一种语言：白 α 0.18 → 更亮）。"""
        o = self._overlay()
        try:
            o.show()
            rule = o._input_rule
            o._sync_focus_look()
            idle = _cg_alpha(rule.layer().backgroundColor())
            self.assertAlmostEqual(idle, panel.FOCUS_RING_IDLE, places=3,
                                   msg="静止态细线不是共享的那一档（0.18）")
            o._panel.makeFirstResponder_(o._input)
            o._sync_focus_look()
            focused = _cg_alpha(rule.layer().backgroundColor())
            self.assertGreater(focused, idle, "聚焦时细线没变亮")
            o._release_focus()
            o._sync_focus_look()
            self.assertAlmostEqual(_cg_alpha(rule.layer().backgroundColor()), idle,
                                   places=3, msg="失焦后细线没复位")
        finally:
            o.close()

    def test_卡片那格换cell之后要重新关环(self):
        """⭐ 环的类型挂在 **cell** 上：`setCell_()` 会把它换回默认（环照画）。

        那条判据（`窗口里所有输入框都没有系统环`）2026-10-17 抓住的就是这个 ——
        外壳、句柄全对，只有环类型是 0。
        """
        src = pathlib.Path("overlay.py").read_text(encoding="utf-8")
        body = src.split("def _card_field_box", 1)[1].split("\n    def ", 1)[0]
        self.assertIn("panel.focus_field", body)
        self.assertLess(body.index("setCell_"), body.index("panel.no_focus_ring"),
                        "关环那一步跑到换 cell 前面去了 —— 会被换掉")


# ══════════════════════════════════════════════════════════════════════
# 三、课程面板（搜索框 + 「＋新建」框）
# ══════════════════════════════════════════════════════════════════════
class 课程面板(unittest.TestCase):
    def _build(self):
        try:
            from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
        except ModuleNotFoundError as e:
            self.skipTest(f"AppKit 不可用, 跳过: {type(e).__name__}: {e}")
        NSApplication.sharedApplication().setActivationPolicy_(
            NSApplicationActivationPolicyAccessory)
        import entry_panel as E
        import ready as ready_mod
        td = tempfile.TemporaryDirectory()
        root = pathlib.Path(td.name)
        home = root / "home"
        home.mkdir()
        os.environ["HOME"] = str(home)
        os.environ.pop("OBSIDIAN_VAULT", None)
        _orig = ready_mod.items
        ready_mod.items = lambda **k: []
        try:
            h = E.build(glossary=None, sessions_dir=root / "s", state_root=root / "st",
                        on_start=lambda c: None, on_test_mode=lambda on: True,
                        on_cloud_asr=lambda c: True, cloud_asr=True)
        finally:
            ready_mod.items = _orig
        return E, h, td

    def test_两个框都没有系统环且都有替代指示(self):
        from AppKit import NSFocusRingTypeNone, NSSearchField
        E, h, td = self._build()
        try:
            self.assertIsNotNone(h)
            fields = _editable_fields(h.window.contentView())
            self.assertEqual(len(fields), 2, f"面板里的输入框不是 2 个：{len(fields)}")
            for f in fields:
                self.assertEqual(int(f.focusRingType()), int(NSFocusRingTypeNone),
                                 f"{type(f).__name__} 还有系统聚焦环")
                handle = getattr(f, "_focus_border", None)
                self.assertIsNotNone(handle, f"{type(f).__name__} 没有替代指示")
                self.assertTrue(handle.ok())
                self.assertAlmostEqual(handle.alpha(), panel.FOCUS_RING_IDLE, places=3)
            self.assertTrue(any(isinstance(f, NSSearchField) for f in fields),
                            "搜索框不在扫描结果里 —— 判据会空转")
        finally:
            E.close_panel()
            td.cleanup()

    def test_聚焦亮_失焦复位_并且换框不连坐(self):
        from AppKit import NSSearchField
        E, h, td = self._build()
        try:
            fields = _editable_fields(h.window.contentView())
            search = [f for f in fields if isinstance(f, NSSearchField)][0]
            add = [f for f in fields if not isinstance(f, NSSearchField)][0]
            hs, ha = search._focus_border, add._focus_border
            on, idle = panel.FOCUS_RING_ON, panel.FOCUS_RING_IDLE
            _pump(20)                       # 让 build() 里那个 0.2s 的 callLater 先跑掉
            self.assertTrue(_pump_until(lambda: search.acceptsFirstResponder()),
                            "搜索框一直拒绝第一响应者（refusesFirstResponder 没放行）")
            h.window.makeFirstResponder_(search)
            self.assertTrue(_pump_until(lambda: hs.alpha() > idle + 0.01),
                            f"点进搜索框后没亮（α={hs.alpha():.3f}）")
            self.assertAlmostEqual(ha.alpha(), idle, places=3, msg="另一个框被连坐")
            h.window.makeFirstResponder_(add)
            self.assertTrue(_pump_until(lambda: ha.alpha() > idle + 0.01),
                            f"点「＋新建」框后没亮（α={ha.alpha():.3f}）")
            self.assertTrue(_pump_until(lambda: abs(hs.alpha() - idle) < 0.01),
                            f"换框后搜索框没复位（α={hs.alpha():.3f}）")
            self.assertAlmostEqual(hs.alpha(), idle, places=3)
            h.window.makeFirstResponder_(None)
            self.assertTrue(_pump_until(lambda: abs(ha.alpha() - idle) < 0.01),
                            f"失焦后没复位（α={ha.alpha():.3f}）")
            self.assertAlmostEqual(hs.alpha(), idle, places=3)
        finally:
            E.close_panel()
            td.cleanup()

    def test_守卫一拍之内把歪掉的指示掰回来(self):
        """钩子万一没来（面板失 key / 程序化挪焦点）—— 守卫那条 0.5s 链要兜住。"""
        E, h, td = self._build()
        try:
            field = [f for f in h.focus_fields if f is not None][0]
            handle = field._focus_border
            handle.set_focus(True)                    # 手工点歪
            self.assertAlmostEqual(handle.alpha(), panel.FOCUS_RING_ON, places=3)
            E._guard_tick(E._GUARD, E._GUARD.get("gen"))
            self.assertAlmostEqual(handle.alpha(), panel.FOCUS_RING_IDLE, places=3,
                                   msg="守卫没把歪掉的指示掰回来")
        finally:
            E.close_panel()
            td.cleanup()


# ══════════════════════════════════════════════════════════════════════
# 四、生命周期（句柄不许成环）
# ══════════════════════════════════════════════════════════════════════
class 生命周期(unittest.TestCase):
    def test_句柄不许把控件钉住(self):
        """⭐ `控件 → 句柄 → 控件` 的环 PyObjC 收不回（实测 3 个一个都不回收）。

        判据取「≤1」而不是「=0」：AppKit 自己会留一个（裸控件实测也是 1）。
        """
        try:
            from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
            NSApplication.sharedApplication().setActivationPolicy_(
                NSApplicationActivationPolicyAccessory)
        except ModuleNotFoundError as e:
            self.skipTest(f"AppKit 不可用, 跳过: {type(e).__name__}: {e}")
        from AppKit import NSTextField, NSMakeRect
        key = "FocusRingLeakProbe"

        def alive() -> int:
            gc.collect()
            gc.collect()
            return sum(1 for o in gc.get_objects()
                       if not isinstance(o, type)
                       and type(o).__name__ == "_ClassLive" + key)

        base = alive()
        for _ in range(3):
            f, _h = panel.focus_field(NSTextField, NSMakeRect(0, 0, 10, 24),
                                      radius=6.0, key=key)
        n = alive() - base
        self.assertLessEqual(n, 1, f"造 3 个扔 3 个，留下 {n} 个 —— 句柄成环了")


if __name__ == "__main__":
    unittest.main(verbosity=2)
