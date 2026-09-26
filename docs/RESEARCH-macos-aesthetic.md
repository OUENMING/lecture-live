# macOS 视觉语言调研：什么让一个浮层「是原生 Mac app」

> 2026-09-26。给 `overlay.py`（字幕悬浮窗）与第二批的 `entry_panel.py`（准备面板）用。
> **口径**：`[一手]` = Apple 官方文档/HIG/WWDC 讲稿/SDK 头文件，读到原文；`[实测]` = 有人给了
> **方法和数字**（像素测量/反汇编）；`[二手]` = 可信第三方转述；`[论坛]` = 社区；`[未找到]` = 明确没有。

---

## 0. ⭐ 先看这条：HIG 有整个 `Panels` 章节，而**我们违反了它好几条**

`[一手]` https://developer.apple.com/design/human-interface-guidelines/panels（含 `HUD-style panels` 子章）

| HIG 原文 | 我们的做法 | 判定 |
|---|---|---|
| 「**a panel... needs a title bar** so people can position it where they want」 | **无边框** + 藏掉红绿灯 | ⚠️ 偏离 |
| 「**When your app is inactive, hide all of its panels.**」 | 面板就靠「app 不在前台时仍然浮着」工作 | ⚠️ **正面冲突** |
| 「**Prefer simple adjustment controls in a panel.** ...avoid including controls that require **typing text**」 | 有输入框（提问） | ⚠️ 偏离 |
| HUD 章：「use a HUD only ... **When you don't need to include controls** — with the exception of the disclosure triangle, **most system-provided controls don't match a HUD's appearance**」 | HUD 材质 + 输入框 + 按钮 | ⚠️ 偏离 |
| 「**Keep HUDs small** ... Don't let a HUD obscure the content it adjusts」 | 660×330 带滚动字幕 | ⚠️ 偏离（但字幕本身就是内容） |

⭐ **为什么这些偏离是对的**：HIG 的 panel 模型是「**服务于本 app 自己的窗口**」的附属面板
（原文：「provides supplementary controls, options, or information **related to the active window
or current selection**」）。而我们的悬浮窗服务于**别的 app 的内容**（课堂 / 网课）。
**形态不同，所以那些条款不直接适用** —— 但**要知道自己偏离了、以及为什么**，
别以为「反正是个浮窗」就随便。**这条要写进代码注释，不然下一个人会当成 bug 来修。**

⚠️ 反面：HUD 章还有一条**不冲突、且我们该直接采纳**的：
> 「**Use color sparingly in HUDs.** ...Often, you need only small amounts of high-contrast color to highlight important information」

---

## 1. ⭐ 同心圆角 —— 全篇**最硬、最可检查**的一条

`[一手]` 三个独立来源给了同一个公式：

- SwiftUI `GeometryProxy.concentricCornerRadii`：「The radius for each corner is calculated as
  **the container's corner radius minus the distance from this view's corner to the container's corner.**」
- WWDC25/356：「**concentric** shapes calculate their radius by **subtracting padding from the parent's**」
- HIG 顶层三原则之 Harmony：「Align with the **concentric** design of the hardware and software」

**→ 定式**：（外圆角 − 内边距 = 内圆角）

| 外 | 内边距 | **内圆角** |
|---|---|---|
| 16（面板） | 12 | **4** |
| 16 | 8 | **8** |
| 16 | 4 | **12** |

⚠️ **Tahoe 时代被骂的不是「圆角多大」，是「半径之间没有推导关系」**（`[二手]` Jeff Johnson /
Michael Tsai 汇集的设计师原话：「comically large」「Clownish」「**the same corner radius is applied
to the bottom of the window, where the concentricity does not match any elements**」）。
→ **子元素圆角一律从容器算，不许硬编码。**

---

## 2. 可直接用的数字（都是 `[一手]`，除非另标）

