#!/usr/bin/env python3
"""自动语种闸（`lang_gate`）的判据 —— L-2「判出中文就切 `zh` 档」。

    ClassLive.app/Contents/MacOS/python tests/test_lang_gate.py

钉住四件事（任务单 `docs/TASKS-auto-lang-20261116.md` L-2）：

  1. `no_llm` = 「这一档发不发模型请求」：`raw` / `zh` 真，`both` / `en` / 未知值假。
     ⚠️ 它与「别上云识别」**不是**同一条判据 —— 见第 3 条。
  2. `LangGate`：连续 3 句 `zh` 才切、只切一次、切过不切回；`ever_off`（用户手动换过档）
     之后永不切。
  3. `main.cloud_asr_off_reason`：`zh` **必须照常上云**（返回 `None`），`raw` 仍是「纯转录档」。
     这一条与 1 相反 —— 它正是 `no_llm` **不许**拿去判上云的那个理由。
  4. `overlay.next_trans_mode`：`zh` → `both`（胶囊不崩），其余 `both ↔ en`（纯转录 11-17 起不在胶囊里）。

⭐ 每条断言都问过「把实现改坏会不会红」（交回里记了两次变异实验）。
"""
from __future__ import annotations

import os
import random
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import lang_gate as L                                            # noqa: E402


class NoLLMTest(unittest.TestCase):
    """"这一档一个模型请求都不发"。"""

    def test_raw_and_zh(self):
        self.assertTrue(L.no_llm("raw"))
        self.assertTrue(L.no_llm("zh"))

    def test_translating_modes(self):
        for mode in ("both", "en"):
            self.assertFalse(L.no_llm(mode), mode)

    def test_unknown_and_empty_are_false(self):
        # 判据越保守越好：认不出就**当它要发请求**，别静默省掉档位判据（反过来会多发请求）。
        for mode in (None, "", "   ", "RAW", "zh ", "local", 0):
            self.assertFalse(L.no_llm(mode), repr(mode))

    def test_matches_the_constant(self):
        # 只许在 `NO_LLM_MODES` 里加档 —— 这个用例就是防有人偷偷在别处写 `mode in (...)`。
        self.assertEqual(set(L.NO_LLM_MODES), {"raw", "zh"})
        for mode in ("both", "en", "raw", "zh"):
            self.assertEqual(L.no_llm(mode), mode in L.NO_LLM_MODES)


class LangGateTest(unittest.TestCase):
    """「连着 3 句中文 → 切一次」。"""

    def test_three_zh_fires_once(self):
        g = L.LangGate()
        self.assertEqual([g.observe(x) for x in "zh zh zh".split()], [False, False, True])
        self.assertTrue(g.fired)

    def test_non_consecutive_does_not_fire(self):
        g = L.LangGate()
        self.assertEqual([g.observe(x) for x in "zh en zh zh".split()], [False] * 4)
        self.assertFalse(g.fired)

    def test_never_fires_again(self):
        g = L.LangGate()
        for x in ("zh", "zh", "zh"):
            g.observe(x)
        # 切过之后连一长串中文也不再动作（不切回、也不重复切）
        self.assertEqual([g.observe(x) for x in "zh zh zh zh en zh".split()], [False] * 6)

    def test_ever_off_at_construction(self):
        g = L.LangGate(ever_off=True)
        self.assertEqual([g.observe("zh") for _ in range(6)], [False] * 6)
        self.assertFalse(g.fired)

    def test_ever_off_at_call_time_latches(self):
        g = L.LangGate()
        g.observe("zh")
        g.observe("zh")
        self.assertFalse(g.observe("zh", ever_off=True), "用户手动换档那一刻不该顺带切 zh")
        # 之后即使用户切回 both（`ever_off` 不再传真），也不再自动切
        self.assertFalse(g.observe("zh"))
        self.assertFalse(g.observe("zh"))
        self.assertFalse(g.observe("zh"))
        self.assertFalse(g.fired)

    def test_empty_lang_breaks_the_streak(self):
        # 本地识别 / 语种没认出来的句子（`lang == ""`）与英文一样把连击清零 ——
        # 「没认出来」不等于「中文」。
        g = L.LangGate()
        self.assertEqual([g.observe(x) for x in ("zh", "zh", "", "zh")], [False] * 4)
        self.assertFalse(g.fired)

    def test_streak_is_readable(self):
        g = L.LangGate()
        g.observe("zh")
        g.observe("zh")
        self.assertEqual(g.streak, 2)
        g.observe("en")
        self.assertEqual(g.streak, 0)

    def test_threshold_is_parameterised(self):
        # 变异实验会用把阈值改成 2 来验 red——这里先钉住「阈值真的是可配的」。
        g = L.LangGate(threshold=2)
        self.assertEqual([g.observe(x) for x in ("zh", "zh")], [False, True])

    # ---- 性质：整段序列上「至多触发一次」+「触发点恰是第 3 个连击」 -----------------
    def test_property_fires_at_most_once_at_the_third_consecutive_zh(self):
        rng = random.Random(20261116)
        alphabet = ("zh", "zh", "zh", "en", "")
        for _ in range(400):
            seq = [rng.choice(alphabet) for _ in range(rng.randint(0, 12))]
            g = L.LangGate()
            fires = [g.observe(x) for x in seq]
            self.assertLessEqual(sum(fires), 1, seq)
            if not any(fires):
                continue
            i = fires.index(True)
            # 触发那一下之前正好是 3 连 zh（含自己）
            self.assertGreaterEqual(i, 2)
            self.assertTrue(all(L.is_zh_lang(x) for x in seq[i - 2:i + 1]), seq)
            # 更早的地方没有出现过 3 连（否则早该触发了）
            run = 0
            for x in seq[:i - 2]:
                run = run + 1 if L.is_zh_lang(x) else 0
                self.assertLess(run, L.THRESHOLD, seq)
            # 触发之后恒 False
            self.assertEqual([g.observe(x) for x in seq], [False] * len(seq))

    def test_property_ever_off_never_fires(self):
        rng = random.Random(4)
        for _ in range(200):
            seq = [rng.choice(("zh", "en", "")) for _ in range(rng.randint(1, 10))]
            g = L.LangGate()
            got = [g.observe(x, ever_off=True) for x in seq]
            self.assertEqual(got, [False] * len(seq), seq)


