#!/usr/bin/env python3
"""3.9.7 字幕「文字稳定」包（S1 / S2 / C2 / C3 / A5）的判据。

    ClassLive.app/Contents/MacOS/python tests/test_text_stability.py

依据：Liu 等, CHI EA 2023「Modeling and Improving Text Stability in Live Captions」
（用户研究 **N=123**）—— **已显示的文字被改与疲劳/分心正相关**；做法是词级对齐后
**只对变化处**做平滑过渡，且**不破坏已有换行**。

钉住什么：
  · **C3** `_ui_font` 带上了 `palt`（标点挤压，`kTextSpacingType=22` / 比例宽度=0），
    且**行高一点都不动**、英文/数字一个像素不动、折行只少不多（`probe_palt.py` 的判据
    收成一条能跑的：长中文样本的合计行数）。
  · **C2** 开课面板课卡 / 落点条的悬停背景走 `motion.FEEDBACK_S` 过渡，
    Reduce Motion 时**瞬变**；构造那一次不许动（层上还没有值）。
  · **S1** 半句合并：反向 roll（`_roll_from < 0`）、被吞掉那一行**原地**淡出（幽灵不参与
    位移）、尾句用 `MERGE_FADE_S` 淡换、未跟随时**只淡换不位移**且视口按行高补偿。
  · **S2** 草稿逐词淡换：`text_diff` 的纯函数（拼回去逐字等于新串 / 变化段至多一段 /
    换行判据）；调整体行为（末尾追加不动画、改前面的词才动画、Reduce Motion 不动画、
    换行变了不动画）；testmode 的两个计数。
  · **A5** 已知问题的修正：一次进来 ≥2 行时，**上一条已经显示过的行**（实时行那一格）
    不许被重新计入淡入。
"""
from __future__ import annotations
import os
# 本文件钉的是**老布局**（底部单独草稿栏 + 新行从草稿处升上来）的行为; D 布局（默认, 2026-10-17）
# 的判据在 tests/test_caption_flow.py 与 tests/test_d_layout.py。老布局仍是 `CLASSLIVE_LAYOUT=classic`
# 的退路, 删它之前这些判据照跑。⚠️ 必须在 import overlay / transcript_view 之前设。
os.environ.setdefault("CLASSLIVE_LAYOUT", "classic")

import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import motion                                                         # noqa: E402
import text_diff                                                      # noqa: E402
import transcript_view as TV                                          # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  {detail}" if detail else ""))


# ══════════════════════════════════════════════════════════════════════
# S2 · 纯函数：token 对齐 + 换行判据
# ══════════════════════════════════════════════════════════════════════

def t_s2_tokens_keepform() -> None:
    print("\n--- S2 分词: 保形（拼回去逐字等于原文） ---")
    cases = [
        ("", False), ("a", False), (" the cat sat ", False),
        ("the  cat\nsat down", False), ("▸ hello there", False),
        ("经济学的边际效用递减规律", True), ("这门课讲 GDP 增长 3.5% 的原因", True),
        ("先说需求，再说供给。", True), ("两行\n折过的中文", True),
    ]
    for s, cjk in cases:
        t = text_diff.tokens(s, cjk=cjk)
        check(f"{s!r}{'(中文)' if cjk else ''} 拼回逐字相等",
              "".join(t) == s, f"{t}")
    check("空串 -> []（不是 ['']）", text_diff.tokens("") == []
          and text_diff.tokens("", cjk=True) == [])


def t_s2_diff_runs() -> None:
    print("\n--- S2 diff_runs: 变化段的对齐 ---")
    # 末尾追加：整串一段、无变化 —— 调用方据此**不做动画**（与改前逐字一致）
    r = text_diff.diff_runs("▸ the cat", "▸ the cat sat")
    check("末尾追加 -> 只有一段且 changed=False", r == [("▸ the cat sat", False)], str(r))
    check("全新的串（旧串为空）同样算追加", text_diff.diff_runs("", "hello") == [("hello", False)])

    # 改了前面的词：只那一段 changed（前缀/后缀都不进变化段）
    r = text_diff.diff_runs("I want to go home", "I want to go back home")
    check("⭐ 中间插入一个词 -> 只有那一段 changed",
          r == [("I want to go ", False), ("back ", True), ("home", False)], str(r))
    r = text_diff.diff_runs("the price is 12.5", "the price is 18.7")
    check("改数字 -> 只数字那一段 changed",
          r == [("the price is ", False), ("18.7", True)], str(r))
    r = text_diff.diff_runs("先说需求，再说供给。", "先说供给，再说需求。", cjk=True)
    check("中文改字走 BudouX 词组（不是逐字）",
          any(ch for _, ch in r) and "".join(t for t, _ in r) == "先说供给，再说需求。", str(r))
    check("中文：未变的词组一个都不在变化段里（首尾对齐）",
          r[0][1] is False and r[-1][0][-3:] == "需求。", str(r))

    # 不变量：拼回去逐字等于新串（渲染层直接拿它拼属性串，差一个字就会串味）
    bad = []
    for old, new, cjk in [("a b", "a c", False), ("abc", "abd", False),
                          ("一句话。", "另一句。", True), ("x", "", False),
                          ("aa bb cc", "aa xx bb cc", False)]:
        rr = text_diff.diff_runs(old, new, cjk=cjk)
        if "".join(t for t, _ in rr) != new:
            bad.append((old, new, rr))
        if sum(1 for _, ch in rr if ch) > 1:
            bad.append(("变化段不止一段", old, new, rr))
    check("⭐ 不变量：拼回 = 新串，且变化段至多一段", not bad, str(bad[:2]))
    check("新串为空 -> 空 runs（没有可淡入的东西）", text_diff.diff_runs("a", "") == [])