### 2.1 排版：macOS text style 全表
`[一手]` HIG › Typography。**macOS 没有 Dynamic Type**（原文逐字）。

| Style | Weight | pt | 行高 | 加粗时 |
|---|---|---|---|---|
| Large Title | Regular | 26 | 32 | Bold |
| Title 1 | Regular | 22 | 26 | Bold |
| Title 2 | Regular | 17 | 22 | Bold |
| Title 3 | Regular | 15 | 20 | Semibold |
| Headline | **Bold** | 13 | 16 | Heavy |
| **Body** | Regular | **13** | **16** | Semibold |
| Callout | Regular | 12 | 15 | Semibold |
| Subheadline | Regular | 11 | 14 | Semibold |
| Footnote | Regular | 10 | 13 | Semibold |
| Caption 1 | Regular | 10 | 13 | Medium |
| Caption 2 | **Medium** | 10 | 13 | Semibold |

- **默认 13pt，最小 10pt** `[一手]`
- **行高**：不是统一倍数，落在 **1.18–1.33**（中位 ≈1.25）
- **不要细字重**（原文）：「avoid Ultralight, Thin, and Light」
- ⚠️ **层级靠「字重 + 字号 + 颜色」三者**（原文：adjust **font weight, size, and color**）——
  **只靠字号、字重都一样 = 主动放弃官方列出的第一项**
- **API**：`NSFont.preferredFontForTextStyle_` + `NSFontTextStyle*` 11 个常量（macOS 11+）

⭐ **字距表（做实时字幕最该抄的一张）** `[一手]` HIG › Typography：

| pt | 13 | 15 | 17 | 20 | 26 |
|---|---|---|---|---|---|
| tracking | **−0.08** | −0.23 | −0.43 | −0.45 | +0.22 |

⚠️ **但那张表不是「让你手动加的字距」** —— 本机实测（2026-09-26）：

```
字号   实测宽 / 字号        13pt 归一
10pt   17.05615           1.03964      ← 纯线性缩放的话，这一列应该全是 1.00000
13pt   16.40576           1.00000
20pt   15.24788           0.92942
26pt   14.83845           0.90447      ← 单调收紧
```

**`NSFont.systemFontOfSize_` 已经自带随字号变化的间距**（比值单调偏离线性，跨度 13%）。
把 HIG 那张表再加一遍就是**双重施加**。

→ **本项目不加字距，这是对的，不是缺口。**
（⚠️ 归因保留：实测的偏离量远大于 HIG 表里那几个值（≤0.45pt/字），
所以驱动它的大概是 **SF 的光学尺寸（optical sizing）**，而不是那张表本身 ——
但**「别手动加」这个结论两条路都成立**。）

### 2.2 控件与内边距
| 项 | 值 | 档 |
|---|---|---|
| macOS 控件默认尺寸 | **28×28** | `[一手]` |
| 最小可点尺寸 | **20×20** | `[一手]` |
| 带 bezel 元素四周 | **~12pt** | `[一手]` |
| 无 bezel 元素（可见边缘）四周 | **~24pt** | `[一手]` |
| 按钮（regular） | 高 ~32 / 圆角 ~6 | `[实测]` 约定 |
| 菜单行高 / 选中块圆角 | **32pt** / **8pt** | `[实测]` |
| 系统 HUD | **200×200**，圆角 **18.0**（反汇编常量 `0x4032000000000000`），停留 **2s** | `[实测]` LLDB |

### 2.3 间距：⚠️ **Apple 对 macOS 零数字，「8pt grid」不是它的**
`[一手]` HIG › Layout 对 macOS **没有任何间距数字**（`Grids` 一节只讲 tvOS/watchOS）。
`NSView` 只有 `layoutMarginsGuide`（「the recommended amount of padding」）**无公开数值**；
`NSStackView.edgeInsets` / `spacing` **都不给默认值**。

→ **别把 8pt grid 说成「Apple 的规矩」**。HIG 对这个问题的官方立场只有一句定性的：
> 「Group related items... you might use **negative space**, container shapes, or **separator lines**」

