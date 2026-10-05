#!/usr/bin/env python3
"""本地实时课堂双语字幕 ClassLive 主入口。

用法:
  python main.py --source file ~/lecture_0909/rec_socio_crisis.m4a --ui terminal
  python main.py --source mic --ui overlay
  python main.py --source blackhole --ui overlay

线程模型:
  主线程  : 读音频块 -> Segmenter(只做 VAD 判定, 快速) -> 排空结果队列 -> 调 UI
  草稿线程: 对"当前句"做 ASR(latest-wins, 旧的丢弃)
  定稿线程: 句末 ASR + LLM 修正翻译 -> 结果队列
  => 音频循环永不阻塞; 所有 UI 调用都在主线程(悬浮窗要求)
"""
from __future__ import annotations
import argparse, collections, os, queue, re, sys, threading, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from capture import load_source, SR
from vad import Segmenter
from asr import load_asr, load_final_asr, is_degenerate
from translator import load_translator
from cloud_translator import (DEFAULT_CTX_CHUNK, load_api_key,
                              load_translator as load_cloud_translator,
                              answer_user_content)
from obsidian_writer import ObsidianWriter, resolve_vault, remember_vault
from build_notes import (TermNotes, format_gloss, detect_proper_nouns, lookup_term)
from testmode import TestSession
import instance_lock
import models
import notice

PARTIAL_MAX_S = 10          # 草稿只转写最近 N 秒, 限制单次耗时
DEVICE_CHECK_S = 2.0        # 实时音源: 多久重探一次「默认输入设备还是不是那个」
SLEEP_GAP_S = 10.0          # 墙钟一次跳这么多 = 系统睡过一觉(见 check_clock_and_device)
#: 收尾时留给实时总结的上限（秒）——提残余窗口 + 给当前章做一次正式合成。
#: ⚠️ 它只是**上限**，不是固定等待：正常那一节实测 6–9 秒就走完了。
#: ⚠️ 定这个数的约束是**收尾不能明显变慢**（那一步本来就在等精修），而不是"够用"。
#: 比它更长的一律放弃 —— 放弃之后 `ChapterWriter` 已关，迟到的写入是 no-op。
SUMMARY_FINISH_S = 25

#: 上传落点：**HTTPS PUT** 到 Cloudflare Worker（**用户零配置** —— 不再靠作者私人
#: ssh 别名 `bldcam`，那正是"朋友传不上来"的根）。落点常量在 `upload_endpoint.py`
#: （单一来源）。**想整个关掉**用 `CLASSLIVE_UPLOAD=0`
#:    （判据与离线回放必须用它 —— 不然每跑一次测试就真往 R2 传一份）。

#: 收尾时最多**等后台上传多久**。⚠️ 实测 9 MB = 1.8 秒（一节课的 Opus），
#: 所以 10 秒够正常那一次跑完；跑不完就留着，下次启动接着传。
#: ⚠️⚠️ **不能不等** —— 它是 **daemon 线程**，进程一退就被杀，
#:    结果是「报告传上去了、音频没传」（2026-09-30 实测就是这形状）。
#: ⚠️ 也不能无限等 —— 收尾那条路上用户已经在等了。
UPLOAD_JOIN_S = 10.0

# 以这些词收尾(且无句末标点)→ 句子没说完, 不能单独送 LLM 翻译
DANGLING_TAILS = {
    "that", "which", "who", "whom", "whose", "where", "when", "because",
    "and", "or", "but", "so", "if", "as", "than", "to", "with", "in", "of",
    "on", "for", "at", "by", "from", "into", "about", "the", "a", "an", "is",
    "are", "was", "were", "be", "been", "will", "would", "can", "could",
    # 口语连读缩写(ASR 常按连读转写; 漏掉会把 "what we're" 当完整句)
    "we're", "they're", "you're", "i'm", "it's", "he's", "she's", "there's",
    "what's", "we've", "i've", "you've", "they've", "we'll", "i'll", "you'll",
    "won't", "don't", "doesn't", "didn't", "isn't", "aren't", "wasn't",
    "can't", "couldn't", "wouldn't", "shouldn't", "hasn't", "haven't",
}


def is_incomplete(text: str) -> bool:
    """以连词/介词/冠词/助动词收尾且无句末标点 → 判断为未说完。"""
    words = (text or "").strip().split()
    if not words:
        return False
    if text.rstrip().endswith((".", "!", "?", "…")):
        return False
    last = words[-1].lower().strip(".,!?\"'()[]")
    return last in DANGLING_TAILS


def all_settled(finalq, streamq, drafts, busy: bool, carry_text: str) -> bool:
    """收尾判据(停止/播完后的冲刷等待): 所有已收进来的语音都已变成落盘结果。

    ⚠️ 四项**必须一起看**, 只看队列会在两种真实场景丢最后一句(均实测复现):
    ① 半句挂在 carry 里等下一个 utterance 拼接 —— 此时 finalq/streamq 全空,
       但它还没送 LLM;
    ② 最后一句以悬挂词收尾 → 进 carry → 定稿线程退出时 `_force_emit_carry`
       才开始调 LLM —— 首字落地前队列恰好全空, 冲刷循环的"双检查"(间隔
       0.25s)会在云端首字延迟(~1s)内双双通过 → 提前 break, 最后一句丢失。
    所以除队列外还要看 busy(定稿线程正在 ASR/LLM)和 carry(还有半句没送出)。"""
    return (finalq.empty() and streamq.empty() and drafts.empty()
            and not busy and not carry_text)


