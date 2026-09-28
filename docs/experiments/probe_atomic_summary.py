#!/usr/bin/env python3
"""探针: 「原子提取 / 常驻主题条」到底有没有用 —— **出数字, 不改产品**。

    ClassLive.app/Contents/MacOS/python docs/experiments/probe_atomic_summary.py --limit 8
    ClassLive.app/Contents/MacOS/python docs/experiments/probe_atomic_summary.py --selftest

## 为什么单独写

`§16.1` 把「先跑离线探针再建第二区」列为第一批第 6 件 —— 因为**这个问题一次都没量过**
（`measure-before-claiming`）。所以这里**只读已有会话 + 只调 LLM + 只写 /tmp**，
一个字节都不落到 vault，也不碰 `sessions/`。

## 四个数（**先出数, 不设阈值**）

| 数 | 怎么来 | 谁算 |
|---|---|---|
| **重入可答率** | 随机停 10 个时刻, 只给主题条 + 最近两行字幕, 问"能说出讲到哪了吗" | ⭐ **人工**（探针只把材料印出来） |
| **可追溯率** | 引用的来源句 id 是否**真在窗口内** | 机械 |
| **稳定度** | 每小时主题条换几次 | 机械 |
| **静默率** | 多大比例的窗口**正确地什么都没输出**（闲聊/课务时也喋喋不休 = 不及格） | 机械 |

⚠️ **不设"≥70% / ≤20%"那种线** —— 那两个数字没有出处。先出数, 阈值等看到真实分布再定。
⚠️ **判据自身先自测**（`--selftest`, 也在每次真跑之前自动跑一遍）: 一个认不出
   "该沉默的窗口"的判定函数, 给出的静默率毫无意义。这条是 `review_prompt_ab.py`
   用一版错判据换来的教训。
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import random
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

WINDOW_MIN = 5.0                     # 一个窗口喂多少分钟的转录
API = "https://api.deepseek.com/v1/chat/completions"
MODEL = "deepseek-chat"

SYS = """你在听一节课的实时转录, 每段给你该段新增的英文定稿(带行号)。
你的任务**不是**写摘要, 而是**记下这段里真正新出现的信息**, 供学生课上快速重入。

输出严格 JSON:
{"topic": "本段主题(中文为主, 术语保留英文, 不超过 12 字)",
 "points": [{"text": "一条要点(中文, 一句话)",
             "kind": "主题|要点|定义|例子|课务|明示强调",
             "src": [3, 4]}]}

硬性要求:
- **没有可记的就返回 `{"topic": "", "points": []}`** —— 闲聊、点名、设备调试、
  重复上段已经说过的东西, 都属于"没有可记的"。**宁可空, 不要凑**。
