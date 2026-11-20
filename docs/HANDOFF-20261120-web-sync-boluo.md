# 交接 · 2026-11-20 · 网页版 1.6% 出错 + boluo 码追踪

> 状态：**修复已写完、测试全绿，未提交、未部署**。改动在 worktree `~/lecture-live-web`（按 11-18 夜间授权：留 worktree 不提交）。
> 部署 / 提交前先问 Owen。

## 1. 结论（一句话）

- 11-19 02:00 → 11-20 02:00 UTC 云端出错 1.6%（115 / 7090）= **网页版下课瞬间并发推句，几十个 PUT 写 R2 同一个键，撞 R2 的 10058**。不是上游 ASR / DeepSeek，也**不是** DO storage（外部 AI 报告这一点说错了）。
- 出错的全是 **6789 码、IE/DUB、网页 0.0.1** —— Owen 自己的测试；没有真用户受影响。但 bug 真实，任何人网页下课都会触发。
- boluo 码只被真用过一次：01:32:26–01:33:43，网页旧包，CA/YYZ + 自报 Asia/Shanghai。**是谁待 Owen 回答。**

## 2. 证据（都现查过，可复跑）

| 说法 | 怎么核 | 结果 |
|---|---|---|
| 115 个全是 scriptThrewException | 面板 `POST /api/cf`（头 `X-ClassLive-Admin: 1`，`{"fresh":true}`） | ✅ success 6562 / clientDisconnected 19 / scriptThrewException 115（窗口随取数时刻滑动） |
| 集中在 3 分钟 | 同上 `minutes` 字段 | ✅ 01:21 = 94、01:44 = 19、00:53 = 2 |
| 异常只有一种 | Workers Logs telemetry，过滤 `$metadata.error exists` | ✅ 232 条全是 `put: Reduce your concurrent request rate for the same object. (10058)`（00:53 ×4、01:21 ×190、01:44 ×38） |
| 10058 是谁报的 | 社区帖 + pds.js 同款修复（「R2 writes per blob key … 10058」） | **R2 同一对象并发写**，不是 DO storage |
| 同一秒的其余请求 | AE：6789、`blob1='other'` | 01:21:23：200×6、202×1（/end）、409×14；01:44:38：200×8、202×1、409×4。500 的那些**不写 AE**（抛在写事件之前） |

取数用的一次性脚本在 scratchpad（会被清）：AE 查询复用 `scripts/cl_admin_ui._ae_sqls(None, [(名, SQL)])`，**必须用 `/Applications/ClassLive.app/Contents/MacOS/python` 跑**（系统 python3 缺 httpx）。

## 3. 根因

`web/src/lib/store/sync.ts` 的 `createSyncer.send()`：
- 闸是 `st.in_flight`，但它在 `await deps.store.pending()` **之后**才置 true。
- `web/src/lib/live/connect.ts` 的 `end()` 对 `session.rows` 每一行 `persist()` → `syncer.push()`；每次 push 攒够 10 句就 `send()`，几十次全从 `await pending()` 那一拍钻进去。
- 每个并发批取的是同一段 pending（`slice(0, 50)`）→ 同一个起始 seq → DO `#putBatch` 写同一个 R2 键 `web/<hash>/sessions/<sid>/lines-<起始seq>.json` → 10058 → 抛异常 → 500。
- 和 /end 并发的那部分被账本拒成 409（session_ended）。

复现：新测试在修前红，**同时在飞的批次峰值 92**（与 01:21:23 一秒 95 个吻合）。

## 4. 改了什么（6 个文件，+164 / −19）

⚠️ 这几个文件在动手前已有别人未提交的改动（`web-session.js` 等原本就是 M），`git diff` 不全是本次的。本次只动下面这些段：

