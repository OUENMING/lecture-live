#!/usr/bin/env python3
"""滚动区的**边缘渐隐**（7 档非线性 alpha 遮罩）—— 全 app 只此一份。

作者 2026-10-17：final5/05-recap-a 里「卡片底下那行字幕被拦腰切了，看着像坏了」——
要求**字幕区顶边**用上「窗口顶边那套 7 级渐隐」并留出和卡片底的间距。

## 为什么是 7 档而不是线性渐变

研究见 `scratchpad/research/cc-motion/report.md`（Claude 桌面端 ASC 逆向 + 官方 CSS）：
`--cds-scroll-fade-size: 28px` + **7 档 alpha**，停靠点 15/30/45/60/75/87/100%，
数值贴合 `y ≈ x^1.5` 的感知幂律 —— 线性渐变在 Retina 上会被看出**色阶断层**
（同一份研究还记了它「触顶/触底时遮罩深度自动归零」的做法，这里不需要：我们只做顶边）。

## 接口（三个名字，一个纯数据 + 两个薄封装）

| 名字 | 干什么 |
|---|---|
| `BAND_PX` | 渐隐带的高度（pt） |
| `stops()` | `(locations, alphas)` —— **纯数据**，判据直接比它（不碰 AppKit） |
| `apply(view, *, edge="top", px=BAND_PX)` | 给一个视图的图层装（或重装）这条遮罩；装不上回 `False` |
| `band_height(h, px=BAND_PX)` | 这条带在**高 h 的视图**里有多高（判据用它算"被裁的行在不在带里"） |

⚠️ 遮罩是**图层**（`layer().setMask_`），所以视图得是 layer-backed —— `apply` 会顺手
   `setWantsLayer_(True)`（`whatsnew.py` 的底边渐隐是同一个做法）。
⚠️ **别在多处各装一条**：同一个视图的 `layer().mask()` 只有一个，后装的会顶掉前一条 ——
   所以「谁负责装」也只此一处（转录区的 `set_frame`），别在调用点到处补。
⚠️ **遮罩要铺满整层**（只把开头那一段做成渐隐）：`layer().setMask_` 的语义是
   「遮罩外 = 全透明」—— 只画一条 24pt 的带子会把剩下整块视图**一起抹掉**
   （2026-10-17 实测：卡片底下直接空了）。见 `_mask_stops`。
"""
from __future__ import annotations

#: 渐隐带高度（pt）。研究里的 28px @2x ≈ 14pt；这里取 **24pt** —— 一行的中文字框
#: （`ROW_ZH_H` 47pt）被切掉一半也还落在带里（作者点名的判据就是这一条）。
BAND_PX = 24.0

#: 7 档（+ 全透明那档 = 8 个停靠点）：位置 0/15/30/45/60/75/87/100%，
#: alpha = `y ≈ x^1.5`（15%→.05、30%→.15、45%→.30、60%→.50、75%→.70、87%→.85、100%→1.0）。
_LOCATIONS = (0.0, 0.15, 0.30, 0.45, 0.60, 0.75, 0.87, 1.0)
_ALPHAS = (0.0, 0.05, 0.15, 0.30, 0.50, 0.70, 0.85, 1.0)


def stops():
    """`(locations, alphas)` —— **纯数据**（判据比它，不去读 AppKit 的图层）。"""
    return _LOCATIONS, _ALPHAS


def band_height(view_h: float, px: float = BAND_PX) -> float:
    """这条带在**高 `view_h` 的视图**里实际多高（不高于视图本身 —— 小窗别整屏都糊）。"""
    try:
        return max(1.0, min(float(px), float(view_h)))
    except (TypeError, ValueError):
        return float(px)


