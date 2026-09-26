# P3 接缝调研：「开课前的准备」到底插在哪里

> 调研目的：把 P3 的**代码接缝**钉到 `file:line`。
> 只读不改 —— 本文档不动任何现有文件。
> 代码状态：HEAD = `1d1d461`（面板判据换成「同进程跟冻住的老配方对拍」），工作区干净。
> 日期 2026-09-26。
>
> ⚠️ **凡与路线图冲突的，以代码为准。** 本文档标出**两处**路线图与代码不一致（§3.2、§5.2）。

---

## 0. 结论先行

| 问题 | 答案 | 关键依据 |
|---|---|---|
| 插在哪 | **一个 UI 无关的 `prep.py` 模块**（新文件），CLI 与面板是它的**两个适配器** | `docs/RESEARCH-entry-and-export.md:294`「一个适配器是假缝，两个是真缝」 |
| `term_notes.json` 谁产 | ✅ **已存在** = `build_notes.build()` | `build_notes.py:242` |
| `glossary/<课号>.txt` 谁产 | ❌ **全仓没有任何写入方**（已 grep 证实） | 见 §3.1 |
| 文本抽取（PDF/PPTX） | ❌ **完全没有**，依赖也没有 | `requirements.txt` 无 pdf/pptx/docling |
| 「抽候选词」的 prompt | ❌ **不存在** —— `SYS_CLASSIFY` 只做分类，不做抽取 | `build_notes.py:38`、`283` ⚠️ 见 §3.2 |
| `panel.py` 缺什么 | 尺寸/自适应高度、滚动视图、按钮/label 辅助、**拖拽投放** | `panel.py:178`、§4 |
| 数据目录 | 现有约定是「小状态文件放**代码旁**」；`~/.classlive` 是**新约定** | §5 |
| 长任务怎么跑 | `daemon` 线程 + `queue` + 回 `streamq` + **`drain()` 加分支** | `main.py:1131`、`main.py:1239`、`CLAUDE.md:94` |

---

## 1. 插入点：真实的选项，以及每个要改哪一行

先看清「今天是怎么起来的」，因为 P3 的语义是**开课之前**，而现有进程一启动就直接进入上课。

**三条启动路径，终点都是同一个 `cl`：**

| 路径 | 到哪 | 依据 |
|---|---|---|
| 双击 `.app` | `cl`（**零参数**） | `cl-bg.py:56-63` 显式复用 `cl`：「不做第二份拷贝 —— 否则"两处各记一次"迟早漂移」 |
| 终端 `cl` | `cl` 零参数分支 | `cl:171` `""` → 什么都不加 → `cl:183` exec `main.py --source mic --ui overlay` |
| `cl file/online/local/test` | `cl` 相应分支 | `cl:155-170` |

**而 `cl` 零参数 = 直接上课**：`main.run()` 的第三件事就是开音源（`main.py:790-801`），
第四件是加载两个 ASR 模型（`main.py:805-811`）。**中间没有任何「准备」的位置。**

### 选项 A —— `cl` 子命令（`cl prep`）

| 要改 | 行 |
|---|---|
| `case` 加分支 | `cl:136-173`（`course` 在 `139`、`doctor` 在 `163`、`update` 在 `164` 是同族先例）|
| `usage()` 文档 | `cl:52-73` |
| 若走 python 侧 | 新 `prep.py` 的 `__main__`，照 `build_notes.py:589-591` 的先例 |

**优点**：零新概念，`cl course` / `cl doctor` / `cl materials` 已经是这个形状；
`--ui terminal` 时没有面板，这是**唯一**能走的路（`docs/RESEARCH-entry-and-export.md:310` 明写）。
**缺点**：收不到拖拽，而 P3 的用户动作就是「**拖** syllabus / 课件进来」（路线图 `docs/PLAN-roadmap.md:37`）。

⚠️ **`cl prep` 会撞单实例锁**：`main.run()` 第一件事就是拿锁（`main.py:745`），
锁是**全局单实例**、不是按用途分的（`instance_lock.py:34-35` 只有 `instance.lock` 一个名字）。
所以「放录音的同时跑 prep」这条路**关着**。这条是实测的代码事实，不是推断。

### 选项 B —— 菜单栏菜单项

`overlay.py:637-670` 已经有一个常驻状态栏图标 🎧，带一个 `NSMenu`（穿透开关 + 退出）。
加一项「开课前准备…」= 改一处、成本最小，而且它**物理上就在字幕面板旁边**。

