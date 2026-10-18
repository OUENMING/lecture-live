#!/usr/bin/env python3
"""课前回顾 —— **上一节的产物**（知识点 / 重点 / 没解决的 / 临近的一些事儿 / 今天的课件）。

作者 2026-10-17 拍板：开课时把它摆进悬浮字幕窗，教授开口（第一条草稿/定稿）后淡出。

## 两块时间，别混

| 什么时候 | 干什么 | 在哪 |
|---|---|---|
| **上节课收尾** | 生成并**存下**（`build` + `save`）→ `paths.recap_state(课号)` | `obsidian_writer.close()` 那一层（和复习层同一步，fail-soft） |
| **这节课开课** | 直接读（`load` + `card`），不现场调模型 | `main` 开课时 → `streamq` → `overlay.recap_show` |

⚠️ 唯一的例外是 **📄「今天可能讲什么」**：它的输入是**今天**新传的课件，上节课时
   还不存在 → 只能开课时算（`today_extra`）。它走**模板**（文件名 + 开头几行标题），
   **不调模型** —— 「不现场调模型」这条对五块一视同仁。
   作者 2026-10-17 砍掉了它原来那条 ②「上节课结尾提到……」：这一块只留课件。

## 一条纪律：没有就不显示

`card()` 是**纯函数**，它决定哪几行出现。任何一块没内容 → 那一行**整行不出现**
（不写「无」、不占位）—— 首节课 C1（没有上节产物）连卡片都不弹。

## 附加层

`build` 失败（没 key / 网络 / 结构不对）**绝不影响笔记**：`points` 空、
`stars` 退回"没数字的 Jev 重点句"，其余照旧。生成用的 `chat` 是**注入的**
（`obsidian_writer._chat` 那个唯一出口），判据喂桩、一个请求都不发。
"""
from __future__ import annotations

import datetime
import json
import pathlib
import time

import duty_when
import paths

#: 卡片上每块的条数上限（原型 §"为什么 30 秒读得完"：信息多的帮忙，不堆满一屏）。
POINTS_MAX = 6
STARS_MAX = 3
OPEN_MAX = 2
DUTIES_MAX = 3
TODAY_MAX = 2

#: ⭐ 「临近的一些事儿」**只看未来这么多天**（作者 2026-10-17）——
#: 期末考试那种两个月后的条目不进"临近"（它还在卡片 / 提醒事项里，只是不占回顾的行）。
DUTIES_WINDOW_DAYS = 14
#: ⭐ **没日期**的条目最多随课传几次（同上）。没日期判不了远近 —— 不给个上限的话
#: 它会永远留在列表里（`carry` 在 `merge_duties` 里加一，见 `upcoming`）。
DUTIES_CARRY_MAX = 2

#: 一次调用能喂多少字符（章节 + 重点句）。一节课 310 句的课约 8K，这里只喂骨架。
FEED_MAX_CHARS = 6000

_SYS = """你是课堂笔记助手。给一节大学课写「课前回顾」用的两样东西，目标是让读者
在两分钟内想起上节课讲了什么。纪律（缺一不可）：
1. 知识点按**讲解顺序**排（输入就是顺序），每点给**一句大白话解释**：
   只讲概念本身，**不带例子、不带数字、不带公式、不带符号**。
2. 表述要**严谨**：说法必须符合理论，**宁可少说一句，也不要说会被老师纠正的绝对话**
   （「一定 / 只可能 / 必然」这类词，除非数学上真的无例外）。
   （反例：「极值只可能出现在驻点或边界上」—— 漏了**不可导点**（如 |x| 在 0 处）；
     应说「可导函数的极值点导数为零（驻点）」。）
3. 重点（`stars`）写 2–3 条**结论式**的句子：中文、不带数字、不出现"概率/得分"，
   写成一个学生可以背下来的一句话。
4. 名字用中文术语；输入里的英文术语照抄进括号可以，别自己翻译出新词。
只输出 JSON：{"points": [{"name": "术语", "explain": "一句大白话"}], "stars": ["…"]}"""


