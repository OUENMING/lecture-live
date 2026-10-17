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


def hms(s) -> int | None:
    """`"13:16:28"` → 秒；解不出返回 `None`（**不许猜**：坏时间戳直接不参与）。"""
    parts = str(s or "").strip().split(":")
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


def report(sk: list, sections, covered) -> dict:
    """生成后核对：**每章至少有一个要点落到某一节**（缺了在 §9 标 ⚠，不静默）。

    `covered`：对齐步骤给的 `[{"i": <gi>, "section": "标题"}]`（`i` 是 `flat_points` 的编号）。
    ⚠️ 判定分两层，都写进行里：① 这一章有没有节（按节 `ts` 落窗）；② 它的要点有没有落到节。
    """
    heads = []
    for s in (sections or []):
        if isinstance(s, dict) and (s.get("heading") or "").strip():
            heads.append((_one(s.get("heading")), hms(s.get("ts"))))
    cov: dict = {}
    for c in (covered or []):
        if isinstance(c, dict) and isinstance(c.get("i"), int):
            cov[int(c["i"])] = _one(c.get("section"))
    rows: list = []
    n_points = n_covered = 0
    # ⚠️ 编号必须用**全局**那一份（`flat_points(sk)` 一次算好）：
    #    曾经在循环里 `flat_points([ch])` 逐章重算 → 每章的 gi 都从 1 开始 →
    #    对齐结果全对不上（第四轮自查抓到：章 2 有节、要点却报 0/1）。
    flat_all = flat_points(sk)
    for ch in (sk or []):
        t0, t1 = hms(ch.get("t0")), hms(ch.get("t1"))
        t1 = t1 if t1 is not None else t0
        secs = [h for h, ts in heads if ts is not None and t0 is not None and t0 <= ts <= t1]
        pts: list = []
        for p in [x for x in flat_all if x.get("chapter") == ch.get("i")]:
            gi = p["gi"]
            sec = cov.get(gi, "")
            ok = bool(sec) and sec not in ("缺失", "missing", "-", "无")
            pts.append({"gi": gi, "ts": p["ts"], "point": p["point"], "section": sec if ok else "",
                        "ok": ok})
        n_points += len(pts)
        n_covered += sum(1 for p in pts if p["ok"])
        if pts:
            ok = any(p["ok"] for p in pts)
            why = "" if ok else "这一章的要点一条都没落到节里"
        else:
            ok = bool(secs)
            why = "" if ok else "这一章没有节（实时要点里也没记下它）"
        rows.append({"i": ch.get("i"), "title": ch.get("title"), "t0": ch.get("t0"),
                     "t1": ch.get("t1"), "sections": secs, "points": pts,
                     "ok": ok, "why": why})
    missing = [{"chapter": r["i"], "chapter_title": r["title"], "ts": p["ts"], "point": p["point"]}
               for r in rows for p in r["points"] if not p["ok"]]
    return {"rows": rows, "n_chapters": len(rows),
            "n_ok": sum(1 for r in rows if r["ok"]), "n_points": n_points,
            "n_covered": n_covered, "missing": missing}
