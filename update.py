#!/usr/bin/env python3
"""共享的更新核心 —— **卡片上的一键更新按钮**和 **`cl update`** 都走这里。

为什么要抽出来：这两条路径的**安全规则必须完全一致**，各写一份迟早在某一边漂移。
规则本身很简单，但错一条就是丢用户的工作。

三条边界（与原 `cl` 里的 bash 实现逐字对齐，改这里时那边也要改）：

  ① **工作区脏就停手** —— 绝不 `stash`、绝不丢弃。那些改动可能是用户自己改的。
  ② **`--ff-only`** —— 本地和远程分叉时**响亮失败**，不静默造一个 merge commit。
  ③ **不替用户决定"可选"的大模型**（Qwen3 那个 938 MB 要不要下是用户的事）——
     它们只能由用户在就绪条上**显式点**。⚠️ **必下的小模型**是**另一回事**：
     `run_step("models")` 会顺手补齐（缺了根本跑不起来）。
     ⚠️ 而**后台自动更新**那条路（`auto_update`）**一个模型都不下** ——
     它只拉代码、并在模型清单有变化时**出一声**（`models_changed`）。
     本模块也不装依赖 —— `requirements.txt` 变了只**报出来**，让调用方决定。
  ④ ⭐ **只更新「正式版那条线」**（2026-09-30 加）—— 站在**开发分支**上、
     或 **detached HEAD**（checkout 过 tag/某个提交）时**一律停手**并说清楚。
     ⚠️ 加它之前那两条路都是**静默**的：开发分支会一直被拉（朋友就此停在
     中间版本），detached 则永远报「已是最新」。见 `branch_state()` 那张表。

⚠️ 与 `cl` 的分工：
    · `cl update`  = 本模块 `pull()` + 之后补依赖 + 跑 `doctor.py`（bash 侧做）
    · 卡片按钮      = 本模块 `pull()`，**不装依赖**（装依赖是"改用户环境"，要用户自己点头）
"""
from __future__ import annotations

import json
import os
import queue
import pathlib
import shlex
import shutil
import subprocess
import sys
import threading
import time

HERE = pathlib.Path(__file__).resolve().parent
# 网络操作的上限。fetch 在慢网下可能很久，但**不能无限等** —— 卡片是后台线程，
# 卡住会让人以为按钮坏了。超时当"没查到"处理，不算失败。
TIMEOUT = 45
DRAFT_MODEL = "draft_model"   # 更新步骤 key：可选的流式草稿模型（`pending_steps` ②b）


