# P3 第二批：课程卡片面板

> 状态：**作者已拍板全部关键决定**（见 §0）。§2 是三份并行调研的结论，带出处。
> 前置阅读：`docs/PLAN-p3-prep.md`（第一批，已实现）、`docs/RESEARCH-macos-aesthetic.md`（视觉语言）。
> 日期 2026-09-26 · 起点 HEAD = `314e26a`

---

## 0. 作者本轮拍板的决定（逐条，别再问一遍）

| # | 问题 | 决定 |
|---|---|---|
| 1 | 面板怎么打开 | **双击 `.app` → 主面板**；但**先做面板、验证过再翻那一下双击** |
| 2 | 这一批做到哪 | **就做「课程卡片面板」**，不碰 P4（不做笔记/转录查看器） |
| 3 | 面板里能选课吗 | **能**，且**同时改 `.course`**（保持一份真源） |
| 4 | 「回头删词」怎么写回 | ⭐ **真删，写回文件**（换一条更窄的不变量，§3.3） |
| 5 | 上课中怎么再拖课件 | **菜单栏 🎧 菜单里加一项「开课前的准备…」** |
| 6 | `design-taste-frontend` | **临时开回来**（它被 `skillOverrides` 关了，是 12 个之一） |

⚠️ **第 1 条的「先验证再翻」是刻意的**：面板崩了**不能**导致录不了课。
翻之前的课上路径**一行不动**。

---

## 0.5 ⭐ 设计取向：美感与创新（作者 2026-09-26 重点要求）

**作者原话**：「面板设计跟 UI 部分**一定要着重考虑**，要保持项目这个**高级美观美感**。
可以**创新**一下看有没有新的思路。」

### 0.5.1 「高级感」在这个项目里 = 一套**已经量过**的具体东西，不是感觉

| 抓手 | 具体值 | 依据 |
|---|---|---|
| ⭐ **同心圆角** | 面板 16 → 卡片内边距 p → **卡片圆角 = 16 − p** | `[一手]` SwiftUI `concentric` 定义 + WWDC25/356 + HIG Harmony。**全篇最硬、最可检查** |
| ⭐ **零色相** | 整个面板**只用一个强调色**，其余全灰阶 | `[一手]` HIG › Panels 的 HUD 章逐字：「**Use color sparingly in HUDs.**」 |
| **字号只用表上的** | 13 正文 / 15 小标题 / 11 次要（**别再自造 18 / 12.5 这种**） | `[一手]` HIG 那 11 档，见 `RESEARCH-macos-aesthetic.md` §2.1 |
| **字距不加** | —— | 已实测证伪：系统字体自带（同文档 §2.1） |
| **文本色用系统色** | `labelColor` / `secondaryLabelColor`，不自定灰 | `[一手]` `NSVisualEffectView` 文档 |
| **对比度** | 文字对底色 **≥4.5:1**（小字 7:1） | `[一手]` HIG › Dark Mode。**可算 —— 是验收判据，不是形容词** |
| **不用 emoji 当图标** | 菜单栏那个已改（模板图）；面板内按钮同理走 SF Symbol 或纯文字 | `[一手]` HIG › Menu bar extras（black + clear） |

### 0.5.2 创新点（**每条都挂在调研的某个真空或反例上**，不是为创新而创新）

1. ⭐⭐ **卡片即拖拽目标** —— 「拖到**哪张卡** = **哪门课**」。
   省掉「先选课、再拖文件」两步，且天然满足 HIG 的「**一次只高亮一个**」（§2.2）。
2. ⭐⭐ **卡片显示「准备度」，不是「文件列表」** —— 这是「课程卡片」相对「一个文件夹」的**唯一真优势**：

   ```
   ECON10740  Exploring Economics
   ████████░░  8/10      课件齐 · 术语跑过 · 上次上课 3 天前
   ```

   普通文件管理器只能告诉你「7 个文件」—— **它不知道你「准备到哪一步了」**。
   而我们**恰好知道**：`materials/` 几个文件、`glossary` 几行、
   `prep-state.json` 跑过几次、`sessions/` 最后日期。**这些数据今天全都躺在磁盘上没人用。**
