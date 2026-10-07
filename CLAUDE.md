# CLAUDE.md

## 这是什么

ClassLive —— 作者自用的**实时英译中课堂字幕**工具：采音频 → 转写 → 逐句修正并翻译 → 悬浮字幕 → 实时落盘、课后渲染双层 Obsidian 笔记。零参数 `cl` 启动；跑在本机，音频不出机器，只发文本。MIT 开源，已脱敏。

## 文件地图

每行是一个 context pointer：先读命中的那一行，再顺着 target 往下。整体地图见 `ARCHITECTURE.md`。

`docs/` 的分区（活文档 / 归档快照 / 可复跑实验）与权威状态见 `docs/README.md`。
⚠️ `docs/archive/` 里的**全部是某一天的快照** —— 只当史料读，**其中的「下一步」一律不作数**，
现在的账在 `docs/HANDBOOK.md §10`。

| 分支（什么时候读） | 读哪个 |
|---|---|
| ⭐⭐ **第一次接手/新会话/新的人** —— 从零上手的全景手册（现状、硬规矩、全部事故与教训、文档索引、公开的账） | `docs/HANDBOOK.md` |
| ⭐ **该做什么、按什么顺序做** —— 动手前先读这份 | `docs/PLAN-roadmap.md` |
| **P1：脱离终端（`.app` 启动器 + 引导式更新）** —— 动手前读 | `docs/PLAN-p1-app-launcher.md` |
| **架构判断、模块深度、seam** —— 动代码前读，但它是 **2026-09-18 的历史快照**，**正文行号一律别抄** | `ARCHITECTURE.md`（文首有横幅说明哪几条已解决） |
| 更新机制（分级 / 自动更新 / 卡片） | `docs/PLAN-update-mechanism.md` |
| 笔记复习层重做 + 课件联动 + 多用户 + UI 动效 | `docs/PLAN-notes-and-ui.md` |
| **导入入口 + 无 Obsidian 时的导出**（调研 + 设计，含全部实测数字） | `docs/RESEARCH-entry-and-export.md` |
| **产品形态（要不要做成 .app）+ VPS 评估 + 课件→关键词** | `docs/RESEARCH-product-shape.md` |
| ⭐ **零配置上传**（测试数据 → Cloudflare Worker + R2；**用户侧零配置**，凭据是公开 write-only token） | `docs/PLAN-zero-config-upload.md`（**动手前先读**） |
| 外部评审（功能/架构/技术/思路，含**已知薄弱点**） | ⚠️ **不在仓库里**（作者决定不推送评审文档）。在 `~/Desktop/classlive-review/`：`CODE-REVIEW-20261001.md`（最新）· `REVIEW-2026-09-24.md` |
| 启动、命令行参数、课程切换 | `cl` → `main.py` |
| **构建可双击的 `.app`**（方案 I：`.app` 就是安装目录） | `make-app.sh`（**动手前先读它的注释**） |
| **装成系统里能直接启动的 app**（`/Applications` 符号链接） | `install.sh`（**动手前先读它的注释**，§3.11 有完整论证） |
| **单实例锁**（为什么用 flock 不用 pidfile） | `instance_lock.py` |
| **磨砂面板配方**（材质/scrim/拖拽层）—— overlay 与 whatsnew 的唯一真源 | `panel.py`（**动手前先读它的文件头**） |
| **ObjC 类名归属** —— 全项目定义 ObjC 子类只此一处 | `objc_own.py`（**动手前先读它的文件头**） |
| **开课前的准备**（课件 → 候选术语 → 该课术语表） | `prep.py`（**动手前先读它的文件头**）、`extract.py` |
| **课程清单 / 片段解析 / 每课的「准备度」**（`cl course` 与第二批面板共用；`glossary/` ∪ `~/.classlive/courses/` 的**并集**） | `courses.py`（⚠️ **`cl course` 的模糊匹配只此一处** —— 别在 shell 里再写一份） |
| **一份课件该归哪门课**（机械层 + 模型层；`entry_panel` 的批量归档调它） | `classify.py`（⚠️ 命令行**只看不动**：`--limit` / `--no-model` 零成本试跑；`--sessions <目录>` 换课程关键词表的来源 —— 默认是仓库的 `sessions/`，**测的时候指向别处**） |
| **`~/.classlive/` 那族新路径的唯一定义点** | `paths.py` |
| **macOS 视觉语言**（同心圆角/字号字距/对比度门槛/材质硬规则/原生指纹）—— 动 `overlay.py` 外观或做第二批面板前读 | `docs/RESEARCH-macos-aesthetic.md` |
| **实时字幕的行数与 roll-up**（为什么 2 行 / 断点降级链 / 上滚即冻结 / 贴底 / 全部一手出处）—— **动草稿或字幕行前读** | `docs/RESEARCH-live-caption-rollup.md` |
| **P3 第二批：课程卡片面板**（三份 UX 调研 + 作者的 6 个决定 + 美感取向 + 动手前先验的两条）—— **做面板前先读** | `docs/PLAN-entry-panel.md` |
| **面向用户的提示**：说人话的弹窗 / 麦克风权限三态 / 跳系统设置 | `notice.py` |
| 主循环、后台线程、队列、UI 路由与落盘分发 | `main.py` |
| 音频采集、麦克风/系统声、电平归一化、**上课中途换输入设备**（`resolve_input_device` 是「录哪个设备」的唯一定义点） | `capture.py` |
| 断句、VAD、静音阈值 | `vad.py` |
| 转写、ASR、Parakeet、听错修正 | `asr.py` |
| 翻译、prompt、DeepSeek 云端、mlx 本地、引擎降级 | `translator.py`、`cloud_translator.py` |
| **云端翻译的前文窗口**（块对齐 / 前缀缓存 / 为什么稳定内容放前）—— 动 `cloud_translator._user_content` 前读 | `docs/experiments/context_ab.md`（复现 `context_ab.py`） |
| **下载模型**（直连 → 镜像 → sha256）—— 动 `models.py` 的 `cmd` / 镜像名单 / 钉死的 sha 前读 | `fetch_model.py`（文件头写了为什么镜像必须过 sha、直连不强制） |
| 术语表、课号、术语查表与注入 | `build_notes.py`、`glossary/`（样例 `glossary.example.txt`） |
| 笔记落盘、`sessions/` 文件格式、Obsidian 双层笔记、**❓「没听懂」的旁路文件 + 课后反查**（`sessions/<同名>.lost.jsonl`，**绝不改会话抬头**） | `obsidian_writer.py` |
| **实时总结（原子 + 章节纲要）** —— 窗口规则 / 重试与积压 / 章节状态机 / 课务 / 草稿与调试入口。⚠️ `main.py` 那边**只接线**，逻辑全在这里 | `live_summary.py`（**动手前先读文件头**） |
| **测试模式的上传**（队列/退避/幂等/脱敏）—— 课后把一节课的文件传去 **Cloudflare Worker + R2**（零配置）。⚠️ `send` 是唯一的传输洞；判据用**本地目录 / 本机 HTTP 服务器**，不碰真服务器 | `upload.py`（**动手前先读文件头**）+ `docs/PLAN-zero-config-upload.md` |
| **章节层** —— `.chapters.jsonl` 的三种记录、写入器、`parse_reply` 的机械闸门、课务正则、`chapter_path_for` | `chapter.py` |
| 整课二级精修（polish） | `polish.py` |
| 悬浮窗、字幕显示、滚动、槽位池化 | `overlay.py`、`transcript_view.py` |
| 滚动行为验收探针（不在运行路径上） | `scripts/probe_scroll.py` |
| **动效基线/验收探针**（不在运行路径上；**确定性**，不靠人手）—— 动 `overlay.py` 的动效前跑 | `scripts/probe_motion.py` |
| **草稿上滚的动效方案**（调研 / 时长三方收敛 / 四条判据 / 谁拥有 frame）—— 做动效前读 | `docs/PLAN-roll-motion.md` |
| **拖拽落点验收探针**（不在运行路径上；**改拖拽相关代码前跑它**，作者手拖一次即可） | `scripts/probe_drag.py` |
| **课程卡片面板验收跑器**（不在运行路径上；**隔离模式** —— 术语表是 /tmp 的副本，删词/撤销都改副本） | `scripts/probe_entry_panel.py` |
| **测试模式开关的拍图探针**（不在运行路径上；**一个字节都不写盘** —— `on_test_mode` 是桩。推送给作者看效果前跑它） | `scripts/probe_test_mode.py` |
| **零参数入口**（双击 `.app` → 先开面板 → 点「开始上课」才录课；**独立短进程**，录课那条路一个字没动） | `entry_launch.py`（⚠️ 退出码 0/1/2/3/4 是它与 `cl` 的契约；`CL_NO_PANEL=1` 退回老行为。**落点条上那颗「测试模式」开关**写 `.test-mode` → `cl` 读 → `--test-mode`） |
| 回归测试 R1–R5、毫秒级断言 | `tests/test_audit_regressions.py` |
| 端到端、拿真实录音跑通 | `scripts/test_pipeline.py` |
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
- **镜像名单会腐坏，校验不会。** `fetch_model.py` 里镜像来的文件必须过 sha256；`ghfast.top` 在测试网络上返回自签证书（连接被换），所以不在名单里，也不用 `-k` 绕过。加镜像前先用 `tests/test_fetch_model.py` 的本机服务器模式验一遍流程，再真下一个小文件核 sha。
- **云端 prompt 里稳定内容在前、逐句变化的在后。** DeepSeek 前缀缓存只认「从头逐字相同」：`ctx_chunk>0` 时前文窗口同块内只往后追加，本句召回术语放在前文之后。`ctx_chunk=0` 的老布局被 `tests/test_translator.py` 逐字冻结，改它要连测试一起说明理由。
- **不阻塞不变量**：`capture` / `vad` 的回调必须立刻返回；AppKit 的调用只能发生在主线程。往流水线里加活先想这两条。
- ⚠️ **加在磨砂面板上的新交互元素，先查 `mouseDownCanMoveWindow`。**
  **事实**：它是 AppKit「按下背景即拖动窗口」的开关，**默认值是 `!isOpaque`**
  （`docs/archive/OVERLAY-RESIZE-REVIEW.md` §2.4 量的）→ **我们的面板是不透明的反面，
  所以每个新视图一出生就是 `True`。**
  ⚠️ **已经咬过两次**：① 窗口四角缩放（`OVERLAY-RESIZE-REVIEW.md` §2.4/§2.6）·
  ② 加 `NSSplitView` 的 pane（本轮调研：**裸 `NSView`/`NSVisualEffectView` 的 pane 是
  `True`，拖 pane 会拖窗口**；`NSSplitView` 自己是 `False`，所以只坑 pane）。
  ⚠️ **2026-09-28 修正 —— 下面这句曾写成「默认 `True` → 鼠标按下就会被当成拖窗口」，
  那是把机制写宽了**（实测见 `tests/test_panel.py` 那条「术语行点击真的触发回调」）：
  · **视图自己实现了 `mouseDown_` → AppKit 把事件交给它，`True` 也不拖窗口**
    （`overlay.ClickView` 就是：`flag=True` 而回调照常触发，红/绿两态都响）
  · ⭐ **之前咬人的两次，受害视图都没有 `mouseDown_`** —— 那才是真条件
  → **清单项**：新交互视图**自己接管 `mouseDown_`**、**或**显式设
  `mouseDownCanMoveWindow -> False`（**任取其一**），并加一条**行为**断言 ——
  **钉「回调真的触发」，别钉 flag 值**（钉 flag 会得出「它是坏的」这个错误结论，
  因为 `ClickView` 现在就是 `True` 且完全正常）。
  ⚠️ 未覆盖：app **激活态**下没测过（要 `activateIgnoringOtherApps_`，会抢用户焦点）；
  本 app 是 `.accessory`、面板是 NonactivatingPanel，正常使用走不到那个状态。
  （`tests/test_panel.py` 有**两条互补**的先例：一条断言拖拽层那个是 `True`、
  一条断言术语行点击真的触发回调。）
  ⚠️ 相关的还有一条**只存在于 NSWindow 的属性**：`ignoresMouseEvents`
  （整个 AppKit 头目录只有 `NSWindow.h` 一处，**NSView 没有** —— 网上写
  `view.ignoresMouseEvents = true` 的是错的）→ **穿透一开，面板内一切交互必然失效**。

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
  ⚠️ **2026-09-30 起 `cl update` 会把 `.app` 一起跟上**（`update.py` 的 `app` 那一步：
  `cl update` → `update.py --run-step app`；卡片那条路走同一个 `run_step`）——
  在那之前它**只拉代码**，于是 `make-app.sh` / `tools/make_icon.py` 的改动
  在用户那儿**永远不生效**（**图标就是这么丢的**）。那一步自己幂等：先问戳记，没变就跳过。
  判据只在 `make-app.sh --up-to-date` **一份实现**里（`update._app_stale_reason()` 调它）；
  别在 `install.sh` / `update.py` 各算一遍指纹（`make-app.sh --fingerprint` 是给判据取值的口子）。
  ⚠️ 它**不含 `requirements.txt`** —— 依赖是 deps 步骤直接装进 `.app` 那个 python 的，不需要重建。
  ⚠️⚠️ **也不含 `VERSION`**（2026-10-04 改，原来在）。`VERSION` 每版都变，而它唯一的落点是
  Info.plist 的 `CFBundleShortVersionString` —— **全仓没有一个代码读者**（读 Info.plist 的地方
  要的都是 `CFBundleIdentifier`；代码读的是仓库根那份，见 `update._version()`），只有 Finder
  「显示简介」看得到。它进指纹 = **每发一版所有用户白重建 1–3 分钟**，而重建是那条
  「可能打断正在录的课」的危险路径。**别把它加回去**（`tests/test_update.py` B13 钉着）。
