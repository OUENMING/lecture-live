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
   整节课（含其他同学的声音）。音频**只写本机**（`sessions/` 同级)。
   （3.9.0 起定稿转写默认会把音频段发给云端 —— 那条路与这里的录音文件无关,见 `final_asr.py`。）
3. **落盘格式要能被脚本读。** 一个 JSON, 字段名直白, 不要嵌套太深。

采什么
------
*音频*  : 逐块累计峰值/RMS/波峰因数 → 电平分布、动态范围
*分段*  : 每段的时长、电平、波峰因数、VAD 判定、文本、ASR 耗时/
          用了哪个模型/是否回退/平均 logprob/词数/有无句末标点
*流水线*: 首字中文延迟（草稿 `lag_s` / 定稿 `translation_latency`）、定稿延迟、翻译失败、
          云端降级、退化回退次数
*资源*  : CPU 秒(整段 + 折算实时占比)、峰值内存
*能耗*  : 可见/不可见两档的能耗(J)/唤醒次数/CPU 秒/墙钟 + pump 各档时长（3.9.8 ①，`energy.py`）
*唤醒*  : `streamq` 每条 item 从 put 到 drain 的停留时间 p50/p90/p99/max（3.9.8 ②）
*补采*  : 跨段合并 / 课后补译的**原文**（3.9.8 ③）；收音断档与系统睡眠（3.9.8 ④）；
          字幕可见行数 / 档位行数 / 面板宽（3.9.8 ⑤）
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
   音频格式见 `AUDIO_SEG_S` / `AUDIO_OPUS_BITRATE`（分段 Opus 24k，约 9–13MB/50 分钟；后者是没 ffmpeg 时的 soundfile 兜底）。