def t_s2_same_breaks() -> None:
    print("\n--- S2 same_breaks: 换行位置判据 ---")
    old = "the first line\nand the second one"
    new = "the first line\nand the second one plus more"
    r = text_diff.diff_runs(old, new)
    check("同一行里变长 -> True（可以淡换）", text_diff.same_breaks(old, new, r) is True)

    # 行数变了 -> False（换行一动, 未变化的词会整块搬到别的行上）
    old2 = "a b c"
    new2 = "a b\nc d"
    check("行数变了 -> False", text_diff.same_breaks(old2, new2, text_diff.diff_runs(old2, new2)) is False)

    # 未变化的段换了行（它前面多出一个换行）-> False。⚠️ 只有"变化段里的换行数变了"
    # 才能造成这件事, 而那必然让总行数也变 —— 所以这条判据本质上就是"行数有没有变"。
    old3 = "aa bb cc"
    new3 = "aa\nbb cc"
    r3 = text_diff.diff_runs(old3, new3)
    check("未变化的段前面多了一个换行 -> False",
          text_diff.same_breaks(old3, new3, r3) is False, str(r3))
    # 断点在**变化段内部**挪了、而未变化的词还在原来那一行 -> True
    # （那一段本来就要整段淡换, 而未变化的词没换行 —— 这正是允许动画的形状）
    old4 = "aa bb\ncc dd"
    new4 = "aa\nbb cc dd"
    r4 = text_diff.diff_runs(old4, new4)
    check("断点在变化段里挪、未变化的词没换行 -> True",
          text_diff.same_breaks(old4, new4, r4) is True, str(r4))


# ══════════════════════════════════════════════════════════════════════
# C3 · 中文标点挤压（palt）
# ══════════════════════════════════════════════════════════════════════

#: 长中文样本（课堂范式；与 `scratchpad/stab/probe_palt.py` 同一批里抽的 20 句）。
CJK_SAMPLES = [
    "政府通过调整税率来影响总需求，这是一种典型的财政政策工具。",
    "如果边际消费倾向是零点八，那么乘数就是五，产出的变化会放大五倍。",
    "我们先把需求曲线画出来：价格越高、需求量越低，这是在其他条件不变的前提下。",
    "央行提高利率，投资会下降，进而通过乘数效应影响总产出和就业。",
    "短期里价格是黏性的，所以总需求的波动会先反映在产出上，而不是价格上。",
    "货币政策传导有三个环节：利率、信贷、以及资产价格，任何一个断了都会打折扣。",
    "垄断厂商的边际收益曲线一定在需求曲线下方，因为它必须降价才能多卖一单位。",
    "完全竞争市场里，单个厂商面对的需求曲线是一条水平线，它是价格接受者。",
    "公共品的两个特征是：非排他性和非竞争性，两者同时满足才叫纯公共品。",
    "我们用一个简单的博弈来说明：两家厂商同时决定产量，这是古诺模型。",
    "囚徒困境告诉我们，个人理性有时候会导致集体非理性，这是整门课的重点。",
    "国内生产总值统计的是最终产品，中间投入不能重复计算，这一点最容易错。",
    "失业率的分母是劳动力，不是总人口，这个区别在很多考试里都会考。",
    "经济周期不是规律的波动，它只是事后划出来的阶段，这一点要记清楚。",
    "汇率的决定有多种理论：购买力平价、利率平价、以及国际收支说。",
    "这道题的陷阱在于，它把「价格上升」和「通货膨胀」当成同一件事。",
    "生产可能性边界上的点都是有效率的，内部的点说明资源没有被充分利用。",
    "比较优势说的是相对成本，不是绝对成本，这一点是最容易混的地方。",
    "关税的福利分析要看三块：生产者剩余、消费者剩余、以及政府收入。",
    "注意需求的价格弹性和收入弹性，后者可以是负的，那叫劣等品。",
]
CJK_WIDTHS = [628.0, 440.0, 360.0]


