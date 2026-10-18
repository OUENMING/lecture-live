"""笔记术语表的口径：**只留本课真实用到的学科术语**（2026-10-17 step 1 止血）。

为什么单独一个模块（而不是塞进 `obsidian_writer._glossary` 那一行）：
写这份笔记的那条路只做「按转录命中 → 打印」，而审计（`notes/report.md` §1.5-④）
逐份坐实了它的三种脏数据 —— **全是运行时查词缓存（`term_notes_auto.json`）进来的**：

| 类型 | 实例（审计原文） | 为什么必须挡 |
|---|---|---|
| 人名 | `Masha`「动画《玛莎和熊》里的小女孩」 | 同学的名字不是术语；百科式解析零学习价值 |
| 口语/常识词 | `Interesting`「英语常用形容词…」/ `Irish` / `Department` | 读者会学会「这张表不用看」 |
| 错语境义 | `USC` →「南加州大学」（本课是 Universal Social Charge） | **同一条术语表教错了一个概念**（B1），比没有更坏 |

⭐ 判据是**「本课」**，不是「这个词难不难」：
- 本课术语表（`glossary/<课号>.txt`，`prep` 从课件里长出来的）里的词 —— 一定留；
- 人工/构建的术语库（`term_notes.json`）里的词 —— 留（它就是按「中英对照价值」分级的）；
- **运行时自动缓存** —— 要过一道 `verify`：要么在本课术语表里，要么在本课**课件材料**里
  真实出现过（`materials.Materials.verify`，2026-10-17 step 2 接上）。
  查不到出处的**不进表**，但**记账**（`Selection.dropped`）—— 静默丢掉是本仓库
  反复咬人的那一类失败（「音译没写好，用户以为写进去了」同形）。

⚠️ 2026-10-17 之前这里没有门：`TermNotes.match()` 返回的就是显示的内容，
   而自动缓存是**跨课共享**的（一学期八门课共用一个 `term_notes_auto.json`），
   于是「某门社会学课查到的人名」会跟着 Oracle 出现在经济数学课的笔记里。
"""
from __future__ import annotations
import dataclasses
import re

#: 来源标签（进笔记那一列，读者据此知道这条解析是哪来的）。
#: ⚠️ 本课术语表（`glossary/<课号>.txt`）**不产生行** —— 它只有词面、没有解析，
#:    在 `select()` 里当 `verify` 的判据用（"本课术语表里有这个词" = 语境核对通过）。
SRC_CURATED = "术语库"
SRC_AUTO = "自动缓存"
SRC_LECTURE = "本课"

#: 「常识词」的小名单。⚠️ 刻意**保持小**：它只收审计实测出现过、且**任何课都不该
#: 解释**的那种词（日常英语 + 办公软件 + 温度单位）。真正的大头由下面两条规则兜：
#: ① 单 token 的普通词（`_looks_like_vocabulary`，复用 `build_notes` 的判据）
#: ② 自动缓存里**单 token、非缩写**的条目一律要过 `verify`（人名/地名/普通词全落这里）。
#: 名单一旦变长就说明有别的信号没接上 —— 那该去修信号，不是往这里加词。
EVERYDAY = frozenset("""
pdf powerpoint excel word outlook zoom google microsoft windows mac
celsius fahrenheit euro dollar
""".split())

#: 机构 / 平台 / 学位 / 职位 / 课型 / 日常词 —— **可维护的小表**（作者第六轮第 4 条）。
#: 判据是「这门课（以及任何一门课）都不需要解释它」：`Brightspace` 是学校平台、
#: `PhD` 是学位、`tutorial` 是课型、`director` 是职位。
#: ⚠️ 为什么"出现过"挡不住它们：它们**真的**出现在转录与课件里（实测 ECON10740 的
#:    材料里 `Brightspace` 23 次、`director` 1 次），所以必须有这张表。
#: ⚠️ 表要**保持小**、按需增补（作者点名的 8 个 + 同类里最常撞见的那几个）。
#:    名单一旦变长，说明有别的信号没接上 —— 那该去修信号，不是继续往这里加词。
GENERIC = frozenset("""
brightspace moodle canvas blackboard panopto turnitin
phd masters master bachelor undergraduate postgraduate
tutorial tutorials seminar seminars lecture lectures lab workshop
director professor lecturer tutor teacher
evidence ai conclusion introduction summary overview
campus university college school module modules semester
assignment assignments exam exams essay essays quiz mcq
slide slides pdf powerpoint excel zoom teams outlook email
""".split())

#: 「口语/元话语词」：课堂上高频，但**从来不是术语**。进表的后果同「Interesting」。
_DISCOURSE = frozenset("""
yeah yep ok okay right well now so just very really quite actually basically
interesting important difficult easy good bad nice great fine sure maybe perhaps
anything something everything nothing everyone anybody somebody nobody
""".split())

