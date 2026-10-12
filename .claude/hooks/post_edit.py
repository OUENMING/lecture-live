#!/usr/bin/env python3
"""PostToolUse(Write|Edit)：只查刚改的那一个文件，毫秒级。退出码 2 = 把问题回给 Claude（改动已落盘）。

· shell 脚本（`*.sh` / `cl` / `cl-x`）：`$VAR` 紧跟非 ASCII → 要写 `${VAR}`。
  判据的唯一定义点是 `tests/test_audit_regressions.py` 的 R27；这里同一条正则，只是早一步提醒。
· Python：`ruff check`（规则与排除见 `ruff.toml`，版本与 `scripts/check.py` 钉的一致）。没有 uvx 就跳过。
全量判据在 `scripts/check.py`，这里不跑测试。
"""
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
RUFF = "ruff@0.17.0"
SHELL_VAR = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*[^\x00-\x7F]")   # 同 R27

try:
    ev = json.load(sys.stdin)
except Exception:                                            # noqa: BLE001
    sys.exit(0)
fp = (ev.get("tool_input") or {}).get("file_path") or ""
p = pathlib.Path(fp)
try:
    p.resolve().relative_to(ROOT)
except (ValueError, OSError):
    sys.exit(0)                                              # 不是本仓库的文件
if not p.is_file():
    sys.exit(0)

problems = ""
if p.suffix == ".sh" or p.name in ("cl", "cl-x"):
    hits = [f"  {p.name}:{i}: {ln.strip()}" for i, ln in
            enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1)
            if not ln.lstrip().startswith("#") and SHELL_VAR.search(ln)]
    if hits:
        problems = ("`$VAR` 后面紧跟非 ASCII 会被 bash 吞成变量名的一部分（set -u 下直接 unbound），"
                    "改成 `${VAR}`：\n" + "\n".join(hits))
elif p.suffix == ".py":
    uvx = shutil.which("uvx") or os.path.expanduser("~/.local/bin/uvx")
    # ⚠️ `os.path.exists` 对目录 / 没有执行位的文件也返回 True，而真正执行时才会抛
    #    （`IsADirectoryError` / `PermissionError`）；`timeout=60` 超时抛 `TimeoutExpired`。
    #    2026-10-12 全仓审查核实：这些异常原来**没人接** → hook 以退出码 1 + traceback 结束，
    #    与本文件「要么静默 exit 0、要么 exit 2 回传问题」的约定相反，反而给工具链刷错误。
    if os.path.exists(uvx) and os.access(uvx, os.X_OK):
        try:
            r = subprocess.run([uvx, RUFF, "check", "--no-cache", "--force-exclude", str(p)],
                               cwd=ROOT, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            r = None                                   # 跑不起来 = 当作"没有 uvx"，静默放行
        if r is not None and r.returncode not in (0, 2) and r.stdout.strip():
            problems = "ruff 报了问题：\n" + r.stdout.strip()[:3000]

if problems:
    print(problems, file=sys.stderr)
    sys.exit(2)
sys.exit(0)
