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
import json
import math
import re
import unicodedata

#: 第一人称 + 猜测：单独一个「我们来看」不算违规（数学写作习惯），猜过程才算。
_HEDGE = r"可能|也许|大概|似乎|恐怕|估计|不确定|猜|怀疑|说不定|应该是|或许"
_GUESS = r"听错|漏掉|漏了|记错|看错|说错|写错|错|口误|不是这个|不同|是别的|反推|凑"
#: 「猜教授的口误/简化」那一档只认**意图/口误词**（F15-A 收紧）。
#: ⚠️ 旧版这里是 `(?:是|为|想|为了|故意|{_GUESS})` —— 一个「是」就够了，于是
#:    「可能只是把噪声也拟合进去了」「估计量**不是**数据的固定属性」
#:    「换数据估计量为何会变？」全被当猜测句删掉（frozen raw 实测：26 处删除 **0 处**是真
#:    自言自语，精确率 0%）。猜教授的意图长这样：「可能是简化处理」「大概是听错了」。
_INTENT = r"简化|口误|听错|漏了|漏掉|记错|看错|说错|写错|反推|凑数|故意|搞混|糊弄|笔误"
_SPEC = re.compile(rf"(?:我|我们)[^。；;\n]{{0,20}}(?:{_HEDGE}|{_GUESS})"
                   # 「猜教授的意图/口误」：hedge 在前、意图词在后、8 字内（顺序有意义）。
                   rf"|(?:{_HEDGE})[^。；;\n]{{0,8}}(?:{_INTENT})"
                   rf"|（\s*(?:{_HEDGE})\s*）")

#: ⭐ 第六轮（作者 2026-10-17）：**自言自语** —— 模型的思考过程漏进了答案。
#: 作者点名的原话（ECON10770 变式 2 的答案，整段都是）：
#:     「…所以（打扫，不打扫）是纳什均衡吗？检查列：给定行打扫，列不打扫得4…
#:      这不对，因为行在列不打扫时无差异。但通常我们要求严格偏好？」
#: 那些句子不是讲解，是它在跟自己讨论；留在答案里读者会以为那是结论。
#: ⚠️ 判据刻意窄：只认**元话语**（检查/重新想/这不对/等等），不认"？"本身 ——
#:    正文里问句是正常的（「为什么会这样？」），宽一层就开始删真内容。
#: ⚠️ F15-A：动词类的元话语（重新算/重新检查/先看看…）必须**在小句开头**且**没有否定前缀**
#:    —— frozen raw 实测：「sigmoid 的导数只要…**不用重新算** $e^{-z}$」被当成自言自语删掉。
#:    （`这不对`/`检查：` 这两档天生就是独白，照旧。）
_START = r"(?:^|[。；;？?！!\n])\s*"
_NEG = r"(?<![不没无需别])\s*"
_SELF_TALK = re.compile(rf"(?:{_START})(?:需要(?:仔细|重新)?(?:检查|核对)|重新检查|重新想|重新算|"
                        rf"让我(?:想|想想|算|检查)|我想想|先看看)"
                        rf"|{_START}检查[:：](?=\s*(?:若|如果|给定|假设))"
                        rf"|这不对[，,]?因为|(?<=[。；;？?])这不对"
                        rf"|(?:{_START})(?:等等|嗯)[，,]")

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


def _norm_ctx(s: str) -> str:
    """比对归一化（**保留 CJK**）：NFKC → 小写 → 去标点 → 收空格（F9，annotate 两把钥匙之一）。

    `_norm` 会把整段中文剥成空串（`课上报的 20` → `20`）—— 短数字 claim 拿 `20`
    当钥匙会挂到任何含 20 的字段上（实测：挂进了 `q^2-8q+20` 的补全题）。
    """
    s = unicodedata.normalize("NFKC", str(s or "")).lower()
    s = s.replace("’", "'").replace("“", '"').replace("”", '"')
    s = re.sub(r"[^\u4e00-\u9fff0-9a-z'\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


#: 段落**开头**不该是这些连接词 —— 它们回指上文；段首出现说明**上一句刚被删掉**。
#: ⭐ F14-2 #3（作者实测）：速览首句是猜测句被删，剩下那段以「接着用 RMSE 看残差…」开头。
_LEAD_CONNECTIVE = re.compile(r"^\s*(?:接着|然后|随后|于是|因此|所以|那么|这样|同样|另外|反之|"
                              r"不过|但|而且|并且|同时|其次|最后|第三|第二|第四)")


#: 「删完之后段落不许以这些词开头」—— 连接词 + 指代上文的代词（F15 §2 ②）。
#: ⚠️ 作者补的：`这种`/`这个`/`这`/`该`/`它`（「**这种**"动"就是抽样波动…」）。
_SAFE_LEAD = re.compile(r"^\s*(?:接着|然后|随后|于是|因此|所以|那么|这样|同样|另外|反之|"
                        r"不过|但|同时|此外|而且|并且|又|也|还|再|其中|其次|最后|"
                        r"这种|这个|这些|这里|这一|那|那种|那个|该|它|它们|"
                        # ⚠️ 光秃秃的「这」只在**回指**时才拦：`这是本段唯一…`（自指、正常行文）
                        #    不许拦 —— 实测误伤了时间戳那条判据的用例（`这是本段唯一有实际
                        #    安排信息的一句话；12:50:28 的…`）。
                        r"这(?!(?:是|时|条|段|句|位|次|么)))")
#: 叠标点（同一种或混着来都算）：`，，` `，。` `。；`…
_SAFE_DOUBLE_PUNCT = re.compile(r"[，,。；;、]\s*[，,。；;、]")
#: 空括号：`（）` / `()` / `（ ）`
_SAFE_EMPTY_PAREN = re.compile(r"（\s*）|\(\s*\)")
#: 删句之后"只剩一句结论"的字段（答案/步骤）至少要留这么多字，否则保留原文（F15-A）。
SAFE_SOLUTION_MIN = 8

_IMMUNE_TOK = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,20}")


def _sentences_with_spans(text: str) -> list:
    """每句 `(句子, 起, 止)`（止含句末标点）。**纯函数。**"""
    s = str(text or "")
    out, start = [], 0
    for m in re.finditer(r"[。！？!?]\s*|\n+", s):
        seg = s[start:m.end()]
        if seg.strip():
            out.append((seg, start, m.end()))
        start = m.end()
    if start < len(s) and s[start:].strip():
        out.append((s[start:], start, len(s)))
    return out


def immune_tokens(*texts) -> set:
    """在 `$…$` / `$$…$$` 里出现过的记号 —— 这些词**不许**当噪声删（F15 §2 ④）。**纯函数。**

    ⚠️ 公式里写 `VIF_j`、正文里写 `VIF` 是**同一个记号**（下标在正文里常被省掉）——
       所以除了全名，也收它的基名（`_` 前面那截）。
    """
    out: set = set()
    for t in texts:
        for seg in re.findall(r"\$\$[^$]*\$\$|\$[^$]*\$", str(t or "")):
            for tok in _IMMUNE_TOK.findall(seg):
                out.add(tok.lower())
                base = tok.split("_")[0].lower()
                if len(base) >= 2:
                    out.add(base)
    return out


def immune_from_review(review: dict) -> set:
    """全笔记（含公式卡/代码）里出现过的记号集合。**纯函数。**"""
    parts: list = []
    for path in paths_of(review or {}):
        if not path or path[0] in _SKIP_TOP:
            continue
        v = _get(review, path)
        if isinstance(v, str) and v:
            parts.append(v)
    return immune_tokens(*parts)


def _bad_after(text: str) -> str:
    """删完之后正文"坏了"吗（F15 §2 ②③）。返回原因（`""` = 没问题）。**纯函数。**"""
    t = str(text or "")
    if _SAFE_LEAD.match(t):
        return "删后段首成了连接词/代词"
    if t.count("$") % 2:
        return "$ 不配对"
    if _SAFE_EMPTY_PAREN.search(t):
        return "留下空括号"
    if _SAFE_DOUBLE_PUNCT.search(t):
        return "叠标点"
    if re.match(r"^\s*[，,。；;、！？!?]", t):
        return "删后段首是标点"
    return ""


def _merge_spans(spans) -> list:
    """把重叠/相邻的删除区间并成一个（**必须**：否则从后往前改字时下标会漂）。**纯函数。**"""
    out: list = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def safe_delete(text: str, removals, *, immune=frozenset(), fix=None) -> tuple:
    """正文里**删字**的唯一入口（F15 §2）。返回 `(新文本, 被打回的, 真删掉的)`。

    `removals` = `[(起, 止), …]`（半开区间，同一字段的坐标系）。四条护栏，**每条都能
    独立把一次删除打回**（打回 = 保留原文，并记下原因）：

      ① **第一句不许删**（整句覆盖才算；句内的小改仍可，但要过 ②③④）。
         ⚠️ 例外：整段**全删**（字段被清空，后面没有段落可"没头没脑"）不算删第一句
         —— 宁缺那一条（`答案整段独白 → 整题不出`）就得靠这个例外。
      ② 删后段首是连接词/指代词（接着/然后/因此/所以/其中/但/同时/此外/它/该/这/这种…）→ 打回；
      ③ 删后 `$` 不配对 / 留下空括号 / 叠标点 / 段首标点 → 打回；
      ④ 要删的字里含**别处公式/代码里出现过**的记号 → 打回（公式变量不是噪声）。

    `fix` = 该守卫**自己**的收尾规整（如"删完把 `，。` 并成 `。`"）——检查 ②③ 时先
    套上它：护栏要拦的是**成品**，不是中间态（否则 ASR 噪声那支的空档标点会把它自己
    的删除打回）。
    """
    s = str(text or "")
    spans = _merge_spans({(int(a), int(b)) for a, b in (removals or [])
                          if 0 <= int(a) < int(b) <= len(s)})
    if not spans:
        return s, [], []
    sent = _sentences_with_spans(s)
    first = (sent[0][1], sent[0][2]) if sent else None
    # 整段全删？（只按非空白字符算）
    whole = True
    for i, ch in enumerate(s):
        if ch.isspace():
            continue
        if not any(a <= i < b for a, b in spans):
            whole = False
            break
    cur, blocked, kept = s, [], []
    for a, b in reversed(spans):            # 从后往前：前面的下标不会漂
        if first and a <= first[0] and b >= first[1] and not whole:
            blocked.append({"span": (a, b), "why": "第一句不许删", "text": s[a:b]})
            continue
        if immune:
            toks = {t.lower() for t in _IMMUNE_TOK.findall(s[a:b])}
            if toks & set(immune):
                blocked.append({"span": (a, b), "why": "公式/代码里出现过这个记号",
                                "text": s[a:b]})
                continue
        cand = s[:a] + s[b:]
        why = _bad_after(fix(cand) if fix else cand)
        if why:
            blocked.append({"span": (a, b), "why": why, "text": s[a:b]})
            continue
        cur = cand
        kept.append((a, b))
    if fix and cur != s:
        cur = fix(cur)
    return cur, blocked, kept


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

    immune = immune_from_review(review)

    held_back = 0

    def clean(v: str, prose_like: bool, solution: bool, immune) -> str:
        """删猜测句 → 新文本。**删除本身走 `safe_delete`**（F15 §2 的四条护栏）。"""
        nonlocal held_back
        sent = _sentences_with_spans(v)
        # ⚠️ 问句那一档要**成串**才动手：解答里单独一个反问是正常的写法
        #    （「为什么会这样？因为价格等于边际成本…」），而成串的问句（实测那一段有 6 句）
        #    是模型在跟自己讨论。一条规则两个方向都要站得住。
        doubts = sum(1 for x, _a, _b in sent if _SELF_Q.search(x))
        drop, frag = [], False
        for x, a, b in sent:
            if (_SPEC.search(x) or _SELF_TALK.search(x)
                    or (solution and _SOLUTION_META.search(x))
                    or (solution and doubts >= 2 and _SELF_Q.search(x))):
                drop.append((a, b))
                frag = frag or bool(_FRAGMENT.search(x))
        if not drop:
            return v
        body, blocked, _kept = safe_delete(v, drop, immune=immune)
        held_back += len(blocked)
        if blocked and not _kept:
            return v                       # 全被打回 → 一个字不动
        body = body.strip()
        # ⭐ F15-A②：**答案/步骤里只剩一句结论**（"不能。"/"是。"）不许留着 —— 读者会以为
        #    那就是全部。少到这个地步就把原文整段留着（宁可留一句猜测，也不给半句结论）。
        #    ⚠️ 只在**还剩东西**时兜：整段都是独白（删空）照旧走"宁缺、整题不出"那条。
        if solution and body and len(body) < SAFE_SOLUTION_MIN:
            held_back += 1
            return v
        # 只把**真删掉的**句子记进台账（被打回/被兜住的还在正文里，记进去就成了假数）。
        removed.extend(v[a:b].strip() for a, b in sorted(_kept))
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
        new = clean(v, _prose_like(path), _solution_like(path), immune)
        if new != v:
            _set(out, path, new)

    # ── 清空之后：列表里的空串一律剪掉（`review_plan` / `prose` / `blanks` …）─────
    _prune_empty(out)

    # ── 宁缺：**答案被清空**的题整道不出（作者第六轮第 3 条）────────────────
    # ⚠️ 只说"清空"这一种。**不许**顺手加"删了太短就扔"这类判据：数学答案本来就短
    #    （`$q=3$`），长度判据会开始扔真题（判据实测：变式答案 `6` 被扔掉）。
    gone = 0
    dropped_blocks: list = []        # 宁缺丢掉的题块（守卫台账计数用，见函数末）
    for s in (out.get("sections") or []):
        if not isinstance(s, dict):
            continue
        keep = []
        for v in (s.get("variants") or []):
            if not isinstance(v, dict):
                continue
            if not _one(v.get("a")) and before.get(id(v)):
                gone += 1
                dropped_blocks.append({"kind": "variants", "why": "答案被清空（宁缺）"})
                continue                       # 答案被清空了 → 这道变式不出
            v["steps"] = [x for x in (v.get("steps") or []) if _alive(x)]
            keep.append(v)
        s["variants"] = keep
        for key in ("example", "faded"):
            blk = s.get(key)
            if not isinstance(blk, dict):
                continue
            if not any(blk.get(f) for f in ("title", "prompt", "steps", "answer",
                                            "q", "a", "blanks")):
                continue        # ⚠️ 空壳不碰 —— 碰一下就长出 `"steps": []`（F9b 作者点名）
            if key == "faded" and not _one(blk.get("a")) and before.get(id(blk)):
                s[key] = {}
                gone += 1
                dropped_blocks.append({"kind": key, "why": "答案被清空（宁缺）"})
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
    if held_back:
        warns.append(f"{held_back} 处删除被安全闸打回并**保留了原句**"
                     "（第一句 / 段首连接词代词 / $ 不配对 / 叠标点 / 公式里出现过的记号 / "
                     "答案只剩一句结论）")
    if gone:
        warns.append(f"{gone} 道题的答案被清空 → 整题不出（宁缺）")
    # ⭐ F9c：宁缺丢掉的题块也要进守卫台账 —— 返回值里带小标记（长度=被删处数，
    #    写作层的 `guards: dropped N` 用；原因在 warns 里）。
    return out, list(removed) + dropped_blocks, warns


