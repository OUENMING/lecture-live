# 计划：没有 Obsidian 时，笔记存成 PDF 放桌面（按课程分文件夹）

> 2026-10-01 · **只是计划，没有改任何仓库代码**。对应手册 §10 账本里的「**PDF 出口（8-D）**」，
> 以及 `docs/RESEARCH-entry-and-export.md` §9 的 8-A 尾巴（"vault 降级为可选出口"）。
> 所有数字都是这次在本机**现跑**的（脚本在 scratchpad，不在仓库；实现阶段会固化成
> `scripts/probe_pdf_render.py`）。**符号不写行号**（行号会腐坏，要定位就 grep 符号）。

---

## 0. 一页纸

**要做的事**：收尾时如果**没有可写的 Obsidian 库** → 照样跑精修 + 复习层，把笔记排成 **PDF**，
存到 `桌面/ClassLive 笔记/<课号 课名>/<日期>_<课号>.pdf`。有库的人**一个字节都不变**。

**判断「有没有 Obsidian」= 看有没有一个能写的库，不看装没装 app**：

| 情形 | 走哪条 |
|---|---|
| `resolve_vault()` 给出的目录**存在** | **Obsidian 路**（现状，零变化） |
| 没设过库 | **PDF 路** |
| 设过但那个目录**不在了**（删了/改名了） | **PDF 路** + 说一句（⚠️ 现状是 `note_path_for` 会 `mkdir -p` 把它**凭空重建**——正是 `aba9d88` 删掉兜底时想杜绝的形状） |
| 装了 Obsidian.app 但没选库 / 有多个库没选 | **PDF 路** + 提示"点就绪条选库"（app 装没装**只用来改提示文案**，不参与裁决） |

**技术路线（已实测）**：WebKit 出 PDF，**但 8-D 调研里那条「0.7 秒搞定」漏了关键一步** ——
`createPDF` 默认只出**一张超长页**（实测 595×10495 pt，1 页），**不是 A4 多页**。
本计划用「按块边界切片 + Quartz 拼页」补上（§3.3），实测 20 页 A4 / 0.86 秒。

**最大的两个坑（都已实测，写进了设计）**：

1. ⚠️ **字体**：`font-family: 'PingFang SC'` 出来是 `CID Type 0C`（CFF）——**正是你 2026-09-25 立的规矩
   「别用 PingFang / 任何 CFF 型 CID 字体」要避的类型**；`-apple-system` 更糟（CJK 也被强制成 PingFang）。
   实测 `'Helvetica Neue','Heiti SC','Songti SC'` 链**全 TrueType**，QuickLook 渲染正常。
2. ⚠️ **线程**：收尾的 `writer.close()` 跑在 **worker 线程**，而 `WKWebView` 只能在**主线程**。
   所以 PDF 不能在 `close()` 里出，要「worker 备料 → 主线程出片」（§3.1）。

**改动面**：新增 3 个模块（`note_html.py` · `pdf_render.py` · `notes_export.py`）+ 4 处接线
（`obsidian_writer.close` · `main.py` 收尾 · 两处文案 · `requirements.txt` 加一个依赖）。
分 **5 个提交**，每个独立可回滚（§6）。

---

## 1. 现状核实（HEAD = `03b7625`，全部现查）

| 事实 | 出处（符号） |
|---|---|
| 没库时 `ObsidianWriter.close()` 提前 return「没设 Obsidian 库，这次不生成笔记」 → **没有任何笔记产物，也不花 API 钱** | `obsidian_writer.close`：`if self._vault is None` |
| 那句早退的注释**已经预告了这个功能**：「将来的『打包成 MD/PDF』接的正是这一档：`_render_note()` 是**纯函数**，导出直接复用它换落点」 | 同上 |
| `sessions/<日期>_<时间>_<课>.md` 没库也照写（2026-09-29 拆 `enabled` 之后） | `ObsidianWriter.__init__` |
| 笔记 Markdown 是**我们自己生成的封闭方言**，全部构件：frontmatter · `#/##/###` · `> [!kind]` callout（含 `-` 折叠、嵌套）· `- ` 列表 + 两空格续行/嵌套 · `**粗**` `` `码` `` `*斜*` · `<sub>` · `问题::答案` | `_render_note` / `_render_outline_section` / `_lost_items` / `_qa_items` |
| 收尾流程：`main.py` 里 `_wrap()` 在 **daemon 线程**里调 `writer.close()`，主线程 `_spin(_done.is_set)` 泵窗口；`_spin` 对终端 UI 是 `sleep(0.02)` | `main.py` 收尾段 |
| 没库的人，**逐句转录只在 `sessions/`（装在 `.app` 目录里，普通人找不到）** → PDF 是他们**唯一看得见的产物** | `SESSIONS = Path(__file__).with_name("sessions")` |
| `find.py`（问答检索）的根里**本来就有 `sessions/`** → PDF 路下「讲一下/追问」的检索**不受影响** | `find.default_roots` |
| WebKit 绑定**没装**在 `.app` 的 venv 里（`ModuleNotFoundError: No module named 'WebKit'`）；`requirements.txt` 现在只有 Cocoa / Quartz / AVFoundation | 现跑 |
| 依赖变更走**既有的 `deps` 步骤**（卡片上要用户点头、`cl update` 自动补）——**不需要新机制** | `update.py` `deps` 步 |
| 本机 `/Applications/Obsidian.app` 在；Obsidian 注册表在 `~/Library/Application Support/obsidian/`；`ready.vault_candidates()` 已能读（8-F） | 现跑 |
| 就绪条那格「笔记库」没设时写的是「没设 —— 笔记只写 sessions/，不落 Obsidian」→ **要改文案** | `ready.vault_item` |
| 收尾卡/终端问话写死「存入 Obsidian 吗？」→ **要改文案** | `overlay.ask_save` · `main._ask_save_notes` |

