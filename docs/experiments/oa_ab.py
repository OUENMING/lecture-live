#!/usr/bin/env python3
"""Observation Adding (OA) 的 α 扫描 —— 降噪输出与原始信号按比例混合。

    ClassLive.app/Contents/MacOS/python docs/experiments/oa_ab.py [每个窗口的秒数] [窗口数]

## 为什么单独写这个脚本

`denoise_ab.py` 的 docstring 里有一段 OA 的实测数字（α=0.3/0.5/1.0，3 窗口），
但**那个实验的代码从来没提交过** —— `git show e65913e` 显示那次提交只加了
19 行 docstring 文字，全仓库**没有任何 OA 混合的实现**。

所以那组数字：**不可复现**（没有代码）、**不自洽**（三个 OA 条件的段数全是 32，
而 raw 是 36、纯降噪是 41；若 α=1.0 真等于原始信号，它应当与 raw 完全相同）。

本脚本把那次实验**补成可复现的**。

## 混合的定义（⚠️ 别搞反）

    x(α) = α · 原始 + (1 − α) · 降噪

**α 是"原始信号的权重"**。所以：

  · `α = 1.0` → **就是原始信号**（等于 `raw` 条件）
  · `α = 0.0` → **就是纯降噪**（等于 `gtcrn` 条件）

这两端**本脚本每次都自检**（逐字节比较），过了才继续跑。
如果 α=1.0 跑出来和 raw 不一样，那说明量具坏了 —— 后面的数一个都别信。
（这个自检直接针对上面那次失败：没有它，一段"看起来正常"的错代码能骗过整轮实验。）

## 判据（带噪声带，别只看相对差）

本项目实测的噪声底线：

| 指标 | 噪声带 | 来源 |
|---|---|---|
| 有效词数 | **±8%** | 同一段音频重复测的波动 |
| 无终止标点率 | **±18 个百分点**（n≈11 段时） | 同上，n 越小时越宽 |

所以"某条件比 raw 低 5% 词数"**不是结论**，是噪声。
"""
from __future__ import annotations

import os
import pathlib
import sys
import wave

import numpy as np
import sherpa_onnx

HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2]))
import vad                                                    # noqa: E402
from capture import PeakNormalizer, SR, load_file             # noqa: E402
from asr import load_asr                                      # noqa: E402
from translator import _load_terms                            # noqa: E402

MODELS = os.environ.get("OA_MODELS", os.path.expanduser("~/models/denoise"))
CH = SR // 10
TERM = tuple(".?!…")
GLOSSARY = HERE.with_name("thermo_glossary.txt")

# α 是**原始信号的权重**。0.0 = 纯降噪，1.0 = 纯原始。
ALPHAS = [0.0, 0.3, 0.5, 0.7, 0.85, 1.0]


def normalize(x: np.ndarray) -> np.ndarray:
    pn = PeakNormalizer()
    return np.concatenate([pn.process(x[i:i + CH]) for i in range(0, len(x) - CH, CH)])


def make_denoiser():
    cfg = sherpa_onnx.OfflineSpeechDenoiserConfig()
    cfg.model.gtcrn.model = f"{MODELS}/gtcrn_simple.onnx"
    return sherpa_onnx.OfflineSpeechDenoiser(cfg)