def _one(v) -> str:
    """单行化（`_one_line` 的本地版，与 `note_v2._one_line` 同一口径）。"""
    return " ".join(str(v or "").split())


def _prune_empty(obj) -> None:
    """列表里的空串剪掉（原地）。`review_plan` / `blanks` / `prose` 这些都可能被清空。

    ⚠️ F10：`compare` 里的空格是**位置占位**（行头列的表头常为空、格子里也可能留空）——
       剪掉会把列错位（che002a 实测：`["", "氧化", "还原"]` 被剪成 `["氧化", "还原"]`，
       表头原地左移一格）。这一支不剪，其余位置照旧。
    """
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "compare":      # 对照表：串的位置就是语义
                continue
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


# ============================================================ 2026-10-18 Δ
# 作者看截图抓的四处（同一张 §1 的图）：
#   ① 错除法「5.3除以3，得到约2.89」正文照印，而三道核的核算结果只活在文末折叠清单里；
#   ② 同一段里「⚠ 课上只给了片段…」抄了两遍（模型自己抄的格式，抄重了）；
#   ③ 线索问题「5.3除以3等于多少？」—— 计算器能答的问题帮不了复习；
#   ④（渲染层）「展开全部步骤」还是原生按钮的灰框。
# ①③④ 的修法见各自函数；② 这里做确定性去重。

#: 三道核判成 `mismatch` 的断言 → 正文里的标注格式（核算结果必须**落到读者眼前**）。
#: ⚠️ 为什么只标不改数字：`5.3` 与 `2.89` 哪个是口播听错，机械核**不知道**
#:    （8.67÷3 = 2.89 恰好成立）—— 改错比标错坏得多（与更正账本同一条纪律）。
#: ⭐ F14 ③：**两种命中必须是两种标签**。数值/公式重算不一致（我们算出来对不上）与
#:    "归属性引语没找到原话"（我们查不到出处）方向完全相反，旧版共用「核算对不上」，
#:    读者把后者读成"数字算错了"（实测：ECS170 一条 `课件末页公式` 被标成核算不符）。
_MISMATCH_MARK = "（⚠ 数字对不上：{how}）"
_QUOTE_MARK = "（⚠ {how}）"
#: 归属性引语没找到时的 `how`（把旧文案"转录里没找到这句（核对）"改成人话）。
_QUOTE_HOW = {"tr": "转录里没找到这句原话", "mat": "材料里没找到这句原话"}
#: 任何已落地的标注（幂等 + 剥离共用；旧文案一起认，历史产物里还有）。
_ANY_MARK = re.compile(r"（⚠ (?:核算对不上|数字对不上|转录里没找到这句原话|"
                       r"材料里没找到这句原话)[^）]*）")


def is_quote_row(row: dict) -> bool:
    """这一行是「归属性引语没找到原话」还是「数值/公式重算对不上」。**纯函数。**"""
    if str(row.get("kind") or "") == "support":
        return True
    if str(row.get("recomputed") or "").startswith("support p="):
        return True
    return "没找到这句" in str(row.get("how") or "")


def _quote_how(row: dict) -> str:
    """引语没找到时的诚实说法（转录查不到 / 材料查不到）。**纯函数。**"""
    return _QUOTE_HOW["mat"] if "材料" in str(row.get("how") or "") else _QUOTE_HOW["tr"]
#: 允许被标注的字段（渲染进正文的那些；`sources`/`ts`/`title` 这类出处字段不碰）。
_MARK_FIELDS = {"prose", "prereq", "conclusion", "algebra", "intuition", "pitfalls",
                "prompt", "answer", "text", "why", "q", "a"}
#: ⚠️ F15-E 实测：⚠ 落到 `algebra` 上 = 把括注缀进公式行（读者会把注释当成式子的一部分）。
#:    矛盾标注只许落在**讲解类**字段上。
_MARK_TEXT_FIELDS = ("prose", "pitfalls", "conclusion", "intuition", "prereq",
                     "prereq_link")


def _field_name(path: tuple) -> str:
    """路径的**字段名**（最后一个字符串分量）—— `("sections", 0, "prose", 0)` → `prose`。

    ⚠️ 不能拿 `path[-1]`：列表字段（`prose`/`sources`/`steps`）的末位是**下标**（int），
       照 `path[-1]` 过滤会把正文全部漏掉（2026-10-18 写这个守卫时踩过一版）。
    """
    for p in reversed(path):
        if isinstance(p, str):
            return p
    return ""


def _div_bad_sentence_end(text: str):
    """这段文本里第一条**算不对的除法**断在哪儿（返回句子结束下标）；没有 → None。

    ⚠️ 判法重新走 `note_math._DIV`（与三道核同一只正则、同一套容差）—— 标注的位置
       必须和"谁被核出问题"完全一致，不另写一套近似。
    """
    import note_math
    for i, m in enumerate(note_math._DIV.finditer(text)):
        try:
            a, b, c = float(m.group("a")), float(m.group("b")), float(m.group("c"))
        except ValueError:
            continue
        if not b:
            continue
        dec = len(m.group("c").split(".")[1]) if "." in m.group("c") else 0
        if abs(round(a / b, dec) - c) > 1e-9:
            j = min([x for x in (text.find(p, m.end()) for p in "。！？") if x >= 0]
                    or [len(text)])
            return j + 1
    return None


def annotate_mismatches(review: dict) -> tuple:
    """三道核里 `status == "mismatch"` 的断言 → **正文原位标 ⚠**（就地，不改数字）。

    ⚠️ 为什么不能只进"修正说明"（2026-10-18 Δ 的根因）：三段核的 mismatch 只落在
       文末折叠的核对清单与「修正记录」里，而 §1 正文照样写着"5.3除以3，得到约2.89"——
       读者把错算术当事实读 4 遍；清单那行还写着"已修正"（其实正文一个字没动）。
       **核算结果必须跟着那句话走**；改不了（不知道哪个数是口播错的）就标 ⚠，不许静默。
    ⚠️ 定位：claim 前 60 字在哪个字段里出现就标哪个字段；能定位到"哪一句"（除法）
       就插在句号后，定位不到就缀在段尾（位置宁糙，不许不标）。
    ⚠️ 幂等：字段里已有"核算对不上"就不再插（同一句可能同时有机械核与模型核两行）。
    """
    out = copy.deepcopy(review)
    rows = [c for c in (out.get("checks") or [])
            if isinstance(c, dict) and c.get("status") == "mismatch"]
    if not rows:
        return out, [], []
    n_num = 0
    n_quote = 0
    for row in rows:
        raw = str(row.get("claim") or "")
        # ⚠️ 前缀长度取在**原串**上再归一化：`_norm` 把 CJK 全剥掉，先归一化再截 60 字
        #    ≈ 原串 250 字 —— 去重/清时间戳正好动在那一段，这句话在去重后就找不回来了
        #    （2026-10-18 实测：真实产线顺序 dedupe → annotate 全灭）。渐进缩短兜底。
        # ⭐ F9（作者点名）：短数字 claim（`课上报的 20`）不许乱挂 —— 两把钥匙：
        #    ① 带 CJK 上下文的前缀（保留中文；先试，最长者赢）；
        #    ② 旧的剥-CJK 前缀只当**兜底**，且要够长、含字母（`20` 这种纯数字不许当钥匙）。
        ctx = [p for p in (_norm_ctx(raw[:n]) for n in (90, 64, 40, 24)) if len(p) >= 6]
        keys = [p for p in (_norm(raw[:n]) for n in (90, 64, 40, 24))
                if len(p) >= 8 and any(c.isalpha() for c in p)]
        # ⚠️ marker 里的 `how` 先把全角括号换成半角：剥 marker 的正则靠"第一个 `）` 收口"，
        #    括号留在 how 里会把标注切成残渣（幂等判断与 claim 比对都靠它）。
        how = str(row.get("how") or "").strip().rstrip("。；;，,")
        how = how.replace("（", "(").replace("）", ")")
        if (not ctx and not keys) or not how:
            continue
        best = None                     # (前缀长度, section 下标, path) —— 更长的主人更可信
        for mode in ("ctx", "keys"):
            pool = ctx if mode == "ctx" else keys
            if not pool:
                continue
            for si, s in enumerate(out.get("sections") or []):
                if not isinstance(s, dict):
                    continue
                for path in paths_of(s):
                    if not path or _field_name(path) not in _MARK_FIELDS:
                        continue
                    v = _get(s, path)
                    if not isinstance(v, str) or not v:
                        continue
                    clean = _ANY_MARK.sub("", v)
                    bare = _norm_ctx(clean) if mode == "ctx" else _norm(clean)
                    hits = [len(p) for p in pool if p in bare]
                    if hits and (best is None or max(hits) > best[0]):
                        best = (max(hits), si, path)
            if best is not None:
                break
        if best is None:
            continue
        _, si, path = best
        v = _get(out["sections"][si], path)
        if _ANY_MARK.search(v):
            row["annotated"] = True      # 这一处已经有 ⚠（别行标的）
            continue
        if is_quote_row(row):
            # ⭐ F14 ③：引语没找到 → 用**说清是哪件事**的标签，且台账里也换同一句人话
            mark = _QUOTE_MARK.format(how=_quote_how(row))
            row["how"] = _quote_how(row)
        else:
            mark = _MISMATCH_MARK.format(how=how)
        where = None if is_quote_row(row) else _div_bad_sentence_end(v)
        _set(out["sections"][si], path, (v.rstrip() + mark) if where is None
             else (v[:where] + mark + v[where:]))
        row["annotated"] = True
        if is_quote_row(row):
            n_quote += 1
        else:
            n_num += 1
    bits = []
    if n_num:
        bits.append(f"{n_num} 处数字对不上")
    if n_quote:
        bits.append(f"{n_quote} 处归属性引语没找到原话")
    warns = ["、".join(bits) + "，已在正文原位标 ⚠"] if bits else []
    return out, [], warns


# ============================================================ F14 ④⑤⑥ Δ
# 作者逐字读完 v2 的 BUSI3243 / ECS170 抓到的三处（自动审计一条都没报）：
#   ④ 悬空节指路：「ReLU 的负区零梯度问题在第三节」（其实在第 21 节）；
#   ⑤ 没有课件却声称有：「课件末页公式…」（该讲是 `📖 无课件材料`）；
#   ⑥ ASR 噪声抄进正文：「误以为 ReLU 负区间输出是 1 或常数 9」（转录 `1, 9, … 0`）、
#      「课程本身不要求 S1」（转录 `We don't require S1`）。

def _walk_text(node, fn, *, skip=frozenset()) -> None:
    """就地：对 `node` 里所有**字符串叶子**调 `fn`（`skip` 里的 key 整个子树跳过）。

    ⚠️ 只认 dict/list/str（笔记数据就这三种）；`skip` 用来保护逐字转录字段。
    """
    if isinstance(node, dict):
        for k, v in list(node.items()):
            if k in skip:
                continue
            if isinstance(v, str):
                node[k] = fn(v)
            else:
                _walk_text(v, fn, skip=skip)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            if isinstance(v, str):
                node[i] = fn(v)
            else:
                _walk_text(v, fn, skip=skip)


# ---- ⑤ 没课件时，「课件/PPT 里写着」这类声称全是假的 --------------------------
#: 材料来源词：出现它 = 声称"这是课件/讲义里写的"。
_MAT_SRC = re.compile(r"(?:课件|讲义|PPT|ppt|幻灯片|[Ss]lides?)\s*"
                      r"(?:末页|最后一页|第\s*\d+\s*页)?")
#: 没课件时的降级目标（与页脚/图例里的 `📚 标准理论` 同一口径）。
_DOWNGRADE_SRC = "📚 标准教材"
#: 逐字转录字段 —— 这些字**不许**被改写（它们本来就是原文）。
_RAW_KEYS = frozenset({"spoken", "quote", "en", "zh", "asr", "ts"})


def strip_false_sources(review: dict, *, has_materials: bool) -> tuple:
    """没课件时，「课件/PPT 里写着 X」这类声称**全是假的** → 降级成 📚 标准教材。F14 ⑤

    ⚠️ 为什么必须做：ECS170 那一讲页头写着 `📖 无课件材料`，正文却写
       「**课件末页公式**：σ(z)=…」，台账里还有一行 `阶跃定义与课件一致` ——
       读者会以为有课件支撑（**假出处比没有出处坏**）。
    ⚠️ 有课件时一个字不动（📖 是真的）。降级目标选 📚：标准教材**永远**在。
    返回 `(review, [{"downgraded": n}], warns)`（计数进守卫台账）。
    """
    if has_materials:
        return review, [], []
    out = copy.deepcopy(review)
    n_src = n_tag = n_title = 0

    def fix(s: str) -> str:
        nonlocal n_src
        new = _MAT_SRC.sub(_DOWNGRADE_SRC, s)
        if new != s:
            n_src += 1
        return new

    for s in out.get("sections") or []:
        if not isinstance(s, dict):
            continue
        src = s.get("sources")
        if isinstance(src, list):
            keep = [x for x in src
                    if not (isinstance(x, str) and ("📖" in x or _MAT_SRC.search(x)))]
            if len(keep) != len(src):
                n_tag += len(src) - len(keep)
                s["sources"] = keep
        ex = s.get("example")
        if isinstance(ex, dict) and isinstance(ex.get("title"), str) and "📖" in ex["title"]:
            ex["title"] = ex["title"].replace("📖", "✏️")
            n_title += 1
        _walk_text(s, fix, skip=_RAW_KEYS | {"sources"})
    _walk_text(out.get("checks") or [], fix, skip=_RAW_KEYS)
    _walk_text(out.get("corrections") or [], fix, skip=_RAW_KEYS)
    bits = []
    if n_src:
        bits.append(f"{n_src} 处「课件/PPT」来源声称 → 📚 标准教材")
    if n_tag:
        bits.append(f"{n_tag} 条 📖 出处标签（本课没有课件）已去掉")
    if n_title:
        bits.append(f"{n_title} 道例题的 📖 标题 → ✏️")
    warns = ["没课件：" + "、".join(bits)] if bits else []
    return out, [{"downgraded": n_src + n_tag + n_title}], warns


