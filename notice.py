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
import sys

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
       「双击了、什么都没发生」—— 正是 `main.py:876` 那条注释要防的事；
       而终端里 `cl` 反而弹模态框。四处文档（`alert()` 的 docstring、本函数的 docstring、
       `main.py:876`、引入它的提交 `e0b3b91` 的正文）写的都是"没有终端时弹框"，
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
        idx = a.runModal() - 1000                          # NSAlertFirstButtonReturn = 1000

        # 设回原值永远是对的（值没变时就是无操作）—— 不用加条件
        app.setActivationPolicy_(_prev)
        if _url_btn is not None and idx == len(buttons):
            open_mic_settings()
            return "打开系统设置"
        return buttons[idx] if 0 <= idx < len(buttons) else (
            fallback if fallback is not None else buttons[0])
    except Exception:                                     # noqa: BLE001
        # 弹不出来也不能让程序静默 —— 至少留下文字
        print(f"\n⚠ {title}\n{message}", flush=True)
        return fallback if fallback is not None else buttons[0]


def open_mic_settings() -> bool:
    """跳到「系统设置 → 隐私与安全性 → 麦克风」。

    ⚠️ URL 里的 bundle id 是 `com.apple.settings.PrivacySecurity.extension`
       （macOS 13+ 的 ExtensionKit 版），**不是**旧的 `com.apple.preference.security`
       —— 两个在磁盘上都存在，但只有前者能在新版 System Settings 里定位到那一页。
       2026-09-26 实测有效（系统设置打开后右侧就是麦克风列表）。
    """
    try:
        subprocess.run(["open", SYSTEM_SETTINGS_MIC], check=False, timeout=10)
        return True
    except Exception:                                     # noqa: BLE001
        return False


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
