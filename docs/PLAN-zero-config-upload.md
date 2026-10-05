# 计划：零配置上传（测试数据 → Cloudflare Worker + R2）

> **本文件是唯一正本。** 动手前读这一份。
> 作者 2026-10-05 拍板：落点 = **Cloudflare Worker + R2**；**用户侧零配置**。
> ⚠️ **本文不抄行号**，只写符号名 —— 行号会漂，符号名查得到。
> 前置：`docs/PLAN-test-mode.md`（测试模式本体 · 上传的队列/退避/幂等/脱敏）。
> ⚠️ **改这份前先读那一份** —— 上传的"该不该传、传什么、怎么脱敏"在那份定死了，本文只管**换传输**。

---

## 0. ⭐ 已核实的事实（2026-10-05 在 HEAD=v3.8.8 上现查）

| 事实 | 出处 | 影响 |
|---|---|---|
| ⭐⭐ **上传目标是作者私人 ssh 别名** —— `UPLOAD_HOST = "bldcam"` · `UPLOAD_REMOTE = "classlive-test"` | `main.py` 顶部 | **这就是"要配置"的根**：`bldcam` 只存在于作者 `~/.ssh/config`，朋友机器上不存在 |
| ⭐⭐ **VPS 全时段只有一台机器连过** —— 228 次成功登录**全部来自同一 IP、同一把密钥**（作者机器） | VPS `auth.log` | **朋友从没连上过**；VPS 上 0 条数据（`~/classlive-test/` 只有 README） |
| ⭐ **`send(local, key) -> bool` 是唯一的传输洞** | `upload.py` 文件头 | **换传输只动这一个函数**，队列/退避/幂等/脱敏全不用碰 |
| `UploadQueue`（队列/退避/幂等/脱敏）**与传输无关** | `upload.py` | 原样保留 |
| 收尾走 `start_upload(tester)` → `q.enqueue(...)` → 后台线程 `q.pump()` | `main.py` | 失败不阻塞收尾；⚠️ **上传成功/失败界面都不显示** → 朋友以为"排过队 = 传成功了" |
| 上传文件 = `report.json` + `*.NNN.opus`（分段）+ `*.md`（转录）；**崩溃补传**另有 `<stem>.meta.json` | `testmode.upload_files` / `upload.recover_pending` | Worker 白名单照这几类 |
| `_remote_name` 把 `*.report.json→report.json` / `*.md→transcript.md` / `*.NNN.opus→audio.NNN.opus`，**认不出的原名透传** | `upload.py` | ⚠️ **崩溃补传的 `<stem>.meta.json` 会原名透传 → 泄漏课号+时间戳**（见 §8.4） |
| ⭐ **`httpx` 已是运行依赖**（DeepSeek 云端翻译在用） | `requirements.txt` | **HTTP 上传不加新包** |
| **本机 HTTP 服务器当上游的判据先例已存在** | `tests/test_fetch_model.py` | 新判据照它，**全离线**（本机起服务器当 Worker） |
| 域 `bldcam.page` 在 Cloudflare 下（`data.bldcam.page` 走 Flexible SSL） | 记忆 `npc-project-site` | Worker 可挂子域 `ingest.bldcam.page` |
| ⚠️ 发出去不到一小时、Release 零 asset 零下载才敢挪 tag（先例） | `classlive-session-state` | 若改动了已发版本的上传常量，参照那条的六项检查 |

---

## 1. 作者拍板的决定（**别再问一遍**）

| # | 事 | 决定 |
|---|---|---|
| **D1** | 落点 | **Cloudflare Worker + R2** |
| **D2** | 用户侧 | ⭐ **零配置** —— 不碰 ssh、不装 key、不做**任何**设置 |
| **D3** | 凭据 | **公开 write-only token**（Sentry DSN 型）—— 编进 app，**被看到也没关系**（见 §6） |
| **D4** | 传输 | **HTTPS PUT**（`httpx`）替掉 rsync over ssh |
| **D5** | 队列 / 退避 / 幂等 / 脱敏 | ⛔ **不动** —— 那是 `PLAN-test-mode.md` 定死的 |
| **D6** | 开关 | 保留 `CLASSLIVE_UPLOAD=0`（判据与离线回放必须用它） |

