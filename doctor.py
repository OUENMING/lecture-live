#!/usr/bin/env python3
"""`cl doctor` —— 只读自检: 版本 / 依赖 / 模型 / 术语表, 缺什么告诉你跑哪条命令。

设计原则(按 Homebrew 官方 troubleshooting 文档的三条):
  1. 每条警告都要能读懂;
  2. 只报**与能不能用有关**的;
  3. 不因为"看起来不相关"就省略 —— 全列出来。

⚠️ 这个脚本**只读、只打印**。绝不下模型、绝不装依赖、绝不改配置。
   作者有一条硬规矩是"不擅自改系统设置"; 顺带的推论就是"也不擅自改用户的项目环境"。
   1GB 的模型要不要下, 是用户的决定, 不是这个脚本的。

退出码: 0 = 能用(可能有可选缺失); 1 = 有硬缺失, 跑不起来。
"""
from __future__ import annotations

import importlib
import importlib.metadata as md
import os
import pathlib
import shutil
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent

# 硬依赖: 缺了跑不起来
REQUIRED = [
    ("sherpa-onnx", "ASR + VAD"),
    ("numpy", None),
    ("sounddevice", "麦克风/系统声采集"),
]
# 软依赖: 缺了有兜底或只是少个模式
OPTIONAL = [
    ("httpx", "云端翻译"),
    ("mlx_lm", "断网时的本地翻译兜底"),
    ("AppKit", "悬浮窗(缺了回退终端)"),
    ("huggingface_hub", "首次下模型用"),
]
MODELS = [
    ("~/models/parakeet-tdt-0.6b-v3-int8", "Parakeet ASR 模型(必需)",
     "见 README 安装段"),
    ("~/models/vad/silero_vad.onnx", "Silero VAD(必需)",
     "见 README 安装段"),
    ("~/models/sherpa-onnx-whisper-turbo", "定稿增强模型(可选, ~1GB)",
     "curl -sL -o /tmp/wt.tar.bz2 "
     "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
     "asr-models/sherpa-onnx-whisper-turbo.tar.bz2 && tar xjf /tmp/wt.tar.bz2 -C ~/models/"),
]


def _mark(ok: bool) -> str:
    return "✅" if ok else "❌"


def version() -> str:
    f = HERE / "VERSION"
    return f.read_text(encoding="utf-8").strip() if f.exists() else "?"


def git_info() -> tuple[str, str | None]:
    """-> (一行描述, 落后提示或 None)。"""
    if not shutil.which("git") or not (HERE / ".git").exists():
        return "(非 git 仓库, 无法自检更新)", None
    def run(*a):
        return subprocess.run(["git", *a], cwd=HERE, capture_output=True,
                              text=True, timeout=10).stdout.strip()
    try:
        head = run("log", "--oneline", "-1") or "?"
        behind = run("rev-list", "--count", "HEAD..@{u}")
        n = int(behind) if behind.isdigit() else 0
        return head, (f"落后远程 {n} 个提交 → 运行 `git pull`" if n else None)
    except Exception:                                     # noqa: BLE001
        return "(git 查询失败)", None


def main() -> int:
    hard_missing = 0
    print(f"\nClassLive {version()} · 自检\n" + "─" * 46)

    # ---- 运行环境 ----
    print(f"✅ Python        {sys.version.split()[0]}")
    if sys.version_info[:2] != (3, 12):
        print("   ⚠️ 本项目在 3.12 上实测, 其他版本未验证")
    venv = HERE / ".venv"
    print(f"{_mark(venv.exists())} 虚拟环境      {'.venv 就位' if venv.exists() else '缺 .venv → 见 README 安装段'}")

    # ---- 版本 / 更新 ----
    head, behind = git_info()
    print(f"✅ 代码          {head}")
    if behind:
        print(f"   ↑ {behind}")

    # ---- 依赖 ----
    print()
    for mod, why in REQUIRED:
        try:
            importlib.import_module(mod if mod != "sherpa-onnx" else "sherpa_onnx")
            v = md.version(mod)
            print(f"✅ {mod:<18}{v}")
        except Exception:                                 # noqa: BLE001
            hard_missing += 1
            print(f"❌ {mod:<18}缺失{(' — ' + why) if why else ''}")
            print(f"   → uv pip install --python .venv/bin/python -r requirements.txt")
    for mod, why in OPTIONAL:
        try:
            importlib.import_module(mod)
            print(f"✅ {mod:<18}({why})")
        except Exception:                                 # noqa: BLE001
            print(f"⚪ {mod:<18}未装 — {why}(有兜底, 不影响能用)")

    # ---- 模型 ----
    print()
    for path, label, cmd in MODELS:
        p = pathlib.Path(os.path.expanduser(path))
        ok = p.exists()
        if not ok and "可选" not in label:
            hard_missing += 1
        print(f"{_mark(ok) if ok else ('⚪' if '可选' in label else '❌')} {label:<28}"
              f"{'就位' if ok else '缺失'}")
        if not ok:
            print(f"   → {cmd}")

    # ---- 数据文件 ----
    print()
    gl = HERE / "glossary.txt"
    print(f"{_mark(gl.exists())} 术语表        "
          f"{'glossary.txt 就位' if gl.exists() else '缺 glossary.txt → cp glossary.example.txt glossary.txt'}")
    if not gl.exists():
        print("   (缺了也能跑, 只是不做术语注入)")

    # ---- 结论 ----
    print("─" * 46)
    if hard_missing:
        print(f"❌ 有 {hard_missing} 项硬缺失, 现在跑不起来。按上面的 → 逐条补齐。\n")
        return 1
    print("✅ 可以跑。`cl` 启动。\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