def split_sentences(text: str, max_words: int = 25) -> list[str]:
    """按句末标点把 ASR 长段切成句子。
    - 过短碎片(<=2词)并入前一句;
    - 无标点 run-on: 优先在逗号/分号处切, 否则才按词数切(避免劈开语法结构)。"""
    text = (text or "").strip()
    if not text:
        return []
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", text) if p.strip()]
    out: list[str] = []
    for p in parts:
        if out and len(p.split()) <= 2:
            out[-1] = f"{out[-1]} {p}"
        else:
            out.append(p)

    final: list[str] = []
    for s in out:
        words = s.split()
        if len(words) <= max_words:
            final.append(s)
            continue
        idx = 0
        while idx < len(words):
            chunk = words[idx:idx + max_words]
            if idx + max_words < len(words):
                cut = None
                for j in range(len(chunk) - 1, max(1, len(chunk) // 2), -1):
                    if chunk[j].endswith((",", ";", ":")):
                        cut = j + 1
                        break
                if cut:
                    final.append(" ".join(chunk[:cut]))
                    idx += cut
                    continue
            final.append(" ".join(chunk))
            idx += max_words
    return final or [text]


def now() -> str:
    return time.strftime("%H:%M:%S")


def echo(msg: str) -> None:
    print(msg, flush=True)


# 终端提示等多久算「人不在」。⚠️ **这个数是拍的，没有测量背书** ——
# 真正该测的是「作者离开键盘多久」，无从测。改这里**不影响 UI 那条路**（那条走卡片倒计时）。
ASK_TIMEOUT_S = 60.0


def _input_timed(prompt: str, timeout: float) -> str | None:
    """带超时的 `input()`。**超时 / EOF / Ctrl+C 一律返回 `None`**（三态合一）。

    ⚠️ 为什么不走 `input()` + `signal.alarm`（那是 SO 上的第二高票做法）：
       本仓库的收尾**可以跑在工作线程上**，而 Python 的信号 handler
       **只能在主线程装、也只在主线程跑**（PEP 475）。从工作线程 `signal.signal`
       直接 `ValueError`；`signal.alarm` 虽然能设，但投递到的是**主线程** ——
       等于把此刻正在跑 run loop 的主线程叫醒。`select` 没有这个问题。

    ⚠️ 四条实测坑（macOS + 真 pty 量的，不知道就会写错）：
      · 规范模式下**打了字但没回车，fd 不算可读** → 超时可能落在半句输入中间；
      · ⚠️ **残留输入会被下一个提示吃掉**（实测：提示 1 里打 `abc` 不回车，
        提示 2 直接返回 `'abc\\n'`）→ 所以超时必须 `tcflush`。**这不是保险丝，是修 bug**；
      · `readline()` 遇 EOF 返回 `''` **不抛** `EOFError`（`input()` 才抛）→ 要判空串；
      · `select` 对 `/dev/null` 与普通文件**恒报就绪** → 必须按 `isatty()` 分支，
        不能靠 `select` 的返回值推断"有没有人"。
    """
    import select
    import sys
    try:
        fd = sys.stdin.fileno()
    except (OSError, ValueError):
        return None                       # stdin 被关了（句柄没了 / 无控制台）
    sys.stdout.write(prompt)
    sys.stdout.flush()
    try:
        if sys.stdin.isatty():
            if not select.select([fd], [], [], timeout)[0]:
                try:
                    import termios
                    termios.tcflush(fd, termios.TCIFLUSH)
                except Exception:                     # noqa: BLE001
                    pass                              # 冲不掉也不该因此不返回
                return None
        line = sys.stdin.readline()       # 非 tty：立刻返回（EOF 给空串）
    except KeyboardInterrupt:
        return None
    except (OSError, ValueError):
        return None
    return None if line == "" else line.strip()


def _terminal_usable(stream=None) -> bool:
    """收尾那句问话落在终端上，用户**看得见**吗。

    ⚠️⚠️ 双击 `ClassLive.app` 那条路**根本没有终端**：`sitecustomize.py` 把
       stdout/stderr 接进 `~/Library/Logs/ClassLive/app.log`，stdin 也不是 tty，
       于是 `_ask_save_notes` 的 `readline()` **立刻**拿到 EOF。
       （2026-09-29 实测：双击 + ✕ 的一节课，日志里只有那句问话，
       屏上从头到尾什么都没有。）
    ⚠️ `isatty()` 在流已关闭时抛 `ValueError` —— 必须接住。
       收尾路径上再抛一次，就等于丢整节课的笔记。
    """
    s = sys.stdin if stream is None else stream
    try:
        return bool(s is not None and s.isatty())
    except (AttributeError, ValueError, OSError):
        return False


def _wrapup_route(ui, ui_gone: bool, terminal: bool | None = None) -> str:
    """收尾那句「存不存」走哪条路：`"ui"` 还是 `"terminal"`。

    ⚠️⚠️ **「窗口在问话之前就关了」≠「用户想放弃这份笔记」。**
       `✕` 正是 overlay 模式的**正常停止方式** —— `README.md:310` 逐字写着
       「点悬浮窗右上角 **✕**，或终端按 **Ctrl+C**（两者都是**优雅退出**：
       冲刷队列 + **落盘**）」，`docs/DESIGN.md:92` 同款，六处文档一致。

       2026-09-28 的回归就出在这里：`Overlay.ask_save()` 一进来看到 `_closed`
       就返回 `None`，于是一路 `give_up` → **整份 `writer.close()` 被跳过，
       这节课一个字笔记都不写**。改前 ✕ 走的是 `_ask_save_notes`，那条对超时 /
       EOF **一律默认存**。

       → 窗口**已经**关了的时候退回终端那条。
       （由 OCR 审计发现，`main.py` 那一段现在直接调它。）
       ⚠️ **2026-09-30 又往前走了一步**：「问话**期间**被关窗」也**不再算放弃** ——
       它现在与超时同一条路（都按存走）。那条真的让一个朋友丢了一节课的笔记
       （课上完点 ✕ 停止 → 窗口还在 → 又点了一次 ✕）。理由见 `Overlay.ask_save`。
       所以**放弃只剩一个触发点**：用户显式说不（终端答 `n` / 卡上点「不存」）。

    ⚠️⚠️ **2026-09-29 补的那个洞：退回终端，前提是「有终端」。**
       双击启动时 `_closed` 一定为真（✕ 是那条路上**唯一**的停止方式），
       而终端不存在 → 上面那条「退回终端」实际是**退回虚空**：
       问话只写进日志、默认存，而**收尾卡一次都不出现** ——
       11 分钟精修全程屏上空的。这正是 `wrapup.py` 当初要消灭的那种「卡住不动」。
       → 没有终端时，关过窗也照样走 UI（`ask_save` 那边同步改了
         「已经关过的窗不算放弃」，两处缺一不可）。

    ⚠️ `terminal` 是个**注入的依赖**（默认才是真去问 `sys.stdin`）：不注入的话，
       这条判据的结果会跟着**跑测试的那个终端**变 —— 同一个缺陷 CI 上绿、本机红。
    """
    if not callable(getattr(ui, "ask_save", None)):
        return "terminal"
    if not ui_gone:
        return "ui"
    if terminal is None:
        terminal = _terminal_usable()
    return "terminal" if terminal else "ui"


def _ask_save_notes(n: int) -> bool:
    """结束时问是否存入 Obsidian。

    ⚠️ **超时 / Ctrl+C / 非交互环境一律默认保存** —— 会话文件已实时落盘,
    这一步只决定要不要复制进 Obsidian 库, **绝不能因为一次误按或走开丢掉整节课**。
    ⚠️ 2026-09-28 加的超时：原来是个裸 `input()`，作者走开时它**永远不返回** ——
       实测挂过 4 小时 12 分，而且主线程卡在这里 → 界面冻死、退不掉。"""
    ans = _input_timed(
        f"\n📝 本次共记录 {n} 句双语。存入 Obsidian 吗? [Y/n] ", ASK_TIMEOUT_S)
    if ans is None:
        echo(f"\n(没等到回应（{ASK_TIMEOUT_S:.0f} 秒）/ 非交互环境 → 默认存入 Obsidian)")
        return True
    return ans.lower() in ("", "y", "yes", "是", "好", "存")


#: ⭐ **进程级唯一一份上传队列**（2026-10-01 审查 F25）：`UploadQueue` 的 docstring
#: 写着「线程不安全，调用方自己串行化」，而启动补传与收尾入队原来是**两个实例**
#: —— 各自攥着一份内存副本，后写的 `_save()` 会把对方刚入的条目整份盖掉
#: （新一节静默不进队列）。收成一份实例 + `upload.UploadQueue` 内部把
#: enqueue/pump 的读改写串行化，就没有第二份副本可盖。
_upload_q_shared = None


def _shared_upload_queue():
    global _upload_q_shared
    if _upload_q_shared is None:
        import upload as _up
        import upload_endpoint as _ue
        _upload_q_shared = _up.UploadQueue(
            _up.make_http_send(_ue.endpoint(), _ue.token()))
    return _upload_q_shared


def start_upload(tester) -> dict:
    """把这一节排进上传队列，并**在后台线程里试一次**。返回 `{queued, pending}`。

    ⚠️⚠️ **绝不能阻塞收尾** —— 它跑在收尾那条路上，而那里本来就在等精修。
       → 排队只写一个小 JSON（落盘，快），真正的传输丢给后台线程。
    ⚠️ **上传失败不影响任何东西** —— 报告、包、音频都已经在盘上了。
       失败的条目留着，`next_at` 到点后由下一次启动再试（退避 1→120 min，14 天过期）。
    """
    if os.environ.get("CLASSLIVE_UPLOAD", "1") != "1":
        return {"queued": 0, "pending": 0, "off": True}
    files = tester.upload_files()
    if not files:
        return {"queued": 0, "pending": 0}
    q = _shared_upload_queue()
    n = q.enqueue(files, tester.stem.name)
    pending = q.pending
    th = None
    if n:
        th = threading.Thread(target=q.pump, kwargs={"max_items": 1}, daemon=True,
                              name="cl-upload")
        th.start()
    return {"queued": n, "pending": pending, "thread": th}


def summary_log_line(payload) -> str | None:
    """实时总结的 payload → 终端那一行；**不是正式章就返回 `None`**。**纯函数。**

    ⚠️ 它是**模块级**的，不是 `drain()` 里的内联代码 —— 这一条是**实测逼出来的**：
       收尾时 `finish()` 会定稿**最后一章**，而那时**主循环已经退出、没人在 `drain`**
       → 最后一章的标题**永远打不出来**（2026-09-30 实测：3 个正式章只打了 2 条）。
       ⭐ 所以这一行归**产出方**（`_summ_emit`）管，**不归消费方**（`drain`）。
    ⚠️ 只打**正式章**（`status == "final"`）—— 临时版每 4 分钟一刷，会刷屏。
    """
    p = payload if isinstance(payload, dict) else {}
    c = p.get("chapter") or {}
    if p.get("kind") != "chapter" or c.get("status") != "final":
        return None
    return f"📑 {c.get('title', '')}"


NO_CLOUD_ANSWER = ("⚠ 讲解需要云端引擎(DeepSeek): 本地的 1.7B 模型只会翻译, 没有讲解能力。"
                   "用 --engine auto/cloud 并配好 API key 后可用。")

# 「讲一下」按钮的固定问题(作者锁定: "讲清楚刚讲的这段")。
# 可以是常量 —— 转录底座由 answer_worker 快照后经 answer_user_content 附在同一
# 个 user turn 里, 问题本身不需要携带任何上下文。
ASK_QUESTION = "讲清楚刚讲的这段: 核心是什么、教授为什么现在要讲它。"


class EngineRouter:
    """翻译引擎路由: cloud 优先, 失败自动降级本地(断网兜底)。

    降级**不是永久的**: 一堂 2h 的课 Wi-Fi 抖一次就全程困在本地小模型上
    (实测一次瞬时 Errno 60 就触发过, 且本地回退质量明显更差)。
    降级后每 RETRY_PROBE_S 秒用 1-token 探针试云端, 通了自动切回。
    状态变化通过 notify(msg, warn) 广播(经 streamq 转主线程, 可进 UI)。"""

    RETRY_PROBE_S = 90.0

    def __init__(self, local, cloud, mode: str, notify=None):
        self._local = local
        self._cloud = cloud
        self._mode = mode
        self._notify = notify or (lambda msg, warn=False: None)
        self._use_cloud = bool(cloud) and mode in ("auto", "cloud")
        self._probe_lock = threading.Lock()   # 防降级→恢复→再降级重复起探针线程
        # 降级是一条**状态迁移**, 不是一次赋值。Phase 2 起有第二条并发路径会进来
        # (问答 worker 与常驻翻译 worker 同时在跑), 两个线程可能同时读到 True,
        # 各自广播一次"已降级本地" —— 用户看到两条重复通知, 且两次都去起探针。
        # 加锁把"判断 + 翻转"并成一步。只护这一处, _use_cloud 本身仍是普通 bool。
        self._state_lock = threading.Lock()

    def warmup(self) -> None:
        if self._mode == "local":
            self._local.warmup()
        elif self._mode == "cloud":
            pass                                  # 云端无需预热
        # auto: 不预热本地, 省内存/启动时间; 真降级时再懒加载

    def _fallback(self, e: Exception) -> None:
        with self._state_lock:                    # 见 __init__: 迁移只能发生一次
            if not self._use_cloud:
                return
            self._use_cloud = False
        # ⭐⭐ **广播要说实话**（2026-10-01 审查 F2）：本地模型是**可选**的，大多数人
        #    没装 —— 没装还说「已降级本地引擎」，用户会以为有兜底，实际每句都失败。
        #    `local_ready()` 只看在不在、不加载（桩/测试没有这个方法就按可用算）。
        try:
            local_ok = bool(self._local.local_ready())
        except Exception:                         # noqa: BLE001
            # 桩没有这个方法（AttributeError）/ 判据自己坏了 → 都按「可用」算
            local_ok = True
        if local_ok:
            self._notify(
                f"云端翻译失败({str(e)[:60]}); 已降级本地引擎, "
                f"每 {self.RETRY_PROBE_S:.0f}s 自动重试", warn=True)
        else:
            self._notify(
                f"云端翻译失败({str(e)[:60]}); 本地模型不可用（没装，或加载失败）"
                f"—— 只保留转录（跑 cl doctor 看看）。每 {self.RETRY_PROBE_S:.0f}s 自动重试云端",
                warn=True)
        self._start_probe()

    def _start_probe(self) -> None:
        if self._cloud is None:
            return
        if not self._probe_lock.acquire(blocking=False):
            return                                # 已有探针在跑
        def probe():
            try:
                while True:
                    time.sleep(self.RETRY_PROBE_S)
                    if self._use_cloud:           # 别处已恢复(理论不可达, 保险)
                        return
                    if self._cloud.probe():
                        with self._state_lock:
                            self._use_cloud = True
                        self._notify("云端翻译已恢复", warn=False)
                        return
            finally:
                self._probe_lock.release()
        threading.Thread(target=probe, daemon=True,
                         name="cloud-retry-probe").start()

    def fix_and_translate_stream(self, en, context, on_zh=None, on_en=None):
        if self._use_cloud:
            try:
                return self._cloud.fix_and_translate_stream(en, context, on_zh, on_en)
            except Exception as e:                # noqa: BLE001
                self._fallback(e)
        return self._local.fix_and_translate_stream(en, context, on_zh, on_en)

    def fix_stream(self, en, context, on_en=None) -> str:
        """只矫正英文(翻译关闭时)。走同一个模型与同一份上下文, 只是不要中文。"""
        if self._use_cloud:
            try:
                return self._cloud.fix_stream(en, context, on_en)
            except Exception as e:                # noqa: BLE001
                self._fallback(e)
        return self._local.fix_stream(en, context, on_en)

    def translate_draft(self, en, on_zh=None) -> str:
        if self._use_cloud:
            try:
                return self._cloud.translate_draft(en, on_zh)
            except Exception as e:                # noqa: BLE001
                self._fallback(e)
        return self._local.translate_draft(en, on_zh)

    def answer(self, question, transcript, history, on_delta=None,
               context: str = "") -> str:
        """按需讲解 / 追问。形状照抄 fix_stream: 云端优先, 异常降级。

        ⚠️ 降级目标**不是** `self._local.answer_stream(...)`: `translator.Translator`
        的方法到 `translate_draft` 就结束了, 本地根本没有 answer_stream, 那样写
        在 `--engine local` / 没配 key 时是 AttributeError —— 会把问答线程直接
        打死(而且是在 daemon 线程里, 表现成"点了没反应")。改成**显式守卫**,
        返回一句能直接上屏的说明。
        为什么不补一个本地实现: 1.7B 的翻译模型解释课堂内容只会编, 而且生成期间
        要抢 `translator.py` 那条本地全局锁, 会把正在进行的翻译整段卡住 ——
        宁可不做, 也不能给一个会撒谎的答案。
        """
        if self._use_cloud:
            try:
                return self._cloud.answer_stream(question, transcript, history,
                                                 on_delta, context=context)
            except Exception as e:                # noqa: BLE001
                # ⚠️ **刻意不走 `_fallback`**。`_fallback` 会把 `_use_cloud` 翻成
                # False —— 于是**一次纯问答的失败会把整节课的翻译也降级到本地**
                # (独立验证实测: 问答抛错后下一句翻译真的走了本地)。问答失败
                # (例如上下文超限)完全不能说明翻译链路有问题。
                # 而且 `_fallback` 的提示语写死是"云端翻译失败", 在这里是**误导**;
                # 它还会起一个 90s 探针线程去计费的 ping。翻译路径下一句自己会发现
                # 真问题并降级 —— 各管各的。
                return f"⚠ 讲解失败({str(e)[:60]})"
        return NO_CLOUD_ANSWER


class TerminalUI:
    # ⚠️ 这个类**不驱动 AppKit** —— `pump()` 是空操作。收尾循环靠它决定
    #    「pump 还是 sleep」：拿 `args.ui == "overlay"` 当判据是错的，
    #    因为 `_load_overlay` 失败时会**静默回退** `TerminalUI()`，那时 args 还写着
    #    overlay → 每轮都调一个空 pump、**一次 sleep 都没有 → 纯烧 CPU**。
    #    （2026-09-28 OCR 审计发现。）
    drives_appkit = False

    def __init__(self):
        self._zh = ""
        self._en = ""
        self._answer = ""

    def add_draft(self, en: str):
        echo(f"[{now()}] ▸ {en}")

    def draft_zh(self, zh: str):
        echo(f"[{now()}]   ↳ {zh}")

    def terms(self, hits):
        echo(f"[{now()}]   {format_gloss(hits)}")

    def stream_zh(self, delta: str):
        self._zh += delta

    def stream_en(self, delta: str):
        self._en += delta

    def finalize(self, en: str, zh: str):
        echo(f"[{now()}] ✅ {en}\n         🌐 {zh}")
        self._zh = self._en = ""

    def answer_delta(self, delta: str):
        # 只累加不上屏: 与 stream_zh 同规矩 —— 完整回答由 drain() 一次性 echo
        # (逐 delta 打印会和字幕行交错成一片糊)。
        self._answer += delta

    def answer_done(self, question: str, text: str):
        self._answer = ""

    def notice(self, msg: str, warn: bool = False):
        echo(("⚠ " if warn else "✅ ") + msg)

    def pump(self):
        pass

    # ---- 收尾（2026-09-28）----
    # ⚠️ 终端这条路**本来就是它的主场**：问话走 `_ask_save_notes`，进度与结果由
    #    `obsidian_writer` 自己 `print` 出来。所以后两个是**有意的空操作** ——
    #    接了反而会让同一行打两遍。它们存在只为让 `main` 不必区分两条 UI。
    def ask_save(self, n: int):
        return _ask_save_notes(n)

    def wrapup_progress(self, stage, done, total):
        pass

    def wrapup_begin(self, cancel):
        # 终端那条路没有可点的按钮 —— 「跳过精修」在那里就是 Ctrl+C，
        # 而 Ctrl+C 由 `run()` 的收尾循环接住并置同一个 `cancel`。
        pass

    def wrapup_done(self, ok: bool, msg: str):
        pass

    def wrapup_acknowledged(self) -> bool:
        # 终端那条路结论是直接 print 的，没有「看没看见」这个概念 —— 恒 True，
        # 于是 main 的「失败留住」循环一次都不转，与今天的行为一致。
        return True

    def wrapup_close(self):
        pass


class _Latest:
    """latest-wins 槽位: 草稿只保留最新一份, 旧的自然丢弃。"""

    def __init__(self):
        self._buf = None
        self._ev = threading.Event()
        self._lock = threading.Lock()

    def put(self, buf) -> None:
        with self._lock:
            self._buf = buf
        self._ev.set()

    def take(self, timeout: float):
        if not self._ev.wait(timeout):
            return None
        with self._lock:
            b, self._buf = self._buf, None
            self._ev.clear()
            return b


def _changelog_section(version: str) -> str:
    """取 CHANGELOG.md 里某个版本的正文（不含标题行）。取不到返回空串。"""
    try:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "CHANGELOG.md")
        text = open(p, encoding="utf-8").read()
    except Exception:                                     # noqa: BLE001
        return ""
    m = re.search(rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|\Z)",
                  text, re.S | re.M)
    return m.group(1) if m else ""


def _changelog_date(version: str) -> str:
    """版本日期（`## [1.2.0] - 2026-09-24` 里的那串）。取不到返回空串。"""
    try:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "CHANGELOG.md")
        text = open(p, encoding="utf-8").read()
    except Exception:                                     # noqa: BLE001
        return ""
    # ⚠️ 这里**不能**写成 rf"..."：`\d{4}` 里的 `{4}` 会被当成 f-string 表达式求值，
    # 模式会变成 `(\d4-\d2-\d2)` —— 编译通过、永不匹配、静默返回空串。踩过。
    m = re.search(r"^## \[" + re.escape(version)
                  + r"\][^\n]*?-\s*(\d{4}-\d{2}-\d{2})", text, re.M)
    return m.group(1) if m else ""


def _changelog_summary(version: str, max_lines: int = 6) -> str:
    """更新卡片上显示的那几行。

    写法参考 Keep a Changelog 与各家 "What's New" 的共识（2026-09-24 调研）：
      · **破坏性 / 要先做的**放最前；
      · **说影响，不说实现** —— "笔记写入可能丢数据" 而不是
        "save_to 改为原子写 (.tmp + os.replace)"；
      · **一条一事**，不要把无关改动捆一行；
      · 短、能扫，前几行就要给出重要信息。

    ⚠️ 为什么优先读「### 更新看点」而不是抓 `### Added`/`### Fixed`：
    那些小节是写给**维护者**的（有文件名、符号名、代码片段），卡片是给**用的人**看的。
    两个读者、两份文字。抓出来会出现 `self._model, self._tokenizer = load(...`
    这种屏上没人看得懂的行（实测踩过）。所以由作者在 CHANGELOG 里显式写一小节，
    没有该小节时才退化成抓取（老版本兼容）。
    """
    body = _changelog_section(version)
    if not body:
        return ""

    # ---- 优先：显式的「更新看点」小节 ----
    m = re.search(r"^###\s*更新看点[^\n]*\n(.*?)(?=^### |\Z)", body, re.S | re.M)
    if m:
        out = []
        for line in m.group(1).splitlines():
            raw = line.rstrip()
            s = raw.strip()
            # ⚠️ **容错**：不要求每行都以 `- ` 开头。踩过 —— 作者手写时漏了 `- `，
            # 那几行就被**静默丢掉**，卡片上不出现，而且没有任何提示。
            # 现在除了空行/小标题/引用/代码块，其余都当条目。
            if not s or s.startswith(("#", ">", "|", "```")):
                continue
            # ⚠️⚠️ **缩进的行是上一条的续行，不是新条目。**
            #   卡片按**物理行**取，而看点写得长时必然折行（源码里缩进两格）——
            #   当成新条目的话，**一条看点会裂成两条**，而且合并多版本时后半截会被
            #   冠上**别的版本号**。3.7.0 那版实测就是这个症状：
            #       `3.6.5 · （⚠️ 现在只认 Markdown，PDF / PPTX 的自动转换还在做）`
            #   —— 那其实是 3.7.0 某条的后半截。（2026-09-28 写 3.8.0 时才发现。）
            continued = bool(out) and (len(raw) - len(raw.lstrip()) > 0)
            # ⚠️⚠️ **项目符号后面必须有空白**（`\s+` 不是 `\s*`，2026-09-30 修）。
            #    原来写 `\s*`：**续行**若以 `**粗体**` 开头，第一个 `*` 被当成项目符号
            #    吃掉，剩下的 `*粗体` 里那个孤星**再也没人剥** —— 卡片上就显示成
            #    「重活被 *默默跳过」。续行本身不该有项目符号，所以「要求空白」正是
            #    它和真项目符号的区别。
            s = re.sub(r"^[-*]\s+", "", s)                # 有就吃掉，没有也认
            s = re.sub(r"\*\*|`", "", s).strip()
            if not s:
                continue
            if continued:
                out[-1] = f"{out[-1]} {s}".strip()
            else:
                out.append(s)
            if len(out) >= max_lines:
                break
        if out:
            return "\n".join(out)

    # ---- 兜底：抓每个 ### 小节的第一条（给没有「更新看点」的老版本）----
    out, cur = [], None
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("### ") and "更新看点" not in s:
            cur = s[4:].strip()
        elif s.startswith(("- ", "* ")) and cur:
            b = re.sub(r"\*\*|`", "", s[2:]).strip()
            b = re.split(r"[。；;]", b)[0]
            if len(b) > 58:                               # 掐在标点处，不切字中间
                cut = max(b.rfind("，", 0, 58), b.rfind("、", 0, 58),
                          b.rfind(" ", 0, 58))
                b = (b[:cut] if cut > 20 else b[:57]) + "…"
            out.append(f"{cur} · {b}")
            cur = None                                    # 每节只取第一条
        if len(out) >= max_lines:
            break
    out.sort(key=lambda x: 0 if ("Breaking" in x or "破坏" in x) else 1)
    return "\n".join(out)


