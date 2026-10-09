#!/usr/bin/env python3
"""离线回放草稿译文的触发策略（读 stream_eval 产出的 jsonl，不跑模型、不花钱）。

数：请求数、首次触发延迟、句末"没被覆盖的尾巴"占比、请求落在从句边界的比例。
⚠️ 这些只是代理指标：量不出译文质量。要不要切 clause，需要拿真译文看一眼再定。
用法：python3 scripts/draft_policy_sim.py out1.jsonl [out2.jsonl ...]
"""
import json, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import draft_policy as P
import stream_metrics as M


def load(path):
    utts = {}
    for line in open(path, encoding="utf-8"):
        r = json.loads(line)
        utts.setdefault(r["utt"], {"drafts": [], "final": None})
        if r["kind"] == "draft":
            utts[r["utt"]]["drafts"].append((r["audio_t"], r["text"]))
        else:
            utts[r["utt"]]["final"] = (r["audio_t"], r["text"])
    return [u for _, u in sorted(utts.items())]


def simulate(utts, policy):
    reqs = bound = 0
    first_delay, tails = [], []
    for u in utts:
        last_tr, last_len = 0.0, 0
        prev_text = ""
        sent = []
        for t, text in u["drafts"]:
            n = len(text.split())
            # ⚠️ 派生量走 `P.append_only`（**判据的唯一定义点**）—— 原来这里拿
            #    `stream_metrics.normalize` 自己算了一份，与主循环那份不等价，
            #    于是这份离线模拟预测的不是生产真跑的那条路。
            ok = P.should_translate(policy, now=t, last_tr=last_tr, n_words=n, last_len=last_len,
                                    text=text, prev_text=prev_text)
            prev_text = text
            if ok:
                reqs += 1
                bound += P.ends_at_clause(text)
                last_tr, last_len = t, n
                sent.append((t, n))
        if u["drafts"]:
            t0 = u["drafts"][0][0]
            if sent:
                first_delay.append(sent[0][0] - t0)
            final_n = len(u["drafts"][-1][1].split())
            covered = sent[-1][1] if sent else 0
            if final_n:
                tails.append(max(0, final_n - covered) / final_n)
    return {"requests": reqs, "boundary_rate": bound / reqs if reqs else None,
            "first_delay_p50": M.percentile(first_delay, 50),
            "uncovered_tail_mean": sum(tails) / len(tails) if tails else None}


def main():
    for path in sys.argv[1:]:
        utts = load(path)
        print(os.path.basename(path), f"({len(utts)} 句)")
        for pol in ("legacy", "clause"):
            r = simulate(utts, pol)
            fmt = lambda v: "-" if v is None else (f"{v:.2f}" if isinstance(v, float) else str(v))
            print(f"  {pol:7s} 请求 {r['requests']:3d}  边界命中 {fmt(r['boundary_rate'])}  "
                  f"首发延迟p50 {fmt(r['first_delay_p50'])}s  句末未覆盖尾巴 {fmt(r['uncovered_tail_mean'])}")


if __name__ == "__main__":
    main()
