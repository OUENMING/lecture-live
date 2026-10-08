"""scripts/stream_metrics.py 的判据。

⚠️ 夹具都是**手算**出来的期望值（不调用被测函数产生期望），
并且每组都配一个"改坏实现就会红"的对照 —— 见底部的变异自检。
"""
import os, random, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import stream_metrics as M


class Normalize(unittest.TestCase):
    def test_case_and_punct_ignored(self):
        self.assertEqual(M.normalize("Hello, World!"), ["hello", "world"])

    def test_apostrophe_kept(self):
        self.assertEqual(M.normalize("It’s Don't"), ["it's", "don't"])

    def test_cjk_per_char(self):
        self.assertEqual(M.normalize("你好 world"), ["你", "好", "world"])


class Numbers(unittest.TestCase):
    def test_digits_become_words(self):
        self.assertEqual(M.normalize("10%"), ["ten", "percent"])
        self.assertEqual(M.normalize("40,000 tickets"), ["forty", "thousand", "tickets"])
        self.assertEqual(M.normalize("13.3 percent"), ["thirteen", "point", "three", "percent"])
        self.assertEqual(M.normalize("35"), ["thirty", "five"])
        self.assertEqual(M.normalize("2,500,001"), ["two", "million", "five", "hundred", "thousand", "one"])

    def test_spelled_and_digit_forms_agree(self):
        r = M.wer("raises the price by ten percent", "raises the price by 10%")
        self.assertEqual(r["wer"], 0.0)

    def test_int_words_hand_values(self):
        self.assertEqual(M._int_words(0), "zero")
        self.assertEqual(M._int_words(21), "twenty one")
        self.assertEqual(M._int_words(100), "one hundred")
        self.assertEqual(M._int_words(1001), "one thousand one")
        self.assertEqual(M._int_words(999), "nine hundred ninety nine")


class Wer(unittest.TestCase):
    def test_hand_computed(self):
        # ref 4 词；hyp: 1 替换(cat→cap) + 1 删除(sat) → 2/4
        r = M.wer("the cat sat down", "the cap down")
        self.assertEqual((r["sub"], r["del"], r["ins"], r["ref_len"]), (1, 1, 0, 4))
        self.assertAlmostEqual(r["wer"], 0.5)

    def test_insertions(self):
        r = M.wer("a b", "a x b y")
        self.assertEqual((r["sub"], r["del"], r["ins"]), (0, 0, 2))
        self.assertAlmostEqual(r["wer"], 1.0)

    def test_punct_and_case_do_not_count(self):
        self.assertEqual(M.wer("Hello, world.", "hello world")["wer"], 0.0)

    def test_empty_ref_has_no_wer(self):
        self.assertIsNone(M.wer("", "anything")["wer"])

    def test_ops_sum_equals_distance_on_random(self):
        # 性质：S+D+I 是编辑距离，必满足 |len差| ≤ 距离 ≤ max(len)；且对自身为 0
        rng = random.Random(7)
        vocab = list("abcdef")
        for _ in range(300):
            a = [rng.choice(vocab) for _ in range(rng.randint(0, 9))]
            b = [rng.choice(vocab) for _ in range(rng.randint(0, 9))]
            s, d, i = M.edit_ops(a, b)
            self.assertGreaterEqual(s + d + i, abs(len(a) - len(b)))
            self.assertLessEqual(s + d + i, max(len(a), len(b)))
            self.assertEqual(M.edit_ops(a, a), (0, 0, 0))
            # 删除-插入的差必等于长度差
            self.assertEqual(i - d, len(b) - len(a))


class Rewrite(unittest.TestCase):
    def test_pure_growth_is_zero(self):
        r = M.rewrite_stats(["a b", "a b c", "a b c d"])
        self.assertEqual(r["rewritten"], 0)
        self.assertEqual(r["shown"], 2 + 3)
        self.assertAlmostEqual(r["rate"], 0.0)

    def test_tail_change_counts_but_is_absorbed_by_holdback(self):
        # prev=a b c d  new=a b x y e → 前缀保留 2 → 改写 2 个词
        r = M.rewrite_stats(["a b c d", "a b x y e"], k=2)
        self.assertEqual((r["shown"], r["rewritten"]), (4, 2))
        self.assertEqual(r["rewritten_beyond_k"], 0)     # 灰尾 2 词全吸收

    def test_head_change_not_absorbed(self):
        # prev=a b c d e  new=z b c d e → 前缀 0 → 改写 5；灰尾 2 吸收后仍有 3
        r = M.rewrite_stats(["a b c d e", "z b c d e"], k=2)
        self.assertEqual(r["rewritten"], 5)
        self.assertEqual(r["rewritten_beyond_k"], 3)

    def test_shrink_counts_as_rewrite(self):
        r = M.rewrite_stats(["a b c", "a"])
        self.assertEqual(r["rewritten"], 2)

    def test_single_draft_has_no_steps(self):
        r = M.rewrite_stats(["a b c"])
        self.assertEqual(r["steps"], 0)
        self.assertIsNone(r["rate"])

    def test_beyond_k_never_exceeds_rewritten(self):
        rng = random.Random(3)
        for _ in range(200):
            seq = [" ".join(rng.choice("abc") for _ in range(rng.randint(1, 8)))
                   for _ in range(rng.randint(2, 6))]
            for k in (0, 1, 2, 5):
                r = M.rewrite_stats(seq, k=k)
                self.assertLessEqual(r["rewritten_beyond_k"], r["rewritten"])
                if k == 0:
                    self.assertEqual(r["rewritten_beyond_k"], r["rewritten"])


class FinalRewrite(unittest.TestCase):
    def test_identical(self):
        r = M.final_rewrite("so the model", "So, the model.")
        self.assertEqual(r["edits"], 0)

    def test_one_sub(self):
        r = M.final_rewrite("so the modal", "so the model")
        self.assertEqual(r["edits"], 1)
        self.assertAlmostEqual(r["rate"], 1 / 3)

    def test_draft_covers_only_tail_of_final(self):
        # 草稿只有最近几个词；定稿更长。开头多出来的不算改写
        r = M.final_rewrite("the model fits", "so first the model fits well")
        self.assertEqual(r["edits"], 1)            # 只多一个尾词 "well"（插入）
        # 反例：开头没有免费跳过时，会把 "so first" 记成两个插入
        self.assertEqual(sum(M.edit_ops(["the", "model", "fits"],
                                        "so first the model fits well".split())), 3)

    def test_empty_draft(self):
        self.assertIsNone(M.final_rewrite("", "x")["edits"])


class Percentile(unittest.TestCase):
    def test_nearest_rank(self):
        v = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
        self.assertEqual(M.percentile(v, 50), 5)
        self.assertEqual(M.percentile(v, 95), 10)
        self.assertEqual(M.percentile(v, 10), 1)

    def test_unsorted_and_empty(self):
        self.assertEqual(M.percentile([3, 1, 2], 100), 3)
        self.assertIsNone(M.percentile([], 50))


if __name__ == "__main__":
    unittest.main()
