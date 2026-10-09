"""`scripts/class_report.py` 的判据 —— 全离线、毫秒级、不读真 `sessions/`。

    ClassLive.app/Contents/MacOS/python tests/test_class_report.py

## 钉住什么

1. **组句判据**：草稿归"它之后第一个定稿"；`utt` 号若事件里写了就用它。
2. ⭐ **`apply_stabilizer(..., 0)` 是恒等** —— hold-back 为 0 时必须**逐字等于**原始假设。
   这是"离线 A/B 与生产同一条路"的锚：离线套出来的 holdback=K 要能对上
   `stream_eval --stabilize K` 的记录（已用 2026-10-08 的三份记录逐位复核过）。
3. **臆造尾巴的判据**（`beyond_draft`）：贪婪子序列对齐，取站不住的那一段。
   ⚠️ 这组里有一条是**真实案例的字面复刻**（2026-10-08 那份 TTS 语料里 utt1 的
   `the quantity is less.`）—— 判据最初写成"某段连续文本在草稿里找不到"，
   **一条真命中都没有**，就是这条把它钉死的。
4. 旧格式（没有 `buf_s`）必须**出声警告**，不许静默给出偏高的数字。
"""
from __future__ import annotations

import json
import os
import random
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "scripts"))
import class_report as CR       # noqa: E402


