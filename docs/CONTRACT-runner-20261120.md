# 网页版笔记 + 实时总结：runner 契约（2026-11-20，Claude 定稿，W22 / W23 / W24 共用）

三张任务单都按这份写。**这份文件只有 Claude 能改**；发现写错 / 不够用，写进交回的「计划外问题」，不要自己改契约、不要绕开它。

## 0. 一句话架构

```
浏览器 ──/v1/web/*（通行码）──> Worker ──> 每码一个 DO `WebSessions`（账本）+ R2 `WEB_DATA`（逐句、产物）
                                   │  开课 / 下课 / 删数据时顺手通知
                                   ▼
                         全局单实例 DO `RunnerHub`（任务队列 + 正在上的课）
                                   ▲
VPS 上的 Python runner ──/v1/runner/*（RUNNER_TOKEN）──┘
   ├─ 笔记：领任务 → 拉逐句 → 写成桌面版 sessions/*.md → 调桌面现成管道出笔记 → 交回 md/html
   └─ 实时总结：每 20 s 看一眼正在上的课 → 拉新句子 → 喂桌面 live_summary（关掉逐段刷新）→ 写回 summary.json
runner 调模型：走 Worker 的 /v1/chat/completions、/v1/jev，用专用通行码 `runner`（Owen 付钱），
              每个请求带 `X-ClassLive-Bill-To: <hash16>`（今天只记账，日后可改成扣用户自己的额度）。
```

## 1. 鉴权与通用

- runner 路由：`Authorization: Bearer <RUNNER_TOKEN>`（Worker secret，形如 `rn_<32 hex>`）。secret 没配 → 一律 401（fail-closed，同 `server/upload-worker/src/index.js` 那段）。
- runner 路由不吃通行码、不进 `passGate`、不过 CORS（浏览器永远不会调）。
- 错误体一律 `{"error": "<code>", "detail"?: "..."}`；成功无内容回 204。
- `hash` = 通行码的 `codeHashHex(code, 16)`（就是 `WebSessions` 的 idFromName 那个值，R2 键里的 `web/<hash>/…`）。
- 时间一律 ISO 字符串（UTC）。

## 2. 全局 DO `RunnerHub`（W22 实现）

单实例：`env.RUNNER_HUB.get(env.RUNNER_HUB.idFromName("hub"))`。新 DO 类要在 `wrangler.toml` 加 binding + 新 migration tag（`new_sqlite_classes`，照现有写法）。

存储：
- `job:<job_id>` → `{job_id, kind:"note", hash, session_id, course, tz, started_at, ended_at, lines_total, level, state, attempts, lease, lease_until, runner, reason, objects, created, updated}`；`state ∈ queued|running|done|failed`。
- `live:<hash>:<session_id>` → `{hash, session_id, course, tz, started_at, last_line_at, high_water}`。

谁写它（都在 `handler.js` 的 webRoute 里，**DO 调用成功之后**才通知 hub）：
| 时机 | 调 hub | 阻塞吗 |
|---|---|---|
| `POST /v1/web/session` 开课成功（新建，不是 dedupe） | `register` 加 `live:` | 不阻塞：`ctx.waitUntil` |
| `PUT …/lines` 成功 | `touch`（更新 `last_line_at`、`high_water`） | 不阻塞：`ctx.waitUntil` |
| `POST …/end` 成功且**新建**了任务（202） | `enqueue` 建 `job:`，并删 `live:` | **await**，最多 2 s；失败只打日志，照常回 202（下面的 cron 会补） |
| `DELETE /v1/web/data` | `purge(hash)`：删该 hash 的 `live:` 和**未完成**的 `job:` | await，失败只打日志 |

补漏：Worker 现有的 cron（`schedule 17 * * * *`）里加一步「`live:` 超过 3 h 没有 `last_line_at` 的删掉」。（`/end` 入队丢了的补救不做，记进计划外。）

状态机（与 `docs/API-web-v0-20261117.md` §5 一致）：租约 90 s、心跳 30 s、`attempts` 上限 3；`claim` 时把租约过期的 `running` 先按 §5 规则回收（`attempts<3` → `queued`，否则 `failed`）。

**任务状态同步回每码的 DO**：`complete` / 变 `failed` 时，hub 调对应 `WebSessions` 实例的新内部路由 `POST /job_update {session_id, job_id, status, attempt, objects?, reason?}`，让 `GET /v1/web/session/:id` 里的 `note.state` 跟着变（现有字段 `status`，取值同上）。

## 3. runner 路由（W22 实现，W23 / W24 调）

| 方法 + 路径 | 请求 | 回 |
|---|---|---|
| `POST /v1/runner/claim` | `{runner: string, kinds: ["note"]}` | `200 {job_id, kind, attempt, lease, lease_until, input:{hash, session_id, course, tz, started_at, ended_at, lines_total, level}}`；没活 `204` |
| `POST /v1/runner/heartbeat` | `{job_id, attempt, lease}` | `200 {lease_until}`；`404 not_found` / `409 lease_mismatch` / `409 stale_attempt` / `410 session_deleted` |
| `POST /v1/runner/complete` | 成功：`{job_id, attempt, lease, ok:true, note:{md, html}, meta?:{...}}`；失败：`{job_id, attempt, lease, ok:false, reason}` | 成功 `200 {job_id, state:"done", attempt, objects:[...]}`；失败 `200 {job_id, state:"queued"|"failed", attempt}`；body > 8 MiB → `413`；拒收规则同 API 文档 §4.3 |
| `GET /v1/runner/lines?hash=&session_id=&after=<seq>` | — | `200` NDJSON：该课所有 final 行里 `seq > after` 的，按 seq 升序，每行 `{seq, t, en, zh, src}`（就是 `lines-<seq>.json` 批里的行原样）；课不存在 `404`；已删 `410` |
| `GET /v1/runner/live` | — | `200 {sessions:[{hash, session_id, course, tz, started_at, last_line_at, high_water}]}`（只回 `last_line_at` 在 3 h 内的） |
| `PUT /v1/runner/summary?hash=&session_id=` | JSON（§5 形状），≤ 1 MiB | `204`；写 R2 `web/<hash>/sessions/<session_id>/summary.json` |

