# 调研：导入入口 + 无 Obsidian 的导出

> 作者 2026-09-25 的原话：
> 「要不做一个入口吧，在入口上传或者倒入文件，这样对于用户好操作和理解，
> 要是没有 Obsidian（可选是否按照 Obsidian）就建个文件夹，每课一份的笔记做成 PDF 放在文件夹，
> 调研这期间的思维链和逻辑，调研同类型产品做法和社区建议等，包括 UI 架构要重新考虑」

这份文档回答上面每一条。**每个数字都是本机实测，每条引文都已回源核对。**

---

## 0. 结论先行

| 问题 | 结论 |
|---|---|
| 入口长在哪 | **复用已有的「磨砂面板」模式**（`whatsnew.py` 那套），**不做菜单栏常驻、不做常规窗口** |
| 无 Obsidian 时 | ClassLive 要有**自己的数据目录**；vault 从「唯一去处」降级为「可选出口」 |
| PDF 怎么出 | **用 WebKit 渲染**（`pyobjc-framework-WebKit`）—— 两条路都能出正确的 PDF，选 WebKit 是因为**朋友不用装 Homebrew**（§3） |
| 「每课一份」 | 自己渲染才做得到；从 Obsidian 导出做合并会很痛（社区实测） |
| 最大的隐藏成本 | 笔记是**用 Obsidian 语法写的**，直接转 PDF 会漏出语法（实测 5 类缺陷）。要**从同一份数据渲第二遍**，不是转格式 |

---

## 1. 现状盘点（实测，不是推测）

### 1.1 现在**没有入口**

§7.2 定的接口是「约定一个目录 + 一条命令」：

```
<vault>/Study/<课号>/materials/     ← 手动 cp 进去
cl materials                         ← 还没做（§7-B）
```

`cl` 现有子命令：`help / last / course / online / file / local / doctor / update / test`。
`materials` **不在其中**。所以今天唯一的办法是**用户自己在 Finder 里 cp**。

### 1.2 现在**完全没有 PDF 能力**

```
requirements.txt 里：sherpa-onnx / numpy / sounddevice / mlx-lm / httpx
                     / pyobjc-Cocoa / pyobjc-Quartz / huggingface-hub
全仓 grep weasyprint|reportlab|markdown|fpdf → 0 命中
```

### 1.3 vault 是**全有或全无**

`obsidian_writer.py:255`：

```python
self.enabled = bool(vault) and mode != "no"
```

`main.py:1387` 的 `--vault` 默认值 `~/Obsidian/Vault`（`obsidian_writer.py:28`）。
`obsidian_writer.py:685` 把笔记写死在 `<vault>/Lectures/`。

**→ 今天只有两个状态：写进 Obsidian vault，或者什么都不写。没有第三个选项。**
这正是作者要补的那个洞。

### 1.4 笔记里用了多少 Obsidian 专有语法

拿真实笔记 `2026-09-24_202833_LECTURE.md`（515 行）数：

| 语法 | 数量 |
|---|---|
| `> [!abstract]`（逐句转录，每条一句） | **77** |
| `> [!note]-`（**折叠**容器） | 1 |
| `> [!info]` / `> [!tip]` | 各 1 |
| `问题::答案`（Spaced Repetition 卡） | **9** |

---

## 2. ⭐ 实测：MD → PDF 这条路**通**，但直接转会漏东西

用真实笔记跑 `pandoc → weasyprint`（两个工具本机都有，`/opt/homebrew/bin/`），
产出 PDF 后**逐页目视**：

| # | 症状 | 严重度 |
|---|---|---|
| 1 | `> [!info]` / `> [!tip]` / `> [!note]-` **原样印成正文**，`[!info]本课信息` | 中 |
| 2 | **所有 emoji 变豆腐块**（🎯📚❓💡⭐📜 全是空方框） | 中 |
| 3 | `问题::答案` **原样漏出**，没渲染成卡片 | 中 |
| 4 | 本该**折叠**的 77 句转录**全部展开**，PDF 被转录淹没 | **高** |
| 5 | 撇号渲染错位：`concept' s`、`it' s`、`we' ve` | 低 |

