#!/usr/bin/env python3
"""一些事儿 —— 课上听到的**考试 / 作业截止 / 调课 / 其他事务**（数据层）。

用户可见文案一律叫「一些事儿」（作者 2026-10-17 定名：原「课务雷达」）。

## 这一层负责什么

    识别 → 条目 → 决定（记下 / 忽略）→ 提醒事项 / 旁路落盘

- **识别不做新模型**：复用 `live_summary` 已经认出来的 `课务` payload
  （正则 `chapter.deadline_hits` 立刻上屏 + 模型报的先过 Jev 闸门）。
  本模块只加**一道过滤**：这条得**带日期 / 星期 / 明确时间指向**
  （`has_time_pointer`）—— 否则老师只是"聊到考试"，不是一条待办。
- **换算交给 `duty_when`**（纯函数、可单测）：卡片上那个日期字段就是它的输出，
  用 `inferred` 标记「推算」，用户可改（PassAI 的先例：算出来的日期要单独标）。
- **决定写会话旁路文件** `sessions/<同名>.duty.jsonl`（照 `.lost.jsonl` 的做法：
  只追加、flush、失败静默、**绝不改会话 Markdown 抬头**——那条抬头是三方契约）。
- **记下 → macOS「提醒事项」**：`osascript`，不新增 Python 依赖。失败（授权被拒 /
  超时）**不静默**：卡片上写明原因，条目落 `paths.duty_backup()`。
  ⚠️ 写盘走**注入的 runner**（`write_reminder(argv, runner=…)`）—— 测试一个字节都
  不进真「提醒事项」（作者手动验真的那条）。

## ⚠️ 三处形状约定

1. 旁路文件的**事件流**：`{"kind":"item"…}` / `{"kind":"decision"…}` /
   `{"kind":"reminders"…}` —— 后到的事件覆盖先到的（`load` 回放时按 id 合并）。
   只追加，不改行：与 `.lost.jsonl` / `testmode` 同一条纪律。
2. 条目的 `date` 一律是 `YYYY-MM-DD` 字符串（`""` = 没算出来 / 没提），
   `time` 是 `HH:MM`（`""` = 只到天）。UI 与笔记**都不许**再解析第二遍。
3. **线程**：`offer()` 从总结 worker 线程调（`_summ_emit` 那条路），
   其余都在 AppKit 主线程。所以内部一把 `threading.Lock` 管 `_items`。
"""
from __future__ import annotations

import datetime
import json
import pathlib
import re
import subprocess
import threading
import time

import duty_when
import paths

#: 旁路文件后缀（`sessions/<同名>.duty.jsonl`）—— 唯一定义点。
SIDE_TAIL = ".duty.jsonl"

#: 四类，够用、好点（原型 §2 字段设计）。
TYPES = ("考试", "作业截止", "调课", "其他")

#: 提醒事项的列表名（原型 §③ 已定：和私人提醒分开）。
LIST_NAME = "ClassLive 课务"

#: `osascript` 的超时。⚠️ 首次会弹**自动化授权**弹窗 —— 用户正在上课，
#: 不点就一直等。等太久会让人以为卡死，所以 30 秒收工 → 落备份。
WRITE_TIMEOUT_S = 30.0

# ---- 类型词表 -----------------------------------------------------------------
#: ⚠️ 顺序 = 优先级：**改期/停课压过"考的是考试还是作业"**（"the exam is moved to
#:    Friday" 是一条调课，不是考试）。词表宁可宽 —— 卡片上类型可改（三个字的事）。
_RE_CHANGED = re.compile(
    r"\b(postpon\w*|reschedul\w*|cancell?ed|moved?\s+to|instead\s+of|swap\w*|"
    r"no\s+(?:class|lecture|tutorial|lab|seminar)|replace[sd]?\s+by)\b"
    r"|停课|调课|改期|不上课|换课|改到", re.I)
_RE_EXAM = re.compile(
    r"\b(exam|midterm|mid-term|final|quiz|test|assessment|re-?sit)\b"
    r"|考试|测验|期末|期中|补考", re.I)
