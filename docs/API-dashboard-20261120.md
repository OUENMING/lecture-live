# 看板（管理面板）接口文档 —— 给重做前端的 AI（2026-11-20）

你要做的：**只重写前端** `scripts/cl_admin_ui.html`（单文件，现有 ~3000 行可整个替换）。后端 `scripts/cl_admin_ui.py` 不改；它已经把下面这些接口都备好了。看不懂某个数字的口径，读 `cl_admin_ui.py` 里同名函数的 docstring（本文每节标了函数名）。

## 0. 硬约束（违反任何一条 = 不收）

1. **单文件 HTML**：CSS / JS 全内联，不引外部 CDN、不 `npm`、不打包。后端用 `PAGE.read_bytes()` 原样发它。
2. **每个请求**：`POST /api/<名字>`，body 是 JSON 对象，头必须带 `X-ClassLive-Admin: 1` 和 `content-type: application/json`。少了头后端回 403（这是防别的网站 CSRF 的闸，不许去掉）。现成写法：
   ```js
   function api(name, body){
     return fetch("/api/" + name, {method:"POST",
       headers:{"content-type":"application/json","X-ClassLive-Admin":"1"},
       body: JSON.stringify(body || {})})
     .then(r => r.json().then(j => { if(!r.ok) throw new Error(j.error || ("HTTP " + r.status)); return j; }));
   }
   ```
3. **页面里永远没有令牌**。完整通行码（`cl-<名>-<16hex>`）会出现在 `list` 的 `code` 字段里 —— 默认**遮住**（显示 `cl-6789-••••`），点了才露全，复制按钮可以有。
4. 只在 `http://127.0.0.1:8770/` 打开（后端只 bind 环回，Host 不是环回回 403）。
5. **任何一张卡报错不许拖垮整页**：每个接口独立加载、独立显示错误（回包里有 `error` 字段 = 这张卡显示原因，别的照常）。
6. 写操作（`revoke` / `delete` / `limit` …）要二次确认；`delete` 要让人输码名确认。
7. 时间显示用浏览器本地时区；接口里的时刻是 **UTC ISO** 或 **毫秒时间戳**（各节标明）。
8. 深浅色都要好看（`prefers-color-scheme`）。窄屏（手机宽）能看，不横向滚动。

## 1. 缓存规则（「慢接口」）

下面标 **[慢]** 的接口后端有缓存：55 s 内直接给；15 min 内先给旧的、后台重取；更旧就同步等（可能 10–60 s）。
- 回包都带 `cache_age`（秒，数字）—— 卡片角上显示「x 分钟前」。
- body 传 `{"fresh": true}` = 强制重取（给每张卡一个刷新按钮）。
- 首屏建议：先并发发所有慢接口（不带 fresh），各自回来各自渲染。

## 2. 接口清单

