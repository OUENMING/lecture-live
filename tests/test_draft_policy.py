import os, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import draft_policy as P


def call(policy, **kw):
    base = dict(now=10.0, last_tr=0.0, n_words=10, last_len=0, text="a b c d e f g h i j", append_only=False)
    base.update(kw)
    return P.should_translate(policy, **base)


class Legacy(unittest.TestCase):
    def test_needs_gap_and_new_words(self):
        self.assertTrue(call("legacy"))
        self.assertFalse(call("legacy", now=2.0))                    # 距上次 <2.5s
        self.assertFalse(call("legacy", n_words=5))                  # 新增 <6
        self.assertFalse(call("legacy", n_words=12, last_len=7))     # 新增 5

    def test_boundaries_inclusive(self):
        self.assertTrue(call("legacy", now=2.5))
        self.assertTrue(call("legacy", n_words=6))


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
                                     text="x", append_only=rng.random() < .5)
            self.assertEqual(got, old)


class Clause(unittest.TestCase):
    def test_midclause_unstable_is_held(self):
        self.assertFalse(call("clause", now=10.5, last_tr=8.0, n_words=8, text="so the price of the", append_only=False))

    def test_clause_end_or_append_only_passes(self):
        self.assertTrue(call("clause", n_words=8, text="so the price of coffee goes up,", append_only=False))
        self.assertTrue(call("clause", n_words=8, text="so the price of the", append_only=True))

    def test_never_starves(self):
        self.assertTrue(call("clause", now=10.5, last_tr=8.0, n_words=12, text="x " * 12, append_only=False))      # 新增 ≥12
        self.assertTrue(call("clause", now=7.0, n_words=7, text="x " * 7, append_only=False))  # 距上次 ≥6s

    def test_clause_is_never_more_eager_than_legacy(self):
        import random
        rng = random.Random(2)
        for _ in range(2000):
            kw = dict(now=rng.uniform(0, 12), last_tr=0.0, n_words=rng.randint(0, 30),
                      last_len=rng.randint(0, 10), text=rng.choice(["a b", "a b.", "a, b"]),
                      append_only=rng.random() < 0.5)
            if P.should_translate("clause", **kw):
                self.assertTrue(P.should_translate("legacy", **kw), kw)

    def test_unknown_policy(self):
        with self.assertRaises(ValueError):
            call("nope")


class Clause_end(unittest.TestCase):
    def test_trailing_space(self):
        self.assertTrue(P.ends_at_clause("hello, "))
        self.assertFalse(P.ends_at_clause("hello"))


if __name__ == "__main__":
    unittest.main()
