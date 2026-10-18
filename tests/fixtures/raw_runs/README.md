# 冻结的原始运行（F15 · 回放用）

这里存的是**守卫链之前**的模型输出 —— 守卫的改动必须在**冻结的输入**上重放，才能把
「判据有效」和「模型今天写得不一样」分开（也顺带把回放的 LLM 成本降成 0）。

## 内容

| 目录 | 来源 | 里面是什么 |
|---|---|---|
| `v3_busi3243/` | `runs/final_v3/busi3243`（2026-10-12 BUSI3243） | `review_raw.json`（`note_v2.review` 的**原始**返回，守卫链之前）+ `note_data.json`（真跑落盘的渲染数据，回放借它读 `entries` 与其余非守卫层） |
| `v3_ecs170/` | `runs/final_v3/ecs170`（2026-10-15 ECS170） | 同上 |
| `v2_busi3243/`, `v2_ecs170/`, `v2_econ10790/`, `v2_che002a/` | `runs/final_v2/*` | 只有 `review_raw.json` —— 用途是**判据的假阳性面**（旧猜测句判据在这 4 份上删掉的每一句都进了 `scrub_audit.json`） |
| `scrub_audit.json` | 本仓库生成 | F15-A 审计：旧判据删掉的每一句 + 逐句手工分类 + 收紧前后的精确率 |

> ⚠️ `note_data.json` 里有整节课的转录，是测试朋友的数据 —— **只放本地、不入库**（`.gitignore`）。
> 没有它时 `tests/test_replay.py` 里依赖转录的用例跳过，其余照跑。

## 怎么用

```bash
# 零 LLM 回放（守卫链重跑 + md/html 重渲染）
ClassLive.app/Contents/MacOS/python scripts/notes_replay.py \
    --data tests/fixtures/raw_runs/v3_busi3243/note_data.json \
    --raw  tests/fixtures/raw_runs/v3_busi3243/review_raw.json \
    --out  /tmp/v3r/busi3243
```

`tests/test_replay.py` 直接 import 这些 JSON 跑断言 —— **不联网、不调模型**。

## 出处与保真

- 原始文件在同一台机器的 scratchpad：`…/scratchpad/final3/runs/`（`run_final_v3.py` 的产物）。
  本目录是**逐字节复制**，只多了一份 `scrub_audit.json` / 这份 README。
- ⚠️ `review_raw.json` 里**没有** `overview`（速览是守卫链之后、由模型另写的一层）。
  F15 的「速览首句不许删」因此在 `tests/test_replay.py` 里用**作者题面给出的那段真实文字**
  （`F14NoDanglingConnective.REAL_TAIL`）验证，而不是声称 raw 里有它。
- `note_data.json` 里的 `review` 是**真跑之后**的版本；回放会**丢弃**它、换成 `review_raw.json`
  重跑守卫 —— 只借它的 `entries` / 术语表 / 速览层（那几层归模型，回放不重写）。
