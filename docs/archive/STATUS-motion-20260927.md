# 阶段文档：草稿上滚的动效（含本阶段全部调研与证伪）

> 2026-09-27 · 状态：**调研已闭环 · 机制已定案 · 动效本体尚未实现**
> 入口：本文件 → `docs/PLAN-roll-motion.md`（方案与四条判据）· `docs/RESEARCH-live-caption-rollup.md`（2 行 roll-up 的台账）

---

## 1. 项目是什么 · 目标是什么

**ClassLive**（`~/lecture-live`，MIT，OUENMING/lecture-live）——
作者自用的 **macOS 实时英译中课堂字幕工具**：采音频 → 转写 → 逐句修正并翻译 → 悬浮字幕 → 实时落盘 → 课后渲染双层 Obsidian 笔记。
跑在本机，音频不出机器，只发文本。

### 1.1 定位（2026-09-27 作者原话）

> 「产品定位已经**升级成了一个留学生的一个助手**……能听教授的总结上课所说的东西、浏览课件、
> 并给学生进行复习，**包括记住学生上课没听懂的东西**，下课也能**看重点（笔记）**。」

### 1.2 ⭐ 目标只有一条筛选线（定位升级后仍然不变）

> **这件事有没有降低"每分钟的认知负荷"？**（README 的原始痛点：**跟不上语速**）

**过不了这条的一律不进来** —— 不管它看起来多智能。

### 1.3 本阶段的目标

给**草稿区上滚**加动效。它是「第一批第 1 件：草稿改 2 行 roll-up」的**后半**：
台账 §2 引的 **CFR §15.119** 逐字要求上滚 `must appear smooth to the user, and must take no more than
0.433 second to complete` —— 而当时**只实现了布局（两行 / 贴底 / 断点），没实现"那一下怎么动"**。

---

## 2. 本阶段做了哪些调研（四条线）

| # | 调研 | 产出 |
|---|---|---|
| **①** | **四轮 OCR**（阿里 `ocr`）：改动的提交 diff · 它新增的判据 · **受影响部分** `transcript_view.py` | 抓到 **2 个 high 级真 bug**（见 §3.A.9/A.10），全部已修 |
| **②** | **`github.com/feitangyuan` 三仓库**（512★/531★/15★） | **代码不能借**（许可证 NC vs 我们 MIT）；借到 3 条理念与数字 |
| **③** | **收口调研**：Apple HIG 对 Reduce Motion 的逐字要求 · `NSWorkspace` 通知的坑 | 用 Apple 逐字替掉了原来从 WCAG 的**外推**；抓到两个**静默失效**陷阱 |
| **④** | **我自己的实测**（AppKit 只能在本机验）：机制可行性 · 系统默认时长 · 帧成本基线 | **推翻了我方案里选的机制**（那个 API 在 NSView 上不存在） |

---

## 3. ⚠️ 被证伪的（**三类**，分开列）

### 3.A 我自己的错（**我先前说过的，被证据推翻**）

| # | 我说过 | 实际 | 怎么发现的 |
|---|---|---|---|
| 1 | ⭐ **本方案的机制**：用 `lbl.setFrame_display_animate_(...)` 动标签 frame | ❌ **`NSView` 上没这个 API** —— 它只在 `NSWindow.h:369` 有声明 | 我实测：`NSView.instancesRespondToSelector_` 对 `setFrame:display:animate:` 与 `setFrame:display:` **都是 False** |
| 2 | ⭐ **判据②**：上滚后上一行字符串**逐字不变**，「CFR §15.119 的可执行版」 | ❌ **两句都错**（见 §3.C.1） | 我拿 2009 版原文做**负向核查**：`already displayed`/`previously displayed`/`may not change`/`other than the base row`/`not be changed` → **全部 0 处** |
| 3 | ⭐「动画的帧成本可以离线量」 | ❌ **量不了** —— 绘制发生在 `pump()` 让步给 run loop 的那一下，**落不落在计时区内是竞态** | 我第一版探针连跑三次得 `238/257/277`，另两次得 `0` 和 `2` |
| 4 | 「47 CFR §**79.103** 定义 pop-on/roll-up/paint-on」 | ❌ §79.103 是**解码器硬件**条款；真出处是 **§15.119** | 拉 eCFR 原文读标题 |
| 5 | 「BBC **§3.8**：从句边界拆」 | ❌ 在 **§3.9** | 回源读 BBC 指南目录与正文 |
| 6 | 「把**盒子每行配额**当行高用」（`_roll_into`） | ❌ 语义错（配额 ≠ 行高） | altitude 审查抓出；**当前恰好怎么取都对**，所以是未来坑不是现行 bug |
| 7 | 「`_layout` 与 `_roll_into` 两个写者是**形状问题**，下一批再做」 | ❌ **判错了** —— 它是**活的行为 bug**，且刚好破坏本次改动的主特性 | OCR 给出了确定触发路径：拖动缩放面板每一步都把 1 行草稿撑回整盒高 |
| 8 | 「'~40 行重构'」（同上的成本估计） | ❌ **想象的成本** —— 实际修法是**删两行、加一行** | 动手修的时候量的 |

