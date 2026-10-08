#!/usr/bin/env python3
"""用 macOS `say` 把 econ_lecture.txt 合成英文讲课音频（带段间停顿），供 stream_eval 当"有标准答案"的语料。

⚠️ TTS 干净、语速均匀 —— WER 是**乐观上界**，只适合做回归基线（改动前后对比），
   不能当作真实课堂的错词率。真实课堂用 `stream_eval.py` 不带 --ref，看稳定度与延迟。
用法：python3 scripts/eval_corpus/make_tts_corpus.py [输出目录，默认 ~/.classlive/eval]
"""
import os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.expanduser(sys.argv[1] if len(sys.argv) > 1 else "~/.classlive/eval")
VOICES = ["Daniel", "Samantha"]

os.makedirs(OUT, exist_ok=True)
ref = open(os.path.join(HERE, "econ_lecture.txt"), encoding="utf-8").read().strip()
say_input = ref.replace("\n\n", " [[slnc 1400]] ")
for v in VOICES:
    wav = os.path.join(OUT, f"econ_{v.lower()}.wav")
    # 直接出 16kHz 单声道 float WAV，不留中间文件
    subprocess.run(["say", "-v", v, "-r", "175", "--data-format=LEF32@16000", "-o", wav, say_input], check=True)
    open(wav[:-4] + ".txt", "w", encoding="utf-8").write(ref + "\n")
    print("生成", wav)
