"""不重建 .app、只换图标：`refresh(app)` —— 图示生成器（`tools/make_icon.py`）变了就当场重画并换上。

为什么要它：面板「立即更新」/ 开课前自动装 / 下课退出时自动装这三条路**都不重建 .app**
（重建要 1–3 分钟，而且不能在 .app 里的 python 正在跑时做），只有终端 `cl update` 会重建。
于是 `make_icon.py` 的改动在不开终端的用户那儿永远不生效（2026-11-20 换 LiveMark 时查出）。
图标只是 `Contents/Resources/AppIcon.icns` 一个文件，签名是 ad-hoc、`Sealed Resources=none`，
换它不破签名 —— 所以单独做这一件小事：比对戳记 → 跑生成器（约 1 s）→ 原子替换 → `touch` bundle
（让 LaunchServices 重新读图标；Dock 上正在跑的那个要下次启动才换）。

对外：`refresh(app, *, run=subprocess.run) -> str`，返回一句状态（"" = 已是最新）；**永不抛**。
"""
from __future__ import annotations

import hashlib
import os
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
GENERATOR = HERE / "tools" / "make_icon.py"
BUILT = HERE / "assets" / "icon" / "ClassLive.icns"
STAMP = ".icon-stamp"          # 放在 Contents/Resources/ 里：记的是「画这张图标用的生成器」的 sha256


def generator_hash(gen: pathlib.Path = GENERATOR) -> str:
    return hashlib.sha256(gen.read_bytes()).hexdigest()


def refresh(app: pathlib.Path, *, gen: pathlib.Path = GENERATOR, built: pathlib.Path = BUILT,
            run=subprocess.run, python: str = sys.executable) -> str:
    try:
        res = app / "Contents" / "Resources"
        if not res.is_dir():
            return ""                                   # 还没构建过 .app：交给 make-app.sh
        want = generator_hash(gen)
        stamp = res / STAMP
        try:
            if stamp.read_text(encoding="utf-8").strip() == want:
                return ""
        except OSError:
            pass
        r = run([python, str(gen)], capture_output=True, text=True, timeout=120)
        if r.returncode != 0 or not built.is_file():
            tail = (r.stderr or r.stdout or "").strip().splitlines()
            return f"图标没换成：{tail[-1] if tail else '生成器失败'}"
        tmp = res / "AppIcon.icns.new"
        tmp.write_bytes(built.read_bytes())
        os.replace(tmp, res / "AppIcon.icns")
        stamp.write_text(want + "\n", encoding="utf-8")
        os.utime(app, None)                             # touch：让 Finder / LaunchServices 重新读
        return "图标已更新（Dock 上下次打开生效）"
    except Exception as e:                              # noqa: BLE001
        return f"图标没换成：{type(e).__name__}: {str(e)[:120]}"
