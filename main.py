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
from asr import load_asr
from translator import load_translator
from cloud_translator import load_api_key, load_translator as load_cloud_translator
from obsidian_writer import ObsidianWriter, DEFAULT_VAULT
from build_notes import (TermNotes, format_gloss, detect_proper_nouns, lookup_term)

PARTIAL_MAX_S = 10          # 草稿只转写最近 N 秒, 限制单次耗时

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


def _ask_save_notes(n: int) -> bool:
    """结束时问是否存入 Obsidian。
    ⚠️ 这里按 Ctrl+C / 非交互环境一律**默认保存** —— 会话文件已实时落盘,
    这一步只决定要不要复制进 Obsidian 库, 绝不能因为一次误按丢掉整节课。"""
    try:
        ans = input(f"\n📝 本次共记录 {n} 句双语。存入 Obsidian 吗? [Y/n] ").strip().lower()
    except KeyboardInterrupt:
        echo("\n(Ctrl+C → 默认存入 Obsidian, 避免误丢)")
        return True
    except EOFError:
        echo("\n(非交互环境 → 默认存入 Obsidian)")
        return True
    return ans in ("", "y", "yes", "是", "好", "存")


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

    def warmup(self) -> None:
        if self._mode == "local":
            self._local.warmup()
        elif self._mode == "cloud":
            pass                                  # 云端无需预热
        # auto: 不预热本地, 省内存/启动时间; 真降级时再懒加载

    def _fallback(self, e: Exception) -> None:
        if self._use_cloud:
            self._use_cloud = False
            self._notify(
                f"云端翻译失败({str(e)[:60]}); 已降级本地引擎, "
                f"每 {self.RETRY_PROBE_S:.0f}s 自动重试", warn=True)
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


class TerminalUI:
    def __init__(self):
        self._zh = ""
        self._en = ""

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

    def notice(self, msg: str, warn: bool = False):
        echo(("⚠ " if warn else "✅ ") + msg)

    def pump(self):
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


