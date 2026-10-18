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


def _balanced(s: str, i: int) -> int:
    """`s[i] == '{'` → 配对 `}` 的下一位；扫不到返回 -1。"""
    depth = 0
    while i < len(s):
        if s[i] == "{":
            depth += 1
        elif s[i] == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return -1


#: 裸 `\frac{…}{…}`（模型忘写 `$…$` 时漏出来的 LaTeX 命令，BUSI3243/ECS170 实录 12/16 处）。
_FRAC_HEAD = re.compile(r"\\frac\s*\{")


def _wrap_bare_frac(seg: str) -> str:
    """把**不在 `$…$` 里的** `\\frac{…}{…}` 包成 `$…$`（花括号配平扫，`\\frac{\\hat{β}}{SE(\\hat{β})}` 里套着 `{}`）。

    ⚠️ 只包 `\\frac` 的**两个参数**，不动周围文字：`t = \\frac{a}{b}` → `t = $\\frac{a}{b}$`。
    """
    out: list = []
    last = 0
    for m in _FRAC_HEAD.finditer(seg):
        if m.start() < last:
            continue
        j = _balanced(seg, m.end() - 1)                 # 第一个 `{`
        if j < 0:
            continue
        k = j + (len(seg[j:]) - len(seg[j:].lstrip()))
        if k >= len(seg) or seg[k] != "{":
            continue
        e = _balanced(seg, k)                            # 第二个 `{`
        if e < 0:
            continue
        out.append(seg[last:m.start()])
        out.append(f"${seg[m.start():e]}$")
        last = e
    out.append(seg[last:])
    return "".join(out)


#: 裸 `\begin{…}…\end{…}` 环境（模型忘写 `$…$` 时漏出来的矩阵、cases 等；ECS 实测）。
_ENV_HEAD = re.compile(r"\\begin\s*\{([A-Za-z*]+)\}")


def _wrap_bare_env(seg: str) -> str:
    """把**不在 `$…$` 里的** `\\begin{X}…\\end{X}` 包成 `$…$`。

    ⚠️ 只认**自配对**的环境（`\\begin{X}` … 最近的 `\\end{X}`）——它自带定界符，
       不会误吞真代码（ECS 的 `torch.nn.Linear(…)` 没有任何反斜杠命令）。
    """
    out: list = []
    last = 0
    for m in _ENV_HEAD.finditer(seg):
        if m.start() < last:
            continue
        name = m.group(1)
        end_m = re.compile(r"\\end\s*\{" + re.escape(name) + r"\}").search(seg, m.end())
        if end_m is None or end_m.end() - m.start() > 2000:
            continue
        out.append(seg[last:m.start()])
        out.append(f"${seg[m.start():end_m.end()]}$")
        last = end_m.end()
    out.append(seg[last:])
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

    # ⚠️ 顺序：先包裸 `\frac{…}{…}` 与 `\begin{…}…\end{…}`（会产生新的 `$…$`），
    #    再按 `$…$` 切开做普通式子的兜底 —— 反过来会把 `\frac{x^2}{2}` 里的 `x^2`
    #    再包一层（`$\frac{$x^{2}$}{2}$`，坏的）。
    seg = _wrap_bare_frac(seg)
    seg = _wrap_bare_env(seg)
    out, last = [], 0
    for m in _SPAN.finditer(seg):
        out.append(_RUN.sub(fix, seg[last:m.start()]))
        out.append(m.group(0))
        last = m.end()
    out.append(_RUN.sub(fix, seg[last:]))
    return "".join(out)


_SUPERS = {"0": "\u2070", "1": "\u00b9", "2": "\u00b2", "3": "\u00b3", "4": "\u2074",
           "5": "\u2075", "6": "\u2076", "7": "\u2077", "8": "\u2078", "9": "\u2079",
           "-": "\u207b", "+": "\u207a"}
_SUBS = {"0": "\u2080", "1": "\u2081", "2": "\u2082", "3": "\u2083", "4": "\u2084",
         "5": "\u2085", "6": "\u2086", "7": "\u2087", "8": "\u2088", "9": "\u2089"}


#: 上标里的字母（Unicode 的"修饰字母"区）：`q^n` → `qⁿ`、`x^T`（转置）→ `xᵀ`。
#: 认得出的都在这里；认不出的（大写 C/F/Q/S/X/Y/Z、小写 q）**不转**（见 `_script`）。
_SUP_LETTERS = {"a": "ᵃ", "b": "ᵇ", "c": "ᶜ", "d": "ᵈ", "e": "ᵉ", "f": "ᶠ", "g": "ᵍ",
                "h": "ʰ", "i": "ⁱ", "j": "ʲ", "k": "ᵏ", "l": "ˡ", "m": "ᵐ", "n": "ⁿ",
                "o": "ᵒ", "p": "ᵖ", "r": "ʳ", "s": "ˢ", "t": "ᵗ", "u": "ᵘ", "v": "ᵛ",
                "w": "ʷ", "x": "ˣ", "y": "ʸ", "z": "ᶻ",
                "A": "ᴬ", "B": "ᴮ", "D": "ᴰ", "E": "ᴱ", "G": "ᴳ", "H": "ᴴ", "I": "ᴵ",
                "J": "ᴶ", "K": "ᴷ", "L": "ᴸ", "M": "ᴹ", "N": "ᴺ", "O": "ᴼ", "P": "ᴾ",
                "R": "ᴿ", "T": "ᵀ", "U": "ᵁ", "V": "ⱽ", "W": "ᵂ"}
#: 下标里的字母：`AC_{min}` → `ACₘᵢₙ`。同样只收 Unicode 里真有的那些。
_SUB_LETTERS = {"a": "ₐ", "e": "ₑ", "h": "ₕ", "i": "ᵢ", "j": "ⱼ", "k": "ₖ", "l": "ₗ",
                "m": "ₘ", "n": "ₙ", "o": "ₒ", "p": "ₚ", "r": "ᵣ", "s": "ₛ", "t": "ₜ",
                "u": "ᵤ", "v": "ᵥ", "x": "ₓ"}
_SUP_ALL = {**_SUPERS, **_SUP_LETTERS, "*": "*"}       # `q^*` → `q*`（经济学写法）
_SUB_ALL = {**_SUBS, "+": "₊", "-": "₋", **_SUB_LETTERS}

