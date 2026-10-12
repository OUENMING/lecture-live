#!/usr/bin/env python3
"""开发快筛：一条命令跑完「不联网、不加载模型、不碰真数据」的全部判据。

用法（仓库根，用 App 自带的 python）：
    ClassLive.app/Contents/MacOS/python scripts/check.py            # 快档：~15 s
    ClassLive.app/Contents/MacOS/python scripts/check.py --all      # 加上 4 个慢的：~45 s
    ClassLive.app/Contents/MacOS/python scripts/check.py --changed [REV]
        # 快档 + 按 `git diff REV`（默认 HEAD，含未跟踪）碰到的文件补跑慢测试，
        # 并列出**要人去跑**的探针 / 离线量具（它们花钱、要真跑或要人手，这里不自动跑）

它做四件事：
1. `tests/test_*.py` 每个文件一个子进程并行跑，看退出码（两种测试风格都兼容）。
   退出码 77 = 「跳过：缺本机样本」，单独列出，**不算通过也不算失败**。
2. 写端隔离：子进程的 `HOME` 指到临时目录、`CLASSLIVE_UPLOAD=0`、`CLASSLIVE_NO_AUTO_UPDATE=1`。
3. 写端核对：跑前跑后给 `sessions/`、真 `~/.classlive/`、Obsidian 的 `Lectures/`、`git status`
   拍快照，有任何变化判红 —— 「测试必须隔离写端」从叮嘱变成判据。
   ⚠️ 正在上课（`cl` 在写 `sessions/`）时跑会误报，看清是哪个文件再说。
4. 静态检查：`ruff`（规则见 `ruff.toml`）+ `pyright` 只看两条抓过真 bug 的规则
   （`reportUndefinedVariable` / `reportCallIssue`，见 HANDBOOK §4.2），已知误报在
   `scripts/pyright_baseline.json`，**新冒出来的才判红**。没有 `uvx` 时这一步跳过并说明。

「碰了什么要跑什么」的唯一定义点是下面的 `SLOW_FOR` / `MANUAL_FOR`。
"""
from __future__ import annotations

import argparse
import collections
import fnmatch
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

ROOT = pathlib.Path(__file__).resolve().parent.parent
PY = ROOT / "ClassLive.app/Contents/MacOS/python"
PY = PY if PY.exists() else pathlib.Path(sys.executable)
SKIP_RC = 77
RUFF = "ruff@0.17.0"   # 钉版本：新版加规则会让同一份代码突然变红
PYRIGHT = "pyright@1.1.414"
PYRIGHT_RULES = {"reportUndefinedVariable", "reportCallIssue"}
BASELINE = ROOT / "scripts/pyright_baseline.json"

# 慢测试：默认不跑；`--all` 全跑；`--changed` 按改动的文件挑。
SLOW_FOR = {
    "test_update.py": ["update.py", "whatsnew.py", "cl", "make-app.sh", "install.sh", "VERSION"],
    "test_live_summary.py": ["live_summary.py", "chapter.py", "main.py"],
    "test_wrapup.py": ["wrapup.py", "polish.py", "obsidian_writer.py", "keypoints.py", "main.py"],
    "test_fetch_model.py": ["fetch_model.py", "models.py"],
}

# 要人去跑的：花钱 / 要真跑一节 / 要人手 / 本身会翻转。只列出来，不自动跑。
MANUAL_FOR = [
    (["main.py", "testmode.py"],
     "scripts/probe_testmode_report.py —— 收尾路径；真跑约 1 分钟，往 sessions/ 写一节测试记录"),
    (["vad.py", "capture.py", "asr.py", "main.py"],
     "scripts/test_pipeline.py <音频> —— 端到端能跑完"),
    (["draft_engine.py", "main.py"],
     "docs/PLAN-streaming-asr.md §6.3 —— 三遍 e2e（parakeet / nemotron / 模型目录不存在），草稿接线只有真跑抓得到"),
    (["vad.py", "asr.py", "draft_policy.py", "draft_stable.py", "local_postprocess.py"],
     "scripts/stream_eval.py —— 回改率 / 延迟 / WER，对照 docs/RESEARCH-stream-eval.md 的基线"),
    (["draft_policy.py", "draft_translate.py", "main.py"],
     "scripts/draft_gap_replay.py <会话>.live.jsonl —— 翻译空窗（基线 10-09 真课：3 次 / 23 s）"),
    (["vad.py", "asr.py"],
     "OCW 定稿真值 —— docs/experiments/cloud_asr_20261010/README.md（基线：本地 12.0% / 漏 2.5%）"),
    (["cloud_translator.py", "translator.py"],
     "docs/experiments/context_ab.py / scripts/draft_ctx_ab.py —— 改 prompt 后的译文并排（花钱，人看）"),
    (["overlay.py", "transcript_view.py"],
     "scripts/probe_scroll.py —— 滚动；它自己会翻转，❌ 先连跑两三次再下结论"),
    (["overlay.py", "draft_translate.py"],
     "scripts/probe_stream_draft.py —— 定稿流式时草稿栏 / 英文占位，改前改后各拍一张（给作者看）"),
    (["overlay.py"],
     "scripts/probe_hint.py —— ❓ 点拨在术语格里的样子（拍两张图，给作者看）"),
    (["entry_panel.py", "entry_launch.py", "final_asr.py"],
     "scripts/probe_cloud_asr_consent.py —— 「语音模型」那一格 + 首次告知弹窗拍图（云端转写菜单项见 test_entry_panel ⑳b）（给作者看）"),
    (["panel.py", "entry_panel.py"],
     "scripts/probe_drag.py / scripts/probe_entry_panel.py —— 真实拖拽落点，作者手拖一次"),
]


