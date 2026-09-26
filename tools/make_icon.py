#!/usr/bin/env python3
"""生成 ClassLive 的 app 图标（`.icns`）。

    ClassLive.app/Contents/MacOS/python tools/make_icon.py

输出到 `assets/icon/`：`ClassLive.icns` + 10 档 PNG + 一张 1024 主图。

## 构图

深色圆角方块（Apple 的 squircle）+ 白色**实心**气泡 + 三行**镂空**字幕。
气泡尾巴在左下，由两段贝塞尔收成一个尖。

## ⚠️ 为什么是**自己画**，不是用 SF Symbols

Apple 明文禁止把 SF Symbols 用在 app 图标里 —— 两条原文（2026-09-26 核实）：

  HIG《SF Symbols》页：
    "Be sure to understand the terms and conditions for using SF Symbols, including
     **the prohibition against using symbols — or images that are confusingly similar —
     in app icons, logos**, or any other trademarked use."

  Xcode 授权协议 §2：
    "You agree that you shall **not use or incorporate the System-Provided Images or any
     substantially or confusingly similar images into app icons, logos** or make any other
     trademark use of the System-Provided Images."

**受限的是 Apple 那些具体图形，不是「气泡」「字幕」这些概念。** 自己画的路径不受任何限制。
所以字幕行刻意排成「两长一短、左缘对齐」—— 与 SF `captions.bubble` 的「五行分两排」不同，
避免落进 "confusingly similar"。

## ⚠️ 形状是 squircle（超椭圆），不是圆角矩形

`NSBezierPath.bezierPathWithRoundedRect_` 给的是**四分之一圆弧的圆角**，而 Apple 的是
**连续曲率的超椭圆**。社区实测原话：

    "Your shape has straight edges and quarter-circle corners (in other words, a rounded
     rectangle), but **the official shape is significantly different**"

所以方块本体用参数式超椭圆 `|x/a|^n + |y/b|^n = 1`，**n = 5**（n=2 是椭圆，n→∞ 是方形）。
几何按 Apple Design Resources 的 grid：**画布 1024×1024、本体 824×824 居中、四边各留 100**
（实测 Obsidian / Claude / Stats 三个图标的透明边距都是 25–26px / 256px，即 0.805，与此吻合）。

## ⚠️ 不加投影、不加内高光、不加光泽

HIG 原文：*"there's **no need to include** specular highlights, **drop shadows** between
layers, beveled edges, blurs, glows, and other effects. In addition to interfering with
system-provided effects, custom effects are static, whereas the system supplies dynamic
ones."* —— 深度只靠**背景那一道径向渐变**（见下），不加别的。

## ⚠️ 为什么背景是**径向**渐变而不是线性

线性竖直渐变 + 白色字形是 2013 年 iOS 7 的「工具类 app」公式，如今一眼廉价。
Apple 自己的图标是**从上方打光**，对应径向渐变的中心偏上。这里是 `(0, 0.62)`。

## ⚠️ 尾巴的 `BLEND` 参数不能改成 1.0

尾巴开口的两个端点里，有一个落在**底边**上，而底边的切线是**水平**的。
「接点沿超椭圆切线」这条约束如果给满（`blend=1`），尾巴两侧就被迫先水平伸出、
再急转直下 —— 128px 下缩成一根**针**，1024 下像**挂下来的一滴水**。

`BLEND = 0.5`：接点柔和并入本体，尖端保持折角。**真气泡本来就有折角**，
完全抹掉反而不像气泡。（2026-09-26 实测对比了 0 / 0.5 / 1 三档。）

## ⚠️ 已知问题：macOS 26 Tahoe 会把本图标 jail 成灰底

Tahoe 起所有 app 图标被强制塞进 squircle，不合规的进「监狱」（灰底 + 缩小内缩）。
触发条件是**单像素 alpha**：Apple 开发者论坛的对照实验（12 个测试 app）给出
`≥253` 干净 / `≤252` 进监狱。本图标四角是透明（alpha 0），所以**上 Tahoe 必被 jail**。

正统解法是改用 `.icon`（Liquid Glass 新格式），但那需要 Icon Composer / `actool`，
**两者都要 Xcode**，本机只有 Command Line Tools。**故暂缓，不是遗漏。**
（这不是我们独有的问题：BBEdit、Chrome、BetterSnapTool 一样中招。）
"""
from __future__ import annotations

