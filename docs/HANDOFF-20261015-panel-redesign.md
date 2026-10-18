# 交接 2026-10-15 · 通行码面板「数据呈现」改版（分支 `next-395`，**改完没提交**）

> 接手先读 §0 和 §6。§6 是**判掉的**（有意不做的事），免得你把它们再「修」一遍。
> 只改了三个文件：`scripts/cl_admin_ui.py` / `scripts/cl_admin_ui.html` / `tests/test_cl_admin_ui.py`，
> 都还是**工作区改动**（`git status` 可查）。没提交、没推、没发版。

## §0 一句话现状

面板的「数字太多、看不出谁在干什么」这件事做完了：KPI 带环比、每人改成日历条、用户表按「谁 → 还活着吗 →
干了什么」重排、抽屉顶上四个大数、**服务健康里的「部署版本」从一张四列表换成部署时间轴**（部署时刻改从
Cloudflare deployments API 取权威值，不再拿「第一次有请求的分钟」冒充）；同一个 zone 上挂的摄影站 / 图床 /
数据站已从面板里剔除。

闸门与单测全绿（§5 贴了输出）。**唯一没验到的是「肉眼」**：深浅两主题的**截图没拍成** —— 显示器当时在睡
（`screencapture` 出全黑、`kimi-cu list_windows` 返回空），而「不擅自改系统设置」这条硬规矩不允许我自己唤醒它。
DOM 层的计算样式是实测过的（§5.5），但发版前**必须补两张图**。

## §1 作者的要求（原话，别改口径）

这是他这轮提的原话，我的取舍都对着这几条：

1. 「我需要你整合一下数据这样子太不直观了…把没有用的剔除出去原有用的整合在一起做成可以直观体现比如说这个人录了多少个字转录了多少次用了什么模型数字也好可视化也好…**一定要热门最新的高级推荐**。」
2. 「**原本的设计语言不变**我是说你的数据呈现方式可以进行一些现代化…」
3. 「没有用的东西指的是因为我这个网站它有挂载了其他的东西嘛比如说我的摄影及网站也在上面把这些无关的去掉然后有关的跟 classlive 项目相关的呢就可以用一些能比较直观的方式呈现。**它可以详细但是让人能看得明白。**」
4. 参考：Behance 的 dashboard 检索页、Dribbble 的 trending-dashboard（两个链接，作为版式参考）。
5. 「录了多少个字」→ 当场拍板**换成「录了多久 + 段数」**。理由不是口味：Worker 只记数字、**从不存文本**，
   字数在这个架构里测不出来，造一个字段就是编数。
6. 「可以保留，但是**能不能再直观一点**啊？比如说**服务健康里面那些版本是什么**。就是这些可以稍微简化一下或者是用可视化来做。」
7. 「然后就是**666 那个 key 最后的活跃时间确定是那个点吗**？」
8. 「还有那个 GROK 它是**轮询**的嘛但是它那个用量更新的时间一个小时还没有更新吗？」

第 2 条与第 1 条是有张力的（要现代化、又不许动设计语言），本轮的实际口径是：
**token / 字号 / 间距 / 圆角 / 阴影一个字都没改，改的全是「同样这些零件怎么摆」** —— 见 §3.10、§6。

## §2 改了什么（看得见的部分）

