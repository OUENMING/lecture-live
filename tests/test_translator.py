#!/usr/bin/env python3
"""`select_terms` 与术语池的判据 —— **全离线**，不加载模型、不发请求。

    ClassLive.app/Contents/MacOS/python tests/test_translator.py

## 钉住什么

1. ⭐⭐ **`extra_terms` 只进「候选池」，不进「每句全量注入」那一档** ——
   这是 2026-09-29 那条改动的**核心不变量**。三档分别是：
     · `core`   常驻
     · `always` 课程术语，**每句全量注入**（`translator.course_term_list`）
     · 池子     按相似度**动态召回** `[:max_dyn]`
   把值钱的词放错档 = 每句的 prompt 成本随词数线性涨（实测 120 条时占 60%）。
2. ⭐ **相似度不到 0.6 的词不进** —— 这条是「提名额不会拉进弱相关词」的依据。
3. `max_dyn` 真的截断；`core` / `always` **无论句子**都在。

⚠️ 本文件此前**一个判据都没有**（`select_terms` / `MAX_DYNAMIC_TERMS` 全仓零测试）。
"""
from __future__ import annotations

import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import translator as T                                               # noqa: E402

CASES: list[tuple[str, object]] = []


def case(name: str):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


def _lines(out: str) -> list:
    return out.split("\n") if out else []


# ---------------------------------------------------------------- 三档语义
@case("⭐ `core` / `always` **无论句子**都在（它们是常驻档）")
def t_core_and_always_always_injected():
    got = _lines(T.select_terms("hello everyone welcome back",
                                [], core=["CC1"], always=["AA1"]))
    assert "CC1" in got and "AA1" in got, f"常驻档没注入：{got}"


@case("⭐⭐ `extra_terms` 进**池子**、不进 `always`")
def t_extra_terms_go_to_pool_not_always():
    """本次改动的**核心不变量**。

    `extra_terms`（转录里挖的词）必须走**动态召回**那一档 —— 那样成本是常数；
    一旦混进 `always`，它就变成**每句全量注入**，成本随词数线性涨。
    """
    w = T.Translator("m", None, course=None, extra_terms=["monopoly", "oligopoly"])
    assert "monopoly" in w._terms, "extra_terms 没进池子"
    assert "monopoly" not in w._course_terms, (
        "extra_terms 混进了 `_course_terms` —— 那档是**每句全量注入**的，"
        "成本会随词数线性涨（实测 120 条时占整条 prompt 60%）")


@case("⭐⭐ 无关句子里，池子里的词**不出现**（动态召回而不是常驻）")
def t_pool_words_not_injected_when_irrelevant():
    pool = ["efficiency", "equity", "monopoly", "oligopoly"]
    got = _lines(T.select_terms("hello everyone welcome back to class",
                                pool, core=[], always=["efficiency", "equity"]))
    assert "efficiency" in got and "equity" in got, f"常驻档丢了：{got}"
    assert "monopoly" not in got and "oligopoly" not in got, (
        f"无关句子里池子词也被注入了 —— 那它就不是动态召回：{got}")


# ---------------------------------------------------------------- 阈值与截断
@case("⭐ 相似度 < 0.6 的词**不进**（陌生长词不该被召回）")
def t_below_threshold_not_recalled():
    # ⚠️ 无匹配时返回的是标记串 `(无特定术语)`，**不是空串** ——
    #    第一版断言 `== []`，判据自己红的（实现没错）。
    got = T.select_terms("the quick brown fox jumps", ["zzzzqqqqwwww"],
                         core=[], always=[])
    assert got == "(无特定术语)", f"完全不像的词被召回了：{got!r}"


@case("⭐ `max_dyn` 真的截断（喂一堆高相似词，只出 max_dyn 个）")
def t_max_dyn_truncates():
    words = ["marginal", "marginalx", "marginaly", "marginalz", "marginalw"]
    for k in (1, 2, 3):
        got = [t for t in _lines(T.select_terms("the marginal utility",
                                                words, core=[], always=[],
                                                max_dyn=k)) if t != "(无特定术语)"]
        assert len(got) <= k, f"max_dyn={k} 却注入了 {len(got)} 个：{got}"