⚠️ 另有**上一阶段**已在台账里记过的三条自我更正（ZFS ARC/frecency 是假的 · 「系统强制切分不如学习者自控」
**反了**（Rey 2019：系统 0.42\*\*\* vs 自控 0.19 n.s.）· 「1 行草稿明确不合格」不准确（Netflix 逐字
`Text should usually be kept to one line`）），一并算在这一类。

### 3.B 子代理 / 外部报告说错的（**我驳回的**）

| # | 谁说的 | 实际 |
|---|---|---|
| 1 | 子代理：那份去闪烁论文是「**Apple** 的」 | ❌ **是 Google LLC**（Bruguier/Qiu/Strohman/He，`978-1-6654-7189-3/22 © 2023 IEEE`）—— 我下载 PDF 读了首页 |
| 2 | 子代理：「**1 行 vs 2 行的对照实验不存在**」 | ❌ 不成立 —— 我自己核过的 Li 2026（Applied Cognitive Psychology）与 Alves Pinto de Assis 2026（ETRA）**就是** 1 vs 2 |
| 3 | 子代理：那条 `or text` 兜底是「**死代码**，直接删」 | ⚠️ **结论错、理由对** —— `U+0085`(NEL) 与 `U+2029`(PARA SEP) **单字符就量出两行**，那条路**可达**；但它说的「那个兜底本身违反不变量」是**对的** → 真修法是治根（归一化换行类字符） |
| 4 | 子代理：ASSETS '26 说「许多人偏好比电视 32 字符/行**更多**」 | ❌ 我读正文**没找到这句**（正文只说播放器**允许**调 characters per line）→ 不用 |
| 5 | 子代理**自报不可靠**的四条（`canopyide/canopy` issue · `daintree` commit · `LiveCaptionN` · EACL 2026 demo） | ✅ 它自己标了不可用 → 我**一律进黑名单** |
| 6 | 第二版审计报告（上一阶段）| 两处**事实错误** + 三处**内部矛盾**，逐条回源码核过 |

### 3.C 引用本身站不住的（**出处有问题，不是我读错**）

| # | 那条引用 | 问题 |
|---|---|---|
| 1 | ⭐「CEA-608 规定 **base row 之外的行内容不变**」 | ❌ **规范里没有这条规则**。我逐条读了 §15.119(f)(1) 的 (i)–(vii)：**所有会改字符的机制都作用在游标上**（`replacing any previous character` · `erasing the character…`），而 `The cursor always remains on the base row`。→ 内容之所以不变，是**"协议没有寻址机制"的后果**。它在 CEA-608 成立，只因**人工速录员只向前打、不回头改**；而我们对**滑动窗口重复解码** → **那个前提根本不存在**。⭐ 而且**我自己引的 Google CHI 2023 就否掉它**：逐字 `Users are often distracted by changes in layout, modification of words, and adjustment of punctuation in live captions` |
| 2 | 47 CFR §15.119 本身 | ⚠️ **已非现行** —— 2009 版有完整 roll-up 正文，我拉 2025 版 **grep 不到任何 roll-up 字句**。接替标准 ANSI/CTA-608-E 在付费墙后 → `[未核]` |
| 3 | 「`NSAnimationContext.duration` 默认 0.20–0.35s」（`[二手整理]`） | ⚠️ 缺一手 → **我自己量了：`0.25s`**（`NSAnimationContext.currentContext().duration()`） |

---

## 4. 三方独立调研 · 收敛与冲突

### 4.1 ✅ 收敛：动效时长

| 来源 | 同类动作 | 档次 |
|---|---|---|
| 我们 `PLAN-panel-ux §11`（macOS 惯例） | **0.20–0.35s**，>0.4s 太慢 | `[二手整理]` |
| 现成动效体系的 duration scale（`motion-web`） | **`standard` = 280–350ms**（panel slide / card expand） | `[惯例]` |
| **CFR §15.119** | **≤0.433s**（**上限**） | `[一手]` |
| ⭐ 我实测的系统默认 | **0.25s** | `[实测]` |

→ **取 `0.28s`**（`ROLL_DURATION_S`）：落在三方之内，离法典上限留 0.15s。

### 4.2 ✅ 收敛：Reduce Motion 该做什么

⭐ **不再是"从 WCAG 外推"，Apple 逐字就是这么写的**（我自己回源核过）：