| 位置 | 之前 | 现在 |
|---|---|---|
| 顶部 KPI 四格 | 只有大数 | 大数 + **环比**（`↑ 23% · 前 7 天`，中性色，不跟状态色抢） |
| 「在用的码」的走势 | 「这天有没有记录」的 0/1（读不出东西） | **这天有用量的码的个数** |
| 每人用量 | 每人一根总条（数字与下面用户表同一列重复，且看不出「哪天在用」） | **「谁在什么时候用」日历条**：一行一人、一格一天、越深越多（单一色相，5 档），悬停出那天的数 |
| 用户表 | 名字 / 状态 / 设备 / 到期 / 最后在线 / 每日转写迷你曲线 / 转写分 / 段 / 翻译 / 花费 | 按「**是谁 → 还活着吗 → 干了什么**（用了什么模型 / 录了多久 / 段 / 翻译 / 花费）→ 设备、到期、最后在线」重排；**新增「用了什么模型」列**（细构成条 + 占最多那个的占比与名字）；删掉迷你曲线（日历条已经画得更好） |
| 抽屉（点开一个码） | 一片小字 kv | 顶上**四个大数**：录了多久 / 转写段数 / 翻译次数 / 花费；kv 里剩下的只是算账（token 怎么分、缓存命中） |
| 服务健康 ② 部署 | 一张表：版本 / 第一次 / 最后一次有请求的分钟 / 请求 | **部署时间轴**：一根条按「这一版当了多久的班」切开，段里写版本号（够宽）/ 序号（窄），现在在岗的那段蓝底 + 1px 内描边；下面是 5 列表：`# / 版本 / 部署于 / 当了多久 / 请求` |
| 服务健康 ⑤ 节点 | 12 行常驻 | 折起来（`details.sub`） |
| 服务健康 ⑥ 站点 | 「bldcam.page 整站」+「出错最多的路径」表 | 只列 **ClassLive 自己的两个主机名**；路径表整张删了（实测 15 行全是扫描器探针，见 §3.9） |
| 上传的课 | 常驻大表 | 折起来，标题里带「N 节 · 别人的 M 节 · 共 X」 |
| Groq 额度那句 | 「Groq 1 小时前报的数」 | 「Groq **21:17** 回报」+ 说明那个钟点是**底数**的时刻、不是面板多久没刷新（第 8 条问题的正解） |

删掉的三样（都是「同一件事说两遍以上」）：**Durable Objects 那一行**（账本请求量比 Worker 小两个数量级，
且「通行码用了多少」在用户表和额度条里已经算过）、**CPU p50 / p99 一列**（这个量级不是任何人会动手的东西，
真超了会以 `exceededCpu` 出现在状态行里）、**「每日转写」迷你曲线**。

## §3 为什么这么做（含那些「看起来怪但有理由」的地方）

**3.1 部署时刻为什么要另开一个 API。** AE 只给「某个版本第一次有请求的分钟」。低流量版本可能几小时才来第一个
请求，用它推断会把部署时刻说得晚得多。**实测**：`da80257c` 真部署在 10-13 04:21（本机），第一次有请求是
10-14 00:40（本机）—— 差 20 小时 19 分，**日期都错了**。`wrangler deploy` 一次产生一个版本号，
`GET /accounts/{acct}/workers/scripts/{script}/deployments` 是权威答案，只读 token 恰好也有 Workers Scripts: Read。

**3.2 为什么必须自己按时间重排。** 上游 `versions` 是按 `first`（第一次有请求的分钟）排的，**那顺序可能跟部署
顺序不一样**。照搬上游顺序会把段画到错的位置、接缝还是断的。`cluiDepSegs` 自己按 `s` 重排（稳定排序，同起点保原序）。

**3.3 为什么 `created_on` 要掐到秒再给出去。** 线上真的带微秒（`2026-10-13T03:21:49.863119Z`）。ISO 只保证 3 位
小数，多出来的位数属于「实现自定义」：V8 收（node v22 实测解析成同一个毫秒数），别的引擎没有保证。页面那边是
`Date.parse` 吃它，**解析失败会静默变 NaN** → 整根时间轴一起退化成「不知道」。所以服务端 `at[:19] + "Z"`，不赌解析器宽容。

**3.4 为什么按完整版本号对上。** 对外只给前 8 位，映射键必须是完整 id（`aaaaaaaa-1`）。对不上会**静默**退回空串，
所以判据里专门钉了「`deployed_at` 真的被填上」这一步。

**3.5 为什么部署条的右端是「现在」而不是 24 个整点。** 否则刚部署的那一版会落在这根轴之外、**整段消失**，
而且条上会把**上一版**错标成「现在还是它」—— 那是比「两轴对不齐」严重得多的错。

