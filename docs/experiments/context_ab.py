#!/usr/bin/env python3
"""云端翻译「前文窗口」A/B：老的滑动窗口（最近 5 句）vs 块对齐窗口（10 句一块，窗口 10–19 句）。

    ClassLive.app/Contents/MacOS/python docs/experiments/context_ab.py \\
        sessions/<会话>.md <课号> <起始句号> <句数> /tmp/ctx_ab.json

用**生产的 `CloudTranslator`**（`ctx_chunk=0` 对 `ctx_chunk=10`），喂同一批真实 ASR 句子，
前文取会话文件里已矫正的 EN。会调 DeepSeek（~200 次请求，约几分钱），只写 `/tmp` 里那个 JSON。
⚠️ 换一段**没跑过的**句子再测：DeepSeek 的前缀缓存会让重复的 prompt 命中率虚高。
⚠️ 首字延迟(TTFT)对并发敏感 —— 别同时开多个测；缓存命中率与 prompt 大小不受影响。
结果与取舍见 `context_ab.md`。
"""
import json, pathlib, statistics, sys, time

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import cloud_translator as ct                                        # noqa: E402
from obsidian_writer import ObsidianWriter                           # noqa: E402

SESSION, COURSE, START, N, OUT = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), sys.argv[5]
txt = pathlib.Path(SESSION).read_text(encoding="utf-8")
ents = [{"en": e["en"], "asr": e["asr"]} for e in ObsidianWriter._parse(txt)]
key = ct.load_api_key(None)
if not key:                       # ⚠️ 不用 `assert`：`python -O` 会把它剥掉，
    sys.exit("没有 DeepSeek key")  #    那样 `None` 会被传进 load_translator 更深处才炸
out = {}
for tag, chunk in (("legacy-k5", 0), ("chunk10", 10)):
    tr = ct.load_translator(key, "deepseek-flash", str(ROOT / "glossary.txt"), 5, course=COURSE,
                            ctx_chunk=chunk, collect_usage=True)
    rows = []
    for i in range(START, START + N):
        first = {"t": None}; t0 = time.time()

        def on_zh(s, first=first, t0=t0):
            if first["t"] is None:
                first["t"] = time.time() - t0
        r = tr.fix_and_translate_stream(ents[i]["asr"], [e["en"] for e in ents[:i]], on_zh=on_zh)
        u = tr.last_usage or {}
        rows.append({"asr": ents[i]["asr"], "en": r.en_fixed, "zh": r.zh, "ttft": first["t"],
                     "prompt": u.get("prompt_tokens"), "hit": u.get("prompt_cache_hit_tokens")})
    ok = [r for r in rows if r["prompt"]]
    # ⚠️ TTFT 要**单独**滤掉 None：空流/中断的请求不会触发 `on_zh`（`ttft` 就是 None），
    #    而 `statistics.median` 内部要排序，None 与 float 比会抛 TypeError ——
    #    那是在整轮 API 调用**都跑完之后**才崩，白花钱。（`scripts/cache_probe.py:52`
    #    那条注释记着这类请求确实会发生。）
    tt = [r["ttft"] for r in rows if r["ttft"] is not None] or [float("nan")]
    print(f"{tag:10s} prompt {statistics.mean(r['prompt'] for r in ok):6.0f} tok | "
          f"缓存命中 {sum(r['hit'] or 0 for r in ok) / sum(r['prompt'] for r in ok):.2f} | "
          f"TTFT p50 {statistics.median(tt):.2f}s", flush=True)
    out[tag] = rows
json.dump(out, open(OUT, "w"), ensure_ascii=False)
