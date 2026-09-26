"""开课前的准备 —— 课件 → 候选术语 → 该课术语表。

**这是 P3 的接缝**：CLI（`cl prep`）与（第二批的）拖拽面板都是它的适配器。
`prepare()` 不读 `argv`、不读 `.course`、不画界面、不起线程 —— 课号与写路径都是参数。

## 落点：为什么是「课后矫正层」而不是「ASR 热词」

作者 brief §5.1 已明确否决走 ASR 热词偏置那条路，`REVIEW-2026-09-24.md:203` 有实测
（权重 2.0 不触发 / 8.0 输出崩坏 / 20.0 直接背热词表 —— **没有可用区间**，
机理是「声学证据不支持那个词时，掰回来的偏置会大到压过声学」）。
这里的术语表喂的是**翻译 prompt**（`translator.select_terms` 的 `always=`），
改的是「翻译怎么理解」，不是「ASR 听到什么」。

## 只能追加，绝不覆盖

`glossary/<课号>.txt` 是**手写内容与自动内容共处**的文件：
第一行的 `# <课号> <课名>` 是**领域先验**（`translator.course_title` 读它当判领域的依据），
课号与教务词是手写的。所以本模块对这份文件的纪律是**只追加**，
而且要求「**已有字节逐字节不变**」（`new.startswith(old)`）——
这条一条断言就盖住课号、教务词、首行、空行、用户手写的任何东西。
"""
from __future__ import annotations

import collections
import datetime
import json
import pathlib
import re
import typing

# 单条术语的 token 数上限。EAMT 2023（TransPerfect）做工业术语库清洗时把
# 「超过 5 个空格分隔 token」的条目当噪声 —— 术语不该是一句话。
MAX_TERM_TOKENS = 5

# 「反复出现 = 版式文字」的判据：出现在 ≥ 这个比例的页上。
# ⚠️ 它**同时出现在提示词正文里** —— 所以提示词是用 `_prompt()` 拼的，不是手写死数字。
REPEAT_PAGE_RATIO = 0.4

# 一次调用最多要几个候选。
MAX_CANDIDATES_PER_CALL = 60

_TRAILING_WS = re.compile(r"[ \t]+$")

# ---- 评分常数 ----
# ⚠️ 频率/标题只能做**有界**的乘子，不能做成第二主轴：一个大文件里平庸词的
#    「出现次数」能高过小文件里真正的核心词（不同量纲）。所以：
#    · confidence 是主轴（LLM 已经看过该文件全貌，是唯一天然跨文件可比的信号）
#    · spread 用**比例**（cross 文件可比），且按秩映射到 [LO, HI]
#    · 由此得到一条**可断言的性质**：翻盘需要 c1/c2 < (HI × TITLE) / (LO × 1.0)。
#      ⚠️ **这里故意不写那个百分比** —— 它是派生值，写死了就会在调 TITLE_MULT 之后
#      继续说谎（`tests/test_prep.py` 那条断言也从这几个常量算，不抄数字）。
SPREAD_MULT_LO = 0.95
SPREAD_MULT_HI = 1.05
TITLE_MULT = 1.1

# 一次 LLM 调用喂多少字符。本机实测单份课件 2K–13K 字符（最大 12,814），
# 所以「一周一份课件」永远是一次调用；一次拖一学期才会分组。
CHUNK_CHARS = 30_000

DEFAULT_MAX_AUTO = 40
DEFAULT_MAX_TOTAL = 120

# ⚠️ 判断标准逐字沿用 `build_notes.SYS_CLASSIFY` 的 SYS_CLASSIFY —— **同一个人的口味，
#    两条路一致**。但职责不重叠：那边是「给已有的术语分档」，这里是「从散文里抽词」。
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


class GlossaryError(RuntimeError):
    """术语表本身有问题（编码不对 / 读不出）。**绝不静默** —— 静默会让人以为写进去了。"""


class AppendResult(typing.NamedTuple):
    """⚠️ 比方案里写的两元组多一个 `skipped_long`。

    方案原文是 `-> (added, skipped_dup)`，但那样「太长被丢掉」就没有出口 ——
    而本仓库反复咬人的正是**静默丢东西**。多一个字段，少一类静默失败。
    """
    added: list
    skipped_dup: list
    skipped_long: list


