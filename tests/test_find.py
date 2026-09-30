#!/usr/bin/env python3
"""`find.py` 的判据。

    ClassLive.app/Contents/MacOS/python tests/test_find.py

## 这个文件钉住了什么

1. ⭐ **CJK 子串、ASCII 词边界** —— `cost` 不许命中 `costly`（裸子串的经典坑，
   `prep._exact_pattern` 的 docstring 逐字讲过）
2. ⭐ **多词是无序 AND**，且**逐词分别匹配**（不是前瞻链 —— 那是二次复杂度）
3. ⭐ **截断必须说出来** —— `limit` 截了就得 `truncated=True` 且 `total` 是**真数**
   （`minutes` 的 `retrieval.rs` 逐字：截断会「let a negative result appear exhaustive
   when it is not」）
4. ⭐⭐ **检索单元是「块」不是「行」** —— 一条定稿块的 EN/ZH/ASR 是**同一句话**。
   按行搜有两个真缺陷（2026-09-28 实测）：**跨语言 AND 恒为 0**（EN 与 ZH 在不同行）、
   **总数虚高一倍**。本组两条判据直接钉这两个
5. **空查询不许倒出整个语料**（返回空，不是"匹配所有行"）
6. `read(path, lo, hi)` **只回那个范围**，且超限时**明说被截断**

⚠️ 语料是**合成**的（`tempfile`），不碰 `sessions/` 一个字节 —— 除最后一条
   耗时判据要读真语料（**只读**）。
⚠️ 写这里每条断言先问「把实现改坏它会不会红」。恒真的断言不如不写。
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import find                                                          # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  {detail}" if detail else ""))


SESSION_MD = """# ECON10740 · 2026-09-28 · 实时会话日志

> [!abstract] 14:08:06
> **EN**: The monopolist sets a price above marginal cost.
> **ZH**: 垄断者把价格定在边际成本之上。
> **ASR**: The monopolist sets a price above marginal cost.

> [!abstract] 14:09:10
> **EN**: This is costly for consumers.
> **ZH**: 这对消费者来说代价高昂。

