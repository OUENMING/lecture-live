"""纲要数字核 —— 把正文那套「算一遍」用在课堂纲要上。

作者 2026-10-17（第五轮）：live 摘要层把 `MC = 3Q²−12Q+60` 原样带进纲要（应为 −24Q：
教授 13:40:41 自己更正过，转录里就有），课上口误 `5.3÷3=2.89` 也一路带到纲要。正文有
三道核 + 更正账本，纲要却一句没核 —— 而纲要是**先读的那一屏**，错数留在那里比正文错更伤。

判据（与正文同口径）：
· 算得对 → 一个字不动；
· 算不对、能按「正文校正账本 / 课件函数表」改对 → **改**，行尾标「（已更正：…）」；
· 改不了 → 行尾标「（⚠ 重算：…）」，**绝不静默通过**。

三档（对应正文的 ⓐ 代数自洽 / ⓑ 对照材料 / ⓒ 核不了）：
  ① 账本：正文三道核判过"这个数算错了"（`checks` 里 status == mismatch），
     纲要里出现**同一个数**就跟着改 —— 依据写「正文三道核」；
  ② 本句自己就能算的：除法（`5.3 除以 3 等于 2.89`）、代入（`AC(4)=12`）、
     方程 + 结论（`Q²−12Q+60 = 3Q²−12Q+60，所以 Q=0 或 Q=6`）；
  ③ 认不出的（文字叙述的式子、次数 > 2 的方程）不硬算、不标 —— 同正文「未核对」的边界。

方程那一档为什么要「课件函数表」：`12Q` 那处错在**式子本身**，纲要里没有 TC/AC/MC 的
定义，单看这一句只能判"结论与式子不符"，没法知道该改成什么。正文同一份笔记里有
`MC(Q)=3Q²−24Q+60`（由 TC 求导得到、`note_ledger.reconcile` 核过），所以把「同次数、
同首项、只是系数不同」的那一侧换成课件里的式子，再验证结论 —— 换不出就只标 ⚠。

纯函数：不联网、不写文件、不调模型。
"""
from __future__ import annotations

import copy
import math
import re

import note_math

#: 数字（带不带小数、正负号；Unicode 负号 `−` 也算）。
_NUM = r"-?\d+(?:\.\d+)?"
#: 「像式子」的一段：变量只认 q/Q/x（本课的数学就这三类），允许中间夹空格。
_MATH_RUN = re.compile(r"[0-9\s.+\-−qQx²³⁴¹⁰⁵⁶⁷⁸⁹^*()/÷×]+")
_EQ = re.compile(r"[=＝]")
#: 结论里的根：`Q = 0`。
_CLAIM = re.compile(r"(?P<var>[qQx])\s*[=＝]\s*(?P<v>" + _NUM + ")")
#: 除法口播：`5.3 除以 3 等于 2.89` / `5.3 ÷ 3 = 2.89` / `5.3/3 ≈ 2.89`。
_DIV = re.compile(r"(?P<a>" + _NUM + r")\s*(?:除以|÷|/)\s*(?P<b>" + _NUM + r")\s*"
                  r"(?:得到|得|等于|是|为|约等于|≈|=|＝)\s*(?P<c>" + _NUM + r")")
#: 代入：`AC(4)=12`（参数是具体数字才算）。
_SUBST = re.compile(r"(?P<name>[A-Za-z][A-Za-z0-9]{0,5})\s*\(\s*(?P<x>" + _NUM + r")\s*\)\s*"
                    r"[=＝]\s*(?P<v>" + _NUM + r")")
_SUP = {"0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴",
        "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹"}
#: 已经标过的行（标记里带着旧数，再核一次会把 `2.89 → 1.767` 读成"又错了"）。
_MARKED = re.compile(r"（已更正：|（⚠ 重算：")


# ---------------------------------------------------------------- 小工具
def _fmt(v: float) -> str:
    """数值 → 展示（3 位小数内，去掉尾零）：1.7666… → `1.767`、24.0 → `24`。"""
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def _same(a: float, b: float) -> bool:
    """数值比较。⚠️ 容差必须容得下**展示时的 3 位小数舍入**（第五轮实测：
    `5.3÷3` 算得 1.76666…、纲英里写 1.767 —— 1e-6 的容差会把它判成"又错一次"，
    于是同一条里出现 `1.767 → 1.767` 的假修正）。"""
    return abs(a - b) <= max(5e-4, 5e-4 * max(abs(a), abs(b)))