_RE_WORK = re.compile(
    r"\b(assignment|problem\s+set|pset|homework|essay|report|coursework|submission|"
    r"submit|hand(?:ed|s|ing)?\s+in|due|deadline|upload)\b"
    r"|作业|提交|截止|实验报告", re.I)

#: 标题开头的口水词（`So the first thing is there is an assignment…`）。
_FILLERS = ("so", "and", "but", "ok", "okay", "now", "well", "alright", "right",
            "um", "uh", "yeah", "the first thing is", "first thing is")
_RE_FILLER = re.compile(
    r"^(?:(?:" + "|".join(re.escape(f) for f in _FILLERS) + r")[\s,，。:：]+)+", re.I)

#: 中文里没有空格断词，所以标题按**字数**截；英文按词截。
TITLE_MAX = 30
#: ⭐ **中文摘要**的长度上限（汉字）。作者 2026-10-17：标题要「中文摘要」
#: （「习题集截止」「本周三停课」「下周必须到场」那种）——12–16 个汉字最像标题。
SUMMARY_MAX = 16
#: 中文摘要截断前的从句边界（在这些字符里往回找一个断点）。
_CLAUSE = "，,；;：:、—–（）()「」“”"

#: 中文摘要开头要剥掉的口水/转述词（老师说的话不是待办本身）。
_ZH_FILLER = re.compile(
    r"^(?:那么|所以|然后|接着|现在|于是|哦|嗯|呃|就是|大家|同学们|各位|注意|记得|"
    r"别忘|不要忘|老师(?:说|讲|提到|强调|提醒)|教授(?:说|讲|提到|强调|提醒)|"
    r"我(?:觉得|认为|建议)|你(?:们)?(?:要|得|可以)|请)[\s，,：:、]*")
#: 中文摘要**句尾**的语气词与标点（作者：不许带句号）。
_ZH_TAIL = re.compile(r"[\s。．\.!！?？~～…，,、；;：:]+$")


def classify(quote: str, *, changed: bool = False) -> str:
    """一句话 → 四类之一。**纯函数、不调模型。**

    ⭐ 顺序是判据的一部分：`changed=True`（`chapter.CHANGED_RE` 亲手判的）或词表命中
       调课动词 → **调课**（"the exam is moved to Friday" 首先是"日程变了"）；
       然后是考试、作业截止；都不像 → 其他。
    """
    s = str(quote or "")
    if changed or _RE_CHANGED.search(s):
        return "调课"
    if _RE_EXAM.search(s):
        return "考试"
    if _RE_WORK.search(s):
        return "作业截止"
    return "其他"


def clean_title(text: str, *, limit: int = TITLE_MAX) -> str:
    """标题的**最后一道清洗**：去首尾空白/引号、去句尾标点（**中英文都不带句号**）、
    限长（中文按字数、英文按词，超了给省略号）。

    ⚠️ 「不带句号」是作者 2026-10-17 点名的规矩 —— 标题是**标签**，不是句子。
    """
    s = re.sub(r"\s+", " ", str(text or "")).strip().strip("“”\"'… ")
    s = _ZH_TAIL.sub("", s)
    if len(s) <= limit:
        return s
    head = s[:limit]
    if re.search(r"[A-Za-z0-9]$", head):                 # 英文：别切在词中间
        cut = head.rfind(" ")
        if cut >= limit // 2:
            head = head[:cut]
    else:                                                # 中文：优先在从句边界断
        cut = max((head.rfind(c) for c in _CLAUSE), default=-1)
        if cut >= limit // 2:
            head = head[:cut]
    return head.rstrip(" ,，。；;、") + "…"


