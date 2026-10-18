#!/usr/bin/env python3
"""`update.py` 的回归测试 —— 全部在临时目录里，**绝不碰真仓库、真日志**。

    ClassLive.app/Contents/MacOS/python tests/test_update.py

覆盖两块：

**A. pull() 的三条边界**（安全规则，错一条就是丢用户的工作）
  ① 干净+落后 -> 能拉        ② 脏工作区 -> 停手且一个字节都不动
  ③ 分叉 -> --ff-only 失败且不造 merge   ④ 已最新 -> skipped
  ⑤ requirements.txt 变化能被检出        ⑥ check() 不动工作区

**B. auto_update() 的分级与降级**（退出时自动更新的全部判断）
  ① auto -> 拉   ② 没写 -> 默认自动（2026-10-13 起）   ③ 显式 manual -> 拒绝
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

# ⚠️ **2026-09-30 起「脏」只算改过的跟踪文件**（未跟踪的不再挡路 —— 见
#    `update._dirty_report()` 那张实测表）→ 夹具必须改一个**仓库里已有的**文件，
#    否则这条测的就不是「脏」了。
(clone / "requirements.txt").write_text("numpy>=2.0\n# 我改的\n", encoding="utf-8")
c = update.check()
check("A6 check() 不动工作区（只报 dirty）", c["ok"] and c["dirty"],
      f"ok={c['ok']} dirty={c['dirty']}")

print("\n--- A2 脏工作区 ---")
tmp, seed, clone = new_world()
release(seed, "0.0.2")
(clone / "requirements.txt").write_text("numpy>=2.0\n# 别丢我\n", encoding="utf-8")
before_v, before_files = (clone / "VERSION").read_text().strip(), sorted(p.name for p in clone.iterdir())
r = update.pull()
after_v, after_files = (clone / "VERSION").read_text().strip(), sorted(p.name for p in clone.iterdir())
check("A2 脏工作区 -> 停手且不动任何文件",
      (not r["ok"]) and r.get("blocked") and before_v == after_v and before_files == after_files,
      f"blocked={r.get('blocked')} VERSION {before_v}->{after_v} 文件数 {len(before_files)}->{len(after_files)}")
check("⭐⭐ 而且我改的那一行**逐字还在**（停手 = 真没动）",
      (clone / "requirements.txt").read_text() == "numpy>=2.0\n# 别丢我\n",
      repr((clone / "requirements.txt").read_text()))

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
        # ⚠️ 同 A2：改的是**仓库里已有的**文件（2026-09-30 起未跟踪不算脏）。
        (clone / "requirements.txt").write_text("numpy>=2.0\n# 别丢我\n", encoding="utf-8")
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
# 2026-10-13 Owen 拍板：默认 auto，只有显式 manual 才拦（`changelog.update_mode`）。
auto_case("⭐ B2 没写「更新方式」-> 默认自动拉", [("0.0.2", None)], True)
auto_case("B2b 跨版本：一个没写 + 一个 manual -> 拒绝", [("0.0.2", None), ("0.0.3", "manual")], False)
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
(clone / "requirements.txt").write_text("numpy>=2.0\n# 我改的\n", encoding="utf-8")
sh("git", "checkout", "main", cwd=clone)
_msgs["工作区脏"] = update.pull().get("user_msg", "")
check("⚠️ 四种停手各有各的话（并成一句 -> 用户会去做错的事）",
      len(set(_msgs.values())) == 4 and all(_msgs.values()),
      f"{ {k: v[:24] for k, v in _msgs.items()} }")

print("--- C6 ⚠️ 停手那句必须**点名文件**（只说「有改动」害人卡了两周）---")
# ⚠️ 2026-10-07 实测：朋友那台被一个**构建产物**挡住，而卡片/日志都只说"你有自己
#    改过的内容"，不给文件名 → 他卡了两周，最后靠手动 `git status` 截图才定位到。
#    这条钉住「点名」这件事本身（变异验证：把 `dirty_files` 去掉 -> 这条红）。
tmp, seed, clone = new_world()
(clone / "requirements.txt").write_text("numpy>=2.0\n# 我改的\n", encoding="utf-8")
_r6 = update.pull()
check("C6 脏停手 -> 卡片那句里出现**具体文件名**（不许只说「有改动」）",
      "requirements.txt" in (_r6.get("user_msg") or "")
      and _r6.get("dirty_files") == ["requirements.txt"],
      f"user_msg={(_r6.get('user_msg') or '')[:70]!r} dirty_files={_r6.get('dirty_files')}")
print()

print("--- C7 ⚠️ 中文名文件必须**原样**点名（git 默认会 C 转义）---")
# ⚠️ 2026-10-07 OCR 审查指出 + 实测：`git status --porcelain` 默认会把「不常见」
#    字符 C 转义 —— 中文名输出成 `"\346\226\207.txt"`。而「点名」这个功能的全部
#    价值就是名字要**一眼能认**，转义串等于没点名。修法是加 `core.quotePath=false`。
#    变异验证：把 `_dirty_report()` 里那个 `-c core.quotePath=false` 去掉 -> 这条红。
tmp, seed, clone = new_world()
(clone / "中文 名.txt").write_text("x\n", encoding="utf-8")
sh("git", "add", "-A", cwd=clone)
sh("git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "cn", cwd=clone)
(clone / "中文 名.txt").write_text("y\n", encoding="utf-8")
_r7 = update.pull()
check("C7 中文名文件 -> 点名里不许出现 \\346 这类 C 转义",
      "中文 名.txt" in (_r7.get("user_msg") or "")
      and _r7.get("dirty_files") == ["中文 名.txt"],
      f"user_msg={(_r7.get('user_msg') or '')[:80]!r} dirty_files={_r7.get('dirty_files')}")
print()

print("--- C8 `_dirty_paths` 的纯解析（重命名 / 引号 / 前导空格被吞）---")
# ⚠️ 这几条是 2026-10-07 OCR 两轮抓出来的形状（先切分还是先剥引号、` -> ` 判据太宽、
#    `_git()` 会 strip 掉首行的前导空格）。纯函数入参=porcelain 原文，不需要 git 夹具。
check("C8 两条路径各自带引号的重命名 -> 取到干净的新名",
      update._dirty_paths('R  "old name" -> "new name"') == ["new name"],
      str(update._dirty_paths('R  "old name" -> "new name"')))
check("C8b 无空格重命名 -> 取新名",
      update._dirty_paths("R  a -> b") == ["b"], str(update._dirty_paths("R  a -> b")))
check("C8c 名字里本来就带 ` -> ` 的普通改动 -> **不切**",
      update._dirty_paths(" M notes -> final.md") == ["notes -> final.md"],
      str(update._dirty_paths(" M notes -> final.md")))
check("C8d 首行被 strip 掉前导空格 -> 仍取对（不许切出 quirements.txt）",
      update._dirty_paths("M requirements.txt") == ["requirements.txt"],
      str(update._dirty_paths("M requirements.txt")))
print()

print("--- D1 `classify_dirt` 四档；有用户改动时**整个不动** ---")
# ⚠️ 判据来源（2026-10-07）：那一周的自锁暴露出「脏了就没有任何一步会自修」这个更一般的
#    问题。修法照 Homebrew 的 `brew update-reset`（专用修复入口、绝不自动做），但**更保守**：
#    只恢复**能证明是产物**的那些。`generated` 的名单**不手写** —— 用 `git check-ignore`
#    读 `.gitignore`（作者声明的"这是产物"），手写清单会腐坏。
tmp, seed, clone = new_world()
(clone / ".gitignore").write_text("out/\n", encoding="utf-8")
(clone / "out").mkdir()
(clone / "out" / "gen.png").write_bytes(b"v1")
(clone / "tool.sh").write_text("#!/bin/sh\n", encoding="utf-8")
(clone / "tool.sh").chmod(0o755)
(clone / "crlf.txt").write_text("a\nb\n", encoding="utf-8")
sh("git", "add", "-A", cwd=clone)
sh("git", "add", "-f", "out/gen.png", cwd=clone)          # 产物也入库 = 那次自锁的形状
sh("git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "base", cwd=clone)
(clone / "out" / "gen.png").write_bytes(b"v2")            # ① 产物被重写
(clone / "tool.sh").chmod(0o644)                          # ② 可执行位丢了
(clone / "crlf.txt").write_bytes(b"a\r\nb\r\n")           # ③ 只有行尾变了
(clone / "requirements.txt").write_text("numpy>=2.0\n# 我改的\n", encoding="utf-8")   # ④
_kinds = {i["path"]: i["kind"] for i in update.classify_dirt()}
check("D1 四档归类正确（产物 / 权限位 / 行尾 / 用户内容）",
      _kinds == {"out/gen.png": "generated", "tool.sh": "mode",
                 "crlf.txt": "eol", "requirements.txt": "content"},
      str(_kinds))
_r = update.repair_generated(apply=True)
check("D1b 有用户改动 -> **整个不动**（只报不改，`ok=False`）",
      _r["applied"] == [] and bool(_r["refused"]) and not _r["ok"],
      f"applied={_r['applied']} refused={[i['path'] for i in _r['refused']]} ok={_r['ok']}")
check("D1c 拒绝时产物原样还在（没被偷偷恢复）",
      (clone / "out" / "gen.png").read_bytes() == b"v2",
      str((clone / "out" / "gen.png").read_bytes()))
print()

print("--- D2 只有产物/权限位 -> 一键恢复，且**先备份** ---")
tmp, seed, clone = new_world()
(clone / ".gitignore").write_text("out/\n", encoding="utf-8")
(clone / "out").mkdir()
(clone / "out" / "gen.png").write_bytes(b"v1")
(clone / "tool.sh").write_text("#!/bin/sh\n", encoding="utf-8")
(clone / "tool.sh").chmod(0o755)
sh("git", "add", "-A", cwd=clone)
sh("git", "add", "-f", "out/gen.png", cwd=clone)
sh("git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "base", cwd=clone)
(clone / "out" / "gen.png").write_bytes(b"v2")
(clone / "tool.sh").chmod(0o644)
_dry = update.repair_generated()                          # 预览
check("D2 预览模式**一个字节都不动**",
      _dry["applied"] == [] and (clone / "out" / "gen.png").read_bytes() == b"v2",
      f"applied={_dry['applied']}")
_ap = update.repair_generated(apply=True)
check("D2b 恢复后工作区**真的干净了**",
      update._dirty_report().strip() == "" and len(_ap["applied"]) == 2,
      f"dirty={update._dirty_report()!r} applied={_ap['applied']}")
_bk2 = pathlib.Path(_ap["backup_dir"]) / "out" / "gen.png"
check("D2c 恢复前**先备份**，且**保留目录结构**（同名产物不许互相覆盖）",
      bool(_ap["backup_dir"]) and _bk2.is_file() and _bk2.read_bytes() == b"v2",
      f"{_ap['backup_dir']} 下有 out/gen.png: {_bk2.is_file()}")
check("D2d 可执行位真的回来了",
      (clone / "tool.sh").stat().st_mode & 0o111 != 0,
      oct((clone / "tool.sh").stat().st_mode))
print()

print("--- D3 ⚠️ **已暂存**的产物改动也要修干净（`checkout --` 修不掉它）---")
# ⚠️ 2026-10-07 OCR 指出：`git checkout -- <path>` **只从索引**恢复工作区 —— 脏来自
#    **已暂存**的改动时，恢复完「工作区 == 索引」而索引仍 ≠ HEAD，树照旧脏，而代码
#    却把它记成 applied/ok。修法是 `git checkout HEAD --`。变异验证：去掉 HEAD -> 这条红。
tmp, seed, clone = new_world()
(clone / ".gitignore").write_text("out/\n", encoding="utf-8")
(clone / "out").mkdir()
(clone / "out" / "gen.png").write_bytes(b"v1")
sh("git", "add", "-A", cwd=clone)
sh("git", "add", "-f", "out/gen.png", cwd=clone)
sh("git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "base", cwd=clone)
(clone / "out" / "gen.png").write_bytes(b"v2")
sh("git", "add", "-f", "out/gen.png", cwd=clone)          # 关键：**暂存**起来（被忽略的路径要 -f）
check("D3 前置：暂存的改动确实被算作脏",
      update._dirty_report().strip() != "", repr(update._dirty_report()))
_r3 = update.repair_generated(apply=True)
check("D3 ⚠️ 已暂存的产物改动 -> 恢复后树**真的**干净（判据用 `git status` 口径）",
      update._dirty_report().strip() == "" and _r3["applied"] == ["out/gen.png"],
      f"dirty={update._dirty_report()!r} applied={_r3['applied']} ok={_r3['ok']}")
print()

print("--- D4 ⚠️ 两个**同名**产物：备份不许互相覆盖 ---")
# ⚠️ 2026-10-07 独立复核指出：原来备份只取 `basename`，`a/out/gen.png` 与 `b/out/gen.png`
#    会写进备份目录的**同一个文件** → 先备份的那份被覆盖，而 `applied` 列两个、`ok=True`
#    —— docstring 承诺的"先备份"当场落空。变异：退回 `.name` -> 这条红。
tmp, seed, clone = new_world()
(clone / ".gitignore").write_text("out/\n")
for _d in ("a", "b"):
    (clone / _d / "out").mkdir(parents=True)
    (clone / _d / "out" / "gen.png").write_bytes(b"v1")
sh("git", "add", "-A", cwd=clone)
sh("git", "add", "-f", "a/out/gen.png", "b/out/gen.png", cwd=clone)
sh("git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "base", cwd=clone)
(clone / "a" / "out" / "gen.png").write_bytes(b"AAA")
(clone / "b" / "out" / "gen.png").write_bytes(b"BBB")
_r4 = update.repair_generated(apply=True)
_bk = pathlib.Path(_r4["backup_dir"])
_ok_a = (_bk / "a" / "out" / "gen.png")
_ok_b = (_bk / "b" / "out" / "gen.png")
check("D4 两份原件**各留一份**（含目录结构），不是只剩一个",
      _ok_a.is_file() and _ok_b.is_file()
      and _ok_a.read_bytes() == b"AAA" and _ok_b.read_bytes() == b"BBB",
      f"a={_ok_a.is_file()} b={_ok_b.is_file()} dir={_r4['backup_dir']}")
print()

print("--- D5 ⚠️ 名字含空格的 mode-only 改动不许被误判成 content ---")
# ⚠️ 2026-10-07 独立复核指出 + 实测：`git diff --summary` 出的是
#    ` mode change 100755 => 100644 my tool.sh`，原来取 `split()[-1]` 得到 `tool.sh`
#    → 与 porcelain 的真路径对不上 → 判成 `content` → **拒绝更新还谎称"内容被改过"**。
#    变异：退回 `split()[-1]` -> 这条红。
tmp, seed, clone = new_world()
(clone / "my tool.sh").write_text("#!/bin/sh\n", encoding="utf-8")
(clone / "my tool.sh").chmod(0o755)
sh("git", "add", "-A", cwd=clone)
sh("git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "t", cwd=clone)
(clone / "my tool.sh").chmod(0o644)
_k5 = {i["path"]: i["kind"] for i in update.classify_dirt()}
check("D5 含空格的 mode-only → `mode`（不是 `content`）",
      _k5.get("my tool.sh") == "mode", str(_k5))
print()

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

# ======================= D. 重建 .app（图标那条路）=======================
# ⭐ 2026-09-30 加。作者的原话：「要让新用户有新图标才行，这个也想办法更新到新图标」。
#    `.app` 里的图标 / Info.plist 版本号 / 启动器**只有 `make-app.sh` 会写**，
#    而 `cl update` 只拉代码 —— 所以「重建」必须是更新流程里的一步，
#    否则用户代码更新了、Dock 上还是旧图。
print("\nD. 重建 .app（图标 / 版本号）\n")

_STUB = """#!/bin/bash
# 桩：`--up-to-date` 时有 STALE 就报「该重建」（非零），否则 0。
# 不带参数（= 真构建）时记一笔到 CALLS，然后按 FAIL 决定成败。
if [ "${1:-}" = "--up-to-date" ]; then
  [ -f STALE ] && { echo "桩：构建输入变了"; exit 1; }
  exit 0