def _font_without_palt(O):
    """`_ui_font` 的**改前形状**: 只摘掉 palt 那一项（走的是同一条构造路径）。"""
    real = O.UI_FONT_FEATURES
    try:
        O.UI_FONT_FEATURES = tuple(f for f in real if f[0] != 22)
        return O._ui_font(18.0)
    finally:
        O.UI_FONT_FEATURES = real


def t_c3_palt() -> None:
    print("\n--- C3 中文 `palt`（标点挤压） ---")
    try:
        import overlay as O
        from AppKit import NSFont
    except Exception as e:                                          # noqa: BLE001
        check("AppKit / overlay 可用", False, f"{type(e).__name__}: {e}")
        return

    check("`_ui_font` 里写着两项 feature（tnum + palt）",
          O.UI_FONT_FEATURES == ((6, 0), (22, 0)), str(O.UI_FONT_FEATURES))

    # "改前"那一份 = **同一把尺**只把 palt 那一项摘掉（自己另建一份字体构造函数量出来的
    # 数不可信: 见 `overlay.UI_FONT_FEATURES` 上面记的 CoreText 缓存那条 —— 先摸过一把
    # 没挂 feature 的同尺寸字体, 之后量到的就是没挂 feature 的宽度）。
    before = _font_without_palt(O)
    after = O._ui_font(18.0)

    hb, ha = O._measure_text_h("Hg", 1e4, before), O._measure_text_h("Hg", 1e4, after)
    check("⭐ 行高不变（LINE_H / ROW_ZH_H / LINE_TIERS 继续成立）",
          abs(hb - ha) < 0.01, f"{hb:.3f} -> {ha:.3f}")
    check("英文/数字的**宽度**一个像素都不动",
          all(abs(O._text_width(s, before) - O._text_width(s, after)) < 0.01
              for s in ("MC = 12.5", "the marginal propensity to consume", "GDP 3.5%")))

    worse, tot0, tot1 = [], 0, 0
    for w in CJK_WIDTHS:
        for s in CJK_SAMPLES:
            n0 = int(round(O._measure_text_h(s, w, before) / O.LINE_H))
            n1 = int(round(O._measure_text_h(s, w, after) / O.LINE_H))
            tot0 += n0
            tot1 += n1
            if n1 > n0:
                worse.append((w, s[:12], n0, n1))
    check(f"⭐ 长中文折行行数只许相同或更少（{len(CJK_SAMPLES)} 句 × {len(CJK_WIDTHS)} 宽度）",
          not worse and tot1 <= tot0, f"{tot0} 行 -> {tot1} 行; 变多 {worse[:2]}")
    # 真的省了宽度（否则上面那条可能恒真 —— palt 没生效时也"只少不多"）
    w0 = O._text_width("政策工具，效果明显。", before)
    w1 = O._text_width("政策工具，效果明显。", after)
    check("⭐ 对照：带标点的中文句子确实变窄了（palt 真生效，不是恒真）", w1 < w0 - 0.2,
          f"{w0:.2f} -> {w1:.2f}")
    check("对照：去标点省得更多（说明省的就是标点那点空白）",
          O._text_width("政策工具效果明显", before) < w1 - 1.0)


# ══════════════════════════════════════════════════════════════════════
# C2 · 开课面板课卡悬停
# ══════════════════════════════════════════════════════════════════════

class _FakeLayer:
    def __init__(self) -> None:
        self.keys: list = []
        self.bg = "CARD"

    def backgroundColor(self):
        return self.bg

    def setBackgroundColor_(self, c):                               # noqa: N802
        self.bg = c

    def addAnimation_forKey_(self, anim, key):                      # noqa: N802
        self.keys.append((str(key), anim))


class _FakeView:
    def __init__(self) -> None:
        self.lay = _FakeLayer()

    def layer(self):
        return self.lay


def t_c2_hover() -> None:
    print("\n--- C2 课卡悬停用 FEEDBACK_S 过渡 ---")
    check("motion.FEEDBACK_S == 0.12（报告 §5 C2 指定这一档）",
          abs(motion.FEEDBACK_S - 0.12) < 1e-9, str(motion.FEEDBACK_S))

    real = motion.reduce_motion
    try:
        motion.reduce_motion = lambda: False
        v = _FakeView()
        motion.fade_property(v.layer(), "backgroundColor", motion.FEEDBACK_S)
        check("⭐ 悬停：挂了一次 `backgroundColor` 的显式动画",
              [k for k, _ in v.lay.keys] == ["fade_backgroundColor"], str(v.lay.keys))
        check("时长 = FEEDBACK_S（不是 CA 默认的 0.25）",
              abs(float(v.lay.keys[0][1].duration()) - motion.FEEDBACK_S) < 1e-9,
              str(v.lay.keys[0][1].duration()))
        check("起点 = 迁入前的模型值（不是 nil/'透明'）",
              v.lay.keys[0][1].fromValue() == "CARD", str(v.lay.keys[0][1].fromValue()))

        motion.reduce_motion = lambda: True
        v2 = _FakeView()
        motion.fade_property(v2.layer(), "backgroundColor", motion.FEEDBACK_S)
        check("Reduce Motion：一条动画都不挂（瞬变）", v2.lay.keys == [], str(v2.lay.keys))
    finally:
        motion.reduce_motion = real

    src = pathlib.Path(HERE / "entry_panel.py").read_text(encoding="utf-8")
    check("课卡与落点条**共用**这一档（两处都写了 FEEDBACK_S）",
          src.count("motion.FEEDBACK_S") >= 2, str(src.count("motion.FEEDBACK_S")))
    check("初始那一次不带动画（`_bg(CARD_FILL_A)` / `_strip_bg(CARD_FILL_A)` 没有 animate）",
          "_bg(CARD_FILL_A)\n" in src and "_strip_bg(CARD_FILL_A)\n" in src)


