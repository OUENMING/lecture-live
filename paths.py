"""路径的唯一定义点 —— **只放 `~/.classlive/` 这一族新路径**。

## 为什么要有这个文件

仓库里既有的小状态文件全是「各自用 `with_name` 算自己的路径」：
`.course`（`cl` 的 `CFG`）· `.window`（`overlay`）· `.deepseek_key`（`cloud_translator`）·
`.update-seen` / `.update-skip`（`main` / `whatsnew`）· `term_notes*.json`（`build_notes`）·
`glossary/`（`translator`）· `sessions/`（`obsidian_writer`）。

开课前的准备要加**两个新位置**（课件归档 + 每课的 prep 状态）。不加这个文件的话，
P3 就会成为**又一批**各自算路径的地方 —— `CLAUDE.md` 对同类问题的判据是
「判据只在**一份实现**里」。

⚠️ **上面一列一律只写「文件 · 符号」，不写行号** —— 行号会腐坏（本文件第一版就写错过一个）。
要定位就 `grep` 那个符号。

## ⚠️ 三套约定并存，故意的

| | 放哪 | 例子 |
|---|---|---|
| **旧** | 代码旁（安装目录） | 上面那一列 |
| **新** | `~/.classlive/` | `courses/<课号>/materials/`、`prep-state.json`、`credentials` |
| **系统惯例** | `~/Library/Logs/ClassLive/` | 日志、`instance.lock`、`update.lock` |

- **新的为什么单开一支**：这里放的是**用户数据**（课件原件），不该住在会被 `cl update`
  的 `git pull` 更新的安装目录里。
- **为什么不干脆放进 `Logs/`**（它也不在仓库里）：`Logs/` 是**可清理**的 ——
  macOS 与各种清理工具都会动它。**用户数据不能住在会被清掉的地方。**
  ⚠️ 顺带：那个 root 今天被**两份文件各定义一次**（`instance_lock` 与 `update`），
  正是本文件想避免的失败形态；P4 搬迁时一并收口。
- 把旧的那一批也搬过来是 **P4「数据自立」** 的事。**两件事别混在一次改动里** ——
  混了就没法判断是哪个改动引起的回归。

## ⚠️ `sessions/` 永不搬

它是**三方共享契约**（`obsidian_writer` 写 / `_parse` 读回 / `cl last` 用 grep 匹配，
见 `CLAUDE.md`），动它会同时打断三处。
"""
from __future__ import annotations

import pathlib

STATE_ROOT = pathlib.Path.home() / ".classlive"


def _root(root) -> pathlib.Path:
    """默认根 —— `~/.classlive/` 那一族的**唯一一处**解析。

    ⚠️ 这条原来是每个函数里手抄一遍的三元表达式（本文件里七处）。
       那正是本文件开头在反对的东西：加一个新的根、或者改默认位置时，
       漏改任意一处就是**静默的路径漂移**。
    """
    return pathlib.Path(root) if root is not None else STATE_ROOT


def course_dir(course: str, *, root=None) -> pathlib.Path:
    """`root` 给了就用它 —— **别让调用方自己拼 `root/"courses"/<课号>`**。

    ⚠️ 加这个口子的理由（2026-09-26 审查指出）：面板要能指到别处（验收用隔离目录），
       于是每个调用点都手抄了一遍布局 —— 布局就有了**第二份定义**。加一个目录（P4 的
       数据目录）要改 N 处，而且读端/写端一旦漂开就是本仓库栽过的那类静默事故。

    ⚠️ **容错解析（2026-09-27 补，修一个真事故）**：目录名是「建它的时候 `.course`
       里的那个写法」，可能短（`cl` 的兜底分支就是把用户原样输入写进去的），
       而面板拿到的是规范全名。**两边必须指到同一个目录**，否则后果有两个、都不报错：
         · 准备度**谎报 0**（目录找不到 → 课件 0 份、自动加过 0 个）
         · **写入锁各拿各的**（`prep.state_lock_path` 明令禁止的那种「互斥静默失效」）
       规则与 `translator.course_terms_path` 同源：**精确优先，否则只认唯一命中**；
       有歧义就**退回精确路径**（宁可指到不存在的目录，也不能指到**别人的**目录）。

    ⚠️ **点开头的目录一律不算候选**（2026-09-28 审查指出）：`courses/.removed/` 是
       「只删课号」的保留区，`".removed".endswith("ed")` 为真 —— 课号 `ed` 这种短号
       会**唯一命中它**，于是课件被读写到已删课程的归档目录里，且全程无声。
    """
    root = _root(root)
    exact = root / "courses" / course
    cdir = exact.parent
    if exact.exists() or not cdir.is_dir():
        return exact
    hits = [p for p in sorted(cdir.iterdir())
            if p.is_dir() and not p.name.startswith(".")
            and (p.name.endswith(course) or course.endswith(p.name))]
    return hits[0] if len(hits) == 1 else exact


