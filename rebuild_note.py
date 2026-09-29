#!/usr/bin/env python3
"""从 sessions/ 会话文件重建 Obsidian 课堂笔记。

用途：ClassLive 收尾时没跑成（崩溃 / 强杀 / 忘记关），sessions/ 里有逐句日志
但 Lectures/ 里没有笔记。本脚本复用 obsidian_writer 自身的管道补生成。

不新建会话文件：先用 mode='no' 构造（__init__ 里 enabled=False 会跳过建文件），
再把 session_path 指到既有文件上。

用法： ClassLive.app/Contents/MacOS/python rebuild_note.py <会话文件> <课号> [--no-polish]
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
    # ⚠️⚠️ **默认值不能再写 `~/Obsidian/Vault`**（2026-09-29 修）—— 那是**废弃的**
    #    那个「用户从没选过」的兜底目录（见 `obsidian_writer.resolve_vault` 的 docstring：
    #    实测那个目录里躺着 9 个笔记，连 `.obsidian` 都没有）。
    #    改成 `None` + 走 `resolve_vault()` —— 与 `cl` / `main.py` **同一条路**
    #    （`--vault` → `$OBSIDIAN_VAULT` → `~/.classlive/vault` → 都没有就报错）。
    ap.add_argument("--vault", default=None)
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
    # ⚠️⚠️ **用真正的解析器数，别用前缀正则**（2026-09-29 修）。
    #    原来写的是 `re.findall(r"^> \[!abstract\]", text, re.M)` —— 只匹配**前缀**，
    #    而 `_TS`（真解析器）要求**完整时间戳**：
    #        `^> \[!abstract\] \d\d:\d\d:\d\d( ⭐ Exam Focus)?\s*$`
    #    → 手改过 / 写入被截断的文件（有抬头、时间戳坏了）会 `n > 0` 通过校验，
    #      而下面 `close()` 里 `_parse` 解析出 **0 条** → **正是这道保护要防的
    #      「生成空笔记」被绕过去了**。
    #    ⚠️ 读法唯一的定义点在 `obsidian_writer._parse` —— 别在这儿再写一份。
    n = len(ObsidianWriter._parse(text))
    if n == 0:
        print("✗ 会话文件里没有任何**能读懂的**句段，拒绝生成空笔记")
        return 1

    # 从会话文件名推日期，而不是用「今天」—— 补生成常常隔天才做
    m = re.match(r"(\d{4}-\d{2}-\d{2})_", sess.name)
    date = m.group(1) if m else None
    if not date:
        print(f"✗ 无法从文件名推日期: {sess.name}")
        return 1

    # ⚠️ 先校验 vault。下面会强制 `w.enabled = True` 绕过构造期的保护
    # (`enabled = mode != "no"`, 见 `obsidian_writer.__init__`)。
    # ⚠️ 2026-09-29 起那两件事已经分开了：`enabled` 只管"要不要记录"，
    #    而"写不写笔记"由 `self._vault is not None` 判（`close()` 里那处早退）。
    #    所以 vault 为空时 `close()` 不再抛 `TypeError` 了 —— 它会**老老实实**
    #    说"没设库、不生成笔记"。这条校验留着是因为**重建这个脚本本来就要写笔记**，
    #    没有库它没有意义（不是防崩，是防无意义地白跑）。
    # 特别地: `os.environ.get("OBSIDIAN_VAULT", 默认)` 在变量**存在但为空**时
    # 返回空串而不是默认值, 所以这条路径真的会走到。
    if not args.vault:
        # ⚠️ 先看还有没有**记住过的**库（`~/.classlive/vault`）—— 与 `cl` 同一条路。
        from obsidian_writer import resolve_vault
        args.vault = resolve_vault(None)
    if not args.vault:
        print("✗ 找不到 Obsidian 库 —— 得知道笔记写到哪儿。\n"
              "  例: --vault ~/Obsidian/SecondBrain   或设环境变量 OBSIDIAN_VAULT\n"
              "  也可以先用 `cl` 跑一次（它会把选过的库记进 ~/.classlive/vault）。")
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
    try:
        print(w.close())
    except Exception as e:                                    # noqa: BLE001
        # ⚠️⚠️ **不许把裸堆栈甩给用户**（2026-09-29 修）。`close()` 才是真正
        #    跑精修 / 复习层 / 写盘的那一步，可能因 API key、模型调用、模板、IO
        #    抛异常 —— 而本脚本为**其它每一类失败**都给了可读中文提示 + 非零返回码，
        #    唯独这里让人吃一坨 traceback。风格要一致。
        print(f"✗ 重建失败：{type(e).__name__}: {str(e)[:200]}")
        print("  会话文件本身没动；修好原因后重跑即可。")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
