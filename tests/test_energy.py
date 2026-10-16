#!/usr/bin/env python3
"""⭐ 3.9.8 ① ② 的判据：真机能耗分桶（`energy.py`）+ `streamq` 唤醒延迟（`_WakeQueue`）。

    ClassLive.app/Contents/MacOS/python tests/test_energy.py

## 钉住了什么

1. ⭐ `energy.snapshot()`（`proc_pid_rusage` RUSAGE_INFO_V4）：忙循环前后各计数器
   **单调不减**、CPU 秒**严格增**，而且那个 CPU 秒与 `resource.getrusage` 的
   utime+stime **对拍得上** —— 这一条才真正钉住「字段偏移 + mach→秒换算」没错位。
2. ⭐⭐ 可见 / 隐藏两个桶之和 == 总量（wall 精确、cpu/energy 在舍入误差内）——
   每次变化都在同一个读者上取快照、逐段相减，求和会望远镜式抵消。
3. ⭐ `power_plugged()` 只读 `ioreg`，返回 `True/False/None`（台式机 / 读不到 = None）。
4. ⭐ `Overlay` 的四个采集钩子（`on_vis` / `on_wait` / `on_view` / `on_power_event`）：
   可见性刚变才回调、pump 的实际等待时长报得出、视图采样**限流到 1 Hz**；
   默认 `None` = 一个字节都不动。
5. ⭐⭐ `_WakeQueue`：没装 stats 时**连 deque 都不建**（= 与旧实现逐字相同）；
   装了之后 put→get 的停留时间量得准（立刻取 < 50 ms、睡 0.2 s 后 ≥ 200 ms），
   多线程投 800 条后时间戳与 item **条数对得上**（同序）。
6. ⭐ `blocks.energy` 的形状 + `blocks.ui.streamq_dwell_ms` 的汇总（p50/p90/p99/max）。

⚠️ 写端全在临时目录；不联网、不 sudo、不动系统设置。
⚠️ 每条断言先问「把实现改坏它会不会红」—— 见每条的注释。
"""
from __future__ import annotations

import json
import pathlib
import queue
import resource
import sys
import tempfile
import threading
import time

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import energy                                                        # noqa: E402
import numpy as np                                                    # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  {detail}" if detail else ""))


def _busy(sec: float = 0.4) -> None:
    t0 = time.time()
    x = 0
    while time.time() - t0 < sec:
        x += 1


