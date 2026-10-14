#!/usr/bin/env python3
"""填 API key 的判据 —— `keyentry` 的纯逻辑 + `notice.build_text_alert` 的几何。

    ClassLive.app/Contents/MacOS/python tests/test_keyentry.py

## 为什么这里能测「填 key」而不联网

`keyentry.save()` / `status_line()` / `has_any()` 全是**纯逻辑**（除了 `has_any`
默认会调 `load_api_key` —— 所以它**必须**能注入读端，见 `test_has_any_injected`）。
校验那两个真的会联网，本文件**不碰**。

## ⚠️ 本文件钉住的两个真实缺陷（都发生过）

1. **只补 Jev 时把已配好的 DeepSeek 抹掉** —— `save(jev=…)` 带默认空串，
   第一版会照着空串写 `credentials`。
2. ⚠️⚠️ **`NSAlert` 不调 `layout()` 就不装附件视图** —— 窗口只剩 260 宽、
   按钮竖排、输入框**根本不进视图树**，而 `accessoryView()` 照样返回那个对象
   （看起来"设过了"）。生产走 `runModal()` 看不出来，**探针不 runModal 才暴露**。
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import keyentry as KE                                                # noqa: E402
import notice                                                        # noqa: E402
import paths as PA                                                   # noqa: E402


RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  {detail}" if detail else ""))


def _read_or(path: pathlib.Path) -> str:
    """读一行文本；**文件不在了返回一个不可能等于期望值的哨兵**。

    ⚠️ 为什么要有它：第一版那几条判据直接 `read_text()` —— 实现一旦坏成「文件被挪走」，
       `FileNotFoundError` 会**把整个文件带崩**（后面的判据一条都不跑，也没有 ❌ 行）。
       实测：变异「去掉 `cl-` 跳过」与「去掉版本标记早返回」都撞上了这一条 ——
       **判据红了，但红得看不见**。同 `test_live_summary` 那条教训。
    """
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return "〈文件不在了〉"


def main() -> int:
    """⚠️ 每个用例各自 `with tempfile.TemporaryDirectory()` —— 它自己回收，
    **本函数刻意没有顶层 `finally`**（第一版在这里留了一行 `shutil.rmtree(...)`，
    长得像会删整个临时目录 —— 已删）。"""
    print("\n--- ① 保存后那句话：三档的唯一定义点 ---")
    # 改坏：把任一档的文案换掉 -> 对应那条红。
    check("两个都填", KE.status_line(True, True) == KE.MSG_BOTH, KE.MSG_BOTH)
    check("只填 DeepSeek", KE.status_line(True, False) == KE.MSG_DS_ONLY, KE.MSG_DS_ONLY)
    check("都没填", KE.status_line(False, False) == KE.MSG_NEITHER, KE.MSG_NEITHER)
    check("只填通行码 → 不叫他去补 Jev（3.9.4 起通行码代付）",
          KE.status_line(True, False, True) == KE.MSG_PASSCODE, KE.status_line(True, False, True))
    check("⚠️ 文案里**不许**出现「失败」这类词（都没填是合法选择，不是错误）",
          "失败" not in KE.MSG_NEITHER and "错误" not in KE.MSG_NEITHER)
    # ⚠️ 也不许承诺「本地模型照常翻译」—— 它**可选、默认没装**，对新用户那是句假话。
    #    与 `entry_panel` 那条「不填只有英文字幕」是同一件事，两处必须一致。
    check("⚠️ 文案里**不许**承诺本地模型会顶上（它默认没装）",
          "本地模型照常" not in KE.MSG_NEITHER and "照常翻译" not in KE.MSG_NEITHER)

    print("\n--- ② `save()`：落盘 + 权限 + 不误伤 ---")
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        okk, msg = KE.save(deepseek="sk-abc", jev="apikey_x_y", root=root)
        cred, jk = root / "credentials", root / "jev-key"
        check("返回成功", okk, msg)
        check("DeepSeek 写进 credentials", cred.read_text().strip() == "sk-abc")
        check("⭐ 官方形状的 key 写进 `jev-key`（不是 jev-token）", jk.exists())
        # 改坏：把 `_write_600` 里的 `chmod(0o600)` 去掉 -> 这条红。
        check("⭐⭐ 权限是 600（密钥文件）",
              oct(cred.stat().st_mode & 0o777) == "0o600",
              oct(cred.stat().st_mode & 0o777))
        check("⭐ 父目录收成 700", oct(cred.parent.stat().st_mode & 0o777) == "0o700",
              oct(cred.parent.stat().st_mode & 0o777))
    with tempfile.TemporaryDirectory() as d:
        okk, msg = KE.save(deepseek="cl-ying-0123456789abcdef", root=pathlib.Path(d))
        check("⭐ 存的是通行码 → 说「Jev 那栏不用填」（不是「随时能补」）", msg == KE.MSG_PASSCODE, msg)
        okk, msg = KE.save(deepseek="sk-own", root=pathlib.Path(d))
        check("存的是自己的 DeepSeek key → 照旧提示可以补 Jev", msg == KE.MSG_DS_ONLY, msg)
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        # ⚠️⚠️ **已存在的、权限宽松的文件也要被收紧** —— 这是那条 `chmod` 唯一不可替代的
        #    作用（新文件光靠 `touch(mode=0o600)` 就是 600 了）。
        #    第一版没测这条 → **去掉 `chmod` 判据照样全绿**（2026-09-30 变异验证抓到的）。
        wide = root / "credentials"
        wide.write_text("sk-old\n", encoding="utf-8")
        wide.chmod(0o644)
        KE.save(deepseek="sk-new", root=root)
        check("⭐⭐ **已存在的宽松文件**会被收紧成 600（不是只对新文件生效）",
              oct(wide.stat().st_mode & 0o777) == "0o600",
              oct(wide.stat().st_mode & 0o777))
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        KE.save(deepseek="sk-first", root=root)
        KE.save(jev="apikey_a_b", root=root)          # 只补 Jev
        # 改坏：`save()` 里对空白栏也照写 -> 这条红（credentials 被写成空）。
        check("⭐⭐ **只补 Jev 不抹掉已配的 DeepSeek**",
              (root / "credentials").read_text().strip() == "sk-first",
              repr((root / "credentials").read_text()))
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        okk, msg = KE.save(root=root)                 # 两栏都空
        check("⭐ 都不填 = 合法选择（成功 + 不建任何文件）",
              okk and not list(root.iterdir()), msg)
    with tempfile.TemporaryDirectory() as d:
        KE.save(jev="cc-token-xyz", root=pathlib.Path(d))
        check("非 `apikey_` 形状 -> 写 `jev-token`（CommandCode 那条）",
              (pathlib.Path(d) / "jev-token").exists())

    print("\n--- ③ `has_any()`：读端必须能换 ---")
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        # 改坏：把 `load_key` 注入去掉（直接调 load_api_key）-> 这几条会去读
        #       **真实用户的 ~/.classlive/**，于是「空目录该返回 False」按构造失败。
        check("⭐⭐ 空目录 + 注入的假读端 -> (False, False)",
              KE.has_any(root=root, load_key=lambda _: None) == (False, False))
        (root / "jev-key").write_text("apikey_a_b\n", encoding="utf-8")
        check("有 jev-key -> (False, True)",
              KE.has_any(root=root, load_key=lambda _: None) == (False, True))
        check("读端说有 -> (True, True)",
              KE.has_any(root=root, load_key=lambda _: "sk-x") == (True, True))
        check("⭐ 读端抛异常 -> 当没有，不崩",
              KE.has_any(root=root, load_key=lambda _: 1 / 0) == (False, True))

    print("\n--- ③c `load_jev()`：读回已存的 Jev key（给填 key 框回填用）---")
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        check("空目录 -> 空串（不抛）", KE.load_jev(root=root) == "")
        (root / "jev-token").write_text("cc-token\n", encoding="utf-8")
        check("有 jev-token -> 读回来", KE.load_jev(root=root) == "cc-token")
        (root / "jev-key").write_text("apikey_a_b\n", encoding="utf-8")
        check("⭐ key 与 token 都在 -> **key 优先**",
              KE.load_jev(root=root) == "apikey_a_b")

    print("\n--- ③b ⚠️ `validate_deepseek` 的 prompt 必须含 'json' ---")
    # ⚠️⚠️ `_chat_json` **永远**带 `response_format: json_object`，而 DeepSeek 对它的
    #    要求逐字是「Prompt must contain the word 'json' in some form」——
    #    不给就 **400**（不是 401，看起来像"key 坏了"）。
    #    第一版的 prompt 是 "Reply with the single word OK." / "ping" → 真 key 也 400。
    #    ⚠️ 这条**不联网**：读源码里那个字符串就够了。
    _src = (pathlib.Path(KE.__file__).read_text(encoding="utf-8"))
    _i = _src.index("def validate_deepseek")
    _body = _src[_i:_src.index("def validate_jev")]
    check("⭐⭐ 那句 system prompt 里出现了 `json`（大小写都算）",
          "json" in _body.lower().split('_chat_json')[1][:120],
          "⚠️ 没有 -> 真 key 也会 400")
    check("⭐ 只发一个 `_chat_json`（不多写第二处 httpx）",
          _body.count("_chat_json(") == 1, str(_body.count("_chat_json(")))

    print("\n--- ③d 一人一码：Worker 的拒绝原因要说成人话（不联网，桩掉 `_chat_json`）---")
    import httpx
    import build_notes
    from unittest import mock

    def _deny(status, body):
        def f(*_a, **_k):
            r = httpx.Response(status, json=body, request=httpx.Request("POST", "https://w/v1/chat/completions"))
            r.raise_for_status()
        return f
    for err, status in (("expired", 403), ("revoked", 403), ("devices", 403), ("quota_exceeded", 429)):
        with mock.patch.object(build_notes, "_chat_json", _deny(status, {"error": err})):
            got = KE.validate_deepseek("cl-x-0123456789abcdef")
        check(f"⭐ {status} {err} -> 「{KE.PASSCODE_REASONS[err]}」", got == (False, KE.PASSCODE_REASONS[err]), str(got))
    with mock.patch.object(build_notes, "_chat_json", _deny(401, {"error": {"message": "Authentication Fails"}})):
        got = KE.validate_deepseek("sk-bad")
    check("DeepSeek 自己的 401（error 是对象）-> 照旧「401 —— 检查是不是复制全了」",
          got == (False, "401 —— 检查是不是复制全了"), str(got))

    # 3.9.2：DeepSeek key 和通行码填同一栏，标签就叫「Key」（Owen 2026-10-12 定，不写别的）
    _f = KE.dialog_fields()
    check("⭐ 翻译那一栏标签是「Key」", _f[0]["label"] == "Key", repr(_f[0]))
    check("已存过 → 提示「已存过」", KE.dialog_fields("sk-1", "")[0]["hint"] == "已存过")

    print("\n--- ③e ⭐ 3.9.3 的一次性搬家：把「Key」那一栏清空 ---")
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        cred = root / "credentials"
        cred.write_text("sk-old-key\n", encoding="utf-8")
        cred.chmod(0o600)
        got = KE.clear_legacy_key(root=root)
        bak = root / f"credentials.bak-{KE.CLEAR_VERSION}"
        # 改坏：把 `os.rename(p, dst)` 那一行删掉 -> 这条红。
        check("⭐ 老的 `sk-` key 被挪走（`credentials` 不再存在）",
              not cred.exists(), str(sorted(x.name for x in root.iterdir())))
        check("⭐⭐ **只挪不删**：内容一字不差地躺在 `.bak` 里（用户能自己找回来）",
              _read_or(bak) == "sk-old-key", _read_or(bak))
        check("⭐ 权限跟着走（备份也是 600，不会变成默认 644）",
              oct(bak.stat().st_mode & 0o777) == "0o600", oct(bak.stat().st_mode & 0o777))
        check("⭐ 返回里点名了落点（终端那一行要把它打出来）",
              got["moved"] == [str(bak)], str(got))
        check("⭐ 标记写在这个 root 里", _read_or(PA.key_cleared_flag(root=root)) == KE.CLEAR_VERSION)
    # ⭐ 标记的**默认**位置必须在状态根（`~/.classlive/`）下 —— 安装目录会被 `git pull` 洗掉，
    #    而标记一丢，这次搬家会**再跑一遍**（那时用户早填好新通行码了）。
    #    改坏：`paths.key_cleared_flag` 改成 `Path(__file__).with_name(...)` -> 这条红。
    check("⭐⭐ 标记默认住状态根（`~/.classlive/`），不是安装目录",
          PA.key_cleared_flag().parent == PA.STATE_ROOT,
          f"{PA.key_cleared_flag()} vs {PA.STATE_ROOT}")

    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        (root / "credentials").write_text("sk-first\n", encoding="utf-8")
        KE.clear_legacy_key(root=root)
        (root / "credentials").write_text("sk-second\n", encoding="utf-8")
        KE.clear_legacy_key(root=root)                     # 标记还在 -> 空操作
        # 改坏：去掉开头那个「标记 == 版本」的早返回 -> 这条红。
        check("⭐⭐ 标记在 -> **再也不清**（用户后来自己填的 key 不会被吃掉）",
              _read_or(root / "credentials") == "sk-second", _read_or(root / "credentials"))
    with tempfile.TemporaryDirectory() as d:
        # ⚠️⚠️ 安全网那一档 —— 一个**全新的 root**（= 标记还从没写过：新装的机器、
        #     或者用户从备份里恢复了 `~/.classlive/`）里已经躺着一枚通行码。
        root = pathlib.Path(d)
        code = "cl-fake-0000000000000000"   # ⚠️ 别放真码（R29；2026-10-14 这里曾是真的 test1 码）
        (root / "credentials").write_text(code + "\n", encoding="utf-8")
        KE.clear_legacy_key(root=root)
        # 改坏：去掉 `val.startswith("cl-")` 那个跳过 -> 这条红。
        check("⭐⭐⭐ 没有标记 + 已配好通行码 -> `cl-` **毫发无损**（重跑也不怕）",
              _read_or(root / "credentials") == code, _read_or(root / "credentials"))

    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        (root / "credentials").write_text("sk-x\n", encoding="utf-8")
        a = KE.clear_legacy_key(root=root)
        b = KE.clear_legacy_key(root=root)
        # ⚠️ `first` 是「那次一次性弹窗」的**唯一**判据（`entry_launch` 判 `first and moved`）。
        #    改坏：把 `first` 恒定返 True -> 「第二次启动还弹」这条红。
        check("⭐⭐ 头一回搬家 -> `first=True`（弹窗就靠它只弹一次）",
              a["first"] is True and a["moved"], str(a))
        check("⭐⭐ 第二次 -> `first=False`（不该再弹）", b["first"] is False, str(b))
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        got = KE.clear_legacy_key(root=root)              # 本来就没有 key 可挪
        # ⚠️ 这条挡的是"把判据放宽成看版本号"：新用户什么都没失去，不该被那张框拦一下。
        check("⭐⭐ 没有 key 可挪 -> **不弹**（判据是 `first and moved`，不是「新版本就弹」）",
              not (got["first"] and got["moved"]), str(got))
        # ⚠️ 两句的**分工**（Owen 2026-10-12 定）：
        #    · 弹窗负责"去哪拿"（点名找菠萝丁）；
        #    · Key 那一栏只管"填什么"（`key 放这就好`）—— 同一个 app 里别把"找谁"说两遍。
        check("⭐ 弹窗点名找菠萝丁",
              "找菠萝丁拿 key" in KE.KEY_RESET_MESSAGE, KE.KEY_RESET_MESSAGE)
        # ⚠️ 后半句由 `tests/test_entry_panel.py` 钉着（它来自 2026-10-04 那次假话），
        #    这里只钉「不再重复说找谁拿」。
        check("⭐ Key 那一栏只说「key 放这就好」+ 不填的后果（不再重复「找谁拿」）",
              KE.ASK_AUTHOR == "翻译用的。key 放这就好；不填只有英文字幕。", KE.ASK_AUTHOR)
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        sentinel = root / f"credentials.bak-{KE.CLEAR_VERSION}"
        sentinel.write_text("更早的一次备份\n", encoding="utf-8")
        (root / "credentials").write_text("sk-second-time\n", encoding="utf-8")
        KE.clear_legacy_key(root=root)
        # 改坏：`_next_backup` 改成直接拼名字（= 覆盖）-> 这条红。
        check("⭐⭐ **绝不覆盖已有的备份** —— 退到 `-2`",
              _read_or(sentinel) == "更早的一次备份"
              and _read_or(root / f"credentials.bak-{KE.CLEAR_VERSION}-2") == "sk-second-time",
              _read_or(sentinel))

    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        (root / "credentials").write_text("\n", encoding="utf-8")     # 空文件
        got = KE.clear_legacy_key(root=root)
        check("⭐ 空文件：不建备份、照样把标记做上",
              got["done"] and got["moved"] == []
              and not (root / f"credentials.bak-{KE.CLEAR_VERSION}").exists(), str(got))
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        (root / "credentials").mkdir()                     # 目录冒充文件
        # 改坏：把 `_read_key` 的 `except (OSError, UnicodeDecodeError)` 收窄成只认 Unicode -> 这条红。
        check("⭐ `_read_key` 把「不是文件」也当空的（目录 / 没权限 / 断链）",
              KE._read_key(root / "credentials") == "" and KE._read_key(root / "没有这个") == "")
    with tempfile.TemporaryDirectory() as d:
        # ⚠️ 搬家路上任何意外都**不该拦上课**。这里让 `os.rename` 真炸一次 ——
        #    ⚠️ 必须放一个**真文件**（放目录的话 `_read_key` 早就当空的跳过了，rename 根本到不了，
        #       这条判据就成了空判据 —— 第一版正是这么写的，实测立刻抓到）。
        root = pathlib.Path(d)
        (root / "credentials").write_text("sk-boom\n", encoding="utf-8")
        from unittest import mock as _mk
        try:
            with _mk.patch.object(KE.os, "rename", side_effect=OSError("boom")):
                got = KE.clear_legacy_key(root=root)
        except Exception as e:                             # noqa: BLE001
            got = f"抛了 {type(e).__name__}: {e}"
        # 改坏：把那个兜底 `except Exception: return out` 去掉 -> got 变成字符串 -> 这条红。
        check("⭐⭐ 搬家途中出意外也**不抛**（返回 dict、标记不写 —— 下次启动再试）",
              isinstance(got, dict) and got["done"] is False
              and _read_or(PA.key_cleared_flag(root=root)) == "〈文件不在了〉", str(got))

    print("\n--- ③f ⭐ 3.9.3：`load_api_key` 不再读旧位置那个文件 ---")
    with tempfile.TemporaryDirectory() as d:
        import cloud_translator as CT
        tmp = pathlib.Path(d)
        legacy = tmp / ".deepseek_key"          # 冒充 `<安装目录>/.deepseek_key`
        legacy.write_text("sk-legacy-only\n", encoding="utf-8")
        missing = tmp / "credentials"           # 新位置故意空着
        env = os.environ.pop("DEEPSEEK_API_KEY", None)
        try:
            # ⚠️ **两个读端口都要换掉**：`KEY_FILE` **和** 状态根下的 `credentials`
            #    （后者默认读真 HOME —— 不换的话这条判据在作者机上会读到真 key）。
            with mock.patch.object(CT, "KEY_FILE", legacy), \
                 mock.patch.object(PA, "credentials", lambda **_k: missing):
                # ⚠️ 前提先坐实 —— 否则「返回 None」可能只是因为那个文件根本不在（空判据）。
                check("（前提）旧位置真有文件、真有 key",
                      _read_or(legacy) == "sk-legacy-only")
                got = CT.load_api_key(None)
        finally:
            if env is not None:
                os.environ["DEEPSEEK_API_KEY"] = env
        # 改坏：把旧位置那段 fallback 加回去 -> 这条红（老用户会一直"看起来配好了"）。
        check("⭐⭐ 旧位置有 key 也**不认**了（老用户会被逼到「找作者拿 Key」）",
              got is None, repr(got))

    print("\n--- ③g 接线：两条入口都真的调了，而且顺序对 ---")
    # ⚠️ 这条判据很窄（只看调用点）—— 故意的：它防的正是这次改动的**唯一**现实失效形态
    #    「函数写好了但没人调」。
    _pkg = pathlib.Path(KE.__file__).resolve().parent
    for _f, _why in (("main.py", "`cl` / 开始上课那条路"),
                     ("entry_launch.py", "双击 .app 开面板那条路")):
        # ⚠️ 认的是**调用**，不认别名写法（`entry_launch` 用的是 `import keyentry as _ke`，
        #    第一版钉死 `keyentry.clear_legacy_key()` 就把自己的重构判红了 —— 判据要指向
        #    「有没有调」这件事本身）。
        check(f"⭐ `{_f}` 调了 `clear_legacy_key()`（{_why}）",
              "clear_legacy_key()" in (_pkg / _f).read_text(encoding="utf-8"))
    # ⚠️ 光是"调了"还不够 —— `entry_launch` 那处若排在 `open_panel(` **后面**，
    #    上面那条照样绿，可就绪条会拿搬家**之前**的状态去画（一口咬定"配好了"）。
    _src = (_pkg / "entry_launch.py").read_text(encoding="utf-8")
    check("⭐⭐ `entry_launch` 里搬家排在 `entry_panel.open_panel(` **之前**",
          _src.index("clear_legacy_key()") < _src.index("entry_panel.open_panel("))

    print("\n--- ④ 那张框的几何（纯函数）---")
    check("0 栏 -> 只有内边距", notice.text_alert_height(0) == notice.TEXT_ALERT_PAD * 2,
          str(notice.text_alert_height(0)))
    check("⭐ 2 栏比 1 栏正好高一栏",
          notice.text_alert_height(2) - notice.text_alert_height(1)
          == notice.TEXT_ALERT_ROW)
    check("负数不会算出负高度", notice.text_alert_height(-3) == notice.TEXT_ALERT_PAD * 2)

    print("\n--- ⑤ ⚠️ `layout()` 真的把附件视图装进去了 ---")
    try:
        _case_appkit()
    except ImportError:
        print("  （没有 AppKit，跳过 ⑤）")
    except Exception as e:                                    # noqa: BLE001
        # ⚠️ 同 `test_live_summary` 那条教训：**一条判据自己抛了不许把整个文件带崩**
        check("⚠️ ⑤ 那一组自己抛了（**后面的没跑**）", False, f"{type(e).__name__}: {e}")

    # ⚠️ `bad` 必须**就在结算这一处**算 —— `test_entry_panel.py` 栽过一次：
    #    它算在中间，加在它后面的判据**只打印、不进统计、失败也不影响退出码**。
    bad = [n for n, ok, _ in RESULTS if not ok]
    print("\n" + "=" * 60)
    print(f"{len(RESULTS) - len(bad)}/{len(RESULTS)} 通过")
    for n in bad:
        print(f"  ❌ {n}")
    return 1 if bad else 0


def _case_appkit() -> None:
    """⑤ 那一组 —— **单独一个函数**，好让 `main()` 在外面加一层兜底。

    ⚠️ 判据必须查**视图树**，不能查 `accessoryView()` 那个 getter ——
       不调 `layout()` 时它照样返回对象，只是**没进视图树**（"看起来设过了"）。
    """
    import objc                                                    # noqa: F401
    from AppKit import NSApplication
    NSApplication.sharedApplication()
    a, boxes = notice.build_text_alert(
        "T", "M", [{"key": "k", "label": "L", "hint": "H"}], ("保存", "以后再说"))
    w = a.window()
    acc = a.accessoryView()
    check("附件视图建出来了且尺寸非零",
          acc is not None and acc.frame().size.width > 0,
          f"{acc.frame().size if acc else None}")

    def _in_tree(v, target, d=0):
        for s in v.subviews():
            if s is target:
                return True
            if d < 4 and _in_tree(s, target, d + 1):
                return True
        return False

    inside = acc is not None and _in_tree(w.contentView(), acc)
    check("⭐⭐ 它在**窗口视图树里**（`layout()` 生效）", inside,
          "" if inside else "⚠️ 不在 —— `build_text_alert` 里那句 `a.layout()` 没跑到")
    check("⭐ 窗口够宽装得下附件（不是那个 260 宽的退化形态）",
          w.frame().size.width >= notice.TEXT_ALERT_W,
          f"窗口 {w.frame().size.width} vs 附件 {notice.TEXT_ALERT_W}")
    check("按钮两个，且**添加顺序是「保存」在前**（= 排在最右 = 主按钮）",
          [b.title() for b in a.buttons()] == ["保存", "以后再说"],
          str([b.title() for b in a.buttons()]))
    # ⚠️ 上面那次是**显式传的**按钮，测不到默认值 —— 而默认值才是 `entry_panel`
    #    真正会用到的那一个（它不传 `buttons`）。要断言**签名本身**。
    #    第一版只测了显式传参 → 把默认值改回 ("以后再说","保存") **判据照样全绿**。
    import inspect
    _dflt = inspect.signature(notice.ask_text).parameters["buttons"].default
    check("⭐⭐ `ask_text` 的**默认**按钮顺序也是「保存」在最前（主操作在右）",
          tuple(_dflt) == ("保存", "以后再说"), str(_dflt))

    # ⚠️ 判重叠**必须同时判 x 和 y** —— 只判 y 会把「标签和输入框并排」误报成重叠
    #    （2026-09-30 我自己写过一版只判 y 的检查器，报了 20pt 的假重叠）。
    rows = [v for v in acc.subviews() if hasattr(v, "stringValue")]
    bad = []
    for i, v in enumerate(rows):
        for u in rows[i + 1:]:
            a1, a2 = v.frame(), u.frame()
            if (a1.origin.x < a2.origin.x + a2.size.width
                    and a2.origin.x < a1.origin.x + a1.size.width
                    and a1.origin.y < a2.origin.y + a2.size.height
                    and a2.origin.y < a1.origin.y + a1.size.height):
                bad.append((v.stringValue()[:14], u.stringValue()[:14]))
    check("⭐⭐ 子视图**两两不重叠**", not bad, str(bad))

    # ── ⭐ 回填：`value` 给了就预填进安全框（重开不再空白）───────────────
    # 动机（2026-10-01 作者实测）：「我填了 API 然后退出、再点进去它又变空白了」——
    # 空白看不出"存过没有"；改成回填（安全框里 = 圆点，不是明文）。
    a2, boxes2 = notice.build_text_alert(
        "T", "M", [{"key": "k", "label": "L", "value": "sekret-42",
                    "hint": "H"}], ("保存", "以后再说"))
    check("⭐⭐ `value` 回填进输入框（重开不空白）",
          boxes2["k"].stringValue() == "sekret-42",
          repr(boxes2["k"].stringValue()))
    check("⚠️ 回填的框仍是**安全框**（圆点显示，不是明文控件）",
          "Secure" in type(boxes2["k"]).__name__, type(boxes2["k"]).__name__)

    # ── ⭐ 编辑菜单：没它 ⌘V 在**所有**输入框里都是死的 ──────────────────
    # 动机（2026-10-01 作者实测）：右键 Paste 能粘、**⌘V 完全没反应**；
    # 根因是 app 从不建 mainMenu —— 文本框的快捷键靠菜单栏键等价分发。
    import panel as P
    ok1 = P.install_edit_menu()
    _mm = NSApplication.sharedApplication().mainMenu()
    _sub = (_mm.itemAtIndex_(0).submenu()
            if _mm is not None and _mm.itemAtIndex_(0) is not None else None)
    _items = list(_sub.itemArray()) if _sub is not None else []
    _pastes = [it for it in _items if it.keyEquivalent() == "v"]
    check("⭐⭐ 装上了 Edit 菜单，且 ⌘V 接的是 `paste:`",
          ok1 and len(_pastes) == 1 and "paste" in str(_pastes[0].action()),
          f"项数={len(_items)} paste={[_pastes[0].action()] if _pastes else None}")
    check("⭐ ⌘C / ⌘A 也在（拷贝与全选）",
          {"c", "a", "x", "z"} <= {it.keyEquivalent() for it in _items},
          str([it.keyEquivalent() for it in _items]))
    check("⭐ 幂等：再装一次不重复、不炸", P.install_edit_menu() is True)


if __name__ == "__main__":
    sys.exit(main())
