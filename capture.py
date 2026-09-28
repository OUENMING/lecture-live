"""音源统一抽象: 麦克风 / BlackHole 系统声 / 文件模拟实时流。

对下游暴露 **可轮询的音频源**(poll() 非阻塞取块, 每块 0.1s / 1600 采样),
这样主线程可以在同一循环里既收音频、又刷新 UI、又排空结果队列, 永不阻塞在 I/O 上。

实时输入用 sounddevice **回调式异步采集**(声卡线程 -> 队列), poll() 只做 get_nowait。
"""
from __future__ import annotations
import queue, subprocess, time
from pathlib import Path
import numpy as np

SR = 16000          # 采样率
CHUNK = 1600        # 0.1s
QUEUE_MAX = 200     # 最多缓冲 20s, 满了丢旧保新


# ---------- 音频解码(ffmpeg, 任意格式 -> 16kHz mono f32) ----------
def load_file(path: str) -> np.ndarray:
    """把任意音频文件解码成 16kHz 单声道 float32 [-1,1]。用 ffmpeg。"""
    try:
        p = subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-i", str(path),
             "-f", "f32le", "-acodec", "pcm_f32le", "-ac", "1", "-ar", str(SR), "-"],
            capture_output=True, timeout=300,
        )
    except subprocess.TimeoutExpired as e:
        # 损坏/超大/含异常流的文件会让 ffmpeg 挂住不退出 —— 而调用方假定这层不阻塞
        raise RuntimeError(f"ffmpeg 解码超时(300s): {path}") from e
    if p.returncode != 0:
        err = p.stderr.decode("utf-8", "ignore").strip()
        raise RuntimeError(f"ffmpeg 解码失败({p.returncode}): {path}"
                           + (f": {err[:300]}" if err else ""))
    return np.frombuffer(p.stdout, dtype=np.float32)


def _mono(data: np.ndarray) -> np.ndarray:
    arr = np.asarray(data)
    if arr.ndim == 2:
        arr = arr[:, 0]
    return arr.astype(np.float32, copy=False)


def _find_input_named(substr: str) -> int:
    """按名字找**输入**设备。必须过滤 max_input_channels>0:
    macOS 上虚拟声卡(如 BlackHole)常同名注册输入+输出两个条目,
    选到纯输出设备会让 sd.InputStream 直接报错。"""
    import sounddevice as sd
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0 and substr.lower() in d["name"].lower():
            return i
    raise RuntimeError(
        f"找不到名为 '{substr}' 的输入设备。请先: brew install --cask blackhole-2ch, "
        f"然后在 Audio MIDI Setup 里创建多输出设备。")


def resolve_input_device(source: str) -> tuple[int, str]:
    """按音源名解析出**当前**的输入设备 -> `(索引, 名字)`。

    ⚠️ 这是「录哪个设备」的**唯一定义点**：`load_source` 开流时走它，上课中途
    重新探查也走它 —— 两处各写一份迟早漂。
    ⚠️ 名字要**一起**返回：索引会漂（睡一觉、插拔耳机、切默认输入都会），
    名字才是能打给用户看的凭据。
    """
    import sounddevice as sd
    src = source.lower()
    if src == "mic":
        idx = sd.default.device[0]
    elif src == "blackhole":
        idx = _find_input_named("BlackHole")
    else:
        raise ValueError(f"{source!r} 不是实时设备音源(只有 mic|blackhole)")
    if idx is None or int(idx) < 0:
        raise RuntimeError(f"系统没有可用的默认输入设备({source})")
    idx = int(idx)
    try:
        name = str(sd.query_devices(idx)["name"])
    except Exception:                                 # noqa: BLE001
        name = f"device#{idx}"
    return idx, name


# ---------- 电平归一化 ----------
class PeakNormalizer:
    """把输入电平拉到接近满刻度但不削顶 —— 远场收音的前提。

    麦克风收到的电平取决于说话人离电脑多远: 讲师在讲台比凑近说低 20–30dB。
    而 Silero VAD 和 Parakeet 都对**绝对电平**敏感, 不是只看信噪比: 实测把一段
    真实课堂录音等比衰减(信噪比不变), 词数从 73 掉到 6。所以必须在进 VAD/ASR
    之前把电平补回来。

    用**滑动峰值**而非逐块峰值: 逐块自适应会跟着音节抖动, 增益抖动本身就会把
    句子切碎(VAD 判定随增益起伏)。滑动峰值让增益在句内近似恒定, 只在跨句
    缓慢适应。hold_s 是峰值衰减 10 倍所需秒数。
    """

    def __init__(self, ceiling: float = 0.95, max_gain_db: float = 30.0,
                 hold_s: float = 4.0, smooth: float = 0.02):
        self.ceiling = ceiling
        self.max_gain = 10 ** (max_gain_db / 20)
        self.decay = 10 ** (-CHUNK / SR / max(hold_s, 0.1))   # 每块衰减比例
        self.smooth = smooth
        self.peak = 1e-4
        self.gain = 1.0

    def process(self, chunk: np.ndarray) -> np.ndarray:
        p = float(np.max(np.abs(chunk))) if chunk.size else 0.0
        self.peak = max(p, self.peak * self.decay)
        want = min(self.ceiling / max(self.peak, 1e-9), self.max_gain)
        self.gain += (want - self.gain) * self.smooth
        g = min(self.gain, self.ceiling / p) if p > 1e-9 else self.gain
        return chunk * g


