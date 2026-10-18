"""课上候选词（链路体检 ⭐2）：**已定稿文本**里反复出现的词 → 翻译侧 `extra_terms` 池。

## 为什么要有它

`scratchpad/chain/report.md` §2/§3.2 的实测（6 节真课）：

- 校正层改对的 **215 个词里 89% 不在任何术语表**（192/215）—— 术语表只从**课件**长出来；
- 从转录长出来的那一路（`corpus.keywords`）只够到「动态召回前 3」，**到不了 ASR 那一层**；
- 同一节课里**同一个词反复被听错**（`votes→boats`×11、`cash→catch`×9、`RSI→ESRI`×5）：
  "这节课自己的词"是最大的术语缺口（`report.md` §0-2）。

所以这一层干的事：**上课途中**把已定稿的英文喂进来，捞出"本课反复出现、过了门"的候选词，
交给翻译侧候选池（`translator.select_terms` 的 `dyn` 那一档，槽位数 `MAX_DYNAMIC_TERMS=3`）。

## ⚠️ 门（比"出现 ≥2 次"严得多，理由在 §2.2）

`report.md` §2.2 点名的风险：**词表噪声会被校正层放大** —— `translator.SYSTEM_PROMPT`
明写「音近术语表里的词就改过去」。而**反复出现的错听恰好是反复出现**：
`votes→boats` 里的 `boats` 在一节课里出现 11 次，若按"≥2 次"就收，等于教模型
"boats 是本课术语"——正好把错误的词**固化成真的**。

所以候选必须**长得像术语**（`_looks_term`），四档：
1. **缩写**（全大写 ≥3 字母）：`ESRI`、`GNI`；
2. **句中首字母大写**的单 token（专有名词）：`Coase`、`Renshaw`；
3. **连字符复合词**：`cost-benefit`；
4. **多词短语**（首尾是实词、≥1 个实义词、≤3 词）：`shutdown price`、`cost theory`
   —— 这一档**要过课件复核**（见下）。

**单个小写普通词一律不收**。理由是实测出来的（2026-10-17 用 ECON10790 全课跑）：
`calculate / given / equal / question / values` 全都在课件里出现（习题页满是"calculate…"），
`materials.verify` 对**单词**没有区分度；而"≥2 次 + 在课件里"两条加起来都过不了它们的
半分门槛。放弃它们换来的是池子里不再有动词/题面词 —— §2.2 那两条风险（噪声被放大、
挤掉名额）都指向同一处。代价写在 §`成本`：`planner / qualitative` 这类**单个**小写词
收不到，需要人工往 `glossary/<课号>.txt` 补。

课件复核（`materials.Materials.verify`，§2.3 建议 4 的"同源复核"）**对多词短语是硬条件**：
实测 `understand the question` / `write it like` 这类"句式模板切出来的短语"被它挡下，
而 `shutdown price` / `average cost` / `stationary points` / `second derivative` 全过。
没有课件索引时（用户没归档课件）短语一律不收，只留专有名词/缩写 —— 宁可少收。

另外两条与笔记侧**同源**的门：
- `note_terms.drop_reason`（人名 / 口语词 / 常识词 / 错语境）—— 与笔记术语表同一套判据；
- **作者删过的词不复活**（`deleted` 集合 = 墓碑 − 现有术语表，见 `note_glossary.deleted_terms`）。

## 已知残留噪声（实测，不是没看见）

ECON10790 全课（345 句）跑一遍：过门的 16 条里，领域词占大头
（`average cost` / `second derivative` / `stationary points` / `cost function` /
`shutting down` / `total cost` / `final exam` / `level up production` 这类），
但有两类挡不住：

- **人名**（`Masha` / `George` / `Mark`）：只有在作者的术语库里标过 `person`
  的才过不了 `type_of` 这关；没标过的挡不住。删它的办法是**在术语库里标成人名**
  （那是现成机制），或者把它加进 `glossary/<课号>.txt` 再删掉（墓碑会让它不再回来）。
- **句中被大写的普通动词**（`Solve`）：仓库里没有词性标注，而 `COMMON_WORDS`
  是张 349 词的手表（缺大部分动词），所以"不是常见词"这条判据对它无效。

两条都不致命：池子只进 `select_terms` 的**动态召回**（每句最多 3 条、要按相似度
过 `difflib`），而且总量由上限管着；把它们钉死要等词性/频次信号（`corpus` 的跨课
IDF 是现成的一条路，但那是它自己的接口）。**先说清楚，不装作没有。**

## ⭐ 第五道门：Jev 判「像不像本课术语」（可选；E2 实测）

上面那格残留噪声（点名垃圾 `Anything` / `Interesting` / `UCD` / `Ireland`）有一条补丁：
候选词过一遍 Jev —— 问「**一个一年级学生在这门课里要不要解释这个词**」。
2026-10-18 的离线实验（`scratchpad/jev_exp/report.md` §E2，102 条真候选）：
**AUC(REAL vs JUNK) = 0.976**，点名的 9 条垃圾全排在点名真词之下。

接法是**注入式**的（同 `verify` / `type_of`，`extract` 保持纯函数）：`judge(terms) →
[0–1 | None]`。低于 `JUDGE_MIN_P` 的词进 `rejected`（"判据：不像术语"）；`None`（没答）
= 照留。**没接 judge / 没配 token / 超时 —— 一律等于没装闸门**（词照收，同装它之前）。

## 两条上限（作者 2026-10-17：术语库/抽词**不能越堆越多**）

上下文一多，转录与翻译都变慢（`prep.py` 量过：术语块 36 条 ≈ 597 字符 ≈ prompt 的 23%）。
所以两层各自有上限，且**撞上限的行为必须是"按频次 + 新近度淘汰"而不是"先到先得"**：

| 层 | 上限 | 排序 / 淘汰 |
|---|---|---|
| **课上池子**（本模块 `LivePool`） | `MAX_TERMS`（默认 **40**，`CLASSLIVE_LIVE_TERMS_MAX` 可配） | 按**本课出现句数**（多→少）+ **最后一次出现**（新→旧）排序；满了淘汰计数最少、最旧的 |
| **下节课加载**（`translator.course_term_list`） | `translator.TERM_LOAD_LIMIT`（**60**） | 手写的在前，自动追加的按**日期倒序**（`prep-state.json` 的 `appended`）；超出的是**最旧的自动词** |

⚠️ 两条上限都不碰 `stream_asr._config` 的 budget（那条线由 `chain-fix` 分支改）。

## Soniox 那一头：**改不了**（查证结果，2026-10-17）

Soniox 的 `context`（`terms` / `general` / `text`）是**会话开始时**随 start request 发一次：
WebSocket API 的控制消息只有 `keepalive` 与 `finalize`（官方 API Reference
<https://soniox.com/docs/api-reference/stt/websocket-api>，2026-10-17 读），
Context 页写的是 "providing context **with each session**"
（<https://soniox.com/docs/stt/concepts/context>，2026-10-17 读），
改 context 要**重开会话**（错误码页明确 `Cannot continue request: Please restart the request`）。
→ 所以**课上不改 Soniox**（那要重连、会丢已定稿的在途音频）；这一课的词在**下节课**
   由 `glossary/<课号>.txt` 带上（`note_glossary.append` 收尾时已写进去），走的就是
   `main.py` 给它 `terms=` 的那条既有路。

⚠️ 本模块**不碰 `stream_asr._config`**（那条线正在被 `chain-fix` 改 budget）；
   它只提供纯函数 + 接线点（`main.live_terms_step`）。
"""
from __future__ import annotations
import dataclasses
import re

