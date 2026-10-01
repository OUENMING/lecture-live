#!/usr/bin/env python3
"""`notice` 的判据 —— 「什么时候弹框」与「问不到人时返回什么」。

    ClassLive.app/Contents/MacOS/python tests/test_notice.py

## 这个文件钉住了什么

1. ⭐⭐ **双击启动（没终端）→ 必须弹框**；**终端启动 → 必须打印**。
   这两条 2026-09-28 之前是**反的**（`_can_alert()` 写成 `not os.environ.get(...)`），
   后果是双击的用户在「麦克风被拒 / 音源打不开 / 模型没装好」时
   **屏幕上什么都不出现**，只往 `app.log` 写一行 —— 他看到的是「双击了、什么都没发生」。
   而四处文档（含 `main.py` 里 `run()` 那处 `notice.alert` 的注释）写的都是
   「没有终端时弹框」。
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


@case("⭐⭐ `_present`：先激活 app、再把窗口抬到 ModalPanel 层（顺序也钉住）")
def t_present_activates_and_raises():
    # ⚠️ 2026-10-01 实测抓到的「点了就假死」：弹窗排在非活动 app 的层级里 +
    #    被自家 floating(3) 的面板盖住 → 整块看不见，主线程却卡在 runModal。
    calls: list = []

    class _App:
        def activateIgnoringOtherApps_(self, flag):
            calls.append(("activate", bool(flag)))

    class _Win:
        def setLevel_(self, lv):
            calls.append(("level", float(lv)))

        def center(self):
            calls.append(("center",))

        def orderFrontRegardless(self):
            calls.append(("front",))

    notice._present(_Win(), app=_App())
    kinds = [c[0] for c in calls]
    assert kinds == ["activate", "level", "center", "front"], (
        f"动作/顺序不对：{calls} —— 官方口径：先 activate，再抬层级，最后才 show")
    assert calls[0][1] is True, f"activate 必须是 True（ignoringOtherApps）：{calls[0]}"
    from AppKit import NSModalPanelWindowLevel
    assert calls[1][1] == float(NSModalPanelWindowLevel), (
        f"层级要抬到 ModalPanel(8)，不然被自家 floating(3) 的面板盖住：{calls[1]}")


@case("⭐⭐ 弹框的两个入口都必须走 `_present`（绕过去 = 又变成看不见的假死）")
def t_alert_paths_use_present():
    seen: list = []
    orig = notice._present

    class _Boom(Exception):
        pass

    def _rec(win, **kw):
        seen.append(win)
        raise _Boom("sentinel：判据不许真弹模态框")

    notice._present = _rec
    try:
        with _Env("1"):                     # 走 AppKit 那一支
            r1 = notice.alert("t", "m", buttons=("知道了",), fallback="知道了")
            r2 = notice.ask_text("t", "m",
                                 fields=[{"key": "k", "label": "L", "hint": "h"}],
                                 fallback=None)
    finally:
        notice._present = orig
    assert len(seen) == 2, (
        f"alert()/ask_text() 各要走一次 `_present`（走到 {len(seen)} 次）—— "
        f"任一入口绕过去，它弹的框就会排在别的窗口后面看不见")
    # 打断后走 fail-soft：返回 fallback（弹框环节出岔子也不许把调用方弄崩）
    assert r1 == "知道了" and r2 is None, (r1, r2)


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