class _NormalizedSource:
    """给任意音源套一层 PeakNormalizer, 对下游保持同样的 poll()/close() 接口。"""

    def __init__(self, inner):
        self._inner = inner
        self._norm = PeakNormalizer()

    def poll(self):
        c = self._inner.poll()
        return None if c is None else self._norm.process(c)

    def is_done(self) -> bool:
        return self._inner.is_done()

    def close(self) -> None:
        self._inner.close()

    @property
    def device_name(self) -> str:
        return getattr(self._inner, "device_name", "")

    def switch_device(self, device_idx: int, device_name: str = "") -> bool:
        """透传到内层。文件源没有这个方法 → `False`（那不是错误，是"没得换"）。"""
        fn = getattr(self._inner, "switch_device", None)
        return bool(fn(device_idx, device_name)) if callable(fn) else False


# ---------- 音源对象: 统一 poll() 接口 ----------
class CallbackSource:
    """麦克风 / BlackHole: 声卡回调线程推块, poll() 非阻塞取。"""

    def __init__(self, device_idx: int, device_name: str = ""):
        self._q: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=QUEUE_MAX)
        self._device_idx = int(device_idx)
        self._device_name = device_name
        self._stream = None
        self._open()

    def _open(self) -> None:
        import sounddevice as sd
        self._stream = sd.InputStream(
            device=self._device_idx, samplerate=SR, channels=1,
            dtype="float32", blocksize=CHUNK, callback=self._cb)
        self._stream.start()

    @property
    def device_name(self) -> str:
        return self._device_name or f"device#{self._device_idx}"

    def switch_device(self, device_idx: int, device_name: str = "") -> bool:
        """换输入设备：**就地**重开流（对象身份不变 → 下游不用重新绑定）。
        返回**是否真的换了**（同一个索引直接 False，不白折腾一次开流）。

        ⚠️ **先开新的、成功了才关旧的，失败必须回滚。** 反过来写（先关旧再开新）
        一旦新设备开不起来，对象就停在**半换**状态：流没了、索引指向坏设备 ——
        表现成「从此再也收不到音频，而屏上一切正常」。这个坑是我自己写第一版时
        踩到的，`R16` 钉住它。
        ⚠️ 队列**刻意不清空**：里面那几块旧设备的音频会被下游自然消费掉（最多
        `QUEUE_MAX` 块 = 2 秒），而清空会在 VAD 眼里造出一个空洞 —— 更容易误切句。
        `_q` 对象自始至终是同一个，`poll()` 那边不受影响。
        """
        if int(device_idx) == self._device_idx:
            return False
        old = (self._device_idx, self._device_name, self._stream)
        self._device_idx, self._device_name = int(device_idx), device_name
        try:
            self._open()                      # 新流起来之前不碰旧流
        except Exception:
            self._device_idx, self._device_name, self._stream = old
            raise
        self._close_stream(old[2])            # 成功了才关旧的
        return True

    @staticmethod
    def _close_stream(stream) -> None:
        if stream is None:
            return
        try:
            stream.stop(); stream.close()
        except Exception:                     # noqa: BLE001
            pass

    def _cb(self, indata, frames, time_info, status):
        blk = _mono(indata).copy()
        try:
            self._q.put_nowait(blk)
        except queue.Full:
            # 丢**最旧**的一块再放新的(与 QUEUE_MAX 的"丢旧保新"语义一致)。
            # 旧写法 except 直接 pass 会把**新**块丢掉, 于是主循环一旦落后
            # >20s(QUEUE_MAX×0.1s)就永远停在 20 秒前的音频上, 再也追不回来。
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
        return False                              # 实时源永不结束

    def close(self):
        self._close_stream(self._stream)
        self._stream = None


class FileSource:
    """把音频按真实时间喂出(可加速)。path=读文件, samples=已解码的样本数组。"""

    def __init__(self, path: str | None = None, speed: float = 1.0,
                 samples: np.ndarray | None = None):
        self._samples = samples if samples is not None else load_file(path)
        self._idx = 0
        self._speed = max(speed, 0.01)
        self._next_due = time.monotonic()

    def poll(self):
        now = time.monotonic()
        if now < self._next_due or self._idx >= len(self._samples):
            return None
        chunk = self._samples[self._idx:self._idx + CHUNK]
        self._idx += CHUNK
        self._next_due = now + CHUNK / SR / self._speed
        return chunk

    def is_done(self) -> bool:
        return self._idx >= len(self._samples)

    def close(self):
        pass


def load_source(source: str, path: str | None = None, speed: float = 1.0):
    """返回带 poll()/close() 的音频源(已套电平归一化)。
    source: mic | blackhole | file。"""
    src = source.lower()
    if src in ("mic", "blackhole"):
        # ⚠️ 设备解析只有 `resolve_input_device` 一份 —— 上课中途重探走的是同一条
        idx, name = resolve_input_device(src)
        inner = CallbackSource(idx, name)
    elif src == "file":
        if not path or not Path(path).exists():
            raise FileNotFoundError(f"文件不存在: {path}")
        inner = FileSource(path, speed)
    else:
        raise ValueError(f"未知音源: {source} (支持 mic|blackhole|file)")
    return _NormalizedSource(inner)