def _same_set(xs: list, ys: list) -> bool:
    if len(xs) != len(ys):
        return False
    left = list(xs)
    for y in ys:
        hit = next((i for i, x in enumerate(left) if _same(x, y)), None)
        if hit is None:
            return False
        left.pop(hit)
    return True


def _numbers(s: str) -> list:
    """字符串里的数（去重、保序）—— 账本配对用。"""
    out: list = []
    for m in re.finditer(_NUM, s):
        if m.group(0) not in out:
            out.append(m.group(0))
    return out


def _is_precise(n: str) -> bool:
    """值得跨句替换的数：带小数点，或至少 3 位（`2.89`/`1.767` ✓，`6`/`24` ✗）。

    ⚠️ 没有这道闸，账本里一条 `3 → 5` 会把纲英里所有 3 都改掉。
    """
    return "." in n or len(n.lstrip("-")) >= 3


def _replace_number(text: str, wrong: str, right: str) -> str:
    """只替换**独立**的那个数（`2.89` 不许动到 `12.89`）。"""
    pat = re.compile(r"(?<![\d.])" + re.escape(wrong) + r"(?![\d.])")
    return pat.sub(right, text)


def _has_number(text: str, n: str) -> bool:
    return re.search(r"(?<![\d.])" + re.escape(n) + r"(?![\d.])", text) is not None


def _var_of(s: str) -> str | None:
    found = {c for c in "qQx" if c in s}
    return found.pop() if len(found) == 1 else None


def _math_span(m) -> tuple:
    """一个"像式子"的 run → 去掉两端空白后的**精确区间**（`(start, end)`）。"""
    raw = m.group(0)
    a = m.start() + (len(raw) - len(raw.lstrip()))
    b = m.end() - (len(raw) - len(raw.rstrip()))
    return a, b


def _tail_math(s: str) -> tuple:
    runs = [m for m in _MATH_RUN.finditer(s) if m.group(0).strip()]
    if not runs:
        return None
    a, b = _math_span(runs[-1])
    return a, b, s[a:b]


def _head_math(s: str) -> tuple:
    runs = [m for m in _MATH_RUN.finditer(s) if m.group(0).strip()]
    if not runs:
        return None
    a, b = _math_span(runs[0])
    return a, b, s[a:b]


def _append_mark(t: str, mark: str) -> str:
    """行尾加标记：有句号就插在句号前（`……。` → `……（已更正：…）。`）。"""
    return (t[:-1] + mark + "。") if t.endswith("。") else (t + mark)


# ---------------------------------------------------------------- 多项式
def _poly(expr: str, var: str):
    """式子 → `Poly`（认不出返回 `None`，= 不判，不是 0）。"""
    import note_ledger
    import note_math
    fn = note_math._compile(expr, var, {})
    if fn is None:
        return None
    return note_ledger._poly_of(fn, var)


def _poly_text(p, var: str) -> str:
    """`Poly` → 展示式子（`3Q² − 24Q + 60`，与纲要里手写的形制一致）。"""
    out = ""
    for a in sorted(p.t, reverse=True):
        b = p.t[a]
        if abs(b) < 1e-9:
            continue
        if a == 0:
            body = f"{abs(b):g}"
        else:
            core = var if a == 1 else f"{var}{_SUP.get(str(a), '^' + str(a))}"
            body = core if abs(abs(b) - 1) < 1e-9 else f"{abs(b):g}{core}"
        if not out:
            out = ("−" if b < 0 else "") + body
        else:
            out += (" − " if b < 0 else " + ") + body
    return out or "0"


def _roots(p) -> list | None:
    """多项式 → 实根（只做到二次；更高次返回 `None` = 不判）。恒等式也返回 `None`。"""
    t = {k: v for k, v in p.t.items() if abs(v) > 1e-9}
    if not t:
        return None
    deg = max(t)
    if deg > 2:
        return None
    if deg == 2:
        a, b, c = t.get(2, 0.0), t.get(1, 0.0), t.get(0, 0.0)
        d = b * b - 4 * a * c
        if d < -1e-9:
            return []
        r = math.sqrt(max(d, 0.0))
        return sorted({round((-b - r) / (2 * a), 6), round((-b + r) / (2 * a), 6)})
    if deg == 1:
        return sorted({round(-t.get(0, 0.0) / t[1], 6)})
    return []


