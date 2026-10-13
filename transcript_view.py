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

IDLE_S = 6.0          # 闲置多久才自动回底(仅在离开底部后又有新句定稿时)
BOTTOM_EPS = 3.0      # origin.y <= 此值即视为在底部(橡皮筋的负值也算)
MOVE_EPS = 1.5        # 与期望 origin 相差超过此值 -> 判定为用户/惯性滚动
FLUSH_DT = 0.016      # 渲染合并闸门 = 一帧

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
        self._count_reset = False   # 整体换内容(replace_items)那一轮不算「多了几行」
        self._roll_t0 = None        # 动画起跑时刻; None = 没在动
        self._roll_from = 0.0       # 起跑时内容往下偏多少 px(终点恒为 0)
        self._roll_off = 0.0        # 当前帧的偏移, _geom 摆每个槽位时减掉它
        self._roll_fade_n = 0       # 减弱动态效果: 最新的这几行淡入, 不位移
        self._roll_alpha = 1.0
        self._verbatim = False      # 行 = (大字, 小字) 原样, 不做中英对调(见 set_rows_verbatim)

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
                            "en_top": None, "en2": False})
        self._cache.append({})

    def _ensure_pool(self, view_h: float) -> None:
        """按当前视口高度保证池够用(池只增不减 —— 换屏幕是极低频事件)。"""
        need = int(math.ceil(view_h / self._row_h)) + 2
        while len(self._slots) < need:
            self._add_slot()

    # ---- 对外: 数据 ----
    def set_content(self, items, en: str, zh: str, live_visible: bool) -> None:
        """**原子**写入历史 + 实时行, 只画一次。

        ⚠️ 必须原子。拆成 set_items() + set_live() 两次调用时, 两次绘制之间会出现
        两种坏帧 —— 因为固定行高下, 实时行的显隐**不改变布局**, 两者会同时可见:
          ① 实时行已清、历史还没接上 -> 最新一句凭空少一行(闪丢)
          ② 实时行既在历史末尾又在实时位 -> 最新一句**重复**(finalize 时必现)
        这正是"定稿瞬间屏上多一行、最新句画两遍"的成因。
        """
        self._set_items(list(items), (en, zh) if live_visible else None,
                        bool(live_visible), bulk=False)

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
        if c.get(which) == text and c.get(key_f) is font:
            return
        c[which] = text
        c[key_f] = font
        if font is not None and lbl.font() != font:
            lbl.setFont_(font)
        lbl.setStringValue_(text)
        # ⚠️ 只在**译文行**上查超限 —— 答案接管期间(`_verbatim`, 与 `_hold` 同时置位)
        #    这一列装的是**中文讲解**, 是另一种内容, 由 `_answer_view_rows` 自己保证
        #    每行放得下; 拿译文的档位去量它只会误报(实测: R8 那个超长词用例就报了一次)。
        if which == "zh" and text and not self._verbatim:
            self._note_overflow(lbl, text)

    def _note_overflow(self, lbl, text: str) -> None:
        """把「文本超出档位行数」从**静默裁切**变成**可观测**。

        ⚠️ 为什么要它: 档位行数(2/3/4/5)**来自一个闭样本** —— `overlay.py` 里对
        4797 句真实中文定稿按宽度取 p100。样本之外的更长文本会被 AppKit 在
        `maximumNumberOfLines` 处**无声**吃掉(不留省略号, 见 `overlay.py` 文件头实测)。
        这条**不改变渲染**, 只保证发生时有痕迹: 记数 + 首次打一行。

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
        if need <= self._zh_h + 0.5:
            return
        self._overflow_n += 1
        if not self._overflow_warned:
            self._overflow_warned = True
            print(f"⚠ transcript_view: 有译文超出档位行数({self._lines} 行 / 盒高 "
                  f"{self._zh_h:.0f}px, 实测需 {need:.0f}px) —— 超出部分会被**静默裁掉"
                  f"且不留省略号**。若某门课的译文普遍更长, 请调大 "
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
        y = level * self._row_h - self._roll_off
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
        a = self._roll_alpha if level < self._roll_fade_n else 1.0
        if s.get("alpha", 1.0) != a:
            s["zh"].setAlphaValue_(a); s["en"].setAlphaValue_(a)
            s["alpha"] = a

    # ---- 新行入列: 整块上滚一个行高 ----
    # 「定稿替换草稿」那一下: 实时行出现(或一句定稿进来)时, 老行原地上跳一个行高、
    # 新行凭空出现在最底下 —— 而草稿就在转录区正下方, 读起来是「草稿没了, 上面换了一句」。
    # 改成: 内容先**往下偏一个行高**(新行藏在视口底边之下, 也就是草稿的位置), 再按
    # motion.MOVE_S 的 ease-out 滑回原位 —— 新行**从草稿那里升上来**, 老行一起被推上去。
    # 位移的是整块, 不是各自补间(两行各动各的就是「字在打架」, 方案 §2)。
    # 减弱动态效果: 不位移, 最新那几行淡入(Apple HIG 的原话是「用淡入代替 x/y/z 轴的过渡」)。
    def _start_roll(self, n: int) -> None:
        if not self._animate_ok():
            self._end_roll()                # 没人推帧: 不起跑, 直接是终态
            return
        now = self._clock()
        if self._reduce_motion():
            self._end_roll()
            self._roll_fade_n, self._roll_alpha = n, 0.0
        else:
            # 动画中又来一行: 从**当前位置**接着走(老内容又被顶高一行, 所以起点加一个行高),
            # 不重置也不跳 —— 方案 §5.2 的选项 ①。
            # 封顶两个行高: 槽位池只比视口多备了 2 行(K = ceil(视口/行高)+2), 偏得更多
            # 顶上就会露出没有槽位的空白。一次来 3 句以上本来就罕见。
            self._roll_from = min(self._roll_off + n * self._row_h, 2.0 * self._row_h)
            self._roll_off = self._roll_from
        self._roll_t0 = now
        self._regeom()          # 留在原槽位的行也要按新偏移重摆(_paint 只摆新进来的)

    def _step_roll(self) -> None:
        e = motion.ease_out(motion.progress(self._roll_t0, self._clock(), motion.MOVE_S))
        if e >= 1.0:
            self._end_roll()
            return
        if self._roll_fade_n:
            self._roll_alpha = e
        else:
            self._roll_off = self._roll_from * (1.0 - e)
        self._regeom()

    def _end_roll(self) -> None:
        """直接落到终态(位移 0、全不透明)。动画自然结束、被打断、换内容都走这里 ——
        「终态精确」是硬判据: 停在半路会让字幕挡在错误的位置(方案 §3.2 ③)。"""
        was = self._roll_t0 is not None
        self._roll_t0 = None
        self._roll_from = self._roll_off = 0.0
        self._roll_fade_n, self._roll_alpha = 0, 1.0
        if was:
            self._regeom()

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
        if not s["hidden"]:
            s["zh"].setHidden_(True); s["en"].setHidden_(True)
            s["hidden"] = True
        s["level"] = None
        s["en_top"] = None
        if s.get("alpha", 1.0) != 1.0:             # 槽位会被改派给别的行, 别把半透明带过去
            s["zh"].setAlphaValue_(1.0); s["en"].setAlphaValue_(1.0)
            s["alpha"] = 1.0

    def _paint(self) -> None:
        clip = self._scroll.contentView()
        view_h = clip.bounds().size.height
        doc_w = clip.bounds().size.width
        self._ensure_pool(view_h)               # 视口可能因换屏变大, 池要跟上
        count = self._count()
        doc_h = max(count * self._row_h, view_h)
        grew = 0 if self._count_reset else count - self._painted_count
        self._painted_count, self._count_reset = count, False

        # 浏览保护: 未跟随时新追加的行会把老内容整体上移 k*row_h,
        # 把视口同步上移同样距离, 视线不动。必须回写 _expected_origin,
        # 否则下一个 tick 会把我们自己的位移误判成用户滚动。
        k = self._pending_appends
        if k and not self._follow:
            oy = clip.bounds().origin.y
            clip.scrollToPoint_((0.0, oy + k * self._row_h))
            self._expected_origin = clip.bounds().origin.y
        self._pending_appends = 0

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
                self._start_roll(grew)

        L0 = max(0, int(clip.bounds().origin.y // self._row_h))
        hi = min(L0 + len(self._slots) - 1, count - 1)

        # 保留仍在覆盖区间内的槽位; 其余的腾出来给新进入的 level。
        keep = {}
        free = []
        for j, s in enumerate(self._slots):
            lv = s["level"]
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
