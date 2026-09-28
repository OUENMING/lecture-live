#!/usr/bin/env python3
"""`~/.classlive/` 下那族**状态文件**的读写约定 —— 版本号 + 原子写。

## 它解决的问题

2026-09-28 架构评估时量到：`~/.classlive/` 下有六种状态文件，
**各管各的读法、没有一个带版本号**：

```
courses/<课号>/prep-state.json   课术语表的墓碑
voice/voice-enroll.json          声纹注册状态机
voice/profiles.json              声纹档案（**唯一真源**，读不回来）
models.json                      模型版本戳
credentials                      DeepSeek key（纯文本，不走这里）
ready-dismissed                  就绪条关过没有（纯文本，不走这里）
```

→ 六份都走 `try/except → 默认值`。**改任何一种格式 = 老文件被静默重置**。
`profiles.json` 那条已经修成抛异常了，其余五份还是「读不出当空」。

## ⚠️ 一条硬规则：**「读不懂」不许被当成「空的」**

这与 `minutes` 那条「**截断必须说出来**」、以及 `voice.load_store` 那次是**同一条纪律**：
**负结果不许读起来像穷尽**。

- 文件**不存在** → 给默认值。这是**全新安装**，是真的什么都没有。
- 文件在、但**读不出来**（坏 JSON / 顶层类型不对）→ **抛**。它可能是用户唯一那份数据。
- 文件在、`_v` 是**我们不认识的版本** → **抛**。那多半是「装过更新的版本又退回来」，
  按老格式去解释它只会解释错。

⚠️ 具体后果：`load_json` 给空 → 调用方加一条 → `save_json` **把真文件整个覆盖掉**。
   这不是「少读了一点」，是**静默全损**。

## 为什么是独立一个模块

`paths.py` 的回答是「**文件在哪**」，本模块回答「**文件长什么样**」。
两件事分开，是因为改格式的节奏和改路径的节奏完全不同。
"""
from __future__ import annotations

import json
import pathlib

# ⚠️ **今天所有状态文件都是 v1** —— 这个数只有在**真的改了格式**时才动，
#    而且动的时候要在这里写清楚 v1→v2 怎么迁（现在没有迁移代码，因为还没改过）。
V = 1

# 版本号的键名。带下划线前缀，免得和任何业务字段撞名。
K = "_v"


class StoreError(RuntimeError):
    """读不出来 / 版本不认识。

    ⚠️ 单独一个异常类，好让调用方**能只接这一种** —— 而不是顺手 `except Exception`
       把「磁盘满了」「权限不对」也一起吞了（那是本仓库栽过的形状）。
    """


def stamp(obj: dict) -> dict:
    """给一份要写出去的数据盖上版本号。**不改入参**。

    ⚠️ `obj` 展开在**前**：反过来的话，调用方手里那份 dict 一旦含 `_v`
       （手工拼装的、或从旧代码里带来的），就会**盖掉本模块写的版本号**，
       记账当场失效（2026-09-28 审查指出）。
    """
    return {**obj, K: V}


def load_json(path, default=None):
    """读一份状态文件。**三条路，三种结果**（见模块头那条硬规则）。

    · 不存在      → `default`（调用方给的，通常是 `{}` 或一份全新状态）
    · 坏 / 类型错 → 抛 `StoreError`
    · 版本不认识  → 抛 `StoreError`
    """
    p = pathlib.Path(path)
    if not p.exists():
        return default if default is not None else {}
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise StoreError(
            f"状态文件读不出来：{p}\n  {type(e).__name__}: {e}\n"
            f"  ⚠️ 这里**不**当它是空的 —— 那会让下一次写入把真内容整个覆盖掉。\n"
            f"  处理：修好它，或者确认不要了再手工移走。") from e
    if not isinstance(obj, dict):
        raise StoreError(f"状态文件顶层该是 dict，实际是 {type(obj).__name__}：{p}")
    got = obj.get(K)
    if got is None:
        # ⚠️ 没有 `_v` = **本约定上线之前写的文件**。按 v1 认它 ——
        #    否则今天所有老文件一夜之间全变「不认识」，等于把用户数据判死刑。
        return obj
    if isinstance(got, bool) or not isinstance(got, int) or got < 1:
        # ⚠️ 三条要分开挡（2026-09-28 审查指出）：
        #    · `bool` 是 `int` 的子类 —— `_v: true` 过得了 `isinstance(_, int)`，
        #      而且 `True == 1` 就会**静默当成 v1**；
        #    · 类型错（`_v: "x"`）与「版本更新」原来共用一句报错，排障的人会被
        #      误导去怀疑版本回退 —— 那是两件完全不同的事；
        #    · 0 / 负数根本不是版本号。
        raise StoreError(
            f"状态文件的版本号不是个正经版本（{K}={got!r}）：{p}\n"
            f"  ⚠️ 版本号是从 1 开始的整数。这个值既不是它，就别猜它的意思。")
    if got > V:
        raise StoreError(
            f"状态文件是更新的版本（{K}={got}，本程序只认 ≤{V}）：{p}\n"
            f"  ⚠️ 多半是「装过更新的版本又退回来」。按老格式解释它只会解释错，\n"
            f"     所以这里抛，而不是猜。处理：用回新版本，或手工备份后移走它。")
    if got < V:
        # ⚠️ 今天走不到（只有 V=1），但**必须留着**：将来把 V 提到 2 的那一刻，
        #    老文件（`_v: 1`）会走到这里。少了这一条，它会**静默按 v2 的格式解释** ——
        #    正是本模块文件头点名要防的那个失败形态。
        raise StoreError(
            f"状态文件是旧版本（{K}={got}，本程序现在是 {V}）：{p}\n"
            f"  ⚠️ 本模块**还没有迁移代码** —— 所以这里抛，而不是按新格式猜着读。\n"
            f"     处理：写迁移（文件头那段就是迁移该写的地方），"
            f"或手工备份后移走它。")
    # ⚠️ **版本号在返回前剥掉** —— 它是本模块的记账，不是业务字段。
    #    留着的话「存进去再读回来」就不等于原来那份，每个调用方都得记得 `pop(_v)`，
    #    而漏掉的那个（迟早有）会把 `_v` 当成一条业务数据写回去。
    #    （2026-09-28 由 `tests/test_store.py` 的往返判据抓出来。）
    return {k: v for k, v in obj.items() if k != K}


def save_json(path, obj: dict) -> None:
    """原子写（同目录唯一 `.tmp` + `replace`），自动盖版本号。

    ⚠️ 原子写是**必须的**：这些文件被 GUI 与后台线程两头读写
       （`voice.py` / `ready.py` / `prep.py` 的文件头都写了这条）。
    ⚠️ 临时名**必须唯一**（2026-09-28 审查指出）：写死 `<name>.tmp` 的话，
       两个线程同时 `save_json` 同一个 path 会**交错写同一个临时文件** ——
       而 `replace` 只保证"发布出去的那一刻是完整的"，挡不住 A 已经把 tmp
       `rename` 走、B 还攥着那个 fd 继续往里写，最终落盘的可能是**两份内容的混血**。
       `pid` + 线程 id 就够区分并发的写者了（同进程多线程、或两个进程）。
    """
    import os
    import threading
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(stamp(obj), ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(p)