⚠️ 但 `docs/RESEARCH-entry-and-export.md:361` 的反模式护栏第 1 条写：
「**别做菜单栏常驻当入口** —— HIG 明说系统会隐藏它」。那条护栏针对的是「**没有窗口时**的入口」；
这里的菜单项是**面板已经起来了**之后的第二入口，与那条护栏不直接冲突。**但这是个判断，不是实测。**

### 选项 C —— 复用「更新卡片」那条路（`whatsnew` 的形状）

这是**仓库里已经跑通的「启动时弹一张非模态磨砂卡片」**：

| 步骤 | 行 |
|---|---|
| `run()` 里判断要不要弹 | `main.py:785` `whatsnew = _maybe_notice_update()` |
| 交给 UI 层弹（终端印框 / 悬浮窗弹卡片） | `main.py:896-909` |
| 悬浮窗实现 | `overlay.py:1232-1284`，`whatsnew.build(...)` 在 `overlay.py:1249` |
| 卡片本体 | `whatsnew.py:152-314` |

**这是最有价值的先例**，因为它已经解决了 P3 要面对的三个问题：
非模态不抢焦点（`whatsnew.py:309` 用 `orderFrontRegardless()` 而**不是** `makeKeyAndOrderFront`）、
后台任务的进度回写（`whatsnew.py:159-161` 的 `on_update(set_status, set_title, done)`
**由调用方在后台线程跑**）、fail-soft 与 `ObjcNameCollision` 的分野（`whatsnew.py:315-321`）。
**照它加一个「准备面板」的改点**：`main.py:896`（多一个参数）+ `overlay.py:1232` 旁多一个
`_show_prep_panel()`——**不动 `run()` 前面那段启动序列**，所以不碰音源/模型的顺序。

### 选项 D —— `.app` 首次运行

⚠️ **这个选项不存在。** 双击 `.app` 走的是 `cl-bg.py:63` 的 `execv("/bin/bash", ... "$CLASSLIVE_HERE/cl")`，
**零参数**，与终端敲 `cl` 完全同一条路。没有「首次运行」分支可挂。
要做「首次运行引导」，得先**发明**一个判据（比如「`glossary/` 里没有当前 `.course`」），
而这个判据目前不存在。**这一项记为「未确认」以外的负结论：无现成插入点。**

---

## 2. 运行时术语流水线：读到代码，不是读到路线图

### 2.1 两个数据文件与它们的读写纪律

| 文件 | 定义点 | 语义 |
|---|---|---|
| `term_notes.json` | `build_notes.py:23` `NOTES_FILE = HERE / "term_notes.json"` | 人工/付费构建，curated |
| `term_notes_auto.json` | `build_notes.py:24` `AUTO_FILE = HERE / "term_notes_auto.json"` | 运行时缓存，**可整文件删除** |

⚠️ 这里是一整套**事故换来的纪律**，P3 若碰这两个文件必须遵守：

- `load_raw()`（`build_notes.py:77`）**区分「不存在」与「损坏」**：不存在 → `{}`；
  **存在但读不出 → 抛 `ValueError`**（`89-101`）。旧写法两者都 `return {}`，
  而 `build()` 拿到空表会**无条件覆盖写回** → 206 条付费术语被静默清空。
- `save_to()`（`144`）覆盖前先留 `.bak`（`155-163`），备份**失败要出声**（`163`）。
- `_atomic_write()`（`167`）先写同目录 tmp 再 `os.replace`。
- `build()` **显式传参**而不用 `load_raw()` 的无参默认值（`250-255` 的注释）：
  默认值在**函数定义时**绑定，与调用时的 `save_to(NOTES_FILE)` 可能不同文件 ——
  而那正是把 `term_notes.json` 从 41KB 写成 4.8KB 的机制。

### 2.2 `TermNotes` 的真实签名（运行时查表）

```
build_notes.py:488   class TermNotes:
build_notes.py:494       def __init__(self, path: pathlib.Path = NOTES_FILE)
build_notes.py:539       def known(self) -> set
build_notes.py:542       def add(self, term: str, note: str, type_: str = "") -> None
build_notes.py:565       def match(self, sentence: str, terms=None, max_n: int = 1)
build_notes.py:583   def format_gloss(hits) -> str
```

- `__init__` 已支持**自定义 path**（`494-501`）：传非默认路径时，
  auto 文件自动落到 `<path 同级>/term_notes_auto.json`。**这是 P3 做「每课一份」唯一的现成钩子。**
