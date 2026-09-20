#!/usr/bin/env python3
"""集成测试: 把一段真实课程录音喂进 分段→ASR→LLM 全链路, 打印草稿/定稿/中文。
用法: .venv/bin/python test_pipeline.py <m4a> [start_s] [dur_s] [speed]
"""
import sys, time, os
sys.path.insert(0, ".")
from capture import load_file, PeakNormalizer, SR
from vad import Segmenter
from asr import load_asr
from translator import load_translator

path = sys.argv[1]
start = float(sys.argv[2]) if len(sys.argv) > 2 else 120.0
dur = float(sys.argv[3]) if len(sys.argv) > 3 else 40.0
speed = float(sys.argv[4]) if len(sys.argv) > 4 else 3.0

asr = load_asr(os.path.expanduser("~/models/parakeet-tdt-0.6b-v3-int8"))
tr = load_translator("mlx-community/Qwen3-1.7B-4bit",
                     "glossary.txt", max_context=2)
tr.warmup()

finals = []

def on_partial(buf):
    t = asr.transcribe(buf)
    if t:
        print(f"[{time.strftime('%H:%M:%S')}] ▸ {t}", flush=True)

def on_utterance_end(buf):
    t0 = time.time()
    text = asr.transcribe(buf)
    if not text:
        return
    res = tr.fix_and_translate(text, finals)
    finals.append(text)
    dt = time.time() - t0
    print(f"[{time.strftime('%H:%M:%S')}] ✅ {res.en_fixed}   (ASR+LLM {dt:.1f}s)", flush=True)
    print(f"[{time.strftime('%H:%M:%S')}]    🌐 {res.zh}", flush=True)

seg = Segmenter(on_partial, on_utterance_end)
s = load_file(path)
clip = s[int(start * SR): int((start + dur) * SR)]

# 按真实时间流式(可加速), 每块 0.1s。电平归一化要和生产路径一致, 否则测不出远场表现
CHUNK = SR // 10
chunk_dur = CHUNK / SR
norm = PeakNormalizer()
i = 0
while i < len(clip):
    seg.accept(norm.process(clip[i:i + CHUNK]))
    i += CHUNK
    time.sleep(chunk_dur / speed)
seg.flush()                 # 收尾: 否则最后一段"语音已起、还没等到静音"的缓冲会被丢掉
_diag = seg.report()        # 与生产路径一致(main.py 收尾同样打这一行)
if _diag:
    print(_diag)
print("=== 结束 ===")