# ══════════════════════════════════════════════════════════════════════
# S1 · 半句合并的过渡（真机路径）
# ══════════════════════════════════════════════════════════════════════

def _overlay():
    from overlay import Overlay
    o = Overlay()
    now = [100.0]
    o._clock = lambda: now[0]
    o._tv._clock = lambda: now[0]
    o._tv._reduce_motion = lambda: False
    o._tv._animate_ok = lambda: True
    return o, now


def _small(o) -> None:
    """窄窗档位: 行数档位跟着变, 池与行高一起变 —— 与 test_fontmotion_A 同一手。"""
    import overlay as O
    o._width = O.MIN_WIDTH
    o._apply_row_metrics(O.MIN_WIDTH)


def _levels(tv) -> dict:
    """当前屏上每个层号 -> 中文那一格的文本。"""
    return {s["level"]: s["zh"].stringValue() for s in tv._slots
            if s["level"] is not None and not s.get("ghost")}


def _y_of(tv, text):
    """屏上那一段文字所在那一格的底边 y（找不到 -> None）。"""
    for sl in tv._slots:
        if sl["zh"].stringValue() == text:
            return sl["zh"].frame().origin.y
    return None


def _alpha_at(tv, level):
    for sl in tv._slots:
        if sl["level"] == level and not sl.get("ghost"):
            return sl.get("alpha", 1.0)
    return None


def t_s1_merge_roll() -> None:
    print("\n--- S1 半句合并: 反向 roll + 原地淡出 + 尾句淡换 ---")
    try:
        o, now = _overlay()
    except Exception as e:                                          # noqa: BLE001
        check("Overlay 可构造（AppKit）", False, f"{type(e).__name__}: {e}")
        return
    try:
        _small(o)
        for i in range(4):
            o.finalize(f"sentence number {i}", f"第{i}句。")
        now[0] += motion.MOVE_S + 0.05
        o._tv.tick()
        tv, rh = o._tv, o._tv._row_h
        before = dict(_levels(tv))
        frames_before = {j: (s["zh"].frame(), s["en"].frame())
                         for j, s in enumerate(tv._slots)}
        y_above = _y_of(tv, before[2])
        check("（前提）合并前四行、不在滚", not tv.rolling and before.get(0) == "第3句。",
              str(before))

        ids = list(o._log.ids)
        ok = o.replace_merge(ids[-2], ids[-1], "merged.", "合并。")
        check("合并落地（尾句换成合并译文、吞掉最后一行）",
              ok and o._history == [("sentence number 0", "第0句。"),
                                    ("sentence number 1", "第1句。"),
                                    ("merged.", "合并。")])

        check("⭐ 起跑的是**合并过渡**（反向 roll，不是上滚）",
              tv.rolling and tv._roll_from == -rh and tv._roll_off == -rh,
              f"from={tv._roll_from} off={tv._roll_off} row_h={rh}")
        check("淡入名单只有**尾句那一行**（层 0），时长 STATE_S",
              tv._roll_fade_lo == 0 and tv._roll_fade_n == 1
              and tv._roll_fade_s == motion.STATE_S, str(tv._roll_fade_s))
        check("尾句新文字的 α 从 0 起跑（未变的下面的行全 α1）",
              tv._roll_alpha == 0.0 and _alpha_at(tv, 0) == 0.0
              and _alpha_at(tv, 2) == 1.0, f"{_alpha_at(tv, 0)} / {_alpha_at(tv, 2)}")

        # ① 被吞掉那一行: 那一**格本身**变幽灵, 文本一个字节都没重写、frame 原地不动
        gj = tv._ghost_j
        check("⭐ 被吞掉那一行**原地**淡出（幽灵占着老那一格、文本原样）",
              gj is not None and tv._slots[gj]["ghost"]
              and tv._slots[gj]["zh"].stringValue() == before[0],
              f"slot={gj} text={tv._slots[gj]['zh'].stringValue()!r}" if gj is not None else "没有幽灵")
        check("⭐ 幽灵的 frame 与合并前**逐像素相同**（不参与位移）",
              gj is not None and frames_before[gj] == (tv._slots[gj]["zh"].frame(),
                                                       tv._slots[gj]["en"].frame()))

        # ② 上面的行: 起跑那一帧还在**老位置**（内容没动），随后滑下来一格
        check("起跑那一帧上面的行还在老位置",
              _y_of(tv, before[2]) is not None and abs(_y_of(tv, before[2]) - y_above) < 0.6,
              f"{y_above:.1f} -> {_y_of(tv, before[2])}")
        check("上面的行已经落到**下一层**（层号减 1 = 少的那一行没了）",
              _levels(tv).get(1) == before[2] and _levels(tv).get(0) == "合并。",
              str(_levels(tv)))
        check("而尾句那一格被摆到**高一个行高**（接下来滑下来）",
              abs(tv._slots[[j for j, s in enumerate(tv._slots)
                             if s["level"] == 0 and not s.get("ghost")][0]]
                  ["zh"].frame().origin.y - rh) < 0.6,
              f"y={_y_of(tv, '合并。'):.1f} row_h={rh:.1f}")

        now[0] += motion.STATE_S
        tv.tick()
        check("⭐ 到 STATE_S 时新文字已经满 α（位移还在走）",
              tv._roll_alpha == 1.0 and tv._roll_off != 0.0,
              f"α={tv._roll_alpha} off={tv._roll_off:.2f}")
        now[0] += motion.MOVE_S
        tv.tick()
        check("位移走完: 落位、幽灵收起、不再滚动",
              not tv.rolling and tv._roll_off == 0.0 and tv._ghost_j is None)
        check("⭐ 上面那些行**恰好下移一个行高**（不多不少）",
              abs(_y_of(tv, before[2]) - (y_above - rh)) < 0.6,
              f"{y_above:.1f} -> {_y_of(tv, before[2]):.1f} (期望 {y_above - rh:.1f})")
        check("终态: 合并译文在最底那一格、再往上是没被动的两行",
              _levels(tv).get(0) == "合并。" and _levels(tv).get(1) == before[2]
              and _levels(tv).get(2) == before[3], str(_levels(tv)))
        check("没有半透明残留（幽灵那一格已还给池子）",
              all(s["zh"].alphaValue() == 1.0 for s in tv._slots))
    finally:
        o.close()


