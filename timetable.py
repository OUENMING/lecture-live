#!/usr/bin/env python3
"""课表导入 —— 从 `.ics` 认出「哪几门课、各在什么时段」。**纯函数，零新依赖。**

## ⭐ 为什么是「按 `SUMMARY` 分组」而不是「提取课号」

调研实拉了 **8 个真实 `.ics`**（不是二手描述），课号**没有标准位置，而且经常根本没有**：

```
EE4790 - Circuit Fundamentals          ← 课号 - 课名       (TU Delft / MyTimetable)
Algorithm Design Techniques (25CS301)  ← 课名 (课号)       (印度 digiCampus)
CSCI-104L (Lecture)                    ← 只有课号 + 课型   (USC Banner，没有课名)
Calculus (Lecture)                     ← 只有课名 + 课型   (SUTD)
📝 EXAM: IMAGE PROCESSING              ← 课号藏在 DESCRIPTION (马来 TARUMT)
Teamarb.u.Teamleitg.iProj              ← 德语缩写，什么都没有 (德国 Progotec)
```

`RFC 5545 / RFC 7986` **完全没有**课程 / 课号 / 教师 / 学期字段（非标准字段一律走 `X-`）；
`CATEGORIES` 在**全部 8 个真文件里一次都没出现**。

→ 所以：**不同的 `SUMMARY` 字符串本身就是「一门课」**，不依赖任何格式假设。
课号只是「有就拿来当默认名」的优化，没有也能用（用户改名）。

## 本产品只覆盖两所学校（作者 2026-09-29：用户只有这两家）

| 学校 | 课表怎么来的 | SUMMARY 形态 | 课号 |
|---|---|---|---|
| **UCD 都柏林** | 学校 push 进学生的 Google 日历 | `ECON10740: Exploring Economics (Lecture)` | `ECON10740`（无空格） |
| **UC Davis** | ⚠️ **没有官方 `.ics`**（只有要登录的 Schedule Builder） | 三家第三方扩展各一种 | `ECS 036B` ⚠️ **空格在课号里面** |

⚠️ 所以**按空格切词一定出错** —— 这里一律不切词。

## ⚠️ 不展开 RRULE

问题是「**现在**该上哪门课」，不是「列出所有课次」→ `BYDAY` + `DTSTART` 的时间就够，
而且这消掉了一个大不确定性（Google 有时导 `RRULE`、有时**已预展开**成多条 `VEVENT`，
两条路在这里都只看 `DTSTART` / `BYDAY`）。

⚠️ **但 `INTERVAL` 与 `EXDATE` 必须读** —— 否则「这周到底上不上」会错（双周 tutorial、
停课日）。它们挂在 `Slot` 上，由 `Slot.active_at()` 判（`active_on()` 与 `suggest()`
都走它 —— 那条规则**只此一处**）。

## 隐私

只读 `SUMMARY / DTSTART / DTEND / RRULE / EXDATE`。
⚠️ **`DESCRIPTION` / `LOCATION` / `UID` 一律不看** —— `[一手]` UBC 的提醒逐字：
「Shared calendars often contain **other people's sensitive information that they
haven't consented to share**」。那些字段里常有教师名、同学名，而这个软件会把内容
写进笔记。（`UID` 我们只在「重复导入去重」的讨论里提过，**实现里没用它** ——
 2026-09-29 修正这里的字段表：它原来把 `LOCATION` / `UID` 也列成"只读"，与实现不符。）
"""
from __future__ import annotations

import dataclasses
import datetime
import re

#: 星期缩写 → `datetime.weekday()` 的编号（0 = 周一）
WEEKDAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}