- `[一手]` **HIG › Accessibility**：`When this setting is active, ensure your app or game responds by
  reducing automatic and repetitive animations…` 清单里逐字列着
  **`Replacing transitions in x-, y-, and z-axes with fades to avoid motion`**
- `[一手]` **HIG › Motion**：`Consider using fades when you need to relocate an object. … If such
  movement **doesn't communicate anything useful** to people, you can fade the object out…`
  → ⚠️ **有条件的**：**我们的上滚恰恰传达信息**（那一行搬上去了）→ **正常态用位移是对的**
- `[一手]` **WWDC20 s10020**：Apple 自己的模式 =「**默认滑动 → 开了设置改交叉淡入**」

### 4.3 ⚔️ 冲突：**"carry" 是几何的还是字符的**

| 一边 | 另一边 |
|---|---|
| `onetake` 的硬规则「**Never replace a beat — carry it**」+ 可量化的 carry score（<0.5 判失败）→ 边界处**必须有东西活下来** | CFR 只约束**位置与结构**；Google CHI 2023 明说**已显示的词会被改写**，它的贡献是**减少而非消除** |

→ **判定：我们能做到的 carry 是几何的，不是字符的。** 判据 ② 已据此改成
「**整块位移恰好一个行高 + 两行同步、不得各自补间**」；字符层面**最多做概率性检查**
（编辑距离阈值之类），**绝不作硬判据** —— 否则要么恒真，要么按我们的架构**根本不可能通过**。

### 4.4 ⚠️ 冲突：许可证

| 仓库 | 星 | 许可证 | 能否用 |
|---|---|---|---|
| `onetake` | 512 | **PolyForm Noncommercial 1.0.0** | ❌ |
| `motion-web` | 531 | **CC BY-NC 4.0** | ❌ |
| `doodle-anim` | 15 | MIT | ✅（但与本案无关） |

我们是 **MIT（允许商用）** → **NC 代码一行都不能抄**（并进来会让再分发冲突）。
借用的只有**理念与事实**（理念不受版权保护、数字是事实），且**用自己的话重写**。

---

## 5. 我们引用了什么（逐条）

| 引用 | 出处 | 日期 | 档次 | 我一手核过？ |
|---|---|---|---|---|
| 上滚须平滑且 **≤0.433s**；顶行 `erased from memory and from the display` | 47 CFR §15.119（2009 版 PDF） | 2009 | `[一手]` | ✅ 我下载 PDF 逐字读 |
| 实时字幕「usually **two to three lines**」 | FCC 14-12 ¶39（PDF） | 2014-02-20 | `[一手]` | ✅ |
| **`Two-lines of scrolling text should be used.`** | BBC Subtitle Guidelines §21.5 | — | `[一手]` | ✅ |
| 两行上限 · 16 字符/行 · 9 cps | Netflix 繁中 Timed Text Style Guide | 页 2025-12-19 | `[一手]` | ✅ |
| `Replacing transitions in x-, y-, and z-axes with fades to avoid motion` | Apple HIG › Accessibility | 2026-09-27 取 | `[一手]` | ✅ JSON 端点逐字 |
| `Consider using fades when you need to relocate an object…` | Apple HIG › Motion | 同上 | `[一手]` | ✅ |
| `If you use a different notification center to register, you won't receive the notification.` | Apple 文档（**Important** 框） | 同上 | `[一手]` | ✅ |
| `users preferred stability over accuracy for previously displayed captions` + Case A/B/C 规则 | Google Research 博客 / CHI EA 2023 | 2023 | `[一手]` | ✅ |
| flicker 六项相关系数（fatigue **+0.36** · distraction **+0.33** · easy-to-read **−0.31**，N=123，p<0.001） | 同上 | 2023 | `[一手]` | ✅ |
| `finalized results, and none of them replace earlier results`（volatile 低不透明度） | Apple WWDC25 session 277 | 2025-06 | `[一手]` | ✅ 页面 transcript |
| `we only support two lines of closed captions now` | Zoom devforum（员工回帖） | 2022-12-21 | `[一手]` | ✅ |
| `kNumLinesCollapsed = 2` · `kLineHeightDip = 24` | Chromium 源码 `components/live_caption/…/format_constants.h` | main | `[一手]` | ✅ 拉原始源码 |
| 「Never replace a beat — carry it」+ carry score | `github.com/feitangyuan/onetake`（**NC**） | 2026-09-27 | `[惯例]` | ✅ 读它 SKILL.md（**只取理念，不抄文字/代码**） |
| `standard` 档 = 280–350ms · `ease-out-expo` 控制点 | `github.com/feitangyuan/motion-web`（**NC**） | 同上 | `[惯例]` | ✅ 同上 |
| 途中抓到同构先例：**约 1 秒延迟靠"假确定"实现** | NHK 技研 R&D No.182 §3.1 | — | `[一手]` | ✅ |
| 2 行上限 · 30 字/行 评分 1.9/5 | 工藤・成田 2005（映像情報メディア学会誌） | 2005 | `[一手]` | ✅ 读 PDF |
| 「每行约 25 字、句读之后立刻换行」 | 中野ほか 2008（ヒューマンインタフェース学会誌） | 2008 | `[一手]` | ✅ 读出版方摘要 |

