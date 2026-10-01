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

import datetime
import os
import pathlib
import re
import threading
import typing

import corpus
import courses
import objc_own
import panel
import paths
import ready
import timetable as T

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
DROP_H = 96.0          # ⚠️ 2026-09-28 从 44 抬到 96（≈ 一张卡那么高）。作者：
                       #    「那个统一的入口我感觉太小了」—— 它才是「把课件全丢进来」
                       #    的主入口，44pt 的页脚条读起来像说明文字，不像可操作的东西。
DROP_GAP = 8.0
BATCH_ROW = 24.0       # 映射表一行
BATCH_HEAD = 18.0      # 组标题（「已认出归属」/「认不出来的」）
BATCH_SEP = 15.0       # 两组之间的分隔
BATCH_CHIP_W = 138.0   # 右侧课号选择器
BATCH_UNDECIDED = "未分类"
SEARCH_W = 220.0       # 标题行右侧搜索框的宽
SEARCH_ROW = 22.0      # 一条搜索结果一行
SEARCH_LIMIT = 80      # 一次最多显示多少条（`find` 会报 `truncated`）
READ_BEFORE = 2        # 点开一条命中时，往前给几句
READ_AFTER = 6         # 往后给几句 —— 一屏读得下，且够看出上下文
READ_LINE_H = 15.0     # 展开块每行的高度（与 `search_card_height` 成对）
SESS_ROW = 22.0        # 课次列表一行
SESS_HEAD = 20.0       # 一组的标题行（「这门课的上课记录」/「没归课的」）
SESS_SEP = 10.0        # 两组之间的分隔（`sessions_card_height` 的算式里有它）
IMPORT_ROW = 22.0      # 导入确认卡一行一门课
IMPORT_HEAD = 20.0     # 导入确认卡的标题行

# ── 新增课程（2026-09-28）────────────────────────────────────────────
# ⭐ 形状是 **inline row**：点「＋」让**底部那条自己**换成输入行，不是弹 sheet、
#    也不是在搜索框旁边再加一个常驻文本框。
#    ⚠️ 面板里原来那句「点右上角的「＋ 新增课程」」是**假的**（按钮不存在）——
#       本轮把按钮做出来，**同时**把那句话改成它真实的位置。两处共用下面这个常数，
#       所以改标题不会再把文案甩下（`tests/test_entry_panel.py` 钉住这条）。
ADD_TITLE = "＋ 新增课程"
ADD_PLACEHOLDER = "课号，如 ECON10740"
ADD_HINT = "回车建课 · Esc 取消"
ADD_W = 116.0          # 「＋ 新增课程」按钮宽
PICK_W = 104.0         # 「选择文件…」按钮宽
# ── 测试模式开关（2026-09-30，`docs/PLAN-test-mode.md` §13.3）──────────
# ⚠️ 它**无边框**（`setBordered_(False)`），和右边那两颗**长得不一样是故意的**：
#    面板里表达「持久状态」的控件一律无边框（顶栏 🌐/❓/字幕、就绪条、卡片上可点的），
#    有边框的那几颗留给**动作**（「选择文件…」「＋ 新增课程」「新建/取消」）。
#    作者 2026-09-30 在「按钮 vs 勾选框」那轮拍的也是这条（§3.2）。
TEST_W = 104.0         # 开关宽（**实测文字 71.7pt** @12pt，两态同宽，余量足够点）
# ⚠️ 这几个 y 与 `DROP_H` 是同一组常数推出的（顶部 25 / 行距 6 / 底部 24），
#    和 `card_height` 那条纪律一样：改 `DROP_H` 就要一起改这里，别各写一遍。
ADD_ROW_Y = 47.0       # 输入框 + 「新建/取消」那一行
ADD_REPLY_Y = 24.0     # 反馈那一行（`plan_add` 的 `text` 画在这儿）
# 落点条里提示文字的宽度：右边要给**三颗**控件让位
# （测试模式开关 +「＋ 新增课程」+「选择文件…」）。
# ⚠️ 算式与右边那三颗的位置是同一件事 —— 改任何一颗的宽度都得改它。
# ⚠️⚠️ **它现在只剩 29.7pt 余量了**：最宽的那条提示
#    「自动认出哪份属于哪门课 —— 认不出的会让你核对」实测 **244.3pt**（11pt），
#    而这个式子给出 274.0。再多塞一颗按钮，提示会**折行 + 第二行被静默吃掉**
#    （`panel.make_label` 的默认换行行为，没有省略号）。
#    → 判据在 `tests/test_entry_panel.py` 那一节，**加控件前先看它**。
HINT_W = WIDTH - 2 * PAD - (CARD_PAD + PICK_W + 8.0 + ADD_W + 8.0 + TEST_W + 16.0)

# ── 就绪条（2026-09-28）──────────────────────────────────────────────
# 插在标题行与卡片区之间。**高度算在 `build()` 那条从下往上的推法里**，
# 与视图的 y 用同一组常数（`card_height` 那条纪律）。
READY_H = 40.0
READY_GAP = 10.0       # 就绪条 ↔ 标题
READY_ITEM_GAP = 16.0  # 项与项之间
READY_PAD = 12.0       # 条内左右留白

# ⚠️⚠️ **状态一律「形状 + 文字」，不许只靠颜色** ——
#    `[一手]` HIG › Accessibility（2025-03-07）逐字：「**Offer visual indicators,
#    like distinct shapes or icons, in addition to color**」。
#    这也是为什么每一项都带一个词，而不是一个小圆点。
# ⚠️ 「未配」这类**可选未做**不许画成警告 —— HIG 没有这个概念，跨应用约定是
#    中性次要文字，「never red, never a warning triangle」。所以它用 `○` 不用 `⚠`。
_MARK = {"ok": "✓", "todo": "○", "warn": "⚠", "unknown": "—", "busy": "◌"}

# 每一项的短名字。⚠️ 长名字会把 680pt 的面板撑爆 —— 短名 + 状态词就够，
#    详情交给点击之后的动作。
_READY_NAME = {"mic": "麦克风", "models": "语音模型", "engine": "翻译引擎",
               "vault": "笔记库"}


def ready_item_text(it: dict) -> str:
    """就绪条上**一项**的那句话。**纯函数**（判据盖这里，不用起窗口）。

    形状：`<形状标记> <名字>  <短状态>`
    ⚠️ 形状标记在前、文字在后 —— 标记是给"扫一眼"的，文字是给"看明白"的，
       两者都不能省（见上面 HIG 那条）。
    """
    mark = _MARK.get(it.get("state", "unknown"), "—")
    name = _READY_NAME.get(it.get("key", ""), it.get("key", ""))
    return f"{mark}  {name}  {ready_short(it)}"


def ready_short(it: dict) -> str:
    """状态词 —— **要短**（一行放三项）。太长就在 `find` 之前先折在这里。

    ⚠️ 进度**不报百分比**：`[一手]` HIG › Progress indicators（2023-09-12）
       「**Don't switch from the circular style to the bar style**」+
       「**Keep progress indicators moving**」。而我们拿不到字节数
       （`update.run_step` 是 subprocess + `capture_output`）→ 只能给状态词。
       Apple 自己那颗 SS 也是「Checking for updates…」这种**状态词 + 转圈**，
       而不是一根假进度条。
    """
    st = it.get("state")
    if it.get("key") == "models":
        if st == "todo":
            return f"缺 {it.get('detail', '').split('还差 ')[-1] or ''}".strip() or "缺"
        if st == "warn":
            return "有旧版"
        return "已就绪"
    if it.get("key") == "engine":
        return "云端" if st == "ok" and "云端" in it.get("detail", "") else (
            "本地" if st == "ok" else "未配")
    if it.get("key") == "vault":
        # ⚠️ 「未设」不是错误 —— 笔记照写 `sessions/`（`ready.vault_item` 那条）
        return {"ok": "已设", "warn": "找不到了"}.get(st, "未设")
    return {  # mic
        "ok": "已允许", "todo": "未授权", "warn": "被拒",
        "unknown": "查不到", "busy": "询问中",
    }.get(st, "—")


def ready_line_text(items: list) -> str:
    """整条就绪条上所有项拼成一行（`·` 分隔）。给状态行/终端用。"""
    return "　·　".join(ready_item_text(i) for i in items)


def test_mode_title(on: bool) -> str:
    """测试模式开关上那句话。**纯函数**（判据盖这里，不用起窗口）。

    ⚠️ **就两态、没有附加说明、也没有图标** —— 作者 2026-09-30 拍板砍掉了
       「开 · 留音频」那半句（`docs/PLAN-test-mode.md` §3.5），
       同一天又砍掉了 🎙（原话：「不要 emoji」）。
    ⭐ 代价：界面上**看不出「开 = 会录音 + 会上传」**。这是**有意砍的**，
       **别哪天把它当成"用户不知道"的 bug 来修**。
    ⚠️ 信息量全压在「开/关」两个字上 → 所以**不许靠颜色或图标**帮它表达状态
       （没有 switch 先例，也不要系统勾选框，见 §3.2）。
    ⚠️ 两态**字数相同** → 文字宽度一模一样（实测 71.7pt）→ 切换时按钮不抖。
    """
    return f"测试模式：{'开' if on else '关'}"


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
    # 同理：新增课程也走不到（没有真键盘）—— 「敲入 X 然后回车」的程序化版本。
    # ⚠️ 它**与手输同一条路**（`submit_add`），判断仍在 `courses.plan_add`。
    add_course: typing.Callable[[str], None]
    # 同理：课次列表那条路也走不到验收跑器里（要点卡上那个「课次」按钮）。
    # ⚠️ 它**只读** `sessions/`；「判给本课」写的是旁路文件 `courses.set_attribution`。
    show_sessions: typing.Callable[[str], None]
    # 同理：导入课表那条路也走不到跑器里（要真拖一个 `.ics`）。
    # ⚠️ 它**只解析、不落盘** —— 建课要人点「新建这些课」。
    import_timetable: typing.Callable[[list], None]
    # 删课那条路**也**走不到跑器里：右键菜单点不了，而确认框是模态的（会把跑器卡住）。
    # → 这个入口跳过确认框（`ask=False`），**其余全同**（`courses.delete` + `delete_msg`）。
    # ⚠️ 生产路径永远 ask=True。
    delete_course: typing.Callable[..., None]
    # 同理：「开始分类」那张卡上的按钮，跑器与测试也点不到 ——
    # 而它是**唯一**能验「免费按文件名认出来的那几份，不许被模型结果覆盖」那条判据的入口
    # （2026-09-28 OCR 审计发现过一个正好相反的静默丢失）。
    start_classify: typing.Callable[[], None]


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
    # ⚠️⚠️ 而那个边界**只认 ASCII 字母数字**（2026-09-28 审查指出）：
    #    中文也是 `isalnum()`，于是 `ECON10740经济学原理` 会被判成
    #    「课号是更长课号的前缀」→ 课号拼了两遍。紧跟中文正是"课号 + 课名"的常态写法。
    rest = t[len(r.course):] if t.startswith(r.course) else None
    if rest is not None and (not rest or not (rest[0].isascii() and rest[0].isalnum())):
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


def _num(v, unit: str) -> str:
    """计数 + 单位。**`None`（读不出）画 `—`，不是 0** —— `courses.py` 模块头第 3 条。

    ⚠️ 提到模块级是因为它现在有**两个**用户：卡片第二行与删除确认框
       （那个框里的数字是**破坏性操作之前**给人看的，读失败被显示成 `0`
       等于说"没有课件会被动"）。
    """
    return f"{v} {unit}" if v is not None else f"— {unit}"


def readiness_line(r: courses.Readiness) -> str:
    """卡片第二行。**每个数都可能是「未知」** —— 那时画 `—` 而不是 0。

    ⚠️ 「未知」与「零」的区别是刻意的：把读失败显示成 0，
    等于跟用户谎称「这门课一个词都没有」（`courses.py` 模块头第 3 条）。
    """
    return (f"{_num(r.materials, '份课件')} · {_num(r.terms, '条术语')}"
            f" · 上次上课 {_short_date(r.last_session)}")


def body_height(n: int, screen_h: float, *, card_h: float = CARD_H) -> float:
    """卡片区高度：**先按内容长，超过屏幕 60% 才开始滚**。

    一个公式管两种情形 —— 不做「≤N 静态 / >N 才滚」两套路径（那会在第 N+1 门课
    上出现断崖，而且两套路径迟早只维护一套）。
    """
    natural = max(1, n) * (card_h + CARD_GAP) - CARD_GAP
    cap = max(card_h + CARD_GAP, screen_h * BODY_SCREEN_FRACTION)
    return min(natural, cap)


def empty_state_lines() -> list:
    """零课程时那张卡上要说的话。**纯函数**（判据盖这里，不用起窗口）。

    ⚠️ 今天这里是**一片空白**：`body_height(0)` 老老实实留了 `CARD_H` 那么高，
       然后什么也不画 —— 用户盯着一个空面板，不知道下一步干什么。
       （2026-09-28 由探索代理核实：`refresh()` 画零张卡，一个字都不说。）

    ⚠️ **两条路都要给**，而且顺序有讲究：先「新建」是因为它才是本面板原来缺的那个
       （`docs/PLAN-entry-panel.md:542` 记着这个缺口）；拖课件那条本来就能用，
       但用户不知道 —— 顺带说出来。

    ⚠️⚠️ **文案里的按钮名与位置必须与真的对得上。** 上一版这里写的是
       「点右上角的「＋ 新增课程」」，而**那个按钮根本不存在** —— 空状态在教用户
       去点一个不存在的东西。现在按钮有了，位置是**底部那条**（`ADD_TITLE` 与
       按钮共用同一个常数，改标题不会把这句话甩下）。
    """
    return ["还没有课",
            f"① 点下面的「{ADD_TITLE}」，输一个课号（如 ECON10740）",
            "② 或者把课件拖进下面那个虚线框 —— 会自动建课"]


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
      （字段：`path, status, chars, blocks, skipped_shapes, ocr_pages, error`）。

    ⚠️ 已有数据里就有原因 —— `entry_panel.py` 原来只 `len()` 它，所以用户看到
       「N 个文件失败」而**不知道是哪个、为什么**（`PLAN-entry-panel §3.6` 三条硬要求
       里差的那两条；调研抄的措辞是 `'Upload failed' is not a message`）。
    ⚠️ 取 `error` 优先、`status` 兜底。实测 `failed` 只会是 `empty`/`unreadable`/
       `unsupported` 三种，而这三条的 `error` **全都非空**（`extract.py` 里那三个
       `return ExtractResult(...)`）→ 兜底**实践中走不到**，留它是安全网。
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


def _short_home(p: str) -> str:
    """把绝对路径里的家目录缩成 `~`。状态行只有一行，全路径会把它撑爆。

    ⚠️ **必须带分隔符边界**（2026-09-28 审查指出）：只判 `startswith(家目录)` 的话，
       家目录 `/Users/owen` 会把同级的 `/Users/owen2/…` 缩成 `~2/…`。
       这个字符串正是 `delete_msg` 里那句「课件留在 …」—— 路径说错等于原件找不着。
    """
    s = str(p)
    h = os.path.expanduser("~")
    if s == h or s.startswith(h + os.sep):
        return "~" + s[len(h):]
    return s


def delete_msg(course: str, res) -> str:
    """删完一门课后，状态行上那句话。**纯函数**（判据盖这里，不用起窗口）。

    ⚠️⚠️ **必须说出"什么没被删"**：`sessions/` 里的上课记录**一个字节都不动**，
       而删了课再建同名，那些记录会**自己接回来**（`courses.session_files` 按课号
       后缀匹配）。不说的话用户会以为"删了就全没了" —— 那是**文案撒谎**，
       同 `empty_state_lines` 那次（教用户去点一个不存在的按钮）。
    ⚠️ 「只删课号」时课件**不在废纸篓里**，它在保留区 —— 所以那句话要写出**去哪儿找**，
       否则等于把原件藏起来了（而它可能是唯一副本）。
    """
    f = res.get("facts") or {}
    errs = [str(e) for e in (res.get("errors") or [])]
    if errs:
        return f"⚠️ {course} 没删干净：{'；'.join(errs)}"
    kept = res.get("kept")
    if kept:
        head = "已从面板移除，术语表进了废纸篓"
        tail = f"课件留在 {_short_home(kept)}"
    elif res.get("trashed"):
        head, tail = "已进废纸篓", "能拖回来"
    else:
        return f"{course}：没什么可删的（本来就不在）"
    mid = f"{f['sessions']} 节上课记录没动" if f.get("sessions") else ""
    return f"{course}：{head}" + "".join(f" · {x}" for x in (mid, tail) if x)


def card_height(entry=None, *, has_actions: bool = True) -> float:
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
         + CARD_PAD)                                  # 下
    if has_actions:
        # ⚠️ **只有真的会画按钮时才留这块高度。** `on_start is None` = 上课中从菜单栏
        #    打开的面板，那时不画「开始上课」—— 而 2026-09-28 之前「选择文件…」是
        #    **永远在**的，所以这个空档看不出来；删掉它之后卡片下半截就空了一截。
        h += CARD_GAP_V + BTN_H
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


def read_span(line: int, *, before: int = READ_BEFORE, after: int = READ_AFTER) -> tuple:
    """命中行 → 交给 `find.read` 的闭区间（**1-based**）。**纯函数** —— 判据指得到它。

    ⚠️ **窗口是面板定的，不是 `find.read` 定的。** 它自己的上限是 200 行 / 6000 字符
       （那是给模型看的量级），直接铺进卡片会撑爆。面板要的是"这一句的前后文"。
    """
    n = int(line)
    return max(1, n - before), n + after


def open_block_lines(text) -> int:
    """展开块占**几行** —— 高度与排版**共用同一份文本**，所以两边必然一致。

    ⚠️ 别改成"估一个行数"：算多算少**都不报错**，卡片会空一截或把内容挤出去
       （同 `search_card_height` / `card_height` 那条纪律）。
    """
    return len(text.splitlines()) if text else 0