> [!abstract] 14:10:00 ⭐ Exam Focus
> **EN**: The function of state is what matters here.
> **ZH**: 状态函数才是这里的关键。
"""


def main() -> int:
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="cl_find_"))
    try:
        sess = tmp / "sessions"
        sess.mkdir()
        (sess / "2026-09-28_140806_ECON10740.md").write_text(SESSION_MD, encoding="utf-8")
        gl = tmp / "glossary"
        gl.mkdir()
        (gl / "ECON10740.txt").write_text(
            "# ECON10740 Exploring Economics\nmarginal cost\nmonopoly\n", encoding="utf-8")
        roots = [(sess, "session"), (gl, "glossary")]

        print("\n--- ① 匹配规则：CJK 子串 / ASCII 词边界 ---")
        rxs_cn = find.compile_query("垄断")
        check("中文能搜到", bool(rxs_cn and find.matches(rxs_cn, "> **ZH**: 垄断者把价格定在边际成本之上。")))
        rxs_en = find.compile_query("cost")
        check("英文命中 'marginal cost'", bool(rxs_en and find.matches(rxs_en, "a price above marginal cost")))
        # ⭐ 这条是这一组的主角：裸子串会让 cost 命中 costly
        check("⭐ 英文**不**命中 'costly'（词边界）",
              not (rxs_en and find.matches(rxs_en, "This is costly for consumers.")))

        print("\n--- ② 多词是无序 AND ---")
        rxs2 = find.compile_query("state function")
        check("⭐ 两个词**顺序反过来**也命中",
              bool(rxs2 and find.matches(rxs2, "The function of state is what matters")))
        check("只命中一个词 -> 不算命中",
              not (rxs2 and find.matches(rxs2, "The function is simple.")))

        print("\n--- ③ 空查询不许倒出整个语料 ---")
        r0 = find.search("", roots=roots)
        check("空查询 -> 0 条（不是全部）", r0.total == 0 and len(r0.hits) == 0,
              f"total={r0.total}")
        check("纯空白也一样", find.search("   ", roots=roots).total == 0)

        print("\n--- ④ total / truncated（截断必须说出来）---")
        # ⚠️ 用 `marginal`：它在夹具里有**两处**（会话 EN 行 + glossary）。
        #    第一版用 `monopolist`，而它只有一处（ASR 行被跳过）→ `limit=1` 根本不截断，
        #    那条断言就恒真了。
        r_all = find.search("marginal", roots=roots, limit=99)
        check("不截断时 truncated=False", not r_all.truncated, f"total={r_all.total}")
        check("夹具里确实有多处命中（否则下面那条是空的）", r_all.total >= 2,
              f"total={r_all.total}")
        r_cut = find.search("marginal", roots=roots, limit=1)
        check("⭐ 被 limit 截断时 truncated=True", r_cut.truncated, str(r_cut))
        check("⭐ 但 total 仍是**真数**（不是显示条数）",
              r_cut.total == r_all.total and len(r_cut.hits) == 1,
              f"total={r_cut.total} shown={len(r_cut.hits)}")

        print("\n--- ⑤ ⭐ 检索单元是「块」不是「行」（一条字幕算一次）---")
        r_blk = find.search("marginal", roots=roots, limit=99)
        # 块 1 的 EN / ASR 两行都含 marginal，按行算会得 3（含 glossary），按块算得 2
        check("⭐ 同一条字幕的 EN+ASR 只算**一次**", r_blk.total == 2, f"total={r_blk.total}")
        # ⭐⭐ 这条是本组的真正主角：EN 与 ZH 在不同行，按行搜时跨语言 AND **恒为 0**
        r_mix = find.search("state 状态", roots=roots, limit=99)
        check("⭐⭐ 跨语言 AND 能命中（EN 一个词 + ZH 一个词）",
              r_mix.total >= 1, f"total={r_mix.total}（按行搜时这里恒为 0）")
        r_mix2 = find.search("monopolist 垄断", roots=roots, limit=99)
        check("⭐⭐ 同上，另一个方向也命中", r_mix2.total >= 1, f"total={r_mix2.total}")
        # ⚠️ `hits[0]` 原来没护栏（2026-09-28 审查指出）：底层回归导致命中变 0 时
        #    这里抛 `IndexError` → `main()` 中断，后面的 ⑥⑦⑧⑨ 全不跑、汇总也没有 ——
        #    本该干净显示一条 ❌ 的，变成难以归因的异常栈。
        _ln = r_blk.hits[0].line if r_blk.hits else None
        check("命中行落在**真正匹配的那一行**（read 才落得准）",
              _ln == 4, f"line={_ln}")

        print("\n--- ⑥ 元数据从文件名来 ---")
        h = find.search("垄断者", roots=roots).hits[0]
        check("课号解析对（复用 courses._session_course）", h.course == "ECON10740", str(h.course))
        check("日期解析对", h.date == "2026-09-28", str(h.date))
        check("kind 认出 sessions / glossary",
              h.kind == "session"
              and find.search("monopoly", roots=roots).hits[0].kind == "glossary")

        print("\n--- ⑦ read 必须带行号范围 ---")
        p = str(sess / "2026-09-28_140806_ECON10740.md")
        got = find.read(p, 3, 5)
        check("只回那几行（1-based 闭区间）",
              "14:08:06" in got and "14:09:10" not in got, got.splitlines()[0])
        # ⚠️⚠️ 这一条原来是**假判据**（2026-09-28 审查把根因挖出来了）：
        #    夹具只有 14 行，而当时 `capped` 是按**请求的范围**算的
        #    （`(9999-1+1) > MAX_READ_LINES`）→ 一个装得下的文件也报「被截断」，
        #    **那句话是假的**，而模型会因此以为自己没看全。
        #    拆成两条，各钉一个真行为：够长才说、装得下**不许**说。
        long_p = tmp / "long.md"          # ⚠️ 放 tmp 根，别落进 sessions/ 影响后面那些检索断言
        long_p.write_text("\n".join(f"第 {i} 行" for i in range(1, find.MAX_READ_LINES + 60)),
                          encoding="utf-8")
        check("⭐ 真超过上限 -> 明说被截断",
              "被截断" in find.read(str(long_p), 1, find.MAX_READ_LINES + 50),
              find.read(str(long_p), 1, find.MAX_READ_LINES + 50).splitlines()[-1])
        check("⭐⭐ 装得下就**不许**谎报被截断（14 行 < 上限）",
              "被截断" not in find.read(p, 1, 9999),
              find.read(p, 1, 9999).splitlines()[-1])
        check("行号越界不崩", isinstance(find.read(p, 9000, 9100), str))

        print("\n--- ⑧ as_context：把命中拼成给模型的材料 ---")
        check("空结果 -> 空串（**不是**「没找到」那句话）",
              find.as_context(find.Result([], 0, False)) == "")
        check("没有命中时也是空串", find.as_context(find.search("zzz不存在zzz", roots=roots)) == "")
        ctx = find.as_context(find.search("marginal", roots=roots, limit=99))
        check("每条都带出处（课号 / 日期）", "[ECON10740 · 2026-09-28]" in ctx, ctx[:70])
        # ⭐ 截断了要说出来 —— 给模型的那一份也要（`Result.truncated` 是给代码看的）
        ctx_cut = find.as_context(find.search("marginal", roots=roots, limit=1))
        check("⭐ 只给了一部分时 -> **明说**", "只给了前" in ctx_cut, ctx_cut[-40:])
        check("给全了就不说那句", "只给了前" not in ctx)
        tiny = find.as_context(find.search("marginal", roots=roots, limit=99), max_chars=5)
        check("预算极小 -> 空串（不吐半行）", tiny == "", repr(tiny))

        print("\n--- ⑨ 真语料：耗时（只读）---")
        real = HERE / "sessions"
        if not real.is_dir():
            check("真语料耗时", False, "sessions/ 不在")
        else:
            t = time.perf_counter()
            rr = find.search("demand", roots=[(real, "session")], limit=20)
            dt = (time.perf_counter() - t) * 1000
            # ⚠️ 将来语料长大这条会红 —— 那就是"该上索引了"的信号（计划 §6）
            check(f"⭐ 全量扫描 < 2000 ms（实测 {dt:.0f} ms, {rr.total} 命中）", dt < 2000)

        print("\n--- ⑩ ⭐⭐ 降级档：`_OW = None`（没有 PyObjC / obsidian_writer）---")
        # ⚠️⚠️ 本模块文件头写着「`find` 要能在没装 PyObjC 的环境里被 import」——
        #    那条承诺的落地形状就是 `try: import obsidian_writer / except: _OW = None`。
        #    而 `default_roots()` 里**一个护栏一个裸取**：
        #      · `getattr(_OW, "SESSIONS", None)`   ← 有
        #      · `_OW.resolve_vault()`              ← 没有 → `AttributeError`
        #    → `_OW` 拿不到时整个函数炸；而**读的人看到上面那行 getattr 就会以为
        #      整段都防住了** —— 半吊子护栏比没有更容易骗人。
        #    变异验证（实跑过）：把 `if _OW is None: return roots` 那行删掉 → 本条变红。
        _saved = find._OW
        try:
            find._OW = None
            try:
                _r = find.default_roots()
                _err = ""
            except Exception as e:                        # noqa: BLE001
                _r, _err = None, f"{type(e).__name__}: {e}"
            check("⭐⭐ `_OW = None` 时 `default_roots()` 不炸", _r is not None, _err)
            check("⭐ 而且仍给出 `glossary/` 那个根（降级不等于搜不了）",
                  bool(_r) and any(k == "glossary" for _, k in _r), str(_r))
            # ⚠️ `_r` 为 None（上面那条已经红了）时这里必须**跟着红** —— 写成
            #    `(_r or [])` 会变成空序列 → `all(...)` 恒真，成了「只在顺境里
            #    成立」的判据（2026-10-01 独立审核指出）。
            check("⚠️ 而且**没有**会话/笔记根（那两处要 obsidian_writer 才查得到）",
                  _r is not None and all(k not in ("session", "note") for _, k in _r),
                  str(_r))
        finally:
            find._OW = _saved
        # 对照：正常那一档三个根都在（别把降级的写法反过来套到正常路上）
        # ⚠️ **读端也要隔离**（2026-10-01 独立审核指出）：不注入 `OBSIDIAN_VAULT`
        #    的话，这台机器上「`~/.classlive/vault` 记没记过」决定这条判据红绿 ——
        #    没配过 vault 的机器只出两个根 → **假红**。`resolve_vault()` 只认环境
        #    变量、不检查目录存不存在，所以给个固定串就够（不建任何目录）。
        _saved_env = os.environ.get("OBSIDIAN_VAULT")
        os.environ["OBSIDIAN_VAULT"] = "/tmp/cl-find-control-vault"
        try:
            _n = find.default_roots()
        finally:
            if _saved_env is None:
                del os.environ["OBSIDIAN_VAULT"]
            else:
                os.environ["OBSIDIAN_VAULT"] = _saved_env
        check("⚠️ 对照：`_OW` 正常时三个根都在（session / glossary / note）",
              sorted(k for _, k in _n) == ["glossary", "note", "session"], str(_n))

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
