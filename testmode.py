#!/usr/bin/env python3
"""测试模式: 跑一节真实课, 把**能抓的都抓下来**, 供以后优化用。

设计前提
--------
1. **绝不能影响上课。** 所有采集点都包在 try/except 里 —— 记录失败是"少一份数据",
   不是"课跑不下去"。这条比拿到数据重要。
2. **采到的数据要能回放。** 只存指标不存音频, 以后想复跑实验就只能干瞪眼。
   所以音频默认录（16kHz int16 单声道, 15 分钟约 28MB), 可用 `--no-record-audio` 关。
   ⚠️ 音频**只写本机**（`sessions/` 同级), 不改变"音频不出机器"这条前提。
3. **落盘格式要能被脚本读。** 一个 JSON, 字段名直白, 不要嵌套太深。

采什么
------
*音频*  : 逐块累计峰值/RMS/波峰因数 → 电平分布、动态范围
*分段*  : 每段的时长、电平、波峰因数、VAD 判定、文本、ASR 耗时/
          用了哪个模型/是否回退/平均 logprob/词数/有无句末标点
*流水线*: 首字中文延迟、定稿延迟、翻译失败、云端降级、退化回退次数
*资源*  : CPU 秒(整段 + 折算实时占比)、峰值内存
*收尾*  : VAD 诊断原文(report())、会话文件路径

用法
----
    cl test                     # 悬浮窗 + 测试模式（默认录音）
    cl test --no-record-audio   # 只要指标, 不留音频
"""
from __future__ import annotations

import atexit
import json
import os
import pathlib
import resource
import sys
import time
import wave

import numpy as np

SR = 16000


def _safe(fn):
    """任何采集失败都吞掉 —— 记录是"锦上添花", 绝不能把课搞崩。"""
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except Exception:                                 # noqa: BLE001
            return None
    return wrapper


def collect_env() -> dict:
    """环境快照 —— 没有它, 对方发来的报告里"为什么他的数不一样"无从判断。"""
    import platform

    def pkg(name):
        try:
            import importlib.metadata as md
            return md.version(name)
        except Exception:                                 # noqa: BLE001
            return None

    models = {}
    for d in ("parakeet-tdt-0.6b-v3-int8", "sherpa-onnx-whisper-turbo", "vad"):
        p = pathlib.Path(os.path.expanduser("~/models")) / d
        models[d] = (round(sum(f.stat().st_size for f in p.rglob("*")
                               if f.is_file()) / 1e6, 1) if p.exists() else None)
    try:
        import subprocess
        root = pathlib.Path(__file__).resolve().parent
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root,
                                capture_output=True, text=True, timeout=3).stdout.strip()
    except Exception:                                     # noqa: BLE001
        commit = ""
    return {
        "os": f"{platform.system()} {platform.release()}",
        "machine": platform.machine(),
        "python": platform.python_version(),
        "classlive_version": (pathlib.Path(__file__).with_name("VERSION")
                              .read_text(encoding="utf-8").strip()
                              if pathlib.Path(__file__).with_name("VERSION").exists() else "?"),
        "git_commit": commit,
        "packages": {n: pkg(n) for n in
                     ("sherpa-onnx", "numpy", "sounddevice", "mlx-lm", "httpx")},
        "models_mb": models,
    }


def make_bundle(stem: pathlib.Path, report_path: str | os.PathLike,
                session_path, include_audio: bool) -> str | None:
    """把一次测试打包成**一个文件**, 方便发给作者。

    ⚠️ **包里含隐私内容**: 课堂音频 + 完整逐字转录 —— 可能有其他同学的声音。
    所以包里放一份 README 说明里面是什么, 且由使用者自己决定发不发。
    """
    import zipfile

    out = pathlib.Path(str(stem) + ".bundle.zip")
    readme = """ClassLive 测试数据包
====================

这是用 `cl test` 跑的一次真实课堂测试。里面:

  README.txt     本说明
  env.json       环境快照（系统 / Python / 依赖版本 / 模型大小 / git 提交）
  report.json    指标报告（逐段的 ASR 耗时、电平、置信度、词数…）
  session.md     逐字转录（中英对照）
  audio.wav      课堂音频（若打包时含音频）

⚠️ 隐私提醒
------------
`sessions.md` 与 `audio.wav` 是**这节课的真实内容**，可能包含其他同学的声音
或个人信息。发出去之前请自己确认可以分享。

这份数据用来做什么
------------------
优化远场收音与转写质量。有了真实音频，作者才能在本机复跑 A/B 对照实验
（此前只能用零散的几段录音）。**只用于这个用途。**
"""
    try:
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("README.txt", readme)
            z.writestr("env.json", json.dumps(collect_env(), ensure_ascii=False, indent=1))
            if os.path.exists(report_path):
                z.write(report_path, "report.json")
            if session_path and os.path.exists(session_path):
                z.write(session_path, "session.md")
            wav = pathlib.Path(str(stem) + ".wav")
            if include_audio and wav.exists():
                z.write(wav, "audio.wav")
    except Exception:                                     # noqa: BLE001
        return None
    return str(out)