# ---- ④ 悬空的「第 N 节 / 上一节」指路 --------------------------------------
_REF_NUM = re.compile(r"第\s*([0-9]{1,3}|[一二三四五六七八九十]{1,3})\s*节")
_REF_REL = re.compile(r"(?:上|前|下|后)(?:一)?(?:小)?节")
_REF_ANY = re.compile(r"(?:" + _REF_NUM.pattern + r"|" + _REF_REL.pattern + r")")
#: 汉字数词（够到 39 节）。
_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
           "十": 10}
#: 概念词的虚字表：含这些字的二字组不算概念词（判据要域中立 → 只认功能字，不认学科词）。
_STOP_CH = set("的在不和与是了也就把被这那有要会能可对为以之其中上下前后里个种些"
               "所但而且或者如果因节已经出现讲过说明先再更最把让使给向从到对于")
_ASCII_STOP = frozenset({"the", "and", "for", "with", "that", "this", "from", "are", "not",
                         "you", "its", "has", "have", "was", "but", "can"})
#: 允许出现"节指路"的字段（正文/链接行；出处与转录字段不碰）。
_REF_FIELDS = frozenset({"prose", "prereq_link", "conclusion", "intuition", "algebra",
                         "pitfalls", "q", "prompt", "answer", "text", "why", "a"})


def _cn_int(s: str):
    """「三」→3、「十二」→12、「21」→21；认不出 → None。**纯函数。**"""
    if str(s).isdigit():
        return int(s)
    if len(s) == 1:
        return _CN_NUM.get(s)
    if len(s) == 2 and s[0] == "十":
        return (10 + _CN_NUM.get(s[1], 0)) or None
    if len(s) == 2 and s[1] == "十":
        return (_CN_NUM.get(s[0], 0) * 10) or None
    if len(s) == 3 and s[1] == "十":
        return (_CN_NUM.get(s[0], 0) * 10 + _CN_NUM.get(s[2], 0)) or None
    return None


def _concept_tokens(text: str) -> set:
    """一段话 → 概念词集合（ASCII 词 + 不含虚字的汉字二字组）。**纯函数。**"""
    out: set = set()
    for w in re.findall(r"[A-Za-z][A-Za-z0-9_.]{1,}", str(text or "")):
        lw = w.lower()
        if lw not in _ASCII_STOP:
            out.add("en:" + lw)
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", str(text or "")):
        for i in range(len(run) - 1):
            bg = run[i:i + 2]
            if not (set(bg) & _STOP_CH):
                out.add("zh:" + bg)
    return out


def _section_blob(s: dict) -> str:
    """一节的可检索全文（判"这个概念讲在哪一节"）。**纯函数。**"""
    parts = [str(s.get("heading") or ""), str(s.get("q") or ""),
             str(s.get("conclusion") or ""), str(s.get("algebra") or ""),
             str(s.get("intuition") or ""), " ".join(s.get("prose") or [])]
    for p in (s.get("prereq_link"), s.get("prereq")):
        parts.append(str(p or ""))
    for t in (s.get("terms") or []):
        if isinstance(t, dict):
            parts.append(str(t.get("en") or "") + " " + str(t.get("zh") or ""))
    return " ".join(parts)


def _ref_title(s: dict, limit: int = 18) -> str:
    """节标题 → 指路用短名（去掉 `[13:23] ` 前缀与结尾括注，太长截断）。**纯函数。**"""
    t = re.sub(r"^\s*\[[^\]]*\]\s*", "", str(s.get("heading") or "")).strip()
    t = re.sub(r"\s*[（(][^）)]*[）)]\s*$", "", t).strip()
    return (t[:limit] + "…") if len(t) > limit else t


def _ref_edits_in(text: str, si: int, secs: list, toks: list) -> list:
    """一段文本里的节指路 → `[(起, 止, 替换)]`（**纯函数**；`""` = 删掉这块指路）。"""
    edits: list = []
    for m in _REF_ANY.finditer(text):
        num = _REF_NUM.fullmatch(m.group(0))
        head_start = max([text.rfind(p, 0, m.start())
                          for p in "。；;，,！？!?\n"] + [-1]) + 1
        mt = _concept_tokens(text[head_start:m.start()])
        scores = []
        for j, tk in enumerate(toks):
            if j == si:
                continue
            sc = (3 * len([x for x in mt if x.startswith("en:") and x in tk])
                  + len([x for x in mt if x.startswith("zh:") and x in tk]))
            if sc:
                scores.append((sc, j))
        scores.sort(key=lambda p: (-p[0], p[1]))
        best = scores[0] if scores else None
        start = m.start()
        pre = re.search(r"(?:在|见|参见|详见|关于)\s*$", text[:m.start()])
        if pre and pre.start() >= head_start:
            start = pre.start()
        target = None
        if num is not None:
            n = _cn_int(num.group(1))
            if best and best[0] >= 3 and (best[1] + 1) != n:
                target = f"「{_ref_title(secs[best[1]])}」一节"
            else:
                # 号数越界 / 只有弱命中：**只把号换成中性方位词**，句子内容一个字不动。
                # ⚠️ 这里不许删整句（v3 实测：定不下来的 5 处整句被删 → 正文破了洞）。
                target = "前面" if (best is None or best[1] < si) else "后面"
        else:
            exp = si + 1 if m.group(0).startswith(("下", "后")) else si - 1
            if best and best[0] >= 3 and best[1] != exp:
                target = f"「{_ref_title(secs[best[1]])}」一节"
        if target is not None:
            end = m.end()
            if target.startswith("「"):
                pass                        # `在「标题」一节` —— 留着"在"读得顺
            else:
                start = m.start()           # 中性方位词：留着"在"（"在前面提过"）
                if text[m.end():m.end() + 1] in ("里", "中"):
                    end = m.end() + 1       # 顺带吃掉紧跟的「里/中」→ "在前面给过"
            edits.append((start, end, target))
    return edits


def fix_section_refs(review: dict) -> tuple:
    """悬空的「第 N 节 / 上一节」指路 → 按**概念**落到真正讲它的那一节。F14 ④

    ⚠️ 为什么必须做（作者实录）：「**ReLU 的负区零梯度问题在第三节**」（其实在第 21 节）、
       「**sigmoid 的饱和问题在第一节已出现**」（其实是第 18 节）—— 读者按这条线回翻会翻错。
    ⚠️ 判据（域中立，不认学科词）：把指路**前面那个小句**里的概念词（ASCII 词 + 无虚字的
       汉字二字组）拿到各节全文里数命中；高分节 ≠ 它说的那一节 → 改成
       `「那一节的标题」一节`（标题是读者在目录里看得见的名字；节号目录里不显示）。
    ⚠️ 只改有证据的：**相对指路**（上一节/下一节）只有在**别处**存在高分命中时才动 ——
       它本来就多半是对的，没证据不许碰（机器不许把对的改错）。
    ⚠️ 号数越界 / 只有弱命中 / 一点线索都没有 → 只把**号**换成中性方位词（前面/后面），
       句子内容一个字不动（v3 实测：删整句会在正文里留破洞）。
    返回 `(review, [{"repaired": n, "dropped": m}], warns)`。
    """
    out = copy.deepcopy(review)
    secs = [s for s in (out.get("sections") or []) if isinstance(s, dict)]
    if len(secs) < 3:
        return review, [], []
    toks = [_concept_tokens(_section_blob(s)) for s in secs]
    fixed = neutral = dropped = 0
    for si, s in enumerate(secs):
        for path in paths_of(s):
            if _field_name(path) not in _REF_FIELDS:
                continue
            v = _get(s, path)
            if not isinstance(v, str) or not v:
                continue
            edits = _ref_edits_in(v, si, secs, toks)
            if not edits:
                continue
            for a, b, repl in sorted(edits, reverse=True):
                if repl.startswith("「"):
                    fixed += 1
                else:
                    neutral += 1
                # ⚠️ 只换**指路那几个字**（`第 7 节` → `前面` / `「标题」一节`），
                #    句子其余部分一个字不动 —— v3 实测删整句会在正文里留下破洞。
                v = v[:a] + repl + v[b:]
            _set(s, path, v)
    bits = []
    if fixed:
        bits.append(f"{fixed} 处节指路按内容改到**真正讲它的那一节**")
    if neutral:
        bits.append(f"{neutral} 处定不下来 → 改成中性说法")
    if dropped:
        bits.append(f"{dropped} 处删掉没线索的指路")
    warns = ["、".join(bits)] if bits else []
    return out, [{"repaired": fixed, "dropped": neutral + dropped}], warns


# ---- ⑥ ASR 噪声被抄进正文 -------------------------------------------------
#: 把裸数字当名词用（`常数 9`）—— 转录里的"数字清单"常被这么误读。
_ASR_NOMINAL = re.compile(r"(?:常数|常量|数字|号码|编号)\s*[=＝]?\s*(?P<d>\d{1,4})")
#: 一次性标识符：`S1` / `Q2` / `ABC` / `XYZs`（≥2 字、含数字或全大写；公式变量不走这里）。
_ASR_IDENT = re.compile(r"(?<![\w$.])(?:[A-Za-z]{1,4}\d{1,3}|[A-Z]{2,8}s?)(?![\w$.=])")
#: 切小句的分隔符。
_CLAUSE_SEP = "。；;！？!?，,\n"


def _clause_span(text: str, i: int) -> tuple:
    """`i` 所在小句的 [起, 止)（不含分隔符）。**纯函数。**"""
    left = max([text.rfind(p, 0, i) for p in _CLAUSE_SEP] + [-1]) + 1
    right = min([x for x in (text.find(p, i) for p in _CLAUSE_SEP) if x >= 0]
                or [len(text)])
    return left, right


def _mask_math(text: str) -> str:
    """把 `$…$` 与 `\\命令` 抹成等长空格（**保持下标**，便于把结果映射回原文）。"""
    s = re.sub(r"\$[^$]*\$", lambda m: " " * len(m.group(0)), str(text or ""))
    return re.sub(r"\\[A-Za-z]+", lambda m: " " * len(m.group(0)), s)


def _occurrences(tok: str, lines: list) -> list:
    """某 token（字面 + 词边界）出现在哪些转录行里。**纯函数。**"""
    rx = re.compile(r"(?<![\w.])" + re.escape(tok) + r"(?![\w.])", re.I)
    return [ln for ln in lines if rx.search(ln)]


def _ident_shape(tok: str) -> bool:
    """像"编号"的词（`S1`/`Q2`/`ABC`/`XYZs`）；普通英文词、公式变量不算。**纯函数。**

    ⚠️ 含数字的那一支**必须以大写字母开头**：`S1`（章节/工具编号）是候选，
       `b1`/`x2`/`q1`（公式里的小写变量，正文里写成纯文本时也长这样）**不是** ——
       v3 实测把「系数 b1」里的 b1 当噪声删掉过（公式变量在正文里很常见）。
    """
    if len(tok) < 2:
        return False
    if re.search(r"\d", tok):
        return len(tok) <= 5 and tok[0].isupper()
    return bool(re.fullmatch(r"[A-Z]{2,8}s?", tok))


def _unknown_idents(line: str, known) -> set:
    """一行转录里"说不通"的编号词（不在材料/术语表/本课术语里）。**纯函数。**"""
    out = set()
    for m in _ASR_IDENT.finditer(_mask_math(line)):
        tok = m.group(0)
        if _ident_shape(tok) and not known(tok):
            out.add(tok.lower())
    return out


def _garbled_line(line: str, known) -> bool:
    """这一行像不像"被截断/拼错的噪声行"。**纯函数。**

    ⚠️ 两个条件缺一不可：① 没有句末标点（ASR 常常整行截断）；② 行里**≥2 个说不通的
       编号词**（`… We don't require S1 for`）—— 单独一个词可能是真术语，
       一行里挤着两个谁都不认识的编号，才是"这段没听清"的现场证据。
    """
    s = str(line or "").strip()
    if not s or re.search(r"[.!?]\s*$", s):
        return False
    return len(_unknown_idents(s, known)) >= 2


