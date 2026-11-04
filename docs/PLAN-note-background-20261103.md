# 3.11 笔记后台化 + 收尾卡改版（2026-11-03）

> 状态：计划（未开工）
> 关联：`docs/PLAN-3.10.6-fixes.md`（同日凌晨记的 6 条修复，独立走，不并进本计划）
> 一句话：下课后笔记生成不再占着上课进程 —— 主进程 ~20 秒退出放锁，复习层 / 核对 / 上传交给 detached worker；收尾卡改成「阶段清单 + 真实进度 + 可离开」。

---

## 1 实测：今天到底卡了多久

2026-11-03 ECON10790（`sessions/2026-11-03_101422_ECON10790`）：

| 时刻 | 事件 |
|---|---|
| 10:50:30 | 停课 |
| 10:50:49 | 基础版 HTML 落盘（**+19 秒**） |
| 11:06:19 | 完整笔记落盘（**+15 分 49 秒**） |

这 15 分 49 秒里，`main.py:1832` 抢到的 `instance.lock` 一直被持有到 `run()` 返回。第二节课两条路都被挡：

1. **双击 `.app`**：LaunchServices 不起第二个进程，只给已在跑的进程发 `rapp`；`overlay.py:6388` 的 `_reopen()` 只把字幕窗叫回前台，**不开课面板**。
2. **终端 `cl`**：面板能开，但点「开始上课」后 `main.py` 撞锁，打印一句「已经有一个 ClassLive 在跑」就退出 —— 看起来就是「点了没反应」。

所以问题不是「按钮不可点」，是**整个进程还没退**。

---

## 2 目标 / 非目标

### 目标

- 停课 → 能开下一节课 **≤ 30 秒**（今天 15 分 49 秒）。
- 收尾卡显示**真实阶段、真实进度、已用时间**；不出现假百分比。
- 卡片上**只能打开完整笔记**；生成中不提供任何笔记入口。
- 生成中可以离开；完成后发系统通知。
- worker 挂了可恢复：下次启动 `resume_pending_notes` 补。

### 非目标

- 不改笔记内容 / 版式 / 复习层提示词。
- 不做云端生成（隐私与离线都要保）。
- 不引入 launchd / SMAppService（理由见 §4.5）。
- 不把笔记生成挪到「下次开课时」—— 那只是把等待换了个时间点。

---

## 3 方案对比

| 方案 | 做法 | 代价 | 结论 |
|---|---|---|---|
| **A. 收尾拆进程** | 下课后只做：冲刷最后一句 → 写 `sessions/` → 写 journal → `setsid` 起 worker → 主进程退出 | MVP 半天；完整 1 天 | **选它** |
| B. 拆锁 | `instance.lock` 拆成 recording / note 两把，第二节课只抢 recording | 旧进程还占着悬浮窗和 LaunchServices 实例，双击 `.app` 还是回旧窗口；要做进程间协调，复杂度不低于 A | 不选 |
| C. 只改卡 + 提速 | 阶段进度、ETA；缩短超时、后处理并发 | 1–2 小时见效，但第二节课照样开不了 | 作为 A 的前置先做 |

**外部同类做法**（2026-11-03 核）：

