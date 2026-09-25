#!/usr/bin/env python3
"""探针 A：**只换 prompt 任务框架**，看复述率降不降。

    .venv/bin/python docs/experiments/review_prompt_ab.py sessions/<课>.md <课号> [--chunks N]

## 为什么单独写、不走 rebuild_note.py

`rebuild_note.py` 会**真的覆盖 Obsidian 笔记**。探针要能反复跑、能 A/B，
所以这里**只读会话文件 + 只调 LLM + 只把结果写到 /tmp**，一个字节都不落到 vault。

## 探什么

病根（`obsidian_writer.py:61` 的 `REVIEW_SYS`）：

    你只依据转录内容输出, 绝不引入外部知识、绝不猜测。
    - 只写转录里确实讲过的内容; 拿不准就不写。

于是每条 point 变成「教授说过的话 → 换个说法 → 再翻成中文」，三段同样内容。
**本探针只改任务框架那一句，其余（结构、字段、分块、模型、温度）全部不动。**

## 判据（先定，再跑）

    复述率 = 含 "the lecturer"/"lecturer states"/"professor" 等模式的 point 数 ÷ 总 point 数

⚠️ 判据自身要先自测：脚本末尾会对两条**手写样本**跑一遍判定函数，
   确认它认得清「复述型」与「整理型」。认不清就别往下看数字。
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve()
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))

import obsidian_writer as ow                                    # noqa: E402

CHUNK = 70

# ---- 两个版本只差**任务框架那一句**，其余逐字相同 ----------------------------
FRAME_OLD = "你是课堂笔记助手, 为一名靠中文听英文课的中国经济学/社会学本科生整理复习层。"

FRAME_NEW = (
    "你是课堂笔记助手, 为一名靠中文听英文课的中国经济学/社会学本科生整理复习层。\n"
    "**你像一个要备考的优秀学生在整理课堂笔记** —— 不是记录这场讲座发生了什么, "
    "而是把这门课的知识整理成一份能直接拿去复习的材料。同一个概念在前面提过、"
    "后面又展开讲的, 合并成一条; 与课程无关的寒暄、点名、通知不要写。")


def variant_sys(which: str) -> str:
    """旧/新两版 system prompt。**只替换框架那句**，其余原样。"""
    if which == "old":
        return ow.REVIEW_SYS
    if FRAME_OLD not in ow.REVIEW_SYS:
        sys.exit(f"❌ 找不到要替换的框架句 —— REVIEW_SYS 变了，探针失效。\n"
                 f"   现在开头是: {ow.REVIEW_SYS[:80]!r}")
    return ow.REVIEW_SYS.replace(FRAME_OLD, FRAME_NEW, 1)


# ---- 判据（探针的"量具"）-----------------------------------------------------
#
# ⚠️ 第一版判据（找 "the lecturer states" 这种句式）**测错了东西**：
# 实测两版复述率都是 0%，而真实病征不是那个句式 —— 是**直接抄转录**。
# 换成两个真正该测的指标：
#
#   ① 离题率 —— 这条 point 是不是与课程无关的琐事（图书馆楼层/开放时间/通知/寒暄）
#   ② 有细节率 —— detail 字段有没有内容（prompt 要求 2-5 句展开）

# 离题词表。⚠️ 只能当**粗筛**，必须人工复核明细（见 /tmp/review_ab.json）。
OFF_TOPIC_PAT = re.compile(
    r"(library|sensory space|seating|level three|level 3|zoom room|"
    r"opening hours|24/7|ucd connect|wifi|password|"
    r"图书馆|座位|楼层|开放时间|翻新|噪音|密码|登录)", re.I)


def is_off_topic(text: str) -> bool:
    return bool(OFF_TOPIC_PAT.search(text or ""))


def has_detail(p: dict) -> bool:
    """统计与自检**共用**这一个判定 —— 否则自检验的是另一个表达式，等于没验。

    ⚠️ 第一版把 `bool(det_yes.strip())` 直接写在自检里：对非空字面量**恒为 True**，
    而且和真正统计用的 `(p.get("detail") or "").strip()` 不是一个表达式。
    2026-09-25 全量 ocr review 发现。
    """
    return bool((p.get("detail") or "").strip())


def self_test() -> None:
    """⚠️ 量具自检：样本必须被分开。分不开 -> 后面的数字全废。"""
    off = ("The library provides sensory spaces for students who need regulation, "
           "and new seating areas are available on level three.")
    on = ("Taxing landlords reduces housing supply: landlords exit the market, "
          "so fewer apartments are available to rent.")
    got = (is_off_topic(off), is_off_topic(on),
           has_detail({"detail": "These spaces are designed based on student surveys."}),
           has_detail({"detail": ""}),
           has_detail({}))                    # 缺键也要算"无 detail"
    print(f"  量具自检: 离题样本 -> {got[0]} (期望 True)")
    print(f"            正题样本 -> {got[1]} (期望 False)")
    print(f"            有 detail -> {got[2]} / 空 detail -> {got[3]} / 缺键 -> {got[4]}")
    if got != (True, False, True, False, False):
        sys.exit("❌ 量具自检失败 —— 判定函数分不清，后面的统计一个都别信。")


# ---- LLM 调用（照抄 _call_review，只换 system）--------------------------------
def call(sys_prompt: str, model: str, key: str, transcript: str) -> dict:
    import httpx
    try:
        payload = {"model": model, "stream": False,
                   "max_tokens": ow.REVIEW_MAX_TOKENS,
                   "temperature": 0.3, "thinking": {"type": "disabled"},
                   "response_format": {"type": "json_object"},
                   "messages": [{"role": "system", "content": sys_prompt},
                                {"role": "user", "content": transcript}]}
        r = httpx.post("https://api.deepseek.com/v1/chat/completions",
                       headers={"Authorization": f"Bearer {key}"},
                       json=payload, timeout=180)
        r.raise_for_status()
        return ow._clean_review(json.loads(r.json()["choices"][0]["message"]["content"]))
    except Exception as e:                                    # noqa: BLE001
        print(f"    ⚠️ 调用失败: {type(e).__name__}: {e}")
        return {}


def points_of(r: dict) -> list[dict]:
    return [p for s in (r.get("sections") or []) for p in (s.get("points") or [])]


def main() -> int:
    ap_args = [a for a in sys.argv[1:] if not a.startswith("--")]
    n_chunks = CHUNK
    # 同时支持 `--chunks 30` 与 `--chunks=30` —— docstring 写的是前者，
    # 只认 `=` 会让按文档敲的人静默拿到默认值（2026-09-25 ocr review 发现）。
    for i, a in enumerate(sys.argv[1:]):
        if a.startswith("--chunks"):
            n_chunks = int(a.split("=", 1)[1]) if "=" in a else int(sys.argv[i + 2])
    if len(ap_args) < 2:
        sys.exit(__doc__)
    session = pathlib.Path(ap_args[0])   # ap_args[1]（课号）仅用于校验必须显式给出

    print("=" * 74)
    print("探针 A：只换 prompt 任务框架（其余一切不动）")
    print("=" * 74)
    self_test()

    # ⚠️ 直接 import，**不要** `hasattr` 兜底 —— 这个探针的前提就是复用
    # `obsidian_writer` 的内部符号。某个符号不存在时应当**响亮报错**，
    # 而不是悄悄换成一个别的模型（那正好是本文件注释一直在骂的失败模式）。
    from cloud_translator import load_api_key
    key = load_api_key()
    if not key:
        sys.exit("❌ 没有 API key（.deepseek_key / DEEPSEEK_API_KEY）—— 探针跑不了")

    # 模型名：`obsidian_writer` 里**没有模块级常量**，它是
    # `ObsidianWriter.__init__(..., model="deepseek-flash")` 的默认参数。
    # 所以这里写死字符串并注明出处 —— 比假装有个常量好。
    model = "deepseek-flash"          # obsidian_writer.py 的 __init__ 默认值

    text = session.read_text(encoding="utf-8")
    entries = ow.ObsidianWriter._parse(text)
    lines = [f"[{e['ts']}] {e['en'] or e['asr']}" for e in entries if (e["en"] or e["asr"])]
    print(f"\n  会话: {session.name}  {len(lines)} 句")

    # 取**同一批**句子做 A/B（不同 chunk 会引入噪声）
    chunks = [lines[i:i + n_chunks] for i in range(0, len(lines), n_chunks)][:2]
    print(f"  用前 {len(chunks)} 块 × {n_chunks} 句 = {sum(len(c) for c in chunks)} 句\n")

    out = {}
    for which in ("old", "new"):
        sp = variant_sys(which)
        allp: list[dict] = []
        for ci, ch in enumerate(chunks):
            r = call(sp, model, key, "\n".join(ch))
            allp.extend(points_of(r))
            print(f"    [{which}] 块 {ci + 1}/{len(chunks)}: {len(points_of(r))} 条 point", flush=True)
        out[which] = allp

    print("\n" + "=" * 74)
    print(f"{'版本':<8}{'point':>7}{'离题':>7}{'离题率':>9}{'有detail':>10}")
    print("-" * 74)
    for which in ("old", "new"):
        ps = out[which]
        n = max(len(ps), 1)
        off = sum(1 for p in ps if is_off_topic(p.get("en", "")))
        det = sum(1 for p in ps if has_detail(p))
        print(f"{which:<8}{len(ps):>7}{off:>7}{off / n * 100:>8.0f}%{det:>10}")

    dst = pathlib.Path("/tmp/review_ab.json")
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n  明细（**必须人工复核** —— 词表只是粗筛）: {dst}")
    print("  ⚠️ 两组句子相同、只有 prompt 差一句；但**样本小，别当结论**。")
    print("  ⚠️ 第一版判据（找 \"the lecturer states\" 句式）测错了东西：两版都是 0%，")
    print("     而真实病征是**直接抄转录**（图书馆楼层/开放时间原样进了知识点）。已换判据。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
