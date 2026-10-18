#!/usr/bin/env python3
"""测试模式（采集 / 录音 / 上传）的判据。

    ClassLive.app/Contents/MacOS/python tests/test_testmode.py

## 这个文件钉住了什么

按 `docs/PLAN-test-mode.md` §7 的判据表来。现在有的是：

1. ⭐⭐ **`PeakNormalizer` 的三个只读计数器真的会动**
   （`capped` / `clamped` / `peak_db` 直方图）—— 那是 `PLAN-audio-gain.md` **步 1**。
2. ⭐ **它们描述的是归一化「之前」的电平** —— 与 `testmode` 已有的
   `block_rms_dbfs_*`（量的是**之后**）是两组不同的数，别混。
3. ⭐⭐ **3.10 `device` 块**（T22）：`pmset` 三种文本（AC / 电池 / 台式机无电池）、
   磁盘 fake、体积扫描（顶层前 5 大 + 预算 → `partial`）、全采集器炸了也照落盘
   （值全 null）、⏱ tick 的闸门与「不碰子进程」。

⚠️ 语料全是合成的（`numpy` 造的块），不碰 `sessions/`、不联网、不起 AppKit。
⚠️ 每条断言先问「把实现改坏它会不会红」—— 见每条的注释。
"""
from __future__ import annotations

import os
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import numpy as np                                                    # noqa: E402
import capture                                                        # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  {detail}" if detail else ""))


def _feed(levels, **kw):
    """喂一串**恒定幅度**的块，返回归一化器的 stats。"""
    nz = capture.PeakNormalizer(**kw)
    for a in levels:
        nz.process(np.full(capture.CHUNK, a, dtype=np.float32))
    return nz.stats


