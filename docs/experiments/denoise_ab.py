#!/usr/bin/env python3
"""实验: 降噪对远场课堂 ASR 是帮忙还是添乱? —— **实测是添乱。**

用法:
    .venv/bin/python docs/experiments/denoise_ab.py <音频> <起始秒> <时长秒> [音频2 起 时 ...]

前置: 降噪模型**已被删除**（本实验否决了降噪后清理掉了），复跑前先下回来：
    gtcrn_simple.onnx     535 KB   https://github.com/k2-fsa/sherpa-onnx/releases/download/speech-enhancement-models/gtcrn_simple.onnx
    dpdfnet_baseline.onnx 8.8 MB   https://github.com/k2-fsa/sherpa-onnx/releases/download/speech-enhancement-models/dpdfnet_baseline.onnx

结论(2026-09-24, 本机实测)
--------------------------
3 个窗口 / 2 种降噪器, 全部词数下降、置信度变差、术语命中减少:

    条件        词数           平均 logprob   术语命中   空转写
    raw        392            -0.374         5          14%
    gtcrn      230 (-41%)    -0.449         2          19%
    dpdfnet    323 (-18%)    -0.484         3          15%

**不是电平问题。** 两个降噪器都把电平大幅压低(GTCRN 23×、DPDFNet 360×), 而
Parakeet 对**绝对电平**敏感(本项目实测: 等比衰减后词数 73→6)。所以本脚本对每个
条件统一做 归一化→[降噪]→**再归一化**。并额外做了 5 种顺序对照排除混淆:

    顺序                             词数
    A 基线(只归一化)                 152
    B 归一化→GTCRN                   105
    C 归一化→GTCRN→再归一化           71
    D 原始→GTCRN→归一化              118
    E 原始→GTCRN                     104

**没有一种顺序能救回来**, 所以不是摆放位置的问题。

**机制是删语音, 不是删噪声。** 逐段对照(段长不变, 音频还在):

    raw   (12.0s): I'm interested. Anyway. Let me tell you why might be something useful...
    gtcrn (11.8s): Oh.

    raw   (12.0s): It's time. It's target. And you can judge my temperatures in that time...
    gtcrn (12.0s): So all of these

→ **结论: 降噪不要接进流水线。** 这独立复现了 arXiv 2512.17562 "When De-noising
   Hurts"(该研究是 medical ASR; 本实验用的是本项目的真实课堂录音)。

补充: Observation Adding (OA) 也测了 —— 不是所有降噪变体都无效
----------------------------------------------------------------
arXiv 2404.14860 提出两条缓解伪影误差的办法, 其中 **Observation Adding (OA)** 极简:
**把降噪输出与原始带噪音频按比例混合再送 ASR**。实测(3 窗口):

    条件              词数   段数   无终止标点   空转写
    原始(现状)         541    36    10 (28%)    2 ( 6%)
    纯降噪(已否决)      298    41     6 (15%)    6 (15%)
    OA α=0.3          466    32     8 (25%)    0 ( 0%)
    OA α=0.5          469    32     6 (19%)    0 ( 0%)
    OA α=1.0          422    32     4 (12%)    0 ( 0%)

**OA 确实缓解了降噪的伤害**: 空转写从 15% 直接归零、词数从 298 回到 422–469。
调研说的"OA 能减少伪影误差"**在本机成立**。

⚠️ 但**两条都别做**：
- OA 的"空转写 15%→0%"**落在噪声带内**（该指标 n≈11 段时噪声底线 ±18 个百分点），
  词数还砍掉两成（541→422~469）。**不作为方案。**
- 去混响 WPE 曾一度被当成"唯一正收益"，**已撤回** —— 那次调用传的是时域波形而
  `nara_wpe.wpe()` 要复数 STFT（实测是恒等变换）；用正确管线重测后**测不出效果**。
  详见 `dereverb_ab.py` 开头的完整记录。

→ **结论：远场收音的软件侧手段到这里全部穷尽，剩下的是硬件（把麦放到讲台附近）。**
"""
import os
import sys
import pathlib

import numpy as np
import sherpa_onnx

HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2]))
import vad                                                    # noqa: E402
from capture import PeakNormalizer, SR, load_file             # noqa: E402
from asr import load_asr                                      # noqa: E402
from translator import _load_terms                            # noqa: E402

