#!/usr/bin/env python3
"""填 API key —— **校验 + 落盘 + 3.9.3 那次一次性搬家**。UI 在 `notice.ask_text`，这里不碰 AppKit。

## 为什么单独一个模块

`entry_panel.py` 已经 1900+ 行，而填 key 这件事**跟面板没关系** ——
它要能被 `cl setkey`（命令行）和面板**共用**，还要能在判据里跑（不弹窗）。

## 两条 key，两个用途，两份文件

| key | 存哪 | 给谁用 | 没有会怎样 |
|---|---|---|---|
| **DeepSeek** | `~/.classlive/credentials` | 翻译 / 精修 / 复习层 / 讲解 | 只有英文字幕（本地模型是可选的、默认没装；装了才会退回它，质量差些） |
| **Jev** | `~/.classlive/jev-key`（官方）<br>`~/.classlive/jev-token`（CommandCode 代理） | 「重点句」+「课务」闸门 | 那两个功能**静默关着** |

⚠️ 两份**刻意分开**：不同厂商、不同账、可以各自单独撤销。

## ⭐ 3.9.3：这一栏从「自己的 key」换成「作者的通行码」

作者 2026-10-12 定：「所有用户更新完这一个版本，Key 那一栏清空，然后提示说找作者拿 Key。」
理由：通行码上线后，散出去的原生 `sk-` key **收不回来**（那把只能整个换掉），也**按不了人计量**。
实现 = `clear_legacy_key()`（只挪不删，见它的 docstring）+ `ASK_AUTHOR` 那句提示。

## ⭐ 校验是「真调一次」，不是「看格式像不像」

一次调用几百 token、约几十毫秒 —— 比正则判断可信得多，而且**能分辨"key 对不对"
和"配额用完了"**（格式检查两件事都报"看起来没问题"）。
"""
from __future__ import annotations

import os
import pathlib

#: 3.9.3 那次一次性搬家的**版本口径** —— 标记文件里存的就是它（`paths.key_cleared_flag`）。
#: 将来若再来一次同样的搬家，改这里即可复用同一份标记文件。
CLEAR_VERSION = "3.9.3"

#: Key 那一栏**空着**时的提示 —— **唯一定义点**（面板与 `scripts/probe_keyentry.py` 共用）。
#: ⚠️ 与上面 MSG_* 三档是**两件事**：那三档说的是"存成没存成"，这一句说的是"去哪拿"。
#: Owen 2026-10-12 定：清空之后提示「找作者拿 Key」。
#: ⚠️ 后半句「不填只有英文字幕」**不能省** —— `tests/test_entry_panel.py` 钉着它，
#:    它是有来历的：上一版这里写的是「不填会退回本地模型」，而本地模型**可选、默认没装**
#:    —— 对没 key 的新用户那是句假话（2026-10-04 修的）。丢了后半句 = 用户不知道不填会怎样。
ASK_AUTHOR = "翻译用的。key 放这就好；不填只有英文字幕。"

#: ⭐ 首次更新后**弹一次**的那张框（Owen 2026-10-12 定：他给的原话 + 一句"填哪儿"）。
#: ⚠️ 只在**真的挪走了东西**的那一次弹（见 `clear_legacy_key` 的 `first`）——
#:    没有 key 的新用户、已经配好通行码的用户都**不该**被这张框拦一下。
KEY_RESET_TITLE = "翻译的 Key 换新了"
KEY_RESET_MESSAGE = ("转录翻译流程在此版本进行了重构，找菠萝丁拿 key。\n"
                     "拿到后在开课面板上点「翻译引擎」，填进「Key」那一栏。")

