# 更新分发：现有策略复核 + 中国可达性重构

> 2026-11-17 起草。触发事件：中国用户 11-17 的课仍跑 3.11.4，而 3.11.5 已于 11-16 19:30（北京）发布。
> 本文只谈**分发与更新**，不重复 `docs/PLAN-update-mechanism.md`（2026-09-24）已锁定的设计。
> 那份文档的结论（git 分发、退出时自动、auto/manual 分级、独立进程）**仍然成立**，本文是它的边界条件补丁。

---

## 0. 结论摘要

| 问题 | 判断 |
|---|---|
| git clone + `pull --ff-only` 作为分发方式，是最优吗？ | **是**。对本项目（无 .app bundle、无签名链、几百个 .py、多台朋友机器）比 Sparkle/Tauri/Homebrew 都便宜且更安全 |
| 现有更新引擎的实现是最优落实吗？ | **主体是**（ff-only / 脏文件停手 / journal 修复 / 执行锁 / 限频 / 独立进程都有实测依据）。**四处在边界上不合格**：单源 GitHub、fetch 不走系统代理、检查失败完全静默无日志、客户端无法被迁移 |
| 要不要改成「服务器推送」？ | **不要**。桌面端没有公网可达入口，push 需要长连接或推送证书；等价能力用「客户端轮询 + 服务端决定返回哪个版本」就能拿到（Tauri 的 dynamic update server 就是这么做的） |
| 能不能只用 VPS？ | **能，且是当前性价比最高的修法**。VPS 上跑只读 git 镜像，客户端换 `remote`（或全局 `insteadOf`），GitHub 退化为你的上游，不再是客户端依赖 |
| 最小改动、今天就能上的 | `_git()` 注入系统代理（约 20 行 + 1 条单测）。这一步单独就能让「开了代理的中国用户」查到更新 |

**一句话**：架构不用换，换的是**可达性**与**可观测性**。前者靠自建源/代理注入，后者靠日志与报告字段。

---

## 1. 触发事实（可复现）

| 证据 | 值 |
|---|---|
| `cl_admin.py stats --days 7` | bldcn 11-12=3.11.1 → 11-16=3.11.4 → **11-17 仍 3.11.4**；bldus 11-11=3.11.1 → 11-13=3.11.3 → **11-16=3.11.5** |
| 11-17 报告 `blocks.region_probe` | `version: 3.11.4`，us/jp/eu 三区全 `error: ImportError` |
| 11-17 报告 `blocks.stream` | `connect_attempts 66 / connections 0 / sealed_n 0`，`last_close_detail = ImportError: connecting through a SOCKS proxy requires python-socks` |
| 同一节课的 HTTP 链路 | qwen 581/592 段成功、翻译 1127 句 p50 1.47 s → **不是断网**，是特定客户端只认系统代理、特定客户端不认 |

结论：同一台机器上，`websockets`（读系统代理）读到 SOCKS、`httpx`（读 http_proxy）读到 HTTP 代理、**`git`（两者都不读）直连 GitHub**。三条路三种命运。

---

## 2. 现有实现解剖

| 环节 | 代码 | 机制 | 评价 |
|---|---|---|---|
| 分发形态 | — | `git clone` 工作目录，无 bundle / 无 pip 包 | ✅ 与「无 .app、无签名」现实匹配 |
| 编译期 | `vendor/` | websockets / python_socks / budoux / katex 随代码走 | ✅ 免 pip，改依赖只需换文件 |
| 检查 | `update.check()` `update.py:442` | `git fetch --quiet` + `rev-list HEAD..@{u}` | ⚠️ **单源、无代理、失败无日志** |
| 卡片入口 | `entry_panel.py:3083-3095` | 面板打开时后台线程 `update.offer()`，**只在 `kind != none` 时出界面** | ⚠️ 「查不到」与「已最新」外观完全相同 |
| 应用 | `update.pull()` `update.py:528` | 三道闸（分支 / 脏文件 / 锁）+ `pull --ff-only` + journal | ✅ 边界处理是这份代码最好的部分 |
| 中断修复 | `repair_interrupted_apply()` `update.py:979` | 比对 HEAD/journal/工作区，决定补完/回滚+备份/保留 | ✅ 有实测现场（3000 文件半换 + `index.lock` 死锁），不是过度设计 |
| 并发 | `update.py:707 _acquire_lock` | `LOCK_STALE_S=900` 自过期 | ✅ |
| 限频 | `update.py:672` `RATE_LIMIT_S=300` | 5 分钟一次；已同意则不限频 | ✅ 比 atomic（1 h）激进但合理（fetch 增量只有几 KB） |
| 退出时自动 | `update.auto_update()` `update.py:1167` + `main.py:1850` | 独立进程、`start_new_session`、失败只写日志 | ✅ 与 Mozilla/Firefox/atomic 同构 |
| 分级 | `changelog.update_mode()` + 发版写 `### 更新方式` | 默认 `manual`，不靠 semver | ✅ 这是本项目最有价值的自定义决策（`3.4.0→3.5.0` 就是 breaking minor） |
| 迁移能力 | — | 客户端 remote 写死 GitHub（clone 时） | ❌ **一旦换源，存量客户端没有任何被动通道** |
| 可观测 | `~/.classlive/`、报告 `blocks.*` | `_log()` 只在 `auto_update()` 里调 | ❌ 面板那次检查失败**零痕迹** |