---

## 2. 为什么要改：现在的设计**结构性地**违反零配置

现状链路（`PLAN-test-mode.md` §6.7 选定的）：

```
app → rsync -a --rsync-path=... <local> bldcam:classlive-test/<key>
                                        ^^^^^^ 作者私人 ssh 别名 + 私人密钥
```

`bldcam` 是**作者机器上**的一条 ssh 别名（Host + IdentityFile）。朋友机器上：

1. **没有这条别名** → rsync 报 `Could not resolve hostname bldcam` → `send` 返回 `False`；
2. `UploadQueue` 照设计把这条**留着重试**（退避到 120 min、14 天过期）→ 数据烂在朋友本机；
3. 收尾界面**不显示成败** → 朋友看到 `📤 已排队 N 个文件`，**以为传上去了**。

→ **这不是 bug，是设计缺口**：`PLAN-test-mode.md` §6.8 只在**作者自己机器**上验证了 `bldcam`，
从没在朋友机器上验过「零配置能不能通」。本文补上这一格。

**结论**：让"用户机器"这一侧**不带任何需要人工填写的东西** —— 传输换成**任何装了 app 的机器都能直接发**的 HTTPS，凭据换成**公开也没关系**的那种。

---

## 3. ⭐ 调研：市面主流做法 + 各自策略（2026-10-05）

零配置客户端上传是**有成熟答案的**问题。四种主流形状：

