#!/usr/bin/env python3
"""**输入框的聚焦指示**（作者 2026-10-17 拍板：去掉系统蓝环、改白色细框）在真窗口上的样子。

    ClassLive.app/Contents/MacOS/python scripts/probe_focus_ring.py [--out DIR]

拍这几张（默认写到仓库外的临时目录；`--out` 指定就用它）：

  00-baseline        全屏基线（显示器在睡 / 没录屏权限 → 拍出来是黑的，先拦下）
  01-card-idle       卡片：两格输入框**没点进去**（外壳白 α0.18）
  02-card-editing    点进**标题**那一格（外壳 α0.55；日期那格仍是 0.18，**不连坐**）
  02-zoom            标题那一格的放大图（裁紧 + 放大 2×）
  03-panel-idle      课程面板：搜索框没点进去
  04-panel-editing   点进搜索框（白细框亮起来）
  04-zoom            搜索框的放大图（裁紧 + 放大 2×）
  05-recap-fade      课前回顾卡：卡片底下那行字幕落在**渐隐带**里（`edge_fade` 的证据）

## 三条纪律（照 `probe_duty_recap` / `probe_test_mode`）

⚠️ **只建窗口，不跑 main** —— 回调全是桩；`HOME` / `state_root` / `sessions_dir`
   全指向 `tempfile` 的目录，一个字节都不写 `sessions/` / `~/.classlive/` / vault。
⚠️ **先拍基线**：纯黑或单色 → 退出（那不是"界面没渲染出来"，是屏幕没醒）。
   `.window`（窗口尺寸记忆）备份后还原 —— 同 `probe_motion.isolated_window_file`。
⚠️ **只动自己的窗**：拍之前把自己的窗临时抬到屏保级别再还原（作者的课堂窗口
   跟我们同级、还会自己抬自己 —— `probe_duty_recap` 那次事故的注释逐字记过）。
"""
from __future__ import annotations

import argparse
import datetime
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from probe_hint import _pump, _region_of, _shot                    # noqa: E402
from probe_motion import isolated_window_file                      # noqa: E402

#: 全屏基线小于这么多字节 = 很可能纯黑（真桌面实拍 ~2MB，纯黑 ~10KB）。
MIN_BASELINE_BYTES = 60_000

RECAP_DAY = datetime.date(2026, 10, 16)
RECAP = {
    "course": "ECON10790", "date": "10-15（周四）",
    "points": [{"name": f"知识点{i}", "explain": "一句解释，用来把卡片撑高。"}
               for i in range(6)],
    "stars": ["重点一", "重点二", "重点三"],
    "open": ["还没懂的一", "还没懂的二"],
    "duties": [{"type": "调课", "title": "下周必须到场，要分组做项目",
                "when": "10-19（周一）", "date": "2026-10-19"}],
    "today": ["今天新课件《week6-monopoly.pdf》：Monopoly · Deadweight loss"],
}
CAPTIONS = [
    ("I've gotten the research done over the last few weeks.", "过去几周我已经把研究做完了。"),
    ("There is no class on Wednesday this week.", "这周三没有课。"),
    ("The problem set is due on Friday afternoon.", "习题集周五下午截止。"),
    ("We will start the group project next week.", "下周开始分组项目。"),
]


def _pump_app(seconds: float = 0.6) -> None:
    """跑 run loop（**课程面板**的绘制全排在 `callLater` 上 —— 不跑它窗口是空的）。

    ⚠️ `probe_hint._pump(o, …)` 驱动的是 `Overlay`（它自己有 pump），课程面板没有
       —— 2026-10-17 第一版漏了这一步，拍出来的「面板」是别人的窗口。
    """
    from AppKit import NSApp, NSDate, NSDefaultRunLoopMode
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        ev = NSApp.nextEventMatchingMask_untilDate_inMode_dequeue_(
            2 ** 30, NSDate.dateWithTimeIntervalSinceNow_(0.02), NSDefaultRunLoopMode, True)
        if ev:
            NSApp.sendEvent_(ev)


def _view_region(view, *, pad: float = 7.0, max_w: float | None = None):
    """某个视图的 frame → `screencapture -R` 的区域（翻转同 `probe_hint._region_of`）。

    ⚠️ `convertRect_toView_` 的两个参数是 **先 rect 后 view**（`convertRect:toView:`）；
       传 `None` 当"窗口坐标系"是 AppKit 的约定（PyObjC 也认）。写反了会抛
       `depythonifying struct`（2026-10-17 第一版就是这么错的）。
    """
    from AppKit import NSScreen
    win = view.window()
    r = win.convertRectToScreen_(view.convertRect_toView_(view.bounds(), None))
    scr = win.screen() or NSScreen.mainScreen()
    h = scr.frame().size.height if scr is not None else 0.0
    w = float(r.size.width)
    if max_w is not None:                     # ⚠️ 输入框是整条卡宽（733pt）——
        w = min(w, float(max_w))               #    全宽放大图没法看，从**左**起裁紧
    return (r.origin.x - pad, h - (r.origin.y + r.size.height) - pad,
            w + pad * 2, r.size.height + pad * 2)


