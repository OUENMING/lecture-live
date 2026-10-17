# ClassLive 手册 —— 从零上手（人和 AI 都读这份）

> 一句话：ClassLive 是作者自用的 **macOS 实时英译中课堂字幕工具** ——
> 采音频 → 转写 → 逐句修正并翻译 → 悬浮字幕 → 实时落盘 → 课后生成双层 Obsidian 笔记。
> 草稿与兜底识别纯本机；翻译只发文本；**定稿转写默认走云端（可关）**——每段音频（Opus 压缩，约 65 KB/段）经作者的服务器转发给 Groq（美国；默认不留存，滥用监控最长 30 天）或阿里云百炼国际站（新加坡；不用于训练，调用数据默认最长保留 30 天）。MIT 开源（`OUENMING/lecture-live`）。
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

**发版哲学：小步快跑、修复向。** 2026-09-30 一天发过 3.8.0→3.8.3，
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
- **定稿 ASR 在 `final_asr.py`**（3.9.0 起）：`main.final_worker` 只调 `FinalASR.transcribe(buf)`（永不抛），
  云端（Groq → 千问，经作者的 Worker）优先，失败 / 熔断 / 退化 / 超时（平时 6 s、收尾 2 s）回本地。
  `main.cloud_asr_off_reason` 是「这一段该不该上云」的唯一判据（配置 / `CLASSLIVE_CLOUD_ASR=0` / `--engine local` /
  纯转录 / 文件倍速回放）。**Whisper 是可选的本地兜底**：云端开着时不加载（省约 1 GB），兜底用 Parakeet；
  云端关着且装了 Whisper 才加载它。⚠️ 代价：整节课断网时兜底是 Parakeet 而不是 Whisper。
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
6. **更新方式默认 `auto`**（2026-10-13 作者改；原来默认 `manual`）——有破坏性变更、要用户先读须知的
   版本才写 `### 更新方式 manual`。3.9.3 及更早的客户端仍按「不写 = manual」，要它们自动装就显式写 `auto`。
7. **更新日志文案必须先给作者确认再发版**；**推送前先给作者看效果**（UI/弹窗/文案）。
8. **改代码前先加载相关 skill**（作者提过三次的硬规矩）。常用对应：
   排查 bug→`diagnosing-bugs` · 声称完成前→`verification-before-completion` ·
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

### 4.6 「更新把自己锁死」（2026-10-07；卡了两周，最后靠一张截图才抓到）

朋友那台一直卡在 3.8.7，提示「这个文件夹里有你自己改过的内容」。
- **根因**：`tools/make_icon.py` 每次构建重画一张 1024 图标写进 `assets/icon/`，而那文件
  **被 git 跟踪**。PNG 字节随 macOS 的 CoreGraphics 版本变 → **别的机器一构建就 `M`**；
  而 `.app` 重建**本身就是 `cl update` 的一步** → **自锁**：构建弄脏 → 下次更新被自己
  的产物挡下 → `.app` 永不重建。
  `.gitignore` 那句注释本来就写对了「产物一律不入库」，却给这张图开了**例外**——
  **例外就是自锁**。（Apple 有同形的成文规则：*a bundle is a read-only structure*，
  它举的反例正是 Python 往源码同目录写 `.pyc`。）
- **为什么只有部分机器中招**：那张图是 2026-09-26 在**本机**（macOS 15.7.5）提交的，
  记的是**本机编码器的字节**——相同就不脏。所以本机两周来只是被那两个未提交的 doc 挡着，
  朋友那台一构建就脏。**判据一条命令**：`git status --porcelain -uno | grep ClassLive-1024`。
- **修**：图标不入库（`87c6698`）+ 闸门 **R23**（再 `git add` 回去即红）。现在是**结构性**的：
  仓库里**没有一个被跟踪的二进制**，构建只写 `.app`（已忽略）与 `/Applications`。
