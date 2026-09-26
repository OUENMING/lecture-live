"""ObjC 类名归属 —— **全项目定义 ObjC 子类只此一处**。

## 为什么需要它

**Objective-C 的类是按名字在进程里全局注册的。** 同一个名字定义两次会抛
`_Panel is overriding existing Objective-C class` —— 而且如果那个异常被 fail-soft 的
`except` 吞掉，症状是**静默的**：

    2026-09-26 真实事故：overlay.py 与 whatsnew.py 各定义了一个 `_Panel`，
    第二个被吞掉 → **更新卡片静默返回 None，而独立测试全绿**
    （那些测试里 overlay 没被 import，只有同进程才复现）。

当时的修法是**改名字**（`_CLWhatPanel`、`_T`）—— 治标：每个新消费者仍得记得挑一个
全局唯一的名字，坑还在原地。

## 这里的做法：让调用方**拿不到命名权**

类名由本模块生成（固定前缀 + 调用方给的 key），调用方只拿到类对象。
撞名于是**在结构上不可能发生**；万一还是撞上（进程里有两份本模块），
立刻抛 `ObjcNameCollision`。

## ⚠️ 调用方必须做到的一件事：别吞掉 `ObjcNameCollision`

`whatsnew.py` 那类「构造失败就静默返回 None」的 fail-soft 是**有道理的**
（卡片只是说明，不能因为它让课起不来）—— 但它会把本模块的异常一起吞掉，
于是症状回到上面那个「静默 None」。**fail-soft 只能盖住 AppKit/运行时的偶发问题，
不能盖住「有人撞了类名」这种程序缺陷**：那种要炸出来让人看见。
"""
from __future__ import annotations

#: key → 类。**单一 definition point**：每个 key 在进程里只可能定义一次。
_OWNED: dict = {}

#: 所有本模块生成的类名都用这个前缀。改它没有意义，
#: 除非你同时确认进程里没有别的代码在用同一个前缀。
_PREFIX = "_ClassLive"


class ObjcNameCollision(RuntimeError):
    """要用的 ObjC 类名已经被别的类占了。

    ⚠️ 单独一个类型（而不是裸 RuntimeError）是为了让调用方**能精确地重新抛出**它，
    而不必放过其它 RuntimeError —— 见文件头「必须做到的一件事」。
    """


def own(key: str, base: type, namespace: dict) -> type:
    """取（或首次定义）一个由本模块拥有的 ObjC 子类。

    `key` 决定类名（`_ClassLive<key>`），所以**同一个 key 只能有一种语义** ——
    想共用就用同一个 key（例如 overlay 与 whatsnew 的按钮目标都叫 `ButtonTarget`）。

    ⚠️ 别用 `objc.getClassList()` 做预检：**实测它在类定义前后都返回 `False`**
       （2026-09-26 由子代理发现、我独立复现），拿它当判据等于永远查不到。
       `objc.lookUpClass` 才对：缺了抛 `nosuchclass_error`，在则返回类。
    """
    got = _OWNED.get(key)
    if got is not None:
        return got
    import objc
    name = _PREFIX + key
    try:
        existing = objc.lookUpClass(name)
    except Exception:                       # noqa: BLE001 — nosuchclass_error
        existing = None
    if existing is not None:
        raise ObjcNameCollision(
            f"ObjC 类名 {name} 已被占用（拿到的是 {existing}）——\n"
            f"  进程里有两份 objc_own.py，或者有人手工用了这个前缀。\n"
            f"  ⚠️ 别改这里去绕开：2026-09-26 那次「更新卡片静默变 None」"
            f"就是撞名被 fail-soft 吞掉的结果。先查清是谁占的。")
    cls = type(name, (base,), namespace)
    _OWNED[key] = cls
    return cls


def cls_of(key: str) -> type:
    """给**方法体**用：`objc.super(cls_of("X"), self).someSelector_(...)`。

    ⚠️ 别图省事写 `objc.super(type(self), self)` —— 类一旦**被子类化**，
       `type(self)` 就是那个子类，`objc.super(子类, self)` 会**从子类开始找**，
       行为和原来的 `objc.super(_字面类名, self)` 不一样。
       方法体在类建出来之前就写好了，所以只能这样**在调用时**取回本类。
       （2026-09-26：这一条是 altitude 审查抓出来的 —— 我第一版用的正是
         `type(self)`，而没有任何测试驱动过那条路径。）
    """
    return _OWNED[key]
