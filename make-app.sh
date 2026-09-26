#!/bin/bash
# 把仓库里的 venv 变成一个可以双击的 ClassLive.app
#
#   ./make-app.sh            # 构建（已存在就重建）
#   ./make-app.sh --check    # 只检查现状，不动任何东西
#
# ## 为什么是这个形状（不是普通的 .app 打包）
#
# 目标是**双击就能上课**，且 macOS 的麦克风授权框上写「ClassLive」而不是「python3.11」。
#
# ⚠️ 关键事实（2026-09-26 实测）：**macOS 判断 bundle 身份的方式，
#    是看可执行文件在不在 `<X>.app/Contents/MacOS/` 里。**
#    - 壳式做法（MacOS/ 放 shell 脚本，脚本 exec 外面的 venv python）
#      → NSBundle.mainBundle() 指向 Python 自己的 bundle
#      → 授权框写「python3.11」、Info.plist 的键全部不生效（LSUIElement 实测也没有）
#    - 把 venv 的**布局搬进** Contents/（bin→MacOS、lib→lib、pyvenv.cfg→Contents/）
#      → mainBundle() 变成我们的 → 授权框写「ClassLive」+ 我们的说明
#
# ⚠️ **是「搬布局」不是「拷一份 python 分布」**：搬完之后它仍然是一个真 venv
#    （pyvenv.cfg 在 Contents/），所以 `uv pip install` 照常能用 ——
#    不需要删 EXTERNALLY-MANAGED、不需要手工拷包。
#
# ⚠️ 不能用「软链接指向外面的 venv」代替搬布局 —— 实测：Python 会顺链解析，
#    sys.prefix 落回基础解释器，venv 的 site-packages 直接失效。
#
# 详见 docs/PLAN-p1-app-launcher.md §1.5
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
APP="$HERE/ClassLive.app"
BUILD="$HERE/.build"
PYVER="${CLASSLIVE_PYVER:-3.12}"
BUNDLE_ID="page.bldcam.classlive"

say()  { printf '%s\n' "$*"; }
fail() { printf '❌ %s\n' "$*" >&2; exit 1; }