def run(args) -> None:
    # 音源最先打开: 失败(没麦/没 BlackHole/坏文件)在这里就给友好提示退出,
    # 不再走完 ASR/LLM 加载 + 悬浮窗后才崩出一个裸 traceback。
    try:
        src = load_source(args.source, args.path, args.speed)
    except Exception as e:                       # noqa: BLE001
        echo(f"⚠ 无法打开音源({args.source}): {e}")
        return

    asr = load_asr(args.model_dir)
    local_tr = load_translator(args.llm, args.glossary, args.context,
                               course=args.course)
    cloud_tr = None
    api_key_val = load_api_key(args.api_key)
    if args.engine in ("auto", "cloud"):
        if api_key_val:
            cloud_tr = load_cloud_translator(api_key_val, args.cloud_model,
                                             args.glossary, args.context,
                                             course=args.course)
            echo(f"☁ 引擎: {args.engine} (云端 {args.cloud_model})")
        else:
            echo("⚠ 未找到 DeepSeek API key(--api-key / DEEPSEEK_API_KEY / .deepseek_key); "
                 "回退本地引擎")
    else:
        echo(f"💻 引擎: local ({args.llm})")

    running = threading.Event()
    running.set()
    flagged = {"on": False}                             # ⭐ 标记当前句
    translating = {"on": True}                          # 🌐 翻译开关(悬浮窗按钮可随时切)
    writer = ObsidianWriter(args.vault, args.course, mode=args.save_notes,
                            api_key=api_key_val, model=args.cloud_model,
                            glossary_path=args.glossary,
                            polish=args.polish != "off",
                            polish_model=args.polish_model or None)
    notes = TermNotes()                                 # 术语通俗解析(查表)

    drafts: "queue.Queue[str]" = queue.Queue()          # 草稿文本 -> 主线程
    drafts_zh: "queue.Queue[str]" = queue.Queue()       # 草稿译文 -> 主线程
    streamq: "queue.Queue[tuple]" = queue.Queue()       # ("zh"/"en"/"final"/"terms"/"notice") -> 主线程
    draftq: "queue.Queue" = queue.Queue(maxsize=1)      # 待译草稿(只保留最新)
    properq: "queue.Queue" = queue.Queue()              # 待查专有名词

    def notify(msg: str, warn: bool = False) -> None:
        """引擎状态广播 -> streamq -> 主线程 drain() -> UI(悬浮窗要求主线程)。"""
        streamq.put(("notice", (msg, warn)))

    translator = EngineRouter(local_tr, cloud_tr, args.engine, notify=notify)
    translator.warmup()

    def submit_question(q: str) -> None:
        """悬浮窗输入框回车回调(Phase 1: 只做可观测回显; 问答引擎在 Phase 2)。

        ⚠️ 在主线程被 AppKit 调用 —— 这里只准做立刻返回的事(不阻塞不变量)。
        """
        echo(f"[{now()}] 🙋 {q}")

    ui = TerminalUI() if args.ui == "terminal" else _load_overlay(
        on_quit=lambda: running.clear(),
        on_flag=lambda: flagged.__setitem__("on", True),
        on_translate=lambda on: translating.__setitem__("on", on),
        on_submit=submit_question,
    )

    seen_proper: collections.Counter = collections.Counter()   # 出现次数(≥2 才查)
    queried_proper: set = set()                         # 已发起过查询的(防重复请求)
    finals: list[str] = []                              # 已定稿(修正后), 作 LLM 上下文
    latest = _Latest()
    finalq: "queue.Queue" = queue.Queue()
    _QUIT = object()
    busy = {"on": False}          # 定稿线程正在处理一句(ASR+LLM), 冲刷等待要等它归零

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
                if translating["on"] and now - last_tr >= 2.5 \
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
            if not translating["on"]:
                # 关的只是**中文**, 不是模型: 英文仍做上下文矫正(ASR 错听照修),
                # 只是不产出译文、也不查术语(术语解析本身是中文, 与"不要中文"冲突)。
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
            if not hits and api_key_val:
                for pn in detect_proper_nouns(sent, notes.known()):
                    seen_proper[pn] += 1
                    if seen_proper[pn] == 2:
                        properq.put(pn)

    def _force_emit_carry() -> None:
        if not carry["text"]:
            return
        t = carry["text"]
        carry["text"], carry["since"] = "", 0.0
        # busy 必须在清空 carry 之前、LLM 调用之前置位: 收尾判据靠"carry 空 + busy 空"
        # 两个条件接棒 —— 缺了这里, 强制送出的 LLM 输出落地前判据就会假性满足,
        # 冲刷循环提前 break 把这句丢了(实测复现)。
        busy["on"] = True
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
                if dtext and translating["on"]:
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
                text = asr.transcribe(buf)
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
                carry["text"], carry["since"] = "", 0.0
                # busy 覆盖 ASR + _emit 全程: 冲刷等待循环若只看队列, 会在
                # "buf 已出队、结果还没进 streamq"的窗口里误判空闲(丢这句)。
                busy["on"] = True
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
            queried_proper.add(term)
            res = lookup_term(term, api_key_val, args.cloud_model)
            if res:
                notes.add(term, res["note"], res["type"])   # 写 auto 文件, 下次查表命中
                streamq.put(("terms", [(term, res["note"])]))

    t1 = threading.Thread(target=partial_worker, daemon=True)
    t2 = threading.Thread(target=final_worker, daemon=True)
    t3 = threading.Thread(target=proper_worker, daemon=True)
    t1.start(); t2.start()
    if api_key_val:
        t3.start()

    echo(f"▶ 开始: source={args.source}" + (f" path={args.path}" if args.path else ""))
    src = load_source(args.source, args.path, args.speed)

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
            elif item[0] == "final":                # ("final", en, zh, asr_raw)
                ui.finalize(item[1] or item[3], item[2])   # 翻译失败时至少显示转录
                writer.append(item[1], item[2], flagged=flagged["on"], raw=item[3])
                flagged["on"] = False

    try:
        while running.is_set():
            chunk = src.poll()
            if chunk is not None and len(chunk):
                seg.accept(chunk)
            drain()
            if src.is_done():
                break
            if args.ui == "overlay":
                ui.pump()
            else:
                time.sleep(0.005)
    except KeyboardInterrupt:
        echo("\n⏹ 手动停止")
    finally:
        # 先撤悬浮窗: 后面可能等冲刷 + 阻塞问"是否存 Obsidian", 窗口留着不动
        # 就是"点了 ✕ / 按了 Ctrl+C 就卡死"。TerminalUI 没有 close, 故用 getattr。
        # (✕ 按钮已经在自己的回调里关过一次, close() 幂等。)
        close_ui = getattr(ui, "close", None)
        if callable(close_ui):
            close_ui()
        src.close()
        # ---- 统一冲刷: 文件播完 / ✕ / Ctrl+C 走同一条路 ----
        # 旧代码只在"文件播完"才冲刷, 手动停止会把分段器里在途的整句话
        # (最多 12s 音频)直接丢掉 —— 实测 SIGINT 复现; 而冲刷等待只看队列,
        # carry 半句与强制送出的 LLM 输出都在队列之外, 竞态下文件尾也会丢句
        # (57.5s 切点实测复现)。现在: 冲刷分段器 + 等定稿线程真正空闲。
        # ⚠️ running 在等待期间必须保持 set, 否则定稿线程直接退出不干活。
        # 外层 try/finally 保证用户在等待中再按一次 Ctrl+C 也会走到 writer.close。
        try:
            seg.flush()
            finalq.put(_QUIT)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                drain()
                if all_settled(finalq, streamq, drafts,
                               busy["on"], carry["text"]):
                    time.sleep(0.25)
                    if all_settled(finalq, streamq, drafts,
                                   busy["on"], carry["text"]):
                        break
                time.sleep(0.02)
            drain()
        finally:
            running.clear()
            msg = writer.close(ask=_ask_save_notes)
            if msg:
                echo(msg)


