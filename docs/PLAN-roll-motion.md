# 方案：草稿上滚的动效（roll motion）

> 2026-09-27 · 作者已定两处（见 §0）· 动手前回滚点 = 分支 **`pre-motion-20260927`**（→ `bfdd845`，且该点**已推送**，远端也可回滚）
>
> **分工**：这一件是 `docs/RESEARCH-live-caption-rollup.md` §8 规格 #4（「上滚 ≤0.433s 且须平滑」）
> **缺的那一半** —— 那份台账管「两行怎么折、断点挑在哪」，这份管「那一下怎么动、怎么验」。
> 本文件也**是**这一轮的调研落点（§1）。

---

## 0. 作者本轮拍板（别再问一遍）

| # | 问题 | 决定 |
|---|---|---|
| 1 | 动效做在哪几处 | **只做草稿上滚那一处**，其余**一律零动效**（沿用 `PLAN-panel-ux §11`） |
| 2 | 开了「减弱动态效果」的用户 | **淡入代替位移**（不做位移动画） |
| 3 | **机制走哪条路** | ✅ **A：自己驱动 `f(t)`**（`pump()` 每帧按 `roll_offset(t)` 直接写 frame）。B `animator()` 与 C Core Animation **不再考虑**（理由见 §4.2） |

**第 2 条不是外推的 —— Apple 逐字就是这么写的**（2026-09-27 我自己回源核过）：

- `[一手]` **HIG › Accessibility**（`developer.apple.com/design/human-interface-guidelines/accessibility`）：
  `When this setting is active, ensure your app or game responds by reducing automatic and repetitive
  animations, including zooming, scaling, and peripheral motion.` 其清单里逐字列着
  **`Replacing transitions in x-, y-, and z-axes with fades to avoid motion`** ✅
- `[一手]` **HIG › Motion** 同页：`Consider using fades when you need to relocate an object. … If such
  movement **doesn't communicate anything useful** to people, you can fade the object out before moving it…`
  → ⚠️ **这是有条件的**：只有"移动不传达信息"才建议淡入。**我们的上滚恰恰传达信息**（那一行搬上去了）
  → 所以**正常态用位移是对的**，HIG 支持这个取舍；淡入只留给 Reduce Motion。
- ⭐ `[一手]` **WWDC20 session 10020** 里 Apple 自己的模式就是「**默认滑动 → 开了设置改交叉淡入**」
  （`When the Prefer Cross-Fade Transition setting is on, we replace the sliding transitions for
  something a little more subtle`）—— 与上面两条一致。

⚠️ **代价仍要说清楚**：对所有用户都成立的「carry」（旧行可见地搬上去）在 Reduce Motion 下**必然丢掉**
（§1.3①：淡入在感知上就是"替换"）。这是**拿观感换无障碍**，而且是**有意的**。

### 0.1 ⚠️ 两个实现陷阱（都已一手核过，别再踩）

1. ⭐ **通知必须挂在 `NSWorkspace` 的 notificationCenter 上**。Apple 文档在一条 **Important** 里逐字写着：
   `To receive this notification, use NSWorkspace.notificationCenter to register for it.
   **If you use a different notification center to register, you won't receive the notification.**`
   → 用 `defaultCenter()` 会**静默收不到**（与本仓库那族「静默不生效」同类）。
2. **这个设置会在运行中变**（Apple 文档明说可注册变更通知）→ 不能只在启动时读一次。
   ⚠️ 而我们的面板是 `NonactivatingPanel`、**永不成为 key** → "等 app 激活时重读"那条路不存在
   → **通知是必须的**，不是可选。

---

## 1. 调研：`github.com/feitangyuan` 有什么能用（2026-09-27）

对方是**做动效**的（6 个公开仓库，头两个 500+★），但**两个都不是我们能用的形态**。

### 1.1 ⚠️ 许可证先把「抄代码」否掉

| 仓库 | 星 | 许可证 | 判定 |
|---|---|---|---|
| `onetake` | 512 | **PolyForm Noncommercial 1.0.0** | ❌ **NC** |
| `motion-web` | 531 | **CC BY-NC 4.0** | ❌ **NC** |
| `doodle-anim` | 15 | MIT | ✅ 可用（但是**手绘角色**动画，与我们完全无关） |

**本项目是 MIT（允许商用）** → NC 的代码并进来会让再分发与 MIT 冲突。
→ **一行代码都不抄**。借用的是**理念与事实**（理念不受版权保护、数字是事实），并且**用自己的话重写**。

### 1.2 它们是什么（决定"能借多少"）

