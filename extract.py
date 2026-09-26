"""课件抽文本 —— PDF / PPTX → 带标签的文本块。**只读，永不写文件。**

这是 P3「开课前的准备」的第一环：`prep.py` 拿这里的 `Block` 去抽候选术语。

## 为什么不用第三方库

三样能力这台机器已经付过钱了：PDF 文字层（PDFKit）、PPTX 解包（stdlib `zipfile`）、
图像 OCR（Vision）。`pyobjc-framework-Quartz` 本来就在 `requirements.txt` 里，
所以**新增依赖 = 0**。实测 1336 页 PDF 4.29 秒、一门课的课件 ≤2 秒
（原始数据见 `docs/RESEARCH-p3-extract.md`）。

## ⚠️ 两个会静默丢内容的坑（都实测过，都在这份代码里堵了）

1. **表格住在 `<p:graphicFrame>`，不在 `<p:sp>`。** 真实语料 34 个 graphicFrame 里
   **19 个带文字**，而且内容很值钱（`Tourism economics` / `SDG Goal 4` / `Audience Purpose Evidence`）。
   只遍历 `<p:sp>` 会一个都抓不到 —— 不报错、不计数，只是内容少一块。
2. **PDFKit 对打不开的文件静默返回 `None`**（空文件 / 非 PDF / 截断的 PDF 实测全是 `None`，
   无异常）。必须显式判，否则坏文件会静默贡献 0 字符，看起来像「这份课件没有术语」。

## 标签口径：`kind` 是事实，不是权重

`kind` 只回答「这段文字住在哪种形状里」，**不含任何评分**。
「标题档该值多少分」是消费者（`prep.py`）的政策 —— 冻在这里的话，调权重就得动一个只负责读文件的模块。
（同 `panel.py` 的「配方唯一定义点」是两回事：那里是刻意的集中，这里是刻意的**不**集中。）

## 不变量

- **永不抛异常**（调用方传错类型是程序员错误，那可以抛）。
  坏输入一律走 `status != "ok"` + `error` 写人话 —— 所以调用方的循环可以没有 `try`。
- 顺序确定：按 `page` 升序，同页按文件里的原始顺序。同字节输入 → 同输出。
- `page` 是 **slide spread 的唯一来源**，必须正确，不能省。
- 只读：本模块不写任何东西。
"""
from __future__ import annotations

import pathlib
import re
import typing

# 允许的 kind 取值。"notes" 是 PPTX 备注页，"ocr" 是扫描页（OCR 出来的文字认不出层级）。
BLOCK_KINDS = ("title", "subtitle", "body", "textbox", "table", "notes", "ocr")

# `ST_PlaceholderType` 的完整取值（ISO/IEC 29500-4:2016 pml.xsd 的 simpleType，
# 16 条 enumeration）。**方案早期只列了 4 个，那是不完整的。**
#   进候选：title body ctrTitle subTitle
#   排除  ：dt sldNum ftr hdr（日期/页码/页眉页脚）obj chart tbl clipArt dgm media sldImg pic（非文本对象）
# ⚠️ 排除是「不抽」，不是「降权」—— 它们从语义上就不是术语，是版式元素。
_PH_TEXT = {"title": "title", "ctrTitle": "title", "subTitle": "subtitle", "body": "body"}
_PH_EXCLUDED = frozenset({
    "dt", "sldNum", "ftr", "hdr",                # 版式 / 页眉页脚
    "obj", "chart", "tbl", "clipArt", "dgm",     # 非文本对象
    "media", "sldImg", "pic",
})

_NS_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_NS_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
_SLIDE_RE = re.compile(r"ppt/slides/slide(\d+)\.xml$")
_NOTES_RE = re.compile(r"ppt/notesSlides/notesSlide(\d+)\.xml$")

# kind → 一句话含义。**它是这份表与提示词之间的唯一定义点**：
# `prep._prompt()` 用它生成那段说明，测试断言它与 `BLOCK_KINDS` 键集相同。
# ⚠️ 加一个 kind 而忘了写含义，测试会红 —— 这样「新增 kind 时提示词没跟上」在结构上不可能。
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


_SUPPORTED = {".pdf", ".pptx"}


