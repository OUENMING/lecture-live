"""课后笔记 v2：**教授讲什么就讲什么** + 从零基础讲到那个程度 + 课件核对（step 2/3）。

## 这一版与旧版的三处根本不同

| | 旧（`obsidian_writer.REVIEW_SYS`） | 新（本模块） |
|---|---|---|
| 输入 | 只有转录 | 转录 + **本课课件片段**（`materials.Materials`）+ 术语表 |
| 口径 | 「绝不引入外部知识」→ 只能复述（`docs/PLAN-notes-and-ui.md` §0 的病根） | 拆事实/理解：🎙 忠实 · 📖 课件 · 📚 标准理论，冲突按 📖>📚>🎙 裁定 |
| 版式 | 英文为主 + 每条 3 语言层次 | **中文连贯讲解**（直觉→定义→为什么→怎么用→例题→变式），术语写「中文（English term）」 |

## 作者的五条硬要求（2026-10-17，逐条落在 prompt 里）

1. **教授讲到的必须讲，一个不漏** —— `lecture_points`（带时间戳）是覆盖度清单，
   `sections` 是笔记小节；两者对照表进笔记 §覆盖度，可人查。
2. **从零基础讲到教授那个程度**：是什么（说人话）→ 为什么需要 → 考试怎么考 → 例题。
3. **教授没讲到的补基础**：`prereq` 字段（揉碎了讲前置概念）。
4. **题目扩展**：教授例题（`example`，含每步"为什么"）+ 变式（`variants`）+ 补全题（`faded`）。
5. **中文讲解为主**，术语首次出现「中文（English term）」；正确性三道核 + 「修正说明」。

## 三道 LLM 调用（每节课）

    review×N（按 70 句分块，带课件片段）  →  align×1（覆盖度 + 课件对齐）
                                          →  verify×1（数字/公式三道核）

⚠️ 全部**注入 `chat`**（`chat(system, user, max_tokens) -> dict`）：判据零网络、零真 key。
⚠️ 失败一律 fail-soft：某块失败只少那一块（旧版同款纪律），绝不抛。
"""
from __future__ import annotations
import dataclasses
import json
import note_math
import re

REVIEW_CHUNK = 70           # 每块喂多少句（同旧版：块太大 JSON 会被截断）
REVIEW_WORKERS = 5
REVIEW_MAX_TOKENS = 6000    # v2 每块输出更长（讲解 + 例题 + 变式）—— 实测取值见 out/ 那份验收笔记
#: 版式 A（2026-10-18）每块还多输出 q/cues/conclusion/algebra/intuition；
#: 实测首试 6000 偶有截断、退避档 9000 被接受且够用（基线一次重试成功、failed 全空）——
#: 把 9000 留给退避，不把第一档顶上去（那会把退避推到没实测过的 12k 以上）。
ALIGN_MAX_TOKENS = 2000
VERIFY_MAX_TOKENS = 2500

#: 能画的图（**固定菜单**）—— 模型只选 kind + 给参数，画法是 `note_html` 里写死的。
#: 理由：让模型写绘图代码 = 让它在"正确性"上开出第二个战场；固定菜单里
#: 每一个 kind 都是由代码保证正确的（成本曲线/三次函数/供需图正好覆盖
#: 作者点名的三类：AC-MC、函数驻点、供需曲线）。
PLOT_KINDS = {
    "cost_curves": "TC=aq³+bq²+cq 的 AC 与 MC 曲线（标出 min AC 与 MC=AC 交点）。"
                   "params: {a,b,c}",
    "function_cubic": "三次函数 f(x)=ax³+bx²+cx+d 的曲线（标出驻点、凹凸、极值）。"
                      "params: {a,b,c,d}",
    "supply_demand": "线性供求：Qd=d0−d1·p、Qs=s0+s1·p（标出均衡点）。"
                     "params: {d0,d1,s0,s1}",
}