#: 常见命令 → Unicode（第五轮：`p = \min AC`、`\sqrt{\frac{2}{3}}`、
#: `AC(q) = \underline{\hspace{1cm}}` 在兜底里原样露出反斜杠 —— 截图自查抓到的）。
_TEX_CMD = {
    # 函数名 / 算子
    "min": "min", "max": "max", "ln": "ln", "log": "log", "lg": "lg", "exp": "exp",
    "sin": "sin", "cos": "cos", "tan": "tan", "cot": "cot", "sec": "sec", "csc": "csc",
    "arcsin": "arcsin", "arccos": "arccos", "arctan": "arctan", "lim": "lim",
    "det": "det", "dim": "dim", "gcd": "gcd", "mod": "mod", "left": "", "right": "",
    # 关系 / 运算
    "leq": "≤", "le": "≤", "geq": "≥", "ge": "≥", "neq": "≠", "ne": "≠", "approx": "≈",
    "sim": "~", "simeq": "≃", "equiv": "≡", "propto": "∝", "pm": "±", "mp": "∓",
    "times": "×", "cdot": "·", "div": "÷", "ast": "*", "infty": "∞", "partial": "∂",
    "nabla": "∇", "sum": "Σ", "prod": "Π", "int": "∫", "sqrt": "√", "perp": "⊥",
    "angle": "∠", "circ": "∘", "bullet": "•", "cdots": "⋯", "ldots": "…", "dots": "…",
    # 箭头
    "to": "→", "rightarrow": "→", "longrightarrow": "→", "leftarrow": "←", "gets": "←",
    "Rightarrow": "⇒", "Leftarrow": "⇐", "Leftrightarrow": "⇔", "mapsto": "↦",
    "up": "↑", "down": "↓",
    # 希腊字母（经济学里常见的）
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε",
    "varepsilon": "ε", "zeta": "ζ", "eta": "η", "theta": "θ", "kappa": "κ",
    "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ", "pi": "π", "rho": "ρ",
    "sigma": "σ", "tau": "τ", "phi": "φ", "varphi": "φ", "chi": "χ", "psi": "ψ",
    "omega": "ω", "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Pi": "Π",
    "Sigma": "Σ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
    # 间距
    "quad": " ", "qquad": "  ",
}
#: 单字符命令（反斜杠后不是字母）：空白类直接吃掉，符号类去反斜杠。
_TEX_CHAR_CMD = {r"\%": "%", r"\$": "$", r"\&": "&", r"\#": "#", r"\_": "_",
                 r"\,": " ", r"\;": " ", r"\:": " ", r"\!": "", r"\ ": " "}


def _script(body: str, table: dict) -> str:
    """整组都认得出才转；有一个认不出就返回 `""`（调用处原样留 TeX 源码）。

    ⚠️ 这道"认不全就放弃"是第五轮补的：老实现认不出就把原字符留在**基线**上 ——
       `q^{q}` 会变成 `qq`（读者当乘积）、`AC_{min}` 会变成 `ACmin`。
       作者口径一贯是「宁可露 TeX 源码，不许显示错的数学」（同 `x^-1`）。
    """
    if not body or any(c not in table for c in body):
        return ""
    return "".join(table[c] for c in body)


def pretty_tex(text: str) -> str:
    """TeX → 能直接读的 Unicode（`x^{3}` → `x³`、`q_{1}` → `q₁`、`q^n` → `qⁿ`）。

    ⚠️ 用途只有一个：**KaTeX 没加载时页面上显示的兜底文字**（作者 2026-10-17：
       「任何短文本都要过同一个渲染……不许露 `^{}`」）。真正的数学仍走 KaTeX
       （`data-tex` 里是原样 TeX），`$…$` 标记照旧。
    """
    s = str(text or "")

    def _sup(m):
        conv = _script(m.group(1).strip("{}"), _SUP_ALL)
        return conv or m.group(0)      # 认不出 → 原样留（宁可露源码）

    def _sub(m):
        conv = _script(m.group(1).strip("{}"), _SUB_ALL)
        return conv or m.group(0)

    # ⚠️⚠️ 上标只吃**紧跟 `^` 的那一个 token**：`{…}`、纯数字串、单个字母、`*`。
    #    历史两次翻车（都是"读出来是错的数学"，作者第四轮再次点名）：
    #      · `[0-9+-]{1,3}` → `Q^3-12Q^2+60Q` 被写成 `Q³⁻¹2Q²⁺⁶0Q`（把 `-12`/`+60` 也上标了）；
    #      · 带符号版 `-?[0-9]{1,3}` 仍会把 `x^2-3` 之外的 `x^-1` 当指数（`-1` 未必是符号）。
    #    现在：数字串（不带符号）/ 单个字母 / `{…}` / `*`；其余一律留在基线 ——
    #    KaTeX 没加载时宁可露出 `x^-1` 这样的 TeX 源码，也不许显示错的数学。
    s = re.sub(r"\^\s*(\{[^{}]{1,12}\}|[0-9]+|[A-Za-z]|\*)", _sup, s)
    s = re.sub(r"_\s*(\{[^{}]{1,12}\}|[0-9]{1,3})", _sub, s)

    # 带参数的命令（先长后短：`\underline{\hspace{…}}` 要整套吃掉）
    s = re.sub(r"\\underline\s*\{\s*\\hspace\s*\{[^{}]{0,16}\}\s*\}", "____", s)
    s = re.sub(r"\\(?:text|mathrm|mathbf|mathit|mathsf|operatorname)\s*\{([^{}]{1,60})\}",
               r"\1", s)
    s = re.sub(r"\\hspace\s*\{[^{}]{0,16}\}", " ", s)
    s = re.sub(r"\\underline\s*\{([^{}]{1,60})\}", r"\1", s)
    s = re.sub(r"\\frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}",
               lambda m: f"({m.group(1)})/({m.group(2)})", s)
    s = re.sub(r"\\sqrt\s*\{([^{}]{1,60})\}", lambda m: f"√({m.group(1)})", s)
    for a, b in _TEX_CHAR_CMD.items():
        s = s.replace(a, b)
    s = re.sub(r"\\([A-Za-z]+)", lambda m: _TEX_CMD.get(m.group(1), m.group(0)), s)
    return s


