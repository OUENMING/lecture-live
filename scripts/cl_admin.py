#!/usr/bin/env python3
"""一人一码的管理命令（Owen 本机跑；`docs/PLAN-passkey.md` §2 第 4 步）。

    ClassLive.app/Contents/MacOS/python scripts/cl_admin.py add liming            # 发一个码（120 天、2 台设备、不限额）
    ClassLive.app/Contents/MacOS/python scripts/cl_admin.py list                   # 所有码：谁、几台设备、到期、最后在线
    ClassLive.app/Contents/MacOS/python scripts/cl_admin.py stats [--day 2026-10-12] [--days 7]
                                                                   # 用量（转写秒 / token / 缓存命中 / 模型 / 版本）
    ClassLive.app/Contents/MacOS/python scripts/cl_admin.py quota                  # 系统额度快照（Groq v3 / turbo / 千问）
    ClassLive.app/Contents/MacOS/python scripts/cl_admin.py revoke liming          # 吊销（unrevoke 恢复）
    ClassLive.app/Contents/MacOS/python scripts/cl_admin.py renew liming [--days 120]
    ClassLive.app/Contents/MacOS/python scripts/cl_admin.py limit liming --tokens 2000000 --asr-s none --devices 3
    ClassLive.app/Contents/MacOS/python scripts/cl_admin.py delete cl-test-1778295d5eb5c1b0 --yes
                                                       # ⚠️ 不可逆：码 + 设备 + 全部用量一起没了

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
import paths                                                          # noqa: E402


class AdminError(Exception):
    """管理接口没做成（令牌不对 / Worker 报错 / 连不上）。

    CLI 把它翻成 `sys.exit`，本机页面（`cl_admin_ui.py`）把它翻成 502 —— **这个模块不自己退出**，
    否则复用它起服务时一次网络抖动就能把服务杀掉。
    """


def admin_post(path: str, body: dict | None = None, *, timeout: float = 20.0) -> dict:
    """打 Worker 的 `/v1/admin/<path>`，回解析好的 JSON。⚠️ **从不打印令牌**。"""
    import httpx
    token_file = paths.asr_admin_token()
    try:
        tok = token_file.read_text(encoding="utf-8").strip()
    except OSError as e:
        raise AdminError(f"读不到管理令牌 {token_file}（与 Worker 的 ADMIN_TOKEN 同值，权限 600）") from e
    base = asr_endpoint.endpoint().rsplit("/", 1)[0]
    try:
        r = httpx.post(f"{base}/admin/{path}", json=body if body is not None else {},
                       timeout=timeout, headers={"Authorization": f"Bearer {tok}"})
    except httpx.HTTPError as e:
        raise AdminError(f"连不上 {base}：{e}") from e
    if r.status_code == 401:
        raise AdminError("管理令牌不对（401）")
    try:
        out = r.json()
    except ValueError as e:
        raise AdminError(f"HTTP {r.status_code}（对方没回 JSON）") from e
    # ⚠️ **先看 body 再看状态码**：Worker 的未知路径回的是 404 + {"error":"not found"}，
    #    先按状态码抛的话这条友好提示永远走不到，拿到的是 httpx 的原始 traceback。
    if isinstance(out, dict) and out.get("error"):
        raise AdminError(str(out["error"]))
    if r.status_code >= 400:
        raise AdminError(f"HTTP {r.status_code}")
    return out


def _post(path: str, body: dict) -> dict:
    """CLI 用的薄包装：出错就退出（页面那条路用上面那个不退出的）。"""
    try:
        return admin_post(path, body)
    except AdminError as e:
        sys.exit(f"没做成：{e}")


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
    s = sub.add_parser("stats"); s.add_argument("--day"); s.add_argument("--days", type=int, default=1)
    sub.add_parser("quota")
    for c in ("revoke", "unrevoke"):
        sub.add_parser(c).add_argument("who")
    r = sub.add_parser("renew"); r.add_argument("who"); r.add_argument("--days", type=int, default=120)
    lim = sub.add_parser("limit"); lim.add_argument("who")
    lim.add_argument("--tokens"); lim.add_argument("--asr-s"); lim.add_argument("--devices", type=int)
    dl = sub.add_parser("delete"); dl.add_argument("code"); dl.add_argument("--yes", action="store_true")
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
        # Worker 侧 2026-10-14 起只回一种形状：{days: [{day, rows}], from, to}（单日就是长度 1）。
        out = _post("stats", {"day": ns.day} if ns.day else {"days": ns.days})
        days = out.get("days")
        if days is None:
            sys.exit("Worker 还是老版本（stats 只回单日形状）。先部署 server/asr-worker 再跑。")
        if not days:                       # 空区间（比如 from > to）——别 IndexError
            print("这段时间没有用量记录。")
            return 0
        span = days[0]["day"] if len(days) == 1 else f"{days[0]['day']} → {days[-1]['day']}"
        print(f"{span} 用量（只有数字，没有文本 / 音频）")
        multi = len(days) > 1
        # 钱按 DeepSeek 当期价目表自己乘（价位会变，这里不写死）：输入分「缓存命中 / 未命中」两档计价。
        lead = f"{'日期':<11} " if multi else ""
        print(lead + f"{'名字':<10} {'转写分钟':>8} {'段数':>6} {'翻译次数':>8} {'输入tok':>9} "
                     f"{'命中':>6} {'输出tok':>8} {'Jev次数':>7} {'Jev题数':>7} {'模型':<22} {'版本':<10}")
        for d in days:
            for u in d["rows"]:
                rate = f"{u['cache_hit'] / u['tokens_in']:.0%}" if u["tokens_in"] else "—"
                # models / vers 是 2026-10-14 加的；老记录没有这两键
                models = " ".join(f"{k}×{v}" for k, v in sorted((u.get("models") or {}).items())) or "—"
                vers = " ".join(f"{k}×{v}" for k, v in sorted((u.get("vers") or {}).items())) or "—"
                pre = f"{d['day']:<11} " if multi else ""
                print(pre + f"{u['name']:<10} {u['asr_s'] / 60:>8.1f} {u['asr_reqs']:>6} {u['llm_reqs']:>8} "
                            f"{u['tokens_in']:>9} {rate:>6} {u['tokens_out']:>8} "
                            f"{u.get('jev_reqs', 0):>7} {u.get('jev_q', 0):>7} {models:<22} {vers:<10}")
    elif ns.cmd == "quota":
        q = _post("quota", {})
        c = q["caps"]
        print(f"{q['day']} 额度快照（UTC）· 上限：{c['reqs_day']} 次/日 · {c['reqs_min']} 次/分 · "
              f"{c['s_hour']:.0f} 秒/时 · {c['s_day']:.0f} 秒/日 · 千问 {c['qwen_day']} 段/日")
        for key, label in (("v3", "Groq v3"), ("turbo", "Groq turbo")):
            b = q[key]
            print(f"  {label:<11} 今日 {b['used_reqs']:>5}/{c['reqs_day']} 次 · {b['used_s']:>7.0f}/{c['s_day']:.0f} 秒 "
                  f"· 本分钟 {b['min_reqs']}/{c['reqs_min']} · 本小时 {b['hour_s']:.0f} 秒")
            g = b.get("groq")           # Groq 回包报的剩余（推算到此刻；Worker 拿它判，见 quota-core.js）
            if g:
                ago = max(0, (q["now"] - g["reported_at"]) / 60000)
                print(f"  {'':<11} Groq 报：剩 {g['req_left']}/{g['req_lim']} 次（24 h 滚动）· "
                      f"剩 {g['aud_left']}/{g['aud_lim']} 秒（1 h 滚动）· {ago:.0f} 分钟前报的")
        qw = q["qwen"]
        tail = f" · 已耗尽 {', '.join(qw['exhausted'])}" if qw["exhausted"] else ""
        print(f"  {'千问':<11} 今日 {qw['used']:>5}/{qw['max']} 段{tail}")
    elif ns.cmd == "delete":
        # 两道闸：必须给完整码（不给名字，名字会歧义），必须显式 --yes。
        # 这一条是**唯一不可逆**的管理操作 —— 用量记录一起删，删完补不回来。
        if not ns.code.startswith("cl-"):
            sys.exit("delete 只认完整码（cl-…），不认名字 —— 名字可能对应多条。")
        if not ns.yes:
            sys.exit(f"这一步不可逆（码 + 设备 + 用量一起删）。确认了就把 --yes 加上：\n"
                     f"    cl_admin.py delete {ns.code} --yes")
        out = _post("delete", {"code": ns.code, "yes": True})
        print(f"✅ 删掉了 {out['name']}（{out['code']}）：{out['removed']} 个键，其中用量 {out['usage_days']} 天。")
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
