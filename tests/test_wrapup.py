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
import shutil
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import main                                                        # noqa: E402
import obsidian_writer as ow                                       # noqa: E402
import polish                                                      # noqa: E402

# ⚠️⚠️ **`SESSIONS` 必须整文件指到临时目录**（2026-09-28 审查指出）：
#    `_writer` 用的是 `mode="yes"` → `enabled=True` → `ObsidianWriter.__init__`
#    会 `SESSIONS.mkdir()` 并往 `obsidian_writer.SESSIONS`
#    （= **仓库真实的 `sessions/`**）写一个 `<日期>_<时间>_TESTX.md`。
#    **本文件的每个用例都会留一个残留**：既违背文件头「不碰 `sessions/` 一个字节」，
#    又是在往一个**只读不删**的目录里堆垃圾（CLAUDE.md 那条硬规矩）。
#    ⚠️ 2026-09-29 实测**同类漏网**：`test_audit_regressions` 的
#       `test_writer_appends_session_and_parses` 只换了 `vault`、没换 `SESSIONS`
#       → **一天里往真 `sessions/` 堆了 91 个 `_TEST.md`**。已修。
#       ⚠️ 教训：**写端**（`SESSIONS`）和读端都要换 —— 只换 `vault` 不够。
_SESSIONS_ISO = pathlib.Path(tempfile.mkdtemp(prefix="cl-wrapup-sessions-"))
ow.SESSIONS = _SESSIONS_ISO

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
    # ⚠️⚠️ **`_review` 必须在这里就接管**（2026-09-28 审查指出）：不接管的话
    #    `close()` 会走到 `obsidian_writer._call_review`，那里是**真的
    #    `httpx.post("https://api.deepseek.com/v1/chat/completions", timeout=180)`**。
    #    本文件头写着「不发任何网络请求（`polish._chat` 与 `_review` 都被替身接管）」——
    #    而实际上只有 `t_review_interrupt` 那一支装了，另外两支**一直在真发请求**
    #    （假 key 会 401 快速失败，所以从没人发现）。
    #    ⚠️ 需要测复习层中断的那一支会在用例里**再覆盖一次**（那是它的被测对象）。
    w._review = lambda *a, **k: {}
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

        def boom(entries, on_progress=None):       # 签名要跟实现走（多了 on_progress）
            raise KeyboardInterrupt
        w._review = boom
        w.close()
        assert w.vault_path and pathlib.Path(w.vault_path).exists(), (
            "复习层被打断后笔记没写出来")


@case("⭐⭐ 取消标志置位 → 跳过精修，但笔记**仍然**写出来")
def t_cancel_still_writes():
    """⭐ 这条是「Ctrl+C / [跳过精修] 不丢笔记」的核心保证。

    ⚠️ 为什么要靠标志而不是 KeyboardInterrupt：收尾跑在 **worker** 上，而 Python
       的信号只在主线程跑（PEP 475）—— worker 永远收不到中断。所以上面那两条
       `except KeyboardInterrupt` 判据**在真实流程里是死的**，真正兜底的是这个标志。
    """
    import threading
    with tempfile.TemporaryDirectory() as d:
        w = _writer(d, api_key="k", polish=True)
        cancel = threading.Event()
        cancel.set()                               # 一进来就叫停
        w.close(cancel=cancel)
        assert w.vault_path and pathlib.Path(w.vault_path).exists(), (
            "取消后笔记没写出来 —— 取消的语义是「跳过精修」，不是「放弃笔记」")
        txt = pathlib.Path(w.vault_path).read_text(encoding="utf-8")
        assert "未跑到的那部分是直播版" in txt, (
            f"frontmatter 该记「精修被跳过」那一档，实际没找到：\n{txt[:300]}")


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


@case("⭐ 窗口在问话前就关了（✕ 正常停止）**但有终端** → 退回终端那条")
def t_route_after_close():
    """⭐ 2026-09-28 的回归就是这一条。

    `✕` 是 overlay 模式的**正常停止方式**（`README.md:310` 逐字：「点悬浮窗右上角
    ✕，或终端按 Ctrl+C（两者都是优雅退出：冲刷队列 + **落盘**）」），而
    `ask_save()` 一看到 `_closed` 就返回 `None` → `give_up` → **整份
    `writer.close()` 被跳过，这节课一个字笔记都不写**。

    ⚠️ `terminal=True` 是**注入**的，不是真去看 `sys.stdin` ——
       否则这条判据的结果会跟着「跑测试的那个终端」变。
    """
    class _Shut:
        _closed = True

        def ask_save(self, n, timeout=60.0):
            return True                  # 与生产同款：关过窗不再等于放弃
    assert main._wrapup_route(_Shut(), True, terminal=True) == "terminal", (
        "有终端时退回终端那条（它默认存）")


