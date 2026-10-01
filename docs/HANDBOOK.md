# ClassLive 手册 —— 从零上手（人和 AI 都读这份）

> 一句话：ClassLive 是作者自用的 **macOS 实时英译中课堂字幕工具** ——
> 采音频 → 转写 → 逐句修正并翻译 → 悬浮字幕 → 实时落盘 → 课后生成双层 Obsidian 笔记。
> 纯本机运行，**音频不出机器，只发文本**；MIT 开源（`OUENMING/lecture-live`）。
>
> **这份文档写给两种读者**：新接手的人、下一个会话的 AI。
> 读完它你应当能：跑起来、知道边界在哪、知道哪些坑踩过绝对不能重踩、
> 知道每个问题的权威文档在哪。**现状数字（版本/提交数/测试数）一律现查**
> （`git log --oneline -5` · `cat VERSION` · 跑一遍闸门），本文只写会长期成立的事。

---

## 0. 现在在哪（会腐坏，给出查法）

| 想知道 | 命令 |
|---|---|
| 版本 | `cat VERSION`（当前线：3.x 系列，一天能发三四个小版本） |
| 已落地什么 | `git log --oneline -15` + `CHANGELOG.md` |
| 计划在哪 | `ls -t docs/PLAN-*.md`（**别写死文件名**，做完的会腐坏） |
| 工作区状态 | `git status --porcelain` |

**发版哲学：小步快跑、修复向、manual 优先。** 2026-09-30 一天发过 3.8.0→3.8.3，
2026-10-01 深夜又 3.8.4→3.8.6 三连发。每次发布都小，但**文案和判据一次都不省**。

---

## 1. 五分钟跑起来

```bash
# 第一次装（会构建 ClassLive.app —— “.app 就是安装目录”）
./install.sh           # 内部调 make-app.sh；装依赖 + 下模型都在这一步

# 日常
cl                     # 零参数：开课面板（选课/拖课件/测试模式开关）→ 开始上课
cl online / cl local   #（已随 3.8.x 演进，见 cl help）云端 / 本地引擎
cl file 录音.m4a       # 转录已有录音（终端输出）
cl update              # 更新：拉代码 + 补依赖 + 重建 .app
cl doctor              # 自检：版本 / 依赖 / 模型 / 分支
cl setkey [key]        # 填 API key（面板就绪条点「翻译引擎」也行）
```

- **解释器只有一个**：`ClassLive.app/Contents/MacOS/python`（系统 python3 没有 AppKit）。
- 默认闸门（毫秒级、无网络）：
  `ClassLive.app/Contents/MacOS/python tests/test_audit_regressions.py`
- 关键路径：会话写 `sessions/`；课后笔记写 `<vault>/Lectures/`；
  用户状态在 `~/.classlive/`（**vault 路径解析**：`--vault` → `$OBSIDIAN_VAULT` → `~/.classlive/vault` → **None（不写 Obsidian，绝不凭空造目录）**）。

---

## 2. 它怎么工作（一张图记牢数据流）

```
麦克风/系统声 ──capture──▶ vad 断句 ──asr──▶ 草稿(EN) ──translate──▶ 定稿(EN)+中文
                                   │（云端 DeepSeek；断网降级本地 Qwen3-1.7B）
                                   ▼
                    main.run() 主循环：drain() 是唯一的 UI 分发点
                     │        （streamq：final/draft/…/summary 七种 tag + 兜底告警）
                     ▼
      overlay 悬浮字幕 ──▶ obsidian_writer 逐句落盘 sessions/<会话>.md（三方契约）
                     │
        live_summary（课中）──▶ .atoms.jsonl / .chapters.jsonl（旁路文件，不碰会话格式）
                     │
      收尾 close() ──▶ 精修(polish) → 复习层(Key Concepts/自测/术语表/Jev 重点句)
                     → 章节纲要（有旁路文件才出）→ 双层 Obsidian 笔记
```

**几个必须知道的结构事实**：

- **`sessions/<会话>.md` 是三方共享契约**：`obsidian_writer` 写它、`_parse` 读回它、
  `cl last` 用 grep 匹配它。**抬头格式一个字节都不许动**；新数据一律走**旁路文件**
  （`.lost.jsonl` / `.atoms.jsonl` / `.chapters.jsonl`）。
