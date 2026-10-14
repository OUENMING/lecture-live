# CLAUDE.md

## 这是什么

ClassLive —— 作者自用的**实时英译中课堂字幕**工具：采音频 → 转写 → 逐句修正并翻译 → 悬浮字幕 → 实时落盘、课后渲染双层 Obsidian 笔记。零参数 `cl` 启动；草稿/兜底识别跑在本机；翻译只发文本；定稿转写默认走云端（Groq / 阿里云，可关：面板开关或 `CLASSLIVE_CLOUD_ASR=0`，3.9.0 起），关掉后音频不出机器。MIT 开源，已脱敏。

## 文件地图

每行是一个 context pointer：先读命中的那一行，再顺着 target 往下。
按文件自动加载的规则在 `.claude/rules/`（AppKit 界面 / shell 与构建 / 发版），碰到对应文件时才进上下文。
`docs/` 每份文档管什么、什么时候读：`docs/README.md`。⚠️ `docs/archive/` 全是某一天的快照，其中的「下一步」不作数。

| 分支（什么时候读） | 读哪个 |
|---|---|
| ⭐⭐ **第一次接手 / 新会话** —— 全景手册（现状、硬规矩、事故与教训、开关登记、公开的账 §10） | `docs/HANDBOOK.md` |
| ⭐⭐⭐ **当前这条线：3.9.4（通行码代付 Jev / 草稿段尾收尾 / 封口句译文锁定；标点切段与译文溢出**没做**的数据）**—— **新会话先读这份** | `docs/HANDOFF-20261012-3.9.4.md` |
| 上一条线：3.9.3（清空老 Key / 旧 `.deepseek_key` 退休 / 弹窗 / 草稿模型课后自动下）+ 九条审查修复（现状 / 为什么 / 怎么做 / 还剩什么 / 踩过的坑） | `docs/HANDOFF-20261012-3.9.3.md` |
| 上一条线：定稿上云 + R1 翻译空窗（历史背景，仍有效） | `docs/HANDOFF-20261011-cloud-asr-r1.md` |
| R1 执行细则（3.8.11 已做；❓ 轻点拨 G 已做、待发版） | `docs/PLAN-r1-translation-gap.md` |
| 定稿上云 R2 / 草稿流式 R3 的逐文件改法（D1–D6 已拍板）—— 动草稿译文、定稿 ASR、隐私文案前读 | `docs/PLAN-cloud-final-asr.md` |
| 云端 ASR 选型与全部实测（WER / 漏段 / 延迟 / 价格 / 额度）—— 动定稿 ASR 前读 | `docs/RESEARCH-cloud-asr.md`；真值语料在 `~/.classlive/eval/`，复跑见 `docs/experiments/cloud_asr_20261010/README.md` |
| 产品长线路线图（P1–P3；不含上面那条线） | `docs/PLAN-roadmap.md` |
| 架构判断、模块深度、seam —— **2026-09-18 快照**，正文行号别抄 | `ARCHITECTURE.md` |
| 外部评审（含已知薄弱点）—— **不在仓库里** | `~/Desktop/classlive-review/CODE-REVIEW-20261001.md` |
| 启动、命令行参数、课程切换 | `cl` → `main.py` |
| 主循环、后台线程、队列、UI 路由与落盘分发（`drain()` 是 streamq 唯一分发点） | `main.py` |
| 零参数入口（双击 `.app` → 面板 → 「开始上课」；退出码 0–4 是它与 `cl` 的契约；`CL_NO_PANEL=1` 退回老行为） | `entry_launch.py` |
| 单实例锁（为什么 flock 不用 pidfile） | `instance_lock.py` |
| 开课前的准备（课件 → 候选术语 → 该课术语表；`glossary/<课号>.txt` **只追加**） | `prep.py`、`extract.py`（先读文件头） |
| 课程清单 / 片段解析 / 准备度（`glossary/` ∪ `~/.classlive/courses/`；`cl course` 模糊匹配只此一处） | `courses.py` |
| 课件归哪门课（命令行**只看不动**；`--sessions <目录>` 测的时候指向别处） | `classify.py` |
| `~/.classlive/` 那族路径的唯一定义点 | `paths.py` |
| 面向用户的提示：弹窗 / 麦克风权限三态 / 跳系统设置 | `notice.py` |
| 音频采集、电平归一化、上课中途换设备（`resolve_input_device` 唯一定义点） | `capture.py` |
| 断句、VAD、静音阈值、硬切找低谷 | `vad.py` |
| 转写、ASR、Parakeet、听错修正 | `asr.py` |
| 定稿 ASR 深模块（云端 Groq/千问优先、本地兜底、熔断、总截止；`main.final_worker` 只调 `FinalASR.transcribe`）；开关 / 告知 / 配置 `read_config` | `final_asr.py`、`asr_endpoint.py`、`tests/test_final_asr.py` |
| 翻译、prompt、DeepSeek 云端、mlx 本地、引擎降级；对话模型地址与非流式 JSON 调用的唯一出口 | `translator.py`、`cloud_translator.py`、`llm_client.py` |
| 云端翻译的前文窗口（块对齐 / 前缀缓存）—— 动 `cloud_translator._user_content` 前读 | `docs/experiments/context_ab.md` |
| 草稿译文：过期判据 `DraftView` + 只译最新的线程 `DraftTranslator`（依赖全注入，`step()` 可单测）；触发判据 | `draft_translate.py`、`draft_policy.py` |
| CHANGELOG 解析（版本号、看点、更新方式；纯函数只吃文本 —— 本地卡片 / 远端分级 / 面板确认框共用） | `changelog.py` |
| 下载模型（直连 → 镜像 → sha256） | `fetch_model.py`（文件头） |
| 术语表、课号、术语查表与注入 | `build_notes.py`、`glossary/`（样例 `glossary.example.txt`） |
| 笔记落盘、`sessions/` 格式、双层笔记、❓ 旁路文件 `sessions/<同名>.lost.jsonl` | `obsidian_writer.py` |
| 实时总结（`main.py` 只接线，逻辑全在这里） | `live_summary.py`（先读文件头）、`chapter.py` |
| 测试模式的上传（`send` 是唯一传输洞；判据用本地目录 / 本机 HTTP 服务器，不碰真服务器） | `upload.py`（先读文件头）+ `docs/PLAN-zero-config-upload.md` |
| 整课二级精修 | `polish.py` |
| 悬浮窗、字幕显示、滚动、槽位池化 | `overlay.py`、`transcript_view.py` |
| 磨砂面板配方（overlay 与 whatsnew 的唯一真源）/ ObjC 类名归属 | `panel.py`、`objc_own.py`（先读文件头） |
| `.app` 构建 / 装进 `/Applications` | `make-app.sh`、`install.sh`（先读注释） |
| ⭐ 流式字幕量具（回改率 / 延迟 / WER）+ 现场采集 + 课堂报告 —— 改草稿 / ASR / 音源前读 | `docs/HANDOFF-20261009-stream-eval.md`、`scripts/stream_eval.py`、`livemetrics.py`、`scripts/class_report.py` |
| ⭐ **模拟课堂语料**（阶梯教室 / 小班 · 人群噪声 + 远场 + 短时高噪事件；24 份带真值）—— 调 VAD / ASR / 增益 / 降噪前先拿它量 | `docs/experiments/classroom_sim_20261011/README.md`、`scripts/eval_corpus/{fetch,make}_classroom_sim.py`、`scripts/eval_corpus/acoustics.py` |
| 草稿引擎（Nemotron 流式优先 / Parakeet 滑窗兜底；选择、喂流、熔断）；R3 计划与 P0 实测 | `draft_engine.py`、`tests/test_draft_engine.py`；`docs/PLAN-streaming-asr.md` |
| 分支上课入口 `./cl-x`（现场采集 + `--freeze` / `--trigger`）；worktree 补运行态软链 | `cl-x`、`scripts/link-runtime.sh` |
| 回归测试 R1–R30（清单在文件头） | `tests/test_audit_regressions.py` |
| 面向用户的说明、开源与脱敏须知 / 环境与选型 | `README.md` / `docs/DESIGN.md` |