### 1.1 这次的实验结果（scratchpad，不进仓库）

| 实验 | 结果 |
|---|---|
| `createPDF` 默认配置，59 段长文 | **1 页 × 595×10495 pt** ← 不是分页 |
| `WKWebView.printOperationWithPrintInfo` 无头保存 | ⛔ **死循环写盘，265 MB 才被我杀掉** —— 无头下**绝不能用** |
| `createPDF(rect=…)` 逐页取 + `CGPDFContext` 拼 A4 | ✅ **20 页 A4（595×842）· 0.86 s · 275 KB** |
| 字体：`Noto Sans SC`（本机装了） | TrueType ✅（⚠️ 朋友机器**没有**它 → 不能依赖） |
| 字体：`'PingFang SC'` | `CID Type 0C` ⛔ 违反你的规矩 |
| 字体：`-apple-system,'PingFang SC'` 或 `-apple-system,'Heiti SC'` | CJK 都被强制成 `PingFangUIDisplaySC`（Type 1 Custom）⛔ |
| 字体：`'Hiragino Sans GB'` | `CID Type 0C` ⛔ |
| 字体：`'Helvetica Neue','Heiti SC'` | **全 TrueType**（HelveticaNeue + STHeitiSC）✅；`'Songti SC'` 也是 TrueType |
| 渲染：中文 + 英文 + emoji（🎯📖🕐） | 正常；QuickLook 缩略图目视通过 |
| 分页质量 | 按块边界切，第 1 页末尾留白，没有切断行 |

⚠️ **没测的**（诚实写）：WeChat / WPS 里的显示 —— 我这里没有它们。选 TrueType 链是**按你的规矩降风险**，
不是验证过。真机验证清单里有这一条（§7）。

---

## 2. 要你拍板的 3 件事（每条都带我的推荐，你不说话我就按推荐做）

| # | 问题 | 推荐 | 为什么 |
|---|---|---|---|
| **Q1** | PDF 里**要不要附完整逐句转录**？ | ⛔ **2026-10-02 作者质疑后改口：不附**（转录早已存在 `sessions/`，再排一遍是重复；原推荐的理由只是"找不到"，那是**可发现性**问题不是**缺内容**问题）。改成：PDF 只放复习层；**把 `sessions/` 那份会话 md 原样复制**到同一课程文件夹，命名 `<日期>_<课号>_转录.md`（一次文件复制，不排版、不占 PDF 页数、含 ASR 原文）。⚠️ 此行以下 §3.5 / §4.2 / T4 里提到 `transcript_html` 与附录的地方，实现时一律作废 ↓（原文保留供对照）<br>~~原推荐：要，放在复习层之后，另起一页、小一号字~~ | 没库的人转录只在 `.app` 目录里。字数量过：682 句的课 EN 4.0 万字 + ZH 1.4 万字；⚠️ **页数是按字数估的（附录约 15–20 页、复习层约 3–6 页），不是测的** —— P2/真机验证 §8-5 才有实测。**没配 key 时复习层是空的**，不附转录的话 PDF 几乎是白纸 |
| **Q2** | **有 Obsidian 的人要不要也出一份 PDF**？ | ❌ **v1 不做**；但加 `--vault none` 让**想强制走 PDF 的人**能用（也是我们自己真机验证的开关） | 你原话是"如果没有则…"；双写要多一个设置项，等有人要再加 |
| **Q3** | 桌面文件夹的名字 + 被拒时怎么办 | 名字 **`ClassLive 笔记`**；桌面写不进（macOS 会弹「想访问桌面文件夹」，用户可能点"不允许"）就**退到 `~/ClassLive 笔记/`** 并明说 | 见 §5.1。桌面是你定的，退路只是兜底 |

---

## 3. 设计

### 3.1 数据流（谁在哪个线程）

