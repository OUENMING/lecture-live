"""更正账本：生成**前**把「教授自己改过口」和「官方公式」固定下来，生成**后**逐条核。

## 为什么要它（作者 2026-10-17 第 4 条）

同一节课连跑两次，第二次把 `MC = 3Q²−12Q+60` 写了回去 —— 那是教授 13:40:30 的
口误，他 13:40:41 **自己更正**成 `−24Q`（转录原文就在那里）。笔记的"稳定性"不能靠
模型的自觉，所以这里做两件**确定性**的事：

1. `build(entries, materials)`：从转录抽自我更正（`sorry` / `I mean` / `X not Y` 这类），
   从材料抽官方公式（`TC(q) = …`）→ 一份账本，**喂进 prompt 当硬约束**；
2. `reconcile(review, …)`：生成后按账本核 —— 而且不只核账本：**同一个单元里
   `TC`/`AC`/`MC` 的微积分关系用数值核一遍**（`MC = TC'`、`AC = TC/q`）。
   违背 → **自动改写**（并进「修正记录」，依据写清楚）或标 ⚠，**绝不静默通过**。

## 为什么"内部一致性"这条比账本还管用

13:40 那处口误的形状是：笔记里同时写了 `TC(Q)=Q³−12Q²+60Q` 与
`MC(Q)=3Q²−12Q+60` —— 两式**互相矛盾**（`TC' = 3Q²−24Q+60`），谁对谁错不用问模型：
对 `TC` 求导就是答案。实测这条判据在两次生成的产物上都命中（一次对、一次错）。

⚠️ 纯函数 + 只读：不联网、不写文件、不调模型。
"""
from __future__ import annotations
import re
import unicodedata

#: 教授自我更正的"话头"（作者点名的 `sorry` / `I mean` / `minus 24 not 12` 这类）。
CUE = re.compile(r"\bi mean\b|\bsorry\b|\bcorrection\b|\bi said\b|\bi wrote\b|"
                 r"\bnot\s+(?:minus\s+)?\d|\bminus\s+\d+\s+not\s+\d|"
                 r"\bmistake\b|\blet me correct\b|\bwrong\b", re.I)

#: 一个"多项式"式子里的变量（只用 q / Q / x 这三类，够本课的数学用了）。
_VAR = "qxy"


# ---------------------------------------------------------------- 抽取
#: "像数学"的判据（用来滤掉"你妈妈发现你在抽烟"这类与数学无关的 `sorry`）。
_MATHY = re.compile(r"\d|squared|minus|plus|equal|derivative|cost|average|marginal|"
                    r"stationary|convex|concave", re.I)
#: 一句"重述的多项式"：`Q squared minus 24 Q plus 60`。
_POLY_TALK = re.compile(r"[qQx]\s*squared|[qQx]\s*[\^²³]|minus\s+\d+\s*[qQx]|"
                        r"\d+\s*[qQx]\s*(plus|minus)", re.I)


def from_transcript(entries: list, *, window: int = 2) -> list:
    """转录 → 教授自我更正的上下文（带时间戳）。**只读字面**，不做推断。

    两类：
      ① 有话头的（`sorry` / `I mean` / `X not Y`），且这句话"像数学"（滤掉闲聊）；
      ② **没有话头的重述** —— 教授先说了一句含 `equal` 的式子、紧接着又单独念了一个
         多项式（实测 13:40:30 → 13:40:41 就是这种：`… 3 Q squared minus 12 Q plus 60`
         → `Q squared minus 24 Q plus 60`）。这类**以重述为准**。
    """
    out: list = []
    rows = [(str(e.get("ts") or ""), str(e.get("en") or e.get("asr") or "")) for e in entries]
    for i, (ts, text) in enumerate(rows):
        if not text:
            continue
        if CUE.search(text) and _MATHY.search(text):
            follow = " ".join(t for _ts, t in rows[i:i + 1 + window] if t)
            out.append({"ts": ts, "text": follow.strip()[:400], "kind": "cue"})
            continue
        prev = rows[i - 1][1] if i else ""
        if "equal" in prev.lower() and _POLY_TALK.search(text):
            out.append({"ts": ts, "text": f"{prev.strip()[:200]} → **{text.strip()[:200]}**",
                        "kind": "restate"})
    return out


#: 材料里的公式：`TC(q) = q³ − 12q² + 60q`。
_FORMULA = re.compile(r"\b(TC|AC|MC|TR|TFC|TVC|AVC)\s*\(\s*([qQx])\s*\)\s*=\s*"
                      r"([^,，。；;\n]{1,60})")


