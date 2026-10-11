#!/usr/bin/env python3
"""合成「模拟真实大学课堂」的测试语料：真课语音 × 实测房间 RIR × 观众噪声 × 短时高噪事件。

配方照 CHiME / REVERB 那一系来（出处见 `docs/experiments/classroom_sim_20261011/README.md`）：
**干净语音 × 实测 RIR + 真实录的噪声，按目标 SNR 混**。和论文里唯一的差别：
我们的"干净语音"**已经是真课录音**（自带那个房间的混响与麦克风），所以这里加的是
**再叠一层**的传播与噪声 —— 报告里必须写明，否则数字会被读成"从零模拟"。

三条正交的轴（要哪条就动哪条，别混着动）：
  · **场景 scene** —— `hall`（阶梯教室）/ `seminar`（小班·研讨室）。差在**用哪个实测 RIR**与哪批底噪。
  · **座位 position** —— front / mid / back。**每个座位用一条真实测距下录的 RIR**
    （AIR 数据库的 `lecture` / `meeting` 房间，同一房间内 6 / 5 个距离档），
    直混比来自 RIR 实测；另加**自由场距离衰减**（−20log10(d/d_front)，上限 −18 dB）——
    加它是因为 AIR 每条 RIR 各自归一化过，绝对电平关系丢了，而后排**信噪比必须跟着掉**。
  · **噪声档 level** —— 按**前排未衰减**的语音活跃功率定标：`quiet` / `snr20` / `snr10` / `snr0`。
    实测扫出来的是 **+23.9…−3.3 dB**（`snr_added_db`；每份文件都实测并写进 manifest）。
    ⚠️ 别按 −20log10(d) 外推座位衰减：座位链叠的混响尾巴把自由场衰减抵掉了大半，
    前排到后排的实测 SNR 只差 ~4 dB（`att_db` 却是 0 / −7.2 / −12.6）。

用法（仓库根、App 自带 python）：
    ClassLive.app/Contents/MacOS/python scripts/eval_corpus/make_classroom_sim.py [--out DIR] [--scene hall]
先跑 `fetch_classroom_sim.py` 把素材下齐；本脚本只读素材、只写 `mix/`。
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import random
import re
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import acoustics as A  # noqa: E402
from capture import load_file  # noqa: E402  ⭐ 解码只此一份（生产那份）

BASE = pathlib.Path(os.path.expanduser("~/.classlive/eval/classroom_sim"))
SR = A.SR
CLIP_S = 300.0            # 每份 5 分钟：够到 ~800–1000 词，WER 的标准误约 1%

# ---------- 三个轴 ----------
# RIR 全部取自 OpenSLR SLR28 里的 **AIR 实测** 部分（`real_rirs_isotropic_noises/`）：
#   · `lecture` 房间：RT60 实测 ~0.86 s（演讲厅的常见区间 0.7–1.0 s），6 个距离档 2.4→10.0 m
#   · `meeting` 房间：RT60 实测 ~0.30 s（小房间，ANSI S12.60 上限 0.6 s），5 个距离档 1.6→3.0 m
# 为什么不用 `simulated_rirs/`：那是仿真鞋盒房间，而这里**有实测的**，没理由退而求其次。
# 距离不写死：由 RIR 里直达声的到达时刻反推（÷343 m/s），实测值进 manifest。
SCENES = {
    "hall": {
        "src": "MIT18.06_lec01",      # Strang 18.06 第 1 讲，26-100 大阶梯教室
        "start": 600.0,
        "room": "lecture",
        # ⚠️ 用 `_0_` 而不是 `_1_` 系列：`lecture_1_1` 那条实测 RT60 3.87 s，是 AIR 里的
        # 离群值（同房间其余 10 条都在 0.79–0.94 s）。挑 RIR 要**条条量过再挑**。
        "rir": {"front": "air_type1_air_binaural_lecture_0_1.wav",
                "mid": "air_type1_air_binaural_lecture_0_3.wav",
                "back": "air_type1_air_binaural_lecture_0_6.wav"},
        "beds": ["bed_hall_murmur", "bed_hall_large_echo", "bed_hall_chatter30",
                 "bed_hall_dining_us", "bed_hall_expectant", "bed_hall_700", "bed_hall_3people"],
        "tones": ["tone_rvb2014_1", "tone_strip_light"],   # 真·大房间本底噪声（REVERB-2014）+ 电器嗡
    },
    "seminar": {
        "src": "BMM_lec1-1",          # MIT RES.9-003 暑期学校，小报告厅、提问多
        "start": 600.0,
        "room": "meeting",
        "rir": {"front": "air_type1_air_binaural_meeting_1_1.wav",
                "mid": "air_type1_air_binaural_meeting_1_3.wav",
                "back": "air_type1_air_binaural_meeting_1_5.wav"},
        "beds": ["bed_sem_chatter8", "bed_sem_party", "bed_sem_lab",
                 "bed_sem_classroom", "bed_sem_library"],
        "tones": ["tone_strip_light", "tone_fridge"],
    },
}
# 距离衰减：自由场 −20log10(d/d_front)，封顶 −18 dB。⚠️ 这是**模型**不是实测：
# 真实房间里超过临界距离后声压级不再按 6 dB/倍距掉（混响场撑着）。写在这里备查。
ATT_CAP_DB = -18.0

# 噪声档：相对**前排语音活跃功率**的全局 SNR（dB）。None = 只保留素材本身的底噪。
LEVELS = {"quiet": None, "snr20": 20.0, "snr10": 10.0, "snr0": 0.0}

# 短时高噪事件：**相对底噪 RMS 的峰值电平**（dB）。定的是**峰值**不是 RMS ——
# 事件的杀伤力在瞬时：一声椅子响的 RMS 被它前后的安静摊平，按 RMS 定标会把它定得过响
# （踩过这个坑：按 RMS +16 dB 时实测事件峰值冲到语音之上 +23 dB，等于在教室里放炮）。
# 相对底噪定标而不是相对语音：整条噪声（底噪+事件）最后会被**整体**缩放到目标 SNR
# （CHiME/MUSAN 那套"把噪声混到指定 SNR"的标准做法）→ 事件与底噪的**质感比例**在各档之间
# 保持一致，档位只改音量；实测 SNR 与实测事件峰值都写进 manifest。
BURSTS = {
    "burst_applause250": 12.0, "burst_applause50": 9.0, "burst_slowclap": 6.0,
    "burst_laugh_crowd": 10.0, "burst_laugh_hearty": 9.0, "burst_laugh_students": 9.0,
    "burst_laugh6": 8.0, "burst_gasp36": 6.0, "burst_hooray": 10.0,
    "burst_boo": 10.0, "burst_dispersing": 2.0,
    "burst_cough_male": 6.0, "burst_sneeze_man": 5.0, "burst_whisper": 0.0,
    "burst_chair_squeak": 7.0, "burst_chairs_fall": 9.0, "burst_chairs_setup": 3.0,
    "burst_desk": 5.0, "burst_door_scrape": 6.0, "burst_paper_rustle": -2.0,
    "burst_classroom_scrape": 4.0, "burst_bell_lesson": 8.0, "burst_dining_crockery": 0.0,
}
BED_RMS_DB = -30.0       # 底噪统一到这个 RMS，事件才有固定参照
BURST_MAX_S = 3.0        # 事件最长取 3 s（掌声/笑声可以长，椅子一声就短）
BURST_MEAN_GAP_S = 22.0  # 平均每隔多久来一次；5 分钟约 12–14 次


# ---------- 素材读取 ----------
def _read(path: pathlib.Path) -> np.ndarray:
    return load_file(str(path))


def _loop_to(x: np.ndarray, n: int, fade_s: float = 0.5) -> np.ndarray:
    """把一段素材首尾交叉淡化地铺满 n 个采样点（底噪要连续，接缝处不能有咔哒）。"""
    if x.size == 0:
        return np.zeros(n, dtype=np.float32)
    f = max(1, int(fade_s * SR))
    if x.size <= f * 2:
        return np.tile(x, n // x.size + 1)[:n]
    head, tail = x[:f].copy(), x[-f:].copy()
    w = np.linspace(0.0, 1.0, f, dtype=np.float32)
    x = x.copy()
    x[:f] = head * w + tail * (1.0 - w)          # 尾部淡入头部 → 循环处听不出接缝
    if x.size >= n:                              # 本来就够长：铺一份就够，别再多分配
        return x[:n]
    return np.tile(x, n // x.size + 1)[:n]


def _burst_window(x: np.ndarray, max_s: float) -> np.ndarray:
    """取这条音效里**最响的那一段**（事件多半就在那儿），并加 30 ms 淡入淡出。

    窗口和的算法：`cumsum(x²)` 上两个前缀相减 = O(N)。⚠️ 别写成
    `np.convolve(x², ones(hop)/hop)` —— 那是**直接法**卷积 O(N·800)，实测慢 35×
    （5.8M 采样点的 `burst_dining_crockery` 一次要 1 s）。
    """
    n = min(x.size, int(max_s * SR))
    if x.size <= n:
        seg = x
    else:
        hop = int(0.05 * SR)
        c = np.concatenate(([0.0], np.cumsum(x.astype(np.float64) ** 2)))
        starts = np.arange(0, x.size - n + 1, hop)
        sums = c[starts + n] - c[starts]
        bi = int(starts[int(np.argmax(sums))])
        seg = x[bi:bi + n]
    seg = np.array(seg, dtype=np.float32)
    f = int(0.03 * SR)
    if seg.size > 2 * f:
        seg[:f] *= np.linspace(0, 1, f, dtype=np.float32)
        seg[-f:] *= np.linspace(1, 0, f, dtype=np.float32)
    return seg


def _rms_db(x: np.ndarray) -> float:
    return float(10.0 * np.log10(np.mean(x.astype(np.float64) ** 2) + 1e-20))


def _scale_to_db(x: np.ndarray, target_db: float) -> np.ndarray:
    return (x * 10.0 ** ((target_db - _rms_db(x)) / 20.0)).astype(np.float32)


def _scale_peak_to_db(x: np.ndarray, target_db: float) -> np.ndarray:
    peak = float(np.max(np.abs(x))) if x.size else 0.0
    return (x * 10.0 ** ((target_db - 20.0 * np.log10(peak + 1e-20)) / 20.0)).astype(np.float32)


# ---------- RIR ----------
def load_rir(root: pathlib.Path, name: str) -> tuple[np.ndarray, dict]:
    """读一条实测 RIR，量出 RT60 / DRR / **直达距离**，返回 (尾巴, 实测值)。

    距离 = 直达声到达时刻 × 343 m/s。⚠️ 绝对量里可能含测量链路的固定时延，
    所以**只在同一房间的几条之间比**（我们做的正是这件事：同一房间的 front/mid/back）。
    """
    p = root / "RIRS_NOISES" / "real_rirs_isotropic_noises" / name
    if not p.exists():
        raise SystemExit(f"缺 RIR：{p}（先跑 fetch_classroom_sim.py）")
    h = load_file(str(p))
    st = A.rir_stats(h)
    peak = int(np.argmax(np.abs(h)))
    st["dist_m"] = round(peak / SR * 343.0, 2)   # 直达声到达时刻 × 声速
    st["file"] = name
    return _tail(h), st


def _tail(h: np.ndarray, skip_ms: float = 8.0) -> np.ndarray:
    """去掉 RIR 的直达声，只留混响尾巴（直混比要单独控制，所以把两者分开用）。

    切点取 `max(固定 8 ms, 主峰 + 2 ms)` 而不是写死 8 ms：SLR28 里有的 RIR 是
    远距离测的，直达声要十几毫秒才到 —— 写死 8 ms 会把直达声留在"尾巴"里。
    """
    k = max(int(skip_ms / 1000.0 * SR), int(np.argmax(np.abs(h))) + int(0.002 * SR))
    t = h[k:].astype(np.float64)
    e = float(np.sqrt(np.sum(t ** 2)))
    return (t / e if e > 0 else t).astype(np.float32)


def _conv(x: np.ndarray, h: np.ndarray) -> np.ndarray:
    n = len(x) + len(h) - 1
    nfft = 1 << (n - 1).bit_length()
    y = np.fft.irfft(np.fft.rfft(x, nfft) * np.fft.rfft(h, nfft), nfft)[:n]
    return y.astype(np.float32)


def apply_position(x: np.ndarray, tail: np.ndarray, *, drr_db: float, att_db: float) -> np.ndarray:
    """座位链：原始录音（含它自己的房间）= 直达；叠一层该座位的实测 RIR 尾巴 = 湿声。

    DRR 的定义在这里是 **原始信号能量 / 叠加尾巴的能量**（不是教科书里"直达声/混响"）——
    源素材本身已经是混响录音，"纯直达"根本拿不到。报告按这个口径写。
    """
    wet = _conv(x, tail)[:len(x)]
    if len(wet) < len(x):
        wet = np.pad(wet, (0, len(x) - len(wet)))
    ratio = 10.0 ** (-drr_db / 20.0)
    y = x + (ratio * wet).astype(np.float32)
    if att_db:
        y = y * 10.0 ** (att_db / 20.0)
    return y.astype(np.float32)


# ---------- 噪声 ----------
def build_noise(scene: dict, dur_s: float, sfx: pathlib.Path, tail: np.ndarray,
                rng: random.Random) -> tuple[np.ndarray, list[dict], float]:
    """铺底（人群 + 电器/房间本底）走房间尾巴，短时事件**不走**（近处的一声椅子响不是扩散场）。

    返回 (噪声信号, 事件清单, 底噪单独看时的 RMS(dBFS))。事件清单进 manifest ——
    事后能一眼看出某一份里到底响了哪几声；底噪 RMS 用来算"把事件抠掉之后底噪自己是多大"，
    因为**事件会把整条噪声的 RMS 抬起来**，只报一个总 SNR 会让人以为底噪就是那个电平。
    """
    n = int(dur_s * SR)
    bed = np.zeros(n, dtype=np.float32)
    for name in scene["beds"]:
        # 每条底噪随机取 0…−6 dB，叠出来才不像单条循环
        bed += _scale_to_db(_loop_to(_read(sfx / "bed" / f"{name}.wav"), n),
                            -20.0 + rng.uniform(-6.0, 0.0))
    for name in scene["tones"]:
        bed += _scale_to_db(_loop_to(_read(sfx / "tone" / f"{name}.wav"), n), _rms_db(bed) - 12.0)
    wet = _conv(bed, tail)[:n]
    if len(wet) < len(bed):
        wet = np.pad(wet, (0, len(bed) - len(wet)))
    bed = _scale_to_db(bed + 0.8 * wet, BED_RMS_DB)
    bed_rms = _rms_db(bed)          # ⚠️ 必须在加事件**之前**取：事件会把整条的 RMS 抬起来

    # 事件排程：泊松间隔（真事件不等距），避开开头 5 s
    events: list[dict] = []
    cut: dict[str, np.ndarray] = {}     # 同一段音效被抽中多次时只解码/裁剪一次（实测最多 26 次→20 次）
    t = 5.0
    while t < dur_s - BURST_MAX_S:
        t += rng.expovariate(1.0 / BURST_MEAN_GAP_S)
        if t >= dur_s - BURST_MAX_S:
            break
        name = rng.choice(list(BURSTS))
        if name not in cut:
            cut[name] = _burst_window(_read(sfx / "burst" / f"{name}.wav"), BURST_MAX_S)
        seg = _scale_peak_to_db(cut[name], BED_RMS_DB + BURSTS[name])
        i = int(t * SR)
        j = min(n, i + len(seg))
        bed[i:j] += seg[: j - i]
        events.append({"t": round(t, 2), "name": name, "peak_rel_bed_db": BURSTS[name],
                       "len_s": round(len(seg) / SR, 2)})
    return bed, events, bed_rms


def require_sfx(base: pathlib.Path, scenes: list[str]) -> None:
    """开跑前把「这次要用到的每条音效都在不在」一次查清。

    为什么要有这一步：加载处原来是 `if not p.exists(): continue` —— 素材没下全时
    它会**安静地产出一份没有底噪、或 0 事件的语料**，而那份语料看起来完全正常。
    少一条就报错，把缺的名单全列出来。
    """
    need: list[tuple[pathlib.Path, str]] = []
    for sc in scenes:
        cfg = SCENES[sc]
        need += [(base / "sfx" / "bed" / f"{n}.wav", f"{sc}.beds") for n in cfg["beds"]]
        need += [(base / "sfx" / "tone" / f"{n}.wav", f"{sc}.tones") for n in cfg["tones"]]
    need += [(base / "sfx" / "burst" / f"{n}.wav", "BURSTS") for n in BURSTS]
    need += [(base / "sources" / f"{SCENES[sc]['src']}.{e}", sc)
             for sc in scenes for e in ("mp4", "srt")]
    missing = [f"{p.relative_to(base)}（{who}）" for p, who in need if not p.exists()]
    if missing:
        raise SystemExit("缺素材（先跑 fetch_classroom_sim.py）：\n  " + "\n  ".join(missing))


# ---------- 参考文本 ----------
def srt_to_text(path: pathlib.Path, start: float, dur: float) -> str:
    """把人工字幕里落在 [start, start+dur) 的 cue 拼成一行纯文本（WER 的参考）。

    ⚠️ 保留数字/缩写原样，`stream_metrics.normalize` 那边会统一大小写与标点 ——
    这里不要自作主张改写，否则两边口径不一致，WER 就没法跟别的语料比。

    同族但**不同**的一份：`cloud_asr_20261010/ocw_ref.py` 出的是**带时间的 ref.json**
    （云端评测要按段对齐），它还会删 `[...]` 注释与「说话人:」标签。本实验的源是 MIT OCW
    字幕：18.06 那份全片 0 处；BMM 全片 3 处（`[AUDIO OUT]`×2、`[APPLAUSE]`×1），实测都落在本实验
    用的 600–900 s 窗口**之外**，所以这里不删也干净（换窗口时要重查一次）。
    """
    txt = path.read_text(encoding="utf-8", errors="ignore")
    out: list[str] = []
    for block in re.split(r"\n\s*\n", txt):
        m = re.search(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)", block)
        if not m:
            continue
        g = [int(v) for v in m.groups()]
        t0 = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000.0
        t1 = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000.0
        if t1 <= start or t0 >= start + dur:
            continue
        body = block[m.end():].strip().replace("\n", " ")
        body = re.sub(r"<[^>]+>", " ", body)
        out.append(re.sub(r"\s+", " ", body).strip())
    return re.sub(r"\s+", " ", " ".join(x for x in out if x)).strip()


# ---------- 主流程 ----------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=str(BASE))
    ap.add_argument("--out", default=None, help="输出目录（默认 <base>/mix）")
    ap.add_argument("--scene", choices=list(SCENES), action="append", help="只做某个场景，可重复")
    ap.add_argument("--seed", type=int, default=20261011)
    a = ap.parse_args()
    base = pathlib.Path(a.base).expanduser()
    out = pathlib.Path(a.out).expanduser() if a.out else base / "mix"
    (out / "ref").mkdir(parents=True, exist_ok=True)

    scenes = a.scene or list(SCENES)
    require_sfx(base, scenes)
    manifest: list[dict] = []
    for scene_name in scenes:
        sc = SCENES[scene_name]
        src_media = base / "sources" / f"{sc['src']}.mp4"
        if not src_media.exists():
            raise SystemExit(f"缺素材：{src_media}（先跑 fetch_classroom_sim.py）")
        # 只解要用的那 5 分钟（`-ss` 输入定位）。原来先整段解码再切片，白解 40 分钟。
        speech = load_file(str(src_media), start=sc["start"], dur=CLIP_S, timeout=1800)
        n_samp = int(CLIP_S * SR)
        if len(speech) < n_samp * 0.95:
            raise SystemExit(f"{sc['src']} 取不到 {CLIP_S}s（从 {sc['start']}s 起）")
        # ⚠️ 补齐/截到**精确**长度：下面 `y = sp + nz` 要求两者等长，而 `-ss/-t` 的取整误差
        #    会让 `load_file` 少给或多给几十个采样点 → 直接抛 broadcast ValueError，很难定位。
        speech = np.pad(speech, (0, max(0, n_samp - len(speech))))[:n_samp]

        # 三个座位的实测 RIR
        rirs = {pos: load_rir(base / "rirs_noises", name) for pos, name in sc["rir"].items()}
        d_front = rirs["front"][1]["dist_m"]
        for pos, (_, st) in rirs.items():
            print(f"[{scene_name}] {pos:5s} RIR {st['file'][22:]:34s} RT60 {st['rt60']}s "
                  f"DRR {st['drr_db']:+.2f}dB 直达 {st['dist_m']}m")

        ref = srt_to_text(base / "sources" / f"{sc['src']}.srt", sc["start"], CLIP_S)
        (out / "ref" / f"{scene_name}.txt").write_text(ref + "\n", encoding="utf-8")

        # 底噪过的是**中间座位**那条 RIR 的尾巴（观众是扩散场，用哪条差别不大）
        rng = random.Random(a.seed)
        noise, events, bed_rms_db = build_noise(sc, CLIP_S, base / "sfx", rirs["mid"][0], rng)
        mask = A.active_speech_mask(speech)
        n_pow = float(np.mean(noise.astype(np.float64) ** 2)) + 1e-20
        # ⭐ SNR 的定义点，只此一处：
        #    P_语音 = **语音活跃帧**上的功率（拿干净源的掩码去框，位置链处理之后的那份）；
        #    P_噪声 = 整条噪声信号（底噪+事件）的平均功率。
        #    为什么不用整体平均功率当 P_语音：讲课录音里有停顿，会把标称 SNR 抬高一点点 ——
        #    实测只抬 1.70 dB（18.06）/ 3.86 dB（BMM），不是大数，但既然精确是免费的，就用活跃帧。
        #    为什么不用 `active_level_db` 当定义：它在噪声大时会把噪声帧也算成语音帧，**越吵越乐观**
        #    （实测：加性噪声标 0 dB 的那一份，它估出 17 dB），所以那个数只当**旁证**写进
        #    manifest（`snr_est_db`），不当口径。
        # 事件把整条噪声的 RMS 抬起来了 → 想知道"只算底噪时是多少 dB"，得把事件的能量占比折算掉。
        # ⚠️ `n_pow` 与 `bed_rms_db` 都取自**同一个（缩放前）域**，比值才是"事件贡献"，与档位无关；
        #    而 `snr_added` 随档位变 → 两者相加才是"这一档里底噪单独看的 SNR"。
        #    （踩过：直接拿 `bed_rms_db - 10log10(ref_pow)` 当它，结果是同一个常数，与档位无关。）
        burst_boost_db = 10.0 * np.log10(n_pow / (10.0 ** (bed_rms_db / 10.0)))
        sp_stats = A.active_level_db(speech)
        print(f"[{scene_name}] 语音有效电平 {sp_stats['speech_db']}dB · 活跃帧 {mask.mean():.0%} "
              f"· 源自身 SNR {sp_stats['snr_db']}dB · 参考 {len(ref.split())} 词 · 事件 {len(events)} 个")
        ref_pow = float(np.mean(speech[mask].astype(np.float64) ** 2)) + 1e-20

        for pos_name in sc["rir"]:
            _, st = rirs[pos_name]
            att_db = max(ATT_CAP_DB, round(20.0 * np.log10(d_front / st["dist_m"]), 2))
            sp = apply_position(speech, rirs[pos_name][0], drr_db=st["drr_db"], att_db=att_db)
            # 这两项只跟**座位**有关，与噪声档无关 → 提到档位循环外面（每座位算一次而不是四次）
            sp_act = float(np.mean(sp[mask].astype(np.float64) ** 2)) + 1e-20
            src_floor_pow = float(np.mean(sp[~mask].astype(np.float64) ** 2)) + 1e-20
            snr_src = round(float(10.0 * np.log10(sp_act / src_floor_pow)), 2)
            for lvl_name, target in LEVELS.items():
                if target is None:
                    y, nz = sp.copy(), None
                else:
                    nz = noise * np.sqrt(ref_pow * 10.0 ** (-target / 10.0) / n_pow)
                    y = sp + nz
                # 实测 SNR：就是**刚加起来的那两个信号**在语音活跃帧上的功率比，不做任何估计。
                # `snr_total` 把源自己的底噪也算进去 —— 它含混响尾巴填进停顿的那部分，
                # 所以**比 `snr_added` 低**（两个都写进 manifest，报数时说清用的是哪个）。
                nz_pow = 0.0 if nz is None else float(np.mean(nz.astype(np.float64) ** 2))
                snr_added = None if nz is None else round(10.0 * np.log10(sp_act / (nz_pow + 1e-20)), 2)
                snr_total = round(float(10.0 * np.log10(sp_act / (nz_pow + src_floor_pow))), 2)
                # ⚠️ 这里用**整条一个全局增益**把峰值压到 0.9，**故意不用**生产那层
                #    `capture.PeakNormalizer`：那层是**滑动**峰值增益，会把每个文件的电平
                #    各自拉平 —— 而我们刚把 SNR 标定好，要的就是"后排比前排低 12 dB"这件事
                #    留在文件里。生产链路本来就会再套一次 PeakNormalizer，这里不必替它做。
                y = (y * (0.9 / float(np.max(np.abs(y))))).astype(np.float32)
                name = f"{scene_name}_{pos_name}_{lvl_name}"
                path = out / f"{name}.wav"
                digest = A.save_wav(str(path), y)      # 摘要由写出的字节直接算，不重读磁盘
                est = A.active_level_db(y)
                manifest.append({
                    "name": name, "file": path.name, "scene": scene_name,
                    "position": pos_name, "level": lvl_name,
                    "src": sc["src"], "src_start_s": sc["start"], "dur_s": round(len(y) / SR, 1),
                    "rir": st["file"], "rir_rt60": st["rt60"], "rir_drr_db": st["drr_db"],
                    "rir_dist_m": st["dist_m"], "att_db": att_db,
                    "nominal_snr_db": target, "snr_added_db": snr_added,
                    "snr_total_db": snr_total, "src_own_snr_db": snr_src,
                    "bed_only_snr_db": (None if snr_added is None
                                        else round(snr_added + burst_boost_db, 2)),
                    "snr_est_db": est["snr_db"], "est_speech_db": est["speech_db"],
                    "est_floor_db": est["floor_db"], "speech_active_ratio": round(float(mask.mean()), 3),
                    "bursts": events, "ref": f"ref/{scene_name}.txt",
                    "sha256": digest,
                })
                print(f"  {name:26s} 实测 SNR {str(snr_added):>7} dB（总 {snr_total:6.1f}）"
                      f" · 分位估计 {est['snr_db']:6.1f} · 距离 {st['dist_m']:5.2f}m att {att_db:6.1f}dB")

        # ⭐ **对照件**：源素材原样（不加房间、不加噪声、不衰减），只做峰值归一化。
        # 它才是"这份真课录音本来的样子"—— 上面那些 `front` 也已经叠了一层 RIR 尾巴，
        # 拿它当基线会把"我们叠的混响"算进噪声的账里。
        # SNR 用**与上面同一套定义**算（活跃帧 / 非活跃帧功率比），不拿分位估计冒充。
        y = (speech * (0.9 / float(np.max(np.abs(speech))))).astype(np.float32)
        path = out / f"{scene_name}_src_raw.wav"
        digest = A.save_wav(str(path), y)
        est = A.active_level_db(y)
        snr_raw = round(float(10.0 * np.log10(
            (float(np.mean(speech[mask].astype(np.float64) ** 2)) + 1e-20) /
            (float(np.mean(speech[~mask].astype(np.float64) ** 2)) + 1e-20))), 2)
        manifest.append({
            "name": f"{scene_name}_src_raw", "file": path.name, "scene": scene_name,
            "position": "src", "level": "raw", "src": sc["src"], "src_start_s": sc["start"],
            "dur_s": round(len(y) / SR, 1), "rir": None, "rir_rt60": None, "rir_drr_db": None,
            "rir_dist_m": None, "att_db": 0.0, "nominal_snr_db": None, "snr_added_db": None,
            "snr_total_db": snr_raw, "src_own_snr_db": snr_raw,
            "bed_only_snr_db": None, "snr_est_db": est["snr_db"], "est_speech_db": est["speech_db"],
            "est_floor_db": est["floor_db"], "speech_active_ratio": round(float(mask.mean()), 3),
            "bursts": [], "ref": f"ref/{scene_name}.txt", "sha256": digest,
        })
        print(f"  {scene_name}_src_raw{'':16s} 源素材原样（对照）· 自身 SNR {snr_raw} dB")

    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{len(manifest)} 份 → {out}\nmanifest.json 里每条都有实测 SNR / RIR / 距离 / 事件清单")
    return 0


if __name__ == "__main__":
    sys.exit(main())
