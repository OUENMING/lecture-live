#!/usr/bin/env python3
"""PreToolUse(Bash) 护栏：把两条「靠记忆」的硬规矩变成拦截。退出码 2 = 拦下，stderr 回给 Claude。

（`sessions/` / `~/.classlive` / Obsidian 的删除由作者的**用户级** hook
 `~/.claude/hooks/guard-destructive.py` 管，这里不再写一份。）

1. 推送 / 打 tag / 发 release 前要作者点头：拦下并提示；作者明确同意后，
   在命令前加 `CLASSLIVE_OWEN_OK=1` 再跑。⚠️ 这是减速带不是锁 —— 防的是「顺手就推了」。
2. 子代理不许跑 `main.py` / `cl`：它们会往真 `sessions/` 和真 Obsidian 库写测试产物
   （2026-10-11 实测发生过）。

只看命令文本，判不准时放行（误拦比漏拦更伤工作流；漏拦有 `scripts/check.py` 的写端核对兜底）。
"""
import json
import re
import sys

try:
    ev = json.load(sys.stdin)
except Exception:                                            # noqa: BLE001
    sys.exit(0)
cmd = (ev.get("tool_input") or {}).get("command") or ""


def block(msg: str) -> None:
    print(msg, file=sys.stderr)
    sys.exit(2)


# 1. 推送 / tag / release（`git tag -l` 之类只读的放行）
if re.search(r"\bgit\s+push\b|\bgit\s+tag\s+(-a\s+)?v?\d|\bgh\s+release\s+create\b", cmd) \
        and "CLASSLIVE_OWEN_OK=1" not in cmd and "--dry-run" not in cmd:
    block("⛔ 推送 / 打 tag / 发 release 要作者先点头（更新日志也要先给他看）。"
          "他明确同意后，在命令前加 CLASSLIVE_OWEN_OK=1 再跑。")

# 2. 子代理跑 main.py / cl（只认「当命令执行」：`python… main.py`，或行首 / ; & | 之后的 `cl`、`./cl`）
RUNS_APP = re.compile(r"python[\w.]*\s+(\S*/)?main\.py\b|(^|[;&|(]\s*)(\S*/)?cl(-x)?(\s|$)")
if ev.get("agent_id") and RUNS_APP.search(cmd):
    block("⛔ 子代理不许跑 main.py / cl —— 会往真 sessions/ 和真 Obsidian 库写测试产物。"
          "要端到端验证，用 docs/experiments/run_isolated.py（写端全隔离），或交回主会话。")

sys.exit(0)
