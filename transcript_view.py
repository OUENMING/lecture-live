"""NSScrollView 支撑的转录区: 聊天式滚动 + 视图回收 + 跟随状态机。

坐标约定(关键)
--------------
文档视图**不翻转**(`isFlipped` 保持 False), 所以 `origin.y = 0` 就是**底部**。

为什么不用翻转坐标: 翻转后 y=0 在顶部, 要"贴底"就得把 origin 设成
`docH - clipH`; 内容比视口短时该值是**负数**, AppKit 会 clamp 到 0 并把内容
顶对齐 —— 空白跑到内容**下方**, 正是聊天布局最怕的。不翻转时静止位置恰好是 0,
clamp 边界与静止位置重合, clamp 永远在帮你; 内容短于视口时也无需 padding 技巧。

行几何: `level 0` = 最新句, 在 y=0; `level L` 在 `y = L * row_h`。
一行内部两条标签的上下顺序见 `EN_LINE_ON_TOP`。

为什么固定行高是命门
--------------------
固定 `row_h` 让"滚动偏移 → 行索引"变成 O(1) 的除法。可变行高会逼出前缀和
布局缓存, 并推翻整套回收方案。所以**所有行等高**、不做逐行自适应。
⚠️ "等高"指的是**同一时刻所有行共享同一个高度**, 不是"高度恒为 70":
`set_row_metrics` 会在窗口变窄时把这一组统一尺寸换成 3/4/5 行(中文需要更多行才不
被静默截断), 而 O(1) 除法这条命门仍然成立 —— 因为它只依赖"等高", 不依赖具体值。

回收
----
池里 K 个槽位常驻, 每个槽位记住自己当前的 `level`。滚动一行时只有**一个**
槽位的 level 会离开覆盖区间、也只有**一个**新 level 进入, 于是只搬动一格。
文案更新则靠 `_set_text` 的缓存按键跳过 —— 只有实时行在流式期间会真的写。
"""

from __future__ import annotations
import math
import time

import motion
import objc_own
import edge_fade

IDLE_S = 6.0          # 闲置多久才自动回底(仅在离开底部后又有新句定稿时)
BOTTOM_EPS = 3.0      # origin.y <= 此值即视为在底部(橡皮筋的负值也算)
MOVE_EPS = 1.5        # 与期望 origin 相差超过此值 -> 判定为用户/惯性滚动
FLUSH_DT = 0.016      # 渲染合并闸门 = 一帧

# ---- 新行淡入（A5）与译文超限截断（#7）----
#: 上滚时**真正的新行**额外淡入的时长 = `motion.MOVE_S` 的前 55%。
#: 来源: 字体/动效报告 §5 方案 A5（Google 2023 的设计动作逐字: "smooth scrolling and
#: fading of newly added tokens"）。位移仍走完整 `MOVE_S`, 淡入**先到 1** ——
#: 「位移还没落位, 新行已经就位」。⚠️ 复用既有的 `_roll_alpha` 机制（`_geom` 里按
#: `level < _roll_fade_n` 施加）, 不是第二套动画。
NEW_LINE_FADE_S = motion.MOVE_S * 0.55

#: **半句合并**（跨段尾句合并少一行）那一帧: 被吞掉那一行原地淡出 / 尾句新文字交叉
#: 淡入用的时长。取 `motion.STATE_S` —— 「原地换内容」那一档（`motion.py` 的分档说明:
#: 150–300ms 的 STATE 档），**不是** `NEW_LINE_FADE_S`: 新行是"入场"（先就位），
#: 而这里是**一次交叉淡换**（一出一进要对齐成一个 dissolve，两边必须同一个时长）。
MERGE_FADE_S = motion.STATE_S

#: 译文超出档位行数时补的省略号（单字符, 与 `linebreak` / `_wrap_zh` 无关）。
ELLIPSIS = "…"


def roll_fade_alpha(t0: float, now: float, duration: float = NEW_LINE_FADE_S) -> float:
    """新行淡入的 alpha(0→1), 时长 `duration`(默认 `NEW_LINE_FADE_S`)、ease-out。
    **纯函数**(A5 的判据)。合并过渡传 `MERGE_FADE_S` —— 两者走同一条曲线与同一个时钟。"""
    return motion.ease_out(motion.progress(t0, now, duration))


def truncate_to_height(text: str, measure, max_h: float, *,
                       suffix: str = ELLIPSIS, eps: float = 0.5) -> str:
    """把 `text` 截到 `measure(结果) <= max_h + eps`, **从尾部**截断并补 `suffix`。

    ⭐ 第 7 项（报告 §5 的 F · 译文溢出）: AppKit 的 `maximumNumberOfLines_` 超限时
    **既不留省略号也不报错**（`overlay.py` 文件头实测过这个坑）, 所以"要换行又要省略号"
    只能自己截。`measure(s) -> px` 由调用方注入（生产环境 = AppKit 实测的 `_measure_text_h`）,
    于是本函数是**纯函数**, 测试里可以用假尺子钉住它。

    三条不变量:
    1. 放得下就**原样返回**（含 `""`）—— 短串一个字节都不动。
    2. 结果**恒以 `suffix` 结尾**（除非连 `suffix` 自己都放不下, 那时只返回 `suffix`）。
    3. **不改输入**: 返回新字符串, `text` 对象原样（显示层专用; `sessions/` / `livemetrics` /
       `writer` 拿到的永远是原文）。
    """
    if measure(text) <= max_h + eps:
        return text
    if measure(suffix) > max_h + eps:
        return suffix
    # 高度对**前缀长度**单调不减 -> 二分找"最长的、加省略号后仍放得下"的前缀。
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if measure(text[:mid] + suffix) <= max_h + eps:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo].rstrip() + suffix


def truncate_head_to_height(text: str, measure, max_h: float, *,
                            prefix: str = ELLIPSIS, eps: float = 0.5) -> str:
    """`truncate_to_height` 的镜像: **从开头**截、前面补 `prefix`, 留住**最新**的那几个词。

    给 D 布局的**当前行英文**用: 草稿英文随老师说话一直往后长, 读者要看的是**刚说的**,
    所以截掉的是开头(实时字幕的通行做法, 原型里叫「尾部显示」)。三条不变量与
    `truncate_to_height` 逐条对应(放得下原样 / 结果恒以 `prefix` 开头 / 不改输入)。
    尽量从词边界切: 切点往后最多挪 14 个字符找空格。"""
    if measure(text) <= max_h + eps:
        return text
    if measure(prefix) > max_h + eps:
        return prefix
    # 高度对**后缀长度**单调不减 -> 二分找"最长的、加前缀后仍放得下"的后缀。
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if measure(prefix + text[len(text) - mid:]) <= max_h + eps:
            lo = mid
        else:
            hi = mid - 1
    cut = len(text) - lo
    sp = text.find(" ", cut)
    if 0 <= sp - cut < 14 and sp + 1 < len(text):
        cut = sp + 1
    return prefix + text[cut:].lstrip()


# ---- D 布局（原地卡片流）----
# 2026-10-17 作者在原型 v7 定稿（artifact 5atKbvKuKib8uBLTGwhR8x）。三件事:
#   ① 草稿并进最底那一行（由 `caption_flow.caption_rows` 决定喂什么, 本模块只管画）;
#   ② 新行**原地显影**（淡入 + 模糊→清晰, 零位移）, 老行照旧上推 —— 参考 Claude 桌面端
#      `_grapheme-reveal`（opacity 0→1 + blur→0, 不位移）。新行晚 `D_ENTER_DELAY_S` 起跑,
#      等被顶上去的那一行先让开（原型实测不等会叠 20–57px）。
#   （原型还有一块垫在最底那一行后面的固定底板; 10-17 真窗口 A/B 后作者定: 不要。）
#: 按**视觉层号**（层号减去还没走完的位移）给整行降亮度: 当前 1.0 → 上一句 0.72 → 更早 0.56。
#: 数值取自原型的颜色表（中文 .98 / .68 / .55 的相对比例）。只在跟随态生效 —— 往回翻时全亮。
D_DIM = (1.0, 0.72, 0.56)
#: 当前行是**草稿**时中文再暗一档（原型 plainZh .72 / 定稿 .98）; 升级为定稿时按 STATE_S 提亮。
D_DRAFT_ZH_DIM = 0.74
D_ENTER_DELAY_S = 0.08
D_ENTER_S = motion.MOVE_S - D_ENTER_DELAY_S          # 与位移同时走完
D_BLUR_PX = 4.0
#: 实时行**新出现的字**从这个透明度淡到 1（原型 v7 的 `dIns`: .2 → 1, 260ms）——
#: 流式中文每次只来两三个字, 直接冒出来就是「跳字」; 淡进来读着是连续的。
D_TYPE_FROM = 0.2
D_TYPE_S = 0.26
#: **改字**（不是往后长）的新字从这个亮度起淡，不从 `D_TYPE_FROM`。
#: ⚠️ Owen 10-17：草稿译文换成定稿译文时「整句突然消失一下再出现」—— 措辞一换，大半个字
#:    同时掉到 20% 再长回来，看上去就是整句闪没了。往后长的新字照旧从 20% 起（那是「新来的」
#:    信号）；改掉的字只做一次轻淡换（0.65 → 1），屏上的墨量不再塌下去。
D_REVISE_FROM = 0.65


def _revise_shift() -> float:
    """让 `D_TYPE_FROM` 起的那条曲线**从 `D_REVISE_FROM` 起跑**要把出生时刻往前挪多少秒。"""
    target = (D_REVISE_FROM - D_TYPE_FROM) / (1.0 - D_TYPE_FROM)
    lo, hi = 0.0, 1.0
    for _ in range(40):
        mid = (lo + hi) / 2
        if motion.ease_out(mid) < target:
            lo = mid
        else:
            hi = mid
    return hi * D_TYPE_S


_REVISE_SHIFT = _revise_shift()


