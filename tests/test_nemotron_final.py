#!/usr/bin/env python3
"""`nemotron_final.py` 的判据 —— 流式模型拿**整段音频重新解码**（云端失败时的本地定稿）。

    ClassLive.app/Contents/MacOS/python tests/test_nemotron_final.py

## 钉住了什么

 1. 构造**不加载**模型（只核对文件）；第一次 `transcribe_lp` 才建 —— 云端定稿从不失败的人
    不该为它花那 630 MB 内存。
 2. **喂整段 + `TAIL_PAD_S` 静音 + `input_finished`**：少补那截静音会吞掉段尾的词
    （`docs/PLAN-streaming-asr.md` §0.1：WER 16.2% → 10.3%）。
 3. 每段**新开一条 stream**（不许复用：`input_finished` 之后那条流是死的）。
 4. 同实例被两个线程并发调用**不会串**（锁）；不同实例各用各的 recognizer（不共享）。
 5. 不足 0.1 s 给 `("", None)`；`logprob` 恒为 `None`（流式结果的 logprob 没有可信的平均值）。
 6. 文件不齐 → **构造时就报**（别拖到课上第一次兜底）。

⚠️ 全用假识别器，**不加载真模型、不碰联网**。
"""
from __future__ import annotations

import pathlib
import sys
import tempfile
import threading
import time

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import numpy as np                                                    # noqa: E402

import nemotron_final as NF                                           # noqa: E402

CASES: list[tuple[str, object]] = []
FAIL: list[str] = []


def case(name: str):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


FILES = ("tokens.txt", "encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx")


def fake_dir(root: pathlib.Path) -> pathlib.Path:
    d = root / "nemotron"
    d.mkdir(parents=True, exist_ok=True)
    for n in FILES:
        (d / n).write_bytes(b"x")
    return d


class FakeStream:
    def __init__(self, rec):
        self.rec = rec
        self.chunks: list = []
        self.finished = False
        self.ready = 0

    def accept_waveform(self, sr, w):
        assert not self.finished, "input_finished 之后又喂了样本 —— 这条流已经死了"
        self.chunks.append((sr, int(len(w))))
        self.ready += 1

    def input_finished(self):
        self.finished = True


class FakeRec:
    """假 `OnlineRecognizer`：记录「几条流 / 喂了多少样本 / 有没有收尾」。"""

    def __init__(self, text="hello there"):
        self.text = text
        self.streams: list[FakeStream] = []

    def create_stream(self):
        s = FakeStream(self)
        self.streams.append(s)
        return s

    def is_ready(self, s):
        return s.finished and s.ready > 0

    def decode_stream(self, s):
        s.ready -= 1

    def get_result(self, s):
        return self.text


@case("⭐⭐ 构造**不加载**模型 → 第一次 transcribe_lp 才建（云端从不失败的人不该付这笔内存）")
def t_lazy_load():
    made: list = []
    with tempfile.TemporaryDirectory() as td:
        d = fake_dir(pathlib.Path(td))

        def loader(p):
            made.append(p)
            return FakeRec()

        nf = NF.NemotronFinal(d, loader=loader)
        assert made == [] and nf.loaded is False, "构造时就加载了模型（那会在开课时多等 1.3 s）"
        nf.transcribe_lp(np.zeros(16000, dtype="float32"))
        assert [str(x) for x in made] == [str(d)] and nf.loaded is True, \
            f"{made} loaded={nf.loaded}"
        nf.transcribe_lp(np.zeros(16000, dtype="float32"))
        assert len(made) == 1, "第二次调用又建了一个 recognizer"


@case("⭐⭐ 喂整段 + 补 TAIL_PAD_S 静音 + input_finished（不补尾会吞掉段尾的词）")
def t_tail_pad_and_finish():
    with tempfile.TemporaryDirectory() as td:
        d = fake_dir(pathlib.Path(td))
        rec = FakeRec("tail kept")
        nf = NF.NemotronFinal(d, rec=rec, tail_pad_s=0.8)
        text, lp = nf.transcribe_lp(np.zeros(16000, dtype="float32"))   # 1.0 s
        assert text == "tail kept" and lp is None, (text, lp)
        s = rec.streams[0]
        assert s.finished, "没有 input_finished —— 最后那截前瞻里的话永远解不出来"
        assert len(s.chunks) == 2, f"该是「整段 + 一截静音」两次喂：{s.chunks}"
        assert s.chunks[0][1] == 16000, s.chunks
        assert s.chunks[1][1] == int(0.8 * 16000), f"补的静音长度不对：{s.chunks[1]}"
        assert all(sr == NF.SR for sr, _n in s.chunks), "采样率不是 16 kHz"


