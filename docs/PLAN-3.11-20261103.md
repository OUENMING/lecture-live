# 3.11 计划（2026-11-03）：收尾后台化 · 断句修复 · UI 统一

> 状态：计划（未开工）｜目标版本：3.11
> 关联：`docs/PLAN-3.10.6-fixes.md`（同日凌晨记的 6 条修复，作为 3.11 第一批）
> 详细设计：`docs/PLAN-note-background-20261103.md`（收尾拆进程 + 收尾卡）
> 本机优先：所有改动先在 `~/lecture-live` 跑通、装到本机自用，不直接发版。

---

## 0 今天记录的问题总表

级别：P0 = 挡上课/丢数据；P1 = 明显影响体验；P2 = 打磨。

| ID | 现象 | 根因（已核实） | 代码位置 | 级别 |
|---|---|---|---|---|
| **A1** | 下课后 15 分 49 秒才能开第二节课 | 笔记生成在录课进程里，`instance.lock` 一直持有到 `run()` 返回；双击 `.app` 只触发 `_reopen`，不开面板 | `main.py:1832`、`overlay.py:6388` | P0 |
| **A2** | 收尾卡只有「正在生成笔记…」，还可能一卡十几分钟 | 进度回调只发状态词；`on_progress("review", ci)` 在 `fut.result()` **之前**发，报的是「正在等第 ci 块」不是「已完成 ci 块」 | `overlay.py:6761`、`note_v2.py:1781` | P1 |
| **A3** | 单块最坏 3×300 秒，整段复习层能拖 15 分钟 | `CALL_DEADLINE_S=300` + `one()` 最多 3 次重试，收尾没有总预算 | `llm_client.py:56`、`note_v2.py:1755-1770` | P1 |
| **A4** | 复习层之后，重点句 / 讲解 / 课前回顾串行跑 | 三层互相独立，但 `close()` 里顺序调用 | `obsidian_writer.py:2120-2175` | P2 |
| **A5** | 生成中能打开半成品笔记（少一层讲解） | 复习层之前先写基础版到 `Lectures/` + 桌面 HTML | `obsidian_writer.py:2079-2082` | P1 |
| **A6** | 点「完成 / 关闭」后窗口卡 5 秒左右 | ack 之后的两处 `join` 在主线程上，期间没有 `pump`：主上传 `join(10)`、诊断包 `join(5)`（`make_extras` 也在主线程） | `main.py:4527`、`main.py:439`、`main.py:4589` | P1 |
| **B1** | 教授补的条件（`With the stable growth rate.`）另起一行 | ASR 端点把介词短语切出去；现有合并只认「上一行没说完」 | `fragment_merge.py:62`、`note_merge.py:79` | P1 |
| **C1** | md 的「课堂纲要」术语仍中文在前 | `note_render` 只对 `review` 调 `announce_terms_in_review`；纲要走 `_render_outline_section`，没过同一道 | `note_render.py:194,287`、`obsidian_writer.py:532` | P1 |
| **C2** | 更新后第一节课吃不到地区测速结果 | `state_for` 版本变化整份重置；预热线程 3 秒后才落盘，开课 `Router` 先读了状态；预热取不到 key 没日志 | `region_route.py:98-106`、`entry_launch.py:237-250` | P1 |
| **C3** | 收尾 Jev 24/24 批失败，且没留原因 | `ask_batch`/`score` 只计失败数不记异常文本；`support_check` 吞掉 `ask` 异常成空列表 | `keypoints.py:182,241`、`note_guards.py:2448` | P1 |
| **C4** | report 里 `bad_block: 'semantic_seal'` | `testmode.BLOCKS` 没登记，`note_block` 拒收 | `testmode.py:381`、`main.py:4092` | P2 |
| **C5** | 数字核对只标不改，错值仍留在正文 | 产品取舍：`annotate_mismatches` 只插 ⚠，不改数字 | `note_guards.py:738`、`note_html.py:1386` | P2 |
| **C6** | 「还没传完（超过 10 秒）」假警报 | `join(10)` 后只判 `is_alive()`，打印前不复查 | `main.py:4527-4530` | P2 |
| **C7** | 18–24 词的短段 Soniox 不封口 | `GATE_WORDS=25`，且已 final 的短段不进 sealer 缓冲 | `semantic_seal.py:43` | 观察 |
| **D1** | 字号 / 透明度 / 圆角各写各的 | 没有 token 层；同一角色 4–6 个值 | 见 §4.1 | P2 |
| **D2** | 动效时长偶有字面量 | 大部分走 `motion.py`，少数别名 / 本地常量 | `overlay.py:259-260` | P2 |
| **D3** | 卡片圆角 4 / 5 / 12 / 16 四档混用 | 每处自己定义 | `panel.py:50`、`entry_panel.py:60`、`whatsnew.py:141` | P2 |

