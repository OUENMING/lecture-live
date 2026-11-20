"""实时核心的浏览器胶水层：Soniox 原始消息 / 音频帧节拍进，一版快照（JSON 字符串）出。

`stream_core.py` 等 8 个文件是从 S1 穿刺目录**原样拷贝**的（一个字节都不改，P2 提纯后才换成
仓库里的正式模块）；本文件是网页版自己的一层，只做三件事：

1. 一条消息里按**桌面版主循环的同一顺序**推一拍：`tick()`（把到期的 P_stable 封口切出来）→
   `feed_msg()`。顺序不能反 —— 反了封口会晚一条消息，`tests/core/tape.test.ts` 拿 CPython 基线钉着。
2. 时钟注入：回放磁带里每条消息带 `_wall`（到达秒数），整堂课没有真时钟也能逐字复现；
   真链路里没有 `_wall`，退回 `time.monotonic()`。**不读别的环境**。
3. 输出一版快照（投影交给 TS 的 `project.ts`，这里只给数据）。

下课时走 `flush()`：直接调 `StreamCore.flush()` —— 桌面版收尾（`stream_asr._finish_stream`）
就是这一句，规则不在这里另发明。它只**封一次**（`_final` / `_nonfinal` 清空），所以重复调
不会让同一句进两次笔记。

不给回调、不写文件、不联网、不带计时（耗时在主线程量）。

⭐ **翻译相关的入口（W10）**也在这里，同样只做搬运：`build_request` / `build_draft_request`
调 `prompt_core` 拼请求体，`draft_begin` / `draft_accept` / `draft_final` 是
`draft_translate.DraftView` 的那三个判据。为什么不让 TS 重写一遍：过期判据与提示词都是桌面
的**同一份代码**，两边各写一份就等于放弃「逐字相同」这条判据。
⚠️ `TRANSLATE_TAIL` 是本文件里唯一一段提示词文本 —— 它在桌面上属于
`cloud_translator.fix_and_translate_stream`（那个模块带着 httpx，搬不进浏览器）。
`tests/translate/` 的 fixture 用桌面 CPython 真调一次生成，这里差一个字就会红。
"""
from __future__ import annotations

import json
import time
from typing import Any

from stream_core import Sealed, StreamCore

import prompt_core
from draft_translate import DraftView
from prompt_core import DRAFT_SYSTEM_CLOUD, SYSTEM_PROMPT_CLOUD


def _sealed(s: Sealed) -> dict[str, Any]:
    return {"text": s.text, "lang": s.lang, "start": s.start_s, "end": s.end_s}


