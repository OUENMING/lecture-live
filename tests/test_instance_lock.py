#!/usr/bin/env python3
"""单实例锁的回归测试。

跑法: ClassLive.app/Contents/MacOS/python tests/test_instance_lock.py

⚠️ 覆盖的核心是 **flock 的"进程怎么死都会自动释放"** 这条 ——
   那正是它相对 pidfile 的全部价值，也是当初选它的唯一理由。
   如果哪天有人把它换成 pidfile，A4 那条必须变红。
"""
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import instance_lock  # noqa: E402

PASS = []
FAIL = []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + (f"   {detail}" if detail else ""))


def _child(code: str, *args) -> subprocess.CompletedProcess:
    """在**另一个进程**里跑 —— 同进程内 flock 是可重入的，测不出互斥。"""
    return subprocess.run(
        [sys.executable, "-c",
         f"import sys, pathlib; sys.path.insert(0, {str(HERE)!r}); "
         f"import instance_lock; {code}"] + list(args),
        capture_output=True, text=True, timeout=30)


# ---- A. 基本互斥 ----

def test_basic(tmp):
    lk = pathlib.Path(tmp) / "a" / "instance.lock"     # 父目录不存在，应自动建
    f, holder = instance_lock.acquire(lk)
    check("A1 第一次能拿到锁", f is not None and holder is None)
    check("A2 锁文件被建出来了（含父目录）", lk.exists())
    check("A3 锁文件里写的是自己的 pid", lk.read_text().strip() == str(os.getpid()),
          f"内容={lk.read_text().strip()!r}")

    r = _child("f, h = instance_lock.acquire(pathlib.Path(sys.argv[1])); "
               "print('HELD' if f is None else 'GOT', h or '')", str(lk))
    out = r.stdout.strip().split()
    check("A4 另一个进程抢不到", out and out[0] == "HELD", r.stdout.strip())
    check("A5 而且能报出占用者的 pid", len(out) > 1 and out[1] == str(os.getpid()),
          f"报的是 {out[1] if len(out) > 1 else '?'}，期望 {os.getpid()}")

    del f                                                # 释放
    r2 = _child("f, h = instance_lock.acquire(pathlib.Path(sys.argv[1])); "
                "print('HELD' if f is None else 'GOT')", str(lk))
    check("A6 持有者释放后，别人能拿到", r2.stdout.strip() == "GOT")


# ---- B. 释放语义 ----

def test_release_on_exit(tmp):
    """持有者**正常退出**，锁要自动释放。"""
    lk = pathlib.Path(tmp) / "b.lock"
    r = _child("f, h = instance_lock.acquire(pathlib.Path(sys.argv[1])); "
               "print('GOT' if f else 'HELD')", str(lk))
    check("B1 子进程拿到了锁", r.stdout.strip() == "GOT")
    time.sleep(0.3)
    r2 = _child("f, h = instance_lock.acquire(pathlib.Path(sys.argv[1])); "
                "print('GOT' if f else 'HELD')", str(lk))
    check("B2 子进程退出后锁自动释放（无需清理逻辑）", r2.stdout.strip() == "GOT")


def test_release_on_kill9(tmp):
    """⚠️ **这条是选 flock 而不是 pidfile 的全部理由。**

    持有者被 SIGKILL 打死 —— pidfile 那套会留下一个死 pid 的文件，
    下次启动被一个不存在的进程"锁死"；flock 是内核托管的，会自动释放。

    ⚠️⚠️ **C3 单独看是恒真的** —— 2026-09-26 红绿验证时实测：把 flock 整行去掉、
    完全不锁，C3 **照样是绿的**（都没锁，当然谁都能拿到）。所以 C3 必须和
    C1/C2 连起来读：C1 证明"确实锁上了"、C2 证明"锁着的时候别人进不来"、
    然后 C3 才有意义。只留 C3 等于没测。
    """
    lk = pathlib.Path(tmp) / "c.lock"
    p = subprocess.Popen(
        [sys.executable, "-c",
         f"import sys, pathlib, time; sys.path.insert(0, {str(HERE)!r}); "
         f"import instance_lock; "
         f"f, h = instance_lock.acquire(pathlib.Path({str(lk)!r})); "
         f"print('GOT' if f else 'HELD', flush=True); time.sleep(60)"],
        stdout=subprocess.PIPE, text=True)
    first = p.stdout.readline().strip()
    check("C1 子进程拿到了锁", first.startswith("GOT"), first)

    r_mid = _child("f, h = instance_lock.acquire(pathlib.Path(sys.argv[1])); "
                   "print('HELD' if f is None else 'GOT')", str(lk))
    check("C2 它活着时别人抢不到", r_mid.stdout.strip() == "HELD")

    p.kill()                                             # SIGKILL
    p.wait(timeout=10)
    time.sleep(0.5)
    r_after = _child("f, h = instance_lock.acquire(pathlib.Path(sys.argv[1])); "
                     "print('HELD' if f is None else 'GOT')", str(lk))
    check("C3 ⭐ 被 kill -9 之后锁**自动释放**", r_after.stdout.strip() == "GOT")


# ---- D. 降级与措辞 ----

def test_degrade(tmp):
    """锁文件建不出来时不能拦住上课 —— 那只是防重复，不是安全边界。"""
    f, h = instance_lock.acquire(pathlib.Path("/proc/nonexistent/x.lock"))
    check("D1 建不出锁文件时不抛异常、当作没锁", f is None and h is None)
    check("D2 describe_holder 有人话版本（有 pid）",
          "进程 12345" in instance_lock.describe_holder(12345))
    check("D3 describe_holder 有人话版本（没 pid）",
          instance_lock.describe_holder(None) == "已经有一个 ClassLive 在跑")


def main():
    print("\n单实例锁 · 回归测试\n" + "─" * 46)
    tmp = tempfile.mkdtemp(prefix="classlive_lock_")
    try:
        for fn in (test_basic, test_release_on_exit, test_release_on_kill9, test_degrade):
            fn(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("─" * 46)
    print(f"{len(PASS)}/{len(PASS) + len(FAIL)} 通过")
    if FAIL:
        print("失败: " + "、".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
