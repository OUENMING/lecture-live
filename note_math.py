"""数学两层硬核：**公式必进 LaTeX**（`auto_tex`）+ **断言必须数值复算**（`check_review`）。

## 为什么要有这个模块（2026-10-17，验收抓到的真错）

笔记 §1 的例题写着「已知 TC(q)=60q+500（假设），求停产价格」，又说「使 AC 最小的产量
≈ 2.89」。而 `AC(q)=60+500/q` **单调递减，根本没有最小值** —— 例题是模型为了"凑完整"
自己编的函数。同一段里模型还把自己的困惑写进了正文（"实际上 AC'(q) 不可能为 0……
可能 TC 函数不同"）。

三道核当时只有两层（模型自己重算 + 课件对照），两层都**共用错的前提**：模型对着自己编的
函数"重算"当然自洽。所以这里加**机械的一层**，只做能确定判定的：

1. `auto_tex`：把 `q^3-3q^2+12q`、`q²`、`q_1` 这类纯文本式子转成 `$…$`，交给 KaTeX。
   ⚠️ 渲染层兜底，**不是**替模型写公式（prompt 仍然要求它自己写 `$…$`）。
2. `check_review`：
   - 解析笔记里出现的函数定义（`TC(q)=…` / `AC(q)=TC(q)/q` / `MC = …`）；
   - 「在 q=c 处取最小/最大」→ 数值求一阶导（中心差分）+ 二阶条件；
   - 「AC(c)=v」→ 代入复算；
   - 判不了的、以及**题面自称「假设」却没有 📖 出处**的例题 → `unchecked`（绝不猜）。

⚠️ 不引入依赖（没有 sympy）：导数用数值差分，表达式用 `ast` 白名单求值。
⚠️ 本模块纯函数：不联网、不读文件、不碰 GPU。
"""
from __future__ import annotations
import ast
import math
import re

# ---------------------------------------------------------------- 一、公式 → LaTeX

#: 上下标的幂：`x^3` / `x^{3}` / `x**3` → `x^{3}`。数字、单字母、括号里的都认。
_CARET = re.compile(r"\^\s*(\{[^{}]*\}|[A-Za-z0-9.]+)")
#: 下标：`q_1` / `q_{12}` → `q_{1}`。
_SUB = re.compile(r"_\s*(\{[^{}]*\}|[A-Za-z0-9]+)")
#: Unicode 上标（课件里常见）：`q²` → `q^{2}`。
_UNICODE_SUP = {"¹": "^{1}", "²": "^{2}", "³": "^{3}", "⁴": "^{4}"}
#: 一段"像公式的连续字符"：字母数字 + 上标/下标/括号 + 运算符。
#: ⚠️ 撇号**必须在原子字符里**（2026-10-17 验收实测）：`f'(x)=3x^2-3` 写成纯文本时，
#:    不带撇号的原子会从 `x)` 开始匹配 → 包出 `f'($x)=3x^{2}-3$` 这种半截公式
#:    （正文里"f'("留在外面、公式从括号中间开始）。带撇号就能整条吃进来。
_RUN = re.compile(r"(?<![A-Za-z0-9_^{}'])(?P<sign>[-−]?)[A-Za-z0-9][A-Za-z0-9_^{}()¹²³⁴']*"
                  r"(?:\s*[-+−*/=÷×]\s*[-−]?[A-Za-z0-9][A-Za-z0-9_^{}()¹²³⁴']*)+")
_SPAN = re.compile(r"\$\$.+?\$\$|\$[^$]+\$", re.S)


