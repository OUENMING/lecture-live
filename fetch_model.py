#!/usr/bin/env python3
"""下载一个模型：**直连 → 镜像 → 校验**。

    python fetch_model.py <vad | whisper | parakeet | llm>

`models.py` 里每个模型的 `cmd` 都指向这里 —— 面板上的「语音模型」、`cl update`、
`cl doctor` 打出来的命令走**同一条路**。

## 为什么要有它（2026-10-04 一位国内新用户的实测）

原来的 `cmd` 是一条裸 `curl -fsSL` / `snapshot_download`：
- GitHub 直连在国内会**一直不动**（curl 没有连接超时；面板只显示「正在下…」，
  直到 `update.download_model` 那一小时的总超时才报错）；
- 没有任何回退，用户只能自己去找镜像、自己拼命令。

## 做法

1. **直连先试**，连接 8 秒连不上、或持续 60 秒低于 5 KB/s 就放弃（`curl --connect-timeout` /
   `--speed-limit`）。同一来源最多续传 3 次（`-C -`）。
2. 直连不行 → 依次换**镜像**：GitHub 资产走前缀代理，HuggingFace 走 `hf-mirror.com`。
3. ⚠️ **镜像是第三方，凡是从镜像来的文件必须过 sha256**（下面的 `PINS`）。对不上就删掉、换下一个。
   直连来的（TLS 直达官方）**不强制**校验 —— 上游哪天重传了文件，我们的钉死值会过期，
   那不该把直连的人拦住。
   · GitHub 资产的值是我们自己下了完整文件算的；
   · HF 的 LFS 文件的值是 HF 官方 API 给的 `lfs.sha256`（`?blobs=true`），与本机已装的那份一致。
4. 全部失败 → 打一行**能直接看懂**的原因（面板会把最后一行显示出来）。

⚠️ 镜像名单是**会腐坏的**东西：`ghfast.top` 在测试那台机器的网络上返回了**自签证书**
（连接被中间人换了）—— 不在名单里，也**绝不用 `-k` 绕过**。名单失效时直连仍然可用，只是国内慢。
"""
from __future__ import annotations

import hashlib
import os
import pathlib
import shutil
import subprocess
import sys
import tarfile
import tempfile
from typing import Callable, NamedTuple

#: 前缀代理（拼成 `<前缀><原始 URL>`）。⚠️ gh-proxy.com 不支持 Range（续传会被拒 → 我们会退成整段重下）。
GH_MIRRORS = ("https://ghproxy.net/", "https://gh-proxy.com/")
HF_MIRROR = "https://hf-mirror.com"

#: 钉死的 sha256。**只对镜像来的文件强制**（见模块说明第 3 条）。
PINS = {
    # GitHub release 资产（2026-10-04 下完整文件算的）
    "vad": "9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6",
    "whisper": "b11acbbcd660b44a8e0df33724feb5aaa709cf65668f2823d59f656312544f22",
    # HuggingFace LFS 文件（HF 官方 API 的 lfs.sha256；tokens.txt 非 LFS，取本机已装那份）
    "parakeet": {
        "encoder.int8.onnx": "acfc2b4456377e15d04f0243af540b7fe7c992f8d898d751cf134c3a55fd2247",
        "decoder.int8.onnx": "179e50c43d1a9de79c8a24149a2f9bac6eb5981823f2a2ed88d655b24248db4e",
        "joiner.int8.onnx": "3164c13fc2821009440d20fcb5fdc78bff28b4db2f8d0f0b329101719c0948b3",
        "tokens.txt": "d58544679ea4bc6ac563d1f545eb7d474bd6cfa467f0a6e2c1dc1c7d37e3c35d",
    },
    "llm": {
        "model.safetensors": "0e86d9677e519323849eac1bc272caae88567a481ff188c431f70be543d9995f",
        "tokenizer.json": "aeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4",
    },
}


class Source(NamedTuple):
    label: str          # 给人看：「直连」「ghproxy.net」
    url: str
    mirror: bool        # True = 第三方 → 必须过 sha


