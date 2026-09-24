# 实施计划：更新机制（卡片入口 + 就地更新）

> 2026-09-24 起草。本文件是给「新会话直接开工」用的执行计划。
> 调研依据（含 URL 与判定）在文末「调研结论」，**结论已锁定，勿重新推翻**。

---

## 0. 现状（已落地，勿改）

| 项 | 现状 |
|---|---|
| 更新说明卡片 | `whatsnew.py` —— 非模态毛玻璃，每版本一次，可勾「不再提示」 |
| 卡片内容来源 | `CHANGELOG.md` 里该版本的 `### 更新看点` 小节（`main._changelog_summary`） |
| 触发条件 | `VERSION` ≠ `.update-seen`（`main._whatsnew_payload`），弹完写回 |
| 命令行更新 | `cl update` —— `git pull --ff-only` + 按需补依赖 + 自检 |
| 分发方式 | `git clone`，无 pip 包、无 lock 文件 |

---

## 1. 需求（已与作者锁定）

| 项 | 决定 |
|---|---|
| **Phase A** 卡片内嵌可滚动完整更新日志 | **要做** —— 卡片本身保持小，完整日志另开入口 |
| **Phase B** 卡片/主界面「一键更新」按钮 | **要做** —— 轻量版：只换代码，**不装依赖** |
| **Phase C** 完全静默自更新 | **评估后暂不做** —— 见 §4 |
| **Phase D** 小更新退出时后台自动、大更新手动点 | **要做** —— 见 §5（作者 2026-09-24 提出） |
| **明确不做** | Sparkle / Homebrew tap / uv tool / pipx / 不分级的完全静默 |

### 两条不可越界的护栏（来自项目硬规矩）

1. **不擅自改用户环境** —— 更新流程里**绝不自动装依赖、绝不下模型**（模型是 1GB 级）。
   只换代码；缺依赖就**打印命令让用户自己敲**。
2. **绝不阻塞上课** —— 这个工具在上课录课时在用。任何更新动作都不得挡启动，
   **绝不在会话进行中重启**。

---

## 2. Phase A：完整更新日志 —— ✅ **已实现（2026-09-24，随 3.6.1）**

原计划是"卡片加一个入口打开网页 changelog"。**实际做法改了**：作者要求直接把卡片
做成**可滚动的完整更新日志**，所以卡片中间现在就是 `NSScrollView` + `NSTextView`，
内容是 `CHANGELOG.md` 的全文（从第一个 `## [` 开始截 —— 文件开头的维护者约定
不上卡片）。

### 已落地的细节

- `whatsnew._add_log_view` / `_log_attr` —— 滚动区与按行样式（四档：一级标题 /
  版本标题 / 小节标题 / 正文与列表）。
- `main._changelog_full()` —— 全文，**掐掉文件头**。
- payload 从元组改成 **dict**（`version` / `date` / `summary` / `log`），
  两边都用 `**payload` 展开，加字段不再动调用点。
- ⚠️ 高度量法踩过两个坑，**别改回去**：
  - `sizeToFit()` 在容器宽度定稿前算 → **算高**（滚到底有几百像素空白）；
  - 视图还很矮时直接量 `usedRect` → 懒排版只排可视部分 → **算矮**（滚不到底）。
  - 正确：先把视图撑到 `1e6` 迫使全量排版，再量，再设成实测值。
- ⚠️ `NSTextView` **没有** `setAttributedString_`，要走 `textStorage()`。
- ⚠️ `NSAttributedString` 不可变，没有 append 方法 —— 必须用
  `NSMutableAttributedString` + `appendAttributedString_`。

### 原计划的 A1（开网页）**不做**了

卡片自己就能滚，再开个网页是重复入口。若将来 CHANGELOG 长到不适合内嵌，再考虑。

---

## 3. Phase B：一键更新按钮（轻量版）—— **待做**

### 要实现的

卡片上加「立即更新」按钮：

1. 点击 → 后台线程跑 `git fetch` + 检查是否落后；
2. 落后 → 跑 `git pull --ff-only`（**绝不 stash、绝不丢弃本地改动**；
   工作区脏就**停手并说明**，照抄 `cl update` 的既有行为）；
