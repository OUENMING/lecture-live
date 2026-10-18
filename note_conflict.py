r"""跨节矛盾（F14 ⑧⑨）：**同一件事在两节里被写成两个值**。

作者逐字读完 v2 的 ECS170 / BUSI3243 抓到的两处（我的自动审计一条都没报）：

⑧ **定义在节与节之间对不上**（ECS170）
   §14 写 `$f(z)=0\ (z\le0)$`（并把「以为 z=0 输出 1」列成常见错误），
   §15 写「大于等于 0 输出 1」，§44 的例题也用「$z\ge0$ 输出 1」—— 同一个阶跃函数，
   `z=0` 一处给 0、一处给 1。**跨节**，所以 F13 的"同一节内代数↔讲解"判据看不到。

⑨ **分组数值在两节里对调**（BUSI3243）
   §15 的自补例：女性 0.5 / 男性 0.7；§18：男性 0.3 / 女性 0.7。而且**转录自己就矛盾**
   （09:33 `if you're male … 0.3 plus 0.4` vs 09:34 `for female workers … beta three
   plus beta four`）—— 笔记该说"课上前后说法不一致"，而不是挑一个当"课上给出"。

## 接口（深模块：小接口，判据全在里面）

    conflicts(review, *, entries=None) -> list[dict]      # 一站式（守卫用）
    point_value_conflicts(review) -> list[dict]           # ⑧
    group_number_conflicts(review) -> list[dict]          # ⑨a（笔记内部）
    source_sum_group_conflicts(entries) -> list[dict]     # ⑨b（转录自相矛盾）

纯函数、不读盘、不联网；`entries` 由调用方给（转录行）。
"""
from __future__ import annotations

import re

# ---- ⑧ 「条件 → 取值」语句 ----------------------------------------------------
#: 点条件：`z=0`（`$z=0$` 里的 `$` 不影响）。
_POINT = re.compile(r"(?P<var>[A-Za-z]{1,2})\s*[=＝]\s*(?P<n>-?\d+(?:\.\d+)?)")
#: 区间条件：`z>0` / `大于等于 0` / `z\le 0` / `小于 0`。
_RAY = re.compile(r"(?:(?P<var>[A-Za-z]{1,2})\s*)?"
                  r"(?P<op>大于等于|不小于|≥|>=|大于|>|小于等于|不大于|≤|<=|小于|<)\s*"
                  r"(?P<n>-?\d+(?:\.\d+)?)")
#: 取值：`输出 1` / `取值 0` / `等于 1` / `是 0`（紧跟条件 ≤12 字内）。
_VALUE = re.compile(r"[^。；;，,]{0,12}?(?:输出|取值|等于|是|为)\s*"
                    r"(?P<v>-?\d+(?:\.\d+)?)")
#: 取值只认小数字：定义/分段函数那一档（`输出 1`/`取值 0`）；`z=100` 这种是自变量取值。
_VALUE_MAX = 10.0
#: 「误以为…」这类**把错值写进正文当反例**的句子不算断言（否则会把反例当矛盾源）。
_ERROR_MARK = re.compile(r"误以为|错误|别把|不要把|不对|说成|混淆|反了")
#: 单字母变量才判（`z`/`x`/`t`）；多字母标识符留给别的判据。
_CLOSED_HI = ("大于等于", "不小于", "≥", ">=")
_CLOSED_LO = ("小于等于", "不大于", "≤", "<=")


def _interval(op: str, n: float):
    """区间条件 → `(lo, hi, lo_closed, hi_closed)`（无界的一边是 None）。**纯函数。**"""
    if op in _CLOSED_HI:
        return (n, None, True, False)
    if op in ("大于", ">"):
        return (n, None, False, False)
    if op in _CLOSED_LO:
        return (None, n, False, True)
    return (None, n, False, False)                      # 「小于」/「<」


def _overlap(a, b) -> bool:
    """两个区间有没有公共点（开闭端点都算准）。**纯函数。**"""
    alo, ahi, alc, ahc = a
    blo, bhi, blc, bhc = b
    if alo is None:
        lo, loc = blo, blc
    elif blo is None:
        lo, loc = alo, alc
    elif alo > blo:
        lo, loc = alo, alc
    elif blo > alo:
        lo, loc = blo, blc
    else:
        lo, loc = alo, (alc and blc)
    if ahi is None:
        hi, hic = bhi, bhc
    elif bhi is None:
        hi, hic = ahi, ahc
    elif ahi < bhi:
        hi, hic = ahi, ahc
    elif bhi < ahi:
        hi, hic = bhi, bhc
    else:
        hi, hic = ahi, (ahc and bhc)
    if lo is None or hi is None:
        return True                                     # 至少一边无界 → 必有交
    if lo < hi:
        return True
    if lo > hi:
        return False
    return bool(loc and hic)                            # 端点重合：两边都得闭