def auto_tex(text: str) -> str:
    """把残留的纯文本式子包成 `$…$`。**幂等**（已有的 `$…$` 原样不动）。

    判据只认「有数学记号」的片段（`^`、Unicode 上标、`_下标`）—— 普通英文里的
    `snake_case` / `e_mail` 不会被误包（它们不带 `^`，也不带"数字下标"）。
    """
    s = str(text or "")
    if not s:
        return s
    out, last = [], 0
    for m in _SPAN.finditer(s):
        out.append(_convert_plain(s[last:m.start()]))
        out.append(m.group(0))                  # 已经是公式（或 $$ 块）：原样
        last = m.end()
    out.append(_convert_plain(s[last:]))
    return "".join(out)


def _convert_plain(seg: str) -> str:
    def fix(m):
        run = m.group(0)
        if "^" not in run and not any(c in run for c in _UNICODE_SUP) \
                and not re.search(r"_\s*[\d{]", run):
            return run                          # 没有数学记号 → 不动（防误伤 snake_case）
        run = _CARET.sub(lambda x: "^{" + x.group(1).strip("{}") + "}", run)
        run = _SUB.sub(lambda x: "_{" + x.group(1).strip("{}") + "}", run)
        for k, v in _UNICODE_SUP.items():
            run = run.replace(k, v)
        run = run.replace("−", "-").replace("×", r" \times ").replace("÷", r" \div ")
        return f"${run}$"

    return _RUN.sub(fix, seg)


# ---------------------------------------------------------------- 二、数值复算
_FUNC_DEF = re.compile(
    r"(?P<name>[A-Za-z][A-Za-z0-9]{0,5})\s*\(\s*(?P<var>[A-Za-z])\s*\)\s*[=＝]\s*"
    r"(?P<expr>[^，,。；;\n]+)")
#: 数字（带不带小数、正负号）。
#: ⚠️ 后面紧跟 `√`/字母/`/`/数字/小数点的不算（`q=10√6` 里的 `10` 不是那个点，
#:    实测：人眼里是 10√6≈24.5，机械读成 10 → 报出 `AC'(10)≈−2.5≠0` 的假修正）。
_NUM = r"-?\d+(?:\.\d+)?(?![0-9.√√A-Za-z/])"
#: 「在 q = 2.89 处取最小」「q=6 时 AC 最小」「最小平均成本对应 q = 6」
_MINMAX = re.compile(
    r"(?:在\s*)?[qQx]\s*\*?\s*[=＝]\s*(?P<c>" + _NUM + r")\s*(?:处|时|点)?[^。；;]{0,12}?"
    r"(?P<kind>最小|最大|极小|极大)"
    r"|(?P<kind2>最小|最大|极小|极大)[^。；;]{0,18}?[qQx]\s*\*?\s*[=＝]\s*(?P<c2>" + _NUM + r")"
    # 「使 AC 最小的产量约为 2.89」（数字在结论后面，这是实测里最常见的中文语序）
    # 「使 AC 最小的**产量**约为 2.89」——⚠️ 必须有"产量/产出/数量"，否则
    # 「最小平均成本**为 16**」里的 16 是值不是点，会报假修正（实测抓到两例）。
    r"|(?P<kind3>最小|最大|极小|极大)[^。；;]{0,10}?(?:产量|产出|数量)[^。；;]{0,6}?"
    r"(?:约|为|是|=|＝)\s*(?P<c3>" + _NUM + r")")
#: 「AC(2.89) = 233.01」
#: 「AC(2.89)=233.01」「AC(2.89)=(60·2.89+500)/2.89≈233.01」——
#: ⚠️ 取**这一句里最后一个** `=`/`≈` 后面的数字（等号右边常是整条算式），
#:    并要求它后面是标点/结尾（`233.01*2` 这种不算一个值）。
_SUBST = re.compile(r"(?P<name>[A-Za-z][A-Za-z0-9]{0,5})\s*\(\s*(?P<c>" + _NUM + r")\s*\)"
                    r"[^。；;\n]*[=＝≈]\s*(?P<v>" + _NUM + r")\s*(?:[。，,；;）)]|$)")