def _one_line(s, limit: int = 160) -> str:
    return " ".join(str(s or "").split())[:limit]


def _flat(x) -> str:
    return " ".join(str(x or "").split())


def _no_digits(s: str) -> bool:
    """⭐ 「不带数字」这条纪律的判据（`stars` 的兜底档用它）。"""
    return not any(ch.isdigit() for ch in str(s or ""))


def build(*, course: str, date: str, chapters, keypoints, lost, duties, tail: str,
          chat=None, session: str = "", extra: dict | None = None) -> dict:
    """收尾时生成回顾数据。**额外调用 ≤1 次**（`chat`）。

    参数都是"现成产物"：
      · `chapters` —— `chapter_items["chapters"]`（正式章，带 `title_zh` / `t0`）；
      · `keypoints` —— `obsidian_writer._keypoint_items` 的产物 `[(概率, 句子, [行号])]`；
      · `lost` —— ❓ 课后讲解的条目（字符串列表）；
      · `duties` —— 「一些事儿」里保存的 + 未处理的（`duty` 的条目 dict）。
    ⚠️ `chat(system, user, max_tokens) -> dict` —— 失败**抛不抛都行**，这里全兜。
    """
    data = {"course": str(course or ""), "date": str(date or ""),
            "session": str(session or ""),
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "points": [], "stars": [], "open": list(lost or [])[:OPEN_MAX],
            "duties": [d for d in (duties or [])][:DUTIES_MAX],
            "tail": _one_line(tail, 140)}
    # ⭐ 兜底档：**不调模型**也要有东西看 —— Jev 的重点句里挑没数字的（结论式那条
    #    由模型负责；没模型时宁可"照抄但没数字"，也不给一条带数字的）。
    fallback_stars = [_one_line(s, 120) for _p, s, *_ in (keypoints or [])
                      if _no_digits(s)][:STARS_MAX]
    data["stars"] = fallback_stars
    if chat is None:
        return data
    feed = _feed(chapters, keypoints)
    if not feed.strip():
        return data
    try:
        obj = chat(_SYS, feed, 1200)
    except Exception as e:                                # noqa: BLE001
        print(f"⚠ 课前回顾没生成({type(e).__name__}: {str(e)[:60]}); 存骨架、笔记不受影响")
        return data
    if not isinstance(obj, dict):
        return data
    pts = []
    for p in (obj.get("points") or []):
        if not isinstance(p, dict):
            continue
        name = _one_line(p.get("name"), 40)
        ex = _one_line(p.get("explain"), 120)
        if name:
            pts.append({"name": name, "explain": ex})
    stars = [_one_line(s, 120) for s in (obj.get("stars") or [])
             if isinstance(s, str) and _one_line(s)]
    stars = [s for s in stars if _no_digits(s)][:STARS_MAX]
    data["points"] = pts[:POINTS_MAX]
    data["stars"] = stars or fallback_stars
    if extra:
        data.update(extra)
    return data


def _feed(chapters, keypoints) -> str:
    """喂给模型的骨架：章节名 + 每章挑几句中文，再加 Jev 的重点句。"""
    lines: list[str] = ["上节课的章节（按讲解顺序）："]
    n_ch = 0
    for c in (chapters or []):
        if (c.get("status") or "") != "final":
            continue
        name = c.get("title_zh") or c.get("title") or ""
        if not name:
            continue
        lines.append(f"- {c.get('t0') or ''} {name}")
        n_ch += 1
        sents = [s.get("zh") or s.get("en") or "" for s in (c.get("sentences") or [])]
        for s in sents[:4]:
            if s:
                lines.append(f"    · {_one_line(s, 90)}")
    if keypoints:
        lines.append("老师强调过的句子（按重要性排序，别照抄，改写成结论）：")
        for _p, s, *_ in (keypoints or [])[:8]:
            lines.append(f"- {_one_line(s, 120)}")
    # ⚠️ 一条内容都没有 → **空串**（调用方据此不发请求：没东西可总结就别花钱）
    return "" if not n_ch and not keypoints else "\n".join(lines)[:FEED_MAX_CHARS]