#: 同一个词至少出现几次才算候选（作者口径：「≥2 次」—— 一次是巧合，两次算反复）。
MIN_COUNT = 2
#: ⭐ 术语闸门（`judge`）的阈值：**低于它 = 砍**。E2 离线实验标定（2026-10-18）：
#: - `AUC(REAL vs JUNK) = 0.976`（102 条真候选，`scratchpad/jev_exp/report.md` §E2）；
#: - 真词最低 0.51（`data analysis`）、垃圾中位 0.155；
#: - 0.35 卡在「点名垃圾里最贵的真垃圾 `Euro` 0.34」和「最便宜的真词 0.51」之间：
#:   `Anything` 0.03 / `Interesting` 0.04 / `UCD` 0.12 / `Ireland` 0.18 全砍，真词零误伤。
#: ⚠️ `Dutch`（0.83）是**已知假阳性**（课上说的是 `Dutch disease`，被切成了单个词）——
#:    那是形状层的锅，这一刀补不了它。
#: ⚠️ 与 `live_summary.DEADLINE_MIN_P` 同一条纪律：**改问法 / 改 state 形状必须重新标定**
#:    （绝对分跨 prompt、跨批大小都会整体平移 —— 官方 `jaggedness #5` 的实测版）。
JUDGE_MIN_P = 0.35
#: 池子上限（默认 40 = "几十条量级"，作者 2026-10-17）。环境变量 `CLASSLIVE_LIVE_TERMS_MAX`
#: 可配 —— 见 `_env_limit()`。⚠️ 撞上限时**不是先到先得**：按"本课出现句数 + 新近度"
#: 淘汰计数最少/最旧的（`LivePool._trim`），且**必须出声**（`report()` 里报淘汰条数）——
#: 静默丢弃是本仓库反复咬人的那类失败。
MAX_TERMS = 40