- ⭐ **连带修的三件，比根因更值钱**：
  1. **停手要点名文件**：原来只说「有你自己改过的内容」，逼得靠截图 `git status` 才定位到。
     现在卡片与 `update.log` 都列具体文件名。
  2. **脏要分类**（`update.classify_dirt()`）：`generated`（既被跟踪、又被 `.gitignore` 忽略
     = 产物）· `mode`（只有权限位变了）· `eol`（只差行尾）· `content`（用户真改的）。
     ⭐ `generated` 的**名单不手写**——判据是 `git check-ignore --no-index`，就是 `.gitignore`
     自己；手写清单会腐坏。
  3. **专用修复入口**（照 Homebrew 的 `brew update-reset` 形状，但**更保守**：它摧毁全部
     未提交改动，这里只动**能证明是产物**的）：`update.py --dirt` / `--repair [--apply]`，
     **先备份**、有 `content` 档就整个不动。
- **教训**：①「产物入库」的代价不是仓库变脏，是**更新链自锁**；② 一个故障卡了两周没人查，
  因为日志里**只有结果没有原因**——**停手也必须说清是谁挡的路**。

### 4.7 「测试报告整整一周没写成」（2026-10-07 发现；最后一份报告停在 09-24）
跑了一周测试模式，`sessions/` 里**一个 `report.json` 都没有**（≈90 节），而屏上与日志
照样打「数据包生成失败，**但报告已写出**」——**那是假话**。
- **根因**：浮窗模式的**正常停止就是 ✕**（`overlay.py` 自己写着"✕ 就是退出口"），所以收尾
  跑到等 `tester.finish()` 那一步时 `ui._closed` **恒为真**；而 `_spin()` 的循环条件是
  `while not until() and not _ui_gone()` → **循环体一次都不跑、立刻返回**。`finish()` 跑在
  **daemon 线程**里 → 主线程走完 → 进程退出 → **线程在转码途中被杀**。
- **现场证据**（比日志硬）：10-06 那次 `.001.opus` 生成了、`.001.wav` 却**没被删** —— 而
  `_to_opus` 转成功是**必删** wav 的 → 正是"转码跑到一半被杀"；`.pending` 标记也活到次日。
- **修**：等待抽成可测的 `main.spin_until(until, tick, ui_gone)` —— **窗口一进来就是关的，
  就不再拿"关窗"当退出条件**（已经关了的窗口没有"冻结界面"可言，该等就得等完）。
  闸门 **R24**（两条互补：已关要等完 / 中途关要停）。
- 连带：那句「但报告已写出」改成**只报实情**（`--no-bundle` 时不许报警；`--save-notes no`
  时报告本来无处可写，也不算故障）。
- **教训**：① `while not X and not Y` 里的每个 `Y` 都要问「它在**这个**时刻可能是真吗」——
  同一个 `_closed` 早就在 `ask_save` 那里咬过一次（`overlay.py` 有注释），这里漏了同族；
  ② **daemon 线程 + 立即退出 = 数据丢失**，凡"必须完成"的收尾不许放在靠运气的线程里。

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
- **挖出行为差异的根因再改**（`diagnosing-bugs`：先造能变红的反馈环），别打补丁让症状消失。
- **`unknown ≠ missing`**、**「没答」≠「答了 0」**——这类"倒向哪边更安全"的选择，全仓一致。
- **失败不许静默**：取数失败要么出声、要么在界面上能看出来；"穷尽地吞掉异常"是最贵的静默。

---

## 6. 测试与闸门

- 27+ 个测试文件（`tests/test_*.py`），风格两种：`@case` 断言流 与 `unittest`。
- ⭐ **一条命令快筛**：`ClassLive.app/Contents/MacOS/python scripts/check.py`（约 10 s；
  `--all` 加慢测试约 25 s；`--changed` 按改动补跑并列出要人跑的量具）。全部测试文件并行 +
  HOME 隔离 + 跑前跑后核对 `sessions/`、`~/.classlive/`、`Lectures/`、`git status` 没变 +
  ruff + pyright 两条规则（已知误报在 `scripts/pyright_baseline.json`）。
  「碰了什么要跑什么」的唯一定义点是它里面的 `SLOW_FOR` / `MANUAL_FOR`。
- **默认闸门**：`tests/test_audit_regressions.py`（R1–R24：毫秒级断言 + 架构回归，
  含 **R17** 签名一致性、**R21** symtable 扫「当全局用、模块层没绑定」、
  **R22** VAD 状态漂移（假 VAD，无模型）、**R23** 构建产物不许入库、
  **R24** 收尾等待不许因"窗口已经关了"就白等）。
