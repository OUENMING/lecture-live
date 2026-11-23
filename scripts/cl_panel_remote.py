#!/usr/bin/env python3
"""通行码面板的**远程只读版** —— 跑在 VPS（bldcam）上，给 Muse / 手机随时看（2026-11-22）。

    python3 scripts/cl_panel_remote.py --port 8772                 # 起服务（systemd: cl-panel-remote）
    python3 scripts/cl_panel_remote.py --import-local < local.json  # Mac 推来的本机会话名 + 上传台账

与本机面板 `cl_admin_ui.py` 的关系（2026-11-23 Owen 定：数据与本机面板**完全一致**，只是不能写）：
  · 复用它的全部取数函数和读操作（`ui.read_op`，同一份代码），`/ui` 直接给同一页 `cl_admin_ui.html`。
  · 管理令牌用的是 Worker 的 **ADMIN_RO_TOKEN**（VPS 的 `~/.classlive/asr-admin-token` 里放的就是它）：
    就算这里被打穿，Worker 也只认读路径，吊销 / 加码 / 改额度一律 403（这里先挡一道，回 `WRITE_MSG`）。
  · 对外鉴权：`Authorization: Bearer <key>`、`?key=<key>` 或 cookie（`/ui?key=` 换发），key 在
    `~/.classlive/remote-panel-key`（600）。面板页发的 POST 还要带 `X-ClassLive-Admin: 1`（同本机面板的 CSRF 闸）。
  · 完整通行码**照给**（同本机面板）。要退回只给前缀：`touch ~/.classlive/remote-panel-mask`（不用重启）。
  · 课堂原文 / 交互版笔记（`/raw/<id>`、`/note/<id>`）**不脱敏**（Owen 2026-11-22 定）。
  · 本机会话名 / 上传台账只在 Mac 推上来时才有（`scripts/cl_panel_push.sh`，launchd 每 10 分钟）；
    Mac 合盖期间停在最后一次推送，`/digest.md` 顶上写着推送时刻。

判据：`tests/test_cl_panel_remote.py`（假上游，不联网）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import hmac
import json
import pathlib
import re
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import cl_admin                                                      # noqa: E402
import cl_admin_ui as ui                                             # noqa: E402

HOME = pathlib.Path.home() / ".classlive"
KEY_FILE = "remote-panel-key"
LOCAL_FILE = "remote-local.json"                                      # Mac 推来的那份（原样留一份，带推送时刻）
SESSIONS_DIR = pathlib.Path(__file__).resolve().parent.parent / "sessions"   # 只放空的标记文件（会话名）

#: 只读的 Worker 管理路径（与 Worker 的 ADMIN_RO_PATHS 对齐；多放了也没用 —— Worker 会 403）。
READ_ADMIN = {"list", "stats", "quota", "minutes", "devices", "routes/get", "terms/stats"}
NAME_RE = re.compile(r"[a-z0-9]{1,16}")


# ---------- 遮码 ----------
_CODE_RE = re.compile(r"\b(cl-[a-z0-9]{1,16}-)([0-9a-f]{4})[0-9a-f]{4,}\b")


def mask(obj):
    """递归把完整通行码换成前缀。字符串里嵌着的码也遮（日志 / 报告里可能有）。"""
    if isinstance(obj, dict):
        return {k: mask(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [mask(v) for v in obj]
    if isinstance(obj, str):
        return _CODE_RE.sub(lambda m: m.group(1) + m.group(2) + "…", obj)
    return obj


# ---------- Mac 推来的本机数据 ----------
def import_local(raw: bytes, home: pathlib.Path = HOME, sessions_dir: pathlib.Path = SESSIONS_DIR,
                 now: dt.datetime | None = None) -> dict:
    """`{"sessions": [会话名…], "ledger": {...}}` → 台账写 `~/.classlive/upload-ledger.json`、
    会话名写成 `sessions/` 下的空文件（`r2_ingest` / `r2_reports` 按文件名认「这节是我的」）。"""
    if sessions_dir == SESSIONS_DIR and any(SESSIONS_DIR.glob("*.md")):
        # ⚠️ 这是真 `sessions/`（Mac 上的课）—— 只追加的用户数据，绝不往里塞标记文件。只该在 VPS 上跑。
        raise SystemExit("拒绝：sessions/ 里有真课（*.md），--import-local 只在 VPS 上跑")
    d = json.loads(raw or b"{}")
    if not isinstance(d, dict):
        raise ValueError("要一个对象")
    names = [str(n) for n in d.get("sessions") or [] if re.fullmatch(r"[\w.\-]{1,120}", str(n))]
    ledger = d.get("ledger") if isinstance(d.get("ledger"), dict) else {}
    home.mkdir(parents=True, exist_ok=True)
    sessions_dir.mkdir(parents=True, exist_ok=True)
    have = {p.name for p in sessions_dir.iterdir()}
    for n in names:
        if n not in have:
            (sessions_dir / n).touch()
    (home / "upload-ledger.json").write_text(json.dumps(ledger, ensure_ascii=False), encoding="utf-8")
    at = (now or dt.datetime.now(dt.timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    (home / LOCAL_FILE).write_text(json.dumps({"pushed_at": at, "sessions": names, "ledger": ledger},
                                              ensure_ascii=False), encoding="utf-8")
    return {"pushed_at": at, "sessions": len(names), "ledger": len(ledger)}


def local_state(home: pathlib.Path = HOME) -> dict:
    try:
        return json.loads((home / LOCAL_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"pushed_at": "", "sessions": [], "ledger": {}}


# ---------- 摘要 ----------
def _ms(v) -> str:
    if not v:
        return "—"
    try:
        return dt.datetime.fromtimestamp(int(v) / 1000, dt.timezone.utc).strftime("%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return "—"


def _sec(fn):
    try:
        return fn()
    except Exception as e:                                            # noqa: BLE001 —— 一节坏了不拖垮整页
        return {"error": f"{type(e).__name__}: {str(e)[:120]}"}


def digest(fetch=None, now=None, home: pathlib.Path | None = None) -> str:
    """一页 Markdown：码 / 今日用量 / 7 天 / 额度 / Worker 健康 / 上传的课 / 队列。`fetch(name)` 测试注入。"""
    if fetch is None:
        def fetch(name):
            if name in READ_ADMIN:
                return cl_admin.admin_post(name, {})
            return ui.cached(name, ui.SLOW[name])
    now = now or dt.datetime.now(dt.timezone.utc)
    out = [f"# ClassLive 面板摘要（只读）", "",
           f"生成于 {now.strftime('%Y-%m-%d %H:%M UTC')}"]
    loc = local_state(home or HOME)
    out.append(f"Mac 本机数据最后推送：{loc.get('pushed_at') or '从没推过'}（本机会话 {len(loc.get('sessions') or [])} 个，"
               f"上传台账 {len(loc.get('ledger') or {})} 条）")

    lst = _sec(lambda: fetch("list"))
    stats = _sec(lambda: fetch("stats"))
    today = {}
    week: dict = {}
    if "days" in stats:
        for day in stats["days"]:
            for r in day.get("rows") or []:
                w = week.setdefault(r.get("name"), {"asr_s": 0.0, "tokens": 0, "rt_s": 0.0, "days": 0})
                w["asr_s"] += float(r.get("asr_s") or 0)
                w["tokens"] += int(r.get("tokens_in") or 0) + int(r.get("tokens_out") or 0)
                w["rt_s"] += float(r.get("rt_s") or 0) + float(r.get("rt_web_s") or 0)
                w["days"] += 1
        if stats["days"]:
            today = {r.get("name"): r for r in stats["days"][-1].get("rows") or []}
    out += ["", "## 通行码", ""]
    if "codes" in lst:
        out += ["| 名字 | 状态 | 到期 | 设备(桌面/网页) | 最后在线 | 今日转写 min | 今日 token | 7 天转写 h |",
                "|---|---|---|---|---|---|---|---|"]
        nowms = now.timestamp() * 1000
        for c in sorted(lst["codes"], key=lambda c: -max(int(c.get("last_seen") or 0), int(c.get("web_last_seen") or 0))):
            nm = c.get("name")
            st = "已吊销" if c.get("revoked") else ("已过期" if c.get("expires") and c["expires"] < nowms else "正常")
            t = today.get(nm) or {}
            w = week.get(nm) or {}
            out.append(f"| {nm} | {st} | {_ms(c.get('expires'))[:5]} | {c.get('devices', 0)}/{c.get('web_devices', 0)}"
                       f"（上限 {c.get('max_devices', '—')}） | {_ms(max(int(c.get('last_seen') or 0), int(c.get('web_last_seen') or 0)))} | "
                       f"{round(float(t.get('asr_s') or 0) / 60, 1)} | {int(t.get('tokens_in') or 0) + int(t.get('tokens_out') or 0)} | "
                       f"{round(float(w.get('asr_s') or 0) / 3600, 2)} |")
    else:
        out.append(f"读不到：{lst.get('error')}")

    q = _sec(lambda: fetch("quota"))
    out += ["", "## 识别额度（今日）", ""]
    if "caps" in q:
        g = (q.get("v3") or {}).get("groq") or {}
        qw = q.get("qwen") or {}
        out += [f"- Groq v3：已用 {q['v3'].get('used_reqs')} 次 / {round(float(q['v3'].get('used_s') or 0) / 60, 1)} min；"
                f"Groq 自报剩余 {g.get('req_left', '—')}/{g.get('req_lim', '—')} 次、音频 {g.get('aud_left', '—')}/{g.get('aud_lim', '—')} s",
                f"- 千问：{qw.get('used')}/{qw.get('max')} 段" + (f"；已耗尽 {', '.join(qw.get('exhausted') or [])}" if qw.get("exhausted") else "")]
    else:
        out.append(f"读不到：{q.get('error')}")

    h = _sec(lambda: fetch("health"))
    out += ["", "## Worker 健康（近 24 h）", ""]
    if "overall" in h:
        o = h["overall"]
        out.append(f"- 请求 {o.get('requests')}，出错 {o.get('errors')}，耗时 p50 {o.get('wall_p50')} ms / p95 {o.get('wall_p95')} ms")
        bad = [u for u in h.get("upstream") or [] if (u.get("c5xx") or 0) or (u.get("c429") or 0)]
        for u in bad:
            out.append(f"- 上游 {u.get('name')}：5xx {u.get('c5xx')}，429 {u.get('c429')}（共 {u.get('total')}）")
        if h.get("versions"):
            v = h["versions"][-1]
            out.append(f"- 当前版本 {v.get('id')}，部署于 {v.get('deployed_at') or '—'}")
    else:
        out.append(f"读不到：{h.get('error')}")

    out += ["", "## 平台余额", ""]
    so = _sec(lambda: fetch("soniox"))
    b = so.get("balance") or {}
    if b:
        u7 = sum(float((v or {}).get("cost_7d") or 0) for v in (so.get("usage") or {}).values())
        out.append(f"- Soniox：剩 ${b.get('available')}（已用 ${b.get('used')}）{'，⚠️ 已耗尽' if b.get('exhausted') else ''}；近 7 天花 ${round(u7, 4)}")
    else:
        out.append(f"- Soniox：读不到（{so.get('balance_error') or so.get('error') or '没回余额'}）")
    orr = _sec(lambda: fetch("openrouter"))
    c = orr.get("credits") or {}
    if c:
        out.append(f"- OpenRouter：剩 ${round(float(c.get('total') or 0) - float(c.get('used') or 0), 4)}（总 ${c.get('total')}，已用 ${c.get('used')}）")
    else:
        out.append(f"- OpenRouter：读不到（{orr.get('error') or '没回额度'}）")
    ds = _sec(lambda: fetch("deepseek"))
    if ds.get("balances"):
        out.append("- DeepSeek：" + "，".join(f"{x['currency']} {x['total']}" for x in ds["balances"])
                   + ("" if ds.get("available") else "，⚠️ 不可用（余额不足会让翻译 402）"))
    else:
        out.append(f"- DeepSeek：读不到（{ds.get('error')}）")

    rn = _sec(lambda: fetch("runner"))
    out += ["", "## 正在上课 / 笔记队列", ""]
    if "counts" in rn:
        live = rn.get("live") or []
        out.append("- 正在上课：" + ("、".join(str(x.get("name") or "未知") for x in live) if live else "没人"))
        out.append("- 队列：" + "，".join(f"{k} {v}" for k, v in rn["counts"].items()))
    else:
        out.append(f"读不到：{rn.get('error')}")

    r2 = _sec(lambda: fetch("r2"))
    out += ["", "## 上传的课（最近 15 节）", ""]
    if "sessions" in r2:
        out.append(f"桶里 {r2.get('objects')} 个对象，{round(int(r2.get('bytes') or 0) / 1e9, 2)} GB；别人的课 {r2.get('others')} 节")
        out += ["", "| id | 本机会话名 | 最后上传 | 文件 | 原文 |", "|---|---|---|---|---|"]
        for g in r2["sessions"][:15]:
            files = g.get("files") or []
            links = " ".join(f"`/raw/{g['id']}?what={f}`" for f in ("report.json", "transcript.md") if f in files)
            out.append(f"| {g['id']} | {g.get('name') or ('（别人的）' if not g.get('mine') else '')} | {str(g.get('last'))[:16]} | "
                       f"{len(files)} + 音频 {g.get('audio', 0)} 段 | {links or '—'} |")
    else:
        out.append(f"读不到：{r2.get('error')}")

    out += ["", "## 还能看", "",
            "- `/api/<名字>`（JSON）：" + " / ".join(sorted(set(ui.SLOW) | READ_ADMIN - {"minutes", "devices"})),
            "- `/api/minutes?who=<名字>[&day=YYYY-MM-DD]`、`/api/devices?who=<名字>`",
            "- `/api/websession?who=<名字>&session=<web-…>`：一节网页课的全部事件；`/api/cfwin?s=<ISO>&u=<ISO>`：一段时间的 Worker 延迟",
            "- `/raw/<16 位 id>?what=report.json|transcript.md|note.html|extra.tar.gz`：那节课的原文；`/note/<id>`：交互版笔记（页面）",
            "- `/ui?key=<key>`：和 Mac 上一模一样的面板页（浏览器打开；只读，写操作会提示去 Mac）",
            "- `/local`：Mac 推上来的本机会话名 + 上传台账", ""]
    md = "\n".join(out)
    return mask(md) if _masking(home) else md


def _code_of(who: str, codes=None) -> str:
    """名字 → 完整码（同名重发过取最新的那张）。`codes` 测试注入。"""
    if codes is None:
        codes = cl_admin.admin_post("list", {}).get("codes", [])
    mine = [c for c in codes if c.get("name") == who and c.get("code")]
    mine.sort(key=lambda c: (not c.get("revoked"), int(c.get("created") or 0)))
    return str(mine[-1]["code"]) if mine else ""


# ---------- HTTP ----------
COOKIE = "clpanel"
MASK_FLAG = "remote-panel-mask"                                       # 有这个文件 = 退回只给码前缀（不用重启）


def _masking(home: pathlib.Path | None = None) -> bool:
    return ((home or HOME) / MASK_FLAG).exists()


def _out(obj):
    return mask(obj) if _masking() else obj


def _key(home: pathlib.Path | None = None) -> str:
    try:
        return ((home or HOME) / KEY_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def ui_page() -> bytes:
    """本机面板那一页原样给，只把绝对路径改成相对的（外面套着 nginx 的 `/cl/` 前缀）。"""
    page = ui.PAGE.read_text(encoding="utf-8")
    page = page.replace('fetch("/api/" + name', 'fetch("api/" + name').replace('href: "/note/"', 'href: "note/"')
    page = page.replace("<small>通行码管理 · 本机</small>", "<small>通行码管理 · 远程只读</small>")
    return page.encode("utf-8")


WRITE_MSG = "远程面板只读：吊销 / 加码 / 续期 / 改额度 / 删除去 Mac 上的面板"


class Handler(BaseHTTPRequestHandler):
    server_version = "ClassLiveRemotePanel"

    def log_message(self, fmt, *a):                                   # 不记 query（里面可能有 key）
        sys.stderr.write("%s %s\n" % (self.command, urllib.parse.urlparse(self.path).path))

    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(code)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Robots-Tag", "noindex")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(_out(obj), ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _text(self, code: int, s: str, ctype="text/markdown; charset=utf-8") -> None:
        self._send(code, s.encode("utf-8"), ctype)

    def _given(self, qs: dict) -> str:
        got = (self.headers.get("authorization") or "").removeprefix("Bearer ").strip() or (qs.get("key") or [""])[0]
        if not got:
            for part in (self.headers.get("cookie") or "").split(";"):
                k, _, v = part.strip().partition("=")
                if k == COOKIE:
                    got = v
        return got

    def _authed(self, qs: dict) -> bool:
        want = _key()
        if not want:                                                  # fail-closed：没配 key 谁都不认
            return False
        return hmac.compare_digest(self._given(qs).encode(), want.encode())

    def do_PUT(self):                                                 # noqa: N802
        self._json(405, {"error": "只读：GET，或面板页发的 POST 读操作"})

    do_DELETE = do_PATCH = do_PUT                                     # noqa: N815

    def do_POST(self):                                                # noqa: N802
        """面板页（`/ui`）的协议：POST `/api/<名>` + 闸门头，和本机面板一样；只认读操作。"""
        u = urllib.parse.urlparse(self.path)
        path = u.path.rstrip("/")
        if not path.startswith("/api/"):
            return self._json(405, {"error": "只读"})
        if not self._authed(urllib.parse.parse_qs(u.query)):
            return self._json(401, {"error": "要 key"})
        if self.headers.get(ui.GATE_HEADER) != "1":                   # 跨站表单 / fetch 带不上这个头（同本机面板）
            return self._json(403, {"error": f"缺少 {ui.GATE_HEADER} 头"})
        name = path[len("/api/"):]
        raw = self.rfile.read(min(int(self.headers.get("content-length") or 0), 65536))
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            if name not in ui.SLOW:
                return self._json(400, {"error": "body 不是 JSON"})
            body = {}
        got = ui.read_op(name, body)
        if got is not None:
            return self._json(*got)
        if name in READ_ADMIN and isinstance(body, dict):
            keep = {k: body[k] for k in ("code", "day") if k in body}
            try:
                return self._json(200, cl_admin.admin_post(name, keep))
            except cl_admin.AdminError as e:
                return self._json(502, {"error": str(e)})
        if name in ui.ALLOWED:
            return self._json(403, {"error": WRITE_MSG})
        return self._json(404, {"error": f"不认：{name}"})

    def do_GET(self):                                                 # noqa: N802
        u = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(u.query)
        path = u.path.rstrip("/") or "/"
        if path == "/health":
            return self._json(200, {"ok": True})
        if path.startswith("/assets/katex/"):                         # 公开的开源库，不要 key（沙箱里的笔记页带不上 cookie）
            got = ui.katex_asset(path[len("/assets/katex/"):])
            if isinstance(got, dict):
                return self._json(404, got)
            return self._send(200, got[1], got[0], {"Access-Control-Allow-Origin": "*"})
        if not self._authed(qs):
            return self._json(401, {"error": "要 key：Authorization: Bearer <key> 或 ?key=<key>"})
        if path == "/ui":
            if qs.get("key"):                                         # 换成 cookie，再把 key 从地址栏拿掉
                return self._send(302, b"", "text/plain", {
                    "Location": "ui", "Set-Cookie": f"{COOKIE}={_key()}; HttpOnly; Secure; SameSite=Strict; Max-Age=31536000"})
            return self._send(200, ui_page(), "text/html; charset=utf-8")
        if path in ("/", "/digest", "/digest.md"):
            return self._text(200, digest())
        if path == "/local":
            return self._json(200, local_state(HOME))
        if path.startswith("/note/"):
            code, body = ui.note_page(path[len("/note/"):], "../assets/katex/")
            if code != 200:
                return self._json(code, body)
            return self._send(200, body, "text/html; charset=utf-8", {"Content-Security-Policy": ui.NOTE_CSP})
        if path.startswith("/raw/"):
            sid = path[len("/raw/"):]
            what = (qs.get("what") or ["report.json"])[0]
            r = ui.r2_raw(sid, what)
            if "error" in r:
                return self._json(404, r)
            if what.endswith(".md") and isinstance(r.get("text"), str):
                return self._text(200, mask(r["text"]) if _masking() else r["text"])
            return self._json(200, r)
        if path.startswith("/api/"):
            name = path[len("/api/"):]
            fresh = (qs.get("fresh") or [""])[0] == "1"
            if name in ui.SLOW:
                return self._json(200, ui.cached(name, ui.SLOW[name], fresh))
            if name == "websession":
                return self._json(*ui.read_op(name, {"name": (qs.get("who") or qs.get("name") or [""])[0],
                                                     "session": (qs.get("session") or [""])[0]}))
            if name == "cfwin":
                return self._json(*ui.read_op(name, {"s": (qs.get("s") or [""])[0], "u": (qs.get("u") or [""])[0]}))
            if name in READ_ADMIN:
                body = {}
                if name in ("minutes", "devices"):
                    who = (qs.get("who") or [""])[0]
                    if not NAME_RE.fullmatch(who):
                        return self._json(400, {"error": "要 ?who=<名字>"})
                    code = _code_of(who)                              # Worker 只认完整码
                    if not code:
                        return self._json(404, {"error": f"没有叫 {who} 的码"})
                    body = {"code": code}
                    day = (qs.get("day") or [""])[0]
                    if name == "minutes" and re.fullmatch(r"\d{4}-\d\d-\d\d", day):
                        body["day"] = day
                try:
                    return self._json(200, cl_admin.admin_post(name, body))
                except cl_admin.AdminError as e:
                    return self._json(502, {"error": str(e)})
            return self._json(404, {"error": f"不认：{name}"})
        return self._json(404, {"error": "not found；看 / 的摘要末尾"})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="通行码面板远程只读版")
    ap.add_argument("--port", type=int, default=8772)
    ap.add_argument("--import-local", action="store_true", help="从 stdin 读 Mac 推来的本机数据")
    a = ap.parse_args(argv)
    if a.import_local:
        print(json.dumps(import_local(sys.stdin.buffer.read()), ensure_ascii=False))
        return 0
    if not _key():
        sys.exit(f"缺 {HOME / KEY_FILE}（对外 key，600）")
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)          # 只 bind 环回；外面走 nginx
    ui.prewarm()
    print(f"远程只读面板：127.0.0.1:{a.port}")
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
