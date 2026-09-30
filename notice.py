#!/usr/bin/env python3
"""面向用户的提示 —— 说人话的那一层。

这个模块只干三件事：

1. `alert(...)`   —— 出事时弹一个**看得见**的框（`.app` 双击启动时 stdout 是没人看的）
2. `mic_permission()` —— 查麦克风授权状态（未决定 / 已拒 / 已授权）
3. `open_mic_settings()` —— 一键跳到系统设置的麦克风页

## ⚠️ 为什么 `.app` 里必须弹框

双击启动的 `.app` **没有终端** —— `print` 出去的东西进虚空。
ClassLive 的启动失败路径原本都是「友好提示 + return」（写得没错），
但在 `.app` 下用户看到的是**双击了、什么都没发生**。

## ⚠️ 为什么这里用 NSAlert，而更新卡片当年**主动弃用**了它

`whatsnew.py` 的文档里记着弃用理由（HIG 三条原话）：*"use alerts sparingly"* /
*"Avoid using an alert **merely to provide information**"* / *"Don't alert merely to
convey information, **on load**"*。**那三条在这里都不成立：**

| | 更新卡片（弃用 NSAlert 的场景） | **这里（启动失败 / 权限被拒）** |
|---|---|---|
| 是纯信息吗？ | ✅ 是 | ❌ **不是 —— 用户必须采取行动** |
| 不打断会怎样？ | ✅ 照样能上课（fail-soft） | ❌ **根本没上课**（fail-stop） |

关键词是 "**merely** to provide information" —— 启动失败不满足 "merely"。
而且它**罕见**，正是 "use alerts sparingly" 该用的地方。

## ⚠️ 两个当年实测出来的坑，这里都避开了

1. **NSAlert 默认用 `NSApplicationIcon`** —— 未打包的脚本会回落到**宿主解释器的图标**。
   方案 I 的 `.app` 里 `mainBundle` 是自己，所以不会弹出一个 Python 火箭。
2. **弹框前 activation policy 还是 `Regular`**（实测）→ 会在 Dock 里冒出 Python 图标。
   **所以下面先 `setActivationPolicy_(Accessory)` 再弹。**
"""
from __future__ import annotations

import os
import subprocess

# ⚠️ bundle id 与 `make-app.sh` 里写死的那个必须一致
SYSTEM_SETTINGS_MIC = (
    "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension"
    "?Privacy_Microphone"
)

_PERM_NAMES = {0: "notDetermined", 1: "restricted", 2: "denied", 3: "authorized"}


def _can_alert() -> bool:
    """要不要走"弹框"而不是"打印"。**`True` = 弹框。**

    ⚠️⚠️ **判据是 `CLASSLIVE_FROM_APP`，不是 `sys.stdout.isatty()`** ——
       而且**方向是"设了才弹"**（2026-09-28 修正，见下）。

    `CLASSLIVE_FROM_APP=1` 由 `ClassLive.app` 里的 sitecustomize 设，而它设的**同时**
       把 stdout/stderr 改道去了 `~/Library/Logs/ClassLive/app.log`。
       所以这个变量的准确含义不是"从 app 来的"，而是
       **「我是个没人看的进程，print 出去没人看得见」** → 这种时候**才必须弹框**。

    ⚠️ 为什么不能用 `isatty`：`cl | tee log`、被别的程序捕获输出、CI 里跑 ——
       这些情况 stdout 都不是 tty。拿 isatty 当判据会去弹一个**模态**框，
       在没有图形会话的地方**永久挂住**（2026-09-26 实测踩到，管道里跑 `alert` 直接卡死）。
       而 `FROM_APP` 没被设 → 走 print，永远不会挂在弹框上。
       ⭐ 换句话说：这一条要成立，**必须**是"设了才弹"。

    ⚠️ **2026-09-28 之前这里是 `return not os.environ.get(...)`，方向是反的。**
       实测后果：双击启动（没终端、print 进日志）时**什么都不弹**，用户看到的是
       「双击了、什么都没发生」—— 正是 `main.py` 里 `run()` 那处
       `notice.alert`（音源打不开那条路）的注释要防的事；
       而终端里 `cl` 反而弹模态框。四处文档（`alert()` 的 docstring、本函数的 docstring、
       `main.py` 那条注释、引入它的提交 `e0b3b91` 的正文）写的都是"没有终端时弹框"，
       只有代码是反的，而且**没有任何判据钉过它**，所以从 09-26 落地起一直没人发现。
    """
    return bool(os.environ.get("CLASSLIVE_FROM_APP"))