def _env_limit() -> int:
    """`CLASSLIVE_LIVE_TERMS_MAX` → 上限；没设/不合法 → `MAX_TERMS`（**不抛**）。"""
    import os
    raw = (os.environ.get("CLASSLIVE_LIVE_TERMS_MAX") or "").strip()
    if not raw:
        return MAX_TERMS
    try:
        n = int(raw)
    except ValueError:
        return MAX_TERMS
    return n if n > 0 else MAX_TERMS
#: 每多少句定稿跑一次抽取（不是每句都跑：抽取是 O(句数) 的扫描）。
STEP_SENTENCES = 25

_WORD = re.compile(r"[A-Za-z][A-Za-z'\-]*")
_ACRONYM = re.compile(r"^[A-Z]{3,}$")


def _edge_functions() -> frozenset:
    """短语**首尾**不许出现的词 —— 两张现成的表取并集，不另写一份。

    ⚠️ 为什么是并集（实测）：只用 `build_notes.FUNCTION_FIRST`（那是"标题首词"表）
       时会漏掉 `is/are/at/of/to` —— 一份课上反复出现的句式模板（`is what we look at`）
       会把 **`look at`** 凑成"出现 6 次的短语"塞进池子（判据实测抓到）。
       `materials._STOP` 正是为"高频到没有区分度"写的，两张合起来才盖住。
    """
    from build_notes import FUNCTION_FIRST
    from materials import _STOP
    return frozenset(FUNCTION_FIRST) | frozenset(_STOP)


#: 短语**中间**允许出现的"连接词"（白名单，不是黑名单）。
#: ⚠️ 实测（ECON10790 全课）：不做这一条时池子里一半是**句子片段** ——
#:    `function is concave` / `x is negative` / `calculate the value` / `q equals`。
#:    英语里"名词短语"中间能夹的几乎只有介词，而句子片段中间夹的是 `is/the/a/and`；
#:    用白名单而不是"动词黑名单"：后者要穷举英语动词，只会在漏词时静默变宽
#:    （本仓库的老教训：名单一旦变长，说明有别的信号没接上）。
_MID_LINK = frozenset("of for in on to with by between across per at from".split())