#: 保存后那一行的三档文案 —— **唯一定义点**（面板与 `cl setkey` 共用）。
MSG_BOTH = "✅ 存好了 —— 写进 ~/.classlive/，权限 600"
MSG_DS_ONLY = "✅ 存了翻译的 —— Jev 那条随时能补（再点这里）"
#: 填的是通行码：重点句 / 课务也由作者代付（3.9.4 起），Jev 那栏**不用**补 —— 别再提示他去补。
MSG_PASSCODE = "✅ 存好了 —— 菠萝丁给的 key 连「重点句」「课务」也一起管，Jev 那栏不用填"
# ⚠️ 第 3 档**不能**说「本地模型照常翻译」：本地模型（Qwen3-1.7B）是**可选、默认没装**
#    的（见本文件上面那张表）。对新用户说这句 = 「界面说的和实际不一样」——
#    正是 2026-10-04 修 key 对话框提示时要消灭的那类话。
MSG_NEITHER = "什么都没填 —— 能上课，但只有英文字幕（本地翻译模型可选、默认没装）"


#: 「填 API key」那张框的文案 —— 面板（`entry_panel._open_key_entry`）和截图探针
#: （`scripts/probe_keyentry.py`）都从这里取，截出来的图才代表真实界面。
DIALOG_TITLE = "填 API key"
DIALOG_MESSAGE = "只写进这台机器的 ~/.classlive/，不上传。"


def dialog_fields(cur_ds: str = "", cur_jv: str = "") -> list[dict]:
    """两栏：翻译用的 key（DeepSeek key 或通行码都填这里）+ Jev（可选）。已存过的回填。"""
    return [{"key": "deepseek", "label": "Key", "value": cur_ds,
             "hint": "已存过" if cur_ds else ASK_AUTHOR},
            {"key": "jev", "label": "Jev key · 可选", "value": cur_jv,
             "hint": ("已存过" if cur_jv
                      else "给「重点句」和「课务」用。填了菠萝丁给的 key 就不用管这栏。")}]


def status_line(has_deepseek: bool, has_jev: bool, passcode: bool = False) -> str:
    """保存之后 `_status` 那一行。**各档的唯一定义点。**"""
    if has_deepseek and has_jev:
        return MSG_BOTH
    if has_deepseek and passcode:
        return MSG_PASSCODE
    if has_deepseek:
        return MSG_DS_ONLY
    return MSG_NEITHER


