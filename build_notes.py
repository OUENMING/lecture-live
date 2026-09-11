"""术语通俗解释: 离线预生成 术语->分层解析, 运行时只查表(零延迟)。

用法:
  python build_notes.py             # 增量: 给缺 level/detail 的术语补齐
  python build_notes.py SOC10101    # 只为某门课
  python build_notes.py --rebuild   # 全量重分类+重展开, 并清掉历史自动缓存的垃圾条目

数据分两层:
  term_notes.json       — 人工/构建管理, 三层结构 (skip/basic/gloss)
  term_notes_auto.json  — 运行时后台查到的专有名词缓存, 独立文件, 可直接删除

条目结构:
  {"level": "gloss", "detail": "首句是独立定义, 其后展开 80-160 字"}
  {"level": "basic", "detail": "一行速查(≤30 字)"}  — 基础概念, 命中显示一行
  {"level": "skip"}    — 教务/课号/常识词, 永不解释

旧格式 {"term": "解释"} 仍可读, 一律当作 gloss。
"""
from __future__ import annotations
import datetime, json, pathlib, re, sys

HERE = pathlib.Path(__file__).parent
NOTES_FILE = HERE / "term_notes.json"
AUTO_FILE = HERE / "term_notes_auto.json"

BATCH_CLASSIFY = 25   # 每次请求分类多少个术语
BATCH_EXPAND = 8      # 每次请求展开多少个术语(输出长, 批量要小)
MAX_TOKENS_EXPAND = 3000

LEVELS = ("skip", "basic", "gloss")
TYPES = ("person", "org", "place", "concept")

# 非 glossary 但确认有价值的术语(历史上由自动查询产生, 人工保留)。
# --rebuild 会保留它们并强制 gloss, 避免"清垃圾"时误伤。
KEEP_EXTRA = ("Byzantine Empire", "Catholic Church", "Roman Empire",
              "Constantinople", "Islam", "United Irishmen")

SYS_CLASSIFY = """你在为一名靠中文听英文课的中国经济学/社会学本科生准备课堂术语表。
判断标准是**中英对照价值**, 不是"这个概念难不难":
- skip: 教务/行政词(如 module, lecture, workshop, deadline, reading week)、课程编号(如 ECON10101)、
  以及任何非专业人士早就知道的常识词(如 welcome, everyone, yeah, four)。这些照抄中文即可, 不解释。
- basic: 本课程的基础概念(如 demand, marginal cost, standard deviation, regression)。学生未必不懂,
  但英文词与中文术语的对应要即时给出 —— 命中后显示**一行速查**。
- gloss: 学生现有知识之外、真正需要展开的难点概念, 或需要背景知识的人名/机构/地名 —— 命中后
  显示**完整解析**。
返回严格 JSON: {"术语": "skip|basic|gloss", ...}, 值只能是这三个词之一。不要输出任何其他内容。"""

SYS_EXPAND_SHORT = """你在为中文大学生写课堂术语的**一行速查**。输入是一个英文术语列表。
硬性要求:
1. **JSON 的键必须原样照抄输入的英文术语** —— 不改大小写、不翻译、不增删词。
2. 值是**一行中文**: 以对应的中文术语开头, 后接一句话说清它是什么,
   形如 "marginal cost" -> "边际成本：多生产一单位产品所增加的成本"。
3. 一行, 不超过 30 个中文字; 不要换行, 不要 markdown, 不要引号,
   不要"这个概念""它"之类不自足的开头。
返回严格 JSON: {"<英文术语原样>": "<一行中文释义>", ...}。"""

SYS_EXPAND = """你在为中文大学生写课堂术语解析。对每个术语写一段 80-160 个中文字符的解析。
硬性要求:
1. 第一句必须是一句话的、能脱离上下文独立读懂的完整定义 —— 折叠界面只显示第一句,
   所以禁止用"这个""它""该概念"开头, 必须自足。
2. 第一句之后展开: 为什么重要 / 在本课程中怎么用 / 常见误解。
3. 用词通俗, 不要 markdown, 不要引号, 不要换行。
返回严格 JSON: {"术语": "解析", ...}。"""