**3.6 为什么 `live` 按「最后一个」判，而不是 `e >= t1`。** 读不到部署时刻时，后两版的段起点都落在窗口最后一
分钟、尾都是 `t1`，按 `e >= t1` 判会**同时亮两格**。判据 `test_最后一段才算_live` 就是钉这个的。

**3.7 图上的橙虚线与部署条的段缝对不上 —— 这是有意的，改之前先读这句。** 上面那张每小时耗时图左边留 44px 给
纵轴刻度、右边 8px，固定铺满 24 个整点；部署条一分不留、铺到「现在」。偏右量 = `44 − 19.7·f` px（`f` = 这版部署
时刻在 24 h 里的位置），解析范围 **24.5（窗口右端）～44px（左端）**；本轮条宽 1200px、8 条线**实测 24.5～39.0px**。
**不是画错了，是留白和轴长不同** —— 详见 §6.1（为什么没去「顺手对齐」）。

**3.8 为什么地图上不画「窗口起点就在岗」的那一版。** 它的线会贴着左边缘，什么也说明不了。

**3.9 站点表为什么只列两个主机名。** 第 3 条要求的「没有用的东西」就是它：同一个 zone 上还挂着摄影站
（`bldcam.page` / `www`）、图床（`cdn`）、数据站（`data`）。过滤放在**后端** `CLASSLIVE_HOSTS`（`cl_admin_ui.py`），
前端不再兜一层（两层判据会各自漂移）。顺带把「出错最多的路径」整张表删了：**实测**那 15 行全是扫描器探针
（`/.env.stage`、`/phpinfo.php~`、`/backup.tbz2`…），一条都要不动；真出错在「上游」表和 4xx/5xx 两列里已经看得见。

**3.10 为什么进度条从 `width` 改成 `transform: scaleX()`。** 这页每分钟自己刷一次，刷新那一刻进度条正在动；
动 `width` 是布局属性、每帧重排。`scaleX` 只走合成，肉眼一模一样。这是本轮唯一一处「动了渲染方式但没动外观」的改法。

**3.11 为什么颜色只给一种色相（日历条）/ 跟模型走（模型条）。** dataviz 的反模式：颜色跟着**名次**走 —— 换个区间、
同一个人就换色。日历条用单一色相 + 深浅（量），模型条用 `MODEL_COLOR`（`whisper-large-v3` 在哪都是同一个色）。

**3.12 为什么模型分布下面必须写那句 hint。** `models` 字段 2026-10-14 才开始记，**只覆盖那之后转的段**，而上面的
「转写段数」是全部 —— 两个数天然不一样（**实测** bldus666666 是 244 对 717）。不说明就像算错了。老记录读出来是 `{}`，
这时写「**没记**」不写「没用过」—— 是两回事。

## §4 逐文件

### 4.1 `scripts/cl_admin_ui.py`（+67）

- `CLASSLIVE_HOSTS = ("asr.bldcam.page", "ingest.bldcam.page")` —— 新常量，注释里记了实测对照
  （24 h：ClassLive 6981+263 对 摄影站 874+52+527+2）。**要加新的 ClassLive 入口改这里。**
- `_HEALTH_Q` 删掉 `path` 那段查询（连 GraphQL 一起删，不是只不显示）。
- `DEPLOY_API` + `_deploys(tok, acct, get=None)` —— 新。回 `{完整版本号: ISO}`；**回 `None` = 读不到**
  （权限不够 / 形状不认识 / 抛异常）→ `deploys_error` 有话、前端退回按请求推断并如实标出来。
  **只取 `created_on` 与 `version_id`：响应里的 `author_email` 不进这个映射**（判据用 `SECRET@example.com` 钉着）。
- `cf_health()`：`versions` 每条加 `deployed_at`（读不到 = 空串）；`zone.hosts` 按 `CLASSLIVE_HOSTS` 过滤；
  `zone.bad_paths` 连同字段一起删；返回体加 `deploys_error`。

