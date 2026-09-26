# P1 计划：脱离终端（`.app` 启动器 + 引导式更新）

> 目标一句话：**双击就能上课，更新也不用碰终端。**
> 作者 2026-09-26：「我不想再从终端启动了。」
>
> 这份是 P1 的完整计划。**每条"已验证"都是本机实测，每条引文都回源核对过**；
> 核不实的已在 §7 列出并标注。

---

## 0. 验收判据（做完要能逐条打勾）

**硬功能：**

- [ ] **双击** `ClassLive.app` → 悬浮窗出现、开始转录，**全程没碰终端**
- [ ] 连点两次**只起一个**实例（不叠两个悬浮窗、不双倍 API 花费）
- [ ] 更新卡片上的「立即更新」能**一路做完**：拉代码 → 补依赖 → 报模型 → 提示重启
- [ ] `cl`（终端路径）**照旧能用**，两条路不打架

**⭐ 面向"不懂 TCC / bundle / flock 是什么"的朋友 —— 所有状态都要说人话**
（§3.8 的原则：**不只是"程序完全起不来"才算失败**）

- [ ] 首次双击弹的麦克风授权框，**写的是「ClassLive」+ 我们的说明文字**（§1.5）
- [ ] 系统框之前**有一层自己的预热说明**（只在首次出现，不是每次启动都弹 —— §3.7）
- [ ] **权限被拒之后有恢复路径**：弹人话说明 + 一个按钮**直达系统设置的麦克风页**（§3.7）
- [ ] **缺 BlackHole 时看得见**：不是静默降级、也不是把提示扔进 stdout（`capture.py:52` 已有好文案，缺的是送达——§3.8）
- [ ] 缺模型 / 缺依赖时同样**弹出来**，不是只打印
- [ ] 启动失败时弹 `NSAlert`，不是静默什么都不发生（§3.5）

---

## 1. ⭐ 麦克风权限：**实测结论**（本节原是"待验"，已完成）

**结论：权限落在「Python 解释器的绝对路径」上 —— 不是 bundle ID，也不是 `bash`。**

两次独立复现（分别用 `/opt/homebrew` 的 python 3.12 和 3.13，
后者在 TCC 里 0 条记录、从 Finder 双击、干净触发）：

```
新增 TCC 条目 = /opt/homebrew/Cellar/python@3.13/3.13.14/…/bin/python3.13   | allowed
而 app 的 bundle ID 是 page.bldcam.test.micprobe313                          ← 完全没用到
```

### 三个后果

**① 授权框上写的不是 ClassLive。**
用户看到的是一个跟 `python3.x` 有关的授权请求，不是「ClassLive 想要访问麦克风」。
**朋友第一次双击时会困惑** —— 必须提前告诉他。

**② 我们写的 `NSMicrophoneUsageDescription` 显示不出来。**
实测：`mainBundle()` 指向的是 **Python 自己的 bundle**（见 §2 #7），
而查了它的 `Info.plist` ——**里面根本没有 `NSMicrophoneUsageDescription` 这个键**：

```
CFBundleIdentifier  => org.python.python
CFBundleName        => Python
（无 NSMicrophoneUsageDescription）
```

→ **TCC 拿到的是「没有说明文字」，框里只会显示一句通用提示。**

**③ ⚠️ Homebrew 每次升级 Python 都会**重新弹框。**
Cellar 路径**带精确补丁号**：

```
/opt/homebrew/Cellar/python@3.12/3.12.13_4/…   ← 3.12.13_4 变了 → 路径变了 → 条目失效
```

而 `.venv/bin/python` 是 symlink，**实测 TCC 认的是 realpath** ——
所以 `brew upgrade python@3.12` 之后，用户会**再被问一次**。

> 对照：系统自带的 `/usr/bin/python3` 路径是
> `/Library/Developer/CommandLineTools/…/Versions/3.9/…` —— **不带补丁号**，更稳。
> 但它是 x86_64 build，装 arm64 wheel 会 `incompatible architecture`（实测），
> **不能当 venv 的基底**。

### 设计应对（要写进 P1 交付物）

**有两条路，二选一 —— 见下面的「能不能改成我们想要的」。**

## 1.5 ⭐⭐ 能不能让授权框写「ClassLive」而不是「python3.11」？—— **能，已实测**

作者问：「能不能改成我们想要的内容呢？」**答案是可以，而且配方已经验证过了。**

### 机制（现在是截图级的证据）

| python 装在哪 | `mainBundle()` | 授权框上写什么 |
|---|---|---|
| `.venv/bin/`（在 `.app` **外面**） | Python 自己的 bundle | ❌ **`"python3.11" would like to access the microphone.`**（无说明文字） |
| **`.app/Contents/MacOS/`** | **我们的 bundle** | ✅ **`"ClassLive 测试.app" would like to access the microphone.`**<br>✅ **`ClassLive 需要麦克风来实时转录课堂内容`** |

**两次都是真截图**，不是推断。**根因**：macOS 认 bundle 的方式是看可执行文件**是不是在 `Contents/MacOS/` 里**。
Homebrew 的 python 是 framework 构建，真实可执行文件在 `Python.framework/…/Python.app` 里，
所以怎么包都改不了；**换成 uv 那种独立 python，把可执行文件放进 `Contents/MacOS/`，就变成我们的了。**

### ✅ 配方（方案 I，已实现成 `make-app.sh`，每个数字都是实测的）

```
1. uv python install 3.12 --managed-python    ← 必须是 uv **管理的**独立构建
2. uv venv <临时>/venv --python <那个 python>  ← 建**空** venv
3. 搬布局（不是拷 python 分布）：
     venv/bin        → ClassLive.app/Contents/MacOS
     venv/lib        → ClassLive.app/Contents/lib
     venv/pyvenv.cfg → ClassLive.app/Contents/pyvenv.cfg
4. ⚠️ 把 MacOS/python* 的**符号链接换成真拷贝**（见下，不做这步双击没反应）
5. 写 Contents/Info.plist
6. uv pip install -r requirements.txt        ← **搬完再装**，shebang 天然正确
7. 写 sitecustomize.py（双击的入口，见 §3.1）
```

