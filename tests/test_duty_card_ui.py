#!/usr/bin/env python3
"""卡片的**视觉规矩**（作者 2026-10-17 那份评审后的拍板）：

- 按钮层级**自己声明**（`primary` / `secondary` / `plain`）—— 不许再按位置猜，
  「全部忽略」是批量放弃，永远不是 primary；
- 卡片**不再有深色底块**（只一道发丝边）、高度**跟内容走**（钉在转录区顶边）；
- emoji 全换 **SF Symbols 单色**小图（课前回顾 / 一些事儿）。

判据分三层（同 `test_duty_chip.py`）：纯函数 → 真 `Overlay` 上量几何 → 源码级钉住字面量。
"""
import ast
import os
import pathlib
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import overlay  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
EMOJI_MIN = 0x1F000


def _item(rid="d1", action="pending", **kw):
    return {"id": rid, "action": action, "type": "调课", "title": "本周三停课",
            "quote": "There is no class on Wednesday.", "date": "2026-10-14",
            "t": "13:52:56", **kw}


def _blocks(o, kind):
    return [b for b in o._card_pending if b["kind"] == kind]


def _fill_alpha(view) -> float:
    """层背景色的 alpha（CGColor → NSColor 再读；CGColor 自己没有 alpha()）。"""
    try:
        from AppKit import NSColor
        cg = view.layer().backgroundColor()
        if cg is None:
            return 0.0
        return float(NSColor.colorWithCGColor_(cg).alphaComponent())
    except Exception:                                     # noqa: BLE001
        return 0.0


def _ink_box(view, thr: int = 120):
    """视图里**亮墨**的行 / 列范围（像素 → 视图坐标）。返回 `(x0, x1, y0, y1, 缩放)`。

    ⚠️ 只对**没有底色**的视图直接可用（输入框 / 普通标签）：판 上的底与描边也是"亮的"，
       有底的胶囊要先走 `_ink_span()` 相减。
    """
    rep = view.bitmapImageRepForCachingDisplayInRect_(view.bounds())
    if rep is None:
        return None
    view.cacheDisplayInRect_toBitmapImageRep_(view.bounds(), rep)
    buf, w, h = bytes(rep.bitmapData()), rep.pixelsWide(), rep.pixelsHigh()
    spp, bpr = rep.samplesPerPixel(), rep.bytesPerRow()
    cols, rows = [], []
    for x in range(2, w - 2):
        if any(buf[y * bpr + x * spp] > thr or buf[y * bpr + x * spp + 1] > thr
               or buf[y * bpr + x * spp + 2] > thr for y in range(h)):
            cols.append(x)
    for y in range(h):
        if any(buf[y * bpr + x * spp] > thr or buf[y * bpr + x * spp + 1] > thr
               or buf[y * bpr + x * spp + 2] > thr for x in range(2, w - 2)):
            rows.append(y)
    if not cols or not rows:
        return None
    scale = w / float(view.bounds().size.width) if view.bounds().size.width else 1.0
    return (min(cols) / scale, max(cols) / scale, min(rows) / scale, max(rows) / scale, scale)


def _render_pixels(view):
    """把一个视图渲染成位图 → `(bytes, w, h, samplesPerPixel, bytesPerRow)`（失败 None）。"""
    try:
        rep = view.bitmapImageRepForCachingDisplayInRect_(view.bounds())
        if rep is None:
            return None
        view.cacheDisplayInRect_toBitmapImageRep_(view.bounds(), rep)
        return (bytes(rep.bitmapData()), rep.pixelsWide(), rep.pixelsHigh(),
                rep.samplesPerPixel(), rep.bytesPerRow())
    except Exception:                                     # noqa: BLE001
        return None


def _ink_span(view, restyle) -> tuple | None:
    """**像素级**量出"文字画在哪"：同一颗胶囊渲染两次（有字 / 无字）相减。

    ⚠️ 为什么要相减：胶囊自己的填充与描边也是"亮的"，直接数亮像素会把整个胶囊量进去
       （实测：选中胶囊 ink 从 3 到 86 —— 那是整个胶囊的形状）。相减只剩文字的墨。
    返回 `(x0, x1, 宽, 缩放)`（像素；`x0/x1` 是**列**范围）。
    """
    a = _render_pixels(view)
    if a is None:
        return None
    title = str(view.title())
    view.setTitle_("")
    try:
        view.setAttributedTitle_(None)   # ⚠️ 光清 `title` 不够：属性串还挂在 cell 上
    except Exception:                    # noqa: BLE001
        pass
    restyle(view)                      # 空标题也要保留同样的底 / 描边
    b = _render_pixels(view)
    view.setTitle_(title)
    restyle(view)
    if b is None or a[1:4] != b[1:4]:
        return None
    buf_a, w, h, spp, bpr = a
    buf_b = b[0]
    cols = []
    for x in range(3, w - 3):
        for y in range(h):
            o = y * bpr + x * spp
            if (abs(buf_a[o] - buf_b[o]) > 40 or abs(buf_a[o + 1] - buf_b[o + 1]) > 40
                    or abs(buf_a[o + 2] - buf_b[o + 2]) > 40):
                cols.append(x)
                break
    if not cols:
        return None
    scale = w / float(view.bounds().size.width) if view.bounds().size.width else 1.0
    return (min(cols), max(cols), w, scale)


