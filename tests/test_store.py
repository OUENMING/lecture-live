#!/usr/bin/env python3
"""`store.py` 的判据 —— `~/.classlive/` 那族状态文件的版本约定。

    ClassLive.app/Contents/MacOS/python tests/test_store.py

## 这个文件钉住了什么

1. ⭐⭐ **「读不懂的版本」不许被当成「空的」** —— 那会让下一次写入
   **把真内容整个覆盖掉**（`profiles.json` 那次就是这条，见 `tests/test_voice.py`）。
2. ⭐ **没有版本号的老文件按 v1 认** —— 否则本约定一上线，用户已有的文件
   一夜之间全变「不认识」，等于把数据判死刑。
3. **写出去的一定带版本号** —— 否则第 1 条永远触发不到（读的时候没得判）。

⚠️ 语料全是 `tempfile`，不碰真实的 `~/.classlive/`。
⚠️ 每条都问过「把实现改坏它会不会红」—— 第 1 组**自带变异验证**。
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import store                                                       # noqa: E402

CASES: list[tuple[str, object]] = []
FAIL: list[str] = []


def case(name: str):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


@case("写出去的一定带版本号")
def t_writes_version():
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "s.json"
        store.save_json(p, {"a": 1})
        raw = json.loads(p.read_text(encoding="utf-8"))
        assert raw.get(store.K) == store.V, f"没盖版本号：{raw}"


@case("往返：存进去读得回来")
def t_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "s.json"
        store.save_json(p, {"a": 1, "b": [1, 2]})
        got = store.load_json(p)
        assert got == {"a": 1, "b": [1, 2]}, f"读回来多了/少了东西：{got}"


@case("文件不存在 → 给 default（全新安装，不是错）")
def t_missing():
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "nope.json"
        assert store.load_json(p, default={"x": 1}) == {"x": 1}
        assert store.load_json(p) == {}


@case("⭐⭐ 未来的版本 → 抛，**绝不**按老格式去解释它")
def t_future_version():
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "s.json"
        p.write_text(json.dumps({"_v": store.V + 1, "a": 1}), encoding="utf-8")
        try:
            got = store.load_json(p)
        except store.StoreError:
            return
        raise AssertionError(
            f"未来版本被当成普通文件读成了 {got!r} —— 按老格式解释新数据只会解释错")


@case("⭐⭐ 坏版本号的其他形态也一律抛（bool / 类型错 / 0 / 负数）")
def t_bad_version_forms():
    """⚠️ 只钉「未来版本」一种是不够的（2026-09-28 审查指出）：

    `store.load_json` 里那三条是**分开挡**的，每条都对应一个具体的坑：
      · `_v: true` —— `bool` 是 `int` 的子类、`True == 1`，光判 `> V` 会**静默当成 v1**；
      · `_v: "1"` —— 类型错，与「版本更新」是两件事（报错文案不该共用）；
      · `_v: 0 / -1` —— 根本不是版本号。
    退化成「只判 `got > V`」的话，`_v: true` 会被当 v1 读出来，而下一次
    `save_json` 就把真内容**整个覆盖** —— 正是本文件头号要防的失败形态。
    """
    for bad in (True, False, "1", 0, -1, 2.0):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "s.json"
            p.write_text(json.dumps({"_v": bad, "a": 1}), encoding="utf-8")
            try:
                got = store.load_json(p)
            except store.StoreError:
                continue
            raise AssertionError(f"_v={bad!r} 没抛，被读成了 {got!r} —— "
                                 f"这几种必须和「未来版本」一样被挡住")


@case("⭐⭐ 变异验证：让未来版本也照读 → 上面那条必须红")
def t_mutation():
    orig = store.load_json

    def mutant(path, default=None):
        p = pathlib.Path(path)
        if not p.exists():
            return default if default is not None else {}
        return json.loads(p.read_text(encoding="utf-8"))    # 变异：不看版本
    store.load_json = mutant
    try:
        caught = False
        try:
            t_future_version()                              # 它读的是 `store.load_json`
        except AssertionError:
            caught = True
    finally:
        store.load_json = orig
    assert caught, "变异没被抓住 —— 上面那条判据没有区分能力"


@case("⭐ 没有版本号的老文件按 v1 认（不然老用户的数据一夜全废）")
def t_legacy_no_version():
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "s.json"
        p.write_text(json.dumps({"a": 2}), encoding="utf-8")   # 老格式：没有 _v
        assert store.load_json(p) == {"a": 2}, (
            "没有 _v 的文件被拒了 —— 本约定上线之前写的文件会全部报错")


@case("坏 JSON → StoreError（不是默认值）")
def t_corrupt():
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "s.json"
        p.write_text("{坏的", encoding="utf-8")
        try:
            store.load_json(p)
        except store.StoreError:
            return
        raise AssertionError("坏 JSON 被当成了正常文件")


@case("顶层不是 dict → StoreError")
def t_wrong_shape():
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "s.json"
        p.write_text("[1,2,3]", encoding="utf-8")
        try:
            store.load_json(p)
        except store.StoreError:
            return
        raise AssertionError("顶层是 list 却被接受了")


@case("`stamp()` 不改入参")
def t_stamp_pure():
    obj = {"a": 1}
    out = store.stamp(obj)
    assert obj == {"a": 1}, "stamp 改了入参"
    assert out[store.K] == store.V and out["a"] == 1


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