- **碰什么就跑什么**（权威在 `scripts/check.py`；下面只是举例）：
  更新机制→`test_update` · 面板→`test_panel` · 就绪条/key→`test_ready`/`test_keyentry` ·
  开课面板→`test_entry_panel` · 课程→`test_courses` · 笔记→`test_vault`。
- **探针**（不在运行路径上，作者手验用）：都在 `scripts/` 下 —— `probe_scroll` / `probe_drag` /
  `probe_entry_panel` / `probe_test_mode` / `probe_motion`；离线全流程
  `docs/experiments/run_isolated.py`（写端全隔离）。
- **探针都住在 `scripts/`**，仓库根只留**被 import 的模块** —— 与社区主流一致
  （实测 23 个热门 Python 仓库里 16 个有 `scripts/`、`tools/`）。
  ⚠️ 它们**自己** import 仓库模块，靠 `ROOT = Path(__file__).resolve().parent.parent` 垫片；
  改那个垫片时**连带改正文里所有 `ROOT / "…"` 的资源路径**。
- **任何"会写盘"的探针先把写端指到临时目录**。

### 6.1 环境变量开关（唯一登记处）

代码里每读一个 `CLASSLIVE_*`，这张表就要有一行（`test_audit_regressions` R30 钉着）。
**发布开关是短命的**：默认翻到开、过了一版没出事，就删掉旧路径和开关本身 —— 「删旧路径」那一列就是为这个记的。