def drop_asr_orphans(review: dict, *, entries: list, known) -> tuple:
    """正文里"只活在转录噪声里"的常数/编号 → 删掉那一小块。F14 ⑥

    ⚠️ 为什么必须做（作者实录）：转录 `Outputs listed: 1, 9, then 0 as a constant.` 被读成
       「输出是 1 **或常数 9**」；转录 `… We don't require S1 for` + `… but S1 matters a lot for ML or HCI` 被抄成「课程本身**不要求 S1**」。两句都是**拼接噪声**，
       读者会把它当课上事实。
    两条判据（都要求"无据"：材料/术语表/本课术语里都没有）：
      ① 裸数字当名词（`常数 N`）：它的转录出现**全在"数字清单"行**（`1, 9, …`）；
      ② 编号词（`S1`）：正文里**只出现一次**、从不出现在公式/例题/术语里，
         而且出现它的**每一行**都是"截断 + 挤着 ≥2 个不认识的编号词"的**噪声行**。
    ⚠️ 判据极保守（宁可漏）：宁可少删，也不许把 `AC`/`RSS`/`SVM`/`DJI` 这种真缩写删掉
       —— 上一版只按"转录里只出现一次"判，econ 一讲误删 113 处，被这条纪律拦下。
    返回 `(review, [{"dropped": n}], warns)`；删了什么逐条进 warn。
    """
    out = copy.deepcopy(review)
    immune = immune_from_review(review)
    lines = [str(e.get("en") or e.get("asr") or "") for e in (entries or [])]
    tech = json.dumps([{k: s.get(k) for k in
                        ("algebra", "terms", "example", "variants", "faded", "program")}
                       for s in (out.get("sections") or []) if isinstance(s, dict)],
                      ensure_ascii=False).lower()
    prose_count: dict = {}
    for s in out.get("sections") or []:
        if isinstance(s, dict):
            for t in (s.get("prose") or []) + (s.get("pitfalls") or []):
                for m in _ASR_IDENT.finditer(_mask_math(str(t))):
                    prose_count[m.group(0).lower()] = prose_count.get(m.group(0).lower(), 0) + 1
    removed: list = []
    for s in out.get("sections") or []:
        if not isinstance(s, dict):
            continue
        for path in paths_of(s):
            if _field_name(path) not in _REF_FIELDS:
                continue
            v = _get(s, path)
            if not isinstance(v, str) or not v:
                continue
            masked = _mask_math(v)
            edits: list = []
            for m in _ASR_NOMINAL.finditer(masked):
                d = m.group("d")
                if known(d):
                    continue
                occ = _occurrences(d, lines)
                if not occ or not all(("constant" in ln.lower()
                                       or re.search(r"\d\s*,\s*\d", ln)) for ln in occ):
                    continue
                a, _b = _clause_span(v, m.start())
                seg = v[a:m.start()]
                conn = max([seg.rfind(p) for p in ("或", "和", "以及", "及", "、")] + [-1])
                edits.append((a + conn if conn >= 0 else a, m.end()))
                removed.append(f"「常数 {d}」（转录里只出现在数字清单里）")
            for m in _ASR_IDENT.finditer(masked):
                tok = m.group(0)
                if not _ident_shape(tok) or known(tok):
                    continue
                low = tok.lower()
                # ⚠️ 同一条噪声可能被模型在**几节里各抄一遍**（v3 实测：同一个编出来的
                #    先修课编号出现在 3 处正文里）——所以是"≤3 处"，不是"只此一次"。
                #    再多的（>3）就不动：那更像一个真记号，宁可漏删。
                if not (1 <= prose_count.get(low, 0) <= 3):
                    continue
                if re.search(r"(?<![A-Za-z])" + re.escape(low) + r"(?![A-Za-z])", tech):
                    continue                                   # 公式/例题/术语里出现过 → 真记号
                occ = _occurrences(tok, lines)
                if not occ or not all(_garbled_line(ln, known) for ln in occ):
                    continue
                a, b = _clause_span(v, m.start())
                if b - a <= 4:
                    continue
                edits.append((a, b))
                removed.append(f"「{tok}」（转录里只出现在拼错的噪声行里）")
            if edits:
                # 收尾规整（§2 的 `fix`）：删掉"…不要求，S1。"里的那一小块会剩下
                # 一个空档标点（`，。`）—— 先并掉，再让护栏看成品。
                v2, blocked, kept_spans = safe_delete(
                    v, sorted(set(edits)), immune=immune,
                    fix=lambda t: re.sub(r"^[，,、]\s*", "",
                                         re.sub(r"[，,。]\s*[，,。]", "。",
                                                re.sub(r"\s+([，。；、！？])", r"\1", t))))
                if not kept_spans:
                    continue
                v2 = re.sub(r"\s+([，。；、！？])", r"\1", v2)
                v2 = re.sub(r"[，,。]\s*[，,。]", "。", v2)
                v2 = re.sub(r"^[，,、]\s*", "", v2).strip()
                _set(s, path, v2)
    warns = []
    if removed:
        warns.append(f"删掉 {len(removed)} 处只活在转录噪声里的字样："
                     + "；".join(removed[:5]))
    return out, [{"dropped": len(removed)}], warns


# ---- ⑧⑨ 跨节矛盾 ----------------------------------------------------------
def _find_and_append(s: dict, needle: str, mark: str, *, fields=None,
                     exact: bool = False) -> bool:
    """把 `mark` 缀到 `s` 里含 `needle` 的那个字段（找不到就缀到第一段 prose）。**就地。**

    ⚠️ 缀在**句末**（`。`/`；` 之后），不切在句子中间。
    `fields` 可收窄可落笔的字段（默认 `_MARK_FIELDS`）—— 跨节矛盾这类**讲解**不该落到
    自测题干（`q`）上（实测：转录前后不一致那条被缀进了「性别如何改变经验的斜率？」）。
    """
    # ⚠️ 默认只取 needle 前 14 字（容错：模型的行文常有出入）；`exact=True` 时用**整条**
    #    —— E 的 needle 是一条等式（`…=g_j b_h`），截到 14 字会先命中**另一条**等式所在的段落，
    #    标注就飘到"第一条式子"上去了（F15-4① 明确要贴在与定义不符的那条上）。
    needle = re.sub(r"\s+", "", str(needle or ""))
    needle = needle if exact else needle[:14]
    cands: list = []
    for path in paths_of(s):
        if _field_name(path) not in (fields or _MARK_FIELDS):
            continue
        v = _get(s, path)
        if isinstance(v, str) and v:
            cands.append((path, v))
    order = list(fields or _MARK_FIELDS)
    cands.sort(key=lambda pv: order.index(_field_name(pv[0]))
               if _field_name(pv[0]) in order else 99)   # 讲解优先，别落到 `conclusion` 之后
    for path, v in cands:
        hit = _find_ws(v, needle) if needle else None
        if hit:
            # ⚠️ F15-4①：标在**含 needle 的那一句之后**，不是段末 ——
            #    「差一个负号」要贴着与定义不符的那条等式，不是飘在整段末尾。
            j = min((p for p in (v.find(ch, hit[1]) for ch in "。；;") if p >= 0),
                    default=-1) + 1
            if j <= 0:
                j = max([v.rfind(p) for p in "。；;"]) + 1
            _set(s, path, v[:j] + mark + v[j:])
            return True
    if cands:
        path, v = cands[0]
        _set(s, path, v.rstrip() + mark)
        return True
    return False


def _beta(tok: str) -> str:
    """归一后的系数名 → 给人看的写法（`b3` → `β₃`；认不出就原样）。**纯函数。**"""
    m = re.fullmatch(r"b(\d+)", str(tok or ""))
    if not m:
        return str(tok)
    return "β" + "".join("₀₁₂₃₄₅₆₇₈₉"[int(d)] for d in m.group(1)) \
        if all(d.isdigit() for d in m.group(1)) else "β" + m.group(1)


def _snip(t: str, n: int = 46) -> str:
    s = re.sub(r"\s+", "", str(t or ""))
    return (s[:n] + "…") if len(s) > n else s


# ---- F14-2 ④ 组件挂错节（对照表/步骤图的时间与它挂的那一节对不上）----------------
#: 组件标题里的 🎙 时刻：`🎙 三种模型评估指标（09:07–09:10）`。
_TITLE_TIME = re.compile(r"(\d{1,2}):(\d{2})(?::(\d{2}))?")
#: 判定"挂错"的余量（分钟）：组件讲的时刻比所挂小节**早**这么多以上 → 挂错了。
COMPONENT_LAG_MIN = 3


def retime_components(review: dict) -> tuple:
    """对照表/步骤图挂错小节时，按**时间**挪到真正讲它的那一节。F14-2 ④

    ⚠️ 作者实测（v3 busi §7，ts=09:13）：那一节挂着「🎙 三种模型评估指标（09:07–09:10）」——
       整张表讲的是 09:07–09:10，挂到了 6 分钟后的节里；读者会以为它是这节的内容。
    判据（只看时间，不看内容 —— 域中立）：标题里的 🎙 起点比所挂小节的 ts **早
    `COMPONENT_LAG_MIN` 分钟以上** → 找 ts ≤ 组件起点的那一节（取最晚的）搬过去；
    目标节已有同类组件（一节只放一张对照表/一张步骤图）→ 宁缺丢掉，并记账。
    返回 `(review, [{"repaired": n, "dropped": m}], warns)`。
    """
    out = copy.deepcopy(review)
    secs = [s for s in (out.get("sections") or []) if isinstance(s, dict)]
    if len(secs) < 2:
        return review, [], []
    ts = [_hms(s.get("ts")) for s in secs]
    moved: list = []
    gone: list = []
    for i, s in enumerate(secs):
        for key in ("compare", "flow"):
            comp = s.get(key)
            if not isinstance(comp, dict):
                continue
            t = _TITLE_TIME.search(str(comp.get("title") or ""))
            if not t or ts[i] is None:
                continue
            start = int(t.group(1)) * 3600 + int(t.group(2)) * 60
            if start >= ts[i] - COMPONENT_LAG_MIN * 60:
                continue                      # 时间对得上（或组件讲的是本节之后）→ 不动
            cands = [j for j in range(len(secs))
                     if ts[j] is not None and ts[j] <= start and j != i]
            if not cands:
                cands = [0]
            target = max(cands, key=lambda j: ts[j] or 0)
            if secs[target].get(key):
                s.pop(key, None)              # 目标节已经有同类组件 → 宁缺
                gone.append(f"{key}「{_one(str(comp.get('title')))[:20]}」")
                continue
            secs[i].pop(key, None)
            secs[target][key] = comp
            moved.append(f"{key}「{_one(str(comp.get('title')))[:20]}」"
                         f"§{i + 1}→§{target + 1}")
    bits = []
    if moved:
        bits.append(f"{len(moved)} 个组件挂错了节，已按时间挪回：" + "、".join(moved[:3]))
    if gone:
        bits.append(f"{len(gone)} 个挂错且目标节已有同类 → 宁缺丢掉：" + "、".join(gone[:3]))
    warns = ["组件错挂：" + "；".join(bits)] if bits else []
    return out, [{"repaired": len(moved), "dropped": len(gone)}], warns


# ---------------------------------------------------------------- 渲染前的收尾小闸
#: 括注（两种括号都认）：`（ANOVA）` / `(ANOVA)`。
_GLOSS = re.compile(r"[（(]([^）)]{1,60})[）)]")
#: 钟点/节号里引用的图表指路词。
_PTR_WORDS = ("下表", "上图", "下图", "见表", "见图", "如表", "如图", "见下表",
              "见下图", "见上图", "见下表", "看下表", "看下图")


def collapse_dup_gloss(text: str, *, window: int = 12, immune=frozenset()) -> str:
    """同一个括注在 `window` 字内出现两次 → 只留前一个。**删字走 `safe_delete`。**

    ⚠️ F15 §3（作者手读 v3）：`方差分析（ANOVA）模型（ANOVA）` —— 模型自己叠了一个括注。
       两种括号风格（`（）` 与 `()`）都要认。
    """
    s = str(text or "")
    kept: list = []                                  # [(上一个括注的结尾, 归一后的内容)]
    drop: list = []
    for m in _GLOSS.finditer(s):
        inner = " ".join(m.group(1).split()).lower()
        if any(0 <= m.start() - e <= window and i == inner for e, i in kept):
            drop.append((m.start(), m.end()))
            continue
        kept.append((m.end(), inner))
    if not drop:
        return s
    # ⚠️ 删掉 `(OLS)` 会留下两个空格（`用  与`）—— 收尾把空档并掉（§2 的 `fix`）。
    out, _b, kept_spans = safe_delete(s, drop, immune=immune,
                                      fix=lambda t: re.sub(r"[ \t]{2,}", " ", t))
    return out if kept_spans else s


#: 复习安排/账本里指路的节号（`第 3 节` / `第二节` / `第 21 节`）。
_SEC_PTR = re.compile(r"[（(]?\s*第\s*[0-9一二三四五六七八九十百]+\s*节\s*[）)]?")
#: 整块括注**只装着**一句节指路（`（对照第二节）`）→ 连括号一起拿走。
_SEC_PTR_WHOLE = re.compile(r"[（(][^）)]{0,10}第\s*[0-9一二三四五六七八九十百]+\s*节"
                            r"[^）)]{0,10}[）)]")


def clean_ledger(review: dict) -> tuple:
    """账本两处收尾（F15-D/H）。**只删/只收窄，不改写。**

    ① **空修正行**：`fixed` 为空的 `corrections` 行在页尾渲染成
       「「原话」→ （依据：…）」—— 一个空箭头。丢掉（依据仍在 `basis` 里，不影响溯源）。
    ② **悬空节号**：`corrections[].where`（如 `第 3 节（🎙 17:02）`）与 `review_plan`
       里的「第 N 节」是指路，指错了没人能查 → 只留时间戳（`（🎙 17:02）`），
       取消节号；`where` 里没有时间戳、去掉节号后什么都不剩 → 保留原样（宁可留着）。
    """
    out = copy.deepcopy(review)
    dropped: list = []
    rows: list = []
    for c in (out.get("corrections") or []):
        if (isinstance(c, dict) and c.get("kind") != "check"
                and not _one(c.get("fixed"))):
            dropped.append(_one(c.get("where")) or _one(c.get("spoken"))[:24])
            continue
        rows.append(c)
    if out.get("corrections") is not None:
        out["corrections"] = rows
    n = 0
    for c in rows:
        if not isinstance(c, dict):
            continue
        w = _one(c.get("where"))
        if not w or not _SEC_PTR.search(w):
            continue
        new = _one(re.sub(r"\s{2,}", " ", _SEC_PTR.sub("", w)).strip(" ，,、"))
        if new and new != _one(w):
            c["where"] = new
            n += 1
    plan = []
    for item in (out.get("review_plan") or []):
        s = _one(item)
        if not s:
            continue
        if _SEC_PTR.search(s):
            # 「（对照第二节）」整块拿走 —— 只删 `第二节` 会留下没配对的「（对照」。
            new = _SEC_PTR_WHOLE.sub("", s)
            if new == s:
                new = _SEC_PTR.sub("", s)
            new = _one(re.sub(r"\s{2,}", " ", new)).strip(" ，,、")
            if (new and new != s and new.count("（") == new.count("）")
                    and new.count("(") == new.count(")")):
                plan.append(new)
                n += 1
                continue
        plan.append(item)
    if out.get("review_plan") is not None:
        out["review_plan"] = plan
    warns = []
    if dropped:
        warns.append(f"账本里 {len(dropped)} 行修正没有修正值（空箭头）→ 丢掉")
    if n:
        warns.append(f"{n} 处账本/复习安排里的「第 N 节」指路收窄成时间戳")
    return out, [{"dropped": len(dropped), "repaired": n}], warns


