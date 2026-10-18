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

#: ⭐ 第六轮（作者 2026-10-17）：**自言自语** —— 模型的思考过程漏进了答案。
#: 作者点名的原话（ECON10770 变式 2 的答案，整段都是）：
#:     「…所以（打扫，不打扫）是纳什均衡吗？检查列：给定行打扫，列不打扫得4…
#:      这不对，因为行在列不打扫时无差异。但通常我们要求严格偏好？」
#: 那些句子不是讲解，是它在跟自己讨论；留在答案里读者会以为那是结论。
#: ⚠️ 判据刻意窄：只认**元话语**（检查/重新想/这不对/等等），不认"？"本身 ——
#:    正文里问句是正常的（「为什么会这样？」），宽一层就开始删真内容。
_SELF_TALK = re.compile(r"需要(?:仔细|重新)?(?:检查|核对)|重新检查|重新想|重新算|"
                        r"让我(?:想|想想|算|检查)|我想想|先看看|"
                        r"检查[:：](?=\s*(?:若|如果|给定|假设))|"
                        r"这不对[，,]?因为|(?<=[。；;？?])这不对|"
                        r"(?:等等|嗯)[，,]")

#: 自问自答的**第二档**：以问号收尾的句子 + 解答里冒出来的「检查：」。
#: ⚠️ 只在**解答字段**里生效（`_solution_like`）—— 讲解与自测**题面**里的问句是正常的
#:    （「什么是公共物品？」「为什么会这样？因为…」），解答里问句则几乎一定是独白。
#:    实测那一段（ECON10770 变式 2 的步骤）是「…也是弱纳什均衡？所以（打扫，不打扫）
#:    也是纳什均衡？…检查：你打扫得0…」整段边问边答 —— 只按元话语删两句等于没删。
_SELF_Q = re.compile(r"^.{6,}[？?]$")
_SOLUTION_META = re.compile(r"检查[:：]")

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

#: 遍历时**不打扫**的顶层键。`cover` 是机器数据（来自 `.atoms.jsonl` 的实时要点），
#: 不是模型的散文；覆盖度那张表就是按它印的。
_SKIP_TOP = ("cover",)


def paths_of(obj, prefix: tuple = ()) -> list:
    """**所有字符串**的路径（`("sections", 0, "variants", 1, "a")` 这种）。

    ⭐ 第六轮第 3 条：一个遍历解决"清理只覆盖 `prose`、变式/答案漏掉"那类事故 ——
       猜过程/自言自语在哪一类字段里都长一个样，而清理代码是**按字段**抄了五六份。
    """
    out: list = []
    if isinstance(obj, str):
        return [prefix]
    if isinstance(obj, dict):
        for k, v in obj.items():
            out += paths_of(v, prefix + (k,))
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            out += paths_of(v, prefix + (i,))
    return out


def _get(obj, path: tuple):
    for k in path:
        obj = obj[k]
    return obj


def _set(obj, path: tuple, val) -> None:
    for k in path[:-1]:
        obj = obj[k]
    obj[path[-1]] = val


def _solution_like(path: tuple) -> bool:
    """**解答字段**（答案 / 步骤正文）—— "边问边答"在这里几乎一定是独白。

    ⚠️ 与 `_prose_like` 是一对：讲解/题面里问句是教学手法（要留），解答里问句是
       模型的思考过程（要删）。同一条文字落在哪一类字段里，判法不同。
    ⚠️ **题面（`q`/`prompt`）与「为什么」（`why`）不算解答**：自测的题干就是问句
       （实测：`qa.0.q`「什么是公共物品？」—— 按解答清理会把整份自测的题干删空）。
    """
    if not path:
        return False
    last = path[-1]
    if last in ("a", "answer"):
        return True
    if last in ("q", "prompt", "why", "claim"):
        return False
    return "steps" in path


