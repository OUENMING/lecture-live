# ClassLive 总说明 + 路线图

> 这份是**入口文档**。想知道"该做什么、按什么顺序做"，从这一份开始，
> 其余文档都由这里指出去。
> 最后更新 2026-09-26。

---

## 0. 现在在哪

ClassLive 是一个 macOS 本地实时英译中课堂字幕工具：`cl` 是 bash 启动器 →
`ClassLive.app/Contents/MacOS/python main.py` → 悬浮窗。
**已经能双击启动，不用终端**（P1，v3.7.0）。

- ✅ **已完成**：P0 清账（工作区干净）、P1 脱离终端、P7 `install.sh` 给朋友装
- ⬜ **还没做**：下面三件事 —— 而它们是**同一个功能的三个面**（见 §1）

> ⚠️ 这份文档以前写的是「只能用终端启动 → `.venv/bin/python`」，并说
> 「① 不能脱离终端」还没做。那两条在 v3.7.0 就过期了：解释器现在住在
> `ClassLive.app/Contents/` 里面，仓库根目录的 `.venv` 已经删掉。
> **看到旧说法以这一段为准。**

---

## 1. ⭐ 核心洞察：三件事其实是**同一个功能**的三个面

（这一条来自外部评审 2026-09-26 的建议，我核对后**认同并做了细化**）

| 面 | 表面上看 | 实际上是 |
|---|---|---|
| **入口** | 「一个上传文件的界面」 | **开课前的准备动作** —— 不是通用上传框 |
| **课件** | 「用户要放 PPT 进来」 | **候选术语的词源** |
| **Obsidian 可选** | 「换个地方存」 | **换一个渲染目标** |

### 1.1 入口 = 开课前的准备，不是通用上传框

某门课**第一次用之前**，把这门课的 syllabus / 课件拖进来 → 批量抽词 →
生成**这门课的候选术语表**。

触发时机从「上课时听到生词手动补」变成「**开课前一次性导入**」。
（运行时那条路已经有了：`build_notes.TermNotes` + `term_notes_auto.json`
—— 实测 `build_notes.py:488`，缓存文件 13 KB。）

**而且课件通常是真实的 PDF / PPTX（不是照片）**，文本抽取比"上课实时截屏 OCR"
简单可靠得多。

### 1.2 ⭐ 一次提取，两个出口（这是我对评审建议的细化）

评审说「`build_notes.TermNotes` 直接就有了更高质量的种子数据」——
方向对，但**要分清两个不同的产物**，它们吃的是同一份候选词：

```
  课件 PDF/PPTX
        ↓ PDFKit + stdlib zipfile 抽文本（零新增依赖）
          ⚠️ **不是 Docling** —— 见 PLAN-notes-and-ui.md §7.3 的作废横幅
        ↓ LLM 抽候选词（**新写的 prompt**）
          ⚠️ **`SYS_CLASSIFY` 抽不出词** —— 它的输入是「已存在的术语列表」，职责是分档。
             判据照抄它（中英对照价值），但抽取本身要新写。2026-09-26 回源码核实。
   候选词列表
        ├──→ glossary/<课号>.txt      ← 注入翻译 prompt（一行一个英文词）
        └──→ term_notes.json          ← 屏幕上的通俗解释（已有流水线）
```

**一份提取，两个出口。不需要新建任何数据管线。**

⚠️ **但有一条不能忘**：`glossary/<课号>.txt` 里有**两类词**，只有一类课件能给：

| 类别 | 例子 | 课件能给吗 |
|---|---|---|
| 领域术语 | `joule` / `OLS` / `regression` | ✅ 能 |
| **课号 + 教务词** | `ECON10101` / `Brightspace` / `deadline` | ❌ **不能** |

`glossary.example.txt` 里明写着：「**课号必须全列在这里** —— 同一院系的课号往往
发音极近，不给模型候选，它必然听混」。
→ **自动生成只能追加，绝不能覆盖**；课号那一段永远手写。

### 1.3 Obsidian 可选 = 多一个渲染目标

评审这条说得对，**但它有一个前提还没满足**：

