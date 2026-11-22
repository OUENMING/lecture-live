"""桌面 `stream_asr.py` 的纯逻辑切片，**由 `web/scripts/sync-core.py` 按名字生成，勿手改**。

从源文件里只剥掉了「包在切片外面」的东西，切片内部的代码一个字没动（`--check` 逐字节钉着）：

- `import numpy as np` —— 只被 `_pcts`（封口延迟量具）用，量具不在切片内。
- `import os` / `import urllib.parse` —— 只被 `endpoint()` / `wanted()` 用（读环境变量，浏览器没有）。
- `import queue` / `import threading` / `import random` —— 收线程、重连退避、keepalive
  都在第二层（传输），切片里没有。
- `import asr_endpoint` / `import final_asr` —— 领 key 与云端定稿模块，纯网络依赖。
- 整个「第二层：传输」：`StreamASR`（线程 + websockets + 计时器）、`_KeyClient`（httpx）、
  `side_probe`（socket）、`metrics` / `no_token_stalled` / `seal_latency_s`（量具，numpy）。
- `wanted()` / `notice_for()` / `endpoint()`：读环境变量 + 拼 URL，浏览器里没有意义。

保留下来的 import 是脚本从桌面源里**按切片实际用到的名字**挑的原文（`json` / `re` / `time` /
`collections` / `typing` 这一类），桌面新增依赖时不用手改这里。
"""
from __future__ import annotations

import collections
import json
import re
import time
from typing import Callable, Iterable, NamedTuple, Sequence

STABLE_S = 0.3

#: terms + general + text **合起来**的字符预算；留一成余量吃序列化骨架与估算偏差。
#: ⚠️ 从前是 30 000（≈ 官方口径的 3 倍）—— 词表一大就会静默踩线，这是那次收口的原因。
CONTEXT_CHAR_BUDGET = 9000
#: 单条术语字符上限 —— 超了整条跳过（手抄的长句不是术语）。
TERM_MAX_CHARS = 200
#: `general` 的键值对数上限（官方 Tips：10 个以内）与单个值字符上限。
GENERAL_KEYS_MAX = 10
GENERAL_VALUE_MAX = 200
#: `context.text`（课件大纲摘要）字符上限 —— 官方：text 影响力弱于 general / terms。
CONTEXT_TEXT_MAX = 1500
#: **平时**进 `context.terms` 的条数目标（可配置：`StreamASR(terms_max=…)`）。
#: ⚠️ 上限（`CONTEXT_CHAR_BUDGET`）只兜底；官方没说 context 大小对延迟的影响，
#: 所以平时就按这个**小目标**走 —— 先小后大，用真课数据说话（#3 的 terms_n 与封口延迟）。
TERMS_MAX = 100
#: 序列化骨架（键名、引号、逗号、括号）的保守余量 —— `build_context` 记账用。
_CONTEXT_SERIAL_MARGIN = 64

#: ⭐ S2：换人就断句。只看 **final** token（Soniox 实时会临时换人随后稳定；non-final 里
#: 的临时换人不许切句）。关掉 = 与加说话人之前逐字相同的切句行为。
SPLIT_ON_SPEAKER = True

#: 句末标点（与 `draft_engine.END_PUNCT` 同口径）。
END_PUNCT = (".", "?", "!", "。", "？", "！")
#: 控制 token：不是词，别进文本。
CONTROL = ("<end>", "<fin>")

def _is_end(tok: str) -> bool:
    return tok.strip().endswith(END_PUNCT)


# ══════════════════════════════════════════════════════════════════════
# 第一层：纯逻辑核心
# ══════════════════════════════════════════════════════════════════════