# `find.read` 的输出是**给模型看的**：`> [!abstract] 14:08:06` 抬头 +
# `> **EN**:` / `> **ZH**:` / `> **ASR**:` 三个字段。铺给人读会碎。
_QUOTE = re.compile(r"^>\s?")
_TS_LINE = re.compile(r"^> \[!abstract\]\s*(.*)$")
_FIELD = re.compile(r"^>\s?\*\*(EN|ZH|ASR)\*\*:\s*(.*)$")


def clean_read_text(text: str) -> str:
    """`find.read` 的输出 → 面板里**给人读**的样子。**纯函数**（2026-09-29 作者拍板）。

    - 抬头 `# 文件名 第 X–Y 行` **留着** —— 它回答「这段来自哪、第几行」，
      那正是展开这一步的意义（`find.read` 的 docstring：命中只是提示，读全文是另一步）
    - `> [!abstract] 14:08:06` → `14:08:06` · `> **EN**: x` → `x`
    - ⭐ **同一时间块里 ASR 与 EN 一字不差就整行丢掉** —— 会话文件里这很常见，
      不丢就是同一句话连读两遍。

    ⚠️ 这条**必须按字段比，不能按相邻行比**（2026-09-29 判据抓出来的）：
       真实顺序是 `EN / ZH / ASR`，EN 与 ASR **隔着 ZH**，相邻去重永远不会触发。
    """
    out: list = []
    en: str | None = None
    for ln in (text or "").splitlines():
        m = _TS_LINE.match(ln)
        if m:
            en = None                                    # 新的一句，EN 基准重置
            out.append(m.group(1).rstrip())
            continue
        f = _FIELD.match(ln)
        if f:
            field, body = f.group(1), f.group(2).strip()
            if field == "EN":
                en = body
            elif field == "ASR" and body and body == en:
                continue                                 # 与 EN 一字不差 —— 不读两遍
            if body:
                out.append(body)
            continue
        s = _QUOTE.sub("", ln).rstrip()
        if s and out and s == out[-1]:
            continue
        out.append(s)
    return "\n".join(out)


def search_card_height(n: int, open_lines: int = 0) -> float:
    """搜索结果卡的高度 —— **与 `_make_search_card` 的排版循环成对**。

    ⚠️ 同 `card_height` / `batch_card_height` 那条纪律：别抄固定值。
       行数是**搜出几条**决定的，写死就会溢出（且**不报错**，只是画到框外）。
    ⚠️ `open_lines` 必须与循环里**真的画了几行**同源（都来自 `open_block_lines`）。
    """
    return (CARD_PAD + max(n, 1) * SEARCH_ROW + open_lines * READ_LINE_H + CARD_PAD)


def _make_search_card(hits, *, width, open_idx=None, open_text=None,
                      on_open=None, targets=None):
    """搜索结果列表。返回 `(视图, targets)`。**排一行是一条命中**。

    ⚠️ 排版一律**从顶部往下**（y 递减，卡片是非翻转坐标）—— 同 `_make_card`。
    ⚠️ `on_open` 给定时，命中那行的**正文变成按钮**（点它就地展开 / 收起原文）。
       ⚠️ 无边框 `NSButton` 是本仓库既有的可点做法（`make_ready_strip` 同款），
          它自己接管点击 —— **不必碰 `mouseDownCanMoveWindow` 那个雷**。
       ⚠️ 回来的 target 必须由调用方持有（`setTarget_` 是**弱引用**）。
    """
    from AppKit import NSButton, NSColor, NSFont, NSView, NSMakeRect

    spans = (open_text or "").splitlines()
    h = search_card_height(len(hits), open_block_lines(open_text))
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
    for i, hit in enumerate(hits or [None]):
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
        rect = NSMakeRect(body_x, y + 4.0, body_w, 16.0)
        if on_open is None:
            view.addSubview_(panel.make_label(hit.text or "", rect, 12.0, truncate=True))
        else:
            b = NSButton.alloc().initWithFrame_(rect)
            b.setTitle_(hit.text or "")
            b.setBordered_(False)
            b.setAlignment_(0)                            # 左对齐（NSTextAlignmentLeft）
            b.setFont_(NSFont.systemFontOfSize_(12.0))
            try:
                b.setContentTintColor_(white.colorWithAlphaComponent_(
                    0.98 if i == open_idx else 0.86))
            except Exception:                             # noqa: BLE001
                pass
            t = _target(lambda k=i: on_open(k))
            b.setTarget_(t)
            b.setAction_("act:")
            if targets is not None:
                targets.append(t)
            view.addSubview_(b)
        if i == open_idx:
            for ln in spans:                              # 展开块：与 open_block_lines 同源
                y -= READ_LINE_H
                view.addSubview_(panel.make_label(
                    ln, NSMakeRect(CARD_PAD, y + 2.0, inner_w, 13.0), 11.0,
                    alpha=0.70, truncate=True))
    return view, (targets or [])

def sessions_card_height(n_rows: int, n_orphans: int = 0) -> float:
    """课次列表卡的高度 —— **与 `_make_sessions_card` 的排版循环逐项对应**。

    ⚠️ 同 `search_card_height` / `card_height` 那条纪律：别抄固定值，别让两边漂。
       `n_orphans > 0` 时多一组标题 + 分隔（**空组不画，也不留高**）。
    """
    h = CARD_PAD + SESS_HEAD + max(n_rows, 1) * SESS_ROW
    if n_orphans:
        h += SESS_SEP + SESS_HEAD + n_orphans * SESS_ROW
    return h + CARD_PAD


def session_row(p, sents: int) -> dict:
    """`sessions/2026-09-24_202216_LECTURE.md` → 课次表的一行。**纯函数**。

    ⚠️ 文件名形状的**唯一来源是 `courses._session_course`** —— 这里只做显示用的
       切分（日期 / HH:MM），**不做归属判断**。归属走 `courses.session_files`。
    """
    parts = p.stem.split("_", 2)
    date = parts[0] if parts else p.stem
    raw = parts[1] if len(parts) > 1 else ""
    hhmm = f"{raw[:2]}:{raw[2:4]}" if len(raw) >= 4 else ""
    return {"stem": p.stem, "name": p.stem, "date": date, "hhmm": hhmm,
            "sents": sents, "state": "ok" if sents >= corpus.MIN_WORDS else "thin"}


def session_row_text(date: str, hhmm: str, sents: int, state: str) -> str:
    """一行课次的人话。**纯函数** —— 判据指得到它。

    `state` 只有两档：`ok`（够格）/ `thin`（空壳，录了一半或测试残留）。
    ⚠️ **空壳要说出来** —— 它们在 `sessions/` 里跟真课长得一模一样，
       而 `corpus` 那道 `MIN_WORDS` 闸是**静默**把它们排除的。
    """
    tag = "" if state == "ok" else "   ⚠️ 空壳（录了一半？）"
    return f"{date}  {hhmm}   ·   {sents} 句{tag}"


def _make_sessions_card(rows, *, width, title, on_back, on_adopt=None,
                        orphans=(), targets=None):
    """一门课的**课次列表** + 「没归课的上课记录」那一组。返回 `(视图, targets)`。

    ⚠️ 排版一律从顶部往下（y 递减）—— 同 `_make_card` / `_make_search_card`。
    ⚠️ 回来的 target 必须由调用方持有（`setTarget_` 是**弱引用**）。
    ⚠️ **只读**：这一屏不改任何文件。「判给本课」写的是**旁路文件**
       （`courses.set_attribution`），会话 `.md` 一个字节都不动。
    """
    from AppKit import NSButton, NSColor, NSFont, NSView, NSMakeRect

    h = sessions_card_height(len(rows), len(orphans))
    inner_w = width - 2 * CARD_PAD
    view = NSView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, width, h))
    view.setWantsLayer_(True)
    view.layer().setCornerRadius_(CARD_RADIUS)
    view.layer().setBorderWidth_(HAIRLINE)
    white = NSColor.whiteColor()
    view.layer().setBorderColor_(white.colorWithAlphaComponent_(CARD_LINE_A).CGColor())
    view.layer().setBackgroundColor_(white.colorWithAlphaComponent_(CARD_FILL_A).CGColor())
    targets = targets if targets is not None else []

    def _btn(text, x, y, w, action, size=11.0):
        b = NSButton.alloc().initWithFrame_(NSMakeRect(x, y, w, BTN_H))
        b.setTitle_(text)
        b.setBezelStyle_(1)
        b.setFont_(NSFont.systemFontOfSize_(size))
        t = _target(action)
        b.setTarget_(t)                                  # ⚠️ 弱引用 —— 靠 targets 留
        b.setAction_("act:")
        targets.append(t)
        view.addSubview_(b)
        return b

    y = h - CARD_PAD
    y -= SESS_HEAD
    view.addSubview_(panel.make_label(title, NSMakeRect(CARD_PAD, y, inner_w - 96.0,
                                                        SESS_HEAD - 4.0), 12.0,
                                      truncate=True))
    _btn("返回", width - CARD_PAD - 88.0, y - 2.0, 88.0, on_back)
    for r in rows or [None]:
        y -= SESS_ROW
        if r is None:
            view.addSubview_(panel.make_label(
                "这门课还没有上课记录", NSMakeRect(CARD_PAD, y + 4.0, inner_w, 16.0),
                11.0, alpha=DIM))
            continue
        view.addSubview_(panel.make_label(
            session_row_text(r["date"], r["hhmm"], r["sents"], r["state"]),
            NSMakeRect(CARD_PAD, y + 4.0, inner_w, 16.0), 11.0,
            alpha=(1.0 if r["state"] == "ok" else DIM), truncate=True))
    if orphans:
        y -= SESS_SEP
        y -= SESS_HEAD
        view.addSubview_(panel.make_label(
            f"没归课的上课记录（{len(orphans)} 节）—— 判给这门课？",
            NSMakeRect(CARD_PAD, y, inner_w, SESS_HEAD - 4.0), 12.0, truncate=True))
        for o in orphans:
            y -= SESS_ROW
            view.addSubview_(panel.make_label(
                f"{o['name']}   ·   {o['sents']} 句",
                NSMakeRect(CARD_PAD, y + 4.0, inner_w - 100.0, 16.0), 11.0,
                alpha=DIM, truncate=True))
            if on_adopt is not None:
                _btn("判给本课", width - CARD_PAD - 88.0, y + 1.0, 88.0,
                     (lambda s=o["stem"]: on_adopt(s)))
    return view, targets


def timetable_files(files) -> list:
    """从拖进来的一堆文件里挑出**课表**。**纯函数** —— 判据指得到它。

    ⚠️ 只按扩展名挑，**不按内容** —— 拖错一个 `.ics` 进去最坏是"认不出课"，
       而按内容猜（比如试着解析每个文件）会让一次误拖变成一次静默的解析尝试。
    """
    return [p for p in (files or [])
            if str(p).lower().endswith((".ics", ".ical"))]


def drop_split(paths) -> tuple[list, list]:
    """拖进来的一串 → `(课表, 其余)`。**纯函数。**

    ⚠️ 抽出来是因为「**哪些算课表、剩下哪些**」是本文件里唯一处分流判断，
       而它原来是内联在 `run_prep` 里的一行 —— `run_prep` 要起整个面板才能跑，
       于是那一行**没有任何判据**（本文件一直被这种事咬）。

    ⚠️ **`其余` 不是「能归档的」** —— 它是「不是课表的那部分」，里面可能有
       `.txt` / 文件夹这种**两条下游都不收**的东西。谁用它谁自己再过一遍
       `extract.is_supported`（`run_prep` 走的就是那条）。
    """
    ics = timetable_files(paths)
    return ics, [p for p in (paths or []) if p not in ics]


def acceptable(paths) -> bool:
    """这一串拖进来的，**面板收不收** —— 悬停高亮与松手分流**共用这一条**。纯函数。

    ⚠️ 存在的理由同 `extract.is_supported`：两个入口（底部落点条 / 课程卡片）各写一份
       判据，用户就会看到「高亮说能收、松手说不要」。
    ⚠️ 它是**并集**，因为下游有**两条**：
       · 课件 → `run_prep`（判据是 `extract.is_supported`，走 `expand` 摊平）
       · `.ics` → `run_import`（判据是 `timetable_files`，**只看扩展名**）
       少了任何一条都会出错，而且方向相反：
       · 只判课件 → 拖 `.ics` **不高亮**（说"收不了"），松手**却真导入了**（2026-09-29 实测）；
       · 只判课表 → 拖 pdf 不高亮，松手却归档了。
       ⚠️ 这正是 `REVIEW §9.2 #5` 那条的镜像 —— 那次是"高亮着却收不了"，
          这次是"不高亮却收下了"。两种都源自「悬停判的和松手做的是两件事」。
    """
    from extract import expand as _expand
    return bool(_expand(paths)[0]) or bool(timetable_files(paths))


def ics_course_row(c, *, known=None) -> dict:
    """`timetable.Course` → 导入确认卡的一行。**纯函数。**

    `state`：`new`（会新建）· `exists`（这门课已经有了）· `bad`（课号形状不对）。
    ⚠️ 判据是 `courses.plan_add` —— **别在这里再写一份**（它才是「能不能建」的
       唯一定义点，`cl course` 与新增课程那条路共用它）。
    """
    import courses as C
    want = T.suggest_code(c.name) or c.name
    plan = C.plan_add(want, list(known or ()))
    action = plan.get("action")
    # ⚠️ `plan_add` 的四个动作是 `bad` / **`exists`** / `pick` / `create` ——
    #    **`exists` 是「已经有这门课了」，`pick` 是「多命中、要你挑」**，两个都要算「已有」。
    #    第一版只判了 `pick` → 现成的课全被标成「课号形状不对，跳过」，而它一个字都没说错。
    state = "new" if action == "create" else (
        "exists" if action in ("exists", "pick") else "bad")
    whens = []
    for s in sorted(c.slots, key=lambda x: (x.weekday, x.hh, x.mm)):
        d = "一二三四五六日"[s.weekday]
        extra = f" 每{s.interval}周" if s.interval > 1 else ""
        whens.append(f"周{d} {s.hh:02d}:{s.mm:02d}{extra}")
    return {"name": c.name, "want": want, "state": state,
            "when": " · ".join(sorted(set(whens))) or "（没说时间）",
            "events": c.events}


def import_card_height(rows) -> float:
    """导入确认卡的高度 —— **与 `_make_import_card` 的排版循环逐项对应**。

    ⚠️⚠️ **必须留出底部按钮行**（2026-09-29 修的）：`_make_import_card` 在行循环
       结束后把「新建这些课」/「取消」画在 `y = CARD_PAD`，而循环推导出的**最后
       一行 y 也正好是 `CARD_PAD`** → 最后一门课那行**与按钮叠在一起**。
       对照 `card_height`：有按钮时它额外加了 `CARD_GAP_V + BTN_H`。
    """
    return (CARD_PAD + IMPORT_HEAD + max(len(rows or []), 1) * IMPORT_ROW
            + CARD_GAP_V + BTN_H + CARD_PAD)


def _make_import_card(rows, *, width, warn, on_confirm, on_cancel, targets=None):
    """导入确认卡。返回 `(视图, targets)`。**确认之前一个字节都不落盘。**

    ⚠️ 回来的 target 必须由调用方持有（`setTarget_` 是**弱引用**）。
    """
    from AppKit import NSButton, NSColor, NSFont, NSView, NSMakeRect

    h = import_card_height(rows)
    inner_w = width - 2 * CARD_PAD
    view = NSView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, width, h))
    view.setWantsLayer_(True)
    view.layer().setCornerRadius_(CARD_RADIUS)
    view.layer().setBorderWidth_(HAIRLINE)
    white = NSColor.whiteColor()
    view.layer().setBorderColor_(white.colorWithAlphaComponent_(CARD_LINE_A).CGColor())
    view.layer().setBackgroundColor_(white.colorWithAlphaComponent_(CARD_FILL_A).CGColor())
    targets = targets if targets is not None else []

    def _btn(text, x, y, w, action):
        b = NSButton.alloc().initWithFrame_(NSMakeRect(x, y, w, BTN_H))
        b.setTitle_(text)
        b.setBezelStyle_(1)
        b.setFont_(NSFont.systemFontOfSize_(12.0))
        t = _target(action)
        b.setTarget_(t)                                  # ⚠️ 弱引用 —— 靠 targets 留
        b.setAction_("act:")
        targets.append(t)
        view.addSubview_(b)

    y = h - CARD_PAD
    y -= IMPORT_HEAD
    n_new = sum(1 for r in rows or [] if r["state"] == "new")
    view.addSubview_(panel.make_label(
        f"认出了 {len(rows or [])} 门课 —— 新建其中 {n_new} 门？",
        NSMakeRect(CARD_PAD, y, inner_w, IMPORT_HEAD - 4.0), 12.0, truncate=True))
    for r in rows or [None]:
        y -= IMPORT_ROW
        if r is None:
            view.addSubview_(panel.make_label(
                "这份课表里没认出任何课程", NSMakeRect(CARD_PAD, y + 4.0, inner_w, 16.0),
                11.0, alpha=DIM))
            continue
        tag = {"new": "", "exists": "（已有）", "bad": "⚠️ 课号形状不对，跳过"}[r["state"]]
        view.addSubview_(panel.make_label(
            f"{r['want']}{tag}", NSMakeRect(CARD_PAD, y + 4.0, 200.0, 16.0), 11.0,
            alpha=(1.0 if r["state"] == "new" else DIM), truncate=True))
        view.addSubview_(panel.make_label(
            r["when"], NSMakeRect(CARD_PAD + 208.0, y + 4.0, inner_w - 208.0, 16.0),
            11.0, alpha=DIM, truncate=True))
    y = CARD_PAD
    _btn("新建这些课", width - CARD_PAD - 200.0, y, 100.0, on_confirm)
    _btn("取消", width - CARD_PAD - 92.0, y, 92.0, on_cancel)
    if warn:
        view.addSubview_(panel.make_label(
            "；".join(warn)[:120], NSMakeRect(CARD_PAD, y + 4.0, inner_w - 216.0, 16.0),
            10.0, alpha=DIM, truncate=True))
    return view, targets


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