3. ⭐ **结果就在卡片里，不是一个弹窗** —— 调研最重要的负面结论是
   「**没有一个产品把「反复导入」做成常驻主界面**」（§2.5 E）。
   **这个真空就是差异化空间**：面板本身**既是入口又是结果页**。
4. ⭐ **材质做分层，不是一味压暗** —— `panel.py` 现在是「material + scrim 0.38」（把底压暗）。
   卡片可以做**反相的一层**：比面板底色**略亮**，形成「内容浮起来」。
   依据：`[一手]` HIG › Materials —— 材质的作用是让「**前景**（文字/控件）」与
   「**背景**（内容）」**视觉分离**；且「Thicker materials, which are more opaque,
   can provide better contrast」。
   ⚠️ **但别把材质盖死** —— HIG 那条「别在控件下面垫不透明底」要守。
5. **状态用「文字 + 一个点」，不用一排图标** —— 依据：`[实测]` Tahoe 的菜单图标被批评
   「**有些有有些没有，还对不齐**」；HIG 也要求图标只在必要时出现。
6. **拖拽反馈用中性色的 alpha，不用固定灰** —— 依据：`[实测]` ImageOptim 的
   `colorWithDeviceWhite:0 alpha: highlight ? 1/4 : 1/8`，**天然自适应深浅色**。

### 0.5.3 ⚠️ 两条**不能为了好看而破坏**的

- **可读性优先于观感**：4.5:1 是硬门槛，不是「看着差不多」。
- **别自造窗口 chrome**：`[一手]` HIG › Windows 逐字 —— 「**Avoid creating custom window UI**...
  don't try to replicate the system-provided appearance」。
  ⚠️ 我们那个**悬浮窗**已经有五条偏离（`RESEARCH-macos-aesthetic.md` §0），
  **那些是有理由的**（形态不同）；**新面板不该再添新的偏离**。

---

## 1. 步 0 —— 备份 + 文档治理（**压缩前必须做完**）

### 1.1 备份（作者要求）

**作者 2026-09-26 定：备份 = 推到 GitHub，要能完整回滚。**

```bash
git push origin main                    # 远端 = 还原点
git tag pre-entry-panel-20260926 && git push origin pre-entry-panel-20260926
```

⚠️ 两件事要记住：
- **推了就会到朋友手上**（`cl update` = `git pull`）。所以**这次推不是发版**：
  `VERSION` 仍是 3.7.0、**CHANGELOG 不写**、**不发 GitHub Release** —— 只是把还原点放上远端。
- **回滚 = `git reset --hard pre-entry-panel-20260926`**（那条 tag 就是完整快照）。
  要回滚时先在本地确认没有未提交改动（`git status --porcelain` 为空）。

### 1.2 落盘（⭐ 最急的一件）

| 写到哪 | 内容 | 状态 |
|---|---|---|
| `docs/PLAN-entry-panel.md`（**新建**） | **本文件** —— §2 三份调研 + §3/§4 方案与顺序。**调研不另开一份**（它是方案的依据，不是独立主题） | ✅ |
| `docs/PLAN-p3-prep.md` §10 | 改指针 + 标注「范围已升级成课程卡片面板」 | ✅ |
| `docs/HANDOFF-p3-prep.md` | 新增 §8：第二批入口在 `PLAN-entry-panel.md`；模型变化的代价 | ✅ |
| `CLAUDE.md` 文件地图 | 加一行指向 `docs/PLAN-entry-panel.md` | ✅ |
| `docs/RESEARCH-macos-aesthetic.md` | 补 §11：沙盒/提前读 URL 会毁掉拖放 + `NSDraggingDestination` 的坑 | ✅ |

### 1.3 开 `design-taste-frontend`

`~/.claude/settings.json` 的 `skillOverrides` 里把 `"design-taste-frontend": "off"` 去掉。
（那批关了 12 个；`code-review` / `deepsearch` / `pdf-converter` 等别动。）

---

## 2. ⭐ 三份调研的结论（只在我上下文里 —— 这是丢不起的部分）

