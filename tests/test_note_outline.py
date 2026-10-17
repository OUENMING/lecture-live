"""纲要数字核（`note_outline`）的判据。

⭐ 作者 2026-10-17 第五轮：live 摘要层把两处错数原样带进了「课堂纲要」——
   ① `Q² − 12Q + 60 = 3Q² − 12Q + 60`（MC 应为 `−24Q`：教授 13:40:41 自己更正过）；
   ② 课上口误 `5.3÷3=2.89`（应为 1.767）。
   正文有三道核 + 更正账本，纲要一句没核 —— 而纲要是**先读的那一屏**。
   下面用 `sessions/2026-10-15_131620_ECON10790.chapters.jsonl` 里的**原句**钉住这两处
   （不读真文件 —— 测试要能离线跑）。

⚠️ 变异验证（每条判据都要能"把实现改坏就看到它红"）：
   · `_ledger_pairs` 改成 `return []` → ② 那条红；
   · `_reconcile_equation` 改成 `return None` → ① 那条红；
   · 除法那一档 `continue` 掉 → 除法用例红；
   · 代入那一档 `continue` 掉 → 代入用例红；
   · 结论替换那一支去掉 → 结论用例红；
   · `warns.append(...)` 改成 `pass` → "改不了必须标 ⚠" 红；
   · `_render_outline_section` 不接 `review` → 渲染集成用例红。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import note_outline as no        # noqa: E402
import obsidian_writer as ow     # noqa: E402

#: 正文里的函数表（AC/MC/TC）+ 三道核里那两条 5.3÷3 的 ⚠ 行 —— 账本的来源。
BODY = {
    "sections": [
        {"heading": "5. 概念回顾与计算",
         "prose": ["平均成本 $AC(Q)=Q^2-12Q+60$。",
                   "总成本 $TC(Q)=Q^3-12Q^2+60Q$，对 $Q$ 求导得到边际成本 $MC(Q)=3Q^2-24Q+60$。"]},
    ],
    "checks": [
        {"claim": "平均成本约为 2.89，停业价格等于该平均成本，即 2.89。",
         "how": "5.3÷3=1.767，不是2.89", "status": "mismatch", "recomputed": "5.3/3 ≈ 1.767"},
        {"claim": "5.3除以3等于2.89", "how": "计算5.3/3=1.7667，与2.89不符",
         "status": "mismatch", "recomputed": "计算5.3/3=1.7667，与2.89不符"},
    ],
}

LIVE_12Q = ("令边际成本等于平均成本得到 Q² − 12Q + 60 = 3Q² − 12Q + 60，"
            "所以 Q = 0 或 Q = 6，答案是 Q = 6。")
LIVE_289 = "停业价格（shutting down price）是 P 等于数量为 2.89 时的平均成本（average cost）。"


def _items(*zhs: str) -> dict:
    return {"chapters": [{"title_zh": "最小化函数的两种方法", "status": "final",
                          "t0": "13:37:43", "t1": "13:42:12",
                          "sentences": [{"zh": z, "en": "x", "terms": []} for z in zhs]}]}


def _zhs(out: dict) -> list:
    return [s["zh"] for s in out["chapters"][0]["sentences"]]


class TestOutlineNumbers(unittest.TestCase):
    # ── ① 两处 live 层错数（回归用例）───────────────────────────────────
    def test_纲里的_12Q_按课件函数表改成_24Q(self):
        """`Q²−12Q+60 = 3Q²−12Q+60` 解不出 `Q=0 或 Q=6` —— 课件里 MC 是 `−24Q`。"""
        out, fixes, warns = no.check(_items(LIVE_12Q), review=BODY)
        zh = _zhs(out)[0]
        self.assertIn("= 3Q² − 24Q + 60", zh)
        self.assertNotIn("= 3Q² − 12Q + 60", zh)   # 标记里仍留旧数（「3Q² − 12Q + 60 → …」）
        self.assertIn("已更正", zh)
        self.assertEqual(len(fixes), 1, fixes)
        self.assertEqual(warns, [])
        self.assertIn("依据课件函数表", fixes[0]["what"])

    def test_纲里的_2_89_跟正文账本改成_1_767(self):
        """纲要不带「5.3÷3」的算式，只能靠正文三道核的账本传过去。"""
        out, fixes, warns = no.check(_items(LIVE_289), review=BODY)
        zh = _zhs(out)[0]
        self.assertIn("数量为 1.767", zh)        # 正文那个数改了
        self.assertIn("已更正", zh)              # 标记里会留旧数（「2.89 → 1.767」），这是账，不是错
        self.assertIn("依据正文三道核", fixes[0]["what"])

    # ── ② 纲要自己就能算的三档 ─────────────────────────────────────────
    def test_纲里的除法自己算(self):
        body = {"sections": BODY["sections"]}          # 没有账本：只靠纲要这一句
        out, fixes, _w = no.check(_items("5.3 除以 3 等于 2.89。"), review=body)
        zh = _zhs(out)[0]
        self.assertIn("等于 1.767", zh)
        self.assertIn("已更正", zh, zh)
        self.assertIn("原写 2.89", zh)

    def test_纲里的代入按课件函数重算(self):
        out, fixes, _w = no.check(_items("在 Q = 4 处，AC(4) = 12，所以平均成本被拉低了。"),
                                  review=BODY)
        zh = _zhs(out)[0]
        self.assertIn("AC(4) = 28", zh, zh)            # 16 − 48 + 60 = 28
        self.assertIn("已更正", zh)

    def test_方程对但结论错_改结论(self):
        out, fixes, warns = no.check(
            _items("Q² − 12Q + 60 = 3Q² − 24Q + 60，所以 Q = 0 或 Q = 5。"), review=BODY)
        zh = _zhs(out)[0]
        self.assertIn("Q = 0 或 Q = 6", zh)
        self.assertNotIn("Q = 5", zh)
        self.assertIn("已更正", zh)
        self.assertEqual(warns, [])

    def test_改不了的必须标重算警告(self):
        """没有课件函数表、账本也帮不上 → 只能标 ⚠（作者：改不了就标 ⚠）。"""
        out, fixes, warns = no.check(_items(LIVE_12Q), review={"sections": []})
        zh = _zhs(out)[0]
        self.assertEqual(fixes, [])
        self.assertEqual(len(warns), 1, warns)
        self.assertIn("⚠ 重算", zh)
        self.assertIn("Q = 0", zh)                     # 按左边的式子实际只有 Q=0

    # ── ③ 不许误伤 ────────────────────────────────────────────────────
    def test_算得对的句子一个字不动(self):
        for zh in ("驻点（stationary points）是 x 等于 1 和 x 等于负 1。",
                   "另一种方法是令边际成本等于平均成本，因为它们在最小值处相等。",
                   "令边际成本等于平均成本得到 Q² − 12Q + 60 = 3Q² − 24Q + 60，"
                   "所以 Q = 0 或 Q = 6。"):
            out, fixes, warns = no.check(_items(zh), review=BODY)
            self.assertEqual(_zhs(out)[0], zh, f"误伤了：{zh}")
            self.assertEqual((fixes, warns), ([], []), zh)

    def test_账本里的小整数不许乱换(self):
        """`_is_precise` 那道闸：账本若出现 `3 → 5`，纲里所有 3 都会被换掉。"""
        body = {"sections": [], "checks": [
            {"claim": "q=3 时取最小", "how": "应为 5", "status": "mismatch", "recomputed": "5"},
        ]}
        out, _f, _w = no.check(_items("在 Q = 3 处，AC(3) 最小。"), review=body)
        self.assertIn("Q = 3", _zhs(out)[0])

    def test_跑两遍不会叠两个已更正(self):
        out1, _f, _w = no.check(_items(LIVE_12Q, LIVE_289), review=BODY)
        out2, fixes2, warns2 = no.check(out1, review=BODY)
        self.assertEqual(_zhs(out2), _zhs(out1), "第二遍又改了句子")
        self.assertEqual((fixes2, warns2), ([], []))

    def test_不改输入(self):
        items = _items(LIVE_12Q)
        before = items["chapters"][0]["sentences"][0]["zh"]
        no.check(items, review=BODY)
        self.assertEqual(items["chapters"][0]["sentences"][0]["zh"], before)


class TestOutlineSectionRendering(unittest.TestCase):
    def test_渲染出来的纲要有已更正和记账行(self):
        md = "\n".join(ow._render_outline_section(_items(LIVE_12Q), review=BODY))
        self.assertIn("3Q² − 24Q + 60", md)
        self.assertIn("已更正", md)
        self.assertIn("纲要数字核：1 处已更正", md)

    def test_不给_review_时行为一个字不变(self):
        """老调用（测试/别的分支）不传 review → 不核、不加标记（向后兼容）。"""
        md = "\n".join(ow._render_outline_section(_items(LIVE_12Q)))
        self.assertIn("3Q² − 12Q + 60", md)
        self.assertNotIn("已更正", md)
        self.assertNotIn("纲要数字核", md)

    def test_没有可核的东西就不加记账行(self):
        md = "\n".join(ow._render_outline_section(_items("驻点（stationary points）是 1 和负 1。"),
                                                  review=BODY))
        self.assertNotIn("纲要数字核", md)


if __name__ == "__main__":
    unittest.main()
