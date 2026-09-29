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


@case("⭐⭐ 没有库时 ObsidianWriter 整个关掉（这才是「不写」而不是「写错地方」）")
def t_writer_disabled_without_vault():
    w = ow.ObsidianWriter(None, "ECON10740", mode="ask")
    assert w.enabled is False, "没有库却仍然 enabled —— 接下来就会 mkdir 一个凭空目录"


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
