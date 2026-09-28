#!/usr/bin/env python3
"""收尾卡的渲染探针（不在运行路径上，**改这张卡的观感前先跑它**）。

    ClassLive.app/Contents/MacOS/python probe_wrapup.py ask|work|done|wide

⚠️ **必须用 `AppHelper.runEventLoop()`**，不是 `runConsoleEventLoop` ——
   后者只跑 `NSRunLoop.runMode_beforeDate_`，从不排空 NSApp 事件队列，
   窗口根本画不出来（本仓库已经栽过，见 appkit-runloop-console-trap 那条记忆）。
"""
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

phase = (sys.argv[1] if len(sys.argv) > 1 else "ask").lower()

from AppKit import (NSApplication, NSApplicationActivationPolicyAccessory,  # noqa: E402
                    NSMakeRect, NSTimer, NSWindow, NSWindowStyleMaskTitled)
from PyObjCTools import AppHelper                                          # noqa: E402

import wrapup                                                              # noqa: E402

app = NSApplication.sharedApplication()
app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

if phase == "live":
    # ⭐ 用**真 Overlay** 跑一遍「问 → 进度（从工作线程推）→ 结论」。
    #    这是唯一能验证跨线程那一段的路：`wrapup_progress` 是在 worker 里被调的，
    #    UI 回写必须经 `AppHelper.callAfter` 回到主线程 —— 这条在离线判据里测不到。
    import threading
    import time

    from overlay import Overlay

    o = Overlay()
    o.show()
    print("live：overlay 已起")

    def worker():
        time.sleep(6.0)                     # 先让 ask 超时（4 秒）走完，卡留在屏上
        for i in (12, 24, 36):
            o.wrapup_progress("polish", i, 60)
            time.sleep(0.7)
        o.wrapup_done(True, "已存入 Obsidian → /tmp/wraptest/vault/Lectures/x.md")
        print("live：worker 推完", flush=True)

    threading.Thread(target=worker, daemon=True).start()
    # ⚠️ **ask_save 自己会 pump**，所以它那 4 秒里窗口是活的；之后由这里的循环接管。
    got = o.ask_save(579, timeout=4.0)
    print(f"live：ask 返回 {got!r}（None=关窗 / True=存）")

    # ⭐ **回读控件上的真实字符串** —— 这是唯一能验证「worker → callAfter → 卡」
    #    那条跨线程路真的通了的手法（截图会骗人：终端在最前时什么都看不见）。
    seen: list[str] = []
    end = time.monotonic() + 9.0
    while time.monotonic() < end:
        o.pump()
        time.sleep(0.02)
        card = getattr(o, "_wrapup_card", None)
        if card is not None:
            try:
                txt = card["_status_lbl"].stringValue()
            except Exception:                             # noqa: BLE001
                txt = "<读不回>"
            if not seen or seen[-1] != txt:
                seen.append(txt)

    print("live：卡片上先后出现过的文字——")
    for s in seen:
        print(f"   {s!r}")
    fail = []
    if got is not True:
        fail.append(f"ask 该超时返回 True，得到 {got!r}")
    if not any("12/60" in s for s in seen):
        fail.append("进度「精修 12/60」没上屏 —— 跨线程那条路没通")
    if not any("已存入" in s for s in seen):
        fail.append("结论「已存入 …」没上屏")
    print("live：" + ("全过 ✅" if not fail else "❌ " + " · ".join(fail)))
    sys.exit(1 if fail else 0)

# 一个假的主面板当锚点（真面板是横长条，这里照那个比例）
anchor = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
    NSMakeRect(400, 500, 900, 260), NSWindowStyleMaskTitled, 2, False)
anchor.setTitle_("(假主面板 · 只当锚点)")
anchor.orderFrontRegardless()

card = wrapup.build(on_close=lambda: print("card closed"))
if card is None:
    print("❌ 卡片构造失败")
    sys.exit(1)

if phase == "ask":
    card["set_status"]("本次共记录 579 句双语。\n存入 Obsidian 吗？")
    card["set_hint"]("60 秒后自动存入 · 关窗 = 放弃这份笔记")
    card["set_buttons"]([("存入", lambda: print("存入")),
                         ("不存", lambda: print("不存"))])
elif phase == "work":
    card["set_status"]("正在精修转录（二次矫正 + 重译）…\n精修 120/579")
    card["set_hint"]("按「跳过精修」直接用直播版转录出笔记")
    card["set_buttons"]([("跳过精修", lambda: print("跳过")),
                         ("中止并退出", lambda: print("退出"))])
elif phase == "done":
    card["set_status"]("✅ 已存入 Obsidian\n～/Obsidian/SecondBrain/Lectures/"
                       "2026-09-28_SOC10020.md")
    card["set_hint"]("3 秒后自动关闭")
    card["set_buttons"]([("立即关闭", lambda: print("关闭"))])
else:
    card["set_status"]("状态行如果太长，会**折行**到这个上限；超过就截断。"
                       "这一行故意写得很长很长很长很长很长很长很长很长很长很长。")
    card["set_hint"]("hint 行同理，但它只占一行")
    card["set_buttons"]([("按钮一", lambda: None), ("按钮二", lambda: None),
                         ("按钮三", lambda: None)])

from AppKit import NSScreen                                               # noqa: E402
import panel                                                              # noqa: E402
panel.place_beside(anchor, card["panel"])

print(f"phase={phase} · 卡片 {card['panel'].frame()} · 锚点 {anchor.frame()}")


def bye():
    print("（探针结束）")
    app.stop_(None)


NSTimer.scheduledTimerWithTimeInterval_repeats_block_(25.0, False, lambda t: bye())
AppHelper.runEventLoop()
