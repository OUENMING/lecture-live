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

## 版式 A（2026-10-18 作者拍板；mockup `notesa/mockup.html` tab A 是结构/视觉的对照物）

| # | 要求 | 实现在哪 |
|---|---|---|
| 1 | 顶部「五分钟速览」= 一句话 + 3 takeaways + **五分钟串讲**（整节课的连贯叙述，段首加粗断言、每段点名小节号并链到锚点）+ 带日期的复习排程（今晚 / +3 天 / +7 天） | `_overview_html` + `_link_sections`；串讲数据来自 `note_v2.overview`（**一次额外小调用**） |
| 2 | 每节左栏 **sticky 线索栏**（1–3 条线索问题 + 术语「中文 (English)」）；窄屏折到节上方 | `_cue_html`（`.grid` 两栏；`@media (max-width:760px)` 单栏） |
| 3 | 每节 **Q 本节回答 → 讲解 → E 三种看法（📈/🧮/💡，只画真有的）→ C 考试结论** | `_section_html` 的 `.qec`；E 的图形轨只在真有 `plot` 时出 |
| 4 | 例题梯度 **W1 完整（每步「为什么？先想 5 秒」）→ W2 补全（真输入框 + 「对答案」，**答案不印在题面旁**）→ W3 变式（答案折叠）**；⚠ 常见错误高亮块 | `_example_html` / `_faded_html` / `_variants_html` |
| 5 | 收尾：自测（既有）+ **合上书写 3 句总结** + 「对照速览」（滚回速览） | `note_page` 的 closing 卡 + `data-cmp` |
| 6 | 图上的**十字光标**（跟随鼠标/手指显示当前 (x, y)，纯 SVG + 原生 JS，打印隐藏） | `svg()` 里的 `.cross` + `_JS` 的 pointermove 段 |
| 7 | **打印档**：强制浅色 token（防暗色主题印出近白字）、打印前展开全部 `<details>`（`beforeprint`/`afterprint`）、隐藏交互件（按钮/滑杆/进度条/主题切换/十字光标）、填空留成下划线、线索栏回正文流、卡片避免跨页断开 | `@media print` 块 + `_JS` 的打印段；来源：MDN *beforeprint/afterprint*、MDN *print-color-adjust*、caniuse *::details-content*（2026-10-18，逐条见交付说明 §5） |

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
    # ⭐ 十字光标（2026-10-18）：线条 + 读数点初始隐藏（JS 跟着鼠标/手指动）。
    #    `data-*` 是**回读数据坐标**要的映射：采样点存的是 viewBox 坐标，
    #    没有这份映射 JS 只能显示屏幕坐标（对读者没有意义）。
    parts = [f'<svg class="plot" data-cross="1" data-x0="{x0:.6g}" data-x1="{x1:.6g}" '
             f'data-y0="{y0:.6g}" data-y1="{y1:.6g}" data-padl="{pad_l}" data-padt="{pad_t}" '
             f'data-iw="{iw:.6g}" data-ih="{ih:.6g}" viewBox="0 0 {w} {h}" role="img" '
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
    # 十字光标本体（初始 display:none，由 JS 显示；打印时 CSS 再藏一次）
    parts.append('<g class="cross" style="display:none">'
                 '<line x1="0" y1="0" x2="0" y2="0"/><line x1="0" y1="0" x2="0" y2="0"/>'
                 '<circle class="cpt" cx="0" cy="0" r="3.2"/><text x="0" y="0"></text></g>')
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
    ⚠️ 最后再扫一遍**整串已渲染 HTML**：`**粗体**` 允许**跨过一个公式**（串讲的加粗
       断言句里常带 `$…$`，逐段处理时首尾星号落在不同段里 → 页面上留着字面 `**`，
       2026-10-18 截图自查抓到）。星号只来自模型文本（TeX 里没有这种连星号），
       跨标签的内容直接包进 `<strong>`。
    """
    import note_math
    s = note_math.auto_tex(str(text or ""))
    out, last = [], 0
    for m in _SPAN.finditer(s):
        out.append(_prose_html(s[last:m.start()]))
        out.append(_tex_span(m.group(0)))
        last = m.end()
    out.append(_prose_html(s[last:]))
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", "".join(out), flags=re.S)


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


def _first_sentence_end(h: str) -> int:
    """在**已渲染的 HTML** 里找第一句的结束位置（。！？），跳过标签内部（属性里的标点不算）。"""
    i, n = 0, len(h)
    while i < n:
        ch = h[i]
        if ch == "<":                       # 跳过整个标签（含 data-tex 属性里的标点）
            j = h.find(">", i)
            if j < 0:
                return n
            i = j + 1
            continue
        if ch in "。！？":
            return i + 1
        i += 1
    return n


def _bold_lead(para_html: str, *, keyline: bool) -> str:
    """把段落**第一句**做成加粗锚点（"段首断言句"，2026-10-18）。

    ⚠️ 为什么由渲染器兜底：提示词里写的是「每段开头用一句完整的断言句（**会被加粗显示**）」——
       实测模型只有 0–24% 的段自己写了 `**`（econ10770 会写、econ10740/soc10020 一段都不写），
       读它的人却指望段首句就是加粗的。这句是系统对读者的承诺 → 渲染时确定性执行：
       首句没加粗就自动包 `<strong>`；`keyline`（每节第一段）另给强调色。
       首句里模型自带的 `<strong>` 先剥掉，避免 `<strong>` 套 `<strong>`。
    """
    end = _first_sentence_end(para_html)
    head, tail = para_html[:end], para_html[end:]
    head = re.sub(r"</?strong[^>]*>", "", head)
    cls = ' class="keyline"' if keyline else ""
    return f"<strong{cls}>{head}</strong>{tail}"


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
:root{--bg:#fbfaf7;--fg:#1f2328;--muted:#5c6672;--card:#fff;--line:hsla(0,0%,8%,.10);
 --code:#f2f0eb;--c1:#c2410c;--c2:#1d4ed8;--c3:#0f766e;--accent:#8a5a00;
 --warn:#b45309;--warnbg:#fdf3e4;--bad:#b42318;--badbg:#fdecea}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#15171a;--fg:#e6e3dd;
 --muted:#9aa4b0;--card:#1c1f23;--line:hsla(0,0%,100%,.09);--code:#22262c;--c1:#fb923c;
 --c2:#7dd3fc;--c3:#5eead4;--accent:#e0b155;--warn:#f0b35a;--warnbg:#33281a;--bad:#f59b91;--badbg:#3a1f1c}}
:root[data-theme=dark]{--bg:#15171a;--fg:#e6e3dd;--muted:#9aa4b0;--card:#1c1f23;
 --line:hsla(0,0%,100%,.09);--code:#22262c;--c1:#fb923c;--c2:#7dd3fc;--c3:#5eead4;
 --accent:#e0b155;--warn:#f0b35a;--warnbg:#33281a;--bad:#f59b91;--badbg:#3a1f1c}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.75 -apple-system,
 "PingFang SC","Noto Sans SC",system-ui,sans-serif}
#prog{position:fixed;top:0;left:0;height:3px;width:0;background:var(--accent);z-index:9;
 transition:width .1s linear}
@media (prefers-reduced-motion:reduce){#prog{transition:none}
 *{animation:none!important;transition:none!important}}
#topbar{position:fixed;top:0;right:0;padding:6px 10px;z-index:8;font-size:13px}
#topbar button{cursor:pointer;background:var(--code);color:var(--fg);border:none;
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
   上面几张卡片的 IntersectionObserver 还没触发 → opacity:0 → 半屏白）。
   ⭐ 2026-10-18（动效规范，值取自反编的 Claude Desktop CSS）：进入动画换成 `block-enter` ——
   0–30% 整体不可见（opacity 0，blur+下沉+缩放就在这里发生），30–100% 才是可见的
   淡入锐化。scale(.9) 只作用在不可见的那 30%，**保留**（不会在可见阶段跳）。 */
.reveal{opacity:1;transform:none}
html.js .reveal{opacity:0;transform:translateY(5px)}
.reveal.in{animation:.3s cubic-bezier(.22,1,.36,1) both block-enter}
@keyframes block-enter{
 0%{opacity:0;filter:blur(4px);transform:translateY(5px) scale(.9)}
 30%{opacity:0;filter:blur(4px);transform:none}
 100%{opacity:1;filter:none;transform:none}}
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
/* ---- 版式 A（作者 2026-10-18 拍板，mockup tab A）---- */
.over{background:var(--card);border:1px solid var(--line);border-radius:12px;
 padding:14px 18px;margin:14px 0}
.over h3{margin:0 0 8px;font-size:18px}
.over ul{margin:4px 0;padding-left:20px}
.story-h{margin:14px 0 6px}
.story p{max-width:46em;margin:0 0 10px}
.story a{color:var(--fg);text-decoration:none;border-bottom:1px dotted var(--muted)}
.sched-line{margin:8px 0 0}
ul.sched{display:flex;flex-wrap:wrap;gap:8px;margin:6px 0 0;padding:0;list-style:none}
ul.sched li{border:1px solid var(--line);border-radius:999px;padding:2px 10px;
 font-size:14px;background:var(--code)}
.grid{display:grid;grid-template-columns:230px minmax(0,1fr);gap:16px;align-items:start;
 margin:14px 0}
.grid.solo{grid-template-columns:minmax(0,1fr)}
.grid>section.card{margin:0}
aside.cue{position:sticky;top:64px;background:var(--card);border:1px solid var(--line);
 border-radius:12px;padding:12px 14px;font-size:14px;
 max-height:calc(100vh - 84px);overflow:auto}
aside.cue p{margin:0 0 6px}
aside.cue .q{border-left:3px solid var(--c2);padding-left:8px;margin:0 0 10px}
aside.cue .t{color:var(--muted)}
@media (max-width:760px){.grid{grid-template-columns:minmax(0,1fr)}
 aside.cue{position:static;max-height:none;overflow:visible}}
/* ⭐ 内侧滚动容器的边缘渐隐（7 档、28px，值取自反编的 Claude Desktop CSS）：
   零 JS —— 滚动驱动动画把 --fade-top/--fade-bot 绑在**自己的**滚动进度上，
   `@supports` 挡住的浏览器就是静态无渐隐（可接受的回退）。打印时 mask 关掉。 */
@property --fade-top{syntax:'<length>';inherits:false;initial-value:0px}
@property --fade-bot{syntax:'<length>';inherits:false;initial-value:0px}
@keyframes fade-top-in{from{--fade-top:0px}to{--fade-top:28px}}
@keyframes fade-bot-out{from{--fade-bot:28px}to{--fade-bot:0px}}
@supports (animation-timeline: scroll(self block)){
 .cue-scroll{
  animation:fade-top-in linear both,fade-bot-out linear both;
  animation-timeline:scroll(self block),scroll(self block);
  animation-range:0 48px,calc(100% - 48px) 100%;
  mask-image:linear-gradient(to bottom,transparent,
   rgba(0,0,0,.05) calc(var(--fade-top)*.15),rgba(0,0,0,.15) calc(var(--fade-top)*.30),
   rgba(0,0,0,.30) calc(var(--fade-top)*.45),rgba(0,0,0,.50) calc(var(--fade-top)*.60),
   rgba(0,0,0,.70) calc(var(--fade-top)*.75),rgba(0,0,0,.85) calc(var(--fade-top)*.87),
   #000 var(--fade-top),#000 calc(100% - var(--fade-bot)),
   rgba(0,0,0,.85) calc(100% - var(--fade-bot)*.87),rgba(0,0,0,.70) calc(100% - var(--fade-bot)*.75),
   rgba(0,0,0,.50) calc(100% - var(--fade-bot)*.60),rgba(0,0,0,.30) calc(100% - var(--fade-bot)*.45),
   rgba(0,0,0,.15) calc(100% - var(--fade-bot)*.30),rgba(0,0,0,.05) calc(100% - var(--fade-bot)*.15),
   transparent);
  -webkit-mask-image:linear-gradient(to bottom,transparent,
   rgba(0,0,0,.05) calc(var(--fade-top)*.15),rgba(0,0,0,.15) calc(var(--fade-top)*.30),
   rgba(0,0,0,.30) calc(var(--fade-top)*.45),rgba(0,0,0,.50) calc(var(--fade-top)*.60),
   rgba(0,0,0,.70) calc(var(--fade-top)*.75),rgba(0,0,0,.85) calc(var(--fade-top)*.87),
   #000 var(--fade-top),#000 calc(100% - var(--fade-bot)),
   rgba(0,0,0,.85) calc(100% - var(--fade-bot)*.87),rgba(0,0,0,.70) calc(100% - var(--fade-bot)*.75),
   rgba(0,0,0,.50) calc(100% - var(--fade-bot)*.60),rgba(0,0,0,.30) calc(100% - var(--fade-bot)*.45),
   rgba(0,0,0,.15) calc(100% - var(--fade-bot)*.30),rgba(0,0,0,.05) calc(100% - var(--fade-bot)*.15),
   transparent);}
}
.qec{border:1px solid var(--line);border-radius:10px;margin:10px 0;overflow:hidden}
.qec>div{padding:8px 12px}
.qec>div+div{border-top:1px solid var(--line)}
.qec .lab{font-size:12px;font-weight:700;letter-spacing:.06em;color:var(--muted);margin-right:6px}
.qec .story p{margin:6px 0}
/* 重点先行：第一段的开头断言句（= 本节的重点）用强调色单独标出 */
.qec .story .keyline{color:var(--accent)}
.tracks{display:flex;gap:6px;flex-wrap:wrap;margin:6px 0}
.tracks button{cursor:pointer;border:none;background:var(--code);color:var(--fg);
 border-radius:6px;padding:3px 10px;font:inherit;font-size:14px}
.tracks button[aria-pressed=true]{color:var(--c3);font-weight:600}
/* ⭐ 展开动画（2026-10-18，值取自反编的 Claude Desktop CSS）：grid-template-rows 0fr↔1fr。
   ⚠️ 用 `html.js` 门控做**渐进增强**：没有 JS 时 `.acc` 默认是开的（正文必须读得到 ——
   模块纪律），有 JS 才折起来由按钮开合；打印档无条件强制全开。 */
.acc{display:grid;grid-template-rows:1fr;opacity:1;
 transition:grid-template-rows .3s cubic-bezier(.19,1,.22,1),opacity .2s ease-out}
.acc>div{overflow:hidden}
html.js .acc{grid-template-rows:0fr;opacity:0}
html.js .acc.open{grid-template-rows:1fr;opacity:1}
@media (prefers-reduced-motion:reduce){.acc{transition:none}}
button.why{cursor:pointer;border:none;background:var(--code);color:var(--accent);
 border-radius:6px;padding:2px 8px;font:inherit;font-size:13px;margin:4px 0}
.pit{background:var(--warnbg);border-left:3px solid var(--warn);border-radius:8px;
 padding:8px 12px;margin:12px 0}
.pit b{color:var(--warn)}
.pit ul{margin:4px 0;padding-left:20px}
input.blank{width:6em;font:inherit;font-size:15px;padding:0 4px;border:none;
 border-bottom:1.5px solid var(--muted);background:transparent;color:var(--fg);text-align:center}
input.blank.ok{border-color:var(--c3);color:var(--c3)}
input.blank.no{border-color:var(--bad);color:var(--bad)}
p.blankrow{margin:4px 0}
.btn{cursor:pointer;border:none;background:var(--code);color:var(--fg);
 border-radius:6px;padding:4px 12px;font:inherit;font-size:14px}
/* 「展开全部步骤」原来漏了样式（原生灰框）—— 和其他控件同一条填充式规则（2026-10-18 Δ④） */
button#expand{cursor:pointer;border:none;background:var(--code);color:var(--fg);
 border-radius:6px;padding:4px 12px;font:inherit;font-size:14px}
/* 屏/纸两版提示文案：屏上显示、纸上隐藏（纸版规则在下面的打印块里） */
.printonly{display:none}
textarea.sum{width:100%;min-height:84px;font:inherit;font-size:15px;border:none;
 border-radius:8px;background:var(--code);color:var(--fg);padding:8px;box-sizing:border-box}
/* 数字对齐：时间戳 / 排程日期 / 计数 / 表格数值列（tabular-nums 的适用范围按 A3） */
code,ul.sched li,th,td,.tick,.q .a,[data-checkmsg]{font-variant-numeric:tabular-nums}
/* 按钮按压反馈（A7）：按下快、回弹带一点过冲；reduced-motion 下由全局规则关掉 */
button{transition:transform .3s cubic-bezier(.34,1.56,.64,1)}
button:active{transform:scale(.975);transition-duration:.08s;transition-timing-function:ease-out}
svg.plot .cross line{stroke:var(--muted);stroke-width:1;stroke-dasharray:3 3}
svg.plot .cross .cpt{fill:var(--accent);stroke:var(--card);stroke-width:1.5}
svg.plot .cross text{fill:var(--fg);font-size:11px}
/* ⭐ 打印档（2026-10-18，§print）：强制浅色 token —— 系统/手动暗色时打印底色仍是白的，
   暗色 token 会把字印成近白（近不可见）。选择器把三种亮暗定义都罩住（`:not()` 那条
   来自 prefers-color-scheme:dark 分支，比 `:root` 的优先级高，只写 `:root` 压不住）。 */
@media print{
 :root,:root[data-theme],:root[data-theme=dark],:root:not([data-theme=light]){
  --bg:#fff;--fg:#111;--muted:#444;--card:#fff;--line:#b9b9b9;--code:#f5f5f5;
  --c1:#9a3412;--c2:#1e40af;--c3:#0f766e;--accent:#7a5200;
  --warn:#92400e;--warnbg:#fdf3e4;--bad:#991b1b;--badbg:#fdecea;color-scheme:light}
 @page{margin:16mm}
 body{background:#fff;color:#111;print-color-adjust:exact;-webkit-print-color-adjust:exact}
 /* 交互件藏起来；填空保留成**空白下划线**（印出来可以用笔写） */
 #prog,#topbar,.slider,.tracks,.btn,button.why,textarea.sum,#expand{display:none!important}
 .cross{display:none!important}
 /* 屏/纸两版提示文案（2026-10-18 Δ④）：纸上没有「对答案」按钮可点 */
 .screenonly{display:none!important}
 .printonly{display:inline!important}
 input.blank{border-bottom:1px solid #333;width:6em}
 /* 「为什么」的折叠动画：纸上**无条件全开**（不靠 JS 的 beforeprint） */
 .acc{grid-template-rows:1fr!important;opacity:1!important;transition:none!important}
 /* 线索栏不再是 sticky；回到窄屏那套（块 → 节上方）；渐隐遮罩关掉 */
 .grid{display:block}
 aside.cue{position:static;margin:0 0 8px;max-height:none;overflow:visible;
  mask-image:none;-webkit-mask-image:none}
 details,figure.fig,.q,.qec>div{break-inside:avoid}
 h2,h3,h4{break-after:avoid}
 details{border-style:solid}
 [data-pane]{display:block!important}
 .story a{border-bottom:none;color:inherit}
 a[href^="http"]::after{content:" (" attr(href) ")";font-size:85%;word-break:break-all}
 html.js .reveal{opacity:1!important;transform:none!important}
}
"""

_JS = r"""
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
 // ---- 版式 A：为什么按钮 / 线索轨切换 / 补全题对答案 / 写总结对照速览 ----
 d.addEventListener('click',function(e){
  var wy=e.target.closest('button.why');
  if(wy){
   var panel=d.getElementById(wy.getAttribute('data-acc')||'');
   if(panel){
    var on=panel.classList.toggle('open');
    wy.setAttribute('aria-expanded',on?'true':'false');
   }
   return;
  }
  var tr=e.target.closest('button[data-tr]');
  if(tr){
   var qec=tr.closest('.qec'); if(!qec) return;
   qec.querySelectorAll('button[data-tr]').forEach(function(x){
    x.setAttribute('aria-pressed',x===tr?'true':'false')});
   qec.querySelectorAll('[data-pane]').forEach(function(p){
    p.hidden=p.getAttribute('data-pane')!==tr.getAttribute('data-tr')});
   return;
  }
  var ck=e.target.closest('button[data-check]');
  if(ck){
   var cid=ck.getAttribute('data-check'), ok=0, n=0;
   d.querySelectorAll('input[id^="'+cid+'-"]').forEach(function(i){
    n++;
    var good=(i.value||'').replace(/\s/g,'')===(i.getAttribute('data-ans')||'').replace(/\s/g,'');
    i.classList.toggle('ok',good); i.classList.toggle('no',!good);
    if(!good) i.setAttribute('placeholder',i.getAttribute('data-ans')||'');
    if(good) ok++;
   });
   var msg=d.querySelector('[data-checkmsg="'+cid+'"]');
   if(msg) msg.textContent='对了 '+ok+'/'+n+'，错的空已在框里提示答案';
   return;
  }
  var cmp=e.target.closest('button[data-cmp]');
  if(cmp){var go=d.querySelector(cmp.getAttribute('data-cmp'));
   if(go) go.scrollIntoView({behavior:reduce?'auto':'smooth'});}
 });
 // ⭐ 打印：所有 details（步骤/为什么/答案/前置）先展开，打印完还原 ——
 //   折叠的 <details> 内容在打印里根本不渲染（`details{open:true}` 不是合法 CSS）。
 //   MDN beforeprint/afterprint 正是给「打印前改内容、打印后还原」用的。
 var printed=[];
 window.addEventListener('beforeprint',function(){
  printed=[];
  d.querySelectorAll('details').forEach(function(x){
   if(!x.open){x.open=true; printed.push(x);}
  });
 });
 window.addEventListener('afterprint',function(){
  printed.forEach(function(x){x.open=false}); printed=[];
 });
 // 十字光标：鼠标/手指移到图上 → 显示最近的 (x, y)。纯 SVG + 原生 JS，不引库。
 // 取**离指针最近的采样点**（不是指针自己）——读数永远是曲线上的值；拖滑杆重画
 // points 后照常工作（每次移动现读 points）。打印时由 CSS 藏掉。
 d.querySelectorAll('svg.plot[data-cross]').forEach(function(sv){
  var cross=sv.querySelector('.cross'); if(!cross) return;
  var ln=cross.querySelectorAll('line'), dot=cross.querySelector('.cpt'),
      tx=cross.querySelector('text');
  var x0=+sv.dataset.x0,x1=+sv.dataset.x1,y0=+sv.dataset.y0,y1=+sv.dataset.y1,
      padl=+sv.dataset.padl,padt=+sv.dataset.padt,iw=+sv.dataset.iw,ih=+sv.dataset.ih,
      vb=sv.viewBox.baseVal;
  function move(ev){
   var r=sv.getBoundingClientRect(); if(!r.width) return;
   var vx=(ev.clientX-r.left)/r.width*vb.width,
       vy=(ev.clientY-r.top)/r.height*vb.height, best=null, bd=1e9;
   sv.querySelectorAll('polyline').forEach(function(pl){
    var s=(pl.getAttribute('points')||'').split(/\s+/);
    for(var i=0;i<s.length;i++){
     if(!s[i]) continue;
     var xy=s[i].split(','), dx=+xy[0]-vx, dy=+xy[1]-vy, dd=dx*dx+dy*dy;
     if(dd<bd){bd=dd; best=xy;}
    }
   });
   if(!best) return;
   var px=+best[0], py=+best[1];
   ln[0].setAttribute('x1',px); ln[0].setAttribute('x2',px);
   ln[0].setAttribute('y1',padt); ln[0].setAttribute('y2',padt+ih);
   ln[1].setAttribute('x1',padl); ln[1].setAttribute('x2',padl+iw);
   ln[1].setAttribute('y1',py); ln[1].setAttribute('y2',py);
   dot.setAttribute('cx',px); dot.setAttribute('cy',py);
   tx.setAttribute('x',Math.min(px+8,padl+iw-52));
   tx.setAttribute('y',Math.max(py-6,padt+12));
   tx.textContent='('+(x0+(px-padl)/iw*(x1-x0)).toFixed(2)
                 +', '+(y0+(1-(py-padt)/ih)*(y1-y0)).toFixed(2)+')';
   cross.style.display='';
  }
  sv.addEventListener('pointermove',function(ev){
   if(ev.pointerType==='touch'&&!ev.buttons) return;     // 手指划过不算，按住拖才算
   move(ev);
  });
  sv.addEventListener('pointerleave',function(){ cross.style.display='none'; });
 });
})();
"""


def _cue_html(sec: dict) -> str:
    """左栏（线索问题 + 术语）；两样都空 → 空串（不画空栏）。"""
    cues = [c for c in (sec.get("cues") or []) if c][:3]
    terms = [t for t in (sec.get("terms") or []) if t.get("en")]
    if not cues and not terms:
        return ""
    o = ['<aside class="cue cue-scroll" aria-label="线索栏">']
    if cues:
        o.append("<p><b>❓ 线索问题</b></p>")
        o += [f'<p class="q">{inline(c)}</p>' for c in cues]
    if terms:
        rows = "<br>".join(
            (f'{inline(str(t.get("zh") or "").strip())} ({html.escape(str(t.get("en")))})'
             if str(t.get("zh") or "").strip() else html.escape(str(t.get("en"))))
            for t in terms)
        o.append(f'<p class="t"><b>术语</b><br>{rows}</p>')
    o.append('<p class="muted">复习时盖住右边，只看这里自问自答</p></aside>')
    return "".join(o)


def _blank_input(uid: str, i: int, ans: str) -> str:
    """一个填空。⚠️ 答案只在 `data-ans` 属性里 —— **不许印在题面旁边**（现状的 bug）。"""
    return (f'<input class="blank" id="{uid}-{i}" aria-label="第 {i + 1} 空" '
            f'autocomplete="off" spellcheck="false" '
            f'data-ans="{html.escape(str(ans or ""), quote=True)}">')


def _faded_html(fd: dict, uid: str) -> str:
    """W2 补全题：挖空的空**做成真输入框**，「对答案」按钮由 JS 核（错空提示答案）。

    ⚠️ 现状的 bug：每个空下面直接打印 `⬜ 答案`；答案只能进 `data-ans`。
    ⚠️ 题面里本来就有 `___` 且个数对得上（且 `$…$` 闭合）→ 原地放输入框；
       否则退化成题面 + 逐条输入行（仍然不打印答案）。
    """
    q = str(fd.get("q") or "").strip()
    blanks = [str(b) for b in (fd.get("blanks") or []) if str(b or "").strip()]
    if not q or not blanks:
        return ""
    parts = re.split(r"_{2,}", q)
    if len(parts) - 1 == len(blanks) and q.count("$") % 2 == 0:
        chunks: list = []
        for i, seg in enumerate(parts):
            chunks.append(inline(seg))
            if i < len(blanks):
                chunks.append(_blank_input(uid, i, blanks[i]))
        body = "<p>" + "".join(chunks) + "</p>"
    else:
        body = f"<p>{inline(q)}</p>" + "".join(
            f'<p class="blankrow">{i}. {_blank_input(uid, i - 1, b)}</p>'
            for i, b in enumerate(blanks, 1))
    return (f'<h4>W2 补全题'
            f'<span class="screenonly">（把关键步骤填上，再点「对答案」）</span>'
            f'<span class="printonly">（用笔填上，再对照上方例题的解法核对）</span></h4>'
            f'<div class="faded">{body}'
            f'<p><button class="btn" type="button" data-check="{uid}">对答案</button> '
            f'<span class="muted" data-checkmsg="{uid}"></span></p></div>')


def _example_html(ex: dict, uid: str) -> str:
    """W1 完整例题：每步一个 `<details class="step">`；「为什么」是**带动画的按钮开合**
    （`aria-expanded` + `.acc` grid 动画，2026-10-18 追认）——点开一步不会顺带剧透"为什么"。"""
    o = [f'<h4>W1 完整例题 · {inline(ex.get("title") or "教授的例题")}</h4>']
    if ex.get("prompt"):
        o.append(f'<p><strong>题目</strong>：{inline(ex["prompt"])}</p>')
    for i, st in enumerate(ex.get("steps") or [], 1):
        t, w = inline(st.get("text")), inline(st.get("why"))
        inner = f"<p>{t}</p>"
        if w:
            pid = f"why-{uid}-{i}"
            inner += (f'<button class="why" type="button" aria-expanded="false" '
                      f'aria-controls="{pid}" data-acc="{pid}">为什么？先想 5 秒</button>'
                      f'<div class="acc" id="{pid}"><div><p>{w}</p></div></div>')
        o.append(f'<details class="step"><summary>第 {i} 步</summary>{inner}</details>')
    if ex.get("answer"):
        o.append(f'<details class="ans"><summary>答案</summary>'
                 f'<p>{inline(ex["answer"])}</p></details>')
    return "".join(o)


def _variants_html(variants: list) -> str:
    """W3 变式：题面在上、答案折在 `<details>` 里（自己从头做）。"""
    o = []
    for j, v in enumerate(variants, 1):
        body = "".join(
            f'<p>{inline(s.get("text"))}'
            + (f'<br><em>为什么：{inline(s.get("why"))}</em>' if s.get("why") else "")
            + "</p>" for s in (v.get("steps") or []))
        o.append(f'<h4>W3 变式 {j}（自己从头做）</h4><p>{inline(v.get("q"))}</p>'
                 f'<details class="ans"><summary>答案</summary>{body}'
                 + (f'<p>{inline(v.get("a"))}</p>' if v.get("a") else "") + "</details>")
    return "".join(o)


def _tracks_html(sec: dict, fig: str) -> str:
    """E 三种看法：**只画真的有的**（图形要真有图；代数/直觉要模型真填了）。

    ≥2 条才出切换按钮；只有一条时直接铺开（没有可切换的东西）。第一条默认选中。
    """
    items: list = []
    if fig:
        items.append(("g", "📈 图形", fig))
    if sec.get("algebra"):
        items.append(("a", "🧮 代数", f'<p>{inline(sec["algebra"])}</p>'))
    if sec.get("intuition"):
        items.append(("i", "💡 直觉", f'<p>{inline(sec["intuition"])}</p>'))
    if not items:
        return ""
    o = []
    if len(items) > 1:
        o.append('<div class="tracks">' + "".join(
            f'<button type="button" data-tr="{k}" '
            f'aria-pressed="{"true" if i == 0 else "false"}">{name}</button>'
            for i, (k, name, _c) in enumerate(items)) + "</div>")
    for i, (k, _name, content) in enumerate(items):
        o.append(f'<div data-pane="{k}"{" hidden" if i else ""}>{content}</div>')
    return "".join(o)


def _section_html(sec: dict, idx: int, *, reveal: bool = True, fig: str = "") -> str:
    cls = "reveal" if reveal else ""
    o = [f'<section class="card {cls}" id="kp{idx}">',
         f'<h3>{idx}. {inline(sec.get("heading"))}'
         + (f' <code>🎙 {html.escape(str(sec.get("ts")))}</code>' if sec.get("ts") else "")
         + "</h3>"]
    if sec.get("prereq_link"):
        o.append(f'<p>🔗 {inline(sec["prereq_link"])}</p>')
    # ── Q / 讲解 / E / C（版式 A 的节头，mockup tab A）────────────────────
    o.append('<div class="qec">')
    if sec.get("q"):
        o.append(f'<div><span class="lab">Q 本节回答</span>{inline(sec["q"])}</div>')
    story = ['<div class="story"><span class="lab">讲解</span>']
    for i, para in enumerate(sec.get("prose") or []):
        # ⭐ 重点先行（作者 2026-10-18）：每段的开头句就是**断言句**（渲染器确保它被加粗，
        #    模型没写 `**` 也加粗）；第一段的那句 = **本节的重点**，再多给强调色。
        story.append(f"<p>{_bold_lead(inline(para), keyline=(i == 0))}</p>")
    if sec.get("prereq"):
        story.append(f'<details><summary>📚 前置补课（教授没讲、但要懂）</summary>'
                     f'<p>{inline(sec["prereq"])}</p></details>')
    story.append("</div>")
    o.append("".join(story))
    tracks = _tracks_html(sec, fig)
    if tracks:
        o.append(f'<div><span class="lab">E 三种看法</span>{tracks}</div>')
    if sec.get("conclusion"):
        o.append(f'<div><span class="lab">C 考试结论</span>{inline(sec["conclusion"])}</div>')
    o.append("</div>")
    # ── 例题梯度：W1 完整 → W2 补全（真输入框）→ W3 变式（答案折叠）─────
    ex = sec.get("example") or {}
    if ex.get("prompt") or ex.get("steps"):
        o.append(_example_html(ex, uid=f"ex{idx}"))
    o.append(_faded_html(sec.get("faded") or {}, uid=f"fd{idx}"))
    o.append(_variants_html(sec.get("variants") or []))
    if sec.get("pitfalls"):
        o.append('<div class="pit"><b>⚠ 常见错误</b><ul>'
                 + "".join(f"<li>{inline(p)}</li>" for p in sec["pitfalls"]) + "</ul></div>")
    if sec.get("terms"):
        o.append("<p>🔑 " + " · ".join(inline(t.get("en")) for t in sec["terms"]) + "</p>")
    if sec.get("sources"):
        o.append("<p>📎 来源：" + " · ".join(inline(s) for s in sec["sources"]) + "</p>")
    o.append("</section>")
    cue = _cue_html(sec)
    return f'<div class="{"grid" if cue else "grid solo"}">{cue}{"".join(o)}</div>'


_SEC_REF = re.compile(r"第\s*(\d{1,2}(?:\s*[、,，和与]\s*\d{1,2})*)\s*节")


def _link_sections(text_html: str, n_sections: int) -> str:
    """串讲段里的「第 2 节」「第 2、3 节」→ 链到对应小节的锚点。

    ⚠️ 在 `inline()` **之后**跑（那时正文已是 HTML）—— 只替换数字，不会碰到标签；
       编号越界（模型写错）就原样留在那儿，让人看得见，不假装链得上。
    """
    def repl(m):
        out = []
        for tok in re.split(r"(\D+)", m.group(1)):
            if tok.strip().isdigit() and 1 <= int(tok) <= n_sections:
                out.append(f'<a href="#kp{int(tok)}">{tok}</a>')
            else:
                out.append(tok)
        return "第 " + "".join(out) + " 节"
    return _SEC_REF.sub(repl, str(text_html or ""))


def _overview_html(review: dict, sections: list, dates: list) -> str:
    """顶部「五分钟速览」：一句话 + 3 takeaways + 五分钟串讲 + 复习排程。

    ⚠️ 串讲（`overview_long`）是**一次额外小调用**的产物；没有也照渲染（少那一块，
       页尾的 ⚠ 记录里会说）——「一节课的线」不该因为一次调用失败整页报废。
    """
    import note_v2
    ov = review.get("overview") or {}
    one = str(ov.get("one_line") or "").strip() or str(review.get("title_zh") or "").strip()
    takes = [str(t) for t in (ov.get("takeaways") or []) if str(t).strip()][:3]
    paras = [str(p) for p in (review.get("overview_long") or []) if str(p).strip()]
    sched = note_v2.schedule_rows(dates)
    if not (one or takes or paras or sched):
        return ""
    o = ['<div class="over" id="overview"><h3>五分钟速览</h3>']
    if one:
        o.append(f'<p><b>一句话：</b>{inline(one)}</p>')
    if takes:
        o.append("<ul>" + "".join(f"<li>{inline(t)}</li>" for t in takes) + "</ul>")
    if paras:
        o.append('<h4 class="story-h">五分钟串讲：把整节课从头讲一遍</h4><div class="story">')
        o += [f"<p>{_link_sections(inline(p), len(sections))}</p>" for p in paras]
        o.append("</div>")
    if sched:
        o.append('<p class="sched-line"><b>复习排程：</b>（笔记只写日期，不推送）</p>')
        o.append('<ul class="sched">' + "".join(
            f'<li>{html.escape(r["label"])} · {html.escape(r["date"])} —— '
            f'{html.escape(r["task"])}</li>' for r in sched) + "</ul>")
    o.append("</div>")
    return "".join(o)


def page(title: str, head: list, toc_items: list, sections_html: list, qa: list,
         plots: list, extra_html: str = "", footer: str = "",
         overview_html: str = "", closing_html: str = "") -> str:
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
{overview_html}
<nav class="toc"><strong>知识点地图</strong><ol>{toc}</ol></nav>
<p><button id="expand" type="button">展开全部步骤</button></p>
{''.join(sections_html)}
{('<h2>图</h2><div class="plots">' + ''.join(plots) + "</div>") if plots else ''}
<h2>自测（先答再揭晓）</h2>{qa_html or '<p>（本节没生成自测）</p>'}
{closing_html}
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
                # ⭐ 2026-10-18 Δ：`mismatch` 现在会被 `note_guards.annotate_mismatches`
                #    **原位标进正文**（"已修正"只有在别处真改过字才配得上 —— 原来一律写
                #    "已修正"，而正文一个字没动，读者读到的是假话）。
                st = {"ok": "✅ 一致",
                      "mismatch": ("⚠ 正文已标注" if c.get("annotated") else "⚠ 有出入"),
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
    # ── 收尾：合上书写 3 句 → 对照速览（版式 A；概念图不在这一版里）────────
    closing = ('<section class="card" id="sum3"><h3>ⓑ 合上书，写 3 句总结</h3>'
               '<label class="muted" for="sum3box">写完再点「对照速览」，看漏了什么</label>'
               '<p><textarea class="sum" id="sum3box" '
               'placeholder="1. 这节课先说……&#10;2. 关键的一步是……&#10;3. 考试要会……"></textarea></p>'
               '<p><button class="btn" type="button" data-cmp="#overview">对照速览</button></p>'
               "</section>")
    overview = _overview_html(review, sections, list(d.review_dates or []))
    return page(title, head, toc_items, secs, review.get("qa") or [], [], extra,
                footer="<p>这是<b>详细版</b>（简版是 Markdown 那一份，同目录）。</p>"
                       + warns_html,
                overview_html=overview, closing_html=closing)
