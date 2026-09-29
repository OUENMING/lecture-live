#!/usr/bin/env python3
"""`obsidian_writer.resolve_vault` / `remember_vault` 的判据 —— 「笔记写到哪个库」。

    ClassLive.app/Contents/MacOS/python tests/test_vault.py

## 这个文件钉住了什么

1. ⭐⭐ **没有库就返回 `None`，绝不凭空造一个目录。**
   这条是一个真 bug 的回归闸：以前 `cl` 写的是
   `${OBSIDIAN_VAULT:-$HOME/Obsidian/Vault}`，而**双击 `.app` 起的那条路没有 shell 环境**
   （Finder 给的是 launchd 的环境；实测 `launchctl getenv OBSIDIAN_VAULT` 是空的）
   → 笔记被写进 `~/Obsidian/Vault/`。那个目录**连 `.obsidian` 都没有**，不是 vault，
   实测里面躺着 9 个笔记。用户永远找不到它们。
2. ⭐ **用户选过一次就记得住** —— 这是修那个 bug 的机制本身：双击启动没有环境变量，
   只有记在 `~/.classlive/vault` 里才找得到。
3. **优先级**：`--vault` > `$OBSIDIAN_VAULT` > 记住的 > `None`。
4. ⚠️ **`DEFAULT_VAULT` 那个常量不许回来** —— 它就是这个 bug 的形态。

⚠️ 语料全是 `tempfile`，**不碰真实的 `~/.classlive/`、也不碰任何真 vault**。
⚠️ 每条都问过「把实现改回坏版本，它会不会红」—— 结果记在各自的注释里。
"""
from __future__ import annotations

import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import obsidian_writer as ow                                        # noqa: E402
import paths                                                        # noqa: E402

CASES: list[tuple[str, object]] = []
FAIL: list[str] = []


def case(name: str):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


def _root() -> pathlib.Path:
    """一个空的 `~/.classlive/` 替身。"""
    return pathlib.Path(tempfile.mkdtemp())


@case("⭐ 什么都没有 → None（不凭空造目录）")
def t_nothing_is_none():
    # 变异验证（实跑过）：把 resolve_vault 最后一条分支改回
    # `return os.path.expanduser("~/Obsidian/Vault")` → 这条立刻红。**这就是那个 bug。**
    got = ow.resolve_vault(env={}, root=_root())
    assert got is None, f"没有库时不该给出路径，给的是 {got!r}"


@case("环境变量给的就用它")
def t_env():
    got = ow.resolve_vault(env={"OBSIDIAN_VAULT": "~/SomeVault"}, root=_root())
    assert got == str(pathlib.Path.home() / "SomeVault"), got


@case("`--vault` 赢过环境变量")
def t_cli_wins():
    got = ow.resolve_vault("/tmp/Explicit", env={"OBSIDIAN_VAULT": "~/Env"}, root=_root())
    assert got == "/tmp/Explicit", got


@case("⭐ 记住的库找得回来（双击启动那条路靠它）")
def t_remembered():
    r = _root()
    ow.remember_vault("~/Chosen", root=r)
    got = ow.resolve_vault(env={}, root=r)
    assert got == str(pathlib.Path.home() / "Chosen"), got


@case("环境变量赢过记住的（用户显式导出是更强的意图）")
def t_env_beats_remembered():
    r = _root()
    ow.remember_vault("~/Chosen", root=r)
    got = ow.resolve_vault(env={"OBSIDIAN_VAULT": "~/Env"}, root=r)
    assert got == str(pathlib.Path.home() / "Env"), got


@case("⚠️ 空白文件不许变成一个空路径")
def t_blank_file():
    # 变异验证：把 `.strip()` 去掉 → 空行会变成 `"\n"`，expanduser 后是个**真值**
    # → 于是又一个凭空目录。这条会红。
    r = _root()
    ow.remember_vault("   ", root=r)
    got = ow.resolve_vault(env={}, root=r)
    assert got is None, f"空白不该给出路径，给的是 {got!r}"


@case("文件不存在 → None（全新安装，不是错）")
def t_missing_file():
    assert ow.resolve_vault(env={}, root=_root()) is None


@case("⚠️ `DEFAULT_VAULT` 常量不许回来")
def t_no_default_vault():
    # 它不是「一个默认值」，它是这个 bug 的形态 —— 谁把它加回来，这条就红。
    assert not hasattr(ow, "DEFAULT_VAULT"), \
        "DEFAULT_VAULT 回来了 —— 那意味着又有一个「用户没选过」的兜底目录"


