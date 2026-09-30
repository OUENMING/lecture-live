# 实施计划：AI 实时总结（课中纲要 → 课后交接）

> **本文件是唯一正本。** 它取代 `docs/PLAN-roadmap.md §2.6` 里那三条线的旧描述，
> 也取代 `~/Downloads/AI 实时总结：可执行实施计划.md`（那份是**初稿**，已删）。
> 上一份交接（首次就绪 / 面板简化 / 架构加固）里**还活着的账**全部搬到了 §15，一份没丢。
>
> **怎么来的**：作者给的初稿 → 在 HEAD 上逐条核对代码（三个 Explore 分区摸清接入点）
> → 三轮审核（每条决定都过了一遍）→ 计划模式写成本文件。
> **核对结果在 §1**：原稿有两处**前提**是错的、一处**算术**是错的，都已就地更正。

---

## ⭐ 当前状态（2026-09-30 收尾 —— **先读这一节再看下面**）

> **六个阶段全部走完了**（0 ✅ / 1 ✅ / 2 ✅ / 3 ✅ / 4 ✅ / 5 ✅）。
> 下一步是 **§15 那批旧账 + 发版 3.8.0**（作者说「一起发」）。

### 这一轮落地的（都在 `feature/ai-summary-panel`，已推）

| 阶段 | 交付 |
|---|---|
| **3** | 原子层内联块 → `live_summary.LiveSummarizer` 接线；`drain()` 加 `"summary"` 分支 + 兜底 `else`；`_wrap()` 里 `summ.finish()` 在 `writer.close()` **之前** |
| **4** | 章节条（顶栏最左）+ 纲要接管视图 + 菜单栏入口 + 空状态 |
| **5** | `_render_note` 里多一节 `## 📑 课堂纲要`（在 Overview 之后、Key Concepts 之前） |

### ⭐ 作者这一轮**新拍的板**（覆盖下面正文的旧写法，以这里为准）

| 事 | 决定 | 影响哪一节 |
|---|---|---|
| **排法①** | **中文当大字 / 英文原句降小字** —— 直播字幕、面板纲要、笔记那一节，**三处同一条** | §9.3（已改）· §10.2 |
| **入口就叫「实时总结」** | 三档：`▸ 实时总结` / `▸ 主题 · since 时间` / `● ▸ 标题 · since 时间` | §9.8/§9.9 |
| **按钮「新话题」→「字幕」** | 这个按钮做的事就是回到字幕 | §9.4 |
| **「↓ 最新」删了** | 它的活并进「字幕」（`_new_topic` 现在也 `scroll_to_bottom()`）。⚠️ 顺带 **章节条多出 63px** | §9.8/§9.9 |
| **原子也要中文** | `atom.SYS` 出 `zh`；⚠️ **`.atoms.jsonl` 键集合 7 → 8**（T16 跟着改了） | §6.1（原写「字段不增不改」**已作废**） |
| **对比度** | 章节条 α **0.65**（实测；原稿拍的 0.60 只有 6.94，差一点点没过 7） | §9.0②/§9.8（已改） |

### ⚠️⚠️ 这一轮挖出来、**只有真跑才看得见**的坑（动手前扫一眼）

1. **`_layout()` 只在尺寸/档位变时跑** —— `_sync_panel_size` 是轮询，尺寸没变就早退。
   → 章节条**永远不会出现**。修法：`summary_update` 末尾自己补一次 `_layout()`。
   ⚠️ **探针里看得见是因为它调了 `_open_outline()` → `_apply_mode` 顺带跑了 `_layout`**。
2. **滚动后回收池不会重算** —— `scroll_to_top`/`scroll_to_index` 会**回写 `_expected_origin`**
   （防误判成用户滚动），代价是 `tick()` 看不出视口动过 → 槽位停在旧位置 → **整屏空白**。
   修法：滚完补 `self._tv.mark_dirty(urgent=True)`。
3. **模块级函数插在类体中间 = 类体到此结束** —— `_render_note`/`close` 会变成模块级函数，
   而 **`ast.parse` 照样通过**（语法合法），只有测试拦得住。
4. **`NSMenu.autoenablesItems` 会覆盖 `setEnabled_(False)`**（实测）→ 改用 `setAction_(None)`。
5. **开关默认值**：阶段 4 起 `CLASSLIVE_LIVE_SUMMARY` **默认 `"1"`**。

### ⭐ 2026-09-30 收尾后的**审计**（下一段上下文先读这一段）

作者问「还有哪里没有收盘、哪里没有做对」。逐条核过了一遍，结果如下。

**① 计划明写、但漏做的代码 —— 只有一条，已补**

`D17` 的后半：`atom.AtomWriter` 的「关闭后拒写」。计划逐字写「**两个写入器都加**」，
当时只加了 `ChapterWriter`。⭐ 补的时候发现**计划的处方本身不够**：

| 层 | 问题 | 修法 |
|---|---|---|
| ① `atom.AtomWriter` 自己 | `close()` 把 `_h` 置 `None` → 再写**重开文件** | 加 `_closed`（D17 原话） |
| ② **`ObsidianWriter._atom_writer()`** ⭐ | `close_atom()` 把 `_atom_w` 置 `None` → 再写**新建一个写入器**（新实例 `_closed=False`，**第 ① 层管不着**） | 加 `_atom_closed`，**照 `close_lost`/`_lost_handle` 的先例** |

⚠️ **只修第 ① 层是假绿** —— 我第一版就是这么修的，复现脚本照样红。
⚠️ **②才是生产走的那条路**：`finish()` 25 秒内拿不到 `_step_lock` 时**放弃 worker 线程**
（那段注释写着这条实测发生过），被放弃的线程之后照样走到 `_absorb_window` → `append_atoms`。
判据在 `tests/test_atom.py` ⑥b（**三层都做了变异验证**）。

**② 作者实测报上来的：排法① 在「原子」那一处**漏了英文**（已修）**

原话：「**AI 总结现在只有中文，英文完全没有了**」。查出来是**同一处坑**，三个面各漏一次：

| 面 | 改前 | 改后 |
|---|---|---|
| 面板纲要 · 原子要点 | 小字**只挂术语**（`terms` 常常是空的）→ 英文整条消失 | 小字挂**英文原句** + 术语 |
| 笔记那一节 · 原子退路 | 只有一行 `- **中文**`，**连小字行都没有** | `- **中文**` + `  · 英文原句` |
| **`en` 档（英文校准）** | 模块级 `_atom_big` **根本不看 mode** → 该只显示英文却仍是中文 | 大字**退回英文**、中文全清 |

⭐ **根因是「一条纪律两处定义」**：合成句那一处写了「英文降小字」，原子那一处没写。
→ 已收成**唯一定义点**：`overlay._pair()`（面板）与 `obsidian_writer._pair_md()`（笔记），
合成句和原子**都走它**。旧那个模块级 `_atom_big` **已删**。

⚠️ **为什么一直没被发现**：㊱ 那条判据的夹具里**原子没有 `zh`** → 三个 bug 全是**假绿**。
现在 ㊱/`test_vault` 各自多了一条**专钉原子英文**的断言（**四处变异全红**）。

⭐ **作者追加的要求（已做）**：占位文案 ——
顶栏章节条空档 `▸ AI 实时总结 · 点这里`（实测 **117.45px**，门槛 90px，够）；
面板空状态 `▍ AI 实时总结 · 内容会自动出现在这里`（有窗口没章节那档也带功能名）。
⚠️ 顺带查明：计划 §9.11 的第三支（「有 gap 且 `state=pending`」）**是死代码** ——
第 ② 步对每个 gap 都 `_push` 一行，`out` 必然非空 → `if not out:` 进不去。**已删**；
计划想要的那句话本来就送达到了，而且更好（带具体时间段）。

**②b ⚠️⚠️ 一个我造成、又修回的真事故：`~/.classlive/vault` 被测试改成了临时目录**

跑验收前的隔离检查时发现：`~/.classlive/vault` 的值是 **`/tmp/cl-e2e/vault3`**
（一个**已经删掉**的临时目录），而不是作者的库。

- **它是什么**：就是 `test_vault.py` 文件头点名要防的那个 bug 的**同一个形状** ——
  「双击 `.app` 起的那条路**没有 shell 环境**（`launchctl getenv OBSIDIAN_VAULT` 是空的）」
  → `resolve_vault` 落到「**记住的**」那一档 → 笔记会被写进一个不存在的路径。
- **谁的**：我。`/tmp/cl-e2e/` 是我早期那次端到端测试的临时目录名，mtime 是 30 Sep 13:33。
- **怎么修的**：`~/Desktop/classlive-review/backup-20260930-pre-ai-panel/classlive-dot/vault`
  里有一份 **30 Sep 00:00 的原值**（33 字节）= `/Users/owen/Obsidian/SecondBrain` →
  按备份**还原**。验证：`env -u OBSIDIAN_VAULT … resolve_vault(env={})` 现在解析到真库 ✅。
- ⭐ **教训**：跑会写盘的 CLI 时，只隔离**这一轮**的写端**不够** ——
  它还会把「记住的库」这类**持久状态**改掉，而那份状态**只在下次没有环境变量时才咬人**。
  → **下一次跑这类测试，先 `cp -a ~/.classlive/`，跑完逐项对拍**（这次是靠备份救回来的）。

**③ 验收层：计划要求跑、但零记录的 5 项**（⚠️ 2026-09-30 复核：**① 已被作者的人工核对覆盖**，见文末）

| 那一条 | 要做什么 |
|---|---|
| §8.5 第 2 项 | 接线前后同一段录音跑文件回放，对拍 `.atoms.jsonl` 键集合 |
| §8.5 第 3 项 | 记「播完到进程退出」耗时，确认 ≤ `SUMMARY_FINISH_S` |
| §8.5 第 5 项 | `--ui overlay` 跑一节，界面与今天一样 |
| §10.3 第 1、2 项 | `say -o` 生成音频回放，课务 **2 秒内**上屏 |
| §11.3 第 ② 条 | 改了 `transcript_view.py` 就要 `probe_scroll.py` **连跑三次**（⚠️ 现在适用） |

**④ §15 那 13 条的现状**（2026-09-30 收尾后**重新逐条现查过**）

| 状态 | 条 |
|---|---|
| ✅ **已收盘（9 条）** | #1 文档口径 + 自动更新的模型盲区 · #3 `measure_text_h`（不合并 + 加行为判据）· #6 OCR 那 165 条（分诊完，真成立的 3 条已修）· #8 `PROBE_ZERO` · #9 `classify.py` · #11 ECON10730 污染 · #12 模型名统一 · #13 `ANSWER_MAX_LINES` · 外加**课号 `ECON0070`→`ECON10070`** |
| ⚠️ **半开（1 条）** | **#10**：`ECON10740` **6 节 → 不再降级** ✅；`ECON10790` **8 个文件但只 2 节够格 → 仍降级** ⛔ |
| ⬜ **还开着（3 条）** | **#2** 端到端「什么都没装」（⚠️ **现在做不了**：`MODELS_ROOT` 写死，要先加覆盖点）· **#4** `entry_panel.py` **3483 行**（计划明写「别顺手拆」）· **#5** 3.8.0 没发 |
| ⚠️ 新发现 | `docs/DESIGN.md` 的「文件」表**整体停更**（48 个模块只列 17 个）—— 另一笔账，没动 |

⚠️ **#5 比原记录更严重**：`CHANGELOG.md:68` 的 `## [3.8.0]` **已有**，但 7 条看点里
**「实时总结」一个字都没有** → **要重写，不是等确认**。

### 还没做的

- **验收层那 4 项**（见上面的 ③，⑤ 已被作者的人工核对覆盖）——
  ⚠️ **实跑才有数的，一项都没跑过**
- ⚠️ **原子/课务的中文**：原子已做；**课务原句仍是纯英文**（它要另一次模型调用，没做）
- **§15 剩下的 3 条**（见上表）
- ⚠️ `sessions/` 里 **3 个 7 行的误启动记录**（`011955` / `020026` / `110641`）——
  按规矩没动；要标「不属于任何课」得作者点头

### ✅ §11.3 人工清单 7 条 —— **作者 2026-09-30 核对过，都没问题**

⚠️ 其中**第 ① ②条我跑不了**（这纠正了我之前把它列进"验收待办"的口径）：
- **①`probe_drag.py`** —— 要**真人拖窗口**
- **②`probe_scroll.py`** —— 它自己的文件头逐字写着「**都只能真机测, 合成事件测不出来**」，
  用法是「**按屏幕提示, 在每一阶段把鼠标移到悬浮窗上双指滚动**」→ **必须人手滚**

→ 所以「验收层 5 项」里那第 ⑤ 项（§11.3 ②）**已经由作者覆盖**，不是待办。

---

## 0. 怎么读这份

- **§1 是已核实的事实**，不是转述。每条都在 2026-09-30 的 HEAD 上现查过，带行号或 sha。
  ⚠️ 原稿 §9 那道「版本矛盾」**在 HEAD 上不存在** —— 先读 §1.1，**别照着去修一个不存在的 bug**。
- **§2 是作者已拍板的决定**（D1–D20），逐条列了。**别再问一遍。**
- **§3 是这一块今天怎么运作** —— 三个**已存在**的缺陷 + 为什么原子要改英文。
- **§5–§10 是五个阶段**，顺序是作者定的（离线 → 开课前 → 课中）。每阶段末尾有**完成判据**。
- **§11 是判据总表**（T1–T24）。每条都写了「**把实现的哪一行改坏，这条会红**」。
- **§12 是已知问题**，不在本计划范围内，但不知道会让阶段 2 的结果被误读。
- **§13 是停手规则** —— 哪些情况**必须**停下来问作者，不许自行判断。

**跑任何东西之前**：解释器是 `ClassLive.app/Contents/MacOS/python`（系统 `python3` 没有 AppKit，
`/usr/bin/python3` 是 3.9；`CLAUDE.md:161` 逐字）。默认闸门：
`ClassLive.app/Contents/MacOS/python tests/test_audit_regressions.py`。

---

## 1. ⭐ 已核实的事实（2026-09-30，全部在 HEAD 上现查）

### 1.1 原稿说的那道「版本矛盾」**不存在**

原稿 §9 第 1 条说：读到的 `main.py` 还在 `from obsidian_writer import ObsidianWriter, DEFAULT_VAULT`，
而 `tests/test_vault.py` 断言没有这个名字；`main.py` 给 `Overlay` 传 `on_flag=`，
而 `Overlay.__init__` 没有这个参数 ——「真这样的话，悬浮窗会抛 TypeError，并被静默回退成终端界面」。

**逐条核对：**

