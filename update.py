#!/usr/bin/env python3
"""共享的更新核心 —— **卡片上的一键更新按钮**和 **`cl update`** 都走这里。

为什么要抽出来：这两条路径的**安全规则必须完全一致**，各写一份迟早在某一边漂移。
规则本身很简单，但错一条就是丢用户的工作。

三条边界（与原 `cl` 里的 bash 实现逐字对齐，改这里时那边也要改）：

  ① **工作区脏就停手** —— 绝不 `stash`、绝不丢弃。那些改动可能是用户自己改的。
  ② **`--ff-only`** —— 本地和远程分叉时**响亮失败**，不静默造一个 merge commit。
  ③ **绝不自动下模型**（1GB 级的东西要不要下是用户的决定）。本模块也不装依赖 ——
     `requirements.txt` 变了只**报出来**，让调用方决定。

⚠️ 与 `cl` 的分工：
    · `cl update`  = 本模块 `pull()` + 之后补依赖 + 跑 `doctor.py`（bash 侧做）
    · 卡片按钮      = 本模块 `pull()`，**不装依赖**（装依赖是"改用户环境"，要用户自己点头）
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
# 网络操作的上限。fetch 在慢网下可能很久，但**不能无限等** —— 卡片是后台线程，
# 卡住会让人以为按钮坏了。超时当"没查到"处理，不算失败。
TIMEOUT = 45


def _git(*args: str, timeout: int = TIMEOUT) -> tuple[int, str, str]:
    """跑一条 git。返回 (返回码, stdout, stderr)。异常统一成返回码 -1。"""
    try:
        r = subprocess.run(["git", *args], cwd=HERE, capture_output=True,
                           text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", f"超时（>{timeout}s）"
    except Exception as e:                                # noqa: BLE001
        return -1, "", str(e)


def _version() -> str:
    f = HERE / "VERSION"
    return f.read_text(encoding="utf-8").strip() if f.exists() else "?"


def _reqs_hash() -> str:
    """requirements.txt 的内容指纹 —— 用来判断"要不要补依赖"。"""
    import hashlib
    f = HERE / "requirements.txt"
    return hashlib.sha256(f.read_bytes()).hexdigest()[:16] if f.exists() else ""


def is_repo() -> bool:
    return _git("rev-parse", "--git-dir")[0] == 0


def has_upstream() -> bool:
    return _git("rev-parse", "--abbrev-ref", "@{u}")[0] == 0


def upstream_ref() -> str:
    """上游 ref（如 `origin/main`）。**不要硬编码 `origin/main`** —— 远程名或分支名
    跟作者的不一样就全废了（踩过：初版写死 `origin/main`）。
    先问 `@{u}`，再退化到常见组合，最后空串表示查不到。
    """
    rc, name, _ = _git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if rc == 0 and name and name != "@{u}":
        return name
    return ""


def remote_file(name: str, ref: str = "") -> str:
    """读**远端 ref** 上的某个文件，**完全不动工作区**。取不到返回空串。

    ⚠️ 这是"退出时自动更新"能不能安全分级的关键：判断新版本是不是 `auto`
    必须**在 pull 之前**做 —— 磁盘上的 CHANGELOG 还没有那些版本。拉完再判断
    就已经应用了，来不及拒绝。
    """
    r = ref or upstream_ref()
    if not r:
        return ""
    rc, so, _ = _git("show", f"{r}:{name}")
    return so if rc == 0 else ""


# ---- 更新决策用的纯解析（`main.py` 也从这里 import，避免两份实现漂移）----

def versions_in(text: str) -> list[str]:
    """一段 CHANGELOG 文本里所有版本号，**新到旧**（文件里的出现顺序）。"""
    import re
    return re.findall(r"^## \[([^\]]+)\]", text or "", re.M)


def update_mode_in(text: str, version: str) -> str:
    """该版本能否**后台自动应用** —— 只看 `### 更新方式` 那节。

    ⚠️ **默认 `manual`**（默认拒绝）：没声明自己安全的，绝不自动应用。

    ⚠️⚠️ **不能用 semver 判**：本仓库 `3.4.0 → 3.5.0` 是 **minor** 版本号、内容却是
    **breaking**（两个 ASR 模型改必装、缺了启动即报错）。作者不按 semver 发版，
    按版本号分级会正好漏掉最危险的那些。
    """
    import re
    sec = re.search(rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|\Z)",
                    text or "", re.S | re.M)
    if not sec:
        return "manual"
    m = re.search(r"^###\s*更新方式[^\n]*\n(.*?)(?=^### |\Z)", sec.group(1), re.S | re.M)
    if not m:
        return "manual"
    return "auto" if re.search(r"^\s*auto\s*$", m.group(1), re.M | re.I) else "manual"


def check() -> dict:
    """只 `fetch` + 看落后几个提交，**不动工作区**。适合启动时/按钮点下去先探一下。

    返回 `{ok, error, dirty, behind, current, ahead, detached}`。
    `ok=False` 表示"查不到"（断网/无 git/无 upstream）—— 调用方应当**静默降级**，
    不要当成错误弹给用户。
    """
    out = {"ok": False, "error": "", "dirty": False, "behind": 0,
           "current": _version(), "ahead": 0, "detached": False}
    if not is_repo():
        out["error"] = "不是 git 仓库"
        return out
    if not has_upstream():
        out["error"] = "没有上游分支"
        out["detached"] = True
        return out
    out["dirty"] = bool(_git("status", "--porcelain")[1])

    rc, _, err = _git("fetch", "--quiet")
    if rc != 0:
        # 断网 ≠ 失败。本地远端 ref 可能还是旧的，但先告诉调用方"这次没查到"。
        out["error"] = f"fetch 失败: {err or rc}"
        return out

    rc, b, _ = _git("rev-list", "--count", "HEAD..@{u}")
    rc2, a, _ = _git("rev-list", "--count", "@{u}..HEAD")
    out["behind"] = int(b) if b.isdigit() else 0
    out["ahead"] = int(a) if a.isdigit() else 0
    out["ok"] = True
    return out


def _friendly(out: dict) -> str:
    """把 `pull()` 的失败**派生**成一句面向用户的话（无 git 术语）。

    ⚠️ 为什么派生而不是每条失败路径各自填 `user_msg`：
    那样新加的失败路径**会忘记填**，卡片上就会冒出「工作区」「stash」这类词。
    这里只认 `blocked` / 几种已知原因，其余统一给一句通用的话 + 提示看日志。
    """
    if not out.get("ok") and out.get("blocked"):
        return "这个文件夹里有你自己改过的内容，这次就先不更新了（怕覆盖掉）。"
    err = out.get("error") or ""
    if "不是 git 仓库" in err:
        return "这个文件夹不是从网上下载的那种，没法自动更新。"
    # ⚠️ 分叉要**先**判：它的 error 里也含「拉取失败」，会被下面那条网络判据吃掉，
    # 于是把"分叉"说成"网络不通" —— 那是误导，两者要做的事完全不同。
    if "分叉" in err:
        return "这个文件夹里的版本和网上的对不上了（你自己改过并提交过），这次先不更新。"
    if "拉取失败" in err or "fetch 失败" in err or "网络" in err:
        return "网络好像不通，这次先不更新了。过会儿再试就行。"
    return "这次没能更新成功（详情看 ~/Library/Logs/ClassLive/ 里的日志）。"


def pull() -> dict:
    """真正应用更新。**先检查再动手**，任何一条边界不满足就返回 `ok=False`。

    返回 `{ok, error, skipped, before, after, commits, log, reqs_changed}`。
    `skipped=True` 表示"本来就没啥可更新"（不是失败）。
    """
    out = {"ok": False, "error": "", "skipped": False, "blocked": False,
           "before": _version(), "after": _version(), "commits": 0, "log": [],
           "reqs_changed": False, "user_msg": ""}
    if not is_repo():
        out["error"] = "这不是 git 仓库，没法更新。"
        return {**out, "user_msg": _friendly(out)}
    # ⚠️ 边界①：脏就停手。放在 fetch 之前 —— 连探测都不该动用户的工作区。
    # `blocked=True` 与"失败"分开：这是**主动拒绝**，不是出错。UI 措辞要不一样
    # （"需先处理改动" 而不是 "更新失败"），否则用户以为工具坏了。
    dirty = _git("status", "--porcelain")[1]
    if dirty:
        out["blocked"] = True
        # `error` 给开发者/命令行看（含 git 术语）；`user_msg` 给**卡片**看。
        # 面向用户那条**不许出现「工作区」「stash」「未提交」**这类词 ——
        # 用工具的人不知道 git，看到这些只会以为哪儿坏了。
        out["error"] = ("工作区有未提交的本地改动，已停手（绝不 stash、绝不丢弃）。\n"
                        + "\n".join("    " + x for x in dirty.splitlines()[:8]))
        return {**out, "user_msg": _friendly(out)}

    head_before = _git("rev-parse", "--short", "HEAD")[1]
    reqs_before = _reqs_hash()

    rc, _, err = _git("fetch", "--quiet")
    if rc != 0:
        out["error"] = f"拉取失败（网络？）：{err or rc}"
        return {**out, "user_msg": _friendly(out)}

    rc, b, _ = _git("rev-list", "--count", "HEAD..@{u}")
    behind = int(b) if b.isdigit() else 0
    if behind == 0:
        out["ok"], out["skipped"] = True, True
        return out

    # ⚠️ 边界②：--ff-only。分叉时这里会失败，那是**想要**的结果。
    rc, so, se = _git("pull", "--ff-only", timeout=TIMEOUT * 2)
    if rc != 0:
        out["error"] = ("拉取失败。常见两种：\n"
                        "    · 网络不通 —— 过会儿再试\n"
                        "    · 本地和远程**分叉**了（你自己在本地提交过）—— 需要你自己决定\n"
                        "      `git rebase origin/main` 还是 `git merge origin/main`\n"
                        f"    原文：{se or so or rc}")
        return {**out, "user_msg": _friendly(out)}

    head_after = _git("rev-parse", "--short", "HEAD")[1]
    out["after"] = _version()
    out["commits"] = behind
    out["reqs_changed"] = (_reqs_hash() != reqs_before)
    out["log"] = _git("log", "--oneline", f"{head_before}..{head_after}")[1].splitlines()
    out["ok"] = True
    return out


# ---- 退出时的自动更新 ------------------------------------------------------

STATE_DIR = pathlib.Path.home() / "Library" / "Logs" / "ClassLive"
RATE_LIMIT_S = 3600     # 一小时最多查一次。atomic 的做法：每机器每小时最多一次
#                         release lookup；别每次启动都打 GitHub。
LOCK_STALE_S = 900      # 锁超过 15 分钟当**陈旧**、自过期 —— 崩溃的更新器不能永久堵住以后


def _log(msg: str) -> None:
    """自动更新是**静默**的 —— 静默失败没有日志就没法排查，所以必须落一行。

    写 `~/Library/Logs/ClassLive/`（与 cl-bg.py 同一处）：不污染仓库、不进 git。
    目录建不出来就放弃（**绝不能因此崩**）。
    """
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with open(STATE_DIR / "update.log", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}\n")
    except Exception:                                     # noqa: BLE001
        pass


def auto_update() -> dict:
    """退出时的自动更新 —— 由 `--auto` 调用，**跑在独立的后台进程里**。

    调研到的业内共识（2026-09-24，见 docs/PLAN-update-mechanism.md §7）：

      · **绝不延迟退出** —— Mozilla Silent Update 的目标原话是
        "We don't want to delay shutdown"。所以这个函数**必须在 detached 子进程里跑**，
        绝不能出现在主进程的退出路径上。
      · **独立进程** —— Firefox 的后台更新器就是独立进程；Automattic 的
        auto-update 也建议 "separate thread (or even a separate process)"。
      · **限频** —— atomic：每机器每小时最多一次 release lookup。
      · **并发保护** —— 两个更新器不能同时换代码；崩溃留下的陈旧锁要能自过期。

    "环境不全"的各种情况一律**静默降级**（只写日志，绝不弹窗、绝不报错）：
    没装 git / 没配 upstream / remote 名不同 / 浅克隆 / 没有 VERSION /
    切了分支 / 磁盘满 / `~/Library/Logs` 不可写 —— 都只是"这次没更新成"。
    """
    out = {"ok": False, "skipped": False, "error": "", "reason": "", "after": ""}
    lock = STATE_DIR / "update.lock"
    held = False          # ⚠️ **只删自己拿到的锁** —— 见下面的 finally
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:                                     # noqa: BLE001
        return out                                        # 连日志目录都没有 -> 放弃

    try:
        # ---- 限频 ----
        stamp = STATE_DIR / "last_check"
        if stamp.exists() and (time.time() - stamp.stat().st_mtime) < RATE_LIMIT_S:
            return {**out, "skipped": True, "reason": "限频（一小时一次）"}
        # ---- 并发保护（陈旧锁自过期）----
        try:
            if lock.exists() and (time.time() - lock.stat().st_mtime) < LOCK_STALE_S:
                return {**out, "skipped": True, "reason": "已有更新在跑"}
            lock.write_text(str(os.getpid()), encoding="utf-8")
            held = True                               # 从这里起，锁才是**我们的**
        except Exception:                                 # noqa: BLE001
            pass
        try:
            stamp.write_text("", encoding="utf-8")
        except Exception:                                 # noqa: BLE001
            pass

        if not is_repo():
            _log("跳过：不是 git 仓库（可能是解压安装的）")
            return {**out, "skipped": True, "reason": "不是 git 仓库"}
        if not upstream_ref():
            _log("跳过：没有配置上游分支（git remote / branch tracking 不全）")
            return {**out, "skipped": True, "reason": "无上游"}

        st = check()                                      # 只 fetch，不动工作区
        if not st["ok"]:
            _log(f"检查失败：{st['error']}")
            return {**out, "error": st["error"]}
        if st["dirty"]:
            _log("跳过：工作区有本地改动（绝不 stash / 绝不丢弃）")
            return {**out, "skipped": True, "reason": "工作区脏"}
        if st["behind"] <= 0:
            return {**out, "ok": True, "skipped": True, "reason": "已是最新"}

        # ---- 分级：**在拉之前**判断，且要读远端 ref 的 CHANGELOG（磁盘上还没有）----
        remote_ver = remote_file("VERSION").strip()
        remote_log = remote_file("CHANGELOG.md")
        if not remote_ver or not remote_log:
            _log(f"跳过：读不到远端文件（VERSION={bool(remote_ver)} "
                 f"CHANGELOG={bool(remote_log)}）")
            return {**out, "skipped": True, "reason": "读不到远端文件"}
        vers = versions_in(remote_log)
        if st["current"] not in vers:
            _log(f"跳过：本地版本 {st['current']} 不在远端历史里（分叉/改名？）")
            return {**out, "skipped": True, "reason": "版本不在远端历史"}
        todo = vers[:vers.index(st["current"])]
        manual = [v for v in todo if update_mode_in(remote_log, v) != "auto"]
        if manual:
            _log(f"跳过自动更新：{'、'.join(manual)} 标了 manual（要用户自己点）")
            return {**out, "skipped": True, "reason": f"manual: {manual}"}

        r = pull()
        msg = (f"自动更新 {st['current']} → {r.get('after') or '?'} "
               f"ok={r['ok']} 提交={r.get('commits')}")
        if r.get("reqs_changed"):
            msg += "  ⚠️ 依赖有变化，需要用户自己跑 cl update"
        if r.get("error"):
            msg += f"  err={r['error'][:160]}"
        _log(msg)
        # ⚠️ 一并透传 `user_msg` —— 否则正常路径与异常路径的返回结构不一致，
        # 上层（卡片）复用这个返回值时，失败文案会退回含 git 术语的 `error`。
        # (2026-09-25 ocr review 发现)
        return {**out, "ok": r["ok"], "after": r.get("after", ""),
                "error": r.get("error", ""), "user_msg": r.get("user_msg", "")}
    except Exception as e:                                # noqa: BLE001
        _log(f"自动更新异常：{type(e).__name__}: {e}")
        out["error"] = str(e)
        return {**out, "user_msg": _friendly(out)}
    finally:
        # ⚠️ **只删还属于自己那把锁。**
        #
        # 两个坑，都是实测/复审发现的：
        #   ① 无条件 unlink 会删掉**别的进程持有的锁**：进程 B 因"已有更新在跑"早退时
        #      并没持锁，却把 A 的锁删了 -> 进程 C 立刻能拿到 -> **两个更新器同时 pull**。
        #      (2026-09-25 全量 OCR 发现)
        #   ② 光记一个 `held` 标志**还不够**：它只证明"我写过锁"，不证明"锁现在还是我的"。
        #      若 A 持锁超过 STALE 被 B 抢走，A 的 finally 仍会删掉 B 的锁。
        #      (同日 ocr review 发现)
        # 所以按**锁里的内容**核对：锁里存的就是 pid，是我的才删。
        if held:
            try:
                if lock.read_text(encoding="utf-8").strip() == str(os.getpid()):
                    lock.unlink(missing_ok=True)
            except Exception:                            # noqa: BLE001
                pass


def spawn_auto_update() -> bool:
    """在**独立的后台进程**里跑 `auto_update()`，父进程立刻返回。

    ⚠️ 这是"绝不延迟退出"的唯一实现方式。同步跑的话，断网时用户要干等
    45 秒才能关掉工具 —— Mozilla 把 "We don't want to delay shutdown"
    明确列为设计目标，就是因为这种体验不能接受。

    `start_new_session=True` = `setsid`：子进程脱离父进程的会话，
    父进程退出后它继续跑完（把代码拉下来），不受影响。
    """
    try:
        subprocess.Popen(
            [sys.executable, str(HERE / "update.py"), "--auto"],
            cwd=HERE, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
        return True
    except Exception:                                     # noqa: BLE001
        return False


def _cli() -> int:
    """命令行：`python update.py [--check] [--quiet] [--auto]`。"""
    check_only = "--check" in sys.argv
    quiet = "--quiet" in sys.argv

    if "--auto" in sys.argv:
        auto_update()
        return 0

    if check_only:
        r = check()
        if not r["ok"]:
            if not quiet:
                print(f"（查不到更新：{r['error']}）")
            return 0
        if r["behind"]:
            print(f"落后远程 {r['behind']} 个提交。")
        else:
            print(f"✅ 已经是最新的 ({r['current']})。")
        return 0

    r = pull()
    if not r["ok"]:
        print(f"❌ {r['error']}")
        return 1
    if r["skipped"]:
        print(f"✅ 已经是最新的 ({r['after']})。")
        return 0
    print(f"✅ {r['before']} → {r['after']}   ({r['commits']} 个提交)")
    if r["log"]:
        print("\n   这次改了什么:")
        for x in r["log"]:
            print(f"     {x}")
    if r["reqs_changed"]:
        print("\n🔧 依赖清单有变化 —— 需要你自己装（本模块不替你改环境）：")
        print("   uv pip install --python .venv/bin/python -r requirements.txt")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
