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
| `FIRST_INTERIM_AFTER_S` 0 → 480 | ⑧「开章那一窗立刻出首段」 |
| `_absorb` 里去掉 `self._interim_first = True` | ⑧「新章在它开张那一窗立刻出首段」 |
| `_absorb_window` 里去掉 `self._maybe_interim(now)` | ⑨b「吸收窗口那一瞬间就刷新」 |
| `REFRESH_GAP_S` 60 → 600 | ⑨「60 秒那一档才刷新」 |
| `REFRESH_MIN_NEW` 3 → 1 | ⑨「只多 2 句不刷新」 |
| `MAX_CHAPTER_S` 900 → 600 | ⑩「899 秒不强切、901 秒强切」 |
| `_synthesize` 里 `interim` 失败也照写空章 | ⑪「interim 失败不 emit」 |
| `_synthesize` 里 `final` 失败写空章（不用上一版） | ⑪「final 失败用上一版」 |
| 去掉 `_synthesize` 开头「句数没变就重写上次结果」 | ⑫「封章无新句不调模型」 |
| `build_prompt(..., prev_para=None)` 写死 `None` | ⑬「第二次合成带上一版段落」 |
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


# ---------------------------------------------------------------- ⑧ 刷新节奏
class Harness:
    """`LiveSummarizer` + 假模型（按 **system prompt 身份** 分发，别按文本前缀）。"""

    def __init__(self, tmp: str, topics, chap_fn=None, on_chap=None, fail_chap=0):
        self.calls: list[str] = []
        self.emits: list[dict] = []
        self.blocks: list[str] = []
        self._topics = iter(topics)
        self._chap_fn = chap_fn
        self._on_chap = on_chap
        #: 前 `fail_chap` 次章节调用直接抛（模拟网络抖动）；之后恢复正常。
        self._fail_left = fail_chap
        self._base = None

    def chat(self, sysp, block, max_tokens, temperature):
        if sysp is atom.SYS:
            self.calls.append("atom")
            t = next(self._topics)
            return {"topic": t, "topic_zh": t + "中",
                    "points": [{"text": "p", "kind": "要点", "src": [1], "terms": []}]}
        assert sysp is ch.SYS_CHAPTER, "只该有两种 system prompt"
        self.calls.append("chap")
        self.blocks.append(block)
        if self._on_chap is not None:
            self._on_chap(block)
        if self._fail_left > 0:
            self._fail_left -= 1
            raise RuntimeError("offline")
        return self._chap_fn(block) if self._chap_fn else default_chap(block)

    def build(self):
        self.s = L.LiveSummarizer(
            chat=self.chat, append_atoms=lambda a: len(a),
            chapter_path=ch.chapter_path_for(pathlib.Path(self._tmp) / "S.md"),
            emit=self.emits.append)
        return self.s

    def say(self, gid: int, flush_at: float, step_first: float | None = None) -> None:
        """喂一句，并让它**在 `flush_at` 这一刻收窗**。

        ⚠️ 收窗要 `flush_at - 最后一句的时刻 >= ATOM_PAUSE_S`（30 秒），所以内部
           把「喂的时刻」定在 `flush_at - 31`。那个中间 `step` 也靠得离阈值远，
           不会自己触发临时合成（否则判据就测不准边界了）。
        """
        self.s.feed((gid, "10:00:%02d" % gid, "sentence %d" % gid, "中%d" % gid))
        self.s.step(flush_at - 31.0 if step_first is None else step_first)
        self.s.step(flush_at)

    def chap_calls(self) -> int:
        return self.calls.count("chap")

    def chapters(self) -> list:
        return [e["chapter"] for e in self.emits if e.get("kind") == "chapter"]


def _harness(d: str, topics, **kw) -> Harness:
    h = Harness(d, topics, **kw)
    h._tmp = d
    h.build()
    return h