# ---------- 写端快照 ----------

def _tree(p: pathlib.Path, depth: int = 3) -> dict:
    out = {}
    if not p.is_dir():
        return out
    base = len(p.parts)
    for d, dirs, files in os.walk(p):
        if len(pathlib.Path(d).parts) - base >= depth:
            dirs[:] = []
        for f in files:
            q = pathlib.Path(d) / f
            try:
                st = q.stat()
            except OSError:
                continue
            out[str(q)] = (st.st_size, st.st_mtime_ns)
    return out


def _vault_lectures() -> pathlib.Path | None:
    sys.path.insert(0, str(ROOT))
    try:
        from obsidian_writer import resolve_vault
        v = resolve_vault(None)
    except Exception:                                        # noqa: BLE001
        return None
    return pathlib.Path(v) / "Lectures" if v else None


def snapshot() -> dict:
    snap = {"sessions": _tree(ROOT / "sessions", 1),
            "~/.classlive": _tree(pathlib.Path.home() / ".classlive", 2)}
    lec = _vault_lectures()
    if lec:
        snap["Lectures"] = _tree(lec, 1)
    git = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain"],
                         capture_output=True, text=True)
    snap["git"] = {ln: 0 for ln in git.stdout.splitlines()}
    return snap


def snapshot_diff(a: dict, b: dict) -> list[str]:
    out = []
    for k in a.keys() | b.keys():
        x, y = a.get(k, {}), b.get(k, {})
        for f in sorted(x.keys() | y.keys()):
            if x.get(f) != y.get(f):
                tag = "新增" if f not in x else "消失" if f not in y else "改动"
                out.append(f"{k}: {tag} {f}")
    return out


# ---------- 测试 ----------

def run_test(path: pathlib.Path, home: pathlib.Path) -> tuple:
    env = dict(os.environ, HOME=str(home), CLASSLIVE_UPLOAD="0",
               CLASSLIVE_NO_AUTO_UPDATE="1", PYTHONDONTWRITEBYTECODE="1")
    t = time.monotonic()
    try:
        p = subprocess.run([str(PY), str(path)], cwd=ROOT, env=env,
                           capture_output=True, text=True, timeout=300)
        rc, out = p.returncode, p.stdout + p.stderr
    except subprocess.TimeoutExpired:
        rc, out = "超时", ""
    return path.name, rc, time.monotonic() - t, out


# ---------- 静态检查 ----------

def _uvx(*args) -> subprocess.CompletedProcess | None:
    uvx = shutil.which("uvx") or str(pathlib.Path.home() / ".local/bin/uvx")
    if not pathlib.Path(uvx).exists():
        return None
    return subprocess.run([uvx, *args], cwd=ROOT, capture_output=True, text=True, timeout=300)


def run_ruff() -> tuple[str, str]:
    p = _uvx(RUFF, "check", "--no-cache", ".")
    if p is None:
        return "skip", "没有 uvx"
    if p.returncode == 0:
        return "ok", "没有问题"
    return "fail", "\n".join("    " + ln for ln in p.stdout.strip().splitlines()[:40])


def pyright_findings() -> list[dict] | None:
    files = sorted(str(p.relative_to(ROOT)) for p in ROOT.glob("*.py"))
    p = _uvx(PYRIGHT, "--pythonpath", str(PY), "--outputjson", *files)
    if p is None or not p.stdout.strip().startswith("{"):
        return None
    out = []
    for d in json.loads(p.stdout)["generalDiagnostics"]:
        if d.get("rule") in PYRIGHT_RULES:
            out.append({"file": pathlib.Path(d["file"]).name, "rule": d["rule"],
                        "message": d["message"].splitlines()[0]})
    return out


