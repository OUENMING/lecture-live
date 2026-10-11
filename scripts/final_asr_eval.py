#!/usr/bin/env python3
"""用 `final_asr.FinalASR` 跑 OCW 切段, 出 hyp 文本 + source/steps 分布 + 延迟 p50/p95 (R22)。

    ClassLive.app/Contents/MacOS/python scripts/final_asr_eval.py <输出目录> [--cloud] [--limit N]
        [--local-whisper] [--min-interval 3.2] [--segs ~/.classlive/eval/ocw]

为什么要有它: `scripts/test_pipeline.py` 只用 Parakeet、不经过 `final_worker`, 验不了定稿 WER。
这里走的是**生产同一条** `FinalASR.transcribe`(编码 / 熔断 / 截止 / 退化 / R5 补跑全在里面)。

- 默认**纯本地**(不发请求); 加 `--cloud` 才打 Worker(要它已部署; 端点 / 令牌可用
  `CLASSLIVE_ASR_ENDPOINT` / `CLASSLIVE_ASR_TOKEN` 覆盖)。云端会烧 Groq/千问免费额度:
  默认每次请求间隔 3.2 s(= Groq 20 RPM), 322 段约 17 分钟。
- 输入只读 `<segs>/segs.json` + `segs.json.norm.npy`; 输出全写进 `<输出目录>`
  (`hyp_final.txt` / `segs.jsonl` / `summary.json`), **不写 `~/.classlive/`**:
  `paths.STATE_ROOT` 被改指到 `<输出目录>/state`(install-id 等落那里)。
- 打分: `python3 docs/experiments/cloud_asr_20261010/ocw_eval.py <segs>/ref.json 名字=<输出目录>/hyp_final.txt`
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys
import time

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import models                                                         # noqa: E402
import paths                                                          # noqa: E402


def pct(xs: list[float], q: float) -> float:
    return float(np.percentile(xs, q)) if xs else 0.0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out", help="输出目录(不要指向 ~/.classlive/)")
    ap.add_argument("--segs", default=str(pathlib.Path.home() / ".classlive/eval/ocw"))
    ap.add_argument("--cloud", action="store_true", help="真的打云端 Worker(默认不打)")
    ap.add_argument("--local-whisper", action="store_true", help="预加载本地 Whisper 作兜底(默认只用 Parakeet)")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 段")
    ap.add_argument("--min-interval", type=float, default=3.2, help="--cloud 时请求最小间隔秒数")
    ap.add_argument("--model-dir", default=models.path_of("parakeet"))
    ap.add_argument("--final-model-dir", default=models.path_of("whisper"))
    a = ap.parse_args()

    out = pathlib.Path(a.out).expanduser().resolve()
    if out == paths.STATE_ROOT.resolve() or paths.STATE_ROOT.resolve() in out.parents:
        print("输出目录不能放进 ~/.classlive/", file=sys.stderr)
        return 2
    segdir = pathlib.Path(a.segs).expanduser()
    segs = json.loads((segdir / "segs.json").read_text())
    audio = np.load(segdir / "segs.json.norm.npy", mmap_mode="r")
    if a.limit:
        segs = segs[:a.limit]
    out.mkdir(parents=True, exist_ok=True)
    paths.STATE_ROOT = out / "state"            # 读完输入再改: 之后任何 ~/.classlive 写入都落到输出目录

    import final_asr
    from asr import load_asr
    final = final_asr.load_final(a.final_model_dir, load_asr(a.model_dir), lambda: a.cloud,
                                 preload_local=a.local_whisper)

    hyps: list[str] = []
    src, stp, lat = collections.Counter(), collections.Counter(), []
    last_cloud = 0.0
    with open(out / "segs.jsonl", "w", encoding="utf-8") as fj:
        for i, s in enumerate(segs):
            buf = np.asarray(audio[int(s["t0"] * 16000):int(s["t1"] * 16000)], dtype="float32")
            if a.cloud and (wait := a.min_interval - (time.monotonic() - last_cloud)) > 0:
                time.sleep(wait)
            last_cloud = time.monotonic()
            r = final.transcribe(buf)
            hyps.append(r.text)
            src[r.source] += 1
            stp[" > ".join(r.steps)] += 1
            lat.append(r.ms)
            fj.write(json.dumps({"i": i, "t0": s["t0"], "t1": s["t1"], "text": r.text, "source": r.source,
                                 "steps": list(r.steps), "ms": round(r.ms), "fell_back": r.fell_back},
                                ensure_ascii=False) + "\n")
            if (i + 1) % 20 == 0:
                print(f"  {i + 1}/{len(segs)}", flush=True)
    (out / "hyp_final.txt").write_text("\n".join(h for h in hyps if h), encoding="utf-8")
    summary = {"n": len(segs), "cloud": a.cloud, "source": dict(src), "steps": dict(stp.most_common()),
               "empty": sum(1 for h in hyps if not h),
               "ms_p50": round(pct(lat, 50)), "ms_p95": round(pct(lat, 95))}
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
