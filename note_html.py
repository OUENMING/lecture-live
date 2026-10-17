"""自包含 HTML 笔记（2026-10-17 作者追加要求 B）：同目录同名 `.html`。

## 组件（作者点名的六项，逐条对到实现）

| # | 要求 | 实现在哪 |
|---|---|---|
| 1 | 例题**分步展开**（点一下出下一步，每步附「为什么」） | 每步一个 `<details>`（**不用 JS 也能用**）+ 顶部「全部展开」按钮 |
| 2 | 自测**先答再揭晓** | 题干在上、答案在 `<details>` 里，**原生折叠** |
| 3 | 公式渲染（KaTeX；离线退回 TeX 源码，**不能白屏**） | `.tex` 元素里先放 TeX 源码，KaTeX 加载成功才替换；失败保持源码可见 |
| 4 | 适合画图的知识点**自动出图** + 可拖动参数 | `PLOT_SPECS` 三个固定图形（成本曲线 / 三次函数 / 供需），**参数曲线在 Python 里预采样**，JS 只做切换 |
| 5 | 知识点地图 / 目录 + 阅读进度 | 左侧目录（点击跳转）+ 顶部进度条 |
| 6 | 克制的过渡动画、亮/暗主题 | 折叠/滚动入场（IntersectionObserver）；`prefers-reduced-motion` 下全关；亮色为默认、`prefers-color-scheme` 跟随、右上角手动切换 |

## 调研结论（`scratchpad/notes/research/d-format.md`，2026-10-17）带来的四处改动

1. **阅读进度条在调研里查不到学习收益证据** —— 保留（作者要求）但**只当导航**，
   文案里不承诺任何学习效果；
2. **默认浅色**（NN/g：浅色模式视觉表现更好）+ 跟随系统 + 手动切换（不是暗色优先）；
3. **CDN 允许失败**：唯一的远端资源是 KaTeX（作者明确允许），失败时公式退回源码；
4. **打印样式**兜底（`@media print`），因为「打印成 PDF 带去上课」是真实用法。

## 纪律

- **纯渲染**：不联网、不读文件。数据全是 `review` dict + 几个字符串。
- **不用 JS 也能读**：正文全部服务端渲染进 HTML；JS 只做展开/切换/进度。
- **不引入新的 Python 依赖**：图形是 SVG（手写坐标系），没有 numpy/matplotlib。
"""
from __future__ import annotations
import html
import json
import math
import pathlib
import re

#: KaTeX 的**仓库内**副本（版本钉死）。⭐ 第六轮：不再走 jsdelivr ——
#: CDN 一断，公式就退回 TeX 源码（作者要求"断网也能渲染"）。
#: ⚠️ 为什么不放 `assets/`：那是**指向出战仓库的软链**（`.git/info/exclude` 里也排除）
#:    —— 放进去既不进这个仓库、又污染另一个仓库（`tests/test_linebreak.py` 记过这事）。
#:    仓库里放第三方件的位置是 `vendor/`（`vendor/budoux` 是成例）。
KATEX_VERSION = "0.16.9"
KATEX_SRC = pathlib.Path(__file__).resolve().parent / "vendor" / "katex" / KATEX_VERSION
#: 装到笔记旁边的那一层（HTML 与它同一个文件夹 → 用**相对**引用，断网也取得到）。
KATEX_DIR = f".classlive-assets/katex/{KATEX_VERSION}"
KATEX_CSS = f"{KATEX_DIR}/katex.min.css"
KATEX_JS = f"{KATEX_DIR}/katex.min.js"


def install_assets(note_html_path) -> bool:
    """把 KaTeX 副本装到笔记旁边（**幂等**）→ 能不能用。

    ⚠️ `note_page` 是**纯渲染**（不读文件、不写文件）；装资源是 I/O，所以单独一个
       函数、由写端（`obsidian_writer._write_sidecars`）调。装不上不算错：
       页面上公式会退回 TeX 源码（那条兜底一直在）。
    """
    import shutil
    try:
        dst = pathlib.Path(note_html_path).parent / KATEX_DIR
        if not (dst / "katex.min.js").exists():
            dst.mkdir(parents=True, exist_ok=True)
            for f in KATEX_SRC.glob("*"):
                if f.is_file():
                    shutil.copy2(f, dst / f.name)
            (dst / "fonts").mkdir(exist_ok=True)
            for f in (KATEX_SRC / "fonts").glob("*.woff2"):
                shutil.copy2(f, dst / "fonts" / f.name)
        return (dst / "katex.min.css").exists() and (dst / "fonts").is_dir()
    except Exception:                                          # noqa: BLE001
        return False

#: 三个固定图形的规范（模型只给 `kind` + `params`，画法在这里写死）。
#: `x` = 横轴范围；`slider` = 允许拖动的参数（拖动会重画）。单位/标签都是中文。
PLOT_SPECS = {
    "cost_curves": {"x": (0.2, 14.0), "xlabel": "产量 q", "ylabel": "成本",
                    "markers": [2],
                    "params": {"a": 1.0, "b": -12.0, "c": 60.0},
                    "slider": ("b", [-24.0, -18.0, -12.0, -8.0, 0.0]),
                    "slider_label": "平均成本 AC 的一次项系数 b",
                    "slider_hint": "拖动看：b 越负，AC 的最低点越靠右、最低值越低，"
                                   "MC 与 AC 的交点跟着一起走。",
                    "legend": ["AC 平均成本", "MC 边际成本"]},
    "function_cubic": {"x": (-4.0, 4.0), "xlabel": "x", "ylabel": "f(x)",
                       #: ⚠️ 1 号 series 是**两个驻点**（不是一个点）—— 不声明成标记的话
                       #: 会被当曲线连成一条直线（第四轮自查：截图里 blue 的假线段）。
                       "markers": [1],
                       "params": {"a": 1.0, "b": 0.0, "c": -3.0, "d": 0.0},
                       "slider": ("c", [-9.0, -6.0, -3.0, 0.0, 3.0]),
                       "slider_label": "一次项系数 c（f′(x)=3x²+c）",
                       "slider_hint": "拖动看：c 越负，两个驻点离得越远；c 到 0 时并成一个，"
                                      "c 为正时 f′(x)=0 无解（没有驻点）。",
                       "legend": ["f(x)", "驻点"]},
    "supply_demand": {"x": (0.0, 100.0), "xlabel": "数量 Q", "ylabel": "价格 p",
                      "markers": [2],
                      "params": {"d0": 100.0, "d1": 2.0, "s0": 10.0, "s1": 1.0},
                      "slider": ("d1", [0.5, 1.0, 2.0, 3.0, 4.0]),
                      "slider_label": "需求斜率 d₁（Qd=d₀−d₁·p）",
                      "slider_hint": "拖动看：需求越陡（d₁ 越大），均衡价格越低、均衡数量越少。",
                      "legend": ["需求 D", "供给 S", "均衡点"]},
}

