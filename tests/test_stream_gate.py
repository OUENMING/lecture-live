#!/usr/bin/env python3
"""第二阶段接线的判据：`stream_gate.py`（闸 / 段号 / 退回只报一次）+ `main.py` 的接线形状。

    ClassLive.app/Contents/MacOS/python tests/test_stream_gate.py

钉住了什么：
 1. `start_ok`：`CLASSLIVE_STREAM_ASR=off` / 没通行码 / 云端定稿不允许 → **不建**；
    三条都满足才建（`main.py` 那一段判断走的就是这个函数）。
 2. 两道闸：Soniox 活着 → `allow_local()` 为假（`latest.put` / `finalq.put` 都不入队）；
    退回之后 → 为真（下一段起本地照常）。
 3. 退回文案**整节课只给一次**（`notice_text()` 走现有 `("notice", …)` 通道）。
 4. 段号对齐：真 `StreamASR`（`passcode=""`，**不联网**）+ 真采样数 ——
    `seg_of()` 与本地 `seg_gen["n"]` 同一套编号；`buf_s()` 与 `len(buf)/SR` 同口径。
 5. `main.py` 的形状：两道闸在 `_on_partial` / `_utterance_end` 里；
    `final_worker` 先排空 `sealedq` 再走 `_emit`（`_emit` 仍然只有那一个线程调）；
    收尾先 `gate.stop()` 再投 `_QUIT`；收尾判据把 `sealedq` 算进去；不新增 streamq tag。

⚠️ 全程离线：`StreamASR(passcode="")` 走「没通行码」那条路，连 key 都不领；
   HOME 指到临时目录（`final_asr` 的 import 期读端也隔离）。
"""
from __future__ import annotations

import ast
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

_TMP = tempfile.TemporaryDirectory(prefix="cl_stream_gate_test_")
os.environ["HOME"] = _TMP.name
os.environ["CLASSLIVE_LAYOUT"] = "classic"
os.environ.pop("CLASSLIVE_STREAM_ASR", None)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

# ⚠️ 必须在 `import stream_asr` 之前：它 import `final_asr`，那两个函数会碰真 HOME。
mock.patch("final_asr.install_id", return_value="0" * 32).start()
mock.patch("asr_endpoint.version_header", return_value="0.0.0-test").start()

import numpy as np                                                          # noqa: E402

import stream_asr                                                           # noqa: E402
import stream_gate                                                          # noqa: E402
from stream_gate import StreamGate, start_ok                                # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent.parent
MAIN_SRC = (HERE / "main.py").read_text(encoding="utf-8")


def _func(name: str) -> ast.FunctionDef:
    """`main.py` 里某个函数（含嵌套闭包）的 AST 节点。"""
    for node in ast.walk(ast.parse(MAIN_SRC)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"main.py 里找不到 {name}")


class FakeStream:
    """只做「活着 / 退回」这两件事的替身（段号与音频那两件用真 StreamASR 测）。"""

    def __init__(self, *, alive: bool = True) -> None:
        self._alive = alive
        self._reason = ""
        self.stopped = 0

    def alive(self) -> bool:
        return self._alive

    def reason(self) -> str:
        return self._reason

    def notice_text(self) -> str:
        return stream_asr.notice_for(self._reason) if self._reason else ""

    def die(self, reason: str = "network") -> None:
        self._alive = False
        self._reason = reason

    def stop(self) -> None:
        self.stopped += 1
        self._alive = False


class StartOk(unittest.TestCase):
    """开课那一次判断（`main.py` 里 `stream_gate.start_ok(...)` 的入参）。"""

    def setUp(self):
        os.environ.pop("CLASSLIVE_STREAM_ASR", None)

    def tearDown(self):
        os.environ.pop("CLASSLIVE_STREAM_ASR", None)

    def test_开关_off_时不建(self):
        os.environ["CLASSLIVE_STREAM_ASR"] = "off"
        self.assertFalse(start_ok(passcode="cl-x", cloud_ok=True))

    def test_没通行码不建(self):
        for bad in ("", "   ", None):
            self.assertFalse(start_ok(passcode=bad, cloud_ok=True), repr(bad))
        # 自带 DeepSeek key 不是通行码：不建（否则每节课都去 Worker 吃一次 401、弹一次「连不上」）
        self.assertFalse(start_ok(passcode="sk-abc123", cloud_ok=True, wanted=True))

    def test_云端定稿不允许时不建(self):
        self.assertFalse(start_ok(passcode="cl-x", cloud_ok=False))

    def test_三条都满足才建(self):
        self.assertTrue(start_ok(passcode="cl-x", cloud_ok=True))

    def test_off_优先于其它条件(self):
        os.environ["CLASSLIVE_STREAM_ASR"] = "OFF"
        self.assertFalse(start_ok(passcode="cl-x", cloud_ok=True))


