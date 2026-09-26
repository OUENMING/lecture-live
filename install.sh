#!/bin/bash
# ClassLive 安装器 —— 让它变成"系统里能直接启动的 app"
#
#   ./install.sh              构建（需要时）+ 装。**幂等**，可重复跑
#   ./install.sh --check      只报告现状，不改任何东西
#   ./install.sh --uninstall  撤掉（**只删我们建的那个符号链接**）
#
# ## 这一步到底做了什么
#
# 在 `/Applications` 里建一个**符号链接**指向仓库里的 `ClassLive.app`。就这一件事。
#
# ⚠️ **为什么是符号链接，不是把 .app 拷过去**（2026-09-26 两轮调研 + 实测，详见
#    docs/PLAN-p1-app-launcher.md §3.11）：
#
#   · 拷过去的话，`git pull` 只更新仓库那份，`/Applications` 那份**静默变旧** ——
#     两处要同步，迟早漂移（这个项目自己警告过这个模式，见 cl-bg.py 的注释）。
#     符号链接**永远指向最新那份**，不存在同步问题。
#   · 发 DMG 让人下载更糟：实测**从被 quarantine 的 DMG 里拷出来的 .app 会沾上
#     quarantine**，于是在朋友的机器上（Gatekeeper 默认开着）被拦，而 macOS 15 起
#     「右键→打开」也绕不过去了 —— 要么花 $99 签名公证，要么让每个朋友去系统设置
#     手动放行。而 `git clone` / `curl` 这条路**天生不带 quarantine**。
#
# ## 为什么装 `/Applications` 而不是 `~/Applications`
#
# 两个都合法（本机 `~/Applications` 里就有 9 个 app）。但实测：
# **Finder 侧边栏默认只有 `/Applications`** —— 而"用户会在那里找"正是这么做的
# **唯一**真好处（Spotlight / `open -a` / 文件位置都不区分，实测过）。
# `/Applications` 是 `drwxrwxr-x root:admin`，admin 组**直接可写、不要密码**（实测）。
#
# ⚠️ 非管理员用户写不进去 → 自动退到 `~/Applications`（那时侧边栏没有，但至少能启动）。
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
APP="$HERE/ClassLive.app"
NAME="ClassLive.app"

say()  { printf '%s\n' "$*"; }
fail() { printf '❌ %s\n' "$*" >&2; exit 1; }

# ---------- 决定装到哪 ----------
# ⚠️ 先试 /Applications；写不进去才退到 ~/Applications。**不要**因为"可能写不进去"
#    就默认用 ~/Applications —— 那会让 99% 的人失去侧边栏里那个入口。
pick_dir() {
  if [ -w /Applications ]; then printf '%s' /Applications
  else printf '%s' "$HOME/Applications"; fi
}

TARGET_DIR="$(pick_dir)"
TARGET="$TARGET_DIR/$NAME"

# ---------- --check ----------
if [ "${1:-}" = "--check" ]; then
  say "ClassLive 安装现状"
  say "──────────────────────────────────"
  # ⚠️ 用 ${VAR} 括起来：变量后面紧跟中文时，bash 会吞掉全角字符的首字节当变量名。
  say "   .app 构建      $([ -d "$APP" ] && echo "就位（$(du -sh "$APP" | cut -f1)）" || echo '❌ 没构建 → 跑 ./install.sh')"
  if [ -L "$TARGET" ]; then
    _tgt="$(readlink "$TARGET")"
    say "   安装位置       $TARGET"
    say "   指向           $_tgt"
    if [ -d "$_tgt" ]; then
      say "   目标还在吗     ✅ 在"
      [ "$_tgt" = "$APP" ] && say "   是不是这一份   ✅ 是（当前仓库）" \
                           || say "   是不是这一份   ⚠️ 不是 —— 指向别的副本（仓库挪过位置？）"
    else
      say "   ⚠️ 目标不在了 —— 仓库挪过位置。重跑 ./install.sh 修好"
    fi
  elif [ -e "$TARGET" ]; then
    say "   ⚠️ $TARGET 存在，但**不是符号链接** —— 是别的东西，本脚本不会碰它"
  else
    say "   安装位置       未安装（跑 ./install.sh）"
  fi
  say "   open -a 能找到 $([ -d "$APP" ] && (open -Ra ClassLive 2>/dev/null && echo 是 || echo 否) || echo '（先构建）')"
  exit 0
fi

