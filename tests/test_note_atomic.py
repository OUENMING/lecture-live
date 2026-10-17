#!/usr/bin/env python3
"""课后笔记**两段式落盘**（3.9.7 第 5b 项，`obsidian_writer.close()`）的判据 —— 全离线。

    ClassLive.app/Contents/MacOS/python tests/test_note_atomic.py

## 钉住了什么

1. ⭐⭐ **基础版先落盘**：复习层（`note_v2.REVIEW_WORKERS=5` 并发那一段）**还没返回**时，
   `Lectures/` 下就已经有一份可读的笔记（转写 + 译文）。
   —— 这一条防的是"复习层那几十秒里关窗 / 崩溃 → 这节课一份笔记都没有"。
   ⚠️ 2026-10-17 第六轮起笔记分两层（md 简版 + HTML 详细版，转录在 HTML 里）——
      **基础版也两份一起写**，所以这里的读取用 `note_text()`（两个文件合一读）。
2. ⭐ **复习层回来后原子重写同一路径**为完整版：**只有一个 .md 文件**
   （`note_path_for` 遇到同名会另存一份 —— 路径必须只算一次），最终内容含复习层。
3. ⭐ **复习层抛异常 / 被取消**：基础版留着（逐句转录一条不少），并多一行
   「复习层这次没生成 + 怎么用 `rebuild_note.py` 补」。
4. **写端隔离**：`sessions/` 的会话文件**字节不变**（三方契约）；vault 是临时目录。
5. **原子**：写完不留 `.tmp`（`os.replace` 换过去）。
6. 🧩🃏 2026-10-17 起同目录还会多**两个旁车文件**（`.html` 交互版 / `.anki.csv`）——
   §2 那条"只有一份笔记"的判据因此**只数 `.md`**（旁车是有意为之，不是路径算两遍）。

⚠️ 复习层是**桩**（`w._review_v2`），不联网；`CLASSLIVE_NOTE_MERGE=0` 按住碎句合并；
   写端（`ow.SESSIONS` 与 vault）与读端都在 `tempfile` 里。
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import obsidian_writer as ow                                          # noqa: E402


def note_text(vault_path) -> str:
    """笔记**两个文件**一起读（md 简版 + 同目录 HTML 详细版）。

    ⚠️ 2026-10-17 第六轮起笔记是两层的：逐句转录搬进了 HTML（md 只是简版）——
       「这条内容丢没丢」的判据必须把两份都读进来，否则量具会比被测对象先坏
       （同 `note_render.chinese_ratio` 那条注释里的教训）。
    """
    p = pathlib.Path(vault_path)
    out = []
    for q in (p, p.with_suffix(".html")):
        try:
            out.append(q.read_text(encoding="utf-8"))
        except OSError:
            out.append("")
    return "\n".join(out)

SESSION = ("# TESTX · 2026-10-16 · 实时会话日志\n\n"
           "> [!abstract] 10:00:03\n"
           "> **EN**: first one.\n> **ZH**: 第一句。\n> **ASR**: first one.\n\n"
           "> [!abstract] 10:00:12\n"
           "> **EN**: second one.\n> **ZH**: 第二句。\n> **ASR**: second one.\n\n")

#: v2 的复习层形状（2026-10-17）：小节是**中文连贯讲解**（`prose`），
#: 另有 term/checks/qa —— 桩给最小可用集，够渲染出可断言的那几行。
REVIEW = {"title_zh": "本课主题",
          "sections": [{"heading": "知识点一", "ts": "10:00",
                        "prose": ["English point 在这里被讲成一个中文段落。"],
                        "terms": [{"en": "Key Point", "zh": "关键点"}]}],
          "qa": [{"q": "自测题？", "a": "答案。", "kind": "concept"}],
          "terms": [{"en": "Key Point", "zh": "关键点"}]}


class _Harness:
    """一处搭好：临时 vault + 临时 sessions/ + 桩复习层。"""

    def __init__(self, review=None, key="k"):
        self.review = review if review is not None else (lambda *a, **k: {})
        self.key = key
        self.seen_during_review: dict = {}

    def run(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.dict(os.environ, {"CLASSLIVE_NOTE_MERGE": "0"}), \
                mock.patch.object(ow, "SESSIONS", pathlib.Path(d) / "sessions"):
            vault = pathlib.Path(d)
            w = ow.ObsidianWriter(str(vault), "TESTX", mode="yes",
                                  api_key=self.key, polish=False)
            w._keypoint_items = lambda *a, **k: []
            w._review_v2 = self._stub(w)
            sp = vault / "s.md"
            sp.write_text(SESSION, encoding="utf-8")
            w.session_path, w._n = sp, 2
            before = sp.read_bytes()
            msg = w.close()
            # ⚠️ 只数 `.md`：`.html` / `.anki.csv` 是 2026-10-17 起的**有意旁车**，
            #    把它们算进"笔记文件数"会让 §2 那条判据（路径只算一次）假红。
            notes = sorted(p.name for p in (vault / "Lectures").glob("*.md")
                           if p.is_file())
            # ⚠️ 旁车文件必须在 **`with tempfile` 里面**数：出了这个块目录就没了
            #    （实测：在用例里 glob -> 永远空，看着像"没写出来"）。
            self.seen_during_review["siblings"] = sorted(
                p.name for p in (vault / "Lectures").glob("*")
                if p.suffix in (".html", ".anki.csv"))
            after = sp.read_bytes()
            text = (note_text(w.vault_path)
                    if w.vault_path and pathlib.Path(w.vault_path).exists() else "")
            return w, msg, text, notes, before, after, self.seen_during_review

    def _stub(self, w):
        def _review(entries, on_progress=None):   # 方法名换了，形状没换
            # ⭐ 关键取样点：复习层**正在跑**（还没返回）时，盘上有什么？
            p = pathlib.Path(w.vault_path) if w.vault_path else None
            self.seen_during_review["exists"] = bool(p and p.exists())
            self.seen_during_review["text"] = (note_text(p)
                                               if self.seen_during_review["exists"] else "")
            time.sleep(0.15)              # 模拟"几十秒"那一段（够主线程/用户切换）
            self.seen_during_review["still_there"] = bool(p and p.exists())
            return self.review(entries, on_progress=on_progress) \
                if callable(self.review) else self.review
        return _review


class BaseLayerFirst(unittest.TestCase):
    def test_复习层还没回来_盘上已有基础版(self):
        h = _Harness(lambda *a, **k: REVIEW)
        w, msg, text, notes, before, after, seen = h.run()
        self.assertTrue(seen["exists"], "复习层开始跑时 Lectures/ 下一个文件都没有")
        self.assertTrue(seen["still_there"], "复习层跑的中途那份笔记不见了")
        base = seen["text"]
        self.assertIn("first one.", base, "基础版里连逐句转录都没有")
        self.assertIn("第一句。", base)
        self.assertNotIn("English point", base, "基础版里不该已经有复习层")

    def test_基础版不说假话(self):
        """⭐ 2026-10-16 OCR：基础版曾把 ❓ 传空（→「课上没按 ❓」）、把有 key 说成「未配 API key」。"""
        h = _Harness(lambda *a, **k: REVIEW)
        orig = h._stub

        def stub(w):
            w._lost_items = lambda entries, explain=True: ["❓LOSTBLOCK"]
            return orig(w)
        h._stub = stub
        w, msg, text, notes, before, after, seen = h.run()
        base = seen["text"]
        self.assertIn("❓LOSTBLOCK", base, "基础版把课上按过的 ❓ 丢了")
        self.assertNotIn("未配 API key", base, "有 key 却写「未配 API key」")

    def test_最终是完整版_只有一个文件(self):
        h = _Harness(lambda *a, **k: REVIEW)
        w, msg, text, notes, before, after, seen = h.run()
        self.assertEqual(notes, [pathlib.Path(w.vault_path).name],
                         "写了不止一个 .md —— 路径算了两遍？")
        self.assertNotIn(".tmp", " ".join(notes), "临时文件没被换走")
        self.assertIn("English point", text, "复习层没并进最终版")
        self.assertIn("本课主题", text)
        self.assertIn("复习层: 讲解+例题+自测+校验清单", msg)
        # 🧩🃏 旁车文件（2026-10-17）：同目录同名，且 Markdown 里要写出它们的名字 ——
        #    写了名字却没写文件（或反过来）都是"假话"。
        sib = seen["siblings"]
        self.assertTrue(sib, "交互版 HTML / Anki CSV 一个都没写出来")
        for name in sib:
            self.assertIn(name, text, f"笔记里没提旁车文件 {name}")

    def test_复习层抛异常_基础版留着且有说明行(self):
        def boom(*a, **k):
            raise RuntimeError("复习层自己崩了")
        h = _Harness(boom)
        w, msg, text, notes, before, after, seen = h.run()
        self.assertTrue(seen["exists"])
        self.assertIn("first one.", text, "复习层崩了，连底座都没了")
        self.assertIn("第二句。", text)
        self.assertIn("复习层这次没生成", text, "没告诉用户怎么补回来")
        self.assertIn("rebuild_note.py", text)
        self.assertIn("复习层这次没生成", msg)
        self.assertEqual(notes, [pathlib.Path(w.vault_path).name])

    def test_复习层返回空_也当没生成(self):
        """桩返回 `{}`（= 模型截断 / 全失败）—— 与抛异常同一条路。"""
        h = _Harness(lambda *a, **k: {})
        w, msg, text, notes, before, after, seen = h.run()
        self.assertIn("复习层这次没生成", text)
        self.assertIn("first one.", text)

    def test_用户取消_基础版留着且有说明行(self):
        import threading
        h = _Harness(lambda *a, **k: REVIEW)
        cancel = threading.Event()
        cancel.set()
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.dict(os.environ, {"CLASSLIVE_NOTE_MERGE": "0"}), \
                mock.patch.object(ow, "SESSIONS", pathlib.Path(d) / "sessions"):
            vault = pathlib.Path(d)
            w = ow.ObsidianWriter(str(vault), "TESTX", mode="yes", api_key="k", polish=False)
            w._keypoint_items = lambda *a, **k: []
            called = []
            w._review_v2 = lambda *a, **k: called.append(1) or REVIEW
            sp = vault / "s.md"
            sp.write_text(SESSION, encoding="utf-8")
            w.session_path, w._n = sp, 2
            w.close(cancel=cancel)
            text = note_text(w.vault_path)
        self.assertEqual(called, [], "用户取消了还去调复习层")
        self.assertIn("first one.", text)
        self.assertIn("复习层这次没生成", text)

    def test_没有key_不说复习层没生成(self):
        """没配 key 时那句话本来就在（"未配 API key"）—— 不该改口说"这次没生成"，
        也不该让人去 rebuild（rebuild 同样没 key）。"""
        h = _Harness(lambda *a, **k: {}, key=None)
        w, msg, text, notes, before, after, seen = h.run()
        self.assertNotIn("复习层这次没生成", text)
        self.assertIn("未配 API key", text)
        self.assertIn("first one.", text)


class CloseMessage(unittest.TestCase):
    """⭐ 第六轮：md 变简版之后，**讲解/例题/变式/自测/图表/转录只在 HTML 里** ——
    下课那一行必须给出它的路径（作者原话：「下课提示里给 HTML 的路径」）。"""

    def test_收尾串给出详细版路径(self):
        h = _Harness(lambda *a, **k: REVIEW)
        w, msg, text, notes, before, after, seen = h.run()
        html = pathlib.Path(w.vault_path).with_suffix(".html")
        self.assertIn(str(html), msg, "收尾串没给详细版路径")
        # ⚠️ 临时目录出了 `run()` 就没了 —— 文件在不在只能看**跑的时候**拍的那张快照。
        self.assertIn(html.name, seen["siblings"], "给了路径却没写文件（那是假话）")

    def test_没写出详细版就不给路径(self):
        """写不出来时**不许编一个路径**（笔记里也要说清楚"详细版这次没生成"）。"""
        import note_html
        h = _Harness(lambda *a, **k: REVIEW)
        with mock.patch.object(note_html, "note_page", side_effect=RuntimeError("x")):
            w, msg, text, notes, before, after, seen = h.run()
        self.assertNotIn(".html: ", msg, "详细版没写出来却给了路径")
        self.assertIn("详细版这次没生成", text)


class WriteEnds(unittest.TestCase):
    def test_sessions字节不变(self):
        h = _Harness(lambda *a, **k: REVIEW)
        w, msg, text, notes, before, after, seen = h.run()
        self.assertEqual(after, before, "会话文件被改写了（三方契约：obsidian_writer 写 / _parse 读 / cl last grep）")
        self.assertEqual(after.decode("utf-8"), SESSION)

    def test_同一天同一课_第二次不会另存一份(self):
        """两段式写完，`Lectures/` 下只能有**一份**笔记（路径只算一次）。"""
        h = _Harness(lambda *a, **k: REVIEW)
        w, msg, text, notes, before, after, seen = h.run()
        self.assertEqual(len(notes), 1, notes)


class RenderLine(unittest.TestCase):
    """`_render_note` 那一行说明：纯函数，直接喂。"""

    def _render(self, **kw):
        w = ow.ObsidianWriter(None, "TESTX", mode="no")
        entries = [{"ts": "10:00:03", "en": "hello", "zh": "你好",
                    "asr": "hello", "star": False}]
        return w._render_note(entries, {}, [], "failed", [], [], {}, **kw)

    def test_说明行只在review_missing时出现(self):
        self.assertIn("复习层这次没生成", self._render(review_missing=True))
        self.assertNotIn("复习层这次没生成", self._render(review_missing=False))
        self.assertIn("未配 API key", self._render(review_missing=False))


if __name__ == "__main__":
    unittest.main(verbosity=2)
