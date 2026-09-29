#!/usr/bin/env python3
"""声纹认课 —— 注册流程 + 识别 + 自洽核对。**默认不开；由状态机驱动。**

## 它解决的问题：启动期的冷启动

第一次用这个产品时，系统不认识任何人。所以：

```
默认录课音频 → 攒样本 → 认出课 → 用户确认 → 够 2 次或录够 6 节就自动停止录音
```

⚠️ **「边用边学」有先例**：`[官方]` Apple 讲「嘿 Siri」的论文逐字
「**initializes a speaker profile using the explicit enrollment**」+
「**implicit enrollment**, in which a speaker profile is created **over a period of time**」。
Apple 还逐字写了不做数据卫生的后果：档案会被 **corrupted**。

## ⚠️⚠️ 三条**由 `sherpa-onnx` 的真实接口形状决定**的事（都回源核过）

1. ⭐⭐ **Python 侧读不回 embedding。** 官方 PR #3950 作者逐字：
   「once an embedding is enrolled there is **no way to read that vector back out**…
   **I usually end up keeping a second copy of the same floats next to the manager**」。
   本机 `sherpa_onnx 1.13.8` 与 master 的 pybind 都核过 —— **没有 getter**。
   → `profiles.json` **是唯一真源，不是缓存**。本模块整个是围着这一条设计的。
2. **`add(name, 单条)` 同名会失败**；`add(name, 列表)` 逐字「**The average of the list
   is the final embedding**」。→ 增量收样本 = `remove` 之后用**全量列表**重建。
3. ⚠️ **`search` 的空串有两种含义** —— 「没人注册」和「谁都不像」返回同一个 `""`
   （C++ `speaker-embedding-manager.cc` 逐字）。→ 必须先看 `num_speakers`。

## ⚠️ 阈值：`0.4` **零背书**

那是官方示例的默认值。调研把 116 条 threshold 相关 issue 翻过，**没有一条**在讲"该用多少"。
→ 所以本模块**不把阈值焊死**，它是参数；真实值要靠 `voice-enroll.json` 里攒的数据定。

## 数据卫生（一条硬要求）

**练习课 / tutorial 的音频绝不能进学习语料** —— 否则会把 TA 的声音注册成教授，
之后每节正课都认成 tutorial。`skipped` 的音轨**永不参与注册**。

`[未核·原文]` 这条有现成先例：Talkdesk 自述有一道 **「Second speaker check」**，
检测「two users are creating a voiceprint as if they were one user only」，
保证「a **1:1 ratio between a voiceprint and a single user**」（⚠️ 原页 403，未打开核实）。
"""
from __future__ import annotations

import dataclasses
import json
import pathlib

# ⚠️ 注意本模块里有**几个参数也叫 `store`**（`add_sample` / `rebuild` 的第一个形参）。
#    那些函数**不用**这个模块，所以在它们体内 `store` 是那个 dict —— 无害。
#    但**别**在需要这个模块的函数里把形参命名成 `store`（`save_store` 就栽过一次）。
import store

# 官方示例的默认值。⚠️ **零背书**（见模块头），真实值要靠数据定。
DEFAULT_THRESHOLD = 0.4
# 确认够几次就停止录音（作者拍的）
CONFIRM_TARGET = 2
# ⚠️ 硬上限（**我加的**）：`voice.py` 没跑通时"识别正确"无从判定，
#    没有它就会**无限录下去**（96 MB/节）。两条停止条件取先到的。
MAX_LECTURES = 6
# 一次最多留几条样本。官方 `add(列表)` 会**平均**，条数多了会把早期样本稀释掉。
MAX_SAMPLES = 5


@dataclasses.dataclass
class Identify:
    """一次识别的结果。⚠️ `course` 为空**不代表**"谁都不像" —— 见 `identify()`。"""
    course: str          # "" = 谁都不像（或没人注册 —— 用 `enrolled` 区分）
    score: float
    enrolled: bool       # 库里到底有没有人。⚠️ 没有它，空串读不出含义


# ---------------------------------------------------------------- 状态机
def load_state(path) -> dict:
    """读注册状态。

    ⚠️ **读不出来会抛**（`store.StoreError`），不再「给一份全新的」。
       2026-09-28 改：原来写的理由也是「它是可重建的计数」—— 但**重建的后果不是无害的**：
       一份新状态是 `done: False` → **下次上课又开始录你的音**。
       「可重建」说的是内容，不是**副作用**。
       （同 `_is_editing` / `load_store` 那两次：理由被搬到了不成立的地方。）
    """
    base = {"confirmed": 0, "skipped": 0, "lectures": 0, "done": False,
            "pending": None}
    try:
        obj = store.load_json(path, default={})
    except store.StoreError:
        raise
    for k in list(base):
        if k in obj:
            base[k] = obj[k]
    return base


