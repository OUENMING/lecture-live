"""课程卡片面板 —— 第二批的本体。

    双击 .app（或菜单栏 🎧）→ 这门课的卡片列表 → 点「开始上课」/ 往卡片上拖课件

## 它不做什么（这些是**刻意的**，不是没做完）

- **不碰 AppKit 之外的东西**：不读 `argv`、不读 `.course`、不碰音频。
  「开始上课」只调调用方给的 `on_start(course)` —— 谁去写 `.course`、谁去起录音，
  是调用方的事。这样面板**崩了也不会导致录不了课**（作者拍的第 1 条决定）。
- **不做常驻**：作者选了「菜单栏入口」，不做 watched folder。

## 每一步挂在哪条依据上

| 做法 | 依据 |
|---|---|
| **整张卡就是拖拽目标** | `docs/PLAN-entry-panel.md` §3.4。⭐ 有业界先例：NoteExpress 官方 wiki 逐字「直接将全文文件或者文件夹拖入目标文件夹」 |
| 悬停高亮**自己画**、**一次只高亮一张**、`draggingExited` 里撤掉 | HIG › Drag and drop 逐字三条（`RESEARCH-macos-aesthetic.md` §11） |
| 必须有一条**不用拖**的路（「选择文件…」） | HIG「Offer alternative ways to accomplish drag-and-drop actions」 |
| 长任务给进度、跑起来后可关窗 | HIG「keep them informed of its progress」+ Otter 的两段式 |
| 失败**逐文件**标、原因写在那一行 | Untitled UI / justfigma（§2.5 D） |
| 卡片内边距 **10pt** / 卡片间距 **10.5pt** / 行标签 **13pt** | System Settings 实测（2026-09-26），见 plan §2 补充 |
| ⚠️ 卡片靠**细描边**分界，不靠填充差 | 实测「填充差 ΔL = 0.012，界全靠那根 0.5 pt 描边」。⚠️ 这条**推翻了**我原来「把卡片做亮」的想法 |
| ⚠️ **没有进度条／没有 `8/10`** | §0.5.2 的草图里那个 `8/10` 隐含一个「准备度分母」，而**分母并不存在**。→ 只报真实事实，不编比率 |

## ⚠️ 卡片尺寸为什么是「算出来」而不是抄来的

系统卡片的「行高 42.5、卡高 = 行数 × 42.5」是**设置页那种一行一控件**的形状。
我们的卡是三行不同角色的内容（课号+课名 / 准备度 / 两个按钮），形状不同，
所以照抄那个公式会得到一个不成立的数。**抄的是内边距与行标签字号**（那两条与形状无关），
高度按内容累加。
"""
from __future__ import annotations

import os
import pathlib
import threading
import typing

import courses
import objc_own
import panel
import paths

# ── 尺寸（每条都有出处，见模块头）────────────────────────────────────
PAD = 20.0             # 面板内边距（实测：System Settings 20pt）
CARD_PAD = 10.0        # 卡片内边距（实测 10pt）
CARD_GAP = 10.5        # 卡片间距（实测 10.5pt）
CARD_H = 88.0          # 按内容累加，非抄来的
CARD_RADIUS = 5.0      # 卡片圆角（实测 5pt）
HAIRLINE = 0.5         # 描边（实测 0.5pt；scale 2 上正好 1 设备像素）
TITLE_H = 34.0
FOOT_H = 46.0
WIDTH = 680.0
# 卡片区高度：**长到装下为止，但最多占屏幕 60%**。
# ⚠️ 原来写死 420（= 4.3 张卡），于是「5 门课」就必有半张卡被切在边上 ——
#    看上去像坏了。规则改成「先按内容长，超了就滚」，一个公式管两种情形，
#    不用做「≤N 静态 / >N 才滚」两套路径。
BODY_SCREEN_FRACTION = 0.60

# 卡片里的三个高度
L1_H, L2_H, BTN_H = 18.0, 16.0, 24.0
CARD_GAP_V = 8.0       # 卡片内：按钮块 ↔ 文字块（`card_height` 的算式里有它）
# 结果列表（plan §3.6）：跑完在**卡片里**展开，不是一个弹窗
RESULT_LEAD = 26.0     # 「本次加了 N 个」那一行
RESULT_ROW = 22.0      # 一个词一行

