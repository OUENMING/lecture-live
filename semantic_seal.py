#!/usr/bin/env python3
"""语意封口：Soniox 不给句号时，按「一个意思讲完了」把未封口的文字送出去。

## 为什么有它

流式识别的封口只认句末标点（`stream_asr` 的 P_stable / `_seal_final_ends`）。老师连着讲、Soniox
的标点崩掉（10-28 CHE002A：开课 ~50 s 后整节课没有逗号句号）→ 字幕堆几分钟不动，然后一次吐一大批；
翻译、笔记、章节时间戳全跟着晚（最长 252 s）。

## 它做什么（和不做什么）

- 只在「**一大段没有任何标点**」时介入（`GATE_WORDS`）；有标点就让句号路径自己封。
- 让一个文本模型（`decide`）从最近 ≤50 个词里指出「哪些词是一句话的最后一个词」，再按本文件的
  规则挑一个：留 `LOOKAHEAD` 个词的余量、不停在虚词上、`so` 归下一句。
- 封口走 `StreamASR.seal_through_word` —— 就是句号封口那条 `_seal`，**水位线去重原样生效**：
  之后 Soniox 把这段变 final 或补上句号，都不会再封一遍。
- 模型不可用 / 没挑出来：词数到 `CAP_WORDS` 就在最近一个不停在虚词上的位置硬切（最后手段，不是主路径）。

## 为什么不是别的

- 不按秒数：同行（Soniox / AssemblyAI）都是「语意优先、时限兜底」；
- 不看 VAD 停顿：10-28 实测 VAD 句界和等数量随机候选几乎没差别（`docs/PLAN-semantic-seal-20261029.md`）；
- 不上本地句界模型：wtpsplit SaT 的 ONNX 要 400 MB，对一个兜底功能太重；LLM 路径零新依赖，
  只在崩塌时才调（约每 2 s 一次）。

## 接缝

`SemanticSealer(stream, decide)`：`stream` 要有 `unsealed_words()` / `seal_through_word(k, expect)`
（`StreamASR` 和测试里的假流都满足）；`decide(words) -> list[int]` 返回「句末词」的下标。
`step()` 做一轮决策、同步、可单测；`start()` / `stop()` 起一条守护线程反复调它。
"""
from __future__ import annotations

import re
import threading
import time
from typing import Callable, Protocol, Sequence

# ---- 可调参数（数据依据：2026-10-29 离线评测，见 docs/PLAN-semantic-seal-20261029.md）

#: 未封口词数达到它、且其中没有任何标点才介入。健康课里只有 ≤1.2% 的句子会撞上它（ECS170 498 句、
#: 10-26 89 句，25 词内无标点的句子占比）。
GATE_WORDS = 25
#: 刚语意封过口（`LATCH_S` 内）→ 说明正处在崩塌里，门槛降到它：不然每段都要再等 25 词。
LATCH_GATE_WORDS = 10
LATCH_S = 30.0
#: 封口位置之后至少还留这么多词：句子末尾的几个词在 non-final 里最容易被改。
LOOKAHEAD = 3
#: 一句至少这么多词（再短的碎句翻译没意义）。
MIN_WORDS = 5
#: 最后手段：未封口词数到这儿还没等到模型给出合适的位置，就硬切。≈ 健康课句长 p99（30–39 词）。
CAP_WORDS = 40
#: 有标点也介入的词数（逗号一路写下去、句号迟迟不来）。
HARD_WORDS = 60
#: 两次决策最小间隔（秒）。模型一次约 1 s。
STEP_S = 2.0
#: 喂给模型的最近词数。
WINDOW_WORDS = 50

_PUNCT = re.compile(r"[,.;:?!]")
#: 不该是一句话最后一个词的词（句子停在这儿几乎一定是切错了）。`so` / `okay` / `right` 不在里面：
#: `so` 另有规则（归下一句），`okay` / `right` 常是句尾口头语。
NOT_END = frozenset("""the a an of to in on at for with and or but that which is are was were be by from as than
this these those its their our your my his her if when because while what how who where between into about
we you it i they not can will would could should have has do does""".split())


class _Stream(Protocol):
    def unsealed_words(self) -> list[str]: ...
    def seal_through_word(self, k: int, expect: Sequence[str] | None = None) -> bool: ...


def choose_cut(words: Sequence[str], ends: Sequence[int], *,
               lookahead: int = LOOKAHEAD, min_words: int = MIN_WORDS) -> int | None:
    """从模型给的「句末词下标」里挑一个封口位置（`words` 里的下标），挑不出返回 `None`。

    纯函数。规则：只看 `[min_words-1, len-1-lookahead]` 内的；取最靠后的；`so` 归下一句（往前挪一个词）；
    停在 `NOT_END` 词上的这个候选作废，换更靠前的一个。
    """
    hi = len(words) - 1 - lookahead
    cands = sorted({int(i) for i in ends if isinstance(i, int) and min_words - 1 <= i <= hi}, reverse=True)
    for i in cands:
        if words[i].lower() == "so" and i - 1 >= min_words - 1:
            i -= 1
        if words[i].lower() in NOT_END:
            continue
        return i
    return None