三个代理并行跑的，都要求「挂来源 + 标档次」。**已自己回源核过的不再标**。

### 2.1 ⭐⭐ 最重要的一条：**沙盒 + 提前读 URL = 拖放坏掉**

`[二手]` Michael Tsai（mjtsai.com/blog/2024/01/10/mac-app-sandboxing-interferes-with-drag-drop/）逐字：

> 「Merely inspecting the UTIs in the pasteboard is fine... But if you want to only react to
> some types of files or folders, you need to know more. **If you ask for the URL – even
> without actually using it – you trigger some behind the scenes activity involving app
> sandboxing. This prevents the file being made accessible to your app if & when it actually
> is dropped into your app.**」

→ **「悬停时判断这是不是 PDF/PPTX」正是这个坑。** 我们最自然的写法恰是最可能踩的。
⚠️ **动手前必须先验这一条**（§4 步 0）。

### 2.2 Apple 官方对「拖拽接收方」的要求（`[一手]`，我回源读过 HIG JSON）

来源：`developer.apple.com/tutorials/data/design/human-interface-guidelines/drag-and-drop.json`

| 要求（逐字） | 落到我们面板 |
|---|---|
| 「**Show people whether a destination can accept dragged content.** ...display an insertion point **or highlight a containing view** only when the destination can accept a dragged item, and show **no visual feedback** — or an explicit "not allowed" image, like the `circle.slash` from SF Symbols — when it can't.」 | 悬停高亮**自己画**；拒收时用 `circle.slash` 或什么都不显示 |
| 「Display highlighting... only while the content is positioned above the destination, **removing the visual feedback when people drag the content away**.」 | ⚠️ **必须在 `draggingExited` 里撤掉** —— 归档指南把这个写成那个方法存在的理由 |
| 「When there are multiple possible destinations, **provide visual cues that help people identify one at a time**.」 | ⭐ **一次只高亮一张卡**（正好配我们的卡片） |
| 「**Consider changing the pointer appearance**... `drag link`, `disappearing item`, and `operation not allowed` pointers」 | 拒收光标 = **`NSCursor.operationNotAllowed`**（HIG › Pointing devices 有具名条目） |
| 「**Provide feedback when dropped content initiates a task or action.** ...show people that the task has begun and **keep them informed of its progress**.」 | 进度指示器是**明文要求，不是可选** |
| 「**Provide feedback when dropped content needs time to transfer.** ...you might **display a progress indicator**」 | 同上。Apple 自己的 file-promises sample 就是「**在接收视图里放 spinner**」 |
| 「**Prefer letting people undo a drag-and-drop operation.** ...**asking for confirmation before completing a drag-and-drop operation that can't be undone**」（举 Finder 拖进只写文件夹） | drop 后**立即开始**；只有**不可撤销**才确认 |
| 「**When people drop an item on an invalid destination, or when dropping fails, provide visual feedback.**」 | 失败**必须**有视觉反馈 |
| 「**Support multi-item drag and drop when it makes sense.**」+ macOS 段：「**Consider displaying a badge during multi-item drag operations.**...**If a destination can accept only a subset**, update the badge to show the new number.」 | 多选拖拽要有数字徽章；**「只收一部分」是官方预期场景** |
| 「**Offer alternative ways to accomplish drag-and-drop actions.**」（已在旧调研里） | 必须有「选择文件…」 |

**HIG › Alerts 逐字**（`[一手]`）—— 删东西用 undo 不用确认框：

> 「**Avoid displaying alerts for common, undoable actions, even when they're destructive.**」
> 理由：「A confirmation on an obvious action teaches people to dismiss confirmations
> reflexively, which is exactly what breaks the one that matters.」

配套 HIG › Undo and redo 有一条**容易漏的实现要求**：
> 「**Show the results of an undo or redo.**」—— 撤销后必须把恢复的那一项**滚回视野**，
> 否则用户以为没生效、反复撤。

### 2.3 `NSDraggingDestination` 的官方约定（`[一手]`，归档指南 + 现代文档）

**生命周期**（归档原文）：

