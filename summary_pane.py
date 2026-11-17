"""悬浮窗右栏「实时总结」：一章一段（2026-11-17 Owen 定，宣传片那个样子：左字幕 · 分割线 · 右总结）。

两层，别混：
- **纯函数层**（`para_blocks` / `bold_segments`）：章节 dict → 要画的块。不碰 AppKit，单测直接调。
- **视图层**（`SummaryPane`）：一个 NSScrollView + 只读 NSTextView，吃纯函数层的输出画富文本。

数据契约（`chapter.Chapter.as_json`，para-summary 分支追加的字段）：
    {"id", "status": "interim"|"final", "title", "title_zh", "t0", "t1",
     "sentences": [{"en", "zh", ...}],                 # 旧字段，一直在
     "para": None | {"lead": {"en","zh","bold":[...],"bold_en":[...],"src":[...]},
                     "body": [同结构, ...], "terms": [...], "words": int}}
`para` 缺失 / 为 None（旧回包、旧 chapters.jsonl、模型没给）→ **退回 `sentences` 的中文拼成一段**，
不丢章（同 `chapter.parse_reply` 的 fail-open 纪律）。

版式依据：~/classlive-web-spike/research/R2-live-summary-findings.md §5.2
（第一句是结论 → 第一句全白、其余退半档；加粗只给关键词；临时版原地替换、不追加新块）。
"""
from __future__ import annotations

import re
import typing

#: 右栏宽度（pt）。打开时**面板往右长这么宽**，左边字幕栏宽度不动 ——
#: 660 宽的面板对半分，18pt 中文一行只剩 ~16 字，会折得很碎（2026-11-17 Claude 估算）。
PANE_W = 340.0
#: 分割线与两侧的间距。
GUTTER = 14.0
#: 正文右侧给浮层滚动条让出的宽度（2026-11-17 截图：行尾的字被滚动条压住）。
SCROLLER_ROOM = 12.0
TITLE_PT = 11.5
BODY_PT = 14.0
#: 结论句（lead）全白；依据句（body）退半档 —— 「第一句是结论」靠亮度区分，不靠色相（Owen：统一白色）。
LEAD_ALPHA = 1.0
BODY_ALPHA = 0.74
META_ALPHA = 0.50
INTERIM_ALPHA = 0.55
#: ❓ 标记那行比元信息亮一档 —— 那是**你自己**留的记号，回看时要一眼找得到。
MARK_ALPHA = 0.72
EMPTY_TEXT = "开课约 3 分钟后出第一段"


def bold_segments(text: str, bolds: typing.Iterable[str]) -> list[tuple[str, bool]]:
    """`text` 按 `bolds` 切成 `[(片段, 是否加粗), …]`。**纯函数。**

    - 只认**逐字出现**在 text 里的项（`chapter.parse_reply` 已经滤过一遍，这里再防一次，旧数据也安全）；
    - 多个项重叠时先到先得（按出现位置），不嵌套；
    - 空 text → `[]`；拼回去必须等于原文（单测钉住）。
    """
    if not text:
        return []
    spans: list[tuple[int, int]] = []
    for b in bolds or ():
        if not isinstance(b, str) or not b.strip():
            continue
        start = 0
        while True:
            i = text.find(b, start)
            if i < 0:
                break
            j = i + len(b)
            if not any(i < e and s < j for s, e in spans):
                spans.append((i, j))
                break                       # 每项只加粗第一次出现，避免一段里亮满
            start = j
    spans.sort()
    out: list[tuple[str, bool]] = []
    pos = 0
    for s, e in spans:
        if s > pos:
            out.append((text[pos:s], False))
        out.append((text[s:e], True))
        pos = e
    if pos < len(text):
        out.append((text[pos:], False))
    return out


_CJK_SPACE = re.compile(r"(?<=[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef])[ \t]+(?=[0-9A-Za-z])"
                        r"|(?<=[0-9A-Za-z%])[ \t]+(?=[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef])")


def _sent_zh(s: typing.Any) -> str:
    """一句的中文。⚠️ 去掉中文与数字/字母之间的空格：模型常写「花费 1000 美元」，
    折行时空格落到行首，左边缘就参差不齐（2026-11-17 截图）—— 中文排版本来也不加这个空格。"""
    if not isinstance(s, dict):
        return ""
    return _CJK_SPACE.sub("", str(s.get("zh") or s.get("en") or "").strip())