```
 worker 线程（_wrap）                              主线程（_spin 之后）
 ───────────────────                              ────────────────────
 writer.close()
   ├─ 精修 / 复习层 / 问答 / 标记 / 重点句 / 纲要      （全部照旧，API 钱照花）
   ├─ note_md = _render_note(…, with_transcript=False)
   ├─ 有库  → 写 <vault>/Lectures/…md                （现状，零变化）
   └─ 没库  → self.pdf_job = PdfJob(note_md, entries, …)   ← 只备料，不碰 AppKit
        返回 msg                                    notes_export.run(writer.pdf_job, render=…, pump=…)
                                                       ├─ note_html.document(…)         纯函数
                                                       ├─ pdf_render.html_to_pdf(…)     ← WKWebView（主线程）
                                                       └─ 失败梯子（§3.4）→ 最终 msg → wrapup_done
```

**为什么「备料 / 出片」分家**：`close()` 是纯 Python（能在 worker、能在 `rebuild_note.py` 同步脚本里跑），
不该沾 AppKit；出片那一步**必须**主线程 + 跑 runloop。分家后：
- `rebuild_note.py`（本来就在主线程同步跑）直接 `close()` 然后 `run(job)`；
- 测试用**假渲染器**注入，不用起窗口。

### 3.2 模块与缝（codebase-design）

| 模块 | 接口（调用方要知道的全部） | 藏在后面的 | 缝（2 个适配器才算真缝） |
|---|---|---|---|
| `notes_export.py` | `decide(vault) -> Dest` · `make_job(…) -> PdfJob` · `run(job, *, render, pump=None, home=None) -> Result` | 目录命名/复用/去重、失败梯子、桌面→家目录退路、文案 | `render`（真：WebKit / 假：测试桩）· `home`（真：`Path.home()` / 假：tmp） |
| `note_html.py` | `md_to_html(md) -> str` · `transcript_html(entries) -> str` · `document(body, title) -> str` · `leaks(html) -> list` | 封闭方言解析、转义、CSS（含字体链）、`::` 卡片 | 纯函数，无缝（不需要） |
| `pdf_render.py` | `html_to_pdf(html, out, *, pump=None) -> Report` · `plan_pages(cands, total, ch) -> list` · `font_report(pdf_bytes) -> list` | WKWebView 生命周期、runloop 泵、JS 量块、贪心切页、Quartz 拼页、字体自检 | `html_to_pdf` 是 AppKit 适配器；`plan_pages`/`font_report` 是纯函数（可单测） |

**删除测试**：删掉 `notes_export` → 目录命名/退路/梯子的复杂度会**散到 `obsidian_writer` 和 `main.py` 两处**
→ 它在挣钱。删掉 `note_html` → `_render_note` 要再长一个 HTML 后端 + 与 md 版**章节顺序分叉**
（我选了"从 md 转"而不是"再写一遍渲染"，理由 §3.5）。

### 3.3 `pdf_render`：实测过的算法

**为什么不用 `printOperation`**：见 §1.1，无头死循环。**不用 `NSAttributedString(html:)`**：
慢、CSS 支持窄、也要主线程，没有比 WebKit 更省。

```python
# pdf_render.py —— 骨架（算法 = scratchpad/pdf_exp2.py 实测通过的那版）
PW, PH, M = 595.0, 842.0, 51.0          # A4 pt；边距 ≈ 18mm
CH = PH - 2 * M                          # 每页内容高 = 740

class RenderUnavailable(RuntimeError): ...   # WebKit 没装 / 起不来 → 上层走失败梯子

def html_to_pdf(html: str, out: Path, *, pump=None, timeout_s: float = 30.0) -> "Report":
    try:
        from WebKit import WKWebView, WKWebViewConfiguration, WKPDFConfiguration
        import Quartz
    except ImportError as e:
        raise RenderUnavailable(f"没装 WebKit 组件（{e}）—— 跑 cl update 补依赖") from e
    # ① 建 WKWebView(frame=(0,0,PW,CH))，loadHTMLString_baseURL_(html, None)，泵到 didFinish
    # ② evaluateJavaScript 量「可断点」：
    #      h1,h2,h3,p,li,.callout,.card,tr 的 [top,bottom,isHeading]
    #      ⚠️ 住在 .callout/.card 里、且容器高 ≤ 0.5*CH 的子元素不算候选（别把小盒子从中间劈开）
    # ③ cuts = plan_pages(cands, total, CH)              ← 纯函数
    # ④ 逐页 createPDFWithConfiguration(rect=(0,a,PW,b-a)) → CGPDFDocument 第 1 页
    #    → CGPDFContextBeginPage → CTM 平移 (0, PH-M-(b-a)) → CGContextDrawPDFPage → End
    # ⑤ CGPDFContextClose；写 out（先写 .tmp 再 rename —— 半截 PDF 不许占着正式名字）
    # ⑥ font_report(bytes)；返回 Report(pages, seconds, font_warnings)
```