| 开关 | 默认 | 管什么 | 类型 / 删旧路径 |
|---|---|---|---|
| `CLASSLIVE_DRAFT_TRIGGER` | `novel` | 草稿送翻判据（`legacy` / `clause` 可退回）—— `draft_policy.py` | 发布开关：3.8.11 后一版没出事就删 `legacy` / `clause` |
| `CLASSLIVE_DRAFT_CONTEXT` | `1` | 草稿翻译带领域 / 术语 / 上一句 —— `cloud_translator.translate_draft` | 发布开关：同上 |
| `CLASSLIVE_VAD_VALLEY` | `1` | 12 s 硬切时往回找能量低谷 —— `vad.Segmenter` | 发布开关：同上 |
| `CLASSLIVE_SEG_SENTENCE` | `1` | 12 s 硬切时先切在本段最后一个句号前的停顿上（草稿模型给句号时刻；只有 Nemotron 草稿时生效）—— `vad.Segmenter` / `vad.find_sentence_cut` | 发布开关（3.9.4）：`0` = 只找能量低谷 |
| `CLASSLIVE_DRAFT_ENGINE` | `auto` | 草稿引擎：`auto`（Nemotron 模型在且内存 ≥ 12 GB 就用流式）/ `parakeet` / `nemotron`；App 用户写仓库根的 `.draft-engine` 文件（`cl` 读）—— `draft_engine.py` | 发布开关：真课过闸、一版没出事后评估退役 Parakeet 草稿 |
| `CLASSLIVE_DRAFT_MODEL_DIR` | 注册表路径 | 流式草稿模型目录（测试 / 故障注入指到别处） | 测试接缝，长期 |
| `CLASSLIVE_DRAFT_HOLDBACK` | 空（关） | 草稿显示层前缀冻结，数字 = 灰尾词数 —— `draft_stable.py` | 实验开关：R3 换 Nemotron 后重新评估去留 |
| `CLASSLIVE_LAYOUT` | `d` | 悬浮窗布局：`d` = 草稿并进最底那一行 + 原地显影 + 新字淡入，无底板（2026-10-17 定版）；`classic` = 老布局（底部单独草稿栏）—— `overlay.LAYOUT_D` / `caption_flow.py` / `transcript_view` 的「D 布局」 | 发布开关：D 上线一版没出事就删 `classic` 和草稿栏代码 |
| `CLASSLIVE_NOTE_MERGE` | `1` | 课后笔记里把切成两三行的同一句话并回一行、整句重译（只改笔记，`sessions/` 不动）—— `note_merge.py` | 发布开关（3.9.5）：`0` = 照旧逐行 |
| `CLASSLIVE_LIVE_SUMMARY` | `1` | 实时总结（章节合成 / 纲要落盘 / 发 streamq） | 运维开关，长期 |
| `CLASSLIVE_LIVE_TERMS_MAX` | `40` | **课上候选词池**的上限（链路体检 ⭐2：已定稿文本里反复出现、过门的词进翻译候选池）—— `live_terms.LivePool`；撞上限按「本课出现句数 + 新近度」淘汰最少的/最旧的 | 运维开关，长期（调大 = 池子更全也更占上下文） |
| `CLASSLIVE_LIVE_LOG` | 平时关、测试模式开 | 现场采集 `<会话>.live.jsonl` —— `livemetrics.py` | 运维开关，长期 |
| `CLASSLIVE_UPLOAD` | `1` | 测试模式课后上传；测试 / 探针一律设 `0` | 运维开关，长期 |
| `CLASSLIVE_UPLOAD_ENDPOINT` / `_TOKEN` | 内置 | 上传端点与 write-only 令牌（测试指到本机假服务器） | 测试接缝，长期 |
| `CLASSLIVE_CLOUD_ASR` | 未设 | `0` = 本进程强制本地定稿：不发请求、不写 `cloud-asr.json` / `install-id`、不弹告知（探针 / 回放脚本默认设 0） | 隐私开关，长期 |
| `CLASSLIVE_STREAM_ASR` | `auto` | `off` = 关 Soniox 实时识别（`stream_asr.py`，可用时草稿与定稿都由它出、一句封口就送翻译）；`auto` 时还要**有通行码 + 云端定稿开着 + 翻译档不是 `raw`** 才真启用。不可用/额度尽 → 本节课退回现有本地流水线（走 notice 提示一次） | 发布开关：真课过闸、一版没出事后评估退役旧草稿/定稿路径 |
| `CLASSLIVE_REGION_PROBE` | 空（auto） | Soniox 多地区路由的探测开关（`region_route.py`）：auto = **每个新版本前 3 节课**开课时后台真测各地区（≤3 s，绝不阻塞），同网络 24 h 结果缓存决定本节领 key 时带不带 `region=`；`0` = 关（不测不改道）；`1` = 强制每节都测。计数/缓存在 `~/.classlive/region-probe.json` | 实验开关：前 3 节的数据评估完再定去留 |
| `CLASSLIVE_STREAM_ASR_ENDPOINT` | 内置 | Soniox 领 key 端点（判据指到本机假 Worker）；不设时跟随 `CLASSLIVE_ASR_ENDPOINT` 换路径 | 测试接缝，长期 |
| `CLASSLIVE_ASR_ENDPOINT` | 内置 | 云端定稿端点（测试指到本机假服务器；断网实测指到不可达地址） | 测试接缝，长期 |
| `CLASSLIVE_ASR_TOKEN` | 内置 | 云端定稿的公开令牌（测试用） | 测试接缝，长期 |
| `CLASSLIVE_NO_AUTO_UPDATE` | 空 | 关后台自动更新（排查用；日志在 `~/Library/Logs/ClassLive/update.log`） | 运维开关，长期 |
| `CLASSLIVE_NO_UPDATE_CHECK` | 空 | 连更新检查也不做 | 运维开关，长期 |
| `CLASSLIVE_DEBUG` | 空 | 面板 / 悬浮窗的调试日志 | 运维开关，长期 |
| `CLASSLIVE_FROM_APP` | `.app` 启动时为 `1` | 标记「从 `.app` 起的、没有终端」→ `notice` 据此弹框而不是只打印 | 内部标记，不是给人设的 |
| `CLASSLIVE_PYVER` | `3.12` | `make-app.sh` 建运行环境用的 Python 版本 | 构建参数，长期 |
| `CLASSLIVE_LOCK_DIR` | `~/Library/Logs/ClassLive` | `make-app.sh` 查单实例锁的位置（只为验收隔离） | 测试接缝，长期 |
| `CL_NO_PANEL` | 空 | `cl` 零参数时跳过课程面板、回老行为 | 运维开关，长期 |
| `CL_RESTARTS` | 空 | `cl` 自己设：面板里更新完重开（退出码 5）的次数，到 2 就不再重开 | 内部计数，别手设 |

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
- ⚠️ **脏工作区 = 停手，绝不 stash / 绝不丢弃**。判据只有一份
  （`update._dirty_report()`，`git status --porcelain -uno`）：**只算改过的跟踪文件**，
  未跟踪的不算（3.8.1 起）。⚠️ 这条闸门**一旦触发就没有任何一步会自修**（§4.6 那次
  卡了两周就是这么来的）→ 所以：**停手时必须点名文件**；排障/修复入口
  `python update.py --dirt`（分四档）· `--repair [--apply]`（只恢复能证明是产物的，先备份）。
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
| 测试模式上传 | Cloudflare Worker + R2（`ingest.bldcam.page` → 桶 `classlive-ingest`）—— **用户零配置**；见 `docs/PLAN-zero-config-upload.md` | 默认关；上传有队列/退避/脱敏 |
| 音频 | **正常上课从不落盘**；唯一例外 = 测试模式（开课前要跟同学说明） | |