- **新增 streamq tag 必须在 `main.drain()` 加同分支** —— 它是唯一的 tag 分发点，漏改即静默丢弃。
  ⚠️ **2026-09-30 起末尾有一条兜底 `else`**：未识别的 tag 会 `echo` 一行告警（**出声**，不再静默丢）。
  但它**只是告警**，不改变「必须加同分支」这条 —— 加了 tag 却不加分支 = 数据照丢，只是你能看见。
- **实时总结的开关是 `CLASSLIVE_LIVE_SUMMARY`**（阶段 3 默认 `"0"` 关、阶段 4 翻成 `"1"`）。
  ⚠️ 它关的是**章节合成 / 纲要落盘 / 往 `streamq` 发消息**这三件。
  ⚠️⚠️ **两处改进不在开关后面**（有意为之）：① 模型调用失败时窗口**留着重试**而不是丢弃；
  ② 下课时**残余窗口补提交**。它们修的是今天确实存在的丢数据问题。
- **会话 Markdown 格式是三方共享契约**：`obsidian_writer` 写它、`_parse` 读回它、`cl last` 用 grep 匹配它；改格式会同时打断三处。
  ⚠️ **抬头正则 `_TS` 是行尾锚定的** —— 所以「课上按下的标记」一律走**旁路文件**
  （`sessions/<同名>.lost.jsonl`），**不许**往抬头加字段：多一个后缀 `_parse` 就认不出
  那一条，会把它的 EN/ZH/ASR **静默盖到上一条**头上（上一条被替换、这一条消失）。
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
- **个人数据保持不入库**：`.gitignore` 覆盖 `sessions/`、`glossary/`、`.course`、`.test-mode`、`.deepseek_key`、`term_notes*.json`；真实课号已三次脱敏。改 `.gitignore` 前先想清楚这一条。

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
- ⭐ **碰过收尾路径 / 测试报告**（`main.py` 的 `spin_until` 或收尾那段、`testmode.finish`）：
  `ClassLive.app/Contents/MacOS/python scripts/probe_testmode_report.py`
  —— **要真跑一个会话**（约 1 分钟，会往 `sessions/` 写一节测试记录）。
  ⚠️ 它防的是「报告**静默**没写成」那类**只有真跑才看得见**的故障 —— 那件事藏了整整一周
  （`report.json` 从 2026-09-24 起一个都没有）。它**不在**默认闸门里（默认闸门要毫秒级）。
  ⚠️ 它**不在**默认闸门里，要单独跑 —— 覆盖 `pull()` 的三条安全边界（脏树停手且**文件数不变** /
  `--ff-only` 不造 merge / 已最新跳过）与自动更新的分级（默认拒绝 / 跨版本夹 manual 拒绝 / 限频 / 并发锁）。
