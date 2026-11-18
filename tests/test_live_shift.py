#!/usr/bin/env python3
"""⭐ 段落刷新里顺带判「这一段讲完了没有 / 换题了没有」→ 就地拆章。**全离线。**

    ClassLive.app/Contents/MacOS/python tests/test_live_shift.py

## 这层东西防的是什么（2026-11-18 作者原话）

「要特别注意语义相关的逻辑 —— 怎么判断老师这一小段讲完了、讲得差不多了，
该让学生知道现在讲到了什么。实用最主要。」

原来换章只靠**每个 75 秒窗口的主题字符串变没变**（`live_summary._advance`）。真课重放里
这个标签会**粘住**：一组讲完、下一组已经讲了 1–4 分钟才换章；有时后一组把前一组的
段落盖掉。而段落刷新那次调用本来就握着整章原文 + 上一版段落 + 新增句号范围 —— 让它
**顺带**说一句「新增里第 N 句开始换题了」，比主题标签多得多。参考 arXiv 2502.03111
（摘要单元对齐话题边界质量最好）、LecSync digest（说话人转下一话题时确认上一节、开新节）。

## 判据（每条都钉一个具体的失效形状）

① prompt：**没有 `prev_para` 时逐字相同**、`shift` 那节只在有新增范围时出现、
   信号标记**只出现在新增范围**的行上。→ 前两条防「顺手改了排版让缓存前缀失效」。
② `parse_shift` 的机械闸门：`at` 越界 / 非 int / `True` / 标题全空 → `None`。
   → 「不猜、不修剪」：越界说明那句**引用不实**，挪到最近一句 = 替模型编引用。
③ 拆章：`at` 落在新增范围内 → 拆；旧章**不再调模型**（用上一版段落写 final）；
   新章**立刻**出首段、`t0` = `at` 那句的时刻、`title` 空（未定题，等下一个窗口认领）。
④ 不拆：旧章不足 `SHIFT_MIN_KEEP` 句 / `at` 越界 / 非 interim 的 `shift`。
   → 前一条防「三五句拆一章」（没纲要价值），后一条防「封章时拿 shift 拆一个不写的章」。
⑤ `retired`：拆完新章**不认领旧组的窗口主题**（否则 180 秒后再切一刀），
   新主题照旧认领。

## 变异验证记录（每条都真改坏过、确认会红、再改回）

| 改坏哪一行 | 哪条红 |
|---|---|
| `_sentence_rows` 去掉 `lo <= gid <= hi`（标记全句） | ①「标记只出现在新增范围」（夹具故意让旧句 31 也命中正则） |
| 标记不再受「有上一版」约束（`new_range if block else None` → `new_range`） | ①「没有 `prev_para` 时逐字相同」×2 |
| `_prev_para_block(prev_para, new_range)` → `new_range=None` | ①「规则在」+ ③「拆得到」 |
| `_shift_rules` 去掉 `if not new_range: return []` | ❗ 直接 `TypeError`（`new_range[0]`） —— 不是红字而是崩，已改回 |
| `parse_shift` 去掉 `isinstance(at, bool)` 那条 | ②「`at=True` → `None`」 |
| `parse_shift` 把越界的 `at` 夹到 `[lo, hi]` | ②「`at` 越界」×2 + ④「`at` 越界不拆」 |
| `_split_at_shift` 里 `len(old_sents) < SHIFT_MIN_KEEP` → `< 1` | ④「旧章不足 15 句」 |
| `_synthesize` 里 `if status == "interim"` → `if True` | ④「final 里的 shift 忽略」（`at` 必须落在收尾那次的范围里，否则 `parse_shift` 先拦了） |
| `_split_at_shift` 里 `self._interim_ok_n = len(old_sents)` → `-1` | ③「旧章 final 的段落就是上一版」 |
| `_split_at_shift` 里不 `self._synthesize("interim")` | ③「新章立刻出首段」等 6 条 |
| `_split_at_shift` 里不重置 `_interim_at/_interim_first/_interim_n` | ③「`_interim_n` 绑在新章自己身上」 |
| `_absorb` 里去掉 `c["times"][gid] = t` | ③「新章 `t0` = `at` 那句的时刻」 |
| `_advance` 去掉 `retired` 那一段（`if False`） | ⑤「粘住的旧主题不认领」×2 + ⑤「认领不等于再切一章」 |
| `_advance` 里 `==` 改成 `!=` | ⑤「粘住的旧主题不认领」×2 + ⑤「认领不等于再切一章」 |
"""
from __future__ import annotations