def _roots_text(roots: list, var: str) -> str:
    return " 或 ".join(f"{var} = {_fmt(r)}" for r in roots) if roots else "无实根"


# ---------------------------------------------------------------- 课件函数表 / 账本
def _course_functions(review: dict) -> dict:
    """正文里出现过的**所有**多项式函数 → `{name: [{var, poly}, …]}`。

    ⚠️ 第五轮实测（产物 I）：这里原先是"每个名字取**最常见**的那条定义"—— 可这节
       课里 `MC` 不止一条（教材例题 `MC=2q`、本课例题 `MC=3q²−24q+60`），出现次数
       多的是**教材那条**，于是纲里 `3Q²−12Q+60` 找不到"同次数同首项"的对照 →
       只能标 ⚠、改不动。函数的**名字**不是唯一键，**形状**才是：候选全留，
       匹配时按形状挑（挑完还要用结论回验 —— 配错也留不下）。
    """
    import note_math
    cand: dict = {}

    def _add(defs: dict) -> None:
        for name, (var, expr, fn) in defs.items():
            poly = _poly_of(fn, var)
            if poly is None:
                continue
            cand.setdefault(name, []).append((var, note_math._clean_expr(expr), poly))

    for s in (review.get("sections") or []):
        for _kind, u in note_math._units(s):
            # ⚠️ 两道都要走：`functions(整个单元)` 才认得出 `AC(q)=TC(q)/q` 这种互相引用，
            #    但它**同名只留第一条定义**（`if name in defs: continue`）—— 本课里
            #    `MC(q)=2q` 与 `MC(q)=3q²−24q+60` 各一条，只走第一道就只剩 `2q`。
            #    所以再逐句扫一遍，把第二条形状也收进来。
            _add(note_math.functions(u))
            for text in u:
                _add(note_math.functions([text]))
    out: dict = {}
    for name, lst in cand.items():
        seen: set = set()
        shapes: list = []
        for v, e, p in lst:
            if (v, e) in seen:
                continue
            seen.add((v, e))
            shapes.append({"var": v, "poly": p})
        out[name] = shapes
    return out


def _poly_of(fn, var: str):
    import note_ledger
    return note_ledger._poly_of(fn, var)


def _ledger_pairs(review: dict) -> list:
    """正文三道核里 status == mismatch 的行 → `[{wrong, right, how}]`。

    ⚠️ 只收「断言里恰好一个数、重算里最后那个数是新值」的行 —— 多值歧义一律跳过，
       宁可漏传也不许把不相干的数替换掉。
    """
    out: list = []
    for r in (review.get("checks") or []):
        if str(r.get("status") or "") != "mismatch":
            continue
        cn = _numbers(str(r.get("claim") or ""))
        rn = _numbers(str(r.get("recomputed") or ""))
        if not rn:
            continue
        wrong = [n for n in cn if n not in rn]
        right = rn[-1]
        if len(wrong) != 1 or right in cn or not _is_precise(wrong[0]):
            continue
        out.append({"wrong": wrong[0], "right": right,
                    "how": str(r.get("how") or r.get("recomputed") or "")})
    return out


def _near_miss(p, q) -> bool:
    """同一个形状、只是系数不对：次数相同、首项系数相同、整体不相等。"""
    if p is None or q is None or not p.t or not q.t:
        return False
    dp, dq = max(p.t), max(q.t)
    if dp != dq:
        return False
    if abs(p.t[dp] - q.t[dq]) > 1e-9 * max(1.0, abs(q.t[dq])):
        return False
    return not p.close(q)


def _matches_course(p, course: dict) -> bool:
    return any(fn["poly"].close(p) for lst in course.values() for fn in lst)


