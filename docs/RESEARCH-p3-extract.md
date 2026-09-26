# 调研：课件抽文本（PDF 讲义 / PPTX 课件）—— P3 的准备动作

> 目的：给 P3「开课前的准备」定**文本抽取**这一环的选型。
> 只读不改 —— 本文档不动任何现有文件。**没有安装任何包**。
> 代码状态：HEAD = `1d1d461`。日期 2026-09-26。
> 解释器一律用 `.app` 里那个：`~/lecture-live/ClassLive.app/Contents/MacOS/python`（3.12.13）。

**口径**：`[实测]` = 本次在这台 M1 Pro 上真跑出来的（贴原始输出）；`[一手]` = PyPI 元数据 / 官方 LICENSE，回源读过；`[未测]` = 没测，明写出来，不拿印象顶。
语料是**真实素材**：`~/UCD/**/*.pdf`（59 份 / 1336 页）+ `~/UCD/**/*.pptx`（23 份 / 554 个内嵌媒体）[实测]。

---

## 1. 结论先行

| 问题 | 答案 |
|---|---|
| PDF 抽文本 | **`from Quartz import PDFKit`** —— PDFKit 已经在装好的 PyObjC 里，**新增依赖 = 0** |
| PPTX 抽文本 | **stdlib `zipfile` + `xml.etree`** —— 零依赖，且**能拿到标题/正文占位符的区分** |
| 扫描件 OCR | **Vision（`objc.loadBundle`）** —— 系统框架，新增依赖 = 0 |
| 要不要 Docling | ❌ **不要**（作者已定，§2 用数字坐实） |
| 要不要 python-pptx / pypdf | ❌ **不需要**。作者 brief 点名的那条能力标准库就能拿到，且更细（§5.1） |
| 全语料跑一遍 | **1336 页 4.29 秒**（PDFKit，含打开文件）[实测] |

**一句话**：这件事上 macOS 把三样东西（PDF 文字层、图像 OCR、PDF 转位图）都白送了，
而这台机器**已经在依赖 PyObjC** —— 所以答案不是「挑一个最轻的第三方库」，而是**根本不要引入第三方库**。

---

## 2. 重验 Docling 的拒绝

### 2.1 记录在哪

**在作者自己的 brief 里**（`docs/BRIEF-p3-author.md`，2026-09-26 16:32 落盘）：

```
docs/BRIEF-p3-author.md:17:常规情况（文件自带文本层）用 `python-pptx` / `pypdf` 这类轻量库，
docs/BRIEF-p3-author.md:18:**不需要 Docling 那套重的**。
```

⚠️ **但仓库里有一处自相矛盾**：`docs/PLAN-notes-and-ui.md:463` 的 §7.3 **是"选它（Docling）"**
（`…:467` 逐字「| **Docling** | ✅ ... | **MIT** | ✅ **选它** ... |」，该文件 15:44，比 brief 早 48 分钟）。
而 `PLAN-roadmap.md` §7 又把 `PLAN-notes-and-ui.md` 标成「**老计划（大部分已被本路线图吸收）**」。
→ **以 brief 为准**（它自称「作者的原话……与它冲突的建议一律先问」）。
**建议顺手在 `PLAN-notes-and-ui.md` §7.3 挂个作废标记。**

### 2.2 拒绝的技术依据（本次实测，仍然成立）

`docs/PLAN-p1-app-launcher.md:719` 记过一条**已核实的**实测：「默认安装会拖 torch（PyPI 元数据确认），
而且**选 extra 也躲不掉**」。今天（docling 2.130.0）复测，**一字不改仍成立**，且能给数字了：

```
$ uv pip compile --python-version 3.12 --python-platform macos - <<< docling | grep -E '^(torch|opencv|scipy)'
torch==2.11.0   torchvision==0.26.0   opencv-python==5.0.0.93   scipy==1.18.1
```

**103 个依赖条目** [实测]；`torch` wheel (macOS arm64) **121.41 MB**，
`opencv-python` / `scipy` / `transformers` 46.08 / 27.40 / 11.73 MB [一手 PyPI]；
首次运行还要拉 `docling-ibm-models` 权重（**[未测]**，不装量不到，不编数）。
**许可证没问题**（docling LICENSE = **MIT**，[一手] 回源）—— **拒绝理由纯粹是"重"**：
多 100 个包、多 200MB+ wheel、多一次模型下载，去换一份**本来就自带文字层的 PDF 文本**，不值。