> 「如果 `obsidian_writer.py` 已经拆成『生成结构化数据』+『渲染成某种格式』两段，
> 那么『没有 Obsidian 就存文件夹 + PDF』就只是多加一个渲染目标」

**⚠️ 它还没拆。** `_render_note()`（`obsidian_writer.py:497`）是**一根到底的字符串拼接**，
内容和 Obsidian 语法（callout / emoji / `<sub>`）焊死在一起。

**所以「多一个渲染目标」这句话省掉了那一步重活 —— 拆本身才是工作量。**
拆法见 §3 的接缝 ③。

---

## 2. 路线图

| # | 阶段 | 做什么 | 依赖 | 成本 | 状态 |
|---|---|---|---|---|---|
| **P0** | **清账** | 提交已完成但没提交的东西 | — | 小 | ✅ 做完 |
| **P1** | **脱离终端** | `.app` 启动器 + 单实例锁 + PATH 补丁 | 无 | **小** | ✅ 做完（v3.7.0） |
| **P2** | **界面地基** | 抽 `panel.py`（**纯搬家**） | 无 | 中 | ⬜ **下一个** |
| **P3** | ⭐ **开课前的准备** | 见下：**第一批（链路 + CLI）已做完**，面板待做 | P2 | 中 | 🔶 **第一批完** |

> **P3 第一批（2026-09-26 完成）**：`extract.py`（PDFKit + stdlib PPTX + Vision 兜底，零新增依赖）
> + `prep.py`（`prepare()` 接缝 + 只追加写入器 + 候选词 prompt + 反幻觉复核）
> + `cl prep` + `paths.py`。**两个出口都复用现成的**：`glossary/<课号>.txt` 喂翻译 prompt，
> `build_notes.build()` 顺手产 `term_notes.json`。
> **第二批待做**：拖拽入口面板（`entry_panel.py`），含 `panel.py` 今天完全没有的
> 拖拽投放支持，以及**必备的 NSOpenPanel**（HIG 要求「拖拽必须配另一条路」）。
> 方案与全部实测：`docs/PLAN-p3-prep.md`。
| **P4** | **拆渲染 + 数据自立** | 拆出内容模型；ClassLive 自己的数据目录；vault 降级为可选出口 | 无 | 中 | ⬜ |
| **P5** | **PDF 输出** | 新增一个渲染目标（HTML → PDF） | P4 | 中 | ⬜ |
| **P6** | **验证术语表** | 带**随机术语对照**的 A/B | P3 | 中 | ⬜ |
| **P7** | **给朋友** | `install.sh` | P1 | 小 | ✅ 做完（v3.7.0） |

**P1 那条「PATH 补丁」是 2026-09-26 才真补上的** —— 之前表里标着完成，实际 `cl` 里
一行 PATH 处理都没有：从 `.app` 启动时 `uv` 和 `ffmpeg` 都找不到，而 `uv` 的备用路
（`$PY -m pip`）也不通，因为 `.app` 里那个 python **不带 pip**。

### 2.1 P0 已经做完了（2026-09-26）

当时挂在工作区里的那 7 项全部提交掉了，工作区现在干净。
留着这一节只是因为 §2.2 的顺序理由引用了它。

### 2.2 为什么是这个顺序

1. ~~**P0 最先**~~ ✅ **已做** —— 当时工作区堆了 7 项，里面还有"待确认"的，
   再拖会跟 P4/P5 的改动混在一起
2. ~~**P1 排第二，因为它几乎不用写代码**~~ ✅ **已做（v3.7.0）** —— 地基当时就在了：
   `cl-bg.py` 的第一行 docstring 就是「**把 ClassLive 从终端彻底剥离**——双重 fork + setsid」，
   而且它**故意复用 `cl` 那一层**（自己的注释：「不做第二份拷贝 —— 否则"两处各记一次"迟早漂移」）。
   缺的只是一个 `.app` 外壳
3. **P2 必须排在 P3 前** —— 磨砂面板配方**已经抄了两份**
   （`overlay.py:600-623`、`whatsnew.py:205-218`，连"必须显式 DarkAqua"那条实测坑都抄了两遍），
   而 ObjC 类名撞车**已经真发生过**（`_Panel` 同名 → PyObjC 静默覆盖 → `build()` 返回 `None`）