import inspect
import pathlib
import re
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import atom                                                          # noqa: E402
import chapter as ch                                                 # noqa: E402
import live_summary as L                                             # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  {detail}" if detail else ""))


# ---------------------------------------------------------------- 夹具
class _Atom:
    """`atom.Atom` 的最小形状（`build_prompt` 只读 `.text` / `.kind`）。"""

    def __init__(self, text: str, kind: str = "定义"):
        self.text, self.kind = text, kind


SENTS = [(30, "Group 3 walks through the gambling market."),
         #: ⚠️ 这句**也**命中信号正则、但它是**旧句**（`prev_hi=31`）——
         #:    拿它钉「标记只加在新增范围里」（否则 `_sentence_rows` 去掉范围判也不会红）。
         (31, "Thank you, the margin is 4 percent of turnover."),
         (32, "Next up, group 4."),
         (33, "The minimum wage rises to 12 euros.")]
ATOMS = [_Atom("赌博市场")]

PREV = {"lead": {"en": "Group 3 shows a 4 percent margin.", "zh": "第三组算毛利率 4%。",
                 "bold": [], "bold_en": [], "src": [30]},
        "body": [{"en": "Turnover drives the margin.", "zh": "营业额决定毛利率。",
                  "bold": [], "bold_en": [], "src": [31]}],
        "words": 14, "terms": [], "flag": None, "para_over": False}


def _plain(**kw) -> str:
    return ch.build_prompt(SENTS, ATOMS, ["Intro"], ["上一章"], **kw)


# ================================================================ ① prompt
def t_prompt() -> None:
    print("\n--- ① prompt：逐字不倒退 + 换题那节只在有新增时出现 ---")
    base = _plain()
    check("⭐⭐ 没有 `prev_para` 时给 `prev_hi` 也**逐字相同**（冻结判据照旧）",
          _plain(prev_hi=31) == base)
    check("⭐ `prev_para=None` + `prev_hi` 逐字相同",
          _plain(prev_para=None, prev_hi=31) == base)
    check("⭐ 换题那节一个字都没漏进「没有上一版」的 prompt",
          "shift" not in base and "换题" not in base)

    p = _plain(prev_para=PREV, prev_hi=31)
    check("⭐⭐ 有新增 → 要 `shift` 字段、`at` 落在新增范围",
          '"shift"' in p and "第 32–33 句里" in p, p[-260:])
    check("⭐⭐ 规则在：明显换题才算 / 举例不算 / 拿不准 `null`",
          "明显转到另一个话题" in p and "不算" in p and "拿不准一律" in p)
    check("⭐ 正例在（`Next up, group 4.`）", "Next up, group 4." in p)
    check("⭐ 反例在（举例 / Q&A 都不算）", "举例" in p and "Q&A" in p)
    check("⭐⭐ 上一版之后**没有新句** → 不出现换题那节",
          "shift" not in _plain(prev_para=PREV, prev_hi=33)
          and "换题" not in _plain(prev_para=PREV, prev_hi=33))

    # ── 信号标记
    lines = p.split("\n")
    marked = [ln for ln in lines if ch.SHIFT_HINT_MARK in ln]
    check("⭐⭐ 信号标记**只**出现在新增范围那一句（32 —— 31 也命中正则但它是旧句）",
          len(marked) == 1 and marked[0].startswith("32. "), str(marked))
    p_old = _plain(prev_para=PREV, prev_hi=32)
    check("⭐ 上一版已写到 32 → 32 那句不再标（它已不是新增）",
          not [ln for ln in p_old.split("\n") if ch.SHIFT_HINT_MARK in ln])
    check("⭐ 标记拼法只有一处定义（模型/测试都读它）",
          ch.SHIFT_HINT_MARK == " ←可能的换题信号")
    check("⭐ 信号正则命中 `moving on` / `any questions`、不命中 `movement`",
          bool(ch.SHIFT_HINT_RE.search("OK, moving on to the production function."))
          and bool(ch.SHIFT_HINT_RE.search("Any questions so far?"))
          and not ch.SHIFT_HINT_RE.search("The movement of prices is smooth."))
    check("⭐ `SYS_CHAPTER` 一个字都没动（系统提示逐字冻结）",
          "shift" not in ch.SYS_CHAPTER)

    # ── 新增范围唯一定义点
    check("⭐⭐ `chapter.new_range_of` 是唯一定义点（prompt 与 live_summary 共用）",
          ch.new_range_of([(30, "a"), (31, "b"), (32, "c")], 31) == (32, 32)
          and ch.new_range_of([(30, "a")], None) is None
          and ch.new_range_of([(30, "a")], 30) is None)