⚠️ 顺带纠正 `PLAN-notes-and-ui.md:467` 的旧口径：它说 Marker 是「GPL 系」—— **已过期**，
`marker-pdf` 现在**代码是 Apache-2.0**（[一手] PyPI + LICENSE）。但**权重是另一回事**（§3.1）。

---

## 3. 候选对比（安装成本全部实测，不是估计）

「会装的包数」= `uv pip install --dry-run` 实测。**没有任何一条需要 Homebrew**
（唯一需要 Homebrew 的是**作者今天正在用的 `pdftotext`**，见 §6）。

| 候选 | 包数 [实测] | macOS arm64 wheel | 许可 [一手] | 文字层 PDF | 扫描件 OCR |
|---|---|---|---|---|---|
| **Quartz/PDFKit**（已在装） | **0** | **0**（随系统） | Apple 框架 + PyObjC(MIT) | ✅ 好 | ❌ 需配 Vision |
| **Vision**（已在装） | **0** | **0**（随系统） | 同上 | — | ✅ 好（§7.1） |
| stdlib `zipfile`+`xml.etree` | **0** | 0 | PSF | — | — |
| `pypdf` | **1** | 纯 py **0.38 MB** | **BSD-2**（回源 LICENSE，无第三条） | ⚠️ 见表注 | ❌ |
| `pymupdf` | 1 | **22.77 MB** | ⚠️ **AGPL-3.0 / 商业双许可** | ✅ 最好 | ❌ |
| `pdfplumber` | **8** | 纯 py 0.06 MB（+`pypdfium2` 3.34 MB） | MIT / BSD-3 | ✅ 好 | ❌ |
| `pdfminer.six` | **5** | 纯 py 6.29 MB | MIT | ✅ 中 | ❌ |
| `python-pptx` | **5** | 纯 py 0.45 MB | MIT | — | — |
| `markitdown` | **20** | 纯 py 0.09 MB | MIT | ✅ 中 | ⚠️ **要 LLM Vision，会静默跳过** |
| `markitdown[all]` | **52** | — | MIT | ✅ | 同上 |
| `unstructured` | **71** | 纯 py 1.56 MB | Apache-2.0 | ⚠️ `fast` = pdfminer 规则 | ❌ |
| `marker-pdf` | **82** | 纯 py 0.19 MB（+torch 121 MB） | 代码 Apache-2.0，**权重 OpenRAIL-M** | ✅ 最好 | ✅ |
| `docling` | **103** | 纯 py（+torch 等） | MIT | ✅ 最好 | ✅ |

### 3.1 四个必须点名的坑

⚠️ **`pymupdf` 是 AGPL-3.0**（PyPI license 逐字：`Dual Licensed - GNU AFFERO GPL 3.0 or Artifex Commercial Lic`）[一手]。
本项目是 MIT —— **与 MinerU 出局同一个理由，不能只对 MinerU 严、对 PyMuPDF 松。**

⚠️ **`markitdown` 说"最轻"，实测并不轻**：基础安装 **20 个包**，因为文件类型识别拉 `magika` + **`onnxruntime`（20.55 MB）** [实测]。
且它的 OCR **不是本地能力** —— 官方 README 逐字：`markitdown-ocr` 插件
「extracting text from embedded images using **LLM Vision**」，「If no `llm_client` is provided …
**OCR is silently skipped**」[一手]。→ **扫描件必须上云，不上云就静默少字**；
与 `PLAN-notes-and-ui.md:468` 旧结论一致，本次**证实**。

⚠️ **`marker-pdf` 权重不是 Apache**：README 逐字「Our code is licensed under Apache 2.0」，
但「Our model weights use a modified AI Pubs Open Rail-M license」，
「free for research, personal use, and startups under **$5M** funding/revenue」[一手]。
本项目（个人自用 + MIT 开源）**在允许范围内**，但**别在 README 里写成 Apache**。

⚠️ **`unstructured` 的 `fast` 模式不是 OCR 替代品**（pdfminer 规则，扫描件抽不出）—— 与 `PLAN-notes-and-ui.md:469` 一致，沿用旧记录未复测。

---