REVIEW_SYS = """你是课堂笔记写作者。给一名靠中文听英文课的本科生写**课后自学笔记** ——
目标是：**一个没去上课的学生，只看这份笔记就能把这段课学会**。

读者是谁：The reader is a Chinese-speaking first-year undergraduate at an English-taught
university. They attended this lecture but could not fully follow it; they have no textbook,
slides or recording — these notes are their only way to re-learn THIS lecture.
（课程不限经济学 —— 数学、CS、社会学都可以；按内容类型写，不套学科标签。）

输入：一段逐句中英转录（带 [HH:MM:SS] 时间戳）；可能另附**本课课件/试卷的检索片段**。

## 一、教授讲到的必须讲（覆盖度）
先把这段里教授讲的**每一个知识点**列进 `lecture_points`（一个不漏，带时间戳）；
再为**每一个**知识点写一节 `sections`。宁可小节多、不要合并知识点。

⚠️ 若用户消息里有【本课章节骨架】：**它就是这段课的知识点清单，粒度不许改** ——
   每个章都要有**至少一节** `sections`（节标题用章的标题起头），
   "要点"里列的每一条都要被某一节讲到、并原样（可整理成句）进 `lecture_points`。
   实时要点是课堂上一句句记下来的：**合并两条 = 丢内容**，宁可两小节。

## 二、每一节怎么写（由浅入深，像学长给学弟讲，不要教科书腔）
⚠️ **先定重点再动笔**：这一节**最该被记住的一件事**（考试会考、或后面几节都靠它）先想清楚 ——
   它就是第一段的开头句；其余的话都围着它展开。写多少**由"讲明白"决定**：简单的一节可以只有
   2 个短段，难点/重点才多花笔墨（大致 300–450 字只是**上限参考**，不是要凑到的字数）。
⚠️ **花字数的依据是重要性和难度**：核心、难懂的想法配例子、一步步讲；顺带的旁支结论一句话
   带过、或干脆不写。**如果一个细节帮不上读者"理解核心 / 考试拿分"，就不要写它**。
`prose` 是**连贯的中文段落**，按这个顺序讲透：
① 它是什么（大白话）→ ② 为什么需要它 / 在经济学或社会学里解决什么问题 →
③ 在哪里用到 → ④ 这门课/这类考试会怎么考。
⚠️ **每段开头用一句完整的断言句**（会被加粗显示）—— 第一段的断言句 = **本节的重点**；
   一段一个想法；用明确的连接词引导读者（先看… / 但问题在于… / 于是… / 这样一来…），
   让段落之间是一条线，不是一盒碎块。
⚠️ **不要一条一条 bullet 堆**：用完整的句子把道理讲通；公式用 LaTeX（行内 $…$，独立 $$…$$）。
⚠️ 术语首次出现写「中文（English term）」；英文原话**只在**引用定义原文/考试原题时出现。
⚠️ **重点/难点要配一个具体例子**（课上讲的数字 / 情境 / 对比都行；课上真没讲例子就算）——
   例子只用来把重点讲透，不是每节都要凑一个；**绝不许自己编例子/编数字**。
⚠️ **每一句都要带信息**：不写"这节课很重要""这个概念很关键"这类空话；不把标题换个说法再写一遍。
⚠️ **过渡段 / 课务 / 学生提问这类"没在讲知识"的片段**：**不许单独写一段讲解** ——
   能并进邻段的就一句话带过，不能就索性不写；同一个意思更不许拆成两段各说一遍。
⚠️ 正文里**不许写钟点时间戳**（`12:50:41` 这种）—— 时间已经在节标题上；正文要指位置写（第 N 节）。
⚠️ **不要退化成考点清单**：把讲解写成一串"要记住 X、要记住 Y"的清单是最常见的失败 ——
   真正的列举（步骤、分类、对比项）才用 bullet，其余一律写成连贯的段落。

另外按需填：
- `q`：**本节回答的一个问题**（≤30 字，问句）—— 读者复习时只看这一问就该想起这一节。
- `cues`：1–3 条**线索问题**（≤25 字/条）：复习时盖住正文、只看问题自问自答用的 ——
  只问**概念 / 方法 / 结论**（答得出 = 这一节真懂了）；**不许问纯算术**（"5.3除以3等于多少？"
  这种计算器就能答的题帮不了复习，写了也会被丢掉）。
- `conclusion`：**考试结论**（≤40 字）—— 这类题怎么答才拿分（可空）。
- `algebra`：**代数主线**（≤80 字）：只列式子与结果的推导主线（这节真的用公式推了才填，可空）。
- `intuition`：**一句人话直觉**（≤40 字）：不写公式也能记住的那种说法（可空）。
- `prereq`：理解这一节需要、而**教授没讲**的前置概念 —— 揉碎了补上（懂就空着）。
- `prereq_link`：这一节用到上一节的什么（衔接一句话，第一节能空）。
- `example`：**教授课上做的例题，完整写出来** —— 题目 + 一步步解 + **每步为什么**。
  教授没做例题就空着；有例题而你没写全，是最严重的失分。
- `variants`：在例题基础上给 1–2 道**变式题**（含完整解答步骤）。
- `faded`：把例题的关键步骤挖空的**补全题**（答案分开写）。
- `pitfalls`：学生最容易错的地方。

## 三、数学与例题的硬规矩（2026-10-17 验收抓到过反例，逐条遵守）
- **公式一律 LaTeX**：行内 `$…$`、独立 `$$…$$`。多项式/导数/分式都必须写，例如
  `$q^{3}-12q^{2}+60q$`、`$AC'(q)=2q-12$`。**不许**写 `q^3-3q^2+12q` 这种纯文本。
- **例题的题面只能来自两个地方**：🎙 教授课上**完整写出来**的式子，或 📖 课件/试卷原文
  （`example.title` 写明「📖 文件名 + 题号」或「🎙 HH:MM」）。
  ⚠️ 教授课上一道一道过的**试卷题**（如 "this was last year, question one"）两个来源都要标：
  `example.title` 写「📖 2025-12 期末卷 Exercise 5（🎙 13:29）」。只标 🎙 会让读者以为
  函数是转录里听来的（上课常常只念了片段）—— 判据会把这种例子挂进「未核对」。
  ⚠️ 题面里出现的**每个参数**都要能从出处推出来；答案里的数字都要能由题面算出。
  **严禁**为了把例题写"完整"而自己设参数、编一个函数、或在题面里写「假设」。
  课上只给了片段（如「the average cost at 2.89」「60 times this plus 500 divided by
  this point」）→ 就**只写片段**，并明确说「原题不在本课材料里 / 只听到片段」，
  在 `checks` 里标 `unchecked`。**不要反推函数。**
- 凡「在 q*=… 处取最小/最大」「代入得 v」的结论：写出是**哪个函数**、并把导数检验
  写出来（`AC'=0` 且 `AC''>0`）。没有函数就不许下极值结论。
- **停产价格（shutting down price）的口径**：本课官方材料（📖 2025-12 期末卷 Exercise 7
  官方解答）按「**平均成本 AC 的最低点**」定义（原话 "the minimum of the average cost…
  the lowest acceptable price"）。标准教科书口径是**平均可变成本 AVC 的最低点**，而 AC
  最低点叫**盈亏平衡价（break-even price）**。写到这一节时：按本课 📖 口径讲，并**加一句**
  与标准口径的对照（标 📚）。

## 三之三、正文纪律（作者 2026-10-17 验收点名，逐字遵守）
- **不许第一人称、不许写猜测过程**。正文只写结论；「我可能听错了」「或者我漏掉了」
  「可能是简化处理（假设……）」这类句子一律**不许出现**（渲染前的规则会删掉它们）。
- 不确定的事只有**一种固定写法**（原样照抄这一行，把 `……` 换成事实）：
  `⚠ 课上只给了片段：原题见 <📖 文件名/题号> / 未核对`
  例：`⚠ 课上只给了片段：原题见 📖 2025-12 期末卷 Exercise 7 / 未核对`。
- **引号里的英文必须是材料或转录里的逐字原文**。不许把两处原文用「…」拼成一句
  （渲染前会核：找不到逐字就**去掉引号**改转述）。引用教材原话时把「📖 文件 + 段」写出来。

## 三之二、正确性（比你写得漂亮重要）
- 每条事实标来源：🎙 课上（转录里确实讲过）/ 📖 课件（附给你的片段里确实有）/
  📚 标准理论（教科书级共识）。
- **转录没讲的事实只能来自 📖 片段或标 📚**；📚 用来讲通"为什么"，
  **绝不许编造本课的数字、例子、结论**。
- 冲突裁定序：📖 课件 > 📚 标准理论 > 🎙 转录。课上讲错、或 ASR 听错的要**改对**，
  并在 `corrections` 里写：{"where": 位置, "spoken": 课上原话, "fixed": 修正值,
  "basis": 依据（📖 文件名 或 📚 哪条理论）}。
- 数字/公式**三道核**：ⓐ 自己代进去算一遍（代数自洽）ⓑ 与 📖 片段里的数字对照
  ⓒ 核不了的**必须**在 `checks` 里标 `unchecked` —— 不许静默通过。
  `checks` 每条：{"claim": 断言（如 "MC(4)=12"）, "how": 怎么核的, "status": "ok|mismatch|unchecked"}。

## 四、语言与格式
- 正文**全中文**（除术语的英文原形、公式、引用原话）。
- `sections` 里每个字段都是**单行字符串**（不出现换行、markdown 标记、引号）；
  `prose` / `steps` 是字符串数组，每个元素一段/一步。
- 适合画图的节给 `plot`：kind 只能从这三个里选 —— __PLOTS__（给不出参数就空着）。

输出严格 JSON（不许多余内容）：
{"title_zh": "本段主题（中文，≤20 字）",
 "lecture_points": [{"ts": "HH:MM", "point": "教授讲的知识点（中文一句）"}],
 "sections": [
   {"heading": "小标题（中文；有术语就写「中文（English term）」）",
    "q": "本节回答的问题（≤30 字，问句）",
    "cues": ["复习线索问题 1", "（至多 3 条，≤25 字/条）"],
    "ts": "本节起点时间戳",
    "prereq_link": "用到上一节的…（可空）",
    "prose": ["第一段", "第二段"],
    "prereq": "前置概念补课（可空）",
    "conclusion": "考试结论：这类题怎么答才拿分（≤40 字，可空）",
    "algebra": "代数主线：只列式子与结果（≤80 字，可空）",
    "intuition": "一句人话直觉（≤40 字，可空）",
    "example": {"title": "例题名（标出处，如 📖 Exercise 5）", "prompt": "题面",
                "steps": [{"text": "这一步做什么", "why": "为什么这一步成立"}],
                "answer": "结论"},
    "variants": [{"q": "变式题", "steps": ["…"], "a": "答案"}],
    "faded": {"q": "补全题（步骤挖空）", "blanks": ["空1的答案", "空2的答案"], "a": "完整解"},
    "pitfalls": ["…"],
    "terms": [{"en": "English term", "zh": "中文"}],
    "sources": ["🎙 HH:MM", "📖 文件名"],
    "plot": {"kind": "cost_curves", "title": "图名", "params": {"a": 1}}}]},
 "qa": [{"q": "自测题（中文题干，术语用英文）", "a": "答案（含步骤）", "kind": "concept|calc|trap"}],
 "corrections": [{"where": "…", "spoken": "…", "fixed": "…", "basis": "…"}],
 "checks": [{"claim": "…", "how": "…", "status": "ok"}],
 "terms": [{"en": "本课出现的学科术语", "zh": "中文"}],
 "review_plan": ["课后可以先做的 2–3 件事（中文，带分钟数）"]}
只输出 JSON。"""