**可用的间距词汇表**来自 `[实测]` 的从业者测量（不是 Apple）：
`6`（最小）/ `8`（label↔控件）/ `12`（分组底噪，**上下限 12–24，别超**）/ `14`（距顶）/ `16`（组内）/ `20`（窗口边距）
→ 用法：**定一套 4 的倍数做 token，比「随手 10/13」强在可解释**，但**别声称是 Apple 规定**。

---

## 3. ⚠️ 材质：三条硬规则 + 两个默认值陷阱（都 `[一手]` SDK/HIG）

1. **vibrancy 只在叶子视图**：
   > 「enable vibrancy only in the **leaf views**... Once enabled in a parent view, a subview
   > **cannot turn off** vibrancy」
2. **内容要灰阶**：
   > 「Vibrancy works best when your custom views contain **grayscale** content」，并点名用
   > **`labelColor` / `secondaryLabelColor` / `tertiaryLabelColor`** 而不是自定灰
3. **别按「看起来什么颜色」选材质**：
   > 「**Don't select materials based on the apparent colors they impart** on your interface」

⚠️ **两个默认值陷阱**：
- `NSVisualEffectView.state` **默认是 `FollowsWindowActiveState`** —— 对一个几乎不激活的
  `.accessory` app，**这是必须覆盖的**（我们设了 `Active` ✓）
- ✅ 「**inactive windows don't use materials**」（HIG › Windows 逐字）—— **已实测（2026-09-26）**，
  结论是**没问题，但依赖一行代码**：

  | `glass.state()` | 离屏渲染均亮 | 上屏 · 白底 | 上屏 · 深底 |
  |---|---|---|---|
  | **`Active`**（`panel.py` 显式设的） | 57.4 | **168.7** | **27.3** |
  | `FollowsWindowActiveState`（AppKit 默认） | 24.7 | **168.7** | **27.3** |

  ⚠️⚠️ **离屏那 2.3 倍的差，上屏量不到** —— 白底、深底都**一模一样**。
  离屏渲染没有「窗口背后是什么」可合成，那个差是这个退化情形的产物。

  ⭐ **所以别把离屏的差值当成用户看得见的差值。** 我第一版就是这么写错的
  （原文：「删掉那一行面板每节课暗 2.3 倍」），**上屏实测推翻了它**。

  ⭐ **这条对仓库的闸门有直接影响**：`test_panel.py` 的 `render_hash` 是**离屏**的 ——
  它是有效的**回归探测器**（确定性、对内容层属性敏感），但**它的幅度不是屏幕上的幅度**。
  **它红了不等于用户看得出。**

  ⚠️ 那 `panel.py` 那行 `setState_(Active)` 还要不要留？**留** ——
  它是配方的一部分、有官方依据（`NSVisualEffectView.state` 默认跟随窗口激活态，
  而我们是几乎不激活的 `.accessory` app），**只是别声称「不留会暗 2.3 倍」**。
  本次没能驱动「app 激活」那条轴（`app.isActive()` 恒 False），
  所以「激活时两者是否有别」**仍未知**。

⭐ **窗口阴影**：`NSVisualEffectView.h` 原文 —— `maskImage` 用在一个**作为 window contentView**
的 effect view 上时，「**will correctly influence the window's shadow**」。
→ 我们 16pt 圆角 + 无边框，**阴影形状由这一条决定**。

### 压暗层：官方数字是 **35%**
`[一手]` HIG › Materials：
> 「If the underlying content is bright, consider adding a **dark dimming layer of 35% opacity.**」
（注：这是 **clear Liquid Glass** 的配方，不是 HUD 的。我们的 scrim 是 **0.38** —— 同数量级，
且当初是像素级量出来的，不是拍的。）

---

## 4. ⭐ 可读性有一条**硬门槛**（我们最该拿去当验收判据的）

