"""系统声音源（实验）：用 CoreAudio Process Tap 直接抓"电脑正在播的声音"，不用 BlackHole。

为什么：线上课现在要先装 BlackHole、再在「音频 MIDI 设置」里建多输出设备（README「线上课」一节），
这是最大的上手门槛。Process Tap（macOS 14.4+）是系统自带的：只要「音频录制」类授权
（`NSAudioCaptureUsageDescription`），不需要装驱动，也不需要「屏幕录制」权限。
思路出处：albond/WhisperCaption（MIT）`Capture/SystemCapture.swift`——tap → 私有聚合设备 → IOProc。
那份代码只读不抄，这里是按同一套 API 链用 PyObjC 重写的。

⚠️⚠️ **状态：未经真机验证。** 写这个模块时用户不在，而首次创建 tap 会弹系统授权框，
   所以只验证了不需要授权的部分（见 `tests/test_system_tap.py`：重采样、分块、静音看门狗、
   接口形状）。**CoreAudio 那段（`ProcessTapBackend`）一行都没真跑过**，上机先跑
   `scripts/probe_system_tap.py`，它会一步步打印每个调用的返回值。

接口与 `capture.CallbackSource` 同形：`poll()` / `close()` / `is_done()` / `device_name`，
所以 `capture.load_source("system")` 能直接套 `_NormalizedSource`。

分层（为了能离线测试）：
  ProcessTapBackend ──(float32 单声道, 采样率)──▶ SystemTapSource._feed ──▶ StreamResampler
                                                                      └▶ 1600 样本分块 ▶ 队列 ▶ poll()
"""
from __future__ import annotations
import ctypes, math, platform, queue, struct, threading, time, uuid
import numpy as np

SR = 16000
CHUNK = 1600
QUEUE_MAX = 200                 # 与 capture.QUEUE_MAX 一致：满了丢旧保新
#: 连续这么久样本全是 0 → 报「可能没授权/没有声音在播」。
#: ⚠️ 只凭全零**分不出**「权限被拒（系统给全零）」和「此刻真的没声音」——
#:    所以文案要两种可能都说；一旦听到过非零，就排除前者。
SILENCE_WARN_S = 8.0
_ZERO_PEAK = 1e-7


# ---------------------------------------------------------------- 重采样
class StreamResampler:
    """流式加窗 sinc 重采样（numpy 实现；App 的 venv 里没有 scipy）。

    对任意比值都成立（48000→16000、44100→16000…）。降采样时截止频率放在
    目标奈奎斯特的 95%，Kaiser 窗 β=8。块边界无缝：分块喂和一次性喂结果一致
    （测试里钉着）。输出时刻 n 对应输入位置 n·(src/dst)。
    """

    def __init__(self, src_sr: float, dst_sr: float = SR):
        if src_sr <= 0 or dst_sr <= 0:
            raise ValueError("采样率必须为正")
        self.src, self.dst = float(src_sr), float(dst_sr)
        self.ratio = self.src / self.dst
        # 截止频率（单位：周期/输入样本）。升采样时不需要抗混叠，取 0.5。
        self.fc = 0.5 * min(1.0, self.dst / self.src) * 0.95 * (1.0 if self.dst <= self.src else 1.0 / 0.95)
        self.half = max(8, int(math.ceil(8 * max(1.0, self.ratio))))
        self._buf = np.zeros(self.half, dtype=np.float64)    # 开头补零 → 输入下标从 -half 起
        self._base = -self.half                              # _buf[0] 对应的绝对输入下标
        self._n = 0                                          # 下一个要产出的输出下标

    def _kernel(self, frac: np.ndarray) -> np.ndarray:
        """frac: (N,) 各输出点相对 floor(p) 的小数偏移 ∈ [0,1)。返回 (N, 2*half) 的滤波系数。"""
        j = np.arange(-self.half + 1, self.half + 1, dtype=np.float64)       # 相对 floor(p) 的下标
        t = j[None, :] - frac[:, None]                                       # 输入点到输出点的距离
        x = 2.0 * self.fc * t
        h = 2.0 * self.fc * np.sinc(x)
        w = np.kaiser(2 * self.half + 1, 8.0)
        # 窗按 |t|/half 取：用线性插值到 Kaiser 的连续形式
        r = np.clip(np.abs(t) / self.half, 0.0, 1.0)
        win = np.interp(r * self.half, np.arange(self.half + 1), w[self.half:])
        win = np.where(np.abs(t) >= self.half, 0.0, win)
        return h * win

    def process(self, x: np.ndarray) -> np.ndarray:
        if x.size:
            self._buf = np.concatenate([self._buf, x.astype(np.float64, copy=False)])
        total = self._base + len(self._buf)                  # 已收到的绝对输入样本数
        # 输出 n 需要输入到 floor(n*ratio)+half（含）
        n_last = int(math.floor((total - 1 - self.half) / self.ratio))
        if n_last < self._n:
            return np.zeros(0, dtype=np.float32)
        ns = np.arange(self._n, n_last + 1, dtype=np.float64)
        p = ns * self.ratio
        i0 = np.floor(p).astype(np.int64)
        frac = p - i0
        k = self._kernel(frac)                                              # (N, 2half)
        idx = (i0[:, None] - self._base) + np.arange(-self.half + 1, self.half + 1)[None, :]
        y = np.einsum("nk,nk->n", self._buf[idx], k)
        self._n = n_last + 1
        keep_from = int(math.floor(self._n * self.ratio)) - self.half + 1   # 下一个输出还要用到的最早输入
        drop = keep_from - self._base
        if drop > 0:
            self._buf = self._buf[drop:]
            self._base += drop
        return y.astype(np.float32)