@case("⭐⭐ 没有库时,**逐句底稿照写**（`sessions/` 那一层不许跟着一起没）")
def t_sessions_still_written_without_vault():
    """⭐⭐ 2026-09-29 修的真缺陷 —— 而**上一条判据原本正钉着它**。

    原来 `enabled = bool(vault) and mode != "no"`，一个标志扛两件事：
    「要不要记录」和「要不要写 Obsidian 笔记」。于是没有库时
    `session_path is None` → `append()` 首行就 return → **整节课一个字都不落盘**。

    ⚠️ 这**绕过了本模块的立身之本**「先落盘, 再询问」（文件头），
       而命令行那句提示还写着"这次只写 sessions/" —— 假话。
    ⚠️ 触发它不需要意外：全新安装、或双击 `.app`（没有 `$OBSIDIAN_VAULT`、
       也还没记住过库）。那正是**最需要底稿**的人 —— 将来"打包成 MD/PDF"
       导出的也就是这一层。

    ⚠️ 上一条判据（`t_writer_disabled_without_vault`）当时的理由是
       「没有库却仍然 enabled —— 接下来就会 mkdir 一个凭空目录」。
       **那理由只对了一半**：会凭空造目录的是 Obsidian 那一半（`note_path_for`），
       不是 `sessions/`（`SESSIONS` 是仓库自己那个目录，与用户选的库无关）。
       判据把两件事当成一件 → 保护了后半、牺牲了前半。**判据钉住的正是缺陷本身。**
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        old = ow.SESSIONS
        ow.SESSIONS = pathlib.Path(d)          # 隔离：别往真 sessions/ 写
        try:
            w = ow.ObsidianWriter(None, "ECON10740", mode="ask")   # 没有库
            assert w.session_path is not None, \
                "没有库时 session_path 是 None —— 逐句底稿会整节丢掉"
            assert pathlib.Path(w.session_path).exists(), "会话文件没建起来"
            w.append("hello world", "你好世界", raw="hello world")
            assert w.count == 1, "append 被守卫挡掉了 —— 这一句没落盘"
            txt = pathlib.Path(w.session_path).read_text(encoding="utf-8")
            assert "hello world" in txt, "落盘的内容里没有那一句"
            # 收尾不许写笔记（没地方写），但**不许抛**，而且要说实话
            msg = w.close()
            assert "已记录" in msg and "没设 Obsidian 库" in msg, \
                f"收尾那句话没说实话：{msg!r}"
            assert w.vault_path is None, "没有库却设了 vault_path"
        finally:
            ow.SESSIONS = old


@case("⚠️ `--save-notes no` 仍然整个关掉（拆 `enabled` 不许把这个也放了）")
def t_save_notes_no_still_disables():
    """拆 `enabled` 时最容易顺手放走的一条 —— `mode="no"` 必须照旧什么都不写。

    ⚠️ 这条是上一条的**对价**：修「没库也要写」的时候，不许把
       「用户明确说了不写」也一起放了。
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        old = ow.SESSIONS
        ow.SESSIONS = pathlib.Path(d)
        try:
            for vault in (None, d):                 # 有没有库都不许写
                w = ow.ObsidianWriter(vault, "TESTX", mode="no")
                assert w.enabled is False, f"vault={vault!r} 时 mode=no 却没关掉"
                assert w.session_path is None
                w.append("x", "y")
                assert w.count == 0, "mode=no 却记录了内容"
            assert not list(pathlib.Path(d).glob("*.md")), "mode=no 却建了会话文件"
        finally:
            ow.SESSIONS = old


@case("有库时笔记真的落在那库里（端到端那条链）")
def t_note_lands_in_vault():
    with tempfile.TemporaryDirectory() as d:
        p = ow.note_path_for(d, "2026-09-29", "ECON10740")
        assert p.parent == pathlib.Path(d) / "Lectures", p
        assert p.name.startswith("2026-09-29_ECON10740"), p


@case("paths.vault_config 是那条路径的唯一定义点")
def t_paths_owns_it():
    r = _root()
    assert paths.vault_config(root=r) == r / "vault"
    # 记住的那个文件就是它 —— 两处不许各写一份路径
    ow.remember_vault("~/X", root=r)
    assert paths.vault_config(root=r).exists()


def main_() -> int:
    print("=" * 60)
    for name, fn in CASES:
        try:
            fn()
        except AssertionError as e:
            print(f"  ❌ {name}\n      {str(e)[:220]}")
            FAIL.append(name)
        except Exception as e:                                 # noqa: BLE001
            print(f"  ❌ {name}\n      {type(e).__name__}: {str(e)[:200]}")
            FAIL.append(name)
        else:
            print(f"  ✅ {name}")
    print("=" * 60)
    print(f"{len(CASES) - len(FAIL)}/{len(CASES)} 通过")
    for n in FAIL:
        print(f"  ❌ {n}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main_())