def card(data, *, day=None) -> dict:
    """数据 → 界面要的行。**纯函数**（判据靠它）。

    ⭐ 「没有就不显示」这条纪律的**唯一定义点**：空的块就是空列表，由 UI 决定不画。
    ⭐ 2026-10-17（作者）：「临近的一些事儿」在这里**按「今天」过滤**（`upcoming`）——
       存下来的那份可能已经旧了（老 recap / 上一次合并漏掉的），显示这一层再挡一道。
    """
    d = data if isinstance(data, dict) else {}
    points = []
    for p in (d.get("points") or []):
        if not isinstance(p, dict):
            continue
        name = _flat(p.get("name"))
        if name:
            points.append({"name": name, "explain": _flat(p.get("explain"))})
    stars = [s for s in (_flat(x) for x in (d.get("stars") or []))
             if s and _no_digits(s)][:STARS_MAX]
    open_qs = [s for s in (_flat(x) for x in (d.get("open") or [])) if s][:OPEN_MAX]
    duties = []
    for x in upcoming(d.get("duties"), day=day):
        title = _flat(x.get("title"))
        if title:
            duties.append({"type": _flat(x.get("type")), "title": title,
                           "when": _flat(x.get("when"))})
    duties = duties[:DUTIES_MAX]
    today = [s for s in (_flat(x) for x in (d.get("today") or [])) if s][:TODAY_MAX]
    return {"course": _flat(d.get("course")), "date": _flat(d.get("date")),
            "points": points[:POINTS_MAX], "stars": stars, "open": open_qs,
            "duties": duties, "today": today}


# ---- 存 / 读 ------------------------------------------------------------------
def save(course: str, data: dict, *, root=None) -> bool:
    """原子写 `~/.classlive/courses/<课号>/recap.json`。失败静默 `False`（收尾照走）。"""
    if not course:
        return False
    try:
        from build_notes import _atomic_write
        p = paths.recap_state(course, root=root)
        p.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(p, json.dumps(data, ensure_ascii=False, indent=1))
        return True
    except Exception as e:                                # noqa: BLE001
        print(f"⚠ 课前回顾没存下来({str(e)[:60]}); 下节课就没有回顾，其余不受影响")
        return False


def load(course: str, *, root=None) -> dict:
    """读上一节的回顾。**fail-soft**：没文件 / 坏 JSON → 空 dict（= 什么都不弹）。"""
    if not course:
        return {}
    try:
        raw = paths.recap_state(course, root=root).read_text(encoding="utf-8")
        got = json.loads(raw)
        return got if isinstance(got, dict) else {}
    except OSError:
        return {}
    except Exception as e:                                # noqa: BLE001
        print(f"⚠ 课前回顾读不出({str(e)[:60]}); 当没有")
        return {}


def _iso_date(v):
    """`"2026-10-14"` / `date` → `date`；认不出 → `None`（**当"没日期"处理**，不是丢掉）。"""
    if isinstance(v, datetime.datetime):
        return v.date()
    if isinstance(v, datetime.date):
        return v
    s = str(v or "").strip()
    if not s:
        return None
    try:
        return datetime.date.fromisoformat(s)
    except ValueError:
        return None