**`plan_pages(cands, total, ch)`**（纯函数，判据重点）：

```python
def plan_pages(cands, total, ch, min_fill=0.35):
    """cands: [(top, bottom, is_heading)] 升序。返回 [(a, b)]，首尾相接地盖住 [0, total]。
    规则：① 优先在**块边界**断（≤ y+ch 的最大 bottom）；② **不许把页断在标题后面**
    （标题单独留在页尾 = 孤标题）→ 丢掉 is_heading 的候选；③ 若最佳断点让这页
    填充 < min_fill，说明后面是个巨块 → 硬切在 y+ch；④ 下一页起点取「断点之后第一块的 top」
    （跳过块间 margin，页顶不留空白）。"""
```

⚠️ 硬切（巨块 / 超长段）会切断一行字 —— 实测里一段 > 740pt 的段落不存在（一段话最长也就十几行），
所以这是**兜底不是常态**；判据里钉住"有断点时绝不硬切"。

### 3.4 失败梯子（笔记绝不能丢；`sessions/` 底稿永远在）

```
① PDF 成功                       → ✅ 已存成 PDF → <路径>
② WebKit 缺失 / 渲染抛 / 超时     → 把 md（含完整转录）存到**同一个课程文件夹**
                                   → ⚠ PDF 没生成成功（原因）—— 已存成 Markdown：<路径>
③ 桌面写不进（PermissionError）   → 换到 ~/ClassLive 笔记/ 再走 ①②
④ 全都写不进                     → 失败（_ok=False，卡片留住）+ 指向 sessions/ 底稿
```
**判据**（同现有 `wrapup_done` 的口径）：只要 ①②③ 任一成功就是 `_ok=True`（有笔记落地）；
只有 ④ 才算失败。**PDF 失败但 md 成功不是失败** —— 别让用户为"换了种格式"而卡在卡片上。

### 3.5 为什么「从 md 转」而不是「再写一个 HTML 渲染」

调研 §6 当时倾向"同一份数据渲第二遍"。现在改口，理由（都来自这次读代码）：
- `_render_note` 里的**章节顺序、措辞、「整节跳过」规则、五层护栏**全在 md 版里 —— 再写一遍 =
  **同一条规则两处定义**（本仓最怕的形状）。
- 方言**封闭**（§1 表里列全了），我们是**自己的作者**，转换器是 ~100 行状态机，**不是**通用 Markdown 解析 ——
  所以调研 §2 那 5 类"直接转漏东西"的缺陷在这里是**可枚举、可断言**的（`leaks()` + 判据 T4）。
- 判据：**每个 `## ` 标题 md 里有 → PDF 里必有**（章节对账，防分叉）。

`with_transcript=False`：给 `_render_note` 加一个**默认 True** 的参数；PDF 路传 False，
转录附录由 `transcript_html(entries)` 单独出（纯 EN/ZH，不要嵌套 callout 语法 —— 这样转换器**不用处理嵌套**）。

---

## 4. 逐文件改动（代码级）

### 4.1 新增 `notes_export.py`

```python
"""笔记去哪儿 —— Obsidian 库，还是桌面上的 PDF。**唯一定义点**（别在 writer/main 里各写一半）。"""
from __future__ import annotations
import dataclasses, os, re, time
from pathlib import Path

NOTES_DIRNAME = "ClassLive 笔记"
UNSORTED = "未归课"                       # 课号是占位名 LECTURE（没设课程）时用
_BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f]')   # 文件名不安全字符


@dataclasses.dataclass(frozen=True)
class Dest:
    kind: str                    # "obsidian" | "pdf"
    vault: Path | None = None
    note: str = ""               # 给人看的一句话（库路径没了 / 装了 app 没选库…）

    @property
    def where(self) -> str:      # 问话/卡片上写的「存到哪儿」
        return "Obsidian" if self.kind == "obsidian" else "PDF（桌面 ClassLive 笔记）"


def decide(vault, *, app_installed=None) -> Dest:
    """⭐ 判「有没有 Obsidian」= **有没有一个存在的库目录**，不是装没装 app。
    app_installed 只用来改 note 文案，不参与裁决。"""
    if vault:
        p = Path(vault).expanduser()
        if p.is_dir():
            return Dest("obsidian", p)
        return Dest("pdf", None, f"记住的库不在了（{p}）—— 这次存成 PDF")
    hint = "（检测到 Obsidian，但还没选库 —— 点面板里的「笔记库」选一个）" if app_installed else ""
    return Dest("pdf", None, hint)


def obsidian_app_installed(home=None) -> bool: ...       # /Applications 或 ~/Applications 下有 Obsidian.app


def notes_roots(*, home=None) -> list[Path]:
    """候选根，**按优先级**：桌面 → 家目录（桌面被 TCC 拒时的退路）。"""
    h = Path(home) if home is not None else Path.home()
    return [h / "Desktop" / NOTES_DIRNAME, h / NOTES_DIRNAME]


def course_folder(root: Path, course: str, title: str = "") -> Path:
    """`<root>/<课号 课名>/`。⚠️ **复用**：已有以「课号」或「课号␠」开头的文件夹就用它，
    不因为术语表首行被改了就再开一个（同课两个文件夹 = 用户找不到哪个是全的）。"""

def pdf_target(folder: Path, date: str, course: str) -> Path:
    """`<日期>_<课号>.pdf`。⚠️ 已有同名 → 加时间戳并存，**绝不覆盖**（同 `note_path_for`）。"""

@dataclasses.dataclass
class PdfJob:
    note_md: str                 # `_render_note(with_transcript=False)`
    entries: list                # 转录附录的原料
    course: str; title: str; date: str
    session_path: Path | None    # 失败时指回底稿
    full_md: str                 # 含完整转录的 md —— ②梯子要写它

@dataclasses.dataclass
class Result:
    ok: bool; kind: str          # "pdf" | "md" | "none"
    path: Path | None; msg: str

def make_job(...) -> PdfJob: ...
def run(job, *, render, pump=None, home=None) -> Result:
    """失败梯子 §3.4。`render(html, out, pump=pump)` 是缝；`home` 是缝（测试指到 tmp）。"""
```