关键缺口集中在一处：`update.py:46` 的 `_git()`。

```python
subprocess.run(["git", *args], cwd=HERE, ...)   # 继承 os.environ，但没有代理、没有 GIT_TERMINAL_PROMPT=0
```

`os.environ` 里通常没有 `http_proxy`（App 由 launchd 起，不读 `~/.zshrc`），而 macOS 的「系统代理」git 根本不看。于是：

```
websockets → urllib.getproxies() → 系统代理 → socks5://127.0.0.1:7890 → ImportError
httpx      → env / 系统代理       → http://127.0.0.1:7890    → 200
git        → 什么都不读          → 直连 github.com         → 超时 / 拒绝
```

在都柏林这台机器上 `curl -I https://asr.bldcam.page` 回 `cf-ray: …-DUB`；中国用户拿到的是 HKG/NRT 之类边缘。Cloudflare 的**中国大陆**网络是独立的企业产品（"built with strategic partners"，需 ICP 备案），免费版不经境內节点 —— 所以「用 CF 子域做更新源」在中国是**可用但抖**，不是「国内加速」。

---

## 3. 战略判断：逐条

### 3.1 git 分发是最优（保持不变）

- 免签名链：Gatekeeper 只拦被 quarantine 的下载件，`git`/`curl` 不打标记（本仓库 PLAN §6 已核实，Apple DTS 原话）。所以 `pull` 换 `.py` 不需要公证。
- 增量天然：只传 diff，17 MB 的 `.git` 在慢网下也是秒级。
- 幂等可重入：同一节课重拉不产生副作用。
- 对象哈希提供**内容完整性**（git 的 tree/blob 校验），比「下 zip 覆盖」强。
- 对照：Sparkle 要求把 `.app` 打进 dmg/zip 并签 EdDSA（ed25519）签名，且官方明确要求**私钥不能放在托管服务器上**；本项目的「可执行体」是系统 Python + 已签名的解释器，没有 bundle 可签 —— 这条路本来就不通，不是不愿意做。

### 3.2 单源 GitHub 是真正的战略缺陷

不是「技术更好的做法」，而是**可达性**：一个源同时是「你的主库」和「所有客户端的唯一入口」，在中国大陆这两件事的可靠性完全不同。

业界的处理不是换协议，而是**多源**：
- Tauri updater 的 `endpoints` 是**数组**，且明确「只有返回非 2XX 才继续试下一个 URL」；
- electron-updater 把 feed 抽成 `publish` 提供方（GitHub / generic / S3），可换而不改代码；
- 企业内网一律自建镜像。

### 3.3 推送不可行，但「服务端主导」可行

桌面 App 没有公网入口，服务端主动 push 需要：常驻长连接（耗电、NAT 超时）或 APNs/FCM（要证书、要审核）。而客户端每次开面板本来就有一次 fetch —— 把它变成「问服务端」，就能拿到 push 的全部能力：

- 服务端可以返回「你该升到哪个版本」（包括**降级/回滚**），Tauri 的 dynamic update server 就是覆盖 `current_version` 比较来实现回滚的；
- 服务端可以按用户/地区下发不同的包（例如中国用户走镜像）；
- 客户端不需要知道有几个源。

