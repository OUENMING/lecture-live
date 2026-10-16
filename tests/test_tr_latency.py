#!/usr/bin/env python3
"""⭐ 译文延迟（`tr_latency.TrTimer` + `livemetrics.final(lat=…)` + `testmode` 汇总）的判据。

    ClassLive.app/Contents/MacOS/python tests/test_tr_latency.py

## 钉住了什么（3.9.7 第 3 项）

1. ⭐ **只有第一个**中文增量落时刻（第二个不许把首字时间推后），数值单位是毫秒。
2. ⭐ `seg_to_zh_ms` 从那**一段音频结束**算起（比 `zh_first_ms` 早很多）；拿不到段尾
   时刻时只记前两个 —— 不为它编一个数。
3. ⭐ `tr_model` **只在真的出过中文**时才有值：失败 / 空译文时为空串，且不抛
   （它在定稿线程上，抛一次就是少一句字幕）。
4. ⭐ `.live.jsonl` 的 `final` 行只在**给了 `lat`** 时多那四个字段（老读端零感知）。
5. ⭐ `report.json` 的 `translation_latency`：`n` 只数等到过中文的句子，
   `by_model` 按引擎计数，一个数都没有时给 `None` 不给 0。

⚠️ 全程离线：桩翻译器只是 `sleep` 完回调，**不发请求**；写端都在临时目录。
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import time
import unittest

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import livemetrics as LM                                               # noqa: E402
import testmode as tm                                                  # noqa: E402
import tr_latency as TL                                                # noqa: E402


def _lines(p):
    with open(p, encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


class Timer(unittest.TestCase):
    def test_只有第一个增量落时刻(self):
        clk = [0.0]
        t = TL.TrTimer(clock=lambda: clk[0])
        seen: list = []
        cb = t.tap(seen.append)
        clk[0] = 0.25
        cb("中")                       # 第一个增量：这一刻就是"中文首字"
        clk[0] = 0.40
        cb("文")                       # 第二个：**不许**改首字时刻
        clk[0] = 0.60
        self.assertEqual(t.done(seg_t0=-1.0, model="deepseek-chat"),
                         (250.0, 600.0, 1250.0, "deepseek-chat"))
        self.assertEqual(seen, ["中", "文"], "tap 改了回调的透传")

    def test_桩翻译器_先sleep再回调(self):
        """真钟 + 桩翻译器：数值得**合理**（正数、首字不晚于返回、段尾更早）。"""
        def stub(en, ctx, on_zh=None, on_en=None):
            time.sleep(0.02)
            on_zh("中文")
            return "结果"

        t = TL.TrTimer()
        seg_t0 = time.monotonic() - 1.0
        stub("english", [], on_zh=t.tap(lambda d: None))
        first, done, seg, model = t.done(seg_t0=seg_t0, model="local")
        self.assertGreaterEqual(first, 15.0, f"首字 {first} ms —— sleep 了 20ms")
        self.assertLess(first, 2000.0, f"首字 {first} ms 不合理")
        self.assertGreaterEqual(done, first)
        self.assertGreaterEqual(seg, first + 900.0, "没有从段尾算起")
        self.assertEqual(model, "local")

    def test_失败_没有中文_模型为空且不抛(self):
        t = TL.TrTimer()
        try:
            raise RuntimeError("503")
        except RuntimeError:
            pass
        first, done, seg, model = t.done(seg_t0=time.monotonic(), model="deepseek-chat")
        self.assertIsNone(first, "没出中文却有首字时刻")
        self.assertIsNone(seg)
        self.assertEqual(model, "", "翻失败却报了一个引擎名（会把 by_model 说成翻出来了）")
        self.assertIsNotNone(done, "失败花的时间也是个数")

    def test_拿不到段尾时刻_只记前两个(self):
        clk = [0.0]
        t = TL.TrTimer(clock=lambda: clk[0])
        t.tap(lambda d: None)("中")
        clk[0] = 0.5
        first, _done, seg, _m = t.done(model="local")     # seg_t0 缺省 = None
        self.assertEqual(first, 0.0)
        self.assertIsNone(seg)

    def test_钟坏了_给空值不抛(self):
        def bad():
            raise OSError("钟坏了")

        t = TL.TrTimer(clock=bad)
        cb = t.tap(lambda d: None)          # 取时刻失败也不许抛
        cb("中")
        self.assertEqual(t.done(seg_t0=1.0, model="local"), (None, None, None, ""))


class LiveLine(unittest.TestCase):
    """`.live.jsonl` 的 `final` 行（`CLASSLIVE_LIVE_LOG=1` 才写）。"""

    def test_给了lat才多那四个字段(self):
        d = tempfile.mkdtemp()
        p = pathlib.Path(d) / "s.live.jsonl"
        log = LM.LiveLog(p)
        log.final("a", "啊", "a", seg=1, lat=(100.0, 200.0, 300.0, "local"))
        log.final("b", "吧", "b")                       # 不给（纯转录 / 只英·校档）
        log.close()
        f0, f1 = [r for r in _lines(p) if r["kind"] == "final"]
        self.assertEqual((f0["zh_first_ms"], f0["zh_done_ms"], f0["seg_to_zh_ms"],
                          f0["tr_model"]), (100.0, 200.0, 300.0, "local"))
        for k in ("zh_first_ms", "zh_done_ms", "seg_to_zh_ms", "tr_model"):
            self.assertNotIn(k, f1, f"没给 lat 却写了 {k}（老读端会看到不存在的数）")

    def test_失败的空lat也照写(self):
        """`(None, 123.0, None, "")` 是**真话**（这句没出中文），得能落进文件。"""
        d = tempfile.mkdtemp()
        p = pathlib.Path(d) / "s.live.jsonl"
        log = LM.LiveLog(p)
        log.final("a", "", "a", lat=(None, 123.0, None, ""))
        log.close()
        f0 = [r for r in _lines(p) if r["kind"] == "final"][0]
        self.assertIsNone(f0["zh_first_ms"])
        self.assertEqual(f0["zh_done_ms"], 123.0)
        self.assertEqual(f0["tr_model"], "")


class Report(unittest.TestCase):
    def test_pcts(self):
        self.assertIsNone(tm.pcts([]), "空表给了 0 —— 那会被读成「延迟为零毫秒」")
        self.assertEqual(tm.pcts([100, 200, 300, 400, 500]), {"p50": 300.0, "p90": 460.0})

    def test_latency_summary(self):
        rows = [{"zh_first_ms": 100.0, "zh_done_ms": 150.0, "seg_to_zh_ms": 1000.0,
                 "model": "deepseek-chat"},
                {"zh_first_ms": 200.0, "zh_done_ms": 260.0, "seg_to_zh_ms": 2000.0,
                 "model": "local"},
                {"zh_first_ms": None, "zh_done_ms": 90.0, "seg_to_zh_ms": None,
                 "model": ""}]                     # 没出中文的那句
        s = tm.latency_summary(rows)
        self.assertEqual(s["n"], 2, "没出中文的句子被算进 n 了")
        self.assertEqual(s["zh_first_ms"], {"p50": 150.0, "p90": 190.0})
        self.assertEqual(s["seg_to_zh_ms"], {"p50": 1500.0, "p90": 1900.0})
        self.assertEqual(s["by_model"], {"deepseek-chat": 1, "local": 1})

    def test_by_idle_按距上次请求多久分桶(self):
        """⭐ 3.9.8 ⑥：`by_idle` 按 `idle_before_ms` 把 `zh_first_ms` 分 `<7s` / `>=7s`。

        ⚠️ 变异验证：把 `_idle_bucket` 里 `< IDLE_SPLIT_MS` 写成 `<=` → 边界那条红；
           把「`None` 不进任何桶」那半句删掉（当成 lt7s）→ 下面第 3 条红。
        """
        rows = [{"zh_first_ms": 100.0, "model": "local", "idle_before_ms": 1000.0},
                {"zh_first_ms": 200.0, "model": "local", "idle_before_ms": 8000.0},
                {"zh_first_ms": 300.0, "model": "local", "idle_before_ms": None},   # 第一次
                {"zh_first_ms": None, "model": "", "idle_before_ms": 500.0},        # 没中文
                {"zh_first_ms": 400.0, "model": "local", "idle_before_ms": 7000.0}]  # 正好边界
        s = tm.latency_summary(rows)
        # ⚠️ 边界那条**排在最前**：把 `<` 写成 `<=` 时，红的正好是它（下面 lt7s 那条
        #    会跟着红——但那是同一个错法的第二个症状，不是两次不同的回归）。
        self.assertEqual(s["by_idle"]["ge7s"], {"p50": 300.0, "p90": 380.0},
                         "7000 ms 正好该进 ge7s（边界是 <）")
        self.assertEqual(s["by_idle"]["lt7s"], {"p50": 100.0, "p90": 100.0},
                         "`idle_before_ms=None` 那句被算进 lt7s 了")
        self.assertEqual(s["n"], 4)

    def test_by_idle_两条桶都空时给None不给0(self):
        s = tm.latency_summary([{"zh_first_ms": 100.0, "model": "local",
                                 "idle_before_ms": None}])
        self.assertIsNone(s["by_idle"]["lt7s"])
        self.assertIsNone(s["by_idle"]["ge7s"])

    def test_一句都没有时全为空(self):
        s = tm.latency_summary([])
        self.assertEqual(s, {"n": 0, "zh_first_ms": None, "seg_to_zh_ms": None,
                             "by_idle": {"lt7s": None, "ge7s": None},
                             "by_model": {}})

    def test_note_translate_进收尾报告(self):
        d = pathlib.Path(tempfile.mkdtemp())
        t = tm.TestSession(d / "x.md")
        t.note_translate(120.0, 200.0, 1500.0, "deepseek-chat", idle_before_ms=1000.0)
        t.note_translate(80.0, 90.0, 900.0, "local", idle_before_ms=9000.0)
        t.note_translate(None, 70.0, None, "")          # 没出中文那句
        rep = json.loads(pathlib.Path(t.finish(bundle=False)).read_text(encoding="utf-8"))
        lat = rep["translation_latency"]
        self.assertEqual(lat["n"], 2)
        self.assertEqual(lat["zh_first_ms"], {"p50": 100.0, "p90": 116.0})
        self.assertEqual(lat["by_model"], {"deepseek-chat": 1, "local": 1})
        # ⭐ 3.9.8 ⑥：`idle_before_ms` 随 `note_translate` 落进行里、进 by_idle。
        self.assertEqual(lat["by_idle"],
                         {"lt7s": {"p50": 120.0, "p90": 120.0},
                          "ge7s": {"p50": 80.0, "p90": 80.0}}, str(lat["by_idle"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
