"""版式 A（2026-10-18 作者拍板）的判据：速览/串讲 / 线索栏 / QEC / 填空不泄答案 /
打印档 / 十字光标 / 动效规范。

每条都做过**变异验证**（把实现对的那一处改回旧行为 → 这条会红）：
  · 填空把答案印回题面旁 → `Blanks.test_填空是输入框_答案只在_data_ans` 红；
  · 删掉打印段的 beforeprint / 十字光标隐藏 → `Print` / `Crosshair` 红；
  · `svg()` 里拿掉 `.cross` → `Crosshair.test_图上带十字光标本体` 红。
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import note_html as nh          # noqa: E402
import note_render as nr        # noqa: E402

SEC = {
    "heading": "边际成本（marginal cost）", "ts": "13:31",
    "q": "多生产一单位要多花多少？",
    "cues": ["为什么 MC 先降后升？", "MC 和 AC 谁穿过谁？"],
    "conclusion": "写清「因为 MC 小于 AC」才拿分。",
    "algebra": "$MC=3q^2-24q+60$。",
    "intuition": "多出来那一个单位的成本就是它。",
    "prose": ["**先说结论。**边际成本是多做一单位要多花的钱，这里接上一节的平均成本。",
              "第二段的解释，接着把式子写出来。"],
    "example": {"title": "📖 Exercise 5", "prompt": "$TC=q^3-12q^2+60q$",
                "steps": [{"text": "求 AC", "why": "平均成本的定义就是摊到每一单位"}],
                "answer": "$AC=q^2-12q+60$"},
    "variants": [{"q": "设 $TC=q^3-6q^2+15q$", "steps": [], "a": "$q=3$"}],
    "faded": {"q": "补全：$TC=q^3-12q^2+60q$ 时 $dAC/dq=$ ____",
              "blanks": ["$2q-12$"], "a": "$q=6$"},
    "pitfalls": ["把符号搞反"], "terms": [{"en": "marginal cost", "zh": "边际成本"}],
    "sources": ["🎙 13:31"],
    "plot": {"kind": "cost_curves", "title": "AC 与 MC", "params": {"a": 1, "b": -12, "c": 60}},
}
REVIEW = {"title_zh": "成本函数", "sections": [SEC],
          "qa": [{"q": "Q1?", "a": "A1", "kind": "concept"}],
          "overview": {"one_line": "一句话总览。", "takeaways": ["t1", "t2", "t3"]},
          "overview_long": ["**第一段。**（第 1 节）讲这个。",
                            "**第二段。**（第 1、2 节）接着讲。"]}


def _data(**kw):
    d = nr.NoteData(date="2026-10-15", course="ECON10790", review=REVIEW,
                    review_dates=["2026-10-15", "2026-10-18", "2026-10-22"],
                    html_name="x.html")
    for k, v in kw.items():
        setattr(d, k, v)
    return d


class Blanks(unittest.TestCase):
    def test_填空是输入框_答案只在_data_ans(self):
        frag = nh._faded_html({"q": "…$f'(x)=$ ____。", "blanks": ["$3x^2-3$"],
                               "a": "x=±1"}, "fd1")
        self.assertIn('<input class="blank"', frag)
        self.assertIn('data-ans="$3x^2-3$"', frag)
        self.assertIn("对答案", frag)
        visible = re.sub(r"<[^>]+>", "", frag)      # 去掉标签与属性 = 肉眼读到的字
        self.assertNotIn("3x^2-3", visible, "答案印在题面旁了（旧 bug）")
        self.assertNotIn("⬜", frag, "旧实现把每个空的答案逐条列在题下")

    def test_空数对不上也退化_但答案仍不可见(self):
        frag = nh._faded_html({"q": "求 $AC$ 最低点。", "blanks": ["$q=6$", "$AC(6)=24$"],
                               "a": "…"}, "fd2")
        self.assertEqual(len(re.findall(r'<input class="blank"', frag)), 2)
        visible = re.sub(r"<[^>]+>", "", frag)
        self.assertNotIn("q=6", visible)
        self.assertNotIn("24", visible)

    def test_页面上的补全题也走这条路(self):
        h = nh.note_page(_data())
        self.assertIn('data-ans="$2q-12$"', h)
        self.assertNotIn(">2q-12<", h, "答案以可见文本进了页面")


class CueQEC(unittest.TestCase):
    def test_线索栏与_QEC(self):
        h = nh.note_page(_data())
        self.assertIn('class="cue cue-scroll"', h)
        self.assertIn("线索问题", h)
        self.assertIn("边际成本 (marginal cost)", h)
        for lab in ("Q 本节回答", "E 三种看法", "C 考试结论"):
            self.assertIn(f'<span class="lab">{lab}</span>', h)
        self.assertIn('data-tr="g"', h)
        self.assertIn('data-tr="a"', h)
        self.assertIn('data-tr="i"', h)
        self.assertRegex(h, r'data-pane="a" hidden')
        self.assertRegex(h, r'data-pane="g">(?!<div)')          # 图形轨默认可见

    def test_没有的轨不画(self):
        sec = dict(SEC, plot={}, algebra="", intuition="")
        h = nh.note_page(_data(review={"sections": [sec]}))
        self.assertNotIn("E 三种看法", h)
        self.assertNotIn('data-pane="a"', h)

    def test_只有图时不出切换按钮(self):
        sec = dict(SEC, algebra="", intuition="")
        h = nh.note_page(_data(review={"sections": [sec]}))
        self.assertIn("E 三种看法", h)
        self.assertNotIn('class="tracks"', h, "只有一条轨时没有可切换的东西")
        self.assertIn('data-pane="g"', h)

    def test_第一段的重点句单独标出(self):
        h = nh.note_page(_data())
        self.assertIn('<strong class="keyline">先说结论。</strong>', h)


class LeadBold(unittest.TestCase):
    """段首断言句的加粗是**渲染器承诺**（2026-10-18）：提示词告诉模型"会被加粗显示"，
    实测模型只有 0–24% 的段自己写了 `**` —— 所以首句必须有确定性兜底。"""

    def test_模型没写粗体也自动加粗首句(self):
        got = nh._bold_lead(nh.inline("先说结论，再解释。后面还有话。"), keyline=True)
        self.assertEqual('<strong class="keyline">先说结论，再解释。</strong>后面还有话。', got)

    def test_每段都加粗_只有第一段是强调色(self):
        a = nh._bold_lead(nh.inline("第一句。第二句。"), keyline=True)
        b = nh._bold_lead(nh.inline("另一段的第一句。后面。"), keyline=False)
        self.assertIn('<strong class="keyline">第一句。</strong>', a)
        self.assertIn('<strong>另一段的第一句。</strong>后面。', b)
        self.assertNotIn("keyline", b)

    def test_首句扫描跳过标签内部(self):
        # 段落以公式开头：句号在里面/外面都不能把 data-tex 属性里的内容算成句子结尾
        got = nh._bold_lead(nh.inline("$q^*=6$ 时最小成本。这是解释。"), keyline=True)
        self.assertIn('<strong class="keyline"><span class="tex"', got)
        self.assertIn("时最小成本。</strong>这是解释。", got)

    def test_模型自己的粗体不产生嵌套(self):
        got = nh._bold_lead(nh.inline("本节的核心是：**拉低平均成本**。这是关键。"), keyline=True)
        self.assertEqual(1, got.count("<strong"))
        self.assertIn('<strong class="keyline">本节的核心是：拉低平均成本。</strong>', got)

    def test_页面里每段的开头句都是加粗的(self):
        sec = dict(SEC, prose=["第一节的第一句。补充说明。", "第二段的第一句。接着讲。"])
        h = nh.note_page(_data(review={"sections": [sec]}))
        self.assertIn('<strong class="keyline">第一节的第一句。</strong>', h)
        self.assertIn('<strong>第二段的第一句。</strong>', h)

    def test_串讲段落链到小节锚点_越界不硬链(self):
        h = nh.note_page(_data())
        self.assertIn('id="overview"', h)
        self.assertIn("五分钟串讲", h)
        self.assertIn('href="#kp1"', h)
        self.assertNotIn("href", nh._link_sections("见（第 9 节）", 2))
        self.assertEqual(nh._link_sections("（第 1 节）", 1), '（第 <a href="#kp1">1</a> 节）')


class Schedule(unittest.TestCase):
    def test_HTML_里是带日期的三档(self):
        h = nh.note_page(_data())
        for want in ("复习排程", "今晚", "+3 天", "+7 天",
                     "2026-10-15", "2026-10-18", "2026-10-22"):
            self.assertIn(want, h)

    def test_md_里的排程行也是带日期的(self):
        md = nr.render(_data())
        self.assertIn("**复习排程**", md)
        self.assertIn("`2026-10-18`", md)


class Print(unittest.TestCase):
    def _h(self):
        return nh.note_page(_data())

    def test_打印档的每一条都在(self):
        h = self._h()
        self.assertIn("@page{margin:16mm}", h)
        self.assertIn("print-color-adjust:exact", h)
        print_block = h.split("@media print")[1]
        self.assertIn("--bg:#fff", print_block[:500], "打印没有强制浅色 token（暗色主题会印出近白字）")
        for want in ("#prog,#topbar,.slider,.tracks,.btn,button.why,textarea.sum,#expand"
                     "{display:none!important}",
                     ".cross{display:none!important}",
                     "aside.cue{position:static;margin:0 0 8px;max-height:none;overflow:visible;",
                     "[data-pane]{display:block!important}",
                     "details,figure.fig,.q,.qec>div{break-inside:avoid}"):
            self.assertIn(want, h, want)

    def test_打印前展开_details_打印后还原(self):
        h = self._h()
        self.assertIn("window.addEventListener('beforeprint'", h)
        self.assertIn("window.addEventListener('afterprint'", h)
        self.assertIn("x.open=true", h)
        self.assertIn("printed.forEach(function(x){x.open=false})", h)


class Crosshair(unittest.TestCase):
    def test_图上带十字光标本体(self):
        s = nh.svg("function_cubic", {"a": 1, "b": 0, "c": -3, "d": 0}, pid="p1")
        self.assertIn('class="cross"', s)
        self.assertIn('data-cross="1"', s)
        for attr in ("data-x0=", "data-x1=", "data-y0=", "data-y1=", "data-iw=", "data-ih="):
            self.assertIn(attr, s)

    def test_JS_跟随指针(self):
        h = nh.note_page(_data())
        self.assertIn("pointermove", h)
        self.assertIn("cross.style.display=''", h)


class Motion(unittest.TestCase):
    def test_block_enter_与按压反馈(self):
        h = nh.note_page(_data())
        self.assertIn("block-enter", h)
        self.assertIn("cubic-bezier(.22,1,.36,1)", h)
        self.assertIn("button:active{transform:scale(.975)", h)
        self.assertIn("prefers-reduced-motion", h)

    def test_线索栏渐隐是滚动驱动_打印关掉(self):
        h = nh.note_page(_data())
        self.assertIn("@supports (animation-timeline: scroll(self block))", h)
        self.assertIn("fade-top-in", h)
        self.assertIn("mask-image:none", h)


class WhyAcc(unittest.TestCase):
    """「为什么」用**带动画的按钮开合**（2026-10-18 追认，值取自反编的 Claude Desktop CSS）。

    三个都要成立：屏幕上有动画（0fr↔1fr）；没有 JS 时默认是开的（正文读得到）；
    打印时无条件全开（不靠 JS）。
    """

    def test_为什么按钮带_aria_与网格动画(self):
        h = nh.note_page(_data())
        self.assertIn('<button class="why" type="button" aria-expanded="false"', h)
        self.assertIn('aria-controls="why-ex1-1"', h)
        self.assertIn('<div class="acc" id="why-ex1-1">', h)
        self.assertIn(".acc{display:grid;grid-template-rows:1fr", h)
        self.assertIn("html.js .acc{grid-template-rows:0fr", h)
        self.assertIn("html.js .acc.open{grid-template-rows:1fr", h)
        self.assertIn("panel.classList.toggle('open')", h)
        self.assertIn("wy.setAttribute('aria-expanded',on?'true':'false')", h)

    def test_打印时无条件全开_按钮藏掉(self):
        block = nh.note_page(_data()).split("@media print")[1]
        self.assertIn(".acc{grid-template-rows:1fr!important;opacity:1!important", block)
        self.assertIn("button.why", block)
        self.assertIn("prefers-reduced-motion:reduce){.acc{transition:none}", nh.note_page(_data()))


class Inline(unittest.TestCase):
    def test_粗体跨公式也要成对(self):
        """⚠️ 2026-10-18 截图自查：串讲的加粗断言句里常带 `$…$` —— 首尾 `**` 落在公式
        两边的不同段里，逐段处理会在页面上留下字面 `**`（截图里肉眼可见）。"""
        got = nh.inline("**两种方法等价，因为有 $MC=AC$。**（第 8 节）求导法也行。")
        self.assertNotIn("**", got)
        self.assertIn("<strong>两种方法等价，因为有 ", got)
        self.assertIn('data-tex="MC=AC"', got)
        self.assertIn("</strong>（第 8 节）", got)


if __name__ == "__main__":
    unittest.main(verbosity=2)