| 名字 | 慢? | body | 管什么 | 后端函数 |
|---|---|---|---|---|
| `list` | | `{}` | 所有通行码 + 设备数 + 今日用量 | Worker `/v1/admin/list` |
| `stats` | | `{days?:7}` 或 `{day}` 或 `{from,to}` | 每天 × 每码的用量 | Worker `/v1/admin/stats` |
| `quota` | | `{}` | 共享额度（Groq / 千问）现状 | Worker `/v1/admin/quota` |
| `minutes` | | `{code, day?}` | 某码某天逐分钟用量 | Worker `/v1/admin/minutes` |
| `devices` | | `{code}` | 某码的桌面 / 网页设备 | Worker `/v1/admin/devices` |
| `add` | | `{name, days?, max_devices?}` | 发新码 | |
| `revoke` / `unrevoke` | | `{code}`（或 `{name}`，同名多条要给 code） | 吊销 / 恢复 | |
| `renew` | | `{code, days}` | 续期（从 max(到期, 现在) 起加） | |
| `limit` | | `{code, max_devices?, daily_asr_s?, daily_tokens?, daily_rt_s?, daily_web_rt_s?}` | 改上限。`daily_asr_s/tokens/rt_s`：`null` = 不限；`daily_web_rt_s`：`null` = 跟随全局、`"none"` = 不限；`max_devices` 只收正整数（默认 2，桌面 + 网页合计） | |
| `delete` | | `{code, yes:true}` | 删码（不可逆） | |
| `cf` | 慢 | `{fresh?}` | Cloudflare：近 24 h Worker 请求/错误/CPU，近 7 天请求/R2/DO | `cf_summary` |
| `health` | 慢 | `{fresh?}` | 每小时延迟、部署版本、上游状态码、节点、站点 | `cf_health` |
| `cfwin` | | `{s, u}`（`2026-10-14T17:00:00Z` 形） | 任一时段的请求数 + 延迟 | `cf_window` |
| `logs` | 慢 | `{fresh?}` | 各 ASR 引擎耗时分布 + 最近的拒绝事件 | `worker_logs` |
| `ae` | 慢 | `{fresh?}` | 近 24 h 逐请求汇总：码 × 路径、地区、≥400 的请求、上游、速度 | `ae_events` |
| `ae310` | 慢 | `{fresh?}` | 近 7 天：Soniox 签发、翻译快速通道、版本普及、笔记 token | `ae_310` |
| `habits` | 慢 | `{fresh?}` | 近 30 天每人上课习惯（时段、星期、时区、版本） | `habits` |
| `r2` | 慢 | `{fresh?}` | 桌面测试模式上传桶清单（按会话） | `r2_ingest` |
| `reports` | 慢 | `{fresh?}` | 桌面测试课 `report.json` 摘要 | `r2_reports` |
| `raw` | | `{id, what:"report.json"|"transcript.md"}` | 桌面某节课原文 | `r2_raw` |
| `soniox` | 慢 | `{fresh?}` | Soniox 花费 / 并发 / 余额 | `soniox_account` |
| `openrouter` | 慢 | `{fresh?}` | OpenRouter 余额 + 近 7 天按模型用量 | `openrouter_account` |
| **`web`** 🆕 | 慢 | `{fresh?}` | **网页版**近 2 天每节课一行（构建号/浏览器/下课总账/异常计数） | `web_sessions` |
| **`websession`** 🆕 | | `{name, session}` | 网页版一节课的全部事件（排障时间线） | `cl_web_data.session_detail` |
| **`runner`** 🆕 | 慢 | `{fresh?}` | 网页版笔记 / 实时总结任务队列 + 正在上的课 | `runner_state` |

GET 路由（不是 API，直接 `<a target=_blank>` / `<iframe>`）：
- `GET /note/<桶会话id>` → 桌面测试课的交互版笔记（已 sandbox）。

## 3. 关键字段口径（最容易做错的）

### `list.codes[]`
- `created` / `expires` / `last_seen` / `web_last_seen`：**毫秒时间戳**。`expires < now` = 已过期。
- `devices` / `web_devices`：已绑定的**桌面 / 网页**设备数（分开存、分开显示）。名额 `max_devices`（默认 2）是**两者合计**（Owen 11-20 定）。显示成「2/2 台（桌面 1 · 网页 1）」，`devices + web_devices ≥ max_devices` 标黄。老码记录里可能还留着 `max_web_devices` 字段 —— 已作废，忽略。
- `daily_*`：`null` = 不限。
- `rt_s` / `rt_web_s`：今天实时识别秒数（总 / 其中网页）。
- `name` 是码名（人）；同名可能有多条（重发过）—— 以 `code` 为主键。

### `stats.days[].rows[]`
- 每行 = 某天某码。`asr_s` 定稿识别秒、`rt_s` 实时识别秒、`rt_web_s` 网页那部分、`tokens_in/out` 翻译+笔记 token、`cache_hit` 前缀缓存命中 token、`jev_*` Jev 请求、`vers` 客户端版本 → 次数、`active_min` 有请求的分钟数。

### `minutes.mins`
- 键 = **UTC 当日第几分钟**（"0"–"1439"），值 = `[asr次数, asr秒, llm次数, token, jev次数]`。画成一天的条带。

