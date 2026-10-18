#!/usr/bin/env python3
"""功能使用度（测试模式）—— **只记动作与计数，一个字的内容都不记**。

一句话：作者开课连测几节，想知道「这些功能到底有没有人用」，而不是「用户写了什么」。

    调用点（overlay / main）──► note_event / livemetrics.event ──► 事件流
                                                                  │
                                          block() ──► report.json 的 `feature_usage`
                                          block() ──► scripts/class_report.py 的几行字

## 接口（全是纯函数 + 两张表）

| 名字 | 干什么 |
|---|---|
| `clean(name, data)` | **发射前**的白名单清洗：不认识的动作 / 字段直接丢掉 |
| `reason_kind(reason)` | 提醒事项的失败原因 → 封闭词表里的一类（原文不进报告） |
| `block(events)` | 事件流 → `report.json` 里的计数块（**吃掉两种形状**，见 `iter_events`） |
| `lines(usage)` | 计数块 → `class_report.py` 里的那几行字 |

## 三条纪律（作者 2026-10-17 点名）

1. ⭐⭐ **不记任何文本内容**：标题 / 原话 / 日期值 **一个字都不进**。
   `FIELDS` 是每个动作允许携带的字段；值只能是**数字 / 布尔 / 封闭词表里的词** ——
   `type` 只认 `duty.TYPES` 那四类，`src` 只认 regex/model/both，`blocks` 只认课前
   回顾那五块，`why` 只认 denied/timeout/error/other。
   **就算调用点手滑把标题塞进 `data`，也会在这一层被丢掉**（不是靠"记得别传"）。
2. 动作名固定在 `FIELDS` 里。拼错的名字**不会**悄悄多出一个键 —— 整条事件被丢掉、
   `block()` 把它计进 `unknown`（和 `testmode.BLOCKS` 同一条规矩：写错要看得见）。
3. **老文件不报错**：老的 `report.json` / 老的 `.live.jsonl` 里根本没有这些事件 →
   `block()` 回一个**全零的块**，渲染端不用写 if。`lines()` 也永远返回同样的行数。

⚠️ 这个模块**不 import** testmode / overlay / main（它是被它们 import 的那一头）。
   唯一的依赖是 `duty`（要它的 `TYPES` 那张词表）—— 纯 stdlib，无副作用。
"""
from __future__ import annotations

import duty as _duty

#: 事件名的前缀。测试模式那边落进 `note_event` / `livemetrics.event` 的 `kind`
#: 就是 `"feature.duty.card"` 这个样子 —— 前缀让「功能事件」在事件流里一眼可辨。
PREFIX = "feature."

#: ⭐ 动作 → **允许携带的字段**。这里是唯一的定义点：
#: 加了新动作/新字段，先动这张表（`clean()` 与 `block()` 都从它读）。
FIELDS: dict[str, tuple] = {
    # 一些事儿
    "duty.card": ("type", "src"),          # 卡片出现（类型 + 来源）
    "duty.save": (),                       # 记下
    "duty.ignore": (),                     # 忽略
    "duty.next": (),                       # 下一条
    "duty.edit": ("title", "date"),        # 改过标题 / 日期（**只记改没改**，两个布尔）
    "duty.reminder": ("ok", "why"),        # 提醒事项：写成没 + 失败原因（封闭词表）
    "duty.wrapup": ("n", "pending"),       # 收尾汇总出现（共几条 / 几条待确认）
    "duty.wrapup.save": (),                # 汇总里逐条：记下
    "duty.wrapup.ignore": (),              # 汇总里逐条：忽略
    "duty.wrapup.all": ("n",),             # 全部忽略（一次放掉几条）
    "duty.wrapup.timeout": ("left",),      # 20s 没人理（剩几条没确认）
    # 课前回顾
    "recap.shown": ("blocks", "n"),        # 出现（有哪几块）
    "recap.dismiss": (),                   # 手动收起（卡上那颗「收起」）
    "recap.auto_hide": ("s",),             # 教授开口自动淡出（距出现多少秒）
    "recap.toggle": ("on",),               # 顶栏「回顾」按钮（展开/收起次数）
}

#: 全部合法动作名（`block()` 的 `unknown` 计数对着它）。
NAMES = tuple(FIELDS)

