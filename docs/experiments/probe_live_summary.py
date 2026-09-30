#!/usr/bin/env python3
"""离线回放探针 —— 拿真课的历史转录，让 `LiveSummarizer` 以为自己在上一节课。

    ClassLive.app/Contents/MacOS/python docs/experiments/probe_live_summary.py \
        --session sessions/2026-09-11_140757_10730.md --out /tmp/classlive-probe

## 它回答什么

判据（`tests/test_live_summary.py`）能说「代码对不对」，**说不了「读起来有没有用」**。
这个探针把一节真课离线重放，产出一份**按纲要界面的样子渲染的 `outline.md`**，
外加一堆**从来没人量过**的数（见 `metrics.json`）—— 由**人**来评审。

⚠️ **它跑的是生产代码本身**（`live_summary.LiveSummarizer`），不是另写一份相似逻辑。
   所以它测出来的东西就是产品会做的东西。

## 五个产物（都写在 `--out` 里，**默认 `/tmp`，不进仓库**）

| 文件 | 内容 |
|---|---|
| `<名>.atoms.jsonl` | 原子，格式与生产**一致**（同一个 `AtomWriter` 写） |
| `<名>.chapters.jsonl` | 窗口 / 章节 / 课务记录（同一个 `ChapterWriter` 写） |
| `<名>.outline.md` | ⭐ 按纲要界面的样子渲染 |
| `<名>.metrics.json` | 指标 |
| `<名>.review.md` | 随机抽 30 条，留一列给**人**填「是否支撑」 |

⚠️ **输出目录不要放进仓库** —— 转录里有其他同学的声音。

## 模拟时间（这是它最要紧的一处）

以**第一句**为 0，按**每句自己的时间戳**推进 `now`；相邻两句之间**每隔 1 秒调一次
`step(now)`**。⚠️ 必须这样，否则「30 秒停顿就提交窗口」那条规则（`ATOM_PAUSE_S`）
在回放里**永远不会按真实节奏触发** —— 量出来的窗口数会是假的。

## `--dry` 免费，而且有用

`--dry` 用固定返回值的桩，**不联网、不花钱**。因为窗口规则是确定性的，
所以 `--dry` 跑出来的**调用次数就是真跑的次数** —— **拿它报价再决定要不要花钱**。

## ⚠️ 还没做的（阶段 2 的后续）

- **覆盖率**（Jev 的重点句有多少落进纲要）：要先给 `keypoints.sentences()` 加
  `with_index=True`（见计划 §7.4），还没做 —— 现在这一版**不出覆盖率**。
- 编号映射自检同上。**在它做出来之前，别把覆盖率当结论。**
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import random
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import atom                                                          # noqa: E402
import chapter as ch                                                 # noqa: E402
import live_summary as L                                             # noqa: E402
import obsidian_writer as OW                                         # noqa: E402


# ---------------------------------------------------------------- 读输入
def _secs(ts: str) -> int:
    h, m, s = (int(x) for x in ts.split(":"))
    return h * 3600 + m * 60 + s


def read_session(path: pathlib.Path) -> list:
    """会话 `.md` → `[(全局句号, "HH:MM:SS", 英文, 中文), …]`。

    ⚠️ **用 `obsidian_writer._parse`，不新写解析器** —— 会话 `.md` 是三方共享契约，
       多一个解析器就多一个会漂的地方。
    ⚠️ 英文取值规则**和 `drain()` 一样**（`main.py:1587` 的 `item[1] or item[3]`）：
       有矫正版用矫正版，没有用原始 ASR。
    ⚠️ **全局句号 = 条目下标 + 1**（`writer.append` 每句 `_n += 1`，`_parse` 按顺序还原）。
    """
    entries = OW.ObsidianWriter._parse(path.read_text(encoding="utf-8"))
    out = []
    for i, e in enumerate(entries):
        en = (e.get("en") or "").strip()
        asr = (e.get("asr") or "").strip()
        text = en or asr
        if not text:
            continue
        out.append((i + 1, e.get("ts") or "00:00:00", text, (e.get("zh") or "").strip()))
    return out


# ---------------------------------------------------------------- 假模型
def dry_chat(counter: dict, now_fn):
    """`--dry` 的桩：**不联网**，但让状态机走**与真跑同一条路**。

    ⚠️ 第一版这里返回「空主题」，结果**合成次数少算得离谱**：空主题只会并进当前章，
       一节 50 分钟的课只会做 **1 次**正式合成；而真课上主题会换十来次。
    → 改成**按模拟时间每 10 分钟换一个主题**（一个可信的假设），
      这样原子次数是**精确的**（窗口规则确定性），合成次数是**估算**（明写在输出里）。

    ⚠️ 它**不产生内容**（`text` 为空）→ `--dry` 的 `outline.md` 是空的，**别拿它评审**。
    """
    def chat(sysp, block, max_tokens, temperature):
        counter["in_chars"] += len(block or "")
        if sysp is atom.SYS:
            counter["atom"] += 1
            k = int(now_fn() // 600)                 # 每 10 分钟换一题
            return {"topic": f"dry topic {k}", "topic_zh": "",
                    "points": [{"text": "", "kind": "要点", "src": [0], "terms": []}]}
        counter["chap"] += 1
        return {"title": "", "title_zh": "", "sentences": []}
    return chat


def real_chat(key: str, counter: dict, fail_window):
    """真调 `build_notes._chat_json`（**要花钱**）。

    `fail_window` = `(start_sec, end_sec)` 或 `None`。判断依据是
    **调用发生那一刻的模拟时钟**（不是窗口里句子的时间戳）—— 见计划 D10。
    """
    from build_notes import _chat_json

    def chat(sysp, block, max_tokens, temperature):
        at = fail_window and fail_window[0] <= fail_window[2]() <= fail_window[1]
        if at:
            counter["failed"] += 1
            raise RuntimeError("probe: --fail-window 里，这次调用按设计失败")
        counter["in_chars"] += len(block or "")
        if sysp is atom.SYS:
            counter["atom"] += 1
        else:
            counter["chap"] += 1
        return _chat_json(key, atom.MODEL, sysp, block, max_tokens, temperature)
    return chat


def sim_stamp(t0: int, holder: dict):
    """探针用的时间戳 —— **模拟时钟（讲座时间轴）**，不是真时钟。

    ⚠️⚠️ **不注入它，纲要里的时间戳就是错的**（2026-09-30 实测）：
       `atom.now_stamp()` 用 `time.strftime("%H:%M:%S")` = **跑探针那一刻的真时间**，
       于是那份纲要的课务写成 `02:08:57`（半夜），而讲座是 `14:08`。
       ⚠️ **生产里两者恰好一致**（跑的时候就是上课的时候），所以这个缺陷
          **只有探针会露馅** —— 也正是探针存在的意义之一。

    ⚠️ 而且它**同时修了 `src` 之外的另一半**：`epoch` 也走模拟时钟，
       否则将来按 epoch 排序/对账会和 `t` 打架。
    """
    def stamp():
        secs = int(t0 + holder["now"]) % 86400
        return (f"{secs // 3600:02d}:{(secs // 60) % 60:02d}:{secs % 60:02d}",
                float(t0 + holder["now"]))
    return stamp


# ---------------------------------------------------------------- 纲要渲染
def render_outline(name: str, entries: list, res: dict) -> str:
    """按**纲要界面的样子**渲染（计划 §9.3 的规格）。

    ⚠️ 阶段 4 会把这个渲染逻辑抽成 `overlay.build_outline_rows()` 那份**模块级纯函数**
       （不起窗口也能单测）。今天它是第一版 —— **规格照 §9.3 写**，别自己发挥，
       否则将来两边会长得不一样。
    """
    L_ = [f"# 课堂纲要 · {name}", ""]
    ins = res
    ins.setdefault("gaps", [])
    ins.setdefault("atoms", [])

    if ins["deadlines"]:
        L_ += ["## 📌 课务", ""]
        for d in ins["deadlines"]:
            tag = " · 已改期" if d.get("changed") else ""
            L_ += [f"- **{d['t']}** {d.get('quote', '')}  "
                   f"*({d.get('source', '')} · 待确认{tag})*"]
        L_.append("")

    if res["gaps"]:
        L_ += ["## ⏸ 离线空档", ""]
        for g in res["gaps"]:
            L_ += [f"- {g['t_from']} – {g['t_to']}（{g.get('state', '')}）"]
        L_.append("")

    for c in res["chapters"]:
        mark = " · 临时" if c.get("status") == "interim" else ""
        L_ += [f"## ▍{c.get('title') or '(无标题)'}"
               f"  <sub>{c.get('t0','')}–{c.get('t1','')}{mark}</sub>", ""]
        if c.get("title_zh"):
            L_ += [f"*{c['title_zh']}*", ""]
        sents = c.get("sentences") or []
        if sents:
            for s in sents:
                flag = {"board": " ⚠ 依赖板书或图，转录不完整",
                        "discussion": " · 课堂讨论"}.get(s.get("flag"), "")
                terms = s.get("terms") or []
                tail = ("  " + " · ".join(f"{a} {b}" for a, b in terms)) if terms else ""
                L_ += [f"- {s.get('en', '')}"
                       f"{tail}"
                       f"  <sub>src {s.get('src')}{flag}</sub>"]
            L_.append("")
        else:
            pts = [a for a in res["atoms"] if c["lo"] <= (a.get("src") or [0])[0] <= c["hi"]]
            if pts:
                L_ += ["*（这一章没有合成句，退回它的原子要点）*", ""]
                for a in pts:
                    L_ += [f"- • {a.get('text', '')}  <sub>[{a.get('kind')}]</sub>"]
                L_.append("")

    if res.get("current"):
        cur = res["current"]
        mark = cur.get("title") or "当前主题"
        L_ += [f"## ▍{mark} · 进行中", ""]
        for a in cur.get("atoms", []):
            T = ("  " + " · ".join(f"{x} {y}" for x, y in (a.get("terms") or []))
                 ) if a.get("terms") else ""
            L_ += [f"- • {a.get('text','')}{T}  <sub>[{a.get('kind')}]</sub>"]
        L_.append("")

    L_ += ["---", "", f"*全文 {len(entries)} 句 · 本节共 {len(res['chapters'])} 章*", ""]
    return "\n".join(L_)


# ---------------------------------------------------------------- 主流程
def run_one(session: pathlib.Path, outdir: pathlib.Path, args) -> dict:
    name = session.stem
    entries = read_session(session)
    if not entries:
        print(f"⚠ {session.name}: 读不出句子，跳过")
        return {}

    t0 = _secs(entries[0][1])
    wall0 = t0

    counter = {"atom": 0, "chap": 0, "in_chars": 0, "failed": 0}
    holder = {"now": 0.0}
    fw = None
    if args.fail_window:
        a, b = args.fail_window.split("-")
        fw = (_secs(a), _secs(b), lambda: wall0 + holder["now"])

    if args.dry:
        chat = dry_chat(counter, lambda: holder["now"])
    else:
        sys.path.insert(0, str(ROOT))
        from cloud_translator import load_api_key
        key = load_api_key()
        if not key:
            print("⚠ 没有 DeepSeek key —— 用 --dry 可以只验流程")
            return {}
        chat = real_chat(key, counter, fw)

    # ⚠️ **一个会话一个写入器**（同生产：`obsidian_writer` 懒建、全程复用）。
    #    路径推导**交给 `AtomWriter` 自己**（`<同名>.atoms.jsonl` 在那儿是唯一定义点），
    #    所以传的是**会话那形状的路径**，不是 .jsonl 路径。
    sess_base = outdir / f"{name}.md"
    atom_w = atom.AtomWriter(str(sess_base))
    insights = {"deadlines": [], "chapters": {}, "atoms": [],
                "windows": [], "gaps": [], "marks": []}

    def emit(p: dict) -> None:
        k = p.get("kind")
        if k == "atoms":
            insights["atoms"].extend(p.get("items") or [])
        elif k == "chapter":
            c = p.get("chapter") or {}
            insights["chapters"][c.get("id")] = c
        elif k == "window":
            insights["windows"].append(p)
        elif k == "gap":
            insights["gaps"].append(p)
        elif k == "deadline":
            insights["deadlines"].append(p)

    summ = L.LiveSummarizer(
        chat=chat, append_atoms=atom_w.append,
        chapter_path=ch.chapter_path_for(sess_base),   # → `<同名>.chapters.jsonl`
        emit=emit, clock=lambda: holder["now"],
        stamp=sim_stamp(t0, holder),                  # ⚠️ 不用真时钟（见 sim_stamp）
        chapters_enabled=not args.no_chapters,
        # ⭐ 模型报的 `课务` 过 Jev 打分闸门（没配 token -> None -> 一条不报）
        deadline_gate=None if args.no_jev else L.jev_deadline_gate())

    # ---- 驱动 ----
    t_wall = time.time()
    prev = 0.0
    for gid, ts, text, zh in entries:
        target = (_secs(ts) - t0) % 86400          # ⚠️ 跨零点要取模，否则会出现负数
        while target < prev:                       # 跨零点后再加一天的偏移
            target += 86400
        prev = target
        while holder["now"] < target:
            holder["now"] = min(target, holder["now"] + 1.0)
            summ.step(holder["now"])
        summ.feed((gid, ts, text, zh))
        summ.step(holder["now"])
    # 收尾：残余窗口 + 正式合成
    summ.finish(timeout_s=args.finish_timeout)
    atom_w.close()
    elapsed = time.time() - t_wall

    # ---- 指标 ----
    chapters = [c for c in insights["chapters"].values() if c.get("status") == "final"]
    print(f"  窗口 {len(insights['windows'])} · 章节 {len(chapters)} · "
          f"原子调用 {counter['atom']} · 合成调用 {counter['chap']} · "
          f"耗时 {elapsed:.1f}s", flush=True)
    if counter["failed"]:
        print(f"  （其中 {counter['failed']} 次按 --fail-window 设计失败）", flush=True)

    intervals = []
    ws = sorted(insights["windows"], key=lambda w: w.get("lo") or 0)
    for a, b in zip(ws, ws[1:]):
        intervals.append((_secs(b.get("t") or "00:00:00") - _secs(a.get("t") or "00:00:00")))
    intervals = sorted(x for x in intervals if x >= 0)
    med = intervals[len(intervals) // 2] if intervals else None

    open_ = [w for w in insights["windows"] if not (w.get("n_points") or 0)]
    board = sum(1 for c in chapters for s in (c.get("sentences") or [])
                if s.get("flag") == "board")
    total_sent = sum(len(c.get("sentences") or []) for c in chapters)
    reg = [d for d in insights["deadlines"] if d.get("source") == "regex"]
    mod = [d for d in insights["deadlines"] if d.get("source") == "model"]

    durs = []
    for c in chapters:
        durs.append(_secs(c.get("t1") or "00:00:00") - _secs(c.get("t0") or "00:00:00"))

    metrics = {
        "session": session.name, "dry": bool(args.dry),
        "sentences": len(entries),
        "minutes": round(prev / 60.0, 1),
        "windows": len(insights["windows"]),
        "atom_calls": counter["atom"], "chapter_calls": counter["chap"],
        "failed_calls": counter["failed"],
        "input_chars": counter["in_chars"],
        "input_tokens_est": counter["in_chars"] // 4,
        "chapters": len(chapters),
        "chapters_interim": sum(1 for c in insights["chapters"].values()
                                if c.get("status") == "interim"),
        "empty_window_ratio": round(len(open_) / len(insights["windows"]), 3)
        if insights["windows"] else None,
        "board_sentence_ratio": round(board / total_sent, 3) if total_sent else None,
        "window_interval_median_s": med,
        "chapter_avg_seconds": round(sum(durs) / len(durs), 1) if durs else None,
        "deadlines_regex": len(reg), "deadlines_model": len(mod),
        "jev_gate_calls": summ.gate_calls,
        "gaps": len(insights["gaps"]),
        "elapsed_s": round(elapsed, 1),
    }

    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / f"{name}.outline.md").write_text(
        render_outline(name, entries, {**insights,
                                       "chapters": list(insights["chapters"].values()),
                                       "current": None}), encoding="utf-8")
    (outdir / f"{name}.metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_review(outdir / f"{name}.review.md", name, insights)
    return metrics


def _write_review(path: pathlib.Path, name: str, insights: dict) -> None:
    """随机抽 30 条，留一列给人填。⚠️ 人填的是「**来源句支撑得了这条吗**」，不是"好不好"。"""
    pool = [(a.get("text", ""), a.get("src")) for a in insights["atoms"]]
    for c in insights["chapters"].values():
        for s in (c.get("sentences") or []):
            pool.append((s.get("en", ""), s.get("src")))
    random.Random(20260930).shuffle(pool)          # 固定种子 = 可复现
    L_ = [f"# 评审 · {name}", "",
          "填最后一列：这条结论，**来源句支撑得了吗**？（是 / 否 / 部分）", "",
          "| # | 条目 | src | 支撑？ |", "|---|---|---|---|"]
    for i, (t, s) in enumerate(pool[:30], 1):
        L_.append(f"| {i} | {str(t)[:80]} | {s} |  |")
    L_.append("")
    path.write_text("\n".join(L_), encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="实时总结的离线回放探针")
    ap.add_argument("--session", action="append", required=True,
                    help="会话 .md（可以给多次）")
    ap.add_argument("--out", default="/tmp/classlive-probe",
                    help="输出目录（⚠️ 别放进仓库：转录里有别人的声音）")
    ap.add_argument("--dry", action="store_true",
                    help="用桩，不联网不花钱 —— 但调用次数与真跑一致，可用来报价")
    ap.add_argument("--no-chapters", action="store_true", help="关掉章节合成")
    ap.add_argument("--fail-window", default=None, metavar="HH:MM-HH:MM",
                    help="这段时间内的模型调用全部失败（测积压/重试）。"
                         "⚠️ 判据是**调用那一刻的模拟时间**")
    ap.add_argument("--finish-timeout", type=float, default=120.0)
    ap.add_argument("--no-jev", action="store_true",
                    help="不接 Jev 课务闸门（= 模型报的课务一条不报）。用来做「接/不接」的对照")
    args = ap.parse_args(argv)

    outdir = pathlib.Path(os.path.expanduser(args.out))
    outdir.mkdir(parents=True, exist_ok=True)
    print(f"输出目录 {outdir}   模式 {'DRY（不花钱）' if args.dry else '真实调用（花钱）'}\n")

    total = {"atom_calls": 0, "chapter_calls": 0, "minutes": 0.0, "input_chars": 0}
    for s in args.session:
        p = pathlib.Path(os.path.expanduser(s))
        if not p.exists():
            print(f"⚠ 找不到 {p}")
            continue
        print(f"▶ {p.name}")
        m = run_one(p, outdir, args)
        for k in total:
            total[k] += m.get(k) or 0
        print()

    print("=" * 56)
    print(f"合计：原子 {total['atom_calls']} 次 · 合成 {total['chapter_calls']} 次")
    print(f"      音频 {total['minutes']:.0f} 分钟 · 输入约 {total['input_chars'] // 4} tokens（估）")
    if args.dry:
        print("⚠️ 这是 DRY —— 一次都没花钱。真跑前把 --dry 去掉。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
