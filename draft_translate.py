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
import re
from concurrent.futures import ThreadPoolExecutor

_SEALED = re.compile(r"[.?!](?=\s+\S)")
#: 停机哨兵（3.9.7 省电）：`stop()` 把它放进队列，`step()` 看到就收摊 ——
#: 让 worker 从「每 0.3 s 空醒一次」变成**阻塞等**。
_STOP = object()


def sealed_prefix(text: str) -> str:
    """英文草稿里**已封口**的那一截：最后一个句末标点（`.?!`）、且它**后面已经有新词**。

    ⭐ 为什么能锁（2026-10-12 SOC10020 真课实测，Nemotron 流式草稿）：封口前缀在之后的
       草稿里被保留 **1302/1302**；而它的中文在下一版译文里被改写 **59/99**（60%）——
       英文早就定了，闪的只是"整段重译"带出来的中文措辞。
    ⚠️ 末尾那个标点**后面还没有词**时不算封口（模型可能马上把 `.` 改成 `,`）。
    """
    m = None
    for m in _SEALED.finditer(text or ""):
        pass
    return text[:m.end()] if m else ""


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

    def stale_en(self, seg: int) -> bool:
        """这份**英文**草稿过期了吗：屏上已是更新一段的草稿，或这段的定稿已经上屏。

        ⚠️ 3.9.4 起才需要：段尾收尾那份草稿（`draft_engine.finish`）比逐秒草稿晚出一拍，
           万一下一段的草稿 / 这段的定稿先到了，它不许把屏上的东西盖回旧的。
        """
        return seg < self.cur_seg or seg <= self.fin_seg

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
    paused()                         窗口不可见 -> 不发请求(3.9.7 省电); 默认恒 False
    stats                            与 DraftView.stats 同一个 dict
    """

    def __init__(self, *, translate, output, mode_ok, should_yield, context, stats,
                 paused=None) -> None:
        self._translate, self._output = translate, output
        self._mode_ok, self._should_yield, self._context = mode_ok, should_yield, context
        self._paused = paused or (lambda: False)
        self.stats = stats
        self._q: queue.Queue = queue.Queue(maxsize=1)
        self._lock = {"seg": None, "en": "", "zh": "", "last": ""}   # 本段已封口的英文 / 锁住的中文 / 最后一句
        self._pool = ThreadPoolExecutor(max_workers=1)    # 新封口那句与尾巴并发时，封口句走这条
        self._stopped = False

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
        """最多处理一条; 取到了(不论是否真的翻了)返回 True。绝不抛。

        ⚠️ `timeout=None` = 一直等（3.9.7 省电：`run()` 用这个，停机靠 `stop()` 的哨兵）。
        """
        try:
            item = self._q.get(timeout=timeout)
        except queue.Empty:
            return False
        if item is _STOP:                                  # 停机哨兵
            self._stopped = True
            return True
        try:
            seq, seg, text = item
        except Exception:                                  # noqa: BLE001
            return True
        if not text or not self._mode_ok():
            return True
        if self._paused():
            # ⭐ 3.9.7 省电：窗口不可见 -> 草稿不用看，**一个请求都不发**。
            #    ⚠️ 丢的是这一条（`submit` 只留最新），恢复后从下一段重开；
            #       段号锁 `self._lock` 会因 `text` 接不上而自己作废（见 `_step_locked`）。
            return True
        if self._should_yield():
            self.stats["yield_local"] += 1
            return True
        try:
            self._step_locked(seq, seg, text)
        except Exception:                                  # noqa: BLE001
            self.stats["errors"] += 1
        return True

    def _step_locked(self, seq: int, seg: int, text: str) -> None:
        """封口句的中文**译一次就锁住**，之后只译没说完的那截，上屏拼成 `锁住的 + 尾巴`。

        新封口了一句 → 那一句与尾巴**并发**各译一次（尾巴不排在它后面）。
        ⚠️ 草稿不再以锁住的英文开头（Parakeet 滑窗会丢掉段首）→ 锁作废，退回整段重译（旧行为）。
        ⚠️ 新封口那句没译出来 → **不锁**，这一轮按整段译（宁可闪，不许漏）。
        ⚠️ 前文：封口句用"上一句已锁的英文"（段首那句用定稿前文），尾巴用"刚封口的那句" ——
           `translate_draft` 的前文槽就是「上一句」（`cloud_translator._draft_user`）。
        """
        lk = self._lock
        if lk["seg"] != seg or not text.startswith(lk["en"]):
            lk.update(seg=seg, en="", zh="", last="")
        sealed = sealed_prefix(text)
        if len(sealed) > len(lk["en"]):
            new_en = sealed[len(lk["en"]):].strip()
            tail = text[len(sealed):].strip()
            f_new = self._pool.submit(self._once, new_en, [lk["last"]] if lk["last"] else self._context())
            zh_tail = self._once(tail, [new_en]) if tail else ""
            zh_new = f_new.result()
            if zh_new:
                lk.update(en=sealed, zh=lk["zh"] + zh_new, last=new_en)
                self._output(seq, seg, lk["zh"] + zh_tail)
                return
            lk.update(en="", zh="", last="")
        if not lk["en"]:
            self._translate(text, lambda d: self._output(seq, seg, d), self._context())
            return
        rest = text[len(lk["en"]):].strip()
        if not rest:
            self._output(seq, seg, lk["zh"])
            return
        zh = lk["zh"]
        self._translate(rest, lambda d: self._output(seq, seg, zh + d), [lk["last"]])

    def _once(self, en: str, context) -> str:
        """译一条、只要最终那份中文（`translate` 的约定是走回调；云端只回调一次，本地也是）。

        ⚠️ **自己接住异常、返回空**：封口句和尾巴是并发的两次，一次抛了不许把另一次已经译好的连坐丢掉
           （云端抖一下 → `EngineRouter` 降级到本地 → 朋友机器没装本地模型 → `_ensure` 抛 RuntimeError）。
           封口句空 = 这一轮不锁（下面整段重译那条路照旧会抛、由 `step` 计数）；尾巴空 = 只上锁住的那截。
        """
        got: list = []
        try:
            self._translate(en, lambda d: got.__setitem__(slice(None), [d]), context)
        except Exception:                                  # noqa: BLE001
            self.stats["errors"] += 1
            return ""
        return got[0] if got else ""

    def run(self, running) -> None:
        # ⚠️ `step(None)` **阻塞等**（3.9.7 省电：原来 `step(0.3)` 是 3.3 次/秒空醒）。
        #    收尾由 `stop()` 的哨兵叫醒（`main` 在 `running.clear()` 之后投）。
        while running.is_set() and not self._stopped:
            self.step(None)

    def stop(self) -> None:
        """停机：让阻塞在 `step(None)` 上的线程立刻收摊（3.9.7）。**永不抛。**"""
        self._stopped = True
        try:
            self._q.get_nowait()          # 腾一格（待译的那条已经不要了）
        except Exception:                 # noqa: BLE001
            pass
        try:
            self._q.put_nowait(_STOP)
        except Exception:                 # noqa: BLE001
            pass