#: 来源词表。条目里的 `source` 可能是 `"regex"` / `"model"` / `"regex/model"`
#:（两条候选合并过 —— 见 `overlay._merge_deadline`），归一成三种。
SRC = ("regex", "model", "both")

#: 提醒事项失败原因的**封闭词表**（原文一个字都不进报告，见 `reason_kind`）。
WHY = ("denied", "timeout", "error", "other")

#: 课前回顾的块名（`recap.card()` 那几把键）。
BLOCK_NAMES = ("points", "stars", "open", "duties", "today")

#: 布尔字段（`clean` 会把它们规整成真正的 bool）。
_FLAG = ("title", "date", "ok", "on")
#: 整数字段。
_INT = ("n", "pending", "left")
#: 秒（浮点，一位小数就够 —— 报告里只看"多久之后淡的"）。
_SEC = ("s",)

#: `clean()` 认不出的字段值的兜底长度（字符串字段只有 `why` 一类封闭词，本就进不来）。
_MAX_STR = 24


# ---------------------------------------------------------------- 发射前：清洗
def _src_kind(v) -> str | None:
    """`"regex/model"` → `"both"`；认不出的来源 → `None`（字段丢掉）。"""
    parts = [p.strip() for p in str(v or "").split("/") if p.strip()]
    got = set(parts) & {"regex", "model"}
    if not got:
        return None
    return "both" if len(got) > 1 else got.pop()


def _coerce(key: str, v):
    """一个字段的**取值规整** —— 认不出就回 `None`（= 这个字段丢掉，不是存个空）。"""
    if key == "type":
        return v if v in _duty.TYPES else None
    if key == "src":
        return _src_kind(v)
    if key == "why":
        return v if v in WHY else None
    if key == "blocks":
        if not isinstance(v, (list, tuple)):
            return None
        got = [b for b in v if b in BLOCK_NAMES]
        return got or None
    if key in _FLAG:
        return bool(v)
    if key in _INT:
        try:
            return int(v)
        except (TypeError, ValueError):
            return None
    if key in _SEC:
        try:
            return round(float(v), 1)
        except (TypeError, ValueError):
            return None
    return None                                            # 白名单外的键根本到不了这


def clean(name: str, data=None):
    """`(动作名, 字段) `→ 清洗后的一对；**认不出动作 → `None`**。

    ⚠️ 这是「不记文本」那道闸，不是"尽量记":`data` 里没在白名单上的键、
      值不合词表的键，**一律丢掉**（宁可少一个数，也不让一个字漏进报告）。
    """
    fields = FIELDS.get(str(name or ""))
    if fields is None:
        return None
    out = {}
    src = data if isinstance(data, dict) else {}
    for k in fields:
        if k not in src:
            continue
        v = _coerce(k, src[k])
        if v is None:
            continue
        if isinstance(v, str):
            v = v[:_MAX_STR]
        out[k] = v
    return str(name), out


def reason_kind(reason: str) -> str:
    """提醒事项的失败原因（系统/osascript 那句话）→ **封闭词表**里的一类。

    ⚠️ 原文**不进报告**（它可能带路径、带用户名…而且没必要）—— 报告只要「是哪一类」。
       词表见 `WHY`；认不出来的一律 `other`（不许悄悄丢掉：失败条数还是要数）。
    """
    s = str(reason or "").strip().lower()
    if not s:
        return "other"
    if "超时" in s or "timeout" in s or "秒内写完" in s:
        return "timeout"
    if ("-1743" in s or "not authorized" in s or "not allowed" in s
            or "denied" in s or "没允许" in s or "授权" in s or "自动化" in s):
        return "denied"
    if ("退出码" in s or "error" in s or "exception" in s or "找不到" in s
            or "failed" in s):
        return "error"
    return "other"


