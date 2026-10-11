# 云端 ASR 测评：MIT OCW 人工字幕真值

结论与全部数字在 `docs/RESEARCH-cloud-asr.md`；这里只讲**语料在哪、怎么复跑**。

## 语料（不进仓库）

位置：`~/.classlive/eval/ocw-14.01-lec11/`（`~/.classlive/eval/ocw` 是它的软链）。
不进仓库的原因：音频约 290 MB；字幕是 CC BY-NC-SA，和本仓库的 MIT 许可不混放。

| 文件 | 内容 | 丢了怎么重建 |
|---|---|---|
| `lec11.srt` | 人工字幕 | `archive.org/download/MIT14.01F18/MIT14_01F18_lec11_300k.srt` |
| `lec11.wav` / `lec11.ogg` | 16 kHz 单声道 / Opus 24k | 下 `…_300k.mp4` 后 `ffmpeg -i … -ac 1 -ar 16000 lec11.wav` |
| `ref.json` | 字幕 → 带时间的参考词 | `ocw_ref.py lec11.srt ref.json` |
| `segs.json` + `segs.json.norm.npy` | 生产切段（`PeakNormalizer` → `vad.Segmenter`，322 段）+ 归一化后的整课音频 | `ocw_segs.py lec11.wav segs.json` |
| `valley_v1/` `valley_v2/` | 硬切找低谷（RESEARCH §14）两版的切段与原始返回 | `ocw_segs_v2.py`（`CLASSLIVE_VAD_VALLEY` 默认开） |
| `raw/` | 各家接口的原始返回 | 重跑对应脚本（**花额度**） |
| `SHA256SUMS` | 上面几份的校验和 | 重建后对一下，切段一变所有 WER 都不可比 |

## 复跑

脚本按相对路径读 `ocw/`，所以**在 `~/.classlive/eval/` 下跑**：

```bash
cd ~/.classlive/eval
python3 ~/lecture-live/docs/experiments/cloud_asr_20261010/ocw_eval.py ocw/ref.json \
  Groq=~/lecture-live/docs/experiments/cloud_asr_20261010/results/hyp_groq.txt
# → Groq  WER 10.4% · 漏段 0.7%（与 RESEARCH 一致即语料完好）
```

打分只读 `results/hyp_*.txt`，不联网、不花钱。`*_run*.py` / `qcn*.py` / `opus_run.py` / `cf_neuron.py`
会真调各家接口（key 从 `~/.classlive/*-token` 读），结果写回 `ocw/`。
