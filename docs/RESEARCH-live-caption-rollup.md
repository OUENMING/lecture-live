# 实时字幕「2 行 roll-up」调研台账

> 2026-09-27 · 四路并行调研（规范一手 / 日文 / 中文 / 现代产品），**所有承重引用由我本人回一手核过**
> —— 子代理给的引用**这一轮抓出 2 处错**（一处归属、一处是我自己上轮写错的）。核法：我下载 PDF 自己抽文本，或直接读官方页面原文。
>
> **口径**（沿用 `docs/RESEARCH-macos-aesthetic.md`，别混）：
> `[一手]` = 我自己读过原始文件/官方页面 · `[论文]` = 读过原始论文/元分析 ·
> `[实测]` = 本项目自己量出来的 · `[惯例]` = 行业做法但**无标准背书** ·
> `[无明文]` = 明确查过、**就是没有** · `[未核]` = 有来源但我没能亲自读到

---

## 0. 一句话

**「2 行 roll-up」有成文标准背书，且三条互不通气的来源在「已显示过的字不许回改」上收敛。**
真正需要我们自己定的只有一件事：**切分规则**（因为我们的 ASR 输出只有 24.3% 带内部标点）。

---

## 1. ⭐ 两条纠正

### 1.1 纠正我自己的错（`PLAN-panel-ux.md §15` 原文）

我上轮写的是「三模式定义见 47 CFR §79.103 与 ANSI/CEA-608-E」。**§79.103 是错的。**

拉 eCFR 原文确认，该节标题是 `§ 79.103 Closed caption decoder and display requirements for apparatus.`
—— 讲的是**解码器硬件义务**（屏幕尺寸、豁免、"achievable"），**通篇没有三种模式的字**。

### 1.2 纠正子代理的归属错误

产品一路的代理拿到一份 `bruguier.com/pub/deflickering.pdf`，被上游告知是「Apple 的去闪烁论文」。
**我下载 PDF 读了首页：是 Google LLC。**（Bruguier, Qiu, Strohman, He，
版权行 `978-1-6654-7189-3/22 © 2023 IEEE`。）论文本身是真的，归属是错的。

---

## 2. Roll-up 的成文机械（`[一手]`）

**47 CFR §15.119**（我下载 2009 版 PDF 逐字读的）。逐字：

> `(1) Roll-up. Roll-up style captioning is initiated by receipt of one of three Miscellaneous Control
> Codes that determine the maximum number of rows displayed simultaneously, either 2, 3 or 4 contiguous
> rows. These are the three Roll-Up Caption commands.`
>
> `(i) The bottom row of the display is known as the "base row". The cursor always remains on the base
> row. Rows of text roll upwards into the contiguous rows immediately above the base row to create a
> "window" 2 to 4 rows high.`
>
> `(iii) Each time a Carriage Return is received, the text in the top row of the window is **erased from
> memory and from the display** or scrolled off the top of the window. The remaining rows of text are each
> rolled up into the next highest row in the window, leaving the base row blank and ready to accept new
> text. This roll-up **must appear smooth to the user, and must take no more than 0.433 second to complete**.`
>
> `(v) Characters are always displayed immediately when received by the receiver.`

**四条可直接落进实现的事实：**

| 事实 | 对我们的含义 |
|---|---|
| **RU2 / RU3 / RU4** —— 2 行是最小档 | 2 行不是"妥协"，是标准里的**下限配置** |
| **base row 固定在底部，只有它能接收新字** | 一行一旦上滚，它就是**静态的** —— 不许回改 |
| **顶出的行「从内存和显示中抹掉」** | **没有滚动缓冲**。顶出即丢，这是设计而非缺陷 |
| **0.433 秒**且「须对用户显得平滑」 | 法典给的**动画预算上限** |

⚠️ **时效性**：§15.119 的 roll-up 正文**完整只存在于 2009 版**。我拉了 **2025 版 PDF，
grep 不到任何 roll-up 字句** —— 该节已非现行条款（随模拟接收机规则一起废）。
接替标准是 **ANSI/CTA-608-E S-2019**，**付费墙，我没读到** → 标 `[未核]`。

### 2.1 监管文本自己说实时字幕是几行（`[一手]`）

**FCC 14-12 ¶39**（2014-02-20 通过，我下载 PDF 逐字读到）：

> `Most live programs utilize "roll-up" captions, which roll onto and off the screen in a continuous
> motion. Usually **two to three lines** of text appear at one time, and as a new bottom line appears,
> it pushes up the previously appearing lines until they roll off the screen.`

→ **实时字幕的监管口径是 2–3 行。我们选 2 = 该区间下限，也是最省高度的一档。**

### 2.2 中国标准（`[一手]`，我下载 PDF 抽文本）

| 标准 | 逐字 | 日期 |
|---|---|---|
| **GY/T 359—2022** 9.4 b) | `行数：台词字幕每屏一行，最多不超过两行。` | 发布 2022-07-21 / 实施 2022-10-21 |
| 同上 c) | `字符数：英文字符数不超过 50 个，其他外文字幕可参考英文字幕宽度。` | |
| 同上 d) | `完整性：每屏、每行字幕的分布应兼顾画面与句型、单词和语义的完整性。` | ← **国标级的「按语义换行」依据** |
| **GY/T 270—2013** 11.4.9 | `CR把当前输入点移动到下一行的起点处。如果下一行在可视窗口的下方，则窗口向上滚动。` | 发布/实施 2013-08-14 |
| 同上 附录 A.6.4 | `字幕解码器应该支持从下到上的滚动方向。` | |
| 同上 11.4.7 | `窗口可最多包含 15 行文本，每行最多包含 32 个字符。` | ⚠️ **解码器容量上限，不是可读性标准** |