def carry_births(prev: str, births: list, display: str, now: float, *,
                 min_run: int = 2, soft: float = 0.0) -> list:
    """上一版每个字的出现时刻 → 新版每个字的出现时刻（新字记 `now`, 旧字原样带过去）。

    按**字**对齐、忽略换行: 中文折行点会随句子变长挪动（`_shape_zh`）, 英文开头截断
    每来一个词就挪一次 —— 字没变、只是位置变了, 不能当新字再淡一遍（Owen 10-17:
    「没改词, 后半句还是刷新一下」）。对齐用 `SequenceMatcher`, 短于 `min_run` 的
    巧合匹配（整句换掉时偶然相同的「的」）不算, 免得一句新话里零星几个字不淡。
    """
    import difflib
    pc = [(c, b) for c, b in zip(prev, births) if c != "\n"]
    keep = [i for i, c in enumerate(display) if c != "\n"]
    dc = "".join(display[i] for i in keep)
    out = [0.0] * len(display)
    for i in keep:
        out[i] = now
    sm = difflib.SequenceMatcher(None, "".join(c for c, _ in pc), dc, autojunk=False)
    last_a = last_b = 0                       # 最后一段算数的匹配之后，新旧各从哪开始
    matched = False
    for a, b, n in sm.get_matching_blocks():
        if n >= min_run or (n and a == 0 and b == 0):
            matched = True
            last_a, last_b = a + n, b + n
            for k in range(n):
                out[keep[b + k]] = pc[a + k][1]
    if soft and matched:
        # 改字 vs 往后长：新字在最后一段匹配**之前**（夹在中间被换掉的）→ 改字；
        # 在之后、且旧版那里本来还有字（尾巴被换掉）→ 改字；旧版尾巴完整、只是后面多出来 → 新长的。
        tail_replaced = last_a < len(pc)
        for bi, i in enumerate(keep):
            if out[i] == now and (bi < last_b or tail_replaced):
                out[i] = now - soft
    return out


def d_dim(x: float) -> float:
    """视觉层号 → 亮度倍数（分段线性, 位移途中连续变化 —— 不跳亮度）。纯函数。"""
    if x <= 0.0:
        return D_DIM[0]
    if x < 1.0:
        return D_DIM[0] + (D_DIM[1] - D_DIM[0]) * x
    if x < 2.0:
        return D_DIM[1] + (D_DIM[2] - D_DIM[1]) * (x - 1.0)
    return D_DIM[2]


# ---- 双语行的竖排顺序 ----
# True = 英文小字在上、中文大字在下(当前)   False = 反过来(2026-09-24 之前)
#
# 为什么换: 三条独立同行评审研究(SSLA / Bilingualism: L&C / JoSTrans)都发现双语
# 字幕里 L2 行被系统性略读, 而**唯一被证实能改变注意分配的是"哪一行在上面"**,
# 不是字重/字号/颜色(同批研究还报告注视时长与理解成绩无显著相关)。作者考试是
# 英文的, 所以把 L2 行放上去。想回退改这一个常量。
# 只作用于**双语行**; 答案行与"翻译关闭时英文顶上"的行仍是大字在上(见 _layout)。
EN_LINE_ON_TOP = True



def _view_classes():
    """两个 ObjC 视图子类。

    ⚠️ 类名由 `objc_own` 生成（key `TranscriptScroll` / `TranscriptDoc`）——
       **调用方从不提名，所以撞不了名**。以前这里是「模块级全局 + 手挑名字 +
       自己写闩锁」，那种写法在 2026-09-26 真出过一次静默事故（见 objc_own 文件头）。
    """
    import objc

    def scroll_wheel(self, event):              # noqa: N802
        # 这个开关**不再区分收起/展开**（2026-09-24 解耦，见 set_collapsed）：
        # 内容永远溢出，所以恒为 True —— 保留它只是为了留一个统一的闸门，
        # 以及保住下面这条注释里的坑。
        # 不用 setIgnoresMouseEvents_ —— 那会连带杀掉内容区的拖拽移动，
        # 且和悬浮窗「鼠标穿透」同类的单向死锁。
        # （NSScrollView 没有 setScrollEnabled_，所以开关放在自己的标志位上。
        #  PyObjC 的 ObjC 子类必须用 objc.super，内置 super() 不认。）
        # ⚠️ 用 `cls_of(key)` 取回本类，**别写 `type(self)`** —— 子类化时行为会变。
        if getattr(self, "_scroll_enabled", True):
            objc.super(objc_own.cls_of("TranscriptScroll"), self).scrollWheel_(event)

    def doc_mouse_down(self, event):            # noqa: N802
        # 整个转录区都是拖拽面。不赌 AppKit「背景拖拽」的启发式认不认
        # NSScrollView；滚轮和按键拖拽是两条独立事件流，互不干扰。
        win = self.window()
        if win is not None:
            win.performWindowDragWithEvent_(event)

    from AppKit import NSScrollView, NSView
    return (
        objc_own.own("TranscriptScroll", NSScrollView, {"scrollWheel_": scroll_wheel}),
        objc_own.own("TranscriptDoc", NSView, {"mouseDown_": doc_mouse_down}),
    )


class LineLog:
    """已定稿字幕行的**身份账本**：行内容 `rows` + 每行一个自增**身份号** `ids`。

    为什么要身份号（跨段半句合并，见 `fragment_merge`）：流程是「尾句先上屏 →
    下一段首句上屏 → 重翻回来后替换尾句那一行、并吞掉首句那一行」。从触发到落地
    之间屏上可能又插进了别的行（同段的后几句 / 答案接管换渲染源），**行号会漂**；
    身份号不会 —— `replace_pair` 按 id 定位，插多少行都对。

    ⚠️ 纯逻辑、**不碰 AppKit**：判据可以直接构造它（`tests/test_merge_replace.py`）。
    ⚠️ `rows` 与 `ids` **永远等长且同序**，且 `replace_pair` **原地改**（保持 list 身份
    —— `Overlay._history` 就是 `rows` 本身，外部引用不许失效）。
    """

    def __init__(self) -> None:
        self.rows: list[tuple[str, str]] = []
        self.ids: list[int] = []
        self._seq = 0
        #: 上一次 `replace_pair` **删掉**的那一行在**调用前**的索引（失败 = `None`）。
        #: ⭐ 给半句合并的过渡用：被吞掉那一行的**层号**只能由这个索引 + 当时的行数算
        #: 出来（`Overlay._render` 拿不到索引，它只看得见 `rows` 的内容）。
        self.last_deleted: int | None = None

    def append(self, en: str, zh: str) -> int:
        """追加一行 → 返回它的身份号。"""
        self._seq += 1
        self.rows.append((en, zh))
        self.ids.append(self._seq)
        return self._seq

    def amend_last(self, en: str, zh: str) -> int | None:
        """把最后一行换成 `(en, zh)`，身份号不变（短尾巴并进上一行用）。没有行 -> `None`。
        行数不变 -> `_set_items` 不起滚动画，只是那一行文字长了。"""
        if not self.rows:
            return None
        self.rows[-1] = (en, zh)
        return self.ids[-1]

    def replace_pair(self, tail_id, next_id, en: str, zh: str) -> bool:
        """把 `tail_id` 那一行换成 `(en, zh)`、**并删掉** `next_id` 那一行（一次原子改两行）。

        返回是否真的改了。定位不到 / 两个 id 相同 / `next` 在 `tail` 之前 → 返回 `False`
        （**一个字节都不动** —— 重翻回来时行可能已被别的路径改过，宁可不动）。
        ⚠️ 行数**减一**：`_set_items` 只在**变多**时记「新句到」并起滚，所以替换不会
        触发上滚动画（与 Reduce Motion 无关 —— 结构上就没动画）。
        ⚠️ 成功时顺带把**删除位置**记进 `last_deleted`（给 `transcript_view` 的
        半句合并过渡定位"被吞掉那一行"用；返回 `bool` 不变 —— 索引 0 是假值，
        改返回值会把 `tests/test_merge_replace.py` 的 `assertTrue/assertFalse` 弄假）。
        """
        self.last_deleted = None
        if tail_id is None or next_id is None or tail_id == next_id:
            return False
        try:
            i = self.ids.index(tail_id)
            j = self.ids.index(next_id)
        except ValueError:
            return False
        if j < i:
            return False
        self.rows[i] = (en, zh)
        del self.rows[j]
        del self.ids[j]
        self.last_deleted = j
        return True


