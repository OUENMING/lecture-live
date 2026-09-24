#!/bin/bash
# ClassLive 一键启动器
#   cl                 线下课(麦克风) + 悬浮窗   ← 最常用, 零参数
#   cl online          线上课(系统声; 需先把系统输出切到 Multi-Output Device)
#   cl file <音频>      转录已有录音(终端输出)
#   cl course ECON10101 记住课程名(之后自动写 Obsidian 笔记 + 用该课术语表)
#   cl local           强制本地引擎(不出网)
#   cl test            测试模式: 采集完整指标 + 留音频, 收尾打成可发送的单个 zip
#   cl doctor          自检: 版本/依赖/模型/术语表, 缺什么告诉你跑哪条命令
#   cl help            帮助
set -u
# 解析符号链接(可能被 ln -s 到 ~/.local/bin)
SELF="$0"
while [ -L "$SELF" ]; do
  TARGET="$(readlink "$SELF")"
  case "$TARGET" in
    /*) SELF="$TARGET" ;;
    *)  SELF="$(dirname "$SELF")/$TARGET" ;;
  esac
done
cd "$(cd "$(dirname "$SELF")" && pwd)" || exit 1

PY=".venv/bin/python"
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
  cl doctor            自检: 依赖/模型/术语表, 缺什么告诉你跑哪条命令
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

list_courses() {
  echo "可选课程(术语表在 glossary/):"
  for f in glossary/*.txt; do
    [ -e "$f" ] || continue
    code="$(basename "$f" .txt)"
    mark=" "; [ "$code" = "$COURSE" ] && mark="*"
    echo "  $mark $code"
  done
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
    COURSE_ARG="$2"
    if [ ! -f "glossary/$COURSE_ARG.txt" ]; then
      _hit="$(find glossary -maxdepth 1 -name "*${COURSE_ARG}*.txt" 2>/dev/null | head -1)"
      if [ -n "$_hit" ]; then
        COURSE_ARG="$(basename "$_hit" .txt)"
        echo "⚠ glossary/$2.txt 不存在, 用最接近的术语表: $COURSE_ARG"
      else
        echo "⚠ 没有 glossary/$2.txt(术语表没建)。仍会记住课程名。"
      fi
    fi
    printf '%s' "$COURSE_ARG" > "$CFG"; echo "✅ 课程已设为 $COURSE_ARG"; exit 0 ;;
  online|net) SRC=blackhole; shift ;;
  file)
    [ -n "${2:-}" ] || { echo "用法: cl file <音频文件>"; exit 1; }
    # ⚠️ 本脚本开头已 cd 到安装目录, 用户在自己工作目录传相对路径会解析错 ->
    # "文件不存在"。这里先校验再转绝对路径。(2026-09-24 OCR 全量审计发现。)
    [ -f "$2" ] || { echo "❌ 找不到音频文件: $2" >&2; exit 1; }
    SRC=file; UI=terminal; ARGS+=(--path "$(cd "$(dirname "$2")" && pwd)/$(basename "$2")"); shift 2 ;;
  local) ENGINE=local; shift ;;
  doctor) "$PY" doctor.py; exit $? ;;
  test)
    # 测试模式: 采全量指标 + 录音频, 收尾打包。额外参数透传(如 --no-record-audio)。
    # ⚠️ 只在**跑课**时用 —— 它会往 sessions/ 旁写一份音频。
    shift
    ARGS+=(--test-mode "$@")
    ;;
  "") ;;
  *) echo "未知参数: $1"; echo; usage; exit 1 ;;
esac

# Obsidian 落盘: 没设课号也照常写(课程名默认 LECTURE, 见 obsidian_writer.py);
# 设了课号则带上它, 用该课术语表。
ARGS+=(--vault "${OBSIDIAN_VAULT:-$HOME/Obsidian/Vault}")
if [ -n "$COURSE" ]; then
  ARGS+=(--course "$COURSE")
fi

echo "▶ ClassLive · 音源=$SRC · 引擎=$ENGINE · 课程=${COURSE:-(未设置, 默认 LECTURE)}"
exec "$PY" main.py --source "$SRC" --ui "$UI" --engine "$ENGINE" ${ARGS[@]+"${ARGS[@]}"}