@case("⭐⭐ 每段**新开**一条流（上一条 input_finished 之后已经死了）")
def t_new_stream_per_segment():
    with tempfile.TemporaryDirectory() as td:
        d = fake_dir(pathlib.Path(td))
        rec = FakeRec()
        nf = NF.NemotronFinal(d, rec=rec)
        for _ in range(3):
            nf.transcribe_lp(np.zeros(16000, dtype="float32"))
        assert len(rec.streams) == 3, f"三段该三条流：{len(rec.streams)}"


@case("⭐ 不足 0.1 s → 空串，且**不碰**模型（Parakeet 当年同一条规矩）")
def t_too_short():
    with tempfile.TemporaryDirectory() as td:
        d = fake_dir(pathlib.Path(td))
        rec = FakeRec()
        nf = NF.NemotronFinal(d, rec=rec)
        assert nf.transcribe_lp(np.zeros(100, dtype="float32")) == ("", None)
        assert rec.streams == [], "太短的段也去建流了"


@case("⭐⭐ 两个线程并发调**同一个实例**：不串（锁内整段跑完）")
def t_thread_safe_same_instance():
    with tempfile.TemporaryDirectory() as td:
        d = fake_dir(pathlib.Path(td))
        rec = FakeRec("ok")
        nf = NF.NemotronFinal(d, rec=rec)
        bad: list = []

        def worker():
            for _ in range(20):
                t, _lp = nf.transcribe_lp(np.zeros(1600, dtype="float32"))
                if t != "ok":
                    bad.append(t)

        ths = [threading.Thread(target=worker) for _ in range(2)]
        [t.start() for t in ths]
        [t.join() for t in ths]
        assert bad == [], f"并发下串了：{bad[:5]}"
        assert len(rec.streams) == 40, f"该 40 条流（每次新开）：{len(rec.streams)}"


@case("⭐⭐ 两个**不同实例**各用各的 recognizer（草稿 / 定稿不许共用一条）")
def t_two_instances_are_independent():
    with tempfile.TemporaryDirectory() as td:
        d = fake_dir(pathlib.Path(td))
        a, b = FakeRec("a"), FakeRec("b")
        na, nb = NF.NemotronFinal(d, rec=a), NF.NemotronFinal(d, rec=b)
        assert na.transcribe_lp(np.zeros(1600, dtype="float32"))[0] == "a"
        assert nb.transcribe_lp(np.zeros(1600, dtype="float32"))[0] == "b"
        assert len(a.streams) == 1 and len(b.streams) == 1, "两个实例共用了流"


@case("⭐ 文件不齐 → **构造时**就报（别拖到课上第一次兜底才炸）")
def t_missing_file_reports_early():
    with tempfile.TemporaryDirectory() as td:
        d = pathlib.Path(td) / "nemotron"
        d.mkdir(parents=True)
        (d / "tokens.txt").write_bytes(b"x")           # 缺三个 onnx
        try:
            NF.NemotronFinal(d, rec=FakeRec())
        except FileNotFoundError as e:
            assert "encoder" in str(e) or "decoder" in str(e) or "joiner" in str(e), str(e)
        else:
            raise AssertionError("缺文件却没在构造时报错")


@case("⭐ 加载器抛错时**原样抛出去**（静默吞掉没人知道模型坏了）")
def t_loader_error_propagates():
    with tempfile.TemporaryDirectory() as td:
        d = fake_dir(pathlib.Path(td))

        def boom(_p):
            raise RuntimeError("坏模型")

        nf = NF.NemotronFinal(d, loader=boom)
        try:
            nf.transcribe_lp(np.zeros(1600, dtype="float32"))
        except RuntimeError as e:
            assert "坏模型" in str(e)
        else:
            raise AssertionError("加载失败被吞了")


@case("⭐ 假识别器的内存代价：一个 recognizer 就够（不重复建）")
def t_one_recognizer_only():
    with tempfile.TemporaryDirectory() as td:
        d = fake_dir(pathlib.Path(td))
        made: list = []

        def loader(_p):
            time.sleep(0.01)                  # 模拟真加载
            made.append(1)
            return FakeRec()

        nf = NF.NemotronFinal(d, loader=loader)
        ths = [threading.Thread(target=lambda: nf.transcribe_lp(
            np.zeros(1600, dtype="float32"))) for _ in range(4)]
        [t.start() for t in ths]
        [t.join() for t in ths]
        assert len(made) == 1, f"4 个线程建了 {len(made)} 个 recognizer（每个 630 MB）"


def main_() -> int:
    print(f"\n{len(CASES)} 条判据\n" + "=" * 60)
    for name, fn in CASES:
        try:
            fn()
            print(f"  ✅ {name}")
        except Exception as e:                                        # noqa: BLE001
            FAIL.append(name)
            print(f"  ❌ {name}\n      {type(e).__name__}: {e}")
    print("=" * 60)
    print(f"{len(CASES) - len(FAIL)}/{len(CASES)} 通过")
    for n in FAIL:
        print(f"  ❌ {n}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main_())
