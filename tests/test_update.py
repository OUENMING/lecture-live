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
    # ⚠️ **不能用 `assert`**（2026-09-28 审查指出）：`python -O` 会把 assert
    #    **整条剥掉**，于是 git 命令失败被静默忽略 —— 测试世界（origin/seed/clone）
    #    自己先坏了，后面全是难以定位的假绿/假红。
    if r.returncode != 0:
        raise RuntimeError(f"{a} -> {r.stderr}")
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
    # ⚠️ 同上：这条护栏防的是「顺序倒置 → `todo` 算空 → 本该跳过的却拉了」，
    #    被 `-O` 剥掉就等于**顺序倒置的 bug 重新变成假绿**。改显式 raise。
    if update.versions_in(cl)[0] != ver:
        raise RuntimeError("测试脚本自己错了：CHANGELOG 顺序不是新的在上")


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


def _snapshot(root) -> dict:
    """工作区快照：**每个文件的相对路径 → 内容 sha256**。

    ⚠️ 跳过 `.git/` —— 那是测试自己造的仓库内务，不是"用户的工作区内容"。
    """
    import hashlib
    out = {}
    for p in sorted(root.rglob("*")):
        if not p.is_file() or ".git" in p.parts:
            continue
        out[p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def auto_case(name, releases, expect_pull, dirty=False):
    tmp, seed, clone = new_world()
    for item in releases:
        release(seed, item[0], item[1])
    if dirty:
        (clone / "MY_EDIT.txt").write_text("别丢我\n", encoding="utf-8")
    before = (clone / "VERSION").read_text().strip()
    names_before = sorted(p.name for p in clone.iterdir())
    snap_before = _snapshot(clone)
    r = update.auto_update()
    after = (clone / "VERSION").read_text().strip()
    names_after = sorted(p.name for p in clone.iterdir())
    snap_after = _snapshot(clone)
    # ⚠️⚠️ 「无损」要分**两档**（2026-09-28 审查指出）：原来只比 `iterdir()` 的
    #    **顶层文件名** —— `VERSION` / `requirements.txt` / `CHANGELOG.md` 被
    #    **改写或截断**时名字一个都没变，于是「脏工作区停手、一个字节都不动」
    #    这条核心安全断言是**弱的**。
    #    · **该拒绝**的那几档：内容也必须逐字节相同（那才是"停手"）；
    #    · **真拉**的那一档：内容本来就会变（那是它的目的）—— 要求的是**没丢文件**。
    no_loss = ((snap_before == snap_after) if not expect_pull
               else (names_before == names_after))
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
# ⚠️ **还原必须走 `finally`**（2026-09-28 审查指出）：原来只在正常返回时才
#    `update.pull = _real_pull` —— `auto_update()` 一抛异常，桩就残留下来
#    污染后面的 B8/B10/B11（而且本文件顶层没有 try，`shutil.rmtree` 与汇总也不会跑）。
try:
    update.pull = _steal
    update.auto_update()
finally:
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

# ============== B11 构建判据的方向：出错要倒向「重建」 ==============
# ⚠️ `make-app.sh --up-to-date` 的退出码方向是**故意的**：0 = 最新、**非 0 = 该重建**。
#    反过来（0 = 该重建）看着更自然，但**脚本自身一出错也落成非零** → 被读成「最新」
#    → **静默跳过重建** —— 正好复现要修的那个 bug（代码更新了、图标不生效）。
#    2026-09-26 实测就踩到过一次：`$_want）` 触发本仓库那条「$VAR 后紧跟全角字符
#    会被吞」的雷区 → unbound → install.sh 当场静默跳过。
#    这条一反过来就**静默退化**，所以两侧都钉住。
print("\n--- B11 构建判据的方向 ---")
from unittest import mock as _mock                                       # noqa: E402


def _pending_keys(rc: int, out: str = "") -> list[str]:
    """把 `make-app.sh --up-to-date` 的返回**假造**成指定的退出码/输出。"""
    with _mock.patch.object(update.subprocess, "run",
                            return_value=subprocess.CompletedProcess([], rc, out, "")):
        return [s["key"] for s in update.pending_steps()]


check("B11 退出码非零（含脚本自身出错）→ 出「重建 .app」",
      "app" in _pending_keys(1, "./make-app.sh: line 76: _want: unbound variable"))
check("B11 退出码为 0 → 不出「重建 .app」",
      "app" not in _pending_keys(0))

# ============== B12 ⭐ 模型清单变了要出声（与 reqs_changed 对称）==============
# ⚠️ 2026-09-30 审计发现的洞：后台**自动更新只拉代码、不下模型**（`run_step("models")`
#    是**手动**那条路才跑的）。原来只有 `reqs_changed` 有警告 ——
#    于是一个新增的**必下**模型会被静默拖到下次启动才暴露。
#    这一组钉两件事：**别漏报**（真变了要说）· **别假阳**（没变不许说）。
print("\n--- B12 模型清单变化 ---")
check("⚠️ 空 sha / 相同 sha -> False（不抛）",
      update._models_changed("", "abc") is False
      and update._models_changed("abc", "abc") is False
      and update._models_changed("abc", "") is False)

_tmp, _seed, _clone = new_world()

# ① **防假阳**：只动 CHANGELOG/VERSION -> 不许报
release(_seed, "0.0.2")
_r1 = update.pull()
check("① 没碰 models.py -> `models_changed=False`（这条防的是「出过 diff 就报」）",
      bool(_r1.get("ok")) and _r1.get("models_changed") is False,
      f"ok={_r1.get('ok')} models_changed={_r1.get('models_changed')!r}")

# ② 真的动了 models.py -> 必须报
(_seed / "models.py").write_text("MODELS = ()\n", encoding="utf-8")
release(_seed, "0.0.3")
_r2 = update.pull()
check("⭐⭐ 碰了 models.py -> `models_changed=True`",
      bool(_r2.get("ok")) and _r2.get("models_changed") is True,
      f"ok={_r2.get('ok')} models_changed={_r2.get('models_changed')!r}")
# ⚠️ 同时确认它**没有**污染另一半：这次没动 requirements.txt
check("⚠️ 那一次 `reqs_changed` 仍然 False（两半互不干扰）",
      _r2.get("reqs_changed") is False, f"{_r2.get('reqs_changed')!r}")

# ③ 自动更新那条路要把这声**真的说出来** —— 不然加 `models_changed` 等于白加。
# ⚠️ 第一版这里写的是「`skipped` 或 `ok is not None`」，**那是假绿**（几乎恒真，
#    而且那时 clone 已经是最新、根本不走警告那条分支）。改成**去日志里找那句话**。
_tmp4, _seed4, _clone4 = new_world()
release(_seed4, "0.0.2", "auto")
(_seed4 / "models.py").write_text("MODELS = ()\n", encoding="utf-8")
release(_seed4, "0.0.3", "auto")
_r4 = update.auto_update()
_ver4 = (_clone4 / "VERSION").read_text(encoding="utf-8").strip()
_log4 = (update.STATE_DIR / "update.log").read_text(encoding="utf-8")
check("⭐⭐ 自动更新**真的拉了**、而且日志里出现了「模型清单有变化」",
      _ver4 == "0.0.3" and "模型清单有变化" in _log4,
      f"版本到 {_ver4}（期望 0.0.3）、日志里有那一声={'模型清单有变化' in _log4}")
# ⚠️ 反向对照：**没**动 models.py 的那一轮，日志里**不该**有这句话。
#    少了这条，上面那句可能只是「无论什么都打」。
#    ⚠️ 每个 `new_world()` 都换一个全新的 `STATE_DIR`（`tmp/logs`）——
#       所以下面是**另一份**日志，不是上面那份的尾巴。
_tmp5, _seed5, _clone5 = new_world()
release(_seed5, "0.0.4", "auto")
update.auto_update()
_log5 = (update.STATE_DIR / "update.log").read_text(encoding="utf-8")
check("⚠️ 对照：这一轮没碰 models.py -> 日志里**没有**那一声",
      "模型清单有变化" not in _log5,
      f"日志={_log5.strip().splitlines()[-1][:90]!r}")

# ======================= C. 只更新「正式版那条线」 =======================
# ⭐ 2026-09-30 加。这三条守的是**同一件事的三种形态**：站在错的线上时，
#    `pull()` 以前是**静默**的（开发分支会一直被拉、detached 永远报「已是最新」），
#    而那正是"朋友停在某个中间版本"的成因。判据要钉住的是：
#    **它必须说出来，而且必须一个字节都不动。**
print("\nC. 分支守卫（只更新正式版那条线）\n")


def _first_line(s) -> str:
    """把一段错误文本压成一行，给 `check` 的 detail 用。

    ⚠️⚠️ **detail 是急切求值的** —— `check(cond, f"…{(r['error'] or '').splitlines()[0]}")`
       会在 `error` 为空时**先抛 IndexError**，于是 ❌ 根本打不出来、整轮测试崩在
       半路（第一版就这么写的，变异验证当场撞上：期望"红一条"，实际是**崩溃**）。
       本仓把这条记在「判据的假绿形态 #6」里 —— detail 一定要**不可能抛**。
    """
    return repr((s or "（空）").splitlines()[0][:60])

print("--- C0 前置：夹具站在正式版线上 ---")
tmp, seed, clone = new_world()
_bs = update.branch_state()
check("C0 夹具自检：branch=main · upstream=origin/main · default=origin/main · on_default",
      _bs["branch"] == "main" and _bs["upstream"] == "origin/main"
      and _bs["default"] == "origin/main" and _bs["on_default"] and not _bs["detached"],
      f"{_bs}")
# ⚠️ 这条不是装饰：`default_ref()` 要是查不出远程默认分支（`on_default` 会**放行**），
#    下面 C1 会**假绿** —— 那是"证据不足时不吓唬人"那条设计反过来咬自己。
check("⚠️ 而且 `default` 真的查得出来（查不出会让 C1 假绿）",
      bool(_bs["default"]), f"default={_bs['default']!r}")

print("--- C1 ⭐⭐ 开发分支：不许拉它，也不许说「已是最新」 ---")
sh("git", "checkout", "-b", "dev", cwd=clone)
sh("git", "push", "-u", "origin", "dev", cwd=clone)
# 开发分支那头**真有新东西**（否则"没拉"这件事证明不了什么）
sh("git", "checkout", "-b", "dev", cwd=seed)
commit(seed, "dev：一个中间版本", version="0.0.9-dev")
sh("git", "push", "-u", "origin", "dev", cwd=seed)
sh("git", "checkout", "main", cwd=seed)
_bs1 = update.branch_state()
r = update.pull()
_ver1 = (clone / "VERSION").read_text().strip()
check("C1 站在开发分支上 -> **停手**（blocked），且说得出是哪条线",
      (not r["ok"]) and r.get("blocked") and "开发分支" in (r.get("error") or ""),
      f"ok={r['ok']} blocked={r.get('blocked')} err={_first_line(r.get('error'))}")
# ⚠️⚠️ **这一条是整组的核心**：老实现返回的是 `ok=True, skipped=True`
#    （=「已是最新」）—— 一模一样的静默。变异验证：把那道 `on_default` 守卫删掉 -> 这条红。
check("⭐⭐ 而且**不许**说「已是最新」（老实现就是这么静默的）",
      not r.get("skipped") and r["before"] == r["after"] == _ver1,
      f"skipped={r.get('skipped')} 版本 {r['before']}->{r['after']}")
check("⭐⭐ 磁盘上**一个字节都没动**（开发分支那头真有新提交也不拉）",
      _ver1 == "0.0.1", f"VERSION={_ver1}（期望仍是 0.0.1；dev 上已经是 0.0.9-dev）")
check("⭐ 返回里带着 `branch`（卡片/日志要能看出停在哪条线上）",
      isinstance(r.get("branch"), dict) and r["branch"].get("upstream") == "origin/dev",
      f"{r.get('branch')}")
check("⚠️ 说人话：不含「工作区/stash/未提交」这类词，且给出回正式版的那条命令",
      all(w not in (r.get("user_msg") or "") for w in ("工作区", "stash", "未提交"))
      and "git checkout main" in (r.get("user_msg") or ""),
      f"user_msg={(r.get('user_msg') or '')!r}")

print("--- C1b 对照：同一条世界，切回 main 就该能更新 ---")
# ⚠️ 没有这条，C1 可能只是"什么都没做也 blocked"（那才是假判据）。
sh("git", "checkout", "main", cwd=clone)
release(seed, "0.0.2")
r2 = update.pull()
check("C1b 切回正式版线 -> 照常拉到（守卫钉的是分支，不是「什么都拦」）",
      r2["ok"] and r2["after"] == "0.0.2", f"ok={r2['ok']} {r2['before']}->{r2['after']}")

print("--- C2 ⭐⭐ detached HEAD：以前**永远报「已是最新」** ---")
tmp, seed, clone = new_world()
release(seed, "0.0.2")                       # 正式版前进了，而这份停在旧提交上
sh("git", "fetch", "--quiet", cwd=clone)     # ⚠️ 先 fetch —— 下面那条前置量的是
                                             #    `origin/main` 这个**本地 ref**，
                                             #    没 fetch 的话它还是旧的（第一版就这么假红）
sh("git", "checkout", "--detach", "HEAD", cwd=clone)
check("C2 前置：夹具真的 detached 了", update.current_branch() == "",
      f"branch={update.current_branch()!r}")
check("C2 前置：它**真的**落后 origin/main（不然'没更新'证明不了什么）",
      sh("git", "rev-list", "--count", "HEAD..origin/main", cwd=clone) == "1",
      sh("git", "rev-list", "--count", "HEAD..origin/main", cwd=clone))
r = update.pull()
check("⭐⭐ detached -> 停手，并说「不在任何分支上」",
      (not r["ok"]) and r.get("blocked") and "不在任何分支上" in (r.get("error") or ""),
      f"ok={r['ok']} blocked={r.get('blocked')}")
check("⭐⭐ **不许**说「已是最新」（老实现就在这一支静默卡死）",
      not r.get("skipped"), f"skipped={r.get('skipped')} error={(r.get('error') or '')[:40]!r}")
check("⭐ 版本没动（0.0.1 而不是 0.0.2）",
      (clone / "VERSION").read_text().strip() == "0.0.1")

print("--- C3 本地新建的分支（没有上游）---")
tmp, seed, clone = new_world()
release(seed, "0.0.2")
sh("git", "checkout", "-b", "mine", cwd=clone)
check("C3 前置：这条分支确实没有上游", update.upstream_ref() == "",
      f"upstream={update.upstream_ref()!r}")
check("⚠️ 但**默认分支仍查得出来**（这正是第一版 `default_ref` 的错处："
      "它拿 upstream 推远程名 -> 无上游就全查不到 -> 又跌回静默）",
      update.default_ref() == "origin/main", f"default={update.default_ref()!r}")
r = update.pull()
check("⭐⭐ 无上游 -> 停手 + 说清「没有对应的远程」，**不是**「已是最新」",
      (not r["ok"]) and r.get("blocked") and "没有对应的远程" in (r.get("error") or "")
      and not r.get("skipped"),
      f"blocked={r.get('blocked')} skipped={r.get('skipped')}")

print("--- C4 ⚠️ 三句停手的话必须**互不相同** ---")
# ⚠️ 合成一句的话，站在开发分支上的人会去翻"我改过什么"—— 而他该做的是换分支。
#    三件事，三个动作。（变异验证：把三条并成一条 -> 这条红。）
tmp, seed, clone = new_world()
sh("git", "checkout", "-b", "dev", cwd=clone)
sh("git", "push", "-u", "origin", "dev", cwd=clone)
_msgs = {}
_msgs["开发分支"] = update.pull().get("user_msg", "")
sh("git", "checkout", "--detach", "HEAD", cwd=clone)
_msgs["detached"] = update.pull().get("user_msg", "")
sh("git", "checkout", "-b", "mine", cwd=clone)
_msgs["无上游"] = update.pull().get("user_msg", "")
(clone / "EDIT.txt").write_text("x\n", encoding="utf-8")
sh("git", "checkout", "main", cwd=clone)
_msgs["工作区脏"] = update.pull().get("user_msg", "")
check("⚠️ 四种停手各有各的话（并成一句 -> 用户会去做错的事）",
      len(set(_msgs.values())) == 4 and all(_msgs.values()),
      f"{ {k: v[:24] for k, v in _msgs.items()} }")

print("--- C5 `cl doctor` 那一行（fix #2：让「我这份是什么」一眼看得出）---")
import doctor                                                    # noqa: E402
tmp, seed, clone = new_world()
check("C5 在正式版线上 -> 平淡地说出分支名",
      doctor._branch_note() == "分支 main（正式版线）", repr(doctor._branch_note()))
sh("git", "checkout", "-b", "dev", cwd=clone)
sh("git", "push", "-u", "origin", "dev", cwd=clone)
_note = doctor._branch_note() or ""
check("⭐⭐ 开发分支 -> 显式 ⚠️ + 说出是哪条 + 给出回正式版的命令",
      _note.startswith("⚠️") and "origin/dev" in _note and "git checkout main" in _note,
      repr(_note))
sh("git", "checkout", "--detach", "HEAD", cwd=clone)
_note2 = doctor._branch_note() or ""
check("⭐⭐ detached -> 也显式 ⚠️（doctor 不许把这种状态说成「可以跑」就完事）",
      _note2.startswith("⚠️") and "不在任何分支上" in _note2, repr(_note2))
# ⚠️ 反向对照：**非 git 仓库**那一档不许冒出分支提示（那是另一码事，
#    那一档的说法已经在 `git_info()` 第一项里了）。
#    ⚠️ 判据要**真的把 HERE 指过去**（第一版写成 `... if not exists else True`
#       —— 那在正常情况下恒真，是条假判据）。
_orig_here = doctor.HERE
_nr = ROOT / "not-a-repo"
_nr.mkdir(exist_ok=True)
try:
    doctor.HERE = _nr
    _gi = doctor.git_info()
finally:
    doctor.HERE = _orig_here
check("⚠️ 对照：非 git 仓库 -> 不提分支（第三项是 None）",
      _gi[2] is None and "非 git 仓库" in _gi[0], str(_gi))

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
