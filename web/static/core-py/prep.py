"""桌面 `prep.py` 里**不碰文件系统、不碰锁**的那半，**由 `web/scripts/sync-core.py` 按名字生成，勿手改**。

带进来的是「候选术语」这条判断链本身：提示词 `SYS_EXTRACT` / `_prompt()`（kind 说明从
`extract.KIND_MEANING` 生成）、`_extract_user`（拼 user 段，含重复行清单）、`_chunks`（按页分块）、
`_verify` / `_verify_folded`（术语得在原文里出现）、`_term_stats` / `_rank`（确定性排序）。

没带进来：

- `append_terms` / `remove_terms` / `restore_lines` / `_course_write_lock` / `_atomic_write` ——
  写 `glossary/<课号>.txt` 与锁，浏览器里没有「只追加的文件」。
- `_archive` / `course_glossary_path` / `_domain_prior` / `_save_state` / `_notes_count` ——
  `~/.classlive/` 的路径、`translator.course_title`、`store`（墓碑）。
- `prepare()` / `main()` —— 装配层（CLI 与面板的适配器）。网页版的装配在
  `web/src/lib/prep/` 的 TS 里，进度、失败清单都由那边出。

⚠️ **域名先验（`domain`）网页版拿不到**（它来自桌面术语表首行），所以 `_extract_user(course, g)`
   不带第三个参数调 —— 那一段提示词就不出现，模型按课程名判领域。
"""
from __future__ import annotations

import collections
import re
import typing

MAX_TERM_TOKENS = 5

REPEAT_PAGE_RATIO = 0.4

MAX_CANDIDATES_PER_CALL = 60

SPREAD_MULT_LO = 0.95
SPREAD_MULT_HI = 1.05
TITLE_MULT = 1.1

CHUNK_CHARS = 30_000

DEFAULT_MAX_AUTO = 40
#: 术语表总条数上限 —— **实测定的**：课程术语**每句全量注入**，所以它直接是
#: 每句的 prompt 成本。实测 36 条 = 597 字符（占整条 prompt 23%），
#: 120 条 = 1544 字符（占 **60%**）。取 60 ≈ 1000 字符，**刚好压在
#: system prompt（1358 字符）之下** —— 这是算出来的线，不是拍的。
DEFAULT_MAX_TOTAL = 60

SYS_EXTRACT = """你在为一门大学课程整理**术语候选表**，供课堂实时字幕的翻译环节使用。

判断标准是**中英对照价值**，不是"这个概念难不难"：
- 要：这门课的领域术语、反复出现的关键概念、学生需要中英对照才听得懂的词
- 不要：教务/行政词（module、lecture、workshop、deadline、reading week）、课程编号、
  以及非专业人士早就知道的常识词（welcome、everyone、yeah、four）

## 输入长什么样
每行是 `[页号] (kind) 文本`。kind 的含义：
__KINDS__

## ⚠️ 只收**这门课**的词
输入开头会给出这门课的**课程名 / 领域**，每段正文前面还会给出**它来自哪份文件**（文件名）。

⚠️ **文件名是一条独立证据，要用上**：它常直接写着这是哪一类材料 ——
`Library session`（图书馆检索培训）、`Orientation`、`Final_presentation`、`Guest lecture`。
正文里这些材料的用词看起来一样「专业」，但**它们不属于这门课的知识体系**，
学生上课时不需要中英对照。碰到这类文件，**把它的候选整体降权或干脆不收**。
判断依据是「这门课会不会讲它」，不是「它是不是专业词汇」。

⚠️ **但别过拟合文件名**：`Week2_Communicating_Ideas` 这种又长又带日期的名字是正常的课程命名，
不要因为名字「不像学术」就把它排掉。看的是**材料的性质**，不是名字好不好看。
如果没给课程名，就按上面那条通用判据来。

## ⚠️ 输入末尾会给一份「重复行清单」
它列出出现在 ≥__REPEAT__ 页上的文字，**多半是版式跑马灯**（章节号、课程名、教师名、页脚）。

**它不是过滤器 —— 重复不等于噪声。** 一整章都在讲 elasticity 时，elasticity 会出现在
几乎每一页上，那正是**最核心的词**，不是跑马灯。判断依据是**内容的学科性**，不是出现得多不多。

## ⚠️ 剥掉模板成分
若某个候选来自反复出现的版式文字，剥掉模板成分（章号 `Ch N:`、课程名、教师名、日期、页脚），
只保留其中真正的学科短语。
例：`Ch 14: Functions of two or more independent variables`
  → 候选写成 `functions of two or more independent variables`
剥完若没有剩下学科内容（例如整行就是课程名或章号），**丢弃，不要输出**。

## 硬性要求
1. `term` 必须**在输入的文本里真的出现过** —— 不改词形之外的东西、不翻译、不补冠词。
   会有一次**确定性复核**把不在原文里的候选丢掉，所以编造的词写了也白写。
2. 单条**不超过 __MAXWORDS__ 个词**，不要输出整句话。
3. 不要重复。最多 __MAXCANDS__ 条。
4. `why` 是 ≤12 字的中文短注，**只给人复核看**，不参与排序。

返回严格 JSON：{"candidates": [{"term": "...", "confidence": 0.0, "why": "..."}]}"""


