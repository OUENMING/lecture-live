"""1) 原生边缘拖拽期间, 我们自己的 pump 循环有没有被 AppKit 的 tracking 循环卡住?
   2) 细网格: 面板上哪些位置能移动窗口。"""
import sys, threading, time
import Quartz
import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[2]))
from AppKit import NSScreen, NSApplication, NSMakeRect
from overlay import Overlay

H = NSScreen.mainScreen().frame().size.height
APP = NSApplication.sharedApplication()
S = {"go": True, "out": [], "reset": False, "stamps": []}
X0, Y0, W0, H0 = 300.0, 420.0, 660.0, 362.0


def post(k, p): Quartz.CGEventPost(Quartz.kCGHIDEventTap, Quartz.CGEventCreateMouseEvent(None, k, p, 0))
def to_cg(x, y): return (float(x), float(H - float(y)))


def drive(o):
    time.sleep(2.0)
    # --- 1) pump 间隔 ---
    S["stamps"].clear()
    S["phase"] = "idle"
    time.sleep(0.8)
    S["phase"] = "drag"
    f0 = o._panel.frame()
    c = to_cg(f0.origin.x + f0.size.width - 1.0, f0.origin.y + f0.size.height / 2)
    post(Quartz.kCGEventMouseMoved, c); time.sleep(0.2)
    post(Quartz.kCGEventLeftMouseDown, c); time.sleep(0.15)
    for i in range(1, 21):
        post(Quartz.kCGEventLeftMouseDragged, (c[0] + 80 * i / 20, c[1])); time.sleep(0.05)
    post(Quartz.kCGEventLeftMouseUp, (c[0] + 80, c[1])); time.sleep(0.4)
    S["phase"] = "after"
    time.sleep(0.5)
    S["phase"] = "done"

    # --- 2) 细网格移动地图 ---
    grid = []
    for fy in (0.95, 0.85, 0.72, 0.60, 0.45, 0.30, 0.18, 0.06):
        for fx in (0.06, 0.25, 0.5, 0.75, 0.94):
            grid.append((fx, fy))
    for fx, fy in grid:
        S["reset"] = True
        while S["reset"]:
            time.sleep(0.03)
        time.sleep(0.1)
        f0 = o._panel.frame()
        ax = f0.origin.x + f0.size.width * fx
        ay = f0.origin.y + f0.size.height * fy
        c = to_cg(ax, ay)
        post(Quartz.kCGEventMouseMoved, c); time.sleep(0.18)
        post(Quartz.kCGEventLeftMouseDown, c); time.sleep(0.15)
        for i in range(1, 9):
            post(Quartz.kCGEventLeftMouseDragged, (c[0] + 50 * i / 8, c[1] + 50 * i / 8)); time.sleep(0.04)
        post(Quartz.kCGEventLeftMouseUp, (c[0] + 50, c[1] + 50)); time.sleep(0.35)
        f1 = o._panel.frame()
        dw, dh = f1.size.width - f0.size.width, f1.size.height - f0.size.height
        mv = abs(f1.origin.x - f0.origin.x) > 2 or abs(f1.origin.y - f0.origin.y) > 2
        v = "缩" if (abs(dw) > 3 or abs(dh) > 3) else ("移" if mv else "—")
        S["out"].append((fy, fx, v))
    S["go"] = False


def main():
    subprocess_off = None
    o = Overlay(); o.show()
    for i in range(20): o.finalize(f"S{i}.", f"第 {i} 句用于测试。")
    APP.activateIgnoringOtherApps_(True)
    threading.Thread(target=drive, args=(o,), daemon=True).start()
    t0 = time.monotonic()
    while time.monotonic() - t0 < 110:
        if S["reset"]:
            o._panel.setFrame_display_(NSMakeRect(X0, Y0, W0, H0), True)
            S["reset"] = False
        S["stamps"].append((S.get("phase"), time.monotonic()))
        o.pump()
        if not S["go"]:
            break
    # 分析 pump 间隔
    st = S["stamps"]
    gaps = [(st[i+1][1] - st[i][1], st[i][0]) for i in range(len(st) - 1)]
    if gaps:
        mx = max(gaps)
        drag_gaps = [g for g, ph in gaps if ph == "drag"]
        idle_gaps = [g for g, ph in gaps if ph == "idle"]
        print(f"pump 调用总数 {len(st)}")
        print(f"  空闲期最大间隔 {max(idle_gaps)*1000 if idle_gaps else 0:.1f}ms")
        print(f"  拖拽期最大间隔 {max(drag_gaps)*1000 if drag_gaps else 0:.1f}ms  "
              f"(若远大于空闲期 -> AppKit 的 tracking 循环把我们的 pump 卡住了)")
        print(f"  全局最大间隔 {mx[0]*1000:.1f}ms @ {mx[1]}")
    print()
    print("移动/缩放地图 (行=纵向位置, 列=横向位置; 移=能移动窗口, 缩=缩放, —=无反应)")
    rows = {}
    for fy, fx, v in S["out"]:
        rows.setdefault(fy, {})[fx] = v
    xs = sorted({fx for _, fx, _ in S["out"]})
    print("   fy\\fx  " + "  ".join(f"{x:.2f}" for x in xs))
    for fy in sorted(rows, reverse=True):
        print(f"   {fy:.2f}   " + "   ".join(f" {rows[fy].get(x,'?')} " for x in xs))
    o.close()


main()