SYS_LOOKUP = """你负责判断一个英文词/短语是否真实存在, 并给出解析。
规则:
- 只有当你确信它是真实存在的人名/机构/地名/学科概念时才返回 known=true。
- 如果它像是语音识别误听、拼写错误、虚构名字, 或你根本不认识, 必须返回 known=false, 绝不编造。
- type 只能是 person / org / place / concept 之一, 不确定就返回 known=false。
返回严格 JSON: {"known": true, "type": "person|org|place|concept", "note": "解析"}
note 要求: 80-160 个中文字符; 第一句是一句话的完整定义、能独立读懂; 其后展开它是什么/为什么重要/常见误解。
只输出 JSON。"""


# ---- 读写 / 结构规范化 ----

def load_raw(path: pathlib.Path = NOTES_FILE) -> dict:
    if path.exists():
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    return {}


def normalize_entry(v) -> dict:
    """单条目 -> {"level":..., "detail":...}, 未知键保留。"""
    if isinstance(v, dict):
        e = dict(v)
        if e.get("level") not in LEVELS:
            e["level"] = "gloss" if e.get("detail") else "skip"
        return e
    if isinstance(v, str) and v.strip():          # 旧格式: 字符串即 gloss 解析
        return {"level": "gloss", "detail": v.strip()}
    return {"level": "skip"}


def normalize(raw: dict) -> tuple[dict, dict, dict, bool]:
    """-> (meta, terms, extra_top_keys, was_new_format)。旧格式不崩溃、不丢键。"""
    meta, terms, extra = {}, {}, {}
    if isinstance(raw.get("terms"), dict):
        for k, v in raw.items():
            if k == "terms":
                continue
            if k == "_meta":
                meta = dict(v) if isinstance(v, dict) else {}
            else:
                extra[k] = v
        for k, v in raw["terms"].items():
            terms[k] = normalize_entry(v)
        return meta, terms, extra, True
    for k, v in raw.items():                      # 旧扁平格式
        if k.startswith("_"):
            extra[k] = v
        else:
            terms[k] = normalize_entry(v)
    return meta, terms, extra, False


def dump(meta: dict, terms: dict, extra: dict) -> dict:
    out = {"_meta": meta, "terms": terms}
    out.update(extra)
    return out


def save_to(path: pathlib.Path, meta: dict, terms: dict, extra: dict = None) -> None:
    path.write_text(json.dumps(dump(meta, terms, extra or {}),
                               ensure_ascii=False, indent=1), encoding="utf-8")


def collect_terms(course: str | None = None) -> list[str]:
    from translator import load_terms
    terms = load_terms(str(HERE / "glossary.txt"), course)
    if course is None:
        for f in sorted((HERE / "glossary").glob("*.txt")):
            terms += load_terms(str(HERE / "glossary.txt"), f.stem)
    seen, out = set(), []
    for t in terms:
        k = t.lower()
        if k not in seen and len(t) > 2:
            seen.add(k); out.append(t)
    return out


# ---- API ----

def _chat_json(key: str, model: str, system: str, user: str,
               max_tokens: int, temperature: float = 0.2) -> dict | None:
    import httpx
    payload = {"model": model, "stream": False, "max_tokens": max_tokens,
               "temperature": temperature, "thinking": {"type": "disabled"},
               "response_format": {"type": "json_object"},
               "messages": [{"role": "system", "content": system},
                            {"role": "user", "content": user}]}
    r = httpx.post("https://api.deepseek.com/v1/chat/completions",
                   headers={"Authorization": f"Bearer {key}"},
                   json=payload, timeout=180)
    r.raise_for_status()
    obj = json.loads(r.json()["choices"][0]["message"]["content"])
    return obj if isinstance(obj, dict) else None


def _pick(obj: dict, term: str):
    return obj.get(term, obj.get(term.lower(), obj.get(term.upper())))