def para_blocks(chapters: typing.Mapping | typing.Iterable) -> list[dict]:
    """章节 → 右栏要画的块，按时间顺序。**纯函数。**

    每块：`{"id", "title", "t", "interim": bool,
            "lead": [(片段, 粗)], "body": [(片段, 粗)]}`
    - 标题用 `title_zh`，没有就 `title`；时间取 `t0` 的 HH:MM；
    - 有 `para` → lead / body 各自按 `bold` 切段；body 多句用空串直接相接（中文不加空格）；
    - 没 `para` → 第一句当 lead、其余当 body，都不加粗；
    - 一个字都没有的章（`sentences: []` 且无 para，= 点名 / 调试 / 闲聊）**不画**。
    """
    items = list(chapters.values()) if isinstance(chapters, typing.Mapping) else list(chapters or [])
    items = [c for c in items if isinstance(c, dict) and c.get("id") is not None]
    items.sort(key=lambda c: (str(c.get("t0") or ""), _id_key(c.get("id"))))
    out: list[dict] = []
    for c in items:
        p = c.get("para") if isinstance(c.get("para"), dict) else None
        lead_seg: list[tuple[str, bool]] = []
        body_seg: list[tuple[str, bool]] = []
        if p and isinstance(p.get("lead"), dict) and _sent_zh(p["lead"]):
            lead = p["lead"]
            lead_seg = bold_segments(_sent_zh(lead), [_CJK_SPACE.sub("", str(x)) for x in lead.get("bold") or []])
            for b in p.get("body") or []:
                body_seg += bold_segments(_sent_zh(b), [_CJK_SPACE.sub("", str(x)) for x in (b or {}).get("bold") or []]) if isinstance(b, dict) else []
        else:
            zs = [z for z in (_sent_zh(s) for s in c.get("sentences") or []) if z]
            if zs:
                lead_seg = [(zs[0], False)]
                body_seg = [("".join(zs[1:]), False)] if len(zs) > 1 else []
        if not lead_seg:
            continue
        out.append({
            "id": c.get("id"),
            "title": str(c.get("title_zh") or c.get("title") or "").strip(),
            "t": str(c.get("t0") or "")[:5],
            "interim": c.get("status") != "final",
            "lead": lead_seg,
            "body": body_seg,
            # 旧纲要的「⚠ 依赖板书或图」：任一句带 board 就在段尾提一句（不逐句标，段落里没有句界）
            "board": any(isinstance(s, dict) and s.get("flag") == "board"
                         for s in c.get("sentences") or []),
            "t1": str(c.get("t1") or ""),
            "t0": str(c.get("t0") or ""),
        })
    return out


def _hm(t: typing.Any) -> str:
    return str(t or "")[:5]


def pane_model(outline: typing.Mapping) -> dict:
    """整份 `_outline` → 右栏要画的东西。**纯函数。**（2026-11-17 Owen：纲要并进右栏）

    旧纲要（`overlay.build_outline_rows`）里除了章节，还有三样东西，这里一样不丢：
    - **课务**（`deadlines`）：置顶，最新的在上；中文优先（`zh`），没有退回原句 `quote`；
    - **断档**（`gaps`）：按时间插在章节之间 —— `failed` 是「这一窗内容没能生成」，不是离线；
    - **❓ 标记**（`marks`，`HH:MM:SS`）：落在某个已画章 `[t0, t1]` 里的挂到那章段尾；
      其余（本章还没出段 / 落在空章里）按时间插进时间线，不挂错章。

    返回 `{"n": 章段数, "deadlines": [{"text","t","changed"}],
           "items": [{"kind": "chapter", ...para_blocks 的块, "marks": [HH:MM]}
                     | {"kind": "gap", "t_from", "t_to", "why", "t"}
                     | {"kind": "mark", "t"}]}`，`items` 按时间排。
    """
    o = outline if isinstance(outline, typing.Mapping) else {}
    blocks = para_blocks(o.get("chapters") or {})
    deadlines = []
    for d in reversed(list(o.get("deadlines") or [])):
        if not isinstance(d, dict):
            continue
        text = str(d.get("zh") or d.get("quote") or "").strip()
        if text:
            deadlines.append({"text": text, "t": _hm(d.get("t")), "changed": bool(d.get("changed"))})
    timeline: list[tuple[str, int, dict]] = []
    for b in blocks:
        timeline.append((b["t0"], 0, {"kind": "chapter", **b, "marks": []}))
    for g in o.get("gaps") or []:
        if not isinstance(g, dict):
            continue
        why = "这一段内容没能生成" if g.get("state") == "failed" else "离线，这一段没有总结"
        timeline.append((str(g.get("t_from") or ""), 1, {
            "kind": "gap", "t": _hm(g.get("t_from")), "t_from": _hm(g.get("t_from")),
            "t_to": _hm(g.get("t_to")), "why": why}))
    for m in sorted(str(x) for x in (o.get("marks") or []) if x):
        home = next((it for _, _, it in timeline if it["kind"] == "chapter"
                     and it["t0"] <= m <= (it["t1"] or it["t0"])), None)
        if home is not None:
            if _hm(m) not in home["marks"]:            # 同一分钟按两次 ❓ 只写一次时间（否则「10:05、10:05」像出错）
                home["marks"].append(_hm(m))
        else:
            timeline.append((m, 2, {"kind": "mark", "t": _hm(m)}))
    timeline.sort(key=lambda x: (x[0], x[1]))
    return {"n": len(blocks), "deadlines": deadlines, "items": [it for _, _, it in timeline]}


