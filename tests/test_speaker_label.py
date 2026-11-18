#!/usr/bin/env python3
"""`speaker_label.py` 的判据：金标准用例（JSON，网页版 TS 要跑同一份）+ 性质测试。

    ClassLive.app/Contents/MacOS/python tests/test_speaker_label.py

## 这个文件钉住了什么

1. ⭐ 金标准：`tests/fixtures/speaker_label_cases.json` 逐句的返回值。那份文件是**跨实现契约**
   （以后网页版 TS 跑同一份），所以只能放 JSON 可表达的东西，不能放正则/回调。
2. 长度单位：汉字个数 + 非汉字按空白切词；够长 = 英文 ≥3 词或汉字 ≥4 个。
3. 性质：字母发出不回收 / 主讲那句一定空 / 返回值只可能是 "" 或已发字母 / 字母连续不跳号。

⚠️ 仓库里没有 hypothesis（`requirements.txt` 不含，打包的 python 也 import 不到），所以性质测试用
   固定种子的随机步骤序列自己生成 —— 覆盖的是同一批不变量，只是不自动收缩反例。
⚠️ 坏输入不抛异常是接口承诺（`main.py` 的字段来自 WebSocket，缺键是常事）。
"""
from __future__ import annotations

import json
import pathlib
import random
import sys

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import speaker_label as SL                                        # noqa: E402

CASES = pathlib.Path(__file__).resolve().parent / "fixtures" / "speaker_label_cases.json"