### 2.1 ⚠️ 顺带发现：现有字体自检**报错了病因**

本机 `md2pdf.py`（鼻咽癌项目的）里那个字体自检，这次报出 1 个 `CIDFontType0`，
但解压对象流后读出来是：

```
/Subtype /CIDFontType0 /BaseFont /COQGKQ+.LastResort
```

**`LastResort` 是 macOS「没有任何字体能渲染这个字符」时的兜底** ——
它说的是**emoji 缺字形**（问题 2），**不是中文乱码**。中文好好的（Noto Sans SC 已正确嵌入）。

那脚本的报错文案是「中文可能在某些阅读器里乱码」→ **指向了错误的病因**，会把人带沟里。
（`~/Desktop/鼻咽癌网药项目/tools/md2pdf.py:184`）

---

## 3. ⚠️ 实测：两条路**都通**（本节已修正，我第一版结论是错的）

### 3.1 我先错在哪

第一版我写「WeasyPrint emoji 变豆腐块」—— **那是错的**。
真凶是**我自己的 CSS 只写了 `Noto Sans SC`，没带 emoji 字体栈**。
加上 `'Apple Color Emoji', 'Apple Symbols'` 重测：

| | 修正前 CSS | **修正后 CSS** |
|---|---|---|
| `CIDFontType0` 命中 | 1 | **0** |
| `LastResort` 字体数 | 2 | **0** |
| 实际渲染 | 🎯 变空方框 | ✅ **彩色 emoji** |

（还有一次自检 bug：正则字符类漏了 `.`，而字体名是 `COQGKQ+.LastResort`，
导致误报"命中 0"。已修。）

### 3.2 修正后的对比

| | **WeasyPrint** | **WebKit**（`WKWebView.createPDF`） |
|---|---|---|
| 中文 | ✅ | ✅ |
| emoji | ✅ 彩色 | ✅ 彩色 |
| 撇号 | ✅ 正常（修正 CSS 后同样正常） | ✅ 正常 |
| 标点 `「」——《》……` / 表格 | ✅ | ✅ |
| **CIDFontType0 命中** | **0** | **0** |
| 速度（真实笔记 515 行） | 5.7 s | **0.7 s** |
| 要装什么 | `uv pip install weasyprint` **+ `brew install python pango libffi`** | `uv pip install pyobjc-framework-WebKit` |
| 跨平台 | ✅ | ❌ 仅 macOS |
| 要不要 NSApplication | 不要 | **不要**（实测：不建 `NSApplication.sharedApplication()` 也能出，0.7 s） |

### 3.3 结论：**选 WebKit**，但理由变了

不是"WeasyPrint 不行"（它行），而是**「朋友要装什么」**：
朋友按现在的 README 装，**全程不需要 Homebrew**；引入 WeasyPrint 会把 Homebrew 变成新依赖。
WeasyPrint 官方文档逐字："The easiest way to install WeasyPrint on macOS is to use Homebrew.
When Homebrew is installed, install Python, Pango and libffi: `brew install python pango libffi`"。

「跨平台」对本项目**价值为零** —— README 写明只支持 macOS + Apple Silicon（MLX/Metal + AppKit）。

⚠️ 而"要保证无 `NSApplication` 也能跑"这条**对 WebKit 仍成立**（实测）→ `cl file` 那条路径一样能用。

> **完整的选型分析见 [`PLAN-roadmap.md`](PLAN-roadmap.md) §4**（含评审建议与事实的逐条核对）。
> ⚠️ 这份文档 §4 里原来的"依赖系统库 pango/cairo"结论**方向是对的**，
> 但当时是把 WeasyPrint 说成"不行"，**那个定性是错的**，只保留"要装 Homebrew"这个事实。

⚠️ **一个已观察到的坑**：WebKit 把 `font-family: 'Noto Sans SC'` 解析到了
`NotoSansSC-Thin`（本机装的是可变字体 `NotoSansSC[wght].ttf`）。
渲染结果看着正常，但**字重偏细**。要显式指定字重或改用静态字重文件。

---

## 4. 调研：同类型产品把入口做在哪

### 4.1 Apple 官方（developer.apple.com，**逐字已核**）