def _statements(text: str, *, subject: set) -> list:
    """一段文本 → `[{"kind","var","point"/"inter","value","sent",**}]`。**纯函数。**"""
    out: list = []
    for sent in re.split(r"[。；;\n]", str(text or "")):
        sent = (sent.replace("\\leq", "≤").replace("\\le", "≤")
                    .replace("\\geq", "≥").replace("\\ge", "≥")
                    .replace("\\lt", "<").replace("\\gt", ">"))
        if _ERROR_MARK.search(sent):
            continue
        last_var = ""
        for m in _RAY.finditer(sent):
            var = (m.group("var") or last_var or "").lower()
            if not var:
                # 区间可以省略变量（「大于等于 0 输出 1」）—— 但本句前面必须出现过变量
                continue
            last_var = var
            vm = _VALUE.search(sent[m.end():m.end() + 16])
            if not vm:
                continue
            val = float(vm.group("v"))
            if abs(val) > _VALUE_MAX:
                continue
            out.append({"kind": "inter", "var": var, "op": m.group("op"),
                        "n": float(m.group("n")), "value": val,
                        "sent": sent.strip(), "subject": set(subject)})
        for m in _POINT.finditer(sent):
            var = m.group("var").lower()
            last_var = var
            vm = _VALUE.search(sent[m.end():m.end() + 16])
            if not vm:
                continue
            val = float(vm.group("v"))
            if abs(val) > _VALUE_MAX:
                continue
            out.append({"kind": "point", "var": var, "n": float(m.group("n")),
                        "value": val, "sent": sent.strip(), "subject": set(subject)})
    return out


def _section_statements(s: dict) -> list:
    """一节里所有「条件→取值」语句（含 `terms` 作为"主体"证据）。**纯函数。**"""
    subject = {str(t.get("en") or "").strip().lower()
               for t in (s.get("terms") or []) if isinstance(t, dict)}
    subject |= {str(t.get("zh") or "").strip() for t in (s.get("terms") or [])
                if isinstance(t, dict)}
    subject.discard("")
    texts = [str(s.get("algebra") or "")] + [str(x) for x in (s.get("prose") or [])]
    ex = s.get("example") or {}
    texts += [str(ex.get("prompt") or ""), str(ex.get("answer") or "")]
    texts += [str(v.get("q") or "") for v in (s.get("variants") or []) if isinstance(v, dict)]
    return [r for t in texts for r in _statements(t, subject=subject)]


def _same_subject(a: dict, b: dict) -> bool:
    """两条语句说的是不是同一个东西（用本节术语交集判）。**纯函数。**"""
    return bool(a.get("subject") and b.get("subject") and (a["subject"] & b["subject"]))


def point_value_conflicts(review: dict) -> list:
    """同一条「条件 → 取值」在**不同节**给出不同值 → 一条矛盾。**纯函数。**"""
    secs = [s for s in (review.get("sections") or []) if isinstance(s, dict)]
    stmts = [(_section_statements(s), str(s.get("heading") or "")) for s in secs]
    out: list = []
    for i in range(len(stmts)):
        for j in range(i + 1, len(stmts)):
            for a in stmts[i][0]:
                for b in stmts[j][0]:
                    if a["var"] != b["var"] or a["value"] == b["value"]:
                        continue
                    if not _same_subject(a, b):
                        continue
                    if a["kind"] == "point" and b["kind"] == "point":
                        if a["n"] != b["n"]:
                            continue
                    elif a["kind"] == "point" or b["kind"] == "point":
                        p, r = (a, b) if a["kind"] == "point" else (b, a)
                        lo, hi, lo_c, hi_c = _interval(r["op"], r["n"])
                        if lo is not None and (p["n"] < lo or (p["n"] == lo and not lo_c)):
                            continue
                        if hi is not None and (p["n"] > hi or (p["n"] == hi and not hi_c)):
                            continue
                    else:
                        if not _overlap(_interval(a["op"], a["n"]),
                                        _interval(b["op"], b["n"])):
                            continue
                    out.append({"kind": "point-value", "var": a["var"],
                                "sections": [i + 1, j + 1],
                                "texts": [a["sent"], b["sent"]],
                                "values": [a["value"], b["value"]]})
    return out


# ---- ⑨a 分组数值（笔记内部） --------------------------------------------------
#: 中文分组标签 + （≤14 字）+ 量词/连接词 + 数字。
_GROUP_NUM = re.compile(r"(?P<label>[\u4e00-\u9fff]{2,4})(?:的)?"
                        r"[^。；;，,]{0,14}?"
                        r"(?:斜率|系数|回报|工资|收入|每年|经验|收益|效应|弹性|涨幅|"
                        r"增长|多|涨|增加|提高|达到|是|为)"
                        r"[^。；;，,]{0,6}?(?P<v>-?\d+(?:\.\d+)?)")
#: 标签里的虚字（`这样`/`其中` 这类不是分组名）。
_NOT_LABEL = re.compile(r"[的在不和与是了也就把被这那其中上下前后]")


def group_numbers(text: str) -> dict:
    """一段文本 → `{中文分组标签: {数值, …}}`（只在"分组→数量"语境里抓）。**纯函数。**

    ⚠️ 标签必须**从小句开头起**（`，女性每多一年经验收入多 0.5`）—— 否则 `写成式子`、
       `最容易错` 这种动词短语也会被当成"分组名"（v2 实测的假报）。
    """
    out: dict = {}
    for m in _GROUP_NUM.finditer(str(text or "")):
        label = m.group("label")
        if _NOT_LABEL.search(label):
            continue
        j = m.start("label")
        prev = str(text or "")[:j].rstrip()
        if prev and prev[-1] not in "。；;，,：:、\n（(【":
            continue
        # ⚠️ 取值必须是**独立数字**：`$R^2$` 的 2（前有 `^`）、`0/1`（后有 `/`）、
        #    `81%`（后有 `%`）都不是"分组拿到的数值"（v2 实测这三档都造过假报）。
        t = str(text or "")
        a, b = m.start("v"), m.end("v")
        if a > 0 and (t[a - 1].isalnum() or t[a - 1] in "^_\\."):
            continue
        if b < len(t) and (t[b].isalnum() or t[b] in "%/^_"):
            continue
        try:
            v = float(m.group("v"))
        except ValueError:
            continue
        if abs(v) > 10:                       # 概率/百分比/编号不在这条判据里
            continue
        out.setdefault(label, set()).add(v)
    return out


