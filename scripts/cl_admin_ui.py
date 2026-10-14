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
import urllib.parse
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


def r2_ingest(get=None, sessions_dir=None, ledger=None) -> dict:
    """上传桶的**实时**清单（逐对象列，不是 Analytics —— `r2StorageAdaptiveGroups` 是每 10–30 分钟的快照，
    2026-10-14 实测比真列表少 7 个对象 / 58 MB）。按会话前缀分组，并标出**哪些不是本机的课**：
    本机 `sessions/` 里的会话名、上传账本里的会话名逐个算 `session_key`，对不上的就是别人的。

    只回数字、前缀、文件名（`report.json` / `audio.003.wav` 这种）和本机自己的会话名；token 从不进响应。
    """
    import hashlib
    home = pathlib.Path.home() / ".classlive"
    try:
        tok = (home / "cf-analytics-token").read_text(encoding="utf-8").strip()
        acct = (home / "cf-account-id").read_text(encoding="utf-8").strip()
    except OSError:
        return {"error": "没配 Cloudflare token（~/.classlive/cf-analytics-token）"}
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
            return {"error": f"列不了上传桶：{type(e).__name__}"}
        if not j.get("success", True) or j.get("errors"):
            return {"error": "Cloudflare 拒了列桶：" + "；".join(str(x.get("message", ""))[:120] for x in (j.get("errors") or [])[:2])}
        objs += j.get("result") or []
        info = j.get("result_info") or {}
        cursor = info.get("cursor")
        if not info.get("is_truncated") or not cursor:
            break
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
#: 子请求的主机名 → 人话。空主机名 = Durable Object（Worker 调自己的 DO，不出 Cloudflare）。
UPSTREAMS = {"api.groq.com": "Groq", "api.deepseek.com": "DeepSeek", "dashscope-intl.aliyuncs.com": "千问（国际）",
             "api.typesafe.ai": "TypeSafe Jev", "": "Durable Object"}
_HEALTH_Q = """query($a:String!,$z:String!,$s:Time!,$u:Time!,$sc:String!){viewer{
 accounts(filter:{accountTag:$a}){
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
   count dimensions{clientRequestHTTPHost edgeResponseStatus clientCountryName}}
  path:httpRequestsAdaptiveGroups(limit:15,orderBy:[count_DESC],filter:{datetime_geq:$s,datetime_leq:$u,edgeResponseStatus_geq:400}){
   count dimensions{clientRequestHTTPHost clientRequestPath edgeResponseStatus}}}}}"""


def _zone_id(tok, get=None):
    """bldcam.page 的 zone ID（同一个只读 token 能列 zone）。拿不到回空串 —— 面板只是少了站点那张表。"""
    if get is None:
        def get(url, headers):
            import httpx
            return httpx.get(url, headers=headers, timeout=20).json()
    try:
        j = get(f"https://api.cloudflare.com/client/v4/zones?name={ZONE}", {"Authorization": f"Bearer {tok}"})
        return str((j.get("result") or [{}])[0].get("id") or "")
    except Exception:                                                 # noqa: BLE001
        return ""


