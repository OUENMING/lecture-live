#!/usr/bin/env python3
"""实时总结 —— 把一节课的**连续定稿句**变成「一段一段的主题 + 一章一章的纲要」。

    ⚠️ 它是从 `main.py` 里那**一整块内联的原子层**搬出来的（2026-09-30），
       搬的理由见 `docs/PLAN-live-summary.md`：那一块**测不了** —— 没有假时钟、
       没有假模型，只能靠真上课去碰。

## 它替掉的那块在哪里

`main.py` 原来的 `ATOM_EVERY_S` / `ATOM_PAUSE_S` / `atomq` / `atom_st` /
`_atom_chat` / `_atom_flush` / `atom_worker`，整块 `1474–1544`。窗口规则**一字未改**。

## 三个**已存在**的缺陷，这一版修掉

1. **失败 = 整窗丢**：原来的 `_atom_flush` 先把 `buf` 取走再调模型，异常就 `return`
   —— 那一窗的字再也回不来。这里改成**留在队列里 `RETRY_S` 后重试**。
2. **线程退出 = 残余窗丢**：原来的 `while running.is_set()` 一退出，`buf` 里没提交的
   句子直接没了。这里 `finish()` 会把残余窗口**作为最后一个窗口提交一次**。
3. **句柄关了会静默重开**：见 `chapter.py` 模块头第 4 条。

## 线程纪律

- `feed()` 是**主线程**唯一的入口，只做 `put_nowait` + `Event.set`，**永不抛**。
- `emit` 是唯一往主线程去的口子，由调用方包上 `stopping` 守卫。
- ⭐⭐ **动状态机的有两条线程**：`run()` 的 worker（经 `step`）和 `finish()` 的 work 线程。
  它们**靠 `_step_lock` 互斥** —— `step()` 全程持有，`finish()` 的 work 要
  `acquire(timeout=timeout_s)` **拿得到才许动** `_buf` / `_pend` / `_cur`。

⚠️⚠️ **为什么必须是锁**（2026-09-30 实测）：第一版用「等 `_stopped` 事件」来判断
worker 退没退 —— 那是**靠时序**成立的。同一份代码连跑 12 次，**有 2 次仍然
把同一个窗口投给模型两次**（双倍 API 花费），而且那次那节课的
`.chapters.jsonl` **一条记录都没有**。换成锁之后 20/20 干净。
→ 教训：**「不许两个线程同时进某个状态机」要写成结构，不能写成时序判断。**

## ⚠️⚠️ 两个域，别混：`self._clock()` vs `step(now)` 的 `now`

**所有章节侧的计时**（`_interim_at`、以及传给 `_finalize_current` 的那个时间）
**必须用 `step(now)` 传进来的 `now`**，不许用 `self._clock()`。
`self._clock()` 只在两处合法：`run()` 里给 `step` 供时间、`finish()` 里起算收尾。

⚠️ 第一版 `_absorb` 写的是 `self._interim_at = self._clock()` —— 而 `_maybe_interim`
拿它跟 `now`（模拟时间，从 0 开始）比 → **永远差一个天文数字 → 临时合成从不触发**。
判据 ⑦ 抓到的。这正是 `atom.traceable` 记过的那个形状：
**两套编号/两个时间域混用，结果不会崩，只会静默地恒不成立。**
（那个是"当日秒数当行号"，可追溯率按构造恒为 0。见 [[measure-before-claiming]]。）

## 依赖全是**注入**的（`codebase-design`：接受依赖，不要自己造）

| 参数 | 生产传什么 | 探针/测试传什么 |
|---|---|---|
| `chat` | 包一层 `build_notes._chat_json` | 固定返回值的桩 / 会失败的桩 |
| `append_atoms` | `writer.append_atoms` | 收进列表 |
| `chapter_path` | 由 `writer.session_path` 推出 | tempdir |
| `emit` | `streamq.put(("summary", p))` | 收进列表 |
| `clock` / `stamp` | `time.monotonic` / `atom.now_stamp` | **假时钟**（确定性推时间） |
"""
from __future__ import annotations

import queue
import threading
import time

import atom
import chapter as ch

#: 窗口上限。**从 `main.py` 原样搬**（连同它的注释）。
#: 计划 §17.3 的「60–90 秒」那一档。
ATOM_EVERY_S = 75.0

#: 停顿阈值。⚠️ **8 秒是错的，2026-09-28 真课上量出来的**：
#: 实测批次间隔中位 24 秒（最小 8s），折合每小时 150 次调用，
#: 而设计节奏是 60–90 秒 → 12.5 倍。根因：讲课里的自然停顿（翻页、思考、学生提问）
#: **经常超过 8 秒**，于是"停顿提前跑"这条几乎每次都先于 75 秒上限触发，把上限整个架空了。
#: → 抬到 30 秒（那才算真的中断）。⚠️ 这个数**还没有第二次实测背书**，
#: 阶段 2 的探针要量「窗口间隔中位数」。
ATOM_PAUSE_S = 30.0