def corrections_log(*, root=None) -> pathlib.Path:
    """**用户纠正过的归属**（文件 → 课号）那本追加日志。

    ⭐ 它是**免费拿到的标注集**：`entry_panel._confirm_batch()` 里本来就
       读到了每个下拉框的值，只是以前读完就扔。
    ⚠️ 放 `~/.classlive/`（**不是** `Logs/`）—— 它是要留着的**数据**，
       不是会被清理工具扫掉的日志。
    """
    return _root(root) / "corrections.json"


def removed_dir(*, root=None) -> pathlib.Path:
    """「只删课号」时课件搬去的地方 —— `courses/.removed/`。

    ⚠️⚠️ **名字以点开头是必须的，不是风格。** `courses.list_courses()` 遍历
       `courses/` 下的目录来拼课程清单，而它最后那句是
       `if n and not n.startswith(".")` —— 点开头才**不会**出现在面板上。
       **把 `.removed` 改个名 = 那些已经搬走的课全部复活在面板上**（而且是空壳卡）。

    ⚠️ 布局定义在这里、不在这里手拼（同 `course_dir` 那条）：调用方一律走本函数。
    """
    return _root(root) / "courses" / ".removed"


def materials_dir(course: str, *, root=None) -> pathlib.Path:
    """课件归档处 —— 拖进来的 PDF/PPTX 放这里。

    归档（而不只在原地读）的三个理由：① 课件原件可能被用户从下载目录删掉；
    ② 重跑 prep 不必再找原文件；③ P4 的数据目录从这里长出来。

    ⚠️ **归档本身由 `prep._archive` 做，不要在别处再写一份拷贝循环** ——
       那份的纪律是「同名但大小不同的加 `-2` 后缀，绝不覆盖」（覆盖等于静默丢掉
       上一份课件）。面板曾经自己 `shutil.copy2` 覆盖，而且因为它在 `prepare` **之前**
       拷，`_archive` 看到的文件已经在归档目录里 → **它那道保护永远触发不到**。
    """
    return course_dir(course, root=root) / "materials"


def prep_state(course: str, *, root=None) -> pathlib.Path:
    """prep 的状态文件 —— 记「曾经追加过哪些词」。

    它是**墓碑**：用户从 `glossary/<课号>.txt` 里删掉的词，重跑时不许复活。
    （行业里的对应物是 memoQ 的 `stop word list`，见 `docs/PLAN-p3-prep.md` §6.1.2。）
    """
    return course_dir(course, root=root) / "prep-state.json"


def credentials(*, root=None) -> pathlib.Path:
    """DeepSeek API key 的**新家**（纯文本，第一行就是 key，权限 600）。

    ⚠️ 为什么要搬：旧位置 `<仓库>/.deepseek_key` 住在**安装目录**里，而那个目录会被
    `cl update` 的 `git pull` 更新 —— **用户数据不该住在会被更新覆盖的地方**（本文件
    文件头那两套约定的理由就是这个）。`.gitignore` 挡住了"被推上 GitHub"，
    挡不住"被更新洗掉"。
    ⚠️ `load_api_key` 仍然**认**旧位置（保留可用 + 启动告警），因为"搬"这件事
    不该由程序替用户做（见 `secrets-via-interactive-login`）。
    """
    return _root(root) / "credentials"


def voice_dir(*, root=None) -> pathlib.Path:
    """声纹材料目录（`~/.classlive/voice/`）—— 录下来的课音频 + 档案 + 状态机。

    ⚠️ **为什么是这里而不是 `sessions/`**：`sessions/` 有一条硬规矩
    「**只读不删、禁通配符**」（曾误删过一节真实课堂记录）。把"默认自动录、
    96 MB/节"的东西往里堆，等于**在一个不许清理的地方堆垃圾**。
    放这里之后清理规则是清晰的：**这个目录就是注册材料，攒够了整个可以删**。

    ⚠️ 它同时是**两份不同生命周期**的东西的家（`PLAN` §5）：
      · `<日期>_<时间>_<课号>.wav`       ← 声纹注册语料（攒到 done 才能删）
      · `<日期>_<时间>_<课号>.test.wav`  ← `cl test --record-audio` 的调试素材（随时可删）
    文件名带 `.test` 那一档把两者分开，**免得删一种时误删另一种**。
    """
    return _root(root) / "voice"