def build(course: str | None = None, api_key: str | None = None,
          model: str = "deepseek-flash", rebuild: bool = False) -> None:
    from cloud_translator import load_api_key

    key = load_api_key(api_key)
    if not key:
        print("⚠ 没有 API key"); return

    meta, terms, extra, was_new = normalize(load_raw())
    targets = collect_terms(course)
    keep = set(targets) | set(KEEP_EXTRA)

    if rebuild:
        dropped = [t for t in terms if t not in keep]
        terms = {t: e for t, e in terms.items() if t in keep}
        if not was_new:                       # 旧扁平格式: 层级全是猜的, 全部重判
            terms = {}
        if dropped:
            print(f"🧹 清掉 {len(dropped)} 条非术语表条目(自动缓存垃圾)")

    for t in keep:
        terms.setdefault(t, {})

    todo_cls = [t for t in terms if terms[t].get("level") not in LEVELS]
    print(f"待分类 {len(todo_cls)} 条…")
    for i in range(0, len(todo_cls), BATCH_CLASSIFY):
        chunk = todo_cls[i:i + BATCH_CLASSIFY]
        try:
            obj = _chat_json(key, model, SYS_CLASSIFY, "\n".join(chunk),
                             1600, 0.2)
            got = 0
            for t in chunk:
                lv = _pick(obj or {}, t)
                lv = lv.strip().lower() if isinstance(lv, str) else ""
                terms[t]["level"] = lv if lv in LEVELS else "basic"
                got += 1
            print(f"  [{i + len(chunk)}/{len(todo_cls)}] 分类完成")
        except Exception as e:                            # noqa: BLE001
            print(f"  ⚠ 分类批次失败: {str(e)[:80]}")

    for t in KEEP_EXTRA:                      # 人工保留项强制 gloss, 不被模型降级
        if t in terms:
            terms[t]["level"] = "gloss"

    todo_exp = [t for t, e in terms.items()
                if e.get("level") == "gloss" and not (e.get("detail") or "").strip()]
    print(f"待展开 {len(todo_exp)} 条…")
    for i in range(0, len(todo_exp), BATCH_EXPAND):
        chunk = todo_exp[i:i + BATCH_EXPAND]
        try:
            obj = _chat_json(key, model, SYS_EXPAND, "\n".join(chunk),
                             MAX_TOKENS_EXPAND, 0.3)
            got = 0
            for t in chunk:
                v = _pick(obj or {}, t)
                if isinstance(v, str) and v.strip():
                    terms[t]["detail"] = v.strip(); got += 1
            print(f"  [{i + len(chunk)}/{len(todo_exp)}] 本批 +{got}")
        except Exception as e:                            # noqa: BLE001
            print(f"  ⚠ 展开批次失败: {str(e)[:80]}")

    # basic 档: 一行速查。**也参与运行时匹配** —— 学生要的是英文词到中文术语的
    # 即时对照(依赖变量/哑变量/直方图…), 这些词"看名字就懂"不等于"看英文就懂"。
    todo_short = [t for t, e in terms.items()
                  if e.get("level") == "basic" and not (e.get("detail") or "").strip()]
    print(f"待短释 {len(todo_short)} 条…")
    for i in range(0, len(todo_short), BATCH_EXPAND):
        chunk = todo_short[i:i + BATCH_EXPAND]
        try:
            obj = _chat_json(key, model, SYS_EXPAND_SHORT, "\n".join(chunk),
                             1200, 0.2)
            got = 0
            for t in chunk:
                v = _pick(obj or {}, t)
                if isinstance(v, str) and v.strip():
                    terms[t]["detail"] = " ".join(v.split()); got += 1
            print(f"  [{i + len(chunk)}/{len(todo_short)}] 本批 +{got}")
        except Exception as e:                            # noqa: BLE001
            print(f"  ⚠ 短释批次失败: {str(e)[:80]}")

    n_gloss = sum(1 for e in terms.values() if e.get("level") == "gloss")
    n_basic = sum(1 for t, e in terms.items()
                  if e.get("level") == "basic" and (e.get("detail") or "").strip())
    save_to(NOTES_FILE, {"built": datetime.date.today().isoformat(),
                         "model": model}, terms, extra)
    print(f"✅ 完成: {len(terms)} 条 (gloss {n_gloss} / basic 有释 {n_basic}) "
          f"→ {NOTES_FILE.name}")


# ---- 专有名词即时识别 ----