⚠️ **直播 vs 录制**：**中国标准里找不到任何区分**。四路搜索全空。可推断的只有：
GY/T 270 把「实时」当**模式**（roll-up + CR 上滚），而**不是放宽限值**。

⚠️ **一处张力，记下来别忽略**：GY/T 359 给英文的上限是 **50 字符/行**，
而我们的面板在 11pt 下**每行装约 115 字符**（实测）—— **2.3 倍**。
但那条约束的是**电视播出字幕**（观看距离、安全框），我们的是**人手前 50cm 的屏幕浮层**。
→ 这是**场景不同**，不是我们违规；但它说明**我们的行长处在"远比广播字幕长"的区间**，
所以「宁可加行数、不要缩字号」这个方向是对的（见 §5 工藤 2005）。

### 2.3 Netflix 繁中（`[一手]`，官方页 2025-12-19）

- `I.1 16 characters per line` · `I.10 Maximum two lines. Text should usually be kept to one line,
  unless it exceeds the character limitation.` · `I.14 Adult programs: Up to 9 characters per second`
- SDH 轨**明确放宽**：`II.2 The character limit can be increased to 18 characters per line` ·
  `II.3 Adult programs: Up to 11 characters per second` · `II.4 Keep to 2 lines for dialogue,
  a 3rd line may be used for descriptors`
- ⭐ 同规范英文是 42 字符/行、20 cps → **中文 9 cps vs 拉丁 20 cps，CPS 不可跨语言换算**

---

## 3. ⭐⭐ 三条互不通气的来源在同一个规则上收敛

**规则：已显示过的文本不许回改。**

| 来源 | 原话 |
|---|---|
| **CEA-608 / CFR §15.119**（`[一手]`） | base row 之外的行是静态的；只有 base row 接受新字 |
| **Apple WWDC25 session 277**（`[一手]`，我读的页面 transcript） | `Note how the timecodes show that later, improved results replace earlier results. This only happens when you enable volatile results. Normally, the transcriber only delivers finalized results, and **none of them replace earlier results.**` |
| **Google Research 2023**（`[一手]`） | `our preliminary studies showed that **users preferred stability over accuracy for previously displayed captions**` |

⭐ **这条是我们的核心实现规则**，三条独立来源同向 —— 而且是**免费**的：只要让"上滚即冻结"，
就同时拿到了 CEA-608 的模型、Apple 的模型、和 Google 测出的 flicker 收益。

### 3.1 Google 2023 测出的代价（`[一手]`，N=123，Spearman，p<0.001）

| 指标 | 与 flicker 的相关 |
|---|---|
| Fatigue | **+0.36** |
| Distraction | **+0.33** |
| Impaired experience | +0.31 |
| Comfort | −0.29 |
| Easy to follow | −0.29 |
| Easy to read | **−0.31** |

原文对 flicker 的定义：`a "flicker" where previously displayed text is updated… which can impair
users' reading experience due to distraction, fatigue, and difficulty following the conversation.`
设计动作：`smooth scrolling and fading of newly added tokens`。

### 3.2 Apple 自己的 volatile / finalized 模型（`[一手]`）

> `You can show a rough result immediately and then show better iterations of that result over the
> next few seconds. We call the immediate rough results "volatile results".`
> `volatile results are realtime guesses, and finalized results are the best guesses. Here, both of
> those are used, with the **volatile results in a lighter opacity**, replaced by the finalized results
> when they come in.`

样例码：`volatileTranscript.foregroundColor = .purple.opacity(0.4)`

⭐ **和我们现在的做法天然一致** —— 我们的草稿本来就是低不透明度
（`_draft_lbl` alpha 0.50 / `_draft_zh` alpha 0.78），定稿行是全不透明。
**这一条不用改，是本轮少有的"已经对了"。**

### 3.3 同构先例：テレ朝「AIポン」（`[一手]`，NHK技研 R&D No.182 §3.1）

> `修正オペレーターを必要とするこれまでのリアルタイム字幕制作手法では，表示までの遅延時間が課題で
> あったが，このシステムでは，**約１秒の遅延時間で画面表示を可能にしている。これは，音声認識の
> 途中のデータを仮確定させて表示することにより実現している。その後の補正処理で，一度画面に表示
> された後に変更される場合もあるが**，表示されるまでの時間を極力短くすることが可能となっている。`

→ **同一个问题、同一个解法、约 1 秒延迟、明确接受"显示后可能被修正"。**
对照：NHK 自己的リスピーク方式是 **5～10 秒**延迟。（分散式人力听写 → 晚 5–10 秒；
假确定 → 早 1 秒但会改。我们选了后者，且我们的实测节流是 0.5–1 秒。）

⭐ 同一篇里还有两条**可直接用的技巧**：
- `音声認識が難しい箇所を自動で推定し，認識結果の代わりに「。。。」を表示` ——
  **低置信处宁可显示「不知道」，也不要显示一个错的猜测。** 我们手上就有 `asr.last_logprob`。
- `認識結果の単語が人名であると判断された場合には人名をカタカナで表記` ——
  用**规避性表记**避开高风险错字（我们的术语表是同一思路的另一形态）。

---

## 4. 我们的实测（`[实测]`，79 份真实 sessions / 4912 条已定稿句）

### 4.1 ⭐ 先决发现：草稿**本来就是滑动窗口**，不是越写越长的前缀

