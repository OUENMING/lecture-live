#!/usr/bin/env python3
"""测试模式 3.9.8 ①：真机上的**能耗 / 唤醒 / CPU / 供电**快照（`proc_pid_rusage` + `ioreg`）。

为什么
------
3.9.7 的省电（空闲档 0.1 s / 隐藏档 0.25 s + 窗口不可见停渲染）只在**离线回放**里量过
（`overlay.py` 常量那段：整机 7.56 → 5.77 W）。真机上到底省了多少、是不是**只在隐藏时**
省、一节课有多少次唤醒 —— 报告里一个数都查不到。这一格补的就是这三件。

怎么取数（**全是只读**，不 sudo / 不 powermetrics / 不写 pmset）
----------------------------------------------------------------
* `proc_pid_rusage(getpid(), RUSAGE_INFO_V6)`（ctypes 调 `libproc.dylib`；macOS 12 之前退回 V4）：
  `ri_energy_nj`（**纳焦**）· `ri_interrupt_wkups` · `ri_pkg_idle_wkups` ·
  `ri_user_time` / `ri_system_time`（mach 绝对时间单位，用 `mach_timebase_info` 换成秒）。
  ⚠️ 能量**必须**取 V6 的 `ri_energy_nj`：V4 的 `ri_billed_energy` 在本机（M1 Pro）实测恒为 0；
     V6 下 1 s 忙循环涨 3.4 J（2026-10-16）。3.9.7 的 A/B（`prof_main.py`）用的也是它。
  ⚠️ 字段偏移错一位读出来的就是另一个计数器 —— 判据拿 `resource.getrusage` 的 CPU 秒**对拍**
     钉住偏移与换算（见 `tests/test_energy.py`）。
* `ioreg -rn AppleSmartBattery` 的 `ExternalConnected`（**只读**）：台式机 / 没电池 → `None`。
  ⚠️ 逐行**前缀**匹配 `"ExternalConnected"` —— 输出里还有个 `AppleRawExternalConnected`
     与 `FedExternalConnected`，子串匹配会把它们算进来。
* 屏幕刷新率由调用方给（`overlay.Overlay.screen_hz`）—— 热档让步就是按它算的。

⚠️ **两个刻意的口径**
  1. 分桶的计时用 `time.monotonic`（只回答「哪一段算可见」）；报告里的墙钟必须用
     `time.time`（monotonic 在系统睡眠时不走，见 `testmode` 的 capture 块）。
  2. 两个桶之和**按构造等于总量**：每次可见性变化都在**同一个读者**上取快照、逐段相减，
     求和会望远镜式抵消 —— 判据拿这条钉「分桶没漏没重」。

⚠️ 全模块**绝不抛**：任何一步失败只该让报告少一个字段（`testmode` 那边再兜一层）。
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os
import subprocess
import time
from collections.abc import Callable

#: `bsd/sys/resource.h`：RUSAGE_INFO_V4 = 4。
RUSAGE_INFO_V4 = 4
#: `ri_billed_energy` 的单位是**纳焦**。
_NJ_PER_J = 1e9

#: `rusage_info_v4` 的字段顺序（`uint8 ri_uuid[16]` 之后全是 `uint64`）。
#: ⚠️ **别改顺序**：这是与内核 ABI 逐字对齐的，改一位读到的就是另一个计数器。
_FIELDS = (
    "ri_user_time", "ri_system_time", "ri_pkg_idle_wkups", "ri_interrupt_wkups",
    "ri_pageins", "ri_wired_size", "ri_resident_size", "ri_phys_footprint",
    "ri_proc_start_abstime", "ri_proc_exit_abstime",
    "ri_child_user_time", "ri_child_system_time",
    "ri_child_pkg_idle_wkups", "ri_child_interrupt_wkups",
    "ri_child_pageins", "ri_child_elapsed_abstime",
    "ri_diskio_bytesread", "ri_diskio_byteswritten",
    "ri_cpu_time_qos_default", "ri_cpu_time_qos_maintenance",
    "ri_cpu_time_qos_background", "ri_cpu_time_qos_utility",
    "ri_cpu_time_qos_legacy", "ri_cpu_time_qos_user_initiated",
    "ri_cpu_time_qos_user_interactive",
    "ri_billed_system_time", "ri_serviced_system_time",
    "ri_logical_writes", "ri_lifetime_max_phys_footprint",
    "ri_instructions", "ri_cycles",
    "ri_billed_energy", "ri_serviced_energy",
    "ri_interval_max_phys_footprint", "ri_runnable_time",
)


class _RUsageV4(ctypes.Structure):
    _fields_ = [("ri_uuid", ctypes.c_uint8 * 16)] + [(n, ctypes.c_uint64) for n in _FIELDS]


#: `bsd/sys/resource.h`：RUSAGE_INFO_V6 = 6（macOS 12+）。V4 之后接着这几项，再留 9 个保留位。
#: ⭐ 能量取 V6 的 `ri_energy_nj`：V4 的 `ri_billed_energy` 在 Apple Silicon 上实测**恒为 0**
#:    （2026-10-16，0.8 s 忙循环一个都不涨）；3.9.7 的 A/B（`prof_main.py`）用的就是 `ri_energy_nj`。
RUSAGE_INFO_V6 = 6
_FIELDS_V6 = _FIELDS + (
    "ri_flags", "ri_user_ptime", "ri_system_ptime", "ri_pinstructions", "ri_pcycles",
    "ri_energy_nj", "ri_penergy_nj", "ri_secure_time_in_system", "ri_secure_ptime_in_system",
    "ri_neural_footprint", "ri_lifetime_max_neural_footprint", "ri_interval_max_neural_footprint",
)


class _RUsageV6(ctypes.Structure):
    _fields_ = ([("ri_uuid", ctypes.c_uint8 * 16)] + [(n, ctypes.c_uint64) for n in _FIELDS_V6]
                + [("ri_reserved", ctypes.c_uint64 * 9)])


#: `mach_timebase_info_data_t` = {uint32 numer; uint32 denom;}。
class _Timebase(ctypes.Structure):
    _fields_ = [("numer", ctypes.c_uint32), ("denom", ctypes.c_uint32)]


#: 懒加载的 `proc_pid_rusage`（拿不到 = `None`）。`_LOADED` 是「试过了」而不是「成功了」——
#: 失败过就不要再每次重试（它跑在热路径的可见性钩子上）。
_LOADED = False
_RUSAGE: "Callable[..., int] | None" = None
_TIMEBASE: tuple[int, int] | None = None


def _load() -> "Callable[..., int] | None":
    """解析一次 `libproc` / `mach_timebase_info`。**绝不抛**（失败 → `None`）。"""
    global _LOADED, _RUSAGE, _TIMEBASE
    if _LOADED:
        return _RUSAGE
    _LOADED = True
    try:
        libproc = ctypes.CDLL(ctypes.util.find_library("proc") or "/usr/lib/libproc.dylib")
        fn = libproc.proc_pid_rusage
        fn.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]   # V6 / V4 两种缓冲都传
        fn.restype = ctypes.c_int
        _RUSAGE = fn
    except Exception:                                     # noqa: BLE001
        _RUSAGE = None
    try:
        libc = ctypes.CDLL(None)
        tb = _Timebase()
        if libc.mach_timebase_info(ctypes.byref(tb)) == 0 and tb.denom:
            _TIMEBASE = (int(tb.numer), int(tb.denom))
    except Exception:                                     # noqa: BLE001
        _TIMEBASE = None
    return _RUSAGE


def timebase() -> tuple[int, int] | None:
    """`(numer, denom)`；拿不到 → `None`。纯读。"""
    _load()
    return _TIMEBASE


def mach_seconds(ticks: float) -> float | None:
    """mach 绝对时间单位 → 秒；拿不到 timebase → `None`。纯函数。"""
    tb = timebase()
    if not tb:
        return None
    numer, denom = tb
    if not denom:
        return None
    return float(ticks) * numer / denom / 1e9


def snapshot(pid: int | None = None) -> dict | None:
    """此刻的 rusage 快照（**只读**）。拿不到 → `None`。

    返回的键：`energy_nj` · `interrupt_wkups` · `pkg_idle_wkups` · `user_s` · `system_s` · `cpu_s`。
    `user_s` / `system_s` 由 mach 单位换算；timebase 拿不到时它们与 `cpu_s` 是 `None`，
    但那三个计数器照给（"少一个字段"而不是"整块没了"）。
    """
    fn = _load()
    if fn is None:
        return None
    try:
        _pid = int(os.getpid() if pid is None else pid)
        buf = _RUsageV6()
        if fn(_pid, RUSAGE_INFO_V6, ctypes.byref(buf)) == 0:
            energy = int(buf.ri_energy_nj)
        else:                                         # 老系统没有 V6 → 退回 V4（能量可能恒 0）
            buf = _RUsageV4()
            if fn(_pid, RUSAGE_INFO_V4, ctypes.byref(buf)) != 0:
                return None
            energy = int(buf.ri_billed_energy)
        user_s = mach_seconds(buf.ri_user_time)
        sys_s = mach_seconds(buf.ri_system_time)
        return {
            "energy_nj": energy,
            "interrupt_wkups": int(buf.ri_interrupt_wkups),
            "pkg_idle_wkups": int(buf.ri_pkg_idle_wkups),
            "user_s": None if user_s is None else round(user_s, 3),
            "system_s": None if sys_s is None else round(sys_s, 3),
            "cpu_s": (None if user_s is None or sys_s is None
                      else round(user_s + sys_s, 3)),
        }
    except Exception:                                     # noqa: BLE001
        return None


def power_plugged(timeout: float = 2.0) -> bool | None:
    """是否接着外接电源。**只读** `ioreg -rn AppleSmartBattery`；台式机 / 读不到 → `None`。"""
    try:
        out = subprocess.run(["ioreg", "-rn", "AppleSmartBattery"],
                             capture_output=True, text=True, timeout=timeout).stdout
    except Exception:                                     # noqa: BLE001
        return None
    for line in out.splitlines():
        s = line.strip()
        # ⚠️ 前缀匹配：`AppleRawExternalConnected` / `FedExternalConnected` 也含这个子串。
        if not s.startswith('"ExternalConnected"'):
            continue
        val = s.split("=", 1)[-1].strip().strip('"')
        if val in ("Yes", "true", "1"):
            return True
        if val in ("No", "false", "0"):
            return False
    return None


#: 每个桶一开始都长这样（键齐全 —— 报告里"没有"与"是零"要分得开）。
_EMPTY_BUCKET = {"energy_j": 0.0, "wkups": 0, "cpu_s": 0.0, "wall_s": 0.0}


class EnergyProbe:
    """一节课的能耗分桶。开课时构造（取第一次快照），收尾 `block()` 取第二次。

    ⚠️ 只在**可见性变化**时取快照（不是每帧）—— 可见性变化是低频事件，一次
       `proc_pid_rusage` 是微秒级系统调用。
    ⚠️ `clock` / `snapshot` / `power` 都可注入（判据用它对齐时间线，不许白等）。
    """

    def __init__(self, snapshot=snapshot, clock=time.monotonic, power=power_plugged):
        self._snap = snapshot
        self._clock = clock
        self._t0 = clock()
        self._start = snapshot()
        self._prev = self._start
        self._prev_t = self._t0
        self._cur_vis = True                 # 开课那一刻窗口是看得见的（口径同 overlay）
        self.visible = dict(_EMPTY_BUCKET)
        self.hidden = dict(_EMPTY_BUCKET)
        #: pump 各档累计秒数（`overlay._yield_to_runloop` 按**实际**等待时长喂）。
        self.pump = {"gesture": 0.0, "hot": 0.0, "idle": 0.0, "hidden": 0.0}
        try:
            self.power_start = power()
        except Exception:                                 # noqa: BLE001
            self.power_start = None

    def on_vis(self, visible: bool) -> None:
        """可见性刚变了：把**刚过去**这一段累进上一档，再取一次快照。**绝不抛**。"""
        try:
            s = self._snap()
        except Exception:                                 # noqa: BLE001
            s = None
        try:
            now = self._clock()
        except Exception:                                 # noqa: BLE001
            return
        self._close(s, now)
        self._cur_vis = bool(visible)

    def add_wait(self, tier: str, seconds: float) -> None:
        """pump 一档的实际等待时长（秒）。未知档位忽略（不建新键）。**绝不抛**。"""
        try:
            if tier in self.pump:
                self.pump[tier] += max(0.0, float(seconds))
        except Exception:                                 # noqa: BLE001
            pass

    def _close(self, s: dict | None, now: float) -> None:
        """把 `_prev_t → now` 这一段记进当前档；`s` 是这一段结尾的快照（可为 None）。"""
        b = self.visible if self._cur_vis else self.hidden
        try:
            b["wall_s"] += max(0.0, now - self._prev_t)
        except Exception:                                 # noqa: BLE001
            pass
        if s is not None and self._prev is not None:
            try:
                b["energy_j"] += (s["energy_nj"] - self._prev["energy_nj"]) / _NJ_PER_J
                b["wkups"] += s["interrupt_wkups"] - self._prev["interrupt_wkups"]
                if s["cpu_s"] is not None and self._prev["cpu_s"] is not None:
                    b["cpu_s"] += s["cpu_s"] - self._prev["cpu_s"]
            except Exception:                             # noqa: BLE001
                pass
        self._prev, self._prev_t = s, now

    def _delta(self, key: str, end: dict | None) -> float | None:
        """`end - start`（同一个读者上的两次快照）；任一端拿不到 → `None`。"""
        start = self._start
        if start is None or end is None:
            return None
        a, b = start.get(key), end.get(key)
        if a is None or b is None:
            return None
        return float(b) - float(a)

    def block(self, screen_hz: float | None = None) -> dict:
        """收尾：关掉最后一个桶，给出 `energy` 块。**绝不抛**（失败只少字段）。"""
        try:
            s = self._snap()
        except Exception:                                 # noqa: BLE001
            s = None
        try:
            now = self._clock()
        except Exception:                                 # noqa: BLE001
            now = self._prev_t
        self._close(s, now)

        def _r(v, n):
            return None if v is None else round(v, n)

        def _bucket(b):
            out = {"energy_j": round(b["energy_j"], 3), "wkups": int(b["wkups"]),
                   "cpu_s": round(b["cpu_s"], 1), "wall_s": round(b["wall_s"], 1)}
            out["mean_w"] = (round(b["energy_j"] / b["wall_s"], 3)
                             if b["wall_s"] > 0 else None)
            return out

        energy_j = self._delta("energy_nj", s)
        cpu_s = self._delta("cpu_s", s)
        wkups = self._delta("interrupt_wkups", s)
        pkg = self._delta("pkg_idle_wkups", s)
        out = {
            "available": self._start is not None and s is not None,
            "power_plugged_start": getattr(self, "power_start", None),
            "power_plugged_end": None,
            "screen_hz": screen_hz,
            "wall_s": round(max(0.0, now - self._t0), 1),
            "billed_energy_j": _r(None if energy_j is None else energy_j / _NJ_PER_J, 3),
            "interrupt_wkups": None if wkups is None else int(wkups),
            "pkg_idle_wkups": None if pkg is None else int(pkg),
            "cpu_s": _r(cpu_s, 1),
            "rusage_start": self._start,
            "rusage_end": s,
            "visible": _bucket(self.visible),
            "hidden": _bucket(self.hidden),
            "pump_s": {k: round(v, 1) for k, v in self.pump.items()},
        }
        try:
            out["power_plugged_end"] = power_plugged()
        except Exception:                                 # noqa: BLE001
            out["power_plugged_end"] = None
        return out