产物位置：实时会话写 `sessions/`，课后笔记写 `<vault>/Lectures/`；vault 由 `--vault` → `$OBSIDIAN_VAULT` → `~/.classlive/vault` 决定（`obsidian_writer.resolve_vault` 唯一定义点）。

## 本仓库特有雷区

- ⭐ **动手改代码前先加载相关 skill**（作者提过三次的硬规矩）：质检→`simplify` · 排查→`diagnosing-bugs` ·
  声称完成前→`verification-before-completion` · 动接口→`codebase-design` · 写给 agent 看的文档→`writing-for-agents`。
  `settings.json` 的 `skillOverrides` 标 `"off"` 的 skill 静默不可用。
  方案里说不清、没实测、靠印象的点（阈值、API 行为、别人的现成做法）先查实。任务够大（几个文件 / 改行为）才需要；一行 typo 不用。
- **发版**：更新方式默认 `auto`（2026-10-13 起），更新日志先给作者确认 —— 细则在 `.claude/rules/release.md`。
- **EN 为基准防倒退**：整课精修同时收到直播定稿的 EN 与 ASR 原文，EN 是基准；中文只能由 EN 修正，不能反过来改写 EN。
- **云端 prompt 里稳定内容在前、逐句变化的在后**（DeepSeek 前缀缓存只认从头逐字相同）。`ctx_chunk=0` 的老布局被
  `tests/test_translator.py` 逐字冻结，改它要连测试一起说明理由。