### `devices`
- `devices[]` 桌面、`web_devices[]` 网页；`id` 只有 install id 前 6 位；`first` / `last` 毫秒。
- 🆕 `info`（可能没有：11-20 之前绑的、或这台之后还没用过）：`{os, br, mobile, ver, country}` —— 例 `{"os":"iOS","br":"Safari 18","mobile":true,"ver":"","country":"IE"}`；桌面 app 是 `{"br":"桌面 app","ver":"3.12.2",…}`。显示成「iPhone · Safari 18 · IE」。是**最近一次**看到的样子，来自请求头（可伪造，只当提示）。

### `web.sessions[]`（🆕 网页版排障主表）
- 一行一节网页课；`name` 码名、`session` 本机会话号（`web-YYYYMMDD-HHMMSS-xxxxxx`）。
- `first_t` / `last_t` 毫秒；`build` 网页构建号（`20261120T0319-<git>` 形，`+dirty` = 未提交代码构建）；`ver`；`ua` / `tz` / `net` / `mobile` 环境。
- `report` = 下课总账（**没正常下课就是 `null`** —— 这本身是信号：关页 / 崩了）：
  `dur_s` 课长、`sentences` 句数、`sent_failed` 没出中文的句、`total_p50/p90` 封口→中文延迟（ms）、
  `final{n,ok,retry,err,first_p50,first_p90,wait_p90}` 定稿翻译、`draft{n,ok,stale,aborted,err}` 草稿翻译、
  `api{n,fail,by}` 所有接口请求（`by` = `"状态码:错误码" → 次数`）、`console{error,warn}`、
  `ws{opened,closed,errors,sockets}` 识别连接、`hidden_s` 页面在后台秒数、`offline_s` 断网秒数、
  `marks` ❓标记数、`sync` 存服务器结果。
- `bad` 异常事件数、`bad_kinds` 按类型计数。**一行的颜色建议**：`report=null` 或 `bad>0` 或 `sent_failed>0` 标黄/红。
- 点一行 → 调 `websession {name, session}` 出时间线。

### `websession`
- `events[]`：按时间排序的全部事件，每条 `{t(毫秒), mono, n, type, ...}`。常见 `type`：
  `env`（设备环境）、`class_open` / `class_end` / `class_report`、`zh`（逐句：`seal_ms` 封口、`zh_ms` 首字、`total_ms`）、
  `tr`（逐次翻译请求：`kind` final|draft、`outcome` ok|stale|empty|echo|error|aborted、`ms`、`status`、`err`）、
  `api`（`path`、`status`、`ok`、`ms`）、`ws_new` / `ws_open` / `ws_close` / `ws_error`、`status`、`sync`、`perf`、`pulse`、
  `rec_start` / `rec_stop` / `rec_upload_failed`、`error` / `rejection` / `console`、`offline`。
- `bad[]` = 异常子集（`error rejection console ws_error ws_close key_failed notice rec_upload_failed collector_error offline dropped`，外加 `api.ok=false`、`tr.outcome≠ok`）。
- `truncated: true` = 超过 2000 条只给了前 2000。
- 建议界面：横向时间轴（课开始 = 0），`zh.total_ms` 画成散点（延迟曲线），异常画成红色竖线，hover 看原事件 JSON。

### `runner`（🆕）
- `counts{queued,running,done,failed}`；`jobs[]` 最近 100 条（新→旧）：`job_id kind course state attempts runner reason lines_total created updated ended_at lease_until`（时刻是 **UTC ISO**）。`state=failed` 标红并显示 `reason`；`running` 且 `lease_until` 已过 = 卡住了。
- `live[]`：正在上的网页课 `{hash, session_id, course, last_line_at, high_water, stale}`；`stale=true` = 3 h 没新句子。
- `hash` 是码的哈希，**对不上码名**（设计如此）；课名 `course` 就够认人。

### `health`
- `lat[]` 每小时 `wall_p50/p95`（ms）；`versions[]` 部署版本（`first`/`last` 出现时刻、`deployed_at`）—— 画在延迟图上当竖线；`upstream[]` 各上游 `ok / c4xx / c429 / c5xx`；`colos[]` Cloudflare 节点（≠ 用户所在地，界面上写明）。

