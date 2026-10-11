import os, sys, unittest

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


class TranslatorRun(unittest.TestCase):
    def test_run_exits_when_event_cleared(self):
        import threading
        f = Fakes(); t = f.make()
        ev = threading.Event()                      # 未 set: 立刻退出
        t.run(ev)


if __name__ == "__main__":
    unittest.main(verbosity=2)