@case("⚠️ 池子空 / 无匹配 -> 给 `(无特定术语)`，不返回空串")
def t_empty_marker():
    assert T.select_terms("hello", [], core=[], always=[]) == "(无特定术语)"
    assert T.select_terms("hello", [], core=["CC1"], always=[]) == "CC1"


@case("⭐ 音近**能**召回一部分（但**大部分接不住** —— 这条是限制，不是保证）")
def t_fuzzy_recall_is_partial():
    """⚠️⚠️ **实测边界**（2026-09-29 量的，逐词 `difflib`）：docstring 自己举的
    4 个例子里**只有 2 个**过得了 0.6：

        lattice/lacker 0.62 ✅   nation/relation 0.71 ✅
        macroscopic/max chocolate **0.40 ❌**   statistics/stalcy **0.50 ❌**

    ⚠️ **这正解释了为什么课程术语是「全量注入」**（`select_terms` 的 docstring：
       纯按匹配筛选会**死循环**）—— 听错的词**根本匹配不上表**。
    → 所以**池子接不住大部分听错**；这条判据钉的就是"能接住一部分"，
      别读成"池子能兜住听错"。
    """
    got = _lines(T.select_terms("average on the lacker level",
                                ["lattice"], core=[], always=[]))
    assert any("lattice" in g for g in got), f"0.62 该过 0.6，却没召回：{got}"
    # 反面：**接不住的**要如实接不住（不许为了"好看"去调低阈值）
    miss = T.select_terms("we're max chocolate properties",
                          ["macroscopic"], core=[], always=[])
    assert miss == "(无特定术语)", (
        f"macroscopic/max chocolate 实测只有 0.40，不该被召回：{miss!r}\n"
        f"  ⚠️ 它靠的是**全量注入**那一档（课程术语），不是匹配。")


@case("⭐⭐ 池子里有重复会**白占一个动态名额** —— `merge_terms` 就是防这个")
def t_duplicate_costs_a_slot():
    """⭐ 2026-09-29 OCR 抓到的：`load_terms(...) + list(extra_terms)` 这种
    **外面拼**的写法绕过了 `load_terms` 内部的去重。转录词与术语表撞词很常见，
    于是池子里有两条同词 —— 而 `select_terms` 是 `scored[:max_dyn]`
    **先截断、再去重** → **重复条目白占一个名额**。

    ⚠️ 这条先**证明缺陷真实存在**（拿未去重的列表跑），再**证明修法有效**
       （`merge_terms` 之后不多占）—— 两半都要，否则只是"看起来修了"。
    """
    dup = ["marginal", "marginal", "marginal utility", "marginalism"]
    en = "the marginal utility of the last unit"
    raw = [t for t in _lines(T.select_terms(en, dup, core=[], always=[],
                                            max_dyn=3)) if t != "(无特定术语)"]
    assert len(raw) == 2, (
        f"这条判据的前提变了 —— 未去重时本该只注入 2 个（被重复占掉一个名额），"
        f"实测 {len(raw)}：{raw}")

    clean = T.merge_terms(dup)                 # 去重
    got = [t for t in _lines(T.select_terms(en, clean, core=[], always=[],
                                            max_dyn=3)) if t != "(无特定术语)"]
    assert len(got) == 3, f"去重后该拿回第 3 个名额，实测 {len(got)}：{got}"


@case("⭐ `merge_terms` 跨组去重、保序、大小写不敏感")
def t_merge_terms():
    got = T.merge_terms(["Alpha", "beta"], ["alpha", "Gamma"], None, [])
    assert got == ["Alpha", "beta", "Gamma"], f"得到 {got}"
    assert T.merge_terms() == [] and T.merge_terms(None) == []