def summary_zh(zh: str, *, en: str = "", limit: int = SUMMARY_MAX) -> str:
    """⭐ 一句话 → 卡片/小卡上的**中文摘要标题**（纯函数，判据靠它）。

    来源优先级（作者 2026-10-17：「优先复用已有的那次模型调用；不额外增加实时调用次数」）：
      ① `zh` —— **已有的那份中文**：正则路径上它是这句的即时译文，模型路径上它是
         `Atom.zh`（那次原子调用本来就给的中文）。两条都**不发新请求**。
      ② 没有 `zh`（纯转录档 / 译文还没到）→ 退回**英文原话**的截短（`clean_title`）。

    ⚠️ 规则只做三件事：剥口水词 → 从句边界截短 → 去句尾标点。**不做**"意译成更短的
       词"那一步 —— 那需要模型（见 `SUMMARY_MAX` 与文件头那条"不加调用"）。
    """
    z = _ZH_TAIL.sub("", re.sub(r"\s+", " ", str(zh or "")).strip())
    for _ in range(4):                       # 「那么，同学们注意，…」这种要连剥几层
        nxt = _ZH_FILLER.sub("", z).strip()
        if nxt == z:
            break
        z = nxt
    if z:
        return clean_title(z, limit=limit)
    return clean_title(en, limit=TITLE_MAX) or "（没有原话）"


def clean_quote(text: str, *, limit: int = 56) -> str:
    """「原话」行：**保留标点**（它是引用，不是标题），只折叠空白 + 限长。

    作者 2026-10-17：英文原话只留在「原话」那一行 —— 那一行要**看得出是原话**，
    所以这里**不剥口水词、不去句号**（与 `clean_title` 是两条路）。
    """
    s = re.sub(r"\s+", " ", str(text or "")).strip().strip("“”\" ")
    if len(s) <= limit:
        return s
    head = s[:limit]
    if re.search(r"[A-Za-z0-9]$", head):                 # 英文：别切在词中间
        cut = head.rfind(" ")
        if cut >= limit // 2:
            head = head[:cut]
    return head.rstrip() + "…"


def short_title(quote: str, *, limit: int = TITLE_MAX) -> str:
    """原话 → 卡片上的默认标题（可改）。剥口水词 + 按字数/词截断 + 省略号。"""
    s = _RE_FILLER.sub("", str(quote or "")).strip()
    if not s:
        return "（没有原话）"
    return clean_title(s, limit=limit)


def has_time_pointer(quote: str, *, day) -> bool:
    """⭐ **识别那一层唯一的过滤**：这条课务**指向了某个时间**吗。

    三条之一：
      ① `duty_when.resolve` 算得出具体日期；
      ② 算不出但**有明确时间指向**（`week 6` / `this week` / `by the end of term`）——
         用 `chapter.TIME_ANCHOR_RE`（笔记那道闸门用的同一张表，不另造一份）；
      ③ `duty_when.undecided`（说到时间但本模块不猜）。
    ⚠️ 三者都不命中 → **不认**（"There is an assignment on Brightspace" 这种
       —— 老师只是提了一句，没有任何时候信息；它照旧会进 `live_summary` 的
       deadline 流与上课时的旧规则，只是不占「一些事儿」的小卡）。
    """
    q = str(quote or "")
    if not q.strip():
        return False
    if duty_when.resolve(q, day=day) is not None:
        return True
    if duty_when.undecided(q):
        return True
    try:
        import chapter
        return bool(chapter.TIME_ANCHOR_RE.search(q))
    except Exception:                                     # noqa: BLE001
        return False


def candidate(payload, *, day) -> dict | None:
    """`live_summary` 的 deadline payload → 一些事儿条目（**不含 id / 状态**）。

    返回 `None` = 这条不认（没有时间指向 / 空原话）。
    """
    p = payload if isinstance(payload, dict) else {}
    quote = str(p.get("quote") or "")
    if not has_time_pointer(quote, day=day):
        return None
    when = duty_when.resolve(quote, day=day)
    t, _phrase = duty_when.time_in(quote)
    return {
        "t": str(p.get("t") or ""),
        "quote": quote,
        #: 已有的中文（正则 = 即时译文；模型 = Atom.zh）—— 摘要的来源，见 `summary_zh`。
        "zh": str(p.get("zh") or ""),
        "src": sorted(set(p.get("src") or ())),
        "source": str(p.get("source") or ""),
        "changed": bool(p.get("changed")),
        "type": classify(quote, changed=bool(p.get("changed"))),
        # ⭐ 标题 = **中文摘要**（英文原话只留在 `quote` 那一行）
        "title": summary_zh(p.get("zh") or "", en=quote),
        "date": when.date.isoformat() if when else "",
        "time": t.strftime("%H:%M") if t else "",
        "inferred": bool(when),                # ⭐ 卡片上标「推算」（PassAI 先例）
        "phrase": when.phrase if when else "",
        "undecided": False if when else duty_when.undecided(quote),
    }


