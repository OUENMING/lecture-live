#!/usr/bin/env python3
"""移进废纸篓 —— macOS 那个**可撤销的删除**。**全项目只此一处碰它。**

## ⚠️ 为什么不是 `rm` / `shutil.rmtree`

「删课」删的是两样**我们不该替用户决定"不要了"**的东西：
`glossary/<课号>.txt`（**他手写的术语表**）与 `courses/<课号>/materials/`
（**课件的原件唯一副本** —— 他很可能已经把下载目录里那份删了）。

废纸篓把「不可撤销」变成「可撤销」，而这一位**直接决定界面该怎么写**：

- `[一手]` HIG › Alerts（现行版）逐字：
  **「Avoid displaying alerts for common, undoable actions, even when they're destructive.」**
  紧接着的反面：「when people take an uncommon destructive action that **they can't undo**,
  it's important to display an alert」。
  → **可撤销 ⇒ 不该弹确认框；不可撤销 ⇒ 必须弹。** 走废纸篓，一条同时满足两头。
- `[一手]` Apple 支持文档《Delete files and folders on Mac》逐字：
  「You start by dragging items to the Trash… but **the items aren't deleted until you
  empty the Trash**.」→ **两段式**：我们只做可逆的那一步，不可逆那一步（清空）
  留给用户自己。

## ⚠️ 不用 `osascript`，也不自己 `shutil.move` 到 `~/.Trash`

- `osascript -e 'tell app "Finder" to delete …'` 是唯一能拿到原生「放回原处」的做法，
  但它**把路径拼进 AppleScript 源码**（路径里的 `"` 能闭合字符串）——
  本项目有一条硬规矩：**用户输入绝不拼进 shell**。不要它。
- 自己 `shutil.move` 到 `~/.Trash/` 不走系统 API → **没有废纸篓元数据**，
  Finder 里不认、也没有「放回原处」。
"""
from __future__ import annotations

import pathlib


def to_trash(path) -> tuple[bool, str]:
    """把 `path`（文件或目录都行）移进废纸篓。返回 `(成功, 失败原因)`。

    ⚠️⚠️ **返回值是三元组，不是 bool —— `if not fm.trashItemAtURL_...(...)` 永远不成立。**
       2026-09-28 本机实测（`ClassLive.app/Contents/MacOS/python`）：

           ok, _url, err = fm.trashItemAtURL_resultingItemURL_error_(url, None, None)
           # 实测返回 → (False, None, NSError Domain=NSCocoaErrorDomain Code=4 …)

       两个 out 参数传 `None`，PyObjC 会把它们**追加到返回值**上，所以拿到三元组。
       而**非空元组恒为真** → 把整个元组当布尔读，**失败会被读成成功**。
       这正是本仓库最怕的那个形状（同类：`subprocess` 的返回码、`bool(msg)` 当成功判据）。

       ⚠️ 传 `objc.NULL` 也不对：实测拿回 `(False, NULL, NULL)` —— **错误被丢掉**，
          连为什么失败都问不出来。传 `None`。

    ⚠️ **幂等**：路径本来就不在 → 也算成功（重入 / 两边同时删不该报错）。

    ⚠️ **悬空符号链接算「还在」。** `Path.exists()` 是**跟随**符号链接的：目标被挪走
       之后它返回 False，于是这里会直接报成功，而**链接本身还在磁盘上**（课卡片也还在）
       —— 静默失败。`is_symlink()` 是把 `os.path.lexists` 那个语义补齐（2026-09-28 审查指出）。
    """
    p = pathlib.Path(path)
    if not (p.exists() or p.is_symlink()):
        return True, ""
    try:
        from Foundation import NSFileManager, NSURL
    except Exception as e:                                    # noqa: BLE001
        return False, f"没有 Foundation（{type(e).__name__}）"
    try:
        ok, _url, err = NSFileManager.defaultManager(
        ).trashItemAtURL_resultingItemURL_error_(
            NSURL.fileURLWithPath_(str(p)), None, None)
    except Exception as e:                                    # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"
    if ok:
        return True, ""
    if not (p.exists() or p.is_symlink()):
        # ⚠️ **竞态**：`exists()` 判完到系统调用之间，别处把它删掉了 —— 系统回的就是
        #    `NSCocoaErrorDomain Code=4`（NSFileNoSuchFileError，docstring 里那个错误码）。
        #    目标已不在原处 = 符合上面那条幂等契约，**不该报成失败**：
        #    调用方（`courses.delete` → 界面）会把这条当错误念给用户听。
        return True, ""
    try:
        why = str(err.localizedDescription() or "")
    except Exception:                                         # noqa: BLE001
        why = str(err or "")
    return False, why or "系统没说原因"
