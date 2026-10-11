"""定稿 ASR 深模块: 云端优先、本地兜底, 对调用方只有一个 `transcribe(samples)`。

    final = load_final(final_model_dir, draft_asr, allow_cloud, preload_local=...)
    r = final.transcribe(buf)          # 永不抛; r.text / r.source / r.steps ...

## 调用方不用知道的事(都在这个模块里)

- 编码: 16 kHz float → Ogg/Opus(soundfile)。千问吃 WAV 慢约 10 倍(11–23 s vs 1.2 s), 必须压。
- 总截止时间(R3): httpx 的超时是**按阶段**的, 最坏 connect+write+read 约 14 s, 卡不住总时长。
  这里用线程池 + `future.result(timeout=剩余)` 卡**总时长**: 平时 6 s, 收尾阶段(`is_stopping()`)
  2 s —— 收尾冲刷有 15 s 上限, 云端拖慢不能把最后几段拖没。
- 熔断: 连续 3 次失败 → 开 60 s(期间直接本地)→ 半开放 1 段试探。状态变化只 echo 一行。
- 云端空串(R5): 段长 ≥2 s 时补跑一次草稿模型, 有字就用它 —— 防 Groq no_speech 规则 /
  千问 NO_WORDS 把有声段误判成静音而**静默丢句**。
- 退化检测: 云端或本地返回 `is_degenerate` 的文本一律当失败, 换下一个来源。
- 开关语义(R7): `CLASSLIVE_CLOUD_ASR=0` → 不发请求、不写 `cloud-asr.json`、不写 `install-id`、
  不弹告知(`read_config` 返回「已告知 + 关」)。

## 为什么「收尾截止」用 `is_stopping` 回调, 而不是 `transcribe(..., deadline_s=)`

截止策略是这个模块的**内部政策**(6 s / 2 s 是同一个决定的两面)。让调用方传秒数, 等于
把政策泄漏给 `final_worker`, 且每个调用点都得记得传 —— 漏传就是静默回到 6 s。
回调与 `allow_cloud` 同形(每段问一次), 调用方只需告诉模块「现在是不是收尾」这个**事实**。
测试也只需翻一个布尔, 不必知道秒数。

## 云端本身的前提(出处: docs/PLAN-r2-cloud-final-asr.md §0.1, 2026-10-11 现查)

Groq 推理请求默认不留存(滥用监控最长 30 天); 阿里云百炼不用于训练但按法规会保存调用数据。
客户端**不重试**: 重试只在 Worker 一层, 这里失败就走本地。
"""
from __future__ import annotations

import datetime
import io
import json
import os
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

import asr_endpoint
import paths
from asr import is_degenerate

SAMPLE_RATE = 16000
NORMAL_DEADLINE_S = 6.0      # 平时一段的总截止(R3)
STOP_DEADLINE_S = 2.0        # 收尾冲刷时(R3)
BREAKER_FAILS = 3            # 连续失败几次开闸
BREAKER_OPEN_S = 60.0
EMPTY_RETRY_MIN_S = 2.0      # R5: 云端回空串且段长 ≥ 这个值才补跑草稿模型
#: Opus 编码额外参数。
# ⭐ 码率实测（RESEARCH §15，OCW 322 段，Groq whisper-large-v3）：soundfile 默认参数 WER 11.0% / 漏 1.0%
#    （两次跑逐字相同 → 不是噪声），`compression_level=0.88` → 10.2% / 0.4%，约 42 KB/段，与 ffmpeg 24k
#    （10.1% / 0.4%）打平。再往大（0.8，65 KB）准确度不再涨，千问却慢一截（p50 2.7 → 4.4 s）。
OPUS_KW: dict = {"compression_level": 0.88}

ENV_SWITCH = "CLASSLIVE_CLOUD_ASR"


def echo(msg: str) -> None:
    print(msg, flush=True)


def cloud_forced_off() -> bool:
    """`CLASSLIVE_CLOUD_ASR=0` —— 本进程强制本地定稿(R7)。"""
    return os.environ.get(ENV_SWITCH, "").strip() == "0"


# ---------------------------------------------------------------- 配置 / install id