#: 有骨架时的对齐（**辅助**，作者第六轮第 2 条）。
#: ⭐ 它不再负责覆盖度的**数字**：那句"每条要点给一行"的输出要点上百条时会被
#: `max_tokens` 截断 → `{}` → 三节课全报"0/117 条落到节里"。数字由
#: `note_cover.report` 按时间窗自己算；这里只留两样**模型看得见而代码看不见**的：
#: 笔记引用了哪些课件、以及它觉得哪几条没讲到（当提示，最多 10 条）。
ALIGN_SYS_SK = """你在给一节英文课的**复盘笔记**做**课件核对**。输入是：
① 这节课的知识点清单（来自课堂实时分章 + 实时要点，每条前面有编号 `[n]`）；
② 这份笔记的小节标题清单。
请输出严格 JSON：
{"materials_used": ["笔记里引用到的课件文件名"],
 "not_covered": [{"i": <编号（整数，原样抄）>, "why": "为什么觉得没讲到（≤20 字）"}],
 "notes": "一句总评（≤60 字）"}
⚠️ `not_covered` **最多 10 条**：只写你确实在小节标题里找不到出处的，编号只许原样抄。
⚠️ 覆盖度的数字由代码按时间窗算、不看你这一栏 —— 这一栏只是给作者的提示。
⚠️ 只做对照，不改写知识点。只输出 JSON。"""

ALIGN_SYS = """你在给一节英文课的**复盘笔记**做覆盖度核对与课件对齐。输入是：
① 教授这节课讲过的知识点清单（来自分块转录，按时间排序，已带时间戳）；
② 这份笔记的小节标题清单。

请输出严格 JSON：
{"covered": [{"point": "知识点（原样抄输入）", "section": "覆盖它的小节标题或 '缺失'"}],
 "materials_used": ["笔记里引用到的课件文件名"],
 "missing": [{"ts": "HH:MM", "point": "没被覆盖的知识点", "why": "为什么可能漏了（一句）"}],
 "notes": "一句总评：这份笔记的覆盖度如何、最该补什么（≤60 字）"}
⚠️ 只做对照，不要发明输入里没有的知识点。只输出 JSON。"""

VERIFY_SYS = """你在给课堂笔记做**数字与公式的第三道核**（前两道是写作时的代数自洽与课件对照）。
输入是一批断言（公式/代入/方程的解），以及可能附上的课件片段。

⚠️ 每条断言前面标着它出自哪一类内容，核的**口径完全不同**：
  · `讲解（🎙/📖）` —— 课上讲的、课件里的数字断言。**只有这一类**可以判 mismatch。
  · `例题解` / `变式` / `补全题` / `自测` —— 那是笔记作者自己出的题。
    每题都附了**它自己那一道题的题面**：只按那一道题的参数核，
    别的题（例题、别的变式）的参数**与它无关**。
    ⚠️ 实测反例：变式写「价值 5 → 5−3=2」（对的），核的时候拿例题的
    「价值 4」去比 → 报成 mismatch，把一套对的解答改错了。
    这一类**一律不许判 mismatch**：不一致也别改，判 "unchecked"。

对每一条：
1. 自己把代数**重算一遍**（代入、移项、求导、解方程）；
2. 如果附了课件片段，对照里面的数字；
3. 判 status：
   - "ok"：重算结果与断言一致（或与课件一致）；
   - "mismatch"：重算结果与断言**不一致**（此时 note 里写正确的值）——
     **只对 `讲解（🎙/📖）` 那一类**；
   - "unchecked"：信息不足、算不出来，或出自模型自己出的题（**不许猜**，标它）。
输出严格 JSON：
{"items": [{"i": 序号, "status": "ok|mismatch|unchecked", "recomputed": "你算出来的值", "note": "依据或为什么不一致（≤60 字）"}]}
只输出 JSON。"""


#: 「五分钟串讲」（版式 A，作者 2026-10-18 拍板 + 当日追认）：合并后的一次小调用。
#: ⚠️ 输入只有合并后的小节（标题 + 讲解 + 关键结果），**没有转录** —— 转录一节课 3–5 万字，
#:    而串讲要的是"整节课的线"，不是逐句材料；输入大了这笔账（时长/费用）会失控。
#: ⚠️ `max_tokens=3500`（作者 2026-10-18 追认）：约 2500 字 + JSON 外壳的上限；
#:    截断（`finish_reason=="length"` 或 JSON 解析失败）按**失败**处理 —— 走速览兜底，
#:    不许把半截叙述印上去（半截的"串讲"比没有更坏）。
OVERVIEW_MAX_TOKENS = 3500
OVERVIEW_SYS = """你在给一节课的**课后笔记**写「五分钟串讲」：把这节课**从头到尾连贯地重讲一遍**，
让一个靠中文听英文课、上课没完全跟上的大一学生，读完这五分钟先把整条线抓住 ——
它不是要点清单，是"讲一遍"。

输入：这节课每个小节的编号、标题、讲解要点与关键结果（按课堂顺序，**排在最前面**）。

## 怎么讲（一条因果链，按教学顺序，不是按标题顺序堆）
- **开头先点出这节课的 2–4 个核心想法**（"这节课其实就在讲两件事：……"），让读者先知道
  什么重要；后面的每一段都回到这几个核心想法；
- 先摆出这节课的**开场问题**（这节课要回答什么）；
- 然后一个问题接一个问题：**每个想法回答上一个想法留下的问题**，最后说清"这些加起来
  得到了什么"；
- 后一节用到前一节时，**说清前一节给了它什么**（"上一节把工具准备好了，这一节把它搬到……"）；
- 课上**留了尾巴**的地方（没讲完的例子、下节课再说的），用一句话交代清楚，不装它不存在；
- **每段动笔前想一想：这一段在讲哪个核心想法、值不值得花这段笔墨？** 核心/难点的段落多写
  （给例子、给步骤），旁支一句话带过或删掉。

## 硬规矩
⚠️ 只根据输入写：不许引入输入里没有的知识点、数字、例子、人名；不许写"输入/材料/我听到"。
⚠️ **数字与例子只能复用输入里的**（要具体：式子、数字、课上用的例子），绝不许自己编。
⚠️ 不许写猜测、感想、与本节无关的话；正文只写知识本身。
⚠️ **写多少由"讲明白"决定，不追求长也不追求短**：一段话里没有信息就删掉；重点少写清楚、
   也不硬凑。长度上限参考 **≈1200–2500 字**（不是要凑到的字数）。

## 输出（严格 JSON）
{"paragraphs": ["第 1 段", "第 2 段", "…"],
 "one_line_takeaway": "一句话说清这节课讲什么、要会什么（≤40 字）",
 "takeaways": ["最该记住的第 1 条（≤30 字，要具体）", "第 2 条", "第 3 条"],
 "self_check": "一行自检：没去上课的学生读完后能不能说出每一节的重点？yes/no + 哪一节最弱（≤60 字）"}

`paragraphs` 的规矩（逐条遵守）：
1. **6–10 段**，每段**一个想法**，说出一个完整的道理再进下一段；
2. 每段**开头一句加粗的断言句**（写作 `**…。**`）；
3. **每段点名它讲的是哪些小节** —— 「（第 2 节）」「（第 2、3 节）」，编号就是输入里的编号
   （不许自己发明编号、不许写别的编号体系）；
4. 正文用连贯句子，**不要 bullet**；只有真的在列举时才用短列举；
5. 公式用 LaTeX（行内 `$…$`）；术语照输入里的写法（中文（English term））。

只输出 JSON。"""


