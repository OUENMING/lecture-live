#!/usr/bin/env python3
"""详细版 HTML 的**断网自足**（第六轮）：公式在无外网时也要渲染出来。

    ClassLive.app/Contents/MacOS/python tests/test_note_offline.py

## 钉住了什么

1. ⭐ **装到笔记旁边的 KaTeX 副本真的装上了**：`.classlive-assets/katex/0.16.9/`
   下有 js / css / `fonts/*.woff2`（断网时的全部家当）。
2. ⭐ **页面里没有任何外链子资源**：KaTeX 走**相对引用**；`src=` / `href=` 里
   一个 `http(s)://` 都不许有（CDN 一断，公式就退回 TeX 源码 —— 作者第六轮的要求）。
3. ⭐ **真·断网渲染**：`--proxy-server=127.0.0.1:9` 指向一个**没人听**的端口
   （全断）打开页面，DOM 里必须出现 `class="katex"`。
   —— 没装 Playwright 的机器这条**跳过**（本机样本，不是产品依赖），其余照跑。
4. **兜底不白屏**：HTML 里公式先以 `.tex`（`data-tex` 放着原始 TeX）出现，
   JS 不可用时它就是可见的源码 —— 这条与第 3 条**方向相反**，两条都在才对。

⚠️ 实现只有一份：`scripts/note_eval_katex.py`（探针脚本，评测报告那一栏也读它）。
   本文件只做**断言**（同一份代码，两种用法）。
"""
import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import note_html as nh          # noqa: E402
import note_render as nr        # noqa: E402

_spec = importlib.util.spec_from_file_location("note_eval_katex", ROOT / "scripts/note_eval_katex.py")
nk = importlib.util.module_from_spec(_spec)
sys.modules["note_eval_katex"] = nk
_spec.loader.exec_module(nk)

#: 两处公式都写进正文（讲解里一条、例题里一条），另加一条转录里的。
DATA = nr.NoteData(
    date="2026-10-17", course="TESTX",
    entries=[{"ts": "13:16:00", "en": "The marginal cost is MC=3q^2-24q+60.",
              "zh": "边际成本是 $MC=3q^2-24q+60$。", "asr": "", "star": False}],
    review={"title_zh": "成本函数",
            "sections": [{"heading": "边际成本", "ts": "13:16",
                          "prose": ["把 $TC(q)=q^3-12q^2+60q$ 除以 $q$ 就得到平均成本。"],
                          "example": {"title": "例", "prompt": "$\\frac{dAC}{dq}=2q-12$",
                                      "steps": [{"text": "求导", "why": "定义"}],
                                      "answer": "$q=6$"}}]},
    html_name="x.html")


class SelfContained(unittest.TestCase):
    """静态那一半：副本 + 相对引用 + TeX 兜底。"""

    def _render(self, d):
        return nk.render_dir(DATA, pathlib.Path(d), html_name="x.html")

    def test_副本装到笔记旁边(self):
        with tempfile.TemporaryDirectory() as d:
            page, ok = self._render(d)
            self.assertTrue(ok, "install_assets 说没装上")
            dst = page.parent / nh.KATEX_DIR
            for f in ("katex.min.js", "katex.min.css", "LICENSE"):
                self.assertTrue((dst / f).exists(), f"副本里缺 {f}")
            fonts = list((dst / "fonts").glob("*.woff2"))
            self.assertGreaterEqual(len(fonts), 10, f"字体只装了 {len(fonts)} 个")
            # ⚠️ 仓库里的那一份也要在（install_assets 拷的就是它）——
            #    少了它 = 换机器 / 新克隆的仓库装不出自足笔记。
            self.assertTrue((nh.KATEX_SRC / "katex.min.js").exists(),
                            f"仓库里没有 KaTeX 副本：{nh.KATEX_SRC}")

    def test_页面里没有任何外链子资源(self):
        with tempfile.TemporaryDirectory() as d:
            page, _ = self._render(d)
            html = page.read_text(encoding="utf-8")
            self.assertEqual(nk.external_refs(html), [], "页面里有外链子资源")
            self.assertIn(nh.KATEX_JS, html, "没引用本地 katex.min.js")
            self.assertIn(nh.KATEX_CSS, html, "没引用本地 katex.min.css")
            # ⚠️ 副本的 CSS 里字体也必须是**相对**路径（`url(fonts/…)`）——
            #    换成绝对 URL 的话断网时公式字体会退化成系统字体（渲染"成功"但不像公式）。
            css = (page.parent / nh.KATEX_DIR / "katex.min.css").read_text(encoding="utf-8")
            self.assertNotIn("url(http", css, "katex.min.css 里有绝对 URL 的字体引用")
            self.assertIn("url(fonts/KaTeX_Main-Regular.woff2)", css)

    def test_公式有_TeX_源码兜底_不白屏(self):
        """JS 挂掉时读者看到的必须是**源码**（`data-tex`），不是空白。"""
        with tempfile.TemporaryDirectory() as d:
            page, _ = self._render(d)
            html = page.read_text(encoding="utf-8")
            self.assertIn('class="tex"', html)
            self.assertIn('data-tex="TC(q)=q^3-12q^2+60q"', html,
                          "`data-tex` 里不是**原文**（那是回退给读者看的源码）")
            # 兜底可见文字是**格式化后的**（Unicode 上标），不是空白也不是乱码。
            self.assertIn("TC(q)=q³-12q²+60q", html, "兜底文字里连公式都没有")


