# P3 方案：开课前的准备（课件 → 候选术语 → glossary + term_notes）

> 状态：**待作者点头**。仓库规矩 —— 方案先给作者看，他同意再动手。
> 作者已拍板三条（§0.3），本方案在它们之上展开。
> 日期 2026-09-26 · 代码状态 HEAD = `0673103`（只有文档变动，代码与 `1d1d461` 相同）

---

## 0. 怎么读这份

### 0.1 上游（都已在 `docs/`，别重复推）

| 文件 | 什么时候读 |
|---|---|
| `BRIEF-p3-author.md` | ⭐ **作者原话**。与本方案冲突先问，别自己改 |
| `RESEARCH-p3-seams.md` | 想知道某一跳插在哪个 `file:line` |
| `RESEARCH-p3-extract.md` | 想知道抽文本为什么选 PDFKit / stdlib |
| `RESEARCH-p3-community.md` | 想知道每条设计在社区里的先例与反例 |
| `HANDOFF-p3-prep.md` | 想知道上一轮做到哪 |

### 0.2 本方案新增的实测（不是转述，是这次现跑的）

| 测什么 | 结果 | 影响 |
|---|---|---|
| **PDFKit 是否释放 GIL** | **释放**（交替 5 轮 ABAB：空闲 33.4M vs 抽取 33.9M iters/s = **101.4%**） | 推翻「抽文本必须独立进程」的原前提 |
| **Vision OCR 是否释放 GIL** | **释放**（35.6M vs 35.4M = **99%**） | 同上 |
| **一门课真实抽文本耗时** | ECON10730 578 页 **1.72s** · SOC10020 461 页 **1.99s** · ECON10790 209 页 0.60s · ECON10740 80 页 0.22s · ECON10770 8 页 **0.02s** | 全 CPU 部分 ≤2 秒 |
| ⭐ **OCR 兜底的代价**（实现后补测） | 全 59 份 PDF：**关 OCR 4.18s / 1,894,516 字符** → **开 OCR 17.61s / 1,997,595 字符**（**60 页**走了 OCR，+5.4% 字符） | 那 13.4 秒买的是两份扫描教材（SOC10020 的 Piketty 与 Wade）—— **课前批处理，值**。⚠️ 早期文档写「≈2 秒」是**不含 OCR** 的数，别混用 |
| **术语文件是「启动读一次」吗** | **是**。`translator.py:384-386` 在 `Translator.__init__` 读；`main.py:845` 的 `TermNotes()` 也在构造时读 | 「下次启动生效」成立，且**没有竞态** |
| **真实 glossary 的首行块** | `ECON10770.txt` 1 行 `#`；`SOC10020.txt` **2 行** `#` | 追加写入器要保**整个前导 `#` 块**，不是「只保第一行」 |

### 0.3 作者已拍板的三条

1. **宿主 = 独立进程**。`cl prep` 单开一个小窗口。附带一条：**启动时探一下实例锁**，
   占用就提示「正在上课，这次改动下节课才生效」—— 讲清楚预期，不是技术防护。
   ⚠️ 作者要求记进范围：**NSOpenPanel（点击选文件）是最终那个窗口的必备件**，
   不因走哪条路消失。「拖拽」是主要交互，但总有人不知道能拖 —— 所以它也是保底路径（§10）。
2. **不做逐词确认**。默认全自动加进去 + 事后一张可删的列表。**这是显式取舍**（§6.3 取舍 1），
   而且**靠三根柱子撑着**：自动加上限 40 条 / 超出的标成「未自动加入」不丢 / 跑完主动提示一句
   （作者 2026-09-26 补的三条，见 §5.2、§5.3）。完整定位见 §6.1。
3. **第一批 = 链路优先**。抽文本 + 写词 + 抽候选 + `cl prep`，**先不做面板窗口**。
   作者给的理由比「面板是大块新代码」更实在：**面板设计现在没有任何真实数据可参照** ——
   候选词抽出来长什么样、噪声大不大、要不要分组，现在全是猜的。
   **先拿一门真课件跑通链路，面板该长什么样会从真实输出里自己冒出来。**

---

## 1. 完整链路：从「拖进窗口」到「悬浮窗里出现中文」

```
用户拖入（或点「选择文件…」走 NSOpenPanel）        ← 第二批才有，第一批是命令行传路径
        ↓  [文件路径列表]
   cl prep <文件…>                                 cl:136 的 case 新分支
        ├─ 读 .course 拿课号                        cl:45-46（CFG）
        ├─ 探实例锁（占用 → 提示"正在上课"）          instance_lock.py 新增 is_held()
        └─ 调 prep.prepare(...)                     本方案新增
        ↓
   prep.prepare(course, files, on_progress)        ⭐ 接缝：UI 无关，纯函数
        ├─ ① 归档文件 → ~/.classlive/courses/<课号>/materials/    paths.py
        ├─ ② extract.extract(path) → ExtractResult(blocks=[Block(text, kind, page)])  extract.py（新）
        │       PDF  : Quartz.PDFKit 逐页 pageAtIndex_(i).string()
        │       PPTX : zipfile + xml.etree，**按 `<a:p>` 分段**，
        │             记 `<p:ph type>` 当权重标记（ctrTitle/subTitle/title/body）
        │       空页 : CGPDFDocument → CGBitmapContext → Vision OCR（accurate 档）
        ├─ ③ candidates.pick(blocks) → [Candidate(term, conf)]    新 prompt（§5.2）
        │       复用 build_notes._chat_json（build_notes.py:222）
        │       ⭐ 置信度由**这一次调用**给出 —— 它已经看过全文了
        ├─ ④ 拆分：前 40 条 → 自动加；其余 → 「未自动加入」清单（§5.2）
        ├─ ⑤ append_terms(course, 前40条) → (added, skipped)      新写入器（§4）
        └─ ⑥ build_notes.build(course=...) → term_notes.json      ✅ 已存在
        ↓  on_progress(stage, done, total)
   窗口/终端显示进度，**跑完主动打一句**：
        ✅ 这次加了 23 个词 → glossary/ECON10770.txt（共 58 条）
           另有 17 个候选未自动加入（置信度不足），要加的话从下面挑：
             · sunk cost        · marginal revenue     · …
        ↓
   【进程退出】
```

⭐ **第一批里，「事后列表」就是这几行终端输出** —— 不需要等面板做好才有地方放它。
作者亲眼看到候选词名单变化，本身就是这个列表最朴素的第一版。

**生效路径**（下一节课）：

```
cl / 双击 .app
  ↓
main.py:812  load_translator(..., course=)  →  Translator.__init__
  ↓  translator.py:384-386   读一次（glossary.txt + glossary/<课号>.txt + 首行领域先验）
  ↓  translator.py:429-430   每句 prompt **全量注入**课程术语（always=self._course_terms）
main.py:845  TermNotes()                    →  读一次 term_notes.json
  ↓  build_notes.py:565  match(sentence)    →  命中即在字幕上显示中文解析
```

⭐ **整条链路只在两处碰现成代码**：`cl` 加一个 case 分支；`build_notes.build()` 原样调用。
**`main.py` / `translator.py` / `drain()` / 单实例锁，一行都不改。**

---

## 2. 接缝

### 2.1 唯一的公开接口（`prep.py`）

> ⚠️ **下面是方案期的草图，别照它调用。** 真实签名有 13 个参数
> （多了 `glossary_dir` / `state_path` / `materials_dir` / `skip` / `api_key` /
> `model` / `max_auto` / `max_total` / `ocr` / `chat` / `build_fn`），
> **以 `prep.py` 的 `prepare()` 为准**（`inspect.signature(prep.prepare)` 一眼看到）。
> 本节保留草图是因为它表达的是**设计意图**（只有课号与文件是必须的，
> 其余都该有默认值），那一条实现时守住了。

```python
def prepare(course: str, files: list[pathlib.Path], *,
            on_progress=None) -> PrepResult
```

