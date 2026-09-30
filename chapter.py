#!/usr/bin/env python3
"""章节层 —— 把一节**已提取出原子**的课，切成「一章一章的纲要」。

    ⚠️ 形状**照抄 `atom.py`**（纯函数 + 一个旁路写入器），不要另立一套。

## 住哪：`sessions/<同名>.chapters.jsonl`（旁路文件）

⭐ 照 `.atoms.jsonl` / `.lost.jsonl` 的成例：**绝不碰会话 `.md`**。
   会话抬头是**三方共享契约**（`obsidian_writer` 写 / `_parse` 读回 / `cl last` grep），
   而 `_TS` 正则是**行尾锚定**的 —— 多一个字段 `_parse` 就认不出那一条，
   会把它的 EN/ZH/ASR **静默盖到上一条头上**。

## 三种记录（**只追加，不改写旧行**）

```
{"type": "window",   "w", "t", "topic", "topic_zh", "lo", "hi", "n_points"}
{"type": "chapter",  "type", …Chapter 全部字段…}
{"type": "deadline", "type", "t", "quote", "src", "source", "changed"}
```

- `w` = 窗口序号，**从 1 起**
- 同一个 `id` 的**临时版**（`status="interim"`）与**正式版**（`"final"`）写同一个 `id`，
  后写覆盖先写 → `load()` 取「每个 id 最后一条」就天然拿到正式版
- `Chapter.version` = 同一个 `id` 第几次合成（临时版 1、2、3…，正式版是最后一次）

## 不变量（同 `atom.py`）

1. **只增不改** —— 旧行永不重写
2. `src` 必须是**全局句号**且落在本章的 `[lo, hi]` 内（机械闸门保证）
3. 「没有实质内容」是**合法输出**（`sentences` 为空）
4. `close()` 之后**拒绝写入**（新建 `_closed` 标志）——
   ⚠️ 这是本模块与 `AtomWriter` **刻意不同**的一处：`AtomWriter._handle()` 在句柄为
   `None` 时会**静默重开文件**；而「被 `finish()` 放弃的线程」正是本模块引入的新暴露面，
   它迟到回来时文件已经关了，重开 = 写脏数据 + 泄漏句柄。
   （`atom.py` 那半在计划里是**单独一个提交**改的，见 D17。）
"""
from __future__ import annotations

import dataclasses
import json
import pathlib
import re

import atom

#: ⚠️ **沿用 `atom.MODEL` 这一个定义点**，不在这里再写一个字符串。
MODEL = atom.MODEL
MAX_TOKENS = 1400         # ⚠️ 2026-09-30 从 900 抬上来：加了 `zh` 之后**一句话两遍**
                          # （英文 + 中文译文），4 句 × (en≈60 + zh≈150 + terms≈30)
                          # 再加标题和 JSON 壳 ≈ 1100 —— 900 会**从尾巴上截断**，
                          # 而截断的表现是"最后一句莫名其妙没了"，不像报错。
                          # ⚠️ 它只是**上限**：模型写得短就花得少。
TEMPERATURE = 0.0         # 同 atom：要可复现
MAX_SENTENCES = 4
SENT_MAX_CHARS = 400      # 单句长度上限（同 atom 的 200，放宽 —— 合成句比要点长）
#: `zh` 的上限。⚠️ 中文比英文密，同样内容字数少一截 —— 拿 `SENT_MAX_CHARS` 卡它会**卡不住**。
#: ⚠️ 超长 / 缺失 / 非字符串一律置空，**不丢句** —— `en` 才是唯一依据，`zh` 只供阅读。
SENT_ZH_MAX_CHARS = 200
CHAPTER_TAIL = ".chapters.jsonl"

#: `flag` 的唯一定义点。不在里面的一律当 `None`（**fail-open**，同 `atom.KINDS` 的取舍：
#: 宁可丢掉一个标记，不可因为一个非法值把整句合成句扔掉）。
FLAGS = (None, "board", "discussion")

WINDOW, CHAPTER, DEADLINE = "window", "chapter", "deadline"