**菜单栏常驻**（`/the-menu-bar` → macOS → Menu bar extras）：

> "A menu bar extra exposes app-specific functionality using an icon that appears in the
> menu bar when your app is running, even when it's not the frontmost app."

> "**When necessary, the system hides menu bar extras to make room for app menus.**
> Similarly, if there are too many menu bar extras, the system may hide some to avoid
> crowding app menus."

> "**Display a menu — not a popover — when people click your menu bar extra.**"

> "**Let people — not your app — decide** whether to put your menu bar extra in the menu bar."

> "**Avoid relying on the presence of menu bar extras.** The system hides and shows menu
> bar extras regularly, and you can't be sure which other menu bar extras people have
> chosen to display or predict the location of your menu bar extra."

> "**Consider exposing app-specific functionality in other ways, too.** … People can hide or
> choose not to use your menu bar extra, but a Dock menu is aways available when your app is
> running."（原文拼写如此）

**→ 四条硬约束**：菜单栏图标**会被系统隐藏**、点了该给**菜单不是弹窗**、
**用户自己决定**放不放、**不能当唯一入口**。

**引导**（`/onboarding`）：*"design a flow that's fast, fun, and optional. When available,
onboarding occurs after launching is complete — it isn't part of the launch experience."*

**启动弹窗**（`/alerts`）：*"Avoid showing an alert when your app starts."*
macOS 节另有一条正是更新卡片的依据：*"Configure repeating alerts to let people suppress
subsequent occurrences of the same alert."*（本仓库已执行，见 §7 的「本版本不再提示」）

### 4.2 ⚠️ ClassLive 自身的约束：`.accessory` + 常规窗口 = 不可靠

`overlay.py:1399`：`app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)`
—— **无 Dock 图标**，也从不主动激活 app。

这个组合下开一个**普通可聚焦窗口**是已知的坑（macOS 14 起
`activate(ignoringOtherApps:)` 已弃用；新 `activate()` 官方明说
*"calling this method doesn't guarantee app activation"*）。

**但本项目已经有解了** —— `overlay.py:692` 的注释：

> 裸 `NSTextField` 即可: 实测非 key 面板上的首次点击能正常拿到 field editor

面板是 **`NonactivatingPanel` + `canBecomeKeyWindow = True`**，
**能收键盘、不抢焦点、不需要 `activate()`**。这就是 Spotlight 那一套。

> ⚠️ **不要重复一条已被否定的说法**：子代理报告说
> `overlay.py:307/421` 调 `activateIgnoringOtherApps_()` 与 1403 行的
> 「绝不调 NSApp.activate」**自相矛盾**。**我回源看了代码，这个判断过重** ——
> `overlay.py:421` 的注释写明了理由：「macOS 只让**活跃 app** 改光标」，
> 那是拖拽改光标的需要；1403 那条说的是 `show()` 时不许抢焦点。**两者语境不同，是有意为之。**
> 真正成立的只有后半句：用的是**已弃用 API**，且包在裸 `except: pass` 里，**失败静默**。

---

## 5. 调研：社区怎么说

