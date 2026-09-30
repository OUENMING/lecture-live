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

import json
import pathlib
import shutil
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


@case("⭐⭐ 会话文件尾巴被截断（半个汉字）→ 笔记照样写得出来")
def t_torn_session_tail():
    """⭐ 2026-09-30 全量 OCR 审查发现 → 已修（`close()` 那处读加 `errors="replace"`）。

    ⚠️ 触发条件是真的：写到一半崩溃/强杀时，末行的**多字节汉字可能只剩半个**。
       改之前这里严格 utf-8 解码 → `UnicodeDecodeError` → **整份笔记写不出来**，
       而 `rebuild_note.py`（补笔记那条路）走的正是同一个 `close()`
       → **连补救路一起断**。

    ⚠️⚠️ **第一版这条是假绿**（记一笔）：夹具只断言「`close()` 不抛」，而
       **没给库**（`vault=None`）时 `close()` 会**提前返回**「没设 Obsidian 库，
       这次不生成笔记」—— 根本没走到那行读。变异验证（删掉 `errors=`）没变红
       才发现。→ 现在给一个**真库**，断言**笔记文件真的落了盘**。
    """
    with tempfile.TemporaryDirectory() as d:
        old = ow.SESSIONS
        ow.SESSIONS = pathlib.Path(d) / "sessions"
        try:
            w = ow.ObsidianWriter(d, "TESTX", mode="ask")      # ⚠️ 给了库才会真建笔记
            w.append("Hello there.", "你好。", raw="Hello there.")
            with open(w.session_path, "ab") as f:
                f.write(b"\n> **ZH**: \xe4\xb8")    # 「中」的半个字节
            # 先自证夹具真的坏了 —— 否则这条测的是别的
            with open(w.session_path, "rb") as f:
                raw = f.read()
            try:
                raw.decode("utf-8")
                raise AssertionError("夹具没截断 —— 这条判据没测到东西")
            except UnicodeDecodeError:
                pass
            w.close(ask=lambda n: True)
            notes = list((pathlib.Path(d) / "Lectures").glob("*.md"))
            assert notes, (
                "尾巴被截断就把**整份笔记**带下水了（这正是那条缺陷："
                "`close()` 里那次读会抛 UnicodeDecodeError，"
                "连 `rebuild_note.py` 一起断）")
        finally:
            ow.SESSIONS = old


@case("paths.vault_config 是那条路径的唯一定义点")
def t_paths_owns_it():
    r = _root()
    assert paths.vault_config(root=r) == r / "vault"
    # 记住的那个文件就是它 —— 两处不许各写一份路径
    ow.remember_vault("~/X", root=r)
    assert paths.vault_config(root=r).exists()


@case("📑 课堂纲要那一节（§10.2）：空跳过 / 临时章不算 / 排法① / 退回原子要点")
def _case_outline_section():
    """📑 课堂纲要那一节（计划 §10.2）。**纯函数**，不碰文件、不联网。

    ⚠️ 钉住四条：空 -> **整节跳过**（不是空标题）· 临时章**不许出现** ·
    排法①（中文当正文）· 没有合成句的章**退回原子要点**。
    """
    import obsidian_writer as OW
    assert OW._render_outline_section({}) == [], "空输入该返回空表（整节跳过）"
    assert OW._render_outline_section({"windows": [1]}) == [], "只有窗口也该跳过"
    assert OW._render_outline_section(
        {"chapters": [{"status": "interim", "title": "临时"}]}) == [], "临时章不算数"
    sec = OW._render_outline_section({
        "deadlines": [{"quote": "Due next Friday.", "t": "15:40:16",
                       "source": "regex/model", "changed": True}],
        "chapters": [
            {"id": 0, "status": "final", "title": "From GDP to GNI", "title_zh": "从GDP到GNI",
             "t0": "15:05:27", "t1": "15:16:35", "lo": 1, "hi": 9,
             "sentences": [{"en": "EN sentence.", "zh": "中文译文。",
                            "terms": [["income method", "收入法"]],
                            "src": [2], "flag": "board"}]},
            {"id": 1, "status": "final", "title": "Prices", "title_zh": "价格",
             "t0": "15:20", "t1": "15:30", "lo": 10, "hi": 20, "sentences": []}],
        "atoms": [{"text": "Prices are key.", "zh": "价格是关键。", "src": [12]}]})
    txt = "\n".join(sec)
    assert "待确认" in txt and "已改期" in txt, "课务那条的两个标记都该在"
    # ⚠️⚠️ **必须钉「那一行本身」** —— 第一版只查 `"EN sentence." in txt`，
    #    而**英文在小字那行也还在** → 把大字换成英文（排法① 失效）**判据照样绿**
    #    （变异验证抓到的）。判据要指向**那个位置**，不是"整个文本里有没有"。
    assert "- **中文译文。**" in txt, "排法①：合成句的**大字**必须是中文"
    assert "- **EN sentence.**" not in txt, "排法①：英文**不许**当大字（它该降小字）"
    assert "EN sentence." in txt, "⚠️ 但英文原句**不许丢**（它该在小字那行）"
    assert "### 从GDP到GNI（" in txt, "章标题也是排法①（中文当标题，英文进小字）"
    assert "收入法" in txt, "术语对照要带上"
    assert "价格是关键。" in txt, "没有合成句的章要退回**中文**原子要点"
    # ⭐⭐ 原子的小字里要有**英文原句** —— 同一个坑（2026-09-30 作者实测反馈：
    #    「AI 总结现在只有中文，英文完全没有了」）。笔记这边**更彻底**：
    #    原来连小字行都没有，整条原子只剩一行中文。
    #    改坏：把 `_pair_md` 返回的第二行去掉 -> 这条红。
    assert "  · Prices are key." in txt, "原子要点也要英文降小字（不许只剩中文）"
    assert "临时" not in txt, "临时章不该出现"
    # 改坏：把 `_render_outline_section` 里的 `if not ds and not chs: return []` 删掉
    #       -> 第一条断言红。
    # 改坏：把大字的 `zh or en` 换成 `en` -> 「排法①」那条红。