def t_s1_merge_not_following() -> None:
    print("\n--- S1 手动回看: 视线不动（视口按行高补偿、不做位移动画）---")
    try:
        o, now = _overlay()
    except Exception as e:                                          # noqa: BLE001
        check("Overlay 可构造（AppKit）", False, f"{type(e).__name__}: {e}")
        return
    try:
        for i in range(16):
            o.finalize(f"line number {i} about economics", f"第{i}句讲的是经济学。")
        now[0] += motion.MOVE_S + 0.05
        o._tv.tick()
        tv = o._tv
        tv.set_frame(0.0, 3.0 * tv._row_h)          # 视口只放得下三行 -> 文档高得多
        tv.mark_dirty(urgent=True)
        tv._follow = False                          # 用户滚回去看历史了
        clip = tv._scroll.contentView()
        clip.scrollToPoint_((0.0, 6.0 * tv._row_h))
        tv._expected_origin = clip.bounds().origin.y
        oy0 = clip.bounds().origin.y
        check("（前提）确实滚上去了、不在跟随",
              oy0 > tv._row_h and not tv.following, f"oy={oy0:.1f}")

        ids = list(o._log.ids)
        self_ok = o.replace_merge(ids[-2], ids[-1], "merged here.", "合并到这里。")
        oy1 = clip.bounds().origin.y
        check("合并落地", self_ok)
        check("⭐ 视口同步下移一个行高（视线不动）",
              abs((oy1 - oy0) + tv._row_h) < 0.6, f"{oy0:.1f} -> {oy1:.1f} row_h={tv._row_h}")
        check("⭐ 未跟随时**不位移**（`_roll_from == 0`，只淡换）", tv._roll_from == 0.0)
        check("未跟随时也不许把读者拽回底部", not tv.following)
        check("幽灵仍在（淡换照做，且它不在视口里）", tv._ghost_j is not None)
        now[0] += motion.MOVE_S + motion.STATE_S
        tv.tick()
        check("落位后视口不再动",
              abs(clip.bounds().origin.y - oy1) < 0.6,
              f"{oy1:.1f} -> {clip.bounds().origin.y:.1f}")
    finally:
        o.close()