- **主线程不阻塞不变量**：`drain()` / AppKit 回调只做立刻返回的事（put / 赋值 / 打印）。
  联网、sleep、模型调用全在 worker 线程。
- **`.accessory` app + 磨砂面板**（`panel.py` 是配方唯一定义点）。新交互视图
  要么自己实现 `mouseDown_`、要么显式关 `mouseDownCanMoveWindow`（默认是 `True`，咬过两次）。
- 所有 ObjC 子类**只允许定义在 `objc_own.py`**。

更深的：模块逐一有文件头 docstring（**先读文件头再动那个文件**）；
架构快照在 `ARCHITECTURE.md`（⚠️ 历史快照，正文行号别抄）。

---

## 3. ⚠️⚠️ 硬规矩（不可越界；每条都有血）

1. **`sessions/` 只读不删、禁通配符** —— 曾误删过一节真实课堂记录，不可恢复。
   里面的 `*_TEST.md` 是测试残留，**也留着**。
2. **测试必须隔离写端**——读端和写端**都要**换。只 patch 读端 = 把真实数据交给测试覆盖
   （`term_notes.json` 41KB→4.8KB 的事故；后来 `.bak` 加固，救过两次）。
   ⭐ 问法要升级：**「这个调用链往下会碰哪些文件？」**（模块级全局最常漏：
   `SESSIONS` / `NOTES_FILE` / `STATE_DIR` / `REQS_STAMP`）。
3. **`~/.classlive/` 与 `glossary/` 在 `.gitignore` 里** → `git tag` 对它们是**空的**。
   动前先 `cp -a`（判据 `git check-ignore -v`）。
4. **不擅自改系统设置**（静音/音量/通知/显示/日历授权）——只报告 + 询问。
5. **git**：绝不代跑 `git config`（用一次性环境变量署名
   `GIT_AUTHOR_NAME="ENMING OU" GIT_AUTHOR_EMAIL="enming.owen@outlook.com"`）；
   **`git add -A` 不用**（按文件名加具体文件）；`sessions/` 相关改动前先 `git status`。
6. **发版默认 `manual`** ——只有作者**明确说**「后台自动更新」才写 `### 更新方式 auto`。
   别因为"这版没依赖变化"自作主张。
7. **更新日志文案必须先给作者确认再发版**；**推送前先给作者看效果**（UI/弹窗/文案）。
8. **改代码前先加载相关 skill**（作者提过三次的硬规矩）。常用对应：
   排查 bug→`systematic-debugging` · 声称完成前→`verification-before-completion` ·
   动接口/模块边界→`codebase-design` · 写文档→`writing-for-agents`。
   ⚠️ `settings.json` 的 `skillOverrides` 会**静默禁用** skill（关了 12 个）。
9. **不拿译文质量换弱防护**：任何要改 prompt 文案的"防护"，先问会不会动译文，
   会 → 默认不做、只记账。
10. **用户可见文案先过作者**（他的品味：不油腻、不硬凹、破坏性条目不带修饰）。
11. **别打开编辑器给他看文档，在对话里直接说**。
12. **凭证别贴对话里**；`~/.aws` / `~/.ssh` / `~/.classlive/credentials` 之类被 deny 规则挡着——不绕。
13. **主线程纪律**（见 §2）与 **`drain()` 是 streamq 唯一分发点**（加 tag 必加分支，
    3.8.4 起末尾有兜底告警，但**只是告警**）。

---

## 4. ⭐ 踩过的坑 —— 真事故清单（每条都真实发生过）

### 4.1 丢笔记 ×2（最贵的一类）
- **朋友的机器丢了一节课的笔记**：收尾卡上「关窗 = 放弃整份笔记」，他课上完点 ✕ 停止、
  卡弹出来、**又点了一次 ✕** 想关窗 → 一个字都没写。
  → 现在是 **关窗 = 存、超时 = 存；放弃只剩显式点「不存」**（3.8.2）。
- **测试写坏真术语表**：`build_notes.save_to` 无备份覆盖，测试只换了读端 →
  `term_notes.json` 41KB→4.8KB。→ 覆盖式写用户数据前**先留 `.bak`**（救过两次）。