def _clean_expr(expr: str) -> str:
    """LaTeX / Unicode 写法 → 可以 `ast` 解析的 Python 表达式。"""
    s = str(expr or "")
    s = s.replace("−", "-").replace("×", "*").replace("÷", "/").replace("·", "*")
    for k, v in (("⁴", "**4"), ("³", "**3"), ("²", "**2"), ("¹", "**1")):
        s = s.replace(k, v)
    s = re.sub(r"\\left|\\right|\\,|\\;|\\ ", "", s)
    s = re.sub(r"\^\s*\{([^{}]*)\}", r"**(\1)", s)
    s = re.sub(r"\^\s*([A-Za-z0-9.]+)", r"**\1", s)
    s = re.sub(r"\\cdot|\\times", "*", s)
    s = re.sub(r"\\div", "/", s)
    s = re.sub(r"\\frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}", r"((\1)/(\2))", s)
    s = re.sub(r"[{}\$\\]", "", s)
    s = re.sub(r"\s+", "", s)
    # 隐含乘法：`12q` / `3(q+1)` / `)(` —— 数学写法里到处都是，Python 不认
    s = re.sub(r"(\d)([A-Za-z(])", r"\1*\2", s)
    s = re.sub(r"\)([A-Za-z(])", r")*\1", s)
    return s


_FUNCS = {"sqrt": math.sqrt, "exp": math.exp, "log": math.log,
          "ln": math.log, "abs": abs}
_BINOPS = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b,
           ast.Mult: lambda a, b: a * b, ast.Div: lambda a, b: a / b,
           ast.Pow: lambda a, b: a ** b}


def _eval(node, var: str, x: float, ns: dict):
    """`ast` 白名单求值（没有 `eval`：表达式来自模型，绝不能执行任意代码）。"""
    if isinstance(node, ast.Expression):
        return _eval(node.body, var, x, ns)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.Name):
        if node.id == var:
            return float(x)
        if node.id in _FUNCS:
            return _FUNCS[node.id]
        if node.id in ns:
            return ns[node.id]
        raise ValueError(f"未知符号 {node.id}")
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        return _BINOPS[type(node.op)](_eval(node.left, var, x, ns),
                                      _eval(node.right, var, x, ns))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_eval(node.operand, var, x, ns)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.UAdd):
        return _eval(node.operand, var, x, ns)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        fn = _FUNCS.get(node.func.id) or ns.get(node.func.id)
        if fn is None:
            raise ValueError(f"未知函数 {node.func.id}")
        return fn(*[_eval(a, var, x, ns) for a in node.args])
    raise ValueError("表达式里有不支持的运算")


def _compile(expr: str, var: str, ns: dict):
    """表达式字符串 → 单变量函数。解析不了返回 `None`（= 未核对，不是 0）。"""
    clean = _clean_expr(expr)
    if not clean:
        return None
    try:
        tree = ast.parse(clean, mode="eval")
    except SyntaxError:
        return None
    try:
        _eval(tree, var, 1.0, ns)               # 先试算一次：未知符号在这里就炸
    except Exception:                                          # noqa: BLE001
        return None

    def f(x):
        return _eval(tree, var, x, ns)
    return f


def _compile_chain(expr: str, var: str, ns: dict):
    """`AC(q)=TC(q)/q=q^2-12q+60` 这种**连等式**：取最右边能解析的一段。

    ⚠️ 不处理它的话，例题步骤里那句"AC(q)=TC(q)/q=q^2-12q+60"整条编译失败 →
       AC 在这一单元里"没有定义" → 后面的 `AC(6)=24` 只能标未核对（判据实测）。
    """
    fn = _compile(expr, var, ns)
    if fn is not None:
        return fn
    if "=" in expr:
        for seg in reversed([s for s in expr.split("=") if s.strip()]):
            fn = _compile(seg, var, ns)
            if fn is not None:
                return fn
    return None


