"""对人工字幕算 WER + 「漏句」（参考里连续 ≥5 词整段没出现）。

⭐ 口径（`normalize` / 编辑距离 / 漏段判据）**已提到 `scripts/stream_metrics.segment_score`** ——
这个文件现在只剩命令行外壳；数字与以前逐位相同（同一个函数体，只是搬了家）。
搬家的理由：`classroom_sim_20261011` 也要用同一把尺子，而"按文件路径 importlib 抠别人的
实验脚本"不是个能长久的取法。
"""
import sys, json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]          # 仓库根（docs/experiments/<本目录>/x.py）
sys.path.insert(0, str(ROOT / "scripts"))
from stream_metrics import segment_score as score   # noqa: E402

if __name__ == "__main__":
    ref = " ".join(c["text"] for c in json.load(open(sys.argv[1])))
    for path in sys.argv[2:]:
        name, p = path.split("=", 1)
        s = score(ref, open(p).read())
        if s["wer"] is None:                      # 参考稿归一化后为空 → 没有分母
            print(f"{name:28s} 参考稿为空，无法打分")
            continue
        print(f"{name:28s} WER {s['wer']:.1%}（替 {s['S']:.1%} 删 {s['D']:.1%} 插 {s['I']:.1%}）"
              f"· 漏段 {s['lost_runs']} 处 / {s['lost_words']} 词（{s['lost_pct']:.1%}）")
        for e in s["examples"][:3]:
            print("     漏:", e[:120])