### 4.2 `scripts/cl_admin_ui.html`（+362 / −85）

- CSS 新增（**全是既有 token**）：`details.sub`（卡片里的小节折叠，比 `details.prices` 小一档）、
  `.cal` / `.cal .cells i`（日历条；格子圆角 2px —— 与既有的微型标识同一档）、`.depbar` / `.depbar i.live` /
  `.depax`、`.kpi .k .d`（环比）、`.hero` / `.hero b small`（抽屉四个大数）；`.prog` 改 `transform`。
- 新函数：`fmtDur`（秒 → `3h54m`）、`stackMini`（表格里的 6px 构成条）、`fold`（`details.sub`）、
  `calStrip`、`deltaOf`、`cluiDepSegs`（哨兵里，纯函数）、`deployStrip`、`renderPerDay`（取代 `renderPerUser`）、`depAt`。
- `MODEL_NAME` / `MODEL_COLOR` / `MODEL_ORDER` 三个常量。
- `load()`：`stats` 改取 `days * 2`，后一半 → `state.days_data`、前一半 → `state.prev_data`（环比用）。
  ⚠️ 后端 `days` 上限 366，所以 366 天这一档**没有环比**，前端如实写「没有可比」。
- 新增 `#kpnote`：上一个同样长的区间**一条记录都没有**时，只在**一条注**里说一次，不往四个格子里各塞一句一样的。

### 4.3 `tests/test_cl_admin_ui.py`（+197，30 → 40 条）

- 新哨兵 `/* >>>CLUI-DEP-START<<< */ … END`，与既有的 `CLUI-COST` 同一套路：Python 抠出**真源码**喂 `node -e` 跑，
  不是手抄一份。`_dep_src()` 里带两条自检（哨兵找不到就报错；抠出来 >1400 字节或没有那个函数名就报错）。
- `DepStripCase` 8 条：段首尾相接且盖满（故意打乱喂进去）/ 部署时刻压过首次请求 / 读不到就退回 first 并标「不知道」/
  窗口末尾及之后才上的版本不画 / 都没得读时也首尾相接 / 最后一段才算 live / 空输入不炸 / **判据抠的是真源码**。
- `Health` 加 2 条：读不到部署时刻如实标出来不编 / 读部署链炸了也不连累别的（各挂各的端点）。
- 既有那条汇总用例扩了：`DEP` 夹具（形状是**字典**不是列表、带微秒、带 `SECRET@example.com`）、
  站点表钉住「只有 `CLASSLIVE_HOSTS` 里的主机名能进」、`assertNotIn("bad_paths", …)`。

## §5 验证（跑出来的原文）

**5.1 完成判据**（`./ClassLive.app/Contents/MacOS/python scripts/check.py`）：

```
测试（55 个文件，HOME 已隔离）
  ✅ 其余 55 个通过
✅ ruff：没有问题
✅ pyright（两条规则）：4 条全在基线里
✅ 写端：sessions/、~/.classlive/、Lectures/、git status 都没变
✅ 全部通过（18.2s）
```

**5.2 面板单测**：`Ran 40 tests in 1.868s` → `OK`（改之前 30 条）。

**5.3 变异测试：11/11 全被抓**（改 `cluiDepSegs` 里的一行 → 跑 `DepStripCase` → 改回来）：
起点不钳窗口 / 段尾恒为 `t1` / 总是退回 `first` / 读不到也说知道 / `at >= t1` 反向 / 完全不滤 /
不重排 / 序号从 0 起 / `dur` 从 `first` 起算 / `live` 改成 `e >= t1` / `live` 全 `true`。
> ⚠️ 跑变异时要**排除** `test_判据抠的是真源码`（它测的是抠源码这件事，变异改不动它），否则算出来的分母是假的。

