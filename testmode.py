#!/usr/bin/env python3
"""测试模式: 跑一节真实课, 把**能抓的都抓下来**, 供以后优化用。

设计前提
--------
1. **绝不能影响上课。** 所有采集点都包在 try/except 里 —— 记录失败是"少一份数据",
   不是"课跑不下去"。这条比拿到数据重要。
2. **采到的数据要能回放。** 只存指标不存音频, 以后想复跑实验就只能干瞪眼。
   所以**想留音频时留得住**（16kHz int16 单声道, 15 分钟约 28MB）——
   但**默认不留**, 要显式加 `--record-audio`。
   ⚠️ 2026-09-28 翻的默认值。原来是默认录 —— 那意味着一次误启动就静默录下
   整节课（含其他同学的声音）。音频**只写本机**（`sessions/` 同级),
   不改变"音频不出机器"这条前提。
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
    cl test                     # 悬浮窗 + 测试模式（**默认留音频**，见下）
    cl test --no-record-audio   # 这节不留音频

⚠️⚠️ **2026-09-30 又翻了一次默认值**（作者拍板）：
   **测试模式下默认录**，正常 `cl` **仍然一个字都不录**。
   ⚠️ 这与 2026-09-28 那次（把默认从"录"改成"不录"）**不冲突** ——
      那次针对的是**正常上课那条路**，它**一个字没动**。
   ⚠️ 翻了之后的代价要记住：**`cl test` 一开就录**，所以披露（跟同学说一声）
      必须在**开课之前**做，不是课后补。
   音频格式见 `AUDIO_SEG_S` / `AUDIO_OPUS_BITRATE`（分段 Opus 24k，约 9MB/50 分钟）。
"""
from __future__ import annotations

import atexit
import json
import os
import pathlib
import resource
import subprocess

import models
import sys
import time
import wave

import numpy as np

SR = 16000

#: ⭐ 录音**分段**长度（秒）。每 10 分钟一个文件。
#: ⚠️ 切点按**样本数**整除，**不按墙钟** —— 按墙钟会在段与段之间丢样本
#:    （`note_chunk` 一次来一整块，落在边界上的那块会被整块归到某一边）。
AUDIO_SEG_S = 600

#: Opus 码率。⚠️ 依据与**诚实前提**（`docs/PLAN-test-mode.md` §5.2）：
#: NoLACE(Amazon 2024) 测得 Opus 20k 在 LibriSpeech clean 上 WER 2.03%（无损 2.01%）；
#: Khare 2020：16k → +12.6% / 32k → +6.6%。
#: ⚠️ **那些全是朗读或远场数据，没有一条是教室噪声录音** → 这是**外推**，不是实测背书。
#: → 24k 起步、**不要低于 16**。
AUDIO_OPUS_BITRATE = "24k"

#: `TestSession.note_block()` 认的名字（见那个方法的 docstring）。
#: ⚠️ **多一格就在这儿加一行** —— 名字打错**不会**静默建键，会进 `events` 报一声。
#: 每一项都是「一整份快照」，与 `note_event` 的时间轴事件分工不同。
BLOCKS = frozenset({
    "norm",     # `capture.PeakNormalizer.stats()` —— 归一化**之前**的电平
    "vad",      # `vad.Segmenter.diag` —— 断句 / 12s 硬切 / 丢弃
    "live",     # `live_summary.LiveSummarizer.stats()` —— 窗口/章/Jev/积压
    "terms",    # 术语注入条数（core / always / scored）
    "ui",       # UI 使用度（纲要开了几次、点了哪些按钮）
})


#: 静默采集最多为**每个方法**记几条异常 —— 热路径上失败会一秒刷几十次，
#: 全记会把 `events` 撑爆、也会把真正的时间轴事件挤没。超出的只累加计数。
_SAFE_MAX_PER_FN = 2