#: 课型后缀。⚠️ **同一门课的不同课型课号相同**（`CSCI-104L (Lecture)` / `(Lab)`），
#: 靠括号区分。这里把它从课名里剥掉，好让同课号的不同课型**归到一门课**。
#: ⚠️ **全角括号也收** —— 中文课表写的是 `社会学导论（实验）`，只写 ASCII `[\(\[]`
#:    会漏掉（2026-09-29 判据抓出来的）。
_SECTION = re.compile(
    r"\s*[\(\[（【]\s*(lecture|lab|laboratory|tutorial|seminar|workshop|quiz|exam|"
    r"discussion|recitation|section|class|studio|practical|cohort class|"
    r"lecture\s*/\s*seminar|大课|小课|研讨|实验|习题|练习)\s*[\)\]）】]\s*$", re.I)

#: 前后都带分隔符的课号（`CODE: Title` / `Title - CODE`）。
#: ⚠️ `ECS 036B` 那种**课号里有空格**的形态也吃得住 —— 不许按空格切。
#: ⚠️⚠️ 位数要留到 **6**：UCD 是 `ECON10740`（**五位**）。原来写 `\d{2,4}`
#:    时它一个都猜不出来（`10740` 匹配不上 → 整个正则退化成不匹配），而且**不报错**。
_CODE_RE = r"[A-Z]{2,6}[-\s]?\d{2,6}[A-Z]?"
_CODE_SLOT = re.compile(rf"^\s*({_CODE_RE})\s*[:：\-–—]\s*(.+)$")
_CODE_TAIL = re.compile(rf"^(.+?)\s*[\-–—(]\s*({_CODE_RE})\s*[\)]?\s*$")
#: 整串就是课号（USC Banner 的 `CSCI-104L`、SUTD 那种只有课名的反例会被排除）
_CODE_ONLY = re.compile(rf"^\s*({_CODE_RE})\s*$")
#: ⭐ **UC Davis 形态**：`ECS 170 001 Introduction to AI` —— 课号打头、**没有分隔符**，
#: 后面还跟一个纯数字班号。⚠️ 这是唯一允许「按空格取一段」的地方，
#: 而且取的是「字母 + 数字」这个**模式**，不是固定索引。
_CODE_LEAD = re.compile(rf"^\s*({_CODE_RE}(?:\s\d{{1,3}}[A-Z]?)?)\s+(\S.*)$")


