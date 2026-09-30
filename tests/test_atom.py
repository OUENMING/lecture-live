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
        # ⭐⭐ **非 dict 元素不许把闸门炸掉**（2026-09-29 修）——
        #    本函数**专门用来兜模型脏输出**，而 `points` 就是模型给的。
        #    原来 `p.get("src")` 遇 `"foo"` 抛 AttributeError 从闸门里逃出去，
        #    反倒把调用方炸掉，而且 `tot` 已先 +1 → 统计也不可用。
        check("⭐⭐ 混入非 dict 元素 -> 不抛，且计进分母不计进分子",
              atom.traceable(["foo", {"src": [1]}, None, 42], 0, 3) == (1, 4))

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
        # ⚠️ 2026-09-30：这条也是**键集合**判据（和上面 T16 那条是两处）——
        #    加 `zh` 时**两处都要改**，改一处会漏（我第一版就漏了这条）。
        check("字段齐全（id/t/epoch/src/kind/text/terms/zh —— 8 个）",
              set(back[0]) == {"id", "t", "epoch", "src", "kind", "text",
                               "terms", "zh"},
              str(sorted(back[0])))
        w.close()
        # ⚠️ 原来是 `check("close() 幂等（调两次不炸）", True)` —— **恒真**
        #    （2026-09-28 审查指出）：`ok` 写死 True；第二次 `close()` 真炸了的话
        #    也是在这一行**抛出**、根本走不到那条 check。
        #    改成真判据：第二次调用之后句柄确实被清空、且证据文件没被动过。
        _path = pathlib.Path(sess)
        _before_txt = _path.read_text(encoding="utf-8")
        w.close()                                    # ⚠️ 幂等：再调一次
        check("close() 幂等（第二次调用后句柄已清空、文件没动）",
              getattr(w, "_h", None) is None
              and _path.read_text(encoding="utf-8") == _before_txt,
              f"_h={getattr(w, '_h', None)!r}")
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
        # ⚠️⚠️ **只看那一行附近**，不能 `'!= "raw"' in src_main` ——
        #    `raw` 档在 main.py 别处也判（`:1009` 那支）→ "把 drain 里那道闸删掉"
        #    **查不出来**。2026-09-28 变异 M6 抓到的，**同一类错这是第三次**：
        #    判据要指向**那个位置**，不要指向"整个文件里有没有"。
        #
        # ⚠️ 2026-09-30 阶段 3 接线：调用点从 `atomq.put_nowait(...)` 换成了
        #    `summ.feed(...)`（`live_summary.LiveSummarizer`）。
        #    ⭐ **不变量一条没变，但「非阻塞」那半换了住处** ——
        #    它现在住在 `live_summary.feed()` 里面（那边 `put_nowait`）。
        #    → 所以下面**分两处钉**：闸门钉 main.py 那个调用点，
        #      非阻塞钉 `live_summary.py` 里 `feed` 的实现。
        #      （只把 `atomq.put_nowait` 改名成 `summ.feed` 就是**假绿**：
        #        `feed` 里换成 `put` 的话判据照样过，而主循环会开始阻塞。）
        CALL = "summ.feed("
        put_idx = [i for i, ln in enumerate(lines) if CALL in ln]
        check(f"找得到 `{CALL}` 那一行（否则下面那条是空的）", bool(put_idx),
              str(len(put_idx)))
        gate_ok = False
        for i in put_idx:
            window = "\n".join(lines[max(0, i - 6):i])
            if '!= "raw"' in window:
                gate_ok = True
        check(f'⭐ `{CALL}` **上面几行**有 `trans["mode"] != "raw"` 那道闸', gate_ok)
        # ⭐ 非阻塞那半 —— 钉实现，不钉"调用点上写没写 `put_nowait`"。
        src_ls = (HERE / "live_summary.py").read_text(encoding="utf-8")
        body = src_ls[src_ls.index("    def feed(self"):]
        body = body[:body.index("\n    def ")]
        check("⭐⭐ `live_summary.feed()` 里用的是 `put_nowait` 而不是 `put`"
              "（drain 在主循环里，不能阻塞）",
              "put_nowait(" in body and "\n" + " " * 8 + "self._q.put(" not in body,
              f"feed 里 put_nowait={body.count('put_nowait(')}")

        # ③ ⭐ 实时总结那一行：**由产出方打，不由 `drain` 打**（2026-09-30 实测）
        #    ⚠️⚠️ 收尾时 `finish()` 定稿**最后一章**，而那时**主循环已经退出、
        #       没人在 `drain`** → 那一行放在 `drain` 里 = **最后一章永远静默**
        #       （实测：3 个正式章只打了 2 条）。
        import main as m
        check("⭐ `summary_log_line`：正式章 -> 那一行；临时章 / 别的 kind / 非 dict -> None",
              m.summary_log_line({"kind": "chapter",
                                  "chapter": {"status": "final", "title": "T"}}) == "📑 T"
              and m.summary_log_line({"kind": "chapter",
                                      "chapter": {"status": "interim", "title": "T"}}) is None
              and m.summary_log_line({"kind": "atoms"}) is None
              and m.summary_log_line({"kind": "chapter"}) is None
              and m.summary_log_line("not a dict") is None)
        _i = src_main.index('elif item[0] == "summary":')
        _blk = src_main[_i:src_main.index('elif item[0] == "final":', _i)]
        # 改坏：把那一行挪回 `drain` 的 summary 分支 -> 这条红。
        check("⭐⭐ `drain()` 的 summary 分支只推界面、**不打终端那一行**"
              "（打了就有**两个定义点**，而收尾那次永远跑不到）",
              "summary_update" in _blk and "📑" not in _blk, _blk[:70].replace("\n", "⏎"))
        _e = src_main.index("def _summ_emit")
        _eblk = src_main[_e:src_main.index("summ = _live.LiveSummarizer", _e)]
        _ok_order = ("summary_log_line(payload)" in _eblk
                     and 'getattr(ui, "_closed"' in _eblk
                     and _eblk.index("summary_log_line(payload)")
                     < _eblk.index('getattr(ui, "_closed"'))
        # 改坏：把那一行挪到守卫**之后** -> 这条红（收尾时 `stopping` 已置位）。
        check("⭐⭐ 那一行排在守卫**之前** —— 收尾时 `stopping` 已置位，"
              "排后面等于把最后一章吞掉", _ok_order)
        check("⭐ 守卫看的是**界面还在不在**（`ui._closed`），不是 `stopping`"
              "（\"播完\"也会置位，那时界面还活着、`finish()` 的 payload 该送达）",
              'stopping.is_set()' not in _eblk)

        # ② ⚠️ 复刻 ❓ 那次**真事故**的形状：`close()` 有三条早退（2026-09-29 起
        #    是四条，多了「没有 Obsidian 库」那条 —— **规矩不变：护栏在早退之前**），
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

        # ⭐⭐ 取句柄那一步的 I/O **也必须在护栏里**（2026-09-29 修）。
        #    `AtomWriter._handle()` 做的是 `path.open("a")` —— 磁盘只读 / 目录被删 /
        #    权限不足都会抛 `OSError`。它原来在 `try` **外面** →
        #    与紧随其后那句「绝不让它把流水线带崩」**自相矛盾**，
        #    而调用方是 `main` 的 atom 工作线程：一抛就整节课不再落原子。
        #    ⚠️ 造这个失败**要绕一下**：`AtomWriter.__init__` 会把传进来的路径
        #       `with_suffix("") + ATOM_TAIL` —— 所以**派的路径**才是真正 open 的那个。
        #       让**派生后**那个名字是个目录，`open(..., "a")` 必抛 IsADirectoryError。
        #       （第一版直接传了个目录进去，派生出来却是个文件 → open 成功、
        #         `append` 正常返回 1，判据在**修好的代码上也红** —— 夹具错，不是实现错。）
        import shutil as _sh
        import atom as _atom
        _td = tempfile.mkdtemp(prefix="cl-atom-")
        try:
            _sess = pathlib.Path(_td) / "x.md"
            (_sess.with_suffix("")).with_name(
                _sess.stem + _atom.ATOM_TAIL).mkdir()
            _w2 = _atom.AtomWriter(str(_sess))
            try:
                _n = _w2.append([_atom.Atom(1, "14:08:06", 1.0, [0], "要点", "x")])
                check("⭐⭐ 句柄打不开时 `append` **返回 0 而不是抛**"
                      "（否则 atom 线程死、整节课不再落原子）", _n == 0, f"拿到 {_n!r}")
            except Exception as _e:                            # noqa: BLE001
                check("⭐⭐ 句柄打不开时 `append` **返回 0 而不是抛**"
                      "（否则 atom 线程死、整节课不再落原子）", False,
                      f"{type(_e).__name__}: {_e}")
        finally:
            _sh.rmtree(_td, ignore_errors=True)

        print("\n--- ⑨ ⭐⭐ `terms` 的机械闸门（2026-09-30 起不再恒空）---")
        # ⚠️ 变异验证：
        #    · 去掉 `len(en) > TERM_MAX_CHARS` 那半 → 「超长丢掉」红
        #    · 去掉 `len(pair) != 2` 那半 → 「单元素丢掉」红（会抛 ValueError）
        #    · 去掉 `if len(out) >= MAX_TERMS: break` → 「第 4 对丢掉」红
        _p = {"text": "t", "kind": "要点", "src": [0], "terms": [
            ["price elasticity of demand", "需求价格弹性"],   # ✅ 正常
            ["a"],                                            # ❌ 不是对
            ["b", "c"],                                       # ✅ 第二个
            ["  ", "y"],                                      # ❌ 空白
            [123, "z"],                                       # ❌ 不是字符串
            ["x" * 61, "y"],                                  # ❌ 超长
            ["k3", "v3"],                                     # ✅ 第三个
            ["k4", "v4"],                                     # ❌ 超上限
        ]}
        _got = atom.parse_reply({"topic": "T", "points": [_p]}, 1)
        check("⭐⭐ 只留合法的那三对，且**保序**",
              _got and _got[0].terms == [["price elasticity of demand", "需求价格弹性"],
                                         ["b", "c"], ["k3", "v3"]],
              str(_got[0].terms if _got else None))
        check("⚠️ 单元素 / 空白 / 非字符串 / 超长**各自**被丢（不是靠总数兜住的）",
              _got and ["a"] not in _got[0].terms
              and ["  ", "y"] not in _got[0].terms
              and [123, "z"] not in _got[0].terms
              and ["x" * 61, "y"] not in _got[0].terms)
        check("⚠️ **不做修补**：`[\"a\"]` 不许被补成 `[\"a\", \"\"]`",
              _got and all(len(t) == 2 and t[1] for t in _got[0].terms))
        check("⚠️ `terms` 缺失 / 不是列表 / 是 None -> 空表，**不抛**",
              atom.parse_reply({"topic": "T", "points": [
                  {"text": "t", "kind": "要点", "src": [0]}]}, 1)[0].terms == []
              and atom.parse_reply({"topic": "T", "points": [
                  {"text": "t", "kind": "要点", "src": [0], "terms": None}]}, 1)[0].terms == []
              and atom.parse_reply({"topic": "T", "points": [
                  {"text": "t", "kind": "要点", "src": [0], "terms": "nope"}]}, 1)[0].terms == [])
        # ⚠️ 2026-09-30：键集合 **7 → 8**（加了 `zh`）。
        #    这条判据的本意是「别**悄悄**改 `.atoms.jsonl` 的格式契约」——
        #    而这次是**有意的**（作者要求给要点也加中文译文），所以这里**跟着改**，
        #    并把新键写进名单。要再加字段，**先改这一行**。
        #    风险低：`find.py` 不索引 `.atoms.jsonl`、`atom.load` 生产调用方为零。
        check("⭐ `.atoms.jsonl` 的键集合恰好这 8 个（加 `zh` 是**有意的**，见上）",
              sorted(_got[0].as_json()) == ["epoch", "id", "kind", "src", "t",
                                            "terms", "text", "zh"],
              str(sorted(_got[0].as_json())))
        _z = lambda **kw: atom.parse_reply(                          # noqa: E731
            {"points": [dict({"text": "T", "kind": "要点", "src": [0]}, **kw)]}, 1)
        # 改坏：把 `if len(zh) > TEXT_ZH_MAX_CHARS: zh = ""` 改成 `continue` -> 这条红。
        check("⭐⭐ `zh`：正常带出 · 缺/None/超长**置空但条目照留**",
              _z(zh="中译")[0].zh == "中译" and len(_z()) == 1 and _z()[0].zh == ""
              and len(_z(zh=None)) == 1 and _z(zh=None)[0].zh == ""
              and len(_z(zh="长" * (atom.TEXT_ZH_MAX_CHARS + 1))) == 1
              and _z(zh="长" * (atom.TEXT_ZH_MAX_CHARS + 1))[0].zh == "",
              repr([_z(zh="中译")[0].zh, _z()[0].zh,
                    _z(zh="长" * (atom.TEXT_ZH_MAX_CHARS + 1))[0].zh]))

        print("\n--- ⑩ ⭐ 主题截断：英文标题不许被拦腰截断 ---")
        # ⚠️ 变异验证：把 `TOPIC_MAX` 改回 12 → 第一条红。
        _long = "Why the marginal utility of the last unit falls as consumption rises"
        _o = {"topic": _long, "topic_zh": "边际效用为何随消费量上升而下降，这是一个很长的中文标题"}
        check("⭐⭐ 英文主题**不再截在 12**（12 是按中文定的）",
              atom.topic_of(_o) == _long and len(atom.topic_of(_o)) > 12,
              f"拿到 {atom.topic_of(_o)!r}（{len(atom.topic_of(_o))} 字）")
        check("⭐ 中文标题截在 `TOPIC_ZH_MAX`(=24)",
              len(atom.topic_zh_of(_o)) == 24, str(len(atom.topic_zh_of(_o))))
        check("⚠️ 空串是**合法**的，两个函数都不兜底",
              atom.topic_of({}) == "" and atom.topic_zh_of({}) == ""
              and atom.topic_of(None) == "" and atom.topic_zh_of(None) == "")
        check("⚠️ 模型没给 `topic_zh` -> 空串（**不退回英文** —— 那是消费方的决定）",
              atom.topic_zh_of({"topic": "T"}) == "")

        print("\n--- ⑪ ⭐ SYS 的输出骨架（改英文为主之后）---")
        # ⚠️⚠️ 第一版这里**抓子串**（找以 `"points"` 开头的那行、看它含不含 `terms`）——
        #    结果**判据自己红了**：`terms` 在骨架的**续行**上，不在 `"points"` 那行。
        #    → 改成**结构性断言**：把骨架当 JSON 解析，再查键。抓子串永远会漏续行。
        import json as _json
        _lines = atom.SYS.splitlines()
        _s = next((i for i, ln in enumerate(_lines)
                   if ln.strip().startswith('{"topic"')), None)
        _e = next((i for i in range(_s or 0, len(_lines))
                   if _lines[i].rstrip().endswith("}]}")), None)
        _skel = _json.loads("\n".join(_lines[_s:_e + 1])) if _s is not None and _e else None
        check("⭐ 骨架是**合法 JSON**（模型照着它回，形状错了就白搭）",
              isinstance(_skel, dict), str(_skel)[:120])
        check("⭐ 骨架顶层有 `topic_zh`（英文标题 + 中文标题成对）",
              isinstance(_skel, dict) and "topic_zh" in _skel, str(sorted(_skel or ())))
        check("⭐ `points[0]` 带 `terms` 字段",
              isinstance(_skel, dict) and "terms" in (_skel.get("points") or [{}])[0],
              str(sorted((_skel or {}).get("points", [{}])[0])))
        check("⚠️ `kind` 枚举那行**仍然是**六个中文值（内部枚举不是显示文字）",
              any('"kind": "主题|要点|定义|例子|课务|讲者强调"' in ln
                  for ln in atom.SYS.splitlines()))

        # ⚠️ `bad` 必须**就在结算这一处**算 —— `test_entry_panel.py` 栽过一次：
        #    它算在中间，加在它后面的判据**只打印、不进统计、失败也不影响退出码**。
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
