#!/usr/bin/env python3
"""**字幕区顶边的渐隐**（作者 2026-10-17：final5/05-recap-a 里「卡片底下那行字幕被
拦腰切了，看着像坏了」）。

    ClassLive.app/Contents/MacOS/python tests/test_edge_fade.py

## 钉住什么

1. ⭐ 那 7 档是**纯数据**（`edge_fade.stops()`）：位置 0/15/30/45/60/75/87/100%，
   alpha 贴合 `y ≈ x^1.5` —— 判据直接比它，不读 AppKit。
2. ⭐ **遮罩铺满整层**（只把开头那段做成渐隐）：只画一条 24pt 的带子会把剩下整块
   视图一起抹掉（2026-10-17 实测：卡片底下直接空了）。
3. ⭐ 装在哪条边上：真 `Overlay` 上量 —— 转录区一 `set_frame` 就有遮罩，且顶边
   （＝卡片底下那条切割线）落在**最透明那端**；开卡 / 关卡通吃（同一条边）。
4. ⭐ 「被裁的行」真的要**存在**（这一档里视口顶边不落在行边界上），否则第 3 条空转。
5. ⭐ 卡片底与字幕区顶之间 ≥8pt（作者点名的那条间距）。

⚠️ 语料全是合成的（没有真实课堂文本）；不开网络、不写任何真实目录。
"""
from __future__ import annotations

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import edge_fade                                                     # noqa: E402


def _cg_alpha(cg) -> float:
    """`CGColorRef` 自己没有 `.alpha()` —— 经 `NSColor` 转一手再读（仓库里同法）。"""
    from AppKit import NSColor
    return float(NSColor.colorWithCGColor_(cg).alphaComponent())


def _item(rid="d1", **kw):
    return {"id": rid, "action": "pending", "type": "调课", "title": "本周三停课",
            "quote": "There is no class on Wednesday.", "date": "2026-10-14",
            "t": "13:52:56", **kw}


# ══════════════════════════════════════════════════════════════════════
# 一、纯数据（不碰 AppKit）
# ══════════════════════════════════════════════════════════════════════
class 纯数据(unittest.TestCase):
    def test_七档的位置与alpha(self):
        locs, alphas = edge_fade.stops()
        self.assertEqual(len(locs), len(alphas), "位置与 alpha 数量不等")
        self.assertEqual(len(locs), 8, "0% 那档 + 7 档 = 8 个停靠点")
        self.assertEqual(locs[0], 0.0)
        self.assertEqual(locs[-1], 1.0)
        self.assertEqual(alphas[0], 0.0, "顶边最外那档必须**全透明**（切割线靠它消失）")
        self.assertEqual(alphas[-1], 1.0, "带子走完必须**全不透明**")
        self.assertEqual([round(a, 2) for a in alphas],
                         [0.0, 0.05, 0.15, 0.30, 0.50, 0.70, 0.85, 1.0])

    def test_位置与alpha都是单调递增(self):
        locs, alphas = edge_fade.stops()
        self.assertEqual(list(locs), sorted(locs))
        self.assertEqual(list(alphas), sorted(alphas))
        self.assertEqual(len(set(locs)), len(locs), "位置不能重复（渐变会退化）")

    def test_曲线贴合x的一点五次方(self):
        """非线性（`y ≈ x^1.5`）—— 线性渐变在 Retina 上看得出来色阶断层。"""
        locs, alphas = edge_fade.stops()
        for x, a in zip(locs, alphas):
            self.assertLessEqual(abs(a - x ** 1.5), 0.08,
                                 f"x={x} 处 alpha={a} 离 x^1.5={x ** 1.5:.3f} 太远")

    def test_带子不高于视图(self):
        self.assertAlmostEqual(edge_fade.band_height(400.0), edge_fade.BAND_PX)
        self.assertAlmostEqual(edge_fade.band_height(10.0), 10.0,
                               msg="小窗时带子不许高过视图本身")
        self.assertGreaterEqual(edge_fade.band_height(0.0), 1.0)

    def test_遮罩铺满整层且尾巴不透明(self):
        """⭐ 只画一条 24pt 的带子 = 把剩下整块视图抹掉（实测事故）。"""
        locs, alphas = edge_fade._mask_stops(250.0, "top", edge_fade.BAND_PX)
        self.assertEqual(locs[0], 0.0)
        self.assertEqual(locs[-1], 1.0, "遮罩没铺满整层（尾巴会整块消失）")
        self.assertEqual(alphas[-1], 1.0, "带子之外必须**全不透明**")
        # 7 档压缩在开头 band/h 那一段里
        frac = edge_fade.BAND_PX / 250.0
        self.assertAlmostEqual(max(l for l in locs if l < 1.0), frac * 1.0, places=6)

    def test_底边渐隐是整条曲线倒过来(self):
        top_l, top_a = edge_fade._mask_stops(250.0, "top", edge_fade.BAND_PX)
        bot_l, bot_a = edge_fade._mask_stops(250.0, "bottom", edge_fade.BAND_PX)
        self.assertEqual(len(top_l), len(bot_l))
        self.assertEqual(bot_a[0], 1.0, "底边渐隐：顶头那档必须全不透明")
        self.assertEqual(bot_a[-1], 0.0, "底边渐隐：最底那档必须全透明")
        # 倒过来的位置也逐个对上
        self.assertEqual([round(x, 6) for x in bot_l],
                         [round(1.0 - x, 6) for x in reversed(top_l)])