**⭐ 第 3 步是"搬布局"不是"拷 python 分布" —— 这个区别是整件事的关键：**

搬完之后它**仍然是一个真 venv**（`pyvenv.cfg` 在 `Contents/`），所以：

- ✅ `uv pip install` **照常能用**（实测）
- ✅ **不需要**删 `EXTERNALLY-MANAGED`、**不需要**手工拷包

**⚠️ 第 4 步不做 = 双击完全没反应**（2026-09-26 实测踩到，排查了一轮）：

venv 的 `bin/python` 天生是**指向基础解释器的符号链接**，而基础解释器在 bundle 外面。
**macOS 的 LaunchServices 不会启动一个符号链接的 `CFBundleExecutable`** ——
退出码 0、没报错、没弹窗、连 python 都不起来。**必须换成真拷贝。**

能直接拷的理由：uv 管理的独立 python 是**真 Mach-O 二进制**，`otool -L` 显示它
**只链接系统库**（CoreFoundation / libSystem / ncurses / panel / SystemConfiguration），
**没有相对路径的 libpython** —— 拷到别处照样跑，stdlib 靠 `pyvenv.cfg` 的 `home` 找回去。

**三个关键点：**

1. **`CFBundleExecutable` 必须指向 `MacOS/` 里真实存在的那个 python** —— 这一条是开关
2. **`Contents/` 下三样东西必须齐**：`MacOS/`(bin)、`lib/`、`pyvenv.cfg`
   —— 少一样 `sys.prefix` 就算错，venv 的包装不上（实测）
3. **不能用「软链接指向外面的 venv」代替搬布局** —— 实测：
   `MacOS/python -> ../Resources/venv/bin/python` 会让 Python 顺链解析、
   `sys.prefix` 落回**基础 python**，venv 的 site-packages 用不上

**体积**：实测 **419 MB**（含 pyobjc + sounddevice + numpy + httpx + mlx-lm）。对比 `.venv` 381 MB，同一量级。

### 这条路顺带解决的另外几件事

- ✅ `LSUIElement` / `CFBundleIdentifier` 终于生效 → **Dock 图标不再闪**（见 §2 #9）
- ✅ **不再依赖用户的 Homebrew python** → 「Homebrew 升级 → 重新弹框」这个问题**消失**（§1 后果③）
- ✅ 授权框写得清清楚楚

### ⚠️ 代价（必须认账）

- `.app` 变成 ~100 MB+，且**它是构建产物、不进 git**（和现在的 `.app` 一样）
- **`.venv` 的去向要重新想**：如果 `.app` 里自带 python，那终端路径 `cl` 用的 `.venv`
  和 `.app` 用的是**两套环境**，会漂移 —— 要么让 `cl` 也用 `.app` 里那个 python，
  要么保留两套并接受不一致。**这是个真正的架构决定，不是细节。**
- `make-app.sh` 复杂度上升（要下载/拷贝 python 分布）

### 三个方案摆在一起 —— ⭐ **已定：选 I**

| | **A. 壳式**（原计划） | ~~B. 拷 python 分布~~ | ⭐ **I. venv 搬进 `.app`** |
|---|---|---|---|
| 授权框 | ❌ 问「python3.12」 | ✅ 问 ClassLive | ✅ **问 ClassLive** |
| **真 venv / uv 能装包** | ✅ | ❌ 手工拷包 + 删 `EXTERNALLY-MANAGED` | ✅ **实测能装** |
| 体积 | 几 KB | ~100 MB | **56 MB**（实测） |
| 依赖谁的 python | Homebrew（**升级会重弹授权**） | 无 | uv 管理的（**不会自动升级**） |
| Dock 图标闪一下 | ⚠️ 会 | ✅ 不会 | ✅ 不会 |
| `.venv` 架构 | 不动 | 要重新设计 | **`.app` 就是安装目录** |

**B 被 I 完全支配，划掉。真正要选的是 A vs I —— 作者 2026-09-26 定了 I。**

### I 的实测结果（全过，不是推断）

```
sys.executable = .../ClassLive.app/Contents/MacOS/python
sys.prefix     = .../ClassLive.app/Contents
mainBundle     = .../ClassLive.app           ← 我们的
CFBundleName   = ClassLive                   ← 我们的
venv 的包      = ✅ 可用（真 venv）
uv pip install = ✅ 认这个 venv
移动 .app      = ✅ 还能跑（sys.prefix / mainBundle 都跟着变）
改名 .app      = ✅ 还能跑
```

**原本最担心的「venv 不能搬」风险 —— 实测不存在。**

### ⭐⭐ 附带发现：I 的 TCC 条目落在 **bundle ID** 上（不是 python 路径）

查 TCC 表，那个测试 app 留下的条目是：

```
page.bldcam.test.macospy  |  allowed      ← 它的 CFBundleIdentifier
```

对照 A（壳式）留下的：

```
/opt/homebrew/Cellar/python@3.12/3.12.13_4/…/bin/python3.12  |  allowed   ← 一条带补丁号的绝对路径
```

| | 条目锚在 | 后果 |
|---|---|---|
| **A 壳式** | python 的**绝对路径**（含补丁号） | ⚠️ Homebrew 一升级 → 路径变 → **重新弹框** |
| **I** | **bundle ID** | ✅ **跟 python 版本无关，永久稳定** |

**→ 方案 I 把「升级要重弹」这个风险直接消灭了**，不需要额外加固。

### 先例（调研得到，非我实测）

- **py2app** 确实在 `Contents/MacOS/` 放一个叫 `python` 的东西
  （changelog 0.9 记着 issue #146/#147：*"The 'python' binary in `MyApp.app/Contents/MacOS` was the small stub executable"*）
  —— 与方案 I 同构
