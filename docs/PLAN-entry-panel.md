# P3 第二批：课程卡片面板

> ⭐ **本批已经在 2026-09-26 当天做完了**（六步全齐、闸门全绿、**但一次都没推**）。
> **新会话先读 `docs/HANDOFF-entry-panel.md`** —— 那份讲「还剩什么、坑在哪、第一步做什么」。
> 本文件现在是**设计与依据的存档**（§2 三份调研 / §7 批量分类方案 / §8 审查遗留清单）。
>
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
   ⭐ **有先例**（2026-09-26 补调研）：NoteExpress 官方 wiki 逐字「或者**直接将全文文件
   或者文件夹拖入目标文件夹**」—— 同一个形状。**这不是我们硬编的交互**（§2.5 E 更正段）。
2. ⭐⭐ **卡片显示「准备度」，不是「文件列表」** —— 这是「课程卡片」相对「一个文件夹」的**唯一真优势**：

   ```
   ECON10740  Exploring Economics
   ████████░░  8/10      课件齐 · 术语跑过 · 上次上课 3 天前
   ```

   普通文件管理器只能告诉你「7 个文件」—— **它不知道你「准备到哪一步了」**。
   而我们**恰好知道**：`materials/` 几个文件、`glossary` 几行、
   `prep-state.json` 跑过几次、`sessions/` 最后日期。**这些数据今天全都躺在磁盘上没人用。**
3. ⭐ **结果就在卡片里，不是一个弹窗** —— ⚠️ **原来的理由是错的，这里更正**：
   我原来写「调研最重要的负面结论是『**没有一个产品**把反复导入做成常驻主界面』」——
   **那句说大了**（分母只有 8+ 个英文产品，漏了「文献管理」一族，见 §2.5 E 更正段）。
   正确的真空是**更窄、但也更实**的一条：**没有一个把「导入结果 + 回头删」做进主界面**
   （所有产品都只给一次性汇总，或不给逐条列表）。
   → **差异化空间在这里**：面板本身**既是入口又是结果页**。
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

> ⚠️ **2026-09-26 实测：本项目的 `.app` 根本不沙盒** ——
> `codesign -d --entitlements - ClassLive.app` **只回 Executable 一行**（无 entitlements），
> `codesign -dv` 显示 `Signature=adhoc` / `linker-signed` / `TeamIdentifier=not set`。
> 而下面那条坑的前提逐字是 **「activity involving app sandboxing」**。
> → **所以这条大概率不适用**，它从「可能推翻设计」降级为「顺手验一下」。
> 但**别据此删掉 §4 步 0 的探针**：探针同时回答 §2.6 那条（非 key 窗口能不能收），
> 而那条**与沙盒无关**，且 Apple 一字未提。
> ⚠️ 另有一个将来会变的点：**一旦上架 App Store / 开沙盒，这条立刻回来。**

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

#### ⚠️⚠️ 2026-09-26 补中文样本后：上面那句**说大了**，这里更正

英文那轮的「**没有一个**产品把反复导入做成常驻主界面」—— **分母是当时抽到的那 8+ 个产品，
而「文献管理」这一族根本没被抽到。** 补搜后：

| 产品 | 实际流程（原文） | 出处 | 档 |
|---|---|---|---|
| ⭐ **NoteExpress**（国产） | 主窗口 = 题录列表 + 文件夹树。右键文件夹→【导入文件】，**「或者直接将全文文件或者文件夹拖入目标文件夹」** | inoteexpress 官方 wiki | `[厂商]` |
| **小绿鲸**（国产 SCI 阅读器） | 「文献导入（**支持批量导入和拖拽**）」，按钮叫【批量导入文献】 | 知乎使用手册 | `[评测]` |
| **Cubox** | 主界面**就是**卡片列表、可拖拽卡片；但导入是浏览器剪藏、**不是文件导入** | help.cubox.pro | `[厂商]` |

