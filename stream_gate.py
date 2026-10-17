#!/usr/bin/env python3
"""`stream_asr.StreamASR` 接进主流水线的那道**闸**（调用方逻辑，不含网络/线程）。

`main.py` 只在几个固定时刻调这里的方法，自己不维护任何状态：

    gate = StreamGate(stream_or_None, sr=SR)   # stream=None = 这节课没建（逐字退化成旧行为）
    gate.mark()          # `_open_segment()` 里：本地段号 +1，Soniox 那边同一刻开新段
    gate.can_feed()      # 主循环：还没退回 -> `stream.feed(chunk)`
    gate.allow_local()   # False -> `latest.put` / `finalq.put` 都别入队（本地草稿/定稿都不跑）
    gate.seg_of(end_s)   # 封口句的音频时刻 -> 本地段号（与 `seg_gen["n"]` 同一套编号）；记下已封到哪段
    gate.draft_seg(n)    # Soniox 草稿该挂的段号：本段已封过句 -> 往后挪一号（否则被判过期）
    gate.draft()         # Soniox 当前未封口那句（没活/没货 -> ""）
    gate.buf_s()         # 本段已采音频秒数（`drafts` 元组第 3 项，与本地路径同口径）
    gate.take_notice()   # 退回文案，**只给一次**（None = 没事）
    gate.stop()          # 收尾：吞异常调 `stream.stop()`（它会 flush 半句、走 `on_sealed`）

为什么单独一个模块（`codebase-design`：接口小、实现厚）：段号对齐、两道闸的开关、
「退回只报一次」这三件事都是纯状态，**可以毫秒级单测**；`main.py` 只负责在正确的时刻
调一下。`tests/test_stream_gate.py` 全部从这里进，不跑 `main.run()`。

`stream=None` 时每个方法都退化成「放行 / 空」，所以主程序里不需要到处写 `if stream`。
"""
from __future__ import annotations

#: 默认采样率（`capture.SR`；构造时可由调用方覆盖）。
SR = 16000


def start_ok(*, passcode: str, cloud_ok: bool, wanted: bool | None = None) -> bool:
    """开课时判**一次**：这节课要不要建 `StreamASR`。

    三个条件（`stream_asr.wanted()` 的语义，见 `stream_asr` 模块头 HANDBOOK §6.1）：

    1. 开关不是 `off`（`CLASSLIVE_STREAM_ASR=off` → 不建，一个字节都不变）；
    2. 有通行码（`cl-…`；现在主程序用的是 `load_api_key()` 那份，和 `final_asr` 同源）；
    3. `cloud_ok`：云端定稿这一刻允许 —— 主程序传的就是现成的 `_cloud_asr_ok()`，
       它等价于 `not final_asr.cloud_forced_off()` 且配置开关 enabled 且
       `--engine` 不是 local 且翻译档不是 `raw` 且不是文件倍速回放。**比需求清单更严**
       （多挡了 engine=local / 倍速回放）—— 那是仓库里「音频要不要出本机」的唯一判据，
       这里复用它，不另写一份判据。

    ⚠️ `wanted=None` 才去问 `stream_asr.wanted()`（这样 `stream_asr` 的 import 留在
      函数里：开关关着连模块都不加载）。
    """
    if wanted is None:
        import stream_asr

        wanted = stream_asr.wanted()
    if not wanted:
        return False
    if not (passcode or "").strip().startswith("cl-"):
        return False                     # 自带 DeepSeek key（sk-…）的用户没有 Worker 额度：不建、不弹「连不上」
    return bool(cloud_ok)