#: 数学/念公式用的词 + 单字母变量。⚠️ 实测（ECON10790，一门数学课）：教授**念代数**
#:    会带出 `squared minus` / `q equals` / `equal to zero` / `Q squared` 这类"短语"，
#:    它们全都过得了课件复核（课件里满是同样的念法）。这些词对**翻译/识别**没有价值
#:    （模型本来就认识），留着只会挤掉真术语的名额 —— 所以要求候选中至少有一个
#:    不在这个集合里的词（`one more unit` 这种就活下来了）。
_MATH_WORDS = frozenset("""
squared cube cubed minus plus equal equals times divided over zero one two three
four five six seven eight nine ten hundred thousand half double twice
""".split())


def _lowinfo() -> frozenset:
    """低信息词：**一个短语至少要有一个 token 不在这个集合里**，否则整条丢掉。

    ⚠️ 它挡的是"框架词拼出来的短语"（`look at` / `part of` / `kind of`），
       而不是领域词：`marginal cost` 里 `marginal` 在外面 → 留（`cost` 在
       `COMMON_WORDS` 里也不影响 —— 判据是"至少有一个"）。
    """
    from build_notes import COMMON_WORDS, FUNCTION_FIRST
    from materials import _STOP
    return frozenset(FUNCTION_FIRST) | frozenset(_STOP) | frozenset(COMMON_WORDS)


@dataclasses.dataclass(frozen=True)
class Candidates:
    """`terms` = 过门的候选；`rejected` =（词，原因）；`trimmed` = 撞上限被截掉几条。

    `stats`：小写词 → `(出现句数, 最后一次出现的句号)`。**给池子淘汰用的** ——
    池子要按"频次 + 新近度"排，而那两个数只有这里算得准（同一遍扫描顺手记）。
    """
    terms: list
    rejected: list
    trimmed: int = 0
    stats: dict = dataclasses.field(default_factory=dict)


def _looks_term(phrase: str, *, verify, position: int) -> bool:
    """形状上像不像术语（`verify` = 课件复核，只对单小写词有意义）。

    `position` = 这个词在本句里的 token 下标（0 = 句首 —— 句首首字母大写不说明它是专有名词）。
    """
    words = phrase.split()
    if not words:
        return False
    if _ACRONYM.match(phrase):
        return True
    if len(words) > 1:
        fn = _edge_functions()
        low = _lowinfo()
        if words[0].lower() in fn or words[-1].lower() in fn:
            return False                 # 首尾是功能词（"part of the" / "look at"）
        if not any(w.lower() not in (low | _MATH_WORDS) and len(w) > 1
                   for w in words):
            return False                 # 全是低信息词/念公式的词（"kind of" / "q equals"）
        if any(mid.lower() not in _MID_LINK for mid in words[1:-1]):
            return False                 # 中间夹了非介词 -> 句子片段（"x is negative"）
        if verify is not None:
            return bool(verify(phrase))  # 课件里真写过这个短语（挡句式模板）
        return all(w[:1].isupper() for w in words)   # 没课件：只认全大写的专有名词短语
    w = words[0]
    if w[0].isupper() and position > 0:
        # 句中大写 = 专有名词（句首大写不算）。⚠️ 三条边角判据都是实测出来的：
        #   · 单字母（`Q`）：变量，不是术语；
        #   · 带撇号（`I'm`）：缩写/口语，不可能有术语形（`_WORD` 会把它整个抓回来）；
        #   · 是常见英语词（`Solve` / `Mark`）：那是"句中被大写的普通词"，不是专有名词。
        # ⚠️ 剩下收的就是人名/地名/品牌这类**只有作者能删**的词（判据见 `type_of`：
        #   作者在术语库里标了 `person` 的就过不去；没标过的挡不住 —— 已知局限）。
        if len(w) <= 1 or "'" in w:
            return False
        return w.lower() not in (_lowinfo() | _MATH_WORDS)
    if "-" in w and len(w) >= 6:
        return True                      # 连字符复合词：cost-benefit / two-thirds
    return False                         # 单个小写词：一律不收（理由见模块头「成本」）


