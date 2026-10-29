# 流式识别重连：策略重写（2026-10-29）

**状态**：已实现在分支 `stream-reconnect-20261029`，未合并、未推送。
代码里三处注释指向本文件：`stream_asr.py` 的 `RETRY_*` / `RESPONSE_TIMEOUT_S` / `SEND_STUCK_S`。

## 起因：中国用户 10-29 BUSI3222 第一节

Soniox 一节课里 4 段在线（128 / 36 / 152 / 343 s），之后 ~57 min 全程本地 + qwen 兜底。
- 在线段之间的空档 35 / 140 / 307 s ≈ 旧退避表 `(0, 30, 120, 300)` + 握手。
- 每段都 < 600 s，旧 `RETRY_RESET_S = 600` 从没把预算还回去 → 第 4 次重连之后表走到头，再不试。
- 第二节同形状：5 次重连，累计断线 187.7 s。
- 内容没丢多少：总词数 7800 vs Groq whisper 参照 7753（比值 1.006）。但落在 Soniox 在线段里的参照词丢了 33.1%（本地段 15.4%）；
  三个 >30 s 的洞（107–162、487–521、1097–1174 s）全在 Soniox 段内。
  口径：数字和数字词先归一掉，减少对参照的依赖。复算脚本 `cov2.py`。

**没证明的事**：每次掉线的真因。10-29 的几次只能推断（链路不稳 / 半开 / 服务端 503 都能解释）。
这次改动让之后的每次掉线都留下 `{at, ran_s, why, detail}`（`metrics()["sessions"]`），下次就能直接看。

## 改了什么

| 项 | 旧 | 新 | 依据 |
|---|---|---|---|
| `RETRY_DELAYS_S` | 0, 30, 120, 300 后停 | 0, 2, 5, 10, 20, 然后 30 × 60 | 指数退避封顶 ~30 s；≈ 35 min 内一直在试 |
| `RETRY_RESET_S` | 600 | 60 | 连上 1 分钟就算健康，表回到头 |
| `RETRY_SKIP_AFTER_S` | 5 | 2 | 等 ≥2 s 的重连，成功后跳到实时，不回放积压 |
| `RETRY_JITTER` | 无 | ±20% | AWS 退避+抖动 |
| `RESPONSE_TIMEOUT_S` | 15 | 8 | 健康课 `msg_gap_max_s` 1.1–1.7 s（10/13 节）；出问题的课 5.4 / 6.0 s |
| `SEND_STUCK_S`（新） | — | 5 | 发送卡死看门狗，见下 |
| 会话日志（新） | — | `sessions[]` ≤120 条 | 下次有真因可查 |

### 发送卡死看门狗

`websockets` 同步客户端的 `send()` = `sendall`，持 `protocol_mutex`。库自带的 keepalive ping 要同一把锁，
所以 **`sendall` 一旦阻塞，库检测不到**（本地复现：靠服务端 `close 1006` 才发现，约 40 s）。
独立线程每 0.5 s 看 `_send_since`，超过 5 s 就 `ws.close_socket()` 打断阻塞，标 `stale`，走正常重连。

## 社区/官方调研（2026-10-29 复核）

| 结论 | 来源 | 日期 | 置信 |
|---|---|---|---|
| Soniox 503「Cannot continue request」→ 应用立即新开请求 | https://soniox.com/docs/stt/rt/error-handling | 抓取 2026-10-29 | 高（官方） |
| 音频要实时或近实时发；持续突发/滞后可能被断开 → **重连后不能回放积压** | 同上 | 同上 | 高（官方） |
| 半开连接（无 FIN/RST）会让客户端永远挂着，要靠心跳/超时发现 | https://github.com/livekit/agents/issues/7356 | 2026-09-19 | 中（社区 issue；与本地复现独立一致） |
| 指数退避 + 抖动 | https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/ | 未重抓 | 中 |
| 1006 = 异常关闭（无 close 帧） | https://websocket.org/reference/close-codes/ | 未重抓 | 中 |
| Deepgram 流式上限约 1.25× 实时 → 补发积压行不通 | developers.deepgram.com（曾在调研中读到） | 未重抓 | 低（只是旁证，不进决策） |
| Soniox 官方 Web SDK 用 1/2/4 s 退避 + 音频缓冲 | soniox.com/docs/sdk/web-SDK | 未重抓 | 低 |

## 故意没做

- **不缓冲回放**：Soniox 明说拒绝持续突发。断线的空档交给本地识别补。
- **P1：Soniox 活着但冻住时让本地接管**——代价只是延迟（笔记靠 qwen 定稿仍完整），而且有和 Soniox 滞后输出内容重复的风险。
- 不改 Soniox 区域路由；不动 Z1 的未提交文件。

## 验证

- `tests/test_stream_asr.py` 新增 4 条：发送卡死看门狗 / 退避抖动 / 会话日志 / 常量贴合 10-29 形状。
- 变异验证：5 处变异各让对应测试变红，已还原并 diff 确认。
- `scripts/check.py` 全绿（115 个测试文件、ruff、pyright 基线、写端未变）。

## 未验证

- 真实日本/欧洲链路上的行为没跑过；测试用本地假 Worker + FakeSoniox。
- `RESPONSE_TIMEOUT_S = 8` 在网络差的用户那边会不会误杀健康连接——取决于他们的 `msg_gap_max_s` 分布，目前只有 13 节课的样本。
- 新退避表对 Soniox 服务端侧的压力（每用户最多 ~每 30 s 一次握手）没和 Soniox 确认。
