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


def skip(name: str, why: str) -> None:
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
            "截断的真 PDF": real.read_bytes()[:8000] if real else b"%PDF-1.4",
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
