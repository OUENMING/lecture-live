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


def conflicts(review: dict, *, entries: list | None = None) -> list:
    """全部跨节矛盾（守卫用的一站式入口）。**纯函数。**"""
    out = (point_value_conflicts(review)
           + group_number_conflicts(review, entries=entries))
    if entries:
        out += source_sum_group_conflicts(entries)
    return out