class Tok(NamedTuple):
    """一个 Soniox token。时间一律**毫秒**（官方字段原样）。`is_final` 逐 token 给。"""
    text: str
    start_ms: int
    end_ms: int
    is_final: bool = False
    #: Soniox `language`（开 `enable_language_identification` 后逐 token 给；没有 → `""`）。
    lang: str = ""
    #: Soniox `speaker`（开 `enable_speaker_diarization` 后逐 token 给；没有 → `""`）。
    #: 原始编号字符串（`"1"` / `"2"`…），显示用字母是下游的事（`speaker_label`）。
    speaker: str = ""


class Sealed(NamedTuple):
    """一句封口。`end_s` = 句末词结束的音频时刻（秒，相对本流起点）。"""
    text: str
    start_s: float
    end_s: float
    #: 句内 token 的多数语种：`"zh"` 要求文本里确有汉字；没开语种识别 / 没标签 → `""`。
    lang: str = ""
    #: 句内多数说话人（按词数，并列取先出现的）；没开分离 / 没标签 → `""`。
    speaker: str = ""


_HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


def _sent_lang(toks: Sequence["Tok"]) -> str:
    """一句的语种：多数 token 标 `zh` 且文本里确有汉字 → `"zh"`；否则取最多的非空标签；都没有 → `""`。

    `""` 与 `"en"` 分开：`""` 是「不知道」（老配置 / 退回 v5 / 消息没带标签），下游别当成英文。
    """
    zh = sum(1 for t in toks if t.lang == "zh")
    if zh * 2 > len(toks) and _HAN.search("".join(t.text for t in toks)):
        return "zh"
    tags = [t.lang for t in toks if t.lang and t.lang != "zh"]
    if not tags:
        return ""
    cnt = collections.Counter(tags)                       # 并列取先出现的那个 —— 不随 PYTHONHASHSEED 变
    return max(cnt, key=lambda k: (cnt[k], -tags.index(k)))


def _sent_speaker(toks: Sequence["Tok"]) -> str:
    """一句的说话人：按**词数**（`len(text.split())`，汉字 token 算 1 个词）取多数，
    并列取先出现的那个（`Counter.most_common` 同计数下按插入顺序 → 不随 PYTHONHASHSEED 变）。

    为什么按词数不按 token 数：Soniox 把中文切成单字 token、英文切成词，按 token 数会让
    汉字说话人的票数虚高（同一句话里 zh token 数量约是 en 的三倍）。
    都没有 → `""`（没开分离 / 没标签）；`""` 不是说话人，不参与投票。
    """
    cnt: collections.Counter[str] = collections.Counter()
    for t in toks:
        w = len(t.text.split())
        if t.speaker and w:
            cnt[t.speaker] += w
    # 一句全空 / 全是纯标点 token → 没人得分 → ""
    return cnt.most_common(1)[0][0] if cnt else ""


def parse_tokens(raw) -> list[Tok]:
    """Soniox 消息的 `tokens` → `[Tok]`。

    - 没有 `start_ms` 的（`<end>` / `<fin>` 控制帧）**丢掉**；
    - 坏条目（缺字段 / 类型不对）静默跳过 —— 收线程里不许抛。
    """
    out: list[Tok] = []
    if not isinstance(raw, list):
        return out
    for t in raw:
        if not isinstance(t, dict):
            continue
        text = t.get("text")
        st, en = t.get("start_ms"), t.get("end_ms")
        if not isinstance(text, str) or text in CONTROL:
            continue
        if isinstance(st, bool) or isinstance(en, bool) or not isinstance(st, (int, float)) \
                or not isinstance(en, (int, float)):
            continue
        lg = t.get("language")
        sp = t.get("speaker")
        out.append(Tok(text, int(st), int(en), bool(t.get("is_final")),
                       lg if isinstance(lg, str) else "",
                       sp if isinstance(sp, str) else ""))
    return out