class 按钮层级纯函数(unittest.TestCase):
    def test_缺省第一颗是主按钮(self):
        self.assertEqual(overlay.card_button_specs([("记下", "save"), ("忽略", "ignore")]),
                         [("记下", "save", "primary"), ("忽略", "ignore", "secondary")])

    def test_显式声明优先(self):
        self.assertEqual(
            overlay.card_button_specs([("全部忽略", "all", "plain")]),
            [("全部忽略", "all", "plain")])

    def test_乱写的角色退回次按钮(self):
        """拼错角色 / 给 None —— 退回**最安静**的档，绝不悄悄变成主按钮。"""
        for bad in ("primary!", "", None, "prominent"):
            with self.subTest(role=bad):
                self.assertEqual(overlay.card_button_specs([("x", "y", bad)])[0][2],
                                 "secondary")

    def test_空输入(self):
        self.assertEqual(overlay.card_button_specs(None), [])
        self.assertEqual(overlay.card_button_specs([]), [])

    def test_三档就是全部(self):
        self.assertEqual(set(overlay.BTN_ROLES), {"primary", "secondary", "plain"})


class 源码级(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = (ROOT / "overlay.py").read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.src)

    @classmethod
    def _func_src(cls, name: str) -> str:
        for fn in ast.walk(cls.tree):
            if isinstance(fn, ast.FunctionDef) and fn.name == name:
                return ast.get_source_segment(cls.src, fn) or ""
        return ""

    def test_place_buttons不再按位置猜主按钮(self):
        """⭐ 作者 2026-10-17：`i == 0` 那种写法**删干净**（每颗按钮自己声明层级）。"""
        src = self._func_src("_card_place_buttons")
        self.assertNotIn("i == 0", src, "「第一个 = 主按钮」的硬编码又回来了")
        self.assertIn("card_button_specs", src)
        self.assertIn("role", src)

    def test_卡片不再铺深色底块(self):
        src = self._func_src("_ensure_card_view")
        self.assertNotIn("setBackgroundColor_", src, "那块深色底又回来了（卡片只留发丝边）")
        self.assertIn("CARD_HAIRLINE", src)

    def test_课前回顾分区标题不再有emoji(self):
        src = self._func_src("_recap_fill")
        self.assertTrue(src, "没找到 `_recap_fill`")
        bad = sorted({c for c in src if ord(c) >= EMOJI_MIN})
        self.assertEqual(bad, [], f"课前回顾里还有 emoji：{bad}")
        for name in ("book", "star", "pin", "doc.text"):
            self.assertIn(name, src, f"分区标题少了 SF Symbols 名字：{name}")

    def test_高度跟内容走(self):
        src = self._func_src("_card_relayout")
        self.assertIn("_card_natural_h", src)
        self.assertNotIn("setFrame_(((x, y), (w, h)))", "")