# ---------------------------------------------------------------- 二、数值复算
#: ⚠️ 表达式**不许跨过并列连词**：实测题面「给定 AC(Q)=Q^2-12Q+60 和 MC(Q)=3Q^2-24Q+60，…」
#:    曾把第二个定义一起吞进 AC 的表达式里（`AC` 于是指向 MC 的式子，报出 `AC(4)=12` 的假修正）。
#: ⚠️ 表达式**不许跨过**：并列连词（`和/与/、`）、`$`、汉字、中文标点。
#:    第四轮实测：`求函数 $f(x) = x^{3} - 12x$ 的驻点…` 曾把 `x^{3} - 12x$ 的驻点`
#:    整段当表达式 → 编译失败 → 这一单元"没有 f" → 回落到**另一单元**的 f（§2 变式的
#:    `驻点 x=±2` 被拿 `x³−3x` 去核，报出两条假 ⚠）。
_FUNC_DEF = re.compile(
    r"(?P<name>[A-Za-z][A-Za-z0-9]{0,5})\s*\(\s*(?P<var>[A-Za-z])\s*\)\s*[=＝]\s*"
    r"(?P<expr>[^，,。；;、\n和与$\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]+)")
#: 数字（带不带小数、正负号）。
#: ⚠️ 后面紧跟 `√`/字母/`/`/数字/小数点的不算（`q=10√6` 里的 `10` 不是那个点，
#:    实测：人眼里是 10√6≈24.5，机械读成 10 → 报出 `AC'(10)≈−2.5≠0` 的假修正）。
_NUM = r"-?\d+(?:\.\d+)?(?![0-9.√√A-Za-z/])"
#: 「在 q = 2.89 处取最小」「q=6 时 AC 最小」「最小平均成本对应 q = 6」
_MINMAX = re.compile(
    r"(?:在\s*)?\$?[qQx]\s*\*?\s*[=＝≈]\s*(?P<c>" + _NUM + r")\$?\s*(?:处|时|点)?[^。；;]{0,12}?"
    r"(?P<kind>最小|最大|极小|极大)"
    r"|(?P<kind2>最小|最大|极小|极大)[^。；;]{0,18}?[qQx]\s*\*?\s*[=＝≈]\s*(?P<c2>" + _NUM + r")\$?"
    # 「使 AC 最小的产量约为 2.89」（数字在结论后面，这是实测里最常见的中文语序）
    # 「使 AC 最小的**产量**约为 2.89」——⚠️ 必须有"产量/产出/数量"，否则
    # 「最小平均成本**为 16**」里的 16 是值不是点，会报假修正（实测抓到两例）。
    r"|(?P<kind3>最小|最大|极小|极大)[^。；;]{0,10}?(?:产量|产出|数量)[^。；;]{0,6}?"
    r"(?:约|为|是|=|＝)\s*(?P<c3>" + _NUM + r")")
#: 句子自己写出的二阶导**函数**：`f''(x)=12x-6`。
_D2_DEF = re.compile(r"(?P<name>[A-Za-z][A-Za-z0-9]{0,5})''\s*\(\s*(?P<var>[A-Za-z])\s*\)"
                     r"\s*[=＝]\s*(?P<expr>[^，,。；;\n]{1,40})")
#: 「在 x=-1 处，f''(-1)=-6<0」「f''(1)=6>0，故为极小值点」「AC''(q)=450/q^3」
_D2 = re.compile(r"(?P<name>[A-Za-z][A-Za-z0-9]{0,5})''\s*\(\s*(?P<arg>[^)]{1,12})\)\s*"
                 r"[=＝]\s*(?P<v>-?\d+(?:\.\d+)?)(?![\d./])")
#: ⚠️ `凹凸`/`凸凹` 是**名词**（凹凸性 / concavity），不是方向断言 —— 第五轮实测：
#:    「拐点在 x=0 处，因为 f''(0)=0 且两侧**凹凸**性改变」被读成"说凹 → 该为极大"，
#:    报出一条 `false ⚠ 应为 0`。判据：`凹`/`凸` 后面紧跟另一个凸凹字时不算方向词。
_DIR_WORD = re.compile(r"极大|最大|极小|最小|凹(?!凸)|凸(?!凹)|convex|concave", re.I)
#: 「极小」这一侧的词：f'' 该为正。凸（convex）与极小同侧 —— 实测句子里常写「函数凸，故为极小值」。
_DIR_MIN = {"极小", "最小", "凸"}

#: 「AC(2.89) = 233.01」
#: 「AC(2.89)=233.01」「AC(2.89)=(60·2.89+500)/2.89≈233.01」——
#: ⚠️ 取**这一句里最后一个** `=`/`≈` 后面的数字（等号右边常是整条算式），
#:    并要求它后面是标点/结尾（`233.01*2` 这种不算一个值）。
_SUBST = re.compile(r"(?P<name>[A-Za-z][A-Za-z0-9]{0,5})\s*\(\s*(?P<c>" + _NUM + r")\s*\)"
                    r"(?P<mid>[^。；;\n]*)(?:[=＝≈]|\\approx\s*)(?P<v>" + _NUM + r")\s*\$?\s*$")
#: 小句切分：`,` `，` `。` `；` `、` 与比较符两侧各是一条独立断言
#: （`MC(4)=12 < AC(4)=28` 是两条，不是"MC(4) 等于 28"）。
_CLAUSE = re.compile(r"[，,。；;、<>≤≥与和]")
#: 代入那一档的"密句"记号：`<`/`>` 已经切成小句，不再算密（实测 `MC(4)=12 < AC(4)=28`
#: 两侧各是干净的一条断言，整句跳过反而把它埋了）。
_NOISY_SUB = re.compile(r"→|f''|f′|∵|∴")


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
            if re.search(r"[A-Za-z][A-Za-z0-9]{0,5}\s*\(\s*[A-Za-z]\s*\)\s*[=＝]", expr):
                continue          # 表达式里还嵌着另一个定义 → 这是解析吞多了，别用（见 _FUNC_DEF 注释）
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
    # ⚠️ "点了名"包括**撇号形**（`AC''(q)=64/q³`）与**纯大写缩写**（`AC 的二阶导`）——
    #    漏判会让"本单元只有一个函数"的兜底拿 TC 去核 AC 的极值（第三轮离线复跑抓到），
    #    而单字母小写函数（`f(x)`）仍然允许兜底（那是普遍写法，不会串错）。
    if len(fdefs) == 1:
        named = re.search(r"[A-Za-z][A-Za-z0-9]{0,5}'{0,3}\s*\(", text)
        caps = {x for x in re.findall(r"(?<![A-Za-z])[A-Z]{2,6}(?![A-Za-z])", text)}
        if not named and not (caps - set(fdefs)):
            return next(iter(fdefs))
    return None


