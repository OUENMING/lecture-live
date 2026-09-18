# 实施计划：按需 AI 讲解 + 追问线程

> Phase 0 文档勘察已完成（2026-09-18）。本文件是给「新会话直接开工」用的执行计划。
> 每阶段自带：要实现的、文档依据（file:line）、验证清单、反模式护栏。

---

## 0. 需求（已与作者锁定，勿改）

| 项 | 决定 |
|---|---|
| 讲解触发 | **只按需** —— 不自动推送任何内容 |
| 提问上下文 | **整节课转录至今** |
| 知识边界 | **允许补充课堂之外背景** |
| 落盘 | **只写「我问过什么」**（问题 + 回答），不写讲解 |
| 输入形态 | **输入框**（非语音）；打断听课可接受 |
| **成功标准** | **当场理解教授此刻讲的内容** —— 不是"学透"，也不是"课后复习材料" |
| **输出语言** | **英文为主 · 中文辅助**（与既有笔记双层格式一致：英文陈述 + `中：`点睛） |
| **讲解长度** | **稍长可接受** —— 以"当场看懂"为准，**不设硬性短上限** |
| **翻译开关** | 现有「译 开/译 关」**保留不变**；讲解是**独立入口**，不被它替代、也不依赖它 |

### 为什么「英文为主」化解了社区那条最大风险（重要）

调研发现最强的一条反对意见是小红书原话 **"千万不要开同步翻译，那变成阅读了"**——中文字幕把「听课」变成了「读屏」。

**「英文为主 + 中文辅助」正好绕开它**：讲解主体是英文，用户仍在**听讲的语言**里，不会滑向"读一篇中文文章"。中文只作点睛。
→ 这也是"讲解可以稍长"能成立的原因：长的是**英文**，不是一篇中文长文。

### 核心设计推论（重要）

「按需讲解」和「输入框提问」**不是两个功能，是同一条对话线程**：

```
点「讲一下」  → 用自动生成的问题开一个 turn  → 写入线程
在框里追问    → 用户的文字追加一个 turn      → 写入线程
```

两者共用：同一份转录底座 + 同一条消息历史 + 同一个渲染区。
**因此只需要实现一套机制，触发入口有两个。**

---

## Phase 0 结论（已完成，供后续阶段引用）

### 0.1 代码接入点

| 事实 | 位置 |
|---|---|
| 队列与 tag 白名单声明 | `main.py:301`（`"zh"/"en"/"final"/"terms"/"notice"`） |
| **唯一 tag 分发点**（新 tag 不加这里 = 静默丢弃） | `main.py:521-550` → `drain()` |
| `final` 消费与落盘 | `main.py:547-549` |
| `EngineRouter` 公开方法（新方法的形状模板） | `main.py:183` / `:191` / `:200` |
| `EngineRouter._use_cloud` **无锁**（竞态风险） | `main.py:145` |
| worker 模板（起/停/去重） | `main.py:330-367`、`:435-487`、`:495-509` |
| 线程停止信号 | `running = threading.Event()` `main.py:288` |
| `busy`（**新 worker 不得触碰**，flush 判定依赖它） | `main.py:324`，`all_settled` `main.py:54-65` |
| 纠错后上下文历史（问答需**拷快照**） | `finals` `main.py:320` |
| 悬浮窗 panel 构造 | `overlay.py:191-198` |
| 按钮工厂 + 弱引用防 GC | `overlay.py:314-325`、`_targets` `:322` |
| 术语卡渲染（问答回答的渲染参考） | `overlay.py:503-526` |
| 渲染合并闸门（`FLUSH_DT=0.016`） | `overlay.py:549-566` |
| `pump()` 事件循环 | `overlay.py:568-608`，调用点 `main.py:561` |
| 云端请求体 / 流式 / 关思考 | `cloud_translator.py:119-142`，`thinking` 关闭在 `:125` |
| 滑动窗口上下文（**不是 bug**，见 0.4 实测） | `cloud_translator.py:163`、`:190` |
| 本地翻译全局锁（第二条本地流会排队） | `translator.py:373` |
| MLX 内存上限（并发 Metal 是崩溃风险源） | `translator.py:344-358` |