# ---------------------------------------------------------------- 清洗
def _one_line(v) -> str:
    return " ".join(v.split()) if isinstance(v, str) else ""


def _lines(v) -> list:
    """字符串数组 -> 单行字符串数组（元素里混进换行会破坏渲染的 callout 结构）。"""
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, list):
        return []
    out = []
    for x in v:
        s = _one_line(x)
        if s:
            out.append(s)
    return out


def _steps(v) -> list:
    """`[{"text","why"}]`（也容忍纯字符串数组 —— 模型偶尔会省掉 why）。"""
    if not isinstance(v, list):
        return []
    out = []
    for x in v:
        if isinstance(x, str):
            s = _one_line(x)
            if s:
                out.append({"text": s, "why": ""})
        elif isinstance(x, dict):
            t = _one_line(x.get("text") or x.get("step") or x.get("t"))
            w = _one_line(x.get("why") or x.get("reason"))
            if t or w:
                out.append({"text": t, "why": w})
    return out


def _terms(v) -> list:
    if not isinstance(v, list):
        return []
    out = []
    for x in v:
        if isinstance(x, dict):
            en, zh = _one_line(x.get("en")), _one_line(x.get("zh"))
            if en:
                out.append({"en": en, "zh": zh})
        elif isinstance(x, str) and _one_line(x):
            out.append({"en": _one_line(x), "zh": ""})
    return out


def _clean_section(s) -> dict:
    if not isinstance(s, dict):
        return {}
    head = _one_line(s.get("heading"))
    if not head:
        return {}
    r = {"heading": head, "ts": _one_line(s.get("ts")),
         "q": _one_line(s.get("q")),
         "cues": _lines(s.get("cues"))[:3],
         "conclusion": _one_line(s.get("conclusion")),
         "algebra": _one_line(s.get("algebra")),
         "intuition": _one_line(s.get("intuition")),
         "prereq_link": _one_line(s.get("prereq_link")),
         "prose": _lines(s.get("prose")) or _lines(s.get("detail")),
         "prereq": _one_line(s.get("prereq")),
         "pitfalls": _lines(s.get("pitfalls")),
         "terms": _terms(s.get("terms")),
         "sources": _lines(s.get("sources")),
         "variants": [], "steps": [], "example": {}, "faded": {}, "plot": {}}
    ex = s.get("example")
    if isinstance(ex, dict):
        steps = _steps(ex.get("steps"))
        if steps or _one_line(ex.get("prompt")):
            r["example"] = {"title": _one_line(ex.get("title")),
                            "prompt": _one_line(ex.get("prompt")),
                            "steps": steps,
                            "answer": _one_line(ex.get("answer"))}
    for v in (s.get("variants") or []):
        if not isinstance(v, dict):
            continue
        q = _one_line(v.get("q") or v.get("question"))
        if q:
            r["variants"].append({"q": q, "steps": _steps(v.get("steps")),
                                  "a": _one_line(v.get("a") or v.get("answer"))})
    fd = s.get("faded")
    if isinstance(fd, dict):
        q = _one_line(fd.get("q"))
        if q:
            r["faded"] = {"q": q, "blanks": _lines(fd.get("blanks")),
                          "a": _one_line(fd.get("a") or fd.get("answer"))}
    pl = s.get("plot")
    if isinstance(pl, dict) and _one_line(pl.get("kind")) in PLOT_KINDS:
        params = pl.get("params")
        r["plot"] = {"kind": _one_line(pl.get("kind")),
                     "title": _one_line(pl.get("title")),
                     "params": params if isinstance(params, dict) else {}}
    return r


def _clean(obj) -> dict:
    """一块的 JSON -> 规范化的 dict。缺字段一律丢弃，**绝不抛**。"""
    if not isinstance(obj, dict):
        return {}
    r: dict = {}
    for k in ("title_zh", "title"):
        v = _one_line(obj.get(k))
        if v:
            r["title_zh"] = v
            break
    pts = []
    for p in (obj.get("lecture_points") or []):
        if isinstance(p, dict):
            t = _one_line(p.get("point") or p.get("text"))
            if t:
                pts.append({"ts": _one_line(p.get("ts")), "point": t})
    if pts:
        r["lecture_points"] = pts
    secs = [x for x in (_clean_section(s) for s in (obj.get("sections") or [])) if x]
    if secs:
        r["sections"] = secs
    qa = []
    for x in (obj.get("qa") or []):
        if not isinstance(x, dict):
            continue
        q, a = _one_line(x.get("q")), _one_line(x.get("a"))
        if q and a:
            qa.append({"q": q, "a": a, "kind": _one_line(x.get("kind"))})
    if qa:
        r["qa"] = qa
    corr = []
    for x in (obj.get("corrections") or []):
        if not isinstance(x, dict):
            continue
        fixed = _one_line(x.get("fixed"))
        if fixed:
            corr.append({"where": _one_line(x.get("where")),
                         "spoken": _one_line(x.get("spoken")),
                         "fixed": fixed, "basis": _one_line(x.get("basis"))})
    if corr:
        r["corrections"] = corr
    checks = []
    for x in (obj.get("checks") or []):
        if not isinstance(x, dict):
            continue
        claim = _one_line(x.get("claim"))
        if claim:
            st = _one_line(x.get("status")).lower()
            checks.append({"claim": claim, "how": _one_line(x.get("how")),
                           "status": st if st in ("ok", "mismatch", "unchecked") else "unchecked"})
    if checks:
        r["checks"] = checks
    terms = _terms(obj.get("terms"))
    if terms:
        r["terms"] = terms
    plan = _lines(obj.get("review_plan"))
    if plan:
        r["review_plan"] = plan
    return r


# ---------------------------------------------------------------- 合并
def _dedup(items: list, key) -> list:
    seen, out = set(), []
    for x in items:
        k = key(x)
        if k and k not in seen:
            seen.add(k)
            out.append(x)
    return out


