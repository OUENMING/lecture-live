#!/usr/bin/env python3
"""3.10.1 诊断包的判据 —— 测试模式收尾把日志 / 依赖 / 旁路文件一起传上来。

    ClassLive.app/Contents/MacOS/python tests/test_diag_upload.py

## 这个文件钉住了什么

1. ⭐⭐ `redact_secrets`：通行码 / key / Bearer / `"token": "…"` 形状都抹掉，普通文字不动
2. ⭐⭐ `make_extras`：收本节课旁路文件（去前缀）+ 笔记 + `diag.json` + `env.json`；
   **不重复带**已单独上传的报告 / 转录 / 音频；文本过脱敏；别的课的文件不进来
3. ⭐ `_remote_name`：`<stem>.extra.tar.gz` → `extra.tar.gz`（Worker 白名单认的名字）
4. ⭐⭐ `pump` 与 `add_files` 的竞态：一轮发送途中并进来的诊断包**不会被整条移走而丢掉**
5. ⭐ `update.deps_diag`：requirements 里装没装上的包分得清；`uv` / 指纹戳字段在

⚠️ 语料全是 `tempfile` 合成的，不碰 `sessions/` / `~/.classlive/`、不联网。
"""
from __future__ import annotations

import json
import pathlib
import shutil
import sys
import tarfile
import tempfile
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import testmode                                                       # noqa: E402
import update                                                         # noqa: E402
import upload                                                         # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  —— {detail}" if detail and not ok else ""))


