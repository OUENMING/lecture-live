"""草稿译文(英文草稿 -> 即时中文)的两半: 主线程的"过期判据"与后台线程的"只译最新"。

R1 方案 C/5b (docs/PLAN-r1-translation-gap.md §C)。原来散在 `main.run()` 的闭包里,
无法脱离整条流水线测试; 这里收成两个依赖全注入的类:

  DraftView        纯状态机(无线程无 I/O)。只在主线程 `drain()` 里用:
                   谁该清中文草稿槽、哪条译文已过期。
  DraftTranslator  只留最新的队列 + 工作线程循环。翻译/输出/让路判据都是注入的函数。

⚠️ 过期判据用段号 `fin_seg`(已定稿上屏的最大段号), 不是 `final_gen`:
   后者在上一段定稿时就自增, 那时下一段的草稿已在翻 -> 会把有效译文当过期丢。
"""
from __future__ import annotations

import queue


class DraftView:
    def __init__(self) -> None:
        self.fin_seg = -1          # 已定稿上屏的最大段号
        self.seq = -1              # 已显示的最大送翻序号
        self.cur_seg = -1          # 屏上英文草稿所属段号
        self.stream_seg = None     # 正在流式吐字的那句定稿所属段号(没有在流 = None)
        # 与 DraftTranslator 共享同一个 dict(收尾打印)
        self.stats = {"stale": 0, "yield_local": 0, "errors": 0}

    def on_draft(self, seg: int) -> bool:
        """新一段的第一份英文草稿 -> True: 调用方应清掉上一段残留的中文草稿。"""
        if seg > self.cur_seg:
            self.cur_seg = seg
            return True
        return False

    def on_final(self, seg) -> bool:
        """先更新 fin_seg 再判: 屏上已是更新一段的草稿时(cur_seg > fin_seg), 旧段定稿不碰草稿槽。
        返回值即 `finalize(clear_draft=)`。seg 缺失/非法时不动 fin_seg, 绝不抛。"""
        self.stream_seg = None
        try:
            self.fin_seg = max(self.fin_seg, int(seg))
        except Exception:                                  # noqa: BLE001
            pass
        return self.cur_seg <= self.fin_seg

    def on_stream(self, seg) -> None:
        """某段的定稿开始/继续流式吐字。seg 缺失/非法 → 当作不知道(None), 绝不抛。"""
        try:
            self.stream_seg = int(seg)
        except Exception:                                  # noqa: BLE001
            self.stream_seg = None

    def over_stream(self) -> bool:
        """定稿流式期间, 屏上草稿是不是**更新一段**的 -> 该照常显示(不是正在定稿那句的重复)。

        悬浮窗流式时会藏起草稿栏(防「同一句在实时行和草稿栏各出现一次」)。但真课里
        81% 的段在上一段定稿上屏时, 下一段草稿已经出来了(10-09 那节, p50 已等 4 s) ——
        一律藏就是每句断档一次。段号不知道时按老行为(藏)。"""
        return self.stream_seg is not None and self.cur_seg > self.stream_seg

    def accept(self, seq: int, seg: int) -> bool:
        """译文能否上屏。False = 过期(这段已定稿 / 屏上已是更新一段的英文草稿 / 更新的译文已显示过), 已计数。"""
        if seg <= self.fin_seg or seg < self.cur_seg or seq < self.seq:
            self.stats["stale"] += 1
            return False
        self.seq = seq
        return True


class DraftTranslator:
    """只留最新一条待译草稿(maxsize=1, 新的顶掉旧的), 后台线程逐条译。

    translate(text, on_zh, context)  译一条; 流式回调 on_zh(部分中文)
    output(seq, seg, zh)             译文送出去(主线程那头的队列)
    mode_ok()                        翻译模式是否开着
    should_yield()                   本地引擎且定稿在排队/在跑 -> 让开(不和定稿抢 MLX 锁)
    context()                        前文(翻译时现取)
    stats                            与 DraftView.stats 同一个 dict
    """

    def __init__(self, *, translate, output, mode_ok, should_yield, context, stats) -> None:
        self._translate, self._output = translate, output
        self._mode_ok, self._should_yield, self._context = mode_ok, should_yield, context
        self.stats = stats
        self._q: queue.Queue = queue.Queue(maxsize=1)

    def submit(self, seq: int, seg: int, text: str) -> None:
        item = (seq, seg, text)
        try:
            self._q.put_nowait(item)
        except queue.Full:                                 # 只留最新
            # ⚠️⚠️ **「清旧」和「入新」必须各自容错，不能合一个 try**（2026-10-12 全仓审查，
            #    核实过是真的、而且是活的）：`put_nowait` 抛 `Full` 之后，消费者线程
            #    （`step()` 那个 `get(timeout=0.3)`）**完全可能刚好把旧项取走**，
            #    于是 `get_nowait()` 抛 `Empty` → 被那个 `except` 一起吞掉 →
            #    **新项根本没入队** —— 而队列此刻其实是空的、完全放得下。
            #    结果与「只留最新」的意图**正好相反**：丢的偏偏是最新那条。
            try:
                self._q.get_nowait()
            except queue.Empty:
                pass                                       # 消费者刚取走 = 队列已空，放得下
            try:
                self._q.put_nowait(item)
            except queue.Full:
                pass                                       # 单生产者，理论到不了这儿

    def step(self, timeout: float = 0.3) -> bool:
        """最多处理一条; 取到了(不论是否真的翻了)返回 True。绝不抛。"""
        try:
            item = self._q.get(timeout=timeout)
        except queue.Empty:
            return False
        try:
            seq, seg, text = item
        except Exception:                                  # noqa: BLE001
            return True
        if not text or not self._mode_ok():
            return True
        if self._should_yield():
            self.stats["yield_local"] += 1
            return True
        try:
            self._translate(text, lambda d: self._output(seq, seg, d), self._context())
        except Exception:                                  # noqa: BLE001
            self.stats["errors"] += 1
        return True

    def run(self, running) -> None:
        while running.is_set():
            self.step(0.3)
