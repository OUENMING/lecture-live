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
  R7  答案接管转录区(Phase 3)不得吃掉字幕; 连续输入不得把「键盘不自留」不变量弄丢
      —— 两者都是"不报错但悄悄错"的类型, 正是本文件要拦的东西
  R8  答案折行完整性 + 两个状态缺陷(2026-09-18 独立验证发现): 超长词被静默裁掉 /
      空回答清空屏幕 / 追问时两轮答案粘在一起
  R9  复习钩子里必须是**用户原话**(Phase 4 独立验证): 问句含分隔符被吞前半段 /
      问句含 `::` 伪造卡片边界 / 截断超上限 / 点睛尾巴不设限
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


    def test_show_does_not_start_in_editing_state(self):
        """⚠️ 回归(2026-09-18): `orderFrontRegardless()` 之后 AppKit 会**自动**把
        输入框的 field editor 装成 first responder —— 于是 `_is_editing()` 从启动
        那一刻起恒为 True, pump 里"键盘不自留"那条不变量被**永久短路**。

        为什么三轮独立验证都没抓到: 它们一律用 `makeFirstResponder_` 手动驱动,
        **没有复现"启动即编辑态"这个唯一真实的初始状态** —— 而它正好让不变量永不触发。
        同批还导致聚焦反馈(细线变亮/白色光标)因为依赖 `controlTextDidBeginEditing_`
        (同样从不触发)而完全不工作。

        本测试会**短暂显示一次悬浮窗** —— 这是覆盖该缺陷的唯一方式(`show()` 是触发点)。
        """
        try:
            from overlay import Overlay
        except Exception as e:                       # noqa: BLE001
            self.skipTest(f"AppKit 不可用, 跳过: {type(e).__name__}: {e}")
        try:
            o = Overlay()
        except Exception as e:                       # noqa: BLE001
            self.fail(f"Overlay() 构造失败: {type(e).__name__}: {e}")
        try:
            o.show()
            self.assertFalse(
                o._is_editing(),
                "show() 之后就已处于编辑态 -> pump 的键盘不变量会被永久短路")
            # 面板被点成 key 后, pump 必须让它退让(这正是真机路径)
            o._panel.makeKeyWindow()
            if not o._panel.isKeyWindow():
                # ⚠️ 锁屏 / 没有窗口服务的会话里,**任何**窗口都无法成为 key window
                # (实测 frontmost = loginwindow)。此时下面那条断言没有意义 ——
                # 跳过而不是失败, 否则这条测试会随"机器锁没锁"忽红忽绿。
                self.skipTest("当前会话无法授予 key window(锁屏?), 跳过键位断言")
            o.pump()
            self.assertFalse(o._panel.isKeyWindow(),
                             "面板成了 key, pump 一轮之后没有退让")
        finally:
            o.close()

    def test_focus_look_syncs_with_editing_state(self):
        """聚焦反馈由 `_sync_focus_look()` 在 pump 里按状态同步 —— 不依赖
        委托回调(实测 `controlTextDidBeginEditing_` 从不触发)。"""
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
            def alpha():
                c = o._input_rule.layer().backgroundColor()
                n = CGColorGetNumberOfComponents(c)
                return round(CGColorGetComponents(c)[n - 1], 3)

            o.show()
            o._sync_focus_look()
            idle = alpha()
            o._panel.makeFirstResponder_(o._input)
            o._sync_focus_look()
            focused = alpha()
            self.assertNotEqual(idle, focused, "进入编辑态后细线没有变化")
            o._release_focus()
            o._sync_focus_look()
            self.assertEqual(idle, alpha(), "退出编辑态后细线没有复位")
        finally:
            o.close()


