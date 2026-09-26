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

# ⚠️ `pwd -P` 取**物理**路径 —— 要和下面 `readlink` 拿到的原样字符串对得上才能比。
#    不规范化的话：仓库路径里含符号链接、或 `/tmp` vs `/private/tmp` 这种差异，
#    都会让路径比较判错 → `--check` 误报「指向别的副本」，
#    `--uninstall` 更会拒绝删掉本属于自己的那个链接。
HERE="$(cd "$(dirname "$0")" && pwd -P)"
APP="$HERE/ClassLive.app"
NAME="ClassLive.app"

say()  { printf '%s\n' "$*"; }
fail() { printf '❌ %s\n' "$*" >&2; exit 1; }

# 比较两个路径是不是同一个地方（都先规范化成物理路径）。
same_as_app() {
  local _t="$1" _real
  _real="$(cd "$(dirname "$_t")" 2>/dev/null && pwd -P)/$(basename "$_t")" || return 1
  [ "$_real" = "$APP" ]
}

# ⚠️⚠️ 参数白名单必须排在**任何会写盘的分支之前**。
#    原来只认 `--check` / `--uninstall`，其余参数（`--help`、拼错的 `--chek`、
#    多余的第二个参数）会**静默落进默认分支** —— 也就是真的执行
#    「构建 + 在 /Applications 建链接」。一个只想看帮助的人会平白装一个 app 上去。
case "${1:-}" in
  ""|--check|--uninstall) ;;
  *) fail "不认识的参数：$1
   可用：./install.sh             构建（需要时）+ 安装
         ./install.sh --check     只报告现状，不改任何东西
         ./install.sh --uninstall 撤掉我们建的那个符号链接" ;;
esac
[ "$#" -le 1 ] || fail "参数太多（只接受一个）：$*"

# ---------- 决定装到哪 ----------
# ⚠️ 先试 /Applications；写不进去才退到 ~/Applications。**不要**因为"可能写不进去"
#    就默认用 ~/Applications —— 那会让 99% 的人失去侧边栏里那个入口。
pick_dir() {
  if [ -w /Applications ]; then printf '%s' /Applications
  # ⚠️ 用 `${HOME:?}` 而不是 `$HOME`：`set -u` 下 HOME 未定义时，`$HOME` 会让脚本
  #    以非零码**直接中断、且不经过 fail()** —— 用户拿到一个没有任何解释的退出。
  else printf '%s' "${HOME:?HOME 未设置，无法确定回退安装目录}/Applications"; fi
}

TARGET_DIR="$(pick_dir)"
TARGET="$TARGET_DIR/$NAME"

# ---------- --check ----------
if [ "${1:-}" = "--check" ]; then
  say "ClassLive 安装现状"
  say "──────────────────────────────────"
  # ⚠️ 攒一个「有没有问题」，最后拿它当退出码。原来无论现状多坏都 `exit 0`，
  #    于是 `if ./install.sh --check; then`、CI、doctor.py 全都失明 ——
  #    而「假装一切正常」恰恰是这类脚本最该避免的。
  _bad=0
  if [ -d "$APP" ]; then
    say "   .app 构建      ✅ 就位（$(du -sh "$APP" | cut -f1)）"
  else
    say "   .app 构建      ❌ 没构建 → 跑 ./install.sh"; _bad=1
  fi
  if [ -L "$TARGET" ]; then
    _tgt="$(readlink "$TARGET")"
    say "   安装位置       $TARGET"
    say "   指向           $_tgt"
    if [ -d "$_tgt" ]; then
      say "   目标还在吗     ✅ 在"
      if same_as_app "$_tgt"; then
        say "   是不是这一份   ✅ 是（当前仓库）"
      else
        say "   是不是这一份   ⚠️ 不是 —— 指向别的副本（仓库挪过位置？）"; _bad=1
      fi
    else
      say "   ⚠️ 目标不在了 —— 仓库挪过位置。重跑 ./install.sh 修好"; _bad=1
    fi
  elif [ -e "$TARGET" ]; then
    say "   ⚠️ $TARGET 存在，但**不是符号链接** —— 是别的东西，本脚本不会碰它"; _bad=1
  else
    say "   安装位置       未安装（跑 ./install.sh）"; _bad=1
  fi
  # ⚠️ 这里**不能**用 `open -Ra ClassLive`：man 原文里 `-R` 是
  #    "Reveals the file(s) in the Finder **instead of opening** them"，
  #    而 `-a` 是 "the application to use for **opening** the file" —— 两者语义互斥，
  #    而本分支号称「只报告现状，不改任何东西」。
  #    改用 mdfind：不启动 app，只查 Spotlight 索引。
  #    ⚠️ 刚建的链接可能还没被索引 → 这条**只提示、不计入 _bad**。
  if [ -d "$APP" ]; then
    _bid="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' \
              "$APP/Contents/Info.plist" 2>/dev/null || true)"
    if [ -n "$_bid" ] && mdfind "kMDItemCFBundleIdentifier == '$_bid'" 2>/dev/null | grep -q .; then
      say "   系统认得出它   ✅ Spotlight 索引到了"
    else
      say "   系统认得出它   ⚠️ Spotlight 还没索引到（等几秒就好，不影响启动）"
    fi
  else
    say "   系统认得出它   （先构建）"
  fi
  [ "$_bad" = 0 ] || say "   ── 上面标 ⚠️ / ❌ 的地方需要处理"
  exit "$_bad"