# ── 颜色 ────────────────────────────────────────────────────────────
# ⚠️ **深色模式的卡片配色没有实测过**（System Settings 跟随系统外观，本机是浅色，
#    改外观属于「不擅自改系统设置」）。所以下面这几个值是**从实测比例推的**，
#    不是量到的 —— 别把它们当测量值引用。
CARD_FILL_A = 0.045    # 卡片填充：比面板底色略亮（"内容浮起来"）
# 卡片描边 —— **分界的主力**（实测：系统卡片的填充差只有 ΔL 0.012，界全靠描边）。
# ⚠️ 但**数值不是量到的**：系统那根是 **0.5pt @ ≈黑 4%**，量在**浅色不透明**底上；
#    我们是**深色半透明**材质、且背后可能是任意内容，同一个常数搬过来会偏弱。
#    所以取了 0.10（白）—— **这是推的，不是测的**，深色模式那组值本轮没量。
CARD_LINE_A = 0.10
HILITE_A = 0.16        # 拖拽悬停
DIM = 0.62             # 次要文字（白字降 alpha）


class Handles(typing.NamedTuple):
    """`build()` 的把手。**调用方必须留住它**（里面有被弱引用的 delegate/target）。"""
    window: typing.Any
    close: typing.Callable[[], None]
    refresh: typing.Callable[[], None]
    set_status: typing.Callable[..., None]


S = {                              # 同进程只允许一个面板（菜单栏/双击两条入口可能都来）
    "panel": None,
    "result": {},                  # 课号 -> {added, not_added, failed, removed, undo}
}


# ══════════════════════════════════════════════════════════════════════
# 纯逻辑（可单测，不碰 AppKit）
# ══════════════════════════════════════════════════════════════════════
def card_title(r: courses.Readiness) -> str:
    """卡片第一行。

    ⚠️ **术语表首行通常已经带课号**（`# ECON10730 Data Analysis for Economists(经济数据分析)`
    → `courses._title` 返回整行）。再加一遍就会出现
    `ECON10730   ECON10730 Data Analysis…` —— 2026-09-26 跑第一遍时就是这样。
    """
    t = (r.title or "").strip()
    if not t:
        return r.course
    # ⚠️ 判据要带**词边界**，不能只 `startswith(课号)` ——
    #    否则课号是别门课前缀时会误判（`ECON1074` vs 标题 `ECON10740 X`：
    #    `"ECON10740 X".startswith("ECON1074")` 是 True，于是前缀该加却没加）。
    rest = t[len(r.course):] if t.startswith(r.course) else None
    if rest is not None and (not rest or not rest[0].isalnum()):
        return t
    return f"{r.course}   {t}"


def _short_date(d: str | None) -> str:
    """`2026-09-25` → `9/25`。卡片上不需要年份，卡上窄。形状不对就原样返回。"""
    if not d:
        return "从没上过"
    try:
        _y, m, dd = d.split("-")
        return f"{int(m)}/{int(dd)}"
    except (ValueError, AttributeError):
        return d


def readiness_line(r: courses.Readiness) -> str:
    """卡片第二行。**每个数都可能是「未知」** —— 那时画 `—` 而不是 0。

    ⚠️ 「未知」与「零」的区别是刻意的：把读失败显示成 0，
    等于跟用户谎称「这门课一个词都没有」（`courses.py` 模块头第 3 条）。
    """
    def n(v, unit):
        return f"{v} {unit}" if v is not None else f"— {unit}"
    return (f"{n(r.materials, '份课件')} · {n(r.terms, '条术语')}"
            f" · 上次上课 {_short_date(r.last_session)}")


def body_height(n: int, screen_h: float) -> float:
    """卡片区高度：**先按内容长，超过屏幕 60% 才开始滚**。

    一个公式管两种情形 —— 不做「≤N 静态 / >N 才滚」两套路径（那会在第 N+1 门课
    上出现断崖，而且两套路径迟早只维护一套）。
    """
    natural = max(1, n) * (CARD_H + CARD_GAP) - CARD_GAP
    cap = max(CARD_H + CARD_GAP, screen_h * BODY_SCREEN_FRACTION)
    return min(natural, cap)


def kept_added(entry) -> list[str]:
    """结果列表里**还在表里**的那些（已删的不再列）。

    ⚠️ 头部那个「本次加了 N 个」和这个列表**必须同源** —— 两处各算一遍迟早对不上，
       而计数撒谎会直接让用户失去信任（`PLAN-entry-panel.md` §2.5 C 的
       Anki「18 added, **148 updated**」与 LingQ「says 1 new word but it does not」两次）。
    """
    e = entry or {}
    gone = set(e.get("removed") or ())
    return [t for t in (e.get("added") or []) if t not in gone]