**正确的说法是**：常驻「反复导入」的主界面**有先例**，在**文献管理**这一族；
但**没有一个把它做成浮层面板**，**也没有一个把「导入结果 + 回头删」做进主界面**。

⭐ **这条纠正反而给出了一样更有用的东西**：
**NoteExpress 的「拖入目标文件夹」= 我们的「拖到哪张卡 = 哪门课」** —— 同一个形状，
有业界先例，不是我们硬编的。**§3.4 那条因此从「我的补充」升级为「有先例的交互」。**

⚠️ 但**形态差异是实的**：那些全是**桌面大窗口**（常驻主界面），
我们是**压在别人内容上的浮层**。**「浮层 + 常驻导入」无先例，这条仍然成立。**

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
- ~~**删除也要进墓碑**（`prep-state.json`）~~ ⚠️ **这句是错的，2026-09-26 实现时纠正**：
  墓碑 `appended` 记的是「prep **曾经加过**哪些词」，而 `prepare()` 里
  `if k in have or k in tomb: skipped`（`prep.py:796`）**已经在跳过墓碑里的词**。
  删掉术语表里那一行**不影响** `appended` → **重跑照样不会复活**。
  → **删除只写术语表一个文件**；多写一次 `prep-state.json` 是白加一条写路径与失败面。

✅ **步 4 已实现**（2026-09-26）：`prep.remove_terms()` + `prep.removed_lines()`。
`removed_lines` 是那条不变量的**可执行定义**（子序列判定），**实现与测试共用同一份** ——
别在测试里另写一份判据，那会漂移，而漂移之后两边都「通过」。
⚠️ **写盘前有闸门**：`removed_lines(old, new) is None` 就抛错**拒绝写入** ——
让不变量**承重**，不是写在注释里（实测把它改成 `if False` 会让两条断言变红）。
测试在 `tests/test_prep.py` 尾部（56 → 84 条）。

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

#### ⚠️⚠️ 第 4 条（2026-09-26 中文补调研挖到，**这条能防一次数据事故**）

**undo 必须把删掉的那几行放回原位，不是「追加到末尾」。**

依据是 NoteExpress 的两条真实论坛事故帖（`[社区]`）：

1. 误删的题录**在回收站里找不到**（回收站不完整）；
2. ⭐ **从回收站恢复到原文件夹，导致原文件夹所有题录全部消失**（原帖：
   「辛辛苦苦收集了几个月的题录，灰飞烟灭」）。

→ **「删」和「撤销」是一对，写错任何一边都会丢真实数据。** 落实成两条：
- 删除时**记下删的是哪几行**（内容 + 位置），undo 按原位置**插回去**
  —— 不是 `append`（追加会把顺序打乱，而 `glossary/<课号>.txt` 的顺序是**有意义的**）
- undo 之后**必须把那一行滚回视野**（HIG › Undo and redo 逐字：「**Show the results
  of an undo or redo.**」）—— 否则用户以为没生效、反复撤

⭐ 顺带：**「删了给撤销、不给确认框」在中文产品里也有真先例** —— 滴答清单官方手册写
「若误删任务，可以点击弹出的提示中【撤销】按钮来恢复任务」（`[社区]`，官方页是 SPA 没读到原文）。
⚠️ 但**同族里也有「有确认框」的**（欧路词典）和**「根本不让删」的**（搜狗输入法官宣
「云端用户词库**不支持**清空删除或修改管理」）→ **不存在统一的行业做法。**
所以别拿「大家都这么做」当论据，**只有「有人这么做」**。

---

## 4. 实现顺序（翻双击之前全部完成并验证）

### 步 0 —— ⚠️ 先验两条，**任一条不成立就要改设计**

1. ⭐⭐ **沙盒/提前读 URL 会不会毁掉拖放**（§2.1）。做法：写一个最小 AppKit 脚本，
   在 `draggingEntered` 里**只判断 UTI、不读 URL**，另一次**读 URL**，看真拖进来时能不能拿到文件。
