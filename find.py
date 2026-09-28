#!/usr/bin/env python3
"""全局检索 —— 在**全部**转录 / 笔记 / 术语表里找东西。**只读，永不写。**

给两个消费者：① 面板上的搜索框 ② 问答线程（让模型先搜再读全文）。

## ⚠️ 为什么不建索引（实测决定的，不是省事）

2026-09-28 在作者真实语料上量的：**整个语料 2.01 MB / 493 个 md，
全量朴素扫描一遍 124.5 ms**（纯 I/O 只占 20.8 ms）。

→ **不需要 FTS5、不需要 bigram、不需要向量库。**
⭐ 而且这顺带躲掉了一整类坑：FTS5 的默认分词器对 CJK 是坏的
（实测 `垄断`/`边际成本`/`定价` 全 0 命中，`monopoly` 命中）——
**那个坑只存在于"走分词器"这条路上**，`'垄断' in text` 根本没有那个问题。

## ⚠️ 匹配规则：CJK 子串、ASCII 词边界、**按"块"而不是按行**

裸子串会让英文 `cost` 命中 `costly`（`prep._exact_pattern` 的 docstring 逐字讲过这个坑）。
所以**按字符类型分开处理**：中文按子串（中文没有词边界这回事），
ASCII 按 `\\b` 词边界。多个词之间是 **AND**，且**不管顺序**。

⭐ **检索单元是「块」，不是「行」。** 一条定稿块有两到三行（`EN` / `ZH` / `ASR`），
**它们是同一句话**。按行搜会有两个真缺陷（2026-09-28 实测出来的）：

| 缺陷 | 实测 |
|---|---|
| **跨语言查询恒为 0** | `monopoly 垄断` → **0 命中**、`state 状态` → **0 命中**（两个词在不同行，AND 永远不成立） |
| **总数虚高** | 搜 `state function` 得 24 条，其中一半是同一个句子的 EN 行与 ASR 行 |

⚠️ 第一条尤其毒：**一个 0 结果会被读成"这节课没讲过"** —— 正是本模块下面
那条「截断必须说出来」要防的同一类错误（负结果读起来像穷尽）。
→ 按块搜之后，ASR 行**自然参与**（它本来就是同一句话），不需要"跳过 ASR"那种行级补丁。

## ⚠️ 截断必须说出来

`search()` 带 `limit`，但**返回里必须带 `total` 与 `truncated`** ——
不许让一个被截断的结果读起来像"就这些"。

出处：`silverstein/minutes` 的 `crates/archive-core/src/retrieval.rs`（我核过原文）。
它宁可**拒绝**也不截断，理由是截断「could drop a genuine match」，
从而「**let a negative result appear exhaustive when it is not**」。
我们这里不拒绝（语料小），但**必须把"被截断了"说出来**。

## ⚠️ 不重复造的三样

1. **不写第五份"会话抬头解析器"。** 全仓已有**四处**独立实现
   （`obsidian_writer.py:33` 是唯一带 `$` 锚的）→ 用 `obsidian_writer._TS`，
   它是**唯一定义点**。
2. **字段前缀从 `obsidian_writer._FIELDS` 生成** —— 不再手写一遍 `EN/ZH/ASR`。
3. **课号解析复用 `courses._session_course()`** —— 「文件名 → 课号」的既有唯一定义点。
"""
from __future__ import annotations

import dataclasses
import pathlib
import re

# ⚠️ 模块级 import 一次，不在四个函数里各写一遍 try/except。
#    `obsidian_writer` 没有 import 期副作用（只有常量与正则），所以这是安全的。
#    仍然留 try：`find` 要能在没装 PyObjC 的环境里被 import（判据只用合成语料）。
try:
    import obsidian_writer as _OW                                   # noqa: E402
except Exception:                                                   # noqa: BLE001
    _OW = None

# `_TS` / `_FIELDS` 都从 `_OW` 取 —— 它们是会话格式的**唯一定义点**。
# ⚠️ **连兜底都不手写一份**：`_OW` 拿不到时 `_TS` 也是 None，本来就已经退化成
#    "一行一块"了；这时前缀剥不掉只是片段里多几个 `> **EN**:`，**检索照常能用**。
#    在兜底里再写一遍 `EN/ZH/ASR` 就等于把那条规矩破在自己手上。
_TS = getattr(_OW, "_TS", None)
# ⚠️ 前缀**不带 `>`** —— 引号标记由下面的正则单独吃。
#    第一版把 `"> **ZH**:"` 整个当前缀，却又**先**删了 `>` 再去找它 →
#    `s.startswith("> **ZH**:")` 永远为假 → **片段里留着 `**ZH**:`**
#    （2026-09-28 截图看出来的）。顺序反了。
_FIELD_PREFIX = tuple((f"**{label}**:", label)
                      for _key, label in getattr(_OW, "_FIELDS", ()))