class StreamGate:
    """Soniox 那半边流水线的闸与段号账本。**只被主线程 / 定稿线程调**（内部无锁）。"""

    def __init__(self, stream=None, *, sr: int = SR) -> None:
        self._stream = stream
        self._sr = int(sr) or SR
        self._base = 0          # 第一个 mark 的流段号（对应本地第 0 段）
        self._seg_start = 0     # 当前本地段起点的累计样本数（`buf_s` 用）
        self._fin = -1          # 已封口上屏的最大本地段号（`draft_seg` 用）
        self._noticed = False
        self._freed = False
        self._stopped = False

    # ---- 开课 / 收尾

    @property
    def on(self) -> bool:
        """这节节课真的建了 `StreamASR` 吗（False = 走改前的路，一个字都不变）。"""
        return self._stream is not None

    def stop(self) -> None:
        """收尾：`stream.stop()`（flush 半句 → `on_sealed`）。可重复调，永不抛。

        ⚠️ 必须在往 `finalq` 投 `_QUIT` **之前**调：哨兵先进队列，`final_worker`
           立刻就 break，`sealedq` 里剩下的封口句没人处理。
        """
        if self._stream is None or self._stopped:
            return
        self._stopped = True
        try:
            self._stream.stop()
        except Exception:                                    # noqa: BLE001
            pass

    # ---- 段号

    def mark(self) -> int:
        """新的一段开始：在 Soniox 流里打一个点，返回流段号。没有流 -> 0。

        ⚠️ 第一次调时记下基准（本地第 0 段 = 流里的第一个段）。音频开始之前
           Soniox 还没收到任何样本，`mark_segment()` 记的位置就是 0；即使漏了这一次，
           `seg_for_audio()` 对「第一个 mark 之前」的音频返回当时的 `_seg`，减出来仍是 0。
        """
        if self._stream is None:
            return 0
        self._seg_start = self._stream.cum_samples()    # 先取，再 mark（同一线程，差值 ≤ 一块）
        seg = self._stream.mark_segment()
        if not self._base:
            self._base = seg
        return seg

    def seg_of(self, end_s: float) -> int:
        """封口句的段号：`max(音频所在的本地段, 上一句 + 1)` —— **每句一个新号**。

        ⚠️ 为什么每句都要新号（10-17 浮窗实测）：草稿中文的「换句就清」靠 `DraftView.on_draft`
           看到**更大的段号**才触发。本地一段出好几句时，同一段里第二句的草稿沿用旧号 →
           上一句的草稿中文挂到下一句底下（"So, what we're" 底下显示「而且它是相互」）。
        ⚠️ 本地段号因此会被拉高：`main._open_segment` 用 `draft_seg(k + 1)` 推进 `seg_gen`，
           退回本地之后的编号仍然只增不减（否则本地草稿全被判过期）。
        """
        if self._stream is None:
            return 0
        seg = max(0, int(self._stream.seg_for_audio(end_s) - self._base), self._fin + 1)
        self._fin = seg
        return seg

    def draft_seg(self, cur: int) -> int:
        """Soniox 草稿的段号：`max(本地当前段, 已封口段 + 1)`。

        ⚠️ `DraftView.stale_en` 把 `seg <= fin_seg` 的草稿当「这段定稿已上屏」丢掉。本地
           一段 = 一次 VAD，一段出 N 句是在段尾一起出；Soniox 却在**段中**逐句封口 ——
           封了第一句，本段后面的草稿就全被丢，老师不停顿时屏上能僵 9 s（10-17 端到端）。
           挪到下一号只影响草稿分组（`livemetrics` 的键），不影响定稿归段。
        ⚠️ `seg_of` 在定稿线程、这里在草稿线程调：只读一个 int，不加锁。
        """
        if self._stream is None:
            return cur
        return max(int(cur), self._fin + 1)

    def buf_s(self) -> float:
        """当前这一段**已经采到**的音频秒数（段起点 -> `cum_samples()`）。

        与本地路径的 `len(buf) / SR` 同口径：都是「这一份草稿看过的音频有多长」，
        现场采集靠它判缓冲滑没滑（见 `main.partial_worker` 那段说明）。
        """
        if self._stream is None:
            return 0.0
        return max(0.0, (self._stream.cum_samples() - self._seg_start) / float(self._sr))

    # ---- 两道闸

    def can_feed(self) -> bool:
        """主循环还要不要把音频喂给 Soniox（**建连中也喂**：它就靠这段音频接上话）。"""
        return (self._stream is not None and not self._stopped
                and not self._stream.reason())

    def allow_local(self) -> bool:
        """本地草稿 / 定稿要不要照跑。Soniox 在出词时 -> False（两道闸同时关）。"""
        if self._stream is None or self._freed:
            return True
        return not self._stream.alive()

    # ---- 草稿（Soniox 这半边）

    def draft(self) -> str:
        """Soniox 当前未封口那句；没建 / 没活 / 已经收尾 -> ""。"""
        if self._stream is None or self._stopped or not self._stream.alive():
            return ""
        try:
            return self._stream.draft_text() or ""
        except Exception:                                    # noqa: BLE001
            return ""

    # ---- 退回

    def take_notice(self) -> str | None:
        """退回文案：**整节课只返回一次**（第一次看到 `reason()` 非空时）。"""
        if self._stream is None or self._noticed:
            return None
        if not self._stream.reason():
            return None
        self._noticed = True
        self._freed = True          # 闸放开（`alive()` 那时也已经 False，这里是显式记账）
        try:
            return self._stream.notice_text() or None
        except Exception:                                    # noqa: BLE001
            return None