_N = 72            # 每条曲线的采样点数（预采样进 HTML，别太大）


# ---------------------------------------------------------------- 曲线数学（纯函数）
def curves(kind: str, params: dict) -> list:
    """`kind` + `params` -> `[(series_index, [(x, y), …])]`。**画法的唯一定义点**。

    三个 kind 的解析式（写在这里而不是 prompt 里 —— 模型只给参数，公式由代码保证）：
      · `cost_curves`   TC = aq³+bq²+cq → AC = aq²+bq+c、MC = 3aq²+2bq+c
      · `function_cubic` f(x) = ax³+bx²+cx+d
      · `supply_demand` Qd = d0−d1·p、Qs = s0+s1·p（横轴 Q、纵轴 p）
    """
    spec = PLOT_SPECS.get(kind)
    if not spec:
        return []
    p = dict(spec["params"])
    for k, v in (params or {}).items():
        if k in p:
            try:
                p[k] = float(v)
            except (TypeError, ValueError):
                pass
    x0, x1 = spec["x"]
    out: list = []

    def f(*vals):
        """把某条曲线的函数值算出来（越界 -> None，画的时候断开）。"""
        ys = []
        for i in range(_N):
            x = x0 + (x1 - x0) * i / (_N - 1)
            try:
                y = vals[0](x)
            except Exception:                                  # noqa: BLE001
                y = None
            if y is None or not math.isfinite(y) or abs(y) > 1e9:
                ys.append(None)
            else:
                ys.append(y)
        return ys

    if kind == "cost_curves":
        a, b, c = p["a"], p["b"], p["c"]
        xs = [x0 + (x1 - x0) * i / (_N - 1) for i in range(_N)]
        out.append((0, list(zip(xs, f(lambda x: a * x * x + b * x + c)))))
        out.append((1, list(zip(xs, f(lambda x: 3 * a * x * x + 2 * b * x + c)))))
        out.append((2, _min_ac_marker(a, b, c)))
    elif kind == "function_cubic":
        a, b, c, d = p["a"], p["b"], p["c"], p["d"]
        xs = [x0 + (x1 - x0) * i / (_N - 1) for i in range(_N)]
        out.append((0, list(zip(xs, f(lambda x: a * x ** 3 + b * x * x + c * x + d)))))
        out.append((1, _stationary(a, b, c, d)))
    elif kind == "supply_demand":
        d0, d1, s0, s1 = p["d0"], p["d1"], p["s0"], p["s1"]
        xs = [x0 + (x1 - x0) * i / (_N - 1) for i in range(_N)]
        out.append((0, [(x, (d0 - x) / d1 if d1 else None) for x in xs]))
        out.append((1, [(x, (x - s0) / s1 if s1 else None) for x in xs]))
        q = (d0 - s0) / (d1 + s1) if (d1 + s1) else None
        pt = [(q, (d0 - q) / d1)] if q and q >= 0 else []
        out.append((2, pt))
    return out


def _min_ac_marker(a: float, b: float, c: float) -> list:
    """`min AC` 那个点：AC=MC ⇔ 2aq²+bq=0 ⇔ q = −b/(2a)（q>0 的那个）。"""
    if a == 0:
        return []
    q = -b / (2 * a)
    if q <= 0:
        return []
    return [(q, a * q * q + b * q + c)]


def _stationary(a: float, b: float, c: float, d: float) -> list:
    """三次函数的驻点：f'(x)=3ax²+2bx+c=0。判别式 <0 就没有实驻点。"""
    if a == 0:
        return []
    disc = 4 * b * b - 12 * a * c
    if disc < 0:
        return []
    sq = math.sqrt(disc)
    pts = []
    for x in ((-2 * b + sq) / (6 * a), (-2 * b - sq) / (6 * a)):
        pts.append((x, a * x ** 3 + b * x * x + c * x + d))
    return pts


# ---------------------------------------------------------------- SVG（服务端预采样）
def _nice_bounds(all_ys: list) -> tuple:
    vals = [y for y in all_ys if y is not None]
    if not vals:
        return -1.0, 1.0
    lo, hi = min(vals), max(vals)
    if lo == hi:
        lo, hi = lo - 1, hi + 1
    pad = (hi - lo) * 0.08
    return lo - pad, hi + pad