`main.py:953` → `asr.transcribe(buf[-PARTIAL_MAX_S * SR:])`，`PARTIAL_MAX_S = 10`。
**每帧草稿 = 把最近 10 秒音频重转一遍，整串替换上一帧。**

→ **roll-up 不是要新加的机制，它已经是草稿的天然语义。** 这比方案原先假设的简单得多。

### 4.2 现在这行裁掉多少

| 测什么 | 结果 |
|---|---|
| 10 秒窗口 · EN 11pt / 628px | 1 行 **53.9%** · 2 行 **40.2%** · 3 行 **5.8%** |
| 10 秒窗口 · ZH 12.5pt | 1 行 69.3% · 2 行 29.5% · 3 行 1.2% |
| 窗口字符数 | 中位 **111** · p90 214 · p99 312 · 最大 431 |
| 11pt 一行容量 | 约 **115 字符 / 20 词** |
| 2 行仍装不下的 | 5.8% 的帧，超出中位 **24 字符**、最大 **201** |
| **已定稿单句** EN | 1 行 **92.5%** · 2 行 7.5% · 3 行 0% |

两个推论：
1. **中位 111 vs 容量 115** —— 正好卡在边界，所以才会一半裁一半不裁。
2. **已定稿行不用动** —— 单句 92.5% 一行够（定稿被强制在 ≤25 词就切）。
   **修复范围只在草稿**，这大幅缩小了改动面。

### 4.3 切分锚点够不够

| 测什么 | 结果 |
|---|---|
| ASR 行**句尾**带标点 | **79.3%** |
| ASR 行**句中**有 `, ; :` | **24.3%** ⚠️ |

⚠️ **这是全篇最影响实现的一条**：如果切分规则只认标点，**75.7% 的帧没有锚点可用**。
→ 切分必须是**降级链**，不能只等标点。

### 4.4 高度代价

| | 1 行实测 | 2 行需 | 现在给 |
|---|---|---|---|
| EN 11pt | 14.00px | **28.00px** | 18.0（4px 余量） |
| ZH 12.5pt | 15.00px | **30.00px** | 20.0（5px 余量） |

→ 最少 **+20px**（`HEIGHT` 362→382）；按现有余量翻倍则 **+38px**（362→400）。
常量链：`HEIGHT = BASE_H + 3*ROW_H`，`BASE_H = BOTTOM_PAD + PINNED_H + 8 + HEADER_H`。

### 4.5 顺带抓到一个既存缺陷

`_draft_lbl` / `_draft_zh` 构造时是 `maxLines=1` + **默认 `WordWrapping`** ——
正是 `overlay.py` 文件头 40–48 行自己警告的「**静默吞行、不给省略号**」那套配置。
`_layout` 里只给 `_gloss_lbl` 换过 `TruncatingTail`，**两个草稿标签从头到尾没人换过**。
→ 现在的裁是**无声的**（用户看不到省略号，无从知道被裁）。

---

## 5. 行数：吸不吸注意力，文献**没有共识**（诚实记录）

| 研究 | n | 结论 |
|---|---|---|
| **Li 2026**（Applied Cognitive Psychology, 2026-07）`[一手]` | 211 收 155（**母语中文**） | 两行**吸更多**注意：总注视 **733.3 vs 357.3 ms**、重看 ×1.23、跳过率 **11.5% vs 34.8%**、首次注视 **465.6 vs 351.1 ms**；⭐**控制字数后两行仍显著更久**（p=0.002） |
| **Zahedi & Khoshsaligheh 2021**（Translation and Cognition）`[一手]` | 32（伊朗） | ⚠️ **反向**：等长度下**一行**吸更多注意；且受试**偏好短句与两行** |
| **Alves Pinto de Assis et al. 2026**（ETRA, 2026-05-28）`[一手]` | 聋 20 + 健听 20 | **速度×行数有显著交互**：180 wpm 时行数无影响；**145 wpm 时两行显著缩短跳视延迟** |
| **工藤・成田 2005**（映像情報メディア学会誌 59(11)）`[一手]` | — | 2 行上限；**30 字/行 评点 1.9/5「小すぎて適当ではない」**；20 vs 25 字/行 **偏好分裂** |
| **中野ほか 2008**（ヒューマンインタフェース学会誌 10(4)）`[一手]` | 8+8 | 高偏好 = **每行约 25 字含 ≥1 读点，且总在句读之后立刻换行**；⚠️ `many of hearing-impaired subjects were **non-follower type**，即不按提示顺序读字幕`；n 很小 |

### 我们落在哪一档（派生）

草稿窗口中位 **111 字符 / 10 秒 = 11.1 cps**。文献把 **12 cps 叫「慢」**（Szarkowska & Gerber-Morón 2025）。
→ 我们落在 **145 wpm 那一档，正是 2026 那篇说「两行缩短延迟」的区间**。
互补数据（同篇 `[一手]`）：跳视延迟 12 cps → **716ms**、20 cps → 397ms；
**有英语音频时 579ms** —— 正是我们的条件。

**结论**：现有证据**不支持**"两行更差"。最差是中性，且在我们所处的速度档上正向。
⚠️ 但**「两行吸注意力」的方向在文献里没有共识**（Li vs Zahedi 直接相反），
所以**不能**引用"两行更好"当定论 —— 只能说"尚无证据反对"。

---

## 6. 现代产品做法