def _sever(card) -> None:
    """断掉一张卡片上那几条 Python 引用（`do_close` 的清环用）。**每条各自 try。**

    ⚠️⚠️ **容错在这里是必需的，而且必须是「每条各自」的。** 2026-09-28 实测抓到：
       `doc` 里的卡片**不全是 Python 子类** —— 零课程的空状态卡、搜索结果卡、
       批量映射卡都是**裸 `NSView`**（`NSView.alloc().initWithFrame_(…)`），
       而裸 ObjC 对象**不接受任意 Python 属性**：
           `_targets = []` → `AttributeError: 'NSView' object has no attribute '_targets'`
       原来这四条摆在**同一个 `try`** 里，于是**一张裸视图就把后面全部跳过**：
       断环、清 `S["batch"]`/`S["search"]`、`win.setContentView_(None)` 一条都不做，
       而异常被那句 `except Exception: pass` 吞掉 —— **什么都不报**。

       两个症状（都不是"少做一点"，是坏掉）：
       1. **每开/关一次泄漏一整个面板** —— 正是这段代码当初存在的理由；
          `win._entry_targets` 没断 → target → `do_close` 闭包 → `win`，环还在。
       2. **关掉再打开还停在上次的批量/搜索/新增模式**（`S` 是模块级的）。

       ⚠️ 触发它的路径正是「**零课程首次打开 → 关窗**」—— 新用户走的第一条路。
       判据在 `tests/test_entry_panel.py` ⑬（`S["add"]` 是这整段清理的哨兵）。
       📌 这个形状**有成文依据**：PEP 8 反对裸 `except:`，ruff 干脆把它设成 lint 规则
       （`S110 try-except-pass`）。这里不是「要容错就随便吞」，是**每条各自 try**。

    ⚠️ 为什么除了 `_targets` 还要断那三个落点回调：**它们是另一条环** ——
       卡片 → `_on_drop` → `run_prep` → `set_status` → 状态标签 → `ve` → 子树 → 卡片。
       那一圈**不含 `win`**（所以窗口能回收），但它自己是个**孤岛**，
       `gc` 收不回（实测：只 `setContentView_(None)` 时 3 轮后仍有 16 个 DropTarget）。
    """
    for attr, val in (("_targets", []), ("_on_enter", None),
                      ("_on_drop", None), ("_on_exit", None)):
        try:
            setattr(card, attr, val)
        except Exception:                                     # noqa: BLE001
            pass                                              # 裸视图：本来就没有这些


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


def _make_card(r: courses.Readiness, *, on_start, on_drop_files, width,
               entry=None, on_delete=None, on_undo=None, on_delete_course=None,
               on_sessions=None, hinted=False, on_not_this=None):
    """一张卡 = 一个落点 + 两行内容（+ 跑过之后的结果列表）。"""
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
        # ⚠️ 判据是 `acceptable()` —— 「面板收不收」的**唯一定义点**（与落点条共用）。
        #    它比"摊平后有能抽的"宽：下游有**两条**（课件走 `prep`、`.ics` 走
        #    `run_import`），少了 `.ics` 那条就会「不高亮却收下了」（2026-09-29 实测）。
        #    更早的错法（原来只看 `bool(panel.file_paths(pb))` → 文件夹、`.txt`
        #    全高亮说"能收"）见 `REVIEW §9.2 #5`。
        #    ⚠️ 拒的时候**也要不高亮**：HIG 逐字要求"收不了时给显式反馈（`circle.slash`）、
        #       **别给高亮**" —— 只改返回值会留下"高亮着但收不了"。
        ok = acceptable(panel.file_paths(pb) or [])
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
    # ⚠️ 还要把「画不画按钮」告诉它 —— 否则不画按钮的那些卡下半截是空的。
    _acts = on_start is not None
    view.setFrame_(NSMakeRect(0.0, 0.0, width, card_height(entry, has_actions=_acts)))
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
    y = card_height(entry, has_actions=_acts) - CARD_PAD
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
        # ⭐ **预选的那张卡，按钮标题自己说清楚**（作者 2026-09-29 的 ③）。
        #    `[一手]` Microsoft HAX G11 逐字：「**Make clear why the system did what it
        #    did**」—— 不说清楚的话，"为什么这张排第一"对用户就是猜的，
        #    而认错课的代价是**整节课术语表全错**。
        #    ⚠️ 理由塞进**按钮标题**而不是新加一行：加行会动 `card_height` 的算式，
        #       而那个算式与 `refresh` 的排版循环是**成对**的（本文件反复记过这条）。
        _label = "开始上课 · 就是这门" if hinted else "开始上课"
        view.addSubview_(mk(_label, x, lambda: on_start(r.course),
                            w=(148.0 if hinted else 92.0)))
        x += (148.0 if hinted else 92.0) + 8.0
    # ⭐ **纠错入口**（HAX G8「Support efficient dismissal」/ G9「efficient correction」）。
    #    只有被预选的那张卡有它 —— 其余四张本来就不需要纠错。
    # ⚠️⚠️ **必须和上面那道 `on_start is not None` 同进同出。** 按钮行的高度
    #    （`card_height` 的 `CARD_GAP_V + BTN_H`）**只在 `on_start is not None` 时才留**，
    #    而 `on_start is None`（上课中从菜单栏打开的面板，或验收跑器）时这一行**没有高度**。
    #    第一版漏了这道守卫 → 按钮照画在 `y=CARD_PAD` → **压在那条就绪行上**
    #    （实测截图抓到的；判据只查"有没有掉出卡片底部"，查不出"两行叠在一起"）。
    if on_start is not None and hinted and on_not_this is not None:
        view.addSubview_(mk("不是这门？", x, on_not_this, w=84.0))
        x += 84.0 + 8.0
    # ⭐ 「课次」= 这门课的上课记录（+ 没归课的那些，可以就地判给本课）。
    # ⚠️ 做成**按钮**而不是"双击卡片"：双击要自己接管 `mouseDown_` 并判 `clickCount`，
    #    而这块面板是磨砂的 —— `mouseDownCanMoveWindow` 那个雷本仓库咬过两次。
    #    按钮是这个面板既有的做法，零风险。（与设计稿的"双击课号"有偏差，已报备。）
    if on_sessions is not None and on_start is not None:
        view.addSubview_(mk("课次", x, lambda: on_sessions(r.course), w=68.0))
        x += 68.0 + 8.0
    # ⚠️ 原来这里还有一个「选择文件…」（单课加课件）。2026-09-28 删掉 ——
    #    作者说「卡片看着太繁杂」，而它确实是卡里唯一的边框元素、占 27% 的高度，
    #    五门课就是五个。
    #    **能力没丢**：
    #      · 拖到某张卡上 = 加到那门课（卡片本来就是落点，零视觉重量）；
    #      · 不想拖 → 用底部那个统一入口，选完文件在批量卡上**手动指定课号**
    #        （`BATCH_CHIP_W` 那个下拉，`["未分类"] + 各课号`）。
    #    ⚠️ 但**「开始上课」留着** —— 它是这个面板的主线动作
    #       （双击 → 选课 → 写 `.course` → 开录），删了主线就断了。

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

    # ── 右键菜单：删除课程（2026-09-28）──────────────────────────────
    # ⭐ 为什么是右键：`[一手]` HIG › Context menus 现行版逐字 ——「A context menu provides
    #    access to functionality that's directly related to an item, **without cluttering
    #    the interface**」。作者刚因为"看得太繁杂"删掉卡上那个「选择文件…」，
    #    不能再往卡上加常驻按钮。**右键零视觉重量。**
    # ⚠️⚠️ **用 `setMenu_()`，别想给视图类加 `menuForEvent_`。** 卡片的类是
    #    `panel.make_drop_target` 里 `objc_own.own("DropTarget", …)` 建的那个，而
    #    `objc_own.own` **按 key 缓存类**：第二次调用拿回同一个类、**新 namespace 被丢弃**
    #    （`make_drop_target` 注释里记着这条）。所以"从这边加个方法"是**静默无效**的。
    #    `setMenu_` 不需要子类化。2026-09-28 实测：面板**没被激活**时真右键也能到达视图
    #    （`menuForEvent_` 被调用，事件类型 3）。
    # ⚠️ `NSMenuItem` 的 target 也是**弱引用**（同 `NSButton.setTarget_`）→ 必须挂进
    #    `view._targets`。漏了就是「点菜单项静默没反应」，而 AppKit 不报错。
    #    `do_close` 的 `_sever` 会把它一起断掉（否则又是一条环）。
    # ⚠️⚠️ **`_target(…)` 的返回值必须在同一条语句里就绑到变量上。**
    #    2026-09-28 实测踩到：本来写的是
    #        `_mi.setTarget_(_target(lambda: …))`  +  下一句 `view._targets.append(_mi.target())`
    #    —— 那个临时对象**语句一结束就被回收**（`setTarget_` 不持有它），于是下一句
    #    拿到的是 `None`，`_targets` 里存的也是 `None`：菜单项**没有 target** →
    #    菜单**变灰点不动**（`autoenablesItems` 默认开，它沿响应链找能响应 `act:` 的，
    #    找不到就禁用）。**而所有单测全绿** —— 它们测的是函数，不是接线。
    #    可复现的判据：`item.target() is None`（`tests/test_entry_panel.py` ⑬ 钉了这条）。
    if on_delete_course is not None:
        from AppKit import NSMenu, NSMenuItem
        _mi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "删除课程…", "act:", "")
        _del_t = _target(lambda: on_delete_course(r.course))
        _mi.setTarget_(_del_t)
        view._targets.append(_del_t)
        _menu = NSMenu.alloc().init()
        _menu.addItem_(_mi)
        view.setMenu_(_menu)

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


def _mic_perm() -> str:
    """麦克风权限四态。⚠️ 查不出来给 `"unknown"`，**绝不因此拦人** ——
    `notice.mic_permission` 那条注释逐字：「调用方**必须按老行为继续**」。"""
    try:
        import notice
        return notice.mic_permission()
    except Exception:                                      # noqa: BLE001
        return "unknown"


def _has_api_key() -> bool:
    """配没配云端 key。⚠️ 纯本地读（env + 一个文件），不联网、不验证 ——
    验真要花一次 1-token 请求，那是用户点了「设置」之后的事。"""
    try:
        from cloud_translator import load_api_key
        return bool(load_api_key(None))
    except Exception:                                      # noqa: BLE001
        return False


def _ready_dismissed(state_root) -> bool:
    try:
        return ready.dismissed(root=state_root)
    except Exception:                                      # noqa: BLE001
        return False


def make_ready_strip(parent, y: float, w: float, items: list, *, on_click):
    """顶部就绪条 —— 「这台机器能不能上课」的三项。

    ⚠️ **每一项自己就是一个按钮**（无边框 `NSButton`），点了就地修那一项。
       不做「图标 + 分开的小按钮」：那会让一行里出现两种可点目标，而
       `[一手]` HIG › Feedback 那条要的是「状态就在它所描述的东西旁边」——
       一项一句话、点它就有动作，最直白。

    ⚠️ **宽度按实测文字定**（`sizeToFit()`），不写死 —— 三项的中文长短差很多，
       写死就会有一项被截断（`NSTextField` 超限**不报错也不省略号**）。

    ⚠️ 返回的 target 列表**必须由调用方持有** —— `setTarget_` 是弱引用，
       被 GC 掉就是「点了没反应，也不报错」。

    ⚠️ 只画**状态**，不画假进度条：见 `ready_short` 里那条 HIG 引用。
    """
    from AppKit import NSButton, NSColor, NSFont, NSMakeRect, NSView

    box = NSView.alloc().initWithFrame_(NSMakeRect(PAD, y, w, READY_H))
    box.setWantsLayer_(True)
    box.layer().setCornerRadius_(CARD_RADIUS)          # ⚠️ 与卡片同一档，别硬编码别的数
    box.layer().setBorderWidth_(HAIRLINE)
    box.layer().setBorderColor_(
        NSColor.whiteColor().colorWithAlphaComponent_(CARD_LINE_A).CGColor())
    box.layer().setBackgroundColor_(
        NSColor.whiteColor().colorWithAlphaComponent_(CARD_FILL_A).CGColor())

    targets: list = []
    buttons: dict = {}                                 # key -> NSButton（下载完改它的标题）
    x = READY_PAD
    btn_h = 22.0
    for it in items:
        b = NSButton.alloc().initWithFrame_(
            NSMakeRect(x, (READY_H - btn_h) / 2.0, 10.0, btn_h))
        b.setTitle_(ready_item_text(it))
        b.setBordered_(False)
        b.setFont_(NSFont.systemFontOfSize_(12.0))
        try:
            # ⚠️ **不是靠颜色区分状态** —— 颜色只是让"已就绪"那项稍微亮一点，
            #    真正的区分在 `ready_item_text` 那个形状标记和状态词上（HIG a11y）。
            b.setContentTintColor_(NSColor.whiteColor().colorWithAlphaComponent_(
                0.92 if it.get("state") == "ok" else 0.80))
        except Exception:                                  # noqa: BLE001
            pass
        t = _target(lambda k=it.get("key"): on_click(k))
        targets.append(t)
        b.setTarget_(t)
        b.setAction_("act:")
        b.sizeToFit()                                      # 宽度按实测文字
        b.setFrame_(NSMakeRect(x, (READY_H - btn_h) / 2.0,
                               b.frame().size.width + 8.0, btn_h))
        box.addSubview_(b)
        buttons[it.get("key")] = b
        x += b.frame().size.width + READY_ITEM_GAP

    parent.addSubview_(box)
    # ⚠️ 按钮也返回 —— 下载完要**就地改那一个的标题**，而不是整块重建
    #    （重建会连带把滚动位置和焦点都抖一下）。
    return targets, buttons


def pick_folder(*, start: str | None = None, message: str = "") -> str | None:
    """弹系统「选文件夹」框 → 选中的路径；取消 / 弹不出来 → `None`。

    ⚠️ **判据必须把这个函数换掉** —— 真 `runModal()` 会阻塞测试
       （`tests/test_entry_panel.py` 的 ㉑ 就是 monkeypatch 它）。
    ⚠️ 与 `_pick_batch_files`（选课件）同一套先例：`NSOpenPanel.openPanel()` +
       `runModal()`，只在主线程调（系统弹窗自己转事件循环，不算"联网/sleep"）。
    """
    from AppKit import NSOpenPanel
    p = NSOpenPanel.openPanel()
    p.setCanChooseFiles_(False)
    p.setCanChooseDirectories_(True)
    p.setAllowsMultipleSelection_(False)
    if message:
        p.setMessage_(message)
    if start:
        from Foundation import NSURL
        p.setDirectoryURL_(NSURL.fileURLWithPath_(start))
    if p.runModal() == 1 and p.URLs():
        return str(p.URLs()[0].path())
    return None


def _vault_now(state_root=None) -> str | None:
    """当前解析到的笔记库路径。**与 `main.run()` 同一条路**
    （`obsidian_writer.resolve_vault` 是唯一定义点）：`--vault` → `$OBSIDIAN_VAULT`
    → 记住的那个 → `None`。

    ⚠️ `root=state_root` **必须传** —— 否则测试沙盒里这一格读的是用户真身
       （`~/.classlive/vault`），隔离就是假的。
    """
    try:
        from obsidian_writer import resolve_vault
        return resolve_vault(root=state_root)
    except Exception:                                     # noqa: BLE001
        return None


def _vault_autodetect(state_root=None) -> tuple:
    """没设过笔记库时**自动找一次**（8-F；设计见 `docs/RESEARCH-entry-and-export.md §9.1`）。

    返回 `(该算的库路径 or None, 要贴状态行的一句话 or "")`：

      · **恰好 1 个**候选 → **自动设上**（唯一写盘处是 `~/.classlive/vault`，
        `root=state_root` 隔离；**读回来验证**，写失败当没找到 —— 界面不许撒谎）；
      · **多个** → **绝不猜**：不设，给一句话（点开时选择框会预指向最近那个）；
      · 0 个 / 探测炸了 → `(None, "")`，与没有这个功能时一模一样。

    ⚠️ 整段**不许抛** —— 它在面板构造路径上，任务只是"锦上添花"；
       探测失败让就绪条整个消失，方向反了。
    """
    try:
        cands = ready.vault_candidates()
        if not cands:
            return None, ""
        if len(cands) > 1:
            return None, (f"找到 {len(cands)} 个 Obsidian 库 —— "
                          f"点就绪条最后的「笔记库」挑一个（先指到最近打开的那个）")
        only = str(cands[0])
        from obsidian_writer import remember_vault
        remember_vault(only, root=state_root)
        ok = (paths.vault_config(root=state_root)
              .read_text(encoding="utf-8").strip() == only.strip())
        if not ok:
            return None, ""
        return only, f"找到你的 Obsidian 库：{only} —— 已设为笔记库（要换再点一下这一格）"
    except Exception:                                     # noqa: BLE001
        return None, ""


def build(*, on_start=None, glossary=None, sessions_dir=None, state_root=None,
          on_close=None, prepare_fn=None, suggest_fn=None,
          trash_fn=None, on_test_mode=None, test_mode=False) -> Handles | None:
    """建并显示面板。**失败返回 `None`**（调用方不必管 —— 同 `whatsnew.build`）。

    `prepare_fn` 是**验收用的注入点**，默认就是真的 `prep.prepare`：
    验收「拖一张卡」这条链路时，真跑会**写术语表 + `term_notes.json` + 调 DeepSeek 花钱**，
    所以那种验收必须能换掉它（同 `prep.prepare` 自己的 `chat=` / `build_fn=` 口子）。
    ⚠️ 具体签名见 `_run_prep` 里那一处调用 —— **只此一处**。

    `on_test_mode` 是测试模式开关的落点（**面板自己不写盘**，同 `on_start` 那条：
    谁去写 `.test-mode` 是调用方的事）—— 传 `None` = **不建那颗开关**
    （上课中从菜单栏打开的那档：课早在录了，开关没有意义）。
    `test_mode` 是它的**当前值**（调用方从盘上读来）。

    ⚠️ 构造失败**不能**吞掉 `objc_own.ObjcNameCollision`：那是程序缺陷，
       被吞掉会静默变成「面板打不开」，查起来极难（`objc_own.py` 文件头那次事故）。
    """
    try:
        return _build(on_start=on_start, glossary=glossary, sessions_dir=sessions_dir,
                      state_root=state_root, on_close=on_close, prepare_fn=prepare_fn,
                      suggest_fn=suggest_fn, trash_fn=trash_fn,
                      on_test_mode=on_test_mode, test_mode=test_mode)
    except objc_own.ObjcNameCollision:
        raise
    except Exception:                                     # noqa: BLE001
        if os.environ.get("CLASSLIVE_DEBUG"):
            import traceback
            traceback.print_exc()
        return None