## 4. ⭐ macOS 原生路线：为什么是降维打击

### 4.1 PDFKit 就躺在已经装好的 `Quartz` 包里

**本次最重要的发现，且违反直觉**：

```
$ python -c "import PDFKit"                → ModuleNotFoundError      ← 顶层没有
$ python -c "from Quartz import PDFKit"    → OK <class PDFDocument>   ← 但 Quartz 里就有
$ ls .../site-packages/Quartz/
CoreGraphics  CoreVideo  ImageIO  ImageKit  PDFKit  QuartzComposer  QuartzCore  QuartzFilters  QuickLookUI
```

`pyobjc-framework-PDFKit` 那个包**不用装** —— PyObjC 的 `Quartz` 包装里**已经含 PDFKit**，
而 `pyobjc-framework-Quartz>=12.2` **已经在 `requirements.txt` 里**。
→ **新增依赖 = 0，新增代码 = 一次 `from Quartz import PDFKit`**。冷启动实测 `0.115s`（含解释器启动）。

### 4.2 全语料实测：59 份 PDF / 1336 页 / 4.29 秒

```
UCD 真实 PDF 共 59 份
总页数=1336  耗时=4.29s  (含 PDFKit 打开+逐页 string())
✅ 有文字层 (>=50 字符/页): 57 份, 共 1282 页
❌ 无文字层 (0 字符, 需 OCR): 2 份
     SOC10020__Picketty 2015 ...                       页= 13
     SOC10020__Terrible Magnificent Sociology (2022) Wade - The S...   页= 41
```

**读法**：真实语料 **95% 的页自带文字层**。全语料 1336 页里**文字层为空的只有 63 页（4.7%）**，
其中 54 页来自上面那 2 份整本扫描的 PDF，另外 9 页是夹在有文字层文档里的空白页/纯图页 [实测]。
→ **OCR 是边角料，不该为它把地基换成重型方案。**

### 4.3 ⚠️ 线程：PDFKit 能脱离主线程（但有一条没测到）

本项目硬不变量之一是「AppKit 的调用只能发生在**主线程**」（`CLAUDE.md`），所以这条必须问：

```
后台线程 PDFKit: OK 页=7 字符=7907      主线程 PDFKit: OK 页=7 字符=7907
后台线程 Vision: OK 块=50 0.236s         主线程 Vision: OK 块=50 0.049s
```

⚠️ **但这条测得不完整**：我是在**没有跑 NSApplication 的裸脚本**里测的。
真 app 里（`main.py` 起了 `NSApplication`）跨线程读 PDFKit 是否同样安全，**[未测]** ——
PyObjC + PDFKit 官方文档没有线程安全承诺。
→ **实现按保守做法：抽取丢 `daemon` 线程，真出问题时的退路是 §8 的备选。**

---

## 5. PPTX：标准库就够，而且比 python-pptx 更细

`zipfile` + `xml.etree` 抽正文实测 `0.002s`（17 页课件）：

```
ECON10740__Week2_Communicating_Ideas_SLIDES_26_fin.pptx
zip 条目=70  slides=17  notesSlides=0  media=1   耗时=0.002s
--- ppt/slides/slide2.xml ---
Why does communication matter for economists? | Economists don't just crunch numbers | ...
```

⚠️ **一个实现坑（实测踩到）**：只 `.iter()` 收 `<a:t>` 会把同段落的 run **粘在一起**
（`Communicating Ideas in EconomicsDr. Ciara WhelanGlenn McNamara`）。
**必须按 `<a:p>`（段落）分段，段内再拼 `<a:t>`** —— 改对后输出是干净的多行。

### 5.1 ⭐ 作者 brief 点名的「标题占位符」，标准库能拿到，且更细

`docs/BRIEF-p3-author.md:22` 要求区分标题/正文占位符，标题级词给更高权重。
**标准库直接读 `<p:ph type=...>` 就行，粒度比 python-pptx 还多两档**（含 `ph` 无 `type` 属性 = body）：

```
--- slide1.xml ---   [ctrTitle] Exploring Economics
                     [subTitle] Week 2:  /  Communicating Ideas in Economics  /  Dr. Ciara Whelan
--- slide2.xml ---   [title   ] Why does communication matter for economists?
                     [body(默认)] Economists don't just crunch numbers ...
```

