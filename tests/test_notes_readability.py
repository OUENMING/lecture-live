"""`scripts/notes_readability.py` 的判据：小样例把每个指标钉住。纯本地，无网。

⚠️ 这里是**算法规格**，不是文风评分：改动任何阈值（长句 90 字 / 重复句 12 字）
   都要连这份样例一起改，否则 `long_sent_ratio` / `dup_sent_ratio` 会静默变含义。
"""
import json
import os
import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import notes_readability as R               # noqa: E402

LONG = "这句话" + "很" * 95 + "长"          # CJK 98 字 → 长句
SAMPLE = f"""\
---
date: 2026-11-17
course: ECON10770
---

# 标题

## 一 · 第一段

正文一句。**值得注意的是**这里真的是套话，另起一段说别的。这一点非常重要。

> [!info] 这段课
> 🕐 12:30 – 12:51 · 🗣 222 句
> 覆盖度：章节 1/1

| a | b |
|---|---|
| 1 | 2 |

- 列表一
- 列表二

$$
E = mc^2
$$

```python
print("不该进正文")
```

<details><summary>展开</summary>
藏起来的东西
</details>

{LONG}。

## 二 · 续

这句话重复出现一次好不好。这句话重复出现一次好不好。

### 补充说明

另外「不仅」这样「而且」那样；「不是」这个「而是」那个。
"""


class TestAnalyze(unittest.TestCase):
    def setUp(self):
        self.m = R.analyze(SAMPLE)

    def test_headings(self):
        self.assertEqual(self.m["h2"], 2)
        self.assertEqual(self.m["h3"], 1)
        self.assertEqual(self.m["sections"], 2)          # sections = h2

    def test_heading_cont(self):
        self.assertEqual(self.m["heading_cont"], 2)      # 「续」+「补充说明」

    def test_noise_excluded(self):
        # 代码块 / 公式块 / details / callout 里的中文都不该进正文
        body = R._strip_noise(SAMPLE)
        for s in ("不该进正文", "藏起来的东西", "[!info]", "E = mc^2"):
            self.assertNotIn(s, body)
        self.assertGreater(self.m["chars_cjk"], 0)
        self.assertLess(self.m["chars_cjk"], R.cjk_count(SAMPLE))

    def test_boilerplate(self):
        b = self.m["boilerplate_by_word"]
        self.assertEqual(b["值得注意的是"], 1)
        self.assertEqual(b["非常重要"], 1)
        self.assertEqual(b["不仅…而且"], 1)
        self.assertEqual(b["不是…而是"], 1)
        self.assertEqual(self.m["boilerplate"], sum(b.values()))

    def test_long_sent(self):
        self.assertAlmostEqual(self.m["long_sent_ratio"],
                               1 / self.m["sent_n"], places=3)

    def test_dup_sent(self):
        self.assertGreater(self.m["dup_sent_ratio"], 0)
        self.assertLess(self.m["dup_sent_ratio"], 1)

    def test_para_and_bullet(self):
        self.assertGreaterEqual(self.m["para_n"], 3)
        self.assertGreater(self.m["bullet_ratio"], 0)
        self.assertLessEqual(self.m["bullet_ratio"], 1)
        self.assertLessEqual(self.m["para_cjk_median"], self.m["para_cjk_p90"])

    def test_empty(self):
        m = R.analyze("")
        self.assertEqual(m["chars_cjk"], 0)
        self.assertEqual(m["bullet_ratio"], 0.0)
        self.assertEqual(m["long_sent_ratio"], 0.0)


class TestCli(unittest.TestCase):
    def test_cli_json(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "n.md"
            p.write_text(SAMPLE, encoding="utf-8")
            r = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "notes_readability.py"),
                 str(p), "--json"],
                capture_output=True, text=True, env={**os.environ, "HOME": d})
            self.assertEqual(r.returncode, 0, r.stderr)
            out = json.loads(r.stdout)
            self.assertIn("n.md", out)
            self.assertEqual(out["n.md"]["h2"], 2)

    def test_cli_missing_file(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            r = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "notes_readability.py"),
                 str(pathlib.Path(d) / "nope.md")],
                capture_output=True, text=True, env={**os.environ, "HOME": d})
            self.assertEqual(r.returncode, 0)
            self.assertIn("没有这个文件", r.stdout)


if __name__ == "__main__":
    unittest.main()
