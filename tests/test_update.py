#!/usr/bin/env python3
"""`update.py` 的回归测试 —— 全部在临时目录里，**绝不碰真仓库、真日志**。

    ClassLive.app/Contents/MacOS/python tests/test_update.py

覆盖两块：

**A. pull() 的三条边界**（安全规则，错一条就是丢用户的工作）
  ① 干净+落后 -> 能拉        ② 脏工作区 -> 停手且一个字节都不动
  ③ 分叉 -> --ff-only 失败且不造 merge   ④ 已最新 -> skipped
  ⑤ requirements.txt 变化能被检出        ⑥ check() 不动工作区

**B. auto_update() 的分级与降级**（退出时自动更新的全部判断）
  ① auto -> 拉   ② 没写 -> 默认拒绝   ③ 显式 manual -> 拒绝
  ④ 跨多版本全 auto -> 拉   ⑤ 跨多版本夹一个 manual -> 拒绝
  ⑥ 限频   ⑦ 并发锁   ⑧ 没 upstream   ⑨ 工作区脏   ⑩ 不是 git 仓库

⚠️ 写这个测试时踩过的坑（**测试自己的** bug，白跑一轮）：
   造"新版本"时必须插到 CHANGELOG **最顶**（那文件是"新的在上"）。初版插在
   最后一个版本前面 -> 顺序变成旧的在上 -> `versions_in` 拿乱序 -> `todo` 算空
   -> 本该跳过的却拉了。测试脚本里加了断言专门防这个。
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import update                                                    # noqa: E402

ROOT = pathlib.Path(tempfile.mkdtemp(prefix="cl_updtest_"))
_N = [0]
RESULTS: list[tuple[str, bool]] = []


def sh(*a, cwd):
    r = subprocess.run(a, cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, f"{a} -> {r.stderr}"
    return r.stdout.strip()


def commit(d, msg, version=None, reqs=None):
    if version is not None:
        (d / "VERSION").write_text(version + "\n", encoding="utf-8")
    if reqs is not None:
        (d / "requirements.txt").write_text(reqs, encoding="utf-8")
    sh("git", "add", "-A", cwd=d)
    sh("git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", msg, cwd=d)


def new_world():
    """一对全新 origin/seed/clone，clone 停在 0.0.1。"""
    _N[0] += 1
    tmp = ROOT / f"w{_N[0]}"
    tmp.mkdir(parents=True)
    origin, seed, clone = tmp / "origin", tmp / "seed", tmp / "clone"
    sh("git", "init", "--bare", "-b", "main", str(origin), cwd=tmp)
    sh("git", "clone", str(origin), str(seed), cwd=tmp)
    sh("git", "checkout", "-b", "main", cwd=seed)
    (seed / "VERSION").write_text("0.0.1\n", encoding="utf-8")
    (seed / "requirements.txt").write_text("numpy>=2.0\n", encoding="utf-8")
    (seed / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [0.0.1] - 2026-01-01\n\n### 更新看点\n\n- 初始\n",
        encoding="utf-8")
    sh("git", "add", "-A", cwd=seed)
    sh("git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "init", cwd=seed)
    sh("git", "push", "-u", "origin", "main", cwd=seed)
    sh("git", "clone", str(origin), str(clone), cwd=tmp)
    update.HERE = clone
    update.STATE_DIR = tmp / "logs"
    return tmp, seed, clone


def release(seed, ver, mode=None, reqs=None):
    """造一个版本推到远端。mode=None -> 不写 `### 更新方式`。⚠️ 插到**最顶**。"""
    cl = (seed / "CHANGELOG.md").read_text(encoding="utf-8")
    msec = f"### 更新方式\n\n{mode}\n\n" if mode else ""
    block = f"## [{ver}] - 2026-09-24\n\n{msec}### 更新看点\n\n- rel {ver}\n\n"
    cl = "# Changelog\n\n" + block + cl[len("# Changelog\n\n"):]
    (seed / "CHANGELOG.md").write_text(cl, encoding="utf-8")
    if reqs is not None:
        (seed / "requirements.txt").write_text(reqs, encoding="utf-8")
    (seed / "VERSION").write_text(ver + "\n", encoding="utf-8")
    sh("git", "add", "-A", cwd=seed)
    sh("git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", f"rel {ver}", cwd=seed)
    sh("git", "push", cwd=seed)
    assert update.versions_in(cl)[0] == ver, "测试脚本自己错了：CHANGELOG 顺序不是新的在上"


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok))
    print(f"  {'✅' if ok else '❌'} {name}" + (f"\n      {detail}" if detail else ""))


# ======================= A. pull() 的三条边界 =======================
print("\nA. pull() 的安全边界\n")

print("--- A1/A4/A5/A6 ---")
tmp, seed, clone = new_world()
r = update.pull()
check("A4 已最新 -> skipped", r["ok"] and r["skipped"], f"{r}")

release(seed, "0.0.2", reqs="numpy>=2.0\nhttpx>=0.28\n")
r = update.pull()
check("A1 干净+落后 -> 拉到", r["ok"] and r["after"] == "0.0.2",
      f"{r['before']}->{r['after']} commits={r['commits']}")
check("A5 requirements 变化被检出", r["reqs_changed"], f"reqs_changed={r['reqs_changed']}")

(clone / "EDIT.txt").write_text("x\n", encoding="utf-8")
c = update.check()
check("A6 check() 不动工作区（只报 dirty）", c["ok"] and c["dirty"],
      f"ok={c['ok']} dirty={c['dirty']}")

print("\n--- A2 脏工作区 ---")
tmp, seed, clone = new_world()
release(seed, "0.0.2")
(clone / "MY_EDIT.txt").write_text("别丢我\n", encoding="utf-8")
before_v, before_files = (clone / "VERSION").read_text().strip(), sorted(p.name for p in clone.iterdir())
r = update.pull()
after_v, after_files = (clone / "VERSION").read_text().strip(), sorted(p.name for p in clone.iterdir())
check("A2 脏工作区 -> 停手且不动任何文件",
      (not r["ok"]) and r.get("blocked") and before_v == after_v and before_files == after_files,
      f"blocked={r.get('blocked')} VERSION {before_v}->{after_v} 文件数 {len(before_files)}->{len(after_files)}")

print("\n--- A3 分叉 ---")
tmp, seed, clone = new_world()
commit(clone, "local: 本地自己提交", version="9.9.9-local")
release(seed, "0.0.2")
r = update.pull()
merges = sh("git", "log", "--merges", "--oneline", cwd=clone)
check("A3 分叉 -> --ff-only 失败且不造 merge",
      (not r["ok"]) and not merges and "9.9.9-local" in (clone / "VERSION").read_text(),
      f"merges={merges!r} 本地提交还在={'9.9.9-local' in (clone / 'VERSION').read_text()}")

# ======================= B. auto_update() 的分级 =======================
print("\nB. auto_update() 的分级与降级\n")


def auto_case(name, releases, expect_pull, dirty=False):
    tmp, seed, clone = new_world()
    for item in releases:
        release(seed, item[0], item[1])
    if dirty:
        (clone / "MY_EDIT.txt").write_text("别丢我\n", encoding="utf-8")
    before = (clone / "VERSION").read_text().strip()
    files_before = sorted(p.name for p in clone.iterdir())
    r = update.auto_update()
    after = (clone / "VERSION").read_text().strip()
    files_after = sorted(p.name for p in clone.iterdir())
    no_loss = files_before == files_after
    check(name, (before != after) == expect_pull and no_loss,
          f"{before}->{after} pulled={before != after} 期望={expect_pull} "
          f"reason={r.get('reason', '') or '-'} 文件无损={no_loss}")


auto_case("B1 remote 标 auto -> 拉", [("0.0.2", "auto")], True)
auto_case("B2 没写「更新方式」-> 默认拒绝", [("0.0.2", None)], False)
auto_case("B3 显式 manual -> 拒绝", [("0.0.2", "manual")], False)
auto_case("B4 跨 3 个版本全 auto -> 拉",
          [("0.0.2", "auto"), ("0.0.3", "auto"), ("0.0.4", "auto")], True)
auto_case("B5 跨 3 个版本夹一个 manual -> 拒绝",
          [("0.0.2", "auto"), ("0.0.3", "manual"), ("0.0.4", "auto")], False)
auto_case("B9 工作区脏 -> 拒绝且不动文件", [("0.0.2", "auto")], False, dirty=True)

print("\n--- B6 限频 ---")
tmp, seed, clone = new_world()
release(seed, "0.0.2", "auto")
update.auto_update()
v1 = (clone / "VERSION").read_text().strip()
release(seed, "0.0.3", "auto")
r2 = update.auto_update()
v2 = (clone / "VERSION").read_text().strip()
check("B6 一小时内第二次 -> 跳过", v1 == "0.0.2" and v2 == "0.0.2" and bool(r2.get("skipped")),
      f"第一次->{v1} 第二次->{v2} reason={r2.get('reason', '')}")

print("\n--- B7 并发锁 ---")
tmp, seed, clone = new_world()
release(seed, "0.0.2", "auto")
update.STATE_DIR.mkdir(parents=True, exist_ok=True)
lk = update.STATE_DIR / "update.lock"
lk.write_text("99999", encoding="utf-8")          # 假装**别的进程**持有锁
r = update.auto_update()
check("B7 有锁 -> 跳过", bool(r.get("skipped")) and "跑" in r.get("reason", ""),
      f"reason={r.get('reason', '')}")
# ⚠️ 关键断言：早退时**绝不能删掉别人的锁**。
# 漏了这条，就抓不住"无条件 unlink 把别人锁删了 -> 两个更新器同时 pull"那个 bug
# （2026-09-25 全量 OCR 发现；当时 B7 只测了"跳过"，所以没拦住）。
check("B7b 早退时**不动**别人的锁", lk.exists(),
      f"锁文件还在={lk.exists()}（旧代码会把它删掉）")
lk.unlink(missing_ok=True)

# B7c: 锁**被别人抢走**后，也不能删别人的。
# ⚠️ 光记 `held` 标志不够 —— 它只证明"我写过锁"，不证明"锁现在还是我的"。
# (2026-09-25 ocr review 发现)
tmp2, seed2, clone2 = new_world()
release(seed2, "0.0.2", "auto")
update.STATE_DIR.mkdir(parents=True, exist_ok=True)
lk2 = update.STATE_DIR / "update.lock"
# 让 auto_update 拿到锁，但桩掉 pull() 使其在持锁期间"被别人抢走"
_real_pull = update.pull
def _steal(*a, **kw):
    lk2.write_text("99999", encoding="utf-8")      # 模拟别的进程抢走
    return {"ok": True, "skipped": True, "after": "0.0.1", "error": "",
            "commits": 0, "reqs_changed": False}
update.pull = _steal
update.auto_update()
update.pull = _real_pull
check("B7c 锁被抢走后**不删**别人的锁", lk2.exists() and lk2.read_text().strip() == "99999",
      f"锁内容={lk2.read_text().strip()!r}（期望 '99999'，即抢走者的 pid）")
lk2.unlink(missing_ok=True)

print("\n--- B8 没有 upstream ---")
tmp = ROOT / "noup"
tmp.mkdir()
update.HERE, update.STATE_DIR = tmp, ROOT / "noup_logs"
(tmp / "VERSION").write_text("0.0.1\n", encoding="utf-8")
(tmp / "CHANGELOG.md").write_text("## [0.0.1]\n", encoding="utf-8")
sh("git", "init", "-b", "main", str(tmp), cwd=ROOT)
sh("git", "add", "-A", cwd=tmp)
sh("git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "x", cwd=tmp)
r = update.auto_update()
check("B8 没 upstream -> 静默跳过不抛", bool(r.get("skipped") and not r.get("error")), f"{r}")

print("\n--- B10 不是 git 仓库 ---")
tmp = ROOT / "plain"
tmp.mkdir()
update.HERE, update.STATE_DIR = tmp, ROOT / "plain_logs"
(tmp / "VERSION").write_text("0.0.1\n", encoding="utf-8")
r = update.auto_update()
check("B10 不是 git 仓库 -> 静默跳过", bool(r.get("skipped") and not r.get("error")), f"{r}")

# ======================= 汇总 =======================
bad = [n for n, ok in RESULTS if not ok]
print(f"\n{'=' * 60}")
print(f"{len(RESULTS) - len(bad)}/{len(RESULTS)} 通过")
if bad:
    print("失败：")
    for n in bad:
        print(f"  ❌ {n}")
shutil.rmtree(ROOT, ignore_errors=True)
sys.exit(1 if bad else 0)