2. ⭐ **浮动面板在「不是 key window」时能不能收拖拽**（§2.6）。做法：面板不激活时从 Finder 拖一个文件上去。

**判据**：两次真拖拽都能拿到文件路径。做不到 → 拖拽降级为「只用 NSOpenPanel」，**HIG 要求的那条替代路径本来就必须有**。

#### ✅ 步 0 结果（2026-09-26 实测，作者手拖）

| 条 | 结果 | 证据 |
|---|---|---|
| **2. 非 key 浮动面板能不能收拖拽** | ✅ **能** | `probe_drag.py`：两次落点都拿到真路径，且日志里两次都是 **`key=False active=False`** |
| **1. 读 URL 会不会毁掉拖放** | ✅ **不会**（在不沙盒的前提下） | `PROBE_MODE=url`：**五次落点全成功**，且每次 `draggingEntered` 读到的 URL 与 `performDragOperation` 拿到的**逐字相同** |

**⭐ 步 0 结论：整条设计成立，不需要降级到「只用 NSOpenPanel」。**

实测日志（v2 探针，整块面板做落点）：

```
【鼠标按下到了面板】—— 说明输入路由是通的
★ draggingEntered  key=False active=False
    types = ['public.file-url', 'CorePasteboardFlavorType 0x6675726C', …,
             'NSFilenamesPboardType', 'Apple URL pasteboard type', 'com.apple.finder.node']
★★★ 落点！ performDragOperation -> 1 个：/private/tmp/classlive-dragtest/week5.pptx
```

`PROBE_MODE=url` 那次（作者拖的是**真课件**，不只是我的测试文件）：

```
★ draggingEntered  key=False active=False mode=url
    ★ 读 URL -> 1 个：/Users/owen/Downloads/Intro 2026 Theme 2 Sociology - Copy (1).pdf
★★★ 落点！ performDragOperation -> 1 个：/Users/owen/Downloads/Intro 2026 Theme 2 Sociology - Copy (1).pdf
```

→ ⭐ **两侧 URL 逐字相同，5/5。** 「悬停时读 URL 会毁掉这次拖拽」在不沙盒的 app 上**测不到**。

→ ⭐ **§3.4「卡片即拖拽目标」成立，设计不用降级。**

**三条副产物：**

1. ⚠️ **`NSFilenamesPboardType` Finder 今天还在提供**（那个 10.14 就废弃的类型）。
   我们注册 `public.file-url` 是对的，别回头去兼容废弃类型。
2. ⚠️ **`probe_drag.py` 的 v1 是坏量具，v2 才是对的。** v1 用三个小格子，
   「没反应」和「瞄偏了」分不清；v2 把整块面板做成落点 + **收到就变绿**，
   结果一眼可读。**探针的判读成本本身要设计** —— v1 害我白跑两轮。
3. ⚠️⚠️ **我自己的合成拖拽测不出这件事。** `CGEventPost` 能移动真实光标
   （实测光标从 (450,255) 到 (1100,700)），但**起不了 Finder 的 drag session** ——
   拖拽中途 `NSDragPboard.types()` 是空的、`changeCount == 0`。
   **所以「拖拽类」的验收，量具自检是必须的一步**，不能因为「事件送出去了」就信。
4. ⚠️ **面板会被手带偏。** 两次实测窗口都从代码设的位置漂走了
   （v1: (436,120)→(664,156)；v2: (396,452)→(421,226)）。`movableByWindowBackground_(True)`
   下按到空白处就会拖窗口。→ **真实面板的卡片必须盖满可拖区域**，
   让「想拖文件」和「想挪面板」不会撞在同一片像素上。

### 步 1 —— `panel.py` 补三样（滚动 / 按钮 label / 拖拽视图）

⭐ **「抽哪一份」已查清（2026-09-26 实测对比）** —— 结论是**两份都不抽，只抽那 4 行约定**。

逐行比对两个现存 NSScrollView（`whatsnew._add_log_view` `w:113-119`
vs `transcript_view` `t:125-132`）：