### 0.2 AppKit 文本输入（Phase 1 的依据）

**根因（实测）**：`canBecomeKeyWindow()` 对 `NSWindow`/`NSPanel` 在所有无边框 mask 下均为 `False`。
Apple 文档：窗口无标题栏时该属性为 false，且 *"Attempts to make the window the key window are abandoned if the value of this property is false."*
→ **当前悬浮窗在物理上无法成为 key window，打字不可能。** 与 `acceptsFirstMouse` 无关。

**采纳方案 A（真·非激活）**，有 PyObjC 真实先例：

- `o312o/cp-clipboard-mac` → `history_window.py`：`_CPPanel(NSPanel)` L70-72，style L261，`setLevel_(NSPopUpMenuWindowLevel)` L266，`setFloatingPanel_(True)` L267，`setBecomesKeyOnlyIfNeeded_(True)` L268，`setHidesOnDeactivate_(False)` L269，可编辑 `NSSearchField` L321，`orderFrontRegardless()` L132
- `p0deje/Maccy` → `Maccy/FloatingPanel.swift`：`[.nonactivatingPanel, ...]` L27，`orderFrontRegardless()` → `makeKey()` L83-84，`override var canBecomeKey` L218，**不激活 app**

**关键机制**：非激活面板 *"becomes key only if the hit view returns true from `needsPanelToBecomeKey`"*。
`NSTextField` 默认返回 **True**；`NSButton`/`NSView` 默认 **False**
→ **加输入框不会改变现有按钮/keyboard 行为。无回归。**

### 0.3 已知坑（全部已验证）

1. `.nonactivatingPanel` 下 `canBecomeKey` 默认 false → `makeKey()` **静默无效**，首次点击被吞。
2. `setFloatingPanel_(True)` 会把 level 强制成 `NSFloatingWindowLevel` → **level 必须最后设**。
3. 提交后必须 `makeFirstResponder_(None)` + `resignKeyWindow()`，否则输入框**吞掉所有按键**（课堂上会吃掉 PPT 快捷键）。
4. **Accessory 激活策略下 ⌘C/⌘V/⌘A 可能失效** → 需 `NSApp.setMainMenu_()` 挂 Edit 菜单。**这是最大残留风险，未实机验证。**
5. `NSControl.target` 与 `NSTextField.delegate` 在 SDK 里都是 **weak** → 必须自己持强引用（本仓库已有血案：`overlay.py:322` 的 `_targets`）。
6. 用 `control_textView_doCommandBySelector_` 处理回车（`insertNewline:` / `cancelOperation:`），**不要用 target/action**（后者在 Tab 和失焦时也会触发）。
7. 已废弃：`activateIgnoringOtherApps:`；`NSApplicationActivateIgnoringOtherApps` 标 `API_DEPRECATED(..., macos(10.6, 14.0))`，文档明说 *"will have no effect."* 用 `activate()` / `yieldActivation(to:)`。
8. 被证伪：网传「`.fullSizeContentView` + 无边框会完全阻断文本输入」——Maccy 正是该组合且搜索框可用。**不成立。**
9. `addGlobalMonitorForEventsMatchingMask_handler_` 只**观察**、不拦截，且需要辅助功能权限；`CGPreflightListenEventAccess()` 当前已为 True。Carbon `RegisterEventHotKey` 不可用（`import Carbon` 失败）。

### 0.4 DeepSeek API（官方页实读 2026-09-18）