def svg(kind: str, params: dict, *, w: int = 460, h: int = 280, pid: str = "p",
        caption: str = "") -> str:
    """一张图（**含可拖动参数的预采样路径** —— JS 只换 `points`，不再算数学）。

    `caption` 是图的标题，**放在 `<figure>` 里面**（2026-10-17 自查抓到的版式 bug：
    标题原来是拼在 figure 后面的兄弟节点，而 `.plots` 是 grid —— 于是标题被当成
    独立的一格排到图旁边去，三张图散成一团。判据：`tests/test_note_v2.py` 里
    「每张图都有自己的 figcaption」那两条）。
    """
    spec = PLOT_SPECS.get(kind)
    if not spec:
        return ""
    series = curves(kind, params)
    if not series:
        return ""
    x0, x1 = spec["x"]
    ys = [y for _i, pts in series for _x, y in pts]
    y0, y1 = _nice_bounds(ys)
    # ⚠️ 左边距 44→56：y 轴刻度是 5 个（第四轮加的），纵轴标题改成**竖排**贴在左缘，
    #    44 的时候「成本」会和顶端刻度（336 这种）叠在一起（自查截图抓到的）。
    pad_l, pad_r, pad_t, pad_b = 56, 12, 16, 34
    iw, ih = w - pad_l - pad_r, h - pad_t - pad_b

    def sx(x):
        return pad_l + (x - x0) / (x1 - x0) * iw

    def sy(y):
        return pad_t + (1 - (y - y0) / (y1 - y0)) * ih

    def path(pts):
        seg, out, pen = [], [], False
        for x, y in pts:
            if y is None:
                if pen:
                    out.append(" ".join(seg))
                    seg, pen = [], False
                continue
            seg.append(f"{sx(x):.1f},{sy(y):.1f}")
            pen = True
        if seg:
            out.append(" ".join(seg))
        return out

    colors = ("var(--c1)", "var(--c2)", "var(--c3)")
    parts = [f'<svg class="plot" viewBox="0 0 {w} {h}" role="img" '
             f'aria-label="{html.escape("、".join(spec["legend"]) or kind)}">',
             f'<g class="grid">']
    # 网格 + 轴标签（4 等分，够读就行）
    for i in range(5):
        gx = pad_l + iw * i / 4
        gy = pad_t + ih * i / 4
        parts.append(f'<line x1="{gx:.1f}" y1="{pad_t}" x2="{gx:.1f}" y2="{pad_t + ih}"/>')
        parts.append(f'<line x1="{pad_l}" y1="{gy:.1f}" x2="{pad_l + iw}" y2="{gy:.1f}"/>')
    parts.append("</g>")
    # 坐标轴
    parts.append(f'<line class="axis" x1="{pad_l}" y1="{pad_t + ih}" '
                 f'x2="{pad_l + iw}" y2="{pad_t + ih}"/>')
    parts.append(f'<line class="axis" x1="{pad_l}" y1="{pad_t}" x2="{pad_l}" '
                 f'y2="{pad_t + ih}"/>')
    parts.append(f'<text class="lbl" x="{pad_l + iw / 2:.0f}" y="{h - 6}">'
                 f'{html.escape(spec["xlabel"])}</text>')
    # 纵轴标题**竖排**贴左缘（横着放会和顶端刻度撞车）
    cy = pad_t + ih / 2
    parts.append(f'<text class="lbl" x="13" y="{cy:.0f}" text-anchor="middle" '
                 f'transform="rotate(-90 13 {cy:.0f})">{html.escape(spec["ylabel"])}</text>')
    # 刻度值：x 轴 5 个（两端 + 3 个内点）、y 轴 5 个 —— 老版只有两端，
    # 作者第四轮点名「纵轴至少 3 个」；读图时要能对着格线读数。
    def _tk(v) -> str:
        return f"{v:.3g}"
    for i in range(5):
        xt, yv = x0 + (x1 - x0) * i / 4, y0 + (y1 - y0) * i / 4
        gx = pad_l + iw * i / 4
        anchor = "start" if i == 0 else ("end" if i == 4 else "middle")
        parts.append(f'<text class="tick" x="{gx:.1f}" y="{pad_t + ih + 14}" '
                     f'text-anchor="{anchor}">{_tk(xt)}</text>')
        parts.append(f'<text class="tick" x="{pad_l - 4}" y="{pad_t + ih * (1 - i / 4) + 4:.1f}" '
                     f'text-anchor="end">{_tk(yv)}</text>')
    markers = set(spec.get("markers") or ())
    for idx, pts in series:
        if idx in markers:              # 关键点（交点 / 均衡点 / 驻点）：只画实心圆
            for x, y in pts:
                if y is None:
                    continue
                parts.append(f'<circle class="dot" cx="{sx(x):.1f}" cy="{sy(y):.1f}" r="4"/>')
            continue
        for _k, seg in enumerate(path(pts)):
            parts.append(f'<polyline class="c{idx}" points="{seg}" '
                         f'style="stroke:{colors[idx % 3]}"/>')
    # 图例：**每条曲线一个色块 + 名字**（旧版只有一行文字 —— 读者不知道哪条是哪条）。
    # ⚠️ 只画真正有曲线的那些 series（`idx` 用图例下标取名字；交点那种点系列跳过）。
    leg = []
    drawn = sorted({idx for idx, _pts in series})
    lx = float(pad_l)
    for idx in drawn:
        if idx >= len(spec["legend"]):
            continue
        name = spec["legend"][idx]
        if idx in markers:              # 点标记：画圆点，不画"一条线"（那会读成曲线）
            parts.append(f'<circle class="dot" cx="{lx + 7:.1f}" cy="{pad_t - 10}" r="4"/>')
        else:
            parts.append(f'<line x1="{lx:.1f}" y1="{pad_t - 10}" x2="{lx + 16:.1f}" '
                         f'y2="{pad_t - 10}" style="stroke:{colors[idx % 3]};stroke-width:2.5"/>')
        parts.append(f'<text class="lbl" x="{lx + 20:.1f}" y="{pad_t - 6}">'
                     f'{html.escape(name)}</text>')
        est = sum(13 if ord(ch) > 0x2000 else 7 for ch in name)     # 估宽：够用就行
        lx += 20 + est + 16
        leg.append(name)
    parts.append("</svg>")

    # 可拖动参数：把每个取值的**整条曲线**预先算好塞进 data——
    # ⚠️ 这是刻意的：JS 里再抄一份解析式 = 两份数学、迟早漂移（`panel.py` 的教训）。
    sliders = []
    pk, vals = spec["slider"]
    base = dict(spec["params"])
    base.update({k: v for k, v in (params or {}).items() if k in base})
    opts = []
    for v in vals:
        pr = dict(base, **{pk: v})
        opts.append([[f"{x:.2f},{y:.2f}" if y is not None else "" for x, y in pts]
                     for _i, pts in curves(kind, pr)])
    parts.append(f'<div class="slider" data-plot="{pid}" '
                 f'data-opts="{html.escape(json.dumps(opts, separators=(",", ":")), quote=True)}" '
                 f'data-vals="{html.escape(json.dumps(list(vals)), quote=True)}">'
                 f'<label>{html.escape(spec.get("slider_label") or pk)} = '
                 f'<b class="v">{base.get(pk, vals[0]):g}</b></label>'
                 f'<input type="range" min="0" max="{len(vals) - 1}" step="1" '
                 f'value="{_nearest(vals, base.get(pk, vals[0]))}" '
                 f'aria-label="{html.escape(spec.get("slider_label") or pk)}">'
                 + (f'<p class="hint">{html.escape(spec["slider_hint"])}</p>'
                    if spec.get("slider_hint") else "")
                 + "</div>")
    cap = (f'<figcaption><strong>{caption}</strong></figcaption>' if caption else "")
    return '<figure class="fig">' + "".join(parts) + cap + "</figure>"


