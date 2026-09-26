# 调研：社区与同类产品怎么做「开课前的准备」（P3）

> 配套阅读：`docs/PLAN-roadmap.md` §1（三件事是同一个功能）、
> `docs/RESEARCH-product-shape.md` §5（课件→关键词，本机流水线已核实）。
> 最后更新 2026-09-26。

## 0. 口径（先看这一节）

本文只做**外部调研**，不写代码。每条结论后面挂来源；来源分四档：

| 标记 | 含义 |
|---|---|
| **[一手]** | 官方文档 / 论文正文 / 官方 issue，我回源读过原文 |
| **[厂商]** | 厂商营销文案。流程描述可信，**效果宣称不可信** |
| **[论坛]** | 用户帖子 / Reddit / HN。**轶事，不作决策依据** |
| ❓ | 我**没能回源**，或不只一处对不上。列出来防止被重新捡起来 |

⚠️ **凡是我自己从证据推出来的结论，都写成「→ 推论」并单独标注**，不和引文混在一起。

⚠️ 三条本次**没找到**的东西，先写在前面（详见 §6）：
1. **没有任何公开的量化数据**证明「课前导入课件 → 术语表 → 实时字幕变好」这条回路有效。
   这是个**新颖组合**，同类产品里没有完全对口的先例。
2. **没有 Otter / Granola 自定义词表的实际增益数字**，两边都只有厂商口播。
3. **LlamATE 的正文数字拿不到**（期刊摘要级），所以「LLM 抽术语到底比监督模型差多少」
   本文只能给方向，给不了数。

---

## 1. 结论先行

| 问题 | 结论 | 依据强度 |
|---|---|---|
| 「拖文件进来」是 2026 年 macOS 上对的交互吗 | **是，但不能只有拖拽**。Apple HIG 明文要求另给一条路 | **[一手]** §3.1 |
| 同类产品怎么做这件事 | **高度一致：文件丢进去 → 自动抽 → 给你一张候选表 → 你点头才写** | **[一手]**+**[厂商]** §1.3 |
| 术语表该多大 | 同类产品**主动设上限**：Granola 30 / Otter 100；翻译侧证据支持「宁少勿滥」 | **[一手]** §2.6 §5.5 |
| 频率法（TF-IDF）能不能用在课件的 slides 上 | **不能单独用**。slides 是 headline 体，IDF 的分母（文档集）根本不存在 | **[一手]**+推论 §2.3 |
| 多词术语（multi-word terms） | 必须处理。**30% 的名词短语在语料里只出现一次**，频率过滤会把它们全杀掉 | **[一手]** §2.4 |
| 双语/二语这边的难点在哪 | **领域术语只占一节课词数的 ~4.4%**，但它是最稀有的那部分——而稀有度是翻译失败的头号预测因子 | **[一手]** §4 |
| 最大的坑 | **「喂了术语表→翻译变好」证明不了术语表内容好**（WMT 随机术语对照）——这条已在 `RESEARCH-product-shape.md` §5.2 记过，本报告补充了 WMT26 的新证据 | **[一手]** §5.5 |
| 提取环节最阴的失败 | **静默丢内容**：PPTX 里的公式形状**整个不出现在 `slide.shapes` 里**；Docling 默认**读不到 speaker notes** | **[一手]** §5.4 |

---

## 2. 同类产品：他们的导入流程长什么样

### 2.1 总表

