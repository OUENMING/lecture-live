#!/usr/bin/env python3
"""收尾阶段的三条护栏 + 终端超时提示的判据。

    ClassLive.app/Contents/MacOS/python tests/test_wrapup.py

## 这个文件钉住了什么

1. ⭐⭐ **精修跑到一半被打断，整份笔记仍然写出来** —— 2026-09-28 查出的真缺陷：
   `close()` 里那一支原来是 `except Exception`，而 `KeyboardInterrupt` **不是**
   `Exception` 的子类 → 在最长的那一段（实测 579 句要 11 分钟）按 Ctrl+C，
   会把整节课的 Obsidian 笔记**静默丢掉**，只剩 `sessions/` 里的原始文件。
2. ⭐⭐ **复习层同理** —— 它完全没有护栏，比精修还脆。
3. ⭐ **终端提示带超时**，且超时后**残留输入不许污染下一个提示**。
4. **`polish.STAGE_NAME` 是 stage 显示名的唯一定义点**，未知名显示原名而不是空白。

⚠️ 语料全是合成的（`tempfile`），不碰 `sessions/` 一个字节，也不发任何网络请求
   （`polish._chat` 与 `_review` 都被替身接管）。
⚠️ 每条断言先问「把实现改坏它会不会红」—— 第 3 条**自带变异验证**：
   把 `termios.tcflush` 换成空操作，那条必须变。
"""
from __future__ import annotations

import os
import pathlib
import pty
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import main                                                        # noqa: E402
import obsidian_writer as ow                                       # noqa: E402
import polish                                                      # noqa: E402

CASES: list[tuple[str, object]] = []


def case(name: str):
    """登记一条判据。⚠️ 用**列表**收集，别靠函数名 —— 第一版所有用例都叫 `_`，
    装饰器互相覆盖，最后只剩一个能跑。"""
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


# ---------------------------------------------------------------- 终端提示
def _with_pty(fn):
    """开一对 pty，把 `sys.stdin` 接上去跑 `fn(master_fd)`。

    ⚠️ **只换 stdin，不换 stdout** —— 第一版把 stdout 也接了过去，断言结果全写进了
       那个 pty，终端上一片空白。量具自己坏了，看起来像产品没问题。
    """
    m, s = pty.openpty()
    saved = sys.stdin
    sys.stdin = os.fdopen(os.dup(s))
    try:
        return fn(m)
    finally:
        sys.stdin = saved
        for fd in (s, m):
            try:
                os.close(fd)
            except OSError:
                pass


@case("非 tty（管道 EOF）→ 立刻返回 None，不许等满超时")
def t_pipe_eof():
    r, w = os.pipe()
    os.close(w)
    saved = sys.stdin
    sys.stdin = os.fdopen(r)
    try:
        t0 = time.monotonic()
        got = main._input_timed("? ", 30.0)
        dt = time.monotonic() - t0
    finally:
        sys.stdin = saved
    assert got is None, f"EOF 该给 None，得到 {got!r}"
    assert dt < 2.0, f"EOF 该立刻返回，等了 {dt:.1f}s"


@case("真 tty 一个字不打 → 到点返回 None，且确实等了那么久")
def t_pty_timeout():
    t0 = time.monotonic()
    got = _with_pty(lambda m: main._input_timed("? ", 1.5))
    dt = time.monotonic() - t0
    assert got is None, f"超时该给 None，得到 {got!r}"
    assert 1.3 <= dt <= 4.0, (
        f"该在 1.5s 上下返回，实测 {dt:.1f}s（立刻 = 没超时，很久 = 超时没生效）")


def _stale(m):
    os.write(m, b"abc")                    # 打字，但**不回车**
    first = main._input_timed("? ", 1.0)   # 超时 → 应当把 abc 冲掉
    os.write(m, b"\n")                     # 事后补的回车
    return first, main._input_timed("? ", 1.0)


@case("⭐ 超时后残留输入不污染下一个提示（读到的是补的那个回车，不是 abc）")
def t_stale_input():
    got = _with_pty(_stale)
    # ⚠️ 机制：规范模式下「abc 不回车」不算一行可读输入，`select` 本来就不返回它 ——
    #    所以断言写成「第二次也超时」是**空判据**（变异验证当场抓过这一条）。
    #    真正能区分的是补的那个回车：它会把残留的 abc 补成完整一行。
    assert got == (None, ""), f"该是 (None, ''), 得到 {got!r}（读到 abc 就是没冲掉）"


@case("⭐⭐ 变异验证：把 tcflush 换成空操作 → 上面那条**必须**变")
def t_stale_mutation():
    import termios
    orig = termios.tcflush
    termios.tcflush = lambda *a, **k: None
    try:
        got = _with_pty(_stale)
    finally:
        termios.tcflush = orig
    assert got != (None, ""), (
        "变异没被抓住 —— 上面那条判据没有区分能力（把实现改坏它照样绿）")