def _id_key(v: typing.Any) -> tuple:
    return (0, v) if isinstance(v, (int, float)) else (1, str(v))


# ---------------------------------------------------------------- 视图层（AppKit）
class SummaryPane:
    """右栏本体。由 Overlay 持有；只有三个入口：`set_frame` / `update` / `set_visible`。

    ⚠️ **不读写 Overlay 的任何 `_answer_*` / `_outline_*` 状态** —— 和答案接管、纲要互不相踩。
    ⚠️ `update` 在主线程 `drain()` 里被调：只重建富文本，块数 ≤ 一节课几十章，成本 ~1 ms 级。
    """

    def __init__(self, parent_view, shadow, font_fn) -> None:
        from AppKit import NSColor, NSMakeRect, NSScrollView, NSTextView, NSView
        self._font = font_fn
        self._shadow = shadow
        self._sig = None
        self.divider = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 0, 0))
        self.divider.setWantsLayer_(True)
        self.divider.layer().setBackgroundColor_(
            NSColor.whiteColor().colorWithAlphaComponent_(0.16).CGColor())
        self.scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, PANE_W, 100))
        self.scroll.setDrawsBackground_(False)
        try:                                              # 无标题栏面板也会被自动加顶部内边距 → 关掉，标题与顶栏对齐
            self.scroll.setAutomaticallyAdjustsContentInsets_(False)
        except Exception:                                 # noqa: BLE001
            pass
        self.scroll.setHasVerticalScroller_(True)
        self.scroll.setAutohidesScrollers_(True)
        try:
            from AppKit import NSScrollerStyleOverlay
            self.scroll.setScrollerStyle_(NSScrollerStyleOverlay)
        except Exception:                                 # noqa: BLE001
            pass
        self.text = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, PANE_W, 100))
        self.text.setEditable_(False)
        self.text.setSelectable_(True)                    # 能选中复制；不能改
        self.text.setDrawsBackground_(False)
        self.text.setRichText_(True)
        self.text.setVerticallyResizable_(True)
        self.text.setHorizontallyResizable_(False)
        self.text.textContainer().setWidthTracksTextView_(True)
        self.text.textContainer().setLineFragmentPadding_(0.0)
        self.text.setTextContainerInset_((0.0, 4.0))
        self.scroll.setDocumentView_(self.text)
        # 顶部固定区：标题 + 课务。**不跟着滚** —— 课上人一直在底部看新段，
        # 课务要是放在滚动内容的最上面，等于没人看见（2026-11-17 截图：课务被滚出视野）。
        self.pin = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, PANE_W, 20))
        self.pin.setEditable_(False)
        self.pin.setSelectable_(True)
        self.pin.setDrawsBackground_(False)
        self.pin.setRichText_(True)
        self.pin.textContainer().setWidthTracksTextView_(True)
        self.pin.textContainer().setLineFragmentPadding_(0.0)
        self.pin.setTextContainerInset_((0.0, 4.0))
        self._frame = (0.0, 0.0, PANE_W, 100.0)
        self._rule_on = False
        parent_view.addSubview_(self.divider)
        parent_view.addSubview_(self.scroll)
        parent_view.addSubview_(self.pin)
        # 固定区与滚动区之间一根细线：不画的话滚上来的第一行像是课务的一部分（2026-11-17 截图）
        self.rule = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 0, 0))
        self.rule.setWantsLayer_(True)
        self.rule.layer().setBackgroundColor_(
            NSColor.whiteColor().colorWithAlphaComponent_(0.10).CGColor())
        parent_view.addSubview_(self.rule)
        self.set_visible(False)

    # -- 布局
    def set_frame(self, x: float, y: float, w: float, h: float) -> None:
        self._frame = (x, y, w, h)
        self._place()

    def _pin_height(self, w: float) -> float:
        if not self.pin.string():
            return 0.0
        self.pin.setFrameSize_((w, self.pin.frame().size.height))
        lm, tc = self.pin.layoutManager(), self.pin.textContainer()
        lm.ensureLayoutForTextContainer_(tc)
        return float(lm.usedRectForTextContainer_(tc).size.height) + 8.0 + 6.0   # 上下 inset 4+4，再空 6 跟正文隔开

    def _place(self) -> None:
        from AppKit import NSMakeRect
        x, y, w, h = self._frame
        iw = max(0.0, w - 2 * GUTTER)
        ph = min(self._pin_height(max(0.0, iw - SCROLLER_ROOM)), max(0.0, h * 0.45))  # 课务再多也只占上半截以内
        self.divider.setFrame_(NSMakeRect(x, y + 6, 0.5, max(0.0, h - 12)))
        self.pin.setFrame_(NSMakeRect(x + GUTTER, y + h - ph, max(0.0, iw - SCROLLER_ROOM), ph))  # 与正文同宽、右边缘对齐
        self.scroll.setFrame_(NSMakeRect(x + GUTTER, y, iw, max(0.0, h - ph)))
        self.rule.setFrame_(NSMakeRect(x + GUTTER, y + h - ph, iw, 0.5) if self._rule_on
                            else NSMakeRect(0, 0, 0, 0))
        self.text.setFrameSize_((max(0.0, iw - SCROLLER_ROOM), self.text.frame().size.height))

    def set_visible(self, on: bool) -> None:
        self.divider.setHidden_(not on)
        self.scroll.setHidden_(not on)
        self.pin.setHidden_(not on)
        self.rule.setHidden_(not on)

    # -- 内容
    def update(self, outline) -> None:
        """吃整份 `_outline`（章节 + 课务 + 断档 + ❓）。只给章节 dict 也行（探针 / 旧调用）。"""
        if isinstance(outline, typing.Mapping) and "chapters" in outline:
            model = pane_model(outline)
        else:
            model = pane_model({"chapters": outline or {}})
        sig = repr(model)
        if sig == self._sig:
            return                                        # 同样的内容不重排（不在读者眼皮底下动）
        self._sig = sig
        at_bottom = self._at_bottom()
        self._rule_on = bool(model["deadlines"])
        self.pin.textStorage().setAttributedString_(self._attr(model, "head"))
        self.text.textStorage().setAttributedString_(self._attr(model, "body"))
        self._place()
        # 读者本来就在底部 → 跟到底（新段 / 临时版原地变长 / 固定区多一条课务把滚动区压矮，都算）；
        # 在往上翻旧章 → 不拽他。⚠️ 只在「多了一块」时才跟不够：课务进来时滚动区变矮，底部会被顶出视野，
        # 下一次 `_at_bottom` 就判成「没在底部」，之后再也不跟（2026-11-17 探针 3b 截图抓到）。
        if at_bottom:
            self.text.scrollToEndOfDocument_(None)

    def _at_bottom(self) -> bool:
        try:
            clip = self.scroll.contentView()
            vis = clip.documentVisibleRect()
            doc_h = self.text.frame().size.height
            return vis.origin.y + vis.size.height >= doc_h - 24
        except Exception:                                 # noqa: BLE001
            return True

    def _attr(self, model: dict, part: str):
        """`part="head"`：固定区（标题 + 课务）；`"body"`：滚动区（时间线 / 空状态）。"""
        from AppKit import (NSColor, NSFontAttributeName, NSForegroundColorAttributeName,
                            NSMutableAttributedString, NSMutableParagraphStyle,
                            NSParagraphStyleAttributeName, NSShadowAttributeName,
                            NSAttributedString)
        white = NSColor.whiteColor()
        out = NSMutableAttributedString.alloc().init()

        def style(space_before: float, line_h: float):
            ps = NSMutableParagraphStyle.alloc().init()
            ps.setParagraphSpacingBefore_(space_before)
            ps.setLineHeightMultiple_(line_h)
            return ps

        def add(s: str, font, alpha: float, ps):
            out.appendAttributedString_(NSAttributedString.alloc().initWithString_attributes_(s, {
                NSFontAttributeName: font,
                NSForegroundColorAttributeName: white.colorWithAlphaComponent_(alpha),
                NSParagraphStyleAttributeName: ps,
                NSShadowAttributeName: self._shadow,
            }))

        def line(space_before: float, line_h: float = 1.0):
            ps = style(space_before, line_h)
            if out.length():                              # 第一段前不垫空行（顶上就是固定区）
                add("\n", self._font(TITLE_PT), META_ALPHA, ps)
            return ps

        if part == "head":
            return self._attr_head(model, out, add, line, style)
        return self._attr_body(model, out, add, line, style)

    def _attr_head(self, model, out, add, line, style):
        n = model["n"]
        add(f"实时总结 · {n} 段" if n else "实时总结", self._font(TITLE_PT, 0.5), META_ALPHA, style(0.0, 1.0))

        # ① 课务置顶 —— 课上最怕漏的就是这个，不跟着时间线沉下去（旧纲要 ① 同一条）
        if model["deadlines"]:
            ps = line(12.0)
            add("课务", self._font(TITLE_PT - 0.5, 0.5), META_ALPHA, ps)
            for d in model["deadlines"]:
                ps = line(5.0, 1.25)
                add(d["text"], self._font(BODY_PT - 1.0, 0.3), 0.92, ps)
                meta = "  ·  ".join(x for x in (d["t"], "待确认", "已改期" if d["changed"] else "") if x)
                add(f"   {meta}", self._font(TITLE_PT - 1.0), META_ALPHA, ps)

        return out

    def _attr_body(self, model, out, add, line, style):
        if not model["items"]:
            ps = line(6.0, 1.25)
            add(EMPTY_TEXT, self._font(BODY_PT), INTERIM_ALPHA, ps)
            return out

        for it in model["items"]:
            if it["kind"] == "gap":
                ps = line(14.0)
                span = it["t_from"] if it["t_from"] == it["t_to"] else f"{it['t_from']}–{it['t_to']}"
                add(f"{span}  {it['why']}", self._font(TITLE_PT - 0.5),
                    META_ALPHA, ps)
                continue
            if it["kind"] == "mark":
                ps = line(10.0)
                add(f"？ {it['t']}  你在这里标记过", self._font(TITLE_PT - 0.5, 0.3), MARK_ALPHA, ps)
                continue
            b = it
            title_ps, body_ps = line(16.0), style(5.0, 1.3)
            meta = f"{b['t']}  ·  整理中" if b["interim"] else b["t"]
            add(b["title"] or "（无标题）", self._font(TITLE_PT + 1.5, 0.4),
                INTERIM_ALPHA if b["interim"] else 0.92, title_ps)
            add(f"   {meta}", self._font(TITLE_PT - 0.5), META_ALPHA, title_ps)
            add("\n", self._font(BODY_PT), 1.0, body_ps)
            lead_a = INTERIM_ALPHA if b["interim"] else LEAD_ALPHA
            body_a = INTERIM_ALPHA if b["interim"] else BODY_ALPHA
            # 结论句与依据句**同字重**，只差亮度（全白 vs 退半档）；加粗只给关键词 ——
            # 结论整句加粗的话，关键词的粗就被稀释没了（2026-11-17 截图看过）。
            for seg, bold in b["lead"]:
                add(seg, self._font(BODY_PT, 0.40 if bold else 0.0), lead_a, body_ps)
            for seg, bold in b["body"]:
                add(seg, self._font(BODY_PT, 0.40 if bold else 0.0), body_a if not bold else min(1.0, body_a + 0.18),
                    body_ps)
            # 段尾附注：板书提示、❓ 标记 —— 都是小字，挂在这一段下面，不另起一块
            notes = []
            if b.get("board"):
                notes.append(("部分内容在板书或图上，转录不完整", META_ALPHA, 0.0))
            if b.get("marks"):
                notes.append((f"？ 你在 {'、'.join(b['marks'])} 标记过", MARK_ALPHA, 0.3))
            for i, (txt, alpha, w) in enumerate(notes):
                ps = line(6.0 if i == 0 else 2.0)
                add(txt, self._font(TITLE_PT - 0.5, w), alpha, ps)
        return out
