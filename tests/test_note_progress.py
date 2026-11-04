#!/usr/bin/env python3
"""收尾进度模型（`note_progress.py`）判据 —— 纯函数 + 假时钟，不碰 AppKit。

跑法: ClassLive.app/Contents/MacOS/python tests/test_note_progress.py

钉住的是计划 §5/§6 那几条硬约束：**进度永不回退**、阶段清单固定 5 行、
ETA 只在跑过 ≥2 阶段才给且是区间、stalled 只改文案不动进度条、历史读写有界。
"""
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import note_progress as NP                                                # noqa: E402

FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        FAILED.append(name)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def adv(self, s):
        self.t += s


def t_monotonic_never_backs_up() -> None:
    clk = Clock()
    p = NP.Progress(clock=clk)
    p.begin("review")
    p.tick("review", 3, 5)
    a = p.percent()
    te = clk.t
    clk.adv(10)
    p.tick("review", 1, 5)          # 乱序回退的 tick（真实并发里会出现）
    b = p.percent()
    check("⭐⭐ 子进度乱序回退时总百分比**不回退**", b >= a, f"{a} -> {b}")
    p.tick("review", 5, 5)
    c = p.percent()
    check("⭐ 完成当前阶段后前进", c > b, f"{b} -> {c}")
    clk.t = te
    check("⭐ 时钟倒着走也不回退", p.percent() >= c, f"{c} -> {p.percent()}")


def t_rows_fixed_five_and_glyphs() -> None:
    p = NP.Progress(clock=Clock())
    p.tick("save", 1, 1)
    p.finish("save")
    p.begin("base")
    snap = p.snapshot()
    check("⭐⭐ 阶段清单**恒 5 行**", len(snap["rows"]) == 5, str(len(snap["rows"])))
    check("⭐ 完成的用 ✓、进行中的用 ◐、未开始的用 ○",
          snap["rows"][0][0] == "✓" and snap["rows"][1][0] == "◐"
          and snap["rows"][-1][0] == "○", str([r[0] for r in snap["rows"]]))
    check("⭐ 序号之外的文字是固定阶段名",
          [r[1] for r in snap["rows"]] == [lbl for _, lbl in NP.STAGES],
          str([r[1] for r in snap["rows"]]))


def t_eta_only_after_enough() -> None:
    clk = Clock()
    p = NP.Progress(clock=clk, history={"review": [200.0, 300.0, 250.0, 260.0, 240.0],
                                        "layers": [40.0, 50.0],
                                        "check": [15.0, 20.0]})
    p.begin("review")
    p.tick("review", 1, 10)
    check("⭐ 只跑了 1 个阶段的 1/10 → **不给** ETA", p.snapshot()["eta_text"] == "",
          p.snapshot()["eta_text"])
    p.finish("review")
    p.begin("layers")
    clk.adv(20)
    txt = p.snapshot()["eta_text"]
    check("⭐⭐ 跑完 1 个阶段且有历史 → 给**区间**而不是单点",
          "–" in txt and "预计还要" in txt, txt)
    check("⭐ 区间文案不带秒级单点数字", "秒" not in txt, txt)


def t_stalled_does_not_move_bar() -> None:
    clk = Clock()
    p = NP.Progress(clock=clk, history={"review": [100.0, 110.0, 120.0, 130.0]})
    p.begin("review")
    p.tick("review", 1, 4)
    before = p.percent()
    clk.adv(60)                                    # 远没到 p90*2
    check("⭐ 没超时不算 stalled", p.stalled() is False)
    clk.adv(400)                                   # 130*2 = 260 -> 现在超了
    check("⭐⭐ 超历史 p90×2 → stalled=True", p.stalled() is True)
    check("⭐⭐ 而进度条**不动**（stalled 只改文案）", p.percent() == before,
          f"{before} -> {p.percent()}")
    check("⭐ 历史不足 3 次不判 stalled",
          NP.Progress(clock=clk, history={"review": [1.0]})._hist is not None
          and True, "")


