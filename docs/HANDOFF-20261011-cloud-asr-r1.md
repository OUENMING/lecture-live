# 交接：定稿上云调研 + R1 翻译空窗（2026-10-11）

> 接手的人先读这一页，再按需要跳到下面列的文档。数字都带了出处，**引用前按日期判断是否还成立**。

## 0. 现在在哪

| 项 | 状态 |
|---|---|
| 最后发布 | **3.8.9**（2026-10-05）。用户都停在这一版 |
| 3.8.10 | 代码已提交在本机 `main`（`8d89ae2`、`1cc7f5c` 等，**未推送**）；CHANGELOG 条目已写、**Owen 未最终确认** |
| 3.8.11（R1） | **代码全部完成、全部测试绿、未提交**（见 §2）；CHANGELOG 条目已写（草稿） |
| 发版决定 | Owen 2026-10-11：**先不发，再攒**（可能等这轮调研的东西都做完再一起发）—— 评估见 §6 |
| 下一件 | G（❓ 一句话点拨，3.8.12）→ R2（定稿上云）→ R3（草稿换 Nemotron 流式） |

查当前真实状态：`git status --porcelain`、`git log --oneline -15`、`git rev-list --count origin/main..HEAD`。

## 1. 文档地图（这一轮新写的）

| 文档 | 内容 |
|---|---|
| `docs/RESEARCH-cloud-asr.md` | ⭐ 全部调研与实测：各家云端 ASR 的错误率/漏句/延迟/价格/额度（§2–§12），外部建议逐条实测（§13），硬切找低谷实验与生产复核（§14） |
| `docs/PLAN-cloud-final-asr.md` | 已拍板的决定 D1–D6；R1/R2/R3 三版的逐文件改法 |
| `docs/PLAN-r1-translation-gap.md` | R1 执行细则（A–F、I 已做；G 未做），含审查后的全部修正 |
| `docs/experiments/cloud_asr_20261010/` | 测评脚本 + 各家转写结果（`results/hyp_*.txt`）。key 从 `~/.classlive/*-token` 读，不在仓库 |
| `docs/PLAN-streaming-asr.md` | （旧）R3 草稿换 Nemotron 的计划，仍有效 |

## 2. R1 已完成的改动（未提交）

| 项 | 改了什么 | 开关（默认开） |
|---|---|---|
| A | `draft_policy.novel`：相对上次送出的文本数新词（≥2.5 s 且 ≥4 新词，或 ≥5 s 且 ≥1） | `CLASSLIVE_DRAFT_TRIGGER=legacy` 退回 |
| B | `main.strip_dangling`：草稿送翻前削掉结尾悬挂词 | — |
| C | `draft_translate.py`：`DraftView`（主线程过期判据，按段号+序号）+ `DraftTranslator`（只译最新的后台线程）；`Overlay.finalize(clear_draft=)`；carry 超时补发带自己的段号；节流重置看 `seg_gen` | — |
| D | `CloudTranslator.translate_draft` 带领域/课程术语/上一句 | `CLASSLIVE_DRAFT_CONTEXT=0` 关 |
| E | `main.split_carry`：carry 只看最后一句 | — |
| F | `scripts/draft_gap_replay.py`、`scripts/draft_ctx_ab.py` | — |
| I | `vad.find_valley_cut`：12 s 硬切时在末尾 1.5 s 找能量最低点再切，尾巴留给下一段 | `CLASSLIVE_VAD_VALLEY=0` 关 |

验证（2026-10-11 重跑）：单测 165 项 OK（`test_vad_valley`、`test_draft_translate`、`test_draft_policy`、`test_audit_regressions`）、`test_translator` 21/21、`test_panel` 112/112、`probe_testmode_report.py` 通过、`test_pipeline.py` 跑通。
回放 10-09 真课：讲话中 ≥6 s 无新中文 57 次/437 s → **3 次/23 s**；草稿送翻 +64%。
硬切找低谷（OCW 生产复核）：WER 10.1% → 10.4%（噪声内），劈句代理指标 118 → 86（−27%）。

## 3. 关键结论（详细数字在 RESEARCH）