def drop_dangling_pointers(review: dict) -> tuple:
    """指路句指着**本节根本没有的组件**（`三者差别看下表即可`，这一节没有表）→ 删掉那句。

    ⚠️ F15-G（作者手读 v3/ecs170 §22）：组件被前面的守卫挪走/丢掉之后，指路句成了悬空的
       指针 —— 读者按它去看表，什么也找不到。判据只认**表格/图**（`下表`/`上图`…），
       并且那一节确实没有 `compare`/`flow`/`plot` 时才动手；删句走 `safe_delete`。
    """
    out = copy.deepcopy(review)
    gone: list = []
    for i, s in enumerate(out.get("sections") or [], 1):
        if not isinstance(s, dict):
            continue
        if any(s.get(k) for k in ("compare", "flow", "plot")):
            continue                              # 本节确实有表/图 → 指路是对的
        immune = immune_from_review(out)
        for path in paths_of(s):
            if _field_name(path) not in ("prose", "pitfalls"):
                continue
            v = _get(s, path)
            if not isinstance(v, str) or not any(w in v for w in _PTR_WORDS):
                continue
            spans = [(a, b) for seg, a, b in _sentences_with_spans(v)
                     if any(w in seg for w in _PTR_WORDS)]
            if not spans:
                continue
            new, _blocked, kept_spans = safe_delete(v, spans, immune=immune)
            if not kept_spans or new == v:
                continue
            new = new.strip()
            if new:
                _set(s, path, new)
            else:
                # 整段就是一句指路 → 这个元素直接不出（不留空串：渲染层按段写）
                holder = s
                for k in path[:-1]:
                    holder = holder[k]
                if isinstance(holder, list):
                    holder.remove(v)
                else:
                    holder[path[-1]] = ""
            gone.append(f"§{i}「{_one(v)[:18]}」")
    warns = []
    if gone:
        warns.append(f"删掉 {len(gone)} 句指向不存在图表的指路句：" + "、".join(gone[:3]))
    return out, [{"dropped": len(gone)}], warns


def _math_bits(expr: str, limit: int = 88) -> str:
    """式子 → `$…$`（**整条，绝不截断**）；太长或夹中文 → `""`（退回说人话）。**纯函数。**

    ⚠️ F15-4①：标注里贴裸 LaTeX / 截断的 LaTeX 是最坏的一档 —— KaTeX 渲染不出来，
       读者看到的是 `\\partialE/\\partialw_{hj}=(\\hat y_j-y_j)\\,\\ha…`。
    """
    s = _one(expr)
    if not s or len(s) > limit or "$" in s or re.search(r"[\u4e00-\u9fff]", s):
        return ""
    return f"${s}$"


def _symbol_sign_mark(c: dict) -> str:
    """F15-E 的标注文案（F15-4①）：式子要么**整条**进 `$…$`，要么一个字都不贴。**纯函数。**"""
    term = _one(c.get("term") or "")
    other = _one(c.get("other") or "")
    defs = {str(k): str(v) for k, v in (c.get("defs") or {}).items()}
    shown, names = [], []
    for k, v in defs.items():
        names.append(_one(k))
        bits = _math_bits(f"{_one(k)}={_one(v)}")
        shown.append(bits or _one(k))
    how = ("按本节的 " + "、".join(shown) + " 代入" if shown else "代入本节的记号定义")
    want = _math_bits(f"{term}=-{other}")
    said = _math_bits(f"{term}={other}")
    why = _one(c.get("why") or "两边对不上")
    if want and said:
        return f"（⚠ {how}，应为 {want}；正文写成 {said}，{why}）"
    if said:
        return f"（⚠ {how}，正文这一条与定义不符（{why}）：{said}）"
    if names:
        return f"（⚠ {how}，本节第二次写的那个式子与定义不符（{why}））"
    return f"（⚠ 本节同一个左端写了两次，代入定义后两边对不上（{why}））"


#: 匹配时**忽略**的字距宏（`g_j b_h` 与 `g_j\,b_h` 是同一个式子，模型两种都写）。
_THIN = r"(?:\s|\\[,;! ])*"


def _find_ws(text: str, needle: str):
    """在原文里找 `needle`（忽略空白与 `\\,` 类字距宏）→ `(起, 止)`；找不到 → `None`。**纯函数。**

    ⚠️ 不能只去空白：v3/ecs §53 的代数行写 `g_j b_h`、讲解写 `g_j\\,b_h` —— 只去空白时
       两者不相等，标注就找不到它的落点（退成"贴在本段第一句上"）。
    """
    n = re.sub(r"\\[,;! ]", "", re.sub(r"\s+", "", str(needle or "")))
    if not n:
        return None
    try:
        rx = re.compile(_THIN.join(re.escape(ch) for ch in n))
    except re.error:
        return None
    m = rx.search(str(text or ""))
    return (m.start(), m.end()) if m else None


def _raw_snip(t: str, n: int = 40) -> str:
    """截断但**保留原有空格**（引语必须逐字，`_snip` 会把空格全吃掉）。**纯函数。**

    ⚠️ F15-4④：截断要落在**词边界**上。拉丁文按空格退到最后一个整词
    （原来 `…each additional year of experience, goin…` 从词中间切开）；
    中文按字切（没有词边界可用），标点处也可以收。
    """
    s = str(t or "").strip()
    if len(s) <= n:
        return s
    head = s[:n]
    if re.search(r"[A-Za-z]$", head) and re.search(r"[A-Za-z]", s[n:n + 1]):
        # 正切在一个拉丁词的中间 → 退到该词之前的最后一个空格
        cut = head.rfind(" ")
        if cut >= max(12, int(n * 0.5)):
            head = head[:cut]
    return head.rstrip(" ,;:、，；：") + "…"


def _hhmmss(t: str) -> str:
    """`09:33` → `09:33:00`（一律写成 HH:MM:SS）。**纯函数。**"""
    m = re.match(r"\s*(\d{1,2}):(\d{2})(?::(\d{2}))?", str(t or ""))
    return f"{int(m.group(1)):02d}:{m.group(2)}:{m.group(3) or '00'}" if m else str(t or "")


def _tsec(t: str) -> int:
    """`HH:MM(:SS)` → 秒；认不出 → `-1`。**纯函数。**"""
    m = re.match(r"\s*(\d{1,2}):(\d{2})(?::(\d{2}))?", str(t or ""))
    if not m:
        return -1
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3) or 0)


def _source_conflict_section(secs: list, src: dict) -> int:
    """哪一节的**时间跨度**同时盖住那两句原话（1 起数；找不到 → 0）。**纯函数。**

    ⚠️ 判据是"本节 ts → 下一节 ts"，不是"本节 ts"：§16 的 ts 是 09:31，而两句原话
       发生在 09:33:35 / 09:34:15 —— 它们落在 §16 的跨度里（§17 从 09:35 开始）。
    """
    qts = [x for x in (_tsec(q.get("ts")) for q in (src.get("quotes") or [])) if x >= 0]
    if len(qts) < 2:
        return 0
    starts = [_tsec(s.get("ts")) for s in secs]
    for i in range(len(secs)):
        a = starts[i]
        if a < 0:
            continue
        b = next((x for x in starts[i + 1:] if x >= 0), None)
        if all(a <= q and (b is None or q < b) for q in qts):
            return i + 1
    return 0


def flag_conflicts(review: dict, *, entries: list | None = None) -> tuple:
    """对不上的地方（跨节 / 本节自己的式与讲解）→ 原位标 ⚠。F14 ⑧⑨ + F14-2 ⑩⑪

    ⚠️ 为什么必须做（作者逐字读 v2）：§14 写 `$z\\le0$ 输出 0`（还把「z=0 输出 1」
       列成常见错误），§15 写「大于等于 0 输出 1」；BUSI §15 自补例男性 0.7、
       §18 又写男性 0.3 —— **读者按哪一节复习都可能记反**。转录自己前后不一致时
       （`if you're male … 0.3 plus 0.4` vs `for female workers … beta three plus
       beta four`），笔记要说出"课上前后说法不一致"，不许挑一个当定论。
    ⚠️ 只标不改（改哪边都可能改错，与三道核同一条纪律）。
    返回 `(review, [{"flagged": n}], warns)`。
    """
    import note_conflict
    out = copy.deepcopy(review)
    secs = [s for s in (out.get("sections") or []) if isinstance(s, dict)]
    if not secs:
        return review, [], []
    got = note_conflict.conflicts(out, entries=entries)
    src = next((c for c in got if c["kind"] == "source-self-conflict"), None)
    n = 0
    details: list = []
    for c in got:
        if c["kind"] == "source-self-conflict":
            continue
        kind = c["kind"]
        if kind == "sign-flip":
            # ⭐ F15-F：跨节同一个符号一会减一会加 → 落在**后一节**那句公式上。
            j = c["sections"][-1]
            later = secs[j - 1]
            mark = f"（⚠ {c['why']}）"
            if _find_and_append(later, c["texts"][0], mark, fields=_MARK_TEXT_FIELDS):
                n += 1
                details.append(f"§{j}（{kind}：{c['sym']}）")
            continue
        if kind == "symbol-sign":
            # ⭐ F15-E：同一节里同一个左端写了两次、代入本节定义后对不上 → 标 ⚠。
            # ⚠️ F15-4①：标注里**不许出现裸的/截断的 LaTeX**（旧的 `\partialE/\partialw_{hj}=
            #    (\hat y_j-y_j)\,\ha…` 既渲染不出来也读不懂）。做法：把式子整条放进 `$…$`
            #    让 KaTeX 渲染；式子太长/夹中文 → 退回**说人话**（不贴式子）。
            #    ⚠ 落在**与定义不符的那一条等式**上（`g_j b_h` 那条），不是第一条。
            j = c["sections"][-1]
            later = secs[j - 1]
            mark = _symbol_sign_mark(c)
            if _find_and_append(later, c["texts"][1], mark, fields=_MARK_TEXT_FIELDS,
                                exact=True):
                n += 1
                details.append(f"§{j}（{kind}：{c['term']}）")
            continue
        if kind in ("dummy-slope", "missing-term"):
            # ⭐ F14-2 ⑩⑪：**本节自己**的式与讲解对不上（不是跨节）。
            #    ⚠ 要落在**讲解那句**上（读者先读到的就是它）。
            j = c["sections"][-1]
            later = secs[j - 1]
            if kind == "dummy-slope":
                first = c["detail"][0]
                mark = (f"（⚠ 本节自己的定义与讲解对不上：按「{first['label']}="
                        f"{first['v']}」该是 {'+'.join(_beta(t) for t in first['want'])}，"
                        f"讲解却说 {'+'.join(_beta(t) for t in first['said'])}）")
                needle = first["label"]
            else:
                # ⚠️ F15-B：原来这里是 `本节模型式里 {term} {why}`，而 `why` 本身就写着
                #    "本节的模型式里…" → 句子重复了一整截（作者手读抓到）。
                mark = (f"（⚠ {c['term']} {c['why']}，例题却给它一个系数）")
                needle = c["texts"][0]
            if _find_and_append(later, needle, mark, fields=_MARK_TEXT_FIELDS):
                n += 1
                details.append(f"§{j}（{kind}：{c.get('labels') or c.get('term')}）")
            continue
        i, j = c["sections"]
        later = secs[j - 1]
        title = _ref_title(secs[i - 1])
        if kind == "point-value":
            a, b = c["texts"]
            mark = (f"（⚠ 与「{title}」一节对不上：{_snip(a)}；{_snip(b)}）")
            needle = b
        else:
            v1, v2 = c["values"]
            mark = (f"（⚠ 与「{title}」一节对不上：{c['label']} 那节写 "
                    f"{','.join(f'{x:g}' for x in v1)}、这节写 "
                    f"{','.join(f'{x:g}' for x in v2)}）")
            needle = next((x for x in (later.get("prose") or [])
                           if c["label"] in str(x)), "")
        if _find_and_append(later, needle, mark, fields=_MARK_TEXT_FIELDS):
            n += 1
            details.append(f"§{j} ↔ §{i}（{c.get('label') or c.get('var')}）")
    # ── ⭐ F15-C：转录自己前后不一致 → **挂到真正讲它的那一节**去 ────────────────
    # ⚠️ 原来它被拼进上面**任何**一条 mark 的尾巴（`src_note`），于是落在了
    #    §15（09:29 t 检验那节）——可两句原话发生在 09:32:33 / 09:34:15，讲的是 §16。
    #    判据：那一节的**时间跨度**（本节 ts → 下一节 ts，`sources` 佐证）要同时盖住两句话。
    src_section = _source_conflict_section(secs, src) if src else 0
    if src and src_section:
        # ⚠️ 一律 `🎙 HH:MM:SS`：戳是**证据**（而且带 🎙 的时间戳不会被时间戳清理删掉）；
        #    引语保留原有空格（作者 C：不许把英文原话的空格吃掉）。
        q = [x for x in (src.get("quotes") or []) if str(x.get("ts") or "").strip()][:2]
        note = ("（⚠ 课上口述本身前后不一致："
                + "；".join(f"🎙 {_hhmmss(x['ts'])} 说「{_raw_snip(x['quote'], 44)}」"
                           for x in q) + "）") if q else ""
        if note and _find_and_append(secs[src_section - 1], str(q[0].get("quote") or "")[:12],
                                     note, fields=("prose", "pitfalls", "conclusion", "intuition")):
            details.append(f"§{src_section}（课上口述前后不一致）")
    warns = []
    if n:
        warns.append(f"{n} 处跨节对不上已在后一节原位标 ⚠：" + "、".join(details))
    if src:
        where = (f"（已挂到 §{src_section}）" if src_section
                 else "（没找到时间跨度同时盖住两句的那一节 —— 未挂正文）")
        warns.append("课上口述前后不一致：" + "；".join(
            f"{x['group']} {x['ts']}" for x in src["quotes"][:3]) + where)
    return out, [{"flagged": n}], warns


#: 去重句首的连接词/强调符：`但课上只给了片段…` 与 `⚠ 课上只给了片段…` 是同一句。
_DUPE_LEAD = re.compile(r"^(?:[⚠️!！?？\s、，。:：]+|但|不过|另外|同时|而且|而|于是|因此|所以|又|还|也)+")


def _sentence_key(s: str) -> str:
    return _DUPE_LEAD.sub("", re.sub(r"\s+", "", s))


