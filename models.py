#!/usr/bin/env python3
"""**模型的唯一注册表** —— 「有哪些本地模型、住哪、从哪来、多大」。

## 为什么要有这个文件

在这之前，一个模型的身份**散在 3~4 处**，换一个模型要同时改、而且它们会漂：

| 模型 | 原来写在哪 |
|---|---|
| Parakeet | 这里 · `main.py` 的 `--model-dir` 默认值 · `testmode.py` 的目录名元组 |
| Whisper | 这里 · `main.py` 的 `--final-model-dir` 默认值 · `testmode.py` |
| VAD | 这里 · **`vad.py` 的 `VAD_MODEL` 常量** · `testmode.py` |
| Qwen3 | 这里 · `main.py` 的 `--llm` 默认值 |

现在所有消费者都从这里读。

## ⚠️ 这个文件**只有数据，没有 I/O**

它 `import` 的东西全是标准库，**没有** `huggingface_hub`、**没有**文件系统调用、**没有**副作用。
「在不在」「完整吗」「是哪一版」那些**要碰硬盘的问题一律不在这里**
（在 `doctor.py`），因为：
- `vad.py`（核心音频）**不该依赖 `doctor.py`**（一个诊断脚本）—— 依赖方向是反的；
- 注册表要能被任何模块 import 而不触发 I/O（社区那条「pure data registry」的共识）。

## ⚠️ 形状是**冻结的表**，不是注册机制 —— 这是有意的

`[一手]` Google Python Style Guide §2.5「Mutable Global State」：
**「Module-level constants are permitted and encouraged」**，
同时把 **「global registries」** 列为可变全局状态的典型危害
（「Has the potential to change module behavior during the import」）。

→ 所以：**一张 `tuple`，值全是数据，没有装饰器注册、没有导入期变异、没有 `register()`**。
   换模型 = 改这张表里的一行。
⚠️ **反对面也真实存在**（Pyramid 自己的 `designdefense.rst` 承认它的注册表
   「is not particularly pretty or intuitive… the conceptual load on a casual
   source code reader is somewhat high」）→ 对策是**不做查找抽象层**：
   消费者直接 `import models` 然后读字段，不要 `models.get(name)` 那种间接。
   `by_key()` 是给人写调用点用的**直白**辅助，不是一层间接。
"""
from __future__ import annotations

import os
import pathlib
import shlex
import sys
from typing import NamedTuple


class Model(NamedTuple):
    """一个本地模型。**全是数据** —— 怎么判断它在不在、怎么下，都不在这里。

    `mb` 是**下载体积**（MB）—— 做加法用（「一共还差 1.2 GB」），也是引导式更新拿来
    问「要下 X 吗」的那个数。`size` 是从它派生的一句话，**不是第二个定义点**。
    """
    key: str          # 稳定标识（`main.py` / `vad.py` / `testmode.py` 按它取）
    path: str         # 规范路径。⚠️ 「一台机器两份」就靠「只认这一个路径」堵
    label: str        # 给人看的中文名
    required: bool    # 缺了就跑不起来？
    cmd: str          # ⚠️ 必须**可直接执行**（引导式更新会真的跑它）
    mb: float         # 下载体积（MB）
    src: str = ""     # HF repo id 或下载 URL —— 版本核实的**身份**
    at_hf: bool = False   # 住在 HuggingFace cache 里（判存在的方式不同，见 doctor）
    extra: str = ""   # 体积那句话里 `mb` 之外的信息（如「解开后 989 MB」）

    @property
    def size(self) -> str:
        return f"约 {self.mb:g} MB{self.extra}"


PARAKEET_SRC = "csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8"
VAD_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/"
           "asr-models/silero_vad.onnx")
WHISPER_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/"
               "asr-models/sherpa-onnx-whisper-turbo.tar.bz2")
QWEN_SRC = "mlx-community/Qwen3-1.7B-4bit"

# 模型都放在这儿。⚠️ 与 `paths.py` 那族不一样：那是**用户数据**（课件、档案），
# 这是**模型权重**（可重下、不进 git、也不算用户资产）。所以没有搬进 `~/.classlive/`。
MODELS_ROOT = "~/models"
#: `MODELS_ROOT` 的 **shell 形态**（`~/models` → `$HOME/models`）—— 给下面那些
#: `cmd` 用。⚠️ 2026-09-30 全量 OCR 审查发现 `PARAKEET.cmd` 里**手写了第二份
#: 路径**：改了 `MODELS_ROOT` 而没改它的话，引导式更新会下到旧路径、doctor 报
#: 「缺模型」—— 而 `MODELS_ROOT` 存在的全部意义就是防这个。
MODELS_ROOT_SH = "$HOME" + MODELS_ROOT[1:]

PARAKEET = Model(
    "parakeet", f"{MODELS_ROOT}/parakeet-tdt-0.6b-v3-int8",
    "Parakeet ASR 模型(必需)", True,
    # ⚠️ `shlex.quote`（2026-10-01 审查 F27）：仓库在含空格的路径下（朋友 clone 到
    #    `~/My Projects/`）不引号的话，`{exe} -c …` 会被 shell 切开 → 下载直接失败。
    f'{shlex.quote(sys.executable)} -c "from huggingface_hub import snapshot_download; '
    f"snapshot_download('{PARAKEET_SRC}', "
    f"local_dir='{MODELS_ROOT_SH}/parakeet-tdt-0.6b-v3-int8')\"",
    640.0, src=PARAKEET_SRC)