#: 一章开多久之后才值得给它做第一次**临时**合成。依据：一章的量级是 10 分钟。
INTERIM_AFTER_S = 480.0

#: 临时合成之间的最小间隔。一章只花一次合成的钱，所以可以比正式版勤。
INTERIM_EVERY_S = 240.0

#: 模型调用失败后多久重试。
RETRY_S = 30.0

#: 积压上限。依据（**推算，不是测量**）：20 × 典型窗口(≈45s) ≈ 15 分钟，
#: 覆盖一次常见的断网。⚠️ 正常回放**测不出**这个数（网络一直是通的）——
#: 探针的 `--fail-window` 就是为它准备的。
MAX_PENDING = 20

#: `kind` == `课务` 的原子走模型路径。⚠️ 从 `atom.KINDS` 取，不另写字符串。
KIND_DEADLINE = "课务"

#: ⭐⭐ **模型报的 `课务` 必须先过 Jev，概率低于这个就不报。**
#:
#: ⚠️⚠️ 为什么（2026-09-30 实测）：模型报的 `课务` **假阳性 70–80%** ——
#:    「接下来讲什么」「邮箱在 Brightspace」「一张纸在传，只写学号」
#:    全被当成课务。**收紧 prompt 试了两轮（14 → 8 条），根因没解决。**
#:    → 换成**结构性闸门**：拦在模型输出后面，不靠模型自觉。
#:
#: ⭐ 这个数是**标定出来的**，不是拍的 —— ⚠️ 而且**跟 state 的框架绑死**：
#:
#: | 框架 | 对照最高（假阳性天花板） | 正样本最高 | 正样本 > 天花板 |
#: |---|---|---|---|
#: | 批量（42 行一个 state，问 `` `S{i}` ``） | **0.12–0.14** | 0.86 | 10/21 |
#: | **单条（一条一个 state，问 "this line"）** | **0.05** | 0.84 | **16/21** |
#:
#: → 实现走的是**单条**（`jev_deadline_gate`），所以取 **0.15**（天花板的 3 倍，留余量）：
#:   留 **10/21**、假阳性 **0**。
#: ⚠️⚠️ **两个框架的数不能互相搬** —— 第一版就是从批量那行搬了 0.20 过来，
#:    那会把 8/21 砍成 10/21 里的两成。这是官方 `jaggedness #5`
#:    「state 变大、准确率掉」的**实测版**。
#: ⚠️ **改 `DEADLINE_INSTRUCTION`、或改 state 的框架，都必须重新标定这个数。**
#: ⚠️ 样本很小（21 条对照、3 节课）→ **更多课之后要重新标定**。
DEADLINE_MIN_P = 0.15

#: 问 Jev 的那句话。⚠️ 照官方 `primitives.md`「问一个**一秒内**能答的判据」写的 ——
#: 用「**要不要记进日历/待办**」这个**具体动作**，比「这是不是课务」这种抽象判断硬。
#: ⚠️ **改这句话必须重新标定 `DEADLINE_MIN_P`**（见上）。
DEADLINE_INSTRUCTION = (
    "Would a student need to write this line into their calendar or their to-do list? "
    "Only deadlines, dates, exams, assignment requirements, and schedule changes count. "
    "Describing what the class is doing right now, how to contact the instructor, "
    "or lecture content does NOT count.")

#: ⭐⭐ **章节粒度的两个确定性旋钮**（2026-09-30 加的）。
#:
#: ⚠️⚠️ **粒度不许交给 prompt 控。** 实测（同一节 42 分钟的热力学课）：
#:    中性措辞 → **14 章**（7 个不到 2 分钟，太碎）；
#:    「只有真换话题才换」→ **1 章**（太粗）；
#:    再加「一节 50 分钟通常 4–8 章」这个尺度提示 → **还是 1 章**。
#:    **两个方向都过了头，而给尺度数字没有用。**
#:
#: ⭐ 同行评审的解释 —— `arxiv 2512.17083` §9.1「Boundary Selection as a Separate Step」
#:    逐字：「separating scoring from selection enables **boundary density to be
#:    controlled independently of scoring granularity**」。
#:    → **我把「粒度」（选择层）塞进了「打分/生成层」。**
#:    正确做法同 `arxiv 2601.03276` Appendix A 逐字：
#:    「Segments that are **too short are concatenated with a neighbouring segment**
#:      and segments that are **too long are recursively segmented**」。
#:    → 所以：**模型只负责说"这段在讲什么"（不管大小），切章按长度定。**
#:
#: ⚠️ **两个都要**（只加一个会掉进另一个极端）：
#:    只加 `MIN` → 模型保守时整节课只有一章（实测过）；
#:    只加 `MAX` → 模型换得勤时照样碎成十几章。
MIN_CHAPTER_S = 180.0     # 3 分钟：比这短的「换题」一律并进当前章
MAX_CHAPTER_S = 600.0     # 10 分钟：到点强制切开（与计划「一章 ≈ 10 分钟」一致）

