# CLAUDE.md

## 这是什么

ClassLive —— 作者自用的**实时英译中课堂字幕**工具：采音频 → 转写 → 逐句修正并翻译 → 悬浮字幕 → 实时落盘、课后渲染双层 Obsidian 笔记。零参数 `cl` 启动；跑在本机，音频不出机器，只发文本。MIT 开源，已脱敏。

## 文件地图

每行是一个 context pointer：先读命中的那一行，再顺着 target 往下。整体地图见 `ARCHITECTURE.md`。

| 分支（什么时候读） | 读哪个 |
|---|---|
| ⭐ **该做什么、按什么顺序做** —— 动手前先读这份 | `docs/PLAN-roadmap.md` |
| **P1：脱离终端（`.app` 启动器 + 引导式更新）** —— 动手前读 | `docs/PLAN-p1-app-launcher.md` |
| **架构判断、模块深度、seam** —— 动代码前读，但它是 **2026-09-18 的历史快照**，**正文行号一律别抄** | `ARCHITECTURE.md`（文首有横幅说明哪几条已解决） |
| 更新机制（分级 / 自动更新 / 卡片） | `docs/PLAN-update-mechanism.md` |
| 笔记复习层重做 + 课件联动 + 多用户 + UI 动效 | `docs/PLAN-notes-and-ui.md` |
| **导入入口 + 无 Obsidian 时的导出**（调研 + 设计，含全部实测数字） | `docs/RESEARCH-entry-and-export.md` |
| **产品形态（要不要做成 .app）+ VPS 评估 + 课件→关键词** | `docs/RESEARCH-product-shape.md` |
| 外部评审（功能/架构/技术/思路，含**已知薄弱点**） | ⚠️ **不在仓库里**（作者决定不推送评审文档）。在 `~/Desktop/classlive-review/REVIEW-2026-09-24.md` |
| 启动、命令行参数、课程切换 | `cl` → `main.py` |
| **构建可双击的 `.app`**（方案 I：`.app` 就是安装目录） | `make-app.sh`（**动手前先读它的注释**） |
| **装成系统里能直接启动的 app**（`/Applications` 符号链接） | `install.sh`（**动手前先读它的注释**，§3.11 有完整论证） |
| **单实例锁**（为什么用 flock 不用 pidfile） | `instance_lock.py` |
| **面向用户的提示**：说人话的弹窗 / 麦克风权限三态 / 跳系统设置 | `notice.py` |
| 主循环、后台线程、队列、UI 路由与落盘分发 | `main.py` |
| 音频采集、麦克风/系统声、电平归一化 | `capture.py` |
| 断句、VAD、静音阈值 | `vad.py` |
| 转写、ASR、Parakeet、听错修正 | `asr.py` |
| 翻译、prompt、DeepSeek 云端、mlx 本地、引擎降级 | `translator.py`、`cloud_translator.py` |
| 术语表、课号、术语查表与注入 | `build_notes.py`、`glossary/`（样例 `glossary.example.txt`） |
| 笔记落盘、`sessions/` 文件格式、Obsidian 双层笔记 | `obsidian_writer.py` |
| 整课二级精修（polish） | `polish.py` |
| 悬浮窗、字幕显示、滚动、槽位池化 | `overlay.py`、`transcript_view.py` |
| 滚动行为验收探针（不在运行路径上） | `probe_scroll.py` |
| 回归测试 R1–R5、毫秒级断言 | `tests/test_audit_regressions.py` |
| 端到端、拿真实录音跑通 | `test_pipeline.py` |
| 面向用户的功能说明、开源与脱敏须知 | `README.md` |
| 环境搭建、依赖清单、选型理由 | `docs/DESIGN.md` |

产物位置：实时会话写 `sessions/`，课后笔记写 `<vault>/Lectures/`；vault 由 `--vault` / `$OBSIDIAN_VAULT` 指定，默认值写在 `cl` 里。

## 本仓库特有雷区

- ⭐ **动手改代码之前，先加载相关 skill**（作者 2026-09-19 / 09-20 两次提过，硬规矩）。
  他 99 天囤了 42 个 skill 却只调用 39 次 —— **装了不用等于没装**。
  常用对应：改完质检→`simplify` · 排查 bug→`systematic-debugging` ·
  声称完成前→`verification-before-completion` · 动模块边界/接口→`codebase-design` ·
  写文档/给 agent 看的东西→`writing-for-agents`。
  ⚠️ `~/.claude/skills/` 里**看得见文件 ≠ 能用** —— `settings.json` 的 `skillOverrides`
  里标 `"off"` 的会**静默不可用**（现在关了 12 个，含 `code-review`）。
  ⚠️ **同时把方案里说不清、没实测、靠印象的点先查实**（阈值、API 行为、别人的现成做法）。
  判定口径：**任务足够大（≥ 几个文件的实质改动 / 会改行为）就值得先加载 + 先调研**；
  一行 typo 不用。