| 原稿说的 | HEAD 上的实际 |
|---|---|
| `main.py` 里 `... import ObsidianWriter, DEFAULT_VAULT` | `main.py:25` = `ObsidianWriter, resolve_vault, remember_vault` —— **没有** `DEFAULT_VAULT` |
| `--vault` 默认值用它 | `main.py:1942` 的 `--vault` **没有 `default`**（= `None`） |
| `main.py` 给 `Overlay` 传 `on_flag=` | `on_flag` **全仓 0 命中**（`grep -rn on_flag --include="*.py" .`） |
| `Overlay.__init__` 没有 `on_flag` | 正确 —— `overlay.py:497` 是 `(on_quit, on_translate, on_submit, on_ask, on_new_topic, on_lost, whatsnew)` |
| `test_vault.py` 断言没有这个常量 | 正确（`tests/test_vault.py:104`），而且**它是通过的** |

**两边完全一致，没有矛盾。** 时间线说明原稿读到的是**旧版** `main.py`：

```
aba9d88  2026-09-29 13:37:19 +0100  Obsidian 库路径收口：删掉那个会凭空造目录的兜底
153e229  2026-09-29 13:38:22 +0100  收尾卡不再「退回虚空」+ ⭐ 并进 ❓      ← 删掉 on_flag
```

两个提交**都在 `origin/main` 的祖先里**（已推）。而且 `tests/test_vault.py`
**就是 `aba9d88` 那一次加的**（`git log --diff-filter=A -- tests/test_vault.py` 只此一条）——
**只要读同一时刻，就不可能读到两者打架**。

⚠️ 原稿自报的读取路径是 `raw.githubusercontent.com/.../main/` —— 那正是**有 Fastly CDN
5 分钟缓存**的路（`cache-control: max-age=300`）。原稿猜的「两次读取拿到了不同时间的缓存」
机制猜对了，只是方向反了：**是读取给了旧版本，不是缓存给了新版本**。

→ **本计划不动它。不影响阶段 3/4 的验收。**

### 1.2 原稿的「代码地图」：14 行逐行核对，**全部属实**

| 文件 | 位置 | 核对结果（行号是 2026-09-30 的 HEAD） |
|---|---|---|
| `main.py` | 原子层块 | ✅ **`1474–1544`**；`import atom` 1483 · `ATOM_EVERY_S` 1485 · `ATOM_PAUSE_S` 1492 · `atomq` 1493 · `atom_st` 1494 · `_atom_chat` 1496–1503 · `_atom_flush` 1505–1525 · `atom_worker` 1527–1542 · daemon 线程 1544 |
| `main.py` | `drain()` 的 `final` 分支 | ✅ `1586–1598`；`atomq.put_nowait` 在 1595，外面有 `if trans["mode"] != "raw"`（1593）与 `try`（1594） |
| `main.py` | `drain()` 的 `streamq` 分发 | ✅ `1557–1598`，**7 个 tag、无兜底 `else`** |
| `main.py` | `all_settled()` | ✅ `61–72`；五个输入 = `finalq` · `streamq` · `drafts` · `busy` · `carry_text` |
| `main.py` | 收尾 `_wrap()` | ✅ `1756–1765`；`running.clear()` 在 **1678**，确实在它之前 |
| `atom.py` | 常量 / `SYS` / `Atom` / 各函数 | ✅ 常量 `47–55` · `SYS` `57–73` · `Atom` `76–90` · `parse_reply` `135–168` · `topic_of` `181–183` · `load` `256–274` |
| `atom.py` | `KINDS` | ✅ `55`，六种，含 `课务` / `讲者强调` |
| `atom.py` | `AtomWriter` / `load()` | ✅ `187–253` / `256–274`；追加写、每次 flush、不 fsync、`close()` 幂等、文件不删 |
| `build_notes.py` | `_chat_json(...)` | ✅ `222`；被 `main`(1502) / `prep`(930) / `classify`(208) / 自身(283,315,334,464) 复用 |
| `overlay.py` | `_render()` | ✅ `2508–2541`；分支在 **2517**（`_answer_on`）→ **2528**（`elif _tv_mode != "cap"`）→ **2534**（`else`） |
| `overlay.py` | 答案接管三件套 | ✅ `_answer_enter` `1833` · `_clear_answer` `1847` · `_apply_mode` `1130` · `_answer_refold` `1870` |
| `overlay.py` | `_answer_fits` / `_answer_split_to_fit` | ✅ `2024` / `2010`；⭐ **两者都不碰答案的「可变」状态**（不读不写 `_answer_text` / `_answer_rows` / `_answer_pend` / `_answer_fold` / `_answer_pr` / `_answer_pw` / `_answer_finished`）。⚠️ **精确口径**：它们**读** `self._answer_font`（`644`，一个不变量）和 `self._tv.text_width()` —— 所以「完全不读任何 `_answer_*`」是**错的**，正确的说法是「**不碰可变状态**」 |
| `overlay.py` | `_layout()` 顶栏 / `MIN_WIDTH` | ✅ `_layout` `1434–1494`，顶栏 `1469–1491`；`MIN_WIDTH = 320.0`（`128`）· `PAD = 16.0`（`50`）· `HEADER_H = 34.0`（`102`） |
| `overlay.py` | `_lost` / `set_trans_mode` / `_make_click_view` | ✅ `2220` / `2261` / `467`（模块级） |
| `obsidian_writer.py` | `append_atoms` / `close` / 笔记渲染 | ✅ `705` / `1029–1192` / `_render_note` `878–1026`；`close_lost()` 1055 + `close_atom()` 1056 **在四条早退之前** |
| `transcript_view.py` | 六个方法 | ✅ 全在；**没有**「滚动到第 N 行」 |

### 1.3 ⭐ 原稿**错了**的三处（就地更正）

**① §4.8 的顶栏几何算错了（会压到按钮）**

原稿写：「循环结束时的 `x` 就是最左边按钮的左边界。可用宽度 = `x - PAD`」。

实测 `overlay.py:1487–1491`：
```python
x = self._width - pad
for b, w in reversed(row):
    x -= w
    b.setFrame_(NSMakeRect(x, self._height - 28, w, 24))
    x -= gap              # ← 循环体最后还减了一个 gap
```
所以**循环结束时 `x` = 最左按钮的左边缘 − `gap`**。

**正确写法**：`最左按钮的左边缘 = x + gap`；`可用宽度 = (x + gap) - PAD`。
⚠️ **2026-09-30 实测更正**：本文件原来写着「照原稿的 `x - PAD` 写会**多算一个 gap
少算 PAD**，章节条会**压到最左边那个按钮上**」—— **方向说反了**。
`x` 比 `x + gap` **小**，所以 `x - PAD` 算出来的可用宽度**窄 6px**：
章节条只会**短 6px**、**不会**压到按钮上
（变异验证实测：改回去「不压到可见按钮」那条**照样绿**）。
→ **公式是对的，后果写重了**；按 `(x + gap) - PAD` 写仍然对，理由改成「用满空档」。
`gap = 6.0`（`1472`）。

**② §1.2 说章节文件有「两种记录」，§5.1 又加了第三种**

`{"type":"window"}` · `{"type":"chapter"}` · `{"type":"deadline"}` —— **三种**。见 §6.2。

**③ §4.6 说「没有分隔线就调 `scroll_to_top()`」，但没提坐标系**

`transcript_view` 的文档视图**不翻转**：`origin.y = 0` 是**底部**，`level 0` 是最新句在
`y = 0`，`level L` 在 `y = L * row_h`（文件头 `12`；`_layout:480` 逐字 `y = level * self._row_h`）。
**任何程序化滚动之后必须回写 `self._expected_origin = clip.bounds().origin.y`**
（`scroll_to_top:340` / `scroll_to_bottom:326` 都是这么做的）——否则 `tick` 会把程序滚动
**误判成用户滚动**。新增的 `_scroll_outline_to_divider()` 必须照办。

### 1.4 原稿七条「开工前必须核实」：逐条结论 —— **两条的前提是错的**

| # | 原稿的顾虑 | 核实结果 |
|---|---|---|
| 1 | 版本矛盾 | ⛔ **不存在**。见 §1.1 |
| 2 | 模型名 | ⚠️ **属实**：`atom.MODEL = "deepseek-chat"`（`atom.py:48`），而 `--cloud-model` 默认 `"deepseek-flash"`（`main.py:1933`；全仓 9 处用它）。**作者已拍板统一**（D2）。⭐ 顺带证实：`_chat_json:227` **已经关了思考模式**（`"thinking": {"type": "disabled"}`）；⚠️ **精确口径**：全仓走 `json_object` 的调用共 **6 处**（`build_notes.py:227` · `obsidian_writer.py:517` · `obsidian_writer.py:537` · `polish.py:76` · `probe_atomic_summary.py:136` · `review_prompt_ab.py:117`），其中 **5 处关了思考模式，`probe_atomic_summary.py:132–139` 那处没设这个字段**（那是探针不是生产）。而 `cloud_translator.py:244` 设了 `thinking: disabled` 但**不是** JSON 调用。→ **判据是「生产那几处都关了」**，本计划要走的 `_chat_json` 正是其中之一 ✅。而且 **`deepseek-flash + json_object` 已在生产跑着**（`build_notes.build()` 默认 `model="deepseek-flash"`，`:283` 把它传给 `_chat_json`）→ **换模型不引入新风险** |
| 3 | `find.py` 会不会受原子改英文影响 | ⛔ **前提不成立**：`find.py:256` 只 glob `("*.md","*.txt")`，**`.atoms.jsonl` 从来不进索引**；`atom.load` 的**生产调用方为零**（只有 `tests/test_atom.py` 调）；问答走 `main.py:1414 _find.as_context(_find.search(q, limit=8))`。→ **改语言对检索零影响** |
| 4 | 有没有「滚到第 N 行」 | ✅ **没有**（公开方法只有 `scroll_to_top` / `scroll_to_bottom` 两个**绝对端点**）。行**确实等高**（`_layout:480`）→ 新增就是 `level * self._row_h`，不变量天然满足 |
| 5 | `_chat_json` 的超时/重试/usage | ⚠️ **有超时 `timeout=180`（太长）· 没有任何重试 · 拿不到 usage**。函数只 `return` 解析后的 dict（`build_notes.py:**235**；⚠️ 233 是 `raise_for_status`），`r.json()["usage"]` 被丢弃 → 探针只能用「字符数 ÷ 4」估算并**在字段名里标明**。⚠️ 180 秒 vs `finish` 的预算 25 秒 → **必须自带界**（D16） |
| 6 | `polish.progress_text` 接受哪些阶段名 | ✅ **接受任意 stage**（`polish.py:38` `STAGE_NAME.get(stage, stage)`，不认识的**显示原名**，不是空白）。已知标签 3 个：`polish` / `polish_partial` / `review` → 收尾进度提示**可加**，但阶段名要么用已有的、要么会显示英文原名 |
| 7 | 渲染函数名与关句柄位置 | ✅ 渲染函数 = **`_render_note`**（`878–1026`）；关句柄在 **`close()` 最前面**（`1055`/`1056`，注释写明理由） |

### 1.5 ⭐⭐ 实测：Jev 的下标 **≠** `atom.src` 的全局句号

**这一条是本次核对新发现的**，原稿没意识到，而它会**静默毁掉**阶段 2 的覆盖率指标。

```
会话 .md 里 EN 行总数          482   ← atom.src 的全局句号是这一套（writer._n，1-based，不筛）
keypoints.sentences() 过滤后   447   ← Jev 的下标是这一套（丢掉 <=15 字符的，0-based）
被长度过滤丢掉的                35
|全局句号 - Jev 下标|          平均 17.5 · 最大 36
```

（量的对象：`sessions/2026-09-11_140757_10730.md`）

**为什么危险**：`keypoints.sentences()`（`181–190`）里有一句 `if len(t) > 15`（**在 188 行**）
把 `Okay.` / `Right.` 这类短句丢掉了，而 `thoughts()`（`64–93`）记的是**过滤后**列表的下标。
两套编号错开平均 17.5 位 → 直接对账会得出一个**像模像样的错数**（不是 0，所以看不出来）。

⚠️ `atom.traceable` 的 docstring（`97–101`）记的**正是这个形状**：把当日秒数当行号比，
「可追溯率**按构造恒为 0**」，而且「**判据自测当时也过了**（合成数据两边都是小整数，
撞不出这个错）—— 是拿真实输出对不上才发现的」。

→ **覆盖率指标必须先做编号映射 + 自检**，见 §7.4。

### 1.6 真实数据形状（现查）

```
.atoms.jsonl 每行的键（**6 个**真实文件的并集，**559 行**）：
  ['epoch','id','kind','src','t','terms','text']        ← 恰好 7 个（= Atom.as_json() 的输出）
  src 是**全局句号列表**，如 [1,6] / [10,11,12]
  terms 在真实文件里全是 []                              ← 定义了但从不填充
  text 在真实文件里全是中文                              ← 这正是本计划要改的
```

会话时长（593 个有抬头的会话，只列 ≥40 分钟的 7 个）：

```
413.8 min / 1084 句  2026-09-22_150213_ECON10740.md   ⚠️ 异常（整晚没关，§12.3）
103.7 min / 1233 句  2026-09-15_150616_ECON10740.md   ⚠️ 同上
 65.6 min /   89 句  2026-09-10_140200_10730.md       稀疏，不适合做样本
 49.8 min /  682 句  2026-09-29_150509_ECON10740.md   ✅
 49.8 min /  579 句  2026-09-28_120341_ECON10xxx.md   ✅（SOC10020 的 Seminar，§12.2）
 42.3 min /  482 句  2026-09-11_140757_10730.md       ✅（**朋友的课**，§12.1）
 42.0 min /  410 句  2026-09-14_110344_SOC10020.md    ✅