def functions(texts) -> dict:
    """从文本里收集函数定义 → `{名字: (变量, 表达式, 可调用)}`。

    ⚠️ 三轮：`AC(q)=TC(q)/q` 可能**先于** `TC(q)=…` 出现（模型的行文顺序），
       一轮的话 AC 会因为"未知函数 TC"被丢掉 —— 那不是"定义不存在"，是我们的顺序问题。
    """
    raw: list = []
    for text in texts:
        for m in _FUNC_DEF.finditer(str(text or "")):
            name, var, expr = m.group("name"), m.group("var"), m.group("expr").strip()
            if name.lower() in {"and", "or", "if", "the"}:
                continue
            raw.append((name, var, expr))
    ns: dict = {}
    defs: dict = {}
    todo = list(raw)
    for _ in range(3):
        rest = []
        for name, var, expr in todo:
            if name in defs:
                continue
            fn = _compile_chain(expr, var, ns)
            if fn is None:
                rest.append((name, var, expr))
                continue
            defs[name] = (var, expr, fn)
            ns[name] = fn                      # 让 `AC(q)=TC(q)/q` 这类定义能互相引用
        if len(rest) == len(todo):
            break
        todo = rest
    return defs


def _d1(f, x: float) -> float:
    h = max(1e-5, abs(x) * 1e-6)
    return (f(x + h) - f(x - h)) / (2 * h)


def _d2(f, x: float) -> float:
    h = max(1e-4, abs(x) * 1e-5)
    return (f(x + h) - 2 * f(x) + f(x - h)) / (h * h)


def _fmt(v: float) -> str:
    return f"{v:.4g}" if abs(v) >= 1e-6 else "0"


#: 中文说法 → 函数名。⚠️ 2026-10-17 两次生成的产物上都抓到一个假修正：
#:   「令 MC=AC 得 q=6，使**平均成本**最小的产量为 q=6」被拿去核 `MC'(6)≠0` ——
#:   因为句子里同时出现了 MC 与 AC，而断言的主语是"平均成本"。中文的学科词是**更强的信号**。
_ZH_FUNC = (("平均可变成本", "AVC"), ("平均成本", "AC"), ("边际成本", "MC"),
            ("总成本", "TC"), ("总收益", "TR"))


def _pick(fdefs: dict, text: str):
    """这条断言说的是哪个函数。

    ⚠️ 文本里**点了名**（`AC(6)=…` / `AC 最小`）就只认那个名字 —— 名字不在
       当前的函数表里就返回 `None`（宁可"未核对"，不许拿另一个函数来算）。
       只有**完全没有函数名**时才允许"本单元只定义了一个函数"的推断。
    """
    for zh, en in _ZH_FUNC:
        if zh in text and en in fdefs:
            return en                 # 中文学科词优先（见 `_ZH_FUNC` 的说明）
    hits = [n for n in fdefs
            if re.search(rf"(?<![A-Za-z]){re.escape(n)}(?![A-Za-z])", text)]
    if hits:
        return max(hits, key=len)     # 最长名字优先（`TFC` 比 `TF` 更具体）
    if len(fdefs) == 1 and not re.search(r"[A-Za-z][A-Za-z0-9]{0,5}\s*\(", text):
        return next(iter(fdefs))
    return None


#: 一句话里出现这些记号时，机械挑数字必错（实测两例，见 `tests/test_note_math.py`）：
#: `…最小值在q→0+处为无穷大？实际上AC(q)=80+2000/q…`（挑到 80）、
#: `…f''(-1)=-18<0（极大）…`（挑到 18）。**判不了就判不了**，绝不许给出错的"修正值"。
_NOISY = re.compile(r"→|[<>]|f''|f′|∵|∴")

_EXTREMUM = re.compile(r"最小|最大|极小|极大")


def _norm_claim(t: str) -> str:
    return re.sub(r"\s+", "", str(t or "")).lower()


