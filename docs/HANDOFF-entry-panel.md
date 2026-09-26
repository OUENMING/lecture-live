# 交接：下一轮从哪开始（第二批「课程卡片面板」收尾）

> 写这份时主会话上下文快用尽。**这份就是为了让下一轮干净开局** —— 先读它，
> 再读它指出去的文档。
> 日期 2026-09-26 · 第二批起点 `ab57f30` · 收尾 `e79c3c7`

---

## 0. 一句话状态

**第二批六步全做完了、十二个提交都在本地 `main` 上、所有闸门绿 —— 但一次都没推。**
剩下三件事：**① `§8` 那批审查遗留（头一条是删词不持锁）② 发版 ③ 批量分类（另开一批）**。

---

## 1. 本轮已完成（十二个提交，`ab57f30..e79c3c7`）

| 步 | 提交 | 做了什么 |
|---|---|---|
| 0 | `4717569` | **拖拽验通**：非 key 浮动面板收得到拖拽（作者手拖）、`draggingEntered` 里读 URL 不毁拖放 |
| 2 | `4717569` | `courses.py`（课程清单/解析/准备度）+ **修掉 `cl course 107` 静默选中 ECON10770** |
| 1+3 | `d59b147` | `panel.py` 三个助手（滚动/标签/落点）· `entry_panel.py` 面板本体 |
| 4 | `d59b147` | `prep.remove_terms()`（换窄不变量） |
| — | `83886f2` | `restore_lines()`：**按原位置**撤销（不是 append） |
| 5 | `c602fff` | 菜单栏 🎧「开课前的准备…」 |
| 3b | `4e3c751` | 卡片里的结果列表 + 逐条删/撤销 |
| — | `1f6ee11` | 修「关闭无反应」（target 被 GC） |
| — | `70c0d46` | 验收跑器 `probe_entry_panel.py`（隔离模式） |
| — | `2851601` | 修「删到最后一个卡住」（在按钮 action 里拆了自己的视图树） |
| — | `462f16c` | 修**生产路径 AttributeError** + 归档静默覆盖 + 布局第二份定义 |
| — | `80e8abd` | 修缓存键 + **每开/关漏一个面板** |
| 6 | `e79c3c7` | 零参数改成「先开面板，点开始上课才录课」 |

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

**两个独立代理 + 一轮 `ocr` 的结论，剔掉已修的，还剩一批。头一条是新的、最要紧的：**

### ⭐⭐ `remove_terms` / `restore_lines` **不持锁**

`prep.prepare` 对**同一个文件**持本课专属锁（`prep.py:830-841` 取、`:1045` 释放），
而**我新加的删词/撤销一个锁都没上**。→ **两个写入器对同一文件读-改-写 → 后写的吃掉先写的。**

`prep.py:828` 自己就写着：「`append_terms` 是读-改-写，两个 `cl prep` 同时跑会互相吃掉
对方的追加」—— 它**为这件事上了锁**，新加的删词没上。

可达路径：prep 正在跑（或终端里 `cl prep`）时点面板上的「删」。

**修法**：给两个写入器加 `lock_path=None` 参数，面板调用点传
`state_file.with_name(state_file.name + ".lock")` —— 与 `prepare` 同一把锁。

⚠️ **这是并发改动，本轮我特意没写**（宁可不写也不写错）。改之前先读
`instance_lock.py` 的文件头与 `prep.py` 那段取锁/释放的形状。

完整的剩余清单（每条带位置与修法）在 `docs/PLAN-entry-panel.md` §8.1。

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
3. **`git status --porcelain` 应当是空的**；`git log --oneline -1` 应当是 `e79c3c7`
4. **动手第一件：§8 那条删词锁**（先读 `instance_lock.py` 的文件头）

⚠️ **别碰 `glossary/<课号>.txt` 与 `~/.classlive/`** 除非在隔离目录里 ——
本轮我的变异测试**差点删掉真实的 `glossary/ECON10740.txt` 一行**
（删了又被「撤销」逐字放回，sha 一致；但那是运气）。