| 配置 | whatsnew | transcript_view |
|---|---|---|
| `setDrawsBackground_(False)` | ✅ | ✅（contentView 也设） |
| `setBorderType_(0)` | ✅ | ❌ **没设** |
| `setHasVerticalScroller_(True)` | ✅ | ✅ |
| `setAutohidesScrollers_(True)` | ✅ | ✅ |
| `setScrollerStyle_(Overlay)` | ✅ | ❌ **没设** |
| `setHorizontalScrollElasticity_(0)` | ✅ | ✅ |
| `setVerticalScrollElasticity_` | ❌ | ✅ **且之后会改**（构造 `0` → `t:292` 改 `Automatic`） |

→ ⚠️ **「两份拷贝」这个前提不成立**：只有 4 项重合，且其中一项在 transcript_view 里
**是动态行为不是静态约定**。真正的「拷贝」只有那 4 行。

**所以：**
- ❌ **不抽 `whatsnew` 那份进 `panel.py`** —— 它的价值在 NSTextView 那套文本管道
  （`setVerticallyResizable_` / `textContainer` 宽度跟随 / `textStorage`），
  **卡片列表根本不用 NSTextView**。搬过来是白带的复杂度。
- ❌ **不复用 `transcript_view` 的槽位池** —— 那套绑死在「**所有行等高** → 滚动偏移
  到行索引 O(1) 除法」上（`t:15-21` 那段），而卡片**必然不等高**（展开态不同）。
  硬套等于把它整个回收方案连同前提一起搬进来。
- ✅ **抽的只有 4 行**：`drawsBackground_(False)` + `borderType_(0)` +
  `autohidesScrollers_(True)` + `setScrollerStyle_(Overlay)` →
  `panel.make_scroll_view(rect)`。理由是这 4 行是**带原因的约定**
  （面板是 vibrancy，不能垫不透明底；overlay 风格是本 app 的既有观感），
  第三个调用方各写一遍迟早漂移。

⭐ **一个实测数据（决定要不要滚动）**：本机今天 `glossary/` 有 **5 门课**
（ECON10730 / 10740 / 10770 / 10790 / SOC10020，30–47 行术语表）。
→ 5 张卡一次放得下，**滚动在日常路径上根本不出现**。
但**必须留好第 6 门课不会引发断崖**：走滚动视图（行为统一），
面板高度按卡数长到上限为止。**别做「≤5 静态 / >5 才滚」的两套代码路径。**

⚠️ **超出的课程怎么办**：`glossary/` 是**唯一真源**（`cl course` 也是列它），
但面板**还该有一个「新建课程」入口** —— 否则新学期的课没法从面板里建。
（这条**方案里原来没有**，是数完 5 门课才发现的缺口。）

拖拽视图**必须走 `objc_own.own()`**（`objc_own.py` 的文件头规矩），
且**类名不能自己起** —— 那是 objc_own 存在的理由。

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

---

## 7. ⭐ 批量分类（作者 2026-09-26 提的第二套交互）

**作者原话大意**：拖**一堆**文件到面板空白处 → 自动判断每份归哪门课 → 落到那门课术语表。
**作者已认可的形状**：`本地先分 → 分不出的才问模型 → ⚠️ 先把映射摆给他看，他点头才写`。

⚠️ **本节是调研结论 + 设计，尚未实现。** 两份补调研 + 一条我自己的实测。

### 7.1 ⚠️ 先纠我自己一句错话

我一度说「4/5 个文件不用问模型」——**那句是错的**。我拿的是碰巧在上下文里见过的 5 个文件名，
**分母一换结论就翻**。真扫 `~/Downloads` 的 **468 份**候选文件（`/tmp/classify_probe.py`，未入库）：

```
唯一命中 7   ·   打平→拒绝 1   ·   一点线索都没有 460
                                    本地只能解决 7/468 = 1%
```

⭐ **根因不是「文件名不够好」，是四门 ECON 课的课名几乎是同一句话**：