def _reorder_log(text: str) -> str:
    """把每个版本里的 `### 更新看点` 提到最前、`### 升级须知` 紧随其后。

    为什么在**代码里**做、而不是靠作者手写顺序：手写顺序会漂 —— 有的版本先写
    升级须知、有的先写更新看点，卡片上看到的东西就忽前忽后。这里统一，
    作者写哪样都行。

    每个版本内按 `### ` 切块，按 (`更新看点`=0 / `升级须知`=1 / 其余=2) **稳定排序**，
    所以其余小节仍保持作者写的相对顺序。
    """
    def rank(title: str) -> int:
        if "更新看点" in title:
            return 0
        if "升级须知" in title:
            return 1
        return 2

    out = []
    for part in re.split(r"(?m)^(?=## \[)", text):
        m = re.match(r"(## \[[^\n]*\n)(.*)\Z", part, re.S)
        if not m:                          # 不是版本段（比如空串）→ 原样
            out.append(part)
            continue
        head, body = m.group(1), m.group(2)
        chunks = re.split(r"(?m)^(?=### )", body)
        pre = "" if chunks and chunks[0].startswith("### ") else chunks.pop(0)
        idx = list(enumerate(chunks))
        idx.sort(key=lambda t: (rank(t[1].split("\n", 1)[0]), t[0]))   # 稳定
        out.append(head + pre + "".join(c for _, c in idx))
    return "".join(out)


def _changelog_full() -> str:
    """CHANGELOG.md 全文 —— 卡片里的可滚动更新日志用。取不到返回空串。

    ⚠️ **从第一个 `## [` 开始截** —— 文件开头是"本文件遵循什么格式""每个版本要怎么写
    `### 更新看点`"那类**维护者约定**，不是更新内容。整段塞进卡片会让人看到
    "每个版本请写一节…"这种跟自己无关的话（实测渲染出来过）。
    """
    try:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "CHANGELOG.md")
        text = open(p, encoding="utf-8").read()
    except Exception:                                     # noqa: BLE001
        return ""
    m = re.search(r"^## \[", text, re.M)
    return _reorder_log(text[m.start():]) if m else text


def _changelog_versions(text: str | None = None) -> list[str]:
    """CHANGELOG.md 里所有版本号，**新到旧**（文件里的出现顺序）。

    实现委托给 `update.versions_in` —— 更新决策的解析**只有一份**，
    否则卡片显示和自动更新判断会各说各话。
    """
    import update as _u
    if text is None:
        try:
            p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "CHANGELOG.md")
            text = open(p, encoding="utf-8").read()
        except Exception:                                 # noqa: BLE001
            return []
    return _u.versions_in(text)


def _update_mode_in(text: str, version: str) -> str:
    """该版本能否后台自动应用。委托 `update.update_mode_in`（见那里的说明）。"""
    import update as _u
    return _u.update_mode_in(text, version)


def _auto_update_on_exit() -> None:
    """退出时**在独立进程里**检查/应用小更新。父进程立刻返回，**零退出延迟**。

    为什么必须是独立进程（2026-09-24 调研，见 `docs/PLAN-update-mechanism.md` §7）：
      · Mozilla Silent Update 把 **"We don't want to delay shutdown"** 列为设计目标；
      · Firefox 的后台更新器本身就是独立进程，且"主进程在跑时它直接退出"；
      · atomic（Go 工具自更新）的原话是 **"the parent process never touches the network"**。
    同步跑的话，断网时用户要干等超时才关得掉 —— 这是**上课录课**用的工具，不能这样。

    "环境不全"全都在子进程里静默降级（没 git / 没 upstream / remote 名不同 /
    浅克隆 / 目录不可写…），父进程这一侧只有一个 `Popen`，失败就算了。
    """
    if os.environ.get("CLASSLIVE_NO_AUTO_UPDATE"):
        return
    try:
        import update as _u
        _u.spawn_auto_update()
    except Exception:                                     # noqa: BLE001
        pass                                              # 绝不因为自动更新影响退出


def _whatsnew_body(seen: str, cur: str, max_lines: int = 7) -> str:
    """该给用户看的内容 —— **`(seen, cur]` 这个区间**，不含比 `cur` 更新的版本。

    ⚠️ **必须跨版本合并** —— 有人会从 3.4.0 直接跳到 3.6.0。只看 `cur` 的话，
    他会**永远看不到 3.5.0 那条破坏性变更**（"两个 ASR 模型改为必装、
    第一次要手动 git pull"）—— 恰恰是跳过版本的人最需要看到的那一条。

    ⚠️ **上界必须是 `cur`，不能"从最新往下数到 seen"**。第一版就是那么写的
    （`vers[:vers.index(seen)]`），结果是：**只要 CHANGELOG 里有比 `cur` 更新的
    版本，它就会被报进来** —— 用户会看到自己**还没装的版本**的说明。
    (2026-09-25 隔离测试发现：`(3.6.4, 3.6.5)` 报出了 `3.7.0` 的看点。)

    多版本时每行前面标版本号；带 ⚠️ 的（破坏性/要先做的）排最前。
    """
    vers = _changelog_versions()          # 新到旧
    if not vers or cur not in vers:
        return _changelog_summary(cur, max_lines)
    lo = vers.index(cur)                  # cur 的位置（越小越新）
    # ⚠️ `seen` 不在库里时**收敛到只报当前版**（`lo + 1`），不能退化成 `len(vers)`——
    # 那会把 cur 及更旧的版本全算成"看点"（2026-09-25 ocr review 发现；
    # 旧实现在这里本来有守卫，本次改动删掉了）。
    hi = vers.index(seen) if seen in vers else lo + 1
    todo = vers[lo:hi]                    # ⚠️ 只取 (seen, cur]；比 cur 新的在 vers[:lo]
    if not todo:
        return ""
    if len(todo) == 1:
        return _changelog_summary(todo[0], max_lines)
    rows = [(v, ln)
            for v in todo
            for ln in _changelog_summary(v, max_lines).splitlines()]
    # 稳定排序: 带 ⚠️ / Breaking 的提到最前, 其余保持"新版本在前"
    rows.sort(key=lambda r: 0 if ("⚠" in r[1] or "Breaking" in r[1]) else 1)
    return "\n".join(f"{v} · {ln}" for v, ln in rows[:max_lines])


def _disp_w(s: str) -> int:
    """终端里的**显示宽度** —— 中文/全角算 2 列。

    ⚠️ 直接用 `len()` 会让框的右边框对不齐: 一个汉字在终端占两列，但只算 1 个字符。
    (2026-09-24 实测发现。)
    """
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in s)


def _whatsnew_payload() -> dict | None:
    """该不该弹「本次更新」卡片；该的话返回 payload dict，否则 None。

    ⚠️ **只判断、不弹** —— 弹的动作交给 UI 层：
      · 悬浮窗模式 → `overlay.show()` 里用**非模态毛玻璃卡片**（见 whatsnew.py）；
      · 终端模式   → 印一个字符框。
    为什么不在这里弹：① 这里是 `run()` 第一行，那时 app 的 activation policy 还是
    `Regular`（实测），NSAlert 会带 Python 的图标 + 在 Dock 里冒出来；② 模态框会
    卡住启动 —— 而这个工具是**上课录课**用的。

    ## 弹窗节奏（2026-09-25 按作者要求重定，逐条都有实测/调研依据）

    **不勾「本版本不再提示」→ 每次启动都弹**，直到勾了它。
    勾了 → 这个版本彻底不弹；出了新版本 → 重新开始弹。如此重复。

    推断「上次看过哪个版本」= `max(.update-seen, .update-skip)`：
      · `.update-seen`  —— 每次弹完都写当前版本（只用来算"从哪报起"）
      · `.update-skip`  —— 勾了才写，值是**版本号**（不是 `1`），所以新版本自动失效

    ⚠️ **三道不能省的判据**（每一条都对应一个实测出来的坏行为）：

    ① **比大小，不是比不等**（`cur <= newest_seen` 就不弹）。
       业界用 `lastVersion < version`；Electron 的 `autoUpdater` 默认
       `allowAnyVersion=false`（默认不允许降级）。**用 `!=` 会让降级也弹。**

    ② **首次安装不弹**（`newest_seen` 为空）。那不是"更新"是全新安装，
       对着一条长长的 changelog 弹卡片没有意义。

    ③ **看点为空就不弹**（`summary` 为空 → 返回 None）。
       降级且 CHANGELOG 里没有该版本的看点时会走到这里 ——
       **实测过：会弹出一张要点空白的卡片**（`_whatsnew_body` 返回 `''`）。

    ⚠️ **写在 card 之前**：`.update-seen` 在**构造 payload 时**就写，
    所以即使 UI 层构造失败，也不会每次启动重复算 —— 但这个不影响"每次弹"，
    因为弹不弹由 `skip` 决定，不看 `seen`。
    """
    if os.environ.get("CLASSLIVE_NO_UPDATE_CHECK"):
        return None
    try:
        root = os.path.dirname(os.path.abspath(__file__))
        ver_file = os.path.join(root, "VERSION")
        cur = (open(ver_file, encoding="utf-8").read().strip()
               if os.path.exists(ver_file) else "?")
        if cur == "?":
            return None

        def _read(name: str) -> str:
            f = os.path.join(root, name)
            return (open(f, encoding="utf-8").read().strip()
                    if os.path.exists(f) else "")

        seen, skip = _read(".update-seen"), _read(".update-skip")

        # ---- 弹不弹：四个"不弹"的理由，其余都弹 ----
        # ⚠️ `seen` **只用来判降级**，绝不用来判"看过了" —— 否则弹完写回 seen，
        #    下次就变成"已看过" -> **把"每次启动都弹"挡死**（第一版就是这么错的）。
        if skip == cur:                                   # ① 勾了「本版本不再提示」
            return None
        if not seen and not skip:                         # ② 首次安装
            # ⚠️ **必须在这里把 `seen` 种下去**，否则全新 clone 的用户**永远弹不出来**：
            # `.update-seen` 全仓库只有本函数会创建，而这个分支直接 return 了 ->
            # 文件永远不存在 -> 每次启动都命中"首次安装"（2026-09-25 ocr review 发现，
            # 是本次改动引入的回归；旧实现在 return 之前**无条件**写这个文件）。
            # 补种不影响"每次启动都弹"——弹不弹只看 skip 与降级。
            with open(os.path.join(root, ".update-seen"), "w", encoding="utf-8") as f:
                f.write(cur)
            return None
        if seen and _ver_key(cur) < _ver_key(seen):       # ③ 降级
            return None
        if skip and _ver_key(cur) <= _ver_key(skip):      # ④ 勾的是更新的版本
            return None

        # ---- 起点：报"从哪一版到这一版" ----
        # 优先用 `skip`（他明确表示看过那个版本）；没有就用 **库里 cur 的前一版**。
        # ⚠️ 不能用 `seen` 当起点：它每次弹完都被改写成 cur，起点会跟着往后跑，
        #    第二次弹就变成"从 cur 到 cur" -> 空内容。用"前一版"则**每次都一样**。
        # ⚠️ 候选必须**在 CHANGELOG 里**且**比 cur 旧**，两个条件缺一不可：
        #   · 不在库里（老格式的 `.update-skip = "1"`、被手工改过的 seen、
        #     或 CHANGELOG 里已删掉的版本号）会让 `_whatsnew_body` 的上界退化成
        #     「倒数到最旧」-> 卡片混进一堆与本机版本无关的旧条目。
        #   · 比 cur 新不是"没看过"。
        # 取 **min**（较旧的那个）：`skip` 是用户明说看过的、`seen` 是自动记的，
        # 宁可他多看几条，也不要漏掉破坏性变更。
        _vers = _changelog_versions()
        cand = [v for v in (skip, seen)
                if v in _vers and _ver_key(v) < _ver_key(cur)]
        start = min(cand, key=_ver_key) if cand else _prev_version(cur)
        summary = _whatsnew_body(start, cur)
        if not (summary or "").strip():                   # ⑤ 看点为空（防空白卡片）
            return None
        payload = {"version": cur, "date": _changelog_date(cur),
                   "summary": summary, "log": _changelog_full()}
        with open(os.path.join(root, ".update-seen"), "w", encoding="utf-8") as f:
            f.write(cur)
        return payload
    except Exception:                                     # noqa: BLE001
        return None


def _prev_version(cur: str) -> str:
    """CHANGELOG 里 `cur` 的**前一版**（比它旧的那个）。取不到就返回 `cur` 自己。

    用途见 `_whatsnew_payload`：弹窗的起点必须是**稳定的**，不能随
    `.update-seen` 往后跑。
    """
    vers = _changelog_versions()
    if cur in vers:
        i = vers.index(cur)
        if i + 1 < len(vers):
            return vers[i + 1]
    return cur


def _ver_key(v: str) -> tuple:
    """版本号排序键。`3.10.0` 必须排在 `3.9.0` 之后 —— 纯字符串比不出来。

    非数字段退化成 0，所以 `3.6.3` vs `3.6.3-rc1` 这类也**不会抛异常**。
    """
    parts = []
    for seg in (v or "").split("."):
        num = ""
        for ch in seg:
            if ch.isdigit():
                num += ch
            else:
                break
        parts.append(int(num) if num else 0)
    return tuple(parts)


def _print_whatsnew_box(version: str, date: str, summary: str,
                        log: str = "") -> None:
    """终端模式下的退化：印一个字符框。宽度按**显示宽度**算（中文占两列）。

    参数名与 `_whatsnew_payload()` 返回的 dict 键一致，调用方可以直接 `**payload`。
    `log`（全量日志）在终端里印不下，忽略 —— 终端没有滚动区。
    """
    title = f"ClassLive 已更新到 {version}" + (f" · {date}" if date else "")
    lines = [x for x in (summary or "").splitlines() if x.strip()] or ["见 CHANGELOG.md"]
    lines.append("")
    lines.append("完整说明见 CHANGELOG.md")
    w = max([_disp_w(title)] + [_disp_w(x) for x in lines]) + 2
    echo("╭─ " + title + " " + "─" * max(0, w - _disp_w(title) - 3) + "╮")
    for x in lines:
        echo("│ " + x + " " * (w - _disp_w(x) - 1) + "│")
    echo("╰" + "─" * (w + 1) + "╯")


def _show_whats_new(version: str, date: str, summary: str,
                    log: str = "") -> None:
    """[已弃用] 原来的 NSAlert 弹框。保留仅为兼容，新路径走 whatsnew.py 的非模态卡片。"""
    _print_whatsnew_box(version, date, summary, log)