- 模型名：`deepseek-flash`、`deepseek-v4-pro`（`deepseek-v4-flash` 是已退役的 legacy 别名，仍被接受但按 Flash 计费）
- 价格（每 1M token，**非高峰**）：命中 $0.003 / 未命中 $0.15 / 输出 $0.60；`v4-pro` 分别为 $0.022 / $0.66 / $1.98
- 高峰时段 = 峰值的 2 倍：**01:00–04:00 与 06:00–10:00 UTC，周一至周五**
- 缓存：默认自动开启，best-effort，**TTL 未文档化**（原话 *"usually within a few hours to a few days"*）
- **⚠️ 命中率 ≠ 省钱（2026-09-18 实测，`cache_probe.py`）**：用真实课堂回放 30 句对照两种 prompt 形态——
  | | A 现状(滑动窗口) | B 只追加(多轮) |
  |---|---|---|
  | 命中率 | 67.6% | 92.7% |
  | 总 prompt token | 15,908 | 120,105 |
  | 实际成本 | $0.00081 | $0.00165 (**2.0×**) |
  外推整节课 600 句：**B 约贵 70 倍**。原因：命中率是比率，成本看绝对量——每次重发整段历史，按便宜价重发 18K tok × 600 次 > 按贵价发 530 tok × 600 次。
  **→ 逐句翻译路径的滑动窗口 `context[-max_ctx:]` 不是 bug，是对的策略。不要改。**
  （但**问答线程**是真正的多轮对话且轮数少，只追加仍然正确——成本模型不同，见 Phase 2。）
- 限流：**只有并发限制，无 RPM/TPM** —— flash 2500 / v4-pro 500，账号级
- 关思考：`{"thinking": {"type": "disabled"}}`（确认为这个拼写）；`max_tokens` 1–384K，非思考模式默认 8K

### 0.5 工程量与成本

- 2 小时课实测 **7–18K token**（取自 `sessions/` 真实会话），**远低于 250K 缓存崩塌点**
- 按需模式下无常驻讲解流 → 成本 = 按问题计费，量级为**每课几毛钱**
- **无第二条常驻流 → 0.1 节的 `_use_cloud` 竞态、`translator.py:373` 本地锁排队、MLX 并发崩溃风险，全部不触发**

### 0.6 市场与社区调研（2026-09-18，四路子代理）

**市场（商业产品 / 开源）**
- 会中问答**已普及**（Otter / Fireflies / Teams Copilot / Google Meet / 腾讯会议），但**全是"会议检索"**——"刚才谁提到我""行动项是什么"，**不是"用初学者能懂的话教我"**
- **实时讲解基本无人做**；且**全部云端**。本地派（Meetily / Hyprnote / Apple Live Captions）**恰好都不做解释**——两边各缺一块
- 开源**无可直接复用**：最接近 `ppXD/Wisp`（MIT，Rust/Tauri，同用 sherpa-onnx）但偏会议场景；`Natively`（非 OSI 许可，只读参考）。**没有任何现成 agent skill**

**社区（英文 Reddit/HN + 中文小红书/知乎，均为实机浏览）**
- ⚠️ **缺口真实 ≠ 需求被验证**：四路一致，**找不到任何一条"要求边听边讲解 / 当场提问"的真实抱怨**
- ⚠️ **最大反对意见**：小红书原话 *"千万不要开同步翻译，那变成阅读了"* → **已由「英文为主」化解**（见需求节）
- ✅ **ADHD 带宽理论支持「只按需」**：知乎 329 赞 *"较高智力 ADHD 最标准的症状就是大脑占用不满导致的走神"*，须一边学习一边做低负荷的事占满带宽 → **"只按需"是占带宽，不索取带宽**
- ⚠️ **学习风险**：小红书 3937 赞反思帖——有疑问先问 AI 再做成卡片，*"最后只能学到关键词"* → 落到 Phase 4 的复习钩子
- ⚠️ 社区对 AI 课堂工具**敌意明显**（"AI slop"），且软文泛滥（多个站点宣称"提分 40%"无任何来源）

