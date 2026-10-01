#!/usr/bin/env python3
"""填 API key —— **校验 + 落盘的两条纯逻辑**。UI 在 `notice.ask_text`，这里不碰 AppKit。

## 为什么单独一个模块

`entry_panel.py` 已经 1900+ 行，而填 key 这件事**跟面板没关系** ——
它要能被 `cl setkey`（命令行）和面板**共用**，还要能在判据里跑（不弹窗）。

## 两条 key，两个用途，两份文件

| key | 存哪 | 给谁用 | 没有会怎样 |
|---|---|---|---|
| **DeepSeek** | `~/.classlive/credentials` | 翻译 / 精修 / 复习层 / 讲解 | 退回本地模型（能上课，质量差些） |
| **Jev** | `~/.classlive/jev-key`（官方）<br>`~/.classlive/jev-token`（CommandCode 代理） | 「重点句」+「课务」闸门 | 那两个功能**静默关着** |

⚠️ 两份**刻意分开**：不同厂商、不同账、可以各自单独撤销。

## ⭐ 校验是「真调一次」，不是「看格式像不像」

一次调用几百 token、约几十毫秒 —— 比正则判断可信得多，而且**能分辨"key 对不对"
和"配额用完了"**（格式检查两件事都报"看起来没问题"）。
"""
from __future__ import annotations

import os
import pathlib

#: 保存后那一行的三档文案 —— **唯一定义点**（面板与 `cl setkey` 共用）。
MSG_BOTH = "✅ 存好了 —— 写进 ~/.classlive/，权限 600"
MSG_DS_ONLY = "✅ 存了翻译的 —— Jev 那条随时能补（再点这里）"
MSG_NEITHER = "什么都没填 —— 不影响上课，本地模型照常翻译"


def status_line(has_deepseek: bool, has_jev: bool) -> str:
    """保存之后 `_status` 那一行。**三档的唯一定义点。**"""
    if has_deepseek and has_jev:
        return MSG_BOTH
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
        got = _chat_json(key, model, 'Reply with JSON: {"ok": true}', "ping", 16, 0.0)
        return (True, "可用") if got is not None else (False, "没回内容")
    except Exception as e:                                    # noqa: BLE001
        s = str(e)
        if "401" in s or "403" in s or "Authentication" in s:
            return False, "401 —— 检查是不是复制全了"
        if "402" in s or "Insufficient" in s.lower():
            return False, "配额/余额不够"
        return False, f"{type(e).__name__}: {s[:60]}"


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
        return True, status_line(bool(ds), bool(jv))
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


if __name__ == "__main__":                                    # `cl setkey` 的兜底入口
    import sys
    a = sys.argv[1:]
    if not a or a[0] in ("-h", "--help"):
        print("用法: python keyentry.py <deepseek-key> [jev-key]")
        print("      也可以在面板里点「翻译引擎」那一项。")
        sys.exit(0)
    _ok, _msg = save(deepseek=a[0] if a else "", jev=a[1] if len(a) > 1 else "")
    print(_msg)
    sys.exit(0 if _ok else 1)
