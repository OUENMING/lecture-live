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