# ---------------------------------------------------------------- 静音看门狗
class SilenceWatch:
    """喂入块，记录「连续全零多久」和「是否听到过非零」。纯计数，不参与任何判定。"""

    def __init__(self, warn_s: float = SILENCE_WARN_S):
        self.warn_s = warn_s
        self.silent_s = 0.0
        self.ever_heard = False
        self.total_s = 0.0

    def feed(self, block: np.ndarray, sr: float) -> None:
        dur = len(block) / sr if sr else 0.0
        self.total_s += dur
        if block.size and float(np.max(np.abs(block))) > _ZERO_PEAK:
            self.ever_heard = True
            self.silent_s = 0.0
        else:
            self.silent_s += dur

    def message(self) -> str | None:
        """该不该提醒、提醒什么。听到过声音之后的静音只是「没人说话」，不提醒。"""
        if self.ever_heard or self.silent_s < self.warn_s:
            return None
        return (f"系统声音源 {self.silent_s:.0f} 秒全是零：要么还没授权「音频录制」"
                "（系统设置 → 隐私与安全性），要么此刻电脑里没有声音在播。")


# ---------------------------------------------------------------- 音源
class SystemTapSource:
    """与 `capture.CallbackSource` 同形的系统声音源。`backend` 可注入（测试用假后端）。"""

    def __init__(self, backend=None):
        self._q: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=QUEUE_MAX)
        self._backend = backend if backend is not None else ProcessTapBackend()
        self._resampler: StreamResampler | None = None
        self._rate: float | None = None
        self._pending = np.zeros(0, dtype=np.float32)
        self._lock = threading.Lock()
        self.watch = SilenceWatch()
        self._closed = False
        self._backend.start(self._feed)

    @property
    def device_name(self) -> str:
        return "系统声音 (Process Tap)"

    # 后端的实时线程调这个：必须快，别在里面做重活（重采样很轻，numpy 一次几百微秒）
    def _feed(self, samples: np.ndarray, sample_rate: float) -> None:
        if self._closed or samples.size == 0:
            return
        with self._lock:
            if self._resampler is None or self._rate != sample_rate:
                # 设备采样率中途变了（换输出设备）：丢掉重采样器的残留状态，重新来
                self._resampler = StreamResampler(sample_rate, SR)
                self._rate = sample_rate
            self.watch.feed(samples, sample_rate)
            out = self._resampler.process(samples)
            if out.size:
                self._pending = np.concatenate([self._pending, out])
            while len(self._pending) >= CHUNK:
                blk, self._pending = self._pending[:CHUNK].copy(), self._pending[CHUNK:]
                try:
                    self._q.put_nowait(blk)
                except queue.Full:
                    try:
                        self._q.get_nowait()
                        self._q.put_nowait(blk)
                    except (queue.Empty, queue.Full):
                        pass

    def poll(self):
        try:
            return self._q.get_nowait()
        except queue.Empty:
            return None

    def is_done(self) -> bool:
        return False

    def health(self) -> str | None:
        """None = 正常；否则是一句给用户看的话（主循环想提醒时读它）。"""
        return self.watch.message()

    def close(self) -> None:
        self._closed = True
        try:
            self._backend.stop()
        except Exception:                                  # noqa: BLE001
            pass


