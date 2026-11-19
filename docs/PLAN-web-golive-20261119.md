# 网页版上线测试评估 · 2026-11-19

目标：网页版搬到 VPS（bldcam，nginx + Cloudflare），先 Owen 自己真课两节，再放 2–3 个朋友（至少 1 个在国内），全程有跟踪数据可查。
前提核对（2026-11-19 09:30 VPS 实测）：内存可用 924 MB、磁盘剩 14 G、nginx 1.24、已有 6 个站点；静态站 build 只有 1.0 MB，放上去不占资源。

## 1. 上线前必须做完（不做会出事）

| # | 事 | 现状 | 谁 / 多久 |
|---|---|---|---|
| 1 | Worker 部署：改课名（W18）、术语上云（W19）、`WEB_ORIGIN` 设成新域名 | 代码在 worktree，测试绿，**未部署** | Claude 验收后部署，**先问 Owen** · 30 分钟 |
| 2 | 网页日额度 4 h（Owen 11-19 定） | 服务端已有 `daily_web_rt_s`，默认 5 h；Soniox 临时 key 的 `max_session_s` 已按剩余额度截断（`handler.js:847`），到点 Soniox 自己断 | 部署时 `WEB_DAILY_RT_S=14400` · 1 分钟 |
| 3 | Pyodide（约 13 MB）、pdf.js 现在从 cdn.jsdelivr.net 拉 | 国内 jsdelivr 时好时坏 → 识别核心起不来 = 整节课没字幕 | 自托管到 VPS `/vendor/`，pi · 1 小时 |
| 4 | 门面回放是作者真课 | `web/.gitignore:26` | 放朋友前换一段公开课，或 Owen 确认可以用 |
| 5 | 跟踪：客户端现在**一条遥测都没有**（grep onerror/sendBeacon = 0） | 只有 nginx 访问日志（真实 IP 已配） | 见 §2 · pi 2 小时 + Claude 界面/文案 |
| 6 | 真麦整节课验收（A11） | 只跑过假麦 e2e（120 s，38 句全有中文） | Owen 上一节真课 |

## 2. 跟踪方案（建议）

只记「好不好用」，不记内容（不存字幕原文、不存音频）：

1. **下课报告**：下课时 `POST /v1/web/report` 一份小 JSON → R2（和桌面版 report.json 同桶不同前缀 `web/`）。字段：时长、句数、断线次数、识别延迟 p50/p90、翻译失败数、同步失败数、浏览器版本、识别核心下载耗时、小窗用了没、是否从课件生成过术语。
2. **出错上报**：`window.onerror` / `unhandledrejection` / 会话 `status='error'` 时发一条（每节课最多 20 条，去重），同一个接口。
3. **看的地方**：管理面板（8770）加一页「网页版」，读 R2 `web/` 前缀；先用脚本出日报也行。
4. **about 页「存了什么」**加一行：「用得怎么样（时长、断线次数、出错信息）会发给作者，不含字幕内容」。

## 3. 搬进 VPS 的做法

- 域名：`class.bldcam.page`（Owen 11-19 定）。**灰云直连 VPS + Let's Encrypt**，不走 Cloudflare 代理：11-18 Globalping 国内探针 CF 78% / VPS 直连 100%（PLAN-update-distribution-cn §10）。
- nginx 新站点：`root /home/ubuntu/classlive-web/current`；`_app/immutable/` 缓存一年；`*.html` 不缓存；`.wasm` 走 `application/wasm` + gzip；SPA 回退到 `200.html`。
- 发布：本机 `npm run build` → rsync 到 `releases/<时间>/` → 切 `current` 软链；回滚 = 软链指回上一版，不删旧版。
- `wrangler.jsonc`（Cloudflare 静态托管方案）先留着不删，VPS 出问题可以 5 分钟切过去。

## 4. 测试分两步

| 阶段 | 谁 | 看什么（过闸门才进下一步） |
|---|---|---|
| T1 | Owen 2 节真课（都柏林） | 整节不断；p90 定稿 ≤ 2 s；下课自动下载成功；换电脑后术语还在 |
| T2 | 2–3 个朋友，≥1 在国内 | 识别核心首下成功率；国内断线次数；有没有人卡在通行码 / 麦克风权限 |

## 5. 已定 / 待定

- 已定（11-19）：域名 class.bldcam.page；网页日额度 4 h；下课报告和出错上报都收、尽量多收。
- 待定：门面回放能不能继续用 Owen 的真课；VPS 反代 Worker / Soniox 开不开（等国内实测）；实时总结排在 T1 之后还是之前。

## 6. 国内网络：一节课要连的 4 个地方（2026-11-19 评估）

| 连哪 | 现在 | 国内风险（证据） | 办法 | 状态 |
|---|---|---|---|---|
| 网页本身 | 还没上线 | CF 代理 78%（同日探针） | 灰云直连 VPS | 定了 |
| 识别核心 Pyodide 13 MB + pdf.js | cdn.jsdelivr.net | GreatFire：`cdn.jsdelivr.net/pyodide/.../pyodide.asm.wasm` 标 Intermittent；21YunBox 2026-08：能通但 p95 首字节 1.76 s | 放 VPS 同源，jsDelivr 只当兜底；`immutable` 缓存一年 → 只下一次 | pi W23 在做 |
| Worker（领 key / 翻译 / 同步 / 术语） | asr.bldcam.page（CF） | 同一个 CF 域名，探针 78% | nginx `/v1/` 同源反代到 Worker（VPS 到 CF 是机房线路，稳）；副作用：Worker 看到的 IP 都是 VPS，按 IP 的统计要改读 X-Forwarded-For | 配置写好、默认关；**先测再开** |
| Soniox 实时流（WebSocket） | 浏览器直连 stt-rt.soniox.com（CF） | 桌面版国内用户：28 次断线、约 2 分钟扎堆 1006、有「坏时段」（11-16 报告） | 两条路：①直连（浏览器会自动走用户开的代理，这点比桌面版好）；②VPS 反代 `/soniox/`。每人每小时约 115 MB 流量过 VPS | 写好、默认关；**国内朋友 A/B 一周再定** |