def dedupe_sentences(review: dict, *, min_chars: int = 10) -> tuple:
    """同一字段里**整句的重复**只留第一遍（模型抄格式抄重了，读者看两遍）。

    ⚠️ 只删**长句**（≥ `min_chars` 字**且**以句末标点收尾）：短句重复是正常行文
       （"对。对。"），拿它开刀会误伤；`⚠ 课上只给了片段…` 这种 40 字的整句不在此列。
    ⚠️ 带换行的字段整段跳过（rejoin 会把行结构吃掉）；`cover` 是台账，不碰。
    """
    out = copy.deepcopy(review)
    gone: list = []
    immune = immune_from_review(review)
    for path in paths_of(out):
        if path and path[0] in _SKIP_TOP:
            continue
        v = _get(out, path)
        if not isinstance(v, str) or "\n" in v:
            continue
        if len(_sentences(v)) < 2:
            continue
        seen, drop = set(), []
        for seg, a, b in _sentences_with_spans(v):
            if len(seg.strip()) >= min_chars and seg.rstrip()[-1] in "。！？!?":
                k = _sentence_key(seg)
                if k in seen:
                    drop.append((a, b))
                    continue
                seen.add(k)
        if drop:
            new_v, blocked, kept_spans = safe_delete(v, drop, immune=immune)
            gone.extend(v[a:b].strip() for a, _b in kept_spans)
            if kept_spans and new_v != v:
                _set(out, path, new_v)
    warns = [f"正文里删掉 {len(gone)} 处重复句"] if gone else []
    return out, gone, warns


#: 正文里的钟点时间戳（"教授在 12:50:41 以笑声结束…"）。节标题/出处字段上的时间戳不碰。
_CLOCK_AT = re.compile(r"(?:在|于)\s*[\[(]?\d{1,2}:\d{2}(?::\d{2})?[\])]?\s*")
#: 戳挡在"的"前（"12:50:28 的「a one day pleasure」是…"）→ 连"的"一起收，
#: 不然剩个残句（"； 的「…」是…"，回放自查抓到）。
_CLOCK_POSS = re.compile(r"(?<![:\d])\d{1,2}:\d{2}(?::\d{2})?\s*的")
_CLOCK_BARE = re.compile(r"(?<![:\d])\[?\d{1,2}:\d{2}(?::\d{2})?\]?(?![:\d])")


def _swallow_parens(text: str, a: int, b: int) -> tuple:
    """删除区间**向外含住包它的括号**（`（12:50:28）` 整对拿走，不留空括号）。**纯函数。**

    ⚠️ 只吃**紧贴着**、且只包住这一段的括号：`（12:50:28）` → 连括号删；`（12:50:28 说的）`
       里括号还包着别的字 → 不动（留给 §2 的空括号护栏去打回）。
    """
    s = str(text or "")
    pairs = (("（", "）"), ("(", ")"))
    for op, cl in pairs:
        if a > 0 and b < len(s) and s[a - 1] == op and s[b] == cl:
            return (a - 1, b + 1)
    return (a, b)


def strip_clock_times(review: dict) -> tuple:
    """正文里的钟点时间戳删掉 —— 时间在**节标题**上；正文指位置写（第 N 节）。

    ⚠️ 不碰：`title`/`ts`/`sources`（题目出处的戳是**该有的**）、**`heading`**
       （节标题里 `[13:16]` 前缀就是"时间在标题上"的那一处）、`checks`/`corrections`/
       `cover`（台账，一个字都不许动；`corrections.where` 存的就是原话发生时间）、
       以及引语字段（原话引用必须逐字）。
       —— 2026-10-18 回放自查抓到：初版把这些全删了（9 个标题前缀 + 账本 where）。
    """
    out = copy.deepcopy(review)
    n = 0
    immune = immune_from_review(review)
    for path in paths_of(out):
        if not path or path[0] in _SKIP_TOP or path[0] in ("checks", "corrections"):
            continue
        if _field_name(path) in ("title", "ts", "sources", "heading", "where", "quote", "spoken"):
            continue
        v = _get(out, path)
        if not isinstance(v, str) or ":" not in v:
            continue
        # ⚠️ `🎙 09:34:15 说「…」` 里的戳是**证据**（转录原话发生的时间），不是行文里的
        #    钟点 —— 与 `sources` 上的戳同一条纪律。实测：第二遍清理把跨节矛盾 ⚠ 里刚写进去
        #    的那个时间戳吃掉了，句子剩成「 说「…」」。
        spans = [_swallow_parens(v, m.start(), m.end())
                 for rx in (_CLOCK_AT, _CLOCK_POSS, _CLOCK_BARE)
                 for m in rx.finditer(v)
                 if not v[max(0, m.start() - 3):m.start()].rstrip().endswith("🎙")]
        if not spans:
            continue
        # 收尾规整（§2 的 `fix`）：删完把空档并掉 —— 护栏看的是**成品**。
        # ⚠️ 空括号**不在这里**收：括号随戳一起被 `_swallow_parens` 整对拿走，
        #    剩下「（）」就交给 §2 ③ 打回（那才是纪律本身）。
        v2, blocked, kept_spans = safe_delete(
            v, spans, immune=immune,
            fix=lambda t: re.sub(r"\s{2,}", " ", t).strip(" "))
        if not kept_spans:
            continue
        v2 = re.sub(r"\s{2,}", " ", v2).strip(" ")
        if v2 != v:
            _set(out, path, v2)
            n += 1
    warns = [f"正文里删掉 {n} 处钟点时间戳（时间在节标题上）"] if n else []
    return out, [], warns


#: 纯算术问题的尾巴（"…等于多少？" / "…是多少？"）。
_ARITH_TAIL = re.compile(r"[，,、;；：:\s]*(?:等于|是|得|为|＝|=)\s*"
                         r"(?:多少|几|什么数(?:字|值)?|什么)?[？?。！!]?\s*$")
_ARITH_WORDS = re.compile(r"除以|除|乘以|乘|加上|加|减去|减")
_ARITH_ONLY = re.compile(r"^[\d\s.+\-*/×÷()（）＝=]+$")


def _trivial_arith_q(q: str) -> bool:
    """纯算术问题（"5.3除以3等于多少？"）：计算器能答 → 复习价值为零 → 丢。"""
    s = str(q or "").strip()
    core = _ARITH_TAIL.sub("", s)
    if core == s or len(core) > 24:
        return False
    core = _ARITH_WORDS.sub("/", core).replace(" ", "")
    return bool(_ARITH_ONLY.fullmatch(core)) and any(ch.isdigit() for ch in core)


def drop_trivial_cues(review: dict) -> tuple:
    """每节的 `cues` 里丢掉纯算术问题（2026-10-18 Δ）。

    ⚠️ 只动 `cues`（复习自问自答用）：`q`（本节回答）、`qa` 的位置和用途不同，
       那里出现数字题另有上下文，不敢一刀切。
    """
    out = copy.deepcopy(review)
    dropped: list = []
    for s in (out.get("sections") or []):
        if not isinstance(s, dict):
            continue
        cues = s.get("cues")
        if not isinstance(cues, list):
            continue
        kept = []
        for c in cues:
            if isinstance(c, str) and _trivial_arith_q(c):
                dropped.append(c)
                continue
            kept.append(c)
        s["cues"] = kept
    warns = [f"丢掉了 {len(dropped)} 条纯算术线索问题（计算器能答，帮不了复习）"] if dropped else []
    return out, dropped, warns


# ---------------------------------------------------------------- F9b：支持性核对（Jev）
#: E3 的指令**原文**（`jev_exp/run_e3.py`）—— 一词不改：分辨力建立在它上面（AUC 1.000）。
SUPPORT_INSTR = ("Look at the transcript excerpt only. Does this transcript excerpt state "
                 "the following? \"{claim}\" Answer yes only if the excerpt actually says "
                 "it; a mathematically correct fact that the excerpt does not state is not "
                 "a yes.")
#: E3 的 state 前言**原文**。
SUPPORT_PREAMBLE = ("This is a transcript excerpt from a university lecture. Each line is "
                    "one sentence the lecturer or a student said, in order, with its "
                    "timestamp.")
#: 材料（📖）判官的平行版本：只换"来源"两处（F9c 作者裁定 —— 📖 题面对材料判）。
SUPPORT_MAT_INSTR = ("Look at the materials excerpt only. Does this materials excerpt "
                     "state the following? \"{claim}\" Answer yes only if the excerpt "
                     "actually says it; a mathematically correct fact that the excerpt "
                     "does not state is not a yes.")
SUPPORT_MAT_PREAMBLE = ("This is an excerpt from the course materials (slides / exam "
                        "sheet) of a university lecture. Each numbered line is one "
                        "sentence or bullet from those materials.")
#: E3 的标签分布：supported 最低 0.31、unsupported 最高 0.04 —— 0.15 在两簇中间的空档。
SUPPORT_TAU = 0.15
#: F9c（作者裁定）：局部性优先 —— **按节分批判**（每节自己 ±8/+32 的窗口），每节 ≤15 次请求。
#: Jev 便宜（~$0.0002/次）且永远在线；旧的"并集窗口"会把窗口稀释成整节课。
SUPPORT_MAX_REQ = 15
SUPPORT_BATCH = 20
#: 「讲义说过 X」的句子判据 + 「自补」豁免（✏️ 本来就不要求转录支持）。
_SUPPORT_WORDS = re.compile(r"课上|老师|本课")
_SUPPORT_SELF = re.compile(r"✏️|自补|自编")
#: 房子里的免责/对照句式 —— 不是「讲义说过」：⚠ 免责句与教材对照（📚）句。
#: ⚠️ 后者若收进来，[转录里没找到这句] 会挂到"未核对"句自己头上（F9b 实测）。
_SUPPORT_SKIP = re.compile(r"📚|未核对|只给了片段|核算对不上|数字对不上|没找到这句原话")
#: 例题标题里的 `🎙 13:40` / `（🎙 13:36–13:38）` —— 课**实际用**它的时刻。
#: ⚠️ 窗口要用它，不是小节 `ts`（F9b 实测：§3 小节 13:25:49、例题标 🎙 13:40，
#:    用小节 ts 会把窗口放到"刚宣布做 Exercise 5"那几句上，题目本身在 14 分钟外）。
_TS_IN_TITLE = re.compile(r"🎙\s*(\d{1,2}:\d{2}(?::\d{2})?)")
#: 检索停用词：LaTeX 命令与排版词 —— 不是内容。
_RETR_STOP = {"mathrm", "frac", "approx", "rightarrow", "to", "times", "text",
              "operatorname", "begin", "end", "align", "leq", "geq", "left", "right"}
#: 命名实体过滤（题面给定里的大写英文词尾巴）：句子开头的常见词不算实体。
_ENTITY_STOP = {"the", "and", "for", "with", "from", "this", "that", "what", "why",
                "when", "how", "are", "was", "were", "you", "they", "their", "about",
                "into", "use", "one", "two", "can", "not", "but", "its", "has", "have"}


def _retr_tokens(claim: str) -> dict:
    """claim → 检索 token（数字 / 拉丁词 / 中文 2-gram）。**纯函数。**"""
    t = str(claim or "")
    nums = set(re.findall(r"\d+(?:\.\d+)?", t))
    latin = {w.lower() for w in re.findall(r"[A-Za-z]{2,}", t)} - _RETR_STOP
    zh = re.sub(r"[^\u4e00-\u9fff]", "", t)
    grams = {zh[i:i + 2] for i in range(len(zh) - 1)} if len(zh) >= 2 else set()
    return {"nums": nums, "latin": latin, "grams": grams}


#: ⭐ F11 ④（作者裁定）：符号 claim（`MC=12`、`AC=28`、`AC(5)`）要能**原样**检索材料 ——
#: 生成侧的材料检索（`materials._query_terms`）会丢 <3 字符词与纯数字，`$MC=12$` 于是
#: 0 个查询词 → 材料片段取不到 → 判官看到空材料 → 假警（F10 §五：5 条真·假警）。
#: 支持核对这边**不走 BM25**：把 `Name=number` / `Name(number)` 当**精确 token**，
#: 在材料片段里做归一化子串扫描（NFKC 把数学斜体 𝑀𝐶 折成 ASCII，去空白/花括号/反斜杠）。
_RETR_SPAN_EQ = re.compile(r"(?<![A-Za-z])[A-Za-z][A-Za-z0-9]{0,6}\s*[=＝]\s*-?\d+(?:\.\d+)?")
_RETR_SPAN_CALL = re.compile(r"(?<![A-Za-z])[A-Za-z][A-Za-z0-9]{0,6}\s*\(\s*-?\d+(?:\.\d+)?\s*\)")


def _norm_span(s) -> str:
    """公式片段归一化：NFKC → 小写 → 去空白/`$`/花括号/反斜杠/`^`/`_`/全角等号。**纯函数。**"""
    s = unicodedata.normalize("NFKC", str(s or "")).lower()
    s = s.replace("＝", "=").replace("−", "-")
    return re.sub(r"[\s$\\{}^_]", "", s)


def _retr_spans(claim: str) -> list:
    """claim 里的 `Name=number` / `Name(number)` / `$…=…$` 片段（归一化）。**纯函数。**"""
    t = str(claim or "")
    out: list = []
    for m in _RETR_SPAN_EQ.finditer(t):
        out.append(_norm_span(m.group(0)))
    for m in _RETR_SPAN_CALL.finditer(t):
        out.append(_norm_span(m.group(0)))
    for m in re.finditer(r"\$[^$]{2,60}\$", t):        # 整条公式（含 `=`）也当一个 token
        inner = m.group(0).strip("$")
        if "=" in inner:
            out.append(_norm_span(inner))
    return [s for s in out if s]


def _symbol_excerpts(materials, claim: str, *, k: int = 2) -> list:
    """符号 claim → 材料里**含这个符号片段**的片段（精确 token 扫描）。**纯函数（只读材料）。**"""
    spans = _retr_spans(claim)
    if not spans:
        return []
    out: list = []
    for c in (getattr(materials, "_chunks", None) or []):
        blob = _norm_span(getattr(c, "text", ""))
        if any(s in blob for s in spans):
            out.append(str(getattr(c, "text", "") or ""))
            if len(out) >= k:
                break
    return out


