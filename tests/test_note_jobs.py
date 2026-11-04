#!/usr/bin/env python3
"""后台任务所有权记录（`note_jobs.py`）判据 —— 纯标准库 + 注入时钟，不碰 AppKit / 网络。

跑法: ClassLive.app/Contents/MacOS/python tests/test_note_jobs.py

钉住的是 §12 那三条阻断项：**活着的 worker 不许被当成残局**、**同一节不许两个写者**、
**判活不能靠固定总时长**（心跳 + pid 两条一起看）。
"""
import json
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import note_jobs as NJ                                                   # noqa: E402

FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        FAILED.append(name)


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t

    def adv(self, s):
        self.t += s


def t_roundtrip_atomic() -> None:
    with tempfile.TemporaryDirectory() as d:
        clk = Clock()
        ok = NJ.write("2026-11-03_ECON10790", {"course": "ECON10790"}, root=d, now=clk)
        check("写成功", ok is True)
        j = NJ.read("2026-11-03_ECON10790", root=d)
        check("⭐ 读回来是刚写的字段", j.get("course") == "ECON10790", str(j))
        check("⭐ 自动带上 stem / updated", j.get("stem") == "2026-11-03_ECON10790"
              and j.get("updated") == clk.t, str(j))
        check("⭐ 合并写：第二次不抹掉第一次的键",
              NJ.write("2026-11-03_ECON10790", {"stage": "review"}, root=d, now=clk)
              and NJ.read("2026-11-03_ECON10790", root=d).get("course") == "ECON10790")
        leftovers = [p.name for p in pathlib.Path(d, "note-jobs").iterdir() if ".tmp" in p.name]
        check("⭐ 原子写不留 .tmp", leftovers == [], str(leftovers))


def t_read_bad() -> None:
    with tempfile.TemporaryDirectory() as d:
        check("没有文件 -> 空 dict", NJ.read("nope", root=d) == {})
        p = pathlib.Path(d, "note-jobs")
        p.mkdir(parents=True, exist_ok=True)
        (p / "broken.json").write_text("{ 半截", encoding="utf-8")
        check("坏 JSON -> 空 dict，不抛", NJ.read("broken", root=d) == {})


def t_claim_exclusive() -> None:
    with tempfile.TemporaryDirectory() as d:
        clk = Clock()
        first = NJ.claim("stem1", root=d, now=clk)
        check("⭐ 第一个占位成功", first is True)
        check("⭐⭐ 活着的任务在时，第二个占位失败（同一节不许两个写者）",
              NJ.claim("stem1", root=d, now=clk) is False)
        NJ.finish("stem1", NJ.DONE, root=d, now=clk)
        check("⭐ 上一个结束了就能重新占位", NJ.claim("stem1", root=d, now=clk) is True)
        j = NJ.read("stem1", root=d)
        check("⭐ 重新占位不会沿用旧的 done", j.get("state") == NJ.RUNNING, str(j.get("state")))


def t_alive_three_conditions() -> None:
    with tempfile.TemporaryDirectory() as d:
        clk = Clock()
        check("没有记录 -> 不活", NJ.alive("x", root=d, now=clk) is False)
        NJ.write("x", {"state": NJ.RUNNING, "pid": 0}, root=d, now=clk)
        check("⭐ 没 pid -> 不活", NJ.alive("x", root=d, now=clk) is False)
        NJ.write("x", {"state": NJ.RUNNING, "pid": os.getpid()}, root=d, now=clk)
        check("⭐ 本进程 pid + 新心跳 -> 活", NJ.alive("x", root=d, now=clk) is True)
        NJ.write("x", {"state": NJ.RUNNING, "pid": 999_999_999}, root=d, now=clk)
        check("⭐ pid 不在 -> 不活", NJ.alive("x", root=d, now=clk) is False)
        NJ.write("x", {"state": NJ.DONE, "pid": os.getpid()}, root=d, now=clk)
        check("⭐ 状态已结束 -> 不活", NJ.alive("x", root=d, now=clk) is False)


def t_alive_uses_heartbeat_not_total_duration() -> None:
    with tempfile.TemporaryDirectory() as d:
        clk = Clock()
        NJ.write("y", {"state": NJ.RUNNING, "pid": 1234}, root=d, now=clk)
        far = lambda p: True                                            # noqa: E731
        clk.adv(NJ.STALE_S - 1)
        check("⭐ 心跳还没过期 -> 活（哪怕已经跑了很久）",
              NJ.alive("y", root=d, now=clk, alive_fn=far) is True)
        NJ.heartbeat("y", root=d, now=clk)
        clk.adv(NJ.STALE_S - 1)
        check("⭐⭐ 心跳续上了就还是活 —— 判活不看总时长（实测最长 15:49）",
              NJ.alive("y", root=d, now=clk, alive_fn=far) is True)
        clk.adv(NJ.STALE_S + 1)
        check("⭐⭐ 心跳过期 + pid 还在 = 卡死 -> 不活（恢复函数接手）",
              NJ.alive("y", root=d, now=clk, alive_fn=far) is False)


