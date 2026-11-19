"""桌面 `extract.py` 的 **PPTX / DOCX** 那条路，**由 `web/scripts/sync-core.py` 按名字生成，勿手改**。

只切「纯 `zipfile` + 标准库 XML」那半：`_slide_blocks` / `_paragraphs`（PPTX，含表格与占位符口径）
与 `_docx_blocks` / `_w_para` / `_w_paras`（DOCX）。两种情况的口径都照桌面，一个字没改。

没带进来（`--check` 不覆盖它们的漂移，改桌面这几个名字不会让这里变红）：

- `_pdf_blocks` / `_pdf_paragraphs` / `_ensure_vision` / `_ocr_pdf_page` —— PDFKit + Vision，
  浏览器里没有。网页版的 PDF 走 `web/src/lib/prep/pdf.ts`（pdf.js），产出的块与 `Block` 同形。
- `is_supported` / `expand` / `extract`（磁盘上的路径与进度条，网页版在 TS 里装配）。

所以网页版的 PDF 与 PPTX **不是同一条实现**：PPTX/DOCX 与桌面逐块相同（有 CPython 对照测试），
PDF 只是「形状相同」。`--check` 只能盯住这里切出来的部分，PDF 那条没法用 Python 钉。
"""
from __future__ import annotations

import pathlib
import re
import typing

BLOCK_KINDS = ("title", "subtitle", "body", "textbox", "table", "notes", "ocr")

_PH_TEXT = {"title": "title", "ctrTitle": "title", "subTitle": "subtitle", "body": "body"}
_PH_EXCLUDED = frozenset({
    "dt", "sldNum", "ftr", "hdr",                # 版式 / 页眉页脚
    "obj", "chart", "tbl", "clipArt", "dgm",     # 非文本对象
    "media", "sldImg", "pic",
})

_NS_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_NS_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"

_NS_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_SLIDE_RE = re.compile(r"ppt/slides/slide(\d+)\.xml$")
_NOTES_RE = re.compile(r"ppt/notesSlides/notesSlide(\d+)\.xml$")

KIND_MEANING = {
    "title":    "标题档，**这些词通常最值钱**（教授精心选过的关键词）",
    "subtitle": "副标题，同标题档",
    "body":     "正文",
    "textbox":  "没有占位符的纯文本框，**混杂**：可能是正文，也可能是版式文字、图片版权行、裸 URL",
    "table":    "表格的**一行**，单元格之间用 ` | ` 连",
    "notes":    "备注页（讲者备注）—— 真实语料里只占 2% 的字，**值钱的少**",
    "ocr":      "扫描页 OCR 出来的文字 —— **没有版式信息、带识别噪声**",
}


def _numbered(names: list, pat) -> list:
    """zip 条目名 → `[(页号, 名字)]`，按页号升序。

    ⚠️ 不靠 `namelist()` 自己的顺序 —— 那是 zip 中央目录的顺序，不保证等于页序。
    （抽 slides 与 notes 时这段一模一样，两处都调它。）
    """
    return sorted(((int(m.group(1)), n) for n in names if (m := pat.match(n))),
                  key=lambda t: t[0])


class Block(typing.NamedTuple):
    text: str
    kind: str
    page: int


class ExtractStats(typing.NamedTuple):
    """审计数字。存在的理由：抽取最容易「静默丢内容」，所以每份文件都要能报出读到了什么、跳过了什么。"""
    pages: int
    shapes_seen: int
    shapes_skipped: int
    skipped_by_reason: dict
    ocr_pages: int
    by_kind: dict


class ExtractResult(typing.NamedTuple):
    path: pathlib.Path
    status: str                       # "ok" | "empty" | "unreadable" | "unsupported"
    blocks: list
    chars: int
    stats: ExtractStats
    error: str = ""

#: 单个 zip 成员读进内存的上限（2026-10-01 审查 F11）：课件是用户自己拖的，正常
#: `slideN.xml` / `document.xml` 远小于它；超过大概率是 **zip 炸弹**。超了不整块读。
_MAX_XML_BYTES = 64 * 1024 * 1024