代价是客户端仍要轮询 —— 但轮询频率（面板打开 + 退出）已经够了。

### 3.4 静默失败是最该修的一条

微软 Huabu 的 electron-updater 设计把两件事分开，值得照抄：

| 事件 | 处置 |
|---|---|
| **版本检查**失败（够不着 feed） | 什么都没装 → **不亮红标**；后台轮询只写 console；**手动检查**才回一句「Unable to check for updates: …」 |
| **下载/安装**失败（用户点了） | 亮常驻红标 + 点击重试 |

我们现在的状态是：两者都**不出界面、不进日志**。用户在面板上看到的永远是「已是最新」，运维看到的永远是「这个版本没升」，中间没有任何信息。这是本次事故**无法当场定位**的直接原因。

### 3.5 完整性：现在够用，升级路径清晰

| 层 | 现在 | 判断 |
|---|---|---|
| 传输 | HTTPS（GitHub） | 在中国这条会退化成「连不上」 → 换源自建后要保证 TLS 不断（见 §6） |
| 内容 | git object hash | ✅ 够 |
| 身份 | 无（仓库 URL 即身份） | ⚠️ 自建源后建议启用 **signed tag**（`git tag -s`）+ 客户端 `git verify-tag`，或退一步只校验 CHANGELOG/VERSION 的 sha256 |
| 私钥 | 无签名私钥 | ✅ 比 Sparkle 的那套简单也更安全（无密钥可偷） |

不要引入 TUF：它是为「多发行者、可轮换密钥、防回滚攻击」设计的，本项目只有一个作者、一个目标机器群，成本远超收益。

### 3.6 无回滚是已知缺口

`pull --ff-only` 只能前进。若某个 `auto` 版本把所有人搞崩，唯一办法是发一个更快的新版本（`--ff-only` 也允许「拉到你没见过的提交」）。可行但被动。

服务端主导（§3.3）可以补：清单里带 `min_version` / `rollback_to`，客户端遇到「启动失败 N 次」时上报并由服务端决定退回。这条留到 P2，先把可达性做对。

---

## 4. 方案矩阵

| 方案 | 客户端改动 | 中国可达 | 新增运维 | 破坏现有保障 | 结论 |
|---|---|---|---|---|---|
| **A. VPS 只读 git 镜像**（nginx + git-http-backend，或 Gitea） | 改 remote 一次（或 `insteadOf`） | 好（腾讯云国际线路） | 一台已有 VPS + 5 分钟 cron | 无（`pull --ff-only` 原样） | ✅ **推荐 P1** |
| **B. Worker 反代 GitHub** | 改 remote 一次 | 中（回源仍是 GitHub，前端是 CF） | 一个 Worker | 无 | ⚠️ 备选：CF→GitHub 抖的时候一起抖 |
| **C. R2 静态裸库（dumb HTTP）** | 改 remote | 好 | 无 | 无 | ❌ clone 逐对象 GET；签名 URL 会过期 |
| **D. 清单 + tar 整包** | 重写引擎 | 好 | 中 | **会丢掉脏文件/journal/锁那一整套** | ❌ 除非放弃 git 语义 |
| **E. 代理注入（`_git` 传 proxy）** | 零 | 好（用户已开代理） | 无 | 无 | ✅ **推荐 P0**，20 行 |
| **F. 多源 fallback（remote 列表）** | 代码 + 配置 | 好 | 低 | 无 | ✅ P2，与 A 互补 |

**为什么 A 优先于 B**：B 的回源还是 GitHub —— 今天出事的是「客户端到 GitHub」，B 能解；但 GitHub 侧限流/故障/区域抖动仍在链路上。A 之后 GitHub 只对 VPS（法兰克福）负责，而 VPS→GitHub 属于你自己的可控链路。

**为什么不做 D**：`update.py` 里 700 行（journal/repair/脏文件/锁）全部建在 git 语义上，换成 tar 覆盖等于重写安全层，收益只是「不再依赖 git」，而 git 在客户端不需要联网安装（macOS 自带 `/usr/bin/git`）。

### 4.1 源分配：不要让所有人都用 VPS，也不要按地区自动判断

前提：两种走法都是**客户端拉**，没有服务端推送（§3.3）。

