"""LLM 修正 + 翻译(流式, 中文优先)。

输出格式改为两段(不再用 JSON —— JSON 无法流式解析):
    ZH: <中文译文>
    EN: <修正后的英文>

用 mlx-lm 的 stream_generate 逐 token 出字: 中文一出现就推给 UI(打字机效果),
英文随后补齐。解析用状态机, 不做正则匹配(流式文本上正则不可靠)。

用一把锁保证串行生成(GPU 上顺序执行)。
"""
from __future__ import annotations
import difflib, os, pathlib, re, threading


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


SYSTEM_PROMPT = """你是英文课堂字幕校正+翻译助手。给你一句 ASR 转写的英文、课程领域、本课术语表, 以及最近几句上下文。
请严格按下面两行格式输出, 不要任何多余文字:
ZH: <中文译文>
EN: <修正后的英文>

要求:
1) 中文译文自然、准确, 专业术语用中文习惯表达;
2) **先在心里把听错的词改对, 再翻译改对后的句子**。中文必须译自修正后的读法 —— 你在 EN 行改掉的词,
   ZH 行绝不能还按错词直译(实测: EN 已修成 "the velocity", 中文却是"所以 74 速度…", 就是没做到这条);
3) 英文只修正明显的语音识别错听(音近词、漏词、专有名词、术语)。**优先采纳课程术语表里的词**:
   当某个词音近术语表条目时, 几乎可以断定是听错, 直接改成该术语。例如:
   "we're max chocolate properties" -> "we're macroscopic properties";
   "how we measure the stalcy" -> "how we measure the statistics";
   "average on the lacker level" -> "average on the lattice level";
4) 本来就正确、也不音近任何术语的词句**原样保留**, 不要改写、润色或调整语序 —— 防范过度矫正;
5) 输入若是名词列表/大纲词条(如课程名、术语串), 逐词直译即可,
   严禁输出"课程术语""翻译""总结"之类的概括性标题。"""


def domain_block(title: str) -> str:
    return f"Course: {title}\n" if title else ""


class Result:
    __slots__ = ("en_fixed", "zh")

    def __init__(self, en_fixed: str, zh: str):
        self.en_fixed = en_fixed
        self.zh = zh


def _has_cjk(s: str) -> bool:
    return any("一" <= c <= "鿿" for c in s)


def guard_zh_result(res: Result, en: str) -> Result:
    """本地引擎结果守卫: 译文里一个汉字都没有 = 模型把英文原样吐回来了
    (云端降级后的小模型实测会这样), 不是翻译。

    置空 zh 走"只显示英文"的既有路径 —— 屏上出现两行一样的英文, 用户会以为
    是翻译坏了; 空译文则明确表示"这句没翻出来"。云端路径有自己的回显检测
    (cloud_translator._looks_like_echo + 极简重试), 不走这里。"""
    if res.zh and not _has_cjk(res.zh):
        return Result(res.en_fixed, "")
    return res


def _load_terms(path: str | None) -> list[str]:
    """读一份术语表(每行一条, # 开头为注释)。读不出 -> 空列表并出声。

    ⚠️ 不裸读: 术语表是手写的(常从网页/Word 粘来), 可能不是 UTF-8; 也可能在
    exists() 之后被删或改权限。裸 read_text 会抛 UnicodeDecodeError/OSError,
    而这是启动路径 —— 术语表坏了不该让整节课起不来。"""
    if not path:
        return []
    p = pathlib.Path(path)
    if not p.exists():          # 没自备术语表是**正常**状态(公开仓库不含 glossary.txt)
        return []
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        print(f"⚠ 术语表读取失败, 本次忽略: {path} ({e})")
        return []
    return [t.strip() for t in text.splitlines()
            if t.strip() and not t.strip().startswith("#")]


#: 课型后缀 —— 带上它们的是**同一门课的分支**（习题课 / 研讨课 / 实验课）。
#: ⚠️ 作者的原话：「TUT 基本上就是在正课的基础上进行一些细化，所以共用一个会更好，
#:    因为都是正相关的术语。」
_CLASS_KINDS = frozenset((
    "tutorial", "tut", "lecture", "lec", "seminar", "sem",
    "lab", "workshop", "practice", "practical", "class",
))