3. **只换代码，不装依赖** —— 若 `requirements.txt` 变了，**打印命令让用户自己敲**；
4. 按钮变成「重启后生效」；**不自动重启**（会话可能在上课）；
5. 失败 → 显示 `git` 的原始报错，别吞。

### 依据

- `cl` 里的 `update_classlive()` —— 已有完整实现（`--ff-only`、脏树停手、按需依赖）。
  Phase B 应当是**复用**它，而不是另写一套。可能的做法：把该函数抽到一个
  `update.py` 模块，`cl` 与卡片都 import 它。
- 抽模块前先读 `ARCHITECTURE.md`（本仓库的模块深度/seam 约定）。

### 验证清单

- [ ] 工作区脏时不 pull、且明确说明（**这条最重要** —— 绝不能丢用户改动）
- [ ] 落后时能拉到，且按钮文案变成「重启后生效」
- [ ] `requirements.txt` 变了时**不自动装**，只打印命令
- [ ] 断网 / 无 git / 分叉 时给出可读的失败信息，不静默
- [ ] 全程不阻塞 AppKit 主线程（后台线程 + 主线程回 UI）

### 反模式护栏

- ❌ 不要在课中重启进程。
- ❌ 不要 `git stash` / `git reset --hard` / `git checkout --`。**任何**形式的
  "先清干净再 pull"都是丢用户改动的路径，本项目对这条已有硬规矩。
- ❌ 不要 `sudo`。

---

## 4. Phase C：完全静默自更新 —— 评估结论：**暂不做**

### 为什么不该做（三条，均为硬约束冲突）

1. **静默装依赖违反「不擅自改用户环境」。**
2. **Python 已 import 的模块不受磁盘替换影响** —— `git pull` 换了 `.py`，进程里
   仍是旧代码，**必须重启才生效**。所以"静默更新完就好了"是假的，用户仍要重启。
3. **课中重启毁会话**，而这个工具正在上课录课。

### 唯一安全的形态（若将来要做）

**启动时静默 `git fetch` 预拉，下次启动时自动切到新版本** —— 用户零操作，
且不碰进行中的会话。但仍是"下次启动才生效"。

→ **2026-09-24 已按这个方向细化成分级方案，见 §5 Phase D。**
（Phase C 的结论是"不分级就别做"；Phase D 加了显式 `auto`/`manual` 标记作为前提。）

---

## 5. Phase D：小更新后台自动、大更新手动点 —— **待做（2026-09-24 设计）**

作者原话：**「小更新我发更新他们后台自动下载，大更新就他们自己点更新。」**

这是 §4「暂不做」的**受限版** —— 与当初结论不矛盾：§4 写的"唯一安全形态"就是
「启动时静默预拉、下次启动生效」，Phase D 只是再加一条**分级**。

### ⚠️ 分级判据**不能**用 semver（这是本阶段最容易踩的坑）

**反例就在本仓库**：`3.4.0 → 3.5.0` 是 **minor** 版本号，内容却是 **breaking**
（两个 ASR 模型改必装、缺了启动即报错）。semver 的 major 边界在这个项目里
**不可靠** —— 作者并不按 semver 发版。

Chrome / Firefox 也不是按 semver 分级自动更新的（它们全自动下载、只靠重启生效；
分级只用在企业策略的 **pin** 上）。所以别照搬 semver 那套。

**改用显式标记**：每个版本在 CHANGELOG 里可选写一节

```markdown
### 更新方式
auto
```

- 写 `auto` → 小更新，可后台自动应用
- **没写 / 写别的 → 一律当 manual**（**默认拒绝**：没声明自己安全的，绝不自动应用）
- 解析：`main._changelog_update_mode(version) -> "auto" | "manual"`

**加一道发版核对**（防手滑）：`requirements.txt` 变了、或更新看点里有 `⚠️` 开头的
条目 → 必须是 `manual`。可直接写成测试。

### 执行时机：**退出时**，不是启动时

**为什么不是启动时**：启动时他正要开始录课，绝不能被网络阻塞。退出时他已经不关心了，
2 秒不可见。