#: 一句话里出现这些记号时，机械挑数字必错（实测两例，见 `tests/test_note_math.py`）：
#: `…最小值在q→0+处为无穷大？实际上AC(q)=80+2000/q…`（挑到 80）、
#: `…f''(-1)=-18<0（极大）…`（挑到 18）。**判不了就判不了**，绝不许给出错的"修正值"。
_NOISY = re.compile(r"→|[<>]|f''|f′|∵|∴")

_EXTREMUM = re.compile(r"最小|最大|极小|极大")
#: 「点在前、结论在后」：`驻点为 x=1（极小值）和 x=-1（极大值）`、`x=-1 处为极大值点`、
#: `故 q = 4 为最小值点` —— `_MINMAX` 管的是"结论在前/变量命名"，这一档补另一半。
_PT_EXT = re.compile(r"[qQx]\s*\*?\s*[=＝]\s*(?P<c>" + _NUM + r")\s*(?:处|时|点)?\s*"
                     r"(?:为|是|即)?\s*[（(]?\s*(?P<kind>极小|极大|最小|最大)(?:值点|值)?")
#: 「二阶导数为负 → 极大值」这种**纯符号逻辑**句：不需要函数（只是把给定的符号读一遍）。
_SIGN = re.compile(r"(?:二阶导数|f''|f″)\s*(?:为|是|等于|＝|=)?\s*"
                   r"(?:(?P<v>-?\d+(?:\.\d+)?)|(?P<w>正|负))")
_CONC = re.compile(r"(?P<conc>极大|极小)值?点?")


def _norm_claim(t: str) -> str:
    return re.sub(r"\s+", "", str(t or "")).lower()


def _txt(x) -> str:
    """核对层只吃字符串：`{text, why}` 这种步骤字典取 `text`。

    ⚠️ 实测（2026-10-17 第三轮）：变式的 `steps` 是字典时 `str(dict)` 会把
       `{\'text\': \'二阶导数 -3 < 0……\', \'why\': \'\'}` 整段当成断言贴进核对表。
    """
    if isinstance(x, dict):
        return str(x.get("text") or x.get("claim") or "")
    return str(x or "")


def _units(s: dict) -> list:
    """一节的文本按**单元**切分：讲解 / 例题 / 每道变式 / 补全题各是一个单元。

    返回 `[(kind, [texts]), …]`，`kind` ∈ `prose` / `example` / `variants` / `faded`
    （2026-10-17 加了 kind：`ensure_min_value` 只给讲解/例题补句，题型单元的答案
      自己已经给了结果，再补一句会打架）。

    ⚠️ 单元 = 函数命名空间（2026-10-17 判定实测抓到）：整节共用一个命名空间时，
       "例题里 AVC=… 与变式里 AVC=…"会互相串门 —— 判据会把变式的函数拿去核例题的
       断言，报出一堆**假 mismatch**（实测：`AC'(2.89)≈1.78` 其实是另一道题的 AC'）。
    """
    out = [("prose", [_txt(x) for x in (s.get("prose") or [])])]
    ex = s.get("example") or {}
    if ex.get("prompt") or ex.get("steps"):
        out.append(("example", [_txt(ex.get("prompt")), _txt(ex.get("answer"))]
                    + [_txt(st) for st in (ex.get("steps") or [])]))
    for v in (s.get("variants") or []):
        out.append(("variants", [_txt(v.get("q")), _txt(v.get("a"))]
                    + [_txt(x) for x in (v.get("steps") or [])]))
    fd = s.get("faded") or {}
    if fd.get("q") or fd.get("a"):
        out.append(("faded", [_txt(fd.get("q")), _txt(fd.get("a"))]))
    return [(k, [x for x in u if x]) for k, u in out]


def _kind(word: str) -> str:
    return "最小" if word in ("最小", "极小") else "最大"


def units(s: dict) -> list:
    """`_units` 的**公开别名**（判据与调用方都走它，别各抄一份单元切法）。"""
    return _units(s)


#: 「模型自己出的题」的单元（作者 2026-10-17 第六轮）：这几类内容里的数字断言
#: **不许**进「修正说明」。理由：错误的真正来源是转录/课件里的数（教授口误、
#: ASR 听错、实时摘要抄错），不是模型算错；而拿例题的参数去改变式，实测抓到过
#: 一整套假修正（ECON10770 厨房博弈：变式「价值 5 → 5−3=2」被改回例题的「4−3=1」）。
MODEL_AUTHORED = ("example", "variants", "faded")
#: 自测题也是模型自己出的（答案由它自己写），同一条纪律。
ALSO_MODEL = ("qa",)


def unit_of_claim(section: dict, claim: str) -> str:
    """一条断言出自哪个单元 → `prose` / `example` / `variants` / `faded`（找不到 `""`）。

    ⚠️ 判据是**文本归属**（这条断言的字面出现在哪个单元里），不是"猜"：
       机械核与模型核的行都只有 `claim` 文本，归属只能从它自己的字面回查。
       比对用 `_norm_claim`（去空白、小写），`claim` 被截过 160 字的也认（前缀匹配）。
    """
    key = _norm_claim(claim)
    if not key:
        return ""
    key = key[:60]
    for kind, texts in _units(section):
        for t in texts:
            n = _norm_claim(t)
            if n and (key in n or (len(n) > 20 and n[:60] in key)):
                return kind
    return ""



