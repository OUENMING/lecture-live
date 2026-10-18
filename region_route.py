#!/usr/bin/env python3
"""Soniox 多地区路由的**客户端**半边：开课前的地区真测（probe）+ 24 h 缓存 + 决策。

     r = region_route.Router(passcode=…, endpoint=…)   # 静态决定（读缓存 + 网络指纹），不联网
     r.region          # 领正式 key 时带的 region（"" = 不带，Worker 决定；见下）
     r.start(on_done)  # 起后台探测线程（该跑的版本前 3 节课才跑）；**立刻返回**
     r.snapshot()      # 报告块 / live 事件用

## 为什么不阻塞（这是它存在的第一前提）

「绝不推迟开课 / 转写启动」。所以探测永远在**后台线程**里跑（总预算 `PROBE_TIMEOUT_S` = 3 s），
而**本节的地区只能来自缓存**：本次探测最快也要几百毫秒，而 `StreamASR` 一构造就领 key。
「结果在 Soniox 会话建立前就绪」= 拿上一次课（同一网络、24 h 内）探到的结果做决定；
没缓存 -> 本节带空串，Worker 按国家表 / 大洲表选默认。

## 探测协议（两数）与官方依据

对每个已配置地区：向 Worker 领一把 `purpose=probe` 的临时 key（会话上限 15 s）→ 连 `ws_url` →
发 config + 约 200 ms 静音 + **空帧**（结束信号）→ 记录：
  ① **建连耗时**：`connect()` 返回 − 发起；② **往返**：发结束帧 → 收到 `finished: true`。
结束帧协议取自官方原文（2026-10-17 读 https://soniox.com/docs/stt/rt/real-time-transcription ）：
「Empty string signals end-of-audio to the server」；服务端处理完剩余音频回一条带
`"finished": true` 的响应再关连接。本机实测（test 通行码、美国区，2026-10-17）：
6/6 稳定拿到 finished，建连 ~0.33 s、往返 ~0.18 s（见交回报告）。
两数各劈一半也不比「connect + rtt」稳 —— 决策统一用**两者之和**（见 `decide`）。

## 决策与缓存（纯函数，表驱动判据从这里进）

`decide(ready=…)`：结果就绪、且某地区总耗时比默认地区快 ≥ `EDGE_MS`(80 ms) -> 改道；否则走默认。
`plan()`：把「开关 / 每版本计数 / 同网络 24 h 缓存」合成开课这一刻的静态决定。
`cache_get/cache_put`：缓存的键是**网络指纹**（`network_key()`：默认网关 MAC 的哈希，取不到退
Wi-Fi SSID 的哈希）—— **明文不落盘**。

## 「每个新版本前 3 节课」与开关

计数与缓存都住 `paths.region_probe_state()`（`~/.classlive/region-probe.json`）；版本对不上整个重置。
`CLASSLIVE_REGION_PROBE`：不设 = auto（每版本前 `FIRST_N` 节跑一次）；`0` = 关（不跑不改道）；
`1` = 强制每节都测。**决策与探测一起被这个门槛管住** —— 「只测前 3 节」之外的课不带 region。
探测失败的节**也算数**（上限就是上限；3 节都用完还没数 -> 之后照旧全默认）。

## 记录

`snapshot()` 原样进 `testmode` 的 `region_probe` 块与 `livemetrics` 的 `region_probe` 事件；
测试模式之外只写本地 live 日志（`CLASSLIVE_LIVE_LOG=1`），**不新增任何上传路径**。
`scripts/class_report.py` 会把它打出来。
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import subprocess
import threading
import time
from typing import Callable

import paths

#: 开关：`""`/未设 = auto、`"0"` = off、`"1"` = force。别的值一律当 auto（宽容方向：不放大成 force、也不默默关）。
ENV = "CLASSLIVE_REGION_PROBE"
#: 每个新版本只测前几节课。
FIRST_N = 3
#: 同网络结果的有效期（秒）。
CACHE_TTL_S = 24 * 3600
#: 改道门槛：别的地区总耗时比默认地区快 ≥ 这么多（毫秒）才换。
EDGE_MS = 80.0
#: 探测总预算（秒）：到点没量完的地区给 None，**绝不超**。
PROBE_TIMEOUT_S = 3.0
#: 探测发的静音时长（毫秒）与采样率（与生产同：pcm_s16le / 16 kHz 单声道）。
SILENCE_MS = 200
SR = 16000
#: 探测 config 的模型名（与 `stream_asr._config` 同一个；那两处是「生产发送」与「探测发送」的分工）。
MODEL = "stt-rt-v5"
#: 客户端能点名的地区（= Worker `/v1/soniox/key` 的 `region=` 合法值，见 soniox-core.REGIONS）。
REGIONS = ("us", "jp", "eu")
#: 缓存里最多留几个网络（防文件无限长大）。
CACHE_NETS = 8
#: 探测请求在 Worker 报的用途（Worker 事件里带 `:probe`，与真上课分开算）。
PURPOSE = "probe"


# ══════════════════════════════════════════════════════════════════════
# 纯逻辑（表驱动判据从这里进）
# ══════════════════════════════════════════════════════════════════════

def probe_mode(env_value: str | None) -> str:
    """开关值 -> `"off"` / `"auto"` / `"force"`。"""
    v = (env_value or "").strip().lower()
    if v in ("0", "off", "false", "no"):
        return "off"
    if v in ("1", "on", "true", "yes"):
        return "force"
    return "auto"


def state_for(state, version: str) -> dict:
    """读出来的状态文件 -> 本版本的状态。**版本对不上（或形状不对）整个重置**（计数与缓存都清）。

    ⚠️ 重置是全量的：「每个新版本前 3 节课」是一条实验纪律 —— 换了版本就从头开始采，
       不拿上一个版本的缓存接着做决定（那样新版本的第一节课就没有干净的数据）。
    """
    s = state if isinstance(state, dict) else {}
    if s.get("v") != version:
        return {"v": version, "n": 0, "cache": {}}
    n = s.get("n")
    cache = s.get("cache")
    return {"v": version,
            "n": n if isinstance(n, int) and not isinstance(n, bool) and n >= 0 else 0,
            "cache": cache if isinstance(cache, dict) else {}}


def should_run(mode_v: str, state: dict) -> tuple[bool, int | None]:
    """这一节要不要跑探测 -> `(跑不跑, 第几节)`。`ordinal` 没意义时是 None。"""
    n = int(state.get("n") or 0)
    if mode_v == "off":
        return False, None
    if mode_v == "force":
        return True, n + 1
    return (n < FIRST_N), (n + 1 if n < FIRST_N else None)


def cache_get(state: dict, net: str, now: float, ttl: float = CACHE_TTL_S) -> dict | None:
    """同网络、`ttl` 内的缓存条目；没有 / 过期 / 形状不对 -> None（顺序都按「没有」处理）。"""
    if not net:
        return None
    c = (state.get("cache") or {}).get(str(net))
    if not isinstance(c, dict):
        return None
    at = c.get("at")
    if isinstance(at, bool) or not isinstance(at, (int, float)):
        return None
    age = float(now) - float(at)
    if age < -60 or age > float(ttl):     # 过期 —— 或时钟倒着走（宁可不信）
        return None
    return c


def cache_put(state: dict, net: str, now: float, default: str, regions: dict,
              *, cap: int = CACHE_NETS) -> dict:
    """这次探测结果写进缓存（同网络盖旧的）。`net` / `default` 缺一个就不写（没键的缓存没用）。"""
    if not net or not default:
        return state
    st = dict(state)
    c = dict(st.get("cache") or {})
    c[str(net)] = {"at": float(now), "default": str(default), "regions": dict(regions or {})}
    if len(c) > cap:                      # 只留最近的 cap 个网络
        for k in sorted(c, key=lambda k: float((c.get(k) or {}).get("at") or 0))[:-cap]:
            c.pop(k, None)
    st["cache"] = c
    return st


def _total_ms(entry) -> float | None:
    """一个地区的可比数 = 建连 + 往返（两半缺一个 -> None，不猜）。"""
    if not isinstance(entry, dict):
        return None
    c, r = entry.get("connect_ms"), entry.get("rtt_ms")
    if isinstance(c, bool) or isinstance(r, bool):
        return None
    if not isinstance(c, (int, float)) or not isinstance(r, (int, float)):
        return None
    return float(c) + float(r)


def decide(*, ready: bool, default: str, regions: dict, edge_ms: float = EDGE_MS) -> dict:
    """地区探测结果 -> 本节选哪个区。纯函数（表驱动判据直接调它）。

    - `ready=False`：「结果在 Soniox 会话建立前就绪」不成立 -> 走默认（不带 region）；
    - 默认地区没有可用数（探测失败 / 超时）-> 走默认（但原因不同，报告里看得见）；
    - 没有**任何**别的地区有完整数 -> 走默认（只配了一个区就是这一档）；
    - 最好的别的地区比默认地区快 ≥ `edge_ms` -> 改它；否则走默认。

    回 `{selected, switched, reason}`。`reason` 取值：`not_ready` / `default_missing` /
    `no_other` / `close` / `faster`（有限枚举 —— 报告与判据都按它分类）。
    """
    if not ready:
        return {"selected": default, "switched": False, "reason": "not_ready"}
    d = _total_ms(regions.get(default)) if default else None
    if d is None:
        return {"selected": default, "switched": False, "reason": "default_missing"}
    best, bv = "", None
    for r in regions:
        if r == default:
            continue
        v = _total_ms(regions.get(r))
        # 并列取名字小的（确定性：同一份结果每次算出同一个地区）
        if v is not None and (bv is None or v < bv or (v == bv and r < best)):
            best, bv = r, v
    if not best:
        return {"selected": default, "switched": False, "reason": "no_other"}
    if d - bv >= float(edge_ms):
        return {"selected": best, "switched": True, "reason": "faster"}
    return {"selected": default, "switched": False, "reason": "close"}


def plan(*, state, version: str, net: str, env_value: str | None, now: float,
         ttl: float = CACHE_TTL_S) -> dict:
    """开课这一刻的静态决定（**不联网**）。回 `{run, ordinal, mode, net, default, selected, switched, reason, cached}`。

    ⚠️ `selected` 只可能来自缓存（本次探测等不起，见文件头）；没缓存 -> `""`（= 不带 region）。
    """
    st = state_for(state, version)
    mode_v = probe_mode(env_value)
    run, ordinal = should_run(mode_v, st)
    raw = (st.get("cache") or {}).get(str(net)) if net else None
    cached = cache_get(st, net, now, ttl)
    if run and cached:
        d = decide(ready=True, default=str(cached.get("default") or ""), regions=cached.get("regions") or {})
    elif run:
        d = {"selected": "", "switched": False, "reason": "cache_stale" if isinstance(raw, dict) else "not_ready"}
    else:
        d = {"selected": "", "switched": False, "reason": mode_v if mode_v == "off" else "done"}
    return {"run": run, "ordinal": ordinal, "mode": mode_v, "net": str(net or ""),
            "default": (str(cached.get("default") or "") or None) if cached else None,
            "selected": str(d["selected"] or ""), "switched": bool(d["switched"]),
            "reason": str(d["reason"]), "cached": bool(cached)}


# ══════════════════════════════════════════════════════════════════════
# 网络指纹（只回哈希；明文不落盘）
# ══════════════════════════════════════════════════════════════════════

def network_key(*, run: Callable = subprocess.run, timeout: float = 0.8,
                budget: float = 1.0) -> str:
    """「同一个网络」的指纹：默认网关 MAC 优先，取不到退 Wi-Fi SSID —— 一律 sha256 前 16 位。

    ⚠️ **只回哈希**：明文（MAC / SSID）不落盘、不进日志（`region-probe.json` 里只见哈希）。
    ⚠️ 它跑在**开课启动路径**上（构造 Router 时同步调）：每条命令 ≤ `timeout`、
       整体 ≤ `budget`（三条都挂也就 1 s 走完，绝不把开课吊在这里）。
    永远不抛：取不到（没有默认路由 / 命令被拒 / 超时）-> `""`，调用方按「没有网络键」处理
    （缓存不读也不写，但探测照跑 —— 数据还是采得到）。
    """
    t0 = time.monotonic()

    def out(cmd: list[str]) -> str:
        left = budget - (time.monotonic() - t0)
        if left <= 0:
            return ""
        try:
            return run(cmd, capture_output=True, text=True, timeout=min(timeout, left)).stdout or ""
        except Exception:                                  # noqa: BLE001
            return ""

    gw = iface = ""
    for line in out(["route", "-n", "get", "default"]).splitlines():
        s = line.strip()
        if s.startswith("gateway:"):
            gw = s.split(":", 1)[1].strip()
        elif s.startswith("interface:"):
            iface = s.split(":", 1)[1].strip()
    if gw:
        a = out(["arp", "-n", gw])
        m = re.search(r"at\s+([0-9a-f]{1,2}(?::[0-9a-f]{1,2}){5})\s+on\b", a, re.I)
        if m and "incomplete" not in a.lower():
            return hashlib.sha256(("gw-mac:" + m.group(1).lower()).encode()).hexdigest()[:16]
    if iface:
        n = out(["networksetup", "-getairportnetwork", iface])
        m = re.search(r"Current Wi-Fi Network:\s*(.+?)\s*$", n, re.M)
        if m:
            return hashlib.sha256(("ssid:" + m.group(1)).encode()).hexdigest()[:16]
    return ""


# ══════════════════════════════════════════════════════════════════════
# 传输（真实现；判据注入替身，不碰网络）
# ══════════════════════════════════════════════════════════════════════

def fetch_probe_key(endpoint: str, passcode: str, region: str, *, timeout: float = 2.0) -> dict:
    """向 Worker 领一把 `purpose=probe` 的临时 key。回 `{api_key, ws_url, region}`；失败抛异常。

    `region=""` = 不带 region（问「默认地区」）。头与 `stream_asr._KeyClient` 同源。
    """
    import httpx

    import asr_endpoint
    import final_asr

    body: dict = {"purpose": PURPOSE}
    if region:
        body["region"] = region
    r = httpx.post(endpoint, headers={
        "Authorization": f"Bearer {asr_endpoint.token(passcode)}",
        "X-ClassLive-Install": final_asr.install_id(),
        "X-ClassLive-Version": asr_endpoint.version_header(),
    }, json=body, timeout=max(0.1, float(timeout)))
    try:
        js = r.json()
    except ValueError:
        js = None
    if r.status_code != 200 or not isinstance(js, dict) or not js.get("api_key"):
        raise RuntimeError(f"probe key http {r.status_code}")
    return {"api_key": str(js["api_key"]), "ws_url": str(js.get("ws_url") or ""),
            "region": str(js.get("region") or "")}


def _ws_probe(ws_url: str, api_key: str, budget_s: float) -> dict:
    """连 ws → config → 200 ms 静音 → 空帧 → 等 `finished`。回 `{connect_ms, rtt_ms, error}`。

    结束帧协议见文件头（官方原文 + 本机实测）；`budget_s` = 从现在到硬截止还剩多少。
    """
    import websockets.sync.client as wsc
    out: dict = {"connect_ms": None, "rtt_ms": None, "error": ""}
    t0 = time.monotonic()
    # ⚠️ `close_timeout` 特意小：真机实测服务端在 `finished` 之后**自己先关**（官方示例也这么写），
    #    这时客户端的关闭握手要空等满 `close_timeout`（1.0 s -> 探测量到 1.5 s）。探测不是会话，
    #    收完 `finished` 就可以走 —— 0.2 s 关不掉就放弃（连接随线程回收）。
    ws = wsc.connect(ws_url, additional_headers={"Authorization": f"Bearer {api_key}"},
                     open_timeout=max(0.2, budget_s), close_timeout=0.2, ping_interval=20.0,
                     legacy=True)
    try:
        out["connect_ms"] = round((time.monotonic() - t0) * 1000, 1)
        ws.send(json.dumps({"model": MODEL, "audio_format": "pcm_s16le",
                            "sample_rate": SR, "num_channels": 1}))
        frame = b"\x00" * int(SR * SILENCE_MS / 1000 / 2) * 2      # 两帧各 100 ms
        ws.send(frame)
        ws.send(frame)
        t_end = time.monotonic()
        ws.send("")                                                # 空帧 = 官方结束信号
        while True:
            remaining = budget_s - (time.monotonic() - t_end)
            if remaining <= 0:
                out["error"] = "timeout"
                break
            try:
                raw = ws.recv(timeout=remaining)
            except TimeoutError:
                out["error"] = "timeout"
                break
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(msg, dict):
                continue
            if msg.get("finished"):
                out["rtt_ms"] = round((time.monotonic() - t_end) * 1000, 1)
                break
            et = msg.get("error_type") or msg.get("error_code")
            if et:
                out["error"] = f"upstream: {et}"
                break
    except Exception as e:                                         # noqa: BLE001
        if not out["error"] and out["rtt_ms"] is None:
            out["error"] = f"{type(e).__name__}"
    finally:
        try:
            ws.close()
        except Exception:                                          # noqa: BLE001
            pass
    return out


def run_probe(*, endpoint: str, passcode: str, fetch_key: Callable | None = None,
              ws_probe: Callable | None = None, timeout_s: float = PROBE_TIMEOUT_S,
              clock: Callable[[], float] = time.monotonic) -> dict:
    """并行探测所有已配置地区。**总耗时 ≤ `timeout_s`**（硬截止；没量完的区给 None + 错误名）。

    两波（都在这一个预算里）：
      ① 不带 region 问一次 Worker —— 这条回的 `region` 就是**默认地区**，顺便量它；
      ② 其余地区并行问；**没配的区会回默认地区** —— 按**回的 region** 去重：
         同一个地区永远只量一次、只开一条流（旧 Worker 不认 region，这一条也天然成立）。
    回 `{default, regions: {区: {connect_ms, rtt_ms, error}}, ms, timeout}`。
    """
    t0 = clock()
    deadline = t0 + float(timeout_s)
    got: dict = {}
    lock = threading.Lock()
    fetch = fetch_key or (lambda region, budget: fetch_probe_key(endpoint, passcode, region, timeout=budget))
    probe_ws = ws_probe or _ws_probe

    def attempt(ask: str) -> str:
        """问一次 Worker（`ask=""` 问默认）并按**回的 region**去重、量到。返回量到的地区（"" = 放弃）。"""
        try:
            key = fetch(ask, max(0.05, deadline - clock()))
        except Exception:                                          # noqa: BLE001
            return ""
        reg = str((key or {}).get("region") or "")
        if reg not in REGIONS:
            return ""
        with lock:
            if reg in got:
                return reg
            got[reg] = {"connect_ms": None, "rtt_ms": None, "error": "pending"}
        try:
            m = probe_ws(str(key.get("ws_url") or ""), str(key.get("api_key") or ""),
                         max(0.05, deadline - clock())) or {}
            res = {"connect_ms": m.get("connect_ms"), "rtt_ms": m.get("rtt_ms"),
                   "error": str(m.get("error") or "")}
        except Exception as e:                                     # noqa: BLE001
            res = {"connect_ms": None, "rtt_ms": None, "error": type(e).__name__}
        with lock:
            got[reg] = res
        return reg

    default = attempt("")                                          # ① 默认地区（同步：它决定后面问哪些）
    if default:
        others = [r for r in REGIONS if r != default]
        ths = [threading.Thread(target=attempt, args=(r,), name=f"region-probe-{r}", daemon=True)
               for r in others]
        for th in ths:
            th.start()
        for th in ths:
            th.join(timeout=max(0.0, deadline - clock()))          # 到点就放（线程是守护的）
    with lock:
        regs = {k: dict(v) for k, v in got.items()}
    for v in regs.values():
        if v.get("error") == "pending":                            # 到点了还没写回的：当超时
            v["error"] = "timeout"
    timeout = bool(regs) and any(v["connect_ms"] is None or v["rtt_ms"] is None for v in regs.values())
    return {"default": default or None, "regions": regs,
            "ms": round((clock() - t0) * 1000), "timeout": timeout or not regs}


# ══════════════════════════════════════════════════════════════════════
# 对外那一个小接口
# ══════════════════════════════════════════════════════════════════════

class Router:
    """开课这一刻的地区路由。`main.py` 只碰 3 个成员：`region` / `start()` / `snapshot()`。

    构造 = 静态决定（读状态文件 + 网络指纹 + 可能命中缓存）：**不联网**，毫秒级，永不抛。
    `start(on_done)` 起后台探测（该跑才跑）并返回；`on_done(snapshot)` 恰好调一次。
    `region` = `""`（不带，Worker 默认）或 `"jp"`/`"eu"`/…（命中了「比默认快 ≥ 80 ms」的缓存）。
    """

    def __init__(self, *, passcode: str, endpoint: str, version: str | None = None,
                 state_path=None, env_value: str | None = None,
                 clock: Callable[[], float] = time.time, net_key_fn: Callable | None = None,
                 fetch_key: Callable | None = None, ws_probe: Callable | None = None,
                 timeout_s: float = PROBE_TIMEOUT_S) -> None:
        self._passcode = str(passcode or "")
        self._endpoint = str(endpoint or "")
        if version is None:
            from asr_endpoint import version_header
            version = version_header()
        self._version = str(version or "")
        self._state_path = pathlib.Path(state_path) if state_path else paths.region_probe_state()
        self._clock = clock
        self._timeout_s = float(timeout_s)
        self._fetch_key = fetch_key
        self._ws_probe = ws_probe
        self._probe: dict | None = None
        self._state = state_for(self._load(), self._version)
        try:
            self._net = str((net_key_fn or network_key)() or "")
        except Exception:                                          # noqa: BLE001
            self._net = ""
        self._plan = plan(state=self._state, version=self._version, net=self._net,
                          env_value=(env_value if env_value is not None
                                     else os.environ.get(ENV, "")),
                          now=self._clock())

    # ---- 主程序碰的三个成员

    @property
    def region(self) -> str:
        """领正式 key 时带的 `region=`。**只有真改道才非空** —— 没缓存 / 不比默认快 / 关了，都走 Worker 默认。"""
        return self._plan["selected"] if self._plan["switched"] else ""

    def start(self, on_done: Callable[[dict], None] | None = None) -> None:
        """起后台探测（该跑才跑）。**立刻返回** —— 绝不推迟开课。

        ⚠️ `on_done` 在**两种路**里都恰好调一次：该跑 -> 探测线程收尾时；不该跑（off / 计数用完）
           -> 这里同步调一次。报告块因此每一节都有（没跑的原因也在里面）。
        """
        if not self._plan["run"]:
            self._finish(on_done)
            return
        th = threading.Thread(target=self._work, args=(on_done,), name="region-probe", daemon=True)
        th.start()

    def snapshot(self) -> dict:
        """报告块 / live 事件。任何时刻可调（探测在跑时 `default`/`regions` 还没写回来 = 空）。

        `default` 的取值：探测回来就以**本次量的**为准（最鲜）；否则用决策那一刻缓存里的。
        `selected`/`switched`/`reason`/`cached` 说的是**本节怎么选**的（决策）。
        """
        p = self._plan
        probe = self._probe
        default = p["default"]
        if probe is not None and probe.get("default"):
            default = probe.get("default")
        snap = {"version": self._version, "mode": p["mode"], "run": bool(p["run"]),
                "ordinal": p["ordinal"], "net": p["net"],
                "default": default, "selected": self.region,
                "switched": bool(p["switched"]), "reason": p["reason"], "cached": bool(p["cached"])}
        if probe is not None:
            snap["regions"] = probe.get("regions") or {}
            snap["ms"] = probe.get("ms")
            snap["timeout"] = bool(probe.get("timeout"))
        return snap

    # ---- 内部

    def _load(self) -> dict:
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except Exception:                                          # noqa: BLE001
            return {}

    def _persist(self) -> None:
        """计数 +1、命中默认地区就把结果写缓存。**一次写盘**（失败吞掉：探测数据丢掉，不该影响课）。

        ⚠️ 计数按**跑过就算**（失败也 +1）：上限就是上限（最多 3 次探测），
           不然网络一直挂会变成「无限次重试」，就不是「前 3 节课」了。
        """
        st = dict(self._state)
        st["n"] = int(st.get("n") or 0) + 1
        probe = self._probe or {}
        if probe.get("default"):
            st = cache_put(st, self._net, self._clock(), str(probe["default"]),
                           probe.get("regions") or {})
        self._state = st
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state_path.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")

    def _work(self, on_done: Callable[[dict], None] | None) -> None:
        try:
            self._probe = run_probe(endpoint=self._endpoint, passcode=self._passcode,
                                    fetch_key=self._fetch_key, ws_probe=self._ws_probe,
                                    timeout_s=self._timeout_s)
        except Exception:                                          # noqa: BLE001
            self._probe = {"default": None, "regions": {}, "ms": None, "timeout": True}
        try:
            self._persist()
        except Exception:                                          # noqa: BLE001
            pass
        self._finish(on_done)

    def _finish(self, on_done: Callable[[dict], None] | None) -> None:
        if on_done is None:
            return
        try:
            on_done(self.snapshot())
        except Exception:                                          # noqa: BLE001
            pass