def _maybe_notice_update() -> dict | None:
    """**一次性**的更新提示: 检测到落后于远程就打印一行, 之后不再打扰。

    **返回**：该弹的「本次更新」卡片 payload（`_whatsnew_payload()` 的产物），
    没有就 `None`。⚠️ 调用方**直接拿它当 dict 用**（`**payload`）——
    标注原来是 `-> None`，与四个 `return payload` 对不上（2026-09-30 pyright 抓到）。

    ⚠️ 三条自我约束(理由见 README「升级到新版本」):
      1. **默认静默** —— 断网/代理/限流/没有 git 一律什么都不说, 绝不阻塞启动;
      2. **每个版本只提示一次** —— 提示过就写 `.update-notice` 记下当前版本号,
         所以"用户已经看到了但还没升"不会每次上课都被念一遍;
      3. **不自动升级** —— `git pull` 由用户自己敲。启动时改用户的工作区
         是同一条硬规矩("不擅自改环境")的越界。

    为什么可以联网: 只在本地问 `git`(远端 ref 已经在本地仓库里), **不发 HTTP**。
    所以既不碰 GitHub 的未认证限流(60/h), 也不需要用户登录。
    """
    if os.environ.get("CLASSLIVE_NO_UPDATE_CHECK"):
        return
    try:
        # ---- ① 该不该弹「本次更新」卡片（只判断，弹的动作交给 UI 层）----
        payload = _whatsnew_payload()

        # ---- ② 落后远程 -> 一行提示（需要 git）----
        root = os.path.dirname(os.path.abspath(__file__))
        cur = (open(os.path.join(root, "VERSION"), encoding="utf-8").read().strip()
               if os.path.exists(os.path.join(root, "VERSION")) else "?")
        if not os.path.isdir(os.path.join(root, ".git")):
            return payload
        import subprocess
        behind = subprocess.run(["git", "rev-list", "--count", "HEAD..@{u}"],
                                cwd=root, capture_output=True, text=True,
                                timeout=3).stdout.strip()
        if not behind.isdigit() or int(behind) == 0:
            return payload
        stamp = os.path.join(root, ".update-notice")
        if os.path.exists(stamp) and open(stamp, encoding="utf-8").read().strip() == cur:
            return payload                          # 这个版本已经提示过了
        echo(f"↑ 有新版本（本地 {cur}，远程领先 {behind} 个提交）"
             f"—— 升级: cl update（变更清单见 CHANGELOG.md）")
        with open(stamp, "w", encoding="utf-8") as f:
            f.write(cur)
        return payload
    except Exception:                               # noqa: BLE001
        return None                                 # 提示而已, 任何失败都静默