class GateFlags(unittest.TestCase):
    """两道闸 + 退回只报一次（状态机，不用真流）。"""

    def test_没建时逐字退化成旧行为(self):
        g = StreamGate(None)
        self.assertFalse(g.on)
        self.assertTrue(g.allow_local())
        self.assertFalse(g.can_feed())               # 没有流 -> 主循环不会去喂（`stream` 是 None）
        self.assertEqual(g.seg_of(12.5), 0)
        self.assertEqual(g.buf_s(), 0.0)
        self.assertEqual(g.draft(), "")
        self.assertIsNone(g.take_notice())
        g.stop()                                     # 不抛、也不该有副作用

    def test_soniox_活着时本地两道闸都关(self):
        g = StreamGate(FakeStream(alive=True))
        self.assertTrue(g.on)
        self.assertFalse(g.allow_local())

    def test_建连中照喂音频但本地照跑(self):
        """`alive()` 还是假（还没接上）时：本地不许停 —— 不然这几秒没有任何字幕。"""
        s = FakeStream(alive=False)
        g = StreamGate(s)
        self.assertTrue(g.allow_local())
        self.assertTrue(g.can_feed())

    def test_退回后下一段开始本地照常(self):
        s = FakeStream(alive=True)
        g = StreamGate(s)
        self.assertFalse(g.allow_local())            # 活着：闸关着
        s.die("quota")
        self.assertTrue(g.allow_local())             # 退回：闸放开，下一段起本地照常
        self.assertTrue(g.take_notice())             # 文案照出
        self.assertTrue(g.allow_local())

    def test_退回通知只发一次(self):
        s = FakeStream(alive=True)
        g = StreamGate(s)
        self.assertIsNone(g.take_notice())           # 还没退回 -> 不报假警
        s.die("network")
        self.assertEqual(g.take_notice(), stream_asr.notice_for("network"))
        self.assertIsNone(g.take_notice())           # 第二次 -> None（只发一次）
        self.assertIsNone(g.take_notice())

    def test_fell_back_只在退回之后为真(self):
        s = FakeStream(alive=False)                  # 建连中：没活但也没退回
        g = StreamGate(s)
        self.assertFalse(g.fell_back)
        s.die("network")
        self.assertFalse(g.fell_back, "还没 take_notice 就算退回了")
        g.take_notice()
        self.assertTrue(g.fell_back)
        self.assertFalse(StreamGate(None).fell_back, "没建流不算退回")

    def test_正常收尾不算退回不发通知(self):
        """`stop()` 之后 `reason()` 是空串（`stream_asr._finish`）—— 不许报假警。"""
        s = FakeStream(alive=True)
        g = StreamGate(s)
        g.stop()
        self.assertEqual(s.stopped, 1)
        self.assertIsNone(g.take_notice())
        self.assertTrue(g.allow_local())

    def test_stop_只调一次且吞异常(self):
        class Boom(FakeStream):
            def stop(self):
                super().stop()
                raise RuntimeError("炸")

        g = StreamGate(Boom(alive=True))
        g.stop()
        g.stop()                                     # 第二次直接返回，不再进流