### 4.2 新增 `note_html.py`（纯函数，不 import AppKit）

- **字体链**（CSS 里**唯一一处**）：
  `font-family: "Helvetica Neue", "Heiti SC", "Songti SC", sans-serif`，
  注释写明：为什么不是 PingFang / `-apple-system`（§1.1 两行）+ `font_report` 会在运行时自检。
- `md_to_html(md)` 状态机：frontmatter 剥掉（`date/course` 取出来做页眉）→ 标题 →
  callout（`> [!kind]` + 可选 `-`/`+` → `<div class="callout kind">`，**折叠标记一律展开**）→
  列表（两空格续行 → `<br>`；`  - ` 嵌套 → 子 `<ul>`）→ 段落。
- 行内：**先整体转义 `& < >`，再**依次处理 `` `码` `` → `**粗**` → 整行 `*斜*` → 放行 `<sub>`。
  （顺序反了会二次转义 / 被粗体吃掉码里的星号。）
- `问题::答案`：`li` 文本含 `::` → `<div class="card"><span class="q">…</span><span class="a">…</span></div>`
  （⚠️ `_qa_items` 已把问题里的 `::` 拆成 `: :`，所以第一个 `::` 就是边界）。
- `leaks(html)`：返回可见文本里残留的 `[!xxx]` / `::` / 行首 `---` / 孤立 `**`（判据与**运行时告警**共用）。
- `transcript_html(entries)`：`<div class="tx">` 每句一块：`<span class="ts">10:02:11</span>` + EN + ZH（灰），
  **不含 ASR**（⭐ 星标保留）；9pt。
- CSS 要点：`.callout{break-inside:avoid}`（只是意图 —— 切页实际由 `plan_pages` 管）；
  `h2{margin-top:1.4em}`；`.tx p{margin:.15em 0}`；颜色全部用 `#` 十六进制（不依赖暗色模式）。

### 4.3 新增 `pdf_render.py` — 见 §3.3。另含 `font_report`：

```python
def font_report(pdf: bytes) -> list[str]:
    """返回命中 `/CIDFontType0`（CFF 型 CID）的字体名，**排除 `.LastResort`**
    （那是 emoji 缺字形的兜底，不是中文乱码 —— RESEARCH-entry-and-export §2.1）。
    ⚠️ 必须先解压所有 stream（weasyprint/CoreGraphics 都把字体字典压进 /ObjStm，明文搜会漏检）
    —— 配方见 memory chinese-pdf-generation。**验证器自身要先自测**（T7 用负样本）。"""
```
运行时：非空 → `print` 一行 ⚠ 并写进 `Report.font_warnings`（不阻断 —— 笔记已经出来了，只是告诉你字体链回落了）。

### 4.4 改 `obsidian_writer.py`

```python
# ① _render_note 加参数（默认 True → 有库路径的 md 输出不变）
def _render_note(self, entries, review, ..., chapter_items=None, with_transcript: bool = True) -> str:
    ...
    if with_transcript:
        L += [ "## 📜 完整逐句转录…" ... ]          # 现有那一整段，原样挪进 if

# ② close()：早退那处换成「判去向」
dest = notes_export.decide(self._vault, app_installed=notes_export.obsidian_app_installed())
# （删掉原来的 `if self._vault is None: return …`）
# …… 精修 / 复习层 / 五层护栏全部照旧 ……
if dest.kind == "obsidian":
    note = self._render_note(…)                                   # with_transcript=True
    self.vault_path = note_path_for(dest.vault, self._date, self._course)
    self.vault_path.write_text(note, encoding="utf-8")            # 现状
    return …                                                       # 现状文案
note_md = self._render_note(…, with_transcript=False)
full_md = self._render_note(…)                                    # ②梯子要用
self.pdf_job = notes_export.make_job(note_md, entries, …, full_md=full_md)
return (f"📄 笔记已备好（{self._n} 句, 复习层: {layer}），正在排成 PDF…"
        + (f"\n   {dest.note}" if dest.note else ""))

# ③ __init__ 里加 self.pdf_job = None
```
⚠️ **早退顺序不变**：`mode=="no"` / 未启用 / 零句无问答 / 用户答"不存" 这四条仍在最前，
**所以没库的人点「不存」依旧一分钱不花**。唯一变化：没库**不再**早退。

