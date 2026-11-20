"""用桌面 CPython 调 prompt_core 生成请求体 fixture（只用合成英文句子，不含课堂数据）。

为什么要有这一步：判据是「网页版的请求体与桌面逐字相同」，而桌面那份拼装只存在于
`/Users/owen/lecture-live-prompt/prompt_core.py`（临时来源）。fixture 由它生成，
测试只读 fixture —— 于是跑测试的人不需要有桌面仓库。
重生成：python3 tests/translate/make-fixtures.py（桌面仓库不在本机时直接退出）。
"""
from __future__ import annotations

import json
import pathlib
import sys

HERE = pathlib.Path(__file__).parent
DESKTOP = pathlib.Path("/Users/owen/lecture-live-prompt")
OUT = HERE / "fixtures" / "prompts.json"
TAIL = (
    "Now translate the ASR utterance above into Chinese and output exactly:\n"
    "ZH: <Chinese translation of the CORRECTED sentence>\n"
    "EN: <same utterance with ASR mishearings fixed>\n"
    "Do not repeat the course-terms list. No headings."
)

CASES = [
    {"en": "The midterm covers chapters one to three.", "context": []},
    {"en": "The midterm covers chapters one to three.", "context": ["Hello everyone.", "Today we start with supply."]},
    {"en": "we're max chocolate properties", "context": ["It is a solid at room temperature."]},
]


def main() -> int:
    if not (DESKTOP / "prompt_core.py").exists():
        print(f"没有桌面仓库 {DESKTOP}（prompt_core 只在那里）", file=sys.stderr)
        return 1
    sys.path.insert(0, str(DESKTOP))
    import prompt_core as pc

    cases = []
    for c in CASES:
        for cfg in (
            {},
            {"domain": "Introduction to Economics", "terms": ["midterm", "supply", "demand"],
             "core": ["Brightspace", "tutorial"], "always": ["utility"], "term_zh": {}},
            {"domain": "", "terms": [], "core": [], "always": [],
             "term_zh": {"midterm": "期中考试"}, "ctx_chunk": 0, "max_context": 1},
        ):
            ctx_chunk = int(cfg.get("ctx_chunk", 10))
            max_context = int(cfg.get("max_context", 2))
            user = pc.user_content(
                cfg.get("domain", ""), cfg.get("terms", []), cfg.get("core", []),
                cfg.get("always", []), cfg.get("term_zh", {}),
                c["en"], c["context"], TAIL, ctx_chunk=ctx_chunk,
                max_context=max_context, zh=True)
            draft = pc.draft_user(cfg.get("domain", ""), cfg.get("always", []),
                                  cfg.get("term_zh", {}), c["en"], c["context"])
            cases.append({
                "en": c["en"], "context": c["context"], "cfg": cfg,
                "final": {"system": pc.SYSTEM_PROMPT_CLOUD, "user": user},
                "draft": {"system": pc.DRAFT_SYSTEM_CLOUD, "user": draft},
            })
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"tail": TAIL, "cases": cases}, ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8")
    print(f"写了 {len(cases)} 条 → {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