```

`keypoints` 的成本口径（覆盖率指标要用）：`CHUNK = 20`（`keypoints.py:43`）→ 40 分钟一节
≈ **19 次** API 调用。

---

## 2. 作者拍板的决定（逐条，**别再问一遍**）

| # | 事 | 决定 |
|---|---|---|
| **D1** | 交付物落点 | `docs/PLAN-live-summary.md` = **唯一正本**。初稿（`~/Downloads`）**删掉**；作者自己的 Claude Doc 加两处标注（§5.3） |
| **D2** | 模型名 | **统一 `deepseek-flash`**。范围 = 三处定义点（`atom.py:48` · `classify.py:47` · `docs/experiments/probe_atomic_summary.py:42`）；且**减少定义点**：探针改成 `from atom import MODEL`（3 处 → 2 处） |
| **D3** | `classify.py` 那处 | 会改术语分类的输出 → **单独一个提交、单独告诉作者**。要不要也引用同一常量，跟那次提交一起定 |
| **D4** | 理工课样本 | ⭐ **朋友的课**（`2026-09-11_140757_10730.md`，热力学）当**测试样本**，**不放进任何课**。→ 原稿「理工课缺失」**撤销**。✅ **已隔离**（§5.2 ②） |
| **D5** | 理解测验 | **去掉**（要找人）。但用**机械指标顶替**（覆盖率，§7.4），并**明确写成「阶段 4 开工前必须补」的硬闸门**（§7.6） |
| **D6** | `review.md` 自填 | **保留**（可追溯性对照原句判断，不依赖「是不是目标用户」） |
| **D7** | 静默检查（看空窗口） | **保留** |
| **D8** | 四个新常量 | `MAX_PENDING` / `RETRY_S` / `INTERIM_AFTER_S` / `INTERIM_EVERY_S` **全进阶段 2 的可调清单**（原稿只列了后两个） |
| **D9** | 探针先跑几节 | **先跑 1 节**（理工那节，最短且密），确认管线 + 算清单节成本，再决定跑满 3 节 |
| **D10** | 断网怎么测 | 探针加 `--fail-window 10:05-10:12`（**墙上时间**）。判「这次调用算不算失败」看**调用发生那一刻的模拟时间**，不是窗口里句子的时间戳 |
| **D11** | `MAX_PENDING`/`RETRY_S` 的依据 | 正常回放测不出来（网络一直通）→ **依据写在注释里靠推算**，不假装是量出来的 |
| **D12** | 覆盖率口径 | ⭐ **(a′) 原子级**（该句被某条原子的 `src` 引用）vs **(b) 合成级**（该句被某条合成句的 `src` 引用）。**两个都记** —— 差值 =「原子抓到了、合成时丢了」 |
| **D13** | 覆盖率的前置 | **先做编号映射 + 自检**（§1.5、§7.4）。对不上**就不出覆盖率数字** |
| **D14** | 阶段 3 的开关 | 默认 **`"0"`（关）**，阶段 4 翻成 `"1"`。⚠️ 但「零变化」**不成立** —— 两处改进（失败不丢窗口、残余窗口补提交）**不收进开关**，是**有意的改进**，在 3.5 里**单独验证** |
| **D15** | 阶段 3 验收 | 3.5 里那几项**都要在开关为 `"1"` 时跑** |
| **D16** | `finish` 的超时 | **(b) 为主 + (a) 兜底**：给 `_chat_json` 加**可选** `timeout` 参数（默认仍 180，**7 个调用方全能不改**），`LiveSummarizer` 传短值；`finish` 仍用 `join(timeout)` 兜住预料外的阻塞 |
| **D17** | 「关闭后拒绝写入」落在哪 | **两个写入器都加**，但**拆成两个提交**：新的 `ChapterWriter` 那半零回归面；`atom.AtomWriter` 那半**单独一个提交 + 变异验证**（它被四条早退围着，`close_atom()` 的位置是 2026-09-28 真事故换来的） |
| **D18** | 两个入口都做 | 章节条可点（主入口）+ 菜单栏「课堂纲要」。⚠️ **纯转录档下菜单项要置灰不可点** |
| **D19** | 阶段 2 的选课 | 三节：理工（朋友的课）· 讨论型（`2026-09-28_120341_ECON10xxx.md`）· 经济（`2026-09-29_150509_ECON10740.md`）。三节时长接近（42/50/50），指标可比 |
| **D20** | 不做的事 | 二级面板 · 课件分页索引 · 理工术语与公式的**专项**优化 · Jev 课中判定 · 相对日期换算 · 与课程大纲比对 · 本地模型兜底 · 纯转录档发任何模型请求 |

---

## 3. 现状：这一块今天怎么运作

### 3.1 原子层的三个**已存在**的缺陷（本计划顺手修掉）

1. **失败 = 整窗丢**：`_atom_flush`（`1505`）先 `buf, atom_st["buf"] = atom_st["buf"], []`（`1506`）
   再调模型（`1512`）；异常就 `return`（`1515–1517`）—— 那一窗的字**再也回不来**。
   75 秒一窗 → 一节 50 分钟的课 ≈ 40 窗。
2. **线程退出 = 残余窗丢**：`atom_worker`（`1527–1542`）的 `while running.is_set()` 一退出，
   `buf` 里没提交的句子直接没了。
3. **句柄关了会静默重开**（`atom.py:213–216` 逐字）：
   ```python
   def _handle(self):
       if self._h is None and self._path is not None:
           self._h = self._path.open("a", encoding="utf-8")
       return self._h
   ```
   `close()` 把 `self._h` 置回 `None`（`249`）→ **之后再写会重开文件并泄漏句柄**。
   今天没有暴露面（没有「放弃线程」机制），**是本计划引入的**（D17）。

### 3.2 三档注入与「为什么原子要改成英文」

`translator.select_terms` 有三档，**成本性质完全不同**（见 [[classlive-term-injection-cost]]）：

```
注入 = core（常驻） + always（课程术语，每句全量） + scored[:MAX_DYNAMIC_TERMS]（动态召回）
```

`glossary/` 的**主用途是全量注入翻译 prompt**，所以「往表里加词」= **每句 prompt 变贵**
（实测 36 条占 23%、120 条占 60%）。**原子不在这三档里** —— 它只写旁路文件。

**改成英文的理由**：原子是给**读者**（课上快速重入）和**合成层**（章节）用的。中文原子有两个
问题：① 它是**二次转述**，术语经两层模型会漂；② 章节合成要以英文原句为**唯一依据**
（`src` 必须指得回去），中间夹一层中文转述会引入**不可追溯的中间态**。
英文为主 + `terms` 存中英对照 = 读者看到的仍是对照，但**依据是原文**。

⚠️ 今天的原子**只写不读**（`atom.load` 零生产调用方；`overlay.py`/`entry_panel.py` 引用 **0 处**；
`obsidian_writer` 只写不渲染）→ 改语言**今天对用户完全不可见**，只会通过**本计划新加的
两个面**（纲要视图 + 笔记那一节）被看到。

### 3.3 会话 `.md` 是**三方共享契约**（不许动）

- 写：`obsidian_writer.append()`（`445–464`）
- 读回：`obsidian_writer._parse()`（`471–492`，`@staticmethod`，**参数是 `text` 不是路径**）
- grep：`cl last`
- 抬头正则（`obsidian_writer.py:73`）：`_TS = re.compile(r"^> \[!abstract\] (\d\d:\d\d:\d\d)( ⭐ Exam Focus)?\s*$")`
  —— **行尾锚定**。加一个字段 `_parse` 就认不出那一条，会把 EN/ZH/ASR **静默盖到上一条头上**。

→ **本计划的新数据一律写旁路文件**（`.chapters.jsonl`），抬头格式**一个字节都不动**。

---

## 4. 范围与顺序

**做**：离线探针 · 数据层（主题落盘 / 章节合成 / 英文为主）· `streamq` 通道 ·
`overlay.py` 的课堂纲要与章节条 · 课务补强 · 下课交接。

**不做**：D20 逐条。

**顺序**（作者定的「离线 → 开课前 → 课中」）：

```
阶段 0 开工核实 → 阶段 1 建新模块（不接线）→ 阶段 2 离线探针【闸门】
              → 阶段 3 接 main.py → 阶段 4 界面 → 阶段 5 课务与交接
```

⚠️ **阶段 2 是闸门**：作者明确说「继续」之前**不进阶段 3**。
⚠️ **阶段 4 是闸门**：理解测验补齐（或作者书面豁免）之前**不开工**（D5）。

---

## 5. 阶段 0：开工前

### 5.1 要跑的与要记的

1. **确认工作区干净、记下 HEAD**。已在分支 `feature/ai-summary-panel` 上；
   回滚点 tag = `pre-ai-summary-panel-20260930`（指向 `ad2a494`）。
   ⚠️ 被 `.gitignore` 的数据（`glossary/` · `~/.classlive/` · `sessions/`）已 `cp -a` 到
   `~/Desktop/classlive-review/backup-20260930-pre-ai-panel/`（逐项数过：**6 / 4 / 730**）。
2. **跑基线**：`tests/test_audit_regressions.py` + `tests/test_wrapup.py`，把结果记下来。
   **改动前就是红的，写进交接说明，不在本计划里修。**
3. **读全文**：`transcript_view.py` · `build_notes._chat_json` · `find.py` ·
   `obsidian_writer._render_note` · `docs/experiments/probe_atomic_summary.py`（**同形状的既有
   探针，照它的约定写**）· `keypoints.py`（覆盖率要用）
4. **确认环境**：`ClassLive.app/Contents/MacOS/python` · `~/.classlive/jev-token` 存在
   （覆盖率要用）· DeepSeek key 可用

**完成判据**：基线数字**记在交接说明里**；六条文件读过的结论落进 §3；
`git status --porcelain` 除作者自己的 `docs/PLAN-audio-gain.md` 外为空。

### 5.2 先修的两件小事（**在阶段 1 之前**）

**① 模型名统一**（D2/D3）—— 三处定义点 → `deepseek-flash`：
```
atom.py:48                            MODEL = "deepseek-chat"   → "deepseek-flash"
classify.py:47                        MODEL = "deepseek-chat"   → 单独提交（D3）
docs/experiments/probe_atomic_summary.py:42  MODEL = "deepseek-chat"
                                      → 删掉常量，改 `from atom import MODEL`
                                      并注释：「2026-09-29 之前的结果用的是 deepseek-chat」
```

**② ⭐ 隔离朋友的课**（D4）—— ✅ **已做完（2026-09-30）**
让 `2026-09-11_140757_10730.md` **不计入任何课**。

⚠️ **实测更正了我自己写的一句话**：那个旁路文件住在 **`sessions/.attribution.json`**，
**不是** `~/.classlive/attribution.json` —— 写后者**没有任何作用**（`session_files` 根本不读那儿；
`courses.py:486-489` 还专门论证过为什么放 `sessions/`：「它描述的就是那些会话，放在旁边才自洽」）。

**做法**（作者已点头：「用 repo 既有机制」）：
- `courses.py` 新增 `NO_COURSE` 哨兵 + `course_matches` 里一条**显式守门** + `orphan_files` 跳过它
  （⚠️ 最后那条是实的：不跳的话它会出现在「没归课的上课记录」里，
  等于在诱用户再点一次「判给本课」）
- `sessions/` 里**只新建一个点开头的旁路文件**，任何 `.md` **一个字节不动**
- 判据在 `tests/test_courses.py`，⭐ **变异验证过**：删守门 / 删跳过**各红一条**，还原后 109/109

**实测效果**：
```
ECON10730 课次          9 → 8
corpus.keywords 里的     force · pressure · mathematically · calculation
                         · optimal · curves · derive · interacts   → **全部消失**
新增的经济词             budget · demand · curve · lectures
```
理由见 §12.1。

### 5.3 两份副本的处理（D1）

- 本文件落进 `docs/PLAN-live-summary.md` 后，**删掉** `~/Downloads/AI 实时总结：可执行实施计划.md`。
- 作者自己的 **Claude Doc** 加两处标注（**作者改**，我把文案给全）：
  1. 开头：`已迁移到 docs/PLAN-live-summary.md，以那份为准。`
  2. §9 第 1 条：`已核实：该版本矛盾在 HEAD 上不存在。证据与时间线见 repo 版 §1.1。`

---

## 6. 阶段 1：新模块（**不接线**）

阶段 1 结束时，实时总结的全部逻辑都在可单测的新模块里，**产品代码行为零变化**。
之所以先建模块、后做探针：**探针要跑的是生产代码本身**，而不是另写一份相似的逻辑。

> **模块seam 的设计**（`codebase-design`）：`live_summary.py` 的接口是 ~7 个方法，
> 背后是窗口规则 + 重试/积压 + 章节状态机 + 临时合成 + 课务正则。
> **两个真 seam**（各有 ≥2 个 adapter）：`chat`（生产 = `_chat_json` / 探针 = 桩与断网桩）
> 与 `emit`（生产 = `streamq.put` / 探针 = 收进文件）；`clock`/`stamp` 也注入（生产 = 真钟 /
> 测试 = 假钟）。**「一个 adapter 只是假想的 seam，两个才是真的」** —— 这里每一个都有两个。

### 6.1 `atom.py`：改成英文为主

**要改的四处**（其余一律不动）：

1. **`SYS`（`57–73`）的输出格式**改为：
   ```
   {"topic": "本段主题(不超过 8 个英文词)",
    "topic_zh": "中文标题",
    "points": [{"text": "一句简短英文",
                "kind": "主题|要点|定义|例子|课务|讲者强调",
                "src": [3, 4],
                "terms": [["price elasticity of demand", "需求价格弹性"]]}]}
   ```
2. **`SYS` 新增两条规则**：用简单句和常用词；`terms` 只放**关键术语和关键短语**，每条最多 3 对。
   ⚠️ **原有五条规则全部保留**（逐字）：「宁可空，不要凑」· `src` 必须是真实存在的行号、
   不许编 · `text` 单行不带换行 · 最多 5 条、不引入外部知识 · `讲者强调` 只标讲者**明确**强调的。
   ⚠️ `kind` 的取值**仍是中文枚举**（`KINDS`，`55`）—— 它是内部枚举，不是显示文字。
3. **`topic_of()`（`181–183`）**：截断 **12 → 80**。⚠️ 它进 `atom_st["prev"]` → 进下一窗 prompt 的
   「上一段的主题是「…」」→ **prompt 会变长**。这是**有意的**（12 会把英文标题拦腰截断），
   但在完成判据里要记一笔。
   **新增 `topic_zh_of(obj)`**：截断 **24**（与 `topic_of` 同一个写法，`_zh` 后缀）。
4. **`parse_reply()`（`135–168`）**：读每条的 `terms`，校验后写进 `Atom.terms`。
   校验：**只接受两个字符串组成的对** · 每个字符串 **≤60 字符** · **最多 3 对** · 其余丢弃。
   ⚠️ **`Atom` 的字段不增不改**（`as_json()` 的**键集合必须逐字不变** —— 判据 T16）。

⚠️ 历史 `.atoms.jsonl` 里的中文条目**不迁移**，照原样显示（渲染层不做版本判断）。

**完成判据**：`tests/test_atom.py` 全绿（含新增的 `terms` 校验组）；
`.atoms.jsonl` 每行的键集合仍是那 7 个（T16）；`atom.traceable` 的判据不受影响。

### 6.2 新建 `chapter.py`（形状照抄 `atom.py`：纯函数 + 写入器）

**常量**
```
MODEL         = atom.MODEL      ← ⚠️ 沿用同一个定义点，不新写字符串
MAX_TOKENS    = 900             ← 新。比 atom 的 700 大：一章要出 2–4 句 + 术语对
TEMPERATURE   = 0.0             ← 同 atom（要可复现）
MAX_SENTENCES = 4
CHAPTER_TAIL  = ".chapters.jsonl"
```

**`SYS_CHAPTER`**
- **输入**：本章标题 · 带全局句号的英文来源句（`"{gid}. {en}"`）· 本章原子要点 ·
  此前各章标题清单（**只给标题，不给摘要** —— 省 token，也避免模型被前文带偏）
- **输出**：`{"title": "…", "title_zh": "…", "sentences": [{"en": "…",
  "terms": [["英文","中文"]], "src": [全局句号], "flag": null}]}`
- **规则**：写 **2–4 句**，按「定义 → 推导或理由 → 结论」的顺序 · **保留公式**，口述的公式
  规范成 `Qd = 120 − 2P` 这种写法 · 讲解**依赖板书或图**（`as you can see here`）时
  `flag` 填 `"board"` · 内容**来自学生发言**时 `flag` 填 `"discussion"` ·
  **不引入外部知识** · 没有实质内容时 `sentences` 为空

**`Chapter` 数据类**
```
id · status（"interim" | "final"）· title · title_zh · t0 · t1（"HH:MM:SS"）
lo · hi（全局句号区间，1-based）· sentences · version
```
⚠️ **`version` 的精确定义（原稿没写）**：同一个 `id` 第 n 次合成就是 `version = n`。
临时版 1、2、3…，正式版是最后一次。`load()` 只按「最后一条」取，`version` 是给人看的。

**`build_prompt(sentences, atoms, prior_titles) -> str`** —— **纯函数**。

**`parse_reply(obj, lo, hi) -> list`** —— **这里是全部的机械闸门**：
- 某句的 `src` **为空或越出 `[lo, hi]`** → **整句丢掉，不修剪**（同 `atom.parse_reply` 的纪律：
  修剪 = 替模型编引用）
- `flag` 不在 `{None, "board", "discussion"}` 里 → **一律当 `None`**
- `terms` 用与 §6.1 **完全相同**的校验
- 最多 `MAX_SENTENCES` 句

**`ChapterWriter`** —— 照抄 `AtomWriter`（`atom.py:187–253`）的纪律：
- 每条记录写一行，**写完即 flush**，**不 fsync**；`close()` **幂等**；**文件不删**
- 路径 = 会话文件去掉后缀再加 `CHAPTER_TAIL`（同 `AtomWriter.__init__:201–207` 的拼法）
- ⚠️ **记录是三种**（§1.3 ② 更正了原稿的「两种」）：
  ```
  {"type": "window",   "w", "t", "topic", "topic_zh", "lo", "hi", "n_points"}
  {"type": "chapter",  "type", …Chapter 全部字段…}
  {"type": "deadline", "type", "t", "quote", "src", "source", "changed"}
  ```
  ⚠️ **`w` 的精确定义（原稿没写）**：窗口序号，**从 1 起**（窗口是第几次提交）。
  ⚠️ 临时版和正式版用**同一个 `id`**，**只追加，不改写旧行**
- ⭐ **`close()` 之后 `append()` 必须是 no-op**（D17）—— 不许照抄 `_handle()` 的重开行为。
  实现：一个 `_closed` 标志，`append` 开头判它

**`load(path)`** —— 返回：
- **全部**窗口记录
- 「每个 `id` **最后一条**」的章节记录（后写覆盖先写 → 正式版自然替换临时版）
- 合并后的 `deadline`（`src` 有交集就算同一条）
- 坏行跳过，同 `atom.load`（`256–274`）的宽容度

**课务正则**
```
DEADLINE_RE：不分大小写、按**词边界**（\b）
  覆盖 due · deadline · submit · submission · hand in · problem set · homework
       assignment · midterm · final exam · quiz · exam