import math
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent.parent
OUT = HERE / "assets" / "icon"

CANVAS = 1024.0
MARGIN = 100.0                 # → 本体 824×824
SIDE = CANVAS - 2 * MARGIN

SQUIRCLE_N = 5.0               # 方块本体：Apple 的 squircle 指数
SQUIRCLE_STEPS = 720

# 配色（ink）。避开 Dock 里已有的颜色 —— Rogue Amoeba《Free The Icons》(2026-06-26)：
# Tahoe 抹平了形状线索，"leaving **color as the primary way to tell icons apart**"。
# 实测作者 Dock：绿×2（Messages/FaceTime）蓝×2（App Store/Outlook）黄（Notes）白红（Calendar）。
BG_TOP = (0.150, 0.160, 0.190)
BG_BOT = (0.055, 0.060, 0.078)
BG_LIGHT_AT = (0.0, 0.62)      # 径向光源，偏上

# 气泡本体（画布单位）
BUB_W, BUB_H = 500.0, 420.0
BUB_N = 3.2                    # 比方块本体圆 —— 气泡要"圆润"，方块要"是 Apple 的 squircle"
BUB_TIP = (0.60, 1.50)         # 尾尖相对半轴的偏移
BUB_T_LO = 1.14 * math.pi      # 开口下沿（落在左侧）
BUB_T_HI = 1.45 * math.pi      # 开口上沿（落在底边）
BLEND = 0.5                    # ⚠️ 别改成 1.0 —— 见文件头

BAR_W_FRAC = 0.70              # 字幕行占本体宽度的比例
BAR_X_FRAC = 0.15
BAR_BLOCK_Y, BAR_BLOCK_H = 0.17, 0.66      # 行块在本体里的位置与高度
BAR_H_OVER_SPACING = 0.64      # ⚠️ 行高**由行距反推**，独立给会让行互相重叠（踩过）


def _unit(v):
    m = math.hypot(*v)
    return (v[0] / m, v[1] / m) if m else (0.0, 0.0)


def _squircle(rect, n=SQUIRCLE_N, steps=SQUIRCLE_STEPS):
    """超椭圆路径。`rect` 是 (x, y, w, h)。"""
    from AppKit import NSBezierPath
    x, y, w, h = rect
    a, b = w / 2.0, h / 2.0
    cx, cy = x + a, y + b
    p = NSBezierPath.bezierPath()
    for i in range(steps + 1):
        t = 2.0 * math.pi * i / steps
        ct, st = math.cos(t), math.sin(t)
        px = cx + a * math.copysign(abs(ct) ** (2.0 / n), ct)
        py = cy + b * math.copysign(abs(st) ** (2.0 / n), st)
        p.moveToPoint_((px, py)) if i == 0 else p.lineToPoint_((px, py))
    p.closePath()
    return p


