import sys
"""八向拖拽地图 —— 激活/重置全在主线程。"""
import subprocess, sys, threading, time
import Quartz
import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[2]))
from AppKit import NSScreen, NSApplication, NSMakeRect
from overlay import Overlay

H = NSScreen.mainScreen().frame().size.height
APP = NSApplication.sharedApplication()
S = {"go": True, "out": [], "reset": False}
X0, Y0, W0, H0 = 300.0, 420.0, 660.0, 362.0


def post(k, p): Quartz.CGEventPost(Quartz.kCGHIDEventTap, Quartz.CGEventCreateMouseEvent(None, k, p, 0))
def to_cg(x, y): return (float(x), float(H - float(y)))


def drive(o):
    time.sleep(2.0)
    zones = [("左", 0.004, .5, -70, 0), ("右", 0.996, .5, 70, 0),
             ("上", .5, 0.996, 0, 70), ("下", .5, 0.004, 0, -70),
             ("左上", 0.004, 0.996, -70, 70), ("右上", 0.996, 0.996, 70, 70),
             ("左下", 0.004, 0.004, -70, -70), ("右下", 0.996, 0.004, 70, -70)]
    for label, fx, fy, dx, dy in zones:
        S["reset"] = True
        while S["reset"]:
            time.sleep(0.03)
        time.sleep(0.12)
        f0 = o._panel.frame()
        ax = f0.origin.x + f0.size.width * fx
        ay = f0.origin.y + f0.size.height * fy
        c = to_cg(ax, ay)
        post(Quartz.kCGEventMouseMoved, c); time.sleep(0.25)
        post(Quartz.kCGEventLeftMouseDown, c); time.sleep(0.18)
        for i in range(1, 13):
            post(Quartz.kCGEventLeftMouseDragged, (c[0]+dx*i/12, c[1]-dy*i/12)); time.sleep(0.04)
        post(Quartz.kCGEventLeftMouseUp, (c[0]+dx, c[1]-dy)); time.sleep(0.45)
        f1 = o._panel.frame()
        dw, dh = f1.size.width-f0.size.width, f1.size.height-f0.size.height
        mv = abs(f1.origin.x-f0.origin.x) > 2 or abs(f1.origin.y-f0.origin.y) > 2
        v = (f"缩放 d=({dw:+.0f},{dh:+.0f})" if (abs(dw) > 3 or abs(dh) > 3)
             else ("移动" if mv else "无反应"))
        S["out"].append(f"  {label:<3} {v}")
    S["go"] = False


def main():
    subprocess.run(["osascript", "-e",
        'tell application "System Events" to set visible of every process whose background only is false to false'], check=False)
    time.sleep(0.9)
    o = Overlay(); o.show()
    for i in range(20): o.finalize(f"S{i}.", f"第 {i} 句。")
    APP.activateIgnoringOtherApps_(True)
    threading.Thread(target=drive, args=(o,), daemon=True).start()
    t0 = time.monotonic()
    while time.monotonic()-t0 < 110:
        if S["reset"]:
            o._panel.setFrame_display_(NSMakeRect(X0, Y0, W0, H0), True)
            S["reset"] = False
        o.pump()
        if not S["go"]:
            break
    for l in S["out"]: print(l)
    o.close()
    subprocess.run(["osascript", "-e",
        'tell application "System Events" to set visible of every process to true'], check=False)


main()