- **不拿译文质量换弱防护**：要改 prompt 文案的「防护」先问会不会动译文，会 → 默认不做、只记账。
- **不阻塞不变量**：`capture` / `vad` 的回调必须立刻返回；AppKit 调用只在主线程。往流水线里加活先想这两条。
- **新增 streamq tag 必须在 `main.drain()` 加同分支**（R28 钉着；漏了数据照丢，兜底只告警）。
- **会话 Markdown 是三方契约**：`obsidian_writer` 写、`_parse` 读回、`cl last` grep。抬头正则 `_TS` 行尾锚定 →
  课上按下的标记一律走旁路文件 `sessions/<同名>.lost.jsonl`，**不许往抬头加字段**（会把这一条静默盖到上一条头上）。
- **`--context` 有两个默认值**：CLI 是 5，`translator.load_translator` / `CloudTranslator` 是 2。
- **开关**：每个 `CLASSLIVE_*` 都登记在 `docs/HANDBOOK.md` §6.1（R30 钉着）。`CLASSLIVE_LIVE_SUMMARY` 关的只是章节合成 /
  纲要落盘 / 发 streamq；「失败留窗重试」「下课残余窗口补提交」有意不在开关后面。
- **镜像名单会腐坏，校验不会**：镜像来的文件必须过 sha256；`ghfast.top` 换证书，不在名单里，也不用 `-k` 绕。
- **环境**：只有 `requirements.txt`（下界、无 lock）。运行环境在 `ClassLive.app/Contents/`（细则见 `.claude/rules/shell-and-build.md`）。
- **`IOGPUFamily` 内核崩溃史**（2026-09-10，GPU 驱动断言）：`translator._configure_mlx` 是缓解措施，动 mlx / 本地模型时留意。
- **用户数据**：`sessions/` 只追加（曾误删一节真课，不可恢复；`*_TEST.md` 也留着）。测试必须**同时**隔离读端和写端，
  包括 `~/.classlive/`（探针曾把 `~/.classlive/vault` 改成临时目录）。子代理不跑 `main.py` / `cl`（hook 拦着）。
- **实验 / A/B / 探针要花 key 的，一律 `cloud_translator.load_test_key()`**（`~/.classlive/test-key` = test1 通行码，不回落）。
  `load_api_key()` 是作者自己的码，只给真上课和 `rebuild_note` 用 —— 2026-10-14 一次实验在 owen 名下记了 1755 次请求，那天没上课。
- **个人数据不入库**：`.gitignore` 覆盖 `sessions/`、`glossary/`、`.course`、`.test-mode`、`.deepseek_key`、`term_notes*.json`；
  仓库公开，key 不许进库（R29 钉着）。改 `.gitignore` 前先想清楚。

## 当前状态 —— 现查，别抄

| 想知道 | 命令 / 位置 |
|---|---|
| 未提交改动、未跟踪文件 | `git status --porcelain` |
| 已落地历史 | `git log --oneline -15`；未推送 `git rev-list --count origin/main..HEAD` |
| 计划与公开的账 | `ls -t docs/PLAN-*.md`、`docs/HANDBOOK.md` §10 |

## 完成判据

- ⭐ **`ClassLive.app/Contents/MacOS/python scripts/check.py`** 全绿（约 10 s）：全部测试文件 + HOME 隔离 + 写端核对 +
  ruff + pyright 两条规则。碰过的东西有慢测试或要人跑的量具时用 `--changed`，它会补跑并列出来（唯一定义点在它的
  `SLOW_FOR` / `MANUAL_FOR`）。默认闸门 `tests/test_audit_regressions.py` 在里面；R1 的并发用例是手抄 `fake_worker`，绿 ≠ 真实流水线通过。
- **要人去跑、check 不自动跑的**：
  - 收尾路径 / 测试报告（`main.py` 收尾那段、`testmode.finish`）：`scripts/probe_testmode_report.py` —— 真跑约 1 分钟、
    往 `sessions/` 写一节测试记录；防的是「报告静默没写成」（藏过一周）。
  - 流水线 / 音频路径：`scripts/test_pipeline.py <音频> [start] [dur] [speed]` 能跑完。
  - **改概率性链路**（切段 / ASR / 草稿触发 / prompt / 模型）：跑对应离线量具、对照基线再下结论 ——
    `check.py --changed` 会列出该跑哪个。
  - 真网络下载：隔离 `HOME` 再跑（`HOME=/tmp/x …`），别写进真 `~/models/`。
- 报「可用」之前先跑上面命中的那条、贴出输出，再下结论。
