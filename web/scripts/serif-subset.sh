#!/bin/sh
# 门面衬线标题字体（思源宋体 Noto Serif SC，SIL OFL）的子集：只含静态标题用到的字，约 35 KB。
# 标题文案改了就重跑：把新标题的字加进 TEXT，跑完替换 static/fonts/serif-display.woff2。
# 动态文字（课名等）不用衬线，免得一行里两种字体。
set -e
TEXT="${TEXT:-一节课，从头记到尾字幕跟着老师走认得这门的术语浮在件上面刷新了也还下就能看笔网页版和桌差不多准、快存什么久安装 Mac备好去开。英文实时中我台电脑}"
Q=$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(''.join(dict.fromkeys(sys.argv[1]))))" "$TEXT")
CSS=$(curl -s -A "Mozilla/5.0 Chrome/130" "https://fonts.googleapis.com/css2?family=Noto+Serif+SC:wght@400..700&text=$Q")
URL=$(printf '%s' "$CSS" | grep -o "https://fonts.gstatic.com[^)]*" | head -1)
curl -s "$URL" -o "$(dirname "$0")/../static/fonts/serif-display.woff2"
echo "ok: static/fonts/serif-display.woff2"