def append_terms(path, terms: list) -> AppendResult:
    """把 `terms` 追加到 `path`。返回 `(added, skipped_dup, skipped_long)`。

    ⚠️ **写路径是参数，不由本函数算** —— 这是 `term_notes.json` 从 41KB 被写成 4.8KB
    那次事故换来的硬规矩（CLAUDE.md「测试必须隔离写端」）。调用方给什么路径就写什么。

    ⚠️ 签名里**没有**公共表（`glossary.txt`）的位置 —— 不是「不调用」，是**结构上拿不到**。
    课号必须全列在公共表里且发音极近，那是手写的、谨慎的东西，不该被自动追加碰。

    幂等：同一批 `terms` 跑两次，第二次 `added` 为空。
    """
    p = pathlib.Path(path)

    if p.exists():
        try:
            old = p.read_text(encoding="utf-8")
        except UnicodeDecodeError as e:
            # ⚠️ 手写术语表常从网页/Word 粘来，可能不是 UTF-8。
            #    `translator._load_terms` 对这种情况是「出声后忽略」——
            #    本函数**不能跟着忽略**：忽略会让人以为词写进去了，而实际一个都没写。
            raise GlossaryError(
                f"{p.name} 不是 UTF-8，拒绝追加（免得把原内容写坏）。"
                f"先转成 UTF-8 再跑。") from e
        except OSError as e:
            raise GlossaryError(f"{p.name} 读不出：{e}") from e
    else:
        # 新课程：建文件。⚠️ **不猜课名** —— 首行是模型判领域的先验，
        # 猜错比空着更糟（会把整个学期的矫正带偏）。调用方负责提示用户手补。
        old = f"# {p.stem}\n"

    have = {ln.strip().lower() for ln in old.splitlines()
            if ln.strip() and not ln.strip().startswith("#")}

    added, dup, long_ = [], [], []
    seen: set = set()
    for raw in terms:
        s = _TRAILING_WS.sub("", (raw or "").strip())
        if not s or s.startswith("#"):
            continue                                   # 空串与注释形态的候选一律不收
        k = s.lower()
        if k in have or k in seen:
            dup.append(s)
            continue
        if len(s.split()) > MAX_TERM_TOKENS:
            long_.append(s)
            continue
        seen.add(k)
        added.append(s)

    if added:
        body = old
        if not body.endswith("\n"):
            # ⚠️ 文件末尾没有换行时先补一个 —— 否则新词会**粘在上一行的尾巴上**，
            #    把一条好好的术语毁掉，而且不报错。
            body += "\n"
        body += "\n".join(added) + "\n"
        _atomic_write(p, body)

    return AppendResult(added, dup, long_)


def _atomic_write(path: pathlib.Path, text: str) -> None:
    """先写同目录 tmp 再 `os.replace` 原子替换。

    ⚠️ **故意复用 `build_notes._atomic_write` 而不是自己抄一份** ——
    抄一份就是「同一条纪律两处定义」，迟早漂移（`panel.py` 的 `SCRIM_ALPHA`
    抄过两份，那次事故的教训）。本仓库里原子写的**唯一定义点**在 `build_notes`。
    """
    from build_notes import _atomic_write as _impl
    _impl(path, text)


# ================================================================ 候选词复核
#
# ⚠️ 这一节是**反幻觉护栏**：LLM 抽出来的候选必须**在语料里真的出现过**，
#    否则一律不自动加入。依据是我们自己引的 EDM 2026：喂长上下文会让 WER
#    从 32.3 **涨到** 43.4（幻觉）。有了这道复核，`confidence` 就从
#    「承重墙」降级成「排序偏好」—— 代价只有一次字符串匹配。
#
# ⚠️ **这道关卡只问「在不在原文里」，不问「跟这门课有没有关系」—— 这是刻意的。**
#    实测：一份图书馆检索培训的课件里 `Boolean Operators` 确实**逐字出现**，所以它会过
#    `verified`；拦不拦得住全看 `confidence` 这道**软排序**。
#    → **不打算往 `verified` 里加主题匹配硬门槛**：那会把「通用学术词」也一起毙掉
#      （`research question` 在一节经济学课里学生真碰到、真需要中文提示，毙掉是错的），
#      而真正该拦的是「**属于另一个专业领域的行话**」，那是**软信号**的活。
#    ⚠️ 这条有它的边界：**支持性材料的占比一大，软排序未必兜得住**
#      （一学期塞三次图书馆培训，频率信号还是会把它们往上顶）。
#      到那时该加的是**跨文件一致性**信号（见 docs/PLAN-p3-prep.md 的「第二批/以后」），
#      不是把 `verified` 收紧 —— 收紧会让它开始误杀掉好词。
#
# ⚠️ **裸的「大小写不敏感子串」是不够的**（本机实测，不是推测）。这六种都会误杀：
#      elasticities   ← elasticity        词形，单向剥 s/es 救不了
#      elasticity     ← elasticities      **反方向**（词在中间、不是词尾）
#      cost-benefit   ← cost benefit      连字符 vs 空格
#      marginal utilities ← marginal utility
#      functions of two or more independent variables ← function of …
#   而第 5 行正是本模块 prompt **自己要求 LLM 做的剥离** —— 不改的话，
#   LLM 只要顺手写成单数，就会被自己的复核判成幻觉。
#
# ⚠️ **不抽成共享模块、也不改 `build_notes.compile_term`**（`build_notes.compile_term`）——
#    不是因为「那边在翻译主链路上」（**那句是错的**：`compile_term` 全仓只有一个调用点
#    `build_notes.py` 的 `TermNotes._build_index`，而 `TermNotes` 只服务字幕上的术语解析；
#    `translator` 根本不 import `build_notes`，它按行读字符串，一个正则都不用）。
#
#    **真理由是：两者是不同政策，不是同一契约的两份实现。**
#      · `compile_term`：问「这句话里出现这个术语了吗」，跑在**课堂热路径、逐句**，
#        错的代价是**假阳性**（课上显示一条错误的释义）
#      · 本模块 `_verify`：问「这个候选是不是模型编的」，**离线、整份语料、一次**，
#        错的代价是**假阴性**（把真术语判成幻觉）—— 反向不可接受
#    所以这边才要对称词形 + 连字符折叠 + 词干护栏，而那边不对称、不折叠。
#    改一边**不需要**镜像改另一边 —— **没有耦合，就没有缝**。
#    硬抽出来只会是「折叠 + 词边界 + 可选复数」这 6 行，配上三个开关服务两种相反的错误代价
#    —— 那正是 `panel.py` 文件头说的「参数化没有调用方的差异 = 白加的接口」。
#
# ⚠️ 一条**没人写下来的跨模块隐含契约**：本模块的对称容忍会把 `elasticities`
#    这类词形写进 glossary，而运行时 `compile_term` 是**不对称**的（只认词尾 `s`/`'s`）
#    → 课上讲 `elasticity` 时那条注释不会亮。代价可接受，因为 glossary 的主用途是
#    **全量注入翻译 prompt**（`translator.py` 的 `select_terms(always=…)`），不是靠匹配触发。
#    **知道就行，别靠共享代码去修**。

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