⚠️ **花钱的行为变化要写进 CHANGELOG**：没设库的用户，以前收尾不跑精修/复习层，现在会跑
（每节课多几次 DeepSeek 调用，和有库的人一样）—— 想省钱就点「不存」。

### 4.5 改 `main.py` 收尾

```python
# `_wrap()` 不动。`_done` 之后、`wrapup_done` 之前插入：
if not give_up and getattr(writer, "pdf_job", None) is not None:
    _ui_p = getattr(ui, "wrapup_progress", None)
    if callable(_ui_p): _ui_p("pdf", 0, 1)                       # polish.STAGE_NAME 加 "pdf": "生成 PDF"
    _appkit = bool(getattr(ui, "drives_appkit", False))
    res = notes_export.run(writer.pdf_job, render=pdf_render.html_to_pdf,
                           pump=(ui.pump if _appkit else None))
    _box["msg"] = res.msg
    if not res.ok:
        _box["err"] = RuntimeError(res.msg)                       # 走既有的「失败留住」那条路
```
- `pump` 缝：渲染里的等待循环每 20ms 调一次 → 收尾卡不冻（同 `_spin` 的纪律）。
- 起头那句 `print("⚠ 没设 Obsidian 库路径…这次只写 sessions/")` 改成
  `ℹ 没设 Obsidian 库 —— 这次笔记会存成 PDF 放桌面「ClassLive 笔记」`。
- `ask_save(n)` / `_ask_save_notes(n)` 各加一个可选 `where="Obsidian"` 参数，
  文案「存入 {where} 吗？」；`main` 传 `decide(vault).where`。
- **`--vault none`**：`resolve_vault` 之前识别 `args.vault == "none"` → 强制 PDF 路（Q2 的逃生口，也是真机验证的开关）。
  ⚠️ 不碰 `resolve_vault` 本身（它是唯一定义点，加哨兵值会污染 8 个调用方）。
- **启动时后台探一次桌面**（§5.1）：`threading.Thread(target=notes_export.probe_home, daemon=True)`，
  只为让 macOS 的"想访问桌面文件夹"弹窗**出现在开课时**（你在场、有上下文），而不是**下课时**（数据在手上）。

### 4.6 其余改动

| 文件 | 改动 |
|---|---|
| `requirements.txt` | 加 `pyobjc-framework-WebKit>=12.2`（注释：只为 PDF 出口；缺了走失败梯子②，不是崩） |
| `ready.vault_item` | `detail`：没设 → 「没设 Obsidian —— 笔记会存成 PDF，放桌面「ClassLive 笔记」」（`state` 仍 UNKNOWN，不是 todo） |
| `polish.STAGE_NAME` | `+ "pdf": "生成 PDF"` |
| `scripts/rebuild_note.py` | `close()` 之后 `if w.pdf_job: print(notes_export.run(w.pdf_job, render=…).msg)` |
| `doctor.py` | 加一条**只读**检查：`import WebKit` 失败 → ⚠（不是 ✗）「PDF 导出不可用，跑 cl update」 |
| `README.md` | "Obsidian 是可选的"一节（当前写的是只写 `Lectures/`） |
| `docs/HANDBOOK.md` §10 | 「PDF 出口（8-D）」从账里划掉，记 8-E（每课合并）仍开 |
| `docs/RESEARCH-entry-and-export.md` §3 | 加更正：「`createPDF` 默认出的是**单张超长页**，不是 A4 分页；字体链见 PLAN-pdf-notes §1.1」 |
| `CLAUDE.md` | 文件地图加三行（`notes_export` / `note_html` / `pdf_render`） |

---

## 5. 要提前想清的两件事

### 5.1 macOS 的「桌面文件夹」授权

写 `~/Desktop` 会触发 macOS 的隐私授权弹窗（"ClassLive 想访问桌面文件夹"）——这是 macOS 10.15 起的
已知行为，**我这次没在你的机器上触发它，所以标"待真机验证"**。风险是**时机**：默认会在**下课落盘那一刻**弹，
用户不知道为什么、点了"不允许"就写不进去。设计上的应对：