def result_header(entry) -> str:
    """结果区那一行。**计数从 `kept_added` 来**，不另存一个数、也不另算一遍。"""
    e = entry or {}
    parts = [f"本次加了 {len(kept_added(e))} 个"]
    if e.get("removed"):
        parts.append(f"已删 {len(e['removed'])}")
    if e.get("not_added"):
        parts.append(f"另有 {len(e['not_added'])} 个没加")
    if e.get("failed"):
        parts.append(f"⚠️ {len(e['failed'])} 个文件失败")
    return "　·　".join(parts)


def card_height(entry=None) -> float:
    """卡片高度 —— **按内容累加推导，不是抄一个数**。

    ⚠️ 它必须和 `_make_card` 里那些 `y` 用**同一组常数**推出来。抄一个固定高度的话，
       加了结果列表之后内容会**溢出卡片**，而且**不报错**（只是画到框外面）。

    ⚠️⚠️ **`card_height(None) == CARD_H` 那条断言不是锁** —— 我一开始以为它是，
       实测：把整个算式换成 `h = 88.0`（写死）**照样全绿**，因为硬编码的 88 恰好
       等于正确值。真正的锁在 `tests/test_panel.py`：**把卡片建出来，断言没有子视图
       超出 `card_height(entry)`** —— 那条不管你常数怎么改都成立。

    无结果时的算式（= `CARD_H` 那个 88）：
        上内边距 10 + 两行标题（18 + 16+2） + 块间距 8 + 按钮 24 + 下内边距 10
    """
    h = (CARD_PAD                                     # 上
         + L1_H + L2_H + 2.0                          # 课号+课名 / 准备度
         + CARD_GAP_V                                 # 结果块与文字块之间
         + BTN_H                                      # 按钮（锚在底部）
         + CARD_PAD)                                  # 下
    if entry is not None:
        n = len(kept_added(entry)) + (1 if entry.get("undo") else 0)
        h += RESULT_LEAD + n * RESULT_ROW
    return h


def progress_text(stage: str, done: int, total: int) -> str:
    """进度文案。stage 是 `prep` 给的机器名，这里只做**显示**。

    ⚠️ 不认识 stage 也不要显示空白 —— 宁可显示原名，也别让人以为卡住了。
    """
    names = {"extract": "抽文本", "candidates": "抽候选词",
             "append": "写入", "notes": "生成释义"}
    label = names.get(stage, stage)
    return f"{label} {done}/{total}…" if total > 0 else f"{label}…"


def summarize(res) -> str:
    """跑完一行话。**计数必须和实际相符**（Anki / LingQ 都因计数撒谎失去信任，§2.5 C）。"""
    if getattr(res, "aborted", False):
        return "没跑完"
    parts = [f"加了 {len(getattr(res, 'added', []) or [])} 个词"]
    na = getattr(res, "not_added", None)
    if na:
        parts.append(f"另有 {len(na)} 个没加")
    sk = getattr(res, "skipped_existing", None)
    if sk:
        parts.append(f"{len(sk) if not isinstance(sk, int) else sk} 个已在表里")
    if getattr(res, "notes_added", None):
        parts.append(f"释义 +{res.notes_added}")
    return "，".join(parts)


# ══════════════════════════════════════════════════════════════════════
# AppKit
# ══════════════════════════════════════════════════════════════════════
def _flipped_doc_class():
    """文档视图必须**翻转**（y=0 在顶部），否则卡片从下往上堆。"""
    from AppKit import NSView
    return objc_own.own("EntryDoc", NSView, {"isFlipped": lambda self: True})