隐私红线（3.9.0 起改口径）：**定稿云端转写默认开、可关**——开着时每段音频（Opus）经作者的 Worker 发给 Groq / 阿里云百炼；要保证音频不出机器只有两个办法：面板「语音模型」菜单里取消勾选「云端转写」（10-12 起不再是落点条上的开关）、或 `CLASSLIVE_CLOUD_ASR=0`。**不能**对用户说「用纯转录档就不出本机」（每节课都从翻译档开始，切换前的几段已发出，R13）。首次启动告知：`.app` 弹窗点过才记 `acknowledged`，终端只打一行提示、不记（R14）。
测试模式传出去的包含真实课堂内容（可能含同学声音）
——README 有原文警告，别弱化它。

---

## 9. 文档索引（权威在哪）

| 问题 | 读哪份 |
|---|---|
| 怎么改、规矩、完成判据 | `CLAUDE.md`（仓库根，**AI 的常读入口**） |
| docs/ 全部分区与权威状态 | `docs/README.md` |
| 架构快照（会旧） | `ARCHITECTURE.md` |
| 实时总结（原子/章节/纲要） | `docs/PLAN-live-summary.md` |
| 测试模式 | `docs/PLAN-test-mode.md` |
| 开课面板 / 课程卡片 | `docs/PLAN-entry-panel.md` |
| 更新机制 | `docs/PLAN-update-mechanism.md` |
| 导入入口 + 无 Obsidian 的导出（PDF=8-D，WebKit 路线已定） | `docs/RESEARCH-entry-and-export.md` |
| macOS 视觉语言（动外观前读） | `docs/RESEARCH-macos-aesthetic.md` |
| 笔记复习层重做（**「知识点详解」像复述的病根诊断在这**） | `docs/PLAN-notes-and-ui.md` §0 |
| 路线图 | `docs/PLAN-roadmap.md` |

⚠️ **本表只列活文档。** 交接 / 现状 / 评审 / 作者原话这些**某一天的快照**都在
`docs/archive/`（9 份，2026-09-24 → 09-30）—— 只当史料读，**里面的「下一步」一律不作数**。
想找某轮的原始账就读它，但**先回本表和 `CLAUDE.md` 核实那句话今天还成不成立**。

---

## 10. 公开的账（没做完的，别假装做完了）

- ⭐⭐⭐ **2026-10-11：定稿上云调研 + R1 翻译空窗 —— 先读 `docs/HANDOFF-20261011-cloud-asr-r1.md`**。
  R1（草稿译文触发 `novel` / 独立线程 `draft_translate.py` / 草稿上下文 / carry 只看最后一句 / 硬切找低谷）
  **代码完成、测试全绿、未提交**；3.8.10 与 3.8.11 都**还没发**（Owen：先攒）。下一件：G（❓ 一句话点拨）→ R2 上云 → R3 Nemotron。