两个都是 **Claude Agent Skill**，不是库：
- `onetake` = 用 HTML 逐帧渲染**发布片/动效短片**（相机、运动模糊、音效、节奏、carry 判据）
- `motion-web` = **动效优先的网页**（Three.js / Canvas 2D / WebGL / CSS + Playwright 无头验证）

→ 我们只要**两行文字上滚约 16px**。它们 ~95% 的内容（相机、运动模糊、物理弹簧、音效、WebGL、Playwright）**与我们无关**。

### 1.3 ✅ 真能借的三条

#### ① 「Never replace a beat — carry it」+ **carry score**（`onetake` 的硬规则）

它的规则：**边界处必须有东西活下来、并可见地移动进下一拍**；用 `display:none` 互换的场景
**节奏再好也读成 PPT**。而且它把这件事**量化**了（carry score < 0.5 判失败；被否的两版 0.00 / 0.40，
通过版 0.75）。

→ ⭐ **这正是我们上滚的不变量，换了一套独立的话说**：上滚那一行的文字，必须**逐字不变地**出现在上一行。
→ ⚠️ 它同时**反对"淡入"**：交叉淡入 == 替换 == 切换。所以**正常态用位移是对的**，
   而 §0 第 2 条那个淡入是**为无障碍做的退让**（代价已在上面写明）。

#### ② ⭐⭐ 「**先建探针面**，事后补比重建还贵」（`motion-web` 建造顺序）

它的原话是：先建可钉住的时钟 / `__seek` / `__hold` 这些**确定性钩子**，再测量；
**事后补比一开始就建贵**。

→ **这正好是我们的缺口**：我们的测试是**静态摊平**（`tests/test_panel.py` 的 docstring 自己写着
「行为（拖拽、live resize）不在里面」），动效**没有测试面**；而现有的 `probe_scroll.py` 是
**交互式且不稳定**（`CLAUDE.md` 记着：改动前的代码连跑三次，失败的阶段会**翻转**）。
→ 采纳：**把位移写成 `f(t)`（时钟可注入）**，见 §3。

#### ③ 时长与曲线可**交叉验证**（取数不取文）

它的 duration scale 里，**`standard` = 280–350ms**，用途写明是「card expand / panel slide / drawer」——
与**面板/行滑动**同类。

⭐ **三个独立来源在这里收敛**：

| 来源 | 同类动作的时长 |
|---|---|
| 我们 `PLAN-panel-ux §11`（macOS 惯例，`[二手整理]`） | **0.20–0.35s**，>0.4s 几乎肯定太慢 |
| `motion-web` 的 `standard` 档（web 惯例，`[惯例]`） | **280–350ms** |
| **CFR §15.119**（字幕规范的**上限**，`[一手]`，见台账 §2） | **≤0.433s** |

→ 取 **`0.28s`**：同时落在三方之内，且离法典上限留 0.15s 余量。

曲线：它给的 `ease-out-expo` = `cubic-bezier(0.16, 1, 0.3, 1)`（「快进 → 软着陆」）。
⚠️ **这不是"web 的数"，AppKit 能直接用**：`CAMediaTimingFunction.functionWithControlPoints_(0.16, 1, 0.3, 1)`。
选它的理由与我们自己的证据一致：文字要**尽快就位、然后静止**（§11「rests 才让动作落地」）。
备用：`ease-out-circ` = `(0, 0.55, 0.45, 1)`「很快的减速、干爽机械感」。

### 1.4 ❌ 明确不借

| 不借 | 为什么 |
|---|---|
| **弹簧 / 物理阻尼**（它的 `spring-*` 预设） | 文字**过冲 = 文字错位**；列表滚动是纯缓动，不是弹跳 |
| 相机 / 运动模糊 / 音效 / 场景 / WebGL | 影片与网页的事 |
| 它们的 `probe.py` / `verify_promo.py` / `verify_case.py` **源码** | **NC** —— 方法可以学，代码不能抄 |
| `1-exp(-k·dt)` 那套「跟随」滤波（`motion-web` 给相机/光标/轨道用） | 我们的上滚**不是"跟随某个连续量"**，是**一次性位移** |

---

## 2. 动效的形状（要动的是什么）

草稿区是**固定两行**的盒子（`overlay.DRAFT_H=32` / `DRAFT_ZH_H=35`，两槽各 2 行、**贴底**）。
上滚发生在：**原来占底行的那段文字，被新内容顶到上一行**。

所以这一次动效 = **两行文字整体向上位移一个行高，同时顶端一行滚出、底行换上新内容**。
⚠️ 关键：**位移的是"整块"，不是各自补间** —— 否则两行会各动各的，读起来是"两条字在打架"。
（这正是 §1.3① 的 carry：上滚的那一行**逐字不变**，只是位置变了。）