#: 放大图最多裁多宽（pt）—— 输入框左边那一段（文字 + 圆角）就够看描边了。
ZOOM_W = 330.0


# ── PIL 只有 homebrew 的 python3 有；App 自带的 python 没有（同 probe_d_layout）──
_ZOOM_CODE = r"""
import pathlib, sys
from PIL import Image
src, dst, k = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), float(sys.argv[3])
im = Image.open(src).convert("RGB")
im = im.resize((max(1, round(im.width * k)), max(1, round(im.height * k))), Image.NEAREST)
im.save(dst)
print(f"{dst} {im.size[0]}x{im.size[1]}")
"""


def _pil_python() -> str | None:
    for cand in (sys.executable, shutil.which("python3"), "/opt/homebrew/bin/python3",
                 "/usr/bin/python3"):
        if not cand or not pathlib.Path(cand).exists():
            continue
        try:
            p = subprocess.run([cand, "-c", "import PIL"], capture_output=True, timeout=30)
        except Exception:                                        # noqa: BLE001
            continue
        if p.returncode == 0:
            return cand
    return None


def _zoom(py: str | None, src: pathlib.Path, dst: pathlib.Path, k: float = 2.0) -> None:
    """把裁紧的那张放大 `k` 倍（没装 PIL 就把它当放大图用，另说一句）。"""
    if py is None:
        shutil.copyfile(src, dst)
        print("  ⚠ 没找到装 PIL 的解释器 → 放大图就是裁紧的原图（未放大）")
        return
    p = subprocess.run([py, "-c", _ZOOM_CODE, str(src), str(dst), str(k)],
                       capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        tail = (p.stderr.strip().splitlines() or ["?"])[-1]
        print(f"  ⚠ 放大失败（{tail}）→ 用裁紧的原图顶上")
        shutil.copyfile(src, dst)
    else:
        print(f"  {p.stdout.strip()}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default="", help="截图目录（默认 tempdir）")
    a = ap.parse_args()

    try:
        from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
        import overlay
    except Exception as e:                                       # noqa: BLE001
        print(f"⚠ 起不来：{type(e).__name__}: {e}")
        return 1
    NSApplication.sharedApplication().setActivationPolicy_(
        NSApplicationActivationPolicyAccessory)

    out = pathlib.Path(a.out) if a.out else pathlib.Path(
        tempfile.mkdtemp(prefix="probe_focus_ring_"))
    out.mkdir(parents=True, exist_ok=True)
    py = _pil_python()

    base = _shot(out / "00-baseline.png")
    print(f"基线（全屏）{base} 字节", flush=True)
    if base < MIN_BASELINE_BYTES:
        print(f"⚠ 基线只有 {base} 字节（< {MIN_BASELINE_BYTES}）：显示器在睡 / "
              f"没开屏幕录制权限，拍下去也是纯黑。等屏幕亮起来再跑。")
        return 1

    def shot(win, name: str, *, region=None, level_up=True) -> int:
        lvl = None
        if level_up:
            try:
                from AppKit import NSScreenSaverWindowLevel
                lvl = win.level()
                win.setLevel_(NSScreenSaverWindowLevel)
            except Exception:                    # noqa: BLE001
                lvl = None
        try:
            win.orderFrontRegardless()
        except Exception:                        # noqa: BLE001
            pass
        time.sleep(0.35)
        p = out / f"{name}.png"
        n = _shot(p, region=region or _region_of(win))
        if lvl is not None:
            try:
                win.setLevel_(lvl)
            except Exception:                    # noqa: BLE001
                pass
        print(f"  {name}.png {n} 字节", flush=True)
        return n

    # ══ 一、悬浮窗：卡片那两格输入框 ══════════════════════════════════
    with isolated_window_file():                 # ⚠️ `.window` 先备份、跑完还原
        o = overlay.Overlay(on_lost=lambda: True)
        try:
            o.show()
            _pump(o, 0.6)
            for en, zh in CAPTIONS:
                o.finalize(en, zh)
            _pump(o, 0.4)
            f = o._panel.frame()
            if 780.0 > float(f.size.height):      # 拉高一点：卡片两格都露出来
                from AppKit import NSMakeRect
                o._panel.setFrame_display_(
                    NSMakeRect(f.origin.x, f.origin.y - (780.0 - f.size.height),
                               f.size.width, 780.0), True)
                _pump(o, 0.4)
            o.duty_update([{"id": "d1", "action": "pending", "type": "调课",
                            "title": "本周三停课", "quote": "There is no class on Wednesday.",
                            "date": "2026-10-21", "t": "13:52:56"}])
            o._duty_open("d1")
            _pump(o, 0.5)
            blk = [b for b in o._card_pending if b["kind"] == "field" and b["key"] == "title"][0]
            fld = blk["fld"]
            shot(o._panel, "01-card-idle")
            raw = out / "_raw-01.png"
            _shot(raw, region=_view_region(blk["box"], max_w=ZOOM_W))
            _zoom(py, raw, out / "01-zoom.png")
            try:
                raw.unlink()
            except OSError:
                pass
            o._panel.makeFirstResponder_(fld)
            _pump(o, 0.45)
            shot(o._panel, "02-card-editing")
            raw = out / "_raw-02.png"
            _shot(raw, region=_view_region(blk["box"], max_w=ZOOM_W))
            _zoom(py, raw, out / "02-zoom.png")
            try:
                raw.unlink()
            except OSError:
                pass
            # 课前回顾那张：卡片底下那行字幕在渐隐带里（`edge_fade` 的证据）
            o._card_close()
            o.recap_show(RECAP, course="ECON10790", day=RECAP_DAY)
            _pump(o, 0.6)
            shot(o._panel, "05-recap-fade")
        finally:
            o.close()

    # ══ 二、课程面板：搜索框 ══════════════════════════════════════════
    import ready as ready_mod
    import entry_panel as E
    td = tempfile.TemporaryDirectory()
    root = pathlib.Path(td.name)
    home = root / "home"
    home.mkdir()
    saved_home = os.environ.get("HOME")
    os.environ["HOME"] = str(home)               # ⚠️ 「笔记库」那条路会读真注册表
    os.environ.pop("OBSIDIAN_VAULT", None)
    orig_items = ready_mod.items
    # ⚠️ 给「全正常」而不是空表：空表会让面板弹一张「流式草稿模型还没装」的卡，
    #    把「＋ 新增课程」那条整个盖住（2026-10-17 第一版拍出来就是那样）。
    ready_mod.items = lambda **k: [{"key": "mic", "state": "ok"},
                                   {"key": "models", "state": "ok"},
                                   {"key": "engine", "state": "ok"},
                                   {"key": "vault", "state": "ok", "detail": str(home)}]
    try:
        gl = root / "glossary.txt"
        gl.write_text("# ECON10790 Data Analysis\n", encoding="utf-8")
        h = E.build(glossary=gl, sessions_dir=root / "sessions",
                    state_root=root / "state", on_start=lambda c: None,
                    on_test_mode=lambda on: True, on_cloud_asr=lambda c: True,
                    cloud_asr=True)
        if h is None:
            print("⚠ 课程面板没建起来 —— 后面两张跳过")
        else:
            try:
                # ⚠️ 先把 run loop 转起来：面板的绘制 / 就绪条都排在 `callLater` 上
                _pump_app(1.2)
                try:
                    h.refresh()                    # 程序化再刷一次（跑器也这么干）
                except Exception as e:             # noqa: BLE001
                    print(f"  ⚠ refresh 抛了：{type(e).__name__}: {e}")
                _pump_app(0.6)
                print(f"  面板可见={bool(h.window.isVisible())} "
                      f"屏上={bool(h.window.isOnActiveSpace())}", flush=True)
                fields = h.focus_fields
                search = next((f for f in fields if f is not None
                               and "Search" in type(f).__name__), None)
                if search is None:
                    from AppKit import NSSearchField
                    search = next((f for f in fields
                                   if isinstance(f, NSSearchField)), None)
                shot(h.window, "03-panel-idle")
                if search is not None:
                    raw3 = out / "_raw-03.png"
                    _shot(raw3, region=_view_region(search, pad=9.0, max_w=ZOOM_W))
                    _zoom(py, raw3, out / "03-zoom.png")
                    try:
                        raw3.unlink()
                    except OSError:
                        pass
                    h.window.makeFirstResponder_(search)
                    _pump_app(0.6)
                    shot(h.window, "04-panel-editing")
                    raw2 = out / "_raw-04.png"
                    _shot(raw2, region=_view_region(search, pad=9.0, max_w=ZOOM_W))
                    _zoom(py, raw2, out / "04-zoom.png")
                    try:
                        raw2.unlink()
                    except OSError:
                        pass
            finally:
                E.close_panel()
    finally:
        ready_mod.items = orig_items
        if saved_home is not None:
            os.environ["HOME"] = saved_home
        td.cleanup()

    print(f"\n截图在：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
