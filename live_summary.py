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

#: ⭐ **首段**的临时合成门槛：**0 秒 = 开章后不再额外等**，开章那一窗吸收完就出第一段。
#:
#: ❓ 为什么是 0（2026-11-17 真课 ECON10740 实测 + test key 重放验收）：
#:    旧值 180 / 480 时，一组开讲 3.5–4 分钟才出第一段、之后同一组 7 分钟不更新。
#:    改成 90 后重放量出**真正的延迟是 165–185 s，不是 90** —— 因为**章是在第一个窗口
#:    被吸收时**才开张（`ATOM_EVERY_S`=75 / `ATOM_PAUSE_S`=30，一窗 30–75 秒才闭合），
#:    `_interim_at` 从那一刻起算，再等 90 → 实际 **75 + 90**。
#:    → 首段不该再叠一层等待：**窗口一吸收就合成**（开章那一窗已经有 13–20 句）。
#:    ⚠️ 会不会太短？`chapter.build_prompt` 本来就有「本章不到 30 句只写 1–2 句」那条规则，
#:    1–2 句的段落正是开局该有的样子；下一窗起按 `REFRESH_GAP_S` 继续长。
#: ⚠️ 它仍是**墙钟秒数**，用 `step(now)` 的 `now` 算（见文件头「两个域别混」）。
#: ⚠️ 调大它就退回旧行为（开章后等 N 秒），不是死常数。
FIRST_INTERIM_AFTER_S = 0.0

#: ⭐⭐ **之后每次刷新的两道闸**（作者原话：「可以多调用几次都没关系」）。
#:
#: 触发点在 `_absorb_window` 之后（每个窗口被吸收进当前章就检查一次，`step` 里另有一道
#: 兜底检查）—— 所以「有新内容」是**结构上保证**的，不再靠「章开够久了」。
#:   · `REFRESH_GAP_S`：距本章上次合成（临时或正式）至少 60 秒；
#:   · `REFRESH_MIN_NEW`：本章**新增**原文句至少 3 句。
#: 两条都满足才花一次调用。60 秒 ≈ Chief / LecSync 的重写节奏，也刚好比一个窗口
#: （`ATOM_EVERY_S`=75 / `ATOM_PAUSE_S`=30）略短，不会每窗都触发、也不会几天一刷。
REFRESH_GAP_S = 60.0
REFRESH_MIN_NEW = 3

#: ⭐⭐ **段落刷新里顺带判出的换题点要拆章，旧章至少留这么多句才拆**。
#:
#: ⚠️ 为什么不是「主题变了就拆」：重放里 2–3 句就拆出来的「章」没有纲要价值
#:    （跟 `MIN_CHAPTER_S` 那条「太短就并进邻居」同一个意思，只是这里按**句数**而不是
#:    按秒 —— 拆章时手里只有句子，秒数在 `c["times"]` 里、能用但不该当主锁）。
#: ⚠️ 15 句 ≈ 3–4 分钟的正常语速发言（一窗 13–20 句），也是离线量具里
#:    「一章至少有一句话讲得清」的下限。
SHIFT_MIN_KEEP = 15
#: ⭐ 同时要求旧章**至少讲了这么多秒**（按 `c["times"]`，拆点那句 − 旧章首句）。
#: ⚠️ 2026-11-18 重放（145925 后半 33 分钟）：只有句数闸时一节拆出 11 章、中位 3 分钟 ——
#:    模型把同一组里「下一个人讲下一部分」当成换题。小组展示每组 12–18 分钟、讲课一段
#:    5–15 分钟，4 分钟以下拆出来的章既不是「一组一段」也没有纲要价值。
SHIFT_MIN_S = 240.0

#: 模型调用失败后多久重试。
RETRY_S = 30.0

#: 积压上限。依据（**推算，不是测量**）：20 × 典型窗口(≈45s) ≈ 15 分钟，
#: 覆盖一次常见的断网。⚠️ 正常回放**测不出**这个数（网络一直是通的）——
#: 探针的 `--fail-window` 就是为它准备的。
MAX_PENDING = 20

#: 同一窗口**连续失败到几次**就启动「毒窗判别」：先试**下一窗**（2026-10-01 审查 F3）。
#: ⚠️ 光有次数上限会把「断网积压」误杀 —— 断网时**每一窗**都会到上限。所以到了
#:    上限**不直接丢**：下一窗也失败 = 断网形状（不丢、继续攒，`MAX_PENDING` 照旧管）；
#:    下一窗成功 = 队头这窗的内容模型消化不了，丢它、发 `gap(state="failed")`、放行。
#: 3 次 × `RETRY_S`(30) ≈ 90 秒出判定，比原来「堵到攒满 20 窗（≈15 分钟）」快一个量级。
MAX_WINDOW_ATTEMPTS = 3

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
#: ⭐ 15 分钟（原 1200 = 20 分钟；更早是 600 = 10 分钟）。**三次调参的实测账**：
#:    · 600：一组 **12–18 分钟**的轮流讲 PPT，正好在组讲到一半被切成两段（右栏凭空
#:      多出一个前半组的半截章）。
#:    · 1200：**有副作用、实测抓到了** —— 窗口主题模型偶尔「粘住」不换题（test key
#:      重放里一次把**赌博组 + 最低工资组并成一章 20 分钟**），而段落每分钟重写：
#:      **前一组的总结被后一组覆盖，丢了**。600 那道切章至少是个保险。
#:    · 900 是折中：一组 15 分钟恰是一段；偶尔在 16–18 分钟那组的**末尾**被切一刀，
#:      代价可接受 —— 段落跟着窗口刷新，切开后的新章**第一窗就出段落**，不再久等。
MAX_CHAPTER_S = 900.0