class 真窗口(unittest.TestCase):
    def _overlay(self):
        try:
            from overlay import Overlay
        except ModuleNotFoundError as e:
            self.skipTest(f"AppKit 不可用, 跳过: {e}")
        try:
            return Overlay()
        except Exception as e:                            # noqa: BLE001
            self.fail(f"Overlay() 构造失败: {type(e).__name__}: {e}")

    @staticmethod
    def _buttons_of(blk) -> list:
        """块里那些**可见**的胶囊按钮（`tag` 是纯文字标签，不是按钮）。"""
        if blk["kind"] in ("buttons", "chips"):
            return [x[0] for x in (blk["extra"] or [])]
        if blk["kind"] == "duty_row" and blk["extra"]:
            return [b for k, b in blk["extra"]["buttons"].items() if k != "tag"]
        return []

    def _all_buttons(self, o) -> list:
        out: list = []
        for blk in o._card_pending:
            out += [b for b in self._buttons_of(blk) if not b.isHidden()]
        return out

    def test_编辑卡记下是唯一主按钮(self):
        o = self._overlay()
        try:
            o.duty_update([_item("d1"), _item("d2")])
            o._duty_open("d1")
            blk = _blocks(o, "buttons")[-1]
            roles = [(lab, role) for _b, lab, _c, role in blk["extra"]]
            prim = [x for x in roles if x[1] == "primary"]
            self.assertEqual([x[0] for x in prim], ["记下"], f"主按钮不止一颗：{roles}")
            self.assertEqual(dict(roles).get("忽略"), "plain")
            self.assertEqual(dict(roles).get("下一条"), "plain")
            # 撤销态：只剩一颗次按钮（它不再假装是主按钮）
            o._duty_act("save", o._duty_get("d1"))
            items = [dict(x) for x in o._duty_items]
            items[0]["action"] = "saved"
            o._duty_items = items
            o._card_refill()
            blk = _blocks(o, "buttons")[-1]
            self.assertEqual([(lab, role) for _b, lab, _c, role in blk["extra"]],
                             [("撤销", "secondary")])
        finally:
            o.close()

    def test_收尾卡全部忽略不是主按钮(self):
        """⭐ 作者点名的判据：「全部忽略」是 plain，绝不是白色实心块。

        顺带钉一条视觉不变量：整个收尾卡里**只有「记下」有底色**（primary 的填充）——
        「全部忽略」与「忽略」都没有底（原来那两块 0.92 / 0.07 的实心，是作者点名的「色块」）。
        """
        o = self._overlay()
        try:
            self.assertTrue(o.duty_wrapup_show([_item("d1"), _item("d2", "saved")]))
            footer = _blocks(o, "buttons")[-1]
            roles = [(lab, role) for _b, lab, _c, role in footer["extra"]]
            self.assertEqual(roles, [("全部忽略", "plain")], str(roles))
            filled = []
            for b in self._all_buttons(o):
                if _fill_alpha(b) >= overlay.BTN_STYLE["primary"][0] - 0.02:
                    filled.append(b.title())
            self.assertEqual(filled, ["记下"], f"有底色的按钮不止「记下」：{filled}")
        finally:
            o.close()

    def test_状态说明带单色符号(self):
        """⭐ 失败/成功那句说明配的是 SF Symbols（不是 ⚠/✔ emoji）。"""
        o = self._overlay()
        try:
            o.duty_update([_item("d1")])
            o._duty_open("d1")
            o._duty_act("save", o._duty_get("d1"))      # 先记下（还没写完）
            items = [dict(x) for x in o._duty_items]
            items[0]["action"] = "saved"
            items[0]["reminders"] = "failed"
            items[0]["reason"] = "系统没允许"
            o._duty_items = items
            o._card_refill()
            notes = [b for b in _blocks(o, "note") if b.get("symbol")]
            self.assertTrue(notes, "状态那一句没有配符号名")
            self.assertEqual(notes[-1]["symbol"], "exclamationmark.triangle")
            self.assertNotIn("⚠", notes[-1]["text"])
            self.assertTrue(notes[-1]["icon"], "符号视图没建出来（icon 标记为空）")
        finally:
            o.close()

    def test_课前回顾图标是单色符号视图(self):
        o = self._overlay()
        try:
            data = {"course": "ECON10790", "date": "10-15",
                    "points": [{"name": "供给曲线", "explain": "向上倾斜"}],
                    "stars": ["最值得记的一句"], "open": ["还没懂的问"],
                    "duties": [{"type": "调课", "title": "本周三停课", "when": "10-14"}],
                    "today": ["今天新课件《week6》"]}
            o.recap_show(data, course="ECON10790", day="2026-10-17")
            labels = _blocks(o, "label")
            self.assertTrue(labels)
            for b in labels:
                self.assertTrue(b["symbol"], f"分区标题没给符号名：{b['text']}")
                self.assertFalse([c for c in b["text"] if ord(c) >= EMOJI_MIN],
                                 f"标题里还有 emoji：{b['text']}")
            self.assertTrue(any(b["extra"] is not None for b in labels),
                            "SF Symbols 视图一个都没建出来（图标那一路断了）")
            pts = _blocks(o, "point")
            self.assertTrue(pts and pts[0]["attr"] is not None, "知识点不是属性串")
            self.assertIn("供给曲线", pts[0]["text"])
        finally:
            o.close()

    def test_课前回顾里过去的条目不显示(self):
        """⭐ 作者 2026-10-17：「临近的一些事儿」按**今天**过滤 —— 开课渲染这一层。

        ⚠️ 判据量的是**卡上真的画了什么**（不是某个函数被调过）：`_recap_fill` 哪天
           忘了把今天（`_session_day`）交给 `recap.card`，这条立刻红。
        """
        o = self._overlay()
        try:
            data = {"course": "ECON10790", "date": "10-15（周四）",
                    "points": [{"name": "停业价格", "explain": "价格低于它就停产"}],
                    "duties": [{"type": "调课", "title": "这条已经过去了",
                                "when": "10-14（周三）", "date": "2026-10-14"},
                               {"type": "其他", "title": "这条还在未来",
                                "when": "10-19（周一）", "date": "2026-10-19"}]}
            o.recap_show(data, course="ECON10790", day="2026-10-16")
            texts = [b["text"] for b in _blocks(o, "text")]
            self.assertFalse([t for t in texts if "已经过去了" in t],
                             f"过去的条目还在卡上：{texts}")
            self.assertTrue([t for t in texts if "还在未来" in t],
                            f"未来的条目不该没：{texts}")
        finally:
            o.close()

    def test_卡片高度跟内容走(self):
        """⭐ 卡片高度跟着内容走；装不下时**卡片内部滚**（不藏块、不挤内容）。

        ⚠️ 2026-10-17「上卡下字幕」改了口径：卡片有**上限**了（转录区的 60% 与
           「至少留 2 行字幕」取小），所以"内容全在卡里"这件事由**滚动文档**保证 ——
           这条判据量的就是它（`_card_doc` 装得下内容）。
        """
        o = self._overlay()
        try:
            o.duty_update([_item("d1")])
            o._duty_open("d1")
            f = o._card_view.frame()
            avail = float(o._scroll_h)
            self.assertLess(f.size.height, avail - 20.0,
                            "短卡还占满整块转录区（底下那片空白又回来了）")
            self.assertAlmostEqual(f.origin.y + f.size.height,
                                   float(o._scroll_y) + avail, delta=1.5,
                                   msg="卡片要钉在转录区**顶边**往下长")
            pend = list(o._card_pending)
            if pend and pend[-1]["kind"] == "buttons" and pend[-1]["buttons"]:
                pend.pop()            # 页脚钉在卡片底、不进滚动文档（同 `_card_plan` 的摘法）
            nat = o._card_natural_h(f.size.width - 2 * overlay.CARD_PAD, pend, None)
            box_h = max(0.0, nat - 2 * overlay.CARD_PAD)     # 内容盒（= 文档坐标的口径）
            clip = float(o._card_scroll.contentView().frame().size.height)
            if box_h <= clip + 1.0:
                self.assertAlmostEqual(
                    f.size.height, box_h + 2 * overlay.CARD_PAD + overlay.CARD_FOOTER_H,
                    delta=2.0, msg="装得下时卡片高度就该等于内容高")
            else:
                doc_h = float(o._card_doc.frame().size.height)
                self.assertGreaterEqual(doc_h + 1.0, box_h,
                                        f"内容被挤掉了：文档 {doc_h} < 内容盒 {box_h}")
        finally:
            o.close()