# ---------- --check：只报告现状 ----------
if [ "${1:-}" = "--check" ]; then
  say "ClassLive.app 构建现状"
  say "──────────────────────────────────"
  if [ -d "$APP" ]; then
    say "✅ .app 存在         $(du -sh "$APP" | cut -f1)"
    # ⚠️ 单列这一条：符号链接的 CFBundleExecutable 会让 LaunchServices
    #    **静默拒绝启动**（双击完全没反应、无报错）。别的检查都看不出这个 ——
    #    `-e` / `-x` 都会**跟随**符号链接，所以上面那几行照样报"就位"。
    # ⚠️ 用 PlistBuddy **不用 `plutil -extract`**：
    #    ① key 不存在时 plutil 返回 1，而这里是 `set -e` 环境 —— 不接住的话
    #       `--check` 会在这一行**直接退出**，连"缺什么"都报不出来（最需要它报的时候）。
    #    ② 接住了也没用：**plutil 的报错信息会进 stdout**（`2>/dev/null` 挡不住），
    #       于是变量里装的是那句 error，`[ -n "$VAR" ]` 判成"有值"。
    #    PlistBuddy 读不到时只写 stderr、stdout 干净。2026-09-26 两个坑都踩过。
    _EXE_C="$(/usr/libexec/PlistBuddy -c "Print :CFBundleExecutable" \
      "$APP/Contents/Info.plist" 2>/dev/null || true)"
    if [ -n "$_EXE_C" ] && [ -L "$APP/Contents/MacOS/$_EXE_C" ]; then
      # ⚠️ 报了就**不再报"就位"** —— `-e` / `-x` 都会跟随符号链接，
      #    两条一起打出来会自相矛盾（"是符号链接" + "就位"），反而让人困惑。
      say "   ⚠️ MacOS/$_EXE_C   **符号链接** → 双击会没反应！重跑 ./make-app.sh"
    else
      say "   MacOS/python      $([ -e "$APP/Contents/MacOS/python" ] && echo 就位 || echo '❌ 缺')"
      [ -n "$_EXE_C" ] && say "   MacOS/$_EXE_C   真文件 ✅"
    fi
    say "   lib/              $([ -d "$APP/Contents/lib" ] && echo 就位 || echo '❌ 缺')"
    say "   pyvenv.cfg        $([ -f "$APP/Contents/pyvenv.cfg" ] && echo 就位 || echo '❌ 缺')"
    say "   Info.plist        $([ -f "$APP/Contents/Info.plist" ] && echo 就位 || echo '❌ 缺')"
    # 图标：Info.plist 里声明了、Resources 里也得真有那个文件，缺一不可。
    # ⚠️ 只查一边不够 —— CFBundleIconFile 指向不存在的文件时**不报错**，
    #    只是 Dock/Finder 上悄悄退回系统通用图标（"看着像没做"）。
    _ICON_N="$(/usr/libexec/PlistBuddy -c "Print :CFBundleIconFile" \
      "$APP/Contents/Info.plist" 2>/dev/null || true)"
    if [ -z "$_ICON_N" ]; then
      say "   图标              ⚠️ Info.plist 没声明 CFBundleIconFile"
    elif [ -f "$APP/Contents/Resources/$_ICON_N.icns" ]; then
      say "   图标              $_ICON_N.icns ✅"
    else
      say "   图标              ❌ 声明了 $_ICON_N 但 Resources/$_ICON_N.icns 不存在"
    fi
    if [ -x "$APP/Contents/MacOS/python" ]; then
      say "   能不能跑           $("$APP/Contents/MacOS/python" -c 'import sys;print(sys.version.split()[0])' 2>&1 | tail -1)"
      say "   mainBundle        $("$APP/Contents/MacOS/python" -c \
        'from Foundation import NSBundle as B;print(B.mainBundle().bundlePath())' 2>&1 | tail -1)"
    fi
  else
    say "⚪ .app 不存在 —— 跑 ./make-app.sh 构建"
  fi
  say ""
  say "旧布局（重构前的 .venv）："
  say "   .venv             $([ -d "$HERE/.venv" ] && du -sh "$HERE/.venv" | cut -f1 || echo '（无）')"
  exit 0
fi

# ---------- 前置检查 ----------
command -v uv >/dev/null 2>&1 || fail "找不到 uv。装它：https://docs.astral.sh/uv/"
[ -f "$HERE/requirements.txt" ] || fail "找不到 requirements.txt"

# ⚠️ 变量后面紧跟中文/全角标点时**必须写 `${VAR}`** ——
#    bash 在 UTF-8 locale 下会把全角字符的首字节当成标识符字符，
#    于是 `$PYVER）` 会被解析成「名为 PYVER）的变量」→ `set -u` 直接报 unbound。
#    （2026-09-26 实测踩到，报错信息是 `PYVER?: unbound variable`）
say "▶ 构建 ClassLive.app（Python ${PYVER}）"
say "──────────────────────────────────"

# ---------- ① 取一个**独立**的 python（不用 Homebrew 的）----------
# ⚠️⚠️ 必须用 uv **管理的**独立 python，不能用 Homebrew 的 framework 构建 ——
#     后者的真实可执行文件在 `Python.framework/…/Python.app` 里，
#     无论怎么包，`mainBundle()` 都落回它，授权框就永远改不成「ClassLive」。
#
# ⚠️ `uv python find 3.12` **不加 `--managed-python` 会返回项目自己的 .venv**
#     （而 .venv 正是基于 Homebrew python 建的）—— 2026-09-26 实测踩到，
#     当时自检的「mainBundle 是 .app」那条直接报了 ❌，才知道拿错了。
say "① 准备 Python ${PYVER} …"
uv python install "$PYVER" --managed-python >/dev/null 2>&1 || true
BASEPY="$(uv python find "$PYVER" --managed-python 2>/dev/null)" \
  || fail "拿不到 uv 管理的 Python ${PYVER}。跑一次：uv python install $PYVER"