- ⭐⭐ **2026-10-07 大修之后：只剩「上一节真课」这一件**（下面那些旧条目不受影响）。
  那天修了 5 件（**每件都有判据**）：VAD 状态漂移（丢词 92→50 · R22）· 更新自锁
  （构建产物入库 · R23）· **测试报告整整一周没写成**（R24）· 脏工作区分四档 + 专用修复入口 ·
  测试模式补「逐段探针 + 异常段原信号」。两起事故的完整教训在 **§4.6 / §4.7**。
  ✅ **2026-10-09：那「上一节真课」跑完了 —— 三样全有答案**（会话
  `sessions/2026-10-09_120723_ECON10770`，47.7 分钟，见 `RESEARCH-stream-eval.md §10.3`）：
  ① **`capped` 15.7%（4070/25928）、`clamped` 19.9%、`gain_db_max` 29.97 dB（上限就是 30）**
  —— 不是"几乎不撞上限" → **`PLAN-audio-gain` 步 3 不该取消**（下一步是步 2 的新基准，
  不是直接改上限）· ② **主模型 Whisper 空转写 0.0%、退化 1.4%**
  —— 旧那个 21% 是拿兜底模型的量，到此结清 · ③ 异常段原信号 2 份 `.npy` 已落盘。
  ⚠️ **为什么不能离线凑**（当时写的，仍然成立）：test-mode 存的 wav 是**归一化之后**的，
  而 `norm` 那格要的是**之前**的电平；且从 16-bit wav 回放**会改 ASR 结果**（实测 17 段里 9 段改口）。
  ⚠️ **10-09 夜修正 ①**：「步 3 不该取消」的推论**站不住了** —— 同一节课里停顿与人声（归一化后）
  只差 2.1 dB，停顿里是响的课堂底噪 → 瓶颈是**信噪比**，放宽增益上限改变不了它。
  **步 2/3 等下一节带 `.blocks.npz` 的真课**（逐块记了实际增益，原始电平可精确还原）再定。
  ⭐ 这一整层（量具 / 现场采集 / 冻结 / 触发策略 / 系统声源）的**交接文档**：
  `docs/HANDOFF-20261009-stream-eval.md`（现状 / 怎么跑 / 全部数字 / 该不该用 / 缺口 / 教训）。
  ⭐ **下一件事**：`docs/PLAN-streaming-asr.md` —— 草稿层换真流式模型（真课实测回改率 36.6% → 0.5%），只写了计划。
  ⭐ 顺带一条**同源证据**：这次真课也量到了草稿前缀冻结的真实收益
  （回改率 36.6% → K=1 35.9%，**只值 −2.1%**，而 5 片回放说 −13%）
  —— 正是上面那条"回放会改结果"在另一个指标上的体现。
  ⚠️ **已评估·不做**（附理由，别再反复讨论）：卡片「修复并更新」按钮（够不到需要它的人）·
  安装目录做成不可变归档（要防的已覆盖，代价是重写三处 + 发版流程）·
  删 `vad.HANGOVER`（作者**有意保留**，且 `vad_params.py` 引用它）· 自动 stash 用户改动。
  ⚠️ **小尾巴**：`--repair` 没有 UI 入口（有意）· 可执行位/行尾两档还没在真机上遇到过 ·
  `~/.opencodereview/config.json` 的 key 是**明文**（建议轮换）· R2 里有一节 smoke test
  传上去的垃圾节（write-only 令牌删不掉，等 180 天生命周期）。

- **B：全仓再扫一遍体验/严重 bug**（① pyright 剩余信号分类 —— ✅ 2026-10-11 结清：330 条里 246 条是 PyObjC 无存根噪音；抓过真 bug 的两条规则 `reportUndefinedVariable` 0 条、`reportCallIssue` 4 条（逐条看过，全是误报，进了 `scripts/pyright_baseline.json`），之后由 `scripts/check.py` 只对**新**冒出来的判红 ② 3.8.2 记账的 6 条 OCR
  发现 —— ⭐ 2026-10-01 **已随 3.8.7 结清 4 条**：`cl setkey` key 进 shell 历史
  （改交互式读入）· `update.py` 抢锁异常被吞（改为出声）· `writer._n` 当全局句号
  （写成功后加 + `mode=no` 也计数）· `finish()` 超时 worker 花钱（`_chat_json`
  可选 timeout，汇总路径 20s）；**仍开着 2 条**：`make-app.sh --check` 恒返回 0 ·
  `cl update` 重建失败退出码仍 0 ③ **真跑各入口**）。