def load_cases() -> list[dict]:
    return json.loads(CASES.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 金标准
def test_fixture_cases() -> None:
    cases = load_cases()
    assert len(cases) >= 12, f"用例太少：{len(cases)}"
    names = set()
    for case in cases:
        name = case["name"]
        assert name not in names, f"用例重名：{name}"
        names.add(name)
        lab = SL.SpeakerLabeler()
        for i, step in enumerate(case["steps"]):
            got = lab.observe(step["speaker"], step["text"])
            assert got == step["expect"], (
                f"{name} 第 {i + 1} 步 {step['speaker']!r} {step['text']!r}: "
                f"期望 {step['expect']!r}，实得 {got!r}")
        if "expect_main" in case:
            assert lab.main == case["expect_main"], (
                f"{name} 主讲 {lab.main!r} != {case['expect_main']!r}")
        if "expect_labels" in case:
            assert lab.labels == case["expect_labels"], (
                f"{name} 字母表 {lab.labels!r} != {case['expect_labels']!r}")


# ---------------------------------------------------------------- 长度单位
def test_count_units() -> None:
    assert SL.count_units("") == 0
    assert SL.count_units("   ") == 0
    assert SL.count_units("Okay.") == 1
    assert SL.count_units("yes I do") == 3
    assert SL.count_units("听不懂") == 3
    # 汉字逐个 + 非汉字按空白切词
    assert SL.count_units("今天讲 marginal revenue") == 3 + 2
    assert SL.count_units("价格 elasticity") == 2 + 1
    # 坏输入当空串
    assert SL.count_units(None) == 0                                   # type: ignore[arg-type]
    assert SL.count_units(42) == 0                                     # type: ignore[arg-type]


def test_long_enough() -> None:
    assert SL.MIN_WORDS == 3 and SL.MIN_HAN == 4
    assert SL.long_enough("is it true") is True
    assert SL.long_enough("is it") is False
    assert SL.long_enough("听懂") is False                              # 2 汉字
    assert SL.long_enough("听不懂") is False                            # 3 汉字
    assert SL.long_enough("听不懂啊") is True                           # 4 汉字，刚好
    assert SL.long_enough("") is False
    assert SL.long_enough(None) is False                               # type: ignore[arg-type]
    # 汉字够长时英文短句不影响结论；反之亦然
    assert SL.long_enough("好 的 我 们 开始") is True


# ---------------------------------------------------------------- 性质
SPEAKERS = ["1", "2", "3", "4", ""]
TEXTS = ["", ".", "Okay.", "yes", "is it true", "is it", "听不懂", "我听不懂",
         "今天讲 marginal revenue", "So here is the whole idea in one sentence."]


def _steps(rng: random.Random, n: int) -> list[tuple[str, str]]:
    out = []
    for _ in range(n):
        sp = rng.choice(SPEAKERS)
        r = rng.random()
        if r < 0.15:
            text = None                                     # 坏输入也要活
        else:
            text = rng.choice(TEXTS)
        out.append((sp, text))
    return out


def test_properties_over_random_sequences() -> None:
    for seed in range(200):
        rng = random.Random(seed)
        lab = SL.SpeakerLabeler()
        seen: dict[str, str] = {}
        for speaker, text in _steps(rng, rng.randint(1, 40)):
            got = lab.observe(speaker, text)
            labels = lab.labels

            # (a) 字母一旦发出，返回值与记录都不许变
            for sp, letter in seen.items():
                assert labels.get(sp) == letter, f"seed={seed} {sp} 的字母被改：{letter}→{labels.get(sp)}"
            assert len(labels) >= len(seen)

            # (b) 不抛异常，返回值只可能是 "" 或已发字母
            assert isinstance(got, str)
            assert got == "" or got in labels.values(), f"seed={seed} 返回 {got!r} 不在 {labels}"

            # (c) 当前主讲那句一定空
            if speaker and lab.main == speaker:
                assert got == "", f"seed={seed} 主讲 {speaker} 被标了 {got!r}"

            # (d) 字母连续不跳号：B、C、D… 的前缀
            issued = list(labels.values())
            assert len(set(issued)) == len(issued)
            assert set(issued) <= set(SL.LETTERS)
            assert all(letter in issued for letter in SL.LETTERS[:len(issued)])
            if len(issued) < len(SL.LETTERS):
                assert SL.LETTERS[len(issued)] not in issued

            # (e) 主讲要么没出现，要么真的累计最多
            if lab.main:
                assert lab.units(lab.main) >= max(lab.units(s) for s in labels.keys() | {lab.main})

            seen = dict(labels)


def test_properties_with_known_letters_only() -> None:
    """主讲每句重算：谁被标过字母，就不该是「当前主讲」，除非他后来被追过。"""
    for seed in range(50):
        rng = random.Random(1000 + seed)
        lab = SL.SpeakerLabeler()
        for speaker, text in _steps(rng, 30):
            got = lab.observe(speaker, text)
            if got:
                assert speaker != lab.main
                assert speaker in lab.labels


def test_labels_copy_is_detached() -> None:
    lab = SL.SpeakerLabeler()
    lab.observe("1", "Welcome to the first lecture.")
    lab.observe("2", "Is it true")
    copy = lab.labels
    assert copy == {"2": "B"}
    copy["2"] = "Z"
    assert lab.labels == {"2": "B"}
    assert lab.main == "1"
    assert lab.units("2") == 3
    assert lab.units("nobody") == 0


def test_does_not_depend_on_repo_modules() -> None:
    """纯逻辑：不许 import 仓库里别的业务模块（网页版要原样搬走）。"""
    src = (HERE / "speaker_label.py").read_text(encoding="utf-8")
    imports = [ln.strip() for ln in src.splitlines()
               if ln.strip().startswith(("import ", "from "))]
    assert imports == ["from __future__ import annotations", "import re"], imports


TESTS = [test_fixture_cases, test_count_units, test_long_enough,
         test_properties_over_random_sequences, test_properties_with_known_letters_only,
         test_labels_copy_is_detached, test_does_not_depend_on_repo_modules]


def main() -> int:
    bad = 0
    for fn in TESTS:
        try:
            fn()
        except AssertionError as e:
            bad += 1
            print(f"❌ {fn.__name__}: {e}")
        except Exception as e:                                     # noqa: BLE001
            bad += 1
            print(f"💥 {fn.__name__}: {e!r}")
        else:
            print(f"✅ {fn.__name__}")
    print(f"\n{len(TESTS) - bad}/{len(TESTS)} 通过")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