def alert(title: str, message: str, buttons: tuple[str, ...] = ("知道了",),
          url: str | None = None, fallback: str | None = None) -> str:
    """弹一个模态提示框；**没有终端**时才弹。

    `url` 给了的话，会在按钮**之外**多一个「打开系统设置」，
    点了就 `open` 那个 URL scheme（用来引导用户去开被拒的权限）。

    返回被点按钮的标题。没有终端 / 弹不出来 / 点了个奇怪的返回值时返回 `fallback`。

    ⚠️⚠️ **`fallback` 默认是 `buttons[0]` —— 也就是说 `buttons[0]` 是"问不到人时的答案"。**
       所以**它必须是安全的那一个**：
       · 纯告知（`("知道了",)`）无所谓，只有一个按钮；
       · ⚠️ **确认框必须显式给 `fallback`** —— 不给就等于「问不到人 = 自动批准」。
         踩过：更新流程那个 `buttons=("现在做", "先不做")` 没给 fallback，
         弹不出来时直接返回「现在做」→ **不问就替用户批准了要联网跑几分钟的重活**。
    """
    if not _can_alert():
        # 终端里就跑 —— 打印比弹框好（能复制、能滚回去看、不打断脚本）
        print(f"\n{'─' * 46}\n⚠ {title}\n{message}\n{'─' * 46}", flush=True)
        if url:
            print(f"   → 去这里打开：{url}")
        return fallback if fallback is not None else buttons[0]

    try:
        from AppKit import NSAlert, NSApplication, NSApplicationActivationPolicyAccessory
        app = NSApplication.sharedApplication()
        # ⚠️ 必须**先**设成 Accessory 再弹 —— 否则 Dock 里冒出一个 Python 图标
        #    （当年 NSAlert 被弃用的两个实测根因之一，见模块 docstring）
        _prev = app.activationPolicy()
        app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

        try:
            a = NSAlert.alloc().init()
            a.setMessageText_(title)
            a.setInformativeText_(message)
            for b in buttons:
                a.addButtonWithTitle_(b)
            _url_btn = None
            if url:
                _url_btn = a.addButtonWithTitle_("打开系统设置")

            a.window().center()
            a.window().orderFrontRegardless()
            idx = a.runModal() - 1000                      # NSAlertFirstButtonReturn = 1000

            if _url_btn is not None and idx == len(buttons):
                # ⚠️ 开的是**调用方给的那个 URL**，不是写死的麦克风页 ——
                #    原来这里无脑调 `open_mic_settings()`，于是「传别的 URL 也会
                #    跳到麦克风页」，与 docstring / 上面那条 print 分支都对不上
                #    （2026-09-28 审查指出）。
                open_url(url)
                return "打开系统设置"
            return buttons[idx] if 0 <= idx < len(buttons) else (
                fallback if fallback is not None else buttons[0])
        finally:
            # ⚠️ **必须在 `finally` 里设回**：放在 try 里的话，`runModal()` 一抛异常
            #    就被下面那个 `except` 接走，policy 残留成 Accessory
            #    —— 之后这个进程在 Dock / 窗口激活上的行为就一直是错的。
            #    「设回原值永远是对的（值没变时就是无操作）」—— 不用加条件。
            app.setActivationPolicy_(_prev)
    except Exception:                                     # noqa: BLE001
        # 弹不出来也不能让程序静默 —— 至少留下文字
        print(f"\n⚠ {title}\n{message}", flush=True)
        return fallback if fallback is not None else buttons[0]