class StreamCore:
    """纯逻辑核心：消息进，草稿 / 封口句出。**单线程用**（StreamASR 在外面加锁）。

    时钟注入（`clock` 返回秒，默认 `time.monotonic`）：P_stable 判据可毫秒级单测。
    """

    def __init__(self, *, clock: Callable[[], float] = time.monotonic,
                 stable_s: float = STABLE_S) -> None:
        self._clock = clock
        self._stable_s = float(stable_s)
        self._final: list[Tok] = []
        self._nonfinal: list[Tok] = []
        self._final_keys: set[tuple[str, int]] = set()    # 已收进 final 文本的 token（防御性去重）
        #: 水位线：已封口到哪个音频时刻（毫秒，= 上一句句末 token 的 end_ms）。之后**结束在它之前**的
        #: token 一律当「已封口句的旧词」丢掉 —— non-final 变 final 时文本 / start_ms 常会微调，
        #: 按 (文本, start_ms) 精确匹配认不出来，同一句会被封好几遍（10-17 真链路冒烟抓到）。
        self._wm_ms = -1
        self._sentences: list[Sealed] = []
        #: 当前候选句末标点 (文本, start_ms)：取**最早**那个（不是最后那个 —— 老师接着往下说时
        #: 新句的句号会出现在后面，取最后那个会让计时一直重来，前一句要等到 final 才封）。
        self._cand_key: tuple[str, int] | None = None
        self._cand_at: float | None = None

    # ---- 输入

    def feed_msg(self, msg) -> None:
        """吃一条 Soniox 消息（`{"tokens": [...]}`；没有 tokens 的忽略）。永不抛。"""
        if not isinstance(msg, dict):
            return
        toks = parse_tokens(msg.get("tokens"))
        if not toks:
            return
        fin = [t for t in toks if t.is_final]
        nf = [t for t in toks if not t.is_final]
        self.feed(fin, nf)

    def feed(self, finals: Iterable[Tok], nonfinal: Iterable[Tok]) -> None:
        """吃一条**已预分好**的消息：final 列表 + non-final 快照（整批替换）。"""
        for t in finals:
            self._append_final(t)
        self._nonfinal = [t for t in nonfinal if t.end_ms > self._wm_ms]
        self._seal_final_ends()
        self._update_candidate()
        self._maybe_seal()

    # ---- 输出

    def draft_text(self) -> str:
        """当前未封口那句 = 已 final 的尾巴 + non-final 快照。"""
        return "".join(t.text for t in self._final) + "".join(t.text for t in self._nonfinal)

    def take_sentences(self) -> list[Sealed]:
        """取走已封口句（取一次就清）。"""
        out, self._sentences = self._sentences, []
        return out

    def at_boundary(self) -> bool:
        """此刻是不是在**两句之间**（手上没挂着半句，或挂着的那句已经说到句末标点）。

        ⭐ 3.11 端点换行：Soniox 的 `<end>` 约 38% 落在一句话中间（老师说到一半停顿，
        `snx/REPORT_ep.md`）—— 只有落在句与句之间的才算「讲完一口气」。
        """
        tail = (self.draft_text() or "").rstrip().rstrip("\"')\u201d\u2019\u300d\u300f\uff09\u3011")   # 中文右引号 / 括号也剥
        return not tail or tail[-1] in END_PUNCT

    def pending(self) -> bool:
        """手上还有没有 token（收尾时判「还要不要等 final」用）。"""
        return bool(self._final or self._nonfinal)

    def tick(self) -> list[Sealed]:
        """时钟推进：把到期的 P_stable 封口切出来。**必须在没有新消息时也定期调**
        —— 一句说完之后 Soniox 会安静下来，只在 `feed` 里判就永远封不了口（真踩过）。
        """
        self._maybe_seal()
        return self.take_sentences()

    def flush(self) -> list[Sealed]:
        """收尾：手上还挂着的半句也封出去（按最后一块音频的结束时刻收口）。

        不下课时最后半句（没等到标点）不封就整句丢掉 —— 收尾调用方拿它补一句。
        """
        self._maybe_seal()
        if self._final or self._nonfinal:
            toks = self._final + self._nonfinal
            self._sentences.append(Sealed(
                "".join(t.text for t in toks),
                min(t.start_ms for t in toks) / 1000.0,
                max(t.end_ms for t in toks) / 1000.0, _sent_lang(toks), _sent_speaker(toks)))
            self._final, self._nonfinal = [], []
            self._cand_key, self._cand_at = None, None
        return self.take_sentences()

    def seal_open(self) -> list[Sealed]:
        """**手动收口**：把手上的半句封出去并推水位线（`{"type":"finalize"}` / `<fin>` 用）。

        ⚠️ 与 `flush()` 的区别：这条走**会话中间**，必须推 `_wm_ms` —— 否则下一条
           non-final 快照会把刚封过的词再带回来，同一句进两次笔记。
        ⚠️ 代价是可能在句中切开（没有标点）。这是有意的取舍：2026-11-09 美国用户课
           连续 12 分钟没有 endpoint，宁可切段也不整段丢。
        """
        self._maybe_seal()
        if self._final or self._nonfinal:
            toks = self._final + self._nonfinal
            end = max(t.end_ms for t in toks)
            self._sentences.append(Sealed(
                "".join(t.text for t in toks),
                min(t.start_ms for t in toks) / 1000.0,
                end / 1000.0, _sent_lang(toks), _sent_speaker(toks)))
            self._wm_ms = max(self._wm_ms, end)
            self._final = [t for t in self._final if t.end_ms > self._wm_ms]
            self._nonfinal = [t for t in self._nonfinal if t.end_ms > self._wm_ms]
            self._cand_key, self._cand_at = None, None
        return self.take_sentences()

    # ---- 语意封口（`semantic_seal.py` 用）

    def unsealed_words(self) -> list[str]:
        """未封口那段文字的词（按空白切）。语意封口按「第几个词」说话，两边的切法必须一致。"""
        return self.draft_text().split()

    def seal_through_word(self, k: int, expect: Sequence[str] | None = None) -> bool:
        """语意封口：把未封口文字的前 `k+1` 个词（0 起）当一句封掉，水位线推到第 `k` 词末 token 的 `end_ms`。

        ⭐ 走的就是句号封口那条 `_seal` —— 水位线之前的词之后一律当旧词丢，所以 Soniox 后来把
        这段变 final / 补上句号都不会再封一遍。**触发判据换了，去重机制没换。**
        不封的情况（返回 `False`，什么都不动）：`k` 越界；`expect` 给了而当前前缀已经变了
        （决策花了一秒，这期间 Soniox 改了词或这句已被句号封走）；第 `k` 词落在一个跨词 token 中间。
        """
        toks = self._final + self._nonfinal
        text = "".join(t.text for t in toks)
        words = text.split()
        if k < 0 or k >= len(words):
            return False
        if expect is not None and words[:k + 1] != list(expect[:k + 1]):
            return False
        # 每个 token 的最后一个非空白字符落在第几个词里
        widx, prev_space, last_word = -1, True, []
        for t in toks:
            lw = None
            for ch in t.text:
                if ch.isspace():
                    prev_space = True
                else:
                    if prev_space:
                        widx += 1
                    prev_space = False
                    lw = widx
            last_word.append(lw)
        end = None
        for t, lw in zip(toks, last_word):
            if lw is not None and lw <= k:
                end = t if lw == k else end
        if end is None:
            return False
        self._seal(end)
        self._update_candidate()
        return True

    # ---- 内部

    #: 两次看到的句末标点算「同一个」的容差：non-final 修订时 start_ms 会抖几十毫秒
    #: （10-16 离线评估用的也是 ±150 ms）。
    CAND_TOL_MS = 150

    def _append_final(self, t: Tok) -> None:
        key = (t.text, t.start_ms)
        if t.end_ms <= self._wm_ms or key in self._final_keys:
            return                                  # 已封口句的旧词 / 重复 final
        self._final_keys.add(key)
        self._final.append(t)

    def _seal_final_ends(self) -> None:
        """final 里出现句末标点 / 换人 → 立刻封到那里（不用等 P_stable）；一条消息里可能有几句。

        先到先封：`_seal` 会推水位线，下一次循环自然从新位置往后看，一条消息里的多处
        断点全部处理完。
        """
        while True:
            end = next((t for t in self._final if _is_end(t.text)), None)
            sw = self._switch_mark()
            if sw is not None and (end is None or sw.end_ms < end.end_ms):
                end = sw                             # 换人比句末标点更早 → 先在换人处断
            if end is None:
                return
            self._seal(end)

    def _switch_mark(self) -> Tok | None:
        """封口位置：**最后一个属于上一个说话人的 final token**（下一个 token 换人了）。

        ⚠️ 返回的是新说话人**前面那个** token，因为 `_seal(end)` 封的是「到 end 为止」
        —— 返回新人第一个 token 会把他的第一个词一起封进别人那句。
        ⚠️ 只看 final：Soniox 实时会临时换人随后稳定（官方说明），拿 non-final 的标签切句会
        把老师一句切成好几截。任一侧 speaker 为空（没开分离 / 该 token 没标签）不算换人。
        """
        if not SPLIT_ON_SPEAKER:
            return None
        prev: Tok | None = None
        for t in self._final:
            if prev is not None and prev.speaker and t.speaker and t.speaker != prev.speaker:
                return prev
            prev = t
        return None

    def _update_candidate(self) -> None:
        end = next((t for t in self._nonfinal if _is_end(t.text)), None)
        if end is None:
            self._cand_key, self._cand_at = None, None
            return
        c = self._cand_key
        if (c is not None and c[0] == end.text
                and abs(c[1] - end.start_ms) <= self.CAND_TOL_MS):
            self._cand_key = (end.text, end.start_ms)   # 同一个句号，位置微调：不重新计时
            return
        self._cand_key, self._cand_at = (end.text, end.start_ms), self._clock()

    def _maybe_seal(self) -> None:
        if self._cand_key is None or self._cand_at is None:
            return
        if self._clock() - self._cand_at < self._stable_s:
            return
        end = next((t for t in self._nonfinal
                    if _is_end(t.text) and abs(t.start_ms - self._cand_key[1]) <= self.CAND_TOL_MS),
                   None)
        if end is not None:
            self._seal(end)
        else:
            self._cand_key, self._cand_at = None, None
        self._update_candidate()                    # 后面可能还挂着下一句的句号：从现在起计时

    def _seal(self, end: Tok) -> None:
        """封出「到 `end` 这个句末 token 为止」的那一句（final 尾巴 + non-final），水位线推到它的 end_ms。"""
        toks = [t for t in self._final + self._nonfinal if t.end_ms <= end.end_ms]
        if toks:
            self._sentences.append(Sealed(
                "".join(t.text for t in toks),
                min(t.start_ms for t in toks) / 1000.0,
                max(t.end_ms for t in toks) / 1000.0, _sent_lang(toks), _sent_speaker(toks)))
        self._wm_ms = max(self._wm_ms, end.end_ms)
        self._final = [t for t in self._final if t.end_ms > self._wm_ms]
        self._nonfinal = [t for t in self._nonfinal if t.end_ms > self._wm_ms]
        self._cand_key, self._cand_at = None, None