### 4.2 「一开就崩」（3.8.0 → 3.8.2 全区块故障）
某次提交给**调用点**和 `Overlay.__init__` 都加了 `note` 形参，**唯独漏了中间包装层**
`main._load_overlay` → `TypeError` 在**绑定参数那一刻**抛出，包装层自己的 `try`
**函数体还没进去**、接不住 → 除 `cl file` 外**所有入口一开就死**。
- 闸门没拦住：它**直接** `Overlay(...)` 构造，从没走过那层包装。
- 全量代码审查也没拦住：规则集里没有「签名/调用一致性」这一类。
- ⭐ **pyright 一秒就抓到**（`reportCallIssue` + `reportUndefinedVariable`）。
  → 此后：R17（AST 比对调用点关键字 vs 包装层签名 + 走真实路径的构造检查）常驻闸门；
  **发布前至少真启动一次**。

### 4.3 「点了就假死」（看不见的模态框）
点「翻译引擎」→ `NSAlert` 弹在**普通窗口层(0)**，被自家 floating(3) 面板和其他 app 的窗口
盖住 → **整块看不见**，而主线程已进 `runModal` 的模态循环 → 全面板无响应。
- 根因（官方口径）：app 是 `.accessory` 且**从不激活**；`orderFrontRegardless` 只顶到
  **「它自己那一级」**的最前。
- 修：`notice._present()` —— **先 `activateIgnoringOtherApps(True)`、再把窗口抬到
  `NSModalPanelWindowLevel`**。`alert()` / `ask_text()` 都必须走它。

### 4.4 「🎯 从来没出现过一次」
笔记里「🎯 这节课最值得记的几句」看似新功能 —— 实为**两个静默故障叠着**：
1. `rebuild_note.py`（补生成）**没把 `keypoints_fn` 接给写入器** → 少一整节，零报错。
2. `keypoints.pick()` 的 **token 用了 CommandCode 的、endpoint/model 走官方** →
   `401` 被 `except` 吞成空表 → 整节静默消失。`ask_one` 一直是对的，只有 `pick` 漏改。
再加一层：`score()` 单批失败**连坐**（29 批挂 1 批 = 整节清零，偶发）。
→ 全部已修。**教训：一个功能"从来没出过结果"时，先怀疑管道两头，再怀疑内容。**

### 4.5 环境坑（本机特有，会再撞）
- **截图黑图 = 显示器在睡**（或锁屏）：拍图前先拍基线、比字节数；锁屏还会让
  `screencapture -R` 直接拒绝、让闸门里那条 key-window 判据**自动跳过**（同一原因）。
- **音频探针不能和被测应用抢设备**（同进程开 Output+duplex → 切采样率 → 慢放变男声+丢帧）。
- **AppKit 会吞 action 里的异常**：症状统一是「点了没反应」，跟"按钮没接上"一模一样。
  → 新按钮必须有一条**行为**判据（真 `performClick_`），不能只查接线。
- **PyObjC `runConsoleEventLoop` 收不到事件** —— GUI 测试脚手架必须用 `runEventLoop()`。
- **`AppHelper.stopEventLoop()` 在没有 run loop 时 = 直接杀进程**（rc=0、连汇总都不打）。
- **`NSMenu.autoenablesItems` 会覆盖 `setEnabled_(False)`**（置灰菜单项要实测）。
- zsh：`$VAR` 后紧跟非 ASCII 会被吞（写 `${VAR}`）；zsh 不做词分割。
- ⚠️ **shell/bash 工具的沙箱**：`/tmp` 与 `~/Desktop` 的写入在本会话里可见，
  但对"防覆盖/改名"类输出**名字会变**——对账前**先 `ls -t` 列目录**，别信脑子里的文件名。

---

## 5. ⭐ 方法论纪律（写代码、写判据、发版时遵守）