@case("⭐⭐ `load_terms` 走的就是 `merge_terms`（唯一定义点，没有第二份去重）")
def t_load_terms_uses_merge():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        g = pathlib.Path(d) / "g.txt"
        g.write_text("Alpha\nalpha\nBeta\n", encoding="utf-8")
        got = T.load_terms(str(g))
        assert got == ["Alpha", "Beta"], f"得到 {got}"
        # 与 merge_terms 逐字一致（同一份实现，不是复制来的）
        assert got == T.merge_terms(T._load_terms(str(g))), "两份去重逻辑分叉了"


@case("⚠️ `_load_terms` 跳注释/跳空行；**去重在 `load_terms` 那一层**")
def t_load_terms_dedup():
    """⚠️ 第一版把这条写在 `_load_terms` 上 —— 而**去重不在那儿**（实测
    `_load_terms` 原样返回 `['Alpha','alpha','Beta']`）。去重是 `load_terms`
    做的（大小写不敏感、保序）。判据要指到**那个函数**上。
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "g.txt"
        p.write_text("# 注释\nAlpha\nalpha\nBeta\n\n", encoding="utf-8")
        raw = T._load_terms(str(p))
        assert raw == ["Alpha", "alpha", "Beta"], f"它只该跳注释与空行：{raw}"
        dedup = T.load_terms(str(p))          # 公共表那条路走 load_terms
        assert dedup == ["Alpha", "Beta"], f"去重保序有问题：{dedup}"


# ---------------------------------------------------------------- F2：本地闩锁
@case("⭐⭐ 本地模型没装 -> `_ensure` 闩住、**立刻**抛（不许每句重试 load）（F2）")
def t_local_missing_latches():
    """动机（2026-10-01 审查 F2）：auto 模式云端失败会切本地，而本地模型是**可选**的
    —— 没装时原来**每一句**都再试一次 `mlx_lm.load()`（在线 = 隐式拉 938 MB
    把定稿线程卡死；离线 = 每句白等一次）。现在：先问在不在 → 闩住 → 立刻抛。

    改坏哪行会红：删掉 `_ensure` 里 `if not local_model_present():` 那段 →
    第一条断言红（会去真 `load()`，异常文本不是「没装」）。
    """
    orig = T.local_model_present
    calls = {"n": 0}

    def fake_present():
        calls["n"] += 1
        return False

    T.local_model_present = fake_present
    try:
        w = T.Translator("m", None, course=None)
        errs = []
        for _ in range(2):                     # 第二次也必须「立刻」抛、同一句
            try:
                w.fix_and_translate_stream("hello", [])
                errs.append(None)
            except RuntimeError as e:
                errs.append(str(e))
        assert errs[0] and "没装" in errs[0], f"第一句应抛「没装」：{errs[0]!r}"
        assert errs[1] == errs[0], f"闩锁后每次都应抛同一句：{errs}"
        assert calls["n"] == 1, f"闩锁之后不该再问在不在（问了 {calls['n']} 次）"
        assert w.local_ready() is False, "闩锁后 local_ready() 应为 False"
    finally:
        T.local_model_present = orig


# ---------------------------------------------------------------- select_term_parts（2026-10-04）
@case("⭐⭐ `select_term_parts`：`stable` 与句子**无关**；`dyn` 随句变；`select_terms` = 两段连起来（老行为逐字不变）")
def t_select_term_parts_split():
    terms = ["monopoly", "oligopoly", "elasticity", "T1"]
    kw = dict(core=["CORE1"], always=["T1", "T2"])
    s1, d1 = T.select_term_parts("the monopoly case", terms, **kw)
    s2, d2 = T.select_term_parts("completely unrelated words here", terms, **kw)
    assert s1 == s2 == ["CORE1", "T1", "T2"], f"稳定段不许随句变：{s1} {s2}"
    assert "monopoly" in d1 and "monopoly" not in d2, f"动态段应随句变：{d1} {d2}"
    assert not set(x.lower() for x in s1) & set(x.lower() for x in d1), "两段不许重复同一术语"
    assert T.select_terms("the monopoly case", terms, **kw) == "\n".join(s1 + d1)
    assert T.select_terms("x y z", [], core=[], always=[]) == T.NO_TERMS, "什么都没有时给占位串"
    s3, d3 = T.select_term_parts("x y z", [], core=[], always=[])
    assert s3 == [] and d3 == [], "云端布局要的是空列表，不是占位串"


# ---------------------------------------------------------------- 云端前文窗口（2026-10-04）
def _ct(chunk: int, max_ctx: int = 5):
    import cloud_translator as CT
    return CT.CloudTranslator("k", "m", glossary_terms=["T1", "T2", "monopoly", "oligopoly"],
                              max_context=max_ctx, core=["CORE1"], course_terms=["T1", "T2"],
                              domain="Econ 101", ctx_chunk=chunk)


@case("⭐ 块对齐窗口：长度恒在 [chunk, 2*chunk)（够长之后），起点只在块边界上动")
def t_chunk_window_shape():
    tr = _ct(10)
    hist = [f"s{i}" for i in range(80)]
    starts = {}
    for n in range(0, 80):
        w = tr._ctx_window(hist[:n])
        if n < 10:
            assert w == hist[:n], f"不足一块时应全给：n={n} {w}"
        else:
            assert 10 <= len(w) < 20, f"窗口长度越界：n={n} len={len(w)}"
        start = n - len(w)
        starts.setdefault(n // 10, set()).add(start)
    assert all(len(v) == 1 for v in starts.values()), f"同一块内起点必须不动：{starts}"
    assert starts[3] == {20} and starts[7] == {60}, f"起点应为「上一块的起点」：{starts}"


@case("⭐⭐ 同一块内：下一句的 user 消息以上一句的「前文段」为前缀（只往后追加）；跨块才重起（前缀可缓存）")
def t_chunk_prefix_append_only_within_block():
    """DeepSeek 前缀缓存认「从头逐字相同」。块对齐窗口的性质是：同一块内起点不动，
    只在末尾追加新句 —— 所以上一句的「到前文末尾为止」那一段，正是下一句的前缀。
    ⚠️ 不是「整个前文段逐字相同」（末尾每句都多一条），第一版测试就这么写错过。"""
    tr = _ct(10)
    hist = [f"sentence number {i}" for i in range(60)]
    cut = "Extra terms possibly relevant to this utterance:"
    pre = lambda n: tr._user_content("monopoly is bad", hist[:n], "TAIL").split(cut)[0].rstrip("\n")
    for n in (31, 32, 38):
        assert pre(n + 1).startswith(pre(n)), f"块内应只追加：n={n}"
    assert not pre(40).startswith(pre(39)), "跨块窗口起点应变（否则窗口会无限长）"
    # 对照：老的滑动窗口每句都整体右移 —— 前文段的**开头**就变了，这就是要换掉它的原因
    old = _ct(0)
    pre_old = lambda n: old._user_content("monopoly is bad", hist[:n], "TAIL").split("ASR utterance:")[0].rstrip("\n")
    assert not pre_old(32).startswith(pre_old(31)), "老布局每句前缀都变（若这条红了，说明对照物坏了）"


@case("⭐ 块对齐布局的顺序：课程 → 稳定术语 → 前文 → 本句召回术语 → 本句 → 指令")
def t_chunk_layout_order():
    tr = _ct(10)
    u = tr._user_content("the oligopoly case", [f"c{i}" for i in range(25)], "TAIL-X")
    marks = ["Course: Econ 101", "Course terms:", "Recent context (already corrected):",
             "Extra terms possibly relevant to this utterance:", "ASR utterance:\nthe oligopoly case", "TAIL-X"]
    idx = [u.index(m) for m in marks]
    assert idx == sorted(idx), f"顺序不对：{list(zip(marks, idx))}"
    stable_part = u.split("Recent context")[0]
    assert "CORE1" in stable_part and "T1" in stable_part and "T2" in stable_part, "核心/课程术语应在稳定段"
    assert "oligopoly" not in stable_part, "本句召回的术语不许进稳定段（会让前缀每句都变）"
    assert "oligopoly" in u.split("Extra terms possibly relevant to this utterance:")[1].split("ASR utterance")[0], \
        "本句召回的术语应在 Extra terms 段"


@case("⭐⭐ `ctx_chunk=0` 时两条 user 消息与老版**逐字相同**（冻结的字面量 = 用 HEAD 旧代码实跑出来的，不是再调一遍函数比）")
def t_legacy_layout_frozen():
    tr = _ct(0, max_ctx=2)
    got = {}

    def fake(messages, max_tokens):
        got["m"] = messages
        return iter(["ZH: x\nEN: y"])

    tr._stream_chat = fake
    tr.fix_and_translate_stream("the oligopoly case", ["a", "b", "c"])
    t_msg = got["m"][1]["content"]
    tr.fix_stream("the oligopoly case", ["a", "b", "c"])
    f_msg = got["m"][1]["content"]
    head = ("Course: Econ 101\nCourse terms:\nCORE1\nT1\nT2\noligopoly\nmonopoly\n\n"
            "Recent context (already corrected):\n- b\n- c\n\n"
            "ASR utterance:\nthe oligopoly case\n\n")
    assert t_msg == head + (
        "Now translate the ASR utterance above into Chinese and output exactly:\n"
        "ZH: <Chinese translation of the CORRECTED sentence>\n"
        "EN: <same utterance with ASR mishearings fixed>\n"
        "Do not repeat the course-terms list. No headings."), f"翻译消息被改了：{t_msg!r}"
    assert f_msg == head + ("Now output that same utterance with ASR mishearings fixed. "
                            "English only — no Chinese, no label."), f"矫正消息被改了：{f_msg!r}"


@case("两种布局都不丢任何一条召回术语（只是换位置）")
def t_no_term_lost():
    for chunk in (0, 10):
        tr = _ct(chunk)
        u = tr._user_content("monopoly and oligopoly", [f"c{i}" for i in range(12)], "")
        for t in ("CORE1", "T1", "T2", "monopoly", "oligopoly"):
            assert t in u, f"chunk={chunk} 丢了术语 {t}"


def _capture_draft_msgs(tr, en, context=None):
    got = {}

    def fake(messages, max_tokens):
        got["m"] = messages
        return iter(["中"])

    tr._stream_chat = fake
    tr.translate_draft(en, None, context)
    return got["m"]


@case("⭐ 草稿翻译：`CLASSLIVE_DRAFT_CONTEXT=0` 时 messages 与老版逐字相同")
def t_draft_ctx_off_identical():
    import os
    import cloud_translator as CT
    os.environ["CLASSLIVE_DRAFT_CONTEXT"] = "0"
    try:
        m = _capture_draft_msgs(_ct(0), "the price of", context=["prev sentence."])
        assert m == [{"role": "system", "content": CT.DRAFT_SYSTEM_CLOUD},
                     {"role": "user", "content": "the price of"}], f"关闭时消息被改了：{m!r}"
    finally:
        os.environ.pop("CLASSLIVE_DRAFT_CONTEXT", None)


@case("⭐ 草稿翻译：默认（不设环境变量）**带上下文**（2026-10-11 Owen 看过对比后定）")
def t_draft_ctx_default_on():
    import os
    os.environ.pop("CLASSLIVE_DRAFT_CONTEXT", None)
    m = _capture_draft_msgs(_ct(0), "the price of", context=["prev sentence."])
    assert "prev sentence." in m[1]["content"], f"默认没带上下文：{m!r}"


@case("⭐ 草稿翻译：开时 user 消息顺序 领域 → 术语 → 上一句 → 本句；三样皆空则原样")
def t_draft_ctx_on_order():
    import os
    import cloud_translator as CT
    os.environ["CLASSLIVE_DRAFT_CONTEXT"] = "1"
    try:
        m = _capture_draft_msgs(_ct(0), "the price of", context=["old a.", "prev sentence."])
        u = m[1]["content"]
        marks = ["Course: Econ 101", "T1", "prev sentence.", "the price of"]
        idx = [u.index(x) for x in marks]
        assert idx == sorted(idx), f"顺序不对：{list(zip(marks, idx))}"
        assert "old a." not in u, "只该带上一句"
        assert "do not translate" in u.lower() and "mid-sentence" in u.lower(), u
        bare = CT.CloudTranslator("k", "m")
        assert _capture_draft_msgs(bare, "hello")[1]["content"] == "hello"
    finally:
        os.environ.pop("CLASSLIVE_DRAFT_CONTEXT", None)


# ---------------------------------------------------------------- #4 B2：术语译名注入（2026-10-17）
@case("⭐ `select_term_zh`：命中 / 不命中 / 大小写 / 复数（同 `select_term_parts` 一把尺子）")
def t_term_zh_match():
    zh = {"elasticity": "弹性", "slope": "斜率", "policy": "政策"}
    assert T.select_term_zh("the elasticity of demand falls", zh, enabled=True) == \
        ["elasticity — 弹性"], "命中丢了"
    assert T.select_term_zh("completely unrelated words here", zh, enabled=True) == [], \
        "不命中却注入了"
    assert T.select_term_zh("ELASTICITY matters", zh, enabled=True) == ["elasticity — 弹性"], \
        "大小写不敏感（两边都 lower）"
    assert T.select_term_zh("these policies matter", zh, enabled=True) == ["policy — 政策"], \
        "复数该被同一套 difflib 召回接住"


@case("⭐ `select_term_zh`：上限截断（`max_n` 与默认 `TERM_ZH_MAX`）")
def t_term_zh_cap():
    big = {f"term{i}": f"译{i}" for i in range(12)}
    sent = " ".join(f"term{i}" for i in range(12))
    assert len(T.select_term_zh(sent, big, max_n=3, enabled=True)) == 3, "max_n 没截断"
    assert len(T.select_term_zh(sent, big, enabled=True)) == T.TERM_ZH_MAX, \
        "默认上限应是 TERM_ZH_MAX"


@case("⭐⭐ `CLASSLIVE_TERM_ZH` 开关：默认开；`0` 关；`enabled=False` 关（开关在纯函数里，调用方不可能忘）")
def t_term_zh_switch():
    import os
    zh = {"elasticity": "弹性"}
    os.environ.pop("CLASSLIVE_TERM_ZH", None)
    try:
        assert T.term_zh_enabled() is True, "默认应开"
        assert T.select_term_zh("the elasticity of demand", zh) == ["elasticity — 弹性"]
        os.environ["CLASSLIVE_TERM_ZH"] = "0"
        assert T.term_zh_enabled() is False
        assert T.select_term_zh("the elasticity of demand", zh) == [], "关掉后还在注入"
        os.environ["CLASSLIVE_TERM_ZH"] = "1"
        assert T.term_zh_enabled() is True
    finally:
        os.environ.pop("CLASSLIVE_TERM_ZH", None)
    assert T.select_term_zh("the elasticity of demand", zh, enabled=False) == []


@case("⭐ `CLASSLIVE_TERM_ZH_MAX` 可配；不合法值退回默认（不抛）")
def t_term_zh_max_env():
    import os
    big = {f"term{i}": f"译{i}" for i in range(12)}
    sent = " ".join(f"term{i}" for i in range(12))
    try:
        os.environ["CLASSLIVE_TERM_ZH_MAX"] = "2"
        assert len(T.select_term_zh(sent, big)) == 2
        os.environ["CLASSLIVE_TERM_ZH_MAX"] = "abc"
        assert len(T.select_term_zh(sent, big)) == T.TERM_ZH_MAX, "非法值应退回默认"
    finally:
        os.environ.pop("CLASSLIVE_TERM_ZH_MAX", None)


class _FakeTermNotes:
    """`build_notes.TermNotes` 的最小替身：`entry_of(term) -> (来源, 条目)`。"""

    def __init__(self, table):
        self._t = table

    def entry_of(self, term):
        return self._t.get((term or "").lower(), ("", {}))


@case("⭐⭐ 译名数据源闸（#4 B2）：basic 档才进；无中文名/空解析/人名不进；**A/B 五条错语境拒**")
def t_term_zh_map_gates():
    """数据源 = `term_notes.json` 的 basic 档 ∩ 本课术语表（`term_zh_map` 的 docstring）。
    五条错语境（A/B 复核）今天全是 gloss 档 —— 这里**故意给 basic 档**，
    钉的是第二道闸（`_ZH_BLOCKED`）也真挡得住。"""
    notes = _FakeTermNotes({
        "alpha": ("curated", {"level": "basic", "detail": "甲的速查"}),
        "beta": ("curated", {"level": "gloss", "detail": "乙的完整解析"}),
        "gamma": ("auto", {"level": "gloss", "detail": "丙（auto 一律 gloss）"}),
        "delta": ("curated", {"level": "basic", "detail": " "}),
        "zeta": ("curated", {"level": "basic", "detail": "泽", "type": "person"}),
        "fiona macmanus": ("curated", {"level": "basic", "detail": "公民社会研究者"}),
        "cso": ("curated", {"level": "basic", "detail": "公民社会组织"}),
        "moderator": ("curated", {"level": "basic", "detail": "调节变量"}),
        "panel": ("curated", {"level": "basic", "detail": "面板数据"}),
        "epoch": ("curated", {"level": "basic", "detail": "地质纪元"}),
        "notlisted": ("curated", {"level": "basic", "detail": "不在术语表里"}),
    })
    terms = ["alpha", "beta", "gamma", "delta", "zeta", "fiona macmanus", "cso",
             "moderator", "panel", "epoch", "omega"]
    m = T.term_zh_map(None, None, terms=terms, notes=notes)
    assert m == {"alpha": "甲的速查"}, f"闸漏了：{m}"
    assert T.term_zh_map(None, None) == {}, "没有课/没有表 → 空表（不注入）"


@case("⭐ 云端块对齐布局：译名段在「Extra terms」之后、「ASR utterance」之前；不命中**一个字符不加**")
def t_cloud_term_zh_chunked():
    import cloud_translator as CT
    tr = CT.CloudTranslator("k", "m", glossary_terms=["oligopoly"], course_terms=["oligopoly"],
                            domain="Econ", ctx_chunk=10, term_zh={"oligopoly": "寡头垄断"})
    u = tr._user_content("the oligopoly case", ["c0"], "TAIL")
    a, b, c = (u.index("Extra terms possibly relevant to this utterance:"),
               u.index("oligopoly — 寡头垄断"), u.index("ASR utterance:"))
    assert a < b < c, f"译名段位置不对（该在变化段里）：{u!r}"
    u2 = tr._user_content("the price of coffee", ["c0"], "TAIL")
    assert "Term translations" not in u2, f"不命中却加了段：{u2!r}"


@case("⭐⭐ 老布局（ctx_chunk=0）：无表时逐字不变（既有冻结判据钉着）；有命中只多一段")
def t_cloud_term_zh_legacy():
    import cloud_translator as CT
    base = CT.CloudTranslator("k", "m", glossary_terms=["oligopoly"], ctx_chunk=0)
    tr = CT.CloudTranslator("k", "m", glossary_terms=["oligopoly"], ctx_chunk=0,
                            term_zh={"oligopoly": "寡头垄断"})
    u0 = base._user_content("the oligopoly case", ["a", "b"], "TAIL")
    u1 = tr._user_content("the oligopoly case", ["a", "b"], "TAIL")
    zb = ("Term translations for this utterance (prefer these Chinese renderings):\n"
          "oligopoly — 寡头垄断\n\n")
    assert u1 == u0.replace("ASR utterance:", zb + "ASR utterance:", 1), \
        "老布局应只多「译名段」，其余逐字不变"
    # 只矫正英文（fix_stream）：EN-only 模式不注入译名
    got = {}
    tr._stream_chat = lambda messages, max_tokens: got.update(m=messages) or iter(["x"])
    tr.fix_stream("the oligopoly case", ["a"])
    assert "Term translations" not in got["m"][1]["content"], "fix_stream 不该注入译名"


@case("⭐ 云端草稿：译名段在上一句之后、本句之前；开关关掉后不加")
def t_cloud_term_zh_draft():
    import os
    import cloud_translator as CT
    os.environ["CLASSLIVE_DRAFT_CONTEXT"] = "1"
    os.environ.pop("CLASSLIVE_TERM_ZH", None)
    try:
        tr = CT.CloudTranslator("k", "m", course_terms=["T1"], domain="Econ 101",
                                ctx_chunk=0, term_zh={"T1": "术语一"})
        u = _capture_draft_msgs(tr, "the T1 case", context=["prev sentence."])[1]["content"]
        a, b, c, d = (u.index("Course: Econ 101"), u.index("prev sentence."),
                      u.index("T1 — 术语一"), u.index("the T1 case"))
        assert a < b < c < d, f"草稿译名段位置不对：{u!r}"
        os.environ["CLASSLIVE_TERM_ZH"] = "0"
        u_off = _capture_draft_msgs(tr, "the T1 case", context=["prev sentence."])[1]["content"]
        assert "Term translations" not in u_off, "开关关掉后草稿还在注入"
    finally:
        os.environ.pop("CLASSLIVE_DRAFT_CONTEXT", None)
        os.environ.pop("CLASSLIVE_TERM_ZH", None)


@case("⭐ 本地定稿：`_terms_context` 有命中才加译名段；`zh=False`（EN-only）不加")
def t_local_term_zh():
    w = T.Translator("m", None, course=None, term_zh={"policy": "政策"})
    ctx = w._terms_context("the policy changed", [])
    assert "policy — 政策" in ctx and \
        ctx.index("policy — 政策") < ctx.index("待处理这句 ASR:"), ctx
    assert "Term translations" not in w._terms_context("the policy changed", [], zh=False), \
        "EN-only 不该注入译名"
    assert "Term translations" not in w._terms_context("nothing relevant here", []), \
        "不命中不该加段"


@case("⭐ main 接线（源检查）：两个引擎同一份表；量具与注入同一把尺子；drain 转发 term_zh_n")
def t_main_wiring_term_zh():
    src = (HERE / "main.py").read_text(encoding="utf-8")
    assert src.count("term_zh=term_zh") == 2, "两个引擎工厂都要拿到译名表（降级后不丢）"
    assert "term_zh_map(args.glossary, args.course)" in src, "译名表要从本课术语建"
    assert "select_term_zh(sent, term_zh)" in src, "量具必须与注入同一把尺子"
    assert "term_zh_n=_tzn" in src, "drain 要把 term_zh_n 转发给 live.final"
    lm = (HERE / "livemetrics.py").read_text(encoding="utf-8")
    assert 'extra["term_zh_n"] = int(term_zh_n)' in lm, "量具字段要真落盘（不给就不写）"


def main_() -> int:
    print("=" * 60)
    fail: list[str] = []
    for name, fn in CASES:
        try:
            fn()
        except AssertionError as e:
            print(f"  ❌ {name}\n      {str(e)[:220]}")
            fail.append(name)
        except Exception as e:                                 # noqa: BLE001
            print(f"  ❌ {name}\n      {type(e).__name__}: {str(e)[:200]}")
            fail.append(name)
        else:
            print(f"  ✅ {name}")
    print("=" * 60)
    print(f"{len(CASES) - len(fail)}/{len(CASES)} 通过")
    for n in fail:
        print(f"  ❌ {n}")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main_())
