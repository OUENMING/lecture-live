#!/usr/bin/env python3
"""断网渲染检查（第六轮第 6 条）：详细版 HTML **没有外网**时公式也要渲染出来。

用法（仓库根，用 App 自带的 python）：

    ClassLive.app/Contents/MacOS/python scripts/note_eval_katex.py \
        <评测 out>/<节名>/note_data.json [更多…]
    # 把结果写回 run JSON（报告里那一栏 `katex_offline` 就是它）：
    ClassLive.app/Contents/MacOS/python scripts/note_eval_katex.py --fill <out>/runs/final.json

判据两层（实现只有这一份；`tests/test_note_offline.py` 走同一份）：

① **静态**：页面里没有任何外链子资源（`src=` / `href=` 里不许有 `http(s)://`）——
   KaTeX 是**仓库内副本**装到笔记旁边、相对引用（`vendor/katex/<版本>`）。
② **动态**：用本机 Playwright 的 `chrome-headless-shell`，`--proxy-server=127.0.0.1:9`
   （死端口 = 全断）打开页面，DOM 里必须出现 `class="katex"`
   —— 本地副本 + 本地字体真把公式渲染出来了，而不是退回 TeX 源码。

⚠️ 两种"跳过"要分清：
   · 机器上没有 headless shell → `ok: "static"`（只跑了 ①，**不算通过也不算失败**）；
   · 有 shell 但页面里有外链 / 公式没渲染 → `ok: false`（**真判红**，进程退出码 1）。
⚠️ 只写系统临时目录；headless 用 `--user-data-dir=<tmp>`，**不碰作者浏览器**的 profile。
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

#: 外链子资源（`src=` / `href=` 指向 http(s)）。页面里出现任何一条 = 断网时那一件没了。
_EXT_REF = re.compile(r'(?:src|href)="(https?://[^"]+)"')


def chrome_shell() -> pathlib.Path | None:
    """本机 Playwright 的 headless shell（没有 = 动态那层跳过）。

    ⚠️ 不能走 `pathlib.Path.home()`：`scripts/check.py` 把子进程的 `HOME` 指到临时
       目录（写端隔离）—— 那条路会永远找不到。取**登录用户**的真实家目录。
    """
    try:
        import pwd
        home = pathlib.Path(pwd.getpwuid(os.getuid()).pw_dir)
    except Exception:                                          # noqa: BLE001
        home = pathlib.Path.home()
    root = pathlib.Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
                        or home / "Library" / "Caches" / "ms-playwright")
    for exe in sorted(root.glob("chromium_headless_shell-*/chrome-headless-shell-*/"
                                "chrome-headless-shell")):
        if exe.exists():
            return exe
    return None


def external_refs(html: str) -> list[str]:
    """页面里的外链子资源（应为空 —— 断网自足的第一条）。"""
    return _EXT_REF.findall(str(html or ""))


def render_dir(data, dirpath: pathlib.Path, *, html_name: str = "note.html") -> tuple:
    """按**写端那条路**渲染：`install_assets`（装副本）→ `note_page`（纯渲染）。"""
    import note_html
    page = pathlib.Path(dirpath) / html_name
    data.html_name = html_name
    ok = note_html.install_assets(page)
    page.write_text(note_html.note_page(data), encoding="utf-8")
    return page, ok


def offline_check(html_path: pathlib.Path, *, shell: pathlib.Path,
                  timeout: int = 120) -> dict:
    """死代理打开页面 → DOM 里必须有 KaTeX 渲染结果（②那一层）。"""
    d = pathlib.Path(html_path).parent
    cmd = [str(shell), "--headless", "--disable-gpu", "--no-sandbox",
           # 死代理 = 全断（连本机回环也走不通）；有网也发不出去。
           "--proxy-server=127.0.0.1:9",
           f"--user-data-dir={d / 'chrome-profile'}",
           "--no-first-run", "--no-default-browser-check",
           "--virtual-time-budget=8000", "--dump-dom", pathlib.Path(html_path).as_uri()]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    dom = r.stdout
    return {"rc": r.returncode,
            "katex_spans": dom.count('class="katex"'),
            "katex_html": "katex-html" in dom,
            "raw_tex_left": 'class="tex"' in dom,
            "dom_bytes": len(dom),
            "err": (r.stderr or "")[-300:] if r.returncode else ""}


def check_data_json(path: pathlib.Path, *, shell: pathlib.Path | None) -> dict:
    """一份 `note_data.json` → 断网检查结果（静态 + 动态）。"""
    import dataclasses
    import note_render
    raw = json.loads(pathlib.Path(path).read_text("utf-8"))
    data = note_render.NoteData(**{k: v for k, v in raw.items()
                                   if k in {f.name for f in dataclasses.fields(note_render.NoteData)}})
    out = {"data": str(path), "ok": False, "external_refs": [], "install_assets": None,
           "tex_spans": 0, "dynamic": None}
    with tempfile.TemporaryDirectory(prefix="katex_check_") as d:
        page, ok = render_dir(data, pathlib.Path(d))
        html = page.read_text(encoding="utf-8")
        out["install_assets"] = bool(ok)
        out["external_refs"] = external_refs(html)
        out["tex_spans"] = html.count('class="tex"')
        if not ok or out["external_refs"]:
            return out                                      # ①没过，动态那一层不用跑
        if shell is None:
            out["dynamic"] = "no-shell"
            out["ok"] = "static"                            # 缺本机样本：不算过也不算败
            return out
        if out["tex_spans"] == 0:
            # ⚠️ 这一节**根本没有公式**（模型没写 `$…$`，兜底也没认出表达式）——
            #    "渲染不出来"这句话对它不适用：没有东西可渲染 ≠ 断网时坏了。
            #    （真公式渲染失败是另一种形状：`raw_tex_left` 为 True。）
            out["dynamic"] = "no-math"
            out["ok"] = True
            return out
        dyn = offline_check(page, shell=shell)
        out["dynamic"] = dyn
        out["ok"] = bool(dyn["rc"] == 0 and dyn["katex_spans"] and not dyn["raw_tex_left"])
    return out


def _fill(runs_path: pathlib.Path, results: dict, *, out_root: pathlib.Path) -> int:
    """把结果写回 `runs/<label>.json` 与各节的 `metrics.json`（报告那一栏读它）。"""
    runs = json.loads(runs_path.read_text("utf-8"))
    n = 0
    for key, m in (runs.get("sections") or {}).items():
        d = out_root / key / "note_data.json"
        if not d.exists() or key not in results:
            continue
        m["katex_offline"] = results[key]["ok"]
        n += 1
        mp = out_root / key / "metrics.json"
        if mp.exists():
            mm = json.loads(mp.read_text("utf-8"))
            mm["katex_offline"] = results[key]["ok"]
            mp.write_text(json.dumps(mm, ensure_ascii=False, indent=1), encoding="utf-8")
    runs_path.write_text(json.dumps(runs, ensure_ascii=False, indent=1), encoding="utf-8")
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("data", nargs="*", help="note_data.json（或装了它们的目录）")
    ap.add_argument("--out", default=None, help="评测输出目录（自动找 */note_data.json）")
    ap.add_argument("--fill", default=None, help="把结果写回这个 runs/*.json")
    ap.add_argument("--static-only", action="store_true", help="不跑 headless（只查外链）")
    args = ap.parse_args()
    paths: list[pathlib.Path] = []
    for p in args.data:
        p = pathlib.Path(p)
        paths += sorted(p.glob("*/note_data.json")) if p.is_dir() else [p]
    if args.out:
        paths += sorted(pathlib.Path(args.out).glob("*/note_data.json"))
    if args.fill and not (paths or args.out):
        # `--fill` 的路是自足的：run JSON 在哪，产物目录就在它旁边（`out/<节>/note_data.json`）。
        paths += sorted(pathlib.Path(args.fill).resolve().parent.parent.glob("*/note_data.json"))
    if not paths:
        ap.error("给几个 note_data.json，或 --out <评测输出目录>，或 --fill <runs/*.json>")
    shell = None if args.static_only else chrome_shell()
    if shell is None and not args.static_only:
        print("⏭ 本机没找到 Playwright 的 chrome-headless-shell —— 只跑静态那一层", flush=True)
    results: dict = {}
    bad = 0
    for p in paths:
        got = check_data_json(p, shell=shell)
        key = p.parent.name
        results[key] = got
        flag = {True: "✅", False: "❌", "static": "⏭"}[got["ok"]]
        extra = ""
        if got["ok"] is False:
            bad += 1
            extra = f"（外链 {got['external_refs'][:3]} · 动态 {got['dynamic']}）"
        elif got["ok"] == "static":
            extra = "（只查了外链）"
        elif got["dynamic"] == "no-math":
            extra = "（这一节没有公式，动态那层不适用）"
        else:
            extra = (f"（公式 {got['dynamic']['katex_spans']}/{got['tex_spans']} 处 · "
                     f"DOM {got['dynamic']['dom_bytes']}B）")
        print(f"{flag} {key} {extra}", flush=True)
    if args.fill:
        n = _fill(pathlib.Path(args.fill), results,
                  out_root=pathlib.Path(args.fill).resolve().parent.parent)
        print(f"✅ 写回 {args.fill}（{n} 节）", flush=True)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