---

## 3. ⭐ 判据先于实现（本轮最重要的一条）

采纳 §1.3② 的教训：**先把探针面建出来，再写动画。**

### 3.1 要建的钩子（确定性，不靠人手）

把位移写成时间的**纯函数**，时钟可注入：

```
# 形状示意（不在此定签名, 实现时定）
roll_offset(t)     -> 当前位移(px)        # t 归一化到 [0,1]；纯函数, 无副作用
```

有了它，探针可以**确定性**地断言（而不是像 `probe_scroll.py` 那样靠人滚、还会翻转）。

### 3.2 四条判据

| # | 判据 | 判据形状 |
|---|---|---|
| **①** | **不超预算** | 动画总时长 ≤ **0.433s**（法典上限）；目标 **0.28s**。超了算失败 |
| **②** | ⭐ **carry 是「几何」的，不是「字符」的** | **整块位移恰好一个行高**，且**两行同步（不得各自补间）**。⚠️ 原来写的是「上一行字符串 == 上滚前底行」—— **那是错的，已改**，理由见 §3.4 |
| **③** | **终态精确** | 动画结束时位移**恰好**等于目标值（**不能停在中间**）。⚠️ 这条是 `PLAN-notes-and-ui.md` §5 记的**可用性事故**：`animator()` 卡住会让面板停在中间尺寸**挡住字幕** |
| **④** | **不在帧预算外** | 动画期间的 pump 帧时间不超 **8.3ms**（120Hz 一帧）——沿用 `probe_scroll.py` 已有的那套统计与闸门 |

### 3.3 判据要在**建动画的同一步**写

不要「先做动画、以后再补探针」—— §1.3② 明说**事后补更贵**，而且我们这个仓库的测试是**静态摊平**，
不主动建就没有任何东西会拦住动效回归。

---

### 3.4 ⚠️ 判据 ② 我写错了 —— 2026-09-27 收口时改掉

**原来写的**：「上滚后，上一行字符串 == 上滚前底行的字符串（逐字不变）」，并声称它是
CFR §15.119「base row 之外的行是静态的」的可执行版。**两句都站不住**：

1. **CFR 里没有那条规则** —— 我自己拿 2009 版原文做**负向核查**：
   `already displayed` / `previously displayed` / `may not change` / `other than the base row` /
   `not be changed` → **全部 0 处命中**。而所有会改字符的机制**都明确作用在游标上**：
   `replacing any previous character occupying that location`(§15.119(v)) ·
   `erasing the character or Mid-Row Code occupying that`(vi) · 而
   `The cursor always remains on the base row`。
   → **规范约束的是位置与结构**（上滚一行、顶行离开、base row 留空、≤0.433s 且平滑）。
   「已上滚的行内容不变」是**"协议没有寻址机制"的后果**，不是一条写着的规则。
   它在 CEA-608 里成立，是因为**人工速录员只向前打、不回头改**；而我们是
   **对滑动窗口重复解码** → **那个前提根本不存在**。

2. **我自己引的那篇 Google CHI 2023 就否掉它**：逐字 `Users are often distracted by changes in
   layout, modification of words, and adjustment of punctuation in live captions`
   —— 它明说已显示的词**会**被改写，它的贡献是**减少**而不是消除。
   负向核查：那篇**没有区分**「已上滚的行」与「最下面那行」。

→ **改成几何判据**（「整块位移一个行高 + 两行同步」）。字符层面**最多做概率性检查**
（例如上滚行与前一帧底行的编辑距离 ≤ 某阈值），**绝不能当硬判据** ——
否则要么恒真（形同虚设），要么按我们的架构**根本不可能通过**。

⭐ 这正是「调研要成闭环」的价值：**这条判据要是照原样写进去，实现时必然卡住**，
而且会让人以为是实现错了。

## 4. 机制（AppKit）—— ⚠️ **2026-09-27 实测后推翻重写**

**实测口径**：`macOS 15.7.5`（`sw_vers`）· 本机 `ClassLive.app/Contents/MacOS/python`。

### 4.1 我原来写的，四条里的三条**不成立**