# 前置检查：确认它不是 framework 构建（否则整个方案的前提不成立）
_BASE_REAL="$(cd "$(dirname "$BASEPY")" && pwd -P)/$(basename "$BASEPY")"
case "$_BASE_REAL" in
  *"/Frameworks/"*|*"/Python.framework/"*)
    fail "拿到的是 framework 构建的 python（${_BASE_REAL}）
   这种 python 的 mainBundle() 永远指向它自己，授权框改不成「ClassLive」。
   需要 uv 管理的独立构建：uv python install $PYVER --managed-python" ;;
esac
say "   $BASEPY"
say "   （独立构建，不是 framework —— 这是方案成立的前提）"

# ---------- ② 建一个**空** venv 到临时位置 ----------
# ⚠️ 顺序很重要：**先搬布局、再装依赖**。
#    venv 的 bin/ 里那些脚本 shebang 是**安装时写死**的绝对路径 ——
#    如果先装依赖再搬，shebang 就指向旧位置了（ClassLive 用不到那些脚本，
#    但没必要留个坏掉的东西）。先把家安好再往里搬东西，就没有这个问题。
say "② 建空 venv …"
rm -rf "$BUILD"
mkdir -p "$BUILD"
uv venv "$BUILD/venv" --python "$BASEPY" -q

# ---------- ③ 停掉正在跑的实例，然后替换 ----------
# ⚠️ 正在跑的时候换掉 Contents/ 会让它读到半新半旧的文件
if pgrep -f "$APP/Contents/MacOS/python" >/dev/null 2>&1; then
  say "③ 有实例在跑 —— 先停掉它"
  pkill -f "$APP/Contents/MacOS/python" || true
  sleep 2
fi

say "③ 搬布局进 .app（bin→MacOS、lib→lib、pyvenv.cfg→Contents）…"
rm -rf "$APP"
mkdir -p "$APP/Contents"
mv "$BUILD/venv/bin"        "$APP/Contents/MacOS"
mv "$BUILD/venv/lib"        "$APP/Contents/lib"
mv "$BUILD/venv/pyvenv.cfg" "$APP/Contents/pyvenv.cfg"
rm -rf "$BUILD"