# ---------- --uninstall ----------
if [ "${1:-}" = "--uninstall" ]; then
  # ⚠️ **只删"是我们的符号链接"那一种**。指向别处的链接、或真 .app，一律不碰 ——
  #    这个脚本没有资格替用户决定"那个东西该不该删"。
  if [ -L "$TARGET" ]; then
    _tgt="$(readlink "$TARGET")"
    if [ "$_tgt" = "$APP" ]; then
      rm "$TARGET" && say "✅ 撤掉了 $TARGET"
      say "   （仓库里的 .app 没动；要删它：rm -rf ${APP}）"
    else
      fail "$TARGET 是符号链接但指向别处（${_tgt}）—— 不是我们建的，不删"
    fi
  elif [ -e "$TARGET" ]; then
    fail "$TARGET 不是符号链接（是真的 app 或别的东西）—— 不是我们建的，不删"
  else
    say "本来就沒装（$TARGET 不存在）"
  fi
  exit 0
fi

say "▶ 安装 ClassLive"
say "──────────────────────────────────"

# ---------- ① 确保 .app 已构建 ----------
# ⚠️ 复用 make-app.sh，**不在这里重写构建逻辑** —— 同一个道理：
#    两处各记一次，迟早漂移。（cl-bg.py 的注释里也写着这条。）
if [ ! -d "$APP" ]; then
  say "① .app 还没构建 —— 交给 make-app.sh"
  "$HERE/make-app.sh" || fail "构建失败"
else
  say "① .app 已就位（$(du -sh "$APP" | cut -f1)）"
fi

# ---------- ② 冲突检查 —— 这一步是**唯一有破坏性**的地方，必须最保守 ----------
say "② 检查 $TARGET …"
if [ -L "$TARGET" ]; then
  _cur="$(readlink "$TARGET")"
  if [ "$_cur" = "$APP" ]; then
    say "   ✅ 已经是我们要的链接（幂等，什么都不用做）"
    exit 0
  fi
  fail "$TARGET 是一个符号链接，但指向别处：
       $_cur
   不是我们建的，**不覆盖**。
   确认那个链接没用之后，自己删：rm \"$TARGET\""
elif [ -e "$TARGET" ]; then
  fail "$TARGET **已经存在，而且不是符号链接** ——
   那可能是：
     · 你（或别人）手动拷过去的 .app
     · 另一个 ClassLive 的安装
   **本脚本不会覆盖它。**
   确认可以删之后：rm -rf \"$TARGET\"，再重跑 ./install.sh"
fi
say "   位置空闲 ✅"

# ---------- ③ 建链接 ----------
say "③ 建符号链接 …"
mkdir -p "$TARGET_DIR" 2>/dev/null || true
ln -s "$APP" "$TARGET" || fail "建链接失败（$TARGET_DIR 不可写？）"
if [ "$TARGET_DIR" != "/Applications" ]; then
  say "   ⚠️ /Applications 不可写，装到了 $TARGET_DIR"
  say "      （Finder 侧边栏没有这一项，但 Spotlight 和「启动台」照常能用）"
fi

# ---------- ④ 自检 ----------
say "④ 自检 …"
_bad=0
chk() { if [ "$2" = 1 ]; then say "   ✅ $1"; else say "   ❌ $1"; _bad=1; fi; }

[ -L "$TARGET" ] && chk "链接在 $TARGET" 1 || chk "链接在 $TARGET" 0
[ "$(readlink "$TARGET")" = "$APP" ] && chk "指向当前仓库" 1 || chk "指向当前仓库" 0
[ -x "$TARGET/Contents/MacOS/python" ] && chk "透过链接能摸到可执行文件" 1 \
                                       || chk "透过链接能摸到可执行文件" 0
_mdls="$(mdls -name kMDItemContentType "$TARGET" 2>/dev/null | sed 's/.*= //')"
[ "$_mdls" = '"com.apple.application-bundle"' ] && chk "系统认它是 app bundle" 1 \
                                               || chk "系统认它是 app bundle（读到 ${_mdls}）" 0
open -Ra ClassLive >/dev/null 2>&1 && chk "open -a ClassLive 找得到" 1 \
                                   || chk "open -a ClassLive 找得到" 0

[ "$_bad" = 0 ] || fail "自检没过 —— 上面标 ❌ 的就是原因"

say ""
say "✅ 装好了"
say "──────────────────────────────────"
say "   现在可以："
say "     · 在 Spotlight / 启动台里搜 ClassLive 启动"
say "     · 或者把 $TARGET 拖到 Dock"
say "     · 或者 open -a ClassLive"
say ""
say "   ⚠️ 链接指向的是这个仓库（${HERE}）。"
say "      **仓库挪位置之后，链接会断** —— 重跑 ./install.sh 就好。"
say "      查现状：./install.sh --check"
