#!/usr/bin/env python3
"""生成 ClassLive 的 app 图标（`.icns`）。

    ClassLive.app/Contents/MacOS/python tools/make_icon.py

输出到 `assets/icon/`：`ClassLive.icns` + 10 档 PNG + 一张 1024 主图。

## 构图

深色圆角方块（Apple 的 squircle）+ **LiveMark**：一摞白色圆头字幕行，行宽沿椭圆收，
远看是一颗菠萝的轮廓；最后一行短一截，后面跟一个白点（正在打字的光标）。
2026-11-20 起换成宣传片（`~/classlive-video/launch/src/kit.jsx` 的 `LiveMark` / `AppIcon`）同一稿，
按 Owen 意见**去掉橙色**：点改成白。几何与片子逐数相同（200 单位框、`ICON_K` 0.92）。
菜单栏图标不跟着换（仍是菠萝，`overlay` / `menubar` 那套），只换 app 图标。

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
import shutil
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

# LiveMark（宣传片 kit.jsx 的同一组数，单位 = 200 的方框；y 向下，画时翻转）
MARK_K = 0.92                  # 标记占本体的比例（片子的 ICON_K）
MARK = dict(cx=100.0, cy=100.0, rx=60.0, ry=64.0, h=12.0, step=20.0, short=30.0)
# ⚠️ 16/32 px 用粗行少行：12 单位的行高在 16px 下不到 1px，必然糊（HIG：小尺寸别用细线）。
MARK_SMALL = dict(cx=100.0, cy=100.0, rx=60.0, ry=64.0, h=22.0, step=40.0, short=30.0)

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


def mark_rows(m=MARK):
    """纯函数：字幕行 `[(x, y, w)]`（200 框、y 向下）+ 光标点 `(x, y, r)`。与 kit.jsx 逐行同算法。"""
    def hw(y):
        t = (y - m["cy"]) / m["ry"]
        return 0.0 if abs(t) >= 1 else m["rx"] * math.sqrt(1 - t * t)
    n = int((2 * m["ry"] - m["h"]) // m["step"]) + 1
    y0 = m["cy"] - ((n - 1) * m["step"] + m["h"]) / 2
    rows = []
    for i in range(n):
        y = y0 + i * m["step"]
        w = 2 * min(hw(y), hw(y + m["h"])) - 4
        if w >= 40:
            rows.append([m["cx"] - w / 2, y, w])
    rows[-1][2] -= m["short"]
    x, y, w = rows[-1]
    dot = (x + w + 16 * m["h"] / 12, y + m["h"] / 2, m["h"] * 0.6)
    return [tuple(r) for r in rows], dot


def _mark(small=False):
    """LiveMark 的路径（画布单位）：圆头行 + 光标点，白色实心。"""
    from AppKit import NSBezierPath, NSMakeRect
    m = MARK_SMALL if small else MARK
    k = SIDE * MARK_K / 200.0
    ox = oy = CANVAS / 2 - 100.0 * k
    rows, (dx, dy, dr) = mark_rows(m)
    out = NSBezierPath.bezierPath()
    h = m["h"] * k
    for x, y, w in rows:
        # 翻转 y：SVG 向下、AppKit 向上
        out.appendBezierPath_(NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(ox + x * k, CANVAS - (oy + y * k) - h, w * k, h), h / 2, h / 2))
    out.appendBezierPath_(NSBezierPath.bezierPathWithOvalInRect_(
        NSMakeRect(ox + (dx - dr) * k, CANVAS - (oy + (dy + dr) * k), 2 * dr * k, 2 * dr * k)))
    return out


def draw(px: int, small: bool = False):
    """渲一张。`small` 是给 16/32 用的简化版（行更少更粗）。"""
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

    # 前景：LiveMark（白，没有橙色）
    NSColor.whiteColor().set()
    _mark(small).fill()

    NSGraphicsContext.restoreGraphicsState()
    return rep.representationUsingType_properties_(NSPNGFileType, {})


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    # `iconutil` 要的 10 档（5 尺寸 × @1x@2x）—— 缺一个它会直接报错
    SIZES = [(16, 1), (16, 2), (32, 1), (32, 2), (128, 1), (128, 2),
             (256, 1), (256, 2), (512, 1), (512, 2)]

    iconset = OUT / "ClassLive.iconset"
    # ⚠️ 与本文件其它分支同一风格（2026-09-28 审查指出）：原来用
    #    `subprocess.run(["rm", "-rf", ...], check=True)` —— 失败时抛
    #    `CalledProcessError`，那个异常**不带 `rm` 的 stderr**，
    #    以堆栈退出，定位信息比旁边 `writeToFile_atomically_` / `iconutil`
    #    那两条（都显式判断 + `return 1`）还差。
    #    仓库内删目录的既有约定是 `shutil.rmtree(..., ignore_errors=True)`。
    shutil.rmtree(iconset, ignore_errors=True)
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
