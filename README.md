# ClassLive · 本地实时课堂双语字幕

> 把英文课实时变成「英文草稿 → 句末定稿 → 中文流式打字机」，全本地、离线、$0。

[![Version](https://img.shields.io/badge/version-3.2-blueviolet?style=flat-square)](#路线图)
[![License: MIT](https://img.shields.io/badge/License-MIT-green?style=flat-square)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12-blue?style=flat-square)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/platform-macOS%20Apple%20Silicon-lightgrey?style=flat-square)](#环境要求)
[![Offline](https://img.shields.io/badge/runs-offline-success?style=flat-square)](#翻译引擎云端--本地)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen?style=flat-square)](#贡献)

ClassLive 是一个跑在你自己电脑上的**实时课堂字幕工具**：老师在讲台上说英文，屏幕上滚出中文双语字幕。
它复刻了商业产品「说完一句、按上下文自动修正」的效果，但**音频不出本机**，课后还能自动整理成 Obsidian 笔记。

---

## 项目介绍

### 为什么做这个

留学第一年最难受的不是听不懂单词，而是**跟不上语速**——一句话里有两三个词没听清，整句就散了，你还在回想上一句，老师已经翻页了。

市面上的商业字幕产品（Otter / Wordly / Interprefy）都要**把音频传到云端**，且按分钟计费。ClassLive 走另一条路：**ASR 完全本地跑，云端只在你愿意时收文本**，实测一节课两小时成本 **0.08–0.19 元**。

### 核心功能

- **实时双语字幕** — 英文草稿逐字浮现 → 句末整句重转定稿 → 中文流式打字机（首字约 0.6s）
- **上下文自动修正** — 不是逐句孤立翻译。前文 + 术语表一起送给模型，`nation to` → `relation to`、`publicate` → `plagiarize` 这种听写错误会被上下文修回来
- **术语即时解析** — 课上冒出 `endogeneity` / `Byzantine Empire` 这类词，底部出现一行术语提示，**点一下**展开完整解析并钉住
- **聊天式悬浮窗** — 不是只能看最近三句。可展开到 60% 屏高**回看整节课**，翻上去看历史不会被新内容拽下来
- **远场收音** — 讲台离你 10 米也能收。靠滑动峰值归一化补电平（实测可用动态范围扩大 ~24dB），不靠降 VAD 阈值
- **课后自动归档** — 逐句实时落盘（`kill -9` 也只丢最后一句），结束后生成**双层 Obsidian 笔记**：复习层（一句话概览 + 自测问答 + 本课术语表 + 你标星的重点）+ 默认折叠的**逐字完整转录**
- **翻译随时开关** — 面板上点一下就在「双语」和「只英文」之间切；关掉中文时**仍会做英文矫正**，不会退回原始 ASR 错听
- **零成本兜底** — 云端翻译失败 / 断网时自动降级到本地 Qwen3-1.7B，课上不会突然哑掉

### 适用场景

- 英语授课的留学生（本科 / 研究生）课堂实时跟课
- 线上课 / 讲座 / 会议（系统声转录）
- 已有录音的事后转写（任意长度音频文件）
- 想学 `sherpa-onnx` + `mlx-lm` + PyObjC 实时音频管线的开发者参考

---

## 效果

```
草稿(Parakeet 实时):  ▸ I'm contacting you in relation to Eco.
句末定稿 + 流式中文:   🌐 我是在就 Econ 10790 与你联系。   ← 中文先出(~0.6s)
                       EN: I'm contacting you in relation to Econ 10790.
```

悬浮窗长这样（收起态 3 句 + 底部术语行；展开态可滚动查阅全部历史）：

```
┌──────────────────────────────────────────────────┐
│  ▾ 展开   ↑ 最新   🌐 译 开   ⭐   ✕              │
├──────────────────────────────────────────────────┤
│  ... the slope of the demand curve reflects ...  │
│  需求曲线的斜率反映了边际效用……                     │
│                                                  │
│  ... in relation to Econ 10790 ...               │
│  我是在就 Econ 10790 与你联系……                    │
├──────────────────────────────────────────────────┤
│  ◈ demand · marginal utility   ▸ 点开解析          │
└──────────────────────────────────────────────────┘
```

---

## 功能清单

| 功能名称 | 功能说明 | 技术栈 | 更新时间 | 版本 |
|---------|---------|--------|----------|------|
| 实时语音识别 | 流式草稿 + 句末整句重转定稿 | Parakeet-TDT-0.6B-v3 int8 (sherpa-onnx) | 2026-09-09 | v1.0 |
| 语音活动检测 | 梯级静音阈值断句 + 300ms pre-roll | Silero VAD (ONNX) | 2026-09-09 | v1.0 |
| 远场电平补偿 | 滑动峰值归一化，恢复远场衰减 | NumPy | 2026-09-10 | v1.2 |
| 流式双语翻译 | ZH 流式打字机 + EN 跟随 | DeepSeek / Qwen3-1.7B | 2026-09-10 | v2.0 |
| 上下文英文矫正 | 翻译关掉也修 ASR 错听 | 同模型 + `fix_stream` | 2026-09-11 | v3.2 |
| RAG-lite 术语注入 | 核心词常驻 + 动态召回 Top-3 | 纯本地检索 | 2026-09-10 | v2.0 |
| 聊天式悬浮窗 | 池化视图回收 + 跟随状态机 | PyObjC + NSScrollView | 2026-09-11 | v3.0 |
| 术语分档解析 | gloss / basic / skip 三档 + 点击展开 | DeepSeek 构建时分类 | 2026-09-11 | v3.1 |
| Obsidian 双层笔记 | 复习层 + 折叠逐字转录 | 本地组装 + LLM 概览 | 2026-09-11 | v3.1 |
| 实时落盘 | 每句定稿立即追加写入 | 纯文件追加 | 2026-09-09 | v1.0 |
| 云端→本地降级 | 断网自动切本地引擎 | 双 Translator 同接口 | 2026-09-10 | v2.0 |

---

## 技术栈

| 技术 | 版本 | 用途 | 官网 |
|------|------|------|------|
| Python | 3.12 | 运行时 | https://www.python.org |
| sherpa-onnx | 1.13.7 | ASR + VAD 推理 | https://github.com/k2-fsa/sherpa-onnx |
| Parakeet-TDT-0.6B-v3 | int8 | 英文语音识别模型 | https://huggingface.co/csukuangfj |
| Silero VAD | v5 | 语音活动检测 | https://github.com/snakers4/silero-vad |
| mlx-lm | 0.31.3 | 本地 LLM 推理 (Metal) | https://github.com/ml-explore/mlx-lm |
| MLX | 0.32.2 | Apple Silicon 张量框架 | https://github.com/ml-explore/mlx |
| Qwen3-1.7B-4bit | 4bit | 本地翻译兜底模型 | https://huggingface.co/mlx-community |
| DeepSeek API | deepseek-flash | 云端翻译（默认） | https://platform.deepseek.com |
| PyObjC | 12.2.2 | 悬浮窗 UI（Cocoa / Quartz） | https://pyobjc.readthedocs.io |
| sounddevice | 0.5.6 | 音频采集（PortAudio） | https://python-sounddevice.readthedocs.io |
| NumPy | 2.5.3 | 音频归一化 | https://numpy.org |
| Obsidian | — | 笔记落盘目标 | https://obsidian.md |

### 技术架构

```
音源(麦克风 / BlackHole 系统声 / 文件)  ← 回调式异步采集(不阻塞主线程)
  → PeakNormalizer: 滑动峰值归一化(补远场衰减, 不削顶)
  → Silero VAD(深度学习)判语音 + 梯级静音切句 + 300ms pre-roll
       dur<3s→0.6s静音 | 3–7s→0.3s | >7s→0.25s | 上限12s
  → Parakeet-TDT-0.6B-v3 int8(sherpa-onnx, CPU, ~28× 实时)
       说话中每 1s 刷草稿 / 句末整句重转定稿
  → 标点断句 → 逐句流式: DeepSeek 云端(本地 Qwen3-1.7B 兜底)
       RAG-lite 术语筛选(核心词常驻 + 动态召回 Top-3)
       输出 ZH: ... / EN: ...  → 中文逐字打字机 → 英文随后
  → 卡片式悬浮窗(NSScrollView 池化回收 + 跟随状态机) / 终端
  → ObsidianWriter 逐句落盘(--course 时) → 双层笔记
```

---

## 项目结构

```
lecture-live/
├── main.py                # CLI 入口；事件驱动主循环 + 三个 worker 线程
├── capture.py             # 音源(回调式异步采集 / 文件) + PeakNormalizer 电平归一化
├── vad.py                 # Silero VAD + 梯级静音 + pre-roll 分段
├── asr.py                 # Parakeet 转写(带锁, 线程安全)
├── translator.py          # 本地流式 ZH/EN + RAG-lite 术语筛选 + 仅英文矫正
├── cloud_translator.py    # DeepSeek 云端翻译(同接口, 失败降级本地)
├── overlay.py             # 卡片式悬浮窗 UI(含翻译开关 + 顶栏)
├── transcript_view.py     # NSScrollView 转录区：池化回收 + 跟随状态机
├── obsidian_writer.py     # Obsidian 双层笔记落盘
├── build_notes.py         # 术语三档分类/扩写 + 专有名词查询闸门
├── cl                     # 一键启动器(软链到 ~/.local/bin/cl)
├── docs/DESIGN.md         # 深度工程笔记：全部实测数据、踩坑、设计论证
├── glossary.example.txt   # 公共术语表模板 → 复制成 glossary.txt 后自填课号
├── probe_scroll.py        # 滚动行为验收探针
├── test_pipeline.py       # 集成测试(走终端路径，可加速回放)
└── sessions/              # 运行时产物：逐句实时落盘的会话文件(不入库)
```

> `glossary/`（分课程术语表）、`term_notes.json`（术语解释库）、`sessions/`（课堂记录）
> 都是**你本地的数据**，随 `.gitignore` 排除，需要自己准备 —— 见下方[术语表](#术语表分课程)。

---

## 实测性能（M1 Pro 16GB）

| 指标 | 值 |
|---|---|
| ASR | Parakeet 12s 音频 0.43s（**≈28× 实时**） |
| 首字中文 | **0.60s** |
| 句末 → 中文完成 | ~1.0s |
| 内存峰值 | ~1.3GB |
| 磁盘（模型） | ~2GB |
| 每节 2h 课成本 | **0.08–0.19 元**（DeepSeek 云端） |
| 悬浮窗 pump 耗时 | 中位 **0.01ms** / p99 0.03ms；超帧率轮次 0.009% |

> ⚠️ **本机模型上限**：OOM 由「模型大小 × prompt 长度」共同触发。
> - Qwen3-4B / Qwen3.5-4B → **必 OOM**
> - Qwen2.5-3B → RAG-lite 缩小 prompt 后**可跑，但格式遵循差**、慢一倍
> - **Qwen3-1.7B → 默认**（最快、格式稳）

---

## 环境要求

- **macOS + Apple Silicon**（M1/M2/M3/M4）—— 本地推理依赖 MLX（Metal），悬浮窗依赖 AppKit
- Python **3.12**
- 约 **2GB** 磁盘放模型
- 磁盘里跑，不需要 GPU 服务器

## 安装

```bash
git clone https://github.com/OUENMING/lecture-live.git
cd lecture-live

# 虚拟环境 + 依赖
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python sherpa-onnx mlx-lm sounddevice numpy \
    huggingface_hub pyobjc-framework-Cocoa pyobjc-framework-Quartz

# Parakeet ASR 模型（~600MB）
.venv/bin/python -c "from huggingface_hub import snapshot_download; \
  snapshot_download('csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8', \
  local_dir='\$HOME/models/parakeet-tdt-0.6b-v3-int8')"

# Silero VAD
mkdir -p ~/models/vad && curl -sL -o ~/models/vad/silero_vad.onnx \
  https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx

# Qwen3-1.7B 首次运行自动下载（仅降级时用到）

# 术语表模板 → 自己的公共术语表
cp glossary.example.txt glossary.txt
```

---

## 使用说明

### 快速开始（推荐：一键 `cl`）

```bash
cl                    # 线下课(麦克风) + 悬浮窗     ← 零参数，打开就能用
cl online             # 线上课(系统声，需先切 Multi-Output Device)
cl file 录音.m4a       # 转录已有录音(终端输出)
cl course ECON10790   # 记住课程名(之后自动写 Obsidian 笔记)
cl local              # 强制本地引擎(断网 / 不想出网)
cl last               # 查看最近一次课堂记录
cl help               # 帮助
```

**停止**：点悬浮窗右上角 **✕**，或终端按 **Ctrl+C**（两者都是优雅退出：冲刷队列 + 落盘）。

> `cl` 是 `~/.local/bin/cl` → `~/lecture-live/cl` 的软链；课程名存在 `~/lecture-live/.course`，不用每次重输。

<details>
<summary><b>直接调 main.py（参数全览）</b></summary>

```bash
cd ~/lecture-live
.venv/bin/python main.py --source mic --ui overlay --course ECON10790
.venv/bin/python main.py --source file --path 录音.m4a --speed 4
```

| 参数 | 说明 |
|---|---|
| `--source` | `mic` / `blackhole` / `file` |
| `--ui` | `terminal` / `overlay`(卡片式悬浮窗) |
| `--engine` | `auto`(默认，云端优先失败降级) / `cloud` / `local` |
| `--cloud-model` | 云端模型（默认 `deepseek-flash`；可选 `deepseek-v4-pro`） |
| `--api-key` | DeepSeek key（默认读 `DEEPSEEK_API_KEY` 或 `.deepseek_key` 文件） |
| `--course` | 课程代码；**给了才写 Obsidian 笔记** |
| `--vault` | Obsidian 库路径（默认 `~/Obsidian/Vault`，可用 `OBSIDIAN_VAULT` 环境变量覆盖） |
| `--speed` | file 模式回放倍速（测试用） |
| `--llm` | 默认 `mlx-community/Qwen3-1.7B-4bit` |
| `--glossary` | 术语表（默认 `glossary.txt`） |

</details>

### 悬浮窗

卡片式双语流动排版，每卡中文（18pt Medium，最多两行）+ 英文（11pt Medium）。**阅读流向：上方旧、下方新；最底部是 💡 术语行**（钉底，不随句子滚）。

| 模式 | 高度 | 显示 | 滚动 |
|---|---|---|---|
| **收起**（默认） | 330px 固定 | 3 句（2 历史 + 1 当前）+ 💡 一行 | ❌ 不可滚（滚轮被吞） |
| **展开** | **屏可见高的 60%** | 任意多历史 + 💡 最多三行 | ✅ 可自由翻 |

- 展开态**顶边不动**（面板向上长高），顶栏按钮不跳。
- 展开态**实时行钉在底部**，只有历史可滚 —— 和聊天软件一致。
- **闲置自动回底**：翻上去看历史时，若又有**新句定稿**，静置 6s 自动回底；若只是翻着没新内容，**不会**把人拽下去。
- 白底 PPT 上也可读：材质之上压 `SCRIM_ALPHA=0.45` 半透明黑 + 紧凑黑描边（可读性由**描边**承担，不是填充色）。

| 按钮 | 位置 | 作用 |
|---|---|---|
| ▾ 展开 / ▴ 收回 | 面板右上 | 切换收起 / 展开 |
| ↓ 最新 | 面板右上（翻上去后才出现） | 回到最新一句 |
| 🌐 译 开/关 | 面板右上 | 翻译开关，随时点随时切 |
| ⭐ | 面板右上 | 标记**当前句**为重点（Obsidian 里加 `⭐ Exam Focus`） |
| ✕ | 面板右上 | 退出（优雅关闭：冲刷队列 + 落盘） |
| 🎧 | **菜单栏** | 开启 / 关闭**鼠标穿透**、退出 |

> 鼠标穿透为什么不在面板上：开启后窗口忽略**所有**鼠标事件，面板按钮会集体失效（单向死锁）。所以穿透只从**菜单栏** 🎧 切换 —— 菜单栏永远可点。

### 🌐 翻译开关

点一下就在「双语」和「只英文」之间切，不用重启。

- **关闭时**：只停**中文** —— 不产译文、不查术语解析（解析本身是中文）。**英文仍走同一个模型做上下文矫正**，ASR 错听照修，阅读流向不变。落盘写 `**EN**: <矫正后>` + `**ASR**: <原始>` 两行。
- **随时可逆**：重新打开即刻恢复双语，上下文一直连着，不会断链。
- **矫正结果三重把关**（`_clean_fix`），不满足就回退原始 ASR：① 混入中文（模型跑偏去翻译了）② 空 ③ **明显变长**（在改写而非矫正）。

> 「不翻译」≠「不联网」。关掉中文后**仍会调模型做英文矫正** —— 这是刻意的：关掉它的理由是"不要中文"，不是"不要修正"。真要做到零模型 / 零网络，用 `--engine local` 断网跑（或不配 key），失败时自动退成纯 ASR 转录。

### 术语表（分课程）

```bash
cp glossary.example.txt glossary.txt    # 先生成公共术语表
mkdir -p glossary && $EDITOR glossary/你的课号.txt
```

```
glossary.txt          ← 公共：课号、考核相关（所有课通用）
glossary/             ← 分课程（每行一个术语，文件名 = 课号）
  ECON10101.txt       经济学导论
  ECON10202.txt       经济数据分析
```

- **术语表是最大的质量杠杆，远大于 prompt 措辞。** 实测：ASR 听成 "essential means" 时，无术语表 → 翻成「小测验」（崩）；有术语表 → 「小测验也是基于必读材料的」+ 英文行也纠正。
- **课号必须全列在公共表里**：同一院系的课号发音往往极近（`…-70 / -30 / -40 / -90` 只差一个词），不给候选必然听混。
- 加词方式：直接往 `glossary/<课号>.txt` 加行即可（上课听到新术语就补）。
- 课程术语表的**解释**（💡 术语行显示的内容）由 `term_notes.json` 提供，用 `build_notes.py` 生成。

```bash
cl course             # 列出可选课程(带 * 标当前)
cl course ECON10101   # 切换课程
```

### 术语自动解析（💡）

字幕里出现**真·难词或专有名词**时，面板最底部出现一行术语提示 —— **查表，零额外延迟**。**默认只列术语名**；**点一下**才展开完整解析并**钉住**。

术语不是"命中就解释"。每条在**构建时**被模型分到三档，判断标准是**中英对照价值**（不是"概念难不难"——本应用是英译中，要给的正是英文词→中文术语的即时对照）：

| 档 | 含义 | 命中后显示 | 例子 |
|---|---|---|---|
| `gloss` | 学生知识之外、真需要展开的难点 | **完整解析**（80–160 字） | `endogeneity`、`constrained optimization` |
| `basic` | 本课程基础概念，但英文↔中文要即时对上 | **一行速查**（≤30 字） | `demand`、`marginal cost` |
| `skip` | 后勤词 / 课号 / 自明词 | **永不显示** | `deadline`、`module`、`ECON10730` |

匹配用**词边界正则**（`\bterm(?:s|'s)?\b`），不是子串 —— 旧实现 `if tl in low` 会让 `GG` 命中 *suggest / struggle / bigger*，`Four` 命中 *fourth*。

```bash
python build_notes.py            # 增量：只为新术语生成解释
python build_notes.py SOC10020   # 只为某门课
python build_notes.py --rebuild  # 全量重分类 + 扩写 + 清掉自动回写的垃圾
```

> **实测效果**：151 句真实课堂转录上，旧版**每 2 句触发一次**（76 次，多为 Yeah / Don't / Four / module / 课号）；修好词边界 + 入口闸门后降到 **12 次**（仅 `gloss` 档）；纳入 `basic` 档后为 **20 次（每 7.5 句）**，命中**全部是真术语**。

### 存入 Obsidian 的笔记是「双层」的

```
<vault>/Lectures/<日期>_<课程>.md
├── 复习层（自动生成，一分钟看完）
│   ├── 🎯 一句话概览          ← LLM 生成，只依据本课转录，不得引入外部知识
│   ├── ❓ 复习自测（3–6 条）   ← `问题::答案`，可被 Spaced Repetition 插件识别
│   ├── 💡 本课术语表          ← 带首次出现时间戳，本地查表（零网络）
│   └── ⭐ 我标记的重点        ← 课上按过 ⭐ 的句子（原文 + EN）
└── 📜 完整逐句转录（默认折叠） ← **逐字保留**：EN / ZH / 原始 ASR，一句不删
```

**双层 ≠ 摘要**。复习层只是**入口**；下面那份转录是**逐字保留、未做任何删改**的完整记录 —— "不遗漏任何要点"靠的是它，因为摘要必然把细节吃掉。复习层生成失败不影响任何内容，转录永远完整落盘。

### 数据落盘（回答"录音和文字会保存吗"）

| 数据 | 是否保存 | 说明 |
|---|---|---|
| **音频** | ❌ **从不** | 只读音频流做转写，不写任何音频文件；要留录音请自己另外录 |
| **原始 ASR 转录** | ✅ **总是** | 每句记 `**ASR**:` 行（未修正）。LLM 偶尔过度修正，原始版是复核凭据 |
| 双语文字（会话文件） | ✅ **总是** | `sessions/` 实时写入，防丢底稿 |
| 双语文字（Obsidian） | ⚠️ 询问后 | 需 `--course`；答"是"才复制进库 |
| 草稿译文 / 💡 术语行 | ❌ | 只在屏幕上显示 |
| `.deepseek_key` | ✅ | 配置，非课堂内容（**已在 `.gitignore` 中**） |

> **为什么实时落盘**：曾经出现过一次事故 —— 45 分钟、544 句的课堂记录，在"是否保存"提示处误按 **Ctrl+C**，旧版本把它当成"不要保存"直接丢弃，内容永久丢失。现在每句定稿立即写文件，`kill -9` 强杀也只丢最后一句；Ctrl+C 与 EOF 一律默认保存；答"否"也不会丢（会话文件仍在，`cl last` 可查看）。

---

## 常见问题

<details>
<summary><b>必须联网吗？说好的全本地呢？</b></summary>

ASR 与 UI **完全本地**，音频一个字节都不出本机。默认 `--engine auto` 只把**识别出的文本**发给 DeepSeek 换更高质量的中文译文（一节课约 0.1 元）。

要绝对离线：`cl local`，或干脆不配 key —— 云端失败会自动降级到本地 Qwen3-1.7B。代价是译文质量从 ⭐⭐⭐⭐⭐ 掉到 ⭐⭐。

</details>

<details>
<summary><b>Windows / Intel Mac 能用吗？</b></summary>

**不能。** 本地推理依赖 MLX（Apple Silicon 专属的 Metal 张量框架），悬浮窗依赖 AppKit。Intel Mac 和 Windows 需要换推理后端（llama.cpp / Ollama）并重写 UI 层。

</details>

<details>
<summary><b>讲师站在讲台很远，能收到吗？</b></summary>

能。**瓶颈不是信噪比，是绝对电平。** 实测：把一段真实课堂录音**等比衰减**（信号和噪声一起降，信噪比完全不变），词数从 73 掉到 6 —— 说明 Silero VAD 和 Parakeet 都吃"响度"。

修法是进 VAD/ASR 之前先补电平（`capture.PeakNormalizer`）。实测可用动态范围扩大约 **24dB**（≈ 自由场 4.6 倍距离）：

| 输入电平 | 修复前 | 修复后 |
|---|---|---|
| -30 dBFS（正常） | 73 词 | 71 词 |
| **-42 dBFS** | **35 词** | **74 词** |
| -54 dBFS | 12 词 | 69 词 |
| -66 dBFS | 5 词 | 56 词 |

> ⚠️ **别靠调低 VAD 阈值来"更灵敏"**。实测 `threshold=0.2` 在弱信号下确实多触发，但 VAD 会近乎恒为"有语音"，句子再也断不开、只能靠 12s 硬切，**正常音量下反而丢词**。灵敏度要补电平，不是降阈值。

</details>

<details>
<summary><b>为什么不用更大的模型？4B 不是更准吗？</b></summary>

试过，**必 OOM**。本机（M1 Pro 16GB）的上限由「模型大小 × prompt 长度」共同触发：Qwen3-4B / Qwen3.5-4B 直接崩，Qwen2.5-3B 能跑但格式遵循差、慢一倍。已试 `set_cache_limit` / `set_memory_limit` / `max_kv_size` 均无法突破 4B。

真正的质量杠杆是**术语表**，不是模型大小。

</details>

<details>
<summary><b>遇到 GPU 驱动崩溃 / 内核崩溃（kernel panic）？</b></summary>

2026-09-10 本程序曾触发一次 `IOGPUFamily` 驱动断言内核崩溃（非 OOM，属 MLX 已知 bug 族 mlx-lm#883 / mlx#3186）。当前配置已缓解：`set_cache_limit(2GB)` 宽松缓存 + 不主动 `clear_cache` + 悬浮窗 25Hz 刷新。

**跑本程序时不要同时开其他吃 GPU 的任务。** 若换 backend 或不在跑 MLX 时仍崩 → 才怀疑硬件。

</details>

<details>
<summary><b>线上课（Zoom / Teams）怎么收系统声？</b></summary>

需要先装一个 **Multi-Output Device**（音频 MIDI 设置里），把系统输出同时送到扬声器和 BlackHole 虚拟声卡，然后 `cl online`。

</details>

<details>
<summary><b>为什么收起态只有 3 句？想回看更早的怎么办？</b></summary>

收起态是**上课时的常态**（占 99% 使用时间），要的是不挡课件、余光可读。回看请点 **▾ 展开**：面板长到屏幕可见高的 60%，可自由滚动翻阅整节课，实时行钉在底部。

</details>

<details>
<summary><b>术语怎么加？听到新词怎么办？</b></summary>

直接往 `glossary/<课号>.txt` 加一行，下次启动即生效。术语解释三档（`gloss`/`basic`/`skip`）写在 `term_notes.json`，改完跑 `python build_notes.py --rebuild`。

> ⚠️ **别放单字母词**（如把 R 语言写成 `R`）：`select_terms` 的"整句包含"判断会让 `"r" in sentence` 几乎**每句都真**。

</details>

---

## 已知限制

- **⚠️ GPU 驱动稳定性**：详见上方 FAQ。跑本程序时不要同时开其他吃 GPU 的任务。
- **1.7B 本地模型质量中等**：偶有误译或"过度修正"（中文通常仍对）。这是降级兜底，不是主力。
- **讲师完全不换气时**靠 **12s 上限 + 标点断句**兜底（实测该片段 15s 内最长静音仅 0.4s）。
- **悬浮窗需图形界面会话**；无显示时自动回退终端。
- **降噪（GTCRN）、Prompt Cache 未接入**（前者需 A/B 实测，后者收益≈0）。
- **仅支持 macOS + Apple Silicon**。

---

## 路线图

- [x] 实时流式双语字幕（草稿 + 定稿两阶段）
- [x] 远场电平归一化（+24dB 动态范围）
- [x] 云端 → 本地自动降级
- [x] 聊天式可滚动悬浮窗（池化回收 + 跟随状态机）
- [x] 术语三档分类 + 点击展开 + 钉住
- [x] Obsidian 双层笔记（复习层 + 折叠逐字转录）
- [x] 翻译关掉仍做上下文英文矫正
- [ ] 真机跑一节完整课堂，验证真实语料下的滚动 + 术语解析
- [ ] 降噪接入（GTCRN）的 A/B 实测
- [ ] 说话人分离（多人讨论课时区分讲师 / 学生）
- [ ] Intel Mac / Windows 后端（换 llama.cpp 或 Ollama）

---

## 贡献

欢迎 Issue 和 PR。这个项目大量结论来自**实测**，所以：

- 提交性能相关的改动时，请附上**真实负载**的测量（不要循环同一批输入 —— 那会把缓存伪影当成加速）。
- 注释里的数字必须来自测量。
- 改动 `overlay.py` 的 `pump()` 或滚动逻辑前，请先读 `docs/DESIGN.md` 里对应那一节，那里记了每个坑。

## License

[MIT](LICENSE) © 2026 OUENMING

---

## Star History

如果这个项目帮到了你，欢迎点个 Star ⭐

[![Star History Chart](https://api.star-history.com/svg?repos=OUENMING/lecture-live&type=Date)](https://star-history.com/#OUENMING/lecture-live&Date)
