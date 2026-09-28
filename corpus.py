#!/usr/bin/env python3
"""课程语料 —— 每门课「长什么样」，从**上课转录**里长出来。

## 它解决的问题

`classify` 给模型看的「这门课是什么」，现在只有 `glossary/<课号>.txt` 里的 12 条术语。
而 2026-09-28 在作者真实语料上量出来：**真正有辨识度的词在几十小时的讲课转录里** ——

    ECON10770  revenue(31) · surplus(16) · monopoly(8) · utilitarianism(7)
    SOC10020   sociology(71) · urban(25) · sociological(22) · epistemology(18)
    ECON10740  policy(87) · sdgs(44) · citation(19) · journal(19)
    ECON10790  assumption(7) · indifference(5) · inverse(5) · linear(4)

每门课 **19%-56% 的词是它独有的**。对照：`classify.py` 文件头那张表里，
「正文 × 术语表撞词」的精确率只有 **12%** —— 那是拿 184 条人工术语当尺子的结果。
**转录替换了那把尺子。**

## ⚠️⚠️ 一条自净规则（这个模块存在的理由之一）

**只在一节课里出现过的词，不进关键词表。**

作者 2026-09-28 亲口确认：`sessions/` 里有一节是**传错文件**录进去的 ——
10730 门里躺着化学课的 `kinetic / velocity / bond / particles`。
那一节是**孤例** → 它的词 df=1 → **被这条规则自动挡掉**；
而真正的课程词会在多节课里反复出现（`revenue` 在 10770 跨多节、共 31 次）。

⚠️ 这条规则是**相对的**（只看"跨了几节"，不看绝对值）—— 因为
「19 个课次里定不出阈值」（同 `voice.DEFAULT_THRESHOLD = 0.4` 那条「**零背书**」）。
**别在这里再定一个魔法数。**

⚠️ **课次太少时它救不回来**（实测：2 节时 df>=2 剩下的全是开场行政话）
→ 那种课**直接不用语料**，见 `MIN_SESSIONS`。
`keywords()` 因此把「哪些课降级了 + **为什么**」一起报出来（`degraded`），
**别让它和正常结果长得一样**。

## ⚠️ 为什么不建索引、不做嵌入

与 `find.py` 同一条理由（那边实测：2.01 MB 语料全量扫一遍 124.5 ms）。
这里更简单 —— 一次批量归档只算一遍，而且**结果要进 prompt**，
所以真正要控制的是**词表长度**，不是计算量。

## ⚠️⚠️ 泛化：上面那些数字**全都来自一个用户的数据**

作者 2026-09-28 的原话：「**你要考虑用户之间的差异** —— 他们不一定跟我的文件、
命名、上课内容、风格一样。」

已知的差异面至少有四条，第一条会**直接让整条路走不通**：
- ⚠️⚠️ **分词器是纯英文的**（`[a-z][a-z-]*`）。**中文讲课的转录抽出来是 0 个词。**
  这不是"效果差一点"，是这条路对那种用户**根本不适用** ——
  所以它必须**退化成"没有语料"并把原因说出来**，不能静默给个空表。
- 课次数（有的人一门课只录了两节 —— 见 `MIN_SESSIONS`）、讲课风格
  （照着 PPT 念 vs 自由发挥）、命名习惯、录音质量。
- `MIN_SESSIONS = 3` 是**在 5 门课上数出来的**，不是普适常数。

→ **纪律**：本模块任何一处「给不出东西」都**退回一个有意义的档**（调用方退回术语表），
  并把**原因用一句人话报出来**。**静默的空表是这里最坏的失败模式** ——
  它看起来像"这门课没有特征"，而真相可能是"我们根本读不懂这种转录"。

## 分工

本模块**不碰 AppKit、不认识 prompt**。它只回答「这门课有哪些特征词」；
怎么拼进 prompt 是 `classify` 的事，从哪来是调用方的事。
"""
from __future__ import annotations

import collections
import math
import pathlib
import re

