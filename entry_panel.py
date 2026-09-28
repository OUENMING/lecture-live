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

# ── 批量归档（plan §7.12）────────────────────────────────────────────
# 面板底部那条**常驻**落点条。⚠️ 常驻是刻意的：HIG › Drag and drop 逐字
# 「As much as possible, support drag and drop throughout your app」，而且
# 面板高度是按卡片数长出来的 —— **没有"空白处"可以拖**（§7.12 开头那条）。
DROP_H = 44.0
DROP_GAP = 8.0
BATCH_ROW = 24.0       # 映射表一行
BATCH_HEAD = 18.0      # 组标题（「已认出归属」/「认不出来的」）
BATCH_SEP = 15.0       # 两组之间的分隔
BATCH_CHIP_W = 138.0   # 右侧课号选择器
BATCH_UNDECIDED = "未分类"
SEARCH_W = 220.0       # 标题行右侧搜索框的宽
SEARCH_ROW = 22.0      # 一条搜索结果一行
SEARCH_LIMIT = 80      # 一次最多显示多少条（`find` 会报 `truncated`）

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


def _later(fn, *a):
    """把 UI 重建**推迟到下一轮 runloop**。

    ⚠️⚠️ **绝不能在按钮的 action 里拆掉那个按钮自己所在的视图树。**
       作者 2026-09-26 实测报的症状：**删到最后一个会卡顿 → 之后「删」和「撤销」
       都点不动 → 第二次尝试又正常**。

       机制：`NSButton` 的点击走 `NSCell` 的**跟踪循环**
       （`trackMouse:inRect:ofView:untilMouseUp:` —— 那是一个**模态事件循环**），
       我们的 action 是从那个循环**里面**被调用的。此刻 `refresh()` 把卡片
       （连同那个按钮）`removeFromSuperview` 掉 —— 跟踪循环还在栈上，
       而它的视图已经不在窗口里了，AppKit 卡在那儿。
       → **主线程被占住 → 所有按钮都没反应**（不只是被点的那个），
         这正好解释「第二次又正常」（那时点的是新建的按钮）。

       ⚠️ 同一件事的另一面：`_target` 对象**只被卡片的 `_targets` 引用**，
          而它此刻**正在执行自己的方法**。同步拆树 = 在方法执行期间把 `self` 释放掉。

       → 推迟一轮：那时跟踪循环已经退干净、按钮也不再是 sender 了。
         用 `AppHelper.callAfter`（＝ `performSelectorOnMainThread:…waitUntilDone:NO`），
         **仍在主线程**，只是晚一个 runloop 周期（毫秒级，看不出延迟）。
    """
    from PyObjCTools import AppHelper
    AppHelper.callAfter(fn, *a)


class Handles(typing.NamedTuple):
    """`build()` 的把手。**调用方必须留住它**（里面有被弱引用的 delegate/target）。"""
    window: typing.Any
    close: typing.Callable[[], None]
    refresh: typing.Callable[[], None]
    set_status: typing.Callable[..., None]
    # 批量归档的**程序化入口** —— 拖拽那条路走不到验收跑器里（没有真拖拽），
    # 而 `probe_entry_panel.py` 必须能把这条链路整个跑一遍。同 `refresh` 的理由：
    # 外部调用方需要一个戳面板的口子。⚠️ 生产路径不调它。
    start_batch: typing.Callable[[list], bool]
    # 同理：搜索那条路也走不到验收跑器里（没有真键盘输入）。
    search: typing.Callable[[str], None]


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


def abort_msg(code) -> str:
    """prep 的 abort 代号 -> **人话**。

    ⚠️ 文案的**唯一定义点是 `prep.ABORT_MSG`**（9 条，多条还带「**术语表一个字没动**」
       —— 那正是用户此刻最要知道的事）。这里只做**取值 + 兜底**，不重写文案。
    ⚠️ 兜底返回**代号本身**：宁可难看也别给空白 —— 空白会被读成"没出错"。
    """
    if not code:
        return ""
    try:
        import prep as prep_mod
        return prep_mod.ABORT_MSG.get(str(code)) or str(code)
    except Exception:                                     # noqa: BLE001
        return str(code)


def failure_text(fr) -> str:
    """一条失败记录 -> **一行人话**：`⚠️ 文件名 — 原因`。

    ⭐ **纯函数**（可单测，不用 AppKit）。喂它 `prep.FileReport`
      （`prep.py:647-654`：`path, status, chars, blocks, skipped_shapes, ocr_pages, error`）。

    ⚠️ 已有数据里就有原因 —— `entry_panel.py` 原来只 `len()` 它，所以用户看到
       「N 个文件失败」而**不知道是哪个、为什么**（`PLAN-entry-panel §3.6` 三条硬要求
       里差的那两条；调研抄的措辞是 `'Upload failed' is not a message`）。
    ⚠️ 取 `error` 优先、`status` 兜底。实测 `failed` 只会是 `empty`/`unreadable`/
       `unsupported` 三种，而这三条的 `error` **全都非空**（`extract.py:123/126/135/140`）
       → 兜底**实践中走不到**，留它是安全网。
    ⚠️ 必须**单行**：那一行的框只有 16pt 高，换行会被静默吃掉（异常串里常带换行）。
    """
    p = getattr(fr, "path", None)
    name = getattr(p, "name", None) or str(p or "").rsplit("/", 1)[-1] or "(未知文件)"
    why = (getattr(fr, "error", "") or "") or (getattr(fr, "status", "") or "")
    why = " ".join(str(why).split())
    return f"⚠️ {name} — {why}" if why else f"⚠️ {name}"


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
    if e.get("aborted"):
        # ⚠️ 头部**只给状态**，人话落在结果区的行里（`abort_msg` 那 9 条是整句，
        #    塞进这行紧凑的 `　·　` 串里会把它撑爆）。
        parts.append("⚠️ 没跑完")
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
        # ⚠️⚠️ **这里的项数必须与 `_make_card` 里那几圈行循环逐项对应** ——
        #     两处是同一件事的两个定义点，没有机制保证同步，只有
        #     `tests/test_panel.py` 那条「没有子视图超出卡片高度」能兜住（它要求
        #     夹具里**真的有**这些内容，否则兜不住）。
        #     2026-09-28 加 `failed` / `not_added` / `aborted` 三档时就是成对改的。
        n = (len(kept_added(entry))                        # 加了（每条一个「删」按钮）
             + (1 if entry.get("undo") else 0)             # 撤销
             + len(entry.get("failed") or [])              # 失败（逐文件，带原因）
             + len(entry.get("not_added") or [])           # 没加（逐条）
             + (1 if entry.get("aborted") else 0))         # 没跑完的人话
        h += RESULT_LEAD + n * RESULT_ROW
    return h


def split_verdicts(verdicts) -> tuple[list, list]:
    """`classify.suggest()` 的结果 → `(已认出归属, 认不出来的)`。

    ⚠️ **高度算式与排版循环都从这一个结果来** —— 各自 `filter` 一遍迟早对不上，
       而对不上表现为「有的行画到卡片外」，**不报错**（同 `card_height` 那条纪律）。
    ⚠️ 判据是 `v.course` 真值，不是 `source`：机械层与模型层都会给课号，
       而未分类那条路 `course` 恒为 `None`。
    """
    return ([v for v in verdicts if v.course],
            [v for v in verdicts if not v.course])


