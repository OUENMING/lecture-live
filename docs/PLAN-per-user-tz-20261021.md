# 按各自的时区看人 + Cloudflare 侧的数据盘点（2026-10-21）

作者 2026-10-21 的三件事：① 面板上美国 / 中国用户的时刻按**他们自己国家的时区**写；
② 把 Cloudflare 侧的数据解析一遍；③ 面板能显示的、能上传的数据，是不是都分析过了。

---

## 1. 时区：从「按国家猜」到「实测」

### 1.1 为什么要改

面板原来的时区是**按国家猜**的（`scripts/cl_admin_ui.py` 的 `_TZ_BY_COUNTRY`）：`US → America/Los_Angeles`。
问题：

- **美国横跨四个时区**（美东 / 中部 / 山地 / 太平洋），同一个 `US` 猜出来的钟能差 3 小时。
  猜对了是运气 —— 现在靠注释里那句「现在唯一的美国用户在加州上课」撑着。
- Cloudflare 只能给到**国家**（`request.cf.country`），给不到城市 / 时区。

### 1.2 现在的做法（2026-10-21 落地）

**实测优先，猜的兜底。** 两级来源：

| 来源 | 谁给 | 什么时候有 |
|---|---|---|
| **客户端自报** | `X-ClassLive-Tz: Europe/Dublin`（`asr_endpoint.tz_header()`，从 macOS 的 `/etc/localtime` 软链取 IANA 名） | 3.10.3 起的客户端（还没发版） |
| **服务端判的** | `request.cf.timezone`（Cloudflare 按访客 IP 判的 IANA 时区，官方文档有） | Worker 重新部署后就有 |
| 兜底 | 按国家猜（`_TZ_BY_COUNTRY`） | 上面两个都没有时 |

链路：

```
客户端 asr_endpoint.tz_header() ──X-ClassLive-Tz──▶ Worker handler.js clientTz()
                                                      │ 取不到就 request.cf.timezone
                                                      ▼
                                    Analytics Engine blob8（新增，2026-10-21）
                                                      ▼
                              cl_admin_ui.py habits_of() → tz / tz_src
                                                      ▼
                                   面板按这个人自己的时区写时刻
```

- Worker：`server/asr-worker/src/handler.js` —— `newEvent()` 多一个 `tz` 字段；
  `track()` 的 `blobs` 末尾多一列（**blob8**）。AE 最多 20 blob / 20 double，现在用 8 / 11。
  ⚠️ 客户端自报的头**形状**由 Worker 砍（只留 `A/B` 这种 IANA 形状、最长 40 字符）——它是客户端可控的。
- 客户端：`asr_endpoint.tz_header()`（唯一来源）、`llm_client.headers()`（翻译 / 笔记那条路）、
  `final_asr.py` 的转写请求头。
- 面板后端：`habits_of()` 每人取**最近一次**报到的时区；`_tz_for()` 决定用实测 / 猜 / UTC；
  输出多一个 `tz_src`（`measured` / `country` / `utc`）——面板上写清楚，不让人以为那是实测。
- 面板前端：`tzOf()` / `tzLabel()` / `hmIn()` / `agoIn()` / `whenIn()`；
  「现在」的在线会话、「要注意」的时间戳、AE 的请求明细与失败请求、测试模式那張表 —— 凡是**带人名**的时刻都换算。

### 1.3 没改的（说明为什么）

- **每日用量 / 速度表的「日期」仍是 UTC 日。** Analytics Engine 的 SQL 不支持带时区的日切
  （实测：`toStartOfDay(timestamp, 'Asia/Shanghai')` 回 `TOSTARTOFDAY() function does not accept 2 arguments`；
  `toTimeZone` 直接 `unknown function call`）。要在本地日切，只能把聚合粒度降到小时再在 Python 里重分桶 ——
  分位数没法从小时 p50 再合成，所以宁可在表里加一列**时区**并写清「日期是 UTC 日」。
- **「上传的课」那张表（云端服务里）仍是本机时间**：那里只有上传动作的时刻，不知道是谁的机器；
  每节课按各自时区显示的时刻在「测试模式」那张表里（那里有 `duration_s`，能反推上课区间）。

### 1.4 判据