---

## 1 A 线：收尾后台化（A1–A5）

详细设计、时序图、失败模式见 `docs/PLAN-note-background-20261103.md`，这里只列**本计划的改动点**。

### 1.1 核心改动

1. 停课 → 冲刷最后一句 → 写 `sessions/<stem>.md` → 写 journal `note=pending` → `Popen(note_worker, start_new_session=True)` → 主进程退出。
2. 新文件 `note_worker.py`：复用 `main.rebuild_note_for`（`main.py:483`）那条管道；写完更新 journal。
3. 主进程退出即释放 `instance.lock`（flock 由内核托管，进程死了就放）。
4. 入口面板加一行「后台笔记任务」，可看进度 / 停止；完成后系统通知。

### 1.2 A5 改为「不写中间笔记」

原 3.9.7 的基础版落盘（`obsidian_writer.py:2079-2082`）是为了崩溃时盘上至少有一份。现在：

- **删掉基础版写入**；只在完整版结束时写一次（同 `_atomic_write`）。
- 崩溃兜底靠 `sessions/<stem>.md` + journal + `resume_pending_notes`（`main.py:587`）。
- 收益：用户**任何时候打开的都是完整笔记**，不会再看到少一层讲解的半成品。
- 代价：worker 在写完前死掉时，盘上没有笔记 —— 下次启动补；数据在 `sessions/` 里，不丢。

### 1.3 A3/A4 提速

- 笔记链单独 `deadline=90`，重试 2 次（不是 3×300）。
- `review` 总预算 5 分钟；超时丢未回块，笔记标「部分生成」。
- `_lost_items` / `_keypoint_items` / `recap` 并发（`ThreadPoolExecutor(3)`）。
- 每块耗时进日志（先量再调）。

### 1.4 A6：点「完成 / 关闭」后卡 5 秒

**实测**（今天 11:06 那节课）：

| 时刻 | 事件 |
|---|---|
| 11:06:19 | 笔记落盘，收尾卡出现 |
| 11:06:25 | `tester.finish()` 写完报告 + 数据包（这 6 秒在 `_spin` 里，窗口**会动**） |
| 11:06:30 | `update.log` 记「退出后收尾」—— 进程退出 |

中间 **5 秒**窗口不动。原因：

- `main.py:4527` `_th.join(timeout=UPLOAD_JOIN_S)` —— 主线程阻塞，最多 10 秒，期间没有 `ui.pump()`；
- `main.py:4589` `start_upload_extras()` 里 `make_extras()` 同步打诊断包，再 `th.join(min(10, 5))`（`main.py:439`）—— 又最多 5 秒。

`_spin()` 就是「不冻主线程」本身，这两处绕过了它。

**修**：

1. 主上传的 join 改成 `_spin(lambda: (not _th.is_alive()) or time.monotonic() >= deadline)`；
2. `start_upload_extras` 整段丢进 worker 线程，主线程 `_spin` 等它，`make_extras` 也跟着进 worker；
3. 等待期间在卡上/浮窗上留一句「正在收尾（上传）…」，让人知道不是死了（可选，但推荐）。

**判据**：点关闭后 5 秒内窗口仍可拖动 / 点 ✕；`ps` 退出时间与日志对得上；不再出现「点了没反应」。

---

## 2 B 线：断句反向合并（B1）

### 2.1 已核实的逻辑

今天 11:07 ECON10770 的原始事件：

```
t=475.499  final: "...grow faster and faster."
t=477.230  final: "With the stable growth rate."
t=478.050  final: "You see?"
```

`sent_gap` 只有 0.12 / 0.66 秒 —— 教授没停，是 ASR 端点把介词短语切出去。

