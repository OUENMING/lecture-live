#!/usr/bin/env python3
"""原子层 —— 把一节课切成「可以单独引用的小块」。**三条线共用的地基。**

消费它的三条线（`docs/PLAN-roadmap.md §2.6`）：

- **重点标注** = 给 `讲者强调` 的 atom 加一层「作者确认过没有」
- **全局检索** = 把这些 atom 索引起来（`find.py` 的 ① 级）
- **声纹守卫** = 查的是同一张「课号」表

⚠️ **三条线共用一层地基是刻意的**：各建一份索引 = 各写一套「什么算重点」。

## 住哪：`sessions/<同名>.atoms.jsonl`（旁路文件）

⭐ **照 `.lost.jsonl` 的成例，绝不碰会话 `.md`。** 理由仓库里写死过
（`obsidian_writer._lost_path` 的 docstring 逐字）：抬头正则 `_TS` 是**行尾锚定**的，
而会话格式是 **`obsidian_writer` 写 / `_parse` 读回 / `cl last` grep** 的
**三方共享契约** —— 抬头多一个后缀，`_parse` 就认不出那一条，
会把它的 EN/ZH/ASR **静默盖到上一条头上**。

## ⚠️⚠️ `kind` 的枚举与「考试重点」**不是一回事**（2026-09-28 实测）

`讲者强调` = **模型判断"讲者强调了这段"**（语义）。
`考试明示` = **字符串命中明示用语**（`this will be on the test`，可机械核验）。

实测：探针那 24 条 `明示强调` 里**只有 2 条**真的含考试相关字样 ——
其余 22 条是"读文章前先看 abstract"这种**语义强调**。
→ **两个概念、两个名、各自一处定义**（`PLAN-panel-ux.md §9.1` 自己警告过
「两处各自判断 = 一条纪律两处定义」）。**别把 `讲者强调` 当重点信号用。**

## 不变量

- **只增不改** —— 但**只有最后一个主题节点可改写**，下一个开出即冻结
  （⭐ 与字幕「只有底行可改写」**是同一条规则**）
- **每条必须引用来源句 id**，且 id 要落在窗口内（机械闸门 `traceable`）
- **「这段没什么可记」是合法输出** —— 闲聊/课务不该被硬凑成要点
- ⚠️ **独立 worker、只读 `finals` 副本**；崩了只是没有 atom，**录课不受影响**
- ⚠️ **纯转录档（`.mode == "raw"`）整条不开** —— `DESIGN.md:187` 逐字
  「**纯转录** | **一个模型请求都不发**」。这条契约不能含糊。
"""
from __future__ import annotations

import dataclasses
import json
import pathlib
import time

ATOM_TAIL = ".atoms.jsonl"
MODEL = "deepseek-chat"
MAX_TOKENS = 700
TEMPERATURE = 0.0
MAX_POINTS = 5

# ⚠️ **this is the single definition point** for `kind`（探针的 SYS 从这里取）。
#    `讲者强调` 刻意**不叫** `明示强调` —— 见模块头那条消歧。
KINDS = ("主题", "要点", "定义", "例子", "课务", "讲者强调")

SYS = """你在听一节课的实时转录, 每段给你该段新增的英文定稿(带行号)。
你的任务**不是**写摘要, 而是**记下这段里真正新出现的信息**, 供学生课上快速重入。

输出严格 JSON:
{"topic": "本段主题(中文为主, 术语保留英文, 不超过 12 字)",
 "points": [{"text": "一条要点(中文, 一句话)",
             "kind": "主题|要点|定义|例子|课务|讲者强调",
             "src": [3, 4]}]}

硬性要求:
- **没有可记的就返回 `{"topic": "", "points": []}`** —— 闲聊、点名、设备调试、
  重复上段已经说过的东西, 都属于"没有可记的"。**宁可空, 不要凑**。
- 每条 point 的 `src` 必须是给你的行号里**真实存在**的; 不许编。
- `text` 里不许出现行号以外的引用标记; 单行, 不带换行。
- 最多 5 条。只依据给的内容, 不引入外部知识。
- `讲者强调` 只标**讲者明确强调**的那句（"记住这个"/"这一点很重要"），
  **不是**"你觉得重要"。"""