def group_for_archive(rows) -> dict:
    """`[(路径, 选择器给的课号), …]` → `{课号: [路径, …]}`。

    ⚠️⚠️ **「未分类」不进结果 —— 这就是"未分类不排队"那条规矩的唯一定义点。**
       分错课会污染那门课的术语表（之后**每一句翻译都在用错术语**，且要到课上才发现），
       所以认不出来的那些必须留在原处，一份都不许被静默归档。

    ⚠️ 空标题也算未分类（模型/机械层都答 `None` 时，选择器就停在这个字面上）。
    """
    out: dict = {}
    for path, title in rows:
        t = str(title or "").strip() or BATCH_UNDECIDED
        if t == BATCH_UNDECIDED:
            continue
        out.setdefault(t, []).append(path)
    return out


def search_card_height(n: int) -> float:
    """搜索结果卡的高度 —— **与 `_make_search_card` 的排版循环成对**。

    ⚠️ 同 `card_height` / `batch_card_height` 那条纪律：别抄固定值。
       行数是**搜出几条**决定的，写死就会溢出（且**不报错**，只是画到框外）。
    """
    return CARD_PAD + max(n, 1) * SEARCH_ROW + CARD_PAD


def _make_search_card(hits, *, width):
    """搜索结果列表。返回视图。**排一行是一条命中**。

    ⚠️ 排版一律**从顶部往下**（y 递减，卡片是非翻转坐标）—— 同 `_make_card`。
    """
    from AppKit import NSColor, NSView, NSMakeRect

    h = search_card_height(len(hits))
    inner_w = width - 2 * CARD_PAD
    view = NSView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, width, h))
    view.setWantsLayer_(True)
    view.layer().setCornerRadius_(CARD_RADIUS)
    view.layer().setBorderWidth_(HAIRLINE)
    white = NSColor.whiteColor()
    view.layer().setBorderColor_(white.colorWithAlphaComponent_(CARD_LINE_A).CGColor())
    view.layer().setBackgroundColor_(white.colorWithAlphaComponent_(CARD_FILL_A).CGColor())

    meta_w, line_w = 178.0, 46.0
    body_x = CARD_PAD + meta_w + line_w
    body_w = max(40.0, inner_w - meta_w - line_w)
    y = h - CARD_PAD
    for hit in hits or [None]:
        y -= SEARCH_ROW
        if hit is None:                                   # 空结果：说一句话，别留白板
            view.addSubview_(panel.make_label(
                "没搜到 —— 换个词试试", NSMakeRect(CARD_PAD, y + 4.0, inner_w, 16.0),
                12.0, alpha=DIM))
            continue
        who = " · ".join(x for x in (hit.course, hit.date) if x) or "—"
        view.addSubview_(panel.make_label(
            who, NSMakeRect(CARD_PAD, y + 5.0, meta_w - 8.0, 15.0), 10.0,
            alpha=DIM, truncate=True))
        view.addSubview_(panel.make_label(
            f"L{hit.line}", NSMakeRect(CARD_PAD + meta_w, y + 5.0, line_w - 6.0, 15.0),
            10.0, alpha=DIM))
        view.addSubview_(panel.make_label(
            hit.text or "", NSMakeRect(body_x, y + 4.0, body_w, 16.0), 12.0,
            truncate=True))
    return view
def batch_card_height(verdicts) -> float:
    """映射卡高度 —— **按内容累加推导**，与 `_make_batch_card` 的排版循环成对。

    ⚠️ 同样别抄一个固定值：行数是**拖进来几份**决定的，写死就会溢出（且不报错）。
    ⚠️ 与 `card_height` 一样：`batch_card_height([])` 恰好是"只有标题 + 上下内边距"，
       那条不是锁；真锁在 `tests/test_panel.py` 的「没有子视图掉出卡片底部」。
    """
    hit, left = split_verdicts(verdicts)
    # ⚠️ 每一项都必须与 `_make_batch_card` 的排版循环**逐项对应** —— 包括
    #    「`hit` 为空时**不画**那个组标题」这一条。不对应的症状是**高度算多一截
    #    而循环没画**（或反过来），卡片会空一截或把内容挤出去，都**不报错**。
    h = CARD_PAD
    if hit:
        h += BATCH_HEAD + len(hit) * BATCH_ROW
    if left:
        h += BATCH_SEP + BATCH_HEAD + len(left) * BATCH_ROW
    return h + CARD_PAD


def progress_text(stage: str, done: int, total: int) -> str:
    """进度文案。stage 是 `prep` 给的机器名，这里只做**显示**。

    ⚠️ **表只有一份，在 `prep.STAGE_NAME`。** 原来这里自己抄了一张，于是它漂了：
       `append` 文案不一致、`notes` 成了**死键**、而 `build` 落到 `names.get` 的兜底
       → **把英文原名显示给用户**（本仓库明令的「一条纪律两处定义」，`SCRIM_ALPHA` 同款）。
    ⚠️ 不认识 stage 也不要显示空白 —— 宁可显示原名，也别让人以为卡住了。
    """
    try:
        import prep as prep_mod
        names = prep_mod.STAGE_NAME
    except Exception:                                     # noqa: BLE001
        names = {}
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