# ---- 旁路文件 -----------------------------------------------------------------
def sidecar_path(session_path) -> pathlib.Path | None:
    """`sessions/<同名>.duty.jsonl`（与 `.lost.jsonl` 同一个命名法）。

    ⚠️ **绝不改会话抬头**：抬头正则 `_TS` 是行尾锚定的，多一个字段会把那条
       静默盖到上一条头上（三方契约，见 `obsidian_writer._lost_path`）。
    """
    if not session_path:
        return None
    return pathlib.Path(str(session_path).rsplit(".", 1)[0] + SIDE_TAIL)


def append(path, rec: dict) -> bool:
    """追加一行 JSON（flush；失败静默 `False`）。**只追加，不改行。**"""
    if path is None:
        return False
    try:
        p = pathlib.Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
        return True
    except Exception as e:                                # noqa: BLE001
        print(f"⚠ 一些事儿落盘失败({str(e)[:60]}); 课堂不受影响")
        return False


def load(path) -> list[dict]:
    """旁路文件回放 → 条目列表（含 `action`）。**fail-soft**：坏行跳过、文件缺失空表。"""
    out: dict = {}
    order: list = []
    if path is None:
        return []
    try:
        text = pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:                                 # noqa: BLE001
            continue
        if not isinstance(rec, dict):
            continue
        rid = str(rec.get("id") or "")
        if not rid:
            continue
        if rec.get("kind") == "item":
            if rid not in out:
                order.append(rid)
                out[rid] = {"id": rid, "action": "pending", "reminders": ""}
            out[rid].update({k: rec[k] for k in
                             ("t", "quote", "zh", "src", "source", "changed", "type",
                              "title", "date", "time", "inferred", "phrase",
                              "undecided") if k in rec})
        elif rid in out:
            if rec.get("kind") == "decision":
                out[rid].update({k: rec[k] for k in
                                 ("action", "title", "date", "time", "type",
                                  "inferred", "at", "decision_source") if k in rec})
            elif rec.get("kind") == "reminders":
                out[rid]["reminders"] = "ok" if rec.get("ok") else "failed"
                out[rid]["reason"] = str(rec.get("reason") or "")
    return [out[i] for i in order]


def save_decision(path, item: dict, *, action: str, at: str = "") -> bool:
    """把一次「记下 / 忽略」写成旁路事件（**先落盘、再写提醒事项** —— 顺序不能反：
    授权弹窗可能挂很久，而用户的决定不能等它）。"""
    rec = {"kind": "decision", "id": str(item.get("id") or ""),
           "action": str(action), "at": at or time.strftime("%H:%M:%S"),
           "title": str(item.get("title") or ""),
           "date": str(item.get("date") or ""),
           "time": str(item.get("time") or ""),
           "type": str(item.get("type") or ""),
           "inferred": bool(item.get("inferred")),
           "decision_source": str(item.get("decision_source") or "")}
    return append(path, rec)


# ---- 提醒事项（osascript）------------------------------------------------------
#: ⭐ **一条静态脚本 + argv**：文案不拼进 AppleScript 源里 → 没有转义面。
#:    日期按**分量**设（`set year of theDate to 2026`），不是 `date "2026-10-20"`
#:    —— 后者按系统区域设置解析，非美式区域会静默算出别的日子。
SCRIPT = """on run argv
  set tTitle to item 1 of argv
  set tBody to item 2 of argv
  set tList to item 3 of argv
  set dYear to (item 4 of argv) as integer
  set dMonth to (item 5 of argv) as integer
  set dDay to (item 6 of argv) as integer
  set dHour to (item 7 of argv) as integer
  set dMin to (item 8 of argv) as integer
  tell application "Reminders"
    if not (exists list tList) then
      make new list with properties {name:tList}
    end if
    set l to list tList
    set theDate to current date
    set year of theDate to dYear
    set month of theDate to dMonth
    set day of theDate to dDay
    set hours of theDate to dHour
    set minutes of theDate to dMin
    set seconds of theDate to 0
    make new reminder at end of l with properties {name:tTitle, body:tBody, due date:theDate}
  end tell
end run"""