def upcoming(duties, *, day=None) -> list:
    """「临近的一些事儿」按**今天**过滤 —— 三条规则（作者 2026-10-17 点名）。

    1. ⭐ **有日期的**：`day <= 日期 <= day + DUTIES_WINDOW_DAYS` 之内才留。
       `日期 < day` = **已经过去了**（原来会跟着一节节传下去 —— 拍摄里就出过
       「上节 10-15、这条 10-14」）；超过窗口 = 不"临近"（两个月后的期末之类）。
    2. ⭐ **没日期的**：**留**（没日期判不了远近），但 `carry`（`merge_duties` 里
       每随课传一次加一）到 `DUTIES_CARRY_MAX` 就丢 —— 不然它永远留着。
    3. `day is None`（不知道今天是哪天）→ **日期一条都不过滤**（宁可多留，不要错杀）；
       `carry` 那条照旧（它不依赖今天）。

    ⚠️ 这是**纯函数**（表驱动判据在 `tests/test_recap.py::临近的一些事儿`）：
       只认 `date`（ISO 串）与 `carry` 两个字段，其余原样带过。
    ⚠️ `date` 认不出（老数据可能只有 `when` 那种显示串）→ **当没日期**：留，
       但受 `carry` 上限管 —— 与"根本没日期"同一条路。
    """
    today = _iso_date(day)
    horizon = (today + datetime.timedelta(days=DUTIES_WINDOW_DAYS)) if today else None
    out = []
    for x in (duties or []):
        if not isinstance(x, dict):
            continue
        d = _iso_date(x.get("date"))
        if today is not None and d is not None and (d < today or d > horizon):
            continue
        # ⚠️ DB 审查 D-17：`carry` 上限只约束**没有可用日期**的条目 —— 有日期的由日期
        #    窗口自然限寿。`merge_duties` 每合并一次给所有条目 `carry+1`，旧版把窗口内
        #    的事项（如 10 天后的考试）在第 3 节课丢掉 = 与规则 ① 矛盾，**且无声**。
        #    不知道今天（`today is None`）时日期判不了远近 → 照旧全部受 carry 管（规则 ③）。
        if (d is None or today is None) and int(x.get("carry") or 0) >= DUTIES_CARRY_MAX:
            continue
        out.append(x)
    return out


def merge_duties(prev, new, *, day=None) -> list:
    """上一节存下的「临近的一些事儿」+ 这节课的 → 合并去重（同一件别记两遍）。

    ⚠️ 顺序：**新在前**（越近的越靠前）；同一 (title, when|date) 只留一条。
    ⭐ 2026-10-17（作者）：**按「今天」过滤**（`upcoming`）——
       · 上一节那份里**已经过去**的条目不再往下一节传（从前会节节相传）；
       · 活下来的那些**没日期的**条目 `carry += 1`（"随课传了几次"，到上限就丢）。
    ⚠️ 这节课**新产生的**条目 `carry` 从 0 起（缺字段 = 0）。
    """
    carried = []
    for x in (prev or []):
        carried.append({**x, "carry": int(x.get("carry") or 0) + 1}
                       if isinstance(x, dict) else x)
    out, seen = [], set()
    for x in upcoming(new, day=day) + upcoming(carried, day=day):
        if not isinstance(x, dict):
            continue
        key = (_flat(x.get("title")), _flat(x.get("when")) or _flat(x.get("date")))
        if not key[0] or key in seen:
            continue
        seen.add(key)
        out.append(x)
    return out[:DUTIES_MAX]


def duties_from_items(items, *, day=None, limit: int = DUTIES_MAX) -> list:
    """`duty` 的条目 → 回顾里那一行（保存的 + 没处理的；忽略掉的**不算**）。"""
    out = []
    for it in (items or []):
        if it.get("action") == "ignored":
            continue
        when = ""
        d = str(it.get("date") or "")
        if d:
            try:
                when = duty_when.fmt_date(datetime.date.fromisoformat(d), anchor=day)
            except ValueError:
                when = d
        if it.get("time"):
            when = (when + " " + str(it["time"])).strip()
        out.append({"type": _flat(it.get("type")), "title": _flat(it.get("title")),
                    "when": when, "date": d})
    return out[:limit]


def tail_quote(entries, *, limit: int = 140) -> str:
    """整节课最后一句英文 —— 📄 那一块的第二行「上节课结尾提到…」用它。"""
    for e in reversed(list(entries or ())):
        en = _flat(e.get("en") or e.get("asr") or "")
        if en:
            return _one_line(en, limit)
    return ""