def _retr_hits(entry: dict, toks: dict) -> list:
    """条目命中的 token（`(kind, token)` 列表）。**纯函数。**

    数字/拉丁词用**词边界**匹配（`12` 不撞 `312`、`ac` 不撞 `interaction`），
    中文 2-gram 直接查 ZH 行。
    """
    if not isinstance(entry, dict):
        return []
    en = (str(entry.get("en") or "") + " " + str(entry.get("asr") or "")).lower()
    zh = str(entry.get("zh") or "")
    zhc = re.sub(r"[^\u4e00-\u9fff]", "", zh)
    out: list = []
    for n in toks["nums"]:
        pat = r"(?<!\d)" + re.escape(n) + r"(?!\d)"
        if re.search(pat, en) or re.search(pat, zh):
            out.append(("num", n))
    for w in toks["latin"]:
        pat = r"(?<![a-z])" + re.escape(w) + r"(?![a-z])"
        if re.search(pat, en):
            out.append(("lat", w))
    for g in toks["grams"]:
        if g in zhc:
            out.append(("gram", g))
    return out


def _retr_weight(df: int, n: int, *, lo: float = 0.5, hi: float = 6.0) -> float:
    """token 的 idf 权重：`w = log2(N/df)`，贴地 `lo`、封顶 `hi`。**纯函数。**

    ⭐ F9d 实测（SOC §22）：满课都出现的词（UCD）不该和稀有词（高中/退出/停业）
    同权 —— 越稀有越硬。只出现一两次的词顶到 6，满课词落到地板 0.5。
    """
    return max(lo, min(hi, math.log2(max(1, n) / max(1, df))))


def _retr_score(entry: dict, toks: dict, weights: dict | None = None) -> float:
    """条目得分 = 命中 token 的 idf 权重之和（`weights=None` → 数命中条数）。**纯函数。**"""
    hits = _retr_hits(entry, toks)
    if weights is None:
        return float(len(hits))
    return float(sum(weights.get(t, 1.0) for t in hits))


def _support_windows(entries: list, claim: str, *, prior=None, fallback=None,
                     limit: int = 2) -> list:
    """claim → 检索出的 1–2 个窗口 `[(lo, hi)]`。**纯函数。**

    ⭐ F9d（作者实测两轮）：正文 claim 没有自己的时间戳，拿小节 `ts` 取窗口会离讲师
    真说的地方很远（SOC：claim 在 §13:27，原话在 **13:30:16** → 假警）。改为**检索**：
    claim 的数字/拉丁词/中文 2-gram（EN+ASR+ZH 一起看，中文 claim 对 ZH 行）——
    ① 命中条目的「前 8 后 32 行」作候选窗；② 窗口分 = **窗口覆盖到的 token 的 idf
    权重之和**（同一个词在窗口里刷 30 遍也只算一次 —— 密集的常见词区不该压过
    含稀有词的真锚区，SOC §22 实测：UCD 满课都是，把窗口拽到了错的区域）；
    ③ 取分最高的 1–2 个互不重叠的窗口。
    · `prior`（🎙 时刻）是**强先验**：给定时它那个窗口永远保留；
    · 完全没有命中 → `prior` → `fallback`（小节 ts）→ 全篇（老兜底）。
    """
    rows = [e for e in (entries or []) if isinstance(e, dict)]
    toks = _retr_tokens(claim)
    n = max(1, len(rows))
    hits: dict = {}                     # token → 出现的条目下标（去重：每 token 只记一次）
    for i, e in enumerate(rows):
        for t in _retr_hits(e, toks):
            hits.setdefault(t, []).append(i)
    weights = {t: _retr_weight(len(idxs), n) for t, idxs in hits.items()}

    def _cov(win: tuple) -> set:        # 窗口覆盖到的 token 集合
        lo, hi = win
        return {t for t, idxs in hits.items() if any(lo <= i < hi for i in idxs)}

    def cover(win: tuple) -> float:     # 窗口分 = 覆盖 token 的 idf 之和（token 不重复计）
        return sum(weights[t] for t in _cov(win))

    cands = sorted({(max(0, i - 8), min(n, i + 32))
                    for idxs in hits.values() for i in idxs})
    # 覆盖分相同时（窗口长度不同、含的 token 一样）取**更紧**的窗口 ——
    # 不然 40 行的窗会在「同分」里先被选走，恰好把结尾的原话截掉（F9d 实测）。
    def _rank(w: tuple):
        c = cover(w)
        return (-c, -(c / max(1, w[1] - w[0])), w[0])
    cands.sort(key=_rank)
    picked: list = []
    covered: set = set()
    for w in cands:                     # 互不重叠才算第二个窗口
        if any(w[0] <= p[1] and p[0] <= w[1] for p in picked):
            continue
        w_cov = _cov(w)
        if picked and not (w_cov - covered):
            continue        # 第二个窗口必须带来新 token —— 否则只是把密集常见词拖进来
        picked.append(w)
        covered |= w_cov
        if len(picked) >= max(1, limit):
            break
    if prior is not None:
        pw = _support_window(rows, prior)
        if pw[1] > pw[0] and pw not in picked:
            picked = ([picked[0]] if picked else []) + [pw]
            picked = picked[:limit]
    if not picked:
        picked = [_support_window(rows, prior if prior is not None else fallback)]
    return sorted(set(picked))


def _support_state_multi(entries: list, ranges: list) -> str:
    """多个窗口 → 一段 state（重叠/相邻先合并）。行号仍是**全篇**行号。

    ⭐ F9d：EN 行后面接 `（ZH）…` —— 中文 claim 要对得上转录里的中文行
    （作者点名）；一行一条，窗口仍 ≈40 行。
    """
    fused: list = []
    for lo, hi in sorted(ranges):
        if fused and lo <= fused[-1][1]:
            fused[-1] = (fused[-1][0], max(fused[-1][1], hi))
        else:
            fused.append((lo, hi))
    lines: list = []
    for lo, hi in fused:
        for j in range(lo, hi):
            e = entries[j]
            if not isinstance(e, dict):
                continue
            en = str(e.get("en") or e.get("asr") or "").strip()
            if not en:
                continue
            zh = str(e.get("zh") or "").strip()
            if zh and zh != en:
                lines.append(f"L{j + 1} [{e.get('ts')}] {en}  （ZH）{zh}")
            else:
                lines.append(f"L{j + 1} [{e.get('ts')}] {en}")
    return SUPPORT_PREAMBLE + "\n\n" + "\n".join(lines)


def _prompt_givens(prompt: str, *, min_len: int = 3) -> list:
    """例题题面 → 只取**具体给定**（作者 F9d 第 2 条）：公式/等式、数字、命名实体。

    ⛔ 祈使话（配平/合并/求/计算…）永远不进来 —— 那些话讲师不会逐字念，
       收进来只会制造"祈使题面 ≠ 口语逐字"的假警（CHE 实测 4/4）。
    返回的每一条都是题面的**逐字子串**（annotate 的定位靠它）。
    """
    t = str(prompt or "")
    out: list = []
    seen: set = set()

    def keep(x: str) -> None:
        x = str(x or "").strip()
        if len(x) >= min_len and x not in seen and x in t:
            seen.add(x)
            out.append(x)

    spans: list = []
    for m in re.finditer(r"\$[^$]+\$", t):
        keep(m.group(0))
        spans.append((m.start(), m.end()))
    for m in re.finditer(r"[A-Za-z][A-Za-z0-9\-]{1,}", t):
        if any(a <= m.start() < b for a, b in spans):
            continue
        w = m.group(0)
        if w.lower() in _RETR_STOP or w.lower() in _ENTITY_STOP:
            continue
        if any(c.isdigit() for c in w) or (w[0].isupper() and len(w) >= 3):
            keep(w)
    for m in re.finditer(r"\d+(?:\.\d+)?", t):
        if any(a <= m.start() < b for a, b in spans):
            continue
        keep(m.group(0))
    return out


def _hms(s) -> int | None:
    """`13:16:28` / `13:16–13:24` / `13:16-13:24` → 秒（取首个时刻；拿不到 None）。E3 的同款口径。"""
    try:
        raw = str(s or "").replace("–", ":").replace("-", ":")
        p = [int(x) for x in raw.split(":")[:3]]
    except ValueError:
        return None
    if not p:
        return None
    while len(p) < 3:
        p.append(0)
    return p[0] * 3600 + p[1] * 60 + p[2]


#: 一节（无区间 ts 时）默认跨度 15 分钟 —— 同 `note_v2._transcript_window` 的兜底。
SECTION_SPAN = 15 * 60


def _anchor_ok(anchor, sec) -> bool:
    """例题的 `anchor_ts` 是否**落在本节的时间范围**里（F11 §1）。**纯函数。**

    节 `ts` 可能是单点（`13:16`）或区间（`13:16–13:24`）；拿不到节起点就放行（保守）。
    不在范围 → 调用方丢掉这个时间戳，退回 F9d 的**检索取窗**。
    """
    t = _hms(anchor)
    if t is None:
        return False
    base = _hms(sec.get("ts")) if isinstance(sec, dict) else None
    if base is None:
        return True
    lo, hi = base, base + SECTION_SPAN
    parts = re.split(r"[–-]", str(sec.get("ts") or ""))
    if len(parts) > 1:
        hi2 = _hms(parts[1])
        if hi2 is not None and hi2 > lo:
            hi = hi2
    return lo - 60 <= t <= hi + 60


def _example_anchor(ex: dict, title_ts, sec) -> str | None:
    """例题的 🎙 时刻：优先 `source_check.anchor_ts`（F11 §1 的新字段），
    其次是标题里的 `🎙 HH:MM`；**校验落在本节范围内**，越界 → `None`（退回检索）。"""
    sc = ex.get("source_check") if isinstance(ex.get("source_check"), dict) else {}
    ts = str(sc.get("anchor_ts") or title_ts or "").strip() or None
    if ts and not _anchor_ok(ts, sec):
        return None
    return ts


def _support_window(entries: list, want) -> tuple:
    """claim 时间戳 →（最近一行的 ±8/+32 窗口）。拿不到 ts / 空转录 → 全篇。"""
    if not entries:
        return 0, 0
    if want is None:
        return 0, len(entries)
    stamped = [(i, _hms(e.get("ts"))) for i, e in enumerate(entries)
               if isinstance(e, dict)]
    stamped = [(i, t) for i, t in stamped if t is not None]
    if not stamped:
        return 0, len(entries)
    i = min(stamped, key=lambda x: abs(x[1] - want))[0]
    return max(0, i - 8), min(len(entries), i + 32)


def _support_state(entries: list, lo: int, hi: int) -> str:
    """转录切片 → state（E3 的 `L<n> [hh:mm:ss] sentence` 原格式，编号是全篇行号）。"""
    lines = [f"L{lo + j + 1} [{e.get('ts')}] {e.get('en') or e.get('asr')}"
             for j, e in enumerate(entries[lo:hi])
             if isinstance(e, dict) and (e.get("en") or e.get("asr"))]
    return SUPPORT_PREAMBLE + "\n\n" + "\n".join(lines)


def _materials_excerpts(materials, claim: str, *, k: int = 2,
                        context: str = "") -> list:
    """claim → 材料 top-k 片段（BM25，`Materials.search`）。拿不到 → `[]`。

    ⭐ F9d §0（作者裁定：支持核对必须与**生成时读的是同一份材料来源与同一检索口径**）：
    claim 本身检索不出词时（纯符号 `$MC=12$` / 纯中文短句 —— 材料是英文的），
    退一步用**本节转录窗口**再查一次（生成时正是拿转录片段查材料的）。
    ⭐ F11 ④：符号 claim（`MC=12`/`AC(28)`/`AC(5)`）先走 `_symbol_excerpts` 的**精确 token
    扫描**（材料里逐字有这个片段就取它）—— BM25 会因 <3 字符词/纯数字把查询词丢光。
    来源集没有第二个定义：`materials` 对象由调用方从生成链原样传进来。
    """
    sym = _symbol_excerpts(materials, claim, k=k)
    if sym:
        return sym
    for q in (claim, context):
        if not q:
            continue
        try:
            hits = materials.search(q, k=k)
        except Exception:                                      # noqa: BLE001
            hits = []
        if hits:
            return [str(getattr(c, "text", "") or "") for c in hits]
    return []


def _section_context(entries: list, secs: list, c: dict, *, cap: int = 2000) -> str:
    """claim 所在小节转录窗口的文本（claim 检索不出词时拿它当查询）。**纯函数。**"""
    try:
        si = c.get("si")
        sec = secs[si] if isinstance(si, int) and 0 <= si < len(secs) else {}
        if not isinstance(sec, dict):
            sec = {}
        want = _hms(c["ts"]) if c.get("ts") else _hms(sec.get("ts"))
        lo, hi = _support_window(entries, want)
        txt = " ".join(str(e.get("en") or e.get("asr") or "")
                       for e in entries[lo:hi] if isinstance(e, dict))
        return txt[:cap]
    except Exception:                                          # noqa: BLE001
        return ""


def _support_mat_state(excerpts: list) -> str:
    """材料片段 → state（`M<i>. sentence`，逐行编号）。"""
    lines: list = []
    for x in excerpts:
        for ln in str(x or "").splitlines():
            t = ln.strip()
            if t:
                lines.append(t[:240])
    return SUPPORT_MAT_PREAMBLE + "\n\n" + "\n".join(f"M{i + 1}. {t}"
                                                     for i, t in enumerate(lines))


def _support_clip(t: str, *, n: int = 140) -> str:
    """长句在标点处截（**逐字子串** —— annotate 的定位靠它，不许加省略号）。"""
    if len(t) <= n:
        return t
    cut = -1
    for ch in "，；。、,;":
        cut = max(cut, t.rfind(ch, 0, n))
    if cut >= 60:
        return t[:cut + 1]
    return t[:n]