- `tests/test_cl_admin_ui.py::HabitsOf`：实测压过国家猜 / 坏时区名退回国家猜 / 取最近一次报的。
- `tests/test_cl_admin_ui.py::TzCase`：抠**页面里真源码**喂 node —— `whenIn` 对不同人给不同钟，
  认不出是谁就退回本机时间。
- `tests/test_final_asr.py::test_10b_带本机时区头`：拿不到 IANA 名就**不发这个头**（空的比假的强）。

### 1.5 还没做的（要发版）

Worker 的 `blob8` 与客户端的 `X-ClassLive-Tz` 都要**部署 / 发版**才生效。
在那之前面板会一直显示「按国家猜」（`tz_src = country`）—— 这是**如实**，不是坏了。

---

## 2. Cloudflare 侧解析（2026-10-21 现查）

### 2.1 账号里有什么

| 资源 | 实况 |
|---|---|
| Worker 脚本 | `classlive-asr`（转写 / 翻译 / 通行码）、`classlive-upload`（只写上传） |
| Durable Object | `classlive-asr` 两个：`QUOTA`（额度）、`PASSES`（一人一码） |
| Analytics Engine | 数据集 `classlive_events`（索引 = 码名） |
| R2 桶 | `classlive-ingest`（我们）、`photosave`（别的项目，面板按 `classlive-` 前缀过滤掉） |
| Zone | `bldcam.page` —— 面板只看 `asr.` / `ingest.` 两个主机名 |
| 其它数据集 | Pages / LB / Tunnel / Queues / Email / Lists / Firewall —— **这个账号没有**（GraphQL 直接报未知字段或无权） |

### 2.2 近 24 h 的数（2026-10-21 18:0x 拉）

- Worker 请求 3390，Cloudflare 口径错误 **0**；墙钟 p50 633 ms、**p95 30.2 s**（流式翻译把尾巴拉长）。
- 子请求：Durable Object 6220、openrouter.ai 1713、DeepSeek 1023、Soniox 三区共 78（eu 32 / us 24 / jp 22）、Jev 9。
  ⚠️ 面板的 `UPSTREAMS` 映射里**没有 openrouter.ai 和 Soniox 三个区** → 那几行显示成裸主机名。
- 节点：SJC 1777（墙钟 p50 589 ms）、DUB 1568（**1924 ms**）、其余零星。
  同样一份代码，落在都柏林的请求慢 3 倍多 —— 值得单独查（是不是 DUB 那边往上游链路更长）。
- 站点：`asr.` 3350（US 1787 / IE 1558 / NL 11 / TH 3 / BE 2 —— 后三个国家基本是扫描器）；
  `ingest.` 119（**PL 72** / IE 23 / US 19 —— 波兰那 72 个是扫上传口的）。

### 2.3 面板已经解析的 Cloudflare 数据

`cf_summary`（Worker 健康 + R2 + DO + 逐分钟）、`cf_health`（延迟 / 上游 / 部署链 / 节点 / 站点）、
`cf_window`（任意时间窗）、`worker_logs`（Workers Logs 里的拒绝 / 全挂 / 额度事件）、
Analytics Engine 9 条 SQL、R2 实时列桶 + 报告摘要、Soniox / OpenRouter 账号。

### 2.4 解析了但**没画出来**的

| 东西 | 在哪 | 说明 |
|---|---|---|
| `cf.scripts.*.cpu_p50/p99_ms` | `cl_admin_ui.py` 解析了 | 页面没有 |
| `cf.dos{}`（DO 调用数） | 解析了 | 页面没有 |
| `cf.r2{桶→逐日 objects/bytes}` | 只用了 ingest 一天的 bytes 当兜底 | 逐日曲线没有 |
| `ae.geo` / `ae.by_code` 里**成功请求的模型分布**（`blob3`） | 只在失败请求里查过 | 成功请求用的哪个模型，面板看不到 |
| `ae` 的 `double7`（Jev 题数） | **一条 SQL 都没查** | （Jev 的次数从 Worker 的 `/v1/admin/stats` 走另一条路到了面板，所以不是完全没数） |
| `ae` 的 `blob1 = 'other'`（不在 AE_PATHS 里的路径） | 从不统计 | 2026-10-21 实查：**近 14 天 1247 条 404**，全是没认证的扫描器 —— 面板的「云端出错率」被它们撑起来了 |
| `soniox.usage[].models[]` / `logs.by_name` | 解析了 | 只在平台卡里聚合成一行 |
| `openrouter.meta`（有哪些指标 / 维度 / 粒度） | 拉了 | 没显示 |