> 「As the image is dragged into the destination's boundaries, the destination is sent a
> `draggingEntered:` message... While the image remains within the destination, a series of
> `draggingUpdated:` messages are sent. If the image is dragged out... `draggingExited:` is
> sent... **When the image is released, it either slides back to its source... or a
> `prepareForDragOperation:` message is sent**, depending on the value returned by the most
> recent invocation of `draggingEntered:` or `draggingUpdated:`.」

⚠️ **几条容易写错的**：

- **「收不收」由 `draggingEntered:` / `draggingUpdated:` 的返回值决定**，不是 `prepareForDragOperation`。
- ⚠️ **返回 `NSDragOperationNone` 之后**，**仍会**收到 `draggingUpdated:` 与 `draggingExited:` —— 别假设「返回 None 就清净了」。
- ⚠️ **`prepareForDragOperation:` 拒绝就断链**（返回 false）；`performDragOperation:` **默认返回 `false`** —— 忘了实现 = 静默不收。
- **在 `draggingEntered:` 里查 pasteboard**（只查一次），**别放 `draggingUpdated:`**（那个会调多次）。
- ⚠️ **跨进程拖拽必须用 `sender.draggingPasteboard`**，不能自己开 `NSPasteboard(name:)`（归档原文：跨进程时「**there is NO guarantee that this will be the pasteboard used**」）。
- **多个重叠的可接收视图：最上层那个收** → 我们的浮动面板叠在别的东西上时，谁收由层级决定。
- 多文件：`NSDraggingInfo.numberOfValidItemsForDrop` —— 只收一部分时**设成收的数量**，拖拽管理器会更新徽章。

**pasteboard 类型**（`[一手]`）：

- **`NSPasteboard.PasteboardType.fileURL`** —— 现代推荐（abstract 就一句「A file URL.」）
- ⚠️ **`NSFilenamesPboardType` 已废弃**（metadata `deprecatedAt: 10.14`）；Discussion 原文：「In macOS 10.6 and later, use `writeObjects(_:)` to write file URLs to the pasteboard.」
- **读法**：`readObjects(forClasses:options:)`，`classArray` 用 `[NSURL]`，options 用 `[.urlReadingFileURLsOnly: true]` + `.urlReadingContentsConformToTypes`（按 UTI 过滤）。
  ⚠️ 返回 **`nil` 是错误、空数组是「没有」** —— 别写成一个判断。
- **另一个可注册的类型**：`NSFilePromiseReceiver` —— Apple sample 说「**Handle file promises before handling URLs**」，因为 promise 一般是更高质量的那份。从 Finder 拖 PDF/PPTX 通常拿到 `.fileURL`，但**注册时一并注册 promise 类型**是官方 sample 的做法。

### 2.4 拖拽的视觉惯例（`[实测]`/`[二手]`）

- ⭐ **AppKit 不给任何自动高亮**。`[二手]` appcoda 原文：「**There's no recipe on how to do that however. It's up to you and your imagination**」。
- **「虚线框」是 Web 惯例，不是 Apple 规范** —— HIG 只说「insertion point **or** highlight」，没规定线型。
- **一个真实的开源实现**（`[二手|开源]` ImageOptim 的 `DragDropImageView.m`）：虚线圆角矩形 + 向下箭头图标；**高亮用透明度变化**（`colorWithDeviceWhite:0 alpha: highlight ? 1/4 : 1/8`），线宽 `MAX(2, size/32)`，虚线 `{size/10, size/16}`。
  ⭐ **用中性色的 alpha 而不是固定灰** —— 天然自适应深浅色。
- **深色背景上怎么画：Apple 侧 `[未找到]`。** HIG 四页全文搜过 `dark`/`contrast`，无条文。
  Web 侧有一次真实修 bug（`[二手|commit`）：虚线用 CSS 变量时夜间模式发光；**修法是写死中灰 `#9ca3af`**。
- **系统自己的反馈都加在「目标物」上**（Finder 的目标文件夹高亮、Dock 图标高亮、Safari 标签），**不是窗口边框** —— 「Finder 窗口内容区边框高亮」`[未找到证据]`。

### 2.5 同类产品：界面层（`[厂商]`/`[社区]`，8+ 产品样本）