def support_claims(review: dict) -> list:
    """抽「讲义说过 X」类 claim —— 支持性核对（Jev）的输入。**纯函数。**

    收（作者 F9b 第 1 条）：
      · 例题块（标题带 🎙，含 🎙+✏️）题面的**具体给定** —— 公式/等式、数字、命名实体
        （`_prompt_givens`；⭐ F9d 第 2 条：**祈使话（配平/合并/求/计算）永远不收** ——
        讲师不会逐字念题面，收进来只会制造假警，CHE 实测 4/4）；
      · 正文里含「课上/老师/本课」的句子（E3 的字段范围：prose/algebra）。
    不收：
      · ✏️ 自补块（无 🎙 的）与自补句 —— 自补充本就不要求转录支持，查了是假警报；
      · 无标签的例题 —— 保守：只查断言"讲义说过"的东西；
      · 免责句（`⚠ …未核对`）与教材对照句（`📚`）—— 那是**注释自己的话**，
        不是"讲义说过 X"（挂上去会出现"未核对句被标：转录里没找到这句"的笑话）；
      · pitfalls/conclusion 等注记字段（E3 只扫 prose/algebra/example，跟它）。
    claim 一律是逐字原文（annotate 靠它在正文里定位）。
    ⭐ F9c：每条带 `src` —— `📖` 的题面**对材料判**（不是转录），`🎙` 的对转录判。
    """
    out: list = []
    seen: set = set()

    def add(text, si: int, field: str, ts: str | None = None, src: str = "🎙",
            min_len: int = 6) -> None:
        t = _support_clip(_one(text))
        if len(t) < min_len or t in seen:
            return
        seen.add(t)
        out.append({"claim": t, "si": si, "field": field, "ts": ts, "src": src})

    for si, s in enumerate(review.get("sections") or []):
        if not isinstance(s, dict):
            continue
        ex = s.get("example")
        title = str((ex or {}).get("title") or "") if isinstance(ex, dict) else ""
        if isinstance(ex, dict) and "🎙" in title:
            m = _TS_IN_TITLE.search(title)
            ts0 = _example_anchor(ex, m.group(1) if m else None, s)
            for g in _prompt_givens(ex.get("prompt")):     # F9d：只收具体给定
                add(g, si, "example", ts0,
                    "📖" if "📖" in title else "🎙", min_len=3)
        for k in ("prose", "algebra"):
            v = s.get(k)
            for para in (v if isinstance(v, list) else [v]):
                for sent in _sentences(str(para or "")):
                    if (_SUPPORT_WORDS.search(sent) and not _SUPPORT_SELF.search(sent)
                            and not _SUPPORT_SKIP.search(sent)
                            and not sent.lstrip().startswith("⚠")):
                        add(sent, si, k)
    return out


def _recon_ok(section: dict) -> bool:
    """F9/F11 的「按口述还原」验证：该块的**机械核**（含 F11 §2 的 program 行）
    ≥1 条 ok 且 0 条 mismatch。

    ⭐ F11 §2：题面有 `program`（代码算）也算 —— 程序把片段里每个数字都复现出来了，
    是比"函数能复算"更硬的证明；没有 program 时仍要求题面里有函数（老口径）。
    """
    try:
        import note_math
        ex = section.get("example")
        if not isinstance(ex, dict):
            return False
        has_prog = isinstance(ex.get("program"), list) and bool(ex.get("program"))
        if not has_prog and not note_math._FUNC_DEF.search(str(ex.get("prompt") or "")):
            return False
        rows = note_math.check_review({"sections": [section]})   # 已含 program_rows
        mine = [r for r in rows
                if note_math.unit_of_claim(section, str(r.get("claim") or ""))
                in note_math.MODEL_AUTHORED]
        return bool(mine) and (not any(r.get("status") == "mismatch" for r in mine)
                               and any(r.get("status") == "ok" for r in mine))
    except Exception:                                          # noqa: BLE001
        return False


# ── F11 §3：🎙 没找到但内容没毛病 → 静默降级 ✏️ ────────────────────────────────
#: 课上/老师的**归属引语** —— 降级时去掉，句子读起来就是笔记自己的话（不再是"讲义说过"）。
#: ⚠️ 锚在**句首**（`^`）：只在句子开头的归属引语上动手，别把「这门课上的图」这类
#:    句中的「课上」误删。
_SUPPORT_LEAD = re.compile(
    r"^\s*(?:(?:老师|教授|讲师)\s*(?:在)?\s*(?:课上|课堂|本课)?\s*(?:的)?\s*"
    r"|(?:课上|课堂|本课|这节(?:课)?)\s*(?:的|里|中|上)?\s*)"
    r"(?:提到|强调|指出|说过|讲到|说|讲|称|认为)?[，,：:、]?\s*")


def _strip_leadin(s: str) -> str:
    """去掉句首的「老师在课上提到/强调…」这类**归属引语**（§3 降级）。**纯函数。**"""
    t = _SUPPORT_LEAD.sub("", str(s or ""), count=1)
    return t.lstrip("，,：:、。！？ ").strip()


def _content_ok(sec, claim: str) -> bool:
    """这条 claim 的**内容**是否没毛病（§3）：非数值，或本节 program 跑通。**纯函数。**

    数值题（含数字）而**没有**跑通的 program → 判不了"内容对不对" → 留给 ⚠（数值错存世）。
    """
    if not re.search(r"\d", str(claim or "")):
        return True
    try:
        import note_math
        if isinstance(sec, dict) and isinstance(sec.get("example"), dict):
            rows = note_math.program_rows(sec)
            if rows and not any(r.get("status") == "mismatch" for r in rows):
                return True
    except Exception:                                          # noqa: BLE001
        pass
    return False


def _to_self_title(t: str) -> str:
    """例题标题 🎙 → ✏️（§3 降级）。**纯函数。**"""
    s = str(t or "")
    s = s.replace("🎙+✏️", "✏️").replace("🎙", "✏️")
    s = _strip_leadin(s)
    return _one(s)


def _downgrade_claim(out: dict, c: dict) -> bool:
    """把一条 claim 的归属从 🎙 降成 ✏️（原地改 `out`）。成功返回 True。

    · 例题给定（field=example）→ 该节例题标题 🎙→✏️；
    · 正文句（prose/algebra）→ 找到那句话，去掉「老师在课上提到…」归属引语
      （容器类型不变：`prose` 是列表、`algebra` 是字符串）。
    """
    try:
        si = c.get("si")
        secs = out.get("sections") or []
        sec = secs[si] if isinstance(si, int) and 0 <= si < len(secs) else None
        if not isinstance(sec, dict):
            return False
        if c.get("field") == "example":
            ex = sec.get("example")
            if not isinstance(ex, dict):
                return False
            ex["title"] = _to_self_title(ex.get("title"))
            return True
        key = str(c.get("field") or "")
        if key not in ("prose", "algebra"):
            return False
        v = sec.get(key)
        items = list(v) if isinstance(v, list) else ([v] if v else [])
        want = _norm_ctx(c.get("claim"))
        for i, para in enumerate(items):
            for sent in _sentences(str(para or "")):
                if _norm_ctx(sent)[:60] == want[:60] or (want and want[:40] in _norm_ctx(sent)):
                    new = _strip_leadin(sent)
                    if new and new != sent:
                        if isinstance(v, list):
                            v[i] = str(para).replace(sent, new, 1)
                            sec[key] = v
                        else:
                            sec[key] = str(para).replace(sent, new, 1)
                        return True
        return False
    except Exception:                                          # noqa: BLE001
        return False


def support_check(review: dict, *, entries: list, materials=None, ask=None,
                  max_requests: int = SUPPORT_MAX_REQ,
                  tau: float = SUPPORT_TAU) -> tuple:
    """F9c：把「讲义说过 X」的 claim 过一遍 Jev（E3 口径）→ p < tau 的挂 ⚠。

    · **按节分批判**（作者 F9c：局部性优先）：每节自己一批；⭐ F9d：每条 claim 的窗口
      由**对转录的检索**决定（数字/拉丁词/中文 2-gram 打分，EN+ASR+ZH 一起看，命中处
      ±8/+32 行，最多 2 个 —— 正文 claim 没有自己的时间戳，小节 ts 会离原话很远）；
      例题标题里 🎙 的时刻是**强先验**（那个窗口永远保留）；检索没有任何命中 → 退回
      小节 ts（老口径）；一批 ≤20 问（`S1…Sn`）；
    · 📖 题面**对材料判**（`Materials.search` 的 top-k 片段做 state，指令平行版）；
      **没有材料就豁免** —— 绝不许因为"讲师没念过卷子上的题"挂 📖；
    · **只标不改**，两个落点：
        – 台账：`checks` 追加 `status=mismatch`、`how=转录里没找到这句（核对）`
          （📖 的是 `材料里没找到这句（核对）`）→ 链上最后的 `annotate_mismatches` 落 ⚠；
        – 🎙 例题给定被标、且 F9 重建验证通过 → 标题改挂 🎙+✏️「按口述还原」
          （`note_v2.with_recon_label`；没通过验证的照常挂 ⚠；📖 的不挂）；
    · ⭐ F11 §3（作者 2026-10-18）：p<τ 但**内容没毛病**（非数值，或本节 program 跑通）→
      **静默降级** 🎙→✏️（去「老师在课上提到/强调」这类归属引语），正文**不挂 ⚠**，
      降级条数走第二返回值 `{"downgraded": N}`（写作层的台账行）；⚠ 只留给存世的数值/理论错；
    · 一次核对（= 一节课）≤ `max_requests` 次请求（超出的**批**不查，条数进提醒）；
      `ask(state, instrs)` 可注入（假判官在位测里用）；
    · 没配 provider / 任何异常 → 全 None 空转，**永不抛**（既不花钱也不挡生成）。
    """
    out = copy.deepcopy(review)
    claims = support_claims(out)
    if not claims or not entries:
        return out, [], []
    if ask is None:
        def ask(state, instructions):                          # noqa: E306
            try:
                import keypoints
                return keypoints.ask_batch(state, instructions)
            except Exception:                                  # noqa: BLE001
                return [None] * len(instructions)
    secs = out.get("sections") or []
    tries = max(1, int(max_requests))
    mat_ok = bool(getattr(materials, "_chunks", None)) if materials is not None else False

    # 工作清单：按节序（每节先转录批、再材料批）；超上限的批整批不查。
    by_sec: dict = {}
    for c in claims:
        by_sec.setdefault(c["si"], []).append(c)
    work: list = []
    exempt = 0
    for si in sorted(by_sec):
        part = by_sec[si]
        t_part = [c for c in part if c.get("src") != "📖"]
        m_part = [c for c in part if c.get("src") == "📖"]
        for kind, items in (("tr", t_part), ("mat", m_part)):
            if kind == "mat" and not mat_ok:
                exempt += len(items)
                continue
            for i in range(0, len(items), SUPPORT_BATCH):
                work.append((kind, si, items[i:i + SUPPORT_BATCH]))
    skipped = sum(len(w[2]) for w in work[tries:])
    rows_new: list = []
    relabeled = 0
    n_flag = 0
    downgraded = 0
    used = 0
    n_scored = 0                    # 拿到真实概率的条数（判官真的回了值的）

    def _want(c: dict):                                        # noqa: E306
        if c.get("ts"):
            return _hms(c["ts"])           # 例题：标题里 🎙 的时刻（不是小节 ts）
        si = c["si"]
        sec = secs[si] if 0 <= si < len(secs) else {}
        return _hms(sec.get("ts")) if isinstance(sec, dict) else None

    for kind, si, part in work[:tries]:
        used += 1
        if kind == "mat":
            excerpts: list = []
            for c in part:
                excerpts.extend(_materials_excerpts(
                    materials, c["claim"],
                    context=_section_context(entries, secs, c)))
            state = _support_mat_state(excerpts)
        else:
            wins: list = []
            for c in part:
                # F9d：例题标题的 🎙 时刻是强先验；正文 claim 纯检索、拿不到命中才回小节 ts。
                wins += _support_windows(entries, c["claim"],
                                         prior=_hms(c["ts"]) if c.get("ts") else None,
                                         fallback=None if c.get("ts") else _want(c))
            state = _support_state_multi(entries, wins)
        instr_tpl = SUPPORT_MAT_INSTR if kind == "mat" else SUPPORT_INSTR
        instrs = [instr_tpl.format(claim=c["claim"]) for c in part]
        try:
            ps = list(ask(state, instrs))
        except Exception:                                      # noqa: BLE001
            ps = []
        how = "材料里没找到这句（核对）" if kind == "mat" else "转录里没找到这句（核对）"
        for k, c in enumerate(part):
            p = ps[k] if k < len(ps) else None
            if not isinstance(p, (int, float)):
                continue
            n_scored += 1
            if float(p) >= tau:
                continue
            sec = secs[c["si"]] if 0 <= c["si"] < len(secs) else None
            if (kind == "tr" and c["field"] == "example"
                    and isinstance(sec, dict) and _recon_ok(sec)):
                t = _one((sec.get("example") or {}).get("title"))
                if "按口述还原" not in t:
                    t = (t + "（按口述还原）") if t else "按口述还原"
                try:
                    from note_v2 import with_recon_label
                    t = with_recon_label(t)
                except Exception:                              # noqa: BLE001
                    pass
                sec.setdefault("example", {})["title"] = t
                relabeled += 1
                continue
            # ⭐ F11 §3（作者 2026-10-18）：p<τ 但**内容正确**（非数值，或本节 program 通过）
            #    → **静默降级** 🎙→✏️（去掉「老师在课上提到/强调」这类归属引语），
            #    正文里**不挂 ⚠**；⚠ 只留给存世的数值/理论错。逐条进台账 `downgraded N`。
            if _content_ok(sec, c["claim"]):
                if _downgrade_claim(out, c):
                    downgraded += 1
                continue
            rows_new.append({"claim": c["claim"], "status": "mismatch", "kind": "support",
                             "recomputed": f"support p={float(p):.2f}", "how": how})
            n_flag += 1
    if rows_new:
        try:
            import note_math
            out["checks"] = note_math.merge_checks(out.get("checks") or [], rows_new)
        except Exception:                                      # noqa: BLE001
            out["checks"] = list(out.get("checks") or []) + rows_new
    bits = [f"{n_flag} 条挂 ⚠（转录/材料里没找到）"] if n_flag else []
    if relabeled:
        bits.append(f"{relabeled} 条例题给定没找到 → 按口述还原挂牌")
    if downgraded:
        bits.append(f"{downgraded} 条 🎙 没找到但内容没毛病 → 静默降 ✏️")
    if exempt:
        bits.append(f"{exempt} 条 📖 题面豁免（没有材料）")
    if skipped:
        bits.append(f"{skipped} 条超成本上限没查")
    # ⚠️⚠️ DB 审查 D-19：一条概率都没拿到（Jev key 过期 / 401 / 全局限流）时**不许**
    #    说"全部找到" —— 那是"闸门坏了"与"闸门正常工作"长得一模一样的那个静默面，
    #    而这层核对的全部价值就是拦住没出处的 claim。`ask_batch` 也会往终端打一行。
    if used and not n_scored:
        bits.append(f"⚠ 一条都没判成（{used} 批判官都没回执）—— 这一层这次没起作用")
    msgs = [f"支持性核对：查了 {used} 批（上限 {tries} 次请求）"
            + ("；" + "；".join(bits) if bits else "；全部找到")]
    return out, [{"downgraded": downgraded}], msgs