- 合并优先级：`merged.update(self._curated)` —— **curated 覆盖 auto**（`522`）。
- 只有 `gloss` / `basic` 进匹配索引，`skip` 永不显示（`529-533`）；**长术语优先**（`536`）。
- 两份文件**分别**加载、各自失败各自清空（`507-513`）—— 不许合并进一个 `try`。

⚠️ **今天它是全局的，不按课分**：`main.py:845` `notes = TermNotes()` —— **零参数**。
所以 `term_notes_auto.json` 是整个安装共享的一份。
P3 若要每课独立，改点就是这一行（传 path）+ 决定文件放哪（§5）。

### 2.3 四个 prompt，各干什么（原文引用）

| 常量 | 行 | 输入 | 输出 |
|---|---|---|---|
| `SYS_CLASSIFY` | `build_notes.py:38` | **一个术语列表**（`283` 是 `"\n".join(chunk)`）| `{"术语": "skip\|basic\|gloss"}` |
| `SYS_EXPAND_SHORT` | `48` | 术语列表 | `{"term": "一行中文≤30字"}` |
| `SYS_EXPAND` | `57` | 术语列表 | `{"term": "80-160字解析"}` |
| `SYS_LOOKUP` | `65` | **单个**词/短语 | `{"known","type","note"}` |

三档的判据是**作者自己定的**，逐字在 `build_notes.py:39-45`：

> 判断标准是**中英对照价值**, 不是"这个概念难不难"

### 2.4 `build()` —— 离线批处理，**这就是 term_notes.json 的生产者**

```
build_notes.py:242   def build(course=None, api_key=None, model="deepseek-flash", rebuild=False)
build_notes.py:259       targets = collect_terms(course)      # ← 已含该课 glossary
build_notes.py:278-306   分类循环 (BATCH_CLASSIFY=25)
build_notes.py:311-326   gloss 展开循环 (BATCH_EXPAND=8)
build_notes.py:330-345   basic 短释循环
build_notes.py:350       save_to(NOTES_FILE, {...}, terms, extra)
```

而 `collect_terms()`（`206-217`）**已经把 `glossary/<课号>.txt` 算进去了**：
`terms = load_terms(glossary.txt, course)`（`208`），且 **`course=None` 时会 `glob` 遍历
`glossary/*.txt` 把每门课都算一遍**（`209-211`）—— 对 P3 意味着「不加课号跑 `build_notes.py`」
= **给全部课程**建笔记。P3 必须显式传 course。

### 2.5 术语表怎么进翻译 prompt（两个消费者，不是一条路）

**注意：`TermNotes` 与 glossary 是两条互不相干的线。** `TermNotes` 不读 glossary。
glossary 的读取全在 `translator.py`：

| 函数 | 行 | 作用 |
|---|---|---|
| `_load_terms` | `73` | 逐行读，丢空行与 `#` 开头（`89-90`）；**读不出只出声，不抛**（`86-88`）|
| `course_terms_path` | `93` | 定位 `glossary/<课号>.txt`，**精确路径不存在时按后缀模糊匹配**（`103-109`）|
| `load_terms` / `course_term_list` / `course_title` | `112` / `128` / `136` | 公共+分课表 / 只取分课表 / 取**第一行**当**领域先验** |
| `core_terms` / `select_terms` | `163` / `171` | 常驻核心词+课号 / 核心常驻 + **课程术语全量** + 动态召回 |

**注入点**（本地与云端各一处）：`translator.py:384-387` 存字段 →
`translator.py:429-430` 与 `cloud_translator.py:245-247` 调 `select_terms(...,
always=self._course_terms)` → 拼进 `user`（`cloud_translator.py:254/281`）；
问答路径额外注入全文（`cloud_translator.py:329-333`）。

⚠️ **课程术语是「全量注入」**，理由逐字在 `translator.py:175-180`：
纯按匹配筛选会**死循环**（ASR 听错 → 匹配不到 → 不注入 → 没有领域先验 → 永远修不回来）。
**代价**：`glossary/<课号>.txt` 每多一条，**每句 prompt 都多一份 token**。
现有课表 28–45 条（`docs/RESEARCH-product-shape.md:193` 实测）—— 这是**天花板，不是起点**。

⚠️ **`course_title` 的领域先验来自文件第一行**（`translator.py:148-154`）。见 §5.2 的 ⚠️。