def cf_health(post=None, get=None, now=None, window=None) -> dict:
    """近 24 h（或 `window=(起, 止)` 的 ISO 区间）的服务健康 —— 全是 Cloudflare 自己的数，本机直接问：

      lat       ASR Worker 每小时的 wallTime / requestDuration p50·p95（**毫秒**；GraphQL 给的是微秒）
      versions  每个部署版本在窗口里**第一次 / 最后一次**出现的分钟（= 部署标记；窗口起点之前就在的标 `before`）
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
    try:
        j = (post or _post_json)(CF_GQL, {"Authorization": f"Bearer {tok}"}, {"query": _HEALTH_Q, "variables": v})
    except Exception as e:                                            # noqa: BLE001
        return {"error": f"连不上 Cloudflare：{type(e).__name__}"}
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
    start_min = s[:16] + ":00Z"
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
            h = hosts.setdefault(d["clientRequestHTTPHost"], {"host": d["clientRequestHTTPHost"], "total": 0,
                                                              "s2": 0, "s3": 0, "s4": 0, "s5": 0, "countries": {}})
            n = int(r["count"])
            h["total"] += n
            h["s%d" % min(5, max(2, int(d["edgeResponseStatus"] or 0) // 100))] += n
            c = d.get("clientCountryName") or "?"
            h["countries"][c] = h["countries"].get(c, 0) + n
        for h in hosts.values():
            h["countries"] = sorted(h["countries"].items(), key=lambda kv: -kv[1])[:5]
        zone = {"hosts": sorted(hosts.values(), key=lambda h: -h["total"]),
                "bad_paths": [{"host": r["dimensions"]["clientRequestHTTPHost"], "path": r["dimensions"]["clientRequestPath"][:120],
                               "status": r["dimensions"]["edgeResponseStatus"], "n": int(r["count"])} for r in zz.get("path") or []]}
    return {"since": s, "until": u, "lat": lat, "versions": versions,
            "upstream": sorted(up.values(), key=lambda r: -r["total"]), "colos": colos, "zone": zone,
            "zone_error": "" if zone else "读不到 bldcam.page 的站点数（token 没有 Zone Analytics 权限，或 zone 没找到）"}


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

    try:
        calc = q({"view": "calculations", "parameters": {
            "datasets": ["cloudflare-workers"],
            "filters": [svc, {"key": "provider", "operation": "exists", "type": "string"}],
            "calculations": [{"operator": op, "key": "ms", "keyType": "number"} for op in ("median", "p90", "max")],
            "groupBys": [{"type": "string", "value": "model"}]}})
        out: dict = {}
        for c in calc.get("calculations") or []:
            op = str(c.get("calculation", "")).split("(")[0]
            for ag in c.get("aggregates") or []:
                r = out.setdefault(ag.get("groupKey") or "?", {"model": ag.get("groupKey") or "?", "count": 0})
                r[op] = round(float(ag.get("value") or 0))
                r["count"] = int(ag.get("count") or 0)
        events = []
        for flt in ({"key": "denied", "operation": "exists", "type": "string"},
                    {"key": "result", "operation": "eq", "type": "string", "value": "all_failed"},
                    {"key": "quota", "operation": "exists", "type": "string"}):
            ev = q({"view": "events", "limit": 30, "parameters": {"datasets": ["cloudflare-workers"], "filters": [svc, flt]}})
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


def ae_events(post=None) -> dict:
    """逐请求事件的近 24 h 汇总：每个码 × 路径的请求数 / p50·p95 毫秒 / 出错数 / 音频秒 / token；
    码 × 国家 × 节点 × 客户端版本；最近 30 条 ≥400 的请求。数据集还不存在（没开 Analytics Engine / 还没部署）→ 回 `pending`。"""
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
    out = {}
    for key, sql in (("by_code", _AE_BY_CODE), ("geo", _AE_GEO), ("bad", _AE_BAD)):
        try:
            code, j = post(url, {"Authorization": f"Bearer {tok}"}, sql)
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
    for r in out["bad"]:
        r.pop("ip", None)
    return out


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
        if name == "cf":                                              # 本机直接问 Cloudflare，不经 Worker
            return self._json(200, cf_summary())
        if name == "r2":                                              # 上传桶实时清单（同上，本机直接问）
            return self._json(200, r2_ingest())
        if name == "health":                                          # 延迟 / 上游 / 部署 / 节点 / 站点（本机直接问）
            return self._json(200, cf_health())
        if name == "logs":                                            # Workers Logs 里我们自己写的事件
            return self._json(200, worker_logs())
        if name == "ae":                                              # Analytics Engine 逐请求事件
            return self._json(200, ae_events())
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