@dataclasses.dataclass(frozen=True)
class Atom:
    """一条原子。字段是 `PLAN-panel-ux.md §17.2` 定的那一组，一字不改。"""
    id: int
    t: str                       # "14:23:06"，与 `.lost.jsonl` 的 `t` 同形
    epoch: float
    src: list                    # 来源句 id（**在窗口内**，机械闸门保证）
    kind: str
    text: str
    terms: list = dataclasses.field(default_factory=list)

    def as_json(self) -> dict:
        return {"id": self.id, "t": self.t, "epoch": self.epoch,
                "src": list(self.src), "kind": self.kind, "text": self.text,
                "terms": list(self.terms)}


# ---------------------------------------------------------------- 机械闸门
def traceable(points, lo_line: int, hi_line: int) -> tuple:
    """`(可追溯条数, 总条数)`。`src` 是**行号**，必须落在 `[lo_line, hi_line]` 内且非空。

    ⚠️⚠️ **参数是行号，不是时间跨度。** 第一版传的是窗口的当日秒数
    （`50836..51101`），而模型返回的 `src` 是 `[17]` 这种行号 —— 两个域根本不是一个，
    于是可追溯率**按构造恒为 0**，看起来像"模型在编引用"。**判据自测当时也过了**
    （合成数据两边都是小整数，撞不出这个错）—— **是拿真实输出对不上才发现的**。
    → 这正是 `measure-before-claiming` 那条：**判据要拿真实形状的输入试一次**。

    ⚠️ 这段是从 `docs/experiments/probe_atomic_summary.py` **搬过来**的
    （不是抄一份）：探针改成 `from atom import traceable`。
    """
    ok = tot = 0
    for p in points:
        tot += 1
        src = p.get("src") or []
        if isinstance(src, list) and src and all(
                isinstance(x, int) and lo_line <= x <= hi_line for x in src):
            ok += 1
    return ok, tot


# ---------------------------------------------------------------- 组装
def build_prompt(sentences, prev_topic: str = "") -> str:
    """窗口里的句子 → 给模型的 block。**行号从 0 起**（与 `src` 同域）。"""
    lines = [f"{i}. {s}" for i, s in enumerate(sentences or [])]
    head = ""
    if prev_topic:
        head = (f"上一段的主题是「{prev_topic}」——这段若还是同一主题，"
                f"`topic` 就沿用；开了新的才换。\n\n")
    return f"{head}{chr(10).join(lines)}"


def parse_reply(obj, n_sent: int, *, base_id: int = 0,
                stamp: tuple = ()) -> list:
    """模型回的 dict → `[Atom]`。**这里是全部的机械闸门。**

    - `src` 必须落在 `[0, n_sent-1]`（越界的整条丢掉，不是截断）
    - `kind` 不在 `KINDS` 里 → 归到 `要点`（**fail-open**：宁可归错档，不可静默丢一条）
    - `text` 为空 / 超长的丢掉
    - 最多 `MAX_POINTS` 条
    - **`topic` 为空且 `points` 为空 → 返回 `[]`**（"没什么可记"是合法输出）

    ⚠️ 丢掉而不是修剪：`src` 越界意味着这条**引用不实**，
       修剪成"窗口内最近的那句"是**替模型编引用**。
    """
    if not isinstance(obj, dict):
        return []
    t, epoch = (stamp + ("", 0.0))[:2] if stamp else ("", 0.0)
    out = []
    for p in (obj.get("points") or []):
        if not isinstance(p, dict):
            continue
        text = str(p.get("text") or "").strip().replace("\n", " ")
        if not text or len(text) > 200:
            continue
        src = [x for x in (p.get("src") or []) if isinstance(x, int)]
        if not src or any(x < 0 or x >= n_sent for x in src):
            continue                                     # ⚠️ 整条丢，不修剪
        kind = p.get("kind")
        if kind not in KINDS:
            kind = "要点"                                 # fail-open
        out.append(Atom(id=base_id + len(out), t=t, epoch=epoch,
                        src=sorted(set(src)), kind=kind, text=text))
        if len(out) >= MAX_POINTS:
            break
    return out