def _prose_like(path: tuple) -> bool:
    """这一段是"讲解/题面"（可以补那句固定格式）还是"答案/步骤"（只能删）。

    ⚠️ 补句只对**讲出处**的那类字段成立（`prose` / `prereq` / 题面 `q`/`prompt`）：
       往答案或步骤里塞「⚠ 课上只给了片段」会把读者指错（那一段讲的是推导）。
    """
    if not path:
        return False
    last = path[-1]
    if isinstance(last, int):
        return len(path) >= 2 and path[-2] in ("prose", "pitfalls")
    return last in ("prose", "prereq", "q", "prompt")


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
    """删掉猜测句/自言自语 →（清理后的 review, 删掉的原句, 提醒）。**全字段**。

    ⚠️ 只删、不重写：重写要懂上下文，删掉是最诚实的动作（事实仍在正文别处或 `checks` 里）。
    ⭐ 第六轮第 3 条：遍历**每一处字符串**（`note_guards.paths_of`），不再按字段抄 ——
       实测漏掉的正是 `variants[].a`（作者亲自标出来那一段自言自语）。
    ⚠️ 清空之后**宁缺**：变式/补全题/自测的答案被清空 → 整题不出（读者会以为那就是答案）。
    """
    out = copy.deepcopy(review)
    removed: list = []
    warns: list = []
    # 清理**前**的答案（用来判"清空"）：原地改对象，id 不变，所以能对上号。
    before: dict = {}
    for s in (out.get("sections") or []):
        if not isinstance(s, dict):
            continue
        for v in (s.get("variants") or []) + [s.get("faded")]:
            if isinstance(v, dict):
                before[id(v)] = _one(v.get("a")) if "a" in v else _one(v.get("answer"))
    for q in (out.get("qa") or []):
        if isinstance(q, dict):
            before[id(q)] = _one(q.get("a")) + "\x00" + _one(q.get("q"))

    def clean(v: str, prose_like: bool, solution: bool) -> str:
        kept, dropped, frag = [], [], False
        parts = _sentences(v)
        # ⚠️ 问句那一档要**成串**才动手：解答里单独一个反问是正常的写法
        #    （「为什么会这样？因为价格等于边际成本…」），而成串的问句（实测那一段有 6 句）
        #    是模型在跟自己讨论。一条规则两个方向都要站得住。
        doubts = sum(1 for x in parts if _SELF_Q.search(x))
        for s in parts:
            if (_SPEC.search(s) or _SELF_TALK.search(s)
                    or (solution and _SOLUTION_META.search(s))
                    or (solution and doubts >= 2 and _SELF_Q.search(s))):
                dropped.append(s.strip())
                frag = frag or bool(_FRAGMENT.search(s))
                continue
            kept.append(s)
        if not dropped:
            return v
        removed.extend(dropped)
        body = "".join(kept).strip()
        if frag and prose_like and FIXED_LINE not in body:
            # ⚠️ 用空格接在末尾，不换行：`prose` 的每个元素就是**一段**（渲染层按段写），
            #    内嵌 `\n` 会破坏"每个字段是单行字符串"那条数据纪律。
            body = (body + " " + FIXED_LINE).strip()
        return body

    for path in paths_of(out):
        if path and path[0] in _SKIP_TOP:
            continue
        v = _get(out, path)
        if not isinstance(v, str) or not v:
            continue
        new = clean(v, _prose_like(path), _solution_like(path))
        if new != v:
            _set(out, path, new)

    # ── 清空之后：列表里的空串一律剪掉（`review_plan` / `prose` / `blanks` …）─────
    _prune_empty(out)

    # ── 宁缺：**答案被清空**的题整道不出（作者第六轮第 3 条）────────────────
    # ⚠️ 只说"清空"这一种。**不许**顺手加"删了太短就扔"这类判据：数学答案本来就短
    #    （`$q=3$`），长度判据会开始扔真题（判据实测：变式答案 `6` 被扔掉）。
    gone = 0
    for s in (out.get("sections") or []):
        if not isinstance(s, dict):
            continue
        keep = []
        for v in (s.get("variants") or []):
            if not isinstance(v, dict):
                continue
            if not _one(v.get("a")) and before.get(id(v)):
                gone += 1
                continue                       # 答案被清空了 → 这道变式不出
            v["steps"] = [x for x in (v.get("steps") or []) if _alive(x)]
            keep.append(v)
        s["variants"] = keep
        for key in ("example", "faded"):
            blk = s.get(key)
            if not isinstance(blk, dict):
                continue
            if key == "faded" and not _one(blk.get("a")) and before.get(id(blk)):
                s[key] = {}
                gone += 1
                continue
            blk["steps"] = [x for x in (blk.get("steps") or []) if _alive(x)]
        s["prose"] = [x for x in (s.get("prose") or []) if _one(x)]
        s["pitfalls"] = [x for x in (s.get("pitfalls") or []) if _one(x)]
    qa = []
    for q in (out.get("qa") or []):
        if not isinstance(q, dict):
            continue
        if (not _one(q.get("q")) or not _one(q.get("a"))) and before.get(id(q)):
            gone += 1
            continue
        qa.append(q)
    if out.get("qa"):
        out["qa"] = qa
    if removed:
        warns.append(f"正文删掉 {len(removed)} 处猜测句/自言自语"
                     "（不许第一人称、不许猜过程、不许自己跟自己讨论）")
    if gone:
        warns.append(f"{gone} 道题的答案被清空 → 整题不出（宁缺）")
    return out, removed, warns