| 做法 | 谁在用 | 客户端带什么 | 滥用防线 | 出处 |
|---|---|---|---|---|
| ⭐ **公开 write-only 摄入 key**（DSN 型） | **Sentry**、**Datadog RUM/logging** | 内置一个**公开** key（明文可见） | 服务端 **per-key 限流** + 域名白名单 + **可轮换/吊销** | [sentry dsn-explainer](https://docs.sentry.io/concepts/key-terms/dsn-explainer/) · [sentry dsn-keys](https://sentrydocs.dev/sdk/dsn-keys) |
| 预签名 URL（短时） | R2 / S3 直传 | 服务端签发的 URL（URL 即 bearer） | 限定**单对象 + 单操作** + 短过期 | [CF R2 presigned](https://developers.cloudflare.com/r2/api/s3/presigned-urls) |
| 能力 URL（不可猜路径） | 各种 webhook / 上传 | 随机长路径当密码 | 路径保密 + 限流 | — |
| ⭐ **Worker 代理（服务端持密）** | **Cloudflare 官方推荐** | 只带**公开** token | Worker 校验 token + CF 限流/WAF | [CF R2 presigned §central app](https://developers.cloudflare.com/r2/api/s3/presigned-urls) |

### 3.1 ⭐ 关键结论（决定本文形状）

**零配置客户端上传，业界从不指望密钥真的保密。** 逐字出处：

- Sentry 官方：*"DSNs are **safe to keep public** because they only allow **submission** of new events... they do **not allow read access** to any information."*
  防线是 *"**Rate limits** ... **Allowed domains** ... **Rotate (and revoke)**"*；并承认 *"there is a risk of abusing a DSN ... this is a **rare occurrence**."*
- Datadog：client token 明确定义为 *"a **write-only credential** for use in the web browser and apps"*。
- Cloudflare：Worker 里的 R2 密钥 *"cryptographically **impossible to obtain** outside of your script"* → **密钥留 Worker，客户端只拿公开 token**。

→ **ClassLive 照 Sentry 那套做**：客户端内置**公开 write-only token**；真正的防线（限流 / 体积上限 / 路径白名单 / 配额）**全在服务端**。

### 3.2 为什么不选其它三条

| 没选 | 为什么 |
|---|---|
| **预签名 URL** | 需要一个**签发服务**，而签发服务本身要鉴权 = 又回到"要配置"。且它是**短时**的，不匹配"课后异步补传"（队列可能隔天才发）。 |
| **能力 URL 单发** | 可行但更弱：整条 URL 即凭据，轮换要改 URL 结构。用法上等价于 DSN，但 DSN 有成熟的服务端防线可照抄。 |
| **R2 直连（嵌 S3 token）** | 嵌的是 **R2 API token**（读+写能力）→ 泄露面比 write-only 大得多。**Worker 代理**把 R2 密钥关在 Worker 里，客户端只拿 write-only token，是更紧的形状。 |

---

## 4. 客户端设计（`upload.py`）

### 4.1 换掉那个洞（只动一处）

`make_rsync_send(host, remote_dir)` → **`make_http_send(base_url, token)`**，签名仍是 `send(local, key) -> bool`：

```python
def make_http_send(base_url: str, token: str, *, timeout: float = 60.0):
    """造一个「HTTPS PUT 到 Worker」的 send。生产用的那一个。

    ⚠️ 与 rsync 版**契约完全一致**：成功 True / 失败 False，**不许抛**
       （调用方是退避循环，见 UploadQueue.pump）。
    ⚠️ 流式发（`open("rb")` 交给 httpx），**不把整段音频读进内存**。
    """
    import httpx

    def send(local: pathlib.Path, key: str) -> bool:
        try:
            with open(local, "rb") as fh, httpx.Client(timeout=timeout) as c:
                r = c.put(f"{base_url.rstrip('/')}/{key}", content=fh,
                          headers={"Authorization": f"Bearer {token}",
                                   "Content-Type": "application/octet-stream"})
            return 200 <= r.status_code < 300
        except Exception:                                     # noqa: BLE001
            return False
    return send
```

⚠️ **不动的东西**：`enqueue` / `pump` / `_save` / `scrub` / `recover_pending` / `.pending.json` 全保留 ——
它们与传输无关，是 `PLAN-test-mode.md` 的成果。

### 4.2 落点常量（**唯一的"配置"点，且是给作者看的，不是给用户**）

新增一个**单一来源**模块 `upload_endpoint.py`（别在 `main.py` 里散着）：

```python
#: 零配置上传的落点。⚠️ 这两个常量是**公开**的 —— 它们随 app 发给每个用户，
#: 而且本仓库是 MIT 公开的。**它们不是密钥**（见 docs/PLAN-zero-config-upload.md §6）。
ENDPOINT = "https://ingest.bldcam.page/v1/objects"
TOKEN    = "<32 位十六进制，公开写入令牌>"
```

`main.py`：`_up.make_http_send(UPLOAD_ENDPOINT, UPLOAD_TOKEN)`（收尾那处 + 启动补传那处，两条都换）。
加一个 `CLASSLIVE_UPLOAD_ENDPOINT` 环境变量覆盖（判据/联调用），默认取常量。

### 4.3 ⚠️ 顺带修**三个**既有泄漏（都出在崩溃补传这条链上）

`recover_pending` 造/带的文件与 `_remote_name` 对不上，逐条：

1. **`.meta.json` 的名字** —— `_remote_name` 认不出 `<stem>.meta.json` → **原名透传**
   → 远端名 = `2026-09-24_203440_ECON10770.meta.json` → 泄漏课号+时间戳。
   修：`_remote_name` 加 `*.meta.json → meta.json`。
2. ⚠️ **`.wav`（OCR 第 6/7 条抓的）** —— 崩溃时 `finish()` 没跑、wav 没转 opus，`recover_pending`
   会把 `<stem>.NNN.wav` 排进队列；`_remote_name` 不认 → 原名透传（漏课号+时间戳）**且**
   Worker 白名单不认 `.wav` → **400 → 那条崩溃音频永远传不上去**（退避到 14 天过期）。
   修：`_remote_name` 加 `*.NNN.wav → audio.NNN.wav`；Worker 白名单加 `audio.\d{3}\.wav`。
3. ⚠️ **meta 正文（OCR 第 5 条）** —— 仅改文件名不够：meta **体**里写了 `stem` 与 `files`
   （原始文件名），而 `_prepare` 的 `scrub` 只认路径、认不出这两个字段 → 正文照样漏。
   修：`recover_pending` 的 meta **不写 stem / 不写文件名**，改写 `key`（哈希）+ 计数 + 类型。

判据：`tests/test_upload.py` —— 崩溃链上**没有一个远端名带 stem**；meta 正文**不含 stem**。

---

## 5. 服务端设计（Cloudflare Worker + R2）

### 5.1 形状

```
客户端 ──PUT──▶ ingest.bldcam.page/v1/objects/<hash16>/<name>
                 │  ① Authorization: Bearer <公开 token> 校验
                 │  ② 路径白名单（正则，见下）—— 挡穿越、挡乱名
                 │  ③ 实际字节数 ≤ 上限（读进来核，不信 Content-Length）
                 ▼
              Worker（持 R2 密钥，密钥永不离开 Worker）
                 │  env.BUCKET.put("classlive-test/<hash16>/<name>", body)
                 ▼
              R2 桶（私有，无公开读）
```

### 5.2 Worker 骨架（`server/upload-worker/src/index.js`）

```js
const MAX_BYTES = 32 * 1024 * 1024;
const NAME_OK = /^\/v1\/objects\/([0-9a-f]{16})\/(report\.json|transcript\.md|meta\.json|audio\.\d{3}\.(opus|wav))$/;

export default {
  async fetch(request, env) {
    if (request.method !== "PUT") return new Response("method not allowed", { status: 405 });

    // ⚠️ fail-closed：secret 没配时 `env.INGEST_TOKEN` 是 undefined，模板串会成
    //    字面量 "Bearer undefined" → 谁都能进。显式挡。
    const expected = env.INGEST_TOKEN ? `Bearer ${env.INGEST_TOKEN}` : null;
    if (!expected || (request.headers.get("Authorization") || "") !== expected)
      return new Response("unauthorized", { status: 401 });

    const m = new URL(request.url).pathname.match(NAME_OK);
    if (!m) return new Response("bad path", { status: 400 });

    const ip = request.headers.get("CF-Connecting-IP") || "0.0.0.0";
    if (!(await env.INGEST_LIMITER.limit({ key: ip })).success)
      return new Response("rate limited", { status: 429 });

    // ⚠️ 按**实际字节数**卡，不看 Content-Length（公开 token → 可伪造）。
    const payload = await request.arrayBuffer();
    if (payload.byteLength === 0) return new Response("empty body", { status: 400 });
    if (payload.byteLength > MAX_BYTES) return new Response("too large", { status: 413 });

    try {
      await env.BUCKET.put(`classlive-test/${m[1]}/${m[2]}`, payload,
        { httpMetadata: { contentType: "application/octet-stream" } });
    } catch { return new Response("storage error", { status: 502 }); }
    return new Response("ok", { status: 200 });
  },
};
```

- **对象名 = `<hash16>/<name>`**：与现行 VPS 布局一致（哈希前缀 = 一节课 = 整节删得掉），只把根从 `~/classlive-test/` 换成 R2 前缀 `classlive-test/`。
- **幂等**：R2 `put` 同名覆盖 → 与 `UploadQueue` 的幂等语义**天然对齐**（重传=覆盖，不新增）。
- **绑定**：`env.BUCKET` = R2 桶绑定；`env.INGEST_TOKEN` = 公开 token（Worker secret，⚠️ 但它公开，见 §6）。

### 5.3 落点与命名（具体值动手时定）

| 项 | 建议值 | 备注 |
|---|---|---|
| 子域 | `ingest.bldcam.page` | Workers Custom Domain/Route，走 Cloudflare（TLS 自动） |
| R2 桶 | **新桶** `classlive-ingest` | ⚠️ **别复用 `photosave`**（BLDcam 的桶）—— 配额/生命周期/权限要独立 |
| 对象前缀 | `classlive-test/` | 与旧 VPS 布局同名，账目连续 |
| 读权限 | **私有**（无公开读、无 R2.dev 公开域） | 只能写，读走面板/rclone |
| 体积上限 | 32 MB/对象 | 单段 Opus 实测约 1–2 MB；一节课合计约 9 MB（`PLAN-test-mode.md` §5） |

### 5.4 限流（⚠️ 必须配，这是公开 token 的兜底）

Cloudflare **Rate Limiting rule**（dashboard 配，不是代码）：对 `ingest.bldcam.page/v1/objects/*`
按 IP 限速（建议 **60 req/min / IP**，burst 允许）。→ 即使 token 公开、被扫到，也只能慢速写、写不出洪峰。

### 5.5 作者的运维（读取 / 删除 / 反查）

- **列/看**：R2 dashboard，或 `rclone ls` / `wrangler r2 object`。
- **删一节课**：删前缀 `classlive-test/<hash16>/`。
- **反查哈希 → 哪节课**：仍靠本机 `~/.classlive/upload-ledger.json`（`upload.py` 已经在写）——**不搬，不重复**。
  ⚠️ 所以 **ledger 这份台账不能删**（`PLAN-test-mode.md` §6.9 同一条）。

---

## 6. ⭐ 安全模型（**本文最要紧的一节**）

### 6.1 这个 token 是什么

**它是公开的。** 理由：它随 app 发给每个用户，而本仓库 MIT 公开 → 任何人都能从源码或反编译 `.app` 里拿到。
**照 Sentry DSN 的定位**：它**只证明"这是 ClassLive 在发"**，**不授权任何读**。

### 6.2 拿到 token 的攻击者能做什么（诚实清单）

| 能 | 不能 |
|---|---|
| 往桶里**写**对象（伪造/垃圾） | **读**任何已上传的数据 |
| 用掉你的 R2 存储/请求**额度** | **列**桶、**删**对象、改他人对象名 |
| — | 拿到 R2 密钥（关在 Worker 里） |

→ **最坏后果 = "有人灌垃圾/耗额度"**，不是"数据泄露"。这正是 Sentry 说的 *"rare occurrence"* 量级。

### 6.3 防线（五条，全在服务端）

1. **write-only**：Worker 只有 `put`，没有读/列/删路由。
2. **路径白名单**：正则钉死 `^<16 hex>/(report\.json|transcript\.md|meta\.json|audio\.\d{3}\.opus)$` → 挡目录穿越、挡乱写任意 key。
3. **体积上限**：**读实际字节数**，`> 32 MB` 拒 → 挡大文件灌盘。⚠️ **不看 `Content-Length`** —— token 公开，攻击者可声明一个小值却流一大坨。
4. **限流**：Cloudflare Rate Limiting（§5.4）→ 挡洪峰。
5. **配额（建议）**：R2 桶用量告警 +（可选）**安全生命周期**（§12 Q1）。

### 6.4 轮换（token 泄露/被滥用时）

1. `wrangler secret put INGEST_TOKEN`（换新值）；
2. 改 `upload_endpoint.py` 的 `TOKEN` → **发一版**（用户 `cl update` 后自动用新值）；
3. 旧 token 在 Worker 里失效即止 —— 旧版本 app 的上传会 401 → 留队列重试 → 用户更新后补传。
⚠️ 代价：**轮换要发版**。接受（低频操作），与 Sentry"deploy 后旧 key 流量归零再删"同形。

### 6.5 ⚠️ 隐私（这条不因为"零配置"而消失）

数据从"作者自己的 VPS"挪到"**Cloudflare 的对象存储**"。内容是**课堂音频 + 逐字转录**，可能含其他人的声音。
→ **同意故事不变**（`PLAN-test-mode.md` D9：作者社交层解决）；但**披露一句要更新**（"会上传到作者控制的云存储"）。
→ 录音开始**之前**说（Granola 的规矩），不是课后补。

---

## 7. ⭐ 零配置验证（D2 怎么算达成）

**判据是一句话**：一台**全新**的 Mac，只装 ClassLive 的 release，**不做任何 ssh/key/设置**，
开一次测试模式录一节课，收尾后 **R2 桶里出现这节课的四个对象**（`report.json` / `transcript.md` / `audio.*.opus` / 需要的 `meta.json`），
且**这台机器上 `~/.classlive/upload-queue.json` 最后是空的**（都传成了）。

⚠️ 这条**必须在一台不是作者机器的机器上跑** —— 作者机器上永远有 `bldcam`，**测不出这个缺口**。
（现状之所以拖到现在，就是因为只在作者机器上验过。）

---

## 8. 改动清单（文件级）

### 8.1 `upload.py`
- 新增 `make_http_send(base_url, token)`（§4.1）。
- `_remote_name` 加 `*.meta.json → meta.json`（§4.3）。
- `make_rsync_send` **留着**（判据里仍可用它钉死旧行为？→ 否，直接删，见 §12 Q2）或删掉。
- 文件头把"生产：rsync 到 VPS"改成"生产：HTTPS PUT 到 Worker"。

### 8.2 `upload_endpoint.py`（新）
- `ENDPOINT` / `TOKEN` 单一来源（§4.2）。

### 8.3 `main.py`
- 删 `UPLOAD_HOST` / `UPLOAD_REMOTE`；两处 `make_rsync_send(...)`（收尾 + 启动补传）换成 `make_http_send(UPLOAD_ENDPOINT, UPLOAD_TOKEN)`。
- 读 `CLASSLIVE_UPLOAD_ENDPOINT` 覆盖。

### 8.4 `server/upload-worker/`（新，独立小项目）
- `wrangler.toml`（桶绑定、route）+ `src/index.js`（§5.2）。
- ⚠️ 这个目录**不进 Python 的默认闸门**，它有自己的部署流程（`wrangler deploy`）。

### 8.5 `tests/test_upload.py`
- 新增 `make_http_send` 的判据：本机 HTTP 服务器（照 `tests/test_fetch_model.py`）断言 ① URL 拼对 ② `Authorization` 头对 ③ body 字节一致 ④ 非 2xx → `False` ⑤ 连不上 → `False`（不抛）。
- 新增 `_remote_name` 那条泄漏判据（§4.3）。

### 8.6 `docs/` / `README.md`
- `docs/HANDBOOK.md` 那张表里"测试模式上传 | VPS `ssh bldcam`"那行改成本文落点。
- `README.md` 的隐私披露一句（§6.5）。

---

## 9. 判据（完成判据）

| # | 判据 | 手段 |
|---|---|---|
| T1 | `make_http_send` 契约：成功 True / 失败 False、**不抛** | 本机 HTTP 服务器（`test_fetch_model.py` 形状） |
| T2 | 非 2xx（401/413/400）→ False | 本机服务器回对应码 |
| T3 | 崩溃补传链上**没有一个远端名带 stem** | `test_upload.py` 新条 |
| T4 | 默认闸门全绿（未碰网络/模型） | `ClassLive.app/Contents/MacOS/python tests/test_audit_regressions.py` |
| T5 | ⭐ **零配置端到端**（§7）：全新机器上跑通 | **手验**（一台非作者机器）—— 写进 §12 Q3 |
| T6 | Worker：非法路径/超大 body/错 token/**secret 未配（fail-closed）**一律拒，不进桶；R2 写失败→5xx | `node server/upload-worker/test.mjs`（执行**真源码** + 桩 env） |

⚠️ **T5 是本文的成败判据，且不能靠作者机器自测** —— 见 §7。

---

## 10. 雷区

- ⚠️ **别把 token 当秘密**：任何"加密/混淆 token"的想法都是**安全剧场** —— 它随 app 公开，防线只能在服务端（§6）。
- ⚠️ **别复用 BLDcam 的桶 `photosave`**（§5.3）—— 权限/配额/生命周期要独立。
- ⚠️ **别在 Worker 里加读/列路由** —— 一旦有读，公开 token 就从"只写"升级成"能读别人数据"，性质全变。
- ⚠️ **别丢 `CLASSLIVE_UPLOAD=0`** —— 判据与离线回放靠它（不然每跑一次测试真往 R2 写一份）。
- ⚠️ **别动 `UploadQueue` / `scrub` / `recover_pending`** —— 那是 `PLAN-test-mode.md` 的成果，与传输无关。
- ⚠️ **零配置判据必须在非作者机器上跑**（§7 · T5）—— 否则永远测不出缺口。
- ⚠️ 改已发版本的上传常量 = 要发新版；若想搭车，先跑 `classlive-session-state` 里那**六项检查**。

---

## 11. 边界情况

| # | 场景 | 期望 |
|---|---|---|
| 1 | 用户离线 / Worker 502 | `send` 返回 False → 留队列、退避重试（现有机制，不动） |
| 2 | 中途关掉测试模式 | 现有 `.pending` 崩补传继续工作；传输无关 |
| 3 | 同一节课重传 | R2 同名覆盖 = 一份（幂等） |
| 4 | 有人扫到公开 token 灌垃圾 | 限流 + 体积上限 + 白名单挡住；最坏是占额度（§6.2） |
| 5 | 作者轮换 token | 旧版 app 401 → 留队列 → 更新后补传（§6.4） |
| 6 | 用户机器无 ssh/key | **正常传**（这正是要修的） |

---

## 12. 未决（动手前要定）

| # | 事 | 建议 |
|---|---|---|
| **Q1** | R2 桶加不加**安全生命周期**（如 180 天自动删）？ | 作者先前（`PLAN-test-mode.md` D8）说手动清。**建议加一条宽松的安全网**（防灌爆成本），但**由作者定** |
| **Q2** | `make_rsync_send` 删还是留？ | **建议删** —— 留着就是"第二条没人走的传输路"，会腐坏（判据不覆盖它） |
| **Q3** | 谁来跑 §7 的零配置端到端（非作者机器）？ | 建议**找那位朋友**在**他机器**上重跑一次（他也正好是"录过一节课"的那位） |
| **Q4** | 子域名 / 桶名的最终取值 | §5.3 是建议值，动手时定 |

---

## 13. 阶段与顺序

| 阶段 | 内容 | 判据 |
|---|---|---|
| **1** | 客户端：`make_http_send` + `upload_endpoint.py` + `main.py` 接线 + `_remote_name` 修泄漏 | T1–T4 全绿 |
| **2** | 服务端：Worker + R2 桶 + route + 限流 | T6（`wrangler dev` 拒非法输入） |
| **3** | ⭐ **零配置端到端**：非作者机器跑通（§7） | **T5** |
| **4** | 收尾：HANDBOOK/README 披露更新、`make_rsync_send` 删除 | 文档一致 |

⚠️ **阶段 3 不过，这次改动就没达成 D2** —— 那正是**现在**这一稿要修的缺口，别用作者机器自测蒙混。

---

## 14. 落地状态（2026-10-05 实现）

**已实现**（客户端 + 服务端骨架）：
- `upload_endpoint.py`（新）：`ENDPOINT = "https://ingest.bldcam.page/v1/objects"` · `TOKEN = <32 hex>`（**公开**，见 §6）。env 覆盖 `CLASSLIVE_UPLOAD_ENDPOINT` / `CLASSLIVE_UPLOAD_TOKEN`。
- `upload.py`：`make_http_send(base_url, token)` 替掉 `make_rsync_send`/`rsync_path`（★Q2 已删）；`_remote_name` 加 `*.meta.json` / `*.NNN.wav` 两条；`recover_pending` 的 meta 不再写 stem/文件名（见 §4.3）。⚠️ 客户端**整份读进来发**（`read_bytes()`）—— 不用 `open()` 交给 httpx，否则走 chunked 没 `Content-Length`。
- `main.py`：删 `UPLOAD_HOST`/`UPLOAD_REMOTE`；两处接线改 `make_http_send(endpoint(), token())`。
- `server/upload-worker/`（新）：`wrangler.toml`（R2 绑定 `BUCKET`→桶 `classlive-ingest`；限流 `INGEST_LIMITER` 60/60；自定义域 `ingest.bldcam.page`）+ `src/index.js`（四道防线）+ `test.mjs`（执行**真源码**的判据）。
- 判据：`tests/test_upload.py` **49/49** · `node server/upload-worker/test.mjs` **14/14** · 默认闸门 109/109。变异验证：删 `Authorization` 头 → 红；fail-closed 退回 naive → 红。

**⭐ OCR 审查（2026-10-05，`ocr review --provider cc-switch`）**：7 条（2 high / 3 med / 2 low），coverage 5/5，**high/med 全部回原码核实属实并已修**：
- **high × 2 = §4.3-2 的 `.wav`**（客户端 `_remote_name` + Worker 白名单各一处）；
- **med × 3** = meta 正文泄漏（§4.3-3）· Worker 鉴权 **fail-open**（secret 未配时 `"Bearer undefined"` 放行）· 体积闸门**信 `Content-Length`**（公开 token 可伪造）；
- **low × 2** = `BUCKET.put` 未捕异常（改 → 502）· `wrangler.toml` 里"180d 生命周期"注释误导（wrangler **不支持** lifecycle，得在 Dashboard/API 配）。
- ⚠️ OCR 审的是**修之前**的快照（边跑边改），它的 `.wav` 两条正好印证了这条链的缺口。

**部署（✅ 2026-10-05 完成）**：
1. ✅ 建私有桶 `classlive-ingest`。
2. ✅ `npm i -g wrangler` → `wrangler login` → `wrangler r2 bucket create classlive-ingest` → `wrangler secret put INGEST_TOKEN`（= `upload_endpoint.py` 的 `TOKEN`）→ `wrangler deploy`（wrangler 4.147.0）。
3. ✅ `ingest.bldcam.page (custom domain)` 已挂；绑定 `env.BUCKET (classlive-ingest)` + `env.INGEST_LIMITER (60/60s)` 已生效。
4. ✅ 真机探针：合法 PUT→200 · 错 token→401 · 坏路径→400 · GET→405 · 错 hash 长度→400；`wrangler r2 object get --remote` 取回内容一致（写入路径确实到桶）。

⚠️ **部署期踩到两个坑（记下来）**：
- **TOML 顺序**：`routes = [...]` 原来写在 `[ratelimits.simple]` **后面** → TOML 把它解析成那张表的键（wrangler 只给一条 warning）→ 自定义域**根本没建**、DNS 解析不了、探针 HTTP 000。
  **修：`routes` 移到所有 `[table]` 之前**（顶层键必须在任何表头之前）。
- **`wrangler r2 object get/put/delete` 默认操作的是「本地模拟桶」** —— 要加 **`--remote`** 才碰真桶；否则得到的 `key does not exist` 是本地空桶的假象。
  查法：`wrangler r2 object get <bucket>/<key> --file x --remote`。

⬜ **180 天生命周期尚未在 Dashboard 配**（Q1 已定，待做）。

**未做**：
- ⬜ **零配置端到端**（§7 / T5）—— 需**朋友机器**跑，作者机器测不出（★Q3）。
- ⬜ `README.md` 未改 —— 它从没点名落点（"传给作者"仍准确），隐私那段"可能含同学声音"仍成立。
