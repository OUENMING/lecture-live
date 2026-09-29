#!/usr/bin/env python3
"""`objc_own` 的判据 —— 它存在的理由就是「**结构上不可能撞名 / 不可能静默覆盖**」。

    ClassLive.app/Contents/MacOS/python tests/test_objc_own.py

## 钉住什么

1. ⭐⭐ **同一个 key 不能有两种语义**（换 `base` 要抛，不许静默复用旧类）——
   本仓库踩过「同一个 key 第二次调用拿回同一个类、**点哪张卡都触发第一张的动作**」。
2. ⭐⭐ **`lookUpClass` 的其它失败不许被当成「类不存在」** —— 那会随后
   `type(name, ...)` 直接定义，把「**静默覆盖已有类**」重新引回来。
3. 正常路径：同 key 同 base -> 拿回同一个类（缓存生效）；新 key -> 新类。
4. ⚠️ 撞名（进程里有别的东西占了这个前缀）要抛 `ObjcNameCollision`。

⚠️ **本文件会真的定义 ObjC 类**（这就是被测对象）—— 所以每个 key 用本文件独有的
   前缀（`T_` 打头），别撞上生产那几个（`Panel` / `DragLayer` / `ButtonTarget` /
   `FrozenPanel` / `FrozenDrag`）。
"""
from __future__ import annotations

import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

CASES: list[tuple[str, object]] = []


def case(name: str):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


# ---------------------------------------------------------------- 正常路径
@case("同 key 同 base -> 拿回**同一个类**（缓存生效）")
def t_same_key_same_base():
    import objc_own
    from AppKit import NSView
    a = objc_own.own("T_Same", NSView, {})
    b = objc_own.own("T_Same", NSView, {})
    assert a is b, f"同 key 同 base 该复用，拿到两个不同的类：{a} / {b}"
    assert a.__name__ == objc_own._PREFIX + "T_Same", a.__name__


@case("不同 key -> 不同的类（名字带前缀，互不冲突）")
def t_different_keys():
    import objc_own
    from AppKit import NSView
    a = objc_own.own("T_Alpha", NSView, {})
    b = objc_own.own("T_Beta", NSView, {})
    assert a is not b and a.__name__ != b.__name__, f"{a} vs {b}"


# ---------------------------------------------------------------- 两条核心
@case("⭐⭐ 同 key 换 base -> **抛**，不许静默复用旧类")
def t_same_key_different_base_raises():
    """⭐ 2026-09-29 修。原来缓存命中直接 `return got` —— 于是第二次调用
    **拿到的是第一次那个基类的类**，而调用方以为自己拿到的是新基类的子类。
    本仓库因此踩过「点哪张卡都触发第一张的动作」。

    「同一个 key 只能有一种语义」是本模块的立身之本 —— 违反它不是"复用"，是**冲突**。
    """
    import objc_own
    from AppKit import NSButton, NSView
    objc_own.own("T_Conflict", NSView, {})
    try:
        objc_own.own("T_Conflict", NSButton, {})     # 换基类 = 语义冲突
    except objc_own.ObjcNameCollision:
        return                                        # ✅ 正是要的行为
    raise AssertionError(
        "换 base 却静默复用了旧类 —— 调用方拿到的是一个**不是它要的基类**的子类")


@case("⭐⭐ `lookUpClass` 的其它失败 -> **不许**当成「类不存在」")
def t_lookup_other_failure_not_swallowed():
    """⭐ 2026-09-29 修。原来写的是 `except Exception: existing = None` ——
    于是 `lookUpClass` 因**别的原因**失败（运行时不正常、名字非法…）也被判成
    「没被占用」，随后 `type(name, ...)` 直接定义 ——
    正好把本模块要消灭的那条「**静默覆盖已有类**」重新引回来。
    """
    import objc
    import objc_own
    from AppKit import NSView
    orig = objc.lookUpClass
    objc.lookUpClass = lambda n: (_ for _ in ()).throw(
        RuntimeError("运行时不正常（不是 nosuchclass_error）"))
    try:
        objc_own.own("T_Boom", NSView, {})
    except RuntimeError:
        return                                        # ✅ 冒泡，不装作"类不存在"
    except objc_own.ObjcNameCollision:
        raise AssertionError("把「查不了」当成了「已被占用」—— 方向反了")
    finally:
        objc.lookUpClass = orig
    raise AssertionError("查类失败被吞掉了 —— 接着就会 `type(...)` 静默定义/覆盖")


@case("⚠️ 真·撞名（前缀已被别人占了）-> 抛 ObjcNameCollision")
def t_real_collision_raises():
    import objc
    import objc_own
    from AppKit import NSView
    # 先手工用掉那个名字
    mine = objc_own._PREFIX + "T_Taken"
    try:
        objc.lookUpClass(mine)
    except objc.nosuchclass_error:
        type(mine, (NSView,), {})                     # 冒充"别人占的"
    try:
        objc_own.own("T_Taken", NSView, {})
    except objc_own.ObjcNameCollision:
        return
    raise AssertionError("名字被占了却没说 —— 会静默拿到别人的类")


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