class AlignmentWithRealStreamASR(unittest.TestCase):
    """段号对齐用**真** `StreamASR`（`passcode=""` = 立刻退回，不联网、不领 key）。

    ⚠️ 每条判据一个**新**流：`mark_segment` 的账本长在流上，共享会把上一条判据的
       采样数与 mark 带进来（第一版就这么写的，当场露馅）。
    """

    def setUp(self):
        self.s = stream_asr.StreamASR(passcode="")     # 后台线程走 no_passcode 那条路
        self.s.stop()                                  # 等它收摊（正常收尾 -> reason 为空）
        self.g = StreamGate(self.s, sr=stream_asr.SR)

    def tearDown(self):
        self.s.stop()

    def _feed_s(self, seconds: float) -> None:
        self.s.feed(np.zeros(int(stream_asr.SR * seconds), dtype="float32"))

    def test_本地段号从_0_开始且跟着_mark_走(self):
        self.g.mark()                                 # 音频开始前：本地第 0 段（main 里也这么做）
        self._feed_s(1.0)
        self.g.mark()                                 # 第 0 段结束 -> 第 1 段开始（1.0 s 处）
        self.assertEqual(self.g.seg_of(0.5), 0)       # 第 0 段的句子在第 0 段
        self.assertEqual(self.g.seg_of(1.0), 1)       # 边界含在新段（与 seg_for_audio 同式）

    def test_同一段里每句一个新号_草稿跟在后面(self):
        """⚠️ 10-17 浮窗实测：同一本地段里的第二句沿用旧号 -> 上一句的草稿中文挂到下一句底下。"""
        from draft_translate import DraftView
        dv = DraftView()
        self.g.mark()
        self._feed_s(5.0)                             # 本地一直是第 0 段
        a = self.g.seg_of(1.0)
        nxt = self.g.draft_seg(0)
        self.assertTrue(dv.on_draft(nxt))             # 下一句的草稿 -> 换句，清旧中文
        b = self.g.seg_of(3.0)                        # 同一本地段的第二句
        self.assertGreater(b, a)
        self.assertTrue(dv.on_draft(self.g.draft_seg(0)), "第二句之后的草稿没触发清中文")

    def test_第一段那句即使封口很晚也不会归错段(self):
        """⚠️ 真踩过的形状：`seg_for_audio` 对"第一个 mark 之前"返回**当时的** `_seg`，
        而 `_seg` 会随后面的 mark 涨 —— 没有起点那个 mark，第一段的句子会被算到第 1 段。"""
        g = self.g
        g.mark()                                   # main 在建完 Segmenter 之后、采音之前打
        self._feed_s(1.0)                          # 第 0 段 = 0–1.0 s
        g.mark()                                   # 第 0 段结束（1.0 s）
        self._feed_s(1.0)
        g.mark()                                   # 第 1 段结束（2.0 s）—— 此时才回来一句 0.5 s 的句子
        self.assertEqual(g.seg_of(0.5), 0)         # 它属于第 0 段
        self.assertEqual(g.seg_of(1.5), 1)
        self.assertEqual(g.seg_of(2.5), 2)

    def test_buf_s_是段起点到当前采到的音频秒数(self):
        self.g.mark()
        self._feed_s(2.0)
        self.assertAlmostEqual(self.g.buf_s(), 2.0, places=3)
        self.g.mark()                                 # 新段起点 = 已采 2.0 s
        self.assertAlmostEqual(self.g.buf_s(), 0.0, places=3)
        self._feed_s(0.5)
        self.assertAlmostEqual(self.g.buf_s(), 0.5, places=3)

    def test_同一段里封过一句之后草稿不会被判过期(self):
        """⚠️ 10-17 端到端露馅的形状：老师一口气讲、本地 VAD 一段 9 s 不断，Soniox 在段中
        封了第一句 -> `fin_seg` = 本段 -> 本段后续草稿全被 `stale_en` 丢掉，屏上 9 s 不动。"""
        from draft_translate import DraftView
        dv = DraftView()
        self.g.mark()
        self._feed_s(3.0)                             # 本地还在第 0 段
        dv.on_draft(self.g.draft_seg(0))
        dv.on_final(self.g.seg_of(2.5))               # 段中封口：第 0 段
        nxt = self.g.draft_seg(0)                     # 下一句的草稿（本地段号仍是 0）
        self.assertFalse(dv.stale_en(nxt))
        self.assertGreater(nxt, 0)
        self.g.mark()                                 # 本地开第 1 段：编号不倒退、不跳号
        self.assertEqual(self.g.draft_seg(1), 1)
        self.assertEqual(StreamGate(None).draft_seg(4), 4)   # 没建流：原样

    def test_草稿只有活着才有(self):
        self.assertEqual(self.g.draft(), "")          # 没活（已经 no_passcode 收尾）
        self.assertFalse(self.g.can_feed())           # reason 非空 -> 不喂
        self.assertTrue(self.g.allow_local())