#: 只有日期没时间 → 当天 9:00（Apple 官方支持页：默认就是早 9:00 提醒）。
DEFAULT_HOUR = 9


def reminder_argv(item: dict, *, list_name: str = LIST_NAME) -> list[str]:
    """条目 → `osascript` 的 argv。**纯函数**（判据靠它：日期/时间/备注的落法）。

    ⚠️ `date` 为空（没算出来 / 老师没说）→ **用今天**（用户既然点了「记下」，
       就是要有一个能提醒的东西；卡片上那行小字已经写明）。调用方给的是
       `item["date"]`，所以把"今天"落在**调用日**。
    """
    d = str(item.get("date") or "")
    if d:
        try:
            day = datetime.date.fromisoformat(d)
        except ValueError:
            day = datetime.date.today()
    else:
        day = datetime.date.today()
    t = str(item.get("time") or "")
    hh, mm = DEFAULT_HOUR, 0
    if t:
        try:
            h, m = t.split(":")
            hh, mm = int(h), int(m)
        except ValueError:
            hh, mm = DEFAULT_HOUR, 0
    title = str(item.get("title") or "").strip() or "一些事儿"
    parts = [title]
    body = [str(item.get("quote") or "").strip()]
    if item.get("t"):
        body.append(f"课堂时间 {item['t']}")
    if item.get("date") and item.get("inferred"):
        body.append(f"（日期按上课日推算：{item.get('phrase') or ''}）")
    parts.append("\n".join(p for p in body if p))
    parts.append(str(list_name))
    parts += [str(day.year), str(day.month), str(day.day), str(hh), str(mm)]
    return parts


def write_reminder(argv: list[str], *, runner=None, timeout: float = WRITE_TIMEOUT_S
                   ) -> tuple[bool, str]:
    """跑 `osascript` 往提醒事项里写一条。返回 `(ok, 失败原因)`。

    ⚠️ `runner` 是**注入的**（测试传假的，一个字节都不进真「提醒事项」）：
       默认实现 = `subprocess.run(["osascript", "-e", SCRIPT, *argv])`。
       runner 的形状：`runner(argv) -> 有 returncode/stdout/stderr 的对象`，
       抛 `TimeoutExpired` 也表示超时。
    ⚠️ 首次会弹**自动化授权**弹窗：拒绝 = 非零退出 + stderr 里带 AppleScript 的
       `-1743`；不点 = 超时。两条都要给**人话**原因（卡片上要显示）。
    """
    if runner is None:
        def runner(a, _t=timeout):                        # 默认实现（真 osascript）
            return subprocess.run(["osascript", "-e", SCRIPT, *a],
                                  capture_output=True, text=True, timeout=_t)
    try:
        proc = runner(list(argv))
    except subprocess.TimeoutExpired:
        return False, f"提醒事项没在 {timeout:.0f} 秒内写完（授权弹窗没点？）"
    except FileNotFoundError:
        return False, "找不到 osascript"
    except Exception as e:                                # noqa: BLE001
        return False, f"{type(e).__name__}: {str(e)[:80]}"
    rc = int(getattr(proc, "returncode", 1) or 0)
    if rc == 0:
        return True, ""
    err = str(getattr(proc, "stderr", "") or "").strip().replace("\n", " ")
    if "(-1743)" in err or "not allowed" in err.lower():
        return False, "系统没允许 ClassLive 控制提醒事项（系统设置 › 隐私与安全性 › 自动化）"
    return False, (err[:160] or f"osascript 退出码 {rc}")


