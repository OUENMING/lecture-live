# 悬浮窗的拖动与缩放：现状、实测证据、待审方案

> 写给外部审查者。**请重点看第 0 节（三个待决问题）与第 5 节（待审方案）。**
> 本文所有数字都是本机实测，不是估计；测法与复现命令在附录。
> 日期 2026-09-24 · 机器 macOS 15.7.5 / Apple Silicon / Python 3.12 + PyObjC

---

## 0. 请审查什么

1. **待审方案（第 5 节）是不是正解**：把窗口从 `Borderless` 换成
   `Titled | FullSizeContentView` + 隐藏标题栏。这是社区公认做法吗？有没有我们没看到的坑？
2. **有没有第三条路**：我们只找到「保持 Borderless（四角不可缩放）」和「换成 Titled（拿到原生四角）」
   两个选项，且认为二者互斥（第 2.3 节）。这个判断对不对？
3. **`NonactivatingPanel` 到底能不能丢**：我们实测丢了会导致窗口不可见（第 2.1 节），
   但这在官方文档和社区里都**查不到解释**。如果这条经验是错的，整个架构可以简化很多。

---

## 1. 这个窗口要满足什么

ClassLive 是一个自用的实时英译中课堂字幕悬浮窗，浮在 PPT 上层，听课全程常驻。

| 需求 | 为什么 |
|---|---|
| 浮在别的 app 之上 | 盖在 PPT / 视频上 |
| **app 非激活时也要可见** | 上课时用户在别的 app 里，ClassLive 永远不是前台 |
| **不抢焦点** | 不能吃掉用户在其他 app 的按键 |
| 可拖动、可缩放 | 不同课、不同屏幕要调整大小与位置 |
| 缩放要跟手、四角能拖 | 用户明确要求「像正常窗口一样」 |
| 排版不能被缩坏 | 行高固定 70px（中文两行封顶），AppKit 超行数时**静默截断不留省略号** |

**其中「不抢焦点」与「像正常窗口一样」在本机实测下是冲突的**，见 2.2。

---

## 2. 实测到的硬约束（带数字）

### 2.1 去掉 `NonactivatingPanel` → 窗口完全不可见

| styleMask | `occlusionState` | 屏幕上 |
|---|---|---|
| `Borderless \| NonactivatingPanel \| Resizable` | **8194**（含 Visible 位 `1<<1`） | 正常渲染 |
| `Borderless \| Resizable` | **8192**（**无** Visible 位） | **什么都没有**（`isVisible` 仍报 `True`） |

测法：构造面板 → `orderFrontRegardless()` → `pump()` 若干轮 → 读 `occlusionState()` + 区域截图。
截图确认：8192 时面板区域内只有桌面内容；8194 时能看到面板。

> ⚠️ 这条在 Apple 文档与社区里**都没找到解释**（只找到 `NSWindowOcclusionStateVisible = 1<<1`
> 的基础语义）。标为**经验事实，机理未知**。这是我们不敢去掉 `NonactivatingPanel` 的唯一理由。

### 2.2 光标反馈需要 app 处于活跃状态

macOS 只让**当前活跃的 app** 改变光标。实测（干净桌面、逐点读 `NSCursor.currentSystemCursor()` 指纹）：

| 配置 | app 活跃 | 边缘光标 |
|---|---|---|
| 任意 styleMask | ✅ | **变成缩放光标** |
| 任意 styleMask | ❌ | 不变（箭头 / 其他 app 漏过来的光标） |

推论：**「靠近边缘光标就变」与「永不抢焦点」不能同时成立。**
作者选了「点击面板时手动激活 app」（`sendEvent_` / 拖拽层里调 `activateIgnoringOtherApps_`）。

### 2.3 无边框窗口没有四角缩放区

| 位置 | 光标 | 拖拽 |
|---|---|---|
| 四条边中点 | 缩放光标 ✅ | 缩放 ✅ |
| **四个角** | 缩放光标 ✅ | **完全无反应** ❌ |

