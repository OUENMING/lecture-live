#!/usr/bin/env python3
"""Soniox 实时识别的**真链路**冒烟（手动跑，花钱，不进 `check.py`）。

推一个音频文件（按实时速度）→ 真 Worker 领 key → 真 Soniox → 打印每句的封口时刻与延迟。

    ClassLive.app/Contents/MacOS/python scripts/probe_stream_asr.py <音频> [起点s] [时长s] [倍速]
    # 默认只推 60 s、1.0 倍速（= 真课堂节奏）；倍速 >1 只是把音频喂快，延迟数字会失真。

⚠️ **只允许用测试 key**（`cloud_translator.load_test_key()`，`~/.classlive/test-key` = test1 通行码）。
   除非显式设 `CLASSLIVE_ASR_TOKEN`（比如 `~/.classlive/soniox-token` 的直连 key）——
   那会记到码名下、也绕开 Worker。默认走 Worker（与生产同一条路）。
⚠️ 音频**会上云**（经作者 Worker 换的临时 key 直连 Soniox）；别拿隐私录音跑。
⚠️ 想验「额度用完 → 退回」那条路，用 test1 码多跑几次把额度耗尽即可看到 reason=quota。

看什么：
- 每句「音频句末 → 封口」的延迟（对照 10-16 实验：p50 0.86 s）；
- 末行汇总：句数 / 延迟 p50 / 收到的字节 / 退回原因（应为空）。
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from capture import PeakNormalizer, SR, load_file  # noqa: E402
from stream_asr import StreamASR, Sealed  # noqa: E402

CHUNK_S = 0.1


def main() -> int:
    ap = argparse.ArgumentParser(description="Soniox 实时识别冒烟（手动、花钱）")
    ap.add_argument("audio", help="音频文件（m4a/wav/…，capture.load_file 支持的都行）")
    ap.add_argument("start", nargs="?", type=float, default=0.0, help="起点秒（默认 0）")
    ap.add_argument("dur", nargs="?", type=float, default=60.0, help="推多久（默认 60 s）")
    ap.add_argument("speed", nargs="?", type=float, default=1.0, help="倍速（默认 1.0 = 实时）")
    a = ap.parse_args()

    passcode = os.environ.get("CLASSLIVE_ASR_TOKEN")
    if not passcode:
        import cloud_translator
        passcode = cloud_translator.load_test_key()      # ⚠️ 只允许 test1（不回落作者自己的码）
        print("通行码: ~/.classlive/test-key（test1）")

    t0 = time.monotonic()            # 推第一块音频时重置：音频时刻 0 = 墙钟 t0（建连 / 解码不算进延迟）
    lines: list[tuple[float, float, str]] = []           # (音频句末, 封口墙钟, 文本)

    def on_sealed(s: Sealed) -> None:
        wall = time.monotonic() - t0
        lines.append((s.end_s, wall, s.text))
        print(f"  [{wall:7.2f}s] 句末 {s.end_s:7.2f}s  延迟 {wall - s.end_s:5.2f}s │ {s.text}",
              flush=True)

    s = StreamASR(passcode=passcode, on_sealed=on_sealed)
    print(f"领 key + 建连…（reason 非空 = 退回本地：{s.reason()!r}）")
    t_conn = time.monotonic()
    while not s.alive() and s.reason() == "" and time.monotonic() - t_conn < 10.0:
        time.sleep(0.05)
    if not s.alive():
        print(f"❌ 没建起来：reason={s.reason()!r} notice={s.notice_text()!r}")
        return 1
    print(f"✅ 连上了（{time.monotonic() - t_conn:.2f}s）")

    # `load_file` 的 start/dur 走 ffmpeg 输入定位 / 输出时长（不用先解全片）
    clip = load_file(a.audio, start=a.start, dur=a.dur)
    norm = PeakNormalizer()
    n = int(CHUNK_S * SR)
    i = 0
    try:
        t0 = time.monotonic()
        while i < len(clip) and s.alive():
            s.feed(norm.process(clip[i:i + n]))
            i += n
            # 按**绝对时刻表**推（不是每块固定 sleep）：逐块 sleep 会把处理耗时累积成越推越慢，
            # 延迟数字就随时间线性涨（10-17 第一次冒烟就是这样：3 s → 7 s）。
            ahead = t0 + i / SR / max(a.speed, 0.01) - time.monotonic()
            if ahead > 0:
                time.sleep(ahead)
        time.sleep(1.0)                                  # 让最后的 final 回来
    except KeyboardInterrupt:
        print("\n（手动中断）")
    finally:
        s.stop()

    waits = sorted(w - e for e, w, _ in lines)
    p50 = waits[len(waits) // 2] if waits else float("nan")
    print(f"\n句 {len(lines)} 句；封口延迟 p50 {p50:.2f}s"
          f"{' 最慢 %.2fs' % waits[-1] if waits else ''}；"
          f"音频 {s.cum_samples() / SR:.1f}s；丢块 {s.dropped()}；"
          f"退回原因 {s.reason()!r}")
    return 0 if not s.reason() else 2


if __name__ == "__main__":
    sys.exit(main())
