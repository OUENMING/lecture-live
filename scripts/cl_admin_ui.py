#!/usr/bin/env python3
"""通行码管理面板 —— 本机网页（`docs/PLAN-passkey.md` §3 扩展功能 1）。

    ClassLive.app/Contents/MacOS/python scripts/cl_admin_ui.py            # → http://127.0.0.1:8770
    ClassLive.app/Contents/MacOS/python scripts/cl_admin_ui.py --port 8800 --open

为什么是**本机服务**而不是挂在 Worker 上：
  · Worker **没有任何 CORS 头**（全仓 grep 零命中），浏览器直接打 `/v1/admin/*` 会被拦；
  · 挂上去就得把管理令牌存进浏览器（localStorage），并把管理面暴露在公开域名上。
本服务**只 bind 127.0.0.1**；令牌只在 Python 侧读、只在服务端加进请求头 —— **从不进浏览器、从不进 HTML**。

⚠️ 三件事：
  1. 页面上的数字全部来自真的 `asr.bldcam.page`（只读的 list/stats/quota，加你点的管理操作）。
  2. 「估算花费」是**本机乘出来的，不是账**。价目表可改，页面上标了价格日期。
  3. 面板**只记数字**（`PLAN-passkey.md` §4）：看不到任何课堂文本、音频，也看不到完整码除非你展开。

判据在 `tests/test_cl_admin_ui.py`（只测转发层与花费计算，用本机假上游，不联网）。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import cl_admin                                                      # noqa: E402

#: 只放行这几个名字 —— 别把 `/api/<任意>` 直接拼进 Worker 的 URL。
ALLOWED = {"add", "list", "stats", "revoke", "unrevoke", "renew", "limit", "quota", "delete", "minutes", "devices"}

#: 自定义头：逼浏览器先发预检。我们**不回** CORS 头，所以预检必失败、真请求不会发出去。
#: 没有这一条，任意网站都能用 `<form>` / `fetch` 打 http://127.0.0.1:8770/api/revoke 把你的码吊销掉。
GATE_HEADER = "X-ClassLive-Admin"

#: 第二道闸：**Host 头必须是环回**。挡的是 DNS rebinding —— 恶意网站把自己的域名临时解析到
#: 127.0.0.1，页面就成了**同源**，自定义头不再触发预检，上面那道闸就形同虚设。
#: 浏览器发的 Host 是**那个域名**（改不了），所以只认 127.0.0.1 / localhost。
LOOPBACK_HOSTS = ("127.0.0.1", "localhost")

PAGE = pathlib.Path(__file__).with_name("cl_admin_ui.html")

#: Cloudflare 自己的数（`/api/cf`）：**本机**用只读 Analytics token 打 GraphQL，不经 Worker。
#: token 读 `~/.classlive/cf-analytics-token`（600），账号读 `~/.classlive/cf-account-id`；**从不进响应**。
#: 只回这几个名字开头的脚本 / 桶 —— 同一个账号里还有别的项目（photosave 桶），不该出现在这张面板上。
ISO_Z = r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ"
CF_PREFIX = "classlive-"
CF_GQL = "https://api.cloudflare.com/client/v4/graphql"
_CF_Q = """query($a:String!,$s:Time!,$u:Time!,$ds:Date!,$du:Date!){viewer{accounts(filter:{accountTag:$a}){
 day:workersInvocationsAdaptive(limit:100,filter:{datetime_geq:$s,datetime_leq:$u}){
  sum{requests errors} quantiles{cpuTimeP50 cpuTimeP99} dimensions{scriptName status}}
 mins:workersInvocationsAdaptive(limit:10000,filter:{datetime_geq:$s,datetime_leq:$u}){
  sum{requests errors} dimensions{scriptName datetimeMinute}}
 week:workersInvocationsAdaptive(limit:500,filter:{date_geq:$ds,date_leq:$du}){
  sum{requests errors} dimensions{scriptName date}}
 r2:r2StorageAdaptiveGroups(limit:100,filter:{date_geq:$ds,date_leq:$du}){
  max{objectCount payloadSize} dimensions{bucketName date}}
 dos:durableObjectsInvocationsAdaptiveGroups(limit:20,filter:{date_geq:$ds,date_leq:$du}){
  sum{requests} dimensions{date}}}}}"""


def cf_summary(post=None, now=None) -> dict:
    """近 24 h 的 Worker 健康 + 近 7 天的请求 / R2 / DO。**只回数字和 classlive-* 的名字**。

    `post(url, headers, body) -> dict` 测试注入；默认 httpx。拿不到 token 回 `{"error": ...}`（面板照常，只是这张卡说明原因）。
    """
    import datetime as dt
    home = pathlib.Path.home() / ".classlive"
    try:
        tok = (home / "cf-analytics-token").read_text(encoding="utf-8").strip()
        acct = (home / "cf-account-id").read_text(encoding="utf-8").strip()
    except OSError:
        return {"error": "没配 Cloudflare 只读 token（~/.classlive/cf-analytics-token，权限 Account Analytics: Read）"}
    if not tok or not acct:
        return {"error": "Cloudflare token / 账号 ID 是空的"}
    now = now or dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    v = {"a": acct, "s": (now - dt.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
         "u": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
         "ds": str((now - dt.timedelta(days=6)).date()), "du": str(now.date())}
    if post is None:
        def post(url, headers, body):
            import httpx
            return httpx.post(url, headers=headers, json=body, timeout=20).json()
    try:
        j = post(CF_GQL, {"Authorization": f"Bearer {tok}"}, {"query": _CF_Q, "variables": v})
    except Exception as e:                                            # noqa: BLE001
        return {"error": f"连不上 Cloudflare：{type(e).__name__}"}
    if j.get("errors"):
        return {"error": "Cloudflare 拒了：" + "；".join(str(x.get("message", ""))[:120] for x in j["errors"][:2])}
    try:
        a = j["data"]["viewer"]["accounts"][0]
    except (KeyError, IndexError, TypeError):
        return {"error": "Cloudflare 回的数据形状不认识"}
    ok = lambda name: str(name or "").startswith(CF_PREFIX)          # noqa: E731
    scripts: dict = {}
    for r in a.get("day") or []:
        d = r.get("dimensions", {})
        if not ok(d.get("scriptName")):
            continue
        sc = scripts.setdefault(d["scriptName"], {"requests": 0, "errors": 0, "status": {}, "cpu_p50_ms": 0.0, "cpu_p99_ms": 0.0})
        n = int(r["sum"]["requests"])
        sc["requests"] += n
        sc["errors"] += int(r["sum"]["errors"])
        sc["status"][d.get("status") or "?"] = sc["status"].get(d.get("status") or "?", 0) + n
        if d.get("status") == "success":                              # CPU 只看成功的那一组（微秒 → 毫秒）
            sc["cpu_p50_ms"] = round(r["quantiles"]["cpuTimeP50"] / 1000.0, 2)
            sc["cpu_p99_ms"] = round(r["quantiles"]["cpuTimeP99"] / 1000.0, 2)
    week: dict = {}
    for r in a.get("week") or []:
        d = r.get("dimensions", {})
        if ok(d.get("scriptName")):
            row = week.setdefault(d["date"], {})
            row[d["scriptName"]] = row.get(d["scriptName"], 0) + int(r["sum"]["requests"])
    r2: dict = {}
    for r in a.get("r2") or []:
        d = r.get("dimensions", {})
        if ok(d.get("bucketName")):
            r2.setdefault(d["bucketName"], {})[d["date"]] = {
                "objects": int(r["max"]["objectCount"]), "bytes": int(r["max"]["payloadSize"])}
    dos = {r["dimensions"]["date"]: int(r["sum"]["requests"]) for r in a.get("dos") or []}
    # 近 24 h 逐分钟：{脚本: {"2026-10-14T17:42:00Z": [请求, 错误]}}，只有有请求的分钟（≤1440×脚本数）
    minutes: dict = {}
    for r in a.get("mins") or []:
        d = r.get("dimensions", {})
        if ok(d.get("scriptName")) and d.get("datetimeMinute"):
            minutes.setdefault(d["scriptName"], {})[d["datetimeMinute"]] = [
                int(r["sum"]["requests"]), int(r["sum"]["errors"])]
    return {"since": v["s"], "until": v["u"], "scripts": scripts, "week": week, "r2": r2, "dos": dos,
            "minutes": minutes}


#: 上传桶（测试模式的课）。对象键 = `classlive-test/<sha256(会话名)[:16]>/<文件>`（`upload.session_key`）。
R2_BUCKET = "classlive-ingest"
#: 作者自己、但本机 `sessions/` 和上传账本里都没有的会话（隔离跑的探针，产物不落 sessions/）。不加就被算成「别人的」。
R2_KNOWN_MINE = {"2026-10-10_002058_TESTISO"}


def _r2_list(get=None):
    """列上传桶的**全部**对象（分页拼齐）。回 `(objs, error)`；`error` 非空时 `objs` 为 `None`。

    `get(url, headers) -> dict` 测试注入；默认 httpx。token 只在这里读，从不进响应。
    """
    home = pathlib.Path.home() / ".classlive"
    try:
        tok = (home / "cf-analytics-token").read_text(encoding="utf-8").strip()
        acct = (home / "cf-account-id").read_text(encoding="utf-8").strip()
    except OSError:
        return None, "没配 Cloudflare token（~/.classlive/cf-analytics-token）"
    if get is None:
        def get(url, headers):
            import httpx
            return httpx.get(url, headers=headers, timeout=20).json()
    objs, cursor = [], None
    for _ in range(50):                                               # 50 页 × 1000 封顶
        url = (f"https://api.cloudflare.com/client/v4/accounts/{acct}/r2/buckets/{R2_BUCKET}/objects?per_page=1000"
               + (f"&cursor={urllib.parse.quote(cursor)}" if cursor else ""))
        try:
            j = get(url, {"Authorization": f"Bearer {tok}"})
        except Exception as e:                                        # noqa: BLE001
            return None, f"列不了上传桶：{type(e).__name__}"
        if not j.get("success", True) or j.get("errors"):
            return None, "Cloudflare 拒了列桶：" + "；".join(str(x.get("message", ""))[:120] for x in (j.get("errors") or [])[:2])
        objs += j.get("result") or []
        info = j.get("result_info") or {}
        cursor = info.get("cursor")
        if not info.get("is_truncated") or not cursor:
            break
    return objs, None


def r2_ingest(get=None, sessions_dir=None, ledger=None) -> dict:
    """上传桶的**实时**清单（逐对象列，不是 Analytics —— `r2StorageAdaptiveGroups` 是每 10–30 分钟的快照，
    2026-10-14 实测比真列表少 7 个对象 / 58 MB）。按会话前缀分组，并标出**哪些不是本机的课**：
    本机 `sessions/` 里的会话名、上传账本里的会话名逐个算 `session_key`，对不上的就是别人的。

    只回数字、前缀、文件名（`report.json` / `audio.003.wav` 这种）和本机自己的会话名；token 从不进响应。
    """
    import hashlib
    home = pathlib.Path.home() / ".classlive"
    objs, err = _r2_list(get)
    if err:
        return {"error": err}
    names: set = set(R2_KNOWN_MINE)
    sd = pathlib.Path(sessions_dir) if sessions_dir else pathlib.Path(__file__).resolve().parent.parent / "sessions"
    try:
        names |= {f.name.split(".")[0] for f in sd.iterdir()}
    except OSError:
        pass
    try:
        lg = json.loads(pathlib.Path(ledger or (home / "upload-ledger.json")).read_text(encoding="utf-8"))
        if isinstance(lg, dict):
            names |= set(map(str, lg))
    except (OSError, ValueError):
        pass
    mine = {hashlib.sha256(n.encode("utf-8")).hexdigest()[:16]: n for n in names}
    groups: dict = {}
    for o in objs:
        parts = str(o.get("key", "")).split("/")
        if len(parts) < 3:
            continue
        g = groups.setdefault(parts[1], {"id": parts[1], "objects": 0, "bytes": 0, "last": "", "audio": 0, "files": []})
        g["objects"] += 1
        g["bytes"] += int(o.get("size") or 0)
        g["last"] = max(g["last"], str(o.get("last_modified") or ""))
        if parts[-1].startswith("audio."):
            g["audio"] += 1
        else:
            g["files"].append(parts[-1])
    out = []
    for g in groups.values():
        g["mine"] = g["id"] in mine
        g["name"] = mine.get(g["id"], "")
        g["files"].sort()
        out.append(g)
    out.sort(key=lambda g: g["last"], reverse=True)
    return {"bucket": R2_BUCKET, "objects": len(objs), "bytes": sum(int(o.get("size") or 0) for o in objs),
            "sessions": out, "others": sum(1 for g in out if not g["mine"])}


#: 上传的 `report.json`（测试模式）里 3.10 才有的块 —— 与 `testmode.BLOCKS` 一一对应。
#: 缺一个键 = 「这节课没采到 / 版本太老」，与「采到了但是 0」是两回事，前端照这个区分。
REPORT_BLOCKS = ("stream", "region_probe", "duty", "device", "energy", "capture",
                 "merge", "backfill", "system", "ui", "terms", "live", "deps")


def _num(v):
    """数字才留（布尔不算数），其余 → None。JSON 里的数可能是字符串，这里只认真数。"""
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _enum(v, n=40):
    """短枚举串（引擎名 / 区域 / 状态词）。**只留短串**，长了丢 —— 报告里不会有正文串，长串一律可疑。"""
    s = str(v) if isinstance(v, str) else ""
    return s if 0 < len(s) <= n else ""


def _block(rep, key):
    b = rep.get("blocks")
    x = b.get(key) if isinstance(b, dict) else None
    return x if isinstance(x, dict) else None


def _report_summary(rep: dict, sid: str, name: str, updated: str, files: list) -> dict:
    """一份上传的 `report.json` → **只含数字与枚举**的摘要（纯函数，可单测）。

    ⚠️ 白名单取值，**不是整份透传**：课堂文本、逐段明细（`segments`）、音频 / 会话 / 笔记文件路径
    （`audio_files` / `session_file` / `note_file`）、`events` 时间线、`vad_report` 一律不出现。
    每个派生字段各自兜底 —— 老版本报告缺哪一格，那一格就是 `None`（前端写「没采」，不写 0）。
    """
    rep = rep if isinstance(rep, dict) else {}
    asr = rep.get("asr") if isinstance(rep.get("asr"), dict) else {}
    corr = rep.get("correction") if isinstance(rep.get("correction"), dict) else {}
    tl = rep.get("translation_latency") if isinstance(rep.get("translation_latency"), dict) else {}

    st = _block(rep, "stream") or None
    if st is not None:
        sl = st.get("seal_latency_s") if isinstance(st.get("seal_latency_s"), dict) else {}
        st = {"engine": _enum(st.get("engine"), 24), "reason": _enum(st.get("fallback_reason")),
              # 3.10.1 起：退回时的异常**类型**（只留冒号前那段，如 `ModuleNotFoundError`；后面的话不要）
              "detail": _enum(str(st.get("fallback_detail") or "").split(":", 1)[0], 40),
              "sealed_n": _num(st.get("sealed_n")), "p50": _num(sl.get("p50")), "p90": _num(sl.get("p90")),
              "lat_n": _num(sl.get("n")), "terms_n": _num(st.get("terms_n")), "terms_dropped": _num(st.get("terms_dropped"))}

    rp = _block(rep, "region_probe") or None
    if rp is not None:
        regions = rp.get("regions") if isinstance(rp.get("regions"), dict) else {}
        rp = {"default": _enum(rp.get("default"), 8), "selected": _enum(rp.get("selected"), 8),
              "switched": rp.get("switched") if isinstance(rp.get("switched"), bool) else None,
              "reason": _enum(rp.get("reason")), "cached": rp.get("cached") if isinstance(rp.get("cached"), bool) else None,
              "ms": _num(rp.get("ms")), "timeout": rp.get("timeout") if isinstance(rp.get("timeout"), bool) else None,
              "regions": {k: {"connect_ms": _num((v or {}).get("connect_ms")), "rtt_ms": _num((v or {}).get("rtt_ms")),
                              "error": _enum((v or {}).get("error"), 24)}
                          for k, v in list(regions.items())[:6] if isinstance(v, dict)}}

    dt_ = _block(rep, "duty") or None
    if dt_ is not None:
        dt_ = {k: _num(dt_.get(k)) for k in ("items", "pending", "saved", "ignored", "inferred")}

    dv = _block(rep, "device") or None
    if dv is not None:
        end = dv.get("end") if isinstance(dv.get("end"), dict) else {}
        start = dv.get("start") if isinstance(dv.get("start"), dict) else {}
        src = end or start
        mach = src.get("machine") if isinstance(src.get("machine"), dict) else {}
        power = src.get("power") if isinstance(src.get("power"), dict) else {}
        fp = end.get("footprint") if isinstance(end.get("footprint"), dict) else {}
        dv = {"samples": len(dv.get("samples")) if isinstance(dv.get("samples"), list) else None,
              "model": _enum(mach.get("model")), "cores_perf": _num(mach.get("cores_perf")),
              "cores_eff": _num(mach.get("cores_eff")), "power": _enum(power.get("power"), 12),
              "battery_pct": _num(power.get("battery_pct")), "low_power": power.get("low_power_mode")
              if isinstance(power.get("low_power_mode"), bool) else None,
              "thermal": _enum(power.get("thermal"), 12), "net": _enum((src.get("net") or ""), 12),
              "lang": _enum((src.get("locale") or {}).get("lang") if isinstance(src.get("locale"), dict) else ""),
              "sessions_mb": _num(fp.get("sessions_mb")), "classlive_mb": _num(fp.get("classlive_mb")),
              "models_mb": _num(fp.get("models_mb")), "partial": fp.get("partial") if isinstance(fp.get("partial"), bool) else None}

    en = _block(rep, "energy") or None
    if en is not None:
        vis = en.get("visible") if isinstance(en.get("visible"), dict) else {}
        hid = en.get("hidden") if isinstance(en.get("hidden"), dict) else {}
        en = {"available": en.get("available") if isinstance(en.get("available"), bool) else None,
              "wall_s": _num(en.get("wall_s")), "cpu_s": _num(en.get("cpu_s")), "energy_j": _num(en.get("billed_energy_j")),
              "screen_hz": _num(en.get("screen_hz")),
              "visible_s": _num(vis.get("wall_s")), "visible_w": _num(vis.get("mean_w")),
              "hidden_s": _num(hid.get("wall_s")), "hidden_w": _num(hid.get("mean_w"))}

    cp = _block(rep, "capture") or None
    if cp is not None:
        gaps = cp.get("gaps") if isinstance(cp.get("gaps"), dict) else {}
        cp = {"available": cp.get("available") if isinstance(cp.get("available"), bool) else None,
              "wall_s": _num(cp.get("wall_s")), "audio_s": _num(cp.get("audio_s")), "deficit_s": _num(cp.get("deficit_s")),
              "gaps_n": _num(gaps.get("n")), "gaps_s": _num(gaps.get("total_s"))}

    mg = _block(rep, "merge") or None
    if mg is not None:
        mg = {k: _num(mg.get(k)) for k in ("triggered", "replaced", "failed")}
    bf = _block(rep, "backfill") or None
    if bf is not None:
        items = bf.get("items") if isinstance(bf.get("items"), list) else None
        bf = {"need": _num(bf.get("need")), "done": _num(bf.get("done")),
              "items": len(items) if items is not None else None}
    sys_ = _block(rep, "system") or None
    if sys_ is not None:
        sys_ = {"draft_engine": _enum(sys_.get("draft_engine"), 24), "draft_reason": _enum(sys_.get("draft_reason")),
                "ram_gb": _num(sys_.get("ram_gb")), "chip": _enum(sys_.get("chip")), "macos": _enum(sys_.get("macos"), 16),
                "arch": _enum(sys_.get("arch"), 12)}
    ui = _block(rep, "ui") or None
    if ui is not None:
        zh = ui.get("zh_overflow") if isinstance(ui.get("zh_overflow"), dict) else {}
        sq = ui.get("streamq_dwell_ms") if isinstance(ui.get("streamq_dwell_ms"), dict) else {}
        ui = {"submit": _num(ui.get("submit")), "hide": _num(ui.get("hide")), "show": _num(ui.get("show")),
              "new_topic": _num(ui.get("new_topic")), "hidden_s": _num(ui.get("hidden_s")),
              "overflow": _num(zh.get("overflow")), "overflow_seen": _num(zh.get("seen")),
              "sq_p50": _num(sq.get("p50")), "sq_p90": _num(sq.get("p90"))}
    dp = _block(rep, "deps") or None                       # 3.10.1：依赖现状（只留包名 / 布尔）
    if dp is not None:
        dp = {"missing": [_enum(x, 40) for x in (dp.get("missing") or [])[:10] if _enum(x, 40)]
              if isinstance(dp.get("missing"), list) else None,
              "uv": bool(dp.get("uv")) if "uv" in dp else None,
              "stamp_ok": dp.get("stamp_ok") if isinstance(dp.get("stamp_ok"), bool) else None}
    tm = _block(rep, "terms") or None
    if tm is not None:
        tm = {"calls": _num(tm.get("calls")), "picked": _num(tm.get("picked")), "empty": _num(tm.get("empty"))}
    lv = _block(rep, "live") or None
    if lv is not None:
        lv = {"calls_total": _num(lv.get("calls_total")), "failed_calls": _num(lv.get("failed_calls")),
              "chapters": _num(lv.get("chapters")), "atoms": _num(lv.get("atoms"))}

    gen = _enum(rep.get("generated"), 24)
    blocks = rep.get("blocks") if isinstance(rep.get("blocks"), dict) else {}
    return {
        "id": sid, "name": name, "files": sorted(str(x) for x in files)[:8], "updated": str(updated or ""),
        "generated": gen, "duration_s": _num(rep.get("duration_s")),
        "audio_saved": rep.get("audio_saved") if isinstance(rep.get("audio_saved"), bool) else None,
        "provisional": bool(rep.get("provisional")),          # 课中临时快照（收尾那份没这个键）
        "blocks": [k for k in REPORT_BLOCKS if k in blocks],  # 采到了哪几块
        "asr": {"segments": _num(asr.get("segments")), "words": _num(asr.get("words")),
                "empty_pct": _num(asr.get("empty_pct")), "final_empty_pct": _num(asr.get("final_empty_pct")),
                "fell_back_pct": _num(asr.get("fell_back_pct")),
                "source": {k: v for k, v in list((asr.get("source_pct") or {}).items())[:6]
                           if _num(v) is not None} if isinstance(asr.get("source_pct"), dict) else {}},
        "corr": {"pairs": _num(corr.get("pairs")), "changed": _num(corr.get("changed")),
                 "changed_pct": _num(corr.get("changed_pct"))},
        "tl": {"n": _num(tl.get("n")),
               "first_p50": _num((tl.get("zh_first_ms") or {}).get("p50")) if isinstance(tl.get("zh_first_ms"), dict) else None},
        "stream": st, "region": rp, "duty": dt_, "device": dv, "energy": en, "capture": cp,
        "merge": mg, "backfill": bf, "system": sys_, "ui": ui, "terms": tm, "live": lv, "deps": dp,
    }


#: 一次最多读几份报告（每份一次对象 GET；桶里几十节，只读最近的）。
R2_REPORT_MAX = 30


def r2_reports(get=None, raw=None, sessions_dir=None, ledger=None, max_fetch=R2_REPORT_MAX) -> dict:
    """上传桶里**每节测试模式的课**的 `report.json` 只读摘要（3.10 的块：Soniox 实时流 / 区域探测 /
    「一些事儿」/ 设备 / 能耗 / 收音断档 …）。

    会话组里有 `report.json` = 这节是**测试模式**（只有测试模式写报告并上传）；`mine` 照 `r2_ingest`
    的判据（本机 `sessions/` / 上传账本里有这个名字）。只取**最近** `max_fetch` 份。
    ⚠️ 对象通过 R2 REST `GET …/objects/{key}` 现取；**只解析、绝不落盘**；`_report_summary` 只回数字与枚举。
    """
    import hashlib
    home = pathlib.Path.home() / ".classlive"
    objs, err = _r2_list(get)
    if err:
        return {"error": err}
    names: set = set(R2_KNOWN_MINE)
    sd = pathlib.Path(sessions_dir) if sessions_dir else pathlib.Path(__file__).resolve().parent.parent / "sessions"
    try:
        names |= {f.name.split(".")[0] for f in sd.iterdir()}
    except OSError:
        pass
    try:
        lg = json.loads(pathlib.Path(ledger or (home / "upload-ledger.json")).read_text(encoding="utf-8"))
        if isinstance(lg, dict):
            names |= set(map(str, lg))
    except (OSError, ValueError):
        pass
    mine = {hashlib.sha256(n.encode("utf-8")).hexdigest()[:16]: n for n in names}
    groups: dict = {}
    for o in objs:
        parts = str(o.get("key", "")).split("/")
        if len(parts) < 3:
            continue
        g = groups.setdefault(parts[1], {"id": parts[1], "last": "", "files": [], "keys": {}})
        g["last"] = max(g["last"], str(o.get("last_modified") or ""))
        g["files"].append(parts[-1])
        if parts[-1] == "report.json":
            g["keys"]["report"] = str(o.get("key"))
    cand = sorted((g for g in groups.values() if "report" in g["keys"]), key=lambda g: g["last"], reverse=True)[:max_fetch]
    if raw is None:
        tok = (home / "cf-analytics-token").read_text(encoding="utf-8").strip()
        acct = (home / "cf-account-id").read_text(encoding="utf-8").strip()

        def raw(key, headers):                                        # noqa: F811  对象内容 via R2 REST（只读）
            import httpx
            url = f"https://api.cloudflare.com/client/v4/accounts/{acct}/r2/buckets/{R2_BUCKET}/objects/{urllib.parse.quote(key, safe='')}"
            return httpx.get(url, headers=headers, timeout=30).text
        auth = {"Authorization": f"Bearer {tok}"}
    else:
        auth = {}
    errors = []
    errs_lock = threading.Lock()

    def one(g):
        uid = g["id"]
        try:
            rep = json.loads(raw(g["keys"]["report"], auth))          # `raw(key, headers) -> 正文文本`
            return _report_summary(rep, uid, mine.get(uid, ""), g["last"], g["files"])
        except Exception as e:                                       # noqa: BLE001  单份坏了不连累别的
            with errs_lock:
                errors.append(f"{uid}：{type(e).__name__}")
            return None
    with ThreadPoolExecutor(min(8, max(1, len(cand)))) as ex:         # 每份一次对象 GET：并发取，别串成 30 个来回
        got = [r for r in ex.map(one, cand) if r is not None]
    return {"bucket": R2_BUCKET, "objects": len(objs), "candidates": len(cand),
            "sessions": got, "errors": errors[:5]}


def _cf_creds():
    """(token, 账号 ID) 或 None。token 只在本进程里用，从不进响应。"""
    home = pathlib.Path.home() / ".classlive"
    try:
        tok = (home / "cf-analytics-token").read_text(encoding="utf-8").strip()
        acct = (home / "cf-account-id").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return (tok, acct) if tok and acct else None


def _post_json(url, headers, body):
    import httpx
    return httpx.post(url, headers=headers, json=body, timeout=20).json()


ASR_SCRIPT = "classlive-asr"
ZONE = "bldcam.page"
#: 这个 zone 上**只有这两个主机名是 ClassLive 的**。同一个 zone 还挂着摄影站（bldcam.page / www）、
#: 图床（cdn）、数据站（data）—— 它们的请求跟这个工具无关，混进同一张表只会把要看的两行冲散。
#: 2026-10-14 实测 24 h：ClassLive 6981+263 对 摄影站 874+52+527+2。要加新的 ClassLive 入口改这里。
CLASSLIVE_HOSTS = ("asr.bldcam.page", "ingest.bldcam.page")
#: 子请求的主机名 → 人话。空主机名 = Durable Object（Worker 调自己的 DO，不出 Cloudflare）。
UPSTREAMS = {"api.groq.com": "Groq", "api.deepseek.com": "DeepSeek", "dashscope-intl.aliyuncs.com": "千问（国际）",
             "api.typesafe.ai": "TypeSafe Jev", "": "Durable Object"}
_HEALTH_Q = """query($a:String!,$z:String!,$s:Time!,$u:Time!,$sc:String!){viewer{
 accounts(filter:{accountTag:$a}){
  all:workersInvocationsAdaptive(limit:1,filter:{datetime_geq:$s,datetime_leq:$u,scriptName:$sc}){
   sum{requests errors} quantiles{wallTimeP50 wallTimeP95}}
  lat:workersInvocationsAdaptive(limit:100,filter:{datetime_geq:$s,datetime_leq:$u,scriptName:$sc}){
   sum{requests errors} quantiles{wallTimeP50 wallTimeP95 requestDurationP50 requestDurationP95} dimensions{datetimeHour}}
  ver:workersInvocationsAdaptive(limit:10000,filter:{datetime_geq:$s,datetime_leq:$u,scriptName:$sc}){
   sum{requests} dimensions{scriptVersion datetimeMinute}}
  sub:workersSubrequestsAdaptiveGroups(limit:200,filter:{datetime_geq:$s,datetime_leq:$u,scriptName:$sc}){
   sum{subrequests} dimensions{hostname httpResponseStatus requestOutcome}}
  colo:workersInvocationsAdaptive(limit:200,filter:{datetime_geq:$s,datetime_leq:$u,scriptName:$sc}){
   sum{requests} quantiles{wallTimeP50} dimensions{coloCode}}}
 zones(filter:{zoneTag:$z}){
  host:httpRequestsAdaptiveGroups(limit:2000,filter:{datetime_geq:$s,datetime_leq:$u}){
   count dimensions{clientRequestHTTPHost edgeResponseStatus clientCountryName}}}}}"""


_ZONE_ID: dict = {}                                                  # token → zone ID；zone ID 不会变，问一次就够


def _zone_id(tok, get=None):
    """bldcam.page 的 zone ID（同一个只读 token 能列 zone）。拿不到回空串 —— 面板只是少了站点那张表。
    拿到的记在进程里（只记成功的；测试注入 `get` 时不读不写这份缓存）。"""
    if get is None and _ZONE_ID.get(tok):
        return _ZONE_ID[tok]
    cache = get is None
    if get is None:
        def get(url, headers):
            import httpx
            return httpx.get(url, headers=headers, timeout=20).json()
    try:
        j = get(f"https://api.cloudflare.com/client/v4/zones?name={ZONE}", {"Authorization": f"Bearer {tok}"})
        zid = str((j.get("result") or [{}])[0].get("id") or "")
        if cache and zid:
            _ZONE_ID[tok] = zid
        return zid
    except Exception:                                                 # noqa: BLE001
        return ""


#: 部署链（`wrangler deploy` 的历史）。只读 token 恰好也有 Workers Scripts: Read。
DEPLOY_API = "https://api.cloudflare.com/client/v4/accounts/{acct}/workers/scripts/{script}/deployments"


def _deploys(tok, acct, get=None):
    """Worker 版本号 → 部署时刻（ISO 字符串）。`wrangler deploy` 一次就产生一个版本号 ——
    这是「那一版什么时候被换上」的**权威**答案。GraphQL 只给「第一次有请求的分钟」，
    低流量的版本可能几小时才来第一个请求，用那个推断会把部署时刻说得晚得多。

    读得到回 `{完整版本号: ISO}`；**回 `None` = 读不到**（token 没有 Workers Scripts: Read、
    或形状不认识）。调用方据此把 `deploys_error` 填上，退回按请求推断并如实标出来。
    只取 `created_on` 与 `version_id` —— 响应里还有 `author_email`，不进这个映射。

    ⚠️ `created_on` 带**微秒**（`2026-10-13T03:21:49.863119Z`）。ISO 的 Date Time String Format
    只认 3 位小数，多出来的位数属于「实现自定义」——V8 收（实测 node v22 解析成同一个毫秒数），
    但别的引擎没有保证。页面那边是 `Date.parse` 吃它，**解析失败会静默变成 NaN**（整根时间轴
    一起退化成「不知道」）。所以这里掐到秒再给出去，不赌解析器宽容。
    """
    if get is None:
        def get(url, headers):
            import httpx
            return httpx.get(url, headers=headers, timeout=20).json()
    try:
        j = get(DEPLOY_API.format(acct=acct, script=ASR_SCRIPT), {"Authorization": f"Bearer {tok}"})
    except Exception:                                                 # noqa: BLE001
        return None
    res = j.get("result")
    if not isinstance(res, dict):
        return None
    out = {}
    for d in res.get("deployments") or []:
        at = str(d.get("created_on") or "")
        if len(at) > 20 and at[19] in ".+":                           # 掐到秒（见上面那条 ⚠️）
            at = at[:19] + "Z"
        for v in d.get("versions") or []:
            vid = str(v.get("version_id") or "")
            if vid and at and vid not in out:
                out[vid] = at
    return out


def cf_health(post=None, get=None, now=None, window=None) -> dict:
    """近 24 h（或 `window=(起, 止)` 的 ISO 区间）的服务健康 —— 全是 Cloudflare 自己的数，本机直接问：

      overall   整个窗口的请求 / 错误 / wallTime p50·p95（毫秒）；读不到为 None
      lat       ASR Worker 每小时的 wallTime / requestDuration p50·p95（**毫秒**；GraphQL 给的是微秒）
      versions  每个部署版本在窗口里**第一次 / 最后一次**出现的分钟（= 部署标记；窗口起点之前就在的标 `before`），
                外加 `deployed_at` = 权威部署时刻（读不到时空串，见 `deploys_error`）
      upstream  子请求按上游 × 状态码（Groq / DeepSeek / 千问 / Jev / DO）
      colos     请求落在哪个 Cloudflare 节点 + 那里的 wallTime p50。⚠️ 节点 ≠ 用户所在地（见 HTML 的说明）
      zone      bldcam.page 整个站点按主机名 × 状态码 × 国家；外加 4xx/5xx 最多的路径。**不查 IP**。

    `post` / `get` 测试注入。token 从不进响应。
    """
    import datetime as dt
    cr = _cf_creds()
    if not cr:
        return {"error": "没配 Cloudflare 只读 token（~/.classlive/cf-analytics-token）"}
    tok, acct = cr
    now = now or dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    s, u = window or ((now - dt.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"), now.strftime("%Y-%m-%dT%H:%M:%SZ"))
    zid = _zone_id(tok, get)
    v = {"a": acct, "z": zid or "none", "s": s, "u": u, "sc": ASR_SCRIPT}
    with ThreadPoolExecutor(2) as ex:                                 # 部署链和 GraphQL 互不依赖：一起问，省一个来回
        dep_f = ex.submit(_deploys, tok, acct, get)
        try:
            j = (post or _post_json)(CF_GQL, {"Authorization": f"Bearer {tok}"}, {"query": _HEALTH_Q, "variables": v})
        except Exception as e:                                        # noqa: BLE001
            return {"error": f"连不上 Cloudflare：{type(e).__name__}"}
        dep = dep_f.result()
    errs = [x for x in (j.get("errors") or []) if "zones" not in (x.get("path") or [])]
    if errs or not j.get("data"):
        return {"error": "Cloudflare 拒了：" + "；".join(str(x.get("message", ""))[:120] for x in (j.get("errors") or [])[:2])}
    try:
        a = j["data"]["viewer"]["accounts"][0]
    except (KeyError, IndexError, TypeError):
        return {"error": "Cloudflare 回的数据形状不认识"}
    us = lambda x: round((x or 0) / 1000.0)                          # noqa: E731  微秒 → 毫秒
    lat = sorted(({"hour": r["dimensions"]["datetimeHour"], "requests": int(r["sum"]["requests"]),
                   "errors": int(r["sum"]["errors"]),
                   "wall_p50": us(r["quantiles"]["wallTimeP50"]), "wall_p95": us(r["quantiles"]["wallTimeP95"]),
                   "dur_p50": us(r["quantiles"]["requestDurationP50"]), "dur_p95": us(r["quantiles"]["requestDurationP95"])}
                  for r in a.get("lat") or []), key=lambda r: r["hour"])
    vers: dict = {}
    for r in a.get("ver") or []:
        d = r["dimensions"]
        x = vers.setdefault(d["scriptVersion"], {"id": d["scriptVersion"][:8], "first": d["datetimeMinute"],
                                                 "last": d["datetimeMinute"], "requests": 0})
        x["first"] = min(x["first"], d["datetimeMinute"])
        x["last"] = max(x["last"], d["datetimeMinute"])
        x["requests"] += int(r["sum"]["requests"])
    w = (a.get("all") or [None])[0]                                   # 整个窗口一个数（各小时的分位数不能再取平均）
    overall = {"requests": int(w["sum"]["requests"]), "errors": int(w["sum"]["errors"]),
               "wall_p50": us(w["quantiles"]["wallTimeP50"]), "wall_p95": us(w["quantiles"]["wallTimeP95"])} if w else None
    start_min = s[:16] + ":00Z"
    for full, x in vers.items():                                      # `vers` 的键就是完整版本号（对外只给前 8 位）
        x["deployed_at"] = (dep or {}).get(full, "")
    versions = sorted(vers.values(), key=lambda x: x["first"])
    for x in versions:
        x["before"] = x["first"] <= start_min                         # 窗口起点就在 = 不知道哪天部署的
    up: dict = {}
    for r in a.get("sub") or []:
        d = r["dimensions"]
        host = d.get("hostname") or ""
        row = up.setdefault(host, {"host": host, "name": UPSTREAMS.get(host, host), "total": 0,
                                   "ok": 0, "c4xx": 0, "c429": 0, "c5xx": 0, "unfinished": 0})
        n = int(r["sum"]["subrequests"])
        st = int(d.get("httpResponseStatus") or 0)
        row["total"] += n
        if d.get("requestOutcome") != "completed" or st == 0:
            row["unfinished"] += n
        elif st == 429:
            row["c429"] += n
        elif st >= 500:
            row["c5xx"] += n
        elif st >= 400:
            row["c4xx"] += n
        else:
            row["ok"] += n
    colos = sorted(({"colo": r["dimensions"]["coloCode"], "requests": int(r["sum"]["requests"]),
                     "wall_p50": us(r["quantiles"]["wallTimeP50"])} for r in a.get("colo") or []),
                   key=lambda r: -r["requests"])
    zone = None
    try:
        zz = j["data"]["viewer"]["zones"][0]
    except (KeyError, IndexError, TypeError):
        zz = None
    if zz:
        hosts: dict = {}
        for r in zz.get("host") or []:
            d = r["dimensions"]
            if d["clientRequestHTTPHost"] not in CLASSLIVE_HOSTS:      # 摄影站 / 图床 / 数据站：不是这个项目的东西
                continue
            h = hosts.setdefault(d["clientRequestHTTPHost"], {"host": d["clientRequestHTTPHost"], "total": 0,
                                                              "s2": 0, "s3": 0, "s4": 0, "s5": 0, "countries": {}})
            n = int(r["count"])
            h["total"] += n
            h["s%d" % min(5, max(2, int(d["edgeResponseStatus"] or 0) // 100))] += n
            c = d.get("clientCountryName") or "?"
            h["countries"][c] = h["countries"].get(c, 0) + n
        for h in hosts.values():
            h["countries"] = sorted(h["countries"].items(), key=lambda kv: -kv[1])[:5]
        # 4xx/5xx 的**路径**那张表删了：2026-10-14 实测 15 行全是扫描器探针（/.env.stage、/phpinfo.php~、
        # /backup.tbz2…），一条都要不动；真出错在「上游」表和 4xx/5xx 那两列里已经看得见。
        zone = {"hosts": sorted(hosts.values(), key=lambda h: -h["total"])}
    return {"since": s, "until": u, "overall": overall, "lat": lat, "versions": versions,
            "upstream": sorted(up.values(), key=lambda r: -r["total"]), "colos": colos, "zone": zone,
            "zone_error": "" if zone else "读不到 bldcam.page 的站点数（token 没有 Zone Analytics 权限，或 zone 没找到）",
            "deploys_error": "" if dep is not None else
            "⚠️ 读不到部署时刻（token 没有 Workers Scripts: Read），「部署于」是从第一次有请求的分钟推的，会偏晚"}


_WIN_Q = """query($a:String!,$s:Time!,$u:Time!,$sc:String!){viewer{accounts(filter:{accountTag:$a}){
 w:workersInvocationsAdaptive(limit:5,filter:{datetime_geq:$s,datetime_leq:$u,scriptName:$sc}){
  sum{requests errors} quantiles{wallTimeP50 wallTimeP95}}
 c:workersInvocationsAdaptive(limit:3,orderBy:[sum_requests_DESC],filter:{datetime_geq:$s,datetime_leq:$u,scriptName:$sc}){
  sum{requests} dimensions{coloCode}}}}}"""


def cf_window(s: str, u: str, post=None) -> dict:
    """一段时间里 ASR Worker 的请求数与墙钟 p50/p95（毫秒）+ 请求最多的 3 个节点。**全站口径**（所有人混在一起）——
    抽屉里给每个时段配一行；单人口径看 Analytics Engine。"""
    cr = _cf_creds()
    if not cr:
        return {"error": "没配 Cloudflare 只读 token"}
    tok, acct = cr
    try:
        j = (post or _post_json)(CF_GQL, {"Authorization": f"Bearer {tok}"},
                                 {"query": _WIN_Q, "variables": {"a": acct, "s": s, "u": u, "sc": ASR_SCRIPT}})
        a = j["data"]["viewer"]["accounts"][0]
    except Exception as e:                                            # noqa: BLE001
        return {"error": f"读不到：{type(e).__name__}"}
    w = (a.get("w") or [{}])[0] if a.get("w") else None
    if not w:
        return {"requests": 0}
    return {"requests": int(w["sum"]["requests"]), "errors": int(w["sum"]["errors"]),
            "wall_p50": round(w["quantiles"]["wallTimeP50"] / 1000.0), "wall_p95": round(w["quantiles"]["wallTimeP95"] / 1000.0),
            "colos": [[r["dimensions"]["coloCode"], int(r["sum"]["requests"])] for r in a.get("c") or []]}


#: Workers Logs（telemetry）—— 只看 `handler.js` 自己 console.log 的那几种事件，只回事件里的字段（码名 / 原因 / 引擎 / 毫秒）。
TELEMETRY = "https://api.cloudflare.com/client/v4/accounts/{acct}/workers/observability/telemetry/query"


def worker_logs(post=None, now_ms=None) -> dict:
    """近 24 h：各 ASR 引擎的耗时分布（Cloudflare 端算 median/p90/max）+ 最近的拒绝 / 全挂 / 额度 DO 不可用。

    ⚠️ 只取 `source.*`（我们自己写的那行 JSON），**不取** `$workers.event.request` —— 那里曾有 IP 与经纬度
    （invocation logs，2026-10-14 已在 wrangler.toml 关掉；旧的 3 天内仍在 Cloudflare 那边）。
    """
    import time
    cr = _cf_creds()
    if not cr:
        return {"error": "没配 Cloudflare 只读 token"}
    tok, acct = cr
    now_ms = now_ms or int(time.time() * 1000)
    tf = {"from": now_ms - 86400000, "to": now_ms}
    svc = {"key": "$metadata.service", "operation": "eq", "type": "string", "value": ASR_SCRIPT}
    url = TELEMETRY.format(acct=acct)
    hdr = {"Authorization": f"Bearer {tok}"}
    post = post or _post_json

    def q(body):
        j = post(url, hdr, {"queryId": "clui", "timeframe": tf, **body})
        if not j.get("success", False):
            raise RuntimeError("；".join(str(x.get("message", ""))[:120] for x in (j.get("errors") or [])[:2]) or "telemetry 拒了")
        return j.get("result") or {}

    flts = ({"key": "denied", "operation": "exists", "type": "string"},
            {"key": "result", "operation": "eq", "type": "string", "value": "all_failed"},
            {"key": "quota", "operation": "exists", "type": "string"})
    try:
        # 四条查询互不依赖，一条约 2 s —— 串着问要 9 s，一起问 ≈ 最慢那条
        with ThreadPoolExecutor(4) as ex:
            calc_f = ex.submit(q, {"view": "calculations", "parameters": {
                "datasets": ["cloudflare-workers"],
                "filters": [svc, {"key": "provider", "operation": "exists", "type": "string"}],
                "calculations": [{"operator": op, "key": "ms", "keyType": "number"} for op in ("median", "p90", "max")],
                "groupBys": [{"type": "string", "value": "model"}]}})
            ev_fs = [ex.submit(q, {"view": "events", "limit": 30, "parameters": {
                "datasets": ["cloudflare-workers"], "filters": [svc, flt]}}) for flt in flts]
            calc = calc_f.result()
            evs = [f.result() for f in ev_fs]
        out: dict = {}
        for c in calc.get("calculations") or []:
            op = str(c.get("calculation", "")).split("(")[0]
            for ag in c.get("aggregates") or []:
                r = out.setdefault(ag.get("groupKey") or "?", {"model": ag.get("groupKey") or "?", "count": 0})
                r[op] = round(float(ag.get("value") or 0))
                r["count"] = int(ag.get("count") or 0)
        events = []
        for ev in evs:
            for e in (ev.get("events") or {}).get("events") or []:
                src = e.get("source") if isinstance(e.get("source"), dict) else {}
                keep = {k: src[k] for k in ("pass", "denied", "kind", "result", "ms", "tried", "quota") if k in src}
                if keep:
                    keep["t"] = int(e.get("timestamp") or 0)
                    events.append(keep)
    except Exception as e:                                            # noqa: BLE001
        return {"error": f"Workers Logs 读不到：{str(e)[:160] or type(e).__name__}"}
    events.sort(key=lambda x: -x["t"])
    return {"models": sorted(out.values(), key=lambda r: -r["count"]), "events": events[:40]}


#: Analytics Engine（`handler.js` 的 track()）。列顺序与那里的注释一一对应 —— 改一边要改另一边。
AE_DATASET = "classlive_events"
AE_SQL = "https://api.cloudflare.com/client/v4/accounts/{acct}/analytics_engine/sql"
_AE_BY_CODE = f"""SELECT blob2 AS name, blob1 AS path, SUM(_sample_interval) AS n,
 quantileExactWeighted(0.5)(double2, _sample_interval) AS p50, quantileExactWeighted(0.95)(double2, _sample_interval) AS p95,
 SUM(IF(double1 >= 400, _sample_interval, 0)) AS errors, SUM(double3 * _sample_interval) AS audio_s,
 SUM((double4 + double5) * _sample_interval) AS tokens, MAX(timestamp) AS last
 FROM {AE_DATASET} WHERE timestamp > NOW() - INTERVAL '1' DAY GROUP BY name, path ORDER BY n DESC LIMIT 100 FORMAT JSON"""
_AE_GEO = f"""SELECT blob2 AS name, blob6 AS country, blob7 AS colo, blob5 AS ver, SUM(_sample_interval) AS n,
 quantileExactWeighted(0.5)(double2, _sample_interval) AS p50
 FROM {AE_DATASET} WHERE timestamp > NOW() - INTERVAL '1' DAY GROUP BY name, country, colo, ver ORDER BY n DESC LIMIT 200 FORMAT JSON"""
_AE_BAD = f"""SELECT timestamp AS t, blob2 AS name, blob1 AS path, blob3 AS model, blob4 AS up, double1 AS status, double2 AS ms
 FROM {AE_DATASET} WHERE timestamp > NOW() - INTERVAL '1' DAY AND double1 >= 400 ORDER BY t DESC LIMIT 30 FORMAT JSON"""

# 每人 × 路径 × 上游尝试串 × 状态：「要注意」那一栏靠它判 —— DeepSeek 402（余额不足）、Groq 403（香港节点被拒）……
# 只有 ≥400 的请求进不了的（Groq 403 后千问接住，最终是 200），所以按上游串分组，不按最终状态。
_AE_UP = f"""SELECT blob2 AS name, blob1 AS path, blob4 AS up, double1 AS status, SUM(_sample_interval) AS n,
 MIN(timestamp) AS first, MAX(timestamp) AS last
 FROM {AE_DATASET} WHERE timestamp > NOW() - INTERVAL '1' DAY GROUP BY name, path, up, status ORDER BY n DESC LIMIT 200 FORMAT JSON"""


# 速度：7 天 × 每人 × 每天 × 类别（只要 200）。类别靠请求的 max_tokens 分（double11，2026-10-15 起才有）：
#   asr = 转写；live = 实时翻译（≤ 600）；note = 课后笔记 / 精修（> 600）；old = 加列之前的翻译（分不出来）。
# 首字 double9 / 完成 double10 只有翻译有；词数 double8 只有转写有。都是数字，不含文本。
_AE_SPEED = f"""SELECT toStartOfDay(timestamp) AS day, blob2 AS name,
 IF(blob1 = '/v1/transcribe', 'asr', IF(double11 > 600, 'note', IF(double11 > 0, 'live', 'old'))) AS kind,
 SUM(_sample_interval) AS n, quantileExactWeighted(0.5)(double2, _sample_interval) AS p50,
 quantileExactWeighted(0.95)(double2, _sample_interval) AS p95,
 quantileExactWeighted(0.5)(double9, _sample_interval) AS first50, quantileExactWeighted(0.5)(double10, _sample_interval) AS done50,
 quantileExactWeighted(0.95)(double10, _sample_interval) AS done95,
 SUM(double8 * _sample_interval) AS words, SUM(double3 * _sample_interval) AS audio_s, SUM(double5 * _sample_interval) AS tout
 FROM {AE_DATASET} WHERE timestamp > NOW() - INTERVAL '7' DAY AND double1 = 200
 AND blob1 IN ('/v1/transcribe', '/v1/chat/completions')
 GROUP BY day, name, kind ORDER BY day DESC, name LIMIT 300 FORMAT JSON"""


def _ae_sqls(post, qs):
    """并发跑一族 AE SQL（`qs` = ((key, sql), …)）→ `{key: rows}`；数据集缺席 / 被拒 → 一条错误对象。

    `post(url, headers, sql) -> (status_code, json)` 测试注入；默认 httpx。
    """
    cr = _cf_creds()
    if not cr:
        return {"error": "没配 Cloudflare 只读 token"}
    tok, acct = cr
    if post is None:
        def post(url, headers, sql):
            import httpx
            r = httpx.post(url, headers=headers, content=sql.encode(), timeout=20)
            try:
                return r.status_code, r.json()
            except ValueError:
                return r.status_code, {"error": r.text[:200]}
    url = AE_SQL.format(acct=acct)
    with ThreadPoolExecutor(len(qs)) as ex:                           # 几条互不依赖，一起问
        futs = [ex.submit(post, url, {"Authorization": f"Bearer {tok}"}, sql) for _, sql in qs]
    out = {}
    for (key, _), f in zip(qs, futs):
        try:
            code, j = f.result()
        except Exception as e:                                        # noqa: BLE001
            return {"error": f"连不上 Analytics Engine：{type(e).__name__}"}
        if code != 200:
            msg = str(j.get("error") or j.get("errors") or j)[:200]
            # 2026-10-14 实测：账号没开 Analytics Engine 时 SQL 回 403 Authorization error（部署回 10089）。
            if code == 403 or "does not exist" in msg.lower() or ("unknown" in msg.lower() and AE_DATASET in msg):
                return {"pending": True, "error": f"还读不到数据集 {AE_DATASET}（{code}）：Cloudflare 后台还没开 Analytics Engine，"
                        "或开了还没重新部署 Worker；都做了仍是 403 → token 要有 Account Analytics: Read"}
            return {"error": f"Analytics Engine 拒了（{code}）：{msg}"}
        out[key] = j.get("data") or []
    return out


def ae_events(post=None) -> dict:
    """逐请求事件的近 24 h 汇总：每个码 × 路径的请求数 / p50·p95 毫秒 / 出错数 / 音频秒 / token；
    码 × 国家 × 节点 × 客户端版本；最近 30 条 ≥400 的请求。数据集还不存在（没开 Analytics Engine / 还没部署）→ 回 `pending`。"""
    out = _ae_sqls(post, (("by_code", _AE_BY_CODE), ("geo", _AE_GEO), ("bad", _AE_BAD), ("up", _AE_UP), ("speed", _AE_SPEED)))
    if "error" in out or out.get("pending"):
        return out
    for r in out["bad"]:
        r.pop("ip", None)
    return out


# ---- 3.10 新量具（Analytics Engine，近 7 天）----
# 这些是 3.10 才开始记 / 才开始有量的东西；口径都对着 handler.js 的 track() 列注释：
#   blob4 up  = 上游尝试串。翻译上是 `openrouter:<状态>` 或 `<openrouter 失败串>deepseek:<状态>` 或 `deepseek:<状态>`；
#               Soniox 上是 `soniox-<区>:<状态>[:probe]`（签发临时 key 的结果）。
#   blob5 ver = 客户端版本。double9 = 翻译首字毫秒。double11 = 请求的 max_tokens（>600 = 笔记 / 精修）。
_AE_310_SONIOX = f"""SELECT blob4 AS up, blob2 AS name, double1 AS status, SUM(_sample_interval) AS n,
 MIN(timestamp) AS first, MAX(timestamp) AS last
 FROM {AE_DATASET} WHERE timestamp > NOW() - INTERVAL '7' DAY AND blob1 = '/v1/soniox/key'
 GROUP BY up, name, status ORDER BY n DESC LIMIT 200 FORMAT JSON"""
# 快速通道命中率的分母只能是**够格**的请求（Worker `useFastLlm`：stream、max_tokens ≤ 400、不在 CN/HK/MO）。
# 2026-10-18 实测：分母混进笔记 / 精修（max_tokens 1500–16384，设计上就不绕道）和通道上线前的老流量，
# 命中率显示 1.1%；只数够格的是 229 / 239 ≈ 96%。首字 double9 > 0 ⇔ 流式（非流式不记首字）。
_AE_310_FAST = f"""SELECT blob4 AS up, double1 AS status, SUM(_sample_interval) AS n,
 quantileExactWeighted(0.5)(double9, _sample_interval) AS f50, quantileExactWeighted(0.95)(double9, _sample_interval) AS f95,
 quantileExactWeighted(0.5)(double2, _sample_interval) AS ms50
 FROM {AE_DATASET} WHERE timestamp > NOW() - INTERVAL '7' DAY AND blob1 = '/v1/chat/completions'
 AND double11 > 0 AND double11 <= 400 AND double9 > 0 AND blob6 NOT IN ('CN', 'HK', 'MO')
 GROUP BY up, status ORDER BY n DESC LIMIT 100 FORMAT JSON"""
_AE_310_VER = f"""SELECT blob5 AS ver, SUM(_sample_interval) AS n, COUNT(DISTINCT blob2) AS users
 FROM {AE_DATASET} WHERE timestamp > NOW() - INTERVAL '7' DAY GROUP BY ver ORDER BY n DESC LIMIT 100 FORMAT JSON"""
_AE_310_NOTES = f"""SELECT blob2 AS name, SUM(_sample_interval) AS n, SUM(double4 * _sample_interval) AS tin,
 SUM(double5 * _sample_interval) AS tout, SUM(double6 * _sample_interval) AS hit, MAX(double11) AS max_tok
 FROM {AE_DATASET} WHERE timestamp > NOW() - INTERVAL '7' DAY AND blob1 = '/v1/chat/completions' AND double11 > 600
 GROUP BY name ORDER BY n DESC LIMIT 100 FORMAT JSON"""


def ae_310(post=None) -> dict:
    """3.10 的新量具（近 7 天）：`soniox` Soniox 临时 key 签发（区 × 结果 × 探测 × 码）、
    `fast` 翻译上游尝试串（OpenRouter 快速通道命中 / 退回 / 直连）、`rollout` 客户端版本普及、
    `notes` 课后笔记（max_tokens > 600）按码的 token。数据集缺席 → 同 `ae_events` 回 `pending`。"""
    return _ae_sqls(post, (("soniox", _AE_310_SONIOX), ("fast", _AE_310_FAST),
                           ("rollout", _AE_310_VER), ("notes", _AE_310_NOTES)))


# ---- 平台账号（Soniox / OpenRouter）：本机 key 只读 GET，key 从不进响应 ----
#: Soniox 各区 API 基址 —— 与 Worker 的 `soniox-core.js` REGIONS 一一对应（加一个区改这里）。
SONIOX_REGION_API = {"us": "https://api.soniox.com", "jp": "https://api.jp.soniox.com", "eu": "https://api.eu.soniox.com"}
#: 各区项目 key 住在哪个本机文件（`~/.classlive/`）。没有这个文件 = 这个区本机没 key，不查。
SONIOX_REGION_FILE = {"us": "soniox-token", "jp": "soniox-jp-token", "eu": "soniox-eu-token"}
#: 组织级 key（余额 / 组织用量）：与项目 key 分开，**没有就看不到余额**（项目 key 查余额回 403）。
SONIOX_ORG_FILE = "soniox-org-token"
#: OpenRouter：**只用管理 key**（Settings → Management Keys；它不能发模型请求）。普通推理 key 打
#: analytics 会 403，而 Observability 的「destinations」会把请求内容推给第三方（Langfuse 等）—— 隐私不行，不用。
#: 管理 key 存在 `~/.classlive/openrouter-mgmt-token`；没有 → 卡片显示「没配」。
OPENROUTER_MGMT_FILE = "openrouter-mgmt-token"
OPENROUTER_CREDITS_URL = "https://openrouter.ai/api/v1/credits"
OPENROUTER_META_URL = "https://openrouter.ai/api/v1/analytics/meta"
OPENROUTER_QUERY_URL = "https://openrouter.ai/api/v1/analytics/query"
#: 想查的指标（**先拿 `/analytics/meta` 问有哪些，取交集**，别硬查一个不存在的名字 → 400）。
OPENROUTER_WANT_METRICS = ("total_usage", "request_count", "tokens_prompt", "tokens_completion", "reasoning_tokens")


def _home_secret(*names) -> str:
    """`~/.classlive/<名字>` 的第一行非空内容（第一个存在的文件的）。没有就是 `""`。**只读、从不外传**。"""
    home = pathlib.Path.home() / ".classlive"
    for n in names:
        try:
            lines = [x.strip() for x in (home / n).read_text(encoding="utf-8").splitlines() if x.strip()]
        except OSError:
            continue
        if lines:
            return lines[0]
    return ""


def _get_json(url, tok=None, get=None, timeout=20) -> dict:
    """GET → JSON。`get(url, headers) -> dict` 测试注入；默认 httpx。`tok` 为空则不带 Authorization。"""
    if get is None:
        def get(url, headers):
            import httpx
            return httpx.get(url, headers=headers, timeout=timeout).json()
    headers = {"Authorization": f"Bearer {tok}"} if tok else {}
    r = get(url, headers)
    return r if isinstance(r, dict) else {}


def soniox_account(get=None, now=None) -> dict:
    """本机 Soniox 项目 key 的用量与并发额度（只读；key 从不进响应）。

    · `usage`   每区一个项目的用量：`GET /v1/usage/summary`（7 天与当日花费，美元）—— 官方 2026-10-18 核过。
    · `concurrency` `GET /v1/concurrency-limits`（project 作用域）：当前并发 vs 上限。
    · `balance` 组织余额 `GET /v1/organization/balance` —— **只在配了组织 key 时**查；项目 key 回 403，那种情况写明原因。
    """
    import datetime as dt
    now = now or dt.datetime.now(dt.timezone.utc)
    keys = {r: _home_secret(f) for r, f in SONIOX_REGION_FILE.items()}
    keys = {r: k for r, k in keys.items() if k}
    if not keys:
        return {"error": "没配 Soniox key（~/.classlive/soniox-token）"}
    s7 = (now - dt.timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    u = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    usage, errs = {}, []
    for region, key in keys.items():
        try:
            j = _get_json(f"{SONIOX_REGION_API[region]}/v1/usage/summary?start_time={s7}&end_time={u}", key, get)
        except Exception as e:                                        # noqa: BLE001
            errs.append(f"{region} 用量读不到：{type(e).__name__}")
            continue
        total = j.get("total") if isinstance(j.get("total"), dict) else {}
        days = total.get("days") if isinstance(total.get("days"), list) else []
        cost = total.get("cost_usd") if isinstance(total.get("cost_usd"), list) else []
        today = cost[-1] if days and days[-1][:10] == now.strftime("%Y-%m-%d") and cost else None
        models = [{"model": str(m.get("model") or ""), "cost": _f(m.get("total_cost_usd"))}
                  for m in (j.get("models") or []) if isinstance(m, dict)][:6]
        usage[region] = {"days": [str(d)[:10] for d in days], "cost": [_f(x) for x in cost],
                         "cost_7d": _f(total.get("total_cost_usd")), "cost_today": _f(today) if today is not None else None,
                         "models": models}
    # 用量日志（`/v1/usage-logs`）：Soniox 侧的**真值** —— 每条一次会话，带音频秒与码名
    # （client_reference_id = 码名，Worker 签发临时 key 时就带上了）。这是「Worker 发了多少把 key
    # 对 Soniox 实际收了多少秒」的第三方对账口径。
    logs = {"sessions": 0, "audio_s": 0.0, "by_name": {}, "more": False}
    for region, key in keys.items():
        try:
            lj = _get_json(f"{SONIOX_REGION_API[region]}/v1/usage-logs?start_time={s7}&end_time={u}&limit=1000", key, get)
        except Exception as e:                                        # noqa: BLE001
            errs.append(f"{region} 用量日志读不到：{type(e).__name__}")
            continue
        rows = lj.get("usage_logs") if isinstance(lj.get("usage_logs"), list) else []
        logs["more"] = logs["more"] or bool(lj.get("next_page_cursor"))
        for r in rows:
            if not isinstance(r, dict):
                continue
            ms = r.get("input_audio_duration_ms")
            sec = float(ms) / 1000.0 if isinstance(ms, (int, float)) else 0.0
            nm = _enum(r.get("client_reference_id"), 24) or "-"
            rd = logs["by_name"].setdefault(nm, {"sessions": 0, "audio_s": 0.0})
            rd["sessions"] += 1
            rd["audio_s"] = round(rd["audio_s"] + sec, 1)
            logs["sessions"] += 1
            logs["audio_s"] = round(logs["audio_s"] + sec, 1)
    first = next(iter(keys))
    concurrency = {"current": None, "limit": None}
    try:
        cj = _get_json(f"{SONIOX_REGION_API[first]}/v1/concurrency-limits", keys[first], get)
        proj = cj.get("project") if isinstance(cj.get("project"), dict) else {}
        org = cj.get("organization") if isinstance(cj.get("organization"), dict) else {}
        pcur = (proj.get("current") or {}).get("transcribe_concurrent") if isinstance(proj.get("current"), dict) else None
        plim = (proj.get("limits") or {}).get("transcribe_concurrent") if isinstance(proj.get("limits"), dict) else None
        ocur = (org.get("current") or {}).get("transcribe_concurrent") if isinstance(org.get("current"), dict) else None
        olim = (org.get("limits") or {}).get("transcribe_concurrent") if isinstance(org.get("limits"), dict) else None
        cur, lim = _num(pcur), _num(plim)
        if lim is None:                                               # 项目没设上限 → 看组织那档（实测上限在建在组织上）
            lim, cur = _num(olim), _num(ocur)
        concurrency = {"current": cur, "limit": lim}
    except Exception as e:                                            # noqa: BLE001
        errs.append(f"并发额度读不到：{type(e).__name__}")
    balance = None
    org = _home_secret(SONIOX_ORG_FILE)
    if org:
        try:
            bj = _get_json(f"{SONIOX_REGION_API['us']}/v1/organization/balance", org, get)
            balance = {"available": _f(bj.get("available_balance_usd")), "free": _f(bj.get("free_balance_usd")),
                       "used": _f(bj.get("used_balance_usd")),
                       "exhausted": bj.get("balance_exhausted") if isinstance(bj.get("balance_exhausted"), bool) else None,
                       "autopay": bj.get("autopay_enabled") if isinstance(bj.get("autopay_enabled"), bool) else None}
        except Exception as e:                                        # noqa: BLE001
            errs.append(f"余额读不到：{type(e).__name__}")
    return {"regions": sorted(usage), "usage": usage, "logs": logs, "concurrency": concurrency,
            "balance": balance, "balance_error": "" if balance else
            "没配组织 key（~/.classlive/soniox-org-token）—— 余额看不到；项目 key 查余额会被拒 403", "errors": errs[:4]}


def openrouter_account(get=None, post=None, now=None) -> dict:
    """本机 OpenRouter **管理 key** 的账户与用量（只读；key 从不进响应）。

    · `GET /api/v1/credits`        账户购买 / 已用总额 → 余额（**要管理 key**；普通 key 403）。
    · `GET /api/v1/analytics/meta` 先问有哪些 metric / dimension（官方原话：query 只能用它报的）。
    · `POST /api/v1/analytics/query` 近 7 天按天 × 模型的用量（指标取 meta 里真有的那几个）。
    管理 key 住在 `~/.classlive/openrouter-mgmt-token`（不能发模型请求，也读不到课堂文本）。

    ⚠️ 我们**不接** Observability 的 destinations（那把请求内容推给第三方）；analytics 只有数。
    """
    import datetime as dt
    key = _home_secret(OPENROUTER_MGMT_FILE)
    if not key:
        return {"error": "没配 OpenRouter 管理 key（放到 ~/.classlive/openrouter-mgmt-token；普通推理 key 读不了 analytics）"}
    now = now or dt.datetime.now(dt.timezone.utc)
    out: dict = {}
    try:
        c = _get_json(OPENROUTER_CREDITS_URL, key, get).get("data")
        c = c if isinstance(c, dict) else {}
        out["credits"] = {"total": _f(c.get("total_credits")), "used": _f(c.get("total_usage"))}
    except Exception as e:                                            # noqa: BLE001
        out["credits_error"] = f"余额读不到：{type(e).__name__}（403 = 这把不是管理 key）"
    # meta：有哪些 metric / dimension。取名字（元素可能是串，也可能是带 name/id/key 的对象）。
    meta = {}
    try:
        mj = _get_json(OPENROUTER_META_URL, key, get).get("data")
        mj = mj if isinstance(mj, dict) else {}
        names = lambda arr: [str(x) if isinstance(x, str) else str((x or {}).get("name") or (x or {}).get("id") or (x or {}).get("key") or "")
                             for x in (arr or []) if isinstance(x, (str, dict))]
        meta = {"metrics": [x for x in names(mj.get("metrics")) if x],
                "dimensions": [x for x in names(mj.get("dimensions")) if x],
                "granularities": [x for x in names(mj.get("granularities")) if x]}
    except Exception as e:                                            # noqa: BLE001
        out["meta_error"] = f"schema 读不到：{type(e).__name__}"
    out["meta"] = meta
    if post is None:
        def post(url, headers, body):
            import httpx
            return httpx.post(url, headers=headers, json=body, timeout=25).json()
    metrics = [m for m in OPENROUTER_WANT_METRICS if m in meta.get("metrics", [])]
    if metrics:
        body = {"metrics": metrics, "granularity": "day",
                "time_range": {"start": (now - dt.timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                               "end": now.strftime("%Y-%m-%dT%H:%M:%SZ")}}
        if "model" in meta.get("dimensions", []):
            body["dimensions"] = ["model"]
        try:
            qj = post(OPENROUTER_QUERY_URL, {"Authorization": f"Bearer {key}", "content-type": "application/json"}, body)
            dd = (qj.get("data") or {}).get("data") if isinstance(qj.get("data"), dict) else None
            out["query"] = {"metrics": metrics, "rows": [r for r in (dd or []) if isinstance(r, dict)][:200]}
        except Exception as e:                                        # noqa: BLE001
            out["query_error"] = f"analytics 查询失败：{type(e).__name__}"
    else:
        out["query_error"] = out.get("meta_error") or "meta 里没有我们认得的那几个指标（total_usage / request_count / …）"
    return out


def _f(v):
    """字符串 / 数字 → float（整数就整数）；解析不出 → None。第三方余额字段都是字符串，这里统一。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return int(f) if f.is_integer() else round(f, 4)


