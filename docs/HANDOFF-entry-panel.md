# 交接：下一轮从哪开始（第二批「课程卡片面板」收尾）

> 写这份时主会话上下文快用尽。**这份就是为了让下一轮干净开局** —— 先读它，
> 再读它指出去的文档。
> 日期 2026-09-26 · 第二批起点 `ab57f30` · 收尾 `04e02e8`

---

## 0. 一句话状态

**第二批六步全做完了、本批提交都在本地 `main` 上、所有闸门绿 —— 但一次都没推。**
（**故意不写提交数** —— 它每落一个文档提交就过期一次，今晚连中三次。
要数就数 `git rev-list --count ab57f30..HEAD`，清单见 §1。）
剩下三件事：**① `§8` 那批审查遗留（头一条是删词不持锁）② 发版 ③ 批量分类（另开一批）**。

---

## 1. 本轮已完成（`ab57f30..04e02e8`，共 14 个提交）

⚠️ 表**只记到本批收尾 `04e02e8`**；它之后的文档纠错提交不列（`git log --oneline 04e02e8..HEAD` 看得到）。

**一行 = 一个提交**（按提交**先后**排，最早的在最上；起点 `ab57f30` 是方案提交，不在表里）。

| 步 | 提交 | 做了什么 |
|---|---|---|
| 0+2 | `4717569` | **拖拽验通**：非 key 浮动面板收得到拖拽（作者手拖）、`draggingEntered` 里读 URL 不毁拖放 · `courses.py`（课程清单/解析/准备度）+ **修掉 `cl course 107` 静默选中 ECON10770** |
| 1+3+4 | `d59b147` | `panel.py` 三个助手（滚动/标签/落点）· `entry_panel.py` 面板本体 · `prep.remove_terms()`（换窄不变量） |
| — | `83886f2` | `restore_lines()`：**按原位置**撤销（不是 append） |
| 5 | `c602fff` | 菜单栏 🎧「开课前的准备…」 |
| 3b | `4e3c751` | 卡片里的结果列表 + 逐条删/撤销 |
| — | `1f6ee11` | 修「关闭无反应」（target 被 GC） |
| — | `70c0d46` | 验收跑器 `probe_entry_panel.py`（隔离模式） |
| — | `2851601` | 修「删到最后一个卡住」（在按钮 action 里拆了自己的视图树） |
| — | `462f16c` | 修**生产路径 AttributeError** + 归档静默覆盖 + 布局第二份定义 |
| — | `2dfe30a` | 把审查遗留落进方案 `§8`（含那条新发现的锁缺失） |
| — | `80e8abd` | 修缓存键 + **每开/关漏一个面板** |
| 6 | `e79c3c7` | 零参数改成「先开面板，点开始上课才录课」 |
| — | `031c14d` | 收尾交接文档 + 旧交接 `§8` 加指针 |
| — | `04e02e8` | 计划顶部加指针（本批已做完，新会话先读交接） |

### 新增/大改的文件

| 文件 | 是什么 |
|---|---|
| `courses.py`（新） | 课程清单（`glossary/` ∪ `~/.classlive/courses/` 并集）· 片段解析 · 每课「准备度」· 给 `cl` 的 CLI |
| `entry_panel.py`（新） | 面板本体。卡片列表 / 卡片即落点 / 进度 / 结果列表 / 删+撤销 |
| `entry_launch.py`（新） | 零参数入口。**独立短进程**，只写 `.course`，退出码 0/1/2/3 是它与 `cl` 的契约 |
| `panel.py`（改） | 加了 `make_scroll_view` / `make_drop_target` / `make_label` |
| `prep.py`（改） | 加了 `remove_terms` / `restore_lines` / `course_glossary_path`（公开） |
| `paths.py`（改） | `course_dir` / `materials_dir` / `prep_state` 各加 `root=` |
| `cl`（改） | `course` 分支改调 `courses.py`；零参数接上 `entry_launch.py` |
| `probe_drag.py` / `probe_entry_panel.py`（新） | 两个验收探针（都不在运行路径上） |