### `ae` / `ae310`
- 字段里 `n` / `errors` / `users` 是**字符串形式的数字**（AE SQL 返回的），前端 `Number()` 一下。
- `ae.bad[]` 最近 30 条 ≥400 请求；`ae.speed[]` 每天 × 码 × 种类的识别速度。

## 4. 信息架构建议（可改，但这几件事要一眼看到）

1. 顶栏：今天在线的人 / 正在上的课（`runner.live` + `list.last_seen` 近 5 分钟）、今日总花费（`soniox.usage.*.cost_today` + `openrouter`）、Worker 错误率（`cf.scripts.classlive-asr.errors/requests`）。
2. 「人」视图：每个码一张卡（`list` + `stats` + `habits` + `devices`），桌面 / 网页分开。
3. 「网页课」视图：`web.sessions` 表 + `websession` 时间线（**这是这次重做最重要的新东西**）。
4. 「笔记队列」：`runner`。
5. 「平台」：`cf` / `health` / `ae` / `ae310` / `logs` / `soniox` / `openrouter` / `quota`。
6. 「桌面测试课」：`r2` / `reports` / `raw` / `/note/`。

## 5. 怎么自测

```
cd ~/lecture-live-web
~/lecture-live/ClassLive.app/Contents/MacOS/python scripts/cl_admin_ui.py --port 8771   # 另开端口，别抢 8770
# 浏览器开 http://127.0.0.1:8771/
~/lecture-live/ClassLive.app/Contents/MacOS/python -m unittest tests.test_cl_admin_ui    # 后端判据（不联网）
```
- 只读接口随便点；**写操作只对 `test1` 码试**，别动别人的码。
- 交付前：深 / 浅色各截一张、窄屏（390 px）截一张；每张卡断网时都显示错误而不是白屏。

## 附录：每个接口的回包形状（2026-11-20 线上实抓，值替换成类型；`[x]` = 数组元素形状）