现有两套合并都接不住：

- `fragment_merge.should_merge()` 要求**段尾没句末标点**（`fragment_merge.py:62`）；
- `note_merge.runs()` 只从**没说完的行**开始并（`note_merge.py:79`）。

### 2.2 规则（候选筛选）

`is_backward_fragment(prev_en, cur_en, gap_s)`：

1. `cur_en` 2–8 词，以**单个句号**结尾（排除 `...`）；
2. 第一个词 ∈ 反向介词表：`with / under / for / in / on / at / by / from / about / after / before / during / despite / per / within / without / due / according / except / including / regarding / concerning`；
3. 第二个词是限定词 / 所有格：`the / a / an / this / that / these / those / my / our / its / his / her / their / your / no / some / any / each / every / all / both`；
4. 不含逗号；
5. 不含限定动词 / 助动词 / 主语代词（`is/are/was/were/has/have/can/will/it's/there's/...`，`I/you/we/they/...`）；
6. 前一行是完整句（以 `. ? !` 结尾），间隔 ≤ 3 秒；
7. **前向检查**：`cur_en` 之后 2 秒内若紧跟 ≥5 词的新句子 → 不接（说明它是后一句的前置状语，如 `With all that. So where's the project going?`）；
8. 上一行只有 1 个词（如 `question.`）→ 不接。

### 2.3 量到的证据

对 `sessions/*.md` 全量筛，去重后 **26 处**；人工看 **23 处应接回去**（约 88%）。加前向检查后，剩下的典型：

| 上一句 | 碎片 |
|---|---|
| the base will grow faster and faster. | With the stable growth rate. |
| marginal revenue was just price. | For a price-taker. |
| declining for like 30 years. | In a row. |
| look at the current production levels... | In the economy. |
| right? | For both groups of countries. |

已知误判：`With all that.`（前向话语标记）、`On the way to the folder...`（列表项）。

### 2.4 两层实现

**笔记层（P0，durable）**

- `note_merge.py` 加 `backward_pairs(entries)`：先跑原有 forward runs，再把不在 run 里的候选按 §2.2 配上；
- 候选再过一次 LLM 二判（接 / 不接）—— 一节课约 1–2 次调用，便宜且跨学科通用；
- 拼接用逗号：`...faster and faster, with the stable growth rate.`；
- 复用 `merge_entries` 的重译 + 替换；`sessions/*.md` 一个字节不改。

**直播层（P1，cosmetic）**

- `fragment_merge.py` 加 `should_attach_back()`；
- 触发后用 `merge_translate(prev + ", " + cur)`，复用现有「原子替换两行 + 吞掉新行」的机制；
- 不喂 `finals` / `live_summary`（同现有纪律，避免缓存下标漂移）。

### 2.5 验收

- 用今天 `2026-11-03_110709_ECON10770` 复跑：11:15:05 与 11:15:06 两条并成一条，中文为整句重译；
- `tests/test_merge_replace.py` 加 5 条：bare PP、带逗号、带动词、前向状语、上一行单字；
- 拿 3 节历史课抽样，人工看合并前后，误判不超过 1/10。

---

## 3 C 线：3.10.6 六条修复

### C1 纲要术语英文化

- **根因**：`note_render.py:194` 只对 `d.review` 调 `announce_terms_in_review`；`note_render.py:287-288` 把 `d.chapter_items` 交给 `obsidian_writer._render_outline_section`，这条路径没有 `announce_terms`。
- **修**：给 `announce_terms_in_review` 加 `seen=None` 参数（跨纲要 / 讲解共用）；新增 `announce_terms_in_outline(items, seen=...)`，对 bullet / 标题走同一份 `flip_head` + 括注。
- **判据**：今天这份 `note.md` 跑 `term_en_residue()` = 0；`tests/test_note_v2.py::AnnounceTermsEn`、`test_note_glossary.py` 全绿。

### C2 地区预热与开课抢跑