# ================================================================ 装配

class Candidate(typing.NamedTuple):
    term: str
    confidence: float
    verified: bool
    count: int
    spread: int
    spread_ratio: float
    title_hits: int
    score: float
    rank: int
    added: bool


class FileReport(typing.NamedTuple):
    path: pathlib.Path
    status: str
    chars: int
    blocks: int
    skipped_shapes: int
    ocr_pages: int
    error: str


class PrepResult(typing.NamedTuple):
    course: str
    files: list
    failed: list
    candidates: list
    added: list
    not_added: list
    skipped_existing: list
    glossary_path: object
    glossary_total: int
    notes_added: object      # int；读不出时是 None（见 _notes_count）
    aborted: str
    archived: list


def _archive(files: list, materials_dir) -> list:
    """把课件原件归档到 `~/.classlive/courses/<课号>/materials/`。

    归档（而不只在原地读）的理由：① 课件原件可能被用户从下载目录删掉；
    ② 重跑 prep 不必再找原文件；③ P4 的数据目录从这里长出来。

    ⚠️ **归档失败不让整场 prep 失败** —— 它是附带动作，不是主链路。
    ⚠️ 已经在归档目录里的不重复拷；同名但大小不同的**加 `-2` 后缀**而不是覆盖
    （覆盖等于静默丢掉上一份课件）。
    """
    md = pathlib.Path(materials_dir)
    out = []
    try:
        md.mkdir(parents=True, exist_ok=True)
        for f in files:
            if not f.exists():
                continue
            try:
                if f.resolve().parent == md.resolve():
                    out.append(f)
                    continue                       # 已经在归档目录里
            except OSError:
                pass
            dst = md / f.name
            if dst.exists():
                try:
                    if dst.stat().st_size == f.stat().st_size:
                        out.append(dst)
                        continue                   # 大小一样 -> 认为是同一份
                except OSError:
                    pass
                i = 2
                while dst.exists():
                    dst = md / f"{f.stem}-{i}{f.suffix}"
                    i += 1
            import shutil
            shutil.copy2(f, dst)
            out.append(dst)
    except OSError as e:
        print(f"⚠ 归档失败（不影响本次准备）：{str(e)[:70]}")
    return out


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


def _domain_prior(glossary_dir, course: str) -> str:
    """这门课的领域先验 —— 取 `glossary/<课号>.txt` 首行的注释。

    ⚠️ **复用 `translator.course_title`**（`translator.course_title`）而不是自己再读一遍：
    里面那套「精确路径不存在就按后缀模糊匹配」的容错逻辑（`course_terms_path`）
    正是「`cl course 1077` 写进去的是短代号」那种情况要的，抄一份就会漂移。
    它要的是**公共术语表**的路径，从它推出 `glossary/` 目录 —— 与运行时同一条路。
    """
    try:
        from translator import course_title
        return course_title(str(pathlib.Path(glossary_dir).parent / "glossary.txt"), course)
    except Exception:                                           # noqa: BLE001
        return ""


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


def _course_glossary_path(glossary_dir, course: str) -> pathlib.Path:
    """定位这门课的术语表 —— **与运行时同一套解析**。

    ⚠️ 别自己裸 join。`translator.course_terms_path` 在后面加了
       「精确路径不存在就按**后缀**唯一匹配」的容错（对付 `.course` 里存短代号那种情况）。
       裸 join 会在那种情况下**写到一个 translator 永远不加载的文件上**，
       而 prep 照样报「✅ 加了 N 个词」—— 正是本仓库最怕的那类静默失败。
    ⚠️ 同一文件里读路径（`_domain_prior` → `course_title` → `course_terms_path`）
       与写路径必须是**同一个答案**，否则一次运行就能出现「先验读 A、词写进 B」。
    """
    from translator import course_terms_path
    public = pathlib.Path(glossary_dir).parent / "glossary.txt"
    found = course_terms_path(str(public), course)
    return found if found is not None else pathlib.Path(glossary_dir) / f"{course}.txt"