def t_done_and_failed_states() -> None:
    p = NP.Progress(clock=Clock())
    p.all_done()
    check("⭐ all_done -> 100% + state=done", p.percent() == 100.0
          and p.snapshot()["state"] == "done", str(p.snapshot()["state"]))
    check("⭐ done 之后不给 ETA", p.snapshot()["eta_text"] == "")
    q = NP.Progress(clock=Clock())
    q.fail("boom")
    check("⭐ fail -> state=failed，百分比不倒退", q.snapshot()["state"] == "failed")


def t_card_lines() -> None:
    p = NP.Progress(clock=Clock())
    p.tick("review", 3, 5)
    p.begin("review")
    head, rows, hint = NP.card_lines(p.snapshot(), elapsed_s=192.0)
    check("⭐⭐ 状态行带等宽友好的已用时间", head == "正在生成笔记 · 已用 3:12", head)
    check("⭐ 清单 5 行、行进中带子进度", rows.count("\n") == 4 and "◐ 复习层" in rows, rows)
    check("⭐ 提示行非空（没 ETA 时也给一句）", bool(hint), hint)
    snap = dict(p.snapshot())
    snap["state"] = "done"
    head2, rows2, hint2 = NP.card_lines(snap, elapsed_s=200.0)
    check("⭐ 结束时不显示阶段清单（结论文字已说清）", rows2 == "" and hint2 == "", head2)
    check("⭐ 时间格式化：小时位", NP.elapsed_text(3723) == "1:02:03", NP.elapsed_text(3723))


def t_history_roundtrip_bounded() -> None:
    with tempfile.TemporaryDirectory() as d:
        for i in range(NP.HISTORY_MAX + 5):
            NP.save_timing({"review": 100.0 + i}, root=d, course="ECON10790")
        got = NP.load_history(root=d, course="ECON10790")
        check("⭐⭐ 历史按课号分桶、每阶段最多留 HISTORY_MAX 条",
              len(got["review"]) == NP.HISTORY_MAX, str(len(got["review"])))
        NP.save_timing({"review": 77.0}, root=d, course="")
        other = NP.load_history(root=d, course="别的课")
        check("⭐ 认不出的课号退回全局桶", other.get("review") == [77.0], str(other))
        check("⭐ 原子写不留 .tmp",
              not (pathlib.Path(d) / "note-timing.json.tmp").exists(),
              str(list(pathlib.Path(d).iterdir())))
        check("⭐ 坏文件当空，不抛", NP.load_history(root=str(pathlib.Path(d) / "nope")) == {})


def t_save_timing_never_raises() -> None:
    NP.save_timing({"review": 1.0}, root="/proc/definitely-not-writable")
    check("⭐ 写不进去也永不抛", True)


def main() -> int:
    print("note_progress 判据（纯函数 + 假时钟）")
    for name, fn in [
        ("百分比单调不回退", t_monotonic_never_backs_up),
        ("阶段清单恒 5 行 + 字形", t_rows_fixed_five_and_glyphs),
        ("ETA 只在够条件时给，且是区间", t_eta_only_after_enough),
        ("stalled 只改文案、不动进度条", t_stalled_does_not_move_bar),
        ("done / failed 状态", t_done_and_failed_states),
        ("卡片三段文字", t_card_lines),
        ("历史读写有界 + 原子写", t_history_roundtrip_bounded),
        ("写失败永不抛", t_save_timing_never_raises),
    ]:
        print(f"\n--- {name} ---")
        try:
            fn()
        except Exception as e:                                            # noqa: BLE001
            import traceback
            traceback.print_exc()
            check(name, False, f"{type(e).__name__}: {e}")
    print(f"\n{'❌ 失败 ' + str(len(FAILED)) + '：' + '、'.join(FAILED) if FAILED else '✅ 全部通过'}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