#: 慢的几个（直接问 Cloudflare，一个 0.5–2 s）走「先给旧的、后台再取」：页面每分钟自动刷一次，
#: 不该每次都等 Cloudflare；手动点「刷新」带 `{"fresh": true}`，那一次同步重取。出错的结果不进缓存。
# ---- 上课习惯（近 30 天，Analytics Engine）----
# 「在上课」= 这 10 分钟里有实时请求：云端转写、实时翻译（max_tokens ≤ 600，笔记 / 精修不算）、
# 领 Soniox 临时 key（开课前的地区探测 `:probe` 不算）。用 Soniox 的人转写走直连、Worker 看不到，
# 但实时翻译照样经过 Worker —— 所以这三样合起来才认得出每个人的每一节课。
_AE_HABITS = f"""SELECT toStartOfInterval(timestamp, INTERVAL '10' MINUTE) AS t, blob2 AS name, blob6 AS country,
 blob5 AS ver, SUM(_sample_interval) AS n
 FROM {AE_DATASET} WHERE timestamp > NOW() - INTERVAL '30' DAY AND double1 = 200
 AND ((blob1 = '/v1/transcribe') OR (blob1 = '/v1/chat/completions' AND double11 <= 600)
      OR (blob1 = '/v1/soniox/key' AND NOT (blob4 LIKE '%:probe%')))
 GROUP BY t, name, country, ver ORDER BY t LIMIT 20000 FORMAT JSON"""