def _button_class():
    """按钮目标。**动作挂在实例上，不挂类上。**

    ⚠️⚠️ **别把回调写进 `objc_own.own()` 的 namespace。** 它按 key 缓存类 ——
    第二次调用拿回的是**同一个类**，于是那个闭包仍是第一次建卡时的那个，
    结果是**每张卡的按钮都触发第一张卡的动作**（今天 5 门课，点哪张都进同一门）。
    仓库既有写法是挂在实例上：`v._cb = fn` + `getattr(self, "_cb")`（见 `panel.py`）。
    """
    from AppKit import NSObject

    def act(self, _sender):
        fn = getattr(self, "_fn", None)
        if fn is None:
            return
        try:
            fn()
        except Exception as e:                            # noqa: BLE001
            # ⚠️⚠️ **AppKit 会吞掉 action 里的异常** —— 症状是「点了没反应」，
            #     和「按钮根本没接上」一模一样，而且**单元测试全绿也照样发生**
            #     （它们测的是被调用的那个函数，不是接线）。
            #     2026-09-26 实测踩到：`_make_card` 调 `on_delete(t)` 只传了词，
            #     而 `do_delete(course, term)` 要两个 → `TypeError` 被吞 →
            #     **点「删」静默无效**。端到端点一下才发现。
            #     → 所以这条路径**出声**，代价只是出错时多打一行。
            import traceback
            print(f"[entry_panel] 按钮动作出错（点了不会有反应）："
                  f"{type(e).__name__}: {e}", flush=True)
            if os.environ.get("CLASSLIVE_DEBUG"):
                traceback.print_exc()

    return objc_own.own("EntryBtn", NSObject, {"act_": act})


def _target(fn):
    """把一个 Python 回调包成能塞给 `setTarget_` 的对象。

    ⚠️ **调用方必须留住它** —— `setTarget_` 是**弱引用**，被 GC 掉就静默不响应。
    所以返回的对象由卡片自己存在 `_targets` 里。
    """
    t = _button_class().alloc().init()
    t._fn = fn
    return t


