"""收尾「合盖会暂停笔记」提示的判据：只在笔记真的交给了后台时出现。

卡用真 `wrapup.build()`（要 AppKit，不开课、不碰 `sessions/`）；Overlay 只 `__new__` 出最小替身去调
`_wrapup_finish`（那是主线程把结论画到卡上的唯一出口）。
"""
import os
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import main  # noqa: E402


def _finish_on_real_card(msg: str):
    import overlay
    import wrapup
    card = wrapup.build()
    assert card is not None, "卡片没建起来（这条判据需要 AppKit）"
    ov = overlay.Overlay.__new__(overlay.Overlay)
    ov._wrapup_card = card
    ov._wrapup_finish(True, msg)
    return card, card["_status_lbl"].stringValue(), card["_height"]()


class LidHint(unittest.TestCase):
    def test_交给后台的文案带提示_且保留原句(self):
        m = main.handoff_msg()
        self.assertIn(main.HANDOFF_LINE, m)
        self.assertIn("合盖会暂停", m)
        self.assertIn("下次打开 ClassLive 会接着生成", m)

    def test_卡上待生成时看得到提示_没笔记待生成时没有(self):
        c1, t1, h1 = _finish_on_real_card(main.handoff_msg())
        c2, t2, h2 = _finish_on_real_card("已存入 Obsidian → /tmp/x/Lectures/a.md")
        try:
            self.assertIn("合盖会暂停", t1)
            self.assertNotIn("合盖", t2)
            self.assertNotIn("合盖", c2["_hint_lbl"].stringValue())
        finally:
            c1["close"]()
            c2["close"]()

    def test_高度按实测文字重排_提示那行不被裁(self):
        import wrapup
        c1, t1, h1 = _finish_on_real_card(main.HANDOFF_LINE)          # 只有原句
        c2, t2, h2 = _finish_on_real_card(main.handoff_msg())         # 多一行提示
        try:
            # 多出来的文字必须让卡变高（measure_text_h 同源），而不是被静默裁掉
            f = c2["_status_lbl"].font()
            need = wrapup.measure_text_h(t2, wrapup.WIDTH - 2 * wrapup.PAD, f)
            self.assertGreater(h2, h1)
            self.assertGreaterEqual(float(c2["_status_lbl"].frame().size.height) + 0.5, need)
        finally:
            c1["close"]()
            c2["close"]()

    def test_停留时间_交给后台的更久(self):
        self.assertGreater(main.wrapup_dwell_s(True), main.wrapup_dwell_s(False))
        self.assertEqual(main.wrapup_dwell_s(False), 3.0)

    def test_接线_只有真派给后台那条路用提示_进程内路径不用(self):
        src = (ROOT / "main.py").read_text(encoding="utf-8")
        i = src.index("if _spawned is None:\n                    threading.Thread(target=_wrap")
        j = src.index("handoff_msg()", i)
        k = src.index("else:", i)
        self.assertLess(k, j, "handoff_msg() 必须在 `_spawned is None` 的 else 分支里")
        self.assertEqual(src.count("handoff_msg()"), 2)     # 定义 + 这一处调用
        self.assertEqual(src.count("LID_HINT"), 2)          # 定义 + handoff_msg 里
        self.assertIn("wrapup_dwell_s(bool(_spawned))", src)


if __name__ == "__main__":
    unittest.main()