def base_course(course: str) -> str:
    """`ECON10070 TUT` -> `ECON10070`；没有课型后缀就**原样返回**。

    ⚠️ 同时认两种真形态：
       · **括号**：`Introduction to Economics (Tutorial)`（UCD 课表里的样子）
       · **裸空格**：作者手打的 `ECON10070 TUT`
    ⚠️ 只剥**末尾**那一个词 —— 课名中间出现 "tutorial" 不该动它。
    """
    s = (course or "").strip()
    if not s:
        return s
    # ⚠️ 组里**不许含括号** —— 否则 `re.search` 会从**最早**那个空格起匹配，
    #    把 `to Economics (Tutorial` 整个吃进去（实测踩过）。
    m = re.search(r"\s*[（(]\s*([^（()）]+?)\s*[)）]\s*$", s)
    if m and m.group(1).strip().lower() in _CLASS_KINDS:
        return s[:m.start()].strip()
    parts = s.rsplit(None, 1)
    if len(parts) == 2 and parts[1].strip().lower() in _CLASS_KINDS:
        return parts[0].strip()
    return s


def course_terms_path(glossary_path: str, course: str) -> pathlib.Path | None:
    """定位分课程术语表 glossary/<课号>.txt。

    ⭐⭐ **课型后缀先剥掉、用主课那份**（2026-09-29 加，作者拍板）。
       `ECON10070 TUT` → 用 `glossary/ECON10070.txt`。
       理由见 `_CLASS_KINDS` 上面那句作者原话。
       ⚠️ **主课优先**：两个文件都在时用主课那份（作者拍板）——
          所以 `ECON10070 TUT.txt` 会被**忽略**；那是刻意的（"共用一个会更好"）。
       ⚠️ 主课那份**不存在**时才落回全名那份 —— 不让人白建的表变成死文件。

    ⚠️ 容错: 用户常用短代号(`cl course 10202` 会把 `10202` 写进 .course), 而文件是
    全名 `ECON10202.txt` —— 严格拼路径会**静默落空**(实测只加载到公共术语,
    课程术语一条没进)。故精确路径不存在时, 退而在 glossary/ 里按**后缀**匹配;
    仅当唯一命中时才采用(防 `202` 这类歧义前缀误配)。
    """
    d = pathlib.Path(glossary_path).parent / "glossary"
    _base = base_course(course)
    if _base and _base != course:
        _main = d / f"{_base}.txt"
        if _main.exists():
            return _main
    exact = d / f"{course}.txt"
    if exact.exists():
        return exact
    if not d.is_dir():
        return None
    hits = [p for p in sorted(d.iterdir())
            if p.suffix == ".txt" and p.stem.endswith(course)]
    return hits[0] if len(hits) == 1 else None


def merge_terms(*groups) -> list:
    """把几组词拼起来并按 `lower` **去重保序** —— 「拼接术语」的**唯一定义点**。

    ⚠️⚠️ 2026-09-29 抽出来的理由：`load_terms(...) + list(extra_terms)` 这种
       **外面拼**的写法会**绕过** `load_terms` 内部的去重 —— 转录词与术语表撞词时
       池子里会有两条，而 `select_terms` 是 `scored[:max_dyn]` **先截断、再去重**
       → **重复条目白占一个动态名额**。
       实测：池子里放两条 `marginal`（`max_dyn=3`）→ 只注入了 **2** 个，本该 3 个。
    ⚠️ 两处（本地 / 云端）都要走它 —— 各写一份迟早分叉。
    """
    seen, out = set(), []
    for g in groups:
        for t in g or ():
            k = str(t).lower()
            if k not in seen:
                seen.add(k)
                out.append(t)
    return out


def load_terms(glossary_path: str | None, course: str | None = None) -> list[str]:
    """公共术语 + 分课程术语(glossary/<课号>.txt)。

    ⚠️ 2026-10-17：分课程那一半走 `course_term_list`（带**加载上限与排序**）——
       公共表是手写的、几十条封顶，不设上限；本课表会长（`prep` + 笔记都在追加），
       所以它必须与 `always` 那一档**同一份口径**（否则动态召回池还会全量吃下全表）。
    """
    g = [_load_terms(glossary_path)]
    g.append(course_term_list(glossary_path, course))
    return merge_terms(*g)


#: 分课程术语**加载上限**（2026-10-17 加；作者约束：术语库不能越堆越多）。
#:
#: 为什么要有上限：这些词**每句全量注入**（`select_terms` 的 `always`），
#: 而 `prep.py` 自己量过：36 条 ≈ 597 字符 ≈ 整条 prompt 的 23%，120 条 ≈ 60%。
#: 上限取 60 与 `prep.DEFAULT_MAX_TOTAL` 同量级（那条是"往表里加"的上限，
#: 这条是"从表里读"的上限）——两条一起保证"表再长，每句也只带 60 条"。
TERM_LOAD_LIMIT = 60