| 产品 | 行数 / 行为 | 出处 |
|---|---|---|
| **Apple Live Captions**（macOS） | ⚠️ **一手资料里没有行数**。官方支持页只有 字号/字体/颜色/背景色 + 可拖边缩放 + 恢复默认位置，**无不透明度项、无行数项** | `[一手]` support.apple.com（今日取） |
| 同上，渲染 | ⚠️ 只有二手 teardown：约**每秒更新一次**；比早期版本「**much less prone to 'back up and rewrite' large chunks**」—— 反证它**确实显示未定稿并确实回改过** | `[二手]` |
| Apple 的**模型**（不是 UI） | volatile / finalized 两态；volatile 低不透明度；**finalized 永不回改** | `[一手]` WWDC25 §3.2 |
| **Zoom** | ⭐ Zoom 员工原话（2022-12-21）：`we only support **two lines** of closed captions now. Unfortunately, we don't have a setting to change this behavior.` | `[一手]` devforum |
| **Teams** | 官方帮助：可 `increase the number of lines displayed`（2023-05 起）→ **行数是用户可调项**，说明"行数不够"是真实痛点 | `[一手]` support.microsoft.com |
| **Google（论文层）** | `reranking the partial results in favor of a more stable prefix` → **flicker 减半**，且对最终结果无影响 | `[一手]` Bruguier et al., IEEE 2023 |

⚠️ **Apple 的 macOS 侧行为 `[无明文]`**：一手资料只说"可缩放窗口"，行数与滚动机制**没有任何第一方说明**。
不要拿二手 teardown 的"a few lines"当规格。

⭐ **Google 那条 stable-prefix reranking 值得单独记**：它是在 **ASR 侧**（beam 候选里挑前缀更稳的），
**零 UI 成本**，能把 flicker 减半。列为后续候选，**本批不做**。

---

## 7. 明确没找到的（诚实清单）

| 想找 | 结论 |
|---|---|
| 中国标准区分**直播/录制**字幕行数 | **四路全空**。「找不到」≠「不存在」，但确实是空的 |
| 日文公文书**明文放宽**实时字幕行数/字数 | **没找到**。差是以 **①容许摘要省略 ②表记规则适用外 ③容许延迟** 的形式出现的，不是以放宽行数出现 |
| Apple **第一方**的行数 / roll-up 机制 / 字体度量 | **没有**。只有"窗口可缩放" |
| 中文产品**公开区分未定稿/已定稿** | **一个都没有**。中文实时字幕普遍把 interim 当正式文本直接滚 |
| 中文社区「一行不够」「截断」「滚动分心」的**用户原话** | **没找到**。知乎命中的是教程不是抱怨 |
| ANSI/CTA-608-E **S-2019 正文** | **付费墙**，未读到 → `[未核]` |
| 「取消后已完成部分怎么办」 | 这轮未查（不属本议题） |
| Bruguier 2023 的**会议名** | PDF 首页无会议名，只有 IEEE 版权行 → 报为 ICASSP 2023 但 `[未核]` |

---

## 8. 结论与实现规格

### 8.1 决定（作者已定「改成 2 行」，本轮补上细节）

**2 行 roll-up，尾锚定，只有底行可变。**

| # | 规格 | 依据 |
|---|---|---|
| 1 | **2 行**（不是 3、不是 1） | CFR §15.119 RU2 是标准下限；FCC 14-12「usually two to three lines」；GY/T 359「最多不超过两行」；Zoom 官方「two lines」；工藤 2005 2 行上限；**实测 2 行覆盖 94.2% 的帧** |
| 2 | **只有底行可被改写**；一行上滚即**冻结** | 三条来源收敛（§3）；且**免费** |
| 3 | **顶出的行直接丢弃**，不做滚动缓冲 | CFR §15.119 `(iii)` 逐字「erased from memory and from the display」 |
| 4 | **上滚动画 ≤0.433 秒**，须平滑（缓动，不瞬跳） | CFR §15.119 `(iii)` 逐字 |
| 5 | **新字立即显示**，不攒到句尾 | CFR §15.119 `(v)` `Characters are always displayed immediately`；Apple「show a rough result immediately」 |
| 6 | **切分走降级链**：① 找目标断点附近的 `, ; :` ② 无则退词边界、优先断在虚词之后 ③ 仍不行则不把单个虚词留在上行 | 中野 2008（句读后立即换行）+ GY/T 359 9.4 d)（语义完整性）+ Netflix（`avoid having just one or two words on the top line`）；⚠️ **锚点只有 24.3%（实测）** |
| 7 | **不缩字号** | 工藤 2005：30 字/行 评点 1.9/5「小さ過ぎて適当ではない」 |
| 8 | **未定稿的视觉区分保持现状**（alpha 0.50 / 0.78，定稿全不透明） | Apple WWDC25 的 volatile-低不透明度模型；⚠️ **这条不用改** |
| 10 | ⭐ **贴底对齐**（不是贴顶）：一行草稿占盒子的**下面**那一行，上面那行留空 | CFR §15.119「base row」在底部、新字从底行进、旧行被顶上去 → 贴底才会让「1 行→2 行」是**向上**走的。实测两种都渲染过 PNG，见 §8.5 |
| 11 | **本批不做** Google 的 stable-prefix reranking | 属 ASR 侧改动，另开一批（但记着它能把 flicker 减半） |

### 8.2 已实现（`overlay.py`，2026-09-27）

| 位置 | 改动 |
|---|---|
| `DRAFT_H` / `DRAFT_ZH_H` | `18.0/20.0` → **`32.0/35.0`**（实测两行 28.00/30.00 + 沿用单行时代那档余量 4/5）→ `PINNED_H` 98→127 · `BASE_H` 152→181 · `HEIGHT` 362→**391**（+29px；⚠️ 这三个是**派生值**，**代码才是唯一定义点**，这里只是当次改动的留档） |
| `rollup_lines`（新，**纯函数**） | 折行 + 断点选择。高度怎么量由调用方注入 → 测试里换假尺子就能不开 AppKit 验 |
| `_render_draft` / `_roll_into`（新） | 两槽各按 2 行写；贴底靠"文字框高按实际行数收缩、底边不动" |
| 草稿两标签 | `maxLines` 1 → `ROLL_LINES`；**换行模式不变（`WordWrapping`）** —— 见下 |

