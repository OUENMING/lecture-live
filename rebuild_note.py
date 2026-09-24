#!/usr/bin/env python3
"""从 sessions/ 会话文件重建 Obsidian 课堂笔记。

用途：ClassLive 收尾时没跑成（崩溃 / 强杀 / 忘记关），sessions/ 里有逐句日志
但 Lectures/ 里没有笔记。本脚本复用 obsidian_writer 自身的管道补生成。

不新建会话文件：先用 mode='no' 构造（__init__ 里 enabled=False 会跳过建文件），
再把 session_path 指到既有文件上。

用法： .venv/bin/python rebuild_note.py <会话文件> <课号> [--no-polish]
"""
import argparse
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cloud_translator import load_api_key          # noqa: E402
from obsidian_writer import ObsidianWriter         # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("session")
    ap.add_argument("course")
    ap.add_argument("--vault", default=os.environ.get(
        "OBSIDIAN_VAULT", os.path.expanduser("~/Obsidian/Vault")))
    ap.add_argument("--glossary", default=str(
        Path(__file__).resolve().parent / "glossary.txt"))
    ap.add_argument("--model", default="deepseek-flash")
    ap.add_argument("--no-polish", action="store_true")
    args = ap.parse_args()

    sess = Path(args.session)
    if not sess.exists():
        print(f"✗ 会话文件不存在: {sess}")
        return 1
    text = sess.read_text(encoding="utf-8")
    n = len(re.findall(r"^> \[!abstract\]", text, re.M))
    if n == 0:
        print("✗ 会话文件里没有任何句段，拒绝生成空笔记")
        return 1

    # 从会话文件名推日期，而不是用「今天」—— 补生成常常隔天才做
    m = re.match(r"(\d{4}-\d{2}-\d{2})_", sess.name)
    date = m.group(1) if m else None
    if not date:
        print(f"✗ 无法从文件名推日期: {sess.name}")
        return 1

    # ⚠️ 先校验 vault。下面会强制 `w.enabled = True` 绕过构造期的保护
    # (`enabled = bool(vault) and mode != "no"`), 所以 vault 为空时 close()
    # 里 `Path(self._vault)` 会抛 TypeError —— 用户只看到堆栈, 不知道是 vault 配错。
    # (2026-09-24 OCR 发现。)
    # 特别地: `os.environ.get("OBSIDIAN_VAULT", 默认)` 在变量**存在但为空**时
    # 返回空串而不是默认值, 所以这条路径真的会走到。
    if not args.vault:
        print("✗ --vault 是空的 —— 得知道笔记写到哪个 Obsidian 库。\n"
              "  例: --vault ~/Obsidian/Vault   或设环境变量 OBSIDIAN_VAULT\n"
              "  ⚠️ OBSIDIAN_VAULT 设成空串时**不会**回退到默认值。")
        return 1

    w = ObsidianWriter(vault=args.vault, course=args.course, mode="no",
                       api_key=load_api_key(None), model=args.model,
                       glossary_path=args.glossary, polish=not args.no_polish)
    # 接管成「已跑完的一节」
    w.enabled = True
    w.mode = "yes"
    w.session_path = sess
    w._course = args.course
    w._date = date
    w._n = n

    print(f"▶ 重建 {sess.name} → {args.course} · {date} · {n} 句")
    print(w.close())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
