"""开课面板小卡的纯函数：看点标签（`changelog.split_tag`）、按版本分组（`changelog.groups`）、
「已更新到 X」弹不弹（`whatsnew.updated_from`）。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import changelog as C                   # noqa: E402
import whatsnew as W                    # noqa: E402

LOG = """# Changelog
## [3.9.6] - 2026-10-20

### 更新看点
- [修复] 未来版本

## [3.9.5] - 2026-10-14

### 更新看点
- [改进] 「在听」小点不闪了
- 【新增】 卡片
- [3.9.4] 方括号不是标签
- **[修复]** 粗体包着的标签

## [3.9.4.1] - 2026-10-13

### 更新看点
- 没有标签的老写法

## [3.9.4] - 2026-10-12

### 更新看点
- 更老的
"""


class SplitTag(unittest.TestCase):
    def test_半角全角都认_别的方括号不认(self):
        self.assertEqual(C.split_tag("[新增] 甲"), ("新增", "甲"))
        self.assertEqual(C.split_tag("【修复】乙"), ("修复", "乙"))
        self.assertEqual(C.split_tag("[3.9.4] 丙"), ("", "[3.9.4] 丙"))
        self.assertEqual(C.split_tag("[其他] 丁"), ("", "[其他] 丁"))

    def test_只有标签没有正文_不当标签(self):
        self.assertEqual(C.split_tag("[新增]"), ("", "[新增]"))


class Groups(unittest.TestCase):
    def test_区间是左开右闭_新的在前_比上界新的不报(self):
        gs = C.groups(LOG, "3.9.4", "3.9.5")
        self.assertEqual([g["version"] for g in gs], ["3.9.5", "3.9.4.1"])
        self.assertEqual(gs[0]["date"], "2026-10-14")
        self.assertEqual(gs[0]["items"], [("改进", "「在听」小点不闪了"), ("新增", "卡片"),
                                          ("", "[3.9.4] 方括号不是标签"), ("修复", "粗体包着的标签")])
        self.assertEqual(gs[1]["items"], [("", "没有标签的老写法")])

    def test_下界不在库里_只报上界那一版(self):
        self.assertEqual([g["version"] for g in C.groups(LOG, "9.9", "3.9.5")], ["3.9.5"])

    def test_上界不在库里_空(self):
        self.assertEqual(C.groups(LOG, "3.9.4", "4.0"), [])

    def test_版本数有上限(self):
        self.assertEqual(len(C.groups(LOG, "3.9.4", "3.9.6", max_versions=2)), 2)
        self.assertEqual(len(C.groups(LOG, "3.9.4", "3.9.6")), 3)


class Rows(unittest.TestCase):
    def test_带标签的最后一行_整颗标签都在列表高度之内_且与字同中线(self):
        """3.9.5 发版后拍图：标签 17pt 比一行字高，行高只按字算 → 最后一颗标签被切掉 2pt。"""
        g = [{"version": "9.9.9", "date": "", "items": [("改进", "一行字")]}]
        views, h = W._rows(g, 400.0, [])
        bottoms = [v.frame().origin.y + v.frame().size.height for v in views]
        self.assertLessEqual(max(bottoms), h + 1e-6, (bottoms, h))
        chip, text = views[-2], views[-1]
        mid = lambda v: v.frame().origin.y + v.frame().size.height / 2.0
        self.assertLess(abs(mid(chip) - (text.frame().origin.y + W._line_h() / 2.0)), 0.6)


class UpdatedFrom(unittest.TestCase):
    V = C.versions(LOG)

    def test_升级了_从上次看过的那版报起(self):
        self.assertEqual(W.updated_from("3.9.4", "3.9.5", self.V), "3.9.4")

    def test_全新安装_同版_降级_不认识的_都不弹(self):
        for seen, cur in (("", "3.9.5"), ("3.9.5", "3.9.5"), ("3.9.6", "3.9.5"),
                          ("1", "3.9.5"), ("3.9.4", "8.0")):
            self.assertEqual(W.updated_from(seen, cur, self.V), "", (seen, cur))


if __name__ == "__main__":
    unittest.main()