#: ⚠️⚠️ **积压里的窗口要按「它自己的内容时刻」消化，不能一律按 `now`**（2026-09-30 修）。
#:    断网 25 分钟攒下的窗口会在**恢复那一刻被一口气消化**，它们**共享同一个 `now`**
#:    → 章节状态机里 `now - t_open ≡ 0` → `MAX_CHAPTER_S` **一次都不触发**
#:    → **一章盖了 34.4 分钟的课**（`--fail-window` 实测；判据 ㉞）。
#:    ⚠️ 那批真数据里 19 个积压窗口**主题完全没变** —— 所以「放行换题」那类修法
#:       救不了它，必须让**先后次序在时钟上体现出来**。
#:    落点在 `_process_pending`：给每个窗口算一个「本该在什么时刻被消化」的有效时钟。


def _span_s(a, b) -> float:
    """两个 `HH:MM:SS` 之间**跨越的课堂秒数**（`b - a`，跨零点取模）。解不出返回 `0.0`。

    ⚠️ 走 `obsidian_writer._hms_sec` —— 那是本仓 `HH:MM:SS` 解析的**唯一定义点**
       （它自己就是 fail-soft 的，解不出返回 `None`）。
    """
    import obsidian_writer as _ow
    sa, sb = _ow._hms_sec(a), _ow._hms_sec(b)
    if sa is None or sb is None:
        return 0.0
    return float((sb - sa) % 86400)


def jev_deadline_gate(*, token_value=None):
    """造一个「课务打分闸门」：`text -> 概率 or None`。**没配 token 返回 `None`。**

    ⚠️ 走 `keypoints.ask_one`（**Jev 的唯一接入点**），不在这里重写 HTTP / schema。
    ⚠️ 一问一次网络往返。一节 50 分钟的课大约 10 个候选 → 10 次调用、每次约 0.5 秒，
       摊在整节课上可以忽略。
    ⚠️⚠️ **token 必须取 `provider_token()`，不能取 `token()`**（2026-09-30 实测栽的）：
       `token()` 是**CommandCode 代理**那一条，而 `ask_commandcode` 的
       endpoint / model 是从 `_provider()` 解析的 —— 有官方 key 时它走**官方**。
       于是「官方端点 + 代理 token」= **401**，闸门**一条都不放行**。
       ⚠️ 而它的表象是「模型报的课务 0 条」—— **和上一版 `q1` 那个 bug 一模一样**：
          「闸门坏了」看起来像「闸门把假阳性全拦住了」。
       ⚠️ 它只在**官方 key 落盘之后**才暴露（那之前两端点一致），
          所以 A5 探针（02:52，key 落盘是 02:55）那一批数是**有效的**。
    """
    import keypoints
    tok = token_value or keypoints.provider_token()
    if not tok:
        return None                      # ⚠️ 没配 -> 闸门不存在 -> 模型路径一条不报

    def gate(text: str):
        state = ("This is a fact extracted from a university lecture.\n\n"
                 + str(text or "").strip())
        return keypoints.ask_one(state, DEADLINE_INSTRUCTION, token_value=tok)

    return gate


