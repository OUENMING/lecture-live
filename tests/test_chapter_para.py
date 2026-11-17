#!/usr/bin/env python3
"""「一章一段」：`chapter.parse_para` 的机械闸门 + `Chapter.para` + 首段 180 秒。**全离线。**

    ClassLive.app/Contents/MacOS/python tests/test_chapter_para.py

## 为什么单开一个文件

`test_live_summary.py` 覆盖的是**窗口 / 章节状态机**（它自己已经 1700 行）。这一段新的东西是
**纯函数 + 一个门槛常量**，跑得比它快得多（它被 `check.py` 归进慢档），所以放在快档里，
每次改 `chapter.py` 都能立刻跑。

## 这一层的判据只有一件事：**模型回的东西一律当不可信**

每一条闸门都对应一个「模型会真的这么干」的形状（2026-11-17 调研 R2 §5.3）：
漏 `para`、`lead` 空、`src` 编一个数、`bold` 抄了自己改写的字、`words` 自报一个漂亮数字、
一段写得比 90 词长一倍。**闸门不猜、不修剪、不重试** —— 猜 = 替模型编引用。

## 变异验证记录（每条都真改坏过、确认会红、再改回）

| 改坏哪一行 | 哪条红 |
|---|---|
| `parse_para` 的 `if not isinstance(raw, dict): return None` → `raw = {}` | ①「缺 `para` → `None`」 |
| `_para_sentence` 去掉 `if not en or …` | ①「`lead.en` 空 → `None`」 |
| `if not src or any(…)` → 去掉 `any(…)` | ②「`src` 越界 → 整段丢」 |
| `parse_para` 的 `if not src_ok: return None` → `continue` | ②「`body` 有一句越界 → 整段丢」 |
| `_bold_of` 去掉 `x not in text` | ③「`bold` 逐字对不上 → 丢该项」 |
| `MAX_BOLD` 3 → 99 | ③「每句最多 3 处加粗」 |
| `words = sum(...)` → 用模型自报的 `words` | ④「`words` 由代码重算」 |
| `para_over` 恒 `False` | ④「超 90 词只标记、不截断」 |
| `FIRST_INTERIM_AFTER_S` 180 → 480 | ⑤「179 秒不出、181 秒出」 |
| `_absorb` 里去掉 `self._interim_first = True` | ⑤「新章重新享受 180 秒」 |
"""
from __future__ import annotations

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

#: 任何一句的 `src` 都必须落在这里面（夹具随便取的闭区间）。
LO, HI = 10, 20
#: 够长的章（≥ 40 句原文）—— 句数闸门 ⑦ 不介入，专测别的闸门时用它。
HI_LONG = LO + 4 * 10 - 1


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  {detail}" if detail else ""))


# ---------------------------------------------------------------- 夹具
def line(en, zh="", src=(10,), bold=(), bold_en=()):
    """`para` 里的一句话（`lead` 或 `body[i]`）。"""
    return {"en": en, "zh": zh, "bold": list(bold), "bold_en": list(bold_en),
            "src": list(src)}


LEAD = line("Demand is elastic when the price rises more than 1%.",
            "价格涨幅超过 1% 时需求富有弹性。",
            src=(10, 11), bold=["富有弹性"], bold_en=["elastic"])
BODY = [line("Revenue falls, because the quantity drop outweighs the price gain.",
             "总收入下降，因为销量跌幅超过涨价收益。", src=(12,), bold=["总收入下降"],
             bold_en=["Revenue falls"]),
        line("The bus example: fares up 10%, ridership down 18%, Ed = -1.8.",
             "公交例子：票价涨 10%，客流降 18%，Ed = -1.8。", src=(13,), bold=["-1.8"],
             bold_en=["-1.8"])]


def reply(**over) -> dict:
    """一份**合法**的旧 + 新双视图回包。`over` 覆盖顶层键（含 `para`）。"""
    obj = {"title": "Elasticity", "title_zh": "弹性",
           "sentences": [{"en": "one sentence", "zh": "一句", "src": [10],
                          "terms": [], "flag": None}],
           "para": {"lead": dict(LEAD), "body": [dict(b) for b in BODY],
                    "terms": [["price elasticity", "价格弹性"]], "flag": None}}
    obj.update(over)
    return obj