# 少于这么多**英文词**的课次不算一节课（空的 / 测试残留 / 录一半中断的）。
# ⚠️ 实测依据：19 个够格课次里，短的那些在"最近邻判课"上接近随机
#    （跑偏组中位 212 词 vs 正确组 1018 词）。
MIN_WORDS = 30

#: 关键词表默认几条。⚠️ 上限来自 **prompt 预算**，不是"越多越好" ——
#: 「描述更长会不会反而变差」**没量过**（正在调研）。
DEFAULT_TOP = 25

#: ⚠️⚠️ **少于这么多节够格课次 -> 这门课不用语料，调用方退回术语表。**
#: 2026-09-28 在作者真实语料上量的 —— 这就是这个数存在的**全部**理由：
#:
#:     10730  4 节 · 951 词 · df>=2 剩 102 (11%)  ← 词表好：constrained/optimal/budget
#:     10740  2 节 ·2243 词 · df>=2 剩 521 (23%)  ← 词表**全是废话**：happens/department/
#:                                                  richard(人名!)/menu/unless
#:     10790  2 节 · 251 词 · df>=2 剩  11 ( 4%)  ← 总共只剩 11 个词
#:
#: 根因是**结构性**的：两节课里"都出现"的词是**开场的行政话**，真正的主题词各讲各的、
#: 只在各自那节出现 → 对 <=2 节的课，**词级规则救不回来**，信息不在那儿。
#: 宁可不给（退回术语表），也不要塞一把 "happens、menu、richard"。
#: ⚠️ 它是**在一个用户的 5 门课上**数出来的，**不是普适常数** —— 换一批数据可能不对。
#:    所以它降级时的行为是"退回术语表 + 说人话"，不是"猜一个更小的数"。
MIN_SESSIONS = 3

_WORD = re.compile(r"[a-z][a-z\-]*")     # ⚠️ **不含撇号** —— 见 `_counts`

# ⚠️ **手写的**填充词表（从这几门课的转录里挑出来的：okay / gonna / sort / kind …）。
#    不是 NLTK 那张通用表 —— 不为一个词表引依赖，而且这张表**看输出就能验**
#    （第一版就是靠看输出才发现缩合词漏进来的）。
#    ⚠️ 但它**不是**主力过滤器：通用**学术**词（function / sample / policy）
#    靠**跨课 IDF** 沉底，不靠这里。这张表只管填充词与口语碎片。
_FILLER = frozenset("""
the a an and or but if of to in on at for with from by as is are was were be been being
that this these those it its you your we our they their he she his her them us so because
when what which who how why not no yes can could would should will shall may might have has
had do does did there here then than about into over under more most less least very just
also only even still yet now one two three four five six seven eight nine ten going gonna
know think want need see look say said get got make made take come well okay right left good
bad thing things people time times part parts way ways like actually really maybe kind sort
lot lots little big small long short same other another each every all some any both few
many much own such nor too let put give given says getting doing having
something anything nothing everything someone anyone everyone
different important work works today always example examples talk next yeah plus
""".split())


def _why_no_sessions(course: str, *, sessions_dir, sessions_of) -> str:
    """这门课为什么给不出词表 —— **一句人话**，给界面直接显示。

    ⚠️ 不去猜"是不是中文"：只说**已知的那件事**（有没有上课记录）。
       猜错了会把用户带沟里（"我明明是英文课"）。
    ⚠️ 原来这里还把每份转录**重读一遍**去数抽出多少英文字符，而那个数**从没进过
       任何判断**，也没进过返回的那句话（2026-09-28 审查指出）→ 整段删掉。
       `_session_tokens` 已经读过同一批文件，那是一次白读。
    """
    if not sessions_of(sessions_dir, course):
        return "还没有上课记录"
    # 文件在、但一节课都攒不出来 —— 要么太短，要么转录本来就不是英文。
    # ⚠️ **两种都说**，不替用户裁决是哪种。
    return "上课记录里的英文太少（要么太短，要么不是英文转录）"