class OfflineRender(unittest.TestCase):
    """动态那一半：**真断网**打开页面，公式必须渲染出来。"""

    def test_断网也渲染出_KaTeX(self):
        exe = nk.chrome_shell()
        if exe is None:
            self.skipTest("本机没装 Playwright 的 chrome-headless-shell（缺本机样本）")
        with tempfile.TemporaryDirectory() as d:
            page, ok = nk.render_dir(DATA, pathlib.Path(d), html_name="x.html")
            self.assertTrue(ok, "install_assets 说没装上")
            got = nk.offline_check(page, shell=exe)
            self.assertEqual(got["rc"], 0, got["err"])
            self.assertGreater(got["katex_spans"], 0,
                               "断网时公式没渲染（KaTeX 副本没起作用）")
            self.assertTrue(got["katex_html"], "只替换了一半（没有排版结构）")
            self.assertFalse(got["raw_tex_left"],
                             "JS 跑过之后还留着 TeX 兜底元素 = 没替换成功")

    def test_真没渲染出来要判红(self):
        """⭐ 反向那条：公式在、渲染失败，`ok` 必须是 False。

        用 `/usr/bin/false` 当"浏览器"（rc=1）—— 不依赖任何真渲染路径，
        钉的是 `check_data_json` 的**判据方向**（第 3 条不是恒真）。
        """
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            p = d / "note_data.json"
            import dataclasses as dc
            p.write_text(json.dumps(dc.asdict(DATA), ensure_ascii=False), encoding="utf-8")
            got = nk.check_data_json(p, shell=pathlib.Path("/usr/bin/false"))
            self.assertFalse(got["ok"], got)
            self.assertGreater(got["tex_spans"], 0)

    def test_没有公式的一节不算失败(self):
        """两节真实产物（10740 / SOC）**一个 `$` 都没有** —— "没东西可渲染" ≠ 坏。"""
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            import dataclasses as dc
            raw = dc.asdict(DATA)
            raw["review"] = {"title_zh": "没有公式的一节",
                             "sections": [{"heading": "H", "ts": "13:16",
                                           "prose": ["这段课全是文字，没有公式。"]}]}
            raw["entries"] = [{"ts": "13:16:00", "en": "no math here",
                               "zh": "这里没有公式。", "asr": "", "star": False}]
            p = d / "note_data.json"
            p.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
            got = nk.check_data_json(p, shell=nk.chrome_shell() or pathlib.Path("/usr/bin/false"))
            self.assertEqual(got["tex_spans"], 0, "这一份的用例里混进了公式")
            self.assertEqual(got["ok"], True, got)
            self.assertEqual(got["dynamic"], "no-math")


if __name__ == "__main__":
    unittest.main(verbosity=2)