def voice_state(*, root=None) -> pathlib.Path:
    """声纹注册的**状态机**（`voice-enroll.json`，形状见 `PLAN` §1）。

    它是**唯一的真源**：`cl` 靠 `done` 决定要不要自动加 `--record-audio`，
    面板靠 `pending` 决定要不要问「上节课听着像 X，对吗」。
    """
    return voice_dir(root=root) / "voice-enroll.json"


def voice_profiles(*, root=None) -> pathlib.Path:
    """各课声纹档案（`profiles.json`）—— `{课号: [[float, …], …]}`。

    ⚠️⚠️ **这一份是唯一真源，不是缓存。** `sherpa-onnx` 在 **Python 侧没有
    "把 embedding 读回来"的接口**（官方 PR #3950 作者逐字：「once an embedding is
    enrolled there is **no way to read that vector back out**」；本机 1.13.8 与
    master 的 pybind 都核过）→ 管理器里那份**取不出来**，必须自己另存一份。
    """
    return voice_dir(root=root) / "profiles.json"


def ready_state(*, root=None) -> pathlib.Path:
    """就绪条被用户关掉了吗（`ready-dismissed`，形状照 `whatsnew` 的 `.update-seen`）。

    ⚠️ **放 `~/.classlive/` 而不是 `~/Library/Logs/ClassLive/`** —— 那个 root 是
    **可清理**的（`paths.py` 文件头逐字：「用户数据不能住在会被清掉的地方」），
    而「我已经把这个提示关掉了」是**用户的决定**，清掉会把它重新弹回来。
    """
    return _root(root) / "ready-dismissed"


def models_stamp(*, root=None) -> pathlib.Path:
    """各模型**装的是哪个版本**（`models.json`）—— `{路径: {"src":…, "rev":…, "at":…}}`。

    它回答的问题是 `doctor.model_present()` 答不了的那个：**「在」不等于「是我要的那版」**。
    一个老用户手里可能有旧版、或者装了一半，只看"目录非空"会一律报 ✅。

    ⚠️ **这份文件是"我们自己装的"的凭据，不是模型自身的属性** —— 所以它缺失
       **不等于模型不能用**（老用户 / 手动装的都会有文件没戳）。那种情况要落
       「版本没法核实」，**绝不重下** —— 重下等于白烧 1.2 GB 流量。
    """
    return _root(root) / "models.json"


def vault_config(*, root=None) -> pathlib.Path:
    """用户选过的 Obsidian 库路径（纯文本一行）—— `~/.classlive/vault`。

    ⚠️ **为什么不能只靠 `$OBSIDIAN_VAULT`**：那个环境变量只在**交互式 shell** 里有
    （`~/.zshrc`），而双击 `.app` 起的那条路继承的是 **launchd 的环境** ——
    实测 `launchctl getenv OBSIDIAN_VAULT` 是空的，`make-app.sh` 里也没有
    `LSEnvironment`。于是「从终端跑」和「双击跑」会落到两个不同的库。
    **实测后果**：`~/Obsidian/Vault/Lectures/` 里躺着 9 个笔记，而那个目录
    连 `.obsidian` 都没有 —— 它不是 vault，是应用自己 `mkdir` 出来的。

    这个文件就是那个缺口的补丁：**用户选过一次就记住**，下次没有 shell 环境也找得到。
    """
    return _root(root) / "vault"


def timetable(*, root=None) -> pathlib.Path:
    """导入过的那份课表（`{课号: [时段…]}`）—— **解析结果，不是原始 `.ics`**。

    ⚠️ 为什么放这儿而不是 `<sessions>/`：它**不是课堂记录**，是用户倒进来的一份配置
       （删掉只是回到「不预选」，一个字都不会丢）。同 `vault` 那一族。
    ⚠️ 为什么必须存：`.ics` 原本解析完就扔了 → **预选没有数据源**。
       原始文件可能上兆，而这里只要 `(周几, 时分, 时长, 间隔, 锚点, 停课日)`。
    """
    return _root(root) / "timetable.json"


def jev_token(*, root=None) -> pathlib.Path:
    """Jev（TypeSafe System One）的 token —— 纯文本一行，权限 600。

    ⚠️ **与 `credentials()` 分开两份。** 那个文件装的是 **DeepSeek** 的 key，
       两个是不同厂商、不同用途、可以各自单独撤销的凭证 —— 塞一个文件里
       会让「我只想关掉重点句」变成「我得把翻译也一起停了」。
    ⚠️ 没有这个文件 = **这个功能就是关的**，不是错误：`keypoints` 那条路
       在课后跑、失败不影响任何东西（同 `polish` 的 fail-soft）。
    """
    return _root(root) / "jev-token"