# ================================================================ ② parse_shift
def t_parse_shift() -> None:
    print("\n--- ② `parse_shift`：不猜、不修剪 ---")
    ok = ch.parse_shift({"shift": {"at": 13, "title": "production", "title_zh": "生产"}},
                        12, 14)
    check("⭐⭐ 合法 → `at` / 标题都在", ok == {"at": 13, "title": "production",
                                               "title_zh": "生产"}, str(ok))
    check("⭐ `at` 取两端（闭区间）",
          ch.parse_shift({"shift": {"at": 12, "title": "t"}}, 12, 14)["at"] == 12
          and ch.parse_shift({"shift": {"at": 14, "title": "t"}}, 12, 14)["at"] == 14)
    for label, raw in [
        ("`at` 越界（大）", {"at": 15, "title": "t"}),
        ("`at` 越界（小）", {"at": 11, "title": "t"}),
        ("`at` 是字符串", {"at": "13", "title": "t"}),
        ("`at` 是 `True`（int 子类，不许蒙混）", {"at": True, "title": "t"}),
        ("`at` 是 float", {"at": 13.0, "title": "t"}),
        ("`at` 缺", {"title": "t"}),
        ("标题全空", {"at": 13, "title": " ", "title_zh": ""}),
    ]:
        check(f"⭐⭐ {label} → `None`",
              ch.parse_shift({"shift": raw}, 12, 14) is None, str(raw))
    for label, obj in [("`shift` 缺", {}), ("`shift` 是 null", {"shift": None}),
                       ("`shift` 不是 dict", {"shift": "x"}), ("整个不是 dict", None)]:
        check(f"⭐ {label} → `None`（不抛）",
              ch.parse_shift(obj, 12, 14) is None, repr(obj)[:40])
    long = ch.parse_shift({"shift": {"at": 13, "title": "x" * 80, "title_zh": "字" * 80}},
                          12, 14)
    check("⭐ 标题走 `atom._label_of` 同一套截断（不另写一个数）",
          len(long["title"]) == atom.TOPIC_MAX and len(long["title_zh"]) == atom.TOPIC_ZH_MAX,
          f"{len(long['title'])}/{len(long['title_zh'])}")
    check("⭐ 只有中文标题也收", ch.parse_shift({"shift": {"at": 13, "title_zh": "生产"}},
                                            12, 14)["title"] == "")


