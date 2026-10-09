#!/bin/bash
# 让**分支的代码**跑在**你原来的数据**上 —— 给 worktree 补一份运行态布局（全是软链）。
#
#   ./scripts/link-runtime.sh              # 主目录默认 ~/lecture-live
#   ./scripts/link-runtime.sh /别的/主目录
#
# ## 为什么需要它
#
# `git worktree` 只给**被 git 跟踪**的东西。上课真正要的那些**都不在 git 里**
# （`.gitignore` 里 `sessions/` `glossary/` `.course` `.test-mode` `ClassLive.app/` …）：
#
#   · 没有 `ClassLive.app/` → `cl` 第一步就退出（找不到 `$PY`）
#   · 没有 `sessions/`      → `obsidian_writer.SESSIONS` 落在 worktree 里，
#                             课堂记录会**写到一个新目录**，`cl last` 也找不到
#   · 没有 `glossary/`      → 该课术语表静默失效（退回只用公共表）
#   · 没有 `.course`        → 这节课记到 `LECTURE` 名下
#
# 所以这里把主目录那几样**软链**过来：**代码是分支的，数据是你原来的那一份**
# （也就没有"两份数据不同步"这回事）。
#
# ⚠️⚠️ 只**创建**软链，**绝不覆盖、绝不删除**任何已存在的东西（与 [[never-delete-user-session-data]]
#    同一条规矩）。已经存在且不是本脚本建的那种软链，就**报出来跳过**，交给你看。
set -u
HERE="$(cd "$(dirname "$0")/.." && pwd)"
MAIN="${1:-${CL_BRANCH_MAIN:-$HOME/lecture-live}}"

if [ ! -d "$MAIN" ]; then echo "❌ 找不到主目录: $MAIN" >&2; exit 1; fi
MAIN="$(cd "$MAIN" && pwd -P)"
if [ "$HERE" = "$MAIN" ]; then echo "❌ 这里就是主目录，不需要补（本脚本给 worktree 用）" >&2; exit 1; fi
if [ ! -e "$MAIN/cl" ]; then echo "❌ $MAIN 看着不像 ClassLive 的安装目录（没有 cl）" >&2; exit 1; fi

#: 要链的名字。**目录**必须软链（上课要往里写）；**小状态文件**也软链，
#: 这样面板改 `.course` 时两边是同一份。
NAMES="ClassLive.app sessions glossary glossary.txt .course .test-mode .window .update-seen .deepseek_key term_notes.json assets"

made=0; skipped=0; missing=0
echo "主目录: $MAIN"
echo "本目录: $HERE"
echo
for n in $NAMES; do
  if [ -e "$HERE/$n" ] || [ -L "$HERE/$n" ]; then
    if [ -L "$HERE/$n" ] && [ "$(readlink "$HERE/$n")" = "$MAIN/$n" ]; then
      printf '  = 已经在位      %s\n' "$n"; skipped=$((skipped+1)); continue
    fi
    printf '  ⚠ 已存在，跳过  %s（不是本脚本建的软链 —— 自己看一眼再决定）\n' "$n"
    skipped=$((skipped+1)); continue
  fi
  if [ ! -e "$MAIN/$n" ]; then
    printf '  - 主目录也没有  %s\n' "$n"; missing=$((missing+1)); continue
  fi
  ln -s "$MAIN/$n" "$HERE/$n" || { printf '  ✗ 软链失败      %s\n' "$n"; continue; }
  printf '  ✓ 已软链        %s -> %s\n' "$n" "$MAIN/$n"; made=$((made+1))
done

echo
echo "建了 ${made}，跳过 ${skipped}，主目录也没有 ${missing} 个。"
echo
# ---- 自检：三件必须成立的 ----
ok=1
[ -x "$HERE/ClassLive.app/Contents/MacOS/python" ] || { echo "❌ ClassLive.app 里的 python 不可执行"; ok=0; }
[ -w "$HERE/sessions" ] && [ -d "$HERE/sessions" ] || { echo "❌ sessions/ 不可写（课堂记录会写不进去）"; ok=0; }
if [ -r "$HERE/.course" ]; then echo "ℹ️ 当前课程 = $(cat "$HERE/.course")"; else echo "ℹ️ 还没设课程（会记到 LECTURE 名下）"; fi
[ "$ok" = 1 ] && echo "✅ 运行态就绪：现在可以直接跑 $HERE/cl"
exit 0
