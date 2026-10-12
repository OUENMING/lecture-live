import os, queue, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import draft_translate as D


class ViewClear(unittest.TestCase):
    """DraftView: 中文草稿槽什么时候该清（纯状态机, 无线程无 I/O）。"""

    def test_new_segment_first_draft_clears_once(self):
        v = D.DraftView()
        self.assertTrue(v.on_draft(3))
        self.assertFalse(v.on_draft(3))             # 同段后续草稿不再清
        self.assertTrue(v.on_draft(4))

    def test_older_segment_draft_never_clears(self):
        v = D.DraftView()
        v.on_draft(5)
        self.assertFalse(v.on_draft(4))

    def test_final_of_current_segment_clears_draft(self):
        v = D.DraftView()
        v.on_draft(2)
        self.assertTrue(v.on_final(2))

    def test_final_of_older_segment_keeps_newer_draft(self):
        v = D.DraftView()
        v.on_draft(2)
        v.on_draft(3)
        self.assertFalse(v.on_final(2))             # 屏上已是第 3 段草稿
        self.assertTrue(v.on_final(3))

    def test_final_without_seg_or_bad_seg_does_not_raise(self):
        v = D.DraftView()
        v.on_draft(1)
        self.assertFalse(v.on_final(None))          # fin_seg 仍 -1, cur_seg=1
        self.assertFalse(v.on_final("x"))
        self.assertEqual(v.fin_seg, -1)

    def test_fin_seg_never_goes_back(self):
        v = D.DraftView()
        v.on_final(5)
        v.on_final(2)
        self.assertEqual(v.fin_seg, 5)


class ViewStaleEn(unittest.TestCase):
    def test_older_segment_or_finalized_is_stale(self):
        v = D.DraftView()
        v.on_draft(3)
        self.assertFalse(v.stale_en(3))
        self.assertFalse(v.stale_en(4))             # 新一段照常
        self.assertTrue(v.stale_en(2))              # 屏上已是第 3 段
        v.on_final(3)
        self.assertTrue(v.stale_en(3))              # 第 3 段定稿已上屏，收尾草稿晚到
        self.assertFalse(D.DraftView().stale_en(0)) # 开课第一份


class ViewAccept(unittest.TestCase):
    def test_accepts_and_records_seq(self):
        v = D.DraftView()
        self.assertTrue(v.accept(1, 0))
        self.assertTrue(v.accept(1, 0))             # 等序号可以(只有 < 才丢)
        self.assertTrue(v.accept(2, 0))
        self.assertEqual(v.seq, 2)

    def test_stale_by_seg(self):
        v = D.DraftView()
        v.on_final(4)
        self.assertFalse(v.accept(1, 4))
        self.assertFalse(v.accept(1, 3))
        self.assertTrue(v.accept(1, 5))
        self.assertEqual(v.stats["stale"], 2)

    def test_stale_when_screen_moved_to_a_newer_segment(self):
        """上一段的译文在**新一段的英文草稿已上屏之后**才回来 → 丢（2026-10-11）。

        `on_draft(新段)` 刚把中文草稿槽清空；这时让旧段译文进来，屏上就是「新段英文 + 旧段中文」，
        要等新段第一条译文（≥6 词、≥2.5 s）才盖掉。旧段还没定稿也一样（fin_seg 判不出来）。"""
        v = D.DraftView()
        v.on_draft(3)
        self.assertTrue(v.accept(1, 3))
        v.on_draft(4)                               # 新一段的英文草稿上屏
        self.assertFalse(v.accept(2, 3))            # seg 3 还没定稿，但屏上已是 seg 4
        self.assertTrue(v.accept(3, 4))

    def test_draft_shows_over_stream_only_when_newer(self):
        """定稿流式期间：草稿是更新一段的 → 显示；是正在定稿那段的 → 藏（防重复）。"""
        v = D.DraftView()
        v.on_draft(3)
        self.assertFalse(v.over_stream())           # 没在流
        v.on_stream(3)
        self.assertFalse(v.over_stream())           # 草稿就是正在定稿那段
        v.on_draft(4)
        self.assertTrue(v.over_stream())            # 讲到下一段了
        v.on_final(3)
        self.assertFalse(v.over_stream())           # 流完了，回到常态
        v.on_stream(None)
        self.assertFalse(v.over_stream())           # 段号未知 → 老行为（藏）

    def test_stale_by_seq(self):
        v = D.DraftView()
        v.accept(5, 0)
        self.assertFalse(v.accept(4, 0))
        self.assertEqual(v.seq, 5)                  # 丢弃不动 seq
        self.assertEqual(v.stats["stale"], 1)

    def test_stats_keys(self):
        self.assertEqual(D.DraftView().stats, {"stale": 0, "yield_local": 0, "errors": 0})


