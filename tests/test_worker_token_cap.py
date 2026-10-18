"""客户端要的输出上限 ≤ Worker 的封顶（`server/asr-worker/src/handler.js` 的 `LLM_MAX_TOKENS`）。

2026-10-18 抓到：Worker 封 4096，笔记讲解要 6000 → 走通行码的人讲解被**静默截断**
（截断的 JSON 解析失败 → 那一块重试或没生成），两边各自的测试都是绿的。
这里扫全仓库 `*_MAX_TOKENS = N` 常量，任何一个超过 Worker 封顶就红。
"""
from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent


class 封顶对得上(unittest.TestCase):
    def test_客户端常量不超过_Worker_封顶(self):
        js = (ROOT / "server" / "asr-worker" / "src" / "handler.js").read_text(encoding="utf-8")
        m = re.search(r"^const LLM_MAX_TOKENS = (\d+);", js, re.M)
        self.assertIsNotNone(m, "handler.js 里找不到 LLM_MAX_TOKENS —— 改名了就同步这条判据")
        cap = int(m.group(1))
        found = {}
        for f in sorted(ROOT.glob("*.py")):
            for name, n in re.findall(r"^([A-Z_]*MAX_TOKENS)\s*=\s*(\d+)", f.read_text(encoding="utf-8"), re.M):
                found[f"{f.name}:{name}"] = int(n)
        self.assertIn("note_v2.py:REVIEW_MAX_TOKENS", found, "扫描没扫到已知常量 —— 正则失效了")
        over = {k: v for k, v in found.items() if v > cap}
        self.assertEqual(over, {}, f"这些超过 Worker 封顶 {cap}（会被静默截断）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