判定「谁是外国用户」在客户端做不到可靠：`locale`/`timezone` 只反映系统设置（作者本人在爱尔兰、`en_IE`），网络探测会被代理和出差骗过，而且**误判的代价是这个人从此收不到更新**。所以分流只能有二选一形态：**按机器显式配置**，或**按可达性自动降级**。

| 方案 | VPS 挂掉时受影响 | 新增凭证 | 排障矩阵 | 判断 |
|---|---|---|---|---|
| 全员走 VPS | **所有人**（VPS 是新的单点） | 每人都要 htpasswd | 1 条 | ❌ 除非保留 GitHub 做 fallback |
| 只中国走 VPS，美国留 GitHub | 1 人（本来在 GitHub 上也是 100% 不可用 → **严格改进**） | 只发给中国那一台 | 2 条，但每条都短 | ✅ **先这么做** |
| 多源自动降级（P2） | 只要能通一条就没事 | 同上 | 1 条（按路由记日志） | ✅ 终态 |

**结论**：P1 只把**中国那一台**换到 VPS，美国那台不动（GitHub 对美国用户是最短路径，且 11-16 已证明可用）。因为 VPS 是 GitHub 的 `--mirror`，两个入口内容完全一致，不存在版本分叉 —— 所以「换谁」是纯可达性选择，不是架构选择。等中国那台稳定一周，再给美国那台加一个 VPS 备源（P2），而不是换掉 GitHub。

---

## 5. 分阶段落地

### P0（半天，先让今天这类用户能更新）

1. `_git()` 注入代理：复用 `ws_compat.routes()` 的判定（HTTP 优先、SOCKS 次之、直连兜底），转成 `git -c http.proxy=… -c https.proxy=… fetch`。
   - SOCKS 前缀用 `socks5h://`（域名交给代理解析，与 `ws_compat._socks_sock` 一致）。
   - 只在 `fetch`/`pull` 这类网络命令注入，`rev-list`/`status` 不动（避免无谓开销）。
2. `_git()` 加 `env={"GIT_TERMINAL_PROMPT": "0"}`：GUI 下没有任何人可以输密码，宁可直接失败也不要挂到 45 s。
3. `check()` / `offer()` 失败写一行 `_log()`（现在只有 `auto_update()` 写）。日志里带 `error` 原文 + 当前代理路由。
4. 报告加 `blocks.update`：`{check_ok, error, behind, current, latest, route, spawn_ok}`，由 `testmode.py` 采集 —— 这是**今天这件事最缺的**：以后不用靠版本号猜。

### P1（一天，换源）

1. VPS（bldcam，43.131.13.220）建只读镜像：`git clone --mirror` + `fcgiwrap` + `nginx` 的 `git-http-backend` + `htpasswd`，`HEAD → refs/heads/main`，`http.receivepack true`。
2. cron 每 5 分钟从 GitHub 拉一次（VPS 在欧洲，到 GitHub 通）：`git -C /srv/git/lecture-live.git fetch -q --prune origin`。
3. 客户端迁移（见 §6）。
4. `whatsnew.py:44 REPO_URL` 改成自建仓地址（只影响「完整更新日志」按钮的跳转）。

### P2（可选）

1. `remote` 多源：`git remote add mirror …`，`_git fetch` 按 `routes()` 顺序逐个试，用一个 flag 记住哪条通（下次优先）。
2. 服务端主导版本选择：`offer()` 改为读 `https://…/update.json`（由 Worker/VPS 生成，含 `latest`、`min`、`rollback_to`），仍以 git 为下载通道。
3. signed tag + `git verify-tag`（自建源后唯一真正新增的信任面）。

---

## 6. 迁移：存量客户端怎么换源

### 6.1 一次性全局重写（推荐）

在客户端执行一次：

```bash
git config --global url."https://git.bldcam.page/".insteadOf "https://github.com/OUENMING/"
```

之后**所有**既有 clone（包括 `~/lecture-live`）的 `github.com/OUENMING/*` 都会被重写到自建源，`remote` 文本不用改、`pull --ff-only` 语义不变、`@{u}` 与 `refs/remotes/origin/HEAD` 全部照旧。这是本次迁移最省事的做法，也是「不让用户理解 git」的唯一可行路径。

为什么**必须**用 `insteadOf` 而不是 `remote set-url`：改 URL 但 remote 名不变，所以 `@{u}`（`update.upstream_ref()` `update.py:124`）与 `refs/remotes/origin/HEAD`（`default_ref()` `update.py:142`）都不动。若直接 `set-url`，必须再跑一次 `git branch --set-upstream-to=origin/main main`，**忘掉就静默返回 `none`**，看起来和「没人更新」一模一样。

