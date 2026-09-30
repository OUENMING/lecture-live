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
| `sentences()` 的 `_parse` 版换回「EN 行的序号」 | ㉚「全局句号是 `[1,4,6]`」（错的那套给 `[1,3,5]`） |
| `coverage_of` 的 `lost` 写成 `hc - ha` | ㉛「`lost` 方向不能反」+「两个口径都算对」 |
| `coverage_of` 空集返回 `0.0` 而不是 `None` | ㉛「一条都没挑到时返回 `None`」 |

⚠️ 本文件**只覆盖 `live_summary` / `chapter` 自己的逻辑**。
`docs/PLAN-live-summary.md` §11.1 的 **T1–T24 已经全部落地**（2026-09-30 收尾时核过）：
T17/T18 在 ㉟/㊱（`overlay` 的纯函数，不起窗口）· T22/T23 在 ㉚/㉛ · T24 在 ㉜。
⚠️ 别照旧版的这句话去"补" —— 它曾写着「还差 T17/T18 与 T24」，而那三组**早就在文件里了**
（2026-09-30 审计发现这行过期，已改）。
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


def drive(s, n_items: int, *, gap_s: float = 190.0) -> None:
    """确定性推进时间：每句之间隔 1 秒，句后停 `gap_s` 秒。

    ⚠️ **默认 190 秒不是随便取的**：`MIN_CHAPTER_S = 180`（太短的章不许换），
       所以夹具**必须推过 180 秒**，否则「换题」会被并进当前章 —— 那是**对的**行为，
       但会让「A→B 该开两章」这类判据假红。
    ⚠️ 又必须**停在 `MAX_CHAPTER_S = 600` 以下**（到点会强制切）——
       190 × 3 窗 = 573 秒，正好夹在两者中间。
    """
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
            for g in range(1, 4):                     # 3 窗攒内容
                s.feed((g, "10:00:%02d" % g, "s%d" % g, "中%d" % g))
                s.step(col)
                col += 1.0
                s.step(col + 40.0)
                col += 40.0
            # ⚠️ 把时钟推过 `INTERIM_AFTER_S`(480) —— 但**必须停在
            #    `MAX_CHAPTER_S`(600) 以下**，否则会被强制切章（那是另一条判据 ㉗）。
            s.step(s._cur["t_open"] + L.INTERIM_AFTER_S + 5.0)
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

        print("\n--- ⑧b ⭐ `zh`（中文译文）：坏形状**置空，但句子照留** ---")
        # ⚠️ 判据指向的是**这个字段**，不是「整句还在不在」——
        #    它的失效形态是「内容还在、只是没中文」，丢句就过头了。
        # ⚠️ **取值器要安全**：`_one(...)[0]` 写在 `check()` 的参数里是**急切求值**的 ——
        #    句子被丢掉时它先抛 `IndexError`，报告变成「判据自己抛了」而不是那条断言红
        #    （诊断差一档，而这一条恰恰是「内容被静默丢掉」的形态，最需要说清楚）。
        def _p(**kw):
            kw.setdefault("en", "X")
            kw.setdefault("src", [1])
            return ch.parse_reply({"sentences": [kw]}, 1, 1)

        def _zh(**kw):
            got = _p(**kw)
            return got[0].get("zh", "<没有 zh 键>") if got else "<整句被丢了>"

        _over = "长" * (ch.SENT_ZH_MAX_CHARS + 1)
        check("⭐ 正常 `zh` 原样带出（换行拍平成空格，同 `en`）",
              _zh(zh="甲译文") == "甲译文" and _zh(zh="带\n换行") == "带 换行",
              repr(_zh(zh="带\n换行")))
        # 改坏：把 `if len(zh) > SENT_ZH_MAX_CHARS: zh = ""` 改成 `continue` -> 这条红。
        # 改坏：把 `str(s.get("zh") or "")` 那个兜底去掉 -> 缺 `zh` 那条也红。
        check("⭐⭐ 缺 / `None` / 超长 -> 一律空串，**句子照留**（不丢内容）",
              _zh() == "" and len(_p()) == 1
              and _zh(zh=None) == "" and len(_p(zh=None)) == 1
              and _zh(zh=_over) == "" and len(_p(zh=_over)) == 1,
              repr([_zh(), len(_p()), _zh(zh=None), _zh(zh=_over)]))
        check("⭐ 正好卡在上限上的 `zh` **不算超长**（边界，`>` 不是 `>=`）",
              _zh(zh="长" * ch.SENT_ZH_MAX_CHARS) != "")
        # ⚠️ 非字符串走 `str()` —— 这是**跟 `en` 逐字一致**的处理（`str(s.get("en") or "")`），
        #    不是"校验"：一个数字 `zh` 会变成 `"12345"` 显示出来（垃圾进垃圾出）。
        #    刻意**不**在这里引入第二套规则 —— 两个字段两种脾气比一个坏字段更糟。
        check("⭐ 非字符串走 `str()`（与 `en` 同一条规则，不另立一套）",
              _zh(zh=12345) == "12345", repr(_zh(zh=12345)))

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

        # ─────────────────────────────────────── ㉖㉗ 章节粒度的两个旋钮
        # ⚠️ 2026-09-30 实测加的：**粒度不能交给 prompt 控**。同一节 42 分钟的课，
        #    中性措辞切出 14 章（7 个不到 2 分钟），「只有真换话题才换」切出 1 章 ——
        #    **两个方向都过了头，而给尺度提示没有用**。
        #    同行评审（`arxiv 2512.17083` §9.1）：「separating scoring from selection
        #    enables boundary density to be controlled independently of scoring
        #    granularity」→ 打分归模型、**选择归状态机**。
        print("\n--- ㉖ `MIN_CHAPTER_S`：太短的章**不许换** ---")
        with tempfile.TemporaryDirectory() as d:
            s, emits, _ = make(d, ["A", "B", "C"])    # 每个窗口都想换题
            col = 0.0
            for g in (1, 2, 3):                       # 总时长 123 秒 < 180
                s.feed((g, "10:00:%02d" % g, "s%d" % g, "中%d" % g))
                s.step(col)
                col += 1.0
                s.step(col + 40.0)
                col += 40.0
            s.finish(timeout_s=5.0)
            cs = chapters(emits)
            # 改坏：把 `_advance` 里那句 `now - self._cur["t_open"] < MIN_CHAPTER_S` 删掉
            #       -> A/B/C 各成一章（3 章）。
            check("⭐⭐ 三次换题都不足 180 秒 -> **只有一章**（换题被并进当前章）",
                  len(cs) == 1, str([(c["id"], c["lo"], c["hi"]) for c in cs]))

        print("\n--- ㉗ `MAX_CHAPTER_S`：太长的章**强制切** ---")
        with tempfile.TemporaryDirectory() as d:
            s, emits, _ = make(d, ["A"] * 19)         # 同一主题，模型永远不换
            col = 0.0
            for g in range(1, 20):
                s.feed((g, "10:00:%02d" % g, "s%d" % g, "中%d" % g))
                s.step(col)
                col += 1.0
                s.step(col + 190.0)
                col += 190.0
            s.finish(timeout_s=5.0)
            cs = chapters(emits)
            ids = {c["id"] for c in cs}
            # 改坏：把 `_absorb_window` 里那句 `self._maybe_force_split(now)` 删掉
            #       -> 整段只有 **1 个 id**（19 窗 × 191 秒 ≈ 60 分钟只有一章）。
            # ⚠️⚠️ 第一版这里断言的是 `len(cs) >= 4`（emit 条数）—— **那是假绿**：
            #     emit 里混着**临时合成**（同一 id 会 emit 很多次），
            #     所以删掉 force-split 之后照样 ≥4。→ **必须数不同的 `id`。**
            #     （2026-09-30 变异验证抓到的。）
            check(f"⭐⭐ 同一主题讲了 {col / 60:.0f} 分钟 -> 被 `MAX_CHAPTER_S` 切成**多个不同的章**",
                  len(ids) >= 4, f"{len(ids)} 个 id（emit 共 {len(cs)} 条）")

        print("\n--- ㉘ `_topic_prev` 必须是**上一窗**主题，不是章标题 ---")
        # ⚠️⚠️ 2026-09-30 实测：喂**章标题**会让模型**一开章就沿用** →
        #    模型侧边界全被抑制，章全靠 `MAX_CHAPTER_S` 到点硬切
        #    （三节课的章长中位全是 ~615 秒 = 正好撞天花板）。
        #    原 `main.py` 的 `atom_st["prev"]` 存的是**上一窗主题**。
        #
        # ⚠️⚠️ **这条判据要构造「两者不同」的时刻**，否则区分不开：
        #    第一版用的是「正常换题」的时序 —— 那时**章标题恰好等于上一窗主题**，
        #    所以把实现改回章标题**照样绿**（2026-09-30 变异验证抓到的）。
        #    → 必须制造一次**被 `MIN_CHAPTER_S` 拦下的换题**：那一刻
        #      `_prev_topic` 已经是新主题 B，而章标题**还停在旧的 A**。
        with tempfile.TemporaryDirectory() as d:
            s, _, _ = make(d, ["A", "B", "C"])
            seen: list = []
            base_chat = s._chat

            def spy(sysp, block, mt, tp):
                # ⚠️ **在调用那一刻快照章标题** —— 不能等跑完再看：
                #    第三次调用**之后**才发生的换章会把 `_cur` 变成 C，
                #    于是「章标题还是 A」这个前置条件看起来不成立（第一版就这么红的）。
                seen.append((block, (s._cur or {}).get("title")))
                return base_chat(sysp, block, mt, tp)

            s._chat = spy
            s.feed((1, "10:00:00", "s1", "中")); s.step(0.0)
            s.step(200.0)                    # w1 提交：主题 A -> 章标题 A，t_open=200
            s.feed((2, "10:00:01", "s2", "中")); s.step(201.0)
            s.step(300.0)                    # w2 提交：主题 B，但离 t_open 只 100s < 180 -> 被并
            s.feed((3, "10:00:02", "s3", "中")); s.step(301.0)
            s.step(600.0)                    # w3 提交
            # ⚠️ 前置：**在第三次调用那一刻**，章标题还是 A（B 那次换题真的被拦了）
            check("⚠️ 前置：第二次那次换题**真的被 `MIN_CHAPTER_S` 拦下了**"
                  "（第三次调用时章标题还是 A）",
                  len(seen) >= 3 and seen[2][1] == "A",
                  f"第三次调用时的章标题={seen[2][1]!r}" if len(seen) >= 3
                  else f"只拿到 {len(seen)} 次")
            # 改坏：把 `_topic_prev` 改回 `self._cur["title"] if self._cur else ""`
            #       -> 这时它会给出 **A**（章标题），而不是 **B**（上一窗主题）。
            check("⭐⭐ 第三次调用拿到的 prev 是**上一窗**的主题 B（不是章标题 A）",
                  len(seen) >= 3 and "上一段的主题是「B」" in seen[2][0],
                  f"第 3 个 block：{seen[2][0][:44]!r}" if len(seen) >= 3
                  else f"只拿到 {len(seen)} 次")

        print("\n--- ㉙ 模型报的 `课务` 必须过 Jev 打分闸门 ---")
        # ⚠️ 2026-09-30：模型报的 `课务` 假阳性 70–80%，收紧 prompt 两轮只从 14 降到 8。
        #    → 换成**结构性闸门**：拦在模型输出后面，不靠模型自觉。
        #    ⭐ 标定依据：3 节课 / 21 条正样本 + 21 条对照，对照的天花板 0.12–0.14 → 取 0.20。

        def _mk_gate_case(gate, tmp):
            de: list = []

            def chat(sysp, block, mt, tp):
                if sysp is atom.SYS:
                    return {"topic": "A", "topic_zh": "",
                            "points": [{"text": "The essay is due Friday.", "kind": "课务",
                                        "src": [0], "terms": []}]}
                return {"title": "T", "title_zh": "", "sentences": []}

            _s = L.LiveSummarizer(
                chat=chat, append_atoms=lambda a: len(a),
                chapter_path=ch.chapter_path_for(pathlib.Path(tmp) / "S.md"),
                emit=de.append, deadline_gate=gate)
            _s.feed((1, "10:00:00", "x", "中"))
            _s.step(0.0)
            _s.step(40.0)
            return [e for e in de if e.get("kind") == "deadline"], _s

        with tempfile.TemporaryDirectory() as d:
            r, s = _mk_gate_case(None, d)
            # 改坏：把 `_deadline_ok` 的 `if self._deadline_gate is None: return False`
            #       改成 `return True` -> 没配时也会报（假阳性全回来）。
            check("⭐⭐ **没配闸门 -> 一条都不报**（fail-closed；模型路径没过滤时精度只有 ~25%）",
                  not r and s.gate_calls == 0, f"报了 {len(r)} 条")
        with tempfile.TemporaryDirectory() as d:
            r, s = _mk_gate_case(lambda t: 0.05, d)
            check("⭐ 低于 `DEADLINE_MIN_P` -> 不报", not r and s.gate_calls == 1,
                  f"报了 {len(r)} 条 · gate_calls={s.gate_calls}")
        with tempfile.TemporaryDirectory() as d:
            r, s = _mk_gate_case(lambda t: L.DEADLINE_MIN_P + 0.01, d)
            check("⭐⭐ 高于阈值 -> 报", len(r) == 1 and s.gate_calls == 1,
                  f"报了 {len(r)} 条")
        with tempfile.TemporaryDirectory() as d:
            def _boom(t):
                raise RuntimeError("jev 挂了")

            r, s = _mk_gate_case(_boom, d)
            check("⭐⭐ 闸门抛异常 -> **不报且不崩**（一次网络抖动不该带走整节课）",
                  not r and s.gate_calls == 1, f"报了 {len(r)} 条")
        with tempfile.TemporaryDirectory() as d:
            r, s = _mk_gate_case(lambda t: None, d)      # 拿不到分（超时/没解析出来）
            check("⭐ 闸门返回 `None` -> 不报", not r, f"报了 {len(r)} 条")
        check("⭐ Jev 的调用次数与 DeepSeek 的**分开计**（两个服务、两笔钱）",
              hasattr(L.LiveSummarizer, "gate_calls") and hasattr(L.LiveSummarizer, "calls"))

        print("\n--- ㉚ ⭐ 编号映射：`with_index=True` 的号必须是**全局句号** ---")
        # ⚠️ 这一组是覆盖率的前置（计划 §7.4）：映射错了，覆盖率会是一个
        #    **像模像样的错数**（不是 0，所以对账时根本看不出来）。
        import obsidian_writer as OW
        import keypoints as KP
        with tempfile.TemporaryDirectory() as d:
            sp = pathlib.Path(d) / "S.md"
            # 三档句：长的（留）· 短的（丢）· ⭐ **`en` 为空的**（丢，但**仍然占一个号**）
            #   —— `ObsidianWriter.append` 的四个 `if` 决定了空 `en` 不写 EN 行，
            #      可 `self._n += 1` 在那之前就加了 → **条目在、EN 行不在**。
            rows = [("09:00:01", "This is a long enough sentence about demand."),
                    ("09:00:02", "Okay."),
                    ("09:00:03", ""),          # 只写 ZH
                    ("09:00:04", "Elasticity measures responsiveness of demand."),
                    ("09:00:05", "Right."),
                    ("09:00:06", "The supply curve slopes upward in most markets.")]
            tag_en, tag_zh = dict(OW._FIELDS)["en"], dict(OW._FIELDS)["zh"]
            lines = ["# LECTURE · test · 实时会话日志", ""]
            for ts, en in rows:
                hdr = f"> [!abstract] {ts}"
                # 格式**自钉**：写出来的抬头必须被**生产那个正则**认出来 ——
                # 否则判据就变成"在测一份过期的格式"，而它照样全绿。
                check(f"抬头 {ts} 与 `obsidian_writer._TS` 一致",
                      OW._TS.match(hdr) is not None, hdr)
                lines.append(hdr)
                lines.append(f"> **{tag_en}**: {en}" if en else f"> **{tag_zh}**: 只写了中文")
                lines.append("")
            sp.write_text("\n".join(lines) + "\n", encoding="utf-8")

            flat = KP.sentences(sp)
            idx = KP.sentences(sp, with_index=True)
            check("句数与不带索引**逐句相同**（过滤规则没分叉）",
                  [s for _, s in idx] == flat and len(flat) == 3, f"{len(flat)} 句")
            gids = [g for g, _ in idx]
            # 改坏：把 `sentences()` 换回"EN 行的序号"那版（已实测）→ 这条红。
            # ⚠️ 那时得到的是 `[1, 3, 5]`（EN 行有 5 行：`Okay.`/`Right.` 也有 EN 行、
            #    只有那个空 `en` 的没有）—— **不是 `[1, 2, 3]`**。
            #    这正是要钉的形态：错的那套给出来的是**一个像模像样的数**。
            check("⭐⭐ 全局句号是 `[1, 4, 6]` —— **空 `en` 的条目照占一个号**",
                  gids == [1, 4, 6], str(gids))
            check("单调递增", all(b > a for a, b in zip(gids, gids[1:])), str(gids))
            check("确实跳过了短句（不是 `1..N` 连续号）",
                  gids != list(range(1, len(gids) + 1)), str(gids))
            check("⚠️ `with_index=False` 返回**字符串表**（既有调用方不用改）",
                  all(isinstance(s, str) for s in flat), str(type(flat[0]).__name__))
            # ⚠️ 只写抬头、一句都没记的会话（麦克风故障 / 刚开课就关）→ 空表。
            # ⚠️ **刻意不测「文件不存在」**：旧实现（扫 `> **EN**: ` 行那版）也是**直接抛**，
            #    那是 CLI 那条路的既有行为。生产路径根本不经过它 ——
            #    `pick()` 收的是**已读好的句子表**，`main._keypoints_for` 自己包了
            #    `try/except Exception: return []`。**没变的行为别在判据里改**。
            (pathlib.Path(d) / "空.md").write_text("# LECTURE · test\n\n", encoding="utf-8")
            check("一句都没有的会话 -> 空表，不抛",
                  KP.sentences(pathlib.Path(d) / "空.md") == [])

        print("\n--- ㉛ ⭐ 覆盖率两个口径 —— 按**手算**对拍 ---")
        sys.path.insert(0, str(HERE / "docs" / "experiments"))
        import probe_live_summary as P
        # 设：Jev 挑了全局句号 {3, 7, 9, 12}
        _atoms = [{"src": [3, 4]}, {"src": [7]}, {"src": [11, 12, 13]}]  # 并集 {3,4,7,11,12,13}
        _chaps = [{"sentences": [{"src": [3]}, {"src": [8]}, {"src": [9]}]}]  # 并集 {3,8,9}
        _picked = {3, 7, 9, 12}
        # 手算：(a′) 命中 {3,7,12} = 3/4 · (b) 命中 {3,9} = 2/4 · 差 1
        check("⭐ 两个口径都算对（原子 3/4 · 合成 2/4）",
              P.coverage_of(_picked, _atoms, _chaps)
              == {"k": 4, "hit_atom": 3, "hit_chapter": 2,
                  "a_atom": 0.75, "b_chapter": 0.5, "lost": 1},
              str(P.coverage_of(_picked, _atoms, _chaps)))
        # 改坏：把 `lost` 写成 `hc - ha` → 这条红。
        check("⭐⭐ `lost` = (a′) − (b)，**方向不能反**（原子抓到、合成丢了）",
              P.coverage_of(_picked, _atoms, [{"sentences": [{"src": [3]}]}])["lost"] == 2,
              str(P.coverage_of(_picked, _atoms, [{"sentences": [{"src": [3]}]}])))
        check("`src` 是 `None` / 缺键 -> 当空表，不抛",
              P.coverage_of(_picked, [{"src": None}, {}], [])["a_atom"] == 0.0)
        # ⚠️⚠️ 空集必须是 `None`，**不是 `0.0`** —— 这一轮真踩过：
        #    「组件没跑通」和「组件把东西全过滤掉了」读数完全一样（见 `prev-A5-broken/`）。
        _e = P.coverage_of(set(), _atoms, _chaps)
        check("⭐⭐ 一条都没挑到时返回 `None`，**不是 0.0**（fail-soft 假绿那条）",
              _e["a_atom"] is None and _e["b_chapter"] is None and _e["k"] == 0,
              str(_e))
        # ⚠️ 没 key 时**在读文件之前**就返回 —— 所以这里给个不存在的路径也无所谓
        #    （这也正是要钉的：功能关着时**一次 IO、一次网络都不该发生**）。
        check("没 key -> `measure_coverage` 返回 `None`（**不读文件、不联网**）",
              P.measure_coverage(pathlib.Path("/nonexistent/x.md"),
                                 {"atoms": [], "chapters": {}},
                                 token_value="") is None)

        print("\n--- ㉜ `--fail-window` 的口径（计划 T24）---")
        # ⚠️ 2026-09-30 实测：**这个参数从加进来那天起就没跑成过** ——
        #    `_secs()` 只吃 `"HH:MM:SS"`，而参数格式是 `"HH:MM"`（argparse 的 help
        #    和计划 D10 的例子都是这个形状）→ 一解析就 `ValueError`。
        #    ⚠️ 也就是说「参数实现了」和「这条路能跑」是两件事。
        check("⭐⭐ `_secs` 收 `HH:MM`（`--fail-window` 的形状）",
              P._secs("14:12") == 14 * 3600 + 12 * 60, str(P._secs("14:12")))
        check("⭐ `_secs` 仍收 `HH:MM:SS`（会话时间戳的形状，没变）",
              P._secs("14:12:30") == 14 * 3600 + 12 * 60 + 30, str(P._secs("14:12:30")))
        # 改坏：把 `_secs` 换回三段解包 -> 第一条红。
        _clock = {"now": 0.0}
        _cnt = {"atom": 0, "chap": 0, "in_chars": 0, "failed": 0}
        _dc = P.dry_chat(_cnt, lambda: _clock["now"], (100.0, 200.0, lambda: _clock["now"]))

        def _try():
            try:
                _dc(atom.SYS, "b", 10, 0.0)
                return False
            except RuntimeError:
                return True

        check("⭐ 调用时**钟在窗外** -> 不失败", not _try())
        _clock["now"] = 150.0
        check("⭐ 钟**进窗** -> 失败", _try())
        _clock["now"] = 250.0
        check("⭐⭐⭐ 判据看的是**调用那一刻**的钟（同一个 chat 对象，钟一挪就翻）"
              "—— 不是窗口里句子的时间戳", not _try())
        check("⭐ 失败单独计数（`failed` 不与正常调用混）",
              _cnt["failed"] == 1 and _cnt["atom"] == 2, str(_cnt))

        print("\n--- ㉝ ⭐⭐ 课务闸门的 token 必须和**端点同源** ---")
        # ⚠️⚠️ 2026-09-30 阶段 3 验收时实测栽的：`jev_deadline_gate` 取的是
        #    `keypoints.token()`（**CommandCode 代理**那条），而 endpoint / model
        #    由 `_provider()` 解析（**有官方 key 时走官方**）
        #    → 「官方端点 + 代理 token」= **401** → **闸门一条都不放行**。
        #    ⚠️ 它的表象是「模型报的课务 0 条」—— 和上一版 `q1` 那个 bug **一模一样**：
        #       「闸门坏了」看起来像「闸门把假阳性全拦住了」。
        #    ⚠️ 它只在**官方 key 落盘之后**才暴露（在那之前两端点一致、看不出来）。
        import keypoints as KP
        _seen: dict = {}
        _orig = (KP.ask_one, KP._provider, KP.token)

        def _spy(state, instruction, *, token_value="", timeout=30.0):
            _seen["tok"] = token_value
            return 0.9

        KP.ask_one = _spy
        KP._provider = lambda *a, **k: {"name": "typesafe", "endpoint": "https://官方.example",
                                        "model": "jev-1.13.0", "token": "官方-TOKEN"}
        KP.token = lambda *a, **k: "代理-TOKEN"
        try:
            L.jev_deadline_gate()("The essay is due Friday.")
            # 改坏：把 `provider_token()` 换回 `token()` -> 这条红。
            check("⭐⭐ 用的是**供应商**的 token（与端点同源），不是 `token()` 那条",
                  _seen.get("tok") == "官方-TOKEN", repr(_seen.get("tok")))
            _seen.clear()
            L.jev_deadline_gate(token_value="显式传的")("x")
            check("⭐ 显式传 `token_value` 优先（判据 / 探针要能注入）",
                  _seen.get("tok") == "显式传的", repr(_seen.get("tok")))
            _seen.clear()
            KP._provider = lambda *a, **k: {"name": "", "endpoint": "", "model": "",
                                            "token": ""}
            check("⭐ 一条 token 都没有 -> 返回 `None`（= 闸门**不存在**；"
                  "`_deadline_ok` 那边 fail-closed，一条不报）",
                  L.jev_deadline_gate() is None)
        finally:
            KP.ask_one, KP._provider, KP.token = _orig

        print("\n--- ㉞ ⭐⭐ 积压恢复：没有一章能盖住超过 `MAX_CHAPTER_S` 的**课堂内容** ---")
        # ⚠️⚠️ 复刻 2026-09-30 `--fail-window` 实测的形状：断网期间积压 N 个窗口，
        #    恢复那一刻被**一口气**消化 —— 它们**共享同一个 `now`**
        #    → 那一刻开的章 `t_open = now`，后面每个窗口算 `now - t_open` 都是 0
        #    → `MAX_CHAPTER_S = 600` **一次都不再触发** → **一章盖了 34.4 分钟的课**。
        # ⚠️ 那批真数据里 19 个积压窗口**主题完全没变** —— 所以「放行换题」那类
        #    修法救不了它，必须让**先后次序在时钟上体现出来**。
        #
        # ⚠️⚠️ 夹具必须**真实**：`now` 和句子时间戳**同步前进**（生产里就是这样 ——
        #    `now` 是 `time.monotonic()`，时间戳是墙钟，两者速率相同）。
        #    第一版夹具写成「now 每窗 +60s / 内容每窗 +360s」，两者速率不一致
        #    → 测出来的东西在生产里不可能发生（那条判据是**假的**）。
        with tempfile.TemporaryDirectory() as d:
            fail = {"on": False}
            emits2: list = []

            def chat2(sysp, block, max_tokens, temperature):
                if fail["on"]:
                    raise RuntimeError("offline")
                if sysp is atom.SYS:
                    return atom_reply("Topic A")     # ⭐ 全程**同一个主题**
                return dict(CHAP_REPLY)

            s2 = L.LiveSummarizer(
                chat=chat2, append_atoms=lambda a: len(a),
                chapter_path=ch.chapter_path_for(pathlib.Path(d) / "S.md"),
                emit=emits2.append)

            def _feed_close(gid, hhmmss, now):
                s2.feed((gid, hhmmss, "sentence %d" % gid, "中"))
                s2.step(now)
                s2.step(now + 40.0)              # 40s 静默 -> 关窗（ATOM_PAUSE_S=30）

            # 每窗相隔 **6 分钟课堂时间**，`now` 同步 +360 —— 一共 6 窗 = 30 分钟
            _feed_close(1, "10:00:00", 0.0)      # 正常开一章（t_open ≈ 40）
            fail["on"] = True                    # ---- 断网，窗口开始积压 ----
            for i in range(2, 7):
                _feed_close(i, "10:%02d:00" % ((i - 1) * 6), (i - 1) * 360.0)
            fail["on"] = False                   # ---- 恢复 ----
            # 时钟要推过 `_retry_at`（最后一次失败在 1840 → +`RETRY_S`30 = 1870）
            s2.step(1900.0)
            check("⭐ 前置：恢复那一步真的把积压**一口气**消化掉了",
                  s2.pending == 0, f"还剩 {s2.pending}")
            s2.finish(timeout_s=5.0)

            _fin = [c for c in chapters(emits2) if c["status"] == "final"]
            _ov = [(c["id"], c.get("t0"), c.get("t1"),
                    L._span_s(c.get("t0"), c.get("t1"))) for c in _fin]
            # ⚠️ **上界是 `MAX + 一窗`，不是 `MAX`** —— 强制切发生在**下一个窗口边界**，
            #    所以过冲最多一个窗口。这条**实测校准过**：真数据（`--fail-window`）
            #    修好后每章 617–662s（窗口间隔约 77s），而 `MAX + 一窗 = 677` ✅。
            #    ⚠️ 写成严格 `<= MAX` 会把**正确行为**判成失败（我没量的东西不许当判据）。
            _SLACK = 360.0                    # 夹具里一窗 = 6 分钟课堂跨度
            _bad = [x for x in _ov if x[3] > L.MAX_CHAPTER_S + _SLACK]
            # 改坏：`_process_pending` 里把 `now_eff` 换回 `now` -> 这条红
            #       （实测那时只有 2 章，其中一章跨度 1440s，远超 960 的上界）。
            check("⭐⭐ **没有一章的内容跨度超过 `MAX_CHAPTER_S` + 一窗**"
                  "（这才是 MIN/MAX 要守的不变量）",
                  _fin and not _bad,
                  f"{len(_fin)} 章 · 超限的 {_bad} · 全部 {_ov}")

        print("\n--- ㉟ T17 `overlay.fold_rows`：按字数算的假 `fits` ---")
        # ⚠️ 这是计划 §11.1 的 T17。`overlay` 的**纯函数**（不起窗口）——
        #    测量由调用方注入，所以判据里传个按字数算的假函数就够。
        import overlay as _ov
        _f = lambda s: len(s) <= 10                    # noqa: E731
        _sp = lambda w: w[:10] if w else ""            # noqa: E731
        check("空串 -> **空列表**（不是 `['']`，那会白多一个空行槽）",
              _ov.fold_rows("", _f, _sp) == []
              and _ov.fold_rows("   ", _f, _sp) == [])
        check("放得下就一行", _ov.fold_rows("hello", _f, _sp) == ["hello"])
        _long = _ov.fold_rows("alpha beta gamma delta epsilon", _f, _sp)
        # 改坏：去掉 `while rest and not fits(rest)` 那个循环（只切一次）-> 这条红。
        check("⭐⭐ **没有一行超出**（含超长单词那条路）",
              all(len(r) <= 10 for r in _long), str(_long))
        check("⭐ 超长单词被**切开**（`split` 的契约是返回一个最长前缀 → 要**循环**切）",
              _ov.fold_rows("x" * 25, _f, _sp) == ["x" * 10, "x" * 10, "x" * 5],
              str(_ov.fold_rows("x" * 25, _f, _sp)))
        check("⭐ 一个词都切不动（`split` 返回空串）-> **整词独占一行**，不丢字",
              _ov.fold_rows("abcd", lambda s: False, lambda w: "") == ["abcd"],
              str(_ov.fold_rows("abcd", lambda s: False, lambda w: "")))

        print("\n--- ㊱ T18 `overlay.build_outline_rows`：行序与排法 ---")
        _ol = {"deadlines": [{"t": "15:40:16", "quote": "Spend 20 minutes in the data lab",
                              "changed": False},
                             {"t": "15:53:16", "quote": "Make a graph", "changed": True}],
               "chapters": {
                   "0": {"id": 0, "status": "final", "title": "T1", "title_zh": "题一",
                         "t0": "15:05:27", "t1": "15:16:35", "lo": 1, "hi": 10,
                         "sentences": [{"en": "EN one", "zh": "中一",
                                        "terms": [["a", "甲"]], "src": [2], "flag": None}]},
                   "1": {"id": 1, "status": "interim", "title": "T2", "title_zh": "题二",
                         "t0": "15:16:43", "t1": "15:20:00", "lo": 11, "hi": 20,
                         "sentences": []}},
               "atoms": [{"text": "a-point", "src": [15], "terms": [["b", "乙"]]}],
               "windows": [{"topic": "进行中的题"}],
               "gaps": [{"t_from": "15:30:00", "t_to": "15:35:00"}], "marks": []}
        _fold = lambda t: _ov.fold_rows(t, _f, _sp)    # noqa: E731
        _R = _ov.build_outline_rows(_ol, None, "both", _fold)
        _big = [b for b, _ in _R]

        check("⭐⭐ 课务**置顶**，且**最新的在上**",
              _big[0].startswith("📌") and "Make a" in _big[0] and "Spend" in "".join(_big),
              str(_big[:4]))
        check("⭐⭐ 排法①：**中文当大字、英文降小字**（作者 2026-09-30 拍板）",
              "中一" in _big and any(s.startswith("EN one") for _, s in _R),
              str([r for r in _R if "中一" in r[0] or "EN one" in r[1]][:1]))
        # ⚠️ 2026-09-30 补：**章标题也要照排法①**（作者：「中文占比再提高一点」）——
        #    标题用英文、正文用中文会让整节**一上一下**。改坏：把 `_push` 的第一个参数
        #    换回 `c.get("title")` -> 这条红。
        check("⭐⭐ 章标题也是排法①（中文大字 / 英文降小字）",
              any(b.startswith("▍题一") for b, _ in _R)
              and any(s.startswith("T1 · ") for _, s in _R),
              str([r for r in _R if r[0].startswith("▍")][:1]))
        check("⭐ 已改期那条标了「已改期」", any("已改期" in s for _, s in _R))
        check("⭐ 临时章在小字里带「临时」", any("临时" in s for _, s in _R))
        check("⭐⭐ 没有合成句的章 -> **退回它的原子要点**（不是留空）",
              any(b.startswith("• a-point") for b, _ in _R), str(_big[-3:]))
        check("⭐ 离线空档渲染成一行", any("未生成：离线" in b for b, _ in _R))
        # ⭐⭐ 原子要点也吃 `zh`（2026-09-30 加）—— 与合成句/章标题同一条排法①。
        #    ⚠️ 改坏：把 `overlay.build_outline_rows` 里那个 `_pair` 的
        #       `zh or en` 换成 `en` -> 这条红。
        _ol2 = dict(_ol)
        _ol2["atoms"] = [{"text": "The supply curve slopes upward.",
                          "zh": "供给曲线向上倾斜。", "src": [15], "terms": []},
                         {"text": "Old atom without zh.", "src": [16], "terms": []}]
        # ⚠️ **夹具要放宽**：上面那个假 `fits` 只允许 10 字符，而
        #    `• 供给曲线向上倾斜。` 是 11 个 → 会被折成两行，判据假红。
        #    （第一版就是这么红的 —— 是**夹具**的问题，不是代码的。）
        _fold2 = lambda t: _ov.fold_rows(t, lambda x: len(x) <= 60, lambda w: w[:60])  # noqa: E731
        _R2 = _ov.build_outline_rows(_ol2, None, "both", _fold2)
        _bigs2 = [b for b, _ in _R2]
        check("⭐⭐ 原子大字吃 `zh`；**没有 `zh` 的旧数据退回英文**（不丢）",
              "• 供给曲线向上倾斜。" in _bigs2 and "• Old atom without zh." in _bigs2,
              str([b for b in _bigs2 if b.startswith("•")]))

        # ⭐⭐ 2026-09-30 作者实测反馈「**AI 总结现在只有中文，英文完全没有了**」——
        #    根因就在这里：原子**只把术语挂小字**，英文原句**没处放**。
        #    ⚠️ 旧判据只查了`大字`，英文不在大字上也算过 → **一直是假绿**。
        #    改坏：把 `_pair` 里 `_sub(en if zh else "", …)` 那一半去掉 -> 这条红。
        _small2 = [s for b, s in _R2 if b.startswith("• 供给曲线向上倾斜")]
        check("⭐⭐ 排法① 对**原子**也成立：英文原句必须**降小字**、不许消失",
              any("The supply curve slopes upward." in s for s in _small2),
              f"大字那行挂的小字={_small2!r}")

        # ⭐⭐ `en` 档：**原子的大字也要退回英文**（计划 §9.3 逐字）。
        #    ⚠️ 原来那个模块级 `_atom_big` **根本不看 mode** —— 它已经被删了，
        #       换成 `_pair`（排法① 的唯一定义点），`en` 档在那里抹空 `zh`。
        #    ⚠️ 旧判据的夹具里原子**没有 `zh`** →「该回英文却仍是中文」这条
        #       **一直是假绿**（2026-09-30 作者实测才暴露）。
        _en2 = _ov.build_outline_rows(_ol2, None, "en", _fold2)
        _enbig = [b for b, _ in _en2]
        check("⭐⭐ `en` 档：原子大字是**英文**（不是中文），且小字里一个中文都没有",
              "• The supply curve slopes upward." in _enbig
              and not any("供给曲线" in b for b in _enbig)
              and not any("供给曲线" in s for _, s in _en2),
              str(_enbig[-4:]))
        check("⭐ 没有 `current_*` 字段也推得出「进行中」—— 推出来是空的就不打标题",
              all("进行中" not in b for b, _ in _R),
              "本夹具的原子全被已合成章覆盖了")
        check("⭐⭐ `raw` 档 -> **空列表**",
              _ov.build_outline_rows(_ol, None, "raw", _fold) == [])
        _en = _ov.build_outline_rows(_ol, None, "en", _fold)
        # 改坏：`_terms` 里去掉 `if only_en: return ""` -> 这条红。
        check("⭐⭐ `en` 档：**所有中文小字留空**（大字也退回英文）",
              all("甲" not in s and "乙" not in s and "题一" not in s for _, s in _en)
              and any(b == "EN one" for b, _ in _en),
              str([r for r in _en if "EN" in r[0]][:1]))
        # ⚠️ 分隔线：插在**第一条时刻晚于 `seen_t`** 的行**之前** ——
        #    夹具里第一条有时刻的行是课务 `15:53:16` > `15:15:00` → 落在 index 0。
        _d = _ov.build_outline_rows(_ol, "15:15:00", "both", _fold)
        check("⭐⭐ `seen_t` 的分隔线插在第一条更晚的行**之前**",
              _d[0][0] == "— 上次看到这里 —", str(_d[:2]))
        check("⭐ 分隔线的位置**不从渲染文字里反解**（标题行的 `t0–t1` 是带破折号的串）",
              _ov.build_outline_rows(_ol, "16:00:00", "both", _fold)[0][0] != "— 上次看到这里 —",
              "16:00 晚于所有时刻 -> 不该插")
        check("⭐ `seen_t=None` -> **不插**分隔线",
              not any("上次看到这里" in b for b, _ in _R))

        print("\n--- ㊲ ⭐ 空状态 / 占位入口的**用户指引**（作者 2026-09-30 追加）---")
        # ⚠️ 这一组钉的是**文案本身** —— 它不是"好不好看"，是**唯一**告诉用户
        #    「这东西叫什么、在哪、能不能点」的地方。
        #    来由：作者实测反馈「我进去没有看到面板」→ 加了入口之后他追加要求
        #    「第一次打开还没有总结的时候显示 AI 实时总结点这里，
        #      直到有总结信息再替换掉那行占位」。
        # ⚠️ 变异验证：任一句改回旧的（`▸ 实时总结` / `▍ 还在记 · 等第一段内容`）→ 对应那条红。

        class _Bare:
            """只带 `_chapter_bar_text` 真正读的**那两个**字段。

            ⚠️ 故意用鸭子类型的假 self（不建窗口）—— 它多读一个字段就会
               `AttributeError`，那正是我们想知道的：**这个方法长胖了**。
            """
            _outline_new = False
            _outline = {"chapters": {}, "windows": [], "gaps": [], "marks": [],
                        "deadlines": [], "atoms": []}

        _bare = _ov.Overlay._chapter_bar_text(_Bare())
        check("⭐⭐ 章节条空档要**写清功能名 + 可点**（光写「实时总结」看不出它可点）",
              "AI 实时总结" in _bare and "点这里" in _bare, repr(_bare))

        class _Live(_Bare):
            _outline = {"chapters": {"0": {"id": 0, "status": "final", "title": "T1",
                                           "title_zh": "题一", "t0": "15:05:27"}},
                        "windows": [], "gaps": [], "marks": [],
                        "deadlines": [], "atoms": []}

        _live = _ov.Overlay._chapter_bar_text(_Live())
        check("⭐ 有章节之后那句占位**被替换掉**（作者原话：「直到有总结信息再替换掉」）",
              "点这里" not in _live and "T1" in _live, repr(_live))

        # ⚠️ 同一个假 `fits` 的坑：夹具放宽到 60 字符，否则空状态那行会被折成两行
        _fold3 = lambda t: _ov.fold_rows(t, lambda x: len(x) <= 60, lambda w: w[:60])  # noqa: E731
        _empty = [b for b, _ in _ov.build_outline_rows(
            {"chapters": {}, "windows": [], "gaps": [], "marks": [],
             "deadlines": [], "atoms": []}, None, "both", _fold3)]
        check("⭐⭐ 面板空状态要说「**内容会自动出现在这里**」（不是「什么都没有」）",
              any("AI 实时总结" in b and "会自动出现" in b for b in _empty), str(_empty))
        # ⭐ 还没出章节、但已经有窗口 -> **也要带功能名**（占位要一直说到有总结为止）
        _mid = [b for b, _ in _ov.build_outline_rows(
            {"chapters": {}, "windows": [{"w": 1, "hi": 42, "topic": ""}],
             "gaps": [], "marks": [], "deadlines": [], "atoms": []}, None, "both", _fold3)]
        check("⭐ 有窗口没章节时也带功能名 + 进度（别只剩一句「已 42 句」）",
              any("AI 实时总结" in b and "42" in b for b in _mid), str(_mid))
        # ⚠️ **离线那一支 2026-09-30 已删（死代码）** —— 本组第一版断言的就是它，
        #    结果**红的**：第 ② 步对每个 gap 都 `_push` 一行，`out` 必然非空
        #    → `if not out:` 那整块进不去。计划 §9.11 想要的那句话**本来就送达到了**，
        #    而且送得更好（带具体时间段、且有章节时也在）。这里改成钉**实际行为**。
        _off = [b for b, _ in _ov.build_outline_rows(
            {"chapters": {}, "windows": [], "gaps": [{"state": "pending",
                                                      "t_from": "15:30", "t_to": "15:35"}],
             "marks": [], "deadlines": [], "atoms": []}, None, "both", _fold3)]
        check("⭐ 离线时**正文里有一行带时间段的空档**（这就是计划要的那句话）",
              any("未生成：离线" in b and "15:30–15:35" in b for b in _off), str(_off))
        check("⚠️ 而**不是**退化成整屏只有一句功能名（那等于没说清为什么没有）",
              not any("会自动出现" in b for b in _off), str(_off))

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