- Otter：上传完成后可以关 app，转写与摘要继续在服务器跑，完成后邮件 / 推送（[FAQ](https://help.otter.ai/hc/en-us/articles/360048322493)，2026-05-27）。
- Granola：笔记在后台生成，「可以先去用别的 app，生成好会通知你」（[docs](https://docs.granola.ai/help-center/ios/notes-not-generating)）。
- Apple：app 退出后还要跑的长任务，要么用 `SMAppService` 注册 launch agent，要么给用户**看得见的进度 + 停止入口**（[Managing ongoing background processes](https://developer.apple.com/documentation/appkit/managing-ongoing-background-processes-in-your-mac)）。

---

## 4 架构：收尾拆进程

### 4.1 时序

```
停课
 ├─ 冲刷最后一句 / 关音频
 ├─ 写 sessions/<stem>.md                （逐句记录，已有）
 ├─ 写 journal: steps.note = "pending"  （已有 testmode journal）
 ├─ Popen([sys.executable, "-m", "note_worker", <stem>],
 │        start_new_session=True,        # setsid：父进程退出不带走它
 │        stdout/stderr → ~/.classlive/note-worker.log)
 └─ 主进程退出                            （锁释放，第二节课随时能开）
        │
        └─ note_worker（detached）
             ├─ 复习层（5 块并发）
             ├─ 重点句 / 讲解 / 课前回顾
             ├─ 数字核对 / 收尾报告
             ├─ 原子写 Lectures/<stem>.md + HTML + Anki
             ├─ journal: note = "done"
             └─ 系统通知
```

### 4.2 新增文件

| 文件 | 作用 |
|---|---|
| `note_worker.py` | CLI：`python -m note_worker <stem>`；复用 `rebuild_note_for` 那条管道（`main.py:483`） |
| `~/.classlive/note-jobs/<stem>.json` | 进度快照：`{stage, done, total, started_at, pid, state}` |
| `~/.classlive/note-worker.log` | worker 的 stdout / stderr（父进程退出后终端没了） |
| `~/.classlive/note-timing.json` | 每阶段历史耗时（ETA 用，见 §6.3） |

### 4.3 失败模式

| 失败 | 表现 | 兜底 |
|---|---|---|
| worker 起不来 | 卡片 5 秒后显示「后台任务没起来」 | 主进程内联跑（退回今天的行为，不丢笔记） |
| worker 中途死 | `note-jobs` 停在 running 超过 10 分钟 | 下次启动 `resume_pending_notes` 补（`main.py:587`） |
| 网络断 | 单块失败 → 少一块，笔记照写 | 已有（`note_v2.review` 失败只丢块） |
| 用户点停止 | 发 SIGTERM；worker 在批次边界退出，写已完成部分 | 卡片上的「停止」按钮 |
| 两节课同时收尾 | 两个 worker，互不干扰 | `note-jobs` 按 stem 隔离 |

### 4.4 「只能打开完整笔记」

现在 `obsidian_writer.close()` 会在复习层之前先写一版基础笔记到 `Lectures/`，理由是崩溃时盘上至少有一份可读的（3.9.7 第 5b 项）。**本次删掉这一段**：

- 不再写任何中间笔记（`obsidian_writer.py:2079-2082` 整段删除）；只在完整版结束时原子写一次（已有的 `_atomic_write`）。
- `_write_sidecars`（HTML / Anki）也只走完整版那一次。
- 代价：worker 在写完前被杀时，盘上没有笔记 —— 但 `sessions/<stem>.md` 一直在，journal 记着 `note=pending`，下次启动 `resume_pending_notes` 能重建。
- 收益：用户**任何时候打开的都是完整笔记**，不会再看到「少一层讲解」的半成品，也不会有 `.draft` 文件在库里。卡上也不提供任何生成中的笔记入口。

### 4.5 为什么不用 launchd

Apple 的建议是「退出后还要长期跑」用 `SMAppService` 注册 launch agent。它要求 login item 注册、用户在「登录项」里可见、签名 / 打包配合 —— 当前 `.app` 是 python bundle，注册成本高，收益是「系统帮你管生命周期」，而我们的任务最长十几分钟。

detached 子进程 + 面板里的「后台任务」行 + 停止按钮，已经满足 Apple 那条要求的**实质**（可见进度 + 可停止）。等真要做「跨天队列」再升级。

---

## 5 收尾卡设计规范

### 5.1 调研结论（2025–2026）

| 结论 | 出处 | 对本案的影响 |
|---|---|---|
| 能用 determinate 就用；进度必须准确；卡住要说原因 | Apple HIG *Progress indicators*（2023-09-12 合并版） | 总进度条 + 阶段计数，不用转圈糊弄 |
| >10 秒要百分比；>1 分钟要 **step list + 活动叙述 + 中间产物 + 完成通知** | Jakob Nielsen, *Progress Indicators Ease the Wait*（2026-08-08） | 阶段清单 + 已用时间 + 完成后通知 |
| 已用时间永远显示；剩余时间只在能诚实计算时显示 | 同上 | ETA 要等 ≥2 个阶段完成才出现 |
| 进度条**永不后退**；阶段清单不能静默变长 | 同上 | 单调 `max()`；阶段固定 5 行 |
| 按用户单位计数，不按内部里程碑 | 同上 | 「复习层 3/5」而不是「chunk 3」 |
| Liquid Glass 只用于 controls / navigation 层；**非交互的状态指示不要用玻璃** | Apple HIG *Materials*（2025-09-09 更新）+ WWDC25 *Build an AppKit app with the new design*（2025-06-09） | 继续用现有 HUD 材质 + 黑 scrim，不追 Liquid Glass |
| 动效要有目的、可关闭；Reduce Motion 下用淡入淡出替代位移 / 缩放 / 模糊 | Apple HIG *Motion* / *Accessibility* | 只做字形淡换 + 4pt 上移；`motion.reduce_motion()` 为真时全关 |
| 状态不能只靠颜色，要有字形 | Apple *Differentiate Without Color Alone* 评估标准 | ✓ / ◐ / ○ + 文字，不只靠绿 / 黄 |
| 流式 / 长任务布局要稳，不抖动；断网给 inline 状态 + 恢复按钮 | Blinkk, *Designing for latency*（2026-09-29） | 进度更新防抖；断网显示「网络中断，正在重试」 |

### 5.2 视觉 token

沿用现有面板语言（`panel.py`：HUDWindow + scrim 0.38 + 圆角 16；`wrapup.py`：宽 380 / padding 18）。**不新增字体、不新增材质。**

| Token | 值 | 说明 |
|---|---|---|
| 卡片宽 | 380 pt | 现有 |
| 内边距 | 18 pt | 现有 |
| 圆角 | 16 pt | 现有 `CORNER_RADIUS` |
| 材质 | `NSVisualEffectMaterialHUDWindow` + 黑 scrim 0.38 | 现有 |
| 标题 | SF Pro 16 semibold，白 1.0 | 现有 `TITLE_FONT_SZ` |
| 行文字 | SF Pro 12.5 regular，白 0.86 | 现有 `STATUS_FONT_SZ` |
| 细节 / 时间 | SF Pro 11 regular，白 0.55，**等宽数字** | `monospacedDigitSystemFontOfSize_weight_`，计时不跳 |
| 强调色 | 琥珀 `(1.0, 0.83, 0.45)` | 现有（`overlay.py:1706`），只给「进行中」 |
| 成功 | 绿 `(0.45, 0.82, 0.55)` + ✓ | 颜色只是辅助 |
| 警告 | 橙 `(1.0, 0.72, 0.35)` + ⚠ | 失败 / 变慢 |
| 行高 | 20 pt | 新 |
| 进度条高 | 4 pt，圆角 2 | 新 |
| 行间距 | 6 pt | 新 |

### 5.3 布局

```
┌──────────────────────────────────────────┐
│ 正在生成笔记                    ECON10790 │  ← 标题 + 课号
│ 11-03 · 已用 3:12                         │  ← 细节行（等宽数字）
│ ████████████████░░░░░░░░  62%             │  ← 总进度（determinate）
│ ✓ 保存逐句记录            378 句           │
│ ✓ 整理底稿                                │
│ ◐ 复习层                  3/5             │  ← 当前阶段，琥珀
│ ○ 重点句 · 术语 · 核对                     │
│ ○ 收尾报告 · 上传                          │
│ 预计还要 2–4 分钟                          │  ← ≥2 阶段完成后才出现
│                    [停止]  [后台继续]      │  ← 右对齐，主操作最右
└──────────────────────────────────────────┘
```

- 一屏最多 **5 行阶段**（ADHD 工作集上限；也符合 Nielsen 的 step list）。
- 阶段名固定，**不动态增加**；子进度只改右侧数字。
- 生成中**没有**「打开笔记」按钮；完成后才出现。
- 完成后卡片原地变成结论：`✅ 笔记已保存（378 句，讲解+例题+自测）` + `[打开笔记] [关闭]`。

### 5.4 状态机

```
preparing ──(>1s)──> running ──> done
                        │
                        ├──> stalled   （当前阶段 > 历史 p90 × 2）
                        └──> failed    （worker 退出 / 全部块失败）
```

- `preparing` 不到 1 秒**不显示卡**（HIG：亚秒等待不要指示器）。
- `stalled` 只改文案「比平时慢，还在跑」，**进度条不动**、不回退。
- `failed` 显示原因 + `[重试]` `[查看日志]`。
- `done` 自动切结论卡；成功 3 秒后自动收，失败留住等「关闭」（沿用现有 `_wrapup_hold`）。

### 5.5 动效

- 阶段完成：字形淡换 + 4 pt 上移，`motion.STATE_S = 0.20`，ease-out-cubic（`motion.ca_timing()`）。
- 进度条：数值直接设，**不做弹簧**；更新最多每 0.3 秒一次（防抖，避免数字抖）。
- 当前阶段：**不加呼吸 / 脉冲** —— 进度条自己会动；再加动画就是噪音。
- `motion.reduce_motion()` 为真：字形直接换、不做位移，进度条直接跳值。
- 卡片变高：顶边固定、向下长（现有 `_relayout` 已保证）。

### 5.6 可访问性

- 对比度：白 0.55 在 scrim 0.38 + HUD 上要实测 ≥ 4.5:1（上线前用取色器量，不靠猜）。
- 不只靠颜色：每行都有 ✓ / ◐ / ○ 字形。
- VoiceOver：每行 `setAccessibilityLabel_("复习层，3/5，进行中")`。
- Reduce Motion：走 `motion.reduce_motion()`，与全 app 同一份判据。

### 5.7 反模式（明确不做）

- 假百分比 / 假的「AI 思考」动画（Nielsen：tie the display to real measurements）。
- 进度条回退、阶段清单静默变长。
- 把进度塞进滚动日志（transcript 是给审计的，不是给等待的）。
- Liquid Glass 叠在内容上（Apple：状态指示不用玻璃）。
- 生成中提供「打开基础版」入口（本次明确取消）。
- 倒计时式 ETA 单点数字（误差大）；只用区间。

---

## 6 进度真实性

### 6.1 现在的问题

`note_v2.py:1781` 在 `fut.result()` **之前**发 `on_progress("review", ci, len(chunks))`。也就是说卡上写「2/5」时，真实状态是「**正在等第 3 块**」，另外 4 块可能早就完成了。这是**假进度**，比没有进度更糟。

### 6.2 改法

- `one()` 完成时 `with lock: counter += 1`，然后 `on_progress("review", counter, total)`。
- 阶段开始 / 结束都发事件（不只 review）：`save` / `base` / `review` / `layers` / `check`。
- 总进度 = 已完成阶段权重 + 当前阶段的部分权重。
- **单调**：`shown = max(shown, computed)`，任何情况下不回退。

### 6.3 权重与 ETA

- 初始权重：`save 1 / base 5 / review 70 / layers 18 / check 6`。
- 每次完成后把实际耗时写 `~/.classlive/note-timing.json`（保留最近 20 次，按课号分桶，不够就回退到全局）。
- ETA 只在 ≥2 个阶段完成后显示；取 p50，向上取整到 30 秒；显示成区间（「2–4 分钟」），不显示单点。
- 某阶段超过历史 p90 × 2 → `stalled` 文案，不冻结进度条。

---

## 7 提速（和拆进程分开做，先做也能单独见效）

1. **每块总时长**：`CALL_DEADLINE_S = 300`（`llm_client.py:56`）对收尾太重。笔记链单独传 `deadline=90`，重试 3 → 2。最坏 3 分钟，不是 15 分钟。
2. **全局预算**：`review` 最多 5 分钟；超时丢未回块，笔记标「部分生成」，不拖整条收尾。
3. **后处理并发**：`_lost_items` / `_keypoint_items` / `recap` 三层互相独立，现在串行；改 `ThreadPoolExecutor(3)`。
4. **每块耗时进日志**：现在没有，慢块无法定位；先量再改。
5. **基础版 19 秒不动**：它已经是快的；慢的是复习层。

---

## 8 验收判据（每条可判对错）

| # | 判据 | 怎么验 |
|---|---|---|
| V1 | 停课 → 主进程退出 ≤ 30 秒 | `ps` 起始时间 + 日志时间戳 |
| V2 | 退出后立刻 `cl` 能开第二节课 | 真跑两节，间隔 1 分钟 |
| V3 | `Lectures/` 只有完整笔记，生成中没有任何笔记文件 | `ls` + 文件名断言 |
| V4 | 卡片阶段计数与日志逐行一致 | 抽 1 节课，把日志的 `生成复习层 x/5` 与卡片截图对账 |
| V5 | 进度条单调不回退 | 每秒采样 `note-jobs` 的 percent，断言无下降 |
| V6 | worker 被 `kill -9` 后能补 | 杀 worker，重启 app，看 `resume_pending_notes` 日志 |
| V7 | Reduce Motion 开：无位移 / 缩放，进度仍更新 | 系统设置开关后录屏对比 |
| V8 | 两节课连跑：两份笔记都完整，不互相覆盖 | 检查两份 md 的 frontmatter 与句数 |

---

## 9 实施顺序与工时

| 阶段 | 内容 | 工时 |
|---|---|---|
| **P0** | 进度真实性（`counter` + 单调）、每块 90s 超时、卡片阶段清单（先不改架构） | 2–3 小时 |
| **P1** | `note_worker.py` + journal + 主进程早退 + 删掉中间笔记 | 半天 |
| **P2** | 面板「后台任务」行 + 停止按钮 + 系统通知 | 半天 |
| **P3** | ETA / 历史耗时 / 文案与对比度打磨 | 2 小时 |

P0 单独上线也有价值：至少卡不再说谎，最坏等待从 15 分钟降到 ~3 分钟。

---

## 10 风险与回滚

- **开关**：`CLASSLIVE_NOTE_WORKER=0` 退回进程内收尾；旧路径保留一个版本，确认稳定后再删。
- **双写**：worker 与主进程都可能写同一路径 → `note.lock`（flock）+ journal 状态机，只有拿到 `pending` 的那个能写。
- **数据**：不写任何中间笔记；完整版 `_atomic_write` 原子替换；`sessions/` 一个字节不改。
- **回滚**：关开关即可，不改数据格式、不改笔记内容。

---

## 11 参考来源（全部 2026-11-03 核）

1. Apple HIG, *Progress indicators* — https://developer.apple.com/design/human-interface-guidelines/progress-indicators
2. Apple HIG, *Materials*（Liquid Glass 段，2025-09-09 更新）— https://developer.apple.com/design/human-interface-guidelines/materials
3. Apple HIG, *Motion* / *Accessibility*（Reduce Motion）— https://developer.apple.com/design/human-interface-guidelines/motion
4. Apple WWDC25, *Build an AppKit app with the new design*（2025-06-09）— https://developer.apple.com/videos/play/wwdc2025/310/
5. Apple, *Managing ongoing background processes in your Mac* — https://developer.apple.com/documentation/appkit/managing-ongoing-background-processes-in-your-mac
6. Jakob Nielsen, *Progress Indicators Ease the Wait*（2026-08-08）— https://www.uxtigers.com/post/progress-indicators
7. Blinkk, *Designing for latency: making streaming LLM interfaces feel tactile*（2026-09-29）— https://blinkk.com/insights/designing-for-latency-streaming-llm-interfaces/
8. Otter.ai, *Transcription processing time FAQ*（2026-05-27）— https://help.otter.ai/hc/en-us/articles/360048322493
9. Granola, *Notes not generating* — https://docs.granola.ai/help-center/ios/notes-not-generating
10. Apple, *Differentiate Without Color Alone* 评估标准 — https://developer.apple.com/help/app-store-connect/manage-app-accessibility/differentiate-without-color-alone-evaluation-criteria/

---

## 12 审查阻断项（2026-11-03，拆进程前必须解决）

1. **报告只能在主进程写**：`TestSession` 的 `segments/events/_extra/_ui/_corr/_tr` 都是内存累加器，worker 拿不到。主进程先退会丢 `note_file`、`blocks.counts`、`blocks.backfill`；上传条目也缺 note.md/note.html。→ 主进程写 report/bundle，worker 只写 note；或主进程落完整 report 快照，worker 单写者补丁。
2. **恢复函数会把活 worker 当残局**：`recover_pending` 把 `.pending.json` 改名，`resume_pending_notes` 看到 `note=pending` 就重跑；第二节课一开就会重复生成/上传。→ journal 加 owner/pid/state，两个恢复函数先查 `note-jobs` + pid 存活。
3. **同课号同日并发**：两个 worker 可能选中同一 note 路径，`.tmp` 同名；`_note_looks_written` 按 date+course glob 会把第一节迟到的笔记当成第二节。→ 生成前 `O_CREAT|O_EXCL` 占位；判据改用 journal/stem。
4. **上传队列跨进程**：`UploadQueue._save()` 整份覆盖；`stop_drainer` 还会 SIGTERM 持锁进程。→ worker 不直接写队列，用 outbox + 单一 drainer，或统一锁并改 stop 协议。
5. **自动更新会杀 worker**：`make-app.sh` 的 `pgrep` 只白名单 `*main.py*`，`-m note_worker` 会被杀。→ 更新器识别并等待，或延后到 worker 结束。
6. **`rebuild_note_for` 不是等价管道**：丢 QA / ❓讲解 / 补译 / 课前回顾，忽略 `--cloud-model` / `--glossary` / `--polish`。→ 新建 job spec + 与 main 同构的 writer。
7. **纯转录/本地档契约**：worker 仍用 `load_api_key` 会发云端。→ job spec 记 mode/allow_cloud/key 来源。
8. **电源**：主进程退出后 worker 没有活动断言，空闲睡眠会挂起生成。→ worker 内 `with power.keep_system_awake()`。
9. **线程退出**：`review()` 到预算返回后线程仍在跑，`_python_exit` 会 join，最坏拖 90 秒。→ 显式 cancel + 有界等待后 `os._exit`。
10. **握手/超时**：5 秒握手误判会让主进程内联跑，重新占锁；`note-jobs` 固定 10 分钟会把活 worker 当死（实测最坏 15:49）。→ pipe 心跳 + pid/启动时间判活。

**结论：先上 P0（真实进度 + 90 秒/5 分钟预算 + 收尾卡），再补跨进程所有权，最后拆 worker。不要按现计划直接拆。**

---

## 13 进展（2026-11-04）

| 项 | 状态 | 落在哪 |
|---|---|---|
| P0 真实进度 / 阶段清单 / 区间 ETA | ✅ 已提交 `ba4a88d` | `note_progress.py`、收尾卡 |
| 遥测脊柱（E） | ✅ 已提交 `ba4a88d` | `telemetry.py` |
| §12-2 活 worker 被当残局 | ✅ 所有权层已上 | `note_jobs.py` + `main.resume_pending_notes` / `upload.recover_pending` 先问 `alive()` |
| §12-3 同一节两个写者 | ✅ 占位互斥 | `note_jobs.claim()`（`O_CREAT\|O_EXCL`） |
| §12-10 固定超时把活 worker 当死 | ✅ 心跳判活 | `STALE_S=180` + pid 存活，**不看总时长** |
| 其余（1/4/5/6/7/8/9） | ⬜ 待做 | 拆 worker 之前必须逐条处理 |

判据：`tests/test_note_jobs.py`（8 组）、`test_wrapup` 新增「活 worker 不补第二份」、
`test_upload` 新增「活 worker 不算残局」；两条都做过红绿对照。

### 13.1 F P1 上半：跨进程所有权 + worker 本体（2026-11-04）

| §12 项 | 状态 | 落在哪 |
|---|---|---|
| 2 活 worker 被当残局 | ✅ | `note_jobs.alive()`；`resume_pending_notes` / `recover_pending` 先问它 |
| 3 同一节两个写者 | ✅ | `note_jobs.claim()`（`O_CREAT\|O_EXCL`） |
| 10 固定超时判死 | ✅ | `STALE_S=180` + pid，判活**不看总时长** |
| 4 上传队列跨进程 | ✅ | worker 只写 `note-outbox/`；主进程 `upload.drain_note_outbox()` 入队 |
| 5 更新脚本杀 worker | ✅ | `make-app.sh` ③ 加第二条探针：`note_jobs.active()` 非空就停下来问人 |
| 6 管道不等价 | ✅ 本机档 | spec 带 mode / cloud-model / glossary / polish / 档位；三个回调在 worker 里按 spec 重建 |
| 7 纯转录 / 本地档契约 | ✅ | spec 的 `use_cloud` / `trans_mode` / `ever_off`；三条回调先判档再发请求 |
| 8 电源 | ✅ | worker 全程 `power.keep_system_awake()` |
| 9 线程退出 | ✅ | worker 收尾走 `os._exit(rc)`，不等残留线程 |
| 1 报告只能在主进程写 | ⚠️ 部分 | 报告 / bundle / 上传仍全在主进程；worker 只写笔记 + 出箱。`note_file` 那一路由 journal 与 `note-jobs` 两条兜 |
| §4.4 删中间笔记 | ✅（后台模式） | `ObsidianWriter(base_note=False)`；进程内那条**保留**基础版（回滚时仍要它兜底） |

**实测（隔离 HOME，`/tmp/nw-e2e`）**：真派生 → `setsid` → 4 句会话 → worker 写出
`Lectures/` 等价的 md + 交互 HTML → journal 记 `note=done` + `note_file` →
`note-outbox/` 落一张票 → 收工状态 `done` → `alive()` 转 False。日志**单份**。

**还没做**（要真跑一节课才算验过）：`main.py` 那条派生分支在真课上的表现
（卡片「已交给后台」文案、锁释放时机、与实时总结的顺序）。
判据：`tests/test_note_jobs.py`、`tests/test_note_worker.py`（13 组）。

### 13.2 P2 面板「后台任务」行（2026-11-04）

落在开课面板（`entry_panel`），落点条上方常驻预留一行 `BG_H=22pt`：

- 有任务：`后台生成笔记：ECON10790　·　复习层　·　已用 3:12` + 一颗「停止」
- 没有：整行隐藏（**空间保留** —— 窗口高度 build 时定死，运行中长高要重排整棵树）
- 数据源 `note_jobs.active()`（pid + 心跳，与恢复函数同一个定义点）；刷新挂在**焦点守卫**那 0.5 s 的拍子上（每 2 拍一次），任务结束自己消失
- 停止走 `note_jobs.stop()`：只给记录里的 pid 发 SIGTERM（批次边界停、笔记照写）
- 判据：`test_entry_panel` ①b（纯函数 6 条）+ ㉔（真窗口接线 8 条）

⚠️ 顺带修的：`_chip_of` 原来按 `y > 60` 绝对坐标找胶囊，加了这一行之后
面板上出现第二颗满足条件的 `NSButton` → 所有胶囊断言一起变红。改成**相对窗口高度**。