class CloudASRStaysOnForZhTest(unittest.TestCase):
    """⭐⭐ `zh` **必须照常上云识别** —— 全表唯一一处 `zh` 与 `raw` 不同。"""

    BASE = dict(cfg_enabled=True, engine="auto", mode="both", source="mic", speed=1.0)

    def test_zh_keeps_cloud_asr(self):
        import main
        self.assertIsNone(main.cloud_asr_off_reason(**{**self.BASE, "mode": "zh"}),
                          "zh 档关掉云端识别 = 退回本地英文模型，中文全乱（任务单开头那条）")

    def test_raw_still_off(self):
        import main
        self.assertEqual(main.cloud_asr_off_reason(**{**self.BASE, "mode": "raw"}), "纯转录档")

    def test_no_llm_is_not_reused_for_cloud_asr(self):
        # 防「图省事把 2128 行也换成 no_llm」：那一改会让上面第一条立刻红。
        import main
        import inspect
        src = inspect.getsource(main.cloud_asr_off_reason)
        self.assertNotIn("lang_gate.no_llm(", src,
                         "cloud_asr_off_reason 不许用 no_llm 判档（zh 要上云）")


class NoRawLiteralLeftBehindTest(unittest.TestCase):
    """`note_worker.py` 那 4 处（68 / 93 / 154 / 269）全都改成 `lang_gate.no_llm` 了。

    行为只测得到 `lost_explain` / `backfill` / `recap` 三条；`keypoints_fn` 与
    `review_state(expected=…)` 直接进 writer，没有可观察的替身 —— 所以在这里钉死
    「整个文件里不再有 `"raw"` 字面量」。漏一处就是中文课多花一次请求。
    """

    def test_note_worker_has_no_raw_literal(self):
        import pathlib
        src = (pathlib.Path(__file__).resolve().parent.parent / "note_worker.py").read_text(
            encoding="utf-8")
        self.assertNotIn('"raw"', src)


class NextTransModeTest(unittest.TestCase):
    """胶囊轮换：`zh` 不在循环里，点一下回 `both`（不许 ValueError）。"""

    def test_zh_goes_back_to_both(self):
        import overlay
        self.assertEqual(overlay.next_trans_mode("zh"), "both")

    def test_cycle_is_unchanged(self):
        import overlay
        self.assertEqual(overlay.next_trans_mode("both"), "en")
        self.assertEqual(overlay.next_trans_mode("en"), "both")
        self.assertEqual(overlay.next_trans_mode("raw"), "both")   # raw 已不在胶囊循环里

    def test_unknown_mode_falls_back_to_both(self):
        import overlay
        self.assertEqual(overlay.next_trans_mode("nope"), "both")

    def test_chip_tables_have_zh(self):
        # 缺一项就是 KeyError / 静默 no-op（`set_trans_mode` 的白名单就是这张表的键）
        import overlay
        for table in (overlay.TRANS_MODE_TITLE, overlay.TRANS_MODE_DESC,
                      overlay.TRANS_MODE_SYMBOL):
            self.assertIn("zh", table)
        self.assertEqual(overlay.TRANS_MODE_TITLE["zh"], "中文")
        self.assertEqual(overlay.TRANS_MODE_SYMBOL["zh"], overlay.TRANS_MODE_SYMBOL["raw"])

    def test_real_overlay_cycles_out_of_zh(self):
        """真胶囊：`set_trans_mode("zh")` 生效，再点一下回到 both（不是三条路各自算一遍）。"""
        try:
            import overlay
            o = overlay.Overlay()
        except Exception as e:                                   # noqa: BLE001
            self.skipTest(f"AppKit 不可用, 跳过: {type(e).__name__}: {e}")
        try:
            o.set_trans_mode("zh")
            self.assertEqual(o._trans_mode, "zh", "set_trans_mode('zh') 被白名单挡掉了")
            o._cycle_translate()
            self.assertEqual(o._trans_mode, "both")
        finally:
            o.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
