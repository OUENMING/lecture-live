"""确定性覆盖度：**章节（chapter.py）+ 实时要点（atom.py）= 知识点清单的唯一口径**。

⚠️ 作者 2026-10-17 第四轮：知识点清单不能每次让模型自由决定粒度 ——
   同一节课，A–C 报「教授知识点 33 个」，E 报 20 个，13:16–13:23 的
   「停产价格 / Exercise 1 三次函数」就掉出了知识点地图（硬要求是「教授讲到的必须讲」）。

口径（全部来自**已经写在盘上的数据**，与模型无关）：
- 骨架 = 每个 `status == "final"` 的章（`t0`–`t1` + 标题）；
- 每章的要点 = 该章时间窗内的原子要点（`kind == "课务"` 不算知识点，它走「课堂纲要」的课务段）；
- 生成前：把骨架**按转录分块**注入 prompt（每一段都看到自己该覆盖的章与要点，一条不许丢）；
- 生成后：`report()` 核「每一章至少有一个要点落到某一节」，缺的在 §9 标 ⚠（不静默）。

⚠️ 时间是 `HH:MM:SS` 字符串，比较**一律换成秒**（字符串比较会把 `13:5:00` 排到 `13:40:00` 后面）。
"""
from __future__ import annotations

import re

#: 一节的**覆盖半径**（秒）：它自己的起点往后算多久。
#: ⚠️ 为什么是 900：开发集 4 节里**实测**节与节的起点间距最长 5m18s、中位 1.4–2.5 分钟
#:    （`scratchpad/notes2/probe_span.py`）。取 15 分钟 = 近三倍余量 → 正常连着的节
#:    之间不会再出现"缝"（假 ⚠），而真的空出 15 分钟以上（这一段时间没有节）照样报。
#: ⚠️ 它只是**上限**，不是"一节写了 15 分钟的内容"：判定的粒度就是"这段时间有没有节"。
SECTION_SPAN = 900


def hms(s) -> int | None:
    """`"13:16:28"` / `"13:16"` → 秒；解不出返回 `None`（**不许猜**：坏时间戳直接不参与）。

    ⚠️ 两种形状都要认（2026-10-17 实测）：小节标题里的 `ts` 是模型写的，
       一半是 `HH:MM:SS`、一半是 `HH:MM`（`🎙 13:11`）。只认三段的话，`13:11`
       会被判成"没有时间戳"→ 那一节直接从覆盖度里消失（假 ⚠ 的来源之一）。
    """
    parts = str(s or "").strip().split(":")
    if len(parts) == 2:
        # ⚠️ 只认正经的 `HH:MM`（两位分钟）—— `13:5` 这种残形状照旧**不猜**
        #    （`tests/test_note_cover.py::test_坏时间戳不猜` 钉着这条）。
        if len(parts[1]) != 2:
            return None
        parts += ["0"]                      # `HH:MM` → 秒补 0（整分钟的粒度）
    if len(parts) != 3:
        return None
    try:
        h, m, sec = (int(p) for p in parts)
    except ValueError:
        return None
    if not (0 <= h < 24 and 0 <= m < 60 and 0 <= sec < 60):
        return None
    return h * 3600 + m * 60 + sec


def _one(s) -> str:
    return " ".join(str(s or "").split())


def _terms(x) -> list:
    out = []
    for t in (x or []):
        if isinstance(t, (list, tuple)) and len(t) >= 2:
            en, zh = _one(t[0]), _one(t[1])
            if en or zh:
                out.append([en, zh])
    return out