@case("📑 纲要那一节的**文件级**接线（§10.2 / §11.2）：真读 `.chapters.jsonl` + 落在概览与知识点之间")
def _case_outline_wiring():
    """⭐ 计划 §11.2 阶段 5 逐字要求：「用一份**固定的 `.chapters.jsonl`** 当输入」。

    ⚠️ 上面那条 `_case_outline_section` 喂的是**内存字典** —— 纯函数那半覆盖住了，
       但另**两条腿没有判据**（2026-09-30 审计发现，计划 §11.2 与实际不符）：
         · `_chapter_items()` 的**真读盘**（`chapter.load` + `atom.load` 那两个旁路文件）
         · 那一节在 `_render_note` 里的**位置**（§10.2：「紧跟在概览之后、知识点之前」）
    ⚠️ 变异验证（做实了）：
       · 把 `_render_note` 那段 `_sec` 挪到 `## 📚 Key Concepts` **之后** → 位置那条红
       · 把 `_chapter_items` 里 `chapter.load(...)` 换成常量 → 「真读回来了」那条红
    """
    import atom as _atom
    import chapter as _ch

    d = pathlib.Path(tempfile.mkdtemp(prefix="cl_vault_outline_"))
    try:
        sess = d / "2026-09-30_150000_ECON99999.md"
        sess.write_text("# x\n", encoding="utf-8")
        (d / "2026-09-30_150000_ECON99999.chapters.jsonl").write_text(
            "\n".join(json.dumps(o, ensure_ascii=False) for o in [
                {"type": "window", "w": 1, "t": "15:01:00", "topic": "T",
                 "lo": 1, "hi": 9, "n_points": 1},
                # ⚠️ 故意先写一条**临时版**再写正式版：同一个 `id`
                #    —— `load()` 必须只留**最后一条**。
                {"type": "chapter", "id": 0, "status": "interim",
                 "title": "旧标题", "sentences": []},
                {"type": "chapter", "id": 0, "status": "final",
                 "title": "From GDP to GNI", "title_zh": "从GDP到GNI",
                 "t0": "15:00:10", "t1": "15:10:00", "lo": 1, "hi": 9,
                 "sentences": [{"en": "EN one.", "zh": "中文一。", "terms": [],
                                "src": [2], "flag": None}]},
                {"type": "deadline", "t": "15:12:00", "quote": "Due Friday.",
                 "src": [7], "source": "regex", "changed": False},
            ]) + "\n", encoding="utf-8")
        (d / "2026-09-30_150000_ECON99999.atoms.jsonl").write_text(
            json.dumps({"id": 0, "t": "15:01:00", "epoch": 1.0, "src": [3],
                        "kind": "要点", "text": "原子要点", "terms": [], "zh": ""},
                       ensure_ascii=False) + "\n", encoding="utf-8")

        w = ow.ObsidianWriter(None, "TESTX", mode="no")     # ⚠️ mode=no：不写会话文件
        w.session_path = sess
        items = w._chapter_items()
        assert len(items["chapters"]) == 1, f"同 id 该只剩一条，拿到 {len(items['chapters'])}"
        assert items["chapters"][0]["status"] == "final", "正式版要顶掉临时版"
        assert items["chapters"][0]["title"] == "From GDP to GNI"
        assert items["deadlines"] and items["atoms"], "课务与原子都要真读回来"
        assert items["windows"], "窗口流水也要（写回复查用）"

        entries = [{"ts": "15:00:10", "en": "a", "zh": "b", "asr": "", "star": False}]
        out = w._render_note(entries, {}, [], "ok", [], None, items)
        i_ov = out.index("## 🎯 Overview 概览")
        i_oc = out.index("## 📑 课堂纲要")
        i_kc = out.index("## 📚 Key Concepts 知识点详解")
        assert i_ov < i_oc < i_kc, f"位置错了：概览@{i_ov} 纲要@{i_oc} 知识点@{i_kc}"
        assert "### Deadlines · 课务" in out
        assert "### 从GDP到GNI（" in out
        assert "中文一。" in out

        # ⭐ 边界：没有 `.chapters.jsonl` -> 整节跳过、**笔记照常生成**（旧会话 / 纯转录档）
        w2 = ow.ObsidianWriter(None, "TESTX", mode="no")
        w2.session_path = d / "never-existed.md"
        assert w2._chapter_items()["chapters"] == []
        out2 = w2._render_note(entries, {}, [], "ok", [],
                               None, w2._chapter_items())
        assert "课堂纲要" not in out2, "没有旁路文件时那一节必须整节消失"
        assert "## 🎯 Overview 概览" in out2 and "## 📚 Key Concepts 知识点详解" in out2, \
            "但笔记其余部分照常"
    finally:
        shutil.rmtree(d, ignore_errors=True)


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