def _safe(fn):
    """任何采集失败都吞掉 —— 记录是"锦上添花", 绝不能把课搞崩。

    ⚠️ 2026-09-30 改：**吞掉，但不再无声**（`PLAN-test-mode.md` 采集面 ⑥）。
       原来纯 `return None` 的后果是「采集坏了」和「本来就没数据」在报告里
       **长得一模一样** —— 那正是这一格要修的东西。
    ⚠️ 但**不能出声到刷屏**：热路径（`note_chunk` 每 0.1 秒一次）一失败就是
       一秒几十条 → **每个方法最多记 `_SAFE_MAX_PER_FN` 条**，之后只计数。
    ⚠️ 而且**「不许把课搞崩」这条一个字不变**：整个包装体仍然只吞不抛，
       连记账自己失败都再吞一层（然后彻底闭嘴）。
    """
    name = getattr(fn, "__name__", "?")

    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except Exception as e:                            # noqa: BLE001
            try:
                slf = a[0] if a else None
                bad = slf._safe_bad
                bad[name] = bad.get(name, 0) + 1
                if bad[name] <= _SAFE_MAX_PER_FN:
                    slf.events.append({
                        "t": round(time.monotonic() - slf.t0, 2),
                        "kind": "safe_exc",
                        "detail": f"{name}: {type(e).__name__}: {str(e)[:120]}"})
            except Exception:                             # noqa: BLE001
                pass
            return None
    return wrapper


