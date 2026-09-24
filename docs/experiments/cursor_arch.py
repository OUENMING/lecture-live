"""架构对比: 无边框 vs Titled+隐藏标题栏, 八个位置的缩放光标。干净桌面。"""
import hashlib, subprocess, sys, threading, time
import Quartz
from AppKit import (NSApplication, NSPanel, NSMakeRect, NSColor, NSScreen, NSCursor,
                    NSWindowStyleMaskBorderless, NSWindowStyleMaskResizable,
                    NSWindowStyleMaskNonactivatingPanel, NSWindowStyleMaskTitled,
                    NSWindowStyleMaskClosable, NSWindowStyleMaskFullSizeContentView,
                    NSBackingStoreBuffered, NSApplicationActivationPolicyAccessory)

STYLE = sys.argv[1]
APP = NSApplication.sharedApplication()
H = NSScreen.mainScreen().frame().size.height
HOLD = {}
KNOWN = ("arrowCursor","resizeLeftRightCursor","resizeUpDownCursor","resizeLeftCursor",
         "resizeRightCursor","openHandCursor","closedHandCursor","pointingHandCursor",
         "IBeamCursor","crosshairCursor","operationNotAllowedCursor")


def cfp():
    try: return hashlib.md5(bytes(NSCursor.currentSystemCursor().image().TIFFRepresentation())).hexdigest()[:8]
    except Exception: return "err"
def kf():
    o = {}
    for nm in KNOWN:
        fn = getattr(NSCursor, nm, None)
        if fn:
            try: o[hashlib.md5(bytes(fn().image().TIFFRepresentation())).hexdigest()[:8]] = nm
            except Exception: pass
    return o
def post(k, p): Quartz.CGEventPost(Quartz.kCGHIDEventTap, Quartz.CGEventCreateMouseEvent(None, k, p, 0))
def to_cg(x, y): return (float(x), float(H - float(y)))


def driver():
    time.sleep(1.8)
    p = HOLD["p"]
    APP.activateIgnoringOtherApps_(True); time.sleep(0.9)
    f = p.frame(); X, Y, W, Hh = f.origin.x, f.origin.y, f.size.width, f.size.height
    K = kf()
    print(f"[{STYLE}] {W:.0f}x{Hh:.0f} active={APP.isActive()}", flush=True)
    for nm, (fx, fy) in [("左", (0.003, .5)), ("右", (0.997, .5)),
                         ("上", (.5, 0.997)), ("下", (.5, 0.003)),
                         ("左上", (0.003, 0.997)), ("右上", (0.997, 0.997)),
                         ("左下", (0.003, 0.003)), ("右下", (0.997, 0.003)),
                         ("正中", (.5, .5))]:
        ax = X + W * fx; ay = Y + Hh * fy
        post(Quartz.kCGEventMouseMoved, to_cg(ax, ay)); time.sleep(0.35)
        print(f"    cursor@{nm:<3} = {K.get(cfp(), '★缩放光标')}", flush=True)
    # 标题栏按钮能否隐藏
    if STYLE == "titled":
        b = p.standardWindowButton_(0)
        print(f"    红绿灯按钮存在? {b is not None}", flush=True)
        if b is not None:
            for i in (0, 1, 2):
                bb = p.standardWindowButton_(i)
                if bb is not None:
                    bb.setHidden_(True)
            print(f"    隐藏后 按钮0.isHidden={p.standardWindowButton_(0).isHidden()}", flush=True)
    HOLD["done"] = True
    APP.performSelectorOnMainThread_withObject_waitUntilDone_("terminate:", None, False)


def main():
    APP.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    scr = NSScreen.mainScreen().visibleFrame()
    if STYLE == "titled":
        st = (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
              | NSWindowStyleMaskResizable | NSWindowStyleMaskFullSizeContentView)
    else:
        st = (NSWindowStyleMaskBorderless | NSWindowStyleMaskResizable
              | NSWindowStyleMaskNonactivatingPanel)
    p = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(scr.origin.x+200, scr.origin.y+300, 420.0, 320.0), st,
        NSBackingStoreBuffered, False)
    p.setFloatingPanel_(True); p.setLevel_(3); p.setContentMinSize_((200.0, 150.0))
    if STYLE == "titled":
        p.setTitleVisibility_(1); p.setTitlebarAppearsTransparent_(True)
    cv = p.contentView(); cv.setWantsLayer_(True)
    cv.layer().setBackgroundColor_(NSColor.blackColor().CGColor())
    p.orderFrontRegardless()
    HOLD["p"] = p
    threading.Thread(target=driver, daemon=True).start()
    APP.run()


main()