#: `Harness` 默认的章节回包：**从 prompt 里读真实的全局句号**，再把 `src` 落在里面。
#: ⚠️ 写死 `src: [10]` 的夹具会害死人 —— `parse_para` 会因为它越界而把整段丢掉，
#: 而报错信息看起来像「闸门坏了」，实际是夹具自己编了一个不存在的句号。
def default_chap(block: str) -> dict:
    ids = [int(m) for m in re.findall(r"^(\d+)\. ", block, re.M)]
    lo, hi = (ids[0], ids[-1]) if ids else (1, 1)
    return reply_in(lo, hi)


def words_of(para) -> int:
    return sum(len(s["en"].split()) for s in [para["lead"], *para["body"]])


def reply_in(lo: int, hi: int) -> dict:
    """合法回包，但 `src` **落在真实的 `[lo, hi]` 里**（端到端用的夹具，`lo`=1 起）。"""
    def ln(en, zh, src, **kw):
        return line(en, zh, src=(src,), **kw)

    return {"title": "Elasticity", "title_zh": "弹性",
            "sentences": [{"en": "one sentence", "zh": "一句", "src": [lo],
                           "terms": [], "flag": None}],
            "para": {"lead": ln("Demand is elastic when |Ed| > 1, so 1% price cuts 2%.",
                                 "当 |Ed| > 1 时需求富有弹性。", lo, bold=["富有弹性"],
                                 bold_en=["elastic"]),
                     "body": [ln("Revenue falls by 1%, because the quantity drop wins.",
                                 "总收入下降 1%。", hi, bold=["总收入下降"],
                                 bold_en=["Revenue falls"])],
                     "terms": [['price elasticity', '价格弹性']], "flag": None}}


# ---------------------------------------------------------------- ① 形状
def t_shape() -> None:
    print("\n--- ① 缺 `para` / `lead` 空 → 整段 `None`（旧回包照旧出章）---")
    check("缺 `para` → `None`", ch.parse_para(reply(para=None), LO, HI) is None)
    check("`para` 不是 dict → `None`", ch.parse_para(reply(para=[1, 2]), LO, HI) is None)
    check("`para` 是空 dict → `None`", ch.parse_para(reply(para={}), LO, HI) is None)
    check("`lead` 不是 dict → `None`", ch.parse_para(reply(para={"lead": "x"}), LO, HI) is None)
    for bad in ("", "   ", None):
        check(f"`lead.en` = {bad!r} → `None`",
              ch.parse_para(reply(para={"lead": line(bad, "中", src=(10,))}), LO, HI) is None)

    got = ch.parse_chapter(reply(para=None), LO, HI)
    check("⭐⭐ 旧回包（无 `para`）：`para` 为 `None` 但 `sentences` **照常**",
          got["para"] is None and len(got["sentences"]) == 1, str(got["sentences"]))

    ok = ch.parse_chapter(reply(), LO, HI)
    check("⭐⭐ 合法回包：两半都在",
          ok["para"] is not None and len(ok["sentences"]) == 1
          and ok["para"]["lead"]["src"] == [10, 11], str(sorted(ok)))


# ---------------------------------------------------------------- ② src
def t_src() -> None:
    print("\n--- ② `src` 越界 → **整段**丢（不是丢那一句）---")
    check("`lead.src` 空 → `None`",
          ch.parse_para(reply(para={"lead": line("en", "中", src=())}), LO, HI) is None)
    check("`lead.src` 越界（HI+1）→ `None`",
          ch.parse_para(reply(para={"lead": line("en", "中", src=(HI + 1,))}), LO, HI) is None)
    check("`lead.src` 越界（LO-1）→ `None`",
          ch.parse_para(reply(para={"lead": line("en", "中", src=(LO - 1,))}), LO, HI) is None)
    check("`lead.src` 形状不对（`src: 12`）→ `None`（闸门自己不许抛）",
          ch.parse_para(reply(para={"lead": {"en": "x", "zh": "", "src": 12}}), LO, HI) is None)
    _p = reply()
    _p["para"]["body"][1]["src"] = [HI + 5]
    check("⭐⭐ `body` 里有一句越界 → **整段** `None`（引用不实就不是同一段话了）",
          ch.parse_para(_p, LO, HI) is None)
    _p = reply()
    _p["para"]["body"].append(line("x", "中", src=[0]))
    check("`body` 里**追加**一句越界 → 整段 `None`", ch.parse_para(_p, LO, HI) is None)