def skeleton(chapters, atoms) -> list:
    """章节 + 原子要点 → 确定性骨架 `[{i,title,t0,t1,points:[…]}]`。

    ⚠️ 只认 `status == "final"` 的章（临时章是同一 id 的早期快照，`chapter.load` 里
       后写覆盖先写，这里再挡一道：调用方可能直接喂原始行）。
    ⚠️ 章内要点：`t0 <= t <= t1` 且 `kind != "课务"`；同一句去重（实时要点会重复写）。
    """
    chs = []
    for c in (chapters or []):
        if not isinstance(c, dict) or c.get("status") != "final":
            continue
        t0, t1 = hms(c.get("t0")), hms(c.get("t1"))
        if t0 is None:
            continue
        chs.append((t0, t1 if t1 is not None else t0, c))
    chs.sort(key=lambda x: x[0])
    out: list = []
    for i, (t0, t1, c) in enumerate(chs, 1):
        pts: list = []
        seen: set = set()
        for a in (atoms or []):
            if not isinstance(a, dict):
                continue
            if _one(a.get("kind")) == "课务":
                continue
            t = hms(a.get("t"))
            if t is None or t < t0 or t > t1:
                continue
            text = _one(a.get("zh")) or _one(a.get("text"))
            if not text:
                continue
            key = text.strip().lower()
            if key in seen:
                continue
            seen.add(key)
            pts.append({"ts": _one(a.get("t")), "zh": text, "en": _one(a.get("text")),
                        "terms": _terms(a.get("terms")), "kind": _one(a.get("kind"))})
        out.append({"i": i,
                    "title": _one(c.get("title_zh")) or _one(c.get("title")) or f"第 {i} 章",
                    "t0": _one(c.get("t0")), "t1": _one(c.get("t1")),
                    "points": pts})
    return out


def flat_points(sk: list) -> list:
    """骨架 → 扁平要点清单（每条带全局编号 `gi`，与生成后的核对共用同一个编号）。"""
    out: list = []
    for ch in (sk or []):
        for p in (ch.get("points") or []):
            out.append({"gi": len(out) + 1, "chapter": ch.get("i"), "chapter_title": ch.get("title"),
                        "ts": p.get("ts"), "point": p.get("zh"), "terms": p.get("terms") or []})
    return out


def lines_range(lines) -> tuple:
    """转录分块的 `[HH:MM:SS]` 范围 → `(lo, hi)` 秒；解不出返回 `(None, None)`。"""
    got = []
    for ln in (lines or []):
        m = re.match(r"\[(\d{2}:\d{2}:\d{2})\]", str(ln or ""))
        if m:
            t = hms(m.group(1))
            if t is not None:
                got.append(t)
    return (min(got), max(got)) if got else (None, None)


def block_for(sk: list, lines) -> str:
    """给一个转录分块的**骨架提示**：只列与该块时间相交的章 + 它们的要点。

    ⚠️ 这是「13:16–13:23 不再掉出去」的那道闸：分块 prompt 里**明文列出**
       这一段的章与要点，模型没有"合并粒度"的自由，只有"讲出来"的义务。
    """
    lo, hi = lines_range(lines)
    if lo is None or not sk:
        return ""
    body: list = []
    for ch in sk:
        t0, t1 = hms(ch.get("t0")), hms(ch.get("t1"))
        if t0 is None:
            continue
        t1 = t1 if t1 is not None else t0
        if t1 < lo or t0 > hi:
            continue                      # 与本块不相交
        cover = "全部" if (t0 >= lo and t1 <= hi) else f"与本章相接的部分"
        body.append(f"- 章 {ch['i']}「{ch['title']}」{ch['t0']}–{ch['t1']}（本段覆盖：{cover}）")
        pts = ch.get("points") or []
        here = [p for p in pts if (nc_t := hms(p.get("ts"))) is not None and lo <= nc_t <= hi]
        # ⚠️ 只列**本段时间内**的要点（第四轮 run F：把整章的要点都塞给每一块 →
        #    17/20 条那种块输出超上限 → JSON 截断 → 整块静默消失）。
        #    全课程的要点由各块**并集**覆盖，一条都不会漏。
        if here:
            body.append("  本段的要点（逐条都要在某一节里讲到）：")
            for p in here:
                body.append(f"  · {p['ts']} {p['zh']}")
        rests = len(pts) - len(here)
        if rests > 0:
            body.append(f"  （本章还有 {rests} 条要点落在别的段里，那一段会讲；"
                        f"这一章仍要有至少一节）")
        if not pts:
            body.append("  要点：实时要点里没有记下这一章（用本段转录把这一章讲全）")
    if not body:
        return ""
    return ("\n【本课章节骨架（来自课堂实时分章 + 实时要点，**确定不变**）】\n"
            + "\n".join(body)
            + "\n要求：① 上面每个章都要写**至少一节** `sections`（节标题用章的标题起头，别改粒度）；"
              "② 本段列的每一条要点都要被某一节讲到（一节讲 2–4 条相邻的就行，"
              "**别为每条要点单开一节**）；③ 不要写本段之外的章。\n")


