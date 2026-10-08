"""draft_stable.DraftStabilizer 的判据。期望值都是手推的。"""
import os, random, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from draft_stable import DraftStabilizer as D


class Basic(unittest.TestCase):
    def test_pure_growth_unchanged(self):
        d = D(holdback=2)
        outs = [d.update(t) for t in ["so the", "so the model", "so the model fits well"]]
        self.assertEqual(outs, ["so the", "so the model", "so the model fits well"])

    def test_tail_change_allowed_inside_holdback(self):
        d = D(holdback=2)
        d.update("the cat sat")
        # LCP("the cat sat","the cat sad on")=2 → 冻结 max(0,2-2)=0，全部可变
        self.assertEqual(d.update("the cat sad on"), "the cat sad on")

    def test_frozen_prefix_resists_later_rewrite(self):
        d = D(holdback=1)
        d.update("one two three four")
        d.update("one two three four five")          # LCP=4 → 冻结 3（one two three）
        out = d.update("won two three for five six")  # 前缀里 1/3 的词被"听错改写"
        # 冻结的 one two three 不变；其后跟最新
        self.assertEqual(out.split()[:3], ["one", "two", "three"])
        self.assertEqual(d.committed, 3)

    def test_punctuation_and_case_follow_latest_inside_frozen(self):
        d = D(holdback=0)
        d.update("well total")
        d.update("well total revenue")               # 冻结 2
        out = d.update("Well, total. revenue is")
        self.assertEqual(out, "Well, total. revenue is")

    def test_random_sequences_invariants(self):
        """任意序列：①输出词数 = 最新假设词数 ②冻结区之后逐词等于最新假设 ③冻结数不越界。"""
        rng = random.Random(11)
        words = "a b c d e f g".split()
        for _ in range(500):
            d = D(holdback=rng.choice([0, 1, 2, 3]))
            for _ in range(rng.randint(1, 8)):
                latest = [rng.choice(words) for _ in range(rng.randint(1, 8))]
                out = d.update(" ".join(latest)).split()
                self.assertEqual(len(out), len(latest))
                self.assertLessEqual(d.committed, len(out))
                self.assertEqual(out[d.committed:], latest[d.committed:])

    def test_output_never_longer_than_latest(self):
        d = D(holdback=1)
        d.update("a b c d e f")
        d.update("a b c d e f g")
        out = d.update("a b")                         # 假设缩短
        self.assertLessEqual(len(out.split()), 2)

    def test_reset_releases_everything(self):
        d = D(holdback=1)
        d.update("a b c d")
        d.update("a b c d e")
        d.reset()
        self.assertEqual(d.update("x y z"), "x y z")
        self.assertEqual(d.committed, 0)

    def test_window_slide_drops_scrolled_words(self):
        d = D(holdback=1)
        d.update("one two three four five six")
        d.update("one two three four five six seven")           # 冻结 6
        out = d.update("three four five six seven eight")        # 窗口滑过去 2 个词
        self.assertEqual(out, "three four five six seven eight")
        self.assertEqual(d.committed, 4)                         # 原冻结 6 − 滚出 2

    def test_unrelated_next_sentence_is_not_dragged_by_old_prefix(self):
        d = D(holdback=1)
        d.update("the price of coffee goes up")
        d.update("the price of coffee goes up by ten")          # 冻结 5
        out = d.update("so let me give you an example")          # 定稿还没到，下一句的草稿先来了
        self.assertEqual(out, "so let me give you an example")
        self.assertEqual(d.committed, 0)

    def test_minority_rewrite_still_frozen(self):
        d = D(holdback=1)
        d.update("the price of coffee goes up")
        d.update("the price of coffee goes up by ten")          # 冻结 5
        out = d.update("the prize of coffee goes up by ten percent")   # 只有 1/5 词位变了
        self.assertEqual(out.split()[:5], ["the", "price", "of", "coffee", "goes"])

    def test_filter_signature_for_eval(self):
        d = D(holdback=2)
        self.assertEqual(d("ignored", "hello there"), "hello there")


class Effect(unittest.TestCase):
    def test_reduces_rewrites_on_noisy_sequence(self):
        """用量具自己的指标证明它有用：一条中间词来回抖的序列，冻结后回改率下降。"""
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
        import stream_metrics as M
        seq = ["the model is", "the model is quite", "the modal is quite good", "the model is quite good for",
               "the muddle is quite good for us", "the model is quite good for us today"]
        raw = M.rewrite_stats(seq, k=0)["rewritten"]
        d = D(holdback=1)
        shown = [d.update(t) for t in seq]
        frozen = M.rewrite_stats(shown, k=0)["rewritten"]
        self.assertGreater(raw, 0)
        self.assertLess(frozen, raw)


if __name__ == "__main__":
    unittest.main()