1. 会话结束 → 后台 `git fetch`（**纯下载进 `.git`，不碰工作区**）
2. 看 `VERSION` 之后有没有 `auto` 的版本
3. 有 → `git pull --ff-only` → 打印一行「已后台更新到 X，下次启动生效」
4. 没有（是 `manual`）→ 什么都不做，留给下次启动弹卡片 + 手动点

### 安全边界（继承 Phase B/C，一条都不能松）

1. **工作区脏就停手** —— 照抄 `cl update` 已有行为，绝不 stash / 绝不丢弃
2. `--ff-only`，分叉时不造 merge
3. **绝不自动装依赖、绝不下模型**
4. **只在退出时应用**，绝不在会话进行中
5. 失败**不弹窗**（刚下课不想被打扰），但**写一行到 `~/Library/Logs/ClassLive/`**
   —— 静默失败没有日志就没法排查

### 风险与缓解

| 风险 | 缓解 |
|---|---|
| **自动应用 = 自动把 bug 推给用户** | 默认 `manual`；`auto` 只在作者**明示**的版本上生效 |
| 误标 `auto` 导致启动即崩 | 发版核对（`requirements.txt` 变 / 有 `⚠️` → 强制 `manual`），写成测试 |
| 分叉后 `--ff-only` 失败 | 静默 + 日志；不影响下次正常启动 |
| 拉了一半（半应用状态） | 靠 `git` 本身的事务性；`git pull` 要么成功要么不落工作区。Firefox 的做法是"记录每一步、出错整体回滚"—— git 天然满足 |

### 与 Phase B 的关系

两者**互补**，不是二选一：

- **小更新** → Phase D 后台自动，用户零操作
- **大更新** → 卡片上「立即更新」按钮（Phase B），他点一下，仍不装依赖

建议顺序：**先做 Phase B**（大更新的人工路径），再做 Phase D。理由是 B 是 D 的兜底 ——
D 只会自动应用 `auto` 的，剩下全落到 B 上。

---

## 6. 调研结论（2026-09-24，已锁定）

| 方案 | 判定 | 理由 |
|---|---|---|
| 卡片/主界面按钮更新 | ✅ **可行，推荐** | 复用 `cl update`，不装依赖、不课中重启 |
| 作者远程下发 + 完全静默 | ⚠️ 技术可行，**不该做** | 三条硬约束冲突（见 §4） |
| Homebrew tap | ⚠️ 有条件 | 最正规（`brew upgrade`），但朋友须先装 Homebrew 且要维护 tap 仓库 —— 对几百行自用工具属过度工程 |
| uv tool / pipx | ⚠️ 有条件但**冲突** | 会把工具装进独立 venv，而 `sessions/` / `glossary/` / `term_notes.json` 都依赖**工作目录** |
| Sparkle | ❌ **不可行** | 官方流程要求 `.app` 放进 dmg/zip + 签 EdDSA；没有 bundle 就没有挂载点 |

### 三条关键事实（都有 ≥2 个独立来源）

1. **本项目根本不在 Gatekeeper 管辖内。** Gatekeeper 只拦**被 quarantine 的**下载件，
   而 git / curl 不给下载件打 quarantine 标记（Apple DTS 工程师 Quinn 原话，
   `developer.apple.com/forums/thread/666452`；另有 Homebrew issue 佐证）。
   所以"没签名、不是 .app"**不影响** `git pull` —— 这正是现状能跑通的原因。
2. **`git pull` 走 git 协议，不占 GitHub REST 的 60/h 未认证额度**
   （`docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api`）。
   现状已绕开限流。
3. **代码签名 / notarization / Gatekeeper 只对 `.app` bundle 与 Mach-O 生效**；
   `.py` 由已签名的解释器执行，脚本本身不查签名
   （`support.apple.com/guide/security/app-code-signing-process-sec3ad8e6e53`）。

---

## 7. 「退出时自动更新」的实现依据（2026-09-24 二次调研）

Phase D 要落地时又查了一轮"自动更新器在真实环境里怎么做"，四条结论**全部**指向
同一个设计：**独立进程 / 限频 / 并发锁 / 不延迟退出**。