class Fakes:
    def __init__(self, **kw):
        self.out, self.calls = [], []
        self.mode = kw.get("mode", True)
        self.yielding = kw.get("yielding", False)
        self.raises = kw.get("raises", False)
        self.ctx = ["prev final"]
        self.stats = {"stale": 0, "yield_local": 0, "errors": 0}

    def translate(self, text, on_zh, context):
        self.calls.append((text, context))
        if self.raises:
            raise RuntimeError("boom")
        on_zh("中")
        on_zh("中文")

    def make(self):
        return D.DraftTranslator(
            translate=self.translate, output=lambda s, g, z: self.out.append((s, g, z)),
            mode_ok=lambda: self.mode, should_yield=lambda: self.yielding,
            context=lambda: self.ctx, stats=self.stats)


class SubmitKeepsNewest(unittest.TestCase):
    """⭐⭐ `submit()` 顶掉旧项时，**新项必须真的入队**（2026-10-12 全仓审查，核实过）。

    ⚠️ 根因：原来「清旧」和「入新」合在一个 `try` 里 —— `put_nowait` 抛 `Full` 之后，
       消费者线程（`step()` 的 `get(timeout=0.3)`）完全可能刚好把旧项取走，
       于是 `get_nowait()` 抛 `Empty` → 被 `except (queue.Empty, queue.Full)` 吞掉 →
       **新项根本没入队**，而队列此刻是空的、完全放得下。
       结果与「只留最新」的意图**正好相反** —— 丢的偏偏是最新那条。
    """

    @staticmethod
    def _bare():
        return D.DraftTranslator(
            translate=lambda *a, **k: "", output=lambda s, g, z: None,
            mode_ok=lambda: True, should_yield=lambda: False,
            context=lambda: "", stats={})

    def test_new_item_lands_when_consumer_drains_mid_swap(self):
        t = self._bare()
        landed: list = []

        class Q:
            """精确复现那次交错：第 1 次 put 说满、get 说空（消费者刚把旧项取走）、
            第 2 次 put 必须被接受。"""

            def __init__(self):
                self.puts = 0

            def put_nowait(self, item):
                self.puts += 1
                if self.puts == 1:
                    raise queue.Full
                landed.append(item)

            def get_nowait(self):
                raise queue.Empty

        t._q = Q()
        t.submit(7, 3, "最新的那句")
        # 改坏：把「清旧 / 入新」合回一个 try -> landed 为空 -> 这条红。
        self.assertEqual(landed, [(7, 3, "最新的那句")],
                         "⚠️ 新草稿被吞了 —— 与「只留最新」的意图正好相反")


class TranslatorStep(unittest.TestCase):
    def test_empty_queue_step_returns_false(self):
        self.assertFalse(Fakes().make().step(0))

    def test_output_gets_seq_seg_zh_and_context(self):
        f = Fakes(); t = f.make()
        t.submit(7, 2, "hello world")
        self.assertTrue(t.step(0))
        self.assertEqual(f.out, [(7, 2, "中"), (7, 2, "中文")])
        self.assertEqual(f.calls, [("hello world", ["prev final"])])

    def test_latest_only_replaces_oldest(self):
        f = Fakes(); t = f.make()
        t.submit(1, 0, "old"); t.submit(2, 0, "new")
        t.step(0)
        self.assertEqual([c[0] for c in f.calls], ["new"])
        self.assertFalse(t.step(0))

    def test_mode_off_skips(self):
        f = Fakes(mode=False); t = f.make()
        t.submit(1, 0, "x y z")
        t.step(0)
        self.assertEqual((f.calls, f.out), ([], []))

    def test_empty_text_skips(self):
        f = Fakes(); t = f.make()
        t.submit(1, 0, "")
        t.step(0)
        self.assertEqual(f.calls, [])

    def test_yield_counts_and_skips(self):
        f = Fakes(yielding=True); t = f.make()
        t.submit(1, 0, "x y z")
        t.step(0)
        self.assertEqual(f.calls, [])
        self.assertEqual(f.stats["yield_local"], 1)

    def test_exception_counts_error_and_does_not_raise(self):
        f = Fakes(raises=True); t = f.make()
        t.submit(1, 0, "x y z")
        t.step(0)
        self.assertEqual(f.stats["errors"], 1)

    def test_malformed_item_is_dropped(self):
        f = Fakes(); t = f.make()
        t._q.put_nowait("bare string")
        self.assertTrue(t.step(0))
        self.assertEqual(f.calls, [])