def _write(events) -> str:
    d = tempfile.mkdtemp()
    p = os.path.join(d, "x.jsonl")
    with open(p, "w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    return p


def _draft(utt, text, raw=None, t=0.0, buf_s=1.0):
    return {"kind": "draft", "utt": utt, "text": text, "raw": raw if raw is not None else text,
            "t": t, "buf_s": buf_s, "compute_s": 0.1}


def _final(utt, text, t=0.0):
    return {"kind": "final", "utt": utt, "text": text, "zh": "", "raw": text, "t": t}


class LoadEvents(unittest.TestCase):
    def test_bad_lines_are_skipped_not_fatal(self):
        p = _write([_draft(0, "a")])
        with open(p, "a", encoding="utf-8") as f:
            f.write("这不是 JSON\n\n")           # 半行/空行
            f.write('{"no_kind": 1}\n')          # 缺 kind
            f.write(json.dumps(_final(0, "a")) + "\n")
        ev = CR.load_events(p)
        self.assertEqual([e["kind"] for e in ev], ["draft", "final"])


class BuildUtts(unittest.TestCase):
    def test_grouping_and_final_advances(self):
        p = _write([_draft(0, "one"), _draft(0, "one two"), _final(0, "one two."),
                    _draft(1, "three"), _final(1, "three.")])
        u = CR.build_utts(CR.load_events(p))
        self.assertEqual(len(u), 2)
        self.assertEqual(len(u[0]["drafts"]), 2)
        self.assertEqual(u[0]["final"], "one two.")
        self.assertEqual(u[1]["final"], "three.")

    def test_audio_t_present_path_sets_start_end_dur(self):
        """有 `audio_t`（离线那份）时 `start_t/end_t/dur` 必须真的填上 —— 延迟那几项靠它们。

        ⚠️ 这条是**跑真数据才发现的**：`audio_t` 缺席那条分支先写对了，在场的这条
           写成了 `u["start_t"] = u["start_t"] if ...`（键还不存在）→ 一读离线文件就 KeyError。
           判据原来只钉了"缺席"那一半。
        """
        p = _write([{"kind": "draft", "utt": 0, "text": "a", "raw": "a", "t": 0.5,
                     "audio_t": 3.0, "buf_s": 1.0, "compute_s": 0.1},
                    {"kind": "draft", "utt": 0, "text": "a b", "raw": "a b", "t": 1.5,
                     "audio_t": 4.0, "buf_s": 2.0, "compute_s": 0.1},
                    {"kind": "final", "utt": 0, "text": "a b.", "t": 2.0, "audio_t": 5.0,
                     "compute_s": 0.2}])
        u = CR.build_utts(CR.load_events(p))
        self.assertEqual((u[0]["start_t"], u[0]["end_t"], u[0]["dur"]), (3.0, 4.0, 1.0))
        r = CR.SE.evaluate(u, None)
        self.assertEqual(r["latency_first_visible_s"]["n"], 1)
        self.assertEqual(r["latency_final_compute_s"]["n"], 1)

    def test_missing_audio_t_stays_none_and_latency_comes_out_empty(self):
        """⭐ 现场那份**没有音频时刻**，那就**必须留空**。

        ⚠️ 曾经写成"拿事件入队秒数顶上" —— 那会让 `stream_eval.evaluate()` 里
        **专为现场加的** `audio_t is not None` 守卫永远成立，于是屏上打出一个
        **假的首字延迟**（恒等于草稿的 `compute_s`：`start_t` 就是 `draft[0].audio_t`，
        相减恒为 0）。生产里根本没有音频时钟，这一格补不出来。
        """
        p = _write([_draft(0, "a", t=3.0), _draft(0, "a b", t=4.0), _final(0, "a b", t=5.0)])
        u = CR.build_utts(CR.load_events(p))
        self.assertEqual([d["audio_t"] for d in u[0]["drafts"]], [None, None])
        self.assertEqual((u[0]["start_t"], u[0]["end_t"], u[0]["dur"]), (0.0, 0.0, 0.0))
        r = CR.SE.evaluate(u, None)
        self.assertEqual(r["latency_first_visible_s"]["n"], 0)
        self.assertIsNone(r["latency_first_visible_s"]["p50"])
        self.assertEqual(r["latency_final_compute_s"]["n"], 0)
        # 但**回改率照样算得出来**（它只读文本）—— 这才是现场那份的价值
        self.assertIsNotNone(r["draft_rewrite"]["rate"])

    def test_segment_grouping_when_seg_is_present(self):
        """⭐ 现场那份按 `seg` 分组，**不是**按定稿句。

        这是 2026-10-09 真跑抓到的缺陷：一段 VAD 被 `split_sentences` 切成 2 句定稿，
        按"一句定稿一组"分的话，那 2 句各自只剩 0–1 份草稿 → 草稿→定稿跳到 93%
        （离线回放同一份音频是 10.7%）。分组键必须是 `seg`。
        """
        ev = [{"kind": "draft", "seg": 0, "utt": 0, "text": "a b", "raw": "a b", "t": 1.0,
               "buf_s": 1.0, "compute_s": 0.1},
              {"kind": "draft", "seg": 0, "utt": 0, "text": "a b c", "raw": "a b c", "t": 2.0,
               "buf_s": 2.0, "compute_s": 0.1},
              {"kind": "final", "seg": 0, "utt": 0, "text": "a b.", "t": 3.0},
              {"kind": "final", "seg": 0, "utt": 1, "text": "c.", "t": 3.0},
              {"kind": "draft", "seg": 1, "utt": 2, "text": "d e", "raw": "d e", "t": 4.0,
               "buf_s": 1.0, "compute_s": 0.1},
              {"kind": "final", "seg": 1, "utt": 2, "text": "d e.", "t": 5.0}]
        u = CR.build_utts(ev)
        self.assertEqual(len(u), 2)
        self.assertEqual(len(u[0]["drafts"]), 2)
        self.assertEqual(u[0]["final"], "a b. c.")          # 同段的两句拼起来
        self.assertEqual(len(u[1]["drafts"]), 1)
        self.assertEqual(u[1]["final"], "d e.")

    def test_utt_grouping_when_seg_is_absent(self):
        """离线回放那份没有 `seg` → 退回按 `utt` 分（回放本来就是一段一句）。"""
        p = _write([_draft(0, "a"), _final(0, "a."), _draft(1, "b"), _final(1, "b.")])
        u = CR.build_utts(CR.load_events(p))
        self.assertEqual([x["final"] for x in u], ["a.", "b."])

    def test_utt_field_is_honoured_over_the_running_counter(self):
        """事件里写了 `utt` 就用它 —— 两个来源（现场/回放）都写，别在这里再推一遍。"""
        p = _write([_draft(7, "x"), _final(7, "x")])
        u = CR.build_utts(CR.load_events(p))
        self.assertEqual([x["final"] for x in u], ["x"])


class StabilizerIsTheSamePath(unittest.TestCase):
    def test_k0_is_identity(self):
        """⭐ hold-back 0 = 原样上屏。这条挂了说明离线套的 K 与生产不是同一条路。"""
        rnd = random.Random(20261009)
        words = "the quantity falls by more than ten percent".split()
        for _ in range(200):
            n = rnd.randint(1, 6)
            texts = [" ".join(rnd.choices(words, k=rnd.randint(1, 9))) for _ in range(n)]
            utts = [{"drafts": [{"raw": t, "shown": t, "buf_s": 1.0, "compute_s": 0.0,
                                 "audio_t": 0.0} for t in texts], "final": texts[-1]}]
            got = CR.apply_stabilizer(utts, 0)
            self.assertEqual([d["shown"] for d in got[0]["drafts"]], texts)

    def test_k_freezes_the_prefix_so_an_early_word_stops_changing(self):
        """K=1 时，**除最后一个词外**的前缀不许再被改写（灰尾只吸收 1 个词）。"""
        first = "the quantity falls by more than ten percent"
        second = "the price falls by more than ten percent"      # 第 2 个词被改（不在灰尾）
        utts = [{"drafts": [{"raw": first, "shown": first, "buf_s": 1.0, "compute_s": 0.0,
                             "audio_t": 0.0},
                            {"raw": second, "shown": second, "buf_s": 1.0, "compute_s": 0.0,
                             "audio_t": 0.0}], "final": second}]
        got = CR.apply_stabilizer(utts, 1)
        # LCP=1（"the"）→ c = max(0, 1-1) = 0 → 这次它**还没冻结任何词** …
        # 所以整句仍然跟着新假设走：这是 hold-back 的定义，不是缺陷。
        self.assertEqual(got[0]["drafts"][1]["shown"], second)
        # 第三份再改一次，这次 "the" 已被冻结
        utts[0]["drafts"].append({"raw": "the amount falls by more than ten percent",
                                  "shown": "the amount falls by more than ten percent",
                                  "buf_s": 1.0, "compute_s": 0.0, "audio_t": 0.0})
        got = CR.apply_stabilizer(utts, 1)
        self.assertTrue(got[0]["drafts"][2]["shown"].startswith("the "), got[0]["drafts"][2]["shown"])
        self.assertEqual(got[0]["drafts"][2]["raw"].split()[1], "amount")   # 原始假设没被动

    def test_raw_is_never_touched(self):
        utts = [{"drafts": [{"raw": "a b c", "shown": "a b c", "buf_s": 1.0, "compute_s": 0.0,
                             "audio_t": 0.0}], "final": "a b c"}]
        got = CR.apply_stabilizer(utts, 3)
        self.assertEqual(got[0]["drafts"][0]["raw"], "a b c")


class BeyondDraft(unittest.TestCase):
    def test_documented_case_word_for_word(self):
        """⭐ 2026-10-08 那份 TTS 语料里 utt1 的**字面复刻**。"""
        draft = ("Simple question. If the price of a coffee goes up by 10%, how much less "
                 "coffee do people buy? The answer depends on the elasticity. If demand is elastic,")
        final = (draft + " the quantity is less.")
        tail = CR.beyond_draft(final.split(), draft.split())
        self.assertEqual(tail, ["the", "quantity", "is", "less."])

    def test_draft_equal_to_final_leaves_nothing(self):
        rnd = random.Random(7)
        for _ in range(200):
            ws = [f"w{rnd.randint(0, 5)}" for _ in range(rnd.randint(1, 12))]
            self.assertEqual(CR.beyond_draft(ws, list(ws)), [])

    def test_empty_draft_means_everything_is_beyond(self):
        self.assertEqual(CR.beyond_draft(["a", "b"], []), ["a", "b"])

    def test_tail_is_always_a_suffix(self):
        """不变量：尾巴一定是定稿的**后缀**（不然就是判据写错了，不是数据的问题）。"""
        rnd = random.Random(11)
        pool = "a b c d e".split()
        for _ in range(300):
            fin = [rnd.choice(pool) for _ in range(rnd.randint(0, 10))]
            dr = [rnd.choice(pool) for _ in range(rnd.randint(0, 10))]
            tail = CR.beyond_draft(fin, dr)
            self.assertEqual(fin[len(fin) - len(tail):], tail)


class HallucinationCandidates(unittest.TestCase):
    def test_flags_the_documented_one_and_only_it(self):
        draft = ("Simple question. If the price of a coffee goes up by 10%, how much less "
                 "coffee do people buy? The answer depends on the elasticity. If demand is elastic,")
        utts = [{"final": draft + " the quantity is less.",
                 "drafts": [{"shown": draft, "raw": draft, "buf_s": 1.0, "compute_s": 0.0,
                             "audio_t": 0.0}]},
                {"final": "quantity falls by more than 10%. If demand is inelastic, it falls by less than 10%.",
                 "drafts": [{"shown": "Quantity fall by more than 10%. If demand is inelastic, "
                                      "it falls by less than 10%.",
                             "raw": "", "buf_s": 1.0, "compute_s": 0.0, "audio_t": 0.0}]}]
        got = CR.hallucination_candidates(utts)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["utt"], 0)
        self.assertEqual(got[0]["unsupported_tail"], "the quantity is less")

    def test_no_candidates_when_drafts_cover_the_final(self):
        t = "the quantity falls by more than ten percent"
        utts = [{"final": t, "drafts": [{"shown": t, "raw": t, "buf_s": 1.0, "compute_s": 0.0,
                                         "audio_t": 0.0}]}]
        self.assertEqual(CR.hallucination_candidates(utts), [])


