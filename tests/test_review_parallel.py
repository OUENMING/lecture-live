"""复习层分块并发（`note_v2.review`）的判据。不联网：`chat` 换成替身。

⚠️ 2026-10-17 v2 迁移：这一条判据原来钉的是 `obsidian_writer._review`（v1 那套）。
   v1 的分块/并发代码**已删**，不变量原样搬到这里 —— 迁的时候**只换被测对象**，
   三条断言（并发确实是 5 路 / 结果按块序合并 / 某块失败不影响别块）一条没动。
"""
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import note_v2                     # noqa: E402


def entries(n):
    return [{"ts": f"10:{i // 60:02d}:{i % 60:02d}", "en": f"sentence {i}.",
             "asr": "", "zh": ""} for i in range(n)]


def _fake(transcript, mapping=None):
    """替身 `chat`：按转录里的句号决定这是第几块（与 v1 判据同一手法）。

    ⚠️ v2 的 `chat(system, user, max_tokens)` 多两个参数 —— 签名跟着实现走。
    """
    def call(system, user, max_tokens):
        first = int(user.split("sentence ", 1)[1].split(".", 1)[0])
        ci = first // note_v2.REVIEW_CHUNK
        if mapping is not None:
            return mapping(ci)
        return {"sections": [{"heading": f"H{ci}", "ts": "10:00", "prose": ["p"]}],
                "title_zh": f"T{ci}"}
    return call


class ReviewParallel(unittest.TestCase):
    def test_各块同时发_结果按块序合并(self):
        live, peak, lock = [0], [0], threading.Lock()

        def call(system, user, max_tokens):
            first = int(user.split("sentence ", 1)[1].split(".", 1)[0])
            ci = first // note_v2.REVIEW_CHUNK
            with lock:
                live[0] += 1
                peak[0] = max(peak[0], live[0])
            time.sleep(0.3 if ci == 0 else 0.1)              # 第 0 块最后回来
            with lock:
                live[0] -= 1
            return {"sections": [{"heading": f"H{ci}", "ts": "10:00", "prose": ["p"]}],
                    "title_zh": f"T{ci}"}

        n = note_v2.REVIEW_CHUNK * 4
        t = time.monotonic()
        r = note_v2.review(entries(n), chat=call)
        dt = time.monotonic() - t
        self.assertEqual(peak[0], min(note_v2.REVIEW_WORKERS, 4), "没有并发")
        self.assertLess(dt, 0.6, "串行要 0.6 s 以上")
        got = [s["heading"].split("] ", 1)[1] for s in r["sections"]]
        self.assertEqual(got, ["H0", "H1", "H2", "H3"], "合并顺序不是块序")
        self.assertEqual(r.get("title_zh"), "T0", "meta 仍取第 0 块，哪怕它最后回来")

    def test_某块失败_其他块照收(self):
        def mapping(ci):
            return {} if ci == 1 else {"sections": [
                {"heading": f"H{ci}", "ts": "10:00", "prose": ["p"]}]}
        r = note_v2.review(entries(note_v2.REVIEW_CHUNK * 2), chat=_fake(None, mapping))
        # ⚠️ 只剩一块成功时**不加** `[HH:MM]` 前缀（`multi` 数的是**成功**的块）
        #    —— 前缀只为多块消歧，一块时它只是噪声。
        self.assertEqual([s["heading"] for s in r["sections"]], ["H0"])

    def test_单块时小标题不加时间前缀(self):
        """多块才加 `[HH:MM]` —— 单块加前缀是噪声（且 `_render` 里的锚点会重复）。"""
        r = note_v2.review(entries(5), chat=_fake(None))
        self.assertEqual([s["heading"] for s in r["sections"]], ["H0"])

    def test_chat炸了不抛(self):
        """`chat` 抛异常 = 那一块空 —— 绝不把整个复习层带下水（close() 的纪律）。"""
        def boom(system, user, max_tokens):
            raise RuntimeError("网络断了")
        self.assertEqual(note_v2.review(entries(10), chat=boom), {})

    def test_没有句子返回空(self):
        self.assertEqual(note_v2.review([], chat=_fake(None)), {})


if __name__ == "__main__":
    unittest.main()