# ⚠️⚠️ 把 bin/python* 的**符号链接换成真拷贝** —— 这一步不做，双击就是"没反应"。
#
# venv 的 bin/python 天生是**指向基础解释器的符号链接**，而基础解释器在 bundle 外面。
# macOS 的 LaunchServices **不会启动一个符号链接的 CFBundleExecutable** ——
# 没有任何报错、没有弹窗、连 python 都不起来，表现就是**双击完全没反应**。
# （2026-09-26 实测踩到：`open ClassLive.app` 退出码 0、日志空白、进程 0 个。）
#
# 为什么可以直接拷：uv 管理的独立 python 是**真 Mach-O 二进制**，且只链接系统库
# （`otool -L` 实测：CoreFoundation / libSystem / ncurses，**没有相对路径的 libpython**），
# 所以拷到别处照样能跑；stdlib 靠 Contents/pyvenv.cfg 的 `home` 找回去。
say "③ 顺带把 python 的符号链接换成真文件（LaunchServices 不认符号链接）…"
for f in "$APP/Contents/MacOS"/python "$APP/Contents/MacOS"/python3 "$APP/Contents/MacOS"/python3.*; do
  [ -L "$f" ] || continue
  _tgt="$(readlink "$f")"
  case "$_tgt" in /*) ;; *) _tgt="$(cd "$(dirname "$f")" && pwd)/$_tgt" ;; esac
  _real="$(cd "$(dirname "$_tgt")" && pwd -P)/$(basename "$_tgt")"
  if [ -f "$_real" ]; then
    rm "$f" && cp "$_real" "$f" && chmod +x "$f"
    say "   $(basename "$f")"
  fi
done

# ---------- ④ Info.plist ----------
# ⚠️ CFBundleExecutable 必须指向 MacOS/ 里真实存在的那个 python ——
#    这一条是整个方案能不能拿到 bundle 身份的开关（§1.5）
say "④ 写 Info.plist …"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleExecutable</key><string>python</string>
  <key>CFBundleIdentifier</key><string>$BUNDLE_ID</string>
  <key>CFBundleName</key><string>ClassLive</string>
  <key>CFBundleDisplayName</key><string>ClassLive</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundleShortVersionString</key><string>$(cat "$HERE/VERSION" 2>/dev/null || echo "0.0.0")</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>NSMicrophoneUsageDescription</key><string>ClassLive 需要麦克风来实时转录课堂内容</string>
  <key>NSCameraUsageDescription</key><string>ClassLive 不使用摄像头。</string>
</dict></plist>
PLIST

# ---------- ⑤ 装依赖 —— shebang 天然指向 .app 里的新位置 ----------
say "⑤ 装依赖（可能要几分钟）…"
uv pip install --python "$APP/Contents/MacOS/python" -q -r "$HERE/requirements.txt"
say "   $(ls "$APP/Contents/lib/python"*/site-packages/ 2>/dev/null | wc -l | tr -d ' ') 个顶层包"

# ---------- ⑥ 图标 ----------
# ⚠️ 必须排在 ⑤ **之后** —— 生成图标要用 .app 里的 python，而 pyobjc 是 ⑤ 才装进去的。
#
# ⚠️ 拷进 Resources/ 而不是让 .app 直接用仓库里的文件：CFBundleIconFile 只在
#    bundle 内部找图标，指向外面会被忽略，而且**不报错**（Dock 上悄悄退回通用图标）。
say "⑥ 装图标 …"
RES="$APP/Contents/Resources"
mkdir -p "$RES"
_icon_out="$("$APP/Contents/MacOS/python" "$HERE/tools/make_icon.py" 2>&1)" \
  || fail "生成图标失败（tools/make_icon.py）：
$_icon_out"
cp "$HERE/assets/icon/ClassLive.icns" "$RES/AppIcon.icns"
say "   Resources/AppIcon.icns  $(du -h "$RES/AppIcon.icns" | cut -f1)"
# ⚠️ LaunchServices 会**缓存**图标。换完之后不碰 .app 的话，Finder/Dock 上可能
#    还显示旧图标（甚至系统通用图标），让人以为没生效。touch 一下逼它重读。
touch "$APP"

# ---------- ⑦ 双击启动的入口 ----------
# ⚠️⚠️ 这就是"双击为什么能跑起来"的那一环。
#
# macOS 启动 .app 时会执行 `Contents/MacOS/<CFBundleExecutable>`，**不带任何参数**。
# 我们的 CFBundleExecutable 是 python —— 于是它进 REPL、等 stdin、没有输入就退出，
# 表现就是**双击了但什么都没发生**。（2026-09-26 实测踩到。）
#
# sitecustomize.py 是 Python 每次启动都会自动 import 的钩子，用它把主程序拉起来。
#
# ⚠️ 但它对**每一次**解释器启动都生效 —— `cl`、tests、各种脚本都会经过这里。
#    所以判断必须严格：**只要带了脚本参数（或 -c/-m）就绝不接管**，
#    否则 `cl doctor` 会莫名其妙开始上课。
SITE="$(ls -d "$APP/Contents/lib/python"*/site-packages)"
say "⑦ 写双击入口 sitecustomize.py …"
cat > "$SITE/sitecustomize.py" <<'PY'
"""双击 ClassLive.app 时自动拉起主程序。

macOS 执行 .app 时是裸调 `python`（没有参数），所以这里负责补上该有的参数。

⚠️ 严格只在「双击启动」时接管 —— 见下面三条判据。放松任何一条，
   `cl` / 测试 / 各种脚本都会莫名其妙地开始上课。
"""
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))))
# __file__ = <repo>/ClassLive.app/Contents/lib/python3.X/site-packages/sitecustomize.py