def _mask_stops(view_h: float, edge: str, px: float):
    """整层遮罩的 `(locations, alphas)` —— 带子只占开头那 `band/h`，**其余全是不透明**。

    ⚠️⚠️ 遮罩图层**必须铺满整个视图**：`layer().setMask_` 的语义是"遮罩外 = 全透明"，
       只画一条 24pt 的带子会把**剩下整块视图都抹掉**（2026-10-17 实测：卡片底下
       直接空了）。所以做法是"整层不透明 + 开头压缩进 7 档渐隐"。
    """
    h = max(1.0, float(view_h))
    band = band_height(h, px)
    frac = max(0.0, min(1.0, band / h))
    locs, alphas = stops()
    out_loc, out_a = [0.0], [0.0]
    for p, a in zip(locs, alphas):
        v = frac * p
        if v <= out_loc[-1]:                              # 退化（带子=整层 / 极小窗）
            continue
        out_loc.append(v)
        out_a.append(a)
    if out_loc[-1] < 1.0:
        out_loc.append(1.0)
        out_a.append(1.0)
    if edge == "bottom":
        # 底边渐隐 = 把整条曲线**倒过来**（顶边那段变成实心）。⚠️ `apply()` **两个 edge
        # 用同一组 start/end**（location 0 = 视口顶），所以方向只由这里的 alpha 决定 ——
        # 别再去翻 `apply` 的 startPoint（翻转叠翻转 = 底边渐隐根本没出现，审查 C1）。
        out_loc = [1.0 - v for v in reversed(out_loc)]
        out_a = list(reversed(out_a))
    return out_loc, out_a


def start_end(flipped: bool) -> tuple:
    """渐变的起止点：起点 = **视觉顶边**。图层几何翻转时 y=0 才是顶。**纯函数。**"""
    return ((0.5, 0.0), (0.5, 1.0)) if flipped else ((0.5, 1.0), (0.5, 0.0))


def apply(view, *, edge: str = "top", px: float = BAND_PX) -> bool:
    """给 `view` 装（或重装）边缘渐隐遮罩。**fail-soft**：装不上回 `False`，绝不抛。

    ⚠️ 每次视图尺寸变了都要再调一次（frame 跟着视口走）—— 转录区就是在 `set_frame`
       里调的（那里是唯一知道"字幕区现在多大"的地方）。
    ⚠️ 遮罩**铺满整层**（只把开头那段做成渐隐）—— 见 `_mask_stops`。遮罩的 frame
       跟着视图的 bounds 走（视图多大它多大）。
    """
    try:
        from AppKit import NSColor
        from Quartz import CAGradientLayer
        try:
            view.setWantsLayer_(True)
        except Exception:                                 # noqa: BLE001
            pass
        lay = view.layer()
        if lay is None:
            return False
        b = view.bounds().size
        m = CAGradientLayer.layer()
        m.setFrame_(((0.0, 0.0), (float(b.width), float(b.height))))
        locs, alphas = _mask_stops(float(b.height), edge, px)
        m.setColors_([NSColor.blackColor().colorWithAlphaComponent_(a).CGColor()
                      for a in alphas])
        m.setLocations_(locs)
        # ⚠️⚠️ **两个 edge 用同一组 start/end**（2026-10-18 审查 C1）：`location 0 = 视口
        #    顶`、`location 1 = 视口底`，**恒定的**。方向靠 `_mask_stops` 里的 alpha 曲线
        #    区分（顶边渐隐：顶透明；底边渐隐：底透明）。
        #    第一版两条 edge 各翻一次 —— 翻转叠翻转，`edge="bottom"` 的结果与 `"top"`
        #    **完全等价**（底边那条带子根本没出现）。`(0.5, 1.0) = 视觉顶边`，
        #    2026-10-17 拍图核过（`fade-check2.png`），别凭"y 朝上"推。
        #    ⭐ 3.10.1（Owen 10-19「中文底下那一块被遮住」）：上面那句只对**不翻转**的图层成立。
        #    转录区是 `NSScrollView` —— `isFlipped()=True`，图层 `geometryFlipped=True`，
        #    `(0.5, 1.0)` 落在**视觉底边**：顶边渐隐整条跑到了最新那行中文的下半截上。
        #    所以按图层自己的翻转取起止点（`start_end`），拍图核过 `fade-fixed.png`。
        start, end = start_end(bool(lay.isGeometryFlipped()))
        m.setStartPoint_(start)
        m.setEndPoint_(end)
        lay.setMask_(m)
        return True
    except Exception:                                     # noqa: BLE001
        return False


def installed(view) -> bool:
    """这个视图身上有没有我们装的遮罩（判据用；不是"遮罩一定生效"的证明）。"""
    try:
        return view.layer() is not None and view.layer().mask() is not None
    except Exception:                                     # noqa: BLE001
        return False
