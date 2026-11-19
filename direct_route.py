"""翻译请求「先直连、不通再走系统代理」（2026-11-19）。

为什么：中国区用户开着系统代理（香港出口）时，每句翻译从他电脑到 Worker 的网络段 ≈965 ms，
直连同一台 Worker（Cloudflare HKG）只要 270–420 ms（客户端 zh_first − Worker 首 token 的中位差，
11-05/11-10/11-18 直连 vs 11-19 代理）。httpx 默认 `trust_env=True` 会读 macOS 系统代理，
所以翻译也被绕去了香港出口。

规则（只管翻译这一路；Soniox 实时流照旧跟随系统代理 —— 11-19 那节代理反而更稳）：
- 系统没给这个地址配代理 → 什么都不变（单个连接池，与从前逐字一致）。
- 配了代理 → 先直连；**建连阶段**失败（DNS / 拒绝 / 连接超时，`httpx.ConnectError`/`ConnectTimeout`）
  → 这一句当场改走系统代理，并冷却 `COOLDOWN_S` 内不再试直连。
- 已经建上连接、流到一半出的错**不在这里管**：交给 `EngineRouter` 原有的降级/探针（换路重发会重复计费）。
- 直连的建连超时压到 `DIRECT_CONNECT_S`：被墙的时候最多拖慢一句 2 s，之后冷却期内都走代理。

开关：`CLASSLIVE_LLM_DIRECT=0` 关掉（一律跟随系统代理，等于旧行为）。
"""
from __future__ import annotations

import contextlib
import os
import time
import urllib.parse
import urllib.request
from typing import Callable

COOLDOWN_S = 300.0
DIRECT_CONNECT_S = 2.0


def enabled() -> bool:
    return os.environ.get("CLASSLIVE_LLM_DIRECT", "1").strip() != "0"


def system_proxy_for(url: str, getproxies: Callable = urllib.request.getproxies,
                     bypass: Callable = urllib.request.proxy_bypass) -> str:
    """系统 / 环境给 `url` 配的代理地址；没有（或在例外名单里）→ `""`。永不抛。

    与 httpx `trust_env=True` 同源：`urllib.request.getproxies()`（macOS 上含系统偏好里的代理）。
    """
    try:
        u = urllib.parse.urlsplit(url)
        proxies = getproxies() or {}
        p = proxies.get(u.scheme) or proxies.get("all") or ""
        if not p:
            return ""
        if u.hostname and bypass(u.hostname):
            return ""
        return str(p)
    except Exception:                                          # noqa: BLE001
        return ""


class DirectFirst:
    """状态：直连冷却到几时 + 计数。连接池由调用方持有（这里不建、不关）。"""

    def __init__(self, *, cooldown_s: float = COOLDOWN_S, clock: Callable[[], float] = time.monotonic):
        self._cooldown = float(cooldown_s)
        self._clock = clock
        self._down_until = 0.0
        self.stats = {"direct": 0, "system": 0, "direct_fail": 0}   # system = 跟随系统设置（有代理就是代理）

    def direct_ok(self) -> bool:
        return self._clock() >= self._down_until

    @contextlib.contextmanager
    def stream(self, direct_client, proxied_client, method: str, url: str, *,
               timeout=None, **kw):
        """`direct_client` 为 None（没代理 / 开关关）→ 直接用 `proxied_client`，行为同旧代码。"""
        import httpx
        with contextlib.ExitStack() as st:
            r = None
            if direct_client is not None and self.direct_ok():
                t = timeout
                if isinstance(t, httpx.Timeout):
                    t = httpx.Timeout(connect=min(t.connect or DIRECT_CONNECT_S, DIRECT_CONNECT_S),
                                      read=t.read, write=t.write, pool=t.pool)
                try:
                    r = st.enter_context(direct_client.stream(method, url, timeout=t, **kw))
                    self.stats["direct"] += 1
                except (httpx.ConnectError, httpx.ConnectTimeout):
                    self.stats["direct_fail"] += 1
                    self._down_until = self._clock() + self._cooldown
                    r = None
            if r is None:
                r = st.enter_context(proxied_client.stream(method, url, timeout=timeout, **kw))
                self.stats["system"] += 1
            yield r
