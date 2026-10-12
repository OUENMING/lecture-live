#!/usr/bin/env python3
"""**隔离跑 `main.py`** —— 给验收用。写端全指到临时目录，**不碰作者的 sessions/ 、vault、~/.classlive/**。

    ClassLive.app/Contents/MacOS/python docs/experiments/run_isolated.py \
        <仓库根> <音频> <倍速> <输出目录> [main.py 的额外参数…]

⚠️⚠️ **为什么要专门写这个**（`CLAUDE.md` 那条「测试必须隔离写端」的硬规矩）：
   `obsidian_writer.SESSIONS` 与 `paths.STATE_ROOT` 都是**模块级常量、没有环境变量口子**，
   直接跑 `main.py` 会往真 `sessions/` 和真 `~/.classlive/` 写。
   **只换读端不换写端 = 会写坏真文件**（这条在 `term_notes.json` 上炸过一次）。

⚠️ **一共三个写端要换**（这一轮实测数出来的）：
   ① `obsidian_writer.SESSIONS`   ② `paths.STATE_ROOT`   ③ `main.py --vault`

⚠️⚠️ **换了这三处还不够** —— 它还会顺手改**持久状态**：
   · `~/.classlive/vault`（「记住的库」）—— 2026-09-30 **真被改坏过**：值变成了一个
     **已经删掉的临时目录**，而它只在「双击 `.app`、没有 shell 环境变量」那条路上咬人
     （笔记会被写进一个不存在的路径）。→ **跑完必须 `cp -a ~/.classlive/` 对拍**。
   · 仓库根的 `.update-seen` —— 每次跑都写当前 `VERSION`，**那是正常行为**，无害。
   → 前者靠 `backup-20260930-pre-ai-panel/` 里那份原值救回来的。

⚠️ **key 不显式给会静默没有**：`load_api_key()` 那条链最后落到 `<仓库>/.deepseek_key`，
   而它是 **gitignored** 的 —— 跑 `git worktree` 那种隔离副本时，那边**没有这个文件**
   → atom 不提取、翻译退回本地引擎，而**两边的结果就没有可比性了**
   （2026-09-30 实测踩到：第一次「接线前/后」对拍，接线前那版一条 atoms 都没写，
   看着像格式回归，其实是量具坏了）。
"""
import os
import pathlib
import shutil
import sys
import tempfile
import time

def _safe_outdir(raw: str) -> pathlib.Path | None:
    """把 `--outdir` 收敛成一个**允许被清空**的目录；不合格返回 `None`（调用方退出）。

    ⚠️⚠️ 为什么要有它（2026-10-12 全仓审查，核实过）：原来这里是
       `if outdir.exists(): shutil.rmtree(outdir)`，而 `outdir` 直接来自 `sys.argv[4]`，
       **一个校验都没有**。一个「隔离测试」脚本却具备 `rmtree($HOME)` / `rmtree(仓库根)` /
       `rmtree(/)` 的能力，手滑一次就是**不可逆**的数据销毁。
    ⚠️ 判据是**白名单**（必须住在一个叫 `ClassLive-测试残留` 的目录下，或在系统临时目录里），
       不是黑名单 —— 黑名单永远会漏一条，白名单漏了只是"用不了"，方向是安全的。
    """
    p = pathlib.Path(raw)
    if not raw.strip() or not p.is_absolute():
        return None
    try:
        p = p.resolve()
    except OSError:
        return None
    if p.parent == p or p == pathlib.Path.home():         # `/`、`$HOME` 本身
        return None
    if any(parent.name == "ClassLive-测试残留" for parent in (p, *p.parents)):
        return p
    if str(p).startswith(("/tmp/", "/private/tmp/", tempfile.gettempdir() + "/")):
        return p
    return None


repo, audio, speed = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
_outdir = _safe_outdir(sys.argv[4])
if _outdir is None:
    sys.exit(f"❌ 拒绝清空这个目录：{sys.argv[4]!r}\n"
             f"   本脚本会把它**递归删掉**，所以只接受两条路径：\n"
             f"   ① 名字叫 `ClassLive-测试残留` 的目录**里面**"
             f"（例：~/ClassLive-测试残留/2026-10-12）\n"
             f"   ② 系统临时目录里（$TMPDIR · /tmp · /private/tmp）")
outdir = _outdir
extra = sys.argv[5:]

if outdir.exists():
    shutil.rmtree(outdir)
for d in ("sessions", "classlive", "vault"):
    (outdir / d).mkdir(parents=True)

# ⚠️ key 从**主仓库**读，再塞进环境变量 —— 命令行里不会出现它（`ps` 看不到）。
_key = pathlib.Path("/Users/owen/lecture-live/.deepseek_key")
if _key.exists():
    os.environ.setdefault("DEEPSEEK_API_KEY", _key.read_text(encoding="utf-8").strip())

sys.path.insert(0, str(repo))
os.chdir(repo)

import capture                                                     # noqa: E402
import obsidian_writer                                             # noqa: E402
import paths                                                       # noqa: E402

obsidian_writer.SESSIONS = outdir / "sessions"                     # ① 写端
paths.STATE_ROOT = outdir / "classlive"                            # ② 写端

T0 = time.time()
MARK: dict = {}

# ---- 「音频播完」那一刻（§8.5-3 要量「播完到进程退出」）----
_is_done = capture.FileSource.is_done


def _done(self):
    r = _is_done(self)
    if r and "audio_done" not in MARK:
        MARK["audio_done"] = time.time() - T0
        print(f"⏱ __AUDIO_DONE__ t=+{MARK['audio_done']:.2f}s", flush=True)
    return r


capture.FileSource.is_done = _done

# ---- 每一次「推给界面」的时刻（§10.3 要量课务多快上屏）----
try:
    import live_summary

    class _Rec(live_summary.LiveSummarizer):
        def __init__(self, *a, **kw):
            em = kw.get("emit")

            def _wrapped(p):
                wall = time.strftime("%H:%M:%S") + f".{int((time.time() % 1) * 100):02d}"
                print(f"⏱ __EMIT__ [{wall}] t=+{time.time() - T0:.2f}s "
                      f"kind={(p or {}).get('kind')}", flush=True)
                return em(p) if em else None
            kw["emit"] = _wrapped
            super().__init__(*a, **kw)

    live_summary.LiveSummarizer = _Rec
except ImportError:
    pass                       # 接线前那版没有 live_summary，正常

import main as M                                                   # noqa: E402

# ---- 「冲刷等待」结束的那一刻（§8.5-3 要确认**没有等满 15s 上限**）----
_all_settled = M.all_settled


def _settled(*a, **kw):
    r = _all_settled(*a, **kw)
    if r and "settled" not in MARK:
        MARK["settled"] = time.time() - T0
        print(f"⏱ __SETTLED__ t=+{MARK['settled']:.2f}s", flush=True)
    return r


M.all_settled = _settled

M.sys.argv = ["main.py", "--source", "file", "--path", audio, "--speed", speed,
              "--ui", "terminal", "--vault", str(outdir / "vault"),    # ③ 写端
              "--course", "TESTISO", *extra]

try:
    M.main()
except SystemExit:
    pass

T = time.time() - T0
print(f"\n⏱ __ELAPSED__ {T:.1f} 秒")
if "audio_done" in MARK:
    print(f"⏱ __WRAPUP__ 播完到退出 {T - MARK['audio_done']:.1f} 秒")
