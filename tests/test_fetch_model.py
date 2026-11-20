#!/usr/bin/env python3
"""`fetch_model.py`（直连 → 镜像 → sha256）的判据 —— **全离线**：本机起一个 HTTP 服务器当「上游/镜像」。

    ClassLive.app/Contents/MacOS/python tests/test_fetch_model.py

## 钉住什么

1. ⭐⭐ **镜像来的文件必须过 sha256**：对不上 → 丢掉、不落盘、换下一个；没有可对照的值 → 直接不收。
2. ⭐ 直连来的**不强制** sha（上游重传文件时不能把直连的人拦住）。
3. ⭐ 直连不通 → 自动换镜像；半截文件不会留在目标位置。
4. 「返回的是一页网页」（强制门户 / 代理报错页）不当成模型。
5. 服务端不支持续传（curl 退出码 33）→ 丢掉半截、整段重下，不死循环。
6. Whisper 的 tar：越界路径拒收；解完 tar 不留。
7. HF 那条：直连失败 → hf-mirror；**镜像来的**必须过 pins，不对就 `cleanup()`。
8. `models.py` 里的 `cmd` 都指向 `fetch_model.py <key>`，且 `shlex.split` 后恰好 3 段。

⚠️ 不碰真网络、不碰 `~/models/`（全在临时目录里）。
"""
from __future__ import annotations

import hashlib
import http.server
import io
import pathlib
import shlex
import sys
import tarfile
import tempfile
import threading

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import fetch_model as F                                              # noqa: E402

CASES: list[tuple[str, object]] = []


def case(name: str):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


PAYLOAD = b"\x00ONNX-model-bytes" * 5000
OTHER = b"\x01evil-bytes" * 5000
SHA = hashlib.sha256(PAYLOAD).hexdigest()


class _Srv:
    """本机 HTTP 服务器：`routes = {路径: bytes}`。"""

    def __init__(self, routes: dict):
        routes_ = routes

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):                                        # noqa: N802
                body = routes_.get(self.path)
                if body is None:
                    self.send_response(404); self.end_headers(); return
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers(); self.wfile.write(body)

            def log_message(self, *a):                               # noqa: D401
                pass

        self.s = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.s.server_address[1]}"
        threading.Thread(target=self.s.serve_forever, daemon=True).start()

    def close(self):
        self.s.shutdown(); self.s.server_close()


DEAD = "http://127.0.0.1:1/never"                                     # 连接被拒绝（秒回）
quiet = lambda s: None                                                # noqa: E731


def _src(label, url, mirror):
    return F.Source(label, url, mirror)


@case("⭐ 直连不通 → 换镜像；镜像文件 sha 对 → 落盘，且没有残留 `.part`")
def t_fallback_to_mirror_ok():
    srv = _Srv({"/m": PAYLOAD})
    try:
        with tempfile.TemporaryDirectory() as td:
            dest = pathlib.Path(td) / "sub" / "m.bin"
            ok, why = F.download_file([_src("直连", DEAD, False), _src("镜像", srv.url + "/m", True)],
                                      dest, SHA, say=quiet)
            assert ok and why == "镜像", (ok, why)
            assert dest.read_bytes() == PAYLOAD, "内容不对"
            assert not list(dest.parent.glob("*.part")), "残留了半截文件"
    finally:
        srv.close()


@case("⭐ 我们自己的 VPS 镜像：按**原名**放在 /models/ 下，且强制过 sha")
def t_vps_mirror_in_sources():
    url = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx"
    srcs = F.gh_sources(url)
    labels = [s.label for s in srcs]
    assert labels[0] == "直连" and labels[-1] == "git.bldcam.page", labels
    vps = srcs[-1]
    # ⚠️ 它不是前缀代理：只拼 URL 的**文件名**
    assert vps.url == "https://git.bldcam.page/models/silero_vad.onnx", vps.url
    assert vps.mirror is True, "自家镜像也必须过 sha —— 第三方存哪儿都一样不可全信"


@case("⭐⭐ 镜像给的文件 sha 对不上 → 拒收：不落盘、不留 `.part`、说清原因")
def t_mirror_wrong_bytes_rejected():
    srv = _Srv({"/m": OTHER})
    try:
        with tempfile.TemporaryDirectory() as td:
            dest = pathlib.Path(td) / "m.bin"
            ok, why = F.download_file([_src("镜像", srv.url + "/m", True)], dest, SHA, say=quiet)
            assert not ok and "校验值不对" in why, (ok, why)
            assert not dest.exists(), "被篡改的文件不许落盘"
            assert not list(pathlib.Path(td).glob("*.part")), "残留了半截文件"
    finally:
        srv.close()


@case("⭐ 镜像来的文件**没有可对照的 sha** → 不收（宁可不下，也不收一份验不了的第三方文件）")
def t_mirror_without_sha_refused():
    srv = _Srv({"/m": PAYLOAD})
    try:
        with tempfile.TemporaryDirectory() as td:
            dest = pathlib.Path(td) / "m.bin"
            ok, why = F.download_file([_src("镜像", srv.url + "/m", True)], dest, None, say=quiet)
            assert not ok and "sha256" in why and not dest.exists(), (ok, why)
    finally:
        srv.close()


