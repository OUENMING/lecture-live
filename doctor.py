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


# ⚠️⚠️ **模型注册表已经搬到 `models.py`**（2026-09-28）。
#    为什么搬：它原来住在这里，于是 `vad.py`（核心音频模块）要用 VAD 的路径就得
#    `import doctor`（一个**诊断脚本**）—— **依赖方向是反的**。而且一个模型的身份
#    当时散在 3~4 处（这里 · `main.py` 的 argparse 默认值 · `vad.py` 的常量 ·
#    `testmode.py` 的目录名元组），换一个模型要同时改四处、而且它们会漂。
#    `models.py` **只有数据、没有 I/O**，谁都能 import 而不触发副作用。
# ⚠️⚠️ **只 import `MODELS`，别 import `REQUIRED`** —— 本文件上面第 29 行已经有一个
#    同名的 `REQUIRED`（那是**硬依赖清单**，`(包名, 用途)` 的列表）。两个都叫
#    `REQUIRED` 的话后来者覆盖先来者，而 import 在下面 → **依赖自检会拿到模型元组**。
#    要用必下模型就写 `[m for m in MODELS if m.required]`。
from models import MODELS  # noqa: F401


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


def hf_cached(repo_id: str, *, must: tuple = ("config.json",),
              any_of: tuple = ("model.safetensors", "model.safetensors.index.json",
                               "pytorch_model.bin")) -> bool | None:
    """HF cache 里**跑得起来**的那几个文件在不在。`True` / `False` / ⚠️ `None`。

    ⚠️⚠️ **不要用 `snapshot_download(local_files_only=True)` 当判据** ——
       它问的是「整个 repo 的文件全不全」，而 `mlx_lm.load()` **只下它要用的那些**。
       实测（2026-09-28，本机）它抛 `IncompleteSnapshotError`，缺的是
       **`.gitattributes` 和 `README.md`** —— 两个文档文件，而
       `model.safetensors`（938 MB 权重）、`config.json`、tokenizer **全都在**。
       拿它当判据就会给一个**完全可用的模型**报「缓存可能不完整」——
       正是本仓库最忌讳的那种谎报。

    `must` 全要有；`any_of` 里**任一**有即可（权重可能是单文件，也可能是分片索引，
    两种布局都得认）。`config.json` 与权重分列两边，是为了不让「只缓存了配置」
    这种半截状态蒙混过去。

    ⚠️ `try_to_load_from_cache` 是**离线**的（不联网），返回缓存的真实路径，
       没缓存时返回 `None` 或 `_CACHED_NO_EXIST` 哨兵 —— 所以判据是
       `isinstance(got, str)`，不是真值判断。

    ⚠️ `None`（`huggingface_hub` 导不进来）的倒向是 **`unknown`（当成有）**，
       不是 `missing` —— 与 `model_present` 那条「不确定就别报缺」同一条纪律。
    """
    try:
        from huggingface_hub import try_to_load_from_cache
    except ImportError:
        return None
    try:
        for f in must:
            if not isinstance(try_to_load_from_cache(repo_id, f), str):
                return False
        return any(isinstance(try_to_load_from_cache(repo_id, f), str)
                   for f in any_of)
    except Exception:                                     # noqa: BLE001
        return None                                       # 问不出来 → 当成有


def model_state(model, *, root=None) -> str:
    """`"missing"` / `"ok"` / ⚠️ `"unknown"` / ⚠️ `"stale"` —— **不是 `bool`**。

    ⚠️⚠️ **`unknown` 绝不等于 `missing`。** 老用户 / 手动装的模型**没有戳**
       （`~/.classlive/models.json` 是 2026-09-28 才有的东西），它们**能用** ——
       判成 `missing` 就是白烧几百 MB 流量重下一份。同族错误：
       `readiness_line` 那条「把读失败显示成 0，等于跟用户谎报」。

    ⚠️ **只认规范路径**（`MODELS[i].path`）—— 「一台机器两份」就靠这条堵：
       下载只写那一个路径，核实也只读那一个路径。

    ⚠️ HF cache 那条要**两个判据配合**：`model_present()` 答「有没有」，
       `hf_cached()` 答「完不完整」。只有前者会**把下了一半的报成 ✅**
       （HF 中途留下 `blobs/*.incomplete`，大小 > 0）。第二个答不出来 → `unknown`。
    """
    if not model_present(model.path):
        return "missing"
    if model.at_hf and hf_cached(model.src) is not True:
        # ⚠️ 可能是「下了一半」，也可能是「问不出来」——**两种都不自动下**。
        return "unknown"
    stamp = _stamp_for(model.path, root=root)
    if stamp is None:
        return "unknown"                                   # 没戳：老用户 / 手动装的
    if model.src and stamp.get("src") != model.src:
        return "stale"
    if stamp.get("fp") and stamp["fp"] != manifest_fp(model.path):
        return "stale"                                     # 文件被人动过 / 换过版
    return "ok"


