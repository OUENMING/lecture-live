#!/usr/bin/env python3
"""真数据重放：Soniox 原始消息 → 切句 → `SpeakerLabeler`，看字母落在谁头上。

用法：`ClassLive.app/Contents/MacOS/python scripts/replay_speaker_label.py <原始消息.jsonl> ...`
为什么切句要认 speaker 变化：Soniox 分人时实测会跨人连读（一个 token 结束、下一个人接着而中间没有标点），
不切就会把两个人的话算到首 token 头上。只读，只往 stdout 写。
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import speaker_label as SL                                        # noqa: E402

END_PUNCT = frozenset(".?!。？！")
PREVIEW = 60


def read_tokens(path: pathlib.Path) -> list[tuple[str, str, int]]:
    """(文本, speaker, 起始毫秒)。缺字段按空/0 处理 —— 录制脚本改过形状也不该炸。"""
    out: list[tuple[str, str, int]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            for tok in msg.get("tokens") or []:
                text = tok.get("text") or ""
                if not text or text.startswith("<") or not tok.get("is_final"):
                    continue
                out.append((text, tok.get("speaker") or "", int(tok.get("start_ms") or 0)))
    return out


def split_sentences(tokens: list[tuple[str, str, int]]) -> list[tuple[str, int, str]]:
    """→ (speaker, 起始毫秒, 句文本)。句末标点切；speaker 变也切。"""
    out: list[tuple[str, int, str]] = []
    cur: list[str] = []
    sp, start = "", 0
    for text, tok_sp, ms in tokens:
        cut = bool(cur) and (tok_sp != sp or cur[-1].rstrip()[-1:] in END_PUNCT)
        if cut:
            out.append((sp, start, "".join(cur).strip()))
            cur = []
        if not cur:
            sp, start = tok_sp, ms
        cur.append(text)
    if cur:
        out.append((sp, start, "".join(cur).strip()))
    return out


def mmss(ms: int) -> str:
    return f"{ms // 60000}:{ms // 1000 % 60:02d}"


def replay(path: pathlib.Path) -> None:
    sents = split_sentences(read_tokens(path))
    lab = SL.SpeakerLabeler()
    marked: list[tuple[int, str, str, str]] = []
    for sp, ms, text in sents:
        label = lab.observe(sp, text)
        if label:
            marked.append((ms, sp, label, text))

    main = lab.main
    print(f"\n=== {path}")
    print(f"  句数 {len(sents)}；主讲 {main or '(无)'}；字母 {lab.labels or '(无)'}")
    counts: dict[str, int] = {}
    for sp, _, text in sents:
        counts[sp] = counts.get(sp, 0) + SL.count_units(text)
    for sp in sorted(counts, key=lambda s: -counts[s]):
        print(f"    speaker {sp or '(空)'}: {counts[sp]} 单位")
    print(f"  被标字母的句子 {len(marked)} 句：")
    for ms, sp, label, text in marked:
        print(f"    {mmss(ms)}  {label}  sp={sp}  {text[:PREVIEW]}")
    wrong = [m for m in marked if m[1] == main]
    print(f"  最终主讲被标了字母的句数：{len(wrong)}"
          + (f"  ⚠️ {[f'{mmss(m[0])} {m[2]}' for m in wrong[:5]]}" if wrong else ""))


def main(argv: list[str]) -> int:
    paths = [pathlib.Path(a).expanduser() for a in argv[1:]]
    if not paths:
        print(__doc__)
        return 2
    missing = 0
    for p in paths:
        if not p.is_file():
            print(f"\n=== {p}\n  ⚠️ 文件不存在，跳过")
            missing += 1
            continue
        replay(p)
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