def _norm_title(s) -> str:
    """标题归一：去 `[13:29]` 前缀、去括号里的英文、去标点空白（只用来判重）。"""
    s = re.sub(r"^\[?\d{1,2}:\d{2}\]?\s*", "", str(s or ""))
    s = re.sub(r"（[^）]*）|\([^)]*\)", "", s)
    return re.sub(r"[\s·:：，,。.、「」【】\-—_/]", "", s)


def dup_caption(caption, heading) -> bool:
    """图题与卡片标题重复？（一方含另一方就算 —— 读者刚看过标题，再读一遍是噪声）

    ⚠️ 第四轮：`平均成本与边际成本曲线` 挂在「平均成本」卡片下面。图里已经有图例，
       重复的那半句没有信息量。
    """
    a, b = _norm_title(caption), _norm_title(heading)
    if not a or not b:
        return False
    return a in b or b in a


def _nearest(vals: list, v) -> int:
    try:
        return min(range(len(vals)), key=lambda i: abs(float(vals[i]) - float(v)))
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------- 行内 Markdown
#: `$$…$$` 或 `$…$`（整块在前，避免 `$$` 被拆成两个 `$`）。
_SPAN = re.compile(r"\$\$.+?\$\$|\$[^$]+\$", re.S)


def inline(text: str) -> str:
    """一段文本 → HTML。**公式先抽出来**，其余照常转义。

    ⚠️ 顺序不能反（2026-10-17 第三轮实测）：先把整段 `html.escape` 再抽 `$…$`，
       TeX 源码里的撇号会变成 `&#x27;` —— 属性里还能被浏览器解回来，但**兜底文字**
       再 escape 一次就成了字面量 `&#x27;`（截图上是 `f&#x27;(x)=3x²-3`）。
       现在：原样文本 → `auto_tex` → 按 `$…$` 切 → 公式段（原件进 `data-tex`、
       格式化后的兜底进正文）与普通段（escape + 三种标记）分别处理。
    """
    import note_math
    s = note_math.auto_tex(str(text or ""))
    out, last = [], 0
    for m in _SPAN.finditer(s):
        out.append(_prose_html(s[last:m.start()]))
        out.append(_tex_span(m.group(0)))
        last = m.end()
    out.append(_prose_html(s[last:]))
    return "".join(out)


def _unquote_md(block) -> str:
    """md 折叠块（`> [!note]- 标题` / `> 正文`）→ 一行能放进 HTML 的字。

    ⚠️ 只做"去掉 md 记号"，**不改写内容**：`lost_items` 是用户的原始标记，
       清掉 `>` 与 callout 头就够读（HTML 那边不需要复刻 Obsidian 的折叠语法）。
    """
    lines = []
    for raw in str(block or "").splitlines():
        t = re.sub(r"^\s*>\s?", "", raw).strip()
        t = re.sub(r"^\[![a-z]+\]-?\s*", "", t).strip()
        if t:
            lines.append(t)
    return "　".join(lines)


def _prose_html(seg: str) -> str:
    """普通文本：转义 + 放行 `**粗**` 与 `` `代码` ``。"""
    seg = html.escape(seg)
    seg = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", seg)
    seg = re.sub(r"`([^`]+)`", r"<code>\1</code>", seg)
    return seg


def _tex_span(raw: str) -> str:
    """`$…$` / `$$…$$` → `.tex` 元素：`data-tex` 存**原样 TeX**（给 KaTeX），
    正文里放 Unicode 兜底（KaTeX 没加载/离线截图时肉眼看到的就是它）。"""
    import note_math
    block = raw.startswith("$$")
    inner = raw[2:-2] if block else raw[1:-1]
    disp = ' data-display="1"' if block else ""
    return (f'<span class="tex"{disp} data-tex="{html.escape(inner, quote=True)}">'
            f"{html.escape(note_math.pretty_tex(inner))}</span>")


