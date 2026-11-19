"""翻译提示词的拼装（只吃字符串、只吐字符串的纯函数）。

管什么：SYSTEM_PROMPT_CLOUD / DRAFT_SYSTEM_CLOUD / FIX_SYSTEM_CLOUD 三条 system prompt，
术语三档（core / always / 池子）与译名段的召回，以及一句 user 消息的两种布局（老滑动窗口 /
块对齐）与草稿消息。对外只这几个函数，没有类、没有全局状态。

为什么这样：`translator.py` / `cloud_translator.py` 带着 mlx、httpx、线程与文件读取，搬不进
浏览器；这一层把「提示词一个字怎么写」独立出来，桌面版与网页版（`docs/PLAN-web-cloud-20261114.md`
§P2 提纯、§M2 网页版）用**同一份**代码，判据也能直接对着字符串比。

纪律：只 import 标准库；这里是**逐字搬运**，任何提示词字符、段序、空白都不许改 ——
`tests/test_translator.py` 的冻结用例与 `tests/test_prompt_core.py` 的旧实现对比钉着。
"""
from __future__ import annotations
import difflib
import os


# 常驻核心词(高频概念): 无论句子内容都注入, 约 15 token。
# 注意: 纯按匹配筛选会漏掉"被听错而正需纠正"的术语, 所以核心词必须常驻。
# 课号不写死在这里 —— 由 core_terms(course) 从 .course / 术语表载入。
CORE_TERMS = [
    "Brightspace", "midterm", "final assessment component",
    "essential readings", "tutorial", "quiz",
]
MAX_DYNAMIC_TERMS = 3
NO_TERMS = "(无特定术语)"           # `select_terms` 没有任何术语时的占位（云端 prompt 里不再用它，见 `select_term_parts`）

#: ⭐ #4 B2（2026-10-17 作者拍板）：术语**中文译名**注入的开关与上限。
#: 开关在内（默认开，`CLASSLIVE_TERM_ZH=0` 关）—— `select_term_zh` 里判，
#: 调用方拿到的永远是"按当下开关该注入的"，不可能忘。
TERM_ZH_ENV = "CLASSLIVE_TERM_ZH"
TERM_ZH_MAX_ENV = "CLASSLIVE_TERM_ZH_MAX"
#: 每句最多注入几条译名（`CLASSLIVE_TERM_ZH_MAX` 可配；先 8 条，作者 2026-10-17）。
TERM_ZH_MAX = 8
#: 译名段的小标题（四处 prompt 共用同一份，别各写各的）。
TERM_ZH_HEADER = "Term translations for this utterance (prefer these Chinese renderings):"


def term_zh_enabled() -> bool:
    """`CLASSLIVE_TERM_ZH`：默认**开**；`0/false/no/off` 关。"""
    v = (os.environ.get(TERM_ZH_ENV) or "").strip().lower()
    if not v:
        return True
    return v in ("1", "true", "yes", "on")


def _term_zh_max() -> int:
    """`CLASSLIVE_TERM_ZH_MAX` → 条数上限；没设/不合法 → `TERM_ZH_MAX`（**不抛**）。"""
    raw = (os.environ.get(TERM_ZH_MAX_ENV) or "").strip()
    if raw:
        try:
            n = int(raw)
            if n > 0:
                return n
        except ValueError:
            pass
    return TERM_ZH_MAX


#: ⭐ #4 B2（2026-10-17 作者拍板：只注入**本句命中**的译名，不注入全表）：
#: 复核过的**错语境**条目 —— `term_notes.json` 里的释义与课堂语境不符，一律不进译名表。
#: 这五条来自 `scratchpad/chain/ab/review.txt`（B2 实验的人工复核）；⚠️ 它们今天**全是
#: gloss 档**（`level != "basic"` 已天然挡住）—— 名单是**第二道闸**：哪天被改成 basic、
#: 或又冒出新条目，判据（`tests/test_translator.py`）仍然钉着"不许注入"。
#: 表里每行都写"为什么"。名单一旦变长就说明有别的信号没接上（同 EVERYDAY 的纪律）。
_ZH_BLOCKED = {
    "cso": "term_notes=公民社会组织；本课术语表配对的是 Central Statistics Office（中央统计局）",
    "moderator": "term_notes=调节变量（统计模型语境）；课堂里是主持老师",
    "panel": "term_notes=面板数据（计量语境）；课堂里是 panel discussion（嘉宾讨论）",
    "fiona macmanus": "人名（嘉宾）；term_notes 的公民社会研究语境与本课对不上",
    "epoch": "term_notes=地质纪元（气候/地质课语境）；经济课无此用法",
}


