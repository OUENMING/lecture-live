#!/usr/bin/env python3
"""课程语料：从上课转录里长出来的关键词表。

    ClassLive.app/Contents/MacOS/python tests/test_corpus.py

## 这个文件钉住什么（判据的唯一定义点，别在别处再抄一份）

1. ⭐⭐ **自净规则：只在一次课里出现过的词不进关键词表。**
   动机是真实事故：作者 2026-09-28 确认 `sessions/` 里有一节是**传错文件**录进去的
   （10730 门里躺着化学课的 `kinetic / velocity / bond`）。那一节是孤例 → df=1 → 挡掉。
   ⚠️ **这条必须做变异验证**（把 `need` 改成 1，判据必须红）—— 否则它可能是恒真的。

2. ⭐⭐ **评分的单位是「跨了几节课」，不是「出现了几次」。**
   两个坑连着踩：`it's` 那种缩合词靠高频压过 `monopoly`（25 个关键词里 8 个是碎片）；
   换成次线性 tf **还是压过**（实测 800 次 vs 12 次 → 5.32 > 3.82）。
   → 只能换单位。判据在 ③，用 `词频 × idf` 跑它**必红**。

3. **短课次不算一节课**（空的 / 测试残留 / 录一半的）。

4. **降级要说出来**：只有一节够格课次时退回 df>=1，课号要出现在 `degraded` 里 ——
   不然它和正常结果长得一模一样（同 `minutes` 那条「负结果不许读起来像穷尽」）。

⚠️ 夹具用的是**真格式**的会话文件（`> [!abstract]` + `> **EN**: …`），
   因为它同时把「怎么读会话文件」那条契约也验了（读法在 `obsidian_writer._parse`，
   本模块**不写第二份**）。⚠️ 别把夹具改成"喂 `_counts` 一段裸文本"——
   那样上面那条契约就没人验了。
"""
from __future__ import annotations

import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import corpus                                                      # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  {detail}" if detail else ""))


def _session(path: pathlib.Path, *lines: str) -> None:
    """按**真格式**写一份会话文件（读法归 `obsidian_writer._parse`）。"""
    body = ["# 会话日志", ""]
    for i, ln in enumerate(lines):
        body += [f"> [!abstract] 12:0{i}:00", f"> **EN**: {ln}",
                 f"> **ZH**: 中文", f"> **ASR**: {ln}", ""]
    path.write_text("\n".join(body), encoding="utf-8")