### 闸门（都在 `CLAUDE.md` 的「完成判据」里有条目）

```
test_panel.py 43 · test_entry_panel.py 43 · test_prep.py 95
test_courses.py 46 · test_extract.py 24 · test_instance_lock.py 22 · test_audit_regressions OK
```

---

## 2. ⚠️ 下一轮第一件事：`docs/PLAN-entry-panel.md` §8 那批审查遗留

**两份审查（两个独立代理 + 一轮 `ocr`）的结论，剔掉已修的，还剩一批。**

### ✅ `remove_terms` / `restore_lines` **不持锁** —— **2026-09-27 已修**（提交 `d935d5a`，已推送）

⚠️ **这一段原来写的是"最要紧、还没修"，2026-09-28 核实时发现它已经做完了** ——
留在这里当"别再照抄本文档"的例子。现状（都已一手核过）：

- `prep.state_lock_path()` 是**锁文件名的唯一定义点**（`prep.py:280`，docstring 明写
  「别在调用点再拼一次 `with_name(name + ".lock")`」）
- `prep._course_write_lock(lock_path)` 围住「读-改-写」那一段；两个写入器
  `remove_terms` / `restore_lines` 都收 `lock_path=None`，且真正的实现拆到了
  `_remove_terms` / `_restore_lines`（**无锁版**），取锁纪律集中在 docstring 里
- 面板两个调用点**都真传了**：`entry_panel.py:703`（删）/ `:731`（撤销），
  值由 `lock_of(course)` 算出 → `prep.state_lock_path(paths.prep_state(...))`
- 「正被另一个写入器占用」走 `GlossaryError` + `LOCKED_MSG`（人话，不叠类型名）
- `tests/test_prep.py` 有争用判据（两个写入器各一条）+ `state_lock_path` 与
  **课号模糊解析**（`ECON10740` / `10740` 指向同一个锁文件）的一致性

→ **本节其余条目仍以 `docs/PLAN-entry-panel.md §8.1` 为唯一定义点**（那份也在维护）。
⚠️ **但它同样会腐坏**：L 行早已过期（上面已改）；"死代码"那 4 条里 3 条也已不存在。
**用它之前先逐条核代码。**

### ✅ 2026-09-28 一批修掉的（`78560cb` 结果面 + 同批的拖拽提交）

结果列表（失败逐文件 + 原因上屏 · `not_added` 逐条 · `abort` 说人话）·
`dragging_updated` 改**回放 entered 的决定** · 悬停判据改成 `extract.is_supported`
（`.docx` / 目录同样被拒）· `on_drop` 返回 `None` 与"回调缺失"分开 ·
stage 表改调 `prep.STAGE_NAME` · `do_delete` 早拒 · 拖拽**第一次**有自动化判据（第 ⑨ 组）。
⚠️ **仍开着**：`probe_entry_panel.py` 自检是死代码 · `not_added` 的**可下载清单** ·
「撤销」行在视口外不滚动（`§8.1 #7`）· `_open_prep` 默认静默（`#14`）。

---

## 3. ⚠️ 下一轮要知道的三条「坑」

### 3.1 隔离跑器会把**生产路径整个绕过**

面板那个 AttributeError（参数名 `paths` 遮蔽模块 `paths`）**只在 `state_root=None` 那条路上**
触发 —— 而验收跑器一直传 `state_root=ISO`，**那条路从来没被跑到过**。
→ **两种都得跑**：隔离的（验写端）+ 生产参数的（验代码路径）。现在 `test_panel.py` 两种都有。

### 3.2 判据要在**事情发生的那一刻**成立

「真 glossary 未变」那条检查原本只在末尾判，而那时「撤销」已经把文件放回去了
→ 变异把删除打向真文件时它**报绿**。→ 删完**立刻**判。