class TestSession:
    """一次测试课的采集器。所有 note_* 方法都保证不抛。"""

    def __init__(self, session_path: pathlib.Path | None, record_audio: bool = True):
        self.t0 = time.monotonic()
        self.session_path = session_path
        self.stem = session_path.with_suffix("") if session_path else None
        self.record_audio = bool(record_audio) and self.stem is not None

        # 音频电平累计（流式, 不存全波形, 除了录音时）
        self._n_samples = 0
        self._sum_sq = 0.0
        self._peak = 0.0
        self._block_peaks: list[float] = []
        self._block_rms: list[float] = []

        if self.record_audio:
            self._wav = wave.open(str(self.stem) + ".wav", "wb")
            self._wav.setnchannels(1)
            self._wav.setsampwidth(2)
            self._wav.setframerate(SR)
            # 查过 CPython 源码 + 实测，把这件事说准：
            # · **wav 不会"头损坏"** —— `Wave_write.writeframes()` 每次调用都会
            #   `_patchheader()` 修正 RIFF 长度字段（`writeframesraw` 才不会），
            #   本模块用的正是 writeframes。
            # · 但 `_patchheader()` 只在**长度变了**时才 seek，而那个 seek 顺带刷
            #   缓冲。所以真正会丢数据的窗口很窄：**总写入量还小于文件缓冲
            #   （Python 默认 ~8KB）就硬退出**。实测 1 块(3.2KB) + `os._exit()`
            #   → 文件 0 字节读不出；2 块(6.4KB) 就正常了。
            #   对真实课堂（几千块）不可能发生，但兜一层也就 6 行，且幂等。
            atexit.register(self._close_wav)
        else:
            self._wav = None

        # 分段
        self.bundle_path: str | None = None
        self.segments: list[dict] = []
        self.events: list[dict] = []
        self._seg_no = 0
        self._cpu0 = self._cpu()
        self._rss_peak = 0

    def _close_wav(self) -> None:
        """幂等关闭 WAV —— `finish()` 与 `atexit` 都会调它。"""
        w, self._wav = self._wav, None
        if w is not None:
            try:
                w.close()
            except Exception:                             # noqa: BLE001
                pass

    # ---- 内部 ----
    @staticmethod
    def _cpu() -> float:
        r = resource.getrusage(resource.RUSAGE_SELF)
        return r.ru_utime + r.ru_stime

    def _rss_mb(self) -> float:
        # ⚠️ `ru_maxrss` 的单位**跨平台不一致**：macOS 是**字节**，Linux 是 **KB**。
        # 项目只支持 macOS，但报告里采了 os/machine（那正是为了跨环境比），
        # 所以按平台换算 —— 免得这份报告哪天在 Linux 上跑出小 1000 倍的数。
        # （2026-09-24 OCR 分块审计发现。）
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return rss / 1e6 if sys.platform == "darwin" else rss / 1e3

    # ---- 采集点（全部 fail-soft）----
    @_safe
    def note_chunk(self, chunk) -> None:
        a = np.abs(chunk)
        self._n_samples += len(chunk)
        self._sum_sq += float(np.sum(chunk.astype(np.float64) ** 2))
        self._peak = max(self._peak, float(a.max()) if len(a) else 0.0)
        if len(a):
            self._block_peaks.append(float(a.max()))
            self._block_rms.append(float(np.sqrt(np.mean(chunk.astype(np.float64) ** 2))))
        if self._wav is not None:
            self._wav.writeframes(
                (np.clip(chunk, -1.0, 1.0) * 32767).astype("<i2").tobytes())
        self._rss_peak = max(self._rss_peak, self._rss_mb())

    @_safe
    def note_segment(self, audio, text: str, asr_ms: float, model: str,
                     logprob: float | None = None, fell_back: bool = False,
                     n_sentences: int = 0) -> None:
        a = np.abs(audio) if len(audio) else np.zeros(1)
        rms = float(np.sqrt(np.mean(np.asarray(audio, dtype=np.float64) ** 2))) \
            if len(audio) else 0.0
        self.segments.append({
            "i": self._seg_no,
            "t": round(time.monotonic() - self.t0, 2),
            "dur_s": round(len(audio) / SR, 2),
            "level_dbfs": round(20 * np.log10(max(rms, 1e-9)), 1),
            "crest_db": round(20 * np.log10(max(float(a.max()), 1e-9) / max(rms, 1e-9)), 1),
            "asr_ms": round(asr_ms, 1),
            "model": model,
            "fell_back": bool(fell_back),
            "logprob": round(logprob, 3) if logprob is not None else None,
            "words": len(text.split()),
            "end_punct": bool(text.rstrip().endswith((".", "!", "?", "…"))) if text else None,
            "empty": not bool(text.strip()),
            "n_sentences": n_sentences,
            "text": text,
        })
        self._seg_no += 1

    @_safe
    def note_event(self, kind: str, detail: str = "") -> None:
        self.events.append({"t": round(time.monotonic() - self.t0, 2),
                            "kind": kind, "detail": detail[:200]})

    # ---- 收尾 ----
    @_safe
    def finish(self, vad_report: str = "", note_path: str = "",
               bundle: bool = True) -> str | None:
        if self._wav is not None:
            self._close_wav()
        if self.stem is None:
            return None

        dur = time.monotonic() - self.t0
        cpu = self._cpu() - self._cpu0
        rms_all = float(np.sqrt(self._sum_sq / max(self._n_samples, 1)))
        segs = self.segments
        n = max(len(segs), 1)

        def pct(cond) -> float:
            return round(sum(1 for s in segs if cond(s)) / n * 100, 1)

        # 电平的分布 —— 用逐块 RMS/峰值的百分位, 看这节课的动态范围
        rms_blocks = np.asarray([x for x in self._block_rms if x > 0] or [1e-9])
        peak_blocks = np.asarray(self._block_peaks or [1e-9])
        rms_dbfs = 20 * np.log10(np.maximum(rms_blocks, 1e-9))
        peak_dbfs = 20 * np.log10(np.maximum(peak_blocks, 1e-9))
        # 动态范围 = 响块的峰值(p95) 减 静块的底噪(p5) —— 远场衰减大的课这个数会很大
        dyn_range = float(np.percentile(peak_dbfs, 95) - np.percentile(rms_dbfs, 5))

        report = {
            "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
            "session_file": str(self.session_path) if self.session_path else None,
            "note_file": note_path,
            "audio_saved": bool(self.record_audio),
            "duration_s": round(dur, 1),
            "audio": {
                "n_samples": self._n_samples,
                "audio_s": round(self._n_samples / SR, 1),
                "peak": round(self._peak, 4),
                "rms_dbfs": round(20 * np.log10(max(rms_all, 1e-9)), 1),
                "block_rms_dbfs_p5": round(float(np.percentile(rms_dbfs, 5)), 1),
                "block_rms_dbfs_p50": round(float(np.percentile(rms_dbfs, 50)), 1),
                "block_rms_dbfs_p95": round(float(np.percentile(rms_dbfs, 95)), 1),
                "dynamic_range_db": round(dyn_range, 1),
            },
            "resource": {
                "cpu_s": round(cpu, 1),
                "audio_s_per_cpu_s": round((self._n_samples / SR) / max(cpu, 1e-9), 2),
                # ⚠️ 这两个是"每音频秒的 CPU 秒"和"CPU/墙上时间"。
                # 后者在 --speed>1 的回放里会被**放大**(墙钟比音频短), 别跨速度比。
                "cpu_s_per_audio_s": round(cpu / max(self._n_samples / SR, 1e-9), 3),
                "cpu_pct_of_walltime": round(cpu / max(dur, 1e-9) * 100, 1),
                "rss_peak_mb": round(self._rss_peak, 0),
            },
            "asr": {
                "segments": len(segs),
                "words": sum(s["words"] for s in segs),
                "empty_pct": pct(lambda s: s["empty"]),
                "no_end_punct_pct": pct(lambda s: s["words"] and not s["end_punct"]),
                "fell_back_pct": pct(lambda s: s["fell_back"]),
                "asr_ms_p50": round(float(np.median([s["asr_ms"] for s in segs])), 1) if segs else None,
                "asr_ms_p95": round(float(np.percentile([s["asr_ms"] for s in segs], 95)), 1) if segs else None,
                "logprob_mean": round(float(np.mean([s["logprob"] for s in segs
                                                     if s["logprob"] is not None])), 3)
                if any(s["logprob"] is not None for s in segs) else None,
            },
            "notes": [
                "logprob 只有 **Parakeet** 会填 —— Whisper 的 result.ys_log_probs 是空的。"
                "定稿默认走 Whisper, 所以这个字段通常是 null, 不是采集失败。",
                "cpu_pct_of_walltime 在 --speed>1 的回放里会被放大;"
                "跨机器/跨速度比请用 cpu_s_per_audio_s。",
                "no_end_punct_pct 在 n<30 段时噪声很大(实测底线 ±18 个百分点),"
                "单节课的数字不要当结论。",
            ],
            "vad_report": vad_report,
            "events": self.events,
            "segments": segs,
        }
        out = str(self.stem) + ".report.json"
        with open(out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=1)
        if bundle:
            self.bundle_path = make_bundle(self.stem, out, self.session_path,
                                           include_audio=self.record_audio)
        return out