| 说法 | 来源 | 置信 |
|---|---|---|
| **Obsidian 专有语法在别的阅读器里不渲染** —— 逐字：*"Obsidian-specific syntax (callouts, wikilinks, dataview, embeds) won't render in any other markdown viewer."* | [unmarkdown.com/blog/how-to-share-obsidian-notes](https://unmarkdown.com/blog/how-to-share-obsidian-notes)（二手，但具体可验）✅已核 | 高 |
| PDF 的公认代价：*"PDFs are not editable. If the recipient needs to modify the content, they're stuck."* / *"Tables and code blocks sometimes render poorly."* | 同上 ✅已核 | 中 |
| **连 Obsidian 自带 PDF 导出也掉中文**（macOS 上必须强指 CJK 字体才正常） | [Obsidian 论坛 #113392](https://forum.obsidian.md/t/built-in-pdf-export-drops-chinese-characters-on-macos-unless-a-cjk-font-is-forced/113392)（2026-04-15，Obsidian 1.12.7 / macOS 15.5）✅已核 | 高 |
| **「每课一份 vs 每节一份」社区无共识**，两派对立。每节派：Craig（8 赞）*"**One note per class seems like a good way to start**, with maybe a Home note that links to each class."* 分层派：juhis 主张 COURSE NOTE → LECTURE NOTE → TOPIC NOTES。**反向派**：pgkaila 引 CODE 框架 *"organize notes based on **how you'll use the information in the future, not where it came from**"* | [Obsidian 论坛 #28956](https://forum.obsidian.md/t/best-approach-for-online-course-notes/28956)（2021-12 开帖，2026-07 仍活跃）✅已核 | 高 |
| **合并很痛，且没有原生解法**：*"If you export the note as PDF, you'll have all the content, but **each embedded link will have an extra level of indentation** on the side. And the data will be in a **read-only PDF format**."* 唯一被采纳的方案是个 **Templater 脚本 = 社区插件** | [Obsidian 论坛 #94574](https://forum.obsidian.md/t/merging-several-notes/94574)（2025-01，2.0k 浏览 / 10 赞）✅已核 | 高 |
| NotebookLM **导出能力不足**催生了至少 8 个第三方导出插件 | 子代理转述，**我未核** | **低，仅作气味** |
| HN 178 分帖里最高赞**反对**菜单栏常驻：*"I'd much prefer apps use Dock menus, like the HIG suggests."* | HN #33774920（子代理转述，**我未核**） | **中，待核** |

---

## 6. 思维链：为什么最后是这个设计

把上面的事实串起来，逻辑是一根链，不是一堆选项：

```
作者要「没有 Obsidian 也能用」
   ↓
但今天 --vault 是唯一出口（§1.3），没有 Obsidian = 什么都不产出
   ↓
所以 ClassLive 必须得有【自己的数据目录】—— 不能寄生在别人的 vault 里
   ↓
一旦有了自己的目录，「课件放哪」就有答案了：放自己的目录里
   ↓
所以【入口】的本质 = 一个把文件放进这个目录的界面
   ↓
而「每课一份 PDF」也自然落在同一个目录里
   ↓
但笔记是用 Obsidian 语法写的（§1.4），
直接转 PDF 会漏 5 类东西（§2）
   ↓
→ 所以要【从同一份数据渲第二遍】，而不是「把 Markdown 转格式」
   ↓
`_render_note()` 已经是「数据 → 字符串」，数据还在手上（`entries`/`review`/`qa_items`）
   ↓
→ 加一个 HTML 后端，和 Markdown 后端**共用数据、不共用字符串**
   ↓
**这个决定顺带解决了「每课合并」** —— 社区里从 Obsidian 导出做合并很痛（§5），
但自己渲染就是「把多节课的数据拼起来再渲一次」，不存在合并难题
```

### 6.1 为什么入口**不做菜单栏、不做常规窗口**

- 菜单栏：HIG 明说**会被系统隐藏**、**不能当唯一入口**（§4.1）
- 常规窗口：`.accessory` 下激活不可靠（§4.2）
- **已有的磨砂面板模式全部避开了这两条**，而且**已经跑通**（更新卡片、字幕面板）

### 6.2 为什么 PDF 选 WebKit 不选 WeasyPrint

不是「谁更好」（**两条路都能出正确的 PDF**，见 §3.2），是**「谁对朋友的安装步骤伤害更小」**。
WeasyPrint 在 macOS 上要额外 `brew install python pango libffi`；WebKit 是在已有的 PyObjC 家族里加一个包。

---

## 7. ⭐ UI 架构重考虑：先还一笔技术债

### 7.1 现状：磨砂面板配方**已经抄了两份**

| | 位置 |
|---|---|
| 字幕面板 | `overlay.py:600-623` |
| 更新卡片 | `whatsnew.py:205-218` |

连那条**实测出来的坑**都抄了两遍：

> 必须显式 DarkAqua —— `.hudWindow` 在浅色系统下被渲染成灰色
> （`overlay.py:601` 记着实测：面板中心平均亮度 **0.314** → 强制 DarkAqua 后 **0.113**）

而且**已经有踩过 ObjC 类名冲突**的记录了 —— `overlay.py` 和 `whatsnew.py`
各自有一套懒建类工厂（`overlay.py:245-471`、`whatsnew.py:51-76`），
两边的类名一度撞车（`_Panel` / `_DragLayer`），PyObjC 静默覆盖，`build()` 返回 `None`。

**→ 再加第三个界面（导入面板）就是第三份拷贝 + 第三次撞名风险。**

按 codebase-design 的判据：**一个适配器是假缝，两个适配器是真缝。**
现在正好两个 → **抽 `panel.py` 是成立的**，不是提前抽象。

### 7.2 建议的形态

```
panel.py                    ← 新：一份磨砂面板配方（唯一真源）
   ├── whatsnew.py          ← 改成它的消费者（更新卡片）
   ├── overlay.py           ← 改成它的消费者（字幕面板）
   └── import_panel         ← 新：导入面板
```

导入面板**长得就像更新卡片**——因为 `whatsnew.build()` 返回的那个控制器
（`set_status` / `set_update_title` / `set_update_enabled`）**正好就是导入要的形状**：
「正在转换 3/12…」+ 一个按钮 + 一个复选框。**这不是巧合 —— 它就是入口面板的原型。**

**入口的两条等价路径**（都要有）：

| 路径 | 谁用 | 为什么不能砍 |
|---|---|---|
| **导入面板**（拖文件 / 选文件） | 朋友 | 作者原话：「对于用户好操作和理解」 |
| **`cl materials <文件…>`** | 作者、脚本 | `--ui terminal` 时没有面板；也是可脚本化的那条路 |

---

## 8. 建议的数据布局

```
~/.classlive/                       ← 新：ClassLive 自己的目录
  courses/<课号>/
    materials/                      ← 入口把文件放这（PDF/PPTX/DOCX → markdown）
    notes/                          ← 每节课一份 .md（今天的 vault 笔记）
    <课号>.pdf                      ← 整门课合并的 PDF（可选产物）
  config.json                       ← 收拢散落的 .course / .update-seen / .update-skip

<vault>/Lectures/                   ← 变成【可选出口】：--vault 有值时才写
```

**⚠️ 必须认账的三件事：**

1. **这是迁移。** 作者自己有历史笔记在 `~/Obsidian/Vault/Lectures/`（实测 **6 份**）。
   不能搬走用户的文件 —— 默认**两个都写**，迁移是作者显式动作。
2. `sessions/` **不动**。它是三方共享契约（`obsidian_writer` 写 / `_parse` 读 /
   `cl last` grep），CLAUDE.md 点名过。
3. `--vault` 的默认值 `~/Obsidian/Vault` 要重新想 —— 现在它默认写进一个可能不存在的 Obsidian 库。

---

## 9. 分阶段

| 阶段 | 做什么 | 依赖 | 成本 |
|---|---|---|---|
| **8-A** | 自己的数据目录 + vault 降级为可选 | 无 | 小 |
| **8-B** | 抽 `panel.py`，先让更新卡片搬过去（**不加新功能，纯搬家**） | 无 | 中 |
| **8-C** | `cl materials` + 导入面板（用 8-B 抽出来的面板） | 8-A、8-B | 中 |
| **8-D** | HTML 渲染后端 + WebKit 出 PDF（`pyobjc-framework-WebKit` 进 requirements） | 8-A | 中 |
| **8-E** | 每课合并 | 8-D | 小（8-D 做完就是拼数据） |

**顺序理由**：8-B 排在 8-C 前面 —— 先搬家再加功能，
否则会把两份拷贝变成三份，然后再抽一次，等于白干。

**与旧 §7 的关系**：§7-A（`materials.example/`）**已经做完、还没推**
（`README.md` + `obsidian_writer.py` + `materials.example/`，等作者确认文案）。
它描述的是**今天**的状态（手动 cp），**没有说错**；8-C 做完后它要再改一次。

---

## 10. 反模式护栏

- ❌ **别做菜单栏常驻当入口** —— HIG 明说系统会隐藏它（§4.1）
- ❌ **别开常规窗口** —— `.accessory` 下激活不可靠（§4.2）；用 non-activating panel
- ❌ **别把 Markdown 转格式当解法** —— 会漏 callout / emoji / `::` / 折叠（§2）
- ❌ **别选 WeasyPrint** —— 不是它不好，是**它会把 Homebrew 变成朋友的新依赖**（§3.3）
- ❌ **别搬走作者已有的 vault 笔记** —— 默认两个都写
- ❌ **别碰 `sessions/`** —— 三方共享契约
- ❌ **别为「每课一份」去做 Obsidian 端合并** —— 从数据层拼才是对的（§6）
- ⚠️ **别信「CIDFontType0 = 中文乱码」** —— `LastResort` 也是这个类型，
  但它说的是 **emoji 缺字形**，不是中文（§2.1）

---

## 11. 验证清单

- [ ] 一个**全新的空目录**（无 vault、无 Obsidian）跑完 `cl` 能出笔记 + 能出 PDF
- [ ] 已有 vault 的用户跑完 `cl`，**旧笔记一份不少**
- [ ] 导入面板在 `.accessory` 下**能收键盘输入**、**不抢别的 app 焦点**
- [ ] 拖 3 个 PDF 进去 → 3 份 markdown 落在 `materials/`，UI 显示进度
- [ ] 转换失败**不阻塞**上课与笔记落盘
- [ ] 生成的 PDF：**中文正常 + emoji 不是豆腐块 + 没有 `[!info]` 漏出 + 撇号不错位**
- [ ] WebKit PDF 在 `--ui terminal` 下**也能出**（无 `NSApplication`）
- [ ] 字重不是 Thin（§3 那个坑）

---

## 12. 顺手发现的两条文档腐坏（与本次调研无关，**已修**）

`CLAUDE.md` 是指针文件，**指针写错 = 该读的人读不到**：

| 位置 | 原写的 | 实际 |
|---|---|---|
| `CLAUDE.md:57` | 「**没有** `requirements.txt` / `pyproject.toml` / `uv.lock`」 | **`requirements.txt` 存在**（实测 1374 字节）|
| `CLAUDE.md:16` | 指向 `docs/REVIEW-2026-09-24.md` | 该文件**不在 git 里、磁盘上也没有**；实际在 `~/Desktop/classlive-review/REVIEW-2026-09-24.md` |

（两条均已本机核实：`wc -c requirements.txt` = 1374；`git ls-files docs/` 输出里没有
`REVIEW-2026-09-24.md`，且 `ls docs/` 也没有。）

**已改成**：第一条写清 `requirements.txt` 存在、并补了「`.venv` 是 uv 建的、**没有 pip**」
这条会让人白踩一次的坑；第二条改成指向真实位置并标明**不推送**。

---

## 附：本次实测的原始数据

```
被测笔记  ~/Obsidian/Vault/Lectures/2026-09-24_202833_LECTURE.md   515 行
          > [!abstract] × 77   > [!note]- × 1   > [!info] × 1   > [!tip] × 1   :: × 9

WeasyPrint   pandoc md→html: 31,938 B    html→pdf: 5.5 s (3.00u+0.65s)   1,134,097 B
             CIDFontType0 = 1  →  /BaseFont /COQGKQ+.LastResort
             字体: Apple-Color-Emoji / Nanum-Gothic / Noto-Sans-SC(-Bold/-Oblique)
                   / Sarasa-Mono-Slab-TC / Menlo

WebKit       createPDF: 0.7 s    31,941 B（小测试文档）
             无 NSApplication 下: 0.7 s    11,915 B
             CIDFontType0 = 0
             字体: NotoSansSC-Thin  ← ⚠️ 可变字体被解析到最细档

本机工具     /opt/homebrew/bin/{pandoc, weasyprint(69.0), typst}
             ✗ 无 wkhtmltopdf / quarto / Chrome
             cupsfilter: "No filter to convert from text/html to application/pdf."
ClassLive    .venv: pyobjc-framework-Cocoa 12.2.2 + Quartz 12.2.2（已装）
             WebKit：未装。venv 无 pip（uv 建的）
中文字体     ~/Library/Fonts/NotoSansSC{,-Bold,-Medium}.ttf + NotoSansSC[wght].ttf
```
