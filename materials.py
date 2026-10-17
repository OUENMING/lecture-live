"""本课**课件索引**：把 vault 里的课程材料变成「可检索的片段 + 可核对的出处」（step 2）。

## 为什么要有它

审计（`notes/report.md` §1.5-⑤）指出：所有 LLM 步骤的输入**只有转录**，
而课件/试卷/教材在盘上躺着从来没人读 —— `Study/ECON10790/materials/` 有 40 份
（含 2024/2025 期末题**与官方更正**），`~/.classlive/courses/<课号>/materials/`
是 `prep` 归档的原件。结果是笔记只能复述，教授的口误与错数字照抄（A2/A3）。

`docs/PLAN-notes-and-ui.md` §2.1-② 的处方：**先做小的** —— slides / syllabus /
历年卷（几 KB–几十 KB，检索而不是整份塞进 prompt）。

## 两个出口（这就是它的全部接口）

- `search(转录片段) -> [Chunk]` —— 复习层每块按需取 top-k 片段（喂 prompt）；
- `verify(词) -> bool` —— 术语表那条路的**语境核对**（`note_terms.select` 的
  `verify` 参数）：这个词在本课材料里真实出现过吗。
  ⚠️ 这一条是 `USC`（南加州大学 vs 本课 Universal Social Charge）那类
  **错语境义**的唯一止血点 —— 词面完全相同，只有"本课材料里出现的是什么"能分开。

## 数据从哪来（**只读**）

1. `<vault>/Study/<课号>/materials/*.md` —— 作者/Claude 转写好的课件与真题；
2. `~/.classlive/courses/<课号>/materials/` —— `prep` 归档的原件
   （只有能当文本读的 `.md`/`.txt` 进索引；PDF/PPTX 留待 `extract` 那条路的将来版本）。

⚠️ 一个目录都没有 = **正常状态**（新库 / 没归档过课件）→ `Materials([])`，
   `len()` 为 0，复习层退化成"没有课件核对"并在笔记头部写明（§2 的退化档）。
"""
from __future__ import annotations
import dataclasses
import math
import pathlib
import re

#: 一块多大。600–1000 字符 ≈ 一张课件页的要点，塞 3 块进 prompt 约 300–600 token。
CHUNK_TARGET = 900
CHUNK_MAX = 1500
#: 索引上限（防一份 200KB 的教材把内存和 prompt 都撑爆；超了从头截断文件列表）。
MAX_CHUNKS = 4000
#: 检索时从 query 里挑几个词去搜（按 idf 取最"informative"的）。
QUERY_TERMS = 14
#: `search` 返回的片段总字符上限 —— 直接决定 prompt 涨多少。
SEARCH_MAX_CHARS = 2200

_WORD = re.compile(r"[a-z][a-z'\-]+", re.I)
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*$", re.M)

#: 检索用的停用词（只挡检索，不影响 `verify`）—— 与 `build_notes.FUNCTION_FIRST`
#: 一样是"高频到没有区分度"的那批。刻意不从别处 import：那边是为"是不是术语"
#: 调的，两个判据会朝相反方向漂。
_STOP = frozenset("""
the a an and or but if then than that this these those there here what when where which
while who whom whose why how be is are was were been being do does did done have has had
we you they he she it i me my our your their its of in on at to for with from by as not no
yes so such can could will would shall should may might must about into over under again
""".split())


@dataclasses.dataclass(frozen=True)
class Chunk:
    """一个可检索片段：`file` 是相对路径（进 prompt 时当出处写），`heading` 是最近的标题。"""
    file: str
    heading: str
    text: str
    score: float = 0.0

    def cite(self) -> str:
        """进笔记/prompt 的出处写法：`📖 文件名 §标题`。"""
        h = f" §{self.heading}" if self.heading else ""
        return f"📖 {self.file}{h}"


def _tokens(text: str) -> list:
    return [w.lower() for w in _WORD.findall(text or "")]