def main() -> int:
    print("\n--- T1 ⭐⭐ PeakNormalizer 三计数器：每个都要有自己的触发场景 ---")

    # ⚠️⚠️ **变异验证（这是本组最重要的一条）**：
    #    把 `_want_uncapped > self.max_gain` 写成 `want > self.max_gain`
    #    → **恒为假**（`want` 就是 `min(…, max_gain)`）→ 安静输入下 capped 会是 0
    #    → 下面第一条立刻红。
    #    （那个错写法在 `PLAN-audio-gain.md` 的步 1 原文里就是这么写的，
    #      2026-09-30 实现时查出来的。）
    quiet = _feed([0.002] * 300)
    check("⭐⭐ `capped`：安静输入会撞上限（⚠️ 写成 `want > max_gain` 就恒为 0）",
          quiet["capped"] == 300,
          f"capped={quiet['capped']}（期望 300）")

    normal = _feed([0.4] * 300)
    check("⭐ `capped`：正常音量**不**撞上限（不然它就是恒真的）",
          normal["capped"] == 0,
          f"capped={normal['capped']}（期望 0）")

    # ⚠️ 变异验证：把 `self.ceiling / p < self.gain` 写成 `>` 或删掉 → 这条红。
    #    ⚠️ 触发它需要**先让增益爬上去、再来一个大的** —— 只喂恒定幅度是测不到的
    #       （恒定幅度下 ceiling/p 恒 > gain，永远不会 clamp）。
    clamp = _feed([0.002] * 300 + [0.99])
    check("⭐⭐ `clamped`：静音期增益爬高后，突然一声大的会被钳位",
          clamp["clamped"] == 1,
          f"clamped={clamp['clamped']}（期望 1）")

    check("⭐ `blocks` 数得对（三种场景都是 300/301）",
          quiet["blocks"] == 300 and normal["blocks"] == 300
          and clamp["blocks"] == 301,
          f"{quiet['blocks']} / {normal['blocks']} / {clamp['blocks']}")

    print("\n--- T1b ⭐ 直方图：落点要准，且总和守恒 ---")
    nz = capture.PeakNormalizer()
    nz.process(np.full(capture.CHUNK, 0.5, dtype=np.float32))
    h = nz.stats["peak_db"]
    # 0.5 → 20·log10(0.5) = -6.02 dB → bin 下标 = int(-6.02) - HIST_DB_MIN
    #                                 = -6 - (-80) = 74
    _bin = int(20 * np.log10(0.5)) - capture.HIST_DB_MIN
    check(f"⭐⭐ 0.5 的块落在 **-6 dB** 那一格（下标 {_bin}）",
          h[_bin] == 1 and sum(h) == 1,
          f"该格={h[_bin]} 总和={sum(h)} 下标={_bin}")
    check("⚠️ 直方图总和 == blocks（计数不许漏）",
          sum(quiet["peak_db"]) == quiet["blocks"],
          f"{sum(quiet['peak_db'])} vs {quiet['blocks']}")

    print("\n--- T1c ⚠️ 只读：计数器不许改 `process()` 的输出 ---")
    # ⚠️ 判据就是**照着改之前那个公式独立重算一遍**，逐块对拍。
    #    变异验证：在 process() 里把 `return chunk * g` 改成 `chunk * self.gain`
    #    （= 顺手"修"掉那个 clamp）-> 这条红。
    def _ref(levels, ceiling=0.95, max_gain_db=30.0, hold_s=4.0, smooth=0.02):
        mx = 10 ** (max_gain_db / 20)
        decay = 10 ** (-capture.CHUNK / capture.SR / max(hold_s, 0.1))
        p, g, out = 1e-4, 1.0, []
        for a in levels:
            x = np.full(capture.CHUNK, a, dtype=np.float32)
            pk = float(np.max(np.abs(x)))
            p = max(pk, p * decay)
            want = min(ceiling / max(p, 1e-9), mx)
            g += (want - g) * smooth
            gg = min(g, ceiling / pk) if pk > 1e-9 else g
            out.append(x * gg)
        return out

    levels = [0.001, 0.002, 0.4, 0.02, 0.99, 0.3, 0.005, 0.6]
    nz2 = capture.PeakNormalizer()
    got = [nz2.process(np.full(capture.CHUNK, a, dtype=np.float32)) for a in levels]
    ref = _ref(levels)
    _d = max(float(np.max(np.abs(u - v))) for u, v in zip(got, ref))
    check("⭐⭐ 加了计数器之后，输出与**独立重算**逐块一致（行为零变化）",
          _d == 0.0, f"最大差 {_d:.3e}")

    print("\n--- T1d ⭐ `_NormalizedSource.stats()` 真的透传 ---")
    class _Src:
        def poll(self):
            return None

        def is_done(self):
            return True

        def close(self):
            pass

    s = capture._NormalizedSource(_Src())
    st = s.stats()
    check("⭐ `stats()` 返回的是**归一化器那个 dict 本身**（不拷贝）",
          st is s._norm.stats, f"{type(st).__name__}")
    check("⚠️ 而且它一开始就是齐的（四个键都在）",
          set(st) == {"blocks", "capped", "clamped", "peak_db",
                      "gain_db_max", "gain_db_sum"}, str(sorted(st)))

    print("\n--- T5 ⭐ 采集面的接缝：`note_block` 只认登记过的名字 ---")
    tm = __import__("testmode")

    class _T(tm.TestSession):
        def __init__(self):                    # ⚠️ 不碰磁盘：绕过它的 __init__
            self.t0 = 0.0
            self.session_path = None
            self.stem = None
            self._wav = None
            self.record_audio = False
            self.segments = []
            self.events = []
            self._extra = {}
            self._corr = {"pairs": 0, "changed": 0, "identical": 0, "no_fix": 0,
                          "words_raw": 0, "words_fixed": 0}
            self._seg_no = 0

    t = _T()
    t.note_block("norm", {"blocks": 1})
    check("⭐ 登记过的名字 -> 真的收下了",
          t._extra.get("norm") == {"blocks": 1}, str(t._extra))

    # ⚠️ 变异验证：把那个 `if name not in BLOCKS` 判断删掉 -> 这条红
    #    （错名字会静默建一个新键，而报告里看起来"采到了"）。
    t.note_block("nrom", {"blocks": 1})         # 打错一个字母
    check("⭐⭐ 打错的名字**不许**静默建键，要进 `events` 报一声",
          "nrom" not in t._extra
          and any(e["kind"] == "bad_block" for e in t.events),
          f"keys={sorted(t._extra)} events={t.events}")

    check("⭐ `BLOCKS` 覆盖计划里那七格对应的五块",
          {"norm", "vad", "live", "terms", "ui"} <= set(tm.BLOCKS),
          str(sorted(tm.BLOCKS)))

    print("\n--- T6 ⭐⭐ 两级矫正对：`note_correction(fixed, raw)` ---")
    # ⚠️ 变异验证：把 `if not fixed: no_fix += 1; return` 那两行删掉
    #    -> 「矫正失败不算 changed」那条红（空 en 会被当成"被改没了"）。
    # ⚠️ 每条用**各自的新实例** —— `_corr` 是**累积**的，共用一个实例会把
    #    前面那几条的数一起算进来（第一版就是这么假红的）。
    t_ident = _T()
    t_ident.note_correction("the same words", "the same words")
    check("⭐ 一模一样的一对 -> `identical`，不是 `changed`",
          t_ident._corr["identical"] == 1 and t_ident._corr["changed"] == 0,
          str(t_ident._corr))

    t_chg = _T()
    t_chg.note_correction("fixed version here", "raw version here")   # 各 3 词
    check("⭐ 不一样 -> `changed` +1，且两个词数都记了",
          t_chg._corr["changed"] == 1 and t_chg._corr["words_fixed"] == 3
          and t_chg._corr["words_raw"] == 3,
          f"changed={t_chg._corr['changed']} wf={t_chg._corr['words_fixed']} "
          f"wr={t_chg._corr['words_raw']}")

    t_nofix = _T()
    t_nofix.note_correction("", "some raw text")
    check("⭐⭐ `fixed` 为空 = **矫正失败/没做**，要单记 `no_fix`，**不许算 changed**",
          t_nofix._corr["no_fix"] == 1 and t_nofix._corr["changed"] == 0
          and t_nofix._corr["words_fixed"] == 0,
          str(t_nofix._corr))

    t_sum = _T()
    for a, b in (("a b", "a b"), ("c d", "x y"), ("", "z")):
        t_sum.note_correction(a, b)
    check("⭐ `pairs` 数的是**全部**收下的对（含 no_fix 那类）",
          t_sum._corr["pairs"] == 3, str(t_sum._corr["pairs"]))

    print("\n--- T7 ⭐⭐ 采集失败不平：吞掉，但**别无声** ---")
    # ⚠️ 变异验证：把 `_safe` 里那半段记账删掉（退回纯 `return None`）
    #    -> 前两条红（「采集坏了」与「本来就没数据」会变得分不出来）。
    class _T2(tm.TestSession):
        def __init__(self):
            self.t0 = 0.0
            self.session_path = None
            self.stem = None
            self._wav = None
            self.record_audio = False
            self.segments = []
            self.events = []
            self._extra = {}
            self._safe_bad = {}
            self._corr = {"pairs": 0, "changed": 0, "identical": 0, "no_fix": 0,
                          "words_raw": 0, "words_fixed": 0}
            self._seg_no = 0

        @tm._safe
        def boom(self):
            raise ValueError("故意的")

    t7 = _T2()
    check("⭐⭐ 采集抛了**不许**把调用方带崩（这条纪律一个字没动）",
          t7.boom() is None)
    check("⭐⭐ 但必须在 `events` 里出声（记方法名 + 异常类型）",
          any(e["kind"] == "safe_exc" and "boom" in e["detail"] and
              "ValueError" in e["detail"] for e in t7.events),
          str(t7.events[:1]))
    check("⭐ 而且按方法名计数（`collector_errors`）",
          t7._safe_bad.get("boom") == 1, str(t7._safe_bad))

    for _ in range(20):                       # 热路径：失败会一秒几十次
        t7.boom()
    _n_ev = sum(1 for e in t7.events if e["kind"] == "safe_exc")
    check(f"⭐⭐ **不刷屏**：每个方法最多记 {tm._SAFE_MAX_PER_FN} 条事件",
          _n_ev == tm._SAFE_MAX_PER_FN, f"记了 {_n_ev} 条")
    check("⭐ 但计数要接着涨（21 次全算上，没丢）",
          t7._safe_bad["boom"] == 21, str(t7._safe_bad))

    print("\n--- T8 ⭐ 术语注入计数：`select_terms(stats=…)` 不改返回值契约 ---")
    import translator as tr

    _s = "the supply curve slopes upward"
    _core = ["demand", "supply"]
    _always = ["elasticity", "equilibrium"]
    _terms = ["supply curve", "price ceiling", "surplus"]
    _no = tr.select_terms(_s, _terms, core=_core, always=_always)
    _st = {}
    _yes = tr.select_terms(_s, _terms, core=_core, always=_always, stats=_st)
    # ⚠️ 变异验证：把 `stats` 那条分支写进返回值的构造里 -> 这条红。
    check("⭐⭐ 传不传 `stats`，**返回的那个字符串必须一模一样**",
          _no == _yes, f"{_no!r} vs {_yes!r}")
    check("⭐ 计数：core/always 各按条数累加、calls +1",
          _st.get("calls") == 1 and _st.get("core") == len(_core)
          and _st.get("always") == len(_always),
          str(_st))
    check("⭐ `picked` 是**去重之后**实际注入的条数（<= 三档之和）",
          isinstance(_st.get("picked"), int) and _st["picked"] > 0
          and _st["picked"] <= _st["core"] + _st["always"] + _st["scored"],
          f"picked={_st.get('picked')} 三档之和="
          f"{_st.get('core', 0) + _st.get('always', 0) + _st.get('scored', 0)}")
    _st2 = {}
    for _ in range(3):
        tr.select_terms(_s, _terms, core=_core, always=_always, stats=_st2)
    check("⭐ 多次调用是**累加**（calls=3）", _st2.get("calls") == 3, str(_st2.get("calls")))
    check("⚠️ 不传 `stats` 时一个键都不该被碰（老调用方零感知）",
          tr.select_terms(_s, _terms, core=_core, always=_always) == _no)

    print("\n--- T9 ⭐ 实时总结 stats：口径要对齐离线探针 ---")
    import live_summary as L

    # ⚠️ 变异验证：把 `stats()` 里某个键名改掉（比如 `windows` → `window_n`）
    #    -> 下面第一条红。这一条守的正是「两边能对拍」这个承诺。
    _probe = {                       # 离线探针 metrics.json 里**能在线对上**的字段
        "windows", "atom_calls", "chapter_calls", "failed_calls", "chapters",
        "chapters_interim", "empty_window_ratio", "deadlines_regex",
        "deadlines_model", "jev_gate_calls", "gaps", "backlog_peak", "retries",
    }
    _s0 = L.LiveSummarizer(chat=lambda *a, **k: {"topic": "T", "points": []},
                           chapters_enabled=True, clock=lambda: 0.0)
    _d = _s0.stats()
    _missing = _probe - set(_d)
    check("⭐⭐ `stats()` 里那 13 个字段名**与探针的 metrics.json 逐字一致**",
          not _missing, f"缺={sorted(_missing)}")
    check("⭐ 而且不返回那些**在线口径对不上**的（宁可缺，别给不可比的数）",
          not ({"window_interval_median_s", "input_tokens_est", "elapsed_s",
                "board_sentence_ratio"} & set(_d)),
          str(sorted(set(_d))))
    check("⭐ 全是计数器/比例，没有非 JSON 类型",
          all(isinstance(v, (int, float, bool)) for v in _d.values()),
          str({k: type(v).__name__ for k, v in _d.items()
               if not isinstance(v, (int, float, bool))}))

    # ⭐ 真的会动：喂句子 + 推时间走完一窗
    _t = [0.0]
    _s1 = L.LiveSummarizer(chat=lambda *a, **k: {"topic": "T", "points": []},
                           chapters_enabled=False, clock=lambda: _t[0])
    for i in range(3):
        _s1.feed((i + 1, f"10:0{i}:00", f"sentence {i}", f"句子{i}"))
        _t[0] += 10.0
        _s1.step(_t[0])
    _t[0] += 40.0                           # 超过 ATOM_PAUSE_S=30 → 该提交这一窗了
    _s1.step(_t[0])
    _d1 = _s1.stats()
    check("⭐⭐ 喂 3 句 + 走完一窗 -> `windows`/`atom_calls` 都涨了",
          _d1["windows"] >= 1 and _d1["atom_calls"] >= 1,
          f"windows={_d1['windows']} atom_calls={_d1['atom_calls']}")
    check("⭐ 空窗被记下（假模型回的是空 points）-> `empty_window_ratio` > 0",
          _d1["empty_window_ratio"] > 0, str(_d1["empty_window_ratio"]))

    print("\n--- T10 ⭐⭐ UI 使用度：累加计数，不是一条一个事件 ---")
    t10 = _T()
    t10._ui = {}
    for _ in range(5):
        t10.note_ui("outline")
    t10.note_ui("ask")
    check("⭐ 同一个交互累加（连点 5 次 = 5）",
          t10._ui.get("outline") == 5 and t10._ui.get("ask") == 1, str(t10._ui))
    # ⚠️ 变异验证：把 `note_ui` 改成 append 一个事件 -> 这条红。
    check("⭐⭐ **不许**往 `events` 里塞 —— 按钮能连点几十下，会把时间轴挤没",
          not any(e.get("kind") == "ui" for e in t10.events), str(t10.events))

    print("\n--- T11 ⭐ `Overlay` 真的接上了那个回调 ---")
    import inspect
    import overlay as ov
    _sig = inspect.signature(ov.Overlay.__init__)
    check("⭐ `Overlay.__init__` 有 `note` 形参（默认 None = 零开销）",
          "note" in _sig.parameters
          and _sig.parameters["note"].default is None, str(_sig))
    # ⚠️ 变异验证：把 overlay 里那 7 处 `self._note(...)` 删掉 -> 这条红。
    _src = pathlib.Path(ov.__file__).read_text(encoding="utf-8")
    _todo = ["submit", "quit", "lost", "ask", "new_topic", "trans_mode", "outline"]
    _missing = [n for n in _todo if f'self._note("{n}")' not in _src]
    check("⭐⭐ 七个交互点**每个都真的记了一笔**（少一个就红）",
          not _missing, f"没记的：{_missing}")
    check("⭐ 而且默认值是个**空 lambda**（不装采集器时零行为）",
          "(lambda _name: None)" in _src)

    print("\n--- T12 ⭐⭐ 真的点一下：`_note` 把交互报出去 ---")
    # ⚠️ 这条是**行为**判据，不是源码串匹配（T11 那条才是串匹配）——
    #    它真的构造一个 Overlay、真的调那两个交互方法，看回调有没有响。
    # ⚠️ 变异验证：把 `_toggle_outline` 里那行 `self._note("outline")` 删掉 -> 这条红。
    _seen: list = []
    try:
        import overlay as _ovmod
        _o = _ovmod.Overlay(note=_seen.append)
    except Exception as _e:                                   # noqa: BLE001
        print(f"  ⏭️  AppKit 不可用，跳过：{type(_e).__name__}")
    else:
        try:
            _o._ask()                      # 「讲一下」
            _o._toggle_outline()           # 纲要（章节条/菜单栏都走它）
            _o._toggle_outline()           # 再点一次 = 关掉
        finally:
            close = getattr(_o, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:                             # noqa: BLE001
                    pass
        check("⭐⭐ 程序化点「讲一下」与「纲要」-> `note` 回调真的响了",
              "ask" in _seen and _seen.count("outline") == 2, str(_seen))

    print("\n--- T13 ⭐⭐ 分段录音：段与段之间**一个样本都不许丢** ---")
    # ⚠️ 变异验证：把切点从「按样本数」改成按墙钟（或漏掉最后一段）
    #    -> 这条红。**这是分段最容易出的错，而且它不报错。**
    import shutil
    import tempfile
    import wave

    _d = pathlib.Path(tempfile.mkdtemp(prefix="cl_tm_rec_"))
    _old_seg = tm.AUDIO_SEG_S
    tm.AUDIO_SEG_S = 1                       # 1 秒一段，方便测（生产是 600）
    try:
        _t = tm.TestSession(_d / "x.md", record_audio=True)
        _n_in = 0
        for _ in range(25):                  # 25 × 0.1s = 2.5 秒 → 该切 3 段
            _blk = np.zeros(tm.SR // 10, dtype=np.float32)
            _n_in += len(_blk)
            _t.note_chunk(_blk)
        _t._close_wav()
        _n_out = 0
        for _p in _t._wav_paths:
            with wave.open(str(_p)) as _w:
                _n_out += _w.getnframes()
        check("⭐⭐ 写进去的样本数 == 所有分段加起来（**段间不丢**）",
              _n_out == _n_in, f"进 {_n_in} / 出 {_n_out}")
        check("⭐ 而且真的**切成了多段**（不然上面那条是恒真的）",
              len(_t._wav_paths) >= 2, f"{len(_t._wav_paths)} 段")
        # ⭐⭐ **更强的那条**：除了最后一段，**每段必须正好是 `AUDIO_SEG_S * SR` 帧**。
        #    守恒那条抓不住「轮转时机错」——错位写会把某个边界块**写到另一段**，
        #    总数照样守恒，但段的边界全歪了。这一条抓得住。
        #    ⚠️ 变异验证：把 `_rotate_wav()` 那行**挪到 `writeframes` 之前** -> 这条红。
        _sizes = []
        for _p in _t._wav_paths:
            with wave.open(str(_p)) as _w:
                _sizes.append(_w.getnframes())
        _want = tm.AUDIO_SEG_S * tm.SR
        _bad_seg = [n for n in _sizes[:-1] if n != _want]
        check(f"⭐⭐ 除最后一段外，每段**正好 {_want} 帧**（轮转时机不许错位）",
              not _bad_seg, f"段大小={_sizes} 期望={_want}")
    finally:
        tm.AUDIO_SEG_S = _old_seg
        shutil.rmtree(_d, ignore_errors=True)

    print("\n--- T14 ⭐⭐ 收尾转码：wav → Opus，转了才删 wav ---")
    _d2 = pathlib.Path(tempfile.mkdtemp(prefix="cl_tm_opus_"))
    try:
        _t2 = tm.TestSession(_d2 / "y.md", record_audio=True)
        _n_chunks = 20
        for _ in range(_n_chunks):
            _t2.note_chunk((np.random.rand(tm.SR // 10).astype(np.float32) - 0.5) * 0.4)
        _t2._close_wav()
        _wavs = list(_t2._wav_paths)
        _sec = _n_chunks * (tm.SR // 10) / tm.SR        # ⚠️ 从块数算 —— **别去读 wav**
        _ops = _t2._to_opus()                           #     （下面那一步已经把它删了）
        _left = [p for p in _wavs if p.exists()]
        check("⭐⭐ 转出了 opus、而且 **wav 被删掉了**",
              bool(_ops) and all(p.suffix == ".opus" for p in _ops) and not _left,
              f"opus={[p.name for p in _ops]} 残留 wav={[p.name for p in _left]}")
        check("⭐ opus 非空（0 字节算失败）",
              all(p.stat().st_size > 0 for p in _ops),
              str([p.stat().st_size for p in _ops]))
        # ⚠️⚠️ **这条第一版被 `except FileNotFoundError` 吞成了「没装 ffmpeg」并跳过** ——
        #    而 ffmpeg 明明装着，真因是我在删掉之后才去 `wave.open` 那个 wav。
        #    **判定：跳过不计入统计 = 判据没跑还报 ✅**（本仓记过这个形态）。
        #    → 现在时长的来源改成**块数**，而且**不再吞 FileNotFoundError**。
        if _ops:
            _kbps = _ops[0].stat().st_size * 8 / _sec / 1000
            check(f"⭐ 码率落在 Opus 24k 附近（实测 {_kbps:.1f} kbps）",
                  15 < _kbps < 40, f"{_kbps:.1f} kbps / {_sec:.1f} 秒")
    finally:
        shutil.rmtree(_d2, ignore_errors=True)

    print("\n--- T15a ⭐⭐ 没有 ffmpeg：soundfile 兜底编出 opus，wav 删掉 ---")
    _d5 = pathlib.Path(tempfile.mkdtemp(prefix="cl_tm_sf_"))
    _old_ff = tm.subprocess.run
    try:
        _t5 = tm.TestSession(_d5 / "w.md", record_audio=True)
        _n5 = 30
        for _ in range(_n5):
            _t5.note_chunk((np.random.rand(tm.SR // 10).astype(np.float32) - 0.5) * 0.4)
        _t5._close_wav()
        _w5 = list(_t5._wav_paths)

        def _noff(*a, **k):
            raise FileNotFoundError("ffmpeg 假装没装")
        tm.subprocess.run = _noff
        _ops5 = _t5._to_opus()
        check("⭐⭐ ffmpeg 不在 -> soundfile 编出 opus、**wav 删掉**",
              bool(_ops5) and all(p.suffix == ".opus" and p.stat().st_size > 0 for p in _ops5)
              and not any(p.exists() for p in _w5),
              f"opus={[p.name for p in _ops5]} 残留={[p.name for p in _w5 if p.exists()]}")
        check("⭐ 走了兜底要**记进 events**（报告里看得出这台没装 ffmpeg）",
              any(e["kind"] == "transcode_soundfile" for e in _t5.events)
              and not any(e["kind"] == "transcode_failed" for e in _t5.events),
              str([e["kind"] for e in _t5.events]))
        if _ops5:
            import soundfile as _sf
            _info = _sf.info(str(_ops5[0]))
            check("⭐ 编出来的是 16 kHz 单声道 Opus、时长对得上",
                  _info.samplerate == tm.SR and _info.channels == 1 and _info.subtype == "OPUS"
                  and abs(_info.duration - _n5 * 0.1) < 0.1,
                  f"{_info.samplerate} Hz · {_info.channels} ch · {_info.subtype} · {_info.duration:.2f}s")
    finally:
        tm.subprocess.run = _old_ff
        shutil.rmtree(_d5, ignore_errors=True)

    print("\n--- T15 ⭐⚠️ 转码失败（ffmpeg 和 soundfile 都不行）：**保留 wav**，别丢音频 ---")
    _d3 = pathlib.Path(tempfile.mkdtemp(prefix="cl_tm_fail_"))
    _old_ff = tm.subprocess.run
    _old_sf = tm._sf_opus
    tm._sf_opus = lambda w, o: "RuntimeError: soundfile 假装坏了"
    try:
        _t3 = tm.TestSession(_d3 / "z.md", record_audio=True)
        for _ in range(20):
            _t3.note_chunk(np.zeros(tm.SR // 10, dtype=np.float32))
        _t3._close_wav()
        _w3 = list(_t3._wav_paths)

        def _boom(*a, **k):
            raise FileNotFoundError("ffmpeg 假装没装")
        tm.subprocess.run = _boom
        _ops3 = _t3._to_opus()
        check("⭐ 转码全失败 -> 不返回 opus，且**记进 events**",
              _ops3 == [] and any(e["kind"] == "transcode_failed" for e in _t3.events),
              f"ops={_ops3} events={[e['kind'] for e in _t3.events]}")
        check("⭐⭐ **wav 必须还在** —— 宁可占地方，不许丢音频",
              all(p.exists() for p in _w3), str([p.name for p in _w3 if not p.exists()]))
        # ⭐⭐ 留下的 wav 还得**进上传清单** —— 只留在本机，作者照样收不到音频
        #    （没装 ffmpeg 的用户正是这样：只传上去报告和转录）。
        #    ⚠️ 变异验证：删掉 `finish` 里补 wav 的那两行 -> 这条红。
        _t3.finish(bundle=False)
        _up3 = _t3.upload_files()
        check("⭐⭐ 转码失败时，**留下的 wav 进上传清单**",
              all(p in _up3 for p in _w3) and bool(_w3),
              f"上传清单={[p.name for p in _up3]}")
    finally:
        tm.subprocess.run = _old_ff
        tm._sf_opus = _old_sf
        shutil.rmtree(_d3, ignore_errors=True)

    print("\n--- T16 ⭐ 默认值：测试模式下默认录，正常 cl 一个字不录 ---")
    _src_main = (HERE / "main.py").read_text(encoding="utf-8")
    check("⭐⭐ `--record-audio` 的 default 是 **None**（这样才关得掉）",
          'action="store_true", default=None' in _src_main)
    check("⭐⭐ 有一个**反向旗标** `--no-record-audio` 绑同一个 dest",
          '--no-record-audio", dest="record_audio", action="store_false"' in _src_main)
    check("⭐ 真正默认值按「是不是测试模式」判（且显式旗标优先）",
          "args.record_audio if args.record_audio is not None" in _src_main)
    check("⭐⭐ 而**正常上课那条路的默认没被动**（D1 的边界）",
          "record_audio=args.record_audio)" not in _src_main)

    # ---- ⭐⭐ 逐段探针：主模型 / 兜底模型**分开**记 ----
    # ⚠️ 2026-10-07 的教训：原来只记"最终用了哪个模型"一格，把两条信息压成了一个 ——
    #    「主模型（Whisper）空了几次」与「兜底模型（Parakeet）空了几次」分不开，
    #    于是拿兜底模型离线跑出来的空被当成了"空转写"去查（那是幽灵）。
    print("--- ⭐⭐ 逐段探针（主模型 vs 兜底模型）+ 异常段原信号 ---")
    _d5 = pathlib.Path(tempfile.mkdtemp(prefix="cl_tm_probe_"))
    _t5 = tm.TestSession(_d5 / "p.md", record_audio=False, record_float=True)
    _b = np.zeros(tm.SR, dtype=np.float32)
    _t5.note_segment(_b, "正常一段", 100.0, "whisper",
                     final_text="正常一段", final_degenerate=False)
    _t5.note_segment(_b, "兜底救回来的", 120.0, "local-parakeet", fell_back=True,
                     final_text="", final_degenerate=False, draft_text="兜底救回来的",
                     steps=("off", "local:whisper", "empty-local", "local:parakeet"),
                     trace={"local_text": ""})   # R10: 最后一个本地主模型的结果是空的
    _p0 = _t5.segments[0]["probe"]
    _p1 = _t5.segments[1]["probe"]
    check("⭐⭐ 主模型那一侧**单独**记（`final_empty` / `final_degenerate`）",
          _p0["final_empty"] is False and _p1["final_empty"] is True
          and _p1["final_degenerate"] is False,
          f"p0={_p0} p1={_p1}")
    check("⭐ 没回退 → 兜底原文是 `None`（**不是空串** —— 那会把「没跑」说成「跑了但空」）",
          _p0["draft_text"] is None and _p0["draft_empty"] is None
          and _p1["draft_text"] == "兜底救回来的",
          f"p0.draft={_p0['draft_text']!r} p1.draft={_p1['draft_text']!r}")
    check("⭐⭐ 异常段（走了回退）留了原信号，正常段**没留**",
          bool(_p1["float_saved"]) and not _p0["float_saved"]
          and pathlib.Path(_p1["float_saved"]).exists()
          and not (pathlib.Path(str(_t5.stem) + ".probe") / "000.npy").exists(),
          f"p0={_p0['float_saved']!r} p1={_p1['float_saved']!r}")
    # ⚠️ 变异敏感：把 `record_float` 那个开关去掉（无条件写），这条红。
    _t6 = tm.TestSession(_d5 / "q.md", record_audio=False, record_float=False)
    _t6.note_segment(_b, "x", 1.0, "parakeet", fell_back=True, final_text="",
                     trace={"local_text": ""})
    check("⚠️ `record_float=False` 时快照目录**根本不该建**",
          _t6.segments[0]["probe"]["float_saved"] == ""
          and not pathlib.Path(str(_t6.stem) + ".probe").exists(),
          repr(_t6.segments[0]["probe"]["float_saved"]))


    # ---- R9 / R10 / R12：定稿上云接线 ----
    print("--- R9/R10/R12 定稿上云（steps / trace / float 条件 / 体积统计）---")
    import json
    _t7a = tm.TestSession(_d5 / "r0.md", record_audio=False, record_float=True)
    _t7a.note_segment(_b, "x", 1.0, "groq", steps=("cloud:groq",), trace={"local_text": None})
    check("R9 `note_segment` 收 `steps` 与 `trace`（签名不匹配=整段被吞成「定稿失败」、课上静默丢句）",
          len(_t7a.segments) == 1 and not _t7a._safe_bad, repr(_t7a._safe_bad))
    _t7 = tm.TestSession(_d5 / "r.md", record_audio=False, record_float=True)
    _t7.note_segment(None, "x", 1.0, "groq")        # audio=None 必抛
    check("R9 `note_segment` 带 `@_safe`：内部抛了不外溢，且记进 collector_errors",
          _t7._safe_bad.get("note_segment") == 1, repr(_t7._safe_bad))
    _t8 = tm.TestSession(_d5 / "s.md", record_audio=False, record_float=True)
    # 1) 云端成功: 本地没跑 → 不存
    _t8.note_segment(_b, "cloud", 5.0, "groq", steps=("cloud:groq",),
                     trace={"local_text": None, "primary_text": "cloud"})
    # 2) 整节课断网: 本地正常出了字 → 不存（旧逻辑 fell_back 会每段都存）
    _t8.note_segment(_b, "local ok", 5.0, "local-parakeet", fell_back=True,
                     steps=("cloud:timeout", "local:parakeet"),
                     trace={"local_text": "local ok"})
    # 3) 本地主模型退化 → 存
    _t8.note_segment(_b, "fb", 5.0, "local-parakeet", fell_back=True,
                     steps=("off", "local:whisper", "degenerate", "local:parakeet"),
                     trace={"local_text": "... ... ... ... ... ..."})
    _fs = [s["probe"]["float_saved"] for s in _t8.segments]
    check("R10 float 只在「最后一个本地主模型空/退化」时存（云端成功/断网回退出了字 都不存）",
          (not _fs[0]) and (not _fs[1]) and bool(_fs[2]), repr(_fs))
    check("R9 `steps` 原样落进段记录",
          _t8.segments[1]["steps"] == ["cloud:timeout", "local:parakeet"],
          repr(_t8.segments[1].get("steps")))
    _t8.note_ui("outline")
    _t8.note_ui_value("zh_overflow", {"overflow": 2, "seen": 40, "lines": 3})
    _rep = json.loads(pathlib.Path(_t8.finish(bundle=False)).read_text(encoding="utf-8"))
    check("⭐ C4 译文超限统计进了 `blocks.ui.zh_overflow`，且不挤掉计数",
          _rep["blocks"]["ui"].get("zh_overflow") == {"overflow": 2, "seen": 40, "lines": 3}
          and _rep["blocks"]["ui"].get("outline") == 1, repr(_rep["blocks"].get("ui")))
    _asr = _rep["asr"]
    check("报告 `asr.source_pct` 是 {source: 占比}",
          _asr.get("source_pct") == {"groq": 33.3, "local-parakeet": 66.7}, repr(_asr.get("source_pct")))
    check("报告 `asr.steps_counts` 逐步计数",
          _asr.get("steps_counts", {}).get("cloud:timeout") == 1
          and _asr["steps_counts"].get("local:parakeet") == 2
          and _asr["steps_counts"].get("degenerate") == 1,
          repr(_asr.get("steps_counts")))
    check("报告 notes 不再说「定稿默认走 Whisper, 所以 logprob 通常是 null」那句过期的话",
          not any("定稿默认走 Whisper" in n for n in _rep["notes"]), repr(_rep["notes"]))
    # R12: 体积统计看「实际在盘上的」（读端换到临时 HOME）
    import models as _models
    _home12 = _d5 / "home12"
    _wd = _home12 / "models" / _models.basename_of("whisper")
    _wd.mkdir(parents=True)
    (_wd / "w.bin").write_bytes(b"x" * 2_000_000)
    _old_home = os.environ.get("HOME")
    os.environ["HOME"] = str(_home12)
    try:
        _mb = tm.collect_env()["models_mb"]
    finally:
        os.environ["HOME"] = _old_home or ""
    _wk = _models.basename_of("whisper")
    check("R12 `models_mb` 把盘上的可选 Whisper 也算进去（原来只数 REQUIRED）",
          _mb.get(_wk, 0) and _mb[_wk] > 0, repr(_mb))
    check("R12 必需模型不在盘上仍报 None（如实说「没有」）",
          _mb.get(_models.basename_of("parakeet"), "缺键") is None, repr(_mb))
    check("R12 collect_env 的包列表含 soundfile",
          "soundfile" in tm.collect_env()["packages"])

    print("\n--- B3 数据包里的 README.txt 要和包里真放的东西对得上 ---")
    import zipfile
    _d6 = pathlib.Path(tempfile.mkdtemp(prefix="cl_tm_bundle_"))
    try:
        _rp = _d6 / "x.report.json"
        _rp.write_text("{}", encoding="utf-8")
        _au = _d6 / "x.001.opus"
        _au.write_bytes(b"OggS")
        _bp = tm.make_bundle(_d6 / "x", _rp, None, [_au])
        with zipfile.ZipFile(_bp) as _z:
            _names = _z.namelist()
            _txt = _z.read("README.txt").decode("utf-8")
        check("README.txt 说的音频位置（audio/）包里真有，且不再写不存在的 audio.wav / sessions.md",
              "audio/x.001.opus" in _names and "audio/" in _txt
              and "audio.wav" not in _txt and "sessions.md" not in _txt, str(_names))
        check("⭐ README.txt 说明**会自动上传**并给出关法（不再是「自己确认再发」）",
              "自动上传" in _txt and "CLASSLIVE_UPLOAD=0" in _txt and "发出去之前" not in _txt)
    finally:
        shutil.rmtree(_d6, ignore_errors=True)

    print("\n--- T17 ⭐⭐ ③ 跨段合并原文：成功一次 + 失败一次，两条都在 ---")
    # ⚠️ 变异验证：把 `note_merge_item` 里 `if len(...) >= TEXT_ITEMS_MAX: return` 那行
    #    删掉（不封顶）→ 下面「超上限截断」那条红；把 `replaced` 写成恒 True →
    #    「失败那条 replaced=False」红。
    _d7 = pathlib.Path(tempfile.mkdtemp(prefix="cl_tm_mg_"))
    try:
        _t17 = tm.TestSession(_d7 / "m.md")
        _ok_job = {"i": 3, "j": 4, "gap_s": 0.812, "trigger": "both",
                   "tail_en": "The gradient is", "tail_zh": "梯度是",
                   "next_en": "positive here.", "next_zh": "这里为正。"}
        _bad_job = {"i": 9, "j": 10, "gap_s": 1.24, "trigger": "asr",
                    "tail_en": "It follows that", "tail_zh": "由此推出",
                    "next_en": "", "next_zh": ""}
        _t17.note_merge_item(_ok_job, "The gradient is positive here.",
                             "梯度这里为正。", True)
        _t17.note_merge_item(_bad_job, "", "", False)     # 重翻失败那条
        _rep17 = json.loads(pathlib.Path(_t17.finish(bundle=False)).read_text(encoding="utf-8"))
        _it = _rep17["blocks"]["merge"]["items"]
        check("⭐⭐ 一次成功 + 一次失败 → items 两条、`replaced` 一真一假",
              len(_it) == 2 and _it[0]["replaced"] is True
              and _it[1]["replaced"] is False, str([x["replaced"] for x in _it]))
        check("⭐ 重翻失败那条 `merged_*` 是空串（不是缺键、不是 null）",
              _it[1]["merged_en"] == "" and _it[1]["merged_zh"] == "",
              f"{_it[1]['merged_en']!r} / {_it[1]['merged_zh']!r}")
        check("⭐ 尾句 / 首句**两边的原文都留了**（只留合并结果就问不出「翻得好不好」）",
              _it[0]["tail_en"] == "The gradient is" and _it[0]["next_zh"] == "这里为正。"
              and _it[0]["trigger"] == "both" and abs(_it[0]["gap_s"] - 0.812) < 1e-6,
              str({k: _it[0][k] for k in ("tail_en", "next_zh", "trigger", "gap_s")}))
        for _ in range(305):                              # 超上限
            _t17.note_merge_item(_ok_job, "x", "y", True)
        check(f"⭐⭐ 超上限截断到 {tm.TEXT_ITEMS_MAX} 条",
              len(_t17._merge_items) == tm.TEXT_ITEMS_MAX,
              f"{len(_t17._merge_items)} 条")
    finally:
        shutil.rmtree(_d7, ignore_errors=True)

    print("\n--- T18 ⭐ ③ 课后补译原文：只记真补出来的那几句 ---")
    _d8 = pathlib.Path(tempfile.mkdtemp(prefix="cl_tm_bf_"))
    try:
        _t18 = tm.TestSession(_d8 / "b.md")
        _t18.note_block("backfill", {"need": 3, "done": 2})
        _t18.note_backfill_items([{"en": "The gradient is positive.",
                                   "zh": "梯度为正。"},
                                  {"en": "", "zh": "只有中文那句。"}])
        _rep18 = json.loads(pathlib.Path(_t18.finish(bundle=False)).read_text(encoding="utf-8"))
        _bf = _rep18["blocks"]["backfill"]
        check("⭐⭐ 计数（need/done）与 items 同时在里面（不是二选一）",
              _bf["need"] == 3 and _bf["done"] == 2 and len(_bf["items"]) == 2, str(_bf))
        check("⭐ 每条是 `{en, zh}`（空 en 也如实留着 —— 那是「没补出英文」的真话）",
              _bf["items"][1] == {"en": "", "zh": "只有中文那句。"}, str(_bf["items"]))
        check("⭐ 没补过 → items 是空表（不是缺键）",
              tm.TestSession(_d8 / "b2.md").finish(bundle=False) and
              json.loads(pathlib.Path(str(_d8 / "b2")).with_suffix(".report.json")
                         .read_text(encoding="utf-8"))["blocks"]["backfill"]["items"] == [])
    finally:
        shutil.rmtree(_d8, ignore_errors=True)

    print("\n--- T19 ⭐⭐ ④ 收音断档：伪造墙钟制造 3 s 空洞 ---")
    # ⚠️ 变异验证：把 `note_chunk` 里那行 `self._capture_tick(self._wall())` 删掉 →
    #    gaps 恒 0 → 第一条红；把 `time.time` 换成 `time.monotonic`（睡眠不走）→
    #    真机上这条抓不出断档（判据注入的正是墙钟）。
    _d9 = pathlib.Path(tempfile.mkdtemp(prefix="cl_tm_cap_"))
    try:
        _clk = [100.0]
        _t19 = tm.TestSession(_d9 / "c.md", wall_clock=lambda: _clk[0])
        _blk = np.zeros(1, dtype=np.float32)
        _t19.note_chunk(_blk)                       # 第一块 @100.0
        _clk[0] = 103.0
        _t19.note_chunk(_blk)                       # +3.0 s → 一次断档
        _cb = _t19.capture_block()
        _g = _cb.get("gaps") or {}
        _gi = _g.get("items") or []
        check("⭐⭐ 一个 3 s 断档 → 恰好 1 条断档、明细 1 条（`gaps` 键必须在）",
              _g.get("n") == 1 and len(_gi) == 1, str(_cb))
        check("⭐⭐ `gap_s`≈3、`t_wall_offset_s`≈3",
              bool(_gi) and abs(_gi[0]["gap_s"] - 3.0) < 0.01
              and abs(_gi[0]["t_wall_offset_s"] - 3.0) < 0.01, str(_gi))
        check("⭐⭐ `deficit_s` ≈ 3（墙钟比实际音频长了 3 s）",
              abs(_cb["deficit_s"] - 3.0) < 0.05, f"deficit={_cb['deficit_s']}")
        check("⭐ 总时长 = 明细之和（超过 50 条只截明细，总数照样涨）",
              abs(_g.get("total_s", -1) - 3.0) < 0.01, str(_g.get("total_s")))
        check("⭐ 块里有 `available` / `wall_s` / `audio_s`",
              _cb["available"] is True and _cb["wall_s"] == 3.0 and _cb["audio_s"] == 0.0,
              str(_cb))
        # 断档明细上限：60 次 1.5 s 空洞 → n=60，明细只留 50 条
        _clk[0] = 104.0
        for _ in range(60):
            _clk[0] += 1.5
            _t19.note_chunk(_blk)
        _cb2 = _t19.capture_block()
        _g2 = _cb2.get("gaps") or {}
        check(f"⭐⭐ 明细封顶 {tm.CAPTURE_GAPS_MAX} 条，但**总数与总时长不封顶**",
              _g2.get("n") == 61 and len(_g2.get("items") or []) == tm.CAPTURE_GAPS_MAX
              and _g2.get("total_s", 0) > 90.0,
              f"n={_g2.get('n')} items={len(_g2.get('items') or [])} "
              f"total={_g2.get('total_s')}")
        check("⭐ 一块音频都没收到 → `available: false`（不是一堆零）",
              tm.TestSession(_d9 / "c2.md").capture_block()["available"] is False)
    finally:
        shutil.rmtree(_d9, ignore_errors=True)

    print("\n--- T20 ⭐ ⑤ 字幕视图：按时间加权 + 面板宽 p50 ---")
    _d10 = pathlib.Path(tempfile.mkdtemp(prefix="cl_tm_view_"))
    try:
        _t20 = tm.TestSession(_d10 / "v.md")
        _t20.note_view(4, 3, 720.0)
        time.sleep(0.06)
        _t20.note_view(5, 2, 800.0)
        time.sleep(0.15)
        _t20.on_vis(False)                          # 不可见 → 收口（不记进任何档位）
        time.sleep(0.15)
        _rep20 = json.loads(pathlib.Path(_t20.finish(bundle=False)).read_text(encoding="utf-8"))
        _v = _rep20["blocks"]["ui"]["view"]
        check("⭐⭐ 各档行数都记了秒数（4 行档 ≈ 0.06 s）",
              "4" in _v["rows_s"] and "5" in _v["rows_s"]
              and _v["rows_s"]["4"] >= 0.05, str(_v["rows_s"]))
        check("⭐ 档位行数（中文 2/3 行）也各有一格",
              "3" in _v["lines_s"] and "2" in _v["lines_s"], str(_v["lines_s"]))
        check("⭐⭐ 面板宽 p50 = median(720, 800) = 760（两次采样都在）",
              _v["width_p50"] == 760.0 and _v["samples"] == 2, str(_v))
        check("⭐⭐ 不可见那一段**没有**算进任何档位（收口之后不再累加）",
              _v["rows_s"]["5"] < 0.22, str(_v["rows_s"]))
    finally:
        shutil.rmtree(_d10, ignore_errors=True)

    print("\n--- T21 ⭐⭐ ⑦ 一份完整 finish 的报告：新字段全在、json 不炸、无 bad_block ---")
    _d11 = pathlib.Path(tempfile.mkdtemp(prefix="cl_tm_full_"))
    try:
        _t21 = tm.TestSession(_d11 / "full.md", wall_clock=time.time)
        _t21.note_chunk(np.zeros(tm.SR // 10, dtype=np.float32))
        _t21.note_translate(120.0, 200.0, 1500.0, "local", idle_before_ms=3000.0)
        _t21.note_streamq_dwell(18.0)
        _t21.note_view(4, 3, 720.0)
        _t21.note_ui("outline")
        _t21.note_merge_item({"gap_s": 0.5, "trigger": "both", "tail_en": "a",
                              "tail_zh": "甲", "next_en": "b", "next_zh": "乙"},
                             "a b", "甲乙", True)
        _t21.note_backfill_items([{"en": "x", "zh": "X"}])
        _t21.note_block("merge", {"triggered": 1, "replaced": 1, "failed": 0})
        _t21.note_block("backfill", {"need": 1, "done": 1})
        _t21.note_block("energy", _t21.energy_block(120.0))
        _t21.note_block("capture", _t21.capture_block())
        _t21.note_block("stream", {"engine": "soniox", "fallback_reason": "",
                                   "sealed_n": 1,
                                   "seal_latency_s": {"p50": 0.9, "p90": 0.9, "n": 1},
                                   "terms_n": 5, "terms_dropped": 0})
        _rep21 = json.loads(pathlib.Path(_t21.finish(bundle=False)).read_text(encoding="utf-8"))
        check("⭐⭐ 六个新格全在：energy / capture / merge.items / backfill.items / "
              "ui.view / ui.streamq_dwell_ms / translation_latency.by_idle",
              all([
                  "energy" in _rep21["blocks"], "capture" in _rep21["blocks"],
                  "items" in _rep21["blocks"]["merge"],
                  "items" in _rep21["blocks"]["backfill"],
                  "view" in _rep21["blocks"]["ui"],
                  "streamq_dwell_ms" in _rep21["blocks"]["ui"],
                  "by_idle" in _rep21["translation_latency"]]),
              str(sorted(_rep21["blocks"])))
        check("⭐⭐ `blocks` 里**没有** `bad_block`（名字都登记过）",
              not [e for e in _rep21["events"] if e["kind"] == "bad_block"],
              str([e for e in _rep21["events"] if e["kind"] == "bad_block"]))
        check("⭐ `collector_errors` 为空（这次采集一条异常都没有）",
              _rep21["collector_errors"] == {}, str(_rep21["collector_errors"]))
        check("⭐⭐ 整份报告 `json.dumps` 不炸（含 numpy 数）",
              bool(json.dumps(_rep21, ensure_ascii=False)), "ok")
        check("⭐ `block_names` 里有 energy / capture（该有哪几块的清单）",
              {"energy", "capture"} <= set(_rep21["block_names"]), str(_rep21["block_names"]))
        check("⭐⭐ `blocks.stream`（Soniox 生产量具）原样落进报告",
              _rep21["blocks"].get("stream", {}).get("engine") == "soniox"
              and _rep21["blocks"]["stream"].get("sealed_n") == 1,
              str(_rep21["blocks"].get("stream")))
        check("⭐ `stream` 在 `block_names` 清单里（该有哪几块）",
              "stream" in _rep21["block_names"], str(_rep21["block_names"]))
    finally:
        shutil.rmtree(_d11, ignore_errors=True)

    print("\n--- T22 ⭐⭐ 3.10 `device` 块：pmset / 磁盘 / 体积 / 全炸 / ⏱ tick ---")
    # ⚠️ 这一节的语料全是合成的（文本样本 / tmp 目录 / fake），不碰真盘、不联网、
    #    不调真 sysctl/pmset —— 每条的变异验证写在它自己头上。
    import json as _json
    import shutil as _sh
    import tempfile as _tf

    # ① `pmset -g batt` 的三种文本（纯函数，不碰真机）
    _ac = tm.parse_pmset("Now drawing from 'AC Power'\n"
                         " -InternalBattery-0 (id=1)\t100%; charged; "
                         "0:00 remaining present: true\n")
    _bat = tm.parse_pmset("Now drawing from 'Battery Power'\n"
                          " -InternalBattery-0 (id=1)\t83%; discharging; "
                          "4:12 remaining present: true\n")
    _desk = tm.parse_pmset("Now drawing from 'AC Power'\n")     # 台式机：没有电池行
    # ⚠️ 变异验证：`m.group(2)` 写成 `m.group(1)`（状态词变成数字）→ 前两条红。
    check("⭐ pmset(AC)：power=AC / 100% / charged",
          (_ac["power"], _ac["battery_pct"], _ac["battery_state"])
          == ("AC", 100, "charged"), str(_ac))
    # ⚠️ 变异验证：power 映射写成恒 "AC" → 这条红。
    check("⭐⭐ pmset(电池)：power=battery / 83% / discharging（别把两种供电都写成 AC）",
          (_bat["power"], _bat["battery_pct"], _bat["battery_state"])
          == ("battery", 83, "discharging"), str(_bat))
    # ⚠️ 变异验证：`battery_pct` 初值写成 0 → 这条红（"没电池"与"电量 0%"是两件事）。
    check("⭐⭐ pmset(台式机)：没有电池行 → 电量/状态 = None（**不是 0**）",
          (_desk["power"], _desk["battery_pct"], _desk["battery_state"])
          == ("AC", None, None), str(_desk))

    # ② 磁盘：fake `disk_usage`（不碰真盘）
    class _U22:
        free, total = 123 * 10**9, 500 * 10**9
    # ⚠️ 变异验证：free/total 对调 → 这条红。
    _dsk = tm._disk(usage=lambda _p: _U22())
    check("⭐ `_disk`：free/total 按 GB 报（fake disk_usage）",
          (_dsk["free_gb"], _dsk["total_gb"]) == (123.0, 500.0), str(_dsk))

    # ③ 体积扫描：真目录树（tmp）+ 顶层前 5 大 + 预算
    _d22 = pathlib.Path(_tf.mkdtemp(prefix="cl_tm_dev_"))
    try:
        def _mk22(p, mb):
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"x" * int(mb * 1e6))
        _sess22 = _d22 / "sessions"
        _mk22(_sess22 / "a.md", 1.0)
        _mk22(_sess22 / "b.md", 0.5)
        _cl22 = _d22 / ".classlive"
        _mk22(_cl22 / "eval" / "big.bin", 2.0)
        _mk22(_cl22 / "eval" / "big2.bin", 1.0)
        _mk22(_cl22 / "models" / "m.bin", 0.4)
        _mk22(_cl22 / "direct.json", 0.2)
        for _i in range(4):                    # 4 个小目录 → 顶层共 6 个子项
            _mk22(_cl22 / f"small{_i}" / "x", 0.11 + _i * 0.01)
        # ⚠️ 变异验证：`kids[:5]` 改 `[:3]` → 第一条红；`kids.sort` 删掉 → 两条都红
        #    （scandir 顺序不是大小序）。
        _fp = tm.footprint(sessions_dir=_sess22, classlive_dir=_cl22)
        check("⭐⭐ `footprint`：三个体积 + 顶层前 5 大（降序、名字对）",
              (_fp["sessions_mb"], _fp["classlive_mb"], _fp["partial"])
              == (1.5, 4.1, False)
              and [k["name"] for k in _fp["classlive_top"]]
              == ["eval", "models", "direct.json", "small3", "small2"],
              str(_fp))
        check("⭐ `footprint`：每个子项带 MB（最大的 eval = 3.0）",
              bool(_fp["classlive_top"]) and _fp["classlive_top"][0]["mb"] == 3.0,
              str(_fp["classlive_top"]))
        # ⚠️ 变异验证：把 deadline 写成 `float("inf")`（等于没有预算）→ 这条红。
        _fp0 = tm.footprint(sessions_dir=_sess22, classlive_dir=_cl22, budget_s=0.0)
        check("⭐⭐ 预算用完 → `partial: true`（停下，不硬走完）",
              _fp0["partial"] is True, str(_fp0))

        # ④ 所有采集器都炸 → 每个值 null，块照样落进报告
        _patched = {}

        def _boom22(*_a, **_kw):
            raise RuntimeError("probe down")
        for _nm in ("_disk", "_power_full", "_machine", "_displays", "audio_info",
                    "net_type", "_locale", "footprint"):
            _patched[_nm] = getattr(tm, _nm)
            setattr(tm, _nm, _boom22)
        try:
            try:
                _st22 = tm.device_start(src=None)
            except Exception:                  # 契约是"不抛"——抛了也要能报红而不是炸掉本文件
                _st22 = None
            _t22 = tm.TestSession(_d22 / "dev.md", wall_clock=time.time)
            _t22.note_device_start(src=None)
            _t22.note_device_tick()
            _t22.note_block("device", _t22.device_block())
            _rp22 = _t22.finish(bundle=False)
            _rep22 = (_json.loads(pathlib.Path(_rp22).read_text(encoding="utf-8"))
                      if _rp22 else {})
        finally:
            for _nm, _fn in _patched.items():
                setattr(tm, _nm, _fn)
        # ⚠️ 变异验证：`_guard` 改成直接调用（去掉每个值自己的兜底）→ 这条红
        #    （device_start 会抛 → `_st22` 是 None，而不是"七个 null"）。
        check("⭐⭐ 每个采集器都炸：`device_start` 七个值全是 None（一个都不带走）",
              isinstance(_st22, dict)
              and set(_st22) == {"disk", "power", "machine", "displays", "audio",
                                 "net", "locale"}
              and all(v is None for v in _st22.values()), str(_st22))
        _dev_blk = (_rep22.get("blocks") or {}).get("device") or {}
        # ⚠️ 变异验证：从 `BLOCKS` 里删掉 "device" → 这条红（bad_block + 块缺席）。
        check("⭐⭐ 全炸也要落盘：`blocks.device` 在、start/end 是 null、没有 bad_block",
              "device" in (_rep22.get("blocks") or {})
              and (_dev_blk.get("start") or {}).get("disk") is None
              and (_dev_blk.get("end") or {}).get("disk") is None
              and (_dev_blk.get("end") or {}).get("footprint") is None
              and not [e for e in _rep22.get("events", [])
                       if e.get("kind") == "bad_block"],
              str(_dev_blk)[:160])

        # ⑤ ⏱ tick：闸门（没到 10 分钟不采）+ 到点采一份 + **不碰子进程**
        _t23 = tm.TestSession(_d22 / "tick.md", wall_clock=time.time)
        _t23.note_device_start(src=None)
        _t23.note_device_tick()                # 刚开课：还没到 10 分钟
        _gated = len(_t23._device["samples"])
        _t23._device_next = 0.0                # 把闸门打开（= 10 分钟到了）
        _sub_calls = []
        _orig_run = tm.subprocess.run

        def _no_sub22(*a, **kw):
            _sub_calls.append(a)
            raise AssertionError("⏱ 样本里出现了子进程调用")
        tm.subprocess.run = _no_sub22
        try:
            _t23.note_device_tick()
            _t23.note_device_tick()            # 刚采过 → 闸门重新关上
        finally:
            tm.subprocess.run = _orig_run
        _smps = _t23._device["samples"]
        # ⚠️ 变异验证：删掉 `if now < self._device_next: return` → 这条红。
        check("⭐⭐ `note_device_tick`：没到 10 分钟一份都不采（开课那次不算）",
              _gated == 0, f"gated={_gated}")
        # ⚠️ 变异验证：删掉 `self._device_next = now + DEVICE_SAMPLE_S`（不重新上闸）→ 这条红。
        check("⭐⭐ 到点采一份、且立刻重新上闸（同一分钟内不会连采）",
              len(_smps) == 1, f"samples={_smps}")
        # ⚠️ 变异验证：`device_cheap` 改成调 `_power_full()`（会起子进程）→ 这条红。
        check("⭐⭐ ⏱ 样本**不碰子进程**（子进程只在 start/end 允许）",
              not _sub_calls, f"subprocess calls={_sub_calls}")
        # ⚠️ 变异验证：`s["t_s"] = …` 那行删掉 → 这条红。
        check("⭐ 样本形状：t_s + 三个便宜值",
              bool(_smps) and set(_smps[0]) == {"t_s", "low_power_mode",
                                                "thermal", "net"},
              str(_smps[:1]))
    finally:
        _sh.rmtree(_d22, ignore_errors=True)

    print("\n--- T23 ⭐⭐ 3.10：段一转完就转（后台工人）+ wav 只在验过后删 + journal ---")
    # ⚠️ 这一节：合成 wav（真 `wave` 模块写）/ 真 soundfile 兜底 / fake 工人。
    #    不碰真 sessions/、不联网、不起 ffmpeg（PATH 掐掉 —— 逼它走兜底那条）。
    import json as _json2
    import threading as _th
    import wave as _wave

    _d23 = pathlib.Path(_tf.mkdtemp(prefix="cl_tm_tc_"))
    try:
        def _mk_wav(p, secs=0.5):
            with _wave.open(str(p), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(tm.SR)
                w.writeframes(b"\0\0" * int(tm.SR * secs))

        # ① `_opus_ok`：**头不对就不算数**（原来只看 size>0 —— 半截文件照样骗过）
        _junk = _d23 / "junk.opus"
        _junk.write_bytes(b"NOTOGG" * 100)
        _empty = _d23 / "empty.opus"
        _empty.write_bytes(b"")
        _good = _d23 / "good.opus"
        _good.write_bytes(b"OggS" + b"\0" * 20 + b"OpusHead" + b"\0" * 20)
        # ⚠️ 变异验证：`_opus_ok` 改回 `exists() and size>0` → 这条红。
        check("⭐⭐ `_opus_ok`：非空但**不是 Ogg/Opus** 的不算数（半截文件不许冒充成品）",
              tm._opus_ok(_good) and not tm._opus_ok(_junk) and not tm._opus_ok(_empty),
              f"good={tm._opus_ok(_good)} junk={tm._opus_ok(_junk)}")
        check("⚠️ 不存在的文件 -> False（不抛）",
              tm._opus_ok(_d23 / "nope.opus") is False)

        # ② `transcode_one`：真 soundfile 兜底（PATH 里没有 ffmpeg）
        _w1 = _d23 / "seg1.wav"
        _mk_wav(_w1)
        _saved_path = os.environ.get("PATH", "")
        os.environ["PATH"] = str(_d23)            # 掐掉 ffmpeg -> 只能走兜底
        try:
            _o1, _why1 = tm.transcode_one(_w1)
        finally:
            os.environ["PATH"] = _saved_path
        check("⭐⭐ `transcode_one`：ffmpeg 不在 -> soundfile 兜底，opus 真产出",
              _o1 is not None and tm._opus_ok(_o1) and _why1.startswith("ffmpeg"),
              f"{_o1} {_why1[:60]}")
        # ⚠️ 变异验证：把 `wav.unlink` 挪到 `_opus_ok` 判断之前 → 这条红（wav 没了）。
        check("⭐⭐ **wav 验过 opus 才删**（兜底成功 -> wav 走人）", not _w1.exists())
        _w2 = _d23 / "seg2.wav"
        _mk_wav(_w2)
        os.environ["PATH"] = str(_d23)
        try:
            _o2, _ = tm.transcode_one(_w2, delete=False)
        finally:
            os.environ["PATH"] = _saved_path
        check("⭐⭐ `delete=False`（后台工人那一档）：**不删 wav**（删除留给收尾对账）",
              _o2 is not None and _w2.exists(), f"opus={_o2} wav={_w2.exists()}")
        _badw = _d23 / "bad.wav"
        _badw.write_bytes(b"this is not a wav" * 50)
        _o3, _why3 = tm.transcode_one(_badw)
        check("⭐⭐ 转不成 -> `(None, 原因)`，**wav 留着**、不留半截 opus",
              _o3 is None and _badw.exists() and not (_d23 / "bad.opus").exists(),
              _why3[:80])

        # ③ `_TranscodeWorker`：**一个线程、按顺序**、结果记账
        _order, _state = [], {"live": 0, "max": 0}
        _lk23 = _th.Lock()

        def _fake_tc(wav, opus=None, *, delete=True):
            with _lk23:
                _order.append(pathlib.Path(wav).name)
                _state["live"] += 1
                _state["max"] = max(_state["max"], _state["live"])
            time.sleep(0.05)                     # 给"并行"留出暴露窗口
            o = pathlib.Path(wav).with_suffix(".opus")
            o.write_bytes(b"OggS" + b"\0" * 8 + b"OpusHead" + b"\0" * 8)
            with _lk23:
                _state["live"] -= 1
            return o, ""

        _wk = tm._TranscodeWorker(transcode=_fake_tc)
        _wavs23 = []
        for _i in (1, 2, 3):
            _p = _d23 / f"w{_i}.wav"
            _mk_wav(_p)
            _wavs23.append(_p)
        for _p in _wavs23:
            _wk.submit(_p)
        _wk.drain(10.0)
        # ⚠️ 变异验证：把 `_run` 改成开线程池并行 -> 这条红（上课期间跟 ASR 抢核）。
        check("⭐⭐ 后台工人：**一次只转一段**（并行会把字幕拖慢）",
              _state["max"] == 1, f"max={_state['max']}")
        check("⭐⭐ 按提交顺序、每段都转完（`finished` 记账）",
              _order == ["w1.wav", "w2.wav", "w3.wav"]
              and [p.name for p in _wk.finished()] == ["w1.opus", "w2.opus", "w3.opus"],
              f"order={_order} done={[p.name for p in _wk.finished()]}")
        check("⭐ 排空后 `unfinished()` 为空（收尾不用跳开谁）", _wk.unfinished() == [])
        _wk.stop()

        # ④ 段一转完就喂工人（`_rotate_wav`）—— 收尾只剩最后一段
        _sub = []
        _fake_w = type("_W", (), {
            "submit": lambda self, p: _sub.append(p),
            "drain": lambda self, t=0: None,
            "finished": lambda self: [],
            "unfinished": lambda self: [],
            "stop": lambda self: None})()
        _t23b = tm.TestSession(_d23 / "rot.md", record_audio=True)
        _t23b._transcode = _fake_w
        _t23b._rotate_wav()                        # 第 1 段关掉 -> 喂
        _t23b._rotate_wav()                        # 第 2 段关掉 -> 喂
        # ⚠️ 变异验证：把 `_rotate_wav` 里那两行 submit 删掉 -> 这条红（收尾又转全部）。
        check("⭐⭐ `_rotate_wav`：关掉的段**当场**交给后台工人",
              [p.name for p in _sub] == ["rot.001.wav", "rot.002.wav"], str(_sub))

        # ⑤ journal：开录就写下"还欠哪几步"；收尾逐步更新；**上传排完才撤**
        _mk23 = _d23 / ("rot" + tm.upload_mod.PENDING_TAIL)
        _j = _json2.loads(_mk23.read_text(encoding="utf-8"))
        check("⭐⭐ 开录就写下 journal：还欠哪四步（转码 / 报告 / 上传 / 笔记）",
              _j.get("steps") == {"transcode": "pending", "report": "pending",
                                  "upload": "pending", "note": "pending"}
              and _j.get("pid"),
              str(_j.get("steps")))

        _t23c = tm.TestSession(_d23 / "fin.md", record_audio=True)
        _t23c._wav.writeframes(b"\0\0" * (tm.SR // 2))    # 第一段写点真样本
        _t23c._rotate_wav()                               # 001 交给**真**工人（后台）
        _t23c._wav.writeframes(b"\0\0" * (tm.SR // 2))    # 最后一段
        # ⚠️ 判据是在 check.py 里跑的，而它设 `CLASSLIVE_UPLOAD=0`（判据不许真上传）——
        #    这一节要的是"上传**开着**"那半（收尾才留着 journal 等排上传）。
        _saved_up = os.environ.get("CLASSLIVE_UPLOAD")
        os.environ["CLASSLIVE_UPLOAD"] = "1"
        os.environ["PATH"] = str(_d23)                    # 逼转码走 soundfile
        try:
            _t23c.finish(bundle=False)
        finally:
            os.environ["PATH"] = _saved_path
            if _saved_up is None:
                os.environ.pop("CLASSLIVE_UPLOAD", None)
            else:
                os.environ["CLASSLIVE_UPLOAD"] = _saved_up
        _af = {p.name for p in _t23c.audio_files}
        check("⭐⭐ 收尾对账：后台转好的 + 最后一段，两段都成了 opus",
              _af == {"fin.001.opus", "fin.002.opus"}, str(sorted(_af)))
        check("⭐⭐ **wav 只在拿到验过的 opus 之后才删**（两段 wav 都没了）",
              not (_d23 / "fin.001.wav").exists()
              and not (_d23 / "fin.002.wav").exists(),
              str([p.name for p in _d23.glob("fin.*")]))
        _j2 = {}
        try:
            _j2 = _json2.loads((_d23 / ("fin" + tm.upload_mod.PENDING_TAIL))
                               .read_text(encoding="utf-8"))
        except Exception:                             # 标记被提前撤了 / 读不了 -> 下面那条红
            _j2 = {}
        # ⚠️ 变异验证：`finish` 里 `_journal(report="done")` 删掉 -> 这条红。
        check("⭐⭐ 收尾把 journal 更新到「转码+报告 done、上传+笔记还欠着」（标记**还在**）",
              _j2.get("steps") == {"transcode": "done", "report": "done",
                                   "upload": "pending", "note": "pending"},
              str(_j2.get("steps")))
        # ⚠️ 变异验证：`journal_upload_done` 里不置 `_pending_marker = None` -> 第二次调
        #    会**把标记重新写出来**（下次启动又"恢复"一节收过尾的课）-> 这条红。
        _t23c.journal_upload_done()
        _t23c.journal_upload_done()                # 幂等：不许复活
        check("⭐⭐ 上传排完 -> 撤掉 `.pending.json`；再调一次也**不许复活**",
              not (_d23 / ("fin" + tm.upload_mod.PENDING_TAIL)).exists()
              and _t23c._pending_marker is None,
              str([p.name for p in _d23.glob("fin.*")]))
        # ⭐⭐ 2026-10-18 审查 D27：笔记还欠着（这一节 `note` 仍是 pending）-> journal
        #    必须**改名留住**（`.recovered.json`），不然补笔记那边永远扫不到。
        #    ⚠️ 不留 `.pending.json`：那会让 `recover_pending` 把已排过上传的这节重复入队。
        check("⭐⭐ D27 笔记没写成 -> 改名为 `.recovered.json` 留作残局（不是删掉）",
              (_d23 / "fin.recovered.json").exists()
              and _json2.loads((_d23 / "fin.recovered.json")
                               .read_text(encoding="utf-8")
                               ).get("steps", {}).get("note") == "pending",
              str([p.name for p in _d23.glob("fin.*")]))
    finally:
        _sh.rmtree(_d23, ignore_errors=True)

    print("\n--- T24 ⭐⭐ 3.10：课中临时报告快照（provisional）+ 笔记那一步 ---")
    # ⚠️ 语料：真 TestSession（temp 目录）+ 合成段；快照线程真起（落盘走真原子写）。
    _d24 = pathlib.Path(_tf.mkdtemp(prefix="cl_tm_snap_"))
    # ⚠️ 判据在 check.py 里跑（它设 `CLASSLIVE_UPLOAD=0`）—— 这一节要的是"上传开着"
    #    那半（journal 才活到 `finish()` 之后，见 ⑤）。整节先打开、完事还原。
    _saved_up24 = os.environ.get("CLASSLIVE_UPLOAD")
    os.environ["CLASSLIVE_UPLOAD"] = "1"
    try:
        _t24 = tm.TestSession(_d24 / "2026-10-18_090000_SNAP.md", record_audio=True)
        for _i in range(12):
            _t24.note_segment(np.zeros(tm.SR, dtype=np.float32), text=f"sentence {_i}",
                              asr_ms=100.0 + _i, model="qwen", logprob=-0.1)
        _t24.note_ui("submit")
        _t24._merge_items.append({"tail_ts": "10:00:01", "next_ts": "10:00:05"})
        # ① 快照的载荷：标 provisional + snapshot_at；正式那份**不许**带这两个键
        #    ⚠️ 顺序要紧：**先**建快照（看它不碰共享状态），再建正式那份。
        _p24 = _t24.report_payload(provisional=True)
        # ⚠️⚠️ 共享状态：快照**不许**把派生格 setdefault 进 `_extra` —— 不然正式
        #    报告会拿到 10 分钟前的旧 items（merge/backfill 少最后一段）。
        #    ⚠️ 变异验证：把 provisional 那条分支改成直接 `self._extra.setdefault(...)`
        #    -> 这条红（`_extra` 被快照写脏）。
        check("⭐⭐ 快照**不碰共享状态**（`_extra` 里没被塞派生格）",
              "merge" not in _t24._extra and "ui" not in _t24._extra,
              str(sorted(_t24._extra)))
        _t24._merge_items.append({"tail_ts": "10:00:09", "next_ts": "10:00:12"})
        _f24 = _t24.report_payload()
        # ⚠️ 变异验证：把 `if provisional:` 那两行删掉 -> 这条红。
        check("⭐⭐ 快照载荷标 `provisional` + `snapshot_at`；正式那份不带",
              _p24.get("provisional") is True and _p24.get("snapshot_at")
              and "provisional" not in _f24 and "snapshot_at" not in _f24,
              f"provisional={_p24.get('provisional')} final={_f24.get('provisional')}")
        check("⭐⭐ 快照里看得到**当前**的 items（1 条）；正式那份是**全部**（2 条）",
              len(_p24["blocks"]["merge"]["items"]) == 1
              and len(_f24["blocks"]["merge"]["items"]) == 2,
              f"snap={len(_p24['blocks']['merge']['items'])} "
              f"final={len(_f24['blocks']['merge']['items'])}")

        # ② 真走一遍快照线程：文件 + 账
        _rep24 = _d24 / "2026-10-18_090000_SNAP.report.json"
        _t24._snapshot_report()
        _th24 = _t24._snap_thread
        if _th24 is not None:
            _th24.join(timeout=10)
        _got24 = _json2.loads(_rep24.read_text(encoding="utf-8"))
        _st24 = _t24._snap_stats()
        # ⚠️ 变异验证：把 `_atomic_json` 换成直接 `open(..., "w")` -> 留下 `.tmp` 的那条红。
        check("⭐⭐ 快照真落盘（原子：不留 `.tmp`）+ 账记下来了",
              _got24.get("provisional") is True and _st24["n"] == 1
              and _st24["last_ms"] is not None
              and _st24["last_ms"] <= tm.PROVISIONAL_MAX_MS
              and _st24["last_bytes"] > 0
              and not list(_d24.glob("*.tmp")),
              f"{_st24} files={[p.name for p in _d24.glob('*.tmp')]}")

        # ③ 超预算 -> **不再采**（这次也不写）：把预算临时压到 -1 就是"一定超"
        _saved_max = tm.PROVISIONAL_MAX_MS
        tm.PROVISIONAL_MAX_MS = -1.0
        try:
            _rep24.unlink()                                # 清掉上一份，看它还写不写
            _t24._snap_thread = None
            _t24._snapshot_report()
            _th24b = _t24._snap_thread
            if _th24b is not None:
                _th24b.join(timeout=10)
        finally:
            tm.PROVISIONAL_MAX_MS = _saved_max
        # ⚠️ 变异验证：把 `if ms > PROVISIONAL_MAX_MS:` 那段删掉 -> 这条红。
        check("⭐⭐ 超过预算 -> **不写、以后也不采**（记一条 event 说明为什么）",
              not _rep24.exists() and _t24._snap_off
              and any(e.get("kind") == "provisional_skipped" for e in _t24.events),
              f"off={_t24._snap_off!r} exists={_rep24.exists()}")

        # ④ 收尾：正式的赢（快照在 `finish` 之后不许再写），且报告里带上快照的账
        _t24._snap_off = ""                                # 放它再采一次
        _t24._snap_thread = None
        _t24._snapshot_report()
        _t24.finish(bundle=False)
        _fin24 = _json2.loads(_rep24.read_text(encoding="utf-8"))
        _th24c = _t24._snap_thread
        if _th24c is not None:
            _th24c.join(timeout=10)
        # ⚠️ 变异验证：`finish` 里去掉 `self._finishing = True`（快照还能写）-> 这条红
        #    （取决于时序；`_finish_snapshots` 的 join 让它在真实节奏下稳定）。
        check("⭐⭐ 收尾之后盘上是**正式那份**（不带 provisional）",
              "provisional" not in _fin24 and _fin24.get("segments"),
              f"provisional={'provisional' in _fin24}")
        check("⭐ 快照的账进了正式报告（`provisional_snapshots`）",
              (_fin24.get("provisional_snapshots") or {}).get("n", 0) >= 1,
              str(_fin24.get("provisional_snapshots")))

        # ⑥ `_finishing` 一置位，快照线程**就不许写**（哪怕它已经跑到写那一步）
        _rep24.unlink(missing_ok=True)
        _t24._finishing = True
        _t24._snapshot_worker()                            # 直接调线程体（确定，不赌时序）
        # ⚠️ 变异验证：把 `if self._finishing: return` 删掉 -> 这条红。
        check("⭐⭐ `_finishing` 之后快照**不许再写**（正式的赢到底）",
              not _rep24.exists(), f"exists={_rep24.exists()}")
        _t24._finishing = False

        # ⑤ 笔记那一步：`finish(note_path=…)` 只在**文件真在**时记 done
        #    ⚠️ 判据在 check.py 里跑（它设 `CLASSLIVE_UPLOAD=0`）—— 这里要的是
        #    "上传开着"那半（journal 才活到 finish 之后）。
        _saved_up24 = os.environ.get("CLASSLIVE_UPLOAD")
        os.environ["CLASSLIVE_UPLOAD"] = "1"
        try:
            _mk24 = _d24 / ("2026-10-18_090000_SNAP" + tm.upload_mod.PENDING_TAIL)
            _j24 = _json2.loads(_mk24.read_text(encoding="utf-8"))
            check("⭐ 收尾后 journal 里 `note` 还是 pending（这次没传 note_path）",
                  (_j24.get("steps") or {}).get("note") == "pending",
                  str(_j24.get("steps")))
            _note24 = _d24 / "2026-10-18_SNAPX.md"
            _note24.write_text("note", encoding="utf-8")
            _t24b = tm.TestSession(_d24 / "2026-10-18_091000_SNAP2.md", record_audio=True)
            _t24b.finish(bundle=False, note_path=str(_note24))
            _j24b = _json2.loads((_d24 / ("2026-10-18_091000_SNAP2"
                                          + tm.upload_mod.PENDING_TAIL))
                                 .read_text(encoding="utf-8"))
            # ⚠️ 变异验证：把 `if pathlib.Path(_np).is_file():` 那半删掉 -> 这条红
            #    （笔记没写成也记 done -> 补传那边永远不补）。
            check("⭐⭐ 笔记写过了 -> `note: done` + `note_file` 记下落点",
                  (_j24b.get("steps") or {}).get("note") == "done"
                  and _j24b.get("note_file") == str(_note24),
                  f"{_j24b.get('steps')} note_file={_j24b.get('note_file')!r}")
            _t24c = tm.TestSession(_d24 / "2026-10-18_092000_SNAP3.md", record_audio=True)
            _t24c.finish(bundle=False, note_path=str(_d24 / "not-written.md"))
            _j24c = _json2.loads((_d24 / ("2026-10-18_092000_SNAP3"
                                          + tm.upload_mod.PENDING_TAIL))
                                 .read_text(encoding="utf-8"))
            check("⭐⭐ 落点给了但**文件不在** -> 仍是 pending（补传那边要能看出来）",
                  (_j24c.get("steps") or {}).get("note") == "pending"
                  and _j24c.get("note_file"),
                  f"{_j24c.get('steps')} note_file={_j24c.get('note_file')!r}")
        finally:
            if _saved_up24 is None:
                os.environ.pop("CLASSLIVE_UPLOAD", None)
            else:
                os.environ["CLASSLIVE_UPLOAD"] = _saved_up24
    finally:
        _sh.rmtree(_d24, ignore_errors=True)
        if _saved_up24 is None:
            os.environ.pop("CLASSLIVE_UPLOAD", None)
        else:
            os.environ["CLASSLIVE_UPLOAD"] = _saved_up24

    bad = [n for n, ok, _ in RESULTS if not ok]
    print("\n" + "=" * 60)
    print(f"{len(RESULTS) - len(bad)}/{len(RESULTS)} 通过")
    for n in bad:
        print(f"  ❌ {n}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