SYS_CHAPTER = """你在听一节课的转录, 手里是**一章**的原料: 这一章的英文原句(带全局句号)、
这一章已经提取出的要点、以及前面几章的**标题**(只给标题, 供你接续)。

把这一章写成**纲要**, 输出严格 JSON:
{"title": "本章标题(不超过 8 个英文词)",
 "title_zh": "中文标题",
 "sentences": [{"en": "一句简短的英文",
                "zh": "这一句的中文译文",
                "terms": [["price elasticity of demand", "需求价格弹性"]],
                "src": [12, 13],
                "flag": null}]}

硬性要求:
- 写 **2 到 4 句**, 按「**定义 → 推导或理由 → 结论**」的顺序。
- **保留公式**。口述出来的公式要规范成 `Qd = 120 − 2P` 这种写法, 不要留着口语说法。
- **只依据给你的内容**, 不引入任何外部知识 —— 你没上过这节课。
- 没有实质内容(这一章全是闲聊、点名、设备调试)时, `sentences` 返回空数组。
- 每条 `src` 必须是给你的**全局句号**里真实存在的; 不许编。
  ⚠️ 它只能落在本章范围内 —— 越界的整句会被丢掉。
- 英文写**简单句、常用词** —— 它是给非英语母语的人一眼扫过的。
- ⭐ 每句都要给 `zh`: **忠实翻译那一句 `en`** 的中文, 不是概括、也不是另写一句。
  ⚠️ `en` 才是**唯一依据**(`src` 指的是英文原句); `zh` 只供阅读, 不算引用来源。
  ⚠️ 不许在 `zh` 里加 `en` 没有的信息。公式照抄, 别改写。
- `terms` 只放关键术语和关键短语, **每句最多 3 对**, 形如 `[["英文", "中文"]]`。
  没把握就留空数组。
- `flag` 只有两种取值:
  · 讲这一句时**依赖板书或图**(如 "as you can see here"、"look at this diagram") → `"board"`
  · 这一句的内容**来自学生发言**(课堂讨论) → `"discussion"`
  · 其余一律 `null`"""


# ---------------------------------------------------------------- 课务正则
#: 课务线索。⚠️ **必须按词边界** —— 否则 `exam` 会命中 `example`、
#:    `due` 会命中 `during`（课堂里这两个词的出现频率高得离谱）。
#: ⚠️ 不设 `final\s+exam` 这种冗余分支：`exam` 已经覆盖了它。
#: ⚠️⚠️ **复数要收**（2026-09-30 独立审查抓到）：第一版全是单数词根 + `\b`，
#:    于是 `The exams are next week` / `midterms` / `quizzes` / `assignments`
#:    **一条都不命中** —— 而老师在课上说的十有八九是复数。
#:    按本模块自己写的优先级（漏真 deadline 比多报一条重得多），这个缺口必须补。
DEADLINE_RE = re.compile(
    r"\b(due|deadlines?|submits?|submissions?|hand\s+in|problem\s+sets?|"
    r"homeworks?|assignments?|midterms?|quiz(?:zes)?|exams?)\b", re.I)

#: 改期线索。命中它 = 这条课务**被改过**（界面要标「已改期」）。
#:
#: ⚠️⚠️ **2026-09-30 实测抓到的错**：第一版写的是 `pushed\s+to` ——
#:    而原话是 `We pushed **the deadline** to Wednesday.`，两个词**根本不相邻**
#:    → `changed` 恒为 False，「已改期」这条功能**整个是死的**。
#:    ⚠️ 更阴的是：**当时的冒烟测放它过去了**，因为那条只断言「命中没命中」、
#:       **没断言 `changed` 的值** —— 命中是对的，值错得离谱。
#:    → 修法：只认**动词**，不认动词后面跟什么（反正在 `DEADLINE_RE` 命中的句子里，
#:       "push / postpone / extend / move" 不会有别的意思）。
#:    → 纪律：**判据要指向那个字段**，不是指向"有没有命中"（同 `verify-criteria-must-point-at-the-field`）。
#: ⚠️ 词形要收全：`moved` 之外还有 `move / moves / moving`（`I'm moving the deadline`
#:    实测漏过）；`push` 之外还有 `pushing`。（2026-09-30 独立审查抓到。）
CHANGED_RE = re.compile(
    r"\b(postpon\w*|push(?:ed|es|ing)?|extend\w*|extension|"
    r"mov(?:e|ed|es|ing)|reschedul\w*)\b", re.I)


def deadline_hits(en) -> dict | None:
    """一句英文 → `{"changed": bool}`，没命中返回 `None`。**纯函数、不调模型。**

    ⭐ **两条独立的判据，顺序不能反**：
       ① **有没有课务对象**（`DEADLINE_RE`：due / deadline / problem set / exam …）
       ② **它有没有被改期**（`CHANGED_RE`：postpone / push / extend / move …）
    ⚠️ **只有改期动词、没有课务对象的句子不算** —— `"It's been pushed back a week."`
       返回 `None`，因为**不知道被推迟的是什么**。反过来也一样：
       `"The problem set is due Friday."` 命中但 `changed=False`。
       这是刻意的：界面上「待确认」和「已改期」是两种东西。

    ⚠️ 它跑在**每一句定稿**上（`LiveSummarizer.step`），所以必须便宜到可以忽略 ——
       两个 `re.search`，没有别的。
    ⚠️ 误报是**设计上接受**的：课堂里「讨论考试」也会命中。每条都显示原句、
       都标「待确认」，用户自己判断。**别为了压误报把词表收窄** ——
       漏掉真的 deadline 比多显示一条假的重得多。
    """
    s = str(en or "")
    if not DEADLINE_RE.search(s):
        return None
    return {"changed": bool(CHANGED_RE.search(s))}