| 文件 | 改动 |
|---|---|
| `web/src/lib/store/sync.ts` | `send()` 改单飞：`running` promise 在第一个 await 前同步占住；并发调用标 `dirty` 并拿同一个 promise（`flush()` 因此真的等推完）；原函数体拆成 `sendOnce()` 返回「还要不要接着发」；`push()` 改看 `running` |
| `web/tests/store/sync.test.ts` | 末尾新测试「并发 push … 也只有一个批次在飞」：pending 慢 1 ms，连推 100 次 → 断言峰值 = 1、PUT ≤ 3、pending 清空 |
| `server/asr-worker/src/web-session.js` | `#putBatch`：同键在 DO 实例内串行（`#r2q` Map）；`putRetry()` 只对 10058 退避重试 250 ms → 1 s，其余错照抛 |
| `server/asr-worker/test-web-session.mjs` | S5b：R2 桩照真规矩（同键在写时再写就抛 10058），12 个并发同批 PUT → 断言 0 个 5xx、只一个对象 |
| `server/asr-worker/src/passes-core.js` | `list()` 加 `web_devices` / `web_last_seen` / `max_web_devices`（读 `devweb:<码>`）；`devices` / `last_seen` 仍只数桌面，含义不变（`cl_admin list` 和老面板按它读） |
| `server/asr-worker/test-web-quota.mjs` | Q11：只用网页领过 key 的码，list 里 `devices=0`、`web_devices=1`、`web_last_seen>0` |

验证（本次跑过）：
- `cd web && node --import tsx --test tests/store/*.test.ts tests/live/*.test.ts tests/api/*.test.ts` → 96 pass / 0 fail
- `cd web && npm run check` → 0 errors / 0 warnings
- `cd server/asr-worker && npm test` → 298 pass / 0 fail
- 两条新测试在修前均为红（峰值 92；11/12 个 599）

## 5. boluo 码追踪

码 `cl-boluo-03e2…`，**2026-11-20 00:39:48 UTC** 创建（同时建了 boluo2，00:40:12，从没用过）。

| 时间 (UTC) | 国家/节点 | 版本 | 时区 | 结果 | 是谁 |
|---|---|---|---|---|---|
| 00:47:46 | IE/DUB | 空 | Europe/Dublin | soniox/key 403 | 上一会话的假码测试 `cl-boluo-0000…`（见记忆卡 classlive-fake-code-test-pollutes-panel） |
| 01:06:27 | IE/DUB | 空 | Europe/Dublin | 400 ×3（含 boluo2） | 会话 5b248889 的 curl 探针 |
| **01:32:26–01:33:43** | **CA/YYZ** | **0.0.1（网页）** | **Asia/Shanghai** | key 200×2 → 开课 201 → 01:32:58 下课 202 → 术语 200 | **真用户** |

- 设备：桌面 0 台；网页 1 台 `55881b`（01:32:26–01:33:43）。今日网页实时 22.4 s。
- 判断：CA + 上海时区 ≈ 人在国内、出口走加拿大代理（国家是 CF 按出口 IP 判的，时区是浏览器自报）。Owen 本机时区 Dublin、无系统代理，排除本机。完整码在本机文件里搜不到，只出现在会话转录里（list 输出 / 探针），不是本机脚本用的。
- **没有遥测**：带遥测的 Worker 01:33:58 部署、网页包 01:34:12 构建 —— 他在这之前用的是旧包。R2 `classlive-web` 里只有 6789（24 条遥测 + 5 段录音）和 test1（10 + 1）。
- 面板显示「0/2 · 从没用过」的原因 = `list()` 只数桌面设备（已修，见 §4，未部署）。
- **没做的**：没拿 boluo 码去查他的网页会话账本 —— 那会在 AE 里以 boluo 名下记一条 IE 请求，污染他的记录。

## 6. 下一步（按顺序）

1. **问 Owen**：boluo 发给了谁？（数据只能到「国内 + 加拿大出口 + 网页试了 77 s」）
2. **问 Owen 要部署许可**，然后：Worker `cd server/asr-worker && npx wrangler deploy`；网页按 `web/README.md` 的发布流程重建、上传。
3. 部署后验收：下一节网页课下课后看面板 `/api/cf` 的 `scriptThrewException` 是否为 0；`/api/list` 里 boluo 应显示 `web_devices: 1`。
4. Owen 已说**先不改通行码页面前端**。要改时的数据已备好：`list()` 新字段、`/api/devices` 早就回 `web_devices`、`scripts/cl_web_data.py list` 能列网页遥测 / 录音。

## 7. 顺手发现、没处理

- 网页版版本头永远是 `0.0.1`，分不出浏览器跑的是哪个构建（这次靠部署时间推断 boluo 用的旧包）。建议版本头带构建号；注意 `handler.clientVer` 截 16 字符、`passes-core.usage()` 会校验形状。
- `connect.end()` 对每行 `persist()` 都会 `push()`：单飞修好后已无害（只标 dirty），没改。