def check_review(review: dict, *, transcript: str = "") -> list:
    """笔记 JSON → 机械核对行（`{claim, how, status, recomputed}`）。

    status：`ok` / `mismatch`（**正文不改**，进"修正说明"）/ `unchecked`（判不了）。
    """
    rows: list = []
    for s in (review.get("sections") or []):
        unit_list = _units(s)
        units = [u for _k, u in unit_list]
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
            fdefs_by_text: list = []
            for t in u:
                fdefs = _scope(t, u_idx)
                fdefs_by_text.append(fdefs)
                rows += _rows_for_text(t, fdefs)
            rows += _qvsp_rows(u, fdefs_by_text)      # ⭐ F9：q 不许当 P（整单元一起看）
    # ⚠️ 同一句断言在 `prose` 与 `answer` 各出现一次 → 会出两行一模一样的核对表；
    #    空的 claim 也在这里丢掉（实测第三份产物上有两行全空）。
    seen: set = set()
    uniq: list = []
    for r in rows + _source_rows(review, transcript):
        key = (_norm_claim(r.get("claim")), str(r.get("recomputed") or ""),
               str(r.get("status") or ""))
        if not key[0] or key in seen:
            continue
        seen.add(key)
        uniq.append(r)
    return uniq


#: 除法口播：`5.3 除以 3 得到 2.89`（`÷`、`/` 都算）。
#: ⚠️ 第六轮补：产物 I 的 §1 里那句错除法（`5.3÷3=2.89`）三道核**一句没拦** ——
#:    原来只有极值/代入/二阶导/符号这几档，没有除法。而它正是 living 摘要里
#:    `2.89` 的来处（纲要那条的账本就指望它）。
#: ⚠️ 2026-10-18 Δ（作者看截图抓的）：**这一档的起因句本身还是没拦住** —— 真笔记里写的是
#:    「5.3除以3**，得到约**2.89」，比当初的紧版多了**一个逗号 + 一个"约"**，正则两项都不认。
#:    现在放行 `除数 后可选逗号` 与 `动词后可选"约"`；容差逻辑不变（按结果写的小数位判）。
_DIV = re.compile(r"(?P<a>-?\d+(?:\.\d+)?)\s*(?:除以|÷|/)\s*(?P<b>-?\d+(?:\.\d+)?)"
                  r"\s*[，,、]?\s*(?:得到|得|等于|是|为|约等于|≈|=|＝)\s*[约]?\s*"
                  r"(?P<c>-?\d+(?:\.\d+)?)"
                  # ⭐ F9：链式分数 `500/60=25/3`（作者读转录重建的式子）不是"500/60=25"。
                  r"(?:\s*/\s*(?P<d>-?\d+(?:\.\d+)?))?")


#: 极限当最值（F8-c，作者从 econ W3 抓到）：`当 q→∞ 时 AC→100，最低点为 100` 必须拦 ——
#: 无穷远处的趋向值**不是**最值点（有限产量处都到不了它），学生照抄就会答错整类题。
_LIMIT_MARK = re.compile(r"→\s*∞|趋于\s*无穷|趋向\s*无穷|趋于\s*∞|取极限|无限趋近|无限接近")
_LIMIT_EXT = re.compile(
    r"(最低|最大|最小|极小|极大)[值点]{0,4}\s*(?:为|是|＝|=)\s*\$?-?\d"
    r"|(?:停产价|停业价|关门价)[^，。；;]{0,6}(?:为|是|＝|=)\s*\$?-?\d")


def _limit_extremum_row(t: str) -> dict | None:
    """同一句里同时出现「→∞ / 取极限」与「某个数就是最值/停产价」→ 一条 mismatch 行。

    ⚠️ 文本里是 TeX（`$q\\to\\infty$`）就先把 `\\to`/`\\infty` 归一成 `→`/`∞` 再判 ——
       实测 econ W3 的原文正是 TeX 写法，只按 `→∞` 认会整条漏掉。
    """
    s = str(t or "")
    if "\\to" in s or "\\infty" in s:                           # TeX 写法归一化
        s = (s.replace("\\rightarrow", "→").replace("\\to", "→")
             .replace("\\infty", "∞"))
    for sent in re.split(r"[。；;！？\n]", s):
        if _LIMIT_MARK.search(sent) and _LIMIT_EXT.search(sent):
            return {"claim": str(t), "status": "mismatch",
                    "recomputed": "最值不能由 q→∞ 的极限得到",
                    "how": "极值题必须真的有最值点（求导=0 且二阶条件）；"
                           "q→∞ 的趋向值不是最小值"}
    return None