def t_s1_merge_with_live_row() -> None:
    print("\n--- S1 实时行占着最底那一格时合并（生产里最常见的一种）---")
    try:
        o, now = _overlay()
    except Exception as e:                                          # noqa: BLE001
        check("Overlay 可构造（AppKit）", False, f"{type(e).__name__}: {e}")
        return
    try:
        _small(o)
        for i in range(3):
            o.finalize(f"sentence number {i}", f"第{i}句。")
        now[0] += motion.MOVE_S + 0.05
        tv = o._tv
        tv.tick()
        o.stream_zh("这一句还在说")                  # 实时行（走 overlay 自己的状态）
        o._mark_dirty(urgent=True)
        now[0] += motion.MOVE_S + 0.05
        tv.tick()
        rh = tv._row_h
        check("（前提）层 0 = 实时行", _levels(tv).get(0) == "这一句还在说", str(_levels(tv)))

        ids = list(o._log.ids)
        deled = _levels(tv).get(1)
        self_ok = o.replace_merge(ids[-2], ids[-1], "merged.", "合并。")
        check("合并落地", self_ok)
        check("⭐ 位移**从实时行那一层之上**开始（实时行不许被推）",
              tv._roll_off_lo == 1 and tv._roll_from == -rh, f"lo={tv._roll_off_lo}")
        check("淡入名单是**已合并的尾句那一行**（层 1）",
              tv._roll_fade_lo == 1 and tv._roll_fade_n == 1,
              f"lo={tv._roll_fade_lo} n={tv._roll_fade_n}")
        check("⭐ 实时行那一格原地不动、α 仍是 1",
              _y_of(tv, "这一句还在说") == 0.0 and _alpha_at(tv, 0) == 1.0,
              f"y={_y_of(tv, '这一句还在说')} α={_alpha_at(tv, 0)}")
        gj = tv._ghost_j
        check("⭐ 幽灵 = 被吞掉那一行（层 1），文本原样、y 也在原来那一格",
              gj is not None and tv._slots[gj]["zh"].stringValue() == deled
              and abs(tv._slots[gj]["zh"].frame().origin.y - rh) < 0.6,
              f"{None if gj is None else tv._slots[gj]['zh'].stringValue()!r} y="
              f"{None if gj is None else round(tv._slots[gj]['zh'].frame().origin.y, 1)}")
        now[0] += motion.MOVE_S
        tv.tick()
        check("终态: 实时行还在最底、合并译文在它上面一格",
              _levels(tv).get(0) == "这一句还在说" and _levels(tv).get(1) == "合并。",
              str(_levels(tv)))
    finally:
        o.close()


def t_s1_merge_no_anim_when_verbatim() -> None:
    print("\n--- S1 答疑 / 纲要接管期间合并: 屏上是别的内容, 不做过渡 ---")
    try:
        o, now = _overlay()
    except Exception as e:                                          # noqa: BLE001
        check("Overlay 可构造（AppKit）", False, f"{type(e).__name__}: {e}")
        return
    try:
        _small(o)
        for i in range(3):
            o.finalize(f"sentence number {i}", f"第{i}句。")
        now[0] += motion.MOVE_S + 0.05
        tv = o._tv
        tv.tick()
        tv.set_rows_verbatim(True)                 # 答案 / 纲要接管
        ids = list(o._log.ids)
        self_ok = o.replace_merge(ids[-2], ids[-1], "merged.", "合并。")
        check("合并仍落到 `_history` 上", self_ok and o._history[-1] == ("merged.", "合并。"))
        check("⭐ 但不做过渡（换上来的行数与'合并'无关）",
              not tv.rolling and tv._ghost_j is None)
    finally:
        o.close()


# ══════════════════════════════════════════════════════════════════════
# S2 · 调整体（真机路径）
# ══════════════════════════════════════════════════════════════════════

def _draft_env():
    o, now = _overlay()
    _small(o)
    o._render_draft()
    return o, now


def _shown(o, key):
    return (o._draft_lbl if key == "draft" else o._draft_zh).stringValue()


def t_s2_append_no_anim() -> None:
    print("\n--- S2 末尾追加: 一个字节的动效都不做 ---")
    try:
        o, now = _draft_env()
    except Exception as e:                                          # noqa: BLE001
        check("Overlay 可构造（AppKit）", False, f"{type(e).__name__}: {e}")
        return
    try:
        o._draft, o._draft_zh_val = "the marginal", "边际的"
        o._render_draft()
        base = _shown(o, "draft")
        o._draft = "the marginal propensity"        # 末尾长出来
        o._render_draft()
        check("⭐ 纯追加：不进淡换名单", o._draft_rev == {}, str(o._draft_rev))
        check("文本照常换了、而且长出来了",
              _shown(o, "draft").startswith(base) and len(_shown(o, "draft")) > len(base),
              _shown(o, "draft"))
    finally:
        o.close()