def _caption_bubble(x, y, w, h):
    """**连续、不自交**的闭合轮廓：超椭圆身体 + 两段贝塞尔尾巴。

    ⚠️ 不能用「身体 + 尾巴各画一条子路径再一起描边」—— 尾巴会压在身体上，
       它的上边缘会在气泡**内部**留下一道线。正解是**在超椭圆上开一个口子**，
       把那一段换掉。

    ⚠️ 接点的控制点沿**超椭圆切线**取（数值求导）。完全拍脑袋会给轮廓留下折角 ——
       那正是「不精致」的来源。（但也不能全给，见 `BLEND` 的说明。）
    """
    from AppKit import NSBezierPath
    cx, cy, a, b = x + w / 2.0, y + h / 2.0, w / 2.0, h / 2.0

    def pt(t):
        ct, st = math.cos(t), math.sin(t)
        return (cx + a * math.copysign(abs(ct) ** (2.0 / BUB_N), ct),
                cy + b * math.copysign(abs(st) ** (2.0 / BUB_N), st))

    def tangent(t):
        e = 1e-5
        p1, p2 = pt(t - e), pt(t + e)
        return _unit((p2[0] - p1[0], p2[1] - p1[1]))

    def mix(d_tangent, d_chord):
        u = (d_tangent[0] * BLEND + d_chord[0] * (1 - BLEND),
             d_tangent[1] * BLEND + d_chord[1] * (1 - BLEND))
        return _unit(u)

    p = NSBezierPath.bezierPath()
    p_hi, p_lo = pt(BUB_T_HI), pt(BUB_T_LO)
    p.moveToPoint_(p_hi)
    for i in range(1, SQUIRCLE_STEPS + 1):     # 绕一圈，走出缺口
        p.lineToPoint_(pt(BUB_T_HI + (BUB_T_LO + 2 * math.pi - BUB_T_HI)
                          * i / SQUIRCLE_STEPS))

    tip = (cx - a * BUB_TIP[0], cy - b * BUB_TIP[1])
    d_lo = _unit((tip[0] - p_lo[0], tip[1] - p_lo[1]))
    d_hi = _unit((p_hi[0] - tip[0], p_hi[1] - tip[1]))
    L1 = math.hypot(tip[0] - p_lo[0], tip[1] - p_lo[1])
    L2 = math.hypot(p_hi[0] - tip[0], p_hi[1] - tip[1])
    a_lo = mix(tangent(BUB_T_LO), d_lo)
    a_hi = mix(tangent(BUB_T_HI), d_hi)
    p.curveToPoint_controlPoint1_controlPoint2_(
        tip, (p_lo[0] + a_lo[0] * L1 * 0.45, p_lo[1] + a_lo[1] * L1 * 0.45),
        (tip[0] - d_lo[0] * L1 * 0.30, tip[1] - d_lo[1] * L1 * 0.30))
    p.curveToPoint_controlPoint1_controlPoint2_(
        p_hi, (tip[0] + d_hi[0] * L2 * 0.30, tip[1] + d_hi[1] * L2 * 0.30),
        (p_hi[0] - a_hi[0] * L2 * 0.45, p_hi[1] - a_hi[1] * L2 * 0.45))
    p.closePath()
    return p


def _caption_bars(x, y, w, h, small=False):
    """气泡里的字幕行。**两长一短、左缘对齐**（刻意不同于 SF `captions.bubble`）。

    ⚠️ 这几行会被**镂空**（奇偶填充）—— 露出背景色，不是画在白色上。
    ⚠️ 小尺寸减到两行并加粗：三行 32 单位高在 16px 下只有 0.5px，必然糊。
       HIG：*"avoid extremely thin line weights… they tend to lose detail and
       crispness in smaller icon sizes"*。
    """
    from AppKit import NSBezierPath, NSMakeRect
    rows = ([(0.62, 0.00, 1.00), (0.34, 0.00, 0.72)] if small
            else [(0.68, 0.00, 1.00), (0.50, 0.00, 1.00), (0.32, 0.00, 0.72)])
    bh = h * BAR_H_OVER_SPACING * (rows[0][0] - rows[1][0])
    out = NSBezierPath.bezierPath()
    for fy, fx0, fx1 in rows:
        cy = y + h * fy
        out.appendBezierPath_(NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(x + w * fx0, cy - bh / 2, w * (fx1 - fx0), bh), bh / 2, bh / 2))
    return out