def term_zh_map_from_notes(terms: list | None, notes) -> dict[str, str]:
    """闸的**纯**部分：`(本课术语表, 已读好的 TermNotes) → {英文: 中文名}`。

    文件读取与「`term_notes.json` 不存在就别构造 TermNotes」留在 `translator.term_zh_map`
    （那是套在它外面的唯一装配点，`Translator` 工厂与 `main.py` 都只认那个名字）。

    闸（任一不过就不注入）：
      1. 词在**本课术语表**里（`course_term_list`，与 always 段同一份加载口径与上限）；
      2. `TermNotes` 查得到 且 `level == "basic"` 且 detail 非空（= "有中文名"）；
      3. 不是人名条目（`type: person`）；
      4. 不在 `_ZH_BLOCKED`（A/B 复核过的错语境条目，见上）。

    ⚠️ 刻意**不**沿用 `note_terms.drop_reason` 的「常识词」那一支：它判的是
       "值不值得给学生讲"（`slope`/`plot`/`graph` 被它挡），而译名注入是为**翻译一致性**
       —— "slope — 斜率"恰恰有用。取舍有判据钉着（test_translator 的 B2 用例）。
    """
    try:
        ts = list(terms) if terms is not None else []
        if not ts:
            return {}
        out: dict[str, str] = {}
        for t in ts:
            k = str(t).strip()
            if not k or k.lower() in _ZH_BLOCKED:
                continue
            try:
                _src, entry = notes.entry_of(k)
            except Exception:                                  # noqa: BLE001
                continue
            if not isinstance(entry, dict) or entry.get("level") != "basic":
                continue
            if str(entry.get("type") or "").lower() == "person":
                continue
            detail = str(entry.get("detail") or "").strip()
            if detail:
                out[k] = detail
        return out
    except Exception:                                          # noqa: BLE001
        return {}


def select_term_parts(sentence: str, terms: list[str],
                      core: list[str] | None = None, max_dyn: int = MAX_DYNAMIC_TERMS,
                      always: list[str] | None = None,
                      stats: dict | None = None) -> tuple[list[str], list[str]]:
    """核心词常驻 + **课程术语全量** + 动态召回最相关的几个。

    返回 `(stable, dyn)`：`stable` = 核心 + 课程术语（**与句子无关**、去重、保序），
    `dyn` = 本句召回的几个（**随句变**）。拆成两段是为了让云端 prompt 把稳定的放前面、
    随句变的放后面（前缀缓存只认「从头逐字相同」）；要老的一整串用 `select_terms`。

    ⚠️ 为什么课程术语要**全量**注入: 纯按匹配筛选会**死循环** —— 句子被 ASR 听错时
    (macroscopic→"max chocolate")术语匹配不到, 于是不注入, 模型没有领域先验, 永远修不回来。
    实测一堂真实热力学课: 65% 的句子矫正后与原文逐字相同, "max chocolate / apple
    temperature / chocolate bonds" 一路留着。课程术语表通常只有几十条(~100 token),
    全量注入的代价可接受。

    匹配: 字面包含优先, 否则与句中词的模糊相似度(difflib); 音近错听(如 nation→relation)
    相似度也较高, 能被召回。

    `stats`（可选，由调用方给的 dict）只做**只读计数**，不改返回值契约 —— 记的是
    **去重前**每一档贡献了几条 + 去重后实际注入几条，差值就是三档之间的重复量。
    """
    core = core if core is not None else CORE_TERMS
    always = always or []
    sent_l = sentence.lower()
    words = [w.strip(".,!?\"'()[]").lower() for w in sentence.split()]
    words = [w for w in words if len(w) > 3]

    core_l = {c.lower() for c in core}
    always_l = {a.lower() for a in always}
    scored: list[tuple[float, str]] = []
    for t in terms:
        tl = t.lower()
        if tl in core_l or tl in always_l:
            continue
        if tl in sent_l:
            scored.append((1.0, t))
            continue
        lt = len(tl)
        best = 0.0
        for w in words:
            lw = len(w)
            if 2 * (lt if lt < lw else lw) < 0.6 * (lt + lw):
                continue                          # real_quick_ratio 上界, 免构造
            r = difflib.SequenceMatcher(None, tl, w).ratio()
            if r > best:
                best = r
                if best >= 0.99:
                    break
        if best >= 0.6:
            scored.append((best, t))
    scored.sort(key=lambda x: x[0], reverse=True)
    seen: set = set()
    stable: list[str] = []
    dyn: list[str] = []
    for out, group in ((stable, list(core) + list(always)), (dyn, [t for _, t in scored[:max_dyn]])):
        for t in group:
            k = t.lower()
            if k not in seen:
                seen.add(k); out.append(t)
    picked = stable + dyn
    if stats is not None:
        stats["calls"] = stats.get("calls", 0) + 1
        stats["core"] = stats.get("core", 0) + len(core)
        stats["always"] = stats.get("always", 0) + len(always)
        stats["scored"] = stats.get("scored", 0) + len(scored[:max_dyn])
        stats["picked"] = stats.get("picked", 0) + len(picked)
        stats["empty"] = stats.get("empty", 0) + (0 if picked else 1)
    return stable, dyn