def merge_blocks(blocks: list) -> dict:
    """各块结果 -> 一份 review dict。**按块序**（合并顺序就是笔记顺序 = 课堂顺序）。

    ⚠️ 小标题加 `[HH:MM]` 前缀（多块时）：读者/模型都要知道"这段在课上的哪一段"，
       也是 `_render` 里回链的锚。
    """
    live = [b for b in blocks if b]
    if not live:
        return {}
    multi = len(live) > 1
    out: dict = {"sections": [], "qa": [], "corrections": [], "checks": [],
                 "terms": [], "lecture_points": [], "review_plan": []}
    for b in live:
        if b.get("title_zh") and not out.get("title_zh"):
            out["title_zh"] = b["title_zh"]
        for s in (b.get("sections") or []):
            if multi and s.get("ts"):
                s = dict(s, heading=f"[{s['ts'][:5]}] {s['heading']}")
            out["sections"].append(s)
        for k, key in (("qa", lambda x: x["q"].strip().lower()),
                       ("corrections", lambda x: (x["where"] + x["spoken"]).strip().lower()),
                       ("checks", lambda x: x["claim"].strip().lower()),
                       ("terms", lambda x: x["en"].strip().lower()),
                       ("lecture_points", lambda x: x["point"].strip().lower())):
            out[k].extend(b.get(k) or [])
        out["review_plan"].extend(b.get("review_plan") or [])
    def _key(k):
        return _DEDUP_KEY.get(k, lambda x: str(x).strip().lower())
    out = {k: (v if k in ("title_zh", "sections") else _dedup(v, _key(k)))
           for k, v in out.items()}
    out["sections"] = [s for s in out["sections"] if s]
    return {k: v for k, v in out.items() if v}


_DEDUP_KEY = {
    "qa": lambda x: x["q"].strip().lower(),
    "corrections": lambda x: (x["where"] + x["spoken"]).strip().lower(),
    "checks": lambda x: x["claim"].strip().lower(),
    "terms": lambda x: x["en"].strip().lower(),
    "lecture_points": lambda x: x["point"].strip().lower(),
}


# ---------------------------------------------------------------- 五分钟串讲
#: 每节喂给串讲调用的讲解上限（字符）。串讲要的是"线的信息"，不是全文复述——
#: 全部喂进去（ECON10770 那种 22 节的课）输入能到 3 万字，代价和时长都不划算。
_OVERVIEW_SEC_CHARS = 420


def overview(review_data: dict, *, chat, chat_meta=None,
             max_tokens: int = OVERVIEW_MAX_TOKENS) -> dict:
    """合并后的小节 → 「五分钟串讲」（**一次小调用**，输入没有转录）。

    返回 `{"paragraphs", "one_line", "takeaways", "self_check"}`；没得写 / 调用失败 /
    **被 max_tokens 截断** → `{}`（fail-soft：这一层失败只少串讲与速览首句，笔记照常）。
    ⚠️ `chat_meta(system, user, max_tokens) -> (obj, finish_reason)` 是可选的更严通道：
       有它才能看见 `finish_reason == "length"` —— 截断的 JSON 有时仍能解析出来，
       而半截的"串讲"比没有更坏（作者 2026-10-18 追认）。没有它时只靠 JSON 解析失败兜底。
    """
    secs = [s for s in (review_data.get("sections") or []) if isinstance(s, dict)]
    if not secs:
        return {}
    lines: list = []
    for i, s in enumerate(secs, 1):
        head = _one_line(s.get("heading"))
        prose = "".join(str(x) for x in (s.get("prose") or []))[:_OVERVIEW_SEC_CHARS]
        q = _one_line(s.get("q"))
        ex = s.get("example") or {}
        ans = _one_line(ex.get("answer"))
        bits = [f"第 {i} 节「{head}」", f"讲解：{prose}"]
        if q:
            bits.append(f"本节回答：{q}")
        if ans:
            bits.append(f"例题结果：{ans}")
        lines.append("\n".join(bits))
    # ⚠️ 小节在前、指令在**最后再重复一遍**（作者 2026-10-18：长输入里结尾的指令落地率最高）。
    user = ("这节课的小节（按课堂顺序）：\n\n" + "\n\n".join(lines)
            + "\n\n现在写「五分钟串讲」：一条因果链按教学顺序把整节课重讲一遍（≈1200–2500 字），"
              "数字与例子只许复用上面的内容，每段点名小节号；严格输出 JSON"
              "（paragraphs / one_line_takeaway / takeaways / self_check）。")
    try:
        if chat_meta is not None:
            obj, finish = chat_meta(OVERVIEW_SYS, user, max_tokens)
            if str(finish or "").lower() == "length":
                return {}          # 截断 = 失败（宁可没有，不许印半截）
        else:
            obj = chat(OVERVIEW_SYS, user, max_tokens)
    except Exception:                                          # noqa: BLE001
        return {}
    if not isinstance(obj, dict):
        return {}
    out: dict = {}
    one = _one_line(obj.get("one_line_takeaway") or obj.get("one_line"))
    if one:
        out["one_line"] = one
    takes = _lines(obj.get("takeaways"))[:3]
    if takes:
        out["takeaways"] = takes
    paras = _lines(obj.get("paragraphs") or obj.get("overview_long"))
    if paras:
        out["paragraphs"] = paras
    sc = _one_line(obj.get("self_check"))
    if sc:
        out["self_check"] = sc
    return out


def transcript_lines(entries: list) -> list:
    return [f"[{e['ts']}] {e['en'] or e['asr']}"
            for e in entries if (e.get("en") or e.get("asr"))]


def chunk_lines(lines: list, size: int = REVIEW_CHUNK) -> list:
    return [lines[i:i + size] for i in range(0, len(lines), size)]


def _user_content(lines: list, *, domain: str = "", materials_block: str = "",
                  extra: str = "") -> str:
    body = "\n".join(lines)
    head = f"Course: {domain}\n" if domain else ""
    return (f"{head}{materials_block}{extra}"
            f"Transcript (each line starts with its timestamp):\n{body}")


def review(entries: list, *, chat, domain: str = "", materials=None,
           on_progress=None, workers: int = REVIEW_WORKERS,
           max_tokens: int = REVIEW_MAX_TOKENS, extra: str = "",
           skeleton: list | None = None) -> dict:
    """分块调 LLM → 合并。`chat(system, user, max_tokens) -> dict`。

    ⚠️ 并发发、**按块序收**（同旧版 `_review` 的取舍：串行那 4–5 块是下课→笔记
       最长的一段）。某块失败只少那一块 —— 绝不抛、绝不整体返回空。
    """
    lines = transcript_lines(entries)
    if not lines:
        return {}
    chunks = chunk_lines(lines)
    meta: dict = {}
    from concurrent.futures import ThreadPoolExecutor
    from materials import snippets_block

    def one(chunk: list) -> dict:
        """一块 → 一次调用；**失败要退一步**。

        ⚠️ 第四轮实测（run F）：带上章节骨架后，17/20 条要点的那几块输出超过
           `max_tokens`（6000）→ JSON 被截断 → `chat_json` 抛 → 这里吞成 `{}` →
           **4/5 块静默消失**，笔记只剩最后一块（2 节）。现在两条腿：
           ① 先按原样（带骨架）重试一次；② 还失败就去掉骨架、放大 token 再来一次
           （宁可按老版式写全，也不许整块丢）。失败块数会报给调用方。
        """
        import note_cover
        sk_block = note_cover.block_for(skeleton, chunk) if skeleton else ""
        hits = materials.search("\n".join(chunk)) if materials is not None else []
        m_block = snippets_block(hits)
        tries = [(extra + sk_block, max_tokens),
                 (extra + sk_block, int(max_tokens * 1.5)),
                 (extra, int(max_tokens * 1.5))]
        for ex2, mt in tries:
            try:
                user = _user_content(chunk, domain=domain, materials_block=m_block, extra=ex2)
                got = _clean(chat(REVIEW_SYS.replace("__PLOTS__", _plots_menu()), user, mt))
                if got:
                    return got
            except Exception:                                 # noqa: BLE001
                continue
        return {}

    failed: list = []

    ex = ThreadPoolExecutor(min(workers, len(chunks)))
    futs = [ex.submit(one, c) for c in chunks]
    ex.shutdown(wait=False)
    blocks = []
    for ci, fut in enumerate(futs):
        if on_progress is not None:
            try:
                on_progress("review", ci, len(chunks))
            except Exception:                                 # noqa: BLE001
                pass
        try:
            b = fut.result()
        except Exception:                                     # noqa: BLE001
            b = {}
        if not b:
            failed.append(ci)
        blocks.append(b)
    got = merge_blocks(blocks)
    if got.get("title_zh"):
        meta["title_zh"] = got.pop("title_zh")
    got.update(meta)
    # ⚠️ 失败块**记账**（不静默）：调用方/验收日志要看得见"这半节课没生成"。
    #    ⚠️ 只有真的生成了内容才挂这个键 —— 全失败时必须还是 `{}`（调用方据此早退）。
    if got:
        got["chunk_stats"] = {"n": len(chunks), "failed": sorted(failed)}
    return got