def t_first_interim() -> None:
    print("\n--- ⑧ 首段临时合成：开章那一窗吸收完就出（0 秒）---")
    check("门槛常量就是 0（2026-11-17 test key 重放实测：真正首段延迟 165–185 s，"
          "因为章是第一个窗口吸收时才开的）",
          L.FIRST_INTERIM_AFTER_S == 0.0
          and L.REFRESH_GAP_S == 60.0 and L.REFRESH_MIN_NEW == 3)

    with tempfile.TemporaryDirectory() as d:
        h = _harness(d, ["A"] * 8)
        h.say(1, 190.0)                         # 开章那一窗（t_open = 190）→ 立刻出首段
        check("⭐⭐ 开章那一窗吸收完**立刻**有一次章节调用",
              h.chap_calls() == 1, f"calls={h.calls}")
        cs = h.chapters()
        check("出的是 `interim` 而不是正式章",
              len(cs) == 1 and cs[0]["status"] == "interim", str([c["status"] for c in cs]))
        h.say(2, 200.0)                         # 距上次 10 s、新增 1 句 -> 两道闸都不够
        check("⭐ 紧接着那一窗不刷新（60 s + 3 句那两道闸）",
              h.chap_calls() == 1, f"calls={h.calls}")

    with tempfile.TemporaryDirectory() as d:
        # 换题 → **新章**同样在它开张那一窗就出首段（`_absorb` 里那个开关跟着重置）
        h = _harness(d, ["A", "A", "B", "B", "B"])
        h.say(1, 190.0)                         # 章 1 开张 + 立刻首段
        check("章 1 开张即出首段", h.chap_calls() == 1, f"calls={h.calls}")
        h.say(2, 281.0)                         # 章龄 91、新增 1 句 -> 不刷（第二道闸）
        n1 = h.chap_calls()
        h.say(3, 375.0)                         # 换题（章龄 185 > MIN 180）→ 章 1 封章 + 章 2 开张
        check("⭐ 换题时把章 1 封成 `final`",
              any(c["status"] == "final" for c in h.chapters()),
              str([(c["id"], c["status"]) for c in h.chapters()]))
        n2 = h.chap_calls()
        # ⚠️ 这一下有两笔：`_finalize_current` 真调了一次（章 1 有 2 句、上次成功合成时
        #    只有 1 句 -> 句数变了，不走“省一次调用”）+ 新章开张那一窗立刻补一次临时合成。
        new_ch = [c for c in h.chapters() if c["id"] == max(x["id"] for x in h.chapters())]
        check("⭐⭐ 新章在它开张那一窗**立刻**出首段（不是等 90 秒）",
              n2 == n1 + 2 and bool(new_ch) and new_ch[0]["status"] == "interim",
              f"{n1} -> {n2} · 新章={[(c['status'], c['version']) for c in new_ch]}")
        h.say(4, 464.0)                         # 距新章首段 89 s、新增 1 句 -> 不刷
        check("⭐ 新章之后照旧走两道闸：89 秒但没新增 3 句 -> 不刷",
              h.chap_calls() == n2, f"calls={h.calls}")

    with tempfile.TemporaryDirectory() as d:
        # ⭐ 开章**之前**（开场那些空主题的窗口还在攒，`_cur` 已经开但没定题 / 或者
        #    一个窗口都没吸收）不许有章节调用 —— 没内容可写。
        h = _harness(d, [""] * 6)
        h.say(1, 100.0)                         # 空主题的第一窗：照样开章收下 -> 立刻出首段
        check("⭐ 空主题的第一窗也算开章（照样立刻出首段；没内容才不合成）",
              h.chap_calls() == 1, f"calls={h.calls}")
        h.s.feed((2, "10:00:02", "s2", "中")); h.s.step(101.0)
        n = h.chap_calls()
        check("⭐⭐ 只是喂句、窗口还没闭合时**不**合成", h.chap_calls() == n and n == 1,
              f"calls={h.calls}")


# ---------------------------------------------------------------- ⑨ 刷新两道闸
_ROWS = [(int(x), float(t)) for x, t in
         [(1, 190.0), (2, 200.0), (3, 210.0), (4, 270.0),
          (5, 330.0), (6, 350.0), (7, 360.0), (8, 370.0)]]


def _drive(h: Harness, rows) -> None:
    """直接按 `(句号, 收窗时刻)` 推进 —— 要精确控「吸收在哪一刻」。"""
    prev = None
    for gid, t in rows:
        if prev is None:
            h.say(gid, t)
        else:
            h.say(gid, t, step_first=t - 1.0)   # 喂的时刻紧跟上一刻，不会被 75 s 切窗
        prev = t