**不变量（调用方必须知道）：**
- **不起线程、不画窗口、不读 `argv`、不读 `.course`**。课号是参数，不是环境。
- **只追加 glossary**，永不覆盖、永不重排、永不删（§4）。
- **可重复**：同一份课件导两次，`glossary` 与 `term_notes.json` 都不变（幂等）。
- **不抛异常给 UI**：单份文件抽不出 → 记进 `PrepResult.failed` 继续下一份。
  只有「全部文件都抽不出」才返回一个失败的 `PrepResult`（**不是**空表，见 §5）。
- 网络调用会阻塞（`timeout=180`）。**调用方自己决定放哪个线程。**

`PrepResult` 至少带：`per_file`（每份抽了多少字符 / 多少形状没读到）、
`candidates`（候选词 + 权重）、`added` / `skipped`（写进 glossary 的 / 因重复跳过的）、
`notes_built`（`build_notes.build` 处理了几条）、`failed`。

### 2.2 两个适配器

| 适配器 | 批次 | 形状 |
|---|---|---|
| `cl prep <文件…>` | **第一批** | 同步调用，进度打到 stdout（照 `build_notes.py:589` 的 `__main__` 先例） |
| `prep_app.py`（小窗口） | 第二批 | 后台线程调 `prepare`，进度回窗口；**拖拽 + NSOpenPanel 两条路**（作者 2026-09-26 要求） |

「一个适配器是假缝，两个是真缝」（`RESEARCH-entry-and-export.md:294`）——
这里正好两个，而且**第二个到现在都还没写**，所以第一批就要把接口按两个调用方来设计。

### 2.3 为什么 `extract.py` 单独成模块

它是这条链上**唯一有独立复杂度**的东西：三种输入格式、四条代码路径（PDF 文字层 /
PPTX 解包 / 转位图 / OCR 兜底）、还要产出「有多少内容没读到」的审计数字。
它也有**独立的变更理由**（macOS 升级改 PDFKit 行为、新增文件格式）。
其余部分（候选词 prompt、追加写入）是小的、内聚的，留在 `prep.py` 里当内部接缝。

**两个新文件，不是一个，也不是四个。**

---

## 3. 四个冲突的处置

### ① Docling 的记录自相矛盾 —— **给 §7.3 挂作废标记**

`PLAN-notes-and-ui.md:463` 的 §7.3 写着「✅ 选它（Docling）」，比作者 brief 早 48 分钟，
而 brief 说「不需要 Docling 那套重的」。**以 brief 为准。**

处置：在 §7.3 标题下加一行作废说明 + 指向本方案 §5.1。**一处编辑，不做别的。**
不改正文（那是历史记录），只加横幅 —— 与 `ARCHITECTURE.md` 文首那条横幅同一个做法。

### ② `SYS_CLASSIFY` 抽不出候选词 —— **确认，要新写 prompt**

`PLAN-roadmap.md:55` 写「用已有的 `SYS_CLASSIFY`」。读代码：`build_notes.py:283`
喂给它的输入是 `"\n".join(chunk)`，而 `chunk` 来自 `todo_cls` → 来自 `terms` 字典的键 →
来自 `collect_terms()` 读**已有文件**。它做的是**给已存在的术语分档**，不是从散文里抽词。

**但它的判据可以照抄，而且应该照抄。** `build_notes.py:39-45` 逐字：

> 判断标准是**中英对照价值**，不是"这个概念难不难"

新 prompt 就用这一条当筛选标准 —— **同一个人的口味，两条路一致**。
顺带修 `PLAN-roadmap.md:55` 那句话。

### ③ glossary 是「三类」不是「两类」—— **追加写入器必须兑现**

第一行/前导 `#` 块是**领域先验**（`translator.py:136-160` 读它），**不是术语**。
实测 `ECON10770.txt` 有 1 行、`SOC10020.txt` 有 **2 行** —— 所以要保的是
**整个连续的前导 `#` 块**，不是「第一行」。

另外两类（课号 + 教务词）**课件给不了**，永远手写 → 写入器**只碰 `glossary/<课号>.txt`，
永不碰公共 `glossary.txt`**。

⚠️ 且 `glossary/<课号>.txt` 今天**全仓零写入方**（已 grep 证实），写入器从零写。

### ④ 进程模型 —— **已定：独立进程。原前提被实测推翻**

`RESEARCH-p3-seams.md` §9 把这条列为「必须动手前定死」，理由是
「Docling 是 CPU 密集的 → 抢 GIL → 必须独立进程 → 独立进程撞单实例锁」。

**这个前提不成立**，两处：

1. **我们不用 Docling。** 选的是 PDFKit + stdlib（`extract` 那份调研的结论）。
2. **PDFKit 和 Vision 都释放 GIL**（本次实测 101.4% / 99%），
   而且全 CPU 部分**一门课 ≤2 秒**（最重的 SOC10020 461 页 1.99s）。
   GIL 从来不是问题。

**结论**：走独立进程（作者已拍板），但理由**不是**「GIL 撑不住」——
是三条界面上的好处：不录制时也能用、对上课那条路**零改动零风险**、
窗口是个能正常聚焦的普通窗口（拖拽投放到非激活浮动面板上**没测过**，
普通窗口则不必赌这一条）。

**附带一条改动**（作者要求）：`instance_lock.py` 新增 `is_held() -> (bool, pid|None)`，
`cl prep` 启动时探一次。占用 → 提示「检测到 ClassLive 正在上课；这次改动**下节课**才生效」。
**探完立刻释放，不持有** —— 它是告知，不是互斥（§0.2 已确认术语只在启动时读一次，
没有竞态可防）。锁的语义只有 `instance_lock.py` 一个定义点，所以这个判断也放那儿。

---

## 4. 追加写入器：规矩逐条（这里最容易写出事故）

| 规矩 | 为什么 |
|---|---|
| **只追加**，永不覆盖/重排/删除 | 课件给不了课号与教务词；覆盖一次就是永久丢失 |
| ⭐ **已有字节逐字节不变，新内容只出现在末尾** | 这一条**盖住**下面那条，也盖住课号、教务词、用户手写的任何东西。**它是最强的那条判据**（§4.1） |
| **保住整个前导 `#` 块**（含空注释行） | 它是领域先验；丢了模型就不知道「这是热力学课」（`translator.py:141-142`） |
| **文件名恰好 `<课号>.txt`** | `course_terms_path` 靠**后缀匹配**兜底（`translator.py:107-109`），名字错了会**静默退回只用公共表** |
| **大小写不敏感去重**，对「已有全部行」和「本批内部」各去一次 | 幂等：同一份课件导两次不许出现第二份 |
| **单条 > 5 个空格分隔 token 的丢弃** | EAMT 2023 TransPerfect 术语库清洗的既有规则（`RESEARCH-p3-community.md` §5.5） |
| **原子写**：先写同目录 tmp 再 `os.replace` | 照 `build_notes._atomic_write`（`build_notes.py:167`）现成的做法 |
| **文件不存在时**：建，首行写 `# <课号>` | ⚠️ **不猜课名** —— 猜错会把模型带偏，比没有更糟。改为在终端提示：
「建议在首行补上课名，例如 `# ECON10770 Introduction to Economics(经济学导论)`」 |
| **写完出声**：`+37 条（跳过 5 条重复）` | 静默是这套代码库反复咬人的东西（`RESEARCH-p3-community.md` §5.4） |

### 4.1 ⭐ 「课号那一段永远手写」—— 用测试接住，不靠记性

作者 2026-09-26 点名要把这条**做成硬测试用例**。先按代码把位置核准
（`PLAN-roadmap.md` 的说法与实测有一处出入，以代码为准）：

| 内容 | 实际住哪 | 谁能写 |
|---|---|---|
| **课号** `ECON10770` / `SOC10020` | ⚠️ **公共表 `glossary.txt:5-9`**，**不在**分课表里 | 手写 |
| **教务词** `Brightspace` / `quiz` / `tutorial` | 公共表**和**分课表**都可能有**（`SOC10020.txt:41-47` 实测有 5 条） | 手写 |
| **首行 `# <课号> <课名>`** | 分课表（`translator.py:136-160` 读它当领域先验） | 手写 |
| **课程术语** | 分课表 | 手写 + **P3 自动追加** |

