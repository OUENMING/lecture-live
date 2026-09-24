"""最终地图: 四角 + 四边 + 移动。"""
import sys, threading, time
import Quartz
import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[2]))
from AppKit import NSScreen, NSApplication, NSMakeRect
from overlay import Overlay, EDGE_BAND
H = NSScreen.mainScreen().frame().size.height
APP = NSApplication.sharedApplication()
S={"go":True,"reset":False,"out":[]}
X0,Y0,W0,H0=300.0,420.0,660.0,362.0
def post(k,p): Quartz.CGEventPost(Quartz.kCGHIDEventTap, Quartz.CGEventCreateMouseEvent(None,k,p,0))
def to_cg(x,y): return (float(x), float(H-float(y)))
def drive(o):
    time.sleep(2.0)
    tests=[("左上角",0.004,0.996,-70,70),("右上角",0.996,0.996,70,70),
           ("左下角",0.004,0.004,-70,-70),("右下角",0.996,0.004,70,-70),
           ("左边中",0.004,0.5,-70,0),("右边中",0.996,0.5,70,0),
           ("上边中",0.5,0.996,0,70),("下边中",0.5,0.004,0,-70),
           ("顶栏左空",0.06,0.95,60,60),("转录区中",0.5,0.6,60,60)]
    for tag,fx,fy,dx,dy in tests:
        S["reset"]=True
        while S["reset"]: time.sleep(0.03)
        time.sleep(0.1)
        f0=o._panel.frame()
        c=to_cg(f0.origin.x+f0.size.width*fx, f0.origin.y+f0.size.height*fy)
        post(Quartz.kCGEventMouseMoved,c); time.sleep(0.22)
        post(Quartz.kCGEventLeftMouseDown,c); time.sleep(0.18)
        for i in range(1,11):
            post(Quartz.kCGEventLeftMouseDragged,(c[0]+dx*i/10,c[1]+dy*i/10)); time.sleep(0.05)
        post(Quartz.kCGEventLeftMouseUp,(c[0]+dx,c[1]+dy)); time.sleep(0.4)
        f1=o._panel.frame()
        dw,dh=f1.size.width-f0.size.width,f1.size.height-f0.size.height
        mx,my=f1.origin.x-f0.origin.x,f1.origin.y-f0.origin.y
        v=("缩放 ✅" if (abs(dw)>3 or abs(dh)>3) else ("移动 ✅" if (abs(mx)>3 or abs(my)>3) else "无反应 ❌"))
        S["out"].append(f"  {tag:<7} d=({dw:+.0f},{dh:+.0f})  origin_d=({mx:+.0f},{my:+.0f})  {v}")
    S["go"]=False
o=Overlay(); o.show()
for i in range(15): o.finalize(f"S{i}.", f"第 {i} 句。")
APP.activateIgnoringOtherApps_(True)
threading.Thread(target=drive,args=(o,),daemon=True).start()
t0=time.monotonic()
while time.monotonic()-t0<120:
    if S["reset"]:
        o._panel.setFrame_display_(NSMakeRect(X0,Y0,W0,H0),True); S["reset"]=False
    o.pump()
    if not S["go"]: break
print(f"EDGE_BAND={EDGE_BAND}  movable={o._panel.isMovableByWindowBackground()}")
for l in S["out"]: print(l)
o.close()