def draw(px: int, small: bool = False):
    """渲一张。`small` 是给 16/32 用的简化版（减少字幕行数、加粗行高）。"""
    from AppKit import (NSBitmapImageRep, NSColor, NSGradient, NSGraphicsContext,
                        NSMakeRect, NSPNGFileType)
    from Quartz import CGContextScaleCTM, CGContextClearRect

    rep = NSBitmapImageRep.alloc(
    ).initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, px, px, 8, 4, True, False, "NSCalibratedRGBColorSpace", 0, 0)
    ctx = NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.setCurrentContext_(ctx)
    # ⚠️ 下面的几何全是 **1024 画布单位** —— 必须按 px 缩放，
    #    否则小尺寸会画成一张被裁掉的大图（踩过）。
    CGContextScaleCTM(ctx.CGContext(), px / CANVAS, px / CANVAS)
    # ⚠️ 清屏必须用 `CGContextClearRect` + **CANVAS** 尺寸，不能写 `clearColor` + `(0,0,px,px)`：
    #    ① 上一行已把 CTM 缩放成 px/CANVAS，所以 `(0,0,px,px)` 落到设备像素只有
    #       px²/1024（16px 时约 **0.25 像素**），根本盖不住整张图；
    #    ② `clearColor` 的 alpha 是 0，在默认的 source-over 合成下**等于没画**。
    #    两条叠加 = 清屏完全没生效，四角透明全靠位图缓冲区碰巧被清零（实现细节，不该依赖）。
    #    （2026-09-26 OCR 审查发现；实测当时四角确实是 0，所以没暴露出来。）
    CGContextClearRect(ctx.CGContext(), NSMakeRect(0, 0, CANVAS, CANVAS))

    # 底：超椭圆 + **径向**渐变（光源偏上）
    NSGradient.alloc().initWithStartingColor_endingColor_(
        NSColor.colorWithCalibratedRed_green_blue_alpha_(*BG_TOP, 1.0),
        NSColor.colorWithCalibratedRed_green_blue_alpha_(*BG_BOT, 1.0)
    ).drawInBezierPath_relativeCenterPosition_(
        _squircle((MARGIN, MARGIN, SIDE, SIDE)), BG_LIGHT_AT)

    # 前景：白色实心气泡，字幕行镂空
    x = CANVAS / 2 - BUB_W / 2
    y = CANVAS / 2 - 0.44 * BUB_H
    body = _caption_bubble(x, y, BUB_W, BUB_H)
    body.appendBezierPath_(_caption_bars(
        x + BUB_W * BAR_X_FRAC, y + BUB_H * BAR_BLOCK_Y,
        BUB_W * BAR_W_FRAC, BUB_H * BAR_BLOCK_H, small))
    # ⚠️ 0 = NSWindingRuleNonZero，1 = NSWindingRuleEvenOdd。
    #    传 0 时镂空**不生效**，整个气泡填成一块白（踩过）。
    body.setWindingRule_(1)
    NSColor.whiteColor().set()
    body.fill()

    NSGraphicsContext.restoreGraphicsState()
    return rep.representationUsingType_properties_(NSPNGFileType, {})


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    # `iconutil` 要的 10 档（5 尺寸 × @1x@2x）—— 缺一个它会直接报错
    SIZES = [(16, 1), (16, 2), (32, 1), (32, 2), (128, 1), (128, 2),
             (256, 1), (256, 2), (512, 1), (512, 2)]

    iconset = OUT / "ClassLive.iconset"
    subprocess.run(["rm", "-rf", str(iconset)], check=True)
    iconset.mkdir(parents=True)
    for base, scale in SIZES:
        px = base * scale
        name = f"icon_{base}x{base}{'@2x' if scale == 2 else ''}.png"
        # ⚠️ 每一档**单独渲**，不是从 1024 那张缩 —— 因为 16/32 要用**简化版**
        # ⚠️ `writeToFile_atomically_` 返回 BOOL、**失败不抛异常** —— 不检查的话
        #    磁盘满/权限不足只会让 PNG 静默缺失，然后在 iconutil 那里报一个
        #    定位不到的错，把真正的失败点盖掉。
        if not draw(px, small=(px <= 32)).writeToFile_atomically_(str(iconset / name), True):
            print(f"❌ 写不出 {name}（磁盘满？权限？）", file=sys.stderr)
            return 1
    # 留一张 1024 主图，给 Git / 文档 / 以后重制用
    if not draw(1024).writeToFile_atomically_(str(OUT / "ClassLive-1024.png"), True):
        print("❌ 写不出 ClassLive-1024.png", file=sys.stderr)
        return 1

    icns = OUT / "ClassLive.icns"
    r = subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(icns)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(f"❌ iconutil 失败：{r.stderr.strip()}", file=sys.stderr)
        return 1
    print(f"  ✅ {icns.name}  ({icns.stat().st_size / 1024:.0f} KB)")
    print(f"     装进 .app 由 make-app.sh 负责（Contents/Resources/AppIcon.icns）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