#: 国家 → 时区（只按国家猜；美国按太平洋 —— 现在唯一的美国用户在加州上课）。猜不出就用 UTC。
_TZ_BY_COUNTRY = {"IE": "Europe/Dublin", "GB": "Europe/London", "CN": "Asia/Shanghai", "HK": "Asia/Hong_Kong",
                  "MO": "Asia/Macau", "TW": "Asia/Taipei", "SG": "Asia/Singapore", "JP": "Asia/Tokyo",
                  "KR": "Asia/Seoul", "US": "America/Los_Angeles", "CA": "America/Toronto",
                  "AU": "Australia/Sydney", "DE": "Europe/Berlin", "FR": "Europe/Paris", "NL": "Europe/Amsterdam"}
#: 一节课：相邻两个有请求的 10 分钟格隔不到这么久就算同一节。
HABIT_GAP_S = 30 * 60
#: 不到这么长的不算「一节课」（打开面板看一眼 / 试一句），只计入「短打开」。
HABIT_MIN_S = 20 * 60


def _ver_key(v):
    try:
        return tuple(int(x) for x in str(v).split("."))
    except ValueError:
        return ()


def habits_of(rows, now_ms=None) -> dict:
    """AE 10 分钟格 → 每个人的上课习惯。**纯函数**（可单测）。

    每人：`sessions`（节数，≥ `HABIT_MIN_S`）/ `short`（短打开次数）/ `hours` / `days`（上过课的天数）/
    `tz` / `by_hour`（本地 0–23 点，按在课上的 10 分钟格数）/ `by_wd`（周一..周日）/ `recent`（最近几节
    `[开始 ms, 结束 ms]`）/ `ver`（最近一次的客户端版本）/ `country`。另回 `latest_ver`（大家里最新的）。
    """
    import datetime as dt
    from zoneinfo import ZoneInfo
    now_ms = now_ms if now_ms is not None else time.time() * 1000
    per: dict = {}
    for r in rows or []:
        nm = _enum(r.get("name"), 40)
        if not nm or nm == "-":
            continue
        try:
            t = dt.datetime.fromisoformat(str(r["t"]).replace(" ", "T")).replace(tzinfo=dt.timezone.utc)
        except (KeyError, ValueError):
            continue
        d = per.setdefault(nm, {"b": set(), "cty": {}, "ver": ("", None)})
        d["b"].add(t)
        c = _enum(r.get("country"), 4) or "?"
        d["cty"][c] = d["cty"].get(c, 0) + float(r.get("n") or 0)
        v = _enum(r.get("ver"), 16)
        if v and (d["ver"][1] is None or t >= d["ver"][1]):
            d["ver"] = (v, t)
    out, latest = {}, ""
    for nm, d in per.items():
        cty = max(d["cty"], key=d["cty"].get) if d["cty"] else "?"
        tzn = _TZ_BY_COUNTRY.get(cty, "UTC")
        tz = ZoneInfo(tzn)
        bs = sorted(d["b"])
        spans: list = []
        for t in bs:
            if spans and (t - spans[-1][1]).total_seconds() <= HABIT_GAP_S:
                spans[-1][1] = t
            else:
                spans.append([t, t])
        long_ = [s for s in spans if (s[1] - s[0]).total_seconds() + 600 >= HABIT_MIN_S]
        by_hour, by_wd = [0] * 24, [0] * 7
        in_long = {t for s, e in long_ for t in bs if s <= t <= e}
        for t in in_long:
            lt = t.astimezone(tz)
            by_hour[lt.hour] += 1
            by_wd[lt.weekday()] += 1
        ms = lambda x: int(x.timestamp() * 1000)                       # noqa: E731
        out[nm] = {"sessions": len(long_), "short": len(spans) - len(long_),
                   "hours": round(sum((e - s).total_seconds() + 600 for s, e in long_) / 3600, 2),
                   "days": len({s.astimezone(tz).date() for s, _ in long_}),
                   "tz": tzn, "country": cty, "by_hour": by_hour, "by_wd": by_wd,
                   "recent": [[ms(s), ms(e) + 600000] for s, e in long_[-12:]],
                   "last": ms(bs[-1]) + 600000 if bs else None, "ver": d["ver"][0]}
        if d["ver"][0] and _ver_key(d["ver"][0]) > _ver_key(latest):
            latest = d["ver"][0]
    return {"people": out, "latest_ver": latest, "since_days": 30}


