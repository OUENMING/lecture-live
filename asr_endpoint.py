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

#: Worker 的转写端点。客户端 POST `audio/ogg` 到这里。
ENDPOINT = "https://asr.bldcam.page/v1/transcribe"

#: **公开**令牌。⚠️ 不是密钥（见文件头）。部署时由主会话替换为 `secrets.token_hex(16)`。
TOKEN = "cl_pub_67edc45b6ba8c20cf96e54dfb27ecc39"


def endpoint() -> str:
    """端点。允许 `CLASSLIVE_ASR_ENDPOINT` 覆盖（判据 / 联调 / 断网实测用）。"""
    return os.environ.get("CLASSLIVE_ASR_ENDPOINT", ENDPOINT)


def token(passcode: str | None = None) -> str:
    """令牌。`CLASSLIVE_ASR_TOKEN` 覆盖（判据用）> 用户填的通行码 `cl-…`（一人一码：转写记到他名下）> 公开常量。"""
    env = os.environ.get("CLASSLIVE_ASR_TOKEN")
    if env:
        return env
    if passcode and passcode.startswith("cl-"):
        return passcode
    return TOKEN