class RealtimeCore:
    """一个会话一个实例。单线程用（Web Worker 里只有一条消息队列）。"""

    def __init__(self, *, stable_s: float | None = None) -> None:
        self._tape_now: float | None = None      # 回放：磁带给的到达秒数；真链路一直 None
        self._t0: float | None = None
        self._n_msgs = 0
        self._seq = 0
        self._notice: str | None = None
        kwargs: dict[str, Any] = {} if stable_s is None else {"stable_s": stable_s}
        self._core = StreamCore(clock=self._clock, **kwargs)

    # ---- 时钟

    def _clock(self) -> float:
        return self._tape_now if self._tape_now is not None else time.monotonic()

    def _elapsed(self) -> float:
        """本课已过的秒数（第一条消息 / 第一拍为 0）。"""
        now = self._clock()
        if self._t0 is None:
            self._t0 = now
        return now - self._t0

    # ---- 输入

    def feed(self, raw: str) -> str:
        """吃一条 Soniox 原始消息（`{"tokens": [...]}`，回放磁带里还带 `_wall`）。永不抛。"""
        sealed: list[dict[str, Any]] = []
        try:
            msg = json.loads(raw)
        except ValueError as e:                  # 库里坏行：给界面一条提示，别炸掉整堂课（U6）
            self._notice = f"消息不是 JSON：{e}"
            return self._snap(sealed)
        self._notice = None
        if isinstance(msg, dict):
            wall = msg.pop("_wall", None)        # 回放时钟，不进 core 的输入
            if isinstance(wall, (int, float)) and not isinstance(wall, bool):
                self._tape_now = float(wall)
        self._n_msgs += 1
        sealed.extend(_sealed(s) for s in self._core.tick())   # 主循环那一拍
        self._core.feed_msg(msg)
        sealed.extend(_sealed(s) for s in self._core.take_sentences())
        return self._snap(sealed)

    def tick(self, seq: int) -> str:
        """音频帧节拍：没有新消息时也得推时钟，否则老师说完一句安静下来就永远封不了口。"""
        self._seq = int(seq)
        return self._snap([_sealed(s) for s in self._core.tick()])

    def flush(self) -> str:
        """下课收尾：手上还没封口的尾句强制定稿（桌面版 `_finish_stream` 同一条路）。

        `StreamCore.flush()` 自己会把 `_final` / `_nonfinal` 清空，所以调两次不会重复出句；
        这里不再加 `_wm_ms` 之类的新判据 —— 那句「不许自己发明规则」就是为了这个。
        """
        return self._snap([_sealed(s) for s in self._core.flush()])

    # ---- 输出

    def _draft_start(self) -> float | None:
        """当前未封口那句的起点（音频秒）。

        `StreamCore.draft_text()` 只给文本，没有公开的起点访问口；这里只**读**它手上还没封口的
        token（`_final` + `_nonfinal`），不改状态。取不到就是 `None`，投影那边退回 `now`。
        """
        toks = getattr(self._core, "_final", []) + getattr(self._core, "_nonfinal", [])
        return min(t.start_ms for t in toks) / 1000.0 if toks else None

    def _snap(self, sealed: list[dict[str, Any]]) -> str:
        return json.dumps(
            {
                "now": self._elapsed(),
                "draft": self._core.draft_text(),
                "draft_start": self._draft_start(),
                "sealed": sealed,
                "n_msgs": self._n_msgs,
                "seq": self._seq,
                "notice": self._notice,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )


_CORE = RealtimeCore()


# ── 翻译：请求体（桌面 `CloudTranslator.fix_and_translate_stream` 的同一段尾句）

#: 定稿句末尾的任务指令。**逐字**抄桌面 `cloud_translator.fix_and_translate_stream` ——
#: 那个模块 import httpx，进不了浏览器；`tests/translate/fixtures.py` 用桌面 CPython
#: 把完整请求体生成成 fixture，这里改一个字就对不上。
TRANSLATE_TAIL = (
    "Now translate the ASR utterance above into Chinese and output exactly:\n"
    "ZH: <Chinese translation of the CORRECTED sentence>\n"
    "EN: <same utterance with ASR mishearings fixed>\n"
    "Do not repeat the course-terms list. No headings."
)


def _config(cfg: str | None) -> dict[str, Any]:
    """一句的术语/课程配置（TS 那边一次给全，这里不读环境变量、不查表）。

    缺字段一律当空：网页版还没有术语表与课名，空配置出来的 prompt 与桌面空术语表时逐字相同。
    """
    try:
        raw = json.loads(cfg) if cfg else {}
    except ValueError:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    return {
        "domain": str(raw.get("domain") or ""),
        "terms": list(raw.get("terms") or []),
        "core": list(raw.get("core") or []),
        "always": list(raw.get("always") or []),
        "term_zh": dict(raw.get("term_zh") or {}),
        "ctx_chunk": int(raw.get("ctx_chunk") if raw.get("ctx_chunk") is not None else 10),
        "max_context": int(raw.get("max_context") if raw.get("max_context") is not None else 2),
    }


def _context(context_json: str) -> list[str]:
    try:
        ctx = json.loads(context_json) if context_json else []
    except ValueError:
        ctx = []
    return [str(x) for x in ctx] if isinstance(ctx, list) else []


def build_request(en: str, context_json: str, cfg: str) -> str:
    """定稿句的请求体 → `{"system", "user"}`（JSON 字符串）。

    拼法全在 `prompt_core.user_content`（两种布局 / 译名段位置 / 前缀缓存顺序都是它的判据），
    这里只补尾句 —— 与桌面 `fix_and_translate_stream` 逐字相同。
    """
    c = _config(cfg)
    user = prompt_core.user_content(
        c["domain"], c["terms"], c["core"], c["always"], c["term_zh"],
        en, _context(context_json), TRANSLATE_TAIL,
        ctx_chunk=c["ctx_chunk"], max_context=c["max_context"], zh=True)
    return json.dumps({"system": SYSTEM_PROMPT_CLOUD, "user": user},
                      ensure_ascii=False, sort_keys=True)


def build_draft_request(en: str, context_json: str, cfg: str) -> str:
    """草稿句的请求体（`prompt_core.draft_user` + `DRAFT_SYSTEM_CLOUD`）。

    与桌面 `CloudTranslator.translate_draft` 一致：带前文（`CLASSLIVE_DRAFT_CONTEXT` 默认开），
    关掉那条开关在老版桌面上只是「消息 = 原文」，浏览器里没必要留那个口子。
    """
    c = _config(cfg)
    user = prompt_core.draft_user(c["domain"], c["always"], c["term_zh"], en,
                                  _context(context_json))
    return json.dumps({"system": DRAFT_SYSTEM_CLOUD, "user": user},
                      ensure_ascii=False, sort_keys=True)


# ── 翻译：草稿的过期判据（`draft_translate.DraftView`，一个字都不在这里重写）


class _Draft:
    """`DraftView` + 一个自增的送翻序号（桌面 `main` 那边由主循环发号）。

    ⚠️ `DraftView` 用线程锁保护 `_seq_gen`；Pyodide 里只有一条消息队列，加锁也无害。
    """

    def __init__(self) -> None:
        self.view = DraftView()
        self.seq = -1
        self.stats = self.view.stats          # 同一个 dict（桌面收尾打印用它）

    def begin(self, seg: int, text: str) -> dict[str, Any]:
        """新草稿：要不要清掉屏上的中文 + 这句话的代次 + 这次送翻的序号。"""
        self.seq += 1
        clear = self.view.on_draft(int(seg), text)
        gen = self.view.generation()
        self.view.note_submitted(self.seq, gen)
        return {"clear": clear, "gen": gen, "seq": self.seq}

    def accept(self, seq: int, seg: int, gen: int) -> bool:
        return self.view.accept(int(seq), int(seg), int(gen))

    def final(self, seg: int) -> bool:
        return self.view.on_final(int(seg))


_DRAFT = _Draft()


def draft_begin(seg: int, text: str) -> str:
    """一份新英文草稿到了 → `{clear, gen, seq}`。`clear` = 界面该清掉上一句的中文。"""
    return json.dumps(_DRAFT.begin(seg, text), sort_keys=True)


def draft_accept(seq: int, seg: int, gen: int) -> bool:
    """这份译文还能上屏吗（过期 / 更新的已显示 / 该段已定稿 → False）。"""
    return bool(_DRAFT.accept(seq, seg, gen))


def draft_final(seg: int) -> bool:
    """某段定稿了（`DraftView.on_final`；返回值 = 该不该清掉草稿槽）。"""
    return bool(_DRAFT.final(seg))


def feed(raw: str) -> str:
    return _CORE.feed(raw)


def tick(seq: int) -> str:
    return _CORE.tick(seq)


def flush() -> str:
    return _CORE.flush()


def reset() -> None:
    """换一盘磁带 / 开一堂新课：模块级单例清空（`import rt` 不会再执行一遍）。

    浏览器那边不用它 —— 换会话就 `dispose()` 换 Worker；这里给同一份 Pyodide 上连跑多盘磁带的测试用。
    """
    global _CORE, _DRAFT
    _CORE = RealtimeCore()
    _DRAFT = _Draft()