def main() -> int:                                                    # noqa: C901
    print("\n--- E1 ⭐⭐ rusage：单调不减 + CPU 秒与 getrusage 对拍 ---")
    s0 = energy.snapshot()
    if s0 is None:
        print("  ⏭️  proc_pid_rusage 拿不到（非 macOS / API 变了），跳过 E1/E2")
    else:
        check("⭐ 快照的六个键齐全（少一个字段也要能看出是哪一个）",
              set(s0) == {"energy_nj", "interrupt_wkups", "pkg_idle_wkups",
                          "user_s", "system_s", "cpu_s"}, str(sorted(s0)))
        ru0 = resource.getrusage(resource.RUSAGE_SELF)
        cpu0 = ru0.ru_utime + ru0.ru_stime
        _busy(0.4)
        s1 = energy.snapshot()
        check("⭐ 快照拿得到（第二次）", s1 is not None)
        # ⚠️ 改坏 1：把 `_FIELDS` 删一格 / 调顺序 → 结构体与内核 ABI 错位 → 下面几条会红。
        # ⚠️ 改坏 2：`mach_seconds` 里去掉 `numer/denom` → CPU 秒差 ~40 倍 → 对拍那条红。
        # ⚠️ 单调：**只要求不减** —— 实测（2026-10-16）本机 0.8 s 忙循环里
        #    `ri_billed_energy` / `ri_interrupt_wkups` **一个都不动**，拿"能量必须涨"
        #    当判据会假红。这几条抓的是「读到了另一个计数器 / 负增量」。
        for k in ("energy_nj", "interrupt_wkups", "pkg_idle_wkups"):
            check(f"⭐ `{k}` 忙循环前后单调不减",
                  s1[k] is not None and s1[k] >= s0[k], f"{s0[k]} → {s1[k]}")
        # V4 的 `ri_billed_energy` 在 Apple Silicon 上恒为 0（2026-10-16 实测）—— 读错字段时
        # 上面那条「单调不减」照样绿，只有「严格增」抓得住。
        check("⭐⭐ 能量**严格增**（忙循环 0.4 s；读到 V4 恒 0 的字段会红在这里）",
              s1["energy_nj"] is not None and s1["energy_nj"] > s0["energy_nj"],
              f"{s0['energy_nj']} → {s1['energy_nj']}")
        check("⭐⭐ CPU 秒**严格增**（忙循环 0.4 s）",
              s1["cpu_s"] is not None and s1["cpu_s"] > s0["cpu_s"],
              f"{s0['cpu_s']} → {s1['cpu_s']}")
        ru1 = resource.getrusage(resource.RUSAGE_SELF)
        d_ru = (ru1.ru_utime + ru1.ru_stime) - cpu0
        d_snap = (s1["cpu_s"] or 0.0) - (s0["cpu_s"] or 0.0)
        check("⭐⭐ rusage 的 CPU 秒与 `resource.getrusage` **对拍得上**（偏移/换算没错位）",
              abs(d_snap - d_ru) < 0.2, f"snapshot Δ{d_snap:.3f}s vs getrusage Δ{d_ru:.3f}s")
        check("⭐ `mach_seconds` 换出来的秒在合理量级（不是纳秒/微秒）",
              0.0 < (s0["cpu_s"] or 0.0) < 1e6, f"cpu_s={s0['cpu_s']}")

        print("\n--- E2 ⭐⭐ 可见/隐藏两桶之和 == 总量（分桶没漏没重）---")
        clk = [1000.0]
        p = energy.EnergyProbe(clock=lambda: clk[0])
        _busy(0.3)
        clk[0] += 100.0
        p.on_vis(False)                                   # 100 s 可见
        _busy(0.3)
        clk[0] += 40.0
        p.on_vis(True)                                    # 40 s 隐藏
        p.add_wait("idle", 12.0)
        p.add_wait("hot", 3.0)
        p.add_wait("不存在档位", 999.0)                     # ⚠️ 不许建新键
        clk[0] += 10.0
        b = p.block(120.0)
        s = b["visible"]["wall_s"] + b["hidden"]["wall_s"]
        check("⭐⭐ 两个桶的墙钟之和 == 总墙钟（< 0.2 s）",
              abs(s - b["wall_s"]) < 0.2, f"{s} vs {b['wall_s']}")
        check("⭐ 两档都用到了（不然上面那条是恒真的）",
              b["visible"]["wall_s"] > 0 and b["hidden"]["wall_s"] > 0,
              f"visible={b['visible']['wall_s']} hidden={b['hidden']['wall_s']}")
        if b["available"] and b["cpu_s"] is not None:
            csum = b["visible"]["cpu_s"] + b["hidden"]["cpu_s"]
            # ⚠️ 容差分两层：**舍入**（三处各 0.1 s 的四舍五入 → 最坏 0.15 s）+ **5%**。
            #    只按 5‰ 卡会在"这节课 CPU 只有 0.6 s"时被 0.1 s 的舍入顶红（假红）。
            check("⭐⭐ 两桶 CPU 秒之和 ≈ 总 CPU 秒（舍入 + 5%）",
                  abs(csum - b["cpu_s"]) < max(0.25, 0.05 * abs(b["cpu_s"])),
                  f"{csum} vs {b['cpu_s']}")
            esum = b["visible"]["energy_j"] + b["hidden"]["energy_j"]
            etot = b["billed_energy_j"] or 0.0
            check("⭐ 两桶能量之和 ≈ 总能量（< 5%）",
                  abs(esum - etot) < max(0.02, 0.05 * abs(etot)),
                  f"{esum} vs {etot}")
        check("⭐ `pump_s` 四档都在，未知档位被忽略（没建第 5 个键）",
              set(b["pump_s"]) == {"gesture", "hot", "idle", "hidden"}
              and b["pump_s"]["idle"] == 12.0, str(b["pump_s"]))
        check("⭐ 块的顶层字段齐全（screen_hz / 供电 / rusage 原样 / 两桶）",
              {"available", "power_plugged_start", "power_plugged_end", "screen_hz",
               "wall_s", "billed_energy_j", "interrupt_wkups", "pkg_idle_wkups",
               "cpu_s", "rusage_start", "rusage_end", "visible", "hidden",
               "pump_s"} <= set(b), str(sorted(b)))
        # 改坏：把 `on_vis` 里那行 `_close(...)` 删掉 → 桶的 wall 恒为 0 → 第 1、2 条红；
        #       把 `_close` 用 `self._t0` 而不是 `_prev_t` 算 → 桶之和翻好几倍 → 第 1 条红。
        if isinstance(b["rusage_end"], dict):
            check("⭐ `rusage_end` 是换好算的秒（user_s/system_s/cpu_s 都换成秒）",
                  b["rusage_end"]["cpu_s"] is None or b["rusage_end"]["cpu_s"] > 0,
                  str(b["rusage_end"]))

    print("\n--- E3 ⭐ 供电：只读 `ioreg`，返回 True/False/None ---")
    pw = energy.power_plugged()
    check("⭐ `power_plugged()` ∈ {True, False, None}（台式机 / 读不到 = None）",
          pw in (True, False, None), repr(pw))

    print("\n--- E4 ⭐⭐ Overlay 的四个采集钩子 ---")
    try:
        from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
        NSApplication.sharedApplication().setActivationPolicy_(
            NSApplicationActivationPolicyAccessory)
    except Exception as e:                                            # noqa: BLE001
        print(f"  ⏭️  AppKit 不可用，跳过 E4：{type(e).__name__}")
    else:
        import overlay as ov
        seen: list = []
        o = ov.Overlay(on_vis=lambda v: seen.append(("vis", v)),
                       on_wait=lambda t, s: seen.append(("wait", t, s)),
                       on_view=lambda r, ln, w: seen.append(("view", r, ln, w)),
                       on_power_event=lambda n: seen.append(("power", n)))
        try:
            # ① on_vis：只有**变**了才回调
            o._visible = True
            o._compute_visible = lambda: True
            o._refresh_visibility()
            check("⭐ 可见性没变 → 不回调（不是每轮都叫一次）", seen == [])
            o._compute_visible = lambda: False
            o._refresh_visibility()
            check("⭐⭐ 可见性变了 → `on_vis(False)` 一次", seen == [("vis", False)], str(seen))
            # ④ on_power_event：屏幕睡眠那条路
            o._set_screens_sleeping = lambda s: None                  # 只验回调，不验渲染
            o._screens_event(True, "screen_sleep")
            check("⭐ 屏幕睡眠 → 回调报 `screen_sleep`",
                  seen[-1] == ("power", "screen_sleep"), str(seen[-1]))
            # ① on_wait：这一轮**实际**等了多久
            o._visible = True                     # 上一条把它置成不可见了 —— 复位再测档位
            o._last_ev_t = 0.0
            o._dirty = o._urgent = False
            o._tv._dirty = o._tv._urgent = False
            o._bar_hide_at = None
            o._wait_interruptible = lambda *a, **k: time.sleep(0.03)
            o._yield_to_runloop(None, "NSDefaultRunLoopMode", time.monotonic())
            _w = [x for x in seen if x[0] == "wait"]
            check("⭐⭐ pump 一档的实际等待时长报得出来（≥ 30 ms）",
                  len(_w) == 1 and _w[0][1] == ov.WAIT_IDLE and _w[0][2] >= 0.028, str(_w))
            # ⑤ on_view：1 Hz 限流 + 行数/档位/宽度
            o._scroll_h, o._row_h = 100.0, 50.0
            o._view_t_last = 0.0
            n0 = len([x for x in seen if x[0] == "view"])
            o._sample_view(100.0)
            o._sample_view(100.5)
            o._sample_view(101.2)
            _v = [x for x in seen if x[0] == "view"]
            check("⭐⭐ 视图采样限流到 1 Hz（三次调用只出两次）",
                  len(_v) - n0 == 2, f"{len(_v) - n0} 次")
            check("⭐ 报的是「视口高/行高」取整 + 档位行数 + 面板宽",
                  _v[-1][1] == 2 and _v[-1][2] == ov._lines_for_width(o._width)
                  and _v[-1][3] == float(o._width), str(_v[-1]))
            check("⭐ `screen_hz` 拿得到正数（热档让步按它算）",
                  o.screen_hz is None or o.screen_hz > 0, repr(o.screen_hz))
        finally:
            try:
                o.close()
            except Exception:                                         # noqa: BLE001
                pass

        # ⚠️ 默认 None = 零开销零行为（这是"tester is None 时与现在逐字相同"那条纪律）
        o2 = ov.Overlay()
        try:
            o2._compute_visible = lambda: False
            o2._visible = True
            o2._refresh_visibility()          # 没有任何钩子，不许抛
            o2._sample_view(1.0)
            o2._power_event("x")
            check("⭐⭐ 四个钩子默认 `None`，且没装时调用它们**一个字节都不动**",
                  o2._on_vis is None and o2._on_wait is None
                  and o2._on_view is None and o2._on_power_event is None
                  and o2._view_t_last == 0.0, str(o2._view_t_last))
        finally:
            try:
                o2.close()
            except Exception:                                         # noqa: BLE001
                pass

    print("\n--- E5 ⭐⭐ `_WakeQueue` 唤醒延迟（3.9.8 ②）---")
    import main as M
    q = M._WakeQueue()
    check("⭐⭐ 没装 stats 时**连 deque 都不建**（= 与旧实现逐字相同）",
          q._put_t is None and q._stats is None and q._t_lock is None,
          f"_put_t={q._put_t!r}")
    q.put("a")
    q.put_nowait("b")
    check("⭐ 没 stats 时行为一字不变（put/get_nowait 照旧）",
          q.get_nowait() == "a" and q.get_nowait() == "b" and q.empty())

    dwell: list = []
    q.set_stats(dwell.append)
    q.put("x")
    check("⭐ put 之后立刻 get：停留 < 50 ms（这才是「没被拖慢」）",
          q.get_nowait() == "x" and dwell and dwell[0] < 50.0, str(dwell))
    q.put("y")
    time.sleep(0.2)
    check("⭐⭐ sleep 0.2 s 再 get：停留 ≥ 200 ms（时间戳真的记了）",
          q.get_nowait() == "y" and len(dwell) == 2 and dwell[1] >= 200.0, str(dwell))
    try:
        q.get_nowait()
        check("⭐ 空队时照抛 `queue.Empty`（drain 靠它收尾）", False, "没抛")
    except queue.Empty:
        check("⭐ 空队时照抛 `queue.Empty`（drain 靠它收尾）", True)
    q.set_stats(None)
    check("⭐ 卸掉 stats → deque 丢掉、退回旧行为",
          q._put_t is None and q._stats is None and q._t_lock is None)

    # ⚠️ 守恒：4 个线程各投 200 条 —— 时间戳必须与 item **条数一致**（同序配对不丢不错）
    q2 = M._WakeQueue()
    got: list = []
    q2.set_stats(got.append)
    for k in range(4):
        threading.Thread(target=lambda kk=k: [q2.put((kk, i)) for i in range(200)],
                         daemon=True).start()
    n = 0
    deadline = time.monotonic() + 5.0
    while n < 800 and time.monotonic() < deadline:
        try:
            q2.get_nowait()
            n += 1
        except queue.Empty:
            time.sleep(0.01)
    check("⭐⭐ 多线程投 800 条：drain 全取到 + 时间戳条数对得上（同序）",
          n == 800 and len(got) == 800 and len(q2._put_t) == 0,
          f"取到 {n} / 时间戳 {len(got)} / 残留 {len(q2._put_t)}")
    # 改坏：`put` 里把 append 挪到 `_wake()` 之后（或 get_nowait 不 popleft）
    #       → 上面那两条之一红（顺序错位 / 条数对不上）。

    print("\n--- E6 ⭐ 报告里的 dwell 汇总 + blocks.energy 的形状 ---")
    import testmode as tm
    d = pathlib.Path(tempfile.mkdtemp(prefix="cl_energy_"))
    try:
        t = tm.TestSession(d / "e.md")
        for v in (5.0, 12.0, 30.0, 200.0):
            t.note_streamq_dwell(v)
        check("⭐ `dwell_summary` 给 n/p50/p90/p99/max",
              tm.dwell_summary([5.0, 12.0, 30.0, 200.0])["max"] == 200.0
              and tm.dwell_summary([5.0, 12.0, 30.0, 200.0])["n"] == 4,
              str(tm.dwell_summary([5.0, 12.0, 30.0, 200.0])))
        check("⭐ 一个数都没有 → `None`（不给 0 —— 0 会被读成「零延迟」）",
              tm.dwell_summary([]) is None)
        t.note_block("energy", t.energy_block(120.0))
        t.note_block("capture", t.capture_block())
        rep = json.loads(pathlib.Path(t.finish(bundle=False)).read_text(encoding="utf-8"))
        check("⭐⭐ `blocks.ui.streamq_dwell_ms` 进了报告（p50/p90/p99/max/n）",
              rep["blocks"]["ui"]["streamq_dwell_ms"]["n"] == 4
              and rep["blocks"]["ui"]["streamq_dwell_ms"]["max"] == 200.0,
              str(rep["blocks"]["ui"].get("streamq_dwell_ms")))
        check("⭐ `blocks.energy` 在（`available` 与屏幕刷新率都能读）",
              "energy" in rep["blocks"] and "available" in rep["blocks"]["energy"],
              str(rep["blocks"]["energy"].get("available")))
        check("⭐⭐ `blocks` 里没有 `bad_block`（名字都在 `BLOCKS` 里登记过）",
              not [e for e in rep["events"] if e["kind"] == "bad_block"],
              str([e for e in rep["events"] if e["kind"] == "bad_block"]))
        check("⭐ `energy` / `capture` 在 `block_names`（该有哪几块的清单）里",
              {"energy", "capture"} <= set(rep["block_names"]), str(rep["block_names"]))
        json.dumps(rep, ensure_ascii=False)          # 不许炸
        check("⭐ 整份报告 `json.dumps` 不炸", True)
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)

    bad = [n for n, ok, _ in RESULTS if not ok]
    print("\n" + "=" * 60)
    print(f"{len(RESULTS) - len(bad)}/{len(RESULTS)} 通过")
    for n in bad:
        print(f"  ❌ {n}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