#: 入库的两条数量纪律（作者第六轮第 4 条；判据在 `note_glossary.candidates`）。
#: ⚠️ 放这里而不是 `note_glossary`：它们是**口径**，与 `EVERYDAY`/`GENERIC` 同族。
MIN_HITS = 2          # 词要在本课转录或课件里出现过 **≥2 次**（一次多半是顺口一说）
MAX_TERMS = 15        # 每节入库上限（入库只追加，宁缺勿滥）

#: 人物头衔/称谓开头的短语 —— 自动缓存里 `Mister X` 这类。
_TITLE_RE = re.compile(r"^(mr|mrs|ms|dr|prof|professor|sir|lady|lord)\.?\s", re.I)


def _looks_like_acronym(term: str) -> bool:
    """全大写 3 字母以上 —— `USC` / `GNI` / `PDF` 那种。

    ⚠️ 与 `build_notes.detect_proper_nouns` 同一口径（≥3 挡掉 `DW`/`OK`）：
       两字母的"缩写"在实际数据里几乎全是噪声。
    """
    t = (term or "").strip()
    return len(t) >= 3 and t.isalpha() and t.isupper()


def _common_word_signal(term: str) -> str:
    """普通英语词信号。返回原因，没命中返回空串。

    ⚠️ 复用 `build_notes` 的判据（`COMMON_WORDS` / `FUNCTION_FIRST` / 单音节），
       **不另写一份高频词表** —— 两份词表迟早漂移，而漂移是静默的。

    ⚠️ DB 审查 D-20：**表里/口语名单要先判**。`Interesting` / `Euro` 是句中大写的单
       token，若让下面「大写单 token = 专有名词」的提前 return 抢在前面，它们会被当
       专有名词**放行**（`SRC_AUTO` 被 `drop_reason` 更早的分支兜住所以测不到；而
       `本课`/`术语库` 两条来源会把它们**只追加**地写进术语表 —— 正是本模块要挡的）。
    """
    from build_notes import _looks_like_vocabulary, COMMON_WORDS
    words = (term or "").split()
    if not words:
        return ""
    low = term.strip().lower()
    if low in _DISCOURSE or low in EVERYDAY:
        return "口语/常识词"
    # ⚠️ 缩写**不走**「普通词汇否决」：那个判据是给英文单词设计的，而
    #    `_syllables("usc") <= 1` 恒成立 → 会把 `USC`/`GNI` 全判成常识词。
    #    `build_notes.detect_proper_nouns` 早就用 `not phrase.isupper()` 挡过同一个坑。
    #    缩写的判据是**语境核对**（`verified`），不是词形。
    if _looks_like_acronym(term):
        return ""
    # ⚠️ **句中大写的单 token = 专有名词**，不走"普通词汇否决"：
    #    `_syllables("coase") <= 1`（`oa`+`e` 收成 1）会把它判成单音节常识词 ——
    #    而 `Coase` / `Renshaw` 正是这类课要收的词（实测：`Coase` 被挡下，
    #    `tests/test_live_terms.py` 的红）。`Interesting` 那种真常识词走的是上面的
    #    `_DISCOURSE`/`EVERYDAY`（**先判**），不会因为这条漏网。
    if len(words) == 1 and term[:1].isupper() and low not in COMMON_WORDS:
        return ""
    if _looks_like_vocabulary(term.strip(), words):
        return "常识词（普通英语词汇）"
    if len(words) == 1 and low in COMMON_WORDS:
        return "常识词（高频词）"
    return ""


#: F8-f（作者点名）：**单单词**里的"泛词"——任何一门课都会出现、出现也不说明是术语。
#: 运行环境没有词频库（wordfreq / zipf）→ 声明这张**小而保守**的表（作者点名的四个 + 同族），
#: 只吃单单词（`marginal cost` 这种多词短语是术语，不走这条）。
#: 例外由**调用方**管：术语库里真有这个词就留（`unless the glossary has them`，
#: 见 `obsidian_writer._in_term_store` 的调用点）。
#: 单单词泛词 —— 两层（F9c 域中立化）：
#: · `ALWAYS`：任何学科里单独出现都不是术语 → 一律算泛词；
#: · `UNLESS_VERIFIED`：在**别的学科里是正词**（化学的 substitution 取代反应、
#:   derivative 衍生物；社会学的 marginal……）→ 只有"本课材料里真出现过"才保留。
TOO_COMMON_ALWAYS = frozenset("""
minimum maximum
""".split())
TOO_COMMON_UNLESS_VERIFIED = frozenset("""
substitute substitution marginal derivative
""".split())