_FIELD_RE = (re.compile(r"^>+\s*(" + "|".join(re.escape(p) for p, _ in _FIELD_PREFIX)
                        + r")\s*")
             if _FIELD_PREFIX else None)

# 一次 read 最多回多少字符。⚠️ 不是为了省内存，是为了**别让模型把一整节课塞进上下文**
# （`minutes` 的成例：命中只给路径，「the result path **is only a hint**」）。
MAX_READ_CHARS = 6000
MAX_READ_LINES = 200

_CJK = re.compile(r"[㐀-䶿一-鿿豈-﫿]")


@dataclasses.dataclass(frozen=True)
class Hit:
    """一处命中。`line` 是**1-based**行号 —— `read(path, lo, hi)` 直接吃它。"""
    path: str
    line: int
    text: str
    course: str | None
    date: str | None
    kind: str                 # "session" | "note" | "glossary"


@dataclasses.dataclass(frozen=True)
class Result:
    hits: list
    total: int                # ⭐ 命中**总数**（按块/行计，不受 limit 影响）
    truncated: bool           # ⭐ 被 limit 截断了没有（见模块头）

    def __iter__(self):
        return iter(self.hits)

    def __len__(self):
        return len(self.hits)


# ---------------------------------------------------------------- 查询
def compile_query(query: str):
    """查询 → **一组**正则（每个词一个）。空查询 → `None`。

    ⚠️⚠️ **刻意不拼成单个"前瞻链" `(?=.*a)(?=.*b)`。** 那种写法是**二次复杂度**：
       `re.search` 会在**每个起始位置**都跑一次前瞻，而每个前瞻要扫到块尾。
       实测（2026-09-28，真语料 2 MB）：

       | 写法 | 单 token | 两 token |
       |---|---|---|
       | 前瞻链（含 DOTALL） | **1284 ms** | **1352 ms** |
       | **逐词分别匹配** | **38 ms** | 见下 |

       语义**完全相同** —— 无序 AND 就是"每个词各自命中"。少一层正则魔法，还快 30 倍。
    ⚠️ 逐词匹配**顺带解决了 DOTALL 的问题**：不再需要 `.` 跨行，因为每个词是在
       **整块文本**上直接搜的（块本来就是多行拼的）。
       第一版拼成单正则时踩过：`. 不匹配换行` → 跨语言 AND **恒为 0**。
    """
    toks = (query or "").split()                    # `str.split()` 已按任意空白切并丢空串
    if not toks:
        return None
    out = []
    for t in toks:
        esc = re.escape(t)
        if _CJK.search(t):
            out.append(re.compile(esc, re.IGNORECASE))      # 中文没有词边界，纯子串
            continue
        # ⚠️ 前后加 `\b` 拦 `cost` 命中 `costly`。但 `\b` 只在 `\w` 与非 `\w` 之间成立，
        #    所以首尾不是词字符时那一侧不加（否则 `.pdf` 这种永远匹配不上）。
        pre = r"\b" if t[:1].isalnum() else ""
        post = r"\b" if t[-1:].isalnum() else ""
        out.append(re.compile(f"{pre}{esc}{post}", re.IGNORECASE))
    return tuple(out)


def matches(compiled, text) -> bool:
    """`compile_query` 的结果 → 这段文本命中了没有（**所有词都要命中**，不管顺序）。"""
    return all(r.search(text) for r in compiled)