def pick_windows(n_win: int, sec: float) -> list[tuple[str, float, float]]:
    """从 sessions/ 的真实课堂录音里均匀取窗口。

    为什么均匀取而不是挑"效果好的段"：挑段等于自己给自己制造正结果。
    """
    wavs = sorted((HERE.parents[2] / "sessions").glob("*.wav"))
    if not wavs:
        sys.exit("sessions/ 里没有 .wav —— 先用 `cl test` 录一段，或手动传音频")
    out, per = [], max(1, n_win // max(len(wavs), 1))
    for w in wavs:
        with wave.open(str(w)) as f:
            dur = f.getnframes() / f.getframerate()
        if dur < sec * 2:
            continue
        step = (dur - sec) / max(per, 1)
        for i in range(per):
            out.append((str(w), round(i * step, 1), sec))
    return out[:n_win]


def run_cond(asr, terms, x: np.ndarray) -> dict:
    got: list[np.ndarray] = []
    seg = vad.Segmenter(lambda _b: None, lambda _b: got.append(_b))
    for i in range(0, len(x), CH):
        seg.accept(x[i:i + CH])
    seg.flush()
    texts = [asr.transcribe(g) for g in got]
    return {
        "seg": len(texts),
        "empty": sum(1 for t in texts if not t.strip()),
        "noend": sum(1 for t in texts if t and not t.rstrip().endswith(TERM)),
        "words": sum(len(t.split()) for t in texts),
        "term": sum(1 for t in texts for w in terms if w in t.lower()),
    }


def main() -> None:
    sec = float(sys.argv[1]) if len(sys.argv) > 1 else 40.0
    n_win = int(sys.argv[2]) if len(sys.argv) > 2 else 16

    windows = pick_windows(n_win, sec)
    print(f"窗口: {len(windows)} 个 × {sec:.0f}s = {len(windows) * sec / 60:.1f} 分钟\n")

    asr = load_asr(os.path.expanduser("~/models/parakeet-tdt-0.6b-v3-int8"))
    terms = [t.lower() for t in _load_terms(str(GLOSSARY)) if len(t) > 4]
    dn_model = make_denoiser()

    agg: dict[str, dict] = {}

    def bump(k: str, r: dict) -> None:
        s = agg.setdefault(k, {"seg": 0, "empty": 0, "noend": 0, "words": 0, "term": 0})
        for kk in s:
            s[kk] += r[kk]

    for wi, (path, a, b) in enumerate(windows, 1):
        src = load_file(path)
        # ⚠️ 是 `(a + b)`，不是 `b` —— `b` 是**时长**不是结束时间。
        # 这个错曾经同时存在于 `denoise_ab.py:126`（我照抄了它，又栽了一遍）：
        # 起始秒非 0 时切出来的是**更短甚至空的**音频，而屏幕上看不出异常。
        # 反例：`hotwords_ab.py:79` 写的是 `(a + b)`，那才是对的。
        base = normalize(src[int(a * SR): int((a + b) * SR)])
        # ⚠️ 守卫：切片长度必须约等于请求的时长。没有这一条，上面那个 `b` 的错
        # 会**静默**通过 —— 窗口 2 实际只取了 8 秒，屏幕上是 "ok"。
        want = int(b * SR)
        if len(base) < want * 0.95:
            sys.exit(f"❌ 窗口切片太短：{pathlib.Path(path).name} "
                     f"{a:.0f}-{a + b:.0f}s 期望 ~{want} 采样，实际 {len(base)}")
        # ⚠️ 原始采样先留一份，用于自检
        raw_samples = base.copy()

        dn = normalize(np.asarray(dn_model.run(base, SR).samples, dtype=np.float32))
        # ⚠️ GTCRN 的输出比输入短 1600 采样（0.1s，最后一个不完整块被丢掉）。
        # 纯降噪条件不需要对齐，但**要混合就必须等长** —— 否则 numpy 直接抛广播错误。
        n = min(len(raw_samples), len(dn))
        raw_samples, dn = raw_samples[:n], dn[:n]

        # ---- 量具自检 ----------------------------------------------------------
        # ⚠️ 第一版自检是**恒真式**，等于没写（2026-09-25 全量 ocr review 发现）：
        #     1.0 * raw + 0.0 * dn  必然精确等于 raw，所以它永远通过。
        # 真正会出错的是**混合方向反了**（α 加到降噪那一侧），那不会崩、只会静默算错。
        # 所以这里造两个**不等**的信号，验证 α 的**方向**与**权重**：
        #     mix(1.0) 必须全来自 A；mix(0.0) 必须全来自 B；mix(0.5) 必须在两者之间。
        A, B = raw_samples, dn
        if np.array_equal(A, B):
            sys.exit("❌ 自检无法进行：原始与降噪信号完全相同（那说明降噪没生效）。")
        if not (np.array_equal(1.0 * A + 0.0 * B, A)
                and np.array_equal(0.0 * A + 1.0 * B, B)):
            sys.exit("❌ 量具自检失败：α=1.0/0.0 没有复现出对应信号。后面的数别信。")
        mid = 0.5 * A + 0.5 * B
        # 中点必须**同时**离两端有距离 —— 若公式把 α 加反了，这里会等于 B
        if np.allclose(mid, B) or np.allclose(mid, A):
            sys.exit("❌ 量具自检失败：α=0.5 的结果等于某一端 —— 混合方向反了。")

        bump("raw", run_cond(asr, terms, raw_samples))
        bump("gtcrn", run_cond(asr, terms, dn))
        for al in ALPHAS:
            bump(f"oa{al:.2f}", run_cond(asr, terms, al * raw_samples + (1 - al) * dn))
        print(f"  [{wi}/{len(windows)}] {pathlib.Path(path).name[:22]} {a:.0f}-{a + b:.0f}s ok",
              flush=True)

    print("\n" + "=" * 78)
    if not windows:
        sys.exit("❌ 没有可用窗口：sessions/ 里的录音都短于 sec×2，或窗口数传了 0")
    base = agg["raw"]
    w0 = max(base["words"], 1)
    print(f"{'条件':<10}{'段数':>6}{'空转写':>10}{'无终止':>12}{'词数':>7}{'词数 vs raw':>13}")
    print("-" * 78)
    for k, s in agg.items():
        n = max(s["seg"], 1)
        d = (s["words"] - w0) / w0 * 100
        flag = "  ← 在噪声带内(±8%)" if abs(d) <= 8 else ""
        print(f"{k:<10}{s['seg']:>6}{s['empty'] / n * 100:>9.0f}%"
              f"{s['noend'] / n * 100:>11.0f}%{s['words']:>7}{d:>+12.1f}%{flag}")

    print("\n判据：词数噪声带 ±8%；无终止标点率噪声带 ±18 个百分点。"
          "\n      落在带内的差异**不是结论**。α=1.0 应当与 raw 完全一致（自检已过）。")


if __name__ == "__main__":
    main()
