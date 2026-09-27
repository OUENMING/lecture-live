#!/usr/bin/env python3
"""课程卡片面板：**纯函数那半**（不碰 AppKit 也能钉住的判据）。

    ClassLive.app/Contents/MacOS/python tests/test_entry_panel.py

## 为什么只测这半

面板本体是 AppKit 装配，`tests/test_panel.py` 已经在管「配方没被我改坏」。
但卡片上**显示什么字**是纯逻辑，而且是最容易悄悄错的地方 —— 它们全部抽成了
模块级函数，在这里逐条钉住：

1. ⭐ **「未知」与「零」必须显示成两样**（`— 条术语` vs `0 条术语`）。
   把读失败显示成 0 = 跟用户谎称「这门课一个词都没有」。
   ⭐ **判据形状：同一个函数、只换 `None` 和 `0`、断言输出不同** ——
   如果只测了 `None` 那一支，改成 `0` 也能过（那就是假测试）。
2. ⚠️ **课号不许显示两遍**（术语表首行本来就带课号）。跑第一遍时真发生过。
3. **不认识的 stage 也要显示点什么** —— 显示空白会让人以为卡住了。
4. **body 高度**：装得下就长，装不下才滚（第 N+1 门课不许出现断崖）。

⚠️ 本文件**不起事件循环、不建窗口** —— 纯 import + 调用。
   （`entry_panel` 的 AppKit 全在函数内 import，所以能这么测。）
"""
from __future__ import annotations

import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import courses                                                     # noqa: E402
import entry_panel as E                                            # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  {detail}" if detail else ""))


def R(course="ECON10740", title="Exploring Economics", terms=36,
      materials=0, auto=0, last="2026-09-22"):
    return courses.Readiness(course, title, terms, materials, auto, last)


class FakeRes:
    def __init__(self, **kw):
        self.aborted = kw.get("aborted", False)
        self.added = kw.get("added", [])
        self.not_added = kw.get("not_added", [])
        self.skipped_existing = kw.get("skipped_existing", 0)
        self.notes_added = kw.get("notes_added", None)