STOP = set("""The This That These Those There Then Than Thank Thanks So Okay OK Now No Yes
What When Where Which While Who Whom Whose Why How But And Or If In On At By For With
From Into About As It Its He She They We You I His Her Their Our Your My Me Us Them
Today Tomorrow Yesterday Monday Tuesday Wednesday Thursday Friday Saturday Sunday
January February March April May June July August September October November December
Professor Doctor Mr Mrs Ms Dr Please Let Let's Well Right Good Great Sure Also However
Because Before After During Between Under Over More Most Some Any All Both Each Every
First Second Third Next Last Another Other Such Only Just Even Still Yet Very Really
""".split())

# 通用高频英语词: 出现即认为是普通词汇, 不是专有名词。
COMMON_WORDS = set("""a an the and or but so if then than that this these those there here
i you we they he she it its his her their our your my me us them
what when where which while who whom whose why how
is are was were be been being am do does did done have has had
will would can could should may might must shall
go goes going get gets got give gives take takes make makes made say says said
know knows think thinks want wants need needs see sees look looks use uses used
come comes find finds feel feels try tries keep keeps let begin starts start
today tomorrow yesterday monday tuesday wednesday thursday friday saturday sunday
january february march april may june july august september october november december
one two three four five six seven eight nine ten zero
first second third fourth fifth next last another other such
some any all both each every more most much many few less least
no yes not none nothing never always often sometimes usually
okay ok oh uh um well now right just even still yet very really only also too
about above across after again against along among around because before behind
below beneath beside between beyond during except inside into near off onto
outside over past since through throughout till toward under until up upon with
within without
good bad great big small large little long short high low old new young
thing things way ways time times year years day days week weeks month months
part parts place places case cases point points number numbers group groups
problem problems fact facts idea ideas word words line lines side sides kind kinds
head hand hands eye eyes face body person people man men woman women child children
name names home house room world life water food air light sound
yeah yep nope hey hello hi thanks thank please sorry excuse welcome everyone everybody
correct indeed naturally positively exactly absolutely certainly probably maybe
mister sir madam lady lord king queen doctor professor mister
find kind sort type form sort
""".split())

FUNCTION_FIRST = set("""the a an and or but so if then than that this these those
there here what when where which while who whom whose why how
yeah yep nope hey hello hi oh uh um well okay ok now
i you we they he she it no yes not
""".split())


def _syllables(word: str) -> int:
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return 1
    n = len(re.findall(r"[aeiouy]+", w))
    if w.endswith("e") and n > 1:
        n -= 1
    return max(1, n)


def _looks_like_vocabulary(phrase: str, words: list[str]) -> bool:
    """普通词汇否决: 高频词 / 单音节词 / 整句都是常见词。"""
    if len(words) == 1:
        w = words[0].lower()
        return w in COMMON_WORDS or _syllables(w) <= 1
    if all(w.lower() in COMMON_WORDS for w in words):
        return True
    return words[0].lower() in FUNCTION_FIRST


def detect_proper_nouns(text: str, known: set[str], max_n: int = 2) -> list[str]:
    """找出可能是专有名词的短语(人名/机构/地名): 连续首字母大写的词。
    过滤句首词、常见词、普通词汇、已收录术语。"""
    out: list[str] = []
    for m in re.finditer(r"\b([A-Z][a-zA-Z'\-]+(?:\s+[A-Z][a-zA-Z'\-]+)*)\b", text or ""):
        phrase = re.sub(r"^(The|A|An)\s+", "", m.group(1))   # 去掉前导冠词
        words = phrase.split()
        if len(phrase) < 4 and not phrase.isupper():    # 放行 MIT/IMF 这类全大写缩写
            continue
        if all(w in STOP for w in words):
            continue
        # 句首的单个普通大写词(Modern/So...) 只是句子开头, 不是专有名词
        if m.start() == 0 and len(words) == 1 and not phrase.isupper():
            continue
        # 普通词汇否决: 全小写常见词 / 单音节 / 高频词表命中
        if not phrase.isupper() and _looks_like_vocabulary(phrase, words):
            continue
        if phrase.lower() in known:
            continue
        if phrase not in out:
            out.append(phrase)
        if len(out) >= max_n:
            break
    return out


