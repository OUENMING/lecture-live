#!/usr/bin/env python3
"""系统声音源（Process Tap）上机探针 —— **第一次会弹系统授权框**，请在你本人在场时跑。

用法：先让电脑放点声音（任何视频/音乐），然后
  ClassLive.app/Contents/MacOS/python scripts/probe_system_tap.py [秒数，默认 10]

它一步步打印每个 CoreAudio 调用的返回值，录 N 秒，写 ~/.classlive/eval/tap_probe.wav，
并报告：采样率、收到的块数、峰值/RMS、是否全零。
判读：
  · 报 `OSStatus=...`  → 看是哪一步；多半是没授权（系统设置 → 隐私与安全性 → 「音频录制」/ 麦克风旁的系统录音）
  · 全零但没报错       → 授权被拒（系统不报错，直接给零）或此刻没声音在播
  · 有声音、wav 能听   → 可行。下一步：`cl` 里 `--source system` 真跑一节线上课的前 5 分钟
⚠️ 这是探针，不在运行路径上；不写 sessions/。
"""
import os, sys, time, wave

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import numpy as np
import system_tap as T


def main():
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 10.0
    ok, why = T.available()
    print(f"available: {ok} {why}")
    if not ok:
        return 2
    raw = []

    class Tee(T.ProcessTapBackend):
        def start(self, on_audio):
            def wrapped(a, sr):
                raw.append((a.copy(), sr))
                on_audio(a, sr)
            super().start(wrapped)

    t0 = time.time()
    try:
        src = T.SystemTapSource(Tee())
    except Exception as e:                                  # noqa: BLE001
        print("❌ 启动失败：", repr(e))
        return 1
    print(f"✅ 已启动（{time.time() - t0:.2f}s）。录 {secs:.0f} 秒……（现在放点声音）")
    chunks = []
    end = time.time() + secs
    while time.time() < end:
        c = src.poll()
        if c is None:
            time.sleep(0.02)
        else:
            chunks.append(c)
    msg = src.health()
    src.close()
    if not raw:
        print("❌ 一个回调都没收到（IOProc 没跑起来）")
        return 1
    rates = sorted({sr for _, sr in raw})
    allraw = np.concatenate([a for a, _ in raw])
    print(f"回调 {len(raw)} 次，采样率 {rates}，共 {len(allraw)} 样本（≈{len(allraw)/rates[0]:.1f}s）")
    print(f"原始：peak={np.max(np.abs(allraw)):.5f} rms={np.sqrt(np.mean(allraw.astype(np.float64)**2)):.5f}")
    if chunks:
        out = np.concatenate(chunks)
        print(f"16k 输出 {len(chunks)} 块（≈{len(out)/16000:.1f}s）peak={np.max(np.abs(out)):.5f}")
        path = os.path.expanduser("~/.classlive/eval/tap_probe.wav")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with wave.open(path, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
            w.writeframes((np.clip(out, -1, 1) * 32767).astype("<i2").tobytes())
        print("已写", path)
    print("health:", msg or "正常")
    return 0


if __name__ == "__main__":
    sys.exit(main())