---

## 3. 已有 vs 缺什么

### 3.1 两个出口，一个已有生产者、一个完全没有

| 出口 | 生产者 | 证据 |
|---|---|---|
| `term_notes.json` | ✅ `build_notes.build()` | `build_notes.py:242`，`__main__` 在 `589-591` |
| `glossary/<课号>.txt` | ❌ **无** | 全仓 grep `glossary` 的**写**操作 → 零命中（`obsidian_writer.py:326` 的 `_glossary` 是拼笔记里的术语小节的**同名方法**，无关）|

`cl course` 只写 `.course`（`cl:154` `printf '%s' "$COURSE_ARG" > "$CFG"`），**不碰 glossary**。

### 3.2 ⚠️ 路线图说「用已有的 `SYS_CLASSIFY` 抽候选词」—— 这句不成立

`docs/PLAN-roadmap.md:55` 写的是 `↓ LLM 抽候选词（用已有的 SYS_CLASSIFY）`。

**读代码：`SYS_CLASSIFY` 的输入是一个已经存在的术语列表**（`build_notes.py:283`
`"\n".join(chunk)`，`chunk` 来自 `todo_cls`，而 `todo_cls` 来自 `terms` 字典的键，
`terms` 又来自 `collect_terms()` 读**已有文件**）。它**从散文里抽不出词**。

同一份仓库里的另一份调研**说对了**（`docs/RESEARCH-product-shape.md:189-190`）：
「**缺的只是「把课件当候选词源喂进去」**」。

→ **P3 需要一个新 prompt（或新启发式）做「正文 → 候选词」这一步。** 可复用的只有：
`_chat_json()` 这个请求封装（`build_notes.py:222`，含 `response_format: json_object`、
`thinking: disabled`、`timeout=180`）、三档分类法、以及 §2.1 的落盘纪律。

### 3.3 文本抽取：完全没有，且没有依赖

- `requirements.txt` 里**没有任何** pdf / pptx / docling 条目（已通读全文）。
- 全仓 grep `docling|pypdf|pdfplumber|python-pptx|pymupdf` → 只在 `docs/*.md` 里出现，**代码零命中**。
- `materials.example/README.md` 自己承认：「PDF / PPTX / DOCX 的自动转换**还没做**」，
  并且只认 `.md`。
- Docling 的选型与代价已经在两份文档里定过：`docs/PLAN-notes-and-ui.md:463-475`（选 Docling，
  MIT，但要 ~258M 模型 + 进 requirements）、`docs/RESEARCH-product-shape.md:148-151`
  （100 页 PDF >10 分钟、3-4 GB 内存尖峰 → **课前批处理**，不上 VPS）。

### 3.4 小结：P3 要**新写**的三件东西

1. **正文 → 候选词**的提取 prompt + 调用（新）
2. **`glossary/<课号>.txt` 的追加写入器**（新）—— 且必须遵守 §5.2 的追加语义
3. **文档 → 纯文本**（新，且要新增依赖）

能**复用**的三件：`build_notes.build()`（直接吃 glossary 产 term_notes）、
`_chat_json`、`panel.py`。

---

## 4. `panel.py` 的第三个消费者：`entry_panel.py` 需要而 `build()` 不提供的东西

### 4.1 现在的接口（逐字）

```python
panel.py:178   def build(rect, style, *, on_background_click=None, on_resize=None) -> FrostedPanel
panel.py:163   class FrostedPanel(typing.NamedTuple):
                   window / glass / scrim / drag / resize_delegate
panel.py:243   def hide_traffic_lights(window) -> None
```

`build()` 提供的**只有 chrome**：appearance（`204`）→ 窗口属性（`205-212`）
→ `glass`（`215-221`）→ `scrim`（`225-229`）→ `drag`（`231-233`）→ 可选 delegate（`236-238`）。
docstring 明写「顺序是**载荷性的**，别调」（`183`）。

### 4.2 六个缺口，逐条对照