# ---------------------------------------------------------------- ③ bold
def t_bold() -> None:
    print("\n--- ③ `bold` 必须**逐字**出现在对应文本里，每句 ≤3 ---")
    p = reply()
    p["para"]["lead"] = line("Demand is elastic when price rises 1%.",
                             "价格上涨 1% 时需求富有弹性。",
                             src=(10,),
                             bold=["富有弹性", "模型自己改写的一句", "不存在的词"],
                             bold_en=["elastic", "elasticity", "price rises"])
    para = ch.parse_para(p, LO, HI)
    check("⭐⭐ `bold` / `bold_en` 逐字对得上才留（对不上的丢）",
          para["lead"]["bold"] == ["富有弹性"]
          and para["lead"]["bold_en"] == ["elastic", "price rises"],
          str(para["lead"]["bold"]) + " / " + str(para["lead"]["bold_en"]))

    p = reply()
    p["para"]["lead"] = line("a b c d e", "一 二 三 四 五", src=(10,),
                             bold=["一", "二", "三", "四", "五"],
                             bold_en=["a", "b", "c", "d", "e"])
    para = ch.parse_para(p, LO, HI)
    check("⭐ 每句最多 3 处加粗（超了就截，不报错）；常量就是 3",
          ch.MAX_BOLD == 3 and len(para["lead"]["bold"]) == 3
          and len(para["lead"]["bold_en"]) == 3,
          str(para["lead"]["bold"]))

    p = reply()
    p["para"]["lead"] = line(" ".join(["w"] * 40), "结论", src=(10,))
    p["para"]["body"] = [line(" ".join(["x"] * 30), "一", src=(11,)),
                         line(" ".join(["y"] * 30), "二", src=(12,), bold=["二"])]
    para = ch.parse_para(p, LO, HI)
    check("⭐⭐ 超 90 词 → 从尾巴整句丢依据句（结论句在、不半句截断），丢完不超就不标 over",
          para["words"] == 70 and [b["zh"] for b in para["body"]] == ["一"]
          and para["para_over"] is False and para["lead"]["zh"] == "结论",
          str(para["words"]) + " " + str([b["zh"] for b in para["body"]]))
    p = reply()
    p["para"]["lead"] = line(" ".join(["w"] * 80), "结论", src=(10,))
    p["para"]["body"] = [line(" ".join(["x"] * 30), "一", src=(11,))]
    para = ch.parse_para(p, LO, HI)
    check("⭐ 至少留 1 句依据：只剩一句仍超 → 不再丢，标 para_over",
          para["words"] == 110 and len(para["body"]) == 1 and para["para_over"] is True)

    p = reply()
    p["para"]["lead"] = line("a b", "甲 乙", src=(10,), bold=["甲", "乙"], bold_en=["a", "b"])
    p["para"]["body"] = [line("c d", "丙 丁", src=(11,), bold=["丙", "丁"], bold_en=["c", "d"]),
                         line("e f", "戊 己", src=(12,), bold=["戊"], bold_en=["e"])]
    para = ch.parse_para(p, LO, HI)
    allb = [b for s in [para["lead"], *para["body"]] for b in s["bold"]]
    allbe = [b for s in [para["lead"], *para["body"]] for b in s["bold_en"]]
    check("⭐⭐ **整段**最多 3 处加粗，结论句先占（2026-11-17 真课重放：按句限 → 一段亮 9–11 处）",
          ch.MAX_PARA_BOLD == 3 and allb == ["甲", "乙", "丙"] and allbe == ["a", "b", "c"],
          str(allb) + " / " + str(allbe))

    p = reply()
    p["para"]["lead"] = line("elastic demand", "富有弹性", src=(10,),
                             bold=["富有弹性", "富有弹性", 3, ""],
                             bold_en=("elastic",))
    para = ch.parse_para(p, LO, HI)
    check("⭐ 重复 / 非字符串 / 空串都丢；tuple 形状也收",
          para["lead"]["bold"] == ["富有弹性"] and para["lead"]["bold_en"] == ["elastic"],
          str(para["lead"]))

    p = reply()
    p["para"]["lead"] = line("elastic", "字" * (ch.SENT_ZH_MAX_CHARS + 5), src=(10,),
                             bold=["字"])
    para = ch.parse_para(p, LO, HI)
    check("⭐ `zh` 超长 → **置空、不丢句**；`bold`（对 zh）随之全丢",
          para is not None and para["lead"]["zh"] == ""
          and para["lead"]["bold"] == [] and para["lead"]["en"] == "elastic")