@case("⭐ 直连来的**不强制** sha（上游换了文件时不能把直连的人拦在门外）")
def t_direct_not_enforced():
    srv = _Srv({"/m": OTHER})
    try:
        with tempfile.TemporaryDirectory() as td:
            dest = pathlib.Path(td) / "m.bin"
            ok, why = F.download_file([_src("直连", srv.url + "/m", False)], dest, SHA, say=quiet)
            assert ok and dest.read_bytes() == OTHER, (ok, why)
    finally:
        srv.close()


@case("直连返回 200 + 一页 HTML（强制门户）→ 不当成模型，继续换下一个来源")
def t_html_page_rejected():
    html = b"<!DOCTYPE html><html><body>Please log in to the Wi-Fi</body></html>" * 20
    srv = _Srv({"/portal": html, "/m": PAYLOAD})
    try:
        with tempfile.TemporaryDirectory() as td:
            dest = pathlib.Path(td) / "m.bin"
            ok, why = F.download_file([_src("直连", srv.url + "/portal", False),
                                       _src("镜像", srv.url + "/m", True)], dest, SHA, say=quiet)
            assert ok and why == "镜像" and dest.read_bytes() == PAYLOAD, (ok, why)
    finally:
        srv.close()


@case("全部来源都不行 → 失败，说出最后一个原因，目标位置什么都没有")
def t_all_fail():
    with tempfile.TemporaryDirectory() as td:
        dest = pathlib.Path(td) / "m.bin"
        ok, why = F.download_file([_src("直连", DEAD, False), _src("镜像", DEAD, True)],
                                  dest, SHA, say=quiet)
        assert not ok and "连不上" in why and not dest.exists(), (ok, why)


@case("连都连不上的来源只试**一次**（不白等 3 轮超时）；中途断了的才续传重试")
def t_dead_source_tried_once():
    calls = []

    def dead(url, part, resume):
        calls.append(url); return 7
    with tempfile.TemporaryDirectory() as td:
        ok, why = F.download_file([_src("直连", "x://a", False)], pathlib.Path(td) / "m", None,
                                  fetch=dead, say=quiet)
        assert not ok and len(calls) == 1, (ok, calls)

    mid = []

    def flaky(url, part, resume):
        mid.append(resume)
        if len(mid) < 3:
            with open(part, "ab") as f:
                f.write(PAYLOAD[: 100 * len(mid)])
            return 18                                               # 收了一点就断
        part.write_bytes(PAYLOAD); return 0
    with tempfile.TemporaryDirectory() as td:
        ok, why = F.download_file([_src("直连", "x://a", False)], pathlib.Path(td) / "m", None,
                                  fetch=flaky, say=quiet)
        assert ok and mid == [False, True, True], (ok, mid)


@case("⭐ 服务端不支持续传（curl 退出码 33）→ 丢掉半截、整段重下一次，不死循环")
def t_range_unsupported():
    calls = []

    def fake(url, part, resume):
        calls.append(resume)
        if len(calls) == 1:
            part.write_bytes(b"half"); return 18                    # 第一次：断了
        if resume:
            return 33                                               # 续传被拒
        part.write_bytes(PAYLOAD); return 0                         # 整段重下
    with tempfile.TemporaryDirectory() as td:
        dest = pathlib.Path(td) / "m.bin"
        ok, why = F.download_file([_src("镜像", "x://", True)], dest, SHA, fetch=fake, say=quiet)
        assert ok and dest.read_bytes() == PAYLOAD, (ok, why, calls)
        assert calls == [False, True, False], f"调用序列不对：{calls}"


def _tar_bytes(members: dict) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:bz2") as tf:
        for name, data in members.items():
            ti = tarfile.TarInfo(name); ti.size = len(data); tf.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


@case("Nemotron：下 tar → 解到模型目录 → 不留 tar / 临时目录")
def t_tarball_extracts_and_cleans():
    tb = _tar_bytes({"sherpa-onnx-nemotron-x/tokens.txt": b"tok",
                     "sherpa-onnx-nemotron-x/encoder.int8.onnx": b"enc"})
    srv = _Srv({"/wt.tar.bz2": tb})
    try:
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td) / "models"
            ok, why = F.fetch_tarball(root, srv.url + "/wt.tar.bz2",
                                      pin=hashlib.sha256(tb).hexdigest(), say=quiet)
            assert ok, why
            assert (root / "sherpa-onnx-nemotron-x" / "tokens.txt").read_bytes() == b"tok"
            left = [p.name for p in root.iterdir()]
            assert left == ["sherpa-onnx-nemotron-x"], f"该只剩模型目录：{left}"
    finally:
        srv.close()


@case("⭐ Nemotron：tar 里有越界路径（../evil）→ 拒收，模型目录之外什么都没写")
def t_tarball_path_traversal():
    tb = _tar_bytes({"../evil.txt": b"x"})
    srv = _Srv({"/wt.tar.bz2": tb})
    try:
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td) / "models"
            ok, why = F.fetch_tarball(root, srv.url + "/wt.tar.bz2",
                                      pin=hashlib.sha256(tb).hexdigest(), say=quiet)
            assert not ok and "越界" in why, (ok, why)
            assert not (pathlib.Path(td) / "evil.txt").exists(), "越界文件被写出去了"
    finally:
        srv.close()