4. **P3 一次做完三个面** —— 按 §1 的洞察，入口/课件/术语表本来就是一件事，
   拆成三个阶段做反而要来回改
5. **P4 可以和 P1/P2 并行** —— 它跟"脱离终端"没有依赖

---

## 3. 架构：后面所有功能挂在三条接缝上

```
① panel.py    一份磨砂面板配方（唯一真源）
                 ├── overlay.py       字幕面板   （消费者一）
                 ├── whatsnew.py      更新卡片   （消费者二，先搬）
                 └── entry_panel.py   开课前准备 （消费者三，P3）
                 ⚠️ 每个面板的 ObjC 类名必须全局唯一 —— 撞过的那次留下的硬约束

② 数据目录    ~/.classlive/courses/<课号>/{materials,notes}/<课号>.pdf
                 ← 入口把课件放进来；笔记和 PDF 从这里出去
                 ← <vault>/Lectures/ 降级为**可选出口**
                 ⚠️ sessions/ 不动 —— 三方共享契约

③ 渲染后端    内容模型（entries / review / qa_items）
                 ├── _render_note() → Obsidian Markdown   （已有，但和内容焊死）
                 └── render_html()  → HTML → PDF          （P5 新增）
                 ⚠️ **共用数据，不共用字符串**
                 ⚠️ 别去"转换 Markdown" —— 实测会漏 5 类
                    （callout / emoji / :: 卡 / 折叠段 / 撇号）
```

**⭐ 把渲染器放在接缝后面，等于让 PDF 引擎的选择变成可逆的。**
先选一个，不合适再换，成本很低 —— 所以下面这个选型不用纠结太久。

---

## 4. PDF 引擎选型（**已修正，我上一版结论有错**）

### 4.1 ⚠️ 先纠正我自己

上一轮我说「WeasyPrint emoji 变豆腐块」—— **那是错的**。
真实原因是**我的 CSS 只写了 `Noto Sans SC`，没写 emoji 字体栈**。
加上 `'Apple Color Emoji', 'Apple Symbols'` 之后重测：

| | 修正前 CSS | **修正后 CSS** |
|---|---|---|
| `CIDFontType0` 命中 | 1 | **0** |
| `LastResort` 字体数 | 2 | **0** |
| 渲染结果 | 🎯 变空方框 | ✅ **彩色 emoji** |

**问题在我的 CSS，不在 WeasyPrint。**

（顺带：我第一次的自检脚本还有个 bug —— 正则的字符类漏了 `.`，
而字体名是 `COQGKQ+.LastResort`，导致误报"命中 0"。已修。）

### 4.2 两条路的实测对比

| | **WeasyPrint** | **WebKit**（`WKWebView.createPDF`） |
|---|---|---|
| 中文 | ✅ | ✅ |
| emoji | ✅ 彩色 | ✅ 彩色 |
| 字体回落 | 0 | 0 |
| 速度（515 行真实笔记） | 5.7 s | **0.7 s** |
| 要装什么 | `uv pip install weasyprint` **+ `brew install python pango libffi`** | `uv pip install pyobjc-framework-WebKit` |
| 跨平台 | ✅ 是 | ❌ 仅 macOS |
| 渲染引擎 | CSS 子集（行为可预测） | 完整 WebKit |

### 4.3 评审的建议与事实的出入

评审说：「给普通用户用，**前者（WeasyPrint）更省心**」，理由是**跨平台、
不依赖系统组件**。逐条核对：

- ❌ **「不依赖系统组件」在 macOS 上是反的。** WeasyPrint 官方文档逐字：
  > "The easiest way to install WeasyPrint on macOS is to use Homebrew.
  > When Homebrew is installed, install Python, Pango and libffi: `brew install python pango libffi`"
  （[doc.courtbouillon.org](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html)）
  装不上会报 `cannot load library 'libgobject-2.0-0'`。
  而 WebKit 是**系统自带框架**，PyObjC 绑定是纯 Python 包装 —— 谁更"依赖系统组件"是反过来的。
- ❌ **「跨平台」对本项目价值为零。** README 写明只支持
  **macOS + Apple Silicon**（本地推理依赖 MLX/Metal，悬浮窗依赖 AppKit）。