即 AppKit **给四角装了光标、但没装缩放区**。（在 `Borderless`、`Borderless|NonactivatingPanel`、
`Titled|FullSizeContentView`、`movable` 开/关的**所有组合**下都一样。）

### 2.4 `movableByWindowBackground_(True)` 会吞掉 mouseDown

实测：`pump()` 里打印派发到的事件类型 ——

```
movable=True : 只见 MouseEntered(8) / Exited(9) / Moved(5)
movable=False: 能看到 LeftMouseDown(1)
```

**window server 在 app 之前就把 mouseDown 拿去跑「拖动窗口」会话了。**
这解释了为什么我们几版自绘缩放层「代码在跑、一次都没被调用」。

推论：**「背景拖动窗口」与「app 自己处理 mouseDown」也不能同时成立。**

### 2.5 拖边缘缩放会把主循环卡住 1.2 秒

| 阶段 | `pump()` 相邻两次调用的最大间隔 |
|---|---|
| 空闲 | **9.6 ms** |
| 拖边缘缩放期间 | **1239.8 ms** |

AppKit 在原生缩放时进入自己的事件跟踪循环，**我们的主循环整段被挂起**。
所以「在 pump 里轮询尺寸再重排」这条路在拖拽期间根本跑不到
→ 窗口框在动、内容冻着、松手才跳一下 = 用户说的「卡顿不够丝滑」。

### 2.6 拖拽生效的必要条件（实测组合）

```
movableByWindowBackground_(True) + 视图的 mouseDownCanMoveWindow() -> True   → 背景拖动 ✅
movableByWindowBackground_(False) + 同上                                    → 背景拖不动 ❌
```
`mouseDownCanMoveWindow` 是 AppKit「按下背景即拖动窗口」的开关，**默认值是 `!isOpaque`**。
我们曾显式设成 `False`，结果是**背景完全拖不动**（用户报「只有按住转录区才拖得动」——
转录区的文档视图恰好是 `True`）。

---

## 3. 当前实现

`overlay.py`（约 1600 行）里与本文相关的部分：

| 机制 | 做法 |
|---|---|
| 窗口 | `NSPanel` 子类，`Borderless \| NonactivatingPanel \| Resizable`，`movableByWindowBackground_(True)` |
| 激活 | 点击时手动 `activateIgnoringOtherApps_(True)`（光标反馈的前提） |
| 移动 | 一层覆盖整面板的「拖拽层」，`mouseDownCanMoveWindow -> True`（见 2.6） |
| 缩放 | 四条边交给 AppKit 原生；**四角**用自绘层 + 嵌套事件循环（`nextEventMatchingMask` in `NSEventTrackingRunLoopMode`），试图绕开 2.5 |
| 宽度下限 | **280 px**，行高按宽度自适应（620→2行 / 440→3行 / 360→4行 / 280→5行，实测 4798 句零吞字） |
| 尺寸记忆 | 写 `.window` 文件；只在用户真拖过、且不等于默认尺寸时才写 |

---

## 4. 已知不达标的地方

| 问题 | 现状 | 原因 |
|---|---|---|
| **四角不能缩放** | ❌ | 见 2.3 + 2.4：`movable=True` 时 mouseDown 到不了 app，自绘四角处理器没机会运行 |
| **拖边缘时内容冻结** | ❌ | 见 2.5 |
| 光标反馈要先点一下 | ⚠️ | 见 2.2，是作者选定的取舍 |
| 点击后 ⌘C/V 被本 app 的 Edit 菜单吃掉 | ⚠️ | 激活的副作用，已如实告知作者 |

---

## 5. 待审方案：换成 `Titled` + 隐藏标题栏

```python
style = (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
         | NSWindowStyleMaskResizable | NSWindowStyleMaskFullSizeContentView)
panel.setTitleVisibility_(NSWindowTitleHidden)          # 藏标题文字
panel.setTitlebarAppearsTransparent_(True)              # 标题栏透明
panel.setContentMinSize_((280, min_h))
# 三个红绿灯按钮
for i in (0, 1, 2):
    b = panel.standardWindowButton_(i)
    if b: b.setHidden_(True)
```

