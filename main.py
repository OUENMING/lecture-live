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
import argparse, collections, datetime, os, pathlib, queue, re, shlex, sys, threading, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from capture import load_source, SR, SilenceWatch
from vad import Segmenter
import draft_policy
import stream_gate
from draft_translate import DraftTranslator, DraftView
import livemetrics
import feature_usage
import tr_latency
import fragment_merge
from asr import load_asr
import draft_engine
import final_asr
from translator import load_translator
from cloud_translator import (DEFAULT_CTX_CHUNK, load_api_key,
                              load_translator as load_cloud_translator,
                              answer_user_content)
from obsidian_writer import ObsidianWriter, resolve_vault, remember_vault
from build_notes import (TermNotes, format_gloss, detect_proper_nouns, lookup_term)
from testmode import TestSession, system_info
import changelog
import keyentry
import instance_lock
import models
import notice
import power

PARTIAL_MAX_S = 10          # 草稿只转写最近 N 秒, 限制单次耗时
DEVICE_CHECK_S = 2.0        # 实时音源: 多久重探一次「默认输入设备还是不是那个」
SLEEP_GAP_S = 10.0          # 墙钟一次跳这么多 = 系统睡过一觉(见 check_clock_and_device)
#: 收尾时留给实时总结的上限（秒）——提残余窗口 + 给当前章做一次正式合成。
#: ⚠️ 它只是**上限**，不是固定等待：正常那一节实测 6–9 秒就走完了。
#: ⚠️ 定这个数的约束是**收尾不能明显变慢**（复习层 / 写盘还在等它），而不是"够用"。
#: ⚠️ 2026-10-12：原话是「那一步本来就在等精修」—— 精修已默认不跑，理由换成上面这个。
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


def strip_dangling(text: str, max_strip: int = 2) -> str:
    """草稿送翻前削掉结尾 1–2 个悬挂词（"... the limit of" → "... the limit"）。
    每削一个词都重新过 `is_incomplete`（口径一致，有句末标点就停）；削完不足 3 个词时不削。
    只用于**草稿**，定稿不动。"""
    words = (text or "").strip().split()
    for _ in range(max_strip):
        if len(words) <= 3 or not is_incomplete(" ".join(words)):
            break
        words.pop()
    return " ".join(words)


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


#: Soniox 封口句的劈句上限（`_emit(max_words=)`）；本地 VAD 段仍是 25。
STREAM_MAX_WORDS = 40


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


def split_carry(text: str, max_words: int = 25) -> tuple[str, str]:
    """(立刻上屏的部分, 要挂起等下一段的半句)。只看**最后一句**：
    它没有句末标点、以悬挂词结尾（`is_incomplete`）、且 < max_words 词，才挂起。"""
    sents = split_sentences(text)
    if not sents:
        return "", ""
    last = sents[-1]
    if is_incomplete(last) and len(last.split()) < max_words:
        return " ".join(sents[:-1]), last
    return text, ""


def now() -> str:
    return time.strftime("%H:%M:%S")


def echo(msg: str) -> None:
    print(msg, flush=True)


# 终端提示等多久算「人不在」。⚠️ **这个数是拍的，没有测量背书** ——
# 真正该测的是「作者离开键盘多久」，无从测。改这里**不影响 UI 那条路**（那条走卡片倒计时）。
# 2026-10-14 作者：60 → 20 秒（下课就走的人不该多等 40 秒才开始写笔记；超时 = 存，不丢东西）。
ASK_TIMEOUT_S = 20.0


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


def _wrapup_plan(enabled: bool, writes_notes: bool, mode: str) -> str:
    """收尾开头那一步：`"notice"` 不问、直接说没生成笔记 / `"ask"` 问存不存 / `"auto"` 不问照存。

    ⚠️ 有记录却没笔记库 → `"notice"`，**不问**「存入 Obsidian 吗？」：那是在问一件做不到的事。
       2026-10-12 朋友实测：她点了「存」，结论「没设库」在一张 3 秒就关的卡上，她以为存好了，
       好几节课都没进 Obsidian。
    """
    if enabled and not writes_notes:
        return "notice"
    return "ask" if mode == "ask" else "auto"


def _gave_up_hint(session_path, course: str | None, n: int) -> tuple[str, str]:
    """用户点了「不存」之后那两句：`(卡上的短句, 终端里带补救命令的长句)`。

    ⚠️ 2026-10-13 真事：一节课分两段录，前一段点了「不存」→ 那 45 分钟**永远没有复习层**，
       屏上一句没提，补救的路只有知道 `scripts/rebuild_note.py`。逐句记录其实还在 `sessions/`。
    零句 / 没有会话文件 → 两句都空（没东西可补，别多嘴）。
    """
    if not session_path or n <= 0:
        return "", ""
    name = os.path.basename(str(session_path))
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts", "rebuild_note.py")
    card = f"没存笔记 · {n} 句逐句记录留在 sessions/{name}"
    term = (f"ℹ️ 按你选的「不存」没生成笔记；{n} 句逐句记录还在 {session_path}\n"
            f"   想补回来（调一次翻译模型）：\n"
            f"   {' '.join(shlex.quote(str(x)) for x in (sys.executable, script, session_path))}"
            f" {shlex.quote(course) if course else '<课号>'}")
    return card, term


