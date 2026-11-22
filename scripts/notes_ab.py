#!/usr/bin/env python3
"""笔记 A/B 跑量器：**同一节课***同一把钥匙**，一次只换一组旋钮，产出可比的笔记。

    ClassLive.app/Contents/MacOS/python scripts/notes_ab.py <会话.md> \
        --arm NAME --out DIR [--env K=V ...] [--effort low|high] [--deadline 90] [--dry-run]

⚠️ **花钱、要网**：一律走 `test1` 通行码（`~/.classlive/test-key`），
   **不**进 `scripts/check.py`（`MANUAL_FOR` 里也还没登记，见交回说明）。
⚠️ 隔离口径（比 `scripts/notes_bench.py` 更狠 —— 这里要跑真收尾链，会碰 courses/ 与 sessions/）：
   - `HOME=DIR/home`：里面只放 `test-key` / `install-id` / `jev-key` 的**副本**（0600）
     和 `~/.classlive/courses/` 的整棵 `copytree`（**只读源**，跑完不回收进真目录）。
   - `OBSIDIAN_VAULT=DIR/vault`；要跑的会话复制到 `DIR/sessions/`（**去掉** `.opus` /
     `.npz` / `.zip` / `.tar.gz`：音频和包对笔记没用，复制它们只会把磁盘吃掉）。
   - `CLASSLIVE_NO_AUTO_UPDATE=1`、`CLASSLIVE_NOTE_WORKER=0`（占线收尾 → 就地跑）。
   - `--env K=V` 逐个写进 `os.environ`（旋钮就走这里，别改源码）。
   - 跑前跑后对真 `~/.classlive` 与真 `sessions/` 各算一次指纹；变了 **exit 2**。
⚠️ 钥匙：**先 import 业务模块，再**把 `cloud_translator.load_api_key` 换成
   `lambda: load_test_key()`。`main.rebuild_note_for` 里是函数内
   `from cloud_translator import load_api_key`，所以**替换模块属性就生效**（脚本里断言过）。
   `load_test_key()` 拿不到 → 直接退出，**绝不回落**到作者自己的码。
⚠️ 已知缺口：`keypoints_fn=_kp.pick` 走 `keypoints._provider()` 的 token，隔离的 HOME
   里通常没有 → 「最值得记的几句」那节在 A/B 里会缺；**两组都缺**，不影响对比，
   但别拿它当质量结论。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import shutil
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
#: 对笔记无用的重文件：不复制。
SKIP_SUFFIX = (".opus", ".npz", ".zip", ".tar.gz")
#: 只复制这三个凭据（其余 ~/.classlive 内容一律不进隔离 HOME）。
CREDS = ("test-key", "install-id", "jev-key")


def _sig(p: pathlib.Path) -> dict:
    """目录指纹（同 `scripts/notes_bench.py`）：文件相对路径 + 大小 + mtime。"""
    h = hashlib.sha256()
    n = 0
    for f in sorted(x for x in p.rglob("*") if x.is_file()):
        st = f.stat()
        h.update(str(f.relative_to(p)).encode())
        h.update(str(st.st_size).encode())
        h.update(str(st.st_mtime_ns).encode())
        n += 1
    try:
        mtime = int(p.stat().st_mtime)
    except OSError:
        mtime = 0
    return {"mtime": mtime, "sha": h.hexdigest()[:12], "files": n}


def _copy_sessions(src_p: pathlib.Path, dst: pathlib.Path) -> list:
    """同名兄弟文件（除重文件）→ dst。**只用这份副本**跑。"""
    out = []
    for f in sorted(src_p.parent.glob(src_p.stem + ".*")):
        if not f.is_file() or f.name.endswith(SKIP_SUFFIX):
            continue
        shutil.copy2(f, dst / f.name)
        out.append(f.name)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("session", help="会话 .md（真 sessions/ 里的那份，只读）")
    ap.add_argument("--arm", required=True, help="这一臂的名字（写进 run.json）")
    ap.add_argument("--out", required=True, help="输出根目录（自成一个隔离世界）")
    ap.add_argument("--env", action="append", default=[], metavar="K=V",
                    help="额外环境变量，可重复（实验旋钮走这里）")
    ap.add_argument("--effort", choices=("low", "high"), default=None,
                    help="改 obsidian_writer.NOTE_EFFORT（只本进程）")
    ap.add_argument("--deadline", type=float, default=None,
                    help="改 obsidian_writer.NOTE_DEADLINE_S（只本进程）")
    ap.add_argument("--llm", choices=("worker", "magpie"), default="worker",
                    help="笔记调用走哪：worker=通行码 Worker（默认）；magpie=本机 magpie 网关 group/commad"
                         "（CommandCode 的 deepseek-v4.1-flash；Owen 2026-11-21 指定，Worker 上游 402 时用）")
    ap.add_argument("--magpie-model", default="group/commad",
                    help="--llm magpie 时的模型名（如 group/commad-low / group/commad-high：在 magpie 里把思考档钉死）")
    ap.add_argument("--dry-run", action="store_true",
                    help="做完全部隔离与钥匙替换，只打印，不跑、不发请求")
    args = ap.parse_args()

    src = pathlib.Path(args.session).resolve()
    if not src.is_file():
        print(f"✗ 没有会话文件：{src}")
        return 1
    real_home = pathlib.Path.home()
    real_cc, real_sess = real_home / ".classlive", src.parent
    sig_cc0, sig_sess0 = _sig(real_cc), _sig(real_sess)

    out = pathlib.Path(args.out).resolve()
    home = out / "home"
    isess = out / "sessions"
    vault = out / "vault"
    for d in (home / ".classlive", isess, vault):
        d.mkdir(parents=True, exist_ok=True)

    print(f"arm={args.arm}  会话={src.name}")
    print(f"隔离 HOME   = {home}")
    print(f"隔离 vault  = {vault}")
    print(f"隔离 sessions = {isess}")

    # ① 凭据（副本，0600）
    got = []
    for f in CREDS:
        s = real_cc / f
        if s.exists():
            shutil.copy2(s, home / ".classlive" / f)
            os.chmod(home / ".classlive" / f, 0o600)
            got.append(f)
    print(f"复制凭据   = {got or '（一个都没有）'}")
    # ② 课程清单（整棵；只读源）
    src_courses = real_cc / "courses"
    n_courses = 0
    if src_courses.is_dir():
        dst = home / ".classlive" / "courses"
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src_courses, dst)
        n_courses = sum(1 for _ in dst.rglob("*") if _.is_file())
    print(f"复制 courses = {n_courses} 个文件")
    # ③ 会话同名兄弟（除重文件）
    copied = _copy_sessions(src, isess)
    print(f"复制会话件 = {copied}")

    # ④ 环境（先设 HOME 再 import 业务模块：paths.py 在 import 期就定锚）
    os.environ["HOME"] = str(home)
    os.environ["OBSIDIAN_VAULT"] = str(vault)
    os.environ["CLASSLIVE_NO_AUTO_UPDATE"] = "1"
    os.environ["CLASSLIVE_NOTE_WORKER"] = "0"
    envs = {}
    for kv in args.env:
        if "=" not in kv:
            print(f"✗ --env 要 K=V：{kv}")
            return 1
        k, v = kv.split("=", 1)
        os.environ[k.strip()] = v
        envs[k.strip()] = v
    sys.path.insert(0, str(ROOT))
    import main                                    # noqa: E402  （先 import 业务模块）
    import obsidian_writer as ow                   # noqa: E402
    import cloud_translator as ct                  # noqa: E402

    # ⑤ 钥匙替换（必须在 import 之后）+ 断言
    ct.load_api_key = lambda *a, **k: ct.load_test_key()
    tk = ct.load_test_key()
    if not tk:
        print("✗ 拿不到 test key（~/.classlive/test-key）—— 不跑，绝不回落真 key")
        return 1
    if ct.load_api_key() != tk:                    # 断言：替换真的生效
        print("✗ 钥匙替换没生效（cloud_translator.load_api_key）")
        return 1
    print(f"key        = {tk[:6]}…（test key ✅ 替换生效）")
    if args.llm == "magpie":
        # 只换**笔记那条出口**（llm_client.chat_json）：base/头/模型名改指本机 magpie 网关；
        # 其余调用（翻译/术语等）照旧走 test key。网关 key 从 ~/.pi/agent/models.json 读（真 HOME）。
        import llm_client as _lc
        _mp = json.loads((real_home / ".pi/agent/models.json").read_text())["providers"]["magpie"]
        _orig_cj = _lc.chat_json
        # llm_client 里只有 chat_json 用 base_for/headers（其余模块各有自己的出口）→ 整进程替换，不来回换（线程安全）
        _lc.base_for = lambda _k: _mp["baseUrl"].rstrip("/")
        _lc.headers = lambda _k, _c="": {"Authorization": f"Bearer {_mp['apiKey']}",
                                         "Content-Type": "application/json"}

        def _cj(key, model, *a, **k):
            return _orig_cj(key, args.magpie_model, *a, **k)
        _lc.chat_json = _cj
        print(f"llm        = magpie {_mp['baseUrl']} · {args.magpie_model}（只换 chat_json）")

    # ⑥ 实验旋钮（只改本进程的模块属性）
    if args.effort:
        ow.NOTE_EFFORT = args.effort
    if args.deadline:
        ow.NOTE_DEADLINE_S = float(args.deadline)
    print(f"旋钮       = effort={ow.NOTE_EFFORT} deadline={ow.NOTE_DEADLINE_S}s env={envs}")

    run = {"arm": args.arm, "session_src": str(src), "session_copy": "",
           "out": str(out), "effort": ow.NOTE_EFFORT,
           "deadline": ow.NOTE_DEADLINE_S, "env": envs,
           "key_prefix": tk[:6], "dry_run": bool(args.dry_run),
           "copied_sessions": copied, "result": {}, "elapsed_s": 0.0,
           "note": "", "html": "", "ok": False}

    if args.dry_run:
        run["session_copy"] = str(isess / src.name)
        print("\n— dry-run：下面这本会跑的东西都**不跑** —")
        print(f"  main.rebuild_note_for({isess / src.name!s},\n"
              f"      vault={vault!s}, polish=False, log=print)")
        print(f"  预算：test1 通行码（{tk[:6]}…）· effort={ow.NOTE_EFFORT} "
              f"· deadline={ow.NOTE_DEADLINE_S}s")
    else:
        cp = isess / src.name
        run["session_copy"] = str(cp)
        t0 = time.monotonic()
        res = main.rebuild_note_for(cp, vault=str(vault), polish=False, log=print)
        run["elapsed_s"] = round(time.monotonic() - t0, 1)
        run["result"] = res if isinstance(res, dict) else {}
        run["note"] = str(run["result"].get("note") or "")
        if run["note"]:
            run["html"] = str(pathlib.Path(run["note"]).with_suffix(".html"))
        run["ok"] = bool(run["result"].get("ok"))
    (out / "run.json").write_text(json.dumps(run, ensure_ascii=False, indent=2) + "\n",
                                  encoding="utf-8")
    print(f"\nrun.json → {out / 'run.json'}")
    if not args.dry_run:
        print(f"结果：ok={run['ok']} {run['elapsed_s']}s · note={run['note'] or '（无）'}")
        if run["note"] and os.path.exists(run["note"]):
            print(f"  {os.path.getsize(run['note'])} B · {run['note']}")

    # ⑦ 真目录指纹前后
    bad = False
    for name, p, before in (("~/.classlive", real_cc, sig_cc0),
                            (f"sessions/ ({real_sess.name})", real_sess, sig_sess0)):
        after = _sig(p)
        if after != before:
            bad = True
            print(f"⚠️ 真 {name} 变了：{before} → {after}")
        else:
            print(f"真 {name} 未动 ✅ {after['files']} 文件 sha={after['sha']}")
    return 2 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