**硬约束（与技术无关，但决定能不能用）**
- **所在学校要求录音需事先获得 module coordinator 书面许可**。Otter 在美国因课堂录音吃过联邦窃听法集体诉讼；教授原话 *"You cannot record my class."*

**→ 从调研推出的三条设计落点**：①英文为主输出；②只按需；③提问后要有复习钩子。

### 0.7 Phase 2/3 勘察结论（2026-09-18，实读代码）

**⚠️ 行号漂移**：0.1 表里的行号整体偏 ~8 行。实际：`drain()` **529-558**、`busy` **332**、`finals` **328**、三个 worker **338-375 / 443-495 / 503-517**。`running` 288 与 `_use_cloud` 145 是对的。

**✅ 顶栏放得下**：现有 5 个按钮实测占 242px（含间隔），起点 x=402，**可用 386px**。加「讲一下」+「新话题」约 124px，仍余 262px。`_layout` 是右对齐自适应宽度，**不硬编码 x**，无重叠风险。
⚠️ 但 `_btn_latest` 隐藏时**仍占 63px 看不见的空位**；新按钮若要靠左，得插在它前面（循环是 `reversed(self._bar)`）。

**❌ 「独立槽位」物理上不存在（计划要改）**：面板是**严格加和**的 `顶栏 + 转录区 + 固定栈`，固定栈高度**只由 `gloss_h` 一个参数**决定——
`_pinned(gloss_h) = INPUT_H + INPUT_GAP + gloss_h + 4 + DRAFT_H + DRAFT_ZH_H + 4`
**没有未分配区域。** 三个真实选项：
- **A（采纳）**：面板**长高**——`_pinned(gloss_h, answer_h)` + 每次答案高度变化重跑 `_apply_mode` 的重算路径。⚠️ 只在**开始/结束**时改高度，别每个 token 都改窗口尺寸。
- B：绝对定位盖在转录区上 → **遮挡字幕**，不采。
- C：替换草稿两行 → ❌ 违反"不被新字幕顶掉"（`finalize` 会清空 `_draft`）。

**加答案块时五处必须一起动**（漏一处就错位）：`_pinned()` 的和 · `_layout()` 的 y 游标 · `PINNED_H`/`BASE_H`（派生，自动）· `_expanded_scroll_h()` · `_apply_mode`。**只有 `_layout` 的游标是手工重复的**，代码里有两处警告注释指这条。

**⚠️ 三条与计划不符的实现事实**
1. **本地没有 `answer_stream`** —— `translator.Translator` 的方法到 `translate_draft` 就结束了。计划说"异常降级本地"，但**没有降级目标**：`--engine local` 或无 API key 时会直接 `AttributeError`。必须补本地实现或显式守卫。
2. **`_use_cloud` 竞态是真的**（0.5 说"全部不触发"是错的）：问答 worker 与**常驻翻译 worker 并发**，两个线程可能同时进 `_fallback`（无锁）。本地降级还会排在 `translator.py:373` 的全局锁上。
3. **`collect_usage` 没接到线上路径** —— 只有 `cache_probe.py` 传 True，没有 CLI 开关。Phase 2 验证项"检查 `prompt_cache_hit_tokens`"**目前没有管道**。

**⚠️ 不能复用的现成件**：`_StreamParser`/`_retry_plain` 是 ZH/EN 双行形状；`max_tokens=220` 是**句子**预算，对讲解太小；`context[-max_ctx:]` 是缓存反模式（见 0.4）。

**⚠️ `TerminalUI`**：新增 UI 方法必须在它上面加桩，或用 `getattr` 守卫——`drain()` 没有 try/except，`AttributeError` 会**直接打死主循环**。

**⚠️ `_submit_input` 先清空再调回调** —— 回调抛错则问题丢失。Phase 2 要接网络，这条会变重要。

---

## Phase 1 — 让悬浮窗能打字（地基）  ✅ **已实现并通过独立验证（2026-09-18）**