class LiveSummarizer:
    """喂句子进去，它往外吐 `payload`（见 `emit`）。"""

    def __init__(self, chat, append_atoms=None, chapter_path=None, emit=None,
                 clock=time.monotonic, stamp=atom.now_stamp,
                 chapters_enabled: bool = True, deadline_gate=None):
        self._chat = chat
        self._append_atoms = append_atoms
        self._emit = emit
        self._clock = clock
        self._stamp = stamp
        self._chapters_on = bool(chapters_enabled)
        #: ⭐ 模型报的 `课务` 的**打分闸门**：`text -> 概率 or None`。
        #: `None` = 没配 → **一条都不报**（见 `_deadline_ok`）。
        self._deadline_gate = deadline_gate
        self._gate_calls = 0          # Jev 的调用次数（⚠️ **与 `_calls` 分开**：那是 DeepSeek）
        self._writer = ch.ChapterWriter(chapter_path)

        # ---- 队列（`feed` 是主线程，其余全在 worker）----
        self._q: queue.Queue = queue.Queue()
        self._wake = threading.Event()
        self._stopped = threading.Event()
        #: ⭐⭐ **状态机的互斥锁** —— `step()` 全程持有它，`finish()` 的 work 线程
        #: 要**拿得到**才许动 `_buf` / `_pend` / `_cur`。
        #: ⚠️ 为什么必须是锁、不能靠 `Event` 或标志位：那是**靠时序**成立的
        #:    （2026-09-30 实测：同一份代码连跑 12 次，有 2 次仍然重复投递）。
        #:    锁让「不许两个线程同时进状态机」变成**结构**，与时序无关。
        self._step_lock = threading.Lock()
        self._closed = False
        self._calls = 0

        # ---- 窗口 ----
        self._buf: list = []          # 当前窗口的 (gid, t, en, zh)
        self._t0 = 0.0
        self._last = 0.0
        self._pend: list = []         # 待提交的窗口，**从最旧开始处理**
        self._retry_at = 0.0          # 下次允许重试的时刻（0 = 随时）

        # ---- 章节 ----
        self._prev_topic = ""         # ⭐ **上一窗**的主题（喂给下一窗的 prompt，见 `_topic_prev`）
        self._cur = None              # 当前章（dict）；`title` 为空 = 还没定题
        self._cid = 0
        self._prior: list = []        # 已经定稿的章标题（给下一章的 prompt）
        self._interim_at = 0.0        # 上次临时合成的时刻

        # ---- 杂项 ----
        self._window_n = 0
        self._atom_n = 0              # 原子的全局递增 id（**跨窗口**，不许每窗从 0 重来）

    # ------------------------------------------------------------ 对外
    @property
    def calls(self) -> int:
        """累计的**模型调用次数**。3.5 的「纯转录档不再增加」判据读它。"""
        return self._calls

    @property
    def gate_calls(self) -> int:
        """Jev（课务闸门）的调用次数。⚠️ **与 `calls` 分开** —— 两个服务、两笔钱。"""
        return self._gate_calls

    @property
    def pending(self) -> int:
        """积压深度。探针的 metrics 与判据读它。"""
        return len(self._pend)

    def feed(self, item) -> None:
        """**主线程**调。只做 `put_nowait`，**永不抛异常**。

        `item` 形状和今天 `atomq` 里的一样：`(全局句号, "HH:MM:SS", 英文, 中文)`。
        ⚠️ `finish()` / `close()` 之后调用**直接忽略** ——
           迟到的句子不该在文件名都关掉之后再往里塞。
        """
        if self._closed:
            return
        try:
            self._q.put_nowait(item)
            self._wake.set()
        except Exception:                                 # noqa: BLE001
            # ⚠️ 丢一句只是少一句，**绝不能让主线程的 `drain()` 抛**（那里没有 try）。
            pass

    def step(self, now: float) -> None:
        """执行一次循环体。**不阻塞地取件**，然后推进窗口 / 积压 / 章节。

        ⚠️ **「不阻塞」指的是取件**（非阻塞 `get_nowait`），**不是**不调模型 ——
           窗满时那一次模型调用**会**阻塞几十秒，**那正是 worker 线程存在的理由**。
           主线程的「不阻塞不变量」管的是 `drain()`，不是这里。

        探针和单测靠它**确定性地推进时间**（`run()` 内部就是循环调它）。
        """
        if self._closed:
            return
        with self._step_lock:                             # ⭐ 与 `finish()` 的 work 互斥
            self._drain_queue(now)
            self._maybe_close_window(now)
            self._process_pending(now)
            self._maybe_interim(now)

    def run(self, running) -> None:
        """worker 线程的循环。⚠️ **它只负责等唤醒，不消费队列** ——
        取件是 `step()` 的事。否则这里 `get()` 会吃掉一条而 `step` 看不到，
        而探针**只调 `step`** → 两条路走岔（判据 T19 钉的就是这条）。
        """
        try:
            while running.is_set():
                self._wake.wait(1.0)
                self._wake.clear()
                try:
                    self.step(self._clock())
                except Exception as e:                    # noqa: BLE001
                    # ⚠️ **一次 `step` 抛了不许把线程带走** —— 带走之后整节课不再出章节，
                    #    而且只在日志里留一次 traceback。同 `atom_worker` 那条纪律。
                    print(f"⚠ 实时总结 step 失败({type(e).__name__}: {str(e)[:60]}); 继续")
        finally:
            self._stopped.set()

    def finish(self, timeout_s: float = 25.0, cancel=None) -> None:
        """收尾。**必须在 `writer.close()` 之前调**（见 `docs/PLAN-live-summary.md` §8.3）。

        顺序：等 `run()` 退出 → 残余窗口**作为最后一个窗口**提交一次 →
        当前章做一次**正式合成** → 关文件。
        整体受 `timeout_s` 约束，**超时就放弃剩下的步骤**并打一行说明。
        `cancel` 已置位时不调模型，**只关文件**。
        ⚠️ **无论走哪条路，`close()` 一定要跑到** —— 否则旁路句柄泄漏。
        """

        def work():
            try:
                # ⚠️⚠️ **`cancel` 先判**（「跳过精修」/ Ctrl+C 那条路）。它**本来就不调模型**，
                #    没有任何理由先等满预算 —— 原来先 `wait` 后判，白等 25 秒才关文件。
                if cancel is not None and cancel.is_set():
                    return
                # ⚠️⚠️⚠️ **拿不到锁 = worker 正在跑状态机 → 一律不许碰共享状态。**
                #    实测（2026-09-30）它可能正卡在一次模型调用里、手里攥着
                #    `_pend[0]`；这边再去 `_flush_window` / `_process_pending`，
                #    两边会**把同一个窗口投给模型两次**（双倍 API 花费，已复现），
                #    并且先返回的那边 `pop` 到空列表 → 收尾整段被跳过
                #    → **那节课的 `.chapters.jsonl` 一条记录都没有**。
                #    → 超时的语义就是「**放弃剩下的步骤**」（计划 §6.3）：只关文件。
                #    ⚠️ 别把它换成 `_stopped.wait()` —— 那是靠时序成立的：
                #       同一份代码连跑 12 次有 2 次仍然重复投递，而加锁后 12/12 干净。
                if not self._step_lock.acquire(timeout=timeout_s):
                    print(f"⚠ 实时总结的 worker 还在跑（超过 {timeout_s:.0f} 秒拿不到锁）"
                          f"—— 收尾只关文件，不再提交残余窗口")
                    return
                try:
                    self._finish_locked(cancel)
                finally:
                    self._step_lock.release()
            except Exception as e:                        # noqa: BLE001
                print(f"⚠ 实时总结收尾失败({type(e).__name__}: {str(e)[:60]})")
            finally:
                self._stopped.set()

        th = threading.Thread(target=work, daemon=True, name="live-summary-finish")
        th.start()
        th.join(timeout_s)
        if th.is_alive():
            print(f"⚠ 实时总结收尾超过 {timeout_s:.0f} 秒 —— 剩下的步骤已放弃"
                  f"（已写下的章节记录不受影响）")
        self.close()

    def _finish_locked(self, cancel) -> None:
        """收尾里**动共享状态**的那几步。⚠️ 调用方必须**已经持有 `_step_lock`**。"""
        if cancel is not None and cancel.is_set():
            return
        now = self._clock()
        # ⚠️⚠️ **先排空 `_q`**（2026-09-30 `ocr` 抓到）。
        #    `running.clear()` 常常落在 worker 正阻塞的那次模型调用**中间** ——
        #    那次 `step()` 返回后 while 条件已假、循环直接退出，
        #    **调用期间 `feed()` 进来的句子还留在 `_q` 里**，永远没人取件：
        #    既没进 `_buf` 也没进 `_pend` → 既没提交也没报错，**静默丢**。
        self._drain_queue(now)
        self._flush_window(now)                   # 残余窗口（改进 ②）
        self._process_pending(now, force=True)
        if cancel is None or not cancel.is_set():
            self._finalize_current(now)

    def close(self) -> None:
        """**只关文件，不调模型。** 用户放弃笔记那条收尾路径用它（同 `give_up`）。"""
        self._closed = True
        self._stopped.set()
        self._writer.close()

    # ------------------------------------------------------------ 窗口
    def _drain_queue(self, now: float) -> None:
        while True:
            try:
                it = self._q.get_nowait()
            except queue.Empty:
                break
            if not isinstance(it, (tuple, list)) or len(it) < 4:
                continue
            gid, t, en, _zh = it[0], it[1], it[2], it[3]
            self._hit_deadline(gid, t, en)
            if not self._buf:
                self._t0 = now
            self._buf.append((gid, t, en, _zh))
            self._last = now

    def _maybe_close_window(self, now: float) -> None:
        """窗口规则 —— **与今天的 `atom_worker` 完全一致**（75 秒上限 / 30 秒停顿）。"""
        if not self._buf:
            return
        if (now - self._t0 >= ATOM_EVERY_S) or (now - self._last >= ATOM_PAUSE_S):
            self._flush_window(now)

    def _flush_window(self, now: float) -> None:
        """把当前窗口挪进待提交队列。⚠️ **不清空就丢** —— 失败要能原样重试。"""
        self._buf, buf = [], self._buf
        if not buf:
            return
        self._pend.append(buf)
        while len(self._pend) > MAX_PENDING:
            gone = self._pend.pop(0)
            self._emit_p({"kind": "gap", "t_from": gone[0][1], "t_to": gone[-1][1],
                          "state": "dropped"})
        self._retry_at = 0.0

    # ------------------------------------------------------------ 提交
    def _process_pending(self, now: float, force: bool = False) -> None:
        """从**最旧**的开始提交。失败的重试（`RETRY_S` 之后），不丢。

        ⭐⭐ **积压里的每个窗口按「它自己的内容时刻」消化，不是一律按 `now`**（2026-09-30 修）。

        ⚠️⚠️ 为什么：断网攒下的窗口会在**恢复那一刻被一口气消化**，它们**共享同一个
           `now`** → 章节状态机里 `now - t_open ≡ 0` → `MAX_CHAPTER_S` 一次都不触发
           → **一章盖了 34.4 分钟的课**（`--fail-window` 实测；判据 ㉞）。
        ⭐ 语义上本来就该如此：一个来自 14:16 的窗口，就该**当作 14:16 处理**。
        ⚠️ 做法是把**内容时间差**搬到 `now` 这根轴上（锚在**最新的那一窗** = `now`）——
           不是为了精确还原墙钟，而是为了让**同一批窗口的先后次序在时钟上体现出来**。
           ⭐ 单一时钟域这个不变量**保住了**（`t_open` 仍然是 `now` 域的）。
        ⚠️ **只有一个窗口时 `now_eff == now`**，行为与改动前**逐字一样** ——
           正常上课永远走那一支，这条改动只对积压生效。
        """
        if self._pending_blocked(now, force):
            return
        #: 这批里**最新的**那个窗口的内容时刻 —— 它对应 `now`（它就是「现在」）。
        #: ⚠️ 必须在循环**之前**取：循环里 `self._pend` 会一直缩短。
        newest_c = self._pend[-1][-1][1]
        while self._pend:
            buf = self._pend[0]
            obj = self._call_atoms(buf)
            if obj is None:
                # ⚠️ **留在队列里**，`RETRY_S` 后重试 —— 这就是改进 ①。
                self._retry_at = now + RETRY_S
                return
            self._pend.pop(0)
            # ⭐ 越旧的窗口，有效时刻越早（最新的那窗差值为 0，原样是 `now`）。
            now_eff = now - _span_s(buf[-1][1], newest_c)
            self._absorb_window(buf, obj, now_eff)
        self._retry_at = 0.0

    def _pending_blocked(self, now: float, force: bool) -> bool:
        if not self._pend:
            return True
        if not force and self._retry_at and now < self._retry_at:
            return True
        return False

    def _call_atoms(self, buf: list):
        """一次原子提取。**抛异常 / 返回 None 都算失败**（同今天的取舍）。"""
        sents = [it[2] for it in buf]
        try:
            self._calls += 1
            return self._chat(atom.SYS, atom.build_prompt(sents, self._topic_prev()),
                              atom.MAX_TOKENS, atom.TEMPERATURE)
        except Exception as e:                            # noqa: BLE001
            # ⚠️ 提不出来是"少几条要点"，**不是"课跑不下去"**（同 `mark_lost` 的纪律）。
            print(f"⚠ atom 提取失败({str(e)[:50]}); 课堂不受影响")
            return None

    def _topic_prev(self) -> str:
        """⭐ **上一窗的主题**（**不是**当前章的标题）。

        ⚠️⚠️ 2026-09-30 实测：返回**章标题**会让模型**一开章就沿用** ——
           因为标题被喂回去当「上一段的主题」，模型倾向于不动。
           后果是**模型认出的边界全被抑制**，章全靠 `MAX_CHAPTER_S` 到点硬切
           （三节课的章长中位全是 ~615 秒 = 正好撞天花板）。
        ⚠️ 原 `main.py` 的 `atom_st["prev"]` 存的就是**上一窗的主题**（而且**空主题不覆盖**）——
           搬过来时改成了章标题，那是**行为变化**（独立审查当时判「语义等价」，
           但那只在 `MIN/MAX_CHAPTER_S` 存在之前成立）。
        """
        return self._prev_topic

    def _absorb_window(self, buf: list, obj, now: float) -> None:
        """窗口提交成功之后：写原子 → 推 `atoms` → 写窗口记录 → 喂章节状态机。"""
        base = buf[0][0]                                  # 窗口第一句的**全局句号**
        # ⚠️⚠️ `base_id` 必须**跨窗口递增**（原 `main.py` 用 `atom_st["n"]` 记的就是这个）。
        #    写成 `base_id=0` 会让**每个窗口的 id 都从 0 重来** → `.atoms.jsonl` 里
        #    大量重复 id，而 `atom.rebase` 只重写 `src`、不动 `id`。
        #    （2026-09-30 `ocr` 抓到的静默回退。）
        got = atom.rebase(atom.parse_reply(obj, len(buf), base_id=self._atom_n,
                                           stamp=self._stamp()), base)
        self._atom_n += len(got)
        if self._append_atoms is not None:
            try:
                self._append_atoms(got)
            except Exception:                             # noqa: BLE001
                pass
        self._emit_p({"kind": "atoms", "items": [a.as_json() for a in got]})

        self._window_n += 1
        topic = atom.topic_of(obj)
        topic_zh = atom.topic_zh_of(obj)
        # ⚠️ **空主题不覆盖** —— 同原 `main.py` 的 `if topic: atom_st["prev"] = topic`。
        #    否则一窗闲聊（主题为空）会把上文清掉，下一窗模型就接不上了。
        if topic:
            self._prev_topic = topic
        rec = {"type": ch.WINDOW, "w": self._window_n, "t": buf[0][1],
               "topic": topic, "topic_zh": topic_zh,
               "lo": buf[0][0], "hi": buf[-1][0], "n_points": len(got)}
        self._write(rec)
        self._emit_p({"kind": "window", **{k: rec[k] for k in
                                           ("t", "topic", "topic_zh", "lo", "hi", "n_points")}})

        for a in got:
            if a.kind == KIND_DEADLINE and self._deadline_ok(a.text):
                self._emit_deadline(a.t, a.text, a.src, "model", False)

        self._maybe_force_split(now)                      # ⭐ 太长的章先切开
        self._advance(topic, topic_zh, buf, got, now)

    # ------------------------------------------------------------ 章节状态机
    def _maybe_force_split(self, now: float) -> None:
        """⭐ **太长的章强制切开**（`MAX_CHAPTER_S`）。与 `MIN_CHAPTER_S` 是一对。

        ⚠️ 为什么必须有它（2026-09-30 实测）：只加「最短章长」的话，
           **模型一旦保守到整节课只给一个 topic**，就再也切不开了 ——
           一节 42 分钟的课只有一章，纲要等于没有。
        ⚠️ 切出来的新章**沿用同一个 topic 起步**，但**合成是独立做的** ——
           模型只看到后半段的内容，会给出更贴的标题。
        """
        if self._cur is None or not self._cur["sents"]:
            return
        if now - self._cur["t_open"] < MAX_CHAPTER_S:
            return
        self._finalize_current(now)

    def _advance(self, topic, topic_zh, buf, atoms, now: float) -> None:
        if not topic:
            # 空主题 -> 并入当前章。**还没有章就先攒着** ——
            # 等第一章开出时它自然在里面（判据 T5）。
            self._absorb(buf, atoms, now)
            return
        # ⚠️ **去空格 + 不分大小写**（计划 §6.3）。用精确相等的话，
        #    模型把同一个主题写成 `Price elasticity` / `price elasticity` 就会被判成换题 →
        #    **凭空多出一章 + 多花一次合成调用**。（2026-09-30 `ocr` 抓到的。）
        if (self._cur is not None
                and topic.strip().lower() == (self._cur["title"] or "").strip().lower()):
            self._absorb(buf, atoms, now)
            return
        if self._cur is not None and not self._cur["title"]:
            # ⭐⭐ **当前章还没定题**（开场那几个空主题的窗口攒在这里）→
            #    让这一章**认领**这个主题，**不是**封章另起。
            #    ⚠️ 少了这一条，一节 50 分钟的课会**多出一章**：开场那 30 秒
            #       （点名/调试/闲聊，模型给空主题）自己成一章，正文另起一章。
            #       （2026-09-30 实测抓到，判据 T5。）
            self._cur["title"], self._cur["title_zh"] = topic, topic_zh
            self._absorb(buf, atoms, now)
            return
        # ⭐⭐ **太短的章不许换** —— 把这次「换题」并进当前章。
        #    ⚠️ 同 `MIN_CHAPTER_S` 的说明：这是「too short → concatenate with neighbour」
        #       那条同行评审做法的**在线版本**（离线是合并，在线是**推迟边界**）。
        if self._cur is not None and now - self._cur["t_open"] < MIN_CHAPTER_S:
            self._absorb(buf, atoms, now)
            return
        # 主题不同 -> **先给当前章做正式合成，再开新章**。A→B→A 按新章处理，不解冻旧章。
        self._finalize_current(now)
        self._absorb(buf, atoms, now, title=topic, title_zh=topic_zh)

    def _absorb(self, buf: list, atoms: list, now: float,
                title: str = None, title_zh: str = None) -> None:
        if self._cur is None:
            self._cur = {"id": self._cid, "title": "", "title_zh": "",
                         "t0": buf[0][1], "lo": buf[0][0], "hi": buf[-1][0],
                         "sents": [], "atoms": [], "ver": 0,
                         #: ⭐ 这一章**开张**的模拟时刻 —— `MIN/MAX_CHAPTER_S` 算的就是它。
                         #: ⚠️ 用 `now`（**模拟时钟**），不是 `self._clock()`：
                         #:    两个域混用会让判据**恒不成立**（2026-09-30 栽过一次，判据 ⑦）。
                         "t_open": now}
            self._cid += 1
            self._interim_at = now
        c = self._cur
        if title:                                         # 定题（开场那些空主题的窗口
            c["title"], c["title_zh"] = title, title_zh    # 早就攒在里面了）
        for gid, t, en, _zh in buf:
            c["sents"].append((gid, en))
            c["hi"] = max(c["hi"], gid)
            c["t1"] = t
        c["atoms"].extend(atoms)

    def _maybe_interim(self, now: float) -> None:
        """临时合成：章开够久了、且距上次临时超过间隔。"""
        if not self._chapters_on or self._closed or self._cur is None:
            return
        if not self._cur["sents"]:
            return
        if now - self._interim_at < INTERIM_AFTER_S:
            return
        self._interim_at = now
        self._synthesize("interim")

    def _finalize_current(self, now: float) -> None:
        """给当前章做**正式**合成并封章。没有内容就什么都不做。"""
        if self._cur is None:
            return
        c = self._cur
        if not c["sents"]:
            self._cur = None
            return
        if self._chapters_on and not self._closed:
            self._synthesize("final")
        if c["title"]:
            self._prior.append(c["title"])
        self._cur = None

    def _synthesize(self, status: str) -> None:
        """一次章节合成。**失败重试一次**；仍失败就写一条空的（界面退回原子要点）。"""
        c = self._cur
        if c is None:
            return
        c["ver"] += 1
        obj = None
        for attempt in (1, 2):
            try:
                self._calls += 1
                obj = self._chat(ch.SYS_CHAPTER,
                                 ch.build_prompt(c["sents"], c["atoms"], self._prior),
                                 ch.MAX_TOKENS, ch.TEMPERATURE)
            except Exception as e:                        # noqa: BLE001
                print(f"⚠ 章节合成失败({str(e)[:50]}); 第 {attempt} 次")
                obj = None
            if obj is not None:
                break
        title, title_zh = ch.title_of(obj or {})
        sents = ch.parse_reply(obj, c["lo"], c["hi"]) if obj is not None else []
        chp = ch.Chapter(id=c["id"], status=status,
                         title=title or c["title"], title_zh=title_zh or c["title_zh"],
                         t0=c["t0"], t1=c.get("t1", c["t0"]),
                         lo=c["lo"], hi=c["hi"], sentences=sents, version=c["ver"])
        self._write(chp)
        self._emit_p({"kind": "chapter", "chapter": chp.as_json()})

    # ------------------------------------------------------------ 课务
    def _deadline_ok(self, text: str) -> bool:
        """模型报的这条 `课务`，要不要留？—— **过 Jev 打分闸门**。

        ⚠️⚠️ **没配闸门 / 打分失败 → 一律 `False`（不报）**。
           这不是保守，是**有依据的**：模型路径在没有闸门时精度只有 ~25%，
           比「不报」更糟 —— 假阳性会让读者**以后整节都不看**（真 deadline 也一起错过）。
           正则路径**不受影响**（它本来就不调模型、也不进这里）。
        """
        if self._deadline_gate is None:
            return False
        try:
            self._gate_calls += 1
            p = self._deadline_gate(text)
        except Exception:                                 # noqa: BLE001
            return False
        return p is not None and p >= DEADLINE_MIN_P

    def _hit_deadline(self, gid: int, t: str, en) -> None:
        """**正则路径**：句子一定稿就查。不调模型 → 一两秒内上屏（判据 T14）。"""
        hit = ch.deadline_hits(en)
        if hit is None:
            return
        self._emit_deadline(t, str(en or ""), [gid], "regex", hit["changed"])

    def _emit_deadline(self, t: str, quote: str, src, source: str, changed: bool) -> None:
        rec = {"type": ch.DEADLINE, "t": t, "quote": quote,
               "src": sorted(set(src or ())), "source": source, "changed": bool(changed)}
        self._write(rec)
        self._emit_p({"kind": "deadline", **{k: rec[k] for k in
                                             ("t", "quote", "src", "source", "changed")}})

    # ------------------------------------------------------------ 杂项
    def _write(self, rec) -> None:
        self._writer.append([rec])

    def _emit_p(self, payload: dict) -> None:
        """⚠️ 推给界面。**调用方负责 `stopping` 守卫** —— 这里只做「有没有人接」。"""
        if self._emit is None:
            return
        try:
            self._emit(payload)
        except Exception:                                 # noqa: BLE001
            pass