# ---------------------------------------------------------------- ④ words
def t_words() -> None:
    print("\n--- ④ `words` 由代码重算；>90 只标记、不截断、不重试 ---")
    p = reply()
    p["para"]["words"] = 7                        # 模型自报一个漂亮数字
    para = ch.parse_para(p, LO, HI)
    check("⭐⭐ 不信模型自报的 `words`（按 `en` 重算）",
          para["words"] == words_of(para) and para["words"] != 7, str(para["words"]))
    check("正常长度 → `para_over` 为 False", para["para_over"] is False)

    long_body = [line("word " * 35, "词", src=(12,)), line("word " * 35, "词", src=(13,)),
                 line("word " * 35, "词", src=(14,))]
    p = reply(para={"lead": dict(LEAD), "body": long_body, "terms": [], "flag": None})
    para = ch.parse_para(p, LO, HI)
    # ⚠️ 2026-11-17 改判据（Claude，有意改行为）：原来「一句不删」；真课重放 6 段里 2 段 94–102 词，
    #    改成从尾巴整句丢依据句（见 ④' 与上面两条新判据）。这里钉：丢到不超或只剩 1 句为止，且 over 与词数一致。
    check("⭐⭐ 超过 90 词 → 从尾巴整句丢，直到不超或只剩 1 句依据；`para_over` 与词数一致",
          len(para["body"]) >= 1 and (para["words"] <= ch.MAX_PARA_WORDS or len(para["body"]) == 1)
          and para["para_over"] == (para["words"] > ch.MAX_PARA_WORDS)
          and para["words"] == words_of(para), f"words={para['words']} body={len(para['body'])}")
    check("⭐ 阈值取的是 `chapter.MAX_PARA_WORDS`（不是另写一个 90）",
          ch.MAX_PARA_WORDS == 90)


# ---------------------------------------------------------------- ⑦ 短章写短
def t_short_chapter() -> None:
    print("\n--- ⑦ 短章写短：每 SRC_PER_PARA_SENT 句原文最多 1 句 ---")
    k = ch.SRC_PER_PARA_SENT
    for n_src, want in ((1, 1), (3, 1), (k, 1), (k + 1, 2), (2 * k, 2), (2 * k + 1, 3), (100, 4)):
        p = reply()
        p["para"]["lead"] = line("Elastic demand means revenue falls when price rises.", "结论", src=(LO,))
        p["para"]["body"] = [line(f"Body sentence {i}.", "中", src=(LO,)) for i in range(3)]
        para = ch.parse_para(p, LO, LO + n_src - 1)
        got = 1 + len(para["body"])
        check(f"原文 {n_src} 句 → 段落 {want} 句（结论句永远在）",
              got == want and para["lead"]["en"] == p["para"]["lead"]["en"], f"got={got}")
    p = reply()
    p["para"]["lead"] = line("Elastic demand means revenue falls when price rises.", "结论", src=(LO,))
    p["para"]["body"] = [line(f"Body sentence {i}.", "中", src=(LO,)) for i in range(3)]
    para = ch.parse_para(p, LO, LO + 2)
    check("⭐ 丢句后 `words` 按留下的句子重算", para["words"] == words_of(para), str(para["words"]))


# ---------------------------------------------------------------- ⑤ 其余字段
def t_fields() -> None:
    print("\n--- ⑤ `body` 单句 / `terms` / `flag` 的闸门 ---")
    p = reply()
    p["para"]["body"].append(line("", "空 en", src=[14]))
    para = ch.parse_para(p, LO, HI_LONG)
    check("⭐ `body` 单句 `en` 空 → 丢**这一句**（其余保留）",
          len(para["body"]) == 2, str([b["en"][:12] for b in para["body"]]))

    p = reply()
    p["para"]["body"] = [line(f"s{i}", "中", src=(12,)) for i in range(6)]
    para = ch.parse_para(p, LO, HI_LONG)
    check(f"⭐ `body` 最多收 {ch.MAX_PARA_BODY} 句（+`lead` 共 4 句封顶）",
          len(para["body"]) == ch.MAX_PARA_BODY == 3)

    p = reply()
    p["para"]["terms"] = [["price elasticity", "价格弹性"], ["a"], 3, ["", "空"]]
    para = ch.parse_para(p, LO, HI)
    check("⭐ `terms` 走 `atom.terms_of`（形状不对的丢）",
          para["terms"] == [["price elasticity", "价格弹性"]], str(para["terms"]))

    for bad in ("board2", 1, True):
        check(f"`flag` = {bad!r} → `None`（fail-open）",
              ch.parse_para(reply(para={"lead": dict(LEAD), "body": [], "flag": bad}),
                            LO, HI)["flag"] is None)
    check("`flag = \"board\"` 照收",
          ch.parse_para(reply(para={"lead": dict(LEAD), "body": [], "flag": "board"}),
                        LO, HI)["flag"] == "board")
    check("`body` 非列表 → 当成没有 body（`lead` 仍在）",
          ch.parse_para(reply(para={"lead": dict(LEAD), "body": 3}), LO, HI)["body"] == [])