> **状态**：代码完成，`tests/` 24 项全绿（含新增 R6），三轮独立验证。
> **未提交**，且仓库里还有**更早会话留下的未提交改动**混在同一批文件里 —— 提交前需要作者决定怎么处理。
> **仍需真人验**：真实物理鼠标/键盘、⌘C、跨 app 按键是否被吞。

**目标**：悬浮窗里出现一个能输入、能回车提交的单行输入框，且**不破坏现有按钮行为**。

**依据**：0.2 + 0.3，**并已于 2026-09-18 实机复核**（子代理跑了真探针，非读文档）。

**⚠️ 实测推翻了计划里的三处假设（实现时按这里来）**
- ❌ **面板没有子类**：`overlay.py:191-198` 是**内联创建**的 `NSPanel`。**必须先造 `NSPanel` 子类**才能 override。
- ❌ **「level 必须最后设」目前是伪命题**：`setFloatingPanel_` / `setHidesOnDeactivate_` / `setBecomesKeyOnlyIfNeeded_` **全仓零命中**；现在只有 `overlay.py:194` 一行 `setLevel_`。**除非主动加 `setFloatingPanel_(True)`，否则不用管顺序**（已实测：该调用会把 level 强制成 3，要加就必须最后设）。
- ❌ **漏了坐标与高度数学**：坐标**未翻转**（原点左下、向上长高）。新输入行要挂进底部栈，牵动 `_pinned()` `overlay.py:54-57`、`BASE_H` `:63`、高度重算 `:397`。

**实测确认的硬事实**
1. `canBecomeKeyWindow` override **生效**——无边框 mask 下基类返 `False`，override 后 `isKeyWindow=True`。（有标题栏的 mask 基类本来就返 True。）
2. **Swift 名 `canBecomeKey` 无效**：PyObjC 会把任意名字注册进 runtime（`respondsToSelector_` 返 True），但 **AppKit 从不调用它**，实测 `isKeyWindow=False`。**必须用 ObjC 名。**
3. `canBecomeMainWindow` **不用 override**（key 与 main 无关，实测 `isKeyWindow=True, isMainWindow=False`）。
4. 委托方法名 = `control_textView_doCommandBySelector_`（PyObjC 把冒号转下划线）；运行时选择器是 `insertNewline:` / `cancelOperation:`，实测经**真实 field editor** 收到。
5. `NSControl.setTarget_` 弱引用**实测确认**：10/10 次 GC 后 `target()` 变 `None`。
6. `NSApp.setMainMenu_()` + Edit 菜单**构造通过**；⚠️ **⌘C/⌘V 运行时行为仍未验证**（需真人按键）。

### ⚠️ 键盘回归风险 —— 0.2 的「无回归」结论已作废（重要）

0.2 原本论证：非激活面板只在命中控件 `needsPanelToBecomeKey=True` 时才变 key，所以"加输入框不影响按钮"。
**实测推翻：一旦 `canBecomeKeyWindow` override 成 True，这个判定就不再被查询。**

→ **点任何地方都可能让面板成为 key window**（今天是永远不会），**用户在自己 app 里按的快捷键可能被面板吃掉**。

**必须配套的退让策略（写进实现，计划原先没有）**
- 提交 / Esc 后：清空 + `makeFirstResponder_(None)` + `resignKeyWindow()`
- **顶栏按钮（`译 开`/`⭐`/`✕`/`▾`/`↓最新`）点击后也要 `resignKeyWindow()`** ← 新增
- 验证时**专门测**：「点 ⭐ 之后，在别的 app 里按方向键/空格是否正常」

