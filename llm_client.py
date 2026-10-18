#!/usr/bin/env python3
"""对话模型（DeepSeek）的唯一出口：地址 + 「一问一答、要 JSON」那种调用。

为什么收在这里（`docs/PLAN-passkey.md` §2 第 1 步）：原来 `build_notes` / `polish` /
`obsidian_writer` ×2 各自手写同一段 `httpx.post("https://api.deepseek.com/…")`，
流式翻译（`cloud_translator`）又另有一份地址。之后「一人一码」要按 key 换地址（`sk-` 直连、
`cl-` 走 Owen 的 Worker），只能改一处才不会漏。

这一步**行为逐字不变**：同样的请求体、同样的超时、同样的异常（HTTP 错误 / JSON 解析错误照原样抛，
各调用方原来怎么接就怎么接）。
"""
from __future__ import annotations

import json

#: DeepSeek 的 OpenAI 兼容地址（流式翻译 `cloud_translator.DEFAULT_BASE` 也用它）。
BASE = "https://api.deepseek.com/v1"


def is_passcode(key: str | None) -> bool:
    """一人一码的通行码 `cl-<名字>-<随机>`（`docs/PLAN-passkey.md`）；原生 `sk-…` key 不是。"""
    return bool(key) and key.startswith("cl-")


def base_for(key: str | None) -> str:
    """通行码 → Owen 的 Worker（与定稿转写同一个域名，`asr_endpoint`）；其余 → DeepSeek 直连。"""
    if is_passcode(key):
        import asr_endpoint
        return asr_endpoint.endpoint().rsplit("/", 1)[0]
    return BASE


def headers(key: str) -> dict:
    """请求头。通行码另外带安装号与客户端版本（Worker 按前者数设备、按后者统计版本分布）——
    直连 DeepSeek 时**都不带**（别把安装号发给第三方）。"""
    h = {"Authorization": f"Bearer {key}"}
    if is_passcode(key):
        import asr_endpoint
        import final_asr
        h["X-ClassLive-Install"] = final_asr.install_id()
        h["X-ClassLive-Version"] = asr_endpoint.version_header()
    return h


def chat_json(key: str, model: str, system: str, user: str, *, max_tokens: int,
              temperature: float = 0.2, timeout: float = 180.0, return_meta: bool = False):
    """非流式、`response_format=json_object`、关掉思考模式；返回模型给的 JSON（**原样**，可能不是 dict）。

    ⚠️ 失败一律抛（`httpx.HTTPError` / `KeyError` / `json.JSONDecodeError`…）—— 吞不吞、吞成什么，
       是调用方的事（它们各自的约定不同：`None` / `{}` / 往上抛给收尾锁）。
    ⚠️ `return_meta=True` → `(obj, finish_reason)`：只有"截断必须当失败"的调用方
       （`note_v2.overview` 五分钟串讲）要它 —— `finish_reason=="length"` 的 JSON
       有时还能解析出来，而半截的叙述印进笔记比没有更坏（2026-10-18）。
    """
    import httpx
    payload = {"model": model, "stream": False, "max_tokens": max_tokens,
               "temperature": temperature, "thinking": {"type": "disabled"},
               "response_format": {"type": "json_object"},
               "messages": [{"role": "system", "content": system},
                            {"role": "user", "content": user}]}
    r = httpx.post(f"{base_for(key)}/chat/completions", headers=headers(key),
                   json=payload, timeout=timeout)
    r.raise_for_status()
    choice = (r.json().get("choices") or [{}])[0]
    obj = json.loads(choice["message"]["content"])
    if return_meta:
        return obj, str(choice.get("finish_reason") or "")
    return obj
