#!/usr/bin/env python3
"""跨课评测：note v2 的产物在**别的课**上还成不成立（作者 2026-10-17 第六轮）。

为什么要有这个文件 —— 前五轮的验收全压在 10-15 那一节课上，换三节课立刻露出五类问题：

| # | 现象（用户看得见） | 根因 |
|---|---|---|
| 1 | 覆盖度三节全 0、§9 假报「全缺」 | `align` 一次调用 `ALIGN_MAX_TOKENS=2000`，要点 >100 条时 JSON 被截断，异常在 `align` 内被吞成 `{}` |
| 2 | 变式被「已修正」回**例题**参数 | 三道核把变式与例题的断言混在一批里核 |
| 3 | 变式答案里整段模型自言自语 | `note_guards.scrub` 只扫 `prose` / `pitfalls` |
| 4 | 术语库收 `Brightspace`/`PhD`、拒 `externality` | 入库判据只有「出现过一次」 |
| 5 | 任何课提到停产就插 ECON10790 的口径 | `note_guards.CALIBER_LINE` 是写死的一句 |

判据在一节课上是"过了"，在别的课上是"错得更多" —— 所以要有一份**跨课、可复算**的打分表：
每节一行、总表一份，JSON + Markdown 两种落盘（作者要拿它并排看改前改后）。

## 三个子命令

    note_eval.py run  --out DIR [--set dev|holdout|all] [--mode fresh|replay] [--label L]
    note_eval.py render --data FILE [--json]      # 只从 note_data.json 重渲染（零 LLM）
    note_eval.py report --out DIR [--runs a,b,c]  # 汇总 runs/*.json（零 LLM）

`run` 一节一个**子进程**：隔离 HOME / vault / glossary（做法照 `scratchpad/gen/gen.py`），
子进程里 `HOME=<out>/<key>/var`、`Study` 软链到真 vault（**只读**）、`glossary.txt` 拷一份。
付费调用只用 `cloud_translator.load_test_key()`（`test1` 那个码，用量与作者自己分开）。

## `--mode replay`：零整节生成的 A/B

生成阶段（`note_v2.review`，整节最贵的一步）的**原始输出**存在 `<key>/review_raw.json`，
三次小调用的问答存在 `<key>/tape.json`。replay 时：`review` 直接回放缓存，
`chat` 按 `(system, user, max_tokens)` 精确匹配回放磁带，**没命中才真调**。
于是"只改后处理"的那些项（覆盖度、清理、术语、口径、版式）可以零整节生成地并排比较，
而且提示词没变的调用拿到的是**同一份**输出 —— 差异只来自被测的那处代码。

⚠️ replay **不做**端到端验收（提示词改过的地方要重跑 `fresh` 才算数）。
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
PY = ROOT / "ClassLive.app/Contents/MacOS/python"
PY = PY if PY.exists() else pathlib.Path(sys.executable)
SCRATCH = pathlib.Path("/private/tmp/claude-501/-Users-owen/"
                       "ca5a4f17-da2c-454a-ae54-4174f7b25187/scratchpad/notes2")

#: 评测集。`holdout` **开发期间不许看**（作者 2026-10-17）：它只用来做最后一次
#: 「改完了在没见过的课上还成立吗」的检验 —— 中途拿它调参就把它污染成第二个开发集了。
SESSIONS = {
    "econ10790-1015": {"session": "2026-10-15_131620_ECON10790", "course": "ECON10790",
                       "date": "2026-10-15", "set": "dev"},
    "econ10770-1009": {"session": "2026-10-09_120723_ECON10770", "course": "ECON10770",
                       "date": "2026-10-09", "set": "dev"},
    "econ10740-1013": {"session": "2026-10-13_150815_ECON10740", "course": "ECON10740",
                       "date": "2026-10-13", "set": "dev"},
    "soc10020-1012": {"session": "2026-10-12_130857_SOC10020", "course": "SOC10020",
                      "date": "2026-10-12", "set": "dev"},
    "econ10770-1013": {"session": "2026-10-13_122245_ECON10770", "course": "ECON10770",
                       "date": "2026-10-13", "set": "holdout"},
}
ORDER = list(SESSIONS)


# ================================================================= 打分（纯函数）
# 这一节全部是**纯函数 + 离线**：判据直接调它们（`tests/test_note_eval.py`），
# 不联网、不读文件、不碰 vault。指标的"口径"只在这里定义一次。

#: 「模型对自己说话」的判据（**比 `note_guards.scrub` 宽**：它是量具，要能抓到
#: 清理层漏掉的那些）。作者在产物里点名的原句是全字段扫出来的 ——
#: `？检查：…这不对，因为…但通常我们要求严格偏好？`（ECON10770 变式 2 的答案）。
_SELFTALK = [
    # ① 第一人称 + 猜测（与 `note_guards._SPEC` 同一条纪律，量具不共用它的实现）
    re.compile(r"(?:我|我们)[^。；;\n]{0,20}"
               r"(?:可能|也许|大概|似乎|恐怕|估计|不确定|猜|怀疑|说不定|或许|"
               r"听错|漏掉|漏了|记错|看错|说错|写错|反推|凑)"),
    # ② 猜教授的意图（「可能是简化处理」）
    re.compile(r"(?:可能|也许)[^。；;\n]{0,8}(?:是|为|想|为了|故意)"),
    re.compile(r"（\s*(?:假设|假定)"),
    # ③ 自言自语/自我纠错的独白（模型的思考过程漏进了答案）
    # ⚠️ 刻意比产品（`note_guards._SELF_TALK`）宽一档：量具要能抓到清理漏掉的那些。
    #    但**不**收裸的「不对，」—— 自测答案是"不对。讲者说…"这种正经写法，
    #    把它算成独白会让这一列失去意义（实测：10740 的 qa 就是这么被误计的）。
    re.compile(r"需要仔细检查|重新检查|再检查|检查[：:]|这不对|(?<=[？?])不对|"
               r"等等[，,]|嗯[，,]|先看看|让我想|我想想|重新想|其实不对|也不对|"
               r"但这(?:不对|样)|前面(?:说|写)的|换一种说法"),
]

#: 露在外面的 TeX 命令（`\min` / `\sqrt` / `\underline{\hspace{1cm}}` …）。
_MATH_SPAN = re.compile(r"\$\$.+?\$\$|\$[^$\n]+\$", re.S)
_TEX_CMD = re.compile(r"\\[A-Za-z]{2,}")

#: 术语的「通用词」表（**评测侧的独立名单** —— 与 `note_terms` 里那份互为交叉验证：
#: 两边都判它是通用词，才算"非术语"；只有评测判、产品没判的，是产品漏了）。
GENERIC = frozenset("""
brightspace phd master phd-student director tutorial tutorials evidence ai conclusion
lecture lectures seminar module modules campus student students semester week weeks
assignment assignments exam exams essay essays slide slides pdf powerpoint zoom
euro euros dollar dollars percent percentage question questions answer answers example examples
""".split())

#: 算术等式（`5-3=2`、`7+5+3=15`）。判「这条『已修正』是不是错的」靠它 ——
#: 用户的结论是"错误来源是转录/课件里的数，不是模型算错"，所以**模型自己算对的地方
#: 被判成 mismatch，就是假修正**。能复算的部分用这个复核，复算不了的列给人看。
_EQ = re.compile(r"(-?\d+(?:\.\d+)?)\s*([+\-−*/×÷])\s*(-?\d+(?:\.\d+)?)"
                 r"\s*[=＝]\s*(-?\d+(?:\.\d+)?)")


def _op(sym: str, a: float, b: float):
    return {"+": a + b, "-": a - b, "−": a - b, "*": a * b, "×": a * b,
            "/": (a / b if b else None), "÷": (a / b if b else None)}[sym]


def arith_equations(text: str) -> list:
    """文本里的 `a?b=c` 全部提出来，带 `ok`（真的等于右边吗）。

    ⚠️ 只认**两个数 + 一个运算符**：`4-3=1` 这种。三项链（`7+5+3=15`）会被切成
       两个等式，判据只在"能一眼判真假"的地方下结论 —— 复算不了的一律不算数。
    """
    out: list = []
    for m in _EQ.finditer(str(text or "")):
        a, sym, b, c = float(m.group(1)), m.group(2), float(m.group(3)), float(m.group(4))
        v = _op(sym, a, b)
        out.append({"expr": m.group(0), "ok": (v is not None and abs(v - c) < 1e-9)})
    return out


def correction_audit(checks: list) -> dict:
    """「已修正」（`status == "mismatch"`）逐条复核 → 分级 + 明细。

    分级（机械能判的那部分）：
      · `wrong`        —— 断言里的等式**算得对**（模型没算错），却被改了 → 假修正；
      · `justified`    —— 断言里有一条等式算错了 → 改得有理；
      · `unverifiable` —— 提不出等式（纯文字断言）→ **列出来给人看**，不假装判过。
    """
    rows: list = []
    n = {"wrong": 0, "justified": 0, "unverifiable": 0}
    for c in (checks or []):
        if not isinstance(c, dict) or c.get("status") != "mismatch":
            continue
        claim = str(c.get("claim") or "")
        eqs = arith_equations(claim)
        if not eqs:
            verdict = "unverifiable"
        elif all(e["ok"] for e in eqs):
            verdict = "wrong"
        else:
            verdict = "justified"
        n[verdict] += 1
        rows.append({"verdict": verdict, "claim": claim,
                     "recomputed": str(c.get("recomputed") or ""),
                     "equations": [e["expr"] for e in eqs]})
    return {"n_mismatch": sum(n.values()), **n, "rows": rows}


def walk_strings(obj, path: str = ""):
    """review dict 里**每一个字符串**（含嵌套 list/dict）→ `(路径, 字符串)`。

    ⚠️ 这是「全字段扫」的唯一定义点：清理层与量具都走它，两处各写一遍遍历
       就会再次出现"清理只覆盖 prose、变式漏掉"那类事故。
    """
    if isinstance(obj, str):
        if obj.strip():
            yield path, obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk_strings(v, f"{path}/{k}" if path else str(k))
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            yield from walk_strings(v, f"{path}/{i}")


#: 扫自言自语时**跳过**的顶层键：`cover` 是机器数据（`.atoms.jsonl` 里实时记下的
#: 要点原文），不是模型写的散文 —— 把讲师的实时摘要算成"模型自言自语"，这一列就废了。
_SKIP_TOP = ("cover",)


def selftalk_hits(obj) -> list:
    """全字段扫自言自语 → `[{"path", "snippet", "pattern"}]`。"""
    out: list = []
    for path, s in walk_strings(obj):
        if path and path.split("/")[0] in _SKIP_TOP:
            continue
        for i, rx in enumerate(_SELFTALK):
            m = rx.search(s)
            if m:
                out.append({"path": path, "snippet": m.group(0)[:60], "pattern": i})
    return out


def tex_leaks(text: str) -> list:
    """Math 之外还露着的 TeX 命令 → `[(命令, 上下文)]`。

    ⚠️ 先剥 `$…$` / `$$…$$`：**包在 math 里的** `\\min` 是正常的（KaTeX 要的就是它），
       露源码说的是"没进 math 的那种"（作者 2026-10-17：兜底文字里写着 `\\min`）。
    """
    out: list = []
    for seg in _MATH_SPAN.split(str(text or "")):
        for m in _TEX_CMD.finditer(seg):
            ctx = seg[max(0, m.start() - 20): m.end() + 20].replace("\n", " ")
            out.append((m.group(0), ctx))
    return out


#: 扫自言自语时**不算数**的小节：纲要来自实时摘要、§9 是机器表 —— 都不是模型的散文。
_MACHINE_SECTIONS = ("## 📑 课堂纲要", "## 覆盖度与校验清单")


def md_prose(md: str) -> str:
    """md 里**模型写的那部分**（去掉附录 B、课堂纲要、§9 机器表）。

    ⚠️ 三处都是**机器数据**：逐句转录（学生自己的记录）、纲要（实时摘要 + 分章）、
       §9（覆盖度表 + 核定表）。把它们算进"模型自言自语"，这一列就废了
       （实测：10740 的「可能为那些受到负面影响的群体提供补偿」是正经讲解句子，
       但它在纲里、不在 review 里）。
    """
    body = md_body(md)
    for sec in _MACHINE_SECTIONS:
        body = body.split(sec)[0]
    return body


def md_body(md: str) -> str:
    """md 正文（**去掉附录 B 的逐句转录**，以及 §9 那两张机器表）。

    ⚠️ 不做这一步，量具会把 300 多句英文转录算成"正文"—— `note_render.chinese_ratio`
       踩过同一个坑（同一份笔记的中文占比从 71% 掉到 34%）。
    ⚠️ §9 的「覆盖度与校验清单」也要去掉：那一节印的是**实时要点原文**与机械核算的
       断言（机器数据），不是模型的散文（同 `_SKIP_TOP` 的理由）。
    ⚠️ 2026-10-17 第六轮起 md 是**简版**（散文搬去同目录的 HTML）—— 这个函数对简版
       基本是恒等变换；扫模型散文要**两个面一起**，见 `html_prose`。
    """
    body = re.split(r"\n## [^\n]*完整逐句转录", str(md or ""))[0]
    return re.split(r"\n## 覆盖度与校验清单", body)[0]


def html_prose(html: str) -> str:
    """HTML 里**模型写的那部分**：讲解/例题/变式/自测 —— 到「本课术语」为止。

    ⚠️ 第六轮起散文住在 HTML 里（md = 简版）—— 这一列只扫 md 的话会**假跌**
       （不是模型变干净了，是内容换了地方）。判据与 `md_body` 同一条：
       术语表（curated 词条注释）、复习排程、转录、覆盖度/核对清单都是**资料或机器
       数据**，不算"模型自言自语" —— 实测踩过：UCD 的词条注释「它可能作为案例…出现」
       命中「猜教授意图」那条模式，把 curated 资料算进了模型账上。
    ⚠️ 顺带剥标签（`<b>检查</b>：` 这种会让关键词被标签切开）。
    """
    body = re.split(r"<h2>(?:本课术语|📜 完整逐句转录)", str(html or ""))[0]
    return re.sub(r"<[^>]+>", "", body)


def hms(s) -> "int | None":
    """`"13:16:28"` / `"13:16"` → 秒；解不出 `None`。**与 `note_cover.hms` 同一条口径**。

    ⚠️ 两段式（`13:11`）也要认：小节的时间戳一半是模型写的 `HH:MM`，不认就会
       把那一节从独立覆盖度里漏掉 —— 而这一栏是**判产品报告有没有说谎**的基准。
    """
    parts = str(s or "").strip().split(":")
    if len(parts) == 2:
        if len(parts[1]) != 2:                  # `13:5` 这种残形状不猜（同 note_cover）
            return None
        parts += ["0"]
    if len(parts) != 3:
        return None
    try:
        h, m, sec = (int(p) for p in parts)
    except ValueError:
        return None
    return h * 3600 + m * 60 + sec if (0 <= h < 24 and 0 <= m < 60 and 0 <= sec < 60) else None


#: 一节的覆盖半径（秒）。⚠️ **必须与 `note_cover.SECTION_SPAN` 相等**：
#: 评测这边是"同一份口径的第二个实现"—— 两边不一致本身就是要报的 bug
#: （`tests/test_note_eval.py::test_半径与产品一致` 钉着）。
SECTION_SPAN = 900


def cover_by_time(skeleton: list, sections: list) -> dict:
    """**独立于产品**的覆盖度真值：按时间窗算（不看模型的 `align`，也不看产品的报告）。

    规则（与产品同一份口径，第二份实现）：
      · 一条要点 `ok` ⟺ 有时间戳、且落在某一节的 `[起点, 起点 + SECTION_SPAN)` 里；
      · 章：有要点看要点；没有要点看有没有节落在 `[t0, t1]`。
    """
    secs = sorted(t for t in (hms(s.get("ts")) for s in (sections or [])
                              if isinstance(s, dict)) if t is not None)

    def covered(t) -> bool:
        """`t` 落在哪一节的半径里（不问模型"这条要点被哪节讲了"，只问"这段时间有没有节"）。"""
        if not secs or t is None:
            return False
        return any(x <= t < x + SECTION_SPAN for x in secs)

    rows: list = []
    n_pts = n_ok = 0
    for ch in (skeleton or []):
        t0, t1 = hms(ch.get("t0")), hms(ch.get("t1"))
        t1 = t0 if t1 is None else t1
        pts: list = []
        for p in (ch.get("points") or []):
            ok = covered(hms(p.get("ts")))
            n_pts += 1
            n_ok += 1 if ok else 0
            pts.append({"ts": p.get("ts"), "point": p.get("zh"), "ok": ok})
        # 章的判据与 `note_cover.report` 同一口径（有要点看要点，没要点看有没有节落在窗内）
        if pts:
            ch_ok = any(p["ok"] for p in pts)
        else:
            ch_ok = bool(secs) and t0 is not None and any(t0 <= x <= t1 for x in secs)
        rows.append({"i": ch.get("i"), "title": ch.get("title"), "t0": ch.get("t0"),
                     "t1": ch.get("t1"), "ok": ch_ok, "points": pts})
    return {"n_chapters": len(rows), "n_ok": sum(1 for r in rows if r["ok"]),
            "n_points": n_pts, "n_covered": n_ok, "rows": rows}


def term_audit(added: list, *, transcript: str, materials_blob: str,
               generic=GENERIC, min_hits: int = 2) -> dict:
    """术语入库复核：`≥min_hits` 次出处 + 不在通用词表。

    ⭐ 作者 2026-10-17 第 4 条的两条闸门在这里量：**出现过 ≥2 次**、**不是通用词**。
       学科词优先（课件里出现过即加分）由产品侧排序实现，这里只判"该不该进"。
    """
    blob_t, blob_m = str(transcript or ""), str(materials_blob or "")
    try:
        from build_notes import compile_term
    except Exception:                                          # noqa: BLE001
        compile_term = None
    rows: list = []
    for t in (added or []):
        n_t = n_m = 0
        if compile_term is not None:
            try:
                rx = compile_term(t)
                n_t, n_m = len(rx.findall(blob_t)), len(rx.findall(blob_m))
            except Exception:                                  # noqa: BLE001
                n_t, n_m = blob_t.lower().count(t.lower()), blob_m.lower().count(t.lower())
        else:
            n_t, n_m = blob_t.lower().count(t.lower()), blob_m.lower().count(t.lower())
        why = []
        if n_t + n_m < min_hits:
            why.append(f"出处只有 {n_t + n_m} 次")
        if t.strip().lower() in generic:
            why.append("通用词")
        rows.append({"term": t, "in_transcript": n_t, "in_materials": n_m,
                     "non_term": bool(why), "why": why})
    return {"added": len(rows), "non_term": sum(1 for r in rows if r["non_term"]),
            "materials_hits": sum(1 for r in rows if r["in_materials"]),
            "rows": rows}


def locate_claim(review: dict, claim: str) -> str:
    """一条断言的**出处字段**（`sections/4/variants/1/a` 这种路径）。

    ⭐ 它是第 1 条改动（"只核 🎙/📖 的断言，模型自己出的例题/变式不做修正"）
       的**量具**：修正前后都能看出"被改的是哪一类字段"。
    """
    key = " ".join(str(claim or "").split())[:40]
    if not key:
        return ""
    for path, s in walk_strings(review):
        if key and key in " ".join(s.split()):
            return path
    return ""


def claim_origins(review: dict, checks: list) -> dict:
    """把 `mismatch` / `unchecked` 按**出处字段**归类（`variants` / `example` / …）。"""
    out: dict = {}
    for c in (checks or []):
        if not isinstance(c, dict):
            continue
        st = str(c.get("status") or "")
        if st not in ("mismatch", "unchecked"):
            continue
        path = locate_claim(review, str(c.get("claim") or ""))
        if path.startswith("sections/"):
            parts = path.split("/")
            unit = parts[2] if len(parts) > 2 else "prose"
        elif path:
            unit = path.split("/")[0]
        else:
            unit = "（找不到出处）"
        out.setdefault(st, {})
        out[st][unit] = out[st].get(unit, 0) + 1
    return out


# ================================================================= 子进程：生成 + 打分
def _isolate(key: str, out: pathlib.Path) -> pathlib.Path:
    """一节一个隔离库（照 `scratchpad/gen/gen.py`）：`HOME`/vault/glossary 全在里面。

    ⚠️ `Study` 是**软链到真 vault**（课件材料按 `<vault>/Study/<课号>/materials/` 找）——
       只读；今天这轮评测一个字节都不往那儿写（`glossary.txt` 也用的是库里的副本）。
    """
    d = out / key
    (d / "var/.classlive").mkdir(parents=True, exist_ok=True)
    src = pathlib.Path.home() / ".classlive"          # 只读：真 key 是 `test1` 那个码
    for f in ("test-key", "install-id"):
        if (src / f).exists() and not (d / "var/.classlive" / f).exists():
            shutil.copy2(src / f, d / "var/.classlive" / f)
    if not (d / "Study").exists() and not (d / "Study").is_symlink():
        (d / "Study").symlink_to("/Users/owen/Obsidian/SecondBrain/Study")
    glo = d / "glossary.txt"
    if not glo.exists():
        shutil.copy2(ROOT / "glossary.txt", glo)
    return d


def _tape_key(system: str, user: str, max_tokens: int) -> str:
    h = hashlib.sha256()
    for x in (system or "", user or "", str(max_tokens)):
        h.update(x.encode("utf-8", "replace"))
        h.update(b"\x00")
    return h.hexdigest()[:32]


def _reset_state(d: pathlib.Path, course: str) -> None:
    """**每一轮 fresh 都从同一个起点开始**（否则 A/B 比不了）。

    ⚠️ 三样东西必须回到起点：
       · `Lectures/` —— `note_path_for` 同一天同课已有文件时**加时间戳另存**（绝不覆盖），
         不清掉就会越跑越多份，体积/产物对不上；
       · `glossary/<课号>.txt` 与 `~/.classlive/.../prep-state.json`（墓碑）——
         不清掉的话第二轮"新加入术语"恒为 0（第一轮已经加过了），
         而"术语新增"正是要量的那一列。
    """
    shutil.rmtree(d / "Lectures", ignore_errors=True)
    shutil.rmtree(d / "glossary", ignore_errors=True)
    shutil.rmtree(d / "var/.classlive/courses" / course, ignore_errors=True)
    shutil.copy2(ROOT / "glossary.txt", d / "glossary.txt")


def child_run(key: str, out: pathlib.Path, mode: str, label: str,
              override: "dict | None" = None) -> dict:
    """**子进程实体**：隔离 → 生成 → 存原料 → 打分 → 写 metrics.json。"""
    one = dict(SESSIONS.get(key) or {})
    if override:
        one.update({k: v for k, v in override.items() if v})
    d = _isolate(key, out)
    _reset_state(d, one["course"])          # fresh / replay 都从同一个起点开始
    os.environ["HOME"] = str(d / "var")
    os.environ["CLASSLIVE_NOTE_MERGE"] = "0"
    os.environ["CLASSLIVE_NO_AUTO_UPDATE"] = "1"
    sys.path.insert(0, str(ROOT))

    import httpx
    import llm_client
    import note_v2
    from cloud_translator import load_test_key
    from obsidian_writer import ObsidianWriter

    usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
             "prompt_chars": 0, "resp_chars": 0}
    post = httpx.post

    def _post(*a, **kw):                                       # 用量（token 从响应里读）
        r = post(*a, **kw)
        try:
            u = (r.json() or {}).get("usage") or {}
            usage["prompt_tokens"] += int(u.get("prompt_tokens") or 0)
            usage["completion_tokens"] += int(u.get("completion_tokens") or 0)
        except Exception:                                      # noqa: BLE001
            pass
        return r
    httpx.post = _post

    tape_path = d / "tape.json"
    raw_path = d / "review_raw.json"
    tape: dict = {}
    if mode == "replay":
        tape = {e["k"]: e["resp"] for e in json.loads(tape_path.read_text("utf-8"))}
        raw = json.loads(raw_path.read_text("utf-8"))
    calls: list = []
    orig_chat = llm_client.chat_json

    def _chat(key_, model, system, user, *, max_tokens, temperature=0.2, timeout=180.0):
        k = _tape_key(system, user, max_tokens)
        usage["prompt_chars"] += len(system or "") + len(user or "")
        if k in tape:
            usage["calls"] += 1
            return copy.deepcopy(tape[k])
        got = orig_chat(key_, model, system, user, max_tokens=max_tokens,
                        temperature=temperature, timeout=timeout)
        usage["calls"] += 1
        usage["resp_chars"] += len(json.dumps(got, ensure_ascii=False))
        calls.append({"k": k, "max_tokens": max_tokens, "resp": got})
        return got
    llm_client.chat_json = _chat

    raw_box: dict = {}
    orig_review = note_v2.review

    def _review(entries, **kw):
        if mode == "replay":
            return copy.deepcopy(raw)                           # 整节最贵的一步：回放
        got = orig_review(entries, **kw)
        raw_box["review"] = copy.deepcopy(got)                  # ⚠️ 必须**当场**深拷：
        return got                                              # 调用方后面会原地改它
    note_v2.review = _review

    data_box: dict = {}
    orig_data = ObsidianWriter._note_data

    def _data(self, *a, **kw):
        got = orig_data(self, *a, **kw)
        data_box["data"] = got
        return got
    ObsidianWriter._note_data = _data

    sess = ROOT / "sessions" / f"{one['session']}.md"
    w = ObsidianWriter(vault=str(d), course=one["course"], mode="no",
                       api_key=load_test_key(), glossary_path=str(d / "glossary.txt"),
                       polish=False)
    w.enabled, w.mode = True, "yes"
    w.session_path, w._course, w._date = sess, one["course"], one["date"]
    w._n = len(ObsidianWriter._parse(sess.read_text(encoding="utf-8", errors="replace")))
    t0 = time.monotonic()
    line = w.close()
    elapsed = time.monotonic() - t0

    if mode == "fresh":
        raw_path.write_text(json.dumps(raw_box.get("review") or {}, ensure_ascii=False),
                            encoding="utf-8")
        tape_path.write_text(json.dumps(calls, ensure_ascii=False), encoding="utf-8")

    data = data_box.get("data")
    if data is not None:
        try:                                                   # NoteData -> 可复算的 JSON
            import dataclasses
            (d / "note_data.json").write_text(json.dumps(
                dataclasses.asdict(data), ensure_ascii=False), encoding="utf-8")
        except Exception as e:                                 # noqa: BLE001
            print(f"⚠ note_data.json 没写出来：{type(e).__name__}")

    m = score(key, d, one=one, data=data,
              entries=(data.entries if data is not None else []),
              elapsed=elapsed, usage=usage, mode=mode, line=line)
    (d / "metrics.json").write_text(json.dumps(m, ensure_ascii=False, indent=1),
                                    encoding="utf-8")
    return m


def _skeleton_for(one: dict) -> list:
    """这节课的确定性骨架（章节 + 实时要点）—— **只读**旁路文件。"""
    import atom
    import chapter
    import note_cover
    sess = ROOT / "sessions" / f"{one['session']}.md"
    ch = chapter.load(chapter.chapter_path_for(sess))
    at = atom.load(atom.AtomWriter(str(sess)).path)
    return note_cover.skeleton(ch.get("chapters"), at)


def _render_metrics(data) -> dict:
    """从 `NoteData` 复渲染一份，量**体积 + 渲染耗时**（零 LLM）。"""
    import note_html
    import note_render
    out: dict = {}
    t0 = time.monotonic()
    md = note_render.render(data)
    out["md_ms"] = round((time.monotonic() - t0) * 1000, 1)
    out["md_bytes"] = len(md.encode("utf-8"))
    t0 = time.monotonic()
    html = note_html.note_page(data)
    out["html_ms"] = round((time.monotonic() - t0) * 1000, 1)
    out["html_bytes"] = len(html.encode("utf-8"))
    out["md_body"] = md
    out["html"] = html
    return out


def score(key: str, d: pathlib.Path, *, one: dict, data, entries: list, elapsed: float,
          usage: dict, mode: str, line: str) -> dict:
    """一节的产物 → 指标 dict（**纯读**：产物、review、骨架）。"""
    review = dict(getattr(data, "review", None) or {})
    checks = list(review.get("checks") or [])
    sk = _skeleton_for(one)
    secs = list(review.get("sections") or [])
    cov_prod = review.get("cover") or {}
    cov_ind = cover_by_time(sk, secs)
    scale = _render_metrics(data) if data is not None else {}
    md = scale.pop("md_body", "")
    html = scale.pop("html", "")
    body = md_body(md)
    transcript = "\n".join(str(e.get("en") or e.get("asr") or "") for e in entries)
    mats = getattr(data, "materials_line", "") if data is not None else ""
    blob = ""
    try:
        import materials
        mm = materials.Materials.for_course(str(d), one["course"])
        blob = str(getattr(mm, "_blob", "") or "")
    except Exception:                                          # noqa: BLE001
        pass
    aud = correction_audit(checks)
    sel = selftalk_hits(review)
    sel_md = selftalk_hits({"md": md_prose(md)})
    sel_html = selftalk_hits({"html": html_prose(html)})
    terms = term_audit(getattr(data, "glossary_added", None) or [],
                       transcript=transcript, materials_blob=blob)
    warn_lines = [ln.strip() for ln in body.splitlines() if "⚠" in ln]
    leaks = tex_leaks(body)
    return {
        "key": key, "session": one["session"], "course": one["course"],
        "date": one["date"], "set": one["set"], "mode": mode,
        "elapsed_s": round(elapsed, 1),
        "llm": dict(usage),
        "sentences": len(entries), "sections": len(secs),
        "cover": {
            "prod": {"n_ok": cov_prod.get("n_ok"), "n_chapters": cov_prod.get("n_chapters"),
                     "n_covered": cov_prod.get("n_covered"), "n_points": cov_prod.get("n_points")},
            "indep": {"n_ok": cov_ind["n_ok"], "n_chapters": cov_ind["n_chapters"],
                      "n_covered": cov_ind["n_covered"], "n_points": cov_ind["n_points"]},
            # ⭐ 这一条就是那个 bug：产物的报告说"全缺"，而按时间窗算并不缺
            "false_all_missing": bool(cov_ind["n_points"] and
                                      (cov_prod.get("n_covered") or 0) == 0 and
                                      cov_ind["n_covered"] > 0),
        },
        "checks": {"n": len(checks),
                   "ok": sum(1 for c in checks if c.get("status") == "ok"),
                   "mismatch": aud["n_mismatch"],
                   "unchecked": sum(1 for c in checks if c.get("status") == "unchecked"),
                   "wrong_correction": aud["wrong"],
                   "justified": aud["justified"],
                   "unverifiable": aud["unverifiable"],
                   # ⭐ 收窄挡下的条数（作者第六轮第 1 条）：模型本想把它们判成
                   #   「已修正」/「通过」，被口径降成未核对。**基线没有这一栏**
                   #   （那时代码不降级），所以这列非 0 = 收窄真的在起作用。
                   "demoted_mismatch": sum(1 for c in checks
                                           if "只标未核对，不改" in str(c.get("how") or "")),
                   "demoted_ok": sum(1 for c in checks
                                     if "自评不算核" in str(c.get("how") or "")),
                   "origins": claim_origins(review, checks),
                   "rows": aud["rows"]},
        "selftalk": {"review": len(sel), "md": len(sel_md), "html": len(sel_html),
                     "hits": (sel + sel_md + sel_html)[:20]},
        "tex_leaks": {"n": len(leaks), "hits": [x[0] for x in leaks[:10]]},
        "warn": len(warn_lines),
        "warn_lines": warn_lines[:10],
        "terms": terms,
        "gloss_added": list(getattr(data, "glossary_added", None) or []),
        "materials_line": (mats or ""),
        "artifacts": {p.name: p.stat().st_size for p in (d / "Lectures").glob("*")
                      if p.is_file()} if (d / "Lectures").exists() else {},
        "render": scale,
        "katex_offline": None,          # 第 6 条那一项跑完才填（`scripts/note_eval_katex.py`）
        "close_line": str(line or "").splitlines()[0][:200] if line else "",
    }


# ================================================================= 编排
def _run_child(key: str, out: pathlib.Path, mode: str, label: str,
               override: "dict | None" = None) -> dict:
    cmd = [str(PY), str(pathlib.Path(__file__).resolve()),
           "child", "--key", key, "--out", str(out), "--mode", mode, "--label", label]
    for f in ("session", "course", "date"):
        if override and override.get(f):
            cmd += [f"--{f}", str(override[f])]
    p = subprocess.run(cmd, capture_output=True, text=True)
    (out / f"child-{key}.log").write_text(p.stdout + "\n--- stderr ---\n" + p.stderr,
                                          encoding="utf-8")
    if p.returncode != 0:
        raise SystemExit(f"❌ {key} 子进程失败（看 {out}/child-{key}.log）")
    return json.loads((out / key / "metrics.json").read_text("utf-8"))


def selftalk_total(m: dict) -> int:
    """自言自语那一列的总数：`review` + 两个渲染面（md 简版 / html 详细版）。

    ⚠️ `.get` 兜底：`runs/baseline.json` 是在**只有 md 那一腿**时跑的 ——
       旧 run 少一栏不该让报告崩；口径变化按"缺失算 0"（对基线是保守的：
       它的 html 面若当时也扫，只会更多）。
    """
    st = m.get("selftalk") or {}
    return (st.get("review") or 0) + (st.get("md") or 0) + (st.get("html") or 0)


def run(args) -> int:
    out = pathlib.Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    if args.only:
        if args.only not in SESSIONS:
            raise SystemExit(f"未知的课节：{args.only}（可选：{'、'.join(ORDER)}）")
        keys = [args.only]
    else:
        keys = [k for k in ORDER
                if args.set == "all" or SESSIONS[k]["set"] == args.set]
    if any(SESSIONS[k]["set"] == "holdout" for k in keys) and not args.yes_holdout:
        raise SystemExit("⛔ 保留集只许在**最后**跑一次：加 --yes-holdout 明确表示这是最后一次")
    print(f"▶ {args.label or args.mode}：{len(keys)} 节 · 模式 {args.mode}", flush=True)
    got: dict = {}
    for k in keys:
        t0 = time.monotonic()
        got[k] = _run_child(k, out, args.mode, args.label or args.mode)
        m = got[k]
        print(f"  · {k}  {m['elapsed_s']}s · 覆盖 {m['cover']['prod']['n_covered']}"
              f"/{m['cover']['prod']['n_points']}（独立 {m['cover']['indep']['n_covered']}）"
              f" · 修正 {m['checks']['mismatch']}（假 {m['checks']['wrong_correction']}）"
              f" · 自言自语 {selftalk_total(m)}"
              f" · 术语 {m['terms']['added']}（非术语 {m['terms']['non_term']}）"
              f" · [{time.monotonic() - t0:.0f}s]", flush=True)
    label = args.label or args.mode
    (out / "runs").mkdir(exist_ok=True)
    (out / "runs" / f"{label}.json").write_text(json.dumps(
        {"label": label, "mode": args.mode, "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
         "sections": got}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"✅ 存到 {out}/runs/{label}.json", flush=True)
    return 0


def child(args) -> int:
    ov = {"session": getattr(args, "session", ""), "course": getattr(args, "course", ""),
          "date": getattr(args, "date", ""),
          "set": (SESSIONS.get(args.key) or {}).get("set", "dev")}
    m = child_run(args.key, pathlib.Path(args.out).resolve(), args.mode, args.label,
                  override=ov)
    print(json.dumps({k: v for k, v in m.items()
                      if k not in ("checks", "selftalk", "terms")}, ensure_ascii=False)[:800])
    return 0


def render(args) -> int:
    """只重渲染（零 LLM）：`NoteData` JSON → md/html 的**体积与耗时**。"""
    import dataclasses
    sys.path.insert(0, str(ROOT))
    import note_render
    raw = json.loads(pathlib.Path(args.data).read_text("utf-8"))
    data = note_render.NoteData(**{k: v for k, v in raw.items()
                                   if k in {f.name for f in dataclasses.fields(note_render.NoteData)}})
    m = _render_metrics(data)
    body = m.pop("md_body", "")
    html = m.pop("html", "")
    if args.out:
        p = pathlib.Path(args.out)
        p.with_suffix(".md").write_text(body, encoding="utf-8")
        p.with_suffix(".html").write_text(html, encoding="utf-8")
    leaks = tex_leaks(md_body(body))
    m["tex_leaks"] = {"n": len(leaks), "hits": [x[0] for x in leaks[:10]]}
    print(json.dumps(m, ensure_ascii=False, indent=1))
    return 0


#: 总表里每一项的取法：`(显示名, 从 metrics 里取值的函数, 方向)`。
#: 方向 `↓` = 越小越好、`↑` = 越大越好、`·` = 只看（人判）。
COLS = [
    ("课时", lambda m: f"{m['elapsed_s']}s", "↓"),
    ("句/节", lambda m: f"{m['sentences']}/{m['sections']}", "·"),
    ("覆盖(章)", lambda m: f"{m['cover']['prod']['n_ok']}/{m['cover']['prod']['n_chapters']}", "↑"),
    ("覆盖(要点)", lambda m: f"{m['cover']['prod']['n_covered']}/{m['cover']['prod']['n_points']}", "↑"),
    ("独立(要点)", lambda m: f"{m['cover']['indep']['n_covered']}/{m['cover']['indep']['n_points']}", "·"),
    ("假报全缺", lambda m: "是" if m["cover"]["false_all_missing"] else "—", "↓"),
    ("已修正", lambda m: m["checks"]["mismatch"], "·"),
    ("其中假修正", lambda m: m["checks"]["wrong_correction"], "↓"),
    ("复核不了", lambda m: m["checks"]["unverifiable"], "·"),
    ("收窄挡下", lambda m: f'{m["checks"].get("demoted_mismatch", 0)}/'
                          f'{m["checks"].get("demoted_ok", 0)}', "·"),
    ("未核对", lambda m: m["checks"]["unchecked"], "·"),
    ("自言自语", selftalk_total, "↓"),
    ("TeX 露源码", lambda m: m["tex_leaks"]["n"], "↓"),
    ("⚠", lambda m: m["warn"], "·"),
    ("术语新增", lambda m: m["terms"]["added"], "·"),
    ("非术语", lambda m: m["terms"]["non_term"], "↓"),
    ("md KB", lambda m: round(m["render"]["md_bytes"] / 1024), "↓"),
    ("html KB", lambda m: round(m["render"]["html_bytes"] / 1024), "·"),
    ("渲染 ms", lambda m: f"{m['render']['md_ms']:.0f}/{m['render']['html_ms']:.0f}", "↓"),
    # 断网渲染（`scripts/note_eval_katex.py --fill` 回填；没跑过那一栏是 None）。
    ("断网渲染", lambda m: {True: "✅", False: "❌", "static": "⏭"}.get(
        m.get("katex_offline"), "—"), "·"),
    ("in/out token", lambda m: f"{m['llm']['prompt_tokens']}/{m['llm']['completion_tokens']}", "↓"),
]


def _table(sections: dict, keys: list) -> list:
    head = "| 节 | " + " | ".join(f"{c[0]}{c[2]}" for c in COLS) + " |"
    sep = "|---" * (len(COLS) + 1) + "|"
    rows = [head, sep]
    for k in keys:
        m = sections.get(k)
        if not m:
            rows.append(f"| {k} | " + " | ".join(["—"] * len(COLS)) + " |")
            continue
        rows.append(f"| {k} | " + " | ".join(str(c[1](m)) for c in COLS) + " |")
    return rows


def _totals(sections: dict) -> dict:
    got = [m for m in sections.values()]
    if not got:
        return {}

    def s(fn):
        return sum(fn(m) for m in got)
    return {
        "n": len(got),
        "elapsed_s": s(lambda m: m["elapsed_s"]),
        "cover_prod": f"{s(lambda m: m['cover']['prod']['n_covered'])}/"
                      f"{s(lambda m: m['cover']['prod']['n_points'])}",
        "cover_prod_ch": f"{s(lambda m: m['cover']['prod']['n_ok'])}/"
                         f"{s(lambda m: m['cover']['prod']['n_chapters'])}",
        "cover_indep": f"{s(lambda m: m['cover']['indep']['n_covered'])}/"
                       f"{s(lambda m: m['cover']['indep']['n_points'])}",
        "false_all_missing": s(lambda m: 1 if m["cover"]["false_all_missing"] else 0),
        "mismatch": s(lambda m: m["checks"]["mismatch"]),
        "wrong_correction": s(lambda m: m["checks"]["wrong_correction"]),
        "unverifiable": s(lambda m: m["checks"]["unverifiable"]),
        "unchecked": s(lambda m: m["checks"]["unchecked"]),
        "selftalk": s(selftalk_total),
        "tex_leaks": s(lambda m: m["tex_leaks"]["n"]),
        "warn": s(lambda m: m["warn"]),
        "terms_added": s(lambda m: m["terms"]["added"]),
        "terms_non_term": s(lambda m: m["terms"]["non_term"]),
        "md_kb": round(s(lambda m: m["render"]["md_bytes"]) / 1024),
        "html_kb": round(s(lambda m: m["render"]["html_bytes"]) / 1024),
        "tokens_in": s(lambda m: m["llm"]["prompt_tokens"]),
        "tokens_out": s(lambda m: m["llm"]["completion_tokens"]),
    }


def report(args) -> int:
    out = pathlib.Path(args.out).resolve()
    runs = {}
    for p in sorted((out / "runs").glob("*.json")):
        if args.runs and p.stem not in args.runs.split(","):
            continue
        runs[p.stem] = json.loads(p.read_text("utf-8"))
    L: list = ["# note v2 跨课评测", ""]
    for label, R in runs.items():
        sec = R["sections"]
        keys = [k for k in ORDER if k in sec]
        L += [f"## {label}（{R['mode']} · {R['ts']}）", ""]
        L += _table(sec, keys)
        t = _totals({k: sec[k] for k in keys})
        L += ["", f"**合计**：覆盖 {t['cover_prod']}（独立 {t['cover_indep']}）· "
                  f"假报全缺 {t['false_all_missing']} 节 · 已修正 {t['mismatch']}"
                  f"（假 {t['wrong_correction']} / 复核不了 {t['unverifiable']}）· "
                  f"未核对 {t['unchecked']} · 自言自语 {t['selftalk']} · "
                  f"TeX 露源码 {t['tex_leaks']} · ⚠ {t['warn']} · "
                  f"术语 {t['terms_added']}（非术语 {t['terms_non_term']}）· "
                  f"md {t['md_kb']}KB / html {t['html_kb']}KB · "
                  f"token {t['tokens_in']}/{t['tokens_out']} · 共 {t['elapsed_s']:.0f}s", ""]
        # 假修正的明细（作者要的"复核不了的列出来给人看"）
        rows = [r for k in keys for r in sec[k]["checks"]["rows"]]
        bad = [r for r in rows if r["verdict"] == "wrong"]
        unk = [r for r in rows if r["verdict"] == "unverifiable"]
        if bad:
            L += ["**假修正（断言的等式算得对，却被改了）**：", ""]
            L += [f"- `{r['claim'][:90]}` → 重算 `{r['recomputed'][:60]}`" for r in bad[:20]]
            L += [""]
        if unk:
            L += ["**复核不了的「已修正」（要人看）**：", ""]
            L += [f"- `{r['claim'][:90]}` → `{r['recomputed'][:60]}`" for r in unk[:20]]
            L += [""]
        tt = [r for k in keys for r in sec[k]["terms"]["rows"] if r["non_term"]]
        if tt:
            L += ["**非术语入库**：", ""]
            L += [f"- `{r['term']}`（转录 {r['in_transcript']} · 课件 {r['in_materials']}）"
                  f" —— {'、'.join(r['why'])}" for r in tt[:20]]
            L += [""]
        st = [h for k in keys for h in sec[k]["selftalk"]["hits"]][:10]
        if st:
            L += ["**自言自语样例**：", ""]
            L += [f"- `{h['snippet']}`（{h['path']}）" for h in st]
            L += [""]
    (out / "report.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    (out / "report.json").write_text(json.dumps(
        {"runs": {k: _totals(v["sections"]) for k, v in runs.items()}},
        ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n".join(L))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="note v2 跨课评测")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def _common(p):
        p.add_argument("--out", required=True)
        p.add_argument("--mode", default="fresh", choices=["fresh", "replay"])
        p.add_argument("--label", default="")
        p.add_argument("--set", default="dev", choices=["dev", "holdout", "all"])
        p.add_argument("--only", default="")
        p.add_argument("--yes-holdout", action="store_true")

    _common(sub.add_parser("run"))
    _common(sub.add_parser("child"))
    sub.choices["child"].add_argument("--key", required=True)
    for f in ("--session", "--course", "--date"):
        sub.choices["child"].add_argument(f, default="")
    r = sub.add_parser("render")
    r.add_argument("--data", required=True)
    r.add_argument("--out", default="")
    rp = sub.add_parser("report")
    rp.add_argument("--out", required=True)
    rp.add_argument("--runs", default="")
    args = ap.parse_args()
    return {"run": run, "child": child, "render": render, "report": report}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
