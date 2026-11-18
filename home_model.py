"""首页（开课面板）改版的数据层 —— **纯函数**：课表 / 上次上过 / 今天全从入参来。

出 `HomeModel`：一条「现在」建议（**只在强证据时**）+ 一组**顺序固定**的课程格子 +
`first_run`。本模块不 import AppKit / objc、不读盘、不写任何状态 —— 判据可以单测。
⚠️「最可能那门」的口径**只有一条**，在 `entry_panel.likely_course`（本模块里**延迟**
   import，免得把 AppKit 拖进来）；这里只是给它多报的 `tier` / `start` 一个用户。
"""
from __future__ import annotations

import dataclasses
import datetime

#: 「下一次上课」最多往后找多少天。双周课（`INTERVAL=2`）留足余量，停课日也够跳过。
NEXT_DAYS = 63

_WEEK = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


@dataclasses.dataclass(frozen=True)
class Suggest:
    """顶部那一条「现在 · 课号 · 14:00 · 还有 6 分钟」。**只在 `tier ∈ {now, soon}` 时有。**"""
    code: str
    tier: str      # "now" / "soon"（`last` 不出顶部条 —— 弱证据，见 `Likely.tier`）
    clock: str     # 那节课的**开始**钟点 `"14:00"`；`start` 未知时 `"—"`
    rel: str       # `"还有 6 分钟"` / `"已开始 12 分钟"` / `"正在上"`


@dataclasses.dataclass(frozen=True)
class Tile:
    """一个课程格子。**顺序 = 入参 `rows` 的顺序** —— 永不因为猜测而重排。"""
    code: str
    title: str
    key: int | None        # 数字键：前 9 个是 1..9，之后 None（面板只画到 9）
    meta: str | None       # `"14 份课件 · 92 术语"`；两者都 0 → None
    next_slot: str | None  # `"今天 14:00"` / `"明天 9:00"` / `"周四 10:00"`；没课表 → None
    last_text: str | None  # `"今天"` / `"昨天"` / `"6 天前"` / `"上周"` / `"3 周前"` / `"9月25日"`
    mark: str | None       # "now" / "soon" / "last" / None —— 只有**那一格**亮


@dataclasses.dataclass(frozen=True)
class HomeModel:
    suggest: Suggest | None
    tiles: list
    first_run: bool


# ------------------------------------------------------------------ 主入口
def build(rows, entries, when, *, last_used=None, today=None) -> HomeModel:
    """首页要显示的一切。**纯函数** —— 一次入参一个结果，不读盘、不写状态。

    `rows`     —— `courses.Readiness` 列表，**顺序原样保留**（`tiles` 同序，key 按位置）。
    `entries`  —— 课表 `[(课号, [timetable.Slot, …]), …]`（`timetable.load()` 的直接产物）。
    `when`     —— 此刻（本地）。`today` 不给就取 `when.date()`。
    `last_used`—— 最近一次真上过课的那门（`entry_panel.last_course_of`）。

    三档证据（口径在 `entry_panel.likely_course`，这里**不重写**）：
      · **强证据** `now` / `soon` → 出 `Suggest`，那一格 `mark` 同档；
      · **弱证据** `last` → **不出** `Suggest`（顶部条要「现在就该上」才成立），
        只在那格 `mark="last"`；
      · **无证据** → 都不出。

    ⭐ **两个时间都拿得到时**（`when` 与 `Likely.start` 都有）：`Suggest.clock` 用
       **课表那节的开始时间**（`Likely.start`，即排课钟点），**不是** `when` 那一刻；
       与 `when` 的差（还有 N 分钟 / 已开始 N 分钟）只进 `Suggest.rel`。
       理由：顶部条给人看的是「这节是几点开始的课」，不是「我算这一下的钟点」。
    """
    rows = list(rows or ())
    day = today if today is not None else when.date()
    lk = _likely(entries, when, last_used)

    strong = lk if lk is not None and lk.tier in ("now", "soon") else None
    suggest = _suggest(strong, when) if strong is not None else None

    slots_of: dict = {}
    for code, slots in entries or ():
        slots_of.setdefault(str(code), []).extend(list(slots or ()))

    tiles = []
    for i, r in enumerate(rows):
        code = str(getattr(r, "course", "") or "")
        tiles.append(Tile(
            code=code,
            title=getattr(r, "title", None) or code,
            key=i + 1 if i < 9 else None,
            meta=_meta(r),
            next_slot=_next_text(slots_of.get(code) or (), when),
            last_text=_last_text(getattr(r, "last_session", None), day),
            mark=(lk.tier or None) if (lk is not None and code == lk.course) else None,
        ))
    return HomeModel(suggest=suggest, tiles=tiles, first_run=_first_run(rows))


# ------------------------------------------------------------------ 内部
def _likely(entries, when, last_used):
    """`entry_panel.likely_course` 的**延迟** import。

    ⚠️ 为什么不在文件头 import：`entry_panel` 一加载就把 AppKit 拖进来，
       而本模块的判据要能在「没有 AppKit 的环境 / 只测数据层」里跑。
    ⚠️ 为什么**不**在这里另写一份「正开着 / 马上要开」的判据：口径只许有一条
       （`entry_panel.likely_course` 的 docstring ①–④），写第二份就是会分叉的那种错。
    """
    import entry_panel

    try:
        return entry_panel.likely_course(entries, when, last_used=last_used)
    except Exception:                                  # noqa: BLE001
        # 猜不中是小事（首页退回「无证据」），把面板整个弄挂是大事。
        return None