| 来源 | 原话 / 做法 | 我们照做了什么 |
|---|---|---|
| Mozilla Silent Update 目标 | **"We don't want to delay shutdown"** 明确列为设计目标 | 更新跑在 `start_new_session` 的**独立子进程**里，父进程只 `Popen` 就返回 |
| Firefox Background Updates | 后台任务是**独立进程**；"主进程在跑时后台任务直接退出，不干活" | 同左；我们更简单（只跑一次，不做常驻 agent） |
| atomic（Go 工具自更新） | **"the parent process never touches the network"**；限频**每机器每小时一次** lookup；并发保护 + **陈旧锁自过期** | `RATE_LIMIT_S=3600` + `update.lock` + `LOCK_STALE_S=900` 自过期 |
| StackOverflow 最高票 | "ask to update when the user is **closing** your application, NOT when it has just launched it" | 挂在 `run()` 的 `finally` **最末** |
| Automattic/auto-update | 更新器应跑在独立线程，**甚至独立进程**（隔离与健壮） | 独立进程 |
| Claude Code 自己的 bug #14791 / #71524 | 运行中更新留下 **0 字节版本文件**；失败**无退避无限重试** | 我们**不在运行中**动工作区；限频天然免疫紧密重试 |

### 因此实现里**刻意**长这样（改之前先读这段）

- ⚠️ **绝不能把网络放回主进程的退出路径**。断网时同步跑要干等超时（45s）才关得掉，
  而这是**上课录课**用的工具。
- ⚠️ **判断必须在 `pull()` 之前**，且读**远端 ref** 的 `CHANGELOG.md`
  （磁盘上还没有那些版本）。拉完再判断就已经应用了，来不及拒绝。
- ⚠️ **`origin/main` 不能硬编码**（初版写死了）：远程名/分支名与作者不同就全废。
  用 `update.upstream_ref()` 解析 `@{u}`。
- **"环境不全"一律静默降级**（只写日志）：没装 git / 没配 upstream / 浅克隆 /
  没有 `VERSION` / 切了分支 / 日志目录不可写 / 不是 git 仓库 —— 都只是"这次没更新成"。

### 已知取舍

- 自动更新**只覆盖 git 分发**。若将来换成 Homebrew/uv tool，这一整套要重做。
- **`auto` 标错 = 自动把 bug 推给用户**。缓解是"默认拒绝 + 发版时自查
  （`requirements.txt` 变了 / 有 ⚠️ -> 必须 manual）"，但**没有自动化强制**，
  靠作者守规矩。要做强制的话：在 `tests/` 里加一条"扫描最新版本"的检查。

| 方案 | 判定 | 理由 |
|---|---|---|
| 卡片/主界面按钮更新 | ✅ **可行，推荐** | 复用 `cl update`，不装依赖、不课中重启 |
| 作者远程下发 + 完全静默 | ⚠️ 技术可行，**不该做** | 三条硬约束冲突（见 §4） |
| Homebrew tap | ⚠️ 有条件 | 最正规（`brew upgrade`），但朋友须先装 Homebrew 且要维护 tap 仓库 —— 对几百行自用工具属过度工程 |
| uv tool / pipx | ⚠️ 有条件但**冲突** | 会把工具装进独立 venv，而 `sessions/` / `glossary/` / `term_notes.json` 都依赖**工作目录** |
| Sparkle | ❌ **不可行** | 官方流程要求 `.app` 放进 dmg/zip + 签 EdDSA；没有 bundle 就没有挂载点 |

### 三条关键事实（都有 ≥2 个独立来源）

1. **本项目根本不在 Gatekeeper 管辖内。** Gatekeeper 只拦**被 quarantine 的**下载件，
   而 git / curl 不给下载件打 quarantine 标记（Apple DTS 工程师 Quinn 原话，
   `developer.apple.com/forums/thread/666452`；另有 Homebrew issue 佐证）。
   所以"没签名、不是 .app"**不影响** `git pull` —— 这正是现状能跑通的原因。
2. **`git pull` 走 git 协议，不占 GitHub REST 的 60/h 未认证额度**
   （`docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api`）。
   现状已绕开限流。
3. **代码签名 / notarization / Gatekeeper 只对 `.app` bundle 与 Mach-O 生效**；
   `.py` 由已签名的解释器执行，脚本本身不查签名
   （`support.apple.com/guide/security/app-code-signing-process-sec3ad8e6e53`）。