fi
echo build >> CALLS
[ -f FAIL ] && exit 1
exit 0
"""


def _app_world():
    """一个带 `make-app.sh` 桩的沙盒。返回 (tmp, clone)。"""
    tmp, seed, clone = new_world()
    mk = clone / "make-app.sh"
    mk.write_text(_STUB, encoding="utf-8")
    mk.chmod(0o755)
    return tmp, clone


def _count(clone) -> int:
    """`make-app.sh` 桩被「真构建」调用了几次（它每跑一次往 CALLS 里记一笔）。"""
    p = clone / "CALLS"
    return len(p.read_text(encoding="utf-8").split()) if p.exists() else 0


def _app_pending() -> list:
    return [s["key"] for s in update.pending_steps()]


tmp, clone = _app_world()
check("D1 戳记对得上 -> **不列**「重建 .app」（不然 `cl update` 每次都白等三分钟）",
      "app" not in _app_pending(), str(_app_pending()))
(clone / "STALE").write_text("", encoding="utf-8")
check("⭐⭐ 戳记对不上 -> 列出「重建 .app」（**图标就是这么丢的**那一条）",
      "app" in _app_pending(), str(_app_pending()))
_st = [s for s in update.pending_steps() if s["key"] == "app"]
check("⭐ 而且 why 里带着 `make-app.sh` 给的那句原因（不是我们自己编的）",
      bool(_st) and "构建输入变了" in _st[0]["why"], str(_st))

(clone / "STALE").unlink()
r = update.run_step("app")
check("⭐⭐ `run_step('app')` 在**最新**时直接跳过、不跑构建"
      "（`cl update` 是无条件调它的，所以这一步必须自己幂等）",
      r["ok"] and not (clone / "CALLS").exists(), f"{r} calls={_count(clone)}")
(clone / "STALE").write_text("", encoding="utf-8")
r = update.run_step("app")
check("⭐ 该重建时**真的跑**了 make-app.sh（桩记了一笔）",
      r["ok"] and _count(clone) == 1, f"{r} calls={_count(clone)}")
(clone / "FAIL").write_text("", encoding="utf-8")
r = update.run_step("app")
check("⚠️ 构建失败 -> `ok=False` **且带原因**（不许静默当成功）",
      (not r["ok"]) and bool(r["error"]), f"{r}")

tmp, _seed, clone = new_world()          # 没有 make-app.sh 的那一档
check("⚠️ 没有 `make-app.sh` -> 不列这一步（**判断不了就不该凭空多一步**）",
      "app" not in _app_pending(), str(_app_pending()))

print("--- D2 `cl update` 真的会去跑那一步（沙盒里真跑 cl，假解释器）---")
import shutil as _sh                                             # noqa: E402
import subprocess as _sp                                         # noqa: E402

_sand = ROOT / "sandbox_upd"
(_sand / "ClassLive.app" / "Contents" / "MacOS").mkdir(parents=True, exist_ok=True)
_sh.copy2(pathlib.Path(update.__file__).resolve().parent / "cl", _sand / "cl")
_fake = _sand / "ClassLive.app" / "Contents" / "MacOS" / "python"
_fake.write_text('#!/bin/sh\necho "FAKEPY $@"\nexit 0\n', encoding="utf-8")
_fake.chmod(0o755)
_pr = _sp.run(["bash", str(_sand / "cl"), "update"], capture_output=True, text=True,
              timeout=60, env={"PATH": "/usr/bin:/bin", "HOME": "/Users/owen"})
_out = _pr.stdout + _pr.stderr
check("⭐⭐ `cl update` 里**真的**出现了 `update.py --run-step app`"
      "（少了它，更新的人永远拿不到新图标）",
      "--run-step app" in _out, _out.strip()[-200:])
check("⚠️ 前置：那一次确实走到了收尾（否则上一条可能只是空转）",
      "FAKEPY doctor.py" in _out, _out.strip()[-200:])

print("--- D2b ⭐⭐ 重建失败 -> `cl update` 的退出码必须非 0（HANDBOOK §10）---")
# ⚠️ HANDBOOK §10 记的另一笔账：`cl update` 里「重建 .app」那一步失败时，
#    那句 `|| echo "⚠ …"` 把非零吞掉了，而函数最后一句话是 `echo` → 退出码恒 0。
#    症状：脚本化调用（CI / 别的脚本）读不出「代码更新了、图标/启动器没生效」。
#    判据在沙盒里**真跑 `cl`**（假解释器），绝不碰真的 ClassLive.app。
_sand3 = ROOT / "sandbox_appfail"
(_sand3 / "ClassLive.app" / "Contents" / "MacOS").mkdir(parents=True, exist_ok=True)
(_sand3 / "home").mkdir(parents=True, exist_ok=True)
_sh.copy2(pathlib.Path(update.__file__).resolve().parent / "cl", _sand3 / "cl")
_fake3 = _sand3 / "ClassLive.app" / "Contents" / "MacOS" / "python"


def _cl_appfail_run(script: str):
    _fake3.write_text(script, encoding="utf-8")
    _fake3.chmod(0o755)
    return _sp.run(["bash", str(_sand3 / "cl"), "update"], capture_output=True,
                   text=True, timeout=60,
                   env={"PATH": "/usr/bin:/bin", "HOME": str(_sand3 / "home")})


_r_fail = _cl_appfail_run('#!/bin/sh\n'
                          'echo "FAKEPY $@"\n'
                          'case "$*" in *"--run-step app"*) exit 1 ;; esac\n'
                          'exit 0\n')
_fail_out = _r_fail.stdout + _r_fail.stderr
check("⭐⭐ 重建失败 -> `cl update` 非零退出（原来被 `|| echo` 吞成 0）",
      _r_fail.returncode != 0, f"rc={_r_fail.returncode}")
check("⭐ 而且原因照说、doctor 照跑（收尾不许因为失败被跳过）",
      "没重建成功" in _fail_out and "FAKEPY doctor.py" in _fail_out,
      _fail_out.strip()[-200:])
_r_ok3 = _cl_appfail_run('#!/bin/sh\necho "FAKEPY $@"\nexit 0\n')
check("⚠️ 对照：重建成功 -> 退出码 0（别把「一切正常」也报成失败）",
      _r_ok3.returncode == 0, f"rc={_r_ok3.returncode}")

# ======================= E. 工作区里的「东西」挡不挡更新 =======================
# ⭐ 2026-09-30 加。作者的原话：「有没有办法让朋友那边工作区有东西也能正常更新？」
#    实测四种情形（见 `update._dirty_report()` 那张表）：**未跟踪文件挡不住 `git pull`** ——
#    唯一危险的那一格 git 自己会拦、而且拦得对。所以前置检查**只算改过的跟踪文件**。
print("\nE. 工作区里的东西挡不挡更新\n")

print("--- E1 ⭐⭐ 未跟踪的文件不再挡路 ---")
tmp, seed, clone = new_world()
(clone / "mynote.txt").write_text("我的便签\n", encoding="utf-8")      # 未跟踪
release(seed, "0.0.2")
r = update.pull()
check("⭐⭐ 目录里躺着未跟踪文件 -> **照样更新**（以前整条更新链一步都不跑）",
      r["ok"] and r["after"] == "0.0.2", f"{r['before']}->{r['after']} ok={r['ok']}")
check("⭐ 而且那个文件**一个字节没动**", (clone / "mynote.txt").read_text() == "我的便签\n")
check("⚠️ `check()['dirty']` 与 `pull()` 同源（未跟踪不算脏）",
      update.check()["dirty"] is False, str(update.check()["dirty"]))

print("--- E2 ⭐ 改过的**跟踪**文件仍然停手（边界①不松）---")
tmp, seed, clone = new_world()
release(seed, "0.0.2")
(clone / "VERSION").write_text("9.9.9-我改的\n", encoding="utf-8")     # 跟踪文件被改
r = update.pull()
check("⭐ 改过仓库里的文件 -> 停手 + blocked（绝不覆盖）",
      (not r["ok"]) and r.get("blocked"), (r.get("error") or "")[:60])
check("⭐ 而且我的改动**逐字还在**", (clone / "VERSION").read_text() == "9.9.9-我改的\n")
check("⚠️ 这一档 `check()['dirty']` 必须是 True（卡片要靠它说人话）",
      update.check()["dirty"] is True, str(update.check()["dirty"]))

print("--- E3 ⭐⭐ 唯一危险的那一格：远端要建**同名**文件 ---")
# 安全网交给 git —— 但要钉住「它真的拦住了，而且我的文件没被覆盖、话也没说错」。
tmp, seed, clone = new_world()
(clone / "mynote.txt").write_text("我的便签\n", encoding="utf-8")
(seed / "mynote.txt").write_text("origin 的版本\n", encoding="utf-8")
commit(seed, "origin: 新建 mynote.txt")
sh("git", "push", cwd=seed)
r = update.pull()
check("⭐⭐ git 自己拒绝了这一格 —— 更新没成，**但什么都没丢**",
      (not r["ok"]) and (clone / "mynote.txt").read_text() == "我的便签\n",
      f"我的文件={(clone / 'mynote.txt').read_text()!r} ok={r['ok']}")
check("⭐⭐ 而且**不许说成「网络不通」**（误诊 —— 用户会去重连 WiFi）",
      bool(r.get("blocked")) and "网络" not in (r.get("user_msg") or ""),
      f"blocked={r.get('blocked')} user_msg={(r.get('user_msg') or '')[:60]!r}")

# ======================= F. 「补依赖」的戳记 =======================
# ⭐ 2026-09-30 加。作者的朋友实测：从没跑过 `cl update` 的机器上，
#    更新卡片永远显示「补依赖 · 约 1–3 分钟」，而点了它**永远做不成**——
#    终端里那个确认框弹不出来（`notice._can_alert()` 只在 .app 里为真）→
#    默默按「先不做」→ 卡上写「再点一次这个按钮」→ 再点一次还是这样。
#    **死循环。**
# 根因两条：① `make-app.sh`（`./install.sh` 那条）装了依赖但**从不写戳记** ——
#    全仓只有 `cl:145` 写，所以走 install.sh 装出来的机器**永远**认为依赖没装；
#    ② 戳记路径原来是个模块级常量，**测试的沙盒换不掉它**（见 F2）。
print("\nF. 「补依赖」的戳记\n")

print("--- F1 ⭐⭐ 装了依赖 -> 戳记写下 -> `pending_steps()` 不再喊「补依赖」---")
tmp, seed, clone = new_world()
(clone / "requirements.txt").write_text("numpy>=9.9\n", encoding="utf-8")
check("F1 前置：requirements 指纹对不上 -> 确实列了「补依赖」",
      [s["key"] for s in update.pending_steps()].count("deps") == 1,
      str([s["key"] for s in update.pending_steps()]))
update.mark_reqs_installed()
check("⭐⭐ 记下之后 -> 「补依赖」**消失**（这就是朋友那个死循环的解药）",
      "deps" not in [s["key"] for s in update.pending_steps()],
      str([s["key"] for s in update.pending_steps()]))

print("--- F2 ⭐⭐ 戳记路径必须跟着 `STATE_DIR` 走（沙盒隔离的写端）---")
# ⚠️ 原来写的是 `REQS_STAMP = STATE_DIR / "reqs-installed"` —— **import 那一刻就绑死**，
#    而 `new_world()` 换的是 `update.STATE_DIR` → 常量不跟着走 →
#    沙盒里跑一次 `--mark-reqs` 会写进**用户真正的** `~/Library/Logs/ClassLive/`，
#    而且写进去的是**沙盒那份 requirements 的指纹** → 真机上从此永远显示「补依赖」。
#    正是 CLAUDE.md 那条「测试必须隔离写端」的形状（只换读端、没换写端）。
check("⭐⭐ 换 `STATE_DIR` 之后，戳记路径跟着换（不跟 = 会写坏真机器）",
      str(update._reqs_stamp()).startswith(str(tmp)),
      f"STATE_DIR={update.STATE_DIR} 戳记={update._reqs_stamp()}")

print("--- F3 ⭐⭐ `make-app.sh` 要写戳记，而且必须在**构建成功之后** ---")
# 判据是**位置**不是「这个字符串出现过」：
#   ① `--mark-reqs` 必须排在 `uv pip install … requirements.txt` **之后**
#      （排前面 = 还没装就记「已装」）；
#   ② 必须排在 `fingerprint > "$STAMP"` **之后** —— 那是脚本自己标的成功点
#      （「只有走到这里才算构建成功」）。排前面的话，中途失败时 ③ 的 trap
#      会把**旧** .app 移回来，而那份装的可能是旧依赖 → **戳记在说谎**。
_mk_src = (pathlib.Path(update.__file__).resolve().parent / "make-app.sh").read_text(
    encoding="utf-8").splitlines()


def _line_of(needle: str) -> int:
    """第一个**非注释**行里含 needle 的行号（0 起）。找不到 -1。

    ⚠️ 跳过注释行是必须的 —— 上面那段说明文字里就有 `--mark-reqs` 这个词，
       不跳的话这条判据会被**自己的注释**喂饱（本仓栽过一次，见
       `test_audit_regressions` 的 R16）。
    """
    for i, ln in enumerate(_mk_src):
        if needle in ln and not ln.lstrip().startswith("#"):
            return i
    return -1


_i_install = _line_of("-r \"$HERE/requirements.txt\"")
_i_stamp = _line_of('fingerprint > "$STAMP"')
_i_mark = _line_of("--mark-reqs")
check("⭐⭐ `make-app.sh` 真的会写戳记（少了它，install.sh 装出来的机器永远喊「补依赖」）",
      _i_mark > 0, f"install@{_i_install} stamp@{_i_stamp} mark@{_i_mark}")
check("⭐⭐ 而且排在**装依赖之后**（排前面 = 还没装就记「已装」）",
      _i_install > 0 and _i_mark > _i_install,
      f"install@{_i_install} mark@{_i_mark}")
check("⭐⭐ 而且排在**构建成功点之后**（构建失败时旧 .app 会被移回来，那时记就是撒谎）",
      _i_stamp > 0 and _i_mark > _i_stamp,
      f"stamp@{_i_stamp} mark@{_i_mark}")

# ======================= G. `cl update` 真的会补依赖 =======================
# （G1 原来驱动的是悬浮窗卡片上的「立即更新」—— 2026-10-12 那颗按钮删了，
#   更新入口挪到开课前面板，判据见 D 组 `offer` / `apply`。）
print("\nG. cl update 真的会补依赖\n")

print("--- G2 ⭐⭐ `cl update` 真的能把那句兑现（判据与卡片同源）---")
# ⚠️ 上面那条话术成立的前提是：`cl update` **真的会去装**。而它原来比的是
#    「**这次 pull 前后** requirements.txt 变没变」—— **第二份判据**，比戳记窄：
#    戳记丢了、清单又没变的机器它永远说"不用装"。→ 改成问 `--steps`。
_cl_src = (pathlib.Path(update.__file__).resolve().parent / "cl").read_text(
    encoding="utf-8")
# ⚠️ 钉**位置**不钉「整个文件里有没有」（2026-10-01 独立审核指出）：
#    `--steps` 必须和 `grep` 出现在**同一行**（那才是那条判据本身）。
#    单独在哪出现一次不算数 —— 判定「有没有这个字符串」的判据，
#    被挪去注释里也照样绿。真被整行注释掉的话由下面 G2 的沙盒实跑兜底。
_c_same_line = any("--steps" in L and "grep" in L for L in _cl_src.splitlines())
check("⭐⭐ `cl update` 问的是 `update.py --steps`（判据与卡片同源），"
      "不是自己算的 shasum 差",
      _c_same_line and "_req_before" not in _cl_src,
      f"same-line={_c_same_line} _req_before={'_req_before' in _cl_src}")

# 真跑一遍：沙盒里放一个**会报 deps 待做**的假解释器，看 cl 有没有走到装依赖。
# ⚠️⚠️ **HOME 也必须是沙盒的**：`cl` 自己会往 PATH 后面补 `$HOME/.local/bin`
#    （那是给双击启动补 `uv` 的）—— 用真 HOME 的话 `uv` 会被找到，
#    于是**真去联网装包**。假 HOME 下找不到 uv → 走 `$PY -m pip` 那条兜底 →
#    仍然是被 FAKEPY 接住。判据只问「有没有走到这一步」，不碰网络。
_sand2 = ROOT / "sandbox_deps"
_sand_home = _sand2 / "home"
(_sand2 / "ClassLive.app" / "Contents" / "MacOS").mkdir(parents=True, exist_ok=True)
_sand_home.mkdir(parents=True, exist_ok=True)
_sh.copy2(pathlib.Path(update.__file__).resolve().parent / "cl", _sand2 / "cl")
(_sand2 / "requirements.txt").write_text("numpy>=2.0\n", encoding="utf-8")
_fake2 = _sand2 / "ClassLive.app" / "Contents" / "MacOS" / "python"
_fake2.write_text(
    '#!/bin/sh\n'
    'case "$*" in\n'
    '  *"--steps"*) printf "deps\\t补依赖\\t约 1–3 分钟\\n"; exit 0 ;;\n'
    'esac\n'
    'echo "FAKEPY $@"\nexit 0\n', encoding="utf-8")
_fake2.chmod(0o755)


def _cl_run(sand_home) -> str:
    r = _sp.run(["bash", str(_sand2 / "cl"), "update"], capture_output=True, text=True,
                timeout=60, env={"PATH": "/usr/bin:/bin", "HOME": str(sand_home)})
    return r.stdout + r.stderr


_out2 = _cl_run(_sand_home)
check("⭐⭐ 报了 deps -> `cl update` **真的去装了**（并写了戳记）",
      "FAKEPY update.py --mark-reqs" in _out2, _out2.strip()[-260:])
check("⚠️ 前置：那次也确实重建了 .app（否则上一条可能只是空转）",
      "FAKEPY update.py --run-step app" in _out2, _out2.strip()[-260:])

# 对照：没报 deps 时**不许**装（别每次 cl update 都白等一次 uv）
_fake2.write_text('#!/bin/sh\necho "FAKEPY $@"\nexit 0\n', encoding="utf-8")
_fake2.chmod(0o755)
_out3 = _cl_run(_sand_home)
check("⚠️ 对照：没报 deps -> **不装**（不许每次 cl update 都白等一次）",
      "FAKEPY update.py --mark-reqs" not in _out3, _out3.strip()[-260:])

# ============== B13 ⭐ 构建指纹**不该**被 VERSION 触发（2026-10-04）==============
# 为什么单列：`VERSION` **每版都变**，而它唯一的落点（Info.plist 的
# `CFBundleShortVersionString`）**全仓没有一个代码读者** —— 只有 Finder「显示简介」
# 看得到。它留在指纹里 = **每发一版，所有用户白重建 1–3 分钟**，而重建正是那条
# 「可能打断正在录的课」的危险路径（3.8.7 才修过）。
#
# ⚠️ 判据在**真脚本**上跑（沙盒里放真的 `make-app.sh` + `tools/make_icon.py`），
#    而且**用 `--fingerprint` 取指纹、不在这儿重算一遍 sha256** —— 本仓规矩：
#    别在两处各算一遍指纹，两处记一次迟早漂（在 Python 里再写一遍就是第二个定义点）。
print("\n--- B13 构建指纹不该被 VERSION 触发 ---")

_sb = ROOT / "appstale"
(_sb / "tools").mkdir(parents=True)
_real_root = pathlib.Path(update.__file__).resolve().parent


def _app_script(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(_sb / "make-app.sh"), *args],
                          capture_output=True, text=True)


def _fp() -> str:
    return _app_script("--fingerprint").stdout.strip()


def _stale() -> bool:
    """真脚本的判据：True = 该重建（退出码非 0）。"""
    return _app_script("--up-to-date").returncode != 0


for _f in ("make-app.sh", "tools/make_icon.py"):
    shutil.copy2(_real_root / _f, _sb / _f)
(_sb / "ClassLive.app" / "Contents").mkdir(parents=True)
(_sb / "VERSION").write_text("3.8.8\n", encoding="utf-8")
(_sb / "ClassLive.app" / "Contents" / ".build-stamp").write_text(_fp(), encoding="utf-8")

check("⚠️ 前置：戳记与当前指纹一致 -> 不算 stale（否则下面几条都是空转）",
      not _stale(), f"戳记={_fp()} 却报了 stale")

(_sb / "VERSION").write_text("3.9.0\n", encoding="utf-8")
check("⭐⭐ bump 了 VERSION -> **仍然不重建**（本次要的正是这个）",
      not _stale(), "VERSION 一变就又要重建了")

(_sb / "make-app.sh").write_text(
    (_sb / "make-app.sh").read_text(encoding="utf-8") + "\n# 结构改动\n", encoding="utf-8")
check("⚠️ 对照：改 `make-app.sh`（结构那一类）-> **必须**还得重建",
      _stale(), "结构变了却不重建 —— 图标就是这么丢的")

(_sb / "tools" / "make_icon.py").write_text(
    (_sb / "tools" / "make_icon.py").read_text(encoding="utf-8") + "\n# 换图\n",
    encoding="utf-8")
check("⚠️ 对照：改 `tools/make_icon.py` -> 还得重建（图标那半不能再丢一次）",
      _stale(), "图标源码改了却不重建")

# ============== H. `make-app.sh --check` 的退出码（2026-10-18）==============
# HANDBOOK §10 记的账：`--check` **恒返回 0**（即使它自己刚打了 ❌）——
# 与 `install.sh --check`（早就 `exit $_bad`）不一致，脚本化调用读不出「缺件」。
# 判据在**真脚本**上跑（沙盒里放真的 `make-app.sh` + `tools/make_icon.py`），
# 而且**一条都不许碰真 `ClassLive.app`**。
print("\n--- H. make-app.sh --check 的退出码 ---")

_chk = ROOT / "checkapp"
(_chk / "tools").mkdir(parents=True)
for _f in ("make-app.sh", "tools/make_icon.py"):
    shutil.copy2(_real_root / _f, _chk / _f)


def _chk_run() -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(_chk / "make-app.sh"), "--check"],
                          capture_output=True, text=True)


def _chk_fp() -> str:
    return subprocess.run(["bash", str(_chk / "make-app.sh"), "--fingerprint"],
                          capture_output=True, text=True).stdout.strip()


_r = _chk_run()
check("⚪ .app 不存在 -> 退出码 0（⚪ 不是问题，别把「没构建」说成缺件）",
      _r.returncode == 0 and "⚪" in _r.stdout and "❌" not in _r.stdout,
      f"rc={_r.returncode} out={_r.stdout.strip()[-80:]!r}")

_appd = _chk / "ClassLive.app"
(_appd / "Contents").mkdir(parents=True)
_r = _chk_run()
_bad_n = _r.stdout.count("❌")
check("⭐⭐ .app 缺件 -> 退出码 1（原来恒 0 —— 这条就是那笔账）",
      _r.returncode == 1, f"rc={_r.returncode} 打了 {_bad_n} 条 ❌")
check("⭐⭐ 而且**报全**、不是见一个就停（≥2 条 ❌ 才够「report everything」）",
      _bad_n >= 2, f"只打了 {_bad_n} 条 ❌")
check("⚠️ 报完之后还说了「需要处理」（别让人对着退出码猜）",
      "需要处理" in _r.stdout, _r.stdout.strip()[-60:])

# 全就位那一档：MacOS/python 真文件 + lib/ + pyvenv.cfg + Info.plist + 戳记 + 图标
(_appd / "Contents" / "MacOS").mkdir(exist_ok=True)
(_appd / "Contents" / "lib").mkdir(exist_ok=True)
(_appd / "Contents" / "Resources").mkdir(exist_ok=True)
(_appd / "Contents" / "pyvenv.cfg").write_text("home = /x\n", encoding="utf-8")
(_appd / "Contents" / "Info.plist").write_text(
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
    '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
    '<plist version="1.0"><dict>\n'
    '  <key>CFBundleExecutable</key><string>python</string>\n'
    '  <key>CFBundleIconFile</key><string>AppIcon</string>\n'
    '</dict></plist>\n', encoding="utf-8")
(_appd / "Contents" / "Resources" / "AppIcon.icns").write_bytes(b"icns")
_py = _appd / "Contents" / "MacOS" / "python"
_py.write_text('#!/bin/sh\necho "stub-python"\n', encoding="utf-8")
_py.chmod(0o755)
(_appd / "Contents" / ".build-stamp").write_text(_chk_fp(), encoding="utf-8")
_r = _chk_run()
check("✅ 全就位（含戳记 / 图标）-> 退出码 0、一条 ❌ 都没有",
      _r.returncode == 0 and "❌" not in _r.stdout,
      f"rc={_r.returncode} out={_r.stdout.strip()[-120:]!r}")

# ⚠️ 只有「符号链接」那一条时**仍是 0**：它是 ⚠️ 不是 ❌（判据只认 ❌）。
_real_py = _appd / "Contents" / "real_python"
_real_py.write_text('#!/bin/sh\necho "stub-python"\n', encoding="utf-8")
_real_py.chmod(0o755)
_py.unlink()
_py.symlink_to(_real_py)
_r = _chk_run()
check("⚠️ 只有符号链接这一条 -> ⚠️ 不算问题，退出码仍是 0",
      _r.returncode == 0 and "⚠️" in _r.stdout,
      f"rc={_r.returncode} 有⚠️={'⚠️' in _r.stdout}")

# ============== B14 ⭐ 只缺**可选**模型 -> 卡片不列「下载模型」==============
# 2026-10-11 外部审查抓出：`pending_steps` 没按 `required` 过滤 —— 只缺可选的 Whisper / Qwen 时
# 卡片写「缺模型的话课上会直接起不来」，而 `run_step("models")` 又跳过可选的、什么都不下 →
# 卡片永远消不掉。判据只看「models 这一步在不在」，不钉文案。
print("\n--- B14 可选模型不进更新卡片 ---")
import doctor as _doctor                                                   # noqa: E402
_req = [m for m in _doctor.MODELS if m.required]
_opt = [m for m in _doctor.MODELS if not m.required]
check("⚠️ 前置：模型清单里必装、可选各至少一条（否则下面两条空转）",
      bool(_req) and bool(_opt), str([(m.key, m.required) for m in _doctor.MODELS]))
with _mock.patch.object(_doctor, "model_present", lambda p: any(p == m.path for m in _req)):
    check("⭐⭐ 必装的都在、可选的全缺 -> **不列**「下载模型」",
          "models" not in [s["key"] for s in update.pending_steps()],
          str([s["key"] for s in update.pending_steps()]))
with _mock.patch.object(_doctor, "model_present", lambda p: False):
    check("⚠️ 对照：必装的也缺 -> **必须**列「下载模型」",
          "models" in [s["key"] for s in update.pending_steps()],
          str([s["key"] for s in update.pending_steps()]))

# ============== B15 ⭐ 流式草稿模型：缺了且用得上 -> 卡片列一条 soft 步骤 ==============
# 作者 2026-10-11 拍板：Nemotron 可选但更新卡片主动提（不提的话 .app 用户永远拿不到 R3）。
# 内存不够 / 手动 parakeet 的机器装了也用不上 -> 不提。下不成不算更新失败（soft）。
print("\n--- B15 流式草稿模型进更新卡片 ---")
import os                                                                   # noqa: E402
import draft_engine as _de                                                  # noqa: E402
import models as _models                                                    # noqa: E402
_NEM = _models.NEMOTRON.path
_keys = lambda: [s["key"] for s in update.pending_steps()]                 # noqa: E731
_GB = 2**30
with _mock.patch.object(_doctor, "model_present", lambda p: p != _NEM), \
        _mock.patch.object(_de, "_ram_bytes", lambda: 16 * _GB), \
        _mock.patch.dict(os.environ, {}, clear=False):
    os.environ.pop(_de.ENV, None)
    _st = [s for s in update.pending_steps() if s["key"] == update.DRAFT_MODEL]
    check("⭐⭐ 缺 Nemotron、16 GB、auto -> 列「下载流式草稿模型」且 soft", len(_st) == 1 and _st[0].get("soft") is True,
          str(_keys()))
    check("⭐ 只缺可选的 -> 「下载模型」（必装那条）仍不列", "models" not in _keys(), str(_keys()))
    with _mock.patch.object(_de, "_ram_bytes", lambda: 8 * _GB):
        check("⭐ 8 GB 机器（auto 会选 Parakeet）-> 不提", update.DRAFT_MODEL not in _keys(), str(_keys()))
    with _mock.patch.dict(os.environ, {_de.ENV: "parakeet"}):
        check("⭐ 手动指定 parakeet -> 不提", update.DRAFT_MODEL not in _keys(), str(_keys()))
with _mock.patch.object(_doctor, "model_present", lambda p: True), \
        _mock.patch.object(_de, "_ram_bytes", lambda: 16 * _GB):
    check("⚠️ 对照：Nemotron 已在 -> 不提", update.DRAFT_MODEL not in _keys(), str(_keys()))
_calls = []
# ⚠️ 要把「没装 + 用得上」钉住：不钉的话装过 Nemotron 的机器上 run_step 直接跳过、这条假红（2026-10-13 撞到）。
with _mock.patch.object(update, "download_model", lambda m, say=None: _calls.append(m.key) or {"ok": False, "error": "x"}), \
        _mock.patch.object(_doctor, "model_present", lambda p: False), \
        _mock.patch.object(_de, "_ram_bytes", lambda: 16 * _GB):
    _r = update.run_step(update.DRAFT_MODEL)
check("⭐ run_step(draft_model) 只下 Nemotron、失败原样回报", _calls == ["nemotron"] and _r["ok"] is False, f"{_calls} {_r}")

# ======================= D. 开课前的更新入口（2026-10-12） =======================
# offer() / apply() / defer_to_exit() / finish()。收尾子进程一律桩掉 —— 真跑会往**真的** .app python
# 里 `uv pip install`（`run_step("deps")` 用的是 `sys.executable`），那是写端没隔离。
print("\nD. 开课前的更新入口\n")
_noop_steps = _mock.patch.object(update, "pending_steps", lambda: [])

_tmpD, _seedD, _cloneD = new_world()
with _noop_steps:
    _o = update.offer()
check("D1 已是最新、没欠账 -> none（不显示）", _o["kind"] == "none", str(_o))
with _mock.patch.object(update, "pending_steps",
                        lambda: [{"key": "deps", "label": "补依赖", "detail": "约 1 分钟", "why": ""},
                                 {"key": "draft_model", "label": "x", "detail": "", "why": ""}]):
    _o = update.offer()
check("D2 已是最新但依赖没补 -> unfinished，只说必做的那几件",
      _o["kind"] == "unfinished" and "补依赖" in _o["message"] and "x（" not in _o["message"], str(_o))

release(_seedD, "0.0.2")
with _noop_steps:
    _o = update.offer()
check("⭐ D3 有新版 -> available，带远端看点、最新版本号",
      _o["kind"] == "available" and _o["latest"] == "0.0.2" and "rel 0.0.2" in _o["summary"], str(_o))
check("⭐ D3b offer 只探不拉（本地版本没动）",
      (_cloneD / "VERSION").read_text().strip() == "0.0.1")
(_cloneD / "requirements.txt").write_text("numpy>=2.0\n# 我改的\n", encoding="utf-8")
with _noop_steps:
    _o = update.offer()
check("⭐ D4 用户改过文件 -> blocked，并点名那个文件",
      _o["kind"] == "blocked" and "requirements.txt" in _o["message"], str(_o))
(_cloneD / "requirements.txt").write_text("numpy>=2.0\n", encoding="utf-8")
with _mock.patch.dict(os.environ, {"CLASSLIVE_NO_UPDATE_CHECK": "1"}), _noop_steps:
    _o = update.offer()
check("D5 CLASSLIVE_NO_UPDATE_CHECK -> none", _o["kind"] == "none", str(_o))
sh("git", "checkout", "-q", "-b", "dev", cwd=_cloneD)
sh("git", "push", "-q", "-u", "origin", "dev", cwd=_cloneD)
# 远端 dev 再前进一个提交 -> 这份 dev 落后了（没有开发分支那道守卫的话会报 available）
sh("git", "fetch", "-q", "origin", cwd=_seedD)
sh("git", "checkout", "-q", "-b", "dev", "origin/dev", cwd=_seedD)
commit(_seedD, "dev 上的新提交", version="0.0.2-dev")
sh("git", "push", "-q", "origin", "dev", cwd=_seedD)
sh("git", "checkout", "-q", "main", cwd=_seedD)
with _noop_steps:
    _o = update.offer()
check("⭐ D6 开发分支（作者自己的 worktree）-> none，不天天提示", _o["kind"] == "none", str(_o))
sh("git", "checkout", "-q", "main", cwd=_cloneD)

_fin_calls = []
_fake_fin = lambda **kw: _fin_calls.append(kw) or {"ok": True, "error": ""}         # noqa: E731
with _mock.patch.object(update, "_finish_in_child", _fake_fin):
    _a = update.apply()
check("⭐⭐ D7 apply：拉到新版、pulled=True（调用方据此重开自己）",
      _a["ok"] and _a["pulled"] and (_cloneD / "VERSION").read_text().strip() == "0.0.2", str(_a))
check("⭐ D7b 收尾交给子进程、而且要补必下模型", _fin_calls and _fin_calls[0].get("models") is True, str(_fin_calls))
check("D7c 锁放掉了", not (update.STATE_DIR / "update.lock").exists())

release(_seedD, "0.0.3")
(update.STATE_DIR / "update.lock").write_text("999999", encoding="utf-8")   # 别人正拿着（新鲜）
with _mock.patch.object(update, "_finish_in_child", _fake_fin):
    _a = update.apply()
check("⭐ D8 锁被别人拿着 -> 不拉、说一句人话",
      not _a["ok"] and "另一个更新" in _a["user_msg"]
      and (_cloneD / "VERSION").read_text().strip() == "0.0.2", str(_a))
check("D8b 别人的锁没被删", (update.STATE_DIR / "update.lock").read_text() == "999999")
(update.STATE_DIR / "update.lock").unlink()
with _mock.patch.object(update, "_finish_in_child", lambda **kw: {"ok": False, "error": "依赖没装上"}):
    _a = update.apply()
check("⭐ D9 收尾失败不算更新失败：代码已新、照样重开，并说下次再试",
      _a["ok"] and _a["pulled"] and "下次" in _a["user_msg"], str(_a))

# ---- 下课退出时安装（同意文件）----
_tmpE, _seedE, _cloneE = new_world()
release(_seedE, "0.0.2", "manual")
_stamp = lambda: (update.STATE_DIR / "last_check").unlink(missing_ok=True)       # noqa: E731
with _mock.patch.object(update, "_finish_in_child", _fake_fin):
    _stamp(); update.auto_update()
    check("D10 没同意 -> manual 照旧不自动装", (_cloneE / "VERSION").read_text().strip() == "0.0.1")
    check("D10b defer_to_exit 记上了", update.defer_to_exit("0.0.2"))
    _fin_calls.clear(); _stamp(); update.auto_update()
    check("⭐⭐ D11 同意过 0.0.2 -> 退出时装上", (_cloneE / "VERSION").read_text().strip() == "0.0.2")
    check("⭐ D11b 退出时也补依赖，但**不下模型**", _fin_calls and _fin_calls[0].get("models") is False, str(_fin_calls))
    check("D11c 装到了 -> 同意文件删掉", not (update.STATE_DIR / update.EXIT_CONSENT).exists())
    release(_seedE, "0.0.3", "manual")
    update.defer_to_exit("0.0.2")
    _stamp(); update.auto_update()
    check("⭐ D12 只同意到 0.0.2 -> 更新的 manual 0.0.3 不自动装",
          (_cloneE / "VERSION").read_text().strip() == "0.0.2")

# 两节课间隔不到 1 小时：第 1 节退出时那次检查刚戳过 last_check（manual 跳过），
# 第 2 节课前点了「下课后自动装」—— 第 2 节退出时不能被限频挡掉（2026-10-12 外部审查）。
_tmpF, _seedF, _cloneF = new_world()
release(_seedF, "0.0.2", "manual")
with _mock.patch.object(update, "_finish_in_child", _fake_fin):
    (update.STATE_DIR / update.EXIT_CONSENT).unlink(missing_ok=True)
    _stamp(); update.auto_update()
    check("D16a 前提：第 1 节退出时 manual 没装、last_check 刚戳过",
          (_cloneF / "VERSION").read_text().strip() == "0.0.1" and (update.STATE_DIR / "last_check").exists())
    update.defer_to_exit("0.0.2")
    _r = update.auto_update()
    check("⭐⭐ D16 用户点过「下课后自动装」-> 一小时内的第二次退出也照装（不受限频）",
          (_cloneF / "VERSION").read_text().strip() == "0.0.2", str(_r))
    _r = update.auto_update()
    check("D16b 同意兑现后限频照旧生效", _r.get("reason", "").startswith("限频"), str(_r))

# ---- finish()：只做必做的，`.app` 重建与可选模型绝不在这里 ----
_ran = []
_all_steps = [{"key": k, "label": k, "detail": "", "why": ""} for k in ("deps", "models", "draft_model", "app")]
with _mock.patch.object(update, "pending_steps", lambda: _all_steps), \
        _mock.patch.object(update, "run_step", lambda k, on_line=None: _ran.append(k) or {"ok": True, "error": ""}):
    _lines = []
    _rc = update.finish(models=False, say=_lines.append)
    check("⭐ D13 退出时那条（no-models）只补依赖", _rc == 0 and _ran == ["deps"] and _lines[-1] == "FINISH ok",
          f"{_ran} {_lines[-1:]}")
    _ran.clear()
    update.finish(models=True, say=_lines.append)
    check("⭐⭐ D14 面板那条：依赖 + 必下模型；`.app` 重建与可选模型绝不在这里", _ran == ["deps", "models"], str(_ran))
with _mock.patch.object(update, "pending_steps", lambda: _all_steps), \
        _mock.patch.object(update, "run_step", lambda k, on_line=None: {"ok": False, "error": "网断了"}):
    _lines = []
    _rc = update.finish(models=True, say=_lines.append)
check("D15 失败 -> 返回 1、最后一行 FINISH fail 带原因",
      _rc == 1 and _lines[-1].startswith("FINISH fail") and "网断了" in _lines[-1], str(_lines[-1:]))

# ============ ⭐ 收尾子进程「一行都不吐」时，超时也必须到点 ============
# ⚠️ 根因（2026-10-12 全仓审查，核实过）：原来是 `for line in p.stdout` —— **阻塞**读，
#    `deadline` 只在**读到一行之后**才被检查。子进程一行都不吐地挂住时（网络卡死 /
#    卡在某个不打印的步骤），循环**永久**阻塞调用方（面板那个后台线程 / 退出时的更新进程），
#    `deadline` 和 `p.kill()` **永远轮不到执行** —— 超时形同虚设。
import os as _os
import threading as _th
from unittest import mock as _mock

_r_fd, _w_fd = _os.pipe()          # 只读端：永远没有数据、也永不关闭 = 最坏情况


class _SilentPopen:
    def __init__(self, *a, **kw):
        self.stdout = _os.fdopen(_r_fd, "r", encoding="utf-8")
        self.killed = False

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return -9


_got: dict = {}


def _run_quiet():
    # ⚠️ `_finish_in_child` 的 cmd 写死在函数里，所以只能换 `Popen` 本体 ——
    #    量到的仍是**真函数**里那段读循环。
    with _mock.patch.object(update.subprocess, "Popen", _SilentPopen):
        _got.update(update._finish_in_child(models=False, timeout=1))


_t = _th.Thread(target=_run_quiet, daemon=True)
_t.start()
_t.join(8)
# ⚠️ 用线程 + join 超时**故意**的：改坏时它是**永久阻塞**而不是抛异常 ——
#    直接调会把整个测试文件挂死（那样连一行 ❌ 都看不到）。这里把它变成一条红。
check("⭐⭐ 子进程一行都不吐 -> 到点返回（不是永久阻塞；改坏则这条超时变红）",
      not _t.is_alive() and _got.get("ok") is False and "超时" in str(_got.get("error", "")),
      f"线程还活着={_t.is_alive()} got={_got}")
check("⭐ 超时那条路会 kill 子进程（不留僵尸）",
      isinstance(_got.get("error"), str) and "超时" in _got.get("error", ""), str(_got))

# ============ ⭐ 下课/空闲才下草稿模型（Owen 2026-10-12）============
# ⚠️ 时机是这条的**全部要点**：464 MB 会和云端定稿的音频上传抢同一条教室 Wi-Fi，
#    所以只在下课之后 / 关掉面板之后开，而且 `start_new_session=True`
#    —— 用户下一句就退出程序，它得像自动更新那样在后台下完。
_spawned: list = []


class _FakePopen:
    def __init__(self, cmd, **kw):
        _spawned.append((cmd, kw))


with _mock.patch.object(update.subprocess, "Popen", _FakePopen):
    _ok_spawn = update.spawn_draft_model_download()

check("⭐ `spawn_draft_model_download()` 起来了",
      _ok_spawn and len(_spawned) == 1, f"ok={_ok_spawn} n={len(_spawned)}")
_cmd, _kw = _spawned[0]
# 改坏：把 `start_new_session=True` 去掉 -> 这条红（用户一退出程序它就被带走了）。
check("⭐⭐ `start_new_session=True` —— 退出程序后照样在后台下完（与自动更新同一条纪律）",
      _kw.get("start_new_session") is True, str(sorted(_kw)))
check("⭐ 三个 fd 都不挂在父进程上（父进程走了它也不会写坏管道）",
      _kw.get("stdin") == update.subprocess.DEVNULL
      and _kw.get("stdout") == update.subprocess.DEVNULL
      and _kw.get("stderr") == update.subprocess.DEVNULL,
      f"stdin={_kw.get('stdin')} stdout={_kw.get('stdout')}")
check("⭐ 起的是这一步（「该不该下」留给子进程的 `run_step`，这里不重复判）",
      list(_cmd[-2:]) == ["--run-step", update.DRAFT_MODEL], str(_cmd[-2:]))

# ⭐ `run_step(DRAFT_MODEL)` 必须**幂等** —— 它现在会被上面那条路无条件叫起来。
import doctor as _doctor
import draft_engine as _draft_engine


def _step(present: bool, want: bool) -> dict:
    got: dict = {}

    def _fake_dl(_m, say=None):
        got["dl"] = True
        return {"ok": True, "error": ""}

    with _mock.patch.object(_doctor, "model_present", lambda _p: present), \
         _mock.patch.object(_draft_engine, "want_model", lambda *a, **k: want), \
         _mock.patch.object(update, "download_model", _fake_dl):
        got["r"] = update.run_step(update.DRAFT_MODEL, on_line=lambda _s: None)
    return got


_g = _step(present=True, want=True)
# 改坏：把那句 `if doctor.model_present(...)` 去掉 -> 这条红（每次下课都重下 464 MB）。
check("⭐⭐ 已经装过 -> 跳过，不重下",
      "dl" not in _g and _g["r"]["ok"], str(_g))
_g = _step(present=False, want=False)
# 改坏：把那句 `if not draft_engine.want_model()` 去掉 -> 这条红
#（内存不够 / 手动指定 parakeet 的机器会被下 464 MB 用不上的东西）。
check("⭐⭐ 这台机器用不上（内存不够 / 手动 parakeet）-> 跳过",
      "dl" not in _g and _g["r"]["ok"], str(_g))
_g = _step(present=False, want=True)
check("⭐ 该下的时候**真的下**（别把下载整个弄没了）", _g.get("dl") is True, str(_g))

# ⭐ 接线：两个调用点 + 那道闸
# ⚠️ 这里**不能**用 `update.HERE` —— 本文件把沙盒的 `update.HERE` 重指到了假 clone 上，
#    读出来是沙盒里那份。仓库根从**本文件自己的位置**算。
_REPO = pathlib.Path(__file__).resolve().parent.parent
_src_main = (_REPO / "main.py").read_text(encoding="utf-8")
_src_el = (_REPO / "entry_launch.py").read_text(encoding="utf-8")
# ⚠️ 判的是**先后**，不是"两行紧挨着" —— 中间本来就有注释（第一版钉死换行相邻，假红）。
check("⭐ `main` 在**下课之后**那条路调它（排在 `_auto_update_on_exit()` 之后）",
      _src_main.index("_auto_update_on_exit()\n") < _src_main.index("            _draft_model_on_exit()"))
# 改坏：把 `rc["code"] == CANCELLED` 放宽成"关面板就下" -> 上课途中也会下 464 MB。
check("⭐⭐ `entry_launch` 只在**关掉面板没开课**（CANCELLED）时下 —— 上课那条路绝不下",
      'rc["code"] == CANCELLED' in _src_el and "spawn_draft_model_download()" in _src_el)
check("⭐⭐ 两处都挂 `CLASSLIVE_NO_AUTO_UPDATE` 那道闸（否则跑一次判据就真去下 464 MB）",
      _src_main.count("CLASSLIVE_NO_AUTO_UPDATE") >= 2
      and "CLASSLIVE_NO_AUTO_UPDATE" in _src_el)

# ======================= E. 面板开着时的后台安装 / 退出前先修产物（2026-10-13） =======================
print("\nE. 后台安装与自修\n")

_tmpQ, _seedQ, _cloneQ = new_world()
release(_seedQ, "0.0.2")                                   # 没写更新方式 = auto
with _noop_steps:
    _o = update.offer()
check("⭐⭐ E1 全 auto、依赖和模型清单没动 -> quiet（面板开着就后台装）",
      _o["kind"] == "available" and _o["quiet"] is True, str(_o))
release(_seedQ, "0.0.3", "manual")
with _noop_steps:
    _o = update.offer()
# 改坏：把 `_quiet_ok` 里那句 update_mode 判断去掉 -> 这条红（manual 版被悄悄装上）。
check("⭐⭐ E2 夹一个 manual -> 不 quiet（照旧出「更新」那一格）",
      _o["kind"] == "available" and _o["quiet"] is False, str(_o))

_tmpQ, _seedQ, _cloneQ = new_world()
release(_seedQ, "0.0.2", reqs="numpy>=2.0\nhttpx>=0.28\n")
with _noop_steps:
    _o = update.offer()
# 改坏：把依赖那条比较去掉 -> 这条红（开课前装 1–3 分钟依赖）。
check("⭐⭐ E3 依赖清单变了 -> 不 quiet", _o["kind"] == "available" and _o["quiet"] is False, str(_o))

_tmpQ, _seedQ, _cloneQ = new_world()
(_seedQ / "models.py").write_text("MODELS = []\n", encoding="utf-8")
release(_seedQ, "0.0.2")
with _noop_steps:
    _o = update.offer()
# 改坏：把 `_models_changed` 那条去掉 -> 这条红（可能多一个必下模型，开课才发现缺）。
check("⭐⭐ E4 模型清单动了 -> 不 quiet", _o["kind"] == "available" and _o["quiet"] is False, str(_o))

_tmpQ, _seedQ, _cloneQ = new_world()
release(_seedQ, "0.0.2")
_fin_calls.clear()
with _mock.patch.object(update, "_finish_in_child", _fake_fin):
    _a = update.apply(models=False)
check("⭐ E5 后台安装只补依赖、不下模型（apply(models=False)）",
      _a["ok"] and _fin_calls and _fin_calls[0].get("models") is False
      and (_cloneQ / "VERSION").read_text().strip() == "0.0.2", f"{_a} {_fin_calls}")

# 退出时的更新器碰到「只是构建产物脏了」-> 先恢复（先备份）再拉，与面板「现在更新」同一条路
_tmpR, _seedR, _cloneR = new_world()
(_seedR / ".gitignore").write_text("out/\n", encoding="utf-8")
(_seedR / "out").mkdir()
(_seedR / "out" / "gen.png").write_bytes(b"v1")
sh("git", "add", "-A", cwd=_seedR)
sh("git", "add", "-f", "out/gen.png", cwd=_seedR)
sh("git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "gen", cwd=_seedR)
sh("git", "push", cwd=_seedR)
sh("git", "pull", "-q", cwd=_cloneR)
release(_seedR, "0.0.2", "auto")
(_cloneR / "out" / "gen.png").write_bytes(b"v2")          # 产物被本机重写
with _mock.patch.object(update, "_finish_in_child", _fake_fin):
    (update.STATE_DIR / "last_check").unlink(missing_ok=True)
    _r = update.auto_update()
# 改坏：把 auto_update 里那段 repair 去掉 -> 这条红（停在「工作区脏」）。
check("⭐⭐ E6 只有构建产物脏 -> 退出更新器先恢复再拉",
      (_cloneR / "VERSION").read_text().strip() == "0.0.2", str(_r))
check("E6b 恢复前先备份了", any((update.STATE_DIR / "dirty-backup").rglob("gen.png")))
(_cloneR / "requirements.txt").write_text("numpy>=2.0\n# 我改的\n", encoding="utf-8")
(_cloneR / "out" / "gen.png").write_bytes(b"v3")
release(_seedR, "0.0.3", "auto")
with _mock.patch.object(update, "_finish_in_child", _fake_fin):
    (update.STATE_DIR / "last_check").unlink(missing_ok=True)
    update.auto_update()
check("⭐⭐ E7 有一个用户改过的文件 -> 整个不动（产物也不恢复、不拉）",
      (_cloneR / "VERSION").read_text().strip() == "0.0.2"
      and (_cloneR / "out" / "gen.png").read_bytes() == b"v3")

check("E8 限频五分钟（原来一小时会挡掉「关面板马上又开」那次）", update.RATE_LIMIT_S == 300)
# 改坏：把 entry_launch 里那句 spawn_auto_update() 删掉 -> 这条红（只开面板的人永远轮不到更新）。
check("⭐ E9 关掉面板没开课 -> 也起退出时的自动更新",
      "_u.spawn_auto_update()" in _src_el
      and _src_el.index('rc["code"] == CANCELLED') < _src_el.index("_u.spawn_auto_update()"))

# ======================= F. 换代码被打断（3.10）=======================
# 现场（实测，不是假想）：3000 个文件的一版全量改动、kill 落在 `git pull` 的
# checkout 中段 -> 工作区**半新半旧**（298/3000 已是新版）、HEAD 还指着旧提交、
# `.git/index.lock` 留在那（之后**每一次** pull 都死在 File exists —— 永远装不上）。
# 判据分两条腿：真跑一遍 `pull()`（journal 写没写 / 撤没撤），以及**手工摆出**
# 半换现场再让 `repair_interrupted_apply()` 收尾（现场可摆、不赌 kill 时机）。
print("\nF. 换代码被打断 -> journal + 开机/更新前收尾\n")

import ast                                                        # noqa: E402

_REPO = pathlib.Path(__file__).resolve().parent.parent


def _big_release(seed, ver="0.0.2"):
    """一版动到**好几个文件**的发布（VERSION / CHANGELOG / 两个 .py）。"""
    for n in ("mod.py", "extra.py"):
        (seed / n).write_text(f"# {n} v1\n", encoding="utf-8")
    sh("git", "add", "-A", cwd=seed)
    sh("git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "files",
       cwd=seed)
    sh("git", "push", cwd=seed)
    release(seed, ver)


def _fetch(clone):
    sh("git", "fetch", "--quiet", cwd=clone)


def _sha(clone, ref="HEAD"):
    return sh("git", "rev-parse", ref, cwd=clone)


def _half_swap(clone, seed, names, *, lock=True):
    """把 target 版的部分文件抄进工作区 + 留下被杀 git 的 `index.lock`（现场还原）。"""
    for n in names:
        (clone / n).write_text((seed / n).read_text(encoding="utf-8"), encoding="utf-8")
    if lock:
        (clone / ".git" / "index.lock").write_text("", encoding="utf-8")


# F1 没有 journal -> 什么都不做
_tmpF1, _seedF1, _cloneF1 = new_world()
_r = update.repair_interrupted_apply()
check("F1 没有 journal -> 什么都不做（常驻成本 = 一次 exists()）",
      _r["ok"] and not _r["repaired"] and not _r["action"], str(_r))

# F2 **主路**：半换 + index.lock + journal -> 把那次更新**做完**
_tmpF2, _seedF2, _cloneF2 = new_world()
_big_release(_seedF2)
_fetch(_cloneF2)
_hb, _tg = _sha(_cloneF2), _sha(_cloneF2, "@{u}")
update._write_apply_journal(_hb, _tg)
_half_swap(_cloneF2, _seedF2, ["VERSION", "mod.py"])
_r = update.repair_interrupted_apply()
_clean = sh("git", "status", "--porcelain", cwd=_cloneF2)
# ⚠️ 变异验证：把 `reset --hard target` 那段删掉 -> 这条红（永远停在半换）。
check("⭐⭐ F2 半换 + 陈旧锁 + journal -> `reset --hard` **补完**（HEAD 到 target、树干净）",
      _r["repaired"] and _sha(_cloneF2) == _tg and not _clean
      and (_cloneF2 / "VERSION").read_text().strip() == "0.0.2"
      and (_cloneF2 / "extra.py").exists(), f"{_r} clean={_clean!r}")
# ⚠️ 变异验证：把 `_rm_stale_index_lock()` 删掉 -> 这条红（之后每次 pull 都 File exists）。
check("⭐⭐ F2b 陈旧 `index.lock` 清了（不清 = 以后每次 pull 都死在 File exists）",
      not (_cloneF2 / ".git" / "index.lock").exists())
check("⭐⭐ F2c journal 撤了（不撤 -> 每次启动都重修一遍）",
      not update.apply_journal_path().exists())

# F3 **干净地没换成**（没有 index.lock）-> 工作区**一个字节不动**，只撤 journal
_tmpF3, _seedF3, _cloneF3 = new_world()
_big_release(_seedF3)
_fetch(_cloneF3)
update._write_apply_journal(_sha(_cloneF3), _sha(_cloneF3, "@{u}"))
(_cloneF3 / "mod.py").write_text("# 用户自己改的，别动我\n", encoding="utf-8")
_r = update.repair_interrupted_apply()
# ⚠️ 变异验证：把"没有 index.lock -> 只撤 journal"那条分支删掉（一律 reset --hard）
#    -> 这条红（用户自己改的东西被吃掉）。
check("⭐⭐ F3 没有 index.lock（那次 merge 干净地没换成）-> 用户改的**逐字还在**",
      _r["repaired"] and (_cloneF3 / "mod.py").read_text() == "# 用户自己改的，别动我\n"
      and (_cloneF3 / "VERSION").read_text().strip() == "0.0.1"
      and not update.apply_journal_path().exists(), str(_r))

# F4 不是快进（本地有分叉）-> 一个字节不动（`reset --hard` 会把用户自己的提交吃掉）
_tmpF4, _seedF4, _cloneF4 = new_world()
_big_release(_seedF4)
commit(_cloneF4, "local: 我自己提交的", version="9.9.9-local")
_fetch(_cloneF4)
update._write_apply_journal(_sha(_cloneF4), _sha(_cloneF4, "@{u}"))
(_cloneF4 / ".git" / "index.lock").write_text("", encoding="utf-8")
_r = update.repair_interrupted_apply()
# ⚠️ 变异验证：把 `merge-base --is-ancestor` 那道闸删掉 -> 这条红（本地提交被 reset 掉）。
check("⭐⭐ F4 分叉 -> **不动**（本地提交还在），只撤 journal",
      (_cloneF4 / "VERSION").read_text().strip() == "9.9.9-local"
      and not _r["repaired"] and not update.apply_journal_path().exists(), str(_r))

# F5 正常 `pull()`：动手**之前**写 journal、换完撤掉
_tmpF5, _seedF5, _cloneF5 = new_world()
_big_release(_seedF5)
_calls = []
_real_w = update._write_apply_journal


def _spy_w(hb, tg):
    _calls.append((hb, tg))
    return _real_w(hb, tg)


update._write_apply_journal = _spy_w
try:
    _r = update.pull()
finally:
    update._write_apply_journal = _real_w
# ⚠️ 变异验证：把 `_write_apply_journal(...)` 那行删掉 -> 这条红（换到一半被杀没据可查）。
check("⭐⭐ F5 `pull()` 动手前写下 journal（head_before -> target）、换完撤掉",
      _r["ok"] and len(_calls) == 1
      and _calls[0][1] == _sha(_cloneF5, "HEAD")      # target == 换完之后的 HEAD
      and _calls[0][0] != _calls[0][1],               # head_before != target
      f"calls={len(_calls)} ok={_r['ok']}")
check("⭐ F5b 换完 journal 不在盘上、版本真的换了",
      not update.apply_journal_path().exists()
      and (_cloneF5 / "VERSION").read_text().strip() == "0.0.2",
      (_cloneF5 / "VERSION").read_text().strip())

# F6 脏工作区 -> `pull()` 在**写 journal 之前**就停手（journal 不该出现）
_tmpF6, _seedF6, _cloneF6 = new_world()
_big_release(_seedF6)
(_cloneF6 / "requirements.txt").write_text("numpy>=2.0\n# 别丢我\n", encoding="utf-8")
_r = update.pull()
# ⚠️ 变异验证：把 `_write_apply_journal(...)` 挪到脏检查**之前** -> 这条红
#    （一次"干净拒绝"也会留下 journal，下次启动还得白修一遍）。
check("⭐⭐ F6 脏工作区 -> 停手且**没写 journal**（拒绝发生在动手之前）",
      (not _r["ok"]) and _r.get("blocked") and not update.apply_journal_path().exists(),
      str(_r)[:120])
_r2 = update.repair_interrupted_apply()
check("⭐ F6b 没有 journal -> 修复是 no-op（工作区一个字节不动）",
      not _r2["repaired"] and not _r2["action"]
      and (_cloneF6 / "requirements.txt").read_text() == "numpy>=2.0\n# 别丢我\n"
      and (_cloneF6 / "VERSION").read_text().strip() == "0.0.1", str(_r2))

# F7 `auto_update` 在**分支/限频检查之前**就修 —— 半换的树会把那些检查全带偏
_tmpF7, _seedF7, _cloneF7 = new_world()
_big_release(_seedF7)
_fetch(_cloneF7)
_hb7, _tg7 = _sha(_cloneF7), _sha(_cloneF7, "@{u}")          # ⚠️ 先读（换分支后没有 @{u}）
sh("git", "checkout", "-q", "-b", "side", cwd=_cloneF7)      # 不在正式版那条线上
update._write_apply_journal(_hb7, _tg7)
_half_swap(_cloneF7, _seedF7, ["VERSION", "mod.py"])         # ⚠️ 摆现场**之后**才有锁
with _mock.patch.object(update, "_finish_in_child", _fake_fin):
    _r = update.auto_update()
# ⚠️ 变异验证：把 auto_update 里那段 repair 挪到分支检查**之后** -> 这条红（跳过 -> 不修）。
check("⭐⭐ F7 分支不对 -> 自动更新照样**先修**（repair 在分支检查之前）",
      (_cloneF7 / "VERSION").read_text().strip() == "0.0.2"
      and not sh("git", "status", "--porcelain", cwd=_cloneF7), str(_r)[:140])

# F8 开机钩子在任何 app 模块 import **之前**（混着的树进不了 main()，钩子必须更早）
_tree_main = ast.parse((_REPO / "main.py").read_text(encoding="utf-8"))


def _first(seq, pred):
    for i, n in enumerate(seq):
        if pred(n):
            return i
    return -1


_i_app = _first(_tree_main.body, lambda n: isinstance(n, ast.ImportFrom)
                and n.module == "capture")
# ⚠️ a9d4e5d 起钩子包在 `try` 里（修不了也照常开课）—— Try / If 都认，
#    但**必须**是其中一个（裸调一段，出意外就把 app 拦在门外了）。
_i_hook = _first(_tree_main.body,
                 lambda n: "repair_interrupted_apply" in ast.unparse(n))
# ⚠️ 变异验证：把钩子挪到 import 之后 -> 这条红（`from capture import` 先炸就没人修了）。
check("⭐⭐ F8 开机钩子排在 `from capture import …` **之前**（认 journal + 包了 try）",
      0 <= _i_hook < _i_app
      and "apply_journal_path" in ast.unparse(_tree_main.body[_i_hook])
      and isinstance(_tree_main.body[_i_hook], (ast.Try, ast.If)),
      f"hook={_i_hook} first_app_import={_i_app} "
      f"node={type(_tree_main.body[_i_hook]).__name__ if _i_hook >= 0 else '?'}")

# ======================= F9. 修（repair）判据 = 一个纯函数（2026-10-18 审查）=======
# D2/D3/D4/D20/D22/D23 全挤在原 `repair_interrupted_apply` 的分支里（互相纠缠、没法逐行
# 验）。现在判据搬进 `update._decide_repair()`（纯函数、无 I/O）—— 这里**逐行**表驱动，
# 外加几个真跑 git 的集成现场。
print("\nF9. 修（repair）判据：纯函数逐行 + 真现场\n")


def _dplan(**kw):
    base = dict(head_now="H", head_before="H", target="T", ancestor=True,
                dirty=set(), changed={"a"}, dirty_matches_target=True,
                untracked_in_way=set(), had_lock=True, mine_lock=True,
                repair_failed=False)
    base.update(kw)
    return update._decide_repair(**base)


# ⚠️ 每一行的"变异验证"：把 `_decide_repair` 里对应那条分支删掉/改向 -> 该行红。
_rows = [
    # (说明, kwargs, 期望 action, 期望 rm_lock)
    ("HEAD 已在 target -> 只撤 journal，**绝不碰锁**（D22）",
     dict(head_now="T", had_lock=True), update.R_DONE, False),
    ("HEAD 两边都不是 -> 只撤 journal", dict(head_now="X"), update.R_OTHER, False),
    ("不是快进（分叉）-> 不动工作区", dict(ancestor=False), update.R_DIVERGED, False),
    ("`git diff HEAD` 失败（dirty=None）-> fail closed（D4）",
     dict(dirty=None), update.R_LEAVE, False),
    ("`git diff head..target` 失败（changed=None）-> fail closed",
     dict(changed=None), update.R_LEAVE, False),
    ("查不出挡路文件（None）-> fail closed", dict(untracked_in_way=None),
     update.R_LEAVE, False),
    ("上次动手后失败过、这次又没脏 -> 不敢说干净（D20）",
     dict(repair_failed=True, dirty=set()), update.R_LEAVE, False),
    ("本次要换的路径一个都不脏 -> 干净（工作区没写过）",
     dict(dirty=set()), update.R_CLEAN, False),
    ("**没有锁**但工作区已等于 target -> 照样补完（D23②）",
     dict(dirty={"a"}, changed={"a"}, had_lock=False), update.R_FINISH, False),
    ("有锁 + 脏⊆changed + 内容=target -> 补完、删锁",
     dict(dirty={"a"}, changed={"a"}, had_lock=True, mine_lock=True),
     update.R_FINISH, True),
    ("有锁 + 内容≠target（写了一半/混着用户改动）-> 备份+回滚",
     dict(dirty={"a"}, changed={"a", "b"}, dirty_matches_target=False,
          had_lock=True, mine_lock=True), update.R_ROLLBACK, True),
    ("**没锁** + 内容≠target -> 那是用户自己改的，一个字节不动（D23①/F3）",
     dict(dirty={"a"}, changed={"a"}, dirty_matches_target=False,
          had_lock=False), update.R_CLEAN, False),
    ("脏的与本次要换的**没有交集** -> 干净、不 checkout（D3：空 pathspec 会 fatal）",
     dict(dirty={"a"}, changed={"b"}), update.R_CLEAN, False),
    ("补完时把挡路的未跟踪文件带上备份（D2）",
     dict(dirty={"a"}, changed={"a"}, untracked_in_way={"z"}, had_lock=True),
     update.R_FINISH, True),
    ("没抢到 update.lock（mine_lock=False）-> 不删锁",
     dict(dirty={"a"}, changed={"a"}, had_lock=True, mine_lock=False),
     update.R_FINISH, False),
]
for _desc, _kw, _act, _lock in _rows:
    _p = _dplan(**_kw)
    check(f"F9 判据：{_desc}",
          _p["action"] == _act and _p["rm_lock"] == _lock,
          f"得 {_p['action']}/rm_lock={_p['rm_lock']}，期望 {_act}/{_lock}")
check("F9 D2：补完时备份清单里带着挡路的未跟踪文件",
      _dplan(dirty={"a"}, changed={"a"}, untracked_in_way={"z"})["backup"] == ["z"])
check("F9 D3：回滚清单 = 脏 ∩ changed（不含用户的无关改动）",
      _dplan(dirty={"a", "q"}, changed={"a", "b"}, dirty_matches_target=False,
             had_lock=True)["roll"] == ["a"])

# ⚠️ D4 的关键实现：**git 查询失败必须返回 `None`**（返回空集 = fail open）。
_orig_raw = update._git_raw


def _boom_raw(*a, **k):
    return 128, b"", "boom"


update._git_raw = _boom_raw
try:
    _d4 = update._dirty_tracked()
    _c4 = update._changed_paths("H", "T")
    _u4 = update._untracked_in_way({"a"})
finally:
    update._git_raw = _orig_raw
check("F9 D4 git 查询失败 -> `None`（不是空集；空集会让判据 fail open 去 reset）",
      _d4 is None and _c4 is None and _u4 is None, f"{_d4} {_c4} {_u4}")

# ---- 集成：D22 HEAD 已在 target 时**不许**碰别人的 index.lock ----
_tmpD22, _seedD22, _cloneD22 = new_world()
_fetch(_cloneD22)
_hb22, _tg22 = _sha(_cloneD22), _sha(_cloneD22, "@{u}")
# 先把 HEAD 挪到 target（模拟"那次 pull 已经跑完"），再摆一个锁 + journal
sh("git", "reset", "--hard", _tg22, cwd=_cloneD22)
update._write_apply_journal(_hb22, _tg22)
(_cloneD22 / ".git" / "index.lock").write_text("foreign", encoding="utf-8")
# ⚠️ 变异验证：把 `R_DONE` 分支里的 `_rm_stale_index_lock()` 加回来 -> 这条红（锁被删）。
_r22 = update.repair_interrupted_apply()
_l22 = _cloneD22 / ".git" / "index.lock"
_l22_txt = _l22.read_text(encoding="utf-8") if _l22.exists() else "<被删了>"
check("F9 D22 HEAD 已在 target -> 撤 journal 但**不动** index.lock（可能就是别人的）",
      _r22["repaired"] and not update.apply_journal_path().exists()
      and _l22_txt == "foreign", f"{_r22} lock={_l22_txt!r}")
(_cloneD22 / ".git" / "index.lock").unlink(missing_ok=True)

# ---- 集成：D3 脏的与本次要换的没有交集 -> 干净收场（原来 checkout 空 pathspec fatal）----
_tmpD3, _seedD3, _cloneD3 = new_world()
_big_release(_seedD3)
_fetch(_cloneD3)
_hb3, _tg3 = _sha(_cloneD3), _sha(_cloneD3, "@{u}")
update._write_apply_journal(_hb3, _tg3)
(_cloneD3 / "requirements.txt").write_text("numpy>=2.0\n# 用户自己改的\n", encoding="utf-8")
(_cloneD3 / ".git" / "index.lock").write_text("", encoding="utf-8")
# ⚠️ 变异验证：把 `if not roll: return R_CLEAN` 删掉 -> 这条红（`git checkout head --` 空
#    pathspec -> fatal -> ok=False 且 journal 留着 —— 正是 D3）。
_r3 = update.repair_interrupted_apply()
check("F9 D3 脏的与本次无关 -> 干净收场、用户改动逐字还在、journal 撤掉",
      _r3["repaired"] and _r3["action"] == "上次没换成（干净）—— 工作区没动"
      and (_cloneD3 / "requirements.txt").read_text() == "numpy>=2.0\n# 用户自己改的\n"
      and not update.apply_journal_path().exists(), str(_r3))
(_cloneD3 / ".git" / "index.lock").unlink(missing_ok=True)

# ---- 集成：D23② 没有 index.lock，但工作区内容已等于 target -> 照样补完 ----
_tmpD23, _seedD23, _cloneD23 = new_world()
_big_release(_seedD23)
_fetch(_cloneD23)
_hb23, _tg23 = _sha(_cloneD23), _sha(_cloneD23, "@{u}")
update._write_apply_journal(_hb23, _tg23)
_half_swap(_cloneD23, _seedD23, ["VERSION"], lock=False)      # 写完、但锁已释放
# ⚠️ 变异验证：把"内容比对"那条 FINISH 判据删掉（只看锁）-> 这条红（判成干净、树半新）。
_r23 = update.repair_interrupted_apply()
check("F9 D23② 没锁但树已等于 target -> 补完（不只信 index.lock）",
      _r23["repaired"] and _sha(_cloneD23) == _tg23
      and (_cloneD23 / "VERSION").read_text().strip() == "0.0.2"
      and not sh("git", "status", "--porcelain", cwd=_cloneD23), str(_r23))

# ---- 集成：D2 reset 前把挡路的未跟踪文件**备份**下来 ----
_tmpD2, _seedD2, _cloneD2 = new_world()
_big_release(_seedD2)
_fetch(_cloneD2)
_hbD2, _tgD2 = _sha(_cloneD2), _sha(_cloneD2, "@{u}")
update._write_apply_journal(_hbD2, _tgD2)
_half_swap(_cloneD2, _seedD2, ["VERSION"], lock=True)          # 半个换 + 锁
(_cloneD2 / "extra.py").write_text("# 用户自己放的同名文件\n", encoding="utf-8")
# ⚠️ 变异验证：把 `backup=sorted(untracked_in_way)` 去掉 -> 这条红（用户文件被静默删掉）。
_rD2 = update.repair_interrupted_apply()
_bdir = pathlib.Path(_rD2.get("backup_dir") or "")
_bk = list(_bdir.rglob("extra.py")) if str(_bdir) and _bdir.exists() else []
check("F9 D2 补完前把挡路的未跟踪文件备份了（不是静默删掉）",
      _rD2["repaired"] and _bk
      and _bk[0].read_text() == "# 用户自己放的同名文件\n"
      and (_cloneD2 / "extra.py").read_text() != "# 用户自己放的同名文件\n",
      f"{_rD2.get('backup_dir')} bk={_bk}")

# ---- 集成：D19 `pull()` 在 journal→pull 这段**必须持 update.lock** ----
_tmpD19, _seedD19, _cloneD19 = new_world()
_big_release(_seedD19)
_fetch(_cloneD19)
_saved_mine = update._LOCK_MINE
update._LOCK_MINE = False
update.STATE_DIR.mkdir(parents=True, exist_ok=True)
_fk = update.STATE_DIR / "update.lock"
_fk.write_text("999999", encoding="utf-8")                     # 别人正拿着（新鲜）
update._rm_journal(update.apply_journal_path())
try:
    _r19 = update.pull()
finally:
    update._LOCK_MINE = _saved_mine
# ⚠️ 变异验证：把 `pull()` 里那段 `_acquire_lock()` 去掉 -> 这条红
#    （它照样写 journal、跑 git pull、更新装上了 —— 与另一个更新器并发换代码）。
check("F9 D19 没抢到锁 -> `pull()` 在写 journal 前就停手（不并发换代码）",
      (not _r19["ok"]) and _r19.get("blocked")
      and not update.apply_journal_path().exists()
      and (_cloneD19 / "VERSION").read_text().strip() == "0.0.1"
      and _fk.read_text() == "999999", str(_r19)[:140])
_fk.unlink(missing_ok=True)

# ---- 集成：D20 上次动手失败过 -> 这次保留 journal、一个字节不动 ----
_tmpD20, _seedD20, _cloneD20 = new_world()
_big_release(_seedD20)
_fetch(_cloneD20)
_hb20, _tg20 = _sha(_cloneD20), _sha(_cloneD20, "@{u}")
update._write_apply_journal(_hb20, _tg20)
update._mark_repair_failed(update.apply_journal_path())        # 记一笔"上次动手后失败"
_r20 = update.repair_interrupted_apply()
check("F9 D20 journal 里有 repair_failed + 工作区干净 -> 保留 journal（不当成干净）",
      (not _r20["ok"]) and _r20.get("skipped")
      and update.apply_journal_path().exists()
      and (_cloneD20 / "VERSION").read_text().strip() == "0.0.1", str(_r20))
update._rm_journal(update.apply_journal_path())

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