CHANGED_RE：postpone · push back · pushed to · extend · extension · moved to
deadline_hits(en) -> dict | None      返回 {"changed": bool} 或 None
```
⚠️ **词边界是关键**：`exam` **不会**命中 `example`（判据 T14 钉住这条）。

### 6.3 新建 `live_summary.py`：`LiveSummarizer`

#### 构造参数（**全部可注入**，为了能单测）

| 参数 | 含义 | `main.py` 传什么 |
|---|---|---|
| `chat(sys, block, max_tokens, temperature)` | 返回 dict 或 None；**网络错误抛异常** | 包一层 `build_notes._chat_json`；没有 key 时返回 None |
| `append_atoms(atoms) -> int` | 写原子 | `writer.append_atoms`（`obsidian_writer.py:705`） |
| `chapter_path` | 章节文件路径，可为 None | `chapter_path_for(writer.session_path)` |
| `emit(payload)` | 推给界面 | **带 `stopping` 守卫**的 `streamq.put(("summary", payload))` |
| `clock` / `stamp` | 时钟与时间戳 | `time.monotonic` / `atom.now_stamp`（`277–279`） |
| `chapters_enabled` | 章节合成开关 | 环境变量，见 §8.4 |

#### 常量（⚠️ 每个都标明**搬来的**还是**新发明的**；新发明的必须有依据）

```
ATOM_EVERY_S    = 75.0   ← 从 main.py:1485 **原样搬**（连同它的注释）
ATOM_PAUSE_S    = 30.0   ← 从 main.py:1492 **原样搬**。⚠️ 它的注释说
                           「8 秒是错的、30 秒还没第二次实测背书」→ 阶段 2 要量窗口间隔中位数
INTERIM_AFTER_S = 480    ← 新。依据：一章的量级是 10 分钟，8 分钟后才值得给它做第一次临时合成
INTERIM_EVERY_S = 240    ← 新。依据：临时版每 4 分钟刷一次；一章只花一次合成的钱，比正式版便宜
RETRY_S         = 30     ← 新。依据（D11）：一次常见断网以分钟计；30 秒重试够快，也不会打爆
MAX_PENDING     = 20     ← 新。依据（D11）：20 × 典型窗口(≈45s) ≈ **15 分钟**，
                           覆盖一次常见断网。⚠️ 这个数**是推算不是测量**，注释里要写明
```

#### 公开方法

- **`feed(item)`** —— **主线程**调，只做 `put_nowait`，**永不抛异常**（整段包 `try`）。
  `item` 形状**同今天的 `atomq`**：`(全局句号, 时间, 英文, 中文)`。
  `finish()` / `close()` 之后调用**直接忽略**
- **`step(now)`** —— 执行一次循环体。⚠️ **原稿说它「不阻塞」，这句要拆开读**：
  - **不阻塞**指的是**取件**用非阻塞 `get_nowait`（`run` 那层才 `wait`），**不是**不调模型。
  - 窗满时的模型调用**会**阻塞几十秒 —— **那正是 worker 线程存在的理由**（主线程
    `drain()` 的不阻塞不变量管的是主线程，不是 worker）。
  - 探针和单测靠它**确定性地推进时间**
- **`run(running)`** —— worker 线程循环。⚠️ **原稿这里含糊，这是精确分工**：
  ```python
  while running.is_set():
      self._wake.wait(1.0)          # ⚠️ 只等唤醒，**不消费队列**
      self._wake.clear()
      self.step(self._clock())      # step 内部用 get_nowait 排空队列
  ```
  **为什么 `run` 不能自己 `get()`**：那样它会吃掉一条，而 `step` 看不到 —— 探针只调
  `step`，两条路会走岔。窗口规则与今天的 `atom_worker`（`1527–1542`）**完全一致**：
  记录开窗时刻 `t0` 和最后一句时刻 `last`，`now - t0 >= ATOM_EVERY_S` 或
  `now - last >= ATOM_PAUSE_S` 就提交。退出时置位内部的 `_stopped` 事件
- **`finish(timeout_s, cancel=None)`** —— **收尾 worker 线程里**调。顺序：
  ① 等 `_stopped` ② 把残余窗口作为**最后一个窗口**提交一次 ③ 对当前章做一次**正式合成**
  ④ 关 `ChapterWriter`。整体受 `timeout_s` 约束，**超时就放弃剩下的步骤**并在终端打一行说明。
  `cancel` 已置位时**不调模型，只关文件**
- **`close()`** —— 只关文件，**不调模型**（用于用户放弃笔记那条路径）
- **`calls`** —— 只读属性，累计模型调用次数（3.5 验收与探针统计用）

⚠️ **超时怎么加（D16）**：
- 给 `build_notes._chat_json` 加**可选** `timeout: float = 180`（**7 个调用方全不用改**）；
  `LiveSummarizer` 通过 `chat` 的包装传短值（如 20 秒）
- ⚠️ **httpx 的超时是按阶段算的**（连接 / 每次读取），**不是整个请求的总时长上限** ——
  一个断断续续回数据的响应可以超过 20 秒。所以 `finish` 仍然用 `join(timeout)` 兜底
- ⚠️ **兜底放弃的线程，结果回来后必须丢掉** —— `ChapterWriter.close()` 之后 `append()` 是
  no-op（§6.2），配单测 T21
- ⚠️ `_chat_json` **没有重试**（已核实），所以最坏耗时 = 1 × timeout，不用额外限次数

#### 模块级函数

`chapter_path_for(session_path)` —— 参数为 None 返回 None，否则会话文件去后缀 + `.chapters.jsonl`。

#### 提交一个窗口时的流程

1. 新窗口先进 `pending` 队列，**从最旧的开始处理**（今天失败就丢，这里改成**留着**）
2. 调原子提取。**抛异常或返回 None** → 窗口**留在队列里**，`RETRY_S` 后重试；
   队列超 `MAX_PENDING` → 丢**最旧**的一个，并 `emit` 一条 `gap`（`state="dropped"`）记录它的时间段
3. 成功 → `rebase(parse_reply(...))` → `append_atoms`（**产出的 `.atoms.jsonl` 行格式必须与今天
   逐字一致**）→ `emit` 一条 `atoms` → 写一条窗口记录 → `emit` 一条 `window`
4. **章节状态机**：
   - **主题为空** → 并入当前章。**还没有章就先缓存**，等第一个章开出时并入
   - **主题与当前章标题相同**（去空格、**不分大小写**比较）→ 并入
   - **主题不同** → **先对当前章做正式合成，再开新章**
   - **A→B→A 按新章处理，不解冻旧章**
5. **临时合成**：当前章已开 ≥`INTERIM_AFTER_S`，且距上次临时合成 >`INTERIM_EVERY_S` → 做一次，
   状态标 `interim`
6. **合成失败**：下一次 tick 重试**一次**；仍失败 → 写一条 `sentences` 为空的章节记录，
   界面退回只显示原子要点

⚠️ **原稿没写、但实现必须有的**：`LiveSummarizer` 要在内存里**留下当前章的英文原句**
（`[(gid, en)]`）**和它的原子** —— 因为 `build_prompt` 需要它们，而窗口 flush 后 `buf` 就清了。
这是本计划里唯一的**无界内存点**（一节 50 分钟 ≈ 600 句 ≈ 几十 KB，可接受；
但见 §12.3 那个 7 小时的异常会话）。

#### 线程纪律

除 `feed` 外，**一切只在 worker 线程里跑**。`ChapterWriter` **只由 worker 线程或 `finish()` 触碰**，
且 **`finish()` 必须等 `run()` 返回之后才动它**。

**完成判据**：§11.1 的 `tests/test_live_summary.py` **全绿**；阶段 0 记下的**基线结果不变**；
`git diff --stat main.py overlay.py` **为空**（一行未改）。

---

## 7. 阶段 2：离线回放探针

用阶段 1 的 `LiveSummarizer` **本身**，在真实历史课上离线回放，产出纲要全文和指标，由人评审。
**探针跑的是生产代码，不另写逻辑。** 阶段结束时有一个**闸门**。

### 7.1 脚本 `docs/experiments/probe_live_summary.py`

**命令**
```
probe_live_summary.py --session <会话 .md> [--session …] --out <目录>
                      [--dry] [--no-chapters] [--fail-window HH:MM-HH:MM]