def _pptx_blocks(path: pathlib.Path, ocr: bool, on_progress, max_pages=None):
    import zipfile

    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        slides = _numbered(names, _SLIDE_RE)
        if not slides:
            raise RuntimeError("zip 里没有 ppt/slides/slideN.xml —— 不是 PPTX？")

        seen = skipped = 0
        reasons: dict = {}
        blocks: list = []
        total = len(slides)
        lim = total if not max_pages else min(total, max_pages)
        for done, (num, name) in enumerate(slides[:lim], 1):
            # ⚠️ 成员大小上限（2026-10-01 审查 F11）：zip 炸弹 / 畸形文件不许整块
            #    进内存 —— 超限的这一页算 `skipped`（诚实进 stats），不是整份失败。
            if zf.getinfo(name).file_size > _MAX_XML_BYTES:
                skipped += 1
                reasons["member_too_big"] = reasons.get("member_too_big", 0) + 1
                if on_progress:
                    on_progress(done, lim)
                continue
            root = _parse(zf.read(name))
            b, s = _slide_blocks(root, num)
            blocks += b
            seen += s["seen"]; skipped += s["skipped"]
            for k, v in s["reasons"].items():
                reasons[k] = reasons.get(k, 0) + v
            if on_progress:
                on_progress(done, lim)

        # 备注页：结构上完全可达、成本≈0，但真实语料里只有 2.03% 的字来自它
        # （`docs/RESEARCH-p3-extract.md` §5.2）→ 顺手抽，不为它做任何设计。
        # ⚠️ 页码按文件名后缀映射到 slide —— 这是常见约定，不是规范保证。
        # ⚠️ `max_pages` 截断时**整段跳过**：备注不是页，且截断后那份映射会错位。
        if not max_pages:
            for num, name in _numbered(names, _NOTES_RE):
                if zf.getinfo(name).file_size > _MAX_XML_BYTES:
                    continue                                  # 同上（F11）：不读进内存
                root = _parse(zf.read(name))
                for para in _paragraphs(root):
                    blocks.append(Block(para, "notes", num))

    stats = ExtractStats(pages=total, shapes_seen=seen, shapes_skipped=skipped,
                         skipped_by_reason=reasons, ocr_pages=0,
                         by_kind=_count_kinds(blocks))
    return blocks, stats


def _docx_blocks(path: pathlib.Path, ocr: bool, on_progress, max_pages=None):
    """DOCX → 文本块。与 PPTX 同属 OOXML，所以还是 `zipfile` + 标准库 XML，**零新依赖**。

    ⚠️ **DOCX 没有"页"。** 它是流式文档，分页由渲染器按纸张大小和字体算出来，
       **文件里不存**。所以 `Block.page` 一律 `1`、`stats.pages` 也是 `1`（"一份文档"）。
       模块头那条「`page` 是 slide spread 的唯一来源」对 docx 是**平凡成立**的。
    ⚠️ `ocr` 与 `max_pages` 对 docx **都无意义**（没有页面图像、没有页）——
       签名保留只为与另两条路一致，不为它加特例分支。
    ⚠️ 段落用 `_w_para`（`w:` 命名空间），**不能借 pptx 的 `_paragraphs`** ——
       后者按 `a:p`/`a:t` 取，对 docx 一个都命中不了（且**不报错**，只是抽到空）。
    """
    import zipfile

    with zipfile.ZipFile(path) as zf:
        try:
            info = zf.getinfo("word/document.xml")
        except KeyError:
            raise RuntimeError("zip 里没有 word/document.xml —— 不是 DOCX？")
        # ⚠️ 大小上限（2026-10-01 审查 F11）：同 pptx 那条 —— 整块读进来之前先拦。
        if info.file_size > _MAX_XML_BYTES:
            raise RuntimeError("word/document.xml 超过 64 MB —— 不是正常 DOCX？")
        raw = zf.read("word/document.xml")
    body = _parse(raw).find(_NS_W + "body")
    if body is None:
        return [], ExtractStats(1, 0, 0, {}, 0, {})

    # ⚠️ **按 body 的直接子元素顺序走一遍**（而不是 `iter(tbl)` 与 `iter(p)` 各扫一次）——
    #    两次扫描会把全部表格排到全部段落前面（pptx 那条路就是这么写的，它认这个代价）。
    #    这里能便宜地保序，就保。
    blocks: list = []
    seen = 0
    for el in body:
        if el.tag == _NS_W + "p":
            seen += 1
            t = _w_para(el)
            if t:
                blocks.append(Block(t, "body", 1))
        elif el.tag == _NS_W + "tbl":
            # 表格按 `<w:tr>` 行成块、行内单元格 `" | "` 连 —— 与 pptx 那条**同一口径**
            # （拆到单元格会丢上下文，整表一块会丢行内共现）。
            for tr in el.iter(_NS_W + "tr"):
                cells = [" ".join(_w_paras(tc)) for tc in tr.iter(_NS_W + "tc")]
                line = " | ".join(c for c in cells if c).strip()
                if line:
                    seen += 1
                    blocks.append(Block(line, "table", 1))

    stats = ExtractStats(pages=1, shapes_seen=seen, shapes_skipped=0,
                         skipped_by_reason={}, ocr_pages=0,
                         by_kind=_count_kinds(blocks))
    return blocks, stats