@case("⭐⭐ 双击启动（✕ 过 + **没有终端**）→ 必须走 UI，不许退回虚空")
def t_route_after_close_no_terminal():
    """⭐ 2026-09-29 实测的洞 —— 上面那条在**有终端**时是对的，双击那条路上是错的。

    双击 `ClassLive.app` 时 `✕` 是**唯一**的停止方式，所以 `_closed` 一定为真；
    而 `sitecustomize.py` 把 stdout/stderr 接进日志文件、stdin 也不是 tty
    → 「退回终端」实际是**退回虚空**：问句只写进 `app.log`、默认存，
    而**收尾卡一次都不出现**，11 分钟精修全程屏上空的。

    证据（2026-09-29 11:14 那节课，481 句）：日志里只有
    `📝 本次共记录 481 句双语。存入 Obsidian 吗? [Y/n]`，
    而 `TerminalUI` 的逐句输出（`▸` / `✅`）和字符框**一个都没有**
    → `ui` 是浮窗、`ask_save` 在，只剩 `_closed=True` 这一个分支。
    """
    class _Shut:
        _closed = True

        def ask_save(self, n, timeout=60.0):
            return True
    assert main._wrapup_route(_Shut(), True, terminal=False) == "ui", (
        "没有终端时「退回终端」= 退回虚空：卡不出现、精修期间屏上什么都没有")


@case("_terminal_usable：非 tty / 已关闭的流 / None 一律算「没有终端」")
def t_terminal_usable():
    class _Tty:
        def isatty(self):
            return True

    class _Pipe:
        def isatty(self):
            return False

    class _Boom:
        def isatty(self):
            raise ValueError("I/O operation on closed file")

    assert main._terminal_usable(_Tty()) is True
    assert main._terminal_usable(_Pipe()) is False
    assert main._terminal_usable(_None()) is False
    assert main._terminal_usable(_Boom()) is False, (
        "流已关闭时 isatty() 抛 ValueError —— 收尾路径上再抛一次就等于丢整节课")


class _None:
    def isatty(self):
        return False


@case("⭐⭐ 已经按过 ✕ 的浮窗：ask_save 返回 True（笔记照写），不是 None")
def t_ask_save_after_close():
    """⭐ 这条钉的是那个洞的**第二半** —— 只改路由是不够的。

    `_closed` 一个标志扛了两件事：「课已经停了」和「用户正看着卡说不存」。
    混在一起时，双击那条路会：路由走 UI（上面那条判据）→ 可 `ask_save` 一进来
    就因为 `_closed` 跳过整段问话、返回 `None` → `give_up` →
    **整份 `writer.close()` 被跳过，这节课一个字笔记都不写**。

    ⚠️ 驱动的是 `Overlay.ask_save` **本体**（不是抄一份逻辑），
       只借一个最小替身喂 `_panel` / `pump` / `wrapup_close`。
    """
    import overlay as ov

    class _Fake:
        _closed = True
        _panel = None

        def pump(self):
            pass

        def wrapup_close(self):
            self.card_closed = True

    f = _Fake()
    got = ov.Overlay.ask_save(f, 3, timeout=0.01)
    assert got is True, (
        f"已经关过窗不算放弃 —— 该默认存，拿到 {got!r}"
        "（None = give_up = 整节课没笔记）")


@case("⭐ 问话**期间**关窗 → ask_save 返回 None（真的放弃），且卡被收掉")
def t_ask_save_closed_during_question():
    import overlay as ov

    class _Fake:
        _closed = False
        _panel = None

        def __init__(self):
            self.card_closed = False

        def pump(self):
            self._closed = True          # 用户在问话期间按了 ✕

        def wrapup_close(self):
            self.card_closed = True

    f = _Fake()
    got = ov.Overlay.ask_save(f, 3, timeout=5.0)
    assert got is None, f"问话期间关窗 = 放弃，拿到 {got!r}"
    assert f.card_closed is True, (
        "卡必须**真的**收掉 —— `wrapup.build()` 已经 orderFrontRegardless 了，"
        "只清引用的话那张卡会一直留在屏上")


@case("窗口还开着 → 走 UI（在卡上问）")
def t_route_open():
    class _Open:
        _closed = False

        def ask_save(self, n, timeout=60.0):
            return True
    assert main._wrapup_route(_Open(), False) == "ui"


@case("TerminalUI（有 ask_save 但没驱动 AppKit）→ 仍走终端那条实现")
def t_route_terminal():
    assert main._wrapup_route(main.TerminalUI(), False) == "ui", (
        "TerminalUI.ask_save 本身就是终端实现，走它没问题")
    assert main.TerminalUI().drives_appkit is False


@case("⭐ 卡片变高时 glass 跟着变（不跟的话顶部就没有磨砂底）")
def t_card_layers_follow_height():
    """⚠️ 2026-09-28 OCR 审计发现：`glass`/`scrim`/`drag` 是 `contentView` 的
    **子视图**，AppKit 对代码建的视图**默认不自动缩放** → 卡片一变高，glass 还停在
    初始高度（实测窗口 380×272 / glass 380×200）。
    ⚠️ 变矮时看不出来（glass 从底部往上盖，多出来那截被窗口裁掉）——
       所以只有"状态文字变长"能撞到它。
    """
    import wrapup
    card = wrapup.build()
    assert card is not None, "卡片没建起来（这条判据需要 AppKit）"
    card["set_status"]("很长" * 200)          # 逼它变高
    win_h = card["panel"].frame().size.height
    glass_h = card["_fp"].glass.frame().size.height
    try:
        assert abs(win_h - glass_h) < 1.0, (
            f"窗口高 {win_h:.0f} 而 glass 高 {glass_h:.0f} —— "
            f"顶部 {win_h - glass_h:.0f}px 没有磨砂背景")
    finally:
        # ⚠️ **关窗要进 finally**（2026-09-28 审查指出）：上面那条断言失败的
        #    时候（恰恰就是本用例要抓的那个 bug）原来会跳过 `close()` ——
        #    把一个建好的窗口/AppKit 状态留给后面的用例，造成连带失败、
        #    把真正的第一个失败点盖住。
        card["close"]()