**做什么**
1. 在 `overlay.py` 顶部（与 `_ButtonTargetCls` `:107` 同区）**定义一次** `NSPanel` 子类，override `canBecomeKeyWindow` 返回 `True`。⚠️ 与 `_ButtonTargetCls` 一样用**模块全局记忆**模式，重复定义会报 override 错。
2. `overlay.py:192` 改用它创建面板；**`setLevel_`（`:194`）保持在原处**（不引入 `setFloatingPanel_` 就不必动）。
3. 加 `NSTextField`（单行 + 占位符），**delegate 存入 `self._targets`**（同 `:322` 的弱引用防护）。
4. 委托类同样**模块全局记忆只定义一次**，用 `objc.super(...)`（本仓库已有 `ObjCSuperWarning` 教训）。
5. 用 `control_textView_doCommandBySelector_` 接 `insertNewline:`（提交）与 `cancelOperation:`（取消）。
6. 提交或 Esc 后：清空、`makeFirstResponder_(None)`、`resignKeyWindow()`。
7. **按钮点击后 `resignKeyWindow()`**（见上「键盘回归风险」）。
8. `NSApp.setMainMenu_()` 挂最小 Edit 菜单（`cut:`/`copy:`/`paste:`/`selectAll:`）。
9. 显示仍走 `orderFrontRegardless()`（`overlay.py:457`）；**绝不调用 `NSApp.activate`**。
10. **终端模式不能崩**：`TerminalUI`（`main.py:209`）是 duck-type 兜底，新方法要么在它上面也实现，要么调用点用 `getattr` 守卫（参照 `main.py:570-572`）。

**验证清单**
- [ ] 点输入框能出现光标，敲字有回显
- [ ] 回车触发提交回调；Esc 取消且**焦点被释放**
- [ ] **点 ⭐ 按钮后，在别的 app 里按方向键/空格正常**（键盘回归风险，见上）
- [ ] 焦点释放后，**在其他 app 里按快捷键正常**
- [ ] 现有 5 个按钮（`译 开`/`⭐`/`✕`/`▾ 展开`/`↓最新`）行为**无变化**
- [ ] ⌘C/⌘V 在输入框里可用（若不可用 → Edit 菜单没挂对）
- [ ] 面板仍浮在其他窗口之上，且**不抢前台 app**（`NSApp.isActive()` 始终 False）
- [ ] **终端模式不崩**（`--ui terminal` 能正常启动与退出）

**反模式护栏**
- ✗ 不要用 `NSApp.activate(ignoringOtherApps:)` —— 已废弃且 14+ 无效
- ✗ 不要指望 `acceptsFirstMouse` 解决打字（它只解决鼠标命中）
- ✗ 不要在 `setFloatingPanel_(True)` **之后**设 level（会被覆盖）
- ✗ 不要把 delegate/target 只存局部变量（weak 引用会被 GC）

**未验证项（探针跑不到，只能真机确认）**
- **⌘C/⌘V 运行时是否生效**：Edit 菜单**构造**已实测通过，但快捷键路由需要真人按键。若失效 → 退回方案 B（激活后恢复前台，依据同文件）。
- **键盘回归**：点按钮后是否吞掉其他 app 的按键 —— 需要真人交互验证。
- **真人打字体验**：面板可见、app 非前台时的实际输入手感。

---

## Phase 2 — 问答引擎 + 线程状态

**目标**：一个能接收「问题」并流式返回「回答」的引擎，消息线程只追加。

**依据**：`fix_stream` 的形状 `main.py:191-198`；云端请求体 `cloud_translator.py:119-142`；0.4 缓存结论。

**做什么**
1. `EngineRouter` 增加 `answer(question, transcript, history, on_delta=None)`，结构照抄 `fix_stream`（云端优先、异常降级本地）。
2. `cloud_translator.py` 增加 `answer_stream(...)`：messages 形状 = `[system, *(历史 turns), user(转录底座 + 当前问题)]`。
3. 线程开头的转录底座**冻结**为一个快照；之后新讲的课程内容**作为新的 user turn 追加**（如「自上次提问后老师又讲了：…」），而不是回头修改开头 → **前缀始终稳定**。
   理由：问答线程是**真多轮且轮数少**（一课几十轮），只追加确实最优；这与逐句翻译路径（上千次独立调用）的成本模型**不同**，别互相套用（见 0.4 实测）。
