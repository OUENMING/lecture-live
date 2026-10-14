"""「有新版本」卡上列哪几条（`whatsnew.offer_points`，纯函数）。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import whatsnew as W                    # noqa: E402


class OfferPoints(unittest.TestCase):
    def test_单版本_去掉版本前缀_最多四条_报剩几条(self):
        s = "\n".join(f"3.9.5 · 第{i}条" for i in range(1, 8))
        pts, more = W.offer_points(s)
        self.assertEqual(pts, ["第1条", "第2条", "第3条", "第4条"])
        self.assertEqual(more, 3)

    def test_跨版本_保留版本号好分清(self):
        pts, _ = W.offer_points("3.9.5 · 甲\n3.9.4 · 乙")
        self.assertEqual(pts, ["3.9.5 · 甲", "3.9.4 · 乙"])

    def test_去掉markdown和行首圆点(self):
        pts, more = W.offer_points("· **加粗** 和 `代码`\n- 另一条\n\n")
        self.assertEqual((pts, more), (["加粗 和 代码", "另一条"], 0))

    def test_空摘要(self):
        self.assertEqual(W.offer_points(""), ([], 0))


if __name__ == "__main__":
    unittest.main()
