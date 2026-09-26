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
from typing import NamedTuple

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


class Model(NamedTuple):
    """一个要检查的模型。

    `size` 是**下载体积**（不是装完的体积）—— 引导式更新要拿它问
    「要下 X 吗（约 N MB）」，用户关心的是要等多久、占多少带宽。
    体积都是 2026-09-26 实测的：Parakeet 640 MB / Whisper 压缩包 538 MB / VAD 0.61 MB。
    """
    path: str
    label: str
    required: bool
    cmd: str          # ⚠️ 必须**可直接执行**（引导式更新会真的跑它）
    size: str


MODELS = [
    Model("~/models/parakeet-tdt-0.6b-v3-int8", "Parakeet ASR 模型(必需)", True,
          f'{sys.executable} -c "from huggingface_hub import snapshot_download; '
          f"snapshot_download('csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8', "
          f"local_dir='$HOME/models/parakeet-tdt-0.6b-v3-int8')\"",
          "约 640 MB"),
    Model("~/models/vad/silero_vad.onnx", "Silero VAD(必需)", True,
          "mkdir -p ~/models/vad && curl -sL -o ~/models/vad/silero_vad.onnx "
          "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx",
          "约 0.6 MB"),
    Model("~/models/sherpa-onnx-whisper-turbo", "定稿 Whisper 模型(必需)", True,
          "curl -sL -o /tmp/wt.tar.bz2 "
          "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
          "asr-models/sherpa-onnx-whisper-turbo.tar.bz2 && "
          "tar xjf /tmp/wt.tar.bz2 -C ~/models/ && rm /tmp/wt.tar.bz2",
          "约 538 MB（解开后 989 MB）"),
]


def _mark(ok: bool) -> str:
    return "✅" if ok else "❌"


def model_present(path: str) -> bool:
    """模型是不是**真的**就位。

    ⚠️ 不能只看 exists()：下载中断会留下空目录，解压失败也会。用户看到 ✅
       就以为模型可用，直到跑课才炸。目录要求非空、文件要求大小 > 0。
       (2026-09-24 OCR 分块审计发现。)

    ⚠️ **这条判据只有这一份**。`doctor` 自己、`update.pending_steps()`、
    `update.run_step()` 都调它 —— 之前三处各写了一遍（2026-09-26 四个审查代理
    独立都指到了），而它恰恰是"改一处要记得改三处"的典型。
    """
    p = pathlib.Path(os.path.expanduser(path))
    if p.is_dir():
        return any(f.is_file() and f.stat().st_size > 0 for f in p.rglob("*"))
    return p.is_file() and p.stat().st_size > 0


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
    # ⚠️ 运行环境住在 ClassLive.app 里面，不是仓库根目录的 .venv ——
    #    这么放是为了让 macOS 认那个目录为 bundle（授权框才写「ClassLive」而不是
    #    「python3.11」）。根因见 docs/PLAN-p1-app-launcher.md §1.5。(2026-09-26 改)
    app_py = HERE / "ClassLive.app" / "Contents" / "MacOS" / "python"
    if not app_py.exists():
        # ⚠️ 计入硬缺失：没有它就跑不了 `cl`（`cl` 用的就是它），
        # 而且**这个脚本本身**通常也是靠它跑的。原来这里只印 ❌ 不加计数，最后仍
        # 输出"可以跑"并返回 0 —— 与文件头"1 = 有硬缺失"的约定矛盾，也会让按
        # 退出码判断的脚本得到错的结论。(2026-09-24 OCR 分块审计发现。)
        hard_missing += 1
    print(f"{_mark(app_py.exists())} 运行环境      "
          f"{'ClassLive.app 就位' if app_py.exists() else '缺 ClassLive.app → 跑 ./make-app.sh'}")

    # ---- 版本 / 更新 ----
    head, behind = git_info()
    print(f"✅ 代码          {head}")
    if behind:
        print(f"   ↑ {behind}")

    # ---- 依赖 ----
    print()
    for mod, why in REQUIRED:
        # ⚠️ 导入与版本查询**分开判**：`md.version()` 在"能导入但查不到发行元数据"
        # 时会抛 PackageNotFoundError（源码/vendored 安装、发行名与导入名不一致
        # 等），那不该被判成"缺失"—— 那会把一个可用的环境误报成跑不起来。
        # (2026-09-24 OCR 分块审计发现。)
        try:
            importlib.import_module("sherpa_onnx" if mod == "sherpa-onnx" else mod)
        except Exception:                                 # noqa: BLE001
            hard_missing += 1
            print(f"❌ {mod:<18}缺失{(' — ' + why) if why else ''}")
            # ⚠️ 绝对路径 —— 这行是给用户**拷去执行**的，相对路径从别的 cwd 跑就错。
            print(f"   → uv pip install --python {app_py} -r requirements.txt")
            continue
        try:
            print(f"✅ {mod:<18}{md.version(mod)}")
        except Exception:                                 # noqa: BLE001
            print(f"✅ {mod:<18}(已装; 查不到版本号, 不影响可用)")
    for mod, why in OPTIONAL:
        try:
            importlib.import_module(mod)
            print(f"✅ {mod:<18}({why})")
        except Exception:                                 # noqa: BLE001
            print(f"⚪ {mod:<18}未装 — {why}(有兜底, 不影响能用)")

    # ---- 模型 ----
    print()
    for path, label, required, cmd, size in MODELS:
        ok = model_present(path)
        if not ok and required:
            hard_missing += 1
        print(f"{_mark(ok) if ok else ('❌' if required else '⚪')} {label:<30}"
              f"{'就位' if ok else ('空目录/空文件' if os.path.exists(os.path.expanduser(path)) else '缺失')}")
        if not ok:
            # ⚠️ 报体积 —— 引导式更新要拿同一个数问用户「要下 X 吗」，
            #    这里也报出来，两边口径才不会漂。
            print(f"   → 要下 {size}：")
            print(f"     {cmd}")

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