- **发版默认 `manual`** —— 只有作者**特意注明**"后台自动更新"才写 `### 更新方式 auto`。
  **默认不写那一节**（不写 = manual，用户点卡片上的「立即更新」按钮更新）。
  ⚠️ 别因为"这版没有依赖变化"就自作主张写 `auto` —— 作者 2026-09-25 收回了这个默认。
  ⚠️ **也不能靠版本号推**：`3.4.0 → 3.5.0` 是 minor 号却是 breaking（模型改必装）。
  ⚠️ **更新日志文案要先打开给作者确认再发版**（他对措辞有明确品味，已手改过两次）。
  ⚠️ 自动更新的日志在 `~/Library/Logs/ClassLive/update.log`（静默失败**只**写这里）；
  `CLASSLIVE_NO_AUTO_UPDATE=1` 可关掉它（排查用）。
- **发版流程**：写 CHANGELOG 条目 → **打开给作者确认** → bump `VERSION` → commit →
  `git tag` → push main + tag → `gh release create` → 弹卡片给他看效果。
- **EN 为基准防倒退**：整课精修同时收到直播定稿的 EN 与 ASR 原文，**EN 是基准**；中文只能由 EN 修正，不能反过来改写 EN。
- **`polish.py` / `tests/` 是在 git 里的**（`git ls-tree -r HEAD` 可验；2026-09-24 核实）。
  ⚠️ 本条曾写成"不在 git 里、clone 后没有" —— 那是 2026-09-18 的旧状态（当时它们确实
  还是未跟踪的 `??`），之后已提交。留这段是因为 `obsidian_writer.close()` 的
  `from polish import polish_entries` 确实被 `try/except` 包着（文件缺失时打一行 ⚠，
  笔记照样生成、退回直播版转录）—— **这个 fail-soft 设计本身值得保留**，但它防的是
  "文件被删/环境不完整"，不是"新 clone 拿不到"。
- **不阻塞不变量**：`capture` / `vad` 的回调必须立刻返回；AppKit 的调用只能发生在主线程。往流水线里加活先想这两条。
- ⚠️ **shell 脚本里 `$VAR` 后面紧跟任何非 ASCII 字符都会被吞** ——
  bash 在 UTF-8 locale 下把那个字符的首字节当成标识符的一部分，
  于是 `$PYVER）` 被解析成「名为 `PYVER）` 的变量」→ `set -u` 直接报 unbound。
  **实测 `）` `。` `，` `：` `（` `、` `】` `·` 全都触发**（含 Latin-1 的 `·`）。
  → **紧跟非 ASCII 时必须写 `${VAR}`**。
  ⚠️ 最阴的是它**只在出错路径上咬人** —— 两个实际案例都是 `||` 后面的提示文案，
  平时跑不到，等真出错时才发现「本来要打印提示，结果崩了」。
  自查：`grep -nP '\$[A-Za-z_][A-Za-z0-9_]*[^\x00-\x7F]' *.sh cl`（注释命中是正常的，那是讲这个坑的）
  ⚠️ **2026-09-26 又踩了两次**，都是在新写的出错路径上（`|| fail \"…$VAR（…\"`）——
  这条不是历史记录，是活的。
- ⚠️ **`make-app.sh --up-to-date` 的退出码方向是故意的**（`0` = 最新，**非 0 = 该重建**）。
  反过来（`0` = 该重建）看着更自然，但**脚本自身一出错也落成非零** → 调用方读成「最新」
  → **静默跳过重建**。2026-09-26 实测踩到：`$_want）` 触发上面那条 $VAR 雷区，
  `install.sh` 当场静默跳过。**凡是要给别的脚本/进程看的判据，先问「它出错时倒向哪边」**。
- **构建戳记 `ClassLive.app/Contents/.build-stamp`**（2026-09-26 起）：记的是构建输入的指纹。
  `cl update` **只拉代码、不重建 `.app`** —— 没有戳记的话 `make-app.sh` /
  `tools/make_icon.py` / `VERSION` 的改动在用户那儿**永远不生效**（图标就是这么丢的）。
  判据只在 `make-app.sh --up-to-date` **一份实现**里；别在 `install.sh` / `update.py` 各算一遍指纹。
  ⚠️ 它**不含 `requirements.txt`** —— 依赖是 deps 步骤直接装进 `.app` 那个 python 的，不需要重建。