class MainWiring(unittest.TestCase):
    """`main.py` 的形状（AST；跑不到 `main.run()`）。"""

    def test_两道闸在本地入队之前(self):
        for name in ("_on_partial", "_utterance_end"):
            src = ast.unparse(_func(name))
            self.assertIn("gate.allow_local()", src, f"{name} 里没有闸")
        # `_open_segment` 里要给 Soniox 打段号（段号对齐的唯一来源）
        self.assertIn("gate.mark()", ast.unparse(_func("_open_segment")))

    def test_采音之前先打一次基准段号(self):
        """⚠️ 基线 mark 必须在 `Segmenter` 建好之后、主循环之前（见 `stream_gate.mark`）。"""
        i_seg = MAIN_SRC.index("seg = Segmenter(")
        i_loop = MAIN_SRC.index("while running.is_set() and not stopping.is_set():")
        self.assertIn("gate.mark()", MAIN_SRC[i_seg:i_loop])

    def test_封口句只走_final_worker_的_emit(self):
        src = ast.unparse(_func("final_worker"))
        self.assertIn("sealedq.get_nowait()", src)
        self.assertIn("gate.seg_of", src)
        self.assertIn("_emit", src)
        # 后台回调只入队（不许在 `_sealed_in` 里碰 _emit —— 那是"只许一个线程调 _emit"）
        self.assertNotIn("_emit", ast.unparse(_func("_sealed_in")))

    def test_封口句不按_25_词劈开(self):
        """Soniox 封的是一整句：`_emit` 带 `STREAM_MAX_WORDS`（本地路径仍是默认 25）。"""
        self.assertIn("_emit(_s.text, gate.seg_of(_s.end_s), max_words=STREAM_MAX_WORDS)",
                      MAIN_SRC)
        import main
        sent = "What we're going to do for the rest of the class, for the rest of the semester, " \
               "is we're going to start to talk about how you apply those tools to more realistic situations."
        self.assertEqual(len(main.split_sentences(sent)), 2)
        self.assertEqual(len(main.split_sentences(sent, max_words=main.STREAM_MAX_WORDS)), 1)

    def test_收尾先停流再投哨兵(self):
        # ⚠️ 用整行匹配：`finalq.put(_QUIT)` 在注释里也出现过（讲到哨兵纪律那一段）
        i_stop = MAIN_SRC.index("gate.stop()")
        i_quit = MAIN_SRC.index("\n            finalq.put(_QUIT)")
        self.assertLess(i_stop, i_quit, "`gate.stop()` 必须排在 `_QUIT` 之前，否则 sealedq 里的句子全丢")
        self.assertLess(MAIN_SRC.index("seg.flush()"), i_stop,
                        "`seg.flush()` 要排在停流之前（最后那半句的音频得先进流）")

    def test_收尾判据把_sealedq_算进去(self):
        """两处 `all_settled` 都要 `and sealedq.empty()`（`test_merge_replace` 钉着那两处）。"""
        i = MAIN_SRC.index("deadline = time.monotonic() + 15")
        window = MAIN_SRC[i:i + 1400]
        self.assertEqual(window.count("all_settled("), 2, "收尾冲刷的两处判据都得在")
        self.assertEqual(window.count("sealedq.empty()"), 2, "两处都要把 Soniox 的 sealedq 算进去")

    def test_退回后关掉短尾巴并行(self):
        """退回本地后 `set_tail_merge(False)`：本地按 VAD 出句，开着会把本地短句并掉。"""
        src = ast.unparse(_func("run"))
        i = src.index("gate.take_notice()")
        window = src[i:i + 600]
        self.assertIn("gate.fell_back", window)
        self.assertIn("set_tail_merge(False)", window)

    def test_退回走现有_notice_tag_不新增_tag(self):
        def _tags(node) -> set:
            out = set()
            for n in ast.walk(node):
                if not isinstance(n, ast.Call):
                    continue
                f = n.func
                if not (isinstance(f, ast.Attribute) and f.attr == "put"
                        and isinstance(f.value, ast.Name) and f.value.id == "streamq"):
                    continue
                if n.args and isinstance(n.args[0], ast.Tuple) and n.args[0].elts:
                    first = n.args[0].elts[0]
                    if isinstance(first, ast.Constant):
                        out.add(first.value)
            return out

        self.assertIn("notice", _tags(_func("run")), "退回文案要走现有 notice 通道")
        # ⚠️ 「不新增 tag」由 R28（`tests/test_audit_regressions.py`）钉着：
        #    每个放进 streamq 的 tag 都必须在 drain() 有分支。这里只回一次那个判据 —
        #    本文件新增的 tag（如果有）会是 `_tags(run) - _tags(旧的)` 里的那一个。
        # ⚠️ 2026-10-17：「一些事儿 / 课前回顾」**有意**加了三个 tag
        #    （`duty` / `duty_result` / `recap` / `recap_more`）—— 那是另一个功能的
        #    消息面，不是第二阶段（Soniox）顺手加的。判据的**意图**不变：
        #    第二阶段自己不许新增（下面这条断言把这三个从集合里剔掉再比）。
        # ⚠️ 2026-10-19（3.10.1）：「连着讲就并成一段」**有意**加 `gap`（上一句与这一句之间的
        #    音频停顿，悬浮窗据此决定并不并行）—— 同样是新功能的消息面，drain 有分支（R28）。
        _new_ok = {"duty", "duty_result", "recap", "recap_more", "gap", "ep"}
        before = {"zh", "en", "en_seed", "final", "terms", "notice", "answer",
                  "answer_done", "summary", "hint", "replace"} | _new_ok
        self.assertLessEqual(_tags(_func("run")), before, "第二阶段不许新增 streamq tag")
        # ⚠️ 而新加的那几个**必须**在 drain() 里有分支 —— R28 钉着（这里再点一次名，
        #    免得有人把 `_new_ok` 当成"随便加 tag 的免罪牌"）。
        for _t in _new_ok:
            self.assertIn(_t, before)
        # 形状必须是 (文案, warn, 补充) 三元组（drain 那边按形状拆）
        shape_ok = False
        for node in ast.walk(_func("run")):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "put" and node.args
                    and isinstance(node.args[0], ast.Tuple) and len(node.args[0].elts) == 2):
                head, payload = node.args[0].elts
                if (isinstance(head, ast.Constant) and head.value == "notice"
                        and isinstance(payload, ast.Tuple) and len(payload.elts) == 3
                        and isinstance(payload.elts[1], ast.Constant)
                        and payload.elts[1].value is False):
                    shape_ok = True
        self.assertTrue(shape_ok, "notice 元组形状不对（应为 ('notice', (msg, False, ''))）")

    def test_音频喂在_seg_accept_之前且过闸(self):
        src = ast.unparse(_func("run"))
        self.assertLess(src.index("gate.can_feed()"), src.index("stream.feed("))
        self.assertLess(src.index("stream.feed("), src.index("seg.accept("))

    def test_草稿线程只在建了流时才起(self):
        src = ast.unparse(_func("run"))
        self.assertIn("gate.on else None", src)
        self.assertIn("if ts is not None:", src)

    def test_Soniox_术语本课在前公共表在后(self):
        """⭐⭐ 3.10：Soniox 的 terms = 本课表（前）+ 公共表（后）。

        公共表从前**从来没发给 Soniox**（`glossary.txt` 开头自己写着「5 个课号发音
        极近…必须全列，否则必然听混」——那条对 ASR 同样成立）。顺序就是优先级：
        超量时先保本课词；去重交给 `merge_terms`（唯一定义点）。
        """
        src = ast.unparse(_func("run"))
        self.assertIn("merge_terms(course_term_list(args.glossary, args.course), "
                      "load_terms(args.glossary, None))", src,
                      "terms 不是「本课表 + 公共表」的合并（或顺序反了）")
        i = src.index("stream_asr.StreamASR(")
        self.assertIn("terms=_terms", src[i:i + 400], "合并结果没进 StreamASR")


if __name__ == "__main__":
    unittest.main(verbosity=2)