4. 线程状态存一个 `list[{"role","content"}]`，**只追加、不重排、不回填**。
5. 运行在 daemon 线程，循环判 `running.is_set()`；**不碰 `busy`**。
6. 回答流式产出走**新 tag**（如 `"answer"`），**必须在 `drain()` `main.py:521-550` 注册**，否则静默丢弃。

**验证清单**
- [ ] 单问能流式出字
- [ ] 连续追问 3 轮，第 2、3 轮命中缓存（检查响应里的 `prompt_cache_hit_tokens`）
- [ ] 云端失败时能降级本地且不崩
- [ ] 新 tag 在 `drain()` 有分支（grep 确认）
- [ ] 问答进行中，**字幕与术语解析不受影响**

**反模式护栏**
- ✗ 不要把翻译的 `context[-max_ctx:]` 模式复制过来（那是缓存反模式）
- ✗ 不要让问答 worker 触碰 `busy` / `finalq` / `carry`（`main.py:54-65` 有竞态注释）
- ✗ 不要复用 `_notify` 显示回答（它只驱动菜单栏图标，`overlay.py:658`）

---

## Phase 3 — UI 接线

**目标**：一个「讲一下」按钮 + 输入框 + 回答渲染区，三者共用一条线程。

**依据**：`overlay.py:503-526`（术语卡渲染）、`:549-566`（渲染闸门）、`:568-608`（pump）；`main.py:521-550`（分发）。

**做什么**
1. 顶栏加「讲一下」按钮（复用 `_button`，注意 `_layout()` `overlay.py:419-450` 是右对齐自适应宽度）。
2. 点「讲一下」→ 用固定 prompt 生成一个问题（"讲清刚才这段"）投进同一线程。
3. 输入框提交 → 问题进线程。
4. 回答渲染区：**复用术语卡的两态模式**（收回 1 行省略 / 展开多行），落在一个独立槽位，**不被新字幕顶掉**。
5. 渲染走 `mark_dirty(urgent=True)`（离散事件必须绕过 16ms 合并闸门）。
6. 提供「新话题」清空线程的入口。
7. **与「译 开/译 关」解耦**：讲解是独立入口、独立 system prompt，**不被翻译开关替代、也不依赖它**。译关时「讲一下」照常可用。

**输出格式（写进 system prompt 的硬要求）**
- **英文为主** —— 讲解主体是英文，面向「非母语但能读英文」的读者。**这是刻意选择**：中文字幕会把听课变成读屏（0.6 的社区反对意见），英文主体让用户留在**听讲的语言**里。
- **中文辅助** —— 关键处附一行 `中：<点睛>`，**只点睛，不整段翻译**。
- **可以不短** —— 以"当场看懂"为准，**不设硬性短上限**。长的是**英文**，不是中文长文。
- **结构** —— 先回答"教授刚说的 X 是什么 / 为什么"，再一句它在整节课里的位置；补充背景时**显式标注"背景"**，与课堂内容区分开（对应需求里的「允许补充课堂外背景」）。
- **术语沿用** —— 复用现有 `course_term_list` 注入，保证课上术语的译法一致。

**验证清单**
- [ ] 「讲一下」→ 出回答 → 追问 → 回答**在同一条线程里延续**
- [ ] 回答还在屏上时，新字幕到达**不会**清掉它
- [ ] 收起态 1 行省略号；展开态多行且可回看
- [ ] 顶栏按钮**无重叠**（历史上踩过：两个按钮交叉 22px）
- [ ] 落盘仍正常（问答不干扰 `writer.append`）
- [ ] 讲解输出**英文为主**、中文只作 `中：` 点睛（**不得整段中文翻译**）
- [ ] **「译 关」状态下「讲一下」仍可用**（解耦验证）