# ---------------------------------------------------------------- ⑥ Chapter 落盘
def t_dataclass() -> None:
    print("\n--- ⑥ `Chapter.para`：默认 `None`、`as_json` 带上、写盘读回都在 ---")
    base = dict(id=1, status="final", title="T", title_zh="题",
                t0="10:00:00", t1="10:05:00", lo=LO, hi=HI,
                sentences=[{"en": "e", "zh": "中", "src": [10]}])
    old = ch.Chapter(**base)
    check("⭐ 老调用（不带 `para`）不报错，默认 `None`", old.para is None)
    check("`as_json` 里 `para` 键**总是存在**（值为 `None`）",
          "para" in old.as_json() and old.as_json()["para"] is None)

    para = ch.parse_para(reply(), LO, HI)
    with tempfile.TemporaryDirectory() as d:
        path = pathlib.Path(d) / "S.chapters.jsonl"
        # 先写一条**旧格式**行（没有 `para` 键、另一个 id）：老客户端 / 老会话的形状
        path.write_text('{"type": "chapter", "id": 2, "status": "final", "lo": 10, '
                        '"hi": 20, "sentences": []}\n', encoding="utf-8")
        w = ch.ChapterWriter(path)
        check("写盘成功", w.append([ch.Chapter(**base, para=para)]) == 1)
        w.close()
        got = ch.load(path)
        by_id = {c["id"]: c for c in got["chapters"]}
        check("⭐⭐ 读回后 `para` 原样在（新字段不丢）",
              by_id[1].get("para") == para, str(by_id[1].get("para")))
        check("⭐⭐ 旧行（无 `para` 键）读回不抛、`.get('para')` 是 `None`",
              by_id[2].get("para") is None and by_id[2]["hi"] == 20)
        check("老字段照旧：`sentences` 还在",
              len(by_id[1].get("sentences") or []) == 1)

    # 没有 `para` 的章写出来是 `"para": null` —— 读回方 `.get("para")` 拿到 `None`
    with tempfile.TemporaryDirectory() as d:
        path = pathlib.Path(d) / "E.chapters.jsonl"
        w = ch.ChapterWriter(path)
        w.append([old])
        w.close()
        txt = path.read_text(encoding="utf-8")
        check("⭐ 写盘时 `para` 写的是 `null`（不是省略键）", '"para": null' in txt, txt.strip())
        got = ch.load(path)
        check("读回后是 `None`", got["chapters"][0].get("para") is None)


# ---------------------------------------------------------------- ⑦ prompt
def t_prompt() -> None:
    print("\n--- ⑦ `build_prompt`：前几章给**标题 + 一行结论** ---")
    p = ch.build_prompt([(10, "Demand slopes down.")], [],
                        ["Intro", "Elasticity"],
                        ["这一章讲的是稀缺性。", ""])
    check("⭐⭐ 有结论的章给「标题｜结论：」", "- Intro｜结论：这一章讲的是稀缺性。" in p)
    check("⭐ 没结论的章**只给标题**（不留空行、不编）", "- Elasticity\n" in p)
    check("⭐⭐ 明写「与它们重复的句子必须删」", "与它们重复的句子必须删" in p)
    check("旧调法（只给标题）照旧可用、且不出现「结论：」",
          "- Intro" in ch.build_prompt([], [], ["Intro"]) 
          and "结论：" not in ch.build_prompt([], [], ["Intro"]))
    check("`prior_leads` 比标题短 → 不抛、后面的只给标题",
          "- B\n" in ch.build_prompt([], [], ["A", "B"], ["只有 A 有结论"]))
    check("`prior_leads` 比标题长 → 不抛",
          "- A｜结论：x\n" in ch.build_prompt([], [], ["A"], ["x", "y"]))
    check("`prior_leads` 给了 `None` 项 → 只给标题",
          "- A\n" in ch.build_prompt([], [], ["A"], [None]))
    check("没有前章 → 整段不出现（省 token）",
          "前面几章" not in ch.build_prompt([], [], []))