def _scroll_undo_into_view(doc, course) -> bool:
    """把 `course` 那张卡的「撤销」行滚进视野。返回是否真滚了。

    ⚠️ **HIG › Undo and redo 逐字要求 `Show the results of an undo or redo.`** ——
       不滚的话，`kept_added` 有 20 条时「撤销」落在**视口之外**，用户
       **以为没生效、反复撤**（`PLAN-entry-panel §8.1 #7` 点名的那条）。
    ⚠️ 目标是**那一行可见**，不是"滚到底"：撤销行下面还有失败/没加那些行、以及
       卡片底部的按钮区 —— 滚到底会把按钮顶出视野。
    ⚠️ 坐标换算交给 AppKit（`convertRect_toView_`）：**卡片自身是非翻转的**（内容从
       卡片顶往下排），而**文档视图是翻转的**（`EntryDoc.isFlipped`）—— 手算这个
       必然错，别自己加加减减。
    """
    for card in doc.subviews():
        if getattr(card, "_course", None) != course:
            continue
        lbl = getattr(card, "_undo_lbl", None)
        if lbl is None:
            return False
        try:
            doc.scrollRectToVisible_(lbl.convertRect_toView_(lbl.bounds(), doc))
            return True
        except Exception as e:                            # noqa: BLE001
            # 滚不动不影响"删除已经生效"这件事 —— 说一句就够，别把它变成失败
            print(f"⚠ 滚到「撤销」行失败({type(e).__name__}: {str(e)[:50]})；"
                  f"删除本身已生效", flush=True)
            return False
    return False


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
        # ⚠️ 判据是「**摊平后有能抽的**」（`extract.expand` 走 `is_supported` ——
        #    支持集的唯一定义点，与流水线共用一份），不是「拖进来一个非空文件列表」。
        #    原来只看 `bool(panel.file_paths(pb))` → **文件夹、`.txt`、`.docx` 全都高亮
        #    说"能收"**，松手才在 `prep` 里判 unsupported（`REVIEW §9.2 #5`）。
        #    ⚠️ 2026-09-28 起走 `expand()`：**文件夹取一层**，两个入口行为一致
        #    （落点条那条也用它；两处不一致比不支持更糟）。
        #    ⚠️ 拒的时候**也要不高亮**：HIG 逐字要求"收不了时给显式反馈（`circle.slash`）、
        #       **别给高亮**" —— 只改返回值会留下"高亮着但收不了"。
        from extract import expand as _expand
        paths = panel.file_paths(pb) or []
        ok = bool(_expand(paths)[0])
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
        # 「没跑完」的人话紧跟头部 —— **它是那一屏的头版**（读序上不该被埋在最后）。
        # ⚠️ 与 `card_height` 的项数**成对**，见那里的注。
        if entry.get("aborted"):
            y -= RESULT_ROW
            view.addSubview_(panel.make_label(
                abort_msg(entry["aborted"]),
                NSMakeRect(CARD_PAD, y + 3.0, inner_w, 16.0), 11.0, alpha=DIM,
                truncate=True))
        for t in kept_added(entry):
            y -= RESULT_ROW
            view.addSubview_(panel.make_label(
                t, NSMakeRect(CARD_PAD, y + 3.0, inner_w - 52.0, 16.0), 12.0,
                truncate=True))               # ⚠️ 长词条名会被静默换行吃掉第二行
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
            _undo_lbl = panel.make_label(
                f"已删除 {undo.get('text', '')}",
                NSMakeRect(CARD_PAD, y + 3.0, inner_w - 76.0, 16.0), 11.0, alpha=DIM,
                truncate=True)
            view.addSubview_(_undo_lbl)
            # ⚠️ **存下来**：删完之后要把它滚进视野 —— 见 `_scroll_undo_into_view`。
            #    只留最近一次删除的撤销（一层），所以一个卡片一个引用就够。
            view._undo_lbl = _undo_lbl
            view.addSubview_(mk("撤销", width - CARD_PAD - 68.0,
                                on_undo or (lambda: None),
                                y=y + 1.0, w=68.0, h=RESULT_ROW - 6.0))
        # ⚠️ 下面两组**没有按钮**（它们不是可操作项，是要看的信息），所以能用满 `inner_w`。
        # ⚠️ 都传 `truncate=True` —— 不传的话默认是 `wraps=True`，超长文本会被**静默换行
        #    吃掉第二行**（`panel.make_label` 的 docstring 记了这条实测）。
        for fr in (entry.get("failed") or []):
            y -= RESULT_ROW
            view.addSubview_(panel.make_label(
                failure_text(fr),
                NSMakeRect(CARD_PAD, y + 3.0, inner_w, 16.0), 11.0, alpha=DIM,
                truncate=True))
        for t in (entry.get("not_added") or []):
            y -= RESULT_ROW
            view.addSubview_(panel.make_label(
                t, NSMakeRect(CARD_PAD, y + 3.0, inner_w, 16.0), 12.0,
                truncate=True))
        # 排完之后 y 应当**恰好**等于 `CARD_PAD + BTN_H`（`card_height` 的算式保证）。
        # 对不上就是算式与坐标漂了 —— 那正是 `tests/test_panel.py` 第 ⑥ 组在量的东西。

    return view


def _make_batch_card(verdicts, *, width, course_names):
    """待确认的映射表。返回 `(view, rows)`，`rows = [(路径, 选择器), …]`。

    两组：**已认出归属** / **认不出来的**（分隔线 + 降透明度）。

    ⚠️⚠️ **「认不出来的」那组不许预填猜测。** `[论文]` AAAI-22（Bondi et al.）四组
       消息对照实测，逐字：「participants are **significantly less accurate when the
       prediction is shown**」（57.8% vs 60.2%，**p = 0.003**）、「exposing humans to
       **incorrect** predictions of an AI makes them **even more likely to be
       incorrect**」。机制怀疑是 anchoring。
       我们这里"认不出来"的那些**恰恰最可能是模型会猜错的**，所以那一组的选择器
       一律停在「未分类」，理由那列照写原因，**不写"我猜是 X"**。

    ⚠️ 排版一律**从顶部往下**（y 递减，卡片是非翻转坐标）—— 同 `_make_card` 那条；
       反过来堆会让列表倒过来（那次事故的说明在 `_make_card` 里）。
    """
    from AppKit import (NSBox, NSBoxSeparator, NSColor, NSFont, NSPopUpButton,
                        NSView, NSMakeRect)

    hit, left = split_verdicts(verdicts)
    h = batch_card_height(verdicts)
    inner_w = width - 2 * CARD_PAD
    white = NSColor.whiteColor()

    view = NSView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, width, h))
    view.setWantsLayer_(True)
    view.layer().setCornerRadius_(CARD_RADIUS)
    view.layer().setBorderWidth_(HAIRLINE)
    view.layer().setBorderColor_(
        white.colorWithAlphaComponent_(CARD_LINE_A).CGColor())
    view.layer().setBackgroundColor_(
        white.colorWithAlphaComponent_(CARD_FILL_A).CGColor())

    # 三列：文件名 | 理由 | 课号。理由列给足宽度 —— 它是作者复核**唯一的依据**，
    # 挤成 7 个字（等于看不见）就白做了。
    chip_x = CARD_PAD + inner_w - BATCH_CHIP_W
    why_w = 150.0
    why_x = chip_x - 10.0 - why_w
    name_w = max(40.0, why_x - CARD_PAD - 12.0)

    rows: list = []
    y = h - CARD_PAD

    def head(text):
        nonlocal y
        y -= BATCH_HEAD
        view.addSubview_(panel.make_label(
            text, NSMakeRect(CARD_PAD, y, inner_w, BATCH_HEAD - 2.0), 11.0, alpha=DIM))

    def row(v, dim):
        nonlocal y
        y -= BATCH_ROW
        view.addSubview_(panel.make_label(
            pathlib.Path(v.path).name,
            NSMakeRect(CARD_PAD, y + 4.0, name_w, 16.0), 12.0,
            alpha=(DIM if dim else 1.0), truncate=True))
        view.addSubview_(panel.make_label(
            v.why, NSMakeRect(why_x, y + 5.0, why_w, 15.0), 10.0,
            alpha=DIM * 0.9, truncate=True))
        # ⚠️ 选择器**从不预设"最佳猜测"** —— 见 docstring 那条 AAAI-22。
        p = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(chip_x, y + 1.5, BATCH_CHIP_W, 21.0), False)
        p.addItemsWithTitles_([BATCH_UNDECIDED] + list(course_names))
        want = (0 if not v.course else
                (list(course_names).index(v.course) + 1
                 if v.course in list(course_names) else 0))
        p.selectItemAtIndex_(want)
        p.setFont_(NSFont.systemFontOfSize_(11.0))
        p.setControlSize_(1)                                       # small
        view.addSubview_(p)
        rows.append((v.path, p))

    if hit:
        head("已认出归属")
        for v in hit:
            row(v, dim=False)
    if left:
        y -= BATCH_SEP
        sep = NSBox.alloc().initWithFrame_(
            NSMakeRect(CARD_PAD, y + BATCH_SEP / 2.0, inner_w, 1.0))
        sep.setBoxType_(NSBoxSeparator)
        view.addSubview_(sep)
        head("认不出来的")
        for v in left:
            row(v, dim=True)

    return view, rows


def build(*, on_start=None, glossary=None, sessions_dir=None, state_root=None,
          on_close=None, prepare_fn=None, suggest_fn=None) -> Handles | None:
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
                      state_root=state_root, on_close=on_close, prepare_fn=prepare_fn,
                      suggest_fn=suggest_fn)
    except objc_own.ObjcNameCollision:
        raise
    except Exception:                                     # noqa: BLE001
        if os.environ.get("CLASSLIVE_DEBUG"):
            import traceback
            traceback.print_exc()
        return None


