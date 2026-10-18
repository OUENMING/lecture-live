#!/usr/bin/env python3
"""上课中的暗号：在输入框里打「菠萝丁」→ 实时识别切回「本地流式草稿 + 云端定稿」。

作者 2026-10-18 要的：上课时想对比两条识别链，不用退出重开。翻译那条不动（照旧走云端）。

    hidden_switch.register(fn)   # main 开课后挂上（fn() -> 给人看的一行）
    hidden_switch.clear()        # 下课收尾时摘掉
    hidden_switch.handle(text)   # 输入框回车时先问一句：是暗号 -> 返回那一行；不是 -> None

两个入口共用这一处：悬浮窗那条「当场问」输入行、上课中从菜单栏打开的开课面板搜索框。
没挂上（没在上课）时 `handle` 一律返回 None —— 暗号就是普通文字，照常提问 / 搜索。

⚠️ `handle` 在 AppKit 主线程调，必须立刻返回：挂上的 `fn` 自己负责把慢活丢到后台
   （`StreamGate.force_local` 起一条线程去关 Soniox）。
"""
from __future__ import annotations

WORD = "菠萝丁"

_action = None


def register(fn) -> None:
    global _action
    _action = fn


def clear() -> None:
    global _action
    _action = None


def handle(text: str) -> str | None:
    """是暗号且挂着动作 -> 执行并返回给人看的一行；否则 None。永不抛。"""
    if "".join(str(text or "").split()) != WORD or _action is None:
        return None
    try:
        return str(_action() or "")
    except Exception:                                       # noqa: BLE001
        return "切换没成功（课照常在上）"