def backup(item: dict, reason: str, *, path=None, root=None) -> bool:
    """写不进提醒事项时，把条目落到本地备份（`paths.duty_backup()`）。**只失败时写。**"""
    p = pathlib.Path(path) if path is not None else paths.duty_backup(root=root)
    rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "title": str(item.get("title") or ""),
           "date": str(item.get("date") or ""),
           "time": str(item.get("time") or ""),
           "type": str(item.get("type") or ""),
           "quote": str(item.get("quote") or ""),
           "t": str(item.get("t") or ""),
           "reason": str(reason or "")}
    return append(p, rec)


# ---- 板子（状态机）-----------------------------------------------------------
class Board:
    """一节课的「一些事儿」：认到候选 → 用户决定 → 写到哪。

    ⚠️ 线程：`offer()` 从总结 worker 线程调；其余方法都在主线程。
       内部一把锁，事件只追加（旁路文件是唯一真源，内存只是它的投影）。
    """

    def __init__(self, *, day, path=None):
        self._day = day
        self._path = pathlib.Path(path) if path is not None else None
        self._lock = threading.Lock()
        self._items: list[dict] = []
        self._seq = 0
        for it in load(self._path):
            self._items.append(it)
            m = re.match(r"^d(\d+)$", it.get("id") or "")
            if m:
                self._seq = max(self._seq, int(m.group(1)))

    # ---- 认 ----
    def offer(self, payload) -> dict | None:
        """一条 deadline payload → 新条目；不认 / 重复 → `None`。**不抛。**"""
        try:
            cand = candidate(payload, day=self._day)
        except Exception:                                 # noqa: BLE001
            return None
        if cand is None:
            return None
        src = set(cand.get("src") or ())
        with self._lock:
            for it in self._items:
                if src and (set(it.get("src") or ()) & src):
                    # 同一条（正则那条先到、模型那条后到）→ 合并，不重复弹卡
                    it["source"] = "/".join(
                        p for p in dict.fromkeys(
                            [x for x in str(it.get("source", "")).split("/") if x]
                            + [p for p in str(cand.get("source", "")).split("/") if p]))
                    it["changed"] = bool(it.get("changed") or cand.get("changed"))
                    # ⚠️ `changed` 变了，**类型要重算**（"已改期"压过词表 —— 见 classify）
                    it["type"] = classify(it.get("quote") or "", changed=it["changed"])
                    if cand.get("date") and not it.get("date"):
                        it["date"] = cand["date"]
                        it["time"] = cand["time"]
                        it["inferred"] = cand["inferred"]
                        it["phrase"] = cand["phrase"]
                    return None
            self._seq += 1
            it = {"id": f"d{self._seq}", "action": "pending", "reminders": "",
                  **cand}
            self._items.append(it)
            rec = {"kind": "item", **{k: it[k] for k in
                                      ("id", "t", "quote", "zh", "src", "source",
                                       "changed", "type", "title", "date", "time",
                                       "inferred", "phrase", "undecided")}}
        append(self._path, rec)
        return dict(it)

    # ---- 读 ----
    def items(self) -> list[dict]:
        with self._lock:
            return [dict(x) for x in self._items]

    def pending(self) -> list[dict]:
        return [x for x in self.items() if x.get("action") == "pending"]

    def saved(self) -> list[dict]:
        return [x for x in self.items() if x.get("action") == "saved"]

    def get(self, rid: str) -> dict | None:
        with self._lock:
            for x in self._items:
                if x.get("id") == rid:
                    return dict(x)
        return None

    # ---- 写 ----
    def decide(self, rid: str, action: str, *, title=None, date=None, time_=None,
               type_=None, at: str = "", source: str = "") -> dict | None:
        """`记下`（`saved`）/ `忽略`（`ignored`）/ 撤销回 `pending`。**先落盘。**"""
        with self._lock:
            it = next((x for x in self._items if x.get("id") == rid), None)
            if it is None:
                return None
            it["action"] = str(action)
            if title is not None:
                it["title"] = str(title)
            if date is not None:
                it["date"] = str(date)
                it["inferred"] = False          # 用户改过 -> 不再是推算
            if time_ is not None:
                it["time"] = str(time_)
            if type_ is not None:
                it["type"] = str(type_)
            if source:
                it["decision_source"] = str(source)
            snap = dict(it)
        save_decision(self._path, snap, action=str(action), at=at)
        return snap

    def note_reminder(self, rid: str, ok: bool, reason: str = "") -> None:
        """提醒事项写完（或失败）—— 记进旁路文件（卡上那句状态也从这里来回放）。"""
        with self._lock:
            it = next((x for x in self._items if x.get("id") == rid), None)
            if it is None:
                return
            it["reminders"] = "ok" if ok else "failed"
            it["reason"] = str(reason or "")
        append(self._path, {"kind": "reminders", "id": rid, "ok": bool(ok),
                            "reason": str(reason or "")})

    # ---- 备注 / 说明 ----
    def sidecar(self):
        return self._path

    def stats(self) -> dict:
        its = self.items()
        return {"items": len(its),
                "pending": sum(1 for x in its if x.get("action") == "pending"),
                "saved": sum(1 for x in its if x.get("action") == "saved"),
                "ignored": sum(1 for x in its if x.get("action") == "ignored"),
                "inferred": sum(1 for x in its if x.get("inferred"))}