def run(args) -> None:
    # ---- 单实例锁 ----
    # ⚠️ 必须是 run() 的**第一件事**：抢在开音频设备、弹更新卡片、加载模型之前 ——
    #    否则第二个实例已经抢走了麦克风、已经烧了 API 调用，再说"你重复了"就晚了。
    #    ⚠️ `_lock` 必须一直活到 run() 结束 —— 文件对象一被回收，锁就释放了。
    #    理由（为什么用 flock 不用 pidfile / 为什么 .app 那条路不靠它）见 instance_lock.py。
    # ⚠️ 先 probe 再 acquire（2026-10-01 审查 F8，同 `prep.prepare` 的先例）：
    #    `acquire()` 把「已被占用」和「连锁文件都建不出来」**压成同一个返回**
    #    （instance_lock.py 自己写明），照它报「已经有一个在跑」会在磁盘满/权限时
    #    **误诊** —— 而那时用户一个实例都没在跑、却起不来。
    _state0, _holder0 = instance_lock.probe()
    if _state0 == "held":
        echo(f"⚠ {instance_lock.describe_holder(_holder0)}。")
        echo("   同时跑两个会抢同一个麦克风、叠两个悬浮窗、翻译费用也翻倍。")
        echo("   要停掉那一个：点它悬浮窗右上角的 ✕，或在它的终端按 Ctrl+C。")
        return
    _instance_lock, _holder = instance_lock.acquire()
    if _instance_lock is None:
        if _holder is None:
            # probe 刚说没被占、acquire 却拿不到且读不到占用者 = **锁文件建不出来**。
            # 按仓库既有取向 fail-open：出声放行，不拦上课（同 prep 那条）。
            echo("⚠ 单实例锁建不出来（磁盘满 / 权限？）—— 这次不拦你，照常上课。")
        else:
            # 极端竞态：probe 与 acquire 之间被另一个实例抢了 —— 按「被占用」处理
            echo(f"⚠ {instance_lock.describe_holder(_holder)}。")
            echo("   同时跑两个会抢同一个麦克风、叠两个悬浮窗、翻译费用也翻倍。")
            echo("   要停掉那一个：点它悬浮窗右上角的 ✕，或在它的终端按 Ctrl+C。")
            return

    # ---- 麦克风权限：三条路径都要说人话 ----
    # ⚠️ 为什么必须做：Apple 原文 —— 被拒时**录音里只有静音**（不是报错、不是崩溃）。
    #    不做这个判断的话，程序看起来一切正常、字幕一直空着，用户完全不知道为什么。
    #    完整理由与实测见 docs/PLAN-p1-app-launcher.md §3.7。
    # ⚠️ `cl file` 放录音不碰麦克风，不能被权限拦住（那正是课上出问题时复现用的手段）。
    if notice.needs_mic(args.source):
        _perm = notice.mic_permission()
        if _perm == "denied":
            notice.alert(
                "ClassLive 拿不到麦克风",
                "之前你点了「不允许」，macOS 就不会再自动问了。\n"
                "要去「系统设置 → 隐私与安全性 → 麦克风」里把 ClassLive 打开。",
                buttons=("知道了",), url=notice.SYSTEM_SETTINGS_MIC)
            echo("   （打开之后重新启动 ClassLive。）")
            return
        if _perm == "restricted":
            notice.alert(
                "这台机器不允许使用麦克风",
                "麦克风被系统策略限制住了（家长控制 / 描述文件 / MDM）。\n"
                "要找管这台机器的人，或者换一台设备。")
            return
        if _perm == "notDetermined":
            # 预热：系统框一冒出来就是「ClassLive.app 想要访问麦克风」，
            # 对第一次用的人毫无预警 —— 先说一句它要干嘛、为什么需要。
            notice.alert(
                "ClassLive 要申请麦克风",
                "接下来系统会弹一个授权框，请点「允许」。\n\n"
                "没有麦克风权限的话，ClassLive 录到的会是一片安静 —— "
                "程序看着在跑，但字幕一直是空的。")

    # 该不该弹「本次更新」卡片 —— 只判断，不弹。弹的动作交给 UI 层：
    # 悬浮窗模式在 overlay.show() 里用非模态毛玻璃卡片（那时 activation policy
    # 已经是 Accessory，不会带 Python 图标，也不阻塞）；终端模式印字符框。
    whatsnew = _maybe_notice_update()
    # 音源最先打开: 失败(没麦/没 BlackHole/坏文件)在这里就给友好提示退出,
    # 不再走完 ASR/LLM 加载 + 悬浮窗后才崩出一个裸 traceback。
    # ⚠️ 这次只是**试开**: 验证完立刻关。句柄留着不关的话, mic 的 InputStream
    #    已经 start() 了, 下面会再开一个 —— 设备被占 + 句柄泄漏两头不讨好。
    try:
        probe_src = load_source(args.source, args.path, args.speed)
    except Exception as e:                       # noqa: BLE001
        # ⚠️ 走 notice.alert 而不是 echo：`.app` 双击启动时没有终端，
        #    echo 出去的东西**没人看得见** —— 用户看到的是"双击了、什么都没发生"。
        #    （错误文本本身是好的，BlackHole 那条甚至带了 brew 安装命令。）
        notice.alert(f"无法打开音源（{args.source}）", f"{e}")
        return
    try:
        probe_src.close()
    except Exception:                            # noqa: BLE001
        pass
    # ⚠️ 释放引用（2026-10-01 审查 F20）：块作用域不存在，`probe_src` 原来会一直
    #    活到 `run()` 结束 —— 整段音频样本在内存里停着（`cl file` 回放时是两份：
    #    试开这份 + 真跑那份）。置 None 让 GC 立刻收掉试开的那一份。
    #    ⚠️ 「ffmpeg 解码两遍」留着 —— 试开的意义就是**先验证再承诺**，
    #       复用同一句柄会在别处改动解码路径时把验证悄悄架空。
    probe_src = None

    # 两个 ASR 模型都是**必需**的: 草稿/兜底用 Parakeet, 定稿用 Whisper。
    # 缺任何一个都在这里说清楚然后退出 —— 不在课上静默降质。
    try:
        asr = load_asr(args.model_dir)
        asr_final = load_final_asr(args.final_model_dir)
    except Exception as e:                       # noqa: BLE001
        notice.alert("模型没装好",
                     f"{e}\n\n装好再跑 —— `cl doctor` 会列出缺哪个、该跑哪条命令。")
        return
    # ⭐⭐ 转录里挖出来的词 → 术语**候选池**（2026-09-29 加）。
    #    ⚠️⚠️ **只进候选池，不进 `course_terms`（那档是每句全量注入）** ——
    #       实测：池子 +25 个转录词，注入结果变了 **63–74%** 的句子，
    #       而**手写术语只被挤掉 3–4%**（名额还有余量，所以 `MAX_DYNAMIC_TERMS` 不用动）。
    #       反过来，把词**写进术语表文件**会让术语块在 120 条时占整条 prompt 的 60%。
    #    ⚠️ **fail-soft**：拿不到就空表 —— 它只是锦上添花，绝不能挡住开课。
    #    ⚠️ `known` 必须传**全部课号**：打分用跨课 IDF，只传当前一门会改变语义。
    #       实测 0.07s（作者的 6 门课）→ 启动期一次性算，不在热路径上。
    #    ⚠️⚠️ **课号必须先归一**（2026-09-29 OCR 指出）：`args.course` 可能是
    #       **短代号**（`10740`）或带课型后缀（`ECON10070 TUT`），而 `list_courses()`
    #       返回**规范名**。原来 `not in names 就 append` → 同一门课在 `known` 里
    #       占两项 → **跨课 IDF 的语义被改**（正是上面那句注释要防的），
    #       而且 `_got.get(args.course)` 用**另一个写法**去取 → **取不到**，
    #       这个功能对短代号用户**静默什么都不做**（实测 `10740` 那份是 0 个词）。
    extra_terms: list = []
    if args.course:
        try:
            import corpus as _cp
            import courses as _cs
            import obsidian_writer as _ow
            _canon = _cs.canonical_course(args.glossary, args.course)
            _names = list(_cs.list_courses(args.glossary, state_root=None))
            if _canon in _names:
                _got, _deg = _cp.keywords(_names, sessions_dir=_ow.SESSIONS)
                extra_terms = list(_got.get(_canon, []))
                if _canon in _deg:
                    # ⚠️⚠️ **降级也要出声**（2026-09-29 OCR 指出）：`corpus` 是
                    #    **故意**把「拿不到词表的课 + 原因」一起报出来的
                    #    （`corpus.py` 模块头：「静默的空表是这里最坏的失败模式」）。
                    #    原来只在 `extra_terms` 非空时才打 —— 于是**新课本该降级**的
                    #    那一刻（正是作者要"如实说"的那档）一个字都不打。
                    echo(f"📚 {_canon} 这次没有转录词表（{_deg[_canon]}）"
                         f" —— 术语召回只用手写术语表")
                elif extra_terms:
                    echo(f"📚 转录词表：{len(extra_terms)} 个词进候选池")
            elif args.course != _canon:
                echo(f"⚠ 课号 {args.course!r} 归一成了 {_canon!r}，但它不在课程清单里"
                     f" —— 不加转录词")
        except Exception as _e:                       # noqa: BLE001
            print(f"⚠ 转录词表拿不到（只影响术语召回）：{type(_e).__name__}: {_e}")

    local_tr = load_translator(args.llm, args.glossary, args.context,
                               course=args.course, extra_terms=extra_terms)
    cloud_tr = None
    api_key_val = load_api_key(args.api_key)
    if args.engine in ("auto", "cloud"):
        if api_key_val:
            cloud_tr = load_cloud_translator(api_key_val, args.cloud_model,
                                             args.glossary, args.context,
                                             course=args.course,
                                             extra_terms=extra_terms,
                                             ctx_chunk=args.cloud_context)
            echo(f"☁ 引擎: {args.engine} (云端 {args.cloud_model})")
        else:
            echo("⚠ 未找到 DeepSeek API key(--api-key / DEEPSEEK_API_KEY / "
                 "~/.classlive/credentials); 回退本地引擎")
    else:
        echo(f"💻 引擎: local ({args.llm})")

    running = threading.Event()
    running.set()
    # ✕ / 菜单栏「退出」请求停机。**必须与 running 分开 —— 这是个真 bug 的修法**:
    # 原来 ✕ 回调直接 running.clear(), 而收尾要先 finalq.put(_QUIT) 交给
    # final_worker 让它收摊; worker 的循环判据正是 running, 提前清掉 = 没人取那个
    # 哨兵 -> finalq 永远非空 -> all_settled() 永远不成立 -> **每次点 ✕ 都卡满
    # 15s deadline**(独立验证实测 15.02s, 而且**完全不提问也一样**)。
    # 现在 ✕ 只置这个标志(主循环据此退出), running 留到收尾真正结束才清。
    stopping = threading.Event()
    trans = {"mode": "both"}                            # 🌐 三档: both 双语 / en 只英·校 / raw 纯转录
    # ⚠️ 库路径在这里**收口**（`resolve_vault` 是唯一定义点）：`--vault` → `$OBSIDIAN_VAULT`
    #    → 上次用过的。**没有兜底目录** —— 拿不到就不写 Obsidian，绝不凭空造一个。
    #    拿到了就**记下来**：双击 `.app` 起的那条路没有 shell 环境变量，不记下次还是找不到。
    vault = resolve_vault(args.vault)
    if vault:
        remember_vault(vault)
    elif args.save_notes != "no":
        print("⚠ 没设 Obsidian 库路径(设上 OBSIDIAN_VAULT, 或用 --vault 指定) —— "
              "这次只写 sessions/, 不写 Obsidian 笔记")

    # 🎯 Jev 的重点句（2026-09-29）—— **收尾那一步**才跑，而且是 fail-soft。
    # ⚠️⚠️ **纯转录档一个模型请求都不发** —— 仓库的硬契约（`DESIGN.md:187`：
    #    「纯转录 | 一个模型请求都不发」）。所以挡在**发请求之前**。
    # ⚠️ 没配 token → 空表（= 功能关着，不是错误）。同 `polish` 的 fail-soft。
    # ⚠️ 同步跑，一节课实测约 6 秒 —— 收尾可以接受（那一步本来就在等精修）。
    def _keypoints_for(sents):
        if args.save_notes == "no" or trans.get("mode") == "raw":
            return []
        try:
            import keypoints as _kp
            return _kp.pick(sents)
        except Exception:                                     # noqa: BLE001
            return []

    writer = ObsidianWriter(vault, args.course, mode=args.save_notes,
                            api_key=api_key_val, model=args.cloud_model,
                            glossary_path=args.glossary,
                            polish=args.polish != "off",
                            polish_model=args.polish_model or None,
                            keypoints_fn=_keypoints_for)
    notes = TermNotes()                                 # 术语通俗解析(查表)

    # 测试模式: 采一份完整报告 + 留音频。**任何采集失败都不能影响上课** ——
    # ⭐ 开机扫**上次没收尾**的测试模式（`.pending` 标记）——
    #    ⚠️ **在 `if args.test_mode` 外面**：那节课可能是崩掉的，而这次未必开测试模式。
    #    ⚠️ 它只 glob 一个后缀、且失败全吞 —— 绝不能因为清理上一节的残局挡住这一节开课。
    try:
        import upload as _upmod
        import obsidian_writer as _owmod
        import upload_endpoint as _uemod
        _r = _upmod.recover_pending(
            _owmod.SESSIONS, _upmod.make_http_send(_uemod.endpoint(), _uemod.token()))
        if _r.get("queued"):
            echo(f"📤 发现上次没收尾的测试课 {_r['found']} 节"
                 f" —— 已把已录到的部分排进上传队列（带 crash_recovered 标记）")
        # ⭐ **启动时把积压的队列推一把** —— 这才是「攒着下次发」的正主。
        #    ⚠️ 上一次收尾时那个 daemon 线程多半被进程退出杀了（实测：
        #       报告传上去了、9 MB 的音频没传完）→ 不在这里补一次，它就**永远传不完**。
        #    ⚠️ 丢后台线程，**不阻塞启动**。
        _q = _shared_upload_queue()          # ⚠️ 与收尾那条路**同一个实例**（审查 F25）
        if _q.pending:
            _pn = _q.pending
            threading.Thread(target=lambda: _q.pump(max_items=_pn), daemon=True,
                             name="cl-upload-startup").start()
            echo(f"📤 待上传 {_pn} 节 —— 后台补传中")
    except Exception:                                     # noqa: BLE001
        pass

    # TestSession 的所有 note_* 都自带 try/except(见 testmode.py)。
    tester = None
    if args.test_mode:
        # ⭐⭐ **2026-09-30 又翻了一次默认值**（作者拍板）：**测试模式下默认录**。
        #    ⚠️ 与 2026-09-28 那次**不冲突** —— 那次翻的是**正常上课**那条路的默认，
        #       而这条路（`args.test_mode` 为真）**当时还没有**。正常 `cl` 仍然不录。
        #    ⚠️ **翻了之后披露更要紧**：`cl test` 一开就录 → 得**开课前**跟同学说。
        _rec = (args.record_audio if args.record_audio is not None
                else True)
        tester = TestSession(getattr(writer, "session_path", None),
                             record_audio=_rec)
        echo(f"🧪 测试模式: 报告将写到 {tester.stem}.report.json")
        # ⚠️ 提醒**放在启动时**：收尾才说就晚了，那时整节课已经录完。
        #    （2026-09-30 起测试模式下默认录 —— 见上面 `_rec` 那段。）
        if tester.record_audio:
            echo("   ⚠️ 会录制**课堂音频**(分段 Opus, 约 9MB/50 分钟), 收尾打成一个 zip。")
            echo("      音频与逐字转录可能含**其他同学的声音** —— 发出去前请自己确认。")
        else:
            echo("   ℹ️ 只采指标, 不留音频（要留: 加 `--record-audio`）。")

    drafts: "queue.Queue[str]" = queue.Queue()          # 草稿文本 -> 主线程
    drafts_zh: "queue.Queue[str]" = queue.Queue()       # 草稿译文 -> 主线程
    streamq: "queue.Queue[tuple]" = queue.Queue()       # ("zh"/"en"/"final"/"terms"/"notice"/"answer"/"answer_done"/"summary") -> 主线程
    # ⚠️ 加新 tag **必须**在 `drain()` 加同分支 —— 它是唯一的分发点，漏改即静默丢弃。
    #    2026-09-30 起 `drain()` 末尾有一条**兜底告警**（未识别的 tag 会 echo 一行）。
    draftq: "queue.Queue" = queue.Queue(maxsize=1)      # 待译草稿(只保留最新)
    properq: "queue.Queue" = queue.Queue()              # 待查专有名词
    answerq: "queue.Queue[str]" = queue.Queue()         # 待讲解的问题(Phase 2)

    def notify(msg: str, warn: bool = False) -> None:
        """引擎状态广播 -> streamq -> 主线程 drain() -> UI(悬浮窗要求主线程)。"""
        streamq.put(("notice", (msg, warn)))

    translator = EngineRouter(local_tr, cloud_tr, args.engine, notify=notify)
    translator.warmup()

    def submit_question(q: str) -> None:
        """悬浮窗输入框回车回调 -> 把问题投进问答线程(Phase 2)。

        ⚠️ 这个函数是在**主线程**被 AppKit 调用的: 只能做立刻返回的事。
        这里只有一次 `queue.put`(无界队列, 不阻塞); 网络与模型全在
        answer_worker 里。echo 是既有行为, 保留它 —— 终端里留一条"我问过什么"。
        """
        echo(f"[{now()}] 🙋 {q}")
        answerq.put(q)

    def ask_about_this() -> None:
        """悬浮窗「讲一下」按钮 -> 用固定问题开一轮(与输入框共用同一条线程)。

        与 submit_question 同规矩: 主线程回调, 只做一次 queue.put —— 不许联网、
        不许 sleep、不许长持 qa_lock(主线程同时还背着音频循环与渲染)。
        """
        echo(f"[{now()}] 🙋 [讲一下] {ASK_QUESTION}")
        answerq.put(ASK_QUESTION)

    ui = TerminalUI() if args.ui == "terminal" else _load_overlay(
        on_quit=stopping.set,
        # ❓ 要的是**按下那一刻** —— 所以它不设标志位, 直接落盘一个时间戳。
        # ⚠️ 这里带上 `writer` 是刻意的: `mark_lost()` 只做"拼一行 + 写 + flush",
        #    全是主线程能扛的量; 而且它就是 `writer` 自己的方法, 不像从前那个 ⭐
        #    那样要绕一圈到 `drain()` 里去补标志位。
        #    （⭐ 已于 2026-09-29 并进 ❓ —— 两个按钮记的是同一件事。）
        on_lost=lambda: writer.mark_lost(),
        on_translate=lambda mode: trans.__setitem__("mode", mode),
        on_submit=submit_question,
        on_ask=ask_about_this,
        # late binding: start_new_topic 定义在下面的问答状态块里, 这里只是把回调
        # 装上(按钮到点名前一定已经定义好了)。
        on_new_topic=lambda: start_new_topic(),
        whatsnew=whatsnew,
        # ⭐ 采集面 ⑦：UI 使用度。⚠️ `tester` 是 `None` 时传 `None`（不是传个假
        #    回调）—— `Overlay` 那边默认就是空 lambda，**零开销零行为**。
        note=(tester.note_ui if tester is not None else None),
    )
    # ⭐ 直播闸门（2026-10-01 审查 F7）：把「课还在音频阶段吗」告诉悬浮窗 ——
    #    更新卡片的重活问询**只在没课的时候弹模态**（模态卡的是主线程，而音频
    #    `poll()/drain()` 也在主线程：弹着不答的每一秒都在丢音频）。收尾开始置 False。
    #    TerminalUI 没有 set_live → callable 守卫跳过。
    _set_live_ui = getattr(ui, "set_live", None)
    if callable(_set_live_ui):
        _set_live_ui(True)
    # 终端模式拿不到悬浮窗，退化成字符框（两种模式都要能看到）
    # ⚠️ 判据同上（`drives_appkit`，不是 `args.ui`）：overlay 载入失败会静默回退
    #    终端 UI，那时 `args.ui` 仍是 "overlay" → 字符框不打印、真悬浮窗又不存在
    #    → **更新卡片整个消失**。（2026-09-28 OCR 审计发现）
    if not getattr(ui, "drives_appkit", False) and whatsnew:
        _print_whatsnew_box(**whatsnew)

    seen_proper: collections.Counter = collections.Counter()   # 出现次数(≥2 才查)
    queried_proper: set = set()                         # 已发起过查询的(防重复请求)
    finals: list[str] = []                              # 已定稿(修正后), 作 LLM 上下文
    latest = _Latest()
    finalq: "queue.Queue" = queue.Queue()
    _QUIT = object()
    busy = {"on": False}          # 定稿线程正在处理一句(ASR+LLM), 冲刷等待要等它归零

    # ---- 问答线程状态(Phase 2) ----
    # 一节课一条线程: 一个**冻结的转录快照** + 一个只追加的 turns 列表。
    # ⚠️ 转录快照必须是 `list(finals)` 的拷贝: finals 由定稿线程持续 append,
    #    持活引用 = 快照会边问边变, 消息前缀不再稳定(缓存全废)。
    # consumed = 已经附进历史的 finals 条数; 之后的追问只补新增的那一段。
    qa_lock = threading.Lock()
    qa = {"history": [], "consumed": 0, "gen": 0}

    def start_new_topic() -> None:
        """清空问答线程(Phase 3 的「字幕」按钮)。下次提问会重新冻结转录底座 ——
        底座仍是"这节课到此刻为止", 只是不再背着上一个话题的问答历史。"""
        with qa_lock:
            qa["history"].clear()
            qa["consumed"] = 0
            qa["gen"] += 1                     # 在途的那一轮回来时会被丢弃

    # ---- 草稿线程 ----
    # 每定稿一句 +1; 草稿线程据此重置节流(见 partial_worker 里的说明)。
    final_gen = {"n": 0}

    def partial_worker():
        last_tr = 0.0
        last_len = 0
        last_gen = 0
        while running.is_set():
            buf = latest.take(0.5)
            if buf is None or len(buf) == 0:
                continue
            # 定稿是关键路径(决定你多久看到中文), 且它要抢同一把 ASR 锁。
            # 有定稿在排队时让草稿这一轮退开: 草稿迟到 1s 无感, 定稿迟到直接
            # 拖慢首字。定稿线程本来就把草稿译文排在定稿之后(见 final_worker)。
            if not finalq.empty():
                continue
            try:
                t = asr.transcribe(buf[-PARTIAL_MAX_S * SR:])
                if not t:
                    continue
                drafts.put(t)
                # 新句子开始 -> 重置草稿节流。**必须重置**: last_len 是**上一句**
                # 收尾时的词数, 新句若更短(如 20 词 vs 30 词), "新增 ≥6 词"永远
                # 不成立 -> 整句拿不到即时中文("首字中文 ~3s"特性静默失效)。
                if final_gen["n"] != last_gen:
                    last_gen = final_gen["n"]
                    last_tr, last_len = 0.0, 0
                # 草稿译文: 距上次 ≥2.5s 且新增 ≥6 词才送(避免刷爆 LLM)
                now = time.monotonic()
                if trans["mode"] == "both" and now - last_tr >= 2.5 \
                        and len(t.split()) - last_len >= 6:
                    last_tr, last_len = now, len(t.split())
                    try:
                        draftq.put_nowait(t)
                    except queue.Full:               # 只留最新
                        try:
                            draftq.get_nowait(); draftq.put_nowait(t)
                        except queue.Empty:
                            pass
            except Exception as e:                       # noqa: BLE001
                echo(f"⚠ 草稿转写失败: {e}")

    # ---- 定稿线程(ASR + 语义缝合 + 断句 + 逐句流式 LLM) ----
    # 优先级: 定稿 > 草稿译文。carry 挂起超 CARRY_MAX_S 或词数超限 → 强制送出,
    # 绝不让"等句子闭合"把翻译无限期阻塞(否则会出现 20s+ 才吐中文)。
    CARRY_MAX_S = 5.0
    CARRY_MAX_WORDS = 25
    carry = {"text": "", "since": 0.0}

    def _emit(text: str) -> None:
        final_gen["n"] += 1                      # 通知草稿线程: 重置节流
        for sent in split_sentences(text):
            if trans["mode"] == "raw":
                # 纯转录: 一个 LLM 请求都不发。ASR 原样上屏、原样落盘。
                # 这是给"不需要翻译、也不想联 API"的场景 —— 离线、零成本、零首字延迟。
                # ⚠️ 与下面两档的关键差别: 不产出 `en` 流(没有矫正可流式), 直接给
                #    ("final", "", "", sent), 让 en 走 final 那一趟。
                finals.append(sent)
                streamq.put(("final", "", "", sent))
                continue
            if trans["mode"] == "en":
                # 只矫正英文: 中文不出, 但 ASR 错听照修(仍要联模型)。
                # 失败则回退原始 ASR —— 与翻译路径同规矩, 绝不因模型问题丢转录。
                try:
                    fixed = translator.fix_stream(
                        sent, finals,
                        on_en=lambda d: streamq.put(("en", d)))
                except Exception as e:              # noqa: BLE001
                    echo(f"⚠ 英文矫正失败({str(e)[:50]}); 保留原始转录")
                    fixed = ""
                finals.append(fixed or sent)
                streamq.put(("final", fixed, "", sent))
                continue
            # 翻译失败也要保住转录: LLM 挂了不代表这节课没听到东西。
            try:
                res = translator.fix_and_translate_stream(
                    sent, finals,
                    on_zh=lambda d: streamq.put(("zh", d)),
                    on_en=lambda d: streamq.put(("en", d)))
                en, zh = res.en_fixed, res.zh
                finals.append(en)
            except Exception as e:                  # noqa: BLE001
                echo(f"⚠ 翻译失败({str(e)[:50]}); 仅保留转录")
                en, zh = "", ""                     # 无修正版/译文, 内容全在转录里
                finals.append(sent)                 # 上下文仍接得上
            # 第 4 项是**原始 ASR 转录**(未修正), 落盘时要一并记下
            streamq.put(("final", en, zh, sent))
            # 术语/专有名词: 命中术语表就把 (术语, 解析) 交给 UI —— 由 UI 决定
            # 什么时候展开(悬浮窗默认只列术语名, 点击才显示解析)。
            hits = notes.match(sent)
            streamq.put(("terms", hits))
            # 术语表没覆盖的专有名词(人名/机构/地名) -> 第 2 次出现才后台查一次并缓存
            # (一次性的 ASR 误听往往只出现一次, 2 次门槛把它们挡在门外)
            if not hits and api_key_val and trans["mode"] != "raw":
                for pn in detect_proper_nouns(sent, notes.known()):
                    seen_proper[pn] += 1
                    if seen_proper[pn] == 2:
                        properq.put(pn)

    def _force_emit_carry() -> None:
        if not carry["text"]:
            return
        t = carry["text"]
        # ⚠️ 顺序是命门: busy 必须在**清空 carry 之前**置位。
        # 收尾判据 all_settled() 靠"carry 空 + busy 空"两个条件接棒 —— 先清 carry
        # 会在这两条字节码之间让**四项同时为空**, 冲刷循环的"双检查"(间隔 0.25s)
        # 若两次都落进这个窗口就提前 break, 这句永久丢失(实测复现过的丢句 bug)。
        # (2026-09-24 全仓审计发现: 注释一直这么写, 代码却是反的 —— 已按注释修回。)
        busy["on"] = True
        carry["text"], carry["since"] = "", 0.0
        try:
            _emit(t.rstrip() + " …")
        except Exception:                               # noqa: BLE001
            pass
        finally:
            busy["on"] = False

    def final_worker():
        EMPTY = object()
        while running.is_set():
            # 1) 定稿优先
            try:
                buf = finalq.get_nowait()
            except queue.Empty:
                buf = EMPTY
            if buf is _QUIT:
                break
            if buf is EMPTY:
                # 2) 其次: 草稿译文(尽力而为, 过期即丢)
                try:
                    dtext = draftq.get_nowait()
                except queue.Empty:
                    dtext = None
                if dtext and trans["mode"] == "both":
                    try:
                        translator.translate_draft(
                            dtext, lambda d: drafts_zh.put(d))
                    except Exception:                   # noqa: BLE001
                        pass
                    continue
                # 3) 空转: 检查 carry 是否挂起过久
                time.sleep(0.2)
                if carry["text"] and time.monotonic() - carry["since"] >= CARRY_MAX_S:
                    _force_emit_carry()
                continue
            try:
                # ⚠️ 顺序: **先跑定稿模型**, 退化才回头跑草稿模型。
                # 反过来写(先 parakeet 再 whisper)会让 parakeet 那 0.36s 白花 ——
                # 实测它的结果只在 whisper 退化时用得上, 而 64 段里退化 0 次。
                # 先跑慢的那个, 命中就省下整个快的那趟; 退化时多花的 0.36s 无所谓。
                _t_asr = time.monotonic()
                text = asr_final.transcribe(buf)
                _used, _lp = "whisper", asr_final.last_logprob
                if not text or is_degenerate(text):
                    text = asr.transcribe(buf)      # 退化/空 -> 用草稿模型(并兜底)
                    _used, _lp = "parakeet", asr.last_logprob
                if tester is not None:
                    tester.note_segment(buf, text, (time.monotonic() - _t_asr) * 1000,
                                        _used, logprob=_lp,
                                        fell_back=(_used == "parakeet"),
                                        n_sentences=len(split_sentences(text)))
                if not text:
                    continue
                if carry["text"]:                           # 与上句半截拼接
                    text = f"{carry['text']} {text}".strip()
                    carry["text"], carry["since"] = "", 0.0
                # 未说完 且 未超上限 -> 继续等; 否则强制送出
                if is_incomplete(text) and len(text.split()) < CARRY_MAX_WORDS:
                    carry["text"] = text
                    if not carry["since"]:
                        carry["since"] = time.monotonic()
                    continue
                # ⚠️ 顺序同 _force_emit_carry: busy 先置位、再清 carry, 否则这两条
                # 字节码之间四项同时为空 -> 冲刷循环可能提前 break 丢掉这句。
                # (注释旧版写的是"busy 覆盖 ASR + _emit 全程" —— 那与实现不符:
                #  asr.transcribe 在这之前就已执行, busy 并没有覆盖 ASR。而且真把
                #  busy 提到 ASR 之前, 下面几条 `continue` 路径都会忘记清 busy,
                #  反而会让冲刷循环空等到 15s 上限。故只修顺序, 并改正这句注释。)
                busy["on"] = True
                carry["text"], carry["since"] = "", 0.0
                try:
                    _emit(text)
                finally:
                    busy["on"] = False
            except Exception as e:                       # noqa: BLE001
                echo(f"⚠ 定稿失败: {e}")
                busy["on"] = False
        _force_emit_carry()                          # 收尾: 残余半句也翻掉

    seg = Segmenter(
        on_partial=lambda buf: latest.put(buf),
        on_utterance_end=lambda buf: finalq.put(buf),
    )

    # ---- 专有名词查询线程(网络请求, 异步且缓存, 不影响主链路) ----
    def proper_worker():
        while running.is_set():
            try:
                term = properq.get(timeout=0.5)
            except queue.Empty:
                continue
            if term is None:
                break
            if term in queried_proper:
                continue
            if trans["mode"] == "raw":       # 纯转录: 不联模型, 专有名词查询整条跳过
                continue
            queried_proper.add(term)
            try:
                res = lookup_term(term, api_key_val, args.cloud_model)
                if res:
                    notes.add(term, res["note"], res["type"])   # 写 auto 文件, 下次查表命中
                    streamq.put(("terms", [(term, res["note"])]))
            except Exception as e:                                 # noqa: BLE001
                # 单条失败就放过去。线程一旦死掉, 之后**所有**专有名词都静默停摆 ——
                # 而这条链本就是可有可无的(不影响字幕主链路), 不值得为它陪葬。
                echo(f"⚠ 专有名词查询失败({term[:24]}): {str(e)[:60]}")

    # ---- 问答线程(Phase 2) ----
    # 形状照抄 proper_worker: daemon + 只认自己的队列 + 只判 running。
    # ⚠️ **绝不碰 busy / finalq / carry**: 那三个是 all_settled() 收尾闸门的输入
    #    (见 all_settled 的注释), 问答去动它们会让"文件播完"的冲刷判断误判 ——
    #    轻则丢最后一句, 重则提前 break 掉整段收尾。问答是**可以丢的**: 进程退出
    #    就走, 答案没写完就算了, 落盘才是不能丢的那件事。
    def answer_worker():
        while running.is_set():
            try:
                q = answerq.get(timeout=0.5)
            except queue.Empty:
                continue
            if q is None:
                break
            with qa_lock:
                gen = qa["gen"]
                first = not qa["history"]
                # 首次提问 = 冻结整段快照; 追问 = 只补上次提问之后新讲的句子
                # (锁定需求是"整节课转录至今", 不是"第一次提问那一刻的转录")。
                # ⚠️ 只快照**一次**。写成 `list(finals[consumed:])` 再 `len(finals)`
                # 是两次独立读取, final_worker 的 append 可以插在中间 —— 那几句
                # 会被**永久跳过**(consumed 已越过它们), 且只在后续追问里静默少掉,
                # 无从察觉。独立验证用强制线程切换复现: 300000 轮里 3283 轮不一致,
                # 共 265801 句被静默丢弃(默认切换间隔下 4/300000)。
                snap = list(finals)
                block = snap if first else snap[qa["consumed"]:]
                qa["consumed"] = len(snap)
                hist = [dict(t) for t in qa["history"]]
            # ⭐ **全库检索**：拿问题去搜一遍，把**历史课**里的相关材料并进这一轮。
            #    （plan §2.3。数据源是 `find.search()`，实测全量 34–100 ms。）
            # ⚠️⚠️ **必须在这里算一次，然后把同一个字符串给两边** —— 下面 `content`
            #    要**逐字**写回历史，而 `answer_stream` 内部会拿 `context` 再拼一遍。
            #    两处各算一次 = 历史里的前缀与真正发出去的对不上（那条警告就在下面）。
            # ⚠️ 检索失败**绝不能让问答挂掉** —— 捞不到材料只是少点背景。
            ctx = ""
            try:
                import find as _find
                ctx = _find.as_context(_find.search(q, limit=8))
            except Exception:                             # noqa: BLE001
                ctx = ""
            # 用户 turn 的正文由 answer_user_content 统一生成 —— 下面要把它**逐字**
            # 写回历史, 必须与 answer_stream 内部发给模型的那一份完全一致。
            content = answer_user_content(q, block, not first, context=ctx)

            def _emit(d):
                # ⚠️ 收尾中就不再往 streamq 写。streamq 是 `all_settled()` 的**五个
                # 输入之一**, 答案还在流就会一直等不到收尾(独立验证实测: 0.38s
                # -> 15.2s)。答案在退出时本来就是可以丢的(见上方注释)——
                # **卡住收尾才是真损失**。
                # ⚠️ 判据是 stopping 而不是 running: running 要到收尾之后才清,
                # 用它等于没写守卫(实测自然结束时密集流仍卡 15s)。
                # ⚠️ 再叠 `gen == qa["gen"]`: 按过「字幕」的那一轮**已经在途的增量
                # 也必须停**。否则 ① 线程历史把它丢了(下面的判断), ② 面板却被它重新
                # 接管 —— 用户按「字幕」要的就是"回到字幕", 结果 0.2s 后答案又回到
                # 屏上, 按钮等于没生效。与下面那条"丢弃这一轮"是同一条规则。
                if not stopping.is_set() and gen == qa["gen"]:
                    streamq.put(("answer", d))

            try:
                text = translator.answer(q, block, hist, on_delta=_emit, context=ctx)
            except Exception as e:                # noqa: BLE001
                text = f"⚠ 讲解失败: {str(e)[:80]}"
            with qa_lock:
                if gen == qa["gen"]:              # 期间按过「字幕」-> 丢弃这轮
                    qa["history"].append({"role": "user", "content": content})
                    qa["history"].append({"role": "assistant", "content": text})
            # 同上: 被「字幕」作废的那一轮连收尾包也不发 —— 连终端那份 echo 一起
            # 丢掉, 与"它不进线程历史"保持一致(半途被作废的回答不该留下记录)。
            if not stopping.is_set() and gen == qa["gen"]:
                streamq.put(("answer_done", q, text))

    t1 = threading.Thread(target=partial_worker, daemon=True)
    t2 = threading.Thread(target=final_worker, daemon=True)
    t3 = threading.Thread(target=proper_worker, daemon=True)
    t4 = threading.Thread(target=answer_worker, daemon=True, name="answer-qa")
    t1.start(); t2.start(); t4.start()            # 问答不需要 key 就能起步
    if api_key_val:                               # (没 key 时它只回一句说明)
        t3.start()

    echo(f"▶ 开始: source={args.source}" + (f" path={args.path}" if args.path else ""))
    try:
        src = load_source(args.source, args.path, args.speed)
    except Exception as e:                       # noqa: BLE001
        # 走到这里模型已加载、会话文件已建 —— 绝不能裸崩, 那会把这次课的转录一起丢掉
        # ⚠️ 这是**运行中**的失败（不是启动失败）：上面那次试开明明成功了。
        #    仍然要说人话 —— 下面还会走 writer.close() 把已录到的内容落盘。
        notice.alert(f"音源断了（{args.source}）",
                     f"{e}\n\n已经录到的内容会照常存下来。")
        # ⚠️ 早退也要收尾: 会话文件已经建了(带抬头), 直接 return 会跳过 writer.close(),
        # 留下一个只有抬头、没走笔记流程的半成品文件 + 泄漏的文件句柄。
        # (2026-09-24 OCR 发现。)
        try:
            writer.close(ask=False)
        except Exception:                        # noqa: BLE001
            pass
        return

    # ══════════════════════════════════════════════════════════════════
    # 实时总结 worker（`live_summary.LiveSummarizer`）—— 原子层 + 章节纲要
    # 把一节课切成可单独引用的小块（原子），再把同主题连续的原子合成**章节**，
    # 供三条线共用（重点标注 / 全局检索 / 声纹）与**课中纲要**。
    # ⚠️ **独立线程 + 只读快照**，与 `entry_launch` 同一条结构保证：它崩了
    #    只是没有纲要，**录课一个字都不受影响**。
    # ⚠️⚠️ **纯转录档整条不开** —— `DESIGN.md:187` 逐字「纯转录 | 一个模型请求都不发」。
    #     判据在**取件时**（`drain` 那一支），**不是启动时判一次** ——
    #     那三档是**课中可切**的（🌐 按钮循环），启动时判会漏掉后来切进去的。
    # ⚠️ 逻辑**全在 `live_summary.py` 里**（阶段 1/2 建的、有 120 条判据），
    #    本文件这一段只做**接线** —— 给它四个依赖：
    #    `chat` / `append_atoms` / `chapter_path` / `emit`。
    # ══════════════════════════════════════════════════════════════════
    import atom
    import chapter as ch
    import live_summary as _live

    #: ⭐ 开关：**阶段 4 起默认开**（计划 §8.4 / D14：阶段 3 默认 `"0"`，阶段 4 翻成 `"1"`）。
    #: ⚠️ 想关掉就 `CLASSLIVE_LIVE_SUMMARY=0 cl`（比如只想省 API 调用时）。
    #: ⚠️ 它关的是**章节合成 / 纲要落盘 / 往 streamq 发消息**这三件新事。
    #: ⚠️⚠️ 另外**两处改进不在开关后面**（作者明确要求，D14）：
    #:    ① 模型调用失败时窗口**不再丢弃**，改为留着 `RETRY_S` 后重试；
    #:    ② 下课时**残余窗口补提交**一次。
    #:    这两条修的是今天确实存在的**丢数据**问题 —— 是**有意的改进**，
    #:    不是新功能，所以开关关着它们也生效。
    _summary_on = os.environ.get("CLASSLIVE_LIVE_SUMMARY", "1") == "1"

    def _summ_chat(sysp, block, max_tokens, temperature):
        """⚠️ 复用 `build_notes._chat_json` —— **不做第 7 处手写 httpx**
        （仓库已有 5 处各自手写调 DeepSeek 的，`classify.py` 也复用的是它）。"""
        from build_notes import _chat_json
        if not api_key_val:
            return None
        # ⚠️ 短超时（2026-10-01 审查 F9 / 计划 D16）：默认 180 秒会让 worker 攥着
        #    `_step_lock` 最长 3 分钟 —— `finish(25s)` 拿不到锁就"只关文件"，
        #    残余窗口和最后一章的正式合成全丢。20 秒对正常调用绰绰有余。
        return _chat_json(api_key_val, atom.MODEL, sysp, block,
                          max_tokens, temperature, timeout=20.0)

    def _summ_emit(payload: dict) -> None:
        """推给界面 **+ 打终端那一行**。**两件事都由产出方负责**（见 `summary_log_line`）。

        ⚠️ 那一行排在守卫**之前**：收尾时 `stopping` 已置位，而**最后一章正是在那时
           定稿的** —— 排在后面就等于**最后一章永远静默**（2026-09-30 实测）。
        ⚠️ 守卫看的是**界面还在不在**，不是**用户停没停**：
           `stopping` 在「播完」时也会置位，那时界面还活着、收尾卡还在显示，
           而 `finish()` 那最后一个 payload **应该送达** —— 否则阶段 4 的纲要会
           **缺最后一章**。判据与 `_wrap` 里的 `_ui_gone()` 一致
           （`ui._closed`；`TerminalUI` 没有这个概念，永远 False）。
        ⚠️ 整段包 `try`：转不过去只是少一条纲要，**绝不能让 worker 抛**
           （worker 抛了会**静默带走整条总结线**）。
        """
        line = summary_log_line(payload)
        if line:
            echo(f"[{now()}] {line}")
        if bool(getattr(ui, "_closed", False)):
            return
        try:
            streamq.put_nowait(("summary", payload))
        except Exception:                                # noqa: BLE001
            pass

    summ = _live.LiveSummarizer(
        chat=_summ_chat,
        append_atoms=writer.append_atoms,
        # ⚠️ `session_path` 可能是 `None`（`--save-notes no`）→ `chapter_path_for`
        #    收 `None` 返回 `None`，`ChapterWriter` 那时不建文件、不写盘。
        # ⚠️ 它在 **`chapter.py`** 里，不在 `live_summary.py`（探针那边也是这么调的）。
        chapter_path=ch.chapter_path_for(writer.session_path) if _summary_on else None,
        emit=_summ_emit if _summary_on else (lambda _p: None),
        chapters_enabled=_summary_on,
        deadline_gate=_live.jev_deadline_gate() if _summary_on else None)

    threading.Thread(target=summ.run, args=(running,), daemon=True,
                     name="live-summary").start()

    def drain():
        while True:
            try:
                ui.add_draft(drafts.get_nowait())
            except queue.Empty:
                break
        while True:
            try:
                ui.draft_zh(drafts_zh.get_nowait())
            except queue.Empty:
                break
        while True:
            try:
                item = streamq.get_nowait()
            except queue.Empty:
                break
            if item[0] == "zh":
                ui.stream_zh(item[1])
            elif item[0] == "en":
                ui.stream_en(item[1])
            elif item[0] == "terms":
                if item[1]:
                    ui.terms(item[1])
            elif item[0] == "notice":
                msg, warn = item[1]
                ui.notice(msg, warn)
            elif item[0] == "answer":               # ("answer", delta) 讲解流式增量
                # ⚠️ 用 getattr 守卫: 悬浮窗的渲染是 Phase 3, Overlay 现在**没有**
                # answer_delta。drain() 没有 try/except, 直接调会 AttributeError
                # 打死主循环(与下面 close() 的守卫同一个理由)。
                fn = getattr(ui, "answer_delta", None)
                if callable(fn):
                    fn(item[1])
            elif item[0] == "answer_done":          # ("answer_done", 问题, 回答)
                fn = getattr(ui, "answer_done", None)
                if callable(fn):
                    fn(item[1], item[2])
                # Phase 2 只要求"可观测/可测": 回答在终端整段打出来(悬浮窗渲染
                # 留给 Phase 3)。与 🙋 那行配对, 终端日志一眼能读完整条问答。
                echo(f"[{now()}] 🤖 {item[2]}")
            elif item[0] == "summary":              # ("summary", payload)
                # ⭐ 实时总结推给界面。⚠️ **`getattr` 守卫** —— `TerminalUI` 没有
                #    `summary_update`，而 `drain()` 没有 try/except，
                #    直接调会 AttributeError **打死主循环**（同上面 answer 那条）。
                # ⚠️ **终端那一行不在这里打** —— 已归产出方 `_summ_emit` 管
                #    （见 `summary_log_line`：收尾时主循环已退出、这里根本跑不到，
                #     放在这儿会让**最后一章永远静默**）。
                fn = getattr(ui, "summary_update", None)
                if callable(fn):
                    fn(item[1])
            elif item[0] == "final":                # ("final", en, zh, asr_raw)
                ui.finalize(item[1] or item[3], item[2])   # 翻译失败时至少显示转录
                writer.append(item[1], item[2], raw=item[3])
                # ⭐ 采集面 ③：`item[1]` 是**矫正后**的英文、`item[3]` 是**原始 ASR**
                #    —— 只有**这里**同时拿得到这一对（`note_segment` 那边没有）。
                #    ⚠️ `note_correction` 自己是 `_safe` 包的，**不可能抛**，
                #       所以放在 `drain()` 里是安全的（这里没有 try/except）。
                if tester is not None:
                    tester.note_correction(item[1], item[3])
                # 🆕 实时总结：把这一句转给 worker。⚠️ **非阻塞** ——
                #    `drain()` 在主循环里，这里是「不阻塞不变量」管着的地方。
                #    ⚠️ 档位判在**这里**，不是启动时 —— 三档课中可切（见 worker 那段）。
                #    ⚠️ 整段包 `try`：转不过去只是少几条，绝不能让 drain 抛。
                if trans["mode"] != "raw":
                    try:
                        summ.feed((writer._n, time.strftime("%H:%M:%S"),
                                   item[1] or item[3], item[2]))
                    except Exception:               # noqa: BLE001
                        pass
            else:
                # ⚠️ **兜底告警**：往 `streamq` 里加了新 tag 却忘了在 `drain` 加分支时
                #    **出声**，而不是静默丢弃。`drain()` 是**唯一**的 tag 分发点。
                #    ⚠️ 只 `echo`，不做任何可能抛的事 —— 这里没有 try/except。
                echo(f"⚠ drain: 未识别的 streamq tag {item[0]!r}（已丢弃）")

    try:
        _dev = {"wall": time.time(), "next": 0.0}
        while running.is_set() and not stopping.is_set():
            chunk = src.poll()
            if chunk is not None and len(chunk):
                seg.accept(chunk)
                if tester is not None:
                    tester.note_chunk(chunk)
            drain()
            if src.is_done():
                break
            if time.monotonic() >= _dev["next"]:
                _dev["next"] = time.monotonic() + DEVICE_CHECK_S
                check_clock_and_device(src, args.source, notify, _dev)
            # ⚠️ 判据是 `drives_appkit`（**UI 对象自己说的**），不是 `args.ui`：见收尾
            #    `_spin` 里那段同款说明 —— `_load_overlay` 失败时会**静默回退**
            #    `TerminalUI()`，那时 args 还写着 overlay → 每轮调一个空 pump、
            #    **一次 sleep 都没有 → 整节课纯烧 CPU**。（2026-09-28 OCR 审计发现；
            #    收尾那处当天已改，主循环这处漏了。）
            if getattr(ui, "drives_appkit", False):
                ui.pump()
            else:
                time.sleep(0.005)
    except KeyboardInterrupt:
        echo("\n⏹ 手动停止")
    finally:
        # 收尾一开始就置停机标志 —— 覆盖**所有**进入收尾的路径(文件播完 / ✕ /
        # Ctrl+C)。它同时是问答的"别再往 streamq 写了"判据: 收尾等的是
        # all_settled(), 而 streamq 是它的五个输入之一, 答案还在流就会一直
        # 等不到(实测密集流把 0.38s 拖到 15.2s)。
        # ⚠️ 判据**不能**用 running: running 要到下面内层 finally 才清, 收尾
        # 期间它一直是 set, 守卫等于没写。
        stopping.set()
        # ⚠️⚠️ **不再先撤悬浮窗**（2026-09-28 把这个决定反过来）。
        #    原来关它的理由是「后面要等冲刷 + 阻塞问是否存笔记，窗口留着不动 =
        #    点了 ✕ 也卡死」。那个理由**只在收尾阻塞主线程时才成立** —— 现在收尾
        #    搬到 worker、主线程继续 pump，窗口留着不但不卡，它还是**唯一的退出口**：
        #      · ✕ 在窗口上；
        #      · 而 `cl` 从终端起的是普通进程，activation policy 又是 `.accessory`
        #        （`overlay.show` 里设的）→ **不出现在「强制退出」窗口里**。
        #        实测作者在收尾卡住时连找都找不到它，只能强退。
        #    `close()` 仍然要调，但挪到收尾**真正结束**之后（见下面 `_close_ui`）。
        # ⚠️ 必须包住: src.close() 抛异常的话, 下面的 seg.flush() / finalq 排空 /
        # writer.close()(会话落盘 + Obsidian 写入 + 精修)**全部会被跳过** ——
        # 与"落盘才是不能丢的事"这条核心约定直接冲突。
        # 上面 probe 阶段的 probe_src.close() 就是包了 try/except 的(作者已知它会抛)。
        # (2026-09-24 OCR 全量审计发现。)
        try:
            src.close()
        except Exception:                           # noqa: BLE001
            pass
        # ---- 统一冲刷: 文件播完 / ✕ / Ctrl+C 走同一条路 ----
        # 旧代码只在"文件播完"才冲刷, 手动停止会把分段器里在途的整句话
        # (最多 12s 音频)直接丢掉 —— 实测 SIGINT 复现; 而冲刷等待只看队列,
        # carry 半句与强制送出的 LLM 输出都在队列之外, 竞态下文件尾也会丢句
        # (57.5s 切点实测复现)。现在: 冲刷分段器 + 等定稿线程真正空闲。
        # ⚠️ running 在等待期间必须保持 set, 否则定稿线程直接退出不干活。
        # 外层 try/finally 保证用户在等待中再按一次 Ctrl+C 也会走到 writer.close。
        try:
            seg.flush()
            # 收尾诊断: 句尾有没有被切、有没有整段丢掉。一切正常时 report() 为空串,
            # 不产生噪音; 有数就说明这节课的转录值得回头看那几处。
            _diag = seg.report()
            if _diag:
                echo(_diag)
            finalq.put(_QUIT)
            deadline = time.monotonic() + 15
            _settled = False
            while time.monotonic() < deadline:
                drain()
                if all_settled(finalq, streamq, drafts,
                               busy["on"], carry["text"]):
                    time.sleep(0.25)
                    if all_settled(finalq, streamq, drafts,
                                   busy["on"], carry["text"]):
                        _settled = True
                        break
                time.sleep(0.02)
            drain()
            if not _settled:
                # ⚠️ 静默丢句是这里最坏的形状（2026-10-01 审查 F6）：15 秒一到、
                #    `running.clear()` 之后 final_worker 退出，finalq 里剩的**没人处理**，
                #    会话记录里直接少几句且一个字都不说。出声（qsize 含 `_QUIT`，
                #    只说"约"）。
                echo(f"⚠ 收尾等了 15 秒还没清完 —— 队列里约 {finalq.qsize()} 句没处理完"
                     f"（这些句子不会进会话记录/笔记）")
        finally:
            running.clear()
            # ⚠️ 音频阶段到此为止（审查 F7）—— 之后更新卡片才允许弹模态重活问询。
            if callable(_set_live_ui):
                _set_live_ui(False)

            def qa_snapshot():
                """「我问过什么」交给笔记(Phase 4)。⚠️ 在 qa_lock 下**拷贝一份**:
                answer_worker 是 daemon 且**从不 join**, 收尾时它可能正卡在
                translator.answer() 里, 回来仍会往 history 追加 —— 传活引用等于让
                writer 边写边看它改。⚠️ 做成函数而不是先取好: close() 里还要等
                用户回答"是否保存"再精修, 早取会漏掉这段时间里回来的那一条问答。
                """
                with qa_lock:
                    return list(qa["history"])

            # ══ 收尾（2026-09-28 重排）══════════════════════════════════════
            # 原来这一整段同步跑在**主线程**上，而主线程就是 AppKit 的 run loop 线程
            # → 界面冻死、退不掉。实测：一节 579 句的 tut 跑完后挂了 4 小时 12 分，
            # 作者只能强制退出（而 `.accessory` 的进程连强制退出窗口里都没有）。
            #
            # `[官方]` Mac App Programming Guide「Don't Block the Main Thread」逐字:
            #   "never use the main thread to perform long-running or potentially
            #    unbounded tasks, such as tasks that require network access"
            # 精修实测 11 分钟（579 句 ÷ 30 一批 = 20 批），全在这一条上。
            #
            # 现在分两段：
            #   ① 问「存不存」—— **留在主线程**。两条硬约束：worker 里做 `input()`
            #      会偷走后续输入（实测）；且 AppKit 只能主线程碰。Overlay 自己
            #      pump，所以这几秒窗口一直是活的。
            #   ② 重活进 worker，**主线程继续 pump** 到它结束。窗口全程留着 = ✕ 有效。

            def _ui_progress(stage, done_, total):
                """⚠️ **从工作线程被调** —— 只许往 UI 的队列里塞，不许碰 AppKit。"""
                fn = getattr(ui, "wrapup_progress", None)
                if fn is not None:
                    fn(stage, done_, total)

            def _ui_gone() -> bool:
                """用户把窗口关了吗（Overlay 的 ✕）。TerminalUI 没有这个概念。"""
                return bool(getattr(ui, "_closed", False))

            def _spin(until) -> None:
                """守着 pump 等 `until()` 为真，或用户关窗。

                ⚠️⚠️ **这一步就是"不冻主线程"本身** —— 没有它，收尾期间窗口是死的，
                   ✕ 按不动，`terminate:` 也收不到。
                ⚠️ 判据是 `drives_appkit`（**UI 对象自己说的**），不是 `args.ui`：
                   `_load_overlay` 失败时会**静默回退** `TerminalUI()`，那时 args 还写着
                   overlay → 每轮都调一个空 pump、**一次 sleep 都没有 → 纯烧 CPU**。
                   （2026-09-28 OCR 审计发现。）"""
                appkit = bool(getattr(ui, "drives_appkit", False))
                while not until() and not _ui_gone():
                    if appkit:
                        ui.pump()
                    else:
                        time.sleep(0.02)

            save, give_up, _box = True, False, {}
            if writer.mode == "ask":
                if _wrapup_route(ui, _ui_gone()) == "terminal":
                    # 窗口已经关了（✕ = 正常停止）或这个 UI 没有卡 —— 走终端那条，
                    # 它对超时 / EOF 一律**默认存**。理由见 `_wrapup_route` 的 docstring。
                    ans = _ask_save_notes(writer.count)
                else:
                    # ⚠️ 返回**只有 `True`/`False`**（2026-09-30 起）——「问话期间关窗」
                    #    不再是第三种答案（那时 `None` → `give_up` → **整节课没笔记**）。
                    #    它现在按「存」走，理由见 `Overlay.ask_save` 的 docstring：
                    #    一个朋友就是这么丢了一节课的笔记。
                    ans = ui.ask_save(writer.count)
                save = bool(ans)
                # ⚠️ `give_up` = 用户**显式说了不要**这份笔记（终端答 `n` / 卡上点「不存」）。
                #    它与「关窗」**不是一回事** —— 关窗现在按存走。
                #    ⭐ 这条路的意图写在下面 `else:` 那段注释里（原文）：
                #    「用户已经说了不要这份笔记，这时候再花他的钱去合成章节是错的」
                #    —— 所以它跳过整段收尾（不精修、不写笔记），只关文件。
                give_up = (ans is False)

            _cancel = threading.Event()
            if not give_up:
                # 卡上装一个 [跳过精修]（终端那条是空操作）。与 Ctrl+C 同一条路。
                _ui_begin = getattr(ui, "wrapup_begin", None)
                if callable(_ui_begin):
                    _ui_begin(_cancel)
                _done = threading.Event()

                def _wrap():
                    try:
                        # ⭐ 实时总结先收尾：**必须在 `writer.close()` 之前**。
                        # ⚠️ 理由不是顺序好看 —— `AtomWriter` 的 `_handle()` 发现句柄为
                        #    `None` 会**重新打开文件写入**（`atom.py` 那条 `_closed` 判据
                        #    是这一轮才加的）。在 `writer.close()` 之后再产原子 =
                        #    **静默重开 + 泄漏句柄**（原 `main.py` 内联那版就有这个暴露面）。
                        # ⚠️ 它自己带 `timeout_s` 兜底；这里再包一层 `try` 是因为
                        #    **收尾失败绝不能连累笔记**（笔记才是这一节课的主产物）。
                        try:
                            summ.finish(timeout_s=SUMMARY_FINISH_S, cancel=_cancel)
                        except BaseException as e:        # noqa: BLE001
                            echo(f"⚠ 实时总结收尾失败（笔记照写）："
                                 f"{type(e).__name__}: {str(e)[:80]}")
                        _box["msg"] = writer.close(ask=lambda n: save,
                                                   qa=qa_snapshot,
                                                   on_progress=_ui_progress,
                                                   cancel=_cancel)
                    except BaseException as e:            # noqa: BLE001
                        _box["err"] = e
                    finally:
                        _done.set()                       # ⚠️ 不置的话主线程等到天荒

                threading.Thread(target=_wrap, daemon=True).start()
                # ⚠️⚠️ **Ctrl+C 打不到 worker 上** —— Python 的信号 handler 只在主线程跑
                #    （PEP 475），所以中断永远落在这一句 `_spin` 里。不接住的话异常从这里
                #    穿出去，进程立刻退出，**把正在写盘的 daemon worker 连同写到一半的
                #    笔记一起杀掉** —— 那正是 `obsidian_writer` 里那两处
                #    `except KeyboardInterrupt` 要防的事，而它们**在真实流程里收不到中断**
                #    （只有 `rebuild_note.py` 那种同步调 `close()` 的路径才收得到，
                #     所以 `tests/test_wrapup.py` 全绿也说明不了这条）。
                #    （2026-09-28 OCR 审计发现。）
                #    → 第一次 Ctrl+C：置 `_cancel`，让它在**批次边界**停下来、**笔记照写**。
                #      第二次：真要立刻走，放它抛。
                while True:
                    try:
                        _spin(_done.is_set)
                        break
                    except KeyboardInterrupt:
                        if _cancel.is_set():
                            echo("\n⏹ 强制退出 —— 笔记没写完，逐句日志仍在 sessions/")
                            raise
                        _cancel.set()
                        echo("\n⏹ 已请求跳过精修，正在把笔记写出来…"
                             "（再按一次 Ctrl+C 强制退出，笔记不会写）")
                if "err" in _box:
                    # ⚠️⚠️ **这里原来是一句 `raise _box["err"]` —— 2026-09-30 全量 OCR
                    #    审查发现它把后面整段都跳掉了**：
                    #      · `tester.finish()`（**测试报告 + 9 MB 音频不会上传**）
                    #      · `wrapup_close()`（收尾卡留着不走）
                    #      · `_auto_update_on_exit()`
                    #    而且异常一抛，下面那句 `_ok = "err" not in _box` **永远算不到
                    #    False** → 「失败留住」那条分支与 `wrapup_done(False, …)`
                    #    **不可达** —— 一个「失败要留住给用户看」的设计被它自己废掉了。
                    #    ⚠️ 触发条件是现实的：`writer.close()` 抛（磁盘满 / vault 写不进 /
                    #       精修崩）。
                    #    → 改成**不出声地继续**：错误照旧留在 `_box` 里，
                    #      交给下面那条 `_ok=False` 的路（它本来就是为这个场景写的），
                    #      外加一行给终端/日志。
                    echo(f"⚠ 收尾失败（笔记可能没写成）："
                         f"{type(_box['err']).__name__}: {str(_box['err'])[:120]}")
            else:
                # ⚠️ **放弃路径**（问话时被关窗）：上面整块被跳过 → `writer.close()`
                #    不会跑，所以实时总结的写入器也没人关。
                # ⭐ `close()` **只关文件、一个模型都不调** —— 用户已经说了不要这份笔记，
                #    这时候再花他的钱去合成章节是错的。
                # ⚠️ 包 `try`：这条路上笔记本来就没写，更不该因为关个句柄而崩。
                try:
                    summ.close()
                except BaseException as e:                # noqa: BLE001
                    echo(f"⚠ 实时总结关闭失败：{type(e).__name__}: {str(e)[:80]}")

            msg = _box.get("msg", "")
            if msg:
                echo(msg)

            # ---- 结论：成功自动退，失败留住 -------------------------------
            # ⚠️ 判据**只看 worker 有没有抛异常** —— 不能拿 `bool(msg)`。
            #    `close()` 有两条**合法的空返回**：① `not enabled or not session_path`；
            #    ② 零句又无问答（麦克风故障 / 开课十几秒就退 —— `close()` 那段注释里
            #    明说这是真实场景）。拿 `bool(msg)` 判会把它们当成失败：卡上打
            #    「⚠ 收尾失败」（**其实什么都没失败**），还卡在「失败留住」等用户
            #    点关闭；而重构前那两条是**静默成功退出**的。
            #    （2026-09-28 OCR 审计发现。）
            _ok = "err" not in _box
            _done_ui = getattr(ui, "wrapup_done", None)
            if _done_ui is not None:
                _done_ui(_ok, msg or ("收尾失败" if not _ok else "收尾结束"))
            if _ok:
                # 让「已写入 …」在屏上留 3 秒 —— 一闪而过等于没说。
                _t3 = time.monotonic() + 3.0
                _spin(lambda: time.monotonic() >= _t3)
            else:
                # ⚠️ **失败不自动退**（作者 2026-09-28 定的口径）：留到他看见为止。
                #    出口有两个：卡上的「关闭」，或直接关窗口。
                _ack = getattr(ui, "wrapup_acknowledged", None)
                if _ack is not None:
                    _spin(_ack)
            if tester is not None:
                # ⭐ 七格采集面里那两格「整块快照」—— 收尾一次性交出去。
                #    ⚠️ 都走 `note_block`（它自己 fail-soft），且**只读**：
                #       `src.stats()` 给的是归一化器内部那个 dict 本身，不拷贝。
                #    ⚠️ `norm` 描述的是**归一化之前**的电平 —— 与 `report.audio`
                #       里那组（量的是**之后**）是两组不同的数，别混。
                tester.note_block("norm", src.stats())
                tester.note_block("vad", dict(getattr(seg, "diag", {}) or {}))
                # ⭐ 采集面 ④：术语注入条数。两个引擎**各有一份**（云端/本地），
                #    合并起来 —— 谁跑的就在谁那份上累加，另一份是空的。
                #    ⚠️ 用 `getattr` 而不是直接取：老引擎对象/替身可能没有这个属性。
                _tm = {}
                for _t in (cloud_tr, local_tr):
                    for _k, _v in (getattr(_t, "term_stats", None) or {}).items():
                        _tm[_k] = _tm.get(_k, 0) + _v
                tester.note_block("terms", _tm)
                # ⭐ 采集面 ⑤：实时总结的只读快照。⚠️ 口径对齐离线探针的
                #    `metrics.json`（能对上的字段名逐字一致）；对不上的那几项
                #    它**刻意不返回**，别在这里补 —— 见 `LiveSummarizer.stats()`。
                tester.note_block("live", summ.stats())
                # ⚠️⚠️ `finish()` 里是 ffmpeg 转码 + 打包（注释估 ~21 秒/50 分钟）
                #    —— 原来直接在**主线程**跑，转码期间窗口不 pump
                #    （2026-10-01 审查 F21）。丢进线程 + `_spin` 等它 ——
                #    `_spin` 就是"不冻主线程"本身（见它的 docstring）。
                _vad = locals().get("_diag", "")
                _tf: dict = {}

                def _finish_worker():
                    try:
                        _tf["rep"] = tester.finish(
                            vad_report=_vad,
                            note_path=str(getattr(writer, "note_path", "") or ""),
                            # ⚠️ `--no-bundle` 原来只声明、没人读（2026-09-28
                            #    OCR 审计发现）—— README 写明它能「不打包，只写报告」，
                            #    不接上线这个开关就是个谎。
                            bundle=not args.no_bundle)
                    except Exception as e:               # noqa: BLE001
                        _tf["err"] = e

                _thf = threading.Thread(target=_finish_worker, daemon=True,
                                        name="tester-finish")
                _thf.start()
                _spin(lambda: not _thf.is_alive())
                if "err" in _tf:
                    echo(f"⚠ 测试报告生成失败（{type(_tf['err']).__name__}: "
                         f"{str(_tf['err'])[:80]}）—— 笔记不受影响")
                _rep = _tf.get("rep")
                if _rep:
                    echo(f"\n🧪 测试报告: {_rep}")
                    # ⭐ 阶段 3：排进上传队列 + 后台试一次（**不阻塞收尾**）。
                    try:
                        _u = start_upload(tester)
                        if _u.get("off"):
                            echo("   📤 上传已关（CLASSLIVE_UPLOAD=0）")
                        elif _u.get("queued"):
                            echo(f"   📤 已排队 {_u['queued']} 个文件"
                                 f"（待传 {_u['pending']} 节，后台上传中）")
                        # ⚠️ **有界地等它一下** —— 不等的话进程一退，daemon 线程
                        #    就被杀，结果是「报告传上去了、音频没传」（实测形态）。
                        #    等不到就留着，下次启动 `pump` 接着传。
                        _th = _u.get("thread")
                        if _th is not None:
                            _th.join(timeout=UPLOAD_JOIN_S)
                            if _th.is_alive():
                                echo(f"   📤 还没传完（超过 {UPLOAD_JOIN_S:.0f} 秒）"
                                     f" —— 留着，下次启动接着传")
                    except Exception as _e:               # noqa: BLE001
                        echo(f"   ⚠ 上传排队失败（{type(_e).__name__}）—— 不影响其余")
                if tester.bundle_path:
                    _sz = os.path.getsize(tester.bundle_path)
                    _mb = f"{_sz / 1e6:.1f} MB" if _sz > 1e6 else f"{_sz / 1024:.0f} KB"
                    echo(f"📦 数据包:   {tester.bundle_path}  ({_mb})")
                    if tester.record_audio:
                        echo("   ⚠️ 内含**课堂音频** + 逐字转录(可能有其他同学的声音)"
                             " —— 发出去前自己确认一下。")
                    else:
                        echo("   ℹ️ 只含指标与转录文本, **不含音频**（要留: --record-audio）。")
                    # ⚠️ 转码失败时音频还是 wav 分段 —— 报告与屏上都看得见。
                    echo("   发给作者即可, 不用解压。")
                else:
                    echo("⚠ 数据包生成失败, 但报告已写出(见上面的路径)")

            # ---- 小更新：退出时在**独立进程**里自动拉（详见 _auto_update_on_exit）----
            # 放在 finally 的**最末**：等用户答完"是否保存笔记"、测试报告也打完，
            # 再起子进程。父进程只 spawn 一下就返回，**零退出延迟**。
            # ---- 收尾真结束了，**现在**才撤悬浮窗（见上面那段：为什么挪到这儿）----
            # ⚠️ 放在最末：前面每一步（tester 报告 / 更新子进程）都可能还在窗口上
            #    留话，提前关就等于没说过。TerminalUI 没有 close，故用 getattr。
            # ⚠️ 先关收尾卡再关主窗口 —— 反过来的话卡片会孤零零留在屏上。
            _wk = getattr(ui, "wrapup_close", None)
            if callable(_wk):
                _wk()
            _close_ui = getattr(ui, "close", None)
            if callable(_close_ui):
                _close_ui()
            _auto_update_on_exit()