def split_chunks(text: str, *, heading: str = "") -> list:
    """一份材料 → 片段。**先按标题切、再按空行/长度并**（纯函数，判据靠它）。

    ⚠️ 标题要**跟着走**（`heading`）：出处那一行 `📖 文件 §标题` 是读者回源唯一的锚，
       丢了它读者拿着一句"📖 econ10790-final-exam.md"在 40 份材料里找不到北。
    """
    out: list = []
    buf: list = []
    cur = heading

    def flush():
        nonlocal buf
        body = "\n".join(buf).strip()
        buf = []
        if body:
            out.append(Chunk(file="", heading=cur, text=body[:CHUNK_MAX]))

    for block in re.split(r"\n\s*\n", text or ""):
        m = _HEADING.match(block)
        if m:
            flush()
            cur = m.group(2) if len(m.groups()) > 1 else m.group(1)
            cur = cur.strip().strip("#").strip()
            continue
        if not block.strip():
            continue
        if buf and sum(len(x) for x in buf) + len(block) > CHUNK_TARGET:
            flush()
        buf.append(block.strip())
        if sum(len(x) for x in buf) > CHUNK_MAX:
            flush()
    flush()
    return out


def read_text(path: pathlib.Path) -> str:
    """读一份材料。⚠️ `errors="replace"` —— 转写件里有截断的多字节汉字（同
    `obsidian_writer.close()` 那条教训：严格解码会让整节课的笔记写不出来）。"""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