_DOMAINS = {
    "ECON": "economics", "SOC": "sociology", "COMP": "computer science",
    "CS": "computer science", "MATH": "mathematics", "STAT": "statistics",
    "PHIL": "philosophy", "PSY": "psychology", "FIN": "finance",
    "ACC": "accounting", "LAW": "law", "GEOG": "geography", "HIST": "history",
}


def _domain_of(title: str) -> str:
    """课名开头形如 `ECON10730 …` → `"economics"`；认不出返回 `""`。"""
    m = re.match(r"\s*([A-Za-z]{2,})\s*\d", title or "")
    return _DOMAINS.get(m.group(1).upper(), "") if m else ""


def general_pairs(title: str) -> list[tuple[str, str]]:
    """课程标题 → 官方 `general` 模板的键值对（≤ `GENERAL_KEYS_MAX` 个短键值）。

    模板（官方 context 页 §Tips / §Experimental，2026-10-17 核对）：
    `topic`（课名）/ `domain`（由课号前缀认，认不出不发）/ `setting` / `language`。
    **没有标题 → 空表**（不凭空发明上下文）。
    """
    t = (title or "").strip()
    if not t:
        return []
    out = [("topic", t)]
    d = _domain_of(t)
    if d:
        out.append(("domain", d))
    out.extend([("setting", "university lecture"), ("language", "English")])
    return out[:GENERAL_KEYS_MAX]