`[一手]` HIG › Dark Mode：
> 「At a minimum, make sure the contrast ratio between colors is **no lower than 4.5:1**.
> For custom foreground and background colors, strive for a contrast ratio of **7:1**,
> especially in **small text**.」

→ **字幕文字对背景的对比度 ≥4.5:1，小字目标 7:1。** 这是**可以算出来的**，
比「看起来够清楚」强得多。⚠️ 但注意：字幕要压在**任意**背景上 —— 这个数只能对
**面板内部的底色**成立，不能对「屏幕后面的 PPT」成立。

---

## 5. 动效

| 项 | 值 | 档 |
|---|---|---|
| macOS 区间 | **0.20–0.35 s**；**超过 0.4s 几乎肯定太慢** | `[二手整理]` |
| `NSAnimationContext.duration` 默认 | **官方文档没写**（唯一出处是 2008 年 Stanford 课件：0.25s） | `[未找到]`/`[二手]` |
| `NSWindow` resize/sheet | **~0.20 s** | `[一手]` |
| SwiftUI `.easeIn`/`.easeInOut` | **0.35 s** | `[一手]` |
| SwiftUI `Animation.default` | `spring(response: 0.55, dampingFraction: 1.0)` | `[一手]` |

⚠️ **HIG › Motion 对 macOS 的原话是「No additional considerations for iOS, iPadOS, macOS, or tvOS.」**
—— 官方**不给时长**。上面那些是别处的默认值，别包装成「HIG 规定」。

⭐ **AppKit 现在能直接吃 SwiftUI 的动画**（macOS 15+，`[一手]`）：
`NSAnimationContext.animate(.smooth) { ... }` —— 且「can be **smoothly retargeted while
preserving velocity**」（我们那个 live-resize 场景正好用得上）。

---

## 6. macOS 26 / Liquid Glass —— 对**第三方浮层**意味着什么

- **构建 SDK 决定外观**：用 **Xcode 26 SDK** 编 → 自动获得新外观；加 `UIDesignRequiresCompatibility`
  可留在旧外观 `[一手]`。**本机：系统 macOS 15.7.5，SDK 26.2** → 今天跑旧外观，
  **一旦用 26 SDK 重编就会变样**。
- **新 API**：`NSGlassEffectView`（macOS 26.0+，有 `cornerRadius` / `tintColor` / `style`）
  与 `NSGlassEffectContainerView` `[一手]` SDK 头文件。
  ⚠️ 注意 `NSVisualEffectView` / `.hudWindow` **没有被废弃**（`.hudWindow` = raw 13）。
- ⚠️ **Apple 对「自己画玻璃」的措辞很硬**（多处 `[一手]`）：
  > 「**Reduce your use of custom backgrounds** in controls and navigation elements」
  > 「**Don't use Liquid Glass in the content layer.**」「**Use Liquid Glass effects sparingly.**」
  > 「If you use an `NSVisualEffectView` to display that material inside of your sidebar,
  > **it will prevent the glass material from showing**」
- ⚠️ **针对「第三方浮动面板」的专门条款：不存在。** 所有措辞都围绕
  **toolbar / sidebar / sheet / popover / menu**。我们这类「压在别人内容上的浮层」
  **Apple 没有表态** —— 所以**没有官方答案可抄，只能自己判断 + 写清理由**。

---

## 7. 自查表：「一眼看出不是原生」的指纹（挑我们可能踩的）