def _load_state(path: pathlib.Path):
    """墓碑：prep 曾经追加过的词。用户删掉之后重跑不许复活（同 memoQ 的 stop word list）。

    返回 dict；**文件存在但读不出时返回 `None`** —— 那是「别覆盖」的信号，不是「空」。

    ⚠️ 这两种情况必须分开，理由与 `build_notes.load_raw` 那条一模一样：
       读不出当空 → 随后照常写回 → 用空字典**覆盖**掉真墓碑 → 用户删掉的词**永久复活**，
       而唯一的痕迹是 stdout 一行 ⚠（跑完就滚掉了）。
       这正是 2026-09-24 那次把 `term_notes.json` 从 41KB 写成 4.8KB 的同一形状。
    """
    if not path.exists():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"⚠ {path.name} 存在但读不出（{type(e).__name__}）—— 本次**不覆盖**它。")
        return None
    got = obj.get("appended") if isinstance(obj, dict) else None
    return dict(got) if isinstance(got, dict) else None


def _save_state(path: pathlib.Path, appended: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, json.dumps({"appended": appended}, ensure_ascii=False, indent=1))


def _default_chat(api_key: str, model: str):
    """默认实现：窄包装 `build_notes._chat_json`（它已带 json_object + thinking=disabled）。"""
    from build_notes import _chat_json
    return lambda system, user: _chat_json(api_key, model, system, user, 4000, 0.2)