| 课 | 有效词 |
|---|---|
| ECON10730 | econ, **economists** |
| ECON10740 | econ, **economics**, exploring |
| ECON10770 | econ, **economics** |
| ECON10790 | econ, **economists**, **mathematics** |
| SOC10020 | **sociology** ← 独一份 |

**`econ` 四门共有** → 任何带 economics 的文件同时给几门打分 → 打平 → 拒绝。
实测那条：`Linear functions in MICROECONOMICS.pdf → [(2,ECON10770), (2,ECON10740), (1,ECON10790)]`。

→ **本地匹配只能吃掉代号有独有词的课（SOC）；ECON 那四门必须看内容。**

### 7.2 ⭐ 调研：Jev 是什么（`[官方]`，与仓库旧结论**不同**）

| 项 | 值 |
|---|---|
| 身份 | TypeSafe AI 的 System One，`jev-1.13.0`（2026-09-15） |
| ⭐ 本质 | **不生成文本**。只答**类型化问题**，返回**概率** |
| 端点 | `POST https://api.commandcode.ai/provider/v1/systemone` —— ⚠️ **不是 `/chat/completions`** |
| 认证 | `Authorization: Bearer <CMD_API_KEY>` |
| 请求体 | TypeSafe 原形 `{model, state, questions}` —— **不是 chat messages** |
| ⭐ 三原语 | `noul`（0–1 是非概率）· **`choice`（≤255 选项，返回全概率分布 + confidence）** · `score` |
| 上下文 | 32k（CommandCode 侧） |
| 套餐 | **GOAT 及以上**才有 Provider API 权限；$1 的 Go 档没有 |
| 价格 | 输入 **$0.042/M**、输出 $0。⚠️ **免费额度已于 2026-09-24 结束**（原以为还有） |
| 流式 | **从不流式**（官方原话「it never streams」） |

⚠️⚠️ **仓库旧结论 `commandcode-endpoint-routing.md`（「非 Claude 模型只走 /v1/chat/completions」）
对 Jev 不适用** —— Jev 根本不是 chat 模型。**别再拿那条去选端点。**

⚠️ **一条会让请求直接失败的**：`typesafe/jev` **没有 ZDR-capable 上游** ——
带 `x-cmd-zdr: 1`（或 `CMD_ZDR=1`）会被 **422 拒绝**。

⚠️ **中文：官方文档自己写明的短板**（逐字）：
> 「English is the primary training language… **Other languages, including CJK scripts, are handled
> but not equally well**; test on your own content before relying on Jev for a non-English workload」

→ **判据只用英文那半**（课号 + 英文课名），中文不进 `state`。

### 7.3 ⭐ 两份调研**独立指向同一个设计**

| 来源 | 说法 |
|---|---|
| DEVONthink `[厂商]` | 「**This command is disabled if DEVONthink is not sure enough** about possible destinations」→ **不确定就不给结论** |
| paperless-ngx `[厂商]` | 措辞是「can **suggest** tags」—— **suggest，不是 assign** |
| Jev `[官方]` | `choice` **原生返回全概率分布 + confidence**，官方原话：「Set the thresholds for when it acts **autonomously** and when it **asks for review**」 |

→ ⭐ **置信度门槛不是我们发明的，是三处独立要求的同一件事。** 而 Jev 是三个里唯一**原生**给的。

### 7.4 ⭐ 先例：「先看映射再落盘」是**行业标准**，不是过度谨慎

| 产品 | 做法 | 档 |
|---|---|---|
| **DEVONthink** | 建议列表**带相关性分数**（"heat-mapped score"），选了才落盘 | `[厂商]` |
| **CSV 导入这一族** | 「an **import preview where you can confirm field mappings**」 | `[厂商]` |
| **导入 UX 共识** | 「A good CSV importer is not an upload form. It is a **staged workflow** that helps users **inspect, fix, validate, and only then commit**」 | `[设计]` |