# ---------------------------------------------------------------- ⑧ 首段 180 秒
class Harness:
    """`LiveSummarizer` + 假模型（按 **system prompt 身份** 分发，别按文本前缀）。"""

    def __init__(self, tmp: str, topics, chap_fn=None, on_chap=None):
        self.calls: list[str] = []
        self.emits: list[dict] = []
        self._topics = iter(topics)
        self._chap_fn = chap_fn
        self._on_chap = on_chap

        def chat(sysp, block, max_tokens, temperature):
            if sysp is atom.SYS:
                self.calls.append("atom")
                t = next(self._topics)
                return {"topic": t, "topic_zh": t + "中",
                        "points": [{"text": "p", "kind": "要点", "src": [1], "terms": []}]}
            assert sysp is ch.SYS_CHAPTER, "只该有两种 system prompt"
            self.calls.append("chap")
            if self._on_chap is not None:
                self._on_chap(block)
            return self._chap_fn(block) if self._chap_fn else default_chap(block)

        self.s = L.LiveSummarizer(
            chat=chat, append_atoms=lambda a: len(a),
            chapter_path=ch.chapter_path_for(pathlib.Path(tmp) / "S.md"),
            emit=self.emits.append)

    def say(self, gid: int, flush_at: float) -> None:
        """喂一句，并让它**在 `flush_at` 这一刻收窗**。

        ⚠️ 收窗要 `flush_at - 最后一句的时刻 >= ATOM_PAUSE_S`（30 秒），所以内部
           把「喂的时刻」定在 `flush_at - 31`。那个中间 `step` 也靠得离阈值远，
           不会自己触发临时合成（否则判据就测不准边界了）。
        """
        self.s.feed((gid, "10:00:%02d" % gid, "sentence %d" % gid, "中%d" % gid))
        self.s.step(flush_at - 31.0)
        self.s.step(flush_at)

    def chap_calls(self) -> int:
        return self.calls.count("chap")