⚠️ **一个必须记住的实现事实**：`WordWrapping` + `maximumNumberOfLines_(2)` + 串里插 `\n`
**确实会画成两行**（PNG 目视确认过）。而 `TruncatingTail` **会强制单行**（`overlay.py` 文件头 40-48 行
那段实测）—— 所以这两个标签**不能**为了拿省略号而改成 Tail，改了就没有第二行了。

### 8.3 ⚠️ 已知残留（写清楚，别装作没有）

- **窄窗会滚掉更多**，而且丢的是**头**。按**作者真实面板宽 455**（`.window` 里存的）实测：

  | 面板宽 | 2 行留住（中位草稿 / p90 草稿） | 旧 1 行留住（中位 / p90） |
  |---|---|---|
  | **455（作者的实际值）** | **99% / 68%** | 76% / **35%** |
  | 660（`WIDTH` 默认值） | 100% / 100% | 100% / 52% |

  → 改动在每个宽度上都是**净改善**，且把"留住最新"变成可能（旧行为留的是**头**、静默把尾裁掉）。
  ⚠️ **丢头是安全的**：草稿窗口滑走的那截，随即会以**定稿**的身份出现在上方转录区。
- **5.8% 的帧 2 行仍不够**（超出中位 24 字符）。同样靠"丢头 + 上方定稿接住"兜。
- 上面两条里"旧行会被顶出去"是**从常量推导**（窗口 `PARTIAL_MAX_S=10`、节流 `latest.take(0.5)`），
  **不是跑出来的** —— 真机上课时须确认"读不到"没有真的发生。
- **切分规则的 ②③ 档（无标点时退词边界）没有实证背书**，只有间接支持，标 `[惯例]`。

### 8.4 闸门与验证

- `tests/test_audit_regressions.py` 新增 **R10**（6 条，进**默认闸门**）：一行够却给两行 / 丢字从中间挖 /
  **产出第 3 行** / 不认从句边界 / 滞回是 no-op / 容差写死。
- **6 条变异全部变红，且红的正是对应那一条**（M1 尾锚定 / M2 从句优先 / M3 滞回 / M4 `lo_ok` 下界 /
  M5 提前返回 / M6 容差写死）。

### 8.5 Altitude 审查的产出（2026-09-27，作者已批准的质检轮）

**已修 —— 我自己的语义错误**：`_roll_into` 原来把 `box_h / ROLL_LINES`（盒子的**每行配额**，含余量）
当 `line_h` 传给 `rollup_lines`。那是**两件事**：

- **行高**由字体决定（实测 11pt = 14.00px / 12.5pt = 15.00px）
- **盒子高** = `ROLL_LINES × 行高 + 余量`

现在真量行高（`_measure_text_h("Hg", w, font)`），贴底收缩仍用配额。
⚠️ 现在**恰好**怎么取都对（14.00 与 16.0 之间没有任何字符串会落进去）→ 这是**未来坑**，不是现存 bug。
顺手把容差改成按行高缩放（`eps = max(0.5, 0.1 × line_h)`）：判据是"高度 ≤ n × 行高"，而实测高度
**当前**总是行高的整数倍 —— 那是巧合不是契约。写死 0.5px 时一个 28.6 的两行高度会被判成"放不下"。

⚠️ **写这条判据时踩了一次自己的坑**：那个症状**不是**"退回一行"，而是**多丢一个头**（后缀窗口缩到
更短仍能凑出两行）。我第一版按**行数**断言 → **变异红不了**；改成断言"**留住了多少字**"才有区分能力。

**判定「真、但不在本次范围」的三条**（都记下来，别当没看见）：

| 发现 | 为什么不现在做 |
|---|---|
| 一个 frame 有**两个写者**（`_layout` 与 `_roll_into`），靠 `_draft_box_y_*` 传状态。更深的形状是 `DraftSlot` 自己拥有 (label, box_y, box_h, base_len) | ~40 行重构；R10 钉的是纯函数所以不受影响 —— 但**值得下一批做** |
| 滞回键用的是**长度**（`prev_base_len`），而 ASR 每帧重解码整个窗口 → 字符串**非单调**变，来自另一个串的长度可能锁住一个无对应的断点 | 影响**有界**（±8 字符，且定稿时重置）。真出现症状再改成锚在底行**首个词**上 |
| 三处的"行高策略"各写各的（`LINE_TIERS` / `ANSWER_TEXT_H` / 草稿的配额）。值得共享的是**量高度的 fitter**，不是渲染器 | 另一批 |

**⭐ 一条同类潜伏缺陷（同源，不在本次范围）**：
`transcript_view.py:255` 的 `maxLines` 是从一个**闭样本**（4797 句）推出来的，**且不给省略号** →
遇到比样本更长的句子**照样静默吞掉**。与我这次修的是**同一类**。
建议下一批加一个 `_set_lines(lbl, n)`（`n == 1` ⇒ `TruncatingTail`）统一守卫，
`_label` / gloss / `transcript_view` 都走它 —— **比断言强**（`-O` 下断言会被去掉）。

**判定「该保持分开」的一条**：定稿区与草稿区**不合并** —— 定稿必须一句不吞，草稿按规范就该丢头。
策略不同，只有**量高度的 fitter** 值得共享。

