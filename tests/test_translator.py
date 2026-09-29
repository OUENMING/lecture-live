#!/usr/bin/env python3
"""`select_terms` 与术语池的判据 —— **全离线**，不加载模型、不发请求。

    ClassLive.app/Contents/MacOS/python tests/test_translator.py

## 钉住什么

1. ⭐⭐ **`extra_terms` 只进「候选池」，不进「每句全量注入」那一档** ——
   这是 2026-09-29 那条改动的**核心不变量**。三档分别是：
     · `core`   常驻
     · `always` 课程术语，**每句全量注入**（`translator.course_term_list`）
     · 池子     按相似度**动态召回** `[:max_dyn]`
   把值钱的词放错档 = 每句的 prompt 成本随词数线性涨（实测 120 条时占 60%）。
2. ⭐ **相似度不到 0.6 的词不进** —— 这条是「提名额不会拉进弱相关词」的依据。
3. `max_dyn` 真的截断；`core` / `always` **无论句子**都在。

⚠️ 本文件此前**一个判据都没有**（`select_terms` / `MAX_DYNAMIC_TERMS` 全仓零测试）。
"""
from __future__ import annotations

import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import translator as T                                               # noqa: E402

CASES: list[tuple[str, object]] = []


def case(name: str):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


def _lines(out: str) -> list:
    return out.split("\n") if out else []


# ---------------------------------------------------------------- 三档语义
@case("⭐ `core` / `always` **无论句子**都在（它们是常驻档）")
def t_core_and_always_always_injected():
    got = _lines(T.select_terms("hello everyone welcome back",
                                [], core=["CC1"], always=["AA1"]))
    assert "CC1" in got and "AA1" in got, f"常驻档没注入：{got}"


@case("⭐⭐ `extra_terms` 进**池子**、不进 `always`")
def t_extra_terms_go_to_pool_not_always():
    """本次改动的**核心不变量**。

    `extra_terms`（转录里挖的词）必须走**动态召回**那一档 —— 那样成本是常数；
    一旦混进 `always`，它就变成**每句全量注入**，成本随词数线性涨。
    """
    w = T.Translator("m", None, course=None, extra_terms=["monopoly", "oligopoly"])
    assert "monopoly" in w._terms, "extra_terms 没进池子"
    assert "monopoly" not in w._course_terms, (
        "extra_terms 混进了 `_course_terms` —— 那档是**每句全量注入**的，"
        "成本会随词数线性涨（实测 120 条时占整条 prompt 60%）")


@case("⭐⭐ 无关句子里，池子里的词**不出现**（动态召回而不是常驻）")
def t_pool_words_not_injected_when_irrelevant():
    pool = ["efficiency", "equity", "monopoly", "oligopoly"]
    got = _lines(T.select_terms("hello everyone welcome back to class",
                                pool, core=[], always=["efficiency", "equity"]))
    assert "efficiency" in got and "equity" in got, f"常驻档丢了：{got}"
    assert "monopoly" not in got and "oligopoly" not in got, (
        f"无关句子里池子词也被注入了 —— 那它就不是动态召回：{got}")


# ---------------------------------------------------------------- 阈值与截断
@case("⭐ 相似度 < 0.6 的词**不进**（陌生长词不该被召回）")
def t_below_threshold_not_recalled():
    # ⚠️ 无匹配时返回的是标记串 `(无特定术语)`，**不是空串** ——
    #    第一版断言 `== []`，判据自己红的（实现没错）。
    got = T.select_terms("the quick brown fox jumps", ["zzzzqqqqwwww"],
                         core=[], always=[])
    assert got == "(无特定术语)", f"完全不像的词被召回了：{got!r}"


@case("⭐ `max_dyn` 真的截断（喂一堆高相似词，只出 max_dyn 个）")
def t_max_dyn_truncates():
    words = ["marginal", "marginalx", "marginaly", "marginalz", "marginalw"]
    for k in (1, 2, 3):
        got = [t for t in _lines(T.select_terms("the marginal utility",
                                                words, core=[], always=[],
                                                max_dyn=k)) if t != "(无特定术语)"]
        assert len(got) <= k, f"max_dyn={k} 却注入了 {len(got)} 个：{got}"


@case("⚠️ 池子空 / 无匹配 -> 给 `(无特定术语)`，不返回空串")
def t_empty_marker():
    assert T.select_terms("hello", [], core=[], always=[]) == "(无特定术语)"
    assert T.select_terms("hello", [], core=["CC1"], always=[]) == "CC1"


@case("⭐ 音近**能**召回一部分（但**大部分接不住** —— 这条是限制，不是保证）")
def t_fuzzy_recall_is_partial():
    """⚠️⚠️ **实测边界**（2026-09-29 量的，逐词 `difflib`）：docstring 自己举的
    4 个例子里**只有 2 个**过得了 0.6：

        lattice/lacker 0.62 ✅   nation/relation 0.71 ✅
        macroscopic/max chocolate **0.40 ❌**   statistics/stalcy **0.50 ❌**

    ⚠️ **这正解释了为什么课程术语是「全量注入」**（`select_terms` 的 docstring：
       纯按匹配筛选会**死循环**）—— 听错的词**根本匹配不上表**。
    → 所以**池子接不住大部分听错**；这条判据钉的就是"能接住一部分"，
      别读成"池子能兜住听错"。
    """
    got = _lines(T.select_terms("average on the lacker level",
                                ["lattice"], core=[], always=[]))
    assert any("lattice" in g for g in got), f"0.62 该过 0.6，却没召回：{got}"
    # 反面：**接不住的**要如实接不住（不许为了"好看"去调低阈值）
    miss = T.select_terms("we're max chocolate properties",
                          ["macroscopic"], core=[], always=[])
    assert miss == "(无特定术语)", (
        f"macroscopic/max chocolate 实测只有 0.40，不该被召回：{miss!r}\n"
        f"  ⚠️ 它靠的是**全量注入**那一档（课程术语），不是匹配。")


@case("⚠️ `_load_terms` 跳注释/跳空行；**去重在 `load_terms` 那一层**")
def t_load_terms_dedup():
    """⚠️ 第一版把这条写在 `_load_terms` 上 —— 而**去重不在那儿**（实测
    `_load_terms` 原样返回 `['Alpha','alpha','Beta']`）。去重是 `load_terms`
    做的（大小写不敏感、保序）。判据要指到**那个函数**上。
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "g.txt"
        p.write_text("# 注释\nAlpha\nalpha\nBeta\n\n", encoding="utf-8")
        raw = T._load_terms(str(p))
        assert raw == ["Alpha", "alpha", "Beta"], f"它只该跳注释与空行：{raw}"
        dedup = T.load_terms(str(p))          # 公共表那条路走 load_terms
        assert dedup == ["Alpha", "Beta"], f"去重保序有问题：{dedup}"


def main_() -> int:
    print("=" * 60)
    fail: list[str] = []
    for name, fn in CASES:
        try:
            fn()
        except AssertionError as e:
            print(f"  ❌ {name}\n      {str(e)[:220]}")
            fail.append(name)
        except Exception as e:                                 # noqa: BLE001
            print(f"  ❌ {name}\n      {type(e).__name__}: {str(e)[:200]}")
            fail.append(name)
        else:
            print(f"  ✅ {name}")
    print("=" * 60)
    print(f"{len(CASES) - len(fail)}/{len(CASES)} 通过")
    for n in fail:
        print(f"  ❌ {n}")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main_())