def too_common(term: str, *, verified: bool = False) -> bool:
    """单单词泛词判据（F8-f；F9c 域中立版）。多词短语永远不算（`marginal cost` 是术语）。

    `verified` = 本课材料里真出现过这个词（调用方查 `Materials.verify`）——
    在经济学里 `substitute` 是口头词，在化学里 `substitution` 是正词；
    判据只能看**这门课自己**用不用它，不能拿一份写死的学科表赌。
    """
    t = (term or "").strip().lower()
    if not t or any(c in t for c in " -"):
        return False
    if t in TOO_COMMON_ALWAYS:
        return True
    return t in TOO_COMMON_UNLESS_VERIFIED and not verified


def drop_reason(term: str, *, type_: str = "", source: str = SRC_AUTO,
                verified: bool = False, lecture_term: bool = False) -> str:
    """一条命中该不该进笔记术语表。返回原因 = 丢掉；返回 `""` = 保留。

    ⚠️ **参数只有判据需要的那几样**（词面、条目类型、来源、是否已核对本课语境、
       本课是否真把它当术语）—— 这是一个纯函数，`Selection` 那边只负责记账。

    保留规则（按来源）：
      · `课术语表`：非人名、非口语常识词 → 留；
      · `术语库`（**跨课共享**）：还要 `verified`（本课术语表/课件里出现过）**或**
        `lecture_term`（本课复习层真把它当术语抽出来了）。⭐ F14 ②
      · `自动缓存`：**再**要 `verified` —— 本课术语表或本课课件里真实出现过。
        ⚠️ 这一条是「错语境义」的唯一止血点：`USC` 的缓存解析（南加州大学）
          与本课（Universal Social Charge）**词面完全相同**，只有"本课材料里
          出现的是什么"能把它们分开。
    """
    if not (term or "").strip():
        return "空词条"
    if term.strip().lower() in GENERIC:
        # ⚠️ 放在人名判据之前：`director` 那种词在任何一门课里都不是术语，
        #    不依赖"查得到类型"这一步（类型来自运行时缓存，常常查不到）。
        return "机构/平台/学位/职位/课型/日常词"
    if (type_ or "").lower() == "person":
        return "人名"
    if _TITLE_RE.match(term.strip()):
        return "人名（称谓开头）"
    if source == SRC_AUTO:
        words = term.split()
        if len(words) == 1 and not _looks_like_acronym(term) and not verified:
            return "常识词（单词条、本课无出处）"
        if not verified:
            return "无法核对本课语境"
        return _common_word_signal(term)
    if source == SRC_CURATED:
        why = _common_word_signal(term)          # 口语/常识词这一档理由更具体，先说它
        if why:
            return why
        if not (verified or lecture_term):
            # ⭐ F14 ②：术语库是**全课共享**的一只文件（社会学/经济学的词条也躺在里面），
            #    旧版对 `术语库` 来源**无条件放行** → ECS170（CS 课）的术语表里长出
            #    `structure — 社会结构，影响行为的规则和制度 · 术语库`、
            #    `variable — 会变化的量，比如收入、年龄` —— 那是**别的课的定义**。
            #    纪律：本课没有把它当术语用（材料/术语表没出现过，复习层也没抽出来）
            #    → 一律不显示；被挡下的进页脚记账（不静默丢）。
            return "跨课术语库（本课没把它当术语用）"
        return ""
    return _common_word_signal(term)


@dataclasses.dataclass(frozen=True)
class Hit:
    """进笔记的一行：`term — gloss　[ts]`。"""
    term: str
    gloss: str
    ts: str = ""
    source: str = ""


@dataclasses.dataclass(frozen=True)
class Dropped:
    term: str
    reason: str


@dataclasses.dataclass(frozen=True)
class Selection:
    """`hits` = 进表；`dropped` = 被挡下的（**记账，不静默丢**）。"""
    hits: list
    dropped: list

    def reasons(self) -> dict:
        """原因 -> 条数。笔记页脚那一行 `已过滤 5 条（人名 2 · 常识词 3）` 用它。"""
        out: dict = {}
        for d in self.dropped:
            out[d.reason] = out.get(d.reason, 0) + 1
        return out

    def footer(self) -> str:
        """页脚一行。没有过滤发生时**返回空串** —— 常态不占版面。"""
        if not self.dropped:
            return ""
        parts = " · ".join(f"{r} {n}" for r, n in
                           sorted(self.reasons().items(), key=lambda kv: -kv[1]))
        return f"*（已从术语表过滤 {len(self.dropped)} 条非本课术语：{parts}）*"