- **新增 streamq tag 必须在 `main.drain()` 加同分支** —— 它是唯一的 tag 分发点，漏改即静默丢弃。
- **会话 Markdown 格式是三方共享契约**：`obsidian_writer` 写它、`_parse` 读回它、`cl last` 用 grep 匹配它；改格式会同时打断三处。
- **`--context` 有两个默认值**：CLI 是 5，`translator.load_translator` / `CloudTranslator` 是 2；直接调库拿到的行为与 `cl` 不同。
- **环境不可复现**：`requirements.txt` 存在（只有下界、无 lock），没有 `pyproject.toml` / `uv.lock`；
  选型理由写在 `docs/DESIGN.md`。
  ⚠️ 本条曾写成「**没有** `requirements.txt`」—— 那是 2026-09-24 之前的旧状态，该文件之后已建。
  ⚠️ **运行环境住在 `ClassLive.app/Contents/` 里，不是仓库根目录的 `.venv`**（2026-09-26 起）。
  里面的 python **没有 pip**（uv venv 默认不带）—— `… -m pip` 会失败，装包一律走
  `uv pip install --python ClassLive.app/Contents/MacOS/python …`。
  **为什么这么放**：让 macOS 认那个目录为 bundle，麦克风授权框才写「ClassLive」
  而不是「python3.11」。根因与配方见 `docs/PLAN-p1-app-launcher.md` §1.5。
  ⚠️ **别用 `uv python find 3.12` 拿基础解释器** —— 不加 `--managed-python` 它会返回
  项目自己的 venv，而那是基于 Homebrew 的 framework 构建，整个方案的前提就不成立。
- **`IOGPUFamily` 内核崩溃史**：2026-09-10 本工具触发过一次 GPU 驱动断言 panic（非 OOM）；`translator._configure_mlx` 是缓解措施。动 mlx / 本地模型路径时留意。
- **`sessions/` 只追加**：曾误删过一节真实课堂记录、不可恢复；里面的 `*_TEST.md` 是测试残留，也留着。
- **个人数据保持不入库**：`.gitignore` 覆盖 `sessions/`、`glossary/`、`.course`、`.deepseek_key`、`term_notes*.json`；真实课号已三次脱敏。改 `.gitignore` 前先想清楚这一条。

## 当前状态 —— 现查，别抄

提交数、日期、"当前在哪个 Phase" 这类数值会腐坏（rot）。这份文档每行都在每轮付 context load，所以状态只放指针：

| 想知道 | 命令 / 位置 |
|---|---|
| 未提交改动、未跟踪文件 | `git status --porcelain` |
| 已落地历史（HEAD 停在哪） | `git log --oneline -15` |
| 计划在哪 | `ls -t docs/PLAN-*.md`（**别写死文件名** —— 做完的那份会腐坏） |

## 完成判据

- 默认闸门：`ClassLive.app/Contents/MacOS/python tests/test_audit_regressions.py` 全绿（无网络、无模型、毫秒级）。注意 R1/R4 复刻了实现逻辑 —— 绿 ≠ 真实流水线通过。
- **碰过更新机制**（`update.py` / `cl update` / 卡片按钮）：`ClassLive.app/Contents/MacOS/python tests/test_update.py` 全绿。
  ⚠️ 它**不在**默认闸门里，要单独跑 —— 覆盖 `pull()` 的三条安全边界（脏树停手且**文件数不变** /
  `--ff-only` 不造 merge / 已最新跳过）与自动更新的分级（默认拒绝 / 跨版本夹 manual 拒绝 / 限频 / 并发锁）。
- **碰过单实例锁**（`instance_lock.py` / `main.run()` 开头）：
  `ClassLive.app/Contents/MacOS/python tests/test_instance_lock.py` 全绿。
  ⚠️ 核心那条是「被 kill -9 之后锁自动释放」—— 但**它单独是恒真的**，
  必须和 C1/C2 连读（见那个测试的 docstring）。
- 碰过流水线 / 音频路径：`ClassLive.app/Contents/MacOS/python test_pipeline.py <音频> [start] [dur] [speed]` 能跑完。
- 碰过 `overlay.py` / `transcript_view.py`：`ClassLive.app/Contents/MacOS/python probe_scroll.py` 验滚动行为。
- 报"可用"之前先跑上面命中的那条、贴出输出，再下结论。