"""
from __future__ import annotations

import atexit
import collections
import functools
import json
import os
import pathlib
import resource
import subprocess

import models
import upload as upload_mod
from asr import is_degenerate as _is_degenerate
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
#: soundfile 兜底的 Opus 档位。0.88 是 `RESEARCH-cloud-asr.md` §15 量过的那一档
#: （Groq WER 10.2% / 漏 0.4%，与 ffmpeg 24k 的 10.1% / 0.4% 打平）—— 这些录音要拿去做评测，音质不能再省。
AUDIO_SF_LEVEL = 0.88

# ---- 3.9.8 新增采集（六组）的常量 ----
#: ② 唤醒延迟：`streamq` 逐条停留时间的上限。一节课几千条 × 一个 float，几十 KB；
#: 封顶只为防「某条路把同一句重复投一万次」时内存失控。
DWELL_MAX = 50000
#: ③ merge / backfill 原文的条数上限（一节课触发次数远低于此；封顶防异常膨胀）。
TEXT_ITEMS_MAX = 300
#: ④ 相邻两块音频的墙钟间隔超过它就记一次**断档**。
CAPTURE_GAP_S = 1.0
#: ④ 断档列表的条数上限（总数与总时长不受它限制，始终如实累加）。
CAPTURE_GAPS_MAX = 50
#: ⑥ `by_idle` 的分界（毫秒）：`< 7 s` = 上一句刚翻完没多久（长连接多半还热着）；
#: `>= 7 s` = 已经隔了 macOS / httpx 默认 keepalive 之外的一段时间（连接可能已重开）。
IDLE_SPLIT_MS = 7000.0
#: ⑤ 面板宽度 p50 用的样本上限（每秒最多一个，一节课 ≤ ~3600 个）。
VIEW_W_MAX = 20000


def _sf_opus(wav: pathlib.Path, opus: pathlib.Path) -> str | None:
    """不靠 ffmpeg 把一段 wav 编成 Ogg/Opus。成功返回 None，失败返回一句原因（不抛）。
    分块读写：600 s 一段整读是 38 MB 的 float32，没必要。"""
    try:
        import soundfile as sf
        with sf.SoundFile(str(wav)) as fi, sf.SoundFile(
                str(opus), "w", samplerate=SR, channels=1, format="OGG", subtype="OPUS",
                compression_level=AUDIO_SF_LEVEL) as fo:
            for blk in fi.blocks(blocksize=SR * 10, dtype="float32"):
                fo.write(blk)
        return None
    except Exception as e:                               # noqa: BLE001
        return f"{type(e).__name__}: {str(e)[:100]}"

#: `TestSession.note_block()` 认的名字（见那个方法的 docstring）。
#: ⚠️ **多一格就在这儿加一行** —— 名字打错**不会**静默建键，会进 `events` 报一声。
#: 每一项都是「一整份快照」，与 `note_event` 的时间轴事件分工不同。
BLOCKS = frozenset({
    "norm",     # `capture.PeakNormalizer.stats()` —— 归一化**之前**的电平
    "vad",      # `vad.Segmenter.diag` —— 断句 / 12s 硬切 / 丢弃
    "live",     # `live_summary.LiveSummarizer.stats()` —— 窗口/章/Jev/积压
    "terms",    # 术语注入条数（core / always / scored）
    "ui",       # UI 使用度（纲要开了几次、点了哪些按钮）
    "backfill",  # 🩹 漏译句课后补译的计数 `{need, done}`（`backfill.backfill_entries`）
    "merge",    # 跨段半句合并的计数 `{triggered, replaced, failed}` + `items`（3.9.7 第 4 项 / 3.9.8 ③）
    "system",   # 机器画像 `{ram_gb, chip, macos, arch, draft_engine, draft_reason}`（`system_info`）
    "energy",   # ⭐ 3.9.8 ①：可见/隐藏分桶的能耗/唤醒/CPU + pump 各档时长（`energy.EnergyProbe`）
    "capture",  # ⭐ 3.9.8 ④：收音断档（墙钟 vs 音频秒）+ 断档列表（`note_chunk` 累计）
    "region_probe",  # ⭐ Soniox 多地区路由：探测结果 + 决策（`region_route.Router.snapshot()`）
})


def system_info(draft_name: str = "", draft_reason: str = "") -> dict:
    """机器画像（3.9.7）：内存 / 芯片 / 系统版本 + 实际用的草稿引擎与理由。**永不抛**。

    为什么要：草稿引擎按内存分档（`draft_engine.MIN_RAM_BYTES` = 12 GB），低内存机退回
    每秒重解整窗的 Parakeet —— 那是本地最耗电的一档。没有这块就答不了「用户里有没有
    低内存 Mac、值不值得为它优化」（2026-10-16：7 节用户报告里一条都查不到）。
    只有硬件型号与版本号，没有序列号 / 主机名 / 用户名。
    """
    import platform
    import subprocess
    out = {"draft_engine": draft_name or "", "draft_reason": draft_reason or ""}
    try:
        out["ram_gb"] = round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30, 1)
    except Exception:                                    # noqa: BLE001
        out["ram_gb"] = None
    try:
        out["chip"] = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"],
                                     capture_output=True, text=True, timeout=2).stdout.strip()
    except Exception:                                    # noqa: BLE001
        out["chip"] = ""
    try:
        out["macos"] = platform.mac_ver()[0]
        out["arch"] = platform.machine()
    except Exception:                                    # noqa: BLE001
        out["macos"] = out["arch"] = ""
    return out


def pcts(vals) -> dict | None:
    """一串数 → `{"p50": …, "p90": …}`；**一个数都没有 → `None`**。纯函数。

    ⚠️ 空的时候给 `None` 不给 `0`（同 `asr.final_empty_pct` 的规矩）：`0` 会被读成
       「延迟为零毫秒」，那是另一个意思。
    """
    if not vals:
        return None
    a = np.asarray(list(vals), dtype=float)
    return {"p50": round(float(np.percentile(a, 50)), 1),
            "p90": round(float(np.percentile(a, 90)), 1)}


def latency_summary(rows) -> dict:
    """逐句译文延迟 → `translation_latency` 汇总。纯函数（判据直接调它）。

    `rows` = `[{"zh_first_ms", "zh_done_ms", "seg_to_zh_ms", "model", "idle_before_ms"}, …]`
    （`note_translate` 收的），一条一句定稿。

    ⚠️ `n` = **真的等到过中文首字**的句数：断网 / 空译文那些不进 `n`、也不进
       `by_model` —— 报一个引擎名会让人以为它们翻出来了。
    ⚠️ `zh_done_ms` 只落在 `.live.jsonl` 的逐句行里，这里不汇总（第 3 项要的就是
       `zh_first_ms` / `seg_to_zh_ms` 两条：前者量翻译，后者是**用户真正等的时间**）。
    ⭐ 3.9.8 ⑥：`by_idle` 按「距上一次定稿翻译**结束**多久」把 `zh_first_ms` 分两桶
       （`< IDLE_SPLIT_MS` / `>=`）—— 答的是「60 s 长连接保活在真课上有没有省下首字延迟」。
       ⚠️ `idle_before_ms` 为 `None`（第一次调用 / 拿不到）的句子**不进任何一桶**：
          把它们塞进 `lt7s` 会把"首次冷启动"混进"刚翻过又翻"那一档。
    """
    first = [r.get("zh_first_ms") for r in rows if r.get("zh_first_ms") is not None]
    seg = [r.get("seg_to_zh_ms") for r in rows if r.get("seg_to_zh_ms") is not None]
    by_model = collections.Counter(r.get("model") or "" for r in rows)
    by_model.pop("", None)

    def _idle_bucket(r):
        v = r.get("idle_before_ms")
        if v is None:
            return None
        return float(v) < IDLE_SPLIT_MS

    lt = [r["zh_first_ms"] for r in rows
          if r.get("zh_first_ms") is not None and _idle_bucket(r) is True]
    ge = [r["zh_first_ms"] for r in rows
          if r.get("zh_first_ms") is not None and _idle_bucket(r) is False]
    return {"n": len(first),
            "zh_first_ms": pcts(first),
            "seg_to_zh_ms": pcts(seg),
            "by_idle": {"lt7s": pcts(lt), "ge7s": pcts(ge)},
            "by_model": dict(by_model.most_common())}


def dwell_summary(vals) -> dict | None:
    """`streamq` 每条 item 的停留时间（毫秒）→ `{n, p50, p90, p99, max}`。纯函数。

    回答 3.9.8 ② 那个问题：空闲档 0.1 s / 隐藏档 0.25 s 有没有把字幕上屏拖慢？
    ⚠️ 一个数都没有 → `None` 不给 `0`（同 `pcts` 的规矩）。
    """
    if not vals:
        return None
    a = np.asarray(list(vals), dtype=float)
    return {"n": int(a.size),
            "p50": round(float(np.percentile(a, 50)), 1),
            "p90": round(float(np.percentile(a, 90)), 1),
            "p99": round(float(np.percentile(a, 99)), 1),
            "max": round(float(a.max()), 1)}


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

    @functools.wraps(fn)          # 保留签名: 测试按 `inspect.signature` 钉调用方传的关键字(§X1)
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
    # R12：必需模型缺了要如实报 None；可选模型（Whisper 现在可选）**在盘上才报**。
    for _m in models.MODELS:
        d = models.basename_of(_m.key)
        if not d:                                         # 住在 ~/models 之外的（如 HF cache 里的 Qwen3）
            continue
        p = pathlib.Path(os.path.expanduser(models.MODELS_ROOT)) / d
        if not _m.required and not p.exists():
            continue
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
                     ("sherpa-onnx", "numpy", "sounddevice", "mlx-lm", "httpx", "soundfile")},
        "models_mb": models_sz,
    }


def make_bundle(stem: pathlib.Path, report_path: str | os.PathLike,
                session_path, audio_paths: list | None = None) -> str | None:
    """把一次测试打包成**一个文件**, 方便发给作者。

    ⚠️ **包里含隐私内容**: 课堂音频 + 完整逐字转录 —— 可能有其他同学的声音。
    所以包里放一份 README 说明里面是什么、以及它会被自动上传（`CLASSLIVE_UPLOAD=0` 关）。
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
  audio/         课堂音频，每 10 分钟一段 Opus（若含音频；转码失败时是 wav）