def main() -> int:
    print("\n--- ① ⭐⭐ 自净：只在一次课里出现过的词，不进关键词表 ---")
    with tempfile.TemporaryDirectory() as td:
        S = pathlib.Path(td) / "sessions"
        S.mkdir()

        # A 课：两节真正的经济学课（monopoly 反复出现）
        for k in (1, 2):
            _session(S / f"2026-09-0{k}_090000_11111.md",
                     *([f"monopoly price surplus demand market " * 8] * 6))
        # A 课：**一节传错的化学课**（kinetic/velocity/bond 只在这一节里）
        _session(S / "2026-09-03_090000_11111.md",
                 *([f"kinetic velocity bond particles microscopic " * 10] * 6))
        # B 课：**三节**社会学课（`MIN_SESSIONS=3` 之后，少于 3 节会降级）
        for k in (1, 2, 3):
            _session(S / f"2026-09-0{k}_090000_22222.md",
                     *([f"sociology solidarity epistemology inequality " * 8] * 6))

        got, deg = corpus.keywords(["11111", "22222"], sessions_dir=S)
        a = got["11111"]
        check("本课反复出现的实词进表了（monopoly）", "monopoly" in a, str(a))
        # ⭐⭐ 这条是本节的重头
        check("⭐⭐ 传错那节的化学词**一个都没进来**",
              not {"kinetic", "velocity", "bond", "particles", "microscopic"} & set(a),
              str([w for w in a if w in {"kinetic", "velocity", "bond"}]))
        check("另一门课的词也没串进来", "sociology" not in a)
        check("B 课只该有它自己的词",
              "sociology" in got["22222"] and "monopoly" not in got["22222"],
              str(got["22222"]))

        # ── 变异验证：把自净门槛改成 1，上面那条必须红 ────────────────
        print("  --- 变异验证：门槛改成 1（= 关掉自净）---")
        _orig = corpus.keywords
        try:
            def _no_clean(known, *, sessions_dir, top=corpus.DEFAULT_TOP,
                          sessions_of=None):
                words, deg2 = _orig(known, sessions_dir=sessions_dir, top=top,
                                    sessions_of=sessions_of)
                # 关掉自净 = 允许 df>=1：直接重算一遍
                from courses import session_files
                per = {c: corpus._session_tokens(c, sessions_dir=sessions_dir,
                                                 sessions_of=session_files)
                       for c in known}
                out = {}
                for c, cs in per.items():
                    tf = {}
                    for x in cs:
                        for w, n in x.items():
                            tf[w] = tf.get(w, 0) + n
                    out[c] = list(tf)
                return out, deg2
            corpus.keywords = _no_clean
            # ⚠️ 直接调变异体拿**词表**；`build()` 返回的是**描述串**，
            #    拿它 `set()` 会按字符切（第一版就是这么写错的）。
            # ⚠️ **必须走被补丁的那个入口**（2026-09-28 审查指出）：原来直接调局部
            #    函数 `_no_clean`，于是上面那句 `corpus.keywords = _no_clean` 与
            #    finally 里的还原**全是死代码** —— 这个"变异体"根本没经过被测入口，
            #    证明不了"真函数被替换后确实会红"。
            mut, _ = corpus.keywords(["11111", "22222"], sessions_dir=S)
            leaked = {"kinetic", "velocity", "bond"} & set(mut["11111"])
            check("⭐ 变异体里化学词**漏了进来**（证明上一条不是恒真）",
                  bool(leaked), str(sorted(leaked)))
        finally:
            corpus.keywords = _orig

    print("\n--- ② 短课次不算一节课 + 只有一节时降级要说出来 ---")
    with tempfile.TemporaryDirectory() as td:
        S = pathlib.Path(td) / "sessions"
        S.mkdir()
        _session(S / "2026-09-01_090000_11111.md", "hello")        # 太短，不算
        _session(S / "2026-09-02_090000_11111.md",
                 *([f"elasticity demand supply market " * 8] * 6))
        got, deg = corpus.keywords(["11111"], sessions_dir=S)
        check("⭐ 只有 1 节够格 -> 降级，**而且给的是人话原因**（不是 True/代号）",
              "11111" in deg and "1 节" in deg.get("11111", ""), str(deg))
        check("⭐ 降级 = **不给词表**（调用方退回术语表），不是给个差一点的表",
              got["11111"] == [], str(got["11111"]))
        got2, deg2 = corpus.keywords(["33333"], sessions_dir=S)
        check("⭐ 一门课都没有 -> 空表 + **原因是「还没有上课记录」**",
              got2["33333"] == [] and deg2.get("33333") == "还没有上课记录", str(deg2))

    print("\n--- ③ ⚠️ 次线性 tf：高频不能压过实词 ---")
    with tempfile.TemporaryDirectory() as td:
        S = pathlib.Path(td) / "sessions"
        S.mkdir()
        # ⚠️ 两个词**都要跨 >=2 节**（不然自净规则就把弱的那个挡掉了，
        #    这条判据就变成"自净"的重复，量不到"评分单位"）。
        # ⚠️ 夹具的**实测**形状（2026-09-28 现数出来的，原来这里写的与夹具不符）：
        #    A 课 **4 节** —— blah 跨 2 节、共 400 次；monopoly 跨 4 节、共 84 次。
        # ⚠️ 每节都 >= `MIN_WORDS`，否则被"短课次"规则滤掉（第一版就栽在这）。
        for k in (1, 2):
            _session(S / f"2026-09-0{k}_090000_11111.md",
                     *([("blah " * 200) + ("monopoly " * 2)] * 1))
        _session(S / "2026-09-03_090000_11111.md", *([("monopoly " * 20)] * 2))
        _session(S / "2026-09-04_090000_11111.md", *([("monopoly " * 20)] * 2))
        # B 课：blah 也出现 —— 这样它的跨课 idf 更低
        _session(S / "2026-09-01_090000_22222.md", *([f"blah sociology " * 8] * 6))
        words, _ = corpus.keywords(["11111", "22222"], sessions_dir=S)
        a = words["11111"]
        # ⭐⭐ 这是本模块的核心取舍：**单位是"跨几节"，不是"出现几次"**。
        #    第一版用 `词频 × idf`，blah（800 次）压过 monopoly（12 次）；
        #    换成次线性 tf **还是压过**（实测 5.32 > 3.82）→ 只能换单位。
        check("两个词都在表里（都跨 >=2 节，自净没挡它们）",
              "monopoly" in a and "blah" in a, str(a))
        # ⭐⭐ 这一条才是"评分单位"的判据：**跨 4 节**的 monopoly 必须排在
        #     跨 2 节、但每节刷 200 遍（共 400 次）的 blah 前面。
        #     ⚠️ 别把它读成"旧公式一定会红"：实测在 `词频 × log(N/df)` 下，
        #        blah 因为两门课都有、idf 恰好归零，monopoly **反而在前** ——
        #        这条钉的是当前 `_score` 的取向（跨几节 × 平滑 idf），不是历史对照。
        #     ⚠️ 存在性要并进同一个表达式（2026-09-28 审查指出）：原来直接
        #        `a.index(...)`，两词缺一个就抛 `ValueError` —— **测试崩掉**，
        #        后面的 ④/④b/⑤ 整块不跑、汇总行也出不来，真因被盖住。
        check("⭐⭐ 跨 4 节的 monopoly 排在跨 2 节、每节刷 200 遍的 blah 前面",
              ("monopoly" in a and "blah" in a
               and a.index("monopoly") < a.index("blah")), str(a))

    print("\n--- ④ 缩合词碎片不许进来 ---")
    with tempfile.TemporaryDirectory() as td:
        S = pathlib.Path(td) / "sessions"
        S.mkdir()
        for k in (1, 2, 3):
            _session(S / f"2026-09-0{k}_090000_11111.md",
                     *([f"it's that's we're don't you're monopoly " * 8] * 6))
        words4, _ = corpus.keywords(["11111"], sessions_dir=S)
        a = set(words4["11111"])
        check("⚠️ 没有任何带撇号的碎片（第一版 25 个里有 8 个是这个）",
              not any("'" in w for w in a), str(sorted(a)))
        check("⚠️ 也没有从缩合词里掉出来的单字母残渣（it's -> it -> 被填充词表挡掉）",
              all(len(w) >= 4 for w in a), str(sorted(a)))

    print("\n--- ④b ⭐ 泛化：非英文转录必须**说出来**，不许静默给空表 ---")
    with tempfile.TemporaryDirectory() as td:
        S = pathlib.Path(td) / "sessions"
        S.mkdir()
        for k in (1, 2, 3):
            # 中文讲课：分词器（纯英文）一个词都抽不出来
            _session(S / f"2026-09-0{k}_090000_11111.md",
                     *(["这门课讲的是边际成本与垄断定价，还有效用函数与生产者剩余。"] * 40))
        got, deg = corpus.keywords(["11111"], sessions_dir=S)
        check("⭐ 抽不出英文 -> 空表（不是静默给一把噪声）", got["11111"] == [])
        # ⚠️ 原来是 `"英文" in … or "太短" in …` —— **把要钉的性质放宽了**
        #    （2026-09-28 审查指出）：实现若因为别的原因报出含"太短"的话，照样绿。
        #    实现那句话是**故意把两种可能都说出来**的（不替用户裁决），所以这里
        #    要求**两半都在** —— 比全等稳（改文案不会白红），比 `or` 紧。
        _why = deg.get("11111", "")
        check("⭐⭐ 而且**报了人话原因**、两种可能都说（不替用户裁决是太短还是非英文）",
              "英文" in _why and "太短" in _why, str(deg))

    print("\n--- ⑤ 结果可复现（平手按词排，不靠 dict 顺序）---")
    with tempfile.TemporaryDirectory() as td:
        S = pathlib.Path(td) / "sessions"
        S.mkdir()
        for k in (1, 2, 3):
            _session(S / f"2026-09-0{k}_090000_11111.md",
                     *([f"alpha beta gamma delta epsilon zeta eta theta " * 8] * 6))
        r1, _ = corpus.keywords(["11111"], sessions_dir=S)
        r2, _ = corpus.keywords(["11111"], sessions_dir=S)
        check("⭐ 两次跑逐字相同（并拼出来看一眼）",
              r1["11111"] == r2["11111"],
              corpus.describe(r1["11111"])[:44])

    bad = [n for n, ok, _ in RESULTS if not ok]
    print("\n" + "=" * 60)
    print(f"{len(RESULTS) - len(bad)}/{len(RESULTS)} 通过")
    for n in bad:
        print(f"  ❌ {n}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