def t_s2_revise_fades() -> None:
    print("\n--- S2 改了前面的词: 只那一段淡入 ---")
    try:
        o, now = _draft_env()
    except Exception as e:                                          # noqa: BLE001
        check("Overlay 可构造（AppKit）", False, f"{type(e).__name__}: {e}")
        return
    try:
        o._draft, o._draft_zh_val = "the marginal propensity to consume", "边际消费倾向"
        o._render_draft()
        o._draft = "the marginal propensity to save"
        o._render_draft()
        shown = o._draft_lbl.stringValue()
        spec = o._draft_rev.get("draft")
        check("⭐ 进了淡换名单（整串 + runs + 起跑时刻）",
              spec is not None and spec[0] == shown
              and any(ch for _, ch in spec[1]), str(None if spec is None else spec[1]))
        runs = spec[1]
        check("未变的那一段 `changed=False`、变的只有一处",
              [ch for _, ch in runs].count(True) == 1
              and "".join(t for t, _ in runs) == shown, str(runs))

        # 过渡态: 变化段的前景色 α 真的被拉低（判据必须能量到"半透明"本身）
        now[0] += motion.STATE_S / 2.0
        o._render_draft()
        att = o._draft_lbl.attributedStringValue()
        alphas = []
        att.enumerateAttribute_inRange_options_usingBlock_(
            "NSColor", (0, att.length()), 0,
            lambda v, rng, stop: alphas.append(float(v.alphaComponent())))
        check("⭐ 过渡中：变的那一段真的半透明（α 低于原色）",
              alphas and min(alphas) < max(alphas) - 0.01
              and max(alphas) == float(o._draft_lbl.textColor().alphaComponent()),
              f"{['%.2f' % a for a in alphas]}")
        check("而未变化的段**一点都没被碰**（还是原色）",
              o._draft_lbl.textColor().alphaComponent() in alphas)

        now[0] += motion.STATE_S
        o._render_draft()
        check("⭐ 走到头就换回普通串（过渡态只活 STATE_S）",
              o._draft_rev == {} and o._draft_lbl.stringValue() == shown,
              o._draft_lbl.stringValue())

        # ⚠️ 淡换途中草稿被让位(定稿上屏了) -> 属性串必须收回来:
        #    留着它, 文本没变时 `_set_draft_text` 会缓存命中直接返回,
        #    那一格就**永久**停在半透明的颜色上。
        o._draft, o._draft_zh_val = "the price is right", "价格合理"
        o._render_draft()
        now[0] += motion.STATE_S
        o._render_draft()
        o._draft = "the price is wrong"
        o._render_draft()
        check("（前提）又起了一次淡换", "draft" in o._draft_rev)
        o._draft, o._draft_zh_val = "", ""          # 让位: 草稿被清空
        o._render_draft()
        att = o._draft_lbl.attributedStringValue()
        alphas = []
        att.enumerateAttribute_inRange_options_usingBlock_(
            "NSColor", (0, att.length()), 0,
            lambda v, rng, stop: alphas.append(float(v.alphaComponent())))
        check("⭐ 让位那一帧把属性串收回成普通串（不留半透明残留）",
              o._draft_rev == {} and alphas == [float(o._draft_lbl.textColor().alphaComponent())],
              f"{alphas}")
    finally:
        o.close()


def t_s2_guards_and_counting() -> None:
    print("\n--- S2 退出条件 + testmode 计数 ---")
    try:
        import overlay as O
        o, now = _draft_env()
    except Exception as e:                                          # noqa: BLE001
        check("Overlay 可构造（AppKit）", False, f"{type(e).__name__}: {e}")
        return
    try:
        notes: list = []
        o._note = notes.append
        o._draft, o._draft_zh_val = "the price is", "价格是"
        o._render_draft()
        o._draft = "the price is high"                    # 追加
        o._render_draft()
        o._draft = "the price was high"                   # 改前面的词
        o._render_draft()
        check("⭐ 计数分开: 一次「纯追加」+ 一次「改了前面的词」",
              notes.count("draft_append") == 1 and notes.count("draft_rewrite") == 1, str(notes))
        check("计数走的是现有 block 机制（名字登记在 `testmode.note_ui`，落进 blocks.ui）",
              all(n in pathlib.Path(HERE / "testmode.py").read_text(encoding="utf-8")
                  for n in ("draft_append", "draft_rewrite"))
              and "self._note(\"draft_append\"" in pathlib.Path(
                  HERE / "overlay.py").read_text(encoding="utf-8"))

        real = O._reduce_motion
        try:
            O._reduce_motion = lambda: True               # 「减弱动态效果」开着
            o._draft = "the price was low"
            o._render_draft()
            check("Reduce Motion: 不进淡换名单、文本直接换",
                  o._draft_rev == {} and o._draft_lbl.stringValue() == "▸ the price was low",
                  o._draft_lbl.stringValue())
        finally:
            O._reduce_motion = real

        # 换行变了（1 行 -> 2 行）: 直接换、不做淡换
        o._draft, o._draft_zh_val = "the price was low", "价格很低"
        o._render_draft()
        now[0] += motion.STATE_S                       # 让上一轮的过渡收干净
        o._render_draft()
        check("（前提）现在是单行", o._draft_lbl.stringValue().count("\n") == 0)
        o._draft = ("well actually the price was quite a lot lower than that "
                    "and it keeps going for a while")
        o._render_draft()
        check("⭐ 换行一变（单行 -> 双行）就老实直接换 → 不进淡换名单",
              o._draft_rev == {} and o._draft_lbl.stringValue().count("\n") == 1,
              f"行数={o._draft_lbl.stringValue().count(chr(10)) + 1} rev={list(o._draft_rev)}")
    finally:
        o.close()


