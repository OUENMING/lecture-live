#!/usr/bin/env python3
"""`atom.py` 的判据。

    ClassLive.app/Contents/MacOS/python tests/test_atom.py

## 这个文件钉住了什么

1. ⭐ **机械闸门**：`src` 越界**整条丢**（不是修剪 —— 修剪等于**替模型编引用**）
2. ⭐ **「没什么可记」是合法输出** —— 空 `points` 就是空，不许凑
3. ⭐ **`kind` 不在枚举里 fail-open 到「要点」** —— 宁可归错档，不可静默丢一条
4. ⭐⭐ **`KINDS` 里不许出现 `明示强调`** —— 2026-09-28 消歧的结果：
   那个名字被 `PLAN-panel-ux.md §9` 的「明示用语」（**字符串**、可机械核验）
   占着，而这里的 `讲者强调` 是模型的**语义**判断。实测那 24 条里只有 2 条真是考试明示。
   **改回旧名 = 两个概念又混一起。**
5. ⭐ **`SYS` 的 kind 枚举必须与 `KINDS` 逐字一致** —— 那是"一条纪律两处定义"的经典形状
6. **旁路写入器**：写进去读得回来 · `close()` 幂等 · 坏行跳过（系统边界）

⚠️ 语料全是合成的（`tempfile`），不碰 `sessions/` 一个字节。
⚠️ 每条断言先问「把实现改坏它会不会红」—— 恒真的断言不如不写。
"""
from __future__ import annotations

import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import atom                                                          # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  {detail}" if detail else ""))