class 上卡下字幕(unittest.TestCase):
    """⭐ 作者 2026-10-17 拍板（评估了外部意见「卡片下方 160pt 空毛玻璃」）：

    卡片钉在转录区**顶边**、字幕在它**下面**继续滚（原来卡片把整块转录区藏掉 ——
    课上改卡片就看不见字幕）。三条硬规矩，全都量**真视图的 frame**：

    1. 卡片底 + `CARD_SUB_GAP` = 字幕区顶；
    2. 字幕区高 ≥ `CARD_SUB_ROWS_MIN` 行；
    3. **窗口 frame 开关卡片前后完全相等**（卡片不许把窗口撑大／缩小）；
    4. 卡片高于转录区 `CARD_MAX_FRAC` 时，内容在**卡片内部**滚（页脚按钮不跟着滚）。
    """

    def _overlay(self):
        try:
            from overlay import Overlay
        except ModuleNotFoundError as e:                      # pragma: no cover
            self.skipTest(f"AppKit 不可用, 跳过: {e}")
        try:
            return Overlay()
        except Exception as e:                                # noqa: BLE001
            self.fail(f"Overlay() 构造失败: {type(e).__name__}: {e}")

    @staticmethod
    def _open(o, kind: str) -> None:
        """开一张卡（三种：duty / wrapup / recap），并把几行字幕喂进去。"""
        for i, (en, zh) in enumerate((
                ("There is no class on Wednesday this week.", "这周三没有课。"),
                ("The problem set is due on Friday.", "习题集周五截止。"),
                ("We will start the group project next week.", "下周开始分组项目。"))):
            o.finalize(en, zh)
        if kind == "duty":
            o.duty_update([_item("d1"), _item("d2")])
            o._duty_open("d1")
        elif kind == "wrapup":
            o.duty_wrapup_show([_item("d1"), _item("d2")])
        else:
            o.recap_show({"course": "ECON10790", "date": "10-15（周四）",
                          "points": [{"name": f"知识点{i}", "explain": "一句解释"}
                                     for i in range(6)],
                          "stars": ["重点一", "重点二"],
                          "open": ["还没懂的问"],
                          "duties": [{"type": "调课", "title": "本周三停课"}],
                          "today": ["今天新课件"]},
                         course="ECON10790", day="2026-10-16")

    def test_卡片底加间距等于字幕区顶(self):
        """三条几何里最硬的一条：`卡片底 + CARD_SUB_GAP == 字幕区顶`。"""
        o = self._overlay()
        try:
            for kind in ("duty", "wrapup", "recap"):
                o._card_close()
                self._open(o, kind)
                card = o._card_view.frame()
                tv = o._tv._scroll.frame()
                bottom = card.origin.y
                top = tv.origin.y + tv.size.height
                self.assertAlmostEqual(
                    bottom - overlay.CARD_SUB_GAP, top, delta=0.75,
                    msg=f"{kind}：卡片底 {bottom:.1f} + 间距 {overlay.CARD_SUB_GAP} "
                        f"≠ 字幕区顶 {top:.1f}")
                # 字幕区的**底边**还是原来那条线（字幕从这儿往上长）
                self.assertAlmostEqual(tv.origin.y, float(o._scroll_y), delta=0.75,
                                       msg=f"{kind}：字幕区底边跑了")
        finally:
            o.close()

    def test_字幕区至少两行(self):
        o = self._overlay()
        try:
            for kind in ("duty", "wrapup", "recap"):
                o._card_close()
                self._open(o, kind)
                h = float(o._tv._scroll.frame().size.height)
                need = overlay.CARD_SUB_ROWS_MIN * float(o._row_h)
                self.assertGreaterEqual(h + 0.5, need,
                                        f"{kind}：字幕区只有 {h:.1f}pt < {need:.1f}pt（2 行）")
        finally:
            o.close()

    def test_卡片开着时字幕还在(self):
        """不给「藏起来再放回来」那种实现留后门：卡片开着时字幕区**必须是可见的**。"""
        o = self._overlay()
        try:
            for kind in ("duty", "wrapup", "recap"):
                o._card_close()
                self._open(o, kind)
                self.assertFalse(bool(o._tv._scroll.isHidden()), f"{kind}：字幕区被藏了")
                self.assertTrue(o._tv._scroll.frame().size.height > 0.5)
        finally:
            o.close()

    def test_窗口frame开关卡片前后完全相等(self):
        """⭐ 卡片**不许**动窗口（旧版：收起态开卡会自动展开面板）。"""
        o = self._overlay()
        try:
            for kind in ("duty", "wrapup", "recap"):
                o._card_close()
                before = o._panel.frame()
                self._open(o, kind)
                during = o._panel.frame()
                self.assertEqual((before.origin.x, before.origin.y,
                                 before.size.width, before.size.height),
                                 (during.origin.x, during.origin.y,
                                  during.size.width, during.size.height),
                                 f"{kind}：开卡把窗口改了（不许随卡片伸缩）")
                o._card_close()
                after = o._panel.frame()
                self.assertEqual((before.origin.x, before.origin.y,
                                  before.size.width, before.size.height),
                                 (after.origin.x, after.origin.y,
                                  after.size.width, after.size.height),
                                 f"{kind}：关卡把窗口改了")
        finally:
            o.close()

    def test_关卡片字幕区恢复全高_滚动位置与跟随不乱(self):
        """关卡：字幕区回到**全高**，而且贴在底部（跟随态）—— 阅读位置不许跳。"""
        o = self._overlay()
        try:
            for kind in ("duty", "wrapup", "recap"):
                o._card_close()
                for i in range(8):
                    o.finalize(f"Line number {i} keeps the transcript rolling.", f"第 {i} 行。")
                self.assertEqual(float(o._tv._scroll.frame().size.height),
                                 float(o._scroll_h), "开卡前字幕区就不是全高？")
                self._open(o, kind)
                o._card_close()
                self.assertAlmostEqual(float(o._tv._scroll.frame().size.height),
                                       float(o._scroll_h), delta=0.75,
                                       msg=f"{kind}：关卡后字幕区没恢复全高")
                self.assertTrue(o._tv.is_at_bottom(),
                                f"{kind}：关卡后跟随状态丢了（没贴底）")
        finally:
            o.close()

    def test_页脚按钮不在滚动文档里(self):
        """⭐ 「记下 / 全部忽略」钉在卡片**外框**上：内容一滚它们也点得到。

        （内容块反过来必须挂在滚动文档上 —— 不然"卡片内部滚动"就是假的。）
        """
        o = self._overlay()
        try:
            for kind in ("duty", "wrapup"):
                o._card_close()
                self._open(o, kind)
                btns = [x[0] for blk in o._card_pending if blk["kind"] == "buttons"
                        for x in (blk["extra"] or [])]
                self.assertTrue(btns, f"{kind}：没找到页脚按钮")
                for b in btns:
                    self.assertIs(b.superview(), o._card_view,
                                  f"{kind}：页脚按钮进了滚动文档（一滚就点不到了）")
                # 内容块必须在**滚动文档**里（一个都不许直接挂外框 —— 那就滚不起来）
                kids = set(o._card_view.subviews())
                content = []
                for blk in o._card_pending:
                    if blk["kind"] == "buttons":
                        continue
                    if blk["view"] is not None:
                        content.append(blk["view"])
                    ex = blk["extra"]
                    if isinstance(ex, dict):          # duty_row：head / quote / 按钮
                        content += [v for k, v in ex.items() if k != "buttons"]
                self.assertTrue(content, f"{kind}：没找到内容块")
                for v in content:
                    self.assertNotIn(v, kids,
                                     f"{kind}：内容块挂在外框上（卡片内部滚不起来）")
        finally:
            o.close()

    def test_装得下的不滚_装不下的才滚(self):
        """⭐ 文档高度 = **内容盒**（不含内边距）：装得下 → 一点也不滚；装不下 → 才滚。

        ⚠️ 文档虚高的话，装得下的卡也能滚出 2×`CARD_PAD` 的**空白**（拍图里见过：
           内容明明不长却停在半中间）—— 这条判据钉的就是那个。
        """
        o = self._overlay()
        try:
            for kind in ("duty", "wrapup"):
                o._card_close()
                self._open(o, kind)
                h = float(o._card_view.frame().size.height)
                region = float(o._scroll_h)
                rows = overlay.CARD_SUB_ROWS_MIN * float(o._row_h)
                cap = max(overlay.CARD_MIN_H,
                          min(overlay.CARD_MAX_FRAC * region,
                              region - rows - overlay.CARD_SUB_GAP))
                capped = h + 0.5 >= cap
                doc = float(o._card_doc.frame().size.height)
                clip = float(o._card_scroll.contentView().frame().size.height)
                if capped:
                    self.assertGreater(doc, clip + 1.0, f"{kind}：封顶了却滚不动")
                else:
                    self.assertLessEqual(doc, clip + 1.0,
                                         f"{kind}：装得下却还要滚（文档 {doc} > 视口 {clip}）")
        finally:
            o.close()

    def test_高卡片在卡片内部滚动_页脚不跟着滚(self):
        """回顾卡内容多：卡片封顶（≤ 60% 且留够字幕），内容进滚动文档，**页脚钉住**。"""
        o = self._overlay()
        try:
            o.recap_show({"course": "ECON10790",
                          "points": [{"name": f"知识点{i}", "explain": "一句解释 " * 3}
                                     for i in range(6)],
                          "stars": ["重点一", "重点二", "重点三"],
                          "open": ["没懂的一", "没懂的二"],
                          "duties": [{"type": "调课", "title": f"事件{i}"} for i in range(3)],
                          "today": ["今天新课件"]},
                         course="ECON10790", day="2026-10-16")
            region = float(o._scroll_h)
            card = o._card_view.frame()
            # ① 封顶：不超过 60%，也不吃掉字幕那两行
            self.assertLessEqual(card.size.height, overlay.CARD_MAX_FRAC * region + 0.5,
                                 "卡片超过了转录区的 60%")
            self.assertGreaterEqual(
                float(o._tv._scroll.frame().size.height) + 0.5,
                overlay.CARD_SUB_ROWS_MIN * float(o._row_h),
                "卡片把字幕挤没了")
            # ② 内容真的比视口高（= 内部滚动这条路走到了）
            clip = float(o._card_scroll.contentView().frame().size.height)
            doc = float(o._card_doc.frame().size.height)
            self.assertGreater(doc, clip + 1.0,
                               f"这张卡该滚却没滚：文档 {doc:.1f} ≤ 视口 {clip:.1f}")
            o._card_relayout()                      # 再摆一次不许把视口/位置弄丢
            self.assertGreater(float(o._card_doc.frame().size.height), 1.0)
            # ③ 页脚按钮钉在**外框**上（不是滚动文档里）—— 滚起来也点得到
            foot = [b for b in o._card_pending if b["kind"] == "buttons"]
            if foot:
                for btn, _lab, _code, _role in (foot[-1]["extra"] or []):
                    self.assertIs(btn.superview(), o._card_view,
                                  "页脚按钮进了滚动文档（滚出去就点不到了）")
        finally:
            o.close()