| 缺口 | 现状 | 一个准备面板要加什么 |
|---|---|---|
| **① 没有尺寸/自适应高度** | 高度由调用方**算好**再传 `rect`：`whatsnew.py:175-176` 自己算 `body_h` / `h` | 面板高度随「拖进来几个文件」变 → 每次都要 `window.setFrame_`（先例 `overlay.py:898`）|
| **② 没有滚动视图** | `whatsnew.py:104` 有一份**手搓的** `_add_log_view`；`transcript_view.py:122-133` 是另一份 | 文件列表 + 进度日志要能滚 → 要么借 `whatsnew` 那份，要么**抽进 `panel.py`**（否则这是**第三份**手搓滚动视图）|
| **③ 没有按钮/label 辅助** | `whatsnew.py:186` 本地 `label()`、`whatsnew.py:332` `_make_target()` | 要按钮就得再抄一遍 → 同②，是「第三份」风险 |
| **④ 没有任何拖拽投放支持** | 全仓 grep `registerForDraggedTypes\|draggingEntered\|performDragOperation` → **零命中** | P3 的核心动作是**拖文件进来** → 必须新写一个 `NSView` 子类，**且必须走 `objc_own.own()`**（`objc_own.py:47`）拿类名 |
| **⑤ `FrostedPanel` 没有 `fit()`** | 确认：`163-176` 的 NamedTuple 只有 5 个字段，没有任何尺寸方法 | 「内容长高了自动长」要自己写；`on_resize` 只管**用户拖窗口**时的重排（`panel.py:130-160`）|
| **⑥ 不是「按键」接口** | `canBecomeKeyWindow -> True` 已经给了（`panel.py:77`）| 这一条**已经够用** ✅ —— 但 `docs/RESEARCH-entry-and-export.md:377` 仍把它列进验收清单，说明没实测过 |

### 4.3 ⚠️ 真正该抄的不是「画法」，是 `whatsnew.build()` 的**返回形状**

```python
whatsnew.py:310-314
return {"panel": p, "close": on_close, "set_status": set_status,
        "set_update_title": ..., "set_update_enabled": ..., "has_update_button": ...}
```

配 `whatsnew.py:159-161` 的约定：`on_update(set_status, set_title, done)`
**由调用方在后台线程里跑**，跑完用这三个回调把进度写回卡片（原文：「绝不在主线程做网络」）。

**这就是准备面板要的形状**，一字不差：「正在转换 3/12…」+ 一个按钮 + 一个复选框。
`docs/RESEARCH-entry-and-export.md:298-303` 已经指出这一点（「这不是巧合 —— 它就是入口面板的原型」），
**读代码后确认这个判断是对的**。

### 4.4 一条硬约束

⚠️ 每个面板的 ObjC 类名必须**全局唯一** —— 这不是风格，是**已发生过的事故**
（`panel.py:8-21`、`objc_own.py:4-14`：`_Panel` 撞名 → PyObjC 静默覆盖 → `build()` 返回 `None`
→ **更新卡片根本不显示，而独立测试全绿**）。
→ 准备面板的所有 `NSView` / `NSWindow` 子类**一律**经 `objc_own.own(key, base, ns)`（`objc_own.py:47`），
**自带命名权的写法一律禁止**。且 `ObjcNameCollision` **绝不能被 fail-soft 吞掉**（`objc_own.py:22-27`、`whatsnew.py:315-321`）。

---

## 5. 数据目录：`~/.classlive` 是**新约定**，不是现有约定

### 5.1 现状：小状态文件一律放**代码旁边**（= 安装目录）

| 文件 | 定义点 | 落哪 |
|---|---|---|
| `.course` | `cl:45` `CFG=".course"`，写在 `cl:154` | 安装目录（`cl:22` 已 `cd` 过去）|
| `.window` | `overlay.py:133` `pathlib.Path(__file__).with_name(".window")` | 仓库/安装目录 |
| `.deepseek_key` | `cloud_translator.py:134` `with_name(".deepseek_key")` | 同上 |
| `.update-seen` / `.update-skip` | `main.py:557-559`、`whatsnew.py:353/362` | 同上 |
| `term_notes*.json` | `build_notes.py:23-24` `HERE / ...` | 同上 |
| `glossary/` | `translator.py:101` `pathlib.Path(glossary_path).parent / "glossary"` | 跟着 `glossary.txt`（仓库根）|
| `sessions/` | `obsidian_writer.py:30` `Path(__file__).with_name("sessions")` | 同上 |

**只有三个例外**，而且都是「大块或系统惯例」：`~/models/`（`main.py:1409,1419`）、
`~/Library/Logs/ClassLive/`（`cl-bg.py:23` 日志、`instance_lock.py:34` **锁也在这**）、
`~/Obsidian/Vault`（`obsidian_writer.py:28` `DEFAULT_VAULT`）。

