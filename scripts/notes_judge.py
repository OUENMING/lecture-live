#!/usr/bin/env python3
"""F14 ⑩：**终读判官**（测量用，不进产线）。

拿 gold 集当考题，问一个"只有五件事要找"的判官：
  ① 答案与题面不匹配 ② 跨节矛盾 ③ 悬空指路 ④ 来源声称不符 ⑤ 分组/方向搞反

- must-flag 集 = 作者逐字读出来的读者可见问题（A2–A7 + ⑧⑨）；
- must-pass 集 = 四讲 **39 道例题/变式/补全**（我已逐条算对）+ 4 段干净正文。

报 **召回率**（must-flag 里判官报出问题的比例）与 **误报率**（must-pass 里被报问题的比例），
重复 `--repeat` 次（默认 3）看方差。

⚠️ 跨家族对照：本机 Worker（`asr.bldcam.page`）**只认 `deepseek-chat`**，别的模型名一律
   400；没有 OpenRouter key → **只能跑 DeepSeek**（脚本会把这件事写进报告）。

用法（需要 test1 通行码的 HOME，例如某次运行的 `var/`）：
    HOME=runs/final_v2/econ10790/var python3 scripts/notes_judge.py --repeat 3
"""
from __future__ import annotations

import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FIX = ROOT / "tests" / "fixtures" / "notes_gold"

SYS = """你在替一位大学生**终读**这份课堂笔记。只找下面五类问题，别的都不要提：

1. `answer_mismatch` —— 例题的**答案与题面/步骤对不上**（含单位、量纲）。
2. `cross_section` —— **两节之间**自相矛盾（同一件事给了两个值/两个方向）。
3. `dangling_ref` —— 指路找不到（「第 N 节」指的节里没有这事、`上一节`对不上）。
4. `source_claim` —— **来源声称不符**（说"课件/讲义里写着"，但这讲没有课件；或引用在材料里查不到）。
5. `group_swap` —— 分组/方向搞反（男女、前后、正负、谁大谁小写反）。

规则：
· 只判给到的文本，别猜笔记之外的内容；看不出问题就**不要报**（宁可漏，不许凑）。
· 每条只给一个 JSON：`{"verdicts": {"<id>": {"issues": [{"class": "...", "why": "≤20字"}]}}}`；
  没有问题写成 `{"issues": []}`。`class` 只能是上面五个之一。"""

#: must-flag：作者逐字读出来的 12 处（文本都是 v2 真实产物里的原句）。
FLAG_ITEMS = [
    ("术语表-跨课定义",
     "ECC170 本课术语（节选）：\n"
     "- derivative — 导数 · 本课\n- sigmoid — S 形函数 · 本课\n"
     "- structure — 社会结构，影响行为的规则和制度 · 术语库\n"
     "- variable — 会变化的量，比如收入、年龄 · 术语库\n"
     "- dataset — 一堆收集好的数据，像表格一样 · 术语库\n"
     "- function — 函数，输入对应唯一输出 · 术语库"),
    ("没有课件却引用课件", "页头：`📖 无课件材料`（本讲没有课件）。\n"
     "正文：课件末页公式：$\\sigma(z)=1/(1+e^{-z})$。均 S 形、有饱和区。\n"
     "核对台账：阶跃定义与课件一致。"),
    ("悬空指路-ReLU", "§24 Leaky ReLU（🔗）：ReLU 的负区零梯度问题在第三节。"),
    ("悬空指路-sigmoid", "§25 Sigmoid 与 tanh 的公式（🔗）：sigmoid 的饱和问题在第一节已出现。"),
    ("ASR-常数9", "§20 常见错误：误以为 ReLU 负区间输出是 1 或常数 9；实际是 0。"),
    ("ASR-S1", "§63 正文：这段推导课上是放选修的：考试不考，项目里也不要求，课程本身不要求 S1。"),
    ("电报体-演进史", "§23 激活函数的演进历史：阶跃0/1无梯度→sigmoid/tanh饱和→ReLU修负区与不光滑；先试ReLU。"),
    ("电报体-前向后向", "§29 前向传播与反向传播：前向算输出；反向链式传梯度，sigmoid导数复用"),
    ("阶跃定义两节不一致",
     "§14 二值阶跃函数及其缺陷：$f(z)=1\\ (z>0)$，$f(z)=0\\ (z\\le0)$；"
     "常见错误：以为阶跃函数在 z=0 处输出 1（课上定的是 0）。\n"
     "§15 二值阶跃函数的梯度问题：输入 z 小于 0 输出 0，大于等于 0 输出 1；"
     "z=0 处从 0 直接跳到 1。"),
    ("男女斜率两节不一致",
     "§15 交互项：✏️ 自补例：β₂=0.5、β₃=0.2，女性每多一年经验收入多 0.5，男性多 0.7。\n"
     "§18 交互项（例题，标「🎙 按口述还原」）：给的例子是 β₁=0.3、β₃=0.4，男性每年 0.3，女性每年 0.7。"),
    ("课上说法前后不一致",
     "笔记 §18 写：「课上给出 β₁=0.3、β₃=0.4，男性每年 0.3，女性每年 0.7。」\n"
     "转录：09:33:35 “And if you're male, every extra year of experience adds 0.3 plus 0.4.”\n"
     "转录：09:34:15 “For each additional year of experience, the slope is beta three plus beta four.”"),
    ("矛盾标签冒充数字错",
     "§25 正文（本讲没有课件）：课件末页公式：$\\sigma(z)=1/(1+e^{-z})$，"
     "$\\tanh(z)=(e^z-e^{-z})/(e^z+e^{-z})$。（⚠ 核算对不上：转录里没找到这句(核对)）"),
]