def t_refresh() -> None:
    print("\n--- ⑨ 刷新：吸收窗口后 60 s + 3 句（两道闸都要满足）---")
    with tempfile.TemporaryDirectory() as d:
        h = _harness(d, ["A"] * 20)
        # ⚠️ 2026-11-17 起**开章那一窗就出首段**（`FIRST_INTERIM_AFTER_S`=0），所以
        #    基准点变成「开章那一下」。下面每条 check 的秒数是算过的，不是猜的。
        h.say(1, 190.0)                         # 开章那一窗（1 句）→ 首段
        check("前置：开章那一窗就出了首段（1 次调用）", h.chap_calls() == 1,
              f"calls={h.calls}")
        h.say(2, 200.0)                         # 距上次 10 s，2 句
        check("⭐ 距上次只 10 秒 → 不刷新（第一道闸）", h.chap_calls() == 1,
              f"calls={h.calls}")
        h.say(3, 210.0)                         # 距上次 20 s，3 句
        check("⭐ 新增 2 句但只过 20 秒 → 仍不刷新", h.chap_calls() == 1,
              f"calls={h.calls}")
        h.say(4, 270.0)                         # 距上次 80 s，4 句（新增 3）
        check("⭐⭐ 够 60 秒 + 新增 3 句 → 刷新", h.chap_calls() == 2, f"calls={h.calls}")
        h.say(5, 320.0)                         # 距上次 50 s，5 句
        check("⭐ 距上次只 50 秒（虽然又多了 1 句）→ 不刷新", h.chap_calls() == 2,
              f"calls={h.calls}")
        h.say(6, 340.0)                         # 距上次 70 s，6 句（新增 2）
        check("⭐⭐ 够 60 秒但只新增 2 句 → 不刷新（第二道闸）", h.chap_calls() == 2,
              f"calls={h.calls}")
        h.say(7, 350.0)                         # 距上次 80 s，7 句（新增 3）
        check("⭐⭐ 60 秒 + 新增 3 句 → 刷新", h.chap_calls() == 3, f"calls={h.calls}")
        h.say(8, 360.0)                         # 距上次 10 s → 不刷
        check("⭐ 又只过 10 秒 → 不刷新（3 次为终值）", h.chap_calls() == 3,
              f"calls={h.calls}")
        st = h.s.stats()
        check("采集面：`chapter_calls` == 真调的次数",
              st["chapter_calls"] == 3, str({k: st[k] for k in
                                            ("chapter_calls", "chapters_interim")}))
        check("两版共用一个 id（右栏按 id 覆盖）",
              len({c["id"] for c in h.chapters()}) == 1,
              str([(c["id"], c["status"], c["version"]) for c in h.chapters()]))


def t_absorb_hook() -> None:
    """⭐ 刷新必须挂在**吸收窗口**那一刻（`step` 里那道只是兜底）。

    ⚠️ 为什么要直接对着 `_absorb_window` 测：正常路径下 `step` 紧接着也会调一次
       `_maybe_interim(now)`，两道口子效果一样 —— 只测 `step` 的话，把吸收那一行删掉
       测试照样绿（变异验证抓到过一次）。
    """
    print("\n--- ⑨b 吸收窗口就刷新（不靠 `step` 的兜底）---")
    obj = {"topic": "A", "topic_zh": "A中",
           "points": [{"text": "p", "kind": "要点", "src": [3], "terms": []}]}

    def buf(*gids):
        return [(g, "10:00:%02d" % g, "sentence %d" % g, "中%d" % g) for g in gids]

    with tempfile.TemporaryDirectory() as d:
        h = _harness(d, ["A"] * 20)
        h.say(1, 190.0)                          # 开章那一窗 -> 立刻首段
        n = h.chap_calls()
        h.s._absorb_window(buf(2, 3, 4), obj, 250.0)     # 距上次 60 s、新增 3 句
        check("⭐⭐ 吸收窗口那一瞬间就刷新（同一调用里完成）",
              h.chap_calls() == n + 1, f"{n} -> {h.chap_calls()}")

    with tempfile.TemporaryDirectory() as d:
        h = _harness(d, ["A"] * 20)
        h.say(1, 190.0)                          # 开章那一窗 -> 立刻首段
        n = h.chap_calls()
        h.s._absorb_window(buf(2, 3), obj, 200.0)        # 距上次 10 s、新增 2 句 -> 两道闸都不够
        check("⭐⭐ 闸门不满足时吸收也不刷新", h.chap_calls() == n, f"{n} -> {h.chap_calls()}")