| 原来写的 | 实测 | 证据 |
|---|---|---|
| 动作用 `lbl.setFrame_display_animate_(...)` | ❌ **这个 API 在 NSView 上不存在** | `setFrame:display:animate:` 只在 **`NSWindow.h:369`** 有声明；运行时 `NSView.instancesRespondToSelector_` 对 `setFrame:display:animate:` 和 `setFrame:display:` **都是 False**，对 `animator` 是 True → **它只能动窗口的 frame，动不了标签** |
| 「必须 layer-backed」 | ⚠️ **现状不是** | 草稿标签 `layer()` 是 `None`、`wantsLayer()` 是 False；面板 contentView 同样 |
| 时长「0.20–0.35s（`[二手整理]`）」 | ✅ **拿到一手了**：`NSAnimationContext.currentContext().duration() == 0.25` | 直接读的；那条二手值 0.25s 是对的，现在有了一手出处 |
| `timingFunction` 收不收 / Reduce Motion 怎么读 | ✅ 都验过了 | `NSAnimationContext.setTimingFunction_` 存在；`NSWorkspace.accessibilityDisplayShouldReduceMotion()` 存在，本机 `False` |

### 4.2 于是可用的只有三条路

| 路 | 是什么 | 评价 |
|---|---|---|
| **A. 自己驱动 `f(t)`** | `pump()` 每帧按 `roll_offset(t)` 直接 `setFrame_` | ⭐ **倾向这条**。确定性、时钟可注入（§3.1 那个钩子**就是实现本身**，不是额外加的测试钩子）、**不依赖任何 AppKit 动画机制**，因此也不受 `animator()` 那条坑影响 |
| B. `view.animator()` 代理 | AppKit 隐式动画 | ❌ `PLAN-notes-and-ui.md` §5 已否过：在自建 run loop 里**可能半途卡住** → 面板停在中间尺寸**挡住字幕**（可用性事故） |
| C. Core Animation（layer 动画） | `setWantsLayer_(True)` + 动 layer | 可行，且 CA 在**渲染服务**里跑、不受我们 run loop 影响。⚠️ 代价：**中间位置不在 `frame()` 里**（在 `presentationLayer`）→ 给「终态精确」和探针都加了一层间接 |

→ ✅ **作者 2026-09-27 拍板走 A**。
它还顺带满足 §1.3② 那条（"先建探针面" —— **A 的探针面就是生产代码路径本身**，不是额外挂的测试钩子）。

### 4.3 ⚠️ A 有一个**没验**的点，只能等实现完在屏量

`pump()` 的让步节奏是「手势中不睡 / 停手 150ms 后回到 8ms」（见它自己的 docstring）。
**A 的推进均匀度完全取决于这个节奏** —— 而 §5.4 已经证明：这个负载**离线量不出来**
（绘制发生在让步里，落不落在计时区内是竞态）。
→ 所以「A 能不能给出够匀的 60Hz 推进」是**实现后**才知道的事，判据就是 §3.2 的 ④。

### 4.4 其余照旧

| 决定 | 用什么 |
|---|---|
| Reduce Motion | `NSWorkspace.accessibilityDisplayShouldReduceMotion()` ✅ 已验存在 → 走**淡入**（`setAlphaValue_` 动画 ✅ 已验该方法存在），**不位移** |
| 必须 layer-backed？ | **A 不需要**（我们直接写 frame，不用 CA）—— 原来那句「必须 layer-backed」是 B/C 路线的前提，随机制一起作废 |
| 不得阻塞 pump | 动画期间 `pump` 照常跑 |

---

## 5. ⚠️ 风险与必须先定的点（**动手前逐条回答**）

### 5.1 ⭐⭐ **谁拥有 frame？—— 这是最危险的一条**

⚠️ **就在这次改动里，OCR 已经抓到过一次同类真 bug**：
`_layout` 与 `_roll_into` **都写**草稿标签的 frame、结论相反 → 拖动缩放面板时每一步都把
1 行草稿撑回整盒高（`REVIEW §8.3` 已修）。

现在要再加**第三个写者**：动画。而 `_layout` 会被 **`_sync_panel_size`（pump 每帧 + live resize 每一步）**
反复调用 —— 也就是说 **动画每改一次 frame，同一帧里 pump 就可能把它改回去**。

→ **必须在动手前定死**：动画期间 **frame 归动画**，`_layout` **不许碰**这两个标签
（例如动画期间置一个旗标，让 `_render_draft` 跳过、`_layout` 也只记底边）。
**这条不先想清楚，写出来就是那个 bug 的第二次。**

### 5.2 动画期间又来了新草稿怎么办

草稿约 **≤1/s**（`vad.py: PARTIAL_INTERVAL_S = 1.0`），而动画只有 0.28s → 撞上不算罕见。
**候选**：① 打断当前动画、从当前位置接上（视觉最顺） ② 丢掉这一帧、等动画完（会滞后） ③ 硬切到终态。
→ **需要定**。（我倾向 ①，但要先看 0.28s 内撞上的实际频率。）