⚠️ **但我们要的那个形态没有现成可抄**：DEVONthink 是**逐条**、CSV 那族是**字段**映射，
**「N 个文件 → N 门课的映射表」夹在两者之间** —— 这是要自己设计的（也是差异化空间）。

### 7.5 ⚠️ 失败模式：我们的版本比 paperless-ngx 那次更毒

`[社区]` paperless-ngx 真实事故：**464 份文档全被分给同一个错的人和同一个错标签** ——
不是随机错，是**自信地系统性错**。

**翻译成我们的场景**：错词进了 `glossary/<课号>.txt` → 而术语表**会被注入翻译 prompt**
（`translator.select_terms`）→ 那门课**以后每一句都在用错词**，且**要到课上才发现**。
→ **比「没加进去」糟得多。这就是「先看映射」那一刀不能省的量化理由。**

⚠️ **paperless-ngx 还有一条规则我们要照抄**：**未归档桶里的文档不参与它的学习。**
我们没有学习型分类器，所以那条的直接版本不适用 —— 但**类比版本适用**：
**没确认的东西不许进术语表**（术语表就是我们的「训练集」，因为它进 prompt）。

### 7.6 设计（**尚未实现**）

```
把一堆文件拖到面板空白处（不是某张卡）
  ↓
① 本地：文件名 × (课号 + 英文课名单词)  →  唯一最优才算命中   [免费·瞬时·不受中文影响]
  ↓  剩下的
② 抽一小段正文（extract.py 已有）+ Jev `choice`(选项 = 各课号)
      ⚠️ 官方三条硬约束：**一份文件一个问题**（别把多个判断塞进一问）·
         **只给文件名或极短摘要**（context rot：无关材料会损害准确率）·
         **计数用代码**（官方：Jev 的计数不可靠）
  ↓
③ confidence < 门槛 → **不猜**，进「未分类」
  ↓
④ ⭐ **把映射摆出来给作者看** → 点头才逐课跑 prep
```

**为什么是「兜底」不是「主力」**（这条与作者提的顺序一致，不是我的偏好）：
仓库对「要不要上模型」有一条翻转条件 —— 高频重复 · 答案空间可穷举 · **且没有确定性替代**。
前两条成立，**第三条不成立**：本地匹配免费、确定、瞬时、且完全不受 CJK 短板影响。

⚠️ **成本不是理由**（几十份文件名 ≈ $0.0001），**准确率也不是** —— 是「有确定解就别上模型」。

### 7.7 ⚠️ 两个 key 的形状对不上文档

作者贴的两个串，形状与官方文档的 `<CMD_API_KEY>` **不一致**（一个 `apikey_…`、一个 `user_…`）。
**不调用无法判断哪个是 Provider API key** —— 要去 Command Code Studio 的 API keys 页面对。
⚠️ 两个都**明文贴进过对话**，逐字记录已落盘；连同那笔「5 个泄露 key 待轮换」的账，**用前先轮换**。

### 7.8 未核实（诚实清单）

| 想查 | 结果 |
|---|---|
| 「本地先筛、模型兜底」的干净先例 | ⚠️ **没找到**（最接近的是 paperless-ngx 按字段选算法，那是配置项不是级联） |
| 自动分类的可信准确率基准 | ⚠️ **没有**，只有单用户自报「~90%」（无分母，不是基准） |
| Jev 在**短英文文件名**上的 calibration | **无公开实测** |
| CommandCode 套餐页里 Jev 现在确切额度 | **未取到**（计价器是动态组件） |
| 那两个 key 能不能用 | **未验证**（全程零认证请求，故意的） |

---

## 8. 审查遗留（2026-09-26，两个独立代理 + 一轮 `ocr`）

**两个必须在翻双击前修的已修**（见提交 `462f16c` / `80e8abd`）：
面板在生产路径上 AttributeError（参数名遮蔽模块名）· 归档静默覆盖 · 布局第二份定义 ·
缓存让第二条入口继承第一条的 `on_start` · 每开/关漏一个面板。