# ---------------------------------------------------------------- CoreAudio 后端（未经真机验证）
def available() -> tuple[bool, str]:
    """能不能用 Process Tap：系统 ≥ 14.4 且 PyObjC 里有 `CATapDescription`。"""
    try:
        ver = tuple(int(x) for x in platform.mac_ver()[0].split(".")[:2])
    except Exception:                                      # noqa: BLE001
        return False, "无法识别 macOS 版本"
    if ver < (14, 4):
        return False, f"需要 macOS 14.4+（当前 {platform.mac_ver()[0]}）"
    try:
        import CoreAudio as CA
        if not hasattr(CA, "CATapDescription") or not hasattr(CA, "AudioHardwareCreateProcessTap"):
            return False, "PyObjC 的 CoreAudio 绑定里没有 Process Tap"
    except Exception as e:                                 # noqa: BLE001
        return False, f"CoreAudio 绑定不可用: {e}"
    return True, ""


class TapError(RuntimeError):
    pass


def _fourcc(s: str) -> int:
    return struct.unpack(">I", s.encode("ascii"))[0]


class _Addr(ctypes.Structure):
    _fields_ = [("sel", ctypes.c_uint32), ("scope", ctypes.c_uint32), ("elem", ctypes.c_uint32)]


def _ca_get(obj: int, selector: str, scope: str, ctype):
    """ctypes 直接调 AudioObjectGetPropertyData（PyObjC 的 `^v` 出参拿不到值，实测过）。
    返回 (OSStatus, 值)。已用「默认输出设备 → 48000.0」验证过调用约定（不需要授权）。"""
    ca = ctypes.CDLL("/System/Library/Frameworks/CoreAudio.framework/CoreAudio")
    ca.AudioObjectGetPropertyData.argtypes = [ctypes.c_uint32, ctypes.POINTER(_Addr), ctypes.c_uint32,
                                              ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
    ca.AudioObjectGetPropertyData.restype = ctypes.c_int32
    a = _Addr(_fourcc(selector), _fourcc(scope), 0)
    out = ctype()
    sz = ctypes.c_uint32(ctypes.sizeof(out))
    err = ca.AudioObjectGetPropertyData(obj, ctypes.byref(a), 0, None, ctypes.byref(sz), ctypes.byref(out))
    return err, out.value


def _abl_to_array(abl) -> np.ndarray | None:
    """把 IOProc 回调里的 AudioBufferList 取成 float32 单声道数组（拷贝，回调返回后缓冲就没了）。
    ⚠️ 没真机验证：PyObjC 把 `mData(^v)` 给成什么类型要看实际 —— 这里按 `.pointerAsInteger` / int 两种试。"""
    try:
        n = int(abl.mNumberBuffers)
        if n < 1:
            return None
        buf = abl.mBuffers[0]
        nbytes = int(buf.mDataByteSize)
        ch = max(1, int(buf.mNumberChannels))
        ptr = buf.mData
        addr = getattr(ptr, "pointerAsInteger", None)
        if addr is None:
            addr = int(ptr)
        if not addr or nbytes < 4:
            return None
        n_f = nbytes // 4
        arr = np.ctypeslib.as_array((ctypes.c_float * n_f).from_address(addr)).copy()
        if ch > 1:
            arr = arr[: (len(arr) // ch) * ch].reshape(-1, ch).mean(axis=1)
        return arr.astype(np.float32, copy=False)
    except Exception:                                      # noqa: BLE001
        return None


class ProcessTapBackend:
    """tap → 私有聚合设备 → IOProc。`start(on_audio)` 后 `on_audio(samples, sample_rate)` 在实时线程里被调。"""

    def __init__(self):
        self._ca = None
        self._tap_id = 0
        self._agg_id = 0
        self._proc_id = None
        self._started = False

    def start(self, on_audio) -> None:
        ok, why = available()
        if not ok:
            raise TapError(why)
        import CoreAudio as CA
        self._ca = CA
        desc = CA.CATapDescription.alloc().initMonoGlobalTapButExcludeProcesses_([])
        desc.setName_("ClassLive system audio")
        desc.setPrivate_(True)
        desc.setMuteBehavior_(CA.CATapUnmuted)                     # 只听，不静音原来的输出
        err, self._tap_id = CA.AudioHardwareCreateProcessTap(desc, None)
        if err != 0:
            raise TapError(f"AudioHardwareCreateProcessTap 失败 OSStatus={err}（多半是没授权「音频录制」）")
        tap_uid = str(desc.UUID().UUIDString())
        agg = {
            CA.kAudioAggregateDeviceNameKey: "ClassLive Tap",
            CA.kAudioAggregateDeviceUIDKey: str(uuid.uuid4()),
            CA.kAudioAggregateDeviceIsPrivateKey: True,
            CA.kAudioAggregateDeviceTapAutoStartKey: True,
            CA.kAudioAggregateDeviceTapListKey: [
                {CA.kAudioSubTapUIDKey: tap_uid, CA.kAudioSubTapDriftCompensationKey: True}],
        }
        err, self._agg_id = CA.AudioHardwareCreateAggregateDevice(agg, None)
        if err != 0:
            self.stop()
            raise TapError(f"AudioHardwareCreateAggregateDevice 失败 OSStatus={err}")
        # 采样率：聚合设备的名义采样率（拿不到就按 48000，并在 on_audio 里原样上报）
        e2, rate = _ca_get(self._agg_id, "nsrt", "glob", ctypes.c_double)
        self.sample_rate = float(rate) if e2 == 0 and 8000 <= rate <= 192000 else 48000.0

        def _block(now, in_data, in_time, out_data, out_time):
            arr = _abl_to_array(in_data)
            if arr is not None and arr.size:
                on_audio(arr, self.sample_rate)

        err, self._proc_id = CA.AudioDeviceCreateIOProcIDWithBlock(None, self._agg_id, None, _block)
        if err != 0:
            self.stop()
            raise TapError(f"AudioDeviceCreateIOProcIDWithBlock 失败 OSStatus={err}")
        self._block_ref = _block                                   # 防止被回收
        err = CA.AudioDeviceStart(self._agg_id, self._proc_id)
        if err != 0:
            self.stop()
            raise TapError(f"AudioDeviceStart 失败 OSStatus={err}")
        self._started = True

    def stop(self) -> None:
        CA = self._ca
        if CA is None:
            return
        # 每一步各自 try：任何一步抛都不能让后面的清理被跳过（否则聚合设备会残留在系统里）
        if self._started and self._proc_id is not None:
            try:
                CA.AudioDeviceStop(self._agg_id, self._proc_id)
            except Exception:                               # noqa: BLE001
                pass
        if self._proc_id is not None:
            try:
                CA.AudioDeviceDestroyIOProcID(self._agg_id, self._proc_id)
            except Exception:                               # noqa: BLE001
                pass
        if self._agg_id:
            try:
                CA.AudioHardwareDestroyAggregateDevice(self._agg_id)
            except Exception:                               # noqa: BLE001
                pass
        if self._tap_id:
            try:
                CA.AudioHardwareDestroyProcessTap(self._tap_id)
            except Exception:                               # noqa: BLE001
                pass
        self._started = False
        self._proc_id = None
        self._agg_id = 0
        self._tap_id = 0