### 5.1 判据（这是本仓最重的一块文化）
- **每条判据先问「把实现改回坏版本，它会不会红」**——会红才叫判据。
- **十五种假绿形态**（都真实踩过，清单在记忆 `judges-that-look-like-they-test`）：
  恒真断言 · 假变异 · 手抄生产逻辑 · **判据钉住的正是缺陷本身** · 闸门自己绕过自己 ·
  急切求值的 detail · `assert` 被 `-O` 剥掉 · 跳过不计入统计 · **夹具缺字段→分支永不触发** ·
  **变异改到死代码** · **期望值用被测函数本身算** · **闸门走了跟生产不一样的那条路**（→一开就崩） ·
  **观测量选在更早失败点的下游** · …
- ⭐ **变异验证不红时，先怀疑观测量、再怀疑实现**：问「实现改坏后，这个量真的会变吗？」
- **结算行与失败清单必须在文件最后**（`bad` 算在中间 → 后面的判据只打印、不影响退出码）。
- **判据要指向那个位置**，不是「整个字符串里有没有」。
- **UI 判据两个盲区**：数调用次数是时序判据（会假绿）；「看得见吗」必须拍图、改前改后对比。
- 声称完成前跑**命中的那条**、贴输出——`verification-before-completion`。

### 5.2 事实纪律
- **数字必须来自测量**；benchmark 用真实负载；**验证器自身也要先自测**
  （量具坏过一次：`ast.walk` 把嵌套作用域算错 → 对着坏代码报绿，改用 `symtable` + 量具自检）。
- **引用必须自己验证**（子代理给的出处常错）；**「同 X 的理由」要连前提一起抄**。
- **一条纪律两处定义 = 必漂移**（`pick` vs `ask_one` 就是现场：一处对一处错）。
  发现第二份判据，先想能不能合流到一个定义点。
- **UI 文案/行为没实测过的不写进文档**；**别信文件名，读自证字段**（**防覆盖型输出名字会带时间戳**）。

### 5.3 改代码时
- **半吊子护栏比没有更骗人**（同一个函数里一个 `getattr` 有护栏、一个裸取）。
- **挖出行为差异的根因再改**（`systematic-debugging` 四阶段），别打补丁让症状消失。
- **`unknown ≠ missing`**、**「没答」≠「答了 0」**——这类"倒向哪边更安全"的选择，全仓一致。
- **失败不许静默**：取数失败要么出声、要么在界面上能看出来；"穷尽地吞掉异常"是最贵的静默。

---

## 6. 测试与闸门

- 27+ 个测试文件（`tests/test_*.py`），风格两种：`@case` 断言流 与 `unittest`。
- **默认闸门**：`tests/test_audit_regressions.py`（R1–R21：毫秒级断言 + 架构回归，
  含 **R17** 签名一致性、**R21** symtable 扫「当全局用、模块层没绑定」）。
- **碰什么就跑什么**（CLAUDE.md 的完成判据一节是权威表）：
  更新机制→`test_update` · 面板→`test_panel` · 就绪条/key→`test_ready`/`test_keyentry` ·
  开课面板→`test_entry_panel` · 课程→`test_courses` · 笔记→`test_vault`。
- **探针**（不在运行路径上，作者手验用）：`probe_scroll` / `probe_drag` / `probe_entry_panel` /
  `probe_test_mode` / `probe_motion`；离线全流程 `docs/experiments/run_isolated.py`（写端全隔离）。
- **任何"会写盘"的探针先把写端指到临时目录**。

---

## 7. 更新与发布机制

- **`.app` 就是安装目录**（`make-app.sh`）；`cl update` = 拉代码 + 补依赖 + 重建 `.app`。
- **依赖戳记 `~/.classlive/reqs-installed`**：三条装依赖的路都要写它
  （`cl update` / 卡片 deps 步 / `make-app.sh`）——少一条，那台机器永远看到假的「补依赖」。
  判据唯一定义点：`update.pending_steps()`。
- **更新卡片**：`.app` 里弹框问；终端里**只打印**（`notice.alert` 在终端不等人）。
  重活（补依赖/下模型）必须先问；**绝不自动下模型**（1GB 是用户的决定）。
- **auto 的边界**：只有 changelog 带 `### 更新方式 auto` 才后台自动拉；
  **坏掉的版本 auto 救不了**（跑不到退出钩子）→ 升级须知直接教 `git pull`。