### 5.3 动画期间用户缩放面板

`setFrame_display_animate_` 与用户拖拽会同时改 frame。**候选**：① 立刻结束动画、交给 live resize
② 拒绝 resize 直到动画完（❌ 手感差）。→ **需要定**（我倾向 ①）。

### 5.4 ✅ 基线已量（2026-09-27，`probe_motion.py`）—— 以及一条**量不了**的

新建了 `probe_motion.py`（**确定性**，不靠人手；`probe_scroll.py` 交互且会翻转，不能当基点）。
四条**内容路径**的基线（面板宽 455 → 中文档位 3 行；每腿 1500 轮）：

| 腿 | pump 中位 | p99 | 超 8.3ms |
|---|---|---|---|
| ① 空转（没东西变） | **0.019ms** | 0.023 | **0/1500** |
| ② 草稿 churn（约 1/s 换文本） | **0.018ms** | 0.022 | **0/1500** |
| ③ 草稿上滚（真实节奏切 1 行/2 行） | **0.018ms** | 0.023 | **0/1500** |
| ④ 答案流式（高频推） | **0.020ms** | 0.036 | **5/1500** |

→ **内容路径的余量极大**（中位 ~0.02ms，120Hz 预算是 8.3ms）。"动画让它变慢了吗"这个问题，
在这些路径上**有足够空间**。

⚠️⚠️ **但"动画本体"那一格量不出来 —— 这是本轮最值钱的发现。**

一开始我加了一条腿：**每帧改两个草稿标签的 frame**（= 动画本体要加的负载），
不加任何同步地量它的 `pump` 耗时。结果**跨会话两态**：

| 场景 | 超 8.3ms 的帧数 |
|---|---|
| 三次连跑 | **238 / 257 / 277** |
| 另一次 | **0 / 1500** |
| 再另一次 | **2 / 1500** |

**机制查明了（读代码，不是猜）**：`Overlay.pump()` 自己写着「先派发事件 → 再 tick/flush →
**最后让步给 run loop，让 AppKit 把标签画到屏幕**」。所以**真正的绘制发生在那次让步里**，
而"这次让步有没有把重绘做掉"是个**竞态**。
→ 加循环、加 `displayIfNeeded()` 都修不好 —— 不是循环写得不够狠，是**成本落不落在计时区内本来就不确定**。

**推论（对实现有直接影响）**：
① **判据 ④ 只能在「在屏 + 真 run loop」条件下、等动画存在之后量**，且**连跑三次**。
   `probe_motion.py` 只能给出上面那四条**内容路径**的基线。
② ⭐ 反过来说：既然 `pump()` 本来就**每次都让步给 run loop**，那么用
   **`NSAnimationContext` 驱动的动画会在这同一次让步里自动前进** ——
   **它不需要自己造定时器，也不往 `pump()` 里加逐帧的 Python 工作**。
   这比"在 tick 里手写 frame"**更好**，也说明 §4 选 `animate:YES` 是对的（不只是"避开 `animator()` 的卡死"）。

---

## 6. 落地顺序

1. **§5.1 先定**（谁拥有 frame）—— 这条不完成不许写动画。
   （§5.4 的基线**已经量了**：内容路径余量极大；动画本体那一格**离线量不了**，
   留到 §6 第 5 步在屏量、连跑三次。）
2. 建 §3.1 的确定性钩子 + §3.2 的四条判据（**先于动画本体**）
3. 实现位移（**自己驱动 `f(t)`**，见 §4.2 路 A —— ⚠️ 原来写的 `setFrame_display_animate_` 在 NSView 上不存在）
4. 接 Reduce Motion（淡入分支）+ 给它一条判据
5. 真机看一眼 + 跑 `probe_scroll.py`（⚠️ 交互式、不稳定，**连跑三次判稳**）
6. 收尾：把 §3.2 的四条判据接进闸门清单（`CLAUDE.md` 完成判据）

⚠️ **动手前先加载 skill**（仓库硬规矩）：`codebase-design`（动 frame 的归属是模块边界问题）·
`verification-before-completion`（声称完成前）· 写文档时 `writing-for-agents`。

---

## 7. 这一件**不做**

- ❌ 不动「定稿句进入转录区」的动效（作者已定只做上滚）
- ❌ 不动面板展开/收起（那是 `PLAN-notes-and-ui.md` §5 阶段 D 的范围）
- ❌ 不引入任何第三方动效库（NC 许可证 + 我们只要一个 16px 位移）
- ❌ 不用弹簧 / 不用过冲
