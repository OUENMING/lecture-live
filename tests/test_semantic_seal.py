#!/usr/bin/env python3
"""`semantic_seal.py` + `StreamCore.seal_through_word` 的判据。全程离线、不花钱、不连 Soniox：
核心用假时钟喂 token，决策用假 `decide`（不碰 LLM）。

钉的是 2026-10-28 CHE002A 的症状：Soniox 标点崩塌 → 一大段没有标点的草稿，句号路径永远封不了。
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import time

_TMP = tempfile.TemporaryDirectory(prefix="cl_semseal_test_")
os.environ["HOME"] = _TMP.name
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import semantic_seal as ss  # noqa: E402
from stream_asr import StreamCore, Tok  # noqa: E402

FAILED: list[str] = []


def check(name: str, fn) -> None:
    t0 = time.monotonic()
    try:
        fn()
        print(f"  ✅ {name}  ({time.monotonic() - t0:.2f}s)")
    except Exception as e:                          # noqa: BLE001
        FAILED.append(name)
        print(f"  ❌ {name}: {type(e).__name__}: {e}")


def words_to_toks(words, *, final_upto: int = 0, step: int = 300, base: int = 0) -> list[Tok]:
    """每个词一个 token（带前导空格），时间 300 ms 一个，`base` 毫秒起；前 `final_upto` 个标 final。"""
    out = []
    for i, w in enumerate(words):
        out.append(Tok(("" if i == 0 else " ") + w, base + i * step, base + i * step + step - 20, i < final_upto))
    return out


def feed(core: StreamCore, words, final_upto: int = 0) -> None:
    toks = words_to_toks(words, final_upto=final_upto)
    core.feed([t for t in toks if t.is_final], [t for t in toks if not t.is_final])


W = ("the rate of effusion is proportional to the speed of the gas particles so therefore when you compare "
     "the effusion rates between the two gases then you are comparing the root mean square speeds").split()


# ── StreamCore.seal_through_word ──

def t_core_seals_prefix_and_keeps_tail() -> None:
    c = StreamCore(clock=lambda: 0.0)
    feed(c, W)
    assert c.seal_through_word(13, W[:14])
    got = c.take_sentences()
    assert len(got) == 1 and got[0].text.split() == W[:14], got
    assert c.unsealed_words() == W[14:], c.unsealed_words()


def t_core_mixed_final_and_nonfinal() -> None:
    c = StreamCore(clock=lambda: 0.0)
    feed(c, W, final_upto=10)                       # 前 10 个已 final，后面是 non-final
    assert c.seal_through_word(15, W[:16])
    assert c.take_sentences()[0].text.split() == W[:16]
    assert c.unsealed_words() == W[16:]


def t_core_no_duplicate_when_soniox_finalizes_later() -> None:
    """语意封口之后 Soniox 把同一段变 final：水位线挡掉，不能再封一遍 / 不能让旧词回到草稿。"""
    c = StreamCore(clock=lambda: 0.0)
    feed(c, W)
    assert c.seal_through_word(13, W[:14])
    c.take_sentences()
    feed(c, W, final_upto=len(W))                   # 同一批词全部 final
    assert c.take_sentences() == [], "旧词又被封了一遍"
    assert c.unsealed_words() == W[14:], c.unsealed_words()


def t_core_period_after_semantic_still_seals() -> None:
    """语意封口不能误伤句号路径：后面出现的句号照样封。"""
    c = StreamCore(clock=lambda: 0.0)
    toks = words_to_toks(W)
    dot = Tok(".", toks[-1].end_ms, toks[-1].end_ms + 100, True)
    c.feed([], toks + [Tok(".", dot.start_ms, dot.end_ms, False)])      # 句号还只是 non-final：不封
    assert c.take_sentences() == []
    assert c.seal_through_word(13, W[:14])
    assert c.take_sentences()[0].text.split() == W[:14]
    c.feed(words_to_toks(W, final_upto=len(W)) + [dot], [])             # 变 final：句号路径封剩下的
    got = c.take_sentences()
    assert len(got) == 1 and got[0].text.strip() == " ".join(W[14:]) + ".", got


def t_core_stale_expect_refuses() -> None:
    c = StreamCore(clock=lambda: 0.0)
    feed(c, W)
    bad = list(W[:14]); bad[3] = "XX"
    assert not c.seal_through_word(13, bad)
    assert c.take_sentences() == [] and c.unsealed_words() == W


def t_core_out_of_range() -> None:
    c = StreamCore(clock=lambda: 0.0)
    feed(c, W)
    assert not c.seal_through_word(len(W), None) and not c.seal_through_word(-1, None)
    assert c.unsealed_words() == W


def t_core_multiword_token_straddle_refused() -> None:
    """一个 token 跨两个词（测试夹具里有这种写法）：在它中间切不了，宁可不封。"""
    c = StreamCore(clock=lambda: 0.0)
    c.feed([], [Tok("one two", 0, 300, False), Tok(" three", 300, 600, False)])
    assert not c.seal_through_word(0, ["one"])
    assert c.seal_through_word(1, ["one", "two"])


def t_core_subword_pieces() -> None:
    c = StreamCore(clock=lambda: 0.0)
    c.feed([], [Tok("Hel", 0, 100, False), Tok("lo", 100, 200, False), Tok(" wor", 200, 300, False),
                Tok("ld", 300, 400, False), Tok(" again", 400, 500, False)])
    assert c.unsealed_words() == ["Hello", "world", "again"]
    assert c.seal_through_word(1, ["Hello", "world"])
    assert c.take_sentences()[0].text.strip() == "Hello world"
    assert c.unsealed_words() == ["again"]


# ── choose_cut / cap_cut ──

def t_choose_cut_rules() -> None:
    w = "a b c d e f g h i j k l m n o".split()
    assert ss.choose_cut(w, [3, 9]) == 9
    assert ss.choose_cut(w, [14]) is None, "末尾留 LOOKAHEAD 个词的余量"
    assert ss.choose_cut(w, [2]) is None, "太短的碎句不要"
    w2 = "the rate of the thing is the".split() + ["x"] * 6
    assert ss.choose_cut(w2, [5, 4]) is None or w2[ss.choose_cut(w2, [5, 4])] not in ss.NOT_END
    w3 = "we go to the store and then we leave right now okay".split() + ["x"] * 4
    i = w3.index("okay")
    assert ss.choose_cut(w3, [i]) == i
    w4 = "this is fine and that is fine so now we go".split() + ["x"] * 4
    assert ss.choose_cut(w4, [w4.index("so")]) == w4.index("so") - 1, "so 归下一句"


def t_cap_cut_avoids_function_word() -> None:
    w = ("one two three four five six seven eight nine ten the of and").split() + ["x", "y", "z"]
    k = ss.cap_cut(w)
    assert w[k].lower() not in ss.NOT_END, (k, w[k])


# ── SemanticSealer ──

class FakeStream:
    """满足 `_Stream` 接缝的假流：背后是真 `StreamCore`（封口语义一模一样）。"""

    def __init__(self, words):
        self.core = StreamCore(clock=lambda: 0.0)
        feed(self.core, words)
        self.sealed = []

    def unsealed_words(self):
        return self.core.unsealed_words()

    def seal_through_word(self, k, expect=None):
        ok = self.core.seal_through_word(k, expect)
        if ok:
            self.sealed += self.core.take_sentences()
        return ok


class Clock:
    t = 100.0

    def __call__(self):
        return self.t


def mk(words, decide, clk=None, **kw):
    clk = clk or Clock()
    stream = FakeStream(words)
    return stream, ss.SemanticSealer(stream, decide, clock=clk, **kw), clk


def t_sealer_below_gate_does_nothing() -> None:
    calls = []
    s, z, _ = mk(W[:20], lambda w: calls.append(1) or [10])
    assert not z.step() and not calls and not s.sealed


def t_sealer_seals_long_unpunctuated_run() -> None:
    seen = []
    s, z, _ = mk(W, lambda w: [13, 25] if seen.append(1) is None else [])
    assert z.step()
    assert s.sealed and s.sealed[0].text.split() == W[:len(s.sealed[0].text.split())]
    assert z.stats["semantic"] == 1 and len(s.sealed[0].text.split()) in (14, 26)


def t_sealer_punctuation_defers_to_period_path() -> None:
    ws = W[:12] + ["wait,"] + W[12:]
    s, z, _ = mk(ws, lambda w: [15])
    assert not z.step() and not s.sealed, "有逗号：句号路径会封，别抢"


def t_sealer_hard_words_ignores_punctuation() -> None:
    ws = ["wait,"] + [f"w{i}" for i in range(ss.HARD_WORDS + 2)]
    s, z, _ = mk(ws, lambda w: [20])
    assert z.step() and s.sealed


def t_sealer_rate_limited() -> None:
    n = []
    s, z, clk = mk(W[:30], lambda w: n.append(1) or [])   # 30 词：过门槛、没到上限，模型什么都没挑出来
    z.step(); z.step()
    assert len(n) == 1, "间隔不到 STEP_S 不许再问"
    clk.t += ss.STEP_S + 0.1
    z.step()
    assert len(n) == 2


def t_sealer_latch_lowers_gate() -> None:
    long = [f"w{i}" for i in range(40)]
    s, z, clk = mk(long, lambda w: [29])
    assert z.step() and z.stats["semantic"] == 1
    # 封走 30 词后剩 10 词；刚封过 → 门槛降到 LATCH_GATE_WORDS
    clk.t += ss.STEP_S + 0.1
    rest = [f"w{i}" for i in range(30, 40)] + [f"v{i}" for i in range(5)]
    s.core.feed([], words_to_toks(rest, base=40 * 300))
    left = s.unsealed_words()
    assert len(left) >= ss.LATCH_GATE_WORDS
    z2 = z
    seen = []
    z2._decide = lambda w: seen.append(len(w)) or []
    z2.step()
    assert seen, "latch 内应该以较低门槛再问一次"
    clk.t += ss.LATCH_S + 1
    clk.t += ss.STEP_S
    seen.clear()
    z2.step()
    assert not seen, "latch 过期后回到正常门槛：15 词不够 25"


def t_sealer_decide_error_falls_back_to_cap() -> None:
    def boom(w):
        raise RuntimeError("llm down")
    long = [f"w{i}" for i in range(ss.CAP_WORDS + 2)]
    s, z, _ = mk(long, boom)
    assert z.step() and z.stats["errors"] == 1 and z.stats["cap"] == 1
    assert s.sealed


def t_sealer_decide_error_below_cap_waits() -> None:
    def boom(w):
        raise RuntimeError("llm down")
    s, z, _ = mk(W[:30], boom)
    assert not z.step() and not s.sealed


def t_sealer_stale_text_not_sealed() -> None:
    """决策期间前缀变了（Soniox 改词 / 句号路径先封走）→ 不封，下一轮重来。"""
    holder = {}

    def decide(w):
        # 模拟决策花了一秒，这期间 Soniox 把第 4 个词改了
        toks = words_to_toks(W)
        toks[3] = Tok(" CHANGED", toks[3].start_ms, toks[3].end_ms, False)
        holder["s"].core.feed([], toks)
        return [13]
    s, z, _ = mk(W, decide)
    holder["s"] = s
    assert not z.step() and z.stats["stale"] == 1 and not s.sealed


def t_sealer_on_seal_callback_and_exception_safe() -> None:
    got = []

    def cb(info):
        got.append(info)
        raise RuntimeError("callback bug")
    s, z, _ = mk(W, lambda w: [13], on_seal=cb)
    assert z.step() and got and got[0]["kind"] == "semantic" and got[0]["words"] == 13   # W[13]=="so" 归下一句


def t_sealer_dead_stream_not_touched() -> None:
    s, z, _ = mk(W, lambda w: [13])
    s.alive = lambda: False
    assert not z.step() and not s.sealed


def t_sealer_thread_starts_and_stops() -> None:
    s, z, _ = mk(W[:5], lambda w: [])
    z.start(); z.start()
    z.stop()
    assert z._thread is not None and not z._thread.is_alive()


def main() -> int:
    print("semantic_seal 判据")
    for name, fn in [
        ("core: 封前缀、留尾巴", t_core_seals_prefix_and_keeps_tail),
        ("core: final 与 non-final 混合", t_core_mixed_final_and_nonfinal),
        ("core: 之后变 final 不重复封", t_core_no_duplicate_when_soniox_finalizes_later),
        ("core: 语意封口后句号照常封", t_core_period_after_semantic_still_seals),
        ("core: 前缀已变 → 拒封", t_core_stale_expect_refuses),
        ("core: 越界不动", t_core_out_of_range),
        ("core: 跨词 token 中间不切", t_core_multiword_token_straddle_refused),
        ("core: 子词 token 拼词", t_core_subword_pieces),
        ("choose_cut 规则", t_choose_cut_rules),
        ("cap_cut 躲开虚词", t_cap_cut_avoids_function_word),
        ("sealer: 不到门槛不动", t_sealer_below_gate_does_nothing),
        ("sealer: 长段无标点 → 封", t_sealer_seals_long_unpunctuated_run),
        ("sealer: 有标点让句号路径封", t_sealer_punctuation_defers_to_period_path),
        ("sealer: 到 HARD 词数无视标点", t_sealer_hard_words_ignores_punctuation),
        ("sealer: 限频", t_sealer_rate_limited),
        ("sealer: 刚封过降门槛", t_sealer_latch_lowers_gate),
        ("sealer: 模型报错 → 到上限硬切", t_sealer_decide_error_falls_back_to_cap),
        ("sealer: 模型报错且未到上限 → 等", t_sealer_decide_error_below_cap_waits),
        ("sealer: 决策期间文字变了不封", t_sealer_stale_text_not_sealed),
        ("sealer: 回调抛错不影响", t_sealer_on_seal_callback_and_exception_safe),
        ("sealer: 流不在线不动", t_sealer_dead_stream_not_touched),
        ("sealer: 线程起停", t_sealer_thread_starts_and_stops),
    ]:
        check(name, fn)
    print(f"\n{'❌ 失败 ' + str(len(FAILED)) + '：' + '、'.join(FAILED) if FAILED else '✅ 全部通过'}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