def group_number_conflicts(review: dict, *, entries: list | None = None) -> list:
    """同一个分组标签在**不同节**拿到不同数值 → 一条矛盾（⑨a）。**纯函数。**

    ⚠️ 给了 `entries` 时再加一道闸：标签必须是**课上真出现过的词**（分组名是念出来的）。
       没有这道闸，`值域 0 到 1` 与 `值域 -1 到 1`（两个不同函数）会被读成"同一个组
       拿了两个值"（v1 实测的假报）。
    """
    secs = [s for s in (review.get("sections") or []) if isinstance(s, dict)]
    spoken = ""
    if entries:
        spoken = "\n".join(str(e.get(k) or "") for e in entries
                           for k in ("en", "asr", "zh"))
    per: list = []
    for s in secs:
        texts = [str(x) for x in (s.get("prose") or [])] + [str(s.get("algebra") or "")]
        ex = s.get("example") or {}
        texts += [str(ex.get("prompt") or ""), str(ex.get("answer") or "")]
        got: dict = {}
        for t in texts:
            for k, vs in group_numbers(t).items():
                got.setdefault(k, set()).update(vs)
        per.append({k: v for k, v in got.items() if not spoken or k in spoken})
    out: list = []
    for i in range(len(per)):
        for j in range(i + 1, len(per)):
            for label in sorted(per[i]):
                if label not in per[j]:
                    continue
                if per[i][label] == per[j][label]:
                    continue
                if per[i][label] & per[j][label]:
                    continue                   # 有交集 = 至少一个说法对得上 → 不指控
                out.append({"kind": "group-value", "label": label,
                            "sections": [i + 1, j + 1],
                            "values": [sorted(per[i][label]), sorted(per[j][label])]})
    return out


# ---- ⑨b 转录自相矛盾（同一个"两项之和"安在两个组上） -------------------------
_GROUP_EN = re.compile(r"if you(?:'re| are)\s+(?P<g>[A-Za-z]{3,12})", re.I)
#: 英文第二档：`For female workers …`（组名 + 通用名词 worker）。
_GROUP_EN2 = re.compile(r"for\s+(?:the\s+)?(?P<g>[A-Za-z]{3,12})\s+workers?", re.I)
#: 中文档：`如果你是男性 …`（`如果（你）是 X` 这个句式本身就说明了 X 是分组）。
_GROUP_ZH = re.compile(r"如果(?:你)?是(?P<g>[\u4e00-\u9fff]{1,2})")
#: "两项之和"的两种写法：`0.3 plus 0.4` / `beta three plus beta four`。
_SUM2 = re.compile(r"(?:\d+(?:\.\d+)?\s*plus\s*\d+(?:\.\d+)?"
                   r"|beta\s+\w+\s+plus\s+beta\s+\w+)", re.I)
#: 数值在看同一段话时允许的跨度（条数）。
_SRC_WINDOW = 8


def source_sum_group_conflicts(entries: list) -> list:
    r"""转录里**"两项之和"被安在两个不同组上** → 一条"课上前后说法不一致"。**纯函数。**

    ⚠️ 判据是**形状**不是写法：`0.3 plus 0.4`（数字版）与 `beta three plus beta four`
       （符号版）说的是同一个"和"，只是念法不同 —— 两个组各拿一个"两项之和"，
       就说明"哪个组拿这个和"课上没定下来。两组各给了**不同的数字之和**时不报
       （那更像两个不同的例子，不是同一件事）。
    """
    ents = list(entries or [])
    rows: list = []
    for i, e in enumerate(ents):
        zh = str(e.get("zh") or "")
        en = str(e.get("en") or e.get("asr") or "")
        gm = _GROUP_ZH.search(zh) or _GROUP_EN.search(en) or _GROUP_EN2.search(en)
        if not gm:
            continue
        g = gm.group("g").strip().lower()
        for k in range(i, min(len(ents), i + _SRC_WINDOW + 1)):      # 只往后找（就近）
            txt = str(ents[k].get("en") or ents[k].get("asr") or "")
            if k > i and (_GROUP_ZH.search(str(ents[k].get("zh") or ""))
                          or _GROUP_EN.search(txt) or _GROUP_EN2.search(txt)):
                continue            # 那一行在讲**别的组** → 不许跨组配和
            sm = _SUM2.search(txt)
            if not sm:
                continue
            rows.append({"group": g, "sum": sm.group(0).strip().lower(),
                         "ts": ents[k].get("ts", ""), "quote": txt.strip()[:120]})
            break
    sums: dict = {}
    for r in rows:
        sums.setdefault(r["group"], r["sum"])                        # 每组只留最先配上的
    if len(sums) < 2:
        return []
    numeric = []
    for v in sums.values():
        m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*plus\s*(\d+(?:\.\d+)?)", v, re.I)
        if m:
            numeric.append(float(m.group(1)) + float(m.group(2)))
    if len(set(numeric)) >= 2:
        return []                       # 两组各自给了不同的**数字**之和 → 不算同一件事
    quotes = []
    for g in sorted(sums):
        r = next(x for x in rows if x["group"] == g)
        quotes.append({"group": g, "ts": r["ts"], "quote": r["quote"]})
    return [{"kind": "source-self-conflict", "groups": sorted(sums), "quotes": quotes}]