def habits(post=None) -> dict:
    """近 30 天每个人的上课习惯（见 `habits_of`）。只读 AE 的数字列，不含文本。"""
    got = _ae_sqls(post, (("rows", _AE_HABITS),))
    if "error" in got or got.get("pending"):
        return got
    out = habits_of(got["rows"])
    try:                                                              # 仓库里的版本 = 已发布的最新版（面板跑在 main 上）
        out["repo_ver"] = (pathlib.Path(__file__).resolve().parent.parent / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        out["repo_ver"] = ""
    return out


SLOW = {"habits": habits, "cf": cf_summary, "r2": r2_ingest, "health": cf_health, "logs": worker_logs, "ae": ae_events,
        "ae310": ae_310, "reports": r2_reports, "soniox": soniox_account, "openrouter": openrouter_account}
FRESH_S = 55                                                          # 这么新：直接给
STALE_S = 15 * 60                                                     # 这么旧以内：先给旧的，后台重取；再旧就同步等
_CACHE: dict = {}                                                     # name → (取到的 monotonic 时刻, 结果)
_BUSY: set = set()                                                    # 正在后台重取的 name（同一个只起一个线程）
_LOCK = threading.Lock()


def _compute(name, fn, clock=time.monotonic):
    try:
        v = fn()
    except Exception as e:                                            # noqa: BLE001  后台线程里的异常没人接，变成一条错误
        v = {"error": f"{name} 出错：{type(e).__name__}"}
    with _LOCK:
        _BUSY.discard(name)
        if isinstance(v, dict) and not v.get("error"):
            _CACHE[name] = (clock(), v)
    return v


def cached(name, fn, fresh=False, clock=time.monotonic) -> dict:
    """返回 `fn()` 的结果，外加 `cache_age`（秒）。见 `SLOW` 上面那段。"""
    with _LOCK:
        hit = None if fresh else _CACHE.get(name)
        age = clock() - hit[0] if hit else None
        bg = hit is not None and FRESH_S <= age < STALE_S and name not in _BUSY
        if bg:
            _BUSY.add(name)
    if hit and age < STALE_S:
        if bg:
            threading.Thread(target=_compute, args=(name, fn, clock), daemon=True).start()
        return {**hit[1], "cache_age": round(age)}
    v = _compute(name, fn, clock)
    return {**v, "cache_age": 0} if isinstance(v, dict) else v


def prewarm() -> None:
    """服务一起来就在后台把慢的几个各取一遍 —— 第一次打开页面不用等。"""
    for name, fn in SLOW.items():
        threading.Thread(target=cached, args=(name, fn, True), daemon=True).start()


class Handler(BaseHTTPRequestHandler):
    server_version = "ClassLiveAdminUI"

    def log_message(self, fmt, *a):                                   # 别刷屏；只留错误
        if not str(a[1] if len(a) > 1 else "").startswith("2"):
            sys.stderr.write("  %s\n" % (fmt % a))

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(body)))
        self.send_header("cache-control", "no-store")
        self.send_header("x-content-type-options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _host_ok(self) -> bool:
        port = self.server.server_address[1]
        return (self.headers.get("Host") or "") in {f"{h}:{port}" for h in LOOPBACK_HOSTS}

    def do_GET(self):                                                 # noqa: N802
        if not self._host_ok():
            return self._json(403, {"error": "Host 不是本机环回（防 DNS rebinding）"})
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/index.html"):
            try:
                self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            except OSError as e:
                self._json(500, {"error": f"读不到模板 {PAGE.name}：{e}"})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):                                                # noqa: N802
        if not self._host_ok():
            return self._json(403, {"error": "Host 不是本机环回（防 DNS rebinding）"})
        path = urllib.parse.urlparse(self.path).path
        if not path.startswith("/api/"):
            return self._json(404, {"error": "not found"})
        if self.headers.get(GATE_HEADER) != "1":
            return self._json(403, {"error": "缺少 %s 头（本机页面对任意网站的第一道闸）" % GATE_HEADER})
        name = path[len("/api/"):]
        # cf：Cloudflare 用量 · r2：上传桶清单 · health：延迟/上游/部署/节点/站点 · logs：Workers Logs 里我们写的事件 ·
        # ae：Analytics Engine 逐请求事件 · ae310：3.10 的新量具（Soniox key / OpenRouter 快速通道 / 版本 / 笔记）·
        # reports：上传桶里 report.json 的只读摘要 · soniox / openrouter：平台账号额度。都不经 Worker。
        if name in SLOW:
            raw = self.rfile.read(int(self.headers.get("content-length") or 0))
            try:
                fresh = bool(json.loads(raw or b"{}").get("fresh"))
            except (ValueError, AttributeError):
                fresh = False
            return self._json(200, cached(name, SLOW[name], fresh))
        if name == "cfwin":                                           # 某一段时间的 Worker 延迟（抽屉里每个时段一行）
            raw = self.rfile.read(int(self.headers.get("content-length") or 0))
            try:
                b = json.loads(raw or b"{}")
                s, u = str(b["s"]), str(b["u"])
            except (ValueError, KeyError, TypeError):
                return self._json(400, {"error": "要 {s, u} 两个 ISO 时刻"})
            if not (re.fullmatch(ISO_Z, s) and re.fullmatch(ISO_Z, u)):
                return self._json(400, {"error": "时刻格式要 2026-10-14T17:00:00Z"})
            return self._json(200, cf_window(s, u))
        if name not in ALLOWED:
            return self._json(404, {"error": f"不认这个操作：{name}"})
        raw = self.rfile.read(int(self.headers.get("content-length") or 0))
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            return self._json(400, {"error": "body 不是 JSON"})
        if not isinstance(body, dict):
            return self._json(400, {"error": "body 要是对象"})
        try:
            return self._json(200, cl_admin.admin_post(name, body))
        except cl_admin.AdminError as e:
            return self._json(502, {"error": str(e)})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="通行码管理面板（只在本机环回上开）")
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--open", action="store_true", help="起来之后用默认浏览器打开")
    a = ap.parse_args(argv)
    if not PAGE.exists():
        sys.exit(f"缺模板 {PAGE}（和本脚本放同一个目录）")
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)         # ⚠️ 只 bind 环回
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    prewarm()
    print(f"通行码管理面板：{url}\n  令牌只在服务端读（{cl_admin.paths.asr_admin_token()}），不进浏览器。Ctrl-C 停。")
    if a.open:
        import subprocess
        subprocess.run(["open", url], check=False, timeout=10)        # 同 notice.py 的既有写法
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n停了。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