def sha256_of(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def looks_like_html(path: pathlib.Path) -> bool:
    """强制门户 / 代理报错页常以 200 返回一页 HTML —— `curl -f` 拦不住，也不该被当成模型。"""
    try:
        with open(path, "rb") as f:
            head = f.read(64).lstrip().lower()
    except OSError:
        return False
    return head.startswith((b"<!doctype", b"<html", b"<?xml", b"<head", b"<body"))


def gh_sources(url: str) -> list[Source]:
    return [Source("直连", url, False)] + [
        Source(m.split("//", 1)[1].rstrip("/"), m + url, True) for m in GH_MIRRORS]


# ---------------------------------------------------------------- 下载一个文件
def _curl(url: str, part: pathlib.Path, resume: bool) -> int:
    """一次 curl。返回退出码。连接 8s 超时；持续 60s 低于 5KB/s 放弃。"""
    cmd = ["curl", "-fL", "--connect-timeout", "8", "--speed-limit", "5000", "--speed-time", "60",
           "-o", str(part)]
    if resume:
        cmd += ["-C", "-"]
    cmd += ["-#"] if sys.stderr.isatty() else ["-sS"]
    return subprocess.run(cmd + [url]).returncode


def _nonempty(path: pathlib.Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def _reject(src: Source, part: pathlib.Path, rc: int, sha: str | None) -> str | None:
    """这次下载**不能收**的理由；`None` = 可以收。"""
    if rc == 0 and part.is_file() and looks_like_html(part):
        return f"{src.label} 返回的是一页网页，不是模型文件（可能被网络拦截）"
    if rc != 0 or not _nonempty(part):
        return f"{src.label} 连不上或太慢（curl 退出码 {rc}）"
    if src.mirror:
        if not sha:
            return f"{src.label}：没有可对照的 sha256，不收第三方文件"
        got = sha256_of(part)
        if got != sha:
            return (f"{src.label} 给的文件校验值不对（{got[:12]}… ≠ {sha[:12]}…）"
                    f"—— 镜像被改动，或上游换了文件")
    return None


def download_file(sources: list[Source], dest: pathlib.Path, sha: str | None, *,
                  fetch: Callable[[str, pathlib.Path, bool], int] = _curl,
                  say: Callable[[str], None] = print) -> tuple[bool, str]:
    """依次试各个来源，成功就把文件放到 `dest`。返回 `(ok, 说明)`。

    ⚠️ 每个来源用**自己的** `.part`，换来源前删掉（不同来源的半截文件不能混着续）。
    ⚠️ 镜像来的文件 `sha` 为空 → 当失败处理（宁可不下也不收一份没法验的第三方文件）。
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    last = "没有可用的来源"
    for src in sources:
        part.unlink(missing_ok=True)
        say(f"· 从 {src.label} 下载 …")
        rc = -1
        for attempt in range(3):                      # 同一来源最多续传 3 次
            rc = fetch(src.url, part, attempt > 0)
            if rc == 33 and part.exists():            # 服务端不支持续传 → 丢掉半截、整段重来一次
                part.unlink(missing_ok=True)
                rc = fetch(src.url, part, False)
            if rc == 0 or not _nonempty(part):        # 成功，或一个字节都没收到（连不上 / 被拒）→ 不再重试
                break
        why = _reject(src, part, rc, sha)
        if why:
            last = why
            say(f"  ✗ {why}")
            part.unlink(missing_ok=True)
            continue
        if src.mirror:
            say("  ✓ sha256 校验通过")
        os.replace(part, dest)
        return True, src.label
    return False, last


# ---------------------------------------------------------------- 各模型
def fetch_whisper(models_root: pathlib.Path, url: str, *,
                  say: Callable[[str], None] = print) -> tuple[bool, str]:
    """下 tar.bz2 → 校验 → 在临时目录里解开 → **整体**挪进 `models_root/`。

    ⚠️ 先解到临时目录再挪：解压中途出错不会在模型目录里留下半截（`doctor.model_present`
       只看「目录非空」，半截目录会被当成已就位）。路径穿越由 `filter="data"` 拦（并只解压一遍）。
    """
    models_root.mkdir(parents=True, exist_ok=True)
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="classlive-wt-", dir=str(models_root)))
    try:
        tarball = tmp / "wt.tar.bz2"
        ok, why = download_file(gh_sources(url), tarball, PINS["whisper"], say=say)
        if not ok:
            return False, why
        out = tmp / "out"
        with tarfile.open(tarball, "r:bz2") as tf:
            tf.extractall(out, filter="data")
        for child in out.iterdir():
            target = models_root / child.name
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
            os.replace(child, target)
        return True, why
    except tarfile.FilterError as e:
        return False, f"压缩包里有越界路径或不允许的成员：{e}"
    except (tarfile.TarError, OSError, EOFError) as e:      # EOFError = bz2 流被截断
        return False, f"解压失败：{e}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _hf_download(repo: str, local_dir: str | None, endpoint: str | None) -> int:
    """在**子进程**里跑 snapshot_download —— `HF_ENDPOINT` 要在 import 之前进环境才生效。"""
    env = dict(os.environ)
    env.setdefault("HF_HUB_ETAG_TIMEOUT", "10")
    env.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "30")
    if endpoint:
        env["HF_ENDPOINT"] = endpoint
    code = ("import sys; from huggingface_hub import snapshot_download; "
            "snapshot_download(sys.argv[1], local_dir=sys.argv[2] or None)")
    return subprocess.run([sys.executable, "-c", code, repo, local_dir or ""], env=env).returncode


def _hf_snapshot_dir(repo: str, filename: str) -> pathlib.Path | None:
    """HF 缓存里某个文件所在的 snapshot 目录（离线查，和 `doctor.hf_cached` 同一个入口）。"""
    from huggingface_hub import try_to_load_from_cache
    p = try_to_load_from_cache(repo, filename)
    return pathlib.Path(p).parent if isinstance(p, str) else None


def _verify_pins(root: pathlib.Path | None, pins: dict[str, str]) -> str | None:
    """返回 None = 全对；否则一句话说哪个不对。"""
    for name, sha in pins.items():
        p = root / name if root else None
        if p is None or not p.is_file():
            return f"{name} 不在"
        if sha256_of(p) != sha:
            return f"{name} 校验值不对"
    return None


def fetch_hf(repo: str, local_dir: pathlib.Path | None, pins: dict[str, str], *,
             find_dir: Callable[[], pathlib.Path | None], purge: pathlib.Path,
             download: Callable[[str, str | None, str | None], int] = _hf_download,
             say: Callable[[str], None] = print) -> tuple[bool, str]:
    """先直连（短超时），不行换 hf-mirror；**镜像来的必须过 pins**，不对就整个 `purge` 掉。

    `find_dir()` = 下完之后「那几个文件所在的目录」；`purge` = 校验失败时要删掉的目录。
    """
    for label, endpoint, mirror in (("直连 HuggingFace", None, False),
                                    ("hf-mirror.com", HF_MIRROR, True)):
        say(f"· 从 {label} 下载 {repo} …")
        rc = download(repo, str(local_dir) if local_dir else None, endpoint)
        if rc != 0:
            say(f"  ✗ {label} 失败（退出码 {rc}）")
            continue
        if mirror:
            bad = _verify_pins(find_dir(), pins)
            if bad:
                say(f"  ✗ {label} 的文件 {bad} —— 镜像被改动，或上游换了文件；已丢弃")
                shutil.rmtree(purge, ignore_errors=True)
                continue
            say("  ✓ sha256 校验通过")
        return True, label
    return False, "直连和镜像都没下成"


KEYS = ("vad", "whisper", "parakeet", "llm")


def fetch(key: str, *, say: Callable[[str], None] = print) -> tuple[bool, str]:
    """下载 `key` 对应的模型。**目的地一律取自 `models` 注册表**（不在这里再写一份路径）。"""
    import models
    path = pathlib.Path(models.path_of(key))
    if key == "vad":
        return download_file(gh_sources(models.VAD_URL), path, PINS["vad"], say=say)
    if key == "whisper":
        return fetch_whisper(path.parent, models.WHISPER_URL, say=say)
    if key == "parakeet":
        return fetch_hf(models.PARAKEET_SRC, path, PINS["parakeet"],
                        find_dir=lambda: path, purge=path, say=say)
    return fetch_hf(models.QWEN_SRC, None, PINS["llm"], purge=path, say=say,
                    find_dir=lambda: _hf_snapshot_dir(models.QWEN_SRC, "model.safetensors"))


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in KEYS:
        print(__doc__.split("\n\n")[0])
        return 2
    import models
    ok, why = fetch(argv[1])
    name = models.by_key(argv[1]).label
    if ok:
        print(f"✓ {name} 已就位（来源：{why}）")
        return 0
    # ⚠️ 失败的结论写到 **stderr**：面板（`update.download_model`）显示的是 stderr 的最后一行，
    #    不然它会显示 curl 自己的那行 `curl: (28) …`，而不是这句人话。
    print(f"✗ {name} 下载失败：{why}。网络通了再点一次；或手动下载，见 README「手动下载模型」。",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