- **根因**：`region_route.state_for()` 在版本变化时整份重置（`region_route.py:105-106`）；`entry_launch._warm_probe()` 是后台线程（`entry_launch.py:237-250`），`Router.__init__` 在预热 `_persist()` 之前读了状态（`region_route.py:474`）；取不到 key 时静默返回。
- **修**（二选一，默认 ①）：
  1. 开课路径发现 `warm` 正在飞，`join` ≤1 秒等它落盘再 `plan()`；
  2. `run_probe` 每测完一个区就写内存，`plan()` 允许补一次 `replan()`。
  另：`_warm_probe()` 取不到 key 补一行日志。
- **判据**：更新后第一节 report `cached=true` 或 `late=false`；日志有 `region-warm` 完成行。

### C3 Jev 失败留原因

- **根因**：`keypoints.ask_batch()`（`keypoints.py:182`）与 `score()`（`keypoints.py:241`）只计 `n_failed`，异常文本丢掉；`note_guards.support_check()`（`note_guards.py:2448`）`except` 后 `ps=[]`，下游当成「全部找到」。
- **修**：两个入口各记「最后一条异常文本 + provider name」（`keypoints.resolve()` 的 `name`）；全失败时笔记页脚降级为「本轮未核」，不静默少一层。
- **判据**：下一次失败日志能看到 `Jev 401 / 429 / 连不上`；单测注入 fake `ask` 抛 `RuntimeError("x")`，断言日志含 `x`。

### C4 semantic_seal 登记

- **根因**：`testmode.BLOCKS`（`testmode.py:381`）没有 `semantic_seal`；`main.py:4092` 已经在调 `note_block("semantic_seal", ...)`，被 `bad_block` 拒收。
- **修**：`BLOCKS` 加一行；把 `SemanticSealer.stats()`（`semantic_seal.py:120` 的 `self.stats`）接进 report；面板白名单放行 `bad_block` 计数。
- **判据**：下一份 report 的 `block_names` 含 `semantic_seal`。

### C5 数字核对：允许回改正文（已拍板 2026-11-03）

- **根因**：产品取舍；`annotate_mismatches`（`note_guards.py:738`）只插 ⚠，不改数字；`note_html.py:1386-1390` 的注释写明这是有意的。
- **拍板**：Owen 2026-11-03 选「**允许回改正文**」。
- **修法**（保守回改，改不了仍标 ⚠）：
  1. 只处理非引语、且有 `recomputed` 的 mismatch 行；
  2. 定位命中的字段后，用 `note_math._DIV` / `claim` 里被核错的那个数（`c`）在正文里做**一次**替换；
  3. 替换成功 → 写 `（已更正：原写 X → Y）`，行上记 `corrected=True` / `original=X`；
  4. 找不到 / 多处出现 / 非数值 → 退回现在的 `（⚠ 数字对不上：…）`，不改正文；
  5. 核对清单那一行从「⚠ 有出入」改成「✅ 已更正（原写 X）」。
- **判据**：今天 `0.160−0.667` 写成 `0.33` 的那句，正文变成 `−0.507`（或 `−0.51`，按原题小数位），清单同步；找不到的仍标 ⚠，不许静默。

### C6 上传假警报

- **根因**：`main.py:4527-4530` `join(UPLOAD_JOIN_S=10)` 后只判 `is_alive()`，打印后约 3 秒就传完。
- **修**：打印前再查一次 pending 数 / 已传对象数；文案改成「已传 X/Y，剩余下次启动接着传」。
- **判据**：一节 9 对象的真上传，消息只在确实有剩余时出现。

### C7 语意封口短段（观察，不修）

- `GATE_WORDS=25`，18–24 词且 Soniox 已 final 的短段不进 sealer 缓冲。这不是 3.10.4 的目标（目标是「字幕几分钟不动」），先记为观察项。

---

## 4 D 线：UI 设计系统统一（D1–D3）

### 4.1 现状审计

| 角色 | 现在用过的值 | 出现处 |
|---|---|---|
| 卡片标题 | 13 / 16 | `overlay.CARD_TITLE_SZ`、`wrapup.TITLE_FONT_SZ` |
| 正文 | 12.5 / 13 / 12 | `overlay.CARD_TEXT_SZ`、`entry_panel`、`wrapup.STATUS_FONT_SZ` |
| 次要 / 元信息 | 10.5 / 11 | `overlay.CARD_META_SZ`、`wrapup.HINT_FONT_SZ` |
| 正文 alpha | 0.92 / 0.90 / 0.93 | wrapup / whatsnew / mode chip |
| 次要 alpha | 0.56 / 0.55 / 0.62 / 0.60 | overlay / wrapup / entry_panel / whatsnew |
| 强调 alpha | 0.70 / 0.74 / 0.75 / 0.78 / 0.80 / 0.85 | 各处 |
| 禁用 alpha | 0.22 | entry_panel / overlay |
| 圆角 | 4 / 5 / 12 / 16 | whatsnew / entry card / overlay card / panel |
| 动效 | 多数走 `motion.py`；少量本地常量 | `overlay.py:259-260` |