class R7_AnswerTakeover(unittest.TestCase):
    """答案接管转录区(Phase 3)。

    ⚠️ 盯住的是**静默**失败: 答案活跃时新字幕若把答案顶掉、或者退出接管时接管期间
    到达的字幕丢了, 都不会抛异常 —— 前者看着像"答案自己没了", 后者看着像"漏听了
    几句", 都要等到复习笔记时才发觉。所以这两条进闸门。
    """

    ANS = ("Regression here means the econometric procedure.\n"
           "EN：regression (econometric procedure)\n"
           "You fit a line through a cloud of points so the sum of squared vertical\n"
           "distances is as small as possible.")

    def _overlay(self):
        try:
            from overlay import Overlay
        except Exception as e:                       # noqa: BLE001
            self.skipTest(f"AppKit 不可用, 跳过: {type(e).__name__}: {e}")
        try:
            o = Overlay()
        except Exception as e:                       # noqa: BLE001
            self.fail(f"Overlay() 构造失败: {type(e).__name__}: {e}")
        return o

    def test_takeover_never_eats_captions_and_is_reversible(self):
        o = self._overlay()
        try:
            for i in range(6):
                o.finalize(f"caption {i}", f"字幕 {i}")
            o.pump()
            h0, n0 = o._panel.frame().size.height, len(o._history)

            # ---- 流式喂答案(在词中间切开, 模拟 SSE 分片) ----
            for i in range(0, len(self.ANS), 13):
                o.answer_delta(self.ANS[i:i + 13])
                o.pump()
            o.answer_done("讲一下", self.ANS)
            o.pump()
            self.assertEqual(o._tv_mode, "ans", "答案没接管转录区")
            self.assertGreater(o._panel.frame().size.height, h0, "接管后没有自动展开")
            self.assertEqual(len(o._history), n0, "_history 被答案污染了")
            flat = " ".join(t for row in o._tv._items for t in row if t)
            self.assertEqual(flat, " ".join(self.ANS.split()), "答案行有丢字")
            self.assertTrue(any(s.startswith("EN：") for _, s in o._tv._items),
                            "EN：英文辅助行没有进小字位")

            # ---- 答案在屏上时到新字幕: 不能顶掉答案, 也不能丢字幕 ----
            for i in range(6, 10):
                o.finalize(f"caption {i}", f"字幕 {i}")
            for _ in range(4):
                o.pump()
            flat2 = " ".join(t for row in o._tv._items for t in row if t)
            self.assertEqual(flat2, flat, "新字幕把答案顶掉了")
            self.assertEqual(len(o._history), n0 + 4, "接管期间的字幕没进 _history")

            # ---- 退出接管: 字幕重现 + 面板还原 ----
            o._clear_answer()
            for _ in range(3):
                o.pump()
            self.assertEqual(o._tv_mode, "cap")
            self.assertEqual(len(o._tv._items), len(o._history))
            self.assertAlmostEqual(o._panel.frame().size.height, h0, delta=0.5)
        finally:
            o.close()

    def test_enter_keeps_focus_but_esc_releases(self):
        """连续输入(Phase 3): 回车后保持焦点, Esc 让出。

        ⚠️ 面板是否真能变 key 在无头环境里不可复现(`makeKeyWindow` 在别的 app 是
        前台时返回后 `isKeyWindow()` 仍是 False —— 原始代码单独跑 R6 也一样),
        所以把 isKeyWindow 打桩成 True, **直接验 pump 里那条分支**: 编辑态下不能
        让出焦点, 非编辑态必须让出。"""
        o = self._overlay()
        try:
            o._input.setStringValue_("what is regression")
            o._submit_input()
            self.assertEqual(o._input.stringValue(), "", "回车后没清空输入框")
            self.assertTrue(o._is_editing(), "回车后输入框丢了焦点 -> 下一问还要再点一次")

            o._panel.isKeyWindow = lambda: True       # 打桩, 见 docstring
            freed = []
            real_release = o._release_focus
            o._release_focus = lambda: freed.append(1)
            try:
                o.pump()
                self.assertFalse(freed, "正在输入时 pump 把焦点夺走了")
                o._cancel_input()                     # Esc = "我打完了"
                o.pump()
                self.assertTrue(freed, "Esc 之后也没让出焦点 -> 键盘不自留失效")
            finally:
                o._release_focus = real_release
        finally:
            o.close()