### 8.6 Efficiency 审查的产出（同一轮，数字全是实测）

**先看基线**：

| 项 | 数 |
|---|---|
| 一次 `rollup_lines` | 文本一行够时 **1** 次 AppKit 测量；否则 **29–31** 次 |
| 一个草稿帧（两槽都活） | **1.6–3.4 ms** |
| 草稿自己的频率 | `vad.py: PARTIAL_INTERVAL_S = 1.0` → ≤1/s → 约 **0.5% 主线程：不算事** |
| ⚠️ **真正的开销窗口** | **答案流式期间**：`answer_delta` 走**非紧急** `_mark_dirty` → `_render` 跑到 ~**60Hz** → 约 **120 ms/s(≈12% 帧预算)** 在反复量**逐字节没变**的草稿 |

**做了三件（都做了「行为不变」的验证）**：

1. ⭐ **记忆化**（`Overlay._draft_cache`，键含 `prev_base_len`）。实测：第一次渲染 86 次测量，
   **之后连渲染 5 次 = 0 次**（全命中缓存）。
2. ⭐ **删掉一次二分**：最长「只占一行」的后缀不必另搜 —— 一行放得下 ⟹ 两行也放得下，
   所以 `one_line = s[lo_ok:]`。**在 41,296 例真实数据上验证 0 处不等价**
   （5162 句真实转录 × 2 字号 × 4 宽度），AppKit 测量次数 **约 30 → 20–24**，
   窄窗扫描结果与改前**逐字相同**。
3. 顺手把 `line_h` 的测量挪进**未命中**分支（原来自文本为空时也照样量）。

⚠️ **一条诚实的空白**：变异 **M7（记忆化键漏掉 `prev_base_len`）红不了** ——
R10 测的是**纯函数**，不经过 `_roll_into` 的跨帧状态，所以没有判据直接钉住那行。
但那条键的必要性**有判据背书**：`test_break_hysteresis_is_not_a_no_op` 已证明
**纯函数的输出确实依赖 `prev_base_len`** → 缓存它就**必须**把它放进键。
（链条是：判据证明依赖存在 → 缓存必须按它分键。不是"我觉得应该"。）

**判定「不必优化」的**（它明说的，记下来免得下次又来一遍）：`lambda` 没有逃逸（不泄漏）、
候选 listcomp 只在 ≤13 字符窗口内跑（0 次测量）、`display.count`、`lbl.font()`、
两次 `setFrame_`（各约 2 µs）—— 全是噪声。

### 8.7 Simplify 审查的产出（同一轮）—— 两条认、两条不认

**认（已改）**

1. ⭐ **`one_line = s[lo_ok:] or text` 那个兜底是错的**（不只是死代码）：它端上**整段**、头锚定，
   AppKit 再把尾裁掉 → 丢的正是**最新的字**，**正好违反本函数的第 1 条不变量**。已删。

   ⚠️ **但子代理判它「死代码」这个结论错了。** 我实测：**有两个单字符自己就能量出两行** ——
   `U+0085`(NEL) 与 `U+2029`(PARA SEP) 在 11pt 下都是 **28.00 = 两行**（试了 3468 个单字符，
   只有这两个，加上 `\x85` 重复出现）。所以那条路**可达**。

   → **真修法是治根**：`rollup_lines` 开头 `text = " ".join(text.split())`，把换行类字符
   **归一掉**（换行归我们的排版管，文本不许自带）。`str.split()` 一次覆盖
   `\t \n \v \f \r \x1c-\x1e \x85` 与 `U+2028`/`U+2029` —— **逐个实测过**。
   归一之后「任何单字符都只占一行」才**可证**，那条路才真的封死，兜底才可以删。

2. **`min(box_h, used * (box_h / ROLL_LINES))` 的 `min` 不可能生效**（`used ≤ ROLL_LINES`），
   而且它会**盖住**「盒子与上限不一致」这种错。删掉。
   ⚠️ 同时 **`ROLL_LINES` 原来并不是行数的唯一定义点** —— `rollup_lines` 里写死着 `2 * line_h`，
   改成 `ROLL_LINES * line_h`。否则 `ROLL_LINES = 3` 会得到「三行的盒子 + 两行的上限」。

**不认（保持原样，理由记下）**

3. 它说 `finalize` / `set_trans_mode` 里那两处 `_draft*_base_len = None` 是**可证的 no-op**
   （`_render` 没有提前返回、每帧都会重算）。**推理成立，但我不删**：删掉之后「状态干净」
   就变成**隐含依赖**「finalize 之后一定有一次 urgent 渲染」。这个仓库被这类**隐式耦合**
   咬过（跨提交回归那一课：每个提交各自绿 ≠ 合起来对）。两行换一个显式不变量，值。
4. 它判定 **不要抽 helper**（我原本倾向抽）—— **接受**：并发改动已经删掉第 4 次搜索，
   剩下两次「最左为真」的基点变量与上限都不同，抽出来省约 6 行却要加一个 def + docstring。
   **在 4 份时值得，现在不值。**

**它判定 keep 的**：`_draft_box_y_*`（钉住 `_layout` 的几何，符合本文件惯例）·
`_roll_into` 的 7 个参数（一个调用点、两次相邻调用、每个实参在 def 处都有名）·
两槽**显式调用**而非循环（槽在标签 / 字体 / 盒常量 / 盒 y / 键 / base_len 属性 / 文本源
**七处**都不同）· 两条 base_len 各自独立。

### 8.8 变异总表（9 条，其中 2 条是**已知 no-op**）

