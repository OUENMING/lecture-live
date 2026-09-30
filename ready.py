#!/usr/bin/env python3
"""就绪状态 —— 「这台机器现在能不能上课」。

## 它解决的问题

新用户今天要走 **6 条终端命令**（`README.md:200-247`：`./install.sh` → 下 Parakeet →
下 VAD → 拷术语表 → 下 Whisper）。**中间任何一步失败，双击进去的界面什么都不说** ——
面板今天**根本没有模型概念**。缺模型时 `doctor.py` 只在终端打印命令。

这个模块算「三项就绪」，面板把它画成顶部一条就绪条。

## ⚠️ 三条硬纪律（每一条都有对应的判据）

1. ⚠️⚠️ **`unknown` 绝不等于 `missing`。** 老用户 / 手动装的模型**没有戳**
   （`~/.classlive/models.json` 是 2026-09-28 才有的），它们**能用** ——
   判成缺失就是白烧 1.2 GB 流量重下一份。同族错误：`entry_panel.readiness_line`
   那条「把读失败显示成 0，等于跟用户谎报」。
2. ⚠️ **`stale` 不自动换。** 换版是**用户的决定**（与 `doctor.py` / `update.py`
   那两处「大模型要不要下是用户的决定」一致）。只说出来，给按钮。
3. ⚠️ **Qwen3（本地翻译模型）绝不自动下。** 它是**可选的**，由用户点才下。
   而它今天恰恰是**静默下**的（`mlx_lm.load()` 隐式 `snapshot_download` 938 MB）——
   本模块把它搬到明面上。

## ⚠️ 纯逻辑，不碰 AppKit

所有外部输入（麦克风权限、模型状态、有没有 API key）**从参数进来**，
脏活集中在 `collect()` 一处。这样 `tests/test_ready.py` 能盖住全部文案与状态机，
不用起窗口、不用真模型。
"""
from __future__ import annotations

import doctor

# 三项的 key（面板按它排布，测试按它断言）
MIC, MODELS, ENGINE = "mic", "models", "engine"

# ⚠️ 状态只有这几个值，面板与判据都按它分支（**别在别处再发明一个字符串**）
OK, TODO, WARN, UNKNOWN, BUSY = "ok", "todo", "warn", "unknown", "busy"

# 麦克风权限四态 -> 就绪条上的状态。`unknown` 见 `notice.mic_permission` 的注释：
# 「调用方必须按老行为继续」—— 查不出来**不是**「没权限」。
_MIC = {
    "authorized": OK,
    "notDetermined": TODO,
    "denied": WARN,
    "restricted": WARN,
    "unknown": UNKNOWN,
}

_MIC_TEXT = {
    "authorized": "已允许",
    "notDetermined": "还没授权 —— 点一下，系统会问",
    "denied": "被拒了 —— 点一下去系统设置里打开",
    "restricted": "这台机器被策略限制住了",
    "unknown": "查不出来（不影响上课）",
}


def mic_item(perm: str) -> dict:
    """第一项。`perm` = `notice.mic_permission()` 的返回值。"""
    return {"key": MIC, "label": "麦克风",
            "state": _MIC.get(perm, UNKNOWN),
            "detail": _MIC_TEXT.get(perm, "查不出来（不影响上课）")}


def required_left(states: dict) -> list:
    """**还缺**的必下模型（`states` = `{path: state}`）。

    ⚠️ **只有 `missing` 算缺。** `unknown`（老用户没戳）与 `stale`（版本对不上）
       都**不算** —— 它们能用，重下是白烧流量（纪律 1、2）。

    ⚠️⚠️ **查不到的那一项按 `unknown` 算，不许按 `missing` 算**（2026-09-29 修）。
       `states` 可能是**部分字典**（上游 `model_states` 失败 / 调用方漏填），
       而 `missing` 会**直接给出重下入口** —— 对 `qwen3` 那种 1GB 级的东西，
       误报一次就是白烧一遍流量。上面那条纪律「`unknown` ≠ `missing`」**就是指这个**，
       而原来 `states.get(m.path, "missing")` 恰好把它写反了。
    """
    return [m for m in doctor.MODELS
            if m.required and states.get(m.path, "unknown") == "missing"]


def models_item(states: dict) -> dict:
    """第二项。`states` = `{path: state}`，由 `model_states()` 算。"""
    left = required_left(states)
    stale = [m for m in doctor.MODELS
             if m.required and states.get(m.path) == "stale"]
    unknown = [m for m in doctor.MODELS
               if m.required and states.get(m.path) == "unknown"]
    if left:
        mb = sum(m.mb for m in left)
        n = len(left)
        return {"key": MODELS, "state": TODO, "label": "语音模型",
                "detail": f"还差 {n} 件，约 {_mb(mb)}",
                "left": [m.path for m in left], "mb": mb}
    if stale:
        # ⚠️ 说出来但**不动手** —— 换版是用户的决定（纪律 2）
        return {"key": MODELS, "state": WARN, "label": "语音模型",
                "detail": f"{len(stale)} 件是旧版 —— 能用，要不要换由你定",
                "left": [], "mb": 0.0}
    if unknown:
        return {"key": MODELS, "state": OK, "label": "语音模型",
                "detail": "齐了（版本没法核实，能用就行）", "left": [], "mb": 0.0}
    return {"key": MODELS, "state": OK, "label": "语音模型",
            "detail": "齐了", "left": [], "mb": 0.0}