**A. 拖拽区的空态与第二条路**

| 产品 | 主文案 | 第二条路 | 格式/上限写在哪 |
|---|---|---|---|
| AnkiDecks | `Drop a file here, or browse` | 同句 | 副文案：`PDFs · images · audio · video · and more · max 100 MB` |
| TypeWhisper | `Drag files into the drop area` | `or choose them from the file picker` | 不在区内 |
| LingQ | `Drag and drop files` | 无（先点按钮才出现） | 前一句：`(EPUB, PDF, DOCX, TXT, or MOBI)` |
| whisper-transcript | `Drag a video or audio file onto the box` | `(or click to browse)` | 仓库末尾 |
| WhisperWebUI | `Drop audio or video here` | 无 | 上方一行：`MP3, WAV, M4A, WEBM, MP4` |
| Uppy Dashboard | `Drop your files here` | **独立 browse 按钮** | — |

**三条互相独立的规律**：
1. **第二条路基本是同一句里的 `or ...`**，只有 Uppy 是独立按钮。
2. ⭐ **格式和上限写在区内的副文案里，是标配**（8 个里 6 个）。`[社区]` 三条独立源把它列为 drop zone 必填部件，并说**「只在被拒时才报出限制」是错误做法**。
3. **「拖拽区」不一定是常驻的**（LingQ 要先点按钮；MacWhisper 是「拖到 app 上」没有框）。

**hover**：`[社区]` Untitled UI 给了理由 —— 「那是用户得到的**唯一**确认『浏览器接受了这次拖拽、松手会有事发生』；**没有明显变化，人们会在犹豫中把文件丢到背后的页面上**」。状态表：`Drag over → Solid border; brand tint background`；⚠️ 明确列为坑：**`drag-over 和 error 同一个红色 → 反馈混淆`**。

**⭐ 一条被推翻的三方说法**：某三方博客说 Quizlet 导入支持拖拽 → `[一手]` Quizlet 官方帮助写的是「复制文本 → 粘贴到 Import 字段」，**没有拖拽**。→ **三方博客连流程都可能编。**

**B. 长任务的进度**

- ⭐ **Otter 是两段式**：上传期百分比 → 处理期再百分比。
- ⭐⭐ **Otter 那条最干净的产品级答案**（`[厂商]` 流程描述可信）：
  > 「**keep the Otter.ai app or browser window open while the file is uploading**. After the
  > upload is successfully completed, **you can safely close the app or browser**, as
  > transcript and AI summary processing continue on our Otter servers.」
  → **把长任务切成「上传期须在前台 / 处理期可关窗」两段。** 对我们直接可用
  （我们的瓶颈在「抽文本 + 调 LLM」，都在本地/网络，但同样可以切成「跑之前你得在 / 跑起来了可以走」）。
- **取消**：`[一手]` HIG 给了完整决策树 ——
  > 「If people can interrupt a process **without causing negative side effects**, include a **Cancel** button.」
  > 「If interrupting... **might cause negative side effects — such as losing the downloaded portion** — it can be useful to provide a **Pause** button in addition to a Cancel button.」
  > 「**Let people know when halting a process has a negative consequence.**」
  → ⚠️ **「取消后已经跑完的部分怎么办」在产品文档里是真空**（代理明确报告「没找到明文」）。

**C. 结果列表**

- **一个列表还是两个**：Anki 是「一次汇总 + 独立 log 页」；Raindrop 是「先给计数 → Review the summary → Start import」（**只给计数，无逐条列表**）；Apple Photos 是**按批次成组**（"Imports" 相簿）；LingQ 是**分色**（蓝=新/黄=LingQ'd/已知）。
- ⚠️ **Anki 的教训值得记**（`[论坛]`）：用户只加了 18 张新卡，Anki 报「18 added, **148 updated**」，而他没改过那 148 张。
  → **汇总数字本身可能是错的，而用户没有下钻的入口去核对。**