def check_clock_and_device(src, source_name: str, notify, state: dict) -> None:
    """上课中途的定时保安: ① 系统睡过一觉就打一条 ② 默认输入设备变了就跟着换。

    **为什么必须有**：`CallbackSource` 按**设备索引**绑定，而索引会漂 ——
    睡一觉、插拔耳机、切默认输入都会让它指向另一个设备，症状是**静默录到错的
    设备**（或干脆什么都没录到），而屏上一切正常。

    ⚠️ **机制为什么是轮询，不是 `NSWorkspace` 的休眠/唤醒通知**（我回源核过两条）：
      · Apple 那两条通知的文档各有一个 **Important** 逐字写着必须用
        `NSWorkspace.notificationCenter()` 注册（QA1340 更直白：`These notifications
        are filed on NSWorkspace's notification center, not the default`）→ 不能挂
        defaultCenter；
      · 但更要紧的是它们**靠 run loop 投递** —— `--ui terminal` 那条路没有 run loop，
        通知根本到不了，bug 照旧；
      · 轮询覆盖得更全：睡醒、插拔耳机、手动切默认输入**都能发现**；
      · 成本实测：`sd.query_devices()` 中位 **0.004ms**（50 次），2 秒一次可忽略。
    ⚠️ 探查失败**不上报**：那多半是切换过程中设备列表短暂为空，报出来只是噪音。
    """
    import time as _t
    now_wall = _t.time()
    if now_wall - state.get("wall", now_wall) > SLEEP_GAP_S:
        notify(f"[系统休眠唤醒 {_t.strftime('%H:%M:%S')}]", warn=True)
    state["wall"] = now_wall
    if source_name.lower() not in ("mic", "blackhole"):
        return
    try:
        from capture import resolve_input_device
        idx, name = resolve_input_device(source_name)
    except Exception:                                     # noqa: BLE001
        return
    try:
        if src.switch_device(idx, name):
            notify(f"[输入设备变了 {_t.strftime('%H:%M:%S')}] 换成「{name}」", warn=True)
    except Exception as e:                                # noqa: BLE001
        notify(f"⚠ 换输入设备失败({str(e)[:60]}); 仍在录上一个设备", warn=True)