def report(sk: list, sections) -> dict:
    """生成后核对：**按时间窗直接算**（不看模型的 `align`，作者第六轮第 2 条）。

    口径（**确定性**，只吃两样东西：骨架的时间戳、小节的起点时间戳）：
      · 一节的覆盖半径 = 它自己的起点往后 `SECTION_SPAN` 秒（实测节间距最长 5.3 分钟，
        取 15 分钟 = 近三倍余量 —— 连着的节之间不会再出现缝，而"这段时间真的没节"
        （15 分钟以上空档）照样报得出来）；
      · 要点 `ok` ⟺ 有时间戳、且有某一节落在它的覆盖半径内；
      · 章：有要点看要点（有一条 ok 就算这章讲到了）；没有要点看有没有节落在
        `[t0, t1]`（老口径，实时要点可能一条都没记下）。

    ⚠️ 为什么不再信 `align`：那份对齐是**一次调用返回的整份清单**，要点上百条时
       输出被 `max_tokens` 截断 → `{}` → "0/117 条落到节里、缺 117 条"（三节课全 0）。
       模型可以补充看法（`align` 的 `not_covered` 只当提示），**数字必须自己算**。
    ⚠️ 判定的粒度就是"这段时间有没有节"。它**不能**证明某一节真的写了那一条要点
       （那是读者自己的事，也是模型那份提示的用处）—— 但它是可复算的，不会说谎。
    """
    secs = []
    for s in (sections or []):
        if not isinstance(s, dict):
            continue
        t = hms(s.get("ts"))
        if t is not None:
            secs.append((t, _one(s.get("heading"))))
    secs.sort(key=lambda x: x[0])

    def near(t) -> tuple:
        """这条要点落在哪一节里 → `(节标题, 这一节的起点秒)`；不在任何一节里 → `("", None)`。"""
        if t is None:
            return "", None
        best = None
        for ts, head in secs:
            if ts <= t < ts + SECTION_SPAN:
                best = (head, ts)
        return best or ("", None)

    rows: list = []
    n_points = n_covered = 0
    for ch in (sk or []):
        t0, t1 = hms(ch.get("t0")), hms(ch.get("t1"))
        t1 = t1 if t1 is not None else t0
        pts: list = []
        for p in (ch.get("points") or []):
            head, ts = near(hms(p.get("ts")))
            n_points += 1
            n_covered += 1 if head else 0
            pts.append({"gi": p.get("gi"), "ts": p.get("ts"), "point": p.get("zh"),
                        "section": head, "ok": bool(head)})
        secs_here = [h for t, h in secs if t0 is not None and t0 <= t <= t1]
        if pts:
            ok = any(p["ok"] for p in pts)
            why = "" if ok else "这一章的要点一条都没落到节里（这段时间没有节）"
        else:
            ok = bool(secs_here)
            why = "" if ok else "这一章没有节（实时要点里也没记下它）"
        rows.append({"i": ch.get("i"), "title": ch.get("title"), "t0": ch.get("t0"),
                     "t1": ch.get("t1"), "sections": secs_here, "points": pts,
                     "ok": ok, "why": why})
    missing = [{"chapter": r["i"], "chapter_title": r["title"], "ts": p["ts"], "point": p["point"]}
               for r in rows for p in r["points"] if not p["ok"]]
    return {"rows": rows, "n_chapters": len(rows),
            "n_ok": sum(1 for r in rows if r["ok"]), "n_points": n_points,
            "n_covered": n_covered, "missing": missing, "span": SECTION_SPAN}
