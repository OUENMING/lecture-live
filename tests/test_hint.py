#!/usr/bin/env python3
"""❓ 轻点拨（计划 `docs/PLAN-r1-translation-gap.md` §G1）的判据 —— **全离线**，不发请求。

    CLASSLIVE_UPLOAD=0 ClassLive.app/Contents/MacOS/python tests/test_hint.py

## 钉住什么

1. `CloudTranslator.hint`：多行只取第一行、去首尾引号、超长截断 —— 它的结果要塞进
   悬浮窗**术语行那一格**（单行、截尾），多行 / 带引号 / 过长都会把那一格撑坏。
2. ⭐ `EngineRouter.hint`：本地引擎 → `""`；云端抛错 → `""` **且 `_use_cloud` 仍为真**。
   和 `answer` 同一个决定：一次点拨失败不能把整节课的翻译降到本地。
3. `Overlay`：`show_hint` 占术语行、到期自动回来、点术语行时只收 hint 不钉术语、
   `_lost()` 按 `on_lost` 的返回值显示两种字。

⚠️ 测 Overlay 的几条在没有 AppKit 时 skip（不是 fail）；导入期的其它错误照样 fail
   —— 理由同 `test_audit_regressions` 里那条（别让 skip 吞掉 NameError）。
"""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main as main_mod
from cloud_translator import CloudTranslator


def _cloud(chunks):
    """一个 `_stream_chat` 被替身掉的云端翻译器（不联网）。"""
    ct = CloudTranslator("k", "m")
    ct._stream_chat = lambda msgs, max_tokens: iter(chunks)      # type: ignore[method-assign]
    return ct


class CloudHint(unittest.TestCase):
    def test_takes_first_line_and_strips_quotes(self):
        ct = _cloud(["“老师在讲", "边际成本。”", "\n多余的第二行"])
        self.assertEqual(ct.hint(["Marginal cost is the cost of one more unit."]),
                         "老师在讲边际成本。")

    def test_long_text_is_truncated_with_ellipsis(self):
        out = _cloud(["字" * 100]).hint(["x"])
        self.assertEqual(len(out), 61)
        self.assertTrue(out.endswith("…"))

    def test_system_has_domain_and_lines_are_the_user_message(self):
        seen = {}

        def fake(msgs, max_tokens):
            seen["msgs"], seen["max"] = msgs, max_tokens
            return iter(["好"])
        ct = CloudTranslator("k", "m", domain="ECON101 Micro", course_terms=["elasticity"])
        ct._stream_chat = fake                                    # type: ignore[method-assign]
        ct.hint(["line one", "line two"])
        self.assertEqual(seen["max"], 80)
        self.assertIn("Course: ECON101 Micro", seen["msgs"][0]["content"])
        self.assertIn("elasticity", seen["msgs"][0]["content"])
        self.assertEqual(seen["msgs"][1]["content"], "line one\nline two")


class RouterHint(unittest.TestCase):
    def _router(self, cloud, mode="cloud"):
        return main_mod.EngineRouter(object(), cloud, mode)

    def test_local_returns_empty(self):
        r = self._router(None, mode="local")
        self.assertFalse(r.uses_cloud)
        self.assertEqual(r.hint(["a"]), "")

    def test_cloud_failure_returns_empty_and_does_not_fall_back(self):
        class Boom:
            def hint(self, lines):
                raise RuntimeError("上下文超限")
        r = self._router(Boom())
        self.assertTrue(r.uses_cloud)
        self.assertEqual(r.hint(["a"]), "")
        self.assertTrue(r.uses_cloud, "点拨失败把整节课的翻译降级到本地了")

    def test_cloud_success_passes_through(self):
        class Ok:
            def hint(self, lines):
                return "一句话"
        self.assertEqual(self._router(Ok()).hint(["a"]), "一句话")


class OverlayHint(unittest.TestCase):
    def _overlay(self, **kw):
        try:
            from overlay import Overlay
        except ModuleNotFoundError as e:
            self.skipTest(f"AppKit 不可用, 跳过: {e}")
        try:
            return Overlay(**kw)
        except Exception as e:                                   # noqa: BLE001
            self.fail(f"Overlay() 构造失败: {type(e).__name__}: {e}")

    def test_hint_takes_gloss_row_and_expires(self):
        o = self._overlay()
        o.terms([("elasticity", "弹性")])
        self.assertIn("elasticity", o._gloss_text())
        o.show_hint("💡 讲边际成本", ttl=15)
        self.assertEqual(o._gloss_text(), "💡 讲边际成本")
        o._hint = ("💡 讲边际成本", time.monotonic() - 1)          # 到期
        o.pump()
        self.assertIsNone(o._hint)
        self.assertIn("elasticity", o._gloss_text(), "到期后术语行没回来")

    def test_empty_text_clears(self):
        o = self._overlay()
        o.show_hint("x", ttl=15)
        o.show_hint("", ttl=15)
        self.assertIsNone(o._hint)

    def test_click_on_gloss_only_clears_hint(self):
        o = self._overlay()
        o.terms([("elasticity", "弹性")])
        o.show_hint("💡 x", ttl=15)
        o._on_gloss_click()
        self.assertIsNone(o._hint)
        self.assertIsNone(o._pinned_term, "hint 显示时点一下不该钉术语")
        o._on_gloss_click()                                       # 再点才是钉术语
        self.assertIsNotNone(o._pinned_term)

    def test_lost_shows_two_variants_by_return_value(self):
        queued = self._overlay(on_lost=lambda: True)
        queued._lost()
        self.assertIn("正在提炼", queued._gloss_text())
        plain = self._overlay(on_lost=lambda: None)
        plain._lost()
        self.assertIn("课后笔记里能找到", plain._gloss_text())


if __name__ == "__main__":
    unittest.main()