def _load_overlay(on_quit=None, on_translate=None, on_submit=None,
                  on_ask=None, on_new_topic=None, on_lost=None, whatsnew=None,
                  note=None):
    """建悬浮窗。**失败回退终端 UI**（`drives_appkit` 为假 = 回退了）。

    ⚠️⚠️ **这一层的形参必须与调用点**逐字**对齐**（2026-09-30 事故）：
       采集面 ⑦ 那次给调用点和 `Overlay.__init__` 都加了 `note`，**唯独漏了这一层**
       —— Python 在**绑定参数时**就抛 `TypeError`，而这个函数自己的 `try` **还没进去**、
       根本接不住 → 异常穿过 `run()` → **进程当场死**。
       影响面：3.8.0/1/2 上**所有走悬浮窗的入口**（`cl` 零参数 / online / local /
       双击 `.app` / `cl test`）全部一开就崩，只有 `cl file`（终端模式）能用。
       → 判据：`tests/test_audit_regressions.py` 的 R17（AST 取调用点的关键字，
         逐个比对这一层的签名 —— 加形参时漏了它就会被拦住）。
    """
    try:
        from overlay import Overlay
        o = Overlay(on_quit=on_quit, on_translate=on_translate,
                    on_submit=on_submit, on_ask=on_ask,
                    on_new_topic=on_new_topic, on_lost=on_lost, whatsnew=whatsnew,
                    note=note)
        o.show()
        return o
    except Exception as e:       # noqa: BLE001
        echo(f"⚠ 悬浮窗不可用({e}); 回退终端 UI。")
        return TerminalUI()