凭证：`credential.helper` 按 host 查，换了 host 就会重新要密码（GUI 里没人能输）。所以一次性把只读 token 内嵌进重写规则，之后零交互：

```bash
# <user>:<read-only-token> —— 每个客户端一个账号，泄漏了只作废那一个
git config --global url."https://ro:<token>@git.bldcam.page/".insteadOf "https://github.com/OUENMING/"
chmod 600 ~/.gitconfig          # 里面现在是明文凭证
```

验证（应看到 `From https://git.bldcam.page/…`）：

```bash
git -C ~/lecture-live config --get-regexp '^url\.'
git -C ~/lecture-live fetch -v 2>&1 | head -3
```

回滚一行：`git config --global --unset-all url."https://git.bldcam.page/".insteadOf`。

**这就是「指定哪台走 VPS」的全部机制**：只在那一台的 `~/.gitconfig` 里加这条。不要按 locale / 时区 / 网络探测自动判断 —— `zh_CN` 只表示系统语言，作者本人是 `en_IE`；判错的代价是那个人从此收不到更新，而且日志里看不出来。

反向风险：`insteadOf` 是全局的，若以后还要从 GitHub 拉别的东西会在暗处被改写。两个缓解：
- 只重写这一个前缀（如上，而不是整个 `github.com/`）；
- 在 `README` 的「回滚」一节写清楚怎么删：`git config --global --unset-all url."https://git.bldcam.page/".insteadOf`。

### 6.2 逐仓改 remote（更显式，但要逐台做）

```bash
git -C ~/lecture-live remote set-url origin https://git.bldcam.page/lecture-live.git
git -C ~/lecture-live fetch
git -C ~/lecture-live symbolic-ref refs/remotes/origin/HEAD   # 必须非空
```

`update.default_ref()`（`update.py:142`）靠 `refs/remotes/<r>/HEAD` 判断「哪条是正式线」，这一步没做好，`offer()` 会静默返回 `none`，更新照样不弹。

### 6.3 新装

`README` 里的 clone 地址直接写自建源；GitHub 只作为作者的 push 目标。

### 6.4 硬约束提醒

存量客户端的 `remote` 是**磁盘上的**，我们没有任何被动通道能改它。要么在 P1 之前发一版「带上迁移脚本」的代码让他点一次，要么 P1 之后每次事故都手动远程教他。前者便宜。

### 6.5 “静默换源”能不能做

技术上可以：新代码里在启动时执行一次 `git config --global url.<mirror>.insteadOf …`。但它要先解决两个真问题：

1. **送达弔论**：干这件事的代码必须通过「那条不通的路」送到他手上。他能拿到这一版的那天，恰好说明 GitHub 是通的 —— 那就顺势换掉，之后不再依赖它。但若那天不通，就永远换不了，**必须留一条手动通道**（否则又回到“没有任何被动通道”）。
2. **凭证**：GitHub 那边用的是 keychain 里的旧 token；换成自建源后 host 变了，`credential.helper` 会要新密码，而 GUI 里没人能输 → **静默换源直接变成永久失败**。所以静默换源 = 代码里带一个**只读**镜像凭证（泄了也仅限于读代码，而代码本来就是发给他们的）。
3. **项目规矩**：`docs/PLAN-update-mechanism.md` 硬约束是「不擅自改用户环境」。git config 比装依赖轻，但仍是改环境。

**推荐形态：静默降级，不静默改写。**

```python
# _git("fetch") 失败（超时/401/443）→ 用运行时参数重试一次，不落盘
_git("-c", f'url.{MIRROR}/.insteadOf={UPSTREAM_PREFIX}/',
     "-c", f'http.extraHeader=Authorization: Basic {RO_MIRROR_AUTH}',
     "fetch", "--quiet", timeout=20)
```

好处：用户零操作、环境零改动、天然只能当 fallback（GitHub 能通就不走镜像）、排障时 `route` 写进日志即可、`CLASSLIVE_NO_MIRROR=1` 一开关就关。代价：每次 fetch 失败后多一次重试（最长 +20 s，只在退出进程/后台线程里），以及代码里多一个只读凭证。