PASS_EXTRA = [
    ("干净-对的相对指路", "§29 前向传播（🔗）：上一节的 $f(1-f)$ 就在这里用上。"),
    ("干净-同口径定义", "§14：$z>0$ 输出 1，$z\\le0$ 输出 0。§15：$z>0$ 输出 1，$z\\le 0$ 输出 0。"),
    ("干净-本课术语行", "本课术语：- binary step function — 二值阶跃函数 · 本课　`16:57:26`"),
    ("干净-带课件的引用", "页头：`📖 课件 37 份 / 索引 595 段`。正文：课件末页公式：$\\sigma(z)=1/(1+e^{-z})$。"),
]


def _items() -> tuple:
    """(must_flag 项, must_pass 项)。"""
    flag = [{"id": f"F{i:02d}", "text": t} for i, (_n, t) in enumerate(FLAG_ITEMS, 1)]
    ex = json.loads((FIX / "examples.json").read_text(encoding="utf-8"))
    pas = []
    for i, e in enumerate(ex, 1):
        txt = (f"【{e['course']} {e['section']}】\n题面：{e['question']}\n"
               + ("步骤：" + " / ".join(e["steps"]) + "\n" if e["steps"] else "")
               + f"答案：{e['answer']}")
        pas.append({"id": f"P{i:02d}", "text": txt})
    for i, (_n, t) in enumerate(PASS_EXTRA, 1):
        pas.append({"id": f"X{i:02d}", "text": t})
    return flag, pas


def _cost_usd(chars_in: int, chars_out: int) -> float:
    """**上界**估算：CJK 按 ≈1 token/字、输入全按未命中价（$0.15/M）、输出 $0.60/M。"""
    tin = chars_in / 2.0            # 中英混排 → 取个保守的上界
    tout = chars_out / 2.0
    return tin / 1e6 * 0.15 + tout / 1e6 * 0.60


def main(argv: list) -> int:
    repeat = 3
    if "--repeat" in argv:
        repeat = int(argv[argv.index("--repeat") + 1])
    cap = 0.50
    if "--max-usd" in argv:
        cap = float(argv[argv.index("--max-usd") + 1])
    import llm_client as lc
    from cloud_translator import load_test_key
    key = load_test_key()
    flag, pas = _items()
    items = flag + pas
    body = "\n\n".join(f"### {it['id']}\n{it['text']}" for it in items)
    user = ("下面是这份笔记里的若干片段，逐条判：\n\n" + body)
    print(f"判官：deepseek-chat（Worker 只认这一个模型名；无跨家族可用）· "
          f"flag {len(flag)} 项 / pass {len(pas)} 项 · 重复 {repeat} 次")
    spent = 0.0
    rows: list = []
    for r in range(1, repeat + 1):
        obj = lc.chat_json(key, "deepseek-chat", SYS, user, max_tokens=4096,
                           temperature=0.0)
        ver = (obj or {}).get("verdicts") or {}
        c = _cost_usd(len(SYS) + len(user), len(json.dumps(ver, ensure_ascii=False)))
        spent += c
        f_hit = sum(1 for it in flag if (ver.get(it["id"]) or {}).get("issues"))
        p_hit = sum(1 for it in pas if (ver.get(it["id"]) or {}).get("issues"))
        rows.append({"run": r, "recall": f_hit / len(flag), "fp": p_hit / len(pas),
                     "miss": [it["id"] for it in flag
                              if not (ver.get(it["id"]) or {}).get("issues")],
                     "fp_ids": [it["id"] for it in pas
                                if (ver.get(it["id"]) or {}).get("issues")],
                     "cost": c})
        print(f"  第 {r} 次：召回 {f_hit}/{len(flag)}={f_hit/len(flag):.0%} · "
              f"误报 {p_hit}/{len(pas)}={p_hit/len(pas):.0%} · 估算 ${c:.4f}")
        if spent > cap:
            print(f"⚠️ 估算花费 ${spent:.3f} 超过上限 ${cap}，停止")
            break
    if rows:
        rc = [x["recall"] for x in rows]
        fp = [x["fp"] for x in rows]
        print(f"\n召回：{min(rc):.0%}–{max(rc):.0%}（均 {sum(rc)/len(rc):.0%}）· "
              f"误报：{min(fp):.0%}–{max(fp):.0%}（均 {sum(fp)/len(fp):.0%}）· "
              f"估算总花费 ${spent:.4f}")
        for x in rows:
            if x["miss"]:
                print(f"  第 {x['run']} 次漏掉：{x['miss']}")
            if x["fp_ids"]:
                print(f"  第 {x['run']} 次误报：{x['fp_ids']}")
    out = ROOT / "tests" / "fixtures" / "notes_gold" / "judge_last.json"
    out.write_text(json.dumps({"model": "deepseek-chat", "runs": rows,
                               "note": "Worker 只认 deepseek-chat；无跨家族对照可用"},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print("→", out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
