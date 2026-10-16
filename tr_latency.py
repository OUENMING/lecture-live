"""⭐ 每条定稿的**译文延迟**（3.9.7 第 3 项）：中文第一个字等了多久、这一段等了多久。

`main._emit()` 在**把英文送进翻译之前**建一个 `TrTimer`，用它包一层 `on_zh`，
翻译返回（或抛）之后用 `done()` 取四个值 —— `*_ms` 全是**毫秒**：

| 字段 | 从哪算到哪 |
|---|---|
| `zh_first_ms` | 送进翻译那一刻 → **第一个**中文增量回调 |
| `zh_done_ms`  | 送进翻译那一刻 → 翻译返回（失败那条路 = 到抛那一刻） |
| `seg_to_zh_ms` | 那**一段音频结束**（VAD 切段那一刻）→ 第一个中文增量 |
| `tr_model`    | 实际出译文的引擎（云端模型名 / `local`）；**没出中文 = `""`** |

⭐ `seg_to_zh_ms` 才是**用户真正等的时间** —— 定稿 ASR + 断句 + 排队 + 翻译全在里面；
`zh_first_ms` 只量翻译那一段（`main` 那边把「该段结束时刻」在 `_open_segment()` 里记下，
见 `seg_ended_at`）。

⚠️ **只记数字与引擎名，一个字都不记**（与 `testmode.note_correction` 同一条规矩）。
⚠️ 全模块**绝不抛**：它跑在定稿线程上，而 `main._emit` 的 `try` 只包住翻译调用 ——
   计时自己出问题只该少一行数，不该少一句字幕。
⚠️ `clock` 可注入（生产是 `time.monotonic`，判据用它对齐时间线，不许白等）。
"""
from __future__ import annotations

import time


def _ms(a: float, b: float) -> float:
    """`a` → `b` 的毫秒数，**负数夹到 0**。

    ⚠️ 夹零是刻意的：注入的钟 / 系统休眠后的 `monotonic` 都可能让 `b < a`，而
       「等了 -3 毫秒」写进报告会被读成"数据坏了"。同 `obsidian_writer.monotone_secs`
       那条「把序列拉直，不是在猜」。
    """
    return round(max(0.0, b - a) * 1000.0, 1)


class TrTimer:
    """一句定稿的译文计时器。**构造即开始计时**（调用点紧邻那次翻译调用）。

    接口刻意小：`tap(on_zh)` 包回调，`done(seg_t0=…, model=…)` 取四元组。
    """

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._t0: float | None = None
        self._first: float | None = None      #: 第一个中文增量的时刻
        self._bad = False                     #: 钟坏了 -> 整条数据作废（交空值）
        try:
            self._t0 = float(clock())
        except Exception:                     # noqa: BLE001  见文件头「绝不抛」
            self._bad = True

    def tap(self, on_zh):
        """包一层 `on_zh`：**只**给第一个增量落时刻，其余原样透传。

        ⚠️ 连取时刻自己都要吞异常：`tap` 是在**翻译引擎的流式循环里**被调的，
           它一抛就会顺着 `main._emit` 的 `except` 变成「只有 ASR」—— 那是真丢内容，
           比少一行延迟数据贵得多。
        """
        def _wrapped(delta):
            if self._first is None and not self._bad:
                try:
                    self._first = float(self._clock())
                except Exception:             # noqa: BLE001
                    self._bad = True
            on_zh(delta)
        return _wrapped

    def done(self, *, seg_t0: float | None = None, model: str = "") -> tuple:
        """→ `(zh_first_ms, zh_done_ms, seg_to_zh_ms, tr_model)`。

        ⭐ `tr_model` **只在真的出过中文增量时**才填：没出中文 = 这一句没有译文
        （异常 / 空译文 / 模型把英文吐回来被守卫挡掉）。那时报一个引擎名会让
        `report.json` 的 `by_model` 计数把"翻失败"说成"翻出来了"。
        ⚠️ `seg_t0` 拿不到（那段音频还没结束 / 调用方没记）→ `seg_to_zh_ms` 为空，
           前两个照记（唯一没得报的就是它，原因就写在这）。
        """
        try:
            t1: float | None = float(self._clock())
        except Exception:                     # noqa: BLE001
            t1 = None
        if self._bad or self._t0 is None or t1 is None:
            return (None, None, None, "")
        first = self._first
        return (_ms(self._t0, first) if first is not None else None,
                _ms(self._t0, t1),
                _ms(seg_t0, first) if (first is not None and seg_t0 is not None) else None,
                model if first is not None else "")