def prepare(course: str, files: list, *, glossary_dir, state_path,
            materials_dir=None, skip=(),
            api_key: str | None = None, model: str = "deepseek-flash",
            max_auto: int = DEFAULT_MAX_AUTO, max_total: int = DEFAULT_MAX_TOTAL,
            ocr: bool = True, on_progress=None, chat=None, build_fn=None) -> PrepResult:
    """课件 → 候选术语 → 追加进 `glossary/<课号>.txt` → 生成中文释义。

    **不读 `argv`、不读 `.course`、不画界面、不起线程**。课号与写路径都是参数。

    `chat(system, user) -> dict|None` 与 `build_fn(course) -> None` 是**注入的依赖**：
    默认分别是 `build_notes._chat_json` 的窄包装和 `build_notes.build`。
    测试注入假的即可做到**全链路零网络**（`codebase-design`：依赖要接受，不要自己造）。

    返回 `PrepResult`；**任何失败都走 `aborted` 而不是抛异常**，
    所以调用方的循环不需要 `try`。`aborted` 非空 = 坏的那一边，适配器据此给非零退出码。
    """
    def _empty(aborted: str, **kw) -> PrepResult:
        base = dict(course=course, files=[], failed=[], candidates=[], added=[],
                    not_added=[], skipped_existing=[], glossary_path=None,
                    glossary_total=0, notes_added=0, aborted=aborted, archived=[])
        base.update(kw)
        return PrepResult(**base)

    def _prog(stage, done, total):
        if on_progress:
            on_progress(stage, done, total)

    if not (course or "").strip():
        return _empty("no_course")
    files = [pathlib.Path(f) for f in (files or [])]
    if not files:
        return _empty("no_files")

    glossary_path = _course_glossary_path(glossary_dir, course)
    state_path = pathlib.Path(state_path)
    # 并发锁：`append_terms` 是读-改-写，两个 `cl prep` 同时跑会互相吃掉对方的追加。
    # ⚠️ 复用 `instance_lock.acquire(path)`（它本来就收自定义路径），锁**本课专属**的
    #    一个文件 —— 不碰上课用的 instance.lock，也不拦 `cl` 启动。
    import instance_lock
    lock_path = state_path.with_name(state_path.name + ".lock")
    # ⚠️ 先 `probe()` 再 `acquire()`。`acquire()` 把「已被占用」和「连锁文件都建不出来
    #    （磁盘满 / 权限）」**压成同一个 `(None, …)`** —— 直接照它报 `locked`
    #    会把磁盘/权限问题误诊成「另一个 cl prep 正在写」，方向正好反了
    #    （`instance_lock.probe()` 的文档写的就是这个坑）。
    _state0, _ = instance_lock.probe(lock_path)
    if _state0 == "held":
        return _empty("locked")
    _lock, _holder = instance_lock.acquire(lock_path)
    if _lock is None and _state0 != "unknown":
        # `unknown`（锁文件建不出/读不出）按本仓库的方向 **fail-open**：
        # 锁坏掉时不该拦住任何事。其余情况是极窄的竞态（probe 说空、acquire 却被占）。
        return _empty("locked")
    try:
        # ---- ⓪ 归档（给了 materials_dir 才做；失败不中止）----
        archived = _archive(files, materials_dir) if materials_dir is not None else []

        # ---- ① 抽文本（只读，坏文件记进 failed 继续）----
        # ⚠️ **手动跳过**：图书馆培训、新生导览、客座讲座这类「混进来的支持性材料」
        #    是会**反复出现**的文件类型（不是这次撞见的特例）。与其指望 prompt 每次都猜对，
        #    不如给一个确定、零误判的开关。**照常归档，只是不参与抽词。**
        import fnmatch
        globs = [str(g) for g in (skip or [])]
        def _skipped(f):
            return any(fnmatch.fnmatch(f.name, g) or fnmatch.fnmatch(str(f), g) for g in globs)
        to_read = [f for f in files if not _skipped(f)]
        skipped_files = [f for f in files if _skipped(f)]
        if not to_read:
            return _empty("all_files_skipped", files=[
                FileReport(f, "skipped", 0, 0, 0, 0, "按 --skip 排除") for f in skipped_files])

        import extract
        reports, all_blocks, per_file = [], [], [
            FileReport(f, "skipped", 0, 0, 0, 0, "按 --skip 排除（仍已归档）")
            for f in skipped_files]
        for i, f in enumerate(to_read, 1):
            r = extract.extract(f, ocr=ocr)
            reports.append(FileReport(f, r.status, r.chars, len(r.blocks),
                                      r.stats.shapes_skipped, r.stats.ocr_pages, r.error))
            all_blocks += list(r.blocks)
            if r.blocks:
                per_file.append((str(f), list(r.blocks)))
            _prog("extract", i, len(to_read))
        failed = [r for r in reports if r.status not in ("ok", "skipped")]
        if len(to_read) and len(failed) == len(to_read):
            return _empty("all_files_failed", files=reports, failed=failed)
        if not all_blocks:
            return _empty("zero_text", files=reports, failed=failed)

        # ---- ② 抽候选词（**先做完，确认成功再落盘** —— 失败就不许动任何文件）----
        # key 只解析一次，两个下游（抽词 / 生成释义）共用。
        from cloud_translator import load_api_key
        key = load_api_key(api_key) or ""
        if chat is None:
            if not key:
                return _empty("no_api_key", files=reports, failed=failed)
            chat = _default_chat(key, model)

        groups = _chunks(per_file)
        domain = _domain_prior(glossary_dir, course)
        merged: dict = {}
        for i, g in enumerate(groups, 1):
            try:
                obj = chat(_prompt(), _extract_user(course, g, domain))
            except Exception as e:                              # noqa: BLE001
                print(f"⚠ 候选词抽取失败：{type(e).__name__}: {str(e)[:80]}")
                return _empty("llm", files=reports, failed=failed)
            if not isinstance(obj, dict) or not isinstance(obj.get("candidates"), list):
                print("⚠ 候选词返回的不是 {'candidates': [...]} 结构")
                return _empty("llm", files=reports, failed=failed)
            for item in obj["candidates"]:
                if not isinstance(item, dict):
                    continue
                t = str(item.get("term", "")).strip()
                if not t:
                    continue
                try:
                    c = float(item.get("confidence", 0.0))
                except (TypeError, ValueError):
                    c = 0.0
                k = t.lower()
                if k not in merged or c > merged[k]["confidence"]:
                    merged[k] = {"term": t, "confidence": max(0.0, min(1.0, c)),
                                 "why": str(item.get("why", ""))[:24]}
            _prog("candidates", i, len(groups))

        # ---- ③ 确定性复核 + 排序 ----
        # ⚠️ 语料**只折一次**（见 `_verify_folded` 的说明）。
        corpus = _fold("\n".join(b.text for b in all_blocks))
        stats = _term_stats(all_blocks, [m["term"] for m in merged.values()])
        rows = []
        for k, m in merged.items():
            st = stats.get(k) or {"count": 0, "spread": 0, "spread_ratio": 0.0,
                                  "title_hits": 0}
            # 短路：`count > 0` = 「某一**块**里出现过」，而块文本是语料的子集 → 必然
            # `verified`。（真语料 60 个候选复核过，反例 0。）
            # ⚠️ **反方向不能换** —— `verified ⇒ count > 0` 不成立（跨块出现的词），
            #    见下面选词循环里那段说明。
            verified = st["count"] > 0 or _verify_folded(m["term"], corpus)
            rows.append(dict(m, verified=verified, **st))
        ranked = _rank(rows)

        # ---- ④ 墓碑 + 现有行 -> 选谁进表 ----
        try:
            existing_lines = [ln.strip() for ln in
                              glossary_path.read_text(encoding="utf-8").splitlines()
                              if ln.strip() and not ln.strip().startswith("#")]
        except FileNotFoundError:
            existing_lines = []
        except UnicodeDecodeError:
            return _empty("glossary", files=reports, failed=failed)
        have = {ln.lower() for ln in existing_lines}
        # ⚠️ 只读**一次**。旧写法这里读一次、写回时又读一次，两次之间没有任何写入方
        #    （本课锁保证了），纯粹是白读一遍。
        state = _load_state(state_path)
        can_save = state is not None            # 读不出 -> 不许覆盖（见 _load_state 的说明）
        if not can_save:
            print("   （本次不更新 prep 状态 —— 删过的词这次可能被重新加回。）")
        tomb = {k.lower() for k in (state or {})}
        existing_total = len(existing_lines)

        # ⚠️ 上限的交互（方案里定义死的）：不是「没到顶就加满 max_auto」，
        #    而是 min(max_auto, max_total − 现有)，负数取 0。
        budget = max(0, min(max_auto, max_total - existing_total))
        picked, not_added, skipped = [], [], []
        for r in ranked:
            k = r["term"].lower()
            # ⚠️ 「已在表里 / 撞墓碑」先判 —— 否则一个已存在的词会因为没通过复核
            #    被报成「未加入，可以手动加」，而它本来就在表里，那个提示是错的。
            if k in have or k in tomb:
                skipped.append(r["term"]); continue
            if not r["verified"]:
                # ⚠️ 这里**只判 verified**，不是 `not verified or count == 0`。
                #
                # 两个条件**不**等价：`count` 数的是「同一块里出现过几次」，
                # 而 `verify` 用的是**全文拼接**后的语料 —— 所以一个词跨两块出现时
                # （`opportunity` 在一块、`cost` 在另一块），`verified=True` 而 `count=0`。
                # 那种词是**真的在语料里**，不该被挡下；它 `spread_ratio=0` 会自然排到后面。
                #
                # （第一版写成两个条件，我在注释里断言后半句是死逻辑 —— **那句是错的**，
                #   是简化审查用反例抓出来的。留着这段是因为这个坑很容易再踩一次。）
                not_added.append(r["term"]); continue
            if len(picked) < budget:
                picked.append(r["term"])
            else:
                not_added.append(r["term"])

        # ---- ⑤ 落盘：**glossary 先、state 后** ----
        # 反过来的失败方向是「词被永久拉黑、从没出现过，用户看不到也删不掉」——更坏。
        try:
            _prog("append", 0, 1)
            ar = append_terms(glossary_path, picked)
        except GlossaryError as e:
            print(f"⚠ {e}")
            return _empty("glossary", files=reports, failed=failed)
        # ⚠️ **只含本次真加进去的**，不并 tomb。并进去的话，用户早就删掉、
        # 这次被墓碑挡下的词会在 `Candidate.added` 里显示成 True —— 而它
        # 既没进 glossary、也不在 `PrepResult.added` 里，两边对不上。
        added_set = {t.lower() for t in ar.added}
        if can_save:
            stamped = dict(state)                    # ⚠️ 复用上面那次读，别重读
            today = datetime.date.today().isoformat()
            for t in ar.added:
                stamped[t] = today
            try:
                _save_state(state_path, stamped)
            except Exception as e:                              # noqa: BLE001
                # ⚠️ 出声，不静默：state 没写成功意味着「用户删掉的词可能复活」，
                #    但这个方向比「词被永久拉黑」轻，所以不中止。
                print(f"⚠ 状态文件写失败（删过的词可能被重新加回）：{str(e)[:70]}")
        _prog("append", 1, 1)

        # ---- ⑥ 出口②：生成中文释义（已有流水线）----
        # ⚠️ `build_notes.build()` **返回 None**，而且没 key 时只打一行就 return
        #    （`build_notes.build`）。所以：
        #    · key 我们自己判，不让它那句「⚠ 没有 API key」混在进度里当成成功
        #    · 加了几条不能从返回值拿 —— 读 `term_notes.json` 前后条数做差
        notes_before = _notes_count()
        _prog("build", 0, 1)
        runner = build_fn
        if runner is None and not key:
            print("\n⚠ 释义没生成：没找到 API key。"
                  "术语表已经写好了 —— 有 key 之后跑 `cl prep` 或 "
                  "`python build_notes.py <课号>` 补上就行。")
        else:
            if runner is None:
                from build_notes import build as _build
                runner = lambda c: _build(c, api_key=key)
                # ⚠️ 这行标题只在**真跑** build 时打；测试注入 build_fn 时不该有它，
                #    否则日志里会出现一块「说在生成释义、其实什么都没干」的空档。
                print("\n── 生成中文释义（build_notes）──")
            try:
                runner(course)
            except Exception as e:                              # noqa: BLE001
                print(f"⚠ 释义生成失败（术语表已写好，不影响翻译）：{str(e)[:80]}")
        notes_after = _notes_count()
        notes_added = (None if (notes_before is None or notes_after is None)
                       else max(0, notes_after - notes_before))
        _prog("build", 1, 1)

        total_after = existing_total + len(ar.added)
        return PrepResult(
            course=course, files=reports, failed=failed,
            candidates=[Candidate(r["term"], r["confidence"], r["verified"],
                                  r["count"], r["spread"], r["spread_ratio"],
                                  r["title_hits"], r["score"], r["rank"],
                                  r["term"].lower() in added_set) for r in ranked],
            added=ar.added, not_added=not_added + ar.skipped_long,
            skipped_existing=skipped + ar.skipped_dup,
            glossary_path=glossary_path, glossary_total=total_after,
            notes_added=notes_added, aborted="", archived=archived)
    finally:
        instance_lock.release(_lock)


