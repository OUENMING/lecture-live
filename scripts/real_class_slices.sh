#!/bin/bash
# 把 `sessions/` 里**已有的真课录音**切片回放，把草稿稳定度的样本堆大。
#
#   ./scripts/real_class_slices.sh [输出目录，默认 /tmp/cl-real-slices]
#
# ## 为什么
#
# 2026-10-08 那张表里的「真实课堂」只有 **3 分钟 / 17 句 / 149 份草稿** —— 那是
# 全项目唯一无关 TTS 的数字。`sessions/` 里躺着 ~95 分钟真课录音没人用。
# 每片 4 分钟，5 片 ≈ 20 分钟音频 → 样本翻好几倍。
#
# ## 判读
#
# 输出每片一份 `.jsonl`（回放事件）与 `.json`（指标）。用
# `scripts/class_report.py <所有 jsonl>` **一次给多份**就得到**按计数相加**的汇总
# （⚠️ 不是对比率取平均 —— 每片句子数不同）。
#
# ## 不能拿它回答的
#
# ⚠️ 这是**回放**：脚本自己搭的流水线、虚拟时钟、**不排队** → 延迟是**下限**，
#    别和现场那份 `<会话>.live.jsonl` 的延迟比。
# ⚠️ 喂进去的是 16-bit wav，而生产吃的是 float —— 从 wav 回放**会改 ASR 结果**
#    （实测 17 段里 9 段改口）。所以**绝对 WER** 不可比；可比的是**文本稳定性**
#    这类相对量（回改率），而且要点是"同一份音频、同一个口径"下的相对比较。
set -u
HERE="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-/tmp/cl-real-slices}"
PY="$HERE/ClassLive.app/Contents/MacOS/python"
EVAL="$HERE/scripts/stream_eval.py"
mkdir -p "$OUT"

# : 录音:起点:时长 —— 挑不同课程/不同时间点，别都落在同一节课的中段
SLICES="
2026-10-06_160543_ECON10740.001.wav:300:240
2026-10-06_160543_ECON10740.003.wav:420:240
2026-10-07_120759_SOC10020.001.wav:300:240
2026-10-07_120759_SOC10020.003.wav:480:240
2026-09-24_203440_ECON10770.wav:60:240
"
for spec in $SLICES; do
  f="${spec%%:*}"; rest="${spec#*:}"; st="${rest%%:*}"; du="${rest#*:}"
  src="$HERE/sessions/$f"
  tag="$(basename "$f" .wav)_${st}s"
  if [ ! -f "$src" ]; then echo "⚠ 没有 ${src}，跳过"; continue; fi
  echo "▶ ${tag}（$du 秒）"
  "$PY" "$EVAL" "$src" --start "$st" --dur "$du" \
      --json "$OUT/$tag.json" --jsonl "$OUT/$tag.jsonl" > "$OUT/$tag.log" 2>&1 \
    || echo "  ⚠ 失败（见 $OUT/$tag.log）"
done
echo
echo "=== 汇总 ==="
"$PY" "$HERE/scripts/class_report.py" "$OUT"/*.jsonl
echo "（每片的明细在 $OUT/*.json）"
