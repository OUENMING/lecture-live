#!/usr/bin/env python3
"""网页版测试数据的拉取工具（W20）：把 Worker 上收的遥测事件 / 课堂录音分段拉到本机。

    ClassLive.app/Contents/MacOS/python scripts/cl_web_data.py list [--day 2026-11-20] [--name amy]
    ClassLive.app/Contents/MacOS/python scripts/cl_web_data.py pull --day 2026-11-20 [--name amy] [--audio]
    ClassLive.app/Contents/MacOS/python scripts/cl_web_data.py report --day 2026-11-20 [--name amy] [--session web-…]

`report`：先 pull 遥测，再按节课汇总（桌面 report.json 的网页版）：时长 / 句数 / 延迟 / 翻译失败 /
API 失败 / 断线 / 后台与断网时长 / 报错 —— 一节一段，`--session` 只看一节并列出它的报错与异常事件。

落盘到 `~/.classlive/web-data/<相同的 R2 key>`（`telemetry/<日>/<码名>/<会话>/…ndjson`、
`audio/<码名>/<会话>/<段>.webm`）；已存在且大小相同的跳过。`--day` 只筛遥测（录音键里没有日期）。
管理令牌读 `~/.classlive/asr-admin-token`（600，与 Worker 的 `ADMIN_TOKEN` 同值）；**本脚本从不打印它**。
⚠️ 会联网（打 `/v1/admin/web/{list,get}`）。
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import asr_endpoint                                                   # noqa: E402
import paths                                                          # noqa: E402
from cl_admin import AdminError, admin_post                            # noqa: E402

PREFIXES = ("telemetry/", "audio/")   # 服务端也挡一遍；这里先自己挡住拼错的 --day / --name


def _want(key: str, day: str | None, name: str | None) -> bool:
    """键的形状：`telemetry/<日>/<码名>/…`、`audio/<码名>/…`。"""
    parts = key.split("/")
    if parts[0] == "telemetry":
        return len(parts) >= 3 and (day is None or parts[1] == day) \
            and (name is None or parts[2] == name)
    if parts[0] == "audio":
        return len(parts) >= 2 and (name is None or parts[1] == name)
    return False


def _scan(prefix: str) -> list[dict]:
    """按前缀分页列全（服务端 1000/批，cursor 是上一页最后一个 key）。"""
    out: list[dict] = []
    cursor = None
    for _ in range(200):                       # 20 万对象的天花板：服务端不认 cursor 时不会死循环
        page = admin_post("web/list", {"prefix": prefix} if cursor is None
                          else {"prefix": prefix, "cursor": cursor})
        out.extend(page.get("objects") or [])
        cursor = page.get("cursor")
        if not cursor:
            break
    return out


def _fetch(key: str) -> bytes:
    """取一个对象的原文。⚠️ 令牌只进请求头，出错信息里不带它。"""
    import httpx
    token = paths.asr_admin_token().read_text(encoding="utf-8").strip()
    base = asr_endpoint.endpoint().rsplit("/", 1)[0]
    r = httpx.post(f"{base}/admin/web/get", json={"key": key}, timeout=60.0,
                   headers={"Authorization": f"Bearer {token}"})
    if r.status_code >= 400:
        raise AdminError(f"HTTP {r.status_code}")
    return r.content


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="拉网页版测试数据（遥测事件 / 课堂录音分段）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for cmd in ("list", "pull", "report"):
        p = sub.add_parser(cmd)
        p.add_argument("--day", help="UTC 日期 YYYY-MM-DD（只筛遥测，录音键里没有日期）")
        p.add_argument("--name", help="码名（键里那一段）")
        if cmd == "pull":
            p.add_argument("--audio", action="store_true", help="连录音分段一起拉（默认只拉遥测）")
        if cmd == "report":
            p.add_argument("--session", help="只看这一节（本机会话号 web-…）")
    ns = ap.parse_args(argv)
    if ns.cmd == "report":
        ns.audio = False

    try:
        rows = [o for prefix in PREFIXES
                if ns.cmd == "list" or prefix == "telemetry/" or ns.audio
                for o in _scan(prefix) if _want(o["key"], ns.day, ns.name)]
    except AdminError as e:
        sys.exit(f"没做成：{e}")

    if ns.cmd == "list":
        for o in sorted(rows, key=lambda o: o["key"]):
            print(f"{o.get('size', 0):>9}  {o.get('uploaded') or '—'}  {o['key']}")
        print(f"共 {len(rows)} 个对象。" + ("（录音分段用 `pull --audio` 下载）" if any(
            o["key"].startswith("audio/") for o in rows) else ""))
        return 0

    root = paths.STATE_ROOT / "web-data"
    got = skipped = failed = 0
    for o in rows:
        key = o["key"]
        dest = root / key
        if dest.exists() and dest.stat().st_size == o.get("size"):
            skipped += 1
            continue
        try:
            data = _fetch(key)
        except (AdminError, OSError) as e:
            print(f"跳过 {key}：{e}", file=sys.stderr)      # 单条失败不影响整批
            failed += 1
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        got += 1
    print(f"下载 {got} 个，跳过（已存在且大小相同）{skipped} 个，失败 {failed} 个 -> {root}")
    if ns.cmd == "report":
        report(root, [o["key"] for o in rows if o["key"].startswith("telemetry/")],
               only=getattr(ns, "session", None))
    return 0


# 逐条列出的「异常」事件类型（report --session）
_BAD = {"error", "rejection", "console", "ws_error", "ws_close", "key_failed", "notice",
        "rec_upload_failed", "collector_error", "offline", "dropped"}


def _load(root: pathlib.Path, keys: list[str], only: str | None = None) -> dict:
    """本机已拉下的遥测 → {(码名, 会话): [事件…按时间]}。"""
    import json
    from collections import defaultdict
    by: dict[tuple, list] = defaultdict(list)
    for k in keys:
        parts = k.split("/")                      # telemetry/<日>/<码名>/<会话>/<文件>
        if len(parts) < 5 or (only and parts[3] != only):
            continue
        try:
            text = (root / k).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for ln in text.splitlines():
            try:
                by[(parts[2], parts[3])].append(json.loads(ln))
            except ValueError:
                pass
    for evs in by.values():
        evs.sort(key=lambda e: (e.get("t", 0), e.get("n", 0)))
    return dict(by)


def bad_events(evs: list) -> list:
    """一节课里值得看的异常事件（报错 / 断线 / 失败的请求与翻译 …）。"""
    return [e for e in evs if e.get("type") in _BAD
            or (e.get("type") == "api" and not e.get("ok"))
            or (e.get("type") == "tr" and e.get("outcome") != "ok")]


def summarize(name: str, sid: str, evs: list) -> dict:
    """一节课的汇总（看板 / report 共用；只取数字与枚举，不含字幕原文）。"""
    rep = next((e for e in reversed(evs) if e.get("type") == "class_report"), None)
    env = next((e for e in evs if e.get("type") == "env"), {})
    op = next((e for e in evs if e.get("type") == "class_open"), {})
    bad = bad_events(evs)
    kinds: dict = {}
    for e in bad:
        kinds[e.get("type")] = kinds.get(e.get("type"), 0) + 1
    return {
        "name": name, "session": sid, "events": len(evs),
        "first_t": evs[0].get("t") if evs else None, "last_t": evs[-1].get("t") if evs else None,
        "build": op.get("build") or (rep or {}).get("build"), "ver": op.get("ver") or env.get("ver"),
        "ua": str(env.get("ua") or "")[:200], "tz": env.get("tz"), "net": env.get("net"),
        "platform": env.get("platform"), "mobile": env.get("mobile"),
        "report": rep and {k: v for k, v in rep.items() if k not in ("t", "mono", "n", "type")},
        "bad": len(bad), "bad_kinds": kinds,
        # 刷新 / 重开接回：同一节课被打开过几次（class_open 每页一条）、其中几次是「接回」
        "pages": sum(1 for e in evs if e.get("type") == "class_open"),
        "resumed": sum(1 for e in evs if e.get("type") == "resumed"),
    }


# 老版本（接回上线前）刷新一次就开一节新课，旧的那节没下课总账、看板上像「还在上」。
# 判据：同一个人（码名）、旧的没有 class_report、新的在旧的最后一条事件之后这么久之内开始 → 旧的被新的接替。
REPLACE_GAP_S = 180


def mark_replaced(rows: list) -> None:
    """就地给被刷新接替的会话加 `replaced_by`（新会话号）。只看现有字段，不碰原始事件。"""
    by_name: dict = {}
    for r in rows:
        by_name.setdefault(r.get("name"), []).append(r)
    for rs in by_name.values():
        for old in rs:
            if old.get("report") or old.get("first_t") is None or old.get("last_t") is None:
                continue
            nxt = [n for n in rs if n is not old and n.get("first_t") is not None
                   and n["first_t"] > old["first_t"]
                   and old["last_t"] - 5000 <= n["first_t"] <= old["last_t"] + REPLACE_GAP_S * 1000]
            if nxt:
                old["replaced_by"] = min(nxt, key=lambda n: n["first_t"])["session"]


def sessions_summary(days: int = 2, *, max_sessions: int = 60) -> dict:
    """看板用：最近 `days` 天（UTC）每节网页课一行。会先把缺的遥测拉到本机缓存（同 pull）。"""
    import datetime as dt
    today = dt.datetime.now(dt.timezone.utc).date()
    wanted = {(today - dt.timedelta(days=i)).isoformat() for i in range(days)}
    rows = [o for o in _scan("telemetry/") if o["key"].split("/")[1:2] and o["key"].split("/")[1] in wanted]
    root = paths.STATE_ROOT / "web-data"
    for o in rows:
        dest = root / o["key"]
        if dest.exists() and dest.stat().st_size == o.get("size"):
            continue
        try:
            data = _fetch(o["key"])
        except (AdminError, OSError):
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    by = _load(root, [o["key"] for o in rows])
    out = [summarize(n, s, e) for (n, s), e in by.items()]
    mark_replaced(out)
    out.sort(key=lambda r: r.get("first_t") or 0, reverse=True)
    return {"days": sorted(wanted), "sessions": out[:max_sessions], "total": len(out)}


def session_detail(name: str, sid: str, *, limit: int = 2000) -> dict:
    """看板用：一节课的全部事件（本机缓存里已有的；先跑过 sessions_summary 才有）。"""
    root = paths.STATE_ROOT / "web-data"
    keys = [str(p.relative_to(root)) for p in (root / "telemetry").glob(f"*/{name}/{sid}/*.ndjson")]
    evs = _load(root, keys).get((name, sid), [])
    return {"summary": summarize(name, sid, evs) if evs else None,
            "bad": bad_events(evs)[:500], "events": evs[:limit], "truncated": len(evs) > limit}


def report(root: pathlib.Path, keys: list[str], *, only: str | None = None) -> None:
    import json
    by = _load(root, keys, only)
    if not by:
        print("没有匹配的遥测。")
        return
    for (name, sid), evs in sorted(by.items(), key=lambda kv: min(e.get("t", 0) for e in kv[1])):
        rep = next((e for e in reversed(evs) if e.get("type") == "class_report"), None)
        env = next((e for e in evs if e.get("type") == "env"), {})
        op = next((e for e in evs if e.get("type") == "class_open"), {})
        bad = bad_events(evs)
        t0 = evs[0].get("t", 0)
        print(f"\n== {name} · {sid} · {len(evs)} 条 · 构建 {op.get('build') or rep and rep.get('build') or '旧包'}")
        print(f"   浏览器 {str(env.get('ua') or '')[:90]}")
        print(f"   时区 {env.get('tz')} · 网络 {env.get('net') or env.get('connection') or '—'}")
        if rep:
            f = rep.get("final") or {}
            print(f"   {rep.get('dur_s')} s · {rep.get('sentences')} 句（失败 {rep.get('sent_failed')}）"
                  f" · 说完→中文 p50 {rep.get('total_p50')} / p90 {rep.get('total_p90')} ms"
                  f" · 首字 p50 {f.get('first_p50')} ms · 重试 {f.get('retry')} · 翻译错 {f.get('err')}")
            print(f"   API {rep.get('api', {}).get('n')} 次失败 {rep.get('api', {}).get('fail')} {rep.get('api', {}).get('by')}"
                  f" · 识别连接 {rep.get('ws')} · 后台 {rep.get('hidden_s')} s · 断网 {rep.get('offline_s')} s"
                  f" · console {rep.get('console')} · ❓ {len(rep.get('marks') or [])}")
            sy = rep.get("sync") or {}
            print(f"   同步 {sy.get('phase')} acked {sy.get('acked')} {sy.get('message') or ''}")
        else:
            print("   （没有 class_report：旧包、没点下课，或下课时页面已关）")
        print(f"   异常 {len(bad)} 条" + ("" if only or not bad else "（--session 看明细）"))
        if only:
            for e in bad[:200]:
                d = {k: v for k, v in e.items() if k not in ("t", "mono", "n", "type")}
                print(f"   +{(e.get('t', 0) - t0) / 1000:7.1f}s {e.get('type'):<16} {json.dumps(d, ensure_ascii=False)[:220]}")


if __name__ == "__main__":
    sys.exit(main())