**23 份真实课件统计：标题档 16,130 字符 / 其余 87,190 字符 → 标题占 15.6%** [实测]。
→ 这 15.6% 是**免费的高权重信号**，**不需要为此引入 python-pptx**。

### 5.2 备注页：能拿到，但真实语料里几乎没内容

备注结构上**完全可达**（`ppt/notesSlides/notesSlide*.xml`，同样 stdlib），但 23 份真实课件合计
**正文 120,759 字符 / 备注 2,454 字符 = 2.03%**（其中 59% 的备注字来自同一份 108 页的 Library session；
`.Trash` 里那份 Physics 课件有 43 个 notesSlide 而**实质内容只有 12 字符** —— 空壳）[实测]。
→ **顺手抽（成本≈0），但不值得为它做任何设计。**

### 5.3 与作者现有手工产出的正面对拍

作者 vault 里已有人工转的 `week2-communicating-ideas-slides-26-fin.md`，拿它当基准：
作者手工 `.md` 833 词 / 425 唯一；**标准库抽取 760 词 / 413 唯一；Jaccard = 0.9671** [实测]。

- 「仅 `.md` 有」的 13 个词**全是元数据/工具噪声**：`course` `econ` `fin` `owen` `pptx` `slides`
  `sub` `materials` `number` `jpg` `contentplaceholder`（前几个是 frontmatter，`owen` 是路径，
  `contentplaceholder` 是**把占位符名字当正文抽出来了** —— 那份流程的瑕疵）。
- 「**仅抽取有**」的 1 个词是 **`visual`** —— 已核实是**真实幻灯片正文**
  （`slide12.xml`「Make use of visuals」等 8 处），**是手工流程漏掉的**。

→ **标准库抽取在正文覆盖上不输手工流程，且更全。**

---

## 6. ⭐ 与「今天实际在用的做法」对拍：`pdftotext`

作者 vault 里材料的 frontmatter 逐字记着用了什么：
`extracted_with: pdftotext -layout (poppler 26.06.0)`（3 份）+ `extracted_with: tvly extract`（6 份，云端）。
而 `pdftotext` 实测装在 **`/opt/homebrew/bin/pdftotext`（Homebrew poppler 26.06.0）** ——
**正是本项目最想避免的那类依赖**。所以这次不是跟假想敌比，是**跟真正在用的工具比**：
同一份 PDF 两边各抽一遍，**按词**（`[A-Za-z][A-Za-z'-]{2,}`，转小写）**比较多重集**。

| PDF | 页 | pt 词数 | PDFKit 词数 | 仅 pt 有 | 仅 PDFKit 有 | Jaccard |
|---|---|---|---|---|---|---|
| ECON10730__Excel Core | 302 | 74,260 | 74,260 | **0** | **0** | **1.0000** |
| ECON10730__Excel Expert | 232 | 52,282 | 52,282 | **0** | **0** | **1.0000** |
| ECON10730__Tutorial 1 | 12 | 1,366 | 1,366 | **0** | **0** | **1.0000** |
| 12bc3578-…（44 页文档） | 44 | 128 | 128 | **0** | **0** | **1.0000** |
| MATH10250/10430 Chap3 Hyperbolic | 29 | 792 | 794 | **0** | **15** | 0.8966 |
| 2. Absorption of beta rays | 7 | 738 | 738 | **0** | **0** | **1.0000** |

**读法（全文最强的证据）**：**584 页里 `pdftotext` 一个词都没多找到**（「仅 pt 有」列全是 0）。
4 份**完全相同**（连词频都一样，不只是词表相同）。唯一有差异的是**公式密集的数学讲义**，
而差异方向是 **PDFKit 多出 15 个数学记号**（`sinhx` `coshx` `lnx` `cothx` `sechx` …）—— **是 PDFKit 赢**。

> **验证器自检**（按「数字必须来自测量」）：负对照 —— 故意从 PDFKit 文本里删掉所有 `the`，
> Jaccard 应声掉到 **0.9869**。说明比较器**不是恒真的**，`1.0000` 是真的相等。