def build_context(terms: Iterable[str], title: str = "", text: str = "",
                  *, budget: int = CONTEXT_CHAR_BUDGET,
                  terms_max: int = TERMS_MAX) -> tuple[dict, dict]:
    """组装 Soniox `context` 消息 + 记账。**纯函数** —— 判据直接喂它。

    优先序（预算/条数不够时先保前面的）：general（领域先验）→ terms（**按给定顺序**
    取前 `terms_max` 条 —— 撞上限时先保排在前的）→ text（截到剩余额度，`CONTEXT_TEXT_MAX` 封顶）。

    预算按**序列化后的字符数**算（官方 8 000 token ≈ 10 000 字符；默认 9 000 留余量），
    三个段落**合起来**不许超。返回 `(ctx, stats)`；`ctx` 空 dict = 不带 context 键。
    `stats` 键：`terms_n` / `terms_dropped`（**不进 context 的条数 —— 必须记账，不许静默**）/
    `terms_chars` / `general_n` / `text_chars`。
    """
    ctx: dict = {}
    stats = {"terms_n": 0, "terms_dropped": 0, "terms_chars": 0,
             "general_n": 0, "text_chars": 0}
    pairs = general_pairs(title)
    if pairs:
        ctx["general"] = [{"key": k, "value": str(v)[:GENERAL_VALUE_MAX]}
                          for k, v in pairs[:GENERAL_KEYS_MAX]]
        stats["general_n"] = len(ctx["general"])
    used = len(json.dumps(ctx, ensure_ascii=False)) if ctx else 2
    room = max(0, budget - used - _CONTEXT_SERIAL_MARGIN)
    all_terms = [str(t).strip() for t in terms if str(t).strip()]
    kept: list[str] = []
    for i, t in enumerate(all_terms):
        if i >= max(0, int(terms_max)):
            stats["terms_dropped"] += len(all_terms) - i
            break
        if len(t) > TERM_MAX_CHARS:
            stats["terms_dropped"] += 1        # 手抄的长句不是术语：跳过、记账、继续
            continue
        if len(t) + 4 > room:
            stats["terms_dropped"] += len(all_terms) - i
            break
        room -= len(t) + 4
        kept.append(t)
    if kept:
        ctx["terms"] = kept
        stats["terms_n"] = len(kept)
        stats["terms_chars"] = sum(len(t) for t in kept)
    txt = (text or "").strip()
    if txt:
        cap = min(CONTEXT_TEXT_MAX, max(0, room - 12))    # `, "text": ""` 的骨架
        cut = txt[:cap]
        if cut:
            ctx["text"] = cut
            stats["text_chars"] = len(cut)
    # 兜底：转义字符（引号等）让保守估计漏了 → 从尾部继续丢术语（照旧记账）。
    while ctx.get("terms") and len(json.dumps(ctx, ensure_ascii=False)) > budget:
        ctx["terms"].pop()
        stats["terms_n"] -= 1
        stats["terms_dropped"] += 1
    if not ctx.get("terms"):
        ctx.pop("terms", None)
    return ctx, stats