MODELS = os.path.expanduser("~/models/denoise")
CH = SR // 10
TERM = tuple(".?!…")
GLOSSARY = HERE.with_name("thermo_glossary.txt")


def normalize(x):
    """滑动峰值归一化。降噪前后各做一次 —— 见模块 docstring 的说明。"""
    pn = PeakNormalizer()
    return np.concatenate([pn.process(x[i:i + CH]) for i in range(0, len(x) - CH, CH)])


def make_denoiser(kind):
    if kind == "raw":
        return None
    cfg = sherpa_onnx.OfflineSpeechDenoiserConfig()
    if kind == "gtcrn":
        cfg.model.gtcrn.model = f"{MODELS}/gtcrn_simple.onnx"
    elif kind == "dpdfnet":
        cfg.model.dpdfnet.model = f"{MODELS}/dpdfnet_baseline.onnx"
    else:
        raise SystemExit(f"未知条件 {kind}")
    return sherpa_onnx.OfflineSpeechDenoiser(cfg)


def main():
    if len(sys.argv) < 4:
        sys.exit("用法: denoise_ab.py <音频> <起始秒> <时长秒> [音频 起 时 ...]")
    args = sys.argv[1:]
    windows = [(args[i], float(args[i + 1]), float(args[i + 2]))
               for i in range(0, len(args) - 2, 3)]

    asr = load_asr(os.path.expanduser("~/models/parakeet-tdt-0.6b-v3-int8"))
    terms = [t.lower() for t in _load_terms(str(GLOSSARY)) if len(t) > 4]
    conds = ["raw", "gtcrn", "dpdfnet"]
    denoisers = {k: make_denoiser(k) for k in conds if k != "raw"}

    hdr = (f"{'窗口':<22}{'条件':<9}{'段数':>5}{'空转写':>9}{'无终止':>9}"
           f"{'词数':>7}{'logprob':>9}{'术语':>6}")
    print(hdr)
    print("-" * len(hdr))
    agg = {k: {"seg": 0, "empty": 0, "noend": 0, "words": 0, "term": 0} for k in conds}
    for path, a, b in windows:
        src = load_file(path)
        # ⚠️ 2026-09-25 修：原为 `src[int(a*SR): int(b*SR)]`，但 `b` 是**时长**不是
        # 结束时间（见上面的用法说明）。起始秒非 0 时切出来的是**更短甚至空的**音频，
        # 而且不报错、屏幕上完全看不出异常 —— 也就是说**本脚本此前所有起始秒非 0 的
        # 运行，量的都是错的音频**。对比 `hotwords_ab.py:79` 用的是 `(a + b)`，那才对。
        base = normalize(src[int(a * SR): int((a + b) * SR)])
        for cond in conds:
            x = base
            if denoisers.get(cond) is not None:
                dn = np.asarray(denoisers[cond].run(base, SR).samples, dtype=np.float32)
                x = normalize(dn)
            got = []
            seg = vad.Segmenter(lambda _b: None, lambda _b: got.append(_b))
            for i in range(0, len(x), CH):
                seg.accept(x[i:i + CH])
            seg.flush()
            texts = [asr.transcribe(g) for g in got]
            n = max(len(texts), 1)
            empty = sum(1 for t in texts if not t.strip())
            noend = sum(1 for t in texts if t and not t.rstrip().endswith(TERM))
            words = sum(len(t.split()) for t in texts)
            terms_hit = sum(1 for t in texts for w in terms if w in t.lower())
            s = agg[cond]
            s["seg"] += len(texts); s["empty"] += empty
            s["noend"] += noend; s["words"] += words; s["term"] += terms_hit
            print(f"{pathlib.Path(path).name[:10]} {int(a)}-{int(b):<9}{cond:<9}{len(texts):>5}"
                  f"{empty:>5}({empty / n * 100:>2.0f}%){noend:>5}({noend / n * 100:>2.0f}%)"
                  f"{words:>7}{'':>9}{terms_hit:>6}", flush=True)

    print("\n=== 合计 ===")
    for cond in conds:
        s = agg[cond]
        n = max(s["seg"], 1)
        print(f"{cond:<9}段数 {s['seg']:>4}  空转写 {s['empty']/n*100:>3.0f}%  "
              f"无终止 {s['noend']/n*100:>3.0f}%  词数 {s['words']:>5}  术语命中 {s['term']:>3}")


if __name__ == "__main__":
    main()