def lookup_term(term: str, api_key: str,
                model: str = "deepseek-flash") -> dict | None:
    """即时查一个术语。返回 {"known","type","note"}; 不确定/编造风险 -> None。"""
    try:
        obj = _chat_json(api_key, model, SYS_LOOKUP, term, 400, 0.2)
    except Exception:                                     # noqa: BLE001
        return None
    if not obj or not obj.get("known"):
        return None
    typ = obj.get("type")
    typ = typ.strip().lower() if isinstance(typ, str) else ""
    note = obj.get("note")
    note = note.strip() if isinstance(note, str) else ""
    if typ not in TYPES or not note:
        return None
    return {"known": True, "type": typ, "note": note}


# ---- 运行时匹配 ----

def compile_term(term: str) -> re.Pattern:
    """词边界正则: 允许多/复数, 不允许子串命中(suggest 不会命中 gg)。"""
    esc = re.escape(term).replace(r"\ ", r"\s+").replace(" ", r"\s+")
    return re.compile(r"\b" + esc + r"(?:s|'s)?\b", re.IGNORECASE)


class TermNotes:
    """运行时查表: 句子命中哪些术语 -> 取它们的分层解析。零延迟(无网络)。

    读 term_notes.json(人工/构建) + term_notes_auto.json(运行时缓存), 前者优先。
    """

    def __init__(self, path: pathlib.Path = NOTES_FILE):
        if path == NOTES_FILE:
            auto_path = AUTO_FILE
        else:
            auto_path = pathlib.Path(path).parent / "term_notes_auto.json"
        _, self._curated, _, _ = normalize(load_raw(path))
        _, self._auto, _, _ = normalize(load_raw(auto_path))
        self._path = path
        self._build_index()

    def _build_index(self) -> None:
        merged = dict(self._auto)
        merged.update(self._curated)                      # curated 覆盖 auto
        self._all = merged
        idx = []
        for t, e in merged.items():
            # gloss(完整解析) 与 basic(一行速查) **都参与匹配**; 只有 skip 永不显示。
            # basic 曾因"学生看名字就懂"被排除, 但本应用是英译中 —— 要给的正是
            # 英文词→中文术语的即时对照, 排除它们等于砍掉一半该显示的词。
            if e.get("level") not in ("gloss", "basic"):
                continue
            d = e.get("detail")
            if not (isinstance(d, str) and d.strip()):
                continue
            idx.append((t, compile_term(t), d))
        # 长术语优先(避免 "crisis" 抢先于 "polycrisis")
        idx.sort(key=lambda x: len(x[0]), reverse=True)
        self._index = idx

    def known(self) -> set:
        return {k.lower() for k in self._all}

    def add(self, term: str, note: str, type_: str = "") -> None:
        """自动查到的专有名词 -> 写 term_notes_auto.json (可整文件删除)。"""
        entry = {"level": "gloss", "detail": note}
        if type_:
            entry["type"] = type_
        _, auto, extra, _ = normalize(load_raw(AUTO_FILE))
        auto[term] = entry
        try:
            save_to(AUTO_FILE, {"built": datetime.date.today().isoformat()},
                    auto, extra)
        except Exception:                                 # noqa: BLE001
            pass
        self._auto[term] = entry
        self._build_index()

    def match(self, sentence: str, terms: list[str] | None = None,
              max_n: int = 1) -> list[tuple[str, str]]:
        if not sentence:
            return []
        if terms is None:
            pool = self._index
        else:
            want = {t.lower() for t in terms}
            pool = [e for e in self._index if e[0].lower() in want]
        hits: list[tuple[str, str]] = []
        for t, rx, d in pool:
            if rx.search(sentence):
                hits.append((t, d))
                if len(hits) >= max_n:
                    break
        return hits


def format_gloss(hits: list[tuple[str, str]]) -> str:
    if not hits:
        return ""
    return "💡 " + " ｜ ".join(f"{t}：{n}" for t, n in hits)


if __name__ == "__main__":
    argv = [a for a in sys.argv[1:] if a != "--rebuild"]
    build(argv[0] if argv else None, rebuild="--rebuild" in sys.argv[1:])