def _recency_rank(terms: list, state_path) -> list:
    """术语表 → **加载顺序**：手写的在前，自动追加的按**新近度**（日期倒序）。

    ⭐ 为什么要分两段（`prep-state.json` 的 `appended` 就是"哪些是自动加的 + 哪天加的"）：
      · 手写那一段是课的**领域先验**（课名、`derivative`、`optimization` 这种），
        它不该被后来的自动词挤掉 —— 挤掉了 Soniox/翻译就丢了领域；
      · 自动追加的那一段会**一直变长**（`prep` 从课件抽、笔记收尾再从转录抽），
        而"这节课要用的是最近几次课长出来的词" —— 所以新的排前面，撞上限时
        掉出去的是**最旧的**（作者口径「超出淘汰最旧/最少的」）。
      · 拿不到 state（用户手写了全表 / 换机器）→ 全表当手写，按文件顺序 ——
        仍然受 `limit` 约束，只是淘汰的是**尾部**（追加写在尾部）。
    """
    if state_path is None or not terms:
        return list(terms)
    import prep
    try:
        state = prep._load_state(pathlib.Path(state_path)) or {}
    except Exception:                                          # noqa: BLE001
        state = {}
    if not isinstance(state, dict) or not state:
        return list(terms)
    added = {str(k).lower(): str(v) for k, v in state.items()}
    hand = [t for t in terms if t.lower() not in added]
    auto = [t for t in terms if t.lower() in added]
    # ⚠️ 日期倒序、**同日保文件顺序**（`sorted` 稳定）—— 同一节课加的那批里，
    #    先抽到的在前（与 `note_glossary` 追加顺序一致，可解释）。
    auto.sort(key=lambda x: added.get(x.lower(), ""), reverse=True)
    return hand + auto


def course_term_list(glossary_path: str | None, course: str | None, *,
                     limit: int | None = TERM_LOAD_LIMIT,
                     state_path=None) -> list[str]:
    """**只**取分课程术语(不含公共表)。这些词每句全量注入 —— 见 select_terms 的说明。

    ⭐ 2026-10-17：**加载有上限**（`TERM_LOAD_LIMIT`，默认 60 条），排序见
       `_recency_rank`（手写在前、自动追加按新近度）。`limit=None` = 不设限
       （判据/导出/面板显示这类"要全表"的场合用它）。
    ⚠️ 上限的作用是**每句注入的量**：表可以一直追加，但每句只带 60 条，
       多出来的（最旧的自动词）不加载 —— 见 `docs/HANDBOOK.md` 的账。
    """
    if not course or not glossary_path:
        return []
    p = course_terms_path(glossary_path, course)
    if p is None:
        return []
    terms = _load_terms(str(p))
    if state_path is None:
        # 状态路径的**唯一入口**是 `paths.prep_state`（别在这里手拼 `.classlive/…`）。
        try:
            import paths
            state_path = paths.prep_state(course)
        except Exception:                                      # noqa: BLE001
            state_path = None
    ranked = _recency_rank(terms, state_path)
    return ranked if limit is None else ranked[:max(0, int(limit))]


def course_title(glossary_path: str | None, course: str | None) -> str:
    """从分课程术语表里取课程标题, 当**领域先验**用。

    例: "# ECON10101 Data Analysis for Economists(经济数据分析)" ->
        "ECON10101 Data Analysis for Economists(经济数据分析)"。
    ⚠️ 为什么要它: 模型知道"这是热力学课"才修得回听错的领域词(macroscopic / enthalpy),
    光给术语表不够 —— 被听错的词根本匹配不上术语表(见 select_terms)。"""
    if not course or not glossary_path:
        return ""
    p = course_terms_path(glossary_path, course)
    if p is None:
        return ""
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("#"):
                return s.lstrip("#").strip()
            if s:
                return s
    except (OSError, UnicodeDecodeError):
        # ⚠️ UnicodeDecodeError 必须一起接: 术语表是手写的, 可能不是 UTF-8。
        # 它在 __init__ 的启动路径上 —— 漏掉就是"术语表编码不对 -> 整节课起不来"。
        # (`_load_terms` 一直是这么接的, 这里当初漏了。2026-09-24 OCR 发现。)
        return ""
    return ""


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