def consent_text() -> str:
    """首次告知文案 —— 终端与弹窗共用的唯一来源。(2026-10-11 Owen 看过: 删掉末段「想保证音频不出本机…」)

    底线(R13): 能保证音频不出本机的只有「云端转写开关 / CLASSLIVE_CLOUD_ASR=0」;
    不说「用纯转录档就不出本机」—— 每节课都从翻译档开始, 切换之前的几段已经发出去了。
    """
    return (
        "定稿转写默认会把每一段音频(Opus 压缩, 约 40 KB/段)经作者的服务器转发给云端识别服务, "
        "换取更准的英文转写:\n"
        "· Groq(美国):推理请求默认不留存, 仅为滥用监控最长保留 30 天;\n"
        "· 阿里云百炼(中国):不用于训练, 但按法规会保存调用记录。\n"
        "只发音频, 不发你的名字、课号或文件路径。云端不可用时自动改用本机识别, 字幕不会中断。"
    )


def read_config(*, root=None) -> dict:
    """`{"enabled": bool, "acknowledged": str}`。

    文件缺失 = 未告知过、默认开; 坏 JSON = 同上。`CLASSLIVE_CLOUD_ASR=0` 时返回
    「关 + 已告知」, 让调用方既不弹告知也不上云, 且全程不碰磁盘。
    """
    if cloud_forced_off():
        return {"enabled": False, "acknowledged": "env"}
    try:
        d = json.loads(paths.cloud_asr_config(root=root).read_text(encoding="utf-8"))
        if isinstance(d, dict):
            return {"enabled": bool(d.get("enabled", True)),
                    "acknowledged": str(d.get("acknowledged") or "")}
    except (OSError, ValueError):
        pass
    return {"enabled": True, "acknowledged": ""}


def write_config(enabled: bool, *, root=None) -> bool:
    """记下选择与告知日期。返回是否真的写了。`CLASSLIVE_CLOUD_ASR=0` 时什么都不写(R7)。"""
    if cloud_forced_off():
        return False
    p = paths.cloud_asr_config(root=root)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps({"enabled": bool(enabled),
                                   "acknowledged": datetime.date.today().isoformat()}),
                       encoding="utf-8")
        os.replace(tmp, p)
        return True
    except OSError:
        return False


_PROC_INSTALL_ID: str | None = None


def install_id(*, root=None) -> str:
    """32 位 hex。没有就生成并写入(权限 600); 写失败用进程内随机值, 不挡开课。"""
    global _PROC_INSTALL_ID
    p = paths.install_id(root=root)
    try:
        v = p.read_text(encoding="utf-8").strip()
        if len(v) == 32 and all(c in "0123456789abcdef" for c in v):
            return v
    except OSError:
        pass
    v = secrets.token_hex(16)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(v)
        os.chmod(p, 0o600)
        return v
    except OSError:
        if _PROC_INSTALL_ID is None:
            _PROC_INSTALL_ID = v
        return _PROC_INSTALL_ID


# ---------------------------------------------------------------- 编码 / HTTP

_warned_no_sf = False


def _encode_opus(samples) -> bytes | None:
    """float32 16 kHz mono → Ogg/Opus。失败返回 None(调用方走本地)。"""
    global _warned_no_sf
    try:
        import soundfile as sf
    except ImportError:
        if not _warned_no_sf:
            _warned_no_sf = True
            echo("⚠ 缺 soundfile, 定稿走本地; `cl update` 会补")
        return None
    try:
        buf = io.BytesIO()
        sf.write(buf, np.asarray(samples, dtype="float32"), SAMPLE_RATE,
                 format="OGG", subtype="OPUS", **OPUS_KW)
        return buf.getvalue()
    except Exception:                                    # noqa: BLE001
        return None


_client = None
_client_lock = threading.Lock()


def _http_post(body: bytes, headers: dict, timeout: float) -> tuple[int, dict | None]:
    """默认的 `post`: httpx 共享连接池(线程安全)。返回 (状态码, JSON 或 None); 网络异常直接抛。"""
    global _client
    import httpx
    with _client_lock:
        if _client is None:
            _client = httpx.Client()
        c = _client
    r = c.post(asr_endpoint.endpoint(), content=body, headers=headers,
               timeout=httpx.Timeout(timeout, connect=min(2.0, timeout)))
    try:
        js = r.json()
    except ValueError:
        js = None
    return r.status_code, js if isinstance(js, dict) else None


# ---------------------------------------------------------------- 结果