`complete` 成功写的 R2 对象：`web/<hash>/sessions/<session_id>/note.md`、`note.html`、`note.meta.json`（meta 原样 + `attempt`、`finished_at`）。

## 4. 浏览器读产物（W22 实现；界面 Claude 接）

走现有 webRoute（通行码 + Origin + install + 查码，同 `GET /v1/web/session/:id`）：
- `GET /v1/web/session/:id/note?fmt=html|md`（默认 html）→ `200` 原文（content-type `text/html; charset=utf-8` / `text/markdown; charset=utf-8`）；还没出 → `404 {"error":"not_ready"}`。
- `GET /v1/web/session/:id/summary` → `200` summary.json 原文；还没有 → `404 {"error":"not_ready"}`。

## 5. summary.json 形状（W24 写、浏览器读）

```json
{ "session_id": "s_…", "updated_at": "2026-11-20T10:00:00Z", "final": false,
  "chapters": [ { "idx": 1, "title": "需求的价格弹性", "start_seq": 1, "end_seq": 48, "closed": true,
                  "summary": "（中文首段 / 封章总结正文）", "points": ["…"] } ] }
```
字段名就这几个；`points` 可为空数组；`closed=false` 的是正在讲的那一章（只有开章首段）。`final=true` = 下课后的最后一版。

## 6. 计费接口（W22 实现「只记账」，W23 / W24 发头）

- runner 调模型用专用通行码 `runner`（Owen 会用 `cl_admin.py add runner` 发，不限额）。码放在 runner 的隔离 HOME 里，按桌面版读码的那套路径（W23 查清楚写进 README）。
- 每个模型请求加头 `X-ClassLive-Bill-To: <hash16>`。Worker（W22）：只在**码名 = env `RUNNER_PASS_NAME`（默认 "runner"）**时认这个头，形状 16 hex 才收，记进 AE 事件（新 blob，W22 自己选位置并写进交回）。**今天不扣任何人的额度**；日后「转给用户付费」= 在这里改成按 bill_to 查那枚码的额度，接口不用变。

## 7. runner 进程（Python，VPS，目录 `server/runner/`）

- 跑在 Linux（VPS，`ssh bldcam`），Python 3.11+，依赖只许 `httpx`（+ 桌面笔记管道本身要的）。**不许 import AppKit / PyObjC / mlx / 本地 ASR 模型**；桌面 `.py` 文件一律**不改**，在仓库根目录 import 它们（runner 在本仓库里跑，`sys.path` 加仓库根）。
- 配置全走环境变量：`RUNNER_BASE`（默认 `https://asr.bldcam.page`）、`RUNNER_TOKEN`、`RUNNER_NAME`（默认主机名）、`RUNNER_HOME`（隔离的桌面 HOME，里面放 runner 码）。
- 文件归属（**互不重叠**）：
  - W23：`server/runner/client.py`、`loop.py`、`note_job.py`、`session_md.py`、`README.md`、`classlive-runner.service`、`tests/test_client.py`、`tests/test_note_*.py`、`tests/fakes.py`
  - W24：`server/runner/summary_job.py`、`tests/test_summary_*.py`（需要假 client 就在自己测试文件里写，不改 `tests/fakes.py`）
- `client.py`（W23 写，W24 照签名用，**签名就这些**）：
  ```python
  class RunnerClient:
      def __init__(self, base: str, token: str, *, timeout: float = 30.0, http=None): ...
      def claim(self, kinds=("note",)) -> dict | None
      def heartbeat(self, job: dict) -> str            # 回 lease_until；409/410 抛 LeaseLost
      def complete_ok(self, job: dict, md: str, html: str, meta: dict) -> dict
      def complete_fail(self, job: dict, reason: str) -> dict
      def lines(self, hash: str, session_id: str, after: int = 0) -> list[dict]   # 404/410 抛 SessionGone
      def live(self) -> list[dict]
      def put_summary(self, hash: str, session_id: str, doc: dict) -> None
  class LeaseLost(Exception): ...
  class SessionGone(Exception): ...
  ```
- `loop.py`（W23）：单进程主循环。每 5 s 试领一条笔记任务（同时最多 1 条，在线程里跑，主线程每 30 s 心跳；`LeaseLost` → 立刻停手不交）；每 20 s 调一次 `summary_job.tick(client, state, now)`（`import summary_job` 失败就跳过这一支）。`tick` 必须在 15 s 内返回（W24 保证：慢活放它自己的线程）。SIGTERM 优雅退出。
- `summary_job.tick(client, state: dict, now: float) -> None`（W24）：`state` 是 loop 给的一个跨 tick 的空 dict，W24 自己往里放东西；永不抛（内部吞掉、打日志）。

## 8. 不做的事

- 课件 → 术语（`kinds:["terms"]`）不做，只留 `kinds` 参数。
- 不部署、不 `wrangler` 远程命令、不 `ssh bldcam`、不调真模型（Claude 验收时自己做）。
