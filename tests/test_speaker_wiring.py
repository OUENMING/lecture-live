#!/usr/bin/env python3
"""说话人接线（S3）：`_emit` 的 `("speaker", 字母)` / `("final", …, (原始编号, 字母))`，
以及 `drain()` 那一侧（`.live.jsonl` 两个键、`ui.finalize(speaker=)`、换人不合并）。

    ClassLive.app/Contents/MacOS/python tests/test_speaker_wiring.py

## 钉住什么

1. `_emit` 三个产出点**都是 8 项**，下标 7 = `(Soniox 原始编号, 字母)`；纯转录 / 只英·校
   两条路的下标 5、6 是 `None`（老规矩：没送翻就不报引擎名 / 译名条数）。
   `split_sentences` 切出的**每一小句**前面都先入队一条 `("speaker", 字母)`。
2. 不给 `speaker` 的调用点（carry 兜底 / 本地定稿路）= `("", "")` —— 老行为逐字不变。
3. `drain()`：`live.final` 拿到原始编号 + 字母（空串照旧不写键，见 `test_livemetrics`）；
   `ui.finalize` 拿到字母；**字母与上一行不同就不调 `merge_tracker.on_line`**；
   会话 Markdown / 实时总结的入参**一个字段都没多**。
4. `("speaker", 字母)` → `ui.set_live_speaker`（没有这一槽的 UI 静默忽略）。
5. `labeler.observe` 抛异常时这一句**照常上屏**（坏了只当字母为空）。

## 怎么驱动

`_emit` / `drain` / `final_worker` 都嵌在 `main.run()` 里（跑真 `run()` 要麦克风、模型、
网络）。这里把它们的**源码**从 `main.py` 取出来，在手工搭的命名空间里 `exec` 一次 ——
函数体是真代码，替身只替换协作方（`ui` / `live` / `writer` / `summ` / 队列 / `final.transcribe`
直接回封口句原文）。⚠️ `final_worker` 源码里用到但本任务不关心的闭包（`_force_emit_carry`）
也一并 `exec`，否则线程会在那条 `NameError` 上死掉、替身就测不到了。
"""
from __future__ import annotations

import ast
import os
import pathlib
import queue
import shutil
import sys
import tempfile
import textwrap
import threading
import types
import unittest

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

# ⚠️ 与 `test_final_wiring.py` 同一套隔离：HOME 与 `paths.STATE_ROOT` 都指向临时目录
#    （`final_asr` / `paths` 在 import 期就算出路径），`tearDownModule` 里逐个还回去。
import paths                                                          # noqa: E402

_STATE_BEFORE = paths.STATE_ROOT
_TMP_HOME = tempfile.mkdtemp(prefix="speaker-wiring-test-")
_HOME_BEFORE = os.environ.get("HOME")
os.environ["HOME"] = _TMP_HOME
os.environ["CLASSLIVE_LAYOUT"] = "classic"

import main as main_mod                                               # noqa: E402
import stream_asr                                                     # noqa: E402
import speaker_label                                                  # noqa: E402

paths.STATE_ROOT = pathlib.Path(_TMP_HOME) / ".classlive"
SRC = (HERE / "main.py").read_text(encoding="utf-8")
TREE = ast.parse(SRC)


def _fn_src(name: str) -> str:
    """`main.py` 里某个（含嵌套闭包的）函数源码，去掉公共缩进 —— 直接能 `exec`。"""
    for node in ast.walk(TREE):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return textwrap.dedent(ast.get_source_segment(SRC, node) or "")
    raise AssertionError(f"main.py 里找不到 {name}")


def _ns(**extra) -> dict:
    """命名空间 = `main` 的模块全局 + 这个测试给的替身（替身优先）。"""
    ns = dict(vars(main_mod))
    ns.update(extra)
    return ns