```

**读输入** —— 用 `obsidian_writer._parse`（**不要新写解析器**）。⚠️ 三个精确点：
- `_parse` 是 `@staticmethod`（`471`），**参数是 `text` 不是路径** → 先 `read_text()`
- 返回 `[{"ts","star","en","zh","asr"}, …]`
- **全局句号 = 条目下标 + 1**（`writer.append` 每句 `_n += 1`，`_parse` 按顺序还原）
- **英文取值规则和 `drain():1587` 一样**：`en or asr`（有矫正版用矫正版，没有用原始 ASR）

**模拟时间**：以第一句为 0，按每句时间戳推进 `now`。**相邻两句之间每隔 1 秒调一次
`step(now)`** —— 这样 30 秒停顿规则才会**按真实节奏**触发。

**模型调用**：默认用 `cloud_translator.load_api_key()` 经 `build_notes._chat_json` **真实调用**。
`--dry` 用固定返回值的桩，只查流程。

**输出目录**：默认 `/tmp/classlive-probe/`，**不要放进仓库**（转录里有其他同学的声音）。

**五个产物**
| 文件 | 内容 |
|---|---|
| `<名>.atoms.jsonl` | 原子，格式与生产**一致** |
| `<名>.chapters.jsonl` | 窗口 / 章节 / 课务记录，由 `ChapterWriter` 写出 |
| `<名>.outline.md` | 按纲要界面的样子渲染：课务置顶；每章标题、时间段、英文合成句加中文术语、缺口标记；当前章的原子要点 |
| `<名>.metrics.json` | 见 §7.2 |
| `<名>.review.md` | 随机抽 30 条原子或合成句，每条附来源句，留一列「是否支撑：是 / 否 / 部分」给人填 |

### 7.2 `metrics.json` 的字段与口径

**原稿的字段**：句数 · 时长（分钟）· 窗口数 · 原子调用次数 · 正式合成次数 · 临时合成次数 ·
空窗口占比 · 章节数 · A→B→A 回摆次数 · 被标为依赖板书的合成句占比 · 正则命中的课务数 ·
模型提取的课务数 · 两者重合数 · 各类调用的输入/输出字符数 · 模拟时间下窗口间隔的**中位数**。

⭐ **新增（D8）**：**积压峰值** · **重试次数** · **临时合成触发次数** · **章节平均时长**。

**口径**：
- token：`_chat_json` **拿不到 `usage`** → 按**字符数 ÷ 4** 估算，
  **字段名里标明是估算**（`*_tokens_est`）
- ⚠️ **窗口间隔中位数要单独看**：`main.py:1487` 的注释里「中位 24 秒」是在**旧的 8 秒停顿
  阈值**下量的，改成 30 秒之后**还没人量过**

### 7.3 选课（D4 / D9 / D19）

| 类别 | 课 | 时长 | 句数 | 备注 |
|---|---|---|---|---|
| **理工** | `2026-09-11_140757_10730.md` | 42.3 | 482 | ⭐ **朋友的课，不放进任何课**（§12.1） |
| **讨论型** | `2026-09-28_120341_ECON10xxx.md` | 49.8 | 579 | SOC10020 的 Seminar（§12.2） |
| **经济** | `2026-09-29_150509_ECON10740.md` | 49.8 | 682 | |

⚠️ **D9 说先跑 1 节**（理工那节，最短且密）→ 确认管线 + **算清单节真实成本** → 再决定跑满 3 节。
⚠️ 那节只验「管线通不通」，**它的成本数字不代表一般的课**（最短且密）。

⚠️ **结果文件必须写明「理工课的代表性有限」**（作者明确要求）：
经济课的供需曲线**只能部分代替**理工课 —— **代表不了满黑板推导 / 大量符号 / 代码**。
所以「依赖板书的段落占比」这个数**只说明经济课的情况，不能当理工课的天花板**。
结果文件里要**明确写「理工课未覆盖」**（或写明只覆盖了朋友那节的一部分），**别让后来的人误读**。

### 7.4 ⭐ 覆盖率指标：先做编号映射 + 自检（D12 / D13）

**这一步不做完，不许出覆盖率数字。** 理由见 §1.5。

**映射怎么建** —— 用**同一条定义点**，不抄第二份过滤逻辑：

给 `keypoints.sentences(path, *, with_index=False)` 加一个**可选**参数；
`with_index=True` 时返回 `[(全局句号, 句子), …]`。
⚠️ 这样过滤规则**仍然只有一处**（`keypoints.py:**188**` 的 `if len(t) > 15`），
探针拿到的是**精确映射**，**不用按文本对**（按文本对会被重复句骗）。

**自检**：重建出来的过滤后句数**必须等于** `keypoints.sentences()` 的句数（本例 **447**），
且 `447 / 482 = 92.7%`。**不达标 → 覆盖率作废**，并在结果文件里写明。

**两个口径都记（D12）**：
- **(a′) 原子级**：该句被**某条原子的 `src`** 引用
- **(b) 合成级**：该句被**某条合成句的 `src`** 引用
- ⭐ **差值本身是信号** —— (a′) 高而 (b) 低 = 章节的**时间段抓对了**（该句确实属于这一章），
  但**合成时没把它引进去**（提炼漏了重点）。**这正是「读完能不能懂」最该暴露的失败形态。**
  ⚠️ 只记一个就把这个差丢了。

⚠️ **原稿的宽松口径（「落进某章的 `[lo,hi]`」）必须去掉** —— 章节首尾相接、铺满整节课，
几乎每句都落在某个 `[lo,hi]` 里，那个数会接近 **100%**，**量不出东西**；
而且它和 (b) 的差值**就等于 (b) 本身**。

⚠️ **成本**：`pick()` 按 `CHUNK = 20` 分批 → 40 分钟一节 ≈ **19 次** API 调用，三节 ≈ 57 次。
⚠️ **跑之前告诉作者**。

### 7.5 人工评审（D5 / D6 / D7）

1. **可追溯**：**填三份 `review.md`**（作者自己填），统计「是」的比例 —— **保留**
2. **静默**：每节随机看 3 个**空窗口**的来源句，判断是不是真的闲聊或课务杂事 —— **保留**
3. ~~理解测验~~ —— ⛔ **去掉**（要找人），**用 §7.4 的覆盖率顶替**，并写成阶段 4 的硬闸门（§7.6）
4. ~~英文可读性（请人标读不懂的句子）~~ —— ⛔ **去掉**（也要人）

### 7.6 闸门与可调清单

- 结果**只记数字和结论**，写进 `docs/experiments/RESULTS-live-summary.md`，**不贴转录原文**
- **闸门不预设阈值，由作者看完决定**。作者明确说「继续」之前**不进阶段 3**
- **可调项（D8：四个常量全在）**：`ATOM_PAUSE_S` · `INTERIM_AFTER_S` · `INTERIM_EVERY_S` ·
  `MAX_PENDING` · `RETRY_S` · 两份 prompt 的措辞。⚠️ **每调一次就重跑探针**，
  并把**调了什么**记进结果文件
- ⭐ **硬闸门（D5）**：理解测验**必须补**才能开阶段 4。写**三处**：
  ① 阶段 4 开头 ② 结果文件的未完成项 ③ §13 停手规则。
  **最低标准**：**1 节课 · 1 位没上过这节课的人**；可**远程**做 ——
  把 `outline.md` + 题目发过去，**15 分钟**就够。
  ⚠️ **作者可显式豁免**（书面决定跳过），但**必须写进 `RESULTS-live-summary.md`**：
  **谁 · 什么时候 · 为什么**。**闸门可被主人有意识地放行，不能被悄悄绕过。**

---

## 8. 阶段 3：接进 `main.py` 与 `streamq`

界面**不变**（悬浮窗还没有 `summary_update`，靠 `getattr` 守卫自然跳过）。
**唯一的可见变化是终端里多出章节标题行。**

### 8.1 替换原子层块（**精确边界**）

**删除 `main.py:1474–1544`**（从注释横幅起到 `threading.Thread(target=atom_worker, daemon=True).start()` 止），
**在同一个位置**换成构造 `LiveSummarizer` + 起线程。

**位置**：`run(args)`（`889–1853`）内，`src = load_source(...)` 成功（`1458`）且失败早退
（`1459–1472`）**之后**、`def drain():`（`1546`）**之前**。

**新块可用的闭包变量**（原块用到的就这几个）：
```
api_key_val    run() 局部，定义于 1006（load_api_key 的返回，str | None）
writer         run() 局部，定义于 1054
running        run() 局部，threading.Event，定义于 1020
echo           模块级函数，118
time           模块级 import，16
queue          模块级 import，16
threading      模块级 import，16   ← ⚠️ 原块 1544 用它起线程，新块也要
stopping       ⚠️ 原块**不使用**它；新块要用（emit 的守卫）
streamq        run() 局部，定义于 1080
```

### 8.2 改 `drain()`

**① `"final"` 分支末尾**（`1595–1598`）：`atomq.put_nowait(同一个元组)` → `summ.feed(同一个元组)`。
外面的 `if trans["mode"] != "raw"`（`1593`）和 `try`（`1594`）**保持原样**。

**② 新增 `"summary"` 分支** —— 放在 `"final"` 分支（`1586`）**之前**：
```python
elif item[0] == "summary":             # ("summary", payload)
    fn = getattr(ui, "summary_update", None)
    if callable(fn):
        fn(item[1])
    p = item[1]
    if p.get("kind") == "chapter" and p["chapter"].get("status") == "final":
        echo(f"[{now()}] 📑 {p['chapter'].get('title', '')}")
```

**③ 兜底分支** —— 整串 `elif` 的**最后**（现在的 `final` 分支之后）：
```python
else:
    echo(f"⚠ drain: 未识别的 streamq tag {item[0]!r}（已丢弃）")
```
不改变任何已有 tag 的行为，只让以后漏配分支时**出声**。
⚠️ `drain()` 本身**没有 `try/except`**（`1573–1575` 的注释点名了这件事）—— 所以 `echo` 是安全的，
但**不许在这里做任何可能抛的事**。

**④ 更新 `streamq` 声明处的注释**（`1080`），把 `"summary"` 加进 tag 清单。

**`payload` 的五种形状**（`LiveSummarizer.emit` 发出，界面**只读不改**）：

| `kind` | 其余字段 |
|---|---|
| `atoms` | `items`：`Atom.as_json()` 的列表 |
| `window` | `t` · `topic` · `topic_zh` · `lo` · `hi` · `n_points` |
| `chapter` | `chapter`：`Chapter` 全部字段，`status` 为 `interim` / `final` |
| `gap` | `t_from` · `t_to` · `state`：`pending`（离线中）/ `dropped`（超出积压上限） |
| `deadline` | `t` · `quote`（英文原句）· `src` · `source`（`regex` / `model`）· `changed` |

### 8.3 改收尾（**精确位置**）

- **正常路径**：`_wrap()`（`1756–1765`）里、`writer.close(...)`（`1758–1761`）**之前**加一行：
  ```python
  summ.finish(timeout_s=SUMMARY_FINISH_S, cancel=_cancel)
  ```
  包 `try/except` 并**打印失败原因**。
  ⚠️ `_cancel` 是**真实变量名**（定义在 `1748`，置位处：Ctrl+C 走 `1786`、
  「跳过精修」按钮走 `overlay.py:2405`）。
  `SUMMARY_FINISH_S = 25`，定义在 `main.py` 顶部常量区
  （`PARTIAL_MAX_S`(32) / `DEVICE_CHECK_S`(33) / `SLEEP_GAP_S`(34) 那一带）。
- **为什么必须在 `writer.close` 之前**：`AtomWriter._handle()`（`atom.py:213–216`）发现句柄为
  `None` 会**重新打开文件** → 在 `close()` 之后再写原子会**静默重开并泄漏句柄**（§3.1 ③）。
- **放弃路径**（`give_up` 为真，`1749–1790` 整块被跳过的那一支）：调 `summ.close()`，
  **只关文件，不调模型**。
- **进度提示（可选）**：`finish` 期间调一次 `_ui_progress(...)`（`main.py:1706–1710`，
  它转发给 `ui.wrapup_progress`）。⚠️ 已核实 `polish.progress_text` **接受任意名但会显示
  英文原名**（§1.4 #6）→ 要么用已有的（`polish` / `review`），要么**不加**。

### 8.4 开关与回滚

- 环境变量 `CLASSLIVE_LIVE_SUMMARY`：**阶段 3 默认 `"0"`（关），阶段 4 翻成 `"1"`**（D14）
- ⚠️ **「默认关 = 产品行为零变化」不成立**（作者指出），有两处改进**不收进开关**：
  ① 调用失败时窗口**不再丢弃**、改为留着重试（§6.3 第 2 步）
  ② 下课时**残余窗口补提交**（§6.3 `finish` 第 ② 步）
  这两处即使开关是 `"0"` 也生效 —— 它们是**有意的改进**（修的是今天确实存在的丢数据问题），
  **在 3.5 里单独验证**，不靠开关关掉
- 整个阶段回滚 = revert 这一次提交；阶段 1 的模块留着不影响任何东西
- 更新 `CLAUDE.md` 的文件地图，加入 `live_summary.py` 与 `chapter.py`；
  在「新增 streamq tag」那条规矩旁补一句：**已有兜底告警分支**

### 8.5 验收（⚠️ D15：**全部要在开关为 `"1"` 时跑**）

1. 默认闸门 `tests/test_audit_regressions.py` · `tests/test_wrapup.py` 与**阶段 0 基线一致**；
   `tests/test_live_summary.py` 全绿
2. **格式对拍**：用**同一段录音**，接线前后各跑一次文件回放
   （`--source file --path <录音> --ui terminal`）。对比 `.atoms.jsonl` **每行的键集合与类型**
   必须一致（**内容可以不同**）；会话 `.md` 的**抬头格式必须一致**
3. **收尾耗时**：记录接线前后「播完到进程退出」的时间。接线后多出的部分
   **不应超过 `SUMMARY_FINISH_S`**，且**不能出现等满 15 秒冲刷上限**的情况
4. **纯转录档**：回放中途切到纯转录后，**模型调用计数不再增加**（读 `LiveSummarizer.calls`）
5. **悬浮窗路径**：`--ui overlay` 跑一节，界面与今天**完全一样**，不报错
6. ⭐ **那两处不收进开关的改进**（D14）**单独验证**：
   ① 模拟一次模型失败 → 窗口在 `RETRY_S` 后重试成功
   ② 模拟一次「播完还有残余窗口」→ `finish()` 把它提交了

---

## 9. 阶段 4：界面（`overlay.py`）

两样东西：收起态顶栏左侧的**章节条**，展开态的「**课堂纲要**」接管视图。
纲要**复用答案接管的展开与还原机制**，渲染**只走 `_render()` 这一个入口**（`2508–2541`，
它是**唯一**喂 `self._tv` 的地方）。

> ⛔ **硬闸门（D5）**：开工前必须补齐 §7.6 的**理解测验**（或作者书面豁免）。

### 9.0 ⭐ 界面纪律（**动手前先读这节**）

来源两份：`impeccable` skill（模式 = **Operate**）+ 仓库自己的
`docs/RESEARCH-macos-aesthetic.md`（410 行，全是 `[一手]` 数字）。
⚠️ **仓库那份优先** —— 它是为本机这一块面板量的，比通用指南准。

**① 判据是「earned familiarity」，不是「好看」**（impeccable/Operate 逐字）

> 「Product UI's failure mode isn't flatness, it's **strangeness without purpose**...
> The bar is **earned familiarity**. The tool should disappear into the task.」
> 「**Consistency over surprise.** The same visual vocabulary screen to screen is a virtue.」

→ **沿用面板既有的视觉词汇，不发明新的**。具体到本计划：
章节条**纯文字 + 透明点击区**，**不做背景、不做胶囊、不做 hover 态** ——
`_make_click_view` 无视觉状态是**既有先例**（`_gloss_hit`，`750–751`），照办。

**② 对比度是一条硬门槛（可算，不是「看起来够清楚」）**

`docs/RESEARCH-macos-aesthetic.md §4` 逐字引 HIG › Dark Mode：

> 「At a minimum, make sure the contrast ratio between colors is **no lower than 4.5:1**.
> For custom foreground and background colors, strive for a contrast ratio of **7:1**,
> especially in **small text**.」

⭐ 那份文档自己就写了：「**这是可以算出来的**，比「看起来够清楚」强得多」+「**我们最该拿去当验收判据的**」。

→ **章节条是 12pt = 小字 → 目标是 7:1。这条必须实测，不许拍。**
⚠️ **量的范围要说清**：这个数**只对「面板内部底色」成立**，**不能**对「屏幕后面的 PPT」成立
（那份文档原话）。所以判据写成「**在面板的合成底色上** ≥4.5:1；能到 7:1 更好」。

⚠️ **原稿给的 `白色 alpha 0.60` 是拍的数**。仓库既有先例：`_draft_lbl` 用 **11.0pt / α0.50**、
`_draft_zh` 用 **12.5pt / α0.78**（`overlay.py:681` / `688`）。→ **量出来再定 alpha**，
不要因为「别的地方用 0.5/0.78」就取中间值。

**③ 不手工加字距**（那份文档 §2.1，本机 2026-09-26 实测）

`NSFont.systemFontOfSize_` **已经自带随字号变化的间距**（10pt→26pt 实测宽/字号从 1.03964
单调降到 0.90447，跨度 13%）。HIG 那张 tracking 表（13pt 时 −0.08）**再加一遍就是双重施加**。
→ 章节条**不加 `NSKernAttributeName`**。

**④ 不用细字重 · 最小可点尺寸 · 整数坐标**（三张硬表）

| 规则 | 出处 | 对章节条 |
|---|---|---|
| 「**avoid Ultralight, Thin, and Light**」 | HIG › Typography | 用 `Regular` 或 `Medium`，**不用 Light** |
| 最小可点 **20×20**；控件默认 28×28 | HIG | 点击区高 **24** ≥ 20 ✅ 过线 |
| ⚠️「**非整数坐标 → 文字发虚**」 | 那份文档 §7 指纹表 | ⭐ **宽度必须 `math.floor`**（见 §9.8） |
| 同心圆角 = **外圆角 − 内边距** | 那份文档 §1（三个独立来源） | 章节条**不加背景就不涉及**；⚠️ **一旦加背景/胶囊，圆角必须从容器算，不许硬编码**（面板外圆角 16pt） |

**⑤ 动效：三个来源取交集 = 0.20–0.25s**

```
impeccable/Operate   「150–250 ms on most transitions. Users are in flow」
macOS                「0.20–0.35 s；超过 0.4s 几乎肯定太慢」
仓库硬约束           动效 ≤ 0.433s（CFR §15.119）
→ 取交集：0.20–0.25s
```
⚠️ 本计划的纲要打开/关闭**默认不做动效**（impeccable：「Motion conveys state, not decoration」）。
若实现时决定加，落在这个区间；且必须尊重
`NSWorkspace.accessibilityDisplayShouldReduceMotion`。

**⑥ ⭐ 原稿漏了空状态**（impeccable/Operate：「Empty states that **teach the interface**,
not "nothing here."」）

纲要打开、但**一章都还没有**（刚上课 / 纯转录档切回来 / 模型一直失败）时显示什么？
⚠️ 原稿一个字没写。**必须定**（建议：一行小字说明「还没到该总结的时候」+ 当前主题或句数，
**不是**空白一片）。→ 见 §9.11。

**⑦ 小圆点 ` ● ` 是状态指示，可以上色**（这条是**支持**原稿的）

那份文档 §0 引 HUD 章：「**Use color sparingly in HUDs.** ...Often, you need only small
amounts of high-contrast color to **highlight important information**」。
→ 「有新章节」正是「important information」→ **给圆点一点颜色是官方支持的**。
⚠️ 但**不能用暖黄**（术语行 `_gloss_lbl` 占了，`744`）。具体颜色**量完对比度再定**。

### 9.1 新增状态（`Overlay.__init__`，`497–814`）

| 字段 | 初值 | 含义 |
|---|---|---|
| `_outline_on` | `False` | 纲要是否打开 |
| `_outline` | `{"deadlines":[], "chapters":{}, "atoms":[], "windows":[], "gaps":[], "marks":[]}` | 由 `summary_update` 累积；**只在主线程读写** |
| `_outline_snapshot` | `[]` | 打开那一刻生成的行；**打开期间冻结** |
| `_outline_seen_t` | `None` | 上次打开纲要的时刻（`HH:MM:SS`），放「上次看到这里」分隔线 |
| `_outline_new` | `False` | 上次打开之后有没有新的**正式**章节（章节条的小圆点） |
| `_mode_before_outline` | `True` | 打开前是不是收起态 |
| `_chapter_lbl` / `_chapter_hit` | `None` | 章节条标签 / 盖在上面的点击区 |

⚠️ `_chapter_hit` 用 `_make_click_view(self._toggle_outline)` 造（`overlay.py:467–493`，
模块级；用法先例 `_gloss_hit` `750–751`）。

### 9.2 `summary_update(payload)`（由 `drain()` 在**主线程**调用）

- 按 §8.2 的五种 `kind` 合并进 `self._outline`。`chapter` **按 `id` 覆盖**，正式版自然替换临时版
- `deadline` 去重：新条目的 `src` 与已有条目**有交集**就合并成一条，`source` **两个都记**
- 收到**正式**章节且纲要**没打开**时 → `_outline_new = True`
- ⚠️ **纲要打开期间不重建行**（快照冻结，只让章节条出小圆点）。
  理由：**不在读者眼皮底下挪动文字**。关掉再打开就会刷新
- 最后 `_mark_dirty(urgent=True)`（`2134–2138`）。**这里不做任何文字测量** ——
  保证主线程开销可以忽略（`drain()` 的不阻塞不变量）

### 9.3 纲要行的构造：**模块级纯函数**

仿 `rollup_lines`（`269–373`，模块级、不 import AppKit、测量由调用方注入）：
**不进窗口也能单测。**

**`fold_rows(text, fits, split) -> list[str]`**
按词贪心装行。`fits(s)` 判断一段文字能否放进**一个大字行槽**；单个词本身就放不下时用
`split(word)` 切开。
- 生产传入的 `fits` / `split` 包装现有的 `_answer_fits`（`2024–2038`）与
  `_answer_split_to_fit`（`2010–2022`）
- ⚠️ **已核实两者都不读写任何 `_answer_*` 状态**（只读 `self._tv.text_width()` 和
  `self._answer_font`）→ 所以给纲要复用是安全的，**但包装层也不许读写 `_answer_*`**
- 字号/阈值：`_answer_font = systemFontOfSize_weight_(18.0, 0.23)`（`644` / `174`）·
  `ANSWER_TEXT_H = 45.0`（`89`）· 快路径 `len(text) <= 24` 直接 True

**`build_outline_rows(outline, seen_t, mode, fold) -> list[tuple[str, str]]`**
返回「大字、小字」行的列表，顺序：
1. **课务置顶**，最新的在上：大字 ` 📌  ` + 英文原句（经 `fold` 折行）；
   小字 `时间 · 待确认`，改期的再加 `已改期`
2. **每个章节**按 `id`：
   - 标题行：大字 `▍标题`，小字 `开始–结束 · 中文标题`；**临时版**在小字末尾加 `· 临时`
   - 合成句 ⭐ **排法①（作者 2026-09-30 拍板，本节据此改过）**：
     **大字 = 中文译文（`zh`）**，折行后可能占多行；
     **小字 = 英文原句 + 术语对照 + flag 标记**，写成 `英文 · 英文 中文 · ⚠ 依赖板书或图`
     - ⚠️ **原规格是「大字 = 英文句」**，作者读完第一版纲要后的原话是
       「**大纲的中文占比要稍微多一点**，读起来是有点费劲」「**重点句可以中文翻译一遍**」。
     - ⭐ 理由：与**直播字幕一致**（那边也是中文那行更大）—— 仓库纪律
       「沿用既有视觉词汇，不发明新的」。
     - ⚠️ **`en` 和 `src` 一个都没丢**：可追溯性靠小字那行。
     - ⚠️ **没有 `zh` 时退回英文当大字** —— `zh` 缺失不该让内容消失。
   - `flag == "board"` 时小字前加 `⚠ 依赖板书或图，转录不完整`；`discussion` 时加 `课堂讨论`
   - **没有合成句的章，退回显示它的原子要点**
   - 落在本章时间段里的 ❓，插一行：大字 `❓ 你在 时间 标记了这里`
3. **进行中的章**（还没合成的原子）：标题行 `▍当前主题 · 进行中`，
   下面每条原子一行（大字 `• 要点`，小字为中文术语对照）
   - ⭐ **「进行中」是推出来的，不是新状态**：`_outline` 就 §9.1 那六个键，
     **不加 `current_*` 字段**（加了就是计划外的新状态）。
     推法 = 「`src` **没落进任何已合成章的 `[lo, hi]`** 的原子」；
     主题取 `windows[-1].topic`，没有就写 `当前主题`。
     ⚠️ **一个都没推出来时连标题行都不打**（否则会留一个空标题）。
4. **离线空档**：大字 `（开始–结束 未生成：离线）`
5. **分隔线**：插在**第一条时间晚于 `seen_t` 的行之前**，大字 `— 上次看到这里 —`；
   `seen_t` 为 `None` 时**不插**
- ⚠️ `mode == "en"`（只英·校）时**所有中文小字留空**；`mode == "raw"` 时**返回空列表**
- ⚠️ **行序即屏幕顺序**：`TranscriptView` 的文档视图不翻转，`items[0]` 在**最上方**
  （`_place:469–472` 用 `count - 1 - level` 取值）→ **列表第一个元素就是屏幕上最上面那行**

### 9.4 打开与关闭

**`_toggle_outline()`** —— **纯转录档直接返回**；否则在打开与关闭之间切换。

**`_open_outline()`**：
1. 答案接管正在显示 → 先调 `_clear_answer()`（`1847–1859`）。它只清屏上的答案、还原尺寸，
   `main.py` 里的问答历史**不受影响**
2. **用当前宽度**生成 `self._outline_snapshot = build_outline_rows(...)`
3. **然后**把 `self._outline_seen_t` 更新为当前时间 —— ⚠️ **顺序不能反**，快照要用**旧值**
4. 记下 `self._mode_before_outline = self._collapsed`；如果是收起态，调 `_apply_mode(False)` 展开
   （`1130–1178`）
5. `_outline_on = True` · `_outline_new = False` · `_mark_dirty(urgent=True)`

**`_close_outline()`** —— `_outline_on = False`；如果打开时改过尺寸，
用 `_apply_mode(self._mode_before_outline)` 还原；`_mark_dirty(urgent=True)`。

**和答案接管的互动**：在 `_answer_enter()`（`1833–1845`）里，如果此刻 `_outline_on` 为真 →
先把 `_outline_on` 置为 `False`，并令 `self._mode_before_answer = self._mode_before_outline`
（该字段定义在 `580`），然后**跳过展开**（已经是展开态）。
这样按「新话题」退出答案后，会回到打开纲要**之前**的尺寸和字幕，而不是停在展开态。

### 9.5 `_render()` 新增分支

在现有 `if self._answer_on:`（`2517`）之后、`elif self._tv_mode != "cap":`（`2528`）**之前**插入：
```python
elif self._outline_on:
    if self._tv_mode != "outline":
        self._tv_mode = "outline"
        self._tv.set_rows_verbatim(True)
        self._tv.set_scroll_hold(True)        # 读纲要期间不许自动回底
        self._tv.replace_items(self._outline_snapshot)
        self._scroll_outline_to_divider()
