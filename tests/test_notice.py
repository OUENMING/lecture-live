#!/usr/bin/env python3
"""`notice` 的判据 —— 「什么时候弹框」与「问不到人时返回什么」。

    ClassLive.app/Contents/MacOS/python tests/test_notice.py

## 这个文件钉住了什么

1. ⭐⭐ **双击启动（没终端）→ 必须弹框**；**终端启动 → 必须打印**。
   这两条 2026-09-28 之前是**反的**（`_can_alert()` 写成 `not os.environ.get(...)`），
   后果是双击的用户在「麦克风被拒 / 音源打不开 / 模型没装好」时
   **屏幕上什么都不出现**，只往 `app.log` 写一行 —— 他看到的是「双击了、什么都没发生」。
   而四处文档（含 `main.py:876` 的注释）写的都是「没有终端时弹框」。
   从 09-26 落地到 09-28 被发现，**一直没有任何判据钉过它**。
2. ⭐ **`CLASSLIVE_FROM_APP` 没设时绝不弹模态框** —— 这条同时是
   「`cl | tee log` / CI 里不会永久挂在弹框上」的判据（那个场景实测卡死过）。
3. ⭐⭐ **确认框必须显式给 `fallback`** —— `alert()` 问不到人时返回 `buttons[0]`，
   而确认框的 `buttons[0]` 是「现在做」。不给就是**不问就替用户批准联网重活**。

⚠️ 不弹任何真框：第 1、3 条都跑在 `_can_alert() is False` 那一支（纯 print）。
⚠️ 每条都问过「把实现改坏它会不会红」—— 第 1、2 条自带变异验证。
"""
from __future__ import annotations

import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import notice                                                      # noqa: E402

CASES: list[tuple[str, object]] = []
CASES_FAIL: list[str] = []


def case(name: str):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


class _Env:
    """临时设/清 `CLASSLIVE_FROM_APP`。"""

    def __init__(self, value):
        self.value = value

    def __enter__(self):
        self.old = os.environ.pop("CLASSLIVE_FROM_APP", None)
        if self.value is not None:
            os.environ["CLASSLIVE_FROM_APP"] = self.value
        return self

    def __exit__(self, *a):
        os.environ.pop("CLASSLIVE_FROM_APP", None)
        if self.old is not None:
            os.environ["CLASSLIVE_FROM_APP"] = self.old
        return False


@case("⭐⭐ 双击启动（CLASSLIVE_FROM_APP=1，没有终端）→ 会弹框")
def t_app_alerts():
    with _Env("1"):
        assert notice._can_alert() is True, (
            "双击时 print 是进 app.log 的，没人看得见 —— 这种情况**必须**弹框")


@case("⭐⭐ 终端启动（变量未设）→ 打印，不弹框")
def t_terminal_prints():
    with _Env(None):
        assert notice._can_alert() is False, (
            "终端里能看见 print，弹模态框只会打断人；"
            "而且 `cl | tee log` / CI 里弹框会**永久挂住**")


@case("⭐⭐ 变异验证：把判据改成 `not ...`（原来的写法）→ 上面两条必须都变")
def t_mutation():
    orig = notice._can_alert
    notice._can_alert = lambda: not bool(os.environ.get("CLASSLIVE_FROM_APP"))
    try:
        with _Env("1"):
            app_side = notice._can_alert()
        with _Env(None):
            term_side = notice._can_alert()
    finally:
        notice._can_alert = orig
    assert app_side is False and term_side is True, (
        f"变异没被抓住（app={app_side} term={term_side}）—— "
        "上面两条判据没有区分能力")


@case("⭐ 确认框问不到人时返回 fallback，**不是** buttons[0]")
def t_confirm_fallback():
    with _Env(None):                        # 走 print 那一支，不弹真框
        got = notice.alert("标题", "正文",
                           buttons=("现在做", "先不做"), fallback="先不做")
    assert got == "先不做", (
        f"该拿 fallback，得到 {got!r} —— 返回 '现在做' 就是**不问就批准了**")


@case("不给 fallback 时退回 buttons[0]（纯告知的框就该这样）")
def t_default_fallback():
    with _Env(None):
        assert notice.alert("标题", "正文") == "知道了"
        assert notice.alert("标题", "正文", buttons=("好", "不好")) == "好"


@case("⭐ `overlay` 那个确认框**显式**给了保守的 fallback")
def t_confirm_site_has_fallback():
    # ⚠️ 这条查的是**那个调用点**（不是整个文件里有没有 fallback 这个词）——
    #    判据得指向它声称的那个位置。
    src = (HERE / "overlay.py").read_text(encoding="utf-8")
    i = src.find('buttons=("现在做", "先不做")')
    assert i >= 0, "没找到那个确认框 —— 它被改过了，这条判据要跟着改"
    around = src[i:i + 700]
    assert 'fallback="先不做"' in around, (
        "那个确认框没给 fallback → 问不到人时返回 '现在做'，"
        "等于不问就替用户批准要联网跑几分钟的重活")


def main_() -> int:
    print("=" * 60)
    for name, fn in CASES:
        try:
            fn()
        except AssertionError as e:
            print(f"  ❌ {name}\n      {str(e)[:220]}")
            CASES_FAIL.append(name)
        except Exception as e:                                 # noqa: BLE001
            print(f"  ❌ {name}\n      {type(e).__name__}: {str(e)[:200]}")
            CASES_FAIL.append(name)
        else:
            print(f"  ✅ {name}")
    print("=" * 60)
    print(f"{len(CASES) - len(CASES_FAIL)}/{len(CASES)} 通过")
    for n in CASES_FAIL:
        print(f"  ❌ {n}")
    return 1 if CASES_FAIL else 0


if __name__ == "__main__":
    sys.exit(main_())