- **LingQ 的同类**：「Ling says my lesson has 1 new word but it does not!」→ **计数和列表对不上，用户就失去信任。**
- **删了要不要确认**：本轮**没找到词表类产品的明文**。只有 HIG 的取向（§2.2）。
- **40+ 条怎么防失控**（四条可抄，各有出处）：① 先给总数再看清单再动手（Raindrop） ② 按批次成组不铺平（Apple Photos） ③ 限量发放（Readwise Daily Review） ④ **失败项故意保留在列表里**（见下）

**D. 失败态**

- ⭐ `[社区]` Untitled UI：**「Failures should also be per file rather than per batch」**（6 张里坏 1 张不该把另外 5 张也丢掉）。
- ⭐ `[社区]` 两条措辞可直接抄：
  > 「**'Upload failed' is not a message.** 'File exceeds 10MB limit' and 'PDF, PNG or JPG only' are.」
  > 「'Upload failed' is useless; **'PNG and JPG up to 10 MB — this file is 14 MB'** tells the user exactly how to succeed.」
- ⭐ `[社区]` justfigma：**错误行要比进行中的行更高** —— 「on the row itself, **next to the file it happened to**, not in a banner at the top that scrolls away」。
- **B2B 派的做法**（`[厂商]` Plytix / HubSpot / MyEmma / Linnworks）：**把失败行导出成一份可下载的清单**。
  → 对我们：「没加的 20 个」给一份**可下载清单**，不只是面板里显示。

**E. ⭐ 常驻面板 —— 没有先例（最重要的负面结论）**

代理原话：

> 「我找到的所有产品里，**没有一个把「反复导入文件」做成常驻主界面的面板**。常驻的都是
> **零交互的文件夹监听**（MacWhisper Watch Folders / TypeWhisper Watch Folder），且**都在
> Settings 里**。手动导入的**全是**一次性向导（Raindrop / Day One / Anki）。
> 唯一接近的是 MacWhisper 的 **Batch 窗口** —— 但它是「一次拖一堆、跑完就关」，仍不是常驻面板。」

→ ⚠️ **我们做的这个东西在样本里没有直接同类。** 既是差异化空间，也意味着**在无参照地做决定**。

**反例（导入放进设置的）**：Notion（`Settings → Import`，**但同时给了 `/` 命令第二条入口**）、Day One（`File > Import`）、Anki（`File > Import`）、Apple Photos（`File > Import` + 拖到窗口/Dock）。
⚠️ `[论坛]` HN 对 Notion 的吐槽：「**duplication of options around the UI including Import**... which further leads to confusion about where to take what kind of action.」
→ **只放一处会难找；放两处又会混淆。** 我们已经有 `cl prep` + 菜单栏 + 双击三条入口了，**要收敛**。

### 2.6 拖拽的已知坑（`[二手]`/`[论坛]`，别人踩过的）

- ⭐ **不可发现是头号问题**。`[二手|NN/g]`：Mac 上「Mickey Mouse cursor」表示可拖、「closed glove」表示正在拖。`[二手]` 原文：「Ironically, not many people know about macOS' **'famously' undiscoverable** drag-and-drop interactions」。→ **「能拖」这件事必须显式写出来。**
- ⭐ **失败静默 = 用户判定「不支持」**。`[二手|2003]` Manton Reece（经典 Cocoa 文本拖拽）：「User tries a few more times, then gives up, thinking that **the app doesn't support dragging of text**.」
- `[论坛]` macOS 26 实测的两个真坑：**① 拖到文件夹「图标」上会 miss，必须拖到「名字」上**（热区画了但判定区不重合）；② 「**MacOS seems to switch between open windows and randomly select which one your files will land on**」。
- ⚠️ `[二手]` 非原生框架在 macOS 上「**窗口没有焦点时收不到 drop**」，而「native macOS apps do」。
  → ⭐ **对我们的浮动面板要实测确认：非 key 状态下能不能收拖拽。** HIG 只明文写了「源可以从非活动窗口拖」，**接收侧一字未提**。

### 2.7 明确没找到的（诚实清单）