# ---------------------------------------------------------------- 页面
_CSS = """
:root{--bg:#fbfaf7;--fg:#1f2328;--muted:#5c6672;--card:#fff;--line:#e5e2dc;
 --code:#f2f0eb;--c1:#c2410c;--c2:#1d4ed8;--c3:#0f766e;--accent:#8a5a00}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#15171a;--fg:#e6e3dd;
 --muted:#9aa4b0;--card:#1c1f23;--line:#2b3037;--code:#22262c;--c1:#fb923c;--c2:#7dd3fc;
 --c3:#5eead4;--accent:#e0b155}}
:root[data-theme=dark]{--bg:#15171a;--fg:#e6e3dd;--muted:#9aa4b0;--card:#1c1f23;
 --line:#2b3037;--code:#22262c;--c1:#fb923c;--c2:#7dd3fc;--c3:#5eead4;--accent:#e0b155}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.75 -apple-system,
 "PingFang SC","Noto Sans SC",system-ui,sans-serif}
#prog{position:fixed;top:0;left:0;height:3px;width:0;background:var(--accent);z-index:9;
 transition:width .1s linear}
@media (prefers-reduced-motion:reduce){#prog{transition:none}
 *{animation:none!important;transition:none!important}}
#topbar{position:fixed;top:0;right:0;padding:6px 10px;z-index:8;font-size:13px}
#topbar button{cursor:pointer;background:var(--card);color:var(--fg);border:1px solid var(--line);
 border-radius:6px;padding:3px 9px}
.wrap{max-width:1020px;margin:0 auto;padding:28px 20px 80px}
h1{font-size:26px;line-height:1.3;margin:6px 0 10px}
.meta{color:var(--muted);font-size:14px;border-left:3px solid var(--line);padding-left:10px}
.meta div{margin:2px 0}
h2{font-size:20px;margin:36px 0 10px;padding-bottom:6px;border-bottom:1px solid var(--line)}
h3{font-size:18px;margin:26px 0 8px}
h4{font-size:16px;margin:18px 0 6px;color:var(--accent)}
section.card{background:var(--card);border:1px solid var(--line);border-radius:12px;
 padding:14px 18px;margin:14px 0}
/* ⚠️ 入场动画只在**有 JS** 时才把内容藏起来（`html.js` 由 <head> 里那行内联脚本加）：
   没 JS 时正文必须看得见 —— 这是本模块开头那条纪律（"不用 JS 也能读"）的实现，
   顺带也修掉了"截图/打印时上半屏空白"（实测：headless 截图滚过 #kp1 之后，
   上面几张卡片的 IntersectionObserver 还没触发 → opacity:0 → 半屏白）。 */
.reveal{opacity:1;transform:none}
html.js .reveal{opacity:0;transform:translateY(6px);
 transition:opacity .28s ease,transform .28s ease}
html.js .reveal.in{opacity:1;transform:none}
@media (prefers-reduced-motion:reduce){html.js .reveal{opacity:1!important;transform:none!important}}
details{border:1px dashed var(--line);border-radius:8px;padding:6px 10px;margin:8px 0;
 background:var(--code)}
details summary{cursor:pointer;font-weight:600;color:var(--accent)}
details[open] summary{margin-bottom:6px}
details.step{background:transparent;border-style:solid;border-color:var(--line)}
formula,.tex{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;background:var(--code);
 padding:1px 4px;border-radius:4px;font-size:15px}
.tex.done{background:none;padding:0;font-family:inherit}
.plots{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:14px}
figure.fig{margin:0;background:var(--card);border:1px solid var(--line);border-radius:12px;
 padding:8px}
svg.plot{width:100%;height:auto;display:block}
svg.plot .grid line{stroke:var(--line);stroke-width:.6;opacity:.7}
svg.plot .axis{stroke:var(--muted);stroke-width:1}
svg.plot polyline{fill:none;stroke-width:2.2;stroke-linecap:round}
svg.plot .dot{fill:var(--c3)}
svg.plot .lbl,svg.plot .tick{fill:var(--muted);font-size:11px;font-family:inherit}
.slider{display:flex;align-items:center;gap:10px;padding:2px 8px 6px;flex-wrap:wrap}
.slider p.hint{flex-basis:100%;margin:0 0 2px 2px;color:var(--muted);font-size:13px}
.slider input{flex:1;accent-color:var(--accent)}
nav.toc{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 18px}
nav.toc ol{margin:6px 0;padding-left:22px}
nav.toc a{color:var(--fg);text-decoration:none;border-bottom:1px dotted var(--line)}
nav.toc a:hover{color:var(--accent)}
table{border-collapse:collapse;width:100%;font-size:15px}
th,td{border:1px solid var(--line);padding:5px 8px;text-align:left;vertical-align:top}
th{background:var(--code)}
code{background:var(--code);padding:1px 4px;border-radius:4px}
.tr{border-bottom:1px dotted var(--line);padding:3px 0;font-size:14px}
.tr b,.tr i{color:var(--muted);font-style:normal;font-weight:600;margin-right:4px}
details.internal{border-style:solid}
.q{border-left:3px solid var(--c2);padding-left:10px;margin:14px 0}
.q .a{margin-top:6px}
footer{color:var(--muted);font-size:13px;margin-top:40px}
@media print{#prog,#topbar,.slider{display:none}section.card{break-inside:avoid}
 html.js .reveal{opacity:1!important;transform:none!important}
 body{background:#fff}}
"""

