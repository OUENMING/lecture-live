#!/usr/bin/env python3
"""下载一个模型：**直连 → 镜像 → 校验**。

    python fetch_model.py <vad | nemotron>

`models.py` 里每个模型的 `cmd` 都指向这里 —— 面板上的「语音模型」、`cl update`、
`cl doctor` 打出来的命令走**同一条路**。

## 为什么要有它（2026-10-04 一位国内新用户的实测）

原来的 `cmd` 是一条裸 `curl -fsSL`：
- GitHub 直连在国内会**一直不动**（curl 没有连接超时；面板只显示「正在下…」，
  直到 `update.download_model` 那一小时的总超时才报错）；
- 没有任何回退，用户只能自己去找镜像、自己拼命令。

## 做法

1. **直连先试**，连接 8 秒连不上、或持续 60 秒低于 5 KB/s 就放弃（`curl --connect-timeout` /
   `--speed-limit`）。同一来源最多续传 3 次（`-C -`）。
2. 直连不行 → 依次换**镜像**：GitHub 资产走第三方前缀代理，最后兜我们自己放在
   `git.bldcam.page/models/` 的那份（原名，不是前缀代理）。
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

#: 钉死的 sha256。**只对镜像来的文件强制**（见模块说明第 3 条）。
PINS = {
    # GitHub release 资产（2026-10-04 下完整文件算的）
    "vad": "9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6",
    # GitHub release API 的 `digest`（2026-10-09 取）；560 ms 版。1120 ms 版是 840c48de…
    "nemotron": "78e2b79fcf7271553a74402a76b771b09ea40117a39566a79f52235b23db6358",
    # ⚠️ 模型换了上游文件（同仓同路径）时要跟着改这里，否则走镜像的人下完被判
    #    「校验不对」→ 永远装不上（直连不校 sha，直连的人不暴露）。
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


#: 我们自己的镜像站（2026-11-20 加）。它**不是**前缀代理：文件按**原名**放在 `/models/` 下，
#: 所以只看 URL 的最后一段 —— 上游哪天换文件名，这里自动跟着走，不用改名单。
#: ⚠️ `mirror=True` → **强制**过 `PINS` 的 sha256（第三方镜像一律不信）。
VPS_MIRROR = "https://git.bldcam.page/models/"


def gh_sources(url: str) -> list[Source]:
    """一份 GitHub release 资产的来源清单：直连 → 前缀代理 → 我们自己的 VPS。"""
    return ([Source("直连", url, False)]
            + [Source(m.split("//", 1)[1].rstrip("/"), m + url, True) for m in GH_MIRRORS]
            + [Source("git.bldcam.page", VPS_MIRROR + url.rsplit("/", 1)[-1], True)])


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
def fetch_tarball(models_root: pathlib.Path, url: str, *, pin: str,
                  say: Callable[[str], None] = print) -> tuple[bool, str]:
    """下 tar.bz2（`pin` 是该包的 sha256；Nemotron 走这里） → 校验 → 在临时目录里解开 → **整体**挪进 `models_root/`。

    ⚠️ 先解到临时目录再挪：解压中途出错不会在模型目录里留下半截（`doctor.model_present`
       只看「目录非空」，半截目录会被当成已就位）。路径穿越由 `filter="data"` 拦（并只解压一遍）。
    """
    models_root.mkdir(parents=True, exist_ok=True)
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="classlive-wt-", dir=str(models_root)))
    try:
        tarball = tmp / "wt.tar.bz2"
        ok, why = download_file(gh_sources(url), tarball, pin, say=say)
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


def _verify_pins(root: pathlib.Path | None, pins: dict[str, str]) -> str | None:
    """返回 None = 全对；否则一句话说哪个不对。"""
    for name, sha in pins.items():
        p = root / name if root else None
        if p is None or not p.is_file():
            return f"{name} 不在"
        if sha256_of(p) != sha:
            return f"{name} 校验值不对"
    return None


KEYS = ("vad", "nemotron")


def fetch(key: str, *, say: Callable[[str], None] = print) -> tuple[bool, str]:
    """下载 `key` 对应的模型。**目的地一律取自 `models` 注册表**（不在这里再写一份路径）。"""
    import models
    path = pathlib.Path(models.path_of(key))
    if key == "vad":
        return download_file(gh_sources(models.VAD_URL), path, PINS["vad"], say=say)
    if key == "nemotron":
        return fetch_tarball(path.parent, models.NEMOTRON_URL, pin=PINS["nemotron"], say=say)
    # ⚠️ **响亮失败，别兜底成 llm**：原来最后一句是无条件 `return`，任何不认识的 key
    #    都会去下 938 MB 的 Qwen3 —— 将来给 MODELS 加第 5 个模型却忘了在这里加分支时，
    #    就是「静默下一份最大的」。`main()` 的 KEYS 只挡命令行，`fetch()` 是公开入口。
    raise ValueError(f"未知模型 key：{key!r}（可用：{'/'.join(KEYS)}）")


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in KEYS:
        # ⚠️ 前两段一起打：第 1 段只有标题那一行，带 `<vad | nemotron>`
        #    的用法在第 2 段 —— 只打第 1 段等于不告诉用户有哪些 key。
        print("\n\n".join(__doc__.split("\n\n")[:2]))
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