# ---------------------------------------------------------------- 切块
def _blocks(lines: list, *, is_session: bool):
    """把行切成**检索单元**。返回 `[(起始行号, 结束行号, 块文本)]`。

    ⚠️ **会话文件按抬头切块**（一条 `> [!abstract]` 一条），其余文件**一行一块**。
       为什么要有"块"这一层：见模块头那两条实测缺陷 —— 按行搜会让
       **跨语言查询恒为 0**，还会让**总数虚高一倍**。
    ⚠️ 抬头正则用 `_OW._TS`（唯一定义点）；**拿不到它就退化成一行一块**
       （检索仍然能用，只是丢掉跨语言 AND —— 退化而不是崩）。
    """
    if not (is_session and _TS):
        return [(i, i, ln) for i, ln in enumerate(lines, 1)]
    out, start = [], None
    for i, ln in enumerate(lines, 1):
        if _TS.match(ln):
            if start is None:
                # ⚠️ **第一条抬头之前的内容也成一块**（2026-09-28 审查指出）：
                #    `obsidian_writer` 在会话文件开头写的是标题行
                #    （`# 课号 · 日期`），原来它**整段被丢掉** ——
                #    "搜课号看哪几节课提过"这种用法会**一条都搜不到**，且无声。
                if i > 1:
                    out.append((1, i - 1, "\n".join(lines[:i - 1])))
            else:
                out.append((start, i - 1, "\n".join(lines[start - 1:i - 1])))
            start = i
    if start is None:
        # ⚠️ 一份会话**一条抬头都没有**（录课中断 / 格式变过）——
        #    别把整份文件丢掉，退回一行一块（与拿不到 `_TS` 时同一条退化路径）。
        return [(i, i, ln) for i, ln in enumerate(lines, 1)]
    out.append((start, len(lines), "\n".join(lines[start - 1:])))
    return out


def _best_line(lines: list, lo: int, hi: int, rxs) -> tuple[int, str]:
    """块内**哪一行**最先命中 —— 命中行号给 `read()` 用，精确落点。

    ⚠️ 多词查询时"命中行"取的是**第一个**命中任一关键词的行。多词散布在
       EN / ZH 两行时，落点必然只落在其中一行 —— 这是**接受的**：
       `read()` 只是给模型一个起点，块本身就在同一个位置。
    都不命中（理论上不会：块文本是这些行拼的）时退回第一行。
    """
    for i in range(lo, hi + 1):
        if matches(rxs, lines[i - 1]):
            return i, _strip_prefix(lines[i - 1])
    return lo, _strip_prefix(lines[lo - 1])


def _strip_prefix(line: str) -> str:
    """把行首的 Markdown 壳剥掉，留下**人话**（只剥壳，不做结构解析）。

    ⚠️ **顺序要紧**：字段前缀那一刀必须**在删 `>` 之前**（前缀里没有 `>`，
       而删完 `>` 之后 `> **ZH**:` 就再也对不上了）。第一版就是反的，
       症状是片段里留着 `**ZH**:` —— 截图才看出来。
    """
    s = line.strip()
    if _FIELD_RE:
        m = _FIELD_RE.match(s)
        if m:
            return s[m.end():].strip()
    s = re.sub(r"^>+\s*", "", s)                    # 引用块标记（可嵌套）
    s = re.sub(r"^\[!\w+\][-+]?\s*", "", s)         # callout 头
    return s.strip()


# ---------------------------------------------------------------- 元数据
def _meta(path: pathlib.Path):
    """文件名 → `(课号, 日期)`。**两个都允许是 None**（不是所有文件都这么命名）。"""
    course = None
    try:
        import courses
        course = courses._session_course(path.stem)
    except Exception:                                     # noqa: BLE001
        pass
    m = re.match(r"(\d{4}-\d{2}-\d{2})", path.name)
    return course, (m.group(1) if m else None)


# ---------------------------------------------------------------- 检索
def default_roots() -> list:
    """默认搜哪几处 → `[(路径, 单元名)]`。

    ⚠️ **单元名跟着根走，不在匹配处按目录名反推。** 第一版在循环里写
       `p.parent.name == "sessions"` —— 传自定义 `roots=` 时 kind 全退成 `"note"`，
       而且"这个根是不是会话格式"这件事被埋进了目录名字符串里。
    ⚠️ 会话目录用 `_OW.SESSIONS`（`obsidian_writer.py:30` 的唯一定义点），
       **不在这里手拼 `here / "sessions"`** —— 布局一变两边指的就不是同一处。
    """
    roots = []
    if getattr(_OW, "SESSIONS", None):
        roots.append((_OW.SESSIONS, "session"))
    here = pathlib.Path(__file__).resolve().parent
    roots.append((here / "glossary", "glossary"))
    vault = getattr(_OW, "DEFAULT_VAULT", None)
    if vault:
        roots.append((pathlib.Path(vault).expanduser() / "Lectures", "note"))
    return roots