⚠️ `overlay.py:131` 的注释把这条约定写明了：
「与 `.course` / `.deepseek_key` **同级同风格**（各自模块管自己的小文件）」。

### 5.2 结论与两个必须认账的代价

**`~/.classlive/`（`docs/PLAN-roadmap.md:137`、`docs/RESEARCH-entry-and-export.md:320`）
与现有 7 个文件的做法不一致** —— 它是**新引入的约定**，不是「沿用」。

它**有正当理由**：`materials/` 是**用户数据**，不该住在会被 `git pull` 更新的安装目录里
（`cl update` 的拉代码在 `update.py`，`cl:102`；`ClassLive.app/Contents/` 是运行环境，见 `CLAUDE.md:100`）。
但代价要写下来：

1. **今天没有「路径的唯一真源」**。七个文件各自用 `with_name` 算自己的路径。
   引入 `~/.classlive` 之前应该先有**一个**定义点（一个 `paths.py` 或类似），
   否则 P3 会变成**第八个**各自算路径的地方。
   （`CLAUDE.md:92` 对同类问题有现成的判据：「判据只在**一份实现**里；别在 `install.sh` / `update.py` 各算一遍」。）
2. **`sessions/` 绝不能动**（`CLAUDE.md:108`、`docs/RESEARCH-entry-and-export.md:328-330`）：
   它是三方共享契约（`obsidian_writer` 写 / `_parse` 读回 / `cl last` grep，`CLAUDE.md:95`）。

⚠️ **另有一条与 P3 直接相关的路径陷阱**：`course_terms_path`（`translator.py:93-109`）
用**后缀匹配**兜底，并且 `course_title`（`136-160`）把**文件第一行**当领域先验。
→ 追加写入器**必须**：
- 文件名仍是 `<课号>.txt`（否则后缀匹配落空 → 静默退回只用公共表，`translator.py:96-98` 记录过这个实测）；
- **保留第一行注释**（否则领域先验丢失 → 模型不再知道「这是热力学课」，
  `translator.py:141-142` 说这正是它存在的理由）。

---

## 6. 线程与阻塞：长任务必须长成什么样

### 6.1 不变量（两条，缺一不可）

`CLAUDE.md:74`：

> **不阻塞不变量**：`capture` / `vad` 的回调必须立刻返回；AppKit 的调用只能发生在主线程。

**代码里怎么兑现的**（不是靠约定，是靠结构）：

| 位置 | 做法 |
|---|---|
| `capture.py:3-4` | 对下游暴露**可轮询**的源：`poll()` 非阻塞，「永不阻塞在 I/O 上」|
| `capture.py:119-131` | 声卡回调 `_cb` 只做 `queue.put_nowait`（满了丢最旧的）|
| `vad.py:238` / `159` | `on_partial` / `on_utterance_end` 在 `accept()` 里同步调用 —— 而 `accept()` 是**主循环**调的 |
| `main.py:1126-1127` | 两个回调**只往队列里塞**：`on_partial=lambda buf: latest.put(buf)`、`on_utterance_end=lambda buf: finalq.put(buf)` |
| `main.py:1285-1297` | 主循环：`poll()` → `accept()` → `drain()` → `ui.pump()` |

### 6.2 现有的「后台跑长活」唯一模式

**最贴近 P3 的形状**（`proper_worker`，`main.py:1131-1152`）：线程由
`main.py:1213-1219` 起（`daemon=True`）→ 从自己的队列取 → 跑网络（`1145` `lookup_term`）
→ 落盘（`1147`）→ **`streamq.put(("terms", ...))` 回主线程**（`1148`）。

四条纪律，逐条都有实测依据：

1. **daemon=True**，且只认自己的队列（`main.py:1155`「形状照抄 proper_worker」）。
2. **结果一律经 `streamq`**，由主线程的 `drain()` 分发给 UI（`main.py:865` 的注释把 tag 列全了；
   `main.py:870-872` 的 `notify()` 就是这个模式的正式版）。
3. ⚠️ **绝不碰 `busy` / `finalq` / `carry`** —— 那三个是收尾闸门 `all_settled()` 的输入
   （`main.py:1156-1159`）。碰了 = 丢最后一句或让收尾卡满 15s。
4. ⚠️ **单条失败不能弄死线程**（`main.py:1149-1152`：线程一死，之后**所有**同类任务静默停摆）。

### 6.3 所以「抽文本 + 调 LLM」必须这么写