@case("⭐⭐ 按钮多了**不许溢出卡片**（4 个原来右边界 448 > 380，被静默裁掉）")
def t_buttons_never_overflow():
    """⭐ 2026-09-29 修。原来固定 `PAD + i*(BTN_W+BTN_GAP)`：
    3 个刚好（3×100+2×10 = 320 ≤ 344），**第 4 个右边界 448 > 380**
    → 溢出卡片被裁，而 **AppKit 不报错**（按钮一半在窗外、点不到）。

    ⚠️ 现在按个数均分（`BTN_W` 只作上限），按钮**一律等宽** ——
       所以「有几个按钮」不再改变单个按钮的观感（≤3 个时与以前完全一样）。
    """
    import wrapup
    card = wrapup.build()
    assert card is not None, "卡片没建起来（这条判据需要 AppKit）"
    try:
        for n in (1, 2, 3, 4, 5):
            card["set_buttons"]([(f"按{i}", (lambda: None)) for i in range(n)])
            card["panel"].displayIfNeeded()
            btns = [v for v in card["_fp"].glass.subviews()
                    if type(v).__name__ == "NSButton"]
            assert len(btns) == n, f"画出来 {len(btns)} 个按钮，期望 {n}"
            right = max(float(b.frame().origin.x) + float(b.frame().size.width)
                        for b in btns)
            assert right <= wrapup.WIDTH - wrapup.PAD + 0.01, (
                f"{n} 个按钮时最右边界 {right:.1f} 超出卡片"
                f"（上限 {wrapup.WIDTH - wrapup.PAD:.1f}）—— 会被静默裁掉")
    finally:
        card["close"]()


@case("⚠️ 按钮**多到放不下** -> 报错，不许悄悄少画一个")
def t_too_many_buttons_raises():
    import wrapup
    card = wrapup.build()
    assert card is not None, "卡片没建起来（这条判据需要 AppKit）"
    try:
        n = 20                                   # 20×60 + 19×10 = 1390 ≫ 344
        try:
            card["set_buttons"]([(f"按{i}", (lambda: None)) for i in range(n)])
        except ValueError:
            return                               # ✅ 正是要的行为
        raise AssertionError(f"{n} 个按钮没报错 —— 会静默裁掉几个，用户点不到")
    finally:
        card["close"]()


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


@case("⭐⭐ 模型给非字符串的 en/zh -> 不抛（原来整节课精修中断）")
def t_polish_nonstring_fields():
    """⭐ 2026-09-29 修。`(it.get("en") or "").split()` —— `12 or ""` 是 **`12`**，
    不是 `""` → `.split()` 抛 `AttributeError`。而这一段在 `_chat` 的 try
    **之外** → **整节课的精修中断**（不只是那一条丢掉）。

    ⚠️ 上面已经挡了「`it` 不是 dict」—— 同一条纪律，字段也要挡。
    """
    entries = [dict(ts="10:00:00", en="orig", zh="原", asr="", star=False)]
    orig = polish._chat
    # 各种模型可能吐出来的脏形状
    for bad in ({"i": 0, "en": 12, "zh": 34},
                {"i": 0, "en": ["a", "b"], "zh": "好"},
                {"i": 0, "en": {"x": 1}, "zh": None}):
        polish._chat = lambda *a, **k: {"items": [bad]}
        try:
            got = polish.polish_entries([dict(e) for e in entries], "k", "m",
                                        batch=5)
        except Exception as e:                                 # noqa: BLE001
            raise AssertionError(f"{bad!r} 让精修抛了 {type(e).__name__}: {e}")
        finally:
            polish._chat = orig
        assert isinstance(got, list) and len(got) == 1, f"{bad!r} -> {got!r}"
    # 反面：**正常字符串照样要采纳**（别修成"一律丢掉"）
    polish._chat = lambda *a, **k: {"items": [{"i": 0, "en": " fixed ", "zh": "改"}]}
    try:
        got2 = polish.polish_entries([dict(e) for e in entries], "k", "m", batch=5)
    finally:
        polish._chat = orig
    assert got2[0]["en"] == "fixed", f"正常字符串没被采纳：{got2[0]!r}"


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
    shutil.rmtree(_SESSIONS_ISO, ignore_errors=True)
    return 1 if CASES_FAIL else 0


CASES_FAIL: list[str] = []

if __name__ == "__main__":
    sys.exit(main_())
