"""路径的唯一定义点 —— **只放 `~/.classlive/` 这一族新路径**。

## 为什么要有这个文件

仓库里既有的小状态文件全是「各自用 `with_name` 算自己的路径」：
`.course`（`cl` 的 `CFG`）· `.window`（`overlay`）· `.deepseek_key`（`cloud_translator`）·
`.update-seen` / `.update-skip`（`main` / `whatsnew`）· `term_notes*.json`（`build_notes`）·
`glossary/`（`translator`）· `sessions/`（`obsidian_writer`）。

开课前的准备要加**两个新位置**（课件归档 + 每课的 prep 状态）。不加这个文件的话，
P3 就会成为**又一批**各自算路径的地方 —— `CLAUDE.md` 对同类问题的判据是
「判据只在**一份实现**里」。

⚠️ **上面一列一律只写「文件 · 符号」，不写行号** —— 行号会腐坏（本文件第一版就写错过一个）。
要定位就 `grep` 那个符号。

## ⚠️ 三套约定并存，故意的

| | 放哪 | 例子 |
|---|---|---|
| **旧** | 代码旁（安装目录） | 上面那一列 |
| **新** | `~/.classlive/` | `courses/<课号>/materials/`、`prep-state.json` |
| **系统惯例** | `~/Library/Logs/ClassLive/` | 日志、`instance.lock`、`update.lock` |

- **新的为什么单开一支**：这里放的是**用户数据**（课件原件），不该住在会被 `cl update`
  的 `git pull` 更新的安装目录里。
- **为什么不干脆放进 `Logs/`**（它也不在仓库里）：`Logs/` 是**可清理**的 ——
  macOS 与各种清理工具都会动它。**用户数据不能住在会被清掉的地方。**
  ⚠️ 顺带：那个 root 今天被**两份文件各定义一次**（`instance_lock` 与 `update`），
  正是本文件想避免的失败形态；P4 搬迁时一并收口。
- 把旧的那一批也搬过来是 **P4「数据自立」** 的事。**两件事别混在一次改动里** ——
  混了就没法判断是哪个改动引起的回归。

## ⚠️ `sessions/` 永不搬

它是**三方共享契约**（`obsidian_writer` 写 / `_parse` 读回 / `cl last` 用 grep 匹配，
见 `CLAUDE.md`），动它会同时打断三处。
"""
from __future__ import annotations

import pathlib

STATE_ROOT = pathlib.Path.home() / ".classlive"


def course_dir(course: str) -> pathlib.Path:
    return STATE_ROOT / "courses" / course


def materials_dir(course: str) -> pathlib.Path:
    """课件归档处 —— 拖进来的 PDF/PPTX 放这里。

    归档（而不只在原地读）的三个理由：① 课件原件可能被用户从下载目录删掉；
    ② 重跑 prep 不必再找原文件；③ P4 的数据目录从这里长出来。
    """
    return course_dir(course) / "materials"


def prep_state(course: str) -> pathlib.Path:
    """prep 的状态文件 —— 记「曾经追加过哪些词」。

    它是**墓碑**：用户从 `glossary/<课号>.txt` 里删掉的词，重跑时不许复活。
    （行业里的对应物是 memoQ 的 `stop word list`，见 `docs/PLAN-p3-prep.md` §6.1.2。）
    """
    return course_dir(course) / "prep-state.json"