def select_terms(sentence: str, terms: list[str],
                 core: list[str] | None = None, max_dyn: int = MAX_DYNAMIC_TERMS,
                 always: list[str] | None = None,
                 stats: dict | None = None) -> str:
    """`select_term_parts` 的一整串形态（逐行一个术语）。老调用点（本地引擎、`ctx_chunk=0`）用它。"""
    stable, dyn = select_term_parts(sentence, terms, core, max_dyn, always, stats)
    picked = stable + dyn
    return "\n".join(picked) if picked else NO_TERMS


def select_term_zh(sentence: str, zh_map, *, max_n: int | None = None,
                   enabled: bool | None = None) -> list[str]:
    """本句命中的「英文 — 中文」条目（≤ 上限）。**纯函数、唯一定义点**。

    云端定稿 / 云端草稿 / 本地定稿 / 本地草稿四处都调它；量具（`.live.jsonl` 的
    `term_zh_n`）也拿同一把尺子数 —— 注入与记账不可能各算各的。

    命中判据**复用 `select_term_parts` 的动态召回**（字面包含优先、difflib ≥ 0.6）：
    与术语池同一套召回，不另写一份 —— B2 实验（`scratchpad/chain/ab/`）里就是这么跑的。

    ⚠️ **开关在里面**（`CLASSLIVE_TERM_ZH`，默认开）：调用方拿到的永远是"按当下开关
       该注入的"，不可能忘。`enabled=` 只给判据注入用；`max_n=None` → 读
       `CLASSLIVE_TERM_ZH_MAX`（默认 `TERM_ZH_MAX`）。
    """
    if enabled is None:
        enabled = term_zh_enabled()
    if not enabled or not zh_map or not sentence:
        return []
    n = _term_zh_max() if max_n is None else max(0, int(max_n))
    if n <= 0:
        return []
    _stable, dyn = select_term_parts(sentence, list(zh_map), core=[], always=[],
                                     max_dyn=n)
    return [f"{t} — {zh_map[t]}" for t in dyn]


def term_zh_block(sentence: str, zh_map, *, max_n: int | None = None) -> str:
    """`select_term_zh` 的 prompt 段落形态（无命中 → `""`，一个字符不加）。

    四处 prompt（云端定稿两种布局 / 云端草稿 / 本地两种）共用这一份标题与排版 ——
    别各写各的。段落以空行结尾，直接拼在下一节前面。
    """
    hits = select_term_zh(sentence, zh_map, max_n=max_n)
    if not hits:
        return ""
    return TERM_ZH_HEADER + "\n" + "\n".join(hits) + "\n\n"


def domain_block(title: str) -> str:
    return f"Course: {title}\n" if title else ""


# 云端用**英文** system prompt: 实测比中文指令首字快约 15%(0.51s vs 0.60s),
# 译文也更自然; 符合 DeepSeek 官方"提示词保持单一语言"的建议。
SYSTEM_PROMPT_CLOUD = """You are a simultaneous interpreter for university lectures.
The user gives one utterance of English lecture speech (already transcribed by ASR),
the course title, the course terminology, and recent context.

Output exactly two lines and nothing else:
ZH: <fluent, accurate Chinese translation OF THE CORRECTED SENTENCE>
EN: <the same sentence with ASR mishearings fixed>

Rules:
1) Silently fix mishearings FIRST, then translate the fixed reading. If you change a word
   on the EN line, the ZH line must reflect the fix — never translate an uncorrected
   garble literally.
2) Fix only clear ASR mishearings (homophones, missing words, proper nouns, technical
   terms). When a word is close in sound to a course term, it is almost certainly a
   mishearing — use the term. Examples:
   "we're max chocolate properties" -> "we're macroscopic properties";
   "how we measure the stalcy" -> "how we measure the statistics";
   "average on the lacker level" -> "average on the lattice level".
3) Keep wording that is already correct and not close to any term exactly as-is.
   Do NOT rewrite, polish, reorder or summarise it (guard against over-correction).
4) Render technical terms the way Chinese textbooks do.
5) If the input is just a list of terms or syllabus keywords, translate them word by word;
   never output a summary heading like "课程术语" or "翻译". """

DRAFT_SYSTEM_CLOUD = ("You are a real-time subtitle translator. "
                      "Translate the user's English speech into Chinese. "
                      "Output ONLY the Chinese translation, no labels, no explanation.")