```
⚠️ `_tv_mode`（`581`）的取值从两个变三个（`"cap"` / `"ans"` / `"outline"`）。
⭐ **精确口径**：`overlay.py` 内读它的只有 `1853` / `2519` / `2528` 三处；
⚠️ **但全仓不止** —— `tests/test_audit_regressions.py:544 / 565 / 648` 也读 `o._tv_mode`
（断言 `"ans"` / `"cap"`）。那三条断言走的是 `_answer_on` 路径，
**新增 `"outline"` 档不会让它们变红**；但别照「只有三处」去改代码。
现有的 `elif self._tv_mode != "cap":` 分支（`2528–2533`）会在纲要关闭后把字幕换回来，
**不需要改**。从纲要切到答案，由答案分支里现有的 `if self._tv_mode != "ans":`（`2519`）处理。

### 9.6 滚到分隔线

`_scroll_outline_to_divider()`：有分隔线就滚到它所在的行，没有就调 `scroll_to_top()`（`332–341`）。
⚠️ **`TranscriptView` 没有「滚动到第 N 行」的方法**（§1.4 #4）→ **新增**一个。
**三条硬约束**：
1. 遵守回收池的「**所有行等高**」不变量 —— 位置就是 `level * self._row_h`（`_layout:480`）
2. 坐标**不翻转**：`origin.y = 0` 是**底部**，`level L` 在 `y = L * row_h`
3. ⚠️ **滚完必须回写 `self._expected_origin = clip.bounds().origin.y`** ——
   `scroll_to_top:340` / `scroll_to_bottom:326` 都这么做，否则 `tick()` 会把程序滚动
   **误判成用户滚动**（并因此关掉跟随）

### 9.7 宽度变化

`_sync_panel_size()`（`1376–1432`）里宽度变化那段加一条：
纲要打开时用**新宽度**重新生成快照，把 `_tv_mode` 置为 `None` 让下一帧重新 `replace_items`，
然后滚回分隔线。
**插入点**：`1430–1431` 那段（那里现在调 `_answer_refold()`，是它的**唯一**调用点）。
理由同 `_answer_refold`：**快照是按旧宽度折的行，宽度变了会被 AppKit 静默截断**
（`_answer_fits:2025` 的注释点名了「AppKit 静默截掉，不留省略号」）。

### 9.8 章节条（`_layout()`，`1434–1494`）

- 顶栏按钮摆完之后（`1488–1491` 的循环），⭐ **最左按钮的左边缘 = `x + gap`**
  （`x` 是循环结束时的值，`gap = 6.0` 定义在 `1472`）。
  **可用宽度 = `(x + gap) - PAD`** —— ⚠️ **§1.3 ① 更正了原稿的算术**
- **三个条件全满足才显示**：可用宽度 **≥ 90** · 不是纯转录档 · 已有章节或当前主题。
  否则标签和点击区**一起隐藏**
- 位置：`(PAD, self._height - 28, 可用宽度, 24)`（与顶栏按钮同一行：`y = self._height - 28`，高 `24`）
  - ⚠️ **宽度必须 `math.floor`**（§9.0 ④）：`可用宽度` 是从 `sizeToFit()` 出来的按钮宽
    累减得到的，**几乎必然是小数** → 不取整会让标签和点击区落在**半像素**上，**文字发虚**。
    取整之后**标签与点击区用同一个数**（同框），别各算各的
  - ⚠️ `y = self._height - 28` 里的 `_height` 本身可能是小数（`_apply_mode` 里
    `_scroll_h` 可以是 `0.60 × 屏高`）—— 这是**顶栏所有按钮的既有状况**，不是本章节条引入的。
    **记一笔，不在本计划里修**（[[scope-claims-must-match-delivery]]）
- 文字：新章节小圆点（` ●  `）+ `▸ 英文标题 · since 开始时间`。单行，尾部省略
  （`NSLineBreakByTruncatingTail`）。⚠️ **用开始时间而不是已进行分钟数** —— 那样**不需要定时刷新**
- 样式：**12pt**，白色 alpha **0.65**（⭐ **2026-09-30 实测定的，不是拍的** —— 见下）。
  ⚠️ **不用暖黄**（暖黄已被术语行 `_gloss_lbl` 占用）

  **实测依据**（`overlay.CHAPTER_FONT_SIZE` / `CHAPTER_TEXT_ALPHA` 的注释里也有一份）：
  量法照那份调研自己的纪律 —— **离屏的幅度不是屏幕上的幅度**（§3）→ **上屏截图取样**：
  铺一块**满屏纯色**当底，再把面板压上去，**每张都亲眼看**。

  ⭐ **面板内部的底色随后面是什么而剧烈变化**（面板内部本身完全均匀）：

  | 面板底下 | 实测底色 | 亮度 |
  |---|---|---|
  | 纯黑铺满 | `#100d0e` | 0.0043 |
  | 纯白铺满（= **白幻灯片**） | `#686566` | 0.1321 |
  | | | **差 30.7 倍** |

  | α | 黑底之上 | 白底之上 |
  |---|---|---|
  | 0.50（`_draft_lbl`） | 5.34 | **2.73 ❌** |
  | **0.65（本章节条）** | **8.38 ✅7:1** | **3.49 ❌** |
  | 0.78（`_draft_zh`） | 11.79 | 4.26 ⚠️ |
  | 0.95（`_gloss_lbl`） | 11.15 | **3.48 ❌** |
  | 1.00 | 19.34 | 5.77 |

  ⚠️⚠️ **白底之下，面板里没有一档文字够得着 4.5:1** —— 要够到需 α≈**0.818**，
  而 **7:1 在任何 α 下都到不了**。这是**面板整体的既有性质**（不是本章节条引入的），
  根子在 `panel.py` 的 `SCRIM_ALPHA`。
  ⭐ 本条选 **0.65**：暗底上稳过 7:1，白底上与既有各档**同一水平**（沿用既有层次）。
  ⚠️ **治本要动 `panel.py` 的 `SCRIM_ALPHA`** —— 那是另一件事、影响整块面板，**单独评估**。
  ⚠️ **范围**：只对**面板内部**的合成底色成立，**不对「屏幕后面的 PPT」直接成立**。

### 9.9 入口（D18）

- **章节条可点**：主入口（`_chapter_hit` 盖在上面）
- **菜单栏新增「课堂纲要」**：`_install_status_item()`（`853–929`）里，
  放在「开课前的准备…」（`mi_prep`，`896–901`）**之前**。调 `_toggle_outline`。
  它**不占顶栏宽度、永远点得到**，拖拽验证不通过时它就是**唯一入口**