# ---------------------------------------------- 2026-10-04 OCR 审查补的三条
# ⚠️ 这三条都是**「静默重活 / 静默放过」**：没判据的话，出错时没人看得见，
#    而代价是「下错一份 938 MB」或「永远重下不了」。


@case("⭐ `fetch()` 见到不认识的 key 要**响亮失败** —— 不许兜底去下最大的那个")
def t_fetch_unknown_key_raises():
    try:
        F.fetch("bogus", say=quiet)
    except ValueError as e:
        assert "bogus" in str(e), str(e)
    else:
        raise AssertionError("未知 key 没报错 —— 会静默去下一个别的东西")


@case("⭐ 压缩包被截断 → 失败，模型目录里**没有**半截模型、没有临时目录")
def t_tarball_truncated_leaves_nothing():
    tb = _tar_bytes({"sherpa-onnx-nemotron-x/tokens.txt": b"tok" * 5000})
    srv = _Srv({"/wt.tar.bz2": tb[: len(tb) // 2]})
    try:
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td) / "models"
            ok, why = F.fetch_tarball(root, srv.url + "/wt.tar.bz2",
                                      pin=hashlib.sha256(tb).hexdigest(), say=quiet)
            assert not ok, why
            assert list(root.iterdir()) == [], f"不该留下任何东西：{list(root.iterdir())}"
    finally:
        srv.close()


@case("目标位置已有一份半截目录 → 被完整的那份整体替换（不是合并）")
def t_tarball_replaces_stale_dir():
    tb = _tar_bytes({"sherpa-onnx-nemotron-x/tokens.txt": b"tok"})
    srv = _Srv({"/wt.tar.bz2": tb})
    try:
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td) / "models"
            stale = root / "sherpa-onnx-nemotron-x"; stale.mkdir(parents=True)
            (stale / "leftover.bin").write_bytes(b"x")
            ok, why = F.fetch_tarball(root, srv.url + "/wt.tar.bz2",
                                      pin=hashlib.sha256(tb).hexdigest(), say=quiet)
            assert ok, why
            assert sorted(p.name for p in stale.iterdir()) == ["tokens.txt"], "旧的残留应被整体替换"
    finally:
        srv.close()


@case("⭐ 目的地**取自注册表**：`fetch()` 不再手写第二份路径（vad / nemotron 的父目录）")
def t_fetch_destinations_come_from_registry():
    import models
    seen = {}
    orig_dl, orig_w = F.download_file, F.fetch_tarball
    F.download_file = lambda srcs, dest, sha, **kw: (seen.__setitem__("vad", dest), (True, "x"))[1]
    F.fetch_tarball = lambda root, url, **kw: (seen.__setitem__("nemotron", root), (True, "x"))[1]
    try:
        F.fetch("vad", say=quiet); F.fetch("nemotron", say=quiet)
    finally:
        F.download_file, F.fetch_tarball = orig_dl, orig_w
    assert str(seen["vad"]) == models.path_of("vad"), seen
    assert str(seen["nemotron"]) == str(pathlib.Path(models.path_of("nemotron")).parent), seen


@case("⭐ `models.py` 里每个模型的 `cmd` 都是「解释器 fetch_model.py <key>」恰好 3 段（路径含空格也不被切开）")
def t_cmds_point_to_fetch_model():
    import models
    for m in models.MODELS:
        parts = shlex.split(m.cmd)
        assert len(parts) == 3, f"{m.key}: {parts}"
        assert parts[1].endswith("fetch_model.py") and parts[2] == m.key, f"{m.key}: {parts}"
    # 路径含空格：shlex.quote 之后仍是一段
    q = shlex.quote("/Users/a b/lecture-live/fetch_model.py")
    assert shlex.split(f"py {q} vad") == ["py", "/Users/a b/lecture-live/fetch_model.py", "vad"]


@case("钉死的 sha256 都是 64 位小写十六进制；未知参数 → 退出码 2")
def t_pins_shape_and_usage():
    import re
    flat = [F.PINS["vad"], F.PINS["nemotron"]]
    assert all(re.fullmatch(r"[0-9a-f]{64}", h) for h in flat), "有 sha 形状不对"
    assert F.main(["fetch_model.py", "bogus"]) == 2


def main_() -> int:
    fail: list = []
    for name, fn in CASES:
        try:
            fn()
        except AssertionError as e:
            print(f"  ❌ {name}\n      {str(e)[:260]}"); fail.append(name)
        except Exception as e:                                       # noqa: BLE001
            print(f"  ❌ {name}\n      {type(e).__name__}: {str(e)[:200]}"); fail.append(name)
        else:
            print(f"  ✅ {name}")
    print("=" * 60)
    print(f"{len(CASES) - len(fail)}/{len(CASES)} 通过")
    for n in fail:
        print(f"  ❌ {n}")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main_())