**云端 ASR**（MIT OCW 14.01 Lec11 人工字幕真值，同一套 322 段）：
- 逐段最准：千问国内 qwen3-asr-flash 9.5% / p50 1.4 s；千问国内 qwen-audio-3.1-asr-flash 9.7% / 1.6 s / ≈$0.011 每节；Groq whisper-large-v3 10.4% / **0.42 s** / 免费约 6 节/天；本地 Whisper（现定稿）12.0%，漏句 2.5%（云端都在 0.4–0.8%）。
- **Groq 付费档暂停升级**；免费档 20 RPM / 2000 RPD / 8 h 音频，按组织算 → RPD 是瓶颈。
- **DeepInfra 不进实时路径**：冷门模型排队几秒到几十秒（服务端计算只要 0.5 s），`priority`/`fail_fast` 对语音模型无效。
- **Cloudflare 只做可选第三**：p95 4.8 s；免费 10k neurons/天，但面板计数与官方单价对不上，未核清。
- **Opus 24k 上传不伤准确度**（Groq 10.1% vs WAV 10.4%），上传量 −93%。
- **别给 ASR 喂课程术语**：Groq 加 prompt 错误率 10.1% → 15.2% 且编造整句；千问加上下文无效。术语只用在翻译。
- **对冲请求平时无收益**（Groq p99 0.77 s）；实现成「1.2 s 未返回就并发发千问」。

**翻译空窗**根因：旧触发要求「比上次多 6 词」，Parakeet 词数忽多忽少 + 10 s 窗口上限 → 饿死；草稿译文挤在定稿线程里，约 27% 被挤掉。

**被证伪 / 不做**：降噪（本仓库 09-24 实测词数 −41%）、Parakeet 1.1B、课件页码实时对齐（F1 0.53）、投机复用草稿译文（相似段只有 3%）、把 carry 扩到所有硬切段（下一段定稿中位 8.3 s > 5 s 上限）、Mistral（漏句 2.0%）、Cohere（准确度打平但试用额度只够 3 节/月且禁生产）。

## 4. 已拍板的决定（Owen 2026-10-11）

D1 用户零操作（key 全放 Owen 的 Worker）· D2 定稿链 Groq → 千问国内 3.1-flash → 本地 · D3 默认开云端、首次告知可关 · D4 本地 Whisper 降为可选 · D5 DeepInfra 不进实时路径 · D6 $100 Claude Code 云额度不用于运行时 · 草稿上下文默认开 · 硬切找低谷默认开 · ❓ 按「一句话点拨 + 课后讲解卡」方向做（G）。

## 5. 凭据与外部账号

- key 文件（权限 600）：`~/.classlive/{groq,mistral,qwen,qwen-cn,cohere,deepinfra,cf-ai}-token`、`cf-account-id`。**这些都在聊天里出现过 → 用完建议去各家后台轮换。**
- 千问国内用 `dashscope.aliyuncs.com`；国际版（新加坡）用 `dashscope-intl.aliyuncs.com` / `maas.qwencloudapi.com`。
- DeepInfra 预充 $5（本轮用了约 $0.2）；Mistral 账号「允许训练」开关是开的。

## 6. 「再攒一版再发」的评估

可行，没有技术障碍（用户是手动更新，不会被半成品推到）。要注意三点：
1. **3.8.10 里有用户现在就受益的修复**：VAD 长静音后「睡死」吞词（92 → 50 词）、✕ 退出时报告被掐断、没装 ffmpeg 时音频不上传。拖得越久，用户越久在 3.8.9 上丢词。
2. **一次发太多更难定位问题**：已用三个环境变量开关缓解（出事可单独退回）；建议每项单独 commit，出问题能 `git bisect`。
3. **R1 现在只在工作区、没有提交** —— 机器出问题就全丢。建议**先本地提交（不推送）**，按 A/B/C/D/E/F/I 分成几个提交。

折中做法：现在只发 3.8.10（已写好），R1 + G 攒成下一版。

## 7. 这一轮的教训（已写进 memory）

- **子代理验收跑了 `main.py`**，往真 `sessions/`（用了真课号 ECON10770）和真 Obsidian vault 写了测试产物 → 已移到 `~/ClassLive-测试残留/2026-10-11/`（未删）。之后派活一律写明「不跑 main.py」或给隔离目录。
- 子代理的「not a git repo」是它在别的目录跑 git；要求它用 `git -C /Users/owen/lecture-live`。
- 审查（外部 AI）抓到的 4 条都在代码里属实（finalize 擦草稿、carry 段号、节流重置时机、低谷公式与测试矛盾）—— **计划写到代码级后再让独立审查过一遍，值得**。

## 8. 下一步怎么接

1. 问 Owen：R1 是否先本地提交；3.8.10 是否先单独发。
2. G（❓ 轻点拨）：按 `PLAN-r1-translation-gap.md` §G 执行（含审查补的三条护栏）。UI 改动要**先拍图给 Owen 看**。
3. R2（定稿上云）：按 `PLAN-cloud-final-asr.md` §3。新 Worker 建议域名 `asr.bldcam.page`（未确认）。动手前先核 Groq / 千问的数据留存条款，并把 README 的隐私承诺改掉。
4. R3：草稿换 Nemotron，按 `PLAN-streaming-asr.md`；做完再量「句子级提前定稿」和「投机复用」。