def _load_overlay(on_quit=None, on_flag=None, on_translate=None, on_submit=None):
    try:
        from overlay import Overlay
        o = Overlay(on_quit=on_quit, on_flag=on_flag, on_translate=on_translate,
                    on_submit=on_submit)
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
    p.add_argument("--model-dir", default=os.path.expanduser("~/models/parakeet-tdt-0.6b-v3-int8"),
                   help="Parakeet 模型目录")
    p.add_argument("--llm", default="mlx-community/Qwen3-1.7B-4bit")
    p.add_argument("--glossary", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "glossary.txt"))
    p.add_argument("--context", type=int, default=5,
                   help="送翻译的最近上下文句数(默认 5; 远场听错多, 上下文越长越好修)")
    p.add_argument("--engine", choices=["auto", "cloud", "local"], default="auto",
                   help="翻译引擎: auto(云端优先,失败降级)/cloud/local")
    p.add_argument("--cloud-model", default="deepseek-flash",
                   help="云端模型名")
    p.add_argument("--api-key", help="DeepSeek API key(默认读 DEEPSEEK_API_KEY 或 .deepseek_key)")
    p.add_argument("--course", help="课程代码(如 ECON10101); 不设也能写笔记(课名默认 LECTURE)")
    p.add_argument("--save-notes", choices=["ask", "yes", "no"], default="ask",
                   help="笔记保存策略: ask(默认,结束时问)/ yes(直接存)/ no(不存)")
    p.add_argument("--polish", choices=["auto", "off"], default="auto",
                   help="落笔前二次精修转录(需 API key; 默认 auto); off 直接用直播版")
    p.add_argument("--polish-model", help="精修用的模型(默认同 --cloud-model)")
    p.add_argument("--vault", default=DEFAULT_VAULT, help="Obsidian 库路径")
    args = p.parse_args()
    run(args)


if __name__ == "__main__":
    main()