```json
{
 "list": {
  "codes": [
   {
    "code": "string",
    "name": "string",
    "created": "number",
    "expires": "number",
    "revoked": "boolean",
    "max_devices": "number",
    "daily_asr_s": "null",
    "daily_tokens": "null",
    "daily_rt_s": "null",
    "devices": "number",
    "last_seen": "number",
    "web_devices": "number",
    "web_last_seen": "number",
    "rt_web_s": "number",
    "rt_s": "number"
   }
  ]
 },
 "stats": {
  "days": [
   {
    "day": "string",
    "rows": [
     {
      "name": "string",
      "code": "string",
      "asr_s": "number",
      "asr_reqs": "number",
      "llm_reqs": "number",
      "tokens_in": "number",
      "tokens_out": "number",
      "cache_hit": "number",
      "jev_reqs": "number",
      "jev_q": "number",
      "rt_s": "number",
      "rt_reqs": "number",
      "models": {},
      "vers": {
       "3.11.3": "number"
      },
      "rt_web_s": "number",
      "active_min": "number"
     }
    ]
   }
  ],
  "from": "string",
  "to": "string"
 },
 "quota": {
  "day": "string",
  "now": "number",
  "caps": {
   "reqs_day": "number",
   "reqs_min": "number",
   "s_hour": "number",
   "s_day": "number",
   "qwen_day": "number"
  },
  "v3": {
   "used_reqs": "number",
   "used_s": "number",
   "min_reqs": "number",
   "hour_s": "number",
   "cooldown_until": "number",
   "groq": {
    "req_left": "number",
    "req_lim": "number",
    "aud_left": "number",
    "aud_lim": "number",
    "reported_at": "number"
   }
  },
  "turbo": {
   "used_reqs": "number",
   "used_s": "number",
   "min_reqs": "number",
   "hour_s": "number",
   "cooldown_until": "number",
   "groq": {
    "req_left": "number",
    "req_lim": "number",
    "aud_left": "number",
    "aud_lim": "number",
    "reported_at": "number"
   }
  },
  "qwen": {
   "used": "number",
   "max": "number",
   "exhausted": [
    "string"
   ],
   "cooldown_until": "number"
  },
  "groq_blocked": {}
 },
 "minutes": {
  "code": "string",
  "day": "string",
  "mins": {
   "<UTC 当日第几分钟>": [
    "number"
   ]
  }
 },
 "devices": {
  "code": "string",
  "devices": [
   {
    "id": "string",
    "first": "number",
    "last": "number"
   }
  ],
  "web_devices": [
   {
    "id": "string",
    "first": "number",
    "last": "number"
   }
  ]
 },
 "cf": {
  "since": "string",
  "until": "string",
  "scripts": {
   "classlive-asr": {
    "requests": "number",
    "errors": "number",
    "status": {
     "success": "number",
     "clientDisconnected": "number",
     "scriptThrewException": "number"
    },
    "cpu_p50_ms": "number",
    "cpu_p99_ms": "number"
   },
   "classlive-upload": {
    "requests": "number",
    "errors": "number",
    "status": {
     "success": "number"
    },
    "cpu_p50_ms": "number",
    "cpu_p99_ms": "number"
   }
  },
  "week": {
   "<YYYY-MM-DD>": {
    "<脚本名>": "number"
   }
  },
  "r2": {
   "<桶名>": {
    "<YYYY-MM-DD>": {
     "objects": "number",
     "bytes": "number"
    }
   }
  },
  "dos": {
   "<YYYY-MM-DD>": "number"
  },
  "minutes": {
   "classlive-asr": {
    "<299 个键，如 '2026-11-19T06:36:00Z'>": [
     "number"
    ]
   },
   "classlive-upload": {
    "2026-11-19T12:23:00Z": [
     "number"
    ],
    "2026-11-19T12:13:00Z": [
     "number"
    ],
    "2026-11-20T01:30:00Z": [
     "number"
    ],
    "2026-11-19T21:04:00Z": [
     "number"
    ],
    "2026-11-19T07:55:00Z": [
     "number"
    ]
   }
  },
  "cache_age": "number"
 },
 "health": {
  "since": "string",
  "until": "string",
  "overall": {
   "requests": "number",
   "errors": "number",
   "wall_p50": "number",
   "wall_p95": "number"
  },
  "lat": [
   {
    "hour": "string",
    "requests": "number",
    "errors": "number",
    "wall_p50": "number",
    "wall_p95": "number",
    "dur_p50": "number",
    "dur_p95": "number"
   }
  ],
  "versions": [
   {
    "id": "string",
    "first": "string",
    "last": "string",
    "requests": "number",
    "deployed_at": "string",
    "before": "boolean"
   }
  ],
  "upstream": [
   {
    "host": "string",
    "name": "string",
    "total": "number",
    "ok": "number",
    "c4xx": "number",
    "c429": "number",
    "c5xx": "number",
    "unfinished": "number"
   }
  ],
  "colos": [
   {
    "colo": "string",
    "requests": "number",
    "wall_p50": "number"
   }
  ],
  "zone": {
   "hosts": [
    {
     "host": "string",
     "total": "number",
     "s2": "number",
     "s3": "number",
     "s4": "number",
     "s5": "number",
     "countries": [
      [
       "string"
      ]
     ]
    }
   ]
  },
  "zone_error": "string",
  "deploys_error": "string",
  "cache_age": "number"
 },
 "logs": {
  "models": [
   {
    "model": "string",
    "count": "number",
    "median": "number",
    "p90": "number",
    "max": "number"
   }
  ],
  "events": [
   {
    "pass": "string",
    "denied": "string",
    "kind": "string",
    "t": "number"
   }
  ],
  "cache_age": "number"
 },
 "ae": {
  "by_code": [
   {
    "name": "string",
    "path": "string",
    "n": "string",
    "p50": "number",
    "p95": "number",
    "errors": "string",
    "audio_s": "number",
    "tokens": "number",
    "last": "string"
   }
  ],
  "geo": [
   {
    "name": "string",
    "country": "string",
    "colo": "string",
    "ver": "string",
    "n": "string",
    "p50": "number"
   }
  ],
  "bad": [
   {
    "t": "string",
    "name": "string",
    "path": "string",
    "model": "string",
    "up": "string",
    "status": "number",
    "ms": "number"
   }
  ],
  "up": [
   {
    "name": "string",
    "path": "string",
    "up": "string",
    "status": "number",
    "n": "string",
    "first": "string",
    "last": "string"
   }
  ],
  "speed": [
   {
    "day": "string",
    "name": "string",
    "kind": "string",
    "n": "string",
    "p50": "number",
    "p95": "number",
    "first50": "number",
    "done50": "number",
    "done95": "number",
    "words": "number",
    "audio_s": "number",
    "tout": "number"
   }
  ],
  "cache_age": "number"
 },
 "ae310": {
  "soniox": [
   {
    "up": "string",
    "name": "string",
    "status": "number",
    "n": "string",
    "first": "string",
    "last": "string"
   }
  ],
  "fast": [
   {
    "up": "string",
    "status": "number",
    "n": "string",
    "f50": "number",
    "f95": "number",
    "ms50": "number"
   }
  ],
  "rollout": [
   {
    "ver": "string",
    "n": "string",
    "users": "string"
   }
  ],
  "notes": [
   {
    "name": "string",
    "n": "string",
    "tin": "number",
    "tout": "number",
    "hit": "number",
    "max_tok": "number"
   }
  ],
  "cache_age": "number"
 },
 "reports": {
  "bucket": "string",
  "objects": "number",
  "candidates": "number",
  "sessions": [
   {
    "id": "string",
    "name": "string",
    "files": [
     "string"
    ],
    "updated": "string",
    "report_at": "string",
    "generated": "string",
    "duration_s": "number",
    "report_version": "number",
    "audio_saved": "boolean",
    "provisional": "boolean",
    "blocks": [
     "string"
    ],
    "asr": {
     "segments": "number",
     "words": "number",
     "empty_pct": "number",
     "final_empty_pct": "number",
     "fell_back_pct": "number",
     "source": {
      "groq": "number"
     }
    },
    "corr": {
     "pairs": "number",
     "changed": "number",
     "changed_pct": "number"
    },
    "tl": {
     "n": "number",
     "first_p50": "number"
    },
    "stream": {
     "engine": "string",
     "reason": "string",
     "detail": "string",
     "sealed_n": "number",
     "p50": "number",
     "p90": "number",
     "lat_n": "number",
     "terms_n": "number",
     "terms_dropped": "number",
     "reconnects": "number",
     "connect_attempts": "number",
     "connections": "number",
     "failed_sessions": "number",
     "final_why": "string",
     "down_s": "number",
     "skipped_s": "number",
     "dropped_s": "number",
     "msg_gap_max_s": "number",
     "drain_gap_max_s": "number",
     "backlog_max_s": "number",
     "model": "string",
     "model_fallback": "boolean",
     "endpoints_n": "number",
     "endpoints_between_n": "number"
    },
    "region": {
     "default": "string",
     "selected": "string",
     "switched": "boolean",
     "reason": "string",
     "cached": "boolean",
     "ms": "number",
     "timeout": "boolean",
     "regions": {
      "eu": {
       "connect_ms": "number",
       "rtt_ms": "number",
       "error": "string"
      },
      "us": {
       "connect_ms": "number",
       "rtt_ms": "number",
       "error": "string"
      },
      "jp": {
       "connect_ms": "number",
       "rtt_ms": "number",
       "error": "string"
      }
     },
     "attempts": [
      {
       "ask": "string",
       "region": "string",
       "ok": "boolean",
       "error": "string"
      }
     ]
    },
    "duty": {
     "items": "number",
     "pending": "number",
     "saved": "number",
     "ignored": "number",
     "inferred": "number"
    },
    "device": {
     "samples": "number",
     "model": "string",
     "cores_perf": "null",
     "cores_eff": "null",
     "power": "string",
     "battery_pct": "number",
     "low_power": "boolean",
     "thermal": "string",
     "net": "string",
     "lang": "string",
     "sessions_mb": "number",
     "classlive_mb": "number",
     "models_mb": "number",
     "partial": "boolean"
    },
    "energy": {
     "available": "boolean",
     "wall_s": "number",
     "cpu_s": "number",
     "energy_j": "number",
     "screen_hz": "number",
     "visible_s": "number",
     "visible_w": "number",
     "hidden_s": "number",
     "hidden_w": "number"
    },
    "capture": {
     "available": "boolean",
     "wall_s": "number",
     "audio_s": "number",
     "deficit_s": "number",
     "gaps_n": "number",
     "gaps_s": "number"
    },
    "merge": {
     "triggered": "number",
     "replaced": "number",
     "failed": "number"
    },
    "backfill": {
     "need": "null",
     "done": "null",
     "items": "number"
    },
    "system": {
     "draft_engine": "string",
     "draft_reason": "string",
     "ram_gb": "number",
     "chip": "string",
     "macos": "string",
     "arch": "string"
    },
    "ui": {
     "submit": "null",
     "hide": "number",
     "show": "number",
     "new_topic": "null",
     "hidden_s": "number",
     "overflow": "number",
     "overflow_seen": "number",
     "sq_p50": "number",
     "sq_p90": "number"
    },
    "terms": {
     "calls": "number",
     "picked": "number",
     "empty": "number"
    },
    "live": {
     "calls_total": "number",
     "failed_calls": "number",
     "chapters": "number",
     "atoms": "number",
     "chapters_attempted": "number",
     "chapters_written": "number",
     "chapters_nonempty": "number",
     "failed_atom_calls": "number",
     "failed_chapter_calls": "number",
     "finish_truncated": "boolean"
    },
    "deps": {
     "missing": [],
     "uv": "boolean",
     "stamp_ok": "boolean"
    },
    "vad": {
     "cuts": "number",
     "hard_cuts": "number",
     "dropped": "number",
     "dropped_s": "number",
     "weak_blocks": "number",
     "vad_resets": "number",
     "blind_dropped": "number",
     "sentence_cuts": "number",
     "silence_cuts": "number",
     "total_cuts": "number",
     "blind_dropped_blocks": "number"
    },
    "norm": {
     "blocks": "number",
     "capped": "number",
     "clamped": "number",
     "gain_db_max": "number"
    },
    "semantic_seal": {
     "calls": "number",
     "errors": "number",
     "semantic": "number",
     "cap": "number",
     "no_cut": "number",
     "stale": "number"
    },
    "jev": {
     "calls": "number",
     "fail": "number",
     "provider": "string",
     "error": "string"
    },
    "counts": "null",
    "review": {
     "review_total": "number",
     "review_ok": "number",
     "review_failed": "number",
     "review_generated": "boolean"
    }
   }
  ],
  "errors": [],
  "cache_age": "number"
 },
 "r2": {
  "bucket": "string",
  "objects": "number",
  "bytes": "number",
  "sessions": [
   {
    "id": "string",
    "objects": "number",
    "bytes": "number",
    "last": "string",
    "audio": "number",
    "files": [
     "string"
    ],
    "mine": "boolean",
    "name": "string"
   }
  ],
  "others": "number",
  "cache_age": "number"
 },
 "habits": {
  "people": {
   "<码名>": {
    "sessions": "number",
    "short": "number",
    "hours": "number",
    "days": "number",
    "tz": "string",
    "tz_src": "string",
    "country": "string",
    "by_hour": [
     "number"
    ],
    "by_wd": [
     "number"
    ],
    "recent": [
     [
      "number"
     ]
    ],
    "last": "number",
    "ver": "string"
   }
  },
  "latest_ver": "string",
  "since_days": "number",
  "repo_ver": "string",
  "cache_age": "number"
 },
 "soniox": {
  "regions": [
   "string"
  ],
  "usage": {
   "us": {
    "days": [
     "string"
    ],
    "cost": [
     "number"
    ],
    "cost_7d": "number",
    "cost_today": "number",
    "models": [
     {
      "model": "string",
      "cost": "number"
     }
    ]
   },
   "eu": {
    "days": [
     "string"
    ],
    "cost": [
     "number"
    ],
    "cost_7d": "number",
    "cost_today": "number",
    "models": [
     {
      "model": "string",
      "cost": "number"
     }
    ]
   }
  },
  "logs": {
   "sessions": "number",
   "audio_s": "number",
   "by_name": {
    "<码名>": {
     "sessions": "number",
     "audio_s": "number"
    }
   },
   "more": "boolean",
   "recent": [
    {
     "region": "string",
     "name": "string",
     "start_ms": "number",
     "end_ms": "number",
     "audio_s": "number",
     "cost": "number"
    }
   ]
  },
  "concurrency": {
   "current": "number",
   "limit": "number"
  },
  "concurrency_by_region": {
   "us": {
    "current": "number",
    "limit": "number"
   },
   "eu": {
    "current": "number",
    "limit": "number"
   }
  },
  "balance": {
   "available": "number",
   "free": "number",
   "used": "number",
   "exhausted": "boolean",
   "autopay": "boolean"
  },
  "balance_error": "string",
  "errors": [],
  "cache_age": "number"
 },
 "openrouter": {
  "credits": {
   "total": "number",
   "used": "number"
  },
  "meta": {
   "metrics": [
    "string"
   ],
   "dimensions": [
    "string"
   ],
   "granularities": [
    "string"
   ]
  },
  "query": {
   "metrics": [
    "string"
   ],
   "rows": [
    {
     "date__day": "string",
     "model": "string",
     "total_usage": "number",
     "request_count": "number",
     "tokens_prompt": "number",
     "tokens_completion": "number",
     "reasoning_tokens": "number"
    }
   ]
  },
  "cache_age": "number"
 },
 "raw": {
  "error": "string"
 },
 "web": {
  "days": [
   "string"
  ],
  "sessions": [
   {
    "name": "string",
    "session": "string",
    "events": "number",
    "first_t": "number",
    "last_t": "number",
    "build": "string",
    "ver": "string",
    "ua": "string",
    "tz": "string",
    "net": "string",
    "platform": "null",
    "mobile": "boolean",
    "report": {
     "dur_s": "number",
     "ver": "string",
     "build": "string",
     "sentences": "number",
     "sent_failed": "number",
     "total_p50": "number",
     "total_p90": "number",
     "final": {
      "n": "number",
      "ok": "number",
      "retry": "number",
      "err": {},
      "first_p50": "number",
      "first_p90": "number",
      "wait_p90": "number"
     },
     "draft": {
      "n": "number",
      "ok": "number",
      "stale": "number",
      "aborted": "number",
      "err": "number"
     },
     "api": {
      "n": "number",
      "fail": "number",
      "by": {}
     },
     "console": {
      "error": "number",
      "warn": "number"
     },
     "ws": {
      "opened": "number",
      "closed": "number",
      "errors": "number",
      "sockets": "number"
     },
     "hidden_s": "number",
     "offline_s": "number",
     "rows": "number",
     "latency": {
      "n": "number",
      "p50": "number",
      "p90": "number",
      "max": "number",
      "sealP50": "number",
      "zhP50": "number",
      "failed": "number"
     },
     "sync": {
      "phase": "string",
      "acked": "number",
      "message": "string",
      "server_id": "string"
     },
     "persistent": "boolean",
     "marks": [],
     "rx_at": "number",
     "country": "string",
     "colo": "string",
     "name": "string",
     "session_id": "string"
    },
    "bad": "number",
    "bad_kinds": {}
   }
  ],
  "total": "number",
  "cache_age": "number"
 },
 "runner": {
  "now": "string",
  "counts": {
   "queued": "number",
   "running": "number",
   "done": "number",
   "failed": "number"
  },
  "jobs": [
   {
    "job_id": "string",
    "kind": "string",
    "hash": "string",
    "session_id": "string",
    "course": "string",
    "tz": "string",
    "state": "string",
    "attempts": "number",
    "runner": "string",
    "reason": "null",
    "lines_total": "number",
    "level": "string",
    "created": "string",
    "updated": "string",
    "ended_at": "string",
    "lease_until": "string"
   }
  ],
  "live": [],
  "cache_age": "number"
 },
 "websession": {
  "summary": "<同 web.sessions[] 一行>",
  "bad": "<事件数组，同 events[] 元素>",
  "events": [
   {
    "t": "number",
    "mono": "number",
    "n": "number",
    "type": "string",
    "...": "string"
   }
  ],
  "truncated": "string"
 }
}
```