def _iter_files(roots):
    for root, kind in (roots or default_roots()):
        r = pathlib.Path(root)
        if not r.is_dir():
            continue
        for pat in ("*.md", "*.txt"):
            for p in sorted(r.glob(pat)):
                if p.is_file():
                    yield p, kind


def search(query, *, roots=None, limit: int = 40) -> Result:
    """扫一遍全部语料，返回最多 `limit` 条 **+ 真实总数**。

    ⚠️ **永远返回 `Result`，不返回裸 list** —— 调用方要能从它身上看出"有没有被截断"。
    """
    rxs = compile_query(query)
    if rxs is None:
        return Result([], 0, False)
    hits: list = []
    total = 0
    for p, kind in _iter_files(roots):
        course, date = _meta(p)
        try:
            lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for lo, hi, block in _blocks(lines, is_session=(kind == "session")):
            if not matches(rxs, block):
                continue
            total += 1
            if len(hits) < limit:
                line, text = _best_line(lines, lo, hi, rxs)
                hits.append(Hit(str(p), line, text, course, date, kind))
    return Result(hits, total, total > limit)


def as_context(result, *, max_chars: int = 2400) -> str:
    """把 `search()` 的结果拼成**给模型看的一段材料**。空结果 → `""`。

    给问答线程用：拿问题全库搜一遍，把命中的那几行连同**出处**一起喂进去。

    ⚠️ **每一条都带出处**（课号 / 日期 / 文件名）—— 模型才会说"这是上周三那节讲的"，
       而不是把三节课的内容糊成一句。也让人能回查（`read` 的入口就是这些行号）。
    ⚠️ **截断了要说出来**（同模块头那条）：超预算时末尾明写"只给了前 N 条"，
       别让模型以为"就这些"。这条与 `Result.truncated` 是同一件事的两种呈现 ——
       那是给**代码**看的，这是给**模型**看的。
    ⚠️ 空结果返回 `""` 而**不是**"没找到"那句话：调用方据此**整段不加**
       （在 prompt 里塞一句"没搜到"只会让模型围着它绕）。
    """
    if not result or not result.hits:
        return ""
    out, used = [], 0
    for h in result.hits:
        who = " · ".join(x for x in (h.course, h.date) if x) or "?"
        line = f"- [{who}] {h.text}"
        if used + len(line) > max_chars:
            break
        used += len(line)
        out.append(line)
    if not out:
        return ""
    more = ""
    if len(out) < result.total:
        more = f"\n（关键词命中 {result.total} 处，上面只给了前 {len(out)} 条）"
    return "\n".join(out) + more


def read(path, lo: int, hi: int) -> str:
    """读 `[lo, hi]`（**1-based，闭区间**）之间的原文。

    ⚠️ **必须带行号范围** —— 没有它，模型会把一整节课（几十 KB）塞进上下文。
       这正是 `minutes` 的成例：命中只给路径（「the result path **is only a hint**」），
       读全文是**另一步**。

    ⚠️ 两重上限（`MAX_READ_LINES` / `MAX_READ_CHARS`）**都会在结果里明说**，
       不静默截断（同模块头那条"截断必须说出来"）。
    """
    p = pathlib.Path(path)
    lo = max(1, int(lo))
    hi = max(lo, int(hi))
    try:
        lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError as e:
        return f"⚠ 读不了 {p.name}：{e}"
    # ⚠️⚠️ **先把 `hi` 夹到真实行数，再判「有没有被上限截断」**（2026-09-28 审查指出）：
    #    顺序反了的话，`read(p, 1, 99)` 对一份只有 50 行的文件会算出 `capped=True`
    #    → 给模型那句「⚠️ 这里被截断了」是**假话**（50 行一条没少），
    #    而模型会因此以为自己没看全（本模块的纪律是"截断必须说出来"，
    #    但**没说到的也绝不能谎报**）。
    hi = min(hi, len(lines))
    capped = (hi - lo + 1) > MAX_READ_LINES
    if capped:
        hi = lo + MAX_READ_LINES - 1
    body = "\n".join(lines[lo - 1:hi])
    cut = len(body) > MAX_READ_CHARS
    body = body[:MAX_READ_CHARS]                    # 越界切片本就是 no-op
    head = f"# {p.name} 第 {lo}–{hi} 行" + (f"（原文共 {len(lines)} 行）" if capped else "")
    tail = ("\n\n⚠️ **这里被截断了** —— 只给了上面这些，别当成全文。"
            if (capped or cut) else "")
    return f"{head}\n{body}{tail}"