def _units(s: dict) -> list:
    """一节的文本按**单元**切分：讲解 / 例题 / 每道变式 / 补全题各是一个单元。

    ⚠️ 单元 = 函数命名空间（2026-10-17 判定实测抓到）：整节共用一个命名空间时，
       "例题里 AVC=… 与变式里 AVC=…"会互相串门 —— 判据会把变式的函数拿去核例题的
       断言，报出一堆**假 mismatch**（实测：`AC'(2.89)≈1.78` 其实是另一道题的 AC'）。
    """
    out = [[str(x) for x in (s.get("prose") or [])]]
    ex = s.get("example") or {}
    if ex.get("prompt") or ex.get("steps"):
        out.append([str(ex.get("prompt") or ""), str(ex.get("answer") or "")]
                   + [str(st.get("text") or "") for st in (ex.get("steps") or [])])
    for v in (s.get("variants") or []):
        out.append([str(v.get("q") or ""), str(v.get("a") or "")]
                   + [str(x) for x in (v.get("steps") or [])])
    fd = s.get("faded") or {}
    if fd.get("q") or fd.get("a"):
        out.append([str(fd.get("q") or ""), str(fd.get("a") or "")])
    return [[x for x in u if x] for u in out]


def _kind(word: str) -> str:
    return "最小" if word in ("最小", "极小") else "最大"


def check_review(review: dict) -> list:
    """笔记 JSON → 机械核对行（`{claim, how, status, recomputed}`）。

    status：`ok` / `mismatch`（**正文不改**，进"修正说明"）/ `unchecked`（判不了）。
    """
    rows: list = []
    for s in (review.get("sections") or []):
        units = _units(s)
        texts = [x for u in units for x in u]
        # 单元自己的定义优先；回落到整节时**同名多定义就算歧义 → 未核对**（不许猜）
        sec_defs = functions(texts)
        unit_defs = [functions(u) for u in units]
        per_name: dict = {}
        for ud in unit_defs:
            for n in ud:
                per_name[n] = per_name.get(n, 0) + 1

        def _scope(t: str, u_idx: int) -> dict:
            local = unit_defs[u_idx]
            if _pick(local, t):
                return local
            return {n: v for n, v in sec_defs.items() if per_name.get(n, 0) == 1}
        for u_idx, u in enumerate(units):
            for t in u:
                fdefs = _scope(t, u_idx)
                rows += _rows_for_text(t, fdefs)
    return rows + _source_rows(review)


