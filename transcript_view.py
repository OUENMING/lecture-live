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
                 on_follow_change=None):
        ScrollCls, DocCls = _view_classes()
        self._parent = parent
        self._make_label = make_label
        self._font_zh = font_zh
        self._font_en = font_en
        self._font_en_big = font_en_big
        self._width = width
        self._pad = pad
        self._en_h, self._gap, self._zh_h, self._row_h = en_h, gap, zh_h, row_h
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
        self._verbatim = False      # 行 = (大字, 小字) 原样, 不做中英对调(见 set_rows_verbatim)

        self._dirty = False
        self._urgent = False
        self._last_flush = 0.0
        self._doc_h = -1.0
        self._doc_w = -1.0
        self._collapsed = True

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
        for j in range(int(math.ceil(max_scroll_h / row_h)) + 2):
            self._add_slot()

    def _add_slot(self) -> None:
        zh = self._make_label(18.0, self._font_zh[1], 2, self._font_zh[2])
        en = self._make_label(11.0, self._font_en[1], 1, self._font_en[2])
        zh.setHidden_(True); en.setHidden_(True)
        self._doc.addSubview_(zh)
        self._doc.addSubview_(en)
        self._slots.append({"zh": zh, "en": en, "level": None, "hidden": True,
                            "en_top": None})
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

    def set_row_metrics(self, zh_h: float, row_h: float, lines: int) -> None:
        """行尺寸随窗口宽度变(窄窗里中文要更多行才不吞字)。

        ⚠️ 变的只是"等高的那个值", **所有行依然等高** —— 回收池依赖的不变量是
        "所有行等高", 不是"高 == 70", 所以换一组统一尺寸是安全的。
        池的容量 K=ceil(视口高/行高)+2 会跟着变小, 多余的槽位被 `_paint` 藏起来,
        不够时 `_ensure_pool` 会补 —— 两个方向都不用额外处理。

        槽位必须**重排**: 清掉每个槽位的 level, `_paint` 会把它们按新行高重新放置。
        文字缓存不清(文本没变, 变的只有 frame), 所以 `_set_text` 会照旧跳过。
        """
        if abs(float(row_h) - self._row_h) < 0.5:
            return
        self._zh_h = float(zh_h)
        self._row_h = float(row_h)
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
        self._collapsed = collapsed
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
        ③惯性自然处理: 每个 tick 都在动就持续刷新计时, 停下后计时才开始。"""
        clip = self._scroll.contentView()
        cur = clip.bounds().origin.y
        if abs(cur - self._expected_origin) > MOVE_EPS:
            self._last_user_t = time.monotonic()
            if self._follow and cur > BOTTOM_EPS:
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
            return zh, en, True
        # 翻译关闭/失败时 zh 为空: 把英文提到大字行, 否则大字行空白
        return en, "", False

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

    def _place(self, j: int, level: int, count: int, doc_w: float) -> None:
        """把第 level 层(＝第 count-1-level 行)摆进槽位 j。"""
        big, sub, is_zh = self._row_texts(count - 1 - level)
        self._layout(j, level, doc_w, big, sub, is_zh)

    def _layout(self, j: int, level: int, doc_w: float,
                big: str, sub: str, is_zh: bool) -> None:
        """摆一行的两个标签: 几何 + 文本。

        竖排顺序**逐行判定** —— 同屏会混着双语行和答案行, 判据见 EN_LINE_ON_TOP。"""
        s = self._slots[j]
        y = level * self._row_h
        w = doc_w
        en_top = EN_LINE_ON_TOP and is_zh
        if en_top:                                   # 英文小字在上, 中文大字在下
            s["en"].setFrame_(((0.0, y + self._zh_h + self._gap), (w, self._en_h)))
            s["zh"].setFrame_(((0.0, y), (w, self._zh_h)))
        else:                                        # 大字在上
            s["en"].setFrame_(((0.0, y), (w, self._en_h)))
            s["zh"].setFrame_(((0.0, y + self._en_h + self._gap), (w, self._zh_h)))
        self._set_text(j, "zh", big, self._font_zh[0] if is_zh else self._font_en_big[0])
        self._set_text(j, "en", sub, self._font_en[0])
        if s["hidden"]:
            s["zh"].setHidden_(False); s["en"].setHidden_(False)
            s["hidden"] = False
        s["level"] = level
        s["en_top"] = en_top

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

    def _paint(self) -> None:
        clip = self._scroll.contentView()
        view_h = clip.bounds().size.height
        doc_w = clip.bounds().size.width
        self._ensure_pool(view_h)               # 视口可能因换屏变大, 池要跟上
        count = self._count()
        doc_h = max(count * self._row_h, view_h)

        # 浏览保护: 未跟随时新追加的行会把老内容整体上移 k*row_h,
        # 把视口同步上移同样距离, 视线不动。必须回写 _expected_origin,
        # 否则下一个 tick 会把我们自己的位移误判成用户滚动。
        k = self._pending_appends
        if k and not self._follow:
            oy = clip.bounds().origin.y
            clip.scrollToPoint_((0.0, oy + k * self._row_h))
            self._expected_origin = clip.bounds().origin.y
        self._pending_appends = 0

        if abs(self._doc_h - doc_h) > 0.5 or abs(self._doc_w - doc_w) > 0.5:
            self._doc_h, self._doc_w = doc_h, doc_w
            self._doc.setFrame_(((0.0, 0.0), (doc_w, doc_h)))

        if self._follow:
            # 跟随态: 钉在底部(origin 0), 内容从下往上铺
            if clip.bounds().origin.y != 0.0:
                clip.scrollToPoint_((0.0, 0.0))
            self._expected_origin = clip.bounds().origin.y

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
        for lv, j in keep.items():
            big, sub, is_zh = self._row_texts(count - 1 - lv)
            # 竖排顺序会随内容变(实时行从"还没译文"变成"有译文")。变了必须重摆:
            # _place 只在槽位**新进入**时调用, 光刷文本会把 frame 留在旧顺序上,
            # 同屏于是出现"有的英文在上、有的在下"。
            if self._slots[j].get("en_top") != (EN_LINE_ON_TOP and is_zh):
                self._layout(j, lv, doc_w, big, sub, is_zh)
                continue
            self._set_text(j, "zh", big,
                           self._font_zh[0] if is_zh else self._font_en_big[0])
            self._set_text(j, "en", sub, self._font_en[0])