def main():
    p = argparse.ArgumentParser(description="本地实时课堂双语字幕")
    p.add_argument("--source", choices=["mic", "blackhole", "file"], default="file")
    p.add_argument("--path", help="--source file 时的音频路径")
    p.add_argument("--speed", type=float, default=1.0, help="file 模式回放倍速(测试用)")
    p.add_argument("--ui", choices=["terminal", "overlay"], default="terminal")
    p.add_argument("--model-dir", default=models.path_of("parakeet"),
                   help="草稿+兜底的 ASR 模型目录(要求够快, 每秒要出一份草稿)")
    p.add_argument("--test-mode", action="store_true",
                   help="测试模式: 采集一份完整指标报告(逐段 ASR 耗时/电平/置信度/"
                        "资源占用), 并在会话文件旁留一份音频, 供以后优化用")
    # ⚠️⚠️ **两个旗标，不是一个**：`--record-audio` 的默认值**不能改成 True** ——
    #      改了就没法关掉它了（`store_true` 没有反向）。所以用 `default=None` +
    #      一对互斥旗标，真正的默认在下面按「是不是测试模式」判。
    p.add_argument("--record-audio", action="store_true", default=None,
                   help="留下课堂音频（**测试模式下这是默认**；约 9MB/50 分钟, "
                        "内含其他同学的声音）")
    p.add_argument("--no-record-audio", dest="record_audio", action="store_false",
                   help="这一节不留音频（覆盖测试模式的默认）")
    p.add_argument("--no-bundle", action="store_true",
                   help="测试模式下不打包成可发送的单个 zip")
    p.add_argument("--final-model-dir",
                   default=models.path_of("whisper"),
                   help="定稿专用 ASR 模型目录(必需; 用 `cl doctor` 检查)")
    p.add_argument("--llm", default=models.by_key("llm").src)
    p.add_argument("--glossary", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "glossary.txt"))
    p.add_argument("--context", type=int, default=5,
                   help="送翻译的最近上下文句数(默认 5; 远场听错多, 上下文越长越好修)")
    p.add_argument("--cloud-context", type=int, default=DEFAULT_CTX_CHUNK,
                   help="云端翻译的前文块大小(默认 %(default)s: 窗口 10–19 句、块对齐以命中 DeepSeek 前缀缓存); "
                        "0 = 老的「最近 --context 句」滑动窗口。本地引擎不受影响(仍用 --context)")
    p.add_argument("--engine", choices=["auto", "cloud", "local"], default="auto",
                   help="翻译引擎: auto(云端优先,失败降级)/cloud/local")
    p.add_argument("--cloud-model", default="deepseek-flash",
                   help="云端模型名")
    p.add_argument("--api-key", help="DeepSeek API key(默认读 DEEPSEEK_API_KEY 或 ~/.classlive/credentials)")
    p.add_argument("--course", help="课程代码(如 ECON10101); 不设也能写笔记(课名默认 LECTURE)")
    p.add_argument("--save-notes", choices=["ask", "yes", "no"], default="ask",
                   help="笔记保存策略: ask(默认,结束时问)/ yes(直接存)/ no(不存)")
    p.add_argument("--polish", choices=["auto", "off"], default="auto",
                   help="落笔前二次精修转录(需 API key; 默认 auto); off 直接用直播版")
    p.add_argument("--polish-model", help="精修用的模型(默认同 --cloud-model)")
    p.add_argument("--vault", help="Obsidian 库路径(默认取 $OBSIDIAN_VAULT 或上次用过的; 都没有就不写 Obsidian)")
    args = p.parse_args()
    run(args)


if __name__ == "__main__":
    main()