def run_pyright(update_baseline: bool) -> tuple[str, str]:
    got = pyright_findings()
    if got is None:
        return "skip", "没有 uvx 或 pyright 拉不下来（要联网一次）"
    key = lambda d: (d["file"], d["rule"], d["message"])          # noqa: E731 不用行号：行号会漂
    if update_baseline:
        BASELINE.write_text(json.dumps(sorted(got, key=key), ensure_ascii=False, indent=1) + "\n",
                            encoding="utf-8")
        return "ok", f"已重写基线（{len(got)} 条）"
    known = collections.Counter(key(d) for d in json.loads(BASELINE.read_text(encoding="utf-8")))
    new = collections.Counter(key(d) for d in got) - known
    if new:
        return "fail", "\n".join(f"    {f} [{r}] {m}" for (f, r, m) in new.elements())
    return "ok", f"{len(got)} 条全在基线里"


# ---------- 主流程 ----------

def changed_files(rev: str) -> set[str]:
    def git(*a):
        return subprocess.run(["git", "-C", str(ROOT), *a], capture_output=True,
                              text=True).stdout.split()
    return set(git("diff", "--name-only", rev)) | set(git("ls-files", "--others", "--exclude-standard"))


def _hit(files: set[str], patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(f, p) for f in files for p in patterns)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--all", action="store_true", help="慢测试也跑")
    ap.add_argument("--changed", nargs="?", const="HEAD", metavar="REV",
                    help="按 git diff REV 补跑相关慢测试并列出要人跑的量具")
    ap.add_argument("--no-static", action="store_true", help="跳过 ruff / pyright")
    ap.add_argument("--update-pyright-baseline", action="store_true",
                    help="确认新的 pyright 条目是误报后，重写基线")
    a = ap.parse_args()

    tests = sorted((ROOT / "tests").glob("test_*.py"))
    changed = changed_files(a.changed) if a.changed else set()
    slow = {n for n, pats in SLOW_FOR.items() if a.all or _hit(changed, pats)}
    tests = [t for t in tests if t.name not in SLOW_FOR or t.name in slow]

    before = snapshot()
    t0 = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="cl_check_home_") as home, \
            ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 4)) as ex:
        # 只读样本：`test_extract` 读真课件 `~/UCD`（写全在 tempfile）。隔离 HOME 里给它一个软链，
        # 不然它在这里永远「缺样本跳过」，等于没跑。
        if (ucd := pathlib.Path.home() / "UCD").is_dir():
            (pathlib.Path(home) / "UCD").symlink_to(ucd)
        futs =[ex.submit(run_test, t, pathlib.Path(home)) for t in tests]
        static = None if a.no_static else ex.submit(
            lambda: (run_ruff(), run_pyright(a.update_pyright_baseline)))
        results = [f.result() for f in futs]
        ruff, pyright = static.result() if static else (("skip", "--no-static"),) * 2
    leaked = snapshot_diff(before, snapshot())

    bad = []
    print(f"\n测试（{len(results)} 个文件，HOME 已隔离）")
    for name, rc, dt, out in sorted(results, key=lambda r: (r[1] == 0, r[0])):
        mark = "✅" if rc == 0 else "⚪" if rc == SKIP_RC else "❌"
        if mark == "❌":
            bad.append(name)
        if mark != "✅":
            tail = "\n".join(out.strip().splitlines()[-8:])
            print(f"  {mark} {name}  rc={rc}  {dt:.1f}s\n" + "\n".join("      " + ln for ln in tail.splitlines()))
    print(f"  ✅ 其余 {sum(r[1] == 0 for r in results)} 个通过")

    for label, (st, msg) in (("ruff", ruff), ("pyright（两条规则）", pyright)):
        mark = {"ok": "✅", "skip": "⚪", "fail": "❌"}[st]
        print(f"{mark} {label}：{msg}" if st != "fail" else f"{mark} {label}：\n{msg}")
        if st == "fail":
            bad.append(label)

    if leaked:
        bad.append("写端泄漏")
        print("❌ 写端泄漏 —— 跑测试期间这些真文件变了（正在上课的话是误报）：")
        for ln in leaked[:20]:
            print("    " + ln)
    else:
        print("✅ 写端：sessions/、~/.classlive/、Lectures/、git status 都没变")

    if a.changed is not None:
        todo = [msg for pats, msg in MANUAL_FOR if _hit(changed, pats)]
        print(f"\n改动 {len(changed)} 个文件（相对 {a.changed}）；补跑的慢测试：{sorted(slow) or '无'}")
        if todo:
            print("还要人去跑（花钱 / 真跑 / 手验，这里不自动跑）：")
            for m in todo:
                print("  · " + m)

    print(f"\n{'❌ 不通过：' + '、'.join(bad) if bad else '✅ 全部通过'}（{time.monotonic() - t0:.1f}s）")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