def _wrapup_hold(ok: bool, plan: str, msg: str) -> bool:
    """收尾结论要不要**留在卡上等用户点关闭**（否则 3 秒自动退）。

    失败要留（作者 2026-09-28 定的口径）；没生成笔记那句也要留 —— 它写着「下次怎么办」，3 秒没人读得完。
    """
    return (not ok) or (plan == "notice" and bool(msg))


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
       11 分钟精修全程屏上空的（那是 **2026-10-12 之前**的事，精修现已默认不跑；
       但复习层仍跑在收尾里，所以这条仍然成立）。这正是 `wrapup.py` 当初要消灭的那种「卡住不动」。
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

    ⚠️⚠️ **绝不能阻塞收尾** —— 它跑在收尾那条路上，那里本来就在等复习层 / 写盘。
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
    降级后探针从 `RETRY_PROBE_MIN_S` 秒起、每失败一次**翻倍**(封顶 `RETRY_PROBE_S`)
    用 1-token 探针试云端, 通了自动切回。
    状态变化通过 notify(msg, warn) 广播(经 streamq 转主线程, 可进 UI);
    `note(kind, detail)` 是测试模式的事件出口(`testmode.TestSession.note_event`)。

    ⭐⭐ **2026-10-15: 降级不再由「任何一次云端异常」触发**。本地模型是**可选**的
    (大多数用户没装), 而原来一次异常就翻 `_use_cloud` → 之后每句只抛 RuntimeError
    → 调用方「只有 ASR」直到探针回来。实测: 中国用户 8 节课 16 个洞, 每个 77–89 秒。
    现在(见 `_sentence_failed`): 本地不可用时**同一句先重试一次**, 仍失败才按**句**计;
    **连续** `FAIL_TRIP` 句失败才降级。本地可用时行为不变(它本来就有真兜底)。"""

    RETRY_PROBE_S = 90.0
    #: 降级后探针的**起点**(秒); 每失败一次翻倍, 封顶 `RETRY_PROBE_S`。
    RETRY_PROBE_MIN_S = 5.0
    #: **按句**计的连续失败阈值 —— 本地不可用时连败这么多句才降级。
    FAIL_TRIP = 3

    def __init__(self, local, cloud, mode: str, notify=None, note=None):
        self._local = local
        self._cloud = cloud
        self._mode = mode
        self._notify = notify or (lambda msg, warn=False: None)
        #: 🧪 测试模式的事件出口。⚠️ 默认空 lambda —— 与 `notify` 同规矩: 测试模式关时
        #   **什么都不做**(`main.run` 那边 `tester` 为 None 就传 None, 零开销零行为)。
        self._note = note or (lambda kind, detail="": None)
        self._use_cloud = bool(cloud) and mode in ("auto", "cloud")
        #: 云端**连续失败了几句**(不是几次调用 —— 同句的重试不另计, 见 `_sentence_failed`)
        self._fail_streak = 0
        #: 探针的 sleep。⚠️ 做成实例属性只为**可注入**(测试不许白等 5–90 秒);
        #:   生产恒等于 `time.sleep`, 行为一个字没变。
        self._sleep = time.sleep
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

    def _local_usable(self) -> bool:
        """本地引擎**现在**能不能用（只看在不在 / 闩没闩，不加载）。

        ⚠️ 桩没有这个方法（AttributeError）/ 判据自己坏了 → 都按「可用」算 ——
           `_fallback` 的广播一直就是这个规矩（2026-10-01 审查 F2），这里保持一致。
        """
        try:
            return bool(self._local.local_ready())
        except Exception:                         # noqa: BLE001
            return True

    @staticmethod
    def _why(e: Exception) -> str:
        """事件的 detail 口径（`testmode.note_event` 还会再截到 200）。"""
        return f"{type(e).__name__}: {str(e)[:120]}"

    def _event(self, kind: str, detail: str = "") -> None:
        """往测试报告记一条事件。⚠️ 自己吞异常 —— 「记录绝不许影响上课」（testmode 纪律）。

        ⚠️ 走的是**直连回调**（`testmode.TestSession.note_event` 只往内存里的 `events`
           列表 append），**不进 `streamq`** —— 那要新增一个 tag 并在 `drain()` 加分支
           （R28 钉着），而这条数据本来就不需要主线程、不需要 UI。
        """
        try:
            self._note(kind, detail)
        except Exception:                         # noqa: BLE001
            pass

    def _sentence_failed(self, e: Exception) -> None:
        """重试那次的失败 —— 记一条事件 + 按**句**计一次连续失败。

        （首试那次由调用点自己记，见 `fix_and_translate_stream` —— 「含重试那次」的
        意思是**两次都要留下痕迹**。）连败 `FAIL_TRIP` 句才降级；本地可用时**不走
        这里**（那条路行为不变）。
        """
        self._event("translate_fail", self._why(e))
        self._count_failure(e)

    def _count_failure(self, e: Exception) -> None:
        """按**句**计一次连续失败（不记事件）；够 `FAIL_TRIP` 句才降级。"""
        with self._state_lock:
            self._fail_streak += 1
            trip = self._fail_streak >= self.FAIL_TRIP
        if trip:
            self._fallback(e)                     # 由它翻 `_use_cloud` + 广播 + 起探针

    def _cloud_ok(self) -> None:
        """云端这一句成功了 —— 连续失败清零（「任何一次成功清零」）。"""
        with self._state_lock:
            self._fail_streak = 0

    def _retry_cloud(self, call):
        """本地不可用 + 屏上还没吐过增量时，**同一句重试一次**云端。

        成功 → 返回结果并清零；再失败 → 记一次「这一句失败了」，够 `FAIL_TRIP` 句时
        `_sentence_failed` 会把它降级，最后**把异常抛回调用方**（它照旧走「只有 ASR」）。
        ⚠️ 这里**只重试一次** —— 再退就是无边界重试，会把定稿线程钉在网络上。
        """
        try:
            res = call()
        except Exception as e:                    # noqa: BLE001
            self._sentence_failed(e)
            raise
        self._cloud_ok()
        return res

    def _fallback(self, e: Exception) -> None:
        with self._state_lock:                    # 见 __init__: 迁移只能发生一次
            if not self._use_cloud:
                return
            self._use_cloud = False
            self._fail_streak = 0                 # 降级后不再走云端, 计数从恢复起重数
        self._event("translate_fallback", self._why(e))
        # ⭐⭐ **广播要说实话**（2026-10-01 审查 F2）：本地模型是**可选**的，大多数人
        #    没装 —— 没装还说「已降级本地引擎」，用户会以为有兜底，实际每句都失败。
        #    `local_ready()` 只看在不在、不加载（桩/测试没有这个方法就按可用算）。
        local_ok = self._local_usable()
        if local_ok:
            self._notify(
                f"云端翻译失败({str(e)[:60]}); 已降级本地引擎, "
                f"{self.RETRY_PROBE_MIN_S:.0f}s 后自动重试", warn=True)
        else:
            self._notify(
                f"云端翻译失败({str(e)[:60]}); 本地模型不可用（没装，或加载失败）"
                f"—— 只保留转录（跑 cl doctor 看看）。{self.RETRY_PROBE_MIN_S:.0f}s 后自动重试云端",
                warn=True)
        self._start_probe()

    def _start_probe(self) -> None:
        if self._cloud is None:
            return
        if not self._probe_lock.acquire(blocking=False):
            return                                # 已有探针在跑

        def probe():
            try:
                # ⭐ 2026-10-15: 起点 `RETRY_PROBE_MIN_S`(5s)，每失败一次**翻倍**，
                #    封顶 `RETRY_PROBE_S`(90s) —— 瞬时抖动几秒就回来，真断网也不会
                #    每 5 秒白打一次计费 ping（原先是死板的 90s 一次，恢复最慢 90s）。
                # ⚠️ 起点取 `min(起点, 封顶)`：老用法是往 `RETRY_PROBE_S` 塞一个小周期
                #    （测试注入"短周期"），那样"封顶 < 起点"，从封顶起步才对。
                delay = min(self.RETRY_PROBE_MIN_S, self.RETRY_PROBE_S)
                while True:
                    self._sleep(delay)
                    if self._use_cloud:           # 别处已恢复(理论不可达, 保险)
                        return
                    if self._cloud.probe():
                        with self._state_lock:
                            self._use_cloud = True
                            self._fail_streak = 0
                        self._event("translate_recovered", "探针成功, 已切回云端")
                        self._notify("云端翻译已恢复", warn=False)
                        return
                    delay = min(delay * 2.0, self.RETRY_PROBE_S)
            finally:
                self._probe_lock.release()
        threading.Thread(target=probe, daemon=True,
                         name="cloud-retry-probe").start()

    @staticmethod
    def _tap(cb, seen: list):
        """包一层：记下「云端已经往屏上吐过增量」。"""
        if cb is None:
            return None

        def f(d):
            seen.append(True)
            cb(d)
        return f

    def _cloud_stream(self, en, context, on_zh, on_en, seen):
        """一次云端定稿翻译；增量经 `_tap` 记进 `seen`（「屏上已经吐过东西」）。"""
        return self._cloud.fix_and_translate_stream(
            en, context, self._tap(on_zh, seen), self._tap(on_en, seen))

    def fix_and_translate_stream(self, en, context, on_zh=None, on_en=None):
        if self._use_cloud:
            seen: list = []
            try:
                res = self._cloud_stream(en, context, on_zh, on_en, seen)
            except Exception as e:                # noqa: BLE001
                self._event("translate_fail", self._why(e))
                if not self._local_usable():
                    # ⭐⭐ 本地没装（大多数用户）时, **一次瞬时失败不许把这一句吃掉**：
                    #    屏上还没吐过增量 → 同一句立刻重试一次云端（见 `_retry_cloud`）。
                    if not seen:
                        return self._retry_cloud(
                            lambda: self._cloud_stream(en, context, on_zh, on_en, seen))
                    # 已吐过半句：不重试（屏上会重复），这句照旧失败，但只计一次 ——
                    # 降级也要连败 `FAIL_TRIP` 句（降到没装的本地 = 整段只剩 ASR）。
                    self._count_failure(e)
                    raise
                self._fallback(e)
            else:
                self._cloud_ok()
                return res
            # ⚠️ 云端断在半句上时屏上已有半句：本地的增量再接上去就是「两份拼一起」。
            #    这时本地**不流式**，结果只走返回值（`final` 那一趟整句替换）。
            if seen:
                return self._local.fix_and_translate_stream(en, context, None, None)
        return self._local.fix_and_translate_stream(en, context, on_zh, on_en)

    def fix_stream(self, en, context, on_en=None) -> str:
        """只矫正英文(翻译关闭时)。走同一个模型与同一份上下文, 只是不要中文。"""
        if self._use_cloud:
            seen: list = []
            try:
                res = self._cloud.fix_stream(en, context, self._tap(on_en, seen))
            except Exception as e:                # noqa: BLE001
                self._event("translate_fail", self._why(e))
                if not self._local_usable():       # 同 `fix_and_translate_stream` 那段
                    if not seen:
                        return self._retry_cloud(
                            lambda: self._cloud.fix_stream(en, context, self._tap(on_en, seen)))
                    self._count_failure(e)
                    raise
                self._fallback(e)
            else:
                self._cloud_ok()
                return res
            if seen:                              # 同上：半句已上屏，本地不再接
                return self._local.fix_stream(en, context, None)
        return self._local.fix_stream(en, context, on_en)

    def add_terms(self, words) -> list:
        """把课上候选词并进**两个**引擎的候选池（链路体检 ⭐2）。

        ⚠️ 两个都喂：`_fallback` 一次降级之后，后面那些句子读的是**本地**那份池子；
           只喂当前在用的那个 = 一降级就把这节课的词全丢了（而本文件头写着降级是
           **常态**：中国用户 8 节课 16 个洞）。
        ⚠️ 任一引擎报错**不影响另一个**，也绝不抛给调用方 —— 它只是锦上添花。
        """
        new: list = []
        for eng in (self._cloud, self._local):
            if eng is None or not hasattr(eng, "add_terms"):
                continue
            try:
                new += list(eng.add_terms(words) or ())
            except Exception:                                 # noqa: BLE001
                continue
        return new

    @property
    def uses_cloud(self) -> bool:
        """只读: 当前是否走云端(草稿译文线程据此决定要不要给定稿让路)。"""
        return self._use_cloud

    def translate_draft(self, en, on_zh=None, context=None) -> str:
        """草稿译文（高频）。⚠️ 云端失败**一律不走 `_fallback`**（2026-10-15）。

        草稿每几秒一条，一次失败就翻 `_use_cloud` 会把**定稿**那条路也一起降级 ——
        而草稿失败完全说明不了定稿链路有问题（同 `answer` / `hint` 的那个决定）。
        本地可用就退本地；本地不可用（大多数用户）返回 `""`：调用方
        `draft_translate.DraftTranslator` 把 `translate` 当「往回调里吐增量」的接口，
        返回值只有 `_once` 用、而它自己就把空当「这轮没译出来」（不落屏、不计数）。
        """
        if self._use_cloud:
            try:
                return self._cloud.translate_draft(en, on_zh, context=context)   # 本地版不收 context
            except Exception:                     # noqa: BLE001
                if not self._local_usable():
                    return ""
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

    def merge_translate(self, en, context, on_zh=None, on_en=None):
        """跨段半句合并的重翻（3.9.7 第 4 项）。只走云端；本地 / 失败一律返回 `None`
        （`fragment_merge.translate_merge` 把它当「没合成」，屏上原样）。

        ⚠️ 和 `hint` 同一个决定：**不走 `_fallback`、不进连败计数、不重试**。合并是锦上添花，
        它失败一次就把整节课的定稿翻译降级 / 起计费探针，是拿主路给支路陪葬。
        ⚠️ 也**不退本地**：本地 mlx 模型此刻正被定稿线程用着，两个线程并发推理会互相卡住。
        回调一律不给（合并结果是原子替换，不流式）。
        """
        if not self._use_cloud:
            return None
        try:
            return self._cloud.fix_and_translate_stream(en, context, None, None)
        except Exception:                         # noqa: BLE001
            return None

    def hint(self, lines) -> str:
        """❓ 一行点拨。本地 / 失败一律返回 `""`(= 调用方改显示「课后笔记里能找到」)。

        ⚠️ 和 `answer` 同一个决定: 本地 1.7B 只会编, 不讲; 云端抛错也**不走 `_fallback`** ——
        一次点拨失败不能把整节课的翻译降级到本地(`_fallback` 会翻 `_use_cloud`、
        起 90s 计费探针, 提示语还写死"云端翻译失败", 在这里是误导)。"""
        if not self._use_cloud:
            return ""
        try:
            return self._cloud.hint(lines)
        except Exception:                         # noqa: BLE001
            return ""


def live_terms_step(pool, router, finals) -> list:
    """已定稿文本 -> 过门的候选词 -> 翻译侧候选池（链路体检 ⭐2 的**接线点**）。

    返回这次**新进池**的词（空列表 = 没到步长 / 没有新词）。**绝不抛** ——
    它跑在定稿线程里，是额外一层（同 `close()` 那八层的纪律）。

    ⚠️ 单独一个函数而不是写进 `_emit`：`_emit` 有两百行、三条 `continue` 分支
       （纯转录 / 只英·校 / 翻译），接线点在四条路上都要走到；抽出来既能单测，
       也**只改一处**（那三条分支的尽头都是 `finals.append(...)`，见调用点）。
    """
    if pool is None or not finals:
        return []
    try:
        got = pool.update(finals)
    except Exception:                                         # noqa: BLE001
        return []
    if not got:
        return []
    if router is not None:
        try:
            router.add_terms(got)
        except Exception:                                      # noqa: BLE001
            pass
    return got


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
        if zh:                                   # 空串 = "清掉上一段的中文草稿", 终端没有可清的
            echo(f"[{now()}]   ↳ {zh}")

    def terms(self, hits):
        echo(f"[{now()}]   {format_gloss(hits)}")

    def stream_zh(self, delta: str):
        self._zh += delta

    def stream_en(self, delta: str):
        self._en += delta

    def finalize(self, en: str, zh: str, *, clear_draft: bool = True):   # clear_draft 终端无草稿槽, 忽略
        echo(f"[{now()}] ✅ {en}\n         🌐 {zh}")
        self._zh = self._en = ""

    def show_hint(self, text: str, ttl: float):
        if text:                                 # 终端没有"术语格", 点拨整行打出来; 不需要到期
            echo(f"[{now()}] {text}")

    def answer_delta(self, delta: str):
        # 只累加不上屏: 与 stream_zh 同规矩 —— 完整回答由 drain() 一次性 echo
        # (逐 delta 打印会和字幕行交错成一片糊)。
        self._answer += delta

    def answer_done(self, question: str, text: str):
        self._answer = ""

    def notice(self, msg: str, warn: bool = False, still: str = ""):
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

    def wrapup_open(self):
        pass

    def wrapup_begin(self, cancel):
        # 终端那条路没有可点的按钮 —— 收尾要提前结束就是 Ctrl+C，
        # 而 Ctrl+C 由 `run()` 的收尾循环接住并置同一个 `cancel`。
        # （2026-10-12：悬浮窗上那个 [跳过精修] 按钮也删了，两边现在都没按钮。）
        pass

    def wrapup_done(self, ok: bool, msg: str):
        pass

    def wrapup_acknowledged(self) -> bool:
        # 终端那条路结论是直接 print 的，没有「看没看见」这个概念 —— 恒 True，
        # 于是 main 的「失败留住」循环一次都不转，与今天的行为一致。
        return True

    def wrapup_close(self):
        pass


class _WakeQueue(queue.Queue):
    """`put` 之后**自动叫醒主循环**的 Queue（3.9.7 省电）。

    主循环的让步在「什么都没在动」时是 0.1 s、窗口不可见时 0.25 s（见 `overlay` 的三档），
    新数据必须**立刻**把它打断 —— 否则字幕要等满那一档才上屏。waker 由 `main.run()`
    在 UI 建好之后装上（`overlay.wake()` 是**跨线程安全**的：Apple 文档
    `NSApplication.postEvent(_:atStart:)` 说它可在子线程调）；**没装上 = 与 `queue.Queue`
    逐字相同** —— check.py 的替身与老测试都直接把它当 Queue 用，功能一个字不变。

    ⚠️ 只覆盖 `put` / `put_nowait`（这两个是全仓实际用到的写入口）：
       `all_settled()` 读 `empty()`、`drain()` 读 `get_nowait()`，都不受影响。

    ⭐ 3.9.8 ②（唤醒延迟）：另有一格**只在装了 stats 回调时才活**的记账 —— 每条 item 从
    `put` 到被 `get_nowait()` 取出的停留时间（毫秒）。答的是「空闲档 0.1 s / 隐藏档
    0.25 s 有没有把字幕上屏拖慢」。
    ⚠️ **item 的形状一个字没改**（R28 / `drain()` 分发不许动）：时间戳放在**与队列同序的
       一个平行 deque** 里，`put` / `get_nowait` 在**同一把锁**里成对进出，顺序因此恒等。
    ⚠️ 没装 stats = `_put_t is None`：**连 deque 都不建**、每轮只多一次 `is None` 判断。
    """

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._waker = None
        #: ⭐ 3.9.8 ②：`None` = 没装唤醒延迟统计（= 逐字相同的旧行为）。
        self._stats = None
        self._put_t = None
        self._t_lock = None

    def set_waker(self, fn) -> None:
        """装/换唤醒回调（`None` = 退回普通 Queue 行为）。"""
        self._waker = fn

    def set_stats(self, fn) -> None:
        """装/卸唤醒延迟回调 `fn(dwell_ms)`；`None` = 退回**逐字相同**的旧行为。

        ⚠️ 装了才建时间戳 deque 与那把锁；卸了就把它们丢掉（`_put_t = None`）。
        """
        self._stats = fn
        self._put_t = collections.deque() if fn is not None else None
        self._t_lock = threading.Lock() if fn is not None else None

    def _wake(self) -> None:
        w = self._waker
        if w is None:
            return
        try:
            w()
        except Exception:                                  # noqa: BLE001
            pass

    def put(self, item, block=True, timeout=None) -> None:
        if self._put_t is None:                            # 没装 stats：与旧实现逐字相同
            super().put(item, block, timeout)
            self._wake()
            return
        # ⚠️ 时间戳与 item 必须**成对、同序**：放在同一把锁里；且时间戳在 `_wake()`
        #    之前入账 —— 否则主线程被叫醒后可能先取到 item、再弹到一个空 deque。
        with self._t_lock:
            super().put(item, block, timeout)
            self._put_t.append(time.monotonic())
        self._wake()

    def put_nowait(self, item) -> None:
        # ⚠️ **不再自己 `_wake()`**：`queue.Queue.put_nowait()` 内部就是
        #    `self.put(item, block=False)` —— 已经走过本类那个 `put`。
        #    多叫一次 = 每次 `put_nowait` 投两个唤醒令牌（判据钉着"一次一条"）。
        super().put_nowait(item)

    def get_nowait(self):
        """取一条并（装了 stats 时）记一次停留时间。**取不到照抛 `queue.Empty`。**

        ⚠️ 消费端只有这一处（`drain()`）—— 它没有 try/except，所以这里除了
           `queue.Empty` **一个异常都不许外抛**：统计自己失败只该少一个数。
        """
        if self._put_t is None:
            return super().get_nowait()
        with self._t_lock:
            item = super().get_nowait()
            t = self._put_t.popleft()
        if self._stats is not None:
            try:
                self._stats(round((time.monotonic() - t) * 1000.0, 1))
            except Exception:                              # noqa: BLE001
                pass
        return item


class _Latest:
    """latest-wins 槽位: 草稿只保留最新一份, 旧的自然丢弃。"""

    def __init__(self):
        self._buf = None
        self._t = 0.0
        #: 上一次 `take()` 拿到的那份是**何时 `put` 进来的**（`time.monotonic()`）。
        #: ⚠️ 只给**唯一的消费者**（`partial_worker`）在 `take()` 之后紧接着读 ——
        #:    它和 `take()` 在同一把锁里一起换，所以不会读到下一份的时刻。
        self.taken_t = 0.0
        #: 同上，上一次 `take()` 那份的**段号**（`put` 那一刻的 `seg_gen`；R3 流式草稿按它分流）。
        self.taken_tag = None
        self._tag = None
        self._end = None
        self._ev = threading.Event()
        self._lock = threading.Lock()
        self._closed = False        # 停机标志: `stop()` 叫醒之后 `take()` 一律返回 None

    def put(self, buf, tag=None) -> None:
        with self._lock:
            self._buf = buf
            self._t = time.monotonic()
            self._tag = tag
        self._ev.set()

    def take(self, timeout: float = None):
        """等一份缓冲。`timeout=None` = 一直等（`stop()` 会叫醒它）。

        ⚠️ 返回 `None` 有**两个**原因：超时（不传 timeout 时没有）与 `stop()` ——
           所以消费者必须看 `stopped`，别把停机当成"暂时没缓冲"。
        """
        if not self._ev.wait(timeout):
            return None
        with self._lock:
            b, self._buf = self._buf, None
            self.taken_t = self._t
            self.taken_tag = self._tag
            self._ev.clear()
            return b

    def put_end(self, buf, tag) -> None:
        """段落结束：整段缓冲 + 段号。**不被后来的 `put` 顶掉**（单独一格），并叫醒 `take()`。"""
        with self._lock:
            self._end = (buf, tag)
        self._ev.set()

    def take_end(self):
        """取走待收尾的那一段 `(buf, tag)`；没有 → `None`。与 `take()` 同一个消费者。"""
        with self._lock:
            e, self._end = self._end, None
            return e

    def stop(self) -> None:
        """停机：叫醒阻塞在 `take()` 上的消费者，并让它知道这是**停机**（见 `stopped`）。"""
        with self._lock:
            self._closed = True
        self._ev.set()

    @property
    def stopped(self) -> bool:
        return self._closed


def _changelog_text() -> str:
    """本地 CHANGELOG.md 全文；读不到返回空串。⚠️ 路径跟着**本文件**走（测试会把 main.py 拷进假世界）。"""
    try:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "CHANGELOG.md")
        return open(p, encoding="utf-8").read()
    except Exception:                                     # noqa: BLE001
        return ""


def _changelog_section(version: str) -> str:
    """取本地 CHANGELOG 里某个版本的正文。解析在 `changelog.section`。"""
    return changelog.section(_changelog_text(), version)


def _changelog_date(version: str) -> str:
    """版本日期。解析在 `changelog.date`。"""
    return changelog.date(_changelog_text(), version)


def _changelog_summary(version: str, max_lines: int = 6) -> str:
    """更新卡片上显示的那几行（本地 CHANGELOG）。规则全在 `changelog.summary`。"""
    return changelog.summary(_changelog_section(version), max_lines)


def _reorder_log(text: str) -> str:
    """见 `changelog.reorder`。"""
    return changelog.reorder(text)


def _changelog_full() -> str:
    """CHANGELOG.md 全文（卡片里的可滚动日志用），去掉文件开头写给维护者的约定。见 `changelog.entries`。"""
    return changelog.entries(_changelog_text())


def _changelog_versions(text: str | None = None) -> list[str]:
    """CHANGELOG.md 里所有版本号，**新到旧**。解析在 `changelog.versions`（更新决策同用这一份）。"""
    return changelog.versions(_changelog_text() if text is None else text)


def _update_mode_in(text: str, version: str) -> str:
    """该版本能否后台自动应用。见 `changelog.update_mode`。"""
    return changelog.update_mode(text, version)


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


def _draft_model_on_exit() -> None:
    """**下课之后**在独立进程里下那个可选的流式草稿模型（464 MB）。父进程立刻返回。

    ⚠️ 为什么是"下课之后"而不是"上课途中"（Owen 2026-10-12 定）：464 MB 会和云端定稿的
       音频上传抢同一条教室 Wi-Fi —— 正好压在最不该压的时刻。所以只在收尾的最末起，
       而且 `start_new_session=True`：用户下一句就退出程序，它照样在后台下完。
    ⚠️ 与自动更新**共用** `CLASSLIVE_NO_AUTO_UPDATE` 那道闸 —— 两者都是「退出时自己在后台
       去做的大活」，判据里必须一起关掉（否则跑一次测试就真去下 464 MB）。
       复用而不是新开一个开关，也是为了让 `docs/HANDBOOK.md` §6.1 的登记表不增项。
    ⚠️ 「该不该下」不在这里判 —— 子进程里的 `run_step(DRAFT_MODEL)` 自己会判（幂等）。
    """
    if os.environ.get("CLASSLIVE_NO_AUTO_UPDATE"):
        return
    try:
        import update as _u
        _u.spawn_draft_model_download()
    except Exception:                                     # noqa: BLE001
        pass                                              # 绝不因为这件事影响退出


def _whatsnew_body(seen: str, cur: str, max_lines: int = 7) -> str:
    """该给用户看的内容 —— **`(seen, cur]` 这个区间**。规则（跨版本合并、上界是 cur）见 `changelog.between`。"""
    return changelog.between(_changelog_text(), seen, cur, max_lines)


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
      · 悬浮窗模式 → **不弹**：2026-10-14 起「已更新到 X」改在开课面板上弹（`entry_panel` + whatsnew.py，
        每版一次），课上悬浮窗旁边那张长日志卡取消了；
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
    """CHANGELOG 里 `cur` 的前一版。见 `changelog.prev_version`。"""
    return changelog.prev_version(_changelog_text(), cur)


def _ver_key(v: str) -> tuple:
    """版本号排序键（`3.10.0` 在 `3.9.0` 之后）。见 `changelog.ver_key`。"""
    return changelog.ver_key(v)


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
    """[已弃用] 原来的 NSAlert 弹框。保留仅为兼容，现在只印终端字符框。"""
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
             f"—— 下次开课前在面板上点「更新」（终端：cl update）")
        with open(stamp, "w", encoding="utf-8") as f:
            f.write(cur)
        return payload
    except Exception:                               # noqa: BLE001
        return None                                 # 提示而已, 任何失败都静默


#: 收尾等待的**兜底上限**（秒）。43 分钟的课转码正常约 21 秒 → 600s 是 20 倍余量。
#: ⚠️ 为什么必须有（2026-10-07 独立复核指出）：把"窗口一关就秒退、把后台的活丢下"
#:    修好之后，换来了一个**新**风险 —— 窗口已经关了时 `spin_until` 只等 `until()`，
#:    万一后台那步卡住（`_to_opus` 每段 180s 超时、而段数不限），进程**永不退出**，
#:    且**没有界面可点**。宁可超时后报一声、也不许挂死。
SPIN_MAX_S = 600.0


def spin_until(until, tick, ui_gone, *, max_wait_s: float = SPIN_MAX_S) -> None:
    """守着 `tick()` 等 `until()` 为真，或**用户此刻**关窗。**纯逻辑，可测。**

    `tick()` = 一轮等待（浮窗模式是 `ui.pump()`，终端模式是 `time.sleep`）。
    `ui_gone()` = 窗口关了吗。

    ⚠️⚠️ **进来时窗口已经关了，就不再把"关窗"当退出条件**（2026-10-07 实测定位）。
       浮窗模式的**正常停止就是 ✕**（`overlay.py` 自己写着"✕ 就是退出口"），所以收尾
       跑到等 `tester.finish()` 那一步时，`_closed` **恒为真** → 循环体一次都不跑 →
       立刻返回 → 后台那份活被丢下 → 主线程走到进程尾 → **daemon 线程被杀**。
       ⚠️ 代价实测：**测试报告从 2026-09-24 之后再没写成过**（≈90 节；`report.json`
       一个都没有），而 `update.log` 还照样打着「数据包生成失败，但报告已写出」——**那是假话**。
       10-06 那次留下了"转到一半"的现场：`.001.opus` 生成了、`.001.wav` 却没被删
       （`_to_opus` 转成功**必删** wav）→ 正是**转码途中被杀**。
       → 窗口已经关了就没有"冻结界面"可言，**该等就得等完**；窗口还开着时才用关窗当退出口。
    ⚠️ 但**等要有上限**：`max_wait_s` 到点就报一声放行，见 `SPIN_MAX_S`。
    """
    wait_ui = not ui_gone()
    t0 = time.monotonic()
    while not until() and not (wait_ui and ui_gone()):
        if max_wait_s and time.monotonic() - t0 >= max_wait_s:
            print(f"⚠ 收尾等待超过 {max_wait_s:.0f} 秒还没完 —— 不再等，接着走"
                  f"（那一步的结果可能不完整）", flush=True)
            return
        tick()


def cloud_asr_off_reason(*, cfg_enabled: bool, engine: str, mode: str,
                         source: str, speed: float) -> str | None:
    """这一刻定稿该不该上云。`None` = 该上; 否则是人话原因(启动时打一行)。

    由 `final_asr.FinalASR` **每段调用一次**(上课中途切到「纯转录」→ 下一段就不上云)。
    ⚠️ `mode` 来自 `trans["mode"]`: 另一个线程在写、这里单键读 —— CPython 下 dict 单键读写
       各自原子, 读到旧值最多晚一段生效, 不需要锁。
    ⚠️ 纯转录档照样不上云, 但**不能**据此向用户保证「纯转录 = 音频不出本机」: 每节课都从
       `both` 开始(`trans` 初值), 切换之前的几段已经发出去了(R13)。保证只有开关 / 环境变量。
    """
    if final_asr.cloud_forced_off():
        return "CLASSLIVE_CLOUD_ASR=0"
    if not cfg_enabled:
        return "云端转写开关已关"
    if engine == "local":
        return "--engine local"
    if mode == "raw":
        return "纯转录档"
    if source == "file" and speed > 1:
        return f"文件倍速回放 x{speed:g}(几秒就会打满云端限额)"
    return None


def resolve_cloud_asr_enabled(*, read=final_asr.read_config, write=final_asr.write_config,
                              alert=notice.alert, can_alert=notice._can_alert,
                              say=echo) -> bool:
    """配置里云端转写开不开; 没告知过就告知。**必须在开始采音之前**调用。

    · `.app`(`can_alert()` 为真): 弹窗(只有「好的」一个按钮, Owen 2026-10-11 定 —— 想关去面板开关), 点过才 `write(True)`。
    · 终端: **只打一行提示, 不弹、不写 `acknowledged`**(R14) —— 否则终端启动一次就等于替
      `.app` 用户同意了; 终端用户想关用 `CLASSLIVE_CLOUD_ASR=0`。
    · `CLASSLIVE_CLOUD_ASR=0`: `read` 返回「关 + 已告知」, 这里什么都不做(R7)。
    """
    cfg = read()
    if not cfg["enabled"]:
        return False
    if cfg["acknowledged"]:
        return True
    if not can_alert():
        say("☁ 定稿转写默认走云端: 音频(Opus 压缩)经作者服务器转发给 Groq(美国)/阿里云百炼(中国)。"
            "不想上云: CLASSLIVE_CLOUD_ASR=0 启动")
        return True
    alert("定稿转写会用云端", final_asr.consent_text(), buttons=("好的",))
    write(True)
    return True


def run(args) -> None:
    # ⏱ 起步计时（2026-10-14 作者：「点开始上课之后字幕卡片弹出的速度」）—— 先量再改。
    #    各段用 `_tp` 记单调时钟；点按时刻读面板留的一次性戳（`_click_wall`）。
    #    ⚠️ 戳在**这里**就读掉（不是等悬浮窗起来再读）：下面被单实例锁拦下、模型缺失等提前 return 的路
    #       也要把它消耗掉，不然下一次从终端起会捡到这次的点按。
    _tp = {"run": time.monotonic()}
    _t_wall0 = time.time()
    _t_click = _click_wall()
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
        if instance_lock.poke(_holder0):
            echo("   已经把它的字幕叫到前面来了（藏着 / 穿透着也一样）。")
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

    # ---- 3.9.3 的一次性搬家：把老的翻译 key 挪到旁边 ----
    # ⚠️ 必须在**任何一处读 key 之前**（`setkey` / `load_translator` / 就绪条都算）。
    # ⚠️ 幂等：版本标记挡着第二次；而且它**从不碰 `cl-` 通行码**，
    #    所以和 `entry_launch` 那条路重复调用无害。
    _moved = keyentry.clear_legacy_key()["moved"]
    if _moved:
        echo("🔑 翻译的 Key 换新了 —— 旧的已经挪到旁边（" + "、".join(_moved) + "）")
        echo("   找作者拿一个新 Key，在开课面板上点「翻译引擎」填进去。")

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
    _tp["audio"] = time.monotonic()

    # 草稿/兜底用 Parakeet, **必需**: 缺了在这里说清楚然后退出, 不在课上静默降质。
    # 定稿(云端优先, Whisper 只是可选的本地兜底)在 `trans` 定义之后装配, 见下面 `final_asr.load_final`。
    # ⚠️ 先只核对文件（`lazy=True`）：草稿走 Nemotron 时 Parakeet 只是兜底，用到才加载（省 ~1.1 GB）。
    try:
        asr = load_asr(args.model_dir, lazy=True)
    except Exception as e:                       # noqa: BLE001
        notice.alert("模型没装好",
                     f"{e}\n\n装好再跑 —— `cl doctor` 会列出缺哪个、该跑哪条命令。")
        return
    # 草稿引擎（R3）：流式 Nemotron 优先，缺模型 / 内存不够 / 加载失败 → 照旧 Parakeet 滑窗。
    # ⚠️ 放在上面那个 try **之外**：它永不抛，失败也不该 `return`。Parakeet 仍是
    #    定稿的本地兜底（下面装配定稿时传进去），也是草稿熔断后的退路 —— 只是晚点加载。
    # ⭐⭐ 3.9.7 第 5c 项：**流式模型在后台线程建**（约 1.28 s 原来卡在这里，悬浮窗 /
    #    采音 / 定稿都得等它）。就绪前的草稿一律跳过（`partial_worker` 那边 `not t →
    #    continue`），**定稿不受任何影响**（它不走草稿引擎）。加载失败 → 既有兜底
    #    （Parakeet），`partial_worker` 会照「流式草稿已停用」那条路出声一次。
    def _draft_ready(ok: bool) -> None:
        """⏱「草稿模型就绪」的时刻。⚠️ **从加载线程被调** —— 只许记一个时刻。"""
        _tp.setdefault("draft_ready", time.monotonic())

    draft = draft_engine.load_draft_engine_async(
        asr, window_samples=PARTIAL_MAX_S * SR, on_ready=_draft_ready)
    if draft.name == "parakeet":
        # 草稿每秒都要它 → 现在就加载；文件坏在这里说清楚，别拖到课上。
        try:
            asr.get()
        except Exception as e:                   # noqa: BLE001
            notice.alert("模型没装好",
                         f"{e}\n\n装好再跑 —— `cl doctor` 会列出缺哪个、该跑哪条命令。")
            return
        # Parakeet 没有被后台加载那一段 —— 它的就绪时刻就是刚才这一下。
        _tp.setdefault("draft_ready", time.monotonic())
    else:
        echo("💤 Parakeet 用到再加载（草稿走 Nemotron；云端失败时第一次兜底多等约 1 秒）")
    _tp["draft"] = time.monotonic()
    echo(f"✍️ 草稿引擎：{draft.name}" + (f"（{draft.reason}）" if draft.reason else ""))

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

    _tp["translator"] = time.monotonic()
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
    # ⭐ 定稿 ASR: 云端优先、本地兜底(`final_asr.py` 文件头)。
    #    ⚠️ 必须在 `trans` 之后: `_cloud_asr_ok` 读 `trans["mode"]`; 也必须在**开始采音之前**
    #       (首次告知要先于第一段音频发出)。
    _cloud_cfg_on = resolve_cloud_asr_enabled()

    def _cloud_asr_off_reason() -> str | None:
        return cloud_asr_off_reason(cfg_enabled=_cloud_cfg_on, engine=args.engine,
                                    mode=trans["mode"], source=args.source, speed=args.speed)

    def _cloud_asr_ok() -> bool:
        return _cloud_asr_off_reason() is None

    _why_off = _cloud_asr_off_reason()
    final = final_asr.load_final(args.final_model_dir, asr, allow_cloud=_cloud_asr_ok,
                                 preload_local=_why_off is not None,  # R4: 云端开着就不加载 Whisper
                                 is_stopping=stopping.is_set,         # 收尾冲刷时云端截止更短
                                 passcode=api_key_val,                # 通行码 `cl-…` 才会用上（一人一码）
                                 # `notify` 在下面才定义; 熔断只会在开课后说话, 那时早已定义好
                                 notify=lambda m, w: notify(m, w, src="转写"))
    echo("☁ 定稿: 云端(Groq → 千问 → 本地 Parakeet)" if _why_off is None
         else f"💻 定稿: 本地({_why_off})")
    # ⚠️ 库路径在这里**收口**（`resolve_vault` 是唯一定义点）：`--vault` → `$OBSIDIAN_VAULT`
    #    → 上次用过的。**没有兜底目录** —— 拿不到就不写 Obsidian，绝不凭空造一个。
    #    拿到了就**记下来**：双击 `.app` 起的那条路没有 shell 环境变量，不记下次还是找不到。
    vault = resolve_vault(args.vault)
    # ⭐2（2026-10-17，链路体检）：**课上候选词池** —— 已定稿文本里反复出现、过了门的词
    #    并进翻译侧的候选池（`extra_terms`）。起点是上面那批 `corpus` 转录词。
    #    ⚠️ 课件核对（`verify`）**懒加载**：这里在启动路径上，而 `Materials.for_course`
    #       要把本课材料读一遍 —— 挪到第一次抽取时再建，别让上课启动多花那几百毫秒
    #       （`tests/test_startup_timing.py` 盯着这条线）。
    #    ⚠️ **不碰 Soniox**：它的 context 是会话开始时定的，改要重开会话（`live_terms`
    #       文件头有查证），所以这一课的词只靠 `glossary/<课号>.txt` 在**下节课**带上。
    #    ⚠️ 建不起来 / 抽不到词都**只影响术语召回**，绝不挡开课（同 `extra_terms` 那条）。
    live_pool = None
    if args.course and vault:
        try:
            import live_terms as _lt
            import note_glossary as _ng

            def _verify_factory(_c=args.course, _v=vault):
                import materials as _mt
                return _mt.Materials.for_course(_v, _c).verify

            live_pool = _lt.LivePool(
                initial=extra_terms, verify_factory=_verify_factory,
                deleted=_ng.deleted_terms(args.course, glossary_txt=args.glossary))
        except Exception as _e:                       # noqa: BLE001
            print(f"⚠ 课上候选词池没建起来（只影响术语召回）：{type(_e).__name__}: {_e}")
    if vault:
        remember_vault(vault)
    elif args.save_notes != "no":
        print("⚠ 没设 Obsidian 库路径(设上 OBSIDIAN_VAULT, 或用 --vault 指定) —— "
              "这次只写 sessions/, 不写 Obsidian 笔记")

    # 🎯 Jev 的重点句（2026-09-29）—— **收尾那一步**才跑，而且是 fail-soft。
    # ⚠️⚠️ **纯转录档一个模型请求都不发** —— 仓库的硬契约（`DESIGN.md:187`：
    #    「纯转录 | 一个模型请求都不发」）。所以挡在**发请求之前**。
    # ⚠️ 没配 token → 空表（= 功能关着，不是错误）。同 `polish` 的 fail-soft。
    # ⚠️ 同步跑，一节课实测约 6 秒 —— 收尾可以接受（那一步本来就在等复习层）。
    def _keypoints_for(sents):
        if args.save_notes == "no" or trans.get("mode") == "raw":
            return []
        try:
            import keypoints as _kp
            return _kp.pick(sents)
        except Exception:                                     # noqa: BLE001
            return []

    # ❓ 课后讲解卡（计划 §G2）—— 同 `_keypoints_for`：收尾那一步才跑、fail-soft、
    #    纯转录档 / 没 key 一个请求都不发（writer 那边另外挡了 `cancel`）。
    def _lost_explain_for(blocks):
        if args.save_notes == "no" or trans.get("mode") == "raw" or not api_key_val:
            return []
        try:
            import polish
            from translator import course_term_list, course_title
            return polish.explain_lost(
                blocks, api_key_val, args.polish_model or args.cloud_model,
                course_term_list(args.glossary, args.course),
                course_title(args.glossary, args.course))
        except Exception:                                     # noqa: BLE001
            return []

    # 🩹 漏译句补译（3.9.7 第 2 项）—— 同 `_keypoints_for` / `_lost_explain_for`：
    #    收尾那一步才跑、fail-soft，且**两条都由这里挡在发请求之前**：
    #      · `cloud_tr is None` = 这节课本来就不走云端（没 key，或用户选了本地引擎）
    #        —— 为补几句中文把文本发出去是**改他的选择**，不许；
    #      · 只有**双语档**才补：纯转录档是「一个模型请求都不发」的硬契约
    #        （`DESIGN.md`），只英·校档的用户明确不要中文。
    #    ⚠️ 档位取的是**收尾那一刻**的档（课中可以切）。从双语切走再切回来的课，
    #       中间那几段的漏译句拿不到补译 —— 宁可少补，不许把别的档的句子翻出来。
    def _backfill_for(entries):
        new, st = entries, {"need": 0, "done": 0}
        # ⚠️ 课中**切过**档（只英 / 纯转录）就整节不补：那段的「只有 ASR」是用户要的，
        #    补译会破「纯转录零请求」的承诺；只看收尾那一刻的档位分不出来。
        if (args.save_notes != "no" and trans.get("mode") == "both"
                and not trans.get("ever_off") and cloud_tr is not None):
            try:
                import backfill
                from translator import course_term_list, course_title
                new, st = backfill.backfill_entries(
                    entries, api_key_val, args.polish_model or args.cloud_model,
                    course_terms=course_term_list(args.glossary, args.course),
                    domain=course_title(args.glossary, args.course))
            except Exception:                                 # noqa: BLE001
                new, st = entries, {"need": 0, "done": 0}
        if tester is not None:              # 没跑（档位不对 / 没库）也记 0/0 —— 不是"没采到"
            tester.note_block("backfill", st)
            # ⭐ 3.9.8 ③：把**真补出来的那几句**的原文也记下来（`backfill_entries` 只把补过的
            #    那几条换成新 dict，`is` 比得出来；入参一个没改）。
            try:
                tester.note_backfill_items(
                    [{"en": n.get("en") or "", "zh": n.get("zh") or ""}
                     for o, n in zip(entries, new) if n is not o])
            except Exception:                                 # noqa: BLE001
                pass
        return new, st

    # 🧭 课前回顾（2026-10-17）：收尾时把这一节的产物压成**下一节开课时**要显示的那几行。
    #    ⚠️ **额外调用的上限是 1 次**（`recap.build` 里那一次），而且：
    #      · 纯转录档 / `--save-notes no`：**一个请求都不发**（`DESIGN.md` 的硬契约，
    #        与 `_keypoints_for` / `_lost_explain_for` 同一条规矩）；
    #      · 没 key：不调模型，只存骨架（知识点那一行就没有解释）；
    #      · 失败：笔记照写（它是第九层，`close()` 里兜着）。
    def _recap_for(payload: dict) -> dict:
        import recap as _recap
        try:
            if args.save_notes == "no" or trans.get("mode") == "raw":
                return {}
            data = payload if isinstance(payload, dict) else {}
            prev = _recap.load(args.course or "")
            duties = _recap.merge_duties(
                (prev or {}).get("duties"),
                _recap.duties_from_items(duty_board.items(), day=_duty_day))

            def _chat(system, user, max_tokens):
                import llm_client
                return llm_client.chat_json(api_key_val, args.cloud_model, system,
                                            user, max_tokens=max_tokens,
                                            temperature=0.3, timeout=120.0)

            return _recap.build(
                course=data.get("course") or "", date=data.get("date") or "",
                session=data.get("session") or "",
                chapters=(data.get("chapters") or {}).get("chapters") or [],
                keypoints=data.get("keypoints") or [],
                lost=[x for x in (data.get("lost") or [])],
                duties=duties, tail=data.get("tail") or "",
                chat=(_chat if api_key_val else None))
        except Exception as e:                            # noqa: BLE001
            echo(f"⚠ 课前回顾没生成（笔记照写）：{type(e).__name__}: {str(e)[:60]}")
            return {}

    writer = ObsidianWriter(vault, args.course, mode=args.save_notes,
                            api_key=api_key_val, model=args.cloud_model,
                            glossary_path=args.glossary,
                            polish=args.polish != "off",
                            polish_model=args.polish_model or None,
                            keypoints_fn=_keypoints_for,
                            lost_explain_fn=_lost_explain_for,
                            backfill_fn=_backfill_for,
                            recap_fn=_recap_for)
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
        # ⭐ 原信号快照（异常段才写）默认**跟随音频那个决定** —— 卡片的「测试模式」开关
        #    只传 `--test-mode`，不能给人加旗标；若它默认关，这一格**在真实使用里永远采不到**
        #    （实测：`cl test` 走的就是这条路）。它比 opus 更"原始"但不含**新的内容类别**
        #    （同一段音频），所以跟随 `--record-audio` 是连贯的；不想要就 `--no-record-float`。
        _rf = (args.record_float if args.record_float is not None else _rec)
        tester = TestSession(getattr(writer, "session_path", None),
                             record_audio=_rec,
                             record_float=_rf)
        echo(f"🧪 测试模式: 报告将写到 {tester.stem}.report.json")
        # ⭐ 3.9.7：机器画像（内存 / 芯片 / 草稿引擎）—— 答「有没有低内存用户」靠它。
        tester.note_block("system", system_info(draft.name, draft.reason))
        # ⚠️ 提醒**放在启动时**：收尾才说就晚了，那时整节课已经录完。
        #    （2026-09-30 起测试模式下默认录 —— 见上面 `_rec` 那段。）
        if tester.record_audio:
            # 9–13MB：同一段 600 s 实测 ffmpeg 1.74 MB / soundfile 兜底 2.59 MB（×5 = 50 分钟）。
            echo("   ⚠️ 会录制**课堂音频**(分段 Opus, 约 9–13MB/50 分钟), 收尾打成一个 zip。")
            # ⚠️⚠️ **必须说"自动"**（2026-10-07）：原句是「发出去前请自己确认」——
            #    读起来像"你要手动发"，而 `start_upload`(收尾那段) 是**自动排队 + 后台就传**。
            #    披露与事实不一致，等于没披露。想关掉：`CLASSLIVE_UPLOAD=0`。
            echo("      音频与逐字转录可能含**其他同学的声音** —— 收尾会**自动上传**给作者"
                 "（Cloudflare R2）。想关掉: `CLASSLIVE_UPLOAD=0`。")
        else:
            echo("   ℹ️ 只采指标, 不留常规音频（要留: 加 `--record-audio`）。")
        if tester.record_float:
            # ⚠️ 这一条**必须单独说**（2026-10-07 OCR 指出）：在
            #    `--no-record-audio --record-float` 的组合下，上面那句"不留常规音频"
            #    很容易被读成"什么都没留"，而异常段其实会写一份**未压缩的原始信号**
            #    —— 它比 opus 更"原始"，披露得说清楚，否则提示话与事实打架。
            echo("   ⚠️ 异常段（主模型空/退化）会另存一份**送进 ASR 的原始信号**"
                 "（`<会话名>.probe/*.npy`，只写本机、不上传）。")

    # ⭐ 现场采集（`livemetrics.py`）：把 drain() 那个缝上发生的事记成 JSONL。
    #    **普通上课默认关**（`CLASSLIVE_LIVE_LOG=1` 才开）；**测试模式默认开**
    #    （App 里点「测试模式」= `--test-mode`，那就是要数据；`CLASSLIVE_LIVE_LOG=0` 仍可关）。
    #    ⚠️ 它自己吞所有异常、队列有界、写盘在后台线程（见该文件头）。
    live = livemetrics.open_for(getattr(writer, "session_path", None),
                                default=bool(args.test_mode))
    if live is not None:
        echo(f"🎥 现场采集: {live.path}")
        live.event("draft_engine", name=draft.name, reason=draft.reason)

    # ⚠️ 元素是 `(原始假设, 上屏文本, 缓冲秒数, 转写耗时, 段号, put 时刻, 引擎)`（2026-10-09 起；引擎是 R3 加的）：
    #    前缀冻结要拿"模型原始假设"跟"上屏的那份"做**同场 A/B**，两个都得留；
    #    后三个是现场采集要的（缓冲滑没滑 / 转写多久 / 归哪一段）。
    #    `drafts` 只有这里一个产出方、`drain()` 一个消费方，改形状是局部的；
    #    `drain()` 侧的 `_pair()` 仍兼容裸字符串与二元组（替身/老测试）。
    drafts: "queue.Queue[tuple]" = _WakeQueue()        # 7 元组（见上）-> 主线程
    drafts_zh: "queue.Queue" = _WakeQueue()         # 草稿译文 (seq, seg, zh) -> 主线程
    streamq: "queue.Queue[tuple]" = _WakeQueue()    # ("zh"/"en"/"final"/"terms"/"notice"/"answer"/"answer_done"/"summary"/"hint"/"replace") -> 主线程
    # ⚠️ 加新 tag **必须**在 `drain()` 加同分支 —— 它是唯一的分发点，漏改即静默丢弃。
    #    2026-09-30 起 `drain()` 末尾有一条**兜底告警**（未识别的 tag 会 echo 一行）。
    draft_view = DraftView()                            # 草稿译文的过期判据(只在主线程 drain() 里用)
    draft_stats = draft_view.stats                      # 丢弃/让路/出错计数(收尾打印)
    properq: "queue.Queue" = queue.Queue()              # 待查专有名词
    answerq: "queue.Queue[str]" = queue.Queue()         # 待讲解的问题(Phase 2)
    hintq: "queue.Queue[list[str]]" = queue.Queue()     # ❓ 待点拨的最近几句(只留最新一份, 见 hint_worker)
    last_draft = {"en": ""}                             # 屏上最新的英文草稿(drain 记; ❓ 点拨要带上最新半句)

    _engine_warns: dict[str, str] = {}                   # 来源 -> 当前告警(翻译 / 转写各一路)

    def notify(msg: str, warn: bool = False, src: str = "翻译") -> None:
        """引擎状态广播 -> streamq -> 主线程 drain() -> UI(悬浮窗要求主线程)。

        ⚠️ 菜单栏只有**一个**图标、两路引擎(翻译 / 定稿转写)各自降级恢复:
        一路恢复时另一路还挂着, 图标得留着 ⚠️ —— 所以按来源记账, 把「还剩哪些告警」一起发。

        ⚠️⚠️ **这里不加锁是核实过的，不是漏了**（2026-10-12 全仓审查提了这条，实测不成立）：
           它确实由两路引擎**各自线程**调用，但 `"\n".join(_engine_warns.values())`
           对 dict 视图的迭代是**一整段不放开 GIL 的 C 循环**（`str.join` 先走
           `PySequence_Fast` → 对非 list/tuple 的 iterable 再 `PySequence_List`），
           别的 Python 线程切不进去 —— 那个 `RuntimeError: dictionary changed size
           during iteration` 在这个形状下**打不到**。
           实测：64 键 · 8 线程 · 400 轮 · `sys.setswitchinterval(1e-6)`，跑三遍
           **0 次报错**（加不加上锁都一样）。所以**不要为它加锁**。
        ⚠️ 但反过来 —— **别把这个表达式改成 Python 层的
           `for k, v in _engine_warns.items()`**：那样迭代就落到字节码上了，
           上面这条结论立刻作废，那时才真的需要锁。"""
        if not src:                                      # 一次性提示(休眠唤醒 / 换设备): 不记账
            pass
        elif warn:
            _engine_warns[src] = msg
        else:
            _engine_warns.pop(src, None)
        streamq.put(("notice", (msg, warn, "\n".join(_engine_warns.values()))))

    translator = EngineRouter(local_tr, cloud_tr, args.engine, notify=notify,
                              # 🧪 测试模式：翻译失败 / 降级 / 恢复进 `report.json` 的 events。
                              #    ⚠️ `tester` 为 None（测试模式关）时传 None —— EngineRouter
                              #    默认就是空 lambda，**零开销零行为**（与 Overlay 的 `note` 同规矩）。
                              #    ⚠️ 直连回调、不进 streamq：那条路要新增 tag + `drain()` 加分支
                              #    （R28 钉着），而这条数据本来就不需要主线程、不需要 UI。
                              note=(tester.note_event if tester is not None else None))
    translator.warmup()

    def _reset_net() -> None:
        """休眠唤醒后丢掉两边的 HTTP 连接池(睡前的连接可能半开)。"""
        final_asr.reset_connections()
        if cloud_tr is not None:
            cloud_tr.reset_connections()

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

    def _on_lost_pressed() -> bool:
        """悬浮窗 ❓ 的回调(AppKit 主线程): 落盘一个时间戳, 并视情况排队一次点拨。

        ⚠️ 主线程 —— 只做 list 复制 + `queue.put`, 不联网不 sleep(同 `ask_about_this`)。
        ⚠️ 纯转录 / 「只英·校」档(点拨是中文的) / 本地引擎 → 不排队、返回 False,
           悬浮窗改显示"课后笔记里能找到"; 纯转录档一个模型请求都不发(DESIGN 契约)。
        返回 True = 已排队, 悬浮窗显示"正在提炼…"。"""
        writer.mark_lost()
        if trans["mode"] != "both" or not translator.uses_cloud:
            return False
        hintq.put([t for t in (*finals[-4:], last_draft["en"]) if t])
        return True

    # ══════════════════════════════════════════════════════════════════════
    # 一些事儿（2026-10-17）—— 板子 + 决定出口 + 提醒事项
    #
    # `live_summary` 认出来的 `课务` payload 走 `_summ_emit` 那条线（worker 线程），
    # 这里把它变成「一些事儿」条目（`duty.Board`），用户点头才写提醒事项。
    # ⚠️ 三处纪律：
    #   ① `offer()` 在 **worker 线程**（`_summ_emit`），其余都在主线程；
    #     `Board` 内部一把锁，`streamq` 推给界面。
    #   ② 提醒事项走 `osascript`，**可能弹授权框**（几十秒）→ 一律在 worker 上，
    #     主线程只 `queue.put`（与 `ask_about_this` 同一条规矩）。
    #   ③ 决定**先落旁路文件**（`duty.save_decision`，在 `Board.decide` 里），
    #     再谈写提醒事项 —— 授权的几十秒里用户已经点了「记下」，那条不能等。
    # ══════════════════════════════════════════════════════════════════════
    import duty as _duty

    _duty_day = datetime.date.today()
    duty_board = _duty.Board(day=_duty_day,
                             path=_duty.sidecar_path(writer.session_path))

    def _feature(name: str, **data) -> None:
        """⭐ **功能使用度**（测试模式，作者 2026-10-17）：一条动作送给**两个既有出口**。

            overlay（悬浮窗那边）──┐
                                    ├─►  这里  ─►  tester.note_event（report.json 的事件流）
            main（提醒事项回执） ──┘         └─►  live.event（`.live.jsonl`，class_report 读）

        ## 三条纪律

        1. ⚠️ **普通模式什么都不做**：`tester` 为 `None`（没开 `--test-mode`）+ live 日志
           默认关 → 两个出口都没有 → **空操作**。这里**不新增任何上传、不新增任何写盘**。
        2. ⚠️⚠️ **不记任何文本内容**：字段白名单在 `feature_usage.clean()`。overlay 那边
           已经过了一道，这里是**第二道**（也管 `main` 自己报的事件）。
        3. 自己吞异常（它在 `drain()` 的回执路径上 —— 采集坏了不许影响上课）。
        """
        try:
            item = feature_usage.clean(name, data)
            if item is None:
                return
            kind = feature_usage.PREFIX + item[0]
            if tester is not None:
                tester.note_event(kind, data=item[1])
            if live is not None:
                live.event(kind, **item[1])
        except Exception:                                     # noqa: BLE001
            pass

    def _duty_push() -> None:
        """把板子的当前状态推给界面（主线程之外也能调：`streamq` 是线程安全的）。"""
        try:
            streamq.put_nowait(("duty", duty_board.items()))
        except Exception:                                     # noqa: BLE001
            pass

    def _duty_decided(action: str, item: dict) -> None:
        """悬浮窗卡片的决定出口（**AppKit 主线程**，必须 O(1) 返回）。

        `action` ∈ `save / ignore / undo / type`。写盘（旁路文件）与起 worker 之外
        什么都不做 —— 提醒事项那条路要几十秒（可能弹授权框）。
        """
        rid = str((item or {}).get("id") or "")
        if not rid:
            return
        if action == "type":
            cur = duty_board.get(rid) or {}
            duty_board.decide(rid, str(cur.get("action") or "pending"),
                              type_=str(item.get("type") or ""), source="card")
            _duty_push()
            _duty_wrapup_sync()
            return
        if action in ("save", "ignore"):
            snap = duty_board.decide(
                rid, "saved" if action == "save" else "ignored",
                title=item.get("title"), date=item.get("date"),
                time_=item.get("time"), type_=item.get("type"), source="card")
            _duty_push()
            _duty_wrapup_sync()
            if action == "save" and snap is not None:
                _duty_remind(snap)
            return
        if action == "undo":
            # ⚠️ 撤销**不动** `reminders` 那一条事件：旁路文件是"发生过什么"的账，
            #    撤销只是把 action 改回 pending（卡上那句状态由 `duty.status_line`
            #    按 action 判 —— pending 时不显示，所以旧回执不会露出来）。重新「记下」
            #    会再写一次提醒事项、也再记一条回执。
            duty_board.decide(rid, "pending", source="card")
            _duty_push()
            _duty_wrapup_sync()

    def _duty_remind(snap: dict) -> None:
        """把一条「记下」写进 macOS 提醒事项 —— **worker 线程**。

        ⚠️ 失败（拒绝授权 / 超时）**不静默**：条目落 `~/.classlive/duty-backup.jsonl`，
           卡上写明原因（回执走 `("duty_result", …)`，见 `drain`）。
        """
        rid = str(snap.get("id") or "")

        def _work() -> None:
            ok, reason = True, ""
            try:
                ok, reason = _duty.write_reminder(_duty.reminder_argv(snap))
            except Exception as e:                            # noqa: BLE001
                ok, reason = False, f"{type(e).__name__}: {str(e)[:60]}"
            if not ok:
                try:
                    _duty.backup(snap, reason)
                except Exception:                             # noqa: BLE001
                    pass
            try:
                duty_board.note_reminder(rid, ok, reason)
            except Exception:                                 # noqa: BLE001
                pass
            try:
                streamq.put_nowait(("duty_result", rid, bool(ok), str(reason)))
            except Exception:                                 # noqa: BLE001
                pass
            echo(f"[{now()}] 📌 一些事儿：{snap.get('title') or rid} → "
                 + ("已记入提醒事项" if ok else f"写不进提醒事项（{reason}）"))

        threading.Thread(target=_work, daemon=True, name="duty-remind").start()

    def _duty_wrapup_sync() -> None:
        """收尾汇总卡开着时**当场**把决定送回去。

        ⚠️ 必需：`ui.duty_wrapup()` 是**主线程阻塞 + 自己 pump** 的（那几十秒里
           `drain()` 不跑），所以只靠 `streamq` 那一条路卡片不会更新 ——
           `duty_wrapup_sync` 是直的（它自己把重画排到下一帧，见 `_card_defer`）。
        """
        fn = getattr(ui, "duty_wrapup_sync", None)
        if callable(fn):
            try:
                fn(duty_board.items())
            except Exception:                                 # noqa: BLE001
                pass

    def _duty_stats() -> dict:
        try:
            return duty_board.stats()
        except Exception:                                     # noqa: BLE001
            return {}

    ui = TerminalUI() if args.ui == "terminal" else _load_overlay(
        on_quit=stopping.set,
        # ❓ 要的是**按下那一刻** —— 所以它不设标志位, 直接落盘一个时间戳。
        # ⚠️ 这里带上 `writer` 是刻意的: `mark_lost()` 只做"拼一行 + 写 + flush",
        #    全是主线程能扛的量; 而且它就是 `writer` 自己的方法, 不像从前那个 ⭐
        #    那样要绕一圈到 `drain()` 里去补标志位。
        #    （⭐ 已于 2026-09-29 并进 ❓ —— 两个按钮记的是同一件事。）
        on_lost=_on_lost_pressed,
        on_translate=lambda mode: trans.update(mode=mode, ever_off=trans.get("ever_off") or mode != "both"),
        on_submit=submit_question,
        on_ask=ask_about_this,
        # late binding: start_new_topic 定义在下面的问答状态块里, 这里只是把回调
        # 装上(按钮到点名前一定已经定义好了)。
        on_new_topic=lambda: start_new_topic(),
        # ⭐ 一些事儿：卡片上的决定（记下 / 忽略 / 撤销 / 改类型）唯一出口。
        #    ⚠️ AppKit 主线程回调 —— 实现见 `_duty_decided`（只落盘 + 起 worker，立刻返回）。
        on_duty=_duty_decided,
        # ⭐ 功能使用度（测试模式）：卡片出现 / 记下 / 忽略 / 下一条 / 改字段 / 收尾 /
        #    课前回顾那几条线的**动作**（**绝不带文本内容**）。
        #    ⚠️ 与 `note`（UI 使用度计数）分工不同：这个带字段（类型 / 来源 / 秒数）。
        #    ⚠️ 两个出口**都没有**时传 `None`（没开测试模式 + live 日志关着）——
        #       `Overlay` 那边第一件事就是早退，普通模式**零开销零行为**（同 `on_vis`）。
        on_feature=(_feature if (tester is not None or live is not None) else None),
        # ⭐ 采集面 ⑦：UI 使用度。⚠️ `tester` 是 `None` 时传 `None`（不是传个假
        #    回调）—— `Overlay` 那边默认就是空 lambda，**零开销零行为**。
        note=(tester.note_ui if tester is not None else None),
        # ⭐ 3.9.8 ①④⑤：四个采集钩子。全部**默认 `None`**（`Overlay` 用 `is not None`
        #    守门）—— 不开测试模式 = 一个字节都不动、一次调用都没有。
        #    ① 可见性变化 → 能耗分桶（`on_vis`）+ pump 实际等待时长（`on_wait`）
        #    ④ 系统 / 屏幕睡眠唤醒 → `events`
        #    ⑤ 视图采样（1 Hz）→ 可见行数 / 档位行数 / 面板宽
        on_vis=(tester.on_vis if tester is not None else None),
        on_wait=(tester.add_wait if tester is not None else None),
        on_view=(tester.note_view if tester is not None else None),
        on_power_event=(tester.note_event if tester is not None else None),
    )
    # ⭐ 3.9.7 省电：新数据到达要**立刻**叫醒主循环（它在空闲档让步 0.1 s、不可见档 0.25 s）。
    #    终端 UI 的 `wake()` 是空实现 —— 装了也只是一次空调用，零行为、零开销。
    _wake = getattr(ui, "wake", None)
    if callable(_wake):
        for _q in (streamq, drafts, drafts_zh):
            try:
                _q.set_waker(_wake)
            except Exception:                             # noqa: BLE001
                pass
    # ⭐ 3.9.8 ②：`streamq` 每条 item 从 put 到 drain 的停留时间（唤醒延迟）。
    #    ⚠️ 只在测试模式装（`tester` 为 None 时**连 deque 都不建**，行为与开销逐字相同）。
    #    `set_stats` 只装在 `streamq` 上 —— `drafts`/`drafts_zh` 的停留时间不是这一格问的。
    if tester is not None:
        try:
            streamq.set_stats(tester.note_streamq_dwell)
        except Exception:                                 # noqa: BLE001
            pass
    _tp["overlay"] = time.monotonic()
    try:
        _st = startup_timing(_tp, _t_wall0, _t_click)
        echo("⏱ " + _st["line"])
        live.event("startup", **_st["fields"])
    except Exception:                              # noqa: BLE001
        pass
    # 终端模式拿不到悬浮窗，退化成字符框（两种模式都要能看到）
    # ⚠️ 判据同上（`drives_appkit`，不是 `args.ui`）：overlay 载入失败会静默回退
    #    终端 UI，那时 `args.ui` 仍是 "overlay" → 字符框不打印、真悬浮窗又不存在
    #    → **更新卡片整个消失**。（2026-09-28 OCR 审计发现）
    if not getattr(ui, "drives_appkit", False) and whatsnew:
        _print_whatsnew_box(**whatsnew)

    # ⭐ 一些事儿 / 课前回顾（2026-10-17）：把「这节课是谁」与**上一节的回顾**给界面。
    #    ⚠️ 回顾**不调模型**（上节课收尾时已经生成并存下）—— 读本地文件、立刻显示。
    #    📄「今天可能讲什么」要看**今天**新传的课件，那份要抽文本 → 丢 worker、
    #    晚 1–2 秒补进来（`("recap_more", …)`），不许挡开课（启动线上有计时判据）。
    try:
        _set_sess = getattr(ui, "set_session", None)
        if callable(_set_sess):
            _set_sess(course=args.course or "", day=_duty_day)
        if args.course:
            import recap as _recap
            _prev = _recap.load(args.course)
            if _prev:
                streamq.put(("recap", _prev, args.course, _duty_day))
                _duty_push()                       # 小卡也一起亮（板子可能已从旁路回放）

                def _today_work(_c=args.course, _d=_prev) -> None:
                    try:
                        extra = _recap.today_extra(_c, _d)
                    except Exception:                 # noqa: BLE001
                        extra = {}
                    if extra:
                        try:
                            streamq.put_nowait(("recap_more", extra))
                        except Exception:             # noqa: BLE001
                            pass

                threading.Thread(target=_today_work, daemon=True,
                                 name="recap-today").start()
    except Exception as e:                                # noqa: BLE001
        echo(f"⚠ 课前回顾没显示（课堂不受影响）：{type(e).__name__}: {str(e)[:60]}")

    seen_proper: collections.Counter = collections.Counter()   # 出现次数(≥2 才查)
    queried_proper: set = set()                         # 已发起过查询的(防重复请求)
    finals: list[str] = []                              # 已定稿(修正后), 作 LLM 上下文
    latest = _Latest()
    finalq: "queue.Queue" = queue.Queue()
    _QUIT = object()
    busy = {"on": False}          # 定稿线程正在处理一句(ASR+LLM), 冲刷等待要等它归零

    # ---- Soniox 实时识别（`stream_asr.py`）----
    # 能连上就用它出草稿 / 定稿；连不上（或压根没建）→ 本地流水线照旧，一个字节都不变。
    # ⚠️ 判据全在 `stream_gate.start_ok()`（单测钉着）；这里只负责「建 / 不建」。
    sealedq: "queue.Queue" = queue.Queue()      # `on_sealed` 只往这里入队（Soniox 收线程）
    _wake_fw = threading.Event()                # 叫醒 `final_worker`：sealedq / finalq 有货
    stream = None
    if stream_gate.start_ok(passcode=api_key_val, cloud_ok=_cloud_asr_ok()):
        try:
            import stream_asr
            from translator import course_term_list, course_title

            def _sealed_in(s, _q=sealedq, _ev=_wake_fw):
                # ⚠️ 这是 Soniox **后台收线程**在调：只许入队 + 叫醒，绝不碰 `_emit`/UI。
                _q.put(s)
                _ev.set()

            stream = stream_asr.StreamASR(
                passcode=api_key_val,
                terms=course_term_list(args.glossary, args.course),
                general=course_title(args.glossary, args.course),
                on_sealed=_sealed_in)
            echo("🎧 实时识别: Soniox（连不上会自动退回本地）")
        except Exception as e:                                 # noqa: BLE001
            echo(f"⚠ 实时识别起不来，本节课走本地：{type(e).__name__}: {e}")
            stream = None
    gate = stream_gate.StreamGate(stream, sr=SR)
    # Soniox 逐句封口会把 "Okay?" 单独封成一句 -> 屏上并进上一行（`caption_flow.is_tail`）
    _tail = {"on": gate.on and callable(getattr(ui, "set_tail_merge", None))}
    if _tail["on"]:
        ui.set_tail_merge(True)

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

    # ---- 段号（"这是这一段音频"的**身份**）----
    # ⚠️⚠️ **它归 `main`，不归 `livemetrics`**（2026-10-09 审查指出）：段号是**流水线的
    #    身份**，不该由"开没开日志"决定 —— 长在日志器里的话，日志一关整条流水线的
    #    段号恒为 0。日志器只是个 sink（`live.segment_end(k)` 只记一行）。
    # ⚠️ 它必须在 **`Segmenter` 的 `on_utterance_end`（音频真正结束那一刻）** 递增，
    #    **不能**挂到定稿上屏 —— 那条路要先跑 Whisper + LLM，晚 2–4 秒，
    #    新段前几份草稿会带着上一段的号（实测把 `草稿→定稿` 从 10.7% 顶到 65%）。
    seg_gen = {"n": 0}
    #: ⭐ 第 3 项：`seg -> 那一段音频结束的时刻`（`time.monotonic()`）。`seg_to_zh_ms`
    #: 的起点 —— 用户真正等的时间从那**一段讲完**算起（定稿 ASR + 断句 + 排队都在里面）。
    #: ⚠️ 在 `_open_segment()` 里写（音频线程）、在 `_emit()` 里读（定稿线程）：每段的
    #:    键只写一次，之后只读 —— GIL 下够用，且它是纯数字、没有顺序语义。
    seg_ended_at: dict[int, float] = {}
    #: ⭐ 第 4 项（跨段半句合并）：`seg -> 那一段的音频时长（秒）`。段的时间轴只有
    #: 「结束时刻 + 时长」两个量，音频间隔 gap = (结束_j − 时长_j) − 结束_i
    #: （与 design.md §3 / `prosody/p1.py:202` 同式）。在 `_utterance_end` 写、`drain` 读。
    seg_dur: dict[int, float] = {}
    #: ⭐ 第 4 项：`seg -> 这一段 ASR 原文有没有句末标点`（口径与 `testmode.note_segment`
    #: 的 `end_punct` **逐字相同**）。定稿线程写、`drain` 读（GIL 够用：每段只写一次）。
    seg_end_punct: dict[int, bool] = {}

    def _open_segment() -> int:
        """音频结束那一刻调：返回刚结束那一段的编号，并把"当前段"推进到下一段。"""
        k = seg_gen["n"]
        # Soniox 每封一句就占一个新号（`gate.seg_of`）-> 本地段号跟着跳过去，只增不减；
        # 没建流时 `draft_seg` 原样返回 = 改前的 `k + 1`
        seg_gen["n"] = gate.draft_seg(k + 1)
        gate.mark()                      # Soniox 那边同一刻开新段（段号对齐，见 stream_gate）
        seg_ended_at[k] = time.monotonic()
        if live is not None:
            live.segment_end(k)
        return k

    # ---- 草稿线程 ----
    # 每定稿一句 +1; 草稿线程据此重置节流(见 partial_worker 里的说明)。
    final_gen = {"n": 0}
    #: ⭐ 3.9.7 省电：窗口**不可见**期间跳过的草稿份数（收尾进测试模式 ui 块
    #: `draft_skipped_hidden` / `draft_zh_skipped_hidden`）。`zh` 那一格由草稿译文
    #: 线程的 `paused` 回调自增（同一条纪律：只说数字，不动文本）。
    draft_skipped = {"n": 0, "zh": 0}

    def _ui_invisible() -> bool:
        """窗口此刻不可见吗（收起 / 完全被遮挡 / 屏幕睡眠）。

        ⚠️ 只读 UI 对象一个 bool（`pump` 每轮现算）。终端 UI / 替身没有 `visible`
           → `getattr` 默认 `True` → **恒不可见为假**（不曾存在悬浮窗，就没有"不可见"）。
        """
        try:
            return not bool(getattr(ui, "visible", True))
        except Exception:                                    # noqa: BLE001
            return False

    def partial_worker():
        last_tr = 0.0
        last_len = 0
        last_gen = 0
        # 草稿显示层的前缀冻结（draft_stable.py）。**默认关**：
        # CLASSLIVE_DRAFT_HOLDBACK=2 打开（数字 = 灰尾词数）。量具数字见 docs/RESEARCH-stream-eval.md。
        # 只影响**上屏的草稿文本**；送去翻译的仍是最新的原始假设。
        _hb = os.environ.get("CLASSLIVE_DRAFT_HOLDBACK", "").strip()
        stab = None
        if _hb.isdigit():
            from draft_stable import DraftStabilizer
            stab = DraftStabilizer(int(_hb))
        stab_gen = 0
        draft_policy_name = os.environ.get("CLASSLIVE_DRAFT_TRIGGER", "novel").strip() or "novel"
        if draft_policy_name not in ("legacy", "clause", "novel"):
            draft_policy_name = "novel"
        draft_seq = 0                         # 送翻序号(drain() 丢弃比已显示的更旧的译文)
        last_sent = ""                        # 上次送翻的草稿原文（未剥悬挂词；给 novel 数新词）
        prev_text = ""                        # 上一次的草稿原文（`draft_policy` 自己派生 append_only）
        last_eng = draft.name                 # 引擎变了（熔断）→ 出声一次 + 记一笔
        _draft_hidden = False                 # 上一轮是不是「因为窗口不可见」跳过的
        _skip_seg = None                      # 恢复后要跳过的那一段（它的草稿流已被 reset）
        while running.is_set() and not latest.stopped:
            buf = latest.take()               # ⚠️ 阻塞等（3.9.7 省电）—— 停机靠 `latest.stop()`
            if latest.stopped:
                break
            _end = latest.take_end()
            if _ui_invisible():
                # ⭐ 3.9.7 省电：窗口不可见 → **停草稿识别**（Nemotron / Parakeet 推理）。
                #    定稿转写 / 翻译 / 落盘**一条都不许少**（它们不走这条路）。
                #    ⚠️ 恢复后从**下一个段**重开 —— 见下面 `_skip_seg` 那一段。
                if (buf is not None and len(buf)) or _end is not None:
                    draft_skipped["n"] += 1
                _draft_hidden = True
                continue
            if _draft_hidden:
                # 刚恢复可见：先把进行中那一段的流状态丢掉（`DraftEngine.reset`），
                # 再从**下一个段**重新开始出草稿 —— 别把隐藏期间积压的那半段缓冲接着喂。
                _draft_hidden = False
                try:
                    draft.reset()
                except Exception:                            # noqa: BLE001
                    pass
                # ⚠️ 这里**不**读 `seg_gen["n"]`（`tests/test_draft_engine.py` 的
                #    「段号只有一个来源」钉着）；段号只从这份缓冲自己的 tag 拿。
                _tt = latest.taken_tag
                _skip_seg = _tt if (buf is not None and len(buf) and _tt is not None) else None
            if _end is not None:
                # ⭐ 段尾收一次（3.9.4，`draft_engine.NemotronDrafter.finish`）：前瞻里那截解出来，
                #    草稿不再"少最后几个词等定稿来补"。**排在下面「定稿在排队就让开」之前**——
                #    段尾那一刻 finalq 里正好躺着这一段，让开就等于永远不收。
                _t_end = time.monotonic()
                t = draft.finish(*_end)
                # 收尾解出来的和这一段上一份草稿**一字不差**（尾词早就解全了，10-12 回放约 2/15 段）
                # → 屏上不用再放一遍，也不用再花一次草稿翻译。
                if t and not (_end[1] == last_gen and t == prev_text):
                    _eseg = _end[1]
                    prev_text = t
                    drafts.put((t, stab.update(t) if stab is not None else t, len(_end[0]) / SR,
                                time.monotonic() - _t_end, _eseg, _t_end, draft.name))
                    if trans["mode"] == "both":
                        draft_seq += 1
                        draft_tr.submit(draft_seq, _eseg, strip_dangling(t))
            if buf is None or len(buf) == 0:
                continue
            _t_put = latest.taken_t                  # 这份缓冲被交出来的时刻（量 `lag_s` 用）
            # put 那一刻的段号 = 这份缓冲真正所属的段。⚠️ 别在这里读 `seg_gen["n"]`：
            # 到这儿下一段可能已经开始了（「分组键取自晚到信号」那类坑）。
            _tag = latest.taken_tag
            _seg = _tag if _tag is not None else seg_gen["n"]
            # ⭐ 3.9.7：恢复可见后**跳过进行中的那一段**，从下一个段重新开始出草稿
            #    （那一段的流状态已被 `draft.reset()` 丢掉；接着喂 = 喂半截）。
            if _skip_seg is not None:
                if _seg == _skip_seg:
                    continue
                _skip_seg = None                 # 新段到了 → 恢复正常
            # 定稿是关键路径(决定你多久看到中文)。定稿走 Whisper 时两边是两把不同的
            # ASR 锁, 不互抢; 只有 Parakeet 兜底(定稿退化回草稿模型)时才共用一把。
            # 有定稿在排队时让草稿这一轮退开: 草稿迟到 1s 无感, 定稿迟到直接
            # 拖慢首字。草稿译文另有独立线程(DraftTranslator), 不再排在定稿之后。
            # ⚠️ 3.9.4 起**只有草稿也是 Parakeet 时才让**：Nemotron 草稿和定稿不共用模型，让开只会把
            #    段尾那一刻 `Segmenter` 给新段的那份缓冲（硬切挪回的 3–6 s）白白丢掉 —— 实测新段第一份
            #    草稿因此晚到 ~2 s（让开 → 等下一拍 1 s → 再从头解一整截）。
            if draft.name == "parakeet" and not finalq.empty():
                continue
            try:
                _t_draft = time.monotonic()
                t, _eng = draft.transcribe(buf, _seg)      # 永不抛；流式坏了这一份就改走 Parakeet
                _dt_draft = time.monotonic() - _t_draft
                if _eng != last_eng:
                    last_eng = _eng
                    if draft.fallback_reason:
                        echo(f"⚠ 流式草稿已停用，本节课改回 Parakeet：{draft.fallback_reason}")
                        if live is not None:
                            live.event("draft_engine_fallback", reason=draft.fallback_reason)
                if not t:
                    continue
                _shown = t
                if stab is not None:
                    if final_gen["n"] != stab_gen:           # 定稿到了 → 冻结前缀作废
                        stab_gen = final_gen["n"]
                        stab.reset()
                    _shown = stab.update(t)
                # ⚠️ `buf_s`/`compute_s`/`seg` 要一路带到 `drain()` —— 现场采集那份
                #    `<会话>.live.jsonl` 靠 `buf_s` 判"这份草稿的缓冲窗口滑了没有"
                #    （滑了的那批**不许**进回改统计，否则长句会把回改率虚高一大截，
                #     离线量具就是栽在这上面，见 scripts/stream_eval.py 的 `sliding`）；
                #    `seg` = 本段草稿所属的段号（`seg_gen`，见上面那一段说明）；
                #    `_t_put` = 缓冲交出的时刻 → `drain()` 用它算上屏延迟 `lag_s`。
                drafts.put((t, _shown, len(buf) / SR, _dt_draft, _seg, _t_put, _eng))
                # 新句子开始 -> 重置草稿节流。**必须重置**: last_len 是**上一句**
                # 收尾时的词数, 新句若更短(如 20 词 vs 30 词), "新增 ≥6 词"永远
                # 不成立 -> 整句拿不到即时中文("首字中文 ~3s"特性静默失效)。
                # ⚠️ 比较段号（草稿所属的音频段），不是 `final_gen`：后者要等上一段
                #    定稿上屏才变（晚 2–4s；进 carry 时根本不变），会在新段讲到一半时清零节流。
                # ⚠️ 用 `_seg`（put 时刻），别现读 `seg_gen`：段号在本函数里**只有一个来源**
                #    （`tests/test_draft_engine.py` Wiring 钉着）。
                if _seg != last_gen:
                    last_gen = _seg
                    last_tr, last_len, last_sent = 0.0, 0, ""
                # 草稿译文: 距上次 ≥2.5s 且新增 ≥6 词才送(避免刷爆 LLM)
                now = time.monotonic()
                # 判据在 draft_policy.py（legacy = 原来的「≥2.5s 且新增 ≥6 词」，逐字等价，测试钉着）。
                # CLASSLIVE_DRAFT_TRIGGER=clause 才换成「落在从句边界/前缀稳定才送」—— 默认不换。
                # ⚠️ 派生量（`append_only`）**归 draft_policy 自己算**（2026-10-09 收口）：
                #    原来这里手搓一份、离线模拟里又用 `stream_metrics.normalize` 写一份，
                #    两份不等价 → 离线模拟预测的不是生产这条路。而且 legacy 档（默认）
                #    本来是白算的。
                _nw = len(t.split())              # 切一次就够（这行原来切了两遍）
                _ok = trans["mode"] == "both" and draft_policy.should_translate(
                    draft_policy_name, now=now, last_tr=last_tr, n_words=_nw,
                    last_len=last_len, text=t, prev_text=prev_text,
                    sent_text=last_sent)
                prev_text = t
                if _ok:
                    last_tr, last_len, last_sent = now, _nw, t
                    # (seq, seg, text): seg 与 drafts 元组里那个段号同源 → drain() 据此丢过期译文
                    draft_seq += 1
                    draft_tr.submit(draft_seq, _seg,
                                    strip_dangling(t))   # 送翻前削掉结尾悬挂词（上屏/记录仍用原文）
            except Exception as e:                       # noqa: BLE001
                echo(f"⚠ 草稿转写失败: {e}")

    # ---- 定稿线程(ASR + 语义缝合 + 断句 + 逐句流式 LLM) ----
    # 优先级: 定稿 > 草稿译文。carry 挂起超 CARRY_MAX_S 或词数超限 → 强制送出,
    # 绝不让"等句子闭合"把翻译无限期阻塞(否则会出现 20s+ 才吐中文)。
    # ⚠️ 5.0 → 3.0（2026-10-14）：4 节真课的 `report.json` 离线回放 —— 90 次挂起里下一段定稿
    #    5 s 内到的只有 19 次（中位要 10–11 s 才到），其余 71 次白等满时长；3 s 仍接住 13/19，
    #    每次白等少 2 s。作者：改短，但保留（对别的用户可能有用）。
    CARRY_MAX_S = 3.0
    CARRY_MAX_WORDS = 25
    carry = {"text": "", "since": 0.0, "seg": 0}
    #: ⭐ 3.9.8 ⑥：定稿翻译的「距上一次结束多久」（`idle_before_ms`）FIFO —— 与带 `lat`
    #:    的 `("final", …)` **一一对应**。`_emit` 在定稿线程 append、`drain()` 在主线程
    #:    取；append 排在 `streamq.put` **之前**，单产单消，顺序恒等。
    #:    `_tr_last["end"]` = 上一次定稿翻译调用**结束**的时刻（第一次 `None`）。
    _tr_idle: "collections.deque" = collections.deque()
    _tr_last = {"end": None}

    def _emit(text: str, seg: int | None = None, max_words: int = 25) -> None:
        # ⭐ `seg` = 这段文本属于哪一段缓冲（`livemetrics` 的分组键；一段 VAD → N 句定稿，
        #    **不是 1:1**）。定稿那条路由 `finalq` 带过来（**音频结束那一刻**定的）；
        #    carry 兜底那条路没得带，取当前段。
        if seg is None:
            seg = seg_gen["n"]
        # R25: 收尾等了 15 s 放弃后(`running.clear()`), 还卡在转写里的 `final_worker` 回来会
        #    对一句**已经被放弃**的话继续调翻译 API。云端定稿最长 6 s 让这个窗口比以前大。
        #    ⚠️ 正常收尾不受影响: `running` 要到冲刷真正结束才清, `_force_emit_carry()` 也在那之前。
        #    (`_emit` 本身不碰 writer —— 落盘只走 `drain()`, 而它在放弃后不再被调用。)
        if not running.is_set():
            return
        final_gen["n"] += 1                      # 通知草稿线程: 重置节流
        for sent in split_sentences(text, max_words=max_words):
            if trans["mode"] == "raw":
                # 纯转录: 一个 LLM 请求都不发。ASR 原样上屏、原样落盘。
                # 这是给"不需要翻译、也不想联 API"的场景 —— 离线、零成本、零首字延迟。
                # ⚠️ 与下面两档的关键差别: 不产出 `en` 流(没有矫正可流式), 直接给
                #    ("final", "", "", sent), 让 en 走 final 那一趟。
                finals.append(sent)
                streamq.put(("final", "", "", sent, seg))
                continue
            if trans["mode"] == "en":
                # 只矫正英文: 中文不出, 但 ASR 错听照修(仍要联模型)。
                # 失败则回退原始 ASR —— 与翻译路径同规矩, 绝不因模型问题丢转录。
                try:
                    fixed = translator.fix_stream(
                        sent, finals,
                        on_en=lambda d: streamq.put(("en", d, seg)))
                except Exception as e:              # noqa: BLE001
                    echo(f"⚠ 英文矫正失败({str(e)[:50]}); 保留原始转录")
                    fixed = ""
                finals.append(fixed or sent)
                streamq.put(("final", fixed, "", sent, seg))
                continue
            # 翻译失败也要保住转录: LLM 挂了不代表这节课没听到东西。
            # 先把 ASR 原句交给悬浮窗当英文行的占位：模型先吐 ZH 后吐 EN，不占位的话
            # 中文打字的那段时间里上面那行英文是空的。修正版到 `final` 时整句换上。
            streamq.put(("en_seed", sent, seg))
            # ⭐ 译文延迟（3.9.7 第 3 项）：只记数字与引擎名，不记文本（见 `tr_latency`）。
            #    ⚠️ 计时器**在翻译调用之前**建（构造即开始计时）。
            _lt = tr_latency.TrTimer()
            # ⭐ 3.9.8 ⑥：这一次翻译**开始时**距上一次翻译**结束**多久（第一次 = None）。
            #    答的是「60 s 长连接保活在真课上有没有省下首字延迟」(by_idle 分桶)。
            #    ⚠️ 整段在 `tester is not None` 里 —— 测试模式关时**一次都不算**。
            _idle_ms = None
            if tester is not None:
                _prev_end = _tr_last["end"]
                _idle_ms = (None if _prev_end is None
                            else round(max(0.0, time.monotonic() - _prev_end) * 1000.0, 1))
            _cloud0 = translator.uses_cloud
            try:
                res = translator.fix_and_translate_stream(
                    sent, finals,
                    on_zh=_lt.tap(lambda d: streamq.put(("zh", d, seg))),
                    on_en=lambda d: streamq.put(("en", d, seg)))
                en, zh = res.en_fixed, res.zh
                finals.append(en)
            except Exception as e:                  # noqa: BLE001
                echo(f"⚠ 翻译失败({str(e)[:50]}); 仅保留转录")
                en, zh = "", ""                     # 无修正版/译文, 内容全在转录里
                finals.append(sent)                 # 上下文仍接得上
            # 第 4 项是**原始 ASR 转录**(未修正), 落盘时要一并记下
            # ⭐ 第 5 项 = 译文延迟 4 元组 `(zh_first_ms, zh_done_ms, seg_to_zh_ms, tr_model)`
            #    （`drain()` 转发给 `live.final` 与 `tester.note_translate`）。
            #    ⚠️ **只有这一条产出点给**：纯转录 / 只英·校两条路没送过翻译，报一个
            #       引擎名会让人以为它们翻出来了（`drain()` 用 `len` 守卫，老形状照样能读）。
            #    ⚠️ 引擎名看「送进去**前后**云端都开着」—— `_fallback` 会把 `_use_cloud`
            #       翻掉，只在返回后读一次会把"降级到本地"误报成云端。
            #    ⭐ 3.9.8 ⑥：`_tr_last["end"]` 在这儿落（= 这次调用的结束时刻），`_tr_idle`
            #       与这条 `("final", …)` 一一对应（`drain()` 侧成对取走）。
            #       ⚠️ 测试模式关时**一次都不做**（连一次 `time.monotonic()` 都不花）。
            if tester is not None:
                _tr_last["end"] = time.monotonic()
                _tr_idle.append(_idle_ms)
            streamq.put(("final", en, zh, sent, seg,
                         _lt.done(seg_t0=seg_ended_at.get(seg),
                                  model=args.cloud_model if (_cloud0 and translator.uses_cloud)
                                  else "local")))
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
        # ⭐2（链路体检）：这一轮定稿的句子喂给候选词池 —— 到步长且过了门的新词
        #    并进翻译侧候选池（`EngineRouter.add_terms` → 云端 + 本地两份）。
        #    ⚠️ 放在**循环之后**而不是每句后面：`finals` 只增不减，一轮喂一次结果一样，
        #       却只要一处调用点（三条 `continue` 分支各写一遍迟早漏一处）。
        #    ⚠️ 步长在池子里（`live_terms.STEP_SENTENCES`）：没到就立刻返回，零开销。
        _new_terms = live_terms_step(live_pool, translator, finals)
        if _new_terms:
            echo(f"💬 本课候选词 +{len(_new_terms)}：{' · '.join(_new_terms[:6])}"
                 + ("…" if len(_new_terms) > 6 else ""))

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
            _emit(t.rstrip() + " …", carry["seg"])      # 带上挂起时存的段号, 不然 _emit 会取"正在讲的下一段"
        except Exception:                               # noqa: BLE001
            pass
        finally:
            busy["on"] = False

    def final_worker():
        EMPTY = object()
        while running.is_set():
            # 0) Soniox 封口句：**先于** `finalq` 取（收尾时哨兵投在 `finalq` 里，
            #    先看哨兵就会 break，`sealedq` 里剩下的句子全丢）。
            #    ⚠️ `_emit` 只在这个线程里被调 —— 原来那些调用者 + 这一处，单线程纪律。
            if gate.on:
                while True:
                    try:
                        _s = sealedq.get_nowait()
                    except queue.Empty:
                        break
                    if not _s.text:
                        continue
                    busy["on"] = True        # 冲刷等待要看它（`all_settled` 的 busy）
                    try:
                        # ⚠️ Soniox 封的本来就是**一句**：别再按 25 词从逗号劈成两行 —— 劈开
                        #    后第二半要排在第一半翻完之后才开始译（10-17 浮窗逐帧：多等 ~1 s，
                        #    中文先显示整句草稿译文再缩回半句）。真长到 40 词以上才劈。
                        _emit(_s.text, gate.seg_of(_s.end_s), max_words=STREAM_MAX_WORDS)
                    finally:
                        busy["on"] = False
            # 1) 定稿优先
            try:
                buf = finalq.get_nowait()
            except queue.Empty:
                buf = EMPTY
            if buf is _QUIT:
                break
            if buf is EMPTY:
                # 2) 空转: **阻塞等**下一份定稿（3.9.7 省电 —— 原来是 `sleep(0.2)` 硬轮询，
                #    空闲时 5 次/秒空醒）。carry 挂着时按它的**剩余期限**醒来（不改变
                #    段尾挂起的时序：原来最坏也要等 0.2 s 才发现到期，这里更准）。
                _wait = CARRY_MAX_S - (time.monotonic() - carry["since"]) if carry["text"] else 1.0
                if gate.on:
                    # ⚠️ Soniox 模式下这种阻塞得能被**封口句**叫醒（`on_sealed` 在后台
                    #    线程里 set）—— 否则一句封好的话要在队列里干等满 1 s 才上屏。
                    #    生产者（`_sealed_in` / `_utterance_end` / 收尾的 `_QUIT`）都会 set。
                    _wake_fw.wait(max(0.0, _wait))
                    _wake_fw.clear()
                    if carry["text"] and time.monotonic() - carry["since"] >= CARRY_MAX_S:
                        _force_emit_carry()
                    continue
                try:
                    buf = finalq.get(timeout=max(0.0, _wait))
                except queue.Empty:
                    buf = EMPTY
                if buf is _QUIT:
                    break
                if buf is EMPTY:
                    if carry["text"] and time.monotonic() - carry["since"] >= CARRY_MAX_S:
                        _force_emit_carry()
                    continue
            # ⚠️⚠️ **拆包必须在两个哨兵之后**（2026-10-09 端到端跑抓回来的真 bug）：
            #    曾经把它提到 `if buf is EMPTY:` 前面 —— 而"空转"那条路**每轮都会走**
            #    （`finalq` 空时 `buf = EMPTY`），于是 `buf, _fseg = EMPTY` 抛
            #    TypeError → **final_worker 线程当场死掉** → 没人消费 `finalq`
            #    → `partial_worker` 的"有定稿在排队就让开"永远成立 → **整节课一份草稿都不出**。
            #    形状：`("final"…)` 与 `drafts` 两处加字段都只是加法，唯独这里
            #    把"可能是哨兵"的值当元组拆了。注释当时还写着"走到这里一定是元组"——错的。
            buf, _fseg = buf
            try:
                # 云端优先 / 本地兜底 / 退化与空串的补救都在 `final_asr` 里(永不抛)。
                # ⭐ 测试模式要的「第一个来源的原文 / 判决 / 兜底原文」在 `r.trace` 里 ——
                #    少记它的代价是实打实的(2026-10-07: 主模型空与兜底模型空分不开)。
                r = final.transcribe(buf)
                text = r.text
                # ⭐ 第 4 项：这一段 ASR 原文有没有句末标点（跨段合并的 ASR 口径）。
                #    口径与 `testmode.note_segment` 的 `end_punct` **逐字相同**（含 `…`）。
                seg_end_punct[_fseg] = fragment_merge.asr_end_punct(text)
                if tester is not None:
                    tester.note_segment(buf, text, r.ms, r.source, logprob=r.logprob,
                                        fell_back=r.fell_back,
                                        n_sentences=len(split_sentences(text)),
                                        final_text=r.trace.get("primary_text"),
                                        final_degenerate=r.trace.get("primary_degenerate", False),
                                        draft_text=r.trace.get("fallback_text"),
                                        steps=r.steps, trace=r.trace)
                if not text:
                    continue
                if carry["text"]:                           # 与上句半截拼接
                    text = f"{carry['text']} {text}".strip()
                    carry["text"], carry["since"] = "", 0.0
                # 只看最后一句：它未说完且未超上限 -> 挂起等下一段；前面说完的立刻出
                ready, tail = split_carry(text, CARRY_MAX_WORDS)
                if tail:
                    busy["on"] = True                 # ⚠️ 先置 busy，再动 carry（all_settled 的四项不能同时为空）
                    carry["text"], carry["since"], carry["seg"] = tail, time.monotonic(), _fseg
                    try:
                        if ready:
                            _emit(ready, _fseg)
                    finally:
                        busy["on"] = False
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
                    _emit(text, _fseg)
                finally:
                    busy["on"] = False
            except Exception as e:                       # noqa: BLE001
                echo(f"⚠ 定稿失败: {e}")
                busy["on"] = False
        _force_emit_carry()                          # 收尾: 残余半句也翻掉

    # ---- 草稿译文线程 ----
    # 原来挂在 final_worker 的空闲分支(finalq 空才轮到), 定稿一忙就被挤掉(回放应触发 570 次,
    # 真课只出 417 条)。独立成线程后与定稿并行; 过期译文在 drain() 里丢(见 DraftView)。
    # 本地引擎(MLX 全局锁)时别和定稿抢: 定稿在排队/在跑就让开。
    def _draft_tr_paused() -> bool:
        """窗口不可见时草稿译文也不送（见 `DraftTranslator.paused`）。⚠️ 只在这个线程调。"""
        if _ui_invisible():
            draft_skipped["zh"] += 1
            return True
        return False

    draft_tr = DraftTranslator(
        translate=lambda text, on_zh, ctx: translator.translate_draft(text, on_zh, context=ctx),
        output=lambda s, g, d: drafts_zh.put((s, g, d)),
        mode_ok=lambda: trans["mode"] == "both",
        should_yield=lambda: not translator.uses_cloud and (not finalq.empty() or busy["on"]),
        context=lambda: finals[-1:], stats=draft_stats,
        # ⭐ 3.9.7 省电：窗口不可见时**不发翻译请求**（草稿是给眼睛看的，看不见就别花这份钱/电）。
        #    定稿翻译**不在这里** —— 它照常，一条都不少。
        paused=_draft_tr_paused)

    # ---- Soniox 草稿线程（本地那半边由 `partial_worker` 管，它不参与）----
    # ⚠️ `partial_worker` 阻塞在 `latest.take()`，Soniox 模式下**没有东西进 `latest`**
    #    （两道闸关着）—— 所以另起一条轻线程按 0.15 s 轮询 `draft_text()`，变了才入队。
    # 元组形状与本地路径**逐字一致**（7 元组），引擎名 `soniox`；
    # `buf_s` = 本段已有多少秒音频（`gate.buf_s()`，与本地 `len(buf)/SR` 同口径）。
    # ⚠️ 窗口不可见时**不放草稿**（与本地同规矩）；不记 `draft_skipped` —— 那个数记的是
    #    "跳过的识别份数"，轮询次数没有对应关系，记进去只会把指标弄脏。
    _drafts_done = threading.Event()

    def stream_draft_worker():
        _policy = os.environ.get("CLASSLIVE_DRAFT_TRIGGER", "novel").strip() or "novel"
        if _policy not in ("legacy", "clause", "novel"):
            _policy = "novel"
        last_t, last_seg = "", None
        prev_t = ""                      # 上一次的草稿原文（`clause` 档自己派生 append_only）
        last_tr, last_len, last_sent = 0.0, 0, ""
        seq = 0
        while running.is_set() and not _drafts_done.is_set():
            time.sleep(0.15)
            if _ui_invisible():
                continue
            try:
                t = gate.draft()         # 没活 / 没货 -> ""
                if not t:
                    continue
                _seg = gate.draft_seg(seg_gen["n"])   # 取当前段；本段已封过句 -> 下一号（见 draft_seg）
                if _seg != last_seg:     # 新段：上一段的"最后一句"不能拿来去重
                    last_seg, last_t = _seg, ""
                if t == last_t:
                    continue
                last_t = t
                _now = time.monotonic()
                drafts.put((t, t, gate.buf_s(), 0.0, _seg, _now, stream.ENGINE))
                # 草稿译文照旧（DraftTranslator 独立线程）；节流判据与本地路径同一份
                # `draft_policy`（只是这条路上没有草稿模型，`compute_s` 恒 0）。
                if trans["mode"] == "both":
                    _nw = len(t.split())
                    _ok = draft_policy.should_translate(
                        _policy, now=_now, last_tr=last_tr, n_words=_nw,
                        last_len=last_len, text=t, prev_text=prev_t,
                        sent_text=last_sent)
                    prev_t = t
                    if _ok:
                        last_tr, last_len, last_sent = _now, _nw, t
                        seq += 1
                        draft_tr.submit(seq, _seg, strip_dangling(t))
            except Exception as e:                             # noqa: BLE001
                echo(f"⚠ 实时草稿失败: {e}")

    def _on_partial(buf):
        """`Segmenter` 的每份进行中缓冲。

        ⚠️ Soniox 在出词（`gate.allow_local()` 为假）时**本地草稿整条不跑** —— 不入
           `latest` 就等于不喂草稿模型（省一次推理，也避免和 Soniox 出两份草稿）。
        """
        if gate.allow_local():
            latest.put(buf, seg_gen["n"])

    def _utterance_end(buf):
        k = _open_segment()
        # ⭐ 第 4 项：本段音频时长（跨段间隔 gap 的一半输入，见 `_merge_gap`）。
        seg_dur[k] = len(buf) / SR
        # ⚠️ 两道闸：Soniox 活着时本地定稿队列**和**草稿收尾都不入队（本地 ASR 不跑）。
        #    退回后 `gate.allow_local()` 立刻为真 -> 从**下一段**起本地照常。
        if gate.allow_local():
            finalq.put((buf, k))
            _wake_fw.set()               # Soniox 模式下 `final_worker` 可能在 Event 上等
            latest.put_end(buf, k)       # 草稿线程据此给这一段收尾（见 partial_worker）

    seg = Segmenter(
        on_partial=_on_partial,
        # ⭐ 段落**真正结束**的那一刻就取段号，随 `finalq` 一路带到定稿 —— 现场采集的分组靠它。
        #    ⚠️ 不这么做就得等定稿上屏才自增，那比段落结束晚 2–4 秒（Whisper + LLM）。
        on_utterance_end=_utterance_end,
        # ⭐ 撞 12s 上限时切在本段最后一个句号前的停顿上（3.9.4，`vad.find_sentence_cut`）。
        #    只读草稿线程最近一次放下的元组，音频线程里调也不阻塞；段号不对（草稿还没跟上新段）→ ()。
        sentence_ends=lambda: draft.sentence_ends(seg_gen["n"]),
    )
    # ⭐ 音频开始**之前**给 Soniox 打一次基准段号（本地第 0 段 = 流里的段 1）。
    #    ⚠️⚠️ 不能省：`seg_for_audio()` 对"第一个 mark 之前的音频"返回**当时的 `_seg`**
    #    （`seg or self._seg`）—— 而 `_seg` 会随着后面的 mark 一直涨。第一段那句要是
    #    封口晚于下一次开段，就会按"当时的段数"算出一个偏大的段号（归错段）。
    #    先打一个采样位置 0 的点，从此每段边界都是"开始采到多少样本"，映射单调。
    gate.mark()

    # ---- 专有名词查询线程(网络请求, 异步且缓存, 不影响主链路) ----
    def proper_worker():
        while running.is_set():
            try:
                term = properq.get()        # 阻塞等（3.9.7 省电）；停机靠收尾投的 `None` 哨兵
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
                q = answerq.get()           # 阻塞等（3.9.7 省电）；停机靠收尾投的 `None` 哨兵
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

    def hint_worker():
        """❓ 一行点拨线程。形状照抄 answer_worker: daemon + 只认自己的队列 + 只判 running。

        连按几下只讲一次: 取到一份后把积压的取空、只留最新的(旧的已被新的覆盖)。
        ⚠️ 不碰 busy / finalq / carry(`all_settled` 的输入); 点拨可丢。
        ⚠️ 写 `streamq` 前判 `stopping`: streamq 是 `all_settled()` 的输入之一,
           收尾中还往里写会拖慢收尾(同 answer_worker 的 `_emit`)。"""
        while running.is_set():
            try:
                lines = hintq.get()         # 阻塞等（3.9.7 省电）；停机靠收尾投的 `None` 哨兵
            except queue.Empty:
                continue
            if lines is None:
                break
            while True:
                try:
                    nxt = hintq.get_nowait()
                except queue.Empty:
                    break
                if nxt is None:
                    return
                lines = nxt
            # `EngineRouter.hint` 自己吞异常(失败/本地 = ""), 这里不重复 try
            text = translator.hint(lines) if lines else ""
            if not stopping.is_set():
                streamq.put(("hint", text))

    # ---- 跨段半句合并（3.9.7 第 4 项，`fragment_merge` 文件头）----
    # 半句照常先上屏（零额外等待）；下一段首句定稿回来后，用**同一个**修正翻译调用把
    # 「尾句 + 首句」合起来重翻，再原子替换屏上那一行、吞掉首句那一行。**独立 worker**，
    # 不阻塞定稿线程的后续句子；在飞期间收尾要等它（有 15s 上限）。
    mergeq: "queue.Queue[dict]" = queue.Queue()
    merge_stats = {"triggered": 0, "replaced": 0}
    _merge_lock = threading.Lock()
    _merge_inflight = [0]            # 已入队未落地（含正在重翻）的合并数 —— 收尾判据用它

    def _merge_inc() -> None:
        with _merge_lock:
            _merge_inflight[0] += 1

    def _merge_dec() -> None:
        with _merge_lock:
            _merge_inflight[0] -= 1

    def merge_pending() -> bool:
        """还有合并没落地吗（入队了或正在重翻）—— 收尾时算「忙」。"""
        with _merge_lock:
            return _merge_inflight[0] > 0

    def _merge_gap(i: int, j: int) -> float | None:
        """段 i→j 的音频间隔（秒）：(结束_j − 时长_j) − 结束_i。同 design.md §3。

        ⚠️ 用**墙钟**近似音频秒（实时音源下二者同步）。拿不到（段号漂了 / 回放倍速）→
        `None`，`should_merge` 一律不触发（宁可不合，不许合错）。
        """
        a, b, d = seg_ended_at.get(i), seg_ended_at.get(j), seg_dur.get(j)
        if a is None or b is None or d is None:
            return None
        return (b - d) - a

    #: 悬浮窗才有"一行"可替换 —— 终端 UI 没有 `replace_merge`，追踪器为 `None`、整条路不启动。
    #: 档位判据（只在**双语**档触发）在 `drain()` 里**逐行现判**：三档课中可切（同实时总结）。
    merge_tracker = (fragment_merge.MergeTracker(_merge_gap)
                     if callable(getattr(ui, "replace_merge", None)) else None)

    def merge_worker():
        """重翻「尾句 + 首句」→ `("replace", job, en, zh)`。失败/超时 → 空串，屏上原样。

        ⚠️ 整段包 `try`：worker 一死，`_merge_inflight` 就永远 >0 → 收尾白等满 15 s。
        """
        while running.is_set():
            try:
                job = mergeq.get()          # 阻塞等（3.9.7 省电）；停机靠收尾投的 `None` 哨兵
            except queue.Empty:
                continue
            if job is None:                 # 收尾哨兵：先把它叫醒的那批 job 都做完了
                break
            try:
                en, zh = fragment_merge.translate_merge(
                    job, translator.merge_translate)     # 只走云端、不碰熔断（见该方法）
                streamq.put(("replace", job, en, zh))
            except Exception:                         # noqa: BLE001  丢了这一次也不能打死 worker
                pass
            finally:
                _merge_dec()

    t1 = threading.Thread(target=partial_worker, daemon=True)
    t2 = threading.Thread(target=final_worker, daemon=True)
    t3 = threading.Thread(target=proper_worker, daemon=True)
    t4 = threading.Thread(target=answer_worker, daemon=True, name="answer-qa")
    t5 = threading.Thread(target=draft_tr.run, args=(running,), daemon=True, name="draft-tr")
    t6 = threading.Thread(target=hint_worker, daemon=True, name="lost-hint")
    t7 = threading.Thread(target=merge_worker, daemon=True, name="frag-merge")
    # Soniox 草稿轮询：**只在真建了 StreamASR 时**才起（没建 = 与改前逐字一致）。
    ts = (threading.Thread(target=stream_draft_worker, daemon=True, name="stream-draft")
          if gate.on else None)
    t1.start(); t2.start(); t4.start(); t5.start(); t6.start(); t7.start()   # 问答不需要 key 就能起步
    if ts is not None:
        ts.start()
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

    # ⭐ 3.9.7 省电：实时采音的**新块到达**也要立刻叫醒主循环（它可能正睡在 0.1/0.25 s 的让步里）。
    #    `CallbackSource` 在声卡回调线程调（`wake()` 跨线程安全）；文件回放由主循环自己 poll，
    #    不需要这条。拿不到 waker 的源（替身 / 老对象）→ 什么都不做。
    if callable(_wake):
        _sw = getattr(src, "set_waker", None)
        if callable(_sw):
            try:
                _sw(_wake)
            except Exception:                    # noqa: BLE001
                pass
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
        # ⭐ 一些事儿（2026-10-17）：同一份 payload 里 `kind == "deadline"` 的那些
        #    再喂给板子 —— **复用** `live_summary` 已认出来的候选（正则立刻上屏 +
        #    模型过 Jev 闸门），这里只多做一道"有没有时间指向"的过滤（在 `duty` 里）。
        #    ⚠️ 这一行在 **worker 线程**：`Board.offer` 自己带锁、只追加，绝不许抛。
        if isinstance(payload, dict) and payload.get("kind") == "deadline":
            try:
                if duty_board.offer(payload) is not None:
                    _duty_push()
            except Exception:                                # noqa: BLE001
                pass
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

    def _pair(item) -> tuple:
        """`drafts` 队列的元素 → `(raw, shown, buf_s, compute_s, seg, t_put, engine)`。

        ⚠️ 产出方只有 `partial_worker` 一处（永远 7 元组），所以**按形状直接拆** ——
           不再逐层猜"是不是裸字符串/二元组"（那种兼容梯子没有调用方，还会把
           `buf_s` 静默猜成 0.0，而 `buf_s` 猜错 = 静默改掉回改率的口径）。
        ⚠️ 但**必须全函数不抛**：它在 `drain()` 里，而 `drain()` 没有 try/except ——
           这里抛一次 = 打死主循环 = 整节课的字幕停摆。形状不对就当空草稿丢掉。
        """
        try:
            return (item[0], item[1], float(item[2]), float(item[3]), int(item[4]), float(item[5]),
                    str(item[6]))
        except Exception:                                  # noqa: BLE001
            return ("", "", 0.0, 0.0, 0, 0.0, "")          # ⚠️ 与上一行同形（7 项），否则 drain 拆包就抛

    def drain():
        while True:
            try:
                _raw, _shown, _buf_s, _cpt, _seg, _t_put, _eng = _pair(drafts.get_nowait())
            except queue.Empty:
                break
            if draft_view.stale_en(_seg):            # 晚到的旧段草稿（段尾收尾那份）：不许盖回去
                continue
            if draft_view.on_draft(_seg):            # 新一段的第一份英文草稿: 清掉上一段残留的中文草稿
                ui.draft_zh("")
            if live is not None:
                # 吞异常，不会打死主循环。`lag_s` 在**这里**算：drain 就是上屏那一刻
                live.draft(_raw, _shown, seg=_seg, buf_s=_buf_s, compute_s=_cpt,
                           lag_s=(time.monotonic() - _t_put) if _t_put else None,
                           engine=_eng or None)
            ui.add_draft(_shown)
            last_draft["en"] = _shown
        while True:
            try:
                _it = drafts_zh.get_nowait()
            except queue.Empty:
                break
            try:                                     # drain() 没有 try/except: 形状不对就丢, 绝不抛
                _dseq, _dseg, _zh = int(_it[0]), int(_it[1]), str(_it[2])
            except Exception:                        # noqa: BLE001
                continue
            if not draft_view.accept(_dseq, _dseg):  # 这段已定稿上屏 / 更新的译文已显示过
                continue
            if live is not None:
                live.draft_zh(_zh)
            ui.draft_zh(_zh)
        while True:
            try:
                item = streamq.get_nowait()
            except queue.Empty:
                break
            if item[0] == "zh":                     # ("zh"/"en", 增量, seg)
                draft_view.on_stream(item[2] if len(item) > 2 else None)
                ui.stream_zh(item[1])
            elif item[0] == "en":
                draft_view.on_stream(item[2] if len(item) > 2 else None)
                ui.stream_en(item[1])
            elif item[0] == "en_seed":              # ("en_seed", ASR 原句, seg)
                draft_view.on_stream(item[2] if len(item) > 2 else None)
                fn = getattr(ui, "seed_en", None)   # 终端没有这一槽
                if callable(fn):
                    fn(item[1])
            elif item[0] == "terms":
                if item[1]:
                    ui.terms(item[1])
            elif item[0] == "notice":
                msg, warn, still = item[1]
                ui.notice(msg, warn, still)
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
            elif item[0] == "duty":                 # ("duty", [条目…]) 一些事儿的当前状态
                # ⭐ 板子（`duty.Board`）是唯一真源，这里把它的投影推给悬浮窗：
                #    角落小卡 + 打开中的可编辑卡片都从这份投影重画。
                # ⚠️ 与 summary 那条同一个 getattr 守卫：TerminalUI 没有 `duty_update`，
                #    而 `drain()` 没有 try/except，直接调会打死主循环。
                fn = getattr(ui, "duty_update", None)
                if callable(fn):
                    fn(item[1])
            elif item[0] == "duty_result":          # ("duty_result", id, ok, 原因)
                # ⚠️ 回执走 ui 那一侧；不放任「失败静默」是这一条的存在理由。
                # ⭐ 功能使用度：写成没 + **失败原因归成哪一类**（原文不进报告 ——
                #    见 `feature_usage.reason_kind`）。
                _feature("duty.reminder", ok=bool(item[2]),
                         why=feature_usage.reason_kind(str(item[3])))
                fn = getattr(ui, "duty_reminder_result", None)
                if callable(fn):
                    fn(item[1], bool(item[2]), str(item[3]))
            elif item[0] == "recap":                # ("recap", {上一节的回顾})
                fn = getattr(ui, "recap_show", None)
                if callable(fn):
                    fn(item[1], course=str(item[2]) if len(item) > 2 else "",
                       day=item[3] if len(item) > 3 else None)
            elif item[0] == "recap_more":           # ("recap_more", {今天的行})
                # 📄「今天可能讲什么」晚 1–2 秒补进来（课件抽文本在 worker 上跑）
                fn = getattr(ui, "recap_update", None)
                if callable(fn):
                    fn(item[1])
            elif item[0] == "hint":                 # ("hint", 一句话 | "")  ❓ 点拨
                # 空 = 本地 / 失败 / 没内容 -> 退回"课后笔记里能找到"(别让"正在提炼…"挂到 20s 才消)
                fn = getattr(ui, "show_hint", None)
                if callable(fn):
                    if item[1]:
                        fn("💡 " + item[1], 15)
                    else:
                        fn("❓ 已标记 · 课后笔记里能找到这几句", 6)
            elif item[0] == "final":                # ("final", en, zh, asr_raw[, seg[, lat]])
                # ⚠️ `item[4]` / `item[5]` 都用 `len` 守卫：`("final", …)` 这条 tag
                #    有 3 个产出点，加字段是**加法**（下标 1–3 一个没动），老形状照样能读。
                #    `item[5]` = 译文延迟 4 元组 `(zh_first_ms, zh_done_ms, seg_to_zh_ms,
                #    tr_model)`（`tr_latency.TrTimer.done()` 的产物）——
                #    ⚠️ **只有双语档那个产出点给**（见 `_emit`），其余两个是 `None`。
                _lat = item[5] if len(item) > 5 else None
                if live is not None:
                    live.final(item[1] or item[3], item[2], item[3],
                               seg=int(item[4]) if len(item) > 4 else 0, lat=_lat)
                # 先更新 fin_seg 再算 clear_draft: 屏上已是更新一段的草稿时, 旧段定稿不碰草稿槽
                _clr = draft_view.on_final(item[4] if len(item) > 4 else None)
                _seg_n = int(item[4]) if len(item) > 4 else 0
                # ⭐ 返回值 = 这一行的**身份号**（悬浮窗铸；终端返回 None）—— 跨段半句合并
                #    靠它定位"尾句那一行"，不靠行号（见 fragment_merge / transcript_view.LineLog）。
                _tok = ui.finalize(item[1] or item[3], item[2],   # 翻译失败时至少显示转录
                                   clear_draft=_clr)
                if _clr:
                    last_draft["en"] = ""           # 草稿已被这句定稿取代 -> ❓ 点拨别再带上它（否则同一句出现两次）
                _ts = writer.append(item[1], item[2], raw=item[3])
                # ⭐ 跨段半句合并（第 4 项，design.md §4）：把这一行喂给记账器；它在本行恰是
                #    某段**首行**且上一段尾句是碎句、gap 又够近时回一份 job。
                #    ⚠️ 走**独立 worker**（不阻塞下面几句）；结果走新 tag `replace`。
                if (merge_tracker is not None and _tok is not None
                        and trans["mode"] == "both"):
                    try:
                        _job = merge_tracker.on_line(
                            _seg_n, _tok, item[1] or item[3], item[2], _ts,
                            seg_end_punct.get(_seg_n, True))
                    except Exception:               # noqa: BLE001  记账坏了只是少合一次, 绝不打死主循环
                        _job = None
                    if _job is not None:
                        # 触发那一刻的前文窗口快照：**不改 `finals`**（改了会让之后所有请求的
                        # 块对齐下标前移、前缀缓存全废，见 design.md §1.d）。
                        _job["ctx"] = list(finals)
                        merge_stats["triggered"] += 1
                        _merge_inc()                # 先记「忙」再入队：收尾闸门不会漏看它
                        mergeq.put(_job)
                # ⭐ 采集面 ③：`item[1]` 是**矫正后**的英文、`item[3]` 是**原始 ASR**
                #    —— 只有**这里**同时拿得到这一对（`note_segment` 那边没有）。
                #    ⚠️ `note_correction` 自己是 `_safe` 包的，**不可能抛**，
                #       所以放在 `drain()` 里是安全的（这里没有 try/except）。
                if tester is not None:
                    tester.note_correction(item[1], item[3])
                # ⭐ 译文延迟（第 3 项）同样只在这里落地 —— 就在它那句 `final` 旁边。
                #    ⭐ 3.9.8 ⑥：`idle_before_ms` 从 `_tr_idle` 取（与带 `lat` 的 final 一一
                #    对应；空 deque 兜一圈给 None —— 采集少一个数，绝不许打死 drain）。
                if tester is not None and _lat is not None:
                    tester.note_translate(
                        *_lat,
                        idle_before_ms=(_tr_idle.popleft() if _tr_idle else None))
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
            elif item[0] == "replace":              # ("replace", job, en, zh) 跨段半句合并
                # ⚠️⚠️ **绝不复用 `("final", …)`**（design.md §1.e）：那条会重复写 `.md`（违约）、
                #    喂实时总结、计 `livemetrics.final`（污染 seg 分组与回放统计）。
                #    这里只做三件事：原子替换屏上两行、记一条 livemetrics 数字、追加旁路凭据。
                #    ⚠️ 失败（重翻失败 / 行已被别的路径改过）→ 什么都不做，**屏上保持原样**。
                _job, _en, _zh = item[1], item[2], item[3]
                _fn = getattr(ui, "replace_merge", None)
                _ok = False
                if (_en or _zh) and callable(_fn):
                    try:
                        _ok = bool(_fn(_job["tail_tok"], _job["next_tok"], _en, _zh))
                    except Exception:               # noqa: BLE001  drain 无 try/except, 这里自己兜住
                        _ok = False
                if _ok:
                    merge_stats["replaced"] += 1
                    if live is not None:
                        live.replace(_job.get("i", 0), _job.get("j", 0))
                    # 旁路凭据（照 `.lost.jsonl` 那套纪律）。⚠️ 它失败**不代表**屏上没替换。
                    writer.mark_merge(_job.get("tail_ts", ""), _job.get("next_ts", ""),
                                      _en, _zh, gap_s=_job.get("gap_s"),
                                      trigger=_job.get("trigger", ""))
                # ⭐ 3.9.8 ③：这一次合并的**原文**（尾句 / 首句 / 合并结果 / 换没换成功）。
                #    ⚠️ 落在 `_ok` 之后 —— 重翻失败那条也记（`merged_*` 为空、replaced=False），
                #       那正是「翻得好不好」要看的另一半。`note_merge_item` 自己是 `_safe`。
                if tester is not None:
                    tester.note_merge_item(_job, _en, _zh, _ok)
            else:
                # ⚠️ **兜底告警**：往 `streamq` 里加了新 tag 却忘了在 `drain` 加分支时
                #    **出声**，而不是静默丢弃。`drain()` 是**唯一**的 tag 分发点。
                #    ⚠️ 只 `echo`，不做任何可能抛的事 —— 这里没有 try/except。
                echo(f"⚠ drain: 未识别的 streamq tag {item[0]!r}（已丢弃）")
        # 定稿流式期间草稿栏显不显示：屏上草稿是更新一段的就照常显示（判据在 DraftView）。
        # 每轮问一次；悬浮窗只在值变了时才重画。终端没有草稿栏 → 没有这个方法。
        _over = getattr(ui, "draft_over_stream", None)
        if callable(_over):
            _over(draft_view.over_stream())

    _hear = getattr(ui, "hearing", None)      # 顶栏「在听」小点; 定义在 try 外 —— finally 要用
    _hear = _hear if callable(_hear) else None
    try:
        _dev = {"wall": time.time(), "next": 0.0}
        # 逐块记录（现场采集开着才有）：原始峰值 / 实际增益 / 当场 VAD。见 livemetrics「逐块记录」
        _last_block = getattr(src, "last_block", None)
        # 收音持续数字静音 → 菜单栏 ⚠️（麦克风被静音 / 设备不对时字幕只是一直空着，没有别的症状）。
        # 文件回放不看；系统声在网课暂停时本来就静音，等得久一些。
        _silence_cfg = _SILENCE_WATCH.get(args.source)
        _silence = (SilenceWatch(_silence_cfg[0])
                    if _silence_cfg is not None and callable(_last_block) else None)
        while running.is_set() and not stopping.is_set():
            chunk = src.poll()
            if chunk is not None and len(chunk):
                # ⚠️ 喂音频**排在 `seg.accept()` 之前**：段尾那一刻 `_utterance_end` 会
                #    `mark_segment()`，它记的是"现在采到多少样本" —— 后喂会让 Soniox 的
                #    段界少这一块，边界附近那句就会归错段（见 `stream_gate.mark`）。
                #    还没退回才喂（`can_feed`）；**建连中也喂**（Soniox 就靠这段接上话）。
                if gate.can_feed():
                    stream.feed(chunk)       # 非阻塞：有界队列，满了丢最旧
                seg.accept(chunk)
                if _hear is not None:
                    _hear(seg.is_speaking)        # 顶栏「在听」小点; 只在变了时才动
                if tester is not None:
                    tester.note_chunk(chunk)
                if callable(_last_block):
                    _p, _g = _last_block()
                    if live is not None:
                        live.block(_p, _g, seg.last_ratio, len(chunk))
                    _sv = _silence.feed(_p, len(chunk)) if _silence is not None else None
                    if _sv == "silent":
                        notify(_silence_cfg[1], True, src="收音")
                    elif _sv == "back":
                        notify("收音恢复了", False, src="收音")
            # ⭐ Soniox 退回：**整节课只报一次**（`take_notice` 自己记账），走现有
            #    `("notice", (文案, warn, 补充))` 通道 —— 不新增 tag（R28）。
            #    两道闸从那之后自动放开（`allow_local()`），下一段起本地照常。
            _rt = gate.take_notice()
            if _rt:
                streamq.put(("notice", (_rt, False, "")))
            #    退回后屏上也回到本地的样子：本地按 VAD 段出句，短尾巴并行会把本地短句并掉。
            if _tail["on"] and gate.fell_back:
                _tail["on"] = False
                ui.set_tail_merge(False)
            drain()
            if src.is_done():
                break
            if time.monotonic() >= _dev["next"]:
                _dev["next"] = time.monotonic() + DEVICE_CHECK_S
                check_clock_and_device(src, args.source,
                                       lambda m, warn=False: notify(m, warn, src=""), _dev,
                                       on_wake=_reset_net)
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
        if _hear is not None:            # 停了就别再亮着「在听」(收尾那 15 s 窗口还在)
            try:
                _hear(False)
            except Exception:                       # noqa: BLE001
                pass
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
            # ⭐ Soniox 收尾：**先**把流里那半句 flush 出来（`on_sealed` 只入队），
            #    再投哨兵。顺序反过来 = `final_worker` 见到 `_QUIT` 就 break，
            #    `sealedq` 里剩下的封口句全丢（和 `seg.flush()` 那一处的纪律同源）。
            #    ⚠️ 排在 `seg.flush()` **之后**：flush 会把最后那半句的音频交给
            #       `_utterance_end`，此时流还活着 -> 本地那道闸仍关着，尾巴不会做两遍。
            if gate.on:
                _drafts_done.set()        # 草稿轮询线程先停（别再往 `drafts` 里放）
                gate.stop()               # 内部 `stream.stop()`：最多等 finish_wait_s
            finalq.put(_QUIT)
            _wake_fw.set()
            deadline = time.monotonic() + 15
            _settled = False
            # ⚠️ `all_settled` 只看 finalq/streamq/drafts/busy/carry 五项 —— Soniox 的
            #    `sealedq` 是**第五**条队列，漏了它会在还有封口句没上屏时提前收摊
            #    （`gate.on` 为假时它恒为空，判据与改前逐字一致）。
            while time.monotonic() < deadline:
                drain()
                if (all_settled(finalq, streamq, drafts,
                                busy["on"] or merge_pending(), carry["text"])
                        and sealedq.empty()):
                    time.sleep(0.25)
                    if (all_settled(finalq, streamq, drafts,
                                    busy["on"] or merge_pending(), carry["text"])
                            and sealedq.empty()):
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
            # ⭐ 3.9.7 省电：把还**阻塞在队列上**的后台线程叫醒收摊。
            #    ⚠️ 只在 `running.clear()` **之后**投 —— 不早投就不会改变收尾时序
            #       （`all_settled` 那 15 s 窗口里这些 worker 照常干活、照常入队）。
            #    `properq`/`answerq`/`hintq` 的循环体本来就认 `None` 哨兵；
            #    `mergeq` 的哨兵排在已入队的 job **之后**，先做完再退（`_merge_inflight`
            #    不会因为早退而永远 >0 → 不会把 `all_settled` 拖到 15 s）。
            for _q in (properq, answerq, hintq, mergeq):
                try:
                    _q.put_nowait(None)
                except Exception:                        # noqa: BLE001
                    pass
            try:
                latest.stop()                            # 叫醒 `partial_worker` 的 `take()`
            except Exception:                            # noqa: BLE001
                pass
            try:
                draft_tr.stop()                          # 叫醒草稿译文线程的 `step()`
            except Exception:                            # noqa: BLE001
                pass
            if any(draft_stats.values()):             # 草稿译文: 丢了几条/让路几次/出错几次(全零不打印)
                echo(f"📝 draft_stats: {draft_stats}")

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
            # 精修实测 11 分钟（579 句 ÷ 30 一批 = 20 批）—— **那是 2026-10-12 之前**，
            # 精修现已默认不跑；但复习层 / 提示层照旧跑在这条 worker 上，纪律不变。
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
                """守着 pump 等 `until()` 为真，或用户关窗。逻辑在 `spin_until`（可测）。

                ⚠️⚠️ **这一步就是"不冻主线程"本身** —— 没有它，收尾期间窗口是死的，
                   ✕ 按不动，`terminate:` 也收不到。
                ⚠️ 判据是 `drives_appkit`（**UI 对象自己说的**），不是 `args.ui`：
                   `_load_overlay` 失败时会**静默回退** `TerminalUI()`，那时 args 还写着
                   overlay → 每轮都调一个空 pump、**一次 sleep 都没有 → 纯烧 CPU**。
                   （2026-09-28 OCR 审计发现。）
                ⚠️ "窗口一进来就是关的"那种情况见 `spin_until` 的 docstring —— 那正是
                   测试报告整整一周没写成的根因。"""
                appkit = bool(getattr(ui, "drives_appkit", False))

                def _tick() -> None:
                    if appkit:
                        ui.pump()
                    else:
                        time.sleep(0.02)

                spin_until(until, _tick, _ui_gone)

            save, give_up, _box = True, False, {}
            # ⚠️ 没设笔记库就**不问**「存入 Obsidian 吗？」—— 那是在问一件做不到的事
            #    （2026-10-12 朋友实测：点了「存」，结论卡 3 秒就关，她以为存好了，
            #    好几节课都没笔记）。改成直接开卡，结论留着等她点关闭（见下面 `_hold`）。
            _plan = _wrapup_plan(writer.enabled, writer.writes_notes, writer.mode)
            _no_notes = _plan == "notice"
            if _no_notes:
                if _wrapup_route(ui, _ui_gone()) == "ui":
                    _open = getattr(ui, "wrapup_open", None)
                    if callable(_open):
                        _open()
            elif _plan == "ask":
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

            # ⭐ 一些事儿：下课把**漏处理的一次性确认**（原型 §④，挂在收尾这条路上）。
            #    ⚠️ 位置：`ask_save` 之后、`writer.close()` 之前的 worker **之前** ——
            #       `obsidian_writer` 渲染笔记时要读决定（旁路文件），所以决定必须先落。
            #    ⚠️ 超时不是错误：没确认的不写任何地方，笔记里标「未确认」。
            if not give_up:
                _dw = getattr(ui, "duty_wrapup", None)
                if callable(_dw):
                    try:
                        _n_dw = _dw(duty_board.items(), timeout=20.0)
                        if _n_dw:
                            echo(f"📌 一些事儿：收尾确认了 {_n_dw} 条")
                    except Exception as e:                    # noqa: BLE001
                        echo(f"⚠ 一些事儿收尾汇总失败（笔记照写）："
                             f"{type(e).__name__}: {str(e)[:60]}")

            _cancel = threading.Event()
            if not give_up:
                # ⚠️ 2026-10-12 起**卡上不再有 [跳过精修] 那个按钮**（精修默认不跑，
                #    按钮没意义了）—— `wrapup_begin` 留着当口子，调用点不动。
                # ⚠️ 但 `_cancel` **仍然要建、要传下去**：Ctrl+C 靠它，
                #    `writer.close(cancel=…)` 在批次边界放弃精修与复习层。
                _ui_begin = None if _no_notes else getattr(ui, "wrapup_begin", None)
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
                        # ⭐2：本课候选词池那一行也走**现成通道**（收尾卡/终端串）——
                        # 不新建 UI。⚠️ 追加而不是新 print：收尾卡里只有这一段文字，
                        # 分开 print 在终端会跟卡片错位。**失败绝不影响收尾**。
                        try:
                            if live_pool is not None:
                                _box["msg"] = (str(_box["msg"] or "")
                                               + "\n   " + live_pool.report())
                        except Exception:                 # noqa: BLE001
                            pass
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
                        echo("\n⏹ 已请求跳过剩下的收尾步骤，正在把笔记写出来…"
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
                # ⭐ 说一声这段去哪了、怎么补（`_gave_up_hint` 的 docstring）。
                _card, _term = _gave_up_hint(getattr(writer, "session_path", None),
                                             args.course, writer.count)
                if _term:
                    echo(_term)
                    _box["msg"] = _card

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
            # 没生成笔记那句（「下次在面板上点笔记库」）和失败一样要留住 —— 3 秒没人读得完。
            _hold = _wrapup_hold(_ok, _plan, msg)
            if not _hold:
                # 让「已写入 …」在屏上留 3 秒 —— 一闪而过等于没说。
                _t3 = time.monotonic() + 3.0
                _spin(lambda: time.monotonic() >= _t3)
            else:
                # ⚠️ **失败不自动退**（作者 2026-09-28 定的口径）：留到他看见为止。
                #    没库那句同理（2026-10-12）。
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
                # ⭐ 一些事儿（2026-10-17）：认到几条 / 记下几条 / 忽略几条 / 推算几条。
                #    ⚠️ 只读快照，不参与任何判定（同 `live` 那一格）。
                tester.note_block("duty", _duty_stats())
                # ⭐ 第 4 项：跨段半句合并的计数。`failed` = 触发了却没换成
                #    （重翻失败 / 超时 / 行已被别的路径改过）—— 从两个计数器算出来，
                #    不另立一个可能不同步的真源。
                tester.note_block("merge", {
                    "triggered": merge_stats["triggered"],
                    "replaced": merge_stats["replaced"],
                    "failed": max(0, merge_stats["triggered"] - merge_stats["replaced"])})
                # ⭐ 3.9.8 ①：能耗 / 唤醒 / 供电（开课时 `TestSession.__init__` 已取第一次
                #    快照，这里取第二次并关掉最后一个分桶）。屏幕刷新率从悬浮窗现读 ——
                #    热档让步就是按它算的，报告里没有它就看不懂"泵每轮等了多久"。
                _hz = None
                try:
                    _hz = getattr(ui, "screen_hz", None)
                except Exception:                         # noqa: BLE001
                    _hz = None
                tester.note_block("energy", tester.energy_block(_hz))
                # ⭐ 3.9.8 ④：收音断档（墙钟 vs 实际音频秒）。⚠️ 用 time.time ——
                #    monotonic 在系统睡眠时不走，而"睡过去了、中间没声音"正是它要抓的。
                tester.note_block("capture", tester.capture_block())
                # ⭐ 译文超出档位行数的次数/分母 —— 原来只在 `transcript_view` 里自增、
                #    哪儿都不导出，「F 只有 ≈1%」只能拿离线回放外推（2026-10-14 C4）。
                _ovs = getattr(ui, "overflow_stats", None)
                if callable(_ovs):
                    try:
                        tester.note_ui_value("zh_overflow", _ovs())
                    except Exception:                     # noqa: BLE001
                        pass
                # ⭐ 3.9.7 省电：窗口不可见的**总时长** + 因不可见跳过的草稿份数
                #    （识别 / 草稿译文各一格）。都落在 `blocks.ui` 里 —— 没有它们，
                #    「省了多少」「是不是整节课都不可见」在报告里看不出来。
                try:
                    tester.note_ui_value("hidden_s", round(float(getattr(ui, "hidden_seconds", 0.0)), 1))
                    tester.note_ui_value("draft_skipped_hidden", draft_skipped["n"])
                    tester.note_ui_value("draft_zh_skipped_hidden", draft_skipped["zh"])
                except Exception:                         # noqa: BLE001
                    pass
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
                # ⚠️⚠️ **报告的真相以磁盘为准，而且它是这一段的唯一真源**
                #    （2026-10-07 OCR 两轮 + 独立复核指出）：`_tf["rep"]` 是
                #    `finish()` 的返回值，而它被 `@_safe_loud` 包着 —— **报告写完了、
                #    随后 `make_bundle` 失败**时异常被吞、照样返回 `None`。
                #    原来下面那句消息已改成看磁盘，**但打印路径与排队上传仍看返回值**
                #    → 报告明明在盘上，却不打路径、也不上传。现在统一到 `_wrote`。
                #    `stem is None`（`--save-notes no`）时报告**本来就不该存在**，不是故障。
                _repfile = (str(tester.stem) + ".report.json"
                            if getattr(tester, "stem", None) is not None else "")
                _wrote = bool(_repfile) and os.path.exists(_repfile)
                if _wrote:
                    echo(f"\n🧪 测试报告: {_repfile}")
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
                elif _repfile:
                    echo("⚠ 测试报告没写成（收尾没跑完？）"
                         " —— 详情看 ~/Library/Logs/ClassLive/app.log")
                if tester.bundle_path:
                    _sz = os.path.getsize(tester.bundle_path)
                    _mb = f"{_sz / 1e6:.1f} MB" if _sz > 1e6 else f"{_sz / 1024:.0f} KB"
                    echo(f"📦 数据包:   {tester.bundle_path}  ({_mb})")
                    if tester.record_audio:
                        echo("   ⚠️ 内含**课堂音频** + 逐字转录(可能有其他同学的声音)")
                    else:
                        echo("   ℹ️ 只含指标与转录文本, **不含音频**（要留: --record-audio）。")
                    # ⚠️ 转码全失败时音频还是 wav 分段（约 10 倍大）—— 屏上要说出来，
                    #    别只写进报告（2026-10-14：别人的机器 3 节课全是 wav、屏上一句没提）。
                    if any(e.get("kind") == "transcode_failed" for e in tester.events):
                        echo("   ⚠ 音频没转成 Opus，按原始 wav 上传（约大 10 倍）"
                             " —— 详情看 report.json 的 events")
                    echo("   收尾会自动上传给作者（关掉: CLASSLIVE_UPLOAD=0）。")
                elif not args.no_bundle:
                    # ⚠️ 只说数据包（2026-10-07 OCR 指出）：报告的实情由上面那条单独说，
                    #    两条都提"报告"会让同一件事报两遍。`--no-bundle`（README 写明可
                    #    「不打包，只写报告」）时 `bundle_path` **合法地**是 None → 不出声。
                    echo("⚠ 数据包生成失败" + ("（报告已写出）" if _wrote else ""))

            # ---- 现场采集收尾 ----
            # ⚠️ **放在 `if tester is not None` 之外**（两者互不依赖）：不开测试模式、
            #    只开 `CLASSLIVE_LIVE_LOG=1` 时也要把 summary 写出去并关文件。
            # ⚠️ 只记**只读计数**，不在这里现算指标 —— 指标归 `scripts/class_report.py`
            #    （它读同一份 JSONL；生产路径只负责"如实记下来"）。
            if live is not None:
                live.close(
                    echo_retries=local_tr.echo_retries,   # 本地小模型回显重试次数（只读计数）
                    mode=translator._mode,
                    source=args.source,
                    draft_engine=draft.name,               # 收尾时生效的（熔断过就是 parakeet）
                    draft_fallback=draft.fallback_reason,
                )
                echo(f"🎥 现场采集已收尾: {live.path}")

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
            # ⭐ 下课了 —— 这才把那个可选的草稿模型放后台下（上课途中绝不下，见它的 docstring）
            _draft_model_on_exit()


#: `SilenceWatch` 按音源的（等多久, 报什么）。⚠️ 只说现象 + 最可能的两个原因，不替用户动系统设置。
#: 系统声那两路（`cl online` 走的是 `blackhole`，`system` 是还没上真机的 Process Tap）
#: 在网课暂停时**本来就是**数字静音，所以等得久、文案也不提麦克风。不在表里的音源（文件回放）不看。
_SYSTEM_SILENT = (60.0, "系统声 60 秒没有任何声音：网课暂停了？没暂停就检查声音输出有没有接到 ClassLive")
_SILENCE_WATCH = {
    "mic": (15.0, "15 秒收不到任何声音：麦克风被静音了，或者输入设备不对？"),
    "blackhole": _SYSTEM_SILENT,
    "system": _SYSTEM_SILENT,
}


def check_clock_and_device(src, source_name: str, notify, state: dict, on_wake=None) -> None:
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
        if on_wake is not None:
            try:
                on_wake()
            except Exception:                             # noqa: BLE001  保安线程不能被它打死
                pass
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


def _click_wall() -> float | None:
    """面板点「开始上课」那一刻的墙钟（`entry_launch.on_start` 写进 `start-click`）。**读完就删** ——
    只认这一次点按，`cl` 从终端起、或者下一次没走面板时不会捡到旧值。拿不到就 `None`。

    ⚠️ 不用 `.course` 的 mtime：`cl course X`、探针都会写它（头一版就这么量出过「进程起来 27 s」的假数）。
    """
    p = instance_lock.STATE_DIR / "start-click"
    try:
        t = float(p.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    try:
        p.unlink()
    except OSError:
        pass
    return t


def startup_timing(tp: dict, wall0: float, click_wall: float | None) -> dict:
    """起步各段耗时 → 一行人话 + 事件字段。**纯函数**。

    `tp` 是单调时钟打点（run → audio → draft → translator → overlay，缺的段跳过）；
    `click_wall` 是点按的墙钟时刻（`_click_wall` 读面板留的戳），只认 2 分钟内的（面板点了却没起来、隔很久才起的不算）。
    """
    order = [("audio", "开麦试探"), ("draft", "草稿模型"), ("translator", "翻译与术语"),
             ("overlay", "定稿 / 落盘 / 悬浮窗")]
    parts, fields, prev = [], {}, tp["run"]
    for k, label in order:
        if k in tp:
            d = tp[k] - prev
            fields[f"{k}_s"] = round(d, 3)
            parts.append(f"{label} {d:.1f}")
            prev = tp[k]
    total = prev - tp["run"]
    fields["run_s"] = round(total, 3)
    # ⭐ 3.9.7 第 5c 项：草稿模型改**后台**加载 → 单列一项「就绪时刻」。
    #    ⚠️ 用**从 run 起算的绝对偏移**，不是上面那种相邻差值：就绪发生在**另一个
    #    线程**上，可能落在 translator / overlay **之后** —— 插进那条链会把
    #    后面几段算成负数（链的前提是各段按时间递增）。
    if "draft_ready" in tp:
        _dr = tp["draft_ready"] - tp["run"]
        fields["draft_ready_s"] = round(_dr, 3)
        parts.append(f"草稿模型就绪 +{_dr:.1f}")
    pre = None
    if click_wall is not None and 0.0 <= wall0 - click_wall <= 120.0:
        pre = wall0 - click_wall
        fields["click_to_run_s"] = round(pre, 3)
    if pre is not None:
        line = f"点「开始上课」→ 字幕出现 {pre + total:.1f} s（进程起来 {pre:.1f} · " + " · ".join(parts) + "）"
        fields["click_to_overlay_s"] = round(pre + total, 3)
    else:
        line = f"起步 → 字幕出现 {total:.1f} s（" + " · ".join(parts) + "）"
    return {"line": line, "fields": fields}


def _load_overlay(on_quit=None, on_translate=None, on_submit=None,
                  on_ask=None, on_new_topic=None, on_lost=None, note=None,
                  on_vis=None, on_wait=None, on_view=None, on_power_event=None,
                  on_duty=None, on_feature=None):
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
                    on_new_topic=on_new_topic, on_lost=on_lost, note=note,
                    on_vis=on_vis, on_wait=on_wait, on_view=on_view,
                    on_power_event=on_power_event, on_duty=on_duty,
                    on_feature=on_feature)
        o.show()
        return o
    except Exception as e:       # noqa: BLE001
        echo(f"⚠ 悬浮窗不可用({e}); 回退终端 UI。")
        return TerminalUI()


def main():
    p = argparse.ArgumentParser(description="本地实时课堂双语字幕")
    p.add_argument("--source", choices=["mic", "blackhole", "system", "file"], default="file")
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
                   help="留下课堂音频（**测试模式下这是默认**；约 9–13MB/50 分钟, "
                        "内含其他同学的声音）")
    p.add_argument("--no-record-audio", dest="record_audio", action="store_false",
                   help="这一节不留音频（覆盖测试模式的默认）")
    # ⭐ 只在**异常段**（主模型空/退化 → 走了回退）另存一份**送进 ASR 的原信号**（float32）。
    #    ⚠️ 默认**关**：它比 opus 更"原始"（未压缩），披露要更明确；体积很小（只有异常段写）。
    #    ⚠️ 存在的理由：离线复现必须**喂同一份信号** —— 拿 16-bit wav 回放会改 ASR 结果
    #       （2026-10-07 实测：同一节 17 段里 9 段改口）。
    p.add_argument("--record-float", action="store_true", default=None,
                   help="异常段另存一份送进 ASR 的原始 float32 信号"
                        "（**测试模式下默认跟随 --record-audio**）")
    p.add_argument("--no-record-float", dest="record_float", action="store_false",
                   help="这一节不存原信号快照（覆盖测试模式的默认）")
    p.add_argument("--no-bundle", action="store_true",
                   help="测试模式下不打包成可发送的单个 zip")
    p.add_argument("--final-model-dir",
                   default=models.path_of("whisper"),
                   help="本地定稿模型(Whisper)目录(可选; 云端不可用时才用, 没有就用 Parakeet)")
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
    # ⚠️⚠️ **默认 `off`**（Owen 2026-10-12 拍板：「精修不再必须，保留相关代码但不执行，只生成笔记」）。
    #    理由：`auto` 会把整节课**逐批串行**发给模型（548 句 / `POLISH_BATCH=30` = 19 批），
    #    收尾因此要跑十几分钟 —— 用户在这十几分钟里看不到笔记，而合盖/退出会让它整个不生成。
    #    代码与开关**都留着**：`--polish auto` 一个字就能开回来。
    p.add_argument("--polish", choices=["auto", "off"], default="off",
                   help="落笔前二次精修转录(需 API key; 默认 off = 直接用直播版, 笔记立刻生成); "
                        "auto 会逐批发给模型, 收尾要多跑十几分钟")
    p.add_argument("--polish-model", help="精修用的模型(默认同 --cloud-model)")
    p.add_argument("--vault", help="Obsidian 库路径(默认取 $OBSIDIAN_VAULT 或上次用过的; 都没有就不写 Obsidian)")
    args = p.parse_args()
    # ⭐⭐ 3.9.7 第 5a 项：**录课 + 收尾全程不许空闲睡眠**（`power.py` 文件头讲了为什么）。
    #    ⚠️ 持有范围是**整个 `main()`**（≈ 点「开始上课」到收尾结束），不是只有收尾那一段：
    #       · 30 个真课的现场记录里，缺音频的那一段正是**课上**（10-14 那位美国用户
    #         中间 44 分钟没声音 = 系统 idle sleep 把音频子系统停了），只罩收尾挡不住它；
    #       · `main()` 起的这个进程**就是**录课进程 —— 它一活，用户就是在上课，
    #         没有"进程活着但不上课"的窗口（面板是另一个短进程，`entry_launch.py`）。
    #    想收窄成"只罩收尾"：把 `with` 挪进 `run()` 的 `finally`（代价：那 400 行整体
    #    缩进 4 格）。**不改系统设置**、**合盖照样会睡** —— 见 `power.py`。
    with power.keep_system_awake():
        run(args)


if __name__ == "__main__":
    main()