**5.4 线上实况**（`POST /api/health`，窗口 2026-10-13T22:40:24Z → 2026-10-14T22:40:24Z，`deploys_error` 为空，
8 个版本）—— 当天下午另有一次同样形状的读，两天口径一致：

```
da80257c  部署 10-13T03:21:49Z  3344 req     fef0f708  13:11:44Z  3300
2c70fe63  部署 10-14T10:41:42Z    11 req     4c84cc3d  20:31:08Z    89
403e4d10  11:21:03Z               69 req     65a31244  21:05:31Z    47
986c8f9d  11:46:27Z               56 req     4110c1c5  21:30:24Z   174  ← 在岗
```

**5.5 DOM 实测**（浏览器里读计算样式；**注意：这是清理最后几处注释之前**的一次渲染，形状与类名此后没再动）。
条宽 1200px，段宽 `613, 32, 20, 69, 353, 28, 20, 52`（+7×2px 缝 ≈ 1201），标签 `da80257c` / `2`…`6` / `fef0f708` / `7` / `8`，
`live` 在第 8 段，tooltip 文案正确，无溢出。

- 浅色：普通 `rgb(143,143,143)` on `rgb(242,242,242)`；live `rgb(0,114,245)` on `rgba(15,98,254,.1)` + 内描边 `rgb(0,114,245)`。
- 深色：普通 `rgb(110,110,110)` on `rgb(28,28,28)`；live `rgb(50,145,255)` on `rgba(69,137,255,.16)` + 内描边 `rgb(50,145,255)`。
- 两主题都是 `border-radius: 2px`、`11px/22px ui-monospace`、`gap 2px`、`height 22px`；轴 `23:00 / 11:17 / 23:35`；深色 body `rgb(10,10,10)`。

**5.6 `impeccable detect scripts/cl_admin_ui.html` → 退出码 0、无输出**（之前报的
`repeating-stripes-gradient` 已随 `.unk` 斜纹一起删掉；`tiny-text` / `low-contrast` / `flat-type-hierarchy` / `side-tab`
四条是**已登记的豁免**，理由在 `.impeccable/config.json`，**对作者是公开的**；`.impeccable/` 未跟踪，别提交）。

**5.7 「666 那个 key 最后活跃时间是不是那个点」—— 是，两个独立来源对上了**：

| 来源 | 值 | 本机时刻 |
|---|---|---|
| `POST /api/list` → `cl-bldus666666-0276189ca5129773` 的 `last_seen` | `1792009054601` | 2026-10-14 **21:17:34** |
| `POST /api/r2` → 桶里那节课 `71eabc0b5dfc603a` 的 `last`（4 个对象 / 3 个音频 / 57,762,302 B） | `2026-10-14T20:18:01.611Z` | 2026-10-14 **21:18:01** |

R2 比 `last_seen` 晚 **27 秒**，方向也对（得先转写完才上传）。**但这个口径要记住**（见 §8.1）：
`last_seen` 不是「最后一次请求」，是「最后一次**被放行**的请求」。

## §6 判掉的（没有改，理由在此，免得下一个人再「修」一次）

**6.1 没有把部署条的轴对齐到那张 24 小时图**（对齐了橙虚线就落在段缝上）。要对齐只能把 `t1` 设成 `t0 + 24h`，
那样 `cluiDepSegs` 里 `at >= t1` 那行会**丢掉最近 59 分钟内刚部署的那一版**，然后上一版被错标成 `live`
（「现在还是它」）。**用一个正确性 bug 换一个 24～44px 的美观问题，不做。**

**6.2 没有加「录了多少个字」。** Worker 从头到尾只存数字（`asr_s` / 段数 / token），没有文本，字数测不出来。
作者已拍板用「录了多久 + 段数」。**谁要加这个字段，先想清楚数据从哪来。**

**6.3 没有把部署条的右端标签换成固定的「现在」二字。** 那根轴是按**取数那一刻**画的；标签写死「现在」的话，
一个开着不动的旧标签页过一小时还在说「现在」，是在撒谎。所以右端永远印**具体钟点**。