def _w_para(el) -> str:
    """一个 `<w:p>` 的纯文本：段内所有 `<w:t>` 拼起来。

    ⚠️ 与 `_paragraphs()`（pptx）同一条纪律 —— **按段落分**。只 `.iter(w:t)` 会把
       不同段落的 run 粘成一行。
    """
    return "".join(t.text or "" for t in el.iter(_NS_W + "t")).strip()


def _w_paras(el) -> list:
    """`el` 里所有 `<w:p>` 的文本（表格单元格走它）。"""
    return [s for s in (_w_para(p) for p in el.iter(_NS_W + "p")) if s]


def _parse(raw: bytes):
    import xml.etree.ElementTree as ET
    return ET.fromstring(raw)


def _slide_blocks(root, page: int):
    """一张 slide → (blocks, 计数)。这是**内部接缝**：测试直接打它，不必往仓库塞真课件。"""
    blocks: list = []
    seen = skipped = 0
    reasons: dict = {}

    def _note_skip(kind: str) -> None:
        nonlocal skipped
        skipped += 1
        reasons[kind] = reasons.get(kind, 0) + 1

    # ---- <p:sp>：普通形状 ----
    for sp in root.iter(_NS_P + "sp"):
        seen += 1
        ph = sp.find(".//" + _NS_P + "ph")
        if ph is None:
            kind = "textbox"          # ⚠️ 不再混进 body —— 真实语料 696 个，450 个带文字
        else:
            ptype = ph.get("type") or "body"
            if ptype in _PH_EXCLUDED:
                _note_skip(ptype)
                continue
            kind = _PH_TEXT.get(ptype, "body")   # 未知类型 fail-open：宁可多抽，不可静默丢
        for para in _paragraphs(sp):
            blocks.append(Block(para, kind, page))

    # ---- <p:graphicFrame>：表格 / 图表 / 嵌入对象 ----
    # ⚠️ 这一段就是「只遍历 <p:sp> 会静默丢内容」的修法（真实语料 19/34 带文字）。
    for gf in root.iter(_NS_P + "graphicFrame"):
        seen += 1
        ph = gf.find(".//" + _NS_P + "ph")
        if ph is not None and (ph.get("type") or "") in _PH_EXCLUDED:
            _note_skip(ph.get("type"))
            continue
        tbl = gf.find(".//" + _NS_A + "tbl")
        if tbl is not None:
            # 按 <a:tr> 行成块，行内单元格 `" | "` 连 —— 表头行因此是独立一块，
            # 行内共现也保住了（拆到单元格会丢上下文，整表一块会丢行内共现）。
            # ⚠️⚠️ **必须是 `findall`（直接子节点），不能是 `iter`（递归）** ——
            #    `iter` 会把**嵌套表格**的行和单元格再数一遍：
            #    外层 `tr.iter("tc")` 已经把它们（作为后代）算进这一行，
            #    内层 `tr` 又被外层 `tbl.iter("tr")` 收一次 → **同一段文字进两次块**，
            #    而那些块的去向是**术语候选池**。
            #    （2026-09-28 OCR 发现，2026-09-30 回原码核实为真。）
            #    ⚠️ 内容不会因此丢：内层表格的段落仍在外层单元格的
            #       `_paragraphs(tc)` 里（那个是**故意**递归的，见它的 docstring）。
            for tr in tbl.findall(_NS_A + "tr"):
                cells = []
                for tc in tr.findall(_NS_A + "tc"):
                    txt = " ".join(_paragraphs(tc))
                    cells.append(txt)
                line = " | ".join(c for c in cells if c).strip()
                if line:
                    blocks.append(Block(line, "table", page))
        else:
            # 非表格（SmartArt 等）：没有 <a:tr>，退化成按段落。
            for para in _paragraphs(gf):
                blocks.append(Block(para, "table", page))

    return blocks, {"seen": seen, "skipped": skipped, "reasons": reasons}


def _paragraphs(el) -> list:
    """按 `<a:p>`（段落）分段，段内拼 `<a:t>` run。

    ⚠️ 必须按段落分 —— 只 `.iter()` 收 `<a:t>` 会把同段落的多个 run **粘在一起**，
    实测输出过 `Ideas in EconomicsDr. Ciara Whelan`（三行标题糊成一行）。
    """
    out = []
    for para in el.iter(_NS_A + "p"):
        t = "".join(r.text or "" for r in para.iter(_NS_A + "t")).strip()
        if t:
            out.append(t)
    return out


def _count_kinds(blocks) -> dict:
    out: dict = {}
    for b in blocks:
        out[b.kind] = out.get(b.kind, 0) + 1
    return out
