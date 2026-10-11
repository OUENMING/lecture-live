import os, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import draft_policy as P


#: 一份**不是** `text` 前缀的上一次草稿 —— 用来表达"前面的词被改过"（append_only=False）。
OTHER = "zzz yyy xxx"


def call(policy, **kw):
    base = dict(now=10.0, last_tr=0.0, n_words=10, last_len=0,
                text="a b c d e f g h i j", prev_text=OTHER)
    base.update(kw)
    return P.should_translate(policy, **base)


class AppendOnly(unittest.TestCase):
    """⭐ 派生量的**唯一定义点**（2026-10-09 收口）。

    ⚠️ 原来主循环手搓一份、离线模拟用 `stream_metrics.normalize` 又写一份 ——
       两份不等价，所以那份模拟预测的**不是**生产真跑的路。
    """

    def test_plain_extension_is_append_only(self):
        self.assertTrue(P.append_only("how much less coffee", "how much less coffee do people buy"))
        self.assertTrue(P.append_only("", "anything at all"))       # 无前文 → 从头顶起算

    def test_a_changed_word_breaks_it(self):
        self.assertFalse(P.append_only("the quantity falls", "the price falls"))

    def test_case_and_trailing_clause_punct_are_ignored(self):
        """词身份只认小写 + 尾随的从句标点 —— 与主循环原来那份**逐字一致**。"""
        self.assertTrue(P.append_only("so the price", "So the PRICE goes up,"))
        self.assertTrue(P.append_only("so the price.", "so the price goes up"))


class Legacy(unittest.TestCase):
    def test_needs_gap_and_new_words(self):
        self.assertTrue(call("legacy"))
        self.assertFalse(call("legacy", now=2.0))                    # 距上次 <2.5s
        self.assertFalse(call("legacy", n_words=5))                  # 新增 <6
        self.assertFalse(call("legacy", n_words=12, last_len=7))     # 新增 5

    def test_boundaries_inclusive(self):
        self.assertTrue(call("legacy", now=2.5))
        self.assertTrue(call("legacy", n_words=6))

    def test_legacy_never_looks_at_prev_text(self):
        """legacy 档**不读** `prev_text` —— 派生量只有在 clause 档才算（默认档不白算）。"""
        for prev in ("", OTHER, "a b c d e f g h i"):
            self.assertTrue(call("legacy", prev_text=prev))


class LegacyEquivalence(unittest.TestCase):
    def test_legacy_equals_the_old_inline_condition(self):
        """main.partial_worker 原来内联的判据：now - last_tr >= 2.5 and n_words - last_len >= 6。"""
        import random
        rng = random.Random(9)
        for _ in range(5000):
            now, last_tr = rng.uniform(0, 20), rng.uniform(0, 20)
            n, ll = rng.randint(0, 40), rng.randint(0, 40)
            old = (now - last_tr >= 2.5) and (n - ll >= 6)
            got = P.should_translate("legacy", now=now, last_tr=last_tr, n_words=n, last_len=ll,
                                     text="x", prev_text=rng.choice(["", OTHER, "x"]))
            self.assertEqual(got, old)


class Clause(unittest.TestCase):
    def test_midclause_unstable_is_held(self):
        self.assertFalse(call("clause", now=10.5, last_tr=8.0, n_words=8,
                              text="so the price of the", prev_text=OTHER))

    def test_clause_end_or_append_only_passes(self):
        self.assertTrue(call("clause", n_words=8, text="so the price of coffee goes up,", prev_text=OTHER))
        self.assertTrue(call("clause", n_words=8, text="so the price of the", prev_text="so the price"))

    def test_never_starves(self):
        self.assertTrue(call("clause", now=10.5, last_tr=8.0, n_words=12, text="x " * 12, prev_text=OTHER))      # 新增 ≥12
        self.assertTrue(call("clause", now=7.0, n_words=7, text="x " * 7, prev_text=OTHER))  # 距上次 ≥6s

    def test_clause_is_never_more_eager_than_legacy(self):
        import random
        rng = random.Random(2)
        for _ in range(2000):
            text = rng.choice(["a b", "a b.", "a, b"])
            kw = dict(now=rng.uniform(0, 12), last_tr=0.0, n_words=rng.randint(0, 30),
                      last_len=rng.randint(0, 10), text=text,
                      prev_text=rng.choice(["", text[:1], OTHER]))   # 前缀 / 不是前缀 各半
            if P.should_translate("clause", **kw):
                self.assertTrue(P.should_translate("legacy", **kw), kw)

    def test_unknown_policy(self):
        with self.assertRaises(ValueError):
            call("nope")


class Clause_end(unittest.TestCase):
    def test_trailing_space(self):
        self.assertTrue(P.ends_at_clause("hello, "))
        self.assertFalse(P.ends_at_clause("hello"))


class Novel(unittest.TestCase):
    """`novel`：相对**上次送出的文本**数新词，而不是数"比上次多几个词"。"""

    SEG91_SENT = ("so the thing is that when the price goes up by a lot of money "
                  "then people buy less of it overall")                      # 19 词
    SEG91_NOW = "price goes up lot money then people buy much less coffee tea sugar fuel"  # 13 词

    def test_seg91_replica_novel_true_legacy_false(self):
        n_sent = len(self.SEG91_SENT.split())
        n_now = len(self.SEG91_NOW.split())
        kw = dict(now=10.0, last_tr=0.0, n_words=n_now, last_len=n_sent,
                  text=self.SEG91_NOW)
        self.assertGreaterEqual(P.novel_words(self.SEG91_SENT, self.SEG91_NOW), 4)
        self.assertTrue(P.should_translate("novel", sent_text=self.SEG91_SENT, **kw))
        self.assertFalse(P.should_translate("legacy", **kw))

    def test_gap_below_min_is_false(self):
        self.assertFalse(P.should_translate(
            "novel", now=2.0, last_tr=0.0, n_words=10, last_len=0,
            text="a b c d e f g h i j", sent_text=""))

    def test_few_new_words_wait_for_starve_gap(self):
        kw = dict(n_words=6, last_len=6, text="a b c d e x", sent_text="a b c d e f")
        self.assertFalse(P.should_translate("novel", now=3.0, last_tr=0.0, **kw))
        self.assertTrue(P.should_translate("novel", now=5.0, last_tr=0.0, **kw))

    def test_identical_text_never_fires(self):
        self.assertFalse(P.should_translate(
            "novel", now=10.0, last_tr=0.0, n_words=4, last_len=4,
            text="a b c d", sent_text="a b c d"))

    def test_novel_words_is_multiset_difference(self):
        self.assertEqual(P.novel_words("the price", "the the price"), 1)
        self.assertEqual(P.novel_words("", "a b"), 2)


if __name__ == "__main__":
    unittest.main()
