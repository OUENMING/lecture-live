"""房间声学与电平的小量具：解码 / 帧电平 / 有效语音电平与信噪比 / RIR 的 RT60 与直混比。

为什么单独一份：`make_classroom_sim.py` 要**按实测挑 RIR、再按实测报参数**，
而不是按文件名猜「大房间 = 混响长」。

口径（都写清楚，免得数字没法比）：
  · 电平一律 dBFS（满刻度 1.0 为 0 dB）。
  · `active_level_db` 用**分位数门限**分语音帧/底噪帧，不是 P.56 —— 讲课录音里
    停顿多、底噪稳，分位数比 P.56 稳，而且不依赖采样率相关的滤波器组。
  · `rir_stats` 的 RT60 是**真脉冲响应上的 Schroeder 积分 + T30 拟合**（RIR 就是脉冲响应，
    所以这里可以谈秒数）；讲课录音那边**不要**照搬——见文件末尾的说明。
"""
from __future__ import annotations

import hashlib
import pathlib
import subprocess
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from capture import SR  # noqa: E402  ⭐ 采样率走生产那一份（解码由调用方走 `capture.load_file`）
FRAME_S = 0.02          # 帧长 20 ms（电平统计）
HOP_S = 0.01            # 帧移 10 ms
ACTIVE_MARGIN_DB = 15.0  # 「比底噪高这么多算语音帧」——`active_level_db` 与 `active_speech_mask`
#                          共用同一个值，改这里两处一起改（分开写会悄悄分叉）


def save_wav(path: str, x: np.ndarray, sr: int = SR) -> str:
    """写 16-bit PCM wav（与 `~/.classlive/eval/` 里既有语料同规格），返回写入字节的 sha256 前 16 位。

    顺带把摘要返回：调用方常要把它记进 manifest，而**刚写完又从磁盘读回来算**是白读一遍
    （26 份 × 9.6 MB ≈ 250 MB）。
    """
    x = np.clip(np.asarray(x, dtype=np.float32), -1.0, 1.0)
    pcm = (x * 32767.0).astype("<i2")
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "s16le", "-ar", str(sr), "-ac", "1",
         "-i", "pipe:0", "-c:a", "pcm_s16le", str(path)],
        input=pcm.tobytes(), check=True, capture_output=True,
    )
    return hashlib.sha256(pcm.tobytes()).hexdigest()[:16]


# ---------- 帧电平 ----------
def frame_db(x: np.ndarray, sr: int = SR, frame_s: float = FRAME_S,
             hop_s: float = HOP_S) -> np.ndarray:
    """逐帧 RMS（dBFS）。帧长/帧移按采样点取整，末尾不足一帧的丢掉。"""
    n, h = int(frame_s * sr), int(hop_s * sr)
    if len(x) < n:
        return np.array([], dtype=np.float64)
    win = np.lib.stride_tricks.sliding_window_view(x, n)[::h]
    rms = np.sqrt(np.mean(win.astype(np.float64) ** 2, axis=1) + 1e-20)
    return 20.0 * np.log10(rms)


def _speech_frame_mask(fd: np.ndarray) -> np.ndarray:
    """逐帧的「是语音」布尔（比 10 分位底噪高 `ACTIVE_MARGIN_DB`）。

    ⭐ 这是**语音/非语音判据的唯一出处**：`active_level_db` 与 `active_speech_mask` 都走它。
    分头写两份的话，阈值一旦在一边改了，主口径（活跃帧功率）和 manifest 里的旁证
    （分位估计）就会各说各话，而且没人会发现。
    """
    if fd.size == 0:
        return np.zeros(0, dtype=bool)
    return fd > float(np.percentile(fd, 10)) + ACTIVE_MARGIN_DB