# ---------------------------------------------------------------- 四档判据
def _equations(text: str) -> list:
    """句子里的 `A = B`（两侧都得是**纯式子**，允许裹着中文）。"""
    out: list = []
    for m in _EQ.finditer(text):
        lt = _tail_math(text[:m.start()])
        rt = _head_math(text[m.end():])
        if lt is None or rt is None:
            continue
        left, right = lt[2], rt[2]
        var = _var_of(left + right)
        if not var:
            continue
        # ⚠️ `Q = 0` / `Q = 6` 是**结论**的写法，不是要解的方程 —— 实测原子要点
        #    「方程给出 Q = 0 或 Q = 6」被当成 `Q = 0`，再拿后面的 `Q = 6` 去对，
        #    报出一条假的「应按 Q = 0 得 Q = 0」。判据：一侧是光杆变量、另一侧是数 → 跳过。
        if re.fullmatch(r"[qQx]", left) or re.fullmatch(r"[qQx]", right):
            continue
        # `lt` 的区间本来就相对 `text`；`rt` 的区间相对后半段 → 加回 m.end()。
        out.append({"start": lt[0], "end": m.end() + rt[1],
                    "left": left, "right": right, "var": var})
    return out


def _claims(text: str, eq: dict) -> list:
    """方程**之后**的结论根（去重；`答案是 Q=6` 这种复述只留一份）。"""
    seen: set = set()
    out: list = []
    for m in _CLAIM.finditer(text):
        if m.group("var") != eq["var"] or m.start() < eq["end"]:
            continue
        v = float(m.group("v"))
        key = round(v, 6)
        if key in seen:
            continue
        seen.add(key)
        out.append({"start": m.start("v"), "end": m.end("v"), "v": v})
    return out


def _reconcile_equation(eq: dict, course: dict, claimed: list):
    """某一侧与课件函数「同次数同首项、只是系数不对」→ 换成课件那条，再看结论对不对。"""
    var = eq["var"]
    pl, pr = _poly(eq["left"], var), _poly(eq["right"], var)
    if pl is None or pr is None:
        return None
    for side, p in (("right", pr), ("left", pl)):
        for lst in course.values():
            for fn in lst:
                if fn["var"].lower() != var.lower() or not _near_miss(p, fn["poly"]):
                    continue
                if side == "right":
                    left, right = eq["left"], _poly_text(fn["poly"], var)
                    roots = _roots(pl - fn["poly"])
                else:
                    left, right = _poly_text(fn["poly"], var), eq["right"]
                    roots = _roots(fn["poly"] - pr)
                if roots is not None and _same_set(roots, claimed):
                    return left, right
    return None