def _prompt() -> str:
    """把常量插进提示词。

    ⚠️ **不能用 f-string / `.format()`** —— 正文里有字面量 `{"candidates": ...}`，
       f-string 会要求转义大括号，`.format()` 会直接炸。所以用 `__TOKEN__` 替换。

    ⚠️ **为什么非插不可**：这几个数字**同时活在代码和提示词散文里**。
       改常量而不改散文，提示词就开始说谎（模型自我审查掉合法候选），
       而且**没有任何测试会红** —— 表现是「词表莫名少几项」。
    """
    # ⚠️ kind 那一段**从 `extract.KIND_MEANING` 生成**，不手抄 ——
    #    手抄的版本曾经只解释了 5 个 kind，而 `notes`/`ocr` 会真的到达提示词却没人解释。
    from extract import KIND_MEANING
    kinds = "\n".join(f"- {k} —— {v}" for k, v in KIND_MEANING.items())
    return (SYS_EXTRACT
            .replace("__REPEAT__", f"{REPEAT_PAGE_RATIO:.0%}")
            .replace("__MAXWORDS__", str(MAX_TERM_TOKENS))
            .replace("__MAXCANDS__", str(MAX_CANDIDATES_PER_CALL))
            .replace("__KINDS__", kinds))

_MIN_STEM = 4
_SUFFIX_FULL = r"(?:y|ies|e?s|'s)?"
_SUFFIX_PL = r"(?:e?s|'s)?"


def _fold(s: str) -> str:
    """小写、连字符各种写法当空格、撇号统一、空白折叠。**只用于复核**，不改动原文。"""
    s = (s or "").lower()
    for ch in "-–—":
        s = s.replace(ch, " ")
    s = s.replace("’", "'").replace("‘", "'")
    return re.sub(r"\s+", " ", s).strip()


def _exact_pattern(folded: str):
    """带**词边界**的精确匹配。

    ⚠️ 不能用裸子串（`"cost" in "costly"` 为真）—— 那会让一个候选因为
    恰好是某个更长单词的一部分而被判「在语料里出现过」，是**放水**。
    """
    return re.compile(r"\b" + r"\s+".join(re.escape(w) for w in folded.split()) + r"\b",
                      re.IGNORECASE)


def _stem(w: str) -> str:
    """很粗的词干 —— 够用就行，不引 NLP lemmatizer（那是杀鸡用牛刀）。

    ⚠️ **词干 < 4 字符就不剥**：`bus` 剥成 `bu` 会命中 `buy`，那是**放水**。
    """
    if len(w) <= _MIN_STEM:
        return w
    if w.endswith("ies"):
        return w[:-3]
    if w.endswith("es"):
        return w[:-2]
    if w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    if w.endswith("y"):
        return w[:-1]
    return w


def _word_pattern(term: str):
    parts = []
    for w in _fold(term).split():
        st = _stem(w)
        if st != w:
            parts.append(re.escape(st) + _SUFFIX_FULL)     # 剥过词干 -> 词尾放开
        else:
            # 没剥过（短词 / 无常见词尾）-> 只放复数。
            # 加 `y|ies` 会让 `bus` 命中 `busy`。
            parts.append(re.escape(w) + _SUFFIX_PL)
    return re.compile(r"\b" + r"\s+".join(parts) + r"\b", re.IGNORECASE)


