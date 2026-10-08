"""本地小模型输出后处理：元话前缀、翻成上一句的回显检测与一次重试。全离线（假 mlx_lm）。"""
import os, sys, types, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import translator as T


class Clean(unittest.TestCase):
    def test_strips_meta_prefix(self):
        for raw in ("以下是译文：需求是有弹性的", "译文: 需求是有弹性的", "好的，以下是翻译：需求是有弹性的",
                    "翻译结果：需求是有弹性的", "这是中文翻译：需求是有弹性的"):
            self.assertEqual(T.clean_local_zh(raw), "需求是有弹性的", raw)

    def test_does_not_touch_real_content(self):
        for raw in ("需求是有弹性的", "翻译是一门艺术", "他说：需求是有弹性的", "中文里没有这个词"):
            self.assertEqual(T.clean_local_zh(raw), raw, raw)

    def test_only_prefix_left_keeps_original(self):
        self.assertEqual(T.clean_local_zh("译文："), "译文：")


class Echo(unittest.TestCase):
    PREV = "总收入等于价格乘以销售数量，当需求缺乏弹性时提价会增加收入"

    def test_same_sentence_again_is_echo(self):
        self.assertTrue(T.zh_echoes_previous("总收入等于价格乘以销售数量，提价会增加收入", self.PREV))

    def test_next_sentence_on_same_topic_is_not(self):
        self.assertFalse(T.zh_echoes_previous("当需求富有弹性时，提高价格实际上会减少总收入", self.PREV))

    def test_short_prev_never_echo(self):
        self.assertFalse(T.zh_echoes_previous("好的", "好的好的"))

    def test_hand_counted_boundary(self):
        prev = "abcdefghijklmnopqrst"             # 20 字 → 19 个二元组；0.45×19 = 8.55 → 需要 ≥9 个重合
        zh8 = "abcdefghi"                         # 8 个二元组全在 prev 里
        zh10 = "abcdefghijk"                      # 10 个
        self.assertFalse(T.zh_echoes_previous(zh8, prev))
        self.assertTrue(T.zh_echoes_previous(zh10, prev))


class FakeMlx:
    """替换 mlx_lm：按顺序吐预先写好的整段输出（每次 stream_generate 调用消费一条）。"""

    def __init__(self, outputs):
        self.outputs, self.calls = list(outputs), []

    def install(self):
        mod = types.ModuleType("mlx_lm")
        su = types.ModuleType("mlx_lm.sample_utils")
        outer = self

        def stream_generate(model, tok, prompt=None, max_tokens=0, sampler=None):
            outer.calls.append(prompt)
            text = outer.outputs.pop(0)
            for i in range(0, len(text), 5):
                yield types.SimpleNamespace(text=text[i:i + 5])
        mod.stream_generate = stream_generate
        su.make_sampler = lambda **kw: None
        sys.modules["mlx_lm"], sys.modules["mlx_lm.sample_utils"] = mod, su

    @staticmethod
    def uninstall():
        sys.modules.pop("mlx_lm", None)
        sys.modules.pop("mlx_lm.sample_utils", None)


def make_translator():
    t = T.Translator("m", None, max_context=2)
    t._ensure = lambda: None
    t._apply_chat = lambda user_content, system=T.SYSTEM_PROMPT: user_content    # prompt 即 user 内容，便于断言
    return t


class Stream(unittest.TestCase):
    def tearDown(self):
        FakeMlx.uninstall()

    def test_meta_prefix_removed_from_result(self):
        fm = FakeMlx(["ZH: 以下是译文：需求缺乏弹性EN: demand is inelastic"]); fm.install()
        r = make_translator().fix_and_translate_stream("demand is inelastic", [])
        self.assertEqual(r.zh, "需求缺乏弹性")

    def test_echo_triggers_one_retry_without_context(self):
        t = make_translator()
        t._last_zh = "总收入等于价格乘以销售数量，当需求缺乏弹性时提价会增加收入"
        fm = FakeMlx([
            "ZH: 总收入等于价格乘以销售数量，提价会增加收入EN: so revenue goes up",     # 翻成了上一句
            "ZH: 所以收入会上升EN: so revenue goes up",                                  # 重试：正常
        ]); fm.install()
        r = t.fix_and_translate_stream("so revenue goes up", ["total revenue is price times quantity"])
        self.assertEqual(r.zh, "所以收入会上升")
        self.assertEqual(len(fm.calls), 2)
        self.assertIn("total revenue is price times quantity", fm.calls[0])
        self.assertNotIn("total revenue is price times quantity", fm.calls[1])        # 重试不带上下文
        self.assertEqual(t.echo_retries, 1)
        self.assertEqual(t._last_zh, "所以收入会上升")                                 # 记的是最终采用的

    def test_retry_happens_at_most_once_and_keeps_translation(self):
        t = make_translator()
        t._last_zh = "总收入等于价格乘以销售数量，当需求缺乏弹性时提价会增加收入"
        same = "ZH: 总收入等于价格乘以销售数量，提价会增加收入EN: x y z"
        fm = FakeMlx([same, same, same]); fm.install()
        r = t.fix_and_translate_stream("x y z", ["ctx"])
        self.assertEqual(len(fm.calls), 2)                                           # 不会无限重试
        self.assertTrue(r.zh)                                                         # 重试仍回显 → 保留，不置空

    def test_no_context_no_retry(self):
        t = make_translator()
        t._last_zh = "总收入等于价格乘以销售数量，当需求缺乏弹性时提价会增加收入"
        fm = FakeMlx(["ZH: 总收入等于价格乘以销售数量，提价会增加收入EN: x"]); fm.install()
        t.fix_and_translate_stream("x", [])
        self.assertEqual(len(fm.calls), 1)

    def test_first_sentence_never_retries(self):
        fm = FakeMlx(["ZH: 需求缺乏弹性EN: d"]); fm.install()
        make_translator().fix_and_translate_stream("d", ["ctx"])
        self.assertEqual(len(fm.calls), 1)


if __name__ == "__main__":
    unittest.main()
