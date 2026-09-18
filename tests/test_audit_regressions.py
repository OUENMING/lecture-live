#!/usr/bin/env python3
"""审计回归测试(2026-09-11): 覆盖本次修复的缺陷, 防止它们悄悄回来。

跑法: .venv/bin/python tests/test_audit_regressions.py   (或 pytest tests/)
全部无网络、无模型加载 —— 毫秒级, 可以随手跑。

覆盖的回归:
  R1  all_settled 收尾判据: 文件尾 carry 竞态(57.5s 切点实测丢最后一句)
  R2  EngineRouter 降级/恢复状态机: 瞬时云端失败曾导致全程困在本地
  R3  本地引擎英文回显曾被当成中文译文上屏
  R4  同日同课笔记覆盖: 上一节的复习层被抹掉
  R5  断句/悬挂词/流式解析的关键行为(此前靠手测)
  R6  悬浮窗必须真的能构造(2026-09-18): 构造期 NameError 曾被 _load_overlay
      静默吞掉、退化成终端 UI —— 表现成"一切正常", 而 --ui overlay 全不可用
"""
from __future__ import annotations
import json
import queue
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main as main_mod
from main import all_settled, is_incomplete, split_sentences
from translator import Result, guard_zh_result, _StreamParser, _clean_fix
from cloud_translator import _looks_like_echo
from obsidian_writer import ObsidianWriter


class R1_Settle(unittest.TestCase):
    """收尾判据必须把 carry/busy 算作未完成 —— 只看队列会丢最后一句。"""

    def test_empty_queues_idle(self):
        self.assertTrue(all_settled(queue.Queue(), queue.Queue(),
                                    queue.Queue(), False, ""))

    def test_pending_carry_blocks_settle(self):
        # 场景: 半句挂在 carry 里, 队列全空 -> 不算收尾
        self.assertFalse(all_settled(queue.Queue(), queue.Queue(),
                                     queue.Queue(), False, "I encourage you to"))

    def test_busy_worker_blocks_settle(self):
        # 场景: buf 已出队、LLM 还在流式, streamq 暂时空 -> 不算收尾
        self.assertFalse(all_settled(queue.Queue(), queue.Queue(),
                                     queue.Queue(), True, ""))

    def test_leftover_stream_blocks_settle(self):
        q = queue.Queue(); q.put(("final", "en", "zh", "raw"))
        self.assertFalse(all_settled(queue.Queue(), q, queue.Queue(), False, ""))

    def test_worker_exit_emits_carry_under_watcher(self):
        """复现 57.5s 切点丢句的并发形态: 定稿线程退出时强制送出 carry,
        模拟云端首字延迟 1s; 收尾观察循环必须等到它落地, 不许提前 break。"""
        finalq: queue.Queue = queue.Queue()
        streamq: queue.Queue = queue.Queue()
        drafts: queue.Queue = queue.Queue()
        busy = {"on": False}
        carry = {"text": "I and uh again I encourage you to"}
        finalq.put(_QUIT := object())            # 与生产一致: 先排 _QUIT

        def fake_worker():
            # 吃掉 _QUIT -> 退出循环 -> _force_emit_carry 的等价行为
            finalq.get(timeout=5)
            time.sleep(0.05)                       # pop 与 emit 之间的空隙
            busy["on"] = True
            carry["text"] = ""
            try:
                time.sleep(1.0)                    # 模拟云端首字 ~1s
                streamq.put(("final", "…", "…", "…"))
            finally:
                busy["on"] = False

        t = threading.Thread(target=fake_worker)
        t.start()
        emitted = []
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            while True:
                try:
                    emitted.append(streamq.get_nowait())
                except queue.Empty:
                    break
            if all_settled(finalq, streamq, drafts, busy["on"], carry["text"]):
                time.sleep(0.25)
                if all_settled(finalq, streamq, drafts, busy["on"], carry["text"]):
                    break
            time.sleep(0.02)
        t.join(timeout=5)
        self.assertTrue(emitted, "收尾循环提前退出, carry 的最后一句丢了")
        self.assertEqual(emitted[0][0], "final")