def _one(v) -> str:
    """单行化（`_one_line` 的本地版，与 `note_v2._one_line` 同一口径）。"""
    return " ".join(str(v or "").split())


def _prune_empty(obj) -> None:
    """列表里的空串剪掉（原地）。`review_plan` / `blanks` / `prose` 这些都可能被清空。"""
    if isinstance(obj, dict):
        for v in obj.values():
            _prune_empty(v)
        return
    if not isinstance(obj, list):
        return
    keep = [x for x in obj if not (isinstance(x, str) and not x.strip())]
    obj[:] = keep
    for x in obj:
        _prune_empty(x)


def _alive(x) -> bool:
    """一条 step 清理后还活着吗（**字符串**与 `{"text","why"}` 两种形状都认）。"""
    if isinstance(x, str):
        return bool(_one(x))
    if isinstance(x, dict):
        return bool(_one(x.get("text")) or _one(x.get("why")))
    return False


# ---------------------------------------------------------------- 停产价格口径
#: 停产价格/CALIBER 相关的**判据**（不是那句写死的话）。
#: ⚠️ 2026-10-17 第六轮：这里原来是一个写死的 `CALIBER_LINE` 常量（"本课按 AC 最低点
#:    定义停产价格"），任何课只要提到停产价格就插进去 —— 课程专属知识写进了通用管线：
#:    ECON10740 / SOC10020 提到停产价格时，插进去的就是一句**假的本课口径**。
#:    现在只留**找出处**用的形状，对照那句话必须从课件里抄出来、并带上出处。
_SHUTDOWN = re.compile(r"停产价格|停业价格|shutting down|shut down|shutdown")
_HAS_AVC = re.compile(r"AVC|平均可变成本|average variable cost")
#: 课件里那条"定义"长什么样：**停产/最低可接受价格**的说法 + "最低/AC/AVC"这类判据词。
#: ⚠️ 两半都要（实测那句原文是 "supply a positive quantity so long as the price is above
#:    the minimum of the average cost … the lowest acceptable price" —— 它未必出现
#:    `shut down` 这个词，但 "lowest acceptable price" 就是停产价格的官方说法）。
_DEF_LINE = re.compile(r"lowest acceptable price|minimum acceptable price|"
                       r"shutting down|shut[- ]?down|supply a positive quantity|"
                       r"停产价格|最低可接受", re.I)
_MIN_WORD = re.compile(r"minimum|lowest|average cost|break-even|AVC|"
                       r"average variable cost|最低", re.I)


def _complete_text(text: str, max_chars: int = 500) -> str:
    """截到**句末**为止 —— 绝不切在句子中间（作者 2026-10-18：`[:200]` 把出处切在
    "…→ 2q + 1" 半句上）。500 字以内找不到句末标点（罕见）就给全文，不猜。"""
    if len(text) <= max_chars:
        return text
    window = text[:max_chars]
    cut = max(window.rfind(m) for m in "。！？.!?")
    if cut >= 60:
        return window[:cut + 1].strip()
    return text