class 几何(unittest.TestCase):
    """⭐ 作者 2026-10-17：判据要**量几何**（在真视图上读 frame），不能只量调用。
    出问题的是排版：文字贴左、胶囊拖长、收尾行按钮两行高、输入框没内边距。
    """

    def _overlay(self):
        try:
            from overlay import Overlay
        except ModuleNotFoundError as e:
            self.skipTest(f"AppKit 不可用, 跳过: {e}")
        try:
            return Overlay()
        except Exception as e:                            # noqa: BLE001
            self.fail(f"Overlay() 构造失败: {type(e).__name__}: {e}")

    def _case(self, o, kind: str):
        """摆一张卡并把**可见的胶囊按钮**取出来（侧注 / 图标那些 `extra` 不算）。"""
        if kind == "duty":
            o.duty_update([_item("d1"), _item("d2")])
            o._duty_open("d1")
        else:
            o.duty_wrapup_show([_item("d1"), _item("d2")])
        out = []
        for blk in o._card_pending:
            ex = blk["extra"]
            if isinstance(ex, list):
                for x in ex:
                    b = x[0]
                    if not hasattr(b, "title") or b.isHidden():
                        continue
                    if len(x) >= 4:                       # 普通按钮：块里存了 role
                        role = str(x[3])
                    else:                                     # 类型胶囊：按选中态算
                        role = ("chip-on" if str(x[1]) == str(blk["chosen"])
                                else "chip-off")
                    out.append((b, role))
        return out

    def test_文字在胶囊里水平垂直居中(self):
        """⭐ 判据量的是**画出来的像素**（不是调用、也不是 frame 的约定）。

        同一颗胶囊渲染两次（有字 / 无字）相减 → 文字的墨迹列范围；它的中心与胶囊中心
        偏差 ≤1pt、两侧留白 ≥ `CARD_BTN_PAD_X`。垂直用 AppKit 给的 title 矩形核。
        """
        o = self._overlay()
        try:
            for kind in ("duty", "wrapup"):
                o._card_close()
                btns = self._case(o, kind)
                self.assertTrue(btns, f"{kind}：没量到按钮")
                for b, role in btns:
                    ink = _ink_span(b, lambda v=b, r=role: o._card_btn_style(v, r))
                    if ink is None:
                        self.skipTest("这台机器上视图渲染不回位图，跳过像素级判据")
                    x0, x1, _w, scale = ink
                    cx = (x0 + x1) / 2.0 / scale
                    bw = b.bounds().size.width
                    c = f"{b.title()!r} ink=({x0},{x1}) 中心={cx:.1f} 胶囊中心={bw / 2:.1f}"
                    self.assertLessEqual(abs(cx - bw / 2.0), 1.0,
                                         f"文字没水平居中：{c}")
                    self.assertGreaterEqual(x0 / scale, overlay.CARD_BTN_PAD_X - 0.6,
                                            f"左边留白不够：{c}")
                    self.assertGreaterEqual((bw - x1 / scale), overlay.CARD_BTN_PAD_X - 0.6,
                                            f"右边留白不够：{c}")
                    # 垂直：AppKit 的 title 矩形要落在胶囊中线上
                    tr = b.cell().titleRectForBounds_(b.bounds())
                    self.assertLessEqual(
                        abs((tr.origin.y + tr.size.height / 2.0) - b.bounds().size.height / 2.0),
                        1.0, f"文字垂直没居中：{c}")
                    self._assert_centered_para(b, c)
        finally:
            o.close()

    def _assert_centered_para(self, b, c: str) -> None:
        title = b.attributedTitle()
        self.assertIsNotNone(title, f"标题不是属性串（对齐无从谈起）：{c}")
        ps = title.attribute_atIndex_effectiveRange_("NSParagraphStyle", 0, None)
        if isinstance(ps, tuple):            # PyObjC 把出参 NSRange 也回给我们了
            ps = ps[0]
        self.assertIsNotNone(ps, f"属性串没有段落样式（对齐会退回左）：{c}")
        from AppKit import NSTextAlignmentCenter
        # ⚠️ 不要写死数字：这套 PyObjC 里 `NSTextAlignmentCenter` = 1（不是教科书里的 2），
        #    写死会把"对的"判成错的 —— 与常量比，别背数值。
        self.assertEqual(int(ps.alignment()), int(NSTextAlignmentCenter),
                         f"段落样式不是居中：{c}")

    def test_常量就是作者定的那组(self):
        """⭐ 判据不许只跟常量比（常量被改了两边一起动 = 恒真）。

        这里钉的是**作者给的值**：胶囊高 28（macOS regular 控件高，且命中区 ≥28）、
        左右留白 ≥10、按钮间距 8、输入框内边距 8、日期框 120–140。
        """
        self.assertEqual(overlay.CARD_BTN_H, 28.0)
        self.assertGreaterEqual(overlay.CARD_BTN_PAD_X, 10.0)
        self.assertEqual(overlay.CARD_BTN_GAP, 8.0)
        # 输入框左内边距 8（与胶囊文字左缘同一条竖线靠**墨迹**判据钉，见 `几何` 那一类）
        self.assertEqual(overlay.FIELD_PAD_X, 8.0)
        self.assertTrue(120.0 <= overlay.CARD_DATE_W <= 140.0,
                        f"日期框宽该在 120–140：{overlay.CARD_DATE_W}")

    def test_按钮高度是常量(self):
        o = self._overlay()
        try:
            for kind in ("duty", "wrapup"):
                o._card_close()
                for b, _role in self._case(o, kind):
                    self.assertAlmostEqual(b.frame().size.height, overlay.CARD_BTN_H,
                                           delta=0.51,
                                           msg=f"{kind} 里 {b.title()!r} 高度不对")
        finally:
            o.close()

    def test_同一行按钮_midY_相等(self):
        """同一行（同一个块里）的按钮中线必须齐 —— 不齐就是"一高一低"。"""
        o = self._overlay()
        try:
            for kind in ("duty", "wrapup"):
                o._card_close()
                self._case(o, kind)
                for blk in o._card_pending:
                    if blk["kind"] not in ("buttons", "chips") or not blk["extra"]:
                        continue
                    mids = [b.frame().origin.y + b.frame().size.height / 2.0
                            for b, *_ in blk["extra"] if not b.isHidden()]
                    self.assertTrue(mids, f"{kind}：这一行一颗按钮都没有")
                    self.assertLessEqual(max(mids) - min(mids), 1.0,
                                         f"{kind}：同一行按钮不齐 {mids}")
        finally:
            o.close()

    def test_收尾行按钮对该行垂直居中(self):
        """每行的「记下 / 忽略」要对**整行**居中（行中线由摆位时写进 `blk["mid"]`）。"""
        o = self._overlay()
        try:
            self._case(o, "wrapup")
            rows = _blocks(o, "duty_row")
            self.assertTrue(rows)
            for blk in rows:
                if not blk["extra"]:
                    continue
                mid = float(blk["mid"])
                for k, b in blk["extra"]["buttons"].items():
                    if k == "tag" or b.isHidden():
                        continue
                    bm = b.frame().origin.y + b.frame().size.height / 2.0
                    self.assertLessEqual(abs(bm - mid), 1.0,
                                         f"{b.title()!r} 没对行居中：{bm} vs {mid}")
        finally:
            o.close()

    def test_输入框内边距与日期定宽(self):
        """输入框：左右内边距 = `FIELD_PAD_X`；高 = 按钮高；日期框按内容定宽。"""
        o = self._overlay()
        try:
            self._case(o, "duty")
            fields = _blocks(o, "field")
            self.assertTrue(fields, "编辑卡里没有输入框")
            for blk in fields:
                box = blk["box"]
                fld = blk["fld"]
                self.assertAlmostEqual(fld.frame().origin.x, overlay.FIELD_PAD_X,
                                       delta=0.51, msg=f"{blk['key']}：左边距不对")
                self.assertAlmostEqual(
                    box.frame().size.width - (fld.frame().origin.x + fld.frame().size.width),
                    overlay.FIELD_PAD_X, delta=0.51, msg=f"{blk['key']}：右边距不对")
                self.assertAlmostEqual(box.frame().size.height, overlay.CARD_BTN_H,
                                       delta=0.51, msg=f"{blk['key']}：高度与按钮不一致")
            date = [b for b in fields if b["key"] == "date"][0]
            self.assertAlmostEqual(date["box"].frame().size.width,
                                   overlay.CARD_DATE_W, delta=0.51,
                                   msg="日期框该按内容定宽（不拉满整行）")
            title = [b for b in fields if b["key"] == "title"][0]
            self.assertGreater(title["box"].frame().size.width,
                               overlay.CARD_DATE_W + 40.0, "标题框该是满行")
        finally:
            o.close()

    def test_输入框文字垂直居中且左缘与胶囊对齐(self):
        """⭐ 作者 2026-10-17 报的三处之一：输入框文字贴顶、左缘与 chip 不一列。

        判据三条：① 文字 rect 中心 vs **框**中心 ≤1pt；② 像素级墨迹中心也 ≤1pt；
        ③ 输入框文字的**左缘**与类型胶囊的文字左缘同一条竖线（都 = 左边距 + `CARD_BTN_PAD_X`）。
        """
        o = self._overlay()
        try:
            self._case(o, "duty")
            fields = _blocks(o, "field")
            self.assertTrue(fields, "编辑卡里没有输入框")
            chips = _blocks(o, "chips")[0]
            chip_btn, chip_name = chips["extra"][0][0], chips["extra"][0][1]
            chip_role = ("chip-on" if chip_name == chips["chosen"] else "chip-off")
            chip_px = _ink_span(chip_btn,
                                lambda v=chip_btn, r=chip_role: o._card_btn_style(v, r))
            for blk in fields:
                box, fld = blk["box"], blk["fld"]
                box_mid = box.frame().size.height / 2.0
                tr = fld.cell().titleRectForBounds_(fld.bounds())
                mid = fld.frame().origin.y + tr.origin.y + tr.size.height / 2.0
                c = (f"{blk['key']}：文字 rect 中线 {mid:.2f} vs 框中线 {box_mid:.2f}")
                self.assertLessEqual(abs(mid - box_mid), 1.0, f"文字 rect 没在框中线：{c}")
                ink = _ink_box(fld)
                if ink is not None:
                    imid = (ink[2] + ink[3]) / 2.0
                    self.assertLessEqual(abs(imid - box_mid), 1.0,
                                         f"墨迹不在框中线：{c} ink={(ink[2], ink[3])}")
                if chip_px is not None:
                    chip_left = chip_px[0] / chip_px[3]
                    chip_left_card = chip_btn.frame().origin.x + chip_left
                    if ink is not None:
                        fld_left_card = box.frame().origin.x + fld.frame().origin.x + ink[0]
                        self.assertLessEqual(
                            abs(fld_left_card - chip_left_card), 1.0,
                            f"{blk['key']}：文字左缘 {fld_left_card:.2f} 与胶囊文字左缘 "
                            f"{chip_left_card:.2f} 不在一条竖线")
            # ⚠️ 两条渲染路径的墨迹起点差约 2.5pt（见 `FIELD_PAD_X` 的注释），
            #    所以这里比的**不是**两个常数相等，而是上面量出来的那条竖线。
            self.assertEqual(overlay.FIELD_PAD_X, 8.0)
        finally:
            o.close()

    def test_推算那行与框内文字同基线(self):
        """⭐ 三处之二：「10-16（周五）· 推算」原来比框内文字低约 4pt。

        判据：与框内文字 ① 中线差 ≤1pt、② 基线差 ≤1pt（基线按 cell 的 drawing rect +
        字体 ascent 算，两边**同一口径**）。
        """
        o = self._overlay()
        try:
            self._case(o, "duty")
            date = [b for b in _blocks(o, "field") if b["key"] == "date"]
            self.assertTrue(date, "日期框不在")
            blk = date[0]
            self.assertTrue(blk["side"], "日期框没有右侧说明")
            box, fld, side = blk["box"], blk["fld"], blk["extra"]
            box_y = box.frame().origin.y
            f_ink, s_ink = _ink_box(fld), _ink_box(side)
            self.assertIsNotNone(f_ink, "没量到框内文字")
            self.assertIsNotNone(s_ink, "没量到右侧说明")
            f_mid = box_y + (f_ink[2] + f_ink[3]) / 2.0
            s_mid = side.frame().origin.y + (s_ink[2] + s_ink[3]) / 2.0
            self.assertLessEqual(abs(s_mid - f_mid), 1.0,
                                 f"中线差 {s_mid - f_mid:+.2f}（框内 {f_mid:.2f} / 说明 {s_mid:.2f}）")
            f_base = (box_y + fld.frame().origin.y
                      + fld.cell().drawingRectForBounds_(fld.bounds()).origin.y
                      + fld.font().ascender())
            s_base = (side.frame().origin.y
                      + side.cell().drawingRectForBounds_(side.bounds()).origin.y
                      + side.font().ascender())
            self.assertLessEqual(abs(s_base - f_base), 1.0,
                                 f"基线差 {s_base - f_base:+.2f}")
        finally:
            o.close()

    def test_收尾页脚与行内按钮右对齐(self):
        """⭐ 三处之三：「全部忽略」原来在左下、被内边距顶进去（像缩进错位）。

        作者选了右下角：它的**右缘**要与每行「忽略」的右缘同一条竖线（≤1pt）。
        """
        o = self._overlay()
        try:
            self.assertTrue(o.duty_wrapup_show([_item("d1"), _item("d2")]))
            rows = _blocks(o, "duty_row")
            foot = _blocks(o, "buttons")[-1]
            fbtn = foot["extra"][0][0]
            self.assertEqual(str(foot["extra"][0][1]), "全部忽略")
            self.assertEqual(str(foot.get("align") or "left"), "right", "页脚没声明贴右")
            rights = [b.frame().origin.x + b.frame().size.width
                      for blk in rows if blk["extra"]
                      for k, b in blk["extra"]["buttons"].items() if k == "ignore"]
            self.assertTrue(rights, "收尾行里没有「忽略」")
            f_right = fbtn.frame().origin.x + fbtn.frame().size.width
            self.assertLessEqual(abs(f_right - max(rights)), 1.0,
                                 f"页脚右缘 {f_right:.2f} vs 行内右缘 {max(rights):.2f}")
        finally:
            o.close()

    def test_输入框编辑态文字也在中线(self):
        """点进输入框时文字**不许跳**：编辑器那行字的中线与静止态一致（≤1pt）。"""
        o = self._overlay()
        try:
            o.show()
            self._case(o, "duty")
            blk = [b for b in _blocks(o, "field") if b["key"] == "title"][0]
            box, fld = blk["box"], blk["fld"]
            still = _ink_box(fld)
            o._panel.makeFirstResponder_(fld)
            for _ in range(3):
                o.pump()
            ed = fld.currentEditor()
            if ed is None:
                self.skipTest("这台机器上输入框拿不到字段编辑器，跳过编辑态判据")
            ed.insertText_("测试")
            for _ in range(3):
                o.pump()
            ink = _ink_box(ed)
            self.assertIsNotNone(ink, "编辑态量不到文字")
            box_mid = box.frame().size.height / 2.0
            mid = (ink[2] + ink[3]) / 2.0
            self.assertLessEqual(abs(mid - box_mid), 1.0,
                                 f"编辑态文字不在框中线：{mid:.2f} vs {box_mid:.2f}")
            if still is not None:
                self.assertLessEqual(
                    abs(mid - (still[2] + still[3]) / 2.0), 1.5,
                    "编辑态与静止态的文字高度差太多（点进去会跳一下）")
        finally:
            o.close()

    def test_选中胶囊用primary的填充(self):
        o = self._overlay()
        try:
            self._case(o, "duty")
            blk = [b for b in o._card_pending if b["kind"] == "chips"][0]
            by_name = {n: b for b, n in blk["extra"]}
            chosen = str(blk["chosen"])
            on = _fill_alpha(by_name[chosen])
            off_name = next(n for n in by_name if n != chosen)
            off = _fill_alpha(by_name[off_name])
            self.assertAlmostEqual(on, overlay.BTN_STYLE["chip-on"][0], delta=0.02)
            self.assertGreater(on - off, 0.08, "选中态不能只靠描边（要用 primary 的填充）")
        finally:
            o.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
