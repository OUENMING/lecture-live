#!/usr/bin/env python3
"""prep 的判据：追加写入器 + 复核 + 装配。

跑法: ClassLive.app/Contents/MacOS/python tests/test_prep.py   (或 pytest tests/)

⚠️ **测试必须隔离写端**（本仓库硬规矩，历史事故把真实的 `term_notes.json` 从 41KB
写成 4.8KB）。这里全程在 `tempfile.mkdtemp()` 里造样本，
**绝不拿仓库里的 `glossary/` 当真样本写**。

## 为什么「只追加」那条是整份测试里最强的一条

`glossary/<课号>.txt` 是**手写内容与自动内容共处**的文件：首行是领域先验（模型判领域的依据），
课号与教务词是手写的。`new.startswith(old)` 一条断言同时盖住课号、教务词、首行、空行、
以及用户手写的任何东西 —— 比逐条列举强得多，也不会随文件内容变化而漏。
"""
from __future__ import annotations

import inspect
import json
import pathlib
import shutil
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import prep                                                        # noqa: E402

RESULTS: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok))
    print(f"  {'✅' if ok else '❌'} {name}" + (f"\n      {detail}" if detail else ""))


# 仿 `glossary/SOC10020.txt` 的**两行**前导注释（真实文件就是两行，不是一行 ——
# 所以写入器要保的是「整个前导 # 块」，不是「第一行」）
SAMPLE = """\
# SOC10020 Introduction to Sociology(社会学导论)
# 依据真实课堂录音(危机/多重危机那一讲)提取。
Introduction to Sociology
sociological imagination
Brightspace
quiz
"""


