#!/usr/bin/env python3
"""F11 §5 跑量器：`tests/fixtures/notes_bench/*.md` 逐片生成笔记 → 打印每片统计。

    ClassLive.app/Contents/MacOS/python scripts/notes_bench.py [名字…] [--limit N]

⚠️ **花钱、要网**（test1 通行码）→ **不**进 `scripts/check.py`。
⚠️ 隔离：`HOME` 指到 `--home`（默认 `/private/var/folders/.../opencode/notesbench-home`），
   里面有 `test-key` + 已登记 `install-id` 的**副本**；`OBSIDIAN_VAULT` 指向临时目录。
   真 `~/.classlive` 只读（跑前跑后给指纹，变了就报）。
不用真 vault、不写 sessions/、不部署任何东西。

每片统计：blocks（节数）· program-verified %（有 program 的例题里程序跑通的比例）·
dropped（组件上限/去重/返修撤）· downgraded（§3 静默降级）· flags（机械 mismatch 数）·
unlabeled（例题标题没标签的数）。本文不调用 Jev（省预算；支持性核对的降级计数见 obsidian_writer 台账行）。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import pathlib
import shutil
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "notes_bench"


def _sig(p: pathlib.Path) -> dict:
    h = hashlib.sha256()
    n = 0
    for f in sorted(x for x in p.rglob("*") if x.is_file()):
        st = f.stat()
        h.update(str(f.relative_to(p)).encode())
        h.update(str(st.st_size).encode())
        h.update(str(st.st_mtime_ns).encode())
        n += 1
    return {"mtime": int(p.stat().st_mtime), "sha": h.hexdigest()[:12], "files": n}


def _unlabeled(paragraph: dict) -> int:
    n = 0
    for s in (paragraph.get("sections") or []):
        ex = s.get("example") or {}
        t = str(ex.get("title") or "")
        if (ex.get("prompt") or ex.get("steps")) and not t.startswith(("🎙", "✏️", "📖")):
            n += 1
    return n


def _prog_stats(review: dict) -> tuple:
    tot = ok = 0
    for s in (review.get("sections") or []):
        ex = s.get("example") or {}
        if not (isinstance(ex.get("program"), list) and ex.get("program")):
            continue
        tot += 1
        rows = note_math.program_rows(s)
        if rows and not any(r.get("status") == "mismatch" for r in rows):
            ok += 1
    return ok, tot


def run_one(path: pathlib.Path, chat) -> dict:
    import note_math
    import note_v2
    from obsidian_writer import ObsidianWriter

    entries = ObsidianWriter._parse(path.read_text(encoding="utf-8", errors="replace"))
    if not entries:
        return {"name": path.name, "blocks": 0, "note": "空"}
    review = note_v2.review(entries, chat=chat, domain="")
    if not review:
        return {"name": path.name, "blocks": 0, "note": "生成失败"}
    # 收尾链里**机械**的那几步（不额外花调用）
    review, drop_cmp, _ = note_v2.clamp_components(review)
    review, drop_dup, _ = note_v2.dedupe_components(review)
    review, rep, _ = note_v2.repair_exercises(review, chat=chat, entries=entries)
    scrubbed, removed, _ = note_guards.scrub(review)
    dropped = len(drop_cmp) + len(drop_dup) + sum(int(x.get("dropped") or 0)
                                                  for x in rep if isinstance(x, dict))
    rows = note_math.check_review(scrubbed)
    flags = sum(1 for r in rows if r.get("status") == "mismatch")
    ok, tot = _prog_stats(scrubbed)
    return {"name": path.name, "blocks": len(scrubbed.get("sections") or []),
            "prog_ok": ok, "prog_tot": tot, "dropped": dropped,
            "repaired": sum(int(x.get("repaired") or 0) for x in rep if isinstance(x, dict)),
            "flags": flags, "unlabeled": _unlabeled(scrubbed),
            "removed": len(removed)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="*", help="只跑这些 fixture 名（默认全跑）")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--home", default=str(pathlib.Path(
        "/private/var/folders/g1/7y1s2wp91txfzbj8ybmxqdb00000gn/T/opencode/notesbench-home")))
    args = ap.parse_args()

    real = pathlib.Path.home()
    before = _sig(real / ".classlive")
    home = pathlib.Path(args.home)
    (home / ".classlive").mkdir(parents=True, exist_ok=True)
    for f in ("test-key", "install-id", "jev-key"):
        src = real / ".classlive" / f
        if src.exists():
            shutil.copy2(src, home / ".classlive" / f)
            os.chmod(home / ".classlive" / f, 0o600)
    vault = home / "vault"
    vault.mkdir(exist_ok=True)
    os.environ["HOME"] = str(home)
    os.environ["OBSIDIAN_VAULT"] = str(vault)
    os.environ["CLASSLIVE_NO_AUTO_UPDATE"] = "1"
    sys.path.insert(0, str(ROOT))

    global note_guards, note_math
    import note_guards                                            # noqa: E402
    import note_math                                              # noqa: E402
    import llm_client                                             # noqa: E402
    from cloud_translator import load_test_key                   # noqa: E402

    key = load_test_key()

    def chat(system, user, max_tokens):
        return llm_client.chat_json(key, "deepseek-chat", system, user,
                                    max_tokens=max_tokens, temperature=0.3, timeout=300,
                                    effort="low")

    files = sorted(FIX.glob("*.md"))
    if args.names:
        files = [f for f in files if f.name in args.names or f.stem in args.names]
    if args.limit:
        files = files[:args.limit]
    print(f"{len(files)} 片 · key {key[:6]}… · HOME={home}\n")
    print(f"{'slice':30} {'blocks':>6} {'prog':>7} {'repair':>6} {'drop':>5} "
          f"{'flags':>5} {'unlab':>5} {'rm':>4}")
    tot = {"blocks": 0, "ok": 0, "pgt": 0, "drop": 0, "flags": 0}
    t0 = time.monotonic()
    for f in files:
        try:
            r = run_one(f, chat)
        except Exception as e:                                    # noqa: BLE001
            print(f"{f.name:30} 炸了 {type(e).__name__}: {str(e)[:40]}")
            continue
        prog = f"{r.get('prog_ok', 0)}/{r.get('prog_tot', 0)}"
        print(f"{r['name']:30} {r.get('blocks', 0):>6} {prog:>7} {r.get('repaired', 0):>6} "
              f"{r.get('dropped', 0):>5} {r.get('flags', 0):>5} {r.get('unlabeled', 0):>5} "
              f"{r.get('removed', 0):>4}")
        tot["blocks"] += r.get("blocks", 0)
        tot["ok"] += r.get("prog_ok", 0)
        tot["pgt"] += r.get("prog_tot", 0)
        tot["drop"] += r.get("dropped", 0)
        tot["flags"] += r.get("flags", 0)
    print(f"\n合计 {len(files)} 片 · 节 {tot['blocks']} · program 跑通 {tot['ok']}/{tot['pgt']} "
          f"· dropped {tot['drop']} · flags {tot['flags']} · {time.monotonic() - t0:.0f}s")
    after = _sig(real / ".classlive")
    print(f"真 ~/.classlive 前后：" + ("未动 ✅" if after == before
                                          else f"⚠️ 变了 {before} → {after}"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
