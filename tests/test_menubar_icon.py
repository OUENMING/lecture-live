"""菜单栏菠萝图标（`overlay._pineapple_icon`）的判据。不起窗口、不碰菜单栏。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import overlay                          # noqa: E402


def _alpha(img, x, y):
    """(x, y) 是 pt、y 向上；回那一点的 alpha（0–1）。"""
    rep = img.representations()[0]
    s = rep.pixelsWide() / img.size().width
    c = rep.colorAtX_y_(int(x * s), int(rep.pixelsHigh() - 1 - y * s))
    return c.alphaComponent() if c is not None else 0.0


class PineappleIcon(unittest.TestCase):
    def setUp(self):
        self.img = overlay._pineapple_icon()
        self.assertIsNotNone(self.img, "画不出来 → 菜单栏会退回耳机")

    def test_模板图_与SF耳机同尺寸_2x位图(self):
        self.assertTrue(self.img.isTemplate(), "不是模板图 → 深色菜单栏上一团黑")
        self.assertEqual((self.img.size().width, self.img.size().height), (16.0, 16.0))
        self.assertEqual(self.img.representations()[0].pixelsWide(), 32)

    def test_有叶冠_有果身外圈_菱格正中是孔_叶冠连着果身(self):
        k = 16.0 / 18.0                                    # 画的时候按 18pt 坐标、整体缩到 16
        self.assertGreater(_alpha(self.img, 9.0 * k, 16.0 * k), 0.5, "中间那片叶子尖")
        self.assertGreater(_alpha(self.img, 5.4 * k, 5.25 * k), 0.5, "果身左边外圈")
        self.assertLess(_alpha(self.img, 9.0 * k, 5.25 * k), 0.5, "果身正中应是一颗菱形孔（不是交叉点、不是实心）")
        self.assertGreater(_alpha(self.img, 9.0 * k, 7.4 * k), 0.5, "孔上方是格条")
        self.assertGreater(_alpha(self.img, 9.0 * k, 10.3 * k), 0.5, "叶冠连着果身（作者：不要有空格）")
        self.assertLess(_alpha(self.img, 1.0, 1.0), 0.05, "角落透明")


if __name__ == "__main__":
    unittest.main()