# ---- ⑩ 虚拟变量：组别 ↔ 0/1 ↔ 斜率，三方对不上 ------------------------------
#: 组别定义两种语序：`1 表示女性` / `0 代表男性`；`女性取 1` / `男性赋值为 0`。
_DEF_V2L = re.compile(r"(?P<v>[01])\s*(?:表示|代表|对应|指|是|＝|=)\s*(?P<label>[\u4e00-\u9fff]{2,4})")
_DEF_L2V = re.compile(r"(?P<label>[\u4e00-\u9fff]{2,4})\s*(?:取|设|赋)\s*(?:值\s*)?(?:为|成)?\s*(?P<v>[01])")
#: 系数式（一个系数，可跟上若干加减项）——TeX 写法 `\beta_3` 与 Unicode 写法 `β₃` 都认。
_COEF_PAT = (r"\\?[βbB](?:eta)?[_^]?\{?[A-Za-z0-9₀-₉]*\}?"
             r"(?:\s*[+＋\-−]\s*\\?[βbB](?:eta)?[_^]?\{?[A-Za-z0-9₀-₉]*\}?)*")
#: 虚拟变量取值 → 斜率：`D=0 斜率 β₃` / `D=1 的斜率是 β₃+β₄`。
_DEF_SLOPE = re.compile(r"(?P<d>[A-Za-z])\s*[=＝]\s*(?P<v>[01])\s*[^。；;，,、：:]{0,10}?"
                        r"(?:斜率|系数)\s*[^。；;，,、：:]{0,4}?(?:是|为|＝|=)?\s*"
                        r"(?P<expr>" + _COEF_PAT + r")")
#: 组别 → 斜率：**组名必须已经认出来**（`女性每多一年经验增加 β₃`）。
#: ⚠️ 不许拿 `[\u4e00-\u9fff]{2,4}` 当组名：它会把「于是女性」「男性每多」一起吞进去
#:    （实测），组名只能来自 `_DEF_V2L`/`_DEF_L2V` 那张表。
_SLOPE_GAP = r"[^。；;，,、：:]{0,24}?(?:增加|多|加|是|为|等于)\s*[^。；;，,、：:]{0,3}?"


def _slope_said(text: str, label: str) -> frozenset:
    """本节里「`label` … 增加 <系数式>」抄到的斜率（取第一处）。**纯函数。**"""
    rx = re.compile(re.escape(label) + _SLOPE_GAP + r"(?P<expr>" + _COEF_PAT + r")")
    m = rx.search(str(text or ""))
    return _coef_terms(m.group("expr")) if m else frozenset()


#: 系数式里的项（`β₃+β₄` → `{b3, b4}`）—— 比较用**项的集合**，与加法顺序无关。
_EXPR_SPLIT = re.compile(r"\s*[+＋\-−]\s*")
#: 系数原子：`\beta_3` / `β₃` / `β3` / `b3` 都归一成 `b3`（TeX 写法与 Unicode 写法同一件事）。
_COEF_BODY = r"\\?[βbB](?:eta)?[_^]?\{?[A-Za-z0-9₀-₉]*\}?"


def _norm_coef(s: str) -> str:
    """系数符号归一：TeX 的 `\\beta_3`、Unicode 的 `β₃`、朴素的 `b3` → 同一个 `b3`。**纯函数。**"""
    import unicodedata
    s = unicodedata.normalize("NFKC", str(s or "")).lower()
    s = s.replace("\\beta", "b").replace("beta", "b").replace("β", "b")
    return re.sub(r"[\s{}\\$^_'`]", "", s)


def _coef_terms(expr: str) -> frozenset:
    """系数式 → 项的集合（`β₃+β₄` ≡ `β₄＋β₃` ≡ `\\beta_3+\\beta_4`）。**纯函数。**"""
    out = set()
    for p in _EXPR_SPLIT.split(_norm_coef(expr)):
        if p:
            out.add(p)
    return frozenset(out)


def _section_text(s: dict) -> str:
    """一节用于**对读**的文本（式 + 讲解 + 例题 + 常见错误，顺序即阅读顺序）。"""
    parts = [str(s.get("algebra") or ""), str(s.get("prereq_link") or ""),
             str(s.get("prereq") or ""), str(s.get("conclusion") or ""),
             str(s.get("intuition") or "")]
    parts += [str(x) for x in (s.get("prose") or [])]
    parts += [str(x) for x in (s.get("pitfalls") or [])]
    ex = s.get("example") or {}
    if isinstance(ex, dict):
        parts += [str(ex.get("prompt") or ""), str(ex.get("answer") or "")]
        parts += [str(x.get("text") if isinstance(x, dict) else x)
                  for x in (ex.get("steps") or [])]
    for v in (s.get("variants") or []):
        if isinstance(v, dict):
            parts += [str(v.get("q") or ""), str(v.get("a") or "")]
    return "\n".join(p for p in parts if p)