| 变异 | 结果 |
|---|---|
| M1 尾锚定坏掉 | ✅ 红 |
| M2 从句优先反转 | ✅ 红 |
| M3 滞回摘掉 | ✅ 红 |
| M4 `lo_ok` 下界摘掉 | ✅ 红 |
| M5 提前返回摘掉 | ✅ 红 |
| M6 容差写死 0.5px | ✅ 红 |
| **M7** 记忆化键漏 `prev_base_len` | ⚠️ **no-op** —— R10 测纯函数，不经过跨帧状态。必要性由 `test_break_hysteresis_is_not_a_no_op` 背书：它证明**输出确实依赖 `prev_base_len`** → 缓存就**必须**按它分键（链条是"判据证明依赖存在 → 必须分键"，不是"我觉得应该"） |
| M8 撤掉换行归一化 | ✅ 红 |
| **M9** 行数上限写死 `2`（而非 `ROLL_LINES`） | ⚠️ **no-op** —— `ROLL_LINES == 2` 时两者逐字等价。这条改的是**单一真源**，不是行为 |
- ⚠️ 写测试时**抓出两个实现缺陷**（都是"不报错但悄悄错"）：
  ① 只保证"整段塞进两行高度"，没保证**切开后下行自己也放得下** → 会溢出成第 3 行、被静默吃掉；
  ② 二分方向写反 —— 谓词在**短后缀**上为真（空后缀高度 0），当成"真在前"就会返回下标 0 = **整串**。
- ⚠️ 还抓出**一条假判据**：`test_one_line_stays_one_line` 原来只用无空格串，变异 M5 **红不了**；
  补了带空格/逗号的短句才有区分能力。

## 9. 第三轮补料（2026-09-27 晚，产品一路的后半段 + 我逐条回源）

### 9.1 ⭐⭐ W3C 给了本课题**最好的机械定义**（`[一手]`）

W3C Text Tracks CG 的 roll-up 页逐字：

> `Roll-up captions are typically used for live captioned content. There are a limited number of lines of
> text displayed (2, 3 or 4) and every time a new line is added, the previous ones are moved up until
> they disappear from the caption window.`
>
> `rollup is a sequence of text lines that are painted to screen successively and **every new line is added
> in the line position of the currently bottom-most line and pushes all the other lines up one, the top
> most disappearing when it reaches the maximum line count**. **The words within each line may appear
> successively, too.**`

⭐ 最后一句解决了我们这里唯一的形态问题：**行内文字逐步出现是被承认的合法做法** ——
不是"等一句话齐了再整行换上"。而且它明说**新行进的是"最底那一行"的位置**（= 我们的贴底）。
同一页还给了 paint-on 的定义：`successively rendered but once rendered doesn't move until its end`
—— 与我们"上滚即冻结"同源。

### 9.2 ⭐ **Chrome 折叠态就是 2 行**（`[一手]`，我取的是 chromium main 的源码）

`components/live_caption/views/format_constants.h` 逐字：

```
kLineHeightDip = 24
kNumLinesCollapsed = 2
kNumLinesExpanded = 8
kMaxScrollViewHeightCollapsed = 48      // = 2 × 24, 自洽
kMaxWidthDip = 536
```

→ **一个出货的、跨平台的实时字幕产品，默认（折叠）就是 2 行**，展开才 8 行。
同目录 `greedy_text_stabilizer.cc` 里有 `stable_token_count_` 这套**稳定前缀**机制
（我只核到机制存在，没逐行核它的"永不缩短"规则 → 标 `[一手·部分核]`）。
另有社区 issue 原话（Chrome #40268358, 2023-05）：
`it automatically inserts punctuation marks, causing the text to jump, become disorganized, and constantly flicker.`
← 用户对 flicker 的抱怨，与 §3 的规则互为印证。

### 9.3 ⭐⭐ Google 的规则可以**直接落地**（`[一手]`，博客逐字）

先前只引了它那句"stability over accuracy"。补上**真正的实现规则**：

> `Case A tokens: We directly add case A tokens, **and line breaks as needed** to fit the updated captions.`
> `Case B tokens: … users preferred stability over accuracy for previously displayed captions. Thus, we only
> update case B tokens **if the updates do not break an existing line layout**.`
> `Case C tokens: … updating them only if they are semantically different (similarity < 0.85) **and the update
> will not cause new line breaks**.`

⭐ **映射到我们**：Case A = 新词进底行（照加）；Case B = 对已显示文字的修正（**不许破坏既有行布局**）；
Case C = 语义改写（既要有实质差异、又不许产生新换行）。
→ 这就是"**一旦上滚即冻结**"那条规则的**可执行版本**，也是"先定好 2 行再滚，而不是句中从 1 行变 2 行"的最强论据。
（DOI `10.1145/3544549.3585609` 存在 —— 我探到 `dl.acm.org/doi/…` 解析成功，正文 403 是反爬。）

### 9.4 ⭐ 2026 的新证据：**延迟比准确率更要紧**（`[一手]`）

**Thompson et al., ASSETS '26**（Gallaudet；DOI `10.1145/3797867.3829032`；arXiv:2609.11408v1；
216 名有效参与者 / 4,832 数据点 / 70 段真实电视片段）。逐字：

- 呈现方式：`The player showed captions in the **roll-up style characteristic of live TV captions with smooth
  line transitions** in compliance with the FCC rules`
- 结论：`addressing delays is a **low-hanging fruit, much more so than accuracy metrics**`
- 参与者原话：`I would rather have **incomplete captions in sync with the spoken words** than better captions
  but lagging behind.`（P4）；另一条：`The captions with mistakes were easier to follow than the captions that
  were delayed.`（P3）

