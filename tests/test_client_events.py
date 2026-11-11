#!/usr/bin/env python3
"""轻量事件时间线 + 上传清单的判据（`client_events.py` / `testmode.make_upload_manifest`）。"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import client_events as CE                                          # noqa: E402
import testmode as TM                                               # noqa: E402


def main_() -> int:
    with tempfile.TemporaryDirectory() as d:
        sess = pathlib.Path(d) / "2026-11-11_120000_ECON10740.md"
        check_p = []
        assert CE.emit(sess, "session_start", version="3.11.1", n=1,
                       bad={"secret": "x"})
        p = CE.path_for(sess)
        rec = json.loads(p.read_text(encoding="utf-8").splitlines()[0])
        check_p.append(("⭐ 事件写成一行 JSONL，只收标量",
                        rec["kind"] == "session_start" and rec["version"] == "3.11.1"
                        and "bad" not in rec, rec))
        check_p.append(("⭐⭐ 课号/路径/凭据字段一律不收",
                        CE.emit(sess, "x", course="ECON10740", path="/tmp/a",
                                token="sk-abcdefgh1234") and
                        all(k not in json.loads(p.read_text(encoding="utf-8").splitlines()[-1])
                            for k in ("course", "path", "token")), ""))
        CE.emit(sess, "y", files=3, has_course=True)
        _last = json.loads(p.read_text(encoding="utf-8").splitlines()[-1])
        check_p.append(("⭐⭐ 计数/布尔不被字段名误伤（files/has_course）",
                        _last.get("files") == 3 and _last.get("has_course") is True, _last))
        CE.emit(sess, "z", t="x", epoch=1, msg="/home/a/secret")
        _last = json.loads(p.read_text(encoding="utf-8").splitlines()[-1])
        check_p.append(("⭐⭐ 元数据字段不可被覆盖、绝对路径写进事件也被抹掉",
                        _last.get("kind") == "z" and _last.get("t") != "x"
                        and _last.get("epoch") != 1
                        and "<path>" in str(_last.get("msg")), _last))
        check_p.append(("⭐ 文件名是 <stem>.client_events.jsonl",
                        p.name.endswith(".client_events.jsonl"), p.name))
        old = CE.MAX_BYTES
        try:
            CE.MAX_BYTES = 1
            check_p.append(("⭐ 超过上限停止写（返回 False）",
                            CE.emit(sess, "again") is False, ""))
        finally:
            CE.MAX_BYTES = old

        stem = pathlib.Path(d) / "2026-11-11_120000_ECON10740"
        for name, data in ((".report.json", "{}"), (".001.opus", "x"),
                           (".bundle.zip", "z"), (".client_events.jsonl", "{}\n"),
                           (".md", "t"), (".pending.json", "{}")):
            (pathlib.Path(str(stem) + name)).write_text(data, encoding="utf-8")
        note = pathlib.Path(d) / "Lectures" / "2026-11-11_ECON10740.md"
        note.parent.mkdir()
        note.write_text("n", encoding="utf-8")
        note.with_suffix(".html").write_text("h", encoding="utf-8")
        out = TM.make_upload_manifest(stem, note_path=str(note))
        doc = json.loads(pathlib.Path(out).read_text(encoding="utf-8"))
        text = json.dumps(doc, ensure_ascii=False)
        check_p.append(("⭐⭐ 清单不含课号/日期/绝对路径",
                        "ECON10740" not in text and str(d) not in text, text[:200]))
        rows = {r["name"]: r for r in doc["files"]}
        check_p.append(("⭐ bundle.zip 标为只留本机",
                        rows.get("bundle.zip", {}).get("where") == "local_only", rows))
        check_p.append(("⭐⭐ 会话 .md 显示为 transcript.md（不是裸 'md'）",
                        "transcript.md" in rows and "md" not in rows, sorted(rows)))
        check_p.append(("⭐ 崩溃 journal 不进诊断包（local_only）",
                        rows.get("pending.json", {}).get("where") == "local_only", rows))
        check_p.append(("⭐ 笔记按类型名出现（note.md/note.html）",
                        {"note.md", "note.html"} <= set(rows), sorted(rows)))
    for name, ok, detail in check_p:
        print(("  ✅ " if ok else "  ❌ ") + name + ("" if ok else f"  {detail}"))
    return 0 if all(ok for _, ok, _ in check_p) else 1


if __name__ == "__main__":
    sys.exit(main_())