def dummy_slope_conflicts(review: dict) -> list:
    r"""**同一节内**：虚拟变量的"哪一组是 1"与"哪一组拿哪条斜率"对不上。F14-2 #1

    ⚠️ 为什么 F13 的"代数↔讲解"判据抓不到（42b9d97 实测漏掉 §17）：它按**组名**配对，
       而这里组名只出现在**讲解**里、代数里只有 `D=0/D=1`；对应关系要**两步推理**
       （`D=1 ⇔ 女性`，`D=1 ⇔ β₃+β₄`）才成立 —— 旧的键（组名）在代数里根本不存在。
    ⚠️ 为什么 `source_sum_group_conflicts`（⑨b）也抓不到 §17：⑨b 只看**转录**（找"同一个
       两项之和被安在两个组上"），不看笔记；它这次**确实报了**（warn 里那行
       `课上口述前后不一致：female 09:34:15；男性 09:33:35`），但旧版只在**跨节**矛盾时
       才把 ⚠ 落到正文，所以 §17 那里读者什么都没看到。
    判据：同节里 ① 组别↔0/1 ② 0/1↔斜率式 ③ 组别↔斜率式 三张表，两两推出来的斜率
    必须一致（按**项的集合**比，与加法顺序无关）；不一致 → 一条 `dummy-slope`。
    """
    out: list = []
    for i, s in enumerate(review.get("sections") or [], 1):
        if not isinstance(s, dict):
            continue
        text = _section_text(s)
        defs: dict = {}
        for m in _DEF_V2L.finditer(text):
            defs.setdefault(m.group("label"), m.group("v"))
        for m in _DEF_L2V.finditer(text):
            defs.setdefault(m.group("label"), m.group("v"))
        slopes: dict = {}
        for m in _DEF_SLOPE.finditer(text):
            slopes.setdefault(m.group("v"), _coef_terms(m.group("expr")))
        if not defs or len(slopes) < 2:
            continue
        bad = []
        for label, v in defs.items():
            want, got = slopes.get(v), _slope_said(text, label)
            if not want or not got or want == got:
                continue
            bad.append({"label": label, "v": v,
                        "want": sorted(want), "said": sorted(got)})
        if bad:
            out.append({"kind": "dummy-slope", "sections": [i], "section": i,
                        "labels": [b["label"] for b in bad], "detail": bad,
                        "defs": defs, "slopes": {k: sorted(v) for k, v in slopes.items()},
                        "texts": [f"{b['label']}：定义说 {','.join(b['want'])}、"
                                  f"讲解说 {','.join(b['said'])}" for b in bad]})
    return out


# ---- ⑪ 例题给了一个"本节的模型式里没有的项"一个系数 -------------------------
#: `male 的系数` / `系数 male`（例题/常见错误里的说法）。
_NAMED_COEF = re.compile(r"(?<![A-Za-z\-])(?P<n1>[A-Za-z][A-Za-z0-9_]{1,20})\s*的(?:系数|斜率)"
                         r"|(?:系数|斜率)\s*(?:是|为|＝|=)?\s*"
                         r"(?<![A-Za-z\-])(?P<n2>[A-Za-z][A-Za-z0-9_]{1,20})")
#: 模型式里的项（`β₃(male\cdot experience)` → 变量集合 `{male, experience}`）。
_MODEL_TERM = re.compile(r"(?<![A-Za-z\-])[A-Za-z][A-Za-z0-9_]{1,20}")
_COEF_LIKE = re.compile(r"^(?:b|beta)_?\d+$")


def _model_summands(algebra: str) -> list:
    """模型式 → 每个加法项的**变量集合**（系数与 TeX 标记不算变量）。**纯函数。**

    ⚠️ 不许把整串先归一：`\beta_1 education` 去掉空格会粘成 `b1education`（实测把
       `experience 的系数` 误报成"模型里没有这一项"）。逐项的**变量名**照原文抽，
       只把每个名字单独拿去判"是不是系数"（`\beta_0`→`b0`）。
    """
    body = re.sub(r"\$+", " ", str(algebra or ""))
    out = []
    for part in re.split(r"[+＋\-−=]", body):
        names = set()
        for m in _MODEL_TERM.finditer(part):
            if m.start() > 0 and part[m.start() - 1] == "\\":
                continue                     # `\cdot` / `\beta_0`：标记，不是变量
            if _COEF_LIKE.match(_norm_coef(m.group(0))):
                continue
            names.add(m.group(0).lower())
        if names:
            out.append(names)
    return out


def missing_term_conflicts(review: dict) -> list:
    r"""例题/常见错误给某个变量"一个系数"，但本节**展示的模型式**里没有它的独立项。F14-2 #2

    ⚠️ 实测（v3 busi §15）：`earnings=β₀+β₁education+β₂experience+β₃(male·experience)+u`
       里 `male` 只**藏在乘积里**（没有主效应项），而同节的例题说「male 的系数是 −2」、
       同节的常见错误写着「加了乘积却漏掉主效应项」—— 读者照着例题做题会找不到那个系数。
    判据：`X 的系数` 里的 X 在本节模型式里 ① 完全没出现，**或** ② 只出现在"多于一个变量的
    乘积"里（= 只有交互项、没有主效应）→ 一条 `missing-term`。只**标注**，不改模型。
    """
    out: list = []
    for i, s in enumerate(review.get("sections") or [], 1):
        if not isinstance(s, dict):
            continue
        terms = _model_summands(str(s.get("algebra") or ""))
        if not terms:
            continue
        standalone = {t for grp in terms if len(grp) == 1 for t in grp}
        in_product = {t for grp in terms if len(grp) > 1 for t in grp}
        # ⚠️ F15-B：**先确认本节真的摆着一条回归模型**。§15 的代数行是
        #    `t=\hat\beta/SE(\hat\beta)`、`p=P(|T|>|t|)` —— 一条 t 检验式子，
        #    根本没摆模型；旧判据却在它上面报「schooling 本节的模型式里没有这一项」，
        #    而 schooling 压根没出现在那行里（假阳性 + 句子还缺字）。
        #    门槛：模型式里要有 **≥2 个回归量**（各占一个加法项），才谈得上"漏了某一项"。
        if len(standalone) < 2:
            continue
        text = _section_text(s)
        named: dict = {}
        for m in _NAMED_COEF.finditer(text):
            named.setdefault((m.group("n1") or m.group("n2")).lower(), m.group(0))
        for name, phrase in named.items():
            if name in standalone or _COEF_LIKE.match(name):
                continue
            # ⚠️ 只有"**只在乘积里**"这一种才报（F15-B 后半）：X 在模型式里
            #    **完全没出现**时不许报 —— 那是"这一节没讲回归模型"，不是"漏了主效应"。
            if name not in in_product:
                continue
            out.append({"kind": "missing-term", "sections": [i], "section": i,
                        "term": name, "why": "只出现在乘积项里（没有单独的主效应项）",
                        "texts": [phrase]})
    return out


