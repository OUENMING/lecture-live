#!/usr/bin/env python3
"""`app_icon.refresh` 的判据：戳记对上不动、对不上就换图标写戳记、生成器失败不抛不换。

    ClassLive.app/Contents/MacOS/python tests/test_app_icon.py

全在临时目录里：假 .app、假生成器（不跑真 make_icon.py，不碰真 ClassLive.app）。
"""
from __future__ import annotations

import pathlib
import sys
import tempfile
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import app_icon  # noqa: E402

FAILS: list[str] = []


def check(name: str, ok: bool, extra: str = "") -> None:
    print(f"  {'✅' if ok else '❌'} {name}  {extra}")
    if not ok:
        FAILS.append(name)


def setup(d: pathlib.Path):
    app = d / "X.app"
    res = app / "Contents" / "Resources"
    res.mkdir(parents=True)
    (res / "AppIcon.icns").write_bytes(b"OLD")
    gen = d / "make_icon.py"
    gen.write_text("# v2\n")
    built = d / "out.icns"
    return app, res, gen, built


def fake_run(built: pathlib.Path, rc: int = 0, payload: bytes = b"NEW"):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        if rc == 0:
            built.write_bytes(payload)
        return types.SimpleNamespace(returncode=rc, stdout="", stderr="boom" if rc else "")
    return run, calls


def main() -> int:
    with tempfile.TemporaryDirectory() as t:
        app, res, gen, built = setup(pathlib.Path(t))
        run, calls = fake_run(built)
        msg = app_icon.refresh(app, gen=gen, built=built, run=run)
        check("戳记缺 → 重画并换上", (res / "AppIcon.icns").read_bytes() == b"NEW" and len(calls) == 1, msg)
        check("写了戳记 = 生成器 sha", (res / app_icon.STAMP).read_text().strip() == app_icon.generator_hash(gen))
        msg2 = app_icon.refresh(app, gen=gen, built=built, run=run)
        check("戳记对上 → 不再跑生成器", len(calls) == 1 and msg2 == "", repr(msg2))
        gen.write_text("# v3\n")
        app_icon.refresh(app, gen=gen, built=built, run=run)
        check("生成器变了 → 再换一次", len(calls) == 2)

    with tempfile.TemporaryDirectory() as t:
        app, res, gen, built = setup(pathlib.Path(t))
        run, _ = fake_run(built, rc=1)
        msg = app_icon.refresh(app, gen=gen, built=built, run=run)
        check("生成器失败 → 旧图标不动、不写戳记、不抛",
              (res / "AppIcon.icns").read_bytes() == b"OLD" and not (res / app_icon.STAMP).exists()
              and msg.startswith("图标没换成"), msg)

    with tempfile.TemporaryDirectory() as t:
        d = pathlib.Path(t)
        msg = app_icon.refresh(d / "None.app", gen=d / "nope.py", built=d / "x", run=None)
        check("没构建过 .app → 空串、不碰", msg == "", repr(msg))

        def explode(*a, **k):
            raise OSError("disk")
        app, res, gen, built = setup(d)
        msg = app_icon.refresh(app, gen=gen, built=built, run=explode)
        check("意外异常 → 不抛、给一句话", msg.startswith("图标没换成"), msg)

    # 接线：三条更新路（面板立即更新 / 开课前自动装 / 下课退出自动装）都走 `update.finish`
    import inspect
    import update
    src = inspect.getsource(update.finish)
    check("⭐⭐ update.finish 调 _refresh_icon（不重建 .app 也换图标）", "_refresh_icon(" in src)
    src2 = inspect.getsource(update._refresh_icon)
    check("⭐ 路径从 update.HERE 算（测试沙盒里不碰真 .app）",
          'HERE / "ClassLive.app"' in src2 and 'HERE / "tools" / "make_icon.py"' in src2)

    print(f"\n{'OK' if not FAILS else '❌ ' + ', '.join(FAILS)}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