def extract(finals: list, *, verify=None, deleted=(), known=(),
            min_count: int = MIN_COUNT, limit: int = MAX_TERMS,
            type_of=None, judge=None) -> Candidates:
    """已定稿句子 → 候选词。**纯函数**（`verify` / `type_of` / `judge` 都是注入的判据）。

    计数单位是"**出现过的句子数**"而不是 token 总数 —— 同一句里连说三遍只是重复，
    跨句复现才说明它是这节课的词。

    ⭐ `judge`（可选，E2 那层）：`judge(terms) -> [0–1 | None]`。低于 `JUDGE_MIN_P`
    的丢进 `rejected`（"判据：不像术语"）、`None`（没答）照留。**不接 = 与装它之前逐字一样。**
    """
    import note_terms as nt
    fn = _edge_functions()
    seen_in: dict = {}                 # 词 -> 出现在几句里
    first_seen: dict = {}              # 词 -> 第一次出现的位置（保序）
    last_seen: dict = {}               # 词 -> 最后一次出现在第几句（新近度）
    shape_ok: dict = {}                # 词（小写）-> 有没有哪一次形状过关
    shape_cache: dict = {}             # (原样, 是否句中) -> `_looks_term` 结果
    for si, sentence in enumerate(finals or []):
        toks = _WORD.findall(sentence or "")
        lows = [t.lower() for t in toks]
        here: set = set()
        for i in range(len(toks)):
            for n in (1, 2, 3):
                if i + n > len(toks):
                    break
                gram = " ".join(toks[i:i + n])
                # 中间可以夹功能词（cost of living），首尾必须是实词 —— 见 `_looks_term`
                if n > 1 and (lows[i] in fn or lows[i + n - 1] in fn):
                    continue
                if len(gram) > 48:
                    continue
                # ⚠️ 判形状的缓存按「原样大小写 + 是否句首」分开记：`Coase` 第一次出现在句首
                #    被判否，不能连累后面句中的 `Coase`（2026-10-18 合并检查抓到）。
                ck = (gram, i > 0)
                if ck not in shape_cache:
                    shape_cache[ck] = _looks_term(gram, verify=verify, position=i)
                ok = shape_cache[ck]
                shape_ok[gram.lower()] = shape_ok.get(gram.lower(), False) or ok
                if not ok:
                    continue
                if gram.lower() not in first_seen:
                    first_seen[gram.lower()] = (si, gram)
                here.add(gram.lower())
        for k in here:
            seen_in[k] = seen_in.get(k, 0) + 1
            last_seen[k] = si

    known_l = {str(x).lower() for x in (known or ())}
    deleted_l = {str(x).lower() for x in (deleted or ())}
    terms, rejected = [], []
    for k, cnt in sorted(seen_in.items(), key=lambda kv: first_seen[kv[0]][0]):
        if cnt < min_count:
            continue
        word = first_seen[k][1]
        if k in known_l:
            rejected.append((word, "已在术语表/池子里"))
            continue
        if k in deleted_l:
            rejected.append((word, "作者删过"))
            continue
        why = nt.drop_reason(word, type_=(type_of(word) if type_of else ""),
                             source=nt.SRC_LECTURE)
        if why:
            rejected.append((word, why))
            continue
        terms.append(word)
    # ⚠️ 子短语判据要把"**已经进池的**长短语"也算上：池子里有 `marginal revenue` 时，
    #    这一轮抽出来的 `revenue` 只是它的碎片 —— 它之所以出现在候选表里，正是因为
    #    `known` 把长短语滤掉了（实测：不补这一句，`Revenue` 会进池并**挤掉**长短语）。
    _extra = [first_seen[k][1] for k in known_l
              if k in shape_ok and shape_ok[k] and k in first_seen]
    _kept = {w.lower() for w in _drop_subphrases(terms + _extra)}
    terms = [w for w in terms if w.lower() in _kept]
    if judge is not None and terms:
        # ⭐ 第五道门（E2）：判据分数 < `JUDGE_MIN_P` 的砍掉（`None` = 没答 → 留）。
        #    ⚠️ 放在子短语合并**之后**：只判极大短语 —— 判据不用为碎片花钱，
        #       被砍的也是个完整的词（子短语本来就已经被合并掉了）。
        terms = _drop_by_judge(terms, judge, rejected)
    trimmed = max(0, len(terms) - limit)
    stats = {k: (c, last_seen.get(k, -1)) for k, c in seen_in.items()}
    return Candidates(terms=terms[:limit], rejected=rejected, trimmed=trimmed,
                      stats=stats)


