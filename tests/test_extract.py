#!/usr/bin/env python3
"""extract.py 的判据。

跑法: ClassLive.app/Contents/MacOS/python tests/test_extract.py   (或 pytest tests/)

语料是作者本机的真实课件（`~/UCD`）。**找不到就跳过那一条并说明，不假装通过。**
本测试**只读**：合成 PPTX 写在 `tempfile.mkdtemp()` 里，绝不碰仓库里的任何文件。

## 每一条都对着一个具体的失败

不凑覆盖率。最要紧的几条：run 粘连、**表格整块丢失**（表格在 `<p:graphicFrame>`
而不在 `<p:sp>`）、版式噪声污染、坏文件静默变空、扫描件静默变空。
⚠️ **别在这里写「共 N 条」** —— 那个数腐坏得比谁都快（本文件已经腐坏过一次）。
"""
from __future__ import annotations

import collections
import pathlib
import shutil
import sys
import tempfile
import zipfile

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import extract                                                     # noqa: E402

UCD = pathlib.Path.home() / "UCD"
RESULTS: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok))
    print(f"  {'✅' if ok else '❌'} {name}" + (f"\n      {detail}" if detail else ""))


SKIPPED: list[tuple[str, str]] = []


def skip(name: str, why: str) -> None:
    """跳过一条**依赖本机真实样本**的判据。

    ⚠️⚠️ **必须进统计**（2026-09-28 审查指出）：原来只打印一行，于是
       `~/UCD` 不存在时（换台机器 / CI / 样本被删）依赖真样本的**关键用例全部
       静默跳过**，结算行照样 `0/0 通过`、退出码 0 —— 一个"看起来绿、其实
       什么都没验"的绿。下面结算时会把跳过当成**没通过**处理。
    """
    SKIPPED.append((name, why))
    print(f"  ⚪ {name}（跳过：{why}）")


def first(pattern: str):
    hits = sorted(UCD.rglob(pattern)) if UCD.is_dir() else []
    hits = [h for h in hits if "/.Trash/" not in str(h)]
    return hits[0] if hits else None


def spread_of(blocks) -> collections.Counter:
    per_page = collections.defaultdict(set)
    for b in blocks:
        per_page[b.page].add(b.text)
    c: collections.Counter = collections.Counter()
    for texts in per_page.values():
        for t in texts:
            c[t] += 1
    return c


# ---- 合成 PPTX（内部接缝 `_pptx_blocks` 的测试夹具）----

_NSDECL = ('xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
           'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"')


def _sp(ph_type, text):
    # ph_type=None 表示**没有 <p:ph>** 的纯文本框（真实语料 696 个那一类）
    nv = (f'<p:nvSpPr><p:nvPr><p:ph type="{ph_type}"/></p:nvPr></p:nvSpPr>' if ph_type
          else '<p:nvSpPr><p:nvPr/></p:nvSpPr>')
    return (f'<p:sp>{nv}<p:txBody><a:bodyPr/>'
            f'<a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:txBody></p:sp>')


def _slide(*shapes):
    return ('<?xml version="1.0"?><p:sld ' + _NSDECL +
            '><p:cSld><p:spTree>' + "".join(shapes) + '</p:spTree></p:cSld></p:sld>')


def _make_pptx(path, slides):
    with zipfile.ZipFile(path, "w") as z:
        for i, xml in enumerate(slides, 1):
            z.writestr(f"ppt/slides/slide{i}.xml", xml)


