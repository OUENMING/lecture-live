#!/usr/bin/env python3
"""`.claude/hooks/pre_bash.py` 的判据 —— 「没作者点头不许推」这道护栏本身还拦得住吗。

    ClassLive.app/Contents/MacOS/python tests/test_hooks.py

## 为什么值得单独钉

它是**护栏**：拦不住的时候不会有任何症状 —— 推送照常成功，而作者并不知道。
所以它和别的代码不一样：**它的失效是静默的**，只有主动去试才会发现。

## ⚠️ 本文件钉住一个**实测确认过的绕过**（2026-10-12 全仓 OCR 审查指出）

原实现只看「整条命令文本里出现过 `CLASSLIVE_OWEN_OK=1` / `--dry-run`」，于是三条都能绕：

```
git push --dry-run origin main && git push origin main   # 后半段真的推
git push origin main # CLASSLIVE_OWEN_OK=1               # 写在注释里
echo CLASSLIVE_OWEN_OK=1; git push origin main           # 写在别的段里
```

修法：**按 `&&` / `||` / `;` / `|` / 换行切段，逐段判** —— 放行令牌要贴着它要放行的那一段。

⚠️ 本文件**不跑 main.py、不碰网络**：只把 JSON 从 stdin 喂给那个 hook 脚本，看退出码
（2 = 拦下 / 0 = 放行）。
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
HOOK = ROOT / ".claude" / "hooks" / "pre_bash.py"

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  {detail}" if detail else ""))


def rc(command: str, *, agent: str = "") -> int:
    """把一条命令喂给 hook，返回它的退出码。**只读** —— hook 自己不做任何事。"""
    ev = {"tool_input": {"command": command}}
    if agent:
        ev["agent_id"] = agent
    p = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(ev),
                       capture_output=True, text=True, timeout=20)
    return p.returncode


def main() -> int:
    print("\n--- ① 该拦的：推送 / tag / release ---")
    # 改坏：把 `block(...)` 那行删掉 -> 这几条红。
    for c in ("git push origin main",
              "git push",
              "git tag -a v3.9.3 -m x",
              "gh release create v3.9.3"):
        _r = rc(c)
        check(f"⛔ 拦下：{c}", _r == 2, f"rc={_r}")

    print("\n--- ② 该放的：作者点头了 / 只读 ---")
    for c in ("CLASSLIVE_OWEN_OK=1 git push origin main",
              "git push --dry-run origin main",
              "git status --porcelain",
              "git tag -l v3.9.3",                 # 只读的 tag 查询
              "git log --oneline -3"):
        _r = rc(c)
        check(f"✅ 放行：{c}", _r == 0, f"rc={_r}")

    print("\n--- ③ ⭐⭐ 三条**实测绕得过去**的写法（本轮的修复对象）---")
    # ⚠️ 这三条是 2026-10-12 实测确认能绕过旧实现的。改回去 -> 三条一起红。
    check("⭐⭐ `--dry-run && 真推`：后半段必须拦（旧实现整条放行）",
          rc("git push --dry-run origin main && git push origin main") == 2)
    check("⭐⭐ 令牌写在**注释**里不算数",
          rc("git push origin main # CLASSLIVE_OWEN_OK=1") == 2)
    check("⭐⭐ 令牌写在**别的段**里不算数",
          rc("echo CLASSLIVE_OWEN_OK=1; git push origin main") == 2)

    print("\n--- ④ 令牌**贴着**它要放行的那一段，仍然算数 ---")
    check("✅ `cd … && CLASSLIVE_OWEN_OK=1 git push …` 放行",
          rc("cd /tmp && CLASSLIVE_OWEN_OK=1 git push origin main") == 0)

    print("\n--- ⑤ 子代理不许跑 main.py / cl（别把这条也弄坏）---")
    check("⛔ 子代理跑 main.py 被拦", rc("python main.py", agent="a-123") == 2)
    check("✅ 主会话跑 main.py 放行", rc("python main.py") == 0)
    # ⚠️ 主会话那条**本来就该放行** —— hook 只拦子代理。写在这里是防止有人
    #    "顺手把 agent 判据去掉"，那会让主会话也被拦（误拦比漏拦更伤工作流）。

    bad = [n for n, ok, _ in RESULTS if not ok]
    print("\n" + "=" * 60)
    print(f"{len(RESULTS) - len(bad)}/{len(RESULTS)} 通过")
    for n in bad:
        print(f"  ❌ {n}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