### 4.2 Token 定义（新文件 `uitokens.py`）

```python
TYPE = {"micro": 10.5, "caption": 11.0, "body": 12.5, "title": 13.0,
        "card": 16.0, "answer": 18.0}

ALPHA = {"primary": 1.0, "strong": 0.92, "label": 0.70,
         "meta": 0.56, "faint": 0.38, "disabled": 0.22}

RADIUS = {"small": 4.0, "chip": 6.0, "card": 10.0, "panel": 16.0}
SPACE  = {"xs": 4.0, "s": 8.0, "m": 10.0, "l": 12.0, "xl": 18.0}

COLOR = {
  "accent": (1.00, 0.83, 0.45),   # 现有琥珀，只给「进行中」
  "ok":     (0.45, 0.82, 0.55),
  "warn":   (1.00, 0.72, 0.35),
  "danger": (1.00, 0.42, 0.38),
}
```

**收敛原则：只收敛、不压暗。** 每个现有值映射到最近档；若有角色需要更亮（如 entry_panel 的 `DIM=0.62`），把 token 的 `meta` 定在 0.56，先截图对比，再决定是否单独给 entry_panel 一档。所有例外写进 `uitokens.py` 的注释，不做隐藏分支。

### 4.3 动效规范

- 唯一参数源：`motion.py`（`FEEDBACK_S` / `STATE_S` / `MOVE_S` / `EXIT_S`）；UI 文件里**不再出现**字面量时长。
- 唯一曲线：ease-out-cubic（`motion.ca_timing()`）；按钮按压才用弹簧。
- `motion.reduce_motion()` 为真：所有位移 / 缩放 / 模糊动画退化为**直接设值或淡入淡出**。
- 进度条：数值直接设、每 0.3 秒最多更新一次；不加呼吸 / 脉冲。
- 状态词：继续走 `motion.render_status`（交叉淡换 + 三颗静默点）。

### 4.4 收尾卡改版

见 `docs/PLAN-note-background-20261103.md` §5：5 行阶段清单 + determinate 进度 + 已用时间 + ETA（≥2 阶段后）+ 只能打开完整笔记。本次补三条：

1. 阶段字形用 SF Symbols（`checkmark.circle.fill` / `circle.dotted` / `circle`），不手写 ✓/◐/○，保证基线与字重一致；
2. 计时 / 计数用 `monospacedDigitSystemFontOfSize_weight_`（`_ui_font` 已带 tnum，可直接复用）；
3. 完成后卡片原地变结论，3 秒自动收；失败留住等「关闭」（沿用 `_wrapup_hold`）。

### 4.5 验收（视觉）

- 5 个面各截一张「改前 / 改后」：悬浮字幕、开课面板、收尾卡、更新卡、一些事儿小卡。
- 对比度：正文 ≥ 4.5:1，大字 ≥ 3:1（用取色器量，不靠猜）。
- Reduce Motion 开：录屏确认无位移 / 缩放，进度仍更新。
- 深浅色系统下各看一遍（面板强制 DarkAqua，不受系统浅色影响，但要确认没有漏网控件）。

---

## 5 实施顺序与本机安装

### 5.1 顺序（每步都可单独回滚）

| 批次 | 内容 | 工时 | 先决条件 |
|---|---|---|---|
| **P0** | C1–C4、C6 + A2 进度说真话 + A3 超时 + A6 收尾卡顿 | 1 天 | 无 |
| **P1** | B1 笔记层反向合并 | 半天 | P0 |
| **P2** | A1 收尾拆进程 + A5 取消中间笔记 | 1 天 | P0 |
| **P3** | B1 直播层 + D1–D3 token 统一 + 收尾卡改版 | 1 天 | P2 |
| **P4** | C5（待拍板）+ 文案 / 对比度打磨 + 本机验收 | 半天 | P3 |

