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

    bad = [n for n, ok, _ in RESULTS if not ok]
    print("\n" + "=" * 60)
    print(f"{len(RESULTS) - len(bad)}/{len(RESULTS)} 通过")
    for n in bad:
        print(f"  ❌ {n}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