@dataclass(frozen=True)
class FinalResult:
    text: str
    source: str            # "groq" / "qwen" / "local-whisper" / "local-parakeet" / "none"
    logprob: float | None  # 只有本地模型会填
    ms: float
    fell_back: bool        # 想上云但最终没用上云的文本(网络 / 熔断 / 退化 / 云端空串被补救)
    steps: tuple[str, ...]  # 按发生顺序, 如 ("cloud:timeout", "local:parakeet")
    #: 测试模式要的原文: primary_text(第一个来源的原文) / primary_degenerate /
    #: fallback_text(真回退时兜底模型的原文, 没回退 = None) / local_text(本地主模型原文)
    trace: dict = field(default_factory=dict)


class _Breaker:
    def __init__(self, clock):
        self._clock = clock
        self._lock = threading.Lock()
        self.fails = 0
        self.open_until: float | None = None
        self._probing = False
        self._announced = False

    def allow(self) -> bool:
        """能不能发请求。开闸期内不能; 过期后放 1 段试探, 试探未回之前其余段不放。"""
        with self._lock:
            if self.open_until is None:
                return True
            if self._clock() < self.open_until or self._probing:
                return False
            self._probing = True
            return True

    def ok(self) -> None:
        with self._lock:
            was_open = self.open_until is not None
            self.fails, self.open_until, self._probing = 0, None, False
            announce, self._announced = was_open and self._announced, False
        if announce:
            echo("☁ 云端转写恢复")

    def fail(self) -> None:
        with self._lock:
            self.fails += 1
            was_probe, self._probing = self._probing, False
            if was_probe or self.fails >= BREAKER_FAILS:
                self.open_until = self._clock() + BREAKER_OPEN_S
                announce, self._announced = not self._announced, True
            else:
                announce = False
        if announce:
            echo("☁ 云端转写暂不可用, 改用本地")


