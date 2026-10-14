# asr-worker 该不该"放得更聪明"—— Cloudflare Placement 与 DO 短缓存

> **一句话**：都不值。默认（就近执行）就是最优；"把 DO 调用缓存掉"这个想法经不起读代码。
>
> **口径日期 2026-10-14。** ⚠️ 文里的 `file:line` 是**当天那次改动之后**的行号，之后会漂 —— 按符号名查。 下面的数字都是当天从作者这台机器（都柏林）实测的，复跑命令在文里。
> 时效性说明：调研类结论的**前提一变就该重跑**（上游换供应商、加了真后端、有了远地用户）。

## 0. 两个问题

`server/asr-worker` 是定稿转写代理，上游四家：Groq / 阿里云百炼国际站 / DeepSeek / TypeSafe(Jev)。

1. Cloudflare **Placement**（把 Worker 放到"离后端近"而不是"离用户近"）该不该开？
2. 每 12 秒一段都要问一次 Durable Object，能不能**短缓存**掉？

---

## 1. 实测数字（2026-10-14 · 都柏林）

### 1.1 到上游的网络

`curl -w`，3 次取最好。

| 上游 | TCP connect | TLS | 首字节 | 总 |
|---|---|---|---|---|
| api.groq.com | 13.9 ms | 24.7 ms | 192.8 ms | 193.5 ms |
| api.deepseek.com | 7.5 ms | 16.0 ms | 289.6 ms | 290.0 ms |
| api.typesafe.ai | 9.2 ms | 19.6 ms | 176.9 ms | 177.0 ms |
| dashscope-intl.aliyuncs.com（新加坡） | 263.5 ms | 527.0 ms | 786.0 ms | 786.4 ms |

⚠️ **口径**：POST 空 JSON 体、对方返 401。所以「首字节」**含对方鉴权拒绝的处理时间**，
**不等于纯网络 RTT**，四家鉴权栈耗时不同，横向只能比 connect / TLS 两列（那两列是纯网络）。

复跑：

```sh
for u in https://api.groq.com/openai/v1/models \
         https://api.deepseek.com/v1/models \
         https://api.typesafe.ai/ \
         https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation; do
  for i in 1 2 3; do
    curl -s -o /dev/null -X POST -H 'content-type: application/json' -d '{}' --max-time 10 \
      -w '%{time_connect}|%{time_appconnect}|%{time_starttransfer}|%{time_total}\n' "$u"
  done | sort -t'|' -k4 -n | head -1
done
```

### 1.2 域名归属（`dig` + 对拍 `cloudflare.com/ips-v4`）

| 域名 | 解析到 | 归属 |
|---|---|---|
| api.groq.com | 172.64.149.20 / 104.18.38.236 | **Cloudflare 段内**（172.64.0.0/13、104.16.0.0/13） |
| api.typesafe.ai | 104.18.24.46 | **Cloudflare 段内** |
| api.deepseek.com | d3bbv8sr76az5s.cloudfront.net | AWS CloudFront（全球 CDN） |
| dashscope-intl.aliyuncs.com | `nlb-…ap-southeast-1.nlb.aliyuncs.com` | **阿里云新加坡**：单区域、非 anycast |
| asr.bldcam.page / ingest.bldcam.page | 104.21.53.142 / 172.67.213.135 | Cloudflare（自己的） |

### 1.3 当前落点

```sh
curl -s https://asr.bldcam.page/cdn-cgi/trace | grep -E '^(colo|loc)='
# colo=DUB  loc=IE
```

⇒ 作者的请求本来就落在都柏林节点，跟前三家上游的边缘是邻居。
**（这是这一台机器的落点，不代表其他用户。）**

---

## 2. 为什么不建议开 Placement

Placement 的本质是**拿「离用户近」换「离后端近」**。它的收益前提是官方那句
"most useful when your Worker makes **multiple round trips**"。ClassLive 五条都不满足：

1. **没有多次往返。** `handler.js:323-330` 注释写得很清楚：千问只在「Groq 失败的当下，
   或 Groq 发出 1.2 s 还没回（对冲）」才起跑 —— 正常情况**只发 1 个子请求**。
2. **上游不是单区域。** 见 1.2：Groq / TypeSafe 在 Cloudflare 段内、DeepSeek 走 CloudFront。
   唯一单区域的是阿里云新加坡，而它是**备胎**。把整个 Worker 拖去新加坡救备胎、牺牲主路径，不划算。
3. **热点路径上有两个单实例 DO**（见 §3），而 **DO 的位置不跟着 Worker 走** ——
   官方：DO "provide automatic placement without configuration"。搬走 Worker 只会把 DO 那两跳拉长。
4. **限流计数是"每个节点各算各的"** —— 官方原文 "Rate limits ... are local to the Cloudflare
   location that your Worker runs in"。把多来源流量收拢到一个节点 = **限流变严**
   （`server/asr-worker/wrangler.toml` 里已注明这条）。