def _write_600(path: pathlib.Path, value: str) -> None:
    """写一行纯文本，**权限 600**。⚠️ 先 `chmod` 再写 —— 反过来的话，
    在文件存在的那一瞬间它是默认权限（别的用户读得到）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    if not path.exists():
        path.touch(mode=0o600)
    path.chmod(0o600)
    with path.open("w", encoding="utf-8") as f:
        f.write(value.strip() + "\n")


# ---------------------------------------------------------------- 校验
def validate_deepseek(key: str, *, model: str = "deepseek-flash") -> tuple[bool, str]:
    """真调一次 DeepSeek。返回 `(行不行, 人话)`。

    ⚠️ 用**最小的请求** —— 校验不该花钱。`max_tokens=16` 够回一个词。
    ⚠️⚠️ **prompt 里必须出现 "json" 这个词**（2026-09-30 实测）：
       `_chat_json` **永远**带 `response_format: {"type": "json_object"}`，
       而 DeepSeek 对它的要求逐字是 ——
       「**Prompt must contain the word 'json' in some form** to use
       'response_format' of type 'json_object'」，
       不给就 **400**（而不是 401，看起来像"key 坏了"）。
       ⚠️ 既有调用方都没事，因为它们的 prompt 里本来就有「输出严格 JSON」——
       **只有新写的调用点会踩**。
    ⚠️ 顺带：这也让校验**验的是 app 真正会用到的那条路**（JSON 模式），不是"随便回句话"。
    ⚠️ 分三种回话：**能用 / key 不对 / 别的错**（网络、配额）。
       全都报「key 用不了」会让用户去改一个本来就对的 key。
    """
    key = (key or "").strip()
    if not key:
        return False, "空的"
    try:
        from build_notes import _chat_json
        # ⚠️ 15 秒（2026-10-01 审查 F9）：面板上有个框在等人 —— 默认 180 秒
        #    意味着「正在验证…」最多转 3 分钟。校验只需回一个词。
        got = _chat_json(key, model, 'Reply with JSON: {"ok": true}', "ping", 16,
                         0.0, timeout=15.0)
        return (True, "可用") if got is not None else (False, "没回内容")
    except Exception as e:                                    # noqa: BLE001
        why = _passcode_reason(e)
        if why:
            return False, why
        s = str(e)
        if "401" in s or "403" in s or "Authentication" in s:
            return False, "401 —— 检查是不是复制全了"
        if "402" in s or "insufficient" in s.lower():
            return False, "配额/余额不够"
        return False, f"{type(e).__name__}: {s[:60]}"


#: 一人一码：Worker 拒绝时响应体里的 `error`（`server/asr-worker/src/handler.js`）→ 给人看的话。
#: 不认得的原因 / 不是通行码 → 走下面通用的 401 / 配额判断。
PASSCODE_REASONS = {
    "unknown": "通行码不对 —— 检查是不是复制全了",
    "revoked": "通行码已停用 —— 问给你码的人",
    "expired": "通行码过期了 —— 问给你码的人续期",
    "devices": "这个码能用的设备数满了 —— 问给你码的人",
    "quota_exceeded": "今天的额度用完了，明天自动恢复",
}


def _passcode_reason(e: Exception) -> str:
    """HTTP 错误的响应体若是 Worker 的 `{"error": "<原因>"}`，返回对应的人话；否则空串。永不抛。"""
    try:
        return PASSCODE_REASONS.get(e.response.json().get("error", ""), "")   # type: ignore[attr-defined]
    except Exception:                                         # noqa: BLE001
        return ""


def validate_jev(key: str) -> tuple[bool, str]:
    """真调一次 Jev。返回 `(行不行, 人话)`。

    ⭐ **两家都试**：TypeSafe 官方（`apikey_…`）和 CommandCode 代理的 token
       长得不一样、也不通用 —— 让用户**粘哪个都行**，这里替他分辨。
    ⚠️ 官方那条**钉版本号**（我们标定过阈值，见 `keypoints.OFFICIAL_MODEL`）。
    """
    key = (key or "").strip()
    if not key:
        return False, "空的"
    import keypoints as K
    qs = {"S1": {"type": "noul", "instructions": "Is the sky blue?"}}
    state = "The sky is blue."
    tries = [("官方", K.OFFICIAL_ENDPOINT, K.OFFICIAL_MODEL),
             ("CommandCode", K.ENDPOINT, K.MODEL)]
    errs = []
    for name, ep, model in tries:
        try:
            got = K.ask_commandcode(state, qs, token=key, endpoint=ep, model=model,
                                    timeout=20.0)
            if K.parse_answers(got, 1)[0] is not None:
                return True, f"可用（{name}）"
        except Exception as e:                                # noqa: BLE001
            errs.append(f"{name}: {str(e)[:50]}")
    return False, " —— ".join(errs)[:110]


# ---------------------------------------------------------------- 落盘
def save(*, deepseek: str = "", jev: str = "", root=None) -> tuple[bool, str]:
    """写进 `~/.classlive/`。返回 `(成败, 人话)`。

    ⚠️ **空白的一栏不动原来的文件** —— 用户只想补 Jev 时，
       不该把已经配好的 DeepSeek 抹掉。
    ⚠️ 一个字节都没填时**直接成功**（文案见 `status_line`）—— 「什么都没填」
       是合法选择，不是错误（HIG：配置必须能推迟）。
    """
    import paths
    ds, jv = (deepseek or "").strip(), (jev or "").strip()
    if not ds and not jv:
        return True, status_line(False, False)
    try:
        if ds:
            _write_600(paths.credentials(root=root), ds)
        if jv:
            # ⭐ 官方 key 写 `jev-key`，CommandCode 的 token 写 `jev-token` ——
            #    ⚠️ **只按形状分写哪个文件**；"能不能用"由 UI 那边的
            #    `validate_jev()` 单独负责（这里不重复调一次网络）。
            _write_600(paths.jev_key(root=root) if _looks_official(jv)
                       else paths.jev_token(root=root), jv)
        import llm_client
        return True, status_line(bool(ds), bool(jv), llm_client.is_passcode(ds))
    except Exception as e:                                    # noqa: BLE001
        return False, f"没写进去（{type(e).__name__}: {str(e)[:60]}）—— 看看 ~/.classlive/ 能不能写"


def _looks_official(key: str) -> bool:
    """TypeSafe 官方的 key 长这样：`apikey_<hex>_<hex>`。

    ⚠️ **只用来决定写哪个文件**，不用来决定"能不能用"（那个靠真调一次）。
       猜错了用户还能再点一次改过来，而"看格式判断有效性"会挡住合法的新格式。
    """
    return key.startswith("apikey_")


def has_any(*, root=None, load_key=None) -> tuple[bool, bool]:
    """`(有 DeepSeek, 有 Jev)` —— 给就绪条和面板用。**读不出当没有，不抛。**

    ⚠️⚠️ `load_key` **可注入**，默认才是 `cloud_translator.load_api_key`。
       DeepSeek 那半走的是那个函数（它有一条 `--api-key` → 环境变量 → 文件
       的查找链），而它**不认 `root`** —— 判据直接调就会**读真实用户的 key**，
       于是"空目录应该返回 False"这条会**按构造失败**。
       同 [[test-harness-must-isolate-writes]]：**读端和写端都要能换。**
    """
    import paths
    if load_key is None:
        from cloud_translator import load_api_key
        load_key = load_api_key

    def _ok(p):
        try:
            return p.exists() and p.read_text(encoding="utf-8").strip() != ""
        except OSError:
            return False

    try:
        ds = bool(load_key(None))
    except Exception:                                         # noqa: BLE001
        ds = False
    jv = _ok(paths.jev_key(root=root)) or _ok(paths.jev_token(root=root))
    return ds, jv


def load_jev(*, root=None) -> str:
    """已存的 Jev key（`jev-key` 优先、其次 `jev-token`）—— 读不到 = `""`。

    ⚠️ 给填 key 框**回填用**（2026-10-01 作者要求「不要变成空白、保留填的」）：
       安全框里回填后显示的是圆点 —— 用户一眼能看出"已经存过了"。
    ⚠️ **不抛**：读不出来当没有（同 `has_any` 的纪律）。
    """
    import paths
    try:
        for p in (paths.jev_key(root=root), paths.jev_token(root=root)):
            try:
                t = p.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if t:
                return t
    except Exception:                                         # noqa: BLE001
        pass
    return ""


# ---------------------------------------------------------------- 3.9.3 的一次性搬家
def _read_key(p: pathlib.Path) -> str:
    """读一个 key 文件。**读不出来一律当空的** —— 断链、没权限、不是 UTF-8、是个目录。"""
    try:
        return p.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return ""


def _next_backup(p: pathlib.Path, version: str) -> pathlib.Path:
    """`credentials` → `credentials.bak-3.9.3`；已被占就往后编号。**绝不覆盖已有的备份。**"""
    for i in range(1, 1000):
        dst = p.with_name(p.name + f".bak-{version}" + (f"-{i}" if i > 1 else ""))
        if not dst.exists() and not dst.is_symlink():
            return dst
    raise OSError(f"{p.name}: 备份名用完了（.bak-{version} 到 -999 全被占）")


def clear_legacy_key(*, root=None, version: str = CLEAR_VERSION) -> dict:
    """3.9.3 一次性：把用户自己填的翻译 key **挪到旁边**，腾出「Key」这一栏改用通行码。

    返回 `{"done": 标记写成了没有, "moved": [落点…], "first": 这次是不是**头一回**做}`。
    **永不抛。**

    ⚠️ `first` 存在的唯一理由是给调用方判「要不要弹那张一次性提示框」：
       `first and moved` 恰好是「**这台机器上真的有一把 key 被挪走了**」——
       那正是唯一该打扰用户的情形。它天然只发生一次（版本标记挡着第二次），
       而且用户把备份恢复回去之后也**不会**再弹（那时 `first` 是 False）。

    ## 为什么要做

    通行码上线后，作者**收不回**已经散出去的原生 `sk-` key（那把只能去 DeepSeek 后台
    整个换掉），也**按不了人**计量。把这一栏清空 + 提示「找作者拿 Key」，
    是客户端这边唯一能把大家换过来的手段。

    ## 三条纪律

    ① **只挪不删** —— 落点是同目录的 `credentials.bak-3.9.3`。`os.rename` 同盘**原子**，
       权限（600）原样带过去。用户想要回自己那把 key，改个名就是原来那个文件。
    ② **绝不碰通行码** —— 值以 `cl-` 开头的**跳过**。这是**安全网**：即使标记丢了、
       这次搬家又跑一遍（换机器整理备份、用户手滑删了 `~/.classlive/`），
       已经配好的通行码也**不会被误伤**。
    ③ **标记最后才写** —— 中途失败就下次启动重试，**不谎报"做过了"**。

    ## 旧位置（`<安装目录>/.deepseek_key`）为什么**不在这里处理**

    它的那份 key 会**顶上来**（`load_api_key` 链上它排在 `credentials` 后面），
    所以"只挪新位置"对从没搬过家的老用户等于什么都没发生。
    但**解法不是去挪那个文件**（它在安装目录里，而且判据一跑就会动到开发机上的真文件）——
    是 3.9.3 起 `load_api_key` **干脆不再读它**。文件一个字节都没动。
    ⚠️ `--api-key` / `$DEEPSEEK_API_KEY` 那两路**照旧认**：它们不在文件里，
       也不是这个程序放的（见 `secrets-via-interactive-login`）。
    """
    out = {"done": False, "moved": [], "first": False}
    try:
        import paths
        flag = paths.key_cleared_flag(root=root)
        if _read_key(flag) == version:
            return {"done": True, "moved": [], "first": False}   # 做过了

        moved: list[str] = []
        p = paths.credentials(root=root)
        val = _read_key(p)
        if val and not val.startswith("cl-"):
            # 空文件没什么好挪；`cl-` 通行码是**目标状态**，绝不碰（纪律②）。
            dst = _next_backup(p, version)
            os.rename(p, dst)           # 同目录 = 同盘 = 原子
            moved.append(str(dst))
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text(version + "\n", encoding="utf-8")   # 纪律③：标记最后才写
        return {"done": True, "moved": moved, "first": True}
    except Exception:                                     # noqa: BLE001
        return out


if __name__ == "__main__":                                    # `cl setkey` 的兜底入口
    import sys
    a = sys.argv[1:]
    if a and a[0] in ("-h", "--help"):
        print("用法: cl setkey              # 交互式读入（不回显、不进 shell 历史）")
        print("      cl setkey <ds> [jev]   # ⚠️ key 会进 shell 历史 / `ps`，只留作兼容")
        print("      也可以在面板里点「翻译引擎」那一项。")
        sys.exit(0)
    if not a:
        # ⚠️ 默认走交互读入（2026-10-01 审查 F12）：`cl setkey <key>` 会把 key 写进
        #    shell 历史与 `ps` 输出 —— 键盘输入 + `getpass` 不回显就不落这两处。
        import getpass
        try:
            ds = getpass.getpass("DeepSeek key（不回显；留空 = 跳过）: ").strip()
            jv = getpass.getpass("Jev key（可选；留空 = 跳过）: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n（取消，什么都没动）")
            sys.exit(1)
        a = [ds, jv]
    else:
        print("⚠️ key 写进了命令行 —— 它已经在 shell 历史 / `ps` 里留下了。"
              "下次直接用 `cl setkey`（不带参数）交互式输入。", file=sys.stderr)
    _ok, _msg = save(deepseek=a[0] if a else "", jev=a[1] if len(a) > 1 else "")
    print(_msg)
    sys.exit(0 if _ok else 1)