# ---------------------------------------------------------------- ⑩ 强切阈值
def t_max_chapter() -> None:
    print("\n--- ⑩ `MAX_CHAPTER_S` = 900：主题粘住时 15 分钟兜一刀 ---")
    check("常量就是 900（15 分钟）", L.MAX_CHAPTER_S == 900.0, str(L.MAX_CHAPTER_S))

    def run_at(age: float, d: str) -> int:
        """章龄 = `age` 那一刻：封了几个 `final`（强切才封）。"""
        h = _harness(d, ["A"] * 4)
        h.say(1, 190.0)
        h.say(2, 190.0 + age)
        return sum(1 for c in h.chapters() if c["status"] == "final")

    with tempfile.TemporaryDirectory() as d:
        check("⭐⭐ 章龄 899 秒**不**强切", run_at(899.0, d) == 0)
    with tempfile.TemporaryDirectory() as d:
        check("⭐⭐ 章龄 901 秒强切（多一条 final）", run_at(901.0, d) == 1)


# ---------------------------------------------------------------- ⑪ 失败不清屏
def t_fail() -> None:
    print("\n--- ⑪ 合成失败：interim 不清屏 / final 用上一版 ---")
    with tempfile.TemporaryDirectory() as d:
        # 首段成功一次 → 之后三条新句（过两闸）→ 两次尝试都抛 → 不许 emit 空章
        h = _harness(d, ["A"] * 20)
        h.say(1, 190.0)
        h.say(2, 281.0)                          # 首段成功（章内 2 句）
        ok_chap = len(h.chapters())
        first = h.chapters()[-1]
        h._fail_left = 2                         # ⚠️ 失败后重试一次，所以两次都挂
        h.say(3, 360.0)                          # 距上次 79 s，新增 1 句 -> 不刷
        h.say(4, 370.0)                          # 新增 2 -> 不刷
        h.say(5, 380.0)                          # 新增 3 -> 尝试（失败×2）
        cs = h.chapters()
        check("⭐⭐ interim 失败**不 emit**（上一版段落仍是最后一条）",
              len(cs) == ok_chap and cs[-1]["id"] == first["id"],
              f"{ok_chap} -> {len(cs)}")
        check("⭐ 失败数照记（failed_chapter_calls 增加）",
              h.s.stats()["failed_chapter_calls"] > 0, str(h.s.stats()["failed_chapter_calls"]))

    with tempfile.TemporaryDirectory() as d:
        h = _harness(d, ["A"] * 20)
        h.say(1, 190.0)
        h.say(2, 281.0)                          # 距开章 91 s、新增 1 句 -> 不刷
        h.say(3, 290.0)
        h.say(4, 300.0)
        h.say(5, 310.0)                          # 距开章 120 s、新增 4 句 -> **会**刷一次
        ok_para = h.chapters()[-1]["para"]
        # ⚠️ 必须让句数**变了**（相对上次成功合成），否则封章走「省一次调用」那条路、
        #    根本不调模型（Claude 上一轮抓到的假绿就是这么来的）。
        h.say(6, 320.0)                          # 距上次合成 10 s -> 不刷，只多一句
        n_before = h.chap_calls()
        check("前置：最后那句没有触发临时合成（距上次不足 60 秒）",
              h.chap_calls() == n_before, f"{n_before} -> {h.chap_calls()}")
        h._fail_left = 2
        h.s.finish(timeout_s=2.0)                # 封章两次尝试都挂
        fin = [c for c in h.chapters() if c["status"] == "final"]
        check("⭐⭐ final 失败 → 用上一版内容写 final（不是空章）",
              bool(fin) and fin[-1]["para"] == ok_para and bool(fin[-1]["sentences"]),
              str([(c["status"], bool(c.get("para")), len(c["sentences"]))
                   for c in h.chapters()]))