def t_first_interim() -> None:
    print("\n--- ⑧ 首段临时合成：180 秒就出（之后回到 480 秒）---")
    check("门槛常量就是 180（Owen 11-17：首段 3 分钟出）",
          L.FIRST_INTERIM_AFTER_S == 180.0 and L.INTERIM_AFTER_S == 480.0
          and L.FIRST_INTERIM_AFTER_S < L.INTERIM_AFTER_S)
    # ⚠️ 这两个数的**相互作用**：一章最多 600 秒（`MAX_CHAPTER_S`），而首段 180 秒后
    #    再等 480 秒 = 660 > 600 → **真实参数下同一章只会出一次临时合成**。
    #    这不是 bug，是要说清楚的账（钱花得比以前少）；下面把 `MAX_CHAPTER_S` 抬高
    #    才能单独把 480 那一档测出来。
    check("⭐ 真实参数下：180 + 480 > 一章上限 600（所以一章只出一次临时版）",
          L.FIRST_INTERIM_AFTER_S + L.INTERIM_AFTER_S > L.MAX_CHAPTER_S,
          f"{L.FIRST_INTERIM_AFTER_S} + {L.INTERIM_AFTER_S} > {L.MAX_CHAPTER_S}")

    with tempfile.TemporaryDirectory() as d:
        h = Harness(d, ["A"] * 8)
        h.say(1, 190.0)                         # 章在这一刻开张（t_open = 190）
        h.say(2, 369.0)                         # 章龄 179 秒
        check("⭐⭐ 179 秒**不出**首段", h.chap_calls() == 0, f"calls={h.calls}")
        h.say(3, 371.0)                         # 章龄 181 秒
        check("⭐⭐ 181 秒**出**首段（临时版）", h.chap_calls() == 1, f"calls={h.calls}")
        cs = [e["chapter"] for e in h.emits if e.get("kind") == "chapter"]
        check("出的是 `interim` 而不是正式章",
              len(cs) == 1 and cs[0]["status"] == "interim", str([c["status"] for c in cs]))

    with tempfile.TemporaryDirectory() as d:
        # ⚠️ 把一章上限抬高，否则 180+480 > 600 永远到不了第二档（见上面那条检查）。
        old_max = L.MAX_CHAPTER_S
        L.MAX_CHAPTER_S = 5000.0
        try:
            h = Harness(d, ["A"] * 8)
            h.say(1, 190.0)
            h.say(2, 371.0)                     # 首段（章龄 181）
            h.say(3, 671.0)                     # 距上次临时 300 秒
            check("⭐ 第二次仍按 480 秒：300 秒不出", h.chap_calls() == 1, f"calls={h.calls}")
            h.say(4, 847.0)                     # 距上次临时 476 秒
            check("⭐ 476 秒仍不出", h.chap_calls() == 1, f"calls={h.calls}")
            h.say(5, 851.0)                     # 480 秒
            check("⭐⭐ 480 秒出（回到 `INTERIM_AFTER_S`）", h.chap_calls() == 2,
                  f"calls={h.calls}")
        finally:
            L.MAX_CHAPTER_S = old_max

    with tempfile.TemporaryDirectory() as d:
        # 换题 → 新章 → 首段**重新**享受 180 秒（`_absorb` 里那个开关）
        h = Harness(d, ["A", "A", "B", "B", "B"])
        h.say(1, 190.0)                         # 章 1 开张
        h.say(2, 371.0)                         # 章 1 首段在章龄 181 秒出
        n1 = h.chap_calls()
        h.say(3, 374.0)                         # 换题 → 章 1 正式合成 + 章 2 开张
        n2 = h.chap_calls()
        check("⭐ 换题时做了正式合成（章 1 封章）", n2 == n1 + 1, f"{n1} -> {n2}")
        h.say(4, 553.0)                         # 章 2 章龄 179 秒
        check("⭐⭐ 新章重来：179 秒不出", h.chap_calls() == n2, f"calls={h.calls}")
        h.say(5, 556.0)                         # 章 2 章龄 182 秒
        check("⭐⭐ 新章 182 秒出（首段那一档跟着章重置）",
              h.chap_calls() == n2 + 1, f"calls={h.calls}")


# ---------------------------------------------------------------- ⑨ 端到端
def t_end_to_end() -> None:
    print("\n--- ⑨ 端到端：`_synthesize` 把 `para` 带进 `Chapter` 且喂给下一章 prompt ---")
    seen_blocks: list[str] = []

    with tempfile.TemporaryDirectory() as d:
        h = Harness(d, ["A", "A", "B", "B", "B"], on_chap=seen_blocks.append)
        h.say(1, 190.0)
        h.say(2, 371.0)                         # 章 1 首段（interim）
        h.say(3, 374.0)                         # 换题 → 章 1 正式 + 章 2 开张
        h.say(4, 553.0)
        h.say(5, 556.0)                         # 章 2 用带前章结论的 prompt 合成
        cs = [e["chapter"] for e in h.emits if e.get("kind") == "chapter"]
        fin = [c for c in cs if c["status"] == "final"]
        check("⭐⭐ 正式章带 `para`", bool(fin) and fin[0].get("para") is not None,
              str([(c["status"], bool(c.get("para"))) for c in cs]))
        check("`para` 里 `words` 是算出来的（非 None）",
              bool(fin) and isinstance((fin[0].get("para") or {}).get("words"), int))
        later = [b for b in seen_blocks if "前面几章讲的是" in b]
        check("⭐⭐ 下一章的 prompt 里有上一章的**结论**（不只是标题）",
              bool(later) and "｜结论：当 |Ed| > 1 时需求富有弹性。" in later[-1],
              later[-1][:120] if later else "没有带前章的 block")


def main() -> int:
    t_shape()
    t_src()
    t_bold()
    t_words()
    t_short_chapter()
    t_fields()
    t_dataclass()
    t_prompt()
    t_first_interim()
    t_end_to_end()
    n = len(RESULTS)
    ok = sum(1 for _, o, _ in RESULTS if o)
    print("\n" + "=" * 60)
    print(f"{ok}/{n} 通过")
    for name, o, detail in RESULTS:
        if not o:
            print(f"  ❌ {name}  {detail}")
    return 0 if ok == n else 1


if __name__ == "__main__":
    sys.exit(main())