def manifest_fp(path: str) -> str:
    """一份**清单指纹** —— 排序后的 `(相对路径, 字节数)` 列表的 sha256。

    ⚠️ **只取路径与大小，不读文件内容。** 989 MB 的 Whisper 树逐字节哈希要几十秒，
       而这里要抓的是「文件被换过/删过/改过大小」，尺寸清单就够。
       （换掉一个同字节数的不同模型，这条抓不到 —— 但那不是现实里的失败形态。）

    ⚠️ 跳过 `.incomplete` / `.locks` —— HF 的半成品文件会让同一份模型的指纹每次都变。
    """
    import hashlib
    p = pathlib.Path(os.path.expanduser(path))
    rows: list[str] = []
    if p.is_dir():
        for f in sorted(p.rglob("*")):
            if not f.is_file():
                continue
            if f.name.endswith(".incomplete") or f.suffix == ".lock":
                continue
            try:
                rows.append(f"{f.relative_to(p)}\t{f.stat().st_size}")
            except OSError:
                continue
    elif p.is_file():
        rows.append(f"{p.name}\t{p.stat().st_size}")
    return hashlib.sha256("\n".join(rows).encode()).hexdigest()[:32]


def _stamp_for(path: str, *, root=None) -> dict | None:
    """读 `models.json` 里那一条。**只读** —— 写戳是 `ready.py` 的事。

    ⚠️ **`root` 要一路透传**（2026-09-29 修）：`ready.mark_installed(model, root=…)`
       按 `root` **写**，而这里原来按**默认路径读** → 隔离运行时读写分家。
       （本仓库的硬规矩「测试必须隔离写端 —— 读端和写端都要替换」。）
    """
    import json
    try:
        import paths
        obj = json.loads(paths.models_stamp(root=root).read_text(encoding="utf-8"))
    except Exception:                                     # noqa: BLE001
        return None
    got = obj.get(path) if isinstance(obj, dict) else None
    return got if isinstance(got, dict) else None


def version() -> str:
    f = HERE / "VERSION"
    return f.read_text(encoding="utf-8").strip() if f.exists() else "?"


def git_info() -> tuple[str, str | None, str | None]:
    """-> (一行描述, 落后提示或 None, 分支提示或 None)。

    ⚠️ 第三条是 **2026-09-30 加的**：`update.pull()` 从那天起**只更新正式版那条线**
       （开发分支 / detached HEAD 一律停手 —— 那两种状态下它以前是**静默**的：
       开发分支会一直被拉、detached 永远报「已是最新」）。
       → 所以 doctor **必须把这件事说出来**：不然一个停在开发分支上的人看到的是
       「✅ 可以跑」，而他的更新**永远是停的**。
    """
    if not shutil.which("git") or not (HERE / ".git").exists():
        return "(非 git 仓库, 无法自检更新)", None, None

    def run(*a):
        return subprocess.run(["git", *a], cwd=HERE, capture_output=True,
                              text=True, timeout=10).stdout.strip()
    try:
        head = run("log", "--oneline", "-1") or "?"
        behind = run("rev-list", "--count", "HEAD..@{u}")
        n = int(behind) if behind.isdigit() else 0
        return (head, (f"落后远程 {n} 个提交 → 运行 `git pull`" if n else None),
                _branch_note())
    except Exception:                                     # noqa: BLE001
        return "(git 查询失败)", None, None


def _branch_note() -> str | None:
    """现在站在**哪条线**上。判断全在 `update.branch_state()`（**唯一定义点**），
    这里只把事实说成人话 —— 两处各判一次迟早会漂。"""
    import update
    bs = update.branch_state()
    if bs["detached"]:
        return ("⚠️ 这份代码**不在任何分支上**（checkout 过某个 tag / 某个提交）"
                "—— 自动更新拉不动它，正式版也进不来。\n"
                "      回正式版：`git checkout main && git pull`")
    if not bs["on_default"]:
        return (f"⚠️ 跟的是**开发分支** `{bs['upstream'] or bs['branch']}`，"
                f"不是正式版 `{bs['default']}` —— 正式版的更新进不来。\n"
                f"      回正式版：`git checkout main && git pull`")
    if not bs["upstream"]:
        return f"⚠️ 分支 `{bs['branch']}` 网上没有对应的版本 —— 自动更新拉不动"
    return f"分支 {bs['branch']}（正式版线）"


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
    head, behind, branch_note = git_info()
    print(f"✅ 代码          {head}")
    if behind:
        print(f"   ↑ {behind}")
    if branch_note:
        print(f"   {branch_note}")

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
    for m in MODELS:
        # ⚠️ 用属性，别位置解包 —— 2026-09-28 给 `Model` 加 `mb`/`src`/`at_hf` 时
        #    这里还写着 `for path, label, required, cmd, size in MODELS`，
        #    于是 `doctor.py` 自己**当场 ValueError 崩掉**。加字段时这类解包是隐形的雷。
        ok = model_present(m.path)
        if not ok and m.required:
            hard_missing += 1
        print(f"{_mark(ok) if ok else ('❌' if m.required else '⚪')} {m.label:<30}"
              f"{'就位' if ok else ('空目录/空文件' if os.path.exists(os.path.expanduser(m.path)) else '缺失')}")
        if ok:
            # ⭐ 新增：`就位` 不等于「是我们要的那版」（2026-09-28 起）。
            #    ⚠️ 只在**能说点什么**的时候多打一行 —— 问不出来（老用户没戳）
            #       不该在这里刷屏，那是正常状态。
            st = model_state(m)
            if st == "stale":
                print("   ⚠️ 版本对不上（戳在 ~/.classlive/models.json）—— 能用，要不要换由你定")
        if not ok:
            # ⚠️ 报体积 —— 引导式更新要拿同一个数问用户「要下 X 吗」，
            #    这里也报出来，两边口径才不会漂。
            print(f"   → 要下 {m.size}：")
            print(f"     {m.cmd}")

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