- 发版流程：写 CHANGELOG（§文案规矩见下）→ **给作者确认** → bump `VERSION` →
  commit → `git tag -a`（**带 -a**，仓库惯例）→ push main + tag → `gh release create`
  → **弹卡片给他看效果**。
- **文案规矩**（CHANGELOG 头部有全文）：看点第一行讲**产品**不写工具自己；
  破坏性排最前带 ⚠️；一条一事；带实测数字；专业区不玩梗，其余可小趣味；
  解析器容错（不必 `- ` 开头）。

---

## 8. 数据落点 & 隐私

| 数据 | 在哪 | 纪律 |
|---|---|---|
| 会话逐句日志 | `sessions/<日期>_<时间>_<课号>.md` | **只读不删**；三方契约 |
| 旁路文件 | 同目录 `.lost.jsonl` / `.atoms.jsonl` / `.chapters.jsonl` | 新数据只写这里 |
| 课后笔记 | `<vault>/Lectures/` | **防覆盖**：同名已存在 → 新笔记带**生成时间戳** |
| 用户状态 | `~/.classlive/`（credentials / vault / courses / 队列…） | 不进 git；`paths.py` 是唯一定义点 |
| 测试模式上传 | VPS `ssh bldcam → ~/classlive-test/` | 默认关；上传有队列/退避/脱敏 |
| 音频 | **正常上课从不落盘**；唯一例外 = 测试模式（开课前要跟同学说明） | |

隐私红线：**音频不出机器、只发文本**；测试模式传出去的包含真实课堂内容（可能含同学声音）
——README 有原文警告，别弱化它。

---

## 9. 文档索引（权威在哪）

| 问题 | 读哪份 |
|---|---|
| 怎么改、规矩、完成判据 | `CLAUDE.md`（仓库根，**AI 的常读入口**） |
| 架构快照（会旧） | `ARCHITECTURE.md` |
| 实时总结（原子/章节/纲要） | `docs/PLAN-live-summary.md` |
| 测试模式 | `docs/PLAN-test-mode.md` |
| 开课面板 / 课程卡片 | `docs/PLAN-entry-panel.md` |
| 更新机制 | `docs/PLAN-update-mechanism.md` |
| 导入入口 + 无 Obsidian 的导出（PDF=8-D，WebKit 路线已定） | `docs/RESEARCH-entry-and-export.md` |
| macOS 视觉语言（动外观前读） | `docs/RESEARCH-macos-aesthetic.md` |
| 笔记复习层重做（**「知识点详解」像复述的病根诊断在这**） | `docs/PLAN-notes-and-ui.md` §0 |
| 路线图 | `docs/PLAN-roadmap.md` |

---

## 10. 公开的账（没做完的，别假装做完了）

- **B：全仓再扫一遍体验/严重 bug**（① pyright 剩余信号分类 ② 3.8.2 记账的 6 条 OCR
  发现：`cl setkey` 的 key 进 shell 历史 · `make-app.sh --check` 恒返回 0 ·
  `update.py` 抢锁异常被吞 · `live_summary.finish()` 超时 worker 还在花 API ·
  `writer._n` 当全局句号 · `cl update` 重建失败退出码仍 0 ③ **真跑各入口**）。
- **「知识点详解」重做**：病根 = `REVIEW_SYS` 的「绝不引入外部知识」逼模型只能复述；
  处方 = 拆「事实/理解」+ `why` 字段（带 `📖课件` 来源标签）+ 喂 `Study/<课号>/materials`。
  下一步是**探针 2**（素材已齐）。
- **PDF 出口（8-D）**：WebKit `WKWebView.createPDF` 路线已定（别选 WeasyPrint，要 Homebrew）。
- **面板缩放 / 最小化评估**：缩放=高度可做（Titled+藏红绿灯，overlay 有先例）·宽度不建议；
  「最小化」对 accessory app 不自然，建议做「收起」形态。
- 测试模式「阶段 5：真课留档」（可选）。

---

*最后一句话：这个仓库真正的资产不是代码，是**这套纪律**。改动之前先读 `CLAUDE.md` 与
本文 §3/§5；写完判据先做变异验证；发版前给作者看效果。*