def _shutdown_source(materials) -> str:
    """课件里那条停产价格定义 → `"📖 <文件名>：<原话>"`；找不到返回 `""`。

    ⚠️ 找不到就**不写**（宁缺）：机器没资格替这门课断言"我们的口径和教科书不一样"。
    ⚠️ 出处**不许截半句**（2026-10-18）：只裁到句末为止。
    """
    if materials is None:
        return ""
    for hit in (materials.search("shutting down price minimum acceptable price average cost",
                                 k=6) or []):
        text = " ".join(str(getattr(hit, "text", "") or "").split())
        if not text or not _DEF_LINE.search(text) or not _MIN_WORD.search(text):
            continue
        if _HAS_AVC.search(text):
            return ""            # 课件里写的就是 AVC 口径 → 与教科书一致，没什么可对照的
        cite = str(hit.cite() or "").strip()
        # ⚠️ `cite()` 自己已经带 `📖 文件名 §段`（来源标记的唯一定义点在 materials），
        #    这里再加一个前缀就会写成「📖 📖 文件名」（实测踩到）。
        body = _complete_text(text)
        return f"{cite}：{body}" if cite.startswith("📖") else f"📖 {cite}：{body}"
    return ""


def ensure_caliber(review: dict, *, materials=None) -> tuple:
    """口径对照**只进讲它的那一节**（标题命中优先，其次第一个提到的节）；至多一节。

    ⚠️ 现状的两个 bug（作者 2026-10-18 点名）：
      · 同一段口径被**复制进好几个小节**（13:23 的二阶导数节也算 —— 它的标题带着
        章名前缀「最优化练习与停业价格」，一提到就命中）；
      · 出处被 `[:200]` **切断在半句**上（"…→ 2q + 1"）。
    现在：只挑**第一个**"关于它"的小节（标题里有它 → 正文里提到它），在那里补一条
    **完整的**（截到句末）对照；找不到出处 / 找不到这一节 → 一个字不写。
    """
    out = copy.deepcopy(review)
    added: list = []
    src = _shutdown_source(materials)
    if not src:
        return out, added, []
    secs = out.get("sections") or []
    target = None
    for s in secs:              # 标题命中优先（讲定义的那些节几乎总在标题里点名）
        if _SHUTDOWN.search(str(s.get("heading") or "")):
            target = s
            break
    if target is None:          # 退一档：第一个正文里提到它的节
        for s in secs:
            blob = " ".join([str(x) for x in (s.get("prose") or [])]
                            + [str((s.get("example") or {}).get("prompt") or "")])
            if _SHUTDOWN.search(blob):
                target = s
                break
    if target is None:
        return out, added, []
    if _HAS_AVC.search(" ".join([str(target.get("heading") or "")]
                                + [str(x) for x in (target.get("prose") or [])])):
        return out, added, []   # 这一节自己已经写了 AVC 口径 → 没什么可对照的
    target.setdefault("prose", [])
    target["prose"] = list(target["prose"]) + [
        "⚠ 口径对照（📚）：标准教科书把**停产价格**定义为平均可变成本（AVC）的最低点，"
        f"而把 AC 的最低点叫**盈亏平衡价（break-even price）**；本课材料另有口径 —— {src}"]
    added.append(str(target.get("heading") or ""))
    return out, added, [f"口径对照只补进讲它的那一节（{added[0]}）；出处按课件原文，不截半句"]


def _sources(materials, transcript: str) -> str:
    """比对用的语料：课件索引的全文 + 转录（归一化后）。"""
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

    ⚠️ 与 `scrub` 同一个遍历（全字段）：`checks` 那一栏也要过一遍 —— 模型会在
       核定表的「断言」列里引用"材料原话"（实测：A 那份的表格里就留着一句拼接引用，
       正文里已经去掉了）。
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

    for path in paths_of(out):
        if path and path[0] in _SKIP_TOP:
            continue
        v = _get(out, path)
        if isinstance(v, str) and v:
            _set(out, path, fix(v))
    if stripped:
        warns.append(f"{len(stripped)} 处引用在材料/转录里找不到逐字原文（已去掉引号改转述）")
    return out, stripped, warns