- 每条 point 的 `src` 必须是给你的行号里**真实存在**的; 不许编。
- `text` 里不许出现行号以外的引用标记; 单行, 不带换行。
- 最多 5 条。只依据给的内容, 不引入外部知识。"""


# ---------------- 判据（先自测再用） ----------------
def traceable(points, lo_line: int, hi_line: int) -> tuple[int, int]:
    """(可追溯条数, 总条数)。`src` 是**行号**, 必须落在 [lo_line, hi_line] 内且非空。

    ⚠️⚠️ **参数是行号, 不是时间跨度。** 第一版我传的是窗口的当日秒数
    (`50836..51101`), 而模型返回的 `src` 是 `[17]` 这种行号 —— 两个域根本不是一个,
    于是可追溯率**按构造恒为 0**, 看起来像"模型在编引用"。判据自测当时也过了
    (合成数据里两边都是小整数, 撞不出这个错), **是拿真实输出对不上才发现的**。
    → 这正是 `measure-before-claiming` 里那条: 判据要拿**真实形状**的输入试一次。
    """
    ok = tot = 0
    for p in points:
        tot += 1
        src = p.get("src") or []
        if isinstance(src, list) and src and all(
                isinstance(x, int) and lo_line <= x <= hi_line for x in src):
            ok += 1
    return ok, tot


def is_silent(points) -> bool:
    return not points


def topic_changes(topics: list[str]) -> int:
    """主题条换了几次（空主题不参与, 它表示"没内容"而不是"换了个空主题"）。"""
    seq = [t for t in topics if t.strip()]
    return sum(1 for a, b in zip(seq, seq[1:]) if a != b)


def selftest() -> bool:
    """⚠️ **判据先自测**: 一个认不出"该沉默"的判定函数, 给出的静默率没有意义。"""
    cases = []

    def chk(name, got, want):
        cases.append((name, got == want, f"{got!r} vs {want!r}"))

    chk("空列表算沉默", is_silent([]), True)
    chk("有要点不算沉默", is_silent([{"text": "x", "src": [1]}]), False)
    chk("src 全在窗口内 -> 可追溯", traceable([{"src": [3, 4]}], 3, 6), (1, 1))
    chk("src 越界 -> 不可追溯", traceable([{"src": [9]}], 3, 6), (0, 1))
    chk("src 为空 -> 不可追溯", traceable([{"src": []}], 3, 6), (0, 1))
    chk("src 缺字段 -> 不可追溯", traceable([{"text": "x"}], 3, 6), (0, 1))
    chk("src 非整数 -> 不可追溯", traceable([{"src": ["3"]}], 3, 6), (0, 1))
    chk("两条一好一坏", traceable([{"src": [3]}, {"src": [99]}], 3, 6), (1, 2))
    chk("主题没换", topic_changes(["A", "A", "A"]), 0)
    chk("主题换一次", topic_changes(["A", "B", "B"]), 1)
    chk("空主题不参与计数", topic_changes(["A", "", "A"]), 0)
    chk("空->A 不算换(从无到有)", topic_changes(["", "A"]), 0)

    bad = [c for c in cases if not c[1]]
    for name, ok, det in cases:
        print(f"   {'✅' if ok else '❌'} {name}   {'' if ok else det}")
    print(f"  判据自测: {len(cases) - len(bad)}/{len(cases)} 通过")
    return not bad


# ---------------- 读会话 ----------------
TS = re.compile(r"^> \[!abstract\] (\d\d):(\d\d):(\d\d)")
EN = re.compile(r"^> \*\*EN\*\*: (.*)$")


def load_sessions(limit_sessions: int | None):
    out = []
    for p in sorted((ROOT / "sessions").glob("*.md")):
        if p.name.endswith("_TEST.md"):
            continue
        rows, cur = [], None
        for ln in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            m = TS.match(ln)
            if m:
                cur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
                continue
            e = EN.match(ln)
            if e and cur is not None:
                rows.append({"sec": cur, "en": e.group(1).strip()})
                cur = None
        if len(rows) >= 40:
            out.append((p.name, rows))
    return out[:limit_sessions] if limit_sessions else out


def windows(rows):
    """按时间切 ~WINDOW_MIN 分钟一个窗口。"""
    out, cur, t0 = [], [], rows[0]["sec"]
    for r in rows:
        if (r["sec"] - t0) >= WINDOW_MIN * 60 and cur:
            out.append(cur)
            cur, t0 = [], r["sec"]
        cur.append(r)
    if cur:
        out.append(cur)
    return out


# ---------------- 调用 ----------------
def call(key: str, block: str) -> dict:
    import httpx
    r = httpx.post(API,
                   headers={"Authorization": f"Bearer {key}",
                            "Content-Type": "application/json"},
                   json={"model": MODEL, "temperature": 0.0,
                         "response_format": {"type": "json_object"},
                         "messages": [{"role": "system", "content": SYS},
                                      {"role": "user", "content": block}]},
                   timeout=90.0)
    r.raise_for_status()
    txt = r.json()["choices"][0]["message"]["content"]
    try:
        obj = json.loads(txt)
    except Exception:                                     # noqa: BLE001
        return {"topic": "", "points": [], "_bad_json": txt[:120]}
    pts = obj.get("points")
    pts = [p for p in pts if isinstance(p, dict)] if isinstance(pts, list) else []
    return {"topic": str(obj.get("topic") or ""), "points": pts}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=8, help="最多跑几个窗口(控成本)")
    ap.add_argument("--sessions", type=int, default=6, help="最多读几节课")
    ap.add_argument("--selftest", action="store_true", help="只跑判据自测")
    ap.add_argument("--seed", type=int, default=20260928)
    a = ap.parse_args()

    print("=" * 74)
    print("判据自测（**先证明量具认得出「该沉默」**）")
    if not selftest():
        print("❌ 判据没通过自测 —— 后面的数字不用看了。")
        return 2
    if a.selftest:
        return 0

    from cloud_translator import load_api_key
    key = load_api_key()
    if not key:
        print("❌ 没有 API key —— 探针跑不了"
              "(它要真调模型, 而「真调得动」正是这条探针要验的一部分)")
        return 2

    sess = load_sessions(a.sessions)
    if not sess:
        print("❌ 没有够长的非测试会话")
        return 2
    wins = []
    for name, rows in sess:
        for w in windows(rows):
            if len(w) >= 8:
                wins.append((name, w))
    print(f"\n会话 {len(sess)} 节 · 可用窗口 {len(wins)} 个"
          f"（每个 ~{WINDOW_MIN:.0f} 分钟, ≥8 句）")
    print(f"本次跑前 {min(a.limit, len(wins))} 个窗口 —— 这是**样本不是全体**。")

    results, topics = [], []
    for i, (name, w) in enumerate(wins[:a.limit], 1):
        lo = min(r["sec"] for r in w)
        block = "\n".join(f"{j}. {r['en']}" for j, r in enumerate(w))
        try:
            got = call(key, block)
        except Exception as e:                            # noqa: BLE001
            print(f"  [{i}] {name} 调用失败: {str(e)[:60]}")
            continue
        got["_window"] = f"{name}#{i}"
        got["_span"] = f"{w[0]['sec']}..{w[-1]['sec']}"
        got["_n_sent"] = len(w)
        got["_lo"], got["_hi"] = 0, len(w) - 1     # ⚠️ 行号域(见 traceable 的注)
        got["_tail"] = [r["en"][:110] for r in w[-2:]]
        results.append(got)
        topics.append(got["topic"])
        tag = "空" if is_silent(got["points"]) else f"{len(got['points'])} 条"
        at_min = w[0]["sec"] // 60
        print(f"  [{i:2}] {name[:28]:28} {at_min:>3}min+ {tag:>6}  "
              f"主题={got['topic'][:18]!r}", flush=True)

    ok = tot = 0
    for r in results:
        a_, b_ = traceable(r["points"], r["_lo"], r["_hi"])
        ok += a_
        tot += b_

    silent = sum(1 for r in results if is_silent(r["points"]))
    print("\n" + "=" * 74)
    print("四个数 —— ⚠️ **不设阈值, 先出数**")
    print(f"  静默率      : {silent}/{len(results)} = "
          f"{silent / max(len(results), 1) * 100:.0f}%  （空窗口占多少）")
    print(f"  可追溯率    : {ok}/{tot} = {ok / max(tot, 1) * 100:.0f}%  "
          f"（引用行号真在窗口内的比例; 机械半边）")
    if tot and ok == 0:
        print("  ⚠️⚠️ 恰好 0 —— **先怀疑判据, 别先怀疑模型**。第一版就是这么错的:"
              "把模型的**行号**拿去跟窗口的**当日秒数**比, 按构造恒为 0。")
        print("      查一下 /tmp/summary_eval.json 里的 `src` 是不是小整数行号, "
              "以及这边传的上下界是不是同一个域。")
    if tot and ok == tot:
        print("  ⚠️ 恰好 100% —— 这只说明**行号没越界**(格式合规), "
              "**不代表引用语义上真支撑那条要点**。语义忠实度要人工看明细, "
              "别把这一格读成\"引用都是对的\"(文献里 RAG 的最高 57% 引用不忠实)。")
    print(f"  稳定度      : 主题条共换 {topic_changes(topics)} 次 / "
          f"{len(results)} 个窗口（窗口 {WINDOW_MIN:.0f} 分钟 → 折合每小时 "
          f"{topic_changes(topics) / max(len(results) * WINDOW_MIN / 60, 1e-9):.1f} 次）")
    print(f"  重入可答率  : **人工** —— 见下面的盲评表")

    # 人工盲评表: 随机停 10 个时刻, 只给主题条 + 最近两行
    rnd = random.Random(a.seed)
    stops = rnd.sample(range(len(results)), min(10, len(results)))
    sheet = ["# 重入测试（只给这些, 问：我能说出讲到哪了吗？）", ""]
    for k, si in enumerate(stops, 1):
        r = results[si]
        sheet.append(f"## 停靠点 {k}  `{r['_window']}`")
        sheet.append(f"- 主题条: **{r['topic'] or '(空)'}**")
        sheet.append(f"- 要点: {len(r['points'])} 条")
        sheet.append("- 最近两行字幕:")
        for ln in (r.get("_tail") or ["(见 /tmp/summary_eval.json)"]):
            sheet.append(f"    - {ln}")
        sheet.append("- 我能说出讲到哪了吗？  [ ] 能  [ ] 勉强  [ ] 不能")
        sheet.append("")
    dst = pathlib.Path("/tmp/summary_eval.json")
    dst.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    sheet_path = pathlib.Path("/tmp/summary_reentry_sheet.md")
    sheet_path.write_text("\n".join(sheet), encoding="utf-8")
    print(f"\n  明细: {dst}")
    print(f"  盲评表: {sheet_path}  ← **看着它填, 那个数才是重入可答率**")
    print("\n  ⚠️ 样本小、只跑了前几个窗口, **别当结论**; 也别拿它去设阈值。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
