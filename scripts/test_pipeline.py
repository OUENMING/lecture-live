#!/usr/bin/env python3
"""集成测试: 把一段真实课程录音喂进 分段→ASR→LLM 全链路, 打印草稿/定稿。

用法: ClassLive.app/Contents/MacOS/python scripts/test_pipeline.py <m4a> [start_s] [dur_s] [speed]

⚠️ 2026-11-20（桌面 D1/D1b）：本地识别只剩流式模型 Nemotron（草稿 + 段尾转录）、
   本地翻译引擎已删。所以这条链路现在是「Nemotron 逐块喂 → 段尾 finish → 只出英文」；
   中文要云端，不在这里试（它是**离线**量具，不许偷偷打云端 API）。
"""
import sys, time, os
sys.path.insert(0, ".")
from capture import load_file, PeakNormalizer, SR
from vad import Segmenter
import models
from draft_engine import DraftEngine, NemotronDrafter

path = sys.argv[1]
start = float(sys.argv[2]) if len(sys.argv) > 2 else 120.0
dur = float(sys.argv[3]) if len(sys.argv) > 3 else 40.0
speed = float(sys.argv[4]) if len(sys.argv) > 4 else 3.0

# 生产同一个草稿引擎（D1b 起它算出来的**段尾转录**就是云端失败时的定稿）。
asr = DraftEngine(NemotronDrafter(models.path_of("nemotron")))
_tag = {"n": 0}

finals = []

def on_partial(buf):
    t, _eng = asr.transcribe(buf, _tag["n"])
    if t:
        print(f"[{time.strftime('%H:%M:%S')}] ▸ {t}", flush=True)

def on_utterance_end(buf):
    t0 = time.time()
    text = asr.finish(buf, _tag["n"])          # 段尾：剩余样本 + 静音 + input_finished
    _tag["n"] += 1
    if not text:
        return
    finals.append(text)
    dt = time.time() - t0
    print(f"[{time.strftime('%H:%M:%S')}] ✅ {text}   (ASR {dt:.1f}s)", flush=True)

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
