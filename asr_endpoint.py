#!/usr/bin/env python3
"""云端定稿转写的**落点** —— 单一来源（`final_asr` 读它）。仿 `upload_endpoint.py`。

⚠️⚠️ **这两个是公开常量，不是密钥。** 随 app 发给每个用户，本仓库（MIT）公开，
   任何人从源码都能拿到 `TOKEN`。它只证明「这是 ClassLive 在发」，**读不到任何数据**；
   真正的防线在服务端（Worker：体积上限 / 按 install 与按 IP 限流 / 额度上限）。
   Groq 与千问的真 key 只在 Worker 的 secret 里，从不进客户端、从不进仓库。

⚠️ 换落点 / 轮换 token：改这里 + 发一版；同步 `wrangler secret put CLIENT_TOKEN`。
"""
from __future__ import annotations

import os
from functools import lru_cache

#: Worker 的转写端点。客户端 POST `audio/ogg` 到这里。
ENDPOINT = "https://asr.bldcam.page/v1/transcribe"

#: **公开**令牌。⚠️ 不是密钥（见文件头）。部署时由主会话替换为 `secrets.token_hex(16)`。
TOKEN = "cl_pub_67edc45b6ba8c20cf96e54dfb27ecc39"


def endpoint() -> str:
    """端点。允许 `CLASSLIVE_ASR_ENDPOINT` 覆盖（判据 / 联调 / 断网实测用）。"""
    return os.environ.get("CLASSLIVE_ASR_ENDPOINT", ENDPOINT)


@lru_cache(maxsize=1)
def version_header() -> str:
    """`X-ClassLive-Version` 的值：客户端版本号。

    单一来源是仓库根的 `VERSION`（经 `update.version()`）—— **别在这里再读一遍那个文件**。
    Worker 把它记进 `use:<日>:<码>` 的 `vers` 字段。
    ⚠️ 口径：这统计的是「**上报的**客户端里各版本各发了多少请求」—— **老客户端不发这个头，
    所以看不见它们**（想数「谁还在老版本」得另找办法，比如在 Worker 上记「没带这个头」的次数）。

    缓存一次：跑起来之后版本就不该再变了（用户中途更新，这个进程里的**仍是老代码** ——
    报老版本号才是实话）。
    """
    import update
    return update.version()


@lru_cache(maxsize=1)
def tz_header() -> str:
    """`X-ClassLive-Tz` 的值：本机的 IANA 时区名（如 `Europe/Dublin` / `America/Los_Angeles`）。

    为什么要有：面板要按**他自己那边**的钟显示「他常在周几几点上课」。只按国家猜是猜不准的 ——
    美国横跨四个时区（美东/中部/山地/太平洋），同一个 `US` 猜出来的钟能差 3 小时。
    客户端自己知道，比服务端按 IP 猜准。

    怎么拿：macOS 的 `/etc/localtime` 是个软链，指向
    `/var/db/timezone/tz/<版本>/zoneinfo/<Area>/<City>` —— 取软链里 `zoneinfo/` 后面的那段。
    拿不到就**不发**（空串）：让服务端退回它按 IP 判的那个，别发一个假的。

    缓存一次：时区不会在一次运行里变（用户换时区要重启 App）。
    """
    import os
    try:
        p = os.path.realpath("/etc/localtime")
    except OSError:
        return ""
    if "/zoneinfo/" not in p:
        return ""
    tz = p.split("/zoneinfo/", 1)[1].strip("/")
    return tz[:40] if "/" in tz else ""


def token(passcode: str | None = None) -> str:
    """令牌。`CLASSLIVE_ASR_TOKEN` 覆盖（判据用）> 用户填的通行码 `cl-…`（一人一码：转写记到他名下）> 公开常量。"""
    env = os.environ.get("CLASSLIVE_ASR_TOKEN")
    if env:
        return env
    if passcode and passcode.startswith("cl-"):
        return passcode
    return TOKEN