### 3.3 一批「看着成立、其实没区分能力」的假测试（本轮写错了十条）

共同形状：**判据看着对，但没有区分能力**。举三个：
`object.__setattr__` 测 frozen（那本来就是绕过它的姿势）· 夹具里放多个等价来源
**把断言遮住** · 只测「全都不存在」那支 → **走提前返回**，测不到最后那条 return。

⚠️ 抓它们只有一招：**变异测试**（改一处实现，看断言会不会红）。
⚠️ 而跑变异测试前**必须 `rm __pycache__/*.pyc`** —— 同尺寸变异的 mtime 会和 `cp` 还原
撞在同一秒，CPython 判定缓存有效：**`cmp` 说源码逐字还原，跑的却是变异版**（被骗过三轮）。

### 3.4 ⚠️ 这个仓库的 AppKit 头号坑：`objc_own` 按 key 缓存类

**回调写进 `own()` 的 namespace 就是错的** —— 第二次调用拿回同一个类，
于是**点哪张卡都触发第一张的动作**。本轮踩了两次（按钮、落点）。
正确写法：**回调挂实例**（`v._cb = fn` + `getattr(self, "_cb")`），
并且**调用方必须留住 target**（`setTarget_` 是**弱引用** —— 不留住就是「点了没反应还不报错」）。

---

## 4. 下一轮的第二步：发版

**第二批做完了，但一次都没推。** 作者一直压着没发。⚠️ 发版规矩（`CLAUDE.md`）：

- **默认 `manual`**（不写 `### 更新方式 auto`）；只有作者明说才 `auto`
- **更新日志文案必须先打开给作者确认再发版**
- 流程：写 CHANGELOG → 给作者看 → bump `VERSION` → commit → `git tag` → push main+tag
  → `gh release create` → 弹卡片给他看效果
- ⚠️ **推了就会到朋友手上**（`cl update` = `git pull`）
- ⚠️ 第二批有**行为变化**（零参数现在先开面板），CHANGELOG 里要说清
- ⚠️ `VERSION` 现在还是 **3.7.0**

---

## 5. 下一轮的第三步（另开一批）：批量分类

**方案在 `docs/PLAN-entry-panel.md` §7，尚未实现。** 作者认可的形状：

```
把一堆文件拖到面板空白处
  → ① 本地：文件名 × (课号 + 英文课名单词) → 唯一最优才算命中
  → ② 分不出的：抽一小段正文 + Jev `choice` → **一份文件一个问题**
  → ③ confidence < 门槛 → 不猜，进「未分类」
  → ④ ⭐ **把映射摆给作者看 → 点头才写**
```

**三条硬事实（都已核实，别重新查）：**

- ⚠️ **Jev 不是聊天模型** —— TypeSafe 的 System One，**不生成文本、只答类型化问题返概率**。
  端点 `POST https://api.commandcode.ai/provider/v1/systemone`，**不是 `/chat/completions`**
  （仓库旧结论对 Jev **不适用**）。体是 `{model, state, questions}`。
  ⚠️ **免费额度已于 2026-09-24 结束**（现 $0.042/M，几十份文件名 ≈ $0.0001）。
  ⚠️ 官方明说 **CJK 明显更差** → 判据**只用英文那半**。
  ⚠️ 带 `x-cmd-zdr: 1` 会被 **422 拒**（`typesafe/jev` 没有 ZDR 上游）。
- ⭐ **我实测纠过自己一句错话**：我说「4/5 个文件名不用问模型」是拿**上下文里的 5 个样本**说的。
  真扫 `~/Downloads` 468 份 → **本地只能解决 7/468 = 1%**。根因：四门 ECON 课的课名
  几乎是同一句话（`econ` 四门共有）→ 打平 → 拒绝。**分母一换结论就翻。**
