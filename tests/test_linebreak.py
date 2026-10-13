"""linebreak: 中文按词组折行(纯函数, 不起 AppKit)。

宽度用「每字 10px」的假尺子 —— 验的是**断点挑在哪**, 不是字宽。
真字宽下的整体效果见 linebreak.py 文件头的离线评估(1459 句真课定稿)。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import linebreak  # noqa: E402


SPEC_ORPHAN_W = 40.0    # 孤尾 = 尾行不足 4 个字(假尺子每字 10px)。写死在测试里 ——
                        # 引用实现里的常量, 把它改成 0 判据也跟着失效


def W(s: str) -> float:
    return 10.0 * len(s)


def natural(text: str, max_w: float) -> int:
    """AppKit 逐字折的行数(假尺子下 = 向上取整)。"""
    per = int(max_w // 10)
    return max(1, -(-len(text) // per))


class TestPhrases(unittest.TestCase):
    def test_matches_budoux_reference(self):
        """照搬的打分循环必须与 budoux 0.9.3 逐字一致(README 的官方例句)。"""
        self.assertEqual(linebreak.phrases("今天是晴天。"), ["今天", "是", "晴天。"])

    def test_model_ships_with_repo(self):
        """模型文件必须真在仓库里: 更新走 `git pull`, 被 ignore 或藏在软链接后面的文件
        朋友那台拿不到 —— 而模型缺失只会**静默**退回逐字折。
        (2026-10-13 真踩过: 先放进了 `assets/`, 那是指向主仓库、且被 info/exclude 的软链接。)"""
        import subprocess
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = linebreak._MODEL_PATH
        self.assertEqual(os.path.realpath(path), os.path.join(os.path.realpath(root), "vendor", "budoux", "zh-hans.json"),
                         "模型路径经过了软链接")
        r = subprocess.run(["git", "-C", root, "check-ignore", "-q", "--no-index", path], capture_output=True)
        self.assertEqual(r.returncode, 1, "模型文件被 git 忽略了")

    def test_concatenation_is_identity(self):
        s = "所以这是一种公共池塘资源：它有竞争性，但不具排他性。"
        self.assertEqual("".join(linebreak.phrases(s)), s)


class TestWrap(unittest.TestCase):
    S = "严格来说这是第一步，但我喜欢把它称为第二步，因为第一步在这里已经完成了。"

    def _bounds(self, s):
        b, k = set(), 0
        for p in linebreak.phrases(s):
            k += len(p)
            b.add(k)
        return b

    def test_single_line_untouched(self):
        self.assertEqual(linebreak.wrap("很短的一句。", W, 300, natural_lines=1, final=True),
                         "很短的一句。")

    def test_breaks_only_at_phrase_boundaries(self):
        max_w = 200.0
        out = linebreak.wrap(self.S, W, max_w, natural_lines=natural(self.S, max_w), final=True)
        self.assertEqual(out.replace("\n", ""), self.S, "折行不许增删字")
        b, k = self._bounds(self.S), 0
        for line in out.split("\n")[:-1]:
            k += len(line)
            self.assertIn(k, b, f"折在词组中间: {out!r}")

    def test_never_more_lines_than_natural(self):
        for max_w in (120.0, 160.0, 200.0, 260.0, 340.0):
            n = natural(self.S, max_w)
            out = linebreak.wrap(self.S, W, max_w, natural_lines=n, final=True)
            self.assertLessEqual(out.count("\n") + 1, n, f"宽 {max_w} 多出一行(档位会静默裁掉)")
            for line in out.split("\n"):
                self.assertLessEqual(W(line), max_w, f"有一行放不下: {line!r}")

    def test_final_avoids_orphan_tail(self):
        """找出贪心折法会留孤尾的宽度, 定稿(DP)在那些宽度上都要避开。"""
        hit = 0
        for cols in range(8, len(self.S)):
            max_w = 10.0 * cols
            n = natural(self.S, max_w)
            if n < 2:
                continue
            greedy = linebreak.wrap(self.S, W, max_w, natural_lines=n, final=False)
            if W(greedy.split("\n")[-1]) >= SPEC_ORPHAN_W:
                continue
            final = linebreak.wrap(self.S, W, max_w, natural_lines=n, final=True)
            if final.count("\n") == greedy.count("\n"):
                hit += 1
                self.assertGreaterEqual(W(final.split("\n")[-1]), SPEC_ORPHAN_W,
                                        f"宽 {max_w}: 孤尾没避开 {final!r}")
        self.assertGreater(hit, 0, "样句在所有宽度下都没有孤尾 —— 判据形同虚设, 换一句")

    def test_final_equals_streaming_unless_orphan(self):
        """定稿那一刻**不许无故换断点**: 没孤尾的句子, 定稿折法与流式逐字相同。
        (每句都重折全局最优会让 85% 的两行句在定稿时挪字 —— 见 linebreak 文件头。)"""
        for cols in range(8, len(self.S)):
            max_w = 10.0 * cols
            n = natural(self.S, max_w)
            g = linebreak.wrap(self.S, W, max_w, natural_lines=n, final=False)
            if W(g.split("\n")[-1]) >= SPEC_ORPHAN_W:
                self.assertEqual(linebreak.wrap(self.S, W, max_w, natural_lines=n, final=True), g,
                                 f"宽 {max_w}: 没孤尾却在定稿时换了断点")

    def test_streaming_is_greedy_prefix_stable(self):
        """流式(final=False)是贪心: 前缀变长时, 已经折好的上一行不往回挪。"""
        max_w = 150.0
        prev_first = None
        for end in range(16, len(self.S) + 1):
            s = self.S[:end]
            out = linebreak.wrap(s, W, max_w, natural_lines=natural(s, max_w), final=False)
            first = out.split("\n")[0]
            if prev_first is not None and "\n" in out and len(first) < len(prev_first):
                # 只允许「最后一个词组被下一个字改了分词」这种一次性回退
                self.assertGreaterEqual(len(first), len(prev_first) - 4, (prev_first, first))
            if "\n" in out:
                prev_first = first

    def test_overlong_phrase_falls_back_to_chars(self):
        s = "这是" + "国" * 30 + "。"
        out = linebreak.wrap(s, W, 100.0, natural_lines=natural(s, 100.0), final=True)
        self.assertEqual(out.replace("\n", ""), s)
        for line in out.split("\n"):
            self.assertLessEqual(W(line), 100.0)

    def test_missing_model_degrades_to_chars(self):
        saved = linebreak._model
        try:
            linebreak._model = {}
            self.assertEqual(linebreak.phrases("今天是晴天"), list("今天是晴天"))
        finally:
            linebreak._model = saved


if __name__ == "__main__":
    unittest.main()