# ---------------------------------------------------------------- 事件流 → 计数
def iter_events(events):
    """事件流 → `(动作名, 原始字段)`。**两种形状都吃**：

    - `testmode.note_event(kind, data=…)` → `{"kind": "feature.duty.save", "data": {...}}`
    - `livemetrics.event(kind, **fields)` → `{"kind": "event", "event": "feature.duty.save",
      …字段平铺在顶层}`

    ⚠️ 两种形状是**两个出口各自的落盘格式**（作者 2026-10-17 点名要走这两个出口），
       归一化就放在这一层 —— 读报告的人（和渲染端）不必知道它从哪来。
    """
    for ev in events or ():
        if not isinstance(ev, dict):
            continue
        kind = str(ev.get("kind") or "")
        if kind.startswith(PREFIX):
            yield kind[len(PREFIX):], ev.get("data")
        elif kind == "event" and str(ev.get("event") or "").startswith(PREFIX):
            name = str(ev["event"])[len(PREFIX):]
            yield name, {k: ev[k] for k in FIELDS.get(name, ()) if k in ev}


def _empty() -> dict:
    """全零的块 —— 老文件 / 没开测试模式时就是它（不是 `None`，渲染端不用 if）。"""
    return {
        "n_events": 0,
        "unknown": 0,
        "duty": {
            "card": 0, "save": 0, "ignore": 0, "next": 0,
            "edit": 0, "edit_title": 0, "edit_date": 0,
            "reminder": 0, "reminder_ok": 0, "reminder_fail": 0,
            "wrapup": 0, "wrapup_save": 0, "wrapup_ignore": 0,
            "wrapup_all": 0, "wrapup_timeout": 0,
            "type": {}, "src": {}, "reminder_why": {}, "wrapup_left": [],
        },
        "recap": {
            "shown": 0, "dismiss": 0, "auto_hide": 0, "toggle": 0, "toggle_open": 0,
            "blocks": {}, "auto_hide_s": [],
        },
    }


def block(events) -> dict:
    """事件流 → `report.json` 的 `feature_usage` 计数块（纯函数）。

    ⚠️ 每条事件先过 `clean()`：**清洗在这里也做一遍**（不是只信发射端）——
       报告是给人看的账本，写进去什么由这一层说了算。
    """
    out = _empty()
    duty, recap = out["duty"], out["recap"]
    hist = {"duty.save": "save", "duty.ignore": "ignore", "duty.next": "next",
            "duty.wrapup.save": "wrapup_save", "duty.wrapup.ignore": "wrapup_ignore",
            "duty.card": "card", "duty.wrapup": "wrapup",
            "duty.wrapup.timeout": "wrapup_timeout", "recap.shown": "shown",
            "recap.dismiss": "dismiss", "recap.auto_hide": "auto_hide",
            "recap.toggle": "toggle"}
    for name, raw in iter_events(events):
        item = clean(name, raw)
        if item is None:
            out["unknown"] += 1
            continue
        name, d = item
        out["n_events"] += 1
        if name in hist:
            key = hist[name]
            (recap if name.startswith("recap.") else duty)[key] += 1
        if name == "duty.card":
            if d.get("type"):
                duty["type"][d["type"]] = duty["type"].get(d["type"], 0) + 1
            if d.get("src"):
                duty["src"][d["src"]] = duty["src"].get(d["src"], 0) + 1
        elif name == "duty.edit":
            duty["edit"] += 1
            duty["edit_title"] += 1 if d.get("title") else 0
            duty["edit_date"] += 1 if d.get("date") else 0
        elif name == "duty.reminder":
            duty["reminder"] += 1
            if d.get("ok"):
                duty["reminder_ok"] += 1
            else:
                duty["reminder_fail"] += 1
                why = str(d.get("why") or "other")
                duty["reminder_why"][why] = duty["reminder_why"].get(why, 0) + 1
        elif name == "duty.wrapup.all":
            duty["wrapup_all"] += 1
        elif name == "duty.wrapup.timeout":
            duty["wrapup_left"].append(int(d.get("left") or 0))
        elif name == "recap.shown":
            for b in d.get("blocks") or ():
                recap["blocks"][b] = recap["blocks"].get(b, 0) + 1
        elif name == "recap.auto_hide":
            if d.get("s") is not None:
                recap["auto_hide_s"].append(float(d["s"]))
        elif name == "recap.toggle":
            if d.get("on"):
                recap["toggle_open"] += 1
    return out