其他实际问题：
- 浏览器自动走系统代理：开了代理的同学，Soniox 和 Worker 都走代理，一般更稳；没开的就看上面的路线。
- 后台标签页：Chrome 会降速计时器，但音频线程照跑；A11 要实测后台 5 分钟断不断。
- 校园网 / 公司网拦 WebSocket：只能靠 VPS 反代走 443 兜底。
- 不需要 ICP：VPS 在境外，境内不放服务器就不用备案；代价是没有境内节点，首次 13 MB 下载取决于跨境线路。

## 7. 自动下课（忘关网页不烧额度）

- 服务端兜底：4 h 日额度到点 Soniox 自己断。
- 客户端（pi W22 写判断，Claude 接界面）：开课 2 分钟后才开始判；10 分钟没出一个字 → 60 秒倒计时「1 分钟后自动下课」，有声音或点一下就取消；标签页在后台时 5 分钟；开课 15 分钟一句都没出也一样。自动下课 = 正常下课（存好、自动下载、进课后页）。
- 不额外耗电：判断挂在已有的音频帧和每秒计时上，不加新轮询。
- 下一步可省更多：静音 2 分钟就先断开 Soniox、本机继续听，有人说话再连（课间休息不计费）。要改会话层，T1 之后再做。

## 8. 下课报告 + 出错上报（Owen 11-19 同意，尽量多收、不多耗电）

- 全程只在内存里攒，**下课时发一次**；关标签页用 `sendBeacon` 兜底；出错按条攒，跟报告一起走（严重错误立刻发一条，每节最多 20 条）。没有心跳、没有定时上报。
- 收什么：设备与浏览器（UA、内存、CPU 核数、屏幕、语言、时区）、网络（类型 / 估计带宽、断线次数和每次时长、重连耗时）、各段耗时（识别核心下载、开课到第一句、定稿 p50/p90、翻译 p50/p90 与失败数）、同步（推了多少、失败几次）、用法（模式、字号、小窗开没开、多久、标记数、术语数 / 共享术语数、是否从课件生成）、电量（Battery API 有就记开始 / 结束电量和是否插电）、结束方式（手动 / 自动下课 / 关页）。不收字幕原文、不收音频。
- 存到 R2 `web/<码哈希>/<日期>/<session>.json`；管理面板 8770 加「网页版」一页。about 页「存了什么」补一行。
- 等 W19 交回再派（同改 Worker 路由，避免撞车）。

## 9. 实时总结放进网页版：难度评估

桌面版：`live_summary.py` 1260 行 + `atom.py` 434 + `chapter.py` 974，纯 Python（threading / json / pathlib），模型调用是注入的 `chat(sys, prompt)` 同步函数。

| 要做的 | 难点 | 量 |
|---|---|---|
| 三个模块搬进 Pyodide（沿用 CORE_MODULES + 防漂移脚本） | `run()` 开线程 → 网页改由 JS 每 15 s 调一次 `step()`；文件写入（原子流水、章节文件）传 None 或改写进 IndexedDB | pi 半天 |
| 模型调用 | Pyodide 里同步调用 → 放在**已有的提示词 Worker**里用同步 XHR（只卡那个 Worker，不卡字幕）；不新起第二个 Pyodide（省 100+ MB 内存） | pi 半天 |
| Worker 放行 | 网页提示词是哈希白名单（`prompt-allow.js`），要加 atom / chapter 两条系统提示词和 `summary` 调用类、max_tokens 上限 | pi 2 小时 |
| 界面 | 上课页右栏加「这一段在讲什么」、课后页显示章节 | Claude 3 小时 |
| 花费 | 一节课约 60 次 DeepSeek 调用（11-18 重放口径），走同一个 4 h 额度之外的 token 额度 | — |

结论：中等难度，约 2 天（含验收）。**建议 T1 跑稳以后再做**：先保证字幕和翻译在国内不掉，再加总结。

## 10. 经 Cloudflare 为主、失败直连 VPS（Owen 11-19 定方向，**先记录，未做**）

现状（11-19）：Owen 把 class.bldcam.page 切成橙色云（经 Cloudflare）。国内 CF 探针 78%，所以要补一条直连兜底：

| 环节 | 主路 | 兜底 |
|---|---|---|
| 网页本身 | class.bldcam.page（CF） | 首次打开失败只能重试（没有代码可跑）；之后靠 Service Worker 缓存外壳，再开不依赖网络。**网页地址只能一个**：通行码 / 术语 / 本机字幕都按 origin 存，换地址数据会散 |
| 识别核心 15 MB | 同源（CF） | 超时 → 直连备用地址（如 direct.bldcam.page，灰云 → VPS，只放 /vendor 与 /v1 转发，带 CORS）→ 最后 jsDelivr |
| Worker 接口 | asr.bldcam.page（CF） | 失败 → 备用地址 /v1（VPS 反代 Worker）；Worker 按 IP 的限流 / 统计改读 X-Forwarded-For / CF-Connecting-IP |
| Soniox | 直连 | 不变 |

要做（约半天）：备用地址 DNS + 证书 + nginx；loader 与 http.ts 加中间档（超时按国内实测定）；Service Worker 只缓存外壳不缓存数据；Worker 读真实 IP；下课报告记「这节走了哪条路」。