- [`glyph/venvdotapp`](https://github.com/glyph/venvdotapp)：*"Make a Python virtual environment into a discrete
  NSBundle application bundle."* —— **直接就是"venv → .app"这个做法**
- [python-list 2011](https://mail.python.org/pipermail/python-list/2011-December/766554.html) 早就判定过
  「shell 脚本包壳」解决不了 bundle 身份：*"the executable that your shell script is starting is
  **in an app bundle of its own**, and MacOSX will be using the plist from that bundle"*
  —— 正是我们实测到的
- [`antonpictures/ANTON-SIFTA`](https://github.com/antonpictures/ANTON-SIFTA/blob/main/System/build_sifta_python_tcc_app.sh)
  2026-04 踩的是同一个坑（*"bare Homebrew Python enumerates cameras but macOS refuses frames
  without showing an Allow dialog"*）

### ⚠️ 一个必须记下的官方警告（与实测不矛盾）

Python 官方 venv 文档逐字：

> "If for any reason you need to move the environment to a new location, you should **recreate it
> at the desired location and delete the one at the old location**."

**为什么不矛盾**：那句话针对的是 `bin/` 里**已存在**的脚本（`activate`、console scripts）——
它们的 shebang 是**安装时写死的绝对路径**。搬布局之后：
- **ClassLive 用不到那些脚本**（只用 `python` 本身 + `site-packages`）→ 实际无影响
- 而且**搬完之后新装的包，shebang 是正确的**（实测 `sys.executable` 已经是新路径）

**配方上加固一句**：搬完布局后**再跑一次 `uv pip install -r requirements.txt`**，
让 console script 的 shebang 也指向新位置。一次性、零成本。

---

## 2. 已验证的事实（本机实测，带证据）

| # | 事实 | 怎么验的 |
|---|---|---|
| 1 | **双 fork 会破坏 LaunchServices 的单实例**：A（双 fork）连开两次 → **2 个进程**；B（不 fork）→ **1 个** | 三个最小 `.app` 对照实验，A/B 都是 python、同一启动路径，**唯一变量是 fork** |
| 2 | `fcntl.flock` 在持锁进程被 `kill -9` 后**自动释放** | 实测；对照 pidfile 法的陈旧文件问题 |
| 3 | **macOS 没有 `setsid` / `flock` 命令**（BSD 系不带） | `command -v` 逐个查。只能用 `os.setsid()` / `fcntl.flock` |
| 4 | Finder 启动时 `PATH` 只有 `/usr/bin:/bin:/usr/sbin:/sbin` | PyInstaller 官方文档逐字 + 逐条查 `cl` 用到的命令 |
| 5 | `cl` 用到的命令里，**只有两个**会找不到：`uv`(`~/.local/bin`)、`ffmpeg`(`/opt/homebrew/bin`) | 逐个 `command -v` + 比对 `/usr/bin` |
| 6 | 壳式 `.app`（`Info.plist` + 一个脚本）能启动、能收事件 | 76 个鼠标事件实测 |
| 7 | **`.app` 里 `NSBundle.mainBundle()` 指向的是 Python 自己的 bundle** | `bundlePath()` = `/opt/homebrew/Cellar/python@3.12/…/Python.framework/…` |
| 8 | ⭐ **`__CFBundleIdentifier` 环境变量是对的**（`page.bldcam.classlive`） | `.app` 启动时实测 |
| 9 | 所以 Info.plist 的 `LSUIElement` **不生效**（实测 `policy=0` Regular） | 与 #7 同根因：AppKit 读的是 **Python** 的 Info.plist |
| 10 | Finder 启动 `.app` 时 **cwd = `/`** | 实测，所以启动脚本必须自己 `cd` |
| 11 | **Liquid Glass 在 macOS 15.7.5 上根本不存在** | `objc.lookUpClass('NSGlassEffectView')` → `nosuchclass_error`；同测法下 `NSVisualEffectView` 存在且能实例化（对照有效） |
| 12 | HIG **明令**内容层不要用 Liquid Glass | 官方原文，见 §6 |
| 13 | ⭐ **麦克风授权落在 Python 解释器的绝对路径上**，不是 bundle ID | 两次独立复现（3.12 / 3.13），3.13 那次从 Finder 干净触发 |
| 14 | **`.app` 从 Finder 双击能跑通全流程**（含申请麦克风） | 双击实测，`__CFBundleIdentifier` 正确、录音成功 |
| 15 | Homebrew Python 自己的 `Info.plist` **没有** `NSMicrophoneUsageDescription` | `plutil -p` 实查（有 `CFBundleIdentifier` / `CFBundleName`，没有 usage 键） |
| 16 | `.venv/bin/python` 是 symlink，**TCC 认 realpath** | 条目里存的是 Cellar 路径，不是 venv 路径 |
| 17 | `tccutil reset Microphone <bundle-id>` **对路径型条目无效** | 实测：报 Successfully reset，条目仍在 |

---

## 3. 设计

### 3.1 `.app` 长什么样（**方案 I：`.app` 就是安装目录**）

```
ClassLive.app/Contents/
  Info.plist              ← CFBundleExecutable = python
                            CFBundleName = ClassLive
                            NSMicrophoneUsageDescription = …
  MacOS/                  ← 原 venv 的 bin/（含 python）
  lib/python3.11/         ← 原 venv 的 lib/（含 site-packages）
  pyvenv.cfg              ← 原 venv 的，位置决定 sys.prefix
  Resources/AppIcon.icns  ← 可选，见 3.3
```

**⚠️ 与旧设计最大的差别**：**没有 shell 启动脚本了。**
`CFBundleExecutable` 直接指向 `MacOS/python` —— 这就是让 `mainBundle()` 变成我们的那个开关（§1.5）。

**那启动参数怎么办？** `CFBundleExecutable` 传不了参数，两条路：

| 做法 | 说明 |
|---|---|
| **`sitecustomize.py`** | python 启动时自动 import，里面 `runpy.run_path(...)` 拉起 `main.py`。**实测可用**（本次验证就是用它） |
| 让 `main.py` 自己在 import 时启动 | 把 `if __name__ == "__main__"` 的逻辑挪到一个总是执行的入口 |

**首次实现建议用 `sitecustomize`** —— 不动 `main.py`，而且出问题容易摘掉。

**`cl` 怎么办**：它不再是"入口"，而是"开发/终端用的一条侧路"，
`PY` 改成 `"$HERE/ClassLive.app/Contents/MacOS/python"`（`cl:24`，**单一定义点**）。

### 3.2 ⭐ 单实例：**不要复用 `cl-bg.py` 的 daemonize**

**这是本计划最重要的一条，它推翻了"P1 几乎不用写代码"的原判断。**

`cl-bg.py` 的双 fork + `setsid` 是为了**脱离终端**而写的（它的 docstring：「把 ClassLive 从终端彻底剥离」）。
但 **`.app` 是 LaunchServices 启动的，根本没有终端可脱离** ——
所以那段 daemonize 在 `.app` 路径上**既没必要，又有害**：

- 实测证据：双 fork 的变体连开两次起了 **2 个**；不 fork 的变体 **1 个**
- 原因：LaunchServices 追踪的是**它启动的那个进程**，而双 fork 的设计就是让那个进程**立刻退出**

**方案**：

| 路径 | 单实例靠什么 |
|---|---|
| `.app` 双击 | **LaunchServices 免费给** —— 前提是**不 daemonize** |
| 终端 `cl`（可能连开两次） | `fcntl.flock` 一把锁 |

锁的实现要点（实测支持）：

- 用 `fcntl.flock(f, LOCK_EX | LOCK_NB)`，**不要**用"pidfile 存不存在"
  （实测：`kill -9` 后 pidfile 还在，得自己写清理逻辑；flock 是内核托管的，进程怎么死都自动释放）
- 锁文件放 `~/Library/Logs/ClassLive/` 或状态目录，**不要放 `/tmp` 固定名**
  （仓库自己的 `cl-bg.py` 注释里已记过这条：别人可预置同名文件或符号链接）

### 3.3 图标

- 仓库里**没有任何图标素材**（只有 README 徽章）→ 要么新做一个，要么先不做
- `iconutil` + `sips` **系统自带**，从 PNG 生成 `.icns` **零新依赖**：

```bash
mkdir AppIcon.iconset
for n in 16 32 128 256 512; do
  sips -z $n $n src.png --out AppIcon.iconset/icon_${n}x${n}.png
  sips -z $((n*2)) $((n*2)) src.png --out AppIcon.iconset/icon_${n}x${n}@2x.png
done
iconutil -c icns AppIcon.iconset
```

- 不做也能跑，只是 Finder/Dock 里是**通用图标**。**建议 P1 先不做**，等功能稳了再补。

### 3.4 `.app` 放哪 / 怎么生成

**放仓库根目录**（`~/lecture-live/ClassLive.app`）—— 但它现在**是安装目录本身**，不只是个壳：

- `git pull` 更新**代码**（`.py` / `cl` / `docs/`）
- `uv pip install` 更新**依赖**（`ClassLive.app/Contents/lib/…`）
- **两者互不干扰**，`.app` 不用重建

**实测：`.app` 被移动 / 改名都还能跑**（`sys.prefix` 与 `mainBundle()` 都跟着变）。
但仍然建议放仓库里 —— 因为 `cl` 要用相对路径找到它。

**生成方式：`make-app.sh`（脚本生成，不提交进 git，`.gitignore` 加一行 `.app/`）**

⚠️ **不要**把 `.app` 打进 GitHub Releases 让人下载 —— 浏览器下载会打 `com.apple.quarantine`
标记 → Gatekeeper 拦未签名应用 → 用户看到「App is damaged」。
**走 `git clone` / `curl` 生成就不带这个标记**（前面的调研已核实）。

### 3.4.1 ⭐ 改动量（实测，不是估的）

**代码里真正的引用只有 3 处，其中只有 1 处是关键：**

```
cl:24          PY=".venv/bin/python"        ← ⭐ 单一定义点，只改这一个
doctor.py:85   venv = HERE / ".venv"         ← "在不在"的检查
doctor.py:113  ┐
update.py:420  ┘ 只是打印给用户看的命令字符串
```

⭐ **`cl` 里 `.venv` 只出现一次**，而且 `cl:89` 补依赖那行**已经在用 `$PY` 变量** → 自动跟随。
**其余全是文档和实验脚本的用法注释。**

| 要改的 | 量 |
|---|---|
| `cl:24` | **1 行** |
| `doctor.py:85 / :113` | 2 行 |
| `update.py:420` | 1 行（提示文案） |
| `make-app.sh` | 新建，约 40 行 |
| `.gitignore` | +1 行（`.app/`） |
| 文档 | README 安装段、`docs/DESIGN.md`、`CLAUDE.md` 的跑法 |

**难度：中低** —— 因为爆破半径集中在一个变量上。

### 3.4.2 ⭐ 会不会给以后加功能带来结构性改变？—— **不会**

关键在**「依赖装到哪」这件事已经被集中了**：

```
现在：  cl:24  PY=".venv/bin/python"          →  uv pip install --python "$PY" -r requirements.txt
改后：  cl:24  PY="…/Contents/MacOS/python"   →  同一行，机制一个字没动
```

**未来要加依赖的地方**（P5 的 typst、P6 的术语表工具）走的都是**这一条路** ——
它们读 `requirements.txt`，`cl update` 和更新卡片按钮调同一份 `update.py`。
**换的只是 `PY` 的值，不是机制。**

- ❌ **不是**"引入一个以后每次加功能都要照顾的新结构"
- ✅ **是**"把一个已经集中的东西换个值"

**反过来说**：如果现在选 A（壳式），将来想换 I，要再动一次 `.app` 构建 + `cl:24` + 文档 ——
**同样的改动做两遍。** 这才是选 I 而不是 A 的真正理由。

### 3.4.3 还剩的风险

| 风险 | 严重度 | 说明 |
|---|---|---|
| ~~Homebrew 升级重弹授权~~ | ✅ **已消除** | 条目锚在 **bundle ID** 上（§1.5），与 python 版本无关 |
| ~~venv 搬进 .app 会坏~~ | ✅ **已排除** | 实测能跑、能装包 |
| ~~移动 / 改名 .app 会断~~ | ✅ **已排除** | 实测都能跑 |
| **首次构建要下 python** | 低 | `uv python install 3.11`（约 30 MB，一次性） |
| **console script 的旧 shebang** | 极低 | 搬布局前装的脚本 shebang 会是旧路径；**ClassLive 用不到**，且第 5 步重装一次就修正 |
| **现有 `.venv` 381MB 的迁移** | 低 | 只有作者这台；新装的人不受影响 |
| **目录结构变了** | 中 | `.venv` 不再是仓库里的独立目录 —— **README / 文档 / 使用习惯都要跟着改** |

### 3.5 启动失败要看得见

- 现在：`.accessory`（无 Dock 图标），失败时**什么都不发生**，日志在
  `~/Library/Logs/ClassLive/bg.log`，但用户不会去翻
- 方案：用 **`NSAlert`**（系统原生对话框，AppKit 现成，成本≈0）弹一条**人话**错误
- ⚠️ 但见 #9：`LSUIElement` 不生效意味着**启动瞬间会闪一下 Dock 图标**。
  这个由运行时的 `setActivationPolicy_(Accessory)` 兜住（ClassLive 已经在做）

### 3.6 引导式更新（让「立即更新」也能管依赖和模型）

**现状**（`cl` 里那段 shell）：记 `requirements.txt` 哈希 → `update.py` → 哈希变了才装依赖 → `doctor.py`

**搬到按钮里要补的东西：**

| 步 | 现状 | 要补 |
|---|---|---|
| 拉代码 | 已在做（`update.pull()`） | —— |
| 补依赖 | 只把命令打给用户 | 改成**问一句再跑**（`uv pip install`，**要几分钟**）。`update.pull()` 的返回值里**已经有 `reqs_changed`**，判定是现成的 |
| 模型 | `doctor.py` 只**打印**命令 | 要能**问「要下 X 吗（1.6 GB）」**。⚠️ `doctor.MODELS` 现在只有 `(path, label, required, cmd)`，**没有大小字段**，要补 |
| 重启 | 提示用户自己重开 | ⚠️ **绕不开**：`git pull` 换的是磁盘上的文件，**当前进程里跑的还是旧代码**。可选做法见 §3.9 |

**设计原则（照抄仓库已有的两条立场）：**

1. **重活先问、要报大小和耗时** —— 因为点了按钮你人就走了
2. **绝不静默下模型** —— README 里已经写着「模型不会自动下载」（1.6 GB 要不要下是用户的决定）
3. **正在录课时绝不自作主张** —— `overlay.py` 已有注释：
   「这个工具正在上课录课 —— 所以只换代码、不装依赖、不重启」

### 3.7 ⭐ 权限的状态流转 —— 三条路径都要说人话

（这一节来自外部评审 2026-09-26 的 UX 缺口建议，**已逐条核实**）

**Apple 官方原文**（[Requesting Authorization for Media Capture on macOS](https://developer.apple.com/documentation/bundleresources/requesting-authorization-for-media-capture-on-macos)）：

> "macOS **remembers the user's response** to this alert, so subsequent uses of the capture system
> **don't cause it to appear again**."

→ **用户点一次「不允许」，系统以后再也不问。** 而 Apple 自己的示例代码里，
`.denied` 分支就是 `return` —— **什么都不做**。

**对 ClassLive 的后果**：朋友第一次手滑点了「不允许」，之后**每次双击都静默失败**，
他完全不知道为什么。**必须自己补恢复路径。**

#### 三条路径

| 状态 | 会发生什么 | 该做什么 |
|---|---|---|
| **notDetermined**（第一次） | 系统弹框 | ⭐ **先弹一个自己的说明**（见下），用户点「继续」再触发系统框 |
| **authorized** | 直接跑 | 什么都不做 |
| **denied** | **系统框永不出现** | ⚠️ 弹一个 `NSAlert`：说清 + 一个按钮**直接跳系统设置** |

#### 预热说明（只在 notDetermined 时出现）

系统框一冒出来就是「`ClassLive.app` 想要访问麦克风」，**对第一次用的人毫无预警**。
在它之前插一层自己的说明，用户点「继续」才触发真正的系统请求。

⚠️ **但不能做成"每次启动都弹"** —— 本仓库已有明确立场（`main.py` / `obsidian_writer.py` 里都引过
Apple HIG：*"Avoid showing an alert when your app starts."*）。**只在 `notDetermined` 时出现一次。**

#### denied 的恢复引导

```
弹一个 NSAlert：
  「ClassLive 需要麦克风才能转录课堂。
   之前你点了「不允许」，macOS 不会再自动问了 —— 要去系统设置里手动打开。」
  [打开系统设置]  [知道了]
```

「打开系统设置」用这个 URL scheme 直达麦克风那一页，**不用让用户自己翻**：

```
x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone
```

（同族的 `…?Privacy_Camera` 有公开文档佐证，这个格式一致。⚠️ **实测未做** —— 打开它会真的弹系统设置窗口，P1 实现时验证一次即可。）

#### ⚠️ 怎么判断当前状态

需要读麦克风授权状态。两条路：

| 做法 | 代价 |
|---|---|
| `AVCaptureDevice.authorizationStatus(for: .audio)` | 精准，但要加 `pyobjc-framework-AVFoundation` |
| **试开一次音频流，catch 异常** | **零新依赖** —— `capture.py` 已经在做这件事（失败会抛 RuntimeError） |

**建议先用第二条** —— 不加依赖，而且失败的错误信息本来就要给用户看。

### 3.8 ⭐ 「说人话」的适用范围（§3.5 的扩展）

§3.5 只覆盖了"程序完全起不来"。但朋友碰到的多数是**半失败** —— 程序能跑，某个功能不可用。
**同一条原则要扩到所有他看不懂的状态：**

| 状态 | 现在的行为 | 该有的行为 |
|---|---|---|
| **缺 BlackHole**（线上课） | `capture.py:52` 抛 `RuntimeError("找不到名为 'BlackHole' 的输入设备。请先: brew install --cask blackhole-2ch …")` —— **提示是有的**，但走 `echo` → stdout | ⚠️ **从 `.app` 启动时 stdout 没有终端** → 提示**看不见**。必须转成 `NSAlert` |
| **权限被拒** | 静默失败 | 见 §3.7 |
| **更新失败** | 卡片上有 `user_msg`（已经去过 git 术语，`update._friendly()`） | ✅ 已达标 |
| **缺模型** | `doctor.py` 打印命令 | 同上，`.app` 里要转 `NSAlert` |

**一句话原则：从 `.app` 启动时，所有 `print` / `echo` 都要么进日志、要么进 `NSAlert` —— 不能扔进虚空。**

### 3.9 更新后自动重启（P1.5 的可选项，**不阻塞**）

现在 §3.6 那张表里「重启」写的是"提示用户自己重开"。可以更顺：给个「立即重启」按钮，
程序自己拉起新实例再退出旧的。

⚠️ **但有个必须处理的点**：**跟 P1.3 的单实例锁交接**。
新实例启动会尝试拿 `flock` —— 而旧实例还持着 → 新实例会把自己当成"已有实例"而退出。
**要么旧实例先放锁再拉新的，要么新实例带一个"我是重启来的"标志、允许短暂等待。**

**不是 P1 的阻塞项**，但属于"引导式更新"这个闭环缺的最后一步。

### 3.10 图标（P1 不做，但记一笔联动）

评审提了一个值得记的角度：**我们把权限弹窗上的名字从「python3.11」改成「ClassLive」花了很大力气，
如果弹窗旁边配的还是系统通用图标，那份信任感会打折扣。**

实测：不提供图标时，TCC 弹窗上是一个**通用图标**（蓝色方块 + 手/麦克风徽标）。
**P1 不做**，但和 §3.7 的预热说明是**同一个体验目的的两半** —— 哪天顺手做图标时放一起考虑。

### 3.11 ⭐ 要不要「走安装程序装进 `/Applications`」—— **结论：不做**

作者 2026-09-26 问的。**两轮独立调研 + 本机实测，结论一致：走符号链接，不做真安装。**

#### 层次一：`/Applications` 里放**符号链接**（⭐ **作者定了：做这个**）

```bash
ln -s ~/lecture-live/ClassLive.app /Applications/ClassLive.app
```

**实测能起**，而且 `mdls` 认它是 `com.apple.application-bundle`（Spotlight / Launchpad 当成 app）。
⭐ **它永远指向最新的那个 `.app`** —— `git pull` 后重建，链接自动跟上，不存在"两处要同步"。

**唯一要加固的**：链接指向仓库路径，**仓库一挪就断**。`install.sh` 里加一句自检即可。

#### 为什么**不**做"真安装"（`.dmg` / `.pkg` / Cask）

**① `/Applications` 的系统级好处，实测几乎为零。** 四条常见说法全被推翻：

| 常见说法 | 本机实测 |
|---|---|
| Spotlight 只索引 `/Applications` | ❌ 假 —— `mdfind` 索引到了 `~/lecture-live/ClassLive.app` |
| `open -a ClassLive` 找不到别处的 | ❌ 假 —— 实测找得到（`open -Ra` 定位到仓库里） |
| Time Machine 对两处策略不同 | ❌ 假 —— 两个都是 `[Included]` |
| `/Applications` 要管理员密码 | ❌ 假 —— `drwxrwxr-x root:admin`，admin 组直接可写 |
| Gatekeeper 对两处不同 | ❌ 无关 —— quarantine 是**文件扩展属性**，跟位置无关 |

**唯一真好处是「用户会在那里找」**（心智模型 + Finder 侧边栏）—— **符号链接就能给**。

**② ⭐ "真安装"会把项目推进「必须签名」的坑。** 三种形态现在都要了：

| | 要签名吗 | 权限 |
|---|---|---|
| `.dmg` | ⚠️ 要 | 用户级 |
| `.pkg` | ⚠️ 要 | **管理员密码** |
| Homebrew Cask | ⚠️ **从 5.0.0 起也要**（未签名的 cask **2026-09 起从官方 Tap 移除**） | 用户级 |

而且 **macOS 15 (Sequoia) 起「右键 → 打开」绕过 Gatekeeper 的做法没了** ——
用户必须去系统设置里手动放行。

**③ 而现在的分发方式（`git clone` / `curl`）天然避开了这一切** ——
`git` / `curl` 下载的文件**不打 quarantine**（§1.5 已实测），**Gatekeeper 根本不介入**。
**改成发 DMG 正中枪口，体验反而更差。**

**④ 自包含和签名天生冲突。** Apple 原文（TN2206）：

> "**Bundles should be treated as read-only once they have been signed.**"

把代码放进 bundle 之后，`git pull` 直写就是篡改 → macOS 报 *"The app has been modified or damaged"*。

**反过来说 —— 我们现在这个形状（运行时在 bundle 内、代码在外）恰恰是签名友好的：**

| | 装什么 | `git pull` 会碰它吗 |
|---|---|---|
| bundle 内 | 构建时就不变的东西（python + 依赖） | ❌ 永远不碰 |
| bundle 外 | 代码 | ✅ 随便改 |

**bundle 保持"出厂状态" —— 那正是签名想要的。** 本来以为自包含是"更正确的形状"，**其实反了**。

#### 什么时候改回来（改走"真安装"）

| 触发条件 | 那时**必须**改 |
|---|---|
| **要签名 / 公证（$99）** | ✅ 签名的前提就是 bundle 只读 |
| 朋友里有人不懂 git | ✅ 该做 `.dmg` 拖拽 |
| 朋友 > 3 个 | ✅ 手工步骤开始亏 |

#### 实施计划（作者 2026-09-26 定：做）

**模块**：「把 ClassLive 装成系统里能直接启动的 app」

**接口**（就三条 —— 其余全是藏起来的复杂度）：

```
./install.sh              构建（需要时）+ 装。幂等，可重复跑
./install.sh --check      只报告，不改任何东西
./install.sh --uninstall  撤掉（**只删我们建的链接**）
```

**藏起来的复杂度：**

| | |
|---|---|
| **冲突处理** | ⚠️ **只在目标"不存在"或"是我们的符号链接"时才动它**。已经是个**真 `.app`**（别人装的/你手动拷的）→ **拒绝并说清**，绝不覆盖。这是唯一有破坏性的分支，必须最保守 |
| **仓库挪位** | 符号链接指向的路径没了 → `--check` 报得出来（这是链接**唯一**的失效方式） |
| **幂等** | 重复跑结果一样 |
| **降级** | `/Applications` 不可写（非管理员用户）→ 退到 `~/Applications` 并说明 |
| **构建** | 内部调 `make-app.sh` —— **不重复那份逻辑**（同一个道理：两处各记一次迟早漂移） |

**为什么装 `/Applications` 而不是 `~/Applications`**（实测）：
两个都合法（`~/Applications` 里有 9 个 app），但**Finder 侧边栏默认只有 `/Applications`** ——
而"用户会在那里找"正是这条路唯一的真好处（§3.11 上面那张表）。
`/Applications` 是 `drwxrwxr-x root:admin`，**admin 组直接可写，不要密码**（实测）。

**不做**：不自动固定到 Dock（那是用户的选择）；不碰 `.app` 本体（它在仓库里，归 `git pull` 管）；
不用 `.pkg` / DMG（上面已论证）。

**验收判据：**

- [ ] `./install.sh` 后 `/Applications/ClassLive.app` **是符号链接**、指向仓库
- [ ] `open -a ClassLive` 能起
- [ ] **重复跑两次，结果一样**（幂等）
- [ ] 目标是**真 `.app`** 时**拒绝且不破坏**（红绿各验一次）
- [ ] 仓库挪位后 `--check` 报得出来
- [ ] `--uninstall` **只删我们建的链接**

---

#### ⚠️ 两条顺带查实、将来会用到的

1. **`ClassLive.app` 现在是 adhoc 签名** —— `codesign -dv` 显示
   `flags=0x20002(adhoc,linker-signed)`、`TeamIdentifier=not set`。
   那是 **Apple Silicon 链接器自动给的，不是真签名**，Gatekeeper 不认。
   **别误以为"已经签了"。**
2. **Apple 说 Python 脚本该放 `Contents/Resources/`**（[TN2206](https://developer.apple.com/library/archive/technotes/tn2206/_index.html) 原文：
   *"Store Python, Perl, shell, and other script files … in your app's `Contents/Resources` directory"*）。
   我们现在把 `sitecustomize.py` 放在 `Contents/lib/python3.12/site-packages/` ——
   **不签名时无害，但真要签名时得挪。**

---

## 4. 陷阱清单（都是实测踩出来的，不是推测）

| 陷阱 | 后果 | 怎么避 |
|---|---|---|
| ⚠️ **daemonize 会破坏单实例** | 双击两次起两个，叠窗 + 双倍花费 | `.app` 路径**不要** daemonize |
| ⚠️ **`mainBundle()` 不是你的 bundle** | 读不到自己 Info.plist 的键（`LSUIElement` 实测生效不了） | 读 `os.environ["__CFBundleIdentifier"]` |
| ⚠️ **Finder 启动 cwd = `/`** | 相对路径全废 | 启动脚本先 `cd` |
| ⚠️ **PATH 被砍成四个目录** | `uv` / `ffmpeg` 找不到 | 补两条绝对路径（**不用改 PATH 环境**） |
| ⚠️ **重新命名 `.app` 会断** | 反推不出安装目录 | 在 README 写明「不要重命名」；或干脆把路径写死 |
| ⚠️ **麦克风权限记在终端名下** | `.app` 要重新授权，且归属未知 | **见 §1，第一步就验** |
| **`.venv` 是 uv 建的、没有 pip** | `.venv/bin/python -m pip` 会失败 | 用 `uv pip install --python .venv/bin/python` |
| ⚠️ **`EXTERNALLY-MANAGED` 挡住 pip** | 自带的 python `pip install` 报「managed by uv」 | 删 `lib/pythonX.Y/EXTERNALLY-MANAGED`（§1.5） |
| ⚠️⚠️ **`MacOS/python` 是符号链接 → 双击没反应** | LaunchServices **不启动符号链接的 `CFBundleExecutable`**；退出码 0、无报错、无弹窗、进程 0 个 | **换成真拷贝**（§1.5 第 4 步）。能拷是因为独立 python 只链接系统库 |
| ⚠️ **`uv python find 3.12` 会返回项目自己的 `.venv`** | 那是基于 Homebrew 的 framework 构建 → 整个方案的前提不成立 | 加 `--managed-python`（§1.5 第 1 步）；`make-app.sh` 里还有一道前置检查专门拦它 |
| ⚠️ **`len(sys.argv) == 1` 判不出"双击"** | `python -c …` / `-m …` 的 argv 长度**也是 1**（实测 `['-c']`/`['-m']`） | 用 **`sys.argv[0] == ""`** —— 裸启动的 argv 是 `['']` |
| ⚠️ **符号链接代替拷贝会失效** | python 顺链解析，`sys.prefix` 落回基础解释器 | **必须真拷贝**（§1.5） |
| ⚠️ **别用 `tccutil reset Microphone` 清理** | 不加 bundle id 会清掉**所有** app 的授权；加了 bundle id 对**路径型**条目匹配不上（实测报 Successfully reset 但条目仍在） | 只能手工去系统设置里关 |

---

## 5. 明确不做（连同理由）

| 不做 | 理由 |
|---|---|
| **PyInstaller 自包含打包** | Finder 下 PATH 被砍、C 扩展隐式 import「no warnings, only an ImportError at run-time」（sherpa-onnx 正是 C 扩展）、6.0 起大量符号链接 |
| **签名 + 公证** | $99/年（Apple 官方逐字），而**走 git/curl 分发根本不需要** |
| **发 DMG** | 浏览器下载会打 quarantine → Gatekeeper 拦 |
| **重写成原生（Swift/Tauri）** | 成本巨大，收益只有"更像一个 app" |
| **换 Liquid Glass** | 见 §6 —— Apple 明令内容层不要用 |
| **图标（P1 阶段）** | 不做也能跑，等功能稳了再补 |

---

## 6. 顺带定案：**不换 Liquid Glass**（含官方依据）

作者问：「液态玻璃的这个效果要不要适配一下。」**答案是不要**，三条理由**一条比一条硬**：

**① Apple 官方明令禁止用在内容层**（HIG《Materials》逐字，已回源核对）：

> "**Don't use Liquid Glass in the content layer.** Liquid Glass works best when it provides a
> clear distinction between interactive elements and content … **Instead, use standard materials
> for elements in the content layer**, such as app backgrounds."

> "Liquid Glass forms a distinct functional layer for **controls and navigation elements**"

**② 而标准材质表里，恰好有一格就是 ClassLive 字幕面板的教科书定义**（同页逐字）：

| Material | Recommended for |
|---|---|
| `thick` | **Overlay views that partially obscure onscreen content** and require a **dark color scheme** |

→ **ClassLive 现在用的就是标准材质**（`NSVisualEffectView` + 暗色）。**它本来就做对了。**

**③ 你机器上调不到这个 API**：macOS 15.7.5，`NSGlassEffectView` 运行时不存在（实测）。

**④ 社区反馈集中在可读性**：Apple 自己在 macOS 26.1 加了「更不透明」的开关
（官方支持文档：「a new tinted look which **increases opacity**」）。

**但有一条该做的准备**（写进 P2）：抽 `panel.py` 时**把材质做成参数**，
不要写死 `NSVisualEffectMaterialHUDWindow`。这样将来真要换，是改一处。

---

## 7. 来源与时效性

**作者要求：「注意时效性，多方来源，成熟的同时有先进理念。」这份文档按这个标准做：**

| 类型 | 处理方式 |
|---|---|
| **本机实测** | §2 全部 12 条 —— 这是**最强的一手证据**，不依赖任何二手转述 |
| **Apple 官方** | HIG / 开发者文档 / 支持文档，**逐字引句已回源核对**（Liquid Glass 那段、PATH 那段） |
| **⏱ 时效** | 涉及版本的关键条目都标了日期/版本：Liquid Glass 是 macOS 26 (2025-09)；HIG 该页 changelog 2025-09-09；docling 2.130.0 |
| **⚠️ 未核实** | 见下 |

**⚠️ 我核不实、因此没有采信的：**

- 有说法称「Docling 首次要拉 **3–5 GB** 模型 / 4 页 PDF 冷启动 **137 秒**」——
  **找不到独立佐证**，只有第三方博客口径。我实测的是「默认安装会拖 torch（PyPI 元数据确认），
  而且**选 extra 也躲不掉**（逐步补依赖时第 2 轮就要 torch）」。**那两条具体数字不采信。**
- 有说法称「`WKWebView.createPDF` 只出一页」——**我实测是 2 页（同内容 WeasyPrint 是 17 页）**，
  即"不分页"成立，但"只出一页"这个具体描述**不准确**。
- 一条看起来像 macOS 26 回归的 `NSVisualEffectView` 问题，**核实后是 macOS 10.13 的旧帖**，与本项目无关。

---

## 8. 建议的动手顺序

| 步 | 做什么 | 状态 |
|---|---|---|
| **P1.0** | ~~验麦克风权限归属~~ | ✅ 完成（§1） |
| **P1.0.5** | ~~定方案 A 还是 I~~ | ✅ **定了 I**（§1.5） |
| **P1.1** | `make-app.sh` —— 生成壳式 `.app` | ✅ **完成**，双击实测能起 |
| **P1.2** | `cl:24` 指向 `.app` 内 python；`doctor.py` / `update.py` 同步 | ✅ **完成** |
| **P1.3** | 终端路径的单实例锁（`instance_lock.py`） | ✅ **完成**，14/14 测试 |
| **P1.4** | 所有状态说人话（`notice.py` + `sitecustomize` 的 stdout 改道） | ✅ **完成** |
| **P1.5** | 引导式更新（`update.pending_steps()` + 卡片按钮） | ✅ **完成** |
| **P1.6** | 更新后自动重启（§3.9） | ⬜ 可选项，不阻塞 |
| **P1.7** | 图标（§3.10） | ⬜ 延后 |
| **P1.8** | `/Applications` 符号链接（§3.11 层次一） | ⬜ 待作者定 —— **10 分钟、零风险** |

**P1.0 / P1.0.5 都已过。P1.1 没有阻塞项。**

### 第一次跑 `make-app.sh` 时要注意

1. **它会下 `uv python install 3.11`**（约 30 MB，一次性）
2. **它会新建 `ClassLive.app/` 并把依赖装进去**（约 56 MB）
3. **现有的 `.venv/` 先别删** —— 等 `.app` 跑通、`cl` 改完、确认没问题再清

（验收判据见文首 §0，不在这里重复。）