5. **流量太小，且可能触发供应商风控。** Smart Placement 需要"多来源 + 足够多的请求"才决策，
   否则状态停在 `INSUFFICIENT_INVOCATIONS`。另有实测报告称 AI 供应商会封某些 CF 节点
   （Gemini 直接返 400 "User location is not supported for the API use"），
   而 placement 会自作主张挪位置、失败又"看起来更快"，未必能识别并回退。

**未证实、如实标出**：Smart Placement 官方明说 "all Workers plans"（免费也能开）；
但 `region` / `host` / `hostname` 三种 **Placement Hints 的计划门槛**，官方那页没写，
社区帖对本机返回 403，**没能确认**。所以那三种不能假定免费。

---

## 3. DO 短缓存：读代码之后不成立

> ⚠️ AI 最初给的建议是"`getPlan` 结果按 (model, seconds) 短缓存"。**读代码后作废。**
> 原因是这两个端点**都不是读接口** —— 坏处不是"缓存会过期"这种泛泛的顾虑。

### 3.1 `/plan` 是「预留」，不是「查询」

`src/quota-core.js:98-112` 的 `#take()`：批准的那一刻就 `b.n += 1`、`b.hour.s += billed`、
`b.s += billed`；`#plan()`（:85-101）结尾 `await this.storage.put("state", s)`。
注释原话：**「批准即计数」**。

⇒ 缓存命中 = 跳过 `#take` = **这次不计数**。这不是"延迟生效"，是**永久漏记**，而且每个 isolate 各漏一份。
R18（Groq 每小时 7200 / 每天 28800 音频秒，95% 阈值）会形同虚设，
`GROQ_DAY_MAX = 1900 // 留 5% 余量，主动降级不等 429` 的设计意图直接作废 ——
等于把「提前降级、不等 429」换成「撞了 429 再靠 `groq_cooldown` 兜」。

### 3.2 `/check` 是读-改-写

`src/passes-core.js:85 起`：判完吊销 / 过期 / 日额度后要 `devs[install] = {first, last}` 再 `storage.put`。

缓存它的代价（可控但真实）：

- **日额度最多超发 TTL 时长**（12 s 一段 + TTL 30 s → 最多多放 2–3 段）
- **吊销 / 过期 / 设备超限的生效延迟 ≤ TTL** —— 与既有立场冲突：`handler.js:89` 注释写着
  "DO 不可用 → 503（**不放行：码模式下钱是 Owen 付的**）"，整套是 fail-closed；
  正缓存等于开一个有时限的 fail-open 窗口。
- 设备登记**不会**额外漏（缓存 key 含 `install`，新设备必然 miss）—— 这条推演过，不算坏处。

**而且：把上面两个洞都补回去，缓存省的又等于零。** 要压回额度超发、要补回 fail-closed，
就得让一部分请求绕过缓存真调 DO，绕回来还是每段一次。

### 3.3 更根本的一条：命中率不可控

缓存只能放模块作用域，而官方（How Workers works）写的是：
"isolates are **not necessarily long-lived**. An isolate may be spun down and evicted for a number of reasons."
同一节点还可能不止一个 isolate。⇒「缓存 30 秒」实际可能是 0 秒，也可能远超。
要判断它到底有没有用，**得先加埋点量命中率** ——
为省 ~1%（DO 就在都柏林隔壁，一段总耗时 ~400 ms）去加一层改变额度语义的缓存再加埋点，不划算。

### 3.4 真要做，干净的形状是「批量预留」，不是缓存

保持「先预留再使用」这个不变量，只把粒度放大：`/plan` 一次预留 N 段、返回一张有过期时间的租约，
之后 N 段本地放行、不再问 DO。DO 调用从「每 12 秒一次」降到「每 N 段一次」。

代价明确、可控：中途停了会多预留（保守方向，只是少用一点自己的额度）；
租约期内档位（v3 / turbo / 千问）不跟着额度变化调整。

**但这是协议改动** —— 要动 `handler.js` 的请求形状和客户端（`final_asr.py` / `main.py`），
不是一个缓存能解决的。

### 3.5 已经做对的那半

`reportUsage`（`handler.js:99-102`）和 `report`（`handler.js:313-316`）都走 `ctx.waitUntil`，
写路径本来就压不到关键路径上。卡在前面的 `checkPass`（:410）和 `getPlan`（:319）
之所以必须 `await`，正因为它们是**预留**，不是能缓存掉的东西。

---

## 4. 什么情况下重新评估

- 有了**真的远地**的通行码用户，且量出来 DO 那两跳占每段延迟的可观比例（比如 > 10%）
- 或者 `dashscope-intl`（新加坡）从「备胎」变成主路径
- 或者真的接了**单区域**的自家后端（数据库 / VPS）—— 那才是 Placement 的标准场景

在此之前，任何放置调整或缓存都是拿正确性换一个测不出来的收益。

---

## 5. 本文的口径限制（别外推）

- §1.1 的「首字节」**含对方鉴权处理**，不是纯 RTT。
- §1.3 的 `colo=DUB` 只是**作者这一台机器**的落点。
- §2 第 5 条里"AI 供应商封节点"来自社区帖标题与搜索摘要，**没能逐条打开原文核实**。
- §2 末尾的 Placement Hints 计划门槛 **未证实**（见该处）。