class Sweep(unittest.TestCase):
    def test_baseline_rewrites_what_holdback_can_hide(self):
        """造一个只有"前缀被改写"的例子：基线必有回改，K=1 应该把它冻住一部分。"""
        a = "the quantity falls by more than ten percent"
        b = "the price falls by more than ten percent"
        c = "the price falls by more than twenty percent"
        mk = lambda: [{"drafts": [{"shown": x, "raw": x, "buf_s": 1.0, "compute_s": 0.0,
                                   "audio_t": 0.0} for x in (a, b, b, c)],
                       "final": c, "start_t": 0.0, "end_t": 1.0, "dur": 1.0,
                       "final_compute_s": 0.0}]
        sw = CR.sweep(mk(), None, (None, 1, 2))
        base = sw["baseline(raw)"]["draft_rewrite"]["rate"]
        k1 = sw["holdback=1"]["draft_rewrite"]["rate"]
        k2 = sw["holdback=2"]["draft_rewrite"]["rate"]
        self.assertGreater(base, 0.0)
        self.assertLessEqual(k1, base)
        self.assertLessEqual(k2, base)
        # ⚠️⚠️ **回改率对 K 不单调** —— 这条是判据，不是巧合：
        #    灰尾越大 → 冻结的前缀**越短** → 上屏文本越跟着最新假设走，
        #    而后几个词被改是**记进分子**的（`rewrite_stats` 数的是所有已显示词）。
        #    真数据同款：真实课堂 K=1 31.2% < K=2 33.1%（见 docs/RESEARCH-stream-eval.md）。
        #    → 「加大灰尾」**不等于**"更稳"；选 K 只能拿真课数据挑，不能靠单调外推。
        self.assertLess(k1, k2)