| 想找 | 结论 |
|---|---|
| **拖拽区的像素级证据** | 代理**读不到图片**，只能读 alt 文字/文案/设计系统 spec。**「真实产品有没有虚线框」无法确证**，只有 4 条 `[社区]` 设计系统源说「dashed 是默认」 |
| **HIG 关于深色背景拖拽区的指引** | 四页 HIG JSON 全文读过，**无** |
| **Apple 明文规定虚线 vs 实线** | **无** |
| **Finder 窗口内容区边框高亮** | **未找到证据**（高亮都加在目标物上） |
| **接收方在非活动窗口能否收 drop** | **Apple 一字未提** |
| **「取消后已完成的部分怎么办」** | 产品级**零明文** |
| **「删词要不要确认」** | 词表类产品**零明文** |
| **StudyFetch / Cramberry 的界面** | **找不到**（前者只有营销话术，后者完全没命中） |
| **中文产品** | **一个字都没搜**（墨墨 / 欧路 / Anki 中文社区 / wolai / FlowUs） |

---

## 3. 设计：课程卡片面板

### 3.1 ⭐ 模型的变化（作者拍的，但要说清代价）

```
现在：  双击 .app → 立刻开麦录音
之后：  双击 .app → 课程卡片面板 → 点某张卡的「开始上课」→ 写 .course + 启动录音
```

**收益**：`.course` 从「看不见的状态文件」变成「面板上你选中的那张卡」—— **可见了**。
**代价**：上课前多一步；**面板挂了就录不了课**。
→ 所以作者选了「**先做面板、验证过再翻那一下双击**」（§0 第 1 条）。

### 3.2 卡片上放什么（全部从现有数据算出来，不新增存储）

```
ECON10740  Exploring Economics              ← 课名 = glossary 首行（translator.course_title 在读）
7 份课件 · 73 条术语 · 上次上课 9/25         ← materials/ 数文件 · glossary 数行 · sessions/ 取日期
[ 开始上课 ]  [ 准备课件 ]                    ← 两个动作
⭐ 这次加了 40 个词，另有 20 个没加  ▸        ← PrepResult，可展开
```

### 3.3 ⚠️ 删词：写入器的不变量要换（作者选了「真删」）

现在 `append_terms` 的硬不变量是「**只追加**」（`new.startswith(old)`，一条断言盖住课号/
教务词/首行/用户手写的一切）。**删词会打破它。**

**新的、更窄的不变量**（要写进 `prep.py` 的文件头 + 一条测试）：

> **除了被删的那几行，其余字节逐字不变。**
>
> 判据形状：逐行 diff —— `old` 的行序列与 `new` 的行序列，**除了被删的行之外完全一致**，
> 且**顺序不变**。

⚠️ 实现要求：
- 删的是**行**（按内容精确匹配），不是「按出现位置」—— 位置会因并发而漂
- 仍走 `_atomic_write`（复用 `build_notes._atomic_write`）
- **删除也要进墓碑**（`prep-state.json`）—— 否则重跑同一份课件会把它加回来（已有的机制）

### 3.4 ⭐ 卡片本身就是拖拽目标（我的补充，也是最值的一条）

**拖到哪张卡 = 哪门课** —— 省掉「先选课再拖文件」两步，同时天然满足 HIG 的
「**一次只高亮一个**」。

### 3.5 长任务：进度 + 可关

照 HIG（§2.2）+ Otter 的两段式（§2.5 B）：

```
[ 拖进来 / 选择文件… ]  PDF · PPTX
       ↓
抽文本 3/7  →  抽候选词 1/1  →  写入  →  生成释义
       ↑ 这一段「跑起来了」之后，关掉面板也继续（任务在进程里跑完）
[ 取消 ]
```

⚠️ **「取消后已跑完的部分怎么办」调研里是真空** → 我们定：**取消 = 停手，但已经追加进
glossary 的词留着**（因为那是「只追加」的语义，回滚反而危险）。**这条要写进方案让作者确认。**

### 3.6 结果列表

| 组 | 显示 | 能做什么 |
|---|---|---|
| **加了** N 个 | 逐条列出 | **删**（真删，写回文件；用 undo 不用确认框） |
| **没加** M 个 | 逐条列出 + **可下载清单** | 手动加进去 |
| **已在表里/你删过** K 个 | 折叠，只给数 | — |
| **失败的文件** | ⚠️ **逐文件标**（不是整批）+ **行内说原因** | 重试 |