**理由**：Titled 窗口是**真正的正常窗口**，四角缩放区、缩放光标、live resize 全部由 AppKit 原生负责，
2.3 / 2.4 / 2.5 三个问题**一次性消失**，而且能删掉我们全部自绘缩放/拖动代码。

**已知先例**：`Henry-Jessie/mac-live-subtitle`（同为 macOS 悬浮字幕，Python + PyObjC）的
`ui_macos/subtitle_panel.py` 用的就是这个 recipe。

**我们的顾虑（请审查者重点看）**：

1. **`NonactivatingPanel` 还能不能叠加？** 我们需要它来保证「非激活时可见」（2.1）。
   `NSPanel + Titled + NonactivatingPanel` 这个组合有没有已知问题？
2. **红绿灯按钮藏得干净吗？** `standardWindowButton_().setHidden_(True)` 是公开 API，
   但有没有「被 AppKit 重新显示」的已知 bug？
3. **圆角打架**：Titled 窗口自带圆角，我们自己用 `layer.cornerRadius_(16)` + `masksToBounds`。
   两者会不会冲突、出现双重圆角或阴影异常？
4. **`FullSizeContentView` 的布局**：内容会不会被标题栏区域遮挡、或需要额外的 inset 补偿？
5. **2.1 那个不可见问题会不会复发**：Titled 窗口在 app 非激活时渲染有保证吗？

---

## 6. 取舍对比

| | 保持 `Borderless`（现状） | 换成 `Titled` + 隐藏标题栏（待审） |
|---|---|---|
| 四角缩放 | ❌ 需自绘，且与背景拖动互斥 | ✅ 原生 |
| 拖边缘丝滑 | ❌ 主循环被卡 1.2s | ✅ 原生 live resize |
| 缩放光标 | ✅（需 app 活跃） | ✅（需 app 活跃） |
| 外观 | 干净圆角卡片 | 需确认圆角/阴影不打架，需藏红绿灯 |
| 自绘代码量 | ~200 行（缩放层 + 拖拽层） | 可删 |
| 风险 | 已知 | 未知（第 5 节的 5 个顾虑） |

---

## 7. 已落地：Titled 方案 + 灰化修复（2026-09-24 当天）

已按第 5 节切到 Titled，并解决了灰化。**以下都是本机实测，不是推断。**

### 7.1 Titled 方案的可测约束全部通过

```
styleMask = 32907   Titled ✅  FullSizeContentView ✅  NonactivatingPanel ✅
occlusionState = 8194   Visible 位在 ✅   ← 关键硬约束(2.1)仍然成立
红绿灯 = [hidden, hidden, hidden]         ← 实测只有 3 个(styleMask 没设 FullScreen 位)
titleVisibility = Hidden, titlebarAppearsTransparent = True
contentView = 满幅 660x362，内容未被标题栏遮挡
```

### 7.2 灰化的真因与修复（像素级 A/B）

**真因不是** material / blendingMode / opaque —— 这三项实测本来就是对的
（`HUDWindow=13` / `BehindWindow=0` / `state=Active`）。

**真因是外观**：系统处于浅色模式（Aqua）时，Titled 窗口的 `NSVisualEffectView`
跟随窗口 `effectiveAppearance`，`.hudWindow` 被渲染成灰色。

```
系统外观 = Aqua
  修复前            面板中心平均亮度 = 0.314   ← 灰
  强制 DarkAqua     面板中心平均亮度 = 0.113   ← 暗 2.8×，通透恢复
  blendingMode=1    0.113（无变化）
  material=4        0.155（反而更亮）
```

**修复**（一行）：

```python
panel.setAppearance_(NSAppearance.appearanceNamed_(NSAppearanceNameDarkAqua))
```

**机理**（Apple 官方文档原文）：*"AppKit creates visual effect views automatically for
**window titlebars**, popovers, and source list table views."*
—— Titled 窗口**多出一个 AppKit 自动创建的标题栏 visual effect view**，合成在内容之下，
就是那层浅灰。（实测按钮的 superview 确实是 `NSTitlebarView`。）