def _drop_subphrases(terms: list) -> list:
    """**极大 n-gram**：`shutting down price` 在池里时，丢掉它的子短语。

    ⚠️ 实测（ECON10790 全课）：不做这一步时同一个东西会以四种长度各占一个名额
       （`shutting` / `down price` / `shutting down` / `shutting down price`），
       池子的一半被自己吃掉。判据是**连续 token 子序列**（不是字符串包含 ——
       `cost` 不该被 `cost of living` 顺手带走）。
    """
    keys = [(w.lower().split(), i) for i, w in enumerate(terms)]
    out = []
    for words, i in keys:
        if any(j != i and len(other) > len(words)
               and _is_subseq(words, other) for other, j in keys):
            continue
        out.append(terms[i])
    return out


def _drop_by_judge(terms: list, judge, rejected: list) -> list:
    """`judge(terms) -> [0–1 | None]`：低于 `JUDGE_MIN_P` 的进 `rejected`，其余照留。

    ⚠️ **fail-soft 有两层**：判据抛异常、或返回的列表比 `terms` 短 → 缺的都当"没答"（留）
       —— 闸门坏了 = **没装闸门**，绝不许反过来把候选词带走（同 `verify` 懒加载那条纪律：
       建不起来只少收一类词，不挡上课）。
    ⚠️ 认不出数字的值（`None` / 形状不对）也当"没答"：`None`（没拿到）与 `0.0`（真判了 0）
       是两件事，**只有数字才参与比较**。
    """
    try:
        ps = list(judge(terms))
    except Exception:                                         # noqa: BLE001
        return terms
    out = []
    for i, w in enumerate(terms):
        p = ps[i] if i < len(ps) else None
        try:
            v = None if p is None else float(p)
        except (TypeError, ValueError):
            v = None
        if v is not None and v < JUDGE_MIN_P:
            rejected.append((w, "判据：不像术语"))
            continue
        out.append(w)
    return out


def _is_subseq(small: list, big: list) -> bool:
    it = iter(big)
    return all(w in it for w in small)