def rebase(atoms, base: int) -> list:
    """把 `src` 从**窗口内**行号换成**全局句号**。

    ⚠️ **必须换**：模型看到的是"这次给你的第 0..n 句"，而落盘之后那个 `0`
       指不到任何东西 —— 以后想回查"这条要点出自哪一句"就无从查起。
       `base` = 这个窗口第一句在整节课里的序号（**1-based**，与 `n` 同域）。
    """
    return [dataclasses.replace(a, src=[base + i for i in a.src]) for a in atoms]


def topic_of(obj) -> str:
    """`topic` 字段（空串 = 这段没什么可记）。⚠️ 它**不是** atom，是窗口标签。"""
    return str((obj or {}).get("topic") or "").strip()[:12]


# ---------------------------------------------------------------- 旁路写入器
class AtomWriter:
    """写 `sessions/<同名>.atoms.jsonl`。**每次写完就 flush。**

    ⚠️ 纪律照 `ObsidianWriter` 的 `.lost.jsonl`：
       · `flush()` **必须**（Python 的 8KB 缓冲正是 `testmode.py` 文档里那个坑）
       · `fsync` **刻意不做** —— 会话 `.md` 自己也只到页缓存，只给旁路文件
         更硬的保证是**假契约**
       · 文件**不删** —— 它是这份笔记的证据
       · ⚠️ **`close()` 要放在调用方收尾路径的**最前面**：`close_lost()` 原来放在
         三条早退**之后**，于是答"不保存笔记"时旁路句柄**没关**（2026-09-28 真事故）。
         （⚠️ 那是**当时**的条数；2026-09-29 起 `close()` 有四条早退 —— 多了
         「没有 Obsidian 库」那一条。**规矩不变：护栏永远在早退之前**。）
    """

    def __init__(self, session_path):
        self._path = None
        if session_path:
            self._path = pathlib.Path(
                str(pathlib.Path(session_path).with_suffix("")) + ATOM_TAIL)
        self._h = None
        self._n = 0

    @property
    def path(self):
        return self._path

    def _handle(self):
        if self._h is None and self._path is not None:
            self._h = self._path.open("a", encoding="utf-8")
        return self._h

    def append(self, atoms) -> int:
        """追加一批。返回**写成了几条**（写失败不是致命错 —— 见下）。"""
        if not atoms:
            return 0
        try:
            # ⚠️⚠️ **`_handle()` 必须在 `try` 里面**（2026-09-29 修）—— 它是本方法
            #    唯一做 I/O 的地方（`path.open("a", ...)`）。原来在 `try` **外面**，
            #    于是磁盘只读 / 目录被删 / 权限不足时 `OSError` **直接向上抛**，
            #    与紧随其后那句「绝不让它把流水线带崩」**自相矛盾**。
            #    调用方是 `main` 的 atom 工作线程 —— 一抛就整节课不再落原子。
            h = self._handle()
            if h is None:
                return 0
            for a in atoms:
                h.write(json.dumps(a.as_json(), ensure_ascii=False) + "\n")
            h.flush()
            self._n += len(atoms)
            return len(atoms)
        except Exception as e:                            # noqa: BLE001
            # ⚠️ 记不下来是"少几条要点", **不是"课跑不下去"** —— 与 `mark_lost`
            #    同一条纪律。绝不让它把流水线带崩。
            print(f"⚠ atom 写入失败({str(e)[:60]}); 课堂不受影响")
            return 0

    def close(self) -> None:
        """幂等。文件**不删**（它是这份笔记的证据）。"""
        if self._h is not None:
            try:
                self._h.close()
            except Exception:                             # noqa: BLE001
                pass
            self._h = None

    @property
    def count(self) -> int:
        return self._n


def load(path) -> list:
    """读回一个 `.atoms.jsonl`。坏行跳过（它是**系统边界** —— 可能是上个进程写的、
    末行可能被崩坏截断，与 `_parse` 对会话文件的宽容度一致）。"""
    out = []
    try:
        text = pathlib.Path(path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return out
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            obj = json.loads(ln)
        except ValueError:
            continue
        if isinstance(obj, dict) and obj.get("text"):
            out.append(obj)
    return out


def now_stamp() -> tuple:
    """`("14:23:06", 1759…)` —— 与 `.lost.jsonl` 那两个字段同形。"""
    return time.strftime("%H:%M:%S"), round(time.time(), 3)