def _verify(term: str, text: str) -> bool:
    """候选在语料里真的出现过吗。① 带词边界的精确 → ② 对称词形。"""
    return _verify_folded(term, _fold(text))


def _verify_folded(term: str, folded: str) -> bool:
    """`_verify` 的内核 —— 假定 `folded` **已经是 `_fold` 过的**。

    ⚠️ 为什么拆出这一层：`prepare()` 要拿**同一段语料**核几十个候选。
       照 `_verify(term, corpus)` 直接调，每个候选都把整份语料重新 `_fold` 一遍 ——
       实测 SOC10020（73.6 万字符 × 60 候选）折了 **4400 万字符**，
       光是重复折叠就占掉那一步的 **67%**。折叠幂等，所以「折一次、用 N 次」结果逐字不变。
    """
    t = _fold(term)
    if not t:
        return False
    if _exact_pattern(t).search(folded):
        return True
    return bool(_word_pattern(term).search(folded))

class Candidate(typing.NamedTuple):
    term: str
    confidence: float
    verified: bool
    #: 同一个词在某一块里出现过几次。⚠️ **别叫 `count`**（2026-09-30 pyright 抓出来的）：
    #: `NamedTuple` 继承 `tuple`，而 `tuple.count` 是个**方法** —— 字段同名会把它遮蔽，
    #: 于是 `candidate.count(x)` 抛 `TypeError: 'int' object is not callable`。
    #: 今天没人这么调（所以是**潜伏**的，不是活的），但那个报错完全不指向真因 ——
    #: 谁撞上都会以为是自己写错了括号。（同类的还有 `index`。）
    occurrences: int
    spread: int
    spread_ratio: float
    title_hits: int
    score: float
    rank: int
    added: bool

def _repeated_lines(blocks, threshold: float = REPEAT_PAGE_RATIO) -> list:
    """出现在 ≥threshold 比例页上的文字 —— 当**参考信息**给 LLM，不是过滤器。

    本机实测这一条正好捞出真语料里的 15 条章节跑马灯
    （`Ch 14: Functions of two or more independent variables` 在 46/47 张上）。
    """
    pages = {b.page for b in blocks}
    n = len(pages)
    if n < 3:
        return []
    seen: dict = collections.defaultdict(set)
    for b in blocks:
        seen[b.text].add(b.page)
    thr = max(2, int(n * threshold))
    hits = [(t, len(ps)) for t, ps in seen.items() if len(ps) >= thr and len(t) > 1]
    return sorted(hits, key=lambda x: (-x[1], x[0]))

def _extract_user(course: str, group: list, domain: str = "") -> str:
    """拼一次调用的 user 消息。

    ⚠️ **文件名也喂进去**，因为它是**免费的、独立于内容的一条证据**：
       `ECON10740__Library session for Stage 1 …` 的「Library session」几个字
       直接说明了这份材料的性质 —— 而这在正文里是看不出来的
       （正文只写检索技巧，看起来全是「专业词汇」）。
       两条证据互相印证，比单靠课程名一条线索稳。
    """
    all_blocks = [b for _, bs in group for b in bs]
    rep = _repeated_lines(all_blocks)
    head = [f"课程：{course}"]
    if domain:
        head.append(f"课程名 / 领域：{domain}   ← 只收与它相关的词")
    head.append("")
    if rep:
        head.append(f"## 重复行清单（出现在 ≥{REPEAT_PAGE_RATIO:.0%} 页上，共 {len(rep)} 条）")
        head.append("（**不是过滤器**，自己判断哪些是版式、哪些是主题词）")
        for t, c in rep[:20]:
            head.append(f"  · {t[:90]}   （{c} 页）")
        head.append("")
    head.append("## 课件正文")
    body = []
    for name, blocks in group:
        body.append(f"### 文件：{name}")
        body += [f"[{b.page}] ({b.kind}) {b.text}" for b in blocks]
    return "\n".join(head + body)


