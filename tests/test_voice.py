#!/usr/bin/env python3
"""`voice.py` 的判据 —— 目前只钉**档案读写**那一条。

    ClassLive.app/Contents/MacOS/python tests/test_voice.py

## 这个文件钉住了什么

⭐⭐ **「读不出」绝不能被报成「里面什么都没有」。**

`load_store` 原来写着「读不出就给空表（**同 `load_state` 的理由**）」——
而 `load_state` 的理由是「它是**可重建的计数**」，**那个理由搬不过来**：
`profiles.json` 是**唯一真源**（`sherpa-onnx` 在 Python 侧读不回 embedding，
官方 PR #3950 作者逐字）。混起来的后果是**静默全损**：

    load_store() 读到坏文件 → {} → add_sample 加一条 →
    save_store({新课: [一条]}) → **把原文件整个覆盖掉**，其余全没了

⚠️ 与 `entry_panel._is_editing` 把 `overlay` 那套的**倒向**抄反、以及
   `classify` 那次，是**同一个形状**：「同 X 的理由」被搬到了 X 的理由不成立的地方。
⚠️ 与 `minutes` 那条「**截断必须说出来**」也是同族 ——
   负结果不许读起来像穷尽。（2026-09-28 架构评估时查出。）
"""
from __future__ import annotations

import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import voice                                                       # noqa: E402

CASES: list[tuple[str, object]] = []
FAIL: list[str] = []


def case(name: str):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


@case("全新安装：文件不存在 → 空表（不是错）")
def t_missing_is_empty():
    with tempfile.TemporaryDirectory() as d:
        assert voice.load_store(pathlib.Path(d) / "nope.json") == {}


@case("⭐ 正常往返：存进去读得回来")
def t_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "profiles.json"
        store = {"ECON10740": [[0.1, 0.2, 0.3]]}
        voice.save_store(p, store)
        assert voice.load_store(p) == store


@case("⭐⭐ 坏文件 → 抛 ValueError，**绝不**返回空表")
def t_corrupt_raises():
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "profiles.json"
        p.write_text("{这不是 JSON", encoding="utf-8")
        try:
            got = voice.load_store(p)
        except ValueError:
            return                                     # ✅ 正是要的行为
        raise AssertionError(
            f"坏文件返回了 {got!r} —— 调用方会把它当「没有档案」，"
            f"下一次 save_store 就**把真数据整个覆盖掉**")


@case("⭐⭐ 形状不对的条目 → 抛，**绝不**静默丢掉（丢了会被 save_store 永久擦掉）")
def t_malformed_entry_raises():
    """2026-09-29 修。原来形状不对的条目只是 `continue` 掉，于是
    `{"A": [[…]], "B": "oops"}` 读回来只剩 A，随后 `save_store` 把 B
    **永久擦掉**，而且没有任何信号 —— 正是本模块 docstring 反复警告的
    「负结果不许读起来像穷尽」/「静默全损」，与上面那条**同一层次**。"""
    with tempfile.TemporaryDirectory() as d:
        from store import save_json
        for label, payload in (
            ("值不是列表", {"A": [[1.0, 2.0]], "B": "oops"}),
            ("元素不是向量", {"A": [[1.0, 2.0]], "B": [1.0, 2.0]}),
            ("向量里混了非数", {"A": [[1.0, 2.0]], "B": [[1.0], ["oops"]]}),
            ("空列表（形状上算坏）", {"A": [[1.0, 2.0]], "B": []}),
        ):
            p = pathlib.Path(d) / f"{abs(hash(label))}.json"
            save_json(p, payload)
            try:
                got = voice.load_store(p)
            except ValueError:
                continue                               # ✅ 正是要的行为
            raise AssertionError(
                f"[{label}] 静默丢了坏条目、返回 {got!r} —— "
                f"调用方 save_store 会把丢掉的那条**永久擦掉**")


@case("⚠️ 但「文件不存在」**不许**抛 —— 全新安装是真的什么都没有")
def t_missing_is_not_an_error():
    with tempfile.TemporaryDirectory() as d:
        assert voice.load_store(pathlib.Path(d) / "nope.json") == {}, \
            "文件不存在该给空表；它是全新安装，不是坏数据"


@case("⭐⭐ 变异验证：让坏文件也返回空表 → 上面那条必须红")
def t_mutation():
    """⚠️ 第一版这条是**空的**：它只定义了一个 mutant 函数就直接断言 `got != {}`，
    根本没拿 mutant 去跑上面那条判据 —— 于是它测的是自己的局部变量。
    改成「换上变异实现，跑真判据，断言它**红了**」。"""
    orig = voice.load_store

    def mutant(path):
        try:
            return orig(path)
        except ValueError:
            return {}                                  # 变异：把坏文件吞成空表
    voice.load_store = mutant
    try:
        caught = False
        try:
            t_corrupt_raises()                         # 它内部读的是 `voice.load_store`
        except AssertionError:
            caught = True
    finally:
        voice.load_store = orig
    assert caught, "变异没被抓住 —— 上面那条判据没有区分能力"


@case("顶层不是 dict（比如是个 list）也要抛，不能当空表")
def t_wrong_shape_raises():
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "profiles.json"
        p.write_text("[1, 2, 3]", encoding="utf-8")
        try:
            voice.load_store(p)
        except ValueError:
            return
        raise AssertionError("顶层不是 dict 却当成了空档案")


def main_() -> int:
    print("=" * 60)
    for name, fn in CASES:
        try:
            fn()
        except AssertionError as e:
            print(f"  ❌ {name}\n      {str(e)[:220]}")
            FAIL.append(name)
        except Exception as e:                                 # noqa: BLE001
            print(f"  ❌ {name}\n      {type(e).__name__}: {str(e)[:200]}")
            FAIL.append(name)
        else:
            print(f"  ✅ {name}")
    print("=" * 60)
    print(f"{len(CASES) - len(FAIL)}/{len(CASES)} 通过")
    for n in FAIL:
        print(f"  ❌ {n}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main_())