def status_line(item: dict, *, day=None) -> str:
    """卡片上那句状态（**纯函数**）：`已记入提醒事项 · 10-20（周二）` / 失败原因。

    ⚠️ 2026-10-17：句首那个 ⚠ / ✔ 挪出去了（emoji 全换单色 SF Symbols）——
       这里只回纯文字，配哪个符号由 `status_symbol` 说，画在 `overlay`。
    """
    a = str(item.get("action") or "pending")
    if a == "pending":
        return ""
    if a == "ignored":
        return "已忽略（不会写进提醒事项）"
    st = str(item.get("reminders") or "")
    when = fmt_when(item, day=day)
    if st == "failed":
        return f"写不进提醒事项：{item.get('reason') or '未知原因'} · 已备份到 ~/.classlive"
    if st == "ok":
        return f"已记入提醒事项 · {when or '无日期'}"
    return "已记下（提醒事项还没写完…）"


def status_symbol(item: dict) -> str:
    """状态那句话配的 **SF Symbols** 名字（纯函数）：失败 → 警告三角；写好 → 钩圈。

    作者 2026-10-17：emoji 全换单色 SF Symbols（跟文字同一色阶）。
    """
    if str(item.get("action") or "pending") != "saved":
        return ""
    st = str(item.get("reminders") or "")
    if st == "failed":
        return "exclamationmark.triangle"
    if st == "ok":
        return "checkmark.circle"
    return ""


def fmt_when(item: dict, *, day=None) -> str:
    """条目里的日期时间 → 卡片上的 `10-20（周二） 17:00`（带「推算」的由 UI 标）。"""
    d = str(item.get("date") or "")
    if not d:
        return ""
    try:
        dd = datetime.date.fromisoformat(d)
    except ValueError:
        return d
    out = duty_when.fmt_date(dd, anchor=day)
    t = str(item.get("time") or "")
    return f"{out} {t}" if t else out


def chip_text(items: list[dict], *, limit: int = 1) -> str:
    """角落小卡那行字（**纯函数**）：`调课 · 本周三停课 · 10-14  +1`。

    ⚠️ 一屏最多一条（原型 §①）：`+N` 表示还有几条没处理的，点开逐条看。
    ⚠️ 2026-10-17：**前缀那个 📌 挪出去了** —— 现在由 `overlay` 用 SF Symbols 单色小图
       画在文字左边（作者拍板「emoji 全换单色 SF Symbols」）。这里只回纯文字，
       于是这个函数在任何没有 AppKit 的地方（笔记、日志、判据）都能直接读。
    """
    live = [x for x in items if x.get("action") == "pending"]
    if not live:
        return ""
    latest = live[-1]
    head = f"{latest.get('type') or '其他'} · {latest.get('title') or ''}"
    when = fmt_when(latest)
    if when:
        head += f" · {when}"
    extra = len(live) - limit
    return head + (f"  +{extra}" if extra > 0 else "")