def term_zh_map(glossary_path: str | None, course: str | None, *,
                notes=None, terms: list | None = None) -> dict[str, str]:
    """本课「英文 → 中文译名」表（译名注入的数据源）。**永不抛**（拿不到 → `{}`）。

    数据源（2026-10-17 查证）：
      · 笔记 v2 的中文对照走 `build_notes.TermNotes` 读 `term_notes.json`
        （basic = 一行速查 / gloss = 完整解析）—— **只取 basic 档**：短、正是要的"中文名"；
      · `term_notes_auto.json`（运行时缓存）里 `add()` 写的一律是 gloss 档 → 天然不进；
      · 复习层 `terms[{en, zh}]`（`note_v2`）只在收尾生成、不落盘 → 课上读不到，不用。

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
        import build_notes
        ts = list(terms) if terms is not None else course_term_list(glossary_path, course)
        if not ts:
            return {}
        if notes is None:
            # ⚠️ curated 文件不存在就别构造 TermNotes —— 它构造时会打印"术语表未加载"
            #    警告；课上用不了译名是**常态**（用户可能根本没有这份文件），
            #    不该每次开课都吓一跳。auto 缓存里没有 basic 档，跳过它不丢东西。
            if not pathlib.Path(build_notes.NOTES_FILE).exists():
                return {}
            notes = build_notes.TermNotes()
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


def core_terms(course: str | None = None) -> list[str]:
    """常驻核心词: 通用核心 + 当前课号(课号发音极近, 必须常驻防听混)。"""
    core = list(CORE_TERMS)
    if course and course not in core:
        core.append(course)
    return core


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
    相似度也较高, 能被召回。"""
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
    # ⭐ 可选的只读计数（`stats` 由调用方给，**不改返回值契约**）。
    #    用途：判断「术语系统值不值」—— 见记忆 `classlive-term-injection-cost`。
    #    ⚠️ 记的是**去重前**每一档贡献了几条 + 去重后实际注入几条 —— 两个都要，
    #       差值就是三档之间的重复量（重复说明表该去重了）。
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


DRAFT_SYSTEM = "你是实时字幕翻译器。把用户给的英文口语快速译成中文, 只输出中文译文。"

# 只矫正、不翻译(关闭翻译时用): 同一个模型、同一份上下文, 只是不要中文。
FIX_SYSTEM = """你是英文课堂字幕校正助手。给你一句 ASR 转写的英文、课程领域、本课术语表, 以及最近几句上下文。
请只修正明显的语音识别错听(音近词、漏词、专有名词、术语)。**优先采纳课程术语表里的词**:
当某个词音近术语表条目时, 几乎可以断定是听错, 直接改成该术语。例如:
"we're max chocolate properties" -> "we're macroscopic properties";
"how we measure the stalcy" -> "how we measure the statistics";
"average on the lacker level" -> "average on the lattice level"。
本来就正确、也不音近任何术语的词句**原样保留**, 不要改写、润色、缩写或调整语序(防范过度矫正)。
只输出修正后的那一句英文, 不要任何前缀、标签、解释或中文。"""

# 模型偶尔给结果加标签(EN: / 修正后: …), 或干脆跑偏去翻译/概括。清一遍。
_FIX_LABELS = ("EN:", "EN：", "FIXED:", "CORRECTED:", "OUTPUT:",
               "英文:", "英文：", "修正后:", "修正后：", "矫正后:")


# ---- 本地小模型的输出后处理（思路来自 six-ddc/livecaption 的读码，按本项目数据重新量过阈值）----
_META_PREFIX = re.compile(
    r"^\s*(?:好的[，,。！!]?\s*)?(?:以下是|下面是|这是|这句的)?"
    r"(?:中文)?(?:译文|翻译结果|翻译|中文)\s*[：:]\s*")


def clean_local_zh(zh: str) -> str:
    """剥掉小模型爱加的「以下是译文：」之类的元话；其余原样。只认"前缀 + 冒号"，不会动正文。"""
    out = _META_PREFIX.sub("", zh or "", count=1).strip()
    return out or (zh or "").strip()


#: 译文与**上一句译文**的字符二元组重合 ≥ 此值 → 判为"模型翻的是上下文，不是这一句"。
#: 0.45 取自 livecaption；本项目的真实数据复核（2026-10-08，39 节课 4780 对相邻句，只读）：
#: 0.45 误伤 0.54%（26/4780，多是讲师真的重复）；0.35 为 1.1%；0.25 为 3.5%。所以沿用 0.45。
ECHO_BIGRAM = 0.45


def zh_echoes_previous(zh: str, prev: str) -> bool:
    if len(prev) < 16 or len(zh) < 2:
        return False
    a = {prev[i:i + 2] for i in range(len(prev) - 1)}
    b = {zh[i:i + 2] for i in range(len(zh) - 1)}
    return len(a & b) >= ECHO_BIGRAM * len(a)


def _clean_fix(text: str, raw: str) -> str:
    """矫正结果归一; 结果不可用时回退原始 ASR(与翻译路径同规矩: 宁可留原样)。
    判为不可用的三种情况: 空、混入中文(说明它跑偏去翻译了)、明显变长(说明在改写
    而不是矫正 —— 本功能的承诺就是"正确的词原样保留")。"""
    t = " ".join((text or "").split())
    up = t.upper()
    for lab in _FIX_LABELS:
        if up.startswith(lab.upper()):
            t = t[len(lab):].strip()
            break
    t = t.strip().strip('"“”\'`').strip()
    if not t or any("一" <= c <= "鿿" for c in t):
        return raw
    if len(t.split()) > 1.8 * len(raw.split()) + 3:
        return raw
    return t


