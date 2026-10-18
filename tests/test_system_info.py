"""3.9.7：测试报告的机器画像块（内存 / 芯片 / 草稿引擎）。"""
from __future__ import annotations
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import testmode


class SystemInfo(unittest.TestCase):
    def test_fields_and_values(self):
        info = testmode.system_info("parakeet", "内存 8 GB < 12 GB")
        self.assertEqual(info["draft_engine"], "parakeet")
        self.assertEqual(info["draft_reason"], "内存 8 GB < 12 GB")
        real = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30
        self.assertAlmostEqual(info["ram_gb"], real, delta=0.1)
        self.assertTrue(info["macos"])
        self.assertIn("system", testmode.BLOCKS, "块名没登记 → note_block 会把它丢进 events")

    def test_never_raises(self):
        with mock.patch("os.sysconf", side_effect=OSError), \
             mock.patch("subprocess.run", side_effect=OSError):
            info = testmode.system_info()
        self.assertIsNone(info["ram_gb"])
        self.assertEqual(info["chip"], "")

    def test_main_wires_block(self):
        src = (Path(__file__).resolve().parent.parent / "main.py").read_text()
        self.assertIn('tester.note_block("system", system_info(draft.name, draft.reason))', src)


if __name__ == "__main__":
    unittest.main()


class 块名都登记了(unittest.TestCase):
    def test_代码里每个_note_block_字面量都在_BLOCKS(self):
        """合并几条分支各自加块名，漏登记的会被 `note_block` 拒收（报告缺那一格）。

        2026-10-18 合并检查抓到 `"duty"` 漏了 —— 这里扫全仓库，以后再漏直接红。
        """
        import re
        root = Path(__file__).resolve().parent.parent
        used = {}
        for f in sorted(root.glob("*.py")):
            for name in re.findall(r'note_block\(\s*"([a-z_]+)"', f.read_text(encoding="utf-8")):
                used.setdefault(name, f.name)
        self.assertIn("duty", used, "扫描没扫到已知调用点 —— 正则失效了")
        missing = {n: f for n, f in used.items() if n not in testmode.BLOCKS}
        self.assertEqual(missing, {}, "这些块名没在 testmode.BLOCKS 登记")