- **碰过单实例锁**（`instance_lock.py` / `main.run()` 开头）：
  `ClassLive.app/Contents/MacOS/python tests/test_instance_lock.py` 全绿。
  ⚠️ 核心那条是「被 kill -9 之后锁自动释放」—— 但**它单独是恒真的**，
  必须和 C1/C2 连读（见那个测试的 docstring）。
- **碰过下载 / 镜像**（`fetch_model.py` / `models.py` 的 `cmd` / `update.download_model`）：
  `ClassLive.app/Contents/MacOS/python tests/test_fetch_model.py` 全绿。**不在默认闸门里**。全离线（本机 HTTP 服务器当上游和镜像）；
  真网络端到端要在隔离 `HOME` 下跑（`HOME=/tmp/x …`），别写进真 `~/models/`。
- **碰过云端翻译的 prompt 布局**：`ClassLive.app/Contents/MacOS/python tests/test_translator.py` 全绿（含老布局逐字冻结那条）。
- 碰过流水线 / 音频路径：`ClassLive.app/Contents/MacOS/python scripts/test_pipeline.py <音频> [start] [dur] [speed]` 能跑完。
- **碰过面板**（`panel.py` / `overlay.py` 的窗口构造 / `whatsnew.py`）：`ClassLive.app/Contents/MacOS/python tests/test_panel.py` 全绿。
  ⚠️ 它**不在**默认闸门里，要单独跑。判据是**同进程跟「抽取前的配方」对拍** ——
  那份老配方**逐字冻在测试文件里**（`frozen_recipe`），当场再建一个面板，两边用
  **同一份枚举**（`dump` + `render_hash`）比。这样做的两个理由（2026-09-26 审查指出）：
  · **不绑机器** —— 原来写死一个 sha256 常量，对 backing scale / 系统版本敏感，
    换台 Mac 必假失败，而它被当仓库闸门。同进程对拍比的是「有没有差别」。
  · **不靠手挑** —— 原来「手挑 13 个属性 + 一个哈希」，而手挑清单**不是闭集**：
    `hasShadow` / `contentMinSize` / `titleVisibility` / `isMovable` 两条腿都盖不住。
    现在两边比同一份 `dump`（21 项 + 递归视图树），名单里有什么就比什么。
  ⚠️ **别改 `frozen_recipe`** —— 它是参照物。真想改配方时这条测试会红，那正是要的：
  逼你想清楚「这次是有意改行为，还是改坏了」。
  ⚠️ **它盖不住什么也要清楚**：那是**静态摊平**，所以 **live resize** 不在里面
  （`sendEvent_` 的分派单独由第 ⑤ 组钉住）。
  ⚠️ **拖拽的接受行为 2026-09-28 起有判据了**（原来"拖拽不在这里面"）：
  第 ⑨ 组直接调 `draggingEntered:` / `draggingUpdated:` / `performDragOperation:`
  的**返回值**（契约就是"收不收由返回值决定"），配一个**桩 sender + 假 pasteboard** ——
  不需要真拖拽、不需要事件循环、不需要窗口。⚠️ 假 pasteboard **必须实现 `types()`**
  （`panel.dragging_entered` 那行 `_log(f"…types=…")` 是**提前求值**的，且在 `_call` 的
  try **外面** → 缺了它 `draggingEntered:` 会**抛异常逃出去**）。
  仍**只靠手验**的：真实 Finder 拖拽（`scripts/probe_drag.py`）、live resize。
