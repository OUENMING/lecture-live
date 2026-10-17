"""笔记正文的两条纪律（2026-10-17 作者验收第 2、3 条）：**不许猜测**、**引号内必须逐字**。

## ① 不许第一人称 / 不许猜测过程

作者点名的三句（真出自交付的那份笔记）：

    「说明我可能听错了公式」
    「或者我漏掉了 q 的一次项」
    「可能是简化处理（假设固定成本为零或忽略）」

正文里**只写结论**；不确定的事只有一种写法：

    ⚠ 课上只给了片段：……原题见 X / 未核对

`scrub(review)` 按句删掉违规句（不是改写成别的话 —— 改写给不出比沉默更好的答案），
并把"这句在讲片段"这件事用上面那个固定格式补一句。

## ② 引号里的英文必须逐字存在于材料或转录

作者点名的假引用：

    「the minimum of the average cost… the lowest acceptable price」

前半句在 📖 里、后半句也在 📖 里，但**中间隔着一段推导** —— 拼起来就不是原文了。
`quote_check(review, …)` 逐条核：找不到就**去掉引号**（改成转述），并记一条提醒。
⚠️ 用省略号拼两段、而两段在材料里**不相邻**的，同样算拼接。

⚠️ 纯函数：不联网、不读文件；"材料/转录"由调用方给（`Materials` 与转录行）。
"""
from __future__ import annotations
import copy
import re
import unicodedata

#: 第一人称 + 猜测：单独一个「我们来看」不算违规（数学写作习惯），猜过程才算。
_HEDGE = r"可能|也许|大概|似乎|恐怕|估计|不确定|猜|怀疑|说不定|应该是|或许"
_GUESS = r"听错|漏掉|漏了|记错|看错|说错|写错|错|口误|不是这个|不同|是别的|反推|凑"
_SPEC = re.compile(rf"(?:我|我们)[^。；;\n]{{0,20}}(?:{_HEDGE}|{_GUESS})"
                   # 「可能是简化处理」这类"猜教授的意图"：可能/也许 后面 8 个字内出现
                   # 「是/为/想/为了/故意」就删（实测作者点名的那句就长这样）。
                   rf"|(?:{_HEDGE})[^。；;\n]{{0,8}}(?:是|为|想|为了|故意|{_GUESS})"
                   rf"|（\s*(?:假设|假定)"
                   rf"|（\s*(?:{_HEDGE})\s*）")

#: 这句话是在讲"出处不可靠"—— 用它决定要不要补那句固定格式。
#: ⚠️ `我可能听错了公式` 这种（第一人称 + 听错/漏掉）也算：它点的是**出处的可靠性**，
#:    不是某个数值的大小。
_FRAGMENT = re.compile(r"片段|未完整|原题|没给全|没有给出完整|只听到|听不全|"
                       r"听错|漏掉|漏了|记错|说错|写错|听不出|没听清")

#: 固定格式（作者给的模板；`……` 处保留事实，不编）。
FIXED_LINE = "⚠ 课上只给了片段：原题不在本课材料里，相关结论标「未核对」，不反推函数。"

_QUOTE = re.compile(r"[“\"]([^”\"]{8,300})[”\"]")
_ELLIPSIS = re.compile(r"…|\.\.\.|\s—\s|--")
_ENOUGH_EN = re.compile(r"[A-Za-z]{2,}")