def _safe_loud(fn):
    """与 `_safe` 同一件事，**但失败要出声**。

    ⚠️ 专给「写最终报告」那种步骤用（2026-09-28 审查指出）：它失败之后用户
       **什么都拿不到**，而 `@_safe` 会连一句话都不留 —— 屏上就只有"跑了测试模式、
       没有任何报告"。热路径上的采集（逐块电平之类）仍然用静默那版，
       否则一秒钟能刷一屏。
    """
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except Exception as e:                            # noqa: BLE001
            print(f"⚠ {fn.__name__} 失败：{type(e).__name__}: {e}"
                  f" —— 这次的测试报告没写成", flush=True)
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

    models_sz = {}
    # ⚠️ 目录名从注册表来 —— 这里原来是**抄死的三个字面量**
    #    （`models.py` 的文件头把这一处点名了）。换一个模型目录名就得记得改这里，
    #    而忘了改的症状是「报告里某个模型体积是 None」，不报错。
    for _m in models.REQUIRED:
        d = models.basename_of(_m.key)
        p = pathlib.Path(os.path.expanduser(models.MODELS_ROOT)) / d
        models_sz[d] = (round(sum(f.stat().st_size for f in p.rglob("*")
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
        "models_mb": models_sz,
    }


def make_bundle(stem: pathlib.Path, report_path: str | os.PathLike,
                session_path, audio_paths: list | None = None) -> str | None:
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
            # ⭐ 分段之后音频有**多个**文件（`<stem>.001.opus` …）——
            #    打进包里 `audio/` 子目录下，保持分段结构。
            for ap in (audio_paths or []):
                ap = pathlib.Path(ap)
                if ap.exists():
                    z.write(ap, f"audio/{ap.name}")
    except Exception:                                     # noqa: BLE001
        return None
    return str(out)


class TestSession:
    """一次测试课的采集器。所有 note_* 方法都保证不抛。"""

    def __init__(self, session_path: pathlib.Path | None, record_audio: bool = False):
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

        # ⚠️ 这几个**必须在 `if` 外面**：不录音时 `_close_wav` / `finish` 也会碰它们。
        self._wav = None
        self._wav_path = None
        self._wav_paths: list = []       # 已经写出来的分段（含当前那个）
        self._seg_i = 0                  # 当前是第几段（从 1 起）
        self._seg_n = 0                  # 当前段已写入多少样本（切点判据用这个）
        self.audio_files: list = []      # 收尾转码后的 Opus 分段（`finish` 里填）

        if self.record_audio:
            # ⚠️⚠️ **这一段必须包起来**（2026-09-28 审查指出）：模块头的承诺是
            #    「所有采集点都包在 try/except 里 / **绝不能影响上课**」，
            #    而 `wave.open` 这一段整个在 try 外面 —— 磁盘满 / `sessions/` 只读 /
            #    路径被占都会抛，把测试模式的构造（乃至整个进程）带下去。
            #    失败就**降级成「不录音频」**，报告照出（同 `_wav = None` 那条既有路径）。
            # ⭐ 2026-09-30 起**分段**（每 `AUDIO_SEG_S` 一个文件）——
            #    理由：① 避开"一次误启动留下整节课"那个隐私顾虑（随时能看到录了几段）
            #          ② 上传能断点续传 ③ 收尾转码可以逐段做、内存不堆积
            self._rotate_wav()
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

        # 分段
        self.bundle_path: str | None = None
        self.segments: list[dict] = []
        self.events: list[dict] = []
        self._extra: dict = {}          # `note_block` 收下的整块快照（见 `BLOCKS`）
        self._safe_bad: dict = {}       # `_safe` 吞掉的异常按方法名计数（见 `_safe`）
        self._ui: dict = {}             # UI 使用度（`note_ui` 的累加器）
        self._corr = {"pairs": 0, "changed": 0, "identical": 0, "no_fix": 0,
                      "words_raw": 0, "words_fixed": 0}
        self._seg_no = 0
        self._cpu0 = self._cpu()
        self._rss_peak = 0

    def upload_files(self) -> list:
        """这节课该传哪些文件（阶段 3 的上传用）。⚠️ 只列**真的存在**的。

        ⚠️ 顺序无关（上传那边按文件名定 key）。⚠️ 不含 `env.json` ——
           那东西在 zip 包里，而上传走的是散文件；环境快照对**这一节课**的意义
           小于它把报告搞复杂。
        """
        if self.stem is None:
            return []
        cand = [pathlib.Path(str(self.stem) + ".report.json")]
        cand += [pathlib.Path(p) for p in (self.audio_files or [])]
        if self.session_path:
            cand.append(pathlib.Path(self.session_path))
        return [p for p in cand if p.is_file()]

    def _to_opus(self) -> list:
        """把 wav 分段转成 Opus，**转成功一个删一个**。返回 opus 路径表。

        ⚠️ 跑在**收尾**，不在上课路径上 —— 实测 5.1 分钟音频转 2.13 秒
           （折合 50 分钟约 21 秒）。「绝不能影响上课」那条纪律因此自动满足。
        ⚠️ **失败就保留那个 wav**（宁可占地方，别丢音频）—— 并记进 `events`。
        ⚠️ PATH：照 `capture.load_file` 的形状调（`ffmpeg` 是外部二进制；
           `.app` 双击那条路靠 `cl` 补的 PATH，不是这里补）。
        ⚠️ 3 分钟超时：正常一条分段约 20 秒，卡住说明 ffmpeg 出问题了。
        """
        out = []
        for wav in list(self._wav_paths):
            opus = wav.with_suffix(".opus")
            try:
                pr = subprocess.run(
                    ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(wav),
                     "-c:a", "libopus", "-b:a", AUDIO_OPUS_BITRATE,
                     "-ac", "1", "-ar", str(SR), str(opus)],
                    capture_output=True, timeout=180)
                if pr.returncode == 0 and opus.exists() and opus.stat().st_size > 0:
                    out.append(opus)
                    wav.unlink(missing_ok=True)          # ⚠️ 转成了才删
                else:
                    self.events.append({
                        "t": round(time.monotonic() - self.t0, 2),
                        "kind": "transcode_failed",
                        "detail": f"{wav.name}: rc={pr.returncode} "
                                  f"{pr.stderr.decode('utf-8', 'ignore')[:100]}"})
            except Exception as e:                       # noqa: BLE001
                self.events.append({
                    "t": round(time.monotonic() - self.t0, 2),
                    "kind": "transcode_failed",
                    "detail": f"{wav.name}: {type(e).__name__}: {str(e)[:100]}"})
        return out

    def _rotate_wav(self) -> None:
        """关掉当前段、开下一段。**失败就降级成「不再录」**，不抛。"""
        self._close_wav()
        self._seg_i += 1
        self._seg_n = 0
        path, w = self._open_wav(self._seg_i)
        self._wav, self._wav_path = w, path
        if path is not None:
            self._wav_paths.append(path)

    def _open_wav(self, idx: int):
        """开第 `idx` 个 wav 分段（从 1 起）。返回 `(path, handle)`，失败返回 `(None, None)`。"""
        p = pathlib.Path(f"{self.stem}.{idx:03d}.wav")
        try:
            w = wave.open(str(p), "wb")
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SR)
            return p, w
        except Exception as e:                            # noqa: BLE001
            print(f"⚠ 音频录制开不了（{type(e).__name__}: {e}）"
                  f" —— 测试模式照跑，只是没有音频", flush=True)
            return None, None

    def _close_wav(self) -> None:
        """幂等关闭**当前**分段 —— `finish()` 与 `atexit` 都会调它。"""
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
            self._seg_n += len(chunk)
            # ⚠️ 切点按**样本数**，不按墙钟（见 `AUDIO_SEG_S` 的注释）。
            if self._seg_n >= AUDIO_SEG_S * SR:
                self._rotate_wav()
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

    @_safe
    def note_correction(self, fixed: str, raw: str) -> None:
        """一句的**两级矫正对**：`fixed` = LLM 矫正后的英文，`raw` = 原始 ASR。

        ⭐ 这是「**矫正到底改了什么**」唯一的量化口径 —— 在那之前这一格完全瞎。
        ⚠️ 调用点是 `main.drain()` 的 `"final"` 分支（那里才有这一对），
           **不是** `note_segment`（那边的草稿走另一条 `draftq` 管线，
           而且是逐句翻译过的，凑不出这一对）。
        ⚠️⚠️ `fixed` 为空 = **矫正失败/没做**（翻译挂了那条路会把 en 置空），
           **不是"被改没了"** —— 要单独计 `no_fix`，别混进 `changed`。
           `obsidian_writer.append` 的 docstring 点名了这个风险：
           「LLM **偶尔会过度修正**（把正确的词改错）」。
        ⚠️ **只累计计数，不存文本** —— 文本已经在 `segments[].text` 里了，别存两份。
        """
        c = self._corr
        c["pairs"] += 1
        if not fixed:
            c["no_fix"] += 1
            return
        wf, wr = len(fixed.split()), len(raw.split())
        c["words_fixed"] += wf
        c["words_raw"] += wr
        if fixed.strip() == (raw or "").strip():
            c["identical"] += 1
        else:
            c["changed"] += 1

    @_safe
    def note_ui(self, name: str) -> None:
        """⭐ **UI 使用度**（采集面 ⑦）：某个交互被用了几次。

        名字由 `overlay` 那边给（`submit` / `ask` / `new_topic` / `lost` /
        `outline` / `trans_mode` / `quit`）。

        ⚠️ **只自增一个计数，不 append 事件** —— 它跑在 AppKit **主线程回调**里，
           而按钮可以连点几十下。「每次一个事件」会把 `events` 撑爆、
           也会把真正的时间轴事件挤没（同 `_safe` 那条防刷屏的理由）。
        ⭐ 用途：**功能有没有被用**。纲要从没被打开过 = 最大的产品信号，
           而在这之前**一条数据都没有**。
        """
        self._ui[name] = self._ui.get(name, 0) + 1

    @_safe
    def note_block(self, name: str, data) -> None:
        """收下一块**「整块」**的数据（不是流式事件）—— 收尾时原样进 `report.json`。

        与 `note_event` 的分工：事件是**时间轴上的点**（按钮点了、设备换了），
        块是**一整份快照**（归一化器计数器、VAD 诊断、实时总结的 stats…）。
        ⚠️ 名字必须在 `BLOCKS` 里 —— 写错的名字**不会**悄悄建一个新键，
           而是进 `events` 报一声（见下）。这一条防的是「打错一个字母，
           报告里多一个空键，而你以为采到了」。
        ⚠️ **不深拷贝** —— 调用方在 `finish()` 之前别再改那块数据。
           （真要改的场合：先 `dict(...)` 一份再传。）
        """
        if name not in BLOCKS:
            self.events.append({"t": round(time.monotonic() - self.t0, 2),
                                "kind": "bad_block",
                                "detail": f"未登记的块名 {name!r}（已知：{sorted(BLOCKS)}）"})
            return
        self._extra[name] = data

    # ---- 收尾 ----
    # ⚠️ 这里是**唯一**用 `_safe_loud` 的地方：它是最终落盘，失败了用户手里
    #    什么都没有 —— 静默吞掉等于"跑了测试模式但没有报告"，且查不出为什么。
    @_safe_loud
    def finish(self, vad_report: str = "", note_path: str = "",
               bundle: bool = True) -> str | None:
        if self._wav is not None:
            self._close_wav()
        if self.stem is None:
            return None
        # ⭐ 收尾转码：wav 分段 -> Opus，转成了就删 wav（见 `_to_opus`）。
        #    ⚠️ 排在**最前面**（报告落盘之前），这样报告里那个 `audio_saved`
        #       和 `audio_files` 说的是**最终状态**。
        self.audio_files = self._to_opus() if self.record_audio else []

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

        # ⭐ UI 使用度是**累加**出来的（`note_ui` 每次自增），不经 `note_block` ——
        #    收尾这里补进 `_extra`，让七格在报告里**形状一致**（都在 `blocks` 下）。
        #    ⚠️ 用 `setdefault` 是防"调用方也显式 note_block 过一次 ui"（那样以它为准）。
        self._extra.setdefault("ui", dict(self._ui))
        report = {
            "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
            "session_file": str(self.session_path) if self.session_path else None,
            "note_file": note_path,
            "audio_saved": bool(self.record_audio),
            # ⭐ 分段之后是**多个**文件（`<stem>.001.opus` …）。⚠️ 空表 = 没录，
            #    或者**转码全失败了**（后者看 `events` 里的 `transcode_failed`）。
            "audio_files": [str(x) for x in getattr(self, "audio_files", [])],
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
            "correction": {
                # ⭐ 「两级矫正到底改了什么」—— 在那之前这一格完全瞎。
                **self._corr,
                "changed_pct": round(
                    self._corr["changed"]
                    / max(self._corr["pairs"] - self._corr["no_fix"], 1) * 100, 1),
                # ⚠️ 分母是「**真的比过了**的对数」（扣掉 `no_fix`）——
                #    矫正失败那些根本没得比，算进分母会把"改了多少"稀释掉。
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
            # ⭐ 七格采集面里那些「整块快照」（`note_block` 收的）。
            # ⚠️ `block_names` 是**该有哪几块**的清单 —— 缺了就一眼看得出来
            #    （不然读报告的人分不清「没采到」和「本来就没有」）。
            "blocks": self._extra,
            "block_names": sorted(BLOCKS),
            # ⚠️ `_safe` 吞掉的采集异常按方法名计数 —— **空 = 采集全干净**。
            #    没有它，「采集坏了」和「本来就没数据」在报告里长得一模一样。
            "collector_errors": self._safe_bad,
            "events": self.events,
            "segments": segs,
        }
        out = str(self.stem) + ".report.json"
        with open(out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=1)
        if bundle:
            self.bundle_path = make_bundle(self.stem, out, self.session_path,
                                           audio_paths=getattr(self, "audio_files", []))
        return out