1. **开课时后台探一次**（§4.5）—— 弹窗出现在你还在看着面板的时候；
2. 被拒（`PermissionError`）→ **自动退到 `~/ClassLive 笔记/`**（家目录不受这个限制）并明说，
   **不重试、不弹第二次、不去碰系统设置**（你的规矩：只报告不动设置）；
3. 这也呼应了 `ready._scan_vaults` 里 F24 的教训（"别在打开面板那一刻扫桌面"）——
   这里不是扫，是**写**，且只在用户走 PDF 路时才发生。

### 5.2 依赖交付

`pyobjc-framework-WebKit` 走既有 `deps` 步骤：老用户 `cl update` / 卡片点头后补；新装走 `install.sh`。
**还没补之前** PDF 路走失败梯子②（存 md + 一句"PDF 没生成：跑 cl update 补依赖"）——**笔记不丢**。
⚠️ `.app` 的 venv 没有 pip，补包是 `uv pip install --python ClassLive.app/Contents/MacOS/python …`
（CLAUDE.md 的坑）。**我不会在没问你的情况下往你的 `.app` 里装包。**

---

## 6. 分阶段 & 提交（每个独立可回滚，**不推送，等你点头**）

| # | 提交 | 内容 | 闸门 |
|---|---|---|---|
| **P1** | 纯模块 | `note_html.py` + `notes_export.py`（含 `decide`/命名/梯子，**假渲染器**）+ `tests/test_notes_export.py` | 新测试全绿 + 默认闸门不变；**`git diff --stat` 只有新文件** |
| **P2** | 出片 | `pdf_render.py` + `scripts/probe_pdf_render.py`（把 scratchpad 实验固化）+ `tests/test_pdf_render.py`（纯函数全跑；WebKit 那组**缺依赖时明确打印 SKIP 并计数**） | 同上；探针在 scratchpad venv 里跑出 ≥2 页 A4 + 字体自检干净 |
| **P3** | 接 writer | `_render_note(with_transcript)` · `close()` 判去向 · `pdf_job` · 更新 `test_vault.py` 里钉旧行为的那条（见 §7 T9） | `test_vault` / `test_audit_regressions` / `test_wrapup` 全绿；**有库路径的 md 输出逐字不变**（T9b） |
| **P4** | 接 main + 文案 | `main.py` 收尾 · `ask_save` 文案 · `ready.vault_item` · `--vault none` · `requirements.txt` · `rebuild_note.py` · `doctor.py` | 上面 + `test_ready` + `test_entry_panel` + `scripts/probe_wrapup.py` 拍图 |
| **P5** | 文档 | README / HANDBOOK / RESEARCH 更正 / CLAUDE.md；**CHANGELOG 文案草稿先给你看** | 你确认文案 → 才 bump / tag / 发版（默认 manual） |

**⚠️ 每个提交前先加载对应 skill**：P1/P3 `codebase-design` · 动界面文案/卡片 `impeccable` ·
每次声称完成前 `verification-before-completion` · 改完 `simplify`。

---

## 7. 判据（每条都写「把实现哪一行改坏它会红」，**实现时真改坏一次确认会红**）

> 测试隔离（你的硬规矩）：**读端写端都换**。`notes_export.run(home=tmp)`、`ow.SESSIONS=tmp`、
> 假渲染器；绝不碰真 `~/Desktop` / `~/.classlive/vault` / `sessions/`。
> 调用链问法答案：`close()` → 读 `session_path` 与它的 `.atoms/.chapters/.lost` 旁路（tmp）；
> `run()` → 写 `home/…`（tmp）；`pdf_render` 不在 P1 的调用链里。