def select(entries: list[dict], *, match, type_of=None, verify=None,
           max_n: int = 0, lecture_terms=None) -> Selection:
    """转录命中 → 过滤后的术语表。**纯逻辑**（读写全在注入的两个函数里）。

    入参：
      · `entries` —— `obsidian_writer._parse` 的输出（每句 `{ts,en,zh,asr}`）；
      · `match(sentence) -> [(term, gloss, source, type_), …]` —— 命中来源
        （生产是 `obsidian_writer._term_hits`，判据注入替身）；
      · `verify(term) -> bool` —— 语境核对（默认全 False：**没有核对手段时宁愿少显示**
        —— 这条纪律与 `TermNotes` 读不出时的降级方向一致）；
      · `lecture_terms` —— 本课复习层抽出来的术语（小写集合）。⭐ F14 ②：`术语库`
        是跨课共享的，只有本课真把它当术语用（`verify` 或在本集合里）才允许显示。

    ⚠️ 顺序：先出现的先留（`ts` 是最早那句），且**同词只留一条** ——
       同一节课里同一个词会在几十句里命中，不去重这张表就没法读。
    """
    verify = verify or (lambda _t: False)
    type_of = type_of or (lambda _t: "")
    lect = {str(t).strip().lower() for t in (lecture_terms or []) if str(t).strip()}
    seen: set = set()
    kept: list = []
    dropped: list = []
    for e in entries:
        for term, gloss, source in match(e.get("en") or e.get("asr") or ""):
            key = term.lower()
            if key in seen:
                continue
            seen.add(key)
            # ⚠️ 只有 `自动缓存` 与 `术语库` 两条路去问 `verify()`（它们可能来自别的课）；
            #    课术语表就是本课的，不必问。
            if source in (SRC_AUTO, SRC_CURATED):
                why = drop_reason(term, type_=type_of(term), source=source,
                                  verified=verify(term), lecture_term=key in lect)
            else:
                why = drop_reason(term, type_=type_of(term), source=source)
            if why:
                dropped.append(Dropped(term, why))
                continue
            kept.append(Hit(term=term, gloss=gloss, ts=e.get("ts", ""),
                            source=source))
    if max_n > 0:
        # ⚠️ 超额只**截断并记账**，不静默：被截掉的算 dropped（原因 `超出上限`），
        #    读者才知道这张表不是全部（同 `AppendResult.skipped_long` 的取舍）。
        for h in kept[max_n:]:
            dropped.append(Dropped(h.term, "超出上限"))
        kept = kept[:max_n]
    return Selection(hits=kept, dropped=dropped)


def final_rows(sel: "Selection", *, extra: list | None = None) -> list:
    """术语表的**最终行**（唯一定义点）：`extra` 在前、按词面去重、同一 gloss 只留一行。

    ⭐ F8-e（作者点名）：同一个中文（gloss）只留一行 —— 术语库行（`sel.hits`）优先，
       其次取**更短**的英文（`shutdown price` 胜 `shutting down price`）。
       占位 gloss（"（本课术语）"）不参与合并，否则会把一堆词并成一行。

    ⚠️ DB 审查 D-7：**显示**（`render_table`）与**标准拼写**（`obsidian_writer._term_pairs_norm`
       喂给 `note_v2.announce_terms_in_review` 的 `canon`）都必须从**这里**取 —— 两处各
       排一次就会出现「表格里是 `shutdown price`、正文括注却是 `shutting down price`」，
       正是 F7-e/F8-e 要消除的「页面上有两种拼写」。
    """
    rows: list = []
    seen: set = set()
    for h in list(extra or []) + list(getattr(sel, "hits", None) or []):
        if not h.term or h.term.lower() in seen:
            continue
        seen.add(h.term.lower())
        rows.append(h)
    store_terms = {h.term.lower() for h in (sel.hits or [])}
    merged: list = []
    pos: dict = {}
    for h in rows:
        g = (h.gloss or "").strip()
        if not g or g.startswith("（"):
            merged.append(h)
            continue
        cur = pos.get(g)
        if cur is None:
            pos[g] = len(merged)
            merged.append(h)
            continue
        a = merged[cur]
        a_store, h_store = a.term.lower() in store_terms, h.term.lower() in store_terms
        if (h_store and not a_store) or (h_store == a_store and len(h.term) < len(a.term)):
            merged[cur] = h
    return merged


def render_table(sel: "Selection", *, extra: list | None = None) -> list:
    """`Selection` → 笔记里的术语表行（Markdown）。

    `extra` 是**本课讲到的术语**（复习层输出，带中文与时间戳）—— 它们排在最前，
    且与命中项**按词面去重**（课术语表/术语库里的旧条目让位给本课语境）。
    """
    out = []
    for h in final_rows(sel, extra=extra):
        tail = f"　`{h.ts}`" if h.ts else ""
        src = f" · {h.source}" if h.source else ""
        gloss = h.gloss or "—"
        out.append(f"- **{h.term}** — {gloss}{src}{tail}")
    return out