class Pooling(unittest.TestCase):
    def test_pools_counts_not_rates(self):
        """⭐ 汇总必须是**计数相加**。

        造两节长短差很远的课：短的 1 个词里改 1 个（比率 100%），长的 99 个词里改 1 个
        （比率 ~1%）。对比率取平均会得出 ~50%，**计数相加**才是 2/100 = 2%。
        """
        def mk(n_words, n_rewritten_words):
            # 造 n 句，每句两份草稿：第一份 n_words 词，第二份**只把最后一个词**改掉
            # ⚠️ 改的是**最后一个**词 —— 回改率数的是"最长的公共前缀之外的词"，
            #    改开头一个词会把整句都算成回改（lcp=0），那样这个夹具就测不出东西。
            utts = []
            for _ in range(n_rewritten_words):
                a = " ".join(f"a{i}" for i in range(n_words))
                b = " ".join([f"a{i}" for i in range(n_words - 1)] + ["b_last"])
                utts.append({"drafts": [{"shown": a, "raw": a, "buf_s": 1.0, "compute_s": 0.0,
                                         "audio_t": 0.0},
                                        {"shown": b, "raw": b, "buf_s": 1.0, "compute_s": 0.0,
                                         "audio_t": 0.0}],
                             "final": b, "start_t": 0.0, "end_t": 1.0, "dur": 1.0,
                             "final_compute_s": 0.0})
            return {"meta": {"source": "s", "utterances": len(utts),
                             "drafts": 2 * len(utts), "sliding_excluded": 0},
                    "sweep": CR.sweep(utts, None, (None,)), "warnings": []}
        r = CR.pool_sweeps([mk(1, 1), mk(99, 1)])
        dr = r["sweep"]["baseline(raw)"]["draft_rewrite"]
        self.assertEqual(dr["shown_words"], 100)
        self.assertEqual(dr["rewritten"], 2)
        self.assertAlmostEqual(dr["rate"], 0.02)

    def test_render_survives_a_pooled_report(self):
        """汇总那份没有延迟/WER 字段 —— `render()` 不许因此炸。"""
        r = CR.pool_sweeps([{"meta": {"source": "s", "utterances": 0, "drafts": 0,
                                      "sliding_excluded": 0},
                             "sweep": {}, "warnings": []}])
        CR.render(r)                                   # 不抛就算过