- ✅ **评审没提但成立的**：WeasyPrint 的渲染是**确定性的、版本可 pin**；
  WebKit 跟随系统版本，macOS 升级可能改变输出。

### 4.4 结论

**选 WebKit。** 但理由不是"WeasyPrint 不行"（它行），而是
**「朋友要装什么」**：朋友按现在的 README 装，**全程不需要 Homebrew**；
引入 WeasyPrint 会把 Homebrew 变成新依赖。

⚠️ 而且这个决定**可逆**（§3 接缝 ③）—— 真被 WebKit 咬了，换成 WeasyPrint 只是换一个后端。

---

## 5. P1 的三个坑（都已实测）

### 5.1 ⚠️ 没有单实例保护 —— 双击会起两个

全仓搜 `single.instance|flock|pidfile|已在运行` → **0 命中**（唯一的锁是 `update.py`
自己的 `update.lock`，只管更新器）。

双击两下 = 两个 `main.py`、两个 `InputStream` 争同一个麦克风、
**两个悬浮窗叠在一起**、两倍 API 花费。
（会话文件名带 `%H%M%S`，所以**文件不会互相覆盖** —— 那层是安全的。）

**这是"改成双击"新引入的风险。**

### 5.2 PATH：Finder 启动时只剩 `/usr/bin:/bin:/usr/sbin:/sbin`

实测逐个查过 `cl` 用到的所有命令，**只有两个会找不到**：

| 命令 | 实际位置 | 影响哪条路径 |
|---|---|---|
| `uv` | `~/.local/bin/uv` | 只有 `cl update` 的**补依赖**分支 |
| `ffmpeg` | `/opt/homebrew/bin/ffmpeg` | 只有 `cl file` |

其余（`git`/`shasum`/`find`/`grep`/`sed`/`tail`/`head`/`ls`/`cat`…）**都在 `/usr/bin`** ✅。
`update.py` 用 `sys.executable` ✅，`main.py` 用 `git` ✅，
`cl-bg.py` 已经 `os.chdir(HERE)` 所以 cwd=`/` 不是问题 ✅。

→ **补两条绝对路径就行，不用改 PATH 环境。**

### 5.3 双击失败是**静默**的

ClassLive 是 `.accessory`（无 Dock 图标），日志在 `~/Library/Logs/ClassLive/bg.log`，
但用户不会去翻。**启动失败得弹个东西出来。**

---

## 6. 明确不做的

| 项 | 结论 | 依据 |
|---|---|---|
| **VPS 参与** | ❌ | 同类工具的自建服务器清一色是同步不是算力；Docling 那点活不值得引入 third-country 合规面 |
| **PyInstaller 自包含打包** | ❌ | Finder 下 PATH 被砍、C 扩展隐式 import 静默失败（sherpa-onnx 正是）、6.0 起符号链接问题 |
| **发 DMG** | ❌ | 浏览器下载会打 `quarantine` → Gatekeeper 拦。**走 git/curl 就完全不用签名**（实测） |
| **重写原生（Swift/Tauri）** | ❌ | 成本巨大，收益只有"更像一个 app" |
| UI 动效 | 延后 | 计划里写着要**先量**词级编辑距离分布再决定 |
| 笔记复习层重做（`REVIEW_SYS` 病根） | 独立大项 | 病根是「绝不引入外部知识」让模型只能复述。**不在本路线图上，但要留着** |

---

## 7. 文档索引

| 想知道 | 读哪份 |
|---|---|
| **该做什么、什么顺序**（你在这） | `docs/PLAN-roadmap.md` ← 本文 |
| 入口形态、无 Obsidian 怎么导出、PDF 实测 | `docs/RESEARCH-entry-and-export.md` |
| 要不要做成 .app、VPS 评估、课件→关键词 | `docs/RESEARCH-product-shape.md` |
| 外部评审（含已知薄弱点） | ⚠️ 不在仓库里。`~/Desktop/classlive-review/REVIEW-2026-09-24.md` |
| 架构快照（**2026-09-18 的历史快照，行号别抄**） | `ARCHITECTURE.md` |
| 老计划（**大部分已被本路线图吸收**） | `docs/PLAN-notes-and-ui.md` |