def main() -> int:
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="cl-diag-"))
    try:
        # ---- 1. 脱敏 ----
        raw = ("passcode cl-AbCdEf123456 ok\nAuthorization: Bearer sk.zzzzzzzzzz\n"
               '{"api_key": "snx_9f8e7d6c5b4a3"} gsk_ABCDEFGH1234 hf_abcdefghij\n'
               "普通一句话，cl- 开头但太短 cl-ab")
        red = testmode.redact_secrets(raw)
        # ⚠️ 变异验证：删掉 `_SECRET_RES` 任何一条 -> 对应的串留下、这条红。
        check("⭐⭐ 凭据形状全被抹掉",
              not any(x in red for x in ("AbCdEf123456", "sk.zzzzzzzzzz", "9f8e7d6c5b4a3",
                                         "ABCDEFGH1234", "abcdefghij")), red)
        check("普通文字与过短的 cl- 原样留着",
              "普通一句话" in red and "cl-ab" in red and "passcode cl-" in red, red)

        # ---- 2. make_extras ----
        sd = tmp / "sessions"
        sd.mkdir()
        stem = sd / "2026-10-19_170000_X"
        for tail, body in ((".live.jsonl", '{"event": "meta"}\n{"k": "cl-SECRET0123456"}\n'),
                           (".atoms.jsonl", '{"id": 0}\n'),
                           (".report.json", "{}"), (".md", "# 转录"),
                           (".001.opus", "OggS"), (".bundle.zip", "PK")):
            pathlib.Path(str(stem) + tail).write_text(body, encoding="utf-8")
        (sd / "2026-10-19_090000_Y.live.jsonl").write_text("{}", encoding="utf-8")   # 别的课
        note = tmp / "note.md"
        note.write_text("# 笔记", encoding="utf-8")
        p = testmode.make_extras(stem, note_path=str(note), diag=lambda: {"deps": {"missing": ["x"]}})
        names, live, diag = [], "", {}
        if p:
            with tarfile.open(p, "r:gz") as tf:
                names = sorted(tf.getnames())
                live = tf.extractfile("live.jsonl").read().decode()
                diag = json.loads(tf.extractfile("diag.json").read())
        check("⭐⭐ 包内：旁路文件（去前缀）+ 笔记 + diag + env",
              names == ["atoms.jsonl", "diag.json", "env.json", "live.jsonl", "note.md"], str(names))
        # ⚠️ 变异验证：把 `_EXTRA_SKIP_TAILS` 里的 `.report.json` 删掉 -> 包里多出 report.json、上一条红。
        check("包里的文本过了脱敏", "SECRET0123456" not in live and "<redacted>" in live, live)
        check("diag.json 是注入的那份 + skipped 记账",
              diag.get("deps") == {"missing": ["x"]} and diag.get("skipped") == [], str(diag))
        check("没有残留 .tmp", not list(sd.glob("*.tmp")), str(list(sd.glob("*"))))
        p2 = testmode.make_extras(stem, diag=lambda: 1 / 0)
        with tarfile.open(p2, "r:gz") as tf:
            d2 = json.loads(tf.extractfile("diag.json").read())
        check("diag 采集器炸了也照打包（记 error）", "ZeroDivisionError" in str(d2.get("error")), str(d2))

        # ---- 2b. 报告里混进不能序列化的一格，整份照写 ----
        import numpy as np
        rp = tmp / "r.report.json"
        okw = testmode._atomic_json(rp, {"a": 1, "fn": (lambda: 0), "n": np.float32(0.5),
                                         "p": pathlib.Path("/x/y")})
        got = json.loads(rp.read_text(encoding="utf-8")) if rp.exists() else {}
        # ⚠️ 变异验证：去掉 `default=_json_fallback` -> 写不出来、这条红（探针 10-19 的事故形态）。
        check("⭐⭐ 报告里一格是函数 / numpy / Path：整份照写",
              okw and got.get("a") == 1 and got.get("fn") == "<function>"
              and got.get("n") == 0.5 and got.get("p") == "/x/y", str(got))

        # ---- 3. 远端名 ----
        check("⭐ <stem>.extra.tar.gz → extra.tar.gz",
              upload._remote_name(pathlib.Path(str(stem) + ".extra.tar.gz")) == "extra.tar.gz")

        # ---- 4. pump × add_files 竞态 ----
        root = tmp / "q"
        sent: list = []
        extra = pathlib.Path(p)
        rep = pathlib.Path(str(stem) + ".report.json")
        q = None

        def send(local, key):
            sent.append(key)
            if len(sent) == 1:                   # 发第一个文件的途中，收尾那边并进诊断包
                q.add_files([extra], "S")
            return True

        q = upload.UploadQueue(send, root=root)
        q.enqueue([rep], "S")
        q.pump()
        # ⚠️ 变异验证：把 `pump` 里 `and not rest` 去掉 -> 条目被整条移走、诊断包永远不发、这两条红。
        check("⭐⭐ 途中并进的诊断包：条目留着没被移走", q.pending == 1, f"pending={q.pending}")
        q.pump()
        check("⭐⭐ 下一轮把诊断包发出去、条目清空",
              any(k.endswith("/extra.tar.gz") for k in sent) and q.pending == 0,
              f"sent={sent} pending={q.pending}")

        # ---- 5. deps_diag ----
        fake = tmp / "repo"
        fake.mkdir()
        (fake / "requirements.txt").write_text(
            "# 注释\nnumpy>=2.0   # 行尾注释\nclasslive-definitely-not-a-pkg>=1\n"
            "mlx-lm>=0.31; platform_release >= '23'\nmlx-lm>=0.29,<0.30; platform_release < '23'\n",
            encoding="utf-8")
        with mock.patch.object(update, "HERE", fake), \
                mock.patch.object(update, "STATE_DIR", tmp / "state"):
            d = update.deps_diag()
        check("⭐ deps_diag：没装的列进 missing、装了的不列",
              d.get("missing") == ["classlive-definitely-not-a-pkg"] or
              d.get("missing") == ["classlive-definitely-not-a-pkg", "mlx-lm"], str(d))
        check("deps_diag：包名去重（mlx-lm 两行算一个）",
              list(d.get("installed", {})).count("mlx-lm") == 1, str(d.get("installed")))
        check("deps_diag：uv / pip / stamp_ok 三格都在",
              all(k in d for k in ("uv", "pip", "stamp_ok")) and d["stamp_ok"] is False, str(d))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    bad = [n for n, ok, _ in RESULTS if not ok]
    print("\n" + "=" * 60)
    print(f"{len(RESULTS) - len(bad)}/{len(RESULTS)} 通过")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