def _plots_menu() -> str:
    return " · ".join(f"`{k}`（{v}）" for k, v in PLOT_KINDS.items())


# ---------------------------------------------------------------- 覆盖度对齐
def align(review_data: dict, *, chat, materials=None,
          max_tokens: int = ALIGN_MAX_TOKENS, skeleton: list | None = None) -> dict:
    """课件核对 + 覆盖度提示（1 次调用）。**辅助信息**，不是覆盖度的口径。

    ⚠️ 带 `skeleton` 时清单**不是**模型自己列的那份，而是章节骨架里的要点，按
       **编号**对齐（`[3]` 这种）—— 让模型"抄一遍长句"再匹配，错一个词就丢一条。
    ⭐ 第六轮（作者 2026-10-17）：这一层**不再决定覆盖度的数字**（要点上百条时
       整份回执会被 `max_tokens` 截断，异常又被吞成 `{}` → "0/117 条落到节里"、
       §9 假报"全缺"）。所以：有骨架时只问两件事（引用了哪些课件、觉得哪几条没讲），
       回执很小，`ALIGN_MAX_TOKENS=2000` 绰绰有余；**失败必须报出去**
       （`{"failed": ...}`），不许再吞成空 dict 让上层以为"模型说一条都没覆盖"。
    ⚠️ 没有骨架的退化档（旧会话 / 开关关着）仍然按文字对齐：那里只有模型自己列的
       知识点清单，按文字匹配是唯一的口径。
    """
    sk_points = None
    if skeleton:
        import note_cover
        sk_points = note_cover.flat_points(skeleton)
    pts = ([{"ts": p["ts"], "point": p["point"], "gi": p["gi"]} for p in sk_points]
           if sk_points else (review_data.get("lecture_points") or []))
    secs = [s.get("heading", "") for s in (review_data.get("sections") or [])]
    if not pts or not secs:
        return {}
    if sk_points:
        lines = "\n".join(f"[{p['gi']}] ({p['ts']}) {p['point']}" for p in sk_points)
    else:
        lines = "\n".join(f"[{p.get('ts', '')}] {p.get('point', '')}" for p in pts)
    user = (f"Professor's knowledge points (in order):\n{lines}\n\n"
            f"Note section headings:\n" + "\n".join(f"- {s}" for s in secs))
    sys_prompt = ALIGN_SYS_SK if sk_points else ALIGN_SYS
    try:
        obj = chat(sys_prompt, user, max_tokens)
    except Exception as e:                                     # noqa: BLE001
        # ⚠️ 这里**不吞**：上层按 `failed` 报出去（作者第六轮第 2 条）。
        return {"failed": f"{type(e).__name__}: {str(e)[:80]}"}
    if not isinstance(obj, dict):
        return {"failed": "模型没回 JSON 对象"}
    out: dict = {}
    covered = []
    by_gi = {p["gi"]: p for p in (sk_points or [])}
    for x in (obj.get("covered") or []):
        if not isinstance(x, dict):
            continue
        s = _one_line(x.get("section"))
        if by_gi:
            try:
                gi = int(x.get("i"))
            except (TypeError, ValueError):
                continue
            p = by_gi.get(gi)
            if p:
                covered.append({"point": p["point"], "section": s, "i": gi,
                                "ts": p.get("ts", "")})
        else:
            p = _one_line(x.get("point"))
            if p:
                covered.append({"point": p, "section": s})
    if covered:
        out["covered"] = covered
    # ⭐ 有骨架时：`not_covered` 是**提示**（最多 10 条），进 `missing` 那一栏给人看。
    hints = []
    for x in (obj.get("not_covered") or [])[:10]:
        if not isinstance(x, dict):
            continue
        p = by_gi.get(x.get("i")) if isinstance(x.get("i"), int) else None
        if p:
            hints.append({"ts": p.get("ts", ""), "point": p["point"],
                          "why": _one_line(x.get("why"))})
    if hints:
        out["missing"] = hints
    else:
        missing = []
        for x in (obj.get("missing") or []):
            if isinstance(x, dict):
                p = _one_line(x.get("point"))
                if p:
                    missing.append({"ts": _one_line(x.get("ts")), "point": p,
                                    "why": _one_line(x.get("why"))})
        out["missing"] = missing
    used = _lines(obj.get("materials_used"))
    if used:
        out["materials_used"] = used
    notes = _one_line(obj.get("notes"))
    if notes:
        out["notes"] = notes
    return out


# ---------------------------------------------------------------- 数字三道核
_NUM = re.compile(r"\d+(?:\.\d+)?")


def _claim_text(v) -> str:
    """`checks` 里的一条断言 → 单行字符串。

    ⚠️ 2026-10-17 验收实测：模型偶尔把**整个 dict** 塞进 `claim`（`{"text": "…",
       "why": ""}` 的形状，那是 `steps` 的 schema）—— 不收拾它，笔记的核定表里
       就会出现 `{'text': 'f(1)=…', 'why': ''}` 这种字面量（读者以为笔记坏了）。
    """
    if isinstance(v, dict):
        for k in ("claim", "text", "q", "step", "statement"):
            if v.get(k):
                return _one_line(str(v[k]))
        return ""
    return _one_line(str(v or ""))


def _clean_checks(rows) -> list:
    """模型自己写的 `checks` → 规范化（`claim` 一定是字符串、`status` 一定有）。

    ⚠️ 每一条**早退路径**（没有断言 / 调用炸了 / 返回不是 JSON）都从 `checks` 直接
       返回给渲染层 —— 所以收拾必须放在这一处，四条路一起走（只在末尾收拾的话，
       早退那条路照样把 dict 字面量写进笔记）。
    """
    out = []
    for c in (rows or ()):
        if not isinstance(c, dict):
            continue
        txt = _claim_text(c.get("claim"))
        if not txt:
            continue
        row = dict(c)
        row["claim"] = txt
        if not isinstance(row.get("recomputed"), str):
            row["recomputed"] = ""
        st = str(row.get("status") or "").strip().lower()
        row["status"] = st if st in ("ok", "mismatch", "unchecked") else "unchecked"
        out.append(row)
    return out


