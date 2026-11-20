# billing —— 计费头 `X-ClassLive-Bill-To: <hash16>` 的唯一出口（契约 §6）。
# 笔记（note_job）与实时总结（summary_job）同在一个进程、并行跑，所以只能包**一层** `llm_client.headers`：
#   线程 / 上下文里 `CTX` 有值 → 用它（summary_job 在每次模型调用前后 set/reset，跑在总结自己的线程里）；
#   否则用进程级 `_default`（note_job 一次只跑一条，开工时 set_default）。
# Claude 11-20 合并：W23 原来的全局包装装在 W24 之后会把总结的调用也记到笔记那位用户头上。
from __future__ import annotations

import contextvars
import re

HASH16 = re.compile(r"[0-9a-f]{16}")
CTX = contextvars.ContextVar("classlive_bill_to", default="")
_default = ""


def install() -> None:
    """幂等：包一次 `llm_client.headers`（全仓模型请求头唯一出口，chat_json 与 Jev 都调用时取名字）。"""
    import llm_client
    cur = llm_client.headers
    if getattr(cur, "_cl_bill", False):
        return

    def headers(key, call_class=""):
        h = cur(key, call_class)
        b = CTX.get() or _default
        if b:
            h["X-ClassLive-Bill-To"] = b
        return h

    headers._cl_bill = True
    headers._cl_orig = cur
    llm_client.headers = headers


def set_default(bill_to: str) -> bool:
    """进程级默认（笔记任务用）。不合 16 hex → 不改、回 False。"""
    global _default
    b = str(bill_to or "").strip().lower()
    if not HASH16.fullmatch(b):
        return False
    install()
    _default = b
    return True


def clear_default() -> None:
    global _default
    _default = ""