### 7.3 一处被否掉的建议：`removeFromSuperview()` 藏红绿灯

有人建议用 `removeFromSuperview()` 而非 `setHidden_(True)`，理由是「AppKit 会把红绿灯
重新置为 visible」。**核实结论：不采纳。**

- 搜 `standardWindowButton removeFromSuperview` **零命中**，没有任何来源支持；
- 有出处的两处（Christian Tietze 2020-10、`mkll/NSWindowStyles`）**都用 `isHidden`**；
- 「红绿灯会自己回来」的社区实证指的是**位置**在窗口 resize 后复位，**不是可见性** ——
  该建议疑似张冠李戴；
- `removeFromSuperview()` 是移除 AppKit 自己的视图，官方对 `standardWindowButton(_:)`
  **没有承诺移除后的行为**，风险不明。

**采用**：`setHidden_(True)` + 循环 0..7（防御性）+ **`show()` 里重调**（Ghostty 的
`reapplyHiddenStyle()` 同思路）。

### 7.4 窗口类型开关（已加）

模块顶部一个常量即可整条切换，两条配方都实测过：

```python
WINDOW_STYLE = "titled"      # 或 "borderless"
```

| | `"titled"`（当前） | `"borderless"` |
|---|---|---|
| styleMask | `32907` Titled+FullSize+Nonact+Resizable | `136` Borderless+Nonact+Resizable |
| 四角缩放 | ✅ 原生 | ❌ 需自绘（且与背景拖动互斥） |
| 材质 | 依赖 `DarkAqua` 抵消标题栏 view 的灰 | 干净 |
| `occlusionState` | **8194 Visible ✅** | **8194 Visible ✅** |
| 实测 | 作者已确认可用 | 移动链路已验证（见 7.5） |

### 7.5 核实：macOS 27 那条风险到底是不是我们的风险