# ---------------------------------------------------------------- ⑫ 封章省一次调用
def t_finalize_free() -> None:
    print("\n--- ⑫ 封章无新句：不调模型，仍写出 final ---")
    with tempfile.TemporaryDirectory() as d:
        h = _harness(d, ["A"] * 20)
        h.say(1, 190.0)                          # 开章那一窗就出首段（1 句）
        n = h.chap_calls()
        check("前置：开章那一窗出了首段", n == 1, f"calls={h.calls}")
        h.s.finish(timeout_s=2.0)                # 句数没变 → 不调模型
        check("⭐⭐ 封章没有新句 → chat 调用次数不增加", h.chap_calls() == n,
              f"{n} -> {h.chap_calls()}")
        fin = [c for c in h.chapters() if c["status"] == "final"]
        check("⭐⭐ 但仍然写出 `final`（version + 1）",
              bool(fin) and fin[-1]["version"] > h.chapters()[-2]["version"]
              if len(h.chapters()) >= 2 else False,
              str([(c["status"], c["version"]) for c in h.chapters()]))
        cs = h.chapters()
        check("⭐ 省调用那版 final 的标题 = 上一版模型给的标题（不退回窗口主题）",
              len(cs) >= 2 and cs[-1]["title"] == cs[-2]["title"]
              and cs[-1]["title_zh"] == cs[-2]["title_zh"],
              str([(c["status"], c["title"], c["title_zh"]) for c in cs]))
        st = h.s.stats()
        check("采集面：写了 1 章但只调了 1 次模型",
              st["chapters_written"] == 1 and st["chapter_calls"] == n, str(st))


# ---------------------------------------------------------------- ⑬ prev_para
def t_prev_para() -> None:
    print("\n--- ⑬ 第二次合成把上一版 `para` 传给模型 ---")
    if not _has_prev_para():
        RESULTS.append(("⚠ chapter.build_prompt 还没有 `prev_para`（另一个 pi 在改）",
                        False, ""))
        print("  ❌ chapter.build_prompt 还没有 `prev_para` 关键字（另一个 pi 在改）")
        return
    with tempfile.TemporaryDirectory() as d:
        blocks: list[str] = []
        h = _harness(d, ["A"] * 20, on_chap=blocks.append)
        h.say(1, 190.0)
        h.say(2, 281.0)                          # 首段（第一版）
        lead1 = h.chapters()[-1]["para"]["lead"]["en"]      # ⚠️ `build_prompt` 带的是 en
        h.say(3, 360.0)                          # 新增 1
        h.say(4, 370.0)                          # 新增 2
        h.say(5, 380.0)                          # 新增 3 -> 第二版
        check("前置：做了两次合成", len(blocks) >= 2, str(len(blocks)))
        check("⭐⭐ 第二次的 prompt 里带上一版的段落",
              len(blocks) >= 2 and lead1 in blocks[1],
              (blocks[1][:200] if len(blocks) >= 2 else "没有第二次"))
        check("⭐ 第一次的 prompt 里没有上一版的结论句（没东西可带）",
              len(blocks) >= 2 and lead1 not in blocks[0], blocks[0][:120])


def _has_prev_para() -> bool:
    import inspect
    return "prev_para" in inspect.signature(ch.build_prompt).parameters



# ---------------------------------------------------------------- ⑨ 端到端
def t_end_to_end() -> None:
    print("\n--- ⑨ 端到端：`_synthesize` 把 `para` 带进 `Chapter` 且喂给下一章 prompt ---")
    seen_blocks: list[str] = []

    with tempfile.TemporaryDirectory() as d:
        h = _harness(d, ["A", "A", "B", "B", "B"], on_chap=seen_blocks.append)
        h.say(1, 190.0)
        h.say(2, 281.0)                         # 章 1 首段（interim）
        h.say(3, 375.0)                         # 换题 → 章 1 封章 + 章 2 开张
        h.say(4, 470.0)
        h.say(5, 473.0)                         # 章 2 首段（章龄 98）→ 带前章结论
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
    t_refresh()
    t_absorb_hook()
    t_max_chapter()
    t_fail()
    t_finalize_free()
    t_prev_para()
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