class SealedLock(unittest.TestCase):
    """⭐ 封口句的中文译一次就锁住（3.9.4）。

    数据（2026-10-12 SOC10020 真课）：英文封口前缀在后续草稿里保留 1302/1302，
    而中文首句在下一版译文里被改写 59/99 —— 闪的只是整段重译带出来的措辞。
    """

    def setUp(self):
        self.calls, self.out = [], []
        self.bad = set()
        self.boom = set()

        def tr(en, on_zh, ctx):
            self.calls.append((en, list(ctx)))
            if en in self.boom:
                raise RuntimeError("本地模型没装（可选）")
            if en not in self.bad:
                on_zh(f"<{en}>")
        self.t = D.DraftTranslator(
            translate=tr, output=lambda s, g, z: self.out.append((s, g, z)),
            mode_ok=lambda: True, should_yield=lambda: False,
            context=lambda: ["prev final"], stats={"stale": 0, "yield_local": 0, "errors": 0})

    def go(self, seq, seg, text):
        self.calls.clear()
        self.t.submit(seq, seg, text)
        self.t.step(0)
        return sorted(self.calls), self.out[-1]

    def test_sealed_prefix(self):
        self.assertEqual(D.sealed_prefix("A b. C d? E"), "A b. C d?")
        self.assertEqual(D.sealed_prefix("A b."), "")          # 句号后面还没词 = 没封口
        self.assertEqual(D.sealed_prefix("no punct here"), "")
        self.assertEqual(D.sealed_prefix(""), "")

    def test_first_seal_translates_sentence_and_tail_then_locks(self):
        calls, out = self.go(1, 0, "A b c. D e")
        self.assertEqual(calls, [("A b c.", ["prev final"]), ("D e", ["A b c."])])
        self.assertEqual(out, (1, 0, "<A b c.><D e>"))
        calls, out = self.go(2, 0, "A b c. D e f")           # 只译尾巴，前半句中文原样
        self.assertEqual(calls, [("D e f", ["A b c."])])
        self.assertEqual(out, (2, 0, "<A b c.><D e f>"))

    def test_tail_that_raises_does_not_drop_the_sealed_sentence(self):
        self.boom.add("D e")
        _, out = self.go(1, 0, "A b c. D e")
        self.assertEqual(out, (1, 0, "<A b c.>"), "尾巴抛了：封口句已经译好，照样上屏并锁住")
        self.boom.clear()
        calls, out = self.go(2, 0, "A b c. D e f")
        self.assertEqual(calls, [("D e f", ["A b c."])], "锁还在 → 只译尾巴")
        self.assertEqual(out, (2, 0, "<A b c.><D e f>"))

    def test_sealed_that_raises_falls_back_without_locking(self):
        self.boom.add("A b c.")
        _, out = self.go(1, 0, "A b c. D e")
        self.assertEqual(out, (1, 0, "<A b c. D e>"), "封口句抛了 → 不锁，这一轮整段译")
        self.assertEqual(self.t._lock["en"], "")

    def test_second_seal_only_translates_the_new_sentence(self):
        self.go(1, 0, "A b c. D e")
        calls, out = self.go(2, 0, "A b c. D e f. G")
        self.assertEqual(calls, [("D e f.", ["A b c."]), ("G", ["D e f."])])
        self.assertEqual(out, (2, 0, "<A b c.><D e f.><G>"))

    def test_draft_no_longer_starts_with_lock_falls_back_to_whole(self):
        self.go(1, 0, "A b c. D e")
        calls, out = self.go(2, 0, "e f g h")                 # 滑窗丢了段首，也没有封口句
        self.assertEqual(calls, [("e f g h", ["prev final"])])
        self.assertEqual(out, (2, 0, "<e f g h>"))

    def test_new_segment_resets_lock(self):
        self.go(1, 0, "A b c. D e")
        calls, _ = self.go(2, 1, "D e f")
        self.assertEqual(calls, [("D e f", ["prev final"])])

    def test_failed_seal_translation_does_not_lock(self):
        self.bad.add("A b c.")
        calls, out = self.go(1, 0, "A b c. D e")
        self.assertIn(("A b c. D e", ["prev final"]), calls)  # 退回整段译
        self.assertEqual(out, (1, 0, "<A b c. D e>"))
        self.bad.clear()
        calls, _ = self.go(2, 0, "A b c. D e f")              # 下一轮再试着锁
        self.assertIn(("A b c.", ["prev final"]), calls)

    def test_nothing_new_after_lock_shows_locked_only(self):
        self.go(1, 0, "A b c. D e")
        self.go(2, 0, "A b c. D e. F")
        calls, out = self.go(3, 0, "A b c. D e. ")
        self.assertEqual(calls, [])
        self.assertEqual(out, (3, 0, "<A b c.><D e.>"))


class TranslatorRun(unittest.TestCase):
    def test_run_exits_when_event_cleared(self):
        import threading
        f = Fakes(); t = f.make()
        ev = threading.Event()                      # 未 set: 立刻退出
        t.run(ev)


if __name__ == "__main__":
    unittest.main(verbosity=2)