⚠️ 路线图/作者原话把课号记成住在 `glossary/<课号>.txt`，**实测是公共表**
（`glossary.example.txt:5` 也明写「课号必须全列在**这里**」= 公共表）。
位置记错不影响结论 —— 结论是**更强的那条**：追加器**一个已有字节都不许动**，
所以「碰不到课号」是它的**推论**，不是它需要单独记住的规则。

**测试怎么钉（两条，一强一具体）**：

1. **强的那条**：读入 → 追加 → 断言**原文件内容是新文件的前缀**（`new.startswith(old)`）。
   一条断言同时盖住课号、教务词、首行、空行、用户手写的任何东西。
2. **具体的那条**：造一份**含课号形态的行**（`ECON10101`）的样表，追加后
   逐字节断言那一行**连位置都没动**。这条是为了让失败信息说人话 ——
   第一条红了你知道「前缀变了」，第二条红了你知道「是课号被动了」。

外加一条**写侧**的判据：`append_terms` 的签名里**没有公共表这个参数**，
它对 `glossary.txt` 的连接**不存在**（不是「不调用」，是拿不到）。

### 4.2 ⚠️ 一个真缺口：删掉的词，重跑会不会被加回来

补调研 Q2 挖到一条**别人踩过、官方写进文档**的坑 ——
Wispr Flow 逐字：「Deleting on desktop is permanent, and **auto-learning will not
re-add a deleted word.**」这句话之所以存在，说明**「删了又被自动加回来」是真实用户会踩到的**。

本项目**今天没有防止它的机制**：写入器的「大小写不敏感去重」只比**文件里现有的行**，
用户删掉一行之后，那份课件的内容还在，**重跑就会把它加回来** —— 而且是静默的。

**处置（进第一批）**：给每门课一份 `prep-state.json`，记「**prep 曾经追加过的词**」：

⭐ **这个机制在术语管理行业里是一等公民，叫 `stop word list`** —— memoQ 逐字：
「add all the candidates you marked as **Dropped** to the current **stop word list**」，
而且「**You don't need to run term extraction to edit a stop word list**」（能脱离抽取单独维护）。
→ **§4.2 不是自己发明的，是行业里的标准配置。**（详见 §6.1.2）

```
~/.classlive/courses/<课号>/prep-state.json
  {"appended": {"polycrisis": "2026-09-26", "sunk cost": "2026-09-26", ...}}
```

追加判据变成 **`候选 − 文件现有行 − 已追加登记`**。于是：

| 场景 | 结果 |
|---|---|
| 同一份课件重跑 | 已追加过的不再写 → **删掉的词不会被复活** ✅ |
| 课件换新版本 | 同上（**用户的删除是权威的**，与「只追加」同精神）✅ |
| 用户想让某个词回来 | 手打一行 —— 代价明确、可接受 |

**一个集合同时干两件事**：它既是幂等的依据，也是墓碑。
（代价：状态文件会涨，但一门课几十条 × 几次导入，量级可忽略。）

⚠️ **别把它做成「按文件哈希跳过整份」** —— 那解决不了「课件换了版本、旧词被删过」
那种情况，而且那样一来「这份导过」和「这个词加过」是两个不同的状态，要维护两套。

⚠️ 这也意味着 `prepare()` **不再是 (course, files) 的纯函数** ——
它要读状态文件。**按接缝规矩：状态路径是参数，不由它自己去算**（§2.1）。

---

## 5. 抽文本与候选词：规矩

### 5.1 抽文本（`extract.py`）

| 输入 | 怎么做 | 依据 |
|---|---|---|
| PDF 文字层 | `from Quartz import PDFKit`，逐页 `.string()` | 584 页与作者在用的 `pdftotext` 对拍**一个词不输** |
| PPTX | stdlib `zipfile` + `xml.etree`，**按 `<a:p>` 分段**（否则同段 run 会粘成 `Ideas in EconomicsDr. Ciara Whelan`），记 `<p:ph type>` | 粒度比 python-pptx 多两档，**零依赖** |
| ⚠️ **表格** | ⭐ **`<p:graphicFrame>`，不在 `<p:sp>` 里** —— 必须单独遍历，按 `<a:tr>` 行成块、单元格 `" \| "` 连 | 见 §5.1.2。**只遍历 `<p:sp>` 会静默丢掉 19/34 个带文字的表格** |
| 空页兜底 | 只有某页 `.string()` 为空才转位图 + Vision（accurate 档） | 语料 4.7% 的页无文字层；SOC10020 实测 62 页 |
| 矢量公式（`wmf`/`emf`） | **不处理**，但**记进审计数字** | Vision 读不了这类，真丢失 |
| ⚠️ **打不开的文件** | **`initWithURL_` 返回 `None` 就是失败**，记进 `failed` 并出声 | 见下 §5.1.3 |

⚠️ **`Block` 上没有 `weight` 字段**：**`kind` 是事实，`weight` 是政策。**
把「标题档值多少分」冻进一个只负责读文件的模块，改权重就得动它。**事实出模块，政策留消费者。**

### 5.1.1 ⚠️ 版式噪声：本机实测（作者 2026-09-26 指出，比预想的更细）

**实测 23 份真课件 / 494 张 slide 的占位符分布**：

```
body 1117 · title 421 · ctrTitle 8 · subTitle 6 · pic 2 · sldNum 1
ftr 0 · dt 0        ← 页脚 / 日期占位符一个都没有
```

而 `sldNum` 那唯一一个的内容是 `"2"`。→ **作者猜的「页脚/页码/日期里塞课号校名」
在这份语料里不成立。但噪声确实存在，只是换了个位置：**

**把「出现在 ≥40% 张 slide 上」的文字捞出来，命中 15 条，全部是同一个东西** ——
**章节标题跑马灯**，而且它在 **`body`** 里（不是 `title`）：

```
ECON10790__Chapter 14.pptx   46/47 张   "Ch 14: Functions of two or more independent variables"
ECON10790__Chapter 9.pptx    13/14 张   "Ch 9: Elasticity"
ECON10790__Chapter 1.pptx    17/19 张   "Ch 1: Arithmetic"
```

**读法（三条）**：

1. ⭐ **作者的机制成立**：这些文字**全篇每页都出现**，按「出现次数」排序会拿到最高分。
   而 `§5.2` 现在的判据正是「单字 ≥2 次 / 多词 ≥1 次」的**下限**，没有**上限** ——
   跑马灯会稳稳挤进前 40。
2. ⚠️ **但它不是「整条都是垃圾」**：`Ch 14:` 是垃圾前缀，
   而 `Functions of two or more independent variables` 是**真领域短语**。
   → **不能整条丢**，只能剥前缀或降权。**这一点让「一刀切黑名单」的方案不成立。**
3. **好在它没吃到 title 权重**（在 `body` 里）—— 这一条是运气，不能指望别的学校。
   别的模板里跑马灯很可能就在 `title` 占位符里。

**处置（三步，都不靠硬编码黑名单）**：

| 步 | 做什么 |
|---|---|
| ① **显式排除** | `<p:ph type>` 属于 `dt` / `sldNum` / `ftr` / `hdr` / `pic` / `media` / `sldImg` / `chart` / `dgm` / `clipArt` / `obj` 的形状**不进候选范围**（不是降权，是不抽）。本机语料里几乎为空，但成本是 0。完整取值见 §5.1.2 |
| ② ⭐ **喂两个信号，不是一个** | 每个候选带 **`出现次数`** 和 **`出现在多少张 slide 上`**（slide spread）两个数。LLM 看到「46/47 张」自然判得出那是不跑马灯，而不是热词 |
| ③ ⭐ **不设硬频率上限** | ⚠️ **别用「≥X% 张 slide 就丢掉」** —— `ECON10790__Chapter 9` 整章讲 `elasticity`，它很可能就在 13/14 张上，硬上限会把**最核心的那个词**丢掉。**判断权交给 LLM**（它本来就有 `SYS_CLASSIFY` 那条「中英对照价值」判据） |

⭐ **② 是关键**：作者担心的是「排序信号本身被污染」，而污染的原因是
**我们把「出现次数」当成了单一证据**。给它配一个「跨多少张 slide」的对照，
跑马灯和核心术语就分得开了 —— **同一个词在一页里出现 46 次**和
**在 46 页里各出现一次**，是完全不同的两件事，而现在的方案把它们算成了一个数。