# ---- E 一节之内：同一个左端的两个等式，代入本节的定义后数值对不上（差个负号）----
#: Unicode 数学符号 → ASCII（`ast` 只认 ASCII 标识符）。
_UNI_MATH = (("ŷ", "yhat"), ("β", "beta"), ("γ", "gamma"), ("θ", "theta"),
             ("δ", "delta"), ("σ", "sigma"), ("α", "alpha"), ("λ", "lambda"),
             ("μ", "mu"), ("ε", "epsilon"), ("φ", "phi"), ("ρ", "rho"),
             ("τ", "tau"), ("²", "**2"), ("³", "**3"), ("⁴", "**4"),
             ("−", "-"), ("×", "*"), ("·", "*"), ("⋅", "*"), ("÷", "/"))
#: 左端是**一个记号**的等式（`g_j=…` / `A=…`）—— 这才是"定义"。
_DEF_LHS = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def _eq_parts(seg: str) -> list:
    """`a=b=c` → `[a,b,c]`（去掉 `$`、空段；**保留内部空格** —— 空格是隐式乘法的证据）。"""
    seg = re.sub(r"\$", "", str(seg or "")).strip()
    return [x.strip() for x in re.split(r"[=＝]", seg) if x.strip()]


def _key(s: str) -> str:
    """式子左端的比较键（去掉所有空白）。**纯函数。**"""
    return re.sub(r"\s+", "", str(s or ""))


def _to_py(expr: str) -> str:
    """式子 → 可 `ast` 解析的 Python（Unicode/LaTeX 记号 + 隐式乘法）。**纯函数。**"""
    import note_math
    s = str(expr or "")
    s = re.sub(r"\\hat\s*\{([^{}]+)\}", r"\1hat", s)        # \hat{y} → yhat
    s = re.sub(r"\\hat\s*([A-Za-z])", r"\1hat", s)
    s = re.sub(r"\\(?:text|mathrm|mathbf)\s*\{([^{}]*)\}", r"\1", s)
    for k, v in _UNI_MATH:
        s = s.replace(k, v)
    s = re.sub(r"_{([^{}]*)}", r"_\1", s)                   # 下标花括号
    # ⚠️ 空格是**隐式乘法**的唯一证据：`g_j b_h` → `g_j*b_h`。
    #    必须在 `_clean_expr` 抹掉空格**之前**做 —— 否则会粘成 `g_jb_h`（一个不存在的名字）。
    s = re.sub(r"(?<=[A-Za-z0-9_)\]])\s+(?=[A-Za-z(\d])", "*", s)
    s = note_math._clean_expr(s)
    # 隐式乘法：`yhat_j(` / `b_h(` —— 函数名（sqrt/exp/…）除外
    funcs = set(note_math._FUNCS)
    s = re.sub(r"\b([A-Za-z_][A-Za-z0-9_]*)\(",
               lambda m: m.group(0) if m.group(1) in funcs else m.group(1) + "*(", s)
    return s


def _py_names(py: str) -> set:
    """表达式里的符号名（函数名不算）。**纯函数。**"""
    import ast
    import note_math
    try:
        tree = ast.parse(py, mode="eval")
    except SyntaxError:
        return set()
    return {n.id for n in ast.walk(tree)
            if isinstance(n, ast.Name) and n.id not in note_math._FUNCS}


def _free_names(py: str, defs: dict, depth: int = 0) -> set:
    """**代入本节定义之后**的自由符号（定义里的记号再展开一层）。**纯函数。**"""
    out: set = set()
    for n in _py_names(py):
        if n in defs and depth < 3:
            out |= _free_names(defs[n], defs, depth + 1)
        else:
            out.add(n)
    return out


#: 探测点用的变量取值（都是**正**的小数）：正负号判据在这里最稳。
_PROBES = (0.3, 0.7, 0.45, 0.8, 0.2)


def _eval_py(py: str, ns: dict, seed: float):
    """在 `ns` 下求值（`_eval` 是白名单求值，不执行任意代码）。失败返回 `None`。**纯函数。**"""
    import ast
    import note_math
    try:
        tree = ast.parse(py, mode="eval")
        return note_math._eval(tree, "", seed, dict(ns))
    except Exception:                                         # noqa: BLE001
        return None


def _resolve_ns(py: str, defs: dict, ns: dict, seed: float, depth: int = 0) -> dict:
    """把表达式里引用的**本节定义**逐个算成数（递归，深度上限 3）。**纯函数。**"""
    out = dict(ns)
    if depth > 3:
        return out
    for n in _py_names(py):
        if n in out or n not in defs:
            continue
        out = _resolve_ns(defs[n], defs, out, seed, depth + 1)
        v = _eval_py(defs[n], out, seed)
        if v is None:
            return out
        out[n] = v
    return out