- ⚠️ **纯转录档下这一项要置灰**：`setEnabled_(False)`，和章节条隐藏保持一致 ——
  否则用户点了没任何反应，会以为功能坏了
  - ⭐ **风险提示**：`_install_status_item` 里三项都用 `_make_button_target(...)` +
    `setTarget_` / `setAction_("clicked:")`（先例 `887–891`），但 **`NSMenuItem.setEnabled_`
    在本仓没有先例**（全仓唯一 `setEnabled_` 用在 `whatsnew.py:285/289/312` 的 `NSButton` 上）。
    ⚠️ `NSMenu` 默认 `autoenablesItems = True` 会**按 target/action 的有效性自动改**
    `enabled` —— 所以置灰那条要**显式 `setEnabled_(False)`** 并在切换时改回来，
    且要**实测一次**（判据 T25）确认没被 autoenable 覆盖

### 9.10 其余接线

- **`_lost()`**（`2220–2223`）：调 `_on_lost()` **之前**，先
  `self._outline["marks"].append(time.strftime("%H:%M:%S"))`
- **`set_trans_mode(mode)`**（`2261–2277`）：切到 `"raw"` 时，纲要开着就**关掉**，
  并**隐藏章节条、置灰菜单项**
- **`TerminalUI`**（`main.py:362–432`）：**不加** `summary_update`，靠 `drain()` 的
  `getattr` 守卫跳过（既有惯例：`1576–1582`）

**阶段 4 完成判据**：`tests/test_panel.py` 的**宽度扫描**里新增断言 ——
章节条**不压到任何可见按钮**；可用宽度不足 90 时**隐藏**；置灰状态与档位一致；
⭐ **宽度是整数**（`floor` 生效）。
外加 §11.3 的**七条人工清单**（每条都要**跑图对比**）。

### 9.11 ⭐ 空状态（**原稿漏了**）

`_outline_on` 为真但 `self._outline` 里**一章都没有**时 —— 刚上课、模型全失败、
或从纯转录档切回来 —— 快照会是**空的**，屏幕上**什么都没有**。

⚠️ 按 impeccable/Operate：「Empty states that **teach the interface**, not『nothing here.』」

**建议的三种情形要分开**（各自一行小字，**都不是空白**）：

| 情形 | 判据 | 显示 |
|---|---|---|
| 刚开课，还没到总结的时候 | 有窗口记录、没有章节 | `▍ AI 实时总结 · 已 N 句 · 等主题稳定后出章节` |
| ~~模型一直失败 / 离线~~ | ~~有 `gap` 且 `state="pending"`~~ | ⛔ **2026-09-30 查明是死代码，已删** —— ② 那一步对每个 gap 都 `_push` 一行（那行文案永远非空）→ 有 gap 时 `out` 必然非空 → `if not out:` 进不去。⭐ 想要的那句话本来就送达到了，而且更好：`（15:30–15:35 未生成：离线）`，**带具体时间段、有章节时也在**。旧那句「`（离线中 —— 这几分钟的内容没生成）`」**已作废** |
| 纯转录档 | `_trans_mode == "raw"` | ⚠️ 这种**根本打不开纲要**（`_toggle_outline` 直接返回）→ 走不到这里 |

⭐ **作者 2026-09-30 追加的「用户指引」**（原话：「第一次打开还没有总结的时候就显示
AI 实时总结点这里，然后直到有总结信息再替换掉那行占位」）—— **两个面都改**：

| 面 | 文案 | 有内容之后 |
|---|---|---|
| 顶栏章节条（空档） | `▸ AI 实时总结 · 点这里` | 自动换成 `● ▸ 标题 · since 时间` |
| 面板空状态 | `▍ AI 实时总结 · 内容会自动出现在这里` | 被真章节顶掉 |

⚠️ 实测：新占位 **117.45px**（12pt Regular），章节条门槛是 **90px**（`CHAPTER_MIN_W`）→
**够**；面板更窄时照旧尾部截断（那是既有行为，有标题那两档一样会截）。
⚠️ 这一条**是设计决定不是机械规则** —— 文案已给作者过目（他选了「两处都改」）。

---

## 10. 阶段 5：课务与下课交接

### 10.1 课务检测（写在 `LiveSummarizer` 里）

- **正则路径**：`step()` 每从队列取到一句，**先**跑 `chapter.deadline_hits(英文)`，**再**进窗口。
  命中就**立刻** `emit` 一条 `deadline`（`t` = 这句的时间 · `quote` = 英文原句 ·
  `src` = 这句的全局句号 · `source` = `regex` · `changed` 取正则结果），
  同时往 `.chapters.jsonl` 写一条 `{"type":"deadline",…}`。
  **不调模型** → 一两秒内就能上屏
- **模型路径**：原子解析完后，`kind == "课务"` 的原子各 `emit` 一条 `deadline`
  （`quote` = 原子文本 · `src` = 原子的 `src` · `source` = `model`），同样写进文件
- **合并**：`src` **有交集**就算同一条。界面按 §9.2 合并；`chapter.load()` 读文件时**按同一规则**合并
- **纯转录档**：`drain()` 不喂数据 → **没有课务**。正则本身不调模型，以后可以单独放开，
  **本计划不做**
- **本计划不做**：相对日期换算（`next Friday` 之类）· 与课程大纲比对。
  v1 一律显示**原话、时间、「待确认」**
- **误报**：正则**按词边界**，`exam` 不会命中 `example`。课堂里「讨论考试」之类会误报，
  但每条都显示原句，用户自己能判断。阶段 2 的探针会给出**正则命中数 vs 模型命中数**的对比，
  据此调词表

### 10.2 下课交接：写进 Obsidian 笔记

**位置**：`obsidian_writer._render_note`（`878–1026`）里。
⚠️ 原稿说「在笔记标题之后、复习层之前」—— 那是一个**区间**不是位置，这里**定死**：

> **紧跟在 `## 🎯 Overview 概览`（`913`）那一段之后、`## 📚 Key Concepts 知识点详解`（`926`）之前。**
>
> 理由：纲要是「这节课的结构」，与概览同类；术语表（`## 💡 Terminology`）和复习自测都在更后面，
> 插在前面会让读者**先看细节再看骨架**。
> *（这一处是我定的，作者想挪就说一声 —— 挪的成本只是一行。）*

**内容**：
1. `### Deadlines · 课务`：每条写**英文原话、时间、来源**，并标「**待确认**」
2. 每个**正式**章节一个 `###` 标题，写「**标题（开始–结束）**」；下面是英文合成句，
   每句后**括注中文术语**，带板书 / 讨论标记的照写。
   **没有合成句的章，列出它的原子要点**

**数据来源**：`chapter.load(章节文件路径)`。**文件不存在就整节跳过，不报错**
（旧会话 / 纯转录档 / 开关关闭时都是这种情况）。

**隔离**：这一节用 `try/except` 包住，失败**只打一行警告** —— 沿用 `obsidian_writer` 里
「**附加层不许拖垮主体**」的写法（同 `close()` 里 qa/lost/keypoints 那五层护栏，`1158–1183`）。

**边界**：**只改输出到 Obsidian 的笔记，不动会话 `.md` 的格式**。

**前提**：§8.3 已保证 `summ.finish()` 在 `writer.close()` **之前**完成，
所以渲染时最后一章的正式版**已经在文件里了**。

### 10.3 验收

1. 用 macOS 自带 `say -o deadline.aiff "The problem set is due next Friday."` 生成一段音频，
   放在一段正常录音**中间**做文件回放 → 纲要**置顶在 2 秒内**出现这条课务；
   笔记的 Deadlines 小节里有它
2. 同一句再说一遍 `we pushed the deadline to Wednesday` → 这条标「**已改期**」
3. **删掉 `.chapters.jsonl`** 后重新生成笔记 → 笔记**照常生成**，只是少了纲要这一节

---

## 11. 测试与验收总表

新逻辑**全部用纯函数 + 注入的假时钟、假模型**测试，**不联网、不起 AppKit**。

**判据文件的骨架照 `tests/test_atom.py`**（⚠️ **不是** `test_translator.py` 那种 `case` 装饰器风格）：
```python
RESULTS: list[tuple[str, bool, str]] = []
def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail)); print(...)
def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="cl_..."))
    try: ...各组...
    finally: shutil.rmtree(tmp, ignore_errors=True)
    bad = [n for n, ok, _ in RESULTS if not ok]
    return 1 if bad else 0
if __name__ == "__main__": sys.exit(main())
```
⚠️ **`bad` 必须在结算行同一个地方算** —— `tests/test_entry_panel.py` 栽过一次：
`bad` 算在中间，**加在它后面的判据只打印、不进统计、失败也不影响退出码**
（[[judges-that-look-like-they-test]]）。

每条关键判据的 docstring 都要写明「**把实现的哪一行改坏，这条会红**」，
并且**真的改坏一次确认它会红，再改回来**。

### 11.1 `tests/test_live_summary.py`

| # | 测什么 | 改坏哪里会红 |
|---|---|---|
| T1 | 窗口规则与今天一致：句在 0/5/10s 进来，窗口在 40s（最后一句 +30s 停顿）提交；每 5s 一句时在 75s 提交 | 改 `ATOM_PAUSE_S` / `ATOM_EVERY_S` 或提交判据 |
| T2 | 模型第一次抛异常：**不写原子**；`RETRY_S` 之后成功，这个窗口的原子被写入**且顺序不变** | 失败时丢掉窗口 |
| T3 | 模型一直失败：积压停在 `MAX_PENDING`，发出一条 `gap`（`state="dropped"`） | 去掉积压上限 |
| T4 | 主题序列 A、A、B：对 A **恰好一次**正式合成，句号区间覆盖 A 的**全部**句子 | 切换时不合成，或合成两次 |
| T5 | 空主题并入当前章；**开场的空主题窗口并入第一章** | 空主题开了新章 |
| T6 | A、B、A 产生**三个**章节，第一章冻结后**不再写新记录** | 把回摆并回旧章 |
| T7 | 章开满 8 分钟产生一条 `interim`；换题后**同 `id`** 产生 `final`；`load()` 返回正式版 | 临时版与正式版用了不同 `id`，或 `load` 取了第一条 |
| T8 | 章节闸门：越界 `src` 的句子**整句丢弃**；非法 `flag` → `None`；超 4 句截断 | **修剪** `src` 而不是丢弃 |
| T9 | `atom.parse_reply` 的 `terms` 校验：只收两个字符串的对 · 长度上限 · 最多 3 对 | 放宽校验 |
| T10 | `topic_of` 不再把英文标题截在 12 个字符 | 截断长度改回 12 |
| T11 | `finish()`：残余窗口提交一次 · 当前章正式合成一次 · 关写入器；`cancel` 已置位时**模型调用 = 0**，文件照样关 | 收尾不提交残余窗口，或忽略 `cancel` |
| T12 | `finish()` 超时：假模型每次睡 5s，`timeout_s=1` 时**约 1 秒内返回** | 去掉超时 |
| T13 | `feed()` **永不抛异常**；`finish()` 之后的 `feed` **被忽略** | `feed` 里做了会抛异常的事 |
| T14 | 课务正则：`due next Friday`/`problem set`/`midterm`/`hand in` 命中；`for example`/`examine` **不**命中；`postponed`/`pushed to Wednesday` 标改期 | 去掉词边界，或删掉改期词 |
| T15 | 课务合并：`src` 有交集的正则条目与模型条目，`load()` 后**只剩一条** | 合并规则失效 |
| T16 | `.atoms.jsonl` 每行的**键集合恰好**是 `id`·`t`·`epoch`·`src`·`kind`·`text`·`terms` | 往 `Atom` 加了字段 |
| T17 | `fold_rows`（按字数算的假 `fits`）：没有一行超出；超长单词被切开；空串返回空列表 | 去掉单词切分 |
| T18 | `build_outline_rows`：课务在最前；分隔线按 `seen_t` 插对位置；`"en"` 档中文小字为空；`"raw"` 档返回空；没有合成句的章退回原子要点；临时章带「临时」 | 任一规则被改动 |
| **T19** ⭐新 | **`run()` 与 `step()` 的分工**：`run` 的 `_wake.wait` **不消费**队列，喂 3 条后 `step` 全部看得到 | `run` 改成 `get()` 消费 |
| **T20** ⭐新 | **关闭后拒绝写入**：`ChapterWriter.close()` 之后 `append` 是 **no-op**，文件字节**不变** | 照抄 `AtomWriter` 的重开行为 |
| **T21** ⭐新 | **迟到的写入**：模拟一个被 `finish` 放弃的写入在 `finish` **之后**返回 → 文件**没有被写**（D16/D17 的那条单测） | 写入器没有 `_closed` 标志 |
| **T22** ⭐新 | **编号映射**：`keypoints.sentences(with_index=True)` 的句数与 `sentences()` **相等**，且全局句号**单调递增**、且**确实跳过了短句** | 过滤规则分叉 |
| **T23** ⭐新 | **覆盖率两个口径**：喂一份已知的 atoms + chapters + 句表，`(a′)` 与 `(b)` 的值**按手算结果** | 命中判据写错 |
| **T24** ⭐新 | **`--fail-window` 的口径**：失败判据看**调用那一刻的模拟时间**，不是窗口里句子的时间戳 | 用句子时间戳判 |

### 11.2 每个阶段要跑什么

| 阶段 | 自动测试 | 人工验收 |
|---|---|---|
| 1 | `tests/test_live_summary.py` + 阶段 0 记下的**基线** | 无 |
| 2 | 同上 | §7.5 的评审（去掉两条） |
| 3 | 加 `tests/test_audit_regressions.py` · `tests/test_wrapup.py` | §8.5 的**六项** |
| 4 | 加 `tests/test_panel.py`（宽度扫描新增断言） | §11.3 的**七条** |
| 5 | 笔记渲染测试：用一份**固定的 `.chapters.jsonl`** 当输入 | §10.3 的三项 |

### 11.3 阶段 4 的人工清单

⚠️ **离线判据看不见「看得见吗」** → 每条都要**改前改后各拍一张图**对比
（[[ui-criteria-two-blind-spots]]）。

1. `probe_drag.py`：在顶栏空白处和正文区拖动，窗口**照常移动**；
   点章节条**只**打开纲要，**不**拖动窗口
2. 若改了 `transcript_view.py` 的滚动：按 `CLAUDE.md` 的说明，`probe_scroll.py`
   **连跑三次**（它本身会翻转）
3. 上课中打开纲要**五分钟**再关掉：这五分钟的字幕**全部出现，一句不少**
4. 收起态 → 打开纲要 → 点「讲一下」→ 点「新话题」：回到字幕，面板回到**收起态的高度**
5. 答案正在显示时打开纲要：答案被清掉、纲要出现；**终端里的问答记录还在**
6. 纲要打开时把窗口**拖窄**：内容重新折行，**没有被截掉的句子**
7. 切到纯转录档：纲要**自动关闭**，章节条**消失**，菜单项**置灰**