@case("tty 上正常输入 → 返回内容")
def t_pty_reads():
    got = _with_pty(lambda m: (os.write(m, b"y\n"),
                               main._input_timed("? ", 3.0))[1])
    assert got == "y", f"该读到 'y'，得到 {got!r}"


# ---------------------------------------------------------------- 中断护栏
SESSION = """# 2026-01-01_000000_TESTX

## 实时转录与双语对照

> [!abstract] 10:00:00
> **EN**: hello world
> **ZH**: 你好世界
> **ASR**: hello world

> [!abstract] 10:00:05
> **EN**: second one
> **ZH**: 第二句
> **ASR**: second one
"""


def _writer(d: str, **kw):
    """一个指向临时 vault / 临时会话的 writer。

    ⚠️ 必须把 `_n` 顶起来：`close()` 在「零句又无问答」时**早退**，
       于是 `vault_path` 是 None，判据会以"没有笔记"的形式假红。
       本文件测的是**中断护栏**，不是 `append()`，所以直接置数。
    """
    w = ow.ObsidianWriter(d, "TESTX", mode="yes", **kw)
    sp = pathlib.Path(d) / "s.md"
    sp.write_text(SESSION, encoding="utf-8")
    w.session_path = sp
    w._n = 2
    return w


@case("⭐ 精修中途抛 KeyboardInterrupt → 笔记**仍然**写出来")
def t_polish_interrupt():
    with tempfile.TemporaryDirectory() as d:
        w = _writer(d, api_key="k", polish=True)
        orig = polish.polish_entries

        def boom(*a, **k):
            raise KeyboardInterrupt
        polish.polish_entries = boom
        try:
            w.close()
        finally:
            polish.polish_entries = orig
        assert w.vault_path and pathlib.Path(w.vault_path).exists(), (
            "精修被打断后笔记没写出来 —— 正是 2026-09-28 那个真缺陷")


@case("⭐ 复习层中途抛 KeyboardInterrupt → 笔记**仍然**写出来")
def t_review_interrupt():
    with tempfile.TemporaryDirectory() as d:
        w = _writer(d, api_key="k", polish=False)

        def boom(entries):
            raise KeyboardInterrupt
        w._review = boom
        w.close()
        assert w.vault_path and pathlib.Path(w.vault_path).exists(), (
            "复习层被打断后笔记没写出来")


@case("中断那一档在 frontmatter 与人话里都写得明白（不是 failed）")
def t_interrupt_state():
    with tempfile.TemporaryDirectory() as d:
        w = _writer(d, api_key="k", polish=True)
        orig = polish.polish_entries

        def boom(*a, **k):
            raise KeyboardInterrupt
        polish.polish_entries = boom
        try:
            w.close()
        finally:
            polish.polish_entries = orig
        txt = pathlib.Path(w.vault_path).read_text(encoding="utf-8")
        assert "polish: interrupted" in txt, "frontmatter 该记 interrupted"
        assert "精修被跳过" in txt, "该有人话解释，不是只写个代号"


# ---------------------------------------------------------------- 进度回调
@case("⭐ `polish_entries` 的 on_progress 是 (stage, done, total) 三元组")
def t_progress_shape():
    seen: list[tuple] = []
    entries = [dict(ts="10:00:00", en=f"e{i}", zh=f"z{i}", asr="", star=False)
               for i in range(5)]
    orig = polish._chat
    polish._chat = lambda *a, **k: {"items": [{"i": i, "en": "E", "zh": "Z"}
                                              for i in range(5)]}
    try:
        polish.polish_entries(entries, "k", "m", batch=5,
                              on_progress=lambda *a: seen.append(a))
    finally:
        polish._chat = orig
    assert seen, "on_progress 一次都没被调"
    for got in seen:
        assert len(got) == 3, f"该是三元组 (stage, done, total)，得到 {got!r}"
        stage, done, total = got
        assert isinstance(stage, str) and stage in polish.STAGE_NAME, (
            f"stage 该是 STAGE_NAME 里的机器名，得到 {stage!r}")
        assert isinstance(done, int) and isinstance(total, int)
    assert seen[-1][0] == "polish" and seen[-1][2] == 5, f"最后一条 {seen[-1]!r}"


@case("stage 显示名只有一份表；未知名显示原名，不显示空白")
def t_stage_table():
    assert polish.progress_text("polish", 3, 10) == "精修 3/10…"
    assert polish.progress_text("who_knows", 1, 2) == "who_knows 1/2…", (
        "不认识的 stage 该显示原名 —— 空白看起来像卡住了")
    for k in ("polish", "polish_partial", "review"):
        assert k in polish.STAGE_NAME, f"{k} 不在 STAGE_NAME 里"


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


CASES_FAIL: list[str] = []

if __name__ == "__main__":
    sys.exit(main_())