def active_level_db(x: np.ndarray, sr: int = SR) -> dict:
    """有效语音电平 / 底噪电平 / 两者之差（dB）。

    底噪 = 帧电平的 10 分位；语音 = 语音帧的功率均值。
    分位数而不是最小值：最小值会被数字静音段（剪辑留白）拉到 -inf。
    """
    fd = frame_db(x, sr)
    if fd.size == 0:
        return {"speech_db": None, "floor_db": None, "snr_db": None, "speech_frames": 0}
    floor = float(np.percentile(fd, 10))
    sp = fd[_speech_frame_mask(fd)]
    if not sp.size:
        # 没有一帧够得上"比底噪高 15 dB"（稳态噪声、或噪声大到把语音压平）
        # → **返回 None，不返回 -200**："没有数"和"数很小"要分得开
        return {"speech_db": None, "floor_db": round(floor, 2),
                "snr_db": None, "speech_frames": 0}
    speech = 10.0 * np.log10(float(np.mean(10.0 ** (sp / 10.0))) + 1e-20)
    return {"speech_db": round(speech, 2), "floor_db": round(floor, 2),
            "snr_db": round(speech - floor, 2), "speech_frames": int(sp.size)}


def active_speech_mask(x: np.ndarray, sr: int = SR) -> np.ndarray:
    """逐**采样点**的语音/非语音掩码（由帧掩码上采样），给"只按语音段算能量"的地方用。"""
    fd = frame_db(x, sr)
    if fd.size == 0:
        return np.zeros(len(x), dtype=bool)
    m = _speech_frame_mask(fd)
    hop, n = int(HOP_S * sr), int(FRAME_S * sr)
    full = np.zeros(len(x), dtype=bool)
    for i in np.flatnonzero(m):        # 只遍历语音帧；实测这个循环比向量化展开更快
        full[i * hop: i * hop + n] = True
    return full


# ---------- RIR ----------
def rir_stats(h: np.ndarray, sr: int = SR, direct_ms: float = 2.5) -> dict:
    """RIR 的 DRR（直混比，dB）与 RT60（Schroeder，从 -5 dB 到 -30 dB 拟合）。

    DRR = 直达声能量（主峰**前 1 ms** 到主峰后 `direct_ms` 毫秒）/ 其余能量。
    这是**估计**：真实直达声窗受采样率限制（16 kHz 一个样本 62.5 µs），
    所以对短 RIR 更准，对长混响 RIR 误差小到不影响选型。

    ⚠️ 只对**真脉冲响应**（RIR）用。对讲课录音做同样的外推会被下一句语音和压缩器毁掉：
    实测在已知 RT60 的合成语音上，外推法给出 4.8 s vs 真值 0.8 s。讲课录音那边改用
    「活跃帧 vs 非活跃帧功率比」（见 `make_classroom_sim.py` 的 SNR 定义点），
    那个只排序、不换算成秒。
    """
    h = np.asarray(h, dtype=np.float64)
    if h.ndim > 1:
        h = h[:, 0]
    p = h ** 2
    peak = int(np.argmax(np.abs(h)))
    w = int(direct_ms / 1000.0 * sr)
    lo, hi = max(0, peak - int(0.001 * sr)), min(len(h), peak + w)
    e_dir = p[lo:hi].sum()
    e_rev = p.sum() - e_dir
    drr = 10.0 * np.log10(e_dir / e_rev) if e_rev > 0 and e_dir > 0 else float("nan")
    edc = 10.0 * np.log10(np.cumsum(p[::-1])[::-1] / p.sum() + 1e-30)
    t = np.arange(len(h)) / sr
    ok = (edc <= -5.0) & (edc >= -30.0)
    rt60 = float("nan")
    if ok.sum() > 20:
        a, _ = np.polyfit(t[ok], edc[ok], 1)
        if a < -1e-3:
            rt60 = -60.0 / a
    return {"drr_db": round(float(drr), 2) if drr == drr else None,
            "rt60": round(rt60, 3) if rt60 == rt60 else None}