def main() -> int:
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="cl_atom_"))
    try:
        print("\n--- ① 机械闸门 traceable ---")
        check("窗口内 -> 可追溯", atom.traceable([{"src": [1]}], 0, 3) == (1, 1))
        check("越界 -> 不可追溯", atom.traceable([{"src": [9]}], 0, 3) == (0, 1))
        check("空 src / 缺字段 / 非整数 -> 都不可追溯",
              atom.traceable([{"src": []}, {"text": "x"}, {"src": ["1"]}], 0, 3) == (0, 3))

        print("\n--- ② parse_reply：越界整条丢（不修剪）---")
        got = atom.parse_reply({"points": [
            {"text": "好的那条", "kind": "要点", "src": [1]},
            {"text": "引用不实的那条", "kind": "要点", "src": [99]},
        ]}, 3)
        check("⭐ 越界那条被**整条**丢掉", len(got) == 1 and got[0].text == "好的那条",
              str([a.text for a in got]))
        check("⚠️ 不是修剪成窗口内最近的那句（那是替模型编引用）",
              all(max(a.src) < 3 for a in got), str([a.src for a in got]))

        print("\n--- ③ 「没什么可记」是合法输出 ---")
        check("空 points -> 空列表", atom.parse_reply({"points": []}, 3) == [])
        check("topic 空 + points 空 -> 也是空", atom.parse_reply(
            {"topic": "", "points": []}, 3) == [])
        check("非 dict -> 空", atom.parse_reply(None, 3) == [])

        print("\n--- ④ kind fail-open 与文本闸门 ---")
        got = atom.parse_reply({"points": [
            {"text": "怪档位", "kind": "瞎写的", "src": [0]},
            {"text": "", "kind": "要点", "src": [0]},
            {"text": "x" * 500, "kind": "要点", "src": [0]},
        ]}, 3)
        check("⭐ 不认识的 kind -> 归到「要点」（fail-open，不丢）",
              len(got) == 1 and got[0].kind == "要点", str([a.kind for a in got]))
        check("空 text / 超长 text 丢掉", len(got) == 1)

        many = atom.parse_reply({"points": [
            {"text": f"第{i}条", "kind": "要点", "src": [i % 3]} for i in range(12)]}, 3)
        check(f"最多 {atom.MAX_POINTS} 条", len(many) == atom.MAX_POINTS, str(len(many)))

        print("\n--- ⑤ ⭐⭐ 消歧：KINDS 里**不许**有「明示强调」---")
        check("⭐ 有「讲者强调」", "讲者强调" in atom.KINDS, str(atom.KINDS))
        check("⭐⭐ **没有**「明示强调」——改回旧名 = 两个概念又混一起",
              "明示强调" not in atom.KINDS, str(atom.KINDS))
        # ⭐ SYS 的枚举是 kind 的**第二处**写法，必须与 KINDS 逐字一致
        # ⚠️⚠️ **只查枚举那一行**，不能 `k not in atom.SYS` ——
        #    `讲者强调` 在 SYS 的**说明文字**里也出现（"- `讲者强调` 只标…"），
        #    于是"枚举里漏了它"这种漂移**查不出来**。2026-09-28 变异验证 M3 抓到的。
        enum_line = next((ln for ln in atom.SYS.splitlines() if '"kind"' in ln), "")
        check("枚举那一行确实找到了（否则下面那条是空的）", "主题" in enum_line, enum_line[:60])
        missing = [k for k in atom.KINDS if k not in enum_line]
        check("⭐ SYS 枚举行与 KINDS 一致（少一个就红）", not missing, str(missing))
        check("SYS 里也没有旧的「明示强调」", "明示强调" not in atom.SYS)

        print("\n--- ⑥ 旁路写入器 ---")
        sess = tmp / "2026-09-28_140806_ECON10740.md"
        sess.write_text("# ECON10740\n", encoding="utf-8")
        w = atom.AtomWriter(sess)
        check("路径形状 = sessions/<同名>.atoms.jsonl",
              w.path and w.path.name == "2026-09-28_140806_ECON10740.atoms.jsonl",
              str(getattr(w, "path", None)))
        n = w.append([atom.Atom(1, "14:08:06", 1.5, [0, 2], "讲者强调", "测试条目")])
        check("写入返回条数", n == 1 and w.count == 1)
        # ⭐ 没 flush 的话这里读不到（8KB 缓冲那个坑）
        back = atom.load(w.path)
        check("⭐ 立刻读得回来（证明 flush 了）",
              len(back) == 1 and back[0]["text"] == "测试条目", str(back))
        check("字段齐全（id/t/epoch/src/kind/text/terms）",
              set(back[0]) == {"id", "t", "epoch", "src", "kind", "text", "terms"},
              str(sorted(back[0])))
        w.close()
        w.close()                                    # ⚠️ 幂等
        check("close() 幂等（调两次不炸）", True)
        check("会话 .md **一个字节没动**",
              sess.read_text(encoding="utf-8") == "# ECON10740\n")
        w2 = atom.AtomWriter(sess)
        w2.append([atom.Atom(2, "14:09:00", 2.5, [1], "要点", "第二条")])
        w2.close()
        check("再追加是**只增不改**（旧的还在）", len(atom.load(w.path)) == 2)

        bad = tmp / "bad.atoms.jsonl"
        bad.write_text('{"text": "好的"}\n这不是 json\n\n{"text": ""}\n', encoding="utf-8")
        check("坏行跳过、空 text 跳过（系统边界要宽容）",
              len(atom.load(bad)) == 1, str(atom.load(bad)))

        print("\n--- ⑦ 没会话路径时不炸 ---")
        w3 = atom.AtomWriter(None)
        check("session_path=None -> path 是 None、append 返回 0",
              w3.path is None and w3.append([atom.Atom(0, "0", 0, [0], "要点", "x")]) == 0)
        w3.close()

        print("\n--- ⑧ ⭐ 两条**接线**判据（静态 + 早退路径）---")
        # ① `raw` 档合同：`DESIGN.md:187` 逐字「纯转录 | 一个模型请求都不发」。
        #    ⚠️ 这条**运行时测不到**（worker 长在 `run()` 里），所以读源码钉。
        #    ⚠️ 它只能证明"那行写了这个判断"，证明不了判断时机对 ——
        #       时机那半由注释钉（**取件时**判，因为三档课中可切）。
        src_main = (HERE / "main.py").read_text(encoding="utf-8")
        lines = src_main.splitlines()
        # ⚠️⚠️ **只看 `put_nowait` 附近那几行**，不能 `'!= "raw"' in src_main` ——
        #    `raw` 档在 main.py 别处也判（`:1009` 那支）→ "把 drain 里那道闸删掉"
        #    **查不出来**。2026-09-28 变异 M6 抓到的，**同一类错这是第三次**：
        #    判据要指向**那个位置**，不要指向"整个文件里有没有"。
        put_idx = [i for i, ln in enumerate(lines) if "atomq.put_nowait" in ln]
        check("找得到 `atomq.put_nowait` 那一行（否则下面那条是空的）", bool(put_idx),
              str(len(put_idx)))
        gate_ok = False
        for i in put_idx:
            window = "\n".join(lines[max(0, i - 6):i])
            if '!= "raw"' in window:
                gate_ok = True
        check('⭐ `put_nowait` **上面几行**有 `trans["mode"] != "raw"` 那道闸', gate_ok)
        check("⭐ 用的是 `put_nowait` 而不是 `put`（drain 在主循环里，不能阻塞）",
              bool(put_idx) and all("put_nowait" in lines[i] for i in put_idx))

        # ② ⚠️ 复刻 ❓ 那次**真事故**的形状：`close()` 有三条早退，
        #    旁路句柄若放在它们**之后**，答"不保存笔记"那条路上就漏关。
        import obsidian_writer as ow
        tmp2 = pathlib.Path(tempfile.mkdtemp())
        try:
            w = ow.ObsidianWriter(str(tmp2), "ECON10740", mode="no")   # mode=no -> 早退
            w.session_path = tmp2 / "sessions" / "x.md"
            w.session_path.parent.mkdir(parents=True)
            w.session_path.write_text("# x\n", encoding="utf-8")
            w.append_atoms([atom.Atom(0, "t", 0.0, [1], "要点", "条目")])
            handle_before = getattr(w, "_atom_w", None)
            check("写之前句柄是开着的", handle_before is not None)
            w.close()                                     # 这条路径**会早退**
            check("⭐⭐ `close()` 早退时 atom 句柄**也关掉了**（同 ❓ 那次事故）",
                  getattr(w, "_atom_w", None) is None,
                  "" if getattr(w, "_atom_w", None) is None else "句柄还开着")
        finally:
            import shutil
            shutil.rmtree(tmp2, ignore_errors=True)

        bad = [n for n, ok, _ in RESULTS if not ok]
        print("\n" + "=" * 60)
        print(f"{len(RESULTS) - len(bad)}/{len(RESULTS)} 通过")
        for n in bad:
            print(f"  ❌ {n}")
        return 1 if bad else 0
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