def _git(*args: str, timeout: int = TIMEOUT) -> tuple[int, str, str]:
    """跑一条 git。返回 (返回码, stdout, stderr)。异常统一成返回码 -1。"""
    try:
        r = subprocess.run(["git", *args], cwd=HERE, capture_output=True,
                           text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", f"超时（>{timeout}s）"
    except Exception as e:                                # noqa: BLE001
        return -1, "", str(e)


def _git_raw(*args: str, timeout: int = TIMEOUT) -> tuple[int, bytes, str]:
    """同 `_git`，但 **stdout 原样是字节**（不 strip）。

    ⚠️ 只为 `-z` 的路径解析存在：`_git` 会 `.strip()`，而 `git status --porcelain`
    的首行形如 ` M a.py` —— 那个前导空格正是状态码的第一位，洗掉就把路径切错。
    """
    try:
        r = subprocess.run(["git", *args], cwd=HERE, capture_output=True,
                           timeout=timeout)
        return r.returncode, r.stdout, r.stderr.decode("utf-8", "replace").strip()
    except subprocess.TimeoutExpired:
        return -1, b"", f"超时（>{timeout}s）"
    except Exception as e:                                # noqa: BLE001
        return -1, b"", str(e)


def _z_paths(rc: int, raw: bytes) -> set:
    """`git … -z` 输出 -> 路径集合（NUL 分隔，**不解析引号转义**）。"""
    if rc != 0:
        return set()
    return {p.decode("utf-8", "replace") for p in raw.split(b"\0") if p}


def version() -> str:
    f = HERE / "VERSION"
    return f.read_text(encoding="utf-8").strip() if f.exists() else "?"


def _reqs_hash() -> str:
    """requirements.txt 的内容指纹 —— 用来判断"要不要补依赖"。"""
    import hashlib
    f = HERE / "requirements.txt"
    return hashlib.sha256(f.read_bytes()).hexdigest()[:16] if f.exists() else ""


#: 「哪些模型是必下的」那份清单的**唯一定义点**（`models.py:MODELS`；
#: `doctor` 只是 `from models import MODELS` 转出来）。只有这个文件动过，
#: 才**可能**引入新的必下模型。
_MODELS_FILE = "models.py"


def _models_changed(before: str, after: str) -> bool:
    """`before..after` 之间「模型清单」动过没有。

    ⚠️ 这是**保守的代理**，不是精确判据 —— 改个注释也会命中。
       **要的就是这个方向**：宁可多提醒一次，也别静默漏下模型。
    ⚠️ 为什么不用 `_reqs_hash()` 那种「内容指纹」：`requirements.txt` 是**纯数据文件**，
       每次从磁盘读就能拿到最新的；而 `models.py` 是**代码**，`MODELS` 在本进程里是
       **已经 import 过的旧对象** —— 拉完代码也读不到新的。对代码只能问
       「这个提交区间里它变没变」，那正是 git 能直接回答的。
    ⚠️ 前缀 `_` 是刻意的：**只有 `pull()` 会调它**，它不是一个给外面用的判据。
    """
    if not before or not after or before == after:
        return False
    rc, names, _ = _git("diff", "--name-only", f"{before}..{after}")
    return rc == 0 and _MODELS_FILE in names.splitlines()


def is_repo() -> bool:
    return _git("rev-parse", "--git-dir")[0] == 0


def has_upstream() -> bool:
    return _git("rev-parse", "--abbrev-ref", "@{u}")[0] == 0


def upstream_ref() -> str:
    """上游 ref（如 `origin/main`）。**不要硬编码 `origin/main`** —— 远程名或分支名
    跟作者的不一样就全废了（踩过：初版写死 `origin/main`）。
    先问 `@{u}`，再退化到常见组合，最后空串表示查不到。
    """
    rc, name, _ = _git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if rc == 0 and name and name != "@{u}":
        return name
    return ""


def current_branch() -> str:
    """当前分支短名。**detached HEAD 返回空串**（`rev-parse --abbrev-ref` 那时
    会吐出字面量 `HEAD`，不能当分支名用）。"""
    rc, name, _ = _git("rev-parse", "--abbrev-ref", "HEAD")
    return name if (rc == 0 and name and name != "HEAD") else ""


def default_ref() -> str:
    """**远端默认分支**的 ref（如 `origin/main`）。查不到返回空串。

    ⚠️ 为什么不拿 `upstream_ref()` 去推远程名（第一版就是这么写的，**错**）：
       一条**没有上游**的本地分支（`git checkout -b mine`）推不出远程名 →
       默认分支也查不出来 → 那条路就**又**退回「已是最新」的静默卡死。
       所以这里**独立地问每一个 remote**。
    ⚠️ 是**离线**查（`refs/remotes/<r>/HEAD`，clone 时就写好），不联网、不 `remote show`。
    """
    rc, remotes, _ = _git("remote")
    for r in (remotes.splitlines() if rc == 0 else []):
        r = r.strip()
        if not r:
            continue
        rc2, name, _ = _git("symbolic-ref", "--short", f"refs/remotes/{r}/HEAD")
        if rc2 == 0 and name:
            return name
    return ""


def _dirty_report() -> str:
    """工作区里**会挡路的**改动（`git status --porcelain` 原文；空串 = 干净）。

    ⚠️⚠️ **只列「改过的跟踪文件」，不列未跟踪文件**（`--untracked-files=no`，2026-09-30 定）。
       实测四种情形（都是 `git pull --ff-only` 真跑）：

       | 情形 | 结果 |
       |---|---|
       | 未跟踪文件，远端没碰它 | ✅ 拉成功，文件保住 |
       | 未跟踪文件，远端要建**同名** | ⛔ **git 自己拒绝**，文件保住 |
       | 改过的跟踪文件，远端也改了它 | ⛔ **git 自己拒绝**，改动保住 |
       | 改过的跟踪文件，远端改的是别的 | ✅ 拉成功，改动保住 |

       → **未跟踪文件挡不住 `git pull`**：唯一会出事的那一格 git 自己会拦（而且拦得对），
         而我们那道前置检查挡掉的却是最普通的日常（目录里一张下载的 PDF、`.DS_Store`、
         用户自己的笔记）—— 一挡就是**整条更新链一步都不跑**（补依赖 / 重建 `.app` 全没了），
         用户还完全不知道为什么。**作者 2026-09-30 就是被自己那份未跟踪的文件挡住的。**
       → **改过的跟踪文件仍然停手** —— 那是「你动过我们的代码」，边界①不为它松。
    """
    # ⚠️ `core.quotePath=false`（2026-10-07 OCR 审查指出 + 实测）：默认 git 会把
    #    「不常见」字符做 C 转义 —— 中文名会输出成 `"\346\226\207.txt"`，
    #    而这函数的用途恰恰是**把文件名亮给人看**，转义串等于没点名。实测对照：
    #      默认            -> ` M "\346\226\207 \344\273\266.txt"`
    #      quotePath=false -> ` M "文 件.txt"`
    #    ⚠️ 注意 **引号还在**（含空格的名字 git 仍会加引号）→ `_dirty_paths` 要剥。
    rc, out, _ = _git("-c", "core.quotePath=false", "status", "--porcelain",
                      "--untracked-files=no")
    return out if rc == 0 else ""


def _dirty_paths(text: str) -> list[str]:
    """`_dirty_report()` 的 porcelain 原文 → 路径表。

    ⚠️ **为什么要它**（2026-10-07 实测教训）：原来"工作区脏"无论是日志还是卡片上
       都**只说「有改动」，不说改了什么**。于是朋友那台卡了两周，最后是靠他手动跑
       `git status` 截图才定位到——挡路的只是**一个构建产物**。
       把文件名说出来，用户/作者一眼就知道该看哪儿。
    """
    paths: list[str] = []
    for ln in text.splitlines():
        ln = ln.rstrip()
        if len(ln) < 3:
            continue
        # ⚠️⚠️ **不许写 `ln[3:]`**（2026-10-07 实测踩到）：porcelain 是 `XY<空格>路径`
        #    （路径在偏移 3），但 `_git()` 对**整段输出**做了 `strip()` —— 于是**第一行**
        #    的前导空格被吃掉（` M x` → `M x`），固定偏移会把路径首字符切掉
        #    （实测切出 `equirements.txt`）。改成"丢掉 2 位状态码再 lstrip"：
        #    两种形状（有没有被 strip 过）都对。
        p = ln[2:].lstrip()
        # ⚠️ 只有**重命名/复制**（X 或 Y 是 R/C）才切 `old -> new`（2026-10-07 OCR 指出）：
        #    判据写成「路径里含 ` -> `」的话，一个名字里本来就带 ` -> ` 的普通改动文件
        #    也会被切开，报出去的名字就成了错的 —— 而本函数的全部价值就是名字要准。
        if ("R" in ln[:2] or "C" in ln[:2]) and " -> " in p:
            p = p.split(" -> ", 1)[1].strip()
        # ⚠️ **剥引号必须在切分之后**（2026-10-07 OCR 指出，实测踩到）：重命名时 git 给
        #    **两条路径各自**加引号（`R  "old name" -> "new name"`），先剥会把这"两个边缘
        #    引号"误当成一对，剥出 `old name" -> "new name` → 取到 `"new name`（脏的）。
        #    含空格/引号的名字 git 即便 `quotePath=false` 也仍会用 `"…"` 包起来。
        if len(p) >= 2 and p[0] == '"' and p[-1] == '"':
            p = p[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        if p:
            paths.append(p)
    return paths


# ---- 脏的**归类**与「只恢复能证明是产物的那些」----------------------------------
# ⭐ 2026-10-07：那次自锁（构建产物入库）暴露出一个更一般的问题 —— 更新一旦被脏工作区
#    挡下，就**没有任何一步会自己修复它**，用户只会看到一句"你有自己改过的内容"。
#    Homebrew 遇到同一件事的官方做法（docs.brew.sh/Common-Issues "Local changes prevent
#    brew update"）是：列出哪个仓库脏 → 给**专用修复入口**（`brew update-reset`）→
#    **绝不自动做** → 并明文警告「别照抄老 issue 里的 git clean / reset --hard」。
#    这里照它的形状，但**比它保守**：它摧毁全部未提交改动，这里只动**能证明是产物**的。

#: 可以安全恢复的三档；`content` **永远不在里面**。
SAFE_DIRT_KINDS = ("generated", "mode", "eol")


def _is_ignored(path: str) -> bool:
    """这个路径命中 `.gitignore` 吗？**"这是产物"的判据来源。**

    ⚠️⚠️ **必须带 `--no-index`**（2026-10-07 实测）：`git check-ignore` **默认会看索引**,
       **被跟踪的文件一律报"不忽略"**（实测 rc=1）—— 而我们要的恰恰是
       「**既被跟踪、又被声明忽略**」这个组合（= 作者自己说了"这是产物"，
       2026-10-07 那次自锁就是 `assets/icon/ClassLive-1024.png` 两条都占）。
       实测三种形状：未跟踪+--no-index → 0；已跟踪+默认 → **1**；已跟踪+--no-index → 0。
    """
    return _git("check-ignore", "-q", "--no-index", "--", path)[0] == 0


def _mode_changed_paths() -> set:
    """`git diff --summary` 里报"只有权限位变了"的路径（可执行位丢失那一类）。

    ⚠️ **必须带 `HEAD`**（2026-10-07 OCR 指出）：不带它只比"工作区 ↔ 索引"，
       而脏判定 `_dirty_report()` 走 `git status --porcelain`（**含已暂存**）——
       两边口径不一致，已 `git add` 过的权限位改动会掉出这一档、被误判成 `content`。
    ⚠️ **路径要取 `=>` 之后那一整段**（2026-10-07 独立复核 + 实测）：原来写
       `ln.split()[-1]`，而名字含空格时会被截掉 ——
       ` mode change 100755 => 100644 my tool.sh` → 取到 `tool.sh` ≠ 真路径
       → 与 porcelain 对不上 → 误判成 `content` → **拒绝更新还谎称"内容被改过"**。
    """
    rc, out, _ = _git("-c", "core.quotePath=false", "diff", "HEAD", "--summary")
    got = set()
    if rc == 0:
        for ln in out.splitlines():
            if "mode change" not in ln or "=>" not in ln:
                continue
            rest = ln.split("=>", 1)[1].strip().split(None, 1)   # ["100644", "<路径>"]
            if len(rest) == 2:
                got.add(rest[1].strip().strip('"'))
    return got


def _only_eol_differs(path: str) -> bool:
    """工作区与 HEAD 的差异**只有行尾**（CRLF ↔ LF）？

    ⚠️ 同族坑有先例：Homebrew 记过 `bad interpreter: /usr/bin/ruby^M`。取不到就当作
       "不止行尾"（宁可不修，也不误判成安全）。
    """
    return _git("diff", "--quiet", "--ignore-cr-at-eol", "HEAD", "--", path)[0] == 0


def classify_dirt() -> list:
    """把「脏」的每个文件**归类**，决定能不能安全恢复。

    四档（`kind`）：
      `generated` 既被跟踪、又被 `.gitignore` 忽略 → **构建产物**，可安全恢复
      `mode`      只有权限位变了（经 zip / FAT 盘 / 部分云盘传输会丢可执行位）
      `eol`       差异只有行尾（CRLF ↔ LF）
      `content`   内容真被改了 → **用户的东西，绝不碰**

    ⚠️ `generated` 的名单**不手写**：手写清单会腐坏；`.gitignore` 就是作者声明的
       "这些是产物" —— 判据只此一份。
    """
    paths = _dirty_paths(_dirty_report())
    if not paths:
        return []
    modes = _mode_changed_paths()
    out = []
    for p in paths:
        if _is_ignored(p):
            out.append({"path": p, "kind": "generated",
                        "detail": "构建产物（.gitignore 里声明了 → 可安全恢复）"})
        elif p in modes:
            out.append({"path": p, "kind": "mode",
                        "detail": "只有权限位变了（可执行位丢了 → 可安全恢复）"})
        elif _only_eol_differs(p):
            out.append({"path": p, "kind": "eol",
                        "detail": "差异只有行尾（CRLF/LF → 可安全恢复）"})
        else:
            out.append({"path": p, "kind": "content",
                        "detail": "内容被改过 —— 不是我们动的，绝不碰"})
    return out


def repair_generated(*, apply: bool = False) -> dict:
    """把**能证明是产物/权限/行尾**造成的「脏」恢复掉，好让更新能继续。

    ⚠️ **`content` 那一档一个字节都不碰** —— 与 `pull()` 的边界①是同一条纪律
       （「绝不 stash / 绝不丢弃」）。恢复前**先备份**到 `STATE_DIR/dirty-backup/<时间戳>/`。
    ⚠️ 有 `content` 档时**只报不改**：光恢复产物也解不开更新（还得用户自己处理那个文件），
       所以不假装"修好了"。

    `apply=False`（默认）只报**会**做什么。
    返回 `{ok, items, repairable, refused, backup_dir, applied, failed?, error?}`。
    **`ok` 只有一个含义**："恢复之后树能干净"（已恢复或可恢复）。区分"已经做了"看
    `applied` 是否非空 —— 别让 `ok` 同时表示"修好了"和"能修"，那是两种意思。
    """
    items = classify_dirt()
    repairable = [i for i in items if i["kind"] in SAFE_DIRT_KINDS]
    refused = [i for i in items if i["kind"] not in SAFE_DIRT_KINDS]
    out = {"ok": not refused, "items": items,
           "repairable": repairable, "refused": refused,
           "backup_dir": "", "applied": []}
    if refused:
        out["error"] = ("有内容被改过的文件，得你自己处理：" +
                        "、".join(i["path"] for i in refused[:5]))
        return out                      # ⚠️ 有一处用户改动就**整个不动**：只恢复一半会留下
                                        #    一个用户看不懂的"半干净"状态，而更新照样被挡
    if not apply or not repairable:
        return out
    ts = time.strftime("%Y%m%d-%H%M%S")
    bdir = STATE_DIR / "dirty-backup" / ts
    try:
        bdir.mkdir(parents=True, exist_ok=True)
        for it in repairable:
            src = HERE / it["path"]
            if src.is_file():
                # ⚠️ **保留目录结构**（2026-10-07 独立复核指出）：原来只取 `.name`，
                #    于是 `a/gen.png` 与 `b/gen.png` 会写进备份目录的**同一个文件** →
                #    先备份的那份被覆盖，而 `applied` 列着两个、`ok=True` ——
                #    docstring 承诺的"先备份"当场落空。
                dst = bdir / it["path"]
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
        out["backup_dir"] = str(bdir)
    except OSError as e:                                  # 备份不了就**不修**（安全第一）
        out["ok"] = False
        out["error"] = f"备份失败，先不动：{e}"
        return out
    gen = [i["path"] for i in repairable if i["kind"] in ("generated", "eol")]
    if gen:
        # ⚠️ **`checkout HEAD --` 而不是 `checkout --`**（2026-10-07 OCR 指出）：
        #    后者**只从索引**恢复工作区 —— 脏来自**已暂存**的改动时，恢复完
        #    「工作区 == 索引」而索引仍 ≠ HEAD，**树照旧是脏的**，而这里会把路径
        #    填进 `applied`、`ok=True`，与 docstring 承诺的"恢复之后树能干净"不符。
        rc, _, err = _git("checkout", "HEAD", "--", *gen)
        if rc != 0:
            out["ok"] = False
            out["error"] = f"恢复失败：{err[:120]}"
            return out
    applied, failed = list(gen), []
    for i in repairable:                                  # 权限位那一档：chmod 回跟踪值
        if i["kind"] != "mode":
            continue
        try:
            rc, so, _ = _git("ls-files", "-s", "--", i["path"])
            mode = so.split()[0].strip()[-3:] if (rc == 0 and so.strip()) else ""
            if not mode:
                raise ValueError("读不到跟踪的权限位")
            (HERE / i["path"]).chmod(int(mode, 8))
            applied.append(i["path"])
        except (OSError, ValueError) as e:
            # ⚠️ **不许静默 `pass`**（2026-10-07 OCR 指出）：那样 CLI 会打「✅ 已恢复」
            #    而权限其实没改回来、树照旧脏 —— 一次"报成功但没修"的假动作。
            failed.append({"path": i["path"], "error": str(e)})
    out["applied"] = applied
    if failed:
        out["ok"] = False
        out["failed"] = failed
        out["error"] = "有文件没恢复成功：" + "、".join(f["path"] for f in failed)
    return out


def branch_state() -> dict:
    """**这份代码站在哪条线上** —— `pull()` 的守卫与 `cl doctor` 共用这一份判断。

    返回 `{branch, upstream, default, detached, on_default}`：

    | 状态 | 长什么样 | 后果（没这道守卫时**实测**到的） |
    |---|---|---|
    | 正式版线 | `branch=main` · `upstream=origin/main` | 正常更新 |
    | 开发分支 | `branch=feature/x` | `pull()` **只拉那条分支**：main 前进两版它说「已是最新」，分支推新版立刻就拉 |
    | detached | `branch=""`（checkout 过 tag/某个提交） | `@{u}` 不存在 → `rev-list` 报错 → stdout 空 → `behind=0` → **永远「已是最新」，静默卡死** |

    ⚠️ `default` 查不到时 `on_default` 给 **`True`** —— **证据不足时不许吓唬人**。
    """
    br, up, df = current_branch(), upstream_ref(), default_ref()
    return {"branch": br, "upstream": up, "default": df,
            "detached": not br,
            "on_default": (not df) or (up == df)}


def remote_file(name: str, ref: str = "") -> str:
    """读**远端 ref** 上的某个文件，**完全不动工作区**。取不到返回空串。

    ⚠️ 这是"退出时自动更新"能不能安全分级的关键：判断新版本是不是 `auto`
    必须**在 pull 之前**做 —— 磁盘上的 CHANGELOG 还没有那些版本。拉完再判断
    就已经应用了，来不及拒绝。
    """
    r = ref or upstream_ref()
    if not r:
        return ""
    rc, so, _ = _git("show", f"{r}:{name}")
    return so if rc == 0 else ""


# ---- 更新决策用的纯解析（`main.py` 也从这里 import，避免两份实现漂移）----

def versions_in(text: str) -> list[str]:
    """一段 CHANGELOG 文本里所有版本号，新到旧。解析在 `changelog.versions`。"""
    import changelog
    return changelog.versions(text)


def update_mode_in(text: str, version: str) -> str:
    """该版本能否后台自动应用（默认 manual）。解析与理由在 `changelog.update_mode`。"""
    import changelog
    return changelog.update_mode(text, version)


def check() -> dict:
    """只 `fetch` + 看落后几个提交，**不动工作区**。适合启动时/按钮点下去先探一下。

    返回 `{ok, error, dirty, behind, current, ahead, detached, branch, upstream,
    default, on_default}`。
    `ok=False` 表示"查不到"（断网/无 git/无 upstream）—— 调用方应当**静默降级**，
    不要当成错误弹给用户。

    ⚠️ **`detached` 是 `branch_state()` 那个意思**（真的不在任何分支上），
       不是"没有上游"。⚠️ 2026-09-30 之前它确实是后者 —— 一个名字两种含义，
       而 `cl doctor` 正要照着它说话，所以在这里纠正。
    """
    bs = branch_state()
    out = {"ok": False, "error": "", "dirty": False, "behind": 0,
           "current": version(), "ahead": 0, "detached": bs["detached"],
           "branch": bs["branch"], "upstream": bs["upstream"],
           "default": bs["default"], "on_default": bs["on_default"]}
    if not is_repo():
        out["error"] = "不是 git 仓库"
        return out
    if not bs["upstream"]:
        out["error"] = ("不在任何分支上" if bs["detached"]
                        else f"`{bs['branch']}` 没有上游")
        return out
    out["dirty"] = bool(_dirty_report())          # ⚠️ 与 `pull()` **同一个定义点**

    rc, _, err = _git("fetch", "--quiet")
    if rc != 0:
        # 断网 ≠ 失败。本地远端 ref 可能还是旧的，但先告诉调用方"这次没查到"。
        out["error"] = f"fetch 失败: {err or rc}"
        return out

    rc, b, _ = _git("rev-list", "--count", "HEAD..@{u}")
    rc2, a, _ = _git("rev-list", "--count", "@{u}..HEAD")
    out["behind"] = int(b) if b.isdigit() else 0
    out["ahead"] = int(a) if a.isdigit() else 0
    out["ok"] = True
    return out


def _friendly(out: dict) -> str:
    """把 `pull()` 的失败**派生**成一句面向用户的话（无 git 术语）。

    ⚠️ 为什么派生而不是每条失败路径各自填 `user_msg`：
    那样新加的失败路径**会忘记填**，卡片上就会冒出「工作区」「stash」这类词。
    这里只认 `blocked` / 几种已知原因，其余统一给一句通用的话 + 提示看日志。
    """
    err = out.get("error") or ""
    if not out.get("ok") and out.get("blocked"):
        # ⚠️ **几种"主动停手"必须说几句不同的话**：混成一句的话，
        #    站在开发分支上的人会去翻"我改过什么"—— 而他真正该做的是换个分支。
        if "不在任何分支上" in err:
            return ("这份代码停在某个历史版本上（不是正式版那条线），没法自动更新。\n"
                    "   想回到正式版：在 lecture-live 目录里跑 `git checkout main`")
        if "开发分支" in err or "不是正式版" in err:
            return ("这份跟的是一条开发分支 —— 正式版的更新永远不会进来。\n"
                    "   想回到正式版：在 lecture-live 目录里跑 `git checkout main && git pull`")
        if "没有对应的远程" in err:
            return ("这条分支网上没有对应的版本，没法自动更新。\n"
                    "   想回到正式版：在 lecture-live 目录里跑 `git checkout main && git pull`")
        if "撞车" in err:
            # ⚠️ 这一格是 **git 在保护用户的东西**（见 `_dirty_report()` 那张表）：
            #    他自己放了个同名文件，或者改了仓库里已有、而这次更新也要动的文件。
            return ("这个文件夹里有和更新**撞车**的东西（你自己放的文件，或者你改过的文件）——\n"
                    "   这次先不动它，你的东西一个字节都没变。把那个文件挪走或改回去再更新。")
        # ⚠️ **必须把文件名说出来**（2026-10-07 实测教训）：原来只说"有改动"，
        #    朋友那台卡了两周，最后靠他手跑 `git status` 截图才定位到挡路的只是
        #    一个构建产物。名字一亮出来，谁都能立刻判断"这是我改的还是产物"。
        _names = out.get("dirty_files") or []
        if _names:
            _shown = "、".join(_names[:5]) + (f" 等 {len(_names)} 个" if len(_names) > 5 else "")
            return (f"这个文件夹里有你自己改过的内容，这次就先不更新了（怕覆盖掉）。\n"
                    f"   改过的是：{_shown}\n"
                    f"   是你的东西就别动它；要更新，先把它改回去或挪走。")
        return "这个文件夹里有你自己改过的内容，这次就先不更新了（怕覆盖掉）。"
    if "不是 git 仓库" in err:
        return "这个文件夹不是从网上下载的那种，没法自动更新。"
    # ⚠️ 分叉要**先**判：它的 error 里也含「拉取失败」，会被下面那条网络判据吃掉，
    # 于是把"分叉"说成"网络不通" —— 那是误导，两者要做的事完全不同。
    if "分叉" in err:
        return "这个文件夹里的版本和网上的对不上了（你自己改过并提交过），这次先不更新。"
    if "拉取失败" in err or "fetch 失败" in err or "网络" in err:
        return "网络好像不通，这次先不更新了。过会儿再试就行。"
    return "这次没能更新成功（详情看 ~/Library/Logs/ClassLive/ 里的日志）。"


def pull() -> dict:
    """真正应用更新。**先检查再动手**，任何一条边界不满足就返回 `ok=False`。

    返回 `{ok, error, skipped, before, after, commits, log, reqs_changed, models_changed}`。
    `skipped=True` 表示"本来就没啥可更新"（不是失败）。
    """
    out = {"ok": False, "error": "", "skipped": False, "blocked": False,
           "before": version(), "after": version(), "commits": 0, "log": [],
           "reqs_changed": False, "models_changed": False, "user_msg": ""}
    if not is_repo():
        out["error"] = "这不是 git 仓库，没法更新。"
        return {**out, "user_msg": _friendly(out)}

    # ⭐⭐ 3.10：**先收尾上一次被打断的换代码**（合盖 / 强杀落在 `git pull` 中间）。
    #     半换的树既跑不对，又会让下面每一步 git 都失败（实测：陈旧 index.lock
    #     让 `pull` **永远**死在 `Unable to create … index.lock`）。
    #     ⚠️ 修复自己管锁（`_LOCK_MINE`）—— `apply` / `auto_update` 已经持锁时不再抢。
    #     ⚠️ 传 `say`（2026-10-18 审查）：不传的话"回滚了用户自己改的东西"这件事
    #        只落在日志里，用户在终端/卡片上一声不吭。
    _rp = repair_interrupted_apply(say=lambda m: _log(f"repair: {m}"))
    if _rp.get("repaired"):
        out["repair"] = _rp

    # ⚠️⚠️ **边界④：只更新「正式版那条线」**（2026-09-30 加）。放在最前面 ——
    #    它最便宜（不含网络）、也最根本：站在错的线上，后面每一条都白搭。
    #    加它之前 `pull()` 只问 `@{u}`，于是有两条**实测**出来的静默路：
    #      · **开发分支**：它会**一直拉那条分支** —— main 前进两版它说「已是最新」，
    #        而作者往分支推一版，它当场就拉下来（朋友就此停在中间版本）。
    #      · **detached HEAD**：`@{u}` 不存在 → `git rev-list` 报错 → stdout 空 →
    #        `behind=0` → 落进「已是最新」。实测那份离 main 差 2 个提交，照样报最新。
    #    （两种都在沙盒里用真的 `pull()` 复现过，见 `tests/test_update.py` C 组。）
    _bs = branch_state()
    out["branch"] = _bs                      # ⭐ 卡片/日志要能看出停在哪条线上
    if _bs["detached"]:
        out["blocked"] = True
        out["error"] = ("**这份代码不在任何分支上** —— checkout 过某个 tag / 某个提交。\n"
                        "    自动更新拉不动它，正式版也进不来。\n"
                        "    回正式版：git checkout main && git pull")
        return {**out, "user_msg": _friendly(out)}
    if not _bs["upstream"]:
        out["blocked"] = True
        out["error"] = (f"**`{_bs['branch']}` 这条分支没有对应的远程** —— 网上没有它的版本。\n"
                        f"    回正式版：git checkout main && git pull")
        return {**out, "user_msg": _friendly(out)}
    if not _bs["on_default"]:
        out["blocked"] = True
        out["error"] = (f"**这份跟的是开发分支 `{_bs['upstream']}`，不是正式版 "
                        f"`{_bs['default']}`** —— 自动更新只会拉那条线，正式版进不来。\n"
                        f"    回正式版：git checkout main && git pull")
        return {**out, "user_msg": _friendly(out)}

    # ⚠️ 边界①：脏就停手。放在 fetch 之前 —— 连探测都不该动用户的工作区。
    # ⚠️ **「脏」的定义只有一份**（`_dirty_report()`）：只算**改过的跟踪文件**，
    #    未跟踪的不算 —— 那句 docstring 里有实测的四种情形。
    dirty = _dirty_report()
    if dirty:
        out["blocked"] = True
        out["dirty_files"] = _dirty_paths(dirty)      # 卡片那句要点名（见 `_friendly`）
        # `error` 给开发者/命令行看（含 git 术语）；`user_msg` 给**卡片**看。
        # 面向用户那条**不许出现「工作区」「stash」「未提交」**这类词 ——
        # 用工具的人不知道 git，看到这些只会以为哪儿坏了。
        out["error"] = ("工作区里**改过仓库里的文件**，已停手（绝不 stash、绝不丢弃）。\n"
                        + "\n".join("    " + x for x in dirty.splitlines()[:8]))
        return {**out, "user_msg": _friendly(out)}

    head_before = _git("rev-parse", "--short", "HEAD")[1]
    reqs_before = _reqs_hash()

    rc, _, err = _git("fetch", "--quiet")
    if rc != 0:
        out["error"] = f"拉取失败（网络？）：{err or rc}"
        return {**out, "user_msg": _friendly(out)}

    rc, b, _ = _git("rev-list", "--count", "HEAD..@{u}")
    behind = int(b) if b.isdigit() else 0
    if behind == 0:
        out["ok"], out["skipped"] = True, True
        return out

    # ⚠️ 边界②：--ff-only。分叉时这里会失败，那是**想要**的结果。
    # ⭐⭐ 3.10：**换代码之前写 journal** —— 杀/合盖在这之后，下一次任何一条更新路
    #     （`pull` / `auto_update` / `main` 开机那次）都能认出来并补完。
    # ⚠️⚠️ **这一段必须持 `update.lock`**（2026-10-18 审查 D19）：`cl update` 走的是
    #     `_cli()` → 本函数，全程不抢锁 —— 于是"活着的换代码过程一定持着 update.lock"
    #     这个前提**并不成立**：修复可能拿到空闲的锁、看到 index.lock 就删掉另一个 git
    #     进程正在持有的它。没有就自己抢；抢不到（busy）**连 journal 都不写**。
    _mine_pull = False
    if not _LOCK_MINE:
        _lk = _acquire_lock()
        if _lk == "busy":
            out["blocked"] = True
            out["error"] = ("另一个更新正在进行（换代码这一段没抢到锁）—— 这次不动，"
                            "过一两分钟再试。")
            return {**out, "user_msg": _friendly(out)}
        _mine_pull = _lk == "held"        # "error" -> 无锁继续（`_acquire_lock` 已记日志）
    try:
        _write_apply_journal(_git("rev-parse", "HEAD")[1], _git("rev-parse", "@{u}")[1])
        rc, so, se = _git("pull", "--ff-only", timeout=TIMEOUT * 2)
        if rc == 0:
            _rm_journal(apply_journal_path())             # 换完了 -> journal 使命结束
    finally:
        if _mine_pull:
            _release_lock()
    # ⚠️ 失败时**不撤** journal：干净失败（分叉 / 撞车 / 网络）与"git 死在半路"
    #    在这里长得一样，判据归 `repair_interrupted_apply()`（它比对 HEAD/工作区内容）——
    #    干净失败那条路也只是下次启动顺手撤掉，不会白动工作区。
    if rc != 0:
        # ⚠️⚠️ **「拉不动」有三种，别都当成网络问题**（2026-09-30 加第三条）。
        #    实测 git 的两种原文：
        #      · `untracked working tree files would be overwritten by merge`
        #      · `Your local changes to the following files would be overwritten by merge`
        #    —— 那两格**不是失败，是 git 在保护用户的东西**（什么都没丢，见
        #    `_dirty_report()` 那张表），所以标成 `blocked`，措辞也要不一样。
        _low = ((se or "") + (so or "")).lower()
        if "would be overwritten" in _low:
            out["blocked"] = True
            out["error"] = ("这份文件夹里有和更新**撞车**的东西（你自己放的文件，"
                            "或者你改过的文件）—— 已停手，一个字节都没动。\n"
                            f"    原文：{(se or so or '')[:200]}")
            return {**out, "user_msg": _friendly(out)}
        out["error"] = ("拉取失败。常见两种：\n"
                        "    · 网络不通 —— 过会儿再试\n"
                        "    · 本地和远程**分叉**了（你自己在本地提交过）—— 需要你自己决定\n"
                        "      `git rebase origin/main` 还是 `git merge origin/main`\n"
                        f"    原文：{se or so or rc}")
        return {**out, "user_msg": _friendly(out)}

    head_after = _git("rev-parse", "--short", "HEAD")[1]
    out["after"] = version()
    out["commits"] = behind
    out["reqs_changed"] = (_reqs_hash() != reqs_before)
    # ⚠️ **与 `reqs_changed` 对称**：自动更新只拉代码、不下模型（`run_step("models")`
    #    是**手动**那条路才跑的），所以这里必须**出个声** ——
    #    少了它，一个新增的必下模型会**静默**拖到下次启动才暴露。
    #    （2026-09-30 审计发现：原来只有依赖那一半有警告。）
    out["models_changed"] = _models_changed(head_before, head_after)
    out["log"] = _git("log", "--oneline", f"{head_before}..{head_after}")[1].splitlines()
    out["ok"] = True
    return out


# ---- 退出时的自动更新 ------------------------------------------------------

STATE_DIR = pathlib.Path.home() / "Library" / "Logs" / "ClassLive"
RATE_LIMIT_S = 300      # 五分钟最多查一次（2026-10-13 从一小时改短，Owen 同意）。
#                         原来照 atomic「每机器每小时一次 release lookup」；但这里只是一次
#                         `git fetch`，而一小时会挡掉「下课关面板 → 马上又开」那一次 ——
#                         更新就这样一拖再拖。五分钟仍挡得住「连开连关」的抖动。
LOCK_STALE_S = 900      # 锁超过 15 分钟当**陈旧**、自过期 —— 崩溃的更新器不能永久堵住以后

#: ⭐⭐ 3.10：**换代码这一步的 journal**（`apply-journal.json`）。`pull()` 在
#: `git pull` **之前**写下 `{head_before, target}`、换完删掉。它是"换到一半被杀"
#: 这件事唯一的证据 —— 实测（3000 个文件、kill 落在 checkout 中段）：
#:   · 工作区**半新半旧**（298/3000 已是新版）、HEAD 还指着旧提交 → 跑起来是混的；
#:   · `.git/index.lock` 留在那 → 之后**每一次** `git pull` 都死在
#:     `Unable to create '…/index.lock': File exists` —— 更新**永远**装不上。
#: 判据与修法在 `repair_interrupted_apply()`（那份 docstring 就是现场报告）。
APPLY_JOURNAL = "apply-journal.json"

#: 本进程现在握着 `update.lock`（`_acquire_lock` 抢到时置位、`_release_lock` 清）。
#: `repair_interrupted_apply()` 靠它决定"要不要自己再抢一次" —— 已经持锁时再抢
#: 会拿到 `busy`，等于把**自己**的修复挡在门外。
_LOCK_MINE = False


def _log(msg: str) -> None:
    """自动更新是**静默**的 —— 静默失败没有日志就没法排查，所以必须落一行。

    写 `~/Library/Logs/ClassLive/`（与 cl-bg.py 同一处）：不污染仓库、不进 git。
    目录建不出来就放弃（**绝不能因此崩**）。
    """
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with open(STATE_DIR / "update.log", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}\n")
    except Exception:                                     # noqa: BLE001
        pass


def _acquire_lock() -> str:
    """抢「同一时刻只许一个更新器换代码」那把锁。返回 `"held"` / `"busy"` / `"error"`。

    两个持锁方：退出时的后台更新器（`auto_update`）与面板上的「现在更新」（`apply`）。
    ⚠️⚠️ **抢锁必须原子**（2026-09-28 审查指出）：原来是
       `if lock.exists() and 不陈旧: 返回` 然后再 `lock.write_text(...)` ——
       那是 check-then-act，两个更新器可以**同时**通过那一关、各自把锁写下去。
       而这道锁护的正是「同时跑 `git pull` / 下 1.2 GB 模型」。走 `O_EXCL` 排他创建。
    ⚠️ 锁在但**陈旧**（上次崩了没清，超过 `LOCK_STALE_S`）才删掉重抢一次，否则认输 ——
       「删了再抢」本身也可能把别人刚建的锁删掉，所以判陈旧要尽量准。
    ⚠️ `"error"`（抢锁逻辑自己崩了）**不许静默**（2026-10-01 审查 F14）：记一行日志，
       要不要无锁继续由调用方定。
    """
    lock = STATE_DIR / "update.lock"

    def _take() -> bool:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        try:
            os.write(fd, str(os.getpid()).encode("utf-8"))
        finally:
            os.close(fd)
        return True

    global _LOCK_MINE
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        if _take():
            _LOCK_MINE = True
            return "held"
        try:
            stale = (time.time() - lock.stat().st_mtime) >= LOCK_STALE_S
            if stale:
                lock.unlink()
        except OSError:
            stale = False
        got = bool(stale and _take())
        if got:
            _LOCK_MINE = True
        return "held" if got else "busy"
    except Exception as e:                                # noqa: BLE001
        _log(f"⚠ 更新锁处理异常（{type(e).__name__}: {str(e)[:80]}）")
        return "error"


def _release_lock() -> None:
    """⚠️ **只删还属于自己那把锁**（按锁里的 pid 核对）。两个实测/复审出来的坑：
    ① 无条件 unlink 会删掉**别的进程持有的锁**（进程 B 因"已有更新在跑"早退却删了 A 的锁 →
       C 立刻能拿到 → 两个更新器同时 pull；2026-09-25 全量 OCR）；
    ② 只记一个 `held` 标志不够：A 持锁超过 STALE 被 B 抢走后，A 的 finally 仍会删掉 B 的锁。
    """
    global _LOCK_MINE
    try:
        lock = STATE_DIR / "update.lock"
        if lock.read_text(encoding="utf-8").strip() == str(os.getpid()):
            lock.unlink(missing_ok=True)
            _LOCK_MINE = False
    except Exception:                                     # noqa: BLE001
        pass


def apply_journal_path() -> pathlib.Path:
    """换代码那份 journal 的落点。⚠️ 一处定义 —— `main.py` 开机那次检查也读它。"""
    return STATE_DIR / APPLY_JOURNAL


def _rm_journal(j) -> None:
    try:
        pathlib.Path(j).unlink(missing_ok=True)
    except OSError:
        pass


def _index_lock_path() -> pathlib.Path:
    rc, gd, _ = _git("rev-parse", "--git-dir")
    p = pathlib.Path(gd) if (rc == 0 and gd) else pathlib.Path(".git")
    return (p if p.is_absolute() else HERE / p) / "index.lock"


def _rm_stale_index_lock() -> None:
    """删掉一次**死掉的** git 留下的 `.git/index.lock`。

    ⚠️ 只在"我们持着 `update.lock` + journal 在"时才被调到 —— 那证明它属于一次
       被杀掉的 pull（活着的换代码过程一定持着 update.lock，见 `_acquire_lock`）。
    ⚠️ 不删的后果是**实测**出来的：之后每一次 `git pull` 都死在
       `Unable to create '…/index.lock': File exists` —— 更新**永远**装不上。
    """
    try:
        _index_lock_path().unlink(missing_ok=True)
    except OSError:
        pass


def _dirty_tracked() -> set | None:
    """跟踪文件里相对 HEAD 改过（含已暂存）的路径。⚠️ 未跟踪的不算 ——
    `reset --hard` 不碰它们，而"脏"这个词在 `pull()` 里也只算跟踪文件（同源）。

    ⚠️⚠️ **必须区分「命令失败」与「确实没脏」**（2026-10-18 审查 D4）：`_z_paths` 在
       `rc != 0` 时返回**空集**，于是 `git diff` 一旦失败就被下游当成"没有脏文件" →
       `not dirty` 为真、`_worktree_matches(target, [])` 也为真 → 直接
       `git reset --hard target`，反而可能**吃掉用户未提交的改动**。失败一律返回 `None`，
       让判据 fail closed（保留 journal、一个字节不动）。
    """
    rc, raw, _ = _git_raw("diff", "--name-only", "-z", "--no-renames", "HEAD")
    return None if rc != 0 else _z_paths(rc, raw)


def _changed_paths(head_before: str, target: str) -> set | None:
    """`head_before..target` 之间**本次更新本来就要换**的那些路径。失败 -> `None`。

    ⚠️ 判据靠它把"脏"分成两半：与本次更新有交集的（半换现场）vs 用户自己改的别的文件。
       失败返回 `None`（fail closed）—— 返回空集会被当成"跟本次更新毫无交集"→ 撤 journal
       却把半换的树留在原地。
    """
    rc, raw, _ = _git_raw("diff", "--name-only", "-z", "--no-renames",
                          head_before, target)
    return None if rc != 0 else _z_paths(rc, raw)


def _untracked_in_way(changed) -> set | None:
    """`changed` 里那些**工作区存在、但未跟踪**的路径（`reset --hard` 会删掉它们）。

    ⚠️ git 文档原话：*Any untracked files or directories in the way of writing any
       tracked files are simply deleted*。所以"补完"（`reset --hard target`）之前必须
       知道它们 —— 否则用户恰好在 target 新增文件的路径上放了自己的文件就被**静默删掉**，
       而这一格**没有任何备份**（2026-10-18 审查 D2）。失败/`changed` 未知 -> `None`。
    """
    if changed is None:
        return None
    rc, raw, _ = _git_raw("ls-files", "--others", "--exclude-standard", "-z")
    if rc != 0:
        return None
    return {p for p in _z_paths(rc, raw) if p in changed}


# ---- 修复判据：**一个纯函数**（2026-10-18 审查：D2/D3/D4/D20/D22/D23 全挤在这里）----
#: `_decide_repair` 的动作码。每个都对应执行器里**唯一**一段副作用。
R_DONE = "done"            # HEAD == target：只撤 journal（**绝不碰 index.lock**，D22）
R_OTHER = "other"          # HEAD 两边都不是：撤 journal，不动工作区
R_DIVERGED = "diverged"    # 不是快进（本地有分叉）：撤 journal，不动工作区
R_LEAVE = "leave"          # 证据不足 / git 出错：**保留 journal**，一个字节不动（fail closed）
R_CLEAN = "clean"          # 本次更新没写过任何跟踪文件：撤 journal，不动工作区、不碰锁
R_FINISH = "finish"        # 工作区已经等于 target：reset --hard target，补完
R_ROLLBACK = "rollback"    # 半换 / 混着用户的东西：备份 + 回滚本次要换的那几个路径

_R_ACTIONS = (R_DONE, R_OTHER, R_DIVERGED, R_LEAVE, R_CLEAN, R_FINISH, R_ROLLBACK)


class RepairPlan(dict):
    """`_decide_repair` 的结果：`action` / `rm_lock` / `backup` / `roll`。**纯数据**。"""

    def __init__(self, action: str, *, rm_lock: bool = False,
                 backup=(), roll=()):
        super().__init__(action=action, rm_lock=bool(rm_lock),
                         backup=list(backup), roll=list(roll))


def _decide_repair(*, head_now: str, head_before: str, target: str, ancestor: bool,
                   dirty: set | None, changed: set | None,
                   dirty_matches_target: bool, untracked_in_way: set | None,
                   had_lock: bool, mine_lock: bool,
                   repair_failed: bool = False) -> RepairPlan:
    """上一次被打断的换代码**该做什么** —— 唯一一份判据（纯函数，无 I/O）。

    输入全是"已经查好的事实"：`dirty` / `changed` / `untracked_in_way` 为 `None` 表示
    那次 git 查询**失败**（不是"确认是空的"）。优先级从上到下，先命中的赢。

    三条纪律（服务这一版审查的 D2/D3/D4/D20/D22/D23）：

    · **fail closed**：任何 git 查询失败 -> `R_LEAVE`（保留 journal，一个字节不动）。
    · **绝不删可能属于活 git 的锁**：只有"**确实死在半路**"（要动工作区：FINISH/ROLLBACK）
      才删 `index.lock`。HEAD 已在 target 时（那次 pull 早跑完、git 自己会释放锁）碰到
      的锁**一定属于别人** -> 只撤 journal（D22）。干净失败时同理不碰锁（D23①）。
    · **绝不无备份回滚用户自己的改动**：混着用户的改动 -> `R_ROLLBACK`，`backup` 里带着
      要备份的路径，执行器**先备份再回滚**（D23①）。
    """
    # 0. 另一次更新可能还在跑（`mine_lock=False` 却没抢到锁时调用方不会走到这里；
    #    这里只兜底"journal 的写者还活着"由执行器处理）。留作显式一行，便于表驱动。
    if head_now == target:
        # HEAD 已经指到 target -> 那次 checkout **跑完了**（git 在更新 ref 之前就删掉了
        # 自己的 index.lock）。此刻若还有锁，它**不属于**我们那次 pull（D22）。
        return RepairPlan(R_DONE, rm_lock=False)
    if head_now != head_before:
        return RepairPlan(R_OTHER, rm_lock=False)        # 5. 两边都不是
    if not ancestor:
        return RepairPlan(R_DIVERGED, rm_lock=False)      # 4. 不是快进
    if dirty is None or changed is None:
        return RepairPlan(R_LEAVE, rm_lock=False)         # git 失败 -> fail closed（D4）
    if untracked_in_way is None:
        return RepairPlan(R_LEAVE, rm_lock=False)         # 查不出挡路文件 -> 不敢 reset（D2）
    if repair_failed and not dirty:
        # 上一次动手后失败过，这次又读不到脏文件 —— 不敢说"干净地没换成"（D20）。
        return RepairPlan(R_LEAVE, rm_lock=False)
    roll = sorted(dirty & changed)
    if not roll:
        # 本次更新要换的路径**一个都不脏** -> 工作区没被写过（或用户改的是别的文件）。
        # 撤 journal，工作区一个字节不动；**不碰锁**（可能属于别的活着的 git，D23①）。
        return RepairPlan(R_CLEAN, rm_lock=False)
    if dirty <= changed and dirty_matches_target:
        # 脏路径 ⊆ 本次要换的、且内容已经等于 target -> 把那次更新**做完**。
        # ⚠️ 这一条**不看 index.lock**：git 可能死在"已写完 index/工作区、还没更新 HEAD"
        #    那一小段（那时锁已释放）—— 只看锁会判成"干净没换成"、树永远停在半新半旧（D23②）。
        # ⚠️ `reset --hard` 会删掉"挡路的未跟踪文件"（D2）—— 所以 `backup` 把它们带上，
        #    执行器**先备份再 reset**（那次更新新加的文件在 checkout 中段往往还是未跟踪态）。
        # 要动工作区 -> 删掉死 git 留下的锁（不删的话 `reset --hard` 自己就失败）。
        return RepairPlan(R_FINISH, rm_lock=had_lock and mine_lock,
                          backup=sorted(untracked_in_way), roll=roll)
    if not had_lock:
        # 写了一半 / 混着用户的改动，但**没有 index.lock**：git 干净失败/拒绝时会删掉自己
        # 的锁；被杀在 checkout 中段则**一定留着**锁。所以"没锁 + 只脏了本次要换的路径"
        # 说明脏的是**用户自己改的**（git 拒绝之前没写过任何文件）-> 一个字节不动（D23①/F3）。
        return RepairPlan(R_CLEAN, rm_lock=False)
    # 有锁 + 要动工作区 = 确实死在半路。备份 + 回滚**本次要换的**那几个路径（一致性优先），
    # 用户的其它改动一个字节不动；未跟踪文件也一并备份（`checkout` 不碰它们，但留着记录）。
    backup = sorted(set(roll) | set(untracked_in_way))
    return RepairPlan(R_ROLLBACK, rm_lock=mine_lock, backup=backup, roll=roll)


def _worktree_matches(ref: str, paths) -> bool:
    """这些路径的工作区内容**已经等于 `ref`** 吗（`git diff --quiet ref -- paths`）。

    ⚠️ 它是"完成/回滚"的分水岭：只有每个脏文件都**已经是新版内容**时才敢
       `reset --hard target` —— 否则那可能是用户自己改的（`reset` 会把它吃掉）。
    """
    paths = [p for p in paths]
    if not paths:
        return True
    rc, _, _ = _git("diff", "--quiet", "--no-renames", ref, "--", *paths)
    return rc == 0


def _backup_paths(paths) -> pathlib.Path:
    """把路径备份到 `STATE_DIR/dirty-backup/<时间戳>/`（**保留目录结构**）。返回备份目录。

    照 `repair_generated` 的老规矩：动手之前先备份 —— 与"绝不 stash / 绝不丢弃"
    是同一条纪律。
    """
    bdir = STATE_DIR / "dirty-backup" / time.strftime("%Y%m%d-%H%M%S")
    bdir.mkdir(parents=True, exist_ok=True)
    for rel in paths:
        src = HERE / rel
        if src.is_file():
            dst = bdir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    return bdir


def _mark_repair_failed(j) -> None:
    """在 journal 里记一笔「这次动手修过、但失败了」—— 下次运行不许把它当成"干净地没换成"。

    ⚠️ 为什么需要它（2026-10-18 审查 D20）：失败路径会**保留** journal，但可能没留下别的
       证据（那次写操作失败时锁已经被删 / 或本来就没锁）。下次运行读到"没有锁"就落进
       「干净地没换成」→ 撤 journal，而工作区里那几个半换文件原样留着 —— 正是这个功能
       要避免的"永远停在半换状态"，对用户还是一句假话。这一笔让判据转成 fail closed。
    失败一律吞（它只是残局记录，绝不能把它自己的问题带进修复）。
    """
    try:
        p = pathlib.Path(j)
        data = json.loads(p.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            data = {}
        data["repair_failed"] = True
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
    except Exception:                                     # noqa: BLE001
        pass


def repair_interrupted_apply(*, say=None) -> dict:
    """收尾**上一次被打断的那次换代码**（合盖 / 强杀 / 断电落在 `git pull` 中间）。

    返回 `{ok, repaired, action, head_before, target, backup_dir, error, skipped}`。

    ## 为什么必须有它（实测现场，不是假想）

    3000 个文件的一版全量改动、kill 落在 checkout 中段：
      · 工作区**半新半旧**（298 个已是新版、2701 个还是旧版）—— 而 `HEAD` 还指着
        **旧**提交 → 这份树**跑起来是混的**（Python 逐文件 import，混着就是混着）；
      · `.git/index.lock` 留在那 → 之后**每一次** `git pull` 都死在
        `Unable to create '…/index.lock': File exists` —— **永远停在半换状态**。
    "合盖 / 睡着"正是这条路上最常见的打断方式（10-14 那节课的收尾转码就是被合盖
    打断的），而更新器恰恰在**退出时**跑 —— 合盖发生在退出更新里毫不稀奇。

    ## 判据：**不在这一份里**（2026-10-18 审查）

    全部搬进 `_decide_repair()`（纯函数、无 I/O、逐行可表驱动单测）。本函数只做三件事：
    **查事实 -> 问判据 -> 执行动作**。事实 = `HEAD` / journal 里的 `head_before`+`target` /
    是否快进 / `dirty`（git 失败=`None`）/ 本次 `changed`（失败=`None`）/ 挡路的未跟踪文件
    （失败=`None`）/ `index.lock` **在清理之前**在不在。

    三条纪律：**fail closed**（git 查询失败 -> 保留 journal、一个字节不动、出声）、
    **绝不删可能属于活 git 的 `index.lock`**（只在"确实死在半路"、要动工作区时才删）、
    **绝不无备份回滚用户自己的改动**（`R_ROLLBACK` 先备份、再回滚、并出声）。

    ⚠️ 锁：与 `auto_update` / `apply` **同一把**（`update.lock`）。调用方已经拿着
       （`_LOCK_MINE`）就直接干；没有就自己抢，抢不到（`busy`）就跳过 ——
       "同一时刻只有一个换代码的人"这条不破。
    ⚠️ 每一步都**永不抛**：它是收尾/开机路径上的一步，坏了只能"这次没修成"。
    """
    out = {"ok": True, "repaired": False, "action": "", "head_before": "",
           "target": "", "backup_dir": "", "error": "", "skipped": False}

    def _say(s: str) -> None:
        if say is not None:
            try:
                say(s)
            except Exception:                             # noqa: BLE001
                pass

    j = apply_journal_path()
    try:
        if not j.exists():
            return out
    except OSError:
        return out

    mine_here = False
    if not _LOCK_MINE:
        lk = _acquire_lock()
        if lk == "busy":
            return {**out, "skipped": True, "action": "另一个更新在跑 —— 这次不修"}
        mine_here = lk == "held"
    try:
        try:
            data = json.loads(j.read_text(encoding="utf-8")) or {}
            if not isinstance(data, dict):
                data = {}
        except Exception:                                 # noqa: BLE001
            data = {}
        head_before = str(data.get("head_before") or "")
        target = str(data.get("target") or "")
        if not head_before or not target:
            _rm_journal(j)                                # 坏了就没法再指路
            _log("⚠ 换代码 journal 读不出来 —— 已撤掉（工作区没动）")
            return {**out, "action": "journal 坏了（工作区没动）"}
        out["head_before"], out["target"] = head_before, target
        head_now = _git("rev-parse", "HEAD")[1]

        # ⚠️⚠️ `had_lock` 必须在**任何清理之前**取（2026-10-18 审查 D20）：原来先
        #    `_rm_stale_index_lock()` 再判，"有没有 index.lock"这个能区分"干净失败"与
        #    "半换"的证据被自己提前销毁了。
        had_lock = _index_lock_path().exists()
        rc_anc, _, _ = _git("merge-base", "--is-ancestor", head_before, target)
        ancestor = rc_anc == 0
        same_head = head_now == head_before
        changed = _changed_paths(head_before, target) if (same_head and ancestor) else None
        dirty = _dirty_tracked() if (same_head and ancestor) else None
        untracked_in_way = _untracked_in_way(changed)
        if dirty is not None and changed is not None:
            dirty_matches_target = _worktree_matches(target, sorted(dirty))
        else:
            dirty_matches_target = False
        plan = _decide_repair(
            head_now=head_now, head_before=head_before, target=target,
            ancestor=ancestor, dirty=dirty, changed=changed,
            dirty_matches_target=dirty_matches_target,
            untracked_in_way=untracked_in_way, had_lock=had_lock,
            mine_lock=bool(_LOCK_MINE or mine_here),
            repair_failed=bool(data.get("repair_failed")))
        action = plan["action"]
        _log(f"换代码残局判据 -> {action}（HEAD={head_now[:8] or '?'} target={target[:8]} "
             f"脏={'?' if dirty is None else len(dirty)} lock={had_lock} "
             f"上次失败={bool(data.get('repair_failed'))}）")

        if action == R_LEAVE:
            # fail closed：证据不足（git 查询失败 / 上次动手后失败过）—— 保留 journal。
            _log("⚠ 换代码残局证据不足 —— **保留 journal**，工作区一个字节不动")
            _say("上次更新被打断过，但这次判断不了现状 —— 什么都没动，下次再试")
            return {**out, "ok": False, "skipped": True,
                    "action": "证据不足 —— 保留 journal，工作区没动"}
        if action == R_DONE:
            _rm_journal(j)
            _log("收尾上次被打断的更新：HEAD 已在目标提交上 —— 撤掉 journal")
            return {**out, "repaired": True, "action": "上次其实换完了 —— 撤掉 journal"}
        if action == R_OTHER:
            _rm_journal(j)
            _log(f"⚠ 换代码 journal 与现状对不上（HEAD={head_now[:8]}）"
                 f" —— 已撤 journal，工作区没动")
            return {**out, "ok": False, "action": f"HEAD 两边都不是（{head_now[:8]}）"}
        if action == R_DIVERGED:
            _rm_journal(j)
            _log("⚠ 上次换代码不是快进（本地有分叉）—— 已撤 journal，工作区一个字节没动")
            return {**out, "ok": False, "action": "不是快进（分叉）—— 工作区没动"}
        if action == R_CLEAN:
            _rm_journal(j)
            _log("上次换代码没换成（干净地失败 / 拒绝）—— 工作区一个字节没动")
            return {**out, "repaired": True, "action": "上次没换成（干净）—— 工作区没动"}

        # ---- 下面两个动作**都要动工作区**：先删掉那枚死 git 留下的锁（有的话）----
        if plan["rm_lock"]:
            _rm_stale_index_lock()

        if action == R_FINISH:
            # ⚠️ D2：`reset --hard` 会把"挡在 target 新文件路径上的未跟踪文件"直接删掉
            #    （git 文档原话）—— 先备份它们再动手（正常 checkout 里那些是 git 自己的
            #    半成品，但也可能是用户放的同名文件）。
            if plan["backup"]:
                try:
                    out["backup_dir"] = str(_backup_paths(plan["backup"]))
                except OSError as e:
                    _mark_repair_failed(j)
                    _log(f"⚠ 补完更新前备份挡路文件失败（{e}）—— 先不动")
                    return {**out, "ok": False, "error": f"备份失败，先不动：{e}"}
            rc, _, se = _git("reset", "--hard", target)
            if rc != 0:
                _mark_repair_failed(j)                    # D20：下次不许当"干净"
                _log(f"⚠ 收尾被打断的更新失败：{se[:160]}")
                return {**out, "ok": False, "error": f"reset 失败：{se[:120]}"}
            _rm_journal(j)
            _log(f"收尾上次被打断的更新：reset --hard {target[:8]}"
                 f"（{len(plan['roll'])} 个半换文件）")
            _say(f"上次更新被打断过 —— 已补完（{target[:8]}）")
            return {**out, "repaired": True,
                    "action": f"补完更新（{len(plan['roll'])} 个文件）"}

        # R_ROLLBACK：**先备份**，再把"本次要换的那几个路径"回滚到 head_before（一致性优先）
        roll = plan["roll"]
        try:
            bdir = str(_backup_paths(plan["backup"]))
        except OSError as e:
            _mark_repair_failed(j)                        # D20
            _log(f"⚠ 半换现场要回滚，但备份失败（{e}）—— 先不动")
            return {**out, "ok": False, "error": f"备份失败，先不动：{e}"}
        rc, _, se = _git("checkout", head_before, "--", *roll)
        if rc != 0:
            _mark_repair_failed(j)                        # D20
            _log(f"⚠ 半换现场回滚失败：{se[:160]}")
            return {**out, "ok": False, "error": f"回滚失败：{se[:120]}"}
        _rm_journal(j)
        _log(f"⚠ 上次换代码被打断、且工作区还有别的改动 —— 已把半换的 {len(roll)} 个"
             f"文件回滚到旧版（原件备份在 {bdir}）；这次更新没做，下次再说")
        _say(f"上次更新被打断过 —— 已回滚到旧版（原件备份在 {bdir}）")
        return {**out, "repaired": True,
                "action": f"回滚到旧版（{len(roll)} 个文件）", "backup_dir": bdir}
    finally:
        if mine_here:
            _release_lock()


def _write_apply_journal(head_before: str, target: str) -> None:
    """写下"要换代码了"（tmp→rename，永不抛）。写不成**照常换** ——
    大不了这次没有恢复凭据，不能让 journal 把更新拦下来。"""
    try:
        p = apply_journal_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps({"head_before": head_before, "target": target,
                                   "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                                   "pid": os.getpid()}, ensure_ascii=False),
                       encoding="utf-8")
        tmp.replace(p)
    except Exception as e:                                # noqa: BLE001
        _log(f"⚠ 换代码 journal 没写进盘（{type(e).__name__}: {str(e)[:60]}）"
             f" —— 这次换到一半被杀就没人接得住")


def auto_update() -> dict:
    """退出时的自动更新 —— 由 `--auto` 调用，**跑在独立的后台进程里**。

    调研到的业内共识（2026-09-24，见 docs/PLAN-update-mechanism.md §7）：

      · **绝不延迟退出** —— Mozilla Silent Update 的目标原话是
        "We don't want to delay shutdown"。所以这个函数**必须在 detached 子进程里跑**，
        绝不能出现在主进程的退出路径上。
      · **独立进程** —— Firefox 的后台更新器就是独立进程；Automattic 的
        auto-update 也建议 "separate thread (or even a separate process)"。
      · **限频** —— atomic：每机器每小时最多一次 release lookup。
      · **并发保护** —— 两个更新器不能同时换代码；崩溃留下的陈旧锁要能自过期。

    "环境不全"的各种情况一律**静默降级**（只写日志，绝不弹窗、绝不报错）：
    没装 git / 没配 upstream / remote 名不同 / 浅克隆 / 没有 VERSION /
    切了分支 / 磁盘满 / `~/Library/Logs` 不可写 —— 都只是"这次没更新成"。
    """
    out = {"ok": False, "skipped": False, "error": "", "reason": "", "after": ""}
    held = False          # ⚠️ **只删自己拿到的锁** —— 见 `_release_lock`
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:                                     # noqa: BLE001
        return out                                        # 连日志目录都没有 -> 放弃

    try:
        # ---- 限频 ----
        # 用户在面板上点过「下课后自动装」就不限频：两节课间隔不到一小时时，第一节退出那次检查
        # 刚戳过 last_check，第二节退出要兑现的那次同意会被静默挡掉（2026-10-12 外部审查）。
        stamp = STATE_DIR / "last_check"
        if (not _consented_version() and stamp.exists()
                and (time.time() - stamp.stat().st_mtime) < RATE_LIMIT_S):
            return {**out, "skipped": True, "reason": "限频（五分钟一次）"}
        # ---- 并发保护（陈旧锁自过期）：与面板「现在更新」共用同一把锁 ----
        _lk = _acquire_lock()
        if _lk == "busy":
            return {**out, "skipped": True, "reason": "已有更新在跑"}
        held = _lk == "held"                          # "error" = 抢锁逻辑自己坏了：照旧无锁继续（已记日志）
        # ⭐⭐ 3.10：**先修上次被打断的换代码**，而且要在分支 / 脏检查**之前** ——
        #     半换的树会把脏检查带偏（298 个"用户改的"文件 → 静默跳过 → 永远不修）。
        #     ⚠️ 这里持着锁，修复不用再抢（`_LOCK_MINE`）。
        _rp = repair_interrupted_apply()
        if _rp.get("repaired"):
            _log(f"先收尾上次被打断的更新：{_rp.get('action')}")
        try:
            stamp.write_text("", encoding="utf-8")
        except Exception:                                 # noqa: BLE001
            pass

        if not is_repo():
            _log("跳过：不是 git 仓库（可能是解压安装的）")
            return {**out, "skipped": True, "reason": "不是 git 仓库"}
        if not upstream_ref():
            _log("跳过：没有配置上游分支（git remote / branch tracking 不全）")
            return {**out, "skipped": True, "reason": "无上游"}
        # ⭐ **不在正式版那条线上，连网都不探**（2026-09-30 加）——
        #    与 `pull()` 的边界④同一条纪律，在这里多省一次 fetch。
        #    ⚠️ 少了它，退出时那个后台更新器会**一直拉开发分支**，
        #       而那正是"朋友停在中间版本"的那台发动机。
        _bs = branch_state()
        if _bs["detached"] or not _bs["on_default"]:
            _log(f"跳过：不在正式版那条线上（{_bs['branch'] or 'detached'}）")
            return {**out, "skipped": True, "reason": "非正式版分支"}

        st = check()                                      # 只 fetch，不动工作区
        if not st["ok"]:
            _log(f"检查失败：{st['error']}")
            return {**out, "error": st["error"]}
        if st["dirty"]:
            # ⭐ 能证明是**构建产物 / 权限位 / 行尾**的「脏」先恢复（先备份）—— 与面板
            #    「现在更新」（`apply`）同一条路（2026-10-13）。原来退出时的更新器碰到它们
            #    就静默停手，而面板那条路会修：同一台机器两条更新路结论不同。
            #    ⚠️ 有一个 `content`（用户改过的）就**整个不动**（`repair_generated` 自己守着）。
            _dirt = classify_dirt()
            if _dirt and all(d["kind"] != "content" for d in _dirt):
                _rep = repair_generated(apply=True)
                _log("退出更新前先恢复构建产物：" + ("好了" if _rep["ok"] and _rep["applied"]
                                                    else f"没成 —— {_rep.get('error', '')[:120]}"))
                st["dirty"] = bool(_dirty_report())
        if st["dirty"]:
            # ⚠️ **日志必须点名**（2026-10-07）：原来只写"有本地改动"，于是朋友那台
            #    卡了两周、日志里一条线索都没有，最后靠他手动 `git status` 截图才找到。
            _paths = _dirty_paths(_dirty_report())
            _log("跳过：工作区有本地改动（绝不 stash / 绝不丢弃）"
                 + (f" —— 挡路的是：{'、'.join(_paths[:8])}" if _paths else ""))
            return {**out, "skipped": True, "reason": "工作区脏",
                    "dirty_files": _paths}
        if st["behind"] <= 0:
            # 代码已是新的、但上次只拉了代码没补依赖（比如旧版本的自动更新拉来的）→ 顺手补上
            if any(x["key"] == "deps" for x in pending_steps()):
                fin = _finish_in_child(models=False)
                _log("补上次没补的依赖：" + ("好了" if fin["ok"] else f"没成 —— {fin['error'][:160]}"))
            return {**out, "ok": True, "skipped": True, "reason": "已是最新"}

        # ---- 分级：**在拉之前**判断，且要读远端 ref 的 CHANGELOG（磁盘上还没有）----
        remote_ver = remote_file("VERSION").strip()
        remote_log = remote_file("CHANGELOG.md")
        if not remote_ver or not remote_log:
            _log(f"跳过：读不到远端文件（VERSION={bool(remote_ver)} "
                 f"CHANGELOG={bool(remote_log)}）")
            return {**out, "skipped": True, "reason": "读不到远端文件"}
        vers = versions_in(remote_log)
        if st["current"] not in vers:
            _log(f"跳过：本地版本 {st['current']} 不在远端历史里（分叉/改名？）")
            return {**out, "skipped": True, "reason": "版本不在远端历史"}
        todo = vers[:vers.index(st["current"])]
        # 用户在面板上点过「下课后自动装」→ 不晚于那个版本的 manual 也当作已同意
        import changelog
        consent = _consented_version()
        manual = [v for v in todo if update_mode_in(remote_log, v) != "auto"
                  and not (consent and changelog.ver_key(v) <= changelog.ver_key(consent))]
        if manual:
            _log(f"跳过自动更新：{'、'.join(manual)} 标了 manual（要用户自己点）")
            return {**out, "skipped": True, "reason": f"manual: {manual}"}

        r = pull()
        msg = (f"自动更新 {st['current']} → {r.get('after') or '?'} "
               f"ok={r['ok']} 提交={r.get('commits')}")
        if r.get("reqs_changed"):
            msg += "  ⚠️ 依赖有变化，需要用户自己跑 cl update"
        if r.get("models_changed"):
            # ⚠️ 与上面那条**并列**，不是可选装饰：自动更新**不下模型**，
            #    所以这是用户唯一能知道「可能多了一个必下模型」的地方。
            msg += "  ⚠️ 模型清单有变化，需要用户自己跑 cl update"
        if r.get("error"):
            msg += f"  err={r['error'][:160]}"
        _log(msg)
        if r["ok"] and not r.get("skipped"):
            # 依赖用**新代码**补（模型不在退出时下 —— 下次面板就绪条上点）
            fin = _finish_in_child(models=False)
            _log("退出后收尾：" + ("依赖已齐" if fin["ok"] else f"没成 —— {fin['error'][:160]}"))
            _clear_consent_upto(r.get("after", ""))
        # ⚠️ 一并透传 `user_msg` —— 否则正常路径与异常路径的返回结构不一致，
        # 上层（卡片）复用这个返回值时，失败文案会退回含 git 术语的 `error`。
        # (2026-09-25 ocr review 发现)
        return {**out, "ok": r["ok"], "after": r.get("after", ""),
                "error": r.get("error", ""), "user_msg": r.get("user_msg", "")}
    except Exception as e:                                # noqa: BLE001
        _log(f"自动更新异常：{type(e).__name__}: {e}")
        out["error"] = str(e)
        return {**out, "user_msg": _friendly(out)}
    finally:
        if held:
            _release_lock()


def spawn_auto_update() -> bool:
    """在**独立的后台进程**里跑 `auto_update()`，父进程立刻返回。

    ⚠️ 这是"绝不延迟退出"的唯一实现方式。同步跑的话，断网时用户要干等
    45 秒才能关掉工具 —— Mozilla 把 "We don't want to delay shutdown"
    明确列为设计目标，就是因为这种体验不能接受。

    `start_new_session=True` = `setsid`：子进程脱离父进程的会话，
    父进程退出后它继续跑完（把代码拉下来），不受影响。
    """
    try:
        subprocess.Popen(
            [sys.executable, str(HERE / "update.py"), "--auto"],
            cwd=HERE, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
        return True
    except Exception:                                     # noqa: BLE001
        return False


def spawn_draft_model_download() -> bool:
    """在**独立的后台进程**里下那个可选的流式草稿模型（464 MB）。父进程立刻返回。

    ⚠️ 时机（Owen 2026-10-12 定，按我的建议）：**只在下课之后 / 关掉面板之后**开，
       **绝不在上课途中** —— 464 MB 会和云端定稿的音频上传抢同一条教室 Wi-Fi，
       而那正好压在最不该压的时刻。两个调用点就是这两处（`main._draft_model_on_exit`
       与 `entry_launch` 关面板那条）。
    ⚠️ `start_new_session=True`：用户下一句就退出程序，它照样下完 ——
       与 `spawn_auto_update` 同一条纪律（实测：父进程 `os._exit` / SIGKILL /
       SIGHUP 打整个进程组，三种死法它都活到了最后）。
    ⚠️ **「该不该下」由子进程里的 `run_step(DRAFT_MODEL)` 自己判**（幂等）——
       本函数不重复那份判断，否则「这台机器要不要它」就有了两份实现。
    """
    try:
        subprocess.Popen(
            [sys.executable, str(HERE / "update.py"), "--run-step", DRAFT_MODEL],
            cwd=HERE, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
        return True
    except Exception:                                     # noqa: BLE001
        return False


# ══════════════════════════════════════════════════════════════════════
# 引导式更新：更新之后还有哪些"重活"没做
#
# 背景：`cl update`（bash）本来就把四件事串好了 —— 记哈希 → pull → 补依赖 → doctor。
# 但**更新卡片上的「立即更新」按钮只做了 pull**，剩下的要用户回终端敲命令。
# 作者 2026-09-26 的要求就是把这个缺口补上：「能不能也点击立即更新也能更新」。
#
# ⚠️ 设计原则（照抄仓库已有的两条立场）：
#   1. **重活先问、要报大小** —— 点了按钮人就走了，不能静默跑几分钟
#   2. **绝不静默下模型** —— README 明写「模型不会自动下载」（1.6 GB 是用户的决定）
#   3. **正在录课时不自作主张** —— overlay.py 的立场：只换代码、不装依赖、不重启
# ══════════════════════════════════════════════════════════════════════

# ⚠️ 记的是「**上次成功装完依赖时** requirements.txt 的指纹」。
#    用它而不是"这次 pull 有没有改 requirements" —— 后者只在同一次会话里有效，
#    用户今天更新、明天才补依赖就丢了。
#
# ⚠️⚠️ **路径在调用点算，不做成模块级常量**（2026-09-30）。原来写的是
#    `REQS_STAMP = STATE_DIR / "reqs-installed"` —— 它在 **import 那一刻**就绑死了，
#    而测试的沙盒换的是 `update.STATE_DIR`（`new_world()` 逐字这么干）→
#    常量不跟着走 → **沙盒里跑一次 `--mark-reqs` 会写进用户真正的
#    `~/Library/Logs/ClassLive/`**，而且写进去的是**沙盒那份 requirements 的指纹**，
#    于是真机上从此永远显示「补依赖」。正是 `CLAUDE.md` 那条「测试必须隔离写端」
#    要防的形状（只换读端、没换写端）。
def _reqs_stamp() -> pathlib.Path:
    return STATE_DIR / "reqs-installed"


def mark_reqs_installed() -> None:
    """记下"当前这份 requirements.txt 已经装好了"。

    三条装依赖的路都要调它 —— **`cl update`（`cl:145`）、卡片上的 deps 步骤
    （`run_step`）、`make-app.sh`（`./install.sh` 那条）**。少任何一条，
    那条路装出来/更新出来的机器就会永远看到一句假的「补依赖」。
    """
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        _reqs_stamp().write_text(_reqs_hash(), encoding="utf-8")
    except Exception:                                     # noqa: BLE001
        pass


def _child_env() -> dict:
    """给子进程的环境：补两条 Finder 启动时缺失的 PATH。

    ⚠️ 从 `.app` 双击启动时 PATH 只剩 `/usr/bin:/bin:/usr/sbin:/sbin`，
       而 `uv` 在 `~/.local/bin`、`ffmpeg` 在 `/opt/homebrew/bin` —— 都会找不到。
       （路线图 §5.2 实测列过这两条。只**追加**路径，不会挤掉原有的。）
    """
    env = dict(os.environ)
    have = [p for p in env.get("PATH", "").split(os.pathsep) if p]
    for extra in (str(pathlib.Path.home() / ".local" / "bin"), "/opt/homebrew/bin"):
        if extra not in have:
            have.append(extra)
    env["PATH"] = os.pathsep.join(have)
    return env


def _app_stale_reason() -> str:
    """`.app` 该不该重建。**最新就返回空串**，否则返回一句给人看的原因。

    ⚠️ 判据**问 `make-app.sh --up-to-date`**（构建指纹的唯一定义点），
       不在这里重算 —— 本仓的规矩：别在两处各算一遍指纹。
    ⚠️ 是 `--up-to-date` 且判**非零**：这样**脚本自身出错也倒向「重建」**。
       反过来问「stale 吗」时，出错会落成非零 → 被读成「最新」→ **静默跳过**。
       （`make-app.sh` 的退出码方向是刻意反的，那里有完整说明。）
    ⚠️ 拿不到答案（没有 `make-app.sh` / 跑不起来）→ 返回空串：**判断不了就不该凭空多出一个步骤**。
       ⚠️ 这条**靠下面那个 `except` 兜**，不要再在前面加一句 `if not mk.exists()` ——
          那会让「用 mock 假造退出码」的判据（`tests/test_update.py` B11）**落空**，
          而 B11 钉的是**退出码方向**（出错要倒向重建）。2026-09-30 实测踩到过。
    """
    mk = HERE / "make-app.sh"
    try:
        r = subprocess.run([str(mk), "--up-to-date"], cwd=HERE,
                           env=_child_env(), capture_output=True, text=True, timeout=60)
    except Exception:                                     # noqa: BLE001
        return ""
    if r.returncode == 0:
        return ""
    return (r.stdout or r.stderr or "构建输入变了").strip().splitlines()[0]


def pending_steps() -> list[dict]:
    """更新之后还有哪些重活没做。按该做的顺序返回。

    每项：`{"key", "label", "detail", "why"}` —— **只描述，不执行**。
    执行交给 `run_step()`，这样 UI 层可以先问用户「要现在做吗（约 N MB）」。
    """
    steps: list[dict] = []

    # ① 补依赖：requirements.txt 的指纹和"上次装完时"对不上
    cur = _reqs_hash()
    try:
        _stamp = _reqs_stamp()
        done = _stamp.read_text(encoding="utf-8").strip() if _stamp.exists() else ""
    except OSError:
        done = ""
    if cur and cur != done:
        steps.append({
            "key": "deps",
            "label": "补依赖",
            "detail": "约 1–3 分钟",
            "why": "代码更新后依赖清单变了 —— 不补的话可能起不来。",
        })

    # ② 下模型：直接问 doctor（同一份定义，口径不会漂）
    # ⚠️ 判据走 `doctor.model_present()` —— 只有那一份。
    #    之前这里和 `run_step()` 各抄了一遍（四个审查代理独立都指到了）。
    try:
        import doctor
        # 只数**必装**的：可选的（Whisper / Qwen）缺了课照上，`run_step("models")` 也不下它们 ——
        # 列进来就是一张永远消不掉的卡（2026-10-11，`tests/test_update.py` B14）。
        missing = [m for m in doctor.MODELS
                   if m.required and not doctor.model_present(m.path)]
        if missing:
            total = "、".join(f"{m.label.split('(')[0].strip()} {m.size}" for m in missing)
            steps.append({
                "key": "models",
                "label": f"下载模型（{len(missing)} 个）",
                "detail": total,
                "why": "缺模型的话课上会直接起不来（不是降质，是跑不了）。",
            })
        # ②b 流式草稿模型：**可选，但卡片主动提**（作者 2026-10-11 拍板 —— 不提的话用 .app 的人
        #    永远拿不到 R3：草稿回改 5.7% → 0.6%）。和 Qwen3 不同：它**列在卡片上、由用户点「现在做」**，
        #    不是静默下；内存不够 / 手动指定 parakeet 的机器装了也用不上，不提（`draft_engine.want_model`）。
        #    `soft`：下不成**不算更新失败**（草稿照旧用 Parakeet），后面的步骤照跑。
        import draft_engine
        import models
        if not doctor.model_present(models.NEMOTRON.path) and draft_engine.want_model():
            steps.append({
                "key": DRAFT_MODEL,
                "label": "下载流式草稿模型",
                "detail": models.NEMOTRON.size,
                "why": "装上后中文草稿基本不再改来改去（实测回改 5.7% → 0.6%）；下不成也照常上课。",
                "soft": True,
            })
    except Exception as e:                                # noqa: BLE001
        # ⚠️ **别静默**（2026-09-28 审查指出）：`import doctor` 失败 / 探测出错时，
        #    「下载模型」这一步会**从卡片上整条消失** —— 用户看到的是"没有要下的东西"，
        #    而真相是"我们没查出来"。同本模块「软失败不等于静默」那条。
        print(f"⚠ 探模型没探成（{type(e).__name__}: {e}）—— 卡片上不会列「下载模型」",
              flush=True)

    # ③ 重建 .app：锚在上面两条之后（它是最后一道打包，也最重）。
    # ⚠️ 为什么需要：`cl update` **只拉代码、不重建 .app** ——
    #    `make-app.sh` / `tools/make_icon.py` 的改动于是在用户那儿
    #    永远不生效（**图标就是这么丢的**：代码更新了，Dock 上还是旧图）。
    # ⚠️ `VERSION` **不在**触发条件里（2026-10-04 起）：它每版都变，而落点
    #    （Info.plist 的 `CFBundleShortVersionString`）全仓没有一个代码读者 ——
    #    算进去等于每发一版所有用户白重建一次。见 `make-app.sh` 文件头那张表。
    # ⚠️ 判据只有一份（`_app_stale_reason()`），`run_step("app")` 与终端那条路共用它。
    _why = _app_stale_reason()
    if _why:
        steps.append({
            "key": "app",
            "label": "重建 .app",
            "detail": "约 1–3 分钟",
            "why": f"{_why} —— 不重建的话图标 / 启动器改动不会生效。",
        })
    return steps


def download_model(model, *, say=None) -> dict:
    """下**一个**模型。返回 `{"ok", "error"}`。⚠️ 要跑网络 —— 调用方在后台线程里调。

    ⚠️ 为什么单独抽出来：它现在有**两个口径不同**的调用方 ——
      · `run_step("models")`（更新流程）**只下必下的**；
      · 就绪条上用户**显式点**某一条 —— 那条**包括可选的 Qwen3**。
        Qwen3 混进上面那个循环就是静默下 938 MB（2026-09-28 差点这么发出去）。
    """
    import doctor
    if doctor.model_present(model.path):
        return {"ok": True, "error": ""}

    def _say(s: str) -> None:
        if say:
            try:
                say(s)
            except Exception:                              # noqa: BLE001
                pass

    _say(f"正在下 {model.label}（{model.size}）…")
    # ⚠️ 总超时 4 小时（原来 1 小时）：慢网络下 Whisper 的 538 MB 在 100 KB/s 就要 1.5 小时；
    #    真卡死由 `fetch_model.py` 里 curl 的 `--speed-limit/--connect-timeout` 管，不靠这个总超时。
    # ⚠️ `cmd` 现在恒为「解释器 fetch_model.py <key>」（已 `shlex.quote`，`test_fetch_model` 钉着
    #    `shlex.split` 后恰好 3 段）—— 所以**不走 shell**：既不再有注入面，也不依赖 `~` / `&&` 展开。
    #    doctor 仍把同一串打出来给人复制。
    r = subprocess.run(shlex.split(model.cmd), cwd=HERE,
                       capture_output=True, text=True, timeout=4 * 3600)
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip().splitlines()
        return {"ok": False,
                "error": f"{model.label} 下载失败：{err[-1] if err else '未知原因'}"}
    # ⭐ 记戳 —— 「这一份是我们装的、装的是什么」。⚠️ 只有活着的 `ready` 才写，
    #    它不在（比如被裁剪过的安装）就当没这回事，绝不因此让下载算失败。
    try:
        import ready
        ready.mark_installed(model)
    except Exception:                                      # noqa: BLE001
        pass
    return {"ok": True, "error": ""}


def run_step(key: str, on_line=None) -> dict:
    """执行一个 `pending_steps()` 里的步骤。返回 `{"ok", "error"}`。

    ⚠️ 会跑网络 + 写磁盘，**调用方要在后台线程里调**。
    `on_line(str)` 用来把进度回传给 UI（可选）。
    """
    def say(s: str) -> None:
        if on_line:
            try:
                on_line(s)
            except Exception:                             # noqa: BLE001
                pass

    try:
        if key == "deps":
            # ⚠️ 用 sys.executable —— 本模块自己就跑在那个解释器里，永远是对的；
            #    写死路径在布局变动时会变成一句误导。
            r = subprocess.run(
                ["uv", "pip", "install", "--python", sys.executable, "-q",
                 "-r", str(HERE / "requirements.txt")],
                cwd=HERE, env=_child_env(), capture_output=True, text=True, timeout=900)
            if r.returncode != 0:
                err = (r.stderr or r.stdout or "").strip().splitlines()
                return {"ok": False, "error": err[-1] if err else "装依赖失败"}
            mark_reqs_installed()
            say("依赖装好了")
            return {"ok": True, "error": ""}

        if key == "models":
            import doctor
            for m in doctor.MODELS:
                # ⚠️⚠️ **只下必下的。** 可选的（Qwen3-1.7B，938 MB）**必须由用户点**
                #    —— 作者 2026-09-28 拍的口径，也正是 doctor/update 那条
                #    「大模型要不要下是用户的决定」。
                #    ⚠️ 2026-09-28 给 `MODELS` 加那条**可选**条目时，这个循环会
                #       **连它一起下** —— 当场引入的静默下载，正是要防的事。
                #    要下某一条可选的，用下面的 `download_model(m)` 显式点。
                if not m.required:
                    continue
                if doctor.model_present(m.path):
                    continue
                r = download_model(m, say=say)
                if not r["ok"]:
                    return r
            say("模型齐了")
            return {"ok": True, "error": ""}

        if key == DRAFT_MODEL:
            import doctor
            import draft_engine
            import models
            # ⭐ **幂等 + 自己判该不该下**（2026-10-12）：这一步现在也会被
            #   「下课后 / 关掉面板后」那条后台路无条件叫起来
            #   （`spawn_draft_model_download`），所以判断只有落在这里**一份**，
            #   别让两个调用方各写一遍（同 `app` 那一步「先问一次戳记」的取法）。
            if doctor.model_present(models.NEMOTRON.path):
                say("流式草稿模型已经装过了，跳过")
                return {"ok": True, "error": ""}
            if not draft_engine.want_model():
                say("这台机器用不上流式草稿模型（内存不够，或手动指定了 parakeet），跳过")
                return {"ok": True, "error": ""}
            r = download_model(models.NEMOTRON, say=say)
            if r["ok"]:
                say("流式草稿模型装好了（下次上课生效）")
            return r

        if key == "app":
            # 重建整个 .app。make-app.sh 自己会装依赖、写戳记，并把旧的那份
            # 改名备份、失败时恢复 —— 这里只负责把它跑起来。
            # ⚠️ **先问一次要不要重建**：`cl update` 是**无条件**调这一步的
            #    （它不知道戳记对不对），所以这一步必须自己幂等 ——
            #    最新时直接跳过，别让 `cl update` 每次都白等三分钟。
            _why = _app_stale_reason()
            if not _why:
                say(".app 已经是最新的，跳过")
                return {"ok": True, "error": ""}
            say(f"正在重建 .app（{_why}；约 1–3 分钟，期间别退出）…")
            r = subprocess.run([str(HERE / "make-app.sh")], cwd=HERE,
                               env=_child_env(), capture_output=True, text=True,
                               timeout=1800)
            if r.returncode != 0:
                err = (r.stderr or r.stdout or "").strip().splitlines()
                return {"ok": False, "error": err[-1] if err else "重建 .app 失败"}
            say(".app 重建好了")
            return {"ok": True, "error": ""}

        return {"ok": False, "error": f"未知步骤 {key!r}"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "超时了（网络太慢或卡住了）"}
    except Exception as e:                                # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:160]}"}


# ══════════════════════════════════════════════════════════════════════
# 开课前的更新入口（2026-10-12，`docs/PLAN-panel-slim-update.md` §8）
#
# 作者的要求：**以后都不让用户开终端**。面板打开时后台问一次 `offer()`，有事就在就绪条上出一格；
# 用户点「现在更新」→ `apply()`；点「下课后自动装」→ `defer_to_exit()`，由退出时的 `auto_update` 兑现。
# ══════════════════════════════════════════════════════════════════════

EXIT_CONSENT = "install-on-exit"      # STATE_DIR 下：用户同意「下课退出时安装」到哪个版本


def _consented_version() -> str:
    try:
        return (STATE_DIR / EXIT_CONSENT).read_text(encoding="utf-8").strip()
    except Exception:                                     # noqa: BLE001
        return ""


def defer_to_exit(version: str) -> bool:
    """记下「下课退出时把更新装到 `version`」。返回是否记上了。

    ⚠️ 只认**这个版本号**：之后再出的新版本仍按它自己的 `更新方式` 走（manual 就再问一次）。
    """
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        (STATE_DIR / EXIT_CONSENT).write_text(version.strip(), encoding="utf-8")
        return True
    except Exception:                                     # noqa: BLE001
        return False


def _clear_consent_upto(version: str) -> None:
    """装到（或超过）同意的那个版本了 → 删掉同意文件。"""
    import changelog
    c = _consented_version()
    if c and changelog.ver_key(version) >= changelog.ver_key(c):
        try:
            (STATE_DIR / EXIT_CONSENT).unlink(missing_ok=True)
        except Exception:                                 # noqa: BLE001
            pass


def _finish_in_child(*, models: bool, on_line=None, timeout: float | None = None) -> dict:
    """拉完代码之后的收尾（补依赖、补必下模型）**交给新代码的子进程**跑。返回 `{ok, error}`。

    ⚠️ 为什么非得是子进程：调用方（面板 / 退出时的更新器）进程里 import 的是**拉之前**的模块。
       拉完再 import 本仓模块就是新旧混用 —— 判据（`pending_steps`）和动作（`run_step`）
       都该用**新版本**自己的定义（新版本可能刚加了一个必下模型）。
    """
    cmd = [sys.executable, str(HERE / "update.py"), "--finish"]
    if not models:
        cmd.append("--no-models")
    try:
        p = subprocess.Popen(cmd, cwd=HERE, env=_child_env(), stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True)
    except Exception as e:                                # noqa: BLE001
        return {"ok": False, "error": f"收尾没起来：{e}"}
    last = ""
    deadline = time.monotonic() + (timeout or (4 * 3600 if models else 1200))
    assert p.stdout is not None
    # ⚠️⚠️ **不能写 `for line in p.stdout`**（2026-10-12 全仓审查，核实过）：
    #    那是**阻塞**读 —— `deadline` 只在**读到一行之后**才被检查。子进程一行都不吐地
    #    挂住时（网络卡死、或卡在某个不打印的步骤），循环**永久**阻塞调用方
    #    （面板上那是个后台线程 → 按钮一直转；退出时是另一个更新进程 → 僵尸留着），
    #    而 `deadline` 和下面的 `p.kill()` **永远轮不到执行** —— 超时形同虚设。
    #    改成「泵线程 + 带超时的队列」：没数据也能到点。
    q: "queue.Queue[str | None]" = queue.Queue()

    def _pump() -> None:
        try:
            for line in p.stdout:                         # noqa: B023  子进程收尾自然结束
                q.put(line)
        except Exception:                                 # noqa: BLE001
            pass
        q.put(None)                                       # 哨兵：流结束

    def _timed_out() -> dict:
        p.kill()
        try:
            p.wait(timeout=5)                             # 收尸，别留僵尸
        except Exception:                                 # noqa: BLE001
            pass
        return {"ok": False, "error": "收尾超时了（网络太慢？）"}

    threading.Thread(target=_pump, daemon=True, name="finish-stdout").start()
    while True:
        remaining = deadline - time.monotonic()
        try:
            line = q.get(timeout=remaining if remaining > 0 else 0)
        except queue.Empty:
            return _timed_out()                           # ⭐ 一行都没来也能到点
        if line is None:
            break
        line = line.rstrip()
        if not line:
            continue
        last = line
        if on_line and not line.startswith("FINISH"):
            try:
                on_line(line.strip())
            except Exception:                             # noqa: BLE001
                pass
        if time.monotonic() > deadline:                   # 原来那条判据，语义不变
            return _timed_out()
    rc = p.wait()
    if rc == 0:
        return {"ok": True, "error": ""}
    return {"ok": False, "error": last.removeprefix("FINISH fail ").strip() or f"退出码 {rc}"}


def finish(*, models: bool, say=print) -> int:
    """`update.py --finish [--no-models]`：把该补的补齐（依赖；`models=True` 时再加必下模型）。

    `.app` 重建与可选模型（Nemotron / Qwen3）**不在这里**：前者不能在 `.app` 里的 python 正在跑时做，
    后者要用户自己点。最后一行是 `FINISH ok` / `FINISH fail <原因>`（给 `_finish_in_child` 读）。
    """
    keys = ("deps", "models") if models else ("deps",)
    for s in pending_steps():
        if s["key"] not in keys:
            continue
        say(f"正在{s['label']}（{s['detail']}）…")
        r = run_step(s["key"], on_line=say)
        if not r["ok"]:
            say(f"FINISH fail {s['label']}没成：{r['error']}")
            return 1
    say("FINISH ok")
    return 0


def offer() -> dict:
    """开课面板打开时在**后台线程**里问一次：要不要在就绪条上出「更新」那一格。⚠️ 会联网（fetch）。

    返回 `{"kind", "current", "latest", "summary", "message"}`，`kind` 四选一：
      `none`        没事 / 查不到 / 开发分支 —— **不显示**（断网时与以前一模一样）
      `available`   有新版本，`summary` 是 (current, latest] 的看点（读**远端**那份 CHANGELOG）；
                    `groups` 是同一区间按版本分组、带标签的版本（「有新版本」卡用，`changelog.groups`）
      `unfinished`  代码已是新的，但依赖 / 必下模型还没补（比如退出时的后台更新只拉了代码）
      `blocked`     有新版本但装不上（用户改过文件 / 本地提交过），`message` 说清楚为什么
    ⚠️ 不写任何文件。开发分支（作者自己的 worktree）一律 `none` —— 别在开发机上天天提示。
    """
    import changelog
    out = {"kind": "none", "current": version(), "latest": "", "summary": "", "message": "",
           "quiet": False, "groups": []}
    if os.environ.get("CLASSLIVE_NO_UPDATE_CHECK"):
        return out
    try:
        bs = branch_state()
        if bs["detached"] or not bs["upstream"] or not bs["on_default"]:
            return out
        st = check()
        if not st["ok"]:
            return out
        if st["behind"] <= 0:
            steps = [s for s in pending_steps() if s["key"] in ("deps", "models")]
            if steps:
                out.update(kind="unfinished",
                           message="、".join(f"{s['label']}（{s['detail']}）" for s in steps))
            return out
        remote_log = remote_file("CHANGELOG.md")
        out["latest"] = remote_file("VERSION").strip() or "?"
        out["summary"] = changelog.between(remote_log, out["current"], out["latest"])
        out["groups"] = changelog.groups(remote_log, out["current"], out["latest"])
        if st["ahead"] > 0:
            out.update(kind="blocked", message=_friendly(
                {"ok": False, "error": "分叉"}))
            return out
        stuck = [d["path"] for d in classify_dirt() if d["kind"] == "content"]
        if stuck:
            out.update(kind="blocked", message=_friendly(
                {"ok": False, "blocked": True, "error": "dirty", "dirty_files": stuck}))
            return out
        out["kind"] = "available"
        out["quiet"] = _quiet_ok(out["current"], remote_log)
        return out
    except Exception as e:                                # noqa: BLE001
        _log(f"offer 异常：{type(e).__name__}: {e}")
        return {**out, "kind": "none"}


def _quiet_ok(current: str, remote_log: str) -> bool:
    """有新版本时，能不能**面板开着就在后台装**（`kind == "available"` 之后才问）。

    三条全满足才行：
      ① (current, latest] 每一版的 `更新方式` 都是 auto（与退出时 `auto_update` 同一个判据）；
      ② `requirements.txt` 远端与本地一字不差 —— 依赖要装 1–3 分钟，留给下课退出；
      ③ `models.py` 在 HEAD..上游 之间没动 —— 可能多一个必下模型（`_models_changed` 的保守代理）。
    ⚠️ 判不了（读不到远端、版本不在历史里）一律 `False` —— 退回「更新」那一格让用户点。
    """
    vers = versions_in(remote_log)
    if current not in vers:
        return False
    todo = vers[:vers.index(current)]
    if not todo or any(update_mode_in(remote_log, v) != "auto" for v in todo):
        return False
    ref = upstream_ref()
    if not ref or _models_changed("HEAD", ref):
        return False
    rc, reqs, _ = _git("show", f"{ref}:requirements.txt")
    if rc != 0:
        return False
    # ⚠️ `_git` 的输出是 strip 过的 → 两边都 strip 再比（首尾空白不影响装什么）
    try:
        local = (HERE / "requirements.txt").read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return reqs.strip() == local


def spawn_quiet_apply():
    """面板开着、`offer()["quiet"]` 为真 → 在**独立进程**里把新代码拉下来。返回 `Popen` 或 `None`。

    ⚠️ 为什么是独立进程而不是面板里的线程：用户随时会关面板（或点「开始上课」让面板退出），
       线程会跟着进程死在 `git pull` 半路、锁也没放。`start_new_session=True` 的子进程照样跑完。
    ⚠️ 面板拿着 `Popen` 只为一件事：「开始上课」要**等它跑完**（`main.py` 是新进程，要读新代码，
       不能读到拉了一半的工作区）。
    """
    try:
        return subprocess.Popen(
            [sys.executable, str(HERE / "update.py"), "--quiet-apply"],
            cwd=HERE, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
    except Exception:                                     # noqa: BLE001
        return None


def apply(on_line=None, *, models: bool = True) -> dict:
    """面板上点了「现在更新」（或「把没装完的装完」）。⚠️ 会联网、可能要几分钟 —— **后台线程里调**。

    顺序：抢锁 → 恢复能证明是产物/权限/行尾的「脏」（先备份）→ `pull()` → 新代码子进程收尾。
    返回 `{ok, pulled, after, user_msg}`；`pulled=True` = 代码换了，调用方该**重开自己**
    （本进程里的模块是旧的）。收尾失败不算更新失败：代码已经是新的，缺依赖时各处会自己降级，
    下次打开面板 `offer()` 会报 `unfinished` 再给一次机会。
    `models=False`：收尾只补依赖、不下模型（面板开着时的后台安装用 —— 1 GB 不该在开课前下）。
    """
    def say(s: str) -> None:
        if on_line:
            try:
                on_line(s)
            except Exception:                             # noqa: BLE001
                pass

    out = {"ok": False, "pulled": False, "after": version(), "user_msg": ""}
    lk = _acquire_lock()
    if lk == "busy":
        return {**out, "user_msg": "另一个更新正在进行（可能是下课后的自动安装），过一两分钟再点。"}
    try:
        dirt = classify_dirt()
        if dirt and all(d["kind"] != "content" for d in dirt):
            say("先把几个构建产物恢复原样（原件已备份）…")
            repair_generated(apply=True)
        say("正在下载新版本…")
        r = pull()
        if not r["ok"]:
            _log(f"面板更新没成：{r.get('error', '')[:160]}")
            return {**out, "user_msg": r.get("user_msg") or _friendly(r)}
        out.update(ok=True, pulled=not r.get("skipped"), after=r.get("after", out["after"]))
        _log(f"面板更新 {r.get('before')} → {out['after']} 提交={r.get('commits')}")
        fin = _finish_in_child(models=models, on_line=say)
        if not fin["ok"]:
            _log(f"面板更新收尾没成：{fin['error'][:160]}")
            out["user_msg"] = f"新版本已装好，但{fin['error']} —— 下次打开会再试一次。"
        _clear_consent_upto(out["after"])
        return out
    finally:
        if lk == "held":
            _release_lock()


def _cli() -> int:
    """命令行：`python update.py [--check] [--quiet] [--auto] [--mark-reqs] [--steps]`。"""
    check_only = "--check" in sys.argv
    quiet = "--quiet" in sys.argv

    if "--mark-reqs" in sys.argv:
        # `cl update` 装完依赖后叫它 —— 否则终端路径装完了，卡片还以为没装
        mark_reqs_installed()
        return 0

    if "--steps" in sys.argv:
        # 引导式更新用：还有哪些重活没做（给脚本/调试看）
        for s in pending_steps():
            print(f"{s['key']}\t{s['label']}\t{s['detail']}")
        return 0

    if "--run-step" in sys.argv:
        # ⭐ `cl update` 用：把某一步**真的跑起来**（与卡片上「立即更新」**同一条路**，
        #    所以「要不要做」的判断也只有一份）。⚠️ 步骤本身幂等（比如 `app` 会自己
        #    先问戳记），所以调用方可以无条件调它。
        _i = sys.argv.index("--run-step")
        _key = sys.argv[_i + 1] if len(sys.argv) > _i + 1 else ""
        _r = run_step(_key, on_line=lambda s: print(f"   {s}", flush=True))
        if not _r["ok"]:
            print(f"⚠ {_key or '(没给 key)'} 没做成：{_r['error']}")
            return 1
        return 0

    if "--dirt" in sys.argv:
        # 卡片用：脏在哪、能不能安全恢复（JSON，给 UI 读）
        print(json.dumps(classify_dirt(), ensure_ascii=False, indent=1))
        return 0

    if "--repair" in sys.argv:
        # `--repair` 只报计划；`--repair --apply` 才动手（照 Homebrew：专用入口，绝不自动做）
        r = repair_generated(apply="--apply" in sys.argv)
        if not r["items"]:
            print("工作区是干净的，没什么要修。")
            return 0
        _done = set(r["applied"])
        for i in r["repairable"]:
            # ⚠️ 逐条判**它自己**有没有成功（2026-10-07 OCR 指出）：原来只要 `applied`
            #    非空，就对**所有** repairable 打「✅ 已恢复」—— 包括 `chmod` 失败那几个。
            print(f"  {'✅ 已恢复' if i['path'] in _done else '· 未恢复'} {i['path']}"
                  f"  —— {i['detail']}")
        if r["refused"]:
            print("⚠ 这些是**你自己改过**的，我没动（要更新得你自己处理）：")
            for i in r["refused"]:
                print(f"     {i['path']}")
        if r["applied"]:
            print(f"（原件已备份到 {r['backup_dir']}）")
        elif r["repairable"]:
            # ⚠️ 别一口咬定"这是预览"（2026-10-07 OCR 指出）：备份失败 / checkout 失败
            #    时 `applied` 也是空的，而用户**已经**加了 `--apply` —— 真实原因得说出来。
            if r.get("error"):
                print(f"⚠ {r['error']}")
            else:
                print("（这只是预览。真要恢复：加 --apply）")
        return 0 if r["ok"] else 1

    if "--auto" in sys.argv:
        auto_update()
        return 0

    if "--quiet-apply" in sys.argv:
        # 面板开着时的后台安装（`spawn_quiet_apply`）：结果只进日志，退出码给面板看
        _r = apply(models=False)
        _log(f"面板后台安装：ok={_r['ok']} → {_r.get('after')} {_r.get('user_msg', '')[:120]}")
        return 0 if _r["ok"] else 1

    if "--finish" in sys.argv:
        # 拉完代码后的收尾（`apply` / `auto_update` 用**新代码**的子进程叫它）
        return finish(models="--no-models" not in sys.argv,
                      say=lambda x: print(x, flush=True))

    if check_only:
        r = check()
        if not r["ok"]:
            if not quiet:
                print(f"（查不到更新：{r['error']}）")
            return 0
        if r["behind"]:
            print(f"落后远程 {r['behind']} 个提交。")
        else:
            print(f"✅ 已经是最新的 ({r['current']})。")
        return 0

    r = pull()
    if not r["ok"]:
        print(f"❌ {r['error']}")
        return 1
    _rp = r.get("repair") or {}
    if _rp.get("repaired"):
        print(f"🧰 {_rp.get('action')}")
    if r["skipped"]:
        print(f"✅ 已经是最新的 ({r['after']})。")
        return 0
    print(f"✅ {r['before']} → {r['after']}   ({r['commits']} 个提交)")
    if r["log"]:
        print("\n   这次改了什么:")
        for x in r["log"]:
            print(f"     {x}")
    if r["reqs_changed"]:
        print("\n🔧 依赖清单有变化 —— 需要你自己装（本模块不替你改环境）：")
        # ⚠️ 用 sys.executable 而不是写死路径 —— 本模块自己就跑在那个解释器里，
        #    它永远是对的；写死一个路径在布局变动时会变成一句误导。
        print(f"   uv pip install --python {sys.executable} -r requirements.txt")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