**6.4 「读不到部署时刻」是删掉斜纹、如实标出来，不是把斜纹压成静默。** 原来是给未知段的格子加 135° 斜纹
（`repeating-linear-gradient`），impeccable 报 `repeating-stripes-gradient`；而且「不知道部署时刻」这件事是
**整体**状态（要么全知道要么全不知道），逐格画斜纹没有信息量。现在：警告色的 hint 行 + 表格每行写「不知道」。

## §7 还剩什么

1. **补两张截图（深浅各一）—— 发版前必须做。** 显示器当时在睡，唤醒是作者的决定（§9.3 有诊断法）。
2. **提交 / 推送 / 发版都还没做**，也没动 `CHANGELOG.md`。真要发 3.9.7，按 `.claude/rules/release.md` 一步步来、
   每步给作者点头（「推送前先给看效果」是硬规矩 —— 所以第 1 条得先做完）。
3. `docs/README.md` 的入口表已经加了一行指向本文件。仓库根的 `CLAUDE.md` 文件地图里**没有面板这一行**
   （`scripts/cl_admin_ui.*` 只在 `entry_launch.py` 那行提了半句）—— 要不要补一行由作者定，我没动它。
4. 本轮**没有碰**：Worker 侧（`server/asr-worker/`）、上传 Worker、`tests/` 之外的任何测试、
   `docs/HANDBOOK.md §10` 的账。

## §8 顺手查清的两件（免得下一个人重新查）

**8.1 `last_seen` 的准确口径。** 它不是「最后一次请求」，是「最后一次**被放行**的请求」——
`server/asr-worker/src/passes-core.js` 里 `devs[install].last = now` 位于**额度判断与设备数判断之后**，
被额度拒掉、被设备数拒掉的请求**不会**刷新它（这是 2026-10-12 全仓审查特意调过来的顺序）。
所以「最后在线 21:17」不等于「21:17 之后就没连过」。

**8.2 面板的 HTTP 形状（自己验数据时天天要用的）。** 所有 `/api/*` 都是 **POST-only**：`GET /api/health`
只会拿到 `{"error":"not found"}`（`do_GET` 只认 `/` 与 `/index.html`）。要带 `X-ClassLive-Admin: 1`，
Host 必须是环回。**HTML 每次请求现读**（改了就生效），**Python 改了要重启进程**。

## §9 踩过的坑（跨项目可复用）

1. **后台标签页里 `requestAnimationFrame` 不跑** → SVG 图一根线都没画，我一度以为部署标记没生成。
   查 DOM 时看到每个 `svg` 只有 2 个子节点、0 条 line，才反应过来是标签页 hidden。
   （同族：hidden 标签里连 `fetch` 到 localhost 都会静默挂住。）
2. **别对已经加载好的页面 `location.reload()`** —— 我把现场毁了，重载 + 异步等待把工具预算烧穿，
   之后连 `/api/health` 都挂。**要验数据用 shell 里的 `curl`，不要用那个标签页。**
3. **显示器睡着时 `screencapture` 出全黑**，而黑图**看起来像**「深色主题渲染成功」。诊断法：先拍一张基线、
   比字节数，连续同字节 = 空图；`mcp__kimi-cu__list_windows` 返回 `[]` 也是同一个信号。
   **唤醒显示器要问作者**（不改系统设置）。
4. **注释里的因果断言也要过量具。** 我在注释里写了「橙虚线正落在段缝上」，实测偏 24.5～39px —— 被自己的
   量具打脸，改了两处注释。**「实测」两个字后面必须是真测过的数**（本轮为此改了三处注释的措辞）。
5. **变异测试要排除「抠源码」那条用例**，否则分母是假的（§5.3 末尾）。
6. **本机没装 `pytest`**（捆绑的 Python 里 `No module named pytest`）→ 用
   `python -m unittest tests.test_cl_admin_ui -v`。