def _suggest(lk, when) -> Suggest:
    """`Likely` → 顶部条。`clock` 用**排课钟点**，`rel` 用它与 `when` 的差。"""
    start = lk.start
    clock = f"{start.hour:02d}:{start.minute:02d}" if start is not None else "—"
    if lk.tier == "soon":
        if start is None:
            rel = "马上要上"
        else:
            rel = f"还有 {max(0, _mins(start - when))} 分钟"
    elif start is None:
        rel = "正在上"
    else:
        n = _mins(when - start)
        rel = "正在上" if n <= 0 else f"已开始 {n} 分钟"
    return Suggest(code=lk.course, tier=lk.tier, clock=clock, rel=rel)


def _mins(delta: datetime.timedelta) -> int:
    """时长 → 整分钟（**向下取整**：还没满 1 分钟就不算 1 分钟）。"""
    return int(delta.total_seconds() // 60)


def _num(v, unit: str) -> str:
    """计数 + 单位；`None`（读不出）画 `—`，**不是 0** —— `courses.py` 模块头第 3 条。

    ⚠️ **与 `entry_panel._num` 逐字同口径**（本模块不 import 它，是因为那个 import
       会拖进 AppKit）。`tests/test_home_model.py` 拿两边比对钉着，改一处必须改两处。
    """
    return f"{v} {unit}" if v is not None else f"— {unit}"


def _meta(r) -> str | None:
    """格子第二行。**两个数都是 0 → 不显示**（别跟用户说「0 份课件 · 0 条术语」）。

    ⚠️ 「未知」（`None`）与「零」的区别照旧：未知画 `—`，零照实画 `0`。
    """
    m = getattr(r, "materials", None)
    t = getattr(r, "terms", None)
    if m == 0 and t == 0:
        return None
    return f"{_num(m, '份课件')} · {_num(t, '条术语')}"


def _next_start(slots, when) -> datetime.datetime | None:
    """这门课 `when` 之后**下一次**上课的时刻；没有 → `None`。

    ⚠️ 双周课 / 停课日走 `timetable.Slot.on_week()` —— 那条规则的**唯一定义点**在
       `timetable.Slot`，这里**不另写**一套（写第二份就会分叉）。
    ⚠️ **正在上的那节不算「下一次」**（它已经开始过了）：顶部条/`mark` 说的是那一节。
    """
    if not slots:
        return None
    day0 = when.date()
    for delta in range(NEXT_DAYS + 1):
        d = day0 + datetime.timedelta(days=delta)
        for s in slots:
            if s.weekday != d.weekday() or not s.on_week(d):
                continue
            start = datetime.datetime.combine(d, datetime.time(s.hh, s.mm))
            if start < when:
                continue
            return start                      # 按天递增，第一个命中就是最近的
    return None


def _next_text(slots, when) -> str | None:
    """`"今天 14:00"` / `"明天 9:00"` / `"周四 10:00"`；更远的用 `"10月27日 14:00"`。

    ⚠️ 小时**不补零**（`9:00` 而不是 `09:00`）—— 格子里这不是时刻表，是给人扫一眼的。
    """
    start = _next_start(slots, when)
    if start is None:
        return None
    n = (start.date() - when.date()).days
    if n == 0:
        head = "今天"
    elif n == 1:
        head = "明天"
    elif n < 7:
        head = _WEEK[start.weekday()]
    else:
        head = f"{start.month}月{start.day}日"
    return f"{head} {start.hour}:{start.minute:02d}"


def _last_text(d, today) -> str | None:
    """上次上课 → 人话。`0/1/2–6/7–13/14–55/56+` 天分别是
    `今天` / `昨天` / `N 天前` / `上周` / `N 周前` / `M月D日`。

    ⚠️ 形状不对原样返回（同 `entry_panel._short_date` 的容错法），不抛。
    ⚠️ 未来的日期（时钟歪了）也按 `M月D日`，**不**报「-3 天前」。
    """
    if not d:
        return None
    try:
        y, m, dd = str(d).split("-")
        last = datetime.date(int(y), int(m), int(dd))
    except (ValueError, AttributeError):
        return str(d)
    n = (today - last).days
    if n < 0:
        return f"{last.month}月{last.day}日"
    if n == 0:
        return "今天"
    if n == 1:
        return "昨天"
    if n < 7:
        return f"{n} 天前"
    if n < 14:
        return "上周"
    if n < 56:
        return f"{n // 7} 周前"
    return f"{last.month}月{last.day}日"


def _first_run(rows) -> bool:
    """全新用户：**一门课的课件、术语、上课记录都没有**（`None` = 读不出，不算「有」）。

    ⚠️ `rows` 为空也算（面板上什么都没有 = 得给引导，不是给一片空白）。
    """
    for r in rows:
        if getattr(r, "materials", 0) or getattr(r, "terms", 0):
            return False
        if getattr(r, "last_session", None):
            return False
    return True