fi

# ---------- --uninstall ----------
if [ "${1:-}" = "--uninstall" ]; then
  # ⚠️ **只删"是我们的符号链接"那一种**。指向别处的链接、或真 .app，一律不碰 ——
  #    这个脚本没有资格替用户决定"那个东西该不该删"。
  if [ -L "$TARGET" ]; then
    _tgt="$(readlink "$TARGET")"
    if same_as_app "$_tgt"; then
      # ⚠️ 不要写成 `rm ... && say ...`：`rm` 失败时 `&&` 短路跳过了成功提示，
      #    但后面的说明行照打、最后仍然 `exit 0` —— 用户以为删掉了，其实没删。
      rm "$TARGET" || fail "删不掉 ${TARGET}（权限不足？）—— 它还在那儿。"
      say "✅ 撤掉了 $TARGET"
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
# ⚠️ 判据**不只是「不存在才建」** —— 还要看构建戳记。
#    `cl update` 只拉代码、**不重建 .app**，所以 `make-app.sh` / `tools/make_icon.py`
#    / `VERSION` 的改动不会自己生效（图标就是这么丢的：代码更新了、Dock 上还是旧图）。
#    ⚠️ 判据交给 `make-app.sh --up-to-date` —— **只有那一份实现**，不在这里重算指纹。
#    ⚠️ 注意是 `--up-to-date` 且用 `!`：**出错也要倒向重建**。
#       若反过来问「stale 吗」，脚本自身出错会落成非零 → 被读成「最新」→ 静默跳过。
if [ ! -d "$APP" ]; then
  say "① .app 还没构建 —— 交给 make-app.sh"
  "$HERE/make-app.sh" || fail "构建失败"
elif ! _why="$("$HERE/make-app.sh" --up-to-date 2>&1)"; then
  say "① .app 该重建：${_why}"
  "$HERE/make-app.sh" || fail "重建失败"
else
  say "① .app 已就位（$(du -sh "$APP" | cut -f1)）"
fi

# ---------- ② 冲突检查 —— 这一步是**唯一有破坏性**的地方，必须最保守 ----------
say "② 检查 $TARGET …"
if [ -L "$TARGET" ]; then
  _cur="$(readlink "$TARGET")"
  if same_as_app "$_cur"; then
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
# ⚠️ 不要把 mkdir 的报错整个吞掉（原来是 `2>/dev/null || true`）：磁盘满、HOME 不可写、
#    路径被同名文件占住 —— 三种原因都要看到原文才知道怎么办，笼统一句
#    「建链接失败」等于没提示。
mkdir -p "$TARGET_DIR" || fail "建不出目录 ${TARGET_DIR}（磁盘满？权限？）"
# ⚠️ ② 的存在性检查与下一行之间**不是原子的**。若那个窗口里 $TARGET 变成了
#    一个**已存在的目录**（手动 mkdir、或另一次安装并发跑），`ln -s` 不会报错，
#    而是把链接建到那个目录**里面**（$TARGET/ClassLive.app）—— 静默的错误结构，
#    而且后面自检 `readlink "$TARGET"` 也读不到预期值。
[ -d "$TARGET" ] && [ ! -L "$TARGET" ] \
  && fail "$TARGET 在检查之后变成了目录 —— 中止，不覆盖"
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
same_as_app "$(readlink "$TARGET")" && chk "指向当前仓库" 1 || chk "指向当前仓库" 0
[ -x "$TARGET/Contents/MacOS/python" ] && chk "透过链接能摸到可执行文件" 1 \
                                       || chk "透过链接能摸到可执行文件" 0

# ⚠️ 原来这里比的是 `mdls` 的**精确字符串** `"com.apple.application-bundle"`
#    （连引号和空格一起比）：输出格式随系统版本会变，而且刚建出来的链接
#    **可能还没被 Spotlight 索引** → 读到 (null) → 在「链接其实已经建好」之后 fail，
#    留下一个「链接在、看着像失败」的半成品，提示还误导人。
#    改成查 bundle id，并且**只提示、不计入失败**（索引滞后是正常现象）。
_bid="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' \
          "$APP/Contents/Info.plist" 2>/dev/null || true)"
if [ -n "$_bid" ] && mdfind "kMDItemCFBundleIdentifier == '$_bid'" 2>/dev/null | grep -q .; then
  chk "系统认得出它（Spotlight 索引到了）" 1
else
  say "   ⚠️ Spotlight 还没索引到它 —— 等几秒会自己好，不影响启动"
fi

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