_JS = """
(function(){
 var d=document;
 function tex(){
  if(!window.katex) return false;
  d.querySelectorAll('.tex').forEach(function(el){
   var src=el.getAttribute('data-tex')||el.textContent;
   try{
    var disp=el.getAttribute('data-display')==='1';
    el.innerHTML=katex.renderToString(src,{displayMode:disp,throwOnError:false});
    el.classList.add('done');
   }catch(e){}
  });
  return true;
 }
 if(!tex()){ // 离线 / CDN 挂了：公式保持 TeX 源码可见（不白屏），5 秒后再试一次
  var t=setTimeout(function(){ if(!tex()) console.warn('KaTeX 不可用：公式显示为 TeX 源码'); },5000);
  window.addEventListener('load',function(){tex();clearTimeout(t);});
 }
 d.querySelectorAll('.slider').forEach(function(w){
  var svg=d.querySelector('[data-plot="'+w.getAttribute('data-plot')+'"]');
  var input=w.querySelector('input'), out=w.querySelector('.v');
  var opts=JSON.parse(w.getAttribute('data-opts')||'[]');
  var vals=JSON.parse(w.getAttribute('data-vals')||'[]');
  input.addEventListener('input',function(){
   var i=+input.value, o=opts[i]; if(!o||!svg) return;
   out.textContent=vals[i];
   svg.querySelectorAll('polyline').forEach(function(pl,k){
    var pts=(o[k]||[]);
    var groups=[],seg=[];pts.forEach(function(s){ if(s) seg.push(s); else if(seg.length){groups.push(seg.join(' '));seg=[];} });
    if(seg.length) groups.push(seg.join(' '));
    pl.setAttribute('points',groups[0]||'');
    var extra=pl.parentNode.querySelectorAll('polyline.userline');extra.forEach(function(e){e.remove()});
    for(var g=1;g<groups.length;g++){
     var c=pl.cloneNode();c.setAttribute('points',groups[g]);c.classList.add('userline');
     pl.parentNode.insertBefore(c,pl.nextSibling);
    }
   });
  });
 });
 var bar=d.getElementById('prog');
 function onScroll(){
  var h=d.documentElement, max=(h.scrollHeight-h.clientHeight)||1;
  bar.style.width=(Math.min(1,h.scrollTop/max)*100).toFixed(2)+'%';
 }
 d.addEventListener('scroll',onScroll,{passive:true});onScroll();
 var reduce=window.matchMedia&&window.matchMedia('(prefers-reduced-motion: reduce)').matches;
 if('IntersectionObserver' in window && !reduce){
  var io=new IntersectionObserver(function(es){es.forEach(function(e){
   if(e.isIntersecting){e.target.classList.add('in');io.unobserve(e.target);}});},
   {rootMargin:'0px 0px -8% 0px'});
  d.querySelectorAll('.reveal').forEach(function(el){io.observe(el)});
 } else { d.querySelectorAll('.reveal').forEach(function(el){el.classList.add('in')}); }
 var btn=d.getElementById('expand');var all=false;
 if(btn) btn.addEventListener('click',function(){
  all=!all;
  d.querySelectorAll('details.step').forEach(function(x){x.open=all});
  btn.textContent=all?'收起全部步骤':'展开全部步骤';
 });
 var tbtn=d.getElementById('theme');
 // ⚠️ 第五轮自查（top-dark 截图）：按钮文字是写死的「暗色」—— 系统是暗色时页面明明已经暗了，
 //    按钮还写着"暗色"（读起来像"点我变暗"）。载入时按**生效的主题**把标签摆正。
 function themeLabel(){
  var set=d.documentElement.getAttribute('data-theme');
  var dark=(set==='dark')||(!set&&window.matchMedia&&window.matchMedia('(prefers-color-scheme: dark)').matches);
  tbtn.textContent=dark?'☀︎ 亮色':'☾ 暗色';
 }
 if(tbtn){ themeLabel();
  tbtn.addEventListener('click',function(){
  var cur=d.documentElement.getAttribute('data-theme');
  var next=(cur==='dark')?'light':(cur==='light'?'dark':'dark');
  d.documentElement.setAttribute('data-theme',next);
  tbtn.textContent=(next==='dark')?'☀︎ 亮色':'☾ 暗色';
  });
 }
})();
"""


def _section_html(sec: dict, idx: int, *, reveal: bool = True, fig: str = "") -> str:
    cls = "reveal" if reveal else ""
    o = [f'<section class="card {cls}" id="kp{idx}">',
         f'<h3>{idx}. {inline(sec.get("heading"))}'
         + (f' <code>🎙 {html.escape(str(sec.get("ts")))}</code>' if sec.get("ts") else "")
         + "</h3>"]
    if sec.get("prereq_link"):
        o.append(f'<p>🔗 {inline(sec["prereq_link"])}</p>')
    for para in (sec.get("prose") or []):
        o.append(f"<p>{inline(para)}</p>")
    if sec.get("prereq"):
        o.append(f"<details><summary>📚 前置补课（教授没讲、但要懂）</summary>"
                 f"<p>{inline(sec['prereq'])}</p></details>")
    if fig:
        o.append(fig)          # ⭐ 图跟着它的知识点（作者第三轮第 1 条）
    ex = sec.get("example") or {}
    if ex.get("prompt") or ex.get("steps"):
        o.append(f'<h4>例题 · {inline(ex.get("title") or "教授的例题")}</h4>')
        if ex.get("prompt"):
            o.append(f'<p><strong>题目</strong>：{inline(ex["prompt"])}</p>')
        for i, st in enumerate(ex.get("steps") or [], 1):
            t, w = inline(st.get("text")), inline(st.get("why"))
            o.append(f'<details class="step"><summary>第 {i} 步</summary>'
                     f"<p>{t}</p>" + (f"<p><em>为什么：{w}</em></p>" if w else "")
                     + "</details>")
        if ex.get("answer"):
            o.append(f'<details><summary>答案</summary><p>{inline(ex["answer"])}</p></details>')
    for j, v in enumerate(sec.get("variants") or [], 1):
        body = "".join(f"<p>{inline(s.get('text'))}"
                       + (f"<br><em>为什么：{inline(s.get('why'))}</em>" if s.get("why") else "")
                       + "</p>" for s in (v.get("steps") or []))
        o.append(f'<h4>变式 {j}（先自己写）</h4><p>{inline(v.get("q"))}</p>'
                 f'<details><summary>答案</summary>{body}'
                 + (f"<p>{inline(v.get('a'))}</p>" if v.get("a") else "") + "</details>")
    fd = sec.get("faded") or {}
    if fd.get("q"):
        blanks = "".join(f'<p>⬜ {inline(b)}</p>' for b in (fd.get("blanks") or []))
        o.append(f'<h4>补全题（把关键步骤填上）</h4><p>{inline(fd["q"])}</p>{blanks}'
                 + (f'<details><summary>答案</summary><p>{inline(fd["a"])}</p></details>'
                    if fd.get("a") else ""))
    if sec.get("pitfalls"):
        o.append("<h4>常见误区</h4><ul>"
                 + "".join(f"<li>{inline(p)}</li>" for p in sec["pitfalls"]) + "</ul>")
    if sec.get("terms"):
        o.append("<p>🔑 " + " · ".join(inline(t.get("en")) for t in sec["terms"]) + "</p>")
    if sec.get("sources"):
        o.append("<p>📎 来源：" + " · ".join(inline(s) for s in sec["sources"]) + "</p>")
    o.append("</section>")
    return "".join(o)


