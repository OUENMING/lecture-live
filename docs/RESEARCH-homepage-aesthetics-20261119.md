# 首页高级感调研 · 2026-11-19

问题：ClassLive 网页版首页（`web/src/routes/+page.svelte`）看着平庸，社区认为「高级感」从哪来，落到这页该改什么。
类型：D（社区共识 + 同类产品对照）。加载 skill：research-sop、design-taste-frontend、impeccable（`impeccable context` 已跑，PRODUCT.md 在；DESIGN.md 是工程笔记，不是视觉规范）。
抓取日期：全部 2026-11-18/19。

## 结论表

| 结论（原子） | 来源 | 源日期 | 置信度 | 备注 |
|---|---|---|---|---|
| 「贵」的头号来源是只有你拿得出的素材（自家实拍/实录），不是布局 | reddit r/web_design「What makes a website feel expensive」高赞回复；design-taste-frontend §4.8 | 2026-03 | 中 | 社区意见，多条回复同向 |
| 感觉统一的站点通常只用 3–4 个字号，杂乱的站 7–8 个以上 | reddit r/UXDesign「looked at about 40 websites」 | 2026-04 | 低 | 单人观察 40 站，无方法；作为自查清单用 |
| 「AI 模板感」的标志：处处圆角卡片+阴影、假产品界面、眉标、emoji 标签、每个决定都一样自信 | reddit r/webdesign「Preventing the AI slop look」、r/UI_Design「Is this style dead」、r/UXDesign「AI-generated UI」 | 2026-06/07 | 中 | 多帖独立一致 |
| 用 div 拼的假产品图是最常见的 AI 痕迹 | design-taste-frontend §9.F | skill | 中 | skill 观点，与社区「fake dashboards」吻合 |
| 「左文案右产品图」本身就是模板，不管刷什么颜色 | impeccable `mode-persuade.md` | skill | 中 | 反方：reddit 有人认为布局没问题，问题在内容不独特 → 见分歧 |
| Wispr Flow 首屏：标题 `Don't type, just speak.`（后半斜体），首屏用「乱说的话 → 整理好的文字」动态演示产品 | wisprflow.ai 原页；growthdives.com 拆解 | 2026-11 / 2026-06 | 高 | 已回源 |
| Wispr Flow 用 Garamond 衬线 + Figtree 无衬线，奶油底 | saaslandingpage.com、a1.gallery | 2026-07 / 2025-09 | 高 | 两个独立收录站一致 |
| Granola 首屏放真笔记内容（Q3 GTM sync），副标题用「Without a meeting bot」点名品类痛点 | granola.ai 原页；ploy.ai 拆解 | 2026-11 / 2026-09 | 高 | 已回源 |
| Superwhisper 首屏是「录音波形 → 转成文字」的动画 | onepagelove.com | 2025-07 | 中 | 单源 |
| Wispr 的 A/B：副标题从诗意改成直接说品类（The voice-to-text AI）| growthdives.com | 2026-06 | 中 | 单源，作者自称观察到的线上版本变化 |
| 中文正文行高约 1.7 倍 | Typotheque《CJK 排版原则》（Peter Biľak）；W3C clreq 行距 50–100% 字号 ⇒ 行高 1.5–2.0 | 2025-05 / 2026-11 | 高 | canonical ×2 |
| 中文正文不加字距（密排是默认），字距只给标题用 | W3C clreq；Bobby Tung（clreq 编辑） | 2026 / 2014 | 高 | |
| 中文标题层级更依赖字重差而非字号；宋体标题「宁可大一号不要加粗」，靠留白做层级 | 龐果設計（2026-09）；站酷「设计便利贴」（2026-09） | 2026-09 | 中 | 两个社区源，方向一致 |
| 宋体在屏幕上 <14–15px 会发虚，只适合大字 | 站酷「设计便利贴」；fournoas 中文网页排版建议 | 2026-09 / 2022-11 | 中 | |
| 中文 web 字体可子集化到约 10KB（只保留用到的字） | fournoas.com | 2022-11 | 中 | 首页标题字固定，正好适用 |

## 分歧

- **衬线 = 高级 还是 = AI 痕迹？** Wispr、Granola 都用衬线标题（高置信），但 reddit 多帖与 design-taste-frontend 都说「每家 AI 创业公司都用的那款衬线」已成 AI 痕迹（Instrument Serif / Fraunces）。裁定：拉丁衬线避开这两款；**中文宋体不在这个 AI 套路里**，社区未见把宋体标题列为 AI 痕迹（未核，低置信）。
- **布局要不要换？** impeccable 说 split hero 本身是模板；reddit 一派说布局没错，错在内容没独特性。裁定：两边都指向同一件事——把左右两半里**能被别家替换的东西**换掉。
- **首屏要不要信任背书？** reddit 40 站帖说要；Ploy 实测 Granola 首屏无背书。ClassLive 没有真实用户评价（PRODUCT.md 禁止编），**真课实录本身就是证据**。

## 对照现首页（1440×900 截图，2026-11-19）

| 社区判据 | 现状 | 判 |
|---|---|---|
| 独有素材当主角 | 真课字幕窗压在一张 div 拼的假课件下面，当配角 | ✗ |
| 不用假产品图 | 蓝金课件是 div 拼的 | ✗ |
| 一种颜色 / 不加彩色（Owen 11-17） | 全页唯一彩色是假课件的藏青+金 | ✗ |
| 3–4 个字号 | 页面约 9 种（120/28/17/16/14/13 + 课件内 + 字幕 18/12） | ✗ |
| 副标题直说品类 / 点名痛点 | 「一个简单的转录翻译项目」+ 测试残留「叽叽喳喳」 | ✗ |
| 中文行高 1.5–2.0 | lede 1.7、hint 1.6 | ✓ |
| 正文不加字距 | ✓ | ✓ |
| 克制动效 | 只有字母入场 + 字幕打字 | ✓ |

## 没查到 / 未核

- Granola 字体：2026 收录写 Quadrant Text，2025 收录写 Radion，可能改版过，未回源 CSS。
- 中文社区（小红书/即刻）对「高级感 AI 产品官网」的具体案例，本轮没搜到成型讨论。
- 宋体标题在中文语境是否已成套路：无源。