**真正的静默换源（写 `.gitconfig`）不建议做**，只用运行时参数就够了——效果一样，风险小一个量级。

---

## 7. 验收

| 项 | 判据 |
|---|---|
| P0-1 代理 | 用 `tests/test_ws_proxy.py` 的假代理手法，把 `http_proxy` 指向一个假的 socks5 口 + 一个活的 http 口，断言 `_git` 收到 `-c http.proxy=<活的>`；关代理时断言不带 `-c` |
| P0-2 不挂死 | `GIT_TERMINAL_PROMPT=0` 生效：仓库配一个 401 的远端，45 s 内返回失败而不是等输入 |
| P0-3 可观测 | `check()` 失败后 `~/Library/Logs/ClassLive/update.log` 有行；报告 `blocks.update.check_ok=false` 且 `route` 非空 |
| P1-1 换源 | 在**一台干净目录**里 `clone` 自建源 → `VERSION` 与 GitHub 一致；`git fetch` 两次，第二次的增量对象数 < 20 |
| P1-2 迁移后不退化 | `offer()` 返回 `available`（不是 `none`）；`pull()` 在脏工作区仍 `blocked`、仍点名文件 |
| P1-3 中国可达 | 让朋友跑 `git -C ~/lecture-live fetch; echo $?`，期望 0，且 `update.log` 出现「已是最新」而不是 `fetch 失败` |

`tests/test_update.py` 不在默认闸门里（PLAN §8），P0 的用例要一起手工跑。

---

## 8. 风险与未决

| 风险 | 说明 | 缓解 |
|---|---|---|
| 单点从 GitHub 变成 VPS | VPS 挂 = 全员不能更新（但**不影响上课**，更新不是启动依赖） | cron 镜像 + GitHub 保留为 fallback remote（P2） |
| VPS 在中国大陆的线路 | 腾讯云国际站（法兰克福）→ 中国用户延迟约 200–250 ms，fetch 几十 KB 可接受 | 实测后再定；必要时换香港轻量 |
| Basic Auth 走明文段 | CF Flexible SSL：CF→源站（法兰克福）是 HTTP | 上 Origin Certificate + Full (strict)；或直接用 `https://` 直连源站并只放行 CF 段 |
| 定制 `update.py` 的安全层被后续改动磨损 | 700 行边界逻辑没有强制闸门（`tests/test_update.py` 不在默认流程） | 把 P0 用例加入 `cl test` |
| `insteadOf` 暗改 | 全局配置 | 只重写单一前缀 + README 写清回滚 |
| 无回滚 | 见 §3.6 | P2 由服务端下发 `rollback_to` |

---

## 9. 来源

- 本仓库：`docs/PLAN-update-mechanism.md`（2026-09-24/25，含 Mozilla「We don't want to delay shutdown」、Firefox 独立进程、"parent process never touches the network"、Claude Code bug #14791/#71524 的对照）；`update.py`；`ws_compat.py:38-50`（SOCKS 病根与三条路由）；`fetch_model.py:44-45`（`ghproxy.net` / `gh-proxy.com` / `hf-mirror.com` 已有镜像兜底）。
- Tauri Updater 文档（`endpoints` 数组；只有非 2XX 才试下一个；dynamic update server 可覆盖版本比较以支持回滚）https://tauri.app/plugin/updater ｜ 2026-11-17 取
- 微软 Huabu `desktop-auto-update.md`（检查失败 vs 下载失败的分层处置：后台轮询只写 console，手动检查才回 toast，不亮常驻红标）https://github.com/microsoft/Huabu/blob/main/docs/architecture/desktop-auto-update.md ｜ 2026-11-17 取
- Sparkle 文档（EdDSA/ed25519 签名、binary delta updates、签名私钥不得放在托管服务器上）https://sparkle-project.org/documentation/ ｜ 2026-11-17 取
- Cloudflare China Network（中国大陆网络为独立企业产品，"built with strategic partners"；免费版不经境內节点为推断，未见官方逐字表述）https://www.cloudflare.com/network/china/ ｜ 2026-11-17 取
- 实测：`curl -I https://asr.bldcam.page` → `server: cloudflare`、`cf-ray: …-DUB`（都柏林）；`cl_admin.py stats --days 7`；11-17 报告 `blocks.stream` / `blocks.region_probe`。