def numeric_claims(review_data: dict, *, limit: int = 24) -> list:
    """从笔记里**挑出值得核的数字断言**（纯函数，判据靠它）。

    ⚠️ 只挑"带等号/结论感"的那种（`MC(4)=12` / `q = 6` / `AC = 24`），
       普通叙述里的数字（"这节课 26 分钟"）不该占核对的额度 ——
       额度有限（每次调用只核一批），要花在会被扣分的地方。

    ⭐ 第六轮（作者 2026-10-17）：每条断言带 `origin`（出自哪一类内容）与 `ctx`
       （**它自己那一道题的题面**）。理由是全套假修正的根因 —— 变式与例题的断言
       混在一批里核，模型拿例题的参数去判变式（"价值应为 4 非 5"）。
    """
    out: list = []
    for s in (review_data.get("sections") or []):
        head = _one_line(s.get("heading"))
        ex = s.get("example") or {}
        if ex.get("prompt") or ex.get("steps") or ex.get("answer"):
            ctx = f"本节例题「{_one_line(ex.get('title')) or '例题'}」的题面：{_one_line(ex.get('prompt'))[:160]}"
            texts = ([ex.get("prompt", ""), ex.get("answer", "")]
                     + [st.get("text", "") for st in (ex.get("steps") or [])])
            out += _num_items(head, texts, "example", ctx, limit)
        for j, v in enumerate(s.get("variants") or [], 1):
            ctx = f"本节变式 {j} 自己的题面：{_one_line(v.get('q'))[:160]}"
            texts = ([v.get("q", ""), v.get("a", "")]
                     + [st.get("text", "") for st in (v.get("steps") or [])])
            out += _num_items(head, texts, "variant", ctx, limit)
        fd = s.get("faded") or {}
        out += _num_items(head, [fd.get("q", ""), fd.get("a", "")], "faded",
                          f"本节补全题的题面：{_one_line(fd.get('q'))[:160]}", limit)
        # 讲解排在每节的最后：额度先给"模型自己出的题"（它们最容易串参数），
        # 讲解那一档量大且稳（作者的正文纪律已经压过一轮）。
        out += _num_items(head, list(s.get("prose") or []), "prose", "", limit)
        if len(out) >= limit:
            return out[:limit]
    for q in (review_data.get("qa") or []):
        out += _num_items("自测", [q.get("a", "")], "qa",
                          f"自测题：{_one_line(q.get('q'))[:160]}", limit)
        if len(out) >= limit:
            return out[:limit]
    # 已有 checks 的也带上（模型自己写的断言最值得复核）
    for c in (review_data.get("checks") or []):
        txt = _claim_text((c or {}).get("claim"))
        if txt:
            out.append({"where": "checks", "text": txt, "origin": "checks", "ctx": ""})
        if len(out) >= limit:
            break
    return out[:limit]


def _num_items(where: str, texts, origin: str, ctx: str, limit: int) -> list:
    """一批文本 → 值得核的断言（`limit` 是全局额度，这里只做形状判据）。"""
    out: list = []
    for t in texts:
        if len(out) >= limit or not t or not _NUM.search(str(t)):
            continue
        if not re.search(r"[=＝]|约等于|等于", str(t)):
            continue
        out.append({"where": where, "text": _one_line(str(t)), "origin": origin,
                    "ctx": ctx})
    return out


#: `origin` → 给模型看的标签（口径不同，见 `VERIFY_SYS`）。
_ORIGIN_ZH = {"prose": "讲解（🎙/📖）", "example": "例题解", "variant": "变式",
              "faded": "补全题", "qa": "自测", "checks": "讲解（🎙/📖）"}
#: 「模型自己出的题」——判据用 `note_math.MODEL_AUTHORED` + 自测。
_MODEL_ORIGINS = set(note_math.MODEL_AUTHORED) | set(note_math.ALSO_MODEL)
#: 收窄降级在行尾留下的**可数标记**（评测按它统计"这条本来会变成什么"）：
#: 没有它就没法在产物上看出"收窄到底挡下了几条假修正"（模型是随机的，不是每次都犯）。
DEMOTED_MISMATCH = "只标未核对，不改"
DEMOTED_OK = "自评不算核"


def _norm_text(s) -> str:
    return re.sub(r"\s+", "", str(s or "")).lower()


def _claim_origin(review_data: dict, claim: str) -> str:
    """一条断言（机械核/模型核的行）出自哪一类内容 → `prose`/`example`/…；找不到返回 `""`。
    ⚠️ `""`（查不到出处）在 `cap_model_rows` 里与"模型自己出的题"**同等对待**：
       一条查不到出处的断言不该拿到"改正文"的权力 —— 反过来（找不到就算讲解）
       正好会让"错的修正"这类事故从缝里钻回来。
    """
    txt = _claim_text(claim)
    if not txt:
        return ""
    for s in (review_data.get("sections") or []):
        k = note_math.unit_of_claim(s, txt)
        if k:
            return k
    for q in (review_data.get("qa") or []):
        t = _norm_text(q.get("a"))
        if t and (_norm_text(txt) in t or t[:60] in _norm_text(txt)):
            return "qa"
    for c in (review_data.get("checks") or []):
        if _norm_text(txt) == _norm_text(_claim_text(c.get("claim"))):
            return "checks"
    return ""


def cap_model_rows(rows: list, review_data: dict, *, llm: bool = False) -> list:
    """**模型自己出的题：不许出现 `mismatch`**（作者第六轮第 1 条）。

    实现的口径：
      · `example` / `variants` / `faded` / `qa` 里的断言，`mismatch` 一律降成
        `unchecked`（重算值留在 `how` 里，读者仍看得到"机器算出来是多少"，
        但**不会**变成一条"已修正"——那正是假修正的来源）；
      · `llm=True` 时这些内容上的 `ok` 也降成 `unchecked`：让模型给自己出的题
        判"通过"，等于没核（它对自己的解答当然自洽）；
      · `讲解（🎙/📖）` 里那些来自转录/课件的断言**原样保留**（那是这份笔记
        真正要守的东西：教授口误 / ASR 听错 / 实时摘要抄错）。
    """
    out = []
    for r in (rows or []):
        if not isinstance(r, dict):
            continue
        row = dict(r)
        st = str(row.get("status") or "")
        if st not in ("mismatch", "ok"):
            out.append(row)
            continue
        origin = _claim_origin(review_data, str(row.get("claim") or ""))
        why = ("模型自己出的题" if origin in _MODEL_ORIGINS
               else "找不到这条断言出自哪里" if origin == "" else "")
        if st == "mismatch" and why:
            row["status"] = "unchecked"
            row["how"] = (str(row.get("how") or "") + f"（{why}：只标未核对，不改）").strip()
        elif st == "ok" and why and llm:
            row["status"] = "unchecked"
            row["how"] = (str(row.get("how") or "") + "（模型自己出的题：自评不算核）").strip()
        out.append(row)
    return out