def _counts(text: str) -> collections.Counter:
    """一段英文正文 → 词频。**只留字母词、长度 >= 4、去掉填充词。**

    ⚠️⚠️ **不把撇号当词内字符** —— 第一版写成 `[a-z][a-z\\-']+`，于是
       `it's` / `that's` / `we're` / `don't` **整块**变成一个词，
       完美绕过填充词表（表里只有 `it` / `that` / `we` / `do`），
       结果是 25 个关键词里有 **8 个是缩合词碎片**。
       **缩合词就是两个功能词**，拆开或丢掉都行 —— 这里选丢掉（切在撇号处）。
    """
    low = (text or "").lower()
    return collections.Counter(
        w for w in _WORD.findall(low) if len(w) >= 4 and w not in _FILLER)


def _score(df_in: int, courses_with: int, n_courses: int) -> float:
    """一个词对一门课的**特征度**。

    ⭐ **单位是「跨了几节课」，不是「出现了几次」** —— 这是本模块的核心取舍。
       理由：`blah × 800` 是教授的口头禅，`monopoly × 12` 才是这门课在讲什么。
       一堂课里重复 200 遍的词，不该比横跨三节课的词更像"这门课的特征"。
       ⚠️ 而且它**和自净规则同一个单位**（那条也是按"跨几节"数）——
          整个模块只用一个尺子，不引入第二个概念。

    ⚠️⚠️ **为什么不是 `词频 × idf`**：那样中了两次同一个坑 ——
       缩合词碎片 `it's`（出现几百次）靠 tf 压过 `monopoly`（8 次）；
       换成次线性 `1 + log(tf)` 之后**还是压过**（实测 800 次 vs 12 次：
       blah 5.32 > monopoly 3.82）。**tf 那一路调不回来，只能换单位。**

    `idf` 那半负责**跨课**区分：每门课都有的通用学术词（`function` / `sample`）
    被**压小** —— 那正是 `classify.py` 里四条机械信号全军覆没的根因。
    ⚠️ **压小，不是压到 0**（2026-09-28 审查纠了一次文档）：这半是
       `log(1 + N/df)` 那个**平滑**形式，`df == N` 时它是 `log 2 ≈ 0.69` 而不是 0。
       5 门课里只有 1 门有的词拿 `log 6 ≈ 1.79` → 通用词被压到约 1/2.6。
       真要归零得用 `log(N/df)`，**但那会改动输出**（词表是实测过的），所以改的是文档。
    ⚠️ 课少时 idf 很粗（5 门课只有 3 档），**别把它当成精确的判别力**。
    """
    return df_in * math.log(1 + max(1, n_courses) / max(1, courses_with))


def _session_tokens(course: str, *, sessions_dir, sessions_of) -> list:
    """这门课**每节课**的词频表。读法唯一的定义点在 `obsidian_writer`。"""
    out = []
    for p in sessions_of(sessions_dir, course):
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        c = _counts(_english(text))
        if sum(c.values()) >= MIN_WORDS:      # 空的 / 测试残留 / 录一半的，不算一节课
            out.append(c)
    return out


def _english(text: str) -> str:
    """一份会话文件 → **英文正文**。

    ⚠️⚠️ **读法只此一处：`obsidian_writer.ObsidianWriter._parse`。**
       会话 Markdown 是**三方共享契约**（`obsidian_writer` 写它、`_parse` 读回它、
       `cl last` 用 grep 匹配它 —— 见 CLAUDE.md）。在这里自己写一遍正则
       = 第四份读法，而格式一改它**静默**读出空串。`find.py` 用的是同一个来源
       （它从 `_OW` 取 `_TS` / `_FIELDS`）。
    ⚠️ 拿不到 `obsidian_writer` 时返回空串（判据只用合成语料，不装 PyObjC 也能跑）。
    """
    try:
        import obsidian_writer as _OW
    except Exception:                                         # noqa: BLE001
        return ""
    try:
        return " ".join(e.get("en") or "" for e in _OW.ObsidianWriter._parse(text))
    except Exception:                                         # noqa: BLE001
        return ""