# ══════════════════════════════════════════════════════════════════════
# 二、真窗口（overlay 的转录区）
# ══════════════════════════════════════════════════════════════════════
class 真窗口(unittest.TestCase):
    def _overlay(self):
        try:
            from overlay import Overlay
        except ModuleNotFoundError as e:
            self.skipTest(f"AppKit 不可用, 跳过: {type(e).__name__}: {e}")
        try:
            return Overlay()
        except Exception as e:                            # noqa: BLE001
            self.fail(f"Overlay() 构造失败: {type(e).__name__}: {e}")

    @staticmethod
    def _feed(o, n: int = 6) -> None:
        for i in range(n):
            o.finalize(f"I have finished the reading for week {i}.",
                       f"第 {i} 周的阅读我已经做完了。")

    def _mask(self, o):
        return o._tv._scroll.layer().mask()

    def _top_cut(self, o) -> tuple[int, float]:
        """顶上那一行被切掉多少 pt（`(行号, 切掉量)`）—— 0 = 视口顶正好落在行边界。"""
        tv = o._tv
        clip = tv._scroll.contentView()
        top = float(clip.bounds().origin.y) + float(clip.bounds().size.height)
        row_h = float(tv._row_h)
        level = int(math.floor(top / row_h))
        return level, (level + 1) * row_h - top

    def test_字幕区一set_frame就有遮罩(self):
        o = self._overlay()
        try:
            self.assertTrue(edge_fade.installed(o._tv._scroll),
                            "转录区的滚动视图上没有渐隐遮罩")
        finally:
            o.close()

    def test_遮罩跟着视口尺寸走(self):
        """卡片开着时字幕区变小 —— 遮罩的停靠点要**按新高度重算**（不是留旧的）。"""
        o = self._overlay()
        try:
            self._feed(o)
            o.duty_update([_item("d1")])
            o._duty_open("d1")
            tv = o._tv._scroll
            m = self._mask(o)
            self.assertIsNotNone(m, "开卡之后遮罩没了")
            locs = [float(x) for x in m.locations()]
            frac = edge_fade.band_height(float(tv.bounds().size.height)) \
                / float(tv.bounds().size.height)
            self.assertAlmostEqual(max(l for l in locs if l < 1.0), frac, places=5,
                                   msg="停靠点没跟着视口高度重算")
            self.assertAlmostEqual(float(m.frame().size.height),
                                   float(tv.bounds().size.height), delta=0.6,
                                   msg="遮罩没铺满整层")
        finally:
            o.close()

    def test_底部最透明_带子走完全不透明(self):
        """⭐⭐ C1（2026-10-18 审查）：底边渐隐的**透明端必须在视口底**。

        ⚠️ 渐变方向只由 alpha 曲线决定 —— `apply()` 两条 edge **同一组 start/end**
        （location 0 = 视口顶）。第一版两条 edge 各翻一次 startPoint，方向叠翻转，
        `edge="bottom"` 与 `"top"` 完全等价（底边那条带子根本没出现）。
        ⚠️ 这条必须在**真的装上去之后**读（`_mask_stops` 单看是看不出来的）。
        """
        o = self._overlay()
        try:
            tv = o._tv._scroll
            self.assertTrue(edge_fade.apply(tv, edge="bottom"), "装不上遮罩")
            m = tv.layer().mask()
            # 变异验证：把 `apply()` 里 `if edge == "top" ... else startPoint=(0.5, 0.0)`
            #    改回来 -> 这条红（location 0 落在视口**底**，底边渐隐变成顶边渐隐）。
            self.assertAlmostEqual(float(m.startPoint().y), 1.0, places=3,
                                   msg="location 0 不在视口顶 —— 底边方向又被翻了一次")
            self.assertAlmostEqual(float(m.endPoint().y), 0.0, places=3)
            alphas = [_cg_alpha(c) for c in m.colors()]
            self.assertAlmostEqual(alphas[0], 1.0, places=3,
                                   msg="视口顶该全不透明（底边渐隐只动底边）")
            self.assertAlmostEqual(alphas[-1], 0.0, places=3,
                                   msg="视口底不是全透明 —— 底边那条带子根本没出现")
        finally:
            o.close()

    def test_顶部最透明_带子走完全不透明(self):
        o = self._overlay()
        try:
            self._feed(o)
            o.duty_update([_item("d1")])
            o._duty_open("d1")
            m = self._mask(o)
            alphas = [_cg_alpha(c) for c in m.colors()]
            self.assertAlmostEqual(alphas[0], 0.0, places=3,
                                   msg="视口顶边那档不是全透明 —— 切割线会看得见")
            self.assertAlmostEqual(alphas[-1], 1.0, places=3,
                                   msg="带子走完不全不透明 —— 字幕会被吃掉")
            self.assertEqual(alphas, sorted(alphas), "alpha 不单调（会出现亮-暗-亮）")
            # ⚠️ 渐变**方向**也钉住：起点必须是视口顶（`startPoint.y=1`）——
            #    写反了整条带子会跑到底边（拍图核过：`fade-check2.png`）。
            #    ⚠️ 只钉配置、钉不住肉眼效果，所以这条和那张图是**一对**。
            sp, ep = m.startPoint(), m.endPoint()
            self.assertAlmostEqual(float(sp.y), 1.0, places=3,
                                   msg=f"渐变的起点不在视口顶（startPoint={tuple(sp)}）")
            self.assertAlmostEqual(float(ep.y), 0.0, places=3)
            self.assertEqual(float(sp.x), float(ep.x))
        finally:
            o.close()

    def test_被裁的那行落在渐隐带里(self):
        """⭐ 作者点名的那条：顶上被切的那一行，切割线必须落在**渐隐带最透明那端**。

        ⚠️ 先证明这一档里**真的有一行被切**（`cut > 0.5`）—— 否则上面那条空转。
        """
        o = self._overlay()
        try:
            self._feed(o, 8)
            o.duty_update([_item("d1")])
            o._duty_open("d1")
            _level, cut = self._top_cut(o)
            self.assertGreater(cut, 0.5,
                               f"视口顶边正好压在行边界上（cut={cut:.2f}）—— 这一档取不到「被裁的行」")
            self.assertLess(cut, float(o._tv._row_h), "切掉量不该超过一行")
            band = edge_fade.band_height(float(o._tv._scroll.bounds().size.height))
            self.assertGreaterEqual(band, 1.0)
            # 切割线 = 视口顶边 = 带子的起点（那一端 α=0，所以切开的地方看不见）
            m = self._mask(o)
            self.assertEqual(float(m.locations()[0]), 0.0,
                             "遮罩的起点不在视口顶边 —— 切割线落在了实心区")
            self.assertAlmostEqual(_cg_alpha(m.colors()[0]), 0.0, places=3)
        finally:
            o.close()

    def test_关卡通吃_没有卡片时也贴着窗口内容区顶边(self):
        o = self._overlay()
        try:
            self._feed(o)
            o.duty_update([_item("d1")])
            o._duty_open("d1")
            during = float(o._tv._scroll.frame().origin.y
                           + o._tv._scroll.frame().size.height)
            o._card_close()
            after = float(o._tv._scroll.frame().origin.y
                          + o._tv._scroll.frame().size.height)
            self.assertTrue(edge_fade.installed(o._tv._scroll), "关卡后遮罩掉了")
            self.assertGreater(after, during + 1.0,
                               "关卡后字幕区没有恢复全高（旧版那类回归）")
        finally:
            o.close()

    def test_卡片底与字幕区顶至少八点(self):
        """作者点名的间距：卡片底 ↔ 字幕区顶 ≥8pt（不然渐隐带会顶到卡片边上）。"""
        o = self._overlay()
        try:
            self._feed(o)
            for kind in ("duty", "wrapup", "recap"):
                o._card_close()
                if kind == "duty":
                    o.duty_update([_item("d1"), _item("d2")])
                    o._duty_open("d1")
                elif kind == "wrapup":
                    o.duty_wrapup_show([_item("d1"), _item("d2")])
                else:
                    o.recap_show({"course": "ECON10790", "date": "10-15（周四）",
                                  "points": [{"name": "知识点", "explain": "一句解释"}],
                                  "stars": ["重点"], "open": ["还没懂的"],
                                  "duties": [], "today": ["今天新课件"]},
                                 course="ECON10790", day="2026-10-16")
                # ⚠️ 先证明这张卡**真的开着** —— 否则量的是上一张卡留下的旧 frame
                #    （2026-10-17 本测试第一版就栽在这：收尾卡没显示，量出 -232.8）。
                self.assertFalse(bool(o._card_view.isHidden()), f"{kind}：卡没开")
                gap = float(o._card_view.frame().origin.y) - float(
                    o._tv._scroll.frame().origin.y + o._tv._scroll.frame().size.height)
                self.assertGreaterEqual(gap + 0.5, 8.0,
                                        f"{kind}: 卡片底与字幕区顶只隔了 {gap:.1f}pt")
        finally:
            o.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