### 5.1.2 ⭐⭐ 表格在 `<p:graphicFrame>` 里 —— 只遍历 `<p:sp>` 会**静默丢掉它们**

**本机实测（23 份真课件）**：

```
<p:graphicFrame>  34 个，其中 19 个带文字     ← <p:sp> 循环一个都抓不到
<p:sp> 无 <p:ph>  696 个，其中 450 个带文字   ← 目前一律当 body，没有任何区分
```

**丢的是什么（抽样，全都不是垃圾）**：

```
ECON10740__Library session:  Tourism economics | Tourism economy | Hospitality industry | finance | travel
ECON10740__Week2:            Format | Audience | Purpose | Style/Tone | Evidence/Visuals | Essay | Academics
Final_presentation.pptx:     SDG Goal 4 - Ensure inclusive and equitable quality education and promote…
SDGs.pptx:                   1 End poverty in all its forms everywhere. 2 End hunger…
```

→ **这正是 §5.1 禁止的那类失败**：不报错、不计数，只是内容少了一块。**必须单独遍历 `graphicFrame`。**

**处置**：
- `graphicFrame` 的文字 → `kind="table"`，**按 `<a:tr>` 行成块**、行内单元格用 `" | "` 连
  （表头行因此是独立一块）；非表格的 graphicFrame（SmartArt 等）没有 `<a:tr>`，
  退化成按 `<a:p>` 分段 —— 对外仍是 `kind="table"`，调用方不必知道这个分叉
- 无 `<p:ph>` 的 `<p:sp>` → `kind="textbox"`（**不再混进 `body`**）。
  里面 450 条有文字，抽样里既有真内容（`Tourism economics`）
  也有噪声（`This Photo by Unknown Author is licensed under CC BY-SA`、裸 URL）——
  **`extract` 只负责贴这个标签，不做过滤**（过滤是 `prep` 的政策，会随语料变）

⚠️ **为什么 `table` 要落在 `kind` 上而不是只记进审计聚合数**：因为**它要逐块到达提示词** ——
表格行 `Monopoly | high barriers to entry | price maker` 和正文 bullet 是两种证据形态，
LLM 得知道「这是表格」才会（a）不抽列头/纯数字单元（b）信任成对出现的术语。
聚合计数到不了 LLM。**两处都成立，所以落 `kind`。**

#### `ST_PlaceholderType` 的**完整** 16 个取值（ISO/IEC 29500-4:2016 `pml.xsd` 原文核过）

```
title  body  ctrTitle  subTitle   ← 文本档，进候选
dt  sldNum  ftr  hdr              ← 版式/页眉页脚，排除
obj  chart  tbl  clipArt  dgm  media  sldImg  pic   ← 非文本对象，排除
```

⚠️ 原文出处：`pml.xsd` 的 `<xsd:simpleType name="ST_PlaceholderType">`，
16 条 `<xsd:enumeration>`。**方案早期只列了 4 个，那是不完整的。**
（⚠️ `tbl`/`chart` 这两个占位符不在 `<p:sp>` 里 —— 它们就是 `graphicFrame`，
所以上面的排除集和「要单独遍历 graphicFrame」并不矛盾：排除的是形状本身，
而带的文字按 `kind="table"` 收。）

### 5.1.3 ⚠️ PDFKit 打不开的文件是**静默**返回 `None`（本机实测）

```
空文件              -> initWithURL_ 返回 None
非 PDF（纯文本改名）   -> initWithURL_ 返回 None
截断的真 PDF         -> initWithURL_ 返回 None
```

**三种都是 `None`，没有异常、没有报错。** → `extract.py` **必须显式判 `doc is None`**
并记进 `failed`，否则坏文件会静默贡献 0 字符，看起来像「这份课件没有术语」。

**加密 PDF**：`PDFDocument` 有 `isEncrypted()` / `isLocked()` 两个现成的判据（API 已确认存在），
但 **`isLocked()==True` 时 `.string()` 到底是什么行为 [未测]** ——
本机 59 份真 PDF **加密 0 份**，没有样本。
→ **实现按保守做法**：`doc is None` **或** `isLocked()` 都算「这份打不开」，
逐份报出来，不静默。

⚠️ **可审计是硬要求，不是加分项**（`RESEARCH-p3-community.md` §5.4 列了六种静默丢内容）：
每份文件都要能回答「读到了 N 个形状 / 跳过 M 个 / 多少页走了 OCR」。

⚠️ **抽到 0 字符要报错，不许生成空术语表。** 这是「静默失败」里最坏的一种。

### 5.2 候选词（新 prompt）

- **输入**：带 `kind` 标记的文本块。标题档（`ctrTitle` / `subTitle` / `title`）**给更高权重** ——
  真实课件实测标题占 15.6% 的字符（`RESEARCH-p3-extract.md` §5.1）。
- **判据**：逐字沿用 `SYS_CLASSIFY` 的「中英对照价值」（`build_notes.py:39-45`）。
- **两个阈值**：单字词 ≥2 次，多词短语 **≥1** 次 —— 「30% 的名词短语在语料里只出现一次」，
  单一阈值会把 `opportunity cost` 这类成批误杀。
- **显式排除**：纯机构/项目名、全大写标题词（D-Terminer 的 `BUILDING` 案例）、
  **多义词**（`organ` / `capital` / `bank` —— 经济学的重灾区）、
  以及**课号/教务词**（它们在 `SYS_CLASSIFY` 的判据里本来就是 `skip` 档，见 §4.1）。
- **输出**：严格 JSON —— **每个候选带一个置信度**，不只是词表。与 `_chat_json` 的
  `response_format: json_object` 对齐。

#### ⭐ 置信度必须由**这一次调用**给出

不是「再起一个模型给候选词打分」。理由：生成候选词的那次调用**已经读过全文**
（包括「这个词出现在哪一页的标题里」）；另起一个只看得到候选词、看不到全文的评判者，
是**用更少的信息做同一个判断**。在同一个 prompt 里多要一个字段，边际成本是零。

#### ⭐ 上限的**作用范围**要说清（作者 2026-09-26 指出方案里漏了）

| 问题 | 答案 |
|---|---|
| 40 是**每次调用**还是**每份文件**？ | ⭐ **每次调用**。作者原话是「每次导入设个上限」，而「一次导入」= 一次 `cl prep` 调用 |
| ⭐ 排序是**按文件顺序**还是**全局**？ | ⭐ **必须全局** —— 跨这次调用里的**全部文件**一起按置信度排 |

⚠️ **第二条是对作者那个担心的直接答案**：如果按文件顺序处理、逐份取前 40，
那么「一次拖一学期」时上限会被**最早那几份**吃光，后面的文件再有好词也进不来。
**全局排序让文件顺序完全不影响结果** —— 进前 40 的永远是这次调用里最好的 40 条。

⚠️ **但还要再加一个「每门课总量」的天花板**（作者没提，是实测逼出来的）：

**课程术语是「每句 prompt 全量注入」**（`translator.py:429-430`，理由见 `translator.py:175-180`）。
实测现有真实表的代价：

```
ECON10790  28 条  345 字符  ≈  86 token/句
ECON10770  35 条  461 字符  ≈ 115 token/句
SOC10020   45 条  564 字符  ≈ 141 token/句
```

→ **每多一条，每句话都要多付一次。** 一节课约 1000 句，`glossary` 从 45 涨到 500
就是每节课多 ~1.1M 输入 token。所以：

- **每次调用自动加 ≤ 40 条**（管复核负担）
- **每门课总量到软天花板（默认 120）就只提示、不再自动加**，要加得显式 `--more`
  （管 token 成本 —— 120 条约合现有最大表的 2.7 倍）

| | 条数 | 去哪 |
|---|---|---|
| **自动加入** | 全局排序**前 40 条**（按置信度 × 标题档权重 × slide spread） | 追加进 `glossary/<课号>.txt` |
| **未自动加入** | 其余的**全部保留**，不丢 | 打在那句提示的下面，标「置信度不足，要加的话从下面挑」 |

**为什么是 40**：现有真实 glossary 是 28–45 条（`ECON10770.txt` 35 / `SOC10020.txt` 45），
作者 2026-09-26 给的建议区间是 30–50。取中间值。