| 指纹 | 我们 | 来源 |
|---|---|---|
| 点标题栏空白拖不动窗口 | ✅ 已修（那个拖拽层就是为此加的） | `[一手]` Electron 自述/文档 |
| styled button 有 hover，系统按钮**没有** | ⚠️ 自查我们的小按钮 | `[二手]` |
| 正文用 16px（web 默认）而不是 13pt | ⚠️ 自查 | `[一手]` 开发者自述 |
| **强调色滥用** | ⚠️ HUD 章明确说「use color sparingly」 | `[一手]` |
| 手型光标（原生只用于「开浏览器的链接」） | ⚠️ 自查 | `[二手]` |
| 该选不选/不该选却可选（正文被随便选中） | 字幕**可选中复制**是有意的 | — |
| 菜单/表格行有 hover 高亮（原生没有） | 不适用 | `[二手]` |
| 非整数坐标 → 文字发虚 | ⚠️ 自查所有 frame 是否整数 | `[一手]` Bjango |

---

## 8. `[未找到]` —— 诚实清单（别当我藏了）

| 缺什么 | 结果 |
|---|---|
| **控制中心 / Quick Look / Xcode HUD 的几何参数** | **公开互联网上没有带方法的数字**。复刻项目只给「复刻者自选」值。⚠️ **Spotlight 已由我们自己量到** —— 见 §10 |
| **macOS 间距网格** | **不存在**（HIG 对 macOS 零数字；AppKit 无数值） |
| **窗口/卡片/按钮的具体圆角值** | Apple **从不公布**；只有「随窗口样式变化」的定性描述 |
| **窗口阴影的参数**（模糊/偏移/不透明度） | HIG 全站 `shadow` **零命中**；SDK 只有 `BOOL hasShadow` |
| **`NSAnimationContext.duration` 的官方默认值** | **未找到**（唯一出处是 2008 年课件） |
| **HUD 面板的尺寸规格** | **不存在**（只有一句「Keep HUDs small」） |
| **针对第三方浮动面板的 Liquid Glass 条款** | **不存在** |

⭐ **一条检索技巧**（下次省一半时间）：Apple 文档站是 Vue SPA，直接抓 HTML 只拿到空壳。
**要抓内部 JSON**：`https://developer.apple.com/tutorials/data/design/human-interface-guidelines/<page>.json`
（Apple 文档页同理：`.../tutorials/data/documentation/...json`）。

---

## 9. ⭐ 缺的那些，**可以在本机自己量**

上面「未找到」的（尤其 Spotlight / 控制中心的几何），方法就摆在机器上（`[实测]` 那几篇用的方法）：

1. **Accessibility Inspector**（Xcode → Open Developer Tool）—— 指向 Spotlight / 控制中心，
   右栏直接读 `AXPosition` + `AXSize`，**单位就是 pt**。最省事。
2. **Retina alpha 边缘测量** —— `screencapture` → 沿对角线扫 alpha，第一个非 0 的就是弧起点。
   ⚠️ Retina 下「10pt」= 20 像素；**窗口阴影会污染结果**，要按真实 bounds 算。
3. **LLDB attach + 断点**（系统 HUD 那个 18pt 就是这么来的）—— 需要先关 SIP。

→ **第二批开工前先做第 1 条**，拿到的比任何博客都准。


---

## 10. ⭐ 我们自己量的：Spotlight 与控制中心的真实几何（2026-09-26）

### 10.1 Spotlight

**方法**（不需要 Xcode —— 这是本次最有用的一条）：**`System Events` 直接读 AX 接口**，
就是 Accessibility Inspector 底层用的那一套，**单位就是 pt**：

```applescript
tell application "System Events" to tell process "Spotlight"
  get {position, size} of window 1
end tell
```

⚠️ 三个**踩过的坑**（都不是猜的）：
- **AX 不暴露圆角** —— 位置/尺寸/层级能读，圆角只能从截图量
- ⚠️ **截图差分法在这台机器上不可用**：桌面有在动的内容（什么都不动连拍两张，
  **96592 个像素点不同、变化区 1182×721 pt**）。所以「跟基线比对找 UI」会把视频帧也算进去。
  **正确做法是拿 AX 给的已知坐标去量**，不做差分。
- ⚠️ 手搓 `CGImage` 解码读过自相矛盾的数据；**用 `NSBitmapImageRep.colorAtX:y:`** 才对