# ---- 今天可能讲什么（开课时，模板、不调模型）------------------------------------
#: 抽出来的标题最多看几个字。
_TITLE_KINDS = ("title", "subtitle")


def today_extra(course: str, data, *, root=None, now=None) -> dict:
    """📄「今天可能讲什么」：**今天新传的课件** → 模板一行。

    作者 2026-10-17 砍掉了原来的 ②「上节课结尾提到……」那一行：块里只留课件这一条，
    没有课件 → `{}`（那一行整块不出现，原型 B 态：不报错、不提示缺东西）。
    ⚠️ 纯模板（`extract` 抽文本 + 文件名），**不调模型** —— 与其余四块同一纪律。
    ⚠️ 它可能慢一点（PDF 抽文本），调用方把它丢在 worker 上、晚 1–2 秒补进来。
    """
    if not course:
        return {}
    try:
        newest = _newest_material(course, data, root=root, now=now)
    except Exception as e:                                # noqa: BLE001
        print(f"⚠ 今天课件没找到({str(e)[:50]})")
        return {}
    if newest is None:
        return {}
    name, path = newest
    lines: list[str] = []
    heads = _slide_heads(path)
    if heads:
        lines.append(f"今天新课件《{name}》：{ ' · '.join(heads) }")
    else:
        lines.append(f"今天新传了课件《{name}》")
    return {"today": lines[:TODAY_MAX]}


def _newest_material(course: str, data, *, root=None, now=None):
    """今天（或上节课之后）新传的那份课件。**没有就返回 None**。

    判据：`mtime` 的本地日期 ≥ 今天 → 今天传的；否则若晚于上节课日期 → 也算新的
    （作者常在头天晚上拖课件）。取 mtime 最新的那一份。
    """
    md = paths.materials_dir(course, root=root)
    if not md.is_dir():
        return None
    today = (now or datetime.datetime.now()).date()
    last = None
    try:
        last = datetime.date.fromisoformat(str((data or {}).get("date") or ""))
    except ValueError:
        last = None
    best = None
    for f in sorted(md.iterdir()):
        if not f.is_file() or f.name.startswith("."):
            continue
        try:
            mt = datetime.datetime.fromtimestamp(f.stat().st_mtime)
        except OSError:
            continue
        if mt.date() >= today or (last is not None and mt.date() > last):
            if best is None or mt > best[0]:
                best = (mt, f)
    if best is None:
        return None
    return best[1].name, best[1]


def _slide_heads(path: pathlib.Path, *, limit: int = 3) -> list:
    """课件开头几行标题（模板那一行用）。**任何失败都当"抽不出"**（静默降级到文件名）。"""
    suf = path.suffix.lower()
    try:
        if suf in (".md", ".txt"):
            import materials
            text = materials.read_text(path)
            rows = [ln.strip().lstrip("# ").strip() for ln in text.splitlines()]
            return [r for r in rows if 2 <= len(r) <= 40][:limit]
        if suf not in (".pdf", ".pptx", ".docx"):
            return []
        import extract
        # ⚠️ `ocr=False` + 只看头 6 页：这是**开课路径**上的一个可选行，
        #    不值得为它跑 Vision OCR（那要几秒）。
        res = extract.extract(path, ocr=False, max_pages=6)
        out = []
        for b in (res.blocks or []):
            t = _flat(b.text)
            if b.kind in _TITLE_KINDS and 2 <= len(t) <= 40:
                out.append(t)
            elif b.kind in ("body", "textbox") and 4 <= len(t) <= 40:
                out.append(t)
            if len(out) >= limit:
                break
        return out[:limit]
    except Exception:                                     # noqa: BLE001
        return []


def merge_extra(data: dict, extra: dict | None) -> dict:
    """`extra`（今天那两行）并进数据 —— 给 UI 的 `recap_update` 用。返回**新** dict。"""
    out = dict(data or {})
    if extra:
        out.update(extra)
    return out
