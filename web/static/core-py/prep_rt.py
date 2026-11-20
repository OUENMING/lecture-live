"""网页版「开课前的准备」的胶水：把 `extract.py` / `prep.py` 的切片包成 JSON 进出的函数。

`rt.py` 的兄弟 —— 同样**手写**（不由 sync-core.py 生成），同样只做搬运，不重写任何判断：
抽块调 `extract._pptx_blocks` / `_docx_blocks`，提问调 `prep._prompt` / `_extract_user` / `_chunks`，
复核与排序调 `prep._fold` / `_term_stats` / `_verify_folded` / `_rank`。
判断全在切片里，所以与桌面**同一份代码**，`sync-core.py --check` 钉着。

对外六个函数（全部收/回字符串或 JSON 字符串，供 `web/src/lib/prep/py.ts` 一行一个调用）：
`pptx(path)` · `docx(path)` · `prompt()` · `chunks(json)` · `user(course, json)` · `score(json, json)`。

为什么经由虚拟文件系统：`_pptx_blocks` 的签名吃一个路径（桌面就是这么调的），
与其在这里改它的签名（那会让切片不再是桌面那份），不如让 TS 侧先把字节写进 `/tmp`。
⚠️ 抽块失败一律落成 `{status, error}` 而不是抛 —— 一份坏课件不该让整场准备没结果（同桌面 `extract()`）。
"""
import json
import pathlib

import extract
import prep


def _blocks_json(blocks):
    return [[b.text, b.kind, b.page] for b in blocks]


def _blocks_of(raw):
    return [extract.Block(str(t), str(k), int(p)) for t, k, p in raw]


def _stats_json(st):
    return {
        "pages": st.pages,
        "shapes_seen": st.shapes_seen,
        "shapes_skipped": st.shapes_skipped,
        "skipped_by_reason": dict(st.skipped_by_reason),
        "ocr_pages": st.ocr_pages,
        "by_kind": dict(st.by_kind),
    }


def _run(kind, path):
    """抽一份课件 → `{"status","error","blocks","stats"}`。`status` 的口径照桌面 `extract()`。"""
    p = pathlib.Path(path)
    try:
        if kind == "docx":
            blocks, st = extract._docx_blocks(p, False, None, None)
        else:
            blocks, st = extract._pptx_blocks(p, False, None, None)
    except Exception as e:                                   # noqa: BLE001
        return json.dumps({"status": "unreadable", "error": f"{type(e).__name__}: {str(e)[:120]}",
                           "blocks": [], "stats": None}, ensure_ascii=False)
    if not blocks:
        return json.dumps({"status": "empty", "error": "抽不出任何文字",
                           "blocks": [], "stats": _stats_json(st)}, ensure_ascii=False)
    return json.dumps({"status": "ok", "error": "", "blocks": _blocks_json(blocks),
                       "stats": _stats_json(st)}, ensure_ascii=False)


def pptx(path):
    return _run("pptx", path)


def docx(path):
    return _run("docx", path)


def prompt():
    """候选词抽取的 system 提示词（`prep._prompt()` —— 数字与 kind 说明都是现场插的）。"""
    return prep._prompt()


def chunks(per_file_json):
    """`[[文件名, [[text, kind, page], …]], …]` → `prep._chunks` 的分块（每块还是那个二元组形状）。"""
    per_file = [(str(name), _blocks_of(raw)) for name, raw in json.loads(per_file_json)]
    return json.dumps([[[name, _blocks_json(bs)] for name, bs in g] for g in prep._chunks(per_file)],
                      ensure_ascii=False)


def user(course, group_json):
    """一次调用的 user 段（重复行清单也在里面 —— `_extract_user` 生成，网页版不另写一份）。

    `domain` 不给：网页版没有桌面术语表首行那条领域先验，模型按课程名判领域。
    """
    group = [(str(name), _blocks_of(raw)) for name, raw in json.loads(group_json)]
    return prep._extract_user(str(course), group)


def score(blocks_json, merged_json):
    """合并后的候选 → 复核 + 排序（桌面 `prepare()` ③ 那一段，逐字同一套判据）。

    `merged_json` 是 `[{term, confidence, why}, …]`；回来的是 `prep._rank` 的行（带
    `verified` / `count` / `spread` / `spread_ratio` / `title_hits` / `score` / `rank`）。

    ⚠️ 这段装配（`verified = count > 0 or _verify_folded(...)`）在桌面 `prepare()` 的**未切片部分**，
    所以 `--check` 盯不到它 —— 桌面改了这条判据，这里要跟着改。判据本身（`_verify_folded` /
    `_term_stats` / `_rank`）都是切片，不在这里重写。
    """
    blocks = _blocks_of(json.loads(blocks_json))
    merged = json.loads(merged_json)
    corpus = prep._fold("\n".join(b.text for b in blocks))
    stats = prep._term_stats(blocks, [m["term"] for m in merged])
    rows = []
    for m in merged:
        st = stats.get(m["term"].lower()) or {"count": 0, "spread": 0,
                                              "spread_ratio": 0.0, "title_hits": 0}
        # 短路：`count > 0` = 「某一**块**里出现过」，而块文本是语料的子集 → 必然 verified。
        # 反方向不成立（跨块出现的词），所以还要上面那次全文复核。
        verified = st["count"] > 0 or prep._verify_folded(m["term"], corpus)
        rows.append(dict(m, verified=verified, **st))
    return json.dumps(prep._rank(rows), ensure_ascii=False)