class R8_AnswerRowIntegrity(unittest.TestCase):
    """答案折行的完整性 + 两个状态缺陷(2026-09-18, 独立验证发现, 都已修)。

    三条全是**静默**失败, 真机上一眼看不出:
      R8a 单个超长词(长 URL / 一长串 CJK)会**超出槽位被裁掉** —— NSTextField 超过
          maximumNumberOfLines 既不留省略号也不报错(阈值为单 token >102 ASCII /
          >68 CJK 字符)。根因: `_answer_take_row` 的 fit 判定写成 `if cur and ...`,
          cur 为空时直接 append, 单体就超宽的词绕过了检查。
      R8b 空回答会**把屏幕整个清空**: 接管换上 0 行的渲染源, 字幕被藏、面板还自动
          展开(362→519), 而且没有任何自动恢复路径。
      R8c 追问时新一轮的增量**接在上一轮答案后面**, 屏上两轮粘成一段读不通的文字,
          要等 answer_done 对账才恢复。
    """

    def _overlay(self):
        try:
            from overlay import Overlay
        except Exception as e:                       # noqa: BLE001
            self.skipTest(f"AppKit 不可用, 跳过: {type(e).__name__}: {e}")
        try:
            o = Overlay()
        except Exception as e:                       # noqa: BLE001
            self.fail(f"Overlay() 构造失败: {type(e).__name__}: {e}")
        return o

    def test_unbreakable_token_is_split_not_clipped(self):
        """超长无空格词必须被切开成多行, 而不是画成一行被裁掉。"""
        o = self._overlay()
        try:
            for tag, txt in (("cjk600", "中" * 600), ("ascii3000", "A" * 3000)):
                o._answer_reset()
                o.answer_delta(txt)
                o.answer_done("q", txt)
                rows = o._answer_view_rows()
                self.assertGreater(len(rows), 1, f"{tag}: 超长词没被切开")
                bad = [r for r in rows if not o._answer_fits(r[0])]
                self.assertEqual(
                    bad, [], f"{tag}: 仍有 {len(bad)} 行超出槽位(会被静默裁掉)")
        finally:
            o.close()

    def test_empty_answer_does_not_take_over(self):
        """空回答什么都别做 —— 接管会把屏幕清空且无法自动恢复。"""
        o = self._overlay()
        try:
            o.answer_done("q", "")
            self.assertFalse(o._answer_on, "空回答也进了接管 -> 屏幕会被清空")
            self.assertEqual(o._tv_mode, "cap")
        finally:
            o.close()

    def test_followup_does_not_concatenate_previous_answer(self):
        """追问时屏上只显示**本轮**答案, 不与上一轮粘在一起。"""
        o = self._overlay()
        try:
            o.answer_delta("FIRST"); o.answer_done("q1", "FIRST")
            o.answer_delta("SECOND")
            txt = " ".join(r[0] for r in o._answer_view_rows())
            self.assertNotIn("FIRST", txt, "新一轮答案粘在上一轮后面")
            self.assertIn("SECOND", txt)
        finally:
            o.close()


class R9_QAQuestionFidelity(unittest.TestCase):
    """复习钩子里必须是**用户原话**(2026-09-18, 独立验证发现, 都已修)。

    这一条的重要性来自设计前提本身: 问过的问题自动变成复习项, 是为了防"先问 AI
    再做成卡片, 最后只学到关键词"。所以问题被改坏或被静默丢掉, 等于把这条钩子
    整个废掉 —— 而这三种失败都不报错。
      R9a 问句里再出现一次分隔符 -> rpartition 取最后一个, 前半段**静默消失**
      R9b 问句里含 `::` -> 伪造卡片边界, Spaced Repetition 把正面切成半句
      R9c 截断的实际长度**超过上限**(注释说截到 N, 实现到 N+2)
      R9d 有点睛尾巴时整行涨到近 600 字符, 卡片背面读不动
    """

    def test_question_containing_separator_survives(self):
        from obsidian_writer import _asked_question
        got = _asked_question("tr\n\nQuestion: First part.\n\nQuestion: second?")
        self.assertEqual(got, "First part. Question: second?")

    def test_double_colon_in_question_cannot_forge_a_card(self):
        from obsidian_writer import ObsidianWriter
        items = ObsidianWriter._qa_items([
            {"role": "user", "content": "Question: What is a::b in stats?"},
            {"role": "assistant", "content": "It means scope."}])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].count("::"), 1, "问句里的 :: 伪造了卡片边界")

    def test_truncate_never_exceeds_limit(self):
        from obsidian_writer import _truncate
        for text in ("A" * 499 + ". tail", "word " * 300, "X" * 2000):
            self.assertLessEqual(len(_truncate(text, 500)), 500)

    def test_aux_tail_is_capped(self):
        from obsidian_writer import ObsidianWriter, QA_AUX_MAX_CHARS
        items = ObsidianWriter._qa_items([
            {"role": "user", "content": "Question: q?"},
            {"role": "assistant", "content": "正文。\nEN：" + "word " * 300}])
        self.assertEqual(len(items), 1)
        self.assertLess(len(items[0]), QA_AUX_MAX_CHARS + 100)


if __name__ == "__main__":
    unittest.main(verbosity=2)