def _make_card(r: courses.Readiness, *, on_start, on_prep, on_drop_files, width,
               entry=None, on_delete=None, on_undo=None):
    """一张卡 = 一个落点 + 三行内容（+ 跑过之后的结果列表）。"""
    from AppKit import NSButton, NSColor, NSFont, NSMakeRect

    holder: dict = {}

    def _bg(alpha):
        v = holder.get("view")
        if v is not None:
            v.layer().setBackgroundColor_(
                NSColor.whiteColor().colorWithAlphaComponent_(alpha).CGColor())

    # ⚠️ 高亮在 `on_enter` / `on_exit` / `on_drop` **三处**都要管：
    #    落点成功时**不会**再收到 `draggingExited:`，只在 exit 里撤会留下一张
    #    永久高亮的卡（见 `panel.make_drop_target` 的说明）。
    def _enter(pb):
        ok = bool(panel.file_paths(pb))
        _bg(HILITE_A if ok else CARD_FILL_A)
        return ok

    def _exit():
        _bg(CARD_FILL_A)

    def _drop(paths):
        _bg(CARD_FILL_A)
        return on_drop_files(r.course, paths)

    view = panel.make_drop_target(_enter, _drop, _exit)
    holder["view"] = view
    # ⚠️ 高度用 `card_height(entry)`，**不是 `CARD_H`** —— 有结果列表的卡会更高。
    view.setFrame_(NSMakeRect(0.0, 0.0, width, card_height(entry)))
    view._course = r.course
    view._tag = r.course
    view._targets = []

    view.setWantsLayer_(True)
    view.layer().setCornerRadius_(CARD_RADIUS)
    view.layer().setBorderWidth_(HAIRLINE)
    view.layer().setBorderColor_(
        NSColor.whiteColor().colorWithAlphaComponent_(CARD_LINE_A).CGColor())
    _bg(CARD_FILL_A)

    inner_w = width - 2 * CARD_PAD
    # ⚠️⚠️ **从上往下排。** 卡片是**非翻转**坐标（y 向上），所以「往下」= y **递减**。
    #    第一版让结果块从卡片底部往上长 —— 于是**列表是倒的**（最后加的排最上面），
    #    而且**头部跑到了列表下面**（2026-09-26 跑第一遍看出来的）。
    #    → 凡是「按阅读顺序排」的内容块，一律从顶部往下算，别从底部往上堆。
    y = card_height(entry) - CARD_PAD
    y -= L1_H
    view.addSubview_(panel.make_label(
        card_title(r), NSMakeRect(CARD_PAD, y, inner_w, L1_H), 13.0, bold=True))
    y -= (L2_H + 2.0)
    view.addSubview_(panel.make_label(
        readiness_line(r), NSMakeRect(CARD_PAD, y, inner_w, L2_H), 11.0, alpha=DIM))

    def mk(title, x, action, y=CARD_PAD, w=92.0, h=BTN_H):
        b = NSButton.alloc().initWithFrame_(NSMakeRect(x, y, w, h))
        b.setTitle_(title)
        b.setBezelStyle_(1)                             # rounded
        b.setFont_(NSFont.systemFontOfSize_(12.0))
        t = _target(action)
        b.setTarget_(t)                                 # ⚠️ 弱引用 —— 靠 _targets 留住
        b.setAction_("act:")
        view._targets.append(t)
        return b

    # 按钮**锚在卡片底部** —— 它们不是阅读顺序的一部分，是固定动作区。
    # ⚠️ 「开始上课」**只在调用方能兑现时才建**。
    #    `on_start is None` = 这个面板是**上课中**从菜单栏打开的，那时再「开始一节课」
    #    没有意义、而且有害（会去写 `.course` 并试着再起一份录音）。
    #    同 `prep.prepare` 的 `chat=None` / `build_fn=None`：**能兑现才给，不给就不画**。
    x = CARD_PAD
    if on_start is not None:
        view.addSubview_(mk("开始上课", x, lambda: on_start(r.course)))
        x += 92.0 + 8.0
    view.addSubview_(mk("选择文件…", x, lambda: on_prep(r.course)))

    # ── 结果列表（plan §3.6）────────────────────────────────────────
    # ⚠️ **就在卡片里，不是一个弹窗** —— 这是相对「一个文件夹」的真优势，
    #    也是调研里那条真空（没有一个产品把「导入结果 + 回头删」做进主界面）。
    if entry is not None:
        y -= CARD_GAP_V
        y -= RESULT_LEAD
        view.addSubview_(panel.make_label(
            result_header(entry),
            NSMakeRect(CARD_PAD, y + 6.0, inner_w, RESULT_LEAD - 6.0), 11.0, alpha=DIM))
        for t in kept_added(entry):
            y -= RESULT_ROW
            view.addSubview_(panel.make_label(
                t, NSMakeRect(CARD_PAD, y + 3.0, inner_w - 52.0, 16.0), 12.0))
            # ⚠️ 「删」**不做确认框**（HIG › Alerts 逐字：「Avoid displaying alerts for
            #    common, undoable actions, even when they're destructive」；理由是同一条
            #    「A confirmation on an obvious action teaches people to dismiss
            #    confirmations reflexively」）—— 改成**撤销**，见下面那一段。
            view.addSubview_(mk("删", width - CARD_PAD - 44.0,
                                (lambda t=t: on_delete(t)) if on_delete else (lambda: None),
                                y=y + 1.0, w=44.0, h=RESULT_ROW - 6.0))
        undo = entry.get("undo")
        if undo:
            y -= RESULT_ROW
            view.addSubview_(panel.make_label(
                f"已删除 {undo.get('text', '')}",
                NSMakeRect(CARD_PAD, y + 3.0, inner_w - 76.0, 16.0), 11.0, alpha=DIM))
            view.addSubview_(mk("撤销", width - CARD_PAD - 68.0,
                                on_undo or (lambda: None),
                                y=y + 1.0, w=68.0, h=RESULT_ROW - 6.0))
        # 排完之后 y 应当**恰好**等于 `CARD_PAD + BTN_H`（`card_height` 的算式保证）。
        # 对不上就是算式与坐标漂了 —— 那正是 `tests/test_panel.py` 第 ⑥ 组在量的东西。

    return view



def build(*, on_start=None, glossary=None, sessions_dir=None, state_root=None,
          on_close=None, prepare_fn=None) -> Handles | None:
    """建并显示面板。**失败返回 `None`**（调用方不必管 —— 同 `whatsnew.build`）。

    `prepare_fn` 是**验收用的注入点**，默认就是真的 `prep.prepare`：
    验收「拖一张卡」这条链路时，真跑会**写术语表 + `term_notes.json` + 调 DeepSeek 花钱**，
    所以那种验收必须能换掉它（同 `prep.prepare` 自己的 `chat=` / `build_fn=` 口子）。
    ⚠️ 具体签名见 `_run_prep` 里那一处调用 —— **只此一处**。

    ⚠️ 构造失败**不能**吞掉 `objc_own.ObjcNameCollision`：那是程序缺陷，
       被吞掉会静默变成「面板打不开」，查起来极难（`objc_own.py` 文件头那次事故）。
    """
    try:
        return _build(on_start=on_start, glossary=glossary, sessions_dir=sessions_dir,
                      state_root=state_root, on_close=on_close, prepare_fn=prepare_fn)
    except objc_own.ObjcNameCollision:
        raise
    except Exception:                                     # noqa: BLE001
        if os.environ.get("CLASSLIVE_DEBUG"):
            import traceback
            traceback.print_exc()
        return None