def symbol_sign_conflicts(review: dict) -> list:
    r"""**同节之内**：同一个左端写了两次，代入本节自己的定义后两边数值对不上。F15-E

    ⚠️ 为什么必须做（作者手读 v3/ecs170 §53 抓到）：本节先写
        `∂E/∂w_hj=(ŷ_j-y_j)ŷ_j(1-ŷ_j)b_h`，又定义 `g_j=(y_j-ŷ_j)ŷ_j(1-ŷ_j)`，
        最后写 `∂E/∂w_hj=g_j b_h` —— **按它自己的定义，右边差一个负号**。
        没有任何判据读"记号定义"，读者照着算会一直差号。
    判据：① 收集本节里"左端是单个记号"的定义 `NAME=expr`（连等式取最右一段）；
        ② 左端相同（去空白后逐字）的其它等式是**断言**，把定义代进去**数值**比对；
        ③ 只认**结构**：两边自由符号集合必须一致、至少一个探测点能算 → 才下结论；
        ④ 三个探测点全都对不上才报，两边成 −1 比例 → 说"差一个负号"，否则说"对不上"。
    只**标注**，一个式子都不改。
    """
    out: list = []
    for i, s in enumerate(review.get("sections") or [], 1):
        if not isinstance(s, dict):
            continue
        eqs: list = []
        # ⚠️ 只认 `$…$` 里的式子（`algebra` 与讲解里的公式都带 `$`）。
        #    不认"裸中文行文里恰好有等号"的那种 —— 实测把 `\hat{y}_j` 的花括号当成了
        #    等号两侧（提取出一堆 `\`=``}` 的垃圾），假阳性全是那么来的。
        for m in re.finditer(r"\$[^$]+\$", _section_text(s)):
            eqs.append(_eq_parts(m.group(0)))
        defs: dict = {}
        defs_raw: dict = {}          # 定义**原样**（标注里要把式子原封不动写出来）
        claims: dict = {}
        lhs_disp: dict = {}          # 左端的**原样写法**（下标/空格都在）
        for parts in (x for x in eqs if x):
            if len(parts) < 2:
                continue
            lhs, rhs = parts[0], parts[-1]
            k = _key(lhs)
            lhs_disp.setdefault(k, lhs)
            if _DEF_LHS.match(k) and not re.search(r"[+\-*/^]", k):
                defs.setdefault(k, _to_py(rhs))
                defs_raw.setdefault(k, rhs)
            claims.setdefault(k, []).append(rhs)
        for k, rhss in claims.items():
            uniq: list = []
            for r in rhss:
                if r not in uniq:
                    uniq.append(r)
            if len(uniq) < 2:
                continue
            base = _to_py(uniq[0])
            base_names = _free_names(base, defs)
            for other in uniq[1:]:
                oth = _to_py(other)
                if not base or not oth:
                    continue
                # 结构：**代入本节定义之后**的自由符号集合要一致（`g_j b_h` 与
                # `(yhat-y)yhat(1-yhat)b` 的自由符号都是 `{yhat_j,y_j,b_h}`）。
                if not base_names or _free_names(oth, defs) != base_names:
                    continue                     # 结构不同 → 未核对，不猜
                sign = same = 0
                ok = 0
                for seed in _PROBES:
                    ns0 = {n: seed + 0.05 * k for k, n in enumerate(sorted(base_names))}
                    va = _eval_py(base, _resolve_ns(base, defs, ns0, seed), seed)
                    vb = _eval_py(oth, _resolve_ns(oth, defs, ns0, seed), seed)
                    if va is None or vb is None or abs(va) < 1e-9 or abs(vb) < 1e-9:
                        continue
                    ok += 1
                    if abs(va + vb) <= 1e-6 * max(1.0, abs(va)):
                        sign += 1
                    elif abs(va - vb) <= 1e-6 * max(1.0, abs(va)):
                        same += 1
                if ok >= 2 and sign >= ok:
                    # ⚠️ F15-4①：**哪一条与定义不符**要指明。用人话说是这样：引用本节记号
                    #    （`g_j`）的那条等式，代入定义后与另一条对不上 —— 该在它身上标 ⚠，
                    #    而"应为"是把**它的右边**取负（`g_j b_h` → `−g_j b_h`），
                    #    这样读起来就是现在这句：「应为 −g_j b_h；正文写成 g_j b_h，差一个负号」。
                    others = _py_names(_to_py(other))
                    bases = _py_names(_to_py(uniq[0]))
                    if others & set(defs_raw):
                        ref, plain = other, uniq[0]
                    elif bases & set(defs_raw):
                        ref, plain = uniq[0], other
                    else:
                        ref, plain = other, uniq[0]
                    # 代入用到的**本节定义**（标注里要写清"按哪条定义代入"）。
                    used = {n: defs_raw.get(n, "") for n in _py_names(_to_py(ref))
                            if n in defs_raw}
                    lhs = lhs_disp.get(k, k)
                    out.append({"kind": "symbol-sign", "sections": [i], "section": i,
                                "term": lhs, "why": "差一个负号",
                                "base": plain, "other": ref, "defs": used,
                                # ⚠️ 两条都留**原样**（不截断）：标注里要放进 `$…$` 让 KaTeX 渲染，
                                #    截断的 LaTeX（`\partialE/\partialw_{hj}=(\hat y_j-y_j)\,\ha…`）
                                #    既渲染不出来、也读不懂。
                                #    `texts[0]` = 本节**先写**的那条；`texts[1]` = **与定义不符**的那条
                                #    （标注贴它 —— F15-4①）。
                                "texts": [f"{lhs}={plain}", f"{lhs}={ref}"]})
                    break
    return out


