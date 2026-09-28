"""课件抽文本 —— PDF / PPTX / DOCX → 带标签的文本块。**只读，永不写文件。**

这是 P3「开课前的准备」的第一环：`prep.py` 拿这里的 `Block` 去抽候选术语。

## 为什么不用第三方库

四样能力这台机器已经付过钱了：PDF 文字层（PDFKit）、OOXML 解包（stdlib `zipfile`）、
图像 OCR（Vision）。**PPTX 与 DOCX 同属 OOXML**，所以加 docx 支持也是零依赖。
`pyobjc-framework-Quartz` 本来就在 `requirements.txt` 里，所以**新增依赖 = 0**。
实测 1336 页 PDF 4.29 秒、一门课的课件 ≤2 秒（原始数据见 `docs/RESEARCH-p3-extract.md`）。

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
# ⚠️ DOCX 用的是 **wordprocessingml**，与上面两个不是同一套前缀。
#    pptx 的 `_paragraphs()` 按 `a:p`/`a:t` 取，对 docx **一个都命中不了** ——
#    所以下面有 `_w_para` / `_w_paras` 两个姊妹函数，而不是把 `_paragraphs` 参数化。
_NS_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
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


_SUPPORTED = {".pdf", ".pptx", ".docx"}


def is_supported(path_or_suffix) -> bool:
    """这份课件**能不能被抽取** —— 支持集的**唯一定义点**。

    ⚠️ 存在的理由：**悬停**时要判"这个收不收"，**跑的时候**也要判同一件事。两处各写
    一份判据，用户就会看到「高亮说能收、跑完说 unsupported」。`REVIEW-midpoint §9.2 #5`
    那条「悬停收文件夹」只是它的一个**特例** —— 文件夹不过是"不属于支持集"的一个例子，
    `.txt` / `.xlsx` / 没有扩展名 一模一样。
    ⚠️ **`.docx` 2026-09-28 起进了支持集**（作者定：`~/Downloads` 里有 52 份）——
    这条注释里的例子据此换过；`_SUPPORTED` 是集合，别再举它当反例。

    ⚠️ 按**后缀**判（与 `extract()` 同一条路），不按 UTI/内容 —— 后者要读文件，而悬停
    阶段**不该读**（`RESEARCH-macos-aesthetic.md` §11）。
    """
    s = str(path_or_suffix)
    suffix = s if s.startswith(".") else pathlib.Path(s).suffix
    return suffix.lower() in _SUPPORTED


def expand(paths) -> tuple[list[str], list[str]]:
    """把拖进来的一串路径摊平：**文件夹取一层**里能抽的文件。

    返回 `(可抽的文件, 被丢掉的)`，两边都是原样的字符串路径。顺序保持输入顺序。

    ⚠️ **必须走 `is_supported`** —— 它是"能不能收"的唯一定义点（见它 docstring 里
       那条「高亮说能收、跑完说 unsupported」）。

    ⚠️ 拖拽**悬停**阶段会调它，于是会 `iterdir()` **列一层目录** —— 与 `is_supported`
       那条「悬停不该读**文件**」不冲突：这里不打开任何文件、不读一个字节的内容，
       问的只是名字。会咬人的是 UTI/内容嗅探那类**要打开并解析**的判定。

    ⚠️ 只展开**一层**，不递归 —— 课件目录不会嵌套，而递归会在用户拖错一个根目录时
       把几千份文件塞进队列。
    """
    ok: list[str] = []
    dropped: list[str] = []
    for p in paths or []:
        s = str(p)
        d = pathlib.Path(s)
        try:
            is_dir = d.is_dir()
        except OSError:
            is_dir = False
        if is_dir:
            try:
                kids = sorted(c for c in d.iterdir() if c.is_file())
            except OSError:
                dropped.append(s)
                continue
            got = [str(c) for c in kids if is_supported(c)]
            ok.extend(got)
            if not got:
                dropped.append(s)          # 文件夹里一个能抽的都没有 -> 整个算丢掉
        elif is_supported(s):
            ok.append(s)
        else:
            dropped.append(s)
    return ok, dropped


def extract(path, *, ocr: bool = True, on_progress=None,
            max_pages: int | None = None) -> ExtractResult:
    """把一份课件抽成带标签的文本块。

    `ocr=False` 关掉扫描页兜底（快，但扫描件会得到空文本）。
    `on_progress(done, total)` 在每页后回调一次 —— **由调用方决定放哪个线程**，
    本函数自己不起线程、不画界面。
    `max_pages` 只看头几页 —— 给**分类**用（判归属看第一页就够，见
    `docs/PLAN-entry-panel.md` §7.10 的实测）。⚠️ 设了它**就不抽 PPTX 备注页**
    （备注不是页，且它按文件名后缀映射到 slide，截断后映射会错位）。
    `stats.pages` 报的始终是**文件真实页数**，不是看了几页。
    """
    p = pathlib.Path(path)
    empty_stats = ExtractStats(0, 0, 0, {}, 0, {})

    if not is_supported(p):
        return ExtractResult(p, "unsupported", [], 0, empty_stats,
                             f"不支持的格式：{p.suffix or '(无扩展名)'}")
    if not p.exists():
        return ExtractResult(p, "unreadable", [], 0, empty_stats, "文件不存在")

    try:
        if p.suffix.lower() == ".pdf":
            blocks, stats = _pdf_blocks(p, ocr, on_progress, max_pages)
        elif p.suffix.lower() == ".docx":
            blocks, stats = _docx_blocks(p, ocr, on_progress, max_pages)
        else:
            blocks, stats = _pptx_blocks(p, ocr, on_progress, max_pages)
    except Exception as e:                                    # noqa: BLE001
        # 抽取失败**不能**让整场 prep 挂掉，也不能静默 —— 说清是哪个文件、什么原因。
        return ExtractResult(p, "unreadable", [], 0, empty_stats,
                             f"{type(e).__name__}: {str(e)[:120]}")

    chars = sum(len(b.text) for b in blocks)
    if not blocks:
        return ExtractResult(p, "empty", [], 0, stats, "抽不出任何文字")
    return ExtractResult(p, "ok", blocks, chars, stats, "")


# ---------------------------------------------------------------- PDF

def _pdf_blocks(path: pathlib.Path, ocr: bool, on_progress, max_pages=None):
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
    lim = n if not max_pages else min(n, max_pages)
    blocks, ocr_pages = [], 0
    for i in range(lim):
        page = doc.pageAtIndex_(i)
        text = (page.string() or "") if page is not None else ""
        if not text.strip() and ocr:
            got = _ocr_pdf_page(page)
            if got.strip():
                ocr_pages += 1
                blocks.append(Block(got.strip(), "ocr", i + 1))
                if on_progress:
                    on_progress(i + 1, lim)
                continue
        # PDF 没有占位符结构，认不出层级 —— 全部 body。
        # （所以「标题加权」是 PPTX 独享的免费信号，见模块文档。)
        for para in _pdf_paragraphs(text):
            blocks.append(Block(para, "body", i + 1))
        if on_progress:
            on_progress(i + 1, lim)

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
            raw = zf.read("word/document.xml")
        except KeyError:
            raise RuntimeError("zip 里没有 word/document.xml —— 不是 DOCX？")
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
