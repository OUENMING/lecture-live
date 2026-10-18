#!/usr/bin/env python3
"""**功能使用度**（作者 2026-10-17 点名加的测试模式采集）的判据。

    ClassLive.app/Contents/MacOS/python tests/test_feature_usage.py

## 这个文件钉住了什么

1. ⭐⭐ **绝不记文本内容** —— 标题 / 原话 / 日期值一个字都不进报告。
   两道闸各有一条判据：`feature_usage.clean()`（字段白名单）与
   `testmode._small_data()`（落盘前的小值兜底）。
2. ⭐ **每个动作一条**：一些事儿（卡片出现 / 记下 / 忽略 / 下一条 / 改字段 / 提醒事项 /
   收尾汇总出现 / 逐条 / 全部忽略 / 20s 超时）+ 课前回顾（出现 / 手动收起 /
   自动淡出秒数 / 展开收起次数）。
3. 两个出口的**两种事件形状**（`note_event` 的 `data` 形 / `livemetrics.event` 的平铺形）
   汇总成**同一份块**；老文件（没有这些事件）→ 全零、不报错。
4. 发射点真的在**动作发生的地方**（`overlay` 那几条走真窗口，`main` 的提醒事项回执
   有一条结构判据）。

⚠️ 语料全是合成的（没有真实课堂文本），隔离目录（`tempfile` + `CLASSLIVE_UPLOAD=0`），
   不碰 `sessions/`、不联网。
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("CLASSLIVE_UPLOAD", "0")     # 保险：这个文件任何路径都不该上传

import feature_usage as FU                                          # noqa: E402

#: 一份「什么都带」的假数据 —— 里面**全是正文**（标题 / 原话 / 日期）。
#: 每条"不记文本"的判据都是拿它往里塞，然后在结果里找这些串，一个都不许出现。
TEXT_PROBE = ["本周三停课（教授说的）", "There is no class on Wednesday.",
              "2026-10-21 08:00", "最值得记的那句原话", "ECON10790 的作业"]


def _ev(name: str, **data) -> dict:
    """测试模式那边的形状（`TestSession.note_event` 落盘的样子）。"""
    ev = {"t": 1.0, "kind": FU.PREFIX + name, "detail": ""}
    if data:
        ev["data"] = data
    return ev


def _live(name: str, **fields) -> dict:
    """现场采集那边的形状（`livemetrics.event` 落盘的样子：字段平铺）。"""
    return {"i": 1, "t": 2.0, "kind": "event", "event": FU.PREFIX + name, **fields}


class 白名单(unittest.TestCase):
    """`clean()`：**发射前**那道闸。写错的东西悄悄漏进报告，这一组就是防它的。"""

    def test_认不出的动作直接丢掉(self):
        self.assertIsNone(FU.clean("duty.saved", {}), "拼错的名字不该活下来")
        self.assertIsNone(FU.clean("", {}))
        self.assertIsNone(FU.clean(None, {}))

    def test_类型只认那四类(self):
        for t in ("调课", "考试", "作业截止", "其他"):
            self.assertEqual(FU.clean("duty.card", {"type": t})[1], {"type": t})
        # ⚠️ 这条就是「把标题塞进 type」那种手滑：「本周三停课」不在词表里 → 丢
        self.assertEqual(FU.clean("duty.card", {"type": TEXT_PROBE[0]})[1], {})

    def test_来源归一到三种(self):
        for raw, want in (("regex", "regex"), ("model", "model"),
                          ("regex/model", "both"), ("model/regex", "both")):
            self.assertEqual(FU.clean("duty.card", {"src": raw})[1], {"src": want}, raw)
        # 认不出的来源（比如整句话）= 丢掉，不是原样存
        self.assertEqual(FU.clean("duty.card", {"src": TEXT_PROBE[1]})[1], {})

    def test_提醒事项原因只有封闭词表(self):
        for why in FU.WHY:
            self.assertEqual(FU.clean("duty.reminder", {"ok": False, "why": why})[1],
                             {"ok": False, "why": why})
        self.assertEqual(FU.clean("duty.reminder", {"why": "系统弹了个框"})[1], {})

    def test_回顾块名只认那五个(self):
        got = FU.clean("recap.shown", {"blocks": ["points", "星星", "open"]})[1]
        self.assertEqual(got, {"blocks": ["points", "open"]})
        self.assertEqual(FU.clean("recap.shown", {"blocks": ["没有这块"]})[1], {})

    def test_数字与布尔规整(self):
        got = FU.clean("duty.wrapup", {"n": "3", "pending": 2.7})[1]
        self.assertEqual(got, {"n": 3, "pending": 2})
        self.assertEqual(FU.clean("recap.auto_hide", {"s": "12.34"})[1], {"s": 12.3})
        self.assertEqual(FU.clean("duty.wrapup.timeout", {"left": None})[1], {})

    def test_白名单外的字段一律丢掉(self):
        got = FU.clean("duty.save", {"title": TEXT_PROBE[0], "n": 1})
        self.assertEqual(got, ("duty.save", {}), "duty.save 本来就不带字段")


class 绝不记文本(unittest.TestCase):
    """⭐⭐ 作者的硬要求：**标题 / 原话 / 日期值一个字都不进**。"""

    def test_清洗后找不到任何正文(self):
        got = FU.clean("duty.card", {"type": "调课", "src": "regex",
                                     "title": TEXT_PROBE[0], "quote": TEXT_PROBE[1],
                                     "date": TEXT_PROBE[2], "zh": TEXT_PROBE[3]})[1]
        blob = json.dumps(got, ensure_ascii=False)
        for t in TEXT_PROBE:
            self.assertNotIn(t, blob, f"正文漏进清洗结果：{t}")
        self.assertEqual(got, {"type": "调课", "src": "regex"})

    def test_整份块里找不到任何正文(self):
        """所有动作都塞一遍正文，汇总出来的块里一个都不许有。"""
        events = [
            _ev("duty.card", type="调课", src="regex", title=TEXT_PROBE[0]),
            _ev("duty.save", title=TEXT_PROBE[0], date=TEXT_PROBE[2], quote=TEXT_PROBE[1]),
            _ev("duty.edit", title=True, date=True, was=TEXT_PROBE[2]),
            _ev("duty.reminder", ok=False, why="denied", reason=TEXT_PROBE[3]),
            _ev("duty.wrapup", n=2, pending=1, items=[TEXT_PROBE[0]]),
            _ev("recap.shown", blocks=["points"], today=[TEXT_PROBE[4]], text=TEXT_PROBE[4]),
            _live("duty.wrapup.all", n=1, titles=[TEXT_PROBE[0]]),
        ]
        blob = json.dumps(FU.block(events), ensure_ascii=False)
        for t in TEXT_PROBE:
            self.assertNotIn(t, blob, f"正文漏进报告块：{t}")
        self.assertNotIn(TEXT_PROBE[0], "\n".join(FU.lines(FU.block(events))))

    def test_落盘前那道闸只管形状(self):
        """`testmode._small_data`：**长度与形状**最后兜一道。

        ⚠️ 它**不是**内容白名单 —— 内容白名单是 `feature_usage.clean()`（上面那组）。
           它管的是「别让一整段正文进 JSON」：长文本截断、嵌套结构丢掉、列表只收短串。
        """
        import testmode
        got = testmode._small_data({"ok": True, "n": 2, "s": 1.5,
                                    "quote": "x" * 500,
                                    "nested": {"a": 1}, "objs": [{"a": 1}]})
        self.assertEqual(got["ok"], True)                                # noqa: E712
        self.assertEqual(got["n"], 2)
        self.assertEqual(len(got["quote"]), 24, "长文本必须被截到 24 字以内")
        self.assertNotIn("nested", got, "嵌套结构不进报告")
        self.assertNotIn("objs", got, "对象列表不进报告")
        self.assertEqual(testmode._small_data("不是字典"), {}, "不是字典 → 空")
        # 短串 / 短串列表照收（`"调课"` / `"regex"` 这些要的就是它）
        self.assertEqual(testmode._small_data({"type": "调课", "blocks": ["points", "open"]}),
                         {"type": "调课", "blocks": ["points", "open"]})


class 计数(unittest.TestCase):
    """`block()`：**每个动作一条**（作者要的就是这张表）。"""

    def test_卡片出现_类型与来源(self):
        b = FU.block([_ev("duty.card", type="调课", src="regex"),
                      _live("duty.card", type="考试", src="model"),
                      _ev("duty.card", type="调课", src="regex/model")])
        self.assertEqual(b["duty"]["card"], 3)
        self.assertEqual(b["duty"]["type"], {"调课": 2, "考试": 1})
        self.assertEqual(b["duty"]["src"], {"regex": 1, "model": 1, "both": 1})

    def test_记下_忽略_下一条(self):
        b = FU.block([_ev("duty.save"), _ev("duty.save"), _ev("duty.ignore"),
                      _ev("duty.next")])
        self.assertEqual((b["duty"]["save"], b["duty"]["ignore"], b["duty"]["next"]),
                         (2, 1, 1))

    def test_改过字段_标题与日期分开(self):
        b = FU.block([_ev("duty.edit", title=True, date=False),
                      _ev("duty.edit", title=False, date=True),
                      _ev("duty.edit", title=True, date=True)])
        self.assertEqual(b["duty"]["edit"], 3)
        self.assertEqual(b["duty"]["edit_title"], 2)
        self.assertEqual(b["duty"]["edit_date"], 2)

    def test_提醒事项_成功与失败原因(self):
        b = FU.block([_ev("duty.reminder", ok=True),
                      _ev("duty.reminder", ok=False, why="denied"),
                      _ev("duty.reminder", ok=False, why="timeout"),
                      _ev("duty.reminder", ok=False, why="timeout")])
        self.assertEqual(b["duty"]["reminder"], 4)
        self.assertEqual((b["duty"]["reminder_ok"], b["duty"]["reminder_fail"]), (1, 3))
        self.assertEqual(b["duty"]["reminder_why"], {"denied": 1, "timeout": 2})

    def test_提醒事项原因归类(self):
        """系统那句话 → 封闭词表里的类（**原文不进报告**）。"""
        for raw, want in (
                ("提醒事项没在 20 秒内写完（授权弹窗没点？）", "timeout"),
                ("系统没允许 ClassLive 控制提醒事项（系统设置 › 隐私与安全性 › 自动化）", "denied"),
                ("osascript 退出码 1", "error"),
                ("RuntimeError: boom", "error"),
                ("找不到 osascript", "error"),
                ("天知道", "other"), ("", "other")):
            self.assertEqual(FU.reason_kind(raw), want, raw)

    def test_收尾汇总_出现与逐条(self):
        b = FU.block([_ev("duty.wrapup", n=3, pending=2),
                      _ev("duty.wrapup.save"), _ev("duty.wrapup.ignore"), _ev("duty.wrapup.ignore")])
        self.assertEqual(b["duty"]["wrapup"], 1)
        self.assertEqual((b["duty"]["wrapup_save"], b["duty"]["wrapup_ignore"]), (1, 2))

    def test_全部忽略与超时(self):
        b = FU.block([_ev("duty.wrapup.all", n=2), _ev("duty.wrapup.timeout", left=3)])
        self.assertEqual(b["duty"]["wrapup_all"], 1)
        self.assertEqual(b["duty"]["wrapup_timeout"], 1)
        self.assertEqual(b["duty"]["wrapup_left"], [3])

    def test_课前回顾_出现与块名(self):
        b = FU.block([_ev("recap.shown", blocks=["points", "stars"]),
                      _live("recap.shown", blocks=["points", "open"])])
        self.assertEqual(b["recap"]["shown"], 2)
        self.assertEqual(b["recap"]["blocks"], {"points": 2, "stars": 1, "open": 1})

    def test_课前回顾_收起_自动淡出_按钮(self):
        b = FU.block([_ev("recap.dismiss"),
                      _ev("recap.auto_hide", s=44.2), _ev("recap.auto_hide", s=3.0),
                      _ev("recap.toggle", on=True), _ev("recap.toggle", on=False),
                      _ev("recap.toggle", on=True)])
        self.assertEqual(b["recap"]["dismiss"], 1)
        self.assertEqual(b["recap"]["auto_hide"], 2)
        self.assertEqual(b["recap"]["auto_hide_s"], [44.2, 3.0])
        self.assertEqual((b["recap"]["toggle"], b["recap"]["toggle_open"]), (3, 2))

    def test_两种形状同一份块(self):
        a = FU.block([_ev("duty.card", type="调课", src="regex"), _ev("duty.save")])
        c = FU.block([_live("duty.card", type="调课", src="regex"), _live("duty.save")])
        self.assertEqual(a, c)

    def test_老文件_全零不报错(self):
        """老 report.json / 老 jsonl 里根本没有功能事件 → 全零的块，不是异常。"""
        for events in ([], [{"t": 1, "kind": "seg"}], [{"kind": "draft", "text": "x"}],
                       [None, 42, "x"]):
            b = FU.block(events)
            self.assertEqual(b["n_events"], 0)
            self.assertEqual(b["duty"]["card"], 0)
            self.assertEqual(b["recap"]["shown"], 0)
            self.assertEqual(len(FU.lines(b)), 5)          # 行数固定（老文件也照打）

    def test_拼错的动作进unknown(self):
        b = FU.block([_ev("duty.card", type="调课"), _ev("duty.saved")])
        self.assertEqual((b["n_events"], b["unknown"]), (1, 1))
        self.assertIn("认不出的动作 1", "\n".join(FU.lines(b)))

    def test_多份先加计数(self):
        """`class_report.py` 多节课那份：先加计数，均值才在渲染时算。"""
        one = FU.block([_ev("duty.card", type="调课", src="regex"),
                        _ev("recap.auto_hide", s=10.0)])
        two = FU.merge([one, one])
        self.assertEqual(two["duty"]["card"], 2)
        self.assertEqual(two["duty"]["type"], {"调课": 2})
        self.assertEqual(two["recap"]["auto_hide_s"], [10.0, 10.0])
        self.assertEqual(FU.merge([]), FU.block([]), "空归并 = 空的块")
        self.assertEqual(FU.merge([None, {}, two])["duty"]["card"], 2, "坏输入不该炸")


class 报告集成(unittest.TestCase):
    """`TestSession` → `report.json`：块真的落进了那份文件（隔离目录 + 隔离 sessions/）。"""

    @staticmethod
    def _tester(tmp: str):
        """临时 `sessions/` 下建一个采集器（**不碰**真会话目录、不录音 → 不写音频）。"""
        import testmode
        stem = pathlib.Path(tmp) / "sessions" / "x.md"
        stem.parent.mkdir(parents=True, exist_ok=True)
        return testmode.TestSession(stem)

    def test_feature_usage_进report_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = self._tester(tmp)
            t.note_event(FU.PREFIX + "duty.card", data={"type": "调课", "src": "regex"})
            t.note_event(FU.PREFIX + "duty.save")
            t.note_event(FU.PREFIX + "recap.auto_hide", data={"s": 8.0})
            rep = json.loads(pathlib.Path(t.finish(bundle=False)).read_text(encoding="utf-8"))
            fu = rep["feature_usage"]
            self.assertEqual(fu["duty"]["card"], 1)
            self.assertEqual(fu["duty"]["type"], {"调课": 1})
            self.assertEqual(fu["duty"]["save"], 1)
            self.assertEqual(fu["recap"]["auto_hide_s"], [8.0])
            # ⚠️ 事件本身也在（时间线那份是原始记录，别只留汇总）
            self.assertIn(FU.PREFIX + "duty.save", [e["kind"] for e in rep["events"]])

    def test_没有功能事件时全零(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = self._tester(tmp)
            rep = json.loads(pathlib.Path(t.finish(bundle=False)).read_text(encoding="utf-8"))
            self.assertEqual(rep["feature_usage"], FU.block([]))
            self.assertEqual(rep["feature_usage"]["n_events"], 0)

    def test_功能事件里的正文进不了报告(self):
        """⭐ 作者那条硬要求在**落盘那份文件**上再验一次（标题 / 原话 / 日期值）。"""
        with tempfile.TemporaryDirectory() as tmp:
            t = self._tester(tmp)
            t.note_event(FU.PREFIX + "duty.card",
                         data={"type": "调课", "title": TEXT_PROBE[0],
                               "quote": TEXT_PROBE[1][:200], "date": TEXT_PROBE[2]})
            t.note_event(FU.PREFIX + "duty.save", data={"title": TEXT_PROBE[0]})
            blob = pathlib.Path(t.finish(bundle=False)).read_text(encoding="utf-8")
            for probe in TEXT_PROBE[:3]:
                self.assertNotIn(probe, blob, f"正文写进了 report.json：{probe}")


class class_report显示(unittest.TestCase):
    """`scripts/class_report.py`：几行字 + **老文件不报错**。"""

    def _report(self, lines_list):
        import importlib
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
        cr = importlib.import_module("class_report")
        with tempfile.TemporaryDirectory() as tmp:
            p = pathlib.Path(tmp) / "x.live.jsonl"
            p.write_text("\n".join(json.dumps(x, ensure_ascii=False)
                                   for x in lines_list) + "\n", encoding="utf-8")
            return cr.render(cr.build_report(p))

    def test_有功能事件就打出那几行(self):
        txt = self._report([
            _live("duty.card", type="调课", src="regex"),
            _live("duty.save"), _live("duty.reminder", ok=False, why="denied"),
            _live("duty.wrapup", n=2, pending=1), _live("duty.wrapup.all", n=1),
            _live("recap.shown", blocks=["points"]), _live("recap.auto_hide", s=30.0),
        ])
        self.assertIn("功能使用（测试模式）", txt)
        self.assertIn("一些事儿：卡片 1 次", txt)
        self.assertIn("调课 1", txt)
        self.assertIn("提醒事项 成功 0 / 失败 1（denied 1）", txt)
        self.assertIn("全部忽略 1", txt)
        self.assertIn("课前回顾：出现 1 次（块 points 1）", txt)
        self.assertIn("自动淡出 1（均 30.0s）", txt)

    def test_老文件不报错(self):
        txt = self._report([{"i": 1, "t": 0.0, "kind": "draft", "text": "x", "raw": "x",
                             "utt": 1, "buf_s": 0.4, "compute_s": 0.2}])
        self.assertIn("功能使用（测试模式）", txt)
        self.assertIn("没有功能事件", txt)

    def test_多份先加计数(self):
        import importlib
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
        cr = importlib.import_module("class_report")
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for i in range(2):
                p = pathlib.Path(tmp) / f"x{i}.live.jsonl"
                p.write_text(json.dumps(_live("duty.card", type="调课", src="regex"),
                                        ensure_ascii=False) + "\n" + json.dumps(
                    _live("duty.save"), ensure_ascii=False) + "\n", encoding="utf-8")
                paths.append(p)
            res = cr.pool_sweeps([cr.build_report(p) for p in paths])
        self.assertEqual(res["feature_usage"]["duty"]["card"], 2, "两份要加，不是覆盖")
        self.assertIn("卡片 2 次", cr.render(res))


class 真窗口(unittest.TestCase):
    """⭐ **发射点**：动作发生的地方真的报了 —— 走真 `Overlay`（不显示窗口）。"""

    def _overlay(self, seen: list):
        try:
            from overlay import Overlay
        except ModuleNotFoundError as e:                      # pragma: no cover
            self.skipTest(f"AppKit 不可用, 跳过: {e}")
        o = Overlay(on_feature=lambda n, d: seen.append((n, d)),
                    on_duty=lambda a, it: None)
        self.addCleanup(o.close)
        return o

    @staticmethod
    def _item(rid="d1", **kw):
        return {"id": rid, "action": "pending", "type": "调课",
                "title": TEXT_PROBE[0], "quote": TEXT_PROBE[1],
                "date": "2026-10-21", "time": "08:00", "source": "regex",
                "src": [1], "t": "10:12", **kw}

    def test_卡片出现与记下与改字段(self):
        seen: list = []
        o = self._overlay(seen)
        o.duty_update([self._item("d1")])
        o._duty_open("d1")
        self.assertEqual(seen[-1], ("duty.card", {"type": "调课", "src": "regex"}),
                         "卡片出现那条没带类型/来源")
        # 只改标题（不碰日期）→ 记 `title=True, date=False`
        for key, fld, _box in o._card_fields:
            if key == "title":
                fld.setStringValue_(TEXT_PROBE[3])
        o._duty_act("save", o._duty_get("d1"))
        self.assertIn(("duty.edit", {"title": True, "date": False}), seen)
        self.assertEqual(seen[-1], ("duty.save", {}))
        # 一个字都没改 → **没有** edit 那一条（把框里的值改回原样）
        for key, fld, _box in o._card_fields:
            if key == "title":
                fld.setStringValue_(TEXT_PROBE[0])
        n = len(seen)
        o._duty_act("ignore", o._duty_get("d1"))
        self.assertEqual([x for x in seen[n:] if x[0] == "duty.edit"], [],
                         "没改也报了 edit")
        self.assertEqual(seen[-1], ("duty.ignore", {}))

    def test_下一条(self):
        seen: list = []
        o = self._overlay(seen)
        o.duty_update([self._item("d1"), self._item("d2")])
        o._duty_open("d1")
        o._duty_act("next", o._duty_get("d1"))
        self.assertIn(("duty.next", {}), seen)

    def test_收尾三件(self):
        seen: list = []
        o = self._overlay(seen)
        items = [self._item("d1"), self._item("d2"), self._item("d3")]
        self.assertTrue(o.duty_wrapup_show(items))
        self.assertEqual(seen[-1], ("duty.wrapup", {"n": 3, "pending": 3}))
        # ⚠️ 真跑里 `main` 会把更新过的板子推回来（`duty_wrapup_sync`）——
        #    这里照做，不然那几条在卡上永远是"待确认"（`on_duty` 是桩）。
        o._wrapup_act("save", "d1")
        self.assertIn(("duty.wrapup.save", {}), seen)
        o.duty_wrapup_sync([dict(items[0], action="saved"), items[1], items[2]])
        o._wrapup_act("ignore", "d2")
        self.assertIn(("duty.wrapup.ignore", {}), seen)
        o.duty_wrapup_sync([dict(items[0], action="saved"),
                            dict(items[1], action="ignored"), items[2]])
        o._wrapup_act("all")
        self.assertIn(("duty.wrapup.all", {"n": 1}), seen)      # 只剩 d3 没处理
        o.duty_wrapup_finish()

    def test_收尾超时(self):
        seen: list = []
        o = self._overlay(seen)
        items = [self._item("d1"), self._item("d2")]
        self.assertEqual(o.duty_wrapup(items, timeout=0.0), 0)
        self.assertIn(("duty.wrapup.timeout", {"left": 2}), seen)
        # ⚠️ 0 条待处理 → 卡都不出现 → 超时那条也不该有（"没出现"是真话）
        seen.clear()
        self.assertEqual(o.duty_wrapup([], timeout=0.0), 0)
        self.assertEqual(seen, [])

    def test_课前回顾四件(self):
        seen: list = []
        o = self._overlay(seen)
        data = {"course": "ECON10790", "date": "10-15",
                "points": [{"name": TEXT_PROBE[4], "explain": "x"}],
                "stars": [TEXT_PROBE[3]], "today": ["今天新课件"]}
        o.recap_show(data)
        name, d = seen[-1]
        self.assertEqual(name, "recap.shown")
        self.assertEqual(d, {"blocks": ["points", "stars", "today"]})
        # 手动收起（卡上那颗「收起」）→ dismiss；按钮收起 **不算**手动
        o._recap_dismiss()
        self.assertIn(("recap.dismiss", {}), seen)
        o._recap_toggle()                                        # 展开（顶栏按钮）
        self.assertIn(("recap.toggle", {"on": True}), seen)
        self.assertEqual(seen[-1][0], "recap.shown", "展开也要记一次「出现」")
        o._recap_toggle()                                        # 收起
        self.assertIn(("recap.toggle", {"on": False}), seen)
        self.assertEqual([x for x in seen if x[0] == "recap.dismiss"], 
                         [("recap.dismiss", {})], "按钮收起不该再记一条「手动收起」")

    def test_课前回顾自动淡出带秒数(self):
        import motion
        seen: list = []
        o = self._overlay(seen)
        o.recap_show({"course": "ECON10790", "points": [{"name": "x", "explain": "y"}]})
        old = motion.reduce_motion
        motion.reduce_motion = lambda: True                      # 走「直接换」那条（不跑动画）
        try:
            o._recap_maybe_hide()
        finally:
            motion.reduce_motion = old
        got = [d for n, d in seen if n == "recap.auto_hide"]
        self.assertEqual(len(got), 1)
        self.assertGreaterEqual(got[0]["s"], 0.0, "秒数没带上")
        self.assertEqual([x for x in seen if x[0] == "recap.dismiss"], [],
                         "自动淡出**不是**手动收起")

    def test_回顾失败时不报出现(self):
        """渲染炸了（`recap.card` 抛）→ 卡收掉 → **没有** `recap.shown` 那条。"""
        seen: list = []
        o = self._overlay(seen)
        import recap
        old = recap.card
        recap.card = lambda _d: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            o.recap_show({"course": "x", "points": [{"name": "a", "explain": "b"}]})
        finally:
            recap.card = old
        self.assertEqual(seen, [], "渲染失败还报了「出现」")


class main那条线(unittest.TestCase):
    """`main._feature` 与 `duty_result` 回执 —— 结构判据（不真起 main）。"""

    ROOT = pathlib.Path(__file__).resolve().parent.parent

    def test_呼叫点与包装层的形参对齐(self):
        """R17 那条既有判据管的是这个 —— 这里只钉 `on_feature` 真的在链上。"""
        import ast
        src = (self.ROOT / "main.py").read_text(encoding="utf-8")
        kws = set()
        for n in ast.walk(ast.parse(src)):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                    and n.func.id == "_load_overlay"):
                kws |= {k.arg for k in n.keywords if k.arg}
        self.assertIn("on_feature", kws)
        self.assertIn("on_feature", src.split("def _load_overlay(")[1].split("):")[0])

    def test_提醒事项回执报了原因归类(self):
        src = (self.ROOT / "main.py").read_text(encoding="utf-8")
        i = src.index('elif item[0] == "duty_result"')
        seg = src[i:i + 700]
        self.assertIn('_feature("duty.reminder"', seg, "回执那条没报功能事件")
        self.assertIn("reason_kind", seg, "失败原因没归类（原文不许进报告）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
