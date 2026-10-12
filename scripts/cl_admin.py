#!/usr/bin/env python3
"""一人一码的管理命令（Owen 本机跑；`docs/PLAN-passkey.md` §2 第 4 步）。

    ClassLive.app/Contents/MacOS/python scripts/cl_admin.py add liming            # 发一个码（120 天、2 台设备、不限额）
    ClassLive.app/Contents/MacOS/python scripts/cl_admin.py list                   # 所有码：谁、几台设备、到期、最后在线
    ClassLive.app/Contents/MacOS/python scripts/cl_admin.py stats [--day 2026-10-12]   # 当天用量（转写秒 / token / 缓存命中）
    ClassLive.app/Contents/MacOS/python scripts/cl_admin.py revoke liming          # 吊销（unrevoke 恢复）
    ClassLive.app/Contents/MacOS/python scripts/cl_admin.py renew liming [--days 120]
    ClassLive.app/Contents/MacOS/python scripts/cl_admin.py limit liming --tokens 2000000 --asr-s none --devices 3

名字对应多个码时（同名重发过）改用码本身。管理令牌读 `~/.classlive/asr-admin-token`（600），
与 Worker 的 `wrangler secret put ADMIN_TOKEN` 同值；**本脚本从不打印它**。
⚠️ 会联网（打 Worker 的 `/v1/admin/*`）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import asr_endpoint                                                   # noqa: E402

TOKEN_FILE = pathlib.Path("~/.classlive/asr-admin-token").expanduser()


def _post(path: str, body: dict) -> dict:
    import httpx
    try:
        tok = TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        sys.exit(f"读不到管理令牌 {TOKEN_FILE}（与 Worker 的 ADMIN_TOKEN 同值，权限 600）")
    base = asr_endpoint.endpoint().rsplit("/", 1)[0]
    r = httpx.post(f"{base}/admin/{path}", json=body, timeout=20,
                   headers={"Authorization": f"Bearer {tok}"})
    if r.status_code == 401:
        sys.exit("管理令牌不对（401）")
    r.raise_for_status()
    out = r.json()
    if isinstance(out, dict) and out.get("error"):
        sys.exit(f"没做成：{out['error']}")
    return out


def _who(x: str) -> dict:
    return {"code": x} if x.startswith("cl-") else {"name": x}


def _date(ms) -> str:
    return dt.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d") if ms else "—"


def _limit_arg(v: str | None):
    if v is None:
        return None
    return None if v.lower() in ("none", "null", "不限") else float(v)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="一人一码管理")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add"); a.add_argument("name"); a.add_argument("--days", type=int); a.add_argument("--devices", type=int)
    sub.add_parser("list")
    s = sub.add_parser("stats"); s.add_argument("--day")
    for c in ("revoke", "unrevoke"):
        sub.add_parser(c).add_argument("who")
    r = sub.add_parser("renew"); r.add_argument("who"); r.add_argument("--days", type=int, default=120)
    lim = sub.add_parser("limit"); lim.add_argument("who")
    lim.add_argument("--tokens"); lim.add_argument("--asr-s"); lim.add_argument("--devices", type=int)
    ns = ap.parse_args(argv)

    if ns.cmd == "add":
        body = {"name": ns.name}
        if ns.days:
            body["days"] = ns.days
        if ns.devices:
            body["max_devices"] = ns.devices
        out = _post("add", body)
        print(f"✅ {out['name']} 的通行码（发给他，粘进「翻译引擎 → 填 key」）：\n\n    {out['code']}\n")
        print(f"   到期 {_date(out['expires'])} · 最多 {out['max_devices']} 台设备 · 不限额")
    elif ns.cmd == "list":
        rows = _post("list", {})["codes"]
        print(f"{'名字':<10} {'状态':<6} {'设备':<6} {'到期':<11} {'最后在线':<11} 码")
        for c in sorted(rows, key=lambda c: c["name"]):
            st = "吊销" if c["revoked"] else "有效"
            print(f"{c['name']:<10} {st:<6} {c['devices']}/{c['max_devices']:<4} {_date(c['expires']):<11} "
                  f"{_date(c['last_seen']):<11} {c['code']}")
    elif ns.cmd == "stats":
        out = _post("stats", {"day": ns.day} if ns.day else {})
        print(f"{out['day']} 用量（只有数字，没有文本 / 音频）")
        # 钱按 DeepSeek 当期价目表自己乘（价位会变，这里不写死）：输入分「缓存命中 / 未命中」两档计价。
        print(f"{'名字':<10} {'转写分钟':>8} {'段数':>6} {'翻译次数':>8} {'输入tok':>9} {'命中':>6} {'输出tok':>8} {'Jev次数':>7} {'Jev题数':>7}")
        for u in out["rows"]:
            rate = f"{u['cache_hit'] / u['tokens_in']:.0%}" if u["tokens_in"] else "—"
            print(f"{u['name']:<10} {u['asr_s'] / 60:>8.1f} {u['asr_reqs']:>6} {u['llm_reqs']:>8} "
                  f"{u['tokens_in']:>9} {rate:>6} {u['tokens_out']:>8} "
                  f"{u.get('jev_reqs', 0):>7} {u.get('jev_q', 0):>7}")   # 3.9.4 之前的老记录没有这两列
    elif ns.cmd in ("revoke", "unrevoke"):
        out = _post(ns.cmd, _who(ns.who))
        print(f"✅ {out['name']}：{'已吊销' if out['revoked'] else '已恢复'}")
    elif ns.cmd == "renew":
        out = _post("renew", {**_who(ns.who), "days": ns.days})
        print(f"✅ {out['name']}：到期改为 {_date(out['expires'])}")
    elif ns.cmd == "limit":
        body = _who(ns.who)
        if ns.tokens is not None:
            body["daily_tokens"] = _limit_arg(ns.tokens)
        if ns.asr_s is not None:
            body["daily_asr_s"] = _limit_arg(ns.asr_s)
        if ns.devices:
            body["max_devices"] = ns.devices
        out = _post("limit", body)
        print(f"✅ {out['name']}：每天 token {out['daily_tokens'] or '不限'} · 转写秒 {out['daily_asr_s'] or '不限'}"
              f" · 设备 {out['max_devices']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