**读数**（空查询状态，即只有搜索条、没有结果列表）：

| 元素 | role | position (pt，左上原点) | size (pt) | 备注 |
|---|---|---|---|---|
| 窗口 | `AXWindow` / `AXSystemDialog` | (456, 197) | **600 × 52** | 水平**精确居中**（456+300 = 756 = 1512/2 ✓） |
| 放大镜按钮 | `AXButton` "Search" | (468, 208) | **28 × 27** | ⭐ **对上 HIG 的「macOS 控件默认 28×28」** |
| 搜索框 | `AXTextField` | (499, 206) | **544 × 33** | |

**算出来的：**

| 项 | 值 | 对照 |
|---|---|---|
| **圆角半径** | **≈15 pt** | 最小二乘拟合 15.00、残差亚像素。⚠️ 另两个「直接读」给 13.5 —— 被首行位置与量化偏了，别用它们 |
| **左内边距** | **12 pt** | ⭐ **正好是 HIG 写死的「带 bezel 元素四周约 12 pt」** |
| 右内边距 | 13 pt | |
| 图标 → 搜索框间距 | 3 pt | |
| 垂直内边距 | 上 11 / 下 14 pt | |

**→ 对我们的面板（圆角 16）：** 系统这一族是 **Spotlight 15 / 我们 16 / 系统 HUD 18** ——
**同一档**。所以 16 不是拍脑袋的数，它落在 Apple 自己的区间里。
⭐ 而 **12pt 左内边距**这条可以直接抄 —— 它同时是 HIG 的明文值和我们量到的实测值。


### 10.2 控制中心（Control Centre）

**怎么叫出来**（不用鼠标点坐标）：

```applescript
tell application "System Events" to tell process "ControlCenter"
  click menu bar item 2 of menu bar 1        -- 第 2 项就是「控制中心」（26×22，在 x=1347）
end tell
```

⚠️ **它是 toggle** —— 连点两次会关掉。前几次我读不到窗口，就是因为多点了几下把它关上了：
`窗口数 = 0` 时**先点一次再读**，别以为「读不到 = 不是 AX 窗口」。

**逐元素读数**（开着时一次读全，`entire contents` 比递归好写）：

| 元素 | 尺寸 (pt) | 位置 | 备注 |
|---|---|---|---|
| 窗口（AX 报的） | **490 × 904** | (1032, 38) | ⚠️ 见下「一个对不上的地方」 |
| 连接类开关 ×3（Wi-Fi / 蓝牙 / AirDrop） | **170 × 41** | x=1146，y=60/100/140 | 两列，**列间距 10 pt** |
| 更高的开关 | **170 × 62** | (1326, 54) | |
| 小圆按钮 | **42 × 45** | (1344, 135) | |
| 大按钮 | **80 × 62** | (1416, 126) | |
| ⭐ **滑块卡片** | **350 × 62** | (1146, 198) | 控制中心里最小的一块**卡片** |
| 滑块本体 | **330 × 40** / **296 × 40** | x=1156 | 亮度 / 音量 |
| 卡片里的标签 | 42×15 / 37×15 | x=1156 | |
| 小图标 | 15×15 / 42×42 | | |
| 方块按钮 | 26×26 ×3 | x=1460 / 1434 | |

**算出来的两条干净的**：
- **列间距 = 10 pt**（第一列右缘 1316 → 第二列左缘 1326）
- ⭐ **卡片内边距 = 10 pt**（卡片 x=1146 → 里面滑块 x=1156）
  —— 和 Spotlight 的 **12pt** 同档，也再次落在 HIG 那句「带 bezel 元素四周约 12 pt」附近

