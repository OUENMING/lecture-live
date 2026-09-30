#!/usr/bin/env python3
"""测试模式（采集 / 录音 / 上传）的判据。

    ClassLive.app/Contents/MacOS/python tests/test_testmode.py

## 这个文件钉住了什么

按 `docs/PLAN-test-mode.md` §7 的判据表来。现在有的是：

1. ⭐⭐ **`PeakNormalizer` 的三个只读计数器真的会动**
   （`capped` / `clamped` / `peak_db` 直方图）—— 那是 `PLAN-audio-gain.md` **步 1**。
2. ⭐ **它们描述的是归一化「之前」的电平** —— 与 `testmode` 已有的
   `block_rms_dbfs_*`（量的是**之后**）是两组不同的数，别混。

⚠️ 语料全是合成的（`numpy` 造的块），不碰 `sessions/`、不联网、不起 AppKit。
⚠️ 每条断言先问「把实现改坏它会不会红」—— 见每条的注释。
"""
from __future__ import annotations

import pathlib
import sys

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

    print("\n--- T15 ⭐⚠️ 转码失败：**保留 wav**，别丢音频 ---")
    _d3 = pathlib.Path(tempfile.mkdtemp(prefix="cl_tm_fail_"))
    _old_ff = tm.subprocess.run
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
    finally:
        tm.subprocess.run = _old_ff
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

    bad = [n for n, ok, _ in RESULTS if not ok]
    print("\n" + "=" * 60)
    print(f"{len(RESULTS) - len(bad)}/{len(RESULTS)} 通过")
    for n in bad:
        print(f"  ❌ {n}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
