# 计划：一人一码（通行码代付 ASR + DeepSeek）

> 2026-10-11 起草 · Owen 已同意方向（「同意设置一人一码 key 的方式，并且可以扩展一些功能」）
> 状态（2026-10-12）：**§5 第 1–4 步代码已写、在 `next-391` 分支，未部署**。第 0 步（量钱）与 §4 告知文案、
> 扩展功能 1–3 未做。部署前 Owen 要：`wrangler secret put DEEPSEEK_KEY` / `ADMIN_TOKEN`、把 ADMIN_TOKEN 存进
> `~/.classlive/asr-admin-token`（600）、点头 `wrangler deploy`（会建新 DO `Passes`，migration v2）。
>
> 与本稿的偏差：码表 / 设备 / 用量用 DO 的 KV 接口存（`code:` / `dev:` / `use:<日>:` 三种键），不是 SQL 表 ——
> 同一个 SQLite 后端，Node 里能直接测；`list({prefix})` 够管理命令用。翻译请求**不过**转写那两道限流
> （码是个人的、可吊销可限额，而一节课的翻译每分钟会超 30 次）。

## 0. 一句话

Owen 给每个朋友发一串通行码 `cl-<名字>-<随机>`。朋友把它粘进现有的「翻译引擎 → 填 key」框，
定稿转写 + 翻译（实时 / 草稿 / ❓点拨 / 问答 / 课后精修 / 实时总结）全部经 Owen 的 Worker 走，
DeepSeek 的钱由 Owen 付；Owen 能按人限额、吊销、看用量。原生 `sk-` key 照旧直连 DeepSeek。

## 1. 已核实的事实（2026-10-11）

| 事实 | 出处 |
|---|---|
| 生产代码里写死 `api.deepseek.com` 的有 5 处：`cloud_translator.py`（`DEFAULT_BASE`）、`build_notes.py`（`_chat_json`）、`polish.py`、`obsidian_writer.py` ×2 | grep；`main.py` `_summ_chat` 注释也记着「5 处各自手写」 |
| `keyentry.py` 校验 key 走 `build_notes._chat_json` | 同上 |
| key 读取顺序：`--api-key` → `DEEPSEEK_API_KEY` → `~/.classlive/credentials` → 旧 `.deepseek_key` | `cloud_translator.load_api_key` |
| 客户端已有持久 `install-id`，每次定稿请求带 `X-ClassLive-Install` | `final_asr.install_id`、`paths.py` |
| Worker 等上游的时间不计 CPU（免费档 10 ms） | developers.cloudflare.com/workers/platform/limits（「Waiting on network requests … does not count toward CPU time」） |
| DeepSeek 流式加 `stream_options.include_usage` 时最后一块带 usage（含 `prompt_cache_hit_tokens`） | api-docs.deepseek.com；`CloudTranslator(collect_usage=True)` 已实现读取 |
| **一节课 DeepSeek 花多少钱：未测** | sessions 报告里没有 token 字段 → §5 第 0 步先量 |

## 2. 结构（先收口，再接网关）

1. **收口**：新建 `llm_client.py`，唯一的 `chat(messages, *, stream, max_tokens, timeout) -> …` 出口 + 共享 `httpx.Client`
   （分阶段超时沿用 `cloud_translator.CONNECT_TIMEOUT_S / READ_TIMEOUT_S`）。5 处改成调它；行为逐字不变，先单独提交。
2. **路由**：`llm_client.base_url(key)`：`sk-…` → `https://api.deepseek.com`；`cl-…` → `https://asr.bldcam.page`（Worker）。
   请求头带 `Authorization: Bearer <码>`、`X-ClassLive-Install`、`X-ClassLive-Version`。