def ask_text(title: str, message: str, fields: list, *,
             buttons: tuple[str, ...] = ("保存", "以后再说"),
             fallback=None) -> dict | None:
    """弹一个**带输入框**的模态框 → `{key: 值}`；弹不出来 / 点了取消 → `fallback`。

    `fields` = `[{"key": …, "label": …, "hint": …}, …]`（输入框一律 `NSSecureTextField`，
    **不回显明文**）。

    ⚠️⚠️ **`buttons[0]` 是最右边那颗 —— 也就是视觉上的主按钮。**
       `NSAlert` 把**先加的排在右边**（2026-09-30 截图核对过），所以默认顺序是
       `("保存", "以后再说")`：主操作在右，符合 macOS 惯例。
       ⚠️ 第一版传的是 `("以后再说", "保存")` → **「以后再说」跑到了最右**，
          看起来像主操作。**顺序不是小事**，它决定用户第一眼按哪个。
    ⚠️⚠️ 也正因为这样，**`buttons[0]` 不再天然是"安全的那一个"** ——
       调用方**必须显式给 `fallback`**（同 `alert()` 那条：不给就等于
       「问不到人 = 自动保存」）。
    ⚠️ 弹不出来时**返回 `fallback` 并把用法打出来** —— 没有 UI 的人也得有办法填
       （`cl setkey`，见 `keyentry.py` 的 `__main__`）。
    ⚠️ 这个函数**不写任何文件** —— 校验和落盘都在 `keyentry`，这里只负责问。
    """
    if not _can_alert():
        print(f"\n{'─' * 46}\n⚠ {title}\n{message}\n{'─' * 46}", flush=True)
        for f in fields:
            print(f"   {f['label']}: {f.get('hint', '')}", flush=True)
        print("   → 没有图形界面时用：`cl setkey <deepseek-key> [jev-key]`", flush=True)
        return fallback

    try:
        from AppKit import (NSApplication, NSApplicationActivationPolicyAccessory)
        app = NSApplication.sharedApplication()
        _prev = app.activationPolicy()
        app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        try:
            a, boxes = build_text_alert(title, message, fields, buttons)
            a.window().center()
            a.window().orderFrontRegardless()
            idx = a.runModal() - 1000                      # NSAlertFirstButtonReturn = 1000
            # ⚠️ `idx` 是**添加顺序**的下标（`buttons[0]` = 最右那颗 = 主按钮），
            #    不是"从左数第几个"。所以「保存」就是 idx == 0。
            if idx != 0:
                return fallback
            return {k: tf.stringValue().strip() for k, tf in boxes.items()}
        finally:
            # ⚠️ 同 `alert()`：**必须在 `finally` 里设回** —— 否则 policy 残留成
            #    Accessory，之后这个进程在 Dock / 窗口激活上的行为一直是错的。
            app.setActivationPolicy_(_prev)
    except Exception:                                         # noqa: BLE001
        print(f"\n⚠ {title}\n{message}", flush=True)
        print("   → 没有图形界面时用：`cl setkey <deepseek-key> [jev-key]`", flush=True)
        return fallback


#: 那张框的几何 —— **唯一定义点**（探针按它断言，见 `tests/test_keyentry.py`）。
TEXT_ALERT_W = 430.0
#: 一栏占多高 = 标签/输入框那一行(22) + 间距(6) + 说明两行(34) + 栏间距(10)
TEXT_ALERT_ROW = 72.0
TEXT_ALERT_PAD = 12.0
#: 标签 + 输入框**同一行**：标签这么宽，剩下的给输入框。
TEXT_ALERT_LABEL_W = 150.0


def text_alert_height(n_fields: int) -> float:
    """带 `n_fields` 个输入框时，附件视图要多高。**纯函数。**"""
    return TEXT_ALERT_PAD * 2.0 + TEXT_ALERT_ROW * max(0, int(n_fields))