class WholeClassWerIsOptIn(unittest.TestCase):
    """⭐ `draft_vs_final_disagreement` 是**整课词表的 O(n²) DP**，默认不许白算。

    2026-10-09 实测（合成 4200 词的事件表，`--ks 0,1,2,3`）：
    开 → 5.36 s，关 → **0.10 s**，而**文本表上的数字逐位相同**。
    原来 `sweep()` 对每个 K 都算一遍（默认 4 遍）。
    """

    def _utts(self):
        a = "the price of coffee goes up and demand falls"
        b = "the price of coffee goes up and demand falls by more"
        return [{"drafts": [{"shown": a, "raw": a, "buf_s": 1.0, "compute_s": 0.1,
                             "audio_t": 0.0},
                            {"shown": b, "raw": b, "buf_s": 2.0, "compute_s": 0.1,
                             "audio_t": 1.0}],
                 "final": b + ".", "start_t": 0.0, "end_t": 1.0, "dur": 1.0,
                 "final_compute_s": 0.1}]

    def test_off_by_default_and_the_table_is_unchanged(self):
        off = CR.sweep(self._utts(), None, (None, 1, 2))
        on = CR.sweep(self._utts(), None, (None, 1, 2), want_disagreement=True)
        for name in off:
            self.assertNotIn("draft_vs_final_disagreement", off[name])
            # ⚠️ 关掉它**不许**动别的任何一格
            self.assertEqual(off[name]["draft_rewrite"], on[name]["draft_rewrite"])
            self.assertEqual(off[name]["draft_to_final"], on[name]["draft_to_final"])
        self.assertIn("draft_vs_final_disagreement", on["baseline(raw)"])

    def test_only_the_baseline_row_pays_for_it(self):
        """即使开着，也**只有基线那一遍**算（曲线上那几行没人看这一项）。"""
        on = CR.sweep(self._utts(), None, (None, 1, 2), want_disagreement=True)
        self.assertIn("draft_vs_final_disagreement", on["baseline(raw)"])
        self.assertNotIn("draft_vs_final_disagreement", on["holdback=1"])
        self.assertNotIn("draft_vs_final_disagreement", on["holdback=2"])


class Warnings(unittest.TestCase):
    def test_old_format_without_buf_s_is_flagged(self):
        p = _write([{"kind": "draft", "utt": 0, "text": "a"}, {"kind": "final", "utt": 0, "text": "a"}])
        res = CR.build_report(p, ks=(None,))
        self.assertTrue(any("buf_s" in w for w in res["warnings"]))

    def test_field_written_files_get_no_buf_s_warning(self):
        p = _write([_draft(0, "a"), _final(0, "a")])
        res = CR.build_report(p, ks=(None,))
        self.assertFalse(any("buf_s" in w for w in res["warnings"]))


class LiveLatency(unittest.TestCase):
    def _ev(self):
        def d(seg, t, buf_s, lag_s):
            return {**_draft(0, "x", t=t, buf_s=buf_s), "seg": seg, "lag_s": lag_s}
        seg_end = lambda seg, t: {"kind": "event", "event": "segment_end", "seg": seg, "t": t}
        fin = lambda seg, t: {**_final(0, "x", t=t), "seg": seg}
        return [d(0, 1.0, 1.0, 0.8), d(0, 2.0, 2.0, 0.6),        # 段 0：首字 = 第一份的 1.0+0.8
                seg_end(0, 5.0), d(1, 6.0, 1.2, 1.1),            # 段 1 的草稿可以先于段 0 的定稿
                fin(0, 8.5), fin(0, 9.0),                         # 段 0 两句定稿：取**第一句**
                seg_end(1, 12.0), fin(1, 14.0)]

    def test_numbers(self):
        lat = CR.live_latency(self._ev())
        self.assertEqual(lat["final_delay_s"]["n"], 2)
        self.assertAlmostEqual(lat["final_delay_s"]["max"], 3.5)     # 段 0：8.5 − 5.0（不是 9.0 − 5.0）
        self.assertAlmostEqual(lat["first_visible_s"]["max"], 2.3)   # 段 1：1.2 + 1.1
        self.assertEqual(lat["first_visible_s"]["n"], 2)             # 每段只取第一份
        self.assertEqual(lat["draft_lag_s"]["n"], 3)

    def test_final_without_its_segment_end_is_not_counted(self):
        ev = [e for e in self._ev() if not (e.get("event") == "segment_end" and e["seg"] == 1)]
        self.assertEqual(CR.live_latency(ev)["final_delay_s"]["n"], 1)

    def test_offline_replay_file_has_none(self):
        self.assertIsNone(CR.live_latency([_draft(0, "a"), _final(0, "a")]))

    def test_report_renders_it(self):
        res = CR.build_report(_write(self._ev()), ks=(None,))
        self.assertIn("段结束→定稿上屏", CR.render(res))


if __name__ == "__main__":
    unittest.main(verbosity=2)