# ---------------------------------------------------------------- 数据类
@dataclasses.dataclass(frozen=True)
class Chapter:
    """一章纲要。临时版与正式版**同一个 `id`**，靠 `status` 区分。"""
    id: int
    status: str                  # "interim" | "final"
    title: str                   # 英文标题
    title_zh: str
    t0: str                      # "HH:MM:SS"
    t1: str
    lo: int                      # 全局句号区间，**闭区间**，1-based
    hi: int
    sentences: list = dataclasses.field(default_factory=list)
    version: int = 1

    def as_json(self) -> dict:
        return {"type": CHAPTER,
                "id": self.id, "status": self.status,
                "title": self.title, "title_zh": self.title_zh,
                "t0": self.t0, "t1": self.t1,
                "lo": self.lo, "hi": self.hi,
                "sentences": [dict(s) for s in self.sentences],
                "version": self.version}


# ---------------------------------------------------------------- 纯函数
def build_prompt(sentences, atoms, prior_titles) -> str:
    """本章的原料 → 给模型的 block。**纯函数。**

    `sentences` = `[(全局句号, 英文), …]`。
    ⚠️ 句号是**全局的**（1-based，与落盘后的 `Atom.src` **同域**）——
       绝不许在这里重新编号，否则模型回的 `src` 和我们存的整整差一节。
    """
    parts: list = []
    if prior_titles:
        parts.append("前面几章讲的是（**只给标题**，供你接续，不要重复它们的内容）：")
        parts.extend(f"- {t}" for t in prior_titles)
        parts.append("")
    parts.append("这一章的英文原句（`全局句号. 句子`）：")
    parts.extend(f"{gid}. {en}" for gid, en in (sentences or ()))
    rows = []
    for a in (atoms or ()):
        text = str(getattr(a, "text", "") or "")
        if text:
            rows.append(f"- [{getattr(a, 'kind', '')}] {text}")
    if rows:
        parts.append("")
        parts.append("这一章已经提取出的要点（供你判断重点，**不要照抄**）：")
        parts.extend(rows)
    return "\n".join(parts)


def parse_reply(obj, lo: int, hi: int) -> list:
    """模型回的 dict → `[{"en","zh","src","terms","flag"}, …]`。**这里是全部的机械闸门。**

    - `src` 为空、或**越出 `[lo, hi]`** → **整句丢掉，不修剪**
    - `flag` 不在 `FLAGS` 里 → 一律当 `None`
    - `terms` 走 `atom.terms_of`（**同一条校验，不是抄一份**）
    - ⭐ `zh` 不合法（缺 / 非字符串 / 超长）→ **置空串，句子照留** ——
      `en` 才是唯一依据，`zh` 只是给人读的；为一个阅读字段丢内容不划算
    - 最多 `MAX_SENTENCES` 句

    ⚠️ 丢掉而不是修剪：`src` 越界意味着这句**引用不实**，
       修剪成"本章最近的那句"是**替模型编引用**（`atom.parse_reply` 逐字的同一条纪律）。
    """
    if not isinstance(obj, dict):
        return []
    raw_sents = obj.get("sentences")
    if not isinstance(raw_sents, (list, tuple)):
        return []                                        # ⚠️ 形状不对 -> 空表，**不抛**
    out: list = []
    for s in raw_sents:
        if not isinstance(s, dict):
            continue
        en = str(s.get("en") or "").strip().replace("\n", " ")
        if not en or len(en) > SENT_MAX_CHARS:
            continue
        # ⚠️⚠️ **先判形状再迭代**。`(s.get("src") or [])` 对 `"src": 12` 这种
        #    非容器**会抛 `TypeError`**，而这个函数自称是「**全部的机械闸门**」——
        #    **闸门自己炸掉比放过一条脏数据糟得多**（异常会冒到 worker 线程，
        #    打死总结器，之后整节课不再出章节）。同 `atom.traceable` 那条纪律。
        raw_src = s.get("src")
        if not isinstance(raw_src, (list, tuple)):
            continue                                     # 形状不对 = 整句丢
        src = [x for x in raw_src if isinstance(x, int)]
        if not src or any(x < lo or x > hi for x in src):
            continue                                     # ⚠️ 整句丢，不修剪
        flag = s.get("flag")
        if flag not in FLAGS:
            flag = None                                  # fail-open
        zh = str(s.get("zh") or "").strip().replace("\n", " ")
        if len(zh) > SENT_ZH_MAX_CHARS:
            zh = ""                                      # ⚠️ 置空，**不丢句**
        out.append({"en": en, "zh": zh, "src": sorted(set(src)),
                    "terms": atom.terms_of(s), "flag": flag})
        if len(out) >= MAX_SENTENCES:
            break
    return out