def _rows_for_text(t: str, fdefs: dict) -> list:
    """一条文本里的极值/代入/二阶导/除法断言 → 核对行。"""
    rows: list = []
    lim = _limit_extremum_row(t)
    if lim is not None:
        rows.append(lim)
    for m in _DIV.finditer(t):
        try:
            a, b, c = float(m.group("a")), float(m.group("b")), float(m.group("c"))
        except ValueError:
            continue
        if not b:
            continue
        want = a / b
        d_s = m.group("d")
        if d_s:                                     # ⭐ F9：链式分数 `500/60=25/3`
            d = float(d_s)
            if not d:
                continue
            claim_v = c / d
            ok = abs(claim_v - want) <= 1e-3 * max(1.0, abs(claim_v))
            rows.append({"claim": t, "status": "ok" if ok else "mismatch",
                         "recomputed": f"{a:g}/{b:g} ≈ {_fmt(want)}",
                         "how": f"{a:g}÷{b:g}={_fmt(want)}，断言写 {c:g}/{d:g}"
                                + ("（一致）" if ok else "（对不上）")})
            continue
        # 容差按断言写的小数位算（`约等于 3.3` 不许判成"错"）
        dec = len(m.group("c").split(".")[1]) if "." in m.group("c") else 0
        if abs(round(want, dec) - c) <= 1e-9:
            rows.append({"claim": t, "status": "ok", "recomputed": f"{a:g}/{b:g} ≈ {_fmt(want)}",
                         "how": f"{a:g}÷{b:g} = {c:g}（一致）"})
        else:
            rows.append({"claim": t, "status": "mismatch",
                         "recomputed": f"{a:g}/{b:g} ≈ {_fmt(want)}",
                         "how": f"{a:g}÷{b:g}={_fmt(want)}，不是{c:g}"})
    # ⚠️ 二阶导断言走**独立一档**：它的形状很紧（`f''(c)=v`），不受噪声句守卫影响 ——
    #    实测未核对行里一半是「f''(-1)=-6<0，故为极大值」这种，机械核完全能做。
    # ⚠️ 优先用**句子自己写的** `f''(x)=12x-6`（同一个单元里可能有两个不同的 `f`：
    #    实测一份产物里「f(x)=x³−3x²−12x+1」与「f''(x)=12x−6」并存，拿前者的二阶导
    #    去比后者给出的值 → 报出假的"不一致"）。
    own = {}
    for m in _D2_DEF.finditer(t):
        fn2 = _compile(m.group("expr"), m.group("var"), {})
        if fn2 is not None:
            own[m.group("name")] = fn2
    for m in _D2.finditer(t):
        name, arg, v = m.group("name"), m.group("arg"), float(m.group("v"))
        if name not in fdefs and name not in own:
            continue
        arg = arg.strip()
        if not re.fullmatch(r"-?\d+(?:\.\d+)?", arg):
            continue              # `AC''(q)=…` 是**函数**不是某点上的值 → 这一档核不了
        x = float(arg)
        try:
            if name in own:
                # 句子自己写的 `f''(x)=12x-6` **就是二阶导函数**：代进去求值。
                # （再对它做数值二阶导只会得到 0 —— 线性函数的二阶导，实测踩过。）
                want = own[name](x)
            else:
                want = _d2(fdefs[name][2], x)
        except Exception:                                     # noqa: BLE001
            continue
        ok_val = abs(want - v) <= max(1e-2, 1e-3 * abs(v))
        # ⚠️ 结论词要**贴着这一条断言**找：同一句里可能有两条（`f''(-1)…极大，f''(2)…极小`），
        #    在整句里 search 会把第一条的"极大"配到第二条上（实测报出假的"方向矛盾"）。
        tail = t[m.end():m.end() + 20]
        word = _DIR_WORD.search(tail) or _DIR_WORD.search(t[max(0, m.start() - 12):m.start()])
        ok_dir = True
        # ⚠️ `f''` 本身就是 0 时**没有方向可言**（驻点/拐点/鞍点）：再拿「凹/极大」去判
        #    必然假报（判据：`f''(0)=0 且两侧凹凸性改变` 是拐点的**定义**）。
        if abs(want) <= max(1e-2, 1e-3 * abs(v)):
            word = None
        if word:
            says_min = word.group(0).lower() in _DIR_MIN
            ok_dir = (says_min and want > 0) or (not says_min and want < 0)
        if ok_val and ok_dir:
            note2 = ("；二阶导≈0，不判极大极小（拐点）" if word is None and abs(want) <= 1e-2 else "")
            rows.append({"claim": t, "status": "ok", "recomputed": f"{name}''({x})≈{_fmt(want)}",
                         "how": f"数值二阶导：{name}''({x})≈{_fmt(want)}（与断言一致）{note2}"})
        else:
            why = (f"数值二阶导 {name}''({x})≈{_fmt(want)}，断言写的是 {_fmt(v)}"
                   if not ok_val else (f"二阶导为正/负与「{word.group(0)}」矛盾" if word
                                       else "二阶导≈0：这一点没有极大/极小而言（拐点）"))
            rows.append({"claim": t, "status": "mismatch",
                         "recomputed": f"{name}''({x})≈{_fmt(want)}", "how": why})
    # ⚠️ 代入那一档**自己切小句**（逗号/比较符都切），所以**先于**整句的密句守卫跑，而且
    #    不能"有代入结果就不看极值"：实测 `MC(4)=12 < AC(4)=28` 两侧各是干净的一条
    #    （第三轮离线复跑），而「令导数为 0 得 q=6 处最小，AC(6)=24」两句都要核。
    rows += _SUBST_rows(t, fdefs, [])
    # ⭐ 纯符号逻辑（第三轮离线复跑：**「二阶导数 -3 < 0 …该点为极大值点」这种 6 行）**：
    #    句子自己给了符号，结论极大/极小只需读一遍符号 —— 不需要函数。
    sign_rows = _sign_rows(t)
    rows += sign_rows
    # ⚠️ 「点在前、结论在后」那一档（`x=1（极小值）`）**也不受整句密句守卫管**：实测
    #    `驻点为 x=1（极小值）…函数在 x>0 时凸` 里的 `>` 是**行文里的比较号**，
    #    跟"公式密到挑不出结论"不是一回事 —— 整句跳过把它埋了（第三轮离线复跑）。
    # ⭐ F9：`q\approx2.89`（TeX）== `q≈2.89` —— 不归一化，舍入驻点的整条路都匹配不上。
    tm = t.replace("\\approx", "≈")
    for m in _PT_EXT.finditer(tm):
        if sign_rows:
            continue          # 这句的结论已经由符号逻辑那一档核过（避免一句两行、一行还挂着"没有函数"）
        rows.append(_extremum_row(t, fdefs, m.group("c"), m.group("kind")))
    if _NOISY.search(t):
        # ⚠️ 公式密到分不清哪句话是"结论"时：**不猜** —— 标未核对，让人来看。
        #    实测猜过两次，两次都猜错（挑到 80 / 挑到 18），而假的"已修正"比漏核坏得多。
        if not rows and _EXTREMUM.search(t):
            rows.append({"claim": t, "status": "unchecked", "recomputed": "",
                         "how": "这句话里公式/推导太密，机械核挑不出唯一的那条结论（请人工复查）"})
        return rows
    pairs = [(m.group("c") or m.group("c2") or m.group("c3"),
              m.group("kind") or m.group("kind2") or m.group("kind3"))
             for m in _MINMAX.finditer(tm)]
    for c, word in pairs:
        if c is None:
            continue
        rows.append(_extremum_row(t, fdefs, c, word))
    return rows


