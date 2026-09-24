#!/usr/bin/env python3
"""测 DeepSeek 缓存在真实课堂回放下的命中率。

用法(必须在 venv 里跑):
    .venv/bin/python cache_probe.py <session.md> [n] [course]

对照两种 prompt 形态(每轮发给模型的"句子内容"完全相同，只改排列方式):
  A = 现状: 每轮一个全新的 user 消息(内含滑动窗口的最近 2 句) -> 前缀每轮都变
  B = 只追加: 每轮的 user 消息都留在 messages 里(多轮对话) -> 前缀稳定增长

只读 token 用量，不写任何文件。
"""
import json
import pathlib
import re
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from cloud_translator import CloudTranslator, SYSTEM_PROMPT_CLOUD, load_api_key  # noqa: E402
from translator import load_terms, core_terms, course_term_list, course_title    # noqa: E402
from translator import domain_block, select_terms                                # noqa: E402


def build_user(tr, en, ctx):
    """与 CloudTranslator.fix_and_translate_stream 里构造的 user 消息完全一致。"""
    ctx_block = "\n".join(f"- {c}" for c in ctx) if ctx else "(none)"
    return (f"{domain_block(tr._domain)}"
            f"Course terms:\n{select_terms(en, tr._terms, core=tr._core, always=tr._course_terms)}\n\n"
            f"Recent context (already corrected):\n{ctx_block}\n\n"
            f"ASR utterance:\n{en}\n\n"
            f"Now translate the ASR utterance above into Chinese and output exactly:\n"
            f"ZH: <Chinese translation of the CORRECTED sentence>\n"
            f"EN: <same utterance with ASR mishearings fixed>\n"
            f"Do not repeat the course-terms list. No headings.")


class Acc:
    def __init__(self, label):
        self.label = label
        self.hit = self.miss = 0
        self.calls = 0
        self.t0 = time.time()

    def add(self, usage):
        if not usage:
            return
        self.hit += usage.get("prompt_cache_hit_tokens", 0) or 0
        self.miss += usage.get("prompt_cache_miss_tokens", 0) or 0
        self.calls += 1

    def report(self):
        tot = self.hit + self.miss
        rate = (self.hit / tot * 100) if tot else 0
        print(f"\n[{self.label}]  {self.calls} 次请求, 用时 {time.time()-self.t0:.0f}s")
        print(f"  命中 {self.hit:,} tok / 未命中 {self.miss:,} tok  =>  命中率 {rate:.1f}%")
        if self.calls:
            print(f"  平均每请求 prompt {tot/self.calls:,.0f} tok")
        return rate


def main():
    if len(sys.argv) < 2:
        sys.exit("用法: cache_probe.py <session.md> [n] [course]")
    sess = pathlib.Path(sys.argv[1])
    if not sess.exists():
        sys.exit(f"文件不存在: {sess}")
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 40
    course = sys.argv[3] if len(sys.argv) > 3 else ""

    utts = [m.group(1).strip() for m in
            re.finditer(r"^>\s*\*\*ASR\*\*: (.+)$",
                        sess.read_text(encoding="utf-8"), re.M)]
    utts = [u for u in utts if u][:n]
    print(f"回放 {len(utts)} 句, 课程={course!r}, 文件={sess.name}")

    key = load_api_key()
    if not key:
        sys.exit("没有 API key")

    tr = CloudTranslator(key, "deepseek-flash",
                         glossary_terms=load_terms(str(HERE / "glossary.txt"), course),
                         max_context=2, core=core_terms(course),
                         course_terms=course_term_list(str(HERE / "glossary.txt"), course),
                         domain=course_title(str(HERE / "glossary.txt"), course),
                         collect_usage=True)

    # ---- A: 现状(每轮全新 user 消息) ----
    accA, ctx = Acc("A 现状: 滑动窗口 / 每轮新 user"), []
    for i, en in enumerate(utts):
        msgs = [{"role": "system", "content": SYSTEM_PROMPT_CLOUD},
                {"role": "user", "content": build_user(tr, en, ctx)}]
        out = ""
        for d in tr._stream_chat(msgs, max_tokens=220):
            out += d
        accA.add(tr.last_usage)
        # 维护"已矫正"上下文(与生产一致: 取 EN 行)
        m = re.search(r"EN:\s*(.+)", out)
        ctx.append(m.group(1).strip() if m else en)
        ctx = ctx[-2:]
        if (i + 1) % 10 == 0:
            print(f"  A {i+1}/{len(utts)} 命中率 {accA.hit/(accA.hit+accA.miss+1e-9)*100:.1f}%")
    rA = accA.report()

    # ---- B: 只追加(多轮对话) ----
    accB, ctx = Acc("B 只追加: 多轮对话"), []
    msgs = [{"role": "system", "content": SYSTEM_PROMPT_CLOUD}]
    for i, en in enumerate(utts):
        msgs.append({"role": "user", "content": build_user(tr, en, ctx)})
        out = ""
        for d in tr._stream_chat(msgs, max_tokens=220):
            out += d
        accB.add(tr.last_usage)
        msgs.append({"role": "assistant", "content": out})
        m = re.search(r"EN:\s*(.+)", out)
        ctx.append(m.group(1).strip() if m else en)
        ctx = ctx[-2:]
        if (i + 1) % 10 == 0:
            print(f"  B {i+1}/{len(utts)} 命中率 {accB.hit/(accB.hit+accB.miss+1e-9)*100:.1f}%")
    rB = accB.report()

    print(f"\n结论: B 比 A 命中率高 {rB - rA:+.1f} 个百分点")


if __name__ == "__main__":
    main()