def title_of(obj) -> tuple:
    """`(title, title_zh)` —— 两个都截断。**与 `atom.topic_of` 同一套读法。**"""
    return (atom._label_of(obj, "title", atom.TOPIC_MAX),
            atom._label_of(obj, "title_zh", atom.TOPIC_ZH_MAX))


def merge_deadlines(items) -> list:
    """课务条目按 `src` **有交集**合并。**纯函数。**

    ⚠️ 合并是必须的：正则路径（句子一定稿就命中）与模型路径（原子里的 `课务`）
       **必然会撞上同一条** —— 前者看的是原句，后者看的是提炼后的要点，
       它们指的是同一件事。不合并的话用户会看到同一条 deadline 出现两次。
    ⚠️ `source` 两个都记（`"model+regex"`）而不是后写覆盖：那是**来源证据**，
       丢掉它就再也说不清这条是"哨兵抓的"还是"模型读出来的"。
    """
    out: list = []
    for d in (items or ()):
        if not isinstance(d, dict):
            continue
        src = set(d.get("src") or ())
        hit = next((o for o in out if src & set(o.get("src") or ())), None)
        if hit is None:
            out.append(dict(d))
            continue
        hit["src"] = sorted(set(hit.get("src") or ()) | src)
        both = {str(hit.get("source") or ""), str(d.get("source") or "")}
        hit["source"] = "+".join(sorted(x for x in both if x))
        hit["changed"] = bool(hit.get("changed") or d.get("changed"))
    return out


def chapter_path_for(session_path):
    """会话文件 → 章节旁路文件路径。**参数为 None 返回 None。**

    ⚠️ 这是那条路径的**唯一定义点** —— `ChapterWriter` 与 `obsidian_writer`
       都要它（后者下课时要 `load()` 它来写笔记）。各写一遍迟早分叉，
       而分叉的表现是「课上写的和课后读的不是同一个文件」，**静默的**。
    """
    if not session_path:
        return None
    return pathlib.Path(str(pathlib.Path(session_path).with_suffix("")) + CHAPTER_TAIL)


# ---------------------------------------------------------------- 旁路写入器
class ChapterWriter:
    """写 `sessions/<同名>.chapters.jsonl`。**每次写完就 flush。**

    ⚠️ 收的是**路径**（不是会话路径）—— 推导在 `chapter_path_for` 那一处，
       别让两个函数各推一遍。没有路径 = 不写（不报错）。

    ⚠️ 纪律照 `atom.AtomWriter`：
       · `flush()` **必须**（Python 的 8KB 缓冲，同 `testmode.py` 那个坑）
       · `fsync` **刻意不做** —— 会话 `.md` 自己也只到页缓存
       · 文件**不删** —— 它是这份笔记的证据
       · `close()` **幂等**
    ⚠️⚠️ **但有一处刻意不同**：`close()` 之后 `append()` 是 **no-op**（见模块头第 4 条）。
    """

    def __init__(self, path=None):
        self._path = pathlib.Path(path) if path else None
        self._h = None
        self._n = 0
        self._closed = False

    @property
    def path(self):
        return self._path

    def _handle(self):
        # ⚠️ **这里刻意不判 `_closed`** —— 那个判在 `append()` 里，一处就够。
        #    第一版两处都判，变异验证时发现删掉这一处**判据照样全绿**
        #    （因为 `_handle` 只被 `append` 调，根本不可达）→ 那就是死代码。
        if self._h is None and self._path is not None:
            self._h = self._path.open("a", encoding="utf-8")
        return self._h

    def append(self, records) -> int:
        """追加一批（dict 或 `Chapter`）。返回**写成了几条**。

        ⚠️ 写失败**不是致命错**（同 `AtomWriter` 的取舍）：少几行章节记录
           比把整条流水线带崩轻得多。但**关了之后的 no-op 不是失败**，
           所以它**不出声**（迟到的线程回来是预期内的，不该刷屏）。
        """
        recs = []
        for r in (records or ()):
            recs.append(r.as_json() if isinstance(r, Chapter) else r)
        if not recs:
            return 0
        if self._closed:
            return 0                                     # ⭐ 静默丢弃（见 docstring）
        try:
            # ⚠️ `_handle()` 必须在 `try` **里面** —— 它是唯一做 I/O 的地方。
            #    `atom.py` 那条注释（2026-09-29）逐字适用：放在外面的话，
            #    磁盘只读 / 目录被删时 `OSError` 直接向上抛，与本函数「不带崩流水线」自相矛盾。
            h = self._handle()
            if h is None:
                return 0
            for r in recs:
                h.write(json.dumps(r, ensure_ascii=False) + "\n")
            h.flush()
            self._n += len(recs)
            return len(recs)
        except Exception as e:                            # noqa: BLE001
            print(f"⚠ 章节写入失败({str(e)[:60]}); 课堂不受影响")
            return 0

    def close(self) -> None:
        """幂等。文件**不删**。⭐ 关了就再也不开（`_closed` 是单向的）。"""
        self._closed = True
        if self._h is not None:
            try:
                self._h.close()
            except Exception:                             # noqa: BLE001
                pass
            self._h = None

    @property
    def count(self) -> int:
        return self._n