def _check_text(text: str, course: dict, ledger: list) -> tuple:
    """一句话 →（改过的句子, 修正说明, 提醒）。**纯函数**。"""
    fixes: list = []
    warns: list = []
    t = text
    if _MARKED.search(t):
        return t, [], []                      # 标记是终态：不重复核、不叠第二个标记

    # ① 账本：正文判过的错数，纲要里同一个数跟着改
    for pair in ledger:
        if _has_number(t, pair["wrong"]):
            t = _replace_number(t, pair["wrong"], pair["right"])
            fixes.append(f"{pair['wrong']} → {pair['right']}；依据正文三道核：{pair['how']}")

    # ② 除法（右往左替换，偏移不会挪）
    hits = [m for m in _DIV.finditer(t)]
    for m in reversed(hits):
        # ⚠️ DB 审查 D-25：**和式里的除法项**不许当单条除法 —— `15 加上 225 除以 15 等于 30`
        #    （或 `60×2.89+500/2.89≈346.4`）会命中 `a=225,b=15,c=30` → 把**算对的**
        #    30 改成 15。判据与 `note_math` 共用（`is_sum_term`），不各写一份。
        if note_math.is_sum_term(t, m.start("a")):
            continue
        try:
            a, b, c = float(m.group("a")), float(m.group("b")), float(m.group("c"))
        except ValueError:
            continue
        if not b or _same(a / b, c):
            continue
        right = _fmt(a / b)
        t = t[:m.start("c")] + right + t[m.end("c"):]
        fixes.append(f"{_fmt(a)}÷{_fmt(b)} = {right}（原写 {_fmt(c)}）")

    # ③ 代入（`AC(4)=12` → 按课件函数重算）
    hits = [m for m in _SUBST.finditer(t)]
    for m in reversed(hits):
        lst = course.get(m.group("name"))
        if not lst:
            continue
        x, v = float(m.group("x")), float(m.group("v"))
        vals = [sum(c * (x ** k) for k, c in fn["poly"].t.items()) for fn in lst]
        if any(_same(want, v) for want in vals):
            continue                      # 有一个形状对得上 → 算对，不动
        # ⚠️ 同名多条（教材例题 `MC=2q` 与本课例题 `MC=3q²−24q+60` 并存）时**不猜**：
        #    多个形状都对不上时无从判定该按哪条改 —— 留给正文那道核（它按单元分辖）。
        if len(lst) != 1:
            continue
        want = vals[0]
        right = _fmt(want)
        t = t[:m.start("v")] + right + t[m.end("v"):]
        fixes.append(f"{m.group('name')}({_fmt(x)}) = {right}（原写 {_fmt(v)}）")

    # ④ 方程 + 结论根（一条纲要不该有两个要解的方程；只处理第一个）
    eqs = _equations(t)
    if eqs:
        eq = eqs[0]
        pl, pr = _poly(eq["left"], eq["var"]), _poly(eq["right"], eq["var"])
        if pl is not None and pr is not None:
            roots = _roots(pl - pr)
            claims = _claims(t, eq) if roots is not None else []
            if roots is not None and claims:
                claimed = sorted(c["v"] for c in claims)
                if not _same_set(roots, claimed):
                    fixed = _reconcile_equation(eq, course, claimed)
                    if fixed:
                        left, right = fixed
                        t = t[:eq["start"]] + f"{left} = {right}" + t[eq["end"]:]
                        if left == eq["left"]:
                            what = f"{eq['right']} → {right}"
                        elif right == eq["right"]:
                            what = f"{eq['left']} → {left}"
                        else:
                            what = f"{eq['left']} = {eq['right']} → {left} = {right}"
                        fixes.append(f"{what}；依据课件函数表")
                    elif _matches_course(pl, course) and _matches_course(pr, course) \
                            and len(claims) == len(roots):
                        # 式子与课件一致 → 是结论写错了：每个数换成**最近的**那个根
                        # （右往左替换，偏移不挪；根不重复用）
                        rest = list(roots)
                        for c in sorted(claims, key=lambda c: -c["start"]):
                            near = min(rest, key=lambda r: abs(r - c["v"]))
                            rest.remove(near)
                            t = t[:c["start"]] + _fmt(near) + t[c["end"]:]
                        fixes.append(f"结论应为 {_roots_text(roots, eq['var'])}")
                    else:
                        warns.append(f"按 {eq['left']} = {eq['right']} 应得 "
                                     f"{_roots_text(roots, eq['var'])}，与结论不符")

    marks = []
    if fixes:
        marks.append("（已更正：" + "；".join(fixes) + "）")
    if warns:
        marks.append("（⚠ 重算：" + "；".join(warns) + "）")
    if marks:
        t = _append_mark(t, "".join(marks))
    return t, fixes, warns


# ---------------------------------------------------------------- 入口
def check(items: dict, *, review: dict | None = None) -> tuple:
    """纲要 items →（核对后的 items, 修正记录, 提醒）。**原地不改输入**（深拷贝再改）。"""
    out = copy.deepcopy(items)
    if not review:
        return out, [], []
    course = _course_functions(review)
    ledger = _ledger_pairs(review)
    fixes: list = []
    warns: list = []
    for ch in (out.get("chapters") or []):
        for sn in (ch.get("sentences") or []):
            if not isinstance(sn, dict) or not str(sn.get("zh") or "").strip():
                continue
            new, f, w = _check_text(str(sn["zh"]), course, ledger)
            if not (f or w):
                continue
            sn["zh"] = new
            where = ch.get("title_zh") or ""
            fixes += [{"chapter": where, "what": x} for x in f]
            warns += [{"chapter": where, "what": x} for x in w]
    for a in (out.get("atoms") or []):
        if not isinstance(a, dict) or not str(a.get("zh") or "").strip():
            continue
        new, f, w = _check_text(str(a["zh"]), course, ledger)
        if not (f or w):
            continue
        a["zh"] = new
        fixes += [{"chapter": "", "what": x} for x in f]
        warns += [{"chapter": "", "what": x} for x in w]
    return out, fixes, warns
