#!/usr/bin/env bash
# 本机 build → rsync 到 VPS 的 releases/<UTC 时间>/ → 原子切 current 软链 → 保留最近 5 版。
#
# 用法：deploy/publish.sh [--rollback] [--dry-run]
#   （在 web/ 下跑；`--dry-run` 只打印，不 ssh 不传文件）
# 环境：VPS_HOST（默认 ubuntu@class.bldcam.page）、VPS_ROOT（默认 ~/classlive-web）。
#
# 为什么是 releases + 软链：切版是一次 `ln -sfn`（原子的），回滚是切回上一版，
# 出问题不用重新 build，也不用等 rsync。nginx 的 root 指向 `current`。
#
# ⚠️ 只写脚本，没跑过：第一次真上线按 deploy/README.md 的顺序来。
set -euo pipefail

HOST="${VPS_HOST:-bldcam}"
ROOT="${VPS_ROOT:-/srv/classlive-web}"   # 绝对路径：nginx（www-data）进不了 /home/ubuntu（750）
KEEP=5
HERE="$(cd "$(dirname "$0")/.." && pwd)"

mode=publish
dry=0
for arg in "$@"; do
	case "$arg" in
		--rollback) mode=rollback ;;
		--dry-run) dry=1 ;;
		*) echo "publish: 不认识的参数 $arg" >&2; exit 2 ;;
	esac
done

run() {
	if [ "$dry" = 1 ]; then echo "+ $*"; else "$@"; fi
}

# 先在远端建好目录（一次就有；重复无害）
run ssh "$HOST" "mkdir -p $ROOT/releases"

if [ "$mode" = rollback ]; then
	# 上一版 = releases 里按名字排倒数第二（名字是 UTC 时间，字典序就是时间序）
	prev="$(ssh "$HOST" "ls -1 $ROOT/releases | sort | tail -n 2 | head -n 1")"
	if [ -z "$prev" ]; then
		echo "publish: 只有一版，没有可回滚的" >&2; exit 1
	fi
	run ssh "$HOST" "ln -sfn $ROOT/releases/$prev $ROOT/current.tmp && mv -Tf $ROOT/current.tmp $ROOT/current"
	echo "publish: 回滚到 $prev（nginx 不用 reload，静态文件按路径读）"
	exit 0
fi

# ---- 发版 ----
cd "$HERE"
export CL_GIT_SHA="$(git rev-parse --short HEAD 2>/dev/null)$(git diff --quiet 2>/dev/null || echo +dirty)"   # 进遥测的构建号（vite.config buildId）
run npm run build

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
echo "publish: $stamp → $HOST:$ROOT/releases/$stamp"

# --delete：这一版目录是全新的，多余文件不该留着（build/ 之外一律不传）
# /vendor/ 是 13 MB 的自托管识别核心，正常发版不该每次都传 —— 见下面的「复用上一版 vendor」
tmp="$ROOT/releases/$stamp"
run ssh "$HOST" "mkdir -p $tmp"
if [ "$dry" = 0 ]; then
	# 复用上一版已经传过的 /vendor/（版本号变了才需要重传；省 14 MB）
	cur="$(ssh "$HOST" "readlink $ROOT/current || true")"
	if [ -n "$cur" ] && ssh "$HOST" "test -d $cur/vendor"; then
		ssh "$HOST" "cp -a $cur/vendor $tmp/vendor" || true
	fi
fi
# vendor 已从上一版拷过来：rsync 照常带上它，只补差异（首发 / 版本变了才真传 14 MB）
run rsync -az --delete --human-readable --stats \
	--exclude '/sessions/' --exclude '/.git' \
	build/ "$HOST:$tmp/"

# 原子切版
run ssh "$HOST" "ln -sfn $tmp $ROOT/current.tmp && mv -Tf $ROOT/current.tmp $ROOT/current"

# 留最近 5 版
run ssh "$HOST" "cd $ROOT/releases && ls -1 | sort | head -n -$KEEP | xargs -r rm -rf"

echo "publish: done → https://class.bldcam.page （静态文件不用 reload nginx）"