def _pair_stub(item) -> tuple:
    """`drain()` 里那个 `_pair`（草稿元组扁平化）—— 本文件不往草稿队列里放东西，
    照它的真形状回一份空草稿就行（原函数也接受空元组）。"""
    try:
        return (item[0], item[1], float(item[2]), float(item[3]), int(item[4]),
                float(item[5]), str(item[6]), int(item[7]) if len(item) > 7 else 0)
    except Exception:                                  # noqa: BLE001
        return ("", "", 0.0, 0.0, 0, 0.0, "", 0)


class Recorder:
    """`ui` 的最小替身：只记被调了什么。"""

    def __init__(self, *, has_live_speaker: bool = True, has_merge: bool = True):
        self.calls: list[tuple] = []
        self._tok = 100
        self._has_spk = has_live_speaker
        if not has_merge:
            del self.replace_merge
        if has_live_speaker:
            self.set_live_speaker = self._set_live_speaker

    def _set_live_speaker(self, label):
        self.calls.append(("set_live_speaker", label))

    def finalize(self, en, zh, *, clear_draft=True, speaker=""):
        self.calls.append(("finalize", en, zh, clear_draft, speaker))
        self._tok += 1
        return self._tok

    def replace_merge(self, *a):
        return False

    # 其余 drain 会碰的槽
    def draft_over_stream(self, flag):
        self.calls.append(("draft_over_stream", flag))

    def stream_zh(self, d):
        pass

    def stream_en(self, d):
        pass

    def add_draft(self, t):
        pass

    def draft_zh(self, t):
        pass

    def terms(self, h):
        pass

    def notice(self, *a):
        pass

    def show_hint(self, *a):
        pass

    def pump(self):
        pass


class Live:
    def __init__(self):
        self.finals: list[dict] = []

    def final(self, en, zh="", raw="", **kw):
        self.finals.append({"en": en, "zh": zh, "raw": raw, **kw})

    def event(self, *a, **kw):
        pass

    def draft(self, *a, **kw):
        pass

    def draft_zh(self, *a, **kw):
        pass


class Writer:
    """`obsidian_writer` 的替身：只记 `append` 的参数（**个数也算判据**）。"""

    def __init__(self):
        self.rows: list[tuple] = []
        self._n = 0
        self.session_path = None

    def append(self, en, zh, raw=""):
        self._n += 1
        self.rows.append((en, zh, raw))
        return f"ts{self._n}"

    def append_atoms(self, *a, **kw):
        pass

    def mark_merge(self, *a, **kw):
        pass


class Summary:
    def __init__(self):
        self.fed: list[tuple] = []

    def feed(self, item):
        self.fed.append(item)


class Tracker:
    def __init__(self, job=None):
        self.lines: list[tuple] = []
        self._job = job

    def on_line(self, *a):
        self.lines.append(a)
        return self._job


class View:
    def on_stream(self, seg):
        pass

    def on_draft(self, *a):
        return False

    def on_final(self, seg):
        return False

    def stale_en(self, seg):
        return False

    def generation(self):
        return 0

    def note_submitted(self, *a):
        pass

    def over_stream(self):
        return False