def _norm(s: str) -> str:
    """比对用的归一化：NFKC → 小写 → 去标点 → 收空格。"""
    s = unicodedata.normalize("NFKC", str(s or "")).lower()
    s = s.replace("’", "'").replace("“", '"').replace("”", '"')
    s = re.sub(r"[^0-9a-z'\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _sentences(text: str) -> list:
    """粗切句（本仓库的笔记是短句 + 折叠块，够用；切不动的长句会整句保留）。"""
    return [x for x in re.split(r"(?<=[。！？!?])\s*|\n+", str(text or "")) if x.strip()]


def scrub(review: dict) -> tuple:
    """删掉猜测句 →（清理后的 review, 删掉的原句, 提醒）。

    ⚠️ 只删、不重写：重写要懂上下文，删掉是最诚实的动作（事实仍在正文别处或 `checks` 里）。
    """
    out = copy.deepcopy(review)
    removed: list = []
    warns: list = []

    def clean(v: str) -> str:
        if not isinstance(v, str) or not v:
            return v
        kept, dropped, frag = [], [], False
        for s in _sentences(v):
            if _SPEC.search(s):
                dropped.append(s.strip())
                frag = frag or bool(_FRAGMENT.search(s))
                continue
            kept.append(s)
        if not dropped:
            return v
        removed.extend(dropped)
        body = "".join(kept).strip()
        if frag and FIXED_LINE not in body:
            # ⚠️ 用空格接在末尾，不换行：`prose` 的每个元素就是**一段**（渲染层按段写），
            #    内嵌 `\n` 会破坏"每个字段是单行字符串"那条数据纪律。
            body = (body + " " + FIXED_LINE).strip()
        return body

    def clean_list(xs):
        return [clean(x) if isinstance(x, str) else x for x in (xs or [])]

    for s in (out.get("sections") or []):
        s["prose"] = [x for x in clean_list(s.get("prose")) if str(x).strip()]
        s["pitfalls"] = [x for x in clean_list(s.get("pitfalls")) if str(x).strip()]
        for key in ("example", "faded"):
            blk = s.get(key) or {}
            for f in ("prompt", "answer", "q", "a"):
                if isinstance(blk.get(f), str):
                    blk[f] = clean(blk[f])
            for st in (blk.get("steps") or []):
                if isinstance(st, dict):
                    for f in ("text", "why"):
                        st[f] = clean(st.get(f, ""))
        for v in (s.get("variants") or []):
            for f in ("q", "a"):
                if isinstance(v.get(f), str):
                    v[f] = clean(v[f])
            for st in (v.get("steps") or []):
                if isinstance(st, dict):
                    for f in ("text", "why"):
                        st[f] = clean(st.get(f, ""))
    if removed:
        warns.append(f"正文删掉 {len(removed)} 处猜测句（不许第一人称/猜过程）")
    return out, removed, warns


# ---------------------------------------------------------------- 引号核
def _sources(materials, transcript: str) -> str:
    blob = str(getattr(materials, "_blob", "") or "")
    return _norm(blob + "\n" + str(transcript or ""))


def _contiguous(src: str, parts: list, *, window: int = 160) -> bool:
    """省略号拼起来的两段，在原文里必须是**相邻**的（`window` 字符以内）。"""
    if len(parts) < 2:
        return False
    i = src.find(parts[0])
    while i >= 0:
        tail = src[i + len(parts[0]): i + len(parts[0]) + window]
        if parts[1] in tail:
            return True
        i = src.find(parts[0], i + 1)
    return False


def quote_check(review: dict, *, materials=None, transcript: str = "") -> tuple:
    """引号内的英文 →（清理后的 review, 被去掉引号的引用, 提醒）。

    判据：整段**逐字**在材料/转录里 → 留；用省略号拼的，两段必须在原文里相邻；
    否则**去掉引号**（`（转述）`）—— 拼接的引用会让读者以为材料里就是这么写的。
    """
    out = copy.deepcopy(review)
    src = _sources(materials, transcript)
    stripped: list = []
    warns: list = []

    def fix(v: str) -> str:
        if not isinstance(v, str) or not v or not src:
            return v

        def one(m):
            span = m.group(1)
            if not _ENOUGH_EN.search(span):
                return m.group(0)                 # 引号里没有英文 → 不动（中文引用另有出处）
            parts = [p for p in (_norm(x) for x in _ELLIPSIS.split(span)) if p]
            ok = _norm(span) in src
            if not ok and len(parts) > 1 and all(p in src for p in parts):
                ok = _contiguous(src, parts)
            if ok:
                return m.group(0)
            stripped.append(span.strip()[:120])
            return f"{span.strip()}（转述）"

        return _QUOTE.sub(one, v)

    def fix_list(xs):
        return [fix(x) if isinstance(x, str) else x for x in (xs or [])]

    for s in (out.get("sections") or []):
        s["prose"] = fix_list(s.get("prose"))
        for key in ("example", "faded"):
            blk = s.get(key) or {}
            for f in ("prompt", "answer", "q", "a"):
                if isinstance(blk.get(f), str):
                    blk[f] = fix(blk[f])
            for st in (blk.get("steps") or []):
                if isinstance(st, dict):
                    for f in ("text", "why"):
                        st[f] = fix(st.get(f, ""))
        for v in (s.get("variants") or []):
            for f in ("q", "a"):
                if isinstance(v.get(f), str):
                    v[f] = fix(v[f])
            for st in (v.get("steps") or []):
                if isinstance(st, dict):
                    for f in ("text", "why"):
                        st[f] = fix(st.get(f, ""))
    if stripped:
        warns.append(f"{len(stripped)} 处引用在材料/转录里找不到逐字原文（已去掉引号改转述）")
    return out, stripped, warns