def _apply_chat_generic(toks, user_content: str, system: str) -> str:
    """套 chat template(带 enable_thinking=False 的兼容回退)。"""
    if getattr(toks, "chat_template", None):
        msgs = [{"role": "system", "content": system},
                {"role": "user", "content": user_content}]
        try:
            return toks.apply_chat_template(
                msgs, tokenize=False, add_generation_prompt=True,
                enable_thinking=False)
        except TypeError:
            return toks.apply_chat_template(
                msgs, tokenize=False, add_generation_prompt=True)
    return user_content


class _StreamParser:
    """把流式文本按 ZH: / EN: 切段, 逐段回调。
    容错: 标记被 token 切开、模型不按格式输出(无标记)、英文段混入中文。"""

    def __init__(self, on_zh, on_en):
        self.on_zh, self.on_en = on_zh, on_en
        self.state = "wait_zh"
        self._zh: list[str] = []
        self._en: list[str] = []
        self._buf = ""
        self._pre: list[str] = []          # 未出现 ZH: 前的累积(防丢弃)

    @staticmethod
    def _find_marker(s: str, marker: str) -> int:
        i = s.find(marker)
        j = s.find(marker.replace(":", "："))     # 容忍全角冒号
        if i == -1:
            return j
        if j == -1:
            return i
        return min(i, j)

    def feed(self, delta: str) -> None:
        if not delta:
            return
        self._buf += delta
        while True:
            if self.state == "wait_zh":
                k = self._find_marker(self._buf, "ZH:")
                if k == -1:
                    # 保留尾部(可能含被切开的标记), 其余累积;
                    # 攒够长度仍无标记 -> 认定模型没按格式, 全当中文
                    if len(self._buf) > 3:
                        emit, self._buf = self._buf[:-3], self._buf[-3:]
                        if emit:
                            self._pre.append(emit)
                    if sum(len(x) for x in self._pre) >= 24:
                        text = "".join(self._pre + [self._buf]).lstrip("：: ").strip()
                        self._pre, self._buf = [], ""
                        if text:
                            self._zh.append(text); self.on_zh(text)
                        self.state = "in_zh"
                    return
                self._pre = []                    # 有标记 -> 丢弃前缀噪声
                self._buf = self._buf[k + 3:]
                self.state = "in_zh"
                continue
            if self.state == "in_zh":
                k = self._find_marker(self._buf, "EN:")
                if k == -1:
                    if len(self._buf) > 3:        # 留 3 字符防 "EN:" 被切开
                        emit, self._buf = self._buf[:-3], self._buf[-3:]
                        if emit:
                            self._zh.append(emit); self.on_zh(emit)
                    return
                emit = self._buf[:k].rstrip()
                self._buf = self._buf[k + 3:]
                if emit:
                    self._zh.append(emit); self.on_zh(emit)
                self.state = "in_en"
                continue
            if self.state == "in_en":
                if self._buf:
                    self._en.append(self._buf); self.on_en(self._buf); self._buf = ""
                return

    @staticmethod
    def _has_cjk(s: str) -> bool:
        return _has_cjk(s)

    def result(self, en_raw: str) -> Result:
        if self._buf:                              # 收尾: 未消费的尾巴
            if self.state == "in_en":
                self._en.append(self._buf); self.on_en(self._buf)
            elif self.state == "in_zh":
                self._zh.append(self._buf); self.on_zh(self._buf)
            elif self.state == "wait_zh":
                self._pre.append(self._buf)
        zh = "".join(self._zh + self._pre).strip().lstrip("：: ").strip()
        en = "".join(self._en).strip()
        # 英文段混入中文(模型跑偏) -> 用 ASR 原文兜底
        if not en or self._has_cjk(en):
            en = en_raw
        return Result(en_fixed=en, zh=zh or en_raw)


def _configure_mlx() -> None:
    """给 MLX 设**宽松**的缓冲缓存上限, 减少 Metal 缓冲的释放/重建次数。

    Why: 2026-09-10 本机发生过一次 IOGPUFamily 驱动断言内核崩溃
    (panic-full-2026-09-10-004444, 肇事进程即本程序), 特征是**内存记账状态损坏**
    而非 OOM(压缩器仅 11%、swap 正常)。已知 MLX 触发同类驱动 panic 的 issue
    (mlx-lm#883 / mlx#3186) 指向 Metal 缓冲的 load/unload 竞态。
    因此: 缓存上限设**宽松**(缓冲复用而非反复释放), 且**不主动 clear_cache**。
    """
    try:
        import mlx.core as mx
        mx.set_memory_limit(6 * 1024 ** 3)       # 仅作上限保护
        mx.set_cache_limit(2 * 1024 ** 3)        # 宽松: 鼓励复用, 减少释放/重建
    except Exception:                             # noqa: BLE001
        pass