3. **定稿转写**：有通行码时 `final_asr` 用通行码代替内置公开令牌（Worker 据此记到人名下）。
4. **Worker** 新增：
   - `POST /v1/chat/completions`：验码 → 查当日额度 → 换成 `DEEPSEEK_KEY` 转发 → `new Response(upstream.body)` 流式透传；
     只放行 `deepseek-chat`（或白名单内模型）、`max_tokens` 封顶；流末尾读 usage 记账。
   - 通行码表放进现有 Durable Object 的 SQLite：`code, name, created, expires, revoked, max_devices, daily_asr_s, daily_tokens, flags`；
     设备表 `code, install_id, first_seen, last_seen`；用量表按天 `code, day, asr_s, asr_reqs, groq/qwen 计数, tokens_in, tokens_out, cache_hit`。
   - 管理接口 `/v1/admin/*` 只认 `ADMIN_TOKEN`（wrangler secret），Owen 本机跑 `scripts/cl_admin.py add|list|revoke|stats|limit`。
5. **超额 / 吊销**：Worker 回 `429 quota_exceeded` / `403 revoked`；客户端当作「云端不可用」走现有降级（定稿 → 本地；翻译 → 本地或只保留转录），
   菜单栏图标给一句人话（走 `notify(src="翻译"|"转写")`，已有）。

## 3. 可扩展的功能（Owen 说「可以扩展」—— 按价值排序，先做前 3 个）

1. **按人看用量**：`cl_admin.py stats` 一张表（人、设备数、今日课时 / 段数 / token / 缓存命中 / 估算花费、最后在线、版本）。只记数字，不记文本与音频。
2. **远程提示**：Worker 响应头 `X-ClassLive-Notice`（如「新版 3.10 已发布」「某旧版有 bug，请更新」）→ 客户端在菜单栏 / 下次开面板时显示一次。不用挨个微信催。
3. **最低版本闸**：码表或全局配置写 `min_version`，低于它的客户端拿到 `426` + 提示更新（只拦云端，本地照用）。
4. 面板里显示「今日已用 / 额度」一行（从响应头 `X-ClassLive-Quota` 读，不加请求）。
5. 有效期 / 续期：码带 `expires`，过期回 `403 expired`，Owen 一行命令续。
6. 按人开关功能（`flags`）：例如只给某人开课后精修，或关掉问答。
7. 开课预检：点「开始上课」后台发一次 `GET /v1/ping`（带码）→ 连不上 / 码失效 / 今日额度用完，开讲前就提示，不等第一句。

## 4. 隐私与告知（必须）

- 通行码模式下**课堂文字会经过 Owen 的 Worker**（转发给 DeepSeek）。告知文案要改：填码时弹一次说明，`docs/` 隐私段落同步。
- 用量统计**明示**，不做「静默收集」：填码时的说明里写清记了哪些数字。
- Worker 日志不记文本、音频、完整码（只记码的名字段）。

## 5. 实施顺序（每步可单独提交、单独验）

0. **先量钱**：用 `collect_usage=True` 跑一节课回放，记 token 入 / 出 / 缓存命中 → 定默认 `daily_tokens`。
1. `llm_client.py` 收口（无行为变化；现有测试全绿 + 新测试：5 处都走它）。
2. Worker：码表 + `/v1/chat/completions` 透传 + 记账 + admin 接口（`test.mjs` 覆盖：验码、吊销、超额、设备上限、usage 记账、只放行白名单模型）。
3. 客户端路由 + 定稿用码 + 429/403 降级与提示。
4. `scripts/cl_admin.py`。
5. 扩展功能 1–3。

## 6. Owen 已拍板（2026-10-11）

1. **没有码的人照旧能用免 key 的云端转写**（内置公开令牌；翻译自己填 `sk-`）。
2. **每人每天默认不限额**；但码表里**留着** `daily_asr_s` / `daily_tokens` 字段（空 = 不限），以后能按人收紧，不用改结构。
3. 设备数：未定 → 默认 2（笔记本 + 台式），可按人改。
4. **有效期一学期 = 4 个月**（`expires` = 发码日 + 120 天），过期回 `403 expired`，`cl_admin.py renew` 续。
