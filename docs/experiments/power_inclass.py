#!/usr/bin/env python3
"""上课节奏下的真实功耗: 现状(全 parakeet) vs 混合(草稿 parakeet + 定稿 whisper)。

电池供电时直接读 `ioreg` 的 InstantAmperage × Voltage —— 不需要 sudo。
先自测仪器: 空闲功耗应落在 MacBook 的合理区间(个位数到十几瓦),
读不到或明显离谱就说明解析写错了, 别拿它下结论。

按真实节奏跑, 不能背靠背灌 —— 功耗是"每单位时间"的量, 必须按墙上时钟。
"""
import os
import re
import resource
import subprocess
import sys
import threading
import time

import numpy as np

import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(_pl.Path(__file__).resolve().parent))
from asr_compare import build, decode                           # noqa: E402
import vad                                                      # noqa: E402
from capture import load_file, PeakNormalizer, SR                # noqa: E402

CH = SR // 10
U64 = 1 << 64


def read_batt():
    """-> (watts_discharge, soc%). InstantAmperage 是有符号的, 会被读成 unsigned。"""
    out = subprocess.run(["ioreg", "-rn", "AppleSmartBattery"],
                         capture_output=True, text=True).stdout
    def g(k):
        m = re.search(rf'"{k}"\s*=\s*(-?\d+)', out)
        if not m:
            return None
        v = int(m.group(1))
        if v >= U64 // 2:            # 补码还原成负数
            v -= U64
        return v
    ma, mv, soc = g("InstantAmperage"), g("Voltage"), g("CurrentCapacity")
    if ma is None or mv is None:
        return None, soc
    return abs(ma) * mv / 1e6, soc


class PowerSampler(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.samples = []
        self.stop = threading.Event()

    def run(self):
        while not self.stop.is_set():
            w, soc = read_batt()
            if w is not None:
                self.samples.append(w)
            time.sleep(1.5)

    def phase(self, label, fn, seconds):
        self.samples = []
        t0 = time.time()
        n = fn(self.stop, seconds)
        dt = time.time() - t0
        s = sorted(self.samples)
        med = s[len(s) // 2] if s else float("nan")
        _, soc = read_batt()
        print(f"  {label:<26} {med:>6.2f} W  (中位 {len(s)} 采样, "
              f"范围 {min(s) if s else 0:.1f}–{max(s) if s else 0:.1f})  "
              f"实测 {dt:.0f}s  完成 {n} 次推理  电量 {soc}%", flush=True)
        return med


def cpu_seconds():
    r = resource.getrusage(resource.RUSAGE_SELF)
    return r.ru_utime + r.ru_stime


def main():
    w0, soc0 = read_batt()
    print(f"起始: {w0:.2f} W, 电量 {soc0}%\n" if w0 else "读不到电池数据")
    if w0 is None:
        raise SystemExit("ioreg 解析失败 —— 先修仪器再测")

    audio = sys.argv[1] if len(sys.argv) > 1 else "lecture.m4a"
    src = load_file(audio)
    clip = src[1200 * SR: 1320 * SR]
    pn = PeakNormalizer()
    audio = np.concatenate([pn.process(clip[i:i + CH]) for i in range(0, len(clip) - CH, CH)])
    got = []
    seg = vad.Segmenter(lambda _b: None, lambda _b: got.append(_b))
    for i in range(0, len(audio), CH):
        seg.accept(audio[i:i + CH])
    seg.flush()
    d10 = np.concatenate([g for g in got if len(g)])[:10 * SR]
    finals = list(got)

    recs = {}
    smp = PowerSampler()
    smp.start()

    def idle(stop, seconds):
        stop.wait(seconds)
        return 0

    def load(draft_model, final_model):
        def fn(stop, seconds):
            d = recs[draft_model]
            f = recs.get(final_model)
            t_end = time.time() + seconds
            n = 0
            i = 0
            while time.time() < t_end and not stop.is_set():
                slot = time.time()
                decode(d, d10)                       # 草稿: 每 1.0s 一次, 转最近 10s
                if f is not None and i % 11 == 0:    # 定稿: 每 ~11s 一段
                    decode(f, finals[i % len(finals)])
                n += 1
                i += 1
                nap = 1.0 - (time.time() - slot)     # 按墙上时钟对齐到 1s 一拍
                if nap > 0:
                    stop.wait(nap)
            return n
        return fn

    print("跑之前先确认仪器读数合理:")
    smp.phase("① 空闲", idle, 15)
    for k in ("parakeet", "whisper-turbo"):
        recs[k] = build(k)
        print(f"  (已加载 {k})", flush=True)
    smp.phase("② 现状: 全 parakeet", load("parakeet", "parakeet"), 45)
    smp.phase("③ 混合: 定稿 whisper", load("parakeet", "whisper-turbo"), 45)
    smp.stop.set()
    time.sleep(1.6)
    w1, soc1 = read_batt()
    print(f"\n结束: {w1:.2f} W, 电量 {soc1}%")


if __name__ == "__main__":
    main()