# ---------------------------------------------------------------- 读回
def load(path) -> dict:
    """读回一个 `.chapters.jsonl` → `{"windows": [...], "chapters": [...], "deadlines": [...]}`。

    - **窗口**：全部（它们是"那一窗说了什么"的流水，不覆盖）
    - **章节**：每个 `id` **最后一条** —— 后写覆盖先写，所以正式版自然替换临时版
    - **课务**：按 `src` 有交集合并（`merge_deadlines`）

    ⚠️ 坏行跳过 —— 它是**系统边界**（可能是上个进程写的、可能写到一半被杀）。
       同 `atom.load` 的宽容度：一行坏掉不该让整份纲要消失。
    ⚠️ 文件不存在返回空的三组，**不抛** —— 旧会话 / 纯转录档 / 开关关着时都是这种情况。
    """
    windows: list = []
    last: dict = {}
    order: list = []
    deadlines: list = []
    # ⚠️⚠️ **`path` 为 `None` 是预期输入，不是理论边界**：`chapter_path_for()` 在
    #    会话路径为空时正是返回 `None`（开关关着 / 纯转录档 / 旧会话都走这条），
    #    而 `obsidian_writer` 下课时就是拿它的返回值直接来 `load()`。
    #    少了这一行，`pathlib.Path(None)` 抛的是 **`TypeError`**，
    #    而下面只吞 `OSError` → 从「不抛」的契约里逃出去。
    #    （2026-09-30 `ocr` 抓到的。我自己的判据只试了「文件不存在」，没试 `None`。）
    # ⚠️ **不要图省事把 `TypeError` 也塞进下面那个 `except`** —— 第一版就是那么写的，
    #    变异验证一测：**删掉这一行判据照样全绿**（因为宽吞把它兜住了）→ 那这行就是死代码。
    #    而且宽吞 `TypeError` 会把「真出了别的类型错」伪装成「文件不存在」。
    #    → 显式守门 + 窄 `except`，两者**各管一段**。
    if not path:
        return {"windows": [], "chapters": [], "deadlines": []}
    try:
        txt = pathlib.Path(path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return {"windows": [], "chapters": [], "deadlines": []}
    for ln in txt.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            obj = json.loads(ln)
        except Exception:                                 # noqa: BLE001
            continue
        if not isinstance(obj, dict):
            continue
        kind = obj.get("type")
        if kind == WINDOW:
            windows.append(obj)
        elif kind == CHAPTER:
            cid = obj.get("id")
            # ⚠️⚠️ `id` 必须是**可哈希**的。`{"id": [1]}` 会让 `cid not in last`
            #    抛 `TypeError: unhashable type: 'list'` —— 从「坏行跳过、不抛」
            #    的契约里逃出去，而这是**课后的笔记路径**（`obsidian_writer` 要调）。
            #    （2026-09-30 独立审查抓到。）
            if not isinstance(cid, (int, str)):
                continue                                 # 坏行跳过
            if cid not in last:
                order.append(cid)
            last[cid] = obj                              # 后写覆盖先写
        elif kind == DEADLINE:
            # ⚠️ 同理：`src` 不是列表时 `merge_deadlines` 里的 `set(...)` 会抛。
            if not isinstance(obj.get("src"), (list, tuple)):
                continue
            deadlines.append(obj)
    return {"windows": windows,
            "chapters": [last[c] for c in order],
            "deadlines": merge_deadlines(deadlines)}