def keywords(known, *, sessions_dir, top: int = DEFAULT_TOP,
             sessions_of=None) -> tuple:
    """`{课号: [关键词…]}` + `{课号: 降级原因}`。**纯读。**

    打分见 `_score()`：**跨了几节课 × 跨课 IDF**。
    ⭐ **通用学术词靠 IDF 自动沉底**：每门课都有的词被压到约 1/2.6
       （不是归零 —— 公式是平滑形式，见 `_score()` 里那条更正）。
       那正是 `classify.py` 里四条机械信号全军覆没的根因（判别词全是通用学术词）。
    ⚠️ 打分那半**不用词频** —— 理由写在 `_score()` 里，那是一个踩了两次的坑。

    ⚠️ **入围门槛（自净规则）**：一个词必须在这门课的 **>= 2 节**里出现过。
       传错的那一节是孤例，它的词进不来 —— 见模块头。
    ⚠️ **够格课次少于 `MIN_SESSIONS` 节就不给词表**：实测 2 节时"两节都出现"的词
       全是开场的行政话（`happens` / `department` / 人名），**词级规则救不回来**。
       课号连同原因一起进 `degraded`（**降级要说出来**，别让它和正常结果长得一样）。
    ⚠️ 课少时 `_score` 里那条 `df_in >= 2` 也必然筛空 —— 两件事指向同一个结论。

    返回 `({课号: [词…]}, {课号: 降级原因})`。⚠️ 返回值是**两个**，不是猜的那个 ——
    调用方要么用降级信息，要么显式丢掉。⚠️ 第二个是**字典不是集合**（2026-09-28
    审查纠了一次文档：原来写成 `{降级的课号}`，读起来像个 set）。
    """
    from courses import session_files as _default_sessions_of
    sessions_of = sessions_of or _default_sessions_of
    known = list(known)

    per = {c: _session_tokens(c, sessions_dir=sessions_dir, sessions_of=sessions_of)
           for c in known}
    # 跨课 df：只要这个词在**这门课至少一节**里出现过，这门课就算一个
    in_course = collections.Counter()
    for c, cs in per.items():
        for w in set().union(*[set(x) for x in cs]) if cs else set():
            in_course[w] += 1

    out, degraded = {}, {}
    for c, cs in per.items():
        if not cs:
            # ⚠️⚠️ **这门课拿不出词表**，而**三种完全不同的原因挤在这一个分支里**：
            #    · 还没录过课（正常的冷启动）
            #    · 录了但都很短（中断 / 测试残留）
            #    · ⭐ **转录不是英文** —— 分词器是 `[a-z][a-z\-]*`，中文讲课抽出来是 0 个词。
            #      换一个用户就会撞上，而它**必须说出来**：否则用户会以为
            #      "我的课没有特征词"，而真相是"我们读不懂这种转录"。
            #    ⚠️ 三种的处理相同（退回术语表），**但说出来的话不该相同**。
            out[c] = []
            degraded[c] = _why_no_sessions(c, sessions_dir=sessions_dir,
                                           sessions_of=sessions_of)
            continue
        if len(cs) < MIN_SESSIONS:
            out[c] = []
            degraded[c] = f"只有 {len(cs)} 节够格课次，词表不可靠"
            continue
        df_in = collections.Counter()
        for x in cs:
            for w in x:
                df_in[w] += 1
        scored = [(w, _score(df_in[w], in_course[w], len(known)))
                  for w in df_in if df_in[w] >= 2]
        scored.sort(key=lambda t: (-t[1], t[0]))     # ⚠️ 平手按词排 —— 否则结果不可复现
        out[c] = [w for w, _ in scored[:top]]
    return out, degraded


def describe(words) -> str:
    """一串词 → 给模型看的那一行。⚠️ 空表返回**空串**（不是"无"之类的占位）——
    调用方要能一眼看出"这门课没有语料"，好退回术语表。

    ⚠️ **本模块只有这一个"出口形状"**（`keywords()` 给词表，`describe()` 拼成串）。
       原来还有一个 `build()` 直接返回拼好的串 —— 删掉了：它和 `keywords()`
       **返回类型不同却长得像**，我在一个测试文件里就把它当词表 `set()` 了两次
       （于是断言的是一堆单字母）。**两个形状就是两个脚枪。**
       ⚠️ 想让调用方少写一行 dict 推导，不值得换来这个。
    """
    return "、".join(words or ())