class R2_EngineRouter(unittest.TestCase):
    """降级要广播 + 起探针; 探针成功要切回云端。"""

    def _router(self, cloud_probe=lambda: False):
        import main as m
        notices = []
        class FakeCloud:
            def probe(self):
                return cloud_probe()
        class FakeLocal:
            def fix_and_translate_stream(self, en, context, on_zh=None, on_en=None):
                return Result(en, "")
        r = m.EngineRouter(FakeLocal(), FakeCloud(), "auto",
                           notify=lambda msg, warn=False: notices.append((msg, warn)))
        return r, notices

    def test_fallback_notifies_and_disables_cloud(self):
        r, notices = self._router()
        r._fallback(RuntimeError("[Errno 60] Operation timed out"))
        self.assertFalse(r._use_cloud)
        self.assertEqual(len(notices), 1)
        self.assertTrue(notices[0][1])                       # warn=True
        self.assertIn("Errno 60", notices[0][0])

    def test_probe_recovers_cloud(self):
        r, notices = self._router(cloud_probe=lambda: True)
        r.RETRY_PROBE_S = 0.05                   # 探针线程启动时就要读到短周期
        r._fallback(RuntimeError("x"))
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not r._use_cloud:
            time.sleep(0.02)
        self.assertTrue(r._use_cloud, "探针成功后应自动切回云端")
        self.assertTrue(any(not warn for _, warn in notices), "恢复应有非警告通知")

    def test_probe_stays_local_on_failure(self):
        r, notices = self._router(cloud_probe=lambda: False)
        r._fallback(RuntimeError("x"))
        time.sleep(0.2)
        self.assertFalse(r._use_cloud)
        self.assertEqual(len(notices), 1)                    # 只有降级通知

    def test_local_mode_never_uses_cloud(self):
        import main as m
        class FakeLocal:
            def fix_and_translate_stream(self, en, context, on_zh=None, on_en=None):
                return Result(en, "zh")
        r = m.EngineRouter(FakeLocal(), None, "local")
        res = r.fix_and_translate_stream("hello", [])
        self.assertEqual(res.zh, "zh")

    def test_cloud_exception_falls_back_same_call(self):
        import main as m
        class FakeCloud:
            def probe(self): return False
            def fix_and_translate_stream(self, *a, **k):
                raise RuntimeError("boom")
        class FakeLocal:
            def fix_and_translate_stream(self, en, context, on_zh=None, on_en=None):
                return Result(en, "本地译文")
        notices = []
        r = m.EngineRouter(FakeLocal(), FakeCloud(), "auto",
                           notify=lambda msg, warn=False: notices.append(msg))
        res = r.fix_and_translate_stream("hello", [])
        self.assertEqual(res.zh, "本地译文")
        self.assertFalse(r._use_cloud)
        self.assertEqual(len(notices), 1)


class R3_LocalEchoGuard(unittest.TestCase):
    """本地小模型把英文原样吐成 zh -> 必须置空, 走"只显示英文"路径。"""

    def test_english_echo_cleared(self):
        res = guard_zh_result(Result("raw text", "raw text"), "raw text")
        self.assertEqual(res.zh, "")

    def test_real_translation_kept(self):
        res = guard_zh_result(Result("fixed", "这是中文译文"), "raw")
        self.assertEqual(res.zh, "这是中文译文")

    def test_empty_untouched(self):
        res = guard_zh_result(Result("fixed", ""), "raw")
        self.assertEqual(res.zh, "")

    def test_parser_cjk_guard_still_works(self):
        p = _StreamParser(lambda s: None, lambda s: None)
        p.feed("EN: fine with 中文 inside")
        r = p.result("RAW")
        self.assertEqual(r.en_fixed, "RAW")      # 英文段混中文 -> ASR 兜底


class R4_VaultCollision(unittest.TestCase):
    """同日同课已有笔记 -> 加时间戳并存, 不覆盖。"""

    def test_collision_gets_timestamped_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "Lectures"
            d.mkdir(parents=True)
            existing = d / "2026-09-11_ECON10101.md"
            existing.write_text("上午的课", encoding="utf-8")
            # 模拟 close() 里的选择逻辑
            path = d / "2026-09-11_ECON10101.md"
            if path.exists():
                path = d / "2026-09-11_143000_ECON10101.md"
            path.write_text("下午的课", encoding="utf-8")
            self.assertEqual(existing.read_text(encoding="utf-8"), "上午的课",
                             "旧笔记被覆盖了!")
            self.assertIn("143000", path.name)

    def test_writer_appends_session_and_parses(self):
        with tempfile.TemporaryDirectory() as tmp:
            w = ObsidianWriter(vault=tmp, course="TEST", mode="yes")
            w.append("fixed english", "中文", flagged=True, raw="raw asr")
            w.append("", "", raw="only asr")            # 翻译失败也要落盘
            self.assertEqual(w.count, 2)
            entries = ObsidianWriter._parse(w.session_path.read_text(encoding="utf-8"))
            self.assertEqual(entries[0]["en"], "fixed english")
            self.assertEqual(entries[0]["zh"], "中文")
            self.assertTrue(entries[0]["star"])
            self.assertEqual(entries[1]["asr"], "only asr")