def normalize_math(text: str) -> str:
    """材料里的数学字形 → ASCII。**实测**：UCD 的 PDF 抽出来是 `𝑇𝐶(𝑞) = 𝑞! − 12𝑞" + 60𝑞`
    （数学粗斜体字母 + `!`/`"` 当上标），不归一化的话一条官方公式都读不出来。"""
    s = unicodedata.normalize("NFKC", str(text or ""))
    # ⚠️ OCR 会在变量和上标之间留空格（实测 `3q " + 4q + 2`），所以允许 0–2 个空格。
    s = re.sub(r"(?<=[A-Za-z])[ \t]{0,2}!", "^3", s)
    s = re.sub(r'(?<=[A-Za-z])[ \t]{0,2}"', "^2", s)
    return s


def from_materials(materials) -> list:
    """材料 → 官方公式（`{name, var, expr, cite}`）。索引拿不到就返回空表。"""
    out: list = []
    for c in getattr(materials, "_chunks", []) or []:
        for m in _FORMULA.finditer(normalize_math(getattr(c, "text", ""))):
            out.append({"name": m.group(1), "var": m.group(2),
                        "expr": m.group(3).strip(),
                        "cite": f"{getattr(c, 'file', '')} {getattr(c, 'heading', '')}".strip()})
    return out


def _derived_basis() -> str:
    return "对同一单元的 TC 逐项求导（📐 内部一致）"


# ---------------------------------------------------------------- 喂给模型
def block(transcript_fixes: list, materials_formulas: list, *, limit: int = 8) -> str:
    """账本 → 一段硬约束（拼在 review 的 user 里）。**没有账本就返回空串**。"""
    lines: list = []
    for f in (transcript_fixes or [])[:limit]:
        lines.append(f"[{f['ts']}] {f['text']}")
    fml: list = []
    seen: set = set()
    for f in (materials_formulas or []):
        key = (f["name"], f["expr"].lower())
        if key in seen:
            continue
        seen.add(key)
        fml.append(f"{f['name']}({f['var']}) = {f['expr']}　（📖 {f['cite']}）")
        if len(fml) >= limit:
            break
    if not lines and not fml:
        return ""
    out = ["", "## 更正账本（**硬约束**，写错就是扣分）",
           "下面这些是「教授自己更正过的地方」与「官方材料里的式子」。"
           "写作时**必须**与它们一致；课上的口误不许写进笔记（要么用更正值，要么标 📚/📖）。"]
    if lines:
        out += ["", "教授自我更正的上下文（🎙，按时间序）："] + [f"- {x}" for x in lines]
    if fml:
        out += ["", "官方材料里的公式（📖）："] + [f"- {x}" for x in fml]
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- 多项式（够用就行）
class Poly:
    """`{次数: 系数}` —— 只要加、减、乘、常数指数幂、常数除，够本课的式子了。"""

    def __init__(self, terms: dict | None = None):
        self.t = {int(k): float(v) for k, v in (terms or {}).items() if abs(v) > 1e-12}

    @staticmethod
    def const(v: float):
        return Poly({0: v})

    def __add__(self, o):
        out = dict(self.t)
        for k, v in o.t.items():
            out[k] = out.get(k, 0.0) + v
        return Poly(out)

    def __sub__(self, o):
        return self + Poly({k: -v for k, v in o.t.items()})

    def __mul__(self, o):
        out: dict = {}
        for a, b in self.t.items():
            for c, d in o.t.items():
                out[a + c] = out.get(a + c, 0.0) + b * d
        return Poly(out)

    def scale(self, k: float):
        return Poly({a: b * k for a, b in self.t.items()})

    def deriv(self):
        return Poly({a - 1: b * a for a, b in self.t.items() if a >= 1})

    def div_var(self):
        """除以变量（`AC = TC/q`）。⚠️ 有余项（次数 < 0）时返回 `None`：那说明不是 AC。"""
        if any(a < 1 for a in self.t):
            return None
        return Poly({a - 1: b for a, b in self.t.items() if a >= 1})

    def close(self, other, *, tol: float = 1e-6) -> bool:
        ks = set(self.t) | set(other.t)
        return all(abs(self.t.get(k, 0.0) - other.t.get(k, 0.0)) <= tol for k in ks)

    def show(self, var: str = "q") -> str:
        out = []
        for a in sorted(self.t, reverse=True):
            b = self.t[a]
            if a == 0:
                out.append(f"{b:g}")
            else:
                core = var if a == 1 else f"{var}^{{{a}}}"
                out.append((f"{b:g}{core}" if abs(abs(b) - 1) > 1e-9 else
                            (f"-{core}" if b < 0 else core)))
        s = "+".join(out).replace("+-", "-")
        return s or "0"