def _build(*, on_start, glossary, sessions_dir, state_root, on_close,
           prepare_fn=None) -> Handles:
    from AppKit import (NSApplication, NSColor, NSScreen, NSWindowStyleMaskBorderless,
                        NSWindowStyleMaskNonactivatingPanel)
    from Foundation import NSMakeRect

    root = pathlib.Path(__file__).resolve().parent
    glossary = pathlib.Path(glossary) if glossary else root / "glossary.txt"
    sessions_dir = pathlib.Path(sessions_dir) if sessions_dir else root / "sessions"

    def load():
        names = courses.list_courses(glossary, state_root=state_root)
        return [courses.readiness(glossary, c, sessions_dir=sessions_dir,
                                  state_root=state_root) for c in names]

    rs = load()
    body_h = body_height(len(rs), NSScreen.mainScreen().frame().size.height)
    h = PAD + TITLE_H + body_h + 8.0 + FOOT_H + PAD

    mask = NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel
    fp = panel.build(NSMakeRect(0.0, 0.0, WIDTH, h), mask)
    win, ve = fp.window, fp.glass
    # ⚠️ borderless 默认没有阴影（Titled 的 overlay 才有）—— 同 whatsnew 的处理，
    #    留在调用点、不进 panel.py 的配方。
    win.setHasShadow_(True)

    ve.addSubview_(panel.make_label(
        "开课前的准备", NSMakeRect(PAD, h - PAD - TITLE_H + 6.0,
                                 WIDTH - 2 * PAD, TITLE_H - 6.0), 15.0, bold=True))

    # ── 卡片区（可滚动；今天 5 门课用不到，但第 6 门不该引发断崖）──────
    from AppKit import NSView, NSViewWidthSizable
    doc = _flipped_doc_class().alloc().initWithFrame_(
        NSMakeRect(0.0, 0.0, WIDTH - 2 * PAD, max(body_h, len(rs) * (CARD_H + CARD_GAP))))
    doc.setAutoresizingMask_(NSViewWidthSizable)
    sc = panel.make_scroll_view(NSMakeRect(PAD, PAD + FOOT_H, WIDTH - 2 * PAD, body_h))
    sc.setDocumentView_(doc)
    ve.addSubview_(sc)

    status_holder: dict = {"label": None}

    def set_status(text, alpha=1.0):
        lb = status_holder.get("label")
        if lb is None:
            return
        try:
            lb.setStringValue_(text)
            lb.setTextColor_(NSColor.whiteColor().colorWithAlphaComponent_(alpha))
        except Exception:                                 # noqa: BLE001
            pass

    def refresh():
        """重画卡片（跑完 prep / 删词 / 撤销都走它）。

        ⚠️ **一处重建，不做增量打补丁。** 每张卡的高度不一样（有结果列表的更高），
           增量改高度是布局 bug 的温床；整块重画只有 ~10 张卡，代价可以忽略。
        ⚠️ 高度与坐标都从 `card_height()` 来 —— **同一个算式**，所以不会算错。
        """
        for sub in list(doc.subviews()):
            sub.removeFromSuperview()
        y = 0.0
        for r in load():
            entry = S["result"].get(r.course)
            card = _make_card(r, on_start=on_start, on_prep=choose_files,
                              on_drop_files=run_prep, width=WIDTH - 2 * PAD,
                              entry=entry,
                              # ⚠️ **课号要在这里绑好。** `_make_card` 只传词
                              #    （它不知道也不该知道课号之外的上下文）——
                              #    第一版直接传 `do_delete`（两个参数），调用点只给一个，
                              #    于是 TypeError 被 AppKit 吞掉、点「删」静默无效。
                              on_delete=lambda t, c=r.course: do_delete(c, t),
                              on_undo=lambda c=r.course: do_undo(c))
            card.setFrameOrigin_((0.0, y))
            doc.addSubview_(card)
            y += card_height(entry) + CARD_GAP
        doc.setFrameSize_((WIDTH - 2 * PAD, max(y, body_h)))

    # ── 长任务：拖/选完就跑，跑起来后关窗也继续 ────────────────────
    def run_prep(course: str, paths: list[str]) -> bool:
        if not paths:
            set_status("没读到文件路径 —— 看日志", 1.0)
            return False
        if S.get("busy"):
            set_status("另一门课还在跑 —— 等它完", 1.0)
            return False
        S["busy"] = True

        # ⚠️ 归档：把原件拷进 materials/（`prep.prepare` 不搬文件，只读）
        mats = (state_root / "courses" / course / "materials") if state_root \
            else paths.materials_dir(course)
        state_file = (state_root / "courses" / course / "prep-state.json") \
            if state_root else paths.prep_state(course)
        keep: list[str] = []
        try:
            mats.mkdir(parents=True, exist_ok=True)
            import shutil
            for p in paths:
                src = pathlib.Path(p)
                if src.is_file():
                    dst = mats / src.name
                    if src.resolve() != dst.resolve():
                        shutil.copy2(src, dst)
                    keep.append(str(dst))
        except OSError as e:
            set_status(f"课件归档失败：{e}", 1.0)
            S["busy"] = False
            return False

        set_status(f"{course}：抽文本 0/{len(keep)}…")

        def on_progress(stage, done, total):
            # ⚠️ 这个回调在**工作线程**里被调 —— UI 回写一律回主线程（CLAUDE.md 的不变量）
            from PyObjCTools import AppHelper
            AppHelper.callAfter(set_status, progress_text(stage, done, total))

        def work():
            try:
                # ⚠️ 真 `prepare` 的调用**只此一处**（`prepare_fn` 的签名以这里为准）。
                if prepare_fn is not None:
                    res = prepare_fn(course, keep, on_progress=on_progress)
                else:
                    import prep as prep_mod
                    from cloud_translator import load_api_key
                    res = prep_mod.prepare(
                        course, keep,
                        glossary_dir=courses.glossary_dir(glossary),
                        state_path=state_file,
                        materials_dir=mats,
                        api_key=load_api_key(None) or "",
                        on_progress=on_progress)
                # ⚠️ 存**逐条列表**，不只是一个计数 —— 卡片里要能一条一条删。
                #    「计数和列表对不上」会让用户直接失去信任（§2.5 C 两次事故），
                #    所以两边都由 `kept_added()` 从这一份数据算出来。
                S["result"][course] = {
                    "added": list(getattr(res, "added", None) or []),
                    "not_added": list(getattr(res, "not_added", None) or []),
                    "failed": list(getattr(res, "failed", None) or []),
                    "removed": set(),
                    "undo": None,
                }
                txt = summarize(res)
                if getattr(res, "aborted", False):
                    detail = getattr(res, "aborted", "")
                    txt = f"{txt}（{detail}）" if isinstance(detail, str) else txt
            except Exception as e:                        # noqa: BLE001
                txt = f"跑失败了：{type(e).__name__}: {e}"
            from PyObjCTools import AppHelper
            AppHelper.callAfter(_done, txt)

        def _done(txt):
            S["busy"] = False
            set_status(f"{course}：{txt}")
            refresh()

        threading.Thread(target=work, daemon=True).start()
        return True

    def choose_files(course: str):
        """HIG 要求的那条「不用拖」的路 —— 必须存在，不是备选。"""
        from AppKit import NSOpenPanel
        p = NSOpenPanel.openPanel()
        p.setAllowsMultipleSelection_(True)
        p.setCanChooseDirectories_(False)
        p.setMessage_(f"给 {course} 选课件")
        if p.runModal() == 1:                             # NSModalResponseOK
            run_prep(course, [str(u.path()) for u in p.URLs()])

    def do_close():
        try:
            win.orderOut_(None)
        except Exception:                                 # noqa: BLE001
            pass
        S["panel"] = None
        if on_close:
            on_close()

    # ── 删词 / 撤销（`remove_terms` / `restore_lines` 的调用方）──────────
    def glossary_of(course):
        """这门课的术语表文件 —— **与 prep 写进去的是同一个答案**。

        ⚠️ **必须走 `courses.glossary_dir(glossary)`，不能写死 `root / "glossary"`。**
           第一版就是写死的 —— 后果是**`build(glossary=…)` 这个注入点根本没生效**：
           验收跑器以为自己在改 /tmp 的副本，**实际指向的是真实的 `glossary/`**。
           2026-09-26 那次没删到真东西，纯粹是因为那个词不在真文件里（运气，不是设计）。
           → 这也是「验收隔离」本身必须被验证的原因：**隔离跑器里的路径也得有人量**。

        ⚠️ 也别自己裸 join。`prep.course_glossary_path` 的 docstring 讲了为什么：
           裸 join 会在「`.course` 存的是短代号」时写到一个 translator 永远不加载的文件上，
           而两边都报成功。
        """
        import prep as prep_mod
        return prep_mod.course_glossary_path(courses.glossary_dir(glossary), course)

    def do_delete(course, term):
        """删掉结果列表里的一条。

        ⚠️ **不做确认框**（HIG › Alerts 逐字：常见且可撤销的动作不要弹确认，
        理由是那会训练用户条件反射地点掉确认，把真正重要的那个也一起点掉）。
        改成**可撤销** —— 卡片上会出现一行「已删除 X · 撤销」。

        ⚠️ 这一步**改的是手写文件**（`glossary/<课号>.txt`），是整套东西里唯一
        会碰用户手写内容的地方。所以：走 `prep.remove_terms`（带「除被删行外逐字不变」
        的闸门），并且撤销**按原位置插回**。
        ⚠️ 同步 I/O 跑在主线程上（按钮 action 本来就在主线程）。这个文件几十行，
        毫秒级 —— 不值得为它引一套线程。
        """
        entry = S["result"].get(course)
        if entry is None:
            return
        try:
            import prep as prep_mod
            res = prep_mod.remove_terms(glossary_of(course), [term])
        except Exception as e:                            # noqa: BLE001
            set_status(f"删不掉：{type(e).__name__}: {e}", 1.0)
            return
        if not res.removed:
            set_status(f"「{term}」不在表里 —— 可能已经被删过了")
            return
        entry["removed"].add(term)
        # ⚠️ **只留最近一次删除的撤销（一层）。** 多层的代价是另一套状态机，
        #    而这里真正要防的是「手滑删错一条」—— 一层够用，而且不会撒谎。
        entry["undo"] = {"text": term,
                         "entries": list(zip(res.positions, res.removed))}
        set_status(f"已删除 {term} —— 卡片上有「撤销」")
        refresh()

    def do_undo(course):
        entry = S["result"].get(course)
        u = (entry or {}).get("undo")
        if not u:
            return
        try:
            import prep as prep_mod
            n = prep_mod.restore_lines(glossary_of(course), u["entries"])
        except Exception as e:                            # noqa: BLE001
            set_status(f"撤销失败：{type(e).__name__}: {e}", 1.0)
            return
        if not n:
            set_status("撤销没写进去 —— 看日志", 1.0)
            return
        entry["removed"].discard(u["text"])
        entry["undo"] = None
        set_status(f"已把「{u['text']}」放回原位置")
        refresh()

    from AppKit import NSButton, NSObject

    status_holder["label"] = panel.make_label(
        "拖课件到某张卡上 = 加到那门课　·　也可以点卡片上的「选择文件…」",
        NSMakeRect(PAD, PAD + 6.0, WIDTH - 2 * PAD - 100.0, 18.0), 11.0, alpha=DIM)
    ve.addSubview_(status_holder["label"])

    btn_tgt = objc_own.own("EntryClose", NSObject,
                           {"close_": lambda self, _: do_close()}).alloc().init()
    btn = NSButton.alloc().initWithFrame_(
        NSMakeRect(WIDTH - PAD - 76.0, PAD + 2.0, 76.0, 26.0))
    btn.setTitle_("关闭")
    btn.setBezelStyle_(1)
    btn.setTarget_(btn_tgt)
    btn.setAction_("close:")
    ve.addSubview_(btn)

    refresh()

    # 位置：屏幕中上（AppKit 左下原点）
    scr = NSScreen.mainScreen().frame()
    win.setFrameOrigin_(((scr.size.width - WIDTH) / 2.0,
                         max(60.0, scr.size.height - h - 140.0)))
    win.orderFrontRegardless()
    return Handles(win, do_close, refresh, set_status)


def open_panel(**kw) -> Handles | None:
    """确保同进程只有一份面板（菜单栏 + 双击两条入口都可能来）。"""
    if S.get("panel") is not None:
        S["panel"].window.orderFrontRegardless()
        return S["panel"]
    h = build(**kw)
    S["panel"] = h
    return h


def close_panel() -> None:
    h = S.get("panel")
    if h is not None:
        h.close()