→ ⭐ **这条正面支持"先给粗略的、晚点再修"这条路**（我们和 テレ朝 AIポン 都在走的）。修文本的收益
**低于**降低延迟的收益 —— 所以**不能**为了追求"更准"而把草稿推迟。

### 9.5 ⭐ 对**产品定位**直接有用的一条（`[一手]`）

**Li, Y. (2025), Behavioral Sciences 15(4):542**（DOI `10.3390/bs15040542`；64 名**中国**英语学习者，
31 低水平 A1–A2 / 33 高水平 C1–C2）。逐字：

- `Low-proficiency learners prioritized captions (reading scores > listening, Z = −4.55, p < 0.001, **r = 0.82**)`
- `low-proficiency learners **relied overwhelmingly on captions** … median reading scores (11) were **double**
  the median listening scores (5)`
- `Multi-speaker videos **amplified** caption reliance for low-proficiency learners (r = 0.75)`

→ 低水平学习者**以字幕为主通道**（中位数差一倍）。**推论：中文块不该为了省地方被压缩** ——
对目标用户来说，那一条就是主信息，不是辅助。

### 9.6 ⚠️ 我驳回子代理的一条结论

子代理的「没找到」里写「**1 行 vs 2 行的对照实验：不存在**」。**这条不成立** ——
本台账 §5 里我自己核过的两篇就是 1 vs 2：Li 2026（Applied Cognitive Psychology，注意与 §9.5 的 Li 2025 是**不同**两篇）
与 Alves Pinto de Assis et al. 2026（ETRA）。它只是没搜到。
（子代理给的其他几条负面结论我核不出来，就**不采信也不转述**。）

### 9.7 ⚠️ 未核 → 不进结论

| 说法 | 处置 |
|---|---|
| ASSETS '26「许多人偏好比电视规定的 32 字符/行**更多**的字符数」 | ❌ **我读了正文没找到这句**（正文只说播放器**允许**调 characters per line）。**不用** |
| Matthew 2025（tandfonline, `10.1080/1475939X.2024.2433259`） | 正文 403，**没取到** → 不当已核来源 |
| 子代理自报不可靠的四条（`canopyide/canopy` issue、`daintree` commit、`LiveCaptionN`、EACL 2026 demo） | ✅ 它自己标了不可用，**一律进黑名单** |
| 「未定稿文本太晃眼所以关掉」的用户原话 | Reddit 抓取被拒；子代理转述的三条相邻证据可留，**"关掉"这个动作没有可引原话** |
| Apple 的行数 / roll-up 机制 | 仍 `[无明文]`（唯一一手线索是支持页图片 alt：`shown as scrollable text`，指向**滚动**而非固定行 roll-up） |

### 9.8 别的实现怎么做（`[一手]`/`[厂商]`，只为对齐，不构成依据）

| 产品 | 事实 |
|---|---|
| **whisper.cpp `stream`** | README 逐字：`one rolling segment, no timestamps, **rewritten in place with ANSI erase-line escapes**.` → 官方 demo 是**原地重写**，不是 roll-up |
| **Windows 11 Live Captions** | 行数靠**拖窗口大小**：`To show more lines of text in the captions window, increase the window size` |
| **Google Meet** | 只有 Font/字号/颜色/背景色；**未记载行数**。`You can scroll up or down to review` ← ⚠️ 那是**回看**，不是 roll-up，两回事 |
| Otter / Rev / Descript / Fireflies / Granola / MacWhisper / Superwhisper | ⚠️ **零厂商文档**写"未定稿文本怎么更新"。Rev 的渲染交给 Zoom → 受 Zoom 的 2 行上限 |
| **livekit/agents #4779**（2026-02） | 社区规则原话：`Transcription text for a segment should only grow or be replaced by the final transcript. **never shrink mid-utterance.**` |
| **FrankerFaceZ #1524**（2024-07） | 用户原话：`the live captions text behave erratically with the text shifting a couple characters back and forth multiple times a second.` |

### 9.9 目视验证（2026-09-27）

- `overlay.py` 的真实 `Overlay` 渲染成 PNG（隔离 `.window`，面板挪到屏幕外）→ 2 行 roll-up 生效、
  草稿比定稿暗（volatile/finalized 区分可见）、**无裁切**。
- ✅ **贴底 vs 贴顶 —— 作者 2026-09-27 看过两种渲染后确认「贴底」**（即当前实现）。
  - **贴底**（现行）＝ roll-up 的 base row 语义：一行草稿占**下面**那一行，上面那行留空等旧行搬过来 →
    「1 行→2 行」时旧行**向上**走。CFR §15.119 与 W3C 都是这个方向。
  - **贴顶** ＝ 旧行不动、新行往下长，到溢出时整块再跳一次。
  - ⚠️ 两种都渲染过（像素实测不同：114 个扫描行、范围 20..155）。**若以后要翻回去，是一行改动**
    （`_roll_into` 里把"文字框高按行数收缩、底边不动"改成"顶边不动"）。**翻之前先问作者。**


### 9.10 收尾要跑的闸门

| 什么时候 | 跑什么 |
|---|---|
| 任何改动 | `tests/test_audit_regressions.py` |
| 碰 `overlay.py` 布局 | `tests/test_panel.py`（**不在默认闸门里**） |
| 碰滚动 | `probe_scroll.py`（⚠️ 它本身不稳定，判读法见 `CLAUDE.md`） |
| 收尾 | 作者真机上一节课，看两行是否够、是否挤 |