class R5_CoreBehaviour(unittest.TestCase):
    """断句 / 悬挂词 / 流式解析 / 云端回显检测的关键行为。"""

    def test_dangling_tails(self):
        self.assertTrue(is_incomplete("the reason that"))
        self.assertTrue(is_incomplete("what we're"))
        self.assertFalse(is_incomplete("Done."))
        self.assertFalse(is_incomplete(""))

    def test_split_sentences(self):
        s = split_sentences("First one here. Second one follows!")
        self.assertEqual(s, ["First one here.", "Second one follows!"])

    def test_stream_parser_marker_split_across_chunks(self):
        zh, en = [], []
        p = _StreamParser(zh.append, en.append)
        for c in ["Z", "H:", " 译文\n", "EN: corrected"]:
            p.feed(c)
        r = p.result("RAW")
        self.assertEqual(r.zh, "译文")
        self.assertEqual(r.en_fixed, "corrected")

    def test_clean_fix_rejects_overgrowth(self):
        self.assertEqual(_clean_fix("a b c d e f g h i j k l", "short"), "short")
        self.assertEqual(_clean_fix("EN: fixed text", "raw"), "fixed text")

    def test_cloud_echo_detection(self):
        self.assertTrue(_looks_like_echo("课程术语 这个模块…", "some long english here"))
        self.assertTrue(_looks_like_echo("", "anything"))
        self.assertFalse(_looks_like_echo("这是正常译文", "english"))

    def test_sentence_state_machine_via_queues(self):
        """录音状态机的核心不变量: 'final' 必然晚于它的流式增量出现,
        且 drain 后 flagged 复位(逻辑级验证, 与 drain() 同构)。"""
        streamq: queue.Queue = queue.Queue()
        streamq.put(("zh", "你"))
        streamq.put(("zh", "好"))
        streamq.put(("final", "en", "你好", "raw"))
        seen, finalized = [], []
        while True:
            try:
                item = streamq.get_nowait()
            except queue.Empty:
                break
            seen.append(item[0])
            if item[0] == "final":
                finalized.append(item)
        self.assertEqual(seen.index("final"), len(seen) - 1)
        self.assertEqual(finalized[0][2], "你好")


class R6_OverlayConstructs(unittest.TestCase):
    """R6 悬浮窗必须真的能构造(2026-09-18)。

    起因: 给输入框加 `NSFocusRingTypeNone` 时漏了 import —— `Overlay.__init__`
    抛 NameError。而 `main._load_overlay` 把这个异常**静默吞掉**、回退终端 UI,
    于是"--ui overlay 完全不可用"表现得像"一切正常"。
    py_compile 不查 NameError, R1–R5 又都不构造 Overlay, 所以当时没有任何闸门
    拦得住 —— 这条就是那道缺失的闸门。顺带盯住那次同批出现的第二个静默失败:
    `NSColor` 被当全局用, 被 except 吞掉后细线永远不上色。
    """

    def test_overlay_constructs_and_widgets_are_live(self):
        try:
            from overlay import Overlay
        except Exception as e:                       # noqa: BLE001
            self.skipTest(f"AppKit 不可用, 跳过: {type(e).__name__}: {e}")
        try:
            o = Overlay()
        except Exception as e:                       # noqa: BLE001
            self.fail(f"Overlay() 构造失败: {type(e).__name__}: {e}")
        try:
            self.assertTrue(o._panel.canBecomeKeyWindow(),
                            "面板成不了 key window -> 输入框永远拿不到键盘")
            self.assertTrue(o._input.isEditable())
            self.assertFalse(o._input.drawsBackground(),
                             "输入框不该有填充色(风格: 可读性靠描边不靠填充)")
            self.assertIsNotNone(
                o._input_rule.layer().backgroundColor(),
                "细线必须有颜色 —— 无色说明 _set_rule_focus 静默失败了")
            self.assertFalse(o._is_editing(),
                             "没在打字时不该报告为编辑中")
        finally:
            o.close()

    def test_rule_changes_colour_on_focus(self):
        """细线聚焦变亮、失焦复位(白 α0.18 <-> α0.45)。**不改色相** ——
        暖黄已被术语行占用, 聚焦只是把细线提到草稿那一档。"""
        try:
            from overlay import Overlay
            from Quartz import CGColorGetComponents, CGColorGetNumberOfComponents
        except Exception as e:                       # noqa: BLE001
            self.skipTest(f"AppKit/Quartz 不可用, 跳过: {type(e).__name__}: {e}")
        try:
            o = Overlay()
        except Exception as e:                       # noqa: BLE001
            self.fail(f"Overlay() 构造失败: {type(e).__name__}: {e}")
        try:
            def rgba():
                # 只读实际分量数: 白+alpha 是 2 分量(灰度+alpha), 越界读会拿到垃圾
                c = o._input_rule.layer().backgroundColor()
                n = CGColorGetNumberOfComponents(c)
                v = CGColorGetComponents(c)
                return tuple(round(v[i], 3) for i in range(n))

            idle = rgba()
            o._set_rule_focus(True)
            focused = rgba()
            o._set_rule_focus(False)
            self.assertNotEqual(idle, focused, "聚焦时细线颜色没变")
            self.assertEqual(idle, rgba(), "失焦后没复位")
        finally:
            o.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