⚠️ **一个被我自己误报过、必须澄清的点**：我一开始看到 PDFKit 把 `α/β/γ` 读成空格，以为"PDFKit 丢符号"。
**是错的，那是我终端的显示假象。** 逐字符实测：两份输出的**非 ASCII 字符集完全相同**，
`α/β/γ` 在**两边都是**私用区码位（`U+F061` `U+F062` `U+F067`）—— 这是 PDF 用 Symbol 字体老编码的结果，
**`pdftotext` 一模一样**。要还原成真 `α` 得自己挂 PUA→Unicode 映射表，**与选谁无关**。

---

## 7. 图像与公式：会丢什么，要不要紧

| 输入 | PDFKit/文字层 | Vision OCR | 结论 |
|---|---|---|---|
| 真·文字层 PDF | ✅ 全拿 | — | 主路径 |
| 扫描件（**63/1336 页 = 4.7%**） | ❌ 0 字符 | ✅ 可读 | OCR 补 |
| 讲义里的**公式** | ⚠️ 私用区码位（§6 澄清） | ⚠️ **更差** | 文字层 > OCR |
| PPTX 内嵌**矢量图**（`wmf`/`emf`/`wdp`） | ❌ | ❌ **Vision 读不了** | 真丢失 |
| PPTX 内嵌**位图**（jpg/png） | ❌ | ✅ 可读 | OCR 补 |

### 7.1 Vision OCR 实测（零新增依赖，靠 `objc.loadBundle`）

没有 `pyobjc-framework-Vision` 也能用 —— **框架本身随系统在**：
`objc.loadBundle("Vision", globals(), bundle_path="/System/Library/Frameworks/Vision.framework")` → `loadBundle OK`，
`VNRecognizeTextRequest` / `VNImageRequestHandler` 都拿到 [实测]。

⚠️ **一个真坑**：没有 PyObjC 的 Vision 包装 = **没有 selector 的 metadata**，
`performRequests:error:` 的 out-param 不会被解包（报 `cannot unpack non-iterable bool`）。**必须手工补一行**：

```python
objc.registerMetaDataForSelector(b"VNImageRequestHandler", b"performRequests:error:",
    {"arguments": {2: {"type": b"o^@"}, 3: {"type": b"o^@"}}})
```

补完之后一切正常。对真实扫描页（Wade 教材，文字层 0 字符）实测：
`位图 1224x1584  OCR 耗时=0.272s  块=18  OCR 字符数=1110`，正文可读：

```
those conclusions on our owrn and certainly before we have the capacity to
challenge thenL "He's a kicker," a soccer-loving expectant mom might say proudly ...
```

→ 噪声集中在虚词（`owrn` `thenL` `nlly` `sticK`）—— **对"挑候选术语"够用**
（噪声词不是术语，真术语 `personality trait` 之类完好）。速度 **0.27s/页** → 63 页全补 **≈17 秒** [实测外推]。

⚠️ **公式上 OCR 反而更差**（这条决定 §8 的顺序）：同一页，PDFKit 给出 PUA 码位（挂映射表能还原），
Vision 却给出**看起来像字母的错字**（`F-rays` `(x-rays` `P-rays` `y-rays`）。→ **先 PDFKit，空了才 OCR。**
（转位图那条路也是免费的：`CGPDFDocument` + `CGBitmapContext`，零依赖。）

### 7.2 ⚠️ Vision 的语言支持有个硬边界（实测，与直觉相反）

```
rev=3 accurate(1)  共 6 种: en-US fr-FR it-IT de-DE es-ES pt-BR     ← 没有中文！
rev=3 fast(0)      共 18 种: ... zh-Hans zh-Hant yue-Hans yue-Hant ko-KR ja-JP
```

**`setRecognitionLanguages_(["zh-Hans"])` 在 accurate 档会被静默忽略**（不报错）。
我一开始拿一张中文图测出满屏乱码，根因就是这条 —— 换 `fast` 后块数 50→92，输出里开始出现中文片段，**机制确认**。
→ 本项目主场景是**英文课堂**，accurate 档正合适；真要读中文图**必须降级 `fast`**。
⚠️ 那张中文测试图是低清翻拍，`fast` 档下仍不理想 —— **中文 OCR 质量本次没测出可信结论**，不硬下判断。

---

## 8. 选型结论

### 主选：macOS 原生三件套（PDFKit + stdlib PPTX + Vision 兜底）