class TranscriptView:
    def __init__(self, parent, make_label, font_zh, font_en, font_en_big,
                 width, pad, en_h, gap, zh_h, row_h, max_scroll_h,
                 on_follow_change=None, *, measure, wrap_zh=None):
        ScrollCls, DocCls = _view_classes()
        self._parent = parent
        self._make_label = make_label
        self._font_zh = font_zh
        self._font_en = font_en
        self._font_en_big = font_en_big
        self._width = width
        self._pad = pad
        self._en_h, self._gap, self._zh_h, self._row_h = en_h, gap, zh_h, row_h
        # ⚠️ 量文字高度的**注入**函数(`measure(text, width, font) -> px`), 与
        #    `make_label` 同一个套路 —— 本模块**不能** import overlay(会成环),
        #    注入才能既拿到同一把尺、又不引入依赖。
        #    用途只有一个: 把"文本超出档位行数"这个**静默裁切**变成可观测(见 _set_text)。
        self._measure = measure
        # 中文折行(`wrap_zh(text, label_w, final) -> 插好换行的文本`), 见 linebreak.py。
        # 注入而不是在这里 import: 它要量字宽, 尺子(字体)归 overlay 管。None = 交给 AppKit 逐字折。
        self._wrap_zh = wrap_zh
        self._wrap_cache: dict = {}
        self._wrap_mode = font_zh[2]          # 中文那格的折行方式(按词折), 英文两行时借用
        self._line_h: dict = {}               # 字体 -> 一行高(实测, 懒量)
        self._en2_cache: dict = {}
        # 观测计数器: 超出档位行数的次数。⚠️ 设它是为了**不再静默** ——
        # 档位行数来自一个闭样本(p100 of 4797 句, 见 overlay.py 的行数分档注释),
        # 一旦真实文本比样本更长, 原来会**无声无息**被裁掉(既不报错也不给省略号)。
        self._overflow_n = 0
        self._zh_seen_n = 0                   # 分母: 量过的译文次数(同口径, 见 `_note_overflow`)
        self._overflow_warned = False
        self._on_follow_change = on_follow_change or (lambda follow: None)

        self._items: list[tuple[str, str]] = []     # 已定稿 [(en, zh)]
        self._live: tuple[str, str] | None = None   # 流式当前行
        self._live_visible = False

        self._follow = True
        self._expected_origin = 0.0
        self._last_user_t = 0.0
        self._new_since_leave = False
        self._pending_appends = 0
        self._hold = False          # 答案接管期间: 不自动回底(见 set_scroll_hold)
        # ---- 新行入列的上滚(见 _start_roll) ----
        # 时钟与「减弱动态效果」都可注入: 位移是时间的纯函数, 探针可以确定性地断言每一帧。
        self._clock = time.monotonic
        self._reduce_motion = motion.reduce_motion
        self._animate_ok = lambda: True   # 宿主说「有人逐帧推 tick 吗」; 没有就直接落位(见 Overlay)
        self._painted_count = 0     # 上一次 _paint 时的行数(含实时行) -> 判「多了几行」
        self._painted_items = 0     # 上一次 _paint 时的**历史行**数 -> 判「上一条新行」是谁(A5)
        self._painted_live = 0      # 上一次 _paint 时实时行在不在(1/0) -> 同上, 也用于幽灵的层号
        self._count_reset = False   # 整体换内容(replace_items)那一轮不算「多了几行」
        self._roll_t0 = None        # 动画起跑时刻; None = 没在动
        self._roll_from = 0.0       # 起跑时内容往下偏多少 px(终点恒为 0; **负数 = 往上偏**)
        self._roll_off = 0.0        # 当前帧的偏移, _geom 摆每个槽位时减掉它
        self._roll_off_lo = 0       # 偏移**从第几层起**施加(半句合并时最底那行要留在原地)
        self._roll_fade_lo = 0      # 淡入名单的**起始层**(A5: 见 _start_roll 的 n/lo)
        self._roll_fade_n = 0       # 减弱动态效果: 这几行淡入, 不位移
        self._roll_fade_s = NEW_LINE_FADE_S   # 淡入时长(半句合并换成 MERGE_FADE_S)
        self._roll_alpha = 1.0
        self._ghost_j = None        # 半句合并: 「被吞掉那一行」占着的槽位(原地淡出)
        self._merge_pending = 0     # 跨段半句合并待落地几次(见 note_merge / _paint)
        self._merge_deleted = None  # 被吞掉那一行在**合并前**的行号(LineLog.last_deleted)
        self._verbatim = False      # 行 = (大字, 小字) 原样, 不做中英对调(见 set_rows_verbatim)
        # ---- D 布局（见模块头「D 布局」）----
        self._d = False             # set_d_style 打开
        self._zh_expected = True    # 宿主: 这一档有中文吗（只英 / 原文档 = False）
        self._live_kind = "final"   # 实时行是草稿还是定稿（"draft" / "final"）
        self._d_enter = False       # 这一次上滚是 D 的「原地显影」
        self._lzd = 1.0             # 实时行中文的亮度倍数（草稿 0.74 / 定稿 1.0）, 带过渡
        #: 实时行新字淡入: (槽位, "zh"/"en") -> (显示文本, 每个字的出现时刻)。空 = 没在淡。
        self._type: dict = {}
        self._lzd_from, self._lzd_to, self._lzd_t0 = 1.0, 1.0, None

        self._dirty = False
        self._urgent = False
        self._last_flush = 0.0
        self._doc_h = -1.0
        self._doc_w = -1.0

        self._scroll = ScrollCls.alloc().initWithFrame_(((0.0, 0.0), (width, 210.0)))
        # 默认 NSClipView 会画不透明底色, 会把毛玻璃和 scrim 整个盖掉
        # (最可能的"看起来坏了")。两处都要关。
        self._scroll.setDrawsBackground_(False)
        self._scroll.contentView().setDrawsBackground_(False)
        self._scroll.setHasHorizontalScroller_(False)
        self._scroll.setHorizontalScrollElasticity_(0)      # 0 = None
        self._scroll.setUsesPredominantAxisScrolling_(True)
        self._scroll.setAutohidesScrollers_(True)
        self._scroll._scroll_enabled = True
        self._scroll.setVerticalScrollElasticity_(0)
        parent.addSubview_(self._scroll)

        self._doc = DocCls.alloc().initWithFrame_(((0.0, 0.0), (width, 210.0)))
        self._scroll.setDocumentView_(self._doc)

        # 池: 一次建好, 按展开态最大视口定初始容量(K = ceil(max_h/row_h)+2)。
        # ⚠️ 这只是**初始**容量 —— 若之后换到更大的外接屏, 展开后视口会比 K 高,
        # 顶部就会露空白行。所以 _paint 每轮按实际视口高度调 _ensure_pool 兜底。
        self._slots = []
        self._cache = []
        # ⭐ **当前档位的行数上限, 池里每个槽位都得用它** —— 构造参数给的那套
        #    (ROW_ZH_H / ROW_H) 就是 2 行档, 之后 `set_row_metrics` 会覆盖。
        #    ⚠️ 必须存成状态, 不能靠 `_add_slot` 写死: `_ensure_pool` 会在**视口变大时**
        #    继续新增槽位, 而那些新槽位没有机会被 `set_row_metrics` 回填 —— 窄窗
        #    (档位 3/4/5 行)下它们只允许 2 行, 第 3 行起**静默裁掉且不给省略号**。
        #    (2026-09-27 OCR 抓出, 与 overlay.py 的草稿标签是同一类缺陷。)
        self._lines = 2
        for j in range(int(math.ceil(max_scroll_h / row_h)) + 2):
            self._add_slot()

    def _add_slot(self) -> None:
        zh = self._make_label(18.0, self._font_zh[1], self._lines, self._font_zh[2])
        en = self._make_label(11.0, self._font_en[1], 1, self._font_en[2])
        # 英文小字可能借中文那格的空行排成两行(见 _en_two_lines)。行尾省略号靠这个开关:
        # TruncatingTail 会**强制单行**, 所以换成「按词折 + 最后一行截断」。
        try:
            en.setLineBreakMode_(self._wrap_mode)
            en.cell().setTruncatesLastVisibleLine_(True)
        except Exception:                                   # noqa: BLE001
            pass
        zh.setHidden_(True); en.setHidden_(True)
        self._doc.addSubview_(zh)
        self._doc.addSubview_(en)
        self._slots.append({"zh": zh, "en": en, "level": None, "hidden": True,
                            "en_top": None, "en2": False, "ghost": False})
        self._cache.append({})

    def _ensure_pool(self, view_h: float) -> None:
        """按当前视口高度保证池够用(池只增不减 —— 换屏幕是极低频事件)。"""
        need = int(math.ceil(view_h / self._row_h)) + 2
        while len(self._slots) < need:
            self._add_slot()

    # ---- 对外: 数据 ----
    def set_content(self, items, en: str, zh: str, live_visible: bool,
                    live_kind: str = "final") -> None:
        """**原子**写入历史 + 实时行, 只画一次。

        ⚠️ 必须原子。拆成 set_items() + set_live() 两次调用时, 两次绘制之间会出现
        两种坏帧 —— 因为固定行高下, 实时行的显隐**不改变布局**, 两者会同时可见:
          ① 实时行已清、历史还没接上 -> 最新一句凭空少一行(闪丢)
          ② 实时行既在历史末尾又在实时位 -> 最新一句**重复**(finalize 时必现)
        这正是"定稿瞬间屏上多一行、最新句画两遍"的成因。
        """
        self._set_live_kind(live_kind if live_visible else "final")
        self._set_items(list(items), (en, zh) if live_visible else None,
                        bool(live_visible), bulk=False)

    # ---- 对外: D 布局 ----
    def set_d_style(self, on: bool) -> None:
        """打开 / 关掉 D 布局的画法（按层降亮 + 原地显影 + 新字淡入）。
        数据怎么喂不归这里管（`caption_flow.caption_rows`）; 接管（答疑 / 纲要）期间
        宿主照样开着它 —— D 的画法只在 `not _verbatim` 时生效。"""
        on = bool(on)
        if on == self._d:
            return
        self._d = on
        self._end_roll()
        for j, s in enumerate(self._slots):
            if s["level"] is not None:
                self._geom(j)
        self.mark_dirty(urgent=True)

    def set_zh_expected(self, on: bool) -> None:
        """宿主告诉视图「这一档会有中文」（双语档 True; 只英 / 原文档 False）。"""
        on = bool(on)
        if on != self._zh_expected:
            self._zh_expected = on
            self.mark_dirty(urgent=True)

    def _set_live_kind(self, kind: str) -> None:
        kind = "draft" if kind == "draft" else "final"
        if kind == self._live_kind:
            return
        self._live_kind = kind
        to = D_DRAFT_ZH_DIM if kind == "draft" else 1.0
        if self._reduce_motion() or not self._animate_ok():
            self._lzd = self._lzd_from = self._lzd_to = to
            self._lzd_t0 = None
        else:
            self._lzd_from, self._lzd_to, self._lzd_t0 = self._lzd, to, self._clock()

    def _step_lzd(self) -> None:
        e = motion.ease_out(motion.progress(self._lzd_t0, self._clock(), motion.STATE_S))
        self._lzd = self._lzd_from + (self._lzd_to - self._lzd_from) * e
        if e >= 1.0:
            self._lzd_t0 = None
        for j, s in enumerate(self._slots):
            if s["level"] == 0:
                self._geom(j)

    def replace_items(self, items) -> None:
        """**整体替换**内容(答案接管 / 退出接管), 不做追加记账。

        为什么不能直接用 set_content: 它把"条数变多"一律当成"字幕追加了新句"。
        答案接管时条数会从 N 条字幕整体换成 M 条答案行、退出时再换回来, 差值本来
        没有语义, 却会凭空记成"来了 N-M 句新话": _pending_appends 于是触发浏览保护
        位移(把视口推到一个无意义的位置), 并置 _new_since_leave 让闲置回底在 6s
        后把读者拽走。换内容 = 换一整套坐标系, 记账必须清零重来。"""
        self._set_items(list(items), None, False, bulk=True)

    def _set_items(self, items, live, live_visible: bool, bulk: bool) -> None:
        n = len(items)
        if not bulk and n > len(self._items):
            self._pending_appends += n - len(self._items)
            if not self._follow:
                # "只有真的有新内容才回底": 新句子(定稿)才算新内容。
                # 流式增量不算 —— 行高固定, 实时行增长不会移动文档。
                self._new_since_leave = True
        elif bulk:
            self._pending_appends = 0
            self._new_since_leave = False
            self._count_reset = True
            self._end_roll()
        self._items = items
        self._live = live
        self._live_visible = live_visible
        self.mark_dirty(urgent=True)

    def set_items(self, items) -> None:
        self.set_content(items, *(self._live or ("", "")), self._live_visible)

    def set_live(self, en: str, zh: str, visible: bool) -> None:
        self.set_content(self._items, en, zh, visible)

    def set_rows_verbatim(self, on: bool) -> None:
        """行内容是否**原样解释**为 (大字, 小字)。

        默认(False)是字幕卡片的语义: 元组是 (英文, 中文), 中文进 18pt 大字位、
        英文进 11pt 小字位, 中文为空时英文顶上去。答案行要的正好相反(英文在
        大字位、`中：`点睛在小字位), 所以给它一条显式通道 —— 而不是靠"把中文
        塞进第一个字段"这种反向 trick 去骗过 _row_texts。"""
        self._verbatim = bool(on)
        self.mark_dirty(urgent=True)

    def set_scroll_hold(self, on: bool) -> None:
        """接管期间冻住自动滚动: 不自动跟随、不闲置回底。

        ⚠️ 为什么必须显式关: 答案可能比视口长得多, 读长答案时 `tick()` 有两条
        路径会**主动移动视口** —— ① 视口一到 0(内容短于视口, 顶部=底部)就被判成
        "用户回到底部" 而重新跟随, 内容一长就每帧钉回底部; ② 新行追加后闲置 6s
        回底。两条都会把正在读长答案的人拽走。
        用户仍然可以自由滚动、也可以点 ↓最新 去看最新一行(那不是自动的)。"""
        self._hold = bool(on)
        if on:
            self._follow = False
            self._new_since_leave = False
            self._expected_origin = self._scroll.contentView().bounds().origin.y
            self._on_follow_change(False)

    def overflow_stats(self) -> dict:
        """译文超限的**次数与分母**（进测试报告 `blocks.ui.zh_overflow`）。

        ⚠️ 单位是「译文行**文本变了**的那一次」, 不是句子 —— 同一句草稿译文改几次就量几次。
           拿它判「F 值不值得做」时比的是 `overflow / seen`, 别拿 `overflow` 去除句数。
        """
        return {"overflow": self._overflow_n, "seen": self._zh_seen_n, "lines": self._lines}

    def text_width(self) -> float:
        """文档视图当前宽度 —— **折行实测**用的宽度(不是面板宽度猜出来的值:
        展开态挂竖向滚动条时 clip 会比面板窄)。"""
        w = self._doc.frame().size.width
        return w if w > 40.0 else self._width - 2 * self._pad

    def set_row_metrics(self, zh_h: float, lines: int) -> None:
        """行尺寸随窗口宽度变(窄窗里中文要更多行才不吞字)。

        ⚠️ 变的只是"等高的那个值", **所有行依然等高** —— 回收池依赖的不变量是
        "所有行等高", 不是"高 == 70", 所以换一组统一尺寸是安全的。
        池的容量 K=ceil(视口高/行高)+2 会跟着变小, 多余的槽位被 `_paint` 藏起来,
        不够时 `_ensure_pool` 会补 —— 两个方向都不用额外处理。

        槽位必须**重排**: 清掉每个槽位的 level, `_paint` 会把它们按新行高重新放置。
        文字缓存不清(文本没变, 变的只有 frame), 所以 `_set_text` 会照旧跳过。

        ⚠️ **行距(`row_h`)不收参数, 由行内三段推出来** —— 同一个几何量不能有两个
        来源: 上游若改了公式(加余量 / 改字重)而两份各自漂开, 表现是行与行**静默**
        重叠或留缝, 没有任何报错。行内三段(en_h / gap / zh_h)构造时就收到了,
        推得出来就推。(2026-09-27 OCR 指出这条双来源。)
        """
        row_h = self._en_h + self._gap + float(zh_h)      # ← 唯一来源, 见 docstring
        if abs(float(row_h) - self._row_h) < 0.5:
            return
        self._end_roll()                  # 行高一变, 进行中的位移就没有意义了 —— 直接落位
        self._zh_h = float(zh_h)
        self._row_h = float(row_h)
        self._lines = int(lines)          # 见 __init__: 供 _ensure_pool 新增的槽位用
        for s in self._slots:
            s["level"] = None
            s["zh"].setMaximumNumberOfLines_(int(lines))
        self._doc_h = -1.0
        # ⚠️ 行高变了 -> 内容总高按新 row_h 重算, 而 clip origin 的**数值**不变,
        # 它代表的却是另一行了。若不回写 _expected_origin, 下一个 tick 会把这个
        # 位移误判成"用户滚动" -> 跟随状态机被无端打断, 可见的句子会跳一下。
        # 只在**离开底部**时才需要回写(跟随态本来就钉在 0, 不需要动)。
        if not self._follow:
            self._expected_origin = self._scroll.contentView().bounds().origin.y
        self.mark_dirty(urgent=True)

    def set_width(self, width: float) -> None:
        """面板宽度变了。

        槽位与文档视图的宽度在 `_paint` 里由 clip 的**实际**宽度决定, 会自动跟上;
        这里只需记住新宽度 —— 供 `set_frame()` 和 `text_width()` 的兜底值使用。
        行高(`_row_h`)不跟着变: 回收池的不变量是"所有行等高", 与宽度无关。
        """
        if abs(float(width) - self._width) < 0.5:
            return
        self._width = float(width)
        self.mark_dirty(urgent=True)

    def set_frame(self, y: float, h: float) -> None:
        self._scroll.setFrame_(((self._pad, y), (self._width - 2 * self._pad, h)))
        # ⭐ 顶边渐隐（作者 2026-10-17）：视口一变就重贴一次遮罩 —— 这里是**唯一**
        #    知道"字幕区现在多大"的地方。卡片开着时这条边就是**卡片底下那条字幕区的
        #    顶边**（被裁的那半行落在这条带里，不再看着像坏了）；没卡时它是窗口内容
        #    区的顶边 —— 同一条边、同一份 7 档（`edge_fade`）。
        edge_fade.apply(self._scroll, edge="top")
        self.mark_dirty(urgent=True)

    def set_collapsed(self, collapsed: bool) -> None:
        from AppKit import NSScrollElasticityAutomatic
        # ⚠️ 滚动权限**与收起/展开解耦**(2026-09-24 改): 内容永远溢出(一节真实课
        # 1000+ 句 vs 可见 3 行), 所以**始终允许滚动**。
        # 原先写成 `_scroll_enabled = not collapsed`, 后果是"把窗口拉大也不能滚"——
        # 于是顶栏那个展开按钮被迫承担"解锁滚动"的职责(它的名字里完全没写这件事)。
        # 解耦后: 拉窗口 = 看几句; 滚动 = 往回翻。两个正交。
        # (橡皮筋一并常开; `setAutohidesScrollers_(True)` 在构造里已设, 滚动条不会常驻。)
        self._scroll._scroll_enabled = True
        self._scroll.setHasVerticalScroller_(True)
        self._scroll.setVerticalScrollElasticity_(NSScrollElasticityAutomatic)
        if collapsed:
            self.scroll_to_bottom()
        self.mark_dirty(urgent=True)

    # ---- 对外: 滚动 ----
    def is_at_bottom(self) -> bool:
        return self._scroll.contentView().bounds().origin.y <= BOTTOM_EPS

    def scroll_to_bottom(self) -> None:
        clip = self._scroll.contentView()
        clip.scrollToPoint_((0.0, 0.0))
        self._expected_origin = clip.bounds().origin.y
        self._new_since_leave = False
        if not self._follow:
            self._follow = True
            self._on_follow_change(True)

    def scroll_to_top(self) -> None:
        """滚到文档**顶部**(最早的一行) —— 答案接管时的落点。

        字幕流是"最新在最下", 竖着读的文档不是: 答案要从第一行读起。取 doc 视图
        自己的高度(不翻转坐标系, origin.y = 0 是底部, 所以顶部 = docH - 视口高)。"""
        clip = self._scroll.contentView()
        h = self._doc.frame().size.height
        clip.scrollToPoint_((0.0, max(0.0, h - clip.bounds().size.height)))
        self._expected_origin = clip.bounds().origin.y
        self._new_since_leave = False

    def scroll_to_index(self, index: int) -> None:
        """把**第 `index` 条**滚到**视口顶部**（`items[0]` = 屏幕上最上面那行）。

        给课堂纲要的「上次看到这里」用: 打开纲要要**落在分隔线上**,
        而不是落在最新一行（那正是 `scroll_to_bottom` 干的事）。

        ⚠️ 三条硬约束 —— 与 `scroll_to_top` / `scroll_to_bottom` **逐字一致**:
        1. **所有行等高**是这个回收池的不变量（`_layout` 里 `y = level * row_h`）
           → 位置就是算出来的，不需要问任何一行真实 frame。
        2. **坐标不翻转**: `origin.y = 0` 是**底部**，`level L` 在 `y = L * row_h`
           → 「顶」是 y **大**的那边，所以最后要 `clamp` 到 `[0, docH - 视口高]`。
           ⚠️ 不 clamp 的话，分隔线靠近文档头时 `scrollToPoint_` 会被 AppKit
           夹回边界、而**我们记的 `_expected_origin` 就成了假的**。
        3. ⚠️⚠️ **滚完必须回写 `self._expected_origin = clip.bounds().origin.y`** ——
           不写的话 `tick()` 会把这次**程序滚动**误判成**用户滚动**、顺手把跟随关掉
           （`scroll_to_top` / `scroll_to_bottom` 都这么做，这是既有契约）。
        ⚠️ `index` 越界一律 clamp，**不抛** —— 打开纲要时快照可能刚被换过。
        """
        clip = self._scroll.contentView()
        n = len(self._items)
        index = max(0, min(int(index), max(0, n - 1)))
        # `_place` 的约定: 第 level 层 = 第 (count - 1 - level) 行 → 反过来。
        level = max(0, n - 1 - index)
        vh = clip.bounds().size.height
        h = self._doc.frame().size.height
        # 目标: 这一行的**上边缘**贴视口顶。行占 `[L*row_h, (L+1)*row_h]`。
        y = (level + 1) * self._row_h - vh
        y = max(0.0, min(y, max(0.0, h - vh)))
        clip.scrollToPoint_((0.0, y))
        self._expected_origin = clip.bounds().origin.y
        self._new_since_leave = False

    @property
    def following(self) -> bool:
        return self._follow

    # ---- 对外: 驱动 ----
    def mark_dirty(self, urgent: bool = False) -> None:
        self._dirty = True
        if urgent:
            self._urgent = True
        self.flush_if_due()

    def flush_if_due(self) -> None:
        if not self._dirty:
            return
        if not self._urgent:
            now = time.monotonic()
            if now - self._last_flush < FLUSH_DT:
                return
        self._dirty = False
        self._urgent = False
        self._last_flush = time.monotonic()
        self._paint()

    def tick(self) -> None:
        """跟随状态机。轮询 clip origin 与期望值比较 —— 不用通知:
        ①不用持有 ObjC 观察者(弱引用 GC 陷阱); ②没有重入(bounds 通知会在
        我们自己 scrollToPoint_ 期间触发, 正是把程序滚动误判成用户滚动的环);
        ③惯性自然处理: 每个 tick 都在动就持续刷新计时, 停下后计时才开始。

        新行入列的上滚也在这里逐帧推进(pump 每轮都调 tick)。"""
        if self._roll_t0 is not None:
            self._step_roll()
        if self._lzd_t0 is not None:
            self._step_lzd()
        if self._type:
            self._step_type()
        clip = self._scroll.contentView()
        cur = clip.bounds().origin.y
        if abs(cur - self._expected_origin) > MOVE_EPS:
            self._last_user_t = time.monotonic()
            if self._follow and cur > BOTTOM_EPS:
                self._end_roll()          # 用户一滚就交还给他, 不和他抢
                self._follow = False
                self._new_since_leave = False
                self._on_follow_change(False)
            self._expected_origin = cur
            # 滚动改变了可见 level 区间, 池必须跟着重算 —— 否则滚过 K-2 行后
            # 新露出的位置没有槽位, 会是空白。
            # ⚠️ 必须 urgent: 滚动是**离散的用户事件**, 和定稿/术语同级, 不能排在
            # 16ms 合并闸门后面。实测非紧急时 scroll→paint 延迟 avg 7.5ms /
            # max 17.0ms, 有 11% 的帧里可见区间缺槽位 —— 那就是"滚动帧率低"的真因
            # (不是 _paint 慢: 实测单次 0.01ms; tick 0.003ms)。
            # 合并闸门是给流式 token 增量用的, 那些才该合并。
            self.mark_dirty(urgent=True)
            return
        if not self._follow:
            # ⚠️ _hold(答案接管)期间这两条自动回底都必须关掉: "视口回到 0" 在答案
            # 比视口短时**恒成立**(顶部即底部), 一旦据此重新跟随, 内容长过视口后
            # 每帧都会被钉回底部; 闲置回底同理, 读长答案读到一半就被拽走。
            if not self._hold and cur <= BOTTOM_EPS:   # 用户自己滚回底部
                self._follow = True
                self._new_since_leave = False
                self._on_follow_change(True)
            elif (self._new_since_leave and not self._hold
                  and time.monotonic() - self._last_user_t >= IDLE_S):
                self.scroll_to_bottom()

    # ---- 渲染 ----
    def _row_texts(self, r: int):
        """r 行 → (大字文本, 小字文本, 大字是否中文)。"""
        if r < len(self._items):
            en, zh = self._items[r]
        else:
            en, zh = self._live or ("", "")
        if self._verbatim:
            # 答案行: 元组就是 (大字, 小字) 本身, 不做中英对调。is_zh 只用来选字体,
            # 而大字位的两个字体对象是同一个(18pt Medium), 所以这里恒返回 False。
            return en, zh, False
        if zh:
            return self._shape_zh(zh, final=r < len(self._items)), en, True
        if self._d and self._zh_expected and r >= len(self._items):
            # D: 实时行的译文还没到 —— 英文留在上面的小字位、中文那格空着等（原型的「预留」）,
            #    不把英文提成大字: 否则译文一到, 这一行的上下顺序和字号整个翻一次。
            return "", en, True
        # 翻译关闭/失败时 zh 为空: 把英文提到大字行, 否则大字行空白
        return en, "", False

    def _lh(self, font) -> float:
        h = self._line_h.get(id(font))
        if h is None:
            h = self._line_h[id(font)] = float(self._measure("中Hg", 1e4, font)) or 1.0
        return h

    def _en_line_h(self) -> float:
        return self._lh(self._font_en[0])

    def _en_two_lines(self, big: str, sub: str, is_zh: bool) -> bool:
        """这一行的英文要不要排两行。

        英文小字原来**强制单行**、装不下就截成「…」: 3 节真课 1459 句里, 默认宽度
        有 118 句(8%)被截, 窄窗(408)有 387 句(27%)。行高是固定档位、不能为它加高,
        但**中文多数只用一行**(默认宽度 90%), 那一格里空着一行 —— 英文借它就行。
        条件: 英文一行装不下、且中文比档位少用至少一行。中文也占满档位时照旧截断。
        """
        if not (EN_LINE_ON_TOP and is_zh and sub) or self._doc_w < 40.0:
            return False
        key = (sub, big, self._doc_w, self._lines)
        hit = self._en2_cache.get(key)
        if hit is not None:
            return hit
        w = self._doc_w - 4.0
        en_n = round(self._measure(sub, w, self._font_en[0]) / self._en_line_h())
        if "\n" in big:
            zh_n = big.count("\n") + 1
        else:
            zh_n = round(self._measure(big, w, self._font_zh[0]) / self._lh(self._font_zh[0]))
        out = en_n >= 2 and zh_n <= self._lines - 1
        if len(self._en2_cache) > 400:
            self._en2_cache.clear()
        self._en2_cache[key] = out
        return out

    def _shape_zh(self, zh: str, *, final: bool) -> str:
        """中文按词组折行(定稿再避开孤尾)。**按 (文本, 宽度, 是否定稿) 记忆化** ——
        `_paint` 每帧都会对可见行走到这里, 而折一句要量十几次字宽。"""
        if self._wrap_zh is None or self._doc_w < 40.0:
            return zh
        key = (zh, self._doc_w, final)
        out = self._wrap_cache.get(key)
        if out is None:
            try:
                out = self._wrap_zh(zh, self._doc_w, final)
            except Exception:                       # noqa: BLE001  折行是锦上添花, 坏了就交还 AppKit
                out = zh
            if len(self._wrap_cache) > 400:
                self._wrap_cache.clear()
            self._wrap_cache[key] = out
        return out

    def _count(self) -> int:
        return len(self._items) + (1 if self._live_visible else 0)

    def _set_text(self, slot_j: int, which: str, text: str, font=None) -> None:
        """按槽位缓存跳过未变内容 —— 缓存必须挂在槽位上, 因为一个槽位会被
        改派到别的行, 那时必须和它**上一行**的文本比较。"""
        c = self._cache[slot_j]
        lbl = self._slots[slot_j][which]
        key_f = which + "_font"
        # D: 当前行英文**从开头截**（见 `truncate_head_to_height`）; 同一串文本在实时行 /
        #    历史行显示不同, 所以「是不是实时行」也进缓存键。
        head = bool(which == "en" and text and self._d and not self._verbatim
                    and self._live_visible and self._slots[slot_j]["level"] == 0)
        if c.get(which) == text and c.get(key_f) is font and c.get("en_head") == (
                head if which == "en" else c.get("en_head")):
            return
        if font is not None and lbl.font() != font:
            lbl.setFont_(font)
        # ⚠️ 只在**译文行**上查超限 —— 答案接管期间(`_verbatim`, 与 `_hold` 同时置位)
        #    这一列装的是**中文讲解**, 是另一种内容, 由 `_answer_view_rows` 自己保证
        #    每行放得下; 拿译文的档位去量它只会误报(实测: R8 那个超长词用例就报了一次)。
        display = text
        if which == "zh" and text and not self._verbatim:
            # ⭐ 第 7 项(报告 §5 的 F): 先按**原文**记账(统计口径不变, R13 钉着),
            #    再把显示截到档位行数内 + 「…」。**只截显示** —— `_items` / `_live` 里
            #    的对象一个字节都不动, 所以 `sessions/*.md` / `livemetrics` / `writer`
            #    拿到的永远是原文。定稿行与草稿行(实时行, `final=False`)同一条路。
            self._note_overflow(lbl, text)
            display = self._fit_zh(lbl, text)
        elif head:
            display = self._fit_en_head(lbl, text)
        # 缓存存的是**原文**: 同一段译文重复回来仍然跳过重排(判据与改前逐字一致)。
        c[which] = text
        c[key_f] = font
        if which == "en":
            c["en_head"] = head
        live = bool(self._d and not self._verbatim and self._live_visible
                    and self._slots[slot_j]["level"] == 0)
        if live and not self._reduce_motion() and self._animate_ok():
            self._type_start(slot_j, which, lbl, display)
            return
        self._type.pop((slot_j, which), None)
        lbl.setStringValue_(display)

    def _fit_zh(self, lbl, text: str) -> str:
        """把要显示的译文截到**当前档位行数**里(超出则尾部「…」)。量不出来就原样返回。"""
        w = float(lbl.frame().size.width)
        if w < 20.0:
            return text                      # 还没摆过(宽度没定): 量了没意义
        try:
            return truncate_to_height(
                text, lambda s: self._measure(s, w, lbl.font()), self._zh_h)
        except Exception:                    # noqa: BLE001 截断是显示层兜底, 坏了就交还 AppKit
            return text

    # ---- D: 实时行新字淡入（原型 dIns）----
    def _type_start(self, slot_j: int, which: str, lbl, display: str) -> None:
        """记下每个字的出现时刻（对齐规则见 `carry_births`）。"""
        old = self._type.get((slot_j, which))
        prev = old[0] if old else lbl.stringValue()
        births = old[1] if old else [0.0] * len(prev)
        nb = carry_births(prev, births, display, self._clock(),
                          min_run=2 if which == "zh" else 3, soft=_REVISE_SHIFT)
        self._type[(slot_j, which)] = (display, nb)
        self._type_paint(slot_j, which)

    def _type_paint(self, slot_j: int, which: str) -> None:
        spec = self._type.get((slot_j, which))
        if spec is None:
            return
        display, births = spec
        lbl = self._slots[slot_j][which]
        now = self._clock()
        alphas = []
        live = False
        for b in births:
            e = motion.ease_out(motion.progress(b, now, D_TYPE_S)) if b else 1.0
            if e < 1.0:
                live = True
            # 量化到 1/12 档: 相邻同档的字拼成一段, 属性串只有几段
            alphas.append(round((D_TYPE_FROM + (1.0 - D_TYPE_FROM) * e) * 12) / 12)
        if not live:
            self._type.pop((slot_j, which), None)
            lbl.setStringValue_(display)
            return
        try:
            from AppKit import (NSAttributedString, NSFontAttributeName,
                                NSForegroundColorAttributeName, NSShadowAttributeName,
                                NSColor, NSMutableAttributedString, NSParagraphStyleAttributeName,
                                NSMutableParagraphStyle)
            base = lbl.textColor() or NSColor.whiteColor()
            attrs0 = {NSFontAttributeName: lbl.font()}
            sh = lbl.shadow()
            if sh is not None:
                attrs0[NSShadowAttributeName] = sh   # 描边跟着, 否则淡入期间字会发糊
            ps = NSMutableParagraphStyle.alloc().init()
            ps.setLineBreakMode_(lbl.lineBreakMode())
            ps.setAlignment_(lbl.alignment())
            attrs0[NSParagraphStyleAttributeName] = ps
            out = NSMutableAttributedString.alloc().init()
            i = 0
            n = len(display)
            while i < n:
                j = i
                while j < n and alphas[j] == alphas[i]:
                    j += 1
                at = dict(attrs0)
                at[NSForegroundColorAttributeName] = base.colorWithAlphaComponent_(
                    base.alphaComponent() * alphas[i])
                out.appendAttributedString_(
                    NSAttributedString.alloc().initWithString_attributes_(display[i:j], at))
                i = j
            lbl.setAttributedStringValue_(out)
        except Exception:                        # noqa: BLE001 淡入是锦上添花
            self._type.pop((slot_j, which), None)
            lbl.setStringValue_(display)

    def _step_type(self) -> None:
        for key in list(self._type):
            self._type_paint(*key)

    @property
    def animating(self) -> bool:
        """有没有逐帧推进的东西（宿主据此保持热档让步: 上滚 / 中文提亮 / 新字淡入）。"""
        return bool(self._roll_t0 is not None or self._lzd_t0 is not None or self._type)

    def _fit_en_head(self, lbl, text: str) -> str:
        """实时行英文: 放不下就从开头截 +「…」（留住刚说的词）。量不出来就原样返回。"""
        w = float(lbl.frame().size.width)
        h = float(lbl.frame().size.height)
        if w < 20.0 or h < 4.0:
            return text
        try:
            return truncate_head_to_height(text, lambda t: self._measure(t, w, lbl.font()), h)
        except Exception:                    # noqa: BLE001 显示层兜底
            return text

    # ---- D: 显影模糊 ----
    def _set_blur(self, s, r: float) -> None:
        """新行显影的模糊（半径 r px; 0 = 摘掉滤镜）。坏了就只剩淡入, 不影响功能。"""
        s["blur"] = r
        for key in ("zh", "en"):
            lbl = s[key]
            try:
                if r <= 0.05:
                    if lbl.layer() is not None and lbl.layer().filters():
                        lbl.layer().setFilters_(None)
                    continue
                if not lbl.wantsLayer():
                    lbl.setWantsLayer_(True)
                lbl.setLayerUsesCoreImageFilters_(True)
                from Quartz import CIFilter
                f = CIFilter.filterWithName_("CIGaussianBlur")
                f.setDefaults()
                f.setValue_forKey_(r, "inputRadius")
                lbl.layer().setFilters_([f])
            except Exception:                # noqa: BLE001
                s["blur"] = 0.0
                return

    def _note_overflow(self, lbl, text: str) -> None:
        """把「文本超出档位行数」从**静默裁切**变成**可观测**。

        ⚠️ 为什么要它: 档位行数(2/3/4/5)**来自一个闭样本** —— `overlay.py` 里对
        4797 句真实中文定稿按宽度取 p100。样本之外的更长文本会被 AppKit 在
        `maximumNumberOfLines` 处**无声**吃掉(不留省略号, 见 `overlay.py` 文件头实测)。
        2026-10-16 起**不再是静默裁切**: 同一个 `_set_text` 里紧接着调 `_fit_zh` 把显示
        截到档位行数内 + 「…」(第 7 项)。这条本身仍然只做**观测**: 记数 + 首次打一行
        —— 它量的是**原文**的高度, 所以统计口径与改前逐字一致(报告 §5 的 F)。

        判据用**盒子自己的高度**: 盒子就是 `_lines` 行 + 一点余量, 所以量出来的高度
        一旦超过 `self._zh_h` 就是装不下 —— 不必知道行高是多少。

        ⚠️ 只在**文本变了**的时候调(缓存未命中), 所以开销是每改一句一次, 不是每帧。
        """
        w = float(lbl.frame().size.width)
        if w < 20.0:
            return                      # 还没摆过(宽度没定): 量了没意义, 别误报
        try:
            need = self._measure(text, w, lbl.font())
        except Exception:               # noqa: BLE001
            return                      # 量不了就算了 —— 这是观测, 不该影响功能
        self._zh_seen_n += 1
        if need <= self._zh_h + 0.5:
            return
        self._overflow_n += 1
        if not self._overflow_warned:
            self._overflow_warned = True
            print(f"⚠ transcript_view: 有译文超出档位行数({self._lines} 行 / 盒高 "
                  f"{self._zh_h:.0f}px, 实测需 {need:.0f}px) —— 显示已从尾部截断 + "
                  f"「{ELLIPSIS}」(原文不动)。若某门课的译文普遍更长, 请调大 "
                  f"overlay.LINE_TIERS / MAX_LINES 的档位。", flush=True)

    def _place(self, j: int, level: int, count: int, doc_w: float) -> None:
        """把第 level 层(＝第 count-1-level 行)摆进槽位 j。"""
        big, sub, is_zh = self._row_texts(count - 1 - level)
        self._layout(j, level, doc_w, big, sub, is_zh)

    def _layout(self, j: int, level: int, doc_w: float,
                big: str, sub: str, is_zh: bool) -> None:
        """摆一行的两个标签: 几何 + 文本。

        竖排顺序**逐行判定** —— 同屏会混着双语行和答案行, 判据见 EN_LINE_ON_TOP。"""
        s = self._slots[j]
        s["level"] = level
        s["en_top"] = EN_LINE_ON_TOP and is_zh
        s["w"] = doc_w
        s["en2"] = self._en_two_lines(big, sub, is_zh)
        s["en"].setMaximumNumberOfLines_(2 if s["en2"] else 1)
        self._geom(j)
        self._set_text(j, "zh", big, self._font_zh[0] if is_zh else self._font_en_big[0])
        self._set_text(j, "en", sub, self._font_en[0])
        if s["hidden"]:
            s["zh"].setHidden_(False); s["en"].setHidden_(False)
            s["hidden"] = False

    def _geom(self, j: int) -> None:
        """槽位 j 的 frame 与透明度。**槽位几何的唯一写者** —— 摆放(_layout)和上滚的
        每一帧(_step_roll)都走这里, 所以动画期间不会有第二个人把 frame 改回去
        (`PLAN-roll-motion.md` §5.1「谁拥有 frame」)。"""
        s = self._slots[j]
        # 宽度取文档**当前**宽度, 不取摆放时记下的: 上滚每帧都会重写 frame,
        # 用旧值会把拖宽过的面板里的行缩回去。
        level = s["level"]
        w = self._doc_w if self._doc_w > 0 else s.get("w", self._width)
        # ⭐ 半句合并: 被吞掉那一行**原地**淡出(幽灵不跟着位移走), 而它**下面**的层
        #    (未跟随时最底那行、跟随时的实时行)也不动 —— 少的那一行在它上面。
        off = 0.0 if (s.get("ghost") or level < self._roll_off_lo) else self._roll_off
        y = level * self._row_h - off
        if s["en_top"]:                              # 英文小字在上, 中文大字在下
            s["en"].setFrame_(((0.0, y + self._zh_h + self._gap), (w, self._en_h)))
            s["zh"].setFrame_(((0.0, y), (w, self._zh_h)))
        else:                                        # 大字在上
            s["en"].setFrame_(((0.0, y), (w, self._en_h)))
            s["zh"].setFrame_(((0.0, y + self._en_h + self._gap), (w, self._zh_h)))
        if s["en_top"] and s.get("en2"):
            # 英文借中文那格的一行: 英文框往下长一行, 中文框顶边跟着降 —— 行距不变
            # (所有行等高是回收池的命门), 中文只是从这一格的空行里让出位置。
            x = self._en_line_h()
            s["en"].setFrame_(((0.0, y + self._zh_h + self._gap - x), (w, self._en_h + x)))
            s["zh"].setFrame_(((0.0, y), (w, self._zh_h - x)))
        entering = False
        if s.get("ghost"):
            # 幽灵: α 1→0（与尾句新文字 0→1 同一个时长、同一个起跑时刻 = 一次交叉淡换）。
            a = 1.0 - roll_fade_alpha(self._roll_t0 or 0.0, self._clock(), MERGE_FADE_S)
        else:
            entering = self._roll_fade_lo <= level < (self._roll_fade_lo + self._roll_fade_n)
            a = self._roll_alpha if entering else 1.0
        a_en = a_zh = a
        blur = 0.0
        if self._d and not self._verbatim:
            # 亮度跟着**视觉**位置走: 被顶上去的行一边上移一边变暗, 不在某一帧突然暗一档。
            vis = level - (off / self._row_h if self._row_h > 0 else 0.0)
            dim = d_dim(vis) if self._follow else 1.0
            a_en = a_zh = a * dim
            if level == 0 and self._live_visible:
                a_zh *= self._lzd
            if entering and self._d_enter and not self._reduce_motion():
                blur = D_BLUR_PX * (1.0 - a)
        # `alpha` = 入场 / 淡换的那个 α（判据读它）; D 的按层降亮叠在上面, 实际写进标签的
        # 一对值另记在 `alpha_vis`（老布局里两者相等）。
        s["alpha"] = a
        if s.get("alpha_vis", (1.0, 1.0)) != (a_en, a_zh):
            s["zh"].setAlphaValue_(a_zh); s["en"].setAlphaValue_(a_en)
            s["alpha_vis"] = (a_en, a_zh)
        if s.get("blur", 0.0) != blur:
            self._set_blur(s, blur)

    # ---- 新行入列: 整块上滚一个行高 ----
    # 「定稿替换草稿」那一下: 实时行出现(或一句定稿进来)时, 老行原地上跳一个行高、
    # 新行凭空出现在最底下 —— 而草稿就在转录区正下方, 读起来是「草稿没了, 上面换了一句」。
    # 改成: 内容先**往下偏一个行高**(新行藏在视口底边之下, 也就是草稿的位置), 再按
    # motion.MOVE_S 的 ease-out 滑回原位 —— 新行**从草稿那里升上来**, 老行一起被推上去。
    # 位移的是整块, 不是各自补间(两行各动各的就是「字在打架」, 方案 §2)。
    # 减弱动态效果: 不位移, 最新那几行淡入(Apple HIG 的原话是「用淡入代替 x/y/z 轴的过渡」)。
    def _start_roll(self, n: int, lo: int = 0, shift: int | None = None) -> None:
        """起跑。`n` = **新到的那几行**在淡入名单里的条数, `lo` = 名单的起始层,
        `shift` = 老内容被顶高了几格(默认 = `n`)。

        ⚠️ `lo` 不是装饰: 实时行**一直占着最底那一层**(`_count` 把它算在行数里),
        所以"这次新到了几行"和"最底那几行"**不是同一段** —— 见 `_paint` 里怎么算
        `lo`/`n`(A5 的已知问题: 一次进来 ≥2 行时, 上一条已经显示过的行会被重新
        计入淡入、闪一下)。`shift` 与 `n` 分开也是同一个理由: 位移量是"行数差",
        名单长度是"这次真的新了几行"。
        """
        if not self._animate_ok():
            self._end_roll()                # 没人推帧: 不起跑, 直接是终态
            return
        self._end_ghost()                   # 上滚与合并是两套位移, 幽灵只属于合并那一次
        self._roll_off_lo = 0
        now = self._clock()
        if self._reduce_motion():
            self._end_roll()
            self._roll_fade_lo, self._roll_fade_n, self._roll_alpha = lo, n, 0.0
        else:
            # 动画中又来一行: 从**当前位置**接着走(老内容又被顶高一行, 所以起点加一个行高),
            # 不重置也不跳 —— 方案 §5.2 的选项 ①。
            # 封顶两个行高: 槽位池只比视口多备了 2 行(K = ceil(视口/行高)+2), 偏得更多
            # 顶上就会露出没有槽位的空白。一次来 3 句以上本来就罕见。
            k = n if shift is None else shift
            self._roll_from = min(self._roll_off + k * self._row_h, 2.0 * self._row_h)
            self._roll_off = self._roll_from
            # ⭐ A5(报告 §5): 位移走完整 MOVE_S, 但**新到的那几行**额外从 α0 淡入,
            #    时长只有 `NEW_LINE_FADE_S`(= MOVE_S*0.55) —— 位移还在走, 它已经就位。
            #    ⚠️ 与 Reduce Motion 那支**共用** `_roll_fade_*` / `_roll_alpha`
            #    (同一个 `_geom` 施加点), 不开第二套机制。
            self._roll_fade_lo, self._roll_fade_n, self._roll_alpha = lo, n, 0.0
        self._roll_t0 = now
        self._regeom()          # 留在原槽位的行也要按新偏移重摆(_paint 只摆新进来的)

    # ---- 跨段半句合并: 「少一行」的那一下 ----
    def note_merge(self, deleted: int | None) -> None:
        """`_history` 刚被 `LineLog.replace_pair` 少了一行、尾句那一行的文字也换了。

        `deleted` = 被吞掉那一行在**调用前**的行号(`LineLog.last_deleted`)。
        真正的判定在 `_paint`(要等行数对账), 这里只记一笔 —— 组合成"少一行但没多行"
        才做过渡, 否则老实落位(见 `_paint` 里那段)。
        """
        self._merge_pending += 1
        self._merge_deleted = deleted

    def _start_merge_roll(self, off_lo: int, *, slide: bool = True) -> None:
        """半句合并的位移: **把上滚反过来** —— 内容先**高**一个行高, 再滑下来。

        ⚠️ 复用同一个时钟(`_roll_t0`)与同一条曲线(`motion.ease_out`), 不另起一套计时;
        淡入名单只有**一行**(合并后的尾句), 时长换成 `MERGE_FADE_S`(原地换内容那一档)。
        ⚠️ 偏移从 `off_lo` 层起施加: `off_lo` 以下的层(实时行)**没动**, 不该跟着偏。
        `slide=False`(手动回看)/ 减弱动态效果: 不位移, 只保留那一次淡换。
        """
        now = self._clock()
        self._end_ghost()                       # 上一次的幽灵别赖着不走
        self._roll_off_lo = int(off_lo)
        if self._reduce_motion() or not slide:
            self._roll_from = self._roll_off = 0.0
        else:
            self._roll_from = self._roll_off = -self._row_h
        self._roll_fade_lo = int(off_lo)
        self._roll_fade_n, self._roll_alpha = 1, 0.0
        self._roll_fade_s = MERGE_FADE_S
        self._roll_t0 = now

    def _make_ghost(self, glev: int) -> bool:
        """把被吞掉那一行占着的那一格标成**幽灵**: 原地淡出, 不参与位移与摆放。

        返回是否真的找到了那一格。⚠️ 一个字的文本都不重写 —— 那格正显示着那一行,
        「原地淡出」就该是**它自己**淡掉(复制一份新文本反而多一次重排)。
        """
        for j, s in enumerate(self._slots):
            if s["level"] == glev and not s.get("ghost"):
                s["ghost"] = True
                self._ghost_j = j
                # 幽灵独占一格 -> 先把池扩一格, 否则可见行会差一行(见 _ensure_pool)
                self._ensure_pool(self._scroll.contentView().bounds().size.height
                                  + self._row_h)
                self._geom(j)
                return True
        return False

    def _step_roll(self) -> None:
        e = motion.ease_out(motion.progress(self._roll_t0, self._clock(), motion.MOVE_S))
        if e >= 1.0:
            self._end_roll()
            return
        # 位移: `_roll_from` 在 Reduce Motion / 落位后恒为 0, 所以这一步对那一支是空操作。
        # 半句合并那一支的 `_roll_from` 是**负数** —— 同一行代码, 方向相反。
        self._roll_off = self._roll_from * (1.0 - e)
        if self._roll_fade_n and self._d_enter:
            # D: 新行晚 D_ENTER_DELAY_S 起跑（等被顶上去的那行先让开）, 与位移同时走完。
            self._roll_alpha = roll_fade_alpha(self._roll_t0 + D_ENTER_DELAY_S,
                                               self._clock(), D_ENTER_S)
        elif self._roll_fade_n:
            # ⭐ A5 / 合并: 淡入只走 `_roll_fade_s`, 先于位移到 1, 之后保持不透明。
            self._roll_alpha = roll_fade_alpha(self._roll_t0, self._clock(),
                                               self._roll_fade_s)
        self._regeom()          # 幽灵的淡出也在这里逐帧推进(_geom 按 `_roll_t0` 算)

    def _end_roll(self) -> None:
        """直接落到终态(位移 0、全不透明)。动画自然结束、被打断、换内容都走这里 ——
        「终态精确」是硬判据: 停在半路会让字幕挡在错误的位置(方案 §3.2 ③)。"""
        was = self._roll_t0 is not None
        self._roll_t0 = None
        self._roll_from = self._roll_off = 0.0
        self._roll_off_lo = 0
        self._roll_fade_lo, self._roll_fade_n, self._roll_alpha = 0, 0, 1.0
        self._roll_fade_s = NEW_LINE_FADE_S
        self._d_enter = False
        self._end_ghost()
        if was:
            self._regeom()

    def _end_ghost(self) -> None:
        """收起「被吞掉那一行」的幽灵(幂等) —— 它只在合并那一次动画里存在。"""
        j, self._ghost_j = self._ghost_j, None
        if j is not None:
            self._hide(j)

    def _regeom(self) -> None:
        for j, s in enumerate(self._slots):
            if s["level"] is not None:
                self._geom(j)

    @property
    def rolling(self) -> bool:
        return self._roll_t0 is not None

    def _hide(self, j: int) -> None:
        s = self._slots[j]
        if s["zh"].stringValue():
            s["zh"].setStringValue_(""); s["en"].setStringValue_("")
        self._cache[j]["zh"] = ""; self._cache[j]["en"] = ""
        self._type.pop((j, "zh"), None); self._type.pop((j, "en"), None)
        if not s["hidden"]:
            s["zh"].setHidden_(True); s["en"].setHidden_(True)
            s["hidden"] = True
        s["level"] = None
        s["en_top"] = None
        # ⚠️ 幽灵标记必须清掉: 槽位会被改派给别的行, 带着它下一行就永远不参与位移了
        s["ghost"] = False
        if s.get("alpha_vis", (1.0, 1.0)) != (1.0, 1.0):   # 槽位会被改派给别的行, 别把半透明带过去
            s["zh"].setAlphaValue_(1.0); s["en"].setAlphaValue_(1.0)
        s["alpha"] = 1.0
        s["alpha_vis"] = (1.0, 1.0)
        if s.get("blur", 0.0):
            self._set_blur(s, 0.0)

    def _paint(self) -> None:
        clip = self._scroll.contentView()
        view_h = clip.bounds().size.height
        doc_w = clip.bounds().size.width
        self._ensure_pool(view_h)               # 视口可能因换屏变大, 池要跟上
        count = self._count()
        doc_h = max(count * self._row_h, view_h)
        reset = self._count_reset
        grew = 0 if reset else count - self._painted_count
        # ⚠️ 行数差(`grew`)与"这次新到的是哪几行"**不是一回事**: 实时行一直占着
        #    最底那一层(`_count` 把它算在行数里), 所以一次进来 ≥2 行时最底那两行
        #    里可能有一行是**上一条已经显示过的**(A5 的已知问题)。判据只能靠
        #    「历史行数变了几行 + 实时行在不在」两个数, 见下面 lo/n 那段。
        items_before, live_before = self._painted_items, self._painted_live
        items_now, live_now = len(self._items), (1 if self._live_visible else 0)
        self._painted_count, self._count_reset = count, False
        self._painted_items, self._painted_live = items_now, live_now

        # ⭐ 跨段半句合并(`Overlay.replace_merge` → `LineLog.replace_pair`):
        #    少一行**并且**尾句那一行的文字被换成合并译文 —— 两件事都要过渡, 不然
        #    上面的行瞬移下来一行、尾句的文字直接换(已显示文字被改 = 分心, CHI 2023)。
        #    判定: 这一笔待落地 + 净少一行 + 删的是**最底那一行历史行**(删中间的话它
        #    下面的行不该跟着动, 而位移是整块的 -> 老实落位)+ 实时行没在这次改换态。
        dj = getattr(self, "_merge_deleted", None)
        merge = False
        if self._merge_pending:
            self._merge_pending = 0
            merge = bool(
                grew == -1 and not reset and dj is not None
                and 0 <= dj < items_before and dj == items_before - 1
                and live_now == live_before and self._animate_ok()
                # ⚠️ 答疑 / 纲要接管期间(`_verbatim`)屏上是**另一种内容**, 行数变少
                #    与"合并"无关(接管换渲染源)。那时只改 `_history`, 不做过渡。
                and not self._verbatim)

        # 浏览保护: 未跟随时新追加的行会把老内容整体上移 k*row_h,
        # 把视口同步上移同样距离, 视线不动。必须回写 _expected_origin,
        # 否则下一个 tick 会把我们自己的位移误判成用户滚动。
        k = self._pending_appends
        if k and not self._follow:
            oy = clip.bounds().origin.y
            clip.scrollToPoint_((0.0, oy + k * self._row_h))
            self._expected_origin = clip.bounds().origin.y
        self._pending_appends = 0
        if grew < 0 and not self._follow:
            # 反向的浏览保护: 少一行时**上面的行整体下移**一格, 视口跟着下移同样的
            # 距离, 视线不动(mirror 上面那一条)。手动回看时那一行本来就在视口外,
            # 所以这一支**不做位移动画**(只有内容淡换, 见 _start_merge_roll)。
            oy = clip.bounds().origin.y
            clip.scrollToPoint_((0.0, max(0.0, oy + grew * self._row_h)))
            self._expected_origin = clip.bounds().origin.y

        width_changed = abs(self._doc_w - doc_w) > 0.5
        if abs(self._doc_h - doc_h) > 0.5 or width_changed:
            self._doc_h, self._doc_w = doc_h, doc_w
            self._doc.setFrame_(((0.0, 0.0), (doc_w, doc_h)))

        if self._follow:
            # 跟随态: 钉在底部(origin 0), 内容从下往上铺
            if clip.bounds().origin.y != 0.0:
                clip.scrollToPoint_((0.0, 0.0))
            self._expected_origin = clip.bounds().origin.y
            if grew > 0:
                # ⭐ A5 的修正: 淡入名单是"这次**新到**的那几行", 而实时行一直占着
                #    最底那一层 -> 名单从 `live_now` 起, 条数 = 这次新到的历史行数;
                #    实时行**这次才出现**时它自己也是最底那一行新的, 名单从 0 起。
                #    ⚠️ 位移量仍然是 `grew`(老内容被顶高的格数), 与名单长度无关。
                d_items = items_now - items_before
                if self._d and not self._verbatim:
                    # D: 新的永远是**最底那 grew 行**（草稿并进了实时行, 上一段还在流的那行
                    #    被挪进 items —— 「历史多了一行」此时不代表它是新内容）。
                    #    新行原地显影、不位移; 只有被顶上去的老行走位移。
                    self._start_roll(grew, 0, grew)
                    if self._roll_t0 is not None:
                        self._d_enter = True
                        self._roll_off_lo = grew
                        self._regeom()
                elif live_now and not live_before:
                    lo, n = 0, d_items + 1
                    self._start_roll(n, lo, grew)
                else:
                    lo, n = live_now, d_items
                    self._start_roll(n, lo, grew)
        if merge:
            # 半句合并: 位移 + 淡换(未跟随时**只**淡换 —— 视线不许动)。
            self._start_merge_roll(live_now, slide=self._follow)
            # 幽灵 = 被吞掉那一行的**那一格本身**: 原地淡出, 一个字的文本都不重写。
            self._make_ghost(live_before)
            # 留在原槽位、只是被顶下去一格的行走这里重摆(`_paint` 只摆**新进来**的);
            # 文本还没换(在下面那段刷新里), 但都在这一轮 `_paint` 内完成、同一帧上屏。
            self._regeom()

        L0 = max(0, int(clip.bounds().origin.y // self._row_h))
        hi = min(L0 + len(self._slots) - 1, count - 1)

        # 保留仍在覆盖区间内的槽位; 其余的腾出来给新进入的 level。
        # ⚠️ 幽灵**不参与**这一套: 它自己管自己(原地淡出), 既不能被当成某一层保留,
        #    也不能被 `_hide` 掉(那会让它提前消失)。
        keep = {}
        free = []
        for j, s in enumerate(self._slots):
            lv = s["level"]
            if s.get("ghost"):
                continue
            if lv is not None and L0 <= lv <= hi and lv not in keep:
                keep[lv] = j
            else:
                free.append(j)
        missing = [lv for lv in range(L0, hi + 1) if lv not in keep]
        for lv, j in zip(missing, free):
            self._place(j, lv, count, doc_w)
        for j in free[len(missing):]:
            self._hide(j)

        # 文案刷新: _place 已写过新放入的槽位, 这里只需刷保留槽位 ——
        # 已定稿的行文本不变(缓存跳过), 真正会写的是流式中的实时行。
        if width_changed:
            # 面板拖宽 / 拖窄: 留在原槽位的行也要跟着换宽度 —— _place 只摆**新进来**的,
            # 不重摆的话老行停在旧宽度上(拖宽了还按窄的折行)直到滚出视口。
            for j in keep.values():
                self._geom(j)
        for lv, j in keep.items():
            big, sub, is_zh = self._row_texts(count - 1 - lv)
            # 竖排顺序会随内容变(实时行从"还没译文"变成"有译文")。变了必须重摆:
            # _place 只在槽位**新进入**时调用, 光刷文本会把 frame 留在旧顺序上,
            # 同屏于是出现"有的英文在上、有的在下"。
            if (self._slots[j].get("en_top") != (EN_LINE_ON_TOP and is_zh)
                    or self._slots[j].get("en2") != self._en_two_lines(big, sub, is_zh)):
                # 英文一行 / 两行也随内容变(流式中文长到占满档位时英文要让回一行)
                self._layout(j, lv, doc_w, big, sub, is_zh)
                continue
            self._set_text(j, "zh", big,
                           self._font_zh[0] if is_zh else self._font_en_big[0])
            self._set_text(j, "en", sub, self._font_en[0])
