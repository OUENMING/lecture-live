"""`scripts/notes_gold_errors.py` 的判据：四种判定 + 归一化。纯本地，无网。

手造 3 条 gold + 一份小 md —— 覆盖 `not_covered` / `wrong` / `right` / `unclear`，
并专门钉住「`92−6Q`（U+2212 减号）能被 `92\\s*-\\s*6\\s*\\*?\\s*Q` 命中」这类归一化。
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import notes_gold_errors as GE                  # noqa: E402

GOLD = [
    {"id": "hit-right", "course": "ECON10770", "topic": "需求",
     "check": {"present": ["92\\s*-\\s*(?:6|2|12)\\s*\\*?\\s*Q?"],
               "bad_any": ["92\\s*-\\s*2\\s*\\*?\\s*Q(?![0-9])"],
               "good_all": ["92\\s*-\\s*6\\s*\\*?\\s*Q"]}},
    {"id": "hit-wrong", "course": "ECON10770", "topic": "垄断产量",
     "check": {"present": ["Q"],
               "bad_any": ["Q_?m\\s*=\\s*15", "Q_?c\\s*=\\s*30"]}},
    {"id": "hit-notcov", "course": "ECON10770", "topic": "渔业总现金",
     "check": {"present": ["450"]}},
    {"id": "hit-unclear", "course": "ECON10770", "topic": "说不清",
     "check": {"present": ["产量"], "bad_any": ["绝不存在"], "good_any": ["也对不上"]}},
    {"id": "other-course", "course": "SOC10020", "topic": "不该出现",
     "check": {"present": ["绝不可能命中"]}},
]

MD = """\
## 垄断

需求 $P=92−6Q$，MR 是 92 − 12Q。解出 Q_m = 15，这是个常见的错。

## 别的

产量这块课上讲了，但没给出数。
"""


class TestNormalize(unittest.TestCase):
    def test_minus_and_dollar(self):
        t = GE.normalize("需求 $P=92−6Q$，还有 \\(92–12Q\\)")
        self.assertIn("92-6Q", t)
        self.assertNotIn("$", t)
        self.assertNotIn("\\(", t)

    def test_times_and_fullwidth(self):
        t = GE.normalize("３×４ 与 5\\times 6 与 7·8，好")
        self.assertIn("3*4", t)         # 全角数字 → 半角，× → *
        self.assertIn("5*6", t)         # \times → *
        self.assertIn("7*8", t)         # · → *
        self.assertIn("7*8,好", t)      # 全角逗号 → 半角

    def test_bold_stripped(self):
        self.assertNotIn("**", GE.normalize("**重点**"))


class TestJudge(unittest.TestCase):
    def setUp(self):
        self.text = GE.normalize(MD)

    def test_right(self):
        r = GE.judge(GOLD[0], self.text)
        self.assertEqual(r["verdict"], "right", r)
        self.assertIn("⟦", r["evidence"])

    def test_wrong(self):
        r = GE.judge(GOLD[1], self.text)
        self.assertEqual(r["verdict"], "wrong", r)
        self.assertIn("Q_m = 15", r["evidence"])

    def test_not_covered(self):
        r = GE.judge(GOLD[2], self.text)
        self.assertEqual(r["verdict"], "not_covered", r)
        self.assertIn("缺", r["evidence"])

    def test_unclear(self):
        r = GE.judge(GOLD[3], self.text)
        self.assertEqual(r["verdict"], "unclear", r)


class TestCli(unittest.TestCase):
    def test_json_and_course_filter(self):
        with tempfile.TemporaryDirectory() as d:
            gp = pathlib.Path(d) / "g.json"
            gp.write_text(json.dumps(GOLD, ensure_ascii=False), encoding="utf-8")
            md = pathlib.Path(d) / "n.md"
            md.write_text(MD, encoding="utf-8")
            env = {**os.environ, "HOME": d}
            r = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "notes_gold_errors.py"),
                 str(md), "--course", "econ10770", "--gold", str(gp), "--json"],
                capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual(out["gold_n"], 4)          # 别课那条被滤掉
            self.assertEqual(out["summary"]["right"], 1)
            self.assertEqual(out["summary"]["wrong"], 1)
            self.assertEqual(out["summary"]["not_covered"], 1)
            self.assertEqual(out["summary"]["unclear"], 1)

    def test_no_such_course(self):
        with tempfile.TemporaryDirectory() as d:
            gp = pathlib.Path(d) / "g.json"
            gp.write_text(json.dumps(GOLD, ensure_ascii=False), encoding="utf-8")
            md = pathlib.Path(d) / "n.md"
            md.write_text(MD, encoding="utf-8")
            r = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "notes_gold_errors.py"),
                 str(md), "--course", "NOPE", "--gold", str(gp)],
                capture_output=True, text=True, env={**os.environ, "HOME": d})
            self.assertEqual(r.returncode, 1)


if __name__ == "__main__":
    unittest.main()