def main() -> int:
    print("\n--- ① 课号不许显示两遍 ---")
    check("首行带课号 -> 不再加前缀",
          E.card_title(R(title="ECON10740 Exploring Economics"))
          == "ECON10740 Exploring Economics",
          E.card_title(R(title="ECON10740 Exploring Economics")))
    check("首行不带课号 -> 补上前缀",
          E.card_title(R(title="Advanced Topics")) == "ECON10740   Advanced Topics")
    check("课号是**别门课**的前缀时也不误判",
          E.card_title(R(course="ECON1074", title="ECON10740 X")) == "ECON1074   ECON10740 X")
    check("标题空 -> 只显示课号", E.card_title(R(title="")) == "ECON10740")

    print("\n--- ② ⭐「未知」与「零」必须显示成两样 ---")
    zero = E.readiness_line(R(terms=0, materials=0))
    unknown = E.readiness_line(R(terms=None, materials=None))
    check("零 -> 写 0", "0 条术语" in zero and "0 份课件" in zero, zero)
    check("⭐ 未知 -> 写 —，不写 0", "— 条术语" in unknown and "— 份课件" in unknown,
          unknown)
    check("⭐ 两者输出**必须不同**（只测一支 = 假测试）", zero != unknown)
    check("零里不许出现 —", "—" not in zero, zero)

    print("\n--- ③ 日期 ---")
    check("2026-09-22 -> 9/22", E._short_date("2026-09-22") == "9/22")
    check("None -> 从没上过", E._short_date(None) == "从没上过")
    check("形状不对 -> 原样返回，不炸", E._short_date("garbage") == "garbage")
    check("readiness_line 里也生效", "上次上课 9/22" in E.readiness_line(R()))

    print("\n--- ④ 进度文案 ---")
    check("认识的 stage -> 中文名", E.progress_text("extract", 3, 7) == "抽文本 3/7…")
    check("total=0 -> 不带分母", E.progress_text("notes", 0, 0) == "生成释义…")
    check("⚠️ 不认识的 stage -> 显示原名，**不留空白**",
          E.progress_text("weird", 1, 2) == "weird 1/2…")

    print("\n--- ⑤ body 高度：装得下就长，装不下才滚 ---")
    natural5 = 5 * (E.CARD_H + E.CARD_GAP) - E.CARD_GAP
    check("5 门课 / 982 屏 -> 恰好装下（不留半张卡）",
          E.body_height(5, 982) == natural5, str(E.body_height(5, 982)))
    check("1 门课 -> 一张卡的高度", E.body_height(1, 982) == E.CARD_H)
    big = E.body_height(30, 982)
    check("30 门课 -> 封顶到屏幕的一部分（开始滚）",
          0 < big < 30 * (E.CARD_H + E.CARD_GAP), str(big))
    check("小屏上也不超过屏幕比例", E.body_height(30, 600) <= 600 * 0.61,
          str(E.body_height(30, 600)))
    check("⭐ 不出现「第 N+1 门课断崖」：5→6 是连续的",
          E.body_height(6, 982) - E.body_height(5, 982) > 0
          or E.body_height(6, 982) >= 982 * 0.60 - 1,
          f"{E.body_height(5, 982)} -> {E.body_height(6, 982)}")

    print("\n--- ⑥ 结果汇总：计数必须与实际相符 ---")
    check("全空 -> 只报加了 0 个",
          E.summarize(FakeRes()) == "加了 0 个词", E.summarize(FakeRes()))
    s = E.summarize(FakeRes(added=list("abc"), not_added=list("de"), notes_added=3))
    check("加了 3 / 没加 2 / 释义 3 三项都在",
          "加了 3" in s and "另有 2" in s and "释义 +3" in s, s)
    check("被中止 -> 单独报「没跑完」",
          E.summarize(FakeRes(added=list("a"), aborted="no_api_key")).startswith("没跑完"),
          E.summarize(FakeRes(added=list("a"), aborted="no_api_key")))
    check("计数为 0 的项不出现（别塞一堆 0）",
          "另有 0" not in E.summarize(FakeRes(added=list("a"))))

    print("\n--- ⑦ 结果列表：计数与列表**必须同源** ---")
    e = {"added": ["a", "b", "c"], "removed": set(), "not_added": [], "failed": []}
    check("没删过 -> 三条都在", E.kept_added(e) == ["a", "b", "c"], str(E.kept_added(e)))
    e["removed"] = {"b"}
    check("删过的**不再列**（列表跟着走）", E.kept_added(e) == ["a", "c"],
          str(E.kept_added(e)))
    check("⭐ 头部计数 == 列表长度（**同一个来源**）",
          f"本次加了 {len(E.kept_added(e))} 个" in E.result_header(e), E.result_header(e))
    # ⭐ 这一条是防 Anki / LingQ 那两次事故的：
    #    「18 added, 148 updated」和「says 1 new word but it does not」都是**计数撒谎**，
    #    而根因是计数和列表各算一遍。这里把它们逼成同一个来源，逐个规模都验一遍。
    check("⭐ 对每个删除规模，计数都与列表对得上（各算一遍必在这里红）",
          all(f"本次加了 {len(E.kept_added({'added': ['a', 'b', 'c', 'd'],
                                           'removed': set(list('abcd')[:k])}))} 个"
              in E.result_header({'added': ['a', 'b', 'c', 'd'],
                                  'removed': set(list('abcd')[:k])})
              for k in range(5)))
    check("删光了 -> 加了 0 个，且列表是空的",
          E.kept_added({"added": ["a"], "removed": {"a"}}) == []
          and "本次加了 0 个" in E.result_header({"added": ["a"], "removed": {"a"}}))
    check("「没加」只在非空时才出现",
          "没加" not in E.result_header({"added": ["a"]})
          and "没加" in E.result_header({"added": ["a"], "not_added": ["x"]}))
    check("失败项单独报（逐文件那条的落点）",
          "1 个文件失败" in E.result_header({"added": [], "failed": [("f", "why")]}),
          E.result_header({"added": [], "failed": [("f", "why")]}))
    # ⚠️ 原来这里是两条**恒真**的断言（OCR 指出）：一条 `check(..., True)`，
    #    一条断言的是 Python 字面量 `{"a","a"}` 的去重语义 —— **完全没碰被测代码**。
    #    改成真的调用 + 断言返回值，以及走真实路径验「删过的词只算一次」。
    _out, _err = [], ""
    for junk in ({}, None, {"added": None, "removed": None}):
        try:
            _out.append(E.result_header(junk))
        except Exception as e:                            # noqa: BLE001
            _err = f"{type(e).__name__}: {e}"
            break
    check("空/None entry 不抛，且返回的是**可显示的一行**",
          not _err and len(_out) == 3 and all(isinstance(x, str) and x for x in _out),
          _err or str(_out))
    _dup = {"added": ["a", "b"], "removed": ["a", "a"]}   # 重复项：不是 set
    check("⚠️ `removed` 传成**含重复项的 list** 也照样对（走真实路径）",
          E.kept_added(_dup) == ["b"] and "本次加了 1 个" in E.result_header(_dup),
          f"{E.kept_added(_dup)} / {E.result_header(_dup)}")

    print("\n--- ⑧ 卡片高度：**与坐标同一组常数推导** ---")
    check("⭐ card_height(None) == CARD_H —— 这条把推导锁住",
          E.card_height(None) == E.CARD_H,
          f"{E.card_height(None)} vs {E.CARD_H}")
    check("有结果 -> 变高",
          E.card_height({"added": ["a"], "removed": set()}) > E.CARD_H)
    h1 = E.card_height({"added": ["a"], "removed": set()})
    h2 = E.card_height({"added": ["a", "b"], "removed": set()})
    check("每多一个词 -> 恰好高一行（RESULT_ROW）",
          abs((h2 - h1) - E.RESULT_ROW) < 1e-9, f"{h1} -> {h2}")
    check("删掉一条 -> 卡片跟着缩（删光了也不留空行）",
          E.card_height({"added": ["a", "b"], "removed": {"b"}}) < h2)
    check("有「撤销」那一行 -> 再高一行",
          abs(E.card_height({"added": ["a"], "removed": {"a"},
                             "undo": {"text": "a"}})
              - E.card_height({"added": ["a"], "removed": {"a"}}) - E.RESULT_ROW) < 1e-9)

    print("\n--- ⑨ 零参数入口的退出码（`cl` 只读得动这个）---")
    # ⚠️ 这一组的判据是**三条退出码必须互不相同**：「用户取消」和「面板挂了」
    #    混在一起的话，要么违背用户意图（他取消了还录课），要么录不了课
    #    （正是作者拍第 1 条时要防的那件事）。
    #
    # ⚠️ 它会写**真的 `.course`**（`entry_launch.CFG`），所以按仓库「测试必须隔离写端」
    #    的规矩**先备份、跑完逐字还原**。同 `test_panel.py` 对 `.window` 的做法。
    import entry_launch
    import entry_panel as _EP

    # ⚠️ **路径从写端常量派生**，不要自己拼 `HERE / ".course"`（OCR 指出）：
    #    两处一旦漂移（比如 CFG 将来跟着 state_root 走），`finally` 里的还原会
    #    **静默指向错路径**，而末尾那条「逐字还原」比的正是同一个错路径 → 照样通过，
    #    真 `.course` 却留着测试值。
    cfg = entry_launch.CFG
    saved = cfg.read_bytes() if cfg.exists() else None
    _orig_open = _EP.open_panel
    try:
        def _run(fake):
            _EP.open_panel = fake
            return entry_launch.main()

        rc = _run(lambda **kw: (kw["on_start"]("ZZTEST"), object())[1])
        check("① 点了「开始上课」-> 0，且 .course 写成那门课",
              rc == entry_launch.PICKED and cfg.read_text(encoding="utf-8") == "ZZTEST",
              f"rc={rc} course={cfg.read_text(encoding='utf-8')!r}")

        cfg.write_text("KEEPME", encoding="utf-8")
        rc = _run(lambda **kw: (kw["on_close"](), object())[1])
        check("⭐ ② 用户关掉面板 -> 1，且 **.course 一个字没动**（取消 ≠ 故障）",
              rc == entry_launch.CANCELLED
              and cfg.read_text(encoding="utf-8") == "KEEPME",
              f"rc={rc} course={cfg.read_text(encoding='utf-8')!r}")

        rc = _run(lambda **kw: None)
        check("⭐ ③ 面板起不来 -> 2（`cl` 据此**退回照旧立刻录课**）",
              rc == entry_launch.UNAVAILABLE, f"rc={rc}")

        check("⭐ 三条码互不相同（`cl` 的 case 分支靠这个分清）",
              len({entry_launch.PICKED, entry_launch.CANCELLED,
                   entry_launch.UNAVAILABLE, entry_launch.NO_COURSES}) == 4)
    finally:
        _EP.open_panel = _orig_open
        if saved is not None:
            cfg.write_bytes(saved)
        elif cfg.exists():
            cfg.unlink()
    check("⚠️ 跑完 .course 逐字还原（隔离真的成立）",
          (cfg.read_bytes() if cfg.exists() else None) == saved)

    bad = [n for n, ok, _ in RESULTS if not ok]
    print("\n" + "=" * 60)
    print(f"{len(RESULTS) - len(bad)}/{len(RESULTS)} 通过")
    for n in bad:
        print(f"  ❌ {n}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
