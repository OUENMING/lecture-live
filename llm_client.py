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
              temperature: float = 0.2, timeout: float = 180.0, return_meta: bool = False,
              effort: str | None = None):
    """非流式、`response_format=json_object`；返回模型给的 JSON（**原样**，可能不是 dict）。

    ⚠️ 失败一律抛（`httpx.HTTPError` / `KeyError` / `json.JSONDecodeError`…）—— 吞不吞、吞成什么，
       是调用方的事（它们各自的约定不同：`None` / `{}` / 往上抛给收尾锁）。
    ⚠️ `return_meta=True` → `(obj, finish_reason)`：只有"截断必须当失败"的调用方
       （`note_v2.overview` 五分钟串讲）要它 —— `finish_reason=="length"` 的 JSON
       有时还能解析出来，而半截的叙述印进笔记比没有更坏（2026-10-18）。
    ⭐ F11 §0（作者 2026-10-18）：`effort` 只给**课后笔记**那条链用（`review` / `repair` /
       `verify` / `overview` / `shorten` / `align`，`note_merge`）——
       传 `"low"` 就开思考模式（官方：`thinking={"type":"enabled"}` +
       `reasoning_effort` ∈ low/high/max；思考模式下 `temperature` 被忽略，故不发；
       推理只从 `message.reasoning_content` 回来，**绝不进笔记**）。
       其余调用方（翻译 / 实时摘要 / 分类 / 术语查询）**不传** → 现状逐字不变（思考关闭）。
    ⚠️ **截断自动降级**（F11 §0 实测）：通行码走 Worker 时 `max_tokens` 被压到 4096
       （`handler.js` 的 `LLM_MAX_TOKENS`），而思考的推理 token **计入** `completion_tokens`
       —— 真 review 块实测 `max_tokens=4096` 时 `finish_reason=length`、`content` 空
       （推理吃掉全部 4096，整块丢）。所以带 `effort` 的调用一旦收到 `length`，
       **同一次重来、关掉思考**再取结果：笔记照常，不丢块（Worker 上限提上去后这条极少触发）。
    """
    import httpx

    def _once(eff: str | None):
        payload = {"model": model, "stream": False, "max_tokens": max_tokens,
                   "response_format": {"type": "json_object"},
                   "messages": [{"role": "system", "content": system},
                                {"role": "user", "content": user}]}
        if eff:
            payload["thinking"] = {"type": "enabled"}
            payload["reasoning_effort"] = eff
        else:
            payload["temperature"] = temperature
            payload["thinking"] = {"type": "disabled"}
        r = httpx.post(f"{base_for(key)}/chat/completions", headers=headers(key),
                       json=payload, timeout=timeout)
        r.raise_for_status()
        ch = (r.json().get("choices") or [{}])[0]
        return ch, str(ch.get("finish_reason") or "")

    choice, finish = _once(effort)
    if effort and finish == "length":
        # 思考把 token 吃光（实测通行码 4096 时 content 为空）→ 关思考重来一次。
        choice, finish = _once(None)
    content = choice["message"]["content"]
    obj = json.loads(content)
    if return_meta:
        # ⭐ F9：带上**返回长度**（chars）—— 速览为空/截断时诊断要有数（finish/len/max_tokens）。
        return obj, finish, len(content)
    return obj