VAD = Model(
    "vad", f"{MODELS_ROOT}/vad/silero_vad.onnx", "Silero VAD(必需)", True,
    # ⚠️ **`-f` 不能漏**（2026-09-28 审查指出）：没有它时 HTTP 404/5xx **curl 照样以 0 退出**，
    #    把错误页（HTML）**原样写成 `silero_vad.onnx`** —— 于是 `doctor` 看见"文件在"、
    #    报 ✅，而 VAD 其实是坏的。`-S` 让 `-f` 失败时**打出原因**（否则 `-s` 把它吞了）。
    "mkdir -p ~/models/vad && curl -fsSL -o ~/models/vad/silero_vad.onnx " + VAD_URL,
    0.61, src=VAD_URL)

WHISPER = Model(
    "whisper", f"{MODELS_ROOT}/sherpa-onnx-whisper-turbo",
    "定稿 Whisper 模型(必需)", True,
    # ⚠️ 同 `VAD` 那条：`-f` 加上（这里即使漏了，后面 `tar` 也会失败 —— 但错误信息
    #    会指向"tar 解不开"，而不是"下载就是 404"）。
    # ⚠️ 固定 `/tmp/wt.tar.bz2` 换成 `mktemp`（2026-10-01 审查 F27）：固定名可被同机
    #    预置符号链接（`curl -o` 会跟随），面板下载与 `cl update` 并发时也会互相覆盖。
    't="$(mktemp -t classlive-wt)" && curl -fsSL -o "$t" ' + WHISPER_URL + " && "
    'tar xjf "$t" -C ~/models/ && rm -f "$t"',
    538.0, src=WHISPER_URL, extra="（解开后 989 MB）")

# ⚠️ **可选**，但它是「没配 API key 时唯一的翻译引擎」，所以必须让人**看得见**。
#    它今天完全在 doctor 视野之外：`mlx_lm.load()` 会在第一次翻译时**隐式**
#    `snapshot_download` 938 MB —— 而 README 和 doctor 都写着「模型不会自动下」。
#    （2026-09-28 查出。）
#    ⚠️ **它住在 HF 的 cache 里**（`~/.cache/huggingface/hub/models--…`），
#       不是 `~/models/` —— `mlx_lm` 就是从那儿读的。搬走 = 制造"两份"。
QWEN = Model(
    "llm", "~/.cache/huggingface/hub/models--mlx-community--Qwen3-1.7B-4bit",
    "本地翻译模型 Qwen3-1.7B(可选)", False,
    # ⚠️ `shlex.quote` 同上（2026-10-01 审查 F27）。
    f'{shlex.quote(sys.executable)} -c "from huggingface_hub import snapshot_download; '
    f"snapshot_download('{QWEN_SRC}')\"",
    938.0, src=QWEN_SRC, at_hf=True)

# 注册表本体。⚠️ 是 `tuple` 不是 `list` —— **冻结**，不给任何人 append 的机会。
MODELS: tuple[Model, ...] = (PARAKEET, VAD, WHISPER, QWEN)

REQUIRED: tuple[Model, ...] = tuple(m for m in MODELS if m.required)


def by_key(key: str) -> Model | None:
    """按 `key` 取一条。取不到给 `None`（**不抛** —— 调用点多半有默认值要退回）。

    ⚠️ 这是**给人写调用点用的直白辅助**，不是一层查找抽象：它直读上面的 tuple，
       没有缓存、没有副作用、没有导入期变异。消费者照样可以直接 `for m in MODELS`。
    """
    for m in MODELS:
        if m.key == key:
            return m
    return None


def path_of(key: str, default: str = "") -> str:
    """某一条的路径（`~` 已展开）。取不到给 `default`。

    ⭐ 这是 `main.py` 的 argparse 默认值、`vad.py` 的 `VAD_MODEL`、`testmode.py`
       的目录名**共同的入口** —— 它们原来各自抄了一遍路径。
    """
    m = by_key(key)
    return os.path.expanduser(m.path) if m else default


def basename_of(key: str, default: str = "") -> str:
    """某一条**在 `~/models/` 下的那个顶层条目名**（给 `testmode` 报体积用）。

    ⚠️⚠️ **别用 `Path.suffix` 猜「是文件还是目录」**（2026-09-28 审查指出）：
       `parakeet-tdt-0.6b-v3-int8` 这种**目录名里带点**的，`suffix` 是
       `.6b-v3-int8`（真值）→ 会被当成文件 → 取到父目录名 `models`
       → 报出来的体积指向一个**根本不存在**的路径。
       改成「相对 `MODELS_ROOT` 取第一段」：既不用 stat 硬盘（本模块不碰 I/O
       这条不变），也对带点的名字免疫。
    """
    m = by_key(key)
    if not m:
        return default
    try:
        rel = pathlib.Path(os.path.expanduser(m.path)).relative_to(
            os.path.expanduser(MODELS_ROOT))
    except ValueError:
        return default
    return rel.parts[0] if rel.parts else default
