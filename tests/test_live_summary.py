#!/usr/bin/env python3
"""实时总结（`live_summary.py` + `chapter.py`）的判据 —— **全离线**，不联网、不起 AppKit。

    ClassLive.app/Contents/MacOS/python tests/test_live_summary.py

## 为什么这个文件必须存在

`live_summary.py` 是从 `main.py` 里那块**内联的原子层**（原 `1474–1544`）搬出来的，
搬的理由就是**那块测不了** —— 没有假时钟、没有假模型，只能靠真上课去碰。
所以这里的每一条都在用**注入的假时钟 + 假模型**推进时间。

## ⚠️⚠️ 写这个文件时踩到的两件事（别再犯）

1. **分发假模型必须按「对象身份」，不能按内容前缀。**
   第一版写的是 `sysp.startswith("你在听一节课的转录")`，而 `atom.SYS` 实际说的是
   「你在听一节课的**实时**转录」→ **三次调用全被判成章节合成** → 主题恒空 →
   三窗并成一章。而它**不报错**，只是断言悄悄变绿/变红。
   → 一律 `sysp is atom.SYS` / `sysp is ch.SYS_CHAPTER`。
2. **断言要指向那个字段，不是指向「有没有发生」。**
   课务那条第一版只断言「命中没命中」，于是 `CHANGED_RE` 写错了
   （`pushed\\s+to` 追不上真实语序 `pushed the deadline to`）**它照样绿** ——
   而「已改期」整个功能是死的。→ 必须断言 `changed` 的**值**。

## 变异验证记录（每条都真改坏过、确认会红、再改回）

| 改坏哪一行 | 哪条红 |
|---|---|
| `ATOM_PAUSE_S` 30 → 300 | ①「40 秒提交」 |
| `_flush_window` 里失败就 `return`（不留队列） | ②「窗口留着」+「重试成功」 |
| `_flush_window` 去掉 `while len(self._pend) > MAX_PENDING` | ③「停在 MAX_PENDING」+「gap」 |
| `_advance` 去掉「还没定题就认领」那一段 | ⑤「只开一章」 |
| `ChapterWriter._handle` 去掉 `if self._closed` | ⑪「关了拒写」 |
| `finish` 不等 `_running` 就 `_stopped.wait` | ⑥「残余窗口提交了」（会被 join 砍掉） |

⚠️ 本文件**只覆盖 `live_summary` / `chapter` 自己的逻辑**。设计文档
`docs/PLAN-live-summary.md` §11.1 的 T17/T18（`fold_rows` / `build_outline_rows`）
属于**阶段 4**，T22–T24（编号映射 / 覆盖率 / `--fail-window`）属于**阶段 2** —— 都还没做。
"""
from __future__ import annotations

import pathlib
import sys
import tempfile
import threading
import time

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import atom                                                          # noqa: E402
import chapter as ch                                                 # noqa: E402
import live_summary as L                                             # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  {detail}" if detail else ""))


# ---------------------------------------------------------------- 夹具
#: 假模型回的章节合成。`src` 故意落在任何合理的 `[lo, hi]` 里（夹具调用方会给）。
CHAP_REPLY = {"title": "Chapter", "title_zh": "章",
              "sentences": [{"en": "one sentence", "terms": [], "src": [1], "flag": None}]}


def atom_reply(topic: str, n: int = 1) -> dict:
    return {"topic": topic, "topic_zh": topic + "中",
            "points": [{"text": f"p{i}", "kind": "要点", "src": [i], "terms": []}
                       for i in range(n)]}


def make(tmp: str, atom_topics=None, *, atom_fn=None, chap_fn=None,
         atoms_n: int = 1):
    """造一个 `LiveSummarizer`：假时钟隐式（`step(now)` 自带），假模型按**身份**分发。

    返回 `(summ, emits, calls)`；`calls` 是 `["atom"/"chap", …]` 的调用序。
    """
    it = iter(atom_topics or [])
    calls: list[str] = []
    emits: list[dict] = []

    def chat(sysp, block, max_tokens, temperature):
        if sysp is atom.SYS:
            calls.append("atom")
            if atom_fn is not None:
                return atom_fn()
            return atom_reply(next(it), atoms_n)
        assert sysp is ch.SYS_CHAPTER, "只该有两种 system prompt"
        calls.append("chap")
        return chap_fn() if chap_fn is not None else dict(CHAP_REPLY)

    s = L.LiveSummarizer(
        chat=chat, append_atoms=lambda a: len(a),
        chapter_path=ch.chapter_path_for(pathlib.Path(tmp) / "S.md"),
        emit=emits.append)
    return s, emits, calls


def drive(s, n_items: int, *, gap_s: float = 40.0) -> None:
    """确定性推进时间：每句之间隔 1 秒，句后停 `gap_s` 秒（> `ATOM_PAUSE_S`）逼出窗口。"""
    col = 0.0
    for g in range(1, n_items + 1):
        s.feed((g, "10:00:%02d" % g, "sentence %d" % g, "中%d" % g))
        s.step(col)
        col += 1.0
        s.step(col + gap_s)
        col += gap_s