总计约 **3.5–4 个工作日**；P0 单独装就能先把「卡说谎 / 等 15 分钟」压下去。

### 5.2 本机安装步骤

1. **先备份**（硬规矩：动大改动前先备份）：
   ```bash
   cp -a ~/lecture-live ~/classlive-backup-3.11-$(date +%Y%m%d-%H%M)
   cp -a ~/.classlive ~/classlive-backup-3.11-dotclasslive
   cp -a ~/lecture-live/glossary ~/classlive-backup-3.11-glossary
   ```
2. 改完跑全量判据：
   ```bash
   cd ~/lecture-live
   ClassLive.app/Contents/MacOS/python scripts/check.py
   ```
3. 重建 `.app` 并装到本机：
   ```bash
   ./make-app.sh
   cp -a ClassLive.app /Applications/   # 或按 install.sh 的口径
   ```
4. 本机冒烟：
   ```bash
   CLASSLIVE_UPLOAD=0 cl        # 跑一节 1–2 分钟的测试课
   ```
5. **两节课连跑**（本次的核心验收）：第一节停课后立刻开第二节，确认：
   - 第一节进程 ≤30 秒退出；
   - 第二节正常开课、录音、出字幕；
   - 第一节笔记在后台生成完，`Lectures/` 只有完整版。
6. 收尾路径专项：
   ```bash
   ClassLive.app/Contents/MacOS/python scripts/probe_testmode_report.py
   ```
7. 本机自用 1–2 天，确认无回归后，再谈版本号 / CHANGELOG / 发布。

### 5.3 发版闸门

- 本机自用期间**不 bump VERSION、不改 CHANGELOG 的发布节**；
- `scripts/check.py` 全绿 + 两节课连跑通过 + 收尾卡截图确认，才进入发版流程；
- 发版规矩见 `.claude/rules/release.md`。

---

## 6 风险 / 回滚 / 开关

| 风险 | 兜底 |
|---|---|
| detached worker 起不来 | 卡片提示 + 主进程内联跑（退回今天的行为） |
| worker 中途死 | journal + 下次启动 `resume_pending_notes` |
| 两节课同时收尾 | `note-jobs` 按 stem 隔离；本地模型段加全局 `note.lock` |
| 反向合并误判 | 候选先过规则 + LLM 二判；误判只影响两行，不改 `sessions/` |
| token 统一后对比度下降 | 先截图量对比度；不达标就保留原值并记例外 |
| 任何一项出问题 | `CLASSLIVE_NOTE_WORKER=0` / `CLASSLIVE_NOTE_MERGE=0` 退回旧路径 |

---

## 7 验收总表

| # | 判据 | 验法 |
|---|---|---|
| V1 | 停课→进程退出 ≤30 秒 | `ps` + 日志时间戳 |
| V2 | 立刻能开第二节课 | 两节课连跑 |
| V3 | `Lectures/` 只有完整笔记 | `ls` + 文件名断言 |
| V4 | 卡片阶段计数与日志一致 | 抽 1 节课对账 |
| V5 | 进度条单调不回退 | 每秒采样 |
| V6 | worker 被杀能补 | `kill -9` + 重启 |
| V7 | 反向合并复现 | 今天 11:15 那两句并成一句 |
| V8 | 术语英文化 | `term_en_residue()=0` |
| V9 | 地区预热 | 更新后第一节 `cached=true` / `late=false` |
| V10 | Jev 失败留原因 | 注入 fake 异常，日志含文本 |
| V11 | semantic_seal 进 report | `block_names` 含它 |
| V12 | 上传文案不假警报 | 9 对象真上传 |
| V13 | UI token | 无裸 alpha / 字号（grep 断言） |
| V14 | Reduce Motion | 录屏 + 开关对比 |
| V15 | 点「完成 / 关闭」后不卡死 | 5 秒内窗口仍可拖动 / 点 ✕；`ps` 退出时间与日志对得上 |

---

## 8 参考来源（2026-11-03 核）