def _own_bundle_id():
    """从**这个 .app 自己的 Info.plist** 读 bundle id。

    ⚠️ 不在这里硬编码一份 —— 那会和 `make-app.sh` 里写进 Info.plist 的那份
       形成两处真源。它是**与 TCC 的契约**（授权锚在 bundle id 上），
       两边不一致时**全程不报错**，只是权限提示和授权记录对不上。
       （2026-09-26 审查发现。）
    """
    import plistlib
    # ⚠️ 从 site-packages 往上要 **3** 层才到 Contents：
    #    site-packages → python3.X → lib → Contents
    here = os.path.dirname(os.path.abspath(__file__))          # …/site-packages
    contents = os.path.dirname(os.path.dirname(os.path.dirname(here)))
    with open(os.path.join(contents, "Info.plist"), "rb") as f:
        return plistlib.load(f).get("CFBundleIdentifier")


_SHOULD_START = (
    # ① 是裸启动 —— 双击时 macOS 就是这么调的：`sys.argv == ['']`
    #    ⚠️ 别写成 `len(sys.argv) == 1`：`python -c …` 和 `-m …` 的 argv 长度**也是 1**
    #       （实测 `['-c']` / `['-m']`），那样会把它们误判成双击。
    sys.argv[0] == ""
    # ② 是 LaunchServices 启动的、而且启动的就是我们这一个 bundle
    #    （从终端跑时这个环境变量是终端的 ID，不会误判）
    and os.environ.get("__CFBundleIdentifier") == _own_bundle_id()
    # ③ 仓库确实在那儿
    and os.path.exists(os.path.join(_REPO, "cl"))
)