# ══════════════════════════════════════════════════════════════════════
# A5 · 「上一条新行被重新计入淡入」的修正
# ══════════════════════════════════════════════════════════════════════

def _count_of(tv, level):
    return 1 if any(s["level"] == level and not s.get("ghost") for s in tv._slots) else 0


def t_a5_fade_range() -> None:
    print("\n--- A5 一次进来 ≥2 行: 上一条已显示过的行不许被重新淡入 ---")
    try:
        o, now = _overlay()
    except Exception as e:                                          # noqa: BLE001
        check("Overlay 可构造（AppKit）", False, f"{type(e).__name__}: {e}")
        return
    try:
        _small(o)
        tv = o._tv
        o.finalize("first sentence.", "第一句。")
        now[0] += motion.MOVE_S + 0.05
        tv.tick()
        tv.set_content(list(o._history), "", "第二句正在说", True)   # 实时行出现并停住
        now[0] += motion.MOVE_S + 0.05
        tv.tick()
        check("（前提）实时行在最低那一层、已经显示出来了（不在淡入状态）",
              tv._roll_fade_n == 0 and _levels(tv).get(0) == "第二句正在说"
              and tv._painted_live == 1, str(_levels(tv)))

        # 一次进来两句定稿（≥2 行），而实时行**还在**（同一帧里文本又长了）
        tv.set_content(list(o._history) + [("second sentence.", "第二句。"),
                                           ("third sentence.", "第三句。")],
                       "", "第二句正在说 and more", True)
        check("⭐ 淡入名单**不含**最底那一层（那是上一条实时行、早就显示过了）",
              tv._roll_fade_lo == 1, f"lo={tv._roll_fade_lo} n={tv._roll_fade_n}")
        check("⭐ 名单覆盖的正是**这次新到的那两行**",
              tv._roll_fade_n == 2 and tv._roll_fade_lo + tv._roll_fade_n == 3,
              f"lo={tv._roll_fade_lo} n={tv._roll_fade_n}")
        check("⭐ 最底那一层（实时行）的 α 仍是 1（没被重新淡入）",
              _alpha_at(tv, 0) == 1.0 and _alpha_at(tv, 1) == 0.0
              and _alpha_at(tv, 2) == 0.0,
              f"α(0)={_alpha_at(tv, 0)} α(1)={_alpha_at(tv, 1)} α(2)={_alpha_at(tv, 2)}")
        check("位移量仍是**行数差**（2 格），与名单长度无关",
              abs(tv._roll_off - 2.0 * tv._row_h) < 0.01,
              f"off={tv._roll_off:.2f} row_h={tv._row_h:.2f}")
    finally:
        o.close()


def t_a5_live_appears() -> None:
    print("\n--- A5 对照组: 实时行**这次才出现**时它自己也要淡入 ---")
    try:
        o, now = _overlay()
    except Exception as e:                                          # noqa: BLE001
        check("Overlay 可构造（AppKit）", False, f"{type(e).__name__}: {e}")
        return
    try:
        _small(o)
        tv = o._tv
        o.finalize("first sentence.", "第一句。")
        now[0] += motion.MOVE_S + 0.05
        tv.tick()
        check("（前提）没有实时行", tv._painted_live == 0)
        tv.set_content(list(o._history), "", "下一句", True)         # 实时行第一次出现
        check("⭐ 名单从最底那一层起（实时行自己是最底那一行新的）",
              tv._roll_fade_lo == 0 and tv._roll_fade_n == 1
              and _alpha_at(tv, 0) == 0.0,
              f"lo={tv._roll_fade_lo} n={tv._roll_fade_n} α={_alpha_at(tv, 0)}")
    finally:
        o.close()


def main() -> int:
    print("3.9.7 文字稳定包（C3 palt / C2 悬停 / S1 合并 / S2 逐词 / A5 淡入名单）")
    for fn in (t_s2_tokens_keepform, t_s2_diff_runs, t_s2_same_breaks,
               t_c3_palt, t_c2_hover,
               t_s1_merge_roll, t_s1_merge_not_following,
               t_s1_merge_with_live_row, t_s1_merge_no_anim_when_verbatim,
               t_s2_append_no_anim, t_s2_revise_fades, t_s2_guards_and_counting,
               t_a5_fade_range, t_a5_live_appears):
        try:
            fn()
        except Exception as e:                                      # noqa: BLE001
            import traceback
            traceback.print_exc()
            check(f"{fn.__name__} 抛出异常", False, f"{type(e).__name__}: {e}")
    bad = [n for n, ok, _ in RESULTS if not ok]
    print(f"\n{'❌ ' + str(len(bad)) + ' 条不通过: ' + ', '.join(bad) if bad else '✅ 全部通过'}"
          f"（{len(RESULTS)} 条）")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