def _drain_ns(*, ui=None, live=None, writer=None, summ=None, tracker=None,
               items=(), mode="both"):
    """搭好 `drain()` 的协作方，返回 `(drain, 记下来的替身们)`。"""
    streamq: queue.Queue = queue.Queue()
    for it in items:
        streamq.put(it)
    ns = _ns(
        streamq=streamq,
        ui=ui if ui is not None else Recorder(),
        live=live if live is not None else Live(),
        writer=writer if writer is not None else Writer(),
        summ=summ if summ is not None else Summary(),
        merge_tracker=tracker,
        drafts=queue.Queue(),
        drafts_zh=queue.Queue(),
        mergeq=queue.Queue(),
        finals=["ctx"],
        trans={"mode": mode},
        tester=None,
        draft_view=View(),
        draft_tr=types.SimpleNamespace(take_clipped=lambda d: False,
                                       run=lambda running: None),
        draft_stats={},
        ctl=types.SimpleNamespace(emit=lambda *a, **kw: None, poll=lambda: []),
        last_draft={"en": ""},
        seg_end_punct={},
        merge_stats={"triggered": 0, "replaced": 0},
        _merge_inc=lambda: None,
        _row_lbl={"lbl": ""},
        _tr_idle=__import__("collections").deque(),
        _tr_last={"end": None},
        _pair=_pair_stub,
        labeler=speaker_label.SpeakerLabeler(),
        _spk_last={"lbl": ""},
        _sent_end={"s": None},
        busy={"on": False},
        carry={"text": "", "since": 0.0, "seg": 0},
        CARRY_MAX_S=3.0,
        _engine_warns={},
        _on_translate_mode=lambda m: None,
        STREAM_MAX_WORDS=40,
        stream=None,
        final=None,
        cloud_final={"used": False},
        running=threading.Event(),
        final_gen={"n": 0},
        translator=types.SimpleNamespace(
            uses_cloud=False,
            fix_stream=lambda s, ctx, on_en=None: "",
            fix_and_translate_stream=lambda s, ctx, on_zh=None, on_en=None:
                types.SimpleNamespace(en_fixed="fixed.", zh="中文")),
        tr_latency=main_mod.tr_latency,
        args=types.SimpleNamespace(cloud_model="m"),
        term_zh={},
        notes=types.SimpleNamespace(match=lambda s: [], known=lambda: []),
        api_key_val="",
        seg_ended_at={},
        TR_STATS=main_mod.TR_STATS,
        live_pool=None,
        live_terms_step=lambda *a: [],
        _WakeQueue=queue.Queue,
    )
    ns["_wake_fw"] = threading.Event()
    ns["_QUIT"] = object()
    ns["finalq"] = queue.Queue()
    ns["sealedq"] = queue.Queue()
    ns["gate"] = types.SimpleNamespace(on=True, seg_of=lambda end_s: 0, can_feed=lambda: True)
    ns["gate_zh"] = types.SimpleNamespace(observe=lambda lang, ever_off=False: False)
    ns["stream"] = None
    exec(compile(_fn_src("_ctl_ev"), "<_ctl_ev>", "exec"), ns)            # noqa: S102
    exec(compile(_fn_src("drain"), "<drain>", "exec"), ns)            # noqa: S102
    exec(compile(_fn_src("_force_emit_carry"), "<_force>", "exec"), ns)   # noqa: S102
    exec(compile(_fn_src("_emit"), "<_emit>", "exec"), ns)            # noqa: S102
    return ns