def chapters(emits) -> list:
    return [e["chapter"] for e in emits if e.get("kind") == "chapter"]


def main() -> int:
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="cl_livesum_"))
    try:
        # ─────────────────────────────────────── ① 窗口规则
        print("\n--- ① 窗口规则（与今天的 `atom_worker` 完全一致）---")
        with tempfile.TemporaryDirectory() as d:
            s, _, calls = make(d, ["A"])
            for t, g in ((0.0, 1), (5.0, 2), (10.0, 3)):
                s.feed((g, "10:00:%02d" % g, "s%d" % g, "中"))
                s.step(t)
            check("10 秒时还没提交", not calls, f"calls={calls}")
            s.step(39.0)
            check("39 秒仍不提交（`ATOM_PAUSE_S`=30 从**最后一句**算）", not calls)
            s.step(40.0)
            check("⭐ 40 秒（最后一句 + 30）提交一次", len(calls) == 1, f"calls={len(calls)}")
        with tempfile.TemporaryDirectory() as d:
            s, _, calls = make(d, ["A"])
            t = 0.0
            for g in range(1, 17):                    # t = 0,5,…,75
                s.feed((g, "10:00:00", "s%02d" % g, "中"))
                s.step(t)
                t += 5.0
            check("⭐ 每 5 秒进一句时不触发停顿，到 **75 秒上限**才提交",
                  len(calls) == 1, f"calls={len(calls)}")
        with tempfile.TemporaryDirectory() as d:
            s, _, calls = make(d, ["A"])
            s.feed((1, "10:00:00", "a", "甲"))
            s.step(0.0)
            s.step(29.9)
            check("⚠️ 29.9 秒不提交（**差 0.1 秒也不行**）", not calls)
            s.step(30.0)
            check("30.0 秒提交（边界是闭的）", len(calls) == 1)

        # ─────────────────────────────────────── ② 失败重试
        print("\n--- ② 模型失败**不丢窗口**，`RETRY_S` 后重试 ---")
        with tempfile.TemporaryDirectory() as d:
            wrote: list = []
            calls: list = []

            def flaky(sysp, block, mt, tp):
                calls.append(1)
                if len(calls) == 1:
                    raise RuntimeError("boom")
                return atom_reply("A")

            s = L.LiveSummarizer(chat=flaky, append_atoms=lambda a: (wrote.extend(a), len(a))[1],
                                 chapter_path=None, emit=lambda p: None)
            s.feed((1, "10:00:00", "a", "甲"))
            s.step(0.0)
            s.step(40.0)
            check("第一次失败 -> 没写原子", not wrote)
            check("⭐⭐ 而且**窗口还留着**（不是丢掉）", s.pending == 1, f"pending={s.pending}")
            s.step(45.0)
            check("差 `RETRY_S` 不重试", len(calls) == 1, f"calls={len(calls)}")
            s.step(71.0)
            check("⭐ `RETRY_S`(30) 后重试成功、原子写入",
                  len(calls) == 2 and len(wrote) == 1, f"calls={len(calls)} wrote={len(wrote)}")
            check("⭐⭐ 原子的 `src` 是**全局句号**（base=1 -> `[1]`，不是窗口内的 0）",
                  [a.src for a in wrote] == [[1]], str([a.src for a in wrote]))

        # ─────────────────────────────────────── ③ 积压上限
        print("\n--- ③ 一直失败 -> 停在 `MAX_PENDING`、发 `gap(dropped)` ---")
        with tempfile.TemporaryDirectory() as d:
            def dead(sysp, block, mt, tp):
                raise RuntimeError("down")

            s, emits, _ = make(d, atom_fn=None, chap_fn=None)
            s._chat = dead                            # 换成永远失败的
            col = 0.0
            for _ in range(L.MAX_PENDING + 3):
                s.feed((1, "10:00:00", "s", "中"))
                s.step(col)
                col += 1.0
                s.step(col + 40.0)
                col += 40.0
                s._retry_at = 0.0                     # 手动放行重试闸门（模拟"一直失败但从不停下"）
            check(f"⭐⭐ 积压停在 `MAX_PENDING`={L.MAX_PENDING}", s.pending == L.MAX_PENDING,
                  f"pending={s.pending}")
            gaps = [e for e in emits if e.get("kind") == "gap"]
            check("⭐⭐ 挤掉的窗口发了 `gap`（**不许静默丢**）", len(gaps) == 3, f"gaps={len(gaps)}")
            check("`gap` 带 `t_from`/`t_to`/`state=dropped`",
                  bool(gaps) and gaps[0]["state"] == "dropped"
                  and "t_from" in gaps[0] and "t_to" in gaps[0], str(gaps[:1]))

        # ─────────────────────────────────────── ④ 章节状态机
        print("\n--- ④ A A B：A 恰好一次正式合成，B 另开一章 ---")
        with tempfile.TemporaryDirectory() as d:
            s, emits, _ = make(d, ["A", "A", "B"])
            drive(s, 3)
            s.finish(timeout_s=5.0)
            cs = chapters(emits)
            fin = [c for c in cs if c["status"] == "final"]
            check("⭐ A 只做**一次**正式合成（不因为 B 出现而合两遍）",
                  len([c for c in fin if c["lo"] == 1]) == 1,
                  str([(c["id"], c["status"], c["lo"], c["hi"]) for c in cs]))
            check("⭐ A 覆盖它**全部**句子（lo=1, hi=2）",
                  any(c["lo"] == 1 and c["hi"] == 2 for c in fin),
                  str([(c["lo"], c["hi"]) for c in fin]))
            check("⭐ B 另开一章（lo=3, hi=3）",
                  any(c["lo"] == 3 and c["hi"] == 3 for c in fin),
                  str([(c["lo"], c["hi"]) for c in fin]))

        print("\n--- ⑤ 空主题并入当前章 / 开场的空主题并入第一章 ---")
        with tempfile.TemporaryDirectory() as d:
            s, emits, _ = make(d, ["", "A"])
            drive(s, 2)
            s.finish(timeout_s=5.0)
            cs = chapters(emits)
            check("⭐⭐ 开场那个空主题窗口**并入第一章**（不是自己成一章）",
                  len(cs) == 1 and cs[0]["lo"] == 1 and cs[0]["hi"] == 2,
                  str([(c["lo"], c["hi"]) for c in cs]))
        with tempfile.TemporaryDirectory() as d:
            s, emits, _ = make(d, ["A", "", "A"])
            drive(s, 3)
            s.finish(timeout_s=5.0)
            cs = chapters(emits)
            check("中途的空主题也并入当前章（**不新开**）",
                  len(cs) == 1 and cs[0]["lo"] == 1 and cs[0]["hi"] == 3,
                  str([(c["lo"], c["hi"]) for c in cs]))

        print("\n--- ⑥ A B A -> 三章（**不解冻**旧章）---")
        with tempfile.TemporaryDirectory() as d:
            s, emits, _ = make(d, ["A", "B", "A"])
            drive(s, 3)
            s.finish(timeout_s=5.0)
            cs = chapters(emits)
            check("⭐⭐ 三个**不同**的 id", len({c["id"] for c in cs}) == 3,
                  str([c["id"] for c in cs]))
            check("三章的 lo 分别是 1/2/3", sorted(c["lo"] for c in cs) == [1, 2, 3],
                  str(sorted(c["lo"] for c in cs)))

        print("\n--- ⑦ 临时合成：同 `id`，正式版顶掉临时版 ---")
        with tempfile.TemporaryDirectory() as d:
            s, emits, _ = make(d, ["A"] * 6)
            col = 0.0
            for _ in range(6):
                s.feed((1, "10:00:00", "s", "中"))
                s.step(col)
                col += 1.0
                s.step(col + 40.0)
                col += 40.0
                col += L.INTERIM_AFTER_S              # 手动跳过 8 分钟
            cs = chapters(emits)
            check("⭐ 出现了 `interim`", any(c["status"] == "interim" for c in cs),
                  str([(c["id"], c["status"]) for c in cs]))
            check("⭐⭐ 临时版与正式版**同一个 `id`**",
                  len({c["id"] for c in cs}) == 1, str({c["id"] for c in cs}))
            s.finish(timeout_s=5.0)
            cs = chapters(emits)
            check("⭐ `finish()` 出了 `final`", any(c["status"] == "final" for c in cs),
                  str([(c["id"], c["status"]) for c in cs]))
            on_disk = ch.load(ch.chapter_path_for(pathlib.Path(d) / "S.md"))
            check("⭐ `load()` 对同一个 `id` 取**最后一条**（正式版顶掉临时版）",
                  len(on_disk["chapters"]) == 1
                  and on_disk["chapters"][0]["status"] == "final",
                  str([(c["id"], c["status"]) for c in on_disk["chapters"]]))

        # ─────────────────────────────────────── ⑧ 章节机械闸门
        print("\n--- ⑧ `chapter.parse_reply` 的机械闸门 ---")
        _obj = {"title": "T", "title_zh": "题", "sentences": [
            {"en": "A", "src": [12], "terms": [["x", "甲"]], "flag": "board"},   # ✅
            {"en": "B", "src": [11]},                                            # ❌ lo-1
            {"en": "C", "src": [14]},                                            # ❌ hi+1
            {"en": "D", "src": []},                                              # ❌ 空 src
            {"en": "E", "src": [13], "flag": "nope"},                            # ✅ flag->None
            {"en": "F", "src": [13]},                                            # ✅
            {"en": "G", "src": [13]},                                            # ✅
            {"en": "H", "src": [13]},                                            # ❌ 超 4 句
        ]}
        _got = ch.parse_reply(_obj, 12, 13)
        check("⭐⭐ 越界 `src` 的句子**整句丢弃**（不修剪成窗口内最近的那句）",
              [g["en"] for g in _got] == ["A", "E", "F", "G"], str([g["en"] for g in _got]))
        check("⭐ 非法 `flag` -> `None`（fail-open，不因为一个标记丢掉整句）",
              _got[1]["flag"] is None and _got[0]["flag"] == "board")
        check("⭐ 最多 `MAX_SENTENCES`=4 句", len(_got) == 4)
        check("`terms` 走的是 `atom.terms_of`（**同一条校验**，不是抄一份）",
              _got[0]["terms"] == [["x", "甲"]], str(_got[0]["terms"]))
        check("非 dict / `sentences` 缺失 -> 空表，**不抛**",
              ch.parse_reply(None, 1, 9) == [] and ch.parse_reply({}, 1, 9) == []
              and ch.parse_reply({"sentences": "nope"}, 1, 9) == [])

        print("\n--- ⑨ `build_prompt`：句号必须是**全局**的 ---")
        _p = ch.build_prompt([(12, "Supply slopes up."), (13, "Demand slopes down.")],
                             [atom.Atom(0, "t", 0.0, [12], "定义", "供给曲线向上")],
                             ["Intro", "Elasticity"])
        check("⭐⭐ 用的是**全局句号** 12/13（不是重新编号的 0/1）",
              "12. Supply slopes up." in _p and "13. Demand slopes down." in _p
              and "0. Supply" not in _p)
        check("给了前章标题（只给标题）", "- Intro" in _p and "- Elasticity" in _p)
        check("给了本章已有要点", "[定义] 供给曲线向上" in _p)
        check("空输入不抛", isinstance(ch.build_prompt([], [], []), str))

        # ─────────────────────────────────────── ⑩ 课务
        print("\n--- ⑩ 课务：正则路径立刻上屏、不调模型 ---")
        with tempfile.TemporaryDirectory() as d:
            s, emits, calls = make(d, ["A"])
            s.feed((1, "10:00:00", "The problem set is due next Friday.", "期中作业下周五截止"))
            s.step(0.0)
            ds = [e for e in emits if e.get("kind") == "deadline"]
            check("⭐⭐ 句子一定稿就 `emit`（**没等窗口**）", len(ds) == 1, str(ds))
            check("⭐⭐ 而且**没调模型**（正则不花钱）", not calls, f"calls={calls}")
            check("带 `source=regex` / `changed=False` / `src=[1]`",
                  bool(ds) and ds[0]["source"] == "regex"
                  and ds[0]["changed"] is False and ds[0]["src"] == [1], str(ds))
        with tempfile.TemporaryDirectory() as d:
            s, emits, _ = make(d, ["A"])
            s.feed((1, "10:00:00", "We pushed the deadline to Wednesday.", "我们把截止推到周三"))
            s.step(0.0)
            ds = [e for e in emits if e.get("kind") == "deadline"]
            check("⭐⭐⭐ `changed` **真的是 True**（第一版这里恒 False，"
                  "因为正则写的是 `pushed\\s+to`、追不上 `pushed the deadline to`）",
                  bool(ds) and ds[0]["changed"] is True, str(ds))
        with tempfile.TemporaryDirectory() as d:
            s, emits, calls = make(d, ["A"])
            s.feed((1, "10:00:00", "The exam is postponed.", "考试推迟了"))
            s.step(0.0)
            s.feed((2, "10:00:05", "for example, examine the graph", "比如看图"))
            s.step(6.0)
            ds = [e for e in emits if e.get("kind") == "deadline"]
            check("⭐ 只命中 1 条（`example`/`examine` 不许被 `exam` 命中）",
                  len(ds) == 1, f"命中了 {len(ds)} 条：{[d['quote'] for d in ds]}")
        with tempfile.TemporaryDirectory() as d:
            s, emits, _ = make(d, ["A"])
            s.feed((1, "10:00:00", "The deadline has been extended.", "截止延长了"))
            s.step(0.0)
            ds = [e for e in emits if e.get("kind") == "deadline"]
            check("⭐ 课务**也写进 `.chapters.jsonl`**（课后交接要读它）",
                  bool(ds) and any(r.get("type") == "deadline"
                                   for r in ch.load(
                                       ch.chapter_path_for(pathlib.Path(d) / "S.md"))["deadlines"]),
                  str(ch.load(ch.chapter_path_for(pathlib.Path(d) / "S.md"))["deadlines"]))

        print("\n--- ⑩b 课务合并：`src` 有交集就算同一条 ---")
        _merged = ch.merge_deadlines([
            {"type": "deadline", "t": "12:02:00", "quote": "due Friday",
             "src": [5], "source": "regex", "changed": False},
            {"type": "deadline", "t": "12:03:00", "quote": "作业周五截止",
             "src": [5, 6], "source": "model", "changed": True}])
        check("⭐⭐ 两条合成一条", len(_merged) == 1, str(_merged))
        check("⭐ `source` **两个都记**（`model+regex`）—— 那是来源证据",
              _merged[0]["source"] == "model+regex", str(_merged[0]["source"]))
        check("`changed` 取**或**（有一条说改期就算改期）", _merged[0]["changed"] is True)
        check("`src` 取并集", _merged[0]["src"] == [5, 6], str(_merged[0]["src"]))
        check("`src` **没有**交集 -> 不合并",
              len(ch.merge_deadlines([
                  {"src": [1], "source": "regex"}, {"src": [2], "source": "model"}])) == 2)

        # ─────────────────────────────────────── ⑪ finish / close
        print("\n--- ⑪ `finish()`：残余窗口 + 正式合成；`cancel` 时不调模型 ---")
        with tempfile.TemporaryDirectory() as d:
            s, emits, calls = make(d, ["A"])
            s.feed((1, "10:00:00", "a", "甲"))
            s.step(0.0)                               # 只喂不提交 —— 这就是"残余窗口"
            s.finish(timeout_s=5.0)
            check("⭐⭐ 残余窗口被**提交**了（不是随线程退出一起丢）", "atom" in calls,
                  f"calls={calls}")
            check("⭐⭐ 当前章做了**正式**合成",
                  [c["status"] for c in chapters(emits)] == ["final"],
                  str([c["status"] for c in chapters(emits)]))
        with tempfile.TemporaryDirectory() as d:
            s, _, calls = make(d, ["A"])
            s.feed((1, "10:00:00", "a", "甲"))
            s.step(0.0)
            ev = threading.Event()
            ev.set()
            s.finish(timeout_s=5.0, cancel=ev)
            check("⭐⭐ `cancel` 已置位 -> **模型调用 = 0**", not calls, f"calls={calls}")
            check("⭐ 但文件**照样关了**（`close()` 一定要跑到）", s._closed is True)
        with tempfile.TemporaryDirectory() as d:
            def slow(sysp, block, mt, tp):
                time.sleep(5.0)
                return atom_reply("A")

            s = L.LiveSummarizer(chat=slow, append_atoms=lambda a: len(a),
                                 chapter_path=None, emit=lambda p: None)
            s.feed((1, "10:00:00", "a", "甲"))
            s.step(0.0)
            t0 = time.monotonic()
            s.finish(timeout_s=1.0)
            dt = time.monotonic() - t0
            check("⭐⭐ `finish` 超时：假模型睡 5 秒、`timeout_s=1` -> **约 1 秒返回**",
                  dt < 2.5, f"{dt:.2f}s")
            check("⭐ 超时也**照样关文件**", s._closed is True)

        print("\n--- ⑫ `feed()` 永不抛 / 关闭后忽略 ---")
        with tempfile.TemporaryDirectory() as d:
            s, _, _ = make(d, ["A"])
            raised = None
            for bad in (None, 1, "x", (), (1, 2), (1, 2, 3, 4, 5), {}, []):
                try:
                    s.feed(bad)
                except Exception as e:                # noqa: BLE001
                    raised = f"{bad!r} -> {type(e).__name__}: {e}"
                    break
            check("⭐⭐ 各种畸形 item **都不抛**（`drain()` 没有 try，这里一抛就打死主循环）",
                  raised is None, raised or "")
        with tempfile.TemporaryDirectory() as d:
            s, _, _ = make(d, ["A"])
            s.close()
            n0 = s._q.qsize()
            s.feed((1, "10:00:00", "a", "甲"))
            check("⭐⭐ `close()` 之后 `feed` **直接忽略**（不再进队列）",
                  s._q.qsize() == n0, f"qsize {n0} -> {s._q.qsize()}")

        # ─────────────────────────────────────── ⑬ 写入器
        print("\n--- ⑬ `ChapterWriter`：关了拒写（`AtomWriter` 那半是单独一个提交）---")
        with tempfile.TemporaryDirectory() as d:
            p = ch.chapter_path_for(pathlib.Path(d) / "2026-01-01_120000_X.md")
            check("路径形状 = `<同名>.chapters.jsonl`",
                  p.name == "2026-01-01_120000_X.chapters.jsonl", p.name)
            w = ch.ChapterWriter(p)
            w.append([{"type": "window", "w": 1}])
            before = p.read_bytes()
            w.close()
            w.close()                                 # 幂等
            n = w.append([{"type": "deadline", "t": "12:09:00", "quote": "迟到",
                           "src": [99], "source": "regex", "changed": False}])
            check("⭐⭐ 关了之后 `append` 返回 0", n == 0, f"{n}")
            check("⭐⭐ 而且文件**逐字节没变**（不重开、不写脏数据）",
                  p.read_bytes() == before)
            check("文件**不删**（它是这份笔记的证据）", p.exists())
        with tempfile.TemporaryDirectory() as d:
            w = ch.ChapterWriter(None)
            check("没有路径 -> 不写、不抛", w.append([{"type": "window"}]) == 0)
            w.close()
        check("⭐⭐ `load()` 文件不存在 -> 空的三组，**不抛**",
              ch.load("/tmp/definitely-not-here-xyz.jsonl")
              == {"windows": [], "chapters": [], "deadlines": []})

        # ─────────────────────────────────────── ⑭ 线程分工
        print("\n--- ⑭ `run()` 只等唤醒、**不消费**队列 ---")
        with tempfile.TemporaryDirectory() as d:
            s, _, _ = make(d, ["A"])
            for g in (1, 2, 3):
                s.feed((g, "10:00:00", "s%d" % g, "中"))
            ev = threading.Event()
            ev.set()
            th = threading.Thread(target=s.run, args=(ev,), daemon=True)
            th.start()
            time.sleep(0.15)
            ev.clear()
            th.join(timeout=3.0)
            check("⭐⭐ `run()` 跑过一轮之后，那 3 条**都还在**（没被它吃掉）",
                  len(s._buf) == 3, f"buf={len(s._buf)}")
            check("`run()` 退出时置位 `_stopped`", s._stopped.is_set())

        # ─────────────────────────────────────── ⑮ 外部审查抓到的六条
        # ⚠️ 这一整节是 2026-09-30 `ocr` 审出来的**六条真问题** —— 修完补判据，
        #    让它们回不来。每条都注明了「改坏哪里会红」。
        print("\n--- ⑮ `load()` 的 `None` / 空路径：**预期输入**，不是理论边界 ---")
        # 改坏：去掉 `load()` 开头那句 `if not path:` -> 三条红（`pathlib.Path(None)` 抛 TypeError）。
        check("⭐⭐ `load(None)` 返回空的三组、**不抛**（`chapter_path_for` 空路径就返回 None）",
              ch.load(None) == {"windows": [], "chapters": [], "deadlines": []}, "")
        check("⭐ `load('')` 同理", ch.load("") == {"windows": [], "chapters": [], "deadlines": []})
        check("⭐ `load(0)` / 非路径类型也不抛", ch.load(0)["chapters"] == [])

        print("\n--- ⑯ `finish()` 要先排空 `_q`（调用期间进来的句子不许静默丢）---")
        with tempfile.TemporaryDirectory() as d:
            s, emits, calls = make(d, ["A"])
            # ⚠️ **只 `feed`、一次 `step` 都不调** —— 模拟「模型调用期间句子进来了，
            #    然后 running.clear()、循环直接退出」那条真实时序。
            for g in (1, 2):
                s.feed((g, "10:00:%02d" % g, "s%d" % g, "中"))
            check("前置：一个都没进 `_buf`", not s._buf, f"buf={len(s._buf)}")
            s.finish(timeout_s=5.0)
            check("⭐⭐ 残余窗口提交了（`_q` 被排空，不是留在队列里烂掉）", "atom" in calls,
                  f"calls={calls}")

        print("\n--- ⑰ 原子的 `id` 必须**跨窗口递增**（原 `main.py` 用 `atom_st['n']` 记的就是这个）---")
        with tempfile.TemporaryDirectory() as d:
            s, emits, _ = make(d, ["A", "A"])
            drive(s, 2)
            ids = [a["id"] for e in emits if e.get("kind") == "atoms" for a in e["items"]]
            check("⭐⭐ 没有重复 id", len(ids) == len(set(ids)), f"ids={ids}")
            check("⭐ 而且是递增的", ids == sorted(ids) and ids and ids[0] == 0, f"ids={ids}")

        print("\n--- ⑱ 主题比较要**去空格 + 不分大小写**（计划 §6.3）---")
        with tempfile.TemporaryDirectory() as d:
            s, emits, _ = make(d, ["Price Elasticity", "price elasticity "])
            drive(s, 2)
            s.finish(timeout_s=5.0)
            cs = chapters(emits)
            check("⭐⭐ 大小写/尾随空格不同 -> **还是同一章**（否则凭空多一章 + 多花一次合成）",
                  len(cs) == 1, str([(c["id"], c["lo"], c["hi"]) for c in cs]))

        print("\n--- ⑲ 机械闸门对**非容器**输入不许抛（闸门自己炸掉比放过脏数据糟）---")
        # 改坏：把 `chapter.parse_reply` 里 `raw_sents` / `raw_src` 的 isinstance 守卫去掉
        #       （或 `atom.parse_reply` 里 `raw_points` / `raw_src` 那两处）-> 下面各自红。
        raised = None
        try:
            check("⭐ `chapter`: `sentences` 非列表 -> 空表", ch.parse_reply({"sentences": 3}, 1, 9) == [])
            check("⭐ `chapter`: 单句 `src` 非列表 -> 该句丢（其余保留）",
                  [g["en"] for g in ch.parse_reply(
                      {"sentences": [{"en": "A", "src": 12},
                                     {"en": "B", "src": [3]}]}, 1, 9)] == ["B"])
            check("⭐ `atom`: `points` 非列表 -> 空表", atom.parse_reply({"points": 3}, 1) == [])
            check("⭐ `atom`: 单条 `src` 非列表 -> 该条丢",
                  [a.text for a in atom.parse_reply(
                      {"points": [{"text": "A", "src": 5},
                                  {"text": "B", "src": [0]}]}, 1)] == ["B"])
        except Exception as e:                            # noqa: BLE001
            raised = f"{type(e).__name__}: {e}"
        check("⭐⭐ 而且**全程没抛**（抛了会冒到 worker 线程把总结器带走）", raised is None, raised or "")

        print("\n--- ⑳ `run()` 里一次 `step` 抛了**不许把线程带走** ---")
        with tempfile.TemporaryDirectory() as d:
            s, _, _ = make(d, ["A"])
            n: list = []

            def boom_step(now):
                n.append(1)
                raise RuntimeError("step 炸了")

            s.step = boom_step
            ev = threading.Event()
            ev.set()
            th = threading.Thread(target=s.run, args=(ev,), daemon=True)
            th.start()
            for _ in range(6):
                s._wake.set()
                time.sleep(0.05)
            ev.clear()
            th.join(timeout=3.0)
            # 改坏：去掉 `run()` 里那个 try -> 只被调 1 次（`run` 直接返回）。
            check("⭐⭐ `step` 抛了之后循环**继续**（不是整条线程死掉）",
                  len(n) >= 2, f"step 被调了 {len(n)} 次")

        # ─────────────────────────────────────── ㉑ 独立审查抓到的五条
        # ⚠️ 2026-09-30 一个**独立审查 agent** 报的（`ocr` 没抓到），我逐条复现过再修。
        print("\n--- ㉑ ⭐⭐⭐ 收尾竞态：worker 还活着时**一律不许碰共享状态** ---")
        with tempfile.TemporaryDirectory() as d:
            blocks: list = []
            T = [0.0]

            def slow(sysp, block, mt, tp):
                blocks.append(block)
                time.sleep(2.0)                  # 超过收尾预算的一次调用
                return atom_reply("A")

            s = L.LiveSummarizer(chat=slow, append_atoms=lambda a: len(a),
                                 chapter_path=ch.chapter_path_for(pathlib.Path(d) / "S.md"),
                                 emit=lambda p: None, clock=lambda: T[0])
            running = threading.Event()
            running.set()
            wk = threading.Thread(target=s.run, args=(running,), daemon=True)
            wk.start()
            s.feed((1, "10:00:00", "First sentence.", "第一句"))
            time.sleep(0.3)                      # worker 取件进 buf
            T[0] = 40.0                          # 推过 `ATOM_PAUSE_S` -> 下次 step 提交
            s._wake.set()
            time.sleep(0.4)                      # worker 现在**卡在那次 2 秒的调用里**
            running.clear()                      # ← `main.py:1678` 就是这么做的
            s.finish(timeout_s=0.3)
            wk.join(timeout=6)

            # 改坏：把 `if not self._stopped.wait(timeout_s): return` 那句删掉
            #       -> 收尾会**把同一个窗口再投一次**（实测 blocks 变 2 条、内容完全相同）。
            check("⭐⭐⭐ 同一个窗口**没有被投给模型两次**（那意味着双倍 API 花费）",
                  len(blocks) == len(set(blocks)), f"blocks={blocks}")
            check("⭐⭐ worker 还活着 -> 收尾**只关文件**（放弃剩下的步骤，计划 §6.3）",
                  s._closed is True)

        print("\n--- ㉒ `terms_of` 对**非容器** `terms` 不许抛 ---")
        _raised = None
        try:
            for _v in (3, 3.5, True, {"a": 1}):
                check(f"⭐ `terms={_v!r}` -> 不抛",
                      atom.terms_of({"terms": _v}) == [])
            check("⭐⭐ 走生产那条路（`atom.parse_reply`）也不抛",
                  atom.parse_reply({"points": [{"text": "t", "kind": "要点",
                                                "src": [0], "terms": 3}]}, 1)[0].terms == [])
        except Exception as e:                    # noqa: BLE001
            _raised = f"{type(e).__name__}: {e}"
            check("⭐ 非容器 `terms` 不许抛", False, _raised)

        print("\n--- ㉓ `cancel` 已置位时 `finish` **不许**等锁/等 worker ---")
        with tempfile.TemporaryDirectory() as d:
            T = [0.0]

            def slow2(sysp, block, mt, tp):
                time.sleep(6.0)                      # 一次很长的调用
                return {"topic": "A", "points": []}

            s = L.LiveSummarizer(chat=slow2, append_atoms=lambda a: len(a),
                                 chapter_path=ch.chapter_path_for(pathlib.Path(d) / "S.md"),
                                 emit=lambda p: None, clock=lambda: T[0])
            running = threading.Event()
            running.set()
            threading.Thread(target=s.run, args=(running,), daemon=True).start()
            s.feed((1, "10:00:00", "First sentence.", "第一句"))
            time.sleep(0.3)                           # worker 取件
            T[0] = 40.0
            s._wake.set()
            time.sleep(0.4)                           # ⭐ worker 现在**正持着 `_step_lock`**
            check("前置：worker 真的在跑（锁被占着）",
                  s._step_lock.locked(), f"locked={s._step_lock.locked()}")
            ev = threading.Event()
            ev.set()                                  # 「跳过精修」/ Ctrl+C
            t0 = time.monotonic()
            s.finish(timeout_s=1.0, cancel=ev)
            dt = time.monotonic() - t0
            # 改坏：把 `work()` 开头那个 `cancel` 判断删掉 -> 它会去 `acquire(timeout=1.0)`
            #       而锁被 worker 占着 -> 白等满 1 秒（实测）。
            check("⭐⭐ cancel 已置位 -> **立刻**返回，不去等锁（那条路本来就不调模型）",
                  dt < 0.5, f"耗时 {dt:.2f}s（timeout_s=1.0，且锁被占着）")

        print("\n--- ㉔ `load()` 对**坏行**不许抛（它在课后的笔记路径上）---")
        with tempfile.TemporaryDirectory() as d:
            bad = pathlib.Path(d) / "bad.chapters.jsonl"
            bad.write_text('{"type": "chapter", "id": [1], "status": "final"}\n'
                           '{"type": "deadline", "src": 3, "quote": "x"}\n'
                           '{"type": "chapter", "id": 0, "status": "final", "lo": 1, "hi": 9}\n',
                           encoding="utf-8")
            _r = None
            try:
                _r = ch.load(bad)
            except Exception as e:                # noqa: BLE001
                _r = f"{type(e).__name__}: {e}"
            check("⭐⭐ 坏行（`id` 是 list / `src` 不是 list）跳过，**不抛**",
                  isinstance(_r, dict), str(_r)[:120])
            check("⭐ 而且**同一份文件里的好行照常读出来**",
                  isinstance(_r, dict) and len(_r["chapters"]) == 1
                  and _r["chapters"][0]["id"] == 0, str(_r if isinstance(_r, str) else _r.get("chapters")))

        print("\n--- ㉕ 课务词形：**复数**和 `moving` 要命中 ---")
        _CASES2 = [("The exams are next week.", True, False),
                   ("The midterms are coming up.", True, False),
                   ("The quizzes start in week 6.", True, False),
                   ("All assignments are submitted online.", True, False),
                   ("The problem sets are due Friday.", True, False),
                   ("I'm moving the deadline to Friday.", True, True),
                   ("We are pushing the exam to next week.", True, True),
                   # ⚠️ 反面：**只有改期动词、没有课务对象** -> 不算（不知道被推翻的是什么）
                   ("It's been pushed back a week.", False, False),
                   # ⚠️ 反面：词边界仍然要挡住
                   ("for example, examine this", False, False),
                   ("during the lecture", False, False)]
        _bad25 = []
        for _s, _hit, _chg in _CASES2:
            _r = ch.deadline_hits(_s)
            _got = (_r is not None, bool(_r and _r["changed"]))
            if _got != (_hit, _chg):
                _bad25.append(f"{_s!r} -> {_got} 期望 {(_hit, _chg)}")
        check("⭐⭐ 九条全对（复数 + `moving` + 词边界仍然生效）",
              not _bad25, "; ".join(_bad25))

    except BaseException as e:                            # noqa: BLE001
        # ⚠️⚠️ **一条判据自己抛了，不许把整个文件带崩。**
        #    崩了的话：后面的组**一条都不跑**、只留一个 traceback、**没有 ❌ 行** ——
        #    而"有没有 ❌"正是这个仓库惯用的读法，等于**量具本身坏了**。
        #    （类似形状记过两次：`test_atom.main()` 只有 `finally` 没有 `except`。）
        #    ⚠️ 它仍然是**失败**（`ok=False`），退出码照样非零。
        RESULTS.append((f"⚠ 判据自己抛了（**后面的组没跑**）：{type(e).__name__}: {e}", False, ""))
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    # ⚠️ `bad` 必须**就在结算这一处**算 —— `test_entry_panel.py` 栽过一次：
    #    它算在中间，加在它后面的判据**只打印、不进统计、失败也不影响退出码**。
    bad = [n for n, ok, _ in RESULTS if not ok]
    print("\n" + "=" * 60)
    print(f"{len(RESULTS) - len(bad)}/{len(RESULTS)} 通过")
    for n in bad:
        print(f"  ❌ {n}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
