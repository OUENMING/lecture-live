"""路径的唯一定义点 —— **只放 `~/.classlive/` 这一族新路径**。

## 为什么要有这个文件

仓库里既有的小状态文件全是「各自用 `with_name` 算自己的路径」：
`.course`（`cl` 的 `CFG`）· `.window`（`overlay`）· `.deepseek_key`（3.9.3 起没人读了）·
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


#: 桌面上的笔记总文件夹（作者 2026-10-19 定名）。里面按课号分文件夹放详细版 HTML。
NOTES_LIBRARY = "烤菠萝的笔记库"


def notes_library(*, home=None) -> pathlib.Path:
    """`~/Desktop/烤菠萝的笔记库` —— 每个人都有的笔记落点（有没有 Obsidian 库都写）。

    ⚠️ 调用时才取 `Path.home()`（不在 import 时定死）：测试改 `HOME` 就能把写端隔离走。
    """
    return pathlib.Path(home if home is not None else pathlib.Path.home()) / "Desktop" / NOTES_LIBRARY


def course_notes_dir(course: str, *, home=None) -> pathlib.Path:
    """`~/Desktop/烤菠萝的笔记库/<课号>`。课号里的 `/` 换成 `-`（不许变成子目录）。"""
    name = str(course or "LECTURE").strip().replace("/", "-").replace(":", "-") or "LECTURE"
    return notes_library(home=home) / name


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


def test_key(*, root=None) -> pathlib.Path:
    """**实验 / 探针 / 测试专用**的通行码（`test1` 那个，权限 600）。

    ⚠️ 2026-10-14：碎句合并实验走了 `load_api_key()` → 用的是作者自己的码，管理面板上
       「owen 今天用了 1755 次」，而作者那天根本没上课。实验、A/B、探针一律读这里
       （`cloud_translator.load_test_key`），用量记在 test1 名下，和真上课的账分开。
    """
    return _root(root) / "test-key"


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


def whisper_card(*, root=None) -> pathlib.Path:
    """「旧的本地 Whisper 还能清掉」那张卡的状态 —— `~/.classlive/whisper-card.json`。

    形状 `{"cloud_classes": int, "answered": "" | "trash" | "keep"}`（读写都在 `whisper_card.py`）。
    ⚠️ 住 `~/.classlive/` 的理由同 `ready_state`：用户的回答与计数是**要留着的数据**，
       不能住会被清理工具扫掉的 `Logs/`，也不能住会被 `git pull` 重写的安装目录。
    """
    return _root(root) / "whisper-card.json"


def key_cleared_flag(*, root=None) -> pathlib.Path:
    """「老 key 已经挪到旁边了」的标记（`key-cleared`，内容是**做这件事的版本号**）。

    ⚠️ **必须住 `~/.classlive/`，不能住安装目录**（`.update-seen` 那一族住那儿）：
       安装目录会被 `git pull` 重写，而标记一丢，那次搬家会**再跑一遍** ——
       那时用户早就填好新的通行码了。同 `ready_state` 那条「用户的决定不能住会被清掉的地方」。
    ⚠️ 存版本号而不是 `1`：将来若再来一次同样的搬家，改 `keyentry.CLEAR_VERSION` 即可
       复用同一份文件（形状照 `whatsnew.seen_flag_path`）。
    """
    return _root(root) / "key-cleared"


def tour_seen(*, root=None) -> pathlib.Path:
    """功能介绍卡看过哪一版（`tour-seen`，内容是 `tour.TOUR_VERSION`）。

    ⚠️ 住 `~/.classlive/` 的理由同 `key_cleared_flag`：安装目录会被 `git pull` 重写，标记一丢就每次都弹。
    """
    return _root(root) / "tour-seen"


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


def upload_queue(*, root=None) -> pathlib.Path:
    """待上传队列（测试模式的课后上传）—— `~/.classlive/upload-queue.json`。

    ⚠️ **必须落盘**，不能只在内存：上传失败要留到**下次启动**再试，
       而那时已经是另一个进程了（`upload.UploadQueue` 的 docstring 有形状）。
    """
    return _root(root) / "upload-queue.json"


def upload_ledger(*, root=None) -> pathlib.Path:
    """上传台账 —— `~/.classlive/upload-ledger.json`，`会话名 -> 对象前缀`。

    ⚠️ **不是可选项**：远端的对象名是**会话名的哈希**（刻意的 —— 原文件名含
       课号 + 精确时间戳，等于一份课表），而哈希**不可逆** →
       没有台账就**删不掉某一节课**（作者定的是手动清，见 `PLAN-test-mode.md` D8）。
    """
    return _root(root) / "upload-ledger.json"


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


def jev_key(*, root=None) -> pathlib.Path:
    """**TypeSafe 官方**（`api.typesafe.ai`）的 API key —— 纯文本一行，权限 600。

    ⚠️ 与 `jev_token()` **分开两份**：那是 CommandCode 代理那条路的 token。
       两个厂商、两笔账、可以各自单独撤销 —— 塞一个文件里会让
       「我只想换一家」变成「我得把另一家也停了」。
    ⚠️ 两个 URL 的**格式完全一样**（同 schema、同响应结构，2026-09-30 实测），
       差别只在**三处**：model ID、认证 key、要不要 `User-Agent`。
       详见 `keypoints` 模块头那张表。
    """
    return _root(root) / "jev-key"


def cloud_asr_config(*, root=None) -> pathlib.Path:
    """云端定稿转写的开关与告知记录 —— `~/.classlive/cloud-asr.json`。

    形状 `{"enabled": bool, "acknowledged": "<ISO 日期>"}`；读写都在 `final_asr`。
    ⚠️ 放 `~/.classlive/` 不放 `Logs/`：「我在面板上关了云端转写」是用户的决定，清掉会重新弹告知、并回到默认开。
    ⚠️ `CLASSLIVE_CLOUD_ASR=0` 时 `final_asr` 不写它（R7）。
    """
    return _root(root) / "cloud-asr.json"


def asr_admin_token(*, root=None) -> pathlib.Path:
    """一人一码的**管理令牌** —— `~/.classlive/asr-admin-token`（纯文本一行，权限 600）。

    ⚠️ 与 Worker 的 `ADMIN_TOKEN` secret 同值。它只有一把钥匙的权限：**管通行码**，
       既不能转写也不能翻译（那两件事走 `CLIENT_TOKEN` / 通行码本身）。
    ⚠️ **只读它的两处**（`scripts/cl_admin.py` / `scripts/cl_admin_ui.py`）**都不打印它**；
       页面那条路更是只留在 Python 侧，绝不进浏览器。
    """
    return _root(root) / "asr-admin-token"


def region_probe_state(*, root=None) -> pathlib.Path:
    """Soniox 地区探测的状态（`region-probe.json`）—— 每版本前 3 节课的计数 + 每网络 24 h 结果缓存。

    形状（读者是 `region_route`）：`{"v": <版本>, "n": <已探测过的节数>,
    "cache": {"<网络哈希>": {"at": <epoch>, "default": "us", "regions": {...}}}}`。
    ⚠️ 版本对不上（`v` ≠ 当前 VERSION）**整个重置** —— 那是「每个新版本只测前 3 节课」的判据。
    ⚠️ 网络指纹**只存哈希**（`region_route.network_key()`），明文 SSID / 网关 MAC 不落盘、不进日志。
    """
    return _root(root) / "region-probe.json"


def install_id(*, root=None) -> pathlib.Path:
    """随机 32 位 hex，云端定稿 Worker 按它限流 —— `~/.classlive/install-id`（权限 600）。

    只是随机串，不含任何个人信息；删了会重新生成。
    """
    return _root(root) / "install-id"


def duty_backup(*, root=None) -> pathlib.Path:
    """「一些事儿」写不进提醒事项时的**本地备份** —— `~/.classlive/duty-backup.jsonl`。

    ⚠️ 为什么必须有（而不是只把失败写在卡上）：写提醒事项要过系统的**自动化授权**
       （`tell application "Reminders"`）—— 首次会弹窗，拒绝 / 超时都算失败。
       那时**用户已经点了「记下」**，条目绝不能因为一次授权没点就消失。
       → 卡片上写明失败原因，条目落这里；成功的那条**不写**（它已经在提醒事项里）。
    ⚠️ 只追加、只失败时写（同 `.lost.jsonl` 的纪律：文件不删、是证据）。
    """
    return _root(root) / "duty-backup.jsonl"


def recap_state(course: str, *, root=None) -> pathlib.Path:
    """「课前回顾」的**上一节产物** —— `~/.classlive/courses/<课号>/recap.json`。

    ⚠️ **按课存，不按会话存**：它要活到**下一节同一门课**（开课时直接读），
       跟 `sessions/` 那种一节课一份的东西不是一类。放 `sessions/` 里就等于
       每次开课都要扫目录猜"上次是哪一节"。
    ⚠️ 形状与写入点见 `recap` 模块头；这里只定义路径。
    """
    return course_dir(course, root=root) / "recap.json"