⚠️ 隐私提醒
------------
`session.md` 与 `audio/` 是**这节课的真实内容**，可能包含其他同学的声音
或个人信息。测试模式收尾会自动上传给作者；不想传就设 CLASSLIVE_UPLOAD=0。

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

    def __init__(self, session_path: pathlib.Path | None, record_audio: bool = False,
                 record_float: bool = False, wall_clock=time.time):
        self.t0 = time.monotonic()
        #: ④ 墙钟（`time.time`，**不是 monotonic**：macOS 的 monotonic 在系统睡眠时
        #:    不走，而这一格要抓的正是"睡过去了、中间没声音"）。可注入（判据伪造断档）。
        self._wall = wall_clock
        self.session_path = session_path
        self.stem = session_path.with_suffix("") if session_path else None
        self.record_audio = bool(record_audio) and self.stem is not None
        #: ⭐ 对**异常段**（主模型空/退化 → 走了回退）另留一份**送进 ASR 的那份原信号**
        #: （float32）。⚠️ 为什么需要它（2026-10-07 实测）：拿 **16-bit** wav 离线回放去
        #: 复现实时（float）流水线时，**量化本身就会改变 ASR 结果** —— 同一节 17 段里
        #: 9 段改口（`"So it's around it."` → `"Sound next."`）。量具自己成了变量。
        #: ⚠️ 默认值**由 `main.py` 定**：跟随 `--record-audio`（测试模式下它默认开），
        #:    另有 `--no-record-float` 可单独关 —— 卡片那颗「测试模式」开关只传
        #:    `--test-mode`，若这里默认关，这一格在真实使用里**永远采不到**。
        #:    它比 opus 更"原始"，但**不含新的内容类别**（同一段音频）。
        #: ⚠️ 它**不进上传**（`upload_files` 只列 report / opus / wav / md）—— 留本机实验用。
        self.record_float = bool(record_float) and self.stem is not None

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
            # ⭐ **崩溃标记：开录时就写**（不是 finish 时）—— 这样 `finish()` 跑不到
            #    那种情况（崩溃 / 强杀 / 关机）才留得下痕迹。
            #    ⚠️ 收尾会删它；**没删成 = 下一次启动 `upload.recover_pending` 会把
            #       这一节已录到的部分排进上传队列**（带 `crash_recovered: true`）。
            #    ⚠️ 没有它，「崩溃那一整类数据」全丢 —— 而崩溃现场恰恰最该看。
            try:
                self._pending_marker = pathlib.Path(
                    str(self.stem) + upload_mod.PENDING_TAIL)
                self._pending_marker.write_text(
                    json.dumps({"stem": self.stem.name, "pid": os.getpid(),
                                "started": time.strftime("%Y-%m-%d %H:%M:%S")},
                               ensure_ascii=False), encoding="utf-8")
            except Exception:                             # noqa: BLE001
                self._pending_marker = None
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
        #: ⭐ 逐句译文延迟（`note_translate` 收的）。⚠️ 存**原始数**而不是计数器：
        #:    p50 / p90 没法用累加算出来（同 `_block_peaks` 那条理由）。
        #:    一节几百句 × 4 个数字，几十 KB —— 收尾在 `finish()` 里汇成一行汇总。
        self._tr: list[dict] = []
        self._seg_no = 0
        self._cpu0 = self._cpu()
        self._rss_peak = 0

        # ---- ⭐ 3.9.8 新增采集的累加器（全部只在内存里长，收尾一次成型）----
        #: ② `streamq` 每条 item 的停留时间（ms）。p50/p90/p99 没法用累加算，所以存原始数。
        self._sq_ms: list[float] = []
        #: ③ 跨段合并 / 课后补译的**原文**（会话 .md 里本来就有同级的东西，隐私口径不变）。
        self._merge_items: list[dict] = []
        self._backfill_items: list[dict] = []
        #: ④ 收音断档（墙钟口径，见 `note_chunk`）。
        self._cap_first_wall: float | None = None
        self._cap_last_wall: float | None = None
        self._cap_gaps: list[dict] = []
        self._cap_gap_n = 0
        self._cap_gap_total = 0.0
        #: ⑤ 字幕视图按时间加权：各档可见行数 / 中文档位行数各累计了多少秒 + 面板宽样本。
        self._view_rows_s: dict[int, float] = {}
        self._view_lines_s: dict[int, float] = {}
        self._view_w: list[float] = []
        self._view_n = 0
        self._view_cur: tuple[int, int, float] | None = None
        self._view_t0: float | None = None
        #: ① 能耗分桶（`energy.EnergyProbe`）。建不起来 = `None`，报告里如实说"没采到"。
        self.energy = None
        try:
            import energy as _energy
            self.energy = _energy.EnergyProbe()
        except Exception:                                 # noqa: BLE001
            self.energy = None

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
        ⭐ ffmpeg 不在 / 失败 → **soundfile 兜底**（`_sf_opus`，2026-10-14 起）。
           之前这一步失败就原样传 wav：一位没装 ffmpeg 的测试者 7 节课传了 610.8 MB wav，
           同批走 opus 约 58 MB（10.5×）。soundfile + libsndfile 本来就打在 `.app` 里（定稿上云在用）。
           ffmpeg 仍然排第一：同一段 600 s 实测 ffmpeg 4.3 s / 1.74 MB，soundfile 15.8 s / 2.59 MB。
        """
        out, fallback = [], []
        for wav in list(self._wav_paths):
            opus = wav.with_suffix(".opus")
            why = ""
            try:
                pr = subprocess.run(
                    ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(wav),
                     "-c:a", "libopus", "-b:a", AUDIO_OPUS_BITRATE,
                     "-ac", "1", "-ar", str(SR), str(opus)],
                    capture_output=True, timeout=180)
                if pr.returncode == 0 and opus.exists() and opus.stat().st_size > 0:
                    out.append(opus)
                    wav.unlink(missing_ok=True)          # ⚠️ 转成了才删
                    continue
                why = f"rc={pr.returncode} {pr.stderr.decode('utf-8', 'ignore')[:100]}"
            except Exception as e:                       # noqa: BLE001
                why = f"{type(e).__name__}: {str(e)[:100]}"
            fallback.append((wav, opus, why))
        if fallback:
            # 慢的那条（~38× 实时）：分段之间互不相干，并行编（cffi 调 C 时放 GIL）。
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=min(4, len(fallback))) as ex:
                errs = list(ex.map(lambda f: _sf_opus(f[0], f[1]), fallback))
            for (wav, opus, why), err in zip(fallback, errs):
                ok = err is None and opus.exists() and opus.stat().st_size > 0
                self.events.append({
                    "t": round(time.monotonic() - self.t0, 2),
                    "kind": "transcode_soundfile" if ok else "transcode_failed",
                    "detail": f"{wav.name}: ffmpeg {why}" + ("" if ok else f" · soundfile {err}")})
                if ok:
                    out.append(opus)
                    wav.unlink(missing_ok=True)          # ⚠️ 转成了才删
                else:
                    opus.unlink(missing_ok=True)         # 半截的 opus 别留着冒充成品
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
        # ⭐ 3.9.8 ④：收音断档（墙钟）。⚠️ 用 `time.time` —— monotonic 在系统睡眠时不走，
        #    而"睡过去了、中间几十分钟没声音"正是这一格要抓的形态。
        self._capture_tick(self._wall())
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
    def _save_probe_float(self, i: int, audio) -> str:
        """异常段留一份原信号（float32）。失败返回空串 —— **绝不抛**。"""
        try:
            d = pathlib.Path(str(self.stem) + ".probe")
            d.mkdir(exist_ok=True)
            p = d / f"{i:03d}.npy"
            np.save(p, np.asarray(audio, dtype=np.float32))
            return str(p)
        except Exception:                                 # noqa: BLE001
            return ""

    @_safe
    def note_segment(self, audio, text: str, asr_ms: float, model: str,
                     logprob: float | None = None, fell_back: bool = False,
                     n_sentences: int = 0,
                     final_text: str | None = None,
                     final_degenerate: bool = False,
                     draft_text: str | None = None,
                     steps=(), trace: dict | None = None) -> None:
        """一段的定稿结果。`final_*` = **第一个被尝试的来源**（云端，或云端关着时的本地主模型）
        在兜底之前的原文与判决；`draft_text` = 真回退时**兜底模型**（Parakeet）的原文；
        都不是就传 `None`。`model` = 最终文本的来源（`FinalResult.source`）。

        R9：必须带 `@_safe` —— 它在 `main.final_worker` 的 try 里、排在 `_emit` 前，
        抛任何异常这一段都会被吞成「定稿失败」而**丢掉**。
        `steps` = 按发生顺序的每一步（`("cloud:timeout", "local:parakeet")`）；
        `trace` = `FinalResult.trace`，目前只用 `local_text`（R10）。

        ⭐ **`probe` 那一格为什么要加**（2026-10-07）：原来只记"最终用了哪个模型"一格 ——
        而那把**两条信息压成了一个**：「主模型空了几次」与「兜底模型空了几次」分不开。
        实际代价就是那次绕远路：我拿**兜底模型**离线跑，把它的空输出当成了"空转写"，
        而生产里主模型根本没空（落盘文本就是主模型给的），`vad_params.py` 那句
        「空转写 21%，这才是该查的方向」**量的是兜底模型 → 是个幽灵**。
        ⚠️ 这两格**是免费的**：主模型的结果本来就算出来了，兜底模型只在回退时才跑。
        ⚠️ 曾经的错误想法是"每段都多跑一次兜底模型来对照"—— 那要 +0.36s × 250 段。

        ⚠️ **float 快照的条件（R10）**：「最后一个本地主模型的结果空或退化」。
        不能再用 `fell_back` —— 它现在表示「想上云没上成」，整节课断网时每段都是真，
        会每段存一份原信号。
        """
        a = np.abs(audio) if len(audio) else np.zeros(1)
        rms = float(np.sqrt(np.mean(np.asarray(audio, dtype=np.float64) ** 2))) \
            if len(audio) else 0.0
        _local = (trace or {}).get("local_text")
        _local_bad = _local is not None and (not _local.strip() or _is_degenerate(_local))
        _probe_saved = self._save_probe_float(self._seg_no, audio) \
            if (self.record_float and _local_bad) else ""
        self.segments.append({
            "i": self._seg_no,
            "t": round(time.monotonic() - self.t0, 2),
            "dur_s": round(len(audio) / SR, 2),
            "level_dbfs": round(20 * np.log10(max(rms, 1e-9)), 1),
            "crest_db": round(20 * np.log10(max(float(a.max()), 1e-9) / max(rms, 1e-9)), 1),
            "asr_ms": round(asr_ms, 1),
            "model": model,
            "fell_back": bool(fell_back),
            "steps": list(steps),
            "logprob": round(logprob, 3) if logprob is not None else None,
            "words": len(text.split()),
            "end_punct": bool(text.rstrip().endswith((".", "!", "?", "…"))) if text else None,
            "empty": not bool(text.strip()),
            "n_sentences": n_sentences,
            "text": text,
            "probe": {
                "final_text": final_text,
                "final_empty": None if final_text is None else not bool(final_text.strip()),
                "final_degenerate": bool(final_degenerate),
                "draft_text": draft_text,
                "draft_empty": None if draft_text is None else not bool(draft_text.strip()),
                "float_saved": _probe_saved,
            },
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
    def note_translate(self, zh_first_ms=None, zh_done_ms=None, seg_to_zh_ms=None,
                       model: str = "", idle_before_ms=None) -> None:
        """⭐ 一句定稿的**译文延迟**（3.9.7 第 3 项）—— 只记数字与引擎名，不记文本。

        口径见 `tr_latency` 的文件头：`zh_first_ms` / `zh_done_ms` 从"英文送进翻译"
        算起，`seg_to_zh_ms` 从那**一段音频结束**算起（后者才是用户真正等的时间），
        `model` = 实际出译文的引擎（云端模型名 / `local`；没出中文 = 空串）。
        ⭐ 3.9.8 ⑥：`idle_before_ms` = 这次定稿翻译**开始时**距上一次翻译**结束**多久
           （第一次 = `None`）。它决定 `translation_latency.by_idle` 分桶 ——
           ⚠️ **不落 `.live.jsonl`**（那是 `tr_latency` 的 4 元组，形状一个字没动）。

        ⚠️ 是**一句一条**的原始数（不是计数器）：收尾在 `finish()` 里汇成
           `translation_latency` 的 p50 / p90。这里只在内存里 append，**不落盘**。
        """
        self._tr.append({"zh_first_ms": zh_first_ms, "zh_done_ms": zh_done_ms,
                         "seg_to_zh_ms": seg_to_zh_ms, "model": model,
                         "idle_before_ms": idle_before_ms})

    @_safe
    def note_streamq_dwell(self, ms: float) -> None:
        """⭐ 3.9.8 ②：`streamq` 一条 item 从 `put` 到被 `drain()` 取出的停留时间（毫秒）。

        ⚠️ 由 `_WakeQueue` 在自己**装了 stats 回调**时才调（没装 = 连时间戳都不记）。
        ⚠️ 只 append 原始数（p50/p90/p99 算不出来），像 `note_translate` 一样在内存里长。
        """
        if len(self._sq_ms) < DWELL_MAX:
            self._sq_ms.append(float(ms))

    @_safe
    def note_view(self, rows: int, lines: int, width: float) -> None:
        """⭐ 3.9.8 ⑤：字幕视图的一次采样（视口可见行数 / 中文档位行数 / 面板宽）。

        ⚠️ `overlay` 在 pump 里**每秒最多采一次**（它自己限流）；这里按**时间加权**
           把「上一次采样到这一次」的秒数记进上一份样本，所以累计秒数 ≈ 该档位在屏上的时长。
        """
        now = time.monotonic()
        self._view_close(now)
        self._view_cur = (int(rows), int(lines), float(width))
        self._view_t0 = now
        self._view_n += 1
        if len(self._view_w) < VIEW_W_MAX:
            self._view_w.append(float(width))

    @_safe
    def note_merge_item(self, job: dict, en: str, zh: str, replaced: bool) -> None:
        """⭐ 3.9.8 ③：一次跨段半句合并的**原文**（触发的那一刻 + 重翻结果）。

        `job` = `fragment_merge.MergeTracker.on_line` 的产物（含 `tail_en/tail_zh/
        next_en/next_zh/gap_s/trigger`）；`en`/`zh` = 重翻后的合并文本（失败 = 空串）；
        `replaced` = 屏上那两行**真的**被换掉了（`False` = 重翻失败 / 行已被别的路径改过）。
        ⚠️ 上限 `TEXT_ITEMS_MAX` 条（一节课触发次数远低于此；封顶防异常膨胀）。
        """
        if len(self._merge_items) >= TEXT_ITEMS_MAX:
            return
        self._merge_items.append({
            "t": round(time.monotonic() - self.t0, 2),
            "gap_s": None if job.get("gap_s") is None else round(float(job["gap_s"]), 3),
            "trigger": str(job.get("trigger") or ""),
            "tail_en": str(job.get("tail_en") or ""),
            "tail_zh": str(job.get("tail_zh") or ""),
            "next_en": str(job.get("next_en") or ""),
            "next_zh": str(job.get("next_zh") or ""),
            "merged_en": str(en or ""),
            "merged_zh": str(zh or ""),
            "replaced": bool(replaced),
        })

    @_safe
    def note_backfill_items(self, items) -> None:
        """⭐ 3.9.8 ③：课后补译**真补出来**的条目 `[{en, zh}, …]`（上限 `TEXT_ITEMS_MAX`）。

        ⚠️ 传进来的只该是 `backfill_entries` **换掉的那些**（`is not` 旧对象）——
           "补了哪几句、补成什么样"是这一格的全部意义，别把整节课的 entries 塞进来。
        """
        for it in (items or []):
            if len(self._backfill_items) >= TEXT_ITEMS_MAX:
                return
            self._backfill_items.append({"en": str((it or {}).get("en") or ""),
                                         "zh": str((it or {}).get("zh") or "")})

    # ---- ⭐ 3.9.8 ①④⑤：overlay 注入的钩子（都在 AppKit 主线程上，必须 O(1)）----
    @_safe
    def on_vis(self, visible: bool) -> None:
        """⭐ ① 窗口可见性刚变了（`overlay._refresh_visibility` 注入）。**绝不抛**。

        ⚠️ 同时收口 ⑤ 的视图计时：不可见期间**不该**算进任何"行数档位"的时长里。
        """
        if self.energy is not None:
            self.energy.on_vis(visible)
        self._view_close(time.monotonic())

    @_safe
    def add_wait(self, tier: str, seconds: float) -> None:
        """⭐ ① pump 一档的**实际**等待时长（`overlay._yield_to_runloop` 注入）。"""
        if self.energy is not None:
            self.energy.add_wait(tier, seconds)

    def _capture_tick(self, w: float) -> None:
        """④ 一块音频到达：记第一块时刻、算与上一块的墙钟间隔。纯记账（被 `note_chunk` 包着）。"""
        if self._cap_first_wall is None:
            self._cap_first_wall = w
        elif self._cap_last_wall is not None and (w - self._cap_last_wall) > CAPTURE_GAP_S:
            gap = w - self._cap_last_wall
            self._cap_gap_n += 1
            self._cap_gap_total += gap
            if len(self._cap_gaps) < CAPTURE_GAPS_MAX:
                self._cap_gaps.append({"t_wall_offset_s": round(w - self._cap_first_wall, 3),
                                       "gap_s": round(gap, 3)})
        self._cap_last_wall = w

    def _view_close(self, now: float | None = None) -> None:
        """⑤ 把「上一次采样 → 现在」的秒数记进上一次那份样本（时间加权）。**绝不抛**。"""
        cur = self._view_cur
        if cur is None:
            return
        if now is None:
            now = time.monotonic()
        dt = now - (self._view_t0 if self._view_t0 is not None else now)
        rows, lines, _w = cur
        if dt > 0:
            self._view_rows_s[rows] = self._view_rows_s.get(rows, 0.0) + dt
            self._view_lines_s[lines] = self._view_lines_s.get(lines, 0.0) + dt
        self._view_cur = None
        self._view_t0 = None

    def _view_block(self) -> dict:
        """⑤ 视图块（进 `blocks.ui.view`）。宽度 p50 用 `np.median`。"""
        out = {"samples": self._view_n,
               "rows_s": {str(k): round(v, 1) for k, v in sorted(self._view_rows_s.items())},
               "lines_s": {str(k): round(v, 1) for k, v in sorted(self._view_lines_s.items())},
               "width_p50": (round(float(np.median(np.asarray(self._view_w, dtype=float))), 1)
                             if self._view_w else None)}
        return out

    def energy_block(self, screen_hz: float | None = None) -> dict:
        """① 能耗块（收尾调一次）。**永不抛** —— 采不到只给 `{available: false, reason}`。"""
        if self.energy is None:
            return {"available": False, "reason": "rusage 探测建不起来"}
        try:
            return self.energy.block(screen_hz)
        except Exception as e:                            # noqa: BLE001
            return {"available": False, "reason": f"{type(e).__name__}: {str(e)[:80]}"}

    def capture_block(self) -> dict:
        """④ 收音断档块（收尾调一次）。**永不抛**。"""
        try:
            first = self._cap_first_wall
            if first is None:
                return {"available": False, "reason": "一块音频都没收到"}
            now = self._wall()
            wall = max(0.0, now - first)
            audio_s = self._n_samples / SR
            return {
                "available": True,
                "wall_s": round(wall, 1),
                "audio_s": round(audio_s, 1),
                #: 墙钟比实际音频长了多少 —— > ~0.2 s/块 就是"中间没在听"。
                "deficit_s": round(wall - audio_s, 1),
                "gaps": {"n": self._cap_gap_n, "total_s": round(self._cap_gap_total, 1),
                         "items": list(self._cap_gaps)},
                "first_block_epoch": round(first, 3),
                "last_block_epoch": (None if self._cap_last_wall is None
                                     else round(self._cap_last_wall, 3)),
            }
        except Exception as e:                            # noqa: BLE001
            return {"available": False, "reason": f"{type(e).__name__}: {str(e)[:80]}"}

    @_safe
    def note_ui(self, name: str) -> None:
        """⭐ **UI 使用度**（采集面 ⑦）：某个交互被用了几次。

        名字由 `overlay` 那边给（`submit` / `ask` / `new_topic` / `lost` /
        `outline` / `trans_mode` / `quit` / `hide` / `show` / `on_top` / `not_on_top` / `reopen` / `edge_tab`，
        以及 3.9.7 文字稳定包那两个：`draft_append` / `draft_rewrite` ——
        草稿更新里「纯追加」与「改了前面已显示的词」各多少次，用来量逐词淡换的收益）。

        ⚠️ **只自增一个计数，不 append 事件** —— 它跑在 AppKit **主线程回调**里，
           而按钮可以连点几十下。「每次一个事件」会把 `events` 撑爆、
           也会把真正的时间轴事件挤没（同 `_safe` 那条防刷屏的理由）。
        ⭐ 用途：**功能有没有被用**。纲要从没被打开过 = 最大的产品信号，
           而在这之前**一条数据都没有**。
        """
        self._ui[name] = self._ui.get(name, 0) + 1

    @_safe
    def note_ui_value(self, name: str, value) -> None:
        """往 UI 使用度里放一个**快照值**（不是计数）—— 例：收尾时的译文超限统计。"""
        self._ui[name] = value

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
        #    ⚠️ 转码失败留下的 wav **也要列进去**（Worker 收 `audio.NNN.wav`）——
        #       不然没装 ffmpeg 的机器上只传得上报告和转录，音频**静默丢**。
        self.audio_files = self._to_opus() if self.record_audio else []
        if self.record_audio:
            self.audio_files += [w for w in self._wav_paths if w.exists()]
        # ⭐ 收尾走到了 —— 撤掉崩溃标记（`recover_pending` 靠它认「这一节没收尾」）。
        _mk = getattr(self, "_pending_marker", None)
        if _mk is not None:
            try:
                _mk.unlink(missing_ok=True)
            except Exception:                             # noqa: BLE001
                pass

        dur = time.monotonic() - self.t0
        cpu = self._cpu() - self._cpu0
        rms_all = float(np.sqrt(self._sum_sq / max(self._n_samples, 1)))
        segs = self.segments
        n = max(len(segs), 1)

        def pct(cond) -> float:
            return round(sum(1 for s in segs if cond(s)) / n * 100, 1)

        def ppct(field: str):
            """**主模型（Whisper）那一侧**的比例 —— 这才是「空转写」的正确口径。

            ⭐ 2026-10-07：原来只有「最终文本空没空」那一格，而它是**两个模型混在一起**
            的结果（主模型空/退化时才轮到兜底模型）→ 分不出是谁空的。那次绕远路就是
            这么来的：拿**兜底模型**离线跑，把它的空当成了"空转写"。
            取不到（老调用方没传 `probe`）时返回 `None` —— **不许当成 0**。
            """
            vals = [(s.get("probe") or {}).get(field) for s in segs]
            vals = [v for v in vals if v is not None]
            return round(sum(1 for v in vals if v) / len(vals) * 100, 1) if vals else None

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
        # ⭐ 3.9.8：新采的那些格落在**已有的块**里（形状与既有的 `zh_overflow` / `hidden_s`
        #    一致），不另建顶层键 —— `block_names` 那套"该有哪几块"的清单因此不用动形状。
        self._view_close()
        _ui = self._extra.get("ui")
        if isinstance(_ui, dict):
            _ui.setdefault("streamq_dwell_ms", dwell_summary(self._sq_ms))
            _ui.setdefault("view", self._view_block())
        _mg = self._extra.setdefault("merge", {})
        if isinstance(_mg, dict):
            _mg.setdefault("items", list(self._merge_items))
        _bf = self._extra.setdefault("backfill", {})
        if isinstance(_bf, dict):
            _bf.setdefault("items", list(self._backfill_items))
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
                # ⭐ 主模型那一侧（见 `ppct`）：`empty_pct` 是**两模型混合**的结果，这两格才分得开
                "final_empty_pct": ppct("final_empty"),
                "final_degenerate_pct": ppct("final_degenerate"),
                "no_end_punct_pct": pct(lambda s: s["words"] and not s["end_punct"]),
                # 语义(定稿上云后): 「想上云但最终没用上云的文本」(网络 / 熔断 / 退化 / 云端空串被补救)。
                "fell_back_pct": pct(lambda s: s["fell_back"]),
                # 最终文本来自谁(groq / qwen / local-whisper / local-parakeet / none)
                "source_pct": {k: round(v / n * 100, 1) for k, v in
                               collections.Counter(s["model"] for s in segs).items()},
                # 每一步出现的段数(同一段里同一步只算一次): cloud:timeout / local:parakeet / degenerate …
                "steps_counts": dict(collections.Counter(
                    st for s in segs for st in set(s.get("steps") or ()))),
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
            # ⭐ 译文延迟（3.9.7 第 3 项，逐句数见 `note_translate`）。
            #    ⚠️ 放这儿（与 `correction` 并列）—— 两格都是**逐句聚合**。
            "translation_latency": latency_summary(self._tr),
            "notes": [
                "logprob 只有 **Parakeet** 会填 —— Whisper 的 result.ys_log_probs 是空的。"
                "云端来源(groq / qwen)也没有 logprob; 定稿主要走云端时这个字段通常是 null, 不是采集失败。",
                "cpu_pct_of_walltime 在 --speed>1 的回放里会被放大;"
                "跨机器/跨速度比请用 cpu_s_per_audio_s。",
                "no_end_punct_pct 在 n<30 段时噪声很大(实测底线 ±18 个百分点),"
                "单节课的数字不要当结论。",
                "translation_latency.n 只数**真的等到过中文首字**的句子: 断网 / 空译文"
                "那些不进 n 也不进 by_model; seg_to_zh_ms 从该段音频结束(VAD 切段)"
                "算起, 含定稿 ASR + 排队 —— zh_first_ms 只量翻译那一段。",
                "blocks.energy: 分桶的**时间**用 monotonic, 报告的墙钟用 time.time;"
                "billed_energy 那三个计数器是**只读**系统调用, 实测有些机器在一段忙循环里"
                "根本不涨(0) —— 0 是「这一档没记到能量」, 不是「没耗电」。cpu_s 可拿"
                "resource.getrusage 对拍。",
                "blocks.capture.deficit_s = 墙钟 - 实际音频秒; 文件回放(--speed≠1) 或"
                "断档两者都会把它推大, 断档明细看 gaps.items。系统/屏幕睡眠进 events。",
                "blocks.ui.streamq_dwell_ms = streamq 每条 item 从 put 到 drain 的停留"
                "时间(唤醒延迟); 空闲档 0.1 s / 隐藏档 0.25 s 若把字幕拖慢, 这里会看到 p90/p99 变大。",
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