def page(title: str, head: list, toc_items: list, sections_html: list, qa: list,
         plots: list, extra_html: str = "", footer: str = "") -> str:
    """整页 HTML。参数全是**已渲染好的片段** —— 这个函数只负责骨架。

    `toc_items` 与 `sections_html` **分开传**：目录要的是标题（知识点），
    正文是 HTML —— 合成一个参数就会逼调用方去解析自己刚生成的 HTML（实测踩过）。
    """
    toc = "".join(
        f'<li><a href="#kp{i}">{inline(t)}</a></li>'
        for i, t in enumerate(toc_items, 1))
    qa_html = ""
    for i, q in enumerate(qa, 1):
        qa_html += (f'<div class="q"><p><strong>{i}. {inline(q.get("q"))}</strong></p>'
                    f'<details><summary>先自己想 · 再揭晓</summary>'
                    f'<div class="a">{inline(q.get("a"))}</div></details></div>')
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<script>document.documentElement.className="js"</script>
<link rel="stylesheet" href="{KATEX_CSS}" onerror="window.__katex_css_failed=1">
<style>{_CSS}</style>
<script defer src="{KATEX_JS}" onerror="window.__katex_failed=1"></script>
</head><body>
<div id="prog" aria-hidden="true"></div>
<div id="topbar"><button id="theme" type="button">☾ 暗色</button></div>
<div class="wrap">
<h1>{html.escape(title)}</h1>
<div class="meta">{''.join(head)}</div>
<nav class="toc"><strong>知识点地图</strong><ol>{toc}</ol></nav>
<p><button id="expand" type="button">展开全部步骤</button></p>
{''.join(sections_html)}
{('<h2>图</h2><div class="plots">' + ''.join(plots) + "</div>") if plots else ''}
<h2>自测（先答再揭晓）</h2>{qa_html or '<p>（本节没生成自测）</p>'}
{extra_html}
<footer>{footer}<p>🎙 课上一手 · 📖 课件材料 · 📚 标准理论；冲突时 📖 &gt; 📚 &gt; 🎙。
本页由 ClassLive 生成，公式在无法加载 KaTeX 时回退为 TeX 源码。</p></footer>
</div>
<script>{_JS}</script>
</body></html>
"""


def note_page(d) -> str:
    """`note_render.NoteData` -> 自包含 HTML。**纯渲染**（失败由调用方兜底）。"""
    review = d.review or {}
    sections = review.get("sections") or []
    head = [f"<div>🕐 {html.escape(str(d.entries[0]['ts'] if d.entries else '—'))} – "
            f"{html.escape(str(d.entries[-1]['ts'] if d.entries else '—'))} · "
            f"🗣 {len(d.entries)} 句</div>"]
    if d.materials_line:
        head.append(f"<div>{inline(d.materials_line)}</div>")
    checks = review.get("checks") or []
    if checks:
        n_ok = sum(1 for c in checks if c.get("status") == "ok")
        n_bad = sum(1 for c in checks if c.get("status") == "mismatch")
        n_un = sum(1 for c in checks if c.get("status") == "unchecked")
        # ⚠️ 学生只看到一句话（作者 2026-10-17 第三轮：「页头给学生看的是内部诊断」）：
        #    生成器的工作记录（哪条公式被改过、删了几句猜测）挪到页尾折叠。
        head.append(f'<div>✅ 数字核对：{n_ok + n_bad} 处已核'
                    + (f"（其中 {n_bad} 处按 📖/📚 改过）" if n_bad else "")
                    + f' · {n_un} 处待你确认（<a href="#checks">点开看</a>）</div>')
    # ⭐ 图放**它所属的知识点里**（作者第三轮第 1 条：不许全堆在最后一个区），
    #    同一张图（同 kind + 同参数）只出一张 —— 实测有 5 张一模一样的 AC/MC 曲线。
    plots_by_idx: dict = {}
    seen_figs: set = set()
    pid = 0
    for i, s in enumerate(sections):
        pl = s.get("plot") or {}
        if not pl.get("kind"):
            continue
        sig = (pl["kind"], json.dumps(pl.get("params") or {}, sort_keys=True))
        if sig in seen_figs:
            continue
        seen_figs.add(sig)
        pid += 1
        cap = "" if dup_caption(pl.get("title"), s.get("heading")) else inline(pl.get("title") or "")
        head_svg = svg(pl["kind"], pl.get("params") or {}, pid=f"plot{pid}", caption=cap)
        if head_svg:
            plots_by_idx[i] = head_svg
    secs = [_section_html(s, i, fig=plots_by_idx.get(i - 1))
            for i, s in enumerate(sections, 1)]
    toc_items = [s.get("heading", "") for s in sections]
    extra = ""
    if d.term_rows:
        extra += "<h2>本课术语</h2><ul>" + "".join(
            f"<li>{inline(str(r).lstrip('- '))}</li>" for r in d.term_rows) + "</ul>"
    if d.glossary_added:
        extra += ("<h2>本课新加入术语库</h2><p>"
                  + " · ".join(f"<code>{html.escape(str(t))}</code>" for t in d.glossary_added)
                  + "</p>")
    if d.review_dates:
        extra += ("<h2>复习排程</h2><p>"
                  + " · ".join(html.escape(x) for x in d.review_dates)
                  + "（合上笔记，先自己写一遍）</p>")
    # ── ⭐ 第六轮：md 变成简版之后，**详细版的那几层全在这里** ─────────────
    # （修正记录 / 我标的地方 / 标星句 / 最值得记的几句 / 逐句转录）
    all_corr = list((review.get("corrections") or [])) + list(d.corrections_extra or [])
    if all_corr:
        extra += ("<h2>修正记录（课上原话 → 修正值 + 依据）</h2>"
                  "<details><summary>点开看</summary>")
        for c in all_corr:
            fixed = inline(c.get("fixed"))
            if c.get("kind") == "check":
                extra += (f"<div>· <b>⚠ 三道核</b>：{inline(c.get('spoken'))}"
                          + (f" —— 重算：<b>{fixed}</b>" if fixed else "")
                          + (f"（{inline(c.get('basis'))}）" if c.get("basis") else "") + "</div>")
                continue
            extra += (f"<div>· <b>{inline(c.get('where')) or '—'}</b>："
                      f"「{inline(c.get('spoken'))}」→ <b>{fixed}</b>"
                      + (f"（依据：{inline(c.get('basis'))}）" if c.get("basis") else "")
                      + "</div>")
        extra += "</details>"
    if d.lost_items:
        extra += ("<h2>我标的地方（重点 / 没听懂）</h2><details><summary>点开看</summary>"
                  + "".join(f"<div>· {inline(_unquote_md(b))}</div>" for b in d.lost_items)
                  + "</details>")
    stars = [e for e in d.entries if e.get("star")]
    if stars:
        extra += ("<h2>⭐ 我标记的重点</h2><ul>" + "".join(
            f"<li><code>{html.escape(str(e.get('ts')))}</code> "
            f"{inline(e.get('zh') or e.get('en') or e.get('asr'))}</li>" for e in stars)
            + "</ul>")
    if d.keypoint_items:
        extra += ("<h2>🎯 这节课最值得记的几句</h2><ol>" + "".join(
            f"<li>{inline(text)} <span class=\"muted\">（{p:.2f}）</span></li>"
            for p, text, _ix in d.keypoint_items) + "</ol>")
    if d.entries:
        # 逐句转录（证据层）：md 变成简版之后**它的落点在这里** —— 一句不删、原样。
        # ⚠️ 中文那一栏要带上 🩹 ` ·补` 标记（课后补译 ≠ 课上直播译文）。
        #    这是渲染层最容易弄丢的一处 —— `note_render._bf` 是它的包装
        #    （唯一定义点：`obsidian_writer._bf_mark`），`tests/test_backfill.py` 钉着。
        import note_render
        extra += (f"<h2>📜 完整逐句转录（{len(d.entries)} 句）</h2>"
                  "<details><summary>点开看（未做任何删改）</summary>")
        for e in d.entries:
            zh = str(e.get("zh") or "") + note_render._bf(e)
            extra += (f"<div class=\"tr\"><code>{html.escape(str(e.get('ts')))}</code> "
                      + (f"<b>EN</b> {inline(e.get('en'))}" if e.get("en") else "")
                      + (f" <span class=\"muted\">ZH</span> {inline(zh)}"
                         if zh else "")
                      + (f" <span class=\"muted\">ASR</span> <i>{inline(e.get('asr'))}</i>"
                         if e.get("asr") else "")
                      + "</div>")
        extra += "</details>"
    # ⭐ 核对清单（`id="checks"`）：页头那行「数字核对：N 处已核…（点开看）」指向这里 ——
    #    第四轮自查发现 HTML 里根本没有这个锚点（点开看 = 死链）。
    #    覆盖度也放这儿：章节 + 实时要点是**确定性**清单，读者要能看到缺了哪条。
    cv = review.get("cover") or {}
    pts = review.get("lecture_points") or []
    if cv.get("rows") or checks or pts:
        body = ""
        if cv.get("rows"):
            body += ("<h3>章节覆盖</h3><p class=\"muted\">章节与实时要点来自课堂实时分章"
                     "（粒度不由模型定）；覆盖按时间窗算 —— 这段课时间上有没有小节。</p>"
                     "<table><tr><th>章节（时间段）</th><th>实时要点</th><th>覆盖</th></tr>")
            for r in cv["rows"]:
                n_ok = sum(1 for x in r["points"] if x["ok"])
                mark = ("✅" if r["ok"] else "⚠ 这一章没落到")
                body += (f"<tr><td>章 {r['i']}「{html.escape(str(r['title']))}」 "
                         f"<code>{html.escape(str(r['t0']))}–{html.escape(str(r['t1']))}</code></td>"
                         f"<td>{n_ok}/{len(r['points'])} 条</td><td>{mark}</td></tr>")
            body += "</table>"
            miss = [x for r in cv["rows"] for x in r["points"] if not x["ok"]]
            if miss:
                body += ("<h4>还没落到的要点（要补）</h4><ul>"
                         + "".join(f"<li><code>{html.escape(str(x['ts']))}</code> "
                                   f"{inline(x['point'])}</li>" for x in miss) + "</ul>")
        if pts and not cv.get("rows"):
            # 退化档（没有章节骨架的旧会话）：教授知识点清单是模型自己列的，按文字对齐。
            cov = {inline(c.get("point")).lower(): c.get("section")
                   for c in (review.get("covered") or [])}
            body += ("<h3>覆盖度</h3><p class=\"muted\">教授知识点（按文字对齐；"
                     "有章节骨架时改成按时间窗算）。</p>"
                     "<table><tr><th>教授讲的（🎙）</th><th>笔记覆盖</th></tr>")
            for p in pts:
                sec = cov.get(inline(p.get("point")).lower(), "")
                ok = bool(sec) and sec not in ("缺失", "missing", "-")
                body += (f"<tr><td><code>{html.escape(str(p.get('ts') or ''))}</code> "
                         f"{inline(p.get('point'))}</td><td>"
                         + (f"✅ {inline(sec)}" if ok else "⚠ 缺失") + "</td></tr>")
            body += "</table>"
        if checks:
            body += ("<h3>数字 / 公式三道核</h3><table>"
                     "<tr><th>断言</th><th>怎么核的</th><th>结果</th></tr>")
            for c in checks:
                st = {"ok": "✅ 一致", "mismatch": "⚠ 已修正",
                      "unchecked": "⬜ 未核对"}.get(str(c.get("status")), "⬜ 未核对")
                rec = c.get("recomputed")
                body += (f"<tr><td>{inline(c.get('claim'))}</td><td>{inline(c.get('how'))}</td>"
                         f"<td>{st}" + (f"（应为 {inline(rec)}）" if c.get('status') == 'mismatch'
                                       and rec else "") + "</td></tr>")
            body += "</table>"
        extra += (f'<details id="checks" class="internal">'
                  f"<summary>核对清单（覆盖度 · 数字三道核）</summary>{body}</details>")
    title = f"{d.course} · {d.date}" + (f" · {review.get('title_zh')}"
                                        if review.get("title_zh") else "")
    warns_html = ""
    if d.warns:
        warns_html = ("<details class=\"internal\"><summary>生成核对记录（内部 · 点开看）</summary>"
                      + "".join(f"<div>· {inline(w)}</div>" for w in d.warns) + "</details>")
    return page(title, head, toc_items, secs, review.get("qa") or [], [], extra,
                footer="<p>这是<b>详细版</b>（简版是 Markdown 那一份，同目录）。</p>"
                       + warns_html)