- Apple HIG, *Progress indicators* — https://developer.apple.com/design/human-interface-guidelines/progress-indicators
- Apple HIG, *Materials*（2025-09-09 更新）— https://developer.apple.com/design/human-interface-guidelines/materials
- Apple HIG, *Motion / Accessibility* — https://developer.apple.com/design/human-interface-guidelines/motion
- Jakob Nielsen, *Progress Indicators Ease the Wait*（2026-08-08）— https://www.uxtigers.com/post/progress-indicators
- BBC Subtitle Guidelines v1.2.5（2026-03）§3.2 / §3.4 — https://www.bbc.co.uk/accessibility/forproducts/guides/subtitles
- SUBTLE, *Recommended Quality Criteria for Subtitling*（2023-01）— https://subtle-subtitlers.org.uk/
- *Segmenting Subtitles for Correcting ASR Segmentation Errors*（EACL 2021）— https://aclanthology.org/2021.eacl-main.248.pdf
- *Streaming Punctuation*（arXiv 2301.03819）— https://arxiv.org/abs/2301.03819
- Google Research, *Semantic Segmentation with Bidirectional Language Models Improves Long-form ASR*（arXiv 2305.18419）— https://arxiv.org/abs/2305.18419
- Otter.ai, *Transcription processing time FAQ*（2026-05-27）— https://help.otter.ai/hc/en-us/articles/360048322493

---

## 9 C5 审查阻断项（2026-11-03，合入前必须解决）

C5 现在会**把算对的式子改成算错的**，并落「已更正」标签。实跑 `note_guards.annotate_mismatches` 复现：

| 正文 | `recomputed` | 实际输出 |
|---|---|---|
| `5.3÷3=2.89` | `5.3/3 ≈ 1.767（保留 2 位）` | `5.3÷3=2。（已更正：原写 2.89 → 2）` |
| `15+225/15=30`（正确） | `225/15 ≈ 15` | `15+225/15=15` |
| `2+3×4=14`（正确） | `3×4 = 12` | `2+3×4=12` |
| `500/60=30/3` | `500/60 ≈ 8.3333` | `500/60=8.3333/3` |
| `5.3÷3≈1.77。而 5.3÷3=2.89 是错的。` | `5.3/3 ≈ 1.767` | 改 `1.77→1.767`，真错值 `2.89` 留下 |

根因：`_last_num_text` 取 `recomputed` 最后一个数（末尾「保留 2 位」会被当结果）；`_EQ`/`_DIV` 是子串匹配、没调用 `note_math.is_sum_term`、没处理链式分母 `d`；第一个通过 `_same_rounded` 的候选直接 return，没有「确实是错的那条」判据。

另外三处一致性问题：

1. `checks_summary` / `checks_headline` / md+HTML 页头 / 终端 🥉 行 / `_guard_counts["flagged"]` 仍按 `status=="mismatch"` 计数，正文已改也说「N 处对不上」。
2. C5 是流水线最后一步；`_with_overview`（速览/串讲）和 `flag_conflicts`（跨节矛盾）都在它之前跑，速览可能保留旧值，跨节标注可能点名已不存在的值。
3. `_done_claims` 按 claim 字符串去重；同一 claim 出现在两节时只改第一节，第二节原样保留却记 `corrected=True`。

修法顺序：

1. 修 `recomputed` 结果定位：取最后一个 `≈/=` 之后的数；没有就只接受串尾数；`答案值 … ≈ 取点` / `程序值：…` 两类禁用。
2. 修候选判据：`_EQ` 前后邻字符不许是运算符/括号；`_DIV` 有 `d` 组直接跳过；复用 `is_sum_term`；收集全部候选，**恰好一个确实算错**才改；改后值必须真不等于原值。
3. 修一致性计数：`checks_summary` 增 `n_fixed`，`n_bad` 排除 `corrected`；页头/终端/`flagged` 跟着改。
4. 拆成「回改早、标注晚」：回改放在 `verify_numbers` 之后、`flag_conflicts`/`_with_overview` 之前；`scripts/notes_replay.py` 同步。
5. 修账本口径：修正记录写「已更正：原写 X → Y」；简版不再计入「三道核另有发现」；`algebra` 只改数字、不缀中文括注。

**总判：C5 现在不能合；修 1–3 是合入前必须，4 不修会在真笔记上留旧值。**