@dataclasses.dataclass(frozen=True)
class Slot:
    """「每周几的几点有课」。**不展开 RRULE** —— 只记模式，`active_at()` 判这一周上不上。"""
    weekday: int              # 0 = 周一（`datetime.weekday()` 同义）
    hh: int
    mm: int
    minutes: int              # 时长（分钟）；`0` = 文件里没说
    interval: int = 1         # `RRULE:INTERVAL=`；1 = 每周
    anchor: datetime.date | None = None   # `DTSTART` 那天 —— 算 INTERVAL 相位用
    skip: tuple = ()          # `EXDATE` 的日期（停课日）

    def active_at(self, when: datetime.datetime) -> bool:
        """`when` 这一刻，这个 slot **正该在上**吗 —— 那条规则的**唯一定义点**。

        ⚠️ **只看周几 + 小时 + 是不是该出现的那一周**，不看分钟（面板要的是
           「现在大概是哪门」，不是精确排课）。
        ⚠️ 2026-09-29 立：原来这条判据在本文件里**写了两遍** —— `active_on()`
           一处、`suggest()` 内层又一处 —— 而文件注释多处写「由 `active_on()` 判」。
           两处规则分叉的话，`suggest`（面板真正走的那条）会和文档说的不一致。
        ⚠️ 它替掉了原来那个无人调用的 `starts_at()`：那个**漏了 `on_week`**
           （于是双周课/停课日会误判），而且 docstring 说「分钟级容差由调用方给」
           却没留参数 —— 调用方根本给不了。**留着比删掉危险**，所以删。
        """
        return (self.weekday == when.weekday() and self.hh == when.hour
                and self.on_week(when.date()))

    def on_week(self, when: datetime.date) -> bool:
        """⭐ **双周课 / 停课日** —— 不看这一条，`INTERVAL=2` 的课会周周误报。"""
        if when in self.skip:
            return False
        if self.interval <= 1 or self.anchor is None:
            return True
        return ((when - self.anchor).days // 7) % self.interval == 0


@dataclasses.dataclass(frozen=True)
class Course:
    """一门课 = **一个 `SUMMARY` 归一化之后的名字**（不是课号 —— 见模块头）。"""
    name: str
    slots: tuple              # `Slot` 的元组
    events: int               # 这条 SUMMARY 下有多少条 VEVENT


# ------------------------------------------------------------------ 折行
def unfold_bytes(data: bytes) -> list:
    """RFC 5545 §3.1 折行还原 → 一行一条 content line。

    ⚠️⚠️ **必须在字节层拼，不能先解码** —— 规范说折行「**可能切在 UTF-8 多字节中间**」，
       先解码再拼会在那个字节上产出替换字符（`\\ufffd`），而且**不报错**。
    ⚠️ 规范写的是 `CRLF`，但真实 feed 里 `LF` 也常见 → 三种行尾都收。
    """
    out: list = []
    for ln in re.split(rb"\r\n|\n|\r", data):
        if not ln:
            continue
        if ln[:1] in (b" ", b"\t") and out:
            out[-1] += ln[1:]                      # 续行 —— 拼在**上一条的字节**上
        else:
            out.append(ln)
    return [x.decode("utf-8", errors="replace") for x in out]


def _split_prop(line: str):
    """`NAME;P=1;Q="a:b":VALUE` → `(NAME, {"P": "1", "Q": "a:b"}, "VALUE")`。

    ⚠️ 冒号**可能在带引号的参数里**（`TZID="Europe/Dublin"` 少见但有），所以要扫引号状态；
       直接 `split(":", 1)` 会把它切错。
    """
    head, val = line, ""
    q = False
    for i, ch in enumerate(line):
        if ch == '"':
            q = not q
        elif ch == ":" and not q:
            head, val = line[:i], line[i + 1:]
            break
    parts = head.split(";")
    name = parts[0].upper()
    params = {}
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            params[k.upper()] = v.strip('"')      # ⚠️ TZID 常带引号
    return name, params, val


# ------------------------------------------------------------------ 日期
def _parse_dt(value: str, params: dict):
    """`DTSTART` 的值 → `datetime`。解不出返回 `None`（**不抛** —— 一份课表里
    坏一条不该让整份导入失败）。"""
    v = (value or "").strip()
    utc = v.endswith("Z")
    v = v[:-1] if utc else v
    try:
        if "T" in v:
            dt = datetime.datetime.strptime(v, "%Y%m%dT%H%M%S")
        else:                                      # `VALUE=DATE`：全天事件
            d = datetime.datetime.strptime(v, "%Y%m%d").date()
            return datetime.datetime.combine(d, datetime.time(0, 0))
    except ValueError:
        return None
    tzid = params.get("TZID")
    if utc:
        return dt.replace(tzinfo=datetime.timezone.utc)
    if tzid:
        try:
            from zoneinfo import ZoneInfo
            return dt.replace(tzinfo=ZoneInfo(tzid))
        except Exception:                          # noqa: BLE001
            # ⚠️ **非 IANA 的 TZID**（Outlook 爱写 `China Standard Time`）→ 当**浮动时间**。
            #    宁可差几小时也不要整条丢掉；调用方从 `warn` 里看得到。
            return dt
    return dt                                      # 浮动时间


def _parse_rrule(value: str) -> dict:
    return {k.upper(): v for k, v in
            (p.split("=", 1) for p in (value or "").split(";") if "=" in p)}


# ------------------------------------------------------------------ 名字
def normalize_name(summary: str) -> str:
    """`SUMMARY` → 「一门课」的名字。**只剥课型后缀**，其余一字不动。

    ⚠️ **不剥课号** —— 见模块头：课号可能不在、可能带空格、可能藏在别处。
       「有课号就拿来当默认名」是调用方的事（`suggest_code()`），不是归并的事。
    """
    s = (summary or "").strip()
    while True:
        t = _SECTION.sub("", s).strip()
        if t == s or not t:
            break
        s = t
    return s or (summary or "").strip()


def suggest_code(name: str) -> str:
    """从课名里**猜**一个课号当默认名；猜不出返回 `""`。⚠️ 猜错没关系 —— 用户可以改。

    吃四种真形态（都来自实拉的样本）：
        `ECON10740: Exploring Economics`   → `ECON10740`   (UCD 都柏林)
        `ECS 170 001 Introduction to AI`   → `ECS 170 001` (UC Davis，课号里有空格)
        `CSCI-104L`                        → `CSCI-104L`   (USC Banner，整串就是课号)
        `Algorithm Design Techniques (25CS301)` → `""`      ⚠️ **故意不猜**（年份前缀那种
                                                形态太杂，猜错不如让用户改名）
    """
    s = (name or "").strip()
    for rx in (_CODE_SLOT, _CODE_ONLY, _CODE_TAIL, _CODE_LEAD):
        m = rx.match(s)
        if m:
            return m.group(1).strip()
    return ""


# ------------------------------------------------------------------ 主入口
def _local(dt, tz):
    """把带时区的时间**换到目标时区**再取小时/星期。

    ⚠️⚠️ **不做这一步就错**：feed 若用 UTC（`DTSTART:20260915T100000Z`）或学校所在地
       以外的时区，`start.hour` 拿到的是**那个时区**的小时 —— 对本地是错的，
       而且**不报错**，只会安安静静地把课排在错的时间上。
    ⚠️ **浮动时间（没有 TZID 也没有 `Z`）不换** —— 它本来就是"墙上钟"，按写的算。
    """
    if dt is None or dt.tzinfo is None:
        return dt
    return dt.astimezone(tz) if tz is not None else dt.astimezone()


# ------------------------------------------------------------------ 主入口
def parse(data: bytes, *, tz=None):
    """`.ics` 字节 → `([Course, …], [警告, …])`。**纯函数。**

    `tz` = 取小时/星期时换算到的目标时区（`None` = 本机）。⚠️ 做成参数是为了
    **判据不绑机器** —— 否则同一份夹具在不同时区的机器上结论不同。

    ⚠️ 返回值是**两个**（同 `corpus.keywords` 那条纪律：降级/异常必须说出来，
       不能和正常结果长得一样）。
    """
    warn: list = []
    courses: dict = {}
    cur = None
    for line in unfold_bytes(data):
        name, params, val = _split_prop(line)
        if name == "BEGIN" and val.strip().upper() == "VEVENT":
            cur = {"summary": "", "start": None, "dur": 0, "rrule": {}, "exdate": []}
            continue
        if name == "END" and val.strip().upper() == "VEVENT":
            if cur is not None:
                _absorb(courses, cur, warn)
            cur = None
            continue
        if cur is None:
            continue
        if name == "SUMMARY":
            cur["summary"] = val.strip()
        elif name == "DTSTART":
            dt = _local(_parse_dt(val, params), tz)
            if dt is None:
                warn.append(f"读不懂的 DTSTART：{val[:40]!r}")
            cur["start"] = dt
        elif name == "DTEND":
            dt = _local(_parse_dt(val, params), tz)
            if dt is not None and cur.get("start") is not None:
                # ⚠️⚠️ **两侧 tzinfo 形态必须一致才相减**（2026-09-29 修）。
                #    `_parse_dt` 对「浮动时间 / `VALUE=DATE`」返回 **naive**，对带 `Z`
                #    或 `TZID` 的返回 **aware** —— 一条 VEVENT 里两种混用时
                #    `dt - cur["start"]` 抛 `TypeError: can't subtract offset-naive
                #    and offset-aware datetimes`，**整个 `parse()` 中断**，
                #    违反本模块自述的「坏一条不该让整份导入失败」。
                #    形态不一致就**不记时长**（`minutes=0` 表示"文件里没说"），
                #    其余字段照收 —— 比整份导入失败好得多。
                a, b = cur["start"], dt
                if (a.tzinfo is None) == (b.tzinfo is None):
                    cur["dur"] = max(0, int((b - a).total_seconds() // 60))
                else:
                    warn.append(f"「{cur['summary'][:30]}」的 DTEND 与 DTSTART "
                                f"时区形态不一致，这条不记时长")
        elif name == "RRULE":
            cur["rrule"] = _parse_rrule(val)
        elif name == "EXDATE":
            for piece in val.split(","):
                d = _local(_parse_dt(piece, params), tz)
                if d is not None:
                    cur["exdate"].append(d.date())
        # ⚠️ `DESCRIPTION` / `LOCATION` **刻意不读** —— 见模块头那条隐私纪律。
    if not courses:
        warn.append("这份 .ics 里一条可用的课程日程都没有")
    return ([Course(c["name"], tuple(c["slots"]), c["events"])
             for c in sorted(courses.values(), key=lambda x: (-x["events"], x["name"]))], warn)



def _absorb(courses: dict, ev: dict, warn: list) -> None:
    """把一条 VEVENT 并进它所属的课。"""
    raw = ev.get("summary") or ""
    if not raw.strip():
        warn.append("有一条日程没有 SUMMARY，跳过了")
        return
    start = ev.get("start")
    if start is None:
        warn.append(f"「{raw[:30]}」那条没有能读懂的 DTSTART，跳过了")
        return
    key = normalize_name(raw)
    c = courses.setdefault(key, {"name": key, "slots": [], "events": 0})
    c["events"] += 1
    rr = ev.get("rrule") or {}
    try:
        interval = max(1, int(rr.get("INTERVAL", "1")))
    except ValueError:
        interval = 1
    days = [WEEKDAYS[d.strip().upper()[:2]] for d in rr.get("BYDAY", "").split(",")
            if d.strip().upper()[:2] in WEEKDAYS] or [start.weekday()]
    for wd in days:
        slot = Slot(weekday=wd, hh=start.hour, mm=start.minute,
                    minutes=ev.get("dur", 0), interval=interval,
                    anchor=start.date(), skip=tuple(sorted(set(ev.get("exdate") or []))))
        if slot not in c["slots"]:
            c["slots"].append(slot)


# ------------------------------------------------------------------ 回答「现在上哪门」
def active_on(course: Course, when: datetime.datetime) -> list:
    """这门课在 `when`（本地时刻）**正开着**的那些 slot。

    ⚠️ **只看周几 + 小时 + 是不是该出现的那一周**，不看分钟 —— 面板要的是
       「现在大概是哪门」，不是精确排课。`minute` 留给调用方按需收紧。
    """
    return [s for s in course.slots if s.active_at(when)]


# ------------------------------------------------------------------ 存 / 取
#: 课表存哪、存什么形状。⚠️ 存的是**解析结果**（`Slot`），不是原始 `.ics` ——
#: 原始文件可能上兆，而这个只需要 `(周几, 时分, 时长, 间隔, 锚点, 停课日)`。
def dump(entries) -> dict:
    """`[(课号, [Slot, …]), …]` → 可 JSON 的 dict。**纯函数，零 I/O。**"""
    out = {}
    for code, slots in entries or ():
        out[str(code)] = [
            {"d": s.weekday, "h": s.hh, "m": s.mm, "min": s.minutes,
             "iv": s.interval,
             "a": s.anchor.isoformat() if s.anchor else None,
             "skip": [d.isoformat() for d in s.skip]}
            for s in slots
        ]
    return out


def load(obj) -> list:
    """`dump()` 的逆。**读不懂的条目直接跳过**（不抛）—— 一份坏数据
    不该让整个面板起不来。

    ⚠️⚠️ **两道形状守卫是 2026-09-29 补的**：原来只有 `Slot(...)` 的构造在 `try` 里，
    而下面两种形状会让异常**逃出整个 `load()`** ——
      · `{"X": [1]}`（行不是 dict）→ `r.get("a")` 抛 `AttributeError`，
        它**不在** `except (KeyError, TypeError, ValueError)` 里；
      · `{"X": 5}`（`rows` 不可迭代）→ `for r in rows` 抛 `TypeError`，
        而它在 `try` **之外**（`TypeError` 虽在列表里也救不到这条语句）。
    ⚠️ 唯一的调用方是 `entry_panel._guesses()`，那里 `T.load(...)` **没有兜底**，
       而 `store.load_json` **只校验顶层是 dict、不校验嵌套形状** →
       一份被手改坏的 `timetable.json` 会让**整个面板 build 时抛异常**。
    """
    out = []
    if not isinstance(obj, dict):
        return out
    for code, rows in obj.items():
        if not isinstance(rows, (list, tuple)):
            continue
        slots = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            try:
                a = r.get("a")
                slots.append(Slot(
                    weekday=int(r["d"]), hh=int(r["h"]), mm=int(r.get("m", 0)),
                    minutes=int(r.get("min", 0)), interval=max(1, int(r.get("iv", 1))),
                    anchor=datetime.date.fromisoformat(a) if a else None,
                    skip=tuple(datetime.date.fromisoformat(x)
                               for x in (r.get("skip") or ()))))
            except (KeyError, TypeError, ValueError):
                continue
        if slots:
            out.append((str(code), slots))
    return out


# ------------------------------------------------------------------ 现在该上哪门
@dataclasses.dataclass(frozen=True)
class Guess:
    """一条「现在大概是这门课」的猜测。⭐ **带理由** —— 见 `suggest()`。"""
    course: str
    why: str
    score: float


def suggest(entries, when, *, history=None, limit=3) -> list:
    """`when`（本地时刻）最可能是哪几门课。**纯函数** —— 判据指得到它。

    入参全是纯数据：
      `entries = [(课号, [Slot, …]), …]` —— 课表（`.ics` 解析出来的，或将来 EventKit 的）
      `history = {课号: [(weekday, hh), …]}` —— **历史**：这门课过去在哪些时段上过
                                                （一节一条，零权限的兜底信号）

    ⭐ **每条都带 `why`**：`[一手]` Microsoft HAX G11 逐字「**Make clear why the
       system did what it did**」。面板要把这句话显示出来 ——
       否则「为什么这张卡排第一」对用户是猜的，而猜错的代价是**整节课术语表全错**。
    ⚠️ **不确定就返回空表**：宁可什么都不预选，也别硬凑一个第一。
       （HIG 那条是「把最可能的放第一」，不是「必须放一个第一」。）
    ⚠️ **两条信号都是二元的，不看次数**：课表命中 = 1.0、历史命中 = 0.5，
       **过去 1 次和过去 5 次给同一个分** —— 「这门课在这个时段上过」见过一次
       模式就成立了，次数只影响**措辞**（"过去也有 N 次"），不改变证据强度。
       给次数加权会造出一个没人验证过的magic 公式，而它直接决定"预选哪张卡"。
    ⚠️ 两条信号的权重是**故意拉开**的：课表（1.0）> 历史（0.5）。
       历史只是「过去常这样」，课表是「今天就是这样」。
    """
    found: dict = {}
    for code, slots in entries or ():
        # ⚠️ 走 `active_at` —— 与 `active_on()` **同一个定义点**（2026-09-29）。
        #    原来这里内联了一份同样的判定，而文件注释写着「由 `active_on()` 判」。
        hit = [s for s in (slots or ()) if s.active_at(when)]
        if hit:
            found[str(code)] = Guess(
                str(code), f"课表上这个点就是这门（{hit[0].hh:02d}:{hit[0].mm:02d}）", 1.0)
    for code, slots in (history or {}).items():
        n = list(slots or ()).count((when.weekday(), when.hour))
        if not n:
            continue
        prev = found.get(str(code))
        why = ((prev.why + f"；过去也有 {n} 次") if prev
               else f"过去有 {n} 次是这个时候上的")
        found[str(code)] = Guess(str(code), why, (prev.score if prev else 0.0) + 0.5)
    return sorted(found.values(), key=lambda g: (-g.score, g.course))[:limit]

