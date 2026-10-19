"""桌面笔记库（3.10，作者 2026-10-19）：`~/Desktop/烤菠萝的笔记库/<课号>/<日期>_<课号>.html`，
生成完自动打开；有 Obsidian 库的人库里照旧 md。合成数据、零网络、HOME 用临时目录。"""
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import obsidian_writer as ow    # noqa: E402
import paths                    # noqa: E402


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.d = pathlib.Path(self._tmp.name)
        self._old_sessions = ow.SESSIONS
        ow.SESSIONS = self.d / "sessions"          # 别往真 sessions/ 写
        ow.SESSIONS.mkdir()
        self.opened: list[str] = []

    def tearDown(self):
        ow.SESSIONS = self._old_sessions
        self._tmp.cleanup()

    def _writer(self, vault=None, course="ECON10790", open_fn="spy"):
        w = ow.ObsidianWriter(vault, course, mode="yes", desk=True, desk_home=self.d,
                              open_fn=self.opened.append if open_fn == "spy" else open_fn)
        w.append("Supply goes up.", "供给上升。", raw="Supply goes up.")
        return w

    def lib(self, course="ECON10790"):
        return self.d / "Desktop" / "烤菠萝的笔记库" / course


class NoVault(_Base):
    def test_没库也写_落到课号文件夹并自动打开(self):
        w = self._writer()
        self.assertTrue(w.writes_notes)
        msg = w.close()
        htmls = sorted(self.lib().glob("*.html"))
        self.assertEqual(len(htmls), 1, f"课号文件夹里该有一份 HTML：{list(self.lib().iterdir())}")
        self.assertRegex(htmls[0].name, r"^\d{4}-\d{2}-\d{2}_ECON10790\.html$")
        self.assertEqual(self.opened, [str(htmls[0])], "生成完要自动打开的正是这份 HTML")
        self.assertIn("笔记已保存", msg)
        self.assertIn(f"📂 桌面 › 烤菠萝的笔记库 › ECON10790 › {htmls[0].name}（已自动打开）", msg,
                      "收尾那句要写出位置（与问句卡同一口径）")
        self.assertNotIn("Obsidian 简版", msg, "没库就别提 Obsidian")

    def test_同日同课第二份不覆盖(self):
        self._writer().close()
        self._writer().close()
        self.assertEqual(len(list(self.lib().glob("*.html"))), 2)

    def test_打不开不连累笔记(self):
        def boom(_p):
            raise OSError("no open")
        msg = self._writer(open_fn=boom).close()
        self.assertIn("笔记已保存", msg)
        self.assertNotIn("已自动打开", msg, "没打开就别说打开了")
        self.assertEqual(len(list(self.lib().glob("*.html"))), 1)

    def test_卡上提示存到哪(self):
        self.assertEqual(self._writer().save_where, "桌面 › 烤菠萝的笔记库 › ECON10790")

    def test_课号里的斜杠不变成子目录(self):
        self.assertEqual(paths.course_notes_dir("A/B:C", home=self.d).name, "A-B-C")


class WithVault(_Base):
    def test_桌面放HTML_库里照旧md(self):
        vault = self.d / "vault"
        vault.mkdir()
        msg = self._writer(vault).close()
        self.assertEqual(len(list(self.lib().glob("*.html"))), 1, "桌面要有 HTML")
        self.assertEqual(len(list((vault / "Lectures").glob("*.md"))), 1, "库里照旧 md")
        self.assertIn("Obsidian 简版", msg)
        self.assertTrue(self.opened and self.opened[0].startswith(str(self.lib())),
                        "自动打开的是桌面那份")


class OptIn(_Base):
    def test_不开桌面也没库_照旧不写(self):
        w = ow.ObsidianWriter(None, "ECON10790", mode="yes")
        self.assertFalse(w.writes_notes)
        self.assertEqual(w.save_where, "")
        self.assertFalse((self.d / "Desktop").exists())


if __name__ == "__main__":
    unittest.main()