### 2.5 远端还有、**完全没用**的

| # | 资源 | 没用的东西 |
|---|---|---|
| C1 | Analytics Engine | `index1`（码名索引）没被利用 —— 所有分组都靠 `blob2` 字符串重算 |
| C2 | Analytics Engine | 成功请求的 `blob3`（模型）分布 |
| C3 | Analytics Engine | `blob1='other'`（未知路径）整桶 |
| C4 | Workers Logs | `soniox:*` / `groq403` / `provider` 事件**连查询都没有**（只查了 denied / all_failed / quota 三种） |
| C5 | R2 | `extra.tar.gz`（诊断包：`diag.json` / `env.json` / `app.log` / `live.jsonl` / `note.md`）**面板从不下载** |
| C6 | R2 | `meta.json`（崩溃补传凭据）、`transcript.md`（**2026-10-21 起可以看了**，见 §4） |
| C7 | R2 | 第 31 节以后的 `report.json` 永远不被解析（`R2_REPORT_MAX = 30`） |
| C8 | Cloudflare GraphQL | 子请求的其他维度（方法、边缘地区）；CPU 分位数；DO 逐日 |
| C9 | Cloudflare Zone | 路径维度的 4xx/5xx 表（2026-10-14 **有意删掉**：全是扫描器探针） |
| C10 | Worker `/v1/admin/*` | `groq_blocked`（被 Groq 封的国家表）、`rt_s` / `rt_reqs`（Soniox 实时转写秒数 / 请求数）、`daily_rt_s`、`created` |

**最值得先补的三个**（有数据、但面板上看不见）：

1. **`quota.groq_blocked`** —— 「哪些国家被 Groq 封了」的权威表就在 `/v1/admin/quota` 的响应里躺着（`quota-core.js:107`），
   现在只能靠 AE 的 `groq:403` 字符串反推。
2. **`rt_s` / `rt_reqs`** —— Soniox 实时转写**转了多少分钟**、发了几次会话，是这个产品最核心的量；
   Worker 每小时 cron 回收进账本（`passes-core.js:80`），但面板的 `KEYS`（`cl_admin_ui.html:495`）里没这两个键。
3. **`blocks.vad` 与 `blocks.norm`** —— 采集了（`testmode.py:382-383`），但不在面板的 `REPORT_BLOCKS` 白名单里，
   所以面板连「采到没有」都不知道，而 `dropped_s` / `weak_blocks` / `vad_resets` / `blind_dropped` 正是查「丢句」的关键量。

---

## 3. 数据审计：能显示的 / 能上传的，都分析过了吗

完整盘点（字段级、带 file:line）见 §5 的清单。结论：

- **面板已经展示**：Worker 健康 / 延迟 / 上游 / 部署链 / 节点 / 站点、AE 的请求明细与速度、
  R2 清单与报告摘要、Soniox / OpenRouter 额度、额度 DO、通行码表与逐日用量。
- **采集了但面板不展示**（约 30 处）：`report.asr.*` / `corr.*` / `tl.*` / `merge.*` / `backfill.*` /
  `terms.*` / `live.*` / `ui.*` 整块、`region.regions`（每区 connect_ms / rtt_ms）、
  `stream.reconnects / skipped_s / backlog_max_s / msg_gap_max_s / dropped_*`（3.10.1 的卡顿量具，**完全没进面板**）、
  `device` 的样本与明细、`system` 的 chip / macos / arch、`deps.uv / stamp_ok`、`energy.cpu_s / energy_j`、
  `capture.gaps_s`、`cf` 的 CPU 分位数与 DO 计数、Soniox 的按模型花费……
  → 2026-10-21 起的「看」按钮把**每节课已解析的全部数字**一次摊开（§4）。
- **采集了但面板连解析都没有**（整块缺口）：
  - `blocks.vad` / `blocks.norm`（不在白名单）—— 查丢句的关键量；
  - `report.audio{}`（电平 / 动态范围）、`report.resource{}`（CPU）、`report.feature_usage{}`（功能有没有被用）、
    `report.translation_latency.by_model / by_idle`、`report.capture.gaps.items[]`、`report.energy.pump_s{}`、
    `report.deps.installed{}`、`report.terms.core/always/scored`、`report.live` 的另外 14 个字段；
  - R2 的 `extra.tar.gz`（诊断包：依赖现状 / 日志 / 崩溃 / git / 包版本）—— **面板从不下载**，
    「他电脑上缺依赖」这种结论现在只能靠用户把包发过来；
  - Workers Logs 的 `soniox:*` 事件（key 签发失败 / 用量拉取失败 / 回收抛错）。