| 产品 | 首次/每次导入怎么开始 | 从文档到词/卡片的路径 | 产物 | 一手来源 |
|---|---|---|---|---|
| **Anki 本体** | **没有「文档→卡片」这条链路** —— 只有 CSV/纯文本导入，内容得你自己排好 | 无（抽取全靠第三方） | `.apkg` / 集合 | — |
| **genanki**（第三方库） | 写 Python 脚本 | 你自己组织内容 → `Deck.write_to_file()` | `.apkg` | [genanki](https://github.com/kerrickstaley/genanki/blob/main/genanki/deck.py) |
| **anki-cards**（第三方） | `uv run make_cards.py <pdf>` | PDF → Claude → 卡 | `.apkg` | [GitHub](https://github.com/pclutton/anki-cards) |
| **AnkiDecks**（商业） | 「Drop a file here, or browse」；PDF/图片/音频/视频，上限 100 MB | AI 抽「key concepts」→ Q&A / Cloze / 选择题 | 站内复习 或 `.apkg` | [厂商](https://anki-decks.com/) |
| **CoursesToCards** | 截屏客户端（**仅 Windows**） | **抓屏**而不是解析文件（明确写「No DRM bypass」） | CSV | [厂商](https://www.coursestocards.com/) |
| **lecture-to-anki**（Claude skill） | 指定课件目录 | ⭐ **五段闸门**：解析 → 出 12–15 题诊断 quiz → 你自评 → 只给「不会的」起草 → 你批准 → 写 AnkiConnect | Anki 卡片 | [一手](https://github.com/joffreywallaart/lecture-to-anki/blob/master/SKILL.md) |
| **Readwise / Reader** | **文件直接拖进去**；PDF 也可邮件发到 `add@readwise.io` | 抽**高亮**（不是抽术语）→ 每日回顾 | 高亮 → 导出到 Obsidian/Notion | [一手](https://docs.readwise.io/readwise/docs/importing-highlights/pdf) |
| **LingQ** | Home 页 `Import Lesson` 表单（标题/图片/正文/URL/等级/标签/音频），或 Ebook 拖拽（EPUB/PDF/DOCX/TXT/MOBI） | 不做术语抽取；**它靠你阅读时点词**来积累 | Lesson + 生词表 | [一手/厂商](https://www.lingq.com/blog/importing-on-lingq) |
| **Quizlet** | 导入 = **你自己先把文本排好格式** | 「Separate terms and definitions with a comma, tab, or dash. Separate rows with a semicolon or a new line」 | 学习集 | [一手](https://help.quizlet.com/hc/en-us/articles/360029977151-Creating-sets-by-importing-content) |
| **Obsidian Spaced Repetition** | 无导入；卡**就写在笔记里**（`:::` / `??`） | 无 | 笔记内联卡 | [一手](https://open-spaced-repetition.github.io/obsidian-spaced-repetition-recall/flashcards/qanda-cards) |
| **Obsidian Text Generator** | 侧栏选中文本 | LLM 生成文本，**没有导入管线** | 笔记 | [一手](https://github.com/nhaouari/obsidian-textgenerator-plugin)（1,980 ★） |
| **StudyFetch** | 「Drop the file you already have」——PDF / slides / YouTube 链接 / 笔记 | 抽 topic → 排成学习顺序 | 笔记/卡/quiz/学习计划 | [厂商](https://www.studyfetch.com/tools) |
| **Cramberry** | 上传 PDF/PPT/**手写笔记照片**/网页/YouTube | 抽 topic + subtopic，另有「key terms」 | StudySet | [厂商](https://www.cramberry.study/syllabus-generator) |
| **Otter** | 无导入；**自动抓屏**（虚拟课堂）+ 设置里填 Custom Vocabulary | 不抽词 | 转写 + 词表 | [一手](https://otter.ai/education) / [词表](https://help.otter.ai/hc/en-us/articles/360048571373-Manage-vocabulary) |
| **Granola** | 无导入；`Settings > Preferences > Language` 里**手打**词表 | 不抽词 | 词表（**上限 30 条**） | [一手](https://docs.granola.ai/help-center/customising-granola/customising-transcription) |
| **GoodNotes** | 导入课件 PDF 到 whiteboard 画布 | 无抽词；自己写 | 手写笔记 | [厂商](https://www.goodnotes.com/blog/annotate-lecture-slides) |
| **Zotero** | **官方明确不做 watched folder**；官方答案是「拖进去就行」 | 抽元数据，不抽术语 | 文献条目 | [一手](https://www.zotero.org/support/kb/watch_folder) |

### 2.2 ⭐ 最值得抄的一份：`lecture-to-anki`

这是**唯一一份把「从课件到词」这条回路写成明确设计规范**的公开材料，
而且它自己写明「refined over several rounds of real user feedback … simpler versions
were explicitly rejected」。原文逐字（[SKILL.md](https://github.com/joffreywallaart/lecture-to-anki/blob/master/SKILL.md)）：

> **the user's own brain is the filter.** They self-quiz, mark what they don't know,
> and only verified gaps become cards. **You never push cards without an explicit go-ahead.
> Growth tracks knowledge, not slide count.**

它的五段闸门：`0` 前置 + 定 deck + 查重 → `1` 生成 12–15 题的交互式 `quiz.html` →
`2` **等**你贴回结果，再按自评预算卡片 → `3` 以表格呈现候选，**一张都不写** →
`4` 批准后写 AnkiConnect 并回查。

几条它写死的硬规矩，**逐条对本项目有映射**：

| 它的规矩（逐字要点） | 对本项目的映射 |
|---|---|
| `know` → **no card**（已经会的不要） | 抽词要**去重去已知**，不是越多越好 |
| 「**One deck per subject area**；No `<course>::<lecture>` deck hierarchies — use tags」 | ⚠️ **本项目的取舍相反**（见 §7）——注入单位就是「课」，合并会破坏隔离 |
| 「**Static sources only.** Extract from slides, textbook chapters… **Never use audio/video recordings**」 | 与 P3 定位一致：课件是**静态源**，课前批处理 |
| 生成前**先查重**（按 tag 扫一遍已学过的） | 「自动生成只能追加」这条已经有；**还需要课内幂等**（同一份课件导两次不能出两份） |
| 诊断 quiz 上限 **12–15 题**，一题一个核心定义 | 候选词也该有**总量上限**，不是全给 |

### 2.3 三条被反复验证的设计模式（→ 推论）

把上表压一下，同类产品在这条回路上**收敛到了同一套形状**：

1. **入口是「一次性动作」，不是「常驻通道」。**
   没有任何一个同类产品是「watched folder + 自动导入」当默认。Zotero 官方还专门写了
   一页解释**为什么不做**（§3.3）。StudyFetch / Cramberry / AnkiDecks 全是
   「drop a file here」的一次性动作。
2. **中间必然有一张「给你看」的候选表。**
   `lecture-to-anki` 是明写的闸门；AnkiDecks 的「AI drafts the cards; **you still review them**」
   也是明写的（[厂商](https://medankigen.com/blog/ai-anki-deck-generator)）；
   独立来源还有一个 HN 高赞回答：
   > "Make your Anki cards one at a time. Quality over quantity. … I often see people do this
   > and then a month later feel totally crushed by **hundreds of reviews information
   > completely out of context**." —— laurieg, [HN 32397162](https://news.ycombinator.com/item?id=32397162) **[论坛]**
3. **产物是「一个可按课/按主题寻址的集合」，不是一个大池子。**
   区别只在于用**文件**分隔还是用**标签**分隔。

---

## 3. ⭐ 「从文档里抽术语」这件事怎么做才对

### 3.1 先接受一件事：学术界对「什么是术语」都没统一

ATE（Automatic Term Extraction）的综述（ACM Computing Surveys，[arXiv 2301.06767](https://ar5iv.labs.arxiv.org/html/2301.06767)）逐字：

> "Diverse approaches have been applied to each different aspect, including annotation type …,
> annotation scheme (binary or multi-label, etc.), annotation guidelines
> (**term length, POS patterns, whether or not to add named entities**, etc.)."

> "ACTER solves the debate about **whether or not to consider named entities as terms**
> by providing two different versions of manual annotations: one containing only terms,
> and the other containing both terms and named entities." **[一手]**

→ **推论**：`glossary/<课号>.txt` 里该不该放 `ECON10101` / `Brightspace` 这种词，
**不是「抽得准不准」的问题，是标注口径的问题**。
业界主流做法是**两类都留、但分开标注**（D-Terminer 给四档：Specific Terms /
Common Terms / Out-of-Domain / Named Entities）——这正好支持本项目
「领域术语 + 课号教务词两类共处一个文件」的现状，只是**来源不同**。

### 3.2 TF-IDF vs LLM：**benchmark 排名和用户评分会对不上**

这是本次调研里**对本项目最有用的一条实证**。

[arXiv 2504.21667](https://arxiv.org/pdf/2504.21667)（*User-Centred Evaluation of Keyword
Extraction Algorithms*，855 名参与者、四组问卷实验，对比 TF-IDF / KeyBERT / Llama 2）：

> "The findings demonstrate that **KeyBERT achieves an effective balance between user
> preferences and computational efficiency**, compared to the other algorithms. We observe a
> clear overall preference for gold-standard keywords, but there is **a misalignment between
> algorithmic benchmark performance and user ratings**. This reveals a long-overlooked gap
> between traditional precision-focused metrics and user-perceived algorithm efficiency."

> （讨论 benchmark 依赖人工标注时）"This approach makes the evaluation heavily dependent on
> the chosen annotations, **potentially penalising algorithms that capture underlying meaning
> rather than exact wording**, even when the former better reflects human-perceived coherence." **[一手]**

→ **推论**：**别把 A/B 的指标建在「和某个金标准列表的重合度」上**。
本项目的下游是「实时字幕里的人读得顺不顺」，正确的判据是**人读**（也就是
`RESEARCH-product-shape.md` §5.2 那条随机术语对照），不是 F1。

### 3.3 ⚠️ 为什么频率法在「课件」上特别容易翻车

三条互相独立的证据：

1. **slides 的文体是 headline，不是句子。**
   ICCV 2025 的 LecSlides-370K（25,542 节课 / 370,078 张 slide / 15 个学科领域）逐字：
   > "Texts in slides are often **conclusive and headline-style statements**,
   > which complicate the task of providing detailed content."
   > "complex and flexible text relations can hinder the understanding of the
   > internal logic of slides." —— [一手](https://openaccess.thecvf.com/content/ICCV2025/papers/Zhang_Towards_Comprehensive_Lecture_Slides_Understanding_Large-scale_Dataset_and_Effective_Method_ICCV_2025_paper.pdf)

2. **TF-IDF 的 IDF 需要一个「文档集」做分母。**
   一门课只有一组 slides，**分母根本不存在**。综述原文对 TF-IDF 的定位是
   「相对**单个文档**与**更大合集**比较」（*A Review of Keyphrase Extraction*，
   [arXiv 1905.05044](https://arxiv.org/pdf/1905.05044) 讲 TF-IDF 是 unsuperised 的
   common baseline）。→ **推论**：单门课内部算 IDF，等价于「在这门课里少见」，
   那正是领域术语的定义——**循环论证**，选不出任何东西。
   要跨课比较才成立，但这又和「每门课一张表」的隔离冲突。

3. **PDF 里的 slide 是文本抽取的最坏情况。**
   Readwise 官方文档（他们自己写解析器）逐字：
   > "PDF is a fickle file format, so some PDFs highlight better than others:
   > **Digital PDFs. Highlights extract much cleaner from digital PDFs than scanned PDFs
   > with OCR applied.** Single Column PDFs. Text in a single column extracts much more
   > accurately than two or three column PDFs. … **The absolute worst are PDF presentations
   > with text all over the page.**" —— [一手](https://docs.readwise.io/readwise/docs/importing-highlights/pdf)

→ **推论**：**本项目的课件形态正好是「最坏的那一类」**（演示文稿 PDF + 多栏 + 图文混排）。
所以 §5.4 的「静默丢内容」不是假想风险，是默认状态。

### 3.4 多词术语：频率过滤会成批误杀

ACL W09-2902 在候选筛选那一节给了两个非常具体的数字（[PDF](https://aclanthology.org/W09-2902.pdf)）：
> "we apply **different frequency thresholds for simplex words (>= 2) and NPs (>= 1)**.
> Note that **30% of NPs occurred only once** in our data." **[一手]**

→ **推论**：如果套一个「出现 ≥2 次才算」的阈值，
**三成的多词术语（正是 `marginal cost` / `opportunity cost` 这类最该进表的词）
一次就会被筛掉**。单字词和多词短语必须**用两个阈值**。

同一条在翻译侧有对应：EAMT 2023（TransPerfect）在做术语库清洗时，
把「**含超过 5 个空格分隔 token 的条目**」当噪声滤掉
（[一手](https://aclanthology.org/2023.eamt-1.34.pdf)）。→ 术语**别太长**。

### 3.5 噪音长什么样：一条可以直接抄的坑

D-Terminer（LREC 2022，[PDF](http://www.lrec-conf.org/proceedings/lrec2022/workshops/TERM21/pdf/2022.term21-1.7.pdf)）
逐字给了一个案例，**和 PPT 的关系是字面的**：

> "The first is a bad CT: **BUILDING, which is part of an all-caps title and falsely
> identified as a CT.** It occurs 7 times in total (mostly lowercased in general contexts)." **[一手]**

同文的另外两条：
- 「recurring issue was **differentiating terms from Named Entities**」——机构和项目名会被当术语。
- 抽取质量**按类别分化**：Named Entities 83%、Common Terms 80%、**Specific Terms 只有 71%**。
  → **推论**：正好是最想抽的那一类，最难抽。

⚠️ 顺带更正：`RESEARCH-product-shape.md` §5.3 把这条记成「子代理转述、**我未逐篇回源**」。
本次**已回源**，原文在 LREC 2022 TERM21 论文正文，置信度**上调为「高」**（一手）。

### 3.6 上限：同类产品**主动给自己设了条数上限**

| 产品 | 上限 | 来源 |
|---|---|---|
| **Granola** | Personal jargon **30 条**；workspace **50 条** | [一手](https://docs.granola.ai/help-center/customising-granola/customising-transcription) |
| **Otter** | 免费 5 条；Pro **100 名字 + 100 其他**；Business 团队 800+800 | [一手](https://help.otter.ai/hc/en-us/articles/360048571373-Manage-vocabulary) |

⚠️ 两家都是**商业产品**，上限很可能同时是产品分层策略。**别把它当科学结论。**
但对照本项目实测规模（`ECON10730=40` / `ECON10740=36` / `ECON10770=35` /
`ECON10790=28` / `SOC10020=45`，见 `RESEARCH-product-shape.md` §5.1），
**28–45 这个量级正好落在 Granola 的 30 和 Otter 的 100 之间** →
**推论：本项目现有术语表规模是健康的，抽词阶段要守的上限大概是「不超过现在的 2 倍」**。

### 3.7 Granola 的「What won't work」——一份现成的限制清单

原文逐字（[一手](https://docs.granola.ai/help-center/customising-granola/customising-transcription)）：

> **What won't work**
> - **Word replacement** - jargon boosts recognition of terms when they are spoken,
>   but they can't replace one word with another in the transcript, and **a similar
>   sounding word may still not be replaced**.
> - **Blocks of text** - each term is sent to the transcription model separately,
>   so big sentences or paragraphs won't be affected unless they are mentioned
>   word-for-word during transcription.
> - **Acronym definitions** - Granola will transcribe acronyms as they're spoken,
>   but can't expand/define these in the transcript.

⭐ **这三条正好是 `glossary/` 的格式说明**（一行一个英文词、无译文、纯候选列表）。
→ **推论**：本项目 glossary 的形状**和 Granola 词表的形状是同一个东西**，
所以它的失败模式**大概率同样适用**，尤其是第一条：
**「发音相近」的词不会被救回来**。

⚠️ **但这一条对本项目的「课号」假设是一个反证需要留意**：
`glossary.example.txt` 写「同一院系的课号往往发音极近，不给模型候选，它必然听混」。
Granola 的证词是「相近音**仍然可能**不被替换」——**不是「一定不被替换」**，
两者不冲突，但它提醒「给了候选 ≠ 一定听对」。

Otter 那边的例子更接近「课号」这类问题（[一手新闻稿](https://otter.ai/blog/otter-ai-adds-custom-vocabulary-to-otter-voice-notes-ai-app-for-ios-android-and-web)）：
> "**Jon vs. John, SDGs (sustainable development goals) vs. STDs**, the guardian ad litem
> (legal term) vs. The Guardian (UK newspaper)" **[厂商]**

---

## 4. UX 形态：2026 年在 macOS 上「拖文件进窗口」对不对

### 4.1 Apple HIG 官方口径 **[一手]**

三页都读过了，对本项目直接相关的逐字：

**Drag and drop**（[URL](https://developer.apple.com/design/human-interface-guidelines/drag-and-drop)）：
> "**Offer alternative ways to accomplish drag-and-drop actions.** Sometimes,
> drag-and-drop operations are inconvenient or impossible for people to perform,
> so it's important to provide other ways to do the same things."

> "**Extract only the relevant portion of dropped content if necessary.**"
> （举的例子：拖联系人到邮件收件人栏，只取姓名和邮箱）

**File management**：
> "People have strong associations with the familiar file browsing experience of the Finder
> and most document-based apps. **Use the default file browser unless you have an important
> reason to create a custom one.**"

> "In a macOS open panel, you can **customize the title of the Open button to reflect the task**
> — for example, if your app lets people insert a file's contents into the current document,
> you might change the title to **Insert**."

**Onboarding**：
> "if onboarding is necessary, design a flow that's **fast, fun, and optional**. …
> If you let people skip the tutorial when they first launch your app or game,
> **don't present it again on subsequent launches**, but make sure it's easy for people
> to find if they want to view it later."
> "People want to start using your app or game **immediately after first launching it**."

→ **推论（三条可以直接落到 P3 的设计约束）**：
1. **拖拽区必须配一个「选择文件…」按钮** —— 这是 HIG 的硬要求，不是偏好。
2. **打开面板的按钮文案改成任务动词**（「导入课件」），不要用默认的 `Open`。
3. **向导必须可跳过、且跳过之后不再弹** —— 直接打脸「开课前引导式流程」的诱惑。
   ⚠️ 本条和 P3 的「开课前准备」**不完全冲突**：HIG 反对的是**首次启动的强制教学**，
   不是「用户主动发起的一次操作」。

### 4.2 watched folder 这条路：Zotero 官方给了「不做」的完整理由

Zotero 专门写了一页 [Why doesn't Zotero have a "watch folder" feature?](https://www.zotero.org/support/kb/watch_folder)，逐字：

> "Zotero is designed around a different workflow from these other tools, and it reflects
> a bit of a philosophical difference. **We don't believe you should have to manually
> download files and worry about folder paths.**"
> "we try hard **not to implement features that we believe encourage more tedious workflows**."

官方论坛里维护者的原话（[论坛 · 低置信但口径明确](https://forums.zotero.org/discussion/75846/watch-folder)）：
> "**All you have to do is drag them into Zotero.**"

⭐ 但**第三方插件把这件事做出来了**，而且做法有可抄之处
（[josesiqueira/zotero-watch-folder](https://github.com/josesiqueira/zotero-watch-folder)，44 ★，GPL-3.0）：

| 它的设计 | 为什么值得注意 |
|---|---|
| 「**Safe by default** — out of the box it only ever **adds** things; nothing is moved or deleted until you opt in」 | **和本项目的「只能追加，绝不能覆盖」是同一条原则**，而且是独立出现的 |
| 「A PDF is imported only after **its file size is stable across two consecutive scans**, so in-progress downloads are not picked up」 | 如果将来真做目录监听，**这条必须有**（否则会抽到半个文件） |
| 「mirrored … both ways」默认**关** | 双向同步是危险面，默认关 |

→ **推论**：watched folder **不是不能用，是默认不该开**。
对本项目更重要的是：**它的两个安全设计（只增、等文件稳定）本身比「监听」这个功能更有价值**。

### 4.3 各种形态横评（→ 我的判断，非引文）

| 形态 | 同类先例 | 对本项目的适配 | 结论 |
|---|---|---|---|
| **磨砂面板 + 拖拽区 + 「选择文件…」** | AnkiDecks / StudyFetch / Cramberry / Readwise | 和 P2 的 `panel.py` 接缝天然对齐 | ⭐ **推荐** |
| 菜单栏 item + 文件选择器 | Granola / Otter（都是**设置里**管理词表） | ClassLive 已经是 `.accessory` 无 Dock；加 `NSStatusItem` 是新面 | 待定（P3 之后再说） |
| `cl prep <课号> <文件>` CLI | anki-cards / genanki 生态 | 作者自己会用，**朋友不会** | 可作为**附带**，不作主入口 |
| watched folder | 无人默认做（Zotero 明确拒绝） | 和 `.course` 状态耦合，容易抽错课 | ⚠️ **不推荐** |
| Qt / SwiftUI 向导 | Apple HIG 建议「可选、可跳过」 | 重写原生已在 roadmap §6 判死 | **不推荐** |

⚠️ **一条实例警示**：Readwise 自己的文档就写明
> "**Which PDF reader apps do not work with Readwise?** … MarginNote, **GoodNotes**,
> **Notability**, Adobe Acrobat on iPad and Android tablets"
——因为那些 app 不按 PDF 注释规范写高亮。→ **推论**：
**「文件格式对不对」比「UI 做得好不好」更决定成败**。UI 再顺，
抽不出文本就是 0 分（详见 §5.4）。

### 4.4 一次真实的「改版踩坑」——LingQ 2026

LingQ 在 2026-03 改了导入流程，论坛反馈（[论坛 · 低声誉但直接](https://forum.lingq.com/t/new-import-workflow-what-the/2574619/3)）：

> "I am not satisfied with the newly redesigned import function. For my most frequent use case,
> **everything has become more cumbersome**, and in some instances, **it no longer works at all**.
> … With the new import system, **I can no longer upload both text and audio simultaneously**
> to create a lesson."

→ **推论**：**把「一次操作」拆成「两步操作」是真实的用户伤害**。
P3 的「选课 + 拖课件 + 确认候选」如果要拆，**必须拆在「确认」这一步**
（那是价值，不是摩擦），**不能拆在「给我文件」这一步**。

---

## 5. 双语特有的问题

### 5.1 ⭐ 一节课的难度里，领域术语占多少？——有确切数字

**Dang & Webb 2014**（*The lexical profile of academic spoken English*，
English for Specific Purposes 33:66–76；分析 BASE 语料库 **160 节课 + 39 个研讨课**、
四个学科；[PDF](https://eprints.whiterose.ac.uk/id/eprint/135476/7/Dang%20%20%20Webb%20fourth%20submission.pdf)）：

| 结论 | 数字（逐字） |
|---|---|
| 达到 **95%** 覆盖需要多少词族 | **4,000 词族** + 专有名词 + 边缘词 → 实际 96.05% |
| 达到 **98%** 覆盖需要多少词族 | **8,000 词族** + 专有名词 + 边缘词 → 98.00% |
| 分学科跨度（95%） | **3,000 – 5,000** 词族 |
| 分学科跨度（98%） | **5,000 – 13,000** 词族 |
| **学术词表（AWL）覆盖多少** | **4.41%** 的学术口语词数；分学科 **3.82% – 5.21%** |
| 会 AWL 之后还需要多少 | 3,000（95%）/ 8,000（98%）词族 |

⭐ **这就是本次调研最重要的一个数字**：
**领域/学术术语只占一节课词数的 ~4.4%**。

→ **推论（两面都要说）**：
- **坏消息**：想靠「术语表」把一节课从听不懂救到听得懂，**数学上不可能**——
  96% 的缺口在通用词上，而通用词不可能放进 glossary。
- **好消息**：这 4.4% 是**最稀有、最不可猜、也最可能被 ASR 听错**的部分。
  结合下面 §5.5 的「稀有度是翻译失败的头号预测因子（r = –.97）」，
  **术语表打的是性价比最高的那一小块**。

### 5.2 一节课能「顺带」学会几个词？——约 1 个

Frontiers in Psychology 2023（澳门某高校计算机课，28 节课语料，[一手](https://www.frontiersin.org/journals/psychology/articles/10.3389/fpsyg.2023.1219159/full)）逐字：

> "To reach 98% lexical coverage, **5,000 word families** are needed. Considering frequency,
> range, and teacher explanation, we concluded that **30 new words** could reasonably be
> incidentally acquired after listening to the **28 lectures**."

→ **推论**：**顺带学会 ≈ 1 词/节课**。
**「课前把术语表给你」不是优化，是必需品**——课堂上指望捡词，量级差两个数量级。

⚠️ 同文也提醒：**不同学科的天花板差很多**（该 CS 语料 98% 只要 5,000 词族，
而 Dang & Webb 的跨度是 5,000–13,000）。**别把「4,000 词族就够」当通用结论。**

### 5.3 ⚠️ 但这套阈值本身是被质疑的

如果要把 95%/98% 写进产品文案，**先知道它不稳**：

- **Schmitt et al. 2011**（The Modern Language Journal 95，[PDF](https://www.lextutor.ca/cover/papers/schmitt_etal_2011.pdf)）逐字：
  > "There was **no indication of a vocabulary "threshold,"** where comprehension increased
  > dramatically at a particular percentage of vocabulary knowledge. Results suggest that
  > the 98% estimate is a more reasonable coverage target for readers of academic texts." **[一手]**
- **Hu & Nation (2000) 的复制研究没复现出来**：Waseda 收录的 Kremmel et al. 2023
  （104 名斯里兰卡成人 L2 学习者）逐字："The results of the original study **could not be
  fully replicated**."，且指出原研究「only **66** university students」（[一手](https://waseda.elsevierpure.com/en/publications/unknown-vocabulary-density-and-reading-comprehension-replicating-)）
- **听的阈值比读的低**：Van Zeeland & Schmitt 2013 / Durbahn et al. 2024 建议
  **95% 就够听力/观片**，读要 98%（[一手汇总](https://scholarspace.manoa.hawaii.edu/server/api/core/bitstreams/be187723-ba8b-433c-9472-b3b4ac847b86/content)）

→ **推论**：**「95%/98%」可以当方向，不能当验收线。**
写文档时标成「常被引用但复制研究有分歧」。

### 5.4 ⚠️⚠️ 提取环节最阴的失败：**静默丢内容**

这一节是本报告里**最该让 P3 设计者读的一节**。所有条目都是 issue 级的一手证据。

| 会丢什么 | 证据 | 严重度 |
|---|---|---|
| **PPTX 里的公式** —— 含 equation 的形状**整个不出现在 `slide.shapes`** | python-pptx [issue #947](https://github.com/scanny/python-pptx/issues/947) 逐字：「The entire shape is "nested" in an `mc:AlternateContent` element **which is why it doesn't appear in `slide.shapes`**」**[一手]** | 🔴 静默 |
| **PPTX 的 speaker notes** —— Docling **读不到**，要 python-pptx 单独取 | 第三方 skill 文档逐字：「docling handles text structure and table recognition whilst **python-pptx extracts speaker notes and embedded images that docling cannot access**」[⚠️ 非官方，但与原库 API 事实一致](https://github.com/DavidROliverBA/Daves-Claude-Code-Skills/blob/main/skills/content-processing/pptx-extract.md) | 🟡 半静默 |
| **扫描件 PDF 输出乱码** —— 有坏文本层时**不跑 OCR**，直接抽垃圾 | Docling [issue #3569](https://github.com/docling-project/docling/issues/3569) 逐字：「if the scanned PDF has an embedded (but corrupt) text layer, **Docling may extract that garbage text instead of running OCR**」**[一手]** | 🔴 静默 |
| **PDF 完全抽不出文本** | Docling [issue #2021](https://github.com/docling-project/docling/issues/2021) 逐字：「Docling uses the `pypdfium2` library for text extraction, which **sometimes fails on PDFs that work with poppler-based tools**」**[一手]** | 🔴 静默 |
| **表格错乱**（密集列 / 不等行高 / 合并单元格） | Docling [issue #2756](https://github.com/docling-project/docling/issues/2756)；[issue #2862](https://github.com/docling-project/docling/issues/2862)（重复单元格）；[issue #2081](https://github.com/docling-project/docling/issues/2081) 逐字：「**TableFormer model sometimes dropping or merging cell texts**」**[一手]** | 🟡 |
| **多栏 PDF 文本顺序错乱** | Readwise 官方逐字：「You can still extract highlights from multi-column PDFs: just be prepared for **things to sometimes fall out of order**」**[一手]** | 🟡 |
| **非水平/垂直排版的文字整块选不中** | Zotero 论坛维护者逐字：「If text is not aligned to horizontal or vertical axis, **it can't be selected**」[论坛] | 🟡 |

→ **推论（三条硬要求）**：
1. **抽取不能"尽力而为"** —— 必须能回答「这份 PPT 里有多少个形状我没读到」，
   并在读到 0 个字符时**弹错，而不是生成一张空术语表**。
2. **公式与 speaker notes 要单独走一路**（python-pptx 侧），别指望 Docling 全包。
3. **扫描件/纯图 PDF 应明确拒绝**，或至少显著提示——这正是
   `RESEARCH-product-shape.md` §1 里「课件通常是真实的 PDF/PPTX（不是照片）」
   这个前提**可能不成立的地方**。

### 5.5 术语表反噬翻译：**宁少勿滥**，有三条独立证据

这条已在 `RESEARCH-product-shape.md` §5.2 记过 WMT25 的随机术语对照。本次补充更硬的证据：

**WMT26（2026-09）RWS 系统报告**逐字（[PDF](https://www2.statmt.org/wmt26/pdf/2026.wmt-1.112.pdf)）：
> "We note **a clear degradation in translation quality for the random mode for all systems
> and language pairs**. We hypothesize that **injecting huge amounts of irrelevant terminology
> could be damaging translation quality**." **[一手]**

**WMT25 Findings**标题本身就给了结论（[PDF](https://aclanthology.org/2025.wmt-1.30.pdf)）：
*"Terminology is Useful **Especially for Good MTs**"*；正文：
> "using proper terms is more useful than random terms for **top-performing systems**." **[一手]**

**稀有度是翻译失败的头号预测因子**：Zainaldin et al. 2026（[arXiv 2602.24119](https://arxiv.org/pdf/2602.24119)）逐字：
> "**Terminology rarity, operationalized via corpus frequency, emerged as the dominant
> predictor of failure (r = –.97).**" **[一手]**

**工业界的术语库清洗实践**（EAMT 2023 · TransPerfect）：
- 原始术语库「very noisy and diverse, including **nouns, adjectives, verbs, prepositions,
  numbers, and acronyms**, and ranging in length from single characters to entire sentences」
- 清洗后：**223k 条 → 78k 条**（丢掉约 **65%**）
- 过滤规则包含「**more than five whitespace-separated tokens**」的条目 **[一手]**

**MT Summit 2021 的术语管理演讲**（[PDF](https://aclanthology.org/attachments/2021.mtsummit-up.7.Presentation.pdf)）列了三类常见问题与解法：
- Specificity / **Ambiguity**（举例：`organ` 多义）/ Needless wordiness
- → "**use Inverse Document Frequency based filtering of your glossary!**"
- → "**filter ambiguous terms**"；"**decompose long multiword expressions** when possible" **[一手]**

→ **推论**：
1. **「I(D)F 过滤」在翻译工业界是标准动作**——注意这和 §3.3 说的
   「IDF 在单门课上没用」并不矛盾：**工业界的 IDF 分母是全公司术语库 + 语料**。
   本项目若要抄，分母只能是**跨课程/跨学期累积的语料**（P4 的数据目录正好能提供）。
2. **候选词条数必须封顶**，且**按稀有度排序**取前 N。
3. **多义词（`organ` / `capital` / `bank`）应显式排除或要求人工确认** —— 这类词正好是
   经济学的重灾区。

---

## 6. 我没能证实的 / 建议不要再捡起来的东西

诚实清单，**这几条一条都别进决策**：

1. **❓ 「AI 生成的 Anki 卡质量差」缺乏可引用的一手证据。**
   找到的最接近的是 HN 的 laurieg 和 Reddit 的 Anki 倦怠帖（[论坛]），
   以及一堆 **AI 卡片生成器自己的博客**在说「AI 抽的卡不一定好」——
   后者是**卖方在自我定位**，不能当独立证据。
2. **❓ Otter / Granola 自定义词表的实际增益**：两边都只有厂商口播
   （「you will see a big leap in Otter's accuracy」），**没有第三方评测**。
3. **❓ LlamATE（Terminology 期刊 2025）只拿到摘要级**。
   摘要说 LLM + 少量 in-domain 示例「demonstrate the potential of implicit in-domain learning」，
   **没有任何数字**。所以本文 §3.2 只用了 arXiv 2504.21667 的用户研究，**没有用它**。
4. **❓ 没有找到任何公开的「导入课件 → 术语表 → 实时字幕」的完整先例。**
   最接近的是 `lecture-to-anki`（缺实时），和 Otter/Granola（缺导入）。
   **本项目这条回路是组合创新，没有现成风险清单可抄。**
5. **❓ 一条我应该踩过但绕开了的坑**：`RESEARCH-product-shape.md` §5.3 明确记过，
   子代理曾给出一篇不存在的「ACL 2025 关键词提取 P/R/F1 表」。
   本次我**没有**再检索到任何「LLM 抽术语 F1」的可信对比表。
   **这类数字目前在本领域就是不公开的**，别指望找到。
6. **❓ 「5 个词的免费上限」之类的数字**（Otter Basic = 5）很可能是产品分层，
   不是研究结论。

---

## 7. 对本项目的取舍建议

> 每条一行。**采纳 / 不采纳 / 待定** + 理由。理由里的编号指本文对应小节。

1. **拖拽区旁边必须有「选择文件…」按钮，打开面板的按钮文案改成「导入课件」** ——
   **采纳**。Apple HIG 明文要求提供拖拽的替代方式，且允许按任务改写按钮标题（§4.1）；
   顺带避免「朋友不知道能拖」这个静默失败。
2. **不做 watched folder** —— **采纳（=不做）**。Zotero 官方为此专门写了一页拒绝理由（§4.2）；
   本项目还要额外承担「哪个 `.course` 在生效」的耦合，收益为零。将来真要做，
   必须抄两条安全设计：**只增不删 + 等文件大小连续两次稳定**（§4.2）。
3. **抽词结果一律「先给你勾选，再追加写盘」，绝不自动落盘** ——
   **采纳**。这是 `lecture-to-anki` 用多轮真实反馈换来的硬规矩（§2.2），
   且和本项目已有的「只能追加、绝不覆盖」是同一条原则。
4. **候选词设上限（建议 60–80），并按「稀有度」而不是「出现次数」排序** ——
   **采纳**。Granola 30 / Otter 100 是同类产品的自我克制（§3.6），
   翻译侧有 WMT26「大量无关术语会损害质量」的实证（§5.5），
   工业术语库清洗要丢掉 65%（§5.5）。
5. **单字词与多词短语用两个不同的出现次数阈值，多词术语降到 ≥1** ——
   **采纳**。「30% 的名词短语在语料里只出现一次」，单一阈值会把它们成批误杀（§3.4）。
6. **显式过滤全大写标题词与纯机构/项目名** —— **采纳**。
   D-Terminer 的 `BUILDING` 案例是逐字的 PPT 场景（§3.5），
   而且 ATE 里最容易抽准的是 Named Entities、最难的是 Specific Terms（§3.5）。
7. **多义词（`organ` / `capital` / `bank`）不自动入表，留给人工确认或直接排除** ——
   **采纳**。MT Summit 2021 把「Ambiguity」列为术语管理三大问题之一（§5.5）。
8. **课件抽取必须"可审计"：能报出「读到 N 个形状、跳过 M 个」；读到 0 字符时弹错** ——
   **采纳**。PPTX 公式形状**整个不出现在 `slide.shapes`**、
   Docling 遇到坏文本层**不跑 OCR 直接抽垃圾**——两件都是静默失败（§5.4）。
9. **扫描件 / 纯图 PDF 明确拒绝并提示，不做 OCR** —— **采纳**。
   Readwise 官方说扫描件抽取质量明显更差、演示文稿 PDF 是「最坏情况」（§3.3）；
   OCR 是另一个依赖面，和 roadmap §6 的「不加新依赖」一致。
10. **不照搬 `lecture-to-anki` 的「一门课一个 deck、取消课程层级」** —— **不采纳**。
    它的理由是 Anki 的复习调度，本项目的**注入单位就是课**（`glossary/<课号>.txt`），
    合并会破坏隔离（§2.2 表）。
11. **`ECON10101` / `Brightspace` 等课号教务词继续手写、只允许追加** —— **采纳**。
    这条已有，本次只是补了证据：ATE 学界承认「词 vs 命名实体」是**标注口径**不是精度问题，
    所以课件抽取器**在原理上就抽不到这一类的正确形态**（§3.1）。
12. **P6 的 A/B 必须带随机术语对照，且判据用「人读」而不是「和金标准列表的重合度」** ——
    **采纳**。WMT25/26 双证（§5.5），且 2504.21667 证明
    **benchmark 排名和用户评分会对不上**（§3.2）。
13. **不要用「术语表让覆盖率从 96% 涨到 →98%」这类话术** —— **不采纳（=不该说）**。
    术语只占一节课 ~4.4% 的词数（§5.1），而 95%/98% 阈值本身**复制研究没复现**（§5.3）。
    正确的说法是「**把最稀有、最不可猜的那一小块先垫上**」（§5.1 + §5.5）。
14. **把「课前术语表」定位成必需品而不是优化项** —— **采纳**。
    课堂顺带学会的量级是 **≈1 词/节课**（30 词 / 28 节课，§5.2），
    指望上课捡词在数量级上不可能。
