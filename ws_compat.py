"""`websockets.sync.client` 的唯一取法：本机装了用本机的，没装上就用仓库自带的 `vendor/websockets`。

⭐ 3.10.1：一位测试者 3.10.0 自动更新拉下了代码、补依赖（`uv pip install`）却没成 →
   缺 `websockets` → Soniox 连不上，整节课静默退回 Groq。补依赖为什么失败要看他本机的
   `update.log`（3.10.1 起随诊断包上传）；在那之前，代码自带一份纯 Python 的
   websockets 17.2（BSD-3，`vendor/websockets/LICENSE`；去掉了可选的 C 加速 `speedups.so`，
   掩码改走纯 Python —— 实时流每秒几 KB，差别量不出来）。
⚠️ 本机装了**能用**的版本就永远优先；只有它取不到（没装 / 太旧没有 `sync`）时，
   才把 vendor 放到 `sys.path` **最前**再取 —— 放末尾的话，一份太旧的本机版本会再次挡住它。
⚠️ `requirements.txt` 里的 `websockets>=15` 不删 —— 正常机器照旧走装好的那份。
"""
from __future__ import annotations

import pathlib
import sys

VENDOR = pathlib.Path(__file__).resolve().parent / "vendor"


def client():
    """返回 `websockets.sync.client` 模块。两份都拿不到时照常抛 ImportError。"""
    try:
        import websockets.sync.client as wsc
        return wsc
    except ImportError:
        pass
    v = str(VENDOR)
    if v in sys.path:
        sys.path.remove(v)
    sys.path.insert(0, v)
    # 第一次 import 失败可能留下半截的 `websockets` 包对象 —— 清掉再从 vendor 取
    for k in [m for m in sys.modules if m == "websockets" or m.startswith("websockets.")]:
        sys.modules.pop(k, None)
    import websockets.sync.client as wsc
    return wsc


# ---------- 走哪条路连：直连 / HTTP 代理 / SOCKS 代理 ----------
#
# ⭐ 3.11.5：中国区用户 11-12、11-16 两节课 Soniox **一次都没连上**（各 66 次，全是
#    `ImportError: connecting through a SOCKS proxy requires python-socks`）。他开了代理软件，
#    Mac 系统代理里 HTTP / HTTPS / SOCKS 都有；websockets 的 `proxy=True`（默认）读系统代理时
#    **SOCKS 优先**（官方文档 topics/proxies），而 SOCKS 要第三方库 python-socks —— 没装。
#    同一节课的翻译（httpx，只认 http/https 那两格）照常从香港出口走代理，一切正常。
# 这里自己挑路，**仍然走用户的代理**，只是换个入口：
#   ① HTTP 代理（websockets 原生支持 CONNECT 隧道，不要依赖）
#   ② SOCKS 代理（python-socks；本机没装就用 `vendor/python_socks`，Apache-2.0）
#   ③ 直连 —— 只在前面的代理**连不上**时兜底（代理软件关了却没清系统代理）
# ⚠️ 不读 websockets 的 `ws_proxy` / `wss_proxy` 环境变量（普通用户不会设）。

def _vendor_tail() -> None:
    """把 `vendor/` 加到 `sys.path` **末尾**：本机装了的第三方包永远优先。"""
    v = str(VENDOR)
    if v not in sys.path:
        sys.path.append(v)