### 8.1 ⚠️ 还没修，按我建议的顺序

| # | 问题 | 在哪 | 为什么 |
|---|---|---|---|
| **L** ⭐⭐ | **`remove_terms` / `restore_lines` 不持锁** —— 而 `prep.prepare` 对**同一个文件**持有本课专属锁（`prep.py:830-841`，1045 释放） | `prep.py` | **两个写入器对同一文件读-改-写 → 后写的吃掉先写的。** `prep.py:828` 自己就写着「两个 `cl prep` 同时跑会互相吃掉对方的追加」——它为这件事上了锁，**新加的删词没上**。可达路径：prep 正在跑（或终端里跑 `cl prep`）时点面板上的「删」。修法：面板调用点用 `state_file + ".lock"` 取同一把锁 |
| **1** | `dragging_updated_` 无条件返回 `NSDragOperationCopy` —— 即使 `draggingEntered` 已经**拒了** | `panel.py` | 拖拽管理器看**最近一次**的返回值 → 被拒的拖拽会被重新接受。修法：记住 enter 的决定，updated 原样返回 |
| **P** | 拖**文件夹**在悬停时被当「收」（`panel.file_paths` 按 `urlBasedFileURLsOnly` 不过滤目录）→ 跑完是 `all_files_failed` | `panel.py` / `entry_panel.py` | 悬停时该拒。用户拿到的是「本次加了 0 个」+ 一个代号，像坏了 |
| **12** | `probe_entry_panel.py` 的「真 glossary 未变」自检**永远不会跑到** —— `runEventLoop()` 之后没有任何退出口 | 探针 | Ctrl+C 在这个进程里不一定送达 → 自检是死代码。修法：给个退出按钮 / 让它调 `AppHelper.stopEventLoop()` |
| **14** | `_open_prep` 的注释说「只记日志，不弹窗、不往外抛」，而它**只在 `CLASSLIVE_DEBUG` 下才记** → 默认整条路径完全静默 | `overlay.py` | 同「按钮 action 静默吞异常」那条教训的另一面 |
| **15** | `progress_text` 的 stage 表抄自 `prep._STAGE_NAME` 且**已漂移**：`build` 显示成英文、`notes` 那个 key **永远跑不到** | `entry_panel.py` | 本仓库明令的「一条纪律两处定义」（`SCRIM_ALPHA` 同款）。`prep._STAGE_NAME` 是唯一定义点 |
| **2** | `on_drop` 返回 `None` → `bool(None)` = False → **静默不收**（调用方忘了 return 就中招） | `panel.py` | 契约陷阱。修法：`None` 当「收」，或把注释写死 |
| **4/6** | `_ABORT_MSG` 有人话却没用（用户看到 `all_files_failed` 这种代号）；失败**只有计数**、逐文件原因从不渲染 | `entry_panel.py` | 分别是 plan 的硬要求（§3.6）和「最要紧那句」 |
| **7** | 「撤销」行落在**视口外**（n≥20 时），且从不滚动到它 | `entry_panel.py` | HIG 点名要防的「用户以为没生效、反复撤」 |
| **A2/A3/A4/B/C** | `_target` 是第 3 份实现 · `make_label` 没迁旧的（字重已不一致）· `make_scroll_view` 自称「只抽 4 行」实测 whatsnew 是 6 行逐字相同 · `sc._on_scroll` 是**死钩子**（全仓库无读取者）· `last_session` 每门课扫一遍 `sessions/`（3.1ms/3.5ms，**代理自己标了「不是性能问题」**） | 多处 | 整洁性 |
| **死代码** | `probe_entry_panel.py` 的 `COURSE` 没被引用 · `probe_drag.py` 的 `S["in_drag"]` 只写不读 · `mouse_down` 里一个没用到的导入 · 测试注释里的 `RESULT_TAIL` 全仓库不存在 | 探针/测试 | 顺手可清 |