def cap_cut(words: Sequence[str], *, lookahead: int = LOOKAHEAD, min_words: int = MIN_WORDS) -> int | None:
    """最后手段的切点：从 `len-1-lookahead` 往前找第一个不是 `NOT_END` 的词（最多退 8 个词）。"""
    i = len(words) - 1 - lookahead
    for j in range(i, max(min_words - 2, i - 9), -1):
        if words[j].lower() not in NOT_END:
            return j
    return i if i >= min_words - 1 else None


class SemanticSealer:
    """反复看 `stream` 的未封口文字，该封就封。接缝见文件头。"""

    def __init__(self, stream: _Stream, decide: Callable[[list[str]], list[int]], *,
                 clock: Callable[[], float] = time.monotonic,
                 on_seal: Callable[[dict], None] | None = None,
                 gate_words: int = GATE_WORDS, latch_gate_words: int = LATCH_GATE_WORDS,
                 cap_words: int = CAP_WORDS) -> None:
        self._stream = stream
        self._decide = decide
        self._clock = clock
        self._on_seal = on_seal
        self._gate = int(gate_words)
        self._latch_gate = int(latch_gate_words)
        self._cap = int(cap_words)
        self._last_call = -1e9
        self._last_sem = -1e9
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.stats = {"calls": 0, "errors": 0, "semantic": 0, "cap": 0, "no_cut": 0, "stale": 0}

    # ---- 一轮决策（同步，可单测）

    def step(self) -> bool:
        """做一轮：该介入就问模型、挑位置、封口。封成了返回 `True`。永不抛。"""
        now = self._clock()
        if now - self._last_call < STEP_S:
            return False
        alive = getattr(self._stream, "alive", None)
        if callable(alive) and not alive():
            return False                            # 流已退回本地 / 重连中：半句归 flush 管，别在这时候封
        words = self._stream.unsealed_words()
        n = len(words)
        latched = now - self._last_sem < LATCH_S
        if n < (self._latch_gate if latched else self._gate):
            return False
        if n < HARD_WORDS and _PUNCT.search(" ".join(words)):
            return False                            # 有标点：句号路径会封，别抢
        self._last_call = now
        off = max(0, n - WINDOW_WORDS)
        tail = words[off:]
        try:
            ends = self._decide(tail)
            self.stats["calls"] += 1
        except Exception:                           # noqa: BLE001
            self.stats["errors"] += 1
            ends = []
        cut = choose_cut(tail, ends)
        kind = "semantic"
        if cut is None:
            self.stats["no_cut"] += 1
            if n < self._cap:
                return False
            cut, kind = cap_cut(tail), "cap"
            if cut is None:
                return False
        k = off + cut
        if self._stream.seal_through_word(k, words[:k + 1]):
            self.stats[kind] += 1
            if kind == "semantic":
                self._last_sem = self._clock()
            if self._on_seal is not None:
                try:
                    self._on_seal({"kind": kind, "words": k + 1, "unsealed": n,
                                   "text": " ".join(words[:k + 1])})
                except Exception:                   # noqa: BLE001
                    pass
            return True
        self.stats["stale"] += 1                    # 决策期间这段文字变了 / 已被句号封走：下一轮重来
        return False

    # ---- 线程

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="semantic-seal", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t is not None:
            t.join(timeout=3.0)

    def _run(self) -> None:
        while not self._stop.wait(0.5):
            try:
                self.step()
            except Exception:                       # noqa: BLE001
                self.stats["errors"] += 1


# ---- 决策适配器：文本模型

_SYSTEM = ("You get the tail of a live university lecture transcript: lowercase, unpunctuated words, numbered "
           "from 0. Find where a complete sentence or self-contained thought ends. Reply JSON only: "
           "{\"ends\":[i,...]} with the index of the LAST word of each complete sentence. Never end in the "
           "middle of a noun phrase or after a function word.")


def llm_decide(key: str, model: str, *, timeout: float = 15.0) -> Callable[[list[str]], list[int]]:
    """用 `llm_client.chat_json` 做 `decide`。提示词与 2026-10-29 离线评测（F1 0.65 / 0.49）同一份。"""
    import llm_client

    def decide(words: list[str]) -> list[int]:
        user = " ".join(f"{i}:{w}" for i, w in enumerate(words))
        r = llm_client.chat_json(key, model, _SYSTEM, user, max_tokens=100, temperature=0.0,
                                 timeout=timeout, deadline=timeout + 5.0)
        ends = r.get("ends", []) if isinstance(r, dict) else []
        return [int(i) for i in ends if isinstance(i, int) and not isinstance(i, bool)]

    return decide
