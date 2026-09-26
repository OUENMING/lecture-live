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

    bad = [n for n, ok, _ in RESULTS if not ok]
    print("\n" + "=" * 60)
    print(f"{len(RESULTS) - len(bad)}/{len(RESULTS)} 通过")
    for n in bad:
        print(f"  ❌ {n}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