def extract(path, *, ocr: bool = True, on_progress=None) -> ExtractResult:
    """把一份课件抽成带标签的文本块。

    `ocr=False` 关掉扫描页兜底（快，但扫描件会得到空文本）。
    `on_progress(done, total)` 在每页后回调一次 —— **由调用方决定放哪个线程**，
    本函数自己不起线程、不画界面。
    """
    p = pathlib.Path(path)
    empty_stats = ExtractStats(0, 0, 0, {}, 0, {})

    if p.suffix.lower() not in _SUPPORTED:
        return ExtractResult(p, "unsupported", [], 0, empty_stats,
                             f"不支持的格式：{p.suffix or '(无扩展名)'}")
    if not p.exists():
        return ExtractResult(p, "unreadable", [], 0, empty_stats, "文件不存在")

    try:
        if p.suffix.lower() == ".pdf":
            blocks, stats = _pdf_blocks(p, ocr, on_progress)
        else:
            blocks, stats = _pptx_blocks(p, ocr, on_progress)
    except Exception as e:                                    # noqa: BLE001
        # 抽取失败**不能**让整场 prep 挂掉，也不能静默 —— 说清是哪个文件、什么原因。
        return ExtractResult(p, "unreadable", [], 0, empty_stats,
                             f"{type(e).__name__}: {str(e)[:120]}")

    chars = sum(len(b.text) for b in blocks)
    if not blocks:
        return ExtractResult(p, "empty", [], 0, stats, "抽不出任何文字")
    return ExtractResult(p, "ok", blocks, chars, stats, "")


# ---------------------------------------------------------------- PDF

def _pdf_blocks(path: pathlib.Path, ocr: bool, on_progress):
    from Foundation import NSURL
    from Quartz import PDFKit

    doc = PDFKit.PDFDocument.alloc().initWithURL_(
        NSURL.fileURLWithPath_(str(path)))
    # ⚠️ 三种坏输入（空文件 / 非 PDF / 截断）实测**都是 None**，不抛异常。
    if doc is None:
        raise RuntimeError("PDFKit 打不开这个文件（损坏或不是 PDF）")
    # 加密 PDF：isLocked 为真时 string() 的行为本机没测到（语料里加密 0 份），
    # 所以按保守做法当「打不开」处理 —— 宁可报错也不静默给空文本。
    try:
        if doc.isLocked():
            raise RuntimeError("PDF 有密码保护，读不了")
    except AttributeError:
        pass                                                  # 老版本没这个方法，跳过

    n = doc.pageCount()
    blocks, ocr_pages = [], 0
    for i in range(n):
        page = doc.pageAtIndex_(i)
        text = (page.string() or "") if page is not None else ""
        if not text.strip() and ocr:
            got = _ocr_pdf_page(page)
            if got.strip():
                ocr_pages += 1
                blocks.append(Block(got.strip(), "ocr", i + 1))
                if on_progress:
                    on_progress(i + 1, n)
                continue
        # PDF 没有占位符结构，认不出层级 —— 全部 body。
        # （所以「标题加权」是 PPTX 独享的免费信号，见模块文档。)
        for para in _pdf_paragraphs(text):
            blocks.append(Block(para, "body", i + 1))
        if on_progress:
            on_progress(i + 1, n)

    stats = ExtractStats(pages=n, shapes_seen=n, shapes_skipped=0,
                         skipped_by_reason={}, ocr_pages=ocr_pages,
                         by_kind=_count_kinds(blocks))
    return blocks, stats


def _pdf_paragraphs(text: str) -> list:
    return [ln.strip() for ln in (text or "").splitlines() if ln.strip()]


# Vision 只在需要时加载；`objc.loadBundle` 把类放进我们自己的 dict，
# 不污染模块全局。⚠️ 没有 PyObjC 的 Vision 包装时**没有 selector metadata**，
# `performRequests:error:` 的 out-param 不会自动解包（报 "cannot unpack non-iterable bool"）
# —— 必须手工补一行 registerMetaDataForSelector。这是实测踩到的。
_vision = None


def _ensure_vision():
    global _vision
    if _vision is not None:
        return _vision
    import objc
    ns: dict = {}
    objc.loadBundle("Vision", ns,
                    bundle_path="/System/Library/Frameworks/Vision.framework")
    objc.registerMetaDataForSelector(
        b"VNImageRequestHandler", b"performRequests:error:",
        {"arguments": {2: {"type": b"o^@"}, 3: {"type": b"o^@"}}})
    _vision = ns
    return ns


