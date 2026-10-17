#!/usr/bin/env python3
"""`region_route.py` 的判据 —— **全离线**（假领 key / 假 WS 探测），毫秒级。

    ClassLive.app/Contents/MacOS/python tests/test_region_route.py

钉住的东西（对应任务书的表驱动清单）：
- `decide`：差 79 ms 不改 / 差 80 ms 改；超时（缺一半数）；只配一个地区；多地区取最快。
- 缓存：24 h 内命中、过了 24 h 不认、时钟倒着走不认、坏形状不认。
- `plan`：每版本前 3 节（第 3 节是最后一个能跑的）、第 4 节起 `done`；版本变了计数与缓存都重置；
  `CLASSLIVE_REGION_PROBE=0` 关 / `1` 强制。
- `network_key`：**只回哈希**（明文 MAC / SSID 不许出现在返回值里）；取不到回 ""。
- `run_probe`：按**回的 region** 去重（没配的区回默认 -> 不重复量）、阶段一失败就不跑阶段二、硬截止。
- `Router`：第 1 节没缓存（`not_ready`，探测后台跑 + 写状态）；第 2 节吃缓存改道；`on_done` 恰好一次。

⚠️ 不碰网络、不碰真 `~/.classlive/`（HOME 已隔离 + 状态文件全走 tmp 路径）。
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
import threading
import time
import types

_TMP = tempfile.TemporaryDirectory(prefix="cl_region_route_test_")
os.environ["HOME"] = _TMP.name
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import region_route as RR  # noqa: E402

FAILED: list[str] = []


def check(name: str, fn) -> None:
    t0 = time.monotonic()
    try:
        fn()
        print(f"  ✅ {name}  ({time.monotonic() - t0:.2f}s)")
    except Exception as e:                          # noqa: BLE001
        FAILED.append(name)
        print(f"  ❌ {name}: {type(e).__name__}: {e}")


def r(c, t):
    return {"connect_ms": c, "rtt_ms": t, "error": ""}


NOW = 1_800_000_000.0


def _fakes():
    """us 慢 / jp 快（总差 120 ms）/ eu 没配（回默认）。回 (calls, fetch, ws)。"""
    calls = {"fetch": [], "ws": []}

    def fetch(region, budget):
        calls["fetch"].append(region)
        reg = {"": "us", "us": "us", "jp": "jp", "eu": "us"}.get(region, "us")   # eu 没配 -> 回默认
        return {"api_key": "k", "ws_url": f"w{reg}", "region": reg}

    def ws(ws_url, key, budget):
        calls["ws"].append(ws_url)
        if ws_url == "wjp":
            return {"connect_ms": 220.0, "rtt_ms": 160.0, "error": ""}
        return {"connect_ms": 300.0, "rtt_ms": 200.0, "error": ""}
    return calls, fetch, ws


# ══════════════════════════════════════════════════════════════════════
# decide（表驱动）
# ══════════════════════════════════════════════════════════════════════

def t_decide_edge_79_80() -> None:
    # 默认 us 总 480；jp 总 401 -> 快 79 -> 不改
    d = RR.decide(ready=True, default="us", regions={"us": r(300, 180), "jp": r(220, 181)})
    assert (d["selected"], d["switched"], d["reason"]) == ("us", False, "close"), d
    # jp 总 400 -> 快 80 -> 改
    d = RR.decide(ready=True, default="us", regions={"us": r(300, 180), "jp": r(220, 180)})
    assert (d["selected"], d["switched"], d["reason"]) == ("jp", True, "faster"), d
    # 两边都缺一半数（超时）时不比
    d = RR.decide(ready=True, default="us",
                  regions={"us": r(300, 180), "jp": {"connect_ms": 100.0, "rtt_ms": None, "error": "timeout"}})
    assert (d["selected"], d["switched"], d["reason"]) == ("us", False, "no_other"), d


def t_decide_not_ready_timeout_single() -> None:
    # 结果没在会话建立前就绪 -> 走默认（哪怕数已经拿到了）
    d = RR.decide(ready=False, default="", regions={"us": r(300, 180), "jp": r(100, 100)})
    assert (d["selected"], d["switched"], d["reason"]) == ("", False, "not_ready"), d
    # 默认地区自己没数 -> default_missing
    d = RR.decide(ready=True, default="us", regions={"us": {"connect_ms": None, "rtt_ms": None, "error": "timeout"},
                                                     "jp": r(100, 100)})
    assert (d["selected"], d["reason"]) == ("us", "default_missing"), d
    # 只配一个地区（只有默认）-> no_other
    d = RR.decide(ready=True, default="us", regions={"us": r(300, 180)})
    assert (d["selected"], d["switched"], d["reason"]) == ("us", False, "no_other"), d
    # 三个地区 -> 取最快；并列取名字小的（确定性）
    d = RR.decide(ready=True, default="us", regions={"us": r(300, 180), "jp": r(250, 180), "eu": r(150, 150)})
    assert (d["selected"], d["switched"], d["reason"]) == ("eu", True, "faster"), d
    d = RR.decide(ready=True, default="us", regions={"us": r(300, 180), "jp": r(100, 100), "eu": r(100, 100)})
    assert d["selected"] == "eu", d          # "eu" < "jp"
    # 布尔不是数字：True 不许被当成 1 ms
    d = RR.decide(ready=True, default="us", regions={"us": r(300, 180), "jp": {"connect_ms": True, "rtt_ms": True}})
    assert (d["selected"], d["reason"]) == ("us", "no_other"), d


# ══════════════════════════════════════════════════════════════════════
# 缓存 / 计数 / 版本
# ══════════════════════════════════════════════════════════════════════

def t_cache_hit_expired() -> None:
    st = {"v": "3.9.7", "n": 0,
          "cache": {"n1": {"at": NOW - 23 * 3600, "default": "us", "regions": {"us": r(1, 1)}}}}
    assert RR.cache_get(st, "n1", NOW) is not None
    st["cache"]["n1"]["at"] = NOW - 25 * 3600
    assert RR.cache_get(st, "n1", NOW) is None, "过期了还认"
    st["cache"]["n1"]["at"] = NOW + 10 * 3600
    assert RR.cache_get(st, "n1", NOW) is None, "时钟倒着走（未来的缓存）不认"
    assert RR.cache_get(st, "other", NOW) is None
    assert RR.cache_get(st, "", NOW) is None, "没有网络键 -> 不读缓存"
    assert RR.cache_get({"cache": {"x": {"at": "bad"}}}, "x", NOW) is None
    # cache_put：没键 / 没默认地区都不写；写了能读回来
    assert RR.cache_put(st, "", NOW, "us", {}) is st
    assert RR.cache_put(st, "n2", NOW, "", {}) is st
    st2 = RR.cache_put({"v": "3.9.7", "n": 0, "cache": {}}, "n2", NOW, "us",
                       {"us": r(1, 2), "jp": r(3, 4)})
    got = RR.cache_get(st2, "n2", NOW)
    assert got["default"] == "us" and got["regions"]["jp"] == r(3, 4), got


def t_state_version_reset() -> None:
    old = {"v": "3.9.6", "n": 3, "cache": {"n1": {"at": NOW, "default": "us", "regions": {}}}}
    st = RR.state_for(old, "3.9.7")
    assert st == {"v": "3.9.7", "n": 0, "cache": {}}, "版本变了要整个重置"
    same = RR.state_for(old, "3.9.6")
    assert same["n"] == 3 and "n1" in same["cache"]
    assert RR.state_for("坏形状", "3.9.7") == {"v": "3.9.7", "n": 0, "cache": {}}
    assert RR.state_for({"v": "3.9.7", "n": -2, "cache": []}, "3.9.7")["n"] == 0
    assert RR.state_for({"v": "3.9.7", "n": True, "cache": []}, "3.9.7")["n"] == 0, "bool 不是计数"


def t_plan_counter_version_env() -> None:
    regions = {"us": r(300, 200), "jp": r(200, 150)}
    cached = {"v": "3.9.7", "n": 1, "cache": {"n1": {"at": NOW - 60, "default": "us", "regions": regions}}}
    for n, want_run, want_ord in ((0, True, 1), (1, True, 2), (2, True, 3), (3, False, None), (9, False, None)):
        p = RR.plan(state={**cached, "n": n}, version="3.9.7", net="n1", env_value="", now=NOW)
        assert (p["run"], p["ordinal"]) == (want_run, want_ord), (n, p)
    p = RR.plan(state=cached, version="3.9.7", net="n1", env_value="", now=NOW)
    assert (p["selected"], p["switched"], p["reason"], p["cached"]) == ("jp", True, "faster", True), p
    # 版本变：计数与缓存都重置 -> 第 1 节、没缓存
    p = RR.plan(state={"v": "3.9.6", "n": 3, "cache": cached["cache"]},
                version="3.9.7", net="n1", env_value="", now=NOW)
    assert (p["run"], p["ordinal"], p["cached"], p["selected"], p["reason"]) == (True, 1, False, "", "not_ready"), p
    # 缓存过期：探测照跑、本节不带 region
    p = RR.plan(state={**cached, "cache": {"n1": {"at": NOW - 25 * 3600, "default": "us", "regions": regions}}},
                version="3.9.7", net="n1", env_value="", now=NOW)
    assert (p["selected"], p["reason"]) == ("", "cache_stale"), p
    # 没网络键：不读缓存
    p = RR.plan(state=cached, version="3.9.7", net="", env_value="", now=NOW)
    assert (p["cached"], p["reason"]) == (False, "not_ready"), p
    # 开关（计数放在前 3 节内，单独看开关本身）
    for v, want_mode, want_run in (("0", "off", False), ("off", "off", False), ("1", "force", True),
                                   ("", "auto", True), ("x", "auto", True)):
        p = RR.plan(state={**cached, "n": 1}, version="3.9.7", net="n1", env_value=v, now=NOW)
        assert (p["mode"], p["run"]) == (want_mode, want_run), (v, p)
    p = RR.plan(state={**cached, "n": 99}, version="3.9.7", net="n1", env_value="1", now=NOW)
    assert p["run"] and p["ordinal"] == 100, "force 不看计数"
    p = RR.plan(state={**cached, "n": 3}, version="3.9.7", net="n1", env_value="0", now=NOW)
    assert p["reason"] == "off" and p["selected"] == ""


# ══════════════════════════════════════════════════════════════════════
# 网络指纹
# ══════════════════════════════════════════════════════════════════════

def t_network_key_hashes_only() -> None:
    def run(cmd, **kw):
        if cmd[0] == "route":
            return types.SimpleNamespace(stdout="   gateway: 10.20.64.1\n  interface: en0\n")
        if cmd[0] == "arp":
            return types.SimpleNamespace(stdout="? (10.20.64.1) at aa:bb:cc:dd:ee:ff on en0 ifscope [ethernet]\n")
        return types.SimpleNamespace(stdout="")

    k1 = RR.network_key(run=run)
    k2 = RR.network_key(run=run)
    assert k1 and k1 == k2 and len(k1) == 16, (k1, k2)
    for secret in ("aa:bb:cc:dd:ee:ff", "10.20.64.1"):
        assert secret not in k1, f"明文 {secret} 漏进了指纹"
    # SSID 兜底（网关 MAC 拿不到时）
    def run_ssid(cmd, **kw):
        if cmd[0] == "route":
            return types.SimpleNamespace(stdout="  interface: en0\n")
        if cmd[0] == "networksetup":
            return types.SimpleNamespace(stdout="Current Wi-Fi Network: MyHome\n")
        return types.SimpleNamespace(stdout="")
    k3 = RR.network_key(run=run_ssid)
    assert k3 and "MyHome" not in k3, k3
    assert k3 != k1
    # arp 卡在 incomplete -> 不当 MAC
    def run_inc(cmd, **kw):
        if cmd[0] == "route":
            return types.SimpleNamespace(stdout="  gateway: 10.0.0.1\n  interface: en0\n")
        return types.SimpleNamespace(stdout="? (10.0.0.1) at (incomplete) on en0 ifscope\n")
    assert RR.network_key(run=run_inc) == ""
    # 命令全挂 / 超时 -> ""
    def boom(*a, **k):
        raise RuntimeError("nope")
    assert RR.network_key(run=boom) == ""


# ══════════════════════════════════════════════════════════════════════
# run_probe
# ══════════════════════════════════════════════════════════════════════

def t_probe_dedup_and_measures() -> None:
    calls, fetch, ws = _fakes()
    out = RR.run_probe(endpoint="x", passcode="p", fetch_key=fetch, ws_probe=ws, timeout_s=2.0)
    assert out["default"] == "us", out
    assert set(out["regions"]) == {"us", "jp"}, "eu 没配（回默认）-> 不该出现，更不该重量一次"
    assert calls["ws"].count("wus") == 1 and calls["ws"].count("wjp") == 1, calls
    assert out["regions"]["jp"]["connect_ms"] == 220.0 and out["regions"]["jp"]["rtt_ms"] == 160.0
    assert out["regions"]["us"]["connect_ms"] == 300.0
    assert out["timeout"] is False and isinstance(out["ms"], int), out
    # 阶段一就失败 -> 不跑阶段二（省 key）
    def fetch_down(region, budget):
        raise RuntimeError("worker down")
    calls2 = {"n": 0}
    def ws_never(*a):
        calls2["n"] += 1
        return {"connect_ms": 1.0, "rtt_ms": 1.0, "error": ""}
    out2 = RR.run_probe(endpoint="x", passcode="p", fetch_key=fetch_down, ws_probe=ws_never, timeout_s=2.0)
    assert out2["default"] is None and out2["regions"] == {} and calls2["n"] == 0 and out2["timeout"] is True


def t_probe_hard_deadline() -> None:
    def fetch(region, budget):
        if region == "":
            return {"api_key": "k", "ws_url": "wus", "region": "us"}
        time.sleep(0.4)                          # 阶段二问 Worker 就卡住
        return {"api_key": "k", "ws_url": "wjp", "region": "jp"}

    def ws(ws_url, key, budget):
        if ws_url == "wjp":
            time.sleep(1.5)                      # 而且连上以后也卡住（守护线程到点被放掉）
            return {"connect_ms": 1.0, "rtt_ms": 1.0, "error": ""}
        return {"connect_ms": 10.0, "rtt_ms": 5.0, "error": ""}
    t0 = time.monotonic()
    out = RR.run_probe(endpoint="x", passcode="p", fetch_key=fetch, ws_probe=ws, timeout_s=0.5)
    dt = time.monotonic() - t0
    assert dt < 1.2, f"总截止没生效：{dt:.2f}s"
    assert out["default"] == "us" and out["timeout"] is True
    assert out["regions"]["jp"]["rtt_ms"] is None and out["regions"]["jp"]["error"], out


# ══════════════════════════════════════════════════════════════════════
# Router（前 3 节的故事）
# ══════════════════════════════════════════════════════════════════════

def t_router_first_three_classes() -> None:
    calls, fetch, ws = _fakes()
    state = pathlib.Path(_TMP.name) / "rp-flow.json"

    def mk(**kw):
        return RR.Router(passcode="cl-test", endpoint="http://x/v1/soniox/key", version="3.9.7",
                         state_path=state, clock=lambda: NOW, net_key_fn=lambda: "netA",
                         fetch_key=fetch, ws_probe=ws, timeout_s=2.0, **kw)

    snaps: list = []
    done = threading.Event()

    def cb(s):
        snaps.append(s)
        done.set()

    # 1️⃣ 没缓存：本节不带 region；探测后台跑；跑完写状态（n=1 + 缓存）
    r1 = mk(env_value="")
    assert r1.region == "", r1.snapshot()
    assert r1.snapshot()["reason"] == "not_ready"
    r1.start(on_done=cb)
    assert done.wait(3.0), "探测没在等它"
    s1 = snaps[0]
    assert s1["run"] is True and s1["ordinal"] == 1 and s1["default"] == "us", s1
    assert s1["selected"] == "" and s1["switched"] is False and s1["cached"] is False
    assert set(s1["regions"]) == {"us", "jp"} and s1["regions"]["jp"]["rtt_ms"] == 160.0
    st = json.loads(state.read_text(encoding="utf-8"))
    assert st["v"] == "3.9.7" and st["n"] == 1 and "netA" in st["cache"], st
    # 2️⃣ 缓存命中（jp 比默认快 120 ms）-> 本节领正式 key 带 region=jp
    snaps.clear(); done.clear()
    r2 = mk(env_value="")
    assert r2.region == "jp", r2.snapshot()
    s2 = r2.snapshot()
    assert (s2["reason"], s2["cached"], s2["ordinal"]) == ("faster", True, 2), s2
    r2.start(on_done=cb)
    assert done.wait(3.0)
    assert snaps[0]["selected"] == "jp" and snaps[0]["switched"] is True
    assert json.loads(state.read_text(encoding="utf-8"))["n"] == 2
    # 3️⃣ 最后一节（第 3 节）
    done.clear()
    r3 = mk(env_value="")
    assert r3.region == "jp"
    r3.start(on_done=cb)
    assert done.wait(3.0)
    assert json.loads(state.read_text(encoding="utf-8"))["n"] == 3
    # 4️⃣ 用完：不探测、不带 region、on_done **同步**调（报告块照样有）
    n_before = len(calls["fetch"])
    done.clear(); snaps.clear()
    r4 = mk(env_value="")
    assert r4.region == "" and r4.snapshot()["reason"] == "done"
    r4.start(on_done=cb)
    assert snaps and snaps[0]["run"] is False, "第 4 节 on_done 要同步调"
    assert len(calls["fetch"]) == n_before, "第 4 节不该再探测"


def t_router_off_force_and_corrupt_state() -> None:
    calls, fetch, ws = _fakes()
    state = pathlib.Path(_TMP.name) / "rp-off.json"

    def mk(**kw):
        return RR.Router(passcode="p", endpoint="e", version="3.9.7", state_path=state,
                         clock=lambda: NOW, net_key_fn=lambda: "netB",
                         fetch_key=fetch, ws_probe=ws, timeout_s=2.0, **kw)

    snaps: list = []
    r = mk(env_value="0")
    assert r.region == "" and r.snapshot()["reason"] == "off"
    r.start(on_done=snaps.append)
    assert snaps and snaps[0]["run"] is False and not calls["fetch"]
    assert not state.exists(), "off 不该动状态文件（连计数都不该记）"
    # force：不看计数
    done = threading.Event()
    snaps2: list = []
    r = mk(env_value="1")
    assert r.snapshot()["mode"] == "force" and r.snapshot()["run"] is True
    r.start(on_done=lambda s: (snaps2.append(s), done.set()))
    assert done.wait(3.0)
    assert snaps2[0]["ordinal"] == 1 and snaps2[0]["mode"] == "force"
    # 状态文件坏了 / 形状不对：构造不许抛，按没缓存处理
    state.write_text("{oops", encoding="utf-8")
    r = mk(env_value="")
    assert r.snapshot()["reason"] in ("not_ready", "cache_stale") and r.region == ""


def main() -> int:
    print("region_route 判据（全部离线）")
    for name, fn in [
        ("decide: 差 79 不改 / 差 80 改 / 超时不比", t_decide_edge_79_80),
        ("decide: not_ready / 默认缺数 / 只配一区 / 多区取最快", t_decide_not_ready_timeout_single),
        ("cache: 24 h 命中、过期不认、倒时钟不认、形状坏不认", t_cache_hit_expired),
        ("state: 版本变了计数与缓存重置", t_state_version_reset),
        ("plan: 前 3 节 / 第 4 节 done / 版本重置 / 开关 auto·off·force", t_plan_counter_version_env),
        ("network_key: 只回哈希（MAC 优先、SSID 兜底、拿不到回空）", t_network_key_hashes_only),
        ("run_probe: 按回的 region 去重、阶段一失败即止", t_probe_dedup_and_measures),
        ("run_probe: 硬截止（阶段二卡住也不超）", t_probe_hard_deadline),
        ("Router: 第 1 节起 3 节的完整故事（缓存/改道/on_done）", t_router_first_three_classes),
        ("Router: off / force / 状态文件坏掉不抛", t_router_off_force_and_corrupt_state),
    ]:
        check(name, fn)
    print(f"\n{'❌ 失败 ' + str(len(FAILED)) + '：' + '、'.join(FAILED) if FAILED else '✅ 全部通过'}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
