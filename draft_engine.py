#!/usr/bin/env python3
"""草稿引擎：选哪个模型出草稿、怎么喂、坏了怎么退。

## 契约（`main.partial_worker` 只依赖这一个方法）

    eng = load_draft_engine(parakeet_asr, window_samples=PARTIAL_MAX_S * SR)   # 永不抛
    text, used = eng.transcribe(buf, tag)     # used ∈ {"nemotron", "parakeet"}；永不抛
    tail = eng.finish(buf, tag)               # 段尾：本段完整假设；没有 → ""；永不抛
    ends = eng.sentence_ends(tag)             # 本段已封口的句末时刻（秒，相对段首）；没有 → ()；永不抛
    eng.name / eng.reason / eng.fallback_reason                               # 只读

⭐ **3.9.7 第 5c 项：模型在后台线程加载** —— `load_draft_engine_async()` 与上面
逐字同契约，只是把**贵的那个构造**（`NemotronDrafter(...)`，实测约 1.28 s）挪到了
后台：悬浮窗、采音、定稿都不等它。就绪前：

- `transcribe` / `finish` 返回空（**这一份不要草稿**），`sentence_ends` 返回 `()`
  —— `main.partial_worker` 那边 `if not t: continue`，屏上就是还没有草稿；
- `name` 先照**选中的那个**报（`nemotron`），加载失败才改口 `parakeet`
  （`fallback_reason` 写清原因，`partial_worker` 会照既有那条路出声一次）；
- **定稿一个字节都不受影响**：它根本不走这个引擎（`final_asr.load_final`）。

⚠️ 就绪前到达的音频**不缓存**（选简单的那条路）：草稿是 latest-wins 的，
缓存下来补喂要自己管过期、还会在段边界上和 `Segmenter` 的段号打架，而**丢掉几秒
草稿的代价远小于把定稿搞乱**。定稿照常，所以"丢"的只是那几秒的实时草稿字样。

- `buf` 是**本段从段首累积的**样本（`Segmenter._buf()`），`tag` 是 put 那一刻的段号。
  流式引擎每段一条流、只喂新增样本 —— latest-wins 丢掉中间几份也不丢样本，下一份 `buf` 里全有。
- Parakeet（窗口型）只看最近 `window_samples`，与 R3 之前的行为逐字相同。

⚠️ 线程：**只能在一个线程里用**（partial_worker）。sherpa 的 OnlineStream 非线程安全；
   本类不加锁 —— 加了也挡不住第二个线程造成的「流状态错乱」，所以干脆规定单线程。
   唯一例外是 `sentence_ends`：它只读草稿线程**整个替换**的一个元组（不碰 sherpa），
   给 `vad.Segmenter` 在音频线程里读（3.9.4 句末切段）。
⚠️ 熔断后**本节课不再试**（不来回抖）：退回 Parakeet，直到进程结束。
⚠️ 段中间**不补静音、不调 `input_finished`**：那会结束这条流，后面再喂的样本全丢。
   段**尾**才补（`finish`，3.9.4）：这条流本来就要扔了，补一截静音让前瞻里那几个词解出来。

为什么单独成模块：`partial_worker` 是 `main.run()` 里的嵌套闭包，默认闸门够不到；
选择、喂流、熔断、回退全放这里，用假识别器做毫秒级判据（`tests/test_draft_engine.py`）。
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any, Callable, Mapping

import models

ENV = "CLASSLIVE_DRAFT_ENGINE"                # auto | parakeet | nemotron（默认 auto）
ENV_MODEL_DIR = "CLASSLIVE_DRAFT_MODEL_DIR"   # 只给测试 / 排查用：指向别的模型目录
MODES = ("auto", "parakeet", "nemotron")
MIN_RAM_BYTES = 12 * 2**30    # auto 的内存门：模型常驻实测 +1073 MB（P0，OCW 全课）
SLOW_S, SLOW_STRIKES = 0.9, 3  # 连续 3 次单次更新 > 0.9 s 熔断（P0 实测 p95 0.15 s）
NUM_THREADS = 2
TAIL_PAD_S = 0.8               # 段尾补的静音（见 `NemotronDrafter.finish`）
SR = 16000
END_PUNCT = (".", "?", "!")


def sealed_ends(tokens, timestamps) -> tuple:
    """纯函数：**已封口**的句末标点时刻 —— 句末标点后面又开了一个新词（token 以空格开头）。

    ⚠️ 只认封口的：段尾那个还没下文的句号随时可能被改掉（流式假设会回改最后几个 token）。
    ⚠️ 时间戳是**模型发出该 token 的时刻**，比音频里那个停顿**晚**（10-12 真课 69 个句号：
       停顿中心中位在时间戳前 0.50 s）—— 用的人自己往前找，见 `vad.find_sentence_cut`。
    """
    toks, ts = list(tokens), list(timestamps)
    return tuple(float(ts[i]) for i in range(min(len(toks), len(ts)) - 1)
                 if toks[i].strip() in END_PUNCT and toks[i + 1].startswith(" "))


def choose(mode: str, model_present: bool, ram_bytes: int | None) -> tuple[str, str]:
    """纯函数：`(引擎名, 理由)`。理由空串 = 没什么要说的。

    - `mode` 非法 → 当 auto
    - parakeet → 手动指定
    - nemotron → 模型在就用（**不看内存**，用户明说要）；不在 → parakeet
    - auto → 模型不在 / 内存已知且不足 → parakeet；内存未知 → 放行并写明
    """
    mode = mode if mode in MODES else "auto"
    if mode == "parakeet":
        return "parakeet", "手动指定"
    if not model_present:
        return "parakeet", "流式模型未下载"
    if mode == "nemotron":
        return "nemotron", "手动指定"
    if ram_bytes is None:
        return "nemotron", "内存未知"
    if ram_bytes < MIN_RAM_BYTES:
        return "parakeet", f"内存 {ram_bytes / 2**30:.0f} GB < {MIN_RAM_BYTES // 2**30} GB"
    return "nemotron", ""


class NemotronDrafter:
    """一条 sherpa `OnlineRecognizer`，每段一条流。`update` 会抛（由 `DraftEngine` 兜）。"""

    def __init__(self, model_dir: str, num_threads: int = NUM_THREADS, *, rec=None):
        if rec is None:
            import sherpa_onnx

            from asr import _find_file

            def f(name: str) -> str:
                # 只给**精确名**；不许退到 `*.txt` 猜词表（`asr.ParakeetASR` 那段注释讲过为什么）
                return _find_file(model_dir, [name])

            rec = sherpa_onnx.OnlineRecognizer.from_transducer(
                tokens=f("tokens.txt"), encoder=f("encoder.int8.onnx"),
                decoder=f("decoder.int8.onnx"), joiner=f("joiner.int8.onnx"),
                num_threads=num_threads, sample_rate=SR, feature_dim=80,
                decoding_method="greedy_search",
                # ⚠️ 关掉 endpoint：分段归 `Segmenter` 管，两套分段同时开会互相打架
                enable_endpoint_detection=False, provider="cpu")
        self._rec = rec
        self._stream = None
        self._tag: object = object()
        self._fed = 0
        self._finished: object = object()     # 最近 `finish` 过的段号
        #: `(段号, 封口句末时刻…)` —— 草稿线程**整个替换**，音频线程只读（见模块头「线程」）
        self.ends: tuple = (None, ())

    def update(self, buf, tag) -> str:
        """喂到 `buf` 末尾，返回本段当前假设。已经 `finish` 过的段 → `""`（晚到的旧缓冲，丢掉）。"""
        if tag == self._finished:
            return ""
        if self._stream is None or tag != self._tag or len(buf) < self._fed:
            self._stream = self._rec.create_stream()
            self._tag, self._fed = tag, 0
        if len(buf) > self._fed:
            self._stream.accept_waveform(SR, buf[self._fed:])
            self._fed = len(buf)
        while self._rec.is_ready(self._stream):
            self._rec.decode_stream(self._stream)
        try:
            r = self._rec.get_result_all(self._stream)
            self.ends = (tag, sealed_ends(r.tokens, r.timestamps))
        except Exception:                     # noqa: BLE001 —— 拿不到时间戳只是不给提示，草稿照出
            self.ends = (tag, ())
        return self._rec.get_result(self._stream).strip()

    def finish(self, buf, tag) -> str:
        """段落结束：喂完剩下的样本 + 补 `TAIL_PAD_S` 静音 + `input_finished` → 本段**完整**假设。

        ⭐ 为什么（2026-10-12 SOC10020 真课回放，47 段对上 Groq 定稿）：不收尾时最后一条草稿
           比定稿平均少 3.6 个词（WER 26.6%）——模型的 560 ms 前瞻里那截从没被解码；
           收尾后少 2.2 个词（WER 20.3%），代价 p50 211 ms / p95 369 ms（草稿线程上）。
           补 0.5 / 0.8 / 1.2 s 差别在 0.3 个百分点内 → 取 0.8。
        ⚠️ 只在段尾调：`input_finished` 之后这条流**不能再喂**（模块头那条）；之后同一 `tag`
           再来 `update` 会因为 `_stream` 被清空而新开一条流（不会往死流里喂）。
        """
        if self._stream is None or tag != self._tag or len(buf) < self._fed:
            # 没跑过草稿 → 不额外花一整段的解码。硬切挪回了一截（模型已经听过切点后面）→ 不补尾，
            # 那会把下一段的词也带进来；也**不截到句号**（3.9.4 试过）：草稿槽只有一格，截掉的那几个词
            # 要等下一段第一份草稿（实测 ~2 s）才回来 —— 屏上会先消失再出现，不如原样挂着等下一段接上。
            return ""
        if len(buf) > self._fed:
            self._stream.accept_waveform(SR, buf[self._fed:])
        import numpy as np
        self._stream.accept_waveform(SR, np.zeros(int(TAIL_PAD_S * SR), dtype=np.float32))
        self._stream.input_finished()
        while self._rec.is_ready(self._stream):
            self._rec.decode_stream(self._stream)
        text = self._rec.get_result(self._stream).strip()
        self._stream, self._finished = None, tag
        return text

    def reset(self) -> None:
        """丢掉当前这条流（3.9.7 省电：窗口恢复可见时「干净重开」）。

        ⚠️ 下一次 `update` 会因 `_stream is None` **按现行 `buf` 从头开一条新流** ——
           配合 `main.partial_worker` 的「跳过进行中那一段」，真正的效果是从**下一个段**
           重新开始。**永不抛。**
        """
        self._stream = None
        self._tag = object()
        self._fed = 0
        self.ends = (None, ())


class _Pending:
    """后台线程里正在构造的流式模型（3.9.7 第 5c 项）。

    `choose` / `doctor.model_present` 是**同步**做完的（都便宜），贵的那一步
    （`NemotronDrafter(...)`，实测约 1.28 s）丢这里。就绪前 `DraftEngine` 一律
    不碰模型、返回空。

    ⚠️ **只被草稿线程读**（`poll` 从 `transcribe` / `finish` 里调）—— 与
       `DraftEngine` 那条"单线程使用"的纪律同源。加载线程只写自己的两个字段。
    ⚠️ 加载失败**不许抛到线程外**（daemon 线程里的异常没人收），改成 `_error` 让
       草稿线程下次 `poll` 时接手、退到 Parakeet。
    """

    def __init__(self, build: Callable[[], Any],
                 thread_factory: Callable[..., Any] = threading.Thread):
        self._lock = threading.Lock()
        self._drafter: Any = None
        self._error: BaseException | None = None
        self._done = threading.Event()
        self.thread = thread_factory(target=self._run, args=(build,), daemon=True,
                                    name="cl-draft-load")
        self.thread.start()

    def _run(self, build) -> None:
        try:
            drafter = build()
        except BaseException as e:                    # noqa: BLE001 —— 见类头第二条
            with self._lock:
                self._error = e
        else:
            with self._lock:
                self._drafter = drafter
        finally:
            self._done.set()

    @property
    def done(self) -> bool:
        return self._done.is_set()

    @property
    def error(self) -> BaseException | None:
        with self._lock:
            return self._error

    def poll(self) -> tuple[Any, bool]:
        """非阻塞：`(drafter, 已失败?)`。还在加载 → `(None, False)`。"""
        if not self._done.is_set():
            return None, False
        with self._lock:
            return self._drafter, self._error is not None


class DraftEngine:
    def __init__(self, parakeet, streaming=None, *, window_samples: int, reason: str = "",
                 clock: Callable[[], float] = time.monotonic, pending=None):
        self._parakeet = parakeet
        self._streaming = streaming
        #: 后台加载中的流式模型（`load_draft_engine_async` 才会给）；就绪前一律不出草稿。
        self._pending = pending
        self._window = int(window_samples)
        self._clock = clock
        self._strikes = 0
        self.reason = reason
        self.fallback_reason = ""

    @property
    def ready(self) -> bool:
        """**后台那一份尘埃落定**了没有（成功或失败都算落定）。⚠️ 选的是 Parakeet 时恒为真。"""
        p = self._pending
        return p is None or p.done

    def _adopt(self) -> None:
        """把后台加载的结果接过来。⚠️ 只在草稿线程调（它会改 `_streaming`）。

        失败 → 改口 Parakeet（`fallback_reason` 说清），**这一份**就从 Parakeet 出
        —— 同"流式坏了退 Parakeet"那条既有路径，只不过原因是加载。
        """
        p = self._pending
        if p is None:
            return
        drafter, _failed = p.poll()
        if not p.done:
            return
        self._pending = None
        if drafter is not None:
            self._streaming = drafter
        else:
            self.fallback_reason = f"出错：加载失败（{p.error!r}）"

    @property
    def name(self) -> str:
        """当前生效（或**将要**生效）的引擎。⚠️ 与 `ready` 不同：加载中先报**选中的**
        那个（`nemotron`），只有**加载失败**才改口 `parakeet`。"""
        if self._streaming is not None:
            return "nemotron"
        if self._pending is not None:
            return "parakeet" if self._pending.error is not None else "nemotron"
        return "parakeet"

    def transcribe(self, buf, tag) -> tuple[str, str]:
        if self._pending is not None:
            self._adopt()
            if self._pending is not None:         # 还在后台加载 → 这一份不要草稿（定稿照常）
                return "", "nemotron"
        if self._streaming is not None:
            try:
                t0 = self._clock()
                text = self._streaming.update(buf, tag)
                self._strikes = self._strikes + 1 if self._clock() - t0 > SLOW_S else 0
                if self._strikes >= SLOW_STRIKES:
                    self._trip(f"太慢：连续 {SLOW_STRIKES} 次 > {SLOW_S}s")
                return text, "nemotron"          # 这一份照常用（已经算出来了）
            except Exception as e:               # noqa: BLE001
                self._trip(f"出错：{e!r}")        # 落到下面的 Parakeet，**这一份不丢**
        return self._parakeet.transcribe(buf[-self._window:]), "parakeet"

    def finish(self, buf, tag) -> str:
        """段尾收一次（见 `NemotronDrafter.finish`）。Parakeet / 熔断后 / 出错 → `""`（= 不补，旧行为）。**永不抛。**

        ⚠️ 不计入「太慢」熔断：这一下本来就要多解 0.8 s 静音，与逐秒草稿不是一个量级。
        ⚠️ 后台还在加载 → `""`（没跑过这一段的草稿，本来也不该收尾）。
        """
        if self._pending is not None:
            self._adopt()
            if self._pending is not None:
                return ""
        if self._streaming is None:
            return ""
        try:
            return self._streaming.finish(buf, tag)
        except Exception as e:               # noqa: BLE001
            self._trip(f"出错：{e!r}")
            return ""

    def sentence_ends(self, tag) -> tuple:
        """本段（`tag`）最近一次草稿里已封口的句末时刻。Parakeet / 熔断后 / 不是这一段 → `()`。**永不抛**。"""
        try:
            got_tag, ends = self._streaming.ends if self._streaming is not None else (None, ())
            return ends if got_tag == tag else ()
        except Exception:                     # noqa: BLE001
            return ()

    def _trip(self, why: str) -> None:
        self._streaming = None
        self.fallback_reason = why

    def reset(self) -> None:
        """丢掉进行中那一段的流状态（见 `NemotronDrafter.reset`）。

        窗口重新可见时用（3.9.7 省电）：「草稿流干净重开，从下一个段开始」——
        隐藏期间没喂过音频，流停在旧位置上，直接接着喂就是**喂半截**。
        Parakeet（窗口型、每次重解整窗）/ 后台还在加载 → 空操作。**永不抛。**
        """
        try:
            r = getattr(self._streaming, "reset", None)
            if callable(r):
                r()
        except Exception:                            # noqa: BLE001
            pass


def _ram_bytes() -> int | None:
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return None


def _mode(env: Mapping[str, str]) -> str:
    return (env.get(ENV) or "auto").strip().lower()


def want_model(env: Mapping[str, str] = os.environ,
               ram_bytes: Callable[[], int | None] | None = None) -> bool:
    """这台机器装了 Nemotron 会不会用上？给更新卡片问「要不要提议下载」。

    与 `load_draft_engine` 走**同一个** `choose`（假设模型已在）—— 内存不够 / 手动指定 parakeet
    的机器装了也用不上，就别让它下 464 MB。**永不抛**（出错当「不要」）。
    """
    try:
        return choose(_mode(env), True, (ram_bytes or _ram_bytes)())[0] == "nemotron"
    except Exception:                            # noqa: BLE001
        return False


def _prepare(env: Mapping[str, str],
             ram_bytes: Callable[[], int | None] | None) -> tuple[str, str, str | None]:
    """`(引擎名, 理由, 模型目录)`。`引擎名 == "parakeet"` 时目录为 `None`。**永不抛**。

    把「选哪个」与「真的去构造」拆开 —— 前者便宜（同步做完），后者贵（见
    `load_draft_engine_async`）。两条加载路径共用这一份判据。
    「模型在不在」用 `doctor.model_present`（全仓唯一判据，目录非空）。
    """
    try:
        import doctor
        model_dir = env.get(ENV_MODEL_DIR) or models.path_of("nemotron")
        ram = (ram_bytes or _ram_bytes)()
        name, why = choose(_mode(env), bool(model_dir) and doctor.model_present(model_dir), ram)
        return name, why, model_dir
    except Exception as e:                       # noqa: BLE001
        return "parakeet", f"流式模型加载失败：{e!r}", None


def load_draft_engine(parakeet, *, window_samples: int, env: Mapping[str, str] = os.environ,
                      ram_bytes: Callable[[], int | None] | None = None,
                      make_drafter: Callable[[str], Any] = NemotronDrafter) -> DraftEngine:
    """**永不抛**。任何一步失败 → Parakeet 引擎，`reason` 写清楚。**同步**加载（约 1.28 s）。

    ⚠️ 生产走 `load_draft_engine_async`（不卡启动）；这个同步版留给离线量具
    （`scripts/stream_eval.py` 要拿真模型量 WER，那本来就没有"启动"要救）。
    """
    name, why, model_dir = _prepare(env, ram_bytes)
    if name == "parakeet":
        return DraftEngine(parakeet, window_samples=window_samples, reason=why)
    try:
        return DraftEngine(parakeet, make_drafter(model_dir),
                           window_samples=window_samples, reason=why)
    except Exception as e:                       # noqa: BLE001
        return DraftEngine(parakeet, window_samples=window_samples,
                           reason=f"流式模型加载失败：{e!r}")


def load_draft_engine_async(parakeet, *, window_samples: int,
                            env: Mapping[str, str] = os.environ,
                            ram_bytes: Callable[[], int | None] | None = None,
                            make_drafter: Callable[[str], Any] = NemotronDrafter,
                            thread_factory: Callable[..., Any] = threading.Thread,
                            on_ready: Callable[[bool], None] | None = None) -> DraftEngine:
    """**永不抛**，且**立刻返回** —— 模型的构造丢到后台线程（3.9.7 第 5c 项）。

    「选哪个」同步做完（`_prepare`，便宜）；选中的是流式就在后台建
    （实测约 1.28 s，原来卡在主线程上：悬浮窗、采音、定稿都得等它）。

    `on_ready(ok)` 在后台**尘埃落定**时被调一次（成功 `True` / 失败 `False`），
    用来记「草稿模型就绪」那一项起步计时。⚠️ 它跑在**加载线程**上 —— 只许做
    「记一个时刻」这种立刻返回的事。⚠️ 选中 Parakeet（没有后台那一段）时**不调**：
    那条路的就绪时刻是调用方等 `asr.get()` 的那一下，由它自己记。
    """
    name, why, model_dir = _prepare(env, ram_bytes)
    if name == "parakeet":
        return DraftEngine(parakeet, window_samples=window_samples, reason=why)

    def _build():
        try:
            drafter = make_drafter(model_dir)
        except BaseException:                    # noqa: BLE001 —— 先告诉 on_ready，再让 _Pending 收下
            if on_ready is not None:
                _notify(on_ready, False)
            raise
        if on_ready is not None:
            _notify(on_ready, True)
        return drafter

    return DraftEngine(parakeet, window_samples=window_samples, reason=why,
                       pending=_Pending(_build, thread_factory=thread_factory))



def _notify(on_ready, ok: bool) -> None:
    """`on_ready` 不许把加载线程带下水（回调自己坏了也照旧加载）。"""
    try:
        on_ready(ok)
    except Exception:                            # noqa: BLE001
        pass