⭐ 三条来自调研的硬要求：
1. **失败要逐文件**，不是整批（Untitled UI）
2. **错误行要在那一行上说原因**，不是顶部横幅（justfigma）
3. **计数必须和列表对得上**（Anki / LingQ 都是计数撒谎失去信任）

---

## 4. 实现顺序（翻双击之前全部完成并验证）

### 步 0 —— ⚠️ 先验两条，**任一条不成立就要改设计**

1. ⭐⭐ **沙盒/提前读 URL 会不会毁掉拖放**（§2.1）。做法：写一个最小 AppKit 脚本，
   在 `draggingEntered` 里**只判断 UTI、不读 URL**，另一次**读 URL**，看真拖进来时能不能拿到文件。
2. ⭐ **浮动面板在「不是 key window」时能不能收拖拽**（§2.6）。做法：面板不激活时从 Finder 拖一个文件上去。

**判据**：两次真拖拽都能拿到文件路径。做不到 → 拖拽降级为「只用 NSOpenPanel」，**HIG 要求的那条替代路径本来就必须有**。

### 步 1 —— `panel.py` 补三样（滚动 / 按钮 label / 拖拽视图）

⚠️ **滚动视图已经有第二份了**（`whatsnew._add_log_view` + `transcript_view`）——
新面板若再写第三份，就必须**抽进 `panel.py`**（「一个数字只有一个定义点」的同一条道理）。
**先决定**：抽 `whatsnew` 那份（NSTextView 型）进 `panel.py`，还是复用 `transcript_view` 的槽位池？

拖拽视图**必须走 `objc_own.own()`**（`objc_own.py` 的文件头规矩）。

### 步 2 —— 课程列表 + `paths.py` 扩

`paths.py` 现在有 `course_dir` / `materials_dir` / `prep_state`。要加：
`list_courses()`（列 `glossary/*.txt`）、`course_title(course)`（读首行，**复用 `translator.course_title`**）。

⚠️ 选课要**同时改 `.course`**（作者决定），而 `cl` 的 `course` 分支有一套**模糊匹配**
（`1077` → `ECON10770`，`cl:150-159`）—— ⚠️ **那套逻辑不能抄第二份**（抄了迟早漂移）。
→ 把它搬到一个 Python 定义点，`cl` 改调它。

### 步 3 —— 面板本体（卡片 + 拖拽 + 进度 + 结果）

照 `whatsnew.build()` 的返回形状（`{panel, close, set_status, ...}` + `on_update` 契约），
UI 回写一律 `AppHelper.callAfter` 回主线程。

位置逻辑**直接复用** `overlay._show_whatsnew_card()` 的四方向候选搜索（§已有代码）。

### 步 4 —— 删词（§3.3 的新写入器）+ 测试

### 步 5 —— 菜单栏 🎧 加「开课前的准备…」（§0 第 5 条）

### 步 6 —— **翻双击**（作者验证过面板之后才做）

改 `cl` 零参数分支 / `cl-bg.py`。⚠️ **必须留一条退路**：面板起不来时还能录课。

---

## 5. 闸门

| 什么时候 | 跑什么 |
|---|---|
| 任何改动 | `tests/test_audit_regressions.py` |
| 碰 `panel.py` / 面板构造 | `tests/test_panel.py`（**接法见它的第 ③ 组**：只比配方负责的 9 个键 + 限深结构） |
| 碰滚动 | `probe_scroll.py`（⚠️ 它自己不稳定，判读方法在 `CLAUDE.md`） |
| 碰 `prep.py` 写入器 | `tests/test_prep.py`（新增「除被删行外逐字不变」） |
| 端到端 | 真拖一份课件进面板，走完 |

---

## 6. 这一批**不做**

- ❌ 不做笔记/转录查看器（P4）
- ❌ 不做 watched folder（Zotero 官方拒绝过；旧调研已判）
- ❌ 不改 `main.py` / `translator.py` / `drain()` 的语义
- ❌ 不做常驻窗口（作者选了菜单栏入口）