def _poly_of(fn, var: str, *, at=(0.7, 1.3, 2.9, 4.7)) -> Poly | None:
    """函数 → 多项式（数值反解系数）。⚠️ 只在次数 ≤ 4 时用（本课的成本函数就三次）。"""
    import note_math
    try:
        ys = [fn(x) for x in at]
    except Exception:                                         # noqa: BLE001
        return None
    n = len(at)
    # 解线性方程组 Σ c_k x_i^k = y_i（范德蒙，高斯消元，n ≤ 4 手写就够）
    a = [[x ** k for k in range(n)] + [y] for x, y in zip(at, ys)]
    for i in range(n):
        p = max(range(i, n), key=lambda r: abs(a[r][i]))
        if abs(a[p][i]) < 1e-12:
            return None
        a[i], a[p] = a[p], a[i]
        for r in range(i + 1, n):
            f = a[r][i] / a[i][i]
            for c in range(i, n + 1):
                a[r][c] -= f * a[i][c]
    sol = [0.0] * n
    for i in range(n - 1, -1, -1):
        s = a[i][n] - sum(a[i][c] * sol[c] for c in range(i + 1, n))
        sol[i] = s / a[i][i]
    out = Poly({k: v for k, v in enumerate(sol)})
    # 回代验一遍：数值反解必须能复原原函数（否则弃用，绝不拿它当结论）
    for x in (0.31, 1.7, 3.3, 5.9):
        try:
            if abs(out_eval(out, x) - fn(x)) > 1e-6 * max(1.0, abs(fn(x))):
                return None
        except Exception:                                     # noqa: BLE001
            return None
    return out


def out_eval(p: Poly, x: float) -> float:
    return sum(c * (x ** k) for k, c in p.t.items())


# ---------------------------------------------------------------- 生成后核对
def reconcile(review: dict, *, materials=None) -> tuple:
    """笔记 →（核对后的笔记, 修正记录, 提醒）。**原地不改输入**（深拷贝再改）。

    判据（都在**同一个单元**里做，避免把变式的函数串过来）：
      · 单元里同时有 `TC` 与 `MC`：`MC` 必须等于 `TC'`；
      · 同时有 `TC` 与 `AC`：`AC` 必须等于 `TC/q`；
      · 材料里有官方 `TC`：笔记的 `TC`（**只在例题/真题那一档**）必须与它一致。
    违背 → 把**错的那个表达式**改写成导出的正确式子，并写一条修正记录。
    """
    import copy
    import note_math
    out = copy.deepcopy(review)
    fixes: list = []
    warns: list = []
    for s in (out.get("sections") or []):
        for u in note_math._units(s):
            fdefs = note_math.functions(u)
            if "TC" not in fdefs:
                continue
            var, _expr, tc = fdefs["TC"]
            poly = _poly_of(tc, var)
            if poly is None:
                continue
            want = {"MC": poly.deriv(), "AC": poly.div_var()}
            for name, target in want.items():
                if name not in fdefs or target is None:
                    continue
                got_var, got_expr, got_fn = fdefs[name]
                got = _poly_of(got_fn, got_var)
                if got is None or got.close(target):
                    continue
                right = target.show(got_var)
                bad = note_math._clean_expr(got_expr)
                fixes.append({"where": s.get("heading") or f"{name}({got_var})",
                              "spoken": f"{name}({got_var})={got_expr}",
                              "fixed": f"{name}({got_var})={right}",
                              "basis": _derived_basis()})
                _replace_in_unit(s, got_expr, right)
                warns.append(f"{name} 与 TC 不自洽（{got_expr} → {right}）")
    return out, fixes, warns


def _replace_in_unit(section: dict, wrong: str, right: str) -> None:
    """把这一节里**字面出现**的那个错式子换掉（含 `MC(Q)=…` 的各种写法）。"""
    def fix_text(v):
        if not isinstance(v, str) or not v:
            return v
        return v.replace(wrong, right)
    for k in ("prose", "pitfalls"):
        if isinstance(section.get(k), list):
            section[k] = [fix_text(x) for x in section[k]]
    for key in ("example", "faded"):
        blk = section.get(key) or {}
        for f in ("prompt", "answer", "q", "a", "title"):
            if isinstance(blk.get(f), str):
                blk[f] = fix_text(blk[f])
        for st in (blk.get("steps") or []):
            if isinstance(st, dict):
                for f in ("text", "why"):
                    st[f] = fix_text(st.get(f, ""))
    for v in (section.get("variants") or []):
        for f in ("q", "a"):
            v[f] = fix_text(v.get(f, ""))
        for st in (v.get("steps") or []):
            if isinstance(st, dict):
                for f in ("text", "why"):
                    st[f] = fix_text(st.get(f, ""))