class FinalASR:
    """`local` 可为 None(= 没有 Whisper, 本地就用 `draft`)。`allow_cloud()` 每段问一次。

    测试缝: `post`(替换 HTTP)、`encode`(替换编码)、`clock`(熔断时钟)、`is_stopping`、`deadlines`。
    """

    def __init__(self, local, draft, allow_cloud: Callable[[], bool], *,
                 post=None, encode=None, clock=time.monotonic,
                 is_stopping: Callable[[], bool] | None = None,
                 deadlines: tuple[float, float] = (NORMAL_DEADLINE_S, STOP_DEADLINE_S)):
        self._local = local if local is not None else draft
        self._local_is_draft = local is None or local is draft
        self._draft = draft
        self._allow = allow_cloud
        self._post = post or _http_post
        self._encode = encode or _encode_opus
        self._breaker = _Breaker(clock)
        self._is_stopping = is_stopping or (lambda: False)
        self._deadlines = deadlines
        # 2 个工作线程: 一个请求挂到截止后还在后台跑时, 下一段不必排在它后面。
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="final-asr")

    # -- 对外唯一入口 --
    def transcribe(self, samples) -> FinalResult:
        t0 = time.perf_counter()
        steps: list[str] = []
        trace: dict = {"primary_text": None, "primary_degenerate": False,
                       "fallback_text": None, "local_text": None}
        wanted = False
        try:
            text, source, lp, wanted = self._run(samples, steps, trace)
        except Exception as e:                           # noqa: BLE001  永不抛
            steps.append(f"error:{type(e).__name__}")
            text, source, lp = "", "none", None
        fell_back = wanted and source.startswith("local")
        return FinalResult(text, source, lp, (time.perf_counter() - t0) * 1000,
                           fell_back, tuple(steps), trace)

    # -- 内部 --
    def _run(self, samples, steps, trace):
        if cloud_forced_off() or not self._allow():
            steps.append("off")
            return (*self._local_chain(samples, steps, trace), False)
        if not self._breaker.allow():
            steps.append("breaker")
            return (*self._fallback(samples, steps, trace), True)
        body = self._encode(samples)
        if body is None:
            steps.append("encode")
            return (*self._fallback(samples, steps, trace), True)

        deadline = self._deadlines[1 if self._is_stopping() else 0]
        cloud = self._cloud(body, len(samples) / SAMPLE_RATE, deadline, steps)
        if cloud is not None:
            text, provider = cloud
            trace["primary_text"] = text
            trace["primary_degenerate"] = is_degenerate(text)
            if trace["primary_degenerate"]:
                steps.append("cloud:degenerate")
            elif text:
                steps.append(f"cloud:{provider}")
                return text, provider, None, True
            elif len(samples) / SAMPLE_RATE < EMPTY_RETRY_MIN_S:
                steps.append(f"cloud:{provider}")
                return "", provider, None, True
            else:
                # R5: 云端说是静音, 但段不短 —— 让草稿模型复核一次, 防静默丢句。
                steps.extend((f"cloud:{provider}", "cloud-empty", "local:parakeet"))
                t, lp = self._draft.transcribe_lp(samples)
                if t and not is_degenerate(t):
                    trace["fallback_text"] = t
                    return t, "local-parakeet", lp, True
                return "", provider, None, True
        return (*self._fallback(samples, steps, trace), True)

    def _fallback(self, samples, steps, trace):
        """想上云没上成 → 本地; 本地的结果就是「兜底原文」(测试模式那一格)。"""
        res = self._local_chain(samples, steps, trace)
        if trace["fallback_text"] is None:
            trace["fallback_text"] = res[0]
        return res

    def _cloud(self, body: bytes, seconds: float, deadline: float, steps):
        """成功返回 (text, provider); 任何失败记 steps + 熔断并返回 None。"""
        t_end = time.monotonic() + deadline
        headers = {"Authorization": f"Bearer {asr_endpoint.token()}",
                   "X-ClassLive-Install": install_id(),
                   "X-Audio-Seconds": f"{seconds:.1f}",
                   "Content-Type": "audio/ogg"}
        fut = self._pool.submit(self._post, body, headers, deadline)
        try:
            status, js = fut.result(timeout=max(0.0, t_end - time.monotonic()))
        except FutureTimeout:
            fut.cancel()
            self._breaker.fail()
            steps.append("cloud:timeout")
            return None
        except Exception:                                # noqa: BLE001
            self._breaker.fail()
            steps.append("cloud:error")
            return None
        if status != 200 or js is None or not isinstance(js.get("text"), str):
            self._breaker.fail()
            steps.append(f"cloud:http-{status}" if status != 200 else "cloud:bad-body")
            return None
        self._breaker.ok()
        prov = js.get("provider")
        return js["text"].strip(), prov if prov in ("groq", "qwen") else "cloud"

    def _local_chain(self, samples, steps, trace):
        """本地主模型; 空或退化且主模型不是草稿 → 草稿模型。返回 (text, source, lp)。"""
        name = "parakeet" if self._local_is_draft else "whisper"
        steps.append(f"local:{name}")
        text, lp = self._local.transcribe_lp(samples)
        degen = is_degenerate(text)
        trace["local_text"] = text
        if trace["primary_text"] is None:
            trace["primary_text"], trace["primary_degenerate"] = text, degen
        if (text and not degen) or self._local_is_draft:
            return text, f"local-{name}", lp
        steps.append("degenerate" if text else "empty-local")
        steps.append("local:parakeet")
        text2, lp2 = self._draft.transcribe_lp(samples)
        trace["fallback_text"] = text2
        return text2, "local-parakeet", lp2


def load_final(final_model_dir: str, draft_asr, allow_cloud: Callable[[], bool], *,
               preload_local: bool, **kw) -> FinalASR:
    """装配 FinalASR。`preload_local` 为真才加载 Whisper(R4: 云端开着时省约 1 GB 内存)。

    Whisper 缺失 / 加载失败**不退出**: 本地兜底改用草稿模型(Parakeet), 启动时说一句。
    """
    local = None
    if preload_local and not os.path.isdir(os.path.expanduser(final_model_dir or "")):
        echo("💻 本地定稿: Parakeet(没装可选的 Whisper)")
    elif preload_local:
        try:
            from asr import load_final_asr
            local = load_final_asr(final_model_dir)
            echo("💻 本地定稿: Whisper(云端不可用时用)")
        except Exception as e:                           # noqa: BLE001
            echo(f"⚠ 本地 Whisper 没加载成功, 定稿兜底改用 Parakeet({str(e).splitlines()[0]})")
    else:
        echo("💻 本地定稿兜底: Parakeet(启动时云端可用, 没预加载 Whisper)")
    return FinalASR(local, draft_asr, allow_cloud, **kw)