def main() -> int:
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="cl_prep_"))
    try:
        def fresh(name="SOC10020.txt", text=SAMPLE):
            p = tmp / name
            p.write_text(text, encoding="utf-8")
            return p

        print("\n1. ⭐ 已有字节逐字节不变（最强的那条）")
        p = fresh()
        old = p.read_bytes()
        r = prep.append_terms(p, ["polycrisis", "rupture"])
        new = p.read_bytes()
        check("new.startswith(old)", new.startswith(old), f"加了 {r.added}")
        check("新内容只出现在末尾",
              new[len(old):].decode("utf-8").split() == ["polycrisis", "rupture"],
              repr(new[len(old):].decode("utf-8")))

        print("\n2. 含课号形态的行：连位置都没动")
        p = fresh(text="# ECON10770 Introduction to Economics(经济学导论)\n"
                       "ECON10101\nECON10102\nsupply\n")
        before = p.read_text(encoding="utf-8").splitlines()
        prep.append_terms(p, ["demand"])
        after = p.read_text(encoding="utf-8").splitlines()
        check("课号那两行原位原样", after[:3] == before[:3], f"前 3 行={after[:3]}")
        check("课号没被当重复或新词处理",
              "ECON10101" not in [ln for ln in after[3:]])

        print("\n3. ⭐ 整个前导 `#` 块逐字不变（两行注释，不是一行）")
        p = fresh()
        prep.append_terms(p, ["anomie"])
        head = p.read_text(encoding="utf-8").splitlines()[:2]
        check("两行注释都在且逐字相同", head == SAMPLE.splitlines()[:2], f"{head}")

        print("\n4. 幂等：同一批导两次不产生第二份")
        p = fresh()
        first = prep.append_terms(p, ["polycrisis", "rupture"])
        snapshot = p.read_bytes()
        second = prep.append_terms(p, ["polycrisis", "rupture"])
        check("第二次 added 为空", second.added == [], f"added={second.added}")
        check("第二次全部进 skipped_dup",
              sorted(second.skipped_dup) == ["polycrisis", "rupture"])
        check("文件一个字节都没变", p.read_bytes() == snapshot)

        print("\n5. 大小写不同算重复")
        p = fresh()
        prep.append_terms(p, ["Polycrisis"])
        r = prep.append_terms(p, ["polycrisis", "POLYCRISIS"])
        check("大小写变体都判重", r.added == [] and len(r.skipped_dup) == 2,
              f"added={r.added} dup={r.skipped_dup}")
        check("文件里只有一份", p.read_text(encoding="utf-8").lower().count("polycrisis") == 1)

        print("\n6. 原子写：不留 tmp 残留")
        p = fresh()
        prep.append_terms(p, ["anomie"])
        leftovers = [x.name for x in tmp.iterdir() if x.name.endswith(".tmp")]
        check("没有 .tmp 残留", not leftovers, f"残留={leftovers}")
        check("内容确实是追加后的", "anomie" in p.read_text(encoding="utf-8"))

        print("\n7. 结构上拿不到公共表（glossary.txt）")
        sig = inspect.signature(prep.append_terms)
        check("签名只有 2 个参数（没有第二个文件位置）",
              list(sig.parameters) == ["path", "terms"], f"{list(sig.parameters)}")
        # ⚠️ 别用「源码里不出现 'glossary.txt'」那种文本断言 —— 文档字符串里就写着它，
        #    一测就红，而且它验的是措辞不是行为。改成**行为**断言：
        #    摆一个真的公共表在旁边，跑完看它动没动、目录里多没多出别的东西。
        pub = tmp / "glossary.txt"
        pub.write_text("# 公共表\nECON10770\n", encoding="utf-8")
        pub_before = pub.read_bytes()
        before = {x.name for x in tmp.iterdir()}
        target = fresh()
        prep.append_terms(target, ["demand"])
        after = {x.name for x in tmp.iterdir()}
        check("旁边的公共表一个字节都没动", pub.read_bytes() == pub_before)
        check("除了目标文件，目录里没多出任何东西", after - before == set(),
              f"多出={sorted(after - before)}")

        print("\n8. 两条边界：末尾无换行 / 非 UTF-8")
        p = tmp / "no_newline.txt"
        p.write_text("# SOC10020\nsupply", encoding="utf-8")   # 末尾**没有** \n
        prep.append_terms(p, ["demand"])
        lines = p.read_text(encoding="utf-8").splitlines()
        check("末尾无换行时新词独占一行（没粘在 supply 尾巴上）",
              lines[-2:] == ["supply", "demand"], f"末两行={lines[-2:]}")

        p = tmp / "latin1.txt"
        p.write_bytes("# SOC10020 Caf\xe9\nsupply\n".encode("latin-1"))  # 非 UTF-8
        raised = None
        try:
            prep.append_terms(p, ["demand"])
        except prep.GlossaryError as e:
            raised = e
        check("非 UTF-8 拒绝追加并报错", raised is not None)
        check("拒绝之后文件一个字节都没动",
              p.read_bytes() == "# SOC10020 Caf\xe9\nsupply\n".encode("latin-1"))

        print("\n9. 超长条目进 skipped_long（不静默丢）")
        p = fresh()
        r = prep.append_terms(p, ["a b c d e f", "marginal cost"])
        check("6 个 token 的被挡下", r.skipped_long == ["a b c d e f"], f"{r.skipped_long}")
        check("正常的多词术语照常进", r.added == ["marginal cost"])

        # ================================================ 10. 反幻觉复核的归一化
        print("\n10. ⭐ `_verify` 的对称归一化（规格就是这 12 行）")
        # 该救的 —— 原文 / 候选。前 6 行是本机实测的误杀，第 7 行是**本模块 prompt
        # 自己要求 LLM 做的剥离**（Ch 14: X → X），不改就会被自己的复核判死。
        for text, cand in [
            ("elasticities", "elasticity"),
            ("elasticity", "elasticities"),          # ⚠️ 反方向：单向剥 s/es 救不了
            ("cost-benefit", "cost benefit"),
            ("cost benefit", "cost-benefit"),
            ("marginal utilities", "marginal utility"),
            ("Marginal Cost", "marginal cost"),
            ("price elasticity of demand", "price elasticities of demand"),  # 词在中间
        ]:
            check(f"不误杀：{text[:34]!r} <- {cand!r}", prep._verify(cand, text))

        # 不该救的
        corpus = ("Elasticities vary along the curve; cost-benefit tradeoffs matter. "
                  "The supply is fixed.")
        for cand in ["elasticity of supply", "cost benefit analysis", "marginal utility",
                     "zzz nonexistent"]:
            check(f"不放水（语料里没有）：{cand!r}", not prep._verify(cand, corpus))

        # ⭐ 词干护栏：词干太短不许剥，否则 `bus` 命中 `buy`
        check("护栏：bus 不命中 buy", not prep._verify("bus", "the buy order"))
        check("护栏：bus 不命中 busy", not prep._verify("bus", "the busy street"))
        # ⭐ 裸子串的放水模式（实现时发现的）：`"cost" in "costly"` 为真
        check("护栏：cost 不命中 costly（裸子串会中招）",
              not prep._verify("cost", "the costly thing"))
        check("但 : curve 应该命中 curves（单复数）",
              prep._verify("curve", "the curves thing"))

        # ================================================ 11. prepare() 装配
        print("\n11. ⭐ `prepare()` 装配（全链路零网络：注入假 chat 和假 build）")
        import build_notes
        import zipfile
        nsd = ('xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
               'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"')
        _saved = (build_notes.NOTES_FILE, build_notes.AUTO_FILE)
        build_notes.NOTES_FILE = tmp / "term_notes.json"
        build_notes.AUTO_FILE = tmp / "auto.json"
        try:
            def _sp(ph, t):
                nv = (f'<p:nvSpPr><p:nvPr><p:ph type="{ph}"/></p:nvPr></p:nvSpPr>' if ph
                      else '<p:nvSpPr><p:nvPr/></p:nvSpPr>')
                return (f'<p:sp>{nv}<p:txBody><a:bodyPr/>'
                        f'<a:p><a:r><a:t>{t}</a:t></a:r></a:p></p:txBody></p:sp>')
            deck = tmp / "w5.pptx"
            with zipfile.ZipFile(deck, "w") as z:
                z.writestr("ppt/slides/slide1.xml",
                           '<?xml version="1.0"?><p:sld ' + nsd + '><p:cSld><p:spTree>'
                           + _sp("title", "Opportunity Cost and Elasticity")
                           + _sp("body", "The elasticity of demand measures responsiveness.")
                           + _sp("body", "Marginal utility falls as consumption rises.")
                           + '</p:spTree></p:cSld></p:sld>')
                z.writestr("ppt/slides/slide2.xml",
                           '<?xml version="1.0"?><p:sld ' + nsd + '><p:cSld><p:spTree>'
                           + _sp("title", "Elasticities in practice")
                           + _sp("body", "Elasticity varies; marginal costs matter too.")
                           + '</p:spTree></p:cSld></p:sld>')

            gdir = tmp / "gl"; gdir.mkdir()
            (gdir / "ECON99999.txt").write_text("# ECON99999\nsupply\n", encoding="utf-8")
            CAND = {"candidates": [
                {"term": "opportunity cost", "confidence": 0.94, "why": "标题"},
                {"term": "elasticity", "confidence": 0.90, "why": "反复"},
                {"term": "marginal utility", "confidence": 0.85, "why": "定义"},
                {"term": "supply", "confidence": 0.99, "why": "已在表里"},
                {"term": "quantum flux capacitor", "confidence": 0.99, "why": "编造"},
            ]}
            calls = []
            def chat(s, u):
                calls.append(len(u))
                return CAND
            def run(**kw):
                return prep.prepare("ECON99999", [deck], glossary_dir=gdir,
                                    state_path=tmp / "st" / "prep-state.json",
                                    chat=chat, build_fn=lambda c: None, **kw)

            r = run()
            gpath = gdir / "ECON99999.txt"
            check("aborted 为空", r.aborted == "", f"{r.aborted!r}")
            check("已验证的词进了表",
                  sorted(r.added) == ["elasticity", "marginal utility", "opportunity cost"],
                  f"{r.added}")
            check("⭐ 编造的候选没进表，且进了 not_added",
                  "quantum flux capacitor" in r.not_added
                  and "quantum flux capacitor" not in r.added)
            check("⭐ 已在表里的词报成 skipped_existing（不是 not_added）",
                  r.skipped_existing == ["supply"], f"{r.skipped_existing}")
            check("首行 `# ECON99999` 原样", gpath.read_text(encoding="utf-8").splitlines()[0]
                  == "# ECON99999")
            check("count/spread 是按**子串搜索**算的，不是 0",
                  all(c.count > 0 for c in r.candidates if c.verified),
                  f"{[(c.term, c.count, c.spread) for c in r.candidates]}")
            check("state 文件记下了追加过的词",
                  set(json.loads((tmp / "st" / "prep-state.json").read_text(
                      encoding="utf-8"))["appended"]) >= {"elasticity", "opportunity cost"})

            print("\n    幂等 / 墓碑")
            snap = gpath.read_bytes()
            check("再跑一次 added 为空", run().added == [])
            check("再跑一次文件一个字节没变", gpath.read_bytes() == snap)
            gpath.write_text("\n".join(l for l in gpath.read_text(encoding="utf-8").splitlines()
                                       if l.strip() != "elasticity") + "\n", encoding="utf-8")
            run()
            check("⭐ 墓碑：手工删掉的词重跑不复活",
                  "elasticity" not in gpath.read_text(encoding="utf-8").split())

            print("\n    ⭐ 状态文件损坏时**不许覆盖**（墓碑丢了 = 删过的词永久复活）")
            spo = tmp / "st_corrupt" / "s.json"
            spo.parent.mkdir(parents=True, exist_ok=True)
            spo.write_text('{"appended": {这不是合法 JSON', encoding="utf-8")
            before_corrupt = spo.read_bytes()
            r5 = prep.prepare("ECON99999", [deck], glossary_dir=gdir, state_path=spo,
                              chat=chat, build_fn=lambda c: None)
            check("⭐ 损坏的 state 一个字节都没被覆盖", spo.read_bytes() == before_corrupt)
            check("但 glossary 照常追加（坏的是墓碑，不该拖垮主链路）", r5.aborted == "",
                  f"aborted={r5.aborted!r}")

            print("\n    ⭐ 上限的交互（min(max_auto, max_total − 现有)，负数取 0）")
            gpath.write_text("# ECON99999\na\nb\nc\nd\ne\n", encoding="utf-8")
            r2 = prep.prepare("ECON99999", [deck], glossary_dir=gdir,
                              state_path=tmp / "st2" / "s.json", chat=chat,
                              build_fn=lambda c: None, max_auto=40, max_total=7)
            check("现有 5 条 + max_total=7 -> 只加 2 条（不是加满 40）",
                  len(r2.added) == 2 and r2.glossary_total == 7, f"added={r2.added}")
            r3 = prep.prepare("ECON99999", [deck], glossary_dir=gdir,
                              state_path=tmp / "st3" / "s.json", chat=chat,
                              build_fn=lambda c: None, max_auto=40, max_total=5)
            check("已经到顶 -> 一条都不加（负数取 0，不是负预算）", r3.added == [],
                  f"added={r3.added}")

            print("\n    aborted 的取值（每个都要有出口）")
            def bad(s, u):
                raise RuntimeError("网络断了")
            # ⚠️ 快照要取在**这次调用之前** —— 上面 r2/r3 已经把文件改过了，
            #    拿最初那份内容当期望值会误报（这条第一版就是这么写错的）。
            before_bad = gpath.read_bytes()
            rb = prep.prepare("ECON99999", [deck], glossary_dir=gdir,
                              state_path=tmp / "st4" / "s.json", chat=bad,
                              build_fn=lambda c: None)
            check("chat 抛异常 -> aborted='llm'", rb.aborted == "llm", f"{rb.aborted!r}")
            check("⭐ aborted 时一个文件都没动", gpath.read_bytes() == before_bad)
            check("课号为空 -> 'no_course'",
                  prep.prepare("", [deck], glossary_dir=gdir, state_path=tmp / "x" / "s.json",
                               chat=chat, build_fn=lambda c: None).aborted == "no_course")
            check("没有文件 -> 'no_files'",
                  prep.prepare("X", [], glossary_dir=gdir, state_path=tmp / "x" / "s.json",
                               chat=chat, build_fn=lambda c: None).aborted == "no_files")
            bad_pdf = tmp / "broken.pdf"
            bad_pdf.write_bytes(b"not a pdf" * 50)
            check("全部文件打不开 -> 'all_files_failed'",
                  prep.prepare("X", [bad_pdf], glossary_dir=gdir,
                               state_path=tmp / "x" / "s.json", chat=chat,
                               build_fn=lambda c: None).aborted == "all_files_failed")

            print("\n    ⭐ 有界乘子：可断言的性质")
            def _row(t, c, ratio, title):
                return dict(term=t, confidence=c, verified=True, count=1, spread=1,
                            spread_ratio=ratio, title_hits=title, why="")
            # ⚠️ **阈值从常量算，不抄数字**。抄死一个 21.6% 的话，把 TITLE_MULT
            #    从 1.1 调到 1.25 之后测试照样绿，而它断言的性质已经不成立了 ——
            #    一条断言着假性质的测试比没有更坏。（同 CLAUDE.md 讲 test_panel.py
            #    那条「写死 sha256 换台 Mac 必假失败」。）
            thr = prep.SPREAD_MULT_HI * prep.TITLE_MULT / (prep.SPREAD_MULT_LO * 1.0)
            hi = _row("high", 0.90, 0.0, 0)                  # 最低乘子
            lo = _row("low", 0.90 / thr * 0.99, 1.0, 1)      # 最高乘子，差 > 阈值
            check(f"置信度相差 >{(thr - 1) * 100:.1f}% -> 顺序不可能被频率/标题翻盘",
                  prep._rank([hi, lo])[0]["term"] == "high", f"翻盘阈值 = {thr:.4f}")
            lo2 = _row("low2", 0.90 / thr * 1.05, 1.0, 1)    # 差 < 阈值
            check("差 <21.6% 时**确实会**被翻盘（证明这条乘子不是死的）",
                  prep._rank([_row("hi2", 0.90, 0.0, 0), lo2])[0]["term"] == "low2")
        finally:
            build_notes.NOTES_FILE, build_notes.AUTO_FILE = _saved

        bad = [n for n, ok in RESULTS if not ok]
        print(f"\n{'=' * 62}")
        print(f"{len(RESULTS) - len(bad)}/{len(RESULTS)} 通过")
        if bad:
            print("失败：")
            for n in bad:
                print(f"  ❌ {n}")
        return 1 if bad else 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