⚠️ **一个对不上的地方，如实记下**：AX 报的窗口是 1032–1522（490 宽），
但内容只占 **1146–1496（350 宽）** → 左边距 114 / 右边距 26，**不对称**。
内容右缘距屏幕右缘 16pt，所以**可见面板**大概是从 ~1120 起，不是 1032。
→ **AX 那个窗口框可能含不可见的动画/外溢边距，它的左边缘不能直接用。**
（这条没法从截图证实 —— 见下。）

### 10.3 ⚠️ 控制中心的圆角**量不到**，原因本身有价值

用跟 Spotlight 同一套方法（已知坐标、不做差分）量它的左上角，结果：

- **亮度**：40pt 跨度内只在 0.20–0.27 之间抖，**没有台阶**
- **饱和度**：0.204 → 0.192，是**缓慢渐变**，不是跳变
- 拟合出来的「半径 5pt / 残差 10.24」是**噪声**，不能采信

⭐ **原因是控制中心的材质边是羽化/外溢的，不是硬边。** 对照：
**Spotlight 的边是硬的**（一跳 0.6 以上），所以那个量得出来；控制中心没有可检测的边界，
**从截图量不到圆角**。

→ 想知道这个值，只能改条件：把桌面壁纸换成**纯色**再量。
⚠️ 这跟「截图差分法不可用」是**两件事**：差分是被**动态背景**毁掉的，
圆角是被**羽化边**毁掉的。


---

## 11. ⚠️ 一条**会毁掉拖拽功能**的实现坑（2026-09-26，做第二批时挖到）

`[二手]` Michael Tsai，<https://mjtsai.com/blog/2024/01/10/mac-app-sandboxing-interferes-with-drag-drop/>，逐字：

> 「Merely inspecting the UTIs in the pasteboard is fine... But if you want to only react to
> some types of files or folders, you need to know more. **If you ask for the URL – even
> without actually using it – you trigger some behind the scenes activity involving app
> sandboxing. This prevents the file being made accessible to your app if & when it actually
> is dropped into your app.**」

→ **「悬停时判断这是不是 PDF/PPTX」正好踩这一条** —— 而那是任何拖拽区最自然的写法。

**规范做法**（`[一手]` AppKit 归档指南 + `NSDraggingInfo` 文档）：

| 要点 | 原文 / 说明 |
|---|---|
| 「收不收」由谁决定 | **`draggingEntered:` / `draggingUpdated:` 的返回值**，不是 `prepareForDragOperation:` |
| ⚠️ 返回 `NSDragOperationNone` 之后 | **仍会**收到 `draggingUpdated:` / `draggingExited:` —— 别以为返回 None 就清净了 |
| ⚠️ `performDragOperation:` | **默认返回 `false`** —— 忘了实现 = 静默不收 |
| 在哪查 pasteboard | **`draggingEntered:` 里**（只查一次）；别放 `draggingUpdated:`（会调多次） |
| ⚠️ 跨进程 | **必须用 `sender.draggingPasteboard`**，不能自己开 `NSPasteboard(name:)`（原文：「there is **NO guarantee** that this will be the pasteboard used」） |
| 文件类型 | **`NSPasteboardTypeFileURL`**；⚠️ `NSFilenamesPboardType` **已废弃**（10.14） |
| 读法 | `readObjects(forClasses:options:)` —— ⚠️ **`nil` 是错误、空数组是「没有」**，别写成一个判断 |
| 多文件 | `NSDraggingInfo.numberOfValidItemsForDrop`：只收一部分时**设成收的数量**，拖拽管理器会更新徽章 |

**另外两条待实测**（`[二手]`/`[未找到]`）：
- 非原生框架在 macOS 上「**窗口没有焦点时收不到 drop**」，而「native macOS apps do」。
  ⚠️ HIG 只明文写了「**源**可以从非活动窗口拖」，**接收侧一字未提** → **我们的浮动面板要实测**。
- **Apple 侧没有任何「drop 目标长什么样」的设计规范**（appcoda 原文：
  「**There's no recipe on how to do that however**」）；「虚线框」是 Web 惯例不是 Apple 规范。
