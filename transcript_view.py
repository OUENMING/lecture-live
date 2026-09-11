"""NSScrollView 支撑的转录区: 聊天式滚动 + 视图回收 + 跟随状态机。

坐标约定(关键)
--------------
文档视图**不翻转**(`isFlipped` 保持 False), 所以 `origin.y = 0` 就是**底部**。

为什么不用翻转坐标: 翻转后 y=0 在顶部, 要"贴底"就得把 origin 设成
`docH - clipH`; 内容比视口短时该值是**负数**, AppKit 会 clamp 到 0 并把内容
顶对齐 —— 空白跑到内容**下方**, 正是聊天布局最怕的。不翻转时静止位置恰好是 0,
clamp 边界与静止位置重合, clamp 永远在帮你; 内容短于视口时也无需 padding 技巧。

行几何: `level 0` = 最新句, 在 y=0; `level L` 在 `y = L * row_h`。
一行内部从下到上是 英文小字 → 中文大字(与既有阅读流一致)。

为什么固定行高是命门
--------------------
固定 `row_h` 让"滚动偏移 → 行索引"变成 O(1) 的除法。可变行高会逼出前缀和
布局缓存, 并推翻整套回收方案。所以主行封顶 2 行 + 尾部省略号, 不做自适应。

回收
----
池里 K 个槽位常驻, 每个槽位记住自己当前的 `level`。滚动一行时只有**一个**
槽位的 level 会离开覆盖区间、也只有**一个**新 level 进入, 于是只搬动一格。
文案更新则靠 `_set_text` 的缓存按键跳过 —— 只有实时行在流式期间会真的写。
"""

from __future__ import annotations
import math
import time

IDLE_S = 6.0          # 闲置多久才自动回底(仅在离开底部后又有新句定稿时)
BOTTOM_EPS = 3.0      # origin.y <= 此值即视为在底部(橡皮筋的负值也算)
MOVE_EPS = 1.5        # 与期望 origin 相差超过此值 -> 判定为用户/惯性滚动
FLUSH_DT = 0.016      # 渲染合并闸门 = 一帧

_ScrollCls = None
_DocCls = None


def _view_classes():
    """定义两个 ObjC 视图子类。⚠️ 只能定义一次(重复定义会报 override 错)。"""
    global _ScrollCls, _DocCls
    if _ScrollCls is None:
        import objc
        from AppKit import NSScrollView

        class _TranscriptScroll(NSScrollView):
            def scrollWheel_(self, event):        # noqa: N802
                # 收回态吞掉滚轮: 不改 origin、不起橡皮筋。
                # 不用 setIgnoresMouseEvents_ —— 那会连带杀掉内容区的拖拽移动,
                # 且和悬浮窗"鼠标穿透"同类的单向死锁。
                # (NSScrollView 没有 setScrollEnabled_, 所以开关放在自己的标志位上。
                #  PyObjC 的 ObjC 子类必须用 objc.super, 内置 super() 不认。)
                if getattr(self, "_scroll_enabled", True):
                    objc.super(_TranscriptScroll, self).scrollWheel_(event)

        _ScrollCls = _TranscriptScroll
    if _DocCls is None:
        from AppKit import NSView

        class _TranscriptDoc(NSView):
            def mouseDown_(self, event):          # noqa: N802
                # 整个转录区都是拖拽面。不赌 AppKit"背景拖拽"的启发式认不认
                # NSScrollView; 滚轮和按键拖拽是两条独立事件流, 互不干扰。
                win = self.window()
                if win is not None:
                    win.performWindowDragWithEvent_(event)

        _DocCls = _TranscriptDoc
    return _ScrollCls, _DocCls


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
        self._slots.append({"zh": zh, "en": en, "level": None, "hidden": True})
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
        n = len(items)
        if n > len(self._items):
            self._pending_appends += n - len(self._items)
            if not self._follow:
                # "只有真的有新内容才回底": 新句子(定稿)才算新内容。
                # 流式增量不算 —— 行高固定, 实时行增长不会移动文档。
                self._new_since_leave = True
        self._items = list(items)
        self._live = (en, zh) if live_visible else None
        self._live_visible = bool(live_visible)
        self.mark_dirty(urgent=True)

    def set_items(self, items) -> None:
        self.set_content(items, *(self._live or ("", "")), self._live_visible)

    def set_live(self, en: str, zh: str, visible: bool) -> None:
        self.set_content(self._items, en, zh, visible)

    def set_frame(self, y: float, h: float) -> None:
        self._scroll.setFrame_(((self._pad, y), (self._width - 2 * self._pad, h)))
        self.mark_dirty(urgent=True)

    def set_collapsed(self, collapsed: bool) -> None:
        from AppKit import NSScrollElasticityAutomatic
        self._collapsed = collapsed
        # NSScrollView 没有 setScrollEnabled_; 收回态的不可滚性由
        # _TranscriptScroll.scrollWheel_ 的开标志位 + 关掉橡皮筋共同保证。
        self._scroll._scroll_enabled = not collapsed
        self._scroll.setHasVerticalScroller_(not collapsed)
        self._scroll.setVerticalScrollElasticity_(
            0 if collapsed else NSScrollElasticityAutomatic)
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
            if cur <= BOTTOM_EPS:               # 用户自己滚回底部
                self._follow = True
                self._new_since_leave = False
                self._on_follow_change(True)
            elif (self._new_since_leave
                  and time.monotonic() - self._last_user_t >= IDLE_S):
                self.scroll_to_bottom()

    # ---- 渲染 ----
    def _row_texts(self, r: int):
        """r 行 → (大字文本, 小字文本, 大字是否中文)。"""
        if r < len(self._items):
            en, zh = self._items[r]
        else:
            en, zh = self._live or ("", "")
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
        s = self._slots[j]
        r = count - 1 - level
        big, sub, is_zh = self._row_texts(r)
        y = level * self._row_h
        w = doc_w
        s["en"].setFrame_(((0.0, y), (w, self._en_h)))
        s["zh"].setFrame_(((0.0, y + self._en_h + self._gap), (w, self._zh_h)))
        self._set_text(j, "zh", big, self._font_zh[0] if is_zh else self._font_en_big[0])
        self._set_text(j, "en", sub, self._font_en[0])
        if s["hidden"]:
            s["zh"].setHidden_(False); s["en"].setHidden_(False)
            s["hidden"] = False
        s["level"] = level

    def _hide(self, j: int) -> None:
        s = self._slots[j]
        if s["zh"].stringValue():
            s["zh"].setStringValue_(""); s["en"].setStringValue_("")
        self._cache[j]["zh"] = ""; self._cache[j]["en"] = ""
        if not s["hidden"]:
            s["zh"].setHidden_(True); s["en"].setHidden_(True)
            s["hidden"] = True
        s["level"] = None

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
            self._set_text(j, "zh", big,
                           self._font_zh[0] if is_zh else self._font_en_big[0])
            self._set_text(j, "en", sub, self._font_en[0])