# --------------------------------------------------------------------------
# 1) `_emit` 的产出形状
# --------------------------------------------------------------------------
class TestEmitShape(unittest.TestCase):
    def _emit_items(self, mode="both", text="one two three.", speaker=("2", "B")):
        ns = _drain_ns(mode=mode)
        ns["running"].set()
        ns["tr_latency"] = types.SimpleNamespace(
            TrTimer=lambda: types.SimpleNamespace(
                tap=lambda fn: fn,
                done=lambda seg_t0=None, model="": (1, 2, 3, model)))
        ns["translator"] = types.SimpleNamespace(
            uses_cloud=False,
            fix_stream=lambda s, ctx, on_en=None: "",
            fix_and_translate_stream=lambda s, ctx, on_zh=None, on_en=None:
                types.SimpleNamespace(en_fixed="fixed.", zh="中文"))
        ns["_emit"](text, 0, speaker=speaker)
        out = []
        while True:
            try:
                out.append(ns["streamq"].get_nowait())
            except queue.Empty:
                break
        return out

    def test_raw_mode_shape(self):
        items = self._emit_items(mode="raw")
        self.assertEqual(items[0], ("speaker", "B"), "先告诉界面这一句是谁说的")
        fin = [i for i in items if i[0] == "final"]
        self.assertEqual(len(fin), 1)
        self.assertEqual(len(fin[0]), 8)
        self.assertIsNone(fin[0][5], "纯转录没送翻 → 没有译文延迟")
        self.assertIsNone(fin[0][6], "纯转录没送翻 → 没有译名条数")
        self.assertEqual(fin[0][7], ("2", "B"))

    def test_en_mode_shape(self):
        items = self._emit_items(mode="en")
        fin = [i for i in items if i[0] == "final"]
        self.assertEqual(len(fin), 1)
        self.assertEqual(len(fin[0]), 8)
        self.assertIsNone(fin[0][5])
        self.assertIsNone(fin[0][6])
        self.assertEqual(fin[0][7], ("2", "B"))

    def test_both_mode_shape(self):
        items = self._emit_items(mode="both")
        fin = [i for i in items if i[0] == "final"]
        self.assertEqual(len(fin), 1)
        self.assertEqual(len(fin[0]), 8)
        self.assertIsNotNone(fin[0][5], "双语档给译文延迟")
        self.assertIsInstance(fin[0][6], int, "双语档给译名条数")
        self.assertEqual(fin[0][7], ("2", "B"))

    def test_speaker_item_precedes_each_sentence(self):
        # 两句（各 ≥ 一个句号）→ 两条 ("speaker", ×)，且每条都排在自己那句 final 前面
        items = self._emit_items(mode="raw", text="First one here. Second one here.")
        kinds = [i[0] for i in items]
        self.assertEqual(kinds.count("speaker"), kinds.count("final"))
        self.assertEqual(kinds[0], "speaker")
        idx = [n for n, k in enumerate(kinds) if k == "speaker"]
        fin = [n for n, k in enumerate(kinds) if k == "final"]
        self.assertEqual(len(idx), 2)
        for a, b in zip(idx, fin):
            self.assertLess(a, b, "说话人必须先于这一句的 final 入队")
        self.assertTrue(all(items[i][1] == "B" for i in idx))

    def test_default_is_unlabelled(self):
        # carry 兜底 / 本地定稿路不传 speaker → ("", "")，与老行为逐字一致
        items = self._emit_items(mode="raw", speaker=("", ""))
        fin = [i for i in items if i[0] == "final"][0]
        self.assertEqual(fin[7], ("", ""))


# --------------------------------------------------------------------------
# 2) `drain()` 那一侧
# --------------------------------------------------------------------------
def _final_item(*, en="hello there", zh="你好", raw="hello there", seg=0,
                lat=None, tzn=None, speaker=None):
    if speaker is None:
        return ("final", en, zh, raw, seg, lat, tzn)
    return ("final", en, zh, raw, seg, lat, tzn, speaker)


