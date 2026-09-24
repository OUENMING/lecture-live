"""ClassLive 缩放方案 A/B 探针 —— 只读实验, 不改仓库任何文件。

目的: 决定性的未知只有一条 ——
  「Borderless | Resizable」在**非激活面板 + app 处于后台**时, 到底能不能拖、有没有光标?

两块面板并排:
  左 = A 路: Borderless | Resizable | NonactivatingPanel   (保持 ClassLive 现外观)
  右 = B 路: Titled | Resizable | FullSizeContentView + TitleHidden  (Henry-Jessie 配方)

测试步骤:
  1. 脚本起后, 点一下浏览器/别的 app, 让**探针 app 进入后台**(这是关键前提)
  2. 把鼠标移到每块面板的**边缘/右下角**, 看光标变不变
  3. 试着拖边缘, 看窗口变不变大
  4. 两边各试一次, 记下差异

自动 180 秒后退出; 也可以点面板上的「退出探针」。
"""
import time
from AppKit import (
    NSApplication, NSApplicationActivationPolicyAccessory, NSApp,
    NSPanel, NSMakeRect, NSColor, NSTextField, NSScreen,
    NSWindowStyleMaskBorderless, NSWindowStyleMaskResizable,
    NSWindowStyleMaskNonactivatingPanel, NSWindowStyleMaskTitled,
    NSWindowStyleMaskClosable, NSWindowStyleMaskFullSizeContentView,
    NSBackingStoreBuffered, NSWindowTitleHidden, NSFont,
    NSFloatingWindowLevel, NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary, NSAppearance,
    NSAppearanceNameDarkAqua, NSTextAlignmentCenter,
)

_panels = []


def _make_panel_cls():
    class _Panel(NSPanel):
        def canBecomeKeyWindow(self):        # noqa: N802
            return True
    return _Panel


Panel = _make_panel_cls()


def build(label, style, rect, hide_title=False, show_traffic=True):
    p = Panel.alloc().initWithContentRect_styleMask_backing_defer_(
        rect, style, NSBackingStoreBuffered, False)
    p.setLevel_(NSFloatingWindowLevel)
    p.setCollectionBehavior_(
        NSWindowCollectionBehaviorCanJoinAllSpaces
        | NSWindowCollectionBehaviorFullScreenAuxiliary)
    p.setOpaque_(False)
    p.setBackgroundColor_(NSColor.clearColor())
    p.setMovableByWindowBackground_(True)
    p.setContentMinSize_((300, 120))
    if hide_title:
        p.setTitleVisibility_(NSWindowTitleHidden)
        p.setTitlebarAppearsTransparent_(True)
    p.setAppearance_(NSAppearance.appearanceNamed_(NSAppearanceNameDarkAqua))

    cv = p.contentView()
    cv.setWantsLayer_(True)
    cv.layer().setBackgroundColor_(
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.12, 0.13, 0.16, 0.94).CGColor())
    cv.layer().setCornerRadius_(14.0)
    cv.layer().setMasksToBounds_(True)

    tf = NSTextField.alloc().initWithFrame_(
        NSMakeRect(14, 14, rect.size.width - 28, rect.size.height - 28))
    tf.setStringValue_(label)
    tf.setEditable_(False)
    tf.setBordered_(False)
    tf.setDrawsBackground_(False)
    tf.setSelectable_(False)
    tf.setTextColor_(NSColor.whiteColor())
    tf.setFont_(NSFont.systemFontOfSize_weight_(13.0, 0.23))
    tf.setAlignment_(NSTextAlignmentCenter)
    tf.setLineBreakMode_(0)
    tf.setMaximumNumberOfLines_(0)
    cv.addSubview_(tf)
    return p


def report(p, name):
    print(f"[{name}] styleMask={p.styleMask()} "
          f"isResizable={bool(p.styleMask() & NSWindowStyleMaskResizable)} "
          f"isKeyWindow={p.isKeyWindow()} "
          f"contentMinSize={p.contentMinSize()}")


def main():
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    scr = NSScreen.mainScreen().visibleFrame()
    w, h = 380.0, 200.0
    y = scr.origin.y + scr.size.height - h - 120

    a = build(
        "【A 路】Borderless | Resizable\n\n保持 ClassLive 现外观。\n"
        "拖这条边 / 右下角试试。\n\n光标有变化吗? 窗口能变大吗?",
        NSWindowStyleMaskBorderless | NSWindowStyleMaskResizable
        | NSWindowStyleMaskNonactivatingPanel,
        NSMakeRect(scr.origin.x + 60, y, w, h))

    b = build(
        "【B 路】Titled | Resizable | FullSizeContentView\n+ TitleHidden\n\n"
        "Henry-Jessie 配方。\n拖这条边 / 右下角试试。\n\n"
        "光标有变化吗? 左上角有红黄绿按钮吗?",
        NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
        | NSWindowStyleMaskResizable | NSWindowStyleMaskFullSizeContentView,
        NSMakeRect(scr.origin.x + 60 + w + 40, y, w, h),
        hide_title=True)

    for p in (a, b):
        p.orderFrontRegardless()
        _panels.append(p)

    report(a, "A borderless+resizable")
    report(b, "B titled+hidden+resizable")
    for nm, p in (("A", a), ("B", b)):
        btn = p.standardWindowButton_(0)     # 0 = close button
        print(f"[{nm}] standardWindowButton(0) = {btn}")

    print("\n>>> 现在点别的 app 让探针进入后台, 然后去拖两块面板的边缘。")
    print(">>> 180 秒后自动退出, 或 Ctrl+C。")

    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        ev = app.nextEventMatchingMask_untilDate_inMode_dequeue_(
            0xFFFFFFFF, None, "NSDefaultRunLoopMode", True)
        if ev is not None:
            app.sendEvent_(ev)
        if not any(p.isVisible() for p in _panels):
            break
        app.updateWindows()


if __name__ == "__main__":
    main()