class Materials:
    """本课材料的检索索引。构造一次、**只读**、线程安全（复习层是并发调的）。"""

    def __init__(self, chunks: list, *, files: list | None = None):
        self._chunks: list = list(chunks or [])[:MAX_CHUNKS]
        self._files: list = list(files or [])
        self._tf: list = []          # 每块: {token: n}
        self._len: list = []         # 每块 token 数
        self._df: dict = {}          # token: 出现在几块里
        self._avg = 0.0
        self._n = len(self._chunks)
        for c in self._chunks:
            toks = _tokens(c.text)
            tf: dict = {}
            for t in toks:
                tf[t] = tf.get(t, 0) + 1
            self._tf.append(tf)
            self._len.append(len(toks) or 1)
            for t in tf:
                self._df[t] = self._df.get(t, 0) + 1
        if self._n:
            self._avg = sum(self._len) / self._n
        self._blob = "\n".join(c.text for c in self._chunks).lower()
        self._rx: dict = {}

    # ---- 构造 ----
    @classmethod
    def for_course(cls, vault, course: str, *, state_root=None,
                   extra_dirs=()) -> "Materials":
        """按课号收集材料目录 → 读 → 切块 → 索引。**不抛**（读不到 = 空索引）。

        ⚠️ 目录找不到是**正常路径**（新库 / 没归档课件）→ 空索引 → 复习层走退化档。
           这里绝不猜另一个目录（同 `resolve_vault` 那条"没有兜底目录"的纪律）。
        """
        dirs: list = []
        if vault:
            dirs.append(pathlib.Path(vault) / "Study" / course / "materials")
        try:
            import paths
            dirs.append(paths.materials_dir(course, root=state_root))
        except Exception:                                     # noqa: BLE001
            pass
        dirs += [pathlib.Path(d) for d in extra_dirs]
        chunks: list = []
        files: list = []
        for d in dirs:
            try:
                ents = sorted(p for p in d.iterdir()
                              if p.is_file() and p.suffix.lower() in (".md", ".txt", ".markdown"))
            except OSError:
                continue
            for p in ents:
                text = read_text(p)
                if not text.strip():
                    continue
                files.append(str(p))
                for c in split_chunks(text):
                    if c.text.strip():
                        chunks.append(dataclasses.replace(c, file=p.name))
                if len(chunks) >= MAX_CHUNKS:
                    break
        return cls(chunks, files=files)

    # ---- 出口 ①：检索 ----
    def __len__(self) -> int:
        return self._n

    def files(self) -> list:
        return list(self._files)

    def _query_terms(self, query: str) -> list:
        """query → 最有区分度的几个词（按 idf 降序，保序去重）。

        ⚠️ **不整段丢给 BM25**：一段 70 句的转录里充满 the/is/of，它们会把分数拉平，
           检索出来的片段与这段课无关。挑 idf 最高的词 = "这段里最不常见的词"，
           正是课件里会写的那种词（stationary point / shutdown price）。
        """
        tf: dict = {}
        for t in _tokens(query):
            if t in _STOP or len(t) < 3:
                continue
            tf[t] = tf.get(t, 0) + 1
        if not tf:
            return []
        n = max(1, self._n)
        scored = [(math.log(1 + n / (1 + self._df.get(t, 0))) * (1 + math.log(c)), t)
                  for t, c in tf.items()]
        scored.sort(key=lambda x: (-x[0], x[1]))
        out: list = []
        for _s, t in scored[:QUERY_TERMS]:
            if t not in out:
                out.append(t)
        return out

    def search(self, query: str, k: int = 3,
               *, max_chars: int = SEARCH_MAX_CHARS) -> list:
        """转录片段 → top-k 课件片段（BM25，k1=1.2 / b=0.75）。空索引 → `[]`。"""
        terms = self._query_terms(query)
        if not terms or not self._n:
            return []
        k1, b = 1.2, 0.75
        n = self._n
        scored: list = []
        for i, chunk in enumerate(self._chunks):
            s = 0.0
            tf = self._tf[i]
            dl = self._len[i]
            for t in terms:
                f = tf.get(t, 0)
                if not f:
                    continue
                idf = math.log(1 + (n - self._df.get(t, 0) + 0.5) / (self._df.get(t, 0) + 0.5))
                s += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / self._avg))
            if s > 0:
                scored.append((s, i))
        scored.sort(key=lambda x: (-x[0], x[1]))
        out: list = []
        used = 0
        seen_heads: set = set()
        for s, i in scored:
            c = self._chunks[i]
            key = (c.file, c.heading)
            if key in seen_heads:                 # 同一节连着命中时只留一块，省 prompt
                continue
            body = c.text[:CHUNK_MAX]
            if used + len(body) > max_chars and out:
                break
            used += len(body)
            seen_heads.add(key)
            out.append(dataclasses.replace(c, score=round(s, 3), text=body))
            if len(out) >= k:
                break
        return out

    # ---- 出口 ②：语境核对 ----
    def verify(self, term: str, *, max_n: int = 2) -> bool:
        """本课材料里**真的出现过**这个词吗（术语表的语境核对用）。

        ⚠️ 走 `build_notes.compile_term` —— 术语匹配的**唯一定义点**：
           它允许多/复数、不允许子串命中（`gg` 不会被 `suggest` 命中）。
           这里再写一份正则就是"一条纪律两处定义"，两边迟早漂移。
        """
        key = (term or "").strip()
        if not key or not self._blob:
            return False
        rx = self._rx.get(key)
        if rx is None:
            try:
                from build_notes import compile_term
                rx = compile_term(key)
            except Exception:                                 # noqa: BLE001
                rx = re.compile(re.escape(key), re.I)
            self._rx[key] = rx
        hits = 0
        for m in rx.finditer(self._blob):
            hits += 1
            if hits >= 1:                    # 出现一次即算"本课材料里有"
                return True
        return False

    def describe(self) -> str:
        """给笔记头部/收尾打印的一行：`📖 课件 N 份 / M 段`。"""
        if not self._n:
            return "📖 无课件材料"
        return f"📖 课件 {len(self._files)} 份 / 索引 {self._n} 段"


def snippets_block(hits: list, *, limit: int = 3) -> str:
    """检索结果 → prompt 里那一段（`📖 出处` + 正文）。

    ⚠️ 出处**必须跟着正文**（模型要在 `sources` 里写出处；没有出处的片段等于让它猜）。
    """
    if not hits:
        return ""
    parts = []
    for c in hits[:limit]:
        parts.append(f"[{c.cite()}]\n{c.text}")
    return "Course materials excerpts:\n" + "\n\n".join(parts) + "\n\n"