- **碰过开课前的准备**（`prep.py` / `extract.py` / `paths.py`）：
  `ClassLive.app/Contents/MacOS/python tests/test_extract.py` 与 `tests/test_prep.py` 全绿。
  ⚠️ **两条都不在默认闸门里**，要单独跑。**它们钉住了什么看两个文件自己的 docstring** —— 那是判据的唯一定义点，别在这里再抄一份（抄了会腐坏）。
  ⚠️ **改 `glossary/<课号>.txt` 的行为前先想清楚**：它是**手写内容与自动内容共处**的文件，
  纪律是**只追加**（见 `prep.py` 文件头）。
- **碰过课程清单 / `cl course`**（`courses.py` / `cl` 的 `course` 分支）：
  `ClassLive.app/Contents/MacOS/python tests/test_courses.py` 全绿。**不在默认闸门里**。
  判据、以及**做变异测试时的两条自测纪律**（`.pyc` 会让「还原」变成假的；测试数据会把自己的断言遮住）
  都写在那个文件的 docstring 里 —— 那是唯一定义点，别在这儿再抄一份。
- **碰过课程卡片面板 / 零参数入口**（`entry_panel.py` / `entry_launch.py` / `panel.py` 的拖拽落点）：
  `ClassLive.app/Contents/MacOS/python tests/test_entry_panel.py` 全绿。**不在默认闸门里**。
  它钉的是**纯函数那半**：卡片上显示什么字、结果计数与列表**同源**、卡片高度与坐标同一组常数推导、
  以及零参数入口的**退出码契约**（0/1/2/3 —— `cl` 只读得动这个）。
  ⚠️ **AppKit 装配那半不在这里**：配方靠 `tests/test_panel.py` 对拍，拖拽/落点靠
  `scripts/probe_entry_panel.py`（隔离模式）作者手验。
  ⚠️ 写这个文件的断言时**先问「改坏实现它会不会红」** —— 本文件里被抓出过 4 条没有区分能力的
  （恒真 / 断言的是 Python 字面量 / 夹具日期不覆盖它声称的行为 / 隔离路径硬编码）。
- 碰过 `overlay.py` / `transcript_view.py` 的**布局或滚动**：`ClassLive.app/Contents/MacOS/python scripts/probe_scroll.py` 验滚动行为。
  ⚠️ **它本身不稳定**（2026-09-26 实测）：**改动前的代码连跑三次**，两次「阶段 1 ✅ / 阶段 2 ❌」、
  一次反过来 —— 每次都是**恰好一个阶段收到 0 个滚轮事件**，而失败的阶段会翻转。
  所以它报的 ❌ **先别当成回归**：跑两三次看是不是在翻转，或者拿 `git stash` 对拍改动前的版本。
  （翻转 = 探针自己的问题；稳定挂在同一阶段才是真回归。）
- 报"可用"之前先跑上面命中的那条、贴出输出，再下结论。