---

## 6. 目前的计划

### 6.1 机制：**A —— 自己驱动 `f(t)`**（作者 2026-09-27 拍板）

| 路 | 评价 |
|---|---|
| **A ✅** | `pump()` 每帧按 `roll_offset(t)` 直接写 frame。**探测面就是生产代码路径本身**；不依赖任何 AppKit 动画机制 |
| B ❌ | `view.animator()` —— `PLAN-notes-and-ui.md` §5 已否：自建 run loop 里**可能卡住** → 面板停在中间尺寸**挡住字幕** |
| C ❌ | Core Animation —— 可行，但中间位置不在 `frame()` 里（在 `presentationLayer`），给「终态精确」与探针都加一层间接 |

### 6.2 四条判据（**先于动画本体**）

| # | 判据 |
|---|---|
| **①** | 时长 **≤ 0.433s**（法典上限）；目标 0.28s |
| **②** | ⭐ **几何 carry**：整块位移**恰好一个行高**，两行**同步、不各自补间** |
| **③** | **终态精确**：结束时位移**恰好**等于目标值（不许停在中间） |
| **④** | 动画期间 `pump` 不超 **8.3ms**（120Hz 一帧） |

### 6.3 进度

| 步骤 | 状态 |
|---|---|
| 调研成闭环（机制可行性 / 时长 / Reduce Motion / 通知坑） | ✅ |
| 基线探针 `probe_motion.py`（确定性，四条内容路径） | ✅ 内容路径 pump 中位 **0.018–0.020ms**，几乎 0 帧超预算 |
| 机制定案 A | ✅ |
| **纯函数钩子 `roll_ease` / `roll_offset`** | ✅ **已写**，附 `R14` 四条判据，变异 M16–M19 **全部变红** |
| Reduce Motion 淡入分支 | ⬜ 未做 |
| **动效本体**（`pump()` 里按 `f(t)` 写 frame） | ⬜ 未做 |
| 判据 ③④ 在屏量（连跑三次） | ⬜ 未做 |
| 真机看一眼 + `probe_scroll.py` 连跑三次 | ⬜ 未做 |

### 6.4 动手前必须先定的一条（方案 §5.1，最危险）

动画会是草稿标签 frame 的**第三个写者**，而 `_layout` 被 `_sync_panel_size`（**pump 每帧**）反复调用
—— **正是这次 OCR 刚抓到的那类 bug**（`REVIEW §8.3`）。
→ 已定：**动画期间置旗标，`_layout` / `_render_draft` 都不碰这两个标签，frame 只归动画。**

---

## 7. 诚实清单：还没验 / 验不了的

| # | 项 | 为什么 |
|---|---|---|
| 1 | ⚠️ **A 的推进均匀度** | 完全取决于 `pump()` 的让步节奏（手势中不睡 / 停手后 8ms），而这个负载**离线量不出来**（§3.A.3）→ **只能实现完在屏量**，判据就是 ④ |
| 2 | 「**将来的**译文会不会越出闭样本」 | **不可测**。样本就是从这些 sessions 取的，再量一遍是循环论证 → 定性为「**已加护栏、风险已知**」，不是「已证明不会发生」 |
| 3 | `ROLL_EASE_POW = 3.0` 这个手感 | 是**我认为对**的取舍（起步快、软着陆），**没有实测背书** → 留给"真机看一眼"（`t=0.5` 时已走 87.5%，可能过于前倾） |
| 4 | 面板被拖到屏幕**边缘**时的动效 | HIG 有一条 `avoid displaying motion at the edges of a person's field of view`，我们没处理 |
| 5 | ANSI/CTA-608-E **正文** | 付费墙 → `[未核]`（现行接替标准，我们引的是已废止的 §15.119） |

---

## 8. 回滚点

- 分支 **`pre-motion-20260927`** → `bfdd845`（且该点**已推送**，远端也可回滚）
- ⚠️ 分支**不覆盖**被 `.gitignore` 的数据（`glossary/` · `~/.classlive/` · `sessions/` · `.course` · `.window`）
  —— 本阶段的改动只碰代码，未触及它们（`.course` / `.window` 内容已核对未变）。