- **远端还有但完全没用**：见 §2.5 的 C1–C10。

### 一个反直觉的点

**面板的「云端出错率」里，84% 是扫描器。** 近 14 天 1490 条错误里，**1247 条是 `other` 路径的 404**
（没认证的探测；asr 主机上还有 NL / TH / BE，ingest 主机上 PL 72 个）。真正要管的是：

- `deepseek:402` **114 次**（余额不足）：其中 **bldcn88888888 15 次，10-15 07:51–08:36** ——
  中国用户那节课被** Owen 的 DeepSeek 账户余额**卡掉了 15 次翻译。这是「一人一码」但**共用一个上游账户**的直接后果。
- `403` 88 次：**全部是 `denied: devices`（设备数超限）**，Workers Logs 里写得很清楚 ——
  但面板只把 denied 事件**列出来**，没有聚合成告警（测试码 test1 一天被拒 30+ 次，没人管）。
- `520` 1 次（bldus666666，10-16 00:51）—— 上游偶发。

---

## 4. 面板不脱敏（作者 2026-10-21 定）

「通行证面板不需要进行数据脱敏，都是经过用户授权的，不需要任何的脱敏。」

落地（仍然**只读、只在本机环回、token 不进响应、不落盘**）：

- 新接口 `POST /api/raw`（`cl_admin_ui.py:r2_raw`）：按会话 id 取 `report.json` / `transcript.md` 的**原文**。
  - 会话 id 必须是 16 位十六进制；只认这两种文件；超过 4 MB 不给。
- 面板「测试模式」表每行多一个「看」按钮：摊开**已解析的全部数字**（asr / corr / tl / merge / backfill /
  terms / live / ui / deps / energy / capture / device / region / stream …）+ 两个原文入口。
- 判据：`tests/test_cl_admin_ui.py::R2Raw`（能取到 / 只认那两种 / id 不认 / 桶里没有）。

⚠️ **口径变了**：`_report_summary` 仍是**白名单 + 只回数字与枚举**（表格要用），
但**原文入口不再过滤**。`report.json` 里含逐句文本（`segments[].text`、`probe.final_text`、`probe.draft_text`）
与 `transcript.md` 里的课堂转录 —— 面板现在能直接看。

**顺带确认了一件时区的事**：`738cfee7aefa6457`（美国用户那节 ECS170）的 `report.json` 里
`generated = "2026-10-20 17:56:56"`，`transcript.md` 的时间戳是 `16:49:41` 起 ——
那是**他机器上的本地时间**（太平洋），与 `report_at`（00:56:57Z）差 7 小时。
所以「US → America/Los_Angeles」这个猜**碰巧是对的** —— 但现在有实测值可用了，不用再靠猜。

---

## 5. 待作者拍板

1. **Worker 重新部署**（`server/asr-worker` → `wrangler deploy`）—— 部署后 `blob8` 才有值，
   面板的时区从「按国家猜」变成「实测」。⚠️ 部署会改线上 Worker，先问。
2. **发一版客户端**（3.10.3）—— 带上 `X-ClassLive-Tz` 头，比 `cf.timezone` 更准（IP 走代理/VPN 时 CF 会判错）。
3. **数据缺口先补哪个**：我建议按这个顺序 ——
   ① `rt_s` / `rt_reqs`（Soniox 到底转了多少分钟，最核心的量）；
   ② `blocks.vad` / `blocks.norm`（查丢句）；
   ③ `groq_blocked`（谁用不了 Groq 的权威表）；
   ④ `denied` 原因聚合成告警（设备数超限现在没人管）；
   ⑤ 诊断包 `extra.tar.gz` 能在面板里展开（省得每次都要用户发包过来）。
4. **「速度」表的日期要不要换成本地日**：换的话要把 AE 聚合降到小时再在 Python 里分桶，
   分位数会掉精度 —— 我倾向**不加**（表里已经写清「日期是 UTC 日」+ 给了时区列）。
