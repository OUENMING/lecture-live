#!/usr/bin/env python3
"""测试模式上传的**落点** —— 单一来源（`main.py` 两处接线都读它）。

⚠️⚠️ **这两个是公开常量，不是密钥。** 它们随 app 发给每个用户，而且本仓库
   （MIT）是公开的 —— 任何人从源码或反编译 `.app` 都能拿到 `TOKEN`。
   设计上就按**公开 write-only 凭据**（Sentry DSN 那套）来：`TOKEN` 只证明
   「这是 ClassLive 在发」，**读不到任何数据**；真正的防线在**服务端**
   （Cloudflare Worker：路径白名单 / 体积上限 / 限流 / 只写）。
   调研与出处见 `docs/PLAN-zero-config-upload.md` §3/§6。

⚠️ 换落点 / 轮换 token：改这里 + **发一版**（用户 `cl update` 后自动用新值）；
   轮换时同步 `wrangler secret put INGEST_TOKEN`（两端要一致）。
"""
from __future__ import annotations

import os

#: Worker 的摄入端点。客户端 PUT 到 `<ENDPOINT>/<hash16>/<name>`。
ENDPOINT = "https://ingest.bldcam.page/v1/objects"

#: **公开**写入令牌。⚠️ 不是密钥（见文件头）。
TOKEN = "090740d2c909d3f606c2efe754c5679e"


def endpoint() -> str:
    """端点。允许 `CLASSLIVE_UPLOAD_ENDPOINT` 覆盖（判据 / 联调用）。"""
    return os.environ.get("CLASSLIVE_UPLOAD_ENDPOINT", ENDPOINT)


def token() -> str:
    """令牌。允许 `CLASSLIVE_UPLOAD_TOKEN` 覆盖（判据用；生产取常量）。"""
    return os.environ.get("CLASSLIVE_UPLOAD_TOKEN", TOKEN)