| # | 钉什么 | 改坏哪里会红 |
|---|---|---|
| **T1** | `decide`：库存在→obsidian；没设→pdf；**设了但目录不在→pdf（且不创建它）** | 把 `p.is_dir()` 改成 `bool(vault)` → 第三条红（这正是「凭空造目录」形状） |
| **T2** | `course_folder`：已有 `ECON10740 旧课名/` → **复用**；`LECTURE`→`未归课`；`/` `:` 被替换；空 title→只用课号 | 删掉前缀复用 → 术语表首行一改就多出第二个文件夹 |
| **T3** | `pdf_target`：同日同课已有 → 加时间戳并存，**原文件字节不变** | 删 `exists()` 判断 → 覆盖，原文件字节变了 |
| **T4** | ⭐ **泄漏对账**：用一份**含全部方言**的 `_render_note` 输出（review sections / `::` 卡 / 标记块 / 纲要 / 重点句 / ⭐ / 折叠 callout）转 HTML → `leaks()==[]`；每个 `## ` 标题在 HTML 里都有；emoji 保留；`with_transcript=False` 时**没有**转录 | 去掉 callout 处理 → `[!info]` 漏出；去掉 `::` 处理 → `::` 漏出 |
| **T5** | 转义：EN 含 `<script>` / `&` / `A<B` → 出现 `&lt;` 而不是标签；`<sub>` 放行；码里的 `*` 不被粗体吃掉 | 去掉转义 → 红 |
| **T6** | `plan_pages`：① 页首尾相接盖满 `[0,total]` ② **有块边界时绝不硬切** ③ 不把页断在标题后 ④ 巨块才硬切 ⑤ 总页数 = ⌈内容/页高⌉±1 | 删「丢标题候选」→ ③红；把 `max` 改 `min` → ② 红 |
| **T7** | `font_report`：**负样本**（含 `/CIDFontType0` 的压缩对象流）→ 命中；`.LastResort` → 不命中；干净样本 → `[]`（**验证器自身先自测**） | 去掉解压 → 负样本漏检 |
| **T8** | `run()` 失败梯子（假渲染器）：渲染抛 → md 落进**同一课程文件夹**且 `ok=True`；桌面根 `chmod 500` → 退到第二个根；两个都不可写 → `ok=False` 且 msg 指向 `session_path` | 去掉 except → 渲染异常穿出去；去掉退路 → 第二条红 |
| **T9** | `close()` 没库：`pdf_job` 非空、`vault_path is None`、**不写任何 `Lectures/`**；`mode="no"` / 零句 / 答「不存」→ `pdf_job is None` 且**没调模型**。⚠️ **要改写 `t_sessions_still_written_without_vault`** —— 它钉的是旧行为（`"没设 Obsidian 库" in msg`），属于「判据钉住的正是旧缺陷」那类；改成钉新行为 | 恢复早退 → `pdf_job` 为空红 |
| **T9b** | 有库：`close()` 产出的 md 与改前**逐字相同**（拿 `with_transcript=True` 的输出对一份冻结的黄金文件） | 误删转录段 / 改措辞 → 红 |
| **T10** | ⭐ **真 WebKit 端到端**（缺依赖则明确 SKIP）：夹具笔记 → PDF → `CGPDFDocument` 页数 ≥2、页框 595×842、PDFKit 取文本含中文且**不含** `[!info]`、`font_report==[]` | CSS 字体链改成 `'PingFang SC'` → `font_report` 红 |
| **T11** | 文案：`vault_item(None)` 含「PDF」且 `state==UNKNOWN`（**不是 todo**）；`ask_save` 文案随 `where` 变 | 改回写死 → 红 |

⚠️ 写判据前先过一遍 `judges-that-look-like-they-test` 的十五种假绿 —— 尤其 T4（别用手写的夹具 md，
**用真 `_render_note` 的输出**，否则就是「手抄生产逻辑」）和 T10（SKIP 不能被算成通过）。

---

## 8. 真机验证清单（P4 之后、发版之前；**都不改你的系统设置**）

1. `cl --vault none`（强制 PDF 路）跑一段短录音 → 桌面出现 `ClassLive 笔记/<课>/…pdf`，**QuickLook 打开中文+emoji 正常**
2. **AirDrop / 微信文件助手发到手机，用微信自带阅读器打开** ← 我没法测的那条（§1.1 末尾）
3. 桌面授权弹窗：时机在**开课时**；点"不允许"→ 退到 `~/ClassLive 笔记/` 且有明说（**你在场我们一起点，不代你点**）
4. 没配 key 的一节：PDF 不是白纸（转录附录在）
5. 682 句的长课：页数 / 耗时 / 文件大小（**记数字**，别拿合成文档的 0.86 秒当结论）
6. 有库路径回归：`cl` 照常写进 Obsidian，`Lectures/` 里的 md 与改前一致

---

## 9. 不做的事

- ❌ 每课合并成一份总 PDF（8-E）；❌ Obsidian 用户双写 PDF（Q2）；❌ PDF 页脚/页码/书签（v2，CoreText 可加）；
- ❌ 暗色 PDF；❌ 在 PDF 里放 ASR 原文；❌ 动 `sessions/` 格式（三方共享契约）；
- ❌ 在没问你之前往 `.app` venv 装包；❌ 去碰 `~/.classlive/vault`（你的真配置）。

## 10. 顺手发现（不在本计划里，记一笔）

1. `main.py` 收尾里 `tester.finish(note_path=str(getattr(writer, "note_path", "") …))` —— **`writer` 上没有 `note_path` 这个属性**
   （属性叫 `vault_path`）→ 测试报告的 `note_file` **恒为空串**。已核实 `upload._SCRUB_KEYS` 含 `note_file`，
   所以修它不会泄露路径。一行的事，但它**不属于**这次范围，**不并进来**（单独提交、单独告诉你）。
2. `RESEARCH-entry-and-export.md` §3 的结论"WebKit 0.7 秒"量的是**单页小文档**，没测分页——P5 更正。
3. 「字重偏细」那条旧警告：`pdffonts` 里字体名带 `Thin` 是**可变字体默认实例名**，我渲染出来的图字重正常。
   但这条在 Heiti 链下不再相关（不用 Noto 了）。
