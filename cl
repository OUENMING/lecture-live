#!/bin/bash
# ClassLive 一键启动器
#   cl                 线下课(麦克风) + 悬浮窗   ← 最常用, 零参数
#   cl online          线上课(系统声; 需先把系统输出切到 Multi-Output Device)
#   cl file <音频>      转录已有录音(终端输出)
#   cl course ECON10101 记住课程名(之后自动写 Obsidian 笔记 + 用该课术语表)
#   cl local           强制本地引擎(不出网)
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
    if [ ! -f "glossary/$2.txt" ] && [ "$(ls glossary/*"$2".txt 2>/dev/null | wc -l)" -ne 1 ]; then
      echo "⚠ 没有 glossary/$2.txt(术语表没建)。仍会记住课程名。"
    fi
    printf '%s' "$2" > "$CFG"; echo "✅ 课程已设为 $2"; exit 0 ;;
  online|net) SRC=blackhole; shift ;;
  file)
    [ -n "${2:-}" ] || { echo "用法: cl file <音频文件>"; exit 1; }
    SRC=file; UI=terminal; ARGS+=(--path "$2"); shift 2 ;;
  local) ENGINE=local; shift ;;
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