社区提到「Warp 在 macOS 27 上四角+红绿灯同时失灵」。**核实原帖
[warpdotdev/Warp#12393](https://github.com/warpdotdev/warp/issues/12393) 后，结论是
这条对我们的适用性有限。**

| 项 | 原帖内容 |
|---|---|
| 标题 | `[macOS 27] Traffic-light buttons and window resize handles do not respond to mouse clicks/drags` |
| 状态 | **CLOSED**（同源 issue #12389 也已关闭） |
| 环境 | macOS 27.0 Developer Beta (26A5353q)，2026-06-09 |
| 症状 | 红绿灯可见但点不动；边缘/四角拖不动；内容仍可点；**辅助功能 API 仍能移动/缩放窗口** |

**维护者的定位（原帖引文）**：

> Warp creates macOS windows as **full-size content views with a hidden/transparent native
> titlebar** ... The most suspicious code path is the **custom AppKit event routing that
> forwards `NSEventTypeLeftMouseDragged` and `NSEventTypeLeftMouseUp` to `contentView`**;
> on macOS 27 this may prevent AppKit from completing native traffic-light and live-resize
> mouse sequences.

**关键区别**：出问题的是 Warp **在 Titled 配方之上额外加的一层 mouseDragged/Up 转发**，
**不是 `Titled | FullSizeContentView` 这个配方本身**。ClassLive 没有那层转发。

⚠️ **但我们的风险不是零**：`overlay.py` 的 `pump()` **本身就是自定义事件路由**
（手动 `nextEventMatchingMask` + `sendEvent_`）。它和 Warp 那层不是一回事（我们是主循环，
他们是叠加的拦截层），但同属「AppKit 事件路由被第三方接管」这一类。**升级系统后必须重测四角。**

### 7.6 剩余风险

| 风险 | 证据 |
|---|---|
| **对系统版本敏感** | Ghostty #7568（macOS 26）*"`macos-titlebar-style = transparent` doesn't work"*；Warp #12393/#12389（macOS 27 beta，已关闭）—— 后者根因是 Warp 自加的转发层 |
| 灰化补偿是**经验值** | `DarkAqua` 一行有效，但官方没有承诺 Titled 窗口的材质行为跨版本一致 |
| 备选（更稳但更朴素） | **放弃材质改纯色**。本项目可读性由 scrim + 文字描边承担（见 `SCRIM_ALPHA` 注释的实测），材质并非关键 —— 跨版本最稳 |
| **升级系统后要重测** | 四角缩放 + 红绿灯是否还在。若坏了 → 把 `WINDOW_STYLE` 改成 `"borderless"` |

---

## 8. 核实：「换材质提升通透感」这条建议**不成立**

收到一条建议：把 `material` 从 `HUDWindow` 换成 `underWindowBackground` 或 `popover`
以提升通透感。**核实结论：不采纳，而且它的意图是反的。**

### 8.1 enum 值错配（查 SDK 头文件）

| 建议写法 | 注释说 | **实际** |
|---|---|---|
| `setMaterial_(2)` | underWindowBackground | **2 = `AppearanceBased`**（10.14 起废弃）；`UnderWindowBackground` 是 **21** |
| `setMaterial_(11)` | popover | **11 = `Sheet`**；`Popover` 是 **6** |
| `setMaterial_(13)` | hudWindow | ✅ 正确 |

### 8.2 通透度实测：HUDWindow 已经是**最高**的（不是最低）

测法：面板下方垫纯白 / 纯黑全屏窗口（**背景窗口只建一次、固定不动**），
截面板中部空白区，量平均亮度；每个材质每侧取 3 次中位数。
「透光差 = 白底亮度 − 黑底亮度」越大 = 透光越多 = 通透感越强。

```
 值 材质                       白底    黑底   透光差
 12 WindowBackground         0.111  0.111  0.000   ← 完全不透
 11 Sheet                    0.092  0.092  0.000   ← 完全不透
 21 UnderWindowBackground    0.187  0.090  0.097   ← 建议推荐的
  2 AppearanceBased          0.333  0.090  0.243   ← 建议字面给的值
  6 Popover                  0.275  0.073  0.201
 15 FullScreenUI             0.320  0.065  0.255
 13 HUDWindow                0.363  0.058  0.306   ← **现状，最高**
```

**`HUDWindow` 的透光差是建议推荐的 `UnderWindowBackground` 的 3.2 倍。**
建议称 HUDWindow「不透明遮罩偏厚、有浓郁的黑墨感、通透感弱」—— 与实测相反。

> ⚠️ 测量噪声提示：第一次测得的绝对值与第二次差很多（HUDWindow 0.306 vs 0.075），
> 原因是背景窗口每次重建导致层叠关系不稳。上表是**固定背景窗口 + 取中位数**后的结果，
> 两次干净测量的**排序一致**（HUDWindow 居首）。绝对数值请勿跨环境引用。

### 8.3 另外两条建议

| 建议 | 核实 |
|---|---|
| 「关掉系统窗口阴影以免边缘发虚」 | ⚠️ **代码与注释自相矛盾**：注释说"关掉"，代码写的是 `setHasShadow_(True)`。当前 `hasShadow=True`（默认值）。对圆角卡片来说阴影通常**是想要的**（层次感），且"阴影会在 NSThemeFrame 外沿合成暗边"这一说法未找到证据。**不动。** |
| 「文字加描边 / 局部 scrim，别靠调暗整块毛玻璃」 | ✅ 说对了，但**本项目已经在做**：`_shadow`（blur 1.5 / offset (0,-1) / 黑 α0.80）+ `SCRIM_ALPHA=0.45`，见 `overlay.py` 顶部 docstring 与 `SCRIM_ALPHA` 注释里的实测。**无需改动。** |

**结论：材质不动。`HUDWindow` 已是最通透的选择，且当前设置（material 13 /
blendingMode 0 BehindWindow / state 1 Active / DarkAqua）全部正确。**

---

## 9. 连带改动：解耦滚动权限 + 删掉顶栏「展开」按钮

**为什么这是「窗口可缩放」的连带后果**：窗口一旦能自由缩放，原来那个「展开/收回」
按钮就暴露出一处设计耦合。

### 9.1 问题：滚动权限被绑在「收没收起」上

`transcript_view.set_collapsed()` 原先写的是 `_scroll_enabled = not collapsed`。
后果实测：

```
收起态:              _scroll_enabled = False
展开态:              _scroll_enabled = True
拉到 700px 高(11 句): _scroll_enabled = False   ← 拉大窗口**不会**打开滚动
```

而一节真实课有 **1080 / 1233 句**（实测两份 `sessions/`），可见只有 3 句。
**于是那个展开按钮被迫承担「解锁滚动」的职责** —— 它名字里完全没写这件事，
而「拉大窗口」这条更直觉的路径对翻历史无效。

### 9.2 改法

| | 改动 |
|---|---|
| `transcript_view.set_collapsed` | `_scroll_enabled` 改为**恒为 True**（内容永远溢出）；橡皮筋一并常开（`autohidesScrollers` 已开，滚动条不常驻） |
| `overlay.py` | 删 `_btn_expand` 与 `_toggle_mode`；顶栏可见按钮 **6 → 5** |
| **保留** `_apply_mode` | **答案接管仍在用它**（答案出现时展开、退出接管时还原）。删除按钮不影响它 |
| `probe_scroll.py` | 阶段①的断言**反转**：旧版断言「收起态视口必须纹丝不动」，现在断言「收起态必须能滚 + ↓最新出现」。这是**有意去掉一条既定不变量**，故留痕 |

### 9.3 新的心智模型

```
拉窗口 = 你想同时看几句
滚动   = 你想往回翻
```
两者正交。

### 9.4 实测（合成滚轮，39 事件/次）

```
头部按钮 = [↓最新(隐藏), 讲一下, 新话题, 译开, ⭐, ✕]   可见 5 个
1 收起态滚动:         origin 0 -> 228    ✅ 能滚   ↓最新出现
2 拉大到 700 后滚动:   origin 234 -> 462  ✅ 能滚  ← **解耦前做不到**
3 展开态滚动:         origin 6 -> 246    ✅ 能滚
答案接管的展开/还原:   518 / 362          ✅ 未受影响
```

### 9.5 代价（已知并接受）

收起态不再吞滚轮 → 鼠标停在面板上时误滚会离开直播视图。
缓解机制本来就有：`↓ 最新` 按钮 + 闲置 6 秒自动回底（`IDLE_S`）。
**但那 6 秒里看不到新字幕** —— 这是本次改动的唯一代价，作者已知悉并接受。

### 9.6 还原点

`~/lecture-live-checkpoints/2026-09-24_1457_before-decouple-scroll/`
（含 `RESTORE.md` + `src-data.tar.gz` + `uncommitted.patch`）

---

## 10. 全仓 OCR 规则集审计（2026-09-24）

用阿里 Open Code Review 的**规则集**（`ocr delegate rule`，delegation 模式，零 LLM）审了
全项目 18 个文件 / 6561 行。规则集第一条是硬指令：*"Favor precision over recall…
a false alarm costs more reviewer trust than a missed minor issue."*

### 10.1 ocr 普通模式已修好（端点要求变了）

cc-switch 端点现在要求 `x-opencode-session` 头，否则 `400 MissingSessionID`。
`ocr config` 支持 `extra_headers`，格式是 **`key=value`**：

```bash
ocr config set custom_providers.cc-switch.extra_headers 'x-opencode-session=ocr-review'
# → ✓ Connection test successful (走 cc-switch → deepseek-v4.1-flash)
```

### 10.2 抓到 3 条真缺陷（全部逐条回原码核实过）

**① `main.py` 收尾丢句 —— 代码与它自己的注释相反**（本次会话**未改动**的存量代码）

`all_settled()` 的 docstring 写明了实测复现过的丢句场景；`_force_emit_carry` 的注释也写明
*"busy 必须在清空 carry **之前**置位"* —— **但代码是先清 `carry` 后置 `busy`**：

```python
carry["text"], carry["since"] = "", 0.0    # 先清 → carry_text 变空
busy["on"] = True                          # 后置 → 这 2 条字节码之间四项同时为空
```
`final_worker` 里是同一个模式。**已按注释修回**（busy 先置位，严格更安全）。
同时改正了 `final_worker` 那句过度声明的注释（原文称 "busy 覆盖 ASR + _emit 全程"，
而 `asr.transcribe` 在其之前执行；真把 busy 提前会让几条 `continue` 路径忘记清 busy，
反而空等到 15s 上限 —— 故只修顺序）。

**② 翻译器负零切片 —— `--context 0` 会把整节课塞进每次请求**

`translator.py:389` / `cloud_translator.py:249,276` 三处 `context[-self._max_ctx:]`。
Python 里 `-0 == 0`，所以 `context[-0:]` == `context[0:]` == **整个列表**：

```
max_ctx=5 → 5 句 ≈ 52 token
max_ctx=0 → 1080 句 ≈ 11062 token   ← 用户想表达"不要上下文"
```
一节 1000+ 次请求，每次都多带 1.1 万 token。**已修**（`if self._max_ctx > 0 else []`）。

**③ 我这次编辑留下的三处残留**（全在 `overlay.py`，已清理）
- `_DragLayer.mouseDown_` 里**激活块重复了两遍**（逐字相同，连注释两份）
- `style = ...` 前**注释块重复**（两组都在讲 NonactivatingPanel + Resizable）
- `_track_loop` 的 `"move"` 分支**全仓无调用点**（唯一调用传 `"resize"`）

### 10.3 ⚠️ 一条**被真机推翻**的审计发现 —— 并连带修正本文 2.4

审计报：**「点击激活」整条链不可达** —— `movableByWindowBackground=True` 时
`_Panel.sendEvent_` 与 `_DragLayer.mouseDown_` 都不被调用。

**真机复核：作者实测「点击激活是正常的」→ 该发现不成立。**

**连带修正 2.4**：那一节写的是「`movableByWindowBackground_(True)` 会吞掉 mouseDown
（`pump` 只看得到 MouseEntered/Moved，看不到 type=1/6）」—— **那条也是合成事件测的**。
真机下激活链是活的，说明**真实事件不会被 window server 那样吞掉**。

**教训（对本文整体适用）**：本文里凡是**用合成鼠标事件测出的 AppKit 行为结论**，
都要打折扣。已知的合成事件边界（见第 11 节）比原先以为的更大 —— 至少包括：

| 结论 | 合成事件 | 真机 |
|---|---|---|
| 激活链是否可达 | ❌ 测成"不可达" | ✅ **正常** |
| `movable=True` 是否吞 mouseDown | ❌ 测成"吞" | 未测（但激活链活着说明不吞） |
| 四角能否拖 | ❌ 全配置都测不出 | ✅ Titled 下正常 |

**结论：`_on_drag_layer_mousedown` / `_track_loop` / `_resized_frame` / `_zone_` 这条自绘链
保留，不是死代码。** 用合成事件给它判死刑是我的装置缺陷，不是代码缺陷。

### 10.4 审计主动排除的（避免假阳性）

`except Exception: pass`（本项目刻意，上课不能崩）、`abs(a-b) < 0.5` 浮点比较（惯用写法）、
`getattr(..., True)` 兜底默认 —— 均为成文惯例或有意设计。

另有一批**查过但确认无问题**的负证据：`drain()` 的 7 个 tag 与 7 个分支逐一对应无孤儿；
`append` 写 / `_parse` 读 / `cl` grep 三方文件契约一致；`_Latest` 并发正确；
`split_sentences` 不会死循环；`asr.py` 的锁覆盖完整。

### 10.5 连带修掉的陈旧文案（断言反转后的遗留）

| 位置 | 问题 |
|---|---|
| `probe_scroll.py` 屏幕提示 | 印「滚轮应无任何反应」，而判定要求 `moved > 5.0` —— **同一次运行里指引与结论互相打脸** |
| `transcript_view.py:52` | 注释「收回态吞掉滚轮」已不成立 |
| `transcript_view.py:18` | docstring「主行封顶 2 行」已陈（现为 3/4/5） |
| `probe_scroll.py:149,116` | 陈旧注释与提示 |
| `transcript_view.set_row_metrics` | 未回写 `_expected_origin` → 离开底部时改窗宽会让可见句**跳一下**（低频，已修） |

### 10.6 另跑了一遍**普通模式**全量扫描（走 LLM）

`ocr scan`（18 文件 / 6561 行）→ **7分35秒 / 138 万 token / 65 条**
（critical 1 · high 4 · medium 27 · low 33）· 走 cc-switch 实花费 0。

**5 条 high/critical 的核实结果**：

| 判定 | 内容 |
|---|---|
| ❌ **假阳性** | critical 报 `rebuild_note.py` 正则 `[!abstract]` 未转义 —— 实际代码**本来就是转义的**，且它给的行号是 `0-0`（没定位到） |
| ✅ 属实（**数据丢失**） | `main.py` 收尾 `src.close()` 无异常保护 → 抛了会跳过 `seg.flush()` + 排空 + `writer.close()`（会话落盘 + Obsidian + 精修全丢）。对照证据：probe 路径专门包了 try/except |
| ✅ 属实 | `translator._ensure()` check-then-act 竞态 —— 4 个调用点都在 `with self._lock` **之外**；`_model, _tokenizer = load()` 是两条 STORE_ATTR，另一线程可能读到 `_model` 有值而 `_tokenizer` 为 None |
| ✅ 属实 | `cl-bg.py` fd 顺序 —— 调用方关了 stdin 时 `os.open(LOG)` 可能拿到 fd 0，随后 `dup2(devnull,0)` 把它关掉，日志全写不进去；临时 fd 还泄漏 |
| ✅ 属实 | `cl` 的 `file` 分支传相对路径 —— 脚本开头已 `cd` 到安装目录，用户在自己目录跑 `cl file x.m4a` 会「文件不存在」 |

**4 条属实的已全部修复**（见 `git diff`）。

⚠️ **一条 token 警告**：`overlay.py`（1918 行）的 prompt 达 50967，**超 80% 上限**（58888）
→ **大文件的审查可能不完整**。以后大文件要拆开单独审。

**它的 `project_summary` 有额外价值**：会自己归纳**跨文件的同一根因**。这次第一条是
「静默失败导致数据丢失（跨 6+ 文件）」，把 `build_notes.py` / `vad.py` / `translator.py` /
`capture.py` / `cloud_translator.py` 的 `except: pass` 串成一条 —— 其中 `build_notes.py`
最重：JSON 解析失败静默返回 `{}`，`build()` 随后以空字典 `save_to()` **覆盖写回**，
人工整理的 glossary 全丢。这类归纳是逐文件审不容易得到的。

---

## 11. 附录：复现方法

所有实验都用**合成鼠标事件**（`Quartz.CGEventPost` 到 `kCGHIDEventTap`）+ 干净桌面
（隐藏其他 app）。探针脚本在 `/tmp`（不在仓库里），关键几个：

| 脚本 | 测什么 |
|---|---|
| `cursor_arch.py <borderless\|titled>` | 八个位置的光标指纹 |
| `final_map.py` | 四角/四边/移动的完整地图 |
| `gap.py` | 拖拽期间 `pump()` 的间隔（2.5） |
| `zone_map.py` | 八向拖拽 |

**已知的测试装置边界（请注意）**：
- 合成事件**不会被送进 `NSEventTrackingRunLoopMode`**，所以**自绘的嵌套事件循环无法用合成事件验证**
  （AppKit 自己的 `performWindowDragWithEvent_` 走 window server 会话，不受此限）。
  这导致「四角自绘方案」我们**无法自证**。
- 合成点击**不能改变前台 app**（window server 限制），所以「点击激活」只能显式调
  `activateIgnoringOtherApps_` 来模拟。
- 屏幕锁定 / 显示器休眠时，`occlusionState` 会丢 Visible 位、区域截图会失败 ——
  测之前必须确认 `CGSSessionScreenIsLocked` 为假。