# ---------------------------------------------------------------- 计数 → 几行字
def _all_num(d: dict) -> bool:
    """这层字典是不是**计数表**（值全是数字）—— `merge` 靠它区分两种字典。"""
    return bool(d) and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                           for v in d.values())


def _add(dst: dict, src: dict) -> None:
    """把一份块**加进** `dst`。两种字典在这里是**不同东西**：

    - 分组（`duty` / `recap`）：键是**结构** —— 认不出的丢掉（同 `clean` 的规矩）；
    - 计数表（`type` / `src` / `reminder_why` / `blocks`）：键是**数据**（封闭词表里的
      词），要按键相加 —— 空表也得能长出「考试 / open」这些新键。
    """
    for k, v in dict(src or {}).items():
        if k not in dst:
            continue                                          # 结构上没有这个位置 → 丢掉
        cur = dst[k]
        if isinstance(cur, dict):
            if not isinstance(v, dict):
                continue
            if _all_num(v):
                for k2, v2 in v.items():                      # 计数表：按键相加
                    cur[k2] = cur.get(k2, 0) + v2
            else:
                _add(cur, v)                                  # 分组：往里递归
        elif isinstance(cur, list):
            if isinstance(v, (list, tuple)):
                cur.extend(v)
        elif isinstance(cur, int) and not isinstance(cur, bool):
            if isinstance(v, int) and not isinstance(v, bool):
                dst[k] = cur + v


def merge(blocks) -> dict:
    """几份计数块 → 一份（`class_report.py` 给多节课那份用的是这个）。

    ⚠️ 与 `pool_sweeps` 同一条规矩：**先把计数加起来**，比率/均值留给渲染那一步 ——
       `auto_hide_s` 那份原始秒数也一路留着（对均值再平均是错的）。
    """
    out = _empty()
    for b in blocks or ():
        if isinstance(b, dict):
            _add(out, b)
    return out


def _pairs(counter) -> str:
    """`{"调课": 2, "考试": 1}` → `"调课 2 / 考试 1"`；空 → `"—"`。"""
    return " / ".join(f"{k} {v}" for k, v in dict(counter or {}).items()) or "—"


def lines(usage) -> list[str]:
    """计数块 → `class_report.py` 里的几行（**永远同样几行**，全零也照打）。"""
    u = usage or {}
    d = u.get("duty") or {}
    r = u.get("recap") or {}
    secs = list(r.get("auto_hide_s") or [])
    mean = f"{sum(secs) / len(secs):.1f}s" if secs else "—"
    lefts = list(d.get("wrapup_left") or [])
    out = [
        f"  一些事儿：卡片 {d.get('card', 0)} 次（类型 {_pairs(d.get('type'))}；"
        f"来源 {_pairs(d.get('src'))}）",
        f"            记下 {d.get('save', 0)} · 忽略 {d.get('ignore', 0)} · "
        f"下一条 {d.get('next', 0)} · 改过字段 {d.get('edit', 0)}"
        f"（标题 {d.get('edit_title', 0)} / 日期 {d.get('edit_date', 0)}）",
        f"            提醒事项 成功 {d.get('reminder_ok', 0)} / 失败 {d.get('reminder_fail', 0)}"
        f"（{_pairs(d.get('reminder_why'))}）",
        f"            收尾 {d.get('wrapup', 0)} 次（行内 记下 {d.get('wrapup_save', 0)}"
        f" / 忽略 {d.get('wrapup_ignore', 0)} · 全部忽略 {d.get('wrapup_all', 0)}"
        f" · 超时 {d.get('wrapup_timeout', 0)}"
        + (f"（剩 {'/'.join(str(x) for x in lefts)}）" if lefts else "") + "）",
        f"  课前回顾：出现 {r.get('shown', 0)} 次（块 {_pairs(r.get('blocks'))}）"
        f" · 手动收起 {r.get('dismiss', 0)} · 自动淡出 {r.get('auto_hide', 0)}"
        f"（均 {mean}） · 回顾按钮 {r.get('toggle', 0)} 次（展开 {r.get('toggle_open', 0)}）",
    ]
    if u.get("unknown"):
        out.append(f"  ⚠️ 认不出的动作 {u['unknown']} 条 —— 名字拼错了？"
                   f"（合法名见 feature_usage.NAMES）")
    return out