def main() -> int:
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="cl_extract_"))
    try:
        print("\n1. PPTX 同段落 run 不粘连")
        f = first("ECON10740__Week2*.pptx")
        if f is None:
            skip("run 不粘", "找不到 ECON10740__Week2*.pptx")
        else:
            texts = [b.text for b in extract.extract(f, ocr=False).blocks]
            check("标题完整成句（含 'Communicating Ideas in Economics'）",
                  any("Communicating Ideas in Economics" in t for t in texts))
            check("没有把相邻 run 粘成 'EconomicsDr. Ciara Whelan'",
                  not any("EconomicsDr." in t for t in texts))

        print("\n2. 标题占位符被标成 title 档（并钉住 kind 表与说明表键集相同）")
        check("⭐ BLOCK_KINDS 与 KIND_MEANING 键集完全相同（新增 kind 不许漏说明）",
              set(extract.BLOCK_KINDS) == set(extract.KIND_MEANING),
              f"只在一侧={set(extract.BLOCK_KINDS) ^ set(extract.KIND_MEANING)}")
        if f is None:
            skip("title 档", "同上，找不到样本")
        else:
            kinds = {b.kind for b in extract.extract(f, ocr=False).blocks}
            check("出现了 kind='title'", "title" in kinds, f"出现的 kind={sorted(kinds)}")

        print("\n3. ⭐ 表格不漏（<p:graphicFrame>）")
        g = first("SDGs.pptx")
        if g is None:
            skip("表格", "找不到 SDGs.pptx")
        else:
            r = extract.extract(g, ocr=False)
            tbl = [b.text for b in r.blocks if b.kind == "table"]
            check("抽到了 kind='table' 的块", bool(tbl), f"共 {len(tbl)} 块")
            check("表格内容真的进来了（含 'End poverty in all its forms everywhere'）",
                  any("End poverty in all its forms everywhere" in t for t in tbl),
                  f"前两块：{tbl[:2]}")

        print("\n4. ⭐ 版式噪声：跑马灯的跨张数算得对，且不产生被排除的 kind")
        h = first("ECON10790__Chapter 14.pptx")
        if h is None:
            skip("版式噪声", "找不到 ECON10790__Chapter 14.pptx")
        else:
            # 跨张数由**消费者**（prep）算 —— extract 只保证 page 正确。
            # 所以这里验的正是「page 没有错位」这一条代理指标。
            r = extract.extract(h, ocr=False)
            sp = spread_of(r.blocks)
            hdr = [(t, c) for t, c in sp.most_common() if t.startswith("Ch 14")]
            ratio = (hdr[0][1] / r.stats.pages) if hdr else 0.0
            check(f"跑马灯跨张数 >90%（实测 {hdr[0][1] if hdr else 0}/{r.stats.pages}）",
                  ratio > 0.9, f"top={hdr[:2]}")
            bad = sorted({b.kind for b in r.blocks} - set(extract.BLOCK_KINDS))
            check("没有产出 BLOCK_KINDS 之外的 kind", not bad, f"越界={bad}")

        print("\n5. ⭐ 打不开的文件不静默")
        real = first("SOC10020__Picketty*.pdf") or first("*.pdf")
        cases = {
            "空文件": b"",
            "非 PDF（纯文本改名）": b"this is not a pdf at all\n" * 40,
            # ⚠️ 两个小问题（2026-09-28 审查指出）：① `read_bytes()[:8000]` 先把
            #    整个 PDF 读进内存再切片（`open().read(8000)` 就够）；
            #    ② 回退桩 `b"%PDF-1.4"` 是**完整**的一个头、不是"截断的" ——
            #    那测的是"只有头"这种坏输入，与这一档声称的对不上。
            "截断的真 PDF": ((real.open("rb").read(8000) if real
                              else (b"%PDF-1.4\n" + b"%" * 400)[:60])),
        }
        for i, (label, data) in enumerate(cases.items()):
            p = tmp / f"bad{i}.pdf"
            p.write_bytes(data)
            r = extract.extract(p)
            check(f"{label} -> status != 'ok' 且有 error",
                  r.status != "ok" and bool(r.error), f"status={r.status} err={r.error[:60]}")
            # ⚠️ 光有 status 不够：外层 try/except 也会给出非 ok —— 那样就把
            #    「显式判 doc is None」的好处（**说人话的错误**）漏掉了。
            #    所以再钉一条：错误必须是句子，不是 Python 内部结构。
            check(f"{label} -> error 是说人话的句子，不是 Python 异常内脏",
                  "object has no attribute" not in r.error
                  and not r.error.startswith(("AttributeError", "TypeError")),
                  f"err={r.error[:60]}")

        print("\n6. 全空 -> status='empty'（不是 'ok' 空表）")
        only_num = tmp / "blank.pptx"
        _make_pptx(only_num, [_slide(_sp("sldNum", "2"), _sp(None, "   "))])
        r = extract.extract(only_num)
        check("只有页码/空白的课件 -> 'empty'", r.status == "empty",
              f"status={r.status} blocks={len(r.blocks)}")

        print("\n7. 无 <p:ph> 的文本框标成 'textbox'（不再混进 body）")
        lib = first("ECON10740__Library session*.pptx")
        if lib is None:
            skip("textbox 标签", "找不到 Library session 那份")
        else:
            blocks = extract.extract(lib, ocr=False).blocks
            check("有 kind='textbox' 的块", any(b.kind == "textbox" for b in blocks))
            check("图库版权行被标成 textbox 而不是 body",
                  any(b.kind == "textbox" and "licensed under" in b.text for b in blocks))

        print("\n8. stats 能逐因报出跳过了什么（审计用）")
        mixed = tmp / "mixed.pptx"
        _make_pptx(mixed, [_slide(_sp("sldNum", "7"), _sp("title", "Real Title"),
                                  _sp("ftr", "University Name"), _sp("body", "content"))])
        st = extract.extract(mixed).stats
        check("skipped_by_reason 记到 sldNum", st.skipped_by_reason.get("sldNum") == 1,
              f"reasons={st.skipped_by_reason}")
        check("skipped_by_reason 记到 ftr", st.skipped_by_reason.get("ftr") == 1)
        check("被排除的页码/页脚文字没有进 blocks",
              "7" not in [b.text for b in extract.extract(mixed).blocks]
              and "University Name" not in [b.text for b in extract.extract(mixed).blocks])
        check("title/body 照常进来",
              {b.kind for b in extract.extract(mixed).blocks} == {"title", "body"},
              f"kinds={sorted({b.kind for b in extract.extract(mixed).blocks})}")

        print("\n9. ⭐ 扫描件（无文字层的页）走 OCR 且标成 'ocr'")
        if real is None:
            skip("OCR 兜底", "没有可用的扫描件样本")
        else:
            on = extract.extract(real, ocr=True)
            off = extract.extract(real, ocr=False)
            check("开 OCR 时报出了 ocr_pages > 0", on.stats.ocr_pages > 0,
                  f"ocr_pages={on.stats.ocr_pages}")
            check("OCR 出来的块 kind='ocr'", any(b.kind == "ocr" for b in on.blocks))
            check("关 OCR 时同文件字符更少（证明 OCR 真的加了东西）",
                  on.chars > off.chars, f"on={on.chars} off={off.chars}")

        print("\n10. DOCX（2026-09-28 进支持集）")
        W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        docx = tmp / "s.docx"
        with zipfile.ZipFile(docx, "w") as z:
            z.writestr("word/document.xml", f"""<?xml version="1.0"?>
<w:document xmlns:w="{W}"><w:body>
<w:p><w:r><w:t>ECON10740 Exploring Economics</w:t></w:r></w:p>
<w:p><w:r><w:t>Week 3 </w:t></w:r><w:r><w:t>Lecture Notes</w:t></w:r></w:p>
<w:tbl><w:tr><w:tc><w:p><w:r><w:t>SDG</w:t></w:r></w:p></w:tc>
<w:tc><w:p><w:r><w:t>Goal 4</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
<w:p><w:r><w:t>AFTER_THE_TABLE</w:t></w:r></w:p>
</w:body></w:document>""".encode())

        r = extract.extract(docx)
        texts = [b.text for b in r.blocks]
        check("DOCX 抽得到字", r.status == "ok" and r.chars > 0, f"{r.status} {r.chars}")
        check("同段多个 run 不粘连（按 w:p 分）",
              "Week 3 Lecture Notes" in texts, str(texts[:3]))
        check("表格行成块、单元格用 ' | ' 连",
              any(b.kind == "table" and " | " in b.text for b in r.blocks),
              str([b.text for b in r.blocks if b.kind == "table"]))
        # ⭐ 这条是**区分性**的：把实现写成「两次 iter()`（先全部表格再全部段落）」
        #    —— 那是 pptx 那条路的写法 —— 这条就会红。见 `_docx_blocks` 的注释。
        # ⚠️⚠️ 两处都会**抛 ValueError 而不是报 ❌**（2026-09-28 审查指出）：
        #    · `texts.index(…)` 找不到那个标记就抛；
        #    · `max(…)` 在**一个表格块都没有**时抛空序列 —— 而"表格抽不出来"
        #      恰恰就是这条判据要守的那个失败 → 测试**崩掉**，后面所有断言不跑，
        #      汇总行也没有。先算好、判存在性，再比大小。
        _tbl = [i for i, b in enumerate(r.blocks) if b.kind == "table"]
        check("⭐ 顺序保真：表格后面的段落仍在表格之后",
              ("AFTER_THE_TABLE" in texts and bool(_tbl)
               and texts.index("AFTER_THE_TABLE") > max(_tbl)),
              f"tbl={_tbl} texts={texts}")
        check("DOCX 没有页的概念 -> page 一律 1",
              all(b.page == 1 for b in r.blocks))

        bad_docx = tmp / "notzip.docx"
        bad_docx.write_bytes(b"definitely not a zip")
        rb = extract.extract(bad_docx)
        check("非 zip 的 .docx -> unreadable，**异常没逃出去**",
              rb.status == "unreadable" and "ZipFile" in rb.error, f"{rb.status} {rb.error}")

        noxml = tmp / "noxml.docx"
        with zipfile.ZipFile(noxml, "w") as z:
            z.writestr("hello.txt", b"hi")
        rn = extract.extract(noxml)
        check("zip 里没有 word/document.xml -> unreadable",
              rn.status == "unreadable" and "document.xml" in rn.error,
              f"{rn.status} {rn.error}")

        print("\n11. expand()：文件夹取一层（拖拽落点用）")
        d = tmp / "mats" / "sub"
        d.mkdir(parents=True)
        (tmp / "mats" / "a.pdf").write_bytes(b"")
        (tmp / "mats" / "b.pptx").write_bytes(b"")
        (tmp / "mats" / "c.txt").write_bytes(b"")
        (d / "deep.pdf").write_bytes(b"")           # 第二层：**不该**被取到

        ok1, drop1 = extract.expand([str(tmp / "mats")])
        names = sorted(pathlib.Path(p).name for p in ok1)
        check("文件夹里支持的都被取到", names == ["a.pdf", "b.pptx"], str(names))
        # ⭐ 变异点：把 `iterdir()` 换成 `rglob()` 这条就红。
        check("⭐ 只展开一层，不递归（子文件夹里的不取）",
              "deep.pdf" not in names, str(names))
        check("文件夹本身不算 dropped", drop1 == [], str(drop1))

        empty_dir = tmp / "nothing"
        empty_dir.mkdir()
        (empty_dir / "x.txt").write_bytes(b"")
        ok2, drop2 = extract.expand([str(empty_dir)])
        check("文件夹里一个能抽的都没有 -> 整体进 dropped",
              ok2 == [] and drop2 == [str(empty_dir)], f"{ok2} {drop2}")

        ok3, drop3 = extract.expand([str(docx), "/nope/x.txt", "/nope/y.pdf"])
        check("散文件：不支持的进 dropped",
              str(docx) in ok3 and drop3 == ["/nope/x.txt"], f"{ok3} {drop3}")
        # ⚠️ **不存在的路径只要后缀支持就放行** —— 这是刻意的，不是漏判：
        #    `expand` 只按后缀认（那正是 `is_supported` 的口径），**存在性是
        #    `extract()` 的活**（它会返回 status="unreadable" 并写人话）。
        #    在这里多查一次 `exists()` 就等于把「能不能收」的判据写成两份。
        check("⚠️ 不存在的路径：按后缀放行（存在性归 extract() 管）",
              "/nope/y.pdf" in ok3 and "/nope/y.pdf" not in drop3,
              f"{ok3} {drop3}")
        check("expand 保持输入顺序", [pathlib.Path(p).name for p in
                                     extract.expand(
                                         [str(tmp / "mats" / "b.pptx"),
                                          str(tmp / "mats" / "a.pdf")])[0]]
              == ["b.pptx", "a.pdf"])

        check("is_supported 认 .docx（且它是支持集的唯一定义点）",
              extract.is_supported("x.docx") and not extract.is_supported("x.txt"))

        bad = [n for n, ok in RESULTS if not ok]
        print(f"\n{'=' * 62}")
        print(f"{len(RESULTS) - len(bad)}/{len(RESULTS)} 通过")
        if bad:
            print("失败：")
            for n in bad:
                print(f"  ❌ {n}")
        if SKIPPED:
            print(f"\n⚠️ 另有 {len(SKIPPED)} 条**跳过**（依赖本机真实样本）：")
            for _n, _w in SKIPPED:
                print(f"  ⚪ {_n} —— {_w}")
        # ⚠️⚠️ **"没跑全"与"跑过且通过"必须分开**（2026-09-28 审查指出）：
        #    有跳过（或一条判据都没跑到）时返回非零 —— 否则换台机器跑出来的
        #    `0/0 通过` 会被当成真绿。这条是本仓库「负结果不许读起来像穷尽」
        #    在测试结算行上的同一形状。
        if SKIPPED or not RESULTS:
            print("⚠️ 有跳过 / 根本没跑到判据 —— 这次**不足以**当作通过。")
            return 1
        return 1 if bad else 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
