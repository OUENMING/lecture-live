#!/usr/bin/env python3
"""⭐ 增量重写：`chapter.build_prompt(..., prev_para=…)` + 重放量具的「时效」表。**全离线。**

    ClassLive.app/Contents/MacOS/python tests/test_chapter_prev_para.py

## 为什么单开一个文件

`test_chapter_para.py` 覆盖的是 `parse_para` 那一层（模型回包 → 可落盘数据）；这里是
**反方向**（上一版数据 → 提示词），外加 `scripts/summary_replay.py` 里三个纯函数的离线自测。
两者都不联网、不写盘，所以放快档：改 `chapter.py` 的 `build_prompt` 能立刻知道破没破。

## 三条判据各自防的是什么

① **`prev_para=None` 与「不传」逐字相同** —— 防「加了参数顺手改了一下 prompt 排版」。
   没有这一条，一次无关的措辞微调会让**所有**真课调用的缓存前缀失效、也让
   `test_chapter_para` ⑦ 那几条悄悄变成测另一样东西。这是本节**最硬**的一条。
② **上一版每句 `en` 都逐字出现在 prompt 里** —— 防「转成了中文 / 截断了 / 只给 `lead`」。
   模型看不见原句就没有「在上一版基础上补」这回事，只会重新写一遍。
③ **坏形状按 `None` 处理、不崩** —— `prev_para` 是**上一次模型输出**过闸门后的东西，
   但它可能来自旧版本、也可能是别处拼的 dict。闸门自己炸掉会打死整个总结线程
   （同 `parse_reply` / `parse_para` 那条纪律：宁可少一节 prompt，不可抛）。

## 变异验证记录（每条都真改坏过、确认会红、再改回）

| 改坏哪一行 | 哪条红 |
|---|---|
| `build_prompt` 末尾无条件 `parts.append("")` | ①「与冻结原文逐字相同」 |
| `_prev_para_block` 里只取 `lead`（去掉 `body` 循环） | ②「上一版每句 EN 都在」 |
| `_prev_para_block` 里 `en` 改成取 `zh` | ②「上一版每句 EN 都在」 |
| `_prev_para_block` 去掉 `if not isinstance(body, (list, tuple))` | ③「body 不是 list 不崩」 |
| 去掉 `if not isinstance(prev_para, dict)` | ③「`prev_para` 不是 dict 不崩」 |
| `emit_record` 的 `has_para` 改成恒 `True` | ④「没 para 的 emit 记 `has_para=False`」 |
| `timeliness` 的 `gaps` 改成 `zip(ts, ts)` | ⑤「相邻最大间隔」 |
| `_prev_para_block` 里 `lead.en` 空不返回空表（拿 `body` 当上一版） | ①「`lead.en` 空 → 按 None」 |
| `timeliness` 的 `first_para_after_s` 改成 `fp - hms_to_s(t0)`（不减 `base`） | ⑥「距开章 −20 s」 |
| `timeliness` 的 `first_para_after_s` 截成 `max(0, …)` | ⑥「距开章可以是负数，不许截」 |
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import chapter as ch                                                 # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  {detail}" if detail else ""))


# ---------------------------------------------------------------- 夹具
class _Atom:
    """`atom.Atom` 的最小形状（`build_prompt` 只读 `.text` / `.kind`）。"""

    def __init__(self, text: str, kind: str = "定义"):
        self.text, self.kind = text, kind


SENTS = [(12, "One trade raises the price of wine."),
         (13, "The real wage falls in the other country.")]
ATOMS = [_Atom("比较优势")]

#: 上一版段落：形状**就是** `parse_para` 的返回值（lead + body + 各种字段）。
PREV = {"lead": {"en": "One trade makes both countries better off.",
                 "zh": "自由贸易让两国都变好。", "bold": [], "bold_en": [], "src": [12]},
        "body": [{"en": "Wine becomes cheaper than cloth in the wine country.",
                  "zh": "酒国买布比自己做便宜。", "bold": [], "bold_en": [], "src": [13]},
                 {"en": "The real wage rises by 3 percent.",
                  "zh": "实际工资涨 3%。", "bold": [], "bold_en": [], "src": [13]}],
        "words": 31, "terms": [], "flag": None, "para_over": False}


def _plain(**kw) -> str:
    return ch.build_prompt(SENTS, ATOMS, ["Intro"], ["这一章讲稀缺性。"], **kw)


#: ⭐ **冻结的 prompt 原文**（同一组夹具、不传 `prev_para`）。
#: ⚠️ 它钉的不是排版美丑，是**云端前缀缓存**：`ctx_chunk=0` 的布局逐字冻结（同
#:    `tests/test_translator.py` 那条）。改这里 = 让所有真课调用的缓存前缀失效，
#:    而且 `test_chapter_para.py` ⑦ 那几条会惄惄变成测另一样东西 —— 想改先想清理由。
PLAIN_PARTS = [
    "前面几章讲的是（标题 + 各一行结论；**与它们重复的句子必须删** —— 只写本章新增的内容）：",
    "- Intro｜结论：这一章讲稀缺性。",
    "",
    "这一章的英文原句（共 2 句；`全局句号. 句子`）：",
    "12. One trade raises the price of wine.",
    "13. The real wage falls in the other country.",
    "",
    "这一章已经提取出的要点（供你判断重点，**不要照抄**）：",
    "- [定义] 比较优势",
]


def _plain(**kw) -> str:
    return ch.build_prompt(SENTS, ATOMS, ["Intro"], ["这一章讲稀缺性。"], **kw)


# ---------------------------------------------------------------- ① 空 / None
def t_none_identical() -> None:
    print("\n--- ① `prev_para=None` / 不传 / 坏形状 → 逐字相同 ---")
    base = _plain()
    check("⭐⭐ 不传 `prev_para` 的 prompt **与冻结原文逐字相同**",
          base == "\n".join(PLAIN_PARTS), repr(base[-40:]))
    check("⭐⭐ `prev_para=None` 与不传**逐字相同**", _plain(prev_para=None) == base)
    check("⭐ 默认值就是 `None`（老调用点一个字都不用改）",
          ch.build_prompt(SENTS, ATOMS, ["Intro"], ["这一章讲稀缺性。"]) == base)
    for label, kw in [
        ("不是 dict", {"prev_para": "上一版"}),
        ("空 dict", {"prev_para": {}}),
        ("lead 缺", {"prev_para": {"body": [{"en": "x"}]}}),
        ("lead 不是 dict", {"prev_para": {"lead": "One trade."}}),
        ("lead.en 空", {"prev_para": {"lead": {"en": "   "}, "body": [{"en": "x"}]}}),
        ("body 不是 list", {"prev_para": {"lead": {"en": "x"}, "body": "y"}}),
        ("body 里全是坏东西", {"prev_para": {"lead": {"en": ""},
                                         "body": [None, 3, {"zh": "只有中文"}]}}),
        ("整段一个 en 都没有", {"prev_para": {"lead": {"zh": "中文"}, "body": []}}),
    ]:
        # ⚠️ 捕获异常也算红：本函数的职责是「**不崩**」，扫到 `AttributeError` 就是它失职。
        #    不加这层 try，改坏时测试会直接 traceback 退出 —— 那不叫「有一条判据红了」。
        try:
            got = _plain(**kw)
        except Exception as e:                           # noqa: BLE001
            check(f"⭐⭐ 坏形状不崩、按 None 处理：{label}", False, f"抛了 {type(e).__name__}")
            continue
        check(f"⭐⭐ 坏形状不崩、按 None 处理：{label}", got == base and "上一版" not in got)
    for b in ([], [PREV], 0, False):
        try:
            ok = _plain(prev_para=b) == base
        except Exception as e:                           # noqa: BLE001
            check(f"⭐ 非 dict 的 {type(b).__name__} 项不抛", False, f"抛了 {type(e).__name__}")
            continue
        check(f"⭐ 非 dict 的 {type(b).__name__} 项按 None 处理", ok)


# ---------------------------------------------------------------- ② 有值时
def t_prev_content() -> None:
    print("\n--- ② 有上一版：每句 EN 逐字进 prompt + 三条规则在 ---")
    p = _plain(prev_para=PREV)
    ens = [PREV["lead"]["en"]] + [b["en"] for b in PREV["body"]]
    check("⭐⭐ 上一版每句 EN 都**逐字**出现", all(en in p for en in ens),
          f"{sum(en in p for en in ens)}/{len(ens)}")
    check("⭐⭐ 三句都在（不是只给 lead）", len(p.split("上一版")[1]) > 0 and
          p.count("- ") >= 3 + 1, "含前章标题那一行")
    check("⭐ 给的是英文原句，不是中文（中文只在原段落里，不另抄）",
          PREV["lead"]["zh"] not in p)
    check("⭐ 明写「还在进行中」", "还在进行中" in p)
    check("⭐ 规则一：没推翻结论 → `lead` 保持原意", "保持原意" in p)
    check("⭐ 规则二：新论点替换**最不具体**的那句，不是追加", "最不具体" in p and "追加" in p)
    check("⭐ 规则三：没变的句子不许改写", "不许改写" in p or "不许换" in p)
    check("⭐ 带反例（⚠️ 开头那一行）", "⚠️ 反例" in p)
    check("⭐ 仍交代 55–80 词 / 每句至少一个具体项", "55–80" in p and "具体项" in p)
    check("⭐⭐ 旧内容一个字没动（前缀不变 = 缓存前缀仍命中）", p.startswith(_plain()))
    check("`body` 为空但 `lead` 有 → 仍算有上一版",
          "上一版" in ch.build_prompt(SENTS, ATOMS, [], [],
                                   prev_para={"lead": {"en": "Only a lead."}}))
    check("`body` 里混了非 dict → 跳过它，其余照给",
          "Good one." in ch.build_prompt([], [], [], [], prev_para={
              "lead": {"en": "Lead."}, "body": [None, {"en": "Good one."}]}))
    check("⭐ `SYS_CHAPTER` 没被改（不许动系统提示）",
          "这一章还在进行中" not in ch.SYS_CHAPTER and "增量" not in ch.SYS_CHAPTER)


# ---------------------------------------------------------------- ③ 重放量具
def _replay():
    """`scripts/summary_replay.py` 不导入（它会 `import live_summary`）——按路径单独载入。

    ⚠️ 载入后**不调 `main()`**：真重放要花 test key 的钱。这里只用三个纯函数。
    """
    path = HERE / "scripts" / "summary_replay.py"
    spec = importlib.util.spec_from_file_location("summary_replay_probe", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def t_replay() -> None:
    print("\n--- ③ 重放量具：`emit_record` / `timeliness` / `render_timeliness` ---")
    sr = _replay()
    para = {"lead": {"en": "Lead.", "zh": "结论。"}, "body": []}
    payload = {"kind": "chapter", "chapter": {"id": 2, "status": "interim", "version": 3,
                                              "t0": "10:00:00", "t1": "10:04:00",
                                              "para": para}}
    rec = sr.emit_record(payload, 245.0)
    check("⭐ chapter emit → 一行，课内秒是 `sim_s`",
          rec is not None and rec["sim_s"] == 245.0 and rec["id"] == 2, repr(rec))
    check("⭐ `has_para` / `lead_zh` 从 para 里取",
          rec["has_para"] is True and rec["lead_zh"] == "结论。")
    check("⭐ version / t0 / t1 / status 都记了",
          (rec["version"], rec["t0"], rec["t1"], rec["status"])
          == (3, "10:00:00", "10:04:00", "interim"))
    check("⭐⭐ 非 chapter emit 不记（窗口 / 课务不进 emits.jsonl）",
          sr.emit_record({"kind": "window", "w": 1}, 1.0) is None
          and sr.emit_record({"kind": "deadline", "t": "x"}, 1.0) is None)
    check("⭐ 形状不对不抛（量具不是闸门）",
          sr.emit_record({}, 0.0) is None and sr.emit_record({"kind": "chapter"}, 0.0) is None
          and sr.emit_record({"kind": "chapter", "chapter": {}}, 0.0)["has_para"] is False)
    check("⭐ 空章 para=None → `has_para=False`、`lead_zh` 空",
          sr.emit_record({"kind": "chapter", "chapter": {"id": 1, "para": None}}, 0.0)
          == {"sim_s": 0.0, "id": 1, "status": None, "version": None, "t0": None,
              "t1": None, "has_para": False, "lead_zh": ""})

    # ── 时效表：两章，手算过的数
    emits = [
        {"sim_s": 60.0, "id": 1, "t0": "10:00:00", "has_para": False},
        {"sim_s": 135.0, "id": 1, "t0": "10:00:00", "has_para": True},
        {"sim_s": 210.0, "id": 1, "t0": "10:00:00", "has_para": True},
        {"sim_s": 285.0, "id": 1, "t0": "10:00:00", "has_para": True},   # 章 1 最大间隔 75
        {"sim_s": 300.0, "id": 2, "t0": "10:05:00", "has_para": False},
        {"sim_s": 375.0, "id": 2, "t0": "10:05:00", "has_para": True},   # 距开章 75
        {"sim_s": 520.0, "id": 3, "t0": "10:08:00", "has_para": False},  # 章 3 从没出过 para
    ]
    t = sr.timeliness(emits)
    rows = {r["id"]: r for r in t["rows"]}
    check("⭐ `timeliness` 每章一行、按 t0 排", [r["id"] for r in t["rows"]] == [1, 2, 3])
    check("⭐ 更新次数 = 这一章几条 emit",
          (rows[1]["updates"], rows[2]["updates"], rows[3]["updates"]) == (4, 2, 1))
    check("⭐⭐ 首条有 para 的**课内秒** + 距开章秒数",
          (rows[1]["first_para_s"], rows[1]["first_para_after_s"],
           rows[2]["first_para_after_s"]) == (135.0, 135.0, 75.0))
    check("⭐⭐ 相邻最大间隔（章 1 = 75 s）", rows[1]["max_gap_s"] == 75.0)
    check("⭐ 从没出过 para 的章：`None`，不写成 0", rows[3]["first_para_s"] is None
          and rows[3]["first_para_after_s"] is None)
    check("⭐ 只有一条 emit 的章：`max_gap_s` 是 `None`", rows[3]["max_gap_s"] is None)
    check("⭐⭐ 全节汇总：首段距开课 / 所有章最大间隔的最大值 / 调用次数",
          (t["first_para_course_s"], t["max_gap_s"], t["calls"]) == (135.0, 75.0, 7))
    check("⭐ 空输入不抛、汇总全 `None`",
          sr.timeliness([])["rows"] == [] and sr.timeliness([])["max_gap_s"] is None)
    check("⭐ 缺 id 的脏行跳过", len(sr.timeliness([{"sim_s": 1.0}])["rows"]) == 0)

    # ⚠️ 时钟/章节边界对不上时**原样给负数**（截成 0 会把「晚出 20 秒」报成「刚好」）。
    #    `base` = 本课首句墙钟（章 1 的 10:00:00）；章 2 的 t0 比它晚 600 s，
    #    而它第一条 emit 在课内 580 s → 距开章 −20 s。
    late = sr.timeliness([{"sim_s": 10.0, "id": 1, "t0": "10:00:00", "has_para": False},
                          {"sim_s": 580.0, "id": 2, "t0": "10:10:00", "has_para": True}])
    check("⭐⭐ 距开章可以是负数，不许截成 0",
          late["rows"][1]["first_para_after_s"] == -20.0, str(late["rows"][1]))

    md = "\n".join(sr.render_timeliness(t))
    check("⭐ report 里有「时效」一节 + 那四列", "## 时效" in md and "更新次数" in md
          and "相邻最大间隔" in md and "首条有 para" in md)
    check("⭐ 汇总三行都在", "首段距开课" in md and "章节调用次数：7" in md)
    check("⭐ 没出过 para 的章写「—」，不写 `None`", "| 3 |" in md and "None" not in md)



# ---------------------------------------------------------------- ④ prev_hi：告诉模型哪几句是新的
def t_new_range() -> None:
    print("\n--- ④ `prev_hi`：上一版之后新增的句号范围（2026-11-18 Claude 加） ---")
    base = _plain()
    check("⭐⭐ 没有 `prev_para` 时给 `prev_hi` 也**逐字相同**",
          _plain(prev_hi=12) == base)
    p = _plain(prev_para=PREV, prev_hi=12)
    check("⭐⭐ 有上一版 + `prev_hi=12` → 写明新增是第 13–13 句",
          "新增**的原文是第 13–13 句" in p, p[-300:])
    check("⭐ 要求新论点至少一句 `src` 落在新增范围",
          "**至少一句**的 `src` 要落在新增范围里" in p)
    p2 = _plain(prev_para=PREV, prev_hi=13)
    check("⭐ 没有新句（prev_hi = 末句）→ 不写范围那行",
          "新增**的原文是第" not in p2, p2[-200:])
    check("⭐ `prev_hi=None` → 不写范围那行（旧调用方式不变）",
          "新增**的原文是第" not in _plain(prev_para=PREV))


def main() -> int:
    t_none_identical()
    t_new_range()
    t_prev_content()
    t_replay()
    bad = [n for n, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(bad)}/{len(RESULTS)} 通过")
    if bad:
        print("❌ " + " / ".join(bad))
        return 1
    print("✅ 全绿")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
