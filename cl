#!/bin/bash
# ClassLive 一键启动器
#   cl                 线下课(麦克风) + 悬浮窗   ← 最常用, 零参数
#   cl online          线上课(系统声; 需先把系统输出切到 Multi-Output Device)
#   cl file <音频>      转录已有录音(终端输出)
#   cl course ECON10101 记住课程名(之后自动写 Obsidian 笔记 + 用该课术语表)
#   cl local           强制本地引擎(不出网)
#   cl test            测试模式: 采集完整指标 + 留音频, 收尾打成可发送的单个 zip
#   cl update          更新到最新版(git pull + 补依赖), 然后自检
#   cl doctor          自检: 版本/依赖/模型/术语表, 缺什么告诉你跑哪条命令
#   cl prep <课件…>     开课前的准备: 从课件抽候选术语, 追加进该课术语表
#   cl help            帮助
set -u
# 解析符号链接(可能被 ln -s 到 ~/.local/bin)
SELF="$0"
_hops=0
while [ -L "$SELF" ]; do
  # ⚠️ 互相引用的链（a→b→a）或超长链会让这一圈**永远转下去**，而且 `$SELF`
  #    每绕一圈只增不减。加个上限直接退出（2026-09-28 审查指出）。
  _hops=$((_hops + 1))
  if [ "$_hops" -gt 40 ]; then
    echo "❌ 解析 $0 的符号链接绕了 40 圈还没到头 —— 链是不是成环了？" >&2
    exit 1
  fi
  TARGET="$(readlink "$SELF")"
  case "$TARGET" in
    /*) SELF="$TARGET" ;;
    *)  SELF="$(dirname "$SELF")/$TARGET" ;;
  esac
done
# ⚠️ 在 cd 之前记住「用户敲命令时所在的目录」。本脚本下面会 cd 到安装目录，
#    那会让 `cl prep week5.pptx` 这类**相对路径**按安装目录解析 —— 结果是
#    「所有文件都打不开（损坏）」，与真因无关。`prep` 分支把它传给 prep.py。
_ORIG_PWD="$PWD"
cd "$(cd "$(dirname "$SELF")" && pwd)" || exit 1

# ⚠️ 补两条 Finder 启动时**缺失**的 PATH。
#    从 .app 双击启动时 PATH 只剩 `/usr/bin:/bin:/usr/sbin:/sbin`，于是：
#      · `uv`（~/.local/bin）找不到 → 下面的 `cl update` 补依赖分支整个失效
#      · 而它备用的 `$PY -m pip` **也不通** —— .app 里那个 python 是 uv 建的空 venv，
#        **不带 pip**（见 CLAUDE.md）。两条路一起断。
#      · `ffmpeg`（/opt/homebrew/bin）找不到 → `cl file` 跑不了
#    只**追加**，不会挤掉原有路径，终端里跑也完全无副作用。
#    （路线图把这条件算在 P1 里，2026-09-26 核实时发现其实一直没做。）
PATH="$PATH:$HOME/.local/bin:/opt/homebrew/bin"
export PATH

# ⚠️ Python 解释器住在 ClassLive.app **里面**，不是仓库根目录的 .venv ——
#    这么放是为了让 macOS 认这个目录为 bundle：授权框才会写「ClassLive」
#    而不是「python3.11」。根因与完整配方见 docs/PLAN-p1-app-launcher.md §1.5。
#    （2026-09-26 起，.venv 不再是安装目录；下面是**唯一**定义点，改这里就够。）
PY="ClassLive.app/Contents/MacOS/python"
if [ ! -x "$PY" ]; then
  echo "❌ 找不到 $PY" >&2
  echo "   还没构建过。跑一次：  ./make-app.sh" >&2
  exit 1
fi
CFG=".course"
COURSE="$(cat "$CFG" 2>/dev/null || true)"
ENGINE=auto
SRC=mic
UI=overlay
ARGS=()

usage() {
  cat <<'EOF'
ClassLive —— 本地实时课堂双语字幕

  cl                   线下课(麦克风) + 悬浮窗        ← 打开就能用
  cl online            线上课(系统声)
  cl file <音频文件>    转录已有录音(终端输出)
  cl course            列出可选课程
  cl course ECON10101  切换课程(之后自动写 Obsidian 笔记 + 用该课术语表)
  cl last              查看最近一次课堂记录(实时落盘的会话文件)
  cl local             强制本地引擎(断网/不想出网)
  cl test              测试模式: 采集完整指标 + 留音频, 收尾打成一个可发送的 zip
  cl update            更新到最新版(git pull + 补依赖), 然后自检
  cl doctor            自检: 依赖/模型/术语表, 缺什么告诉你跑哪条命令
  cl prep <课件…>       开课前的准备: 从课件抽候选术语, 追加进该课术语表
  cl help              显示本帮助

停止：点悬浮窗右上角 ✕,或在本终端按 Ctrl+C
说明：运行中每句都实时写入 sessions/,误按 Ctrl+C 或崩溃都不会丢
     (✕/Ctrl+C 时在途的最后一句也会冲刷落盘;同日同课的多节笔记不互相覆盖)
     不设课程代码也照常写笔记(课程名默认 LECTURE);设了则用该课术语表。
EOF
}

show_last() {
  f="$(ls -t sessions/*.md 2>/dev/null | head -1)"
  if [ -z "$f" ]; then echo "还没有任何课堂记录(sessions/ 为空)"; return; fi
  echo "最近一次记录: $f"
  echo "共 $(grep -c '^> \[!abstract\]' "$f") 句"
  echo "--- 开头 ---"
  sed -n '1,16p' "$f"
  echo "--- 结尾 ---"
  tail -8 "$f"
  echo "---"
  echo "完整路径: $(cd "$(dirname "$f")" && pwd)/$(basename "$f")"
}

update_classlive() {
  # 一条命令更新: 拉代码 + 补依赖 + 自检。
  #
  # ⚠️ **拉代码那部分全部在 `update.py`** —— 与更新卡片上的「立即更新」按钮共用
  #    同一份实现。两条路径的安全规则必须逐字一致，各写一份迟早在某一边漂移。
  #
  # 三条边界(与"不擅自改用户环境"同源, 实现在 update.py):
  #   ① **工作区有本地改动时停手** —— 绝不 stash、绝不丢弃。
  #   ② `--ff-only` —— 分叉时**响亮失败**, 不静默造 merge commit。
  #   ③ **绝不自动下模型** —— 1GB 的东西要不要下是用户的决定, 只打印命令。
  #
  # 这里只留两件 bash 更合适的事: **补依赖** + **跑 doctor**（环境关注点）。
  _req_before="$(shasum requirements.txt 2>/dev/null | cut -d' ' -f1)"

  "$PY" update.py || return $?

  # 依赖变了才装 —— 每次都装会白等, 且可能把环境改坏
  if [ "$_req_before" != "$(shasum requirements.txt 2>/dev/null | cut -d' ' -f1)" ]; then
    echo
    echo "🔧 依赖清单有变化, 正在安装…"
    if command -v uv >/dev/null 2>&1; then
      uv pip install --python "$PY" -q -r requirements.txt 2>&1 | tail -3
    else
      "$PY" -m pip install -q -r requirements.txt 2>&1 | tail -3
    fi
    # ⚠️⚠️ **管道会吞掉退出码**（2026-09-28 审查指出）：`cmd | tail -3` 的 `$?` 是
    #    `tail` 的（恒 0）—— 于是**安装失败也照样往下走**，还顺手把 requirements
    #    标记成"已装好"，更新卡片**再也不提示补依赖**（静默失败里最贵的一种：
    #    用户以为环境是全的）。`PIPESTATUS[0]` 是管道里第一条命令的退出码
    #    （bash 专有；本脚本 shebang 就是 `#!/bin/bash`）。
    if [ "${PIPESTATUS[0]}" -ne 0 ]; then
      echo "❌ 依赖没装上（上面是最后几行输出）—— 这次**不**标记已装好，下次还会提示。" >&2
      echo "   排查：uv 在不在（~/.local/bin）？网络通不通？" >&2
      return 1
    fi
    # ⚠️ 告诉 update.py「这份 requirements 已经装好了」——
    #    卡片上的「立即更新」按钮靠这个标记判断还要不要提示补依赖。
    #    不记的话终端这边装完了、卡片那边还会一直让你再装一次。
    "$PY" update.py --mark-reqs
  fi

  echo
  "$PY" doctor.py || true
  echo "   （模型不会自动下载 —— 要装哪个按上面的命令来。）"
}


# 课程清单 —— **实现在 courses.py 一处**。
# ⚠️ 原来这里自己遍历 glossary/*.txt，于是漏掉「拖了课件但还没建术语表」的课
#    （那些只在 ~/.classlive/courses/ 里有）。courses.py 的 list_courses 是**并集**。
list_courses() {
  echo "可选课程(术语表在 glossary/, 课件在 ~/.classlive/courses/):"
  # ⚠️ **别把 stderr 一起吞掉**（2026-09-28 审查指出）：原来是
  #    `... 2>/dev/null || true` —— `courses.py` 一崩，用户只看到标题 +「当前: xxx」，
  #    会以为**一门课都没有**，而真相是解析失败。`course` 分支刻意区分
  #    「故障 vs 没命中」，这里要对齐。`|| true` 留着（列课程失败不该拦住后面的流程）。
  "$PY" courses.py --list --current "${COURSE:-}" || \
    echo "  ⚠️ 列课程失败（见上面的报错）—— 这**不是**「一门课都没有」"
  echo "  当前: ${COURSE:-未设置}"
}

case "${1:-}" in
  help|-h|--help) usage; exit 0 ;;
  last) show_last; exit 0 ;;
  course)
    if [ -z "${2:-}" ]; then list_courses; exit 0; fi
    # ⚠️ 要写进 .course 的是**下游真正会加载的那个术语表名**, 不是原始输入:
    #    原来模糊命中("cl course 1077" 命中 ECON10770.txt)时只跳过警告, 写入的
    #    仍是 1077 —— 下游按 glossary/1077.txt 找不到, 静默退回只用公共术语表。
    #
    # ⚠️ **解析只在 courses.py 一处实现。** 原来这里是自己一句
    #    `find … -name "*${COURSE_ARG}*" | head -1` —— 实测 `cl course 107`
    #    会**静默选中 ECON10770**（`head -1` 的产物，既不是「最接近」也不是
    #    用户意图），后果是那节课用错术语表。现在歧义就拒绝并列出候选。
    #
    # courses.py 的退出码约定（bash 只读得动这个）：
    #    0 = 唯一命中，stdout 是规范课号
    #    2 = 歧义，stderr 每行一个候选
    #    1 = 没命中（**两个流都是空的**；stderr 非空 = python 自己炸了，那要分开报）
    # ⚠️ **stdout 与 stderr 必须分开收。**
    #    原先是 `2>&1` —— 于是任何一次 stderr 输出（Python 的 DeprecationWarning、
    #    依赖打的提示）都会混进 `_out`，而 `0)` 分支是把 `_out` **整段**写进 `.course`：
    #    `.course` 就变成「警告行 + 课号」，下游按整个文件内容当课号。
    #    分开之后，「python 炸了」的判据从「`_out` 非空」改成「**stderr 非空**」。
    _errf="$(mktemp -t clcourse 2>/dev/null)" \
      || { echo "⚠ 建不出临时文件 —— 课程没改（别当成「没找到」往下走）"; exit 1; }
    _out="$("$PY" courses.py --resolve="${2}" 2>"${_errf}")"
    _rc=$?
    _err="$(cat "${_errf}" 2>/dev/null || true)"
    rm -f "${_errf}"
    case "$_rc" in
      0) # ⚠️ 采信的**只有 stdout**，而且必须是**单行**（多行 = 混进了别的东西）
         if [ -z "${_out}" ] || [ "$(printf '%s' "${_out}" | wc -l | tr -d ' ')" != "0" ]; then
           echo "⚠ 课程解析的输出不干净（空或多行）—— 不写 .course："
           printf '%s\n' "${_out}" | sed 's/^/       /'
           exit 1
         fi
         printf '%s' "${_out}" > "$CFG"; echo "✅ 课程已设为 ${_out}"; exit 0 ;;
      2) echo "⚠ 「${2}」命中多门课，请写全其中一个："
         printf '%s\n' "${_err}" | sed 's/^/       /'
         exit 1 ;;
      *) # ⚠️ **判据出错时要倒向「叫人来看」，不能倒向「当成没命中」。**
         #    倒错的话，python 一炸就会伪装成「术语表没建」，然后一路往下走。
         if [ -n "${_err}" ]; then
           echo "⚠ 课程解析出错了 —— 不是「没找到」："
           printf '%s\n' "${_err}" | sed 's/^/       /'
           exit 1
         fi
         echo "⚠ 没有 glossary/${2}.txt(术语表没建)。仍会记住课程名。"
         printf '%s' "${2}" > "$CFG"; echo "✅ 课程已设为 ${2}"; exit 0 ;;
    esac ;;
  online|net) SRC=blackhole; shift ;;
  file)
    [ -n "${2:-}" ] || { echo "用法: cl file <音频文件>"; exit 1; }
    # ⚠️⚠️ **相对路径必须挂回用户原来那个目录再判**（2026-09-28 审查指出）：
    #    本脚本开头已经 `cd` 到安装目录，所以 `[ -f "$2" ]` 和 `dirname "$2"`
    #    全是**相对安装目录**解析的 —— 用户在自己目录敲 `cl file week5.mp3`
    #    仍然报"找不到"，而他明明就在那个文件旁边。
    #    上面那句注释写着"(2026-09-24 OCR 全量审计发现)"，但**只改了注释没改代码**；
    #    `prep` 分支是传了 `_ORIG_PWD` 的，这里要对齐。
    case "$2" in
      /*) _AUDIO="$2" ;;                       # 本来就给的绝对路径 -> 别动它
      *)  _AUDIO="$_ORIG_PWD/$2" ;;
    esac
    [ -f "$_AUDIO" ] || { echo "❌ 找不到音频文件: $2" >&2; exit 1; }
    SRC=file; UI=terminal; ARGS+=(--path "$_AUDIO"); shift 2 ;;
  local) ENGINE=local; shift ;;
  doctor) "$PY" doctor.py; exit $? ;;
  # 开课前的准备。照 doctor 的形状：转发 + 透传退出码（prep.py 用非零表示失败）。
  # ⚠️ 课号在这里解析一次（`$COURSE` 来自上面读的 .course），prep.py 不自己去读环境。
  prep) shift; "$PY" prep.py --course "${COURSE:-}" --cwd "$_ORIG_PWD" "$@"; exit $? ;;
  update) update_classlive; exit $? ;;
  test)
    # 测试模式: 采全量指标, 收尾打包。额外参数透传(如 --record-audio)。
    # ⚠️ 默认**不**留音频; 加了 --record-audio 才会往 sessions/ 旁写一份。
    shift
    ARGS+=(--test-mode "$@")
    ;;
  # ⭐ 零参数 = 先开「课程卡片面板」，点「开始上课」才开麦（作者 2026-09-26 拍的第 1 条）。
  #
  # ⚠️ **面板是一个独立短进程**（`entry_launch.py`）：它只负责写 `.course` 然后退出，
  #    录课那条路**一个字没动**。所以「面板崩了不能导致录不了课」是**结构上**成立的，
  #    不靠 try/except 兜 —— 面板再怎么崩，最坏也就是本进程非零退出，下面照旧起录音。
  #
  # ⚠️ **退出码必须分开处理** —— `1`（用户主动关掉=不录，是意图）和
  #    `2/3`（面板挂了 / 没课可选=退回老路，是故障）混在一起的话，
  #    要么违背用户意图（他取消了还录），要么录不了课（正是要防的那件事）。
  #    ⚠️ `4` 是 2026-09-29 加的：**选了课但 `.course` 写不下去**。
  #       它原来复用了 `2` → 照旧开麦，而盘上那份 `.course` 还是上一门课的
  #       → 这节课**静默记到上一门课名下**（笔记/术语/课次全挂错门，不可逆）。
  #       现在 4 = **不录**：少录一次当场能发现，记错门要翻很久才发现。
  #    `CL_NO_PANEL=1` 可显式跳过面板（排查用 / 想回到老行为）。
  "")
    if [ "${CL_NO_PANEL:-0}" = "1" ]; then
      :
    else
      "$PY" entry_launch.py
      case $? in
        0) COURSE="$(cat "${CFG}" 2>/dev/null || true)"
           echo "▶ 课程 = ${COURSE:-未设置}" ;;
        1) echo "（面板被关掉了 —— 不录课）"; exit 0 ;;
        2) echo "⚠ 面板起不来 —— 照旧直接开始录课" ;;
        3) : ;;                                   # 没课程可显示：照旧录
        4) echo "⚠ 选了课但存不下来 —— **不录**（否则会记到上一门课上）"; exit 0 ;;
      esac
    fi ;;
  *) echo "未知参数: $1"; echo; usage; exit 1 ;;
esac

# Obsidian 落盘: 没设课号也照常写(课程名默认 LECTURE, 见 obsidian_writer.py);
# 设了课号则带上它, 用该课术语表。
# ⚠️⚠️ **不在这里兜底** —— 原来写的是 `${OBSIDIAN_VAULT:-$HOME/Obsidian/Vault}`，
#    而双击 `.app` 起的那条路**没有 shell 环境**（Finder 给的是 launchd 的环境，
#    实测 `launchctl getenv OBSIDIAN_VAULT` 是空的）→ 笔记被写进 `~/Obsidian/Vault/`。
#    那个目录连 `.obsidian` 都没有，**不是 vault**，实测里面躺着 9 个笔记。
#    现在收口到 `obsidian_writer.resolve_vault()`：`--vault` → `$OBSIDIAN_VAULT`
#    → `~/.classlive/vault`（上次用过的）→ **都没有就不写 Obsidian**（会话照常落 sessions/）。
if [ -n "${OBSIDIAN_VAULT:-}" ]; then
  ARGS+=(--vault "$OBSIDIAN_VAULT")
fi
if [ -n "$COURSE" ]; then
  ARGS+=(--course "$COURSE")
fi

echo "▶ ClassLive · 音源=$SRC · 引擎=$ENGINE · 课程=${COURSE:-(未设置, 默认 LECTURE)}"
exec "$PY" main.py --source "$SRC" --ui "$UI" --engine "$ENGINE" ${ARGS[@]+"${ARGS[@]}"}