def _ocr_pdf_page(pdf_page) -> str:
    """把一页 PDF 转成位图再走 Vision。零依赖（CGBitmapContext + 系统 Vision）。"""
    if pdf_page is None:
        return ""
    ns = _ensure_vision()
    import Quartz
    from Quartz.CoreGraphics import (
        CGColorSpaceCreateDeviceRGB, CGBitmapContextCreate, CGRectMake,
        CGBitmapContextCreateImage, CGContextDrawPDFPage, CGContextFillRect,
        CGContextScaleCTM, CGContextSetRGBFillColor, CGPDFPageGetBoxRect,
        kCGImageAlphaPremultipliedFirst, kCGPDFMediaBox)

    cg = pdf_page.pageRef()
    if cg is None:
        return ""
    rect = CGPDFPageGetBoxRect(cg, kCGPDFMediaBox)
    scale = 1.5
    w, h = int(rect.size.width * scale), int(rect.size.height * scale)
    if w <= 0 or h <= 0:
        return ""
    ctx = CGBitmapContextCreate(None, w, h, 8, 0,
                                CGColorSpaceCreateDeviceRGB(),
                                kCGImageAlphaPremultipliedFirst)
    if ctx is None:
        return ""
    CGContextSetRGBFillColor(ctx, 1, 1, 1, 1)
    CGContextFillRect(ctx, CGRectMake(0, 0, w, h))
    CGContextScaleCTM(ctx, scale, scale)
    CGContextDrawPDFPage(ctx, cg)
    img = CGBitmapContextCreateImage(ctx)

    req = ns["VNRecognizeTextRequest"].alloc().init()
    # ⚠️ accurate 档（1）**只支持 6 种拉丁语系语言，没有中文**；
    #    要读中文图必须降到 fast 档（0）。本工具主场景是英文课堂，accurate 正合适。
    req.setRecognitionLevel_(1)
    handler = ns["VNImageRequestHandler"].alloc().initWithCGImage_options_(img, None)
    # ⚠️ **必须解包**：`performRequests:error:` 带 out-param（上面 `_ensure_vision()` 里
    #    刚注册过 metadata），PyObjC 对这类方法的约定是返回 `(返回值, error)` **元组**。
    #    实测：`handler.performRequests_error_([req], None)` → `(True, None)`。
    #    原来写成 `ok = ...; if not ok: return ""` —— `ok` 拿到的是元组，**恒为真**，
    #    那条错误分支**从来不可达**（注释还说要靠它，自相矛盾）。
    _ok, _err = handler.performRequests_error_([req], None)
    if not _ok:
        return ""
    out = []
    for obs in (req.results() or []):
        cands = obs.topCandidates_(1)
        if cands and len(cands):
            out.append(cands[0].string())
    return "\n".join(out)


# ---------------------------------------------------------------- PPTX

def _pptx_blocks(path: pathlib.Path, ocr: bool, on_progress):
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
        for done, (num, name) in enumerate(slides, 1):
            root = _parse(zf.read(name))
            b, s = _slide_blocks(root, num)
            blocks += b
            seen += s["seen"]; skipped += s["skipped"]
            for k, v in s["reasons"].items():
                reasons[k] = reasons.get(k, 0) + v
            if on_progress:
                on_progress(done, total)

        # 备注页：结构上完全可达、成本≈0，但真实语料里只有 2.03% 的字来自它
        # （`docs/RESEARCH-p3-extract.md` §5.2）→ 顺手抽，不为它做任何设计。
        # ⚠️ 页码按文件名后缀映射到 slide —— 这是常见约定，不是规范保证。
        for num, name in _numbered(names, _NOTES_RE):
            root = _parse(zf.read(name))
            for para in _paragraphs(root):
                blocks.append(Block(para, "notes", num))

    stats = ExtractStats(pages=total, shapes_seen=seen, shapes_skipped=skipped,
                         skipped_by_reason=reasons, ocr_pages=0,
                         by_kind=_count_kinds(blocks))
    return blocks, stats


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
            for tr in tbl.iter(_NS_A + "tr"):
                cells = []
                for tc in tr.iter(_NS_A + "tc"):
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