def local_model_present() -> bool:
    """本地 Qwen 兜底模型**在不在** —— `doctor.model_present` 的薄包装（判据只此一份）。

    ⚠️ 惰性 import：`doctor` 与 `update`/`ready` 之间有已知的惰性环（模块地图有记），
       模块级 import 会把 translator 拖进去 —— 函数内 import 没这个问题。
    ⚠️ **任何异常都当「在」**（返回 True）—— 同 `doctor.model_present` 那条
       「不确定就别报缺」的纪律：判据自己坏了宁可按原样去试 load，也不许拿它拦人。
    """
    try:
        import doctor
        import models
        return doctor.model_present(models.QWEN.path)
    except Exception:                             # noqa: BLE001
        return True


class Translator:
    """懒加载 mlx-lm 模型; 生成用锁串行; chat template 提升指令遵循。"""

    def __init__(self, model: str, glossary_path: str | None, max_context: int = 2,
                 course: str | None = None, extra_terms: list | None = None,
                 term_zh: dict | None = None):
        self._model = None
        self._tokenizer = None
        #: ⭐ 本地加载的**失败闩锁**（2026-10-01 审查 F2）：一旦确定「装不了 / 装不上」，
        #:   之后每次调用都**立刻**抛同一句原因，不再重试 `load()` —— 原来云端失败后
        #:   每一句都去 `mlx_lm.load()`（在线 = 隐式拉 938 MB 把定稿线程卡住；离线 =
        #:   每句白等一次失败）。进程重启才清（中途装好模型需要重开，可接受）。
        self._load_err: str | None = None
        # ⚠️⚠️ **`extra_terms` 只进「候选池」，绝不进 `course_terms`**（2026-09-29）。
        #    池子里的词按相似度**动态召回**（`select_terms` 的 `scored[:max_dyn]`），
        #    而 `course_terms` 是**每句全量注入**的。
        #    实测（作者的 ECON10770/10740，各 448/1003 句真实课堂）：
        #      · 池子 +25 个转录词 → 注入结果**变了 63–74% 的句子**（新词真被召回）
        #      · ⚠️ 但手写术语**只被挤掉 3–4%** → **名额不用改**
        #    而如果把词**写进术语表文件**（走全量注入），120 条时术语块占整条
        #    prompt 的 **60%**（实测 1544 字符 / 总 ~2582 字符）→ 那条路会过量。
        self._terms = merge_terms(load_terms(glossary_path, course), extra_terms)
        self._course_terms = course_term_list(glossary_path, course)
        #: ⭐ #4 B2：本课「英文 → 中文」译名表（`term_zh_map` 建好后由工厂传进来）。
        #:    命中的条目按 `select_term_zh` 注入每句 prompt 的**变化段**（不注入全表）。
        self._term_zh = dict(term_zh or {})
        #: ⭐ 术语注入的**只读计数**（`select_terms` 填）。
        #: ⚠️ 名字与 `cloud_translator.CloudTranslator.term_stats` **必须一致** ——
        #:    `main.py` 收尾时两个引擎各读一次再合并（它俩本来就各有一份）。
        self.term_stats: dict = {}
        self._domain = course_title(glossary_path, course)
        self._core = core_terms(course)
        self._max_ctx = max_context
        self._lock = threading.Lock()
        self._load_lock = threading.Lock()   # 保护 _ensure 的懒加载(见该处说明)
        self._model_name = model
        self._last_zh = ""                   # 上一句最终译文（回显检测用）
        self.echo_retries = 0                # 只读计数：因"翻成上一句"重试过几次

    def _ensure(self) -> None:
        """懒加载(双重检查加锁)。

        ⚠️ 必须自己加锁: 4 个调用点(fix_and_translate_stream / fix_stream /
        translate_draft / warmup)都在 `with self._lock` **之外**调它, 而草稿线程与
        定稿线程会并发进来。原先只做 check-then-act: 两线程可能同时看到
        `_model is None` 而**重复 load()**(显存翻倍); 更糟的是
        `self._model, self._tokenizer = load(...)` 是两条 STORE_ATTR, 另一线程可能
        读到 `_model` 已赋值而 `_tokenizer` 仍是 None -> `_apply_chat` 里
        `apply_chat_template` 抛 AttributeError。
        (2026-09-24 OCR 全量审计发现。)"""
        if self._model is None:
            with self._load_lock:
                if self._model is None:
                    # ⚠️⚠️ 两道**不联网就能答**的闸（2026-10-01 审查 F2）：
                    #    ① 闩锁：已经确定装不上 → 立刻抛同一句，不再试 `load()`；
                    #    ② 在不在：**先问 `doctor.model_present`** —— 没装就闩住
                    #       「本地不可用」。绝不让 `mlx_lm.load()` 顺手去 HF 隐式
                    #       下载 938 MB（「模型绝不自动下」那条铁律在运行期的缺口；
                    #       离线时更是**每一句**都白等一次 load 失败）。
                    if self._load_err is not None:
                        raise RuntimeError(self._load_err)
                    if not local_model_present():
                        self._load_err = (
                            "本地模型没装（可选）；跑 `cl doctor` 看怎么装")
                        raise RuntimeError(self._load_err)
                    _configure_mlx()
                    # ⚠️ **赋值顺序是这里的关键**: 必须先 tokenizer、后 model。
                    # 上面那句 `if self._model is None` 是**无锁**读的 —— 它把
                    # `_model` 当成就绪标志。原来写成 `self._model, self._tokenizer
                    # = load(...)`, 是两条 STORE_ATTR 且 **model 在前**: 另一线程可能
                    # 恰在两条之间读到 `_model` 已非 None, 于是跳过整段、拿着还是 None
                    # 的 `_tokenizer` 去 `apply_chat_template` → AttributeError。
                    # 换成 tokenizer 先落地, 则"看到 _model 有值"就等于"两样都齐了"。
                    try:
                        # ⚠️ 取数期间**强制离线**（2026-10-01 深度审查）：上面那道
                        #    「在不在」是启发式（目录里任一非空文件就算在），半截缓存
                        #    它能放行，然后 `load()` 对着 HF 的 repo id **联网补全**
                        #    —— 「绝不自动下」真正的关门点在**会下载的那一层**。
                        #    模型齐 → 离线可加载；不齐 → 立刻失败进闩（消息会出声）。
                        #    ⚠️ 只影响本进程：`cl doctor` 的下载走独立子进程，不受影响。
                        import os as _os
                        _os.environ.setdefault("HF_HUB_OFFLINE", "1")
                        from mlx_lm import load
                        _m, _tok = load(self._model_name)
                    except Exception as e:                    # noqa: BLE001
                        # ⚠️ 失败也上闩（2026-10-01 审查 F2）：原来每次调用都重试
                        # `load()` —— 云端挂着的时候那是**每一句**一次失败尝试。
                        self._load_err = (f"本地模型加载失败({type(e).__name__}: "
                                          f"{str(e)[:80]})")
                        raise
                    self._tokenizer = _tok
                    self._model = _m

    def warmup(self) -> None:
        self._ensure()

    def local_ready(self) -> bool:
        """本地引擎**现在**能不能用（只看在不在 / 闩没闩，**不加载**）。

        ⚠️ `EngineRouter._fallback` 的广播用它说实话（2026-10-01 审查 F2）：
           本地模型是**可选**的，没装还说「已降级本地引擎」会让用户以为有兜底。
        """
        if self._load_err is not None:
            return False
        return local_model_present()

    def _apply_chat(self, user_content: str, system: str = SYSTEM_PROMPT) -> str:
        return _apply_chat_generic(self._tokenizer, user_content, system)

    def add_terms(self, words) -> list:
        """把**课上现出现的候选词**并进候选池（链路体检 ⭐2）。返回真加进去的那些。

        ⚠️ 走 `merge_terms` —— 池子的**唯一定义点**（理由见它的 docstring：外面拼
           会绕过 `load_terms` 的去重，重复条目白占一个 `max_dyn` 名额）。
        ⚠️ 只进候选池（`dyn` 那一档），**不进 `course_terms`**（每句全量注入那档）——
           这条纪律 2026-09-29 就定了（见 `__init__` 里那段实测）。
        ⚠️ 每句都会读 `self._terms`，所以**中途追加立刻生效**，不需要重启引擎。
        """
        new = [w for w in merge_terms(words) if w.lower() not in
               {t.lower() for t in self._terms}]
        if new:
            self._terms = merge_terms(self._terms, new)
        return new

    def _terms_context(self, en: str, context: list[str], zh: bool = True) -> str:
        ctx = context[-self._max_ctx:] if self._max_ctx > 0 else []
        ctx_block = "\n".join(f"- {c}" for c in ctx) if ctx else "(无)"
        terms = select_terms(en, self._terms, core=self._core,
                             always=self._course_terms, stats=self.term_stats)
        # ⭐ #4 B2：译名段 = 逐句变化的内容 → 放在上下文之后、待处理这句之前。
        #    `zh=False`（只矫正英文那些调用点）：EN-only 模式没有译文，不注入译名（白占 token）。
        zblock = term_zh_block(en, self._term_zh) if zh else ""
        return (f"{domain_block(self._domain)}"
                f"课程术语:\n{terms}\n\n"
                f"最近上下文(已校正):\n{ctx_block}\n\n"
                f"{zblock}"
                f"待处理这句 ASR:\n{en}")

    def _user_content(self, en: str, context: list[str]) -> str:
        return self._terms_context(en, context)

    # ---- 流式(主用) ----
    def fix_and_translate_stream(self, en: str, context: list[str],
                                 on_zh=None, on_en=None, _retried: bool = False) -> Result:
        from mlx_lm import stream_generate
        from mlx_lm.sample_utils import make_sampler
        self._ensure()          # auto 模式下云端首次失败才走到这里, 那时模型还没加载
        prev_zh = self._last_zh                  # 回显检测的参照：进来时的"上一句"，重试也比这个
        parser = _StreamParser(on_zh or (lambda s: None), on_en or (lambda s: None))
        prompt = self._apply_chat(self._user_content(en, context))
        with self._lock:
            for resp in stream_generate(
                    self._model, self._tokenizer, prompt=prompt,
                    max_tokens=220, sampler=make_sampler(temp=0.2, top_p=0.9)):
                parser.feed(getattr(resp, "text", "") or "")
            # 刻意**不**调用 mx.clear_cache(): 频繁释放/重建 Metal 缓冲是
            # IOGPUFamily 驱动断言的已知竞态窗口(见 _configure_mlx 注释)。
        res = guard_zh_result(parser.result(en), en)
        res = Result(res.en_fixed, clean_local_zh(res.zh))
        # 翻成了上一句 → 不带上下文重来一次（只重试一次；仍是回显就保留，宁可有译文）。
        # 屏上已流出的那版会被定稿覆盖，这里只管最终 res。
        if context and not _retried and zh_echoes_previous(res.zh, prev_zh):
            retry = self.fix_and_translate_stream(en, [], None, None, _retried=True)
            self.echo_retries += 1
            if retry.zh and not zh_echoes_previous(retry.zh, prev_zh):
                res = retry
        if not _retried:
            self._last_zh = res.zh or self._last_zh
        return res

    # ---- 只矫正英文(关闭中文翻译时用): 不出译文, 但仍吃上下文修 ASR 错听 ----
    def fix_stream(self, en: str, context: list[str], on_en=None) -> str:
        """返回上下文矫正后的英文。失败由调用方兜底(保留原始 ASR)。"""
        from mlx_lm import stream_generate
        from mlx_lm.sample_utils import make_sampler
        self._ensure()
        prompt = self._apply_chat(self._terms_context(en, context, zh=False), FIX_SYSTEM)
        buf: list[str] = []
        with self._lock:
            for resp in stream_generate(
                    self._model, self._tokenizer, prompt=prompt,
                    max_tokens=180, sampler=make_sampler(temp=0.1, top_p=0.9)):
                d = getattr(resp, "text", "") or ""
                if d:
                    buf.append(d)
                    if on_en:
                        on_en(d)
        return _clean_fix("".join(buf), en)

    # ---- 非流式封装(测试/兼容) ----
    def fix_and_translate(self, en: str, context: list[str]) -> Result:
        return self.fix_and_translate_stream(en, context)

    # ---- 草稿轻量翻译(无术语表/无上下文, 只为尽早给一版中文) ----
    def translate_draft(self, en: str, on_zh=None) -> str:
        from mlx_lm import stream_generate
        from mlx_lm.sample_utils import make_sampler
        self._ensure()
        # ⭐ #4 B2：命中的译名放在这句之前（草稿 prompt 只有一句，译名段就是它的"变化段"）。
        #    无命中 → ""（与旧行为逐字相同）。
        user = term_zh_block(en, self._term_zh) + en
        prompt = _apply_chat_generic(self._tokenizer, user, DRAFT_SYSTEM)
        buf: list[str] = []
        with self._lock:
            for resp in stream_generate(self._model, self._tokenizer, prompt=prompt,
                                        max_tokens=120,
                                        sampler=make_sampler(temp=0.1, top_p=0.9)):
                buf.append(getattr(resp, "text", "") or "")
        text = "".join(buf).strip()
        if on_zh and text and _has_cjk(text):    # 英文回显不是译文, 不上屏
            on_zh(text)
        return text


def load_translator(model: str, glossary_path: str | None, max_context: int = 2,
                    course: str | None = None, extra_terms: list | None = None,
                    term_zh: dict | None = None):
    return Translator(model, glossary_path, max_context, course=course,
                      extra_terms=extra_terms, term_zh=term_zh)
