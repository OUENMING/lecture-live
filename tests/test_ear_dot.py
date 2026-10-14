"""「在听」小点的亮灭判据（`Overlay.hearing`）：快亮、保持 `EAR_HOLD_S` 才灭、灭到一半又亮从半亮接着走。

不起窗口：把 `hearing` 当普通函数调在一个替身上（只要 `_clock` / `_ear.layer()` / 两个状态字段）。
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import overlay as O                     # noqa: E402


class _Layer:
    def __init__(self):
        self.op, self.anims, self.pres = O.EAR_QUIET_ALPHA, [], None

    def opacity(self):
        return self.op

    def setOpacity_(self, v):
        self.op = v

    def presentationLayer(self):
        return self.pres

    def addAnimation_forKey_(self, a, k):
        self.anims.append((float(a.fromValue()), float(a.duration())))


def _fake():
    t = {"now": 0.0}
    lay = _Layer()
    me = types.SimpleNamespace(_clock=lambda: t["now"], _ear=types.SimpleNamespace(layer=lambda: lay),
                               _hearing=False, _heard_at=-1e9)
    return me, lay, t


def feed(me, t, flags, dt=0.1):
    for f in flags:
        t["now"] += dt
        O.Overlay.hearing(me, f)


class EarDot(unittest.TestCase):
    def test_有人声立刻亮(self):
        me, lay, t = _fake()
        feed(me, t, [True])
        self.assertEqual(lay.op, O.EAR_ON_ALPHA)
        self.assertEqual(len(lay.anims), 1)

    def test_短停顿不灭_这就是治闪的那条(self):
        me, lay, t = _fake()
        n = int(O.EAR_HOLD_S / 0.1) - 1          # 停顿短于保持时长
        feed(me, t, [True] + [False] * n + [True])
        self.assertEqual(lay.op, O.EAR_ON_ALPHA)
        self.assertEqual(len(lay.anims), 1, "短停顿里一次动画都不该有")

    def test_停够保持时长才灭_而且慢灭(self):
        me, lay, t = _fake()
        feed(me, t, [True] + [False] * (int(O.EAR_HOLD_S / 0.1) + 2))
        self.assertEqual(lay.op, O.EAR_QUIET_ALPHA)
        self.assertGreater(lay.anims[-1][1], lay.anims[0][1], "灭要比亮慢")

    def test_灭到一半又亮_从正在显示的值接着走(self):
        me, lay, t = _fake()
        feed(me, t, [True] + [False] * (int(O.EAR_HOLD_S / 0.1) + 2))
        lay.pres = types.SimpleNamespace(opacity=lambda: 0.5)
        feed(me, t, [True])
        self.assertEqual(lay.anims[-1][0], 0.5)


if __name__ == "__main__":
    unittest.main()