def _chunks(per_file: list, limit: int = CHUNK_CHARS) -> list:
    """把 `[(文件名, [Block]), …]` 打包成若干次调用，**保证一页不被拆开**
    （拆开会打散表格行与上下文的对应）。

    ⚠️ **按文件分组而不是把所有块拼成一条流** —— 因为**文件名是证据**
    （见 `_extract_user` 的说明），拼成一条流之后「这段文字来自哪份文件」就丢了。
    一个分块里可以有多个文件；单份文件超限时只切它自己，文件名跟着每一块走。
    """
    out, cur, size = [], [], 0
    for name, blocks in per_file:
        piece, psize = [], 0
        for b in blocks:
            line = len(b.text) + 16
            if piece and psize + line > limit:
                if cur and size + psize > limit:
                    out.append(cur); cur, size = [], 0
                cur.append((name, piece)); size += psize
                piece, psize = [], 0
            piece.append(b); psize += line
        if piece:
            if cur and size + psize > limit:
                out.append(cur); cur, size = [], 0
            cur.append((name, piece)); size += psize
    if cur:
        out.append(cur)
    return out


def _term_stats(blocks, terms: list) -> dict:
    """确定性地算每个候选词的 出现次数 / 跨页数 / 是否进过标题档。

    ⚠️ **候选词是块里的子串**，不是整块文本（`opportunity cost` 出现在
    `Opportunity Cost and Elasticity` 这一整块里）。所以必须**在每块里搜**，
    不能拿块文本当键去查 —— 那条路会让所有 count 都等于 0，一个词都进不了表。
    （这是实现时踩到的：第一版按整块文本建索引，`added` 全空。）

    ⚠️ **不让 LLM 数**：它会在 12k 字符上数错、数抖；而且这些数还要用来排序，
    让模型数就等于「两份定义」（它数它的、我们算我们的），迟早对不上。
    ⚠️ 分块**不影响**这些数 —— 它们在**全部** Block 上算，与分块边界无关。

    ⚠️ **别把 N 个 pattern 合并成一个 alternation 去扫**。看着能把 O(词 × 块) 压成
       O(块)，但交替式会**吞掉**已匹配的文本 —— `marginal cost` 与 `cost` 同时在场时，
       后者永远数不到 → `count` 集体变小 → **排序静默改变**。
    """
    total_pages = max(1, len({b.page for b in blocks}))
    # ⚠️ **先把每块 fold 一次**，别在内层循环里 fold。旧写法是 `pat.findall(_fold(b.text))`
    #    —— 每换一个词、所有块重新折一遍：实测 SOC10020（15,637 块 × 60 词）调了
    #    **93.8 万次 `_fold`、折了 4300 万字符**（= 语料的 58.7 倍），占掉那一步的 **82%**。
    #    折叠幂等，所以「折一次用 N 次」结果不变。
    folded = [_fold(b.text) for b in blocks]
    out: dict = {}
    for t in terms:
        pat = _word_pattern(t)          # 是 _exact_pattern 的超集，且带词边界
        count, pages, titles = 0, set(), 0
        for b, ft in zip(blocks, folded):
            n = len(pat.findall(ft))
            if not n:
                continue
            count += n
            pages.add(b.page)
            if b.kind in ("title", "subtitle"):
                titles += n
        out[t.lower()] = {"count": count, "spread": len(pages),
                          "spread_ratio": len(pages) / total_pages,
                          "title_hits": titles}
    return out


def _rank(rows: list) -> list:
    """按 `confidence × spread_mult × title_mult` 排序。spread 的乘子**按秩映射、有界**。

    有界是刻意的：它保证「置信度相差 ≥21.6% 的两个候选，顺序不可能被频率/标题翻盘」
    （见 SPREAD_MULT_* 的说明）。同分时按 `term` 字典序 —— 否则重跑的溢出清单顺序会抖。
    """
    ratios = sorted({r["spread_ratio"] for r in rows})
    def _mult(r: float) -> float:
        if len(ratios) <= 1:
            return 1.0
        frac = ratios.index(r) / (len(ratios) - 1)
        return SPREAD_MULT_LO + frac * (SPREAD_MULT_HI - SPREAD_MULT_LO)

    ranked = []
    for r in rows:
        mult = _mult(r["spread_ratio"]) * (TITLE_MULT if r["title_hits"] else 1.0)
        ranked.append(dict(r, score=r["confidence"] * mult))
    ranked.sort(key=lambda x: (-x["score"], x["term"].lower()))
    for i, r in enumerate(ranked, 1):
        r["rank"] = i
    return ranked
