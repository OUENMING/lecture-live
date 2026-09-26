# 课程资料

（这个目录是给你放**这门课的资料**的：课件、教材、讲义、论文。

    <你的 vault>/Study/<课号>/materials/
                       ↑
              你在 `cl course` 里设的那个课号，比如 ECON10740

## 现在能做什么、还不能做什么

| | |
|---|---|
| ✅ **能** | Obsidian 里直接打开读；让 AI（Claude / Obsidian Copilot）读着它帮你整理复习 |
| ⏳ **还不能** | **ClassLive 自动生成笔记时还不会读这个目录** —— 那部分在做（见 `docs/PLAN-notes-and-ui.md` §7-B/§7-C） |

**所以这个目录现在放不放都不影响 ClassLive 正常跑。** 早放的好处是：等功能上了，
你的资料已经在位，不用再补。

## 支持什么格式

**只认 Markdown（`.md`）。**

PDF / PPTX / DOCX 的自动转换**还没做**。在那之前手动转：

```bash
pandoc -t markdown -o 输出.md 输入.pdf        # 通用，双栏 PDF 效果一般
markitdown 输入.pdf > 输出.md                 # 微软的，更轻；扫描件要联网 OCR
```

⚠️ 学术 PDF（双栏、图表、公式）转换质量参差，转完**扫一眼**再用。

## 命名建议

按**周次或主题**开头，方便和课堂录音对齐（ClassLive 也是按这个找对应资料的）：

```
week1-crisis.md                     ← 按周
week2-communicating-ideas-slides.md ← 课件
theme2-sociology.md                 ← 按主题
wade-ch1-the-self.md                ← 教材章节（作者-章-标题）
collins-2001-social-movements.md    ← 论文（作者-年份-关键词）
```

## 这个目录里别放什么

- ❌ **不要放你的课堂录音**（那在 `sessions/`，且音频不出本机）
- ❌ 不要放个人隐私材料 —— 这个目录会被 AI 读