---

## 12. 已知问题（**不在本计划里，但你必须知道**）

### 12.1 ⭐⭐ 朋友的课正在污染 `ECON10730`（**已实测**）

```
ECON10730 的术语表 = "Data Analysis for Economists"（回归 / OLS / p-value）
它的转录词表（corpus.keywords 现跑）= 
  begins, calculation, constrained, fail, fundamental, optimal, step, basis,
  constraint, curves, derive, force, interacts, mathematically, pressure, ...
                        ↑↑↑↑↑        ↑↑↑↑↑           ↑↑↑↑↑↑↑↑↑
```

`force` / `pressure` / `mathematically` / `calculation` **全来自朋友的**热力学那节
（`2026-09-11_140757_10730.md`，STEM 命中 **168 : 8**）。

**机制**：`courses.course_matches` 是 `course == c or course.endswith(c)`
（`courses.py:541`）→ `"ECON10730".endswith("10730")` 为真 → 任何文件名叫 `10730` 的都算 ECON10730。
而 `10730` 这个短号**被两门课共用**：
```
2026-09-11_013747_10730.md   "unconstrained choice"    ← 真是经济
2026-09-11_140757_10730.md   热力学（朋友的课）          ← 污染源
```

**后果**：这些词被当候选术语**注入 ECON10730 的翻译 prompt**（`extra_terms` 那条路，
2026-09-29 刚上线）。**这是活的缺陷** → ✅ **2026-09-30 已修**，见 §5.2 ②
（`ECON10730` 课次 9 → 8，热力学术语从 `corpus.keywords` 里全部消失）。

### 12.2 老计划 §5.2 的「归课能救两门课」**是错的**

`docs/PLAN-panel-ux.md` 那一轮的建议是：把没归课的 8 个 `LECTURE` 记录判给经济课，
好让 `ECON10740` / `ECON10790` 各差的那 1 节补上、结束降级。

⚠️ **但那 8 个 `LECTURE` 全是朋友的课（STEM）**：
```
watts per kilogram · Every turn of the equation has to have the same deal
Equation, we'll be using this over and over again · the amount of water in the pipe
```

**判给经济课 = 把热力学灌进经济课。** 那条建议**就地作废**。

（另注：`2026-09-28_120341_ECON10xxx.md` 那个 2897 行的文件是 **SOC10020 的 Seminar**，
不是经济课 —— 内容为「the world gets divided into different kingdoms」等社会学内容。）

### 12.3 数据质量：两个异常会话

```
413.8 min / 1084 句  2026-09-22_150213_ECON10740.md
103.7 min / 1233 句  2026-09-15_150616_ECON10740.md
```

都是「整晚没关」的会话（时间跨度以小时计，内容跨多个话题）。⚠️ **不要拿它们当探针样本** ——
`LiveSummarizer` 的章节状态机会把它们切成一堆章，而且**内存会一直涨**（§6.3 那个无界点）。

### 12.4 顺带发现的一处过期注释（不修，记一笔）

`overlay.py:2025` 的 `_answer_fits` docstring 提到 `ANSWER_MAX_LINES`，
但**全仓没有这个常量** —— 实际阈值是 `ANSWER_TEXT_H = 45.0`（`overlay.py:89`）。
不在本计划范围内（[[scope-claims-must-match-delivery]] 那条纪律：别顺手扩大改动面）。

---

## 13. 停手规则

### 执行中**必须停下来问作者**的情况

- **任何一个阶段的闸门没过**，或者**阶段 0 记下的基线结果发生了变化**
- 需要改**会话 `.md` 的格式**、`atom.KINDS` 的取值，或 **`Atom` 的字段**
- 想加**本地模型兜底**，或想在**纯转录档**发任何模型请求
- 想新增 **`"summary"` 以外**的 `streamq` tag
- 需要在 **`objc_own.py` 以外**定义新的 ObjC 类
- 需要改 **`entry_panel.py`**（二级面板不在本计划范围内）
- ⭐ **阶段 4 开工前没补理解测验、也没有作者的书面豁免**（D5）
- ⭐ **探针要跑满三节之前**（先报单节成本与结果，等作者点头）（D9）

### 每个阶段结束时**交回**的东西

一份简短报告：**改了哪些文件 · 跑了哪些测试、各自结果 · 和本计划有哪些偏离、为什么偏离**。
阶段 2 另外交 `docs/experiments/RESULTS-live-summary.md`。

---

## 14. 雷区（动手前必读）

**来自代码注释的既有规矩**（不是新定的）：

- 主线程（`drain()`、AppKit 回调）**只做立刻返回的事**：`queue.put`、赋值、打印。
  **不联网、不 sleep、不长持锁**
- 新增 `streamq` tag **必须**在 `drain()` 加对应分支，且用 `getattr(ui, ...)` 守卫
  （`TerminalUI` 没有悬浮窗的方法；`drain` 没有 `try/except`，直接调会 `AttributeError`
  **打死主循环**）
- **纯转录档一个模型请求都不发**。档位**课中可切**（`trans["mode"]`，`main.py:1029`），
  所以在**取件时**判断，不在启动时判断
- **会话 `.md` 的抬头格式不许动**（三方共享契约，§3.3）。新数据一律写旁路文件
- **新的 ObjC 类只能定义在 `objc_own.py`**
- 讲解与总结**都不用本地 Qwen 兜底**

**仓库硬规矩**（见 `CLAUDE.md`）：

- ⚠️ **`sessions/` 只读不删、禁通配符** —— 曾误删过一节真实课堂记录，不可恢复
  （[[never-delete-user-session-data]]）
- ⚠️ **测试必须隔离写端**（读端和写端**都**要换）—— 只 patch 读端会写坏真文件
  （[[test-harness-must-isolate-writes]]）
- ⚠️ **`~/.classlive/` 与 `glossary/` 在 `.gitignore` 里** → `git tag` 对它们是**空的**，
  动前先 `cp -a`（[[git-tag-backup-excludes-ignored-data]]）
- **不擅自改系统设置**（日历授权 / 静音 / 音量 / 通知 / 显示）：只报告 + 询问
- **推送 / 发版要作者点头**；更新日志文案**先给作者确认**；**推送前先给作者看效果**
- **绝不代跑 `git config`**；**`git add -A` 不用**（按文件名加具体文件）
- **改代码前先加载相关 skill**（硬规矩）；**文档别打开，直接说**
- ⚠️ zsh：`$VAR` 后紧跟非 ASCII 会被吞；**zsh 不做词分割**（`for x in $IDS` 要写 `${=IDS}`）
- ⚠️ **两份「还没修」清单会腐坏**（`docs/REVIEW-midpoint-20260927.md §9.2` 与
  `docs/PLAN-entry-panel.md §8.1`）—— 是线索不是事实，动手前 grep

---

## 15. 上一份交接里仍然活着的账

> ⚠️ **2026-09-30 全部现查过一遍**（不是转述）。**13 条里 10 条真开着** ——
> 下面「现状」那列是**当时**的读数，会腐坏；动手前 grep。

| # | 项 | 现状（2026-09-30 现查） |
|---|---|---|
| 1 | **文档口径同步**（`README.md` + `doctor.py` 文件头 + `update.py`） | 🔨 **改掉了** —— `README.md:273` 与 `update.py:11` 原来写「**绝不自动下模型**（只打印命令）」，**是假的**（`run_step("models")` 真下必下模型）。已改成「不替用户决定**可选**的大模型 / **必下**的顺手补齐」。⭐ 顺带补了一个**真缺陷**：后台自动更新**只警告依赖变化、不警告模型变化** → 现已加 `models_changed`（= `pull()` 里 `git diff --name-only <before>..<after>` 看 `models.py` 动没动，**保守代理**；判据在 `tests/test_update.py` B12，**三处变异验证过**）。⭐ `doctor.py` 文件头那处**也查了 —— 它是准的**：原文说的是「**这个脚本**只读、只打印」（`doctor` 自己确实不下），不是「整个工具不下」；句子主语就是那个脚本，**不动它** |
| 2 | **端到端「什么都没装」** | ⬜ 还开着。⚠️ 而且**现在做不了**：`models.py:78 MODELS_ROOT = "~/models"` **写死**，没有环境变量能指到空目录（`CLASSLIVE_MODELS` 零命中）→ 要测**得先加覆盖点** |
| 3 | **`measure_text_h` 有两份** | ✅ **收盘（2026-09-30）**。两份**仍在**（`overlay.py:256` / `wrapup.py:53`）—— ⭐ **刻意不合并**：`wrapup` 那份的 docstring 自己写了理由（overlay 那份在**动画路径**上被每帧调用，搬动收益不抵风险），**那条理由成立**。⚠️ 但「行为必须保持一致」此前**只写在注释里、没有任何东西在保证它** → 加了一条**行为**判据（`tests/test_wrapup.py`，同输入必须同输出），**两处真变异全红** |
| 4 | **`entry_panel.py` 太大** | ⬜ 还开着，**而且涨了**：现查 **3483 行**（上面这行原来记的是「1900+」） |
| 5 | **3.8.0 没发** | ⬜ 还开着。⚠️ **不是「文案在等确认」** —— `CHANGELOG.md:68` 的 `## [3.8.0]` **已经有了**，但 7 条看点里**这一整轮「实时总结」一个字都没有** → **要重写**。`VERSION` 仍 `3.7.0`，最新正式 tag `v3.7.0` |
| 6 | **约 165 条未处理的 OCR 发现** | ⭐ **2026-09-30 分诊过了，结论比「165 条待办」轻得多**。台账：发现 **268** / 实修 91 / 判定不改 ~10。**分桶**：已删的 `checkpoints/` 分块副本 **80**（出局）· 一次性探针 53 · 测试副本 27 · **生产代码 108**。66 个生产代码的 high/critical **逐条回原码核实**：**13 条早已修**（09-28/09-29 那两轮）· **2 条核实为假**（`update.py` 缺 `env=` 那条机制不成立；`ready.ready_item_text` 那个名字根本不存在）· **1 条作者判过不改**（`paths.course` 路径穿越，单用户本地工具）· ⭐ **真还成立的只有 3 条，已修**（见下）。⚠️ **剩下的 medium/low 多为 C 档内部质量**，OCR 的行号已多处漂移，顺手再说 |
| 6a | ⭐ **`extract.py` 嵌套表格重复计数** | ✅ **已修**。`tbl.iter("a:tr")` / `tr.iter("a:tc")` 都是**递归**的 → 嵌套表格的内层单元格被数两遍，而表格块的去向是**术语候选池** → 会污染它。改 `findall`（直接子节点）；⚠️ 内容不会丢（内层段落仍在外层单元格的 `_paragraphs(tc)` 里）。判据 `test_extract.py` §3b，**变异验证过** |
| 6b | ⭐ **`capture.py._close_stream` 占住麦克风** | ✅ **已修**。`stop(); close()` 挤在同一个 `try` → `stop()` 一抛就跳过 `close()`，而 `close()` 才是**把设备让出去**的那步。拆成两个 `try`。判据 `test_audit_regressions.py` R20，**变异验证过** |
| 6c | ⚠️ **`panel.py.make_label` 的 `wrap` 参数是死的** | ✅ **已删**。`wrap=True` 设的 `setWraps_(True)` 本来就是默认值，而**全仓零调用点**（`wrapup.py`/`whatsnew.py` 里那些 `wrap=` 是它们各自的局部 helper）。⚠️ **那条 OCR 说它是"静默吞第二行"的 bug，核实为「不是」**：docstring 早已写明这是已知行为，判据里也拍过板（「标题/准备度/头部本来就该走默认」）。删参数是**零行为变化**的清理 |
| 7 | **`corrections.json` 不存在** | ⬜ 还开着。文件确实不存在；`courses.py:313` 写 / `:327` 读，调用方**只有测试** → **生产零调用**，那条路从没真跑过 |
| 8 | **`PROBE_ZERO` 没写进 docstring** | ✅ **已补**（`probe_entry_panel.py` 文件头的环境变量清单里，含「**在 3 秒后**才动手，别急着关」那条） |
| 9 | **`classify.py --sessions` 没写文档** | ✅ **已补** —— ⚠️ 查下来**不只是 `--sessions`**：`classify.py` **整个模块**都不在文档里。进了 `CLAUDE.md` 文件地图（写清「命令行**只看不动**」+ `--limit`/`--no-model`/`--sessions`）。⚠️ 另发现 `docs/DESIGN.md` 那张「文件」表**整体停更**（48 个模块只列了 17 个）—— **那是另一笔账，没动** |
| 10 | **两门课在降级** | ⚠️ **半开，而且换了门**。2026-09-30 **现跑 `corpus.keywords`**：`ECON10740` **6 节 → 不再降级** ✅；`ECON10790` 8 个文件但只 **2 节够格 → 仍降级** ⛔。⚠️ 另外两门也在降级但**是预期的**：`ECON10070`（1 节，刚建）· `测试`（2 节 —— ⭐ 作者 2026-09-30 说明：**那是他自己跑 `cl test` 两次留下的**，不是垃圾） |
| 11 | ⭐ **`ECON10730` 的污染**（§12.1） | ✅ **已收盘** —— `courses.py:502 NO_COURSE` + 守门 `:557/:590`；`sessions/.attribution.json` 里写着 `"2026-09-11_140757_10730": "(不属于任何课)"` |
| 12 | ⭐ **模型名统一**（D2/D3） | ✅ **已收盘** —— `atom.py:53` / `classify.py:58` 都是 `deepseek-flash`；`chapter.py:47` 是 `MODEL = atom.MODEL`；探针改成 `from atom import … MODEL` |
| 13 | ⭐ **`ANSWER_MAX_LINES` 过期注释**（§12.4） | ✅ **已修**。那句 docstring 改成准确的写法（判的是**高度不超 `ANSWER_TEXT_H = 45.0`**，不是「几行」—— 那个常量**全仓不存在**）。⚠️ 现在 `grep -n ANSWER_MAX_LINES overlay.py` **还会命中 1 处**，那是**解释这次修正的那段注释本身**，不是漏改 |

⚠️ **顺带一条（超出这 13 条）**：`ECON0070 TUT` 自己成了一门独立课（1 节 → 降级），
而当初为它加的规则是「`ECON10070 TUT` → 剥离课型后缀」—— **课号不一样**
（`ECON0070` vs `ECON10070`）。

✅ **2026-09-30 作者确认那是他打漏了一个 `1`**，已修：
`glossary/ECON0070 TUT.txt` → **`glossary/ECON10070.txt`**（首行同改），
那节真课（`2026-09-29_123046`）走既有旁路判给 `ECON10070`（**只写 `.attribution.json`，
一个 `.md` 都没动**）→ `ECON10070` 现在 **1 节**。
⚠️ **`sessions/` 里还有 3 个 7 行的**（`011955` / `020026` / `110641`，内容是
"Maharatbha" / "I'm so excited." / "I'm not sure what I'm saying."）—— 看着是**误启动**。
按规矩**没动它们**（`sessions/` 只读）；要标成「不属于任何课」得作者点头。