**反模式护栏**
- ✗ 不要让回答复用 `_terms` 槽（那个被术语解析占用，会被 `terms(hits)` 覆盖）
- ✗ 不要在 `finalize()` 里清空回答区（术语槽的历史 bug 就是这么来的）
- ✗ 不要硬编码按钮 x 坐标（用 `_layout()` 的右对齐 + `sizeToFit`）

---

## Phase 4 — 落盘（只写「我问过什么」）

**目标**：问答写进 Obsidian 笔记，作为「我卡住的地方」。

**依据**：`obsidian_writer.py`（会话日志解析 → 双层笔记）；现有复习层结构。

**做什么**
1. 问答线程**不写 `sessions/`**（那是逐句转录的 crash-safe 日志，语义不同）。
2. 结束时把线程渲染成复习层的一个新小节，如 `### 🙋 我课上问过的问题`，每条 = 问题 + 回答。
3. **复习钩子（防"只学到关键词"，依据见 0.6）**：每条问答**同时**渲染成 `问题::答案` 一行，追加进既有的 `❓ 复习自测` 区块。
   **为什么**：调研里那条 3937 赞反思帖的原话是 *"先问 AI 再做成卡片，最后只能学到关键词"*——**只是记下来不复习，等于没问**。而 `问题::答案` 这个格式**已经能被 Spaced Repetition 插件识别**，所以这是**零新增机制**地把问答接进间隔复习。
   **→ 问过的问题自动成为复习项，这是本功能对"听懂 ≠ 学会"的唯一防线。**
4. 无问答则不产生该小节（避免空标题）。

**验证清单**
- [ ] 有问答时笔记多出该小节，内容完整
- [ ] **问答同时出现在 `❓ 复习自测` 区块**（`问题::答案` 格式）
- [ ] 无问答时笔记**结构一字不变**（回归）
- [ ] `sessions/` 的原始日志格式不变（`cl last` 照常可用）

**反模式护栏**
- ✗ 不要把问答混进逐句转录区（转录是复核凭据，混入即污染）
- ✗ 不要改动 `sessions/` 的写入路径

---

## Phase 5 — 验证与收尾

1. **用真实音频端到端跑**（`--source file --path <音频>`）验证全链路，注意：`file` 模式要验落盘需带 `--course X --vault /tmp/xx --save-notes yes`，且**必须自然跑完**（kill 会丢缓冲）。
2. **实机打字测试**：Phase 1 的风险项（\(\Cmd\) 键、焦点归还）只能在真机确认。
3. **缓存命中率实测**：跑 5 轮追问，记录 `prompt_cache_hit_tokens`，与 0.4 理论对照。
4. **回归：用仓库既有的三道闸门（见 `CLAUDE.md`「完成判据」）**
   - 默认闸门：`.venv/bin/python tests/test_audit_regressions.py` 全绿（无网络、无模型、毫秒级）。
     ⚠️ R1/R4 **复刻了实现逻辑** —— **绿 ≠ 真实流水线通过**
   - 碰过流水线 / 音频路径：`.venv/bin/python test_pipeline.py <音频>`
   - 碰过 `overlay.py` / `transcript_view.py`：`.venv/bin/python probe_scroll.py`
   - **报"可用"之前先跑上面命中的那条、贴出输出，再下结论。**
5. 更新 `README.md` 路线图与 `docs/DESIGN.md`。

---

## 未决 / 留待决策

- ~~「讲一下」的成本软上限~~ → **已定：不设**。作者 2026-09-18：*"讲解字数稍微多一点也没事，目的是当时能理解教授讲的内容"*
- ~~线程跨课持久化~~ → **已定：不做**，换课清空
- **回答是否朗读？** —— 未讨论
- **是否复用 `polish.py` 的二次精修来改善回答质量？** —— 未验证。⚠️ 该文件**不在 git 里**（只存在于工作区）
- **译关时讲解要不要也给中文点睛？** —— 暂定**给**（点睛只有一行，不会变成"读屏"），Phase 1 实机时一并确认