- ⭐ **「先看映射再落盘」是行业标准**（DEVONthink 给带分数的建议列表 / CSV 导入那一族
  「confirm field mappings」/ 共识原话「inspect, fix, validate, and only then commit」）。
  ⚠️ 但**「N 个文件 → N 门课的映射表」这个形态没有现成可抄**。

⚠️ **作者贴过两个 API key 明文进对话**（一个 `apikey_…`、一个 `user_…`，形状与官方文档的
`<CMD_API_KEY>` **对不上**）。逐字记录已落盘 → **用前先轮换**，连同那笔「5 个泄露 key 待轮换」的账。

---

## 6. 还挂着的旧事（与第二批无关，别忘了）

| | |
|---|---|
| **Brightspace 收材料** | 等朋友那边验证 |
| **`probe_scroll.py` 本身不稳定** | 改动前连跑三次；翻转 = 探针的问题，稳定挂同一阶段才是真回归 |
| **`test_panel.py` 盖不到行为** | 拖拽 / live resize 至今没有自动化覆盖（静态摊平 + 那几个真点击测试之外） |
| **PDFKit 跨线程** | 在真 `NSApplication` 里的安全性没测过 |
| **Tailscale / 轮换 5 个泄露 key** | 见 `~/.claude` 记忆 |

---

## 7. 下一轮的第一步（照做即可）

1. **读这份 + `docs/PLAN-entry-panel.md` §8**（那份是审查遗留的唯一定义点）
2. **跑一遍全部闸门**，确认干净开局：
   ```bash
   cd ~/lecture-live
   for t in test_audit_regressions test_panel test_courses test_entry_panel \
            test_prep test_extract test_instance_lock; do
     rm -f __pycache__/*.pyc
     echo "--- $t"
     PYTHONDONTWRITEBYTECODE=1 ClassLive.app/Contents/MacOS/python tests/$t.py | tail -2
   done
   ```
3. **`git status --porcelain` 应当是空的**；再核一遍所有 SHA 引用都还有效：
   ```bash
   grep -ohE '`[0-9a-f]{6,40}`' docs/HANDOFF-entry-panel.md docs/PLAN-entry-panel.md \
     | tr -d '`' | sort -u | while read s; do git cat-file -t "$s" >/dev/null 2>&1 || echo "❌ $s"; done
   ```
   ⚠️ **下界必须是 `{6,` 不是 `{7,`** —— 本轮那个坏引用是把下面这个 SHA **抄漏了一位**
   （`80e8abd` 少个字符 → 只剩 6 位），`{7,40}` 的检查**看不见它**
   （我第一版就是这么写的，变异测试当场没抓到）。**少一位正是这类错最可能的形态。**
   ⚠️ 顺带：别在本文档里把坏 SHA 写字面量 —— 这条命令会**把自己写的例子抓出来**。
   ⚠️ **别校验「HEAD == 某个 SHA」** —— 收尾提交之后还会有文档提交，写死当场就不成立
   （本书面化过一次：写死 `e79c3c7`，提交完自己就过期了）。要的是**引用有效**，不是 HEAD 等于谁。
4. **动手第一件：§8 那条删词锁**（先读 `instance_lock.py` 的文件头）

⚠️ **别碰 `glossary/<课号>.txt` 与 `~/.classlive/`** 除非在隔离目录里 ——
本轮我的变异测试**差点删掉真实的 `glossary/ECON10740.txt` 一行**
（删了又被「撤销」逐字放回，sha 一致；但那是运气）。

⚠️ **`pre-entry-panel-20260926` 那个 tag 不是这些文件的备份** ——
`glossary/` / `.course` / `~/.classlive/` 都在 `.gitignore` 里，`git show <tag>:glossary/x.txt`
返回的是**空**。要动它们之前**单独 `cp -a` 一份到隔离目录**（唯一有风险的数据，
恰好是 tag 碰不到的那部分）。