def _rows_for_text(t: str, fdefs: dict) -> list:
    """一条文本里的极值/代入断言 → 核对行。"""
    rows: list = []
    if _NOISY.search(t):
        # ⚠️ 公式密到分不清哪句话是"结论"时：**不猜** —— 标未核对，让人来看。
        #    实测猜过两次，两次都猜错（挑到 80 / 挑到 18），而假的"已修正"比漏核坏得多。
        if _EXTREMUM.search(t):
            rows.append({"claim": t, "status": "unchecked", "recomputed": "",
                         "how": "这句话里公式/推导太密，机械核挑不出唯一的那条结论（请人工复查）"})
        return rows
    for m in _MINMAX.finditer(t):
        c = m.group("c") or m.group("c2") or m.group("c3")
        word = m.group("kind") or m.group("kind2") or m.group("kind3")
        if c is None:
            continue
        kind = _kind(word)
        name = _pick(fdefs, t)
        if name is None:
            rows.append({"claim": t, "status": "unchecked", "recomputed": "",
                         "how": "没有给出函数，无法复算极值（不许猜）"})
            continue
        _var, _expr, f = fdefs[name]
        x = float(c)
        try:
            d1, d2 = _d1(f, x), _d2(f, x)
        except Exception:                                     # noqa: BLE001
            rows.append({"claim": t, "status": "unchecked", "recomputed": "",
                         "how": f"{name} 在 q={c} 附近算不出来（定义域外/数值问题）"})
            continue
        tol = 1e-3 * max(1.0, abs(x))
        if abs(d1) > tol:
            rows.append({"claim": t, "status": "mismatch",
                         "recomputed": f"{name}'({c})≈{_fmt(d1)}",
                         "how": f"数值求导：{name}'({c})≈{_fmt(d1)}≠0，"
                                f"该点不是驻点，谈不上{word}"})
            continue
        bad = (kind == "最小" and d2 < 0) or (kind == "最大" and d2 > 0)
        if bad:
            rows.append({"claim": t, "status": "mismatch",
                         "recomputed": f"{name}''({c})≈{_fmt(d2)}",
                         "how": f"二阶条件不成立：{name}''({c})≈{_fmt(d2)}，"
                                f"这是{'最大' if d2 < 0 else '最小'}值"})
            continue
        rows.append({"claim": t, "status": "ok",
                     "recomputed": f"{name}({c})={_fmt(f(x))}",
                     "how": f"数值求导：{name}'({c})≈{_fmt(d1)}（≈0，驻点成立）、"
                            f"{name}''({c})≈{_fmt(d2)}（{word}的二阶条件成立）"})
    if _NOISY.search(t):
        return rows
    for m in _SUBST.finditer(t):
        # ⚠️ 这里**按名字**取（大小写不敏感），不走 `_pick`：`AC(6)=24` 点名了 AC，
        #    本单元只定义了 TC 时**不许**拿 TC 代为计算（判据实测抓到过 144 这种假数）。
        _n = m.group("name")
        name = next((n for n in fdefs if n.lower() == _n.lower()), None)
        if name is None:
            continue
        x, v = float(m.group("c")), float(m.group("v"))
        try:
            got = fdefs[name][2](x)
        except Exception:                                     # noqa: BLE001
            rows.append({"claim": t, "status": "unchecked", "recomputed": "",
                         "how": f"{name}({x}) 算不出来"})
            continue
        ok = abs(got - v) <= 1e-3 * max(1.0, abs(v))
        rows.append({"claim": t, "status": "ok" if ok else "mismatch",
                     "recomputed": f"{_fmt(got)}",
                     "how": f"代入复算：{name}({_fmt(x)})={_fmt(got)}"
                            + ("（与断言一致）" if ok else f"，断言写的是 {_fmt(v)}")})
    return rows


def _source_rows(review: dict) -> list:
    """例题来源：题面自称"假设" / 给了函数，而标题没有 📖 出处 —— "自编例题"的形状。"""
    rows: list = []
    for s in (review.get("sections") or []):
        ex = s.get("example") or {}
        title, prompt = str(ex.get("title") or ""), str(ex.get("prompt") or "")
        if not prompt:
            continue
        # ⚠️ 只有 📖 能证明"题面来自材料"；`🎙` 只说明"这题课上讲过"，**不能**证明
        #    函数是哪来的（课上常常只给片段）。所以 🎙 的例子**也不许**自称"假设"。
        if "📖" in title:
            continue
        confession = ("假设" in prompt) or ("假定" in prompt)
        defined = bool(_FUNC_DEF.search(prompt))
        if confession or defined:
            rows.append({"claim": f"例题：{title or prompt[:40]}", "status": "unchecked",
                         "recomputed": "",
                         "how": "例题标题没有 📖 出处，题面却给了函数"
                                + ("/自称「假设」" if confession else "")
                                + " —— 自编例题是硬禁区（课上只给片段就写片段+未核对）"})
    return rows


def merge_checks(det: list, llm: list) -> list:
    """机械核与模型核合并：**机械核优先**（模型会对着错前提"自洽"）。

    同一条断言（去空白、小写后相同）时，`det` 覆盖 `llm`；其余按 `det` 在前。
    """
    det = det or []
    seen = {_norm_claim(r.get("claim")) for r in det}
    return list(det) + [r for r in (llm or []) if _norm_claim(r.get("claim")) not in seen]