⚠️ **上限是「不做逐词确认」这个论证的承重墙**（作者 2026-09-26 指出）：
「回头删一行」之所以比「逐个确认」便宜，**前提是数量可控**。
一份高密度课件如果一次塞两百个进去，「回头删」这个动作本身就变得跟逐个确认一样烦
—— 只是把烦的时间挪到了事后。**所以超出的部分宁可标成「未加入」，也不自动灌进去。**

---

### 5.3 事后要有**主动的**一句，不能只留一张等人来翻

跑完打一句：`✅ 这次加了 23 个词`（不阻塞、不确认、纯提示）。
P6 的 A/B 是第二道闸门没错，但那是**以后、批量**的验证；
这句话管的是「这次导入完之后马上看一眼有没有明显不对」这种**即时纠错**。
留一张列表等人自己想起来去查，被看到的概率低得多。

### 5.4 ⭐ 文件名也是证据（作者 2026-09-26 指出，**已实现**）

第一次真跑时，`ECON10740__Library session for Stage 1 …`（一份**图书馆检索培训**）贡献了
一堆非经济学词混进前 40。只喂「课程名 / 领域」修掉了 8 个中的 7 个 —— 因为
**正文里那些材料的用词看起来一样「专业」**（`Boolean Operators`、`Field Searching`、
`Truncation` 确实逐字出现在那份 PDF 里）。

⭐ **而「Library session」几个字就在文件名里**，那是**免费、独立于内容**的第二条证据：
- 成本：0 —— `extract.py` 的 `path` 本来就有，只是要不要多传一个字符串进 prompt
- 实现：`_extract_user` 按文件分组拼正文（`### 文件：<名>`），**不新增抽取逻辑**
- 顺带解决了一个信息丢失：原来把所有块拼成一条流，**「这段文字来自哪份文件」就没了**，
  所以 `_chunks` 也改成了按文件打包

⚠️ prompt 里同时写了**反过拟合的一条**：`Week2_Communicating_Ideas_SLIDES_26_fin`
这种又长又带日期的名字是**正常的课程命名**，别因为「名字不像学术」就排掉它 ——
看的是**材料的性质**，不是名字好不好看。

### 5.5 ⚠️ `verified` **不管主题相关性** —— 有意为之

那道复核只问「**这个词在不在原文里**」，不问「跟这门课有没有关系」。
`Boolean Operators` 确实逐字出现，所以它**会**过 `verified`；拦不拦得住全看
`confidence` 这道**软排序**。

**不加主题匹配硬门槛**，两个理由：
1. 硬门槛会把**通用学术词**一起毙掉 —— `research question` 在一节经济学课里
   学生真碰到、真需要中文提示，毙掉是错的。真正该拦的是「**属于另一个专业领域的行话**」，
   那是**软信号**的活，不是字符串匹配的活。
2. 残留结果（8 → 1）已经画对了边界，为一个观察样本再加机制是**过拟合**。

⚠️ **边界**：**支持性材料的占比一大，软排序未必兜得住**（一学期塞三次图书馆培训，
频率信号还是会把它们往上顶）。到那时该加的是下面 §10 的**跨文件一致性**信号，
**不是**把 `verified` 收紧。

---

## 6. 定位与取舍

### 6.1 定位：这条回路在社区里是常见还是创新（作者要求的第 2 问）

**结论（已修正两次，这是定稿）**：

> ⭐ **在「机器从文档批量抽词 → 产出持久词表」这个格子里，我找到 6 个实现，
> 全部要求人工确认，反例 0。而且它横跨两个行业、两个层。**

⚠️ **我先前给的两条辩护都不成立，逐条撤回：**
- ❌ 「**先例全在 ASR 解码层，本项目在翻译层所以不受约束**」—— **错**。
  翻译行业（CAT/TMS）有整整一族同形态产品，**它们也全部要确认**。见 §6.1.2。
- ❌ 「**这是组合创新，社区没做过**」—— **错**。见下表。

**表一：词表作用在识别层**

| 产品 | 文档 → 词 | 人工确认 | 落在哪一层 |
|---|---|---|---|
| **Dragon**（Nuance，90 年代起） | ✅ `Learn from specific documents`：扫文档 + 「**Analyzes the frequency and order**」 | ✅ ⭐ **默认全部不勾选**（逐字 `All words are deselected by default`），配 Check All / Uncheck All | **改识别本身**（「make better guesses about my speech」）+ 要跑 **5–30 分钟 adaptation** |
| **CaptionHub**（2026-09） | ✅ `Generate terms from a source`：AI 抽 **PDF / Word / 纯文本**（单次 ≤25,000 字符） | ✅ 逐字「**review the suggested terms, select the ones you'd like to add and click Add accepted terms**」 | **改 ASR 输出**（`Mark as default` → **original caption transcriptions**） |
| Philips SpeechExec | ✅ 同名入口 | ✅ | ⚠️ **与 Dragon 同一套 Nuance 引擎 OEM**，不算第二个独立实现 |
| Teams / Webex / Vimeo / Apple Voice Control / Rev AI | ❌ 只吃**已整理好的清单** | — | ASR 词表 |
| Fathom | ❌ 明文拒绝 bulk import | — | — |
| Granola / Otter | ❌ 只能一个字一个字敲 | — | ASR 词表 |

**表二：词表作用在翻译层**（← 这一族是本次新增，它推翻了我原来的辩护）

| 产品 | 文档 → 词 | 人工确认 |
|---|---|---|
| **memoQ** | ✅ 逐字「memoQ can extract possible terms from **documents**, translation memories, and LiveDocs corpora」 | ✅ ⭐ **逐条 Accept / Drop** —— 逐字「Initially, the status of all candidates is **Candidate**」；「**Only accepted candidates are copied to the final term base**」；配 `Ctrl+Enter` 接受 / `Ctrl+D` 丢弃 |
| **SDL Trados / MultiTerm Extract** | ✅ | ✅ 「**Following review and validation**, term candidates become term lists」 |
| **Smartcat** | ✅（抽取在另一篇文档） | ✅ ⭐ FAQ 逐字：「Term suggestions **don't appear in the active glossary immediately**. They go to the **Suggested Terms tab where administrators can review and approve them before they become active**」；另有专文《**Glossary permissions and the term approval workflow**》 |
| Alconost AI glossary generator | ✅ `[未回源]` | ⚠️ 未核 |

⚠️ **引用从哪来**：子代理检索 + 我**逐条回源**。已自己核实原文的有：
CaptionHub 的 `review the suggested terms…`、Dragon 的 `All words are deselected by default`、
**memoQ 的 `Only accepted candidates are copied to the final term base`**（连同
`There may be a lot of garbage in the list` 那句）、**Smartcat 的 `Suggested Terms tab … approve`**。
EDM 2026（§6.1.1）我读了正文。
（子代理第一轮曾写「翻译层 + 机器抽词这个格子是空的」，**它自己第二轮撤回了** ——
原因是它没搜过 CAT/TMS 这条线。）

**三条结论**：

1. **「文档 → 词表」不新，而且「机器抽词 + 持久词表」这个格子有 6 个先例，全部要确认。**
   **自变量不是「作用在哪一层」**（两个层都要确认），
   **而是「这批词是不是机器批量生成的」** ——
   机器抽 → 全部要人过目（6 家，反例 0）；人手上传现成清单 → 全部直接生效（5 家，反例 0）。
2. ⚠️ **所以「不做逐词确认」是**反先例**的，而且我原来那句「反的顾虑在本项目不存在」也站不住**
   —— 「确认」在翻译行业是**术语管理的固有工序**（memoQ 明说要先清理那堆 garbage），
   不只是「ASR 概率被污染」那一种顾虑。
3. **本项目与那 6 家仍然有一处真实差异，但它是「代价」不是「层」**：
   它们的词表**带译文**（bilingual term base，错一条直接产出错的译文）、
   或**多用户组织级**（一条错词扩散到所有译员/项目/语言）；
   本项目的 `glossary/<课号>.txt` 是**单语、单人、纯英文词表**，
   错一条的代价 ≈ 多几行注入 prompt 的 token + 一行删除。
   → **这是辩护，不是先例。要如实这么写。**
   作者的完整设计（含「不做确认」）见 §6.1.3 的取舍，**这是作者的决定，方案按它执行**。

