#!/usr/bin/env python3
"""`scripts/stream_eval.py` 的装配判据 —— 「定稿引擎」那层适配。

    ClassLive.app/Contents/MacOS/python tests/test_stream_eval.py

## ⚠️ 本文件钉住一个**已复现**的真缺陷（2026-10-12 全仓 OCR 审查）

`load_final_engine("whisper", …)` 原来**直接返回 `FinalASR`**，而 `FinalASR.transcribe()`
返的是 `FinalResult`（`final_asr.py:295`）—— **不是 `str`**。
`replay.on_end` 拿它当定稿文本存进 `u["final"]`，收尾 `evaluate()` 里
`" ".join(u["final"] …)` 就 `TypeError: expected str instance, FinalResult found`。

⚠️ 而 **`--final` 的默认值就是 `whisper`**（`scripts/real_class_slices.sh` 也不覆盖）
→ **默认那条路必崩**，且回放要跑完好几分钟才在收尾处炸，量具白跑一趟。
旁边的 `cloud` 分支早就用一层适配做了 `r.text` 提取 —— 缺的只是把 `whisper` 也走同一层。

## 为什么能测（不加载真模型）

`load_final_engine` 里那句 `final_asr.load_final(...)` 是**唯一**碰模型的地方，
把它换成桩就够了 —— 剩下的适配层是真代码。⚠️ 这也顺带钉住了
「`whisper` 分支必须调 `load_final`（那层容错：Whisper 缺失会退回 Parakeet 并说一句）」。
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  {detail}" if detail else ""))


def _load_stream_eval():
    """按路径加载 `scripts/stream_eval.py`（它不在包路径上，且**不 import main**）。"""
    p = ROOT / "scripts" / "stream_eval.py"
    spec = importlib.util.spec_from_file_location("stream_eval_under_test", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    import final_asr
    from final_asr import FinalResult

    SE = _load_stream_eval()
    calls: list = []

    def _fake_load_final(model_dir, draft_asr, allow_cloud, **kw):
        calls.append({"model_dir": model_dir,
                      "allow_cloud": bool(allow_cloud()) if callable(allow_cloud) else None})
        calls[-1].update(kw)

        class _FakeASR:
            def transcribe(self, buf):
                # ⭐ 生产形状：返 FinalResult，**不是 str**
                return FinalResult("定稿文本", "local-whisper", None, 1.0, False,
                                   ("local:whisper",))
        return _FakeASR()

    orig = final_asr.load_final
    final_asr.load_final = _fake_load_final
    try:
        got = SE.load_final_engine("whisper", "/tmp/不存在的模型目录", draft_asr=None)
    finally:
        final_asr.load_final = orig

    check("⭐ whisper 分支确实走了 `final_asr.load_final`（那层容错不能自己重写）",
          len(calls) == 1 and calls[0]["model_dir"] == "/tmp/不存在的模型目录", str(calls))
    check("⭐ whisper 分支给的是 `allow_cloud=lambda: False`（回放不许偷偷上云）",
          calls and calls[0]["allow_cloud"] is False, str(calls[:1]))

    r = got.transcribe(None)
    # 改坏：把 `whisper` 分支改回 `return final_asr.load_final(...)`（不走 `_text_only`）-> 这两条红。
    check("⭐⭐ `transcribe()` 返回的是 **str**（不是 `FinalResult`）",
          isinstance(r, str), f"{type(r).__name__}: {r!r}")

    try:
        joined = " ".join([r])
        join_ok = True
    except TypeError as e:
        joined, join_ok = f"TypeError: {e}", False
    check("⭐⭐ `\" \".join(...)` 不炸 —— `evaluate()` 里逐字就是这么用的",
          join_ok, joined)
    check("⭐ 顺手记下 `last_source`（事后要能回答「这段走的是哪条路」）",
          getattr(got, "last_source", None) == "local-whisper",
          repr(getattr(got, "last_source", None)))
    check("⭐ `last_fell_back` 也在（用生产现成的字段，别拿 source 字符串猜）",
          getattr(got, "last_fell_back", None) is False)

    # ⚠️ `parakeet` 分支是**直通**（返草稿 ASR 本身），别顺手也包一层 ——
    #    它的 `transcribe` 返的就是 str，包了反而多一层。
    sentinel = object()
    check("⭐ `parakeet` 分支保持直通（不额外包）",
          SE.load_final_engine("parakeet", "/x", draft_asr=sentinel) is sentinel)

    # ── ⭐ 云端那两条判据（2026-10-12 全仓审查核实，两条都是实测过的错数）──
    # ⚠️ 「这段的文本是谁给的」与「这段发没发过请求」是**两个问题**，
    #    都得从生产记的字段读（`final_source` / `steps`），不能拿字符串前缀猜。
    class _Stub:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    for _src, _want in (("groq", True), ("qwen", True), ("cloud", True),
                        ("local-whisper", False), ("local-parakeet", False),
                        ("none", False), (None, False), ("", False)):
        _r = SE._is_cloud_source(_src)
        check(f"⭐ cloud 来自判据：{_src!r} -> {_want}", _r is _want, repr(_r))

    # ⚠️ 默认那条路（`--final whisper` → `allow_cloud=False`）每段都是
    #    `local-whisper` 且 `fell_back=False`。旧判据（有 source 且没 fell_back）
    #    会把**整节课**算成云端 —— 报告里的"云端占比"正好反了。
    _whisper_utts = [{"dur": 10.0, "start_t": 0.0, "end_t": 10.0, "final": "hi",
                      "final_compute_s": 1.0, "drafts": [],
                      "final_source": "local-whisper", "final_fell_back": False}]
    _ev = SE.evaluate(_whisper_utts, None)
    check("⭐⭐ `--final whisper`（默认）不许被算成云端",
          _ev["cloud_segments"] == 0 and _ev["cloud_audio_s"] == 0.0,
          f"{_ev['cloud_segments']} 段 / {_ev['cloud_audio_s']} s")
    _cloud_utts = [dict(_whisper_utts[0], final_source="groq"),
                   dict(_whisper_utts[0], final_source="local-parakeet",
                        final_fell_back=True)]
    _ev2 = SE.evaluate(_cloud_utts, None)
    check("⭐ 真走云端的算 1 段；回落本地的不算",
          _ev2["cloud_segments"] == 1 and _ev2["cloud_audio_s"] == 10.0, str(_ev2["cloud_segments"]))

    # ⚠️ 节流判据：**真发了请求但回落到本地**那类必须算（旧判据按 `last_source` 判 → 漏掉，
    #    于是限流时欠节流、把 429 放大成级联熔断）。
    _paced = [("cloud:timeout", "local:parakeet"), ("cloud:http-429", "local:whisper"),
              ("cloud:groq",), ("cloud:qwen", "local:parakeet")]
    for _steps in _paced:
        check(f"⭐ 发过请求（{_steps[0]}）-> 照节流", SE._sent_request(_Stub(last_steps=_steps)) is True)
    for _steps in (("off", "local:whisper"), ("breaker", "local:parakeet"),
                   ("encode", "local:whisper"), ("local:whisper",)):
        check(f"⭐ 一次网络都没发（{_steps[0]}）-> 不空等", SE._sent_request(_Stub(last_steps=_steps)) is False)
    check("⭐ 没有 `last_steps`（parakeet 直通）-> 不当成发过",
          SE._sent_request(_Stub()) is False)

    # ── ⭐⭐ 节流真的「请求到请求」≥ pace_s（2026-10-17 模拟课堂 A/B 抓到：原来只隔一次生效，
    #    相邻间隔 0.0 / 3.36 s 交替，有效速率 ≈ 2×）。真 `replay` + 桩 Segmenter（每块都收一段）。
    #    改坏：把 `wait` 改回拿上一趟时刻算（`pace_s - (t_req - last_req)`）-> 这条红。
    import types
    import numpy as np

    class _SegEveryChunk:
        def __init__(self, on_partial, on_end):
            self._end = on_end

        def accept(self, x):
            self._end(np.zeros(160, dtype=np.float32))

        def flush(self):
            pass

    class _PacedFinal:
        last_source, last_fell_back, last_steps = "groq", False, ("cloud:groq",)

        def __init__(self):
            self.times: list = []

        def transcribe(self, buf):
            import time as _t
            self.times.append(_t.monotonic())
            return "x"

    _real_vad = sys.modules.get("vad")
    sys.modules["vad"] = types.SimpleNamespace(Segmenter=_SegEveryChunk, time=None)
    try:
        _pf = _PacedFinal()
        SE.replay(np.zeros(1600 * 5, dtype=np.float32), _Stub(), _pf, pace_s=0.05)
    finally:
        if _real_vad is not None:
            sys.modules["vad"] = _real_vad
        else:
            sys.modules.pop("vad", None)
    _gaps = [b - a for a, b in zip(_pf.times, _pf.times[1:])]
    check("⭐⭐ 节流：相邻两趟请求的间隔每一次都 ≥ pace_s（不是隔一次）",
          len(_gaps) >= 3 and min(_gaps) >= 0.045, f"{[round(g, 3) for g in _gaps]}")

    bad = [n for n, ok, _ in RESULTS if not ok]
    print("\n" + "=" * 60)
    print(f"{len(RESULTS) - len(bad)}/{len(RESULTS)} 通过")
    for n in bad:
        print(f"  ❌ {n}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