def verify_numbers(review_data: dict, *, chat, materials=None,
                   transcript: str = "", max_tokens: int = VERIFY_MAX_TOKENS) -> list:
    """数字/公式第三道核（1 次调用）→ 规范化后的 `checks` 列表（不是 dict）。

    ⚠️ `mismatch` **不改正文**：它进「修正说明」清单由渲染层显示 ——
       静默改数字是这份笔记最不能做的事（读者用它对答案）。
    ⭐ 第六轮：`mismatch` 只留给**讲解里那些来自 🎙 转录 / 📖 课件的断言**；
       例题解答 / 变式 / 补全题 / 自测是模型自己出的题，最多标未核对。
    """
    # ⭐ 机械核**先跑**（2026-10-17 验收：模型对着自己编的函数"重算"当然自洽 ——
    #    自编 `TC=60q+500` + 凑出来的驻点 2.89 就这样溜过了三道核）。
    det = note_math.check_review(review_data, transcript=transcript)
    claims = numeric_claims(review_data)
    if not claims:
        return _finish(det, _clean_checks(review_data.get("checks")), review_data)
    rows = []
    evidence = []
    for i, c in enumerate(claims):
        tag = _ORIGIN_ZH.get(c.get("origin"), "讲解（🎙/📖）")
        ctx = f"；{c['ctx']}" if c.get("ctx") else ""
        rows.append(f"[{i}] (节「{c['where']}」· {tag}{ctx}) {c['text']}")
        if materials is not None:
            for hit in materials.search(c["text"], k=1):
                evidence.append(f"[{i}] {hit.cite()} {hit.text[:300]}")
    user = ("Claims:\n" + "\n".join(rows)
            + (("\n\nMaterials excerpts:\n" + "\n".join(evidence[:6]))
               if evidence else ""))
    try:
        obj = chat(VERIFY_SYS, user, max_tokens)
    except Exception:                                         # noqa: BLE001
        return _finish(det, _clean_checks(review_data.get("checks")), review_data)
    items = obj.get("items") if isinstance(obj, dict) else None
    if not isinstance(items, list):
        return _finish(det, _clean_checks(review_data.get("checks")), review_data)
    got: dict = {}
    for it in items:
        if not isinstance(it, dict) or not isinstance(it.get("i"), int):
            continue
        i = it["i"]
        if not (0 <= i < len(claims)):
            continue
        st = _one_line(it.get("status")).lower()
        got[i] = {"claim": claims[i]["text"][:160],
                  "how": (_one_line(it.get("note")) or "")[:160],
                  "status": st if st in ("ok", "mismatch", "unchecked") else "unchecked",
                  "recomputed": _one_line(it.get("recomputed"))[:80]}
    out = [got[i] for i in sorted(got)]
    # 模型自己写的 checks 也留着（它们带着别的依据）；同 claim 以核过的为准。
    seen = {c["claim"].strip().lower() for c in out}
    for row in _clean_checks(review_data.get("checks")):
        if row["claim"].lower() not in seen:
            row["recomputed"] = ""
            out.append(row)
    return _finish(det, out, review_data)


def _finish(det: list, llm_rows: list, review_data: dict) -> list:
    """机械核与模型核**各自**过一遍收窄，再合并。

    ⚠️ 两边的口径不同，**不能合并后再一起降**：
       · 机械核是**算出来的**（代入/求导/解方程），它说 `ok` 就是算对了 ——
         即便那条断言在"模型自己出的题"里，也不该被降成未核对（降了等于白算）；
       · 模型核是在**评自己的作业**，它说 `ok` 不算核过 → 这一类要降。
       合并后再降会把机械核那 30 条 `ok` 一起抹掉（实测：10790 的 ✅ 从 61 掉到 17）。
    """
    return note_math.merge_checks(cap_model_rows(det, review_data),
                                  cap_model_rows(llm_rows, review_data, llm=True))


def corrections_from_mismatch(checks: list) -> list:
    """`mismatch` -> 修正说明条目（渲染层与附录共用一条口径）。"""
    out = []
    for c in checks or []:
        if c.get("status") == "mismatch":
            out.append({"kind": "check",
                        "where": "三道核",
                        "spoken": c.get("claim", ""),
                        "fixed": c.get("recomputed", ""),
                        "basis": c.get("how", "")})
    return out


# ---------------------------------------------------------------- 复习排程
#: 三个复习档（作者 2026-10-18 拍板：**今晚 / +3 天 / +7 天**，日期从上课那天算）。
#: ⚠️ 每档配的动作是**静态**的（与课程无关）：现在重做例题，三天后做变式+自测，
#:    七天后只看线索问题口头答 —— 不再用早先的 2/7/21 天（那时没有"今晚"这一档，
#:    而作者要的是"下课今晚就有一件事可做"）。
REVIEW_SLOTS = (
    ("今晚", 0, "重做例题 + 补全题"),
    ("+3 天", 3, "变式 + 自测"),
    ("+7 天", 7, "只看线索问题，口头答"),
)


def review_dates(date_str: str, *, offsets=(0, 3, 7)) -> list:
    """间隔复习日期（写进笔记的「复习排程」那一节；不做推送）。

    ⚠️ 间隔取 0/3/7 天（作者 2026-10-18 拍板）：上课当晚重做例题 → +3 天变式+自测 →
       +7 天只看线索问题口头答。刻意**不做**"智能排程" —— 笔记只写日期，
       什么时候看由人定。
    """
    import datetime
    try:
        d0 = datetime.date.fromisoformat(date_str)
    except ValueError:
        return []
    return [(d0 + datetime.timedelta(days=o)).isoformat() for o in offsets]


def schedule_rows(dates: list) -> list:
    """复习日期 + 静态档位 → `[{"label", "date", "task"}]`（md 与 HTML 共用一份口径）。

    ⚠️ 标签按**位置**取（今晚/+3 天/+7 天）—— 日期从上课那天算，位置天然对齐；
       日期算不出来（坏日期）时 `dates=[]`，调用方整段跳过。
    """
    out = []
    for (label, _off, task), d in zip(REVIEW_SLOTS, dates or []):
        out.append({"label": label, "date": str(d), "task": task})
    return out


def anki_rows(review_data: dict, *, course: str = "", date: str = "") -> list:
    """复习层 → Anki CSV 行 `[front, back, tags]`（**本地生成、零 LLM**）。

    ⚠️ 只导出"能当卡片"的东西：术语（英→中）、自测题（问→答）、公式结论。
       调研（`research/d-format.md`）里 Anki 社区对"自动生成的烂卡"评价很差 ——
       所以**不导出**整段讲解，也不假装它是完整的复习计划（笔记 §复习排程 里有说明）。
    """
    tags = " ".join(x for x in ("classlive", course, (date or "").replace("-", "")) if x)
    rows: list = []
    seen: set = set()
    for t in (review_data.get("terms") or []):
        en, zh = t.get("en", ""), t.get("zh", "")
        if not en or not zh or en.lower() in seen:
            continue
        seen.add(en.lower())
        rows.append([en, zh, f"{tags} terminology"])
    heads = {s.get("heading", "") for s in (review_data.get("sections") or [])}
    for q in (review_data.get("qa") or []):
        if not q.get("q") or not q.get("a"):
            continue
        k = q["q"].strip().lower()
        if k in seen:
            continue
        seen.add(k)
        rows.append([q["q"], q["a"], f"{tags} selftest"])
    for c in (review_data.get("checks") or []):
        if c.get("status") == "ok" and c.get("claim"):
            k = c["claim"].strip().lower()
            if k not in seen:
                seen.add(k)
                rows.append([c["claim"], c.get("how", ""), f"{tags} formula"])
    del heads
    return rows


def to_csv(rows: list) -> str:
    """Anki 导入用 CSV（`front,back,tags`，UTF-8 带 BOM —— Excel 打开不乱码）。"""
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    for r in rows:
        w.writerow(r)
    return "\ufeff" + buf.getvalue()


def dumps(obj) -> str:
    """调试用（收尾日志/判据里看结构）。"""
    return json.dumps(obj, ensure_ascii=False, indent=1)