def save_state(path, st: dict) -> None:
    """原子写 + 盖版本号（`store.save_json` 管这两件事）。"""
    store.save_json(path, st)


def should_record(st: dict) -> bool:
    """`cl` 要不要自动加 `--record-audio`。**两条停止条件取先到的。**

    ⚠️ 这是**默认开启录音**的开关 —— 所以判据必须保守：任何一项说不录就不录。
    """
    if st.get("done"):
        return False
    if int(st.get("confirmed") or 0) >= CONFIRM_TARGET:
        return False
    if int(st.get("lectures") or 0) >= MAX_LECTURES:
        return False
    return True


def mark_done(st: dict) -> dict:
    """够条件了就置 `done`（幂等）。调用方负责 `save_state`。"""
    if (int(st.get("confirmed") or 0) >= CONFIRM_TARGET
            or int(st.get("lectures") or 0) >= MAX_LECTURES):
        st["done"] = True
    return st


# ---------------------------------------------------------------- 档案（唯一真源）
def load_store(path) -> dict:
    """`{课号: [[float, …], …]}`。

    ⚠️⚠️ **「文件不存在」给空表，「文件坏了」抛异常 —— 两者绝不能混。**
       这里原来写的是「读不出就给空表（**同 `load_state` 的理由**）」，
       而 `load_state` 的理由是「它是**可重建的计数**」—— **那个理由搬不过来**：
       本函数管的是 `profiles.json`，`save_store` 自己的 docstring 逐字写着
       「**这份文件丢了就真丢了** —— 管理器里那份读不回来」。

       混起来的后果是**静默全损**：
         `load_store()` 读到坏文件 → `{}` → 调用方 `add_sample` 加一条 →
         `save_store({新课: [一条]})` → **把原文件整个覆盖掉**，其余全没了。
       ⚠️ 与 `_is_editing` 那次、以及 `classify` 那次是**同一个形状**：
          「同 X 的理由」被搬到了 X 的理由不成立的地方。
       ⚠️ 与 `minutes` 那条「**截断必须说出来**」也是同族 ——
          负结果不许读起来像穷尽。（2026-09-28 架构评估时查出。）
    """
    p = pathlib.Path(path)
    if not p.exists():
        return {}                                    # 全新安装：真的什么都没有
    try:
        obj = store.load_json(p, default={})
    except store.StoreError as e:
        # ⚠️ **不返回空表** —— 那会让随后的 save_store 覆盖掉真数据（见 docstring）。
        raise ValueError(str(e)) from e
    out = {}
    bad = []
    for k, v in obj.items():
        # ⚠️ 这里**不用**跳过 `_v` —— `store.load_json` 返回前已经剥掉了。
        if isinstance(v, list) and v and all(isinstance(x, list) for x in v):
            try:
                out[str(k)] = [[float(y) for y in vec] for vec in v]
            except (TypeError, ValueError):
                # 形状对但元素不是数（`[[1],["oops"]]`）—— 与下面那条**同一类**。
                bad.append(str(k))
        else:
            bad.append(str(k))
    # ⚠️⚠️ **形状不对的条目必须出声，不许静默丢掉**（2026-09-29 修）。
    #    原来只是 `continue` —— 于是 `{"A": [[…]], "B": "oops"}` 读回来只剩 A，
    #    而调用方随后的 `save_store` 就把 B **永久擦掉**了，**没有任何信号**。
    #    这正是本模块 docstring 反复警告的那条「负结果不许读起来像穷尽」/
    #    「静默全损」—— 与上面 `StoreError` 那条**同一层次**，所以同样**抛**。
    #    （`path.exists()` 为假那条**不算错**：全新安装是真的什么都没有，早退不抛。）
    if bad:
        raise ValueError(
            f"档案里有 {len(bad)} 条读不懂的条目（{', '.join(sorted(bad)[:5])}）—— "
            f"不覆盖它。要么手工修这份文件，要么先备份再删。")
    return out


def save_store(path, data: dict) -> None:
    """⚠️ **这份文件丢了就真丢了** —— 管理器里那份读不回来（模块头第 1 条）。
    原子写 + 盖版本号，两件事都由 `store.save_json` 管。

    ⚠️ 参数叫 `data` 不叫 `store` —— 后者会和本模块 import 的 `store` **撞名**，
       在函数体里永远拿到的是那个 dict，模块一根手指都碰不到（2026-09-28 踩到）。
    """
    store.save_json(path, data)