7. **面板的 `/api/cf` 里没有逐码列表**：`state.codes` 来自 `api("list")`（`load()` 里 `Promise.all` 的 `r[0]`），
   别把顺序读错（我读错过一次）。

## §10 命令备忘

```sh
cd /Users/owen/lecture-live-stream

# 起面板（改 HTML 不用重启，改 Python 要）
./ClassLive.app/Contents/MacOS/python scripts/cl_admin_ui.py            # → http://127.0.0.1:8770
#   深色：http://127.0.0.1:8770/?theme=dark      指定展开某个码：?open=<名字>

# 自己验一个接口（POST + 闸门头 + 环回 Host）
curl -s -X POST -H 'X-ClassLive-Admin: 1' -H 'Content-Type: application/json' \
     -d '{}' http://127.0.0.1:8770/api/health | python3 -m json.tool | head -40

# 只看部署链（不经过面板，用同一个只读 token）
tok=$(cat ~/.classlive/cf-analytics-token 2>/dev/null | head -1)   # 凭证由面板服务端读，别贴进聊天
#   acct 在 _cf_creds() 里；手验时直接看 /api/health 的 versions[].deployed_at 更快

# 单测（本机没有 pytest）
./ClassLive.app/Contents/MacOS/python -m unittest tests.test_cl_admin_ui -v

# 完成判据 / 设计检查
./ClassLive.app/Contents/MacOS/python scripts/check.py
~/.claude/skills/impeccable/scripts/impeccable detect scripts/cl_admin_ui.html
```

---

**状态行**：`next-395` 上三个文件改了、**没提交**；`origin/main..HEAD` 有 2 个提交（`ea0cdd6`、`103a71e`，
是另一个窗口做的服务健康视图与逐请求事件，**不在这轮的账上**，但服务健康这个区块本身是 `103a71e` 建的）。
截图欠着，发版没动。

---

## 补记 · 2026-10-15 凌晨（第二个窗口：精简 + 提速）

**提速**（`cl_admin_ui.py`）：
- 慢的五个（`cf` `r2` `health` `logs` `ae`）走 `cached()`：≤ 55 s 直接给；≤ 15 min 先给旧的、后台重取；再旧同步等。
  出错不进缓存。`{"fresh": true}` = 手动「刷新」，当场重取。服务一起来 `prewarm()` 各取一遍。
- `worker_logs` 四条 telemetry 查询并发；`cf_health` 的部署链与 GraphQL 并发，`_zone_id` 记在进程里。
- `cf_health` 多一条 `all:` 别名 → `overall`（整个窗口的 p50/p95，不拿各小时分位数平均）。
- 实测（同一台机、同一时刻的线上数）：logs 9.13 → 3.15 s（fresh）/ < 1 ms（缓存）；health 2.10 → 1.09 s；
  页面首屏不再等慢接口（原来 `Promise.all` 等 logs 9 s）。

**精简**（`cl_admin_ui.html`）：
- 顺序：KPI → 用户 → 用量图 → 额度 → **服务** → 价目表。
- 「Cloudflare」+「服务健康」并成「服务」：一行五个数 + 每小时耗时 + 部署条；其余七节折叠，摘要行带一个关键数，
  开合状态在自动刷新后保留（`fold(…, key)` + `state.folds`）。
- 长说明收进 `title`（额度口径、节点 ≠ 人在哪、墙钟定义）。抽屉删了两处重复（真实音频秒、「这笔钱怎么来的」），
  客户端版本没有就不画。
- 本节 §6 的「有意不做」一条没动。

---

## 补记 · 2026-10-18（3.10：Soniox / OpenRouter / 测试模式上板）

3.10 开始记三样新东西（Soniox 实时流、OpenRouter 快速通道、客户端版本），测试模式的报告里也多了几块
（`stream` / `region_probe` / `duty` / `device` / `energy` / `capture` …）。这一轮把它们摆上面板。