def _build(*, on_start, glossary, sessions_dir, state_root, on_close,
           prepare_fn=None, suggest_fn=None) -> Handles:
    from AppKit import (NSButton, NSColor, NSFont, NSScreen, NSSearchField,
                        NSWindowStyleMaskBorderless, NSWindowStyleMaskNonactivatingPanel)
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
    # ⚠️ 高度**从下往上推**：页脚 → 落点条 → 滚动区 → 标题。改任何一条边距时，
    #    `h` 与视图的 y **用的是同一组常数**，不是各写一遍（`card_height` 那条纪律）。
    drop_y = PAD + FOOT_H + DROP_GAP
    sc_y = drop_y + DROP_H + DROP_GAP
    h = sc_y + body_h + 8.0 + TITLE_H + PAD

    mask = NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel
    fp = panel.build(NSMakeRect(0.0, 0.0, WIDTH, h), mask)
    win, ve = fp.window, fp.glass
    # ⚠️ borderless 默认没有阴影（Titled 的 overlay 才有）—— 同 whatsnew 的处理，
    #    留在调用点、不进 panel.py 的配方。
    win.setHasShadow_(True)

    # 标题在批量模式下会换字 —— 存进 holder，别让 `refresh()` 摸不到它。
    # ⚠️ 2026-09-28 起标题右边多了个搜索框，所以标题**只占左边那段**。
    title_lbl = panel.make_label(
        "开课前的准备", NSMakeRect(PAD, h - PAD - TITLE_H + 6.0,
                                 WIDTH - 2 * PAD - SEARCH_W - 10.0, TITLE_H - 6.0),
        15.0, bold=True)
    ve.addSubview_(title_lbl)

    # ── 标题行右侧：全局搜索（`find.search()`，plan §2.3）──────────────
    # ⚠️⚠️ **这是本面板第一个文本输入控件** —— 在此之前它从不变成 key window。
    #    于是「面板赖在 key 上、吃掉用户在别的 app 里按的键」这条**从今天起才存在**。
    #    解法照 `overlay` 那条**不变量**（它逐字写着「用不变量兜住所有路径,
    #    而不是逐个交互点打补丁」）：**只有真的在编辑时才允许是 key**。
    #    ⚠️ `overlay` 靠每帧 `pump()` 跑它，本面板没有 pump → 用轮询定时器。
    search_field = NSSearchField.alloc().initWithFrame_(
        NSMakeRect(PAD + WIDTH - 2 * PAD - SEARCH_W, h - PAD - TITLE_H + 4.0,
                   SEARCH_W, TITLE_H - 10.0))
    search_field.setPlaceholderString_("搜索转录 / 笔记…")
    search_field.setFont_(NSFont.systemFontOfSize_(12.0))
    search_field.setTarget_(_target(lambda: run_search(search_field.stringValue())))
    search_field.setAction_("act:")            # NSSearchField 的回车走 action
    search_field.setSendsWholeSearchString_(True)   # 回车才发，别边打边搜
    ve.addSubview_(search_field)

    # ── 卡片区（可滚动；今天 5 门课用不到，但第 6 门不该引发断崖）──────
    from AppKit import NSView, NSViewWidthSizable
    doc = _flipped_doc_class().alloc().initWithFrame_(
        NSMakeRect(0.0, 0.0, WIDTH - 2 * PAD, max(body_h, len(rs) * (CARD_H + CARD_GAP))))
    doc.setAutoresizingMask_(NSViewWidthSizable)
    sc = panel.make_scroll_view(NSMakeRect(PAD, sc_y, WIDTH - 2 * PAD, body_h))
    sc.setDocumentView_(doc)
    ve.addSubview_(sc)

    # ── 底部常驻落点条：批量归档的入口（plan §7.12）────────────────────
    # ⭐ 常驻而不是"先点按钮再弹一个拖拽框"：HIG › Drag and drop 逐字
    #    「As much as possible, support drag and drop throughout your app」；
    #    而 HIG › Modality 逐字「Present content modally only when there's a
    #    clear benefit」判了那个弹框死刑 —— 它既不是 critical information 也不是 options。
    # ⚠️ 面板高度是按卡片数长出来的，**没有"空白处"可以拖** —— 所以这条得自己占位。
    strip_holder: dict = {"view": None, "hint": [], "actions": []}
    # 映射表的 `[(路径, 选择器), …]` —— ⚠️ **不能挂在卡片视图上**：`_make_batch_card`
    # 返回的是纯 `NSView`，而纯 ObjC 对象**不接受任意 Python 属性**
    # （`card._rows = rows` 会 AttributeError，而它被 `callAfter` 吞掉 → 表永远不出现。
    #  卡片那条路能挂 `_targets` 是因为 `make_drop_target` 返回的是 `objc_own` 造的
    #  **Python 子类**）。放这里，作用域与面板同生共死。
    batch_rows: list = []
    W_IN = WIDTH - 2 * PAD

    def _strip_bg(alpha):
        v = strip_holder.get("view")
        if v is not None:
            try:
                v.layer().setBackgroundColor_(
                    NSColor.whiteColor().colorWithAlphaComponent_(alpha).CGColor())
            except Exception:                                 # noqa: BLE001
                pass

    def _drop_enter(pb):
        # ⚠️ 判据是「**摊平后有能抽的**」（`extract.expand` 走 `is_supported`，
        #    那是"能不能收"的唯一定义点）—— 与卡片同一条。
        #    原来只看 `bool(file_paths(pb))` → 文件夹、`.txt` 全高亮说"能收"，
        #    松手才在 prep 里判 unsupported（`REVIEW §9.2 #5`）。
        from extract import expand as _expand
        paths = panel.file_paths(pb) or []
        ok = bool(_expand(paths)[0])
        _strip_bg(HILITE_A if ok else CARD_FILL_A)
        return ok

    def _drop_exit():
        _strip_bg(CARD_FILL_A)

    def _drop_batch(paths):
        _strip_bg(CARD_FILL_A)
        return run_batch(paths)

    strip = panel.make_drop_target(_drop_enter, _drop_batch, _drop_exit)
    strip.setFrame_(NSMakeRect(PAD, drop_y, W_IN, DROP_H))
    strip.setWantsLayer_(True)
    strip.layer().setCornerRadius_(CARD_RADIUS)
    strip.layer().setBorderWidth_(HAIRLINE)
    strip.layer().setBorderColor_(
        NSColor.whiteColor().colorWithAlphaComponent_(CARD_LINE_A).CGColor())
    strip_holder["view"] = strip
    _strip_bg(CARD_FILL_A)
    ve.addSubview_(strip)

    strip_holder["hint"] = [
        panel.make_label("把一堆课件拖到这里 · 自动分到各课",
                         NSMakeRect(CARD_PAD, DROP_H - 22.0, W_IN - 140.0, 17.0), 12.0),
        panel.make_label("文件夹也行（取里面一层）",
                         NSMakeRect(CARD_PAD, DROP_H - 38.0, W_IN - 140.0, 14.0),
                         10.0, alpha=DIM),
    ]
    for _v in strip_holder["hint"]:
        strip.addSubview_(_v)

    def _pick_batch_files():
        from AppKit import NSOpenPanel
        p = NSOpenPanel.openPanel()
        p.setAllowsMultipleSelection_(True)
        p.setCanChooseDirectories_(True)                  # 文件夹走 expand()
        p.setMessage_("选一堆课件 —— 会自动分到各课")
        if p.runModal() == 1:                             # NSModalResponseOK
            run_batch([str(u.path()) for u in p.URLs()])

    _pick_btn = NSButton.alloc().initWithFrame_(
        NSMakeRect(W_IN - CARD_PAD - 104.0, (DROP_H - BTN_H) / 2.0, 104.0, BTN_H))
    _pick_btn.setTitle_("选择文件…")
    _pick_btn.setBezelStyle_(1)
    _pick_btn.setFont_(NSFont.systemFontOfSize_(12.0))
    _pick_t = _target(_pick_batch_files)
    _pick_btn.setTarget_(_pick_t)                         # ⚠️ 弱引用 —— 靠这里留
    _pick_btn.setAction_("act:")
    strip._targets = [_pick_t]
    strip.addSubview_(_pick_btn)
    strip_holder["hint"].append(_pick_btn)

    def _batch_buttons():
        """批量模式下的动作区 —— **与提示**同一块地方**换着显示**（位置固定、永远看得见）。

        ⚠️ 不放进映射卡里：卡比视口高时按钮会滚出屏幕。
        """
        out = []
        for title, fn, x, w, primary in (
                ("取消", _cancel_batch, W_IN - CARD_PAD - 100.0, 100.0, False),
                ("确认", _confirm_batch, W_IN - CARD_PAD - 100.0 - 8.0 - 100.0,
                 100.0, True)):
            b = NSButton.alloc().initWithFrame_(
                NSMakeRect(x, (DROP_H - BTN_H) / 2.0, w, BTN_H))
            b.setTitle_(title)
            b.setBezelStyle_(1)
            b.setFont_(NSFont.systemFontOfSize_(12.0))
            t = _target(fn)
            b.setTarget_(t)
            b.setAction_("act:")
            b.setHidden_(True)
            strip.addSubview_(b)
            out.append((b, t))
        return out

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

    # ── 键盘焦点：**只允许在真的编辑时持有**（不变量，抄 overlay）────────
    def _is_editing() -> bool:
        """面板现在是 key，是不是因为在输入框里编辑？

        ⚠️ 判据抄 `overlay._is_editing`：**field editor 存在 = 正在编辑**。
           `NSTextField` 拿到焦点时 first responder 是它的 field editor
           （一个 `NSTextView`），**不是控件自己**。
        """
        try:
            fr = win.firstResponder()
            return bool(fr) and fr is not win and bool(fr.isFieldEditor())
        except Exception:                                     # noqa: BLE001
            return False

    def _release_focus() -> None:
        """把键盘还出去。

        ⚠️ 两个调用**分开 try** —— 合在一起时前一个抛错会连后一个都不做
           （`overlay._release_focus` 的注释逐字记过这条）。
        """
        try:
            win.makeFirstResponder_(None)
        except Exception:                                     # noqa: BLE001
            pass
        try:
            win.resignKeyWindow()
        except Exception:                                     # noqa: BLE001
            pass

    def _focus_guard() -> None:
        """⭐ 不变量：**只有真的在编辑时，面板才允许是 key**。

        ⚠️ 为什么是**不变量**而不是逐点补：点面板任何非控件区域（卡片空白、落点条、
           标题行）都会让它变 key 并赖着，于是用户在别的 app 里按的键被吃掉。
           逐点补只能覆盖已知的那几处（`overlay` 的独立验证实测过四条路径都中招）。
        ⚠️ `overlay` 靠每帧 `pump()` 跑这条；本面板**没有 pump 循环**，所以用
           0.5 秒定时器。代价是"最多赖 0.5 秒"，换来的好处是不必给这个面板再造一个 pump。
        ⚠️ 窗口一关就**不再续期** —— 否则定时器会跟着进程活到天荒地老。
        """
        try:
            if not win.isVisible():
                return
            if win.isKeyWindow() and not _is_editing():
                _release_focus()
        except Exception:                                     # noqa: BLE001
            pass
        from PyObjCTools import AppHelper
        AppHelper.callLater(0.5, _focus_guard)

    _focus_guard()

    # ── 全库搜索（`find.search`，plan §2.3）──────────────────────────
    def run_search(q: str):
        """标题行搜索框回车 → 全库搜。**只读**，不改任何文件。"""
        q = (q or "").strip()
        if not q:
            S["search"] = None
            set_status("已清除搜索", 1.0)
            _later(refresh)
            return
        S["search"] = {"q": q, "hits": [], "total": 0, "truncated": False,
                       "busy": True, "err": ""}
        set_status(f"搜「{q}」…")
        _later(refresh)

        def work():
            try:
                import find as find_mod
                r = find_mod.search(q, limit=SEARCH_LIMIT)
                got = {"hits": r.hits, "total": r.total, "truncated": r.truncated}
            except Exception as e:                            # noqa: BLE001
                got = {"hits": [], "total": 0, "truncated": False,
                       "err": f"{type(e).__name__}: {e}"}
            s = S.get("search")
            if s is None or s.get("q") != q:
                return                                        # 期间又搜了别的
            s.update(got)
            s["busy"] = False
            from PyObjCTools import AppHelper
            AppHelper.callAfter(_search_ready)

        threading.Thread(target=work, daemon=True).start()

    def _search_ready():
        s = S.get("search")
        if s is None:
            return
        if s.get("err"):
            set_status(f"搜索失败：{s['err']}", 1.0)
        else:
            # ⚠️ 截断了就说出来 —— 别让一个被截断的结果读起来像"就这些"
            #    （`minutes` 的 retrieval.rs 逐字：截断会「let a negative result
            #    appear exhaustive when it is not」）。
            more = (f" · 只显示前 {len(s['hits'])} 条" if s.get("truncated") else "")
            set_status(f"「{s['q']}」命中 {s['total']} 处{more}", 1.0)
        cur = S.get("panel")
        if cur is not None:
            cur.refresh()

    # ── 批量归档（plan §7.12）────────────────────────────────────────
    def run_batch(paths):
        """拖/选一堆文件 → 后台分类 → 映射卡。**只出建议，不写盘**（写盘归 `prep`）。"""
        if S.get("batch") is not None:
            set_status("已经在核对这一批了 —— 先确认或取消", 1.0)
            return False
        from extract import expand as _expand
        files, dropped = _expand(paths)
        if not files:
            set_status("这些都不收（只认 PDF / PPTX / DOCX；文件夹取里面一层）", 1.0)
            return False
        names = courses.list_courses(glossary, state_root=state_root)
        S["batch"] = {"verdicts": [], "busy": True, "total": len(files),
                      "dropped": len(dropped), "error": ""}
        title_lbl.setStringValue_(f"准备归档 {len(files)} 份课件")
        _later(refresh)
        set_status(f"扫描 0/{len(files)}…")

        def work():
            got, err = [], ""
            try:
                import classify
                from cloud_translator import load_api_key
                key = load_api_key(None)
                briefs = classify.briefs(courses.glossary_dir(glossary), names)
                ask = classify.make_ask(key) if key else (lambda prompt: None)

                def prog(i, n, name):
                    # ⚠️ 在工作线程里被调 —— UI 回写一律回主线程（CLAUDE.md 的不变量）。
                    #    用 `_status`（查**当前**面板），不要捕获本代的 `set_status`。
                    from PyObjCTools import AppHelper
                    AppHelper.callAfter(_status, f"扫描 {i}/{n}：{name[:38]}")

                # ⚠️ `suggest_fn` 是**验收用的注入点**（同 `prepare_fn` 那条）——
                #    验收批量那条路时真跑会调 DeepSeek 花钱，所以要能换掉。
                suggest = suggest_fn or classify.suggest
                got = suggest(files, courses=names, course_briefs=briefs,
                              head_of=classify.head_of, ask=ask,
                              on_progress=prog)
            except Exception as e:                            # noqa: BLE001
                err = f"{type(e).__name__}: {e}"
            b = S.get("batch")
            if b is None:                                     # 期间被取消了
                return
            b["verdicts"], b["busy"], b["error"] = got, False, err
            from PyObjCTools import AppHelper
            AppHelper.callAfter(_batch_ready)

        threading.Thread(target=work, daemon=True).start()
        return True

    def _batch_ready():
        b = S.get("batch")
        if b is None:
            return
        if b.get("error"):
            set_status(f"扫描失败：{b['error']}", 1.0)
        else:
            hit = len([v for v in b["verdicts"] if v.course])
            tail = f"（另有 {b['dropped']} 份格式不收，没进表）" if b.get("dropped") else ""
            set_status(f"认出 {hit} 份，{len(b['verdicts']) - hit} 份认不出来 —— "
                       f"核对后点确认{tail}", 1.0)
        cur = S.get("panel")
        if cur is not None:
            cur.refresh()

    def _cancel_batch():
        S["batch"] = None
        set_status("已取消 —— 什么都没动", 1.0)
        _later(refresh)

    def _confirm_batch():
        """把映射表上的选择**分组、排队**，然后踢第一门。

        ⚠️ **未分类的不进队列** —— 它们留在原处，一份都不会被静默归档。
        ⚠️ 队列由 `_done` 逐门排空（复用 `busy` 当串行器），**这里不自己跑 `prepare`** ——
           那会把这批"结果记进 `S['result']`"的逻辑抄第二份。
        """
        rows = list(batch_rows)
        pairs = []
        for path, popup in rows:
            try:
                pairs.append((path, popup.titleOfSelectedItem()))
            except Exception:                                 # noqa: BLE001
                pairs.append((path, BATCH_UNDECIDED))
        # ⚠️ 分组走**纯函数** —— 「未分类不排队」那条规矩在 `group_for_archive` 里，
        #    只有一处定义，也只有那一处需要判据。
        by_course = group_for_archive(pairs)
        S["batch"] = None
        if not by_course:
            set_status("一份都没归课 —— 什么都没动", 1.0)
            _later(refresh)
            return
        for _p, _popup in rows:
            try:
                _popup.removeFromSuperview()
            except Exception:                                 # noqa: BLE001
                pass
        S["queue"] = sorted(by_course.items())                # 顺序稳定（按课号）
        n_course, n_file = len(S["queue"]), sum(len(v) for v in by_course.values())
        set_status(f"开始跑 {n_course} 门课 / {n_file} 份课件…", 1.0)
        _later(refresh)
        run_prep(*S["queue"].pop(0))

    strip_holder["actions"] = _batch_buttons()                # 建在函数定义之后

    # 搜索模式下那一块的动作区（**位置与批量那对完全重合** —— 换着显示，不同时出现）。
    _clr = NSButton.alloc().initWithFrame_(
        NSMakeRect(W_IN - CARD_PAD - 100.0, (DROP_H - BTN_H) / 2.0, 100.0, BTN_H))
    _clr.setTitle_("清除")
    _clr.setBezelStyle_(1)
    _clr.setFont_(NSFont.systemFontOfSize_(12.0))
    _clr_t = _target(lambda: run_search(""))
    _clr.setTarget_(_clr_t)                                   # ⚠️ 弱引用 —— 靠这里留
    _clr.setAction_("act:")
    _clr.setHidden_(True)
    strip.addSubview_(_clr)
    strip_holder["search_btns"] = [(_clr, _clr_t)]

    def refresh():
        """重画（跑完 prep / 删词 / 撤销 / 批量扫描完 都走它）。

        ⚠️ **一处重建，不做增量打补丁。** 每张卡的高度不一样（有结果列表的更高），
           增量改高度是布局 bug 的温床；整块重画只有 ~10 张卡，代价可以忽略。
        ⚠️ 高度与坐标都从 `card_height()` / `batch_card_height()` 来 —— **同一个算式**，
           所以不会算错。
        ⚠️ 它同时负责**切换落点条的两个状态**（提示 ↔ 批量动作）—— 那是唯一需要
           "面板级"而非"卡片级"改动的东西，塞在这里比另开一条更新路径省事。
        """
        for sub in list(doc.subviews()):
            sub.removeFromSuperview()

        b = S.get("batch")
        s = S.get("search")
        for _v in strip_holder["hint"]:
            _v.setHidden_(b is not None or s is not None)
        for _btn, _t in strip_holder["actions"]:
            _btn.setHidden_(b is None)
        for _btn, _t in strip_holder.get("search_btns", []):
            _btn.setHidden_(s is None)

        if s is not None:
            # ── 搜索模式：卡片列表换成命中列表 ────────────────────────
            # ⚠️ 与批量模式**互斥**：`run_search` 不清 `S["batch"]`，但 `run_batch`
            #    也不清 `S["search"]`。两条路都从自己的入口进，不会同时开 ——
            #    真同时开了，这里是 search 优先，`strip` 会把批量那对按钮也亮着，
            #    所以上面那两圈 hidden 是**分开判**的（各按各的）。
            title_lbl.setStringValue_(f"搜索：{s.get('q', '')}")
            if s.get("busy"):
                doc.setFrameSize_((WIDTH - 2 * PAD, max(body_h, 120.0)))
                return
            card = _make_search_card(s.get("hits") or [], width=WIDTH - 2 * PAD)
            card.setFrameOrigin_((0.0, 0.0))
            doc.addSubview_(card)
            doc.setFrameSize_((WIDTH - 2 * PAD,
                               max(body_h, card.frame().size.height)))
            return

        if b is not None:
            # ── 批量核对模式：卡片列表整块换成映射表 ──────────────────
            title_lbl.setStringValue_(f"准备归档 {b.get('total', 0)} 份课件")
            if b.get("busy") or not b.get("verdicts"):
                doc.setFrameSize_((WIDTH - 2 * PAD, max(body_h, 120.0)))
                return
            card, rows = _make_batch_card(
                b["verdicts"], width=WIDTH - 2 * PAD,
                course_names=courses.list_courses(glossary, state_root=state_root))
            # ⚠️ **原地改 `batch_rows`，不要重新绑定** —— `_confirm_batch` 闭包抓的是
            #    同一个列表对象。写 `batch_rows = rows` 会让它变成 `refresh` 的局部变量，
            #    而 `_confirm_batch` 永远看到空表（且**不报错**，只是点了没反应）。
            batch_rows[:] = rows
            card.setFrameOrigin_((0.0, 0.0))
            doc.addSubview_(card)
            doc.setFrameSize_((WIDTH - 2 * PAD,
                               max(body_h, card.frame().size.height)))
            return

        # ── 正常模式：课程卡片 ────────────────────────────────────────
        title_lbl.setStringValue_("开课前的准备")
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
        # ⚠️ 滚**必须在重画之后**：上面那圈把旧卡片全 `removeFromSuperview` 了，
        #    所以只能拿**新**卡片的 `_undo_lbl`。意图是**一次性**的（`pop` 掉）。
        _want = S.pop("_undo_scroll", None)
        if _want:
            _scroll_undo_into_view(doc, _want)

    # ── 长任务：拖/选完就跑，跑起来后关窗也继续 ────────────────────
    def run_prep(course: str, files: list) -> bool:
        """拖进来的（或选中的）课件 → 跑 prep。

        ⚠️⚠️ **参数名叫 `files` 不是 `paths`** —— 第一版叫 `paths`，把模块 `paths`
           **遮蔽**了，于是 `paths.materials_dir(course)`（`state_root` 为空那支）
           变成「对列表取属性」→ `AttributeError`。而它发生在 `S["busy"] = True`
           **之后** → 那个异常一旦逃出，`busy` 永远卡 True，**这个进程再也拖不动**。
           ⚠️ 更毒的是：它在拖拽路径上被 `panel.make_drop_target` 的异常守卫吞掉
           （只在 `CLASSLIVE_DEBUG` 下出声）→ 松手后**毫无反应**。
           **验收跑器一直传 `state_root=ISO`，所以这条生产路径（`state_root=None`）
           从来没被跑到过。** → 隔离跑器会把「生产路径」整个绕过。
        """
        if not files:
            set_status("没读到文件路径 —— 看日志", 1.0)
            return False
        if S.get("busy"):
            set_status("另一门课还在跑 —— 等它完", 1.0)
            return False
        S["busy"] = True

        # ⚠️⚠️ **面板不做归档 —— 那是 `prep._archive` 的活，别在这里再写一份。**
        #    第一版自己 `shutil.copy2` 拷进 `materials/`，两个后果：
        #    ① 它是**无条件覆盖**，而 `_archive` 的纪律是「同名但大小不同的加 `-2`
        #       后缀，**覆盖等于静默丢掉上一份课件**」→ 从面板拖入同名不同内容的
        #       第二份 → 上一份**无痕消失**；
        #    ② 它跑在 `prepare` **之前**，于是 `_archive` 看到的文件已经在归档目录里
        #       → 走「已在归档目录」分支 → **它那道保护永远触发不到**。
        #    → 直接把**源路径**交给 `prepare`（它自己 `_archive`，`prep.py:848`）。
        mats = paths.materials_dir(course, root=state_root)
        state_file = paths.prep_state(course, root=state_root)
        keep = [str(p) for p in files]

        set_status(f"{course}：抽文本 0/{len(keep)}…")

        def on_progress(stage, done, total):
            # ⚠️ 这个回调在**工作线程**里被调 —— UI 回写一律回主线程（CLAUDE.md 的不变量）
            # ⚠️ 用 `_status`（查**当前**面板），不要捕获本代的 `set_status` —— 见它的说明。
            from PyObjCTools import AppHelper
            AppHelper.callAfter(_status, progress_text(stage, done, total))

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
                    # ⚠️ abort 代号**存进 entry**：原来只进临时状态行，面板一关就没了。
                    #    存下来卡片才**持久**说得清"为什么没跑完"（人话在结果区的行里）。
                    "aborted": str(getattr(res, "aborted", "") or ""),
                }
                txt = summarize(res)
                if getattr(res, "aborted", False):
                    # ⚠️ 人话优先 —— 原来直接把代号拼上去，用户看到 `all_files_failed`
                    #    这种机器名（`§9.2 #5`）。
                    txt = f"{txt}（{abort_msg(getattr(res, 'aborted', ''))}）"
            except Exception as e:                        # noqa: BLE001
                txt = f"跑失败了：{type(e).__name__}: {e}"
            from PyObjCTools import AppHelper
            AppHelper.callAfter(_done, txt)

        def _done(txt):
            S["busy"] = False
            # ⚠️ 写给 / 重画**当前**打开的面板 —— 可能已经不是发起这次跑的那一个了。
            _status(f"{course}：{txt}")
            cur = S.get("panel")
            if cur is not None:
                cur.refresh()
            # ── 批量：接着踢下一门（plan §7.12）─────────────────────────
            # ⭐ 上面那句 `S["busy"] = False` 就是**串行器**：`run_prep` 开头那句
            #    `if S["busy"]: return False` 保证一次只跑一门。这里只要在它清空
            #    **之后**接着踢，不必另写一套并发控制，也不必自己再跑一遍
            #    `prep.prepare`（那会把"结果记进 S['result']"的逻辑抄第二份）。
            # ⚠️ 此时面板可能已经关了（`cur is None`）—— 队列照跑，只是不重画。
            queue = S.get("queue") or []
            if queue:
                nxt = queue.pop(0)
                if not queue:
                    S.pop("queue", None)                  # 排空了就撤掉那个键
                run_prep(*nxt)

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
        # ⚠️⚠️ **必须断环，否则每开/关一次漏掉一整个面板。**
        #     实测（审查代理量的，连跑两遍一致）：开/关 3 轮之后
        #     `{'Panel': 3, 'DropTarget': 15, 'EntryBtn': 18, 'EntryDoc': 3}`，
        #     `NSApp.windows()` 仍是 3 —— 关闭后面板不可见，但**整棵树都还活着**，
        #     连两次 `gc.collect()` 都收不回。
        #     环在哪：`win._entry_targets` → `target._fn = do_close` → 闭包持有 `win`；
        #     每张卡的 `_targets` → `_fn` = refresh/do_delete/do_undo → 持有 doc/ve/win。
        #     面板每次「开课前的准备」都要开关 → 一学期累积**无上界**。
        try:
            for _card in doc.subviews():
                _card._targets = []          # 断卡片 → target → 闭包 → win 那条
                # ⚠️ **落点回调也必须断。** 只清 `_targets` 不够 —— 还有一圈：
                #    卡片 → `_on_drop` → `run_prep` → `set_status` → 状态标签
                #    → 它的 superview(`ve`) → 子树 → 卡片。那一圈不含 `win`
                #    （所以窗口能走），但它自己是个**孤岛**，`gc` 收不回
                #    （实测：只 `setContentView_(None)` 时 3 轮后仍有 16 个 DropTarget）。
                _card._on_enter = _card._on_drop = _card._on_exit = None
            win._entry_targets = []          # 断关闭按钮那条
            status_holder["label"] = None    # 断 `set_status` → 标签 → `ve` 那条
            # ⚠️ 落点条（批量入口）也要断 —— 它的 `_targets` 里那个 target 的
            #    `_fn` 是 `run_batch` → `_later(refresh)` → 闭包持有 `title_lbl`/`doc`
            #    → `ve` → `win`。与卡片那条**同一条环**，只是入口不同。
            strip._targets = []
            strip._on_enter = strip._on_drop = strip._on_exit = None
            # ⚠️ **落点条自己那三份 Python 引用也要断。** `strip._targets` 只断了
            #    「条 → target」，可 `strip_holder` 里还各攥着一份子视图与 target：
            #    `["actions"]` → target → `_confirm_batch` 闭包 → `win`。
            #    实测漏掉它：开/关 3 轮 144 个 `_ClassLive*`、9 轮 180 —— **随轮数增长**。
            strip_holder["view"] = None
            strip_holder["hint"] = []
            strip_holder["actions"] = []
            strip_holder["search_btns"] = []
            # ⚠️ 批量状态是**模块级**的，跨面板存活 —— 不清的话，关掉面板再打开
            #    会直接落进"上次那批还没确认"的模式里。
            #    （`S["result"]` 是**故意**跨面板的，别把这条规矩套到它头上。）
            S["batch"] = None
            S["search"] = None
            S.pop("queue", None)
            # ⚠️ 映射表那批选择器也要断 —— 它们自己抓着菜单，而菜单抓着 target。
            #    与卡片 `_targets` 是**同一类**孤岛（那次实测：只 setContentView_(None)
            #    时 3 轮后仍有 16 个 DropTarget）。
            batch_rows.clear()
            win.setContentView_(None)        # 丢掉整棵视图树
        except Exception:                                 # noqa: BLE001
            pass
        # ⚠️⚠️ **必须 `close()`，不是 `orderOut_`。** 实测：只 `orderOut_` 的话
        #     开/关 3 轮之后 `NSApp.windows()` 仍是 3 —— 窗口只是**不可见**，
        #     对象还活着（`orderOut_` 不释放）。而我们再也不需要它了。
        try:
            win.close()
        except Exception:                                 # noqa: BLE001
            pass
        S["panel"] = None
        S.pop("panel_key", None)
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

    def lock_of(course):
        """这门课的写入锁 —— **与 `prep.prepare()` 同一把**。

        ⚠️ 路径一律由 `prep.state_lock_path()` 算，**这里不许自己拼 `.lock`** ——
           拼第二份就会两边各拿各的锁：互斥**静默失效**，什么错都不报。
        ⚠️ 锁的就是 `prep-state.json.lock`，所以 `state_root` 必须与跑 prep 时一致
           （同 `glossary_of` 的道理：注入点必须真的生效）。
        """
        import prep as prep_mod
        return prep_mod.state_lock_path(paths.prep_state(course, root=state_root))

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
        # ⚠️ **早拒**：prep 正在跑的时候不要动术语表。
        #    `prep._course_write_lock` 已经挡住了**破坏**（拿不到锁会抛，见 `d935d5a`），
        #    但那是"点下去、跑一圈、再报错"；早拒把话说在前面 ——
        #    用户不会先看到那一行从屏幕上消失、再看到一行失败。
        if S.get("busy"):
            set_status("正在跑准备 —— 等它完再删", 1.0)
            return
        import prep as prep_mod
        try:
            res = prep_mod.remove_terms(glossary_of(course), [term],
                                        lock_path=lock_of(course))
        except prep_mod.GlossaryError as e:
            # ⚠️ 术语表自身的问题（**含「正被另一个写入器占用」**）——
            #    `LOCKED_MSG` 本来就是人话，别再往上叠一层类型名。
            set_status(f"删不掉：{e}", 1.0)
            return
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
                         "entries": list(zip(res.positions, res.originals))}
        # ⚠️ 一次性意图：`refresh` 重画**之后**把这一行滚进视野（HIG：撤销的结果要看得见）。
        #    必须**推迟到重画之后** —— 重画是整块重建卡片，旧对象全没了。
        S["_undo_scroll"] = course
        set_status(f"已删除 {term} —— 卡片上有「撤销」")
        _later(refresh)                # ⚠️ **必须推迟** —— 见 `_later` 的说明

    def do_undo(course):
        entry = S["result"].get(course)
        u = (entry or {}).get("undo")
        if not u:
            return
        import prep as prep_mod
        try:
            n = prep_mod.restore_lines(glossary_of(course), u["entries"],
                                       lock_path=lock_of(course))
        except prep_mod.GlossaryError as e:
            set_status(f"撤销失败：{e}", 1.0)
            return
        except Exception as e:                            # noqa: BLE001
            set_status(f"撤销失败：{type(e).__name__}: {e}", 1.0)
            return
        if not n:
            set_status("撤销没写进去 —— 看日志", 1.0)
            return
        entry["removed"].discard(u["text"])
        entry["undo"] = None
        set_status(f"已把「{u['text']}」放回原位置")
        _later(refresh)                # ⚠️ 同上：别在 action 里拆 sender 的视图

    from AppKit import NSButton, NSObject

    status_holder["label"] = panel.make_label(
        "拖课件到某张卡上 = 加到那门课　·　也可以点卡片上的「选择文件…」",
        NSMakeRect(PAD, PAD + 6.0, WIDTH - 2 * PAD - 100.0, 18.0), 11.0, alpha=DIM)
    ve.addSubview_(status_holder["label"])

    # ⚠️⚠️ **关闭按钮曾两次踩同一个坑**（2026-09-26 作者实测「关闭无反应」）：
    #   1. 第一版是 `objc_own.own("EntryClose", …, {"close_": lambda …: do_close()})`
    #      + 一个**局部变量** `btn_tgt`。函数一返回就没人引用它 → 被 GC →
    #      而 `setTarget_` 是**弱引用** → `target()` 变成 `None` → **点了完全没反应，
    #      也不报错**。实测：连 `gc.collect()` 都不用，检查时它已经是 None 了。
    #   2. 同一个 key 的类**被缓存** → 面板关掉再开，那个 lambda 还是**第一次**的
    #      `do_close`，会去关一个已经不在的窗口。
    #   → 正确答案仓库里早就有：走 `_target()`（**回调挂实例**），
    #     并把 target **留住**（`_targets` 那份约定）。我在这里没照做。
    btn_tgt = _target(do_close)
    btn = NSButton.alloc().initWithFrame_(
        NSMakeRect(WIDTH - PAD - 76.0, PAD + 2.0, 76.0, 26.0))
    btn.setTitle_("关闭")
    btn.setBezelStyle_(1)
    btn.setTarget_(btn_tgt)                             # ⚠️ 弱引用 —— 靠下面那行留住
    btn.setAction_("act:")
    ve.addSubview_(btn)
    win._entry_targets = [btn_tgt]                      # ← 留住它，否则被 GC

    refresh()

    # 位置：屏幕中上（AppKit 左下原点）
    scr = NSScreen.mainScreen().frame()
    win.setFrameOrigin_(((scr.size.width - WIDTH) / 2.0,
                         max(60.0, scr.size.height - h - 140.0)))
    win.orderFrontRegardless()
    return Handles(win, do_close, refresh, set_status, run_batch, run_search)


