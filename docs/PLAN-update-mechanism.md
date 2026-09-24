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
| **Phase A** 卡片加「完整更新日志」入口 | **要做** —— 卡片本身保持小，完整日志另开入口 |
| **Phase B** 卡片/主界面「一键更新」按钮 | **要做** —— 轻量版：只换代码，**不装依赖** |
| **Phase C** 启动时静默预拉、下次启动生效 | **评估后暂不做** —— 见 §4 |
| **明确不做** | Sparkle / Homebrew tap / uv tool / pipx / 完全静默自更新 |

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
且不碰进行中的会话。但仍是"下次启动才生效"。**本阶段不做。**

---

## 5. 调研结论（2026-09-24，已锁定）

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