def _sign_rows(t: str) -> list:
    """「二阶导数 = -3 < 0，因此该点为极大值点」→ 一条纯逻辑核对行。

    ⚠️ 只在**同一句**里读符号：跨句的符号推理（题面给事实、答句给结论）不做 ——
       那需要把整节的事实表建起来，风险比收益大（实测留下的未核对行里最长的就是这一类）。
    """
    rows: list = []
    if re.search(r"判断|还是|？|\?", t):
        return rows              # 提问句没有结论可核（`判断 x=2 是极大还是极小？`）
    for m in _SIGN.finditer(t):
        v, w = m.group("v"), m.group("w")
        if v is not None:
            sign = 1 if float(v) > 0 else -1
            shown = f"{v}"
        elif w is not None:
            sign = 1 if w == "正" else -1
            shown = w
        else:
            continue
        # ⚠️ 结论词要**跟着这条符号走**：一句话里可以有两条互不相干的符号+结论
        #    （「…二阶导数为负，该点是极大…；…二阶导数为正，该点是极小…」），
        #    整句取第一个结论会把第二条配错（第三轮离线复跑抓到 3 行假 ⚠）。
        tail = t[m.end():m.end() + 40]
        tail = re.split(r"[。；;]", tail)[0]
        c = _CONC.search(tail)
        if not c:
            continue
        says_min = _kind(c.group("conc")) == "最小"
        ok = (sign > 0) == says_min
        rows.append({"claim": t, "status": "ok" if ok else "mismatch",
                     "recomputed": f"二阶导符号：{'正' if sign > 0 else '负'}",
                     "how": (f"符号逻辑：二阶导数{shown}"
                             + ("（正）→ 凸 → 极小值" if sign > 0 else "（负）→ 凹 → 极大值")
                             + ("" if ok else f"；但句子里写的是「{c.group('conc')}」"))})
    return rows


def _extremum_row(t: str, fdefs: dict, c: str, word: str) -> dict:
    """「在 q=c 处取最小/最大」的一条核对行。判不了 → `unchecked`（不许猜）。"""
    kind = _kind(word)
    name = _pick(fdefs, t)
    if name is None:
        why = ("没有给出函数，无法复算极值（不许猜）")
        if re.search(r"二阶导数|f''|f″|一阶导数", t):
            why = "只给了导数的符号、没有函数：跨句的符号推理不机械核（请人工看一眼）"
        return {"claim": t, "status": "unchecked", "recomputed": "", "how": why}
    _var, _expr, f = fdefs[name]
    x = float(c)
    try:
        d1, d2 = _d1(f, x), _d2(f, x)
    except Exception:                                         # noqa: BLE001
        return {"claim": t, "status": "unchecked", "recomputed": "",
                "how": f"{name} 在 q={c} 附近算不出来（定义域外/数值问题）"}
    tol = 1e-3 * max(1.0, abs(x))
    flip = False
    if abs(d1) > tol:
        # ⭐ F9（作者读转录后重建：课上 q*≈2.89，真值 √(25/3)=2.8867…）：**舍入过的驻点**
        #    不许报假 mismatch。判据：±1% 邻域里导数**变号** = 驻点就在舍入点旁边。
        #    （实测：60q+500/q 在 q=2.89 的导数≈0.135≠0，但 2.86→2.92 之间变号。）
        h = 0.01 * max(1.0, abs(x))
        try:
            flip = _d1(f, x - h) * _d1(f, x + h) < 0
        except Exception:                                     # noqa: BLE001
            flip = False
        if not flip:
            return {"claim": t, "status": "mismatch",
                    "recomputed": f"{name}'({c})≈{_fmt(d1)}",
                    "how": f"数值求导：{name}'({c})≈{_fmt(d1)}≠0，"
                           f"该点不是驻点，谈不上{word}"}
    bad = (kind == "最小" and d2 < 0) or (kind == "最大" and d2 > 0)
    if bad:
        return {"claim": t, "status": "mismatch",
                "recomputed": f"{name}''({c})≈{_fmt(d2)}",
                "how": f"二阶条件不成立：{name}''({c})≈{_fmt(d2)}，"
                       f"这是{'最大' if d2 < 0 else '最小'}值"}
    gotline = (f"数值求导：{name}'({c})≈{_fmt(d1)}"
               + ("（x 有舍入：±1% 邻域内导数变号 → 驻点成立）" if flip
                  else "（≈0，驻点成立）"))
    return {"claim": t, "status": "ok",
            "recomputed": f"{name}({c})={_fmt(f(x))}",
            "how": gotline + f"、{name}''({c})≈{_fmt(d2)}（{word}的二阶条件成立）"}


def _SUBST_rows(t: str, fdefs: dict, rows: list) -> list:
    """「NAME(c)=v」代入复算（极值那一档的两条路都会用到）。

    ⚠️ 实测两个跨句吃数的假修正（2026-10-17 第三轮离线复跑抓到）：
       · `所以 AC(4)=28，MC(4)=12。` → 曾经读成「AC(4)=12」；
       · `MC(4)=12 < AC(4)=28，所以…` → 曾经读成「MC(4)=28」。
       判据：先按小句切（逗号/句号/比较符都切），小句内取**最后一个** `=`，
       并禁止小句里再出现另一个「名字(数字」。
    """
    if _NOISY_SUB.search(t):
        return rows
    for cl in _CLAUSE.split(t):
        for m in _SUBST.finditer(cl.strip()):
            if re.search(r"[A-Za-z][A-Za-z0-9]{0,5}\s*\(\s*-?\d", m.group("mid")):
                continue          # 小句里还有另一条 `NAME(数字` → 不猜，交给它自己的小句
            # ⚠️ 这里**按名字**取（大小写不敏感），不走 `_pick`：`AC(6)=24` 点名了 AC，
            #    本单元只定义了 TC 时**不许**拿 TC 代为计算（判据实测抓到过 144 这种假数）。
            _n = m.group("name")
            name = next((n for n in fdefs if n.lower() == _n.lower()), None)
            if name is None:
                continue
            x, v = float(m.group("c")), float(m.group("v"))
            try:
                got = fdefs[name][2](x)
            except Exception:                                 # noqa: BLE001
                rows.append({"claim": t, "status": "unchecked", "recomputed": "",
                             "how": f"{name}({x}) 算不出来"})
                continue
            ok = abs(got - v) <= 1e-3 * max(1.0, abs(v))
            rows.append({"claim": t, "status": "ok" if ok else "mismatch",
                         "recomputed": f"{_fmt(got)}",
                         "how": f"代入复算：{name}({_fmt(x)})={_fmt(got)}"
                                + ("（与断言一致）" if ok else f"，断言写的是 {_fmt(v)}")})
    return rows


