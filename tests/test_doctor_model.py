#!/usr/bin/env python3
"""`doctor.model_present()` 对目录型模型的判据。

     ClassLive.app/Contents/MacOS/python tests/test_doctor_model.py

## 钉住什么

1. ⭐ 注册表里的目录型模型**必须那几个文件都在**才算就位 —— 只看「目录里有非空文件」
   会在缺 `encoder.int8.onnx` 时假绿（2026-11-10：起课抛 FileNotFoundError，doctor 仍 ✅）。
2. 不认识的自定义目录仍走原来的宽松判据（任意非空文件 = 在），不误伤 whisper 等。
"""
from __future__ import annotations

import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import doctor                                                        # noqa: E402
import models                                                        # noqa: E402

CASES: list[tuple[str, object]] = []


def case(name: str):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


@case("目录型模型缺 encoder → 不算就位（11-10 假绿）")
def t_requires_declared_files():
    with tempfile.TemporaryDirectory() as td:
        d = pathlib.Path(td) / "model"
        d.mkdir()
        (d / "tokens.txt").write_text("x", encoding="utf-8")
        (d / "decoder.int8.onnx").write_text("x", encoding="utf-8")

        fake = models.Model("t_fake", str(d), "测试模型", True, "true", 1.0)
        old_models = models.MODELS
        old_req = dict(models.REQUIRED_FILES)
        models.MODELS = (fake,)
        models.REQUIRED_FILES["t_fake"] = ("encoder.int8.onnx", "tokens.txt")
        try:
            assert not doctor.model_present(str(d)), "缺 encoder 仍报就位"
            (d / "encoder.int8.onnx").write_text("x", encoding="utf-8")
            assert doctor.model_present(str(d))
            (d / "encoder.int8.onnx").write_text("", encoding="utf-8")
            assert not doctor.model_present(str(d)), "空文件也算就位"
        finally:
            models.MODELS = old_models
            models.REQUIRED_FILES.clear()
            models.REQUIRED_FILES.update(old_req)


@case("不认识的目录仍走宽松判据（不误伤）")
def t_unknown_dir_stays_lenient():
    with tempfile.TemporaryDirectory() as td:
        d = pathlib.Path(td) / "whatever"
        d.mkdir()
        (d / "a.bin").write_text("x", encoding="utf-8")
        assert doctor.model_present(str(d))
        (d / "a.bin").write_text("", encoding="utf-8")
        assert not doctor.model_present(str(d))


@case("注册表里 parakeet / nemotron 都声明了四个文件")
def t_registry_declared():
    for m in (models.PARAKEET, models.NEMOTRON):
        req = models.required_files_for(m.path)
        assert req == ("encoder.int8.onnx", "decoder.int8.onnx",
                       "joiner.int8.onnx", "tokens.txt"), (m.key, req)


def main() -> int:
    fail: list = []
    for name, fn in CASES:
        try:
            fn()
        except AssertionError as e:
            print(f"  ❌ {name}\n      {str(e)[:260]}")
            fail.append(name)
        except Exception as e:                                       # noqa: BLE001
            print(f"  ❌ {name}\n      {type(e).__name__}: {str(e)[:200]}")
            fail.append(name)
        else:
            print(f"  ✅ {name}")
    print("=" * 60)
    print(f"{len(CASES) - len(fail)}/{len(CASES)} 通过")
    for n in fail:
        print(f"  ❌ {n}")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