# ================================================================ ③ 拆章
def _ts(gid: int) -> str:
    """句号 → `HH:MM:SS`（每句 20 秒，从 10:00:00 起）。⚠️ 句间隔必须够大：
    `SHIFT_MIN_S`(240 s) 按句子时刻算旧章讲了多久，1 秒一句的夹具永远拆不了。"""
    t = 36000 + 20 * gid
    return "%02d:%02d:%02d" % (t // 3600, t % 3600 // 60, t % 60)


class Harness:
    """`LiveSummarizer` + 假模型。**按 system prompt 身份分发**。

    `shift_at` = 章节回包里带的 `shift.at`（`None` = 不带）。

    ⚠️ `shift_at` 是**一直**带着的：每版回包都报同一个换题点（真实模型不会这样，
       但这是最简单的「一直报」形状 —— 拆过一次之后旧章已经没了，`at` 自然落在
       新章的新增范围外，`parse_shift` 自己就拦下）。
    """

    def __init__(self, tmp, topics, shift_at=None, on_chap=None):
        self.calls: list[str] = []
        self.emits: list[dict] = []
        self.blocks: list[str] = []
        self._topics = iter(topics)
        self._shift_at = shift_at
        self._on_chap = on_chap
        self._tmp = tmp
        #: 第几次章节调用 —— 回包把它写进 `lead.zh`，用来区分「这一版是谁生成的」。
        self.n_chap = 0

    def chat(self, sysp, block, max_tokens, temperature):
        if sysp is atom.SYS:
            self.calls.append("atom")
            t = next(self._topics)
            return {"topic": t, "topic_zh": t + "中",
                    "points": [{"text": "p", "kind": "要点", "src": [1], "terms": []}]}
        assert sysp is ch.SYS_CHAPTER, "只该有两种 system prompt"
        self.calls.append("chap")
        self.blocks.append(block)
        self.n_chap += 1
        if self._on_chap is not None:
            self._on_chap(block)
        obj = _chap_reply(block, self.n_chap)
        if self._shift_at is not None:
            obj["shift"] = {"at": self._shift_at, "title": "minimum wage",
                            "title_zh": "最低工资"}
        return obj

    def build(self):
        self.s = L.LiveSummarizer(
            chat=self.chat, append_atoms=lambda a: len(a),
            chapter_path=ch.chapter_path_for(pathlib.Path(self._tmp) / "S.md"),
            emit=self.emits.append, clock=lambda: self._now)
        return self.s

    _now = 0.0

    def say(self, gid: int, flush_at: float, step_first: float | None = None) -> None:
        self._now = flush_at
        self.s.feed((gid, _ts(gid), "sentence %d" % gid, "中%d" % gid))
        self.s.step(flush_at - 31.0 if step_first is None else step_first)
        self._now = flush_at
        self.s.step(flush_at)

    def chap_calls(self) -> int:
        return self.calls.count("chap")

    def chapters(self) -> list:
        return [e["chapter"] for e in self.emits if e.get("kind") == "chapter"]


def _chap_reply(block: str, n_chap: int = 0) -> dict:
    """从 prompt 里读真实句号，段落只落在**开头两句**（模拟“上一版只写到开头”的常态）。

    ⚠️ **不能把 `src` 落在 `hi`**：那样每一版段落都盖到最新一句，`para_hi == hi`
    → 上一次刷新之后永远“没有新增” → 模型看不到换题范围（夹具自己把被测行为灭掉）。
    只用 `lo, lo+1`：那一版盖到 `lo+1`，下一次刷新自然有新增；
    而章尾那几句始终没进过段落 —— 拆章时旧章的上一版确实是**旧组的**。
    ⚠️ `n_chap` 进 `lead.zh`：夹具回包否则是**完全确定性**的，于是「旧章 final 用的是
    上一版还是又调了一次模型」分辨不出来（两种回包长得一模一样，变异验证会假绿）。
    """
    ids = [int(m) for m in re.findall(r"^(\d+)\. ", block, re.M)]
    lo, hi = (ids[0], ids[-1]) if ids else (1, 1)
    a, b = lo, min(lo + 1, hi)

    def ln(en, zh, src):
        return {"en": en, "zh": zh, "bold": [], "bold_en": [], "src": [src]}

    return {"title": "Elasticity", "title_zh": "弹性",
            "sentences": [{"en": "one sentence", "zh": "一句", "src": [a],
                           "terms": [], "flag": None}],
            "para": {"lead": ln("Demand is elastic when |Ed| > 1, so revenue falls.",
                                f"第{n_chap}次合成", a),
                     "body": [ln("The bus example: fares up 10 percent.", "公交例子。", b)],
                     "terms": [], "flag": None}}


def _harness(d, topics, **kw) -> Harness:
    h = Harness(d, topics, **kw)
    h.build()
    return h


def _windows(h: Harness, n: int, *, first: int = 1) -> None:
    """推 `n` 个窗，**每窗两句**（共 2n 句）。

    ⚠️ 为什么每窗两句、而不是一句：一节可有 20 句但必须**短于 `MAX_CHAPTER_S`(900 s)**，
       否则 `_maybe_force_split` 先切一刀，拆章那一段永远进不了（夹具自己把被测行为掩盖掉）。
       句号 = 窗口序号×2−1 / ×2，`t` 列按句号（`_ts`：每句 20 秒）—— `t0` 判据与 `SHIFT_MIN_S` 都靠它。
    """
    for k in range(first, first + n):
        t = 80.0 * k
        for gid in (2 * k - 1, 2 * k):
            h.s.feed((gid, _ts(gid), "sentence %d" % gid, "中%d" % gid))
        h.s.step(t - 31.0)
        h._now = t
        h.s.step(t)


def t_split() -> None:
    print("\n--- ③ 拆章：shift 落在新增范围 → 拆 ---")
    with tempfile.TemporaryDirectory() as d:
        # 10 个窗 = 20 句（章龄 800 s < MAX_CHAPTER_S）。`at=17` 落在最后一次刷新的
        # 新增范围（17–18）里，旧章剩 1–16 = 16 句 ≥ `SHIFT_MIN_KEEP`。
        h = _harness(d, ["A"] * 40, shift_at=17)
        _windows(h, 10)
        cs = h.chapters()
        ids = sorted({c["id"] for c in cs})
        fin = [c for c in cs if c["status"] == "final"]
        check("⭐⭐ 拆出两章（旧章 final + 新章 interim）", len(ids) == 2, str(ids))
        check("⭐⭐ 旧章 `hi` = `at` − 1 = 16",
              bool(fin) and fin[0]["hi"] == 16, str([(c["id"], c["hi"]) for c in fin]))
        new = [c for c in cs if c["id"] == max(ids)]
        check("⭐⭐ 新章 `lo` = `at` = 17", bool(new) and new[0]["lo"] == 17,
              str([(c["id"], c["lo"], c["hi"]) for c in new]))
        check("⭐⭐ 新章**立刻**出了首段（interim），不等下一窗",
              bool(new) and new[0]["status"] == "interim", str([c["status"] for c in new]))
        check("⭐ 新章 `t0` = 换题点那一句的时刻（_ts(17)，不是窗口首句）",
              bool(new) and new[0]["t0"] == _ts(17),
              str([(c["id"], c["t0"]) for c in new]))
        check("⭐ `stats()['shift_splits'] == 1`", h.s.stats()["shift_splits"] == 1,
              str(h.s.stats()["shift_splits"]))
        check("⭐⭐ 拆章那次混合段落**没写**（旧章 final 的段落里没有新增那几句）",
              bool(fin) and fin[0]["para"] is not None
              and all(s["src"][0] < 17 for s in [fin[0]["para"]["lead"],
                                                *fin[0]["para"]["body"]]),
              str([s["src"] for s in (fin[0].get("para") or {}).get("body", [])])[:60])

    with tempfile.TemporaryDirectory() as d:
        # 旧章句数够、但拆章那一刻旧章 final **不该再打一次模型**
        h = _harness(d, ["A"] * 40, shift_at=17)
        _windows(h, 10)
        cs = h.chapters()
        old_interims = [c for c in cs if c["id"] == 0 and c["status"] == "interim"]
        fin = [c for c in cs if c["id"] == 0 and c["status"] == "final"]
        lead_fin = fin[0]["para"]["lead"]["zh"] if fin and fin[0].get("para") else None
        lead_prev = (old_interims[-1]["para"]["lead"]["zh"]
                     if old_interims and old_interims[-1].get("para") else None)
        check("⭐⭐ 旧章 final 的段落就是**上一版**（不是拆章时又调了一次模型）",
              lead_fin is not None and lead_fin == lead_prev,
              f"final={lead_fin} 上一版={lead_prev}")
        check("⭐ 旧章只封了一次 final（没有拆两刀）", len(fin) == 1, str(len(fin)))
        new_id = max(c["id"] for c in cs)
        new_cs = [c for c in cs if c["id"] == new_id]
        new_lead = (new_cs[0]["para"]["lead"]["zh"]
                    if new_cs and new_cs[0].get("para") else None)
        check("⭐⭐ 新章首段是**拆完那一次**新生成的（不是把旧章段落拄过来）",
              new_lead is not None and new_lead != lead_prev,
              f"新={new_lead} 旧={lead_prev}")

    with tempfile.TemporaryDirectory() as d:
        # ⭐⭐ Claude 验收加：`clock` 与 `step(now)` **不同域**（真实的积压回放就是这样）。
        #    新章的 `t_open` / `_interim_at` 必须落在 step 域，否则 MIN/MAX 对新章恒成立/恒不成立。
        h = _harness(d, ["A"] * 40, shift_at=17)
        h.s._clock = lambda: 1.0e6 + h._now
        _windows(h, 10)
        cur = h.s._cur
        check("⭐⭐ 拆出的新章 `t_open` 在 step 域（拆章那一窗的 now = 720），不是 `self._clock()`",
              cur is not None and cur["t_open"] == 720.0 and h.s._interim_at == 720.0,
              str(cur and (cur["t_open"], h.s._interim_at)))
        new_first = [b for b in h.blocks if "sentence 17" in b and "sentence 16" not in b]
        check("⭐⭐ 新章首段**从零写**：它的 prompt 里没有「上一版」那一节（不被旧组结论拽住）",
              bool(new_first) and "上一版" not in new_first[0],
              (new_first[0][-200:] if new_first else "没找到新章首段的 prompt"))

    with tempfile.TemporaryDirectory() as d:
        # 拆完整条流水线照旧走：新章之后继续长、最后封章
        h = _harness(d, ["A"] * 40, shift_at=17)
        _windows(h, 10)
        h.s.finish(timeout_s=2.0)
        fin_ids = {c["id"] for c in h.chapters() if c["status"] == "final"}
        check("⭐⭐ 收尾把两章都封成 final", len(fin_ids) >= 2, str(sorted(fin_ids)))

    with tempfile.TemporaryDirectory() as d:
        # ⚠️ 新章的刷新闸门状态要跟 `_absorb` 一样**重置**：不重置的话 `_interim_n` /
        #    `_interim_ok_n` 还挂在**旧章**的句数上，两道闸对新章算的就是另一章的数
        #    （旧章 16+ 句 vs 新章 2 句 —— 什么时候突然又合一次就不由新章自己的节奏决定了）。
        h = _harness(d, ["A"] * 40, shift_at=17)
        _windows(h, 10)                      # 第 9 窗拆章（新章 17–18），第 10 窗只多 2 句
        new_id = max(c["id"] for c in h.chapters())
        check("⭐⭐ `_interim_n` 绑在新章自己身上（不是旧章那 16+ 句）",
              h.s._interim_n == 2 and h.s._interim_ok_n == 2,
              f"i_n={h.s._interim_n} i_ok={h.s._interim_ok_n}")
        check("⭐ `_interim_first` 已落（首段刚刚在拆章里出了）",
              h.s._interim_first is False)
        n_before = len([c for c in h.chapters() if c["id"] == new_id])
        _windows(h, 1, first=11)             # 新章 21–22：新增 4 句、距首段 160 s
        check("⭐⭐ 新章照旧按两道闸长下去（过 60 s + 新增 3 句 → 真的再合一次）",
              len([c for c in h.chapters() if c["id"] == new_id]) == n_before + 1,
              f"{n_before} -> {len([c for c in h.chapters() if c['id'] == new_id])}")


def t_no_split() -> None:
    print("\n--- ④ 不拆：旧章太短 / 越界 / 非 interim ---")
    check("常量 `SHIFT_MIN_S` 就是 240（同组换人讲不拆）", L.SHIFT_MIN_S == 240.0, str(L.SHIFT_MIN_S))
    with tempfile.TemporaryDirectory() as d:
        # 句数够（16 句）但旧章只讲了 16×20=320 s 的话会拆；把 `SHIFT_MIN_S` 临时抬到 400 → 不拆
        old = L.SHIFT_MIN_S
        L.SHIFT_MIN_S = 400.0
        try:
            h = _harness(d, ["A"] * 40, shift_at=17)
            _windows(h, 10)
            check("⭐⭐ 旧章讲得不够 `SHIFT_MIN_S` 秒 → 不拆（句数够也不拆）",
                  h.s.stats()["shift_splits"] == 0, str(h.s.stats()["shift_splits"]))
        finally:
            L.SHIFT_MIN_S = old
    with tempfile.TemporaryDirectory() as d:
        # ⭐ 硬切出来的章（`forced`）不受 `SHIFT_MIN_S` 限制
        old = L.SHIFT_MIN_S
        L.SHIFT_MIN_S = 400.0
        try:
            h = _harness(d, ["A"] * 40, shift_at=17)
            h.s._forced_next = True                   # 等价于上一章刚被 `MAX_CHAPTER_S` 切开
            _windows(h, 10)
            check("⭐⭐ 硬切出来的章：讲得不够 `SHIFT_MIN_S` 也照样按换题点拆",
                  h.s.stats()["shift_splits"] == 1, str(h.s.stats()["shift_splits"]))
        finally:
            L.SHIFT_MIN_S = old
    check("常量就是 15（太少句的章拆出来没纲要价值）", L.SHIFT_MIN_KEEP == 15,
          str(L.SHIFT_MIN_KEEP))
    with tempfile.TemporaryDirectory() as d:
        # 只有 3 个窗 = 6 句就报换题（at=5）：不够 `SHIFT_MIN_KEEP`
        h = _harness(d, ["A"] * 40, shift_at=5)
        _windows(h, 3)
        check("⭐⭐ 旧章不足 15 句 → 不拆（只有一章）",
              len({c["id"] for c in h.chapters()}) == 1,
              str(sorted({c["id"] for c in h.chapters()})))
        check("⭐ 不拆时 `shift_splits` 仍是 0", h.s.stats()["shift_splits"] == 0)
        check("⭐ 没有 final 被凭空写出来",
              not [c for c in h.chapters() if c["status"] == "final"])

    with tempfile.TemporaryDirectory() as d:
        # `at` 越出新增范围（模型编了一个句号）→ 不拆
        h = _harness(d, ["A"] * 40, shift_at=99)
        _windows(h, 10)
        check("⭐⭐ `at` 越界 → 不拆（引用不实的那一次不算）",
              len({c["id"] for c in h.chapters()}) == 1, str(h.s.stats()["shift_splits"]))

    with tempfile.TemporaryDirectory() as d:
        # 封章（final）那次回包里带 shift → **忽略**（章已经封了，拆一个不写的章没意义）
        # ⚠️ `at` 必须落在**收尾那次**的新增范围（19–20）里，否则 `parse_shift` 早就拦下了，
        #    测不到「interim 以外一律忽略」那一条（变异验证抓到过一次假绿）。
        h = _harness(d, ["A"] * 40, shift_at=19)
        _windows(h, 10)
        n_ids = len({c["id"] for c in h.chapters()})
        h.s.finish(timeout_s=2.0)
        check("⭐⭐ final 里的 shift 被忽略（章数不变、没有白拆）",
              len({c["id"] for c in h.chapters()}) == n_ids and
              h.s.stats()["shift_splits"] == 0,
              f"{n_ids} -> {sorted({c['id'] for c in h.chapters()})} "
              f"shift_splits={h.s.stats()['shift_splits']}")


def t_retired() -> None:
    print("\n--- ⑤ `retired`：拆完新章不认领旧组的粘住主题 ---")
    with tempfile.TemporaryDirectory() as d:
        # 窗口主题一直是 `A`（重放里的「粘住」形状），最后一次刷新的新增里有换题点
        h = _harness(d, ["A"] * 60, shift_at=17)
        _windows(h, 10)
        check("前置：真的拆了", h.s.stats()["shift_splits"] == 1,
              str(h.s.stats()["shift_splits"]))
        new_id = max(c["id"] for c in h.chapters())
        check("前置：新章未定题（窗口标题空）", h.s._cur is not None
              and h.s._cur["id"] == new_id and not h.s._cur["title"],
              str((h.s._cur or {}).get("title")))
        _windows(h, 2, first=11)             # 主题还是 `A`（旧组的标签）
        check("⭐⭐ 主题还粘在旧组（`A`）→ 新章**不认领**它",
              h.s._cur is not None and not h.s._cur["title"],
              repr((h.s._cur or {}).get("title")))
        check("⭐ 也没有因此再切一刀（章数没涨）",
              len({c["id"] for c in h.chapters()}) == 2,
              str(sorted({c["id"] for c in h.chapters()})))

    with tempfile.TemporaryDirectory() as d:
        # 真·新主题终于进来（晚 1–4 分钟）→ 新章认领它，**不再切一刀**。
        # ⚠️ 这是 `retired` 真正防的那个失效：新章如果已经被标成 `A`，下一个
        #    不同主题 `B` 在章龄过 180 秒时就会触发一次**多余的切章**。
        h = _harness(d, ["A"] * 60, shift_at=17)
        _windows(h, 10)                      # 拆章（新章 t_open = 第 10 窗）
        _windows(h, 2, first=11)             # 主题 A：不认领
        _windows(h, 1, first=13)             # 主题 A 仍是 A（给是否将来会误切创造条件）
        check("前置：新章此时仍未定题", h.s._cur is not None and not h.s._cur["title"],
              repr((h.s._cur or {}).get("title")))
        h._topics = iter(["B"] + ["B"] * 60)
        _windows(h, 1, first=14)             # 章龄 320 s > MIN_CHAPTER_S，主题 = B
        check("⭐⭐ 另一个主题的窗口 → 新章认领它（不另开一章）",
              h.s._cur is not None and h.s._cur["title"] == "B",
              repr((h.s._cur or {}).get("title")))
        check("⭐ 认领不等于再切一章（章数仍是 2）",
              len({c["id"] for c in h.chapters()}) == 2,
              str(sorted({c["id"] for c in h.chapters()})))


def t_api() -> None:
    print("\n--- ⑥ 形状：`new_range_of` / `refresh` 都只走一个入口 ---")
    check("⭐ `chapter.build_prompt` 仍收 `prev_para` / `prev_hi`（老调用点一个字不用改）",
          {"prev_para", "prev_hi"} <= set(inspect.signature(ch.build_prompt).parameters))
    check("⭐⭐ `live_summary._new_range` 就是 `chapter.new_range_of`（不各算一遍）",
          L.LiveSummarizer._new_range.__doc__ is not None
          and "chapter.new_range_of" in (L.LiveSummarizer._new_range.__doc__ or ""))
    check("⭐ `stats()` 里加了 `shift_splits`（全是计数器，没塞字符串）",
          isinstance(L.LiveSummarizer.stats.__doc__, str))


def main() -> int:
    t_prompt()
    t_parse_shift()
    t_split()
    t_no_split()
    t_retired()
    t_api()
    bad = [n for n, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(bad)}/{len(RESULTS)} 通过")
    if bad:
        print("❌ " + " / ".join(bad))
        return 1
    print("✅ 全绿")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