- **卡片一键更新（重建 + 自动重开）**：现在每个发版的 `.app` 重建都要**终端一次**
  —— 卡片路径 2026-10-01 起如实把「重建 .app」引导到终端（它没法在正在运行的自己
  身上换掉 `.app`）。要做的是「守护小流程」：卡片替你优雅退出 → 重建 → 自动重开，
  朋友全程不碰终端。独立一件事。
- **「知识点详解」重做**：病根 = `REVIEW_SYS` 的「绝不引入外部知识」逼模型只能复述；
  处方 = 拆「事实/理解」+ `why` 字段（带 `📖课件` 来源标签）+ 喂 `Study/<课号>/materials`。
  下一步是**探针 2**（素材已齐）。
- **PDF 出口（8-D）**：WebKit `WKWebView.createPDF` 路线已定（别选 WeasyPrint，要 Homebrew）。
- **入口面板太大（2026-10-02 作者提）**：要么能缩放、要么整体做小。实测（本机屏高 982 / 可视 871）：
  面板高 = 卡片区（上限屏高 60%）+ 就绪条 40 + 落点条 96 + 页脚 46 + 标题 34 + 边距；
  **5 门课 ≈ 756pt，7 门课 ≈ 863pt**（已顶到可视区）。宽固定 680。
  ⚠️ **与「卡片一键更新」互相牵制**：把更新卡片放进入口面板会再加一块，小屏装不下 —— 两件事要**一起设计**
  （方向：更新入口做成就绪条里的一格 / 标题栏小按钮，不新增整块）。
- **面板缩放 / 最小化评估**：缩放=高度可做（Titled+藏红绿灯，overlay 有先例）·宽度不建议；
  「最小化」对 accessory app 不自然，建议做「收起」形态。
- **更新卡片上的「修复并更新」按钮**：**有意不做**（2026-10-07 定的）。理由：被卡住的人
  **拉不到**含这个按钮的新代码（鸡生蛋），按钮救不到他们；而能在下一版拿到按钮的人，本来
  就不容易再卡（根因已结构性堵住，见 §4.6）。后端已备（`update.py --repair`），
  将来真要接 UI 时不必重写。
- 测试模式「阶段 5：真课留档」（可选）。

### 📁 仓库布局（2026-10-01 实测定的，别再改）

**根目录只留「被 import 的模块」+ 三个可执行入口**（`cl` / `make-app.sh` / `install.sh`）。
12 个独立脚本（`probe_*` · `cache_probe` · `trash` · `rebuild_note` · `test_pipeline` · `cl-bg`）
住在 `scripts/`。

**为什么**（实测 23 个 Python 仓库，stars>1500、排除文档型仓库）：

| | 数字 |
|---|---|
| 扁平布局（根目录直接放 .py） | **19 / 23** |
| 用 `src/` 布局 | 4 / 23 |
| 扁平仓库里有 `scripts/` 或 `tools/` 目录 | **16 / 23** |
| 把工具脚本留在根目录 | 3 / 23 |

⚠️ **两份结论都要记**：扁平才是主流（所以 40 个运行模块**别动**，搬进 `src/` 要改
`make-app.sh` 的 `main.py` 特判 + 全仓 import）；但**独立脚本进 `scripts/`** 同样是主流。

⚠️ **`scripts/` 里的东西自己 import 仓库模块**，靠
`ROOT = Path(__file__).resolve().parent.parent` 垫片。改那个垫片时**连带改正文里所有
`ROOT / "…"` 的资源路径**（`glossary.txt` / `glossary/` / `.window`）——
漏一处就静默走错目录。

**命名**：`.md` 用小写单词 / kebab-case 是主流（实测 505 个文件：小写单词 280 · kebab 142 ·
snake 73 · SCREAMING 前缀 10）。本仓的 `PLAN-*.md` / `ARCHITECTURE.md` 已在主流里，**别改**。

---

*最后一句话：这个仓库真正的资产不是代码，是**这套纪律**。改动之前先读 `CLAUDE.md` 与
本文 §3/§5；写完判据先做变异验证；发版前给作者看效果。*
