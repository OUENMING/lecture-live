#!/usr/bin/env python3
"""审计回归测试(2026-09-11): 覆盖本次修复的缺陷, 防止它们悄悄回来。

跑法: ClassLive.app/Contents/MacOS/python tests/test_audit_regressions.py   (或 pytest tests/)
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
  R10 草稿「2 行 roll-up」的折行与断点(2026-09-27): 一行够却给了两行 /
      丢字从中间挖而不是从前面滑走 / **产出第 3 行**(会被静默吃掉, 不报错) /
      断点不认从句边界 / 断点无滞回(每来一个新词整行就横向跳) /
      容差写死 0.5px(非整数倍的实测高度会把两行判成一行) /
      文本自带换行类字符偷走一行(NEL 与 PARA SEP 单字符就量出两行, 实测)
  R11 草稿标签的 frame 只能有**一个写者**(2026-09-27, OCR 抓出): `_layout` 被
      `_sync_panel_size`(pump 每帧 + live resize 每步)反复调用, 它也写一次 frame 就会
      把 1 行草稿撑回整盒高 → 文字停在**顶行**, 与「贴底」的 roll-up 语义相反
  R12 池扩容的新槽位必须继承**当前档位**的行数(2026-09-27, OCR 抓出): `_add_slot`
      写死 2 行, 而窄窗档位是 3/4/5 行 → 新槽位第 3 行起被**静默裁掉**
  R13 两条护栏(2026-09-27, OCR §8.3): 行距只能由行内三段推出来(双来源会静默
      重叠/留缝) / 超出档位行数的译文必须留下痕迹(原来静默裁掉且不留省略号) /
      **答案接管不许被误判成译文超限**(加完观测器实测到 R8 用例误报过)
  R14 草稿上滚的缓动(2026-09-27) —— ⚠️ carry 判据是**几何的**(位移恰好一个行高),
      不是字符的(原写法拿 CFR §15.119 背书, 但那条规则**不存在**, 见 PLAN-roll-motion §3.4):
      端点精确(不许停中间) / 单调且越界夹取 / 起步快软着陆 / 时长 ≤ 法典上限 0.433s
  R15 ❓「没听懂」的课后时间反查(2026-09-28) —— 本特性的**深度就在这条纯函数里**
      (按下只记一个时刻, 回退范围课后算)。防的是: 锚点取错(取成按下之后那句) /
      起点越界 / 窗口把两端切开(交回半段语音) / 不足下限或越过上限 /
      开课头十几秒按下时崩掉 / **拿字符串直接比大小**(跨午夜会把 00:05 排到 23:50 前)
  R16 实时音源换设备的**半换**防护(2026-09-28): `CallbackSource` 按设备索引绑定,
      而索引会漂(睡一觉/插拔耳机/切默认输入)。第一版 `switch_device` 先关旧流再开
      新流 —— 新设备开不起来就停在半换状态(流没了、索引指向坏设备), 表现成
      「从此再也收不到音频, 而屏上一切正常」。**全部用桩, 不起真设备**。
  R17 更新卡片的「更新看点」(2026-09-28): 卡片按**物理行**取条目, 而看点写长了必然折行
      —— 折行被当成新条目的话, 一条变两条, 且**合并多版本时后半截会被冠上别的版本号**
      (3.7.0 那版实测就是这个症状)。防的是: 缩进行当成新条目 / 上限数的是物理行而不是条目 /
      那条"漏写 `- ` 也认"的容错被顺手弄丢 / 粗体与反引号没剥掉
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
from obsidian_writer import (ObsidianWriter, monotone_secs, note_path_for,
                             resolve_lost_range, _hms_sec)


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
            # ⚠️⚠️ **必须调生产那条**（2026-09-28 审查指出）：原来是测试里
            #    手抄一遍 `if path.exists(): path = …143000…` —— 断言的对象是
            #    **那段副本**，生产把冲突处理删掉/改坏它照样绿（"覆盖感"不是覆盖）。
            path = note_path_for(tmp, "2026-09-11", "ECON10101")
            path.write_text("下午的课", encoding="utf-8")
            self.assertEqual(existing.read_text(encoding="utf-8"), "上午的课",
                             "旧笔记被覆盖了!")
            self.assertNotEqual(path, existing, "同日同课没有分叉，直接覆盖了")
            self.assertIn("ECON10101", path.name)

    def test_writer_appends_session_and_parses(self):
        """⚠️⚠️ **必须连 `SESSIONS` 一起换掉**（2026-09-29 修）—— 这条原来只把
        `vault` 指到临时目录，**没管 `SESSIONS`**，而 `ObsidianWriter.__init__`
        会 `SESSIONS.mkdir()` 再写一个 `<日期>_<时间>_TEST.md`。
        于是每跑一次这个文件，**真 `sessions/` 里就多一个测试残留**。
        实测：2026-09-29 一天里攒了 **91 个** `_TEST.md`。
        ⚠️ 本仓库的硬规矩是「**测试必须隔离写端 —— 读端和写端都要替换**；
           只 patch 读端会写坏真文件」。这里漏的是**写端**。
        ⚠️ 那不是"不小心手滑"能解释的类别：`*_TEST.md` 混在真课堂记录里，
           而 `sessions/` 是**只读不删**的目录（删错过一次，不可恢复）。
        """
        import obsidian_writer as _ow
        with tempfile.TemporaryDirectory() as tmp:
            old_sessions = _ow.SESSIONS
            _ow.SESSIONS = Path(tmp) / "sessions"      # ⚠️ 写端也要换
            try:
                w = ObsidianWriter(vault=tmp, course="TEST", mode="yes")
                w.append("fixed english", "中文", flagged=True, raw="raw asr")
                w.append("", "", raw="only asr")            # 翻译失败也要落盘
                self.assertEqual(w.count, 2)
                self.assertEqual(w.session_path.parent, _ow.SESSIONS,
                                 "会话文件写到真 sessions/ 去了")
                entries = ObsidianWriter._parse(
                    w.session_path.read_text(encoding="utf-8"))
                self.assertEqual(entries[0]["en"], "fixed english")
                self.assertEqual(entries[0]["zh"], "中文")
                self.assertTrue(entries[0]["star"])
                self.assertEqual(entries[1]["asr"], "only asr")
            finally:
                # ⚠️ **还原写端**（进 `finally`：断言失败时也要还回来，
                #    否则后面的用例会继续往临时目录里写 —— 那还算轻的，
                #    真正要防的是"忘了还原"变成下一个人照抄的样板）。
                _ow.SESSIONS = old_sessions


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
        """录音状态机的核心不变量: 'final' 必然晚于它的流式增量出现
        (逻辑级验证, 与 drain() 同构)。
        ⚠️ 从前这里还写着「drain 后 flagged 复位」—— 那个标志位 2026-09-29
           随 ⭐ 并进 ❓ 一起删了（❓ 是直接落盘时间戳，不设标志位），故删去。"""
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
        except ModuleNotFoundError as e:
            self.skipTest(f"AppKit 不可用, 跳过: {e}")
        except Exception as e:                       # noqa: BLE001
            # ⚠️⚠️ **这里不许 skip**（2026-09-28 审查指出）：本用例的**存在理由**
            #    就是拦 `Overlay.__init__` 里那个被 `_load_overlay` 静默吞掉的
            #    `NameError`（漏 import）。而 `except Exception: skipTest` 会把
            #    **任何**导入期错误（含 NameError）都变成"跳过" —— 一道专门防
            #    「NameError 静默」的闸门，自己被同一个形状绕过去了。
            self.fail(f"overlay 导入失败 —— 这不是「AppKit 不可用」，是代码坏了："
                      f"{type(e).__name__}: {e}")
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

    def test_flag_button_merged_into_lost(self):
        """⭐ 2026-09-29 合并：⭐ 按钮没了，它的意思并进 ❓。

        为什么合并：**两个按钮记的是同一件事**（"这一刻值得回头看"），只落两个
        不同的地方 —— 而实测（2026-09-29 重测）`sessions/` 里 **97 份真实课堂
        记录**，按 ⭐ 的一共**只有 3 下**。⚠️ 早先写的是「675 个会话里只有 2 下」：
        `675` 数的是**全目录**（含 600+ 个 `_TEST` 残留），那个数也早已漂移。
        留两个按钮，等于让用户在课上现猜它们的区别。

        ⚠️ **删的只是按下去的那个按钮**：历史会话里的 `⭐ Exam Focus` 抬头仍由
        `obsidian_writer._TS` 照旧渲染，`R4.test_writer_appends_session_and_parses`
        钉住了那个写入格式 —— 所以**不许**顺手把 writer 的能力也删掉。
        """
        try:
            from overlay import Overlay
        except ModuleNotFoundError as e:
            self.skipTest(f"AppKit 不可用, 跳过: {e}")
        except Exception as e:                       # noqa: BLE001
            self.fail(f"overlay 导入失败 —— 这不是「AppKit 不可用」，是代码坏了："
                      f"{type(e).__name__}: {e}")
        try:
            o = Overlay()
        except Exception as e:                       # noqa: BLE001
            self.fail(f"Overlay() 构造失败: {type(e).__name__}: {e}")
        try:
            titles = [b.title() for b in o._bar]
            self.assertNotIn("⭐", titles, f"⭐ 按钮还在，合并没做干净：{titles}")
            self.assertIn("❓", titles, f"❓ 是合并后的落点，不许一起删：{titles}")
            tip = o._btn_lost.toolTip() or ""
            self.assertIn("重点", tip, f"❓ 的 tooltip 该同时说两件事：{tip!r}")
            self.assertIn("没听懂", tip, f"❓ 的 tooltip 该同时说两件事：{tip!r}")
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


def _fake_height(per_line: int):
    """按字数算折行高度的**假尺子**: 10px/行、每行 per_line 字符。

    真尺子是 AppKit 实测(`overlay._measure_text_h`) —— 那要开窗口、要主线程。
    换成假的, `rollup_lines` 的**断点选择**就能不开 AppKit 验, 而"断点挑在哪"
    正是这个函数唯一会悄悄错的地方。"""
    def h(s: str) -> float:
        return 0.0 if not s else -(-len(s) // per_line) * 10.0
    return h


class R10_DraftRollup(unittest.TestCase):
    """草稿「2 行 roll-up」的折行与断点(2026-09-27) —— `overlay.rollup_lines`。

    为什么值得一条独立回归: 这里全是**不报错但悄悄错**。断点挑歪之后
    `NSTextField` 只会把多余的行**静默**吃掉(不留省略号、不报错) ——
    屏上少一句没有任何人会发现。所以只能靠断言钉住, 不能靠"看着对"。

    钉六件事: 一行够就不给两行 · 尾锚定(丢字只从**前面**丢) · **绝不产出第 3 行** ·
    从句边界优先 + 断点滞回 · 容差按行高缩放(写死 0.5px 时非整数倍的实测高度会把
    两行判成一行 → 丢内容) · 文本自带的换行类字符不许偷走一行(NEL / PARA SEP 单字符
    就量出两行, 实测见台账 §8.7)。

    依据与全部一手出处见 `docs/RESEARCH-live-caption-rollup.md §8`。
    """

    def test_one_line_stays_one_line(self):
        """⚠️ 必须**带空格/逗号**的短句也算进来。只用 "x"*20 那种无空白串时,
        候选断点是空的, 走 [ideal] 兜底恰好也只剩一行 —— 于是"砍掉那个提前返回"
        这种变异根本红不了(写这条时实测过)。带空格的短句才会暴露它。"""
        from overlay import rollup_lines
        h = _fake_height(20)
        for t in ("hi", "aa bb cc", "aaaa bbbb cccc", "aaaa,bbbb", "x" * 19, "x" * 20):
            display, base = rollup_lines(t, h, 10.0)
            self.assertNotIn("\n", display, t)
            self.assertEqual(display, t)
            self.assertIsNone(base, "一行够用就不该记底行长度")

    def test_head_slides_off_instead_of_the_middle_being_cut(self):
        from overlay import rollup_lines
        h = _fake_height(20)
        t = " ".join("w%02d" % i for i in range(15))       # 59 字符 = 3 行
        display, _ = rollup_lines(t, h, 10.0)
        self.assertNotIn("w00", display, "最早的字该随窗口滑走")
        flat = display.replace("\n", "").replace(" ", "")
        self.assertTrue(t.replace(" ", "").endswith(flat),
                        f"显示出来的必须是原文的连续后缀, 不能中间被挖掉: {display!r}")

    def test_never_produces_a_third_line(self):
        """⚠️ 这条是写测试时抓出来的**真缺陷**的守卫: 光保证"整段塞得进两行高度"
        是不够的 —— 从中间切开后, 上行放得下不代表下行也放得下。漏了这一步,
        断点稍靠前就会让下行溢出成第 3 行, 而第 3 行会被静默裁掉。"""
        from overlay import rollup_lines
        h = _fake_height(20)
        for n in range(1, 200):
            t = " ".join("w%03d" % i for i in range(n))
            display, _ = rollup_lines(t, h, 10.0)
            self.assertLessEqual(display.count("\n"), 1, f"n={n}: {display!r}")
            for part in display.split("\n"):
                self.assertLessEqual(h(part), 10.5,
                                     f"n={n}: 这一行本身装不下, 会被静默吃掉: {part!r}")

    def test_break_prefers_clause_boundary(self):
        from overlay import rollup_lines
        h = _fake_height(20)
        t = "aaaa bbbb cccc dd,eeee ffff gggg"       # 逗号后**故意没有**空格
        display, _ = rollup_lines(t, h, 10.0)
        self.assertTrue(display.split("\n")[0].endswith(","), display)
        self.assertTrue(display.split("\n")[1].startswith("eeee"), display)

    def test_break_hysteresis_is_not_a_no_op(self):
        """滞回必须**真的**能改变断点, 否则它只是注释里的一句话。

        ⚠️ 第一版实现写成"先按容差过滤候选、再取 max" —— 候选彼此只差几个字符,
        那两个通常同时入选, max 又把结果拉回原处, 等于没做。这条断言就是拦它。"""
        from overlay import rollup_lines
        h = _fake_height(30)
        #               0123456789...
        t = "aaaa bbbb cccc dddd eeee,ffff,gggg hhhh iiii jjjj kkkk"
        plain, _ = rollup_lines(t, h, 10.0)
        seen = {plain}
        for prev in (5, 15, 25, 29, 35, 45):
            seen.add(rollup_lines(t, h, 10.0, prev_base_len=prev)[0])
        self.assertGreater(len(seen), 1,
                           "prev_base_len 完全改变不了结果 -> 滞回是 no-op")
        held, hb = rollup_lines(t, h, 10.0, prev_base_len=29)
        self.assertLess(len(held.split("\n")[0]), len(plain.split("\n")[0]),
                        "上一帧底行很长 -> 断点该往左拉(上行变短)")
        self.assertGreater(hb, 0)
        for d in seen:
            self.assertEqual(d.count("\n"), 1, d)


    def test_two_lines_fit_even_when_the_height_is_not_an_exact_multiple(self):
        """⚠️ 容差必须**按行高缩放**, 不能写死 0.5px。

        判据是"高度 ≤ n × 行高", 而实测高度**当前恰好**总是行高的整数倍 —— 那是这套
        字体与缩放下的巧合, 不是契约(换字号、系统缩放、AppKit 内部取整都能让它变成
        28.6 这种)。写死 0.5px 时 28.6 > 28.5, "两行装得下"就被判成装不下。

        ⚠️ 症状**不是**"退回一行", 而是**多丢一个头**: 后缀窗口缩到更短的那一截,
        短到够两行 —— 行数照样是 2, 但开头那部分白白滚掉了。所以断言的是**留住了
        多少字**, 不是行数(写这条时先按行数断言, 变异红不了, 才改成这个)。"""
        from overlay import rollup_lines

        def h(s):                      # 两行量到 28.6, 不是 28.0
            n = -(-len(s) // 20)
            return {0: 0.0, 1: 14.0, 2: 28.6}.get(n, 43.0)

        t = " ".join("w%02d" % i for i in range(10))       # 39 字符 -> 正好两行
        display, base = rollup_lines(t, h, 14.0)
        self.assertEqual(display.count("\n"), 1, f"两行该折成两行: {display!r}")
        self.assertIsNotNone(base)
        kept = len(display.replace("\n", "").replace(" ", ""))
        want = len(t.replace(" ", ""))
        self.assertEqual(kept, want, f"两行装得下就该一个字不丢: 只留了 {kept}/{want}")


    def test_a_line_break_in_the_text_cannot_steal_the_last_row(self):
        """⚠️ 文本自带的换行类字符不许**偷走一行** —— 排版的行归我们管。

        LF / U+0085(NEL) / U+2028(LINE SEP) / U+2029(PARA SEP) 会让**单个字符**量出
        两行高(2026-09-27 实测: 11pt 下 NEL 与 PARA SEP 都量到 28.00 = 两行)。混在
        文本里时, "最长只占一行的后缀"会变成空串 —— 那条路一走到, 兜底就只能端上
        **整段**(头锚定), 再被 AppKit 裁掉尾巴, 丢的正好是**最新的字**。
        归一化(`" ".join(text.split())`)把这条路封死, 这条判据就是钉它。"""
        from overlay import rollup_lines
        BREAKS = ("\n", "\x85", "\u2028", "\u2029")

        def h(s):                      # 模拟 AppKit: 文本自带换行 = 多占一行
            n = -(-len(s) // 20) + (1 if any(c in s for c in BREAKS) else 0)
            return n * 10.0

        for brk in BREAKS:
            t = "aaa bbb ccc ddd" + brk
            display, _ = rollup_lines(t, h, 10.0)
            self.assertLessEqual(display.count("\n"), 1, f"{brk!r} -> {display!r}")
            self.assertIn("ddd", display, f"{brk!r}: 最新的词被吃掉了 -> {display!r}")


class R11_DraftFrameOneWriter(unittest.TestCase):
    """草稿标签的 frame 只能有**一个**写者(2026-09-27, OCR 抓出)。

    `_layout` 会被 `_sync_panel_size`(**pump 每帧** + live resize 的每一步)反复调用,
    而 `_render_draft` 只在脏标记时跑。所以只要 `_layout` 也写一次 frame, 屏上有 1 行
    草稿时**拖动缩放面板**就会每一步把它撑回**整盒高** → 文字停在**顶行**, 与
    「贴底 + 1→2 行时旧行向上搬」的 roll-up 语义相反, 且一直错到下次草稿更新(~1s)。

    这条判据钉的就是「谁是唯一写者」: `_layout` 之后, 1 行草稿必须**还是 1 行的框**。
    """

    def _ov(self):
        """⚠️ 用完**必须** `close()`: `Overlay.__init__` 末尾会 `_install_status_item()`
        在真菜单栏注册一个 status item —— 不关就是每次测试在菜单栏留一个孤儿 🎧
        (且断言一失败就永远不会被拆)。R6/R7/R8 一律 `try/finally: o.close()`。
        (2026-09-27 OCR 抓出。)
        """
        import overlay
        return overlay.Overlay()

    def test_layout_does_not_inflate_a_one_line_draft(self):
        import overlay
        ov = self._ov()
        try:
            ov.add_draft("hi")                   # 短 -> 只占一行
            one_line_h = overlay.DRAFT_H / overlay.ROLL_LINES
            h1 = ov._draft_lbl.frame().size.height
            self.assertLess(h1, overlay.DRAFT_H - 0.01, f"1 行草稿不该占整盒高: {h1}")
            self.assertAlmostEqual(h1, one_line_h, places=3)
            for i in range(3):                   # 模拟 pump 每帧 / 拖拽缩放的每一步
                ov._layout()
                h = ov._draft_lbl.frame().size.height
                self.assertAlmostEqual(h, h1, places=3,
                                       msg=f"第 {i+1} 次 _layout 把 1 行草稿撑回了整盒高")
            # 贴底: frame 的底边必须就是盒底边(不贴顶)
            self.assertAlmostEqual(ov._draft_lbl.frame().origin.y,
                                   ov._draft_box_y_en, places=3)
        finally:
            ov.close()

    def test_a_two_line_draft_still_fills_the_box(self):
        import overlay
        ov = self._ov()
        try:
            ov.add_draft(" ".join("w%02d" % i for i in range(40)))   # 长 -> 两行
            self.assertAlmostEqual(ov._draft_lbl.frame().size.height,
                                   overlay.DRAFT_H, places=3)
            ov._layout()
            self.assertAlmostEqual(ov._draft_lbl.frame().size.height,
                                   overlay.DRAFT_H, places=3)
        finally:
            ov.close()


class R12_PoolGrowthKeepsTheTierLineCount(unittest.TestCase):
    """池扩容产生的新槽位必须拿到**当前档位**的行数上限(2026-09-27, OCR 抓出)。

    `set_row_metrics` 只遍历**当时**的槽位, 而 `_ensure_pool`(视口变大 / 换大屏时)
    之后还会新增槽位。新槽位若拿写死的 2 行上限, 窄窗(档位 3/4/5 行)下第 3 行起
    就被**静默裁掉**(WordWrapping 不给省略号, 见 overlay.py 文件头的实测记录)——
    与 `overlay.py` 草稿标签那次是同一类缺陷。

    ⚠️ 这条只能靠**真 Overlay** 验: 缺陷在 `_add_slot` 的实参里, 纯函数看不见。
    """

    def test_new_slots_inherit_the_current_tier(self):
        import overlay as O
        ov = O.Overlay()
        try:
            narrow = O.MIN_WIDTH
            ov._width = narrow
            ov._apply_row_metrics(narrow)          # 落到窄档位(>=3 行)
            tier = O._lines_for_width(narrow)
            self.assertGreaterEqual(tier, 3, "用例前提: 这个宽度得落在 >=3 行的档位")
            before = len(ov._tv._slots)
            ov._tv._ensure_pool(3000.0)            # 模拟视口变高 / 换到更大的屏
            fresh = ov._tv._slots[before:]
            self.assertTrue(fresh, "用例前提: 这次要真的扩容")
            got = sorted({int(s["zh"].maximumNumberOfLines()) for s in fresh})
            self.assertEqual(got, [tier],
                             f"新槽位的行数上限应等于当前档位 {tier}, 实际 {got}"
                             " -> 会静默裁掉第 3 行起")
        finally:
            ov.close()


class R13_RowPitchAndOverflowSignal(unittest.TestCase):
    """两条护栏(2026-09-27, OCR §8.3 的 medium + low)。

    ① **行距只有一个来源**: `row_h` 由行内三段(en_h + gap + zh_h)推出来, 不再收
       调用方传的值。同一几何量有两个来源时, 两份漂开的表现是行与行**静默**重叠或
       留缝 —— 没有报错、没有日志, 只能靠肉眼。
    ② **超出档位行数不再静默**: 档位(2/3/4/5)来自一个**闭样本**(overlay.py 对
       4797 句真实中文定稿按宽度取 p100)。样本外的更长译文会被 AppKit 在
       `maximumNumberOfLines` 处**无声**吃掉(不留省略号)。现在会记数 + 首次打一行。
    """

    def _ov(self):
        import overlay
        return overlay.Overlay()

    def test_row_pitch_is_derived_from_the_intra_row_parts(self):
        import overlay as O
        ov = self._ov()
        try:
            for w in (O.MIN_WIDTH, 360.0, 440.0, 620.0, 900.0):   # 覆盖 5/4/3/2 行各档
                ov._width = w
                ov._apply_row_metrics(w)
                tv = ov._tv
                self.assertAlmostEqual(
                    tv._row_h, tv._en_h + tv._gap + tv._zh_h, places=3,
                    msg=f"宽 {w}: 行距与行内三段漂开了 "
                        f"({tv._row_h} vs {tv._en_h + tv._gap + tv._zh_h})")
        finally:
            ov.close()

    def test_overlong_translation_leaves_a_trace(self):
        import overlay as O
        ov = self._ov()
        try:
            ov._width = O.MIN_WIDTH
            ov._apply_row_metrics(O.MIN_WIDTH)
            ov.finalize("short english", "短译文。")
            for _ in range(3):
                ov.pump()
            n0 = ov._tv._overflow_n
            self.assertEqual(n0, 0, "正常长度的译文不该被判超限")
            import contextlib, io
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):        # 别把警告喷进测试输出
                ov.finalize("long english", "这是一句非常长的中文译文，" * 20)
                for _ in range(3):
                    ov.pump()
            self.assertGreater(ov._tv._overflow_n, n0,
                               "超长的译文必须留下痕迹 —— 原来它是**静默**裁掉的")
            self.assertIn("超出档位行数", buf.getvalue(),
                          "除了记数, 首次还该打一行 —— 否则日志里根本看不到")
        finally:
            ov.close()

    def test_answer_takeover_is_not_mistaken_for_an_overlong_translation(self):
        """⚠️ 接管态那一列装的是**中文讲解**, 不是译文 —— 不许拿译文的档位去判它。

        写这条之前**实测**过: R8 那个超长词用例会让观测器误报一次
        ("有译文超出档位行数")。答案内容由 `_answer_view_rows` 自己保证每行放得下,
        是另一套排版。(2026-09-27 抓出。)
        """
        import overlay as O
        ov = self._ov()
        try:
            ov._width = O.MIN_WIDTH
            ov._apply_row_metrics(O.MIN_WIDTH)
            for txt in ("中" * 600, "超长的一段中文讲解文字，" * 30):
                ov._answer_reset()
                ov.answer_delta(txt)
                ov.answer_done("q", txt)
                for _ in range(3):
                    ov.pump()
            self.assertEqual(ov._tv._overflow_n, 0,
                             "接管态被误判成译文超限 —— 那是另一种内容")
        finally:
            ov.close()


class R14_RollEase(unittest.TestCase):
    """草稿上滚的**缓动**是纯函数, 所以判据也是纯的(2026-09-27)。

    ⚠️ 判据 ② 在这里**只能是几何的** —— 理由见 `docs/PLAN-roll-motion.md` §3.4:
    「上滚后上一行字符串逐字不变」**是错的**(CFR §15.119 通篇没有约束"已在上方的行"的
    内容, 负向核查 0 处命中; 而它所有改字符的机制**都作用在游标上**)。
    这里是几何的那一半: **位移恰好一个行高**。
    """

    def test_endpoints_are_exact(self):
        """⭐ 判据 ③ 的纯函数那一半: 终态**恰好**是 1, 不是 0.999。

        动画结束时位移必须精确等于目标值 —— 「停在中间」是
        `PLAN-notes-and-ui.md` §5 记过的**可用性事故**(面板停在中间尺寸挡住字幕)。"""
        from overlay import roll_ease, roll_offset
        self.assertEqual(roll_ease(0.0), 0.0)
        self.assertEqual(roll_ease(1.0), 1.0)
        self.assertEqual(roll_offset(0.0, 16.0), 0.0)
        self.assertEqual(roll_offset(1.0, 16.0), 16.0)     # 恰好一个行高

    def test_monotone_and_clamped(self):
        from overlay import roll_ease
        ys = [roll_ease(i / 200) for i in range(201)]
        self.assertEqual(ys, sorted(ys), "缓动必须单调不减, 否则文字会来回抖")
        self.assertEqual(roll_ease(-0.5), 0.0, "越界要夹取: 动效首尾帧天然会越界")
        self.assertEqual(roll_ease(1.5), 1.0)

    def test_front_loaded(self):
        """选 ease-out 的**理由**: 起步快、软着陆 —— 字幕要「尽快就位然后静止」。

        这是"我认为对"的取舍, 所以钉住它, 免得被无意改成 ease-in(那就变成"慢慢起步、
        突然到位", 与字幕要的相反)。"""
        from overlay import roll_ease
        self.assertGreater(roll_ease(0.5), 0.5, "前半程应当已走完大半")

    def test_duration_within_the_legal_ceiling(self):
        """⭐ 判据 ①: CFR §15.119 的**上限** 0.433s(`[一手]`, 见台账 §2)。"""
        from overlay import ROLL_DURATION_S
        self.assertGreater(ROLL_DURATION_S, 0.0)
        self.assertLessEqual(ROLL_DURATION_S, 0.433)


class R15_LostRange(unittest.TestCase):
    """❓「没听懂」的课后时间反查(2026-09-28)。

    本特性的**深度**就在这条纯函数里: 按下只记一个时刻(T_按下), 「回退到哪几句」
    是课后算的 —— 因为意识到没懂时话已经过去几句了, 再加字幕延迟。
    常数来自**实测**(20 节非测试课 / 5026 句): 回退 15s 覆盖 p05=2 p50=4 p95=7;
    相邻句间隔 ≤3s 占 57%(同一段语音内)、≥8s 占 36%(段与段之间)。

    ⚠️ 这些用例**不引 Thiede 2003 当依据** —— 那是「读完文章延迟写关键词再判断
       学没学会」, 与「回退一段标一下」是两回事(见 `obsidian_writer.LOST_*`)。
    """

    # 按实测的"成簇"形态造: 段内间隔 2s, 段间 12s / 10s
    BURSTY = ["10:00:00", "10:00:02", "10:00:04",
              "10:00:16", "10:00:18",
              "10:00:30", "10:00:32", "10:00:34"]

    @staticmethod
    def _press(hms: str) -> int:
        s = _hms_sec(hms)
        assert s is not None
        return s

    def test_anchor_is_the_last_sentence_at_or_before_the_press(self):
        """锚点必须是**按下之前**最后落盘的那句, 不是之后的。"""
        secs = monotone_secs(self.BURSTY)
        press = self._press("10:00:33")
        i0, i1 = resolve_lost_range(press, secs)
        anchor = max(i for i, s in enumerate(secs) if s <= press)
        self.assertEqual(anchor, 6, "10:00:33 之前最后一句是 idx6(10:00:32)")
        self.assertLessEqual(i0, anchor, "锚点被切掉了")
        self.assertLessEqual(anchor, i1, "锚点被切掉了")
        self.assertLess(i1, len(secs))

    def test_both_ends_snap_to_the_whole_burst(self):
        """⭐ 两端都吸到**整段语音**的边界 —— 不交回半段(实测段内 ≤3s, 段间 ≥8s)。"""
        secs = monotone_secs(self.BURSTY)
        i0, i1 = resolve_lost_range(self._press("10:00:33"), secs)
        self.assertEqual(secs[i0], self._press("10:00:16"),
                         "起点要吸到那一段的头(10:00:16), 不是窗口硬切的 10:00:18")
        self.assertEqual(secs[i1], self._press("10:00:34"))

    def test_window_does_not_over_extend_when_there_is_no_burst(self):
        """没有同段可吸时, 15s 窗口就是 15s(不硬扩)。"""
        secs = monotone_secs(self.BURSTY)
        i0, i1 = resolve_lost_range(self._press("10:00:36"), secs)
        self.assertEqual((i0, i1), (5, 7))

    def test_never_shorter_than_the_floor(self):
        """窗口算出来只有 1 句时, 往前补到下限(实测 p05=2, 慢速段落会更短)。"""
        secs = monotone_secs(["10:00:00", "10:01:00", "10:02:00", "10:03:00"])
        i0, i1 = resolve_lost_range(self._press("10:03:00"), secs)
        self.assertEqual((i0, i1), (1, 3))

    def test_never_longer_than_the_cap_and_shaves_the_old_end(self):
        """⭐ 超上限时**只从旧的那头削** —— 宁可少给一句, 也不能削掉用户真没听懂那句。"""
        secs = monotone_secs([f"10:00:{i * 2:02d}" for i in range(12)])
        i0, i1 = resolve_lost_range(self._press("10:00:22"), secs)
        self.assertEqual(i1, 11, "新那头(锚点)必须保住")
        self.assertEqual(i1 - i0 + 1, 8)

    def test_press_before_the_first_sentence_degrades_to_the_opening(self):
        """开课头十几秒就按了 —— 退化成"开头那几句", 这是真话, 不假装知道更多。"""
        secs = monotone_secs(self.BURSTY)
        i0, i1 = resolve_lost_range(self._press("10:00:01"), secs)
        self.assertEqual(i0, 0)
        self.assertEqual(i1, 2, "同段往后再吸, 但不越过 12s 那个段间空隙")

    def test_start_is_clamped_even_when_the_floor_cannot_be_met(self):
        secs = monotone_secs(["10:00:00", "10:00:30", "10:01:00"])
        self.assertEqual(resolve_lost_range(self._press("10:00:00"), secs), (0, 0))

    def test_empty_returns_none_not_a_crash(self):
        self.assertIsNone(resolve_lost_range(36000, []))
        self.assertIsNone(resolve_lost_range(36000, [], i_max=5))

    def test_anchor_is_inside_the_range_for_every_press(self):
        """不变量: 范围**必定含锚点那句**。

        ⚠️ 锚点这里用**线性扫描**独立算, 不复刻 bisect 那条实现路径 ——
           复刻出来的断言是恒真的(R1/R4 就是这么被点名的)。
        """
        secs = monotone_secs(self.BURSTY)
        for m in range(0, 60, 3):
            press = self._press("10:00:00") + m
            rng = resolve_lost_range(press, secs)
            self.assertIsNotNone(rng)
            i0, i1 = rng
            anchor = 0
            for i, s in enumerate(secs):
                if s <= press:
                    anchor = i
            self.assertLessEqual(i0, anchor, f"press+{m}s: 锚点被切掉了")
            self.assertLessEqual(anchor, i1, f"press+{m}s: 锚点被切掉了")
            self.assertGreaterEqual(i0, 0)
            self.assertLess(i1, len(secs))
            self.assertLessEqual(i1 - i0 + 1, 8)

    def test_midnight_is_monotone(self):
        """⭐ 跨午夜不许把 `00:05` 排到 `23:50` 前面 —— 这正是不能拿字符串比大小的理由。"""
        self.assertEqual(monotone_secs(["23:59:58", "00:00:01"]), [86398, 86401])
        self.assertEqual(monotone_secs(["10:00:00", "00:00:01", "00:00:03"]),
                         [36000, 86401, 86403])

    def test_unparseable_timestamps_are_skipped_not_fatal(self):
        self.assertIsNone(_hms_sec("坏"))
        self.assertIsNone(_hms_sec(None))
        self.assertEqual(monotone_secs(["10:00:00", "坏", "10:00:05"]),
                         [36000, 36005])


    def test_mark_lost_writes_a_well_formed_sidecar_line(self):
        """端到端(临时目录): 按下 -> 旁路文件真的多一行, 且形状对。"""
        with tempfile.TemporaryDirectory() as d:
            w = ObsidianWriter(None, "TESTX", mode="no")
            w.session_path = Path(d) / "s.md"
            w._n = 7
            self.assertTrue(w.mark_lost())
            w.close_lost()
            p = Path(d) / "s.lost.jsonl"
            self.assertTrue(p.exists(), "旁路文件没写出来")
            row = json.loads(p.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(row["kind"], "lost")
            self.assertEqual(row["n"], 7)
            self.assertIsNotNone(_hms_sec(row["t"]), "t 必须是能解出的 HH:MM:SS")

    def test_sidecar_joins_back_to_a_range_and_tolerates_bad_lines(self):
        """旁路事件 -> 那一段句子。坏行(崩坏截断/上一个进程写的)必须被跳过。"""
        with tempfile.TemporaryDirectory() as d:
            w = ObsidianWriter(None, "TESTX", mode="no")
            w.session_path = Path(d) / "s.md"
            (Path(d) / "s.lost.jsonl").write_text(
                json.dumps({"epoch": 0.0, "t": "10:00:33", "n": 8, "kind": "lost"})
                + "\n这不是 JSON\n", encoding="utf-8")
            entries = [{"ts": t, "en": "E", "zh": "Z" + t, "asr": "", "star": False}
                       for t in self.BURSTY]
            items = w._lost_items(entries)
            self.assertEqual(len(items), 1, "坏行不该变成第二条标记")
            self.assertIn("共 5 句", items[0])
            self.assertIn("`10:00:16`", items[0])
            self.assertIn("`10:00:34`", items[0])

    def test_press_before_any_sentence_is_dropped_not_pointed_forward(self):
        """⭐ **真数据抓出来的缺陷**（2026-09-28，作者真按出来的）：

        一段 mic 会话开了 20 秒、一句都还没定稿就按了 ❓ —— 事件里 `n = 0`。
        第一版**没接** `n`（`i_max` 参数在、没人传），于是反查把**第一句**当答案，
        而那一句比按下时刻晚 19 秒 → 「回退到」变成了**往后指**。
        判据: 按下时没有可回退的句子 -> **不编一个出来**。
        """
        with tempfile.TemporaryDirectory() as d:
            w = ObsidianWriter(None, "TESTX", mode="no")
            w.session_path = Path(d) / "s.md"
            (Path(d) / "s.lost.jsonl").write_text(
                json.dumps({"epoch": 1.0, "t": "09:59:40", "n": 0, "kind": "lost"})
                + "\n", encoding="utf-8")
            entries = [{"ts": t, "en": "E", "zh": "Z", "asr": "", "star": False}
                       for t in self.BURSTY]
            self.assertEqual(w._lost_items(entries), [],
                             "按下时一句都没有 -> 不许指向后面才出现的那句")

    def test_close_releases_the_sidecar_handle_on_every_early_return(self):
        """⭐ 答"不保存笔记"时**也必须**关掉旁路句柄。

        2026-09-28 由作者一句"不用保存笔记"提醒才发现：`close()` 有三条早退
        （未启用 / 零句又无问答 / 用户答否），句柄关闭原来放在后面 → 三条路径全漏。
        ⚠️ 那是**当时**的条数；2026-09-29 起是**四条**（多了「没有 Obsidian 库」
        那条）—— **规矩不变：护栏永远挂在早退之前**，所以这条判据照旧有效。
        数据不会丢（每按一次都 flush），但句柄会跟着进程或被后续 close 覆盖而悬着。
        """
        import obsidian_writer as ow
        with tempfile.TemporaryDirectory() as d:
            old = ow.SESSIONS
            ow.SESSIONS = Path(d)              # 隔离: 不往真 sessions/ 写测试会话
            try:
                w = ow.ObsidianWriter(str(d), "TESTX", mode="ask")
                self.assertTrue(w.mark_lost())
                self.assertIsNotNone(w._lost_h, "按过之后句柄该开着")
                w.close(ask=lambda n: False)   # 用户答"不保存" -> 早退
                self.assertIsNone(w._lost_h, "答否时旁路句柄也必须关掉")
            finally:
                ow.SESSIONS = old

    def test_range_is_bounded_by_the_sentence_count_at_press_time(self):
        """范围的上界 = **按下那一刻已定稿的句数**，不许越过它去引用后来的句子。"""
        with tempfile.TemporaryDirectory() as d:
            w = ObsidianWriter(None, "TESTX", mode="no")
            w.session_path = Path(d) / "s.md"
            (Path(d) / "s.lost.jsonl").write_text(
                json.dumps({"epoch": 1.0, "t": "10:00:33", "n": 4, "kind": "lost"})
                + "\n", encoding="utf-8")
            entries = [{"ts": t, "en": "E", "zh": "Z" + t, "asr": "", "star": False}
                       for t in self.BURSTY]
            items = w._lost_items(entries)
            self.assertEqual(len(items), 1)
            self.assertIn("`10:00:16`", items[0], "上界应停在按下时那句(idx3)")
            self.assertNotIn("`10:00:30`", items[0], "不许引用按下之后才落盘的句子")

    def test_render_note_carries_the_block_and_the_count(self):
        """接线: 块要出现, 汇总行数的是**标记**（❓ 兼表重点/没听懂, 2026-09-29）。"""
        w = ObsidianWriter(None, "TESTX", mode="no")
        entries = [{"ts": "10:00:00", "en": "a", "zh": "b", "asr": "", "star": False}]
        out = w._render_note(entries, {}, [], "ok", ["- 一块"])
        self.assertIn("## 🤔 我标的地方（重点 / 没听懂）", out)
        self.assertIn("- 一块", out)
        self.assertIn("❓ 1 处标记", out)

    def test_star_section_renders_only_when_there_are_stars(self):
        """⭐ 按钮 2026-09-29 并进 ❓ 之后, **空 ⭐ 块不许再渲染**。

        空的时候它写的是「*（课上没按 ⭐；觉得哪句重要就按一下）*」——
        那是在叫一个**已经不存在**的按钮（`R6.test_flag_button_merged_into_lost`
        钉住了按钮确实没了）。同一条笔记里还留着 ⭐，等于对着空气说话。
        ⚠️ **历史会话里的 ⭐ 必须照旧渲染**：抬头 `⭐ Exam Focus` 由
        `obsidian_writer._TS` 解析（`R4` 钉住了那个写入格式），
        `rebuild_note.py` 重建时走的是同一条路 —— 所以这里两态都要断。
        """
        w = ObsidianWriter(None, "TESTX", mode="no")
        plain = [{"ts": "10:00:00", "en": "a", "zh": "b", "asr": "", "star": False}]
        self.assertNotIn("## ⭐ 我标记的重点", w._render_note(plain, {}, [], "ok", []),
                         "没按过 ⭐ 却渲染了 ⭐ 块 —— 空状态那句话是假话")
        self.assertNotIn("处重点", w._render_note(plain, {}, [], "ok", []),
                         "汇总行还在数一个恒为 0 的量")

        starred = [dict(plain[0], star=True)]
        out = w._render_note(starred, {}, [], "ok", [])
        self.assertIn("## ⭐ 我标记的重点", out,
                      "历史会话里的 ⭐ 必须照旧渲染（抬头仍写着 Exam Focus）")

    def test_render_note_renders_the_keypoint_section(self):
        """⭐⭐ **非空**那一态必须真的渲染出来 —— 我原来只测了空态。

        2026-09-29 OCR 抓到的真缺陷：`_render_note` 的签名改成 `keypoint_items`
        之后，里面那个循环还写着 `for p, text, ix in kp:` —— `kp` 是上一版
        `_keypoint_items` 里的局部名。于是**只要 Jev 成功**（非空）就 `NameError`，
        而 `close()` 调 `_render_note` **没有 try/except** → **整份笔记写不出来**。

        ⚠️ 教训写在判据里：**只测空态等于没测这一节**。空态走的是 `if` 的假分支，
           而缺陷住在真分支里 —— 本仓库的「判据要指向那个位置」那条的又一例。
        """
        w = ObsidianWriter(None, "TESTX", mode="no")
        entries = [{"ts": "10:00:00", "en": "hello", "zh": "你好", "asr": "",
                    "star": False}]
        out = w._render_note(entries, {}, [], "ok", [], [(0.90, "hello", [0])])
        self.assertIn("## 🎯 这节课最值得记的几句", out, "有重点句却没渲染那一节")
        self.assertIn("`0.90`", out, "概率没写进去")
        self.assertIn("hello", out, "句子没写进去")

    def test_render_note_never_calls_the_keypoints_fn(self):
        """⭐ `_render_note` 是**纯函数**, 取数那一步（会联网）在 `close()` 里。

        2026-09-29 挪的。原来它在 `_render_note` 内部现算 —— 后果是这条判据
        依赖的"纯渲染"契约被悄悄破掉了：改这个函数的人不知道它会发 20 次请求
        （一节课实测 11 秒）。`close():` 那段「五层护栏」的注释也数不出它是第五层。

        ⚠️ 判据钉的是**行为**：把 `keypoints_fn` 换成一个**一调就炸**的替身，
        正常渲染必须一声不吭地过 —— 反过来，只要有人在 `_render_note` 里碰它，
        这条立刻红。
        """
        w = ObsidianWriter(None, "TESTX", mode="no")

        def boom(_sents):
            raise AssertionError("_render_note 里不许调 keypoints_fn（那是联网的）")
        w._keypoints_fn = boom

        entries = [{"ts": "10:00:00", "en": "a", "zh": "b", "asr": "", "star": False}]
        out = w._render_note(entries, {}, [], "ok", [])          # 不许抛
        self.assertNotIn("这节课最值得记的几句", out, "没传 keypoint_items 就不许有那一节")

    def test_keypoint_items_wraps_the_fn_and_survives_failure(self):
        """`_keypoint_items` 是取数那一步 —— 它自己兜错, 还兜得住"没配 token"。

        ⚠️ 三态都要断：
          · 没配（`keypoints_fn is None`）→ 空表，**不去调任何东西**
          · 调了但炸了 → 空表（收尾这一步绝不抛，否则整节课丢笔记）
          · 调了给了值 → 原样带出来，而且要**传句子**（不是 entries）
        """
        w = ObsidianWriter(None, "TESTX", mode="no")
        entries = [{"ts": "10:00:00", "en": "hello", "zh": "你好", "asr": "", "star": False},
                   {"ts": "10:00:05", "en": "", "zh": "", "asr": "", "star": False}]

        # ① 没配 = 功能关着
        self.assertEqual(w._keypoint_items(entries), [], "没配 token 该给空表")

        # ② 传进去的必须是**句子**，且空的 EN 不许混进去
        seen = {}

        def ok(sents):
            seen["sents"] = list(sents)
            return [(0.77, "hello", [0])]
        w._keypoints_fn = ok
        self.assertEqual(w._keypoint_items(entries), [(0.77, "hello", [0])])
        self.assertEqual(seen["sents"], ["hello"],
                         "要传句子、不许带空 EN —— 读法唯一的定义点在 writer._parse")

        # ③ 炸了也要给空表（收尾这一步绝不抛）
        def boom(_sents):
            raise RuntimeError("Jev 502")
        w._keypoints_fn = boom
        self.assertEqual(w._keypoint_items(entries), [], "取数失败不许把笔记带下水")


class R16_DeviceSwitchRollback(unittest.TestCase):
    """实时音源换设备时的**半换**防护(2026-09-28)。

    防的是: 新设备开不起来时把旧流也弄丢了(症状 = 静默收不到音频) /
    同一个索引白开一次流 / 文件源被当成能换设备 / 文件源也去查默认输入。
    ⚠️ 这里**一根真设备都不碰** —— 本文件是默认闸门, 不能绑在"这台机器有麦克风"上。
    """

    @staticmethod
    def _src(name="旧设备"):
        from capture import CallbackSource
        s = CallbackSource.__new__(CallbackSource)     # 绕过 __init__: 不开真流
        s._q = queue.Queue()
        s._device_idx, s._device_name = 1, name
        s._stream = object()
        return s

    def test_same_index_is_a_noop(self):
        src = self._src()
        opened = []
        src._open = lambda: opened.append(1)
        self.assertFalse(src.switch_device(1, "旧设备"))
        self.assertEqual(opened, [], "同一个索引不该白开一次流")

    def test_failed_open_rolls_back_and_keeps_the_old_stream(self):
        """⭐ 本组最要紧的一条。"""
        src = self._src()
        old = src._stream

        def boom():
            raise OSError("PortAudioError: Error querying device 999")
        src._open = boom
        with self.assertRaises(OSError):
            src.switch_device(999, "坏设备")
        self.assertEqual(src._device_idx, 1, "索引必须回滚")
        self.assertEqual(src._device_name, "旧设备")
        self.assertIs(src._stream, old, "旧流必须保住 —— 丢了就是静默收不到音频")

    def test_successful_switch_commits_and_closes_the_old_stream(self):
        src = self._src()
        old = src._stream
        closed = []
        src._open = lambda: setattr(src, "_stream", "新流")
        src._close_stream = lambda s: closed.append(s)
        self.assertTrue(src.switch_device(7, "新设备"))
        self.assertEqual((src._device_idx, src._device_name), (7, "新设备"))
        self.assertEqual(src._stream, "新流")
        self.assertEqual(closed, [old], "成功了才关旧的")

    def test_wrapper_passes_through_and_tolerates_sources_that_cannot_switch(self):
        from capture import _NormalizedSource

        class Can:
            def switch_device(self, i, n=""):
                return True

        class Cannot:
            pass

        self.assertTrue(_NormalizedSource(Can()).switch_device(2, "x"))
        self.assertFalse(_NormalizedSource(Cannot()).switch_device(2, "x"))

    def test_resolve_input_device_rejects_file_sources(self):
        from capture import resolve_input_device
        with self.assertRaises(ValueError):
            resolve_input_device("file")

    def test_check_clock_and_device_reports_a_sleep_and_skips_file_sources(self):
        from main import check_clock_and_device
        msgs: list[str] = []
        switched = []

        class Src:
            def switch_device(self, i, n=""):
                switched.append((i, n))
                return True

        # wall=0 → 与"现在"的差必然 > SLEEP_GAP_S, 所以必然判成睡过一觉
        check_clock_and_device(Src(), "file", lambda m, warn=False: msgs.append(m),
                               {"wall": 0.0})
        self.assertTrue(any("系统休眠唤醒" in m for m in msgs), msgs)
        self.assertEqual(switched, [], "文件源不该去换设备")

    def test_check_clock_and_device_switches_when_the_default_input_moves(self):
        import capture
        from unittest import mock
        from main import check_clock_and_device
        msgs: list[str] = []
        switched = []

        class Src:
            def switch_device(self, i, n=""):
                switched.append((i, n))
                return True

        with mock.patch.object(capture, "resolve_input_device",
                               return_value=(9, "外接麦克风")):
            check_clock_and_device(Src(), "mic", lambda m, warn=False: msgs.append(m),
                                   {"wall": time.time()})
        self.assertEqual(switched, [(9, "外接麦克风")])
        self.assertTrue(any("外接麦克风" in m for m in msgs), msgs)


class R17_ChangelogSummary(unittest.TestCase):
    """更新卡片的「更新看点」（2026-09-28）。

    防的是：**折行的看点裂成两条**。卡片按**物理行**取条目，而看点写长了必然折行
    （源码里缩进两格）—— 当成新条目的话，一条看点变两条，而且**合并多版本时后半截
    会被冠上别的版本号**。`3.7.0` 那版实测就是这个症状：
        `3.6.5 · （⚠️ 现在只认 Markdown，PDF / PPTX 的自动转换还在做）`
    —— 那其实是 3.7.0 某条的后半截。（写 3.8.0 的文案时才发现，之前两个测试的夹具
    都是单行 bullet，正好把这条绕过去了。）
    """

    @staticmethod
    def _summ(body: str, max_lines: int = 7) -> str:
        from unittest import mock
        with mock.patch.object(main_mod, "_changelog_section", return_value=body):
            return main_mod._changelog_summary("X", max_lines)

    def test_indented_line_continues_the_previous_item(self):
        got = self._summ("### 更新看点\n\n"
                         "- 第一条很长很长，长到在源码里折了行\n"
                         "  这是它的后半截\n"
                         "- 第二条\n")
        self.assertEqual(got.splitlines(),
                         ["第一条很长很长，长到在源码里折了行 这是它的后半截", "第二条"])

    def test_max_lines_counts_items_not_physical_lines(self):
        """⚠️ 上限数的是**条目** —— 折行不该吃掉配额。"""
        got = self._summ("### 更新看点\n\n"
                         "- 甲甲甲甲\n  折行一\n"
                         "- 乙乙乙乙\n  折行二\n"
                         "- 丙丙丙丙\n", max_lines=3)
        self.assertEqual(len(got.splitlines()), 3, got)
        self.assertTrue(got.splitlines()[2].startswith("丙丙丙丙"), got)

    def test_unindented_lines_are_still_separate_items(self):
        """⚠️ 容错那条不能丢：漏写 `- ` 的**不缩进**行仍要算独立条目。"""
        got = self._summ("### 更新看点\n\n甲没有减号\n乙也没有\n")
        self.assertEqual(got.splitlines(), ["甲没有减号", "乙也没有"])

    def test_markup_is_stripped(self):
        got = self._summ("### 更新看点\n\n- **粗体**和`代码`\n")
        self.assertEqual(got.splitlines(), ["粗体和代码"])


class R18_AnswerContextContract(unittest.TestCase):
    """问答线程「全库检索并进 prompt」之后，那条**跨模块契约**还在不在（2026-09-28）。

    链路：`main.answer_worker` 拼 user turn → 逐字写回 history →
    `obsidian_writer._asked_question()` 用 `partition("\\n\\nQuestion: ")` **反解**出
    用户原话（落盘"我问过什么"）。`find.as_context()` 给的那段**插在最前面**之后，
    这条契约有两个会静默坏掉的地方：

    1. `Question: ` 那行**不再是最后一段** → 反解把后面的话也当成问题
    2. 检索回来的材料里**恰好含 `"\\n\\nQuestion: "`** → `partition` 取**第一个**匹配，
       于是反解出的是材料里的那句话，**不是你问的**

    ⚠️ 两条都不报错 —— 只是笔记里"我问过什么"从此是错的。所以钉在这里（默认闸门）。
    """

    Q = "上周讲过什么垄断?"
    CTX = "- [ECON10770 · 2026-09-18] 相反，在垄断的情况下。"
    # ⚠️⚠️ **冻结的字面量，不是"再调一次比一比"。**
    #    第一版写的是 `assertEqual(self._content(""), ct.answer_user_content(q, ...))`
    #    —— 那**两次都走同一个（可能已经坏掉的）函数**，于是"给空 context 也塞一段头"
    #    这种变异**两边一起变**，判据照样绿。`/simplify` 之后的变异验证抓出来的
    #    （M5 没红）。要防"老形状被改掉"，参照物必须是**写死的**。
    EXPECTED_NO_CTX = ("Lecture transcript so far (chronological):\n"
                       "first sentence\n\n"
                       f"Question: {Q}")

    def _content(self, context):
        import cloud_translator as ct
        return ct.answer_user_content(self.Q, ["first sentence"], False, context=context)

    def test_question_line_stays_last(self):
        c = self._content(self.CTX)
        self.assertTrue(c.rstrip().endswith(f"Question: {self.Q}"))

    def test_reverse_parse_still_works(self):
        import obsidian_writer as ow
        self.assertEqual(ow._asked_question(self._content(self.CTX)), self.Q)

    def test_empty_context_changes_nothing(self):
        """没有检索结果时，形状必须与**加这个功能之前逐字相同**。"""
        self.assertEqual(self._content(""), self.EXPECTED_NO_CTX)
        self.assertNotIn("Related material", self._content(""))

    def test_context_is_present_when_given(self):
        c = self._content(self.CTX)
        self.assertIn(self.CTX, c)
        self.assertTrue(c.index(self.CTX) < c.index("Question:"))

    def test_context_containing_the_marker_does_not_hijack(self):
        """⚠️ 这条是**真威胁**：材料里出现 `Question: ` 时反解不能认错。

        ⚠️ 已知且**接受**的边界：材料里带**换行 + `Question: `** 时仍会被抢
        （`partition` 取第一个）。检索回来的片段是**单行**的
        （`find.Hit.text` 就是一行），所以现实中构造不出这个形状 ——
        这条只钉"单行里出现 `Question: ` 不抢"。
        """
        import obsidian_writer as ow
        c = self._content("- [x] the professor said: Question: is that clear?")
        self.assertEqual(ow._asked_question(c), self.Q)


    def test_answer_worker_passes_context_to_both(self):
        """⚠️ 这条是**静态的**（读源码），因为它防的那件事**运行时测不到**。

        `answer_worker` 有**两个**调用点要用同一个 `ctx`：
        ① `answer_user_content(..., context=ctx)` → 这段要**逐字**写回 history
        ② `translator.answer(..., context=ctx)` → 真正发出去的那一份
        只要漏掉 ②，模型就**收不到检索材料**，而**不报任何错** ——
        表现成"这个功能好像没生效"，查起来极难。所以要一条静态断言钉住。

        ⚠️ 它**只能**证明"那行 C 里写了 `context=`"，证明不了值对不对。
           运行时那半边由上面几条钉。
        """
        import pathlib
        src = (pathlib.Path(__file__).resolve().parent.parent / "main.py").read_text(
            encoding="utf-8")
        calls = [ln for ln in src.splitlines() if "translator.answer(" in ln]
        self.assertTrue(calls, "main.py 里找不到 translator.answer( 的调用")
        self.assertTrue(any("context=" in ln for ln in calls),
                        f"translator.answer 没把 context 传下去：{calls}")


class R19_OutlineContract(unittest.TestCase):
    """课堂纲要的**状态与合并规则**（计划 §9.2 / §9.4）。

    ⚠️ 这几条**都是"顺序/合并"类**的性质 —— 正是那种"改坏了也照样跑、
       只是结果悄悄错掉"的地方，所以必须钉住：
       · `seen_t` 与快照的**先后**（反了的话「上次看到这里」永远落在最后一行）
       · `deadline` 的**合并判据**（`src` 有交集）
       · 章节**按 id 覆盖**（正式版要顶掉临时版）
       · **只认正式章**才点亮小圆点（临时版每 4 分钟一刷，认它就一直亮）
    """

    def _ov(self):
        try:
            from overlay import Overlay
        except ModuleNotFoundError as e:
            self.skipTest(f"AppKit 不可用, 跳过: {e}")
        except Exception as e:                       # noqa: BLE001
            self.fail(f"overlay 导入失败（不是「AppKit 不可用」，是代码坏了）："
                      f"{type(e).__name__}: {e}")
        try:
            return Overlay()
        except Exception as e:                       # noqa: BLE001
            self.fail(f"Overlay() 构造失败: {type(e).__name__}: {e}")

    def test_deadlines_merge_when_src_overlaps(self):
        o = self._ov()
        o.summary_update({"kind": "deadline", "t": "15:40:16", "src": [10, 11],
                          "quote": "problem set due", "source": "regex", "changed": False})
        o.summary_update({"kind": "deadline", "t": "15:40:20", "src": [11, 12],
                          "quote": "problem set due", "source": "model", "changed": True})
        ds = o._outline["deadlines"]
        self.assertEqual(len(ds), 1, f"`src` 有交集该合成一条，实得 {ds}")
        # ⚠️ `source` **两个都记** —— 两条路都真实发生过，合并成一条说不清来源
        self.assertIn("regex", ds[0]["source"])
        self.assertIn("model", ds[0]["source"])
        self.assertTrue(ds[0]["changed"], "任一条说改期就是改期")
        # 无交集 -> 另起一条
        o.summary_update({"kind": "deadline", "t": "15:50:00", "src": [99],
                          "quote": "other", "source": "regex", "changed": False})
        self.assertEqual(len(o._outline["deadlines"]), 2)

    def test_chapter_overwrites_by_id(self):
        o = self._ov()
        o.summary_update({"kind": "chapter", "chapter": {"id": 3, "status": "interim",
                                                         "title": "临时"}})
        o.summary_update({"kind": "chapter", "chapter": {"id": 3, "status": "final",
                                                         "title": "正式"}})
        got = o._outline["chapters"]
        self.assertEqual(len(got), 1, f"同 id 该覆盖，实得 {got}")
        self.assertEqual(got[3]["status"], "final")

    def test_only_final_chapters_light_the_dot(self):
        o = self._ov()
        o._outline_on = False
        o.summary_update({"kind": "chapter", "chapter": {"id": 0, "status": "interim"}})
        self.assertFalse(o._outline_new, "临时章**不该**点亮小圆点（每 4 分钟一刷 = 一直亮）")
        o.summary_update({"kind": "chapter", "chapter": {"id": 1, "status": "final"}})
        self.assertTrue(o._outline_new)

    def test_open_outline_snapshots_before_updating_seen_t(self):
        """⚠️⚠️ 计划 §9.4 第 2/3 步的**顺序**：**先**生成快照、**再**更新 `seen_t`。

        ⚠️ 判据**不用真实的"现在"** —— 第一版是拿一句 `23:58:00` 去比，
           而跑测试的"现在"是清晨，字典序上 `23:58 > 现在` → **顺序反了也照样有分隔线**，
           于是那条判据**恒真**（我自己的假绿，变异验证抓出来的）。
        ⭐ 改成**在 `_build_rows` 里当场取 `seen_t`**（同判据 ㉘ 的手法）：
           它记下的必须是**旧值**。顺序反了就会记成"现在"。
        """
        o = self._ov()
        o._outline_seen_t = "00:00:01"
        seen_at_build: list = []
        orig = o._build_rows

        def _spy():
            seen_at_build.append(o._outline_seen_t)
            return orig()

        o._build_rows = _spy
        o._open_outline()
        try:
            self.assertEqual(
                seen_at_build, ["00:00:01"],
                "生成快照时 `seen_t` 已经不是旧值了 —— 第 2/3 步的顺序反了，"
                "「上次看到这里」会永远落在最后一行（等于没有）")
        finally:
            o._close_outline()

    def test_biao_ti_button_closes_the_outline(self):
        """⭐⭐ 「字幕」必须能关掉纲要（2026-09-30 修的真 bug，作者实测）。

        ⚠️ 症状：「点字幕有时候不会切到字幕，还是会留在总结页面」。
        根因：`_new_topic` 只调 `_clear_answer()`，而**它不认纲要** ——
        它的早退条件判的是 `_answer_on` / `tv_mode`，`_outline_on` 压根不在它视野里
        → 纲要开着时点「字幕」**什么都不发生**。
        ⭐ 顺带钉住「统一回到最新那句」（原来那是另一个按钮的活）。
        """
        o = self._ov()
        o.summary_update({"kind": "chapter", "chapter": {
            "id": 0, "status": "final", "title": "T", "title_zh": "题",
            "t0": "15:05:27", "t1": "15:16:35", "lo": 1, "hi": 9, "sentences": []}})
        o._panel.orderFrontRegardless()
        o._open_outline()
        self.assertTrue(o._outline_on, "前置：纲要该是开着的")
        o._new_topic()                       # ← 「字幕」按钮走的就是它
        # 改坏：把 `_new_topic` 里 `if self._outline_on: self._close_outline()` 删掉 -> 这条红。
        self.assertFalse(o._outline_on,
                         "点「字幕」之后纲要还开着 —— 正是作者报的那个 bug")
        self.assertEqual(o._tv_mode, "cap", "没切回字幕")
        self.assertTrue(o._tv.following, "「字幕」该统一回到最新那句（跟随=开）")

    def test_toggle_outline_is_a_noop_in_raw_mode(self):
        """⚠️ 纯转录档**根本打不开纲要** —— 与章节条隐藏、菜单项置灰**同一个判据**。"""
        o = self._ov()
        o._trans_mode = "raw"
        o._toggle_outline()
        self.assertFalse(o._outline_on, "纯转录档不该能打开纲要")


class R20_StreamCloseOnFailure(unittest.TestCase):
    """⭐⭐ `CallbackSource._close_stream`：`stop()` 抛了也必须走到 `close()`（2026-09-30 修）。

    ⚠️ 来由（2026-09-28 OCR 发现，09-30 回原码核实为真）：
       原来是 `stream.stop(); stream.close()` 挤在**同一个 `try`** 里 ——
       `stop()` 一抛就**跳过 `close()`**，而 `close()` 才是真正**把设备让出去**
       的那一步（PortAudio 的流不关，麦克风可能一直被占着）。
       `stop()` 抛是现实：设备在睡眠/拔插后被换掉时，回调线程那边的状态已经变了。

    ⚠️ 变异验证：把两句塞回同一个 `try` -> `test_close_runs_even_when_stop_raises` 红。
    """

    class _Bad:
        """`stop()` 必抛、`close()` 正常 —— 模拟设备被抽走那一刻。"""

        def __init__(self):
            self.stopped = False
            self.closed = False

        def stop(self):
            self.stopped = True
            raise RuntimeError("device gone")

        def close(self):
            self.closed = True

    def _cls(self):
        import capture
        return capture.CallbackSource

    def test_close_runs_even_when_stop_raises(self):
        s = self._Bad()
        self._cls()._close_stream(s)
        self.assertTrue(s.stopped, "stop 该被调过")
        self.assertTrue(
            s.closed, "⭐⭐ stop() 抛了也必须走到 close() —— 否则设备一直被占着")

    def test_both_raising_is_swallowed(self):
        """两句都抛也不许冒出去 —— 它跑在**换设备**那条路上，抛了会把换设备整个带崩。"""

        class _Worse(self._Bad):
            def close(self):
                raise RuntimeError("nope")

        self._cls()._close_stream(_Worse())          # 不许抛

    def test_none_is_a_noop(self):
        self._cls()._close_stream(None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