#: F9（作者读转录后定的新口径）：**q 不许当 P 用** ——「2.89 是产量，不是价格」。
_QV_MIN = re.compile(r"最低|最小|极大|极小|停产|停业|关门")
_QV_ARG = re.compile(r"[qQ]\s*(?:[=≈]|\\approx\s*)\s*(?P<x>\d+(?:\.\d+)?)")
_QV_PRICE = re.compile(
    r"(?:停产|停业|关门)[^。；\n]{0,12}?价[^。；\n]{0,10}?(?:约\s*为?|为|是|＝|=|≈|\\approx\s*)\s*\$?(?P<p>\d+(?:\.\d+)?)"
    r"|(?:^|[；;。：:\s])P\s*(?:约\s*为?|为|是|＝|=|≈|\\approx\s*)\s*\$?(?P<p2>\d+(?:\.\d+)?)")


def _qvsp_rows(unit_texts: list, fdefs_by_text: list) -> list:
    """F9：极小值/停产价格题里「q 当 P」的两条守卫（整单元一起看）。

    ① 代入值 ≈ 代入点（`AC(2.89)≈2.89`），而这一节没有函数能复算 → mismatch；
    ② 写着最低/停产价格、却没有函数、还端着一个算好的价格数 → mismatch
       （⬜「没有给出函数，无法复算极值」那条的升级：要么给函数，要么只留片段）；
       有函数时：价格数 ≈ 驻点值 且函数复算对不上 → mismatch（P 必须拿 AC(q*) 算）。
    """
    blob = " ".join(str(x or "") for x in unit_texts)
    if not blob or not _QV_MIN.search(blob):
        return []
    xs = [float(m.group("x")) for m in _QV_ARG.finditer(blob)]
    rows: list = []
    # ① 代入值 ≈ 代入点
    for t, fd in zip(unit_texts, fdefs_by_text or [{}] * len(unit_texts)):
        for cl in _CLAUSE.split(str(t or "")):
            for m in _SUBST.finditer(cl.strip()):
                name = next((n for n in (fd or {}) if n.lower() == m.group("name").lower()), None)
                x, v = float(m.group("c")), float(m.group("v"))
                if name is None and abs(v - x) <= 0.02 * max(1.0, abs(x)):
                    rows.append({"claim": str(t), "status": "mismatch",
                                 "recomputed": f"{m.group('name')}({_fmt(x)})：答案值≈取点",
                                 "how": f"答案值 {_fmt(v)} 恰好等于代入点 {_fmt(x)}，而这里"
                                        "没有能复算的函数 —— 像是把产量 q 当成了价格 P"
                                        "（F9：给出函数，或只留片段）"})
    # ② 价格/答案那一档
    name, fn = None, None
    for fd in (fdefs_by_text or []):
        name = _pick(fd or {}, blob)
        if name:
            fn = (fd or {})[name][2]
            break
    for t in unit_texts:
        for m in _QV_PRICE.finditer(str(t or "")):
            p = m.group("p") or m.group("p2")
            if not p:
                continue
            pv = float(p)
            if name is None:
                rows.append({"claim": str(t), "status": "mismatch",
                             "recomputed": f"价格/答案 {_fmt(pv)}",
                             "how": "这一节写着最低/停产价格，却没有给出函数（无法复算）—— "
                                    "没有函数的例子不许端着一个算好的数"
                                    "（F9：给出函数，或只留片段）"})
                continue
            if not any(abs(pv - x) <= 0.02 * max(1.0, abs(x)) for x in xs):
                continue
            try:
                verified = any(abs(fn(x) - pv) <= 0.02 * max(1.0, abs(pv)) for x in xs)
            except Exception:                                 # noqa: BLE001
                verified = False
            if not verified:
                rows.append({"claim": str(t), "status": "mismatch",
                             "recomputed": f"价格/答案 {_fmt(pv)} ≈ 驻点值",
                             "how": f"答案值 {_fmt(pv)} 与驻点值相同，而 {name} 在驻点算出来的"
                                    "不是这个数 —— P 必须拿 AC(q*) 算（F9：q 不是 P）"})
    return rows


def _source_rows(review: dict, transcript: str = "") -> list:
    """例题来源：题面自称"假设" / 给了函数，而标题没有 📖 出处 —— "自编例题"的形状。

    ⚠️ 第四轮：`🎙` 的例题**可以**从转录自证 —— 题面里的数字如果在转录里出现过
       （课上确实给了这些数），就不再挂"自编例题是硬禁区"的 ⚠，改记一条 ✅；
       找不到才保持未核对（作者第 2 条：找不到出处就写「课上只给了片段」，不许装）。
    """
    rows: list = []
    t_numbers = set(re.findall(r"\d+(?:\.\d+)?", str(transcript or "")))
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
        if not (confession or defined):
            continue
        # 🎙 自证：题面里的数字在转录里出现过 → 这是**课上真的给过**的题面
        p_numbers = set(re.findall(r"\d+(?:\.\d+)?", prompt))
        if (not confession and "🎙" in title and p_numbers
                and p_numbers <= t_numbers and t_numbers):
            rows.append({"claim": f"例题：{title or prompt[:40]}", "status": "ok", "recomputed": "",
                         "how": f"题面里的数字（{'、'.join(sorted(p_numbers))}）在转录里都出现过"
                                " —— 🎙 例子成立"})
            continue
        rows.append({"claim": f"例题：{title or prompt[:40]}", "status": "unchecked",
                     "recomputed": "",
                     "how": "例题标题没有 📖 出处，题面却给了函数"
                            + ("/自称「假设」" if confession else "")
                            + " —— 课上的例子要能在转录里对上数字；对不上就按「课上只给了片段」写"})
    return rows


def merge_checks(det: list, llm: list) -> list:
    """机械核与模型核合并：**机械核优先**（模型会对着错前提"自洽"）。

    同一条断言（去空白、小写后相同）时，`det` 覆盖 `llm`；其余按 `det` 在前。
    """
    det = det or []
    seen = {_norm_claim(r.get("claim")) for r in det}
    return list(det) + [r for r in (llm or []) if _norm_claim(r.get("claim")) not in seen]