def add_sample(store: dict, course: str, emb: list) -> bool:
    """收一条样本进档案。返回**有没有真的变**（重复向量不算）。

    ⚠️ 上限 `MAX_SAMPLES`：官方 `add(列表)` 会**平均**，
       样本太多会把早期的稀释掉（而越早的越可能是干净的信道）。
    """
    vecs = store.setdefault(course, [])
    if any(_close(a, emb) for a in vecs):
        return False                                  # 同一个向量，别重复记
    vecs.append([float(x) for x in emb])
    if len(vecs) > MAX_SAMPLES:
        del vecs[0]
    return True


def _close(a, b, eps: float = 1e-9) -> bool:
    return len(a) == len(b) and all(abs(x - y) < eps for x, y in zip(a, b))


# ---------------------------------------------------------------- 识别
def rebuild(extractor, store: dict):
    """按档案重建一个管理器。**每次识别前都要重建** —— 因为管理器里的向量取不出来，
    而档案可能被上一个进程改过。

    ⚠️ `add(name, 列表)` 会**平均**，所以直接喂全量列表；单条时喂那条本身
       （喂一个单元素列表也行，这里取更直白的写法）。
    """
    import sherpa_onnx
    mgr = sherpa_onnx.SpeakerEmbeddingManager(extractor.dim)
    for course, vecs in (store or {}).items():
        if not vecs:
            continue
        try:
            mgr.add(course, vecs[0] if len(vecs) == 1 else vecs)
        except Exception:                             # noqa: BLE001
            continue                                  # 单条坏向量不该毁掉整次识别
    return mgr


def identify(mgr, emb, *, threshold: float = DEFAULT_THRESHOLD) -> Identify:
    """闭集辨认 + 拒识。⚠️ **空串的两种含义在这里被分开。**

    ⚠️⚠️ `sherpa-onnx` 的 `search` 对「没人注册」和「谁都不像」返回**同一个空串**
    （C++ 源码逐字）。不先看 `num_speakers` 的话，"还没注册"会被读成"这课没讲过" ——
    而这正是本仓库那条「**负结果不许读起来像穷尽**」同族错误。
    """
    enrolled = int(mgr.num_speakers) > 0
    if not enrolled:
        return Identify("", 0.0, False)
    name = mgr.search(emb, threshold=threshold)
    if not name:
        return Identify("", 0.0, True)                # 有人注册，但都不像
    try:
        s = float(mgr.score(name, emb))                # 余弦相似度（两侧都归一化过）
    except Exception:                                  # noqa: BLE001
        s = 0.0
    return Identify(str(name), s, True)


def check_label(mgr, emb, course: str, *,
                threshold: float = DEFAULT_THRESHOLD) -> tuple:
    """⭐ 用户说「这段是 X」之后**顺手核对**。返回 `(像不像, 相似度)`。

    ⭐ 这是作者那句「让用户手动填**看能不能匹配**」的落点。它把
    「**选错课**」和「**信道变了**」这两种**都会毁掉声纹**的情况**当场暴露** ——
    而不是等到课上用错术语表（那时 undo 已经晚了）。

    ⚠️ 该课**还没有任何样本**时返回 `(True, 0.0)` —— 第一份样本无从核对，
       这不是"不像"，别把首次注册判成可疑。

    ⚠️ 调研边界：**产品里没找到这个模式先例**。最接近的是 IBM 逐字
       「a unique voiceprint identifier (**claim**) must be provided **by the user**」——
       标签来自用户，但**没有"事后核对标得对不对"那一步**。这条是新东西。
    """
    try:
        if not (course in mgr):
            return True, 0.0
        s = float(mgr.score(course, emb))
    except Exception:                                  # noqa: BLE001
        return True, 0.0                               # 核对不了就别拦人
    return s >= threshold, s


# ---------------------------------------------------------------- 音频 → 向量
def embed(audio_path, *, extract_fn=None) -> list:
    """一段音频 → 声纹向量。

    ⚠️ `extract_fn` 是**验收注入点**（同 `classify.suggest_fn` / `prep.prepare` 的
       惯例）：没有它，判据要么要真音频要么要真模型。
    ⚠️ 真实现要用 16000 Hz / **单声道 float32**（官方示例逐字），且**少于 1 秒直接跳过**
       （`speaker-identification-with-vad-dynamic.py:124`）。
    """
    if extract_fn is not None:
        return list(extract_fn(audio_path))
    import sherpa_onnx
    raise NotImplementedError(
        "真提取要指定模型路径（~/.classlive/voice/ 那族）—— "
        "先用 extract_fn 注入，或等 doctor 装上模型")