if _SHOULD_START:
    os.chdir(_REPO)
    # ⚠️ **先把 stdout/stderr 接到日志文件** —— 双击启动的 .app 没有终端，
    #    不接的话所有 print / traceback 都进虚空，出事时无从查起。
    #    接了之后 `notice.alert` 里 `sys.stdout.isatty()` 那条判据自然为假 → 走弹框那条路。
    _logdir = os.path.expanduser("~/Library/Logs/ClassLive")
    try:
        os.makedirs(_logdir, exist_ok=True)
        _fd = os.open(os.path.join(_logdir, "app.log"),
                      os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        os.dup2(_fd, 1)
        os.dup2(_fd, 2)
        if _fd > 2:
            os.close(_fd)
        sys.stdout = os.fdopen(1, "w", buffering=1, encoding="utf-8", errors="replace")
        sys.stderr = os.fdopen(2, "w", buffering=1, encoding="utf-8", errors="replace")
        sys.stdout.write("=" * 60 + "\nClassLive 启动（双击）"
                         + __import__("time").strftime(" %Y-%m-%d %H:%M:%S") + "\n")
    except Exception:
        pass                       # 日志建不出来也不能拦着上课

    # ⚠️ 复用 `cl` 那一层，不在这里重写一遍参数/vault/课程号 ——
    #    那些逻辑只该有一份，否则两处迟早漂移。（cl 自己的注释里也写了这条。）
    os.environ.setdefault("CLASSLIVE_FROM_APP", "1")
    os.execv("/bin/bash", ["/bin/bash", os.path.join(_REPO, "cl")])
PY

# ---------- ⑧ 自检 ----------
say "⑧ 自检 …"

# ⚠️⚠️ 头一条，也是**最容易被漏掉的一条**：`Contents/MacOS/<CFBundleExecutable>`
#     必须是**真文件**，不能是符号链接。
#
#     为什么单列：LaunchServices **不会启动符号链接的 CFBundleExecutable** ——
#     退出码 0、无报错、无弹窗、连 python 都不起来，表现就是**双击完全没反应**。
#     （2026-09-26 实测踩到，排查了一轮。）
#
#     而下面那段 Python 自检**查不出这个** —— 它自己就是拿那个 python 跑的，
#     能跑通说明"这个 python 可用"，不能说明"LaunchServices 肯启动它"。
#     两条是**不同的问题**：前者是解释器可用性，后者是 bundle 合法性。
_EXE="$(/usr/libexec/PlistBuddy -c "Print :CFBundleExecutable" "$APP/Contents/Info.plist" 2>/dev/null)"
if [ -L "$APP/Contents/MacOS/$_EXE" ]; then
  fail "Contents/MacOS/$_EXE 是**符号链接** ——
   LaunchServices 不会启动它，双击会完全没反应（且没有任何报错）。
   见 ③ 那一步：必须换成真拷贝。"
fi
[ -f "$APP/Contents/MacOS/$_EXE" ] && [ -x "$APP/Contents/MacOS/$_EXE" ] \
  || fail "Contents/MacOS/$_EXE 不存在或不可执行"
say "   ✅ $_EXE 是真文件且可执行（LaunchServices 肯启动的那种）"

"$APP/Contents/MacOS/python" - <<'PY' || fail "自检没过 —— 上面标 ❌ 的就是原因"
import os
import sys
from Foundation import NSBundle
b = NSBundle.mainBundle()
info = b.infoDictionary() or {}
bad = []
def chk(label, cond, got=""):
    print(("   ✅ " if cond else "   ❌ ") + label + (f"  {got}" if got else ""))
    if not cond: bad.append(label)

chk("sys.executable 在 .app 里", "/Contents/MacOS/" in sys.executable, sys.executable)
chk("sys.prefix 是 Contents",    sys.prefix.endswith("/Contents"), sys.prefix)
chk("mainBundle 是 .app",        b.bundlePath().endswith(".app"), b.bundlePath())
chk("bundle 里有我们的名字",      info.get("CFBundleName") == "ClassLive", str(info.get("CFBundleName")))
chk("麦克风说明文字就位",         bool(info.get("NSMicrophoneUsageDescription")))
# 图标：声明 + 文件**两样都要在**。只声明不装文件时系统**不报错**，
# 只是 Dock/Finder 上悄悄退回通用图标 —— 那种"做了但看着像没做"最难查。
_icon = info.get("CFBundleIconFile")
_icon_p = os.path.join(b.bundlePath(), "Contents", "Resources", f"{_icon}.icns") if _icon else None
chk("图标已装进 bundle", bool(_icon) and os.path.isfile(_icon_p),
    f"{_icon}.icns" if _icon and os.path.isfile(_icon_p) else f"未就位（{_icon or '未声明'}）")
for m in ("sounddevice", "numpy", "AppKit", "httpx"):
    try:
        __import__(m); chk(f"import {m}", True)
    except Exception as e:
        chk(f"import {m}", False, str(e)[:60])
sys.exit(1 if bad else 0)
PY

say ""
say "✅ 构建完成"
say "──────────────────────────────────"
say "   .app        $APP  ($(du -sh "$APP" | cut -f1))"
say "   双击它就能上课（第一次会弹麦克风授权，点允许）"
say ""
say "⚠️ 旧的 .venv/ $( [ -d "$HERE/.venv" ] && echo "($(du -sh "$HERE/.venv" | cut -f1)) 先别删" ) ——"
say "   等 cl 改完、确认 .app 能跑之后再说。"
say "   查现状：./make-app.sh --check"