def _notes_count():
    """`term_notes.json` 里有多少条 —— 用来算 `build()` 实际加了几条
    （`build()` 返回 None，拿不到别的数）。

    ⚠️ **读不出时返回 `None`，不返回 0**。返回 0 与「表本来就是空的」分不开，
    于是文件损坏时 `notes_added` 会是 0，而 `main()` 会打印
    「没有新词，释义也没变」—— 一条与事实相反的结论。
    本仓库对 `load_raw` 的纪律就是「读不出不许当空」（那次 41KB→4.8KB 事故）。
    """
    try:
        from build_notes import NOTES_FILE, normalize, load_raw
        _, terms, _, _ = normalize(load_raw(NOTES_FILE))
        return len(terms)
    except Exception as e:                                      # noqa: BLE001
        print(f"⚠ 读不出 term_notes.json（{type(e).__name__}）—— 本次释义新增数记成「未知」")
        return None


# ================================================================ CLI 适配器
#
# 这一层只做三件事：解析 argv / 把进度变成人话 / 把 `aborted` 变成退出码。
# **「报错」在本模块里是一个状态，不是一次抛出** —— 模块层永不抛，适配器层让错误「响」。

_ABORT_MSG = {
    "no_course": "没给课号。先 `cl course <课号>` 设一次，或者 `cl prep --course <课号> <课件…>`。",
    "no_files": "没给课件。用法：`cl prep <课件.pdf|pptx> …`（可给多个）",
    "locked": "另一个 `cl prep` 正在写这门课的术语表 —— 等它跑完再试。",
    "all_files_failed": "所有文件都打不开（损坏 / 不是 PDF 或 PPTX / 有密码）。**术语表一个字没动。**",
    "zero_text": "这些文件一个字都抽不出来（可能是扫描件且 OCR 也读不出）。**术语表一个字没动。**",
    "no_api_key": "没找到 DeepSeek API key。**术语表一个字没动** —— 配好 key 再跑。",
    "llm": "连不上 DeepSeek，或它返回的不是预期结构。**术语表一个字没动**，有网了再跑一次就行。",
    "glossary": "术语表本身有问题（比如不是 UTF-8）。已停手，没改动它。",
    "all_files_skipped": "这些课件全被 `--skip` 排除了，没有可抽的。",
}
_STAGE_NAME = {"extract": "抽文本", "candidates": "抽候选词",
               "append": "写入术语表", "build": "生成中文释义"}


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog="cl prep",
        description="开课前的准备：课件 → 候选术语 → 该课术语表。",
        epilog="词只**追加**进 glossary/<课号>.txt（不清空、不重排），"
               "然后由 build_notes 生成屏幕上的中文释义。")
    ap.add_argument("files", nargs="*", help="课件（.pdf / .pptx），可给多个")
    ap.add_argument("--course", default="", help="课号（`cl` 会把 .course 传进来）")
    # ⚠️ `cl` 脚本启动时会 `cd` 到安装目录，所以用户在**自己目录**里敲
    #    `cl prep week5.pptx` 时，那个相对路径会被解析成「安装目录下的 week5.pptx」——
    #    结果是 `extract` 报 `unreadable`，最后汇总成
    #    「所有文件都打不开（损坏 / 不是 PDF 或 PPTX）」——**一条与真因无关的错**。
    #    `cl` 的 `file` 分支正是为这个坑做了校验+转绝对路径。
    #    这里让调用方把「原来的工作目录」告诉模块，比在 bash 里猜哪个参数是路径可靠
    #    （`--skip` 的值就不是路径，按「不像参数就当路径」去猜会误伤它）。
    ap.add_argument("--cwd", default="", help="相对路径相对于哪个目录（`cl` 会传它原来的 PWD）")
    ap.add_argument("--max-add", type=int, default=DEFAULT_MAX_AUTO,
                    help=f"每次自动加多少条（默认 {DEFAULT_MAX_AUTO}）")
    ap.add_argument("--max-total", type=int, default=DEFAULT_MAX_TOTAL,
                    help=f"每门课术语总量的软天花板（默认 {DEFAULT_MAX_TOTAL}）")
    ap.add_argument("--more", action="store_true",
                    help="越过总量天花板再加一次（术语表越大，每句 prompt 越贵）")
    ap.add_argument("--no-ocr", action="store_true",
                    help="不给扫描页跑 OCR（快，但扫描件会抽不到字）")
    ap.add_argument("--skip", action="append", default=[], metavar="GLOB",
                    help="跳过匹配这个 glob 的课件（可多次）。**仍会归档**，只是不参与抽词 —— "
                         "给图书馆培训/新生导览这类混进来的支持性材料用")
    args = ap.parse_args(argv)

    course = (args.course or "").strip()
    if not course:
        print("❌ " + _ABORT_MSG["no_course"])
        return 1

    import instance_lock
    import paths

    held, holder = instance_lock.is_held()
    print(f"▶ 开课前的准备 · {course}")
    if held:
        print(f"  ⚠ 检测到 ClassLive 正在上课（{instance_lock.describe_holder(holder)}）")
        print("     这次改动**下节课**才生效 —— 术语表只在启动时读一次。")

    def on_progress(stage, done, total):
        name = _STAGE_NAME.get(stage, stage)
        if total > 0:
            print(f"\r  · {name} {done}/{total}…", end="", flush=True)
            if done >= total:
                print()

    base = pathlib.Path(args.cwd) if args.cwd else pathlib.Path.cwd()
    # ⚠️ 别把这个变量叫 `paths` —— 上面 `import paths` 是模块，同名会把它覆盖掉，
    #    下面 `paths.prep_state(...)` 就炸（改的时候真踩了，被自己的测试抓住）。
    resolved = []
    for f in args.files:
        q = pathlib.Path(f).expanduser()
        resolved.append(q if q.is_absolute() else (base / q))
    missing = [str(q) for q in resolved if not q.exists()]
    if missing:
        print("❌ 找不到这些课件：")
        for m in missing:
            print(f"     {m}")
        print("   （给的是相对路径的话，检查一下当前目录 —— `cl` 会按你敲命令时的目录解析。）")
        return 1
    result = prepare(
        course, resolved,
        glossary_dir=pathlib.Path(__file__).with_name("glossary"),
        state_path=paths.prep_state(course),
        materials_dir=paths.materials_dir(course),
        max_auto=args.max_add,
        max_total=10 ** 6 if args.more else args.max_total,
        ocr=not args.no_ocr,
        skip=args.skip,
        on_progress=on_progress,
    )

    if result.aborted:
        print(f"\n❌ {_ABORT_MSG.get(result.aborted, result.aborted)}")
        return 1

    for f in result.files:
        # ⚠️ 符号只用仓库既有那一套（`doctor.py` 的 ✅/❌/⚪/⚠️ + `cl` 的 ▶ + `build_notes` 的 💡）。
        #    「跳过」用 ⚪ —— `doctor.py` 里它的语义就是「这项不参与」，对得上。
        mark = "  ⚪" if f.status == "skipped" else ("  ✅" if f.status == "ok" else "  ⚠️")
        ocr = f"，{f.ocr_pages} 页走了 OCR" if f.ocr_pages else ""
        skip = f"，跳过 {f.skipped_shapes} 个版式形状" if f.skipped_shapes else ""
        tail = f"  —— {f.error}" if f.error else ""
        print(f"{mark} {f.path.name}：{f.blocks} 块 / {f.chars:,} 字符{ocr}{skip}{tail}")

    if result.archived:
        print(f"\n   课件已归档到 {result.archived[0].parent}")
    print(f"\n✅ 这次加了 {len(result.added)} 个词 → "
          f"{result.glossary_path}（共 {result.glossary_total} 条）")
    if result.added:
        print("   " + "、".join(result.added))
    if result.not_added:
        print(f"\n   另有 {len(result.not_added)} 个候选**没自动加入**"
              f"（没通过复核 / 超上限），要加的话从下面挑：")
        for t in result.not_added[:24]:
            print(f"     · {t}")
    if result.skipped_existing:
        print(f"\n   （{len(result.skipped_existing)} 个已在表里或你删过，已跳过）")
    if result.notes_added is None:
        print("\n   ⚠️ 释义新增数**读不出**（term_notes.json 可能损坏）—— 建议自己看一眼那个文件。")
    elif result.notes_added:
        print(f"\n✅ 中文释义 +{result.notes_added} 条 → term_notes.json")
    elif not result.added:
        print("\n   （没有新词，释义也没变。）")

    # ⚠️ 新课程的首行只有 `# <课号>`，没有课名 —— 那是模型的**领域先验**，
    #    空着会让它判不出「这是哪门课」。提示手补，**不猜**（猜错比空着更糟）。
    try:
        first = result.glossary_path.read_text(encoding="utf-8").splitlines()[0]
        if first.strip() == f"# {course}":
            # ⚠️ 照 `doctor.py` 的约定：**凡是让人去做什么，就给一行可以直接拷的命令**
            #    （那边原文：「绝对路径 —— 这行是给用户**拷去执行**的」）。
            print(f"\n💡 这个术语表的首行还没有课名 —— 它是模型判**领域**的先验"
                  f"（缺了它，模型不知道该往哪个学科上猜）。")
            print(f"   现在：{first.strip()}")
            print(f"   {result.glossary_path}   ← 把这行改成像")
            print(f'   # {course} Introduction to Economics(经济学导论)')
    except (OSError, IndexError, UnicodeDecodeError):
        pass
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