# 只矫正、不翻译。同样用英文 prompt(与 SYSTEM_PROMPT_CLOUD 同理由: 首字更快)。
FIX_SYSTEM_CLOUD = """You are an ASR transcript corrector for university lectures.
The user gives one utterance of English lecture speech transcribed by ASR, the course title,
the course terminology, and recent context.

Fix ONLY clear ASR mishearings (homophones, missing words, proper nouns, technical terms).
When a word is close in sound to a course term, it is almost certainly a mishearing — use
the term. Examples:
"we're max chocolate properties" -> "we're macroscopic properties";
"how we measure the stalcy" -> "how we measure the statistics";
"average on the lacker level" -> "average on the lattice level".
Keep wording that is already correct and not close to any term exactly as-is. Do NOT
rewrite, polish, shorten, reorder or summarise it (guard against over-correction).

Output ONLY the corrected English sentence on one line. No label, no quotes, no explanation,
and never any Chinese."""


def lines_or_none(lines: list[str]) -> str:
    """段落里的「一行一条」；空 → `(none)`（prompt 里留一个明确占位，不空着）。"""
    return "\n".join(lines) if lines else "(none)"


def ctx_window(context: list[str], ctx_chunk: int, max_context: int) -> list[str]:
    """送给模型的前文。`ctx_chunk>0`：块对齐窗口，起点 = 「上一块」的起点，
    所以窗口长度在 [chunk, 2*chunk) 内、同一块内连续请求的起点相同（前缀可缓存）；
    `ctx_chunk==0`：老行为，最近 `max_context` 句。"""
    n = len(context)
    if ctx_chunk > 0:
        c = ctx_chunk
        return context[max(0, (n // c) * c - c):]
    return context[-max_context:] if max_context > 0 else []


def user_content(domain: str, terms: list[str], core: list[str], always: list[str],
                 term_zh: dict, en: str, context: list[str], tail: str,
                 *, ctx_chunk: int, max_context: int, zh: bool = True,
                 stats: dict | None = None) -> str:
    """一句的 user 消息。`tail` = 末尾的任务指令（翻译 / 只矫正，措辞与老版逐字相同）。

    ⚠️ 老布局（`ctx_chunk==0`）**逐字不变**。块对齐布局把**稳定的**东西放前面
    （课程 + 核心/课程术语），**随句变的**放后面（前文窗口之后才是本句召回的术语与本句）——
    前缀缓存只认「从头开始逐字相同的那一段」，顺序错了就白搭。
    ⭐ #4 B2：译名段（命中的「英文 — 中文」）也是**随句变**的 → 放在两种布局的
    变化段里（本句的前一节之后、`ASR utterance` 之前）。无命中 → 一个字符不加。
    `zh=False`（只矫正英文）：不注入译名（EN-only 没有译文，白占 token）。
    """
    ctx = ctx_window(context, ctx_chunk, max_context)
    ctx_block = lines_or_none([f"- {c}" for c in ctx])
    zblock = term_zh_block(en, term_zh) if zh else ""
    if ctx_chunk <= 0:
        return (f"{domain_block(domain)}"
                f"Course terms:\n{select_terms(en, terms, core=core, always=always, stats=stats)}\n\n"
                f"Recent context (already corrected):\n{ctx_block}\n\n"
                f"{zblock}"
                f"ASR utterance:\n{en}\n\n" + tail)
    stable, dyn = select_term_parts(en, terms, core=core, always=always, stats=stats)
    return (f"{domain_block(domain)}"
            f"Course terms:\n{lines_or_none(stable)}\n\n"
            f"Recent context (already corrected):\n{ctx_block}\n\n"
            f"Extra terms possibly relevant to this utterance:\n{lines_or_none(dyn)}\n\n"
            f"{zblock}"
            f"ASR utterance:\n{en}\n\n" + tail)


def draft_user(domain: str, course_terms: list[str], term_zh: dict,
               en: str, context: list[str]) -> str:
    """草稿的 user 消息: 稳定内容在前(领域 → 课程术语), 随句变的在后(上一句 → 本句) ——
    前缀缓存只认从头逐字相同的那段。三样都没有时返回 `en` 原文(与旧行为逐字相同)。
    ⭐ #4 B2：译名段随句变 → 放在上一句之后、本句之前；无命中 → 不加。"""
    parts = []
    dom = domain_block(domain).strip()
    if dom:
        parts.append(dom)
    if course_terms:
        parts.append("Course terms: " + ", ".join(course_terms[:40]))
    prev = context[-1] if context else ""
    if prev:
        parts.append(f"Previous sentence (context only, do not translate):\n{prev}")
    zh = term_zh_block(en, term_zh).strip()
    if zh:
        parts.append(zh)
    if not parts:
        return en
    parts.append(f"Current speech (may stop mid-sentence; translate only what is there):\n{en}")
    return "\n\n".join(parts)