```
① 面板回调（主线程）只做一件事：把「用户选了这些文件」塞进一个 queue
② 一个 daemon 线程从 queue 里取，跑 Docling + LLM 提取（网络/CPU 都在这里）
③ 进度与结果 → streamq.put(("prep", ...)) / ("prep_done", ...)
④ 主循环 drain() 里加分支，把 tag 分发给面板
```

⚠️ **第 ④ 步是硬规矩**，`CLAUDE.md:94` 逐字：

> **新增 streamq tag 必须在 `main.drain()` 加同分支** —— 它是唯一的 tag 分发点，漏改即静默丢弃。

⚠️ **`drain()` 没有 try/except**（`main.py:1239-1282`），一个异常直接打死主循环。
新分支必须照 `main.py:1269-1271` 的 `getattr` 守卫写法（那是为「UI 还没有这个方法」加的）。

⚠️ **两条额外的、P3 特有的**：

1. **Docling 是 CPU 密集的。** 若准备动作与上课**同进程**跑，GIL 会饿死主循环
   （`capture.py` 那套只保证「不阻塞在 I/O」，保证不了「不抢 CPU」）。
   → 「同进程线程」只在**课前**（主循环还没起）成立；上课中跑就得是**独立进程**。
   而独立进程会撞单实例锁（`instance_lock.py:34-35`，见 §1 选项 A 的 ⚠️）。
   **这是 P3 最硬的一个结构性矛盾，必须在设计阶段就定，不能留到实现。**
2. **改了 glossary 不会立刻生效。** `load_terms` 只在 `Translator.__init__` 调一次
   （`translator.py:384-387`、`cloud_translator.py:373-376`），而 `Translator` 在 `main.py:812` 构造。
   → 课前准备生成的词，**下一次启动**才进 prompt。这条要写进 UI 文案，否则用户会以为白干了。

---

## 7. 完成判据：动这块要过哪些闸门

来自 `CLAUDE.md:121-150`，按 P3 实际会碰的面**逐条筛**：

| 闸门 | 何时必跑 | 命令 |
|---|---|---|
| **默认闸门** | **任何**改动 | `… tests/test_audit_regressions.py`（`CLAUDE.md:123`）|
| **`tests/test_panel.py`** | 碰 `panel.py` / 面板构造（P3 面板**必然**碰）| `… tests/test_panel.py`（`CLAUDE.md:132`）|
| `tests/test_instance_lock.py` | 若动 `main.run()` 开头或新增独立入口 | `… tests/test_instance_lock.py`（`CLAUDE.md:127-128`）|
| `probe_scroll.py` | 若准备面板**带滚动**（§4.2 ②） | `… probe_scroll.py`（`CLAUDE.md:145`）|
| `test_pipeline.py` | 若碰翻译/术语注入路径（P3 改 glossary → **会**碰）| `… test_pipeline.py <音频>`（`CLAUDE.md:131`）|

（`tests/test_update.py` 只在碰更新机制时跑，与 P3 无关。）
路径前缀一律是 `ClassLive.app/Contents/MacOS/python`。

⚠️ **`test_panel.py` 的两条性质必须先知道**（`CLAUDE.md:136-144`）：

- 判据是**同进程跟「抽取前的配方」对拍**，老配方**逐字冻在 `frozen_recipe`** 里。
- **别改 `frozen_recipe`** —— 它是参照物。真想改配方时这条会红，**那正是要的**。
- 它盖的是**静态摊平**：行为（拖拽、live resize）**不在里面**。
  → P3 的拖拽投放（§4.2 ④）**这条测试盖不住**，要另想验证法。
- ⚠️ 新增面板若定义新的 ObjC 类，必须确认它**不与 `frozen_recipe` 那次对拍的进程**撞名。

**另外三条不是测试、但同属判据**：

- ⚠️ **动手前先加载 skill**（`CLAUDE.md:47-56` 的硬规矩）：动模块边界 → `codebase-design`；
  改完质检 → `simplify`；声称完成前 → `verification-before-completion`。
- ⚠️ **`.gitignore` 已经把 P3 的产物覆盖了**（`CLAUDE.md:109`）：`glossary/`、`term_notes*.json`
  都在里面 —— **所以自动生成的候选词不会入库，这条不用改**。
  但**新增任何新路径之前先看 `.gitignore`**，别把个人课件路径加进去。
- 发版时：默认 `manual`、更新日志**先打开给作者确认**（`CLAUDE.md:58-66`）。

---