def routes(url: str, getproxies=None, bypass=None) -> list[tuple[str, str | None]]:
    """按优先级列出 `url` 能走的路：`[(kind, proxy_url), …]`，`kind` ∈ http / socks / direct。

    最后一条永远是 `("direct", None)`。`getproxies` / `bypass` 测试注入；默认读系统
    （macOS 的系统代理设置 + `*_proxy` 环境变量，与 urllib / httpx 同源）。
    """
    import urllib.parse
    import urllib.request
    getproxies = getproxies or urllib.request.getproxies
    bypass = bypass or urllib.request.proxy_bypass
    u = urllib.parse.urlparse(url)
    host = u.hostname or ""
    port = u.port or (443 if u.scheme in ("wss", "https") else 80)
    direct = [("direct", None)]
    try:
        if not host or bypass(f"{host}:{port}") or bypass(host):
            return direct
        px = getproxies() or {}
    except Exception:                                   # noqa: BLE001 —— 读不了系统设置就直连
        return direct
    http, socks = [], []
    # 环境变量写法里 `https_proxy=socks5h://…` 也合法 —— 按**前缀**分，不按键名分
    for key in ("https", "http", "all", "socks"):
        val = str(px.get(key) or "").strip()
        if not val:
            continue
        if key == "socks" and val.startswith("http://"):  # macOS 系统设置把 SOCKS 写成 http://host:port
            val = "socks5h://" + val[len("http://"):]
        if "://" not in val:
            val = ("socks5h://" if key == "socks" else "http://") + val
        if val.startswith("socks"):
            socks.append(("socks", val))
        elif val.startswith("http"):
            http.append(("http", val))
    out, seen = [], set()
    for r in http + socks + direct:
        if r[1] not in seen:
            seen.add(r[1])
            out.append(r)
    return out


def _socks_sock(proxy_url: str, url: str, timeout: float):
    """经 SOCKS 代理连到 `url` 的主机，回已连好的 TCP socket（TLS 由 websockets 接着做）。"""
    import urllib.parse
    try:
        from python_socks.sync import Proxy
    except ImportError:
        _vendor_tail()
        from python_socks.sync import Proxy
    u = urllib.parse.urlparse(url)
    p = urllib.parse.urlparse(proxy_url)
    ver = "socks4" if p.scheme.startswith("socks4") else "socks5"
    rdns = p.scheme in ("socks5h", "socks4a")           # 域名交给代理解析（系统设置来的都是 5h）
    prx = Proxy.from_url(f"{ver}://{p.netloc}", rdns=rdns)
    sock = prx.connect(dest_host=u.hostname, dest_port=u.port or (443 if u.scheme == "wss" else 80),
                       timeout=timeout)
    sock.settimeout(None)
    return sock


def connect(url: str, *, open_timeout: float = 10.0, routes_fn=None, **kw):
    """连 websocket，**自己挑路**（见上）。回 `(ws, route)`。

    `route` = 走成的那条（`direct` / `http` / `socks`）；前面有没走通的会写在前头，
    如 `socks:ProxyError>direct`（≤ 100 字，进断线现场）。
    ⚠️ 总耗时封顶 `open_timeout`：每一条用剩下的时间，不会因为多试几条就翻倍。
    ⚠️ 服务端已经回了 HTTP 状态（`InvalidStatus`，如 401）= 路是通的，**不换路**直接抛。
    全都失败 → 抛**最后一条**的异常，`ws_route` 属性带着整条尝试链。
    """
    import time
    wsc = client()
    try:
        from websockets.exceptions import InvalidStatus
    except ImportError:                                 # pragma: no cover
        InvalidStatus = ()                              # noqa: N806
    rs = (routes_fn or routes)(url)
    t_end = time.monotonic() + open_timeout
    tried: list[str] = []
    last: BaseException | None = None
    for i, (kind, purl) in enumerate(rs):
        left = t_end - time.monotonic()
        if i and left < 0.3:
            break
        left = max(left, 0.3)
        try:
            if kind == "socks":
                sock = _socks_sock(purl, url, left)
                try:
                    ws = wsc.connect(url, sock=sock, open_timeout=max(0.3, t_end - time.monotonic()), **kw)
                except BaseException:
                    sock.close()
                    raise
            else:
                ws = wsc.connect(url, proxy=purl, open_timeout=left, **kw)
        except InvalidStatus:
            raise
        except Exception as e:                          # noqa: BLE001
            last = e
            tried.append(f"{kind}:{type(e).__name__}")
            continue
        return ws, ">".join(tried + [kind])[:100]
    if last is None:                                    # pragma: no cover —— routes() 至少有直连
        raise OSError("no route")
    try:
        last.ws_route = ">".join(tried)[:100]           # type: ignore[attr-defined]
    except Exception:                                   # noqa: BLE001
        pass
    raise last