def open_panel(**kw) -> Handles | None:
    """确保同进程只有一份面板（菜单栏 + 双击两条入口都可能来）。

    ⚠️⚠️ **按「有没有 `on_start`」定键，不是拿整个 `kw` 比。**
       最要命的一种：先在上课中从菜单栏开过（`on_start=None`，**故意不建**「开始上课」），
       那个面板还开着时再双击 `.app`（`on_start` 能兑现）——
       直接复用旧面板的话，**双击打开的面板没有「开始上课」按钮**。
       ⚠️ 但**别拿整个 `kw` 比**：调用方每次传一个新的 lambda 就永远不等，
       于是每次打开都白重建（实测踩到）。`on_start` 有没有，正是**改变界面结构**的那一项。
    """
    cur = S.get("panel")
    if cur is not None:
        if S.get("panel_key") == (kw.get("on_start") is None):
            cur.window.orderFrontRegardless()
            return cur
        cur.close()                       # 结构不同 -> 关掉重建（`S["result"]` 在模块级，不丢）
    h = build(**kw)
    S["panel"] = h
    S["panel_key"] = kw.get("on_start") is None
    return h


def _status(text, alpha=1.0) -> None:
    """把状态写进**当前**打开的那个面板 —— **不是**发起这次跑的那个。

    ⚠️ 跑 prep 中途关窗再开：旧的 `_done` 闭包捕获的是**旧面板**的 `set_status`/`refresh`
       → 结果写进一个已经 `orderOut` 的窗口 → **新面板永远看不到结果列表**，
       而 `busy` 已经清了 → 新面板里拖东西又被拒成「另一门课还在跑」= 幽灵在跑。
       → 一律走这里「查当前面板」，别捕获某一代。
    """
    h = S.get("panel")
    if h is not None:
        h.set_status(text, alpha)


def close_panel() -> None:
    h = S.get("panel")
    if h is not None:
        h.close()