def engine_item(*, has_key: bool, local_state: str) -> dict:
    """第三项 —— ⭐ **这是个选择，不是一个状态**。

    ⚠️ 两边都**不选也能上课**：没配 key 时现状就回退本地。所以这一项永远
       不返回 TODO（那会让整条就绪条看起来"没准备好"，而实际上可以上课）。
    ⚠️ **本地模型绝不自动下**（纪律 3）—— 只给一个 [下载] 动作。
    """
    if has_key:
        return {"key": ENGINE, "state": OK, "label": "翻译引擎",
                "detail": "云端翻译（推荐）", "offer_local": local_state != "ok"}
    if local_state == "ok":
        return {"key": ENGINE, "state": OK, "label": "翻译引擎",
                "detail": "本地模型（免费 · 离线可用 · 质量不如云端）",
                "offer_local": False}
    return {"key": ENGINE, "state": UNKNOWN, "label": "翻译引擎",
            # ⚠️ 2026-09-30 改的措辞：原来写「云端翻译（推荐）· 或下载本地模型」——
            #    读起来像**说明**；改成「点一下填 key」才像**入口**
            #    （那一项现在真的能点了，见 `entry_panel.on_ready_click`）。
            "detail": "点一下填 key（推荐）· 或下本地模型",
            "offer_local": True}


def items(*, perm: str, states: dict, has_key: bool) -> list[dict]:
    """就绪条要画的三项，按显示顺序。"""
    return [mic_item(perm),
            models_item(states),
            engine_item(has_key=has_key,
                        local_state=states.get(_local_path(), "missing"))]


def _local_path() -> str:
    """可选的本地翻译模型那一条的 `path`。**由 `doctor.MODELS` 定义，不在这里写死。**"""
    for m in doctor.MODELS:
        if not m.required:
            return m.path
    return ""


def _mb(v: float) -> str:
    """体积说人话。⚠️ 与 `doctor.Model.size` 同一套口径。"""
    return f"{v / 1024:.1f} GB" if v >= 1024 else f"{v:g} MB"


# ---------------------------------------------------------------- 脏活都在这儿
def model_states(*, root=None) -> dict:
    """`{path: state}` —— 逐个问 `doctor.model_state()`。

    ⚠️ **`root` 必须传下去**（2026-09-29 修）：原来收了 `root` 却**从不使用**
       → 本模块按 `root` **写戳**、却按**默认路径读戳**，隔离运行时两边分家。
       生产环境 `root=None` 时看不出差别，所以一直没被发现。
    """
    return {m.path: doctor.model_state(m, root=root) for m in doctor.MODELS}


def mark_installed(model, *, root=None) -> None:
    """记下「这一份是我们装的、装的是什么」。

    ⚠️ **写戳的动作在这里，不在 `doctor.py`** —— 那份文件的契约是
       「**只读、只打印**。绝不下模型、绝不装依赖、绝不改配置」，别去破坏它。

    ⚠️ 失败**静默**：戳只是"锦上添花"，写不进去（权限/磁盘）不该拦住装好的模型。
    """
    import time

    import paths
    import store
    try:
        p = paths.models_stamp(root=root)
        try:
            obj = store.load_json(p, default={})   # `_v` 已由 store 剥掉
        except store.StoreError:
            # ⚠️⚠️ **放弃写入，不许拿空壳覆盖**（2026-09-29 修）。
            #    原来这里是 `obj = {}` 然后**继续往下 save_json** —— 那会把
            #    **其他所有模型的戳**一次抹掉（文件里只剩刚写的那一个）。
            #    上面那句「倒向是 unknown（不重下），安全」**只对读那一侧成立**：
            #    读的倒向确实安全，**写的倒向是"整份丢"**。
            #    ⚠️ `StoreError` 单独一个异常类存在的理由，就是让调用方
            #       **放弃写入**（见 `store.py` 的类 docstring）—— 这里正是那个约定。
            print(f"⚠ 模型戳读不出来，这次**不写**（免得抹掉别的模型的记录）：{p}",
                  flush=True)
            return
        obj[model.path] = {"src": model.src, "at": time.time(),
                           "fp": doctor.manifest_fp(model.path)}
        store.save_json(p, obj)
    except Exception:                                     # noqa: BLE001
        pass


def dismiss(*, root=None) -> None:
    """用户把就绪条关掉了。⚠️ 写 `~/.classlive/`（`Logs/` 会被清理工具清掉）。"""
    import paths
    try:
        p = paths.ready_state(root=root)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("1", encoding="utf-8")
    except Exception:                                     # noqa: BLE001
        pass


def dismissed(*, root=None) -> bool:
    import paths
    try:
        return paths.ready_state(root=root).exists()
    except Exception:                                     # noqa: BLE001
        return False