**一句话理由**：三样能力（PDF 文字层、PPTX 解包、图像 OCR）**这台机器已经全都付过钱了**
（PyObjC 本来就在 `requirements.txt`，Vision/PDFKit 是系统框架），新增依赖 **0**、新增安装步骤 **0**、
全语料 **4.29 秒**，而且在**与作者今天在用的 `pdftotext` 的正面对拍里，584 页一个词都没输**。

| 步 | 做什么 | 依据 |
|---|---|---|
| 1 | 扩展名分流（`.pdf` / `.pptx`） | — |
| 2 | **PDF**：`from Quartz import PDFKit` → 逐页 `pageAtIndex_(i).string()` | §4 |
| 3 | **PPTX**：`zipfile` + `xml.etree`，**按 `<a:p>` 分段**，**记下 `<p:ph type>` 作权重标记** | §5 |
| 4 | **空页兜底**：只有某页 `string()==""` 时才走 Quartz 转位图 + Vision OCR（accurate 档） | §7.1 |
| 5 | 全流程丢 `daemon` 线程，结果回 `streamq`（⚠️ `drain()` 记得加分支） | §4.3 |
| 6 | 转换失败**不阻塞**笔记落盘 | `PLAN-notes-and-ui.md:498` |

### 备选（runner-up）：`pdfplumber` + stdlib PPTX

8 个包、MIT、纯 Python（重活在 `pypdfium2` 的 3.34 MB wheel 里）、**完全无线程顾虑、输出不随 macOS 版本变**。
**它存在的唯一理由就是 §4.3 那个 [未测] 项** —— 万一 PDFKit 在真 app 里跨线程出问题，换掉抽 PDF 那一行即可。
（`pypdf` 更轻（1 包 0.38 MB）但版式处理弱于 `pdfplumber`；`pymupdf` 抽取最好但 **AGPL-3.0**，**不建议**。）

### ⚠️ 最强的一条反对意见（我认这条是真风险）

**PDFKit 的输出跟着 macOS 版本走，不可 pin。** WeasyPrint 那次选型（`PLAN-roadmap.md` §4.3）里，
评审提的「确定性、版本可 pin」被记成**成立但不足以翻盘** —— 同样的天平在这里也成立：
今天我在 macOS 24.6.0 上量到 PDFKit 与 `pdftotext` 词表逐一相同，
**但 Apple 改一次 PDFKit 的 `string()` 行为，这份"零依赖"就从优势变成不可复现。**

**第二条（更具体）**：`PDFKit` 藏在 `Quartz` 包里是 **PyObjC 的打包细节**，不是 Apple 的承诺；
PyObjC 上游若把 PDFKit 拆成独立包，`from Quartz import PDFKit` 会**在用户的机器上**断掉。
缓解：这一行包 `try/except`，失败回落 `pdfplumber`。

**两条都不推翻主选** —— 它们都有**一行代码就能换掉**的退路（这正是接缝的价值），
而主选换来的是「朋友不用装任何东西」+「584 页零词丢失」。

### 用户装它要花什么

**0 步。** 不用 `brew`、不用 `pip install`、不用下模型、不用联网。
`requirements.txt` **一个字都不用改** —— `pyobjc-framework-Quartz` 已经在里面了。
（这也是本次唯一"安装成本为零"的候选；其余每一个都至少多 1 个包。）

---

## 9. 本次没能测到的（不许当结论用）

- **docling-ibm-models 首次要下多少 MB** —— **[未测]**，不装量不到。§2 数字全部来自依赖解析，不含模型权重。
- **PDFKit 在真 NSApplication 里跨线程读是否安全** —— **[未测]**（§4.3），裸脚本通过，真 app 未验。
- **Vision OCR 的中文质量** —— **[未测]**，唯一中文测试图是低清翻拍，两档都不理想，不能据此判断。
- **PPTX 里 `wmf`/`emf`/`wdp` 矢量公式的内容** —— **[未测]**，Vision 读不了这类格式；
  UCD 语料里可忽略（8.78 MB 是 `mp4`，`wmf` 仅 0.00 MB）。
- **扫描件 OCR 出的词对术语抽取是否有帮助** —— **[未测]**，要跑完整流水线并人工看候选词，超出「抽文本」这一环。
- **`pdftotext` 对拍在更多公式密集 PDF 上的表现** —— **[未测]**，只有 1 份数学讲义命中差异，样本是 1。