⭐ 顺带：**「事后可编辑」这一半是行业标配** —— 调研核到 **9 个产品全部支持事后删改，
其中 4 个有批量管理入口**（CaptionHub `bulk deleting`、Wispr Flow 多选批量删、
Apple Voice Control `Delete All Vocabulary`、Webex/Vimeo 下载 CSV 改后重传）。
CaptionHub 还建议「keeping your dictionary to fewer than **1,000** entries」——
与 §5.2 设上限是同一个方向。

#### 6.1.1 学界同款：EDM 2026（课堂材料 → LLM 事后改写）

**EDM 2026 short paper #112** ——《Improving Speech Recognition of Named Entities in
Classroom Speech with LLM Revision and Phonetic-Semantic Context》
（[原文](https://educationaldatamining.org/edm2026/proceedings/2026.EDM.short-papers.112/index.html)，
**本文作者自己回源读过正文，下面的数字都是原文表格里的**）

| | 内容 |
|---|---|
| **输入** | **课堂语音 + 这门课的讲义/幻灯片** —— 与 P3 同一个问题 |
| **落点** | ⭐ **LLM 事后改写 ASR 输出**，**不是**解码期偏置。原文明确与先前工作对比：「without needing to be trained on a specific NE list」 |
| **人工审查** | ⭐⭐ **没有。整条推理链全自动**，人只用于评测（500 句手工标注，量 Flair 的 precision 96% / recall 86%） |
| **做法** | Whisper large-v3 → Flair NER **两侧同时抽**（ASR 输出 + 上下文文档）→ **Double Metaphone 音码**过滤「听起来像」的 → LLM 拿着**过滤后的**实体 + 它们所在的句子改写 |
| **结果** | 实体 WER **32.3% → 22.7%**（相对 −30%），非实体 WER **不动**（7.0 → 7.0） |
| **数据** | NER-MIT-OpenCourseWare，45 小时 MIT 课程；测试集 17 小时（动物行为课） |

**三条直接可用的发现**：

1. ⚠️ **它支持的是「落点」，不是「确认闸门」—— 别混。**
   - ✅ **支持落点**：它证明「课堂材料 → 改善转写」这件事，学界选的也是
     **LLM 事后改写**、不是解码期偏置 —— 与你 brief §5.2 同向。
   - ❌ **不构成「不做逐词确认」的先例**：它**不产出任何持久词表**，
     只是对当前这句做一次性改写，用完即弃 —— **没有「错一条就长期留在那儿」那个对象**。
     它跟 P3 的 `glossary` **不是同一个东西**。
   - ⚠️ 而「**持久词表 + 文档来源**」这个形态的产品先例是 Dragon 与 CaptionHub，
     **2/2 都要求人工确认，反例 0**（补调研 Q3 给了分母）。
2. ⭐⭐ **过滤是必需的，不是优化。** 原文对照组给了硬数字：
   - 喂**完整上下文文档**：WER **从 32.3 涨到 43.4**（变差！）
   - 喂**上下文摘要**：**38.6**（gpt-4o 那档甚至 **54.2**）
   - 作者归因于**长 prompt 幻觉**
   → **P3 的落点必须是「过滤后的术语清单」，绝不是「把课件全文塞进提示词」。**
   这一条同时给 §5.2 的**上限**添了证据。（ClassLive 现有做法是注入 28–45 条的课程术语表
   ≈100 token，远不到「全文」那个量级，不冲突。）
3. **「不会帮倒忙」**：原文追测了「上下文文档里**没有**的实体」，WER 23.6%，
   和总体 22.7% 差不多、仍**远好于**只用 Whisper 的 32.3%。→ **加术语表即使没覆盖到，也不伤。**

⚠️ 另有一个数字值得记住（它是 P3 的前提能成立多少的量化答案）：
原文测试集里**真值命名实体有 66% 出现在幻灯片上**。
这就是「课件能覆盖多少难点」的上界参考 —— **约三分之二**。

⚠️ **还没核实的边界**：这篇的对照组是「全文档 / 摘要 / 无上下文」，
**没有「已整理好的术语清单」这一组** —— 所以它没有直接验证 ClassLive 的做法优于
「完全不注入」。这一条留作**未验证**，别当成已证。

#### 6.1.2 翻译行业那一族，多给了四条可直接用的东西

memoQ 的三页官方文档（`extract-candidates` / `term-extraction-editor` / `extract-terms`）
+ Smartcat 的术语页，**都已自己回源**：

1. ⭐ **「墓碑」有现成的名字：`stop word list`。** memoQ 逐字：
   「Choose if you want to add all the candidates you marked as **Dropped** to the current
   **stop word list**」；「You can prepare stop words in the Edit stop word list window…
   **You don't need to run term extraction to edit a stop word list**」。
   → ⭐ **这正是 §4.2 要的东西，在行业里是一等公民**（自己的 UI、自己的持久文件、
   能脱离抽取单独编辑）。**它验证了 §4.2 的判断，也给那个机制一个可以借用的名字。**
2. **§5.2 定的两条规矩，正好是它的两个默认旋钮。** 逐字：
   `Maximum length (words)`「Normally, it's **4**」；`Minimum frequency`「will not list
   candidates that do not occur … as many or more times as the number specified here」。
3. ⚠️ **「增量追加」不是本项目独有的范式 —— 我原先以为没有先例，错了。**
   Smartcat 的导入有两个选项，逐字：「**Replace all terms** overwrites all data currently in
   your glossary … **Add terms** adds new terms from the file to the existing glossary
   **without removing existing terms**」。
   → 行业里有名字、有实现（虽然它是**文件级批量**，不是逐词追加）。
   本项目的差异仍然存在（**与手写内容共处**，所以连重排都不许），但**不能说「没人这么干」**。
4. **先例的「确认」不是逐个弹窗，是勾完敲一下。** memoQ 用 `Ctrl+Enter` 接受、`Ctrl+D` 丢弃，
   候选表**默认全不勾**（初始 status 是 `Candidate`）。→ 摩擦确实很小。

#### 6.1.3 这条最终怎么记（口径定稿）

1. **事实**：机器从文档抽词 + 产出持久词表 —— **6 家先例，全部要人工确认，反例 0**，
   横跨识别层（Dragon / CaptionHub）与翻译层（memoQ / Trados / Smartcat）两个行业。
2. **差异**：那 6 家的词表**带译文**（bilingual term base，错一条直接产出错译文）、
   或**多用户组织级**；本项目是**单语、单人、纯英文词表** ——
   错一条的代价 ≈ 多几行注入 prompt 的 token + 一行删除。
   **这是辩护，不是先例。**
3. **执行**：按作者 2026-09-26 的决定 —— **默认全自动写入**，靠三根柱子兜底（§5.2 / §5.3）。
   ⚠️ 如果将来想改成先例同款，**最小的改法不是「逐条确认」，是「默认全选 + 敲一下回车」**
   —— memoQ 的接受键就是 `Ctrl+Enter`。那是**一次按键**，不是四十次。

### 6.2 核实：brief 的两条技术判断，回原文档逐字确认（作者要求的第 1 问）

原文档 = `~/Desktop/classlive-review/REVIEW-2026-09-24.md`（**不在仓库里**，作者决定不推）。

| brief 说的 | 原文档位置 | 逐字原文 |
|---|---|---|
| §5.1 塞进 ASR 热词偏置是死路 | **`:203`**（§5.2「已尝试并**否决**」） | 「ASR 热词偏置 … 热词权重 **2.0 不触发 / 8.0 输出崩坏 / 20.0 直接背热词表**」「**没有可用区间**。机理：**声学证据不支持那个词时，掰回来所需的偏置会大到压过声学**」 |
| §5.2 正确落点是 LLM 事后矫正 | **`:229`**（§5.3「已尝试并**采纳**」） | 「**LLM 事后按上下文矫正**（只对英文原文，**EN 为基准防倒退**）｜ 把 `max chocolate` 修回 `macroscopic`」 |

**两条都逐字对上，而且原文档给的机理比 brief 的转述更结实**（「压过声学」那句可以直接引）。
结论成立，范围也守得住：**热词偏置这条路不是「换个来源就能用」，是解码层的机制本身撑不住。**

⭐ **本次新发现一条支撑**（同文档 §5.2 的「三条结构性洞察」第 1 条）：

> 降噪/分离/滤波有一个共同缺陷：**先破坏信号、再交给 ASR**。而现代 ASR **是在带噪数据上训练的**，
> 它自己就在做这件事……「听起来干净」和「识别得准」是两回事，而且经常冲突。

**这和热词偏置是同一种失败形状** —— 都在**信息更少的上游**干预；
而下游（LLM 事后矫正）手里有「整句 + 领域先验 + 全量术语表」。
**P3 选下游，与这份文档已经得出的结论是同一个判断，不是新赌注。**

（顺带：同一条也是 §9 否决 Jev 的理由 —— 一个只看得到候选词、看不到全文的评判者，
就是在**信息更少的地方**下判断。）

### 6.3 取舍表

| # | 取舍 | 决定 | 反方 / 代价 |
|---|---|---|---|
| 1 | **不做逐词确认** | ✅ **作者定** | ⚠️ **这是反先例的，把分母说清**：在「**持久词表 + 文档来源**」这个形态里，找到的产品先例是 Dragon 与 CaptionHub，**2/2 都要求人工确认，反例 0**（补调研 Q3 给的分母）。EDM 2026（§6.1.1）落点相同、也全自动，但**它不产出持久词表**（所以不算这个形态的先例）。**辩护理由是「先例要防的代价本项目没有」**：它们的词表作用在 **ASR 解码层**（Dragon 还要跑 5–30 分钟 adaptation、错一条长期留在那儿），本项目在 **LLM 事后矫正层**（删一行即回滚）。⚠️ 成立**要靠三根柱子撑着**（§5.2 / §5.3）：**① 自动加上限 40 条 ② 超出的不丢、标成「未加入」 ③ 跑完主动提示一句**。少了任一根，就退化成「把逐个确认的烦挪到事后」。**写进方案是为了让它看起来像故意，不像漏了一道** |
| 2 | **独立进程**，不内嵌 | ✅ 作者定 | 内嵌能「录课时随手拖」，但要碰 `drain()`（**无 try/except**）、要做术语热重载、且拖放到浮动面板上没测过 |
| 3 | **扫描件走 OCR 兜底**，不拒绝 | ✅ 采纳 | ⚠️ **两份调研在这里冲突**：community 建议「明确拒绝」，extract 建议「OCR 兜底」。**裁决给 extract** —— 它实测过（Vision 零依赖、0.27s/页、噪声集中在虚词，真术语完好），而 community 的拒绝理由是「OCR 是另一个依赖面」，那条在 macOS 原生路线下**不成立**。但**采纳 community 的审计要求**（§5.1） |
| 4 | **不用 Docling** | ✅ 作者定 | 103 个依赖条目 + torch 121MB wheel，换一份**本来就自带文字层的** PDF 文本 |
| 5 | **不用 python-pptx** | ✅ 采纳 | stdlib 能拿到 `<p:ph type>`，**粒度还多两档** |
| 6 | **不新建数据管线** | ✅ 采纳 | 出口 ① 的生产者 `build_notes.build()` 已存在，而且**已经会读** `glossary/<课号>.txt`（`build_notes.py:208`）—— 写好词，那个出口白得 |
| 7 | **不做 watched folder** | ✅ 采纳（=不做） | Zotero 官方专门写页拒绝；还要额外承担「哪个 `.course` 在生效」的耦合 |
| 8 | **公共表 `glossary.txt` 只读不写** | ✅ 采纳 | 课号必须全列在那里，且发音极近 —— 手写、谨慎，不适合自动追加 |
| 9 | **新课程首行不猜课名** | ✅ 采纳 | 猜错比空着更糟（模型会被带偏） |
| 10 | **`~/.classlive` 是新约定** | ⚠️ 采纳，但记债 | 现有 7 个小状态文件全在**代码旁**（`with_name`）。`materials/` 是用户数据，不该住在 `git pull` 会动的安装目录里 —— 理由成立。但**先建一个 `paths.py`**，否则 P3 会成为**第 8 个**各自算路径的地方 |

---

## 7. 实现顺序（第一批）

每步的判据都是「跑哪条命令 + 看到什么」。

### 步 1 —— `extract.py` + 测试

- 做：三种输入 + 空页 OCR 兜底 + 审计数字。
- **判据**：新 `tests/test_extract.py` 全绿，且 **red-green 验过**。
  至少钉**六条**：
  ① PPTX 同段落 run 不粘（拿真实课件断言出现 `Communicating Ideas in Economics`
  而不是 `Ideas in EconomicsDr. Ciara Whelan`）
  ② `<p:ph type=title>` 被标成 title 档
  ③ 空文字层页走了 OCR
  ④ 全空 → 报错而不是返回空表
  ⑤ ⭐ **版式噪声**（作者 2026-09-26 要求）：拿 `ECON10790__Chapter 14.pptx` 当样本，断言
     **`sldNum`/`ftr`/`dt`/`pic` 里的文字不进候选范围**；且断言那条跑马灯
     `Ch 14: …` **被算出了正确的 slide spread**（46/47）—— 不是断言它「等于某个值」，
     是断言**这个信号被算出来并带下去了**（§5.1.1 的处置 ②）
  ⑥ ⭐ **打不开的文件不静默**：拿本机实测的三种坏输入（空 / 非 PDF / 截断）
     各断言一次 —— `doc is None` → 进 `failed` 且**出声**，不是贡献 0 字符了事
- 跑真实语料冒烟：`~/UCD` 五门课，总耗时应 ≈2s（本方案 §0.2 的基线）。

### 步 2 —— 追加写入器 + 测试

- 做：`append_terms(course, terms) -> (added, skipped)`。
- **判据**：新 `tests/test_glossary_append.py` 全绿，**red-green 验过**。钉：
  ① **`new.startswith(old)`** —— 已有字节逐字节不变，新内容只在末尾（**最强的那条**，§4.1）
  ② **含课号形态的行（`ECON10101`）连位置都没动** —— 让失败信息说人话
  ③ 导两次不产生第二份（幂等）④ 大小写不同算重复 ⑤ 原子写（tmp 后 `os.replace`）
  ⑥ **首行 `#` 块逐字不变**（用 `SOC10020.txt` 那份**两行** `#` 的当样本）
  ⑦ 签名里**拿不到**公共表的路径（`glossary.txt` 在结构上不可达）
- ⚠️ **测试必须隔离写端**（`CLAUDE.md` 硬规矩）：换掉真 glossary 的路径，
  绝不拿 `glossary/` 当真样本写。（那条规矩是 `term_notes.json` 41KB→4.8KB 换来的。）

### 步 3 —— 候选词 prompt

- 做：新 prompt + 分块 + 标题档加权 + **slide spread 与出现次数两个信号** +
  **置信度字段** + 全局排序 + 上限 40 + 每门课总量天花板 + 溢出清单。
- **判据是人工的，这里说清楚**：拿 `ECON10770` 一周的真课件跑一遍，
  **打印候选表给作者看**，他判断三件事：
  1. 这些词对不对、置信度排序合不合理、该进的和该在溢出清单里的分界对不对；
  2. ⭐ **跑马灯有没有被挡住** —— 拿 `ECON10790`（15 份都有 `Ch N: …`）跑一遍，
     看 `Ch 14` 这种前缀有没有进前 40；
  3. ⭐ **反过来也要看**：`ECON10790__Chapter 9`（整章讲 `elasticity`）里
     `elasticity` 有没有因为「几乎每页都出现」被误当跑马灯丢掉
     —— **这是不设硬频率上限（§5.1.1 处置 ③）的理由，要在这里验一次**。
- 没有自动判据能替代这一步 —— `RESEARCH-p3-community.md` §3.2 明确说
  benchmark 排名与用户评分会对不上。

### 步 4 —— `prep.py` 装配 + `cl prep`

- 做：`prepare()` 串起 1→2→3 + `build_notes.build(course)`；`cl` 加 case 分支；
  `usage()` 补一行；`instance_lock.is_held()` 与那句提示；`paths.py`。
- **判据**（每条都要看到具体输出）：
  - `cl prep <一周的真课件>` 端到端跑通，`glossary/<课号>.txt` 追加了、`term_notes.json` 长了；
  - **打出了那句主动提示**（`✅ 这次加了 N 个词`）+ 溢出清单（若超 40 条）；
  - ⚠️ **这不是「打印了就行」** —— 要断言提示里的 **N 等于文件实际新增的行数**。
    提示与实际不符比不提示更糟（会让人以为写进去了）；
  - **再跑一次，两个文件都不变**（幂等，端到端验）；
  - `cl prep` 在 `ClassLive.app` 已跑时给出「正在上课」那句提示；
  - **默认闸门全绿**：`ClassLive.app/Contents/MacOS/python tests/test_audit_regressions.py`；
  - **碰了面板类才要**跑 `tests/test_panel.py` —— 第一批不碰，所以不跑。

### 步 5 —— 修两处腐坏文档

`PLAN-notes-and-ui.md` §7.3 挂作废横幅；`PLAN-roadmap.md:55` 改掉「用已有的 `SYS_CLASSIFY`」。

---

## 8. 没测到的 / 风险（不许当结论用）

| 风险 | 状态 | 退路 |
|---|---|---|
| PDFKit 输出**跟着 macOS 版本变**，不可 pin | 已知 | 换 `pdfplumber` 只需改 `extract.py` 里抽 PDF 那一行 |
| `from Quartz import PDFKit` 是 **PyObjC 的打包细节**，不是 Apple 承诺 | 已知 | 那一行包 `try/except`，失败回落 `pdfplumber` |
| **拖拽投放到非激活浮动面板**能不能收 Finder 的拖拽 | [未测] | 第一批不做窗口，**第二批动手前先单独验这一条**；NSOpenPanel 是保底路径（作者要求，必须在） |
| LLM 抽出来的候选词质量 | 未验 | 步 3 的**人工判据**就是为它设的 |
| **加密 PDF 的 `isLocked()` 行为** | **[未测]** —— 本机 59 份真 PDF **加密 0 份**，没有样本。`isEncrypted()`/`isLocked()` 这两个 API 已确认存在 | 按保守做法：`doc is None` **或** `isLocked()` 都算「打不开」，逐份报出来（§5.1.3） |
| **别的学校的 PPTX 模板长什么样** | **本机语料只有一种模板**（`ftr`/`dt` 各 0 个，跑马灯在 `body` 里） | §5.1.1 的处置不依赖具体模板：显式排除 4 类占位符 + 喂 slide spread，两条都跟模板无关 |
| 扫描件 OCR 出的词对抽术语有没有帮助 | [未测] | 审计数字会告诉我们 OCR 贡献了多少；坏了就退回「拒绝扫描件」 |
| `build_notes.build()` 的 API 花费 | 未量 | 增量处理（只处理缺 level/detail 的，`build_notes.py:278/311/330`），重复跑很便宜 |

---

## 9. Jev 接不接（作者 2026-09-26 提出）

**结论：不接。理由不是额度**（你说有免费额度，那笔账我不算），**是形状对不上。**

Jev（TypeSafe，`jev-1.13.0`）是「系统一模型」——**只返回类型化决策**
（`Choice` ≤255 选项 / `Score` / `Noul` 0–1 概率），**不生成文本**。

| P3 里的判断 | 形状 | Jev 能不能做 |
|---|---|---|
| 哪些文件是课件 | 扩展名 | 规则 |
| 哪页要走 OCR | `.string() == ""` | 规则 |
| 这个候选词是不是已在表里 | 字符串比较 | 规则 |
| 这份课件属于哪门课 | `.course` | 规则 |
| **从散文里产出候选词表** | **生成** | ❌ **它生成不了** |
| 给候选词排序/过滤 | 判定 ✅ | 形状吻合，但**见下** |

**唯一形状吻合的那一步，恰恰是最不该外挂的一步**：生成候选词的那次调用
**已经读过全文**（知道这词出现在第 3 页的标题里、出现了几次）。
Jev 只看得到一份候选词列表，看不到原文 —— 那是**用更少的信息做同一个判断**。
而且「在同一个 prompt 里多要一个置信度字段」的边际成本**本来就是零**。

⚠️ 这条结论 `jev-decision-model-fit-for-owen.md` 里已经有过一版 **ClassLive 专项**
（2026-09-25，第 5 次复核），当时的措辞是「项目里 100% 的模型调用都是生成文本，
Jev 生成不了」。**P3 这个新角度不推翻它** —— 抽候选词仍然是生成任务。

⚠️ 还有一笔与能力无关的账：Jev 要新 API key + 2 个 env 字段，而
**cc-switch 每次切 provider 都会重建 `settings.json` 把它们抹掉**（已踩两次）。

**翻转条件**（与前五次一致）：出现「**高频重复、答案空间可穷举、且没有确定性替代**」的
判断时才值。P3 里没有这样的位置。

**如果还是想试**：只做 30 分钟对照实验 —— 拿一门真课件的候选词，
DeepSeek 自评的排序 vs Jev 打分的排序，看差多少。**不要写进链路。**

---

## 10. 第二批（面板窗口）的范围 —— 先记着，不在第一批

**必备件**（作者 2026-09-26 明确要求，**不因走哪条路消失**）：

1. **NSOpenPanel 文件选择器** —— 拖拽是主要交互，但总有人不知道能拖、或就是想点开浏览。
   Apple HIG 也把它写成硬要求（「Offer alternative ways to accomplish drag-and-drop actions」）。
2. ⭐ **「这份文件不参与抽词」的开关**（作者 2026-09-26 要求）
   —— 图书馆培训、新生导览、选修课的客座讲座，这类「这门课里混进来的非核心材料」
   是个**会反复出现的文件类型**，不是这次撞见的特例。
   **语义**：**照常归档进 `materials/`，只是不参与候选词抽取。**
   它「不聪明但确定、零误判风险」，对这类情况**比任何启发式都可靠**。
   ✅ **CLI 侧已经先用上了**：`cl prep --skip '*Library*'`（可多次，glob）。
3. **拖拽投放**（`registerForDraggedTypes_` / `draggingEntered_` / `performDragOperation_`）
   —— `panel.py` 今天**完全没有**这类支持（全仓 grep 零命中），且**必须经 `objc_own.own()`**。
4. 「这次从课件里加了这些词」的可删列表。
5. 进度区（照 `whatsnew.build()` 的返回形状：`set_status` / `done` 回调，调用方在后台线程跑）。
6. 打开面板的按钮文案用**任务动词**，不用默认的 `Open`。

**动手前先验两条**（都写清方法了）：
1. 这个窗口能不能收到 Finder 的拖拽。收不到就先只做 NSOpenPanel。
2. ✅ **面板在 app 不激活时材质还在不在 —— 已量（2026-09-26），没问题但依赖一行代码**：
   `glass.state()` 是 `Active` 时离屏均亮 **57.4**，换成 AppKit 默认的
   `FollowsWindowActiveState` 就掉到 **24.7**（**2.3 倍**）—— 而上课时 app 一直不激活。
   结论与限制都记在 `docs/RESEARCH-macos-aesthetic.md` §3。

⭐ **视觉语言规格另有一份**：`docs/RESEARCH-macos-aesthetic.md`
（同心圆角公式 / macOS 字号字距表 / 对比度硬门槛 4.5:1 / 材质三条硬规则 /
「一眼看出不是原生」自查表 / 以及**我们自己量 Spotlight 与控制中心几何的方法**）。

### ⭐ 记一笔：跨文件一致性（**现在不实现**，等材料攒够）

一门课的材料到五六份之后，多出一条**不花额外成本、但会随材料积累自然变强**的信号：

> 某个词只出现在**一份**文件里，而且那份文件的**整体用词风格**跟其它几份明显不一样。

第一次 `cl prep` 往往只有一两份文件，**没有「其它文件」可比**，所以现在用不上。
但它正是 §5.5 那条边界的解药（支持性材料占比一大、软排序兜不住时）——
到时候加在**排序信号**里，而不是往 `verified` 加硬门槛。
