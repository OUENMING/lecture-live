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

    bad = [n for n, ok, _ in RESULTS if not ok]
    print("\n" + "=" * 60)
    print(f"{len(RESULTS) - len(bad)}/{len(RESULTS)} 通过")
    for n in bad:
        print(f"  ❌ {n}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