class TestDrainFinal(unittest.TestCase):
    def test_speaker_reaches_live_and_ui(self):
        live, ui = Live(), Recorder()
        ns = _drain_ns(live=live, ui=ui,
                       items=[_final_item(speaker=("2", "B"))])
        ns["drain"]()
        self.assertEqual(live.finals[0]["speaker"], "2")
        self.assertEqual(live.finals[0]["speaker_label"], "B")
        fin = [c for c in ui.calls if c[0] == "finalize"][0]
        self.assertEqual(fin[4], "B")

    def test_old_shape_still_reads(self):
        # 老形状（5 项）→ speaker 当空串；`live.final` 收到的仍是关键字
        live, ui = Live(), Recorder()
        ns = _drain_ns(live=live, ui=ui, items=[("final", "hi", "嗨", "hi", 0)])
        ns["drain"]()
        self.assertEqual(live.finals[0]["speaker"], "")
        self.assertEqual(live.finals[0]["speaker_label"], "")
        self.assertEqual([c for c in ui.calls if c[0] == "finalize"][0][4], "")

    def test_writer_and_summary_get_no_new_field(self):
        # `.md` 是三方契约：`append` 仍然只有三个位置参数；`summ.feed` 仍是 4 元组
        writer, summ = Writer(), Summary()
        ns = _drain_ns(writer=writer, summ=summ,
                       items=[_final_item(speaker=("3", "C"))])
        ns["drain"]()
        self.assertEqual(writer.rows, [("hello there", "你好", "hello there")])
        self.assertEqual(len(summ.fed[0]), 4)

    def test_same_speaker_still_merges(self):
        tracker = Tracker()
        ns = _drain_ns(tracker=tracker, items=[
            _final_item(seg=0, speaker=("1", "")),
            _final_item(seg=1, speaker=("1", "")),
        ])
        ns["drain"]()
        self.assertEqual(len(tracker.lines), 2, "字母相同 → 行为逐字不变（照旧喂）")

    def test_different_speaker_does_not_merge(self):
        tracker = Tracker()
        ns = _drain_ns(tracker=tracker, items=[
            _final_item(seg=0, speaker=("1", "")),
            _final_item(seg=1, speaker=("2", "B")),
        ])
        ns["drain"]()
        self.assertEqual(len(tracker.lines), 1, "换人那一行不许拼成一句")
        self.assertEqual(tracker.lines[0][0], 0, "只喂了主讲那一行")

    def test_merge_resumes_after_the_same_speaker_talks_twice(self):
        # 主讲 → 主讲（合）→ B（不合）→ 主讲（上一行是 B，不合）→ 主讲（合）
        tracker = Tracker()
        ns = _drain_ns(tracker=tracker, items=[
            _final_item(seg=0, speaker=("1", "")),
            _final_item(seg=1, speaker=("1", "")),
            _final_item(seg=2, speaker=("2", "B")),
            _final_item(seg=3, speaker=("1", "")),
            _final_item(seg=4, speaker=("1", "")),
        ])
        ns["drain"]()
        self.assertEqual([c[0] for c in tracker.lines], [0, 1, 4])


class TestDrainSpeakerTag(unittest.TestCase):
    def test_set_live_speaker_called(self):
        ui = Recorder()
        ns = _drain_ns(ui=ui, items=[("speaker", "B")])
        ns["drain"]()
        self.assertEqual([c for c in ui.calls if c[0] == "set_live_speaker"],
                         [("set_live_speaker", "B")])

    def test_ui_without_the_slot_is_ignored(self):
        # 终端 UI 没有 set_live_speaker；`main.TerminalUI` 就是没有这一槽的那种
        ui = main_mod.TerminalUI()
        self.assertFalse(callable(getattr(ui, "set_live_speaker", None)))
        ns = _drain_ns(ui=ui, items=[("speaker", "B")])
        ns["drain"]()                                   # 不抛，就算过

    def test_ui_raising_does_not_kill_drain(self):
        ui = Recorder()
        ui.set_live_speaker = lambda label: (_ for _ in ()).throw(RuntimeError("boom"))
        ns = _drain_ns(ui=ui, items=[("speaker", "B"), _final_item()])
        ns["drain"]()
        self.assertTrue(ns["live"].finals, "后面的 final 照常处理")


# --------------------------------------------------------------------------
# 3) `final_worker` 里的封口句 → 字母
# --------------------------------------------------------------------------
def _sealed(text, start, end, speaker="", lang=""):
    return stream_asr.Sealed(text, start, end, lang, speaker)