## 8. ⚠️ 本文档查实的两条「文档腐坏 / 说法不严」

| # | 说法 | 实际（代码为准） | 位置 |
|---|---|---|---|
| 1 | 「LLM 抽候选词（**用已有的 `SYS_CLASSIFY`**）」 | `SYS_CLASSIFY` 只对**已存在的术语列表**分档，抽不出候选词。同仓库的 `docs/RESEARCH-product-shape.md:189-190` 说得对（「缺的只是把课件当候选词源喂进去」）| `docs/PLAN-roadmap.md:55` |
| 2 | 「`glossary/<课号>.txt` 里有**两类词**（领域术语 + 课号/教务词）」 | 真实文件里**教务词确实有**（如 `glossary/SOC10020.txt` 的 `Brightspace` / `quiz` / `final test`），但**课号按设计是写在公共 `glossary.txt` 里**（`glossary.example.txt` 明写「**课号必须全列在这里**」）。另有第三类内容被漏掉了：**第一行 `# <课号> <课程名>` 是领域先验，不是术语**（`translator.py:136-160` 读它）| `docs/PLAN-roadmap.md:63-69` |

第 2 条对实现有直接影响：追加写入器**必须保留首行注释**，否则领域先验静默丢失（§5.2）。

---

## 9. 接缝结论

**推荐接缝：新建一个 UI 无关的 `prep.py`，接口形如
`prepare(course, files, on_progress) -> PrepResult`，它自己不起线程、不画窗口、不读 argv。**
CLI（`cl prep <文件…>`）与 `entry_panel.py` 是它的**两个适配器**：
CLI 同步调它、面板在后台线程调它；两者都经**同一份**追加写入器落到
`glossary/<课号>.txt`（只追加），再经**已有的** `build_notes.build(course)` 产 `term_notes.json`。
**插入点单独定**：面板走 `whatsnew` 那条已验证过的非模态卡片路（`main.py:896` + `overlay.py:1232` 旁），
CLI 走 `cl` 的 `case`（`cl:136`）——两者都不动 `run()` 的音源/模型启动序列。

**为什么是这个接缝：**

1. **它同时满足仓库自己的两条判据。** 「一个适配器是假缝，两个是真缝」
   （`docs/RESEARCH-entry-and-export.md:294`）—— CLI + 面板正好两个；
   「一个数字只有一个定义点」（`panel.py:25-26`）—— 「只追加不覆盖」这条规则只有一处实现。
2. **它不碰流水线，所以不用碰不变量。** 不新增 `streamq` tag、不改 `drain()`（`CLAUDE.md:94`）、
   不碰 `busy`/`finalq`/`carry`（`main.py:1156-1159`）。
   面板的线程归面板自己管，与 `whatsnew` 的 `on_update` 同构（`whatsnew.py:159-161`）。
3. **它最大化复用、最小化新写。** 出口 ① 的生产者（`build_notes.build()`）已经存在且已调好，
   而它**已经会读** `glossary/<课号>.txt`（`build_notes.py:208-211`）—— P3 只要把词写进去，那个出口就白得。
   新写的只有：抽取 prompt、追加写入器、Docling 依赖。
4. **它把「只追加」约束放在唯一正确的位置。** 课号/教务词/首行领域先验三类内容课件都给不了，
   而这个约束只有写入器能兑现（`translator.py:136-160`、`glossary.example.txt`）。

**最强的反对理由：**

> **「同进程」与「同实例锁」二选一，而这个矛盾不在接缝里，在进程模型里。**

如果准备动作要**在上课中**从面板发起，它就得是独立进程（Docling 抢 GIL），
而独立进程会撞 `main.py:745` 的单实例锁（`instance_lock.py:34-35` 只有一个锁名）。
反过来说，若限定「只在课前、主循环起来之前跑」，面板就得在**没有任何 UI 宿主**的时候存在
——而今天唯一会画东西的东西是 `overlay`（`main.py:896`），它自己也要等模型加载完（`main.py:812`）。

**所以 `prep.py` 这个模块接缝是对的，但「谁来启动它」和「它在什么进程里跑」必须
在动手前定死** —— 这是 P3 真正的设计决策，不是实现细节。
（若选「课后/课前离线跑，与上课互斥」，那 §1 选项 A 的 `cl prep` 就够了，面板可以不做第一批；
若坚持要拖拽面板，那就得先解决进程模型，`panel.py` 的第三消费者反而是后面的事。）