def t_heartbeat_does_not_resurrect() -> None:
    with tempfile.TemporaryDirectory() as d:
        clk = Clock()
        NJ.claim("z", root=d, now=clk)
        NJ.finish("z", NJ.DONE, root=d, now=clk)
        check("⭐ 结束后的心跳不写", NJ.heartbeat("z", root=d, now=clk) is False)
        check("⭐⭐ 迟到的 worker 不许把 done 改回 running",
              NJ.read("z", root=d).get("state") == NJ.DONE, str(NJ.read("z", root=d)))
        check("收工后不活", NJ.alive("z", root=d, now=clk) is False)


def t_bad_stem_and_unwritable() -> None:
    try:
        NJ.path("../etc/passwd")
        check("⭐⭐ stem 里有 .. 要拒", False)
    except ValueError:
        check("⭐⭐ stem 里有 .. 要拒", True)
    try:
        NJ.path("a/b")
        check("⭐ stem 里有分隔符要拒", False)
    except ValueError:
        check("⭐ stem 里有分隔符要拒", True)
    check("坏 stem 读成空、不抛", NJ.read("a/b", root="/tmp") == {})
    check("⭐ 写不进去也永不抛", NJ.write("ok", {"a": 1}, root="/proc/definitely-not-writable") is False)


def t_active_lists_live_only() -> None:
    with tempfile.TemporaryDirectory() as d:
        clk = Clock()
        NJ.claim("live1", root=d, now=clk)
        NJ.claim("live2", root=d, now=clk)
        NJ.finish("live2", NJ.DONE, root=d, now=clk)
        NJ.write("old", {"state": NJ.RUNNING, "pid": os.getpid()}, root=d, now=clk)
        clk.adv(NJ.STALE_S + 5)
        NJ.heartbeat("live1", root=d, now=clk)
        got = NJ.active(root=d, now=clk)
        check("⭐⭐ 只列活着的（done 的、心跳过期的都不列）",
              [r["stem"] for r in got] == ["live1"], str(got))


def t_stop_only_signals_recorded_pid() -> None:
    with tempfile.TemporaryDirectory() as d:
        clk = Clock()
        # 记录里的 pid 不存在（真的不存在 —— 不替换 `os.kill`，那条 patch 会让
        # `pid_alive` 跟着说谎，判据就成了自娱自乐）
        NJ.write("s1", {"state": NJ.RUNNING, "pid": 999_999_999}, root=d, now=clk)
        got = NJ.stop("s1", root=d, now=clk, timeout=0.1)
        check("⭐ pid 已经不在 -> 不发信号，直接把状态收成 stopped（免得白等 180 s）",
              got is False and NJ.read("s1", root=d).get("state") == NJ.STOPPED,
              str((got, NJ.read("s1", root=d).get("state"))))

        # 记录里是**自己** -> 绝不许发（那是自尽）
        NJ.write("s2", {"state": NJ.RUNNING, "pid": os.getpid()}, root=d, now=clk)
        check("⭐⭐ 记录里是本进程 pid -> 不发信号（不许自尽）",
              NJ.stop("s2", root=d, now=clk, timeout=0.1) is False)

        # 僵尸：已经退出、但父进程没 wait -> `kill -0` 照样成功。不许白等满超时。
        import subprocess as _sp
        z = _sp.Popen(["true"])
        z.wait()                                          # 现在它是僵尸，直到被回收
        # ⚠️ 这里已经被 wait 掉了，所以再造一个不 wait 的：
        z2 = _sp.Popen(["true"])
        import time as _t2
        _t2.sleep(0.2)                                    # 让它跑完、进入僵尸
        NJ.write("s4", {"state": NJ.RUNNING, "pid": z2.pid}, root=d, now=clk)
        _t0 = _t2.time()
        got4 = NJ.stop("s4", root=d, now=clk, timeout=3.0)
        _dt = _t2.time() - _t0
        try:
            z2.kill()
        except Exception:                                  # noqa: BLE001
            pass
        check("⭐⭐ 僵尸进程算死 —— 不白等满超时（实测 0.01 s 而不是 5 s）",
              got4 is False and _dt < 1.0
              and NJ.read("s4", root=d).get("state") == NJ.STOPPED,
              f"{got4} {_dt:.2f}s {NJ.read('s4', root=d).get('state')}")

        # 已经结束 -> 什么都不做
        NJ.finish("s3", NJ.DONE, root=d, now=clk)
        check("⭐ 已经结束的任务 -> 不发信号", NJ.stop("s3", root=d, now=clk) is False)


def main() -> int:
    print("note_jobs 判据（注入时钟，不碰 AppKit）")
    for name, fn in [
        ("记录读写 + 原子写", t_roundtrip_atomic),
        ("坏文件当空", t_read_bad),
        ("占位互斥", t_claim_exclusive),
        ("判活三条缺一不可", t_alive_three_conditions),
        ("判活靠心跳不靠总时长", t_alive_uses_heartbeat_not_total_duration),
        ("结束后的心跳不复活", t_heartbeat_does_not_resurrect),
        ("坏 stem / 写不进去", t_bad_stem_and_unwritable),
        ("active 只列活任务", t_active_lists_live_only),
        ("停止只认记录里的 pid", t_stop_only_signals_recorded_pid),
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