#: ⚠️⚠️ **积压里的窗口要按「它自己的内容时刻」消化，不能一律按 `now`**（2026-09-30 修）。
#:    断网 25 分钟攒下的窗口会在**恢复那一刻被一口气消化**，它们**共享同一个 `now`**
#:    → 章节状态机里 `now - t_open ≡ 0` → `MAX_CHAPTER_S` **一次都不触发**
#:    → **一章盖了 34.4 分钟的课**（`--fail-window` 实测；判据 ㉞）。
#:    ⚠️ 那批真数据里 19 个积压窗口**主题完全没变** —— 所以「放行换题」那类修法
#:       救不了它，必须让**先后次序在时钟上体现出来**。
#:    落点在 `_process_pending`：给每个窗口算一个「本该在什么时刻被消化」的有效时钟。


#: ⭐ 3.11.1：**账户级**失败 —— 这一节再试也不会好（座席没余额 / key 错 / 被禁）。
#:    ⚠️ 429 不在里面（限流是暂时的）；5xx 也不在（可能是一次抖动）。
DEAD_STATUS = (401, 402, 403)


def _http_code(e) -> int | None:
    """异常里能不能读出 HTTP 状态码。读不出 → `None`（**不许猜**）。

    兼容三种形状：`httpx.HTTPStatusError`（`.response.status_code`）、
    自带 `.status_code` 的异常、以及 `urllib` 那种 `.code`。
    """
    for obj, attr in ((getattr(e, "response", None), "status_code"),
                      (e, "status_code"), (e, "code")):
        try:
            v = getattr(obj, attr, None) if obj is not None else None
        except Exception:                                  # noqa: BLE001
            continue
        if v is None:
            continue
        try:
            return int(v)
        except (TypeError, ValueError):
            continue
    return None


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
        #: ⭐ 3.11.1：**账户级失败熔断**。401/402/403 是确定性失败（key 错 / 余额没了 /
        #:    被禁），不是抖动 —— 每 30 s 重试、一节课几百次全是白花钱，报告里只剩
        #:    「AI 调用失败 N 次」。第一次撞上就本节停用并**出声一次**。
        #:    ⚠️ 429 不在此列：那是限流，等一会儿就会好（11-05 的 402 则不会）。
        self._model_down = ""
        self._chat = self._guarded(chat)
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
        #: ⭐ 只读计数（测试模式的采集面 ⑤）—— **不参与任何判定**。
        #: ⚠️ 字段名与语义**对齐离线探针的 `metrics.json`**（`docs/experiments/
        #:    probe_live_summary.py`），这样在线/离线两边能直接对拍。
        #: ⚠️ 名对不上的那几项（`input_tokens_est` / `elapsed_s` / `board_sentence_ratio`
        #:    之类）**刻意不硬凑** —— 凑出来口径不同反而是误导。
        self._atom_calls = 0          # 里面 `_calls` 是**总**调用数（原子+章节）
        self._chapter_calls = 0
        self._failed = 0
        #: ⭐ 3.11：`failed_calls` 是原子+章节的合计，报告里看不出是哪一层。分开记。
        self._failed_atoms = 0
        self._failed_chapters = 0
        self._empty_windows = 0
        self._chapters_final = 0      # ⚠️ 名不副实：这是**尝试合成**的章数，不是写成的
        #: ⭐ 3.11：真正写进 `.chapters.jsonl` 的**正式章**数（`ChapterWriter.append`
        #:    返回非 0 才算）—— 11-03 `chapters=5` 而笔记只落 4 章，就是差这一格。
        self._chapters_written = 0
        #: ⭐ 3.11：其中**有内容**的（`sents` 非空）—— 合成全失败时仍会写一条空壳记录，
        #:    只看 `chapters_written == attempted` 会误以为 5 章都落盘了。
        self._chapters_nonempty = 0
        #: ⭐ 3.11：收尾被截断（拿不到锁 / 超时 / 异常）。`finish()` 之后才有效。
        self._finish_truncated = False
        self._chapters_interim = 0
        #: ⭐ 3.12：段落刷新里判出换题点、**就地拆章**的次数（
        #: 窗口主题那条路换的章不算）。采集面用。
        self._shift_splits = 0
        self._shift_now = 0.0
        self._forced_next = False      # 下一章是不是 `MAX_CHAPTER_S` 硬切出来的          # `_maybe_interim` 记下的 step 域时刻（拆章用）
        self._deadlines_regex = 0
        self._deadlines_model = 0
        self._retries = 0
        self._gap_dropped = 0
        self._backlog_peak = 0

        # ---- 窗口 ----
        self._buf: list = []          # 当前窗口的 (gid, t, en, zh)
        self._t0 = 0.0
        self._last = 0.0
        #: 待提交的窗口，**从最旧开始处理**；元素 = `[失败次数, 窗口]` 成对存放 ——
        #: `_process_pending` 要按窗记重试次数（2026-10-01 审查 F3），裸 list 挂不住。
        self._pend: list = []
        self._retry_at = 0.0          # 下次允许重试的时刻（0 = 随时）

        # ---- 章节 ----
        self._prev_topic = ""         # ⭐ **上一窗**的主题（喂给下一窗的 prompt，见 `_topic_prev`）
        self._cur = None              # 当前章（dict）；`title` 为空 = 还没定题
        self._cid = 0
        self._prior: list = []        # 已经定稿的章标题（给下一章的 prompt）
        #: ⭐ 与 `_prior` **按下标对齐**的各章结论句（`para.lead.zh`；没有就是空串）。
        #: 光给标题拦不住「换个说法把上一章的结论再写一遍」（`chapter.build_prompt`）。
        self._prior_leads: list = []
        self._interim_at = 0.0        # 上次合成的时刻（临时或正式）
        self._interim_n = -1          # 上次合成时这一章有几句（没新句就别再合）
        #: ⭐ 当前章还没做过临时合成吗（首段用更短的 `FIRST_INTERIM_AFTER_S`）。
        self._interim_first = True
        #: ⭐ 上次合成成功时的**章内句数**（`-1` = 本章还没成功过）。
        #: `REFRESH_MIN_NEW` 与「封章省一次调用」都拿它比（见 `_synthesize`）。
        self._interim_ok_n = -1

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

    def stats(self) -> dict:
        """⭐ 只读快照（测试模式采集面 ⑤）。⚠️ **口径对齐离线探针的 `metrics.json`**。

        ⚠️ 在线与离线**必然有一部分对不上**，别把它们当同一件事：
        - 离线是**一次跑完**再统计；在线是**跑到哪算到哪**（收尾那一刻的快照）。
        - `window_interval_median_s` / `input_tokens_est` / `elapsed_s` /
          `board_sentence_ratio` 这类在线**要么拿不到、要么口径不同**
          → **刻意不放进这里**。宁可缺，也别给一个看着像、其实不可比的数。
        - ⭐ 能对上的那几项（窗口 / 章 / 空窗比 / Jev / 积压 / 重试 / 课务）
          **字段名逐字一致** —— 就是为了对拍。
        """
        w = max(self._window_n, 1)
        return {
            "windows": self._window_n,
            "atom_calls": self._atom_calls,
            "chapter_calls": self._chapter_calls,
            "calls_total": self._calls,
            "failed_calls": self._failed,
            # ⭐ 3.11：分开原子/章节的失败数 + 「尝试合成」vs「真写进文件」的章数。
            "failed_atom_calls": self._failed_atoms,
            "failed_chapter_calls": self._failed_chapters,
            "chapters": self._chapters_final,
            #: ⚠️ 口径（2026-11-17 起）：**不是**“调了几次模型”，而是“封了几章”。
            #: 封章省一次调用那条路（句数没变）**不增加** `chapter_calls`，但仍算一章。
            "chapters_attempted": self._chapters_final,
            "chapters_written": self._chapters_written,
            "chapters_nonempty": self._chapters_nonempty,
            "chapters_interim": self._chapters_interim,
            #: ⭐ 3.12：段落刷新里的**语义拆章**次数（窗口主题换章的不算）。
            "shift_splits": self._shift_splits,
            # ⭐ 3.11：收尾有没有被截断（线程没回来 / 拿不到锁 / 异常）。
            "finish_truncated": bool(self._finish_truncated),
            "empty_window_ratio": round(self._empty_windows / w, 3),
            "deadlines_regex": self._deadlines_regex,
            "deadlines_model": self._deadlines_model,
            "jev_gate_calls": self._gate_calls,
            "gaps": self._gap_dropped,
            "backlog_peak": self._backlog_peak,
            "retries": self._retries,
            "atoms": self._atom_n,
            "chapters_enabled": self._chapters_on,
            # ⭐ 3.11.1：账户级失败熔断（401/402/403）后本节停用 —— 报告要能看出
            #    "atoms=0" 是因为 402，而不是模型一直空转。
            #    ⚠️ 存**状态码**（0 = 没熔断）：`stats()` 的约定是"全是计数器/比例"，
            #    塞字符串会破坏那条不变量（`test_testmode.py` 的 T9 钉着）。
            "model_down": int(self._model_down or 0),
        }

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
                    self._finish_truncated = True         # ⭐ 3.11：收尾被截断，报告要能看出来
                    print(f"⚠ 实时总结的 worker 还在跑（超过 {timeout_s:.0f} 秒拿不到锁）"
                          f"—— 收尾只关文件，不再提交残余窗口")
                    return
                try:
                    self._finish_locked(cancel)
                finally:
                    self._step_lock.release()
            except Exception as e:                        # noqa: BLE001
                self._finish_truncated = True             # ⭐ 3.11
                print(f"⚠ 实时总结收尾失败({type(e).__name__}: {str(e)[:60]})")
            finally:
                self._stopped.set()

        th = threading.Thread(target=work, daemon=True, name="live-summary-finish")
        th.start()
        th.join(timeout_s)
        if th.is_alive():
            self._finish_truncated = True                 # ⭐ 3.11：剩下的步骤已放弃
            print(f"⚠ 实时总结收尾超过 {timeout_s:.0f} 秒 —— 剩下的步骤已放弃"
                  f"（已写下的章节记录不受影响）")
        self.close()

    def _finish_locked(self, cancel) -> None:
        """收尾里**动共享状态**的那几步。⚠️ 调用方必须**已经持有 `_step_lock`**。"""
        if cancel is not None and cancel.is_set():
            return
        if self._closed:
            self._finish_truncated = True                 # ⭐ 3.11：文件已关 = 剩下的步骤没做
            # ⚠️⚠️ **`close()` 已经跑过 = 这半步白跑**（2026-10-12 全仓审查核实）。
            #    走得通的路径只有一条：`finish()` 在 `timeout_s` 内没等到 work 线程
            #    → 主线程 `close()` 关文件 → 而被放弃的 work 线程**还在**这里接着走。
            #    它每一步都要调一次模型，写出来的章节记录却**静默丢掉**
            #    （`ChapterWriter.append` 关了就 no-op）—— 纯白花钱。
            #    （关文件本身是安全的：`ChapterWriter.close/append` 同锁，见它那段。）
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
        # ⚠️ 上面两步都可能跑很久（各调一次模型），中途主线程可能已经放弃并关了文件。
        if self._closed:
            return
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
            # ⭐ `zh` 一路带到 payload：卡片/小卡的**中文摘要**用它（句子刚定稿时
            #    那份译文已经在手边了，**零额外调用**）。见 `duty.summary_zh`。
            self._hit_deadline(gid, t, en, _zh)
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
        self._pend.append([0, buf])                       # 元素 = [失败次数, 窗口]
        if len(self._pend) > self._backlog_peak:          # ⭐ 采集面 ⑤
            self._backlog_peak = len(self._pend)
        while len(self._pend) > MAX_PENDING:
            gone = self._pend.pop(0)[1]
            self._emit_gap(gone, "dropped")
        self._retry_at = 0.0

    def _emit_gap(self, gone: list, state: str) -> None:
        """丢了一个窗口 → 计数 + 发一条 `gap`。**唯一定义点**（2026-10-01 质量审查）：
        积压挤掉（`dropped`）与毒窗跳过（`failed`）两条路都走它 ——
        `gap` 的字段形状只有一个生产者。

        `gone` = 窗口本体（`[(gid, t, en, zh), …]`，不是 `[次数, 窗口]` 那个信封）。
        """
        self._gap_dropped += 1                            # ⭐ 采集面 ⑤
        self._emit_p({"kind": "gap", "t_from": gone[0][1], "t_to": gone[-1][1],
                      "state": state})

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
        if self._model_down:
            # ⭐ 3.11.1：账户级失败之后，积压的窗口**再也补不回来**（模型不会自己好）。
            #    清队、不重试 —— 第一次撞上时已经出过声；留在这里只会每 30 秒白试一次。
            #    ⚠️ 清队要**发 gap**（`state="failed"`），否则这些窗口静默消失，
            #       笔记覆盖度会低估丢量（第二轮核实指出）。
            for _item in self._pend:
                self._emit_gap(_item[1], "failed")
            self._pend.clear()
            self._retry_at = 0.0
            return
        if self._pending_blocked(now, force):
            return
        #: 这批里**最新的**那个窗口的内容时刻 —— 它对应 `now`（它就是「现在」）。
        #: ⚠️ 必须在循环**之前**取：循环里 `self._pend` 会一直缩短。
        newest_c = self._pend[-1][1][-1][1]
        while self._pend:
            if self._closed:
                # ⚠️ 收尾超时那条路：主线程已经 `close()`，而这个循环**每个窗口调一次
                #    模型**（最多 20 个）—— 写出来的章节记录却没人收（`ChapterWriter`
                #    关了就 no-op）。一次都别再调（2026-10-12 全仓审查指出）。
                return
            item = self._pend[0]
            buf = item[1]
            obj = self._call_atoms(buf)
            if obj is None:
                # ⚠️ **留在队列里**，`RETRY_S` 后重试 —— 这就是改进 ①。
                item[0] += 1
                self._retry_at = now + RETRY_S
                self._retries += 1                            # ⭐ 采集面 ⑤
                if item[0] >= MAX_WINDOW_ATTEMPTS and len(self._pend) > 1:
                    # ⭐⭐ **队头疑似「毒窗」—— 试下一窗做判别**（2026-10-01 审查 F3）。
                    #    原来没有这一层：一窗确定性失败会**一直堵在队头**，直到攒满
                    #    `MAX_PENDING`(20 窗 ≈ 15 分钟)才被当积压挤掉 —— 期间没有原子、
                    #    没有章节，只有每 30 秒白花钱的重试。
                    #    判别语义：下一窗也挂 = 断网形状（不丢、继续攒）；下一窗成功 =
                    #    队头这窗的内容消化不了 → 丢它、发 `gap(state="failed")`、放行。
                    #    ⚠️ 探测**不动**下一窗自己的失败计数 —— 它这次失败是替队头试的。
                    nxt = self._pend[1]
                    probe = self._call_atoms(nxt[1])
                    if probe is None:
                        return
                    self._pend.pop(0)                         # 毒窗出队
                    self._emit_gap(buf, "failed")
                    print(f"⚠ 实时总结：一个窗口试了 {MAX_WINDOW_ATTEMPTS} 次都失败"
                          f"（{buf[0][1]}–{buf[-1][1]}），按缺口跳过；后面的窗口继续")
                    # ⚠️ 换成下一窗，**落穿到唯一的正常尾部**去消化 —— 出队/有效时刻
                    #    公式只留一份（2026-10-01 质量审查），少一处以后漂的地方。
                    item, buf, obj = nxt, nxt[1], probe
                else:
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

    def _guarded(self, chat):
        """包一层：账户级失败（401/402/403）**第一次**就把本节关掉，并出声一次。

        ⚠️ 关掉之后**不抛新的网络请求**：直接 raise 一个本地 RuntimeError，
           让上面既有的 `except` 照旧记账（`failed_calls` 还是真的失败次数），
           但一个字节都不会再发出去。
        """
        def call(*a, **k):
            if self._model_down:
                raise RuntimeError(f"实时总结已停用（HTTP {self._model_down}）")
            try:
                return chat(*a, **k)
            except Exception as e:                        # noqa: BLE001
                code = _http_code(e)
                if code in DEAD_STATUS and not self._model_down:
                    self._model_down = str(code)
                    print(f"⚠ 实时总结已停用（HTTP {code}）：本节不再调用模型；"
                          f"字幕与笔记不受影响", flush=True)
                raise
        return call

    def _call_atoms(self, buf: list):
        """一次原子提取。**抛异常 / 返回 None 都算失败**（同今天的取舍）。"""
        sents = [it[2] for it in buf]
        try:
            self._calls += 1
            self._atom_calls += 1                         # ⭐ 采集面 ⑤
            return self._chat(atom.SYS, atom.build_prompt(sents, self._topic_prev()),
                              atom.MAX_TOKENS, atom.TEMPERATURE)
        except Exception as e:                            # noqa: BLE001
            # ⚠️ 提不出来是"少几条要点"，**不是"课跑不下去"**（同 `mark_lost` 的纪律）。
            self._failed += 1                             # ⭐ 采集面 ⑤
            self._failed_atoms += 1                       # ⭐ 3.11：按层分开
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

        if not got:                                       # ⭐ 采集面 ⑤
            self._empty_windows += 1
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

        _when_by_gid = {it[0]: it[1] for it in buf}
        for a in got:
            if a.kind == KIND_DEADLINE and self._deadline_ok(a.text):
                # ⭐ `a.zh` = **这次原子调用本来就给的中文**（`Atom.zh`，2026-09-30 加的）
                #    —— 摘要用它，**没有新增调用**。
                # ⭐ 3.11.1：`t` 用**来源句**的时刻（`a.src[0]`），不是原子生成时刻 ——
                #    模型窗口可能比原句晚 1–2 分钟，卡片/提醒事项里的时间会被读成"老师说这话的时间"。
                _t = _when_by_gid.get(a.src[0]) if a.src else None
                self._emit_deadline(_t or a.t, a.text, a.src, "model", False, zh=a.zh)

        self._maybe_force_split(now)                      # ⭐ 太长的章先切开
        self._advance(topic, topic_zh, buf, got, now)
        # ⭐⭐ **段落跟着窗口刷新**（2026-11-17）：每个窗口被吸收进当前章之后就检查一次，
        #    后面两路闸在 `_maybe_interim`（有测试钉住“不满足任一条不合成”）。
        #    ⚠️ 必须用 `now`（不是 `self._clock()`）—— 否则两道闸恒成立/恒不成立。
        # ⚠️ 3.12：`_synthesize` 里判出换题点时会就地拆章（`_split_at_shift`），
        #    拆完的**新章**已经自己合过首段了，这里不会重复合成。
        self._maybe_interim(now)

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
        #: ⭐ 下一章是**到点硬切**出来的（不是讲者换题）→ 它对语义换题不设 `SHIFT_MIN_S` 门槛。
        #: ⚠️ 2026-11-18 重放：第五组讲 + 问答 18 分钟，15 分钟那刀切在问答中间，3 分钟后
        #:    最后一组上台时新章「才讲了 3 分钟」被 `SHIFT_MIN_S` 拦住 → 问答和下一组并成一章。
        self._forced_next = True

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
            # ⭐⭐ 3.12：**刚拆出来的新章不认领「旧组的粘住主题」**。
            #    段落刷新里拆章后，窗口主题还粘在前一组上（重放里它 1–4 分钟才换）——
            #    如果让新章认领它，新章会被标回旧题，`MIN_CHAPTER_S`(180 s) 一过
            #    窗口主题再变一次 → **又切一刀**（同一组被拆成两章）。
            #    → 只吸收、不认领，等一个**其他**主题的窗口进来再定题。
            #    ⚠️ 比较方式与下面那条完全一致（去空格 + 不分大小写），否则就成了另一种判据。
            retired = (self._cur.get("retired") or "").strip().lower()
            if retired and topic.strip().lower() == retired:
                self._absorb(buf, atoms, now)
                return
            # ⭐⭐ **当前章还没定题**（开场那几个空主题的窗口攒在这里，
            #    或者是刚拆出来的新章）→ 让这一章**认领**这个主题，**不是**封章另起。
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
                         #: ⭐ 3.12：`全局句号 → "HH:MM:SS"`。拆章时新章的 `t0` 要取
                         #: **换题点那一句**的时刻（拿窗口首句或 `now` 都不对）。
                         "times": {},
                         #: ⭐ 这章是不是 `MAX_CHAPTER_S` 硬切出来的（见 `_maybe_force_split`）。
                         "forced": self._forced_next,
                         #: ⭐ 上一版（成功的）段落 —— 下次合成时喂给模型当 `prev_para`，
                         #: 失败**不覆盖**（见 `_synthesize`）。
                         "para": None,
                         #: ⭐ 这一章**开张**的模拟时刻 —— `MIN/MAX_CHAPTER_S` 算的就是它。
                         #: ⚠️ 用 `now`（**模拟时钟**），不是 `self._clock()`：
                         #:    两个域混用会让判据**恒不成立**（2026-09-30 栽过一次，判据 ⑦）。
                         "t_open": now}
            self._cid += 1
            self._forced_next = False
            self._interim_at = now
            self._interim_first = True     # ⭐ 新章：首段重新回到「开章那一窗就出」那一档
            self._interim_n = -1          # 新章：上一章的句数不作数（撞上同一个数会白等 60 秒）
            self._interim_ok_n = -1       # 新章：还没成功合成过（“封章省一次调用”看它）
        c = self._cur
        if title:                                         # 定题（开场那些空主题的窗口
            c["title"], c["title_zh"] = title, title_zh    # 早就攒在里面了）
        for gid, t, en, _zh in buf:
            c["sents"].append((gid, en))
            c["times"][gid] = t
            c["hi"] = max(c["hi"], gid)
            c["t1"] = t
        c["atoms"].extend(atoms)

    def _maybe_interim(self, now: float) -> None:
        """临时合成（段落刷新）：**开章那一窗就出首段** / **之后每吸收一窗过两道闸**。

        ⭐⭐ 两条路（2026-11-17 作者定「跟着窗口刷新」）：
          1. **首段**：`FIRST_INTERIM_AFTER_S` = 0 —— 开章那一窗吸收完就合成。
             `_absorb` 里 `self._interim_at = now`，所以 `now - _interim_at ≡ 0` ≥ 0。
             ⚠️ 不再叠等待：实测（test key 重放）真正的首段延迟是 **165–185 s**，
                因为**章是第一个窗口吸收时才开的**（窗 75/30 秒才闭合）+ 那 90 秒。
             ⚠️ 不要求「新增 3 句」—— 开章那一刻章里所有句子都是新的，且
                `build_prompt` 有「不到 30 句只写 1–2 句」那条规则。
          2. **之后**：每吸收一个窗口就检查一次（`_absorb_window` 里调），这里是
             `step` 里的**兜底**（窗口被闭合但没吸收、或时间在走）。两道闸：
             距上次合成 ≥ `REFRESH_GAP_S` **且** 章内新增原文句 ≥ `REFRESH_MIN_NEW`。
        ⚠️ 两路都要先过「没有新句就不合成」——那是**结构不变量**，不是优化。
        ⚠️ 时间一律用 `step(now)` 的 `now` 域（文件头「两个域别混」）。
        """
        if not self._chapters_on or self._closed or self._cur is None:
            return
        #: ⭐ `_split_at_shift` 只会从这里（经 `_synthesize("interim")`）进来 —— 它要的
        #: 「现在」必须是 `step(now)` 的域，不是 `self._clock()`（文件头「两个域别混」；
        #: Claude 验收时抓到 pi 第一版用了 `self._clock()`：假时钟/积压窗口下新章的 `t_open`
        #: 会落在另一个时间轴上，`MIN/MAX_CHAPTER_S` 对新章恒成立或恒不成立）。
        self._shift_now = now
        n = len(self._cur["sents"])
        if not n:
            return
        # ⚠️ 没有新句子就不再合成（2026-10-17 美国用户实测）: 下课后 app 没退出,
        #    同一章、同一份 859 token 的输入每 8 分钟（醒着的时间）合成一次, 7 小时里
        #    白发 15 次 —— 结果和上一次一模一样。面板上看着像「一分钟的在线」。
        if n == self._interim_n:
            return
        if self._interim_first:
            if now - self._interim_at < FIRST_INTERIM_AFTER_S:
                return
        elif (now - self._interim_at < REFRESH_GAP_S
              or n - max(self._interim_ok_n, 0) < REFRESH_MIN_NEW):
            return
        # ⚠️ 先落下开关再合成：即使模型报错（`_synthesize` 内部已重试一次），也不该
        #    在 60 秒后再发一遍 —— 那一档是给「快点出」的，不是给重试的。
        self._interim_at = now
        self._interim_n = n
        self._interim_first = False
        self._synthesize("interim")

    def _finalize_current(self, now: float) -> None:
        """给当前章做**正式**合成并封章。没有内容就什么都不做。

        ⭐ 封章时把这一章的 `para.lead.zh` 记进 `_prior_leads` —— 下一章的 prompt 要
           它（光有标题拦不住重复，见 `chapter.build_prompt`）。
        ⭐⭐ **封章省一次调用**（2026-11-17）：段落现在每 60 秒就刷一次，段落已经是最终
           内容之后再花一次调用只能拿到同样的结果。→ 章内句数与上次**成功**合成时相同
           就不调模型，直接把上次结果重写成 `status="final"`（`version+1`）。
           ⚠️ 计数口径：`chapter_calls` / `calls_total` **不增加**（确实一次模型都没调）；
           `chapters_attempted`（= `chapters`）/ `chapters_written` / `chapters_nonempty`
           照旧算一章（确实封了一章）。`chapters_interim` 也不动（这次不是临时合成）。
        """
        if self._cur is None:
            return
        c = self._cur
        if not c["sents"]:
            self._cur = None
            return
        para = None
        if self._chapters_on and not self._closed:
            para = self._synthesize("final")
        elif c.get("para") is not None:
            # ⭐ 章节关掉 / 已关文件时，至少把已有的那一版段落作为本章的结论交出去
            #   （不调模型、不写文件）。
            para = c["para"]
        if c["title"]:
            self._prior.append(c["title"])
            #: ⚠️ **只在这一个分支里追加** —— 两个列表必须按下标对齐，
            #: 分两处追加迟早錯位（而错位的表现是「上一章的结论跑到这一章头上」）。
            self._prior_leads.append(str(((para or {}).get("lead") or {}).get("zh") or ""))
        self._cur = None

    def _synthesize(self, status: str) -> dict | None:
        """一次章节合成。**失败重试一次**。

        返回这次的 `para`（`dict` 或 `None`）—— 调用方 `_finalize_current` 要它的
        `lead.zh` 给下一章当「已经写过什么」。⚠️ 不再另存一个 `_last_lead` 字段：
        那是把同一个值存两份，迟早两份不一样。

        ⭐⭐ 失败时的行为（2026-11-17，作者：右栏不能因为一次网络抖动清屏）：
          · `interim` 失败 → **什么都不写、不 emit**（只记失败数）—— 界面上一次那一版
            段落**保持原样**，等下一次刷新。
          · `final` 失败 → 本章有上一版成功的 `para` / `sentences` 就拿它们写 final
            （不是空章 —— 空壳记录会让课后笔记里这一章变成空的）。
          · 两者都没有上一版 → 照旧写一条空章（**只有这种情况**：从没成功过）。
        """
        c = self._cur
        if c is None:
            return None
        #: ⭐ 这一段有没有新增原文的范围（`_prev_para_block` 只在它非空时才让模型判 `shift`）。
        new_range = self._new_range(c)
        #: ⭐ 封章省一次调用：句数与上次**成功**合成时相同 -> 重写上次结果即可。
        #: ⚠️ 只有 `final` 省；`interim` 走不到这里（`_maybe_interim` 已经拦了「没新句」）。
        if (status == "final" and self._interim_ok_n >= 0
                and self._interim_ok_n == len(c["sents"])
                and c.get("para") is not None):
            c["ver"] += 1
            self._chapters_final += 1                     # ⚠️ 口径：仍是“尝试封章”，但这一条
                                                         #    **没有** 消耗模型调用（见下）
            return self._write_chapter(c, status, c["para"], c.get("sentences") or [],
                                       keep=True)
        c["ver"] += 1
        obj = None
        if status == "final":                             # ⭐ 采集面 ⑤
            self._chapters_final += 1
        else:
            self._chapters_interim += 1
        for attempt in (1, 2):
            if self._model_down:
                # ⭐ 3.11.1：熔断后 `_chat` 是**本地抛**（一个字节都不发）——
                #    再记 calls/failed 会让报告写成「调了 N 次、全失败」，
                #    与实际请求数对不上。直接收手，章节按空章落盘。
                break
            try:
                self._calls += 1
                self._chapter_calls += 1                  # ⭐ 采集面 ⑤
                obj = self._chat(ch.SYS_CHAPTER,
                                 ch.build_prompt(c["sents"], c["atoms"],
                                                 self._prior, self._prior_leads,
                                                 prev_para=c.get("para"),
                                                 prev_hi=c.get("para_hi")),
                                 ch.MAX_TOKENS, ch.TEMPERATURE)
            except Exception as e:                        # noqa: BLE001
                self._failed += 1                         # ⭐ 采集面 ⑤
                self._failed_chapters += 1                # ⭐ 3.11：按层分开
                if not self._model_down:                  # 停用后每章两次都很吵，只记数
                    print(f"⚠ 章节合成失败({str(e)[:50]}); 第 {attempt} 次")
                obj = None
            if obj is not None:
                break
        if obj is None:
            # ⭐⭐ 失败：**不清屏**（见 docstring 那张表）。
            if status == "interim":
                return None                               # 不写、不 emit —— 上一版段落呼吸照旧
            prev_para, prev_sents = c.get("para"), c.get("sentences") or []
            if prev_para is not None or prev_sents:
                return self._write_chapter(c, status, prev_para, prev_sents, keep=True)
            return self._write_chapter(c, status, None, [])   # 从没成功过 -> 空壳（老行为）
        title, title_zh = ch.title_of(obj)
        parsed = ch.parse_chapter(obj, c["lo"], c["hi"])
        if status == "interim":
            #: ⭐⭐ 3.12：**换题点**（只在这个刷新调用里判，见 `chapter._shift_rules`）。
            #: ⚠️ 取不到、越界、旧章不够长 → `None`，照旧只写段落（原行为）。
            #: ⚠️ `final` 里的 `shift` **一律忽略**（那时章已封，拿它拆一个不写的章没意义）。
            shift = ch.parse_shift(obj, *new_range) if new_range else None
            if shift is not None and self._split_at_shift(c, shift):
                return None                           # 混合段落**丢弃不写**（见 `_split_at_shift`）
        return self._write_chapter(c, status, parsed["para"], parsed["sentences"],
                                   title=title, title_zh=title_zh)

    # ------------------------------------------------------------ 拆章
    def _new_range(self, c: dict) -> tuple | None:
        """本章**上一版之后新增**的原文范围 `(首句号, 末句号)`；没有 → `None`。

        ⚠️ 与 `chapter.build_prompt` 那句提示**同一个定义点**（`chapter.new_range_of`）：
           两边各算一遍的话，模型收到的「新增是第 X–Y 句」与本地拿来闸 `shift.at`
           的范围会错开一句，症状是换题点**静默永不命中**。
        """
        return ch.new_range_of(c["sents"], c.get("para_hi"))

    def _split_at_shift(self, c: dict, shift: dict) -> bool:
        """按段落刷新里判出的换题点**就地拆章**。拆了返回 `True`（调用方丢掉这次的段落）。

        ⭐ 为什么该在**这里**拆（而不是等窗口主题）：真课重放里主题标签会**粘住** ——
           一组讲完、下一组已经讲了 1–4 分钟才换章；有时后一组把前一组的段落盖掉。
           而这一次调用手里有整章原文 + 上一版段落 + 新增范围，`shift` 就是它给出的
           语义边界（arXiv 2502.03111 / LecSync digest 的做法）。

        ⚠️⚠️ **这次合成退回的段落不能写**：它把旧组和新组的要点混成了一段，
           两边都写不准。旧章用**上一版**（`para_hi < at`，恰好只盖旧组），
           新章用旧章的段落当底稿**立刻重合一版**。
        ⚠️ 旧章句数不够 `SHIFT_MIN_KEEP` → 不拆（三五句拆出来的章没有纲要价值）。
        """
        at = shift["at"]
        old_sents = [s for s in c["sents"] if s[0] < at]
        if len(old_sents) < SHIFT_MIN_KEEP:
            return False
        t_a = c["times"].get(at)
        t_b = c["times"].get(old_sents[0][0])
        if t_a and t_b and not c.get("forced") and _span_s(t_b, t_a) < SHIFT_MIN_S:
            return False                              # 旧章讲得太短（见 `SHIFT_MIN_S`）
        new_sents = [s for s in c["sents"] if s[0] >= at]
        if not new_sents:
            return False                              # `at` 落在末句之后= 没新章可言
        prev_para, prev_sents = c.get("para"), c.get("sentences") or []
        #: 旧章的窗口主题（拆出来的新章要先把它当「粘住的主题」防一手）。
        #: ⚠️ 必须在 `_finalize_current` **之前**取：它清 `_cur`，但 `atoms` 那边不改；
        #:    这里先把旧章快照下来，后面的归属计算也读得快。
        retired = str(c.get("title") or "")
        old_atoms = list(c["atoms"])
        old_times = dict(c["times"])
        # ⚠️⚠️ 旧章**不许再调一次模型**：这次合成返回的段落虽然混了新组的要点，
        #    但上一版段落（`para_hi < at`）正好只盖旧组，它就是旧章的定稿。
        #    `_synthesize("final")` 的「封章省一次调用」看 `_interim_ok_n`，所以把
        #    它按旧章句数对上 —— 这是在**声明**「上一版段落就是这一章的终稿」，
        #    不是偷数字（`keep=True` 那条路不改 `para_hi`，段落盖到第几句仍是事实）。
        #    ⚠️ 没有上一版（从没成功过）才真的调一次 final（`-1` 那条路）。
        self._interim_ok_n = len(old_sents) if prev_para is not None else -1
        # ⚠️ `_finalize_current` 会拿 `c["title"]` 当 `_prior` —— 但那个位置该是**旧章的**
        #    模型标题（`_write_chapter` 里 `title or c["title"]` 那条已经这么用了）。
        #    新章的 `syn_title` 更不能带过去（否则旧章的 final 记录顶着新话题的标题）。
        c["title"] = str((c.get("syn_title") or ("", ""))[0] or retired)
        c["title_zh"] = str((c.get("syn_title") or ("", ""))[1] or "")
        c["sents"] = list(old_sents)               # 旧章的句数就是 `at` 之前那些
        c["hi"] = max(g for g, _ in old_sents)
        c["t1"] = old_times.get(c["hi"]) or c.get("t1", c["t0"])
        c["atoms"] = [a for a in old_atoms if a not in self._atoms_from(old_atoms, at)]
        c["times"] = {g: t for g, t in old_times.items() if g < at}
        now = self._shift_now
        self._finalize_current(now)
        cc = {"id": self._cid, "title": "", "title_zh": "",
              "syn_title": (shift["title"], shift["title_zh"]),
              "t0": old_times.get(at) or old_times.get(new_sents[0][0]) or c["t0"],
              "lo": new_sents[0][0], "hi": new_sents[-1][0],
              "sents": list(new_sents), "atoms": self._atoms_from(old_atoms, at),
              "ver": 0,
              #: ⚠️⚠️ 新章**从零写**（Claude 验收改）：拿旧章段落当「上一版」会触发
              #:    `_prev_para_block` 的「结论没被推翻就保持 lead」—— 新一组的首段会被
              #:    旧一组的结论拽住。「别重写旧章内容」由 `_prior_leads` 管（`_finalize_current`
              #:    已把旧章 lead 记进去，`build_prompt` 会要求删掉与它重复的句子）。
              "para": None,
              "para_hi": None,
              "sentences": [],
              "times": {g: t for g, t in old_times.items() if g >= at},
              "t_open": now,
              #: ⭐ 旧章的窗口主题 —— `_advance` 靠它不认领（标签还粘在旧组上）。
              "retired": retired}
        self._cid += 1
        self._cur = cc
        #: ⚠️ 新章的开启闸门要跟 `_absorb` 一样**重置**：不重置的话 `_interim_n` /
        #:    `_interim_ok_n` 还挂在**旧章**的句数上，两道闸对新章算的就是另一章的数。
        #:    `_interim_first=False`：首段就在下面这一行里出，不该再走「开章那一窗」。
        #:    `_interim_ok_n` 落到 `-1` 是因为新章还没**成功**合过（下一版真有新句就该真调）。
        self._interim_at = now
        self._interim_first = False
        self._interim_n = len(new_sents)
        self._interim_ok_n = -1
        #: ⚠️ 计数口径：`chapters_interim` 由 `_synthesize` 自己记（这次确实调了模型）。
        self._shift_splits += 1
        self._synthesize("interim")
        return True

    @staticmethod
    def _atoms_from(atoms: list, at: int) -> list:
        """`at` 及之后的原子归新章。**没有 `src` 的留旧章**（归属不明 → 不抢）。

        ⚠️ 只在 `_split_at_shift` 一处用两次（新旧两边互补），同一个判法不抄两份 ——
           抄两份迟早两边加起来不等于全体，那批原子就**静默消失**了。
        """
        return [a for a in atoms if a.src and min(a.src) >= at]

    def _write_chapter(self, c: dict, status: str, para, sents: list,
                       *, title: str = "", title_zh: str = "", keep: bool = False) -> dict | None:
        """把一版章节写进旁路文件 + emit。**`_synthesize` 里唯一的落盘出口。**

        `keep=True` = 用的是**上一版**的 `para` / `sentences`（封章省一次调用，或者失败
        回退）—— 那就不用再拷回 `c`，但也**不算一次成功合成**（`_interim_ok_n` 不动）。
        """
        if not keep:
            if para is not None:
                c["para"] = para                         # ⭐ 上一版段落（失败不覆盖）
                c["para_hi"] = c["hi"]                   # ⭐ 上一版写到第几句（下一版告诉模型哪些是新的）
                #: ⭐ 合成成功时记下“这一章当时有多少句” —— 封章省一次调用靠它比。
                #: ⚠️ 与 `_interim_n`（“上次尝试时的句数”）不是同一个数、不能合。
                self._interim_ok_n = len(c["sents"])
            if sents:
                c["sentences"] = sents                   # ⭐ final 失败时拿它回退
            if title or title_zh:
                c["syn_title"] = (title, title_zh)       # ⭐ 模型给的标题，复用上一版时一起带上
        else:
            # ⚠️ 复用上一版时标题也要用上一版**模型**给的那个 —— 否则 final 记录会退回
            #    窗口主题当标题，右栏标题在封章那一刻无故变一下（Claude 验收时发现）。
            title, title_zh = c.get("syn_title") or ("", "")

        chp = ch.Chapter(id=c["id"], status=status,
                         title=title or c["title"], title_zh=title_zh or c["title_zh"],
                         t0=c["t0"], t1=c.get("t1", c["t0"]),
                         lo=c["lo"], hi=c["hi"], sentences=sents,
                         para=para, version=c["ver"])
        n_written = self._write(chp)
        if status == "final" and n_written:
            self._chapters_written += 1
            if sents:
                self._chapters_nonempty += 1
        self._emit_p({"kind": "chapter", "chapter": chp.as_json()})
        return para

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

    def _hit_deadline(self, gid: int, t: str, en, zh="") -> None:
        """**正则路径**：句子一定稿就查。不调模型 → 一两秒内上屏（判据 T14）。

        ⚠️ `zh` = 这句的**即时译文**（`feed` 那条路上的第四个元素）—— 只用来做卡片上的
           中文摘要，**不发任何请求**（「一些事儿」代价里没有模型调用这一项）。
        """
        hit = ch.deadline_hits(en)
        if hit is None:
            return
        self._emit_deadline(t, str(en or ""), [gid], "regex", hit["changed"], zh=zh)

    def _emit_deadline(self, t: str, quote: str, src, source: str, changed: bool,
                       zh: str = "") -> None:
        """⚠️ `zh` = **已有的**中文（正则路径 = 即时译文；模型路径 = 模型给那句的中文）。
        两个都是**这次调用本来就有的产物**，不是为「一些事儿」新加的请求。"""
        # ⭐ 采集面 ⑤ —— 按来源分（正则/模型），比在两个调用点各加一处不容易漏。
        if source == "regex":
            self._deadlines_regex += 1
        else:
            self._deadlines_model += 1
        rec = {"type": ch.DEADLINE, "t": t, "quote": quote, "zh": str(zh or ""),
               "src": sorted(set(src or ())), "source": source, "changed": bool(changed)}
        self._write(rec)
        self._emit_p({"kind": "deadline", **{k: rec[k] for k in
                                             ("t", "quote", "zh", "src", "source",
                                              "changed")}})

    # ------------------------------------------------------------ 杂项
    def _write(self, rec) -> int:
        """追加一条记录，返回**写成了几条**（`ChapterWriter.append` 的约定；关文件后是 0）。"""
        return int(self._writer.append([rec]) or 0)

    def _emit_p(self, payload: dict) -> None:
        """⚠️ 推给界面。**调用方负责 `stopping` 守卫** —— 这里只做「有没有人接」。"""
        if self._emit is None:
            return
        try:
            self._emit(payload)
        except Exception:                                 # noqa: BLE001
            pass