def _build(*, on_start, glossary, sessions_dir, state_root, on_close,
           prepare_fn=None, suggest_fn=None, trash_fn=None,
           on_test_mode=None, test_mode=False) -> Handles:
    from AppKit import (NSButton, NSColor, NSFont, NSScreen, NSSearchField,
                        NSTextField,
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
    # ⭐⭐ **课表预选**（plan §5.4.4）。两条信号，都只说"时段对得上"，不猜内容：
    #    ① 导入过的课表（`.ics` 解析结果，存在 `~/.classlive/timetable.json`）
    #    ② **历史**：这门课过去常在周几几点上（零权限的兜底）—— 实测留一法 10/10
    #    ⚠️ **只改顺序、只改按钮标题**，**一个状态都不写**（`courses.set_attribution`
    #       那种都不碰）—— 认错课的代价是"那门课每一句翻译都在用错词"（§2.4 B 已定）。
    def _guesses():
        import store
        known = [r.course for r in rs]
        ent = T.load(store.load_json(paths.timetable(root=state_root), {}))
        hist = {}
        for c in known:
            slots = []
            for p in courses.session_files(sessions_dir, c):
                parts = p.stem.split("_", 2)
                if len(parts) == 3 and len(parts[1]) >= 2:
                    try:
                        slots.append((datetime.date.fromisoformat(parts[0]).weekday(),
                                      int(parts[1][:2])))
                    except ValueError:
                        continue
            if slots:
                hist[c] = slots
        return T.suggest(ent, datetime.datetime.now(), history=hist)

    S["guesses"] = _guesses()
    S["guess_i"] = 0

    def next_guess():
        """「不是这门？」→ 换下一条猜测；没有了就**不猜了**（`guess_i = -1`）。

        ⚠️ 不弹确认框：`[一手]` NN/g 逐字「Do not use confirmation dialogs for routine
           actions… if you cry wolf too many times, people will stop paying attention」。
        ⚠️ 也不写任何持久状态 —— 下一次开面板重新按课表/历史算，这是**每节课**的事。
        """
        g = S.get("guesses") or []
        i = S.get("guess_i", 0) + 1
        S["guess_i"] = i if i < len(g) else -1
        if S["guess_i"] < 0:
            set_status("好，那我不猜了 —— 你自己挑", 2.0)
        # ⚠️⚠️ **必须 `_later`**（2026-09-29 修）—— 这是卡上「不是这门？」按钮的
        #    action，同步 `refresh()` 会把 sender 所在那张卡整块拆掉，而
        #    `NSCell` 的跟踪循环**还在栈上** → 主线程被占住、**所有按钮都没反应**。
        #    同批新增的其它 action（`toggle_hit` / `close_sessions` / `cancel_import`
        #    / `adopt_session` / `confirm_import`）都规规矩矩用 `_later(refresh)`，
        #    这里漏了。机制见 `_later` 的 docstring。
        _later(refresh)

    body_h = body_height(len(rs), NSScreen.mainScreen().frame().size.height,
                         card_h=card_height(has_actions=(on_start is not None)))
    # ── 就绪条（2026-09-28）──────────────────────────────────────────
    # ⚠️ 算它要问权限、stat 四个模型目录 —— 都在本地、没网络。实测 ~85ms
    #    （其中 Qwen3 那条走 HF cache 的 `try_to_load_from_cache`，占大头）。
    #    面板打开不是一个热路径，先同步算；真变慢了再说（见 `ready.model_states`）。
    ready_items = []
    ready_targets: list = []
    vault_auto_msg = ""
    if not _ready_dismissed(state_root):
        try:
            vault_now = _vault_now(state_root)
            if not vault_now:
                # 8-F：从没设过 → 自动找一次（唯一候选设上；多个绝不猜）
                vault_now, vault_auto_msg = _vault_autodetect(state_root)
            ready_items = ready.items(
                perm=_mic_perm(), states=ready.model_states(root=state_root),
                has_key=_has_api_key(), vault=vault_now)
        except Exception:                                  # noqa: BLE001
            ready_items = []                               # 算不出来不该拦住面板
    rh = READY_H if ready_items else 0.0
    rg = READY_GAP if ready_items else 0.0

    # ⚠️ 高度**从下往上推**：页脚 → 落点条 → 滚动区 → 就绪条 → 标题。改任何一条边距时，
    #    `h` 与视图的 y **用的是同一组常数**，不是各写一遍（`card_height` 那条纪律）。
    drop_y = PAD + FOOT_H + DROP_GAP
    sc_y = drop_y + DROP_H + DROP_GAP
    ready_y = sc_y + body_h + 8.0                          # 就绪条的底边
    h = ready_y + rh + rg + TITLE_H + PAD

    mask = NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel
    fp = panel.build(NSMakeRect(0.0, 0.0, WIDTH, h), mask)
    win, ve = fp.window, fp.glass
    # ⚠️ borderless 默认没有阴影（Titled 的 overlay 才有）—— 同 whatsnew 的处理，
    #    留在调用点、不进 panel.py 的配方。
    win.setHasShadow_(True)
    # ⚠️ 这一句**没有**解决聚焦环（2026-09-29 实测）：设上之后
    #    `becomesKeyOnlyIfNeeded` 读回来确实是 `True`、`isKeyWindow` 也确实变 `False`，
    #    **但 `firstResponder` 仍是那个 `NSTextView`（搜索框的 field editor），蓝环照画**。
    #    → 学到一条：**聚焦环是按「控件是不是第一响应者」画的，和窗口 key 不 key 无关。**
    #    保留它是因为「面板弹出不抢 key」本身是想要的行为；治环的是搜索框那对
    #    `refusesFirstResponder`。
    try:
        win.setBecomesKeyOnlyIfNeeded_(True)
    except Exception as _e:                                   # noqa: BLE001
        print(f"⚠ 面板不抢 key 的开关没设上：{_e}")

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
    # ⭐⭐ **开局拒绝当第一响应者**，等面板真的上屏之后再放行 —— 这就是治那圈蓝环的那一步。
    #    机制（本机实测，2026-09-29）：**聚焦环是按「控件是不是第一响应者」画的，
    #    和窗口 key 不 key 无关** —— 把窗口弄成不 key（`becomesKeyOnlyIfNeeded`）后
    #    `isKeyWindow=False` 了，`firstResponder` 仍是这个框的 field editor，环照画。
    #    所以要在**控件这一层**拒绝，而不是在窗口那一层。
    # ⚠️ 这是 AppKit 社区的既有配方（SO 7024224 最高赞）：**开局 `refusesFirstResponder=YES`，
    #    出现之后再改回 `NO`** —— 不放回去的话用户**点它也没反应**（键盘永远进不去）。
    search_field.setRefusesFirstResponder_(True)
    # ⚠️⚠️ **别碰 `setBezeled_` / `setDrawsBackground_`。** 2026-09-29 试过一次，
    #     两件事同时发生：
    #     ① 那个蓝框**一点没变** —— 它是**聚焦环**，和 bezel 是分开画的两样东西；
    #     ② ⚠️ **搜索图标压到占位文字上** —— `NSSearchFieldCell` 给放大镜留的内边距
    #        是挂在 bezel 那套布局里的，关掉 bezel 就一起没了。
    #
    # ⭐ **那个蓝环的正解不在这里，在窗口那边** —— 见 `win.setBecomesKeyOnlyIfNeeded_(True)`
    #    那一段：让面板弹出时**不成为 key**，环就不出现；用户真点搜索框时才出现。
    # ⚠️ 而**改环的颜色/自己画**这条路是死的：Apple 没有设色的 API（`NSFocusRingType`
    #    只有三个枚举、无颜色参数），它**就是用户的系统强调色**（HIG 逐字
    #    「the system applies their chosen color… **replacing your accent color**」），
    #    而关掉默认指示不补替代 = **W3C F78**（违反 1.4.11 + 2.4.7）。
    # ⚠️⚠️ **`_target(…)` 的返回值必须留住。** 写成一行 `setTarget_(_target(…))` 的话，
    #     那个临时对象**语句一结束就被回收** → `target()` 变成 `None` →
    #     **回车静默无反应**，而 AppKit 不报任何错。`_target()` 的 docstring 讲的就是
    #     这个形状，这里是全文**唯一**漏掉的一处（其余八处都显式留了引用）。
    #     2026-09-28 OCR 审计抓出；本机实测复核：不保留时 `field.target()` 就是 `None`。
    #     ⚠️ **只存局部变量不够** —— `_build` 一返回局部就没了，必须挂到活到面板结束的
    #     容器上（`S`），并在 `do_close` 里像 `S["batch"]` 那样清掉。
    _search_t = _target(lambda: run_search(search_field.stringValue()))
    search_field.setTarget_(_search_t)
    S["search_target"] = _search_t
    search_field.setAction_("act:")            # NSSearchField 的回车走 action
    search_field.setSendsWholeSearchString_(True)   # 回车才发，别边打边搜
    ve.addSubview_(search_field)

    # ── 就绪条（2026-09-28）──────────────────────────────────────────
    # `[一手]` HIG › Feedback 逐字：「Consider integrating status feedback into your
    # interface. When status feedback is available **near the items it describes**,
    # people get important information without having to take action or leave their
    # current context.」—— 这条就是它放在卡片区正上方、而不是弹一个向导的理由。
    ready_buttons: dict = {}          # key -> NSButton（下载完改它那一项的标题）
    ready_targets: list = []          # ⚠️ 必须活到面板结束（`setTarget_` 是弱引用）

    def _retitle(key: str, text: str) -> None:
        b = ready_buttons.get(key)
        if b is None:
            return
        b.setTitle_(text)
        b.sizeToFit()
        b.setFrame_(NSMakeRect(b.frame().origin.x, (READY_H - 22.0) / 2.0,
                               b.frame().size.width + 8.0, 22.0))

    def _download_required() -> None:
        """把那三件必下的补齐。**在后台线程跑**，主线程只回写文字。

        ⚠️⚠️ **只下 `required=True` 的。** 可选的 Qwen3（938 MB）**必须由用户点**
           —— 作者 2026-09-28 的口径，也是 `doctor`/`update` 那条「大模型要不要下
           是用户的决定」。这里与 `update.run_step("models")` 用同一套口径。
        ⚠️ **不占 `S["busy"]`** —— 那是 prep 的串行器。占着它，用户在这十几分钟里
           就没法拖课件配课表了，而 HIG 那条恰恰说「别让大下载挡住 onboarding」。
        """
        if S.get("downloading"):
            return
        S["downloading"] = True
        _status("正在下语音模型 —— 你可以同时配课表 / 拖课件")

        def work() -> None:
            try:
                import doctor
                import update as update_mod
                left = [m for m in doctor.MODELS
                        if m.required and not doctor.model_present(m.path)]
                if not left:
                    _later(_retitle, "models", ready_item_text(
                        {"key": "models", "state": "ok", "detail": "已就绪"}))
                    _later(_status, "语音模型已经齐了")
                    return
                for m in left:
                    # ⚠️ 进度只能是**行级**（`download_model` 是 subprocess +
                    #    capture_output），所以转圈 + 换状态词，不做假的百分比条。
                    r = update_mod.download_model(
                        m, say=lambda s: _later(_status, s))
                    if not r["ok"]:
                        _later(_status, r["error"])
                        return
                _later(_retitle, "models", ready_item_text(
                    {"key": "models", "state": "ok", "detail": "已就绪"}))
                _later(_status, "语音模型齐了 —— 启动！")
            except Exception as e:                         # noqa: BLE001
                _later(_status, f"下载失败：{str(e)[:70]}")
            finally:
                S["downloading"] = False

        threading.Thread(target=work, daemon=True).start()

    def _open_key_entry() -> None:
        """点「翻译引擎」→ 填 key（DeepSeek + Jev）。**校验在后台线程跑。**

        ⚠️ 这是 AppKit 主线程回调：`notice.ask_text` 自己 `runModal`（既有做法，
           它会跑 runloop），**但校验要真调一次 API —— 那个必须丢到后台**。
        ⚠️ 校验用**真调一次**而不是"看格式像不像"：前者还能分清
           「key 不对」和「配额用完了」，后者两件事都报"看起来没问题"。
        ⚠️ **两栏都空 = 合法选择**，不是错误（HIG：配置必须能推迟）。
        """
        import keyentry
        # ⚠️⚠️ **这一行不能省**（2026-09-30 实测抓到的真 bug）：本模块**没有**
        #    模块级的 `notice`，另外两处用它都各自 `import`，唯独这里漏了 ——
        #    于是点「翻译引擎」**一跑就 `NameError`**，而 AppKit 把 action 里的
        #    异常**吞掉** → 用户看到的是「点了没反应」，跟「按钮没接上」一模一样。
        #    从 3.8.0 起一直如此（`pyright` 的 `reportUndefinedVariable` 一跑就报）。
        import notice

        # ⚠️ **已存的 key 要回填**（2026-10-01 作者实测反馈：填完重开变空白，
        #    以为没存上）。安全框里回填显示的是圆点 —— 不是明文，但"有东西"。
        try:
            from cloud_translator import load_api_key
            cur_ds = load_api_key(None) or ""
        except Exception:                                 # noqa: BLE001
            cur_ds = ""
        cur_jv = keyentry.load_jev(root=state_root)

        got = notice.ask_text(
            "填 API key",
            "只写进这台机器的 ~/.classlive/，不上传。",
            [{"key": "deepseek", "label": "DeepSeek key", "value": cur_ds,
              "hint": ("已存过" if cur_ds
                       else "翻译用的。不填也能上课 —— 退回本地模型，质量差一些。")},
             {"key": "jev", "label": "Jev key · 可选", "value": cur_jv,
              "hint": ("已存过" if cur_jv
                       else "给「重点句」和「课务」用。不填这两个功能就不出现。")}],
            fallback=None)
        if got is None:
            return
        ds = (got.get("deepseek") or "").strip()
        jv = (got.get("jev") or "").strip()
        if not ds and not jv:
            _status(keyentry.status_line(False, False), 2.0)
            return
        _status("正在验证…", 0)

        def work() -> None:
            # ⚠️ **验不过的不落盘** —— 存一把不能用的 key 只会让用户以为配好了，
            #    而真正上课时才发现是坏的（fail-soft 会把症状藏起来）。
            lines: list = []
            keep_ds = keep_jv = ""
            if ds:
                okd, md = keyentry.validate_deepseek(ds)
                lines.append(f"{'✅' if okd else '❌'} DeepSeek key "
                             + (md if okd else f"用不了（{md}）"))
                keep_ds = ds if okd else ""
            if jv:
                okj, mj = keyentry.validate_jev(jv)
                lines.append(f"{'✅' if okj else '❌'} Jev key "
                             + (mj if okj else f"用不了（{mj}）"))
                keep_jv = jv if okj else ""
            okk, msg = keyentry.save(deepseek=keep_ds, jev=keep_jv, root=state_root)
            # ⚠️ **写盘失败也要把校验结果一起说出来** —— 不然用户只看到
            #    "没写进去"，会以为是 key 的问题。
            lines.append(msg if okk else f"❌ {msg}")
            _later(_status, " · ".join(lines))
            if okk and keep_ds:
                # 就绪条就地变「云端翻译」—— 不用重开面板
                _later(_retitle, "engine", ready_item_text(
                    {"key": "engine", "state": "ok", "detail": "云端翻译（推荐）"}))
        threading.Thread(target=work, daemon=True).start()

    def _open_vault_picker() -> None:
        """点「笔记库」—— 选个文件夹**就地**设好（不用重开面板）。

        ⚠️ 弹窗走模块级的 `pick_folder`（判据把它换掉用）。取消 = 什么都不动。
        ⚠️ **界面不许撒谎**（同测试模式开关那条）：写没写进盘，以
           `paths.vault_config()` 那份**读回来**为准，不认"我以为写了"。
        """
        from obsidian_writer import remember_vault
        start = _vault_now(state_root)
        message = "选笔记库文件夹（Obsidian 库最合适）—— 笔记会写进它的 Lectures/"
        if not start:
            # 8-F：先看自动找的结果（唯一候选在 build 那一步就该设上了，
            #      走到这儿多半是"多个候选不猜"那一档）——
            #      起点指到最近打开 / 最新的那个，并在框里说清找到了几个。
            try:
                cands = ready.vault_candidates()
            except Exception:                              # noqa: BLE001
                cands = []
            if cands:
                start = str(cands[0])
                if len(cands) > 1:
                    message = (f"找到 {len(cands)} 个 Obsidian 库 —— "
                               f"先指到最近打开的那个；也可以选别的文件夹")
            else:
                # 智能起点：iCloud 的 Obsidian 容器（Mac 上最常见的库位置）
                ic = (pathlib.Path.home()
                      / "Library/Mobile Documents/iCloud~md~obsidian/Documents")
                start = str(ic) if ic.is_dir() else str(pathlib.Path.home())
        got = pick_folder(start=start, message=message)
        if not got:
            return
        remember_vault(got, root=state_root)
        try:
            ok = (paths.vault_config(root=state_root)
                  .read_text(encoding="utf-8").strip() == got.strip())
        except OSError:
            ok = False
        if not ok:
            _status("没记住（写盘失败）—— 这次先不生效，回头再试")
            return
        _retitle("vault", ready_item_text(ready.vault_item(got)))
        hint = ("" if (pathlib.Path(got) / ".obsidian").is_dir()
                else "（没找到 .obsidian —— 笔记会写进它的 Lectures/）")
        _status(f"笔记库设好了：{got}{hint}")

    def on_ready_click(key: str) -> None:
        """点就绪条上的某一项 —— 就地修那一项。

        ⚠️ 这是 AppKit 主线程回调（按钮 action）：**只许置标志 / 起线程 / 打印**，
           不许在这儿联网、sleep、下载 —— 同 `overlay._ask` 上方那条纪律。
        """
        if key == "mic":
            try:
                import notice
                if notice.mic_permission() == "denied":
                    notice.open_mic_settings()
                    _status("已打开系统设置 —— 在「隐私与安全性 → 麦克风」里打开 ClassLive")
                    return
            except Exception:                              # noqa: BLE001
                pass
            _status("麦克风没问题就不用管它；被拒时点这里会打开系统设置")
        elif key == "models":
            _download_required()
        elif key == "engine":
            # ⚠️ 2026-09-30 起**真的能点了** —— 之前在的那句注释写着
            #    「这一版**只说明，不做**…不假装能点」。现在开一个填 key 的框。
            _open_key_entry()
        elif key == "vault":
            _open_vault_picker()

    if ready_items:
        ready_targets, ready_buttons = make_ready_strip(
            ve, ready_y, WIDTH - 2 * PAD, ready_items, on_click=on_ready_click)
        if vault_auto_msg:
            # ⚠️ 走 `callAfter` 排队：这一行还在 `build()` 里跑，而 `S["panel"]`
            #    要到 build 返回前才挂上（`_status` 查的是"当前面板"）——
            #    直接调会写进上一个面板或写进空气。排到 run loop 起来之后再落。
            try:
                from PyObjCTools import AppHelper
                AppHelper.callAfter(_status, vault_auto_msg, 2.0)
            except Exception:                              # noqa: BLE001
                pass
        # ⚠️⚠️ **`make_ready_strip` 返回的 target 列表必须活到面板结束**
        #     （它的 docstring 逐字写着这条）。原来只接到一个**局部变量**上 ——
        #     `_build` 一返回那个列表就随栈帧没了 → 弱引用被回收 →
        #     **整条就绪条点了没反应，也不报错**。
        #     2026-09-28 本机实测：不挂容器时条上按钮 `target()` 就是 `None`，
        #     而同屏的落点条按钮**全都正常**（它们的 `_targets` 挂在自己的视图上）。
        #     ⚠️ 挂 `S` 上而不是挂视图：这些是标准 `NSView`/`NSButton`（没有 `__dict__`），
        #     挂不上去；`S` 是模块级、且 `do_close` 里有一处统一的断环表。
        S["ready_targets"] = ready_targets

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
    # ⚠️ 落点条上所有按钮的 target 集中在**一个**列表里，最后一次性挂给 `strip._targets`。
    #    原来每加一颗按钮就写一次 `strip._targets = [...]` —— 那是**覆盖**：
    #    后加的会把先加的挤掉 → 先建的那颗按钮静默失效（点了没反应，AppKit 不报错，
    #    单测也全绿，因为它们测的是被调用的函数不是接线）。同 `batch_rows` 那条。
    strip_targets: list = []
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
        # ⚠️ 判据是 `acceptable()` —— 与卡片那条**共用同一个定义点**
        #    （`extract.is_supported` 只管"能不能被抽取"，而下游还有 `.ics`→`run_import`）。
        #    原来只看 `bool(file_paths(pb))` → 文件夹、`.txt` 全高亮说"能收"，
        #    松手才在 prep 里判 unsupported（`REVIEW §9.2 #5`）。
        ok = acceptable(panel.file_paths(pb) or [])
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
        panel.make_label("把课件全拖到这里",
                         NSMakeRect(CARD_PAD, DROP_H - 40.0, HINT_W, 22.0), 15.0),
        panel.make_label("PDF / PPTX / DOCX · 课表 .ics · 文件夹也行",
                         NSMakeRect(CARD_PAD, DROP_H - 60.0, HINT_W, 16.0),
                         11.0, alpha=DIM),
        panel.make_label("自动认出哪份属于哪门课 —— 认不出的会让你核对",
                         NSMakeRect(CARD_PAD, DROP_H - 78.0, HINT_W, 16.0),
                         11.0, alpha=DIM),
    ]
    for _v in strip_holder["hint"]:
        strip.addSubview_(_v)

    # ⭐ 虚线描边 —— macOS 里「这是拖拽落点」的通用画法（实线读起来像卡片）。
    # ⚠️ `CAShapeLayer` 包 try：画不出虚线不该让整个面板起不来，退回上面那圈实线。
    try:
        from Quartz import CGPathCreateWithRoundedRect, CAShapeLayer
        _dash = CAShapeLayer.layer()
        _dash.setFrame_(strip.bounds())
        _dash.setPath_(CGPathCreateWithRoundedRect(
            NSMakeRect(0.5, 0.5, W_IN - 1.0, DROP_H - 1.0), CARD_RADIUS, CARD_RADIUS, None))
        _dash.setFillColor_(None)
        _dash.setStrokeColor_(NSColor.whiteColor()
                              .colorWithAlphaComponent_(0.22).CGColor())
        _dash.setLineWidth_(1.0)
        _dash.setLineDashPattern_([5.0, 4.0])
        strip.layer().addSublayer_(_dash)
        strip.layer().setBorderWidth_(0.0)         # 实线让位给虚线，别叠着
    except Exception:                                  # noqa: BLE001
        pass

    # ── 新增课程：底部那条**自己换成**输入行（2026-09-28）────────────────
    # ⭐ 形状是 **inline row**（提醒事项那种）：点「＋」让**同一块地方**变成输入行，
    #    不弹 sheet、也不在搜索框旁边再加一个**常驻**文本框 —— 面板里那个搜索框
    #    已经是「要打字的控件」的第一个例外，不要有第二个常驻的。
    # 📌 社区做法对得上（2026-09-28 调研）：这类就地输入行的通行写法是
    #    **控件建一次、只切 `setHidden_`**，而不是每次重建一个 —— 免得输入框在
    #    "文本 ↔ 输入框"之间换身份时丢焦点。下面四个控件就是建一次、`refresh()` 切显隐。
    #    （焦点也**要等视图真进了窗口再给**，所以给焦点的是 `refresh()`，不是 `open_add()`。）
    def open_add() -> None:
        """点「＋ 新增课程」—— 让底部那条换成输入行。**只切状态，不建任何东西。**"""
        _b = S.get("batch")
        if S.get("search") is not None or (_b is not None and not _b.get("need_course")):
            # ⚠️ 与批量/搜索**互斥**：那两种模式下这条的三个状态会同时亮出来。
            #    （正常路径走不到 —— 那两种模式里 ＋ 按钮本身是藏着的。）
            # ⭐ **例外：零课程那一档**（`need_course`）—— 那时这一行正是主线动作，
            #    文件已经拖进来了，用户要建课才能往下走。
            set_status("先确认或取消手上这一批，再新增课程", 1.0)
            return
        S["add"] = True
        try:
            add_field.setStringValue_("")
            add_reply.setStringValue_(ADD_HINT)
        except Exception:                                 # noqa: BLE001
            pass
        # ⚠️ 焦点交给 `refresh()` 去给，而且是**一次性**的（它 `pop` 掉这个键）——
        #    在这里直接 `makeFirstResponder_` 会落在一个**还藏着的**文本框上。
        S["_focus_add"] = True
        _later(refresh)

    def close_add() -> None:
        """收起输入行。

        ⚠️ **必须把键盘还出去**（`_release_focus`）：藏掉一个正被编辑的文本框，
           焦点守卫要等最多 0.5 秒才发现 —— 那半秒里用户在别的 app 按的键被吃掉。
           （不用恒真：`makeFirstResponder_(None)` 一调，field editor 当场就掉了。）
        ⚠️ 隐藏与重画都交给 `_later(refresh)` —— **绝不能在按钮的 action 里拆视图树**
           （`_later` 那条：NSButton 的跟踪循环还在栈上）。
        """
        _b = S.get("batch")
        if _b is not None and _b.get("need_course"):
            # ⚠️ **这个模式下这颗按钮的标题是「开始分类」**（`refresh()` 改的）——
            #    文件已经拖进来了，「取消」在这儿没有意义。
            #    ⚠️ 改标题必须和改行为在**同一条分支**上，否则会出现
            #    「按钮写着开始分类、点了却把输入行收起来」。
            _start_classify()
            return
        if S.get("add") is None:
            return
        S["add"] = None
        S.pop("_focus_add", None)
        try:
            add_field.setStringValue_("")
        except Exception:                                 # noqa: BLE001
            pass
        _later(refresh)
        _release_focus()

    def submit_add() -> None:
        """回车 / 点「新建」。**判断交给 `courses.plan_add`，这里只执行。**"""
        if S.get("add") is None:
            return
        r = courses.plan_add(add_field.stringValue(),
                             courses.list_courses(glossary, state_root=state_root))
        if r["action"] != "create":
            # 没成的时候**行留着、字留着** —— 让他就地改，别把刚敲的弄丢
            add_reply.setStringValue_(r["text"])
            return
        try:
            made = courses.create(glossary_of(r["course"]), r["course"])
        except OSError as e:
            add_reply.setStringValue_(f"建不了：{e}")
            return
        _b = S.get("batch")
        if _b is not None and _b.get("need_course"):
            # ⭐ 零课程批量模式：建完课**立刻免费分一遍**（按文件名，0 次 API），
            #    然后**留在输入行里**让用户接着建下一门 —— 别把他踢出去。
            set_status(f"建好了：{r['course']} —— 名字里带它的课件已经归好，"
                       f"继续建下一门，或点「开始分类」", 1.0)
            _recode_pending()
            return
        close_add()                       # ⚠️ 它自己会 `_later(refresh)`
        set_status(f"{'建好了' if made else '已经有'}：{r['course']}"
                   f" —— 拖课件进来，或点卡片上的「开始上课」")

    def add_course(text: str) -> None:
        """⭐ **验收跑器的程序化入口** —— 等价于「点 ＋ → 敲入 → 回车」。

        ⚠️ 与手输**走同一条路**（`submit_add`），不是另写一份判断 ——
           `start_batch` / `search` 两条也是这个理由（跑器里没有真键盘）。
        """
        if S.get("add") is None:
            open_add()
        add_field.setStringValue_(text)
        submit_add()

    # ⚠️ 三个 `def` 必须**在**建控件之前 —— `_target(close_add)` 是**提前求值**的，
    #    放到后面会 `UnboundLocalError`（它们是 `_build` 的局部名）。
    #    lambda 那几处倒是可以后置（调用时才查名），但别只对一半，读起来会以为有玄机。
    add_field = NSTextField.alloc().initWithFrame_(
        NSMakeRect(CARD_PAD, ADD_ROW_Y, W_IN - CARD_PAD - 208.0 - 8.0 - CARD_PAD,
                   BTN_H))
    add_field.setPlaceholderString_(ADD_PLACEHOLDER)
    add_field.setFont_(NSFont.systemFontOfSize_(13.0))
    _add_submit_t = _target(submit_add)
    add_field.setTarget_(_add_submit_t)     # ⚠️ 弱引用 —— 存进 `strip_targets` 留它
    add_field.setAction_("act:")           # ⚠️ 回车走 action —— 2026-09-28 实测过
    strip_targets.append(_add_submit_t)
    add_reply = panel.make_label(ADD_HINT, NSMakeRect(
        CARD_PAD, ADD_REPLY_Y, W_IN - 2 * CARD_PAD, 17.0), 11.0, alpha=DIM)

    _add_cancel = NSButton.alloc().initWithFrame_(
        NSMakeRect(W_IN - CARD_PAD - 100.0, ADD_ROW_Y, 100.0, BTN_H))
    _add_cancel.setTitle_("取消")
    _add_cancel.setBezelStyle_(1)
    _add_cancel.setFont_(NSFont.systemFontOfSize_(12.0))
    _add_cancel_t = _target(close_add)
    _add_cancel.setTarget_(_add_cancel_t)                 # ⚠️ 弱引用 —— 靠这里留
    _add_cancel.setAction_("act:")
    # ⚠️⚠️ **Esc 靠这一行，不靠委托。** 2026-09-28 实测（真 Esc 键，CGEventPostToPid）：
    #    非激活面板里按 Esc，`controlTextDidEndEditing:` **一条都不触发**；
    #    同一个框上真 Return 键**会**触发（movement=0x10）且 action 也响。
    #    ⚠️ 网上流行的那句「movement == 0 就是用户按了 Esc」**连值都是错的** ——
    #    本机 SDK 一手（`AppKit/…/Headers/NSText.h:166-174`）逐字：
    #      `NSTextMovementReturn = 0x10` · **`NSTextMovementCancel = 0x17`** · `Other = 0`，
    #    而 0 那档的注释写着「movements that do not fall under any of the other values」。
    #    （我实测到的 movement=0 全部来自「焦点被挪走」—— 正好对上 `Other`。）
    #    换 `cancelOperation:` 也不行：`NSResponder.h:132` 逐字「NSResponder does not
    #    implement any of them. NSTextView implements a certain subset」，
    #    而整个 AppKit 头目录里 `cancelOperation` **只出现一处**（NSResponder.h:266 的协议声明）
    #    —— 实测调它确实抛 unrecognized selector。
    #    → 而「取消按钮设 `\x1b` 键等价」是 macOS 自己的机制（NSAlert 文档逐字：
    #      any button titled "Cancel" has a key equivalent of Escape），**当场就响**。
    #    ⚠️ 它藏起来时还吃不吃 Esc **没有可靠来源**（Apple 那句 "whether the view is hidden"
    #      讲的是 key view loop，不是 `performKeyEquivalent:`，别混用）——
    #      靠 `close_add()` 开头的空判兜住，行为上是无害的。
    _add_cancel.setKeyEquivalent_("\x1b")
    strip_targets.append(_add_cancel_t)

    _add_ok = NSButton.alloc().initWithFrame_(
        NSMakeRect(W_IN - CARD_PAD - 208.0, ADD_ROW_Y, 100.0, BTN_H))
    _add_ok.setTitle_("新建")
    _add_ok.setBezelStyle_(1)
    _add_ok.setFont_(NSFont.systemFontOfSize_(12.0))
    _add_ok_t = _target(lambda: submit_add())
    _add_ok.setTarget_(_add_ok_t)                         # ⚠️ 弱引用 —— 靠这里留
    _add_ok.setAction_("act:")
    strip_targets.append(_add_ok_t)

    strip_holder["add_widgets"] = [add_field, add_reply, _add_cancel, _add_ok]
    # ⚠️ 单独留一份引用：**零课程模式**要把它的标题改成「开始分类」——
    #    那一档下文件已经拖进来了，「取消」没有意义（见 `close_add`）。
    strip_holder["add_cancel"] = _add_cancel
    for _v in strip_holder["add_widgets"]:
        _v.setHidden_(True)                               # 默认不出现
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
        NSMakeRect(W_IN - CARD_PAD - PICK_W, (DROP_H - BTN_H) / 2.0, PICK_W, BTN_H))
    _pick_btn.setTitle_("选择文件…")
    _pick_btn.setBezelStyle_(1)
    _pick_btn.setFont_(NSFont.systemFontOfSize_(12.0))
    _pick_t = _target(_pick_batch_files)
    _pick_btn.setTarget_(_pick_t)                         # ⚠️ 弱引用 —— 靠这里留
    _pick_btn.setAction_("act:")
    strip_targets.append(_pick_t)
    strip.addSubview_(_pick_btn)
    strip_holder["hint"].append(_pick_btn)

    # ⭐ 「＋ 新增课程」（2026-09-28）—— **就放在这里，不放右上角**。
    #    理由：点了之后出现的那一行输入框**就在它下面**（底部那条自己换成输入行）。
    #    放在标题行右侧的话，用户在面板顶上点一下、输入框在面板最底下冒出来，
    #    中间隔着四百点且**没有任何动效** —— 那是一处静默的可发现性失败。
    #    （`docs/PLAN-entry-panel.md` 的草图把它画在右上角；本轮的实现挪到了这里。）
    _add_btn = NSButton.alloc().initWithFrame_(
        NSMakeRect(W_IN - CARD_PAD - PICK_W - 8.0 - ADD_W, (DROP_H - BTN_H) / 2.0,
                   ADD_W, BTN_H))
    _add_btn.setTitle_(ADD_TITLE)
    _add_btn.setBezelStyle_(1)
    _add_btn.setFont_(NSFont.systemFontOfSize_(12.0))
    _add_t = _target(open_add)
    _add_btn.setTarget_(_add_t)                           # ⚠️ 弱引用 —— 靠这里留
    _add_btn.setAction_("act:")
    strip_targets.append(_add_t)
    strip.addSubview_(_add_btn)
    strip_holder["hint"].append(_add_btn)

    # ⭐ 测试模式开关（2026-09-30，`docs/PLAN-test-mode.md` §13.3）──────────
    # 📌 **放这儿不放卡片上**：作者 2026-09-28 删过卡片上的「选择文件…」，
    #    原话「**卡片看着太繁杂**」—— 5 张卡各挂一个开关就是同样的噪音。
    #    而这是个**全局**状态（这节课录不录、传不传），底部那条正是全局控件的位置。
    # ⚠️ `on_test_mode is None` = 这个面板不是"开课前"那条路起的（上课中从菜单栏开的）
    #    —— 那时**这节课早在录了**，开关没有意义 → **不建**（同 `on_start` 那条纪律）。
    # ⚠️ 状态活在**闭包**里，不进模块级的 `S`：`S` 是跨面板共享的，
    #    而开关的初值每次都由调用方从盘上读（`entry_launch.read_test_mode`）。
    tm_on = [bool(test_mode)]
    if on_test_mode is not None:
        tm_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(W_IN - CARD_PAD - PICK_W - 8.0 - ADD_W - 8.0 - TEST_W,
                       (DROP_H - BTN_H) / 2.0, TEST_W, BTN_H))
        tm_btn.setTitle_(test_mode_title(tm_on[0]))
        tm_btn.setBordered_(False)                 # ⚠️ 见 `TEST_W` 那段：状态→无边框
        tm_btn.setFont_(NSFont.systemFontOfSize_(12.0))
        try:
            # ⚠️ 只是深色玻璃上的可读性，**两态同一个值** —— 不许拿颜色表达状态。
            tm_btn.setContentTintColor_(
                NSColor.whiteColor().colorWithAlphaComponent_(0.92))
        except Exception:                                  # noqa: BLE001
            pass

        def toggle_test_mode() -> None:
            """点一下 = 翻一次，**当场落盘**。

            ⚠️ **失败要弹回原状** —— 盘上没写成就显示「开」，用户会以为在采集，
               其实什么都没记。同 `.course` 那次事故的形状（2026-09-29，
               `entry_launch` 的 `UNSAVED`）：**界面说的和盘上写的是两件事**。
            ⚠️ 回调**同步调**：它只写一个几字节的文本文件，不碰视图树 ——
               `_later` 那条纪律管的是「别在 action 里拆自己所在的视图」，这里不适用。
            """
            on = not tm_on[0]
            ok = True
            try:
                ok = on_test_mode(on) is not False
            except Exception:                              # noqa: BLE001
                ok = False
            if not ok:
                set_status("⚠ 测试模式没存下来（.test-mode 写不了）—— 这次不算数", 2.0)
                return
            tm_on[0] = on
            try:
                tm_btn.setTitle_(test_mode_title(on))
            except Exception:                              # noqa: BLE001
                pass

        tm_t = _target(toggle_test_mode)
        tm_btn.setTarget_(tm_t)                    # ⚠️ 弱引用 —— 靠 `strip_targets` 留它
        tm_btn.setAction_("act:")
        strip_targets.append(tm_t)
        strip.addSubview_(tm_btn)
        # ⚠️ 也进 `hint` 那组：**批量/搜索/新增那几档会把它藏起来**
        #    （`refresh()` 按 `strip_holder["hint"]` 逐个 `setHidden_`）——
        #    那些档下右下角是「取消/确认」，多一颗开关会挤在同一块地方。
        strip_holder["hint"].append(tm_btn)

    # ⚠️ **一次性挂上**（见 `strip_targets` 的说明）—— 别在上面每一处各写一遍。
    strip._targets = strip_targets

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

        ⚠️ 判据与 `overlay._is_editing` **同源但更强**：那边只问自己那一个 `_input`，
           这里问「**任何** field editor」—— 本面板会有**两个**文本框（搜索 + 新增课程），
           盯单个控件会漏掉另一个。

        ⚠️⚠️ **出错时返回 `True`，不是 `False`** —— 2026-09-28 修正。
           这里原来返回 `False`，而它自己的注释写着「判据抄 `overlay._is_editing`」——
           **判据抄了，倒向抄反了**，理由没跟着过来。overlay 那段逐字写着：
             「返回 False 会让不变量在没有可靠依据的情况下**主动抢走焦点** ——
               正在打字时被抢是最坏的失败模式。检测不出来就当作在打字, 保守。」
           → 两份拷贝各自漂、而且漂在**最不该漂的那一位**上，这正是本仓库
             「一条纪律两处定义」的同族事故。
        """
        try:
            fr = win.firstResponder()
            return bool(fr) and fr is not win and bool(fr.isFieldEditor())
        except Exception:                                     # noqa: BLE001
            return True                                       # 判不出来 = 当他在打字

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
                       "busy": True, "err": "", "open": None, "open_text": None}
        # ⚠️⚠️ **三个模式必须真互斥**（2026-09-29 修）。它们各从自己的入口进、
        #    不共用一个状态位，而 `refresh` 里是按 `ics → 课次 → 搜索` 的顺序
        #    挨个 `return` 的 —— 所以只设 `S["search"]` 是**不够**的：
        #    在课次卡或导入卡上敲回车，`refresh` 会先撞上前面那两支并 `return`，
        #    状态行报「命中 N 处」而屏上**毫无变化**。
        #    （原来那句注释写着"真同时开了，这里是 search 优先" —— 新增
        #     `ics`/`课次` 两支之后它就**不成立了**，这正是本条要修的。）
        #    ⚠️ 选「进来就清掉另外两个」而不是「把搜索那支提到最前」：后者要搬代码块，
        #       而且会让屏上优先级与实际入口脱节；清掉之后三个状态是**真的**互斥，
        #       与文件里那条设计声明一致。代价只是回到课次卡要多点一下。
        S["sessions"] = None
        S["ics"] = None
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

    def toggle_hit(i: int):
        """点一条命中：**就地展开 / 收起**那段原文（`find.read`）。

        ⚠️ 这是「命中的路径只是个提示，读全文是另一步」那半 —— 本仓库
           `find.read` 的 docstring 逐字引的就是 `silverstein/minutes` 的成例。
        ⚠️ 展开的是**会话/笔记原文**，不是 `find` 给模型的那种带抬头与截断警告的格式：
           面板只要「这一句的前后文」。
        """
        s = S.get("search")
        if not s or s.get("busy"):
            return
        if s.get("open") == i:
            s["open"], s["open_text"] = None, None
            _later(refresh)
            return
        hits = s.get("hits") or []
        if not (0 <= i < len(hits)):
            return
        import find as find_mod
        h = hits[i]
        try:
            s["open_text"] = clean_read_text(find_mod.read(h.path, *read_span(h.line)))
        except Exception as e:                                # noqa: BLE001
            s["open_text"] = f"⚠ 读不了这份原文：{type(e).__name__}: {e}"
        s["open"] = i
        _later(refresh)

    # ── 课次列表（卡上那个「课次」按钮进的）────────────────────────────
    def open_sessions(course: str):
        """一门课的**上课记录** + 「没归课的那些」。⚠️ 要读盘 → 工作线程。"""
        S["sessions"] = {"course": course, "rows": [], "orphans": [],
                         "busy": True, "err": ""}
        _later(refresh)

        def work():
            import corpus as corpus_mod
            try:
                known = courses.list_courses(glossary, state_root=state_root)
                rows = [session_row(p, _sess_words(p))
                        for p in sorted(courses.session_files(sessions_dir, course),
                                        reverse=True)]
                orph = [session_row(p, _sess_words(p))
                        for p in courses.orphan_files(sessions_dir, known)]
                got = {"rows": rows, "orphans": orph}
            except Exception as e:                            # noqa: BLE001
                got = {"rows": [], "orphans": [],
                       "err": f"{type(e).__name__}: {e}"}
            s = S.get("sessions")
            if s is None or s.get("course") != course:
                return                                        # 期间又开了别的课
            s.update(got)
            s["busy"] = False
            from PyObjCTools import AppHelper
            AppHelper.callAfter(refresh)

        threading.Thread(target=work, daemon=True).start()

    def _sess_words(p) -> int:
        try:
            return corpus.session_words(
                p.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            return 0

    def adopt_session(stem: str):
        """把一节没归课的记录判给当前这门课。

        ⚠️ 写的是**旁路文件**（`courses.set_attribution`），会话 `.md` 一个字节不动
           —— 抬头是三方共享契约（`obsidian_writer` 写 / `_parse` 读回 / `cl last` grep）。
        """
        s = S.get("sessions")
        if not s or s.get("busy"):
            return
        courses.set_attribution(sessions_dir, stem, s["course"])
        set_status(f"已把 {stem[-14:]} 判给 {s['course']}", 2.0)
        open_sessions(s["course"])            # 重开一次，两组列表都刷新

    def close_sessions():
        S["sessions"] = None
        _later(refresh)

    # ── 课表导入（把 `.ics` 拖进来）──────────────────────────────────
    def run_import(files: list):
        """⭐ **先解析给你看清楚，确认了才建课** —— 这一步一个字节都不落盘。

        ⚠️ **批量必确认**：业界对批量动作的通行做法，而且 Google / Apple 的日历
           导入**都没有撤销功能**（调研核过）→ 猜错了只能自己收拾。
        ⚠️ 解析在工作线程里（读文件 + 可能上兆的字节）。
        """
        S["ics"] = {"rows": [], "warn": [], "slots": {}, "busy": True, "err": ""}
        _later(refresh)

        def work():
            keep: dict = {}
            try:
                known = courses.list_courses(glossary, state_root=state_root)
                rows, warn = [], []
                for f in files:
                    data = pathlib.Path(str(f)).read_bytes()
                    got, w = T.parse(data)
                    warn.extend(w)
                    for c in got:
                        r = ics_course_row(c, known=known)
                        rows.append(r)
                        # ⭐ 把**时段**留下来 —— 导入完 `.ics` 就扔的话，
                        #    §5.4.4 那个预选**没有数据源**（作者 2026-09-29 发现的缺口）。
                        if r["state"] != "bad":
                            keep.setdefault(r["want"], []).extend(c.slots)
                res = {"rows": rows, "warn": warn, "slots": T.dump(sorted(keep.items()))}
            except Exception as e:                            # noqa: BLE001
                res = {"rows": [], "warn": [],
                       "err": f"{type(e).__name__}: {e}"}
            s = S.get("ics")
            if s is None:
                return
            s.update(res)
            s["busy"] = False
            from PyObjCTools import AppHelper
            AppHelper.callAfter(refresh)

        threading.Thread(target=work, daemon=True).start()

    def confirm_import():
        """确认 → 真的建课。**只建 `state == "new"` 那些**（已有的跳过、坏的跳过）。"""
        s = S.get("ics")
        if not s or s.get("busy"):
            return
        made = []
        for r in s.get("rows") or []:
            if r["state"] != "new":
                continue
            try:
                if courses.create(courses.glossary_file(glossary, r["want"]), r["want"]):
                    made.append(r["want"])
            except Exception:                                 # noqa: BLE001
                pass                                          # 一门坏不影响其余
        S["ics"] = None
        # ⭐ **把时段存下来** —— 这是预选（§5.4.4）唯一的数据源。
        #    ⚠️ 只在用户**点了确认**之后写（前面一直没落盘）。
        if s.get("slots"):
            try:
                import store
                store.save_json(paths.timetable(root=state_root), s["slots"])
                S["guesses"] = _guesses()          # 立刻生效，不用重开面板
                S["guess_i"] = 0
            except Exception as _e:                            # noqa: BLE001
                print(f"⚠ 课表存不下来（预选会没有数据源）：{_e}")
        # ⚠️ 有几个没建成也要说 —— 别让"新建了 3 门"读起来像"4 门都成了"
        _skip = len([r for r in (s.get("rows") or []) if r["state"] != "new"])
        set_status(f"新建了 {len(made)} 门课" +
                   (f"，跳过 {_skip} 门" if _skip else ""), 3.0)
        # ⚠️⚠️ **必须 `_later`**（2026-09-29 修）—— 这是导入确认卡上「新建这些课」
        #    那个 `NSButton` 的 action，同步 `refresh()` 会把 sender 所在的导入卡
        #    （连同 sender 自己）在 `NSCell` 的跟踪循环**还在栈上**时拆掉。
        #    症状见 `_later` 的说明：**主线程被占住 → 之后所有按钮都点不动**。
        _later(refresh)

    def cancel_import():
        S["ics"] = None
        _later(refresh)


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
        """拖/选一堆文件 → 后台分类 → 映射卡。**只出建议，不写盘**（写盘归 `prep`）。

        ⭐ **零课程时一次模型都不调**（2026-09-28 加）—— 那时候选列表是空的，
        模型按 `classify.SYS` 的规矩**只能**答"都不属于"，跑 N 次纯粹烧钱，
        而用户拿到的是 N 行「未分类」。改成先让他把课建出来（见 `_pending_card`）。

        ⚠️ 这一档在业界也是同一个答案：`paperless-ngx` 没有可训练项时
        「**不训练，还把已有模型文件删掉**」、`DEVONthink` 的 Classify
        「**is disabled if DEVONthink is not sure enough**」、我们自己的
        `classify.py` 早就写着「宁可答空，不要硬凑」。
        """
        # ⭐ **落点条与卡片走同一条分流**（2026-09-29 补的这一半）。
        #    原来这里只 `_expand`，而 `.ics` 不在课件支持集里 → 拖课表到**底部落点条**
        #    时 `acceptable()`（高亮）说"收"、这里却回"这些都不收" ——
        #    **悬停判的和松手做的是两件事**，正是 `acceptable()` 的 docstring 里
        #    想消灭的那个形状。⚠️ 与 `run_prep` 同形，两处不许再分叉。
        #    ⚠️ 放在批量忙检查**之前**：导入有自己独立的忙状态，"另一批在核对"
        #       跟"能不能导入课表"是两件事（同 `run_prep` 的理由）。
        _ics, _rest = drop_split(paths)
        if _ics:
            run_import(_ics)
            if _rest:
                set_status(f"课表先导入了 —— 剩下这 {len(_rest)} 份课件请再拖一次",
                           3.0)
            return True
        if S.get("batch") is not None:
            set_status("已经在核对这一批了 —— 先确认或取消", 1.0)
            return False
        from extract import expand as _expand
        files, dropped = _expand(_rest)
        if not files:
            set_status("这些都不收（只认课件 PDF / PPTX / DOCX、课表 .ics；"
                       "文件夹取里面一层）", 1.0)
            return False
        names = courses.list_courses(glossary, state_root=state_root)
        S["batch"] = {"verdicts": [], "busy": True, "total": len(files),
                      "dropped": len(dropped), "error": ""}
        if not names:
            S["batch"]["busy"] = False
            S["batch"]["need_course"] = True
            S["batch"]["pending"] = [str(p) for p in files]
            S["batch"]["matched"] = {}
            title_lbl.setStringValue_(f"准备归档 {len(files)} 份课件")
            set_status(f"还没有课 —— 先建课号，我再开始分（这 {len(files)} 份先放这儿）",
                       1.0)
            # ⚠️ 让底部那条变成**输入行**：这一档下它才是主线动作。
            open_add()
            return True
        title_lbl.setStringValue_(f"准备归档 {len(files)} 份课件")
        _later(refresh)
        set_status(f"扫描 0/{len(files)}…")
        _classify(files)
        return True

    def _classify(files):
        """后台跑分类。**只出建议，不写盘。**（`run_batch` 与「开始分类」共用）"""
        S["batch"]["busy"] = True
        _later(refresh)

        def work():
            # ⚠️⚠️ **两个都要在开头抓下来**：
            #   · `got` 的预读 —— 零课程那条路先把「按文件名免费认出来的」放进去了，
            #     下面必须**接着往后加**（见 `suggest` 那句）；
            #   · `mine` 是**批次代际守卫** —— 这个线程比用户慢，期间用户完全可能
            #     「取消」再拖一批，那时 `S["batch"]` 已经换成**另一个 dict**。
            #     只判 `b is None` 的话，上一批的结果会**覆盖**用户正在核对的新批次，
            #     连 `busy` 一起置假 → 看着像"新批次跑完了"，其实内容是旧的。
            #     `run_search.work` 早就用 `s.get("q") != q` 防了同一件事。
            #
            # ⚠️⚠️ **`mine is None` 必须一起判**（2026-09-30 修，pyright 抓出来的）。
            #    只写 `S.get("batch") is not mine` 有个洞：用户在 `_classify` 与
            #    这个线程真正开跑之间点了「取消」→ `S["batch"] = None` →
            #    `mine` 也是 `None` → `None is not None` 判成 **False**（"没换过"）→
            #    **不返回** → 落到下面 `mine["verdicts"]` → `TypeError`。
            #    ⚠️ 这一格**只有"取消"这一条路**能撞到，而且线程是 daemon、
            #       异常没人接 —— 表现为"点了取消之后面板偶尔不刷新"，很难查。
            mine = S.get("batch")
            if mine is None:
                # ⚠️ **早退，不只是"最后别写回"**：下面那一大段会真干活 ——
                #    `load_api_key` 读盘、`courses.list_courses`、
                #    **`corpus.keywords()` 把每门课的转录全读一遍**（那是这里最贵的一步）。
                #    批次已经取消了还跑这些，纯属白干（而且下面 `S["batch"]["weak"]`
                #    会直接 `TypeError` 掉进 except，白跑完还得再报一次错）。
                #    这一格只在「`_classify` 与线程真正开跑之间被取消」时命中，
                #    窗口很小，但**它是唯一能保证下面那段一次都不跑的写法**。
                # ⚠️ 判据两处缺一不可（变异验证抓过）：删掉它 → `corpus.keywords`
                #    会被调；只删最后那句守卫 → 落到 `mine["verdicts"]` 崩。
                return
            got, err = list((mine or {}).get("verdicts") or []), ""
            try:
                import classify
                import corpus as corpus_mod
                from cloud_translator import load_api_key
                key = load_api_key(None)
                names = courses.list_courses(glossary, state_root=state_root)
                # ⭐ **课程描述优先用上课转录**（`corpus.py`）—— 实测每门课
                #    19%-56% 的词是它独有的（`monopoly` / `sociology` / `epistemology`…），
                #    远好过术语表那 12 条（拿它当尺子实测精确率只有 7-12%）。
                #    ⚠️ 读转录是 I/O（本机 2 MB）→ 放在**这个工作线程**里，别搬主线程。
                _words, _degraded = corpus_mod.keywords(
                    names, sessions_dir=sessions_dir)
                briefs = classify.briefs(
                    courses.glossary_dir(glossary), names,
                    corpus={c: corpus_mod.describe(v) for c, v in _words.items()})
                # ⚠️ **哪些课没有语料，界面上要说出来**（见 `_batch_ready`）——
                #    没术语表也没上课记录的课，描述退化成一个光秃秃的课号，
                #    判别力≈0，而用户有权知道自己"为什么它认不出来"。
                # ⚠️ `_degraded` 已经是「拿不出词表的课 + **为什么**」——
                #    它和"词表为空"是同一件事，**别再算一份 `no_corpus`**
                #    （两份清单迟早对不上，而这是"哪些课认不准"的清单）。
                # ⚠️⚠️ **写进捕获的那一份 `mine`，不是 `S["batch"]`**（2026-10-01 审核
                #    指出，pre-existing）：`S["batch"]` 此刻可能已经是**下一批**了 ——
                #    「取消后又重拖」会把 A 批的「认不准」名单**盖进 B 批的字典**，
                #    而 B 的卡片会拿它当自己的结果显示。
                mine["weak"] = dict(_degraded)
                mine["has_key"] = bool(key)
                # 顺带查一次代际：期间被取消 / 换批就别再往下跑 ——
                # 下一段就是真花钱的 `suggest`（这也保住了原来靠 TypeError 偶然做到的事）。
                if S.get("batch") is not mine:
                    return
                ask = classify.make_ask(key) if key else (lambda prompt: None)

                def prog(i, n, name):
                    # ⚠️ 在工作线程里被调 —— UI 回写一律回主线程（CLAUDE.md 的不变量）。
                    #    用 `_status`（查**当前**面板），不要捕获本代的 `set_status`。
                    from PyObjCTools import AppHelper
                    AppHelper.callAfter(_status, f"扫描 {i}/{n}：{name[:38]}")

                # ⚠️ `suggest_fn` 是**验收用的注入点**（同 `prepare_fn` 那条）——
                #    验收批量那条路时真跑会调 DeepSeek 花钱，所以要能换掉。
                suggest = suggest_fn or classify.suggest
                # ⚠️⚠️ **`+`：接着已有的 verdict 往后加，不能覆盖。** `suggest()` 内部是
                #     `out = []` 从零重建，而且只为传进去的 `files`（= 还没归好的那些）
                #     出 verdict —— 直接赋值会把免费认出来的那几份**静默丢光**
                #     （结果列表上它们凭空消失，用户刚看着它们被认出来）。
                #     上面那句预读本来就是为这个留的，注释写了意图而代码没照做。
                #     2026-09-28 OCR 审计发现。
                got = got + suggest(files, courses=names, course_briefs=briefs,
                                    head_of=classify.head_of, ask=ask,
                                    on_progress=prog)
            except Exception as e:                            # noqa: BLE001
                err = f"{type(e).__name__}: {e}"
            if S.get("batch") is not mine:                    # 期间被取消 / 换了一批
                return
            mine["verdicts"], mine["busy"], mine["error"] = got, False, err
            from PyObjCTools import AppHelper
            AppHelper.callAfter(_batch_ready)

        threading.Thread(target=work, daemon=True).start()
        return True

    def _recode_pending():
        """⭐ **免费**再分一遍：只按文件名里的课号（`classify.by_code`，0 次 API）。

        每建一门课就跑一次 —— 名字里带那个课号的文件立刻归好。
        用户建 3 门课就看到 3 批自动归位，**在花一分钱之前**。
        ⚠️ 这是**纯函数 + 字符串匹配**，不抽正文、不联网，所以可以随便跑。
        """
        b = S.get("batch")
        if b is None or not b.get("need_course"):
            return
        import classify
        names = courses.list_courses(glossary, state_root=state_root)
        hit = {}
        for p in b.get("pending") or []:
            c = classify.by_code(pathlib.Path(p).name, names)
            if c:
                hit[p] = c
        b["matched"] = hit
        _later(refresh)

    def _start_classify():
        """「开始分类」——**这一刻才第一次花钱**。

        只把**还没按文件名归好**的那些送去模型：已经免费的就不再问一遍。
        """
        b = S.get("batch")
        if b is None or not b.get("need_course"):
            return
        names = courses.list_courses(glossary, state_root=state_root)
        if not names:
            set_status("还没有课 —— 先在上面建一个课号，我再开始分", 1.0)
            return
        matched = b.get("matched") or {}
        left = [p for p in (b.get("pending") or []) if p not in matched]
        # ⭐ 免费的先落成 verdict —— 它们**不经过模型**，理由也照实写。
        # ⚠️ 必须是 `classify.Verdict`（**不是 dict**）—— 下游全程用 `v.course`，
        #    给 dict 会 `AttributeError`，而它在工作线程里被 `_batch_ready` 吞掉。
        import classify as _cl
        b["verdicts"] = [_cl.Verdict(str(p), c, "文件名里有课号", "code")
                         for p, c in matched.items()]
        b.pop("need_course", None)
        b.pop("pending", None)
        _later(refresh)
        if not left:
            set_status(f"这 {len(b['verdicts'])} 份全都按文件名认出来了 —— "
                       f"核对后点确认（一次都没花钱）", 1.0)
            _batch_ready()
            return
        set_status(f"开始认剩下的 {len(left)} 份…")
        _classify(left)

    def _pending_card(files, matched, *, width):
        """零课程时那张卡：**为什么分不了 + 建课就会自动归好**。纯装配，不碰状态。

        ⚠️⚠️ **它必须塞得进滚动区，而滚动区的高度是建面板时算死的。**
           零课程时 `body_height(0)` = **88pt**（一张卡的最小值），而那时还没有文件、
           窗口高度就定下来了。第一版画了「标题 + 说明 + 最多 5 行文件名」= 156pt
           → **真机截图里卡片被滚到底部，那句解释根本看不见**（离线判据全绿，
           因为它只断言"函数被调用"，不断言"看得见"）。
        ⭐ 所以这里**只留三行、不列文件**：文件在不在由**计数**回答就够了，
           逐份的细节等「开始分类」之后那张映射卡再说。
           ⚠️ 三行 = 20(内边距) + 18 + 16 + 16 = **70pt < 88** ✓
        """
        from AppKit import NSColor, NSMakeRect, NSView
        h = CARD_PAD * 2 + L1_H + L2_H * 2
        view = NSView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, width, h))
        view.setWantsLayer_(True)
        view.layer().setCornerRadius_(CARD_RADIUS)
        view.layer().setBorderWidth_(HAIRLINE)
        view.layer().setBorderColor_(NSColor.whiteColor()
                                     .colorWithAlphaComponent_(CARD_LINE_A * 0.7).CGColor())
        view.layer().setBackgroundColor_(NSColor.whiteColor()
                                         .colorWithAlphaComponent_(CARD_FILL_A * 0.5).CGColor())
        y = h - CARD_PAD
        y -= L1_H
        view.addSubview_(panel.make_label(
            "还没有课 —— 课件不知道往哪儿分",
            NSMakeRect(CARD_PAD, y, width - 2 * CARD_PAD, L1_H), 13.0, bold=True))
        y -= L2_H
        # ⚠️ 这句是**流程承诺**，必须与 `_start_classify` 的行为逐字对应：
        #    建课真的会先按文件名免费分，而模型要等用户点「开始分类」才跑。
        # ⚠️ **不许出现 markdown 星号** —— `panel.make_label` 画的是纯文本，
        #    星号会原样显示（我在本文件里犯这个错第三次了，真机截图才看出来）。
        view.addSubview_(panel.make_label(
            f"这 {len(files)} 份先放这儿。建一门课，名字里带那个课号的会立刻归好",
            NSMakeRect(CARD_PAD, y, width - 2 * CARD_PAD, L2_H), 11.0, alpha=DIM,
            truncate=True))
        y -= L2_H
        view.addSubview_(panel.make_label(
            (f"已按文件名归好 {len(matched)} 份 —— 继续建课，或点「开始分类」"
             if matched else "还没有按文件名认出来的 —— 建完课点「开始分类」"),
            NSMakeRect(CARD_PAD, y, width - 2 * CARD_PAD, L2_H), 11.0,
            alpha=1.0 if matched else DIM, truncate=True))
        return view

    def _batch_ready():
        b = S.get("batch")
        if b is None:
            return
        if b.get("error"):
            set_status(f"扫描失败：{b['error']}", 1.0)
        else:
            hit = len([v for v in b["verdicts"] if v.course])
            tail = f"（另有 {b['dropped']} 份格式不收，没进表）" if b.get("dropped") else ""
            line = (f"认出 {hit} 份，{len(b['verdicts']) - hit} 份认不出来 —— "
                    f"核对后点确认{tail}")
            weak = b.get("weak") or {}
            if not b.get("has_key", True):
                # ⚠️ 没配 key 时每行的理由会是「模型没给出可解析的结果」——
                #    那是**我们自己的**机器话，用户读起来像"程序坏了"。
                #    在这里一次说清（而不是让 55 行都重复同一句废话）。
                line = "⚠️ 没配 key，只能按文件名分 —— " + line
            elif weak:
                # ⭐ 说**为什么**它认不准（没有记录 / 记录太短 / 不是英文转录）。
                #    只说"认它最不准"等于让用户去猜 —— 而三种原因的修法完全不同。
                items = [f"{c}（{why}）" for c, why in list(weak.items())[:2]]
                line += "　⚠️ 这几门认不准：" + "、".join(items)
            set_status(line, 1.0)
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
        # ⭐ **这里就是那本"免费标注集"的落点**（2026-09-28）——
        #    上面那个 `pairs` 本来就读到了每个下拉框的值，以前读完就扔。
        #    ⚠️ **只记录，不训练、不影响分类**：它先当**度量**用
        #    （「上了转录语料之后到底准了多少」），没有它就只能靠感觉。
        #    ⚠️ 记的是**用户的最终认定**，同时带上**模型当时说的**（`ai`）——
        #       有它才算得出"改对了几条"。
        try:
            _b = S.get("batch") or {}
            # ⚠️ 记的是 **`by_course` 里那些**（= 真的会被归档的），不是原始 `pairs`——
            #    「未分类」不在里面（`group_for_archive` 已经把它滤掉了），
            #    而"过滤规则"只有那一处定义，别再在这里重写一遍。
            _assigned = [(p, c) for c, ps in by_course.items() for p in ps]
            courses.record_batch(
                _assigned, root=state_root,
                ai={v.path: v.course for v in (_b.get("verdicts") or [])})
        except Exception as e:                                # noqa: BLE001
            # ⚠️ **出声**：它是唯一的度量来源，写不进去要说，别静默丢。
            print(f"⚠ 纠正日志没写成（{type(e).__name__}: {e}）—— "
                  f"这次归档照常，但少了一条可度量的数据", flush=True)
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
        # ⚠️⚠️ **先判 `busy` 再 `pop`。** `run_prep` 开头那句 `if S.get("busy"): return False`
        #    在**批量分类期间完全可达**（`S["busy"]` 是 prep 的串行器，
        #    而批量用的是 `S["batch"]["busy"]` —— 两回事）。
        #    原来写成 `run_prep(*S["queue"].pop(0))`：队首已经弹掉了、`run_prep` 却直接返回
        #    False → **整批里静默少掉一门**（映射表的行也移走了，状态行还写着「开始跑 N 门课」）。
        #    2026-09-28 OCR 审计发现。
        #    ⚠️ 早退时**不要**把队列丢掉 —— 正在跑的那门的 `_done` 会接着从
        #    `S["queue"]` 排空（它是在清完 `busy` **之后**才踢下一门的）。
        if S.get("busy"):
            set_status(f"另一门课还在跑 —— 这 {n_course} 门排在队列里等它，别关面板", 1.0)
            return
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
        a = S.get("add")
        q = S.get("sessions")
        for _v in strip_holder["hint"]:
            _v.setHidden_(b is not None or s is not None or a is not None
                          or q is not None or S.get("ics") is not None)
        _need = bool(b is not None and b.get("need_course"))
        for _btn, _t in strip_holder["actions"]:
            # ⚠️ 零课程那一档**不显示**取消/确认 —— 那一行被输入行占了，
            #    两套按钮在同一块地方会**叠在一起**（y 区间真的重合）。
            _btn.setHidden_(b is None or _need)
        _ac = strip_holder.get("add_cancel")
        if _ac is not None:
            # ⚠️ 标题与行为必须一起改（行为在 `close_add` 的同名分支里）。
            _ac.setTitle_("开始分类" if _need else "取消")
        for _btn, _t in strip_holder.get("search_btns", []):
            _btn.setHidden_(s is None)
        # ── 新增课程那一行：显隐 + **一次性**把焦点给它 ────────────────────
        # ⚠️ 焦点在这里给，不在 `open_add()` 里：那一刻这几个视图还藏着，
        #    藏着的视图当第一响应者是很怪的状态。`pop` 保证只给一次 ——
        #    否则每次 `refresh()`（跑完 prep、删词…）都会把焦点从搜索框抢过来。
        for _v in strip_holder.get("add_widgets", []):
            _v.setHidden_(a is None)
        if S.pop("_focus_add", False) and a is not None:
            try:
                win.makeFirstResponder_(add_field)
            except Exception:                                 # noqa: BLE001
                pass

        _i = S.get("ics")
        if _i is not None:
            # ── 导入确认模式：**确认之前一个字节都不落盘** ──────────────
            title_lbl.setStringValue_("导入课表")
            if _i.get("busy"):
                doc.setFrameSize_((WIDTH - 2 * PAD, max(body_h, 120.0)))
                return
            if _i.get("err"):
                set_status(f"读课表失败：{_i['err']}", 1.0)
            card, _imp_t = _make_import_card(
                _i.get("rows") or [], width=WIDTH - 2 * PAD,
                warn=_i.get("warn") or [], on_confirm=confirm_import,
                on_cancel=cancel_import, targets=[])
            strip_holder["import_rows"] = _imp_t
            card.setFrameOrigin_((0.0, 0.0))
            doc.addSubview_(card)
            doc.setFrameSize_((WIDTH - 2 * PAD,
                               max(body_h, card.frame().size.height)))
            return

        if q is not None:
            # ── 课次模式：一门课的上课记录 + 「没归课的那些」──────────────
            # ⚠️ 与搜索/批量**互斥**（各从自己的入口进，不共用一个状态位）。
            title_lbl.setStringValue_(f"{q.get('course', '')} · 上课记录")
            if q.get("busy"):
                doc.setFrameSize_((WIDTH - 2 * PAD, max(body_h, 120.0)))
                return
            if q.get("err"):
                set_status(f"读上课记录失败：{q['err']}", 1.0)
            _rows = q.get("rows") or []
            card, _sess_t = _make_sessions_card(
                _rows, width=WIDTH - 2 * PAD, title=f"这门课 {len(_rows)} 节",
                on_back=close_sessions, on_adopt=adopt_session,
                orphans=q.get("orphans") or [], targets=[])
            strip_holder["sess_rows"] = _sess_t
            card.setFrameOrigin_((0.0, 0.0))
            doc.addSubview_(card)
            doc.setFrameSize_((WIDTH - 2 * PAD,
                               max(body_h, card.frame().size.height)))
            return

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
            # ⚠️ 回来的 targets **必须留住** —— `setTarget_` 是弱引用，
            #    GC 掉就是「点了没反应，也不报错」（本轮就绪条踩过同一形状）。
            card, _rows_t = _make_search_card(
                s.get("hits") or [], width=WIDTH - 2 * PAD,
                open_idx=s.get("open"), open_text=s.get("open_text"),
                on_open=toggle_hit, targets=[])
            strip_holder["search_rows"] = _rows_t
            card.setFrameOrigin_((0.0, 0.0))
            doc.addSubview_(card)
            doc.setFrameSize_((WIDTH - 2 * PAD,
                               max(body_h, card.frame().size.height)))
            return

        if b is not None:
            # ── 批量核对模式：卡片列表整块换成映射表 ──────────────────
            title_lbl.setStringValue_(f"准备归档 {b.get('total', 0)} 份课件")
            if b.get("need_course"):
                # ⭐ **零课程：一次模型都没跑过。** 画说明卡，而不是一张
                #    N 行全是「未分类」的映射表（那样用户以为"分过了，都认不出"）。
                _pc = _pending_card(b.get("pending") or [], b.get("matched") or {},
                                    width=WIDTH - 2 * PAD)
                _pc.setFrameOrigin_((0.0, 0.0))
                doc.addSubview_(_pc)
                doc.setFrameSize_((WIDTH - 2 * PAD,
                                   max(body_h, _pc.frame().size.height)))
                return
            if b.get("busy") or not b.get("verdicts"):
                # ⚠️⚠️ **早退之前必须把映射表清空。** 映射表不在屏上时，
                #    `_confirm_batch` 那个按钮却是**可见可点**的，而它读的
                #    `batch_rows` 里可能还留着**上一批**的行 → 点下去会把上一批的
                #    路径重新分组入队再跑一次 prep，而 `prep._archive` 对
                #    「同名但大小不同」是加 `-2` 后缀（不跳过）→ 归档目录里出重复课件。
                #    （2026-09-28 OCR 审计发现；`_confirm_batch`/`_cancel_batch` 都不清它，
                #    只有 `do_close` 清。）
                batch_rows.clear()
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
        rows = load()
        # ⭐ 课表预选：把猜的那门**排到第一**，并把理由挂到状态行。
        #    ⚠️ **只改顺序** —— 不预选、不自动应用（`[一手]` Apple HIG 是「把最可能的
        #       放第一」，但它那句「select the first option by default」在这里被否掉了：
        #       认错课 = 整节课术语表全错，而作者拍的是「预选 + 显式纠错入口」）。
        _gs = S.get("guesses") or []
        _gi = S.get("guess_i", 0)
        _hint = _gs[_gi].course if 0 <= _gi < len(_gs) else None
        if _hint:
            rows.sort(key=lambda r: (r.course != _hint,))
            set_status(f"课表推测：{_hint} —— {_gs[_gi].why}", 0)   # 0 = 不自动消失
        elif _gs:
            set_status("你自己挑一门 —— 我不猜了", 0)
        if not rows:
            # ⭐ 零课程的空状态（2026-09-28）—— 原来这里是**一片空白**。
            from AppKit import NSColor, NSMakeRect, NSView
            card = NSView.alloc().initWithFrame_(
                NSMakeRect(0.0, 0.0, WIDTH - 2 * PAD, CARD_H))
            card.setWantsLayer_(True)
            card.layer().setCornerRadius_(CARD_RADIUS)
            # ⚠️ 虚线感的弱描边：它是**提示**不是内容，别长得和课程卡一样实
            card.layer().setBorderWidth_(HAIRLINE)
            card.layer().setBorderColor_(NSColor.whiteColor()
                                         .colorWithAlphaComponent_(CARD_LINE_A * 0.7).CGColor())
            card.layer().setBackgroundColor_(NSColor.whiteColor()
                                             .colorWithAlphaComponent_(CARD_FILL_A * 0.5).CGColor())
            _ly = CARD_H - CARD_PAD
            for _i, _txt in enumerate(empty_state_lines()):
                _ly -= (L1_H if _i == 0 else L2_H)
                card.addSubview_(panel.make_label(
                    _txt,
                    NSMakeRect(CARD_PAD, _ly, WIDTH - 2 * PAD - 2 * CARD_PAD, L1_H),
                    13.0 if _i == 0 else 12.0, bold=(_i == 0),
                    alpha=1.0 if _i == 0 else DIM))
            doc.addSubview_(card)
            doc.setFrameSize_((WIDTH - 2 * PAD, max(body_h, CARD_H)))
            return
        for r in rows:
            entry = S["result"].get(r.course)
            card = _make_card(r, on_start=on_start,
                              on_drop_files=run_prep, width=WIDTH - 2 * PAD,
                              entry=entry, on_sessions=open_sessions,
                              hinted=(r.course == _hint),
                              on_not_this=next_guess,
                              # ⚠️ **课号要在这里绑好。** `_make_card` 只传词
                              #    （它不知道也不该知道课号之外的上下文）——
                              #    第一版直接传 `do_delete`（两个参数），调用点只给一个，
                              #    于是 TypeError 被 AppKit 吞掉、点「删」静默无效。
                              on_delete=lambda t, c=r.course: do_delete(c, t),
                              on_undo=lambda c=r.course: do_undo(c),
                              on_delete_course=delete_course)
            card.setFrameOrigin_((0.0, y))
            doc.addSubview_(card)
            y += card_height(entry, has_actions=(on_start is not None)) + CARD_GAP
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
        # ⭐ **拖进来的是课表（`.ics`）→ 走去导入那条路**，不当课件处理。
        #    ⚠️ 放在 `S["busy"]` 检查**之前**：导入自己也有一条独立的忙状态，
        #       而"另一门课在跑"跟"能不能导入课表"是两件事。
        #    ⚠️ `.ics` 拖到**哪张卡上**都走同一条路 —— 导入本来就要你确认，
        #       卡片上那个课号在这条路上没有意义。
        _ics, _rest = drop_split(files)
        if _ics:
            run_import(_ics)
            # ⚠️⚠️ **混拖时课件那半边会掉**（2026-09-29）：`.ics` 先摘走就 `return`，
            #     剩下的 pdf/docx 从来没人处理。**本轮不修**（记为下一件，见
            #     `docs/PLAN-entry-panel.md §8.1` 的 `MIX` 那行），
            #     但**必须说出来** —— 原来是不说话地丢掉，用户看到"拖了 4 个文件、
            #     只有一个有反应"却不知道为什么。
            #     为什么不当场两条都跑：`run_import` 是**异步**的（起线程解析后就返回），
            #     紧接 `run_prep` 会让"确认卡还没点、课件已经在归档"两件事同时压在屏上，
            #     而且随后那张批量映射卡会把确认卡挤掉。串行要碰确认卡的生命周期 ——
            #     那是刚做完、还没被实机用过的东西，不值得为这个罕见场景动它。
            #     ⚠️ 真实使用里两者**时间上不重合**：课表开学导入一次，课件每周拖。
            if _rest:
                set_status(f"课表先导入了 —— 剩下这 {len(_rest)} 份课件请再拖一次",
                           3.0)
            return True
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
        #    → 直接把**源路径**交给 `prepare`（它自己 `_archive`，见 `prep._archive`）。
        # ⚠️⚠️ **`S["busy"] = True` 之后到工作线程起来之前，一句都不许抛。**
        #    这几句是真 I/O（`paths.course_dir` 里有 `iterdir()/is_dir()`，可抛
        #    OSError / 权限错），而它们在任何 try 之外、也还没进工作线程 ——
        #    异常会沿调用方（拖拽那条被 drop 守卫吞、`_confirm_batch` 里被 AppKit 吞）逃出去，
        #    于是 `busy` **永久为真**：之后每次拖入都被开头那句挡掉（说"另一门课还在跑"），
        #    而其实什么都没在跑，面板直到重启都救不回来（注释里记的第一版事故就是这个形状）。
        #    2026-09-28 OCR 审计发现。
        try:
            mats = paths.materials_dir(course, root=state_root)
            state_file = paths.prep_state(course, root=state_root)
            keep = [str(p) for p in files]
        except Exception as e:                            # noqa: BLE001
            S["busy"] = False
            set_status(f"{course}：路径算不出来（{type(e).__name__}: {e}）"
                       f" —— 这次没跑，再拖一次", 1.0)
            return False

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

    # ⚠️ `choose_files(course)` —— 单课的文件选择器 —— 2026-09-28 删掉了：
    #    它只服务卡片上那个已删的「选择文件…」。**「不用拖」这条路没消失** ——
    #    底部统一入口有它自己的「选择文件…」（`_pick_batch_files`，还多选 + 收文件夹），
    #    选完在批量卡上指定课号即可。

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
                _sever(_card)                # ⚠️ 逐条各自 try —— 见 `_sever` 的说明
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
            # ⚠️ 新增课程那一行的四个控件也要断 —— 它们的 target 里攥着
            #    `submit_add`/`close_add` 的闭包，而闭包持有 `add_field`/`strip`/`win`。
            #    与上面三条是**同一条环**（漏一条就漏一整个面板，且随开关轮数增长）。
            strip_holder["add_widgets"] = []
            # ⚠️ **三个 `*_rows` 也要断**（2026-09-29 补）—— 它们是后加的
            #    三张卡的 target 容器（搜索命中 / 课次 / 导入确认），只被写、没被读，
            #    作用就是留住弱引用的 target。
            #    漏掉的话：`holder → target → act 闭包 → refresh → doc/win` 是一条环，
            #    `gc` 收不回 —— 与上面 `["actions"]` 那条**同一形状**
            #    （那条注释里记着实测：开/关 3 轮 144 个 `_ClassLive*`、9 轮 180，
            #     **随轮数增长**）。⚠️ 这几张卡是裸 `NSView`，`_sever` 对它们只会
            #    `AttributeError` 后跳过 —— 所以只能靠这里断。
            strip_holder["search_rows"] = []
            strip_holder["sess_rows"] = []
            strip_holder["import_rows"] = []
            # ⚠️ 新增模式是**模块级**的，跨面板存活 —— 不清的话，关掉面板再打开
            #    会直接落进「输入行开着」的状态（同 `S["batch"]`/`S["search"]` 那条）。
            S["add"] = None
            S.pop("_focus_add", None)
            # ⚠️ 批量状态是**模块级**的，跨面板存活 —— 不清的话，关掉面板再打开
            #    会直接落进"上次那批还没确认"的模式里。
            #    （`S["result"]` 是**故意**跨面板的，别把这条规矩套到它头上。）
            S["batch"] = None
            S["search"] = None
            S["sessions"] = None
            # ⚠️ 导入那条也是**模块级**的，同上面三条 —— 2026-09-29 补。
            #    原来漏了它：`.ics` 拖进来、看到确认卡之后**直接关面板** →
            #    `S["ics"]` 残留 → 下次开面板 `refresh` 里 `_i = S.get("ics")` 非空
            #    → **又弹出一张陈旧的导入确认卡**（行数据还是上一轮的）。
            S["ics"] = None
            # ⚠️ 面板里那两个**弱引用 target 的锚点**（就绪条 / 搜索框）也要断 ——
            #    它们不是状态，是"别让 target 被 GC 掉"的容器。留着就等于每次
            #    开关面板多留两个对象（与上面那几条同一条纪律）。
            S["ready_targets"] = None
            S["search_target"] = None
            S.pop("queue", None)
            # ⚠️ 映射表那批选择器也要断 —— 它们自己抓着菜单，而菜单抓着 target。
            #    与卡片 `_targets` 是**同一类**孤岛（那次实测：只 setContentView_(None)
            #    时 3 轮后仍有 16 个 DropTarget）。
            batch_rows.clear()
            win.setContentView_(None)        # 丢掉整棵视图树
        except Exception as e:                            # noqa: BLE001
            # ⚠️ **出声。** 这一段一旦中断，后果是「泄漏一个面板 + 状态不清」，
            #    而它以前是**静默**的 —— 于是坏了几个月没人知道（见 `_sever` 的说明）。
            print(f"⚠ 关面板时清理没做完（{type(e).__name__}: {e}）"
                  f" —— 这次会留下一个面板对象，且模式状态没清", flush=True)
        # ⚠️⚠️ **必须 `close()`，不是 `orderOut_`**（后者只是让它不可见，对象照样活）。
        # ⚠️ 但 2026-09-28 补测：**`close()` 之后它照样留在 `NSApp.windows()` 里** ——
        #    连开/关 3 轮 → 3 个 `_ClassLivePanel`，`objc.autorelease_pool()` 也挡不住。
        #    根因是 `isReleasedWhenClosed()` 返回 **False**，而那是 **NSPanel 的默认值**
        #    （官方文档逐字：`NSWindow` 默认 true、**`NSPanel` 默认 false**）。
        # ⚠️⚠️ **别把它改成 True 来"修"这个 —— 那是坑。** PyObjC（非 ARC）下改成 True 会
        #    **过度释放**：pywebview #1799 在 macOS ARM64 上用 lldb 实证同一地址 dealloc
        #    两次，他们的修法**正是** `setReleasedWhenClosed_(False)`；GitHub 上 20+ 个
        #    Python 项目一致写 False。**False 才是这边公认的做法。**
        #
        # ⚠️ **这是一个已知的、没修的小账**（如实记，别读成"已经没问题"）：
        #    · 实测：每开/关一轮，`NSApp.windows()` 里**多留一个窗口对象**；
        #      逐个查身份 → 旧的那些**在 ObjC 侧活着、Python 侧已经没有对象**
        #      （N 个窗口里只有 1 个的 `id()` 出现在 `gc.get_objects()` 里）。
        #    · 但**它的视图树已经掏空了**（`setContentView_(None)` + `_sever` + 上面那圈
        #      `strip_holder[...] = []`）—— 会真正累积的那一大坨是这个，而它被断掉了
        #      （实测过：漏一条就随轮数增长）。剩下的是个**空壳 NSPanel**。
        #    · 频率：面板每次上课开一次，不是热路径。
        # ⭐ 社区对"反复开关的面板"给的答案**不是"释放"，是"复用"**：单例 + `orderOut_`
        #    + `releasedWhenClosed=False`（SO 13924105 就是这个场景）。我们其实**有**单例
        #    那条路（`open_panel` 会复用 `S["panel"]`），但 `do_close` 把窗口拆了，
        #    所以每次重开都是新建的。
        #    → **要真修就是改成"隐藏不销毁、复用时重建内容视图"**，那是独立一件事
        #      （本面板的清理逻辑是围着"销毁"写的），别顺手改。
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

    from AppKit import NSButton

    status_holder["label"] = panel.make_label(
        "拖课件到某张卡上 = 加到那门课　·　拖到下面那个虚线框 = 自动分到各课",
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

    # ── 删除课程（2026-09-28）────────────────────────────────────────
    def delete_course(course: str, keep_materials: bool = False,
                      *, ask: bool = True) -> None:
        """删一门课。**右键菜单那条路与验收跑器那条路都走这里。**

        `keep_materials=True` = 「只删课号」（术语表进废纸篓、课件搬进保留区）。
        `ask=False` 跳过确认框 —— ⚠️ **只给验收跑器用**（跑器里没有真键盘，
        而模态框会把那个进程卡住）。菜单那条路**永远** ask=True。

        ⚠️⚠️ **弹窗只在 `.app` 里弹得出来**（`notice._can_alert()` = `CLASSLIVE_FROM_APP`，
           由 app 的 sitecustomize 设）。终端里 `cl` 也能开这个面板，那时 `alert()`
           **只打印**并返回 `fallback` —— 而这里 fallback 是「取消」，所以**不会误删**，
           但用户会对着面板等一个永远不出现的框。
           → 所以这里**先问 `_can_alert()`**，弹不出来就直说，别装作问过了。
           （这正是 `notice.alert` 那条「确认框必须显式给 fallback、且 fallback 要安全」的延伸。）
        ⚠️ **确认框的按钮顺序**：`notice.alert` 把**第一个**按钮设成默认键（实测 `\r`），
           而 HIG › Alerts 逐字要求「you don't want to make a Cancel button the default
           button」→ 默认键只能落在动作上。两个动作里**默认给破坏性小的那个**
           （「只删课号」），免得顺手一个回车就把课件也扔了。
        """
        # ⚠️⚠️ **早拒：prep 正在跑的时候不许删课。** 与 `do_delete`（删术语）同一条纪律，
        #    但这边更险 —— `do_delete` 还有 prep 写锁兜底，而 **`courses.delete`
        #    全程不取本课的写锁**。不早拒的后果是**半删**：术语表进了废纸篓、
        #    课件搬进了保留区，而 prep 收尾时照旧往原路径写回 / 往已搬走的目录写 notes
        #    → 删掉的课**半路又冒出来，内容还是残缺的**。
        #    （2026-09-28 OCR 审计发现；`S["busy"]` 是 prep 的串行器，跑一门十几分钟。）
        if S.get("busy"):
            set_status("正在跑准备 —— 等它完再删课", 1.0)
            return
        if ask:
            import notice
            if not notice._can_alert():
                set_status("删课要在 ClassLive.app 里确认 —— 终端里弹不出框，这次没删",
                           1.0)
                return
            f = courses.facts(glossary, course, sessions_dir=sessions_dir,
                              state_root=state_root)
            msg = (f"术语表 {_num(f['glossary_bytes'], '字节')}"
                   f" · 课件 {_num(f['materials'], '份')}")
            if f["sessions"]:
                # ⚠️ 这句是**必须说的**：删除**不碰**上课记录，而删了课再建同名，
                #    那些记录会自己接回来。不说 = 用户以为全没了（文案撒谎那类）。
                msg += f"\n{f['sessions']} 节上课记录不受影响（它们在 sessions/）"
            if f["materials"]:
                what = notice.alert(f"删除「{course}」？", msg,
                                    buttons=("只删课号", "全部删除", "取消"),
                                    fallback="取消")
            else:
                # ⚠️ 没有课件时两档**完全一样** —— 别给一个不存在的选择，
                #    那是在骗人点（同 `empty_state_lines` 那条）。
                what = notice.alert(f"删除「{course}」？", msg,
                                    buttons=("删除", "取消"), fallback="取消")
            if what == "取消":
                set_status(f"{course}：没删，什么都没动", 1.0)
                return
            keep_materials = (what == "只删课号")
        res = courses.delete(glossary, course, sessions_dir=sessions_dir,
                             state_root=state_root, keep_materials=keep_materials,
                             trash_fn=trash_fn)
        set_status(delete_msg(course, res), 1.0)
        _later(refresh)

    refresh()

    # 位置：屏幕中上（AppKit 左下原点）
    scr = NSScreen.mainScreen().frame()
    win.setFrameOrigin_(((scr.size.width - WIDTH) / 2.0,
                         max(60.0, scr.size.height - h - 140.0)))
    win.orderFrontRegardless()
    # ⭐ 面板**已经上屏**了 → 现在把搜索框放回"可以被点进焦点"的状态。
    #    过早放行 = 白做（AppKit 会在上屏那一刻把它设成第一响应者）；
    #    这一小段延时是必须的，和窗口建立初始第一响应者是同一个时刻的事。
    #    ⚠️ 放行**不是** promise 它会拿焦点 —— 只是允许用户点进去。
    from PyObjCTools import AppHelper as _AH
    _AH.callLater(0.2, lambda: search_field.setRefusesFirstResponder_(False))
    # ⚠️⚠️ **一律用关键字构造** —— 这里原先是按位置传的，2026-09-29 加
    #    `show_sessions` 时把参数插错了格：`open_sessions` 落进 `delete_course`、
    #    那个删课 lambda 落进 `show_sessions` → **调 `h.show_sessions('ECON10740')`
    #    实际执行的是删课**，而且一个错都不报（`NamedTuple` 位置构造对类型不做检查）。
    #    关键字构造让顺序不再有意义，整类 bug 消失。
    return Handles(
        window=win,
        close=do_close,
        refresh=refresh,
        set_status=set_status,
        start_batch=run_batch,
        search=run_search,
        add_course=add_course,
        show_sessions=open_sessions,
        import_timetable=run_import,
        delete_course=lambda c, keep=False: delete_course(c, keep, ask=False),
        start_classify=_start_classify,
    )


def _panel_key(kw) -> tuple:
    """面板复用判据：**界面结构**（有没有 `on_start` / `on_test_mode`）+ **读写目标**（那三个路径）。

    ⚠️ 为什么路径也要进键（2026-09-28 OCR 审计指出）：这三个参数**决定读哪儿写哪儿**。
       进了键之后，同一个进程里拿另一组根再开面板（隔离跑器与测试正是这么干的）
       会**重建**而不是复用 —— 复用的后果是：后面每一次增删改都写向**旧**路径，
       而本仓库有一条硬规矩「**测试必须隔离写端**」。不隔离就是写坏真数据。

    ⚠️ `str()` 归一化：调用方可能传 `Path` 也可能传 `str`，同一个位置不该因为
       类型不同就白重建一次。

    ⚠️ **别拿整个 `kw` 比** —— 里面还有 `on_close` / `prepare_fn` 这类 lambda，
       每次传一个新的就永远不等，于是每次打开都白重建（实测踩到）。

    ⚠️ `on_test_mode` 是**第二个**会改界面结构的项（2026-09-30 加）：有它 = 落点条上
       多一颗开关，没有 = 一颗都不画。少写这一项的话，「上课中开过面板」再
       「双击 .app」会复用那个**没有开关**的旧面板 —— 而这一次是要开课的，
       用户会找不到开关，且**照样不报错**（同 `on_start` 当年那条一模一样的形状）。
    """
    return (kw.get("on_start") is None,
            kw.get("on_test_mode") is None,
            str(kw.get("glossary") or ""),
            str(kw.get("state_root") or ""),
            str(kw.get("sessions_dir") or ""))


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
        if S.get("panel_key") == _panel_key(kw):
            cur.window.orderFrontRegardless()
            return cur
        cur.close()                       # 结构不同 -> 关掉重建（`S["result"]` 在模块级，不丢）
    h = build(**kw)
    S["panel"] = h
    S["panel_key"] = _panel_key(kw)
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