def build_text_alert(title: str, message: str, fields: list, buttons: tuple):
    """建那张带输入框的 `NSAlert`（**不弹**）→ `(alert, {key: NSTextField})`。

    ⚠️ 抽出来是为了**能在不起模态框的情况下看它的样子**（`probe_keyentry.py`）
       —— 也顺带让几何变成可断言的纯函数（`text_alert_height`）。
    ⚠️ 输入框一律 `NSSecureTextField`（**不回显明文**）。
    ⚠️⚠️ **排布照作者拍过的那张稿**：`[标签] [输入框]` 同一行，说明在**下面**小字。
       第一版把三者**竖着堆**，而且算错了 y —— 说明和输入框**重叠了 12pt**
       （2026-09-30 探针截图 + 几何诊断抓到的）。
    """
    from AppKit import (NSAlert, NSView, NSTextField, NSSecureTextField, NSFont, NSColor,
                        NSLineBreakByWordWrapping)
    W, ROW, PAD = TEXT_ALERT_W, TEXT_ALERT_ROW, TEXT_ALERT_PAD
    LW = TEXT_ALERT_LABEL_W
    h = text_alert_height(len(fields))
    box = NSView.alloc().initWithFrame_(((0.0, 0.0), (W, h)))
    boxes: dict = {}
    y = h - PAD
    for f in fields:
        # ① 标签 + 输入框：**同一行**（标签左、输入框右）
        lbl = NSTextField.alloc().initWithFrame_(((0.0, y - 20.0), (LW, 20.0)))
        lbl.setStringValue_(f["label"])
        lbl.setBezeled_(False)
        lbl.setEditable_(False)
        lbl.setDrawsBackground_(False)
        lbl.setSelectable_(False)
        lbl.setFont_(NSFont.systemFontOfSize_(12.0))
        box.addSubview_(lbl)

        tf = (NSSecureTextField if f.get("secure", True)
              else NSTextField).alloc().initWithFrame_(((LW + 8.0, y - 22.0), (W - LW - 8.0, 22.0)))
        tf.setFont_(NSFont.systemFontOfSize_(12.0))
        box.addSubview_(tf)
        boxes[f["key"]] = tf
        y -= 22.0 + 6.0

        # ② 说明：**在下面**，两行、自动折行
        if f.get("hint"):
            ht = NSTextField.alloc().initWithFrame_(((0.0, y - 34.0), (W, 34.0)))
            ht.setStringValue_(f["hint"])
            ht.setBezeled_(False)
            ht.setEditable_(False)
            ht.setDrawsBackground_(False)
            ht.setSelectable_(False)
            ht.setFont_(NSFont.systemFontOfSize_(10.0))
            ht.setTextColor_(NSColor.secondaryLabelColor())
            # ⚠️ 不设这两条的话长说明会被**静默截断**（AppKit 默认单行）
            ht.setLineBreakMode_(NSLineBreakByWordWrapping)
            try:
                ht.setMaximumNumberOfLines_(2)
            except Exception:                                 # noqa: BLE001
                pass
            box.addSubview_(ht)
        y -= 34.0 + 10.0

    a = NSAlert.alloc().init()
    a.setMessageText_(title)
    a.setInformativeText_(message)
    a.setAccessoryView_(box)
    for b in buttons:
        a.addButtonWithTitle_(b)
    # ⚠️⚠️ **`layout()` 必须显式调**（2026-09-30 实测）：
    #    不调的话窗口只有 **260 宽**、**附件视图根本不进视图树**、按钮被挤成**竖排**，
    #    而 `accessoryView()` 照样返回那个对象 —— 看起来"设过了"。
    #    `runModal()` 内部会替我们调，所以生产路径上看不出来；
    #    但**探针（`probe_keyentry.py`）不 runModal**，不调就渲染成一张完全不同的框。
    #    ⚠️ 这是 `build_text_alert` 抽出来之后才暴露的 —— 抽之前没人单独建过它。
    a.layout()
    return a, boxes


def open_url(url: str) -> bool:
    """用 `open` 打开一个 URL。返回**系统有没有接受**。

    ⚠️ `check=False` 之下 `subprocess.run` 几乎不抛，所以原来那个「恒返回 True」
       等于没有返回值的意义（2026-09-28 审查指出）—— 现在看返回码。
    """
    try:
        done = subprocess.run(["open", url], check=False, timeout=10)
        return done.returncode == 0
    except Exception:                                     # noqa: BLE001
        return False


def open_mic_settings() -> bool:
    """跳到「系统设置 → 隐私与安全性 → 麦克风」。

    ⚠️ URL 里的 bundle id 是 `com.apple.settings.PrivacySecurity.extension`
       （macOS 13+ 的 ExtensionKit 版），**不是**旧的 `com.apple.preference.security`
       —— 两个在磁盘上都存在，但只有前者能在新版 System Settings 里定位到那一页。
       2026-09-26 实测有效（系统设置打开后右侧就是麦克风列表）。
    """
    return open_url(SYSTEM_SETTINGS_MIC)


def mic_permission() -> str:
    """查麦克风授权状态：`notDetermined` / `restricted` / `denied` / `authorized` / `unknown`。

    ## ⚠️ 为什么值得为它单独引一个依赖（实测 +3 MB）

    Apple 原文：*"If a user has denied your app recording permission, or hasn't yet
    responded to the permission prompt, **audio recordings contain only silence**."*

    **不是报错、不是崩溃 —— 是照常有数据、但全是静音。**
    对 ClassLive 意味着：不做这个判断的话，被拒之后程序**看起来一切正常**、
    字幕一直空着、用户完全不知道为什么。这类失败最难查。

    ⚠️ `unknown` 表示查不了（AVFoundation 没装 / 非 macOS）——
       调用方**必须按老行为继续**，不能因为查不到就把人拦在门外。
    """
    try:
        import AVFoundation as A
        s = A.AVCaptureDevice.authorizationStatusForMediaType_(A.AVMediaTypeAudio)
        return _PERM_NAMES.get(int(s), "unknown")
    except Exception:                                     # noqa: BLE001
        return "unknown"


def needs_mic(source: str) -> bool:
    """这个音源要不要麦克风权限。

    ⚠️ `cl file` 放录音**不碰麦克风**，不该被权限拦住 ——
       而且 `cl file` 正是课上出问题时用来复现的手段，拦住它最要不得。
    """
    return source in ("mic", "blackhole")