class TestFinalWorkerSpeaker(unittest.TestCase):
    def _run(self, sealed, *, labeler=None, air=()):
        """跑 `final_worker`：`air` = 先播放的封口句（造出「上一句」），然后放 `sealed`。

        `auto_final.transcribe` 用替身（不联网、不碰模型）：回的字就是封口句原文 ——
        本文件关心的是**封口句 → 字母 → `streamq`**，不是 ASR 本身。
        """
        live, ui = Live(), Recorder()
        ns = _drain_ns(live=live, ui=ui)
        ns["running"].set()
        if labeler is not None:
            ns["labeler"] = labeler
        ns["final"] = types.SimpleNamespace(
            transcribe=lambda buf: types.SimpleNamespace(
                text=buf, source="test", ms=1.0, logprob=None, fell_back=False,
                steps=[], trace={}))
        air = (air,) if isinstance(air, stream_asr.Sealed) else tuple(air)
        for s in air + (sealed,):
            ns["sealedq"].put(s)
        exec(compile(_fn_src("final_worker"), "<final_worker>", "exec"), ns)  # noqa: S102
        t = threading.Thread(target=ns["final_worker"], daemon=True)
        t.start()
        want = len(air) + 1
        for _ in range(400):
            got = [i for i in list(ns["streamq"].queue) if i[0] == "final"]
            if len(got) >= want:
                break
            t.join(0.02)
        ns["running"].clear()
        ns["_wake_fw"].set()
        t.join(2.0)
        items = list(ns["streamq"].queue)
        return ns, items


    def test_letters_assigned_per_speaker(self):
        labeler = speaker_label.SpeakerLabeler()
        # 主讲 1 先开口、说得够多；2 说一句长句 → 拿 B
        labeler.observe("1", "this is a long lecture sentence here")
        ns, items = self._run(_sealed("okay so let me add something", 1.0, 2.0, "2"),
                              labeler=labeler)
        fin = [i for i in items if i[0] == "final"][0]
        self.assertEqual(fin[7], ("2", "B"))
        spk = [i for i in items if i[0] == "speaker"]
        self.assertEqual(spk[-1], ("speaker", "B"))

    def test_main_speaker_is_unlabelled(self):
        labeler = speaker_label.SpeakerLabeler()
        labeler.observe("1", "this is a long lecture sentence here")
        ns, items = self._run(_sealed("and another main sentence", 1.0, 2.0, "1"),
                              labeler=labeler)
        fin = [i for i in items if i[0] == "final"][0]
        self.assertEqual(fin[7], ("1", ""), "原始编号照记，字母为空（主讲不标）")

    def test_same_speaker_keeps_the_gap(self):
        # 同一人连着说 → `("gap", 秒数)` 原样（并行逻辑一个字没动）
        labeler = speaker_label.SpeakerLabeler()
        long_main = "this is a long lecture sentence here"
        labeler.observe("1", long_main)
        ns, items = self._run(_sealed("and another main sentence", 5.0, 6.0, "1"),
                              labeler=labeler,
                              air=_sealed(long_main, 1.0, 2.0, "1"))
        gaps = [i[1] for i in items if i[0] == "gap"]
        self.assertEqual(gaps[0], None, "第一句没有上一句 → 停顿未知")
        self.assertAlmostEqual(gaps[1], 3.0, msg="同一人 → 照旧并行（3 s 停顿）")

    def test_speaker_change_drops_the_gap(self):
        # 主讲 → B：第二句的 `gap` 必须变 None（否则两个人的话会被并成一行）
        labeler = speaker_label.SpeakerLabeler()
        long_main = "this is a long lecture sentence here"
        labeler.observe("1", long_main)
        labeler.observe("2", "okay student question here")
        ns, items = self._run(_sealed("so here is my question to you", 5.0, 6.0, "2"),
                              labeler=labeler,
                              air=_sealed(long_main, 1.0, 2.0, "1"))
        gaps = [i[1] for i in items if i[0] == "gap"]
        self.assertEqual(gaps, [None, None], "换人 → 不并行")

    def test_labeler_exception_still_prints(self):
        class Boom:
            def observe(self, speaker, text):
                raise RuntimeError("boom")

        ns, items = self._run(_sealed("a sentence that must still show", 1.0, 2.0, "2"),
                              labeler=Boom())
        fin = [i for i in items if i[0] == "final"]
        self.assertEqual(len(fin), 1, "labeler 坏了也必须上屏")
        self.assertEqual(fin[0][7], ("", ""), "坏了当没标签")
        self.assertEqual(fin[0][3], "a sentence that must still show")


def tearDownModule():
    if _HOME_BEFORE is None:
        os.environ.pop("HOME", None)
    else:
        os.environ["HOME"] = _HOME_BEFORE
    os.environ.pop("CLASSLIVE_LAYOUT", None)
    paths.STATE_ROOT = _STATE_BEFORE
    shutil.rmtree(_TMP_HOME, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