# ---- F 跨节：同一个符号，一节当阈值**减**、另一节当偏置**加** -------------------
#: 偏置/阈值类的符号（只认希腊字母）：`-\gamma` / `+\theta`。
#: 偏置/阈值符号的出现形状。三道闸都是**实测**加上的（每一道都对应一个假阳性）：
#:   ① 符号后面**必须就此打住**：`+\alpha\ln K` 里的 α 是乘在别人身上的系数
#:      （柯布–道格拉斯），`+\alpha_1x_{i2}` 后面跟着变量同理 —— 只有
#:      `+\gamma`、`-\gamma_h)`、`+\theta` 这种（后面是 `)`/`,`/行末）才是被加上的偏置项；
#:   ② 加减号前面得是**式子里的东西**（`)`/`]`/`}`/字母/数字/`=`）—— 中文行文里的
#:      "——θ"、"和 θ" 不算；
#:   ③ `_{1-\alpha}` 这种**花括号下标里面**的符号不收（卡方分位数）。
_SIGN_SYM = re.compile(r"(?<=[)\]}\w=])"
                       r"(?P<s>[+＋\-−])\s*(?P<sym>\\?(?:gamma|theta|beta|alpha|delta|"
                       r"lambda|sigma|epsilon|phi|rho|tau)"
                       r"|[\u03b1-\u03c9])(?P<sub>_(?:\{[A-Za-z0-9]{1,3}\}|[A-Za-z0-9]))?"
                       r"(?![A-Za-z\\}\d])")
#: 数学片段（只在这些里面看加减号，免得把中文行文的破折号当符号）。
_MATH_SPAN = re.compile(r"\$[^$]+\$")


def sign_flip_conflicts(review: dict) -> list:
    r"""**跨节**：同一个符号，前面当阈值**减**、后面当偏置**加**。F15-F

    ⚠️ 实测（v3/ecs170）：§37/§38 写 `h_h=f(\sum v_{ih}x_i-\gamma_h)`；§45 写
        `h=f(xV+\gamma)`、`\hat y=hW+\theta` —— 同一门课里 γ/θ 一会儿减一会儿加。
        读者按 §37 记，到 §45 就配错号。
    判据：只认**希腊字母**（`\gamma`/`γ` 这类偏置/阈值符号）：先出现的写法定"约定"，
    后面某一节出现**反号的同符号**且那一节自己没有混用 → 在后一节标 ⚠。
    ⚠️ 合法的不同符号（`\theta` 与 `b`，且文中写明 `b=-\theta`）**不许报** —— 不同符号。
    """
    per: dict = {}
    for i, s in enumerate(review.get("sections") or [], 1):
        if not isinstance(s, dict):
            continue
        text = str(s.get("algebra") or "") + "\n" + "\n".join(
            m.group(0) for m in _MATH_SPAN.finditer(_section_text(s)))
        for m in _SIGN_SYM.finditer(text):
            sym = _norm_symbol(m.group("sym"))
            per.setdefault((i, sym), set()).add(-1 if m.group("s") in "-−" else 1)
    conv: dict = {}
    for (i, sym), signs in sorted(per.items()):
        if len(signs) == 1 and sym not in conv:
            conv[sym] = (i, next(iter(signs)))
    out: list = []
    for (i, sym), signs in sorted(per.items()):
        if sym not in conv or i <= conv[sym][0] or len(signs) != 1:
            continue
        if next(iter(signs)) == conv[sym][1]:
            continue
        j = conv[sym][0]
        how = "减去" if conv[sym][1] < 0 else "加上"
        # ⚠️ F15-4②：原来写「（偏置写法 b=−θ 是另一回事）」—— 读起来像"这条不作数"，
        #    没告诉读者**为什么**两种写法都对。改成把理由说全：偏置 = −阈值，所以同一个
        #    符号在两个位置上的正负号本来就该相反（换个写法而已，不是笔误）。
        out.append({"kind": "sign-flip", "sections": [j, i], "section": i,
                    "sym": sym, "texts": [sym],
                    "why": f"第 {j} 节把 {_pretty_sym(sym)} 当阈值{how}，"
                           f"这里写成{'加上' if how == '减去' else '减去'}："
                           f"这是偏置写法，偏置 = −阈值，两种写法符号相反"})
    return out


def _norm_symbol(sym: str) -> str:
    """`\\gamma` / `γ` → 同一个规范名。**纯函数。**"""
    import unicodedata
    s = str(sym or "").lstrip("\\")
    s = unicodedata.normalize("NFKC", s).lower()
    for g, name in (("γ", "gamma"), ("θ", "theta"), ("β", "beta"), ("α", "alpha"),
                    ("δ", "delta"), ("λ", "lambda"), ("σ", "sigma"),
                    ("ε", "epsilon"), ("φ", "phi"), ("ρ", "rho"), ("τ", "tau")):
        s = s.replace(g, name)
    return s


def _pretty_sym(sym: str) -> str:
    """规范名 → 给人看的样子（`gamma` → `γ`）。**纯函数。**"""
    return {"gamma": "γ", "theta": "θ", "beta": "β", "alpha": "α", "delta": "δ",
            "lambda": "λ", "sigma": "σ", "epsilon": "ε", "phi": "φ",
            "rho": "ρ", "tau": "τ"}.get(str(sym), str(sym))


def conflicts(review: dict, *, entries: list | None = None) -> list:
    """全部跨节/节内对不上的地方（守卫用的一站式入口）。**纯函数。**"""
    out = (point_value_conflicts(review)
           + group_number_conflicts(review, entries=entries)
           + dummy_slope_conflicts(review)
           + missing_term_conflicts(review)
           + symbol_sign_conflicts(review)
           + sign_flip_conflicts(review))
    if entries:
        out += source_sum_group_conflicts(entries)
    return out