class LivePool:
    """课上候选词池。**不是纯函数**（要记住"上次跑到第几句"与"池子里现在有什么"）。

    ⚠️ 线程：只有在**定稿线程**里被调（`main._emit`），所以内部不加锁 ——
       与 `term_stats` 同一个约定；真要跨线程用，先想清楚谁写谁读。

    ⭐ 上限（作者 2026-10-17）：`limit`（默认 `MAX_TERMS`，`CLASSLIVE_LIVE_TERMS_MAX` 可配）。
       撞上限时**按本课出现句数 + 新近度淘汰最少的/最旧的**，不是先到先得 ——
       这样"这节课真正反复说的词"一定留得住，被淘汰的是那句提过一次的。

    ⭐ 术语闸门（E2，可选）：`judge` 原样传给 `extract` —— 只影响"哪些词算候选"，
       不影响池子容量/淘汰。**缓存与超时不在这一层**（那是调用方的事，见
       `main.make_term_judge`）；这一层只保证"调用方给什么判据就用什么"。
    """

    def __init__(self, initial=(), *, verify=None, verify_factory=None,
                 deleted=(), known=(), type_of=None, min_count: int = MIN_COUNT,
                 limit: int | None = None, step: int = STEP_SENTENCES, judge=None):
        self._pool: list = []
        #: 小写词 -> {"count": 本课出现句数, "last": 最后一次出现的句号,
        #:            "first": 进池顺序（淘汰的**最后**一道 tie-break）}
        self._stat: dict = {}
        self._n_seen = 0                 # 上次抽取时定稿了多少句
        self._n_total = 0
        self._verify = verify
        self._verify_factory = verify_factory
        self._deleted = set(deleted or ())
        self._known = {str(x) for x in (known or ())}
        self._type_of = type_of
        #: ⭐ 第五道门（E2）：术语判据，原样传 `extract` —— **缓存 / 超时是调用方的事**
        #:（`main.make_term_judge`）。这里只是把它存住。
        self._judge = judge
        self._min_count = min_count
        self._limit = _env_limit() if limit is None else max(0, int(limit))
        self._step = max(1, int(step))
        self._trimmed = 0                # 撞上限被淘汰的条数（出声用）
        self._evicted: list = []
        self._rejected: list = []
        self._seq = 0
        self.add(initial, corpus=True)   # 起步就把 `corpus` 的转录词放进来（与今天同池）

    # ---- 池子 ----
    def terms(self) -> list:
        """池子内容，**按相关度排**（本课出现多 → 少，新 → 旧）。

        ⚠️ 顺序**有意义**：它经 `merge_terms` 进 `select_terms` 的候选池，
           而那一档是"先按相似度截断再取前 `MAX_DYNAMIC_TERMS` 条"——
           平手时在前的先被看到。
        """
        return sorted(self._pool, key=self._rank_key)

    def _rank_key(self, w: str):
        """**排在前面的更该留**（`terms()` 用它排序）：多 → 少、新 → 旧、先 → 后。

        ⚠️ 第三个符号（`-first`）对**起步那批**（`add(corpus=True)`，`first` 是负数）
           刚好就是"corpus 打分从高到低"——见 `add` 的说明。
        """
        st = self._stat.get(w.lower()) or {"count": 0, "last": -1, "first": 0}
        return (-st["count"], -st["last"], -st["first"])

    def _evict_key(self, w: str):
        """淘汰**取 `min`**：少 → 旧 → 进池更早的（含"corpus 里排最后的"）先走。

        ⚠️ 两条 key 每个符号都相反，**不能共用** —— 第一版就是复用 `_rank_key`
           再 `min(...)`，结果选出来的是**最该留的那个**（实测：count=4 的
           `Elasticity Curve` 被踢掉，留下 count=2 的 `Sunk Cost`）。
        """
        st = self._stat.get(w.lower()) or {"count": 0, "last": -1, "first": 0}
        return (st["count"], st["last"], st["first"])

    def add(self, words, stats: dict | None = None, corpus: bool = False) -> list:
        """直接往池子里放词（起步那批 / 抽取结果）。**去重按 lower**（同 `merge_terms`）。

        `stats`（可选）= 抽取器给的 `{小写词: (句数, 最后句号)}`，用来给新词记上
        频次/新近度；没有的（起步那批）记 0 —— 它们是**上一次课**的词，
        被淘汰的顺序本来就该排在"这节课说过的词"之后。

        `corpus=True` = 起步那批（**上一次课的转录词，本身已经按 corpus 打分排好**）：
        它们的 `first` 记成**负数递减**，于是①同分时保持 corpus 的先后（`terms()` 的
        第三项小者在前 = `-0 > -1 > -2`）；②淘汰时（`-first` 大者先走）**先掉的是
        corpus 里排在最后的那个**，不是它排在头名的词。没有这一格，起步那批里
        最相关的词会第一个被踢出去（那正是 `corpus` 打分想避免的）。
        """
        have = {w.lower() for w in self._pool}
        new = []
        for w in words or ():
            s = " ".join(str(w).split())
            k = s.lower()
            if s and k not in have and k not in self._known:
                have.add(k)
                seq = -len(new) - 1 if corpus else self._seq
                new.append(s)
                cnt, last = (stats or {}).get(k, (0, -1))
                self._stat[k] = {"count": cnt, "last": last, "first": seq}
                if not corpus:
                    self._seq += 1
        self._pool.extend(new)
        return new

    # ---- 主入口 ----
    def update(self, finals: list) -> list:
        """已定稿文本 → 新进池的词。**没到步长就返回空**（便宜到可以每句调）。

        ⚠️ 给 `extract` 的 `limit` 是个**够大的数**：它的 `limit` 是"按**首次出现顺序**
           截断"（先到先得）—— 那是错的口径，池子的淘汰才按频次/新近度
           （`_trim`）。两处都截等于**两套口径打架**，所以这里只让它算。
        """
        self._n_total = len(finals or [])
        if self._n_total - self._n_seen < self._step and self._n_seen:
            return []
        self._n_seen = self._n_total
        if self._verify is None and self._verify_factory is not None:
            try:
                self._verify = self._verify_factory()
            except Exception:                                    # noqa: BLE001
                self._verify = None                  # 课件索引拿不到 = 少收一类词，不挡上课
        got = extract(finals, verify=self._verify, deleted=self._deleted,
                      known=self._known | {w.lower() for w in self._pool},
                      min_count=self._min_count, limit=10 ** 6,
                      type_of=self._type_of, judge=self._judge)
        self._rejected = got.rejected
        # ⚠️ 池子里的老词也要跟着这节课走：它这节课又被说了 5 次 → 计数变 5、
        #    新近度变新，淘汰顺序随之推后。**不更新这一格，淘汰就是按出生顺序**——
        #    正好是作者点名不要的那种（"淘汰最旧/最少"要的是这节课的数据）。
        for k, (cnt, last) in (got.stats or {}).items():
            st = self._stat.get(k)
            if st is not None and cnt > st["count"]:
                st["count"], st["last"] = cnt, last
        new = self.add(got.terms, stats=got.stats)
        gone = {w.lower() for w in self._trim()}
        return [w for w in new if w.lower() not in gone]   # 进池即被淘汰的不算「新进池」

    def _trim(self) -> list:
        """超上限时淘汰：**计数最少 → 最旧 → 最早进池**。返回这次淘汰掉的词。"""
        out: list = []
        while self._limit and len(self._pool) > self._limit:
            victim = min(self._pool, key=self._evict_key)
            self._pool.remove(victim)
            self._stat.pop(victim.lower(), None)
            out.append(victim)
            # 被淘汰的词下一轮抽取又会被捞回来再淘汰：账上只记一次（不然 `report()` 越报越多）
            if victim.lower() not in {e.lower() for e in self._evicted}:
                self._evicted.append(victim)
                self._trimmed += 1
        return out

    def evicted(self) -> list:
        """被淘汰过的词（**记账**：`report()` 要把它报出来，不许静默）。"""
        return list(self._evicted)

    def report(self) -> str:
        """给启动行/收尾的一行人话（**没有变化也要能说"没有"** —— 不许静默）。"""
        if not self._pool:
            tail = "，%d 条撞上限被淘汰" % self._trimmed if self._trimmed else ""
            return f"💬 本课候选词：暂无（还没到抽取步长或全部没过门）{tail}"
        tail = f"，{self._trimmed} 条按频次/新近度淘汰" if self._trimmed else ""
        return f"💬 本课候选词：{len(self._pool)} 个进翻译候选池（上限 {self._limit}）{tail}"