**顺序**：KPI → 用户 → 用量图 → 额度 → **平台** → 服务 → **3.10 新数据** → **测试模式** → 价目表。
设计语言一个字没动（复用 `hero` / `fold` / `mkTable` / `hBars` / `sparkline`），零外链。

**三段各显示什么、数据从哪来**：

| 段 | 显示 | 来源 |
|---|---|---|
| 平台 | 一眼五格：Soniox 7 天花费 · 并发、OpenRouter 7 天花费 · 请求、3.10.0 普及。两张卡：Soniox 分区花费 / 走势 / **对账** / 余额 / 按模型；OpenRouter 余额 + runway + 按模型 + 快速通道一行 | Soniox `/v1/usage/summary` + `/v1/concurrency-limits` + `/v1/usage-logs` + `/v1/organization/balance`；OpenRouter `/credits` + `/analytics/meta` + `/analytics/query`；普及率来自 AE |
| 3.10 新数据 | 五个折叠：Soniox 实时转写（区×结果、报告 stream 汇总）、OpenRouter 快速通道（命中率 / 超时 / 首字 p50）、客户端版本普及、课后笔记（次数 / token / 花费）、「一些事儿」·设备·能耗·收音断档 | 前四样 AE（`/api/ae310`）；第五样上传桶的 `report.json`（`/api/reports`） |
| 测试模式 | 每节报告一行：Soniox 引擎（退回原因）/ 封口句 / 上移 p50 / 术语 vs 矫正率 / 块数。**只有测试模式**写报告并上传 —— 报告在桶里 = 这节是测试模式 | `/api/reports` |

**对外接口（都本机 GET，key 从不进响应）**：
- Soniox：项目 key `~/.classlive/soniox-token`（可加 `soniox-jp-token` / `soniox-eu-token`，按区各查）；
  组织余额另要 `~/.classlive/soniox-org-token`（**没有就写「余额看不到」**，项目 key 查余额回 403）。
  官方 2026-10-18 核过：`/v1/usage/summary`、`/v1/usage-logs`（`client_reference_id` = 码名，Soniox 侧真值）、
  `/v1/concurrency-limits`（上限在建在**组织**档，实测项目档是 null）、`/v1/organization/balance`。
- OpenRouter：**只用管理 key** `~/.classlive/openrouter-mgmt-token`（普通推理 key 打 analytics 回 403）。
  先 `/analytics/meta` 拿指标/维度，再 `/analytics/query` **只查它真有的**（查到不存在的名字会 400）。
  **不接** Observability 的 destinations（那把请求内容推给 Langfuse 等 —— 隐私不行）。

**报告摘要是白名单**（`_report_summary`）：只取数字与枚举，`segments` / `events` / 课堂文本 / `audio_files` /
`session_file` / `note_file` / `vad_report` 一律不进响应；单份坏掉只记一条错、不连累别的。30 份对象 **8 路并发**
取（实测 ~2 s；串行时能到十几秒）。

**「菠萝丁」暗号**就是 A/B 开关（`stream_gate.force_local`：切回本地草稿 + 云端定稿），写进报告的
`blocks.stream.fallback_reason`，所以测试模式那张表能看出哪节是 A/B 的 Soniox 半边。

**判据**：`tests/test_cl_admin_ui.py` +12 条（`AE310` / `ReportSummary` / `R2Reports` / `SonioxAccount` /
`OpenRouterAccount`，全注入、不联网）。变异检查 5/5 全被抓住（改 `_report_summary` 漏 segments、挑错键、
换 AE 路径、日志计数 +0、去掉指标交集过滤 → 各自变红）。`check.py` 103 通过 / 1 跳过（`test_extract` 缺本机
样本）/ ruff · pyright 干净。深浅两主题截图见 `/private/tmp/.../scratchpad/dash/shots/`（本轮临时目录）。

**没做 / 想做的**：Worker 没记 OpenRouter 的 `generation_id`，所以拿不到逐次生成的花费与 provider
—— 见下面的「Worker 侧建议」；面板现在只有账号级 `/analytics/query` 与 AE 的上游串。
